# -*- coding: utf-8 -*-
"""v2 회귀 테스트: stat_skip 증분 빌드 · 역할별 LLM · 요청 프로파일(requests) · 컨텍스트 압축/중복 제거 · doc_refs · 질의 캐시 · 워처.
실행: python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki.config import Settings, Toggles, apply_overrides  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402
from llmwiki.profiler import Profiler, flatten_trace  # noqa: E402
from llmwiki.providers import make_llm  # noqa: E402

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
"""
DOC_B = "# 수출통제 동향\n\n케이스 B 즉시 시행 시 HBM 매출 -4.5% 영향. 미래에셋 (5/15): \"확률 30%대\"\n"
DOC_C = "# NVIDIA 협상 메모\n\nNVIDIA 추가 물량 협상은 HBM4 캐파 확장 결정과 연결된다. SK하이닉스 수율 75% 보고.\n"
CSV = "사업부,매출,영업이익\n메모리,100,20\n파운드리,50,-3\n"


def _stages(trace):
    return {c["name"]: c for c in trace["children"]}


class ScaleProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus = os.path.join(self.tmp, "corpus")
        os.makedirs(self.corpus)
        self._write("capex.md", DOC_A)
        self._write("other.md", DOC_B)
        self._write("실적.csv", CSV)
        s = Settings(corpus_dirs=[self.corpus], data_dir=os.path.join(self.tmp, "data"), wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=512, debug_level=2)
        s.toggles = Toggles()
        self.s = s
        self.p = Pipeline(s)

    def tearDown(self):
        self.p.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        with open(os.path.join(self.corpus, name), "w", encoding="utf-8") as f:
            f.write(text)

    # ---- 확장: stat_skip 증분 빌드 ----
    def test_incremental_stat_skip_and_new_docs(self):
        res, tr = self.p.build(full=True)
        self.assertEqual(res["docs"], 3)
        self.assertEqual(res["changed"], 3)
        st = _stages(tr)
        self.assertEqual(st["load_corpus"]["meta"]["skipped_by_stat"], 0)
        self.assertIn("csv", st["load_corpus"]["meta"]["kinds"])
        self.assertIn("warm_cache", st)
        self.assertGreater(st["prune"]["meta"].get("fts_optimize_ms", 0), 0)
        # 변경 없음 → stat 만으로 스킵, embed/graph/wiki 는 'no changes' 로 skipped
        res2, tr2 = self.p.build(full=False)
        st2 = _stages(tr2)
        self.assertEqual(res2["changed"], 0)
        self.assertEqual(st2["load_corpus"]["meta"]["skipped_by_stat"], 3)
        self.assertEqual(st2["load_corpus"]["meta"]["read"], 0)
        self.assertFalse(st2["embed"]["enabled"])
        self.assertFalse(st2["graph_build"]["enabled"])
        self.assertFalse(st2["wiki_pages"]["enabled"])
        self.assertIn("no changes", st2["embed"]["meta"]["reason"])
        # 새 문서 20개 추가 (매일 20개 시나리오) → 그 문서들만 읽고 색인, IDF 는 재사용, 커뮤니티는 생략
        for i in range(20):
            self._write("new_%02d.md" % i, DOC_C.replace("메모", "메모 %d" % i))
        res3, tr3 = self.p.build(full=False)
        st3 = _stages(tr3)
        self.assertEqual(res3["changed"], 20)
        self.assertEqual(st3["load_corpus"]["meta"]["read"], 20)
        self.assertEqual(st3["load_corpus"]["meta"]["skipped_by_stat"], 3)
        self.assertFalse(st3["embed"]["meta"]["idf_refit"])
        self.assertEqual(res3["embedded"], st3["chunk_index"]["meta"]["chunks"])   # 변경 청크만 임베딩
        gb = {c["name"]: c for c in st3["graph_build"]["children"]}
        self.assertFalse(gb["communities"]["enabled"])       # incremental_communities off
        self.assertTrue(gb["doc_refs"]["meta"]["entities_updated"] > 0)
        self.assertTrue(st3["wiki_pages"]["meta"]["mode"].startswith("incremental"))
        self.assertEqual(self.p.store.stats()["docs"], 23)
        # 문서 수정 → mtime 변경 → 해당 문서만 재색인 / 문서 삭제 → removed
        time.sleep(0.02)
        self._write("other.md", DOC_B + "\n추가 문장: 케이스 C 검토.\n")
        os.remove(os.path.join(self.corpus, "new_00.md"))
        res4, _ = self.p.build(full=False)
        self.assertEqual(res4["changed"], 1)
        self.assertEqual(res4["removed"], 1)
        self.assertEqual(self.p.store.stats()["docs"], 22)
        # 워처: 변경 없음이면 build 하지 않음
        r = self.p.auto_build_tick()
        self.assertFalse(r["built"])
        self._write("new_99.md", DOC_C)
        r = self.p.auto_build_tick()
        self.assertTrue(r["built"])
        self.assertEqual(self.p.watcher["builds"], 1)
        # 토글: incremental_communities / wiki_full_rewrite / idf_refit_incremental 켜면 증분에서도 수행
        self.p.s.toggles.incremental_communities = True
        self.p.s.toggles.wiki_full_rewrite = True
        self.p.s.toggles.idf_refit_incremental = True
        self._write("new_98.md", DOC_C)
        res5, tr5 = self.p.build(full=False)
        st5 = _stages(tr5)
        self.assertTrue(st5["embed"]["meta"]["idf_refit"])
        self.assertTrue({c["name"]: c for c in st5["graph_build"]["children"]}["communities"]["enabled"])
        self.assertEqual(st5["wiki_pages"]["meta"]["mode"], "full")
        # stat_skip 끄면 전부 읽음
        self.p.s.toggles.stat_skip = False
        _, tr6 = self.p.build(full=False)
        self.assertEqual(_stages(tr6)["load_corpus"]["meta"]["skipped_by_stat"], 0)

    # ---- 그래프 노드 → 문서 참조 ----
    def test_doc_refs_on_nodes(self):
        self.p.build(full=True)
        from llmwiki.graph_rules import entity_id_for
        d = self.p.entity_detail(entity_id_for("CFO"))
        self.assertTrue(d)
        refs = d["entity"]["doc_refs"]
        self.assertTrue(refs)
        self.assertTrue(any("capex.md" in r["doc_id"] for r in refs))
        self.assertIn("first_chunk", refs[0])
        self.assertIn("title", refs[0])
        self.assertGreaterEqual(d["entity"]["n_docs"], 1)
        # 위키 페이지에도 문서 참조 섹션
        page = os.path.join(self.s.wiki_dir, "CFO.md")
        self.assertTrue(os.path.exists(page))
        with open(page, encoding="utf-8") as f:
            self.assertIn("## 문서 참조", f.read())
        # graph export 에 n_docs 포함
        g = self.p.graph_export(limit=50)
        self.assertTrue(any(n.get("n_docs") for n in g["nodes"]))

    # ---- 역할별 LLM ----
    def test_role_llms(self):
        s = self.s
        s.llm_roles = {"rerank": {"provider": "none"}, "extract": {"provider": "mock", "effort": "high"}}
        self.p.reload()
        self.assertEqual(self.p.llm_for("answer").name, "mock")
        self.assertEqual(self.p.llm_for("rerank").name, "none")
        self.assertEqual(self.p.llm_for("extract").role, "extract")
        self.assertEqual(s.role_llm("extract")["effort"], "high")
        self.assertEqual(s.role_llm("answer")["effort"], "medium")
        apply_overrides(s, {"review_model": "claude-sonnet-5", "summary_provider": "none"})
        self.assertEqual(s.role_llm("review")["model"], "claude-sonnet-5")
        self.assertEqual(s.role_llm("summary")["provider"], "none")
        apply_overrides(s, {"review_model": ""})
        self.assertEqual(s.role_llm("review")["model"], s.llm_model)
        self.p.reload()
        self.p.build(full=True)
        r, t = self.p.query("캐파 확장 1차 투자 담당은?", log=False)
        st = _stages(t)
        self.assertEqual(r["answer_mode"], "llm")                 # answer = mock
        self.assertFalse(st["rerank_llm"]["enabled"])            # rerank = none → local
        self.assertIn("rerank_local", st)
        status = self.p.provider_status()
        self.assertEqual(status["roles"]["rerank"]["name"], "none")
        self.assertTrue(status["roles"]["rerank"]["overridden"])
        tp = self.p.test_providers(["answer", "embedder"])
        self.assertTrue(tp["answer"]["ok"] and tp["embedder"]["ok"])

    # ---- 성능/토큰 토글 + 프로파일/디버그 ----
    def test_query_perf_toggles_and_profile(self):
        self.p.build(full=True)
        # query_cache 는 2026-09-16 부터 **기본 off** 다 (설정을 바꿔 가며 볼 때 예전 답이 돌아와서).
        # 이 테스트는 캐시 동작 자체를 보는 것이므로 명시적으로 켠다.
        self.p.s.toggles.query_cache = True
        q = "캐파 확장 1차 투자 금액과 담당은?"
        r, t = self.p.query(q, log=False)
        st = _stages(t)
        self.assertIn("summary", t)
        self.assertGreater(t["summary"]["llm"]["calls"], 0)            # mock rerank + answer
        self.assertGreater(t["summary"]["sql_statements"], 0)
        self.assertEqual(t["debug_level"], 2)
        self.assertIn("samples", st["answer_llm"])                      # debug 2 → 프롬프트 샘플
        self.assertIn("prompt", st["answer_llm"]["samples"])
        self.assertIn("debug", st["fts_search"])                        # debug 1 → 상세 메타
        self.assertIn("match", st["fts_search"]["debug"])
        self.assertIn("counters", st["answer_llm"])
        self.assertGreater(st["answer_llm"]["counters"].get("llm_calls", 0), 0)
        self.assertIn("hops", st["graph_search"]["meta"])
        self.assertIn("overlap", st["rrf_fuse"]["meta"])
        self.assertIn("est_tokens", st["context"]["meta"])
        self.assertTrue(all("offset_ms" in c for c in t["children"]))
        self.assertTrue(r["request_id"])
        # 캐시: 같은 질의 → cache_hit 단계, LLM 호출 0
        r2, t2 = self.p.query(q, log=False)
        self.assertTrue(r2["cached"])
        self.assertIn("cache_hit", _stages(t2))
        self.assertEqual(t2["summary"]["llm"]["calls"], 0)
        self.assertEqual(self.p.cache_info()["query_cache"]["hits"], 1)
        # 토글 변경 → 캐시 미스; rerank_llm off → 토큰 절약 (rerank_llm skipped)
        self.p.s.toggles.rerank_llm = False
        r3, t3 = self.p.query(q, log=False)
        self.assertFalse(r3["cached"])
        st3 = _stages(t3)
        self.assertFalse(st3["rerank_llm"]["enabled"])
        self.assertLess(t3["summary"]["llm"]["calls"], t["summary"]["llm"]["calls"])
        # query_cache off → 캐시 안 함
        self.p.s.toggles.query_cache = False
        r4, t4 = self.p.query(q, log=False)
        self.assertFalse(r4["cached"])
        self.assertNotIn("cache_hit", _stages(t4))
        # debug 0 → samples/debug 없음
        r5, t5 = self.p.query(q, log=False, debug=0)
        self.assertNotIn("samples", _stages(t5)["answer_llm"])
        self.assertNotIn("debug", _stages(t5)["fts_search"])
        # context_trim / dedupe_hits 는 meta 로 확인
        self.p.s.context_chunk_chars = 60
        r6, t6 = self.p.query(q, log=False)
        self.assertGreater(_stages(t6)["context"]["meta"]["trimmed_chunks"], 0)
        self.p.s.toggles.context_trim = False
        r7, t7 = self.p.query(q, log=False)
        self.assertEqual(_stages(t7)["context"]["meta"]["trimmed_chunks"], 0)
        # 컨텍스트 청크가 hits 의 in_context 로 표시됨
        self.assertTrue(any(h["in_context"] for h in r7["hits"]))

    # ---- 요청 로그 (requests) ----
    def test_requests_log_and_system_info(self):
        self.p.build(full=True)
        self.p.query("수출통제 케이스 B", log=False)
        rows = self.p.store.requests(None, 10)
        kinds = [r["kind"] for r in rows]
        self.assertIn("build", kinds)
        self.assertIn("query", kinds)
        qr = [r for r in rows if r["kind"] == "query"][0]
        full = self.p.store.get_request(qr["id"])
        self.assertIn("trace", full)
        self.assertEqual(full["trace"]["name"], "query")
        self.assertIn("summary", full["trace"])
        flat = flatten_trace(full["trace"])
        self.assertTrue(any(f["name"] == "fts_search" for f in flat))
        self.assertEqual(full["result"]["query"], "수출통제 케이스 B")
        ev, _ = self.p.evaluate(k=3, questions=[{"q": "캐파 확장 담당", "expect_docs": ["capex"], "expect_terms": ["COO"]}])
        self.assertTrue(ev["request_id"])
        self.assertIn("total_tokens", ev["summary"])
        info = self.p.system_info(3000, 20, 365)
        self.assertEqual(info["projection"]["target"]["docs"], 3000)
        self.assertEqual(info["projection"]["after_horizon"]["docs"], 3000 + 20 * 365)
        self.assertTrue(info["build_history"])
        self.assertIn("vector_matrix_mb", info["projection"]["target"])
        m = self.p.maintenance("fts_optimize")
        self.assertTrue(m["ok"])
        self.assertIn("error", self.p.maintenance("nope"))

    # ---- 프로파일러 단위 ----
    def test_profiler_levels_and_counters(self):
        from llmwiki import profiler
        p0 = Profiler("t", debug=0)
        with p0.stage("a") as st:
            st.note(x=1)
            st.debug(y=2)
            st.sample(z=3)
            st.log("hello")
            profiler.count("sql", 5)
        d0 = p0.finish()
        a = d0["children"][0]
        self.assertEqual(a["meta"], {"x": 1})
        self.assertNotIn("debug", a)
        self.assertNotIn("samples", a)
        self.assertNotIn("logs", a)
        self.assertEqual(a["counters"]["sql"], 5)
        self.assertEqual(d0["summary"]["sql_statements"], 5)
        p2 = Profiler("t", debug=2)
        with p2.stage("a") as st:
            st.debug(y=2)
            st.sample(z=3)
            st.log("hello")
        d2 = p2.finish()
        a2 = d2["children"][0]
        self.assertEqual(a2["debug"], {"y": 2})
        self.assertEqual(a2["samples"], {"z": 3})
        self.assertTrue(a2["logs"][0].endswith("hello"))
        # LLM 호출 카운터
        llm = make_llm(Settings(llm_provider="mock"), "answer")
        p3 = Profiler("t")
        with p3.stage("call"):
            llm.complete("sys", "user [C1]")
        d3 = p3.finish()
        self.assertEqual(d3["children"][0]["counters"]["llm_calls"], 1)
        self.assertGreater(d3["summary"]["llm"]["total_tokens"], 0)
        self.assertEqual(llm.stats["calls"], 1)


if __name__ == "__main__":
    unittest.main()
