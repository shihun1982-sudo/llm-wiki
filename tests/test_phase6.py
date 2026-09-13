# -*- coding: utf-8 -*-
"""Phase 6: trial 회귀 비교 시스템 (run/compare/report, 확장 지표, 설정 diff, 질문별 승/패) + MCP 신규 도구."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402
from llmwiki import trials as tr  # noqa: E402
from llmwiki import tuning as tn  # noqa: E402
from llmwiki import schema as sc  # noqa: E402
from llmwiki import query_rules as qr  # noqa: E402
from llmwiki import mcp  # noqa: E402
from llmwiki.cli import run_captured  # noqa: E402


def _gen_corpus(out: str) -> None:
    spec = importlib.util.spec_from_file_location("mk", os.path.join(ROOT, "setup", "make_sample_corpus_modem.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    mod.gen(out, 1)


class Phase6Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus = os.path.join(self.tmp, "corpus")
        _gen_corpus(self.corpus)
        for k in ("LOGS_DIR", "SCHEMAS_DIR", "QUERY_RULES", "MCP_SOURCES", "PROMPTS_DIR", "PINS", "PRESETS"):
            os.environ["LLMWIKI_%s_PATH" % k] = os.path.join(self.tmp, k.lower())
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        self._tuning_path = tn.TUNING_PATH          # 프로젝트 tuning.json(사용자가 프리셋을 저장했을 수 있음)과 격리
        tn.TUNING_PATH = os.path.join(self.tmp, "tuning.json")
        s = Settings(corpus_dirs=[self.corpus], data_dir=os.path.join(self.tmp, "data"), wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=256)
        s.toggles = Toggles(query_cache=False, health_check=False)
        self.s = s
        self.p = Pipeline(s)
        self.p.build(full=True)
        self.qs = json.load(open(os.path.join(self.corpus, "questions.json"), encoding="utf-8"))[:8]

    def tearDown(self):
        self.p.store.close()
        for k in ("LOGS_DIR", "SCHEMAS_DIR", "QUERY_RULES", "MCP_SOURCES", "PROMPTS_DIR", "PINS", "PRESETS"):
            os.environ.pop("LLMWIKI_%s_PATH" % k, None)
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        tn.load_tuning(os.path.join(self.tmp, "none.json"))
        tn.TUNING_PATH = self._tuning_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_preset_tuning_applies_in_memory(self):
        """--preset 의 tuning 값(예: token → answer_length_target=short)이 reload_tuning 에 의해 지워지지 않아야 한다."""
        from llmwiki import presets as pr
        pa = pr.apply(self.p.s, ["token"], save=False)
        self.p.reload_tuning(from_file=False)
        self.assertEqual(tn.T.get("answer_length_target"), "short")
        self.assertIs(self.p.tuning, tn.T)
        self.p.s.toggles.llm_answer = False      # 구조화 추출식 답변 경로
        try:
            r, _ = self.p.query("ISR 안에서 blocking 대기를 써도 되나?", log=False)
        finally:
            self.p.s.toggles.llm_answer = True
        self.assertEqual(r["answer_mode"], "extractive")
        for sect in ("## 핵심", "## 상세 (근거 문서별)", "## 근거", "| [C1] |"):
            self.assertIn(sect, r["answer"])
        self.assertNotIn("(추출식 답변 — LLM 미사용) 질문과 관련된 핵심 문장", r["answer"])
        pr.restore(self.p.s, pa["prev"])
        self.p.reload_tuning(from_file=False)
        self.assertEqual(tn.T.get("answer_length_target"), "normal")
        # run_captured(--preset) 경로도 동일해야 한다
        out = run_captured(["query", "ISR blocking 대기", "--preset", "token", "--no-llm-answer", "--json", "--no-log"], settings=self.s, pipe=self.p)
        self.assertEqual(out["code"], 0, out["output"])
        j = json.loads(out["output"])
        self.assertEqual(j["result"]["answer_mode"], "extractive")
        ans = next(c for c in j["trace"]["children"] if c["name"] == "answer_extractive")
        self.assertIn("answer_chars", ans["meta"])
        self.assertEqual(tn.T.get("answer_length_target"), "normal")   # 요청 후 복원

    def test_trials_run_compare(self):
        a = tr.run_trial(self.p, "base", self.qs, k=5, note="baseline")
        self.assertTrue(a["trial_id"])
        for m in ("hit@k", "mrr", "groundedness", "insufficient_rate", "fallback_rate", "p95_ms", "tokens_per_query", "embed_coverage"):
            self.assertIn(m, a["summary"])
        self.assertEqual(a["summary"]["embed_coverage"], 1.0)
        b = tr.run_trial(self.p, "no-graph", self.qs, k=5, overrides={"graph": "false", "fts_mode": "or"})
        self.assertTrue(self.p.s.toggles.graph)                 # 실행 후 원복
        self.assertEqual(tn.T.get("fts_mode"), "tiered")
        c = tr.run_trial(self.p, "speed", self.qs, k=5, preset="speed")
        self.assertTrue(self.p.s.toggles.rerank_llm)
        cmp_ = tr.compare(self.p.store, ["base", "no-graph", c["trial_id"]])
        self.assertEqual([t["name"] for t in cmp_["trials"]], ["base", "no-graph", "speed"])
        self.assertTrue(cmp_["same_questions"])
        self.assertEqual(len(cmp_["per_question"]), len(self.qs))
        hit = next(m for m in cmp_["metrics"] if m["metric"] == "hit@k")
        self.assertEqual(len(hit["values"]), 3)
        self.assertIn("delta", hit)
        diff_keys = {d["key"] for d in cmp_["config_diff"]}
        self.assertIn("toggles.graph", diff_keys)
        self.assertIn("tuning.fts_mode", diff_keys)
        self.assertIn("preset", diff_keys)
        self.assertIn("no-graph", cmp_["wins"])
        md = tr.report_md(cmp_)
        self.assertIn("| hit@k", md)
        self.assertIn("## 설정 차이", md)
        rows = tr.list_trials(self.p.store)
        self.assertEqual(len(rows), 3)
        t0 = tr.get_trial(self.p.store, "base")
        self.assertTrue(all("request_id" in r for r in t0["rows"]))
        self.assertIn("answer_mode", t0["rows"][0])
        out = run_captured(["trial", "list"], self.s, self.p)
        self.assertIn("no-graph", out["output"])
        out2 = run_captured(["trial", "compare", "base", "speed", "--md"], self.s, self.p)
        self.assertIn("Trial 비교", out2["output"])
        out3 = run_captured(["trial", "run", "--name", "cli", "--set", "top_k_final=4", "--set", "rerank_method=local", "--questions", os.path.join(self.corpus, "questions.json"), "--k", "5"], self.s, self.p)
        self.assertIn("trial #", out3["output"])
        self.assertEqual(self.p.s.top_k_final, 8)
        out4 = run_captured(["trial", "report", "cli"], self.s, self.p)
        self.assertIn("질문별", out4["output"])
        self.assertIn("error", tr.compare(self.p.store, ["base"]))

    def test_mcp_new_tools(self):
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "wiki_doc", "arguments": {"id": "CL-55301"}}})
        txt = r["result"]["content"][0]["text"]
        self.assertIn("CL-55301", txt)
        self.assertIn("fixes", txt)
        r2 = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "wiki_related", "arguments": {"text": "RX DMA underrun 이 발생하고 PHY 재시작이 실패한다. FIFO 임계값이 의심된다.", "doc_types": ["issue", "cl"]}}})
        txt2 = r2["result"]["content"][0]["text"]
        self.assertIn("ISSUE-2001", txt2)
        r3 = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "wiki_propose", "arguments": {"kind": "corpus_gap", "payload": {"topic": "AGC 튜닝"}, "reason": "분석 중 부족"}}})
        pid = json.loads(r3["result"]["content"][0]["text"])["proposal_id"]
        self.assertEqual(self.p.store.get_proposal(pid)["origin"], "mcp")
        r4 = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "wiki_query", "arguments": {"question": "ISSUE-2001 원인", "mode": "fast", "doc_types": ["issue"]}}})
        txt4 = r4["result"]["content"][0]["text"]
        self.assertIn("판정:", txt4)
        self.assertIn("request_id", txt4)
        self.assertTrue(self.p.s.toggles.rerank_llm)             # fast(speed 프리셋) 적용 후 원복
        r5 = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "wiki_propose", "arguments": {"kind": "bogus", "payload": {}}}})
        self.assertTrue(r5["result"].get("isError"))


if __name__ == "__main__":
    unittest.main()
