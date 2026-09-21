# -*- coding: utf-8 -*-
"""출력 모드 output_mode = answer | fused | reranked | context (docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md §2.3).

지키려는 것:
  1) fused/reranked 는 그 지점에서 멈춘다 — 뒤 단계(리랭크/컨텍스트/판정/답변 LLM/claim/evolve)는 trace 에 skipped 로 남고,
     결과에는 candidates/lists/stages 와 후보 표(answer) 가 있다. result_type 이 그것을 말한다.
  2) context 는 컨텍스트까지 만들고 답변 LLM 을 부르지 않는다 — answer = 컨텍스트 본문, context/refs 가 있다.
  3) 중간 산출물은 캐시에 들어가지 않는다 (다음 일반 질의가 후보 표를 답으로 받으면 안 된다).
  4) CLI --output · MCP wiki_query(output_mode=) 가 같은 경로를 탄다.
"""
import io
import json
import os
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
from llmwiki import tuning as tn                       # noqa: E402
from llmwiki import answer as _ans                     # noqa: E402

DOC_A = "# ISSUE-4001 PDCCH 디코딩 실패\n\nFIFO 임계값 미설정으로 underrun. 담당 김희훈, 수정 CL-80001.\n\n## 조치\n임계값 8 로 상향.\n"
DOC_B = "# ISSUE-4002 RACH 프리앰블 충돌\n\n프리앰블 충돌이 잦아 접속 지연. 담당 이수현, 수정 CL-80002.\n"
DOC_C = "# 주간 보고\n\nPDCCH 디코딩 실패(ISSUE-4001) 는 조치 완료. RACH 충돌은 진행 중.\n"


def stage_map(trace):
    out = {}

    def walk(n):
        out[n.get("name")] = n
        for c in n.get("children") or []:
            walk(c)
    for c in trace.get("children") or []:
        walk(c)
    return out


class OutputModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="lwout_")
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        for name, text in (("a.md", DOC_A), ("b.md", DOC_B), ("c.md", DOC_C)):
            open(os.path.join(corpus, name), "w", encoding="utf-8").write(text)
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"), wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=512, rerank_candidates=4, top_k_final=2)
        s.toggles = Toggles(query_cache=True, precompute=False, evolve_capture=True, rerun_capture=True)
        cls.p = Pipeline(s)
        cls.p.build(full=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        tn.load_tuning(os.path.join(cls.tmp, "none.json"))
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _q(self, q, mode, **ov):
        with self.p.request_scope(overrides=dict({"output_mode": mode}, **ov)):
            return self.p.query(q, log=True)

    def test_normalize(self):
        self.assertEqual(_ans.normalize_output_mode(None), "answer")
        self.assertEqual(_ans.normalize_output_mode(" Reranked "), "reranked")
        self.assertEqual(_ans.normalize_output_mode("bogus"), "answer")
        self.assertEqual(_ans.OUTPUT_STOP_AFTER["fused"], "boost")

    def test_fused(self):
        res, tr = self._q("PDCCH 디코딩 실패 담당", "fused")
        st = stage_map(tr)
        self.assertEqual(res["output_mode"], "fused")
        self.assertEqual(res["result_type"], "candidates_fused")
        self.assertEqual(res["answer_mode"], "candidates")
        for name in ("rerank", "context", "evidence_check", "answer_llm", "claim_check", "evolve_capture"):
            self.assertIn(name, st, name)
            self.assertFalse(st[name].get("enabled", True), "%s 는 건너뛰어야 한다" % name)
            self.assertIn("output_mode", st[name]["meta"].get("reason", ""), name)
        self.assertTrue(res["candidates"])
        c0 = res["candidates"][0]
        for k in ("rank", "chunk_id", "doc_id", "heading", "text", "scores", "ranks", "fused", "boosts", "why"):
            self.assertIn(k, c0)
        self.assertIsNone(c0["rerank"])                                # 리랭크 전
        self.assertEqual([c["chunk_id"] for c in res["candidates"]], res["stages"]["final_order"])
        self.assertEqual(res["stages"]["final_order"], res["stages"]["boost_order"][:len(res["candidates"])])
        self.assertLessEqual(len(res["candidates"]), 4)                # output_candidates_n=0 → rerank_candidates
        self.assertTrue(set(res["lists"]) & {"fts", "vector"})
        self.assertTrue(all(len(v) <= 20 for v in res["lists"].values()))
        self.assertIn("| # | chunk_id |", res["answer"])               # 후보 표(마크다운)
        self.assertIn(c0["chunk_id"], res["answer"])
        self.assertEqual(res["refs"], [])
        self.assertIsNone(res.get("query_id"))                         # 자가진화 기록 없음
        self.assertTrue(res["hits"])
        self.assertTrue(res["request_id"])
        self.assertTrue(res.get("rerun", {}).get("saved"), res.get("rerun"))
        self.assertEqual(res["config"]["round"]["stop_after"], "boost")

    def test_reranked_and_candidate_options(self):
        res, tr = self._q("PDCCH 디코딩 실패 담당", "reranked", tuning={"output_candidates_n": 3, "output_chunk_chars": 10, "output_list_n": 1})
        st = stage_map(tr)
        self.assertEqual(res["result_type"], "candidates_reranked")
        ran = [n for n in ("rerank_llm", "rerank_local", "rerank_api", "rerank_cross_encoder") if n in st and st[n].get("enabled", True)]
        self.assertTrue(ran, "리랭크가 실제로 돌아야 한다: %s" % sorted(st))
        for name in ("doc_expand", "context", "answer_llm", "evidence_check"):
            self.assertFalse(st[name].get("enabled", True), name)
        self.assertEqual(len(res["candidates"]), 3)
        self.assertTrue(all(len(c["text"]) <= 10 for c in res["candidates"]))
        self.assertTrue(all(c["chars"] >= len(c["text"]) for c in res["candidates"]))
        self.assertTrue(all(c["rerank"] is not None for c in res["candidates"]))   # 리랭크 뒤 (top_k_final=2 보다 많은 3개 모두 점수 있음)
        self.assertTrue(all(len(v) == 1 for v in res["lists"].values()))
        self.assertTrue(0 < len(res["stages"]["rerank_before"]) <= 4)     # 리랭크 창(rerank_candidates=4) 안의 입력 순서
        self.assertEqual(res["stages"]["final_order"], [c["chunk_id"] for c in res["candidates"]])

    def test_context(self):
        res, tr = self._q("PDCCH 디코딩 실패 담당", "context")
        st = stage_map(tr)
        self.assertEqual(res["result_type"], "context")
        self.assertEqual(res["answer_mode"], "context")
        self.assertTrue(st["context"].get("enabled", True))
        self.assertTrue(st["evidence_check"].get("enabled", True))          # 판정은 한다
        self.assertFalse(st["answer_llm"].get("enabled", True))
        self.assertFalse(st["claim_check"].get("enabled", True))
        self.assertIn("[C1]", res["answer"])
        self.assertEqual(res["answer"], res["context"]["text"])
        self.assertGreater(res["context"]["chars"], 0)                   # chars = [C#] 블록 합 (그래프 관계 부록은 제외)
        self.assertLessEqual(res["context"]["chars"], len(res["context"]["text"]))
        self.assertTrue(res["refs"])
        self.assertEqual(res["refs"][0]["n"], 1)
        self.assertTrue(res["refs"][0]["preview"])
        self.assertEqual(len(res["refs"]), len(res["context"]["citations"]))
        self.assertIn("verdict", res["evidence"])
        self.assertNotIn("candidates", res)

    def test_not_cached_and_answer_unchanged(self):
        q = "RACH 프리앰블 충돌 담당"
        r1, _ = self._q(q, "fused")
        self.assertFalse(r1["cached"])
        r2, _ = self._q(q, "answer")
        self.assertEqual(r2["output_mode"], "answer")
        self.assertFalse(r2["cached"])                                    # 후보 표가 캐시에 들어가 있지 않다
        self.assertNotIn("| # | chunk_id |", r2["answer"])
        self.assertIn(r2["result_type"], ("grounded", "extractive", "insufficient", "best_effort"))
        self.assertIn("refs", r2)
        r3, _ = self._q(q, "answer")
        self.assertTrue(r3["cached"])                                     # 일반 답변은 캐시된다
        r4, tr4 = self._q(q, "context")
        self.assertFalse(r4["cached"])                                    # 캐시가 있어도 중간 산출물 모드는 다시 만든다
        self.assertFalse(stage_map(tr4)["cache_hit"].get("enabled", True))
        self.assertIn("output_mode", stage_map(tr4)["cache_hit"]["meta"].get("reason", ""))

    def test_settings_default_and_invalid(self):
        res, _ = self._q("PDCCH", "bogus")
        self.assertEqual(res["output_mode"], "answer")
        self.assertEqual(self.p.s.output_mode, "answer")

    def test_cli_output_flag(self):
        from llmwiki import cli
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.run(["query", "PDCCH 디코딩 실패 담당", "--output", "fused", "--tuning", "fts_topk_n=1,fts_topk_w=1.5", "--json", "--no-log"],
                         settings=self.p.s, pipe=self.p, gate=False)
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["result"]["output_mode"], "fused")
        self.assertTrue(out["result"]["candidates"])
        self.assertEqual(stage_map(out["trace"])["rrf_fuse"]["meta"]["topk"]["fts"]["n"], 1)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.run(["query", "PDCCH 디코딩 실패 담당", "--output", "reranked", "--no-log"], settings=self.p.s, pipe=self.p, gate=False)
        self.assertEqual(rc, 0)
        self.assertIn("output_mode: reranked result_type: candidates_reranked", buf.getvalue())
        self.assertIn("| # | chunk_id |", buf.getvalue())

    def test_mcp_output_mode(self):
        from llmwiki import mcp
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "wiki_query", "arguments": {"question": "PDCCH 디코딩 실패 담당", "output_mode": "reranked",
                                                                                "overrides": {"tuning": {"output_candidates_n": 2}}}}})
        sc = r["result"]["structuredContent"]
        self.assertEqual(sc["output_mode"], "reranked")
        self.assertEqual(sc["result_type"], "candidates_reranked")
        self.assertEqual(len(sc["candidates"]), 2)
        self.assertIn("lists", sc)
        self.assertIn("rerank_before", sc["stages"])
        self.assertIn("| # | chunk_id |", r["result"]["content"][0]["text"])
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": "wiki_query", "arguments": {"question": "PDCCH 디코딩 실패 담당", "output_mode": "context"}}})
        sc = r["result"]["structuredContent"]
        self.assertEqual(sc["result_type"], "context")
        self.assertIn("[C1]", sc["context"]["text"])
        self.assertTrue(sc["refs"])
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                "params": {"name": "wiki_query", "arguments": {"question": "PDCCH", "output_mode": "nope"}}})
        self.assertTrue(r["result"].get("isError"))                       # enum 검증
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                "params": {"name": "wiki_query", "arguments": {"question": "PDCCH", "overrides": {"tuning": {"nope": 1}}}}})
        self.assertTrue(r["result"].get("isError"))
        self.assertIn("nope", r["result"]["content"][0]["text"])
        self.assertFalse(tn.T.has_overlay())

    def test_web_override_whitelist(self):
        from llmwiki.web.server import OVERRIDE_SAFE_KEYS
        self.assertIn("output_mode", OVERRIDE_SAFE_KEYS)
        self.assertIn("tuning", OVERRIDE_SAFE_KEYS)


if __name__ == "__main__":
    unittest.main()
