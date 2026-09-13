# -*- coding: utf-8 -*-
"""빠른 회귀 테스트 (unittest, 외부 의존성 없음).  실행: python -m unittest discover -s tests -v"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki.config import Settings, Toggles  # noqa: E402
from llmwiki.corpus import Document, chunk_document  # noqa: E402
from llmwiki.textutil import tokenize_for_fts, fts_query, keywords, strip_josa  # noqa: E402
from llmwiki.graph_rules import RuleExtractor, DEFAULT_RULES  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402

SAMPLE = """# 2026년 5월 20일 (수) · HBM4 캐파 확장 검토 임원회의

## 참석
대표이사, CFO, COO

## 결정사항

### D1. 캐파 확장 1차 투자 승인
- **금액**: 1,500억원 (1차분)
- **담당**: COO실 + 양산기획
- **마감**: 7월 글로벌 발표 전 완료

### D2. 추가 2,800억 투자 — 5/22 임원회의 재검토
- **담당**: CFO실

## 애널리스트 코멘트
- **미래에셋 (3/5)**: "SK하이닉스의 약 2% 저가 정책은 NVIDIA 락업 의도"
"""


class TextUtilTest(unittest.TestCase):
    def test_josa(self):
        self.assertEqual(strip_josa("하이닉스의"), "하이닉스")
        self.assertEqual(strip_josa("수율은"), "수율")
        self.assertEqual(strip_josa("NVIDIA"), "NVIDIA")

    def test_fts_tokens(self):
        toks = tokenize_for_fts("SK하이닉스의 HBM4 수율은 75%")
        # 혼합 스크립트 토큰은 스크립트 경계에서 분리된다 (질의도 동일 규칙이므로 매칭에 영향 없음)
        self.assertIn("sk", toks)
        self.assertIn("하이닉스", toks)
        self.assertIn("수율", toks)
        self.assertIn("hbm4", toks)

    def test_fts_query_safe(self):
        q = fts_query('수율 "따옴표" (괄호) AND')
        self.assertTrue(q.startswith('"'))
        self.assertNotIn('""따옴표""', q)

    def test_keywords(self):
        self.assertEqual(keywords("HBM4 캐파 확장의 담당은 누가?")[:3], ["hbm4", "캐파", "확장"])


class ChunkerTest(unittest.TestCase):
    def test_heading_chunks(self):
        d = Document("t/a.md", "a.md", "title", SAMPLE, "md", "h")
        ch = chunk_document(d, 300, 40)
        self.assertGreaterEqual(len(ch), 3)
        self.assertTrue(any("D1" in c.heading for c in ch))


class RuleExtractorTest(unittest.TestCase):
    def test_extract(self):
        ex = RuleExtractor(json.loads(json.dumps(DEFAULT_RULES)))
        ents, counts, rels = ex.extract_chunk(SAMPLE, "결정사항", "t/a.md", "HBM4 캐파 확장 검토 임원회의")
        names = {e.name for e in ents.values()}
        self.assertIn("CFO", names)
        self.assertIn("NVIDIA", names)
        self.assertTrue(any(n.startswith("D1") for n in names))
        rel_types = {r.rel for r in rels}
        self.assertIn("owner", rel_types)
        self.assertIn("amount", rel_types)
        self.assertIn("attendee", rel_types)
        self.assertIn("comments_on", rel_types)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        corpus = os.path.join(self.tmp, "corpus")
        os.makedirs(corpus)
        with open(os.path.join(corpus, "capex.md"), "w", encoding="utf-8") as f:
            f.write(SAMPLE)
        with open(os.path.join(corpus, "other.md"), "w", encoding="utf-8") as f:
            f.write("# 수출통제 동향\n\n케이스 B 즉시 시행 시 HBM 매출 -4.5% 영향. 미래에셋 (5/15): \"확률 30%대\"\n")
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(self.tmp, "data"), wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=512)
        s.toggles = Toggles(llm_graph=True, community_summary=True)
        self.p = Pipeline(s)

    def tearDown(self):
        self.p.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_query_evolve(self):
        res, tr = self.p.build(full=True)
        self.assertEqual(res["docs"], 2)
        self.assertGreater(res["stats"]["entities"], 5)
        self.assertGreater(res["stats"]["embeddings"], 0)
        names = [c["name"] for c in tr["children"]]
        self.assertIn("graph_build", names)
        # incremental: no change → nothing re-indexed
        res2, _ = self.p.build(full=False)
        self.assertEqual(res2["changed"], 0)
        # query with all channels
        r, t = self.p.query("캐파 확장 1차 투자 담당은?", log=True)
        self.assertTrue(r["hits"])
        self.assertIn("capex.md", r["hits"][0]["chunk_id"])
        self.assertEqual(r["answer_mode"], "llm")
        stages = [c["name"] for c in t["children"]]
        for st in ("router", "fts_search", "vector_search", "graph_search", "rrf_fuse", "rerank_llm", "answer_llm"):
            self.assertIn(st, stages)
        # toggles off → stages skipped
        self.p.s.toggles.vector = False
        self.p.s.toggles.llm_answer = False
        r2, t2 = self.p.query("수출통제 케이스 B 영향", log=False)
        skipped = [c["name"] for c in t2["children"] if not c["enabled"]]
        self.assertIn("vector_search", skipped)
        self.assertEqual(r2["answer_mode"], "extractive")
        self.assertIn("other.md", r2["hits"][0]["chunk_id"])
        # evolve: feedback → proposal → apply (with eval) → applied & overlay indexed
        from llmwiki import evolve as ev
        from llmwiki import evalset
        evalset.EVAL_PATH = os.path.join(self.tmp, "q.json")
        with open(evalset.EVAL_PATH, "w", encoding="utf-8") as f:
            json.dump([{"q": "캐파 확장 담당", "expect_docs": ["capex"], "expect_terms": ["COO"]}], f)
        fb = ev.record_feedback(self.p, r["query_id"], -1, "담당은 COO실과 양산기획이다")
        self.assertTrue(fb["proposals"])
        out = ev.apply_proposal(self.p, fb["proposals"][0])
        self.assertEqual(out["status"], "applied")
        self.assertTrue(any(d["doc_id"].startswith("wiki/") for d in self.p.store.list_docs()))
        # synonym proposal applies without rebuild
        pid = self.p.store.add_proposal("synonym", {"term": "캐파", "expansion": "생산능력"}, "test", 0.9, "manual")
        out2 = ev.apply_proposal(self.p, pid, evaluate=False)
        self.assertEqual(out2["status"], "applied")
        self.assertIn("캐파", self.p.store.synonyms())


if __name__ == "__main__":
    unittest.main()
