# -*- coding: utf-8 -*-
"""튜닝 레지스트리 · 구조 레지스트리 · MCP · 검색 품질 옵션(tiered FTS, PRF, weighted fusion, 인접 청크, 유사 문단 dedupe) 테스트."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki.config import Settings, Toggles  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402
from llmwiki import tuning as tn  # noqa: E402
from llmwiki.architecture import registry, render_text, FLOWS  # noqa: E402
from llmwiki import mcp  # noqa: E402

DOC_A = """# 2026년 5월 20일 (수) · HBM4 캐파 확장 검토 임원회의

## 참석
대표이사, CFO, COO

## 결정사항

### D1. 캐파 확장 1차 투자 승인
- **금액**: 1,500억원 (1차분)
- **담당**: COO실 + 양산기획
- **마감**: 7월 글로벌 발표 전 완료

### D2. 추가 2,800억 투자 — 5/22 임원회의 재검토
- **담당**: CFO실

## 후속
NVIDIA 추가 물량 협상 결과에 따라 2차 투자 규모를 조정한다. 수율 목표 75%.
"""
DOC_B = "# 수출통제 동향\n\n케이스 B 즉시 시행 시 HBM 매출 -4.5% 영향. 미래에셋 (5/15): \"확률 30%대\"\n"
DOC_DUP = "# 일정 메모\n\n케이스 B 즉시 시행 시 HBM 매출 -4.5% 영향. 미래에셋 (5/15): \"확률 30%대\"\n"


def _stages(trace):
    return {c["name"]: c for c in trace["children"]}


class TuningRegistryTest(unittest.TestCase):
    def test_registry_and_coerce(self):
        keys = [p["key"] for p in tn.TUNABLES]
        self.assertEqual(len(keys), len(set(keys)))
        for p in tn.TUNABLES:
            self.assertIn(p["stage"], tn.STAGES)
            self.assertTrue(p["desc"])
            self.assertIn(p["source"], ("tuning", "config"))
        t = tn.Tuning({"fts_mode": "or", "graph_decay": "0.7", "bogus": 1, "top_k_fts": 99})
        self.assertEqual(t.get("fts_mode"), "or")
        self.assertAlmostEqual(t.get("graph_decay"), 0.7)
        self.assertNotIn("bogus", t.values)
        self.assertNotIn("top_k_fts", t.values)          # config 항목은 tuning 에 저장되지 않음
        with self.assertRaises(ValueError):
            t.set("rerank_method", "nope")
        with self.assertRaises(ValueError):
            t.set("graph_hops_x" if False else "graph_frontier", 0)   # < min
        with self.assertRaises(KeyError):
            t.set("top_k_fts", 5)
        t.set("graph_decay", 0.5)                          # 기본값이면 오버라이드 제거
        self.assertNotIn("graph_decay", t.values)
        self.assertTrue(t.set("prf_enabled", "true"))
        rows = t.describe(Settings())
        self.assertTrue(any(r["overridden"] for r in rows if r["key"] == "prf_enabled"))
        self.assertTrue(any(r["source"] == "config" and r["key"] == "top_k_fts" for r in rows))
        doc = tn.render_doc(Settings(), t)
        self.assertIn("| fts_search | `fts_mode`", doc)
        self.assertIn("**(변경)**", doc)

    def test_load_save(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "tuning.json")
            t = tn.Tuning({"fts_mode": "or"})
            tn.save_tuning(t, path)
            data = json.load(open(path, encoding="utf-8"))
            self.assertEqual(data["fts_mode"], "or")
            self.assertIn("_comment", data)
            t2 = tn.load_tuning(path)
            self.assertEqual(t2.get("fts_mode"), "or")
            self.assertIs(tn.T, t2)
        finally:
            tn.load_tuning(os.path.join(tmp, "none.json"))   # 전역 초기화
            shutil.rmtree(tmp, ignore_errors=True)


class ArchitectureTest(unittest.TestCase):
    def test_registry_consistency(self):
        reg = registry()
        self.assertEqual(set(reg["flows"]), {"build", "query", "evolve", "watch"})
        toggles = set(Toggles.__dataclass_fields__)
        stages_with_tunables = 0
        for f in reg["flows"].values():
            for st in f["stages"]:
                for t in st["toggles"]:
                    self.assertIn(t, toggles, "unknown toggle %s in %s" % (t, st["key"]))
                if st["tunables"]:
                    stages_with_tunables += 1
                    for tu in st["tunables"]:
                        self.assertIn("impact", tu)
        self.assertGreaterEqual(stages_with_tunables, 8)
        # 모든 튜닝 단계가 어느 흐름에든 연결되어 있어야 함
        linked = {st["tuning_stage"] for f in FLOWS.values() for st in f["stages"] if st["tuning_stage"]}
        self.assertEqual(linked, set(tn.STAGES))
        txt = render_text(reg, {"fts": False})
        self.assertIn("fts(OFF)", txt)

    def test_guide_markdown(self):
        """docs/OPTIMIZATION_GUIDE.md 는 레지스트리에서 생성된다 — 흐름·단계·손잡이가 모두 실려야 한다."""
        from llmwiki import optimize as opt
        md = opt.guide_markdown()
        for head in ("## 0. 세 가지 렌즈", "## 3. 전체 구조", "## 9. LLM 에게 최적화를 묻는 법"):
            self.assertIn(head, md)
        reg = registry()
        for fk, f in reg["flows"].items():
            self.assertIn("%s — %s" % (fk, f["title"]), md)
            for st in f["stages"]:
                name = ", ".join(st.get("trace") or [st["key"]])
                self.assertIn("| `%s` |" % name, md, "단계 %s 가 가이드에 없음" % st["key"])
                for t in st["toggles"]:
                    self.assertIn("`%s`" % t, md)

    def test_demote_keeps_code_fences(self):
        """묶음에 끼워 넣을 때 제목만 낮추고, 코드블록 안의 '#' 은 건드리지 않는다 (프롬프트 샘플 보호)."""
        from llmwiki import optimize as opt
        src = "# 제목\n\n```\n# 이건 샘플 안의 주석\n```\n\n## 소제목\n####### 제목아님\n"
        out = opt.demote(src, 2)
        self.assertIn("### 제목", out)
        self.assertIn("#### 소제목", out)
        self.assertIn("\n# 이건 샘플 안의 주석\n", out)     # 펜스 안은 그대로
        self.assertIn("####### 제목아님", out)              # h7 은 제목이 아님
        self.assertEqual(opt.demote("###### 최하단", 2), "###### 최하단")   # 6 을 넘지 않음


class QualityOptionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus = os.path.join(self.tmp, "corpus")
        os.makedirs(self.corpus)
        for name, text in (("capex.md", DOC_A), ("other.md", DOC_B), ("dup.md", DOC_DUP)):
            with open(os.path.join(self.corpus, name), "w", encoding="utf-8") as f:
                f.write(text)
        s = Settings(corpus_dirs=[self.corpus], data_dir=os.path.join(self.tmp, "data"), wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=512, debug_level=1)
        s.toggles = Toggles(query_cache=False)
        self.s = s
        self.p = Pipeline(s)
        self.p.build(full=True)

    def tearDown(self):
        self.p.store.close()
        tn.load_tuning(os.path.join(self.tmp, "none.json"))
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _q(self, q, **over):
        for k, v in over.items():
            tn.T.set(k, v)
        try:
            return self.p.query(q, log=False)
        finally:
            for k in over:
                tn.T.reset(k)

    def test_tiered_fts_and_prf(self):
        r, t = self._q("캐파 확장 1차 투자 담당은?")
        st = _stages(t)
        self.assertEqual(st["fts_search"]["meta"]["mode"], "tiered")
        self.assertTrue(any(x.startswith("AND:") for x in st["fts_search"]["meta"]["tiers"]))
        self.assertIn("capex.md", r["hits"][0]["chunk_id"])
        r2, t2 = self._q("캐파 확장 1차 투자 담당은?", fts_mode="or")
        self.assertEqual(_stages(t2)["fts_search"]["meta"]["mode"], "or")
        r3, t3 = self._q("캐파 확장 투자", prf_enabled=True)
        m = _stages(t3)["fts_search"]["meta"]
        self.assertTrue(m["prf_terms"])
        self.assertTrue(any(x.startswith("PRF:") for x in m["tiers"]))

    def test_fusion_and_router_tunables(self):
        r, t = self._q("캐파 확장 1차 투자 담당은?", fusion_method="weighted", fusion_multi_bonus=0.01)
        st = _stages(t)
        self.assertEqual(st["rrf_fuse"]["meta"]["method"], "weighted")
        self.assertTrue(r["hits"])
        r2, t2 = self._q("캐파 확장 1차 투자 담당은?", router_base_graph=2.5)
        self.assertEqual(_stages(t2)["router"]["meta"]["signals"]["relational"], True)
        r3, t3 = self._q("수율", router_kw_fts=3.0)
        self.assertEqual(_stages(t3)["router"]["meta"]["kind"], "keyword")
        self.assertAlmostEqual(_stages(t3)["router"]["meta"]["weights"]["fts"], 3.0)

    def test_context_neighbors_and_similar_dedupe(self):
        # dup.md 는 other.md 와 본문이 같고 헤딩만 다름 → 토큰 Jaccard ≈ 0.79 이므로 임계 0.7 에서 중복 제거
        r, t = self._q("수출통제 케이스 B 영향", context_neighbors=1, context_neighbor_top=1, dedupe_similarity=0.7)
        st = _stages(t)
        self.assertGreaterEqual(st["context"]["meta"]["dropped_duplicates"], 1)
        self.assertIn("neighbors_added", st["context"]["meta"])
        # 최종 후보를 1개로 줄이면 그 청크의 앞/뒤 청크는 후보에 없으므로 인접 청크로 추가된다
        saved = self.p.s.top_k_final
        self.p.s.top_k_final = 1
        try:
            r2, t2 = self._q("캐파 확장 1차 투자", context_neighbors=1, context_neighbor_top=2)
        finally:
            self.p.s.top_k_final = saved
        self.assertGreaterEqual(_stages(t2)["context"]["meta"]["neighbors_added"], 1)
        self.assertTrue(any(h["why"] == ["neighbor"] for h in r2["hits"]))
        self.assertTrue(all(h["n"] for h in r2["hits"] if h["in_context"]))
        r3, t3 = self._q("수출통제 케이스 B 영향", dedupe_similarity=1.0)
        self.assertEqual(_stages(t3)["context"]["meta"]["dropped_duplicates"], 0)

    def test_rerank_methods_and_query_expand(self):
        r, t = self._q("캐파 확장 담당", rerank_method="local")
        st = _stages(t)
        self.assertFalse(st["rerank_llm"]["enabled"])
        self.assertIn("weights", st["rerank_local"]["meta"])
        r2, t2 = self._q("캐파 확장 담당", rerank_method="cross_encoder")
        st2 = _stages(t2)
        self.assertIn("rerank_cross_encoder", st2)          # 모델 없으면 error 메모 후 local 폴백
        self.assertIn("rerank_local", st2)
        self.p.s.toggles.query_expand = True
        try:
            r3, t3 = self._q("캐파 확장 담당", query_expand_n=2)
        finally:
            self.p.s.toggles.query_expand = False
        st3 = _stages(t3)
        self.assertTrue(st3["query_expand"]["enabled"])
        self.assertEqual(len(st3["query_expand"]["meta"]["alt_queries"]), 2)
        self.assertIn("fts_alt1", st3["rrf_fuse"]["meta"]["sources"])
        self.assertEqual(len(r3["config"]["alt_queries"]), 2)
        # 캐시 키에 tuning 이 포함되는지
        self.p.s.toggles.query_cache = True
        k1 = self.p._cache_key("x")
        tn.T.set("fts_mode", "or")
        k2 = self.p._cache_key("x")
        tn.T.reset("fts_mode")
        self.assertNotEqual(k1, k2)

    def test_optimize_bundle(self):
        """`optimize` 묶음 = 설정 스냅샷 + 실측 리포트 + 지시문 + 손잡이 지도, 목차가 겹치지 않아야 한다."""
        from llmwiki import optimize as opt
        self.p.query("캐파 확장 1차 투자 담당은?", log=True)
        b = opt.bundle_markdown(self.p, None, "quality")
        md = b["markdown"]
        for head in ("## A. 지금 설정 스냅샷", "## B. 질의 실측", "## C. 무엇을 해 달라는 요청인가", "## D. 손잡이 지도"):
            self.assertIn(head, md)
        self.assertIn(opt.FOCUS_KO["quality"], md)
        self.assertEqual(b["focus"], "quality")
        self.assertEqual(b["chars"], len(md))
        self.assertTrue(b["request_id"])
        tops = [ln for ln in md.splitlines() if ln.startswith("# ")]
        self.assertEqual(len(tops), 1, "최상위 제목이 하나여야 한다: %s" % tops)

    def test_mcp_tools(self):
        init = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "llmwiki")
        self.assertIsNone(mcp.handle(self.p, {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        tools = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        # 도구를 늘리면 여기도 함께 고친다 (MCP.md 의 표와 verify_surface_align.py 의 대조표도)
        self.assertEqual({t["name"] for t in tools}, {"wiki_query", "wiki_search", "wiki_entity", "wiki_status", "wiki_related", "wiki_doc", "wiki_propose",
                                                      "wiki_feedback", "wiki_forensic", "wiki_sources", "wiki_external_search", "wiki_analysis",
                                                      "wiki_requests", "wiki_rerun"})
        # 붙는 LLM 이 "읽기 전용인가" 를 판단하는 근거 — 도구를 늘리면서 빠뜨리기 쉽다
        self.assertFalse([t["name"] for t in tools if not (t.get("annotations") or {})], "annotations 가 없는 도구가 있습니다")
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "wiki_query", "arguments": {"question": "캐파 확장 담당", "k": 3}}})
        self.assertIn("[C1]", r["result"]["content"][0]["text"])
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "wiki_entity", "arguments": {"name": "CFO"}}})
        self.assertIn("문서 참조", r["result"]["content"][0]["text"])
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "wiki_search", "arguments": {"channel": "fts", "query": "수출통제"}}})
        self.assertIn("other.md", r["result"]["content"][0]["text"])
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(r["result"].get("isError"))
        r = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 7, "method": "bogus"})
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
