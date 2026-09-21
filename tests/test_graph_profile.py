# -*- coding: utf-8 -*-
"""그래프 진단 프로파일 (요청 5): 절 존재·수치 일관성·저장/이력/비교·제안·CLI/MCP 창구."""
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
from llmwiki import graph_profile as gp  # noqa: E402
from llmwiki import tuning as tn  # noqa: E402
from llmwiki import schema as sc  # noqa: E402
from llmwiki import query_rules as qr  # noqa: E402

ENV_KEYS = ("LOGS_DIR", "SCHEMAS_DIR", "QUERY_RULES", "MCP_SOURCES", "PROMPTS_DIR", "PINS", "PRESETS")


def _gen_corpus(out: str) -> None:
    spec = importlib.util.spec_from_file_location("mk", os.path.join(ROOT, "setup", "make_sample_corpus_modem.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    mod.gen(out, 1)


class GraphProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="lwgp_")
        cls.corpus = os.path.join(cls.tmp, "corpus")
        _gen_corpus(cls.corpus)
        for k in ENV_KEYS:
            os.environ["LLMWIKI_%s_PATH" % k] = os.path.join(cls.tmp, k.lower())
        os.environ["LLMWIKI_RULES_PATH"] = os.path.join(cls.tmp, "rules.json")
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        cls._tuning_path = tn.TUNING_PATH
        tn.TUNING_PATH = os.path.join(cls.tmp, "tuning.json")
        s = Settings(corpus_dirs=[cls.corpus], data_dir=os.path.join(cls.tmp, "data"), wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=256, graph_profile_keep=3, graph_profile_hubs=5, graph_profile_requests=50)
        s.toggles = Toggles(query_cache=False, health_check=False, community_summary=False, wiki_pages=False)
        cls.s = s
        cls.p = Pipeline(s)
        cls.p.build(full=True)
        # 질의 활용 절이 볼 요청을 하나 남긴다 (mock LLM)
        cls.p.query("ISSUE-2001 의 근본 원인은?", log=True)
        cls.p.query("인터럽트 지연이 왜 생기나", log=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        for k in ENV_KEYS:
            os.environ.pop("LLMWIKI_%s_PATH" % k, None)
        os.environ.pop("LLMWIKI_RULES_PATH", None)
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        tn.load_tuning(os.path.join(cls.tmp, "none.json"))
        tn.TUNING_PATH = cls._tuning_path
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_sections_and_consistency(self):
        prof = gp.profile(self.p)
        for sec in ("size", "connectivity", "coverage", "quality", "rules", "usage", "suggestions", "generated_at", "build_version", "thresholds"):
            self.assertIn(sec, prof)
        st = self.p.store.stats()
        self.assertEqual(prof["size"]["entities"], st["entities"])
        self.assertEqual(prof["size"]["relations"], st["relations"])
        self.assertGreater(prof["size"]["entities"], 0)
        c = prof["connectivity"]
        self.assertGreaterEqual(c["components"], 1)
        self.assertLessEqual(c["largest_component"], prof["size"]["entities"])
        self.assertEqual(sum(prof["size"]["entities_by_type"].values()), prof["size"]["entities"])
        self.assertEqual(sum(prof["size"]["relations_by_rel"].values()), prof["size"]["relations"])
        self.assertLessEqual(len(c["hubs"]), 5)                      # graph_profile_hubs
        if c["hubs"]:
            self.assertEqual(c["hubs"][0]["degree"], c["degree"]["max"])
            self.assertTrue(all("warning" in h and "type" in h for h in c["hubs"]))
        self.assertEqual(len(c["isolated_samples"]), min(12, c["isolated"]))
        cov = prof["coverage"]
        self.assertEqual(cov["covered"] + cov["uncovered"], cov["docs"])
        self.assertEqual(sum(r["total"] for r in cov["by_doc_type"].values()), cov["docs"])
        q = prof["quality"]
        self.assertEqual(q["dangling_relations"], 0)                # 방금 전체 빌드 → 끊긴 관계 없음
        self.assertTrue(0.0 <= q["cooccur_share"] <= 1.0)
        ru = prof["rules"]
        self.assertTrue(ru["rows"])
        kinds = {r["kind"] for r in ru["rows"]}
        self.assertTrue({"id_pattern", "link_rule"} <= kinds, kinds)
        self.assertEqual(ru["dictionary"]["active"] + ru["dictionary"]["dead"], ru["dictionary"]["total"])
        self.assertIsInstance(ru["dead_rules"], list)
        # 샘플 코퍼스는 ISSUE/CL ID 를 쓰므로 id_pattern issue 는 살아 있어야 한다
        issue_row = next(r for r in ru["rows"] if r["kind"] == "id_pattern" and r["name"] == "issue")
        self.assertGreater(issue_row["entities"], 0)
        us = prof["usage"]
        self.assertGreaterEqual(us["requests"], 2)
        self.assertTrue(0.0 <= us["seed_share"] <= 1.0)
        self.assertIsInstance(prof["suggestions"], list)
        for sg in prof["suggestions"]:
            for k in ("kind", "severity", "detail", "action", "target"):
                self.assertIn(k, sg)
            self.assertIn(sg["severity"], ("info", "warn", "error"))
        # 렌더는 예외 없이 문자열
        self.assertIn("그래프 진단 프로파일", gp.render_text(prof))
        self.assertIn("| 허브 |", gp.render_markdown(prof))
        json.dumps(prof, ensure_ascii=False)                          # JSON 직렬화 가능

    def test_save_history_compare_and_prune(self):
        d = gp.profiles_dir(self.s)
        shutil.rmtree(d, ignore_errors=True)
        paths = []
        for _ in range(5):
            prof = gp.profile(self.p)
            paths.append(gp.save(prof))
        self.assertTrue(all(p.startswith(d) for p in paths))
        files = [f for f in os.listdir(d) if f.endswith(".json")]
        self.assertEqual(len(files), 3, "graph_profile_keep=3 이면 3개만 남아야 한다: %s" % files)
        hist = gp.history(self.s)
        self.assertEqual(len(hist), 3)
        self.assertEqual(hist[0]["path"], paths[-1])                  # 최신 우선
        for k in ("entities", "relations", "isolated_ratio", "components", "coverage_pct", "cooccur_share"):
            self.assertIn(k, hist[0])
        prev, cur = gp.load(hist[1]["path"]), gp.load(hist[0]["path"])
        cmp = gp.compare(prev, cur)
        self.assertIn("deltas", cmp)
        for k in ("entities", "isolated_ratio", "coverage_pct", "components", "hub_max_degree", "cooccur_share"):
            self.assertIn(k, cmp["deltas"])
            self.assertEqual(cmp["deltas"][k]["delta"], 0)            # 같은 그래프 → 변화 없음
        self.assertEqual(cmp["changed"], [])
        # 이전이 없을 때
        cmp0 = gp.compare(None, cur)
        self.assertIsNone(cmp0["deltas"]["entities"]["before"])
        self.assertIsNone(cmp0["deltas"]["entities"]["delta"])

    def test_include_eval_restores_toggles(self):
        t = self.s.toggles
        before = (t.fts, t.vector, t.graph)
        seen = {}

        def fake_eval(k=5, questions=None, log=False):
            seen["toggles"] = (t.fts, t.vector, t.graph)
            return {"summary": {"n": 2, "hit@k": 0.5, "mrr": 0.4, "term_recall": 0.3, "answer_term_recall": 0.2}, "rows": [], "request_id": 1}, {}
        self.p.evaluate = fake_eval  # type: ignore
        try:
            prof = gp.profile(self.p, include_eval=True)
        finally:
            del self.p.evaluate
        self.assertEqual(seen["toggles"], (False, False, True), "그래프 채널만 켜고 평가해야 한다")
        self.assertEqual((t.fts, t.vector, t.graph), before, "평가 뒤 토글이 복원돼야 한다")
        self.assertEqual(prof["eval"]["channel"], "graph")
        self.assertEqual(prof["eval"]["hit@k"], 0.5)
        self.assertEqual(gp.key_metrics(prof)["eval_hit"], 0.5)

    def test_suggestions_rule_based(self):
        """제안은 규칙 기반 — 만들어 둔 프로파일에 임계값을 넘는 값을 넣으면 해당 제안이 나온다."""
        prof = gp.profile(self.p)
        prof["connectivity"]["isolated_ratio"] = 0.9
        prof["connectivity"]["hubs"] = [{"entity_id": "e:2026.05.22", "name": "2026.05.22", "type": "date", "degree": 40, "warning": True}]
        prof["quality"]["dangling_relations"] = 3
        prof["coverage"]["by_doc_type"] = {"weekly_report": {"total": 10, "covered": 1, "pct": 10.0, "uncovered_samples": ["a.md"]}}
        sg = gp.suggest(prof)
        kinds = {x["kind"]: x for x in sg}
        self.assertIn("isolated", kinds)
        self.assertIn("rules.json", kinds["isolated"]["action"])
        self.assertIn("hub_date", kinds)
        self.assertIn("dates_per_chunk", kinds["hub_date"]["action"])
        self.assertEqual(kinds["hub_date"]["target"], "tuning")
        self.assertIn("dangling", kinds)
        self.assertEqual(kinds["dangling"]["target"], "build")
        self.assertIn("coverage", kinds)
        self.assertIn("weekly_report", kinds["coverage"]["detail"])

    def test_cli_and_mcp_surfaces(self):
        from llmwiki.cli import run_captured
        from llmwiki import mcp

        def run(argv):
            r = run_captured(argv, self.s, self.p)
            return r["code"], r["output"]
        code, out = run(["graph", "profile", "--json"])
        self.assertEqual(code, 0, out)
        j = json.loads(out)
        self.assertIn("size", j)
        self.assertTrue(j["saved"].endswith(".json"))
        code, out = run(["graph", "profile", "--compare"])
        self.assertEqual(code, 0, out)
        self.assertIn("직전 실행과 비교", out)
        md = os.path.join(self.tmp, "gp.md")
        code, out = run(["graph", "profile", "--out", md])
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(md))
        with open(md, encoding="utf-8") as f:
            self.assertIn("# 그래프 진단 프로파일", f.read())
        # 기존 `graph` 는 그대로 export
        code, out = run(["graph", "--limit", "3"])
        self.assertEqual(code, 0, out)
        self.assertIn("nodes=", out)
        # MCP
        r = mcp.call_tool(self.p, "wiki_graph_profile", {"compare": True})
        self.assertFalse(r.get("isError"), r)
        self.assertIn("size", r["structuredContent"])
        self.assertIn("그래프 진단 프로파일", r["content"][0]["text"])
        spec = next(t for t in mcp.TOOLS if t["name"] == "wiki_graph_profile")
        self.assertTrue(mcp.ANNOTATIONS["wiki_graph_profile"]["readOnlyHint"])
        self.assertIn("eval", spec["inputSchema"]["properties"])


if __name__ == "__main__":
    unittest.main()
