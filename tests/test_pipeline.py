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


class RepeatLoopGuardTest(unittest.TestCase):
    """LLM 이 같은 구절을 되풀이하는 고장(반복 루프)을 잡아내는지.

    2026-09-16: llama3.1 이 'FIFO 임계값이 설정된 상태에서 RX DMA가 …' 를 수십 번 반복한 답변이
    그대로 나가고 사전계산 캐시에까지 저장돼, 같은 질문이 계속 고장 답변을 즉시 돌려줬다.
    """

    def test_detects_inline_phrase_loop(self):
        from llmwiki.answer import find_repeat_loop, guard_repeat
        phrase = "FIFO 임계값이 설정된 상태에서 RX DMA가 작동하는 것을 방지하기 위해 "
        text = "핵심 답변\nISSUE-2001 의 원인은 FIFO 임계값 설정 오류이다. " + phrase * 30
        info = find_repeat_loop(text)
        self.assertIsNotNone(info, "반복 루프를 찾지 못했다")
        self.assertGreaterEqual(info["times"], 4)
        cut, info2 = guard_repeat(text)
        self.assertIsNotNone(info2)
        self.assertLess(len(cut), len(text), "잘리지 않았다")
        self.assertIn("ISSUE-2001", cut, "정상 부분까지 잘렸다")
        self.assertIn("반복 루프", cut, "왜 잘렸는지 알리는 문구가 없다")
        self.assertLessEqual(cut.count(phrase.strip()), 2, "반복이 그대로 남았다")

    def test_detects_line_loop(self):
        from llmwiki.answer import find_repeat_loop
        text = "요약\n" + "- RX DMA 가 멈춘다\n" * 12
        info = find_repeat_loop(text)
        self.assertIsNotNone(info)
        self.assertEqual(info["kind"], "line")

    def test_does_not_flag_normal_answer(self):
        """정상 답변·표·목록을 반복으로 오판하지 않아야 한다."""
        from llmwiki.answer import find_repeat_loop
        ok = [
            "## 핵심 답변\nISSUE-2001 의 원인은 FIFO 임계값 설정 오류이고 수정 CL 은 CL-55309 이다. [C1]\n\n"
            "## 상세 설명\nRX DMA 드라이버(drivers/rx_dma.c)에서 임계값이 초기화되지 않아 underrun 이 발생했다. [C2]\n"
            "펌웨어 초기화 순서를 바꾸어 해결했다. [C3]\n\n"
            "| 근거 | 문서 | 유형 |\n|---|---|---|\n| [C1] | ISSUE-2001 | issue |\n| [C2] | CL-55309 | cl |\n| [C3] | SWD-RX-DMA-01 | sw_design |\n",
            "관련 문단을 찾지 못했습니다. 근거 표가 비어 있습니다.",
            "짧은 답.",
            "- 항목 A\n- 항목 B\n- 항목 C\n- 항목 D\n- 항목 E\n",     # 줄마다 내용이 다르다
            "",
        ]
        for t in ok:
            with self.subTest(t=t[:30]):
                self.assertIsNone(find_repeat_loop(t), "정상 텍스트를 반복으로 오판했다")

    def test_broken_answer_is_not_cached(self):
        """반복 루프 답변은 사전계산 캐시에 들어가지 않는다."""
        from llmwiki import precompute as pc

        class _FakeStore:
            def __init__(self):
                self.calls = 0

            def build_version(self):
                return 1

            @property
            def conn(self):
                raise AssertionError("반복 루프 답변인데 캐시에 쓰려고 했다")
        pc.put_cached(_FakeStore(), "k", "q", {"answer": "…", "repeat_loop": {"times": 9}})   # 예외가 나면 실패


class RoleTokensAndDocExpandTest(unittest.TestCase):
    """역할별 출력 토큰 상한과 '근거 문서 전체 읽기'(doc_expand_mode=full).

    2026-09-16: 예전에는 answer 만 토큰을 조절할 수 있었고(answer_max_tokens), 라우터·확장·리랭크·검증·
    추출·요약은 코드에 박힌 값이었다. 단계마다 필요한 길이가 달라 튜닝 지점이 되므로 역할별로 뺐다.
    """

    def _pipe(self, roles=None, toggles=None, docs=1, sections=8):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        corpus = os.path.join(tmp, "corpus")
        os.makedirs(corpus)
        for d in range(docs):
            body = ["# ISSUE-300%d FIFO 임계값 오류\n" % d]
            body.append("RX DMA 의 FIFO 임계값이 설정되지 않아 underrun 이 발생한다. 수정 CL 은 CL-6600%d.\n" % d)
            for i in range(sections):
                body.append("## 부록 %d\n서로 다른 주제의 보충 설명 %d 번. 링크 계층 협상과 무관한 내용이다.\n" % (i, i))
            open(os.path.join(corpus, "d%d.md" % d), "w", encoding="utf-8").write("\n".join(body))
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data"), wiki_dir=os.path.join(tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64, chunk_max_chars=200)
        if roles:
            s.llm_roles = roles
        s.toggles = Toggles(**dict(dict(llm_graph=False, community_summary=False, precompute=False,
                                        query_cache=False, doc_expand=True), **(toggles or {})))
        p = Pipeline(s)
        self.addCleanup(p.store.close)
        p.build(full=True)
        return p

    def test_role_max_tokens_reaches_each_call(self):
        seen = []

        class Spy:
            available = True

            def __init__(self, role):
                self.role, self.name, self.model = role, "spy", "spy"

            def complete(self, system, user, max_tokens=2048, effort="low", json_mode=False):
                seen.append((self.role, max_tokens))
                return {"text": '{"ranking":[0],"queries":[],"keywords":[],"verdict":"sufficient","claims":[]}'
                                if json_mode else "핵심 답변 [C1]", "usage": {}, "ms": 1.0, "model": self.model}

            def describe(self):
                return {"name": self.name, "model": self.model}

        want = {"expand": 111, "rerank": 222, "verify": 333, "answer": 444}
        p = self._pipe(roles={k: {"max_tokens": v} for k, v in want.items()},
                       toggles=dict(query_expand=True, rerank_llm=True, claim_check=True,
                                    claim_check_llm=True, evidence_check_llm=True))
        for role in want:
            p._llms.pins[role] = Spy(role)
        p.query("ISSUE-3000 원인과 수정 CL 은?", log=False)
        for role, tokens in want.items():
            got = [mt for r, mt in seen if r == role]
            if got:                      # 그 단계가 실제로 돌았다면 설정값이 그대로 실려야 한다
                self.assertTrue(all(mt == tokens for mt in got),
                                "%s 단계에 %s 가 아닌 %s 가 실렸다" % (role, tokens, got))
        self.assertTrue(seen, "LLM 단계가 하나도 돌지 않았다")

    def test_role_max_tokens_falls_back_to_stage_default(self):
        p = self._pipe()
        self.assertEqual(p.s.role_max_tokens("router", 200), 200, "지정이 없으면 단계 기본값이어야 한다")
        self.assertEqual(p.s.role_max_tokens("rerank", 400), 400)
        p.s.llm_roles = {"rerank": {"max_tokens": 900}}
        self.assertEqual(p.s.role_max_tokens("rerank", 400), 900, "지정하면 그 값이어야 한다")

    def test_doc_expand_full_reads_whole_document(self):
        """full 모드는 점수로 거르지 않고 근거 문서의 나머지를 문서 순서대로 넣는다."""
        from llmwiki import tuning as _t
        p = self._pipe(sections=10)
        added = {}
        try:
            for mode in ("hybrid", "full"):
                # reload_tuning() 은 tuning.json 을 다시 읽어 메모리 변경을 지운다 → 여기서는 부르지 않는다
                _t.T.set("doc_expand_mode", mode)
                _t.T.set("doc_expand_max_chunks", 20)
                _t.T.set("doc_expand_min_score", 0.9)     # hybrid 는 이 문턱에서 거의 다 걸러진다
                r, tr = p.query("FIFO 임계값 underrun", log=False)
                meta = {}
                stack = [tr]
                while stack:
                    n = stack.pop()
                    if n.get("name") == "doc_expand":
                        meta = n.get("meta") or {}
                        break
                    stack.extend(n.get("children") or [])
                self.assertEqual(meta.get("mode"), mode, "튜닝이 반영되지 않았다: %s" % meta)
                added[mode] = int(meta.get("added") or 0)
                if mode == "full":
                    # full 은 점수로 거르지 않는다 → 아직 안 들어간 청크 전부(상한까지)가 들어가야 한다
                    self.assertEqual(added[mode], min(int(meta.get("candidates") or 0), 20),
                                     "full 인데 일부만 들어갔다: %s" % meta)
        finally:
            for k in ("doc_expand_mode", "doc_expand_max_chunks", "doc_expand_min_score"):
                _t.T.reset(k)
        self.assertGreater(added["full"], added["hybrid"],
                           "full 이 hybrid 보다 많은 청크를 넣어야 한다 (%s)" % added)

    def test_doc_expand_mode_choices_include_full(self):
        from llmwiki import tuning as _t
        self.assertIn("full", _t._INDEX["doc_expand_mode"]["choices"])
        with self.assertRaises(ValueError):
            _t.coerce("doc_expand_mode", "nope")


class FtsRebuildTest(unittest.TestCase):
    """FTS·trigram 색인을 문서 단위로 지우도록 바꾼 뒤에도 행이 새지 않는지.

    2026-09-15: 청크마다 `DELETE ... WHERE chunk_id=?` 하던 것이 FTS5 전체 스캔이라
    빌드가 청크 수의 제곱으로 느려졌다(trigram 을 켜면 특히). 문서 단위 삭제 + 전체 리빌드 시
    일괄 비우기로 바꿨으므로, 중복 행이 남지 않는지와 검색 결과가 그대로인지 지킨다.
    """

    def _pipe(self, trigram, n=6):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        corpus = os.path.join(tmp, "corpus")
        os.makedirs(corpus)
        for i in range(n):
            with open(os.path.join(corpus, "d%d.md" % i), "w", encoding="utf-8") as f:
                f.write("# 문서 %d\n\nPDCCH 디코더 실패와 DMA underrun 관계 %d\n\n## 추가\n펌웨어 초기화 순서 %d\n" % (i, i, i))
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data"), wiki_dir=os.path.join(tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False, fts_trigram=trigram, embed=False)
        p = Pipeline(s)
        self.addCleanup(p.store.close)
        return p, corpus

    def _counts(self, p):
        c = p.store.conn
        out = {"chunks": c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
               "fts": c.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]}
        if p.store._has_trigram():
            out["tri"] = c.execute("SELECT COUNT(*) FROM chunks_tri").fetchone()[0]
        return out

    def test_full_rebuild_twice_has_no_duplicates(self):
        for trigram in (False, True):
            with self.subTest(trigram=trigram):
                p, _ = self._pipe(trigram)
                p.build(full=True)
                a = self._counts(p)
                p.build(full=True)                      # 두 번째 전체 리빌드
                b = self._counts(p)
                self.assertEqual(a, b, "전체 리빌드를 두 번 했더니 행 수가 달라졌다 (중복 또는 누락)")
                self.assertEqual(a["fts"], a["chunks"], "chunks_fts 와 chunks 의 행 수가 다르다")
                if trigram:
                    self.assertEqual(a["tri"], a["chunks"], "chunks_tri 와 chunks 의 행 수가 다르다")
                    self.assertTrue(p.store.trigram_search("디코더", 5), "trigram 검색이 결과를 내지 못했다")
                self.assertEqual(p.store.verify().get("issues") or [], [], "build verify 가 결손을 보고했다")

    def test_incremental_rebuild_of_one_doc(self):
        for trigram in (False, True):
            with self.subTest(trigram=trigram):
                p, corpus = self._pipe(trigram)
                p.build(full=True)
                before = self._counts(p)
                with open(os.path.join(corpus, "d0.md"), "w", encoding="utf-8") as f:
                    f.write("# 문서 0 수정\n\n새 본문: 링크 계층 협상 실패\n")   # 청크 1개로 줄어든다
                res, _ = p.build(full=False)
                self.assertEqual(res["changed"], 1)
                after = self._counts(p)
                self.assertEqual(after["fts"], after["chunks"], "증분 빌드 뒤 FTS 행이 남았다")
                if trigram:
                    self.assertEqual(after["tri"], after["chunks"], "증분 빌드 뒤 trigram 행이 남았다")
                self.assertEqual(before["fts"], before["chunks"])     # 전체 리빌드 직후 정합
                n_doc = p.store.conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id LIKE '%d0.md'").fetchone()[0]
                rows = p.store.conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE doc_id LIKE '%d0.md'").fetchone()[0]
                self.assertEqual(rows, n_doc, "수정한 문서의 옛 FTS 행이 남아 있다")
                self.assertNotIn("PDCCH", (p.store.conn.execute(
                    "SELECT group_concat(body) FROM chunks_fts WHERE doc_id LIKE '%d0.md'").fetchone()[0] or ""),
                    "수정 전 본문이 FTS 에 남아 있다")

    def test_deleted_doc_leaves_nothing(self):
        p, corpus = self._pipe(True)
        p.build(full=True)
        os.remove(os.path.join(corpus, "d1.md"))
        p.build(full=False)
        c = p.store.conn
        for tbl in ("chunks", "chunks_fts", "chunks_tri"):
            n = c.execute("SELECT COUNT(*) FROM %s WHERE doc_id LIKE '%%d1.md'" % tbl).fetchone()[0]
            self.assertEqual(n, 0, "%s 에 삭제된 문서의 행이 남았다" % tbl)

    def test_relations_chunk_index_exists(self):
        """relations(chunk_id) 인덱스가 없으면 문서마다 relations 전체를 훑어 빌드가 제곱으로 느려진다."""
        p, _ = self._pipe(False)
        idx = [r["name"] for r in p.store.conn.execute("PRAGMA index_list('relations')")]
        self.assertIn("idx_rel_chunk", idx)
        plan = [r["detail"] for r in p.store.conn.execute(
            "EXPLAIN QUERY PLAN DELETE FROM relations WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE doc_id=?)", ("x",))]
        self.assertTrue(any("idx_rel_chunk" in d for d in plan), "관계 삭제가 인덱스를 타지 않는다: %s" % plan)
        self.assertFalse(any("SCAN relations" in d for d in plan), "관계 삭제가 전체 스캔이다: %s" % plan)


if __name__ == "__main__":
    unittest.main()
