# -*- coding: utf-8 -*-
"""운영 통계 (llmwiki/opstats.py) — 관리자가 묻는 것에 답하는가.

왜 (2026-09-20 요청): *"옵저빌리티 - 시스템, 규모 메뉴를 개선하고 싶어. 빌드, 질의에 대한 각종 통계
포함해서 관리자에게 도움이 될만한 통계 추가해줘."*

그 화면은 **색인 규모**만 보여 줬다. 운영자가 실제로 묻는 것 — 빌드가 어느 단계에서 느린가, 질의가
언제 몰리나, 토큰을 어디에 쓰나, 근거를 못 찾은 질의가 얼마나 되나, 디스크가 어디서 커지나 — 에 필요한
데이터는 **이미 DB 에 다 있었다**(요청 trace·질의 로그·임베딩 실행 기록). 읽지 않고 있었을 뿐이다.

이 테스트가 지키는 것:
  - **읽기 전용** (통계를 내면서 무엇도 바꾸지 않는다)
  - 데이터가 없어도 깨지지 않는다 (새로 세운 환경에서 화면이 죽으면 안 된다)
  - 섹션을 골라 부를 수 있다 (화면은 필요한 것만)
  - 표본 수를 함께 돌려준다 (3건으로 낸 p95 를 숫자만 보고 믿지 않게)
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import opstats as O      # noqa: E402

DOC = """---
doc_type: issue
ext_id: ISSUE-8{n}
---

# RX DMA underrun {n}

RX DMA 에서 underrun 이 발생하면 PHY 재시작이 실패한다. 클럭 게이팅 타이밍이 원인이다.
rev B1 에서 t_setup 은 4 ns 이다.
"""


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        cls.tmp = tempfile.mkdtemp(prefix="llmwiki_ops_")
        cls._saved = os.environ.get("LLMWIKI_LOGS_DIR_PATH")
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(cls.tmp, "logs")
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        for i in range(3):
            with open(os.path.join(corpus, "d%d.md" % i), "w", encoding="utf-8") as f:
                f.write(DOC.format(n=i) * 3)
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"),
                     wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False, query_cache=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)
        for q in ("RX DMA underrun 원인", "rev B1 t_setup", "RX DMA underrun 원인"):
            cls.p.query(q, log=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        if cls._saved is None:
            os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
        else:
            os.environ["LLMWIKI_LOGS_DIR_PATH"] = cls._saved
        shutil.rmtree(cls.tmp, ignore_errors=True)


class CollectTest(_Base):
    def test_all_sections_present_by_default(self):
        d = O.collect(self.p, days=30)
        for s in O.SECTIONS:
            self.assertIn(s, d, s)

    def test_every_section_has_help_text(self):
        """화면·CLI·MCP 가 같은 설명을 쓴다 — 섹션을 늘리면 설명도 같이 늘어야 한다."""
        for s in O.SECTIONS:
            self.assertIn(s, O.SECTION_HELP, s)
            self.assertTrue(O.SECTION_HELP[s], s)

    def test_sections_filter(self):
        d = O.collect(self.p, days=30, sections=["index", "storage"])
        self.assertIn("index", d)
        self.assertIn("storage", d)
        self.assertNotIn("tokens", d)

    def test_unknown_section_falls_back_to_all(self):
        d = O.collect(self.p, days=30, sections=["없는섹션"])
        self.assertEqual(set(d["sections"]), set(O.SECTIONS))

    def test_is_read_only(self):
        """통계를 내면서 색인·로그를 바꾸면 안 된다."""
        before = (self.p.store.stats(),
                  self.p.store.conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0],
                  self.p.store.conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0])
        O.collect(self.p, days=30)
        after = (self.p.store.stats(),
                 self.p.store.conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0],
                 self.p.store.conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0])
        self.assertEqual(before[1], after[1])
        self.assertEqual(before[2], after[2])
        self.assertEqual(before[0].get("chunks"), after[0].get("chunks"))


class ContentTest(_Base):
    def test_index_flags_missing_embeddings(self):
        d = O.collect(self.p, days=30, sections=["index"])
        self.assertIn("chunks_without_embedding", d["index"])
        self.assertGreater(d["index"]["chunks"], 0)
        self.assertGreater(d["index"]["chunks_per_doc"], 0)

    def test_build_reports_slowest_stages(self):
        d = O.collect(self.p, days=30, sections=["build"])
        self.assertIn("slowest_stages", d["build"])
        st = d["build"]["slowest_stages"]
        self.assertTrue(st, "빌드 요청 trace 에서 단계가 나와야 한다")
        self.assertEqual([x["ms"] for x in st], sorted([x["ms"] for x in st], reverse=True))

    def test_queries_counts_and_hour_histogram(self):
        d = O.collect(self.p, days=30, sections=["queries"])
        q = d["queries"]
        self.assertGreaterEqual(q["total"], 3)
        self.assertEqual(len(q["by_hour"]), 24)
        self.assertEqual(sum(q["by_hour"]), q["total"])

    def test_latency_reports_sample_size(self):
        """표본 수가 없으면 p95 를 믿을 수 없다."""
        d = O.collect(self.p, days=30, sections=["latency"])
        self.assertIn("n", d["latency"])
        self.assertGreaterEqual(d["latency"]["n"], 1)

    def test_tokens_per_query_is_derived_not_raw(self):
        d = O.collect(self.p, days=30, sections=["tokens"])
        t = d["tokens"]
        self.assertIn("per_query", t)
        self.assertIn("calls_per_query", t)

    def test_quality_has_rates_not_only_counts(self):
        d = O.collect(self.p, days=30, sections=["quality"])
        qa = d["quality"]
        for k in ("insufficient_rate", "feedback_rate", "proposals_pending"):
            self.assertIn(k, qa, k)

    def test_storage_lists_dirs_and_tables(self):
        d = O.collect(self.p, days=30, sections=["storage"])
        s = d["storage"]
        self.assertIn("tables", s)
        self.assertIn("data_dirs", s)
        self.assertIn("hints", s)
        self.assertEqual([x["rows"] for x in s["tables"]], sorted([x["rows"] for x in s["tables"]], reverse=True))

    def test_embed_reports_cache_hit_rate(self):
        d = O.collect(self.p, days=30, sections=["embed"])
        self.assertIn("cache_hit_rate", d["embed"])
        self.assertGreaterEqual(d["embed"]["cache_hit_rate"], 0.0)


class RedactTest(_Base):
    """`users` 절은 **누가 얼마나 썼나** 다 — admin 만 본다.

    왜 (2026-09-20 정렬 감사): `GET /api/opstats` 는 read 등급이라 viewer 도 부를 수 있는데,
    같은 값을 주는 `GET /api/query_users` 는 admin 전용이다. 가려 두지 않으면 **막아 둔 문을
    옆문으로 여는 것**이 된다. 거르는 자리는 집계 함수 안이 아니라 **내보내는 자리**다
    (CLI 는 이미 security.json 등급표로 실행자를 검사한 뒤라 그대로 본다).
    """

    def test_admin_sees_everything(self):
        d = O.collect(self.p, days=30)
        self.assertEqual(O.redact(d, admin=True), d, "admin 에게는 아무것도 가리지 않는다")

    def test_non_admin_loses_the_users_section(self):
        d = O.collect(self.p, days=30)
        self.assertIn("users", d, "가릴 대상이 애초에 없으면 이 테스트가 무의미하다")
        r = O.redact(d, admin=False)
        self.assertNotIn("users", r)
        self.assertNotIn("users", r.get("sections") or [], "sections 목록에도 남으면 화면이 빈 절을 그린다")

    def test_redaction_says_why(self):
        """가린 사실을 감추지 않는다 — 화면이 '왜 안 보이는지' 를 말할 수 있어야 한다."""
        r = O.redact(O.collect(self.p, days=30), admin=False)
        why = [x["reason"] for x in (r.get("redacted") or []) if x.get("section") == "users"]
        self.assertTrue(why and "admin" in why[0], r.get("redacted"))

    def test_other_sections_survive(self):
        r = O.redact(O.collect(self.p, days=30), admin=False)
        for s in ("index", "queries", "latency", "storage"):
            self.assertIn(s, r, "%s 까지 사라지면 viewer 에게 화면이 비어 버린다" % s)

    def test_every_admin_only_section_is_a_real_section(self):
        """가림 목록이 오타로 아무것도 안 가리는 상태가 되지 않게."""
        for k in O.ADMIN_ONLY_SECTIONS:
            self.assertIn(k, O.SECTIONS, k)


class EmptyEnvironmentTest(unittest.TestCase):
    """새로 세운 환경 — 데이터가 하나도 없어도 화면이 죽으면 안 된다."""

    def test_collect_on_empty_index(self):
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        tmp = tempfile.mkdtemp(prefix="llmwiki_ops0_")
        saved = os.environ.get("LLMWIKI_LOGS_DIR_PATH")
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(tmp, "logs")
        try:
            corpus = os.path.join(tmp, "corpus")
            os.makedirs(corpus)
            s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data"),
                         wiki_dir=os.path.join(tmp, "wiki"),
                         llm_provider="mock", embed_provider="hash", embed_dim=64)
            s.toggles = Toggles(llm_graph=False, community_summary=False)
            p = Pipeline(s)
            try:
                d = O.collect(p, days=7)
                self.assertEqual(d["index"]["docs"], 0)
                self.assertEqual(d["queries"]["total"], 0)
                self.assertIsNone(d["latency"]["p95_ms"])
                self.assertIn("빌드 기록이 없습니다", O.format_text(d))
            finally:
                p.store.close()
        finally:
            if saved is None:
                os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
            else:
                os.environ["LLMWIKI_LOGS_DIR_PATH"] = saved
            shutil.rmtree(tmp, ignore_errors=True)


class FormatTest(_Base):
    def test_text_render_covers_every_section(self):
        txt = O.format_text(O.collect(self.p, days=30))
        for s in O.SECTIONS:
            self.assertIn("[%s]" % s, txt, s)

    def test_text_render_of_a_subset(self):
        txt = O.format_text(O.collect(self.p, days=30, sections=["index"]))
        self.assertIn("[index]", txt)
        self.assertNotIn("[tokens]", txt)


if __name__ == "__main__":
    unittest.main()
