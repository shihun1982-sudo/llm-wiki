# -*- coding: utf-8 -*-
"""답변 모드·실패 처리·융합/리랭크 뒤 LLM 단계 (docs/ANSWER_MODES.md).

2026-09-18 회차에서 설정·프롬프트·결과 필드만 있고 `QueryEngine.run()` 이 연결하지 않아 **켜도 아무 일도 일어나지 않던**
세 가지를 연결했다. 이 테스트가 그 연결을 고정한다 — 회귀하면 "설정은 있는데 동작하지 않는다" 로 되돌아간다.

  1) answer_mode=best_effort  — 근거가 insufficient 여도 LLM 을 부르고 result_type=best_effort ([BK] 허용)
  2) degrade_on_llm_failure   — 답변 LLM 이 끝내 실패했을 때 추출식으로 잇는다(기본) / 끄면 result_type=error
  3) llm_after_fusion · llm_after_rerank — 단계가 trace 에 나타나고 순위·컨텍스트를 실제로 바꾼다

mock 프로바이더(`llmwiki/providers.py MockLLM`)가 두 새 TASK 에 결정적으로 답한다:
fusion_review 는 마지막 후보 하나를 drop, rerank_review 는 순서를 뒤집고 첫 문서를 expand_docs 로 지목한다.
"""
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
from llmwiki import providers as _prov                 # noqa: E402
from llmwiki import tuning as tn                       # noqa: E402

DOC_A = ("# ISSUE-4101 PDCCH 디코딩 실패\n\nFIFO 임계값 미설정으로 underrun 이 발생했다. 담당 김희훈, 수정 CL-81001.\n\n"
         "## 조치\n임계값을 8 로 상향하고 인터럽트 우선순위를 조정했다.\n\n## 검증\nTC-5001 회귀 통과.\n")
DOC_B = "# ISSUE-4102 RACH 프리앰블 충돌\n\n프리앰블 충돌이 잦아 초기 접속이 지연된다. 담당 이수현, 수정 CL-81002.\n"
DOC_C = "# 주간 보고 2026-09\n\nPDCCH 디코딩 실패(ISSUE-4101) 는 조치 완료. RACH 충돌(ISSUE-4102) 은 진행 중이다.\n"
DOC_D = "# 코딩 규칙\n\nDMA 버퍼는 캐시 라인 정렬을 지킨다. 인터럽트 핸들러에서 블로킹 호출을 하지 않는다.\n"


def stage_map(trace):
    """trace 트리를 {단계이름: 노드} 로 (skipped·replayed 노드도 포함된다). `Pipeline.query` 는 (result, trace) 를 준다."""
    out = {}

    def walk(n):
        out[n.get("name")] = n
        for c in n.get("children") or []:
            walk(c)
    for c in trace.get("children") or []:
        walk(c)
    return out


class AnswerModesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="lwans_")
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        for name, text in (("a.md", DOC_A), ("b.md", DOC_B), ("c.md", DOC_C), ("d.md", DOC_D)):
            open(os.path.join(corpus, name), "w", encoding="utf-8").write(text)
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"), wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=512, rerank_candidates=4, top_k_final=3)
        # query_cache off: 같은 질의를 모드만 바꿔 여러 번 부르므로 캐시가 결과를 가로채면 안 된다
        s.toggles = Toggles(query_cache=False, precompute=False, evolve_capture=False, claim_check=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        tn.load_tuning(os.path.join(cls.tmp, "none.json"))
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        # 실패 훅을 쓰는 테스트가 회로 차단기(mock/mock, 60초)를 열어 둔 채 끝나면 **뒤 테스트가 전부 대체 경로**를 탄다.
        # 훅과 회로를 둘 다 앞뒤로 비운다 (실행 순서에 의존하지 않게).
        _prov.MockLLM._fail_counts.clear()
        _prov.circuit_reset()
        os.environ.pop("LLMWIKI_MOCK_FAIL", None)

    def tearDown(self):
        os.environ.pop("LLMWIKI_MOCK_FAIL", None)
        _prov.MockLLM._fail_counts.clear()
        _prov.circuit_reset()

    def _q(self, q="PDCCH 디코딩 실패의 원인과 조치는?", **ov):
        """질의 1회 → (result, trace)."""
        with self.p.request_scope(overrides=ov):
            return self.p.query(q, log=False)

    # ---------------- 1) answer_mode ----------------
    def test_grounded_is_default(self):
        r, _tr = self._q()
        self.assertEqual(r["result_type"], "grounded", r.get("answer", "")[:200])
        self.assertTrue(r["refs"], "refs 는 항상 있어야 한다")

    def test_best_effort_answers_when_evidence_insufficient(self):
        """근거를 일부러 굶겨 insufficient 를 만든 뒤, grounded 는 답하지 않고 best_effort 는 답하는지."""
        # 근거 판정을 일부러 못 넘기게: 상위 점수 임계와 최소 글자수를 범위 안 최대값으로 (rrf 점수는 0.05 를 넘지 않는다)
        starve = {"evidence_check": True, "top_k_final": 1, "context_max_chars": 120,
                  "tuning": {"evidence_min_score": 1.0, "evidence_min_chars": 5000}}
        g, g_tr = self._q("보안 인증서 만료 정책은?", answer_mode="grounded", **starve)
        self.assertEqual(g["result_type"], "insufficient", "grounded 는 근거가 부족하면 LLM 을 부르지 않는다")
        self.assertIn("answer_llm", stage_map(g_tr))
        self.assertFalse(stage_map(g_tr)["answer_llm"].get("enabled", True), "grounded: answer_llm 은 skipped")

        b, b_tr = self._q("보안 인증서 만료 정책은?", answer_mode="best_effort", **starve)
        self.assertEqual(b["result_type"], "best_effort", b.get("answer", "")[:200])
        st = stage_map(b_tr)["answer_llm"]
        self.assertNotEqual(st.get("enabled"), False, "best_effort: answer_llm 이 실제로 돌아야 한다")
        self.assertEqual((st.get("meta") or {}).get("answer_mode"), "best_effort")

    def test_best_effort_unknown_value_falls_back_to_grounded(self):
        r, _tr = self._q(answer_mode="완전자유")
        self.assertEqual(r["result_type"], "grounded")

    # ---------------- 2) degrade_on_llm_failure ----------------
    def test_degrade_true_falls_back_to_extractive(self):
        os.environ["LLMWIKI_MOCK_FAIL"] = "timeout"      # 계속 실패 → 재시도 뒤 최종 실패
        r, _tr = self._q(degrade_on_llm_failure=True, llm_retries=0)
        self.assertEqual(r["result_type"], "extractive", "기본값: 추출식 답변으로 잇는다")
        self.assertTrue(r.get("llm_report"), "실패는 llm_report 로 보고한다")

    def test_degrade_false_ends_with_error(self):
        os.environ["LLMWIKI_MOCK_FAIL"] = "timeout"
        r, _tr = self._q(degrade_on_llm_failure=False, llm_retries=0)
        self.assertEqual(r["result_type"], "error", "끄면 추출식으로 잇지 않고 오류로 끝낸다")
        self.assertIn("degrade_on_llm_failure", r["answer"], "본문이 이유와 되돌리는 법을 말해야 한다")
        self.assertTrue(r.get("llm_report"))

    # ---------------- 3) 융합 뒤 · 리랭크 뒤 LLM ----------------
    def test_fusion_llm_off_by_default(self):
        _r, tr = self._q()
        st = stage_map(tr)
        self.assertIn("fusion_llm", st)
        self.assertFalse(st["fusion_llm"].get("enabled", True))
        self.assertFalse(st["rerank_review_llm"].get("enabled", True))

    def test_llm_after_fusion_demotes_candidate(self):
        """기본 penalty(0.3)는 제거가 아니라 감점 — drop 된 후보의 순위가 실제로 내려가야 한다."""
        base, _ = self._q(output_mode="fused")
        base_rank = {c["chunk_id"]: c["rank"] for c in base["candidates"]}
        r, tr = self._q(llm_after_fusion=True, output_mode="fused")
        st = stage_map(tr)["fusion_llm"]
        self.assertNotEqual(st.get("enabled"), False, "토글을 켜면 단계가 실제로 돈다")
        meta = st.get("meta") or {}
        self.assertTrue(meta.get("dropped"), "mock 은 마지막 후보 하나를 drop 한다: %s" % meta)
        self.assertFalse(meta.get("removed"), "penalty>0 이면 제거가 아니라 감점")
        now_rank = {c["chunk_id"]: c["rank"] for c in r["candidates"]}
        for cid in meta["dropped"]:
            # 감점된 후보는 순위가 내려간다 — 목록이 output_candidates_n 으로 잘리면 아예 빠질 수도 있다(그것도 '내려간 것')
            self.assertGreater(now_rank.get(cid, 10 ** 6), base_rank.get(cid, 0), "drop 된 후보의 순위가 내려가지 않았다")
            for c in r["candidates"]:
                if c["chunk_id"] == cid:
                    self.assertIn("llm_drop", c.get("why") or [], "근거 표·워터폴에서 이유가 보여야 한다")

    def test_fusion_llm_drop_penalty_zero_removes(self):
        r, tr = self._q(llm_after_fusion=True, output_mode="fused", tuning={"fusion_llm_drop_penalty": 0})
        # fused 는 리랭크 전 순위 — fusion_llm 은 그 앞이므로 제거가 후보 목록에 그대로 보인다
        st = stage_map(tr)["fusion_llm"]
        meta = st.get("meta") or {}
        self.assertTrue(meta.get("removed"), "penalty=0 이면 목록에서 제거한다: %s" % meta)
        ids = {c["chunk_id"] for c in r["candidates"]}
        self.assertFalse(ids & set(meta.get("dropped") or []), "제거된 후보는 목록에 없어야 한다")

    def test_llm_after_rerank_reorders_and_expands(self):
        base, _btr = self._q()
        r, tr = self._q(llm_after_rerank=True, doc_expand=True)
        st = stage_map(tr)["rerank_review_llm"]
        self.assertNotEqual(st.get("enabled"), False)
        meta = st.get("meta") or {}
        self.assertTrue(meta.get("selected"), "select 가 비면 순위가 그대로다: %s" % meta)
        self.assertTrue(meta.get("expand_docs"), "mock 은 첫 후보의 문서를 expand_docs 로 준다")
        # mock 이 순서를 뒤집으므로 최종 근거 순서가 기본 실행과 달라야 한다
        self.assertNotEqual([h["chunk_id"] for h in r["hits"]][:3], [h["chunk_id"] for h in base["hits"]][:3])
        # expand_docs 로 지목된 문서는 doc_expand 가 통째로 읽는다
        self.assertEqual(r["doc_expand"].get("llm_expand_docs"), sorted(meta["expand_docs"]))

    def test_rerank_review_skipped_for_output_mode_reranked(self):
        """output_mode=reranked 는 '리랭크 순위를 그대로 보여 준다' 가 목적이므로 선택 LLM 을 돌리지 않는다."""
        _r, tr = self._q(llm_after_rerank=True, output_mode="reranked")
        st = stage_map(tr)["rerank_review_llm"]
        self.assertFalse(st.get("enabled", True))
        self.assertIn("output_mode", str((st.get("meta") or {}).get("reason") or ""))

    def test_stages_are_in_architecture_registry(self):
        """두 단계가 구조 레지스트리에 있어야 Pipeline 화면·arch doc·analysis 가 그린다."""
        from llmwiki import architecture as arch
        keys = {s["key"] for s in arch.registry()["flows"]["query"]["stages"]}
        self.assertIn("fusion_llm", keys)
        self.assertIn("rerank_review_llm", keys)


if __name__ == "__main__":
    unittest.main()
