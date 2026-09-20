# -*- coding: utf-8 -*-
"""단계 재실행 (docs/RERUN.md) — 저장·재생·거부 조건.

여기서 지키려는 것은 세 가지다.
  1) 재시작점 **앞** 단계는 재생되고(LLM·검색을 다시 하지 않고), **뒤** 단계는 다시 계산된다.
  2) 재실행에서 바꾼 설정이 실제로 뒤 단계에 반영된다 (안 그러면 기능 자체가 무의미하다).
  3) 색인이 바뀌었으면 재생을 **거부**한다 (엉뚱한 근거로 답을 만들어 내지 않는다).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles          # noqa: E402
from llmwiki.pipeline import Pipeline                  # noqa: E402
from llmwiki import rerun as _rerun                    # noqa: E402

DOC = """# ISSUE-2001 PDCCH 디코딩 실패

RX 경로의 FIFO 임계값이 설정되지 않아 underrun 이 발생한다. 담당은 김희훈, 수정 CL 은 CL-66001.

## 배경
링크 계층 협상 중 DCI 포맷 1_0 을 놓치면 PDCCH 블라인드 디코딩이 실패한다.

## 조치
임계값을 8 로 올리고 DMA 재시작 순서를 바꾼다.
"""
DOC2 = """# ISSUE-2002 RACH 프리앰블 충돌

프리앰블 충돌이 잦아 접속 지연이 생긴다. 담당은 이수현, 수정 CL 은 CL-66002.
"""


def make_pipe(cleanup, toggles=None):
    tmp = tempfile.mkdtemp(prefix="lwrerun_")
    cleanup(shutil.rmtree, tmp, True)
    corpus = os.path.join(tmp, "corpus")
    os.makedirs(corpus)
    open(os.path.join(corpus, "issue2001.md"), "w", encoding="utf-8").write(DOC)
    open(os.path.join(corpus, "issue2002.md"), "w", encoding="utf-8").write(DOC2)
    s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data"), wiki_dir=os.path.join(tmp, "wiki"),
                 llm_provider="mock", embed_provider="hash", embed_dim=512)
    s.toggles = Toggles(**dict({"rerun_capture": True, "query_cache": False, "precompute": False}, **(toggles or {})))
    return Pipeline(s)


def stage_map(trace):
    """trace → {단계이름: replayed 여부}. 같은 이름이 여러 번이면 하나라도 재계산이면 False."""
    out = {}
    def walk(n):
        name = n.get("name")
        rep = bool(n.get("replayed"))
        out[name] = rep if name not in out else (out[name] and rep)
        for c in n.get("children") or []:
            walk(c)
    for c in trace.get("children") or []:
        walk(c)
    return out


class RerunTest(unittest.TestCase):
    def setUp(self):
        self.p = make_pipe(self.addCleanup)
        self.addCleanup(self.p.store.close)
        self.p.build(full=True)
        with self.p.request_scope():
            self.res, self.trace = self.p.query("ISSUE-2001 의 원인은?", log=False)
        self.rid = self.res["request_id"]

    # ---- 1. 저장 ----
    def test_capture_saved_with_expected_parts(self):
        self.assertTrue(self.res.get("rerun", {}).get("saved"), self.res.get("rerun"))
        d = _rerun.load(self.p.s, self.rid)
        self.assertIsNotNone(d, "중간 결과 파일을 읽지 못했습니다")
        for k in ("plan", "lists", "fused", "boosted", "reranked", "final", "ctx", "answer", "build_version"):
            self.assertIn(k, d, "%s 가 저장되지 않았습니다" % k)
        # 청크 본문은 저장하지 않는다 (색인에서 다시 읽는다) — 파일이 비대해지지 않게
        self.assertNotIn("chunks", d)

    def test_capture_off_saves_nothing(self):
        p2 = make_pipe(self.addCleanup, {"rerun_capture": False})
        self.addCleanup(p2.store.close)
        p2.build(full=True)
        with p2.request_scope():
            res, _ = p2.query("ISSUE-2001", log=False)
        self.assertFalse(_rerun.have(p2.s, res["request_id"]))
        self.assertIsNone(res.get("rerun"))

    # ---- 2. 재생 범위 ----
    def test_replay_from_claim_check_reuses_answer(self):
        with self.p.request_scope():
            res, tr = self.p.rerun(self.rid, "claim_check", log=False)
        st = stage_map(tr)
        self.assertTrue(st.get("answer_llm"), "'검증부터' 인데 답변을 다시 만들었습니다")
        self.assertFalse(st.get("claim_check", False), "claim_check 은 다시 계산해야 합니다")
        self.assertEqual(res["answer"], self.res["answer"], "답변을 재생했으면 글자가 같아야 합니다")
        self.assertEqual(res["rerun_from"], "claim_check")

    def test_replay_from_answer_recomputes_answer_only(self):
        with self.p.request_scope():
            _res, tr = self.p.rerun(self.rid, "answer_llm", log=False)
        st = stage_map(tr)
        self.assertTrue(st.get("context"), "컨텍스트는 재생해야 합니다")
        self.assertTrue(st.get("rerank"), "리랭크는 재생해야 합니다")
        self.assertFalse(st.get("answer_llm", False), "'답변부터' 인데 답변을 재생했습니다")

    def test_replay_from_rerank_keeps_fusion(self):
        with self.p.request_scope():
            _res, tr = self.p.rerun(self.rid, "rerank", log=False)
        st = stage_map(tr)
        self.assertTrue(st.get("rrf_fuse"), "융합은 재생해야 합니다")
        self.assertTrue(st.get("boost"), "부스트는 재생해야 합니다")
        self.assertNotIn(True, [st.get("rerank_llm", False)], "리랭크는 다시 계산해야 합니다")

    def test_replay_from_retrieve_redoes_search(self):
        with self.p.request_scope():
            _res, tr = self.p.rerun(self.rid, "retrieve", log=False)
        st = stage_map(tr)
        self.assertTrue(st.get("query_expand"), "계획(질의 확장)은 재생해야 합니다 — LLM 호출을 아끼는 자리입니다")
        self.assertFalse(st.get("fts_search", True), "'검색부터' 인데 검색을 재생했습니다")

    def test_plan_point_is_full_rerun(self):
        with self.p.request_scope():
            _res, tr = self.p.rerun(self.rid, "plan", log=False)
        st = stage_map(tr)
        self.assertFalse(any(v for v in st.values()), "'계획부터' 는 아무것도 재생하지 않아야 합니다")

    # ---- 3. 바꾼 설정이 반영되는가 (이게 안 되면 기능 자체가 무의미하다) ----
    @staticmethod
    def _primary(res):
        """검색으로 올라온 근거 수. `hits` 전체를 세면 안 된다 — 컨텍스트 구성이 덧붙인
        이웃 청크(neighbor)와 문서 확장(doc_expand) 이 섞여 있어 총 개수는 잘 변하지 않는다."""
        return len([h for h in res["hits"] if (h.get("why") or [""])[0] not in ("neighbor", "doc_expand")])

    def test_override_takes_effect_after_restart_point(self):
        base = self._primary(self.res)
        self.assertGreaterEqual(base, 2, "표본이 너무 적어 이 검사가 의미 없습니다")
        with self.p.request_scope(overrides={"top_k_final": 1}):
            res, _tr = self.p.rerun(self.rid, "context", log=False)
        self.assertEqual(self._primary(res), 1, "재실행에서 바꾼 top_k_final 이 반영되지 않았습니다")

    def test_toggle_change_takes_effect_after_restart_point(self):
        self.assertIsNotNone(self.res.get("claims"), "원 질의에서 claim_check 이 돌지 않아 이 검사가 의미 없습니다")
        # overrides 는 **평면** dict 다 (toggles 도 이름으로 바로 찾는다) — config.apply_overrides 참고
        with self.p.request_scope(overrides={"claim_check": False}):
            res, _tr = self.p.rerun(self.rid, "claim_check", log=False)
        self.assertIsNone(res.get("claims"), "claim_check 을 껐는데 재실행에서 여전히 돌았습니다")

    def test_query_cache_never_serves_a_rerun(self):
        """캐시가 켜져 있어도 재실행은 **반드시 다시 돈다**.

        예전에는 캐시가 먼저 맞아 `cache_hit` 하나만 찍고 예전 답이 그대로 돌아왔다.
        설정을 바꿔 가며 눌러도 화면이 그대로여서 "재실행 버튼이 안 먹는다" 로 보인다.
        """
        self.p.s.toggles.query_cache = True
        try:
            with self.p.request_scope():
                self.p.query("ISSUE-2001 의 원인은?", log=False)      # 캐시에 넣는다
            with self.p.request_scope():
                _res, tr = self.p.rerun(self.rid, "answer_llm", log=False)
            names = [c.get("name") for c in (tr.get("children") or [])]
            self.assertNotIn("cache_hit", [n for n, c in zip(names, tr.get("children") or []) if c.get("enabled", True)],
                             "재실행이 캐시로 응답했습니다 — 바꾼 설정이 반영되지 않습니다")
            self.assertIn("answer_llm", names, "재실행인데 답변 단계가 아예 돌지 않았습니다")
        finally:
            self.p.s.toggles.query_cache = False

    # ---- 4. 거부 조건 ----
    def test_missing_checkpoint_is_refused(self):
        with self.assertRaises(ValueError) as e:
            with self.p.request_scope():
                self.p.rerun(999999, "answer_llm", log=False)
        self.assertIn("중간 결과", str(e.exception))

    def test_stale_index_is_refused(self):
        path = _rerun.path_for(self.p.s, self.rid)
        d = json.load(open(path, encoding="utf-8"))
        keep = d["build_version"]
        d["build_version"] = "deadbeef-옛날색인"
        json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        try:
            with self.assertRaises(ValueError) as e:
                with self.p.request_scope():
                    self.p.rerun(self.rid, "answer_llm", log=False)
            self.assertIn("색인이 그때와 다릅니다", str(e.exception))
            # '계획부터' 는 중간 결과를 쓰지 않으므로 색인이 달라도 허용된다
            with self.p.request_scope():
                res, _ = self.p.rerun(self.rid, "plan", log=False)
            self.assertTrue(res["answer"])
        finally:
            d["build_version"] = keep
            json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False)

    def test_unknown_point_is_refused(self):
        with self.assertRaises(ValueError):
            with self.p.request_scope():
                self.p.rerun(self.rid, "없는단계", log=False)

    # ---- 5. 원본을 덮어쓰지 않는다 · 이어서 재실행할 수 있다 ----
    def test_rerun_saves_its_own_checkpoint_and_keeps_original(self):
        before = open(_rerun.path_for(self.p.s, self.rid), encoding="utf-8").read()
        with self.p.request_scope():
            res, _ = self.p.rerun(self.rid, "answer_llm", log=False)
        self.assertNotEqual(res["request_id"], self.rid)
        self.assertEqual(before, open(_rerun.path_for(self.p.s, self.rid), encoding="utf-8").read(),
                         "재실행이 원본 중간 결과를 덮어썼습니다")
        # 방금 재실행한 결과에서 또 이어서 돌 수 있어야 한다
        self.assertTrue(_rerun.have(self.p.s, res["request_id"]))
        with self.p.request_scope():
            res2, _ = self.p.rerun(res["request_id"], "claim_check", log=False)
        self.assertTrue(res2["answer"])

    # ---- 6. 화면이 쓰는 표 ----
    def test_stage_to_point_mapping(self):
        self.assertEqual(_rerun.point_for_stage("answer_llm"), "answer_llm")
        self.assertEqual(_rerun.point_for_stage("fts_search_alt"), "retrieve")
        self.assertEqual(_rerun.point_for_stage("rerank_llm"), "rerank")
        self.assertIsNone(_rerun.point_for_stage("sync_index"), "모르는 단계에는 ⟲ 를 달지 않는다")

    def test_prune_keeps_newest(self):
        s = self.p.s
        d = _rerun.rerun_dir(s)
        os.makedirs(d, exist_ok=True)
        for i in range(5):
            with open(os.path.join(d, "req_zz%d.json" % i), "w", encoding="utf-8") as f:
                f.write("{}")
        keep = getattr(s, "rerun_keep", 50)
        try:
            s.rerun_keep = 3
            _rerun.prune(s)
            left = [n for n in os.listdir(d) if n.startswith("req_")]
            self.assertEqual(len(left), 3)
        finally:
            s.rerun_keep = keep


if __name__ == "__main__":
    unittest.main()
