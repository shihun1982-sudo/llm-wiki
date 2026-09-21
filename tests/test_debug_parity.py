# -*- coding: utf-8 -*-
"""Ask 디버그(질의 해부)가 **실제 질의 경로와 같은 것을 보여 주는가**.

왜 이 테스트가 있나 (2026-09-20):
  "ask debug 는 실제로 사용자 시나리오와 동일하게 동작해서 보여주는 거지?" 라는 물음에 확인해 보니
  **세 군데가 달랐다.**

  | 단계 | 실제 질의 경로(query_engine) | 해부 화면(querydebug) — 고치기 전 |
  |---|---|---|
  | 규칙 확장 | `expand(q_search, …)` — 시간 표현을 뗀 뒤의 말 | `expand(q, …)` — **원문** |
  | 채널 라우팅 | `route(q_search, store)` | `route(q, store)` — **원문** |
  | 고정 근거 | `match_pins(store, q, route_info["doc_types"])` | `match_pins(store, q)` — **doc_types 없음** |

  그래서 "지난주 ISSUE-2001 의 원인" 같은 질의에서 화면과 실제가 달랐다. 디버그 화면이 거짓말을 하면
  그걸로 내린 판단이 전부 틀어지므로, 같은 입력을 쓰는지 **코드로** 지킨다.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import querydebug as qd      # noqa: E402

DOC = """---
doc_type: issue
ext_id: ISSUE-2001
---

# RX DMA underrun

RX DMA 에서 underrun 이 발생하면 PHY 재시작이 실패한다. 원인은 클럭 게이팅 타이밍이다.
AGC 수렴 지연과는 다른 문제다. CL-55302 에서 고쳤다.
"""


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        cls.tmp = tempfile.mkdtemp(prefix="llmwiki_dbgp_")
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        with open(os.path.join(corpus, "issue.md"), "w", encoding="utf-8") as f:
            f.write(DOC * 4)
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"),
                     wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False, query_cache=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TimeStripParityTest(_Base):
    """시간 표현을 뗀 뒤의 말로 계산하는가 (실제 경로와 같은 순서)."""

    def test_time_expression_is_stripped_before_the_rest(self):
        d = qd.inspect_query(self.p, "지난주 ISSUE-2001 의 원인")
        t = d["time"]
        self.assertTrue(t["scope"], "'지난주' 가 해석되어야 한다")
        self.assertTrue(t["stripped"], "시간 표현을 뗐다고 보고해야 한다")
        self.assertNotIn("지난주", t["q_search"])

    def test_rule_expansion_uses_the_stripped_query(self):
        d = qd.inspect_query(self.p, "지난주 ISSUE-2001 의 원인")
        self.assertEqual(d["fts"]["input"], d["time"]["q_search"])
        self.assertNotIn("지난주", d["fts"]["match_expr"] or "")

    def test_without_a_time_expression_nothing_is_stripped(self):
        d = qd.inspect_query(self.p, "ISSUE-2001 의 원인")
        self.assertFalse(d["time"]["stripped"])
        self.assertEqual(d["fts"]["input"], "ISSUE-2001 의 원인")

    def test_matches_the_engine_for_the_same_query(self):
        """엔진이 쓰는 함수·인자와 같은지 — 같은 입력을 주면 같은 값이 나와야 한다."""
        from llmwiki import query_rules as _qr
        from llmwiki import tuning as _tn
        from llmwiki.retrieval import route
        q = "지난주 ISSUE-2001 의 원인"
        d = qd.inspect_query(self.p, q)
        qs = d["time"]["q_search"]
        T = _tn.T
        eng = _qr.expand(qs, T.get("syn_w"), T.get("related_w"), T.get("acronym_phrase"))
        self.assertEqual(d["fts"]["rule_match"], eng.get("fts_query"))
        self.assertEqual(d["router"]["weights"], route(qs, self.p.store)["weights"])

    def test_pins_get_the_router_doc_types(self):
        """실제 경로는 라우터가 고른 문서 유형까지 pin 판정에 넘긴다."""
        import inspect as _i
        src = _i.getsource(qd.inspect_query)
        self.assertIn('match_pins(store, q, (rt or {}).get("doc_types"))', src)

    def test_source_stays_aligned_with_the_engine(self):
        """엔진이 인자를 바꾸면 여기서 먼저 깨지게 — 조용히 어긋나는 것을 막는다."""
        import inspect as _i
        from llmwiki import query_engine as _qe
        eng = _i.getsource(_qe.QueryEngine.run) if hasattr(_qe, "QueryEngine") else _i.getsource(_qe)
        self.assertIn('_qr.expand(q_search, T.get("syn_w"), T.get("related_w"), T.get("acronym_phrase"))', eng)
        self.assertIn("route(q_search, p.store)", eng)


class RouterExplainTest(_Base):
    """'유형 relational' 이 무엇인지 화면이 답하는가."""

    def test_kind_comes_with_a_label_and_description(self):
        d = qd.inspect_query(self.p, "ISSUE-2001 의 원인은 무엇인가")
        r = d["router"]
        self.assertEqual(r["kind"], "relational")
        self.assertTrue(r["kind_label"])
        self.assertIn("그래프", r["kind_desc"])

    def test_why_lists_the_actual_signals(self):
        d = qd.inspect_query(self.p, "ISSUE-2001 의 원인은 무엇인가")
        why = " / ".join(d["router"]["why"])
        self.assertIn("원인", why, "어떤 낱말 때문에 관계형이 됐는지 말해야 한다")

    def test_matched_relational_words_are_reported(self):
        d = qd.inspect_query(self.p, "ISSUE-2001 의 원인은 무엇인가")
        self.assertIn("원인", d["router"]["matched_rel_words"])

    def test_plain_query_is_not_relational(self):
        d = qd.inspect_query(self.p, "RX DMA")
        self.assertNotEqual(d["router"]["kind"], "relational")
        self.assertTrue(d["router"]["why"])

    def test_every_kind_is_described(self):
        from llmwiki.retrieval import ROUTER_KINDS, describe_router_kinds
        for k in ("keyword", "semantic", "relational", "hybrid"):
            self.assertIn(k, ROUTER_KINDS, k)
            self.assertTrue(ROUTER_KINDS[k]["label"] and ROUTER_KINDS[k]["desc"], k)
        self.assertEqual(len(describe_router_kinds()), len(ROUTER_KINDS))

    def test_text_render_shows_the_reason(self):
        txt = qd.render_text(qd.inspect_query(self.p, "ISSUE-2001 의 원인은 무엇인가"))
        self.assertIn("근거", txt)
        self.assertIn("관계형", txt)


class ToggleReportTest(_Base):
    def test_toggles_are_reported_for_every_stage_shown(self):
        d = qd.inspect_query(self.p, "ISSUE-2001")
        for k in ("query_rules", "time_scope", "router", "pins", "fts", "vector", "graph"):
            self.assertIn(k, d["toggles"], k)

    def test_turning_a_stage_off_changes_the_result(self):
        """토글 줄이 '보여주기용' 이 아니라 실제로 결과를 바꾸는지 — 임시 시험의 전제."""
        self.p.s.toggles.time_scope = False
        try:
            d = qd.inspect_query(self.p, "지난주 ISSUE-2001 의 원인")
            self.assertFalse(d["time"]["stripped"], "time_scope 가 꺼지면 시간 표현을 떼지 않는다")
            self.assertEqual(d["fts"]["input"], "지난주 ISSUE-2001 의 원인")
        finally:
            self.p.s.toggles.time_scope = True


if __name__ == "__main__":
    unittest.main()
