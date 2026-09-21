# -*- coding: utf-8 -*-
"""파라미터 스윕 (llmwiki/sweep.py · docs/SWEEP.md) — 값 해석 · 키 분류 · 재실행 재생 위의 end-to-end · 비교 · 보관 · CLI · MCP.

지키려는 것:
  1) 값 목록 해석: range(start:stop:step) · 명시 목록 · 토글 기본 [false,true] · sweep_max_values 상한.
  2) 키 분류: 튜닝/설정 키는 레지스트리의 단계 → 재시작점, 토글은 질의 흐름의 첫 단계, 역할 키는 역할별 재시작점.
  3) run() 은 값마다 `rerun` 재생 경로를 타고, 기록이 sweep_dir 에 남으며 compare() 는 첫 값을 기준으로 값마다 단계별 diff 를 만든다.
  4) CLI `sweep run|list|show|compare|keys` 와 MCP `wiki_sweep` 이 같은 기록을 본다.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles          # noqa: E402
from llmwiki.pipeline import Pipeline                  # noqa: E402
from llmwiki import sweep as _sw                       # noqa: E402
from llmwiki import rerun as _rr                       # noqa: E402
from llmwiki import tuning as tn                       # noqa: E402

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
DOC3 = """# 주간 보고

PDCCH 디코딩 실패(ISSUE-2001) 는 FIFO 임계값 조치 완료. RACH 충돌은 진행 중.
"""


def make_pipe(cleanup, toggles=None, **kw):
    tmp = tempfile.mkdtemp(prefix="lwsweep_")
    cleanup(shutil.rmtree, tmp, True)
    corpus = os.path.join(tmp, "corpus")
    os.makedirs(corpus)
    for name, text in (("issue2001.md", DOC), ("issue2002.md", DOC2), ("weekly.md", DOC3)):
        with open(os.path.join(corpus, name), "w", encoding="utf-8") as f:
            f.write(text)
    s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data"), wiki_dir=os.path.join(tmp, "wiki"),
                 llm_provider="mock", embed_provider="hash", embed_dim=512, **kw)
    s.toggles = Toggles(**dict({"rerun_capture": True, "query_cache": False, "precompute": False}, **(toggles or {})))
    return Pipeline(s)


# ---------------------------------------------------------------- 1. 값 해석 · 키 분류 (파이프라인 불필요)
class ResolveValuesTest(unittest.TestCase):
    def test_range_spec(self):
        self.assertEqual(_sw.resolve_values("rrf_k", {"range": "10:40:10"}), [10, 20, 30, 40])
        self.assertEqual(_sw.resolve_values("rrf_k", "10:40:10"), [10, 20, 30, 40])          # 문자열도 range 로
        self.assertEqual(_sw.resolve_values("rrf_k", {"range": "10:12"}), [10, 11, 12])       # step 생략 = 1

    def test_float_range_and_type_coerce(self):
        vals = _sw.resolve_values("fts_topk_w", {"range": "1.0:2.0:0.5"})
        self.assertEqual(vals, [1.0, 1.5, 2.0])
        self.assertTrue(all(isinstance(v, float) for v in vals))

    def test_explicit_list(self):
        self.assertEqual(_sw.resolve_values("rrf_k", [10, 60]), [10, 60])
        self.assertEqual(_sw.resolve_values("rrf_k", {"values": "10,60"}), [10, 60])
        self.assertEqual(_sw.resolve_values("rrf_k", {"values": ["10", 60, 10]}), [10, 60])   # 문자열 coerce · 중복 제거

    def test_toggle_default_and_choice_default(self):
        self.assertEqual(_sw.resolve_values("rerank"), [False, True])
        self.assertEqual(_sw.resolve_values("toggles.rerank", {}), [False, True])
        self.assertEqual(_sw.resolve_values("rerank", "true,false"), [True, False])
        # choice 키(effort)는 spec 이 없으면 choices 전부
        self.assertEqual(_sw.resolve_values("answer_effort"), ["low", "medium", "high"])

    def test_max_values_cap(self):
        with self.assertRaises(ValueError) as e:
            _sw.resolve_values("rrf_k", {"range": "10:100:10"}, max_values=3)
        self.assertIn("sweep_max_values", str(e.exception))
        self.assertEqual(len(_sw.resolve_values("rrf_k", {"range": "10:100:10"}, max_values=10)), 10)

    def test_bad_specs(self):
        with self.assertRaises(ValueError):
            _sw.resolve_values("rrf_k")                        # 숫자 키에 값이 없다
        with self.assertRaises(ValueError):
            _sw.resolve_values("rrf_k", {"range": "10:1:1"})   # 방향 불일치
        with self.assertRaises(ValueError):
            _sw.resolve_values("rrf_k", {"range": "1:10:0"})   # step 0
        with self.assertRaises(ValueError):
            _sw.resolve_values("rrf_k", [0])                   # min=1 아래 (tuning.coerce)
        with self.assertRaises(ValueError):
            _sw.resolve_values("rrf_k", ["abc"])


class ClassifyKeyTest(unittest.TestCase):
    def test_tuning_registry_key_rrf_k(self):
        info = _sw.classify_key("rrf_k")
        self.assertEqual(info["key"], "rrf_k")
        self.assertEqual(info["stage"], "rrf_fuse")
        self.assertEqual(info["point"], "rrf_fuse")
        self.assertEqual(info["type"], "int")
        self.assertIn(info["kind"], ("tuning", "config"))    # rrf_k 는 레지스트리에 source=config 로 있다
        self.assertEqual(_sw.point_for_key("rrf_k"), "rrf_fuse")
        self.assertEqual(_sw.classify_key("tuning.rrf_k")["key"], "rrf_k")

    def test_pure_tuning_key(self):
        info = _sw.classify_key("fts_topk_n")
        self.assertEqual(info["kind"], "tuning")
        self.assertEqual(info["point"], "rrf_fuse")
        self.assertEqual(_sw.classify_key("doc_expand_max_chunks")["point"], "doc_expand")   # 키 접두 표가 stage 표보다 우선

    def test_toggle_key(self):
        info = _sw.classify_key("rerank")
        self.assertEqual(info["kind"], "toggle")
        self.assertEqual(info["type"], "bool")
        self.assertEqual(info["choices"], [False, True])
        self.assertEqual(info["point"], "rerank")
        self.assertEqual(_sw.classify_key("toggles.rerank")["point"], "rerank")
        self.assertEqual(_sw.classify_key("claim_check")["point"], "claim_check")

    def test_config_key(self):
        info = _sw.classify_key("top_k_final")
        self.assertEqual(info["kind"], "config")
        self.assertEqual(info["type"], "int")
        self.assertIn(info["point"], _rr.POINT_IDS)
        info2 = _sw.classify_key("answer_mode")
        self.assertEqual(info2["kind"], "config")
        self.assertEqual(info2["point"], "answer_llm")

    def test_role_key(self):
        info = _sw.classify_key("answer_model")
        self.assertEqual(info["kind"], "role")
        self.assertEqual(info["point"], "answer_llm")
        self.assertEqual(_sw.classify_key("llm_roles.answer.effort")["key"], "answer_effort")
        self.assertEqual(_sw.classify_key("answer_effort")["choices"], ["low", "medium", "high"])
        self.assertEqual(_sw.classify_key("rerank_model")["point"], "rerank")
        with self.assertRaises(ValueError):
            _sw.classify_key("extract_model")           # 빌드 역할은 질의 경로에 없다

    def test_unsweepable_keys(self):
        for k in ("rerun_capture", "health_check", "nope_key_zzz", "corpus_dirs", ""):
            with self.assertRaises(ValueError, msg=k):
                _sw.classify_key(k)
        with self.assertRaises(ValueError):
            _sw.point_for_key("rrf_k", "없는단계")
        self.assertEqual(_sw.point_for_key("rrf_k", "answer_llm"), "answer_llm")   # --from 강제

    def test_sweepable_keys_listing(self):
        rows = _sw.sweepable_keys()
        keys = {r["key"] for r in rows}
        self.assertTrue({"rrf_k", "rerank", "answer_model", "answer_mode", "fts_topk_n"} <= keys, sorted(keys)[:10])
        self.assertNotIn("rerun_capture", keys)
        self.assertTrue(all(r["point"] in _rr.POINT_IDS for r in rows))
        # 재시작점 순서로 정렬돼 있어야 화면 폼이 단계 순으로 보인다
        order = {p: i for i, p in enumerate(_rr.POINT_IDS)}
        self.assertEqual([order[r["point"]] for r in rows], sorted(order[r["point"]] for r in rows))


# ---------------------------------------------------------------- 2. end-to-end (재실행 재생 위)
class SweepRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._cleanups = []
        cls.p = make_pipe(lambda fn, *a: cls._cleanups.append((fn, a)), sweep_keep=30, sweep_max_values=20)
        cls.p.build(full=True)
        with cls.p.request_scope():
            cls.res, cls.trace = cls.p.query("ISSUE-2001 의 원인은?", log=False)
        cls.rid = cls.res["request_id"]

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        for fn, a in reversed(cls._cleanups):
            fn(*a)

    def test_capture_exists(self):
        self.assertTrue(self.res.get("rerun", {}).get("saved"), self.res.get("rerun"))
        self.assertTrue(_rr.have(self.p.s, self.rid))

    def test_run_compare_render_brief(self):
        rec = _sw.run(self.p, self.rid, "rrf_k", [10, 60])
        self.assertEqual(rec["key"], "rrf_k")
        self.assertEqual(rec["point"], "rrf_fuse")
        self.assertEqual(rec["values"], [10, 60])
        self.assertEqual(rec["request_id"], self.rid)
        self.assertEqual(rec["n_ok"], 2, [r.get("error") for r in rec["runs"]])
        self.assertEqual(rec["n_error"], 0)
        # 기록 파일이 sweep_dir 아래에 남는다
        path = rec["path"]
        self.assertTrue(os.path.isfile(path), path)
        self.assertEqual(os.path.dirname(path), _sw.sweep_dir(self.p.s))
        self.assertTrue(os.path.basename(path).startswith("sw_"))
        loaded = _sw.load(self.p.s, rec["id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["id"], rec["id"])
        self.assertEqual(_sw.load(self.p.s, "sw_" + rec["id"])["id"], rec["id"])      # 접두 표기도 받는다
        # 값마다 재실행: 재시작점 앞(rrf_fuse 앞 = 검색)은 재생, 뒤는 다시 계산. 각 실행은 자기 request_id 를 갖는다
        for r in rec["runs"]:
            self.assertIsNone(r["error"])
            self.assertNotEqual(r["request_id"], self.rid)
            self.assertIn("fts_search", r["replayed"], r["replayed"])
            self.assertNotIn("rrf_fuse", r["replayed"])
            self.assertIn("rrf_fuse", r["stages"])
            self.assertTrue(r["stages"]["rrf_fuse"]["present"])
            self.assertFalse(r["stages"]["rrf_fuse"]["replayed"])
            self.assertTrue(r["orders"].get("fused"), r["orders"])
            self.assertTrue(r["answer"])
        self.assertEqual([r["value"] for r in rec["runs"]], [10, 60])
        # compare: 첫 값이 기준
        cmp_ = _sw.compare(rec)
        self.assertEqual(cmp_["baseline"]["value"], 10)
        self.assertEqual(cmp_["n_runs"], 2)
        self.assertEqual(cmp_["n_error"], 0)
        self.assertEqual(cmp_["stages"], _sw.STAGES)
        rows = cmp_["runs"]
        self.assertTrue(rows[0]["is_base"])
        self.assertFalse(rows[1]["is_base"])
        self.assertEqual(rows[0]["n_changed_stages"], 0)       # 기준은 자기 자신과 다르지 않다
        for st in _sw.STAGES:
            self.assertIn(st, rows[1]["stages"])
            self.assertIn("changed", rows[1]["stages"][st])
        self.assertIn("order", rows[1]["stages"]["rrf_fuse"])
        self.assertIn("same", rows[1]["hits"])
        self.assertIn("jaccard", rows[1]["context"])
        self.assertIn("ratio", rows[1]["answer"])
        self.assertIn("ms", cmp_["best"])
        self.assertIn("citations", cmp_["best"])
        # 텍스트 렌더
        txt = _sw.render_text(rec, cmp_)
        self.assertTrue(txt)
        self.assertIn("sw_" + rec["id"], txt)
        self.assertIn("rrf_k = 10, 60", txt)
        self.assertIn("(기준)", txt)
        self.assertIn("최적 힌트", txt)
        for st in _sw.STAGES:
            self.assertIn(st[:10], txt)
        # brief: 답변을 잘라 크기를 줄인 사본 (원본은 그대로)
        b = _sw.brief(rec, answer_chars=5)
        self.assertEqual(len(b["runs"]), 2)
        self.assertTrue(all(len(r["answer"]) <= 5 for r in b["runs"]))
        self.assertTrue(any(len(r["answer"]) > 5 for r in rec["runs"]))
        # 목록에도 보인다
        lst = _sw.list_sweeps(self.p.s)
        self.assertTrue(any(x["id"] == rec["id"] and x["key"] == "rrf_k" and x["n_runs"] == 2 and x["n_errors"] == 0 for x in lst), lst)

    def test_run_with_last_and_tuning_key_and_repeats(self):
        rec = _sw.run(self.p, "last", "fts_topk_n", [0, 1], repeats=2)
        self.assertEqual(rec["kind"], "tuning")
        self.assertEqual(rec["repeats"], 2)
        self.assertEqual(len(rec["runs"]), 4)
        self.assertEqual([(r["value"], r["repeat"]) for r in rec["runs"]], [(0, 0), (0, 1), (1, 0), (1, 1)])
        self.assertEqual(rec["n_ok"], 4, [r.get("error") for r in rec["runs"]])
        # 튜닝 오버레이가 실제로 단계에 들어갔는가: rrf_fuse meta 에 topk 정보
        m = rec["runs"][2]["stages"]["rrf_fuse"]["meta"]
        self.assertTrue(m, rec["runs"][2]["stages"]["rrf_fuse"])
        cmp_ = _sw.compare(rec)
        self.assertEqual(cmp_["baseline"]["value"], 0)
        self.assertEqual(cmp_["baseline"]["repeat"], 0)
        txt = _sw.render_text(rec, cmp_)
        self.assertIn("2회 반복", txt)
        self.assertIn("#2", txt)

    def test_toggle_sweep_changes_stage_state(self):
        rec = _sw.run(self.p, self.rid, "claim_check", None)      # 토글: 값 생략 → [False, True]
        self.assertEqual(rec["values"], [False, True])
        self.assertEqual(rec["point"], "claim_check")
        self.assertEqual(rec["n_ok"], 2, [r.get("error") for r in rec["runs"]])
        # 꺼진 값에서는 claim_check 단계가 건너뜀, 켜진 값에서는 실행 → 비교에 state_changed
        cmp_ = _sw.compare(rec)
        self.assertTrue(cmp_["runs"][1]["stages"]["claim_check"]["changed"], cmp_["runs"][1]["stages"]["claim_check"])
        self.assertTrue(cmp_["runs"][1]["stages"]["answer_llm"]["replayed"])   # 답변은 재생

    def test_run_with_query_makes_baseline(self):
        rec = _sw.run(self.p, None, "top_k_final", [1, 2], query="RACH 프리앰블 충돌 담당")
        self.assertIsNotNone(rec["base_request_id"])
        self.assertEqual(rec["request_id"], rec["base_request_id"])
        self.assertEqual(rec["query"], "RACH 프리앰블 충돌 담당")
        self.assertEqual(rec["n_ok"], 2, [r.get("error") for r in rec["runs"]])

    def test_errors(self):
        with self.assertRaises(ValueError):
            _sw.run(self.p, 999999, "rrf_k", [10, 60])            # 중간 결과 없음
        with self.assertRaises(ValueError):
            _sw.run(self.p, self.rid, "rrf_k", list(range(1, 30)))  # sweep_max_values 초과
        with self.assertRaises(ValueError):
            _sw.run(self.p, self.rid, "rerun_capture", None)      # 스윕 불가 토글
        with self.assertRaises(ValueError):
            _sw.run(self.p, self.rid, "rrf_k", [10], from_point="없는단계")
        self.assertIsNone(_sw.load(self.p.s, "nope"))
        self.assertEqual(_sw.compare({"runs": []})["runs"], [])
        self.assertIn("error", _sw.compare({"runs": []}))

    def test_prune_respects_sweep_keep(self):
        s = self.p.s
        d = _sw.sweep_dir(s)
        os.makedirs(d, exist_ok=True)
        for i in range(6):
            with open(os.path.join(d, "sw_zz%d.json" % i), "w", encoding="utf-8") as f:
                f.write("{}")
        keep = s.sweep_keep
        try:
            s.sweep_keep = 2
            removed = _sw.prune(s)
            left = [n for n in os.listdir(d) if n.startswith("sw_") and n.endswith(".json")]
            self.assertEqual(len(left), 2, left)
            self.assertGreaterEqual(removed, 4)
            s.sweep_keep = 0
            self.assertEqual(_sw.prune(s), 0)                      # 0 = 무제한
        finally:
            s.sweep_keep = keep
            for n in os.listdir(d):
                if n.startswith("sw_zz"):
                    os.remove(os.path.join(d, n))


# ---------------------------------------------------------------- 3. CLI · MCP
class SweepCliMcpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._cleanups = []
        cls.p = make_pipe(lambda fn, *a: cls._cleanups.append((fn, a)))
        cls.p.build(full=True)
        with cls.p.request_scope():
            cls.res, _ = cls.p.query("ISSUE-2001 의 원인은?", log=False)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        for fn, a in reversed(cls._cleanups):
            fn(*a)

    def _cli(self, argv):
        from llmwiki import cli
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.run(argv, settings=self.p.s, pipe=self.p, gate=False)
        return rc, buf.getvalue()

    def test_cli_run_list_show_compare_keys(self):
        rc, out = self._cli(["sweep", "keys"])
        self.assertEqual(rc, 0)
        self.assertIn("rrf_k", out)
        self.assertIn("rrf_fuse", out)
        rc, out = self._cli(["sweep", "run", "last", "--key", "rrf_k", "--values", "10,60"])
        self.assertEqual(rc, 0, out)
        self.assertIn("rrf_k = 10, 60", out)
        self.assertIn("(기준)", out)
        m = re.search(r"스윕 sw_(\S+)", out)
        self.assertIsNotNone(m, out)
        sid = m.group(1)
        rc, out = self._cli(["sweep", "run", "last", "--key", "rrf_k", "--range", "10:30:10", "--json"])
        self.assertEqual(rc, 0, out)
        j = json.loads(out)
        self.assertEqual(j["record"]["values"], [10, 20, 30])
        self.assertEqual(j["compare"]["baseline"]["value"], 10)
        rc, out = self._cli(["sweep", "list"])
        self.assertEqual(rc, 0)
        self.assertIn(sid, out)
        self.assertIn("rrf_k", out)
        rc, out = self._cli(["sweep", "list", "--json"])
        self.assertEqual(rc, 0)
        self.assertTrue(any(x["id"] == sid for x in json.loads(out)["sweeps"]))
        rc, out = self._cli(["sweep", "show", sid])
        self.assertEqual(rc, 0)
        self.assertIn("sw_" + sid, out)
        rc, out = self._cli(["sweep", "show", "sw_" + sid, "--json"])
        self.assertEqual(rc, 0)
        j = json.loads(out)
        self.assertEqual(j["record"]["id"], sid)
        self.assertIn("compare", j)
        rc, out = self._cli(["sweep", "compare", sid])
        self.assertEqual(rc, 0)
        self.assertIn("sw_" + sid, out)
        rc, out = self._cli(["sweep", "compare", sid, "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["baseline"]["value"], 10)
        # 잘못된 호출
        rc, out = self._cli(["sweep", "show", "nope-id"])
        self.assertEqual(rc, 1)
        rc, out = self._cli(["sweep", "run", "last"])
        self.assertEqual(rc, 2)                                       # --key 없음
        rc, out = self._cli(["sweep", "run", "last", "--key", "rrf_k"])
        self.assertEqual(rc, 2)                                       # 값 없음
        self.assertIn("스윕할 수 없습니다", out)
        rc, out = self._cli(["sweep", "run", "last", "--key", "rerun_capture"])
        self.assertEqual(rc, 2)

    def test_mcp_wiki_sweep(self):
        from llmwiki import mcp
        tools = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        t = next(x for x in tools if x["name"] == "wiki_sweep")
        self.assertTrue(t.get("annotations"))
        self.assertTrue(t["annotations"].get("readOnlyHint"))
        for k in ("request_id", "key", "values", "range", "repeats"):
            self.assertIn(k, t["inputSchema"]["properties"], k)
        # 키 없이 → 스윕 가능 키 목록
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "wiki_sweep", "arguments": {}}})
        self.assertFalse(r["result"].get("isError"), r)
        body = json.loads(r["result"]["content"][0]["text"])
        self.assertTrue(any(k["key"] == "rrf_k" for k in body["keys"]))
        self.assertTrue(body["points"])
        self.assertEqual(body["max_values"], self.p.s.sweep_max_values)
        self.assertEqual(r["result"]["structuredContent"]["n_keys"], len(body["keys"]))
        # 키 + 값 → 실행
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                "params": {"name": "wiki_sweep", "arguments": {"request_id": "last", "key": "rrf_k", "values": [10, 60]}}})
        self.assertFalse(r["result"].get("isError"), r)
        self.assertIn("rrf_k = 10, 60", r["result"]["content"][0]["text"])
        sc = r["result"]["structuredContent"]
        self.assertEqual(sc["record"]["values"], [10, 60])
        self.assertEqual(sc["record"]["n_ok"], 2)
        self.assertEqual(sc["compare"]["baseline"]["value"], 10)
        self.assertIsNotNone(_sw.load(self.p.s, sc["record"]["id"]))
        # range 표기 · 잘못된 키는 isError
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                "params": {"name": "wiki_sweep", "arguments": {"key": "rrf_k", "range": "10:20:10"}}})
        self.assertFalse(r["result"].get("isError"), r)
        self.assertEqual(r["result"]["structuredContent"]["record"]["values"], [10, 20])
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                "params": {"name": "wiki_sweep", "arguments": {"key": "nope_zzz", "values": [1]}}})
        self.assertTrue(r["result"].get("isError"))
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                "params": {"name": "wiki_sweep", "arguments": {"key": "rrf_k"}}})
        self.assertTrue(r["result"].get("isError"))                # 값 없음


if __name__ == "__main__":
    unittest.main()
