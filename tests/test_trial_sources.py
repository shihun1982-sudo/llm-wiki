# -*- coding: utf-8 -*-
"""Trial 비교의 두 확장 — **실제 질의 이력**을 문항으로, **단계별** 지표 비교.

왜 (2026-09-20 요청):
  "Trial 비교는 테스트 몇 개를 비교하는 거야? 실제 질의 history 를 보든지 해야 하는 거 아니야?
   그리고 볼 수 있는 지표가 제한적인데, 각 단계·역할별로 모든 지표를 비교할 수 있어야 하는 거 아니야?"

확인한 사실:
  - `run_trial` 은 `evalset.load_questions()` 의 **고정 25문항**만 썼다. 고를 방법이 없었다.
  - `query_log` 에 실제 질의가 쌓이는데 trial 은 그것을 **보지 않았다.**
  - 저장하는 지표는 **파이프라인 최종 결과 12개**뿐. 각 문항의 요청 trace 에 단계별 ms·토큰·호출수가
    이미 있는데 버리고 있었다 — "rerank 를 켜서 느려진 만큼 값어치를 했나" 에 답할 수 없었다.

이 테스트가 지키는 것:
  - 이력 문항은 중복을 지우고 기간·건수·피드백으로 거른다
  - 정답이 없는 원천이면 **계산 불가 지표를 명시**한다 (빈칸을 0점으로 오해하지 않게)
  - 단계별 집계가 실제 trace 에서 나오고, 비교가 **달라진 단계를 앞에** 놓는다
  - 예전 trial(단계 기록 없음)과 섞여도 깨지지 않는다
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import evalset as E      # noqa: E402
from llmwiki import trials as T       # noqa: E402

DOC = """---
doc_type: issue
ext_id: ISSUE-{n}
---

# RX DMA underrun {n}

RX DMA 에서 underrun 이 발생하면 PHY 재시작이 실패한다. 클럭 게이팅 타이밍이 원인이다.
rev B1 에서 t_setup 은 4 ns 이다. AGC 수렴 지연은 별개 문제다.
"""


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        cls.tmp = tempfile.mkdtemp(prefix="llmwiki_ts_")
        cls._saved_logs = os.environ.get("LLMWIKI_LOGS_DIR_PATH")
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(cls.tmp, "logs")
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        for i in range(4):
            with open(os.path.join(corpus, "d%d.md" % i), "w", encoding="utf-8") as f:
                f.write(DOC.format(n=7000 + i) * 3)
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"),
                     wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False, query_cache=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        if cls._saved_logs is None:
            os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
        else:
            os.environ["LLMWIKI_LOGS_DIR_PATH"] = cls._saved_logs
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def logq(self, q, feedback=None):
        # 2026-09-23: 질의 이력의 원천이 query_log → requests(kind='query') 로 바뀌었다.
        # 질의 결과의 `query_id` 가 곧 request id 이므로 그것으로 피드백을 단다.
        r = self.p.query(q, log=True)
        qid = (r[0] or {}).get("query_id")
        if feedback is not None and qid:
            self.p.store.set_feedback(int(qid), feedback, "")
        return r

    def clear_history(self):
        """질의 이력을 비운다 (테스트 격리). 원천이 requests 이므로 그쪽도 함께 비운다."""
        c = self.p.store.conn
        c.execute("DELETE FROM query_log")
        c.execute("DELETE FROM requests WHERE kind='query'")
        c.execute("DELETE FROM query_feedback")
        c.commit()


class FromQueryLogTest(_Base):
    def setUp(self):
        self.clear_history()

    def test_takes_questions_from_the_log(self):
        self.logq("RX DMA underrun 원인")
        self.logq("AGC 수렴 지연")
        got = E.from_query_log(self.p.store, days=7, limit=10)
        self.assertEqual({x["q"] for x in got["questions"]}, {"RX DMA underrun 원인", "AGC 수렴 지연"})
        self.assertEqual(got["source"]["kind"], "queries")

    def test_duplicates_are_collapsed(self):
        for _ in range(3):
            self.logq("RX DMA underrun 원인")
        self.assertEqual(len(E.from_query_log(self.p.store, days=7, limit=10)["questions"]), 1)

    def test_limit_is_respected(self):
        for i in range(5):
            self.logq("질문 %d 번 RX DMA" % i)
        self.assertEqual(len(E.from_query_log(self.p.store, days=7, limit=2)["questions"]), 2)

    def test_old_queries_are_excluded_by_days(self):
        self.logq("RX DMA underrun 원인")
        # 이력의 원천이 requests 이므로 그쪽 시각을 옮긴다 (2026-09-23)
        old = time.time() - 40 * 86400
        self.p.store.conn.execute("UPDATE requests SET ts=? WHERE kind='query'", (old,))
        self.p.store.conn.execute("UPDATE query_log SET ts=?", (old,))
        self.p.store.conn.commit()
        self.assertEqual(E.from_query_log(self.p.store, days=7, limit=10)["questions"], [])
        self.assertTrue(E.from_query_log(self.p.store, days=90, limit=10)["questions"])

    def test_only_negative_filters_by_feedback(self):
        self.logq("좋은 질문 RX DMA", feedback=1)
        self.logq("나쁜 질문 AGC", feedback=-1)
        self.logq("평가 없는 질문 PHY")
        neg = [x["q"] for x in E.from_query_log(self.p.store, days=7, limit=10, only="negative")["questions"]]
        self.assertEqual(neg, ["나쁜 질문 AGC"])
        fb = {x["q"] for x in E.from_query_log(self.p.store, days=7, limit=10, only="feedback")["questions"]}
        self.assertEqual(fb, {"좋은 질문 RX DMA", "나쁜 질문 AGC"})

    def test_too_short_queries_are_skipped(self):
        self.logq("ab")
        self.assertEqual(E.from_query_log(self.p.store, days=7, limit=10)["questions"], [])

    def test_source_says_why_metrics_are_missing(self):
        self.logq("RX DMA underrun 원인")
        self.assertIn("정답이 없어", E.from_query_log(self.p.store, days=7, limit=5)["source"]["note"])

    def test_empty_log_is_not_an_error(self):
        got = E.from_query_log(self.p.store, days=7, limit=5)
        self.assertEqual(got["questions"], [])
        self.assertEqual(got["source"]["n"], 0)


class StageAggregateTest(_Base):
    def test_stages_are_collected_from_real_traces(self):
        r = T.run_trial(self.p, "stg-a", [{"q": "RX DMA underrun 원인", "expect_docs": ["ISSUE-7000"]}], k=5)
        st = r["summary"]["_stages"]
        self.assertGreater(st["n_requests"], 0)
        self.assertGreater(len(st["stages"]), 5, "단계가 여러 개 잡혀야 한다")
        for name, d in st["stages"].items():
            self.assertIn("avg_ms", d, name)
            self.assertIn("tokens", d, name)
            self.assertGreaterEqual(d["avg_ms"], 0, name)

    def test_known_stages_appear(self):
        r = T.run_trial(self.p, "stg-b", [{"q": "RX DMA underrun 원인"}], k=5)
        names = set(r["summary"]["_stages"]["stages"])
        self.assertTrue({"fts_search", "rrf_fuse", "context"} & names, names)

    def test_repeated_stage_is_summed_not_overwritten(self):
        """fts_search_alt 처럼 한 질의에서 여러 번 도는 단계가 있다."""
        r = T.run_trial(self.p, "stg-c", [{"q": "RX DMA underrun 원인"}], k=5)
        for name, d in r["summary"]["_stages"]["stages"].items():
            self.assertGreaterEqual(d["n"], 1, name)
            self.assertGreaterEqual(d["runs_per_query"], 0, name)


class StageCompareTest(_Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        qs = [{"q": "RX DMA underrun 원인", "expect_docs": ["ISSUE-7000"]},
              {"q": "AGC 수렴 지연", "expect_docs": ["ISSUE-7001"]}]
        cls.a = T.run_trial(cls.p, "cmp-a", qs, k=5)
        cls.b = T.run_trial(cls.p, "cmp-b", qs, k=5, overrides={"top_k_final": 12})

    def test_compare_has_a_stage_section(self):
        c = T.compare(self.p.store, ["cmp-a", "cmp-b"])
        self.assertTrue(c["stages"]["available"])
        self.assertTrue(c["stages"]["rows"])

    def test_stage_rows_carry_deltas(self):
        c = T.compare(self.p.store, ["cmp-a", "cmp-b"])
        row = c["stages"]["rows"][0]
        self.assertEqual(len(row["cells"]), 2)
        self.assertEqual(len(row["delta_ms"]), 2)
        self.assertIsNone(row["delta_ms"][0], "기준 trial 의 delta 는 없다")

    def test_changed_stages_come_first(self):
        c = T.compare(self.p.store, ["cmp-a", "cmp-b"])
        flags = [r["changed"] for r in c["stages"]["rows"]]
        self.assertEqual(flags, sorted(flags, reverse=True), "달라진 단계가 앞에 와야 눈에 띈다")

    def test_markdown_includes_stages(self):
        md = T.report_md(T.compare(self.p.store, ["cmp-a", "cmp-b"]))
        self.assertIn("## 단계별", md)
        self.assertIn("## 판정", md)

    def test_old_trial_without_stages_degrades_gracefully(self):
        """2026-09-20 이전 trial 에는 `_stages` 가 없다 — 깨지지 말고 이유를 말해야 한다."""
        row = self.p.store.conn.execute("SELECT summary FROM trials WHERE name=?", ("cmp-b",)).fetchone()
        summ = json.loads(row[0])
        summ.pop("_stages", None)
        self.p.store.conn.execute("UPDATE trials SET summary=? WHERE name=?",
                                  (json.dumps(summ, ensure_ascii=False), "cmp-b"))
        self.p.store.conn.commit()
        try:
            c = T.compare(self.p.store, ["cmp-a", "cmp-b"])
            self.assertFalse(c["stages"]["available"])
            self.assertIn("다시 돌리면", c["stages"]["note"])
            self.assertIn("## 단계별", T.report_md(c))       # 그래도 절은 나오고 이유를 적는다
        finally:
            summ["_stages"] = T.collect_stages(self.p.store, json.loads(
                self.p.store.conn.execute("SELECT rows FROM trials WHERE name=?", ("cmp-b",)).fetchone()[0]))
            self.p.store.conn.execute("UPDATE trials SET summary=? WHERE name=?",
                                      (json.dumps(summ, ensure_ascii=False), "cmp-b"))
            self.p.store.conn.commit()


class UnavailableMetricsTest(_Base):
    def test_query_history_trial_marks_ground_truth_metrics_unavailable(self):
        self.logq("RX DMA underrun 원인")
        got = E.from_query_log(self.p.store, days=7, limit=3)
        T.run_trial(self.p, "gt-eval", [{"q": "RX DMA underrun 원인", "expect_docs": ["ISSUE-7000"]}], k=5)
        T.run_trial(self.p, "gt-hist", got["questions"], k=5, source=got["source"])
        c = T.compare(self.p.store, ["gt-eval", "gt-hist"])
        self.assertEqual(sorted(c["unavailable_metrics"]), sorted(T.NEEDS_GROUND_TRUTH))
        self.assertIn("0점", c["unavailable_why"])
        self.assertIn("계산할 수 없음", c["unavailable_why"])

    def test_evalset_only_comparison_has_no_unavailable_metrics(self):
        qs = [{"q": "RX DMA underrun 원인", "expect_docs": ["ISSUE-7000"]}]
        T.run_trial(self.p, "ev-1", qs, k=5)
        T.run_trial(self.p, "ev-2", qs, k=5)
        self.assertEqual(T.compare(self.p.store, ["ev-1", "ev-2"])["unavailable_metrics"], [])

    def test_source_is_recorded_on_every_trial(self):
        T.run_trial(self.p, "src-default", [{"q": "RX DMA underrun 원인"}], k=5)
        t = T.get_trial(self.p.store, "src-default")
        self.assertEqual((t["summary"].get("_source") or {}).get("kind"), "evalset")

    def test_markdown_warns_about_unavailable(self):
        self.logq("RX DMA underrun 원인")
        got = E.from_query_log(self.p.store, days=7, limit=3)
        T.run_trial(self.p, "md-eval", [{"q": "RX DMA underrun 원인", "expect_docs": ["ISSUE-7000"]}], k=5)
        T.run_trial(self.p, "md-hist", got["questions"], k=5, source=got["source"])
        md = T.report_md(T.compare(self.p.store, ["md-eval", "md-hist"]))
        self.assertIn("계산할 수 없는 지표", md)
        self.assertIn("실제 질의 이력", md)


if __name__ == "__main__":
    unittest.main()
