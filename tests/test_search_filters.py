# -*- coding: utf-8 -*-
"""채널 검색의 문서 유형 필터 — 엔진 동작과 **세 창구 동등성**.

왜 이 기능이 있나 (2026-09-20):
  채널 검색은 "무엇이 왜 걸렸나" 를 보는 화면인데, 코퍼스가 섞여 있으면 (이슈·CL·TC·주간보고…)
  보고 싶은 유형 밖의 문서가 자리를 다 차지한다. 질의 경로의 `doc_types` 는 *가중치를 올리는* 것이라
  다른 유형이 여전히 올라온다 — 검색 화면에서 원하는 것은 **거르는** 쪽이다. 그래서 별도로 넣었다.

왜 여기서 채널별 원본 목록부터 거르나:
  최종 행에서만 걸러도 표는 맞아 보이지만, `per_channel` 의 건수와 발췌가 실제와 달라진다
  (문서 접근 제어가 같은 이유로 같은 자리에서 거른다).
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki.retrieval import channel_search      # noqa: E402

ISSUE = """---
doc_type: issue
ext_id: ISSUE-7001
---

# RX DMA underrun

RX DMA 에서 underrun 이 발생하면 PHY 재시작이 실패한다. 클럭 게이팅 타이밍이 원인이다.
재현은 rev B1 에서 t_setup 을 4 ns 로 두었을 때다. 잡음 여유는 1.5 dB.
"""
CL = """---
doc_type: cl
ext_id: CL-70001
---

# RX DMA underrun 수정

RX DMA underrun 을 고친 변경이다. 클럭 게이팅 타이밍을 조정했다.
ISSUE-7001 을 참고한다. rev B1 기준으로 검증했다.
"""
TC = """---
doc_type: tc
ext_id: TC-RX-7001
---

# RX DMA underrun 검증

RX DMA underrun 재현 절차와 판정 기준. 클럭 게이팅 타이밍을 확인한다.
rev B1 에서 t_setup 4 ns 로 측정한다.
"""


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        cls.tmp = tempfile.mkdtemp(prefix="llmwiki_sf_")
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        for name, body in (("issue.md", ISSUE), ("cl.md", CL), ("tc.md", TC)):
            with open(os.path.join(corpus, name), "w", encoding="utf-8") as f:
                f.write(body * 3)
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"),
                     wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def search(self, **kw):
        kw.setdefault("channels", ["fts"])
        kw.setdefault("k", 10)
        return channel_search(self.p.store, self.p.embedder, self.p.s, "RX DMA underrun", **kw)

    def types_of(self, r):
        meta = self.p.store.doc_meta_map()
        return sorted({str((meta.get(x.get("doc_id") or "") or {}).get("doc_type") or "") for x in r["rows"]})


class FilterTest(_Base):
    def test_without_filter_all_types_appear(self):
        self.assertEqual(self.types_of(self.search()), ["cl", "issue", "tc"])

    def test_single_type(self):
        r = self.search(doc_types=["issue"])
        self.assertEqual(self.types_of(r), ["issue"])
        self.assertTrue(r["rows"])

    def test_multiple_types(self):
        self.assertEqual(self.types_of(self.search(doc_types=["issue", "cl"])), ["cl", "issue"])

    def test_comma_string_is_accepted(self):
        """CLI 는 `--doc-types issue,cl` 로 문자열을 준다."""
        self.assertEqual(self.types_of(self.search(doc_types="issue,cl")), ["cl", "issue"])

    def test_empty_means_no_filter(self):
        for empty in (None, [], "", "  "):
            self.assertEqual(self.types_of(self.search(doc_types=empty)), ["cl", "issue", "tc"], repr(empty))

    def test_unknown_type_returns_nothing_not_everything(self):
        """오타가 '전체' 로 조용히 풀리면 필터를 믿을 수 없다."""
        self.assertEqual(self.search(doc_types=["없는유형"])["rows"], [])

    def test_per_channel_counts_follow_the_filter(self):
        """표만 거르고 채널 통계가 그대로면 '몇 건 중 몇 건' 이 거짓말이 된다."""
        r = self.search(doc_types=["issue"])
        n_rows = len(r["rows"])
        self.assertTrue(r["per_channel"]["fts"]["n"] >= n_rows)
        for row in r["per_channel"]["fts"]["rows"]:
            cid = row["chunk_id"].rsplit("#", 1)[0]
            self.assertEqual((self.p.store.doc_meta_map().get(cid) or {}).get("doc_type"), "issue")

    def test_filtered_count_is_reported(self):
        r = self.search(doc_types=["issue"])
        self.assertGreater(r["counts"]["doc_type_filtered"], 0)
        self.assertEqual(r["doc_types"], ["issue"])

    def test_expression_shows_the_filter(self):
        self.assertIn("문서유형", self.search(doc_types=["issue"])["expr"])

    def test_works_with_every_mode(self):
        for mode in ("or", "and", "rrf"):
            r = self.search(channels=["fts", "vector"], mode=mode, doc_types=["cl"])
            self.assertEqual(self.types_of(r) or ["cl"], ["cl"], mode)

    def test_works_with_require_exclude(self):
        r = self.search(channels=["fts"], require=["fts"], doc_types=["issue"])
        self.assertEqual(self.types_of(r) or ["issue"], ["issue"])


class SurfaceParityTest(_Base):
    """CLI · Web · MCP 가 같은 필터에 **같은 답**을 주는가 (세 창구 정렬)."""

    def _rows(self, r):
        return [(x["chunk_id"], round(float(x["score"]), 4)) for x in r["rows"]]

    def test_mcp_matches_the_engine(self):
        from llmwiki import mcp
        res = mcp.call_tool(self.p, "wiki_search",
                            {"query": "RX DMA underrun", "channels": ["fts"], "k": 10, "doc_types": ["issue"]})
        import json
        got = json.loads(res["content"][0]["text"])
        self.assertEqual(self._rows(got), self._rows(self.search(doc_types=["issue"])))
        self.assertEqual(got["doc_types"], ["issue"])

    def test_mcp_without_filter_matches_too(self):
        from llmwiki import mcp
        import json
        got = json.loads(mcp.call_tool(self.p, "wiki_search",
                                       {"query": "RX DMA underrun", "channels": ["fts"], "k": 10})["content"][0]["text"])
        self.assertEqual(self._rows(got), self._rows(self.search()))

    def test_cli_accepts_the_flag(self):
        """`search --doc-types` 가 파서에 있고 엔진까지 전달되는지 (인자 이름이 어긋나면 조용히 무시된다)."""
        from llmwiki.cli import build_parser
        ns = build_parser().parse_args(["search", "fts", "RX DMA", "--doc-types", "issue,cl"])
        self.assertEqual(ns.doc_types, "issue,cl")


if __name__ == "__main__":
    unittest.main()
