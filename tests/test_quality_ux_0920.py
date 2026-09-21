# -*- coding: utf-8 -*-
"""Quality/Observability 화면이 **의도한 것을 보여 주는가** (2026-09-20 요청).

사용자가 화면을 실제로 써 보고 세 가지를 지적했다. 셋 다 "기능은 있는데 화면이 쓸모없다" 였다.

| 지적 | 실제로 무엇이었나 | 여기서 지키는 것 |
|---|---|---|
| Trial 비교가 실제 데이터를 못 비교하는 것 같다 | 질의 이력 trial 의 hit@k 가 **0.0 으로 저장**돼 "완전 실패" 로 보였다 | 정답이 없으면 `None` (§UngradedTrialTest) |
| 포렌식이 쓸모없다 | 기록 1,122건 중 1,025건이 정상이라 목록이 정상 건으로 덮였다 | 기본이 **문제 건만** (§ForensicFilterTest) |
| 통계에 추세·차트가 없다 | 한 시점 값만 있어 "나아지나" 에 답할 수 없었다 | 일/주/월 추세 (§TrendTest) |

세 가지 모두 **숫자를 만드는 쪽**에서 고쳤다 — 화면에서만 가리면 CLI·MCP 와 다른 말을 하게 된다.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import forensic as FX      # noqa: E402
from llmwiki import opstats as O        # noqa: E402
from llmwiki import trials as TR        # noqa: E402

DOC = """---
doc_type: issue
ext_id: ISSUE-9{n}
---

# RX DMA underrun {n}

RX DMA 에서 underrun 이 발생하면 PHY 재시작이 실패한다. rev B1 에서 t_setup 은 4 ns 이다.
"""


class _Env(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        cls.tmp = tempfile.mkdtemp(prefix="llmwiki_qux_")
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
        for q in ("RX DMA underrun 원인", "rev B1 t_setup", "PHY 재시작 실패"):
            cls.p.query(q, log=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        if cls._saved is None:
            os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
        else:
            os.environ["LLMWIKI_LOGS_DIR_PATH"] = cls._saved
        shutil.rmtree(cls.tmp, ignore_errors=True)


# ---------------------------------------------------------------- Trial
class UngradedTrialTest(_Env):
    """**정답이 없으면 0 이 아니라 공백이다.**

    `evaluate()` 는 기대 문서가 없으면 "못 맞혔다" 로 세어 hit@k 가 0.0 이 된다. 실제 질의 이력으로
    돌린 trial 이 목록에서 `hit@k 0.000` 으로 보이면 **완전히 실패한 설정으로 읽힌다** — 실제로는
    채점할 정답이 없었을 뿐이다. 저장할 때와 읽을 때 양쪽에서 같은 규칙을 걸어 어느 화면에서 보든 같게 한다.
    """

    def test_detects_ground_truth(self):
        self.assertTrue(TR._has_ground_truth([{"q": "a", "expect_docs": ["ISSUE-1"]}]))
        self.assertTrue(TR._has_ground_truth([{"q": "a"}, {"q": "b", "expect_terms": ["4 ns"]}]))
        self.assertFalse(TR._has_ground_truth([{"q": "a"}, {"q": "b"}]))
        self.assertFalse(TR._has_ground_truth([]))

    def test_ground_truth_keys_match_the_scorer(self):
        """**채점기가 실제로 읽는 키**와 같아야 한다.

        2026-09-20 에 `docs`/`terms` 로 잘못 적었다가 평가셋 문항까지 '채점 불가' 로 가려져
        hit@k 가 통째로 사라졌다. 이름을 두 곳에 적어 두면 또 어긋나므로, 채점기 소스에서
        읽는 키를 직접 확인한다.
        """
        import inspect
        from llmwiki import evalset as ES
        src = inspect.getsource(ES.score_result)
        for k in TR.GROUND_TRUTH_KEYS:
            self.assertIn('"%s"' % k, src,
                          "score_result() 가 읽지 않는 키 %r 를 정답 기준으로 쓰고 있다" % k)

    def test_masks_old_rows_on_read(self):
        """2026-09-20 이전에 0.0 으로 저장된 trial 도 읽을 때 공백이 된다."""
        old = {"n": 5, "hit@k": 0.0, "mrr": 0.0, "term_recall": 0.0, "groundedness": 0.4,
               "_source": {"kind": "queries", "n": 5}}
        m = TR._mask_ungraded(old)
        for k in TR.NEEDS_GROUND_TRUTH:
            self.assertIsNone(m[k], k)
        self.assertEqual(m["groundedness"], 0.4, "정답이 필요 없는 지표는 그대로 둔다")
        self.assertTrue(m["_no_ground_truth"])

    def test_evalset_rows_are_untouched(self):
        ev = {"n": 25, "hit@k": 0.92, "mrr": 0.75, "_source": {"kind": "evalset"}}
        self.assertEqual(TR._mask_ungraded(ev)["hit@k"], 0.92)

    def test_real_run_on_query_history_leaves_blanks(self):
        """실제로 질의 이력으로 trial 을 돌려 본다 (mock LLM)."""
        from llmwiki.evalset import from_query_log
        got = from_query_log(self.p.store, days=30, limit=3)
        self.assertTrue(got["questions"], "질의 로그에서 문항을 못 뽑았다 — 이 테스트가 무의미해진다")
        r = TR.run_trial(self.p, "q-hist", questions=got["questions"], k=3, source=got["source"])
        for k in TR.NEEDS_GROUND_TRUTH:
            self.assertIsNone(r["summary"][k], "%s 가 0 으로 저장됐다 — 실패한 설정으로 읽힌다" % k)
        self.assertIsNotNone(r["summary"]["insufficient_rate"], "정답 없이도 낼 수 있는 지표는 있어야 한다")

    def test_list_exposes_source_and_graded(self):
        rows = TR.list_trials(self.p.store, 10)
        self.assertTrue(rows, "trial 이 없다")
        r = rows[0]
        self.assertIn("source", r, "화면·CLI 가 '무엇으로 돌렸나' 를 보여 줄 수 없다")
        self.assertIn("graded", r)

    def test_compare_flags_mixed_sources(self):
        """평가셋 trial 과 질의 이력 trial 을 같이 놓으면 **문항 자체가 다르다** — 먼저 말해 줘야 한다."""
        ev = TR.run_trial(self.p, "ev", questions=[{"q": "RX DMA underrun 원인", "expect_docs": ["ISSUE-90"]}], k=3)
        from llmwiki.evalset import from_query_log
        got = from_query_log(self.p.store, days=30, limit=3)
        qh = TR.run_trial(self.p, "qh", questions=got["questions"], k=3, source=got["source"])
        c = TR.compare(self.p.store, [ev["trial_id"], qh["trial_id"]])
        self.assertTrue(c["mixed_sources"])
        self.assertTrue(c["mixed_why"])
        self.assertEqual(set(c["unavailable_metrics"]), set(TR.NEEDS_GROUND_TRUTH))
        self.assertTrue(c["comparable_metrics"], "읽을 수 있는 지표를 알려 줘야 한다")
        for m in TR.NEEDS_GROUND_TRUTH:
            self.assertNotIn(m, c["comparable_metrics"])


class PickQuestionsTest(_Env):
    """**과거 질의를 직접 고를 수 있어야** 비교가 내 관심사 위에서 돈다.

    왜 (2026-09-20 요청: *"과거 질의를 어디서 선택할 수 있어? 선택을 해야 비교를 하지"*):
    `from_query_log` 는 기간·건수·피드백으로 뭉뚱그려 고른다. 그런데 비교하고 싶은 질의는 대개
    몇 개로 정해져 있다 — "이 세 질문이 느린데 설정을 바꾸면 나아지나".
    """

    def _ids(self, n=3):
        from llmwiki.evalset import from_query_log
        rows = from_query_log(self.p.store, days=365, limit=20)["questions"]
        return [r["from_query_id"] for r in rows[:n]]

    def test_candidates_carry_what_you_need_to_choose(self):
        from llmwiki.evalset import from_query_log
        rows = from_query_log(self.p.store, days=365, limit=20)["questions"]
        self.assertTrue(rows, "질의 로그에서 후보를 못 뽑았다 — 이 테스트가 무의미해진다")
        for k in ("q", "from_query_id", "ts", "feedback", "verdict", "groundedness", "insufficient"):
            self.assertIn(k, rows[0], "고를 때 판단할 값 %s 가 없다" % k)
        self.assertTrue(any(r.get("verdict") for r in rows),
                        "판정이 전부 비어 있다 — 화면에 빈 칸만 뜨고 거르기가 아무것도 못 고른다")

    def test_verdict_comes_from_the_field_that_actually_exists(self):
        """**`query_log.scores` 에 있는 키**를 봐야 한다.

        2026-09-20 에 `answer_mode` 를 보고 있었는데 그 키는 `scores` 에 없다(요청 결과에만 있다).
        그래서 `--only insufficient` 가 **항상 0건**이었다 — 필터가 죽어 있었고 아무도 몰랐다.
        """
        from llmwiki import evalset as ES
        row = {"scores": '{"verdict": "insufficient", "groundedness": 0.1}'}
        self.assertEqual(ES._verdict_of(row), "insufficient")
        self.assertEqual(ES._scores_of(row).get("groundedness"), 0.1)
        self.assertEqual(ES._verdict_of({"scores": None}), "")

    def test_only_filter_actually_filters(self):
        """거르기가 **실제로 거르는지** — 통과만 하고 아무것도 안 거르면 있으나 마나다."""
        import json as _j
        now = time.time()
        for i, v in enumerate(("insufficient", "weak", "sufficient")):
            self.p.store.conn.execute(
                "INSERT INTO query_log(ts, query, scores, feedback, origin) VALUES(?,?,?,?,?)",
                (now - i, "거르기 시험 %s" % v, _j.dumps({"verdict": v}), -1 if v == "weak" else None, "cli"))
        self.p.store.conn.commit()
        from llmwiki.evalset import from_query_log
        ins = from_query_log(self.p.store, days=365, limit=50, only="insufficient")["questions"]
        self.assertTrue(ins, "근거 못 찾은 질의가 있는데 0건이 나왔다 — 필터가 죽었다")
        self.assertTrue(all(r["verdict"] == "insufficient" for r in ins))
        wk = from_query_log(self.p.store, days=365, limit=50, only="weak")["questions"]
        self.assertTrue(all(r["verdict"] in ("weak", "insufficient") for r in wk))
        neg = from_query_log(self.p.store, days=365, limit=50, only="negative")["questions"]
        self.assertTrue(all((r.get("feedback") or 0) < 0 for r in neg))

    def test_filter_scans_wider_than_the_default_window(self):
        """문제 질의는 드물다 — 좁게 훑으면 '그런 질의가 없다' 고 **잘못** 답한다."""
        import inspect
        from llmwiki import evalset as ES
        src = inspect.getsource(ES.from_query_log)
        self.assertIn("2000 if only else 200", src,
                      "거르기를 걸었을 때 스캔 폭을 넓히지 않는다")

    def test_pick_uses_exactly_those_questions(self):
        from llmwiki.evalset import pick_questions
        ids = self._ids(2)
        got = pick_questions(self.p.store, ids)
        self.assertEqual([q["from_query_id"] for q in got["questions"]], ids)
        self.assertEqual(got["source"]["kind"], "list")
        self.assertEqual(got["source"]["n"], len(ids))

    def test_pick_reports_missing_ids_instead_of_silently_dropping(self):
        from llmwiki.evalset import pick_questions
        got = pick_questions(self.p.store, self._ids(1) + [99999999])
        self.assertIn(99999999, got["source"]["missing"], "없는 번호를 조용히 버리면 왜 문항이 준 지 알 수 없다")

    def test_pick_empty_is_not_a_silent_full_run(self):
        """고른 것이 없다고 **전부 돌리면** 안 된다 — 사람이 고를 생각이었기 때문이다."""
        from llmwiki.evalset import pick_questions
        got = pick_questions(self.p.store, [])
        self.assertEqual(got["questions"], [])
        self.assertEqual(got["source"]["kind"], "list")

    def test_trial_on_picked_questions_is_ungraded(self):
        from llmwiki.evalset import pick_questions
        got = pick_questions(self.p.store, self._ids(2))
        r = TR.run_trial(self.p, "picked", questions=got["questions"], k=3, source=got["source"])
        self.assertEqual(r["summary"]["_source"]["kind"], "list")
        for k in TR.NEEDS_GROUND_TRUTH:
            self.assertIsNone(r["summary"][k], "고른 질의에도 정답은 없다 — %s 는 공백이어야 한다" % k)


# ---------------------------------------------------------------- 포렌식
class ForensicFilterTest(_Env):
    """포렌식 목록은 **볼 이유가 있는 것부터**.

    실제 저장소에서 기록 1,122건 중 1,025건이 `sufficient` 였다. 최근 N건을 그냥 나열하면
    "왜 답이 부실했나" 를 보러 온 사람이 찾는 줄을 하나도 못 본다.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import json as _j
        now = time.time()
        for i, v in enumerate(["sufficient"] * 12 + ["weak", "insufficient", "expectation"]):
            cls.p.store.conn.execute(
                "INSERT INTO forensics(ts, request_id, run_id, query, verdict, groundedness, findings, suggestions, topics, origin) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (now - i, 1000 + i, "r%d" % i, "테스트 질의 %d" % i, v, 0.5,
                 _j.dumps([{"stage": "fts_search", "problem": "x", "severity": "warn"}]), "[]", "[]", "auto"))
        cls.p.store.conn.commit()

    def test_default_hides_the_successes(self):
        rows = FX.list_forensics(self.p.store, 50, only_problems=True)
        self.assertTrue(rows, "문제 건이 하나도 안 나온다")
        self.assertFalse([r for r in rows if r["verdict"] == "sufficient"],
                         "기본 보기에 정상 건이 섞였다 — 화면이 다시 덮인다")

    def test_all_includes_everything(self):
        allrows = FX.list_forensics(self.p.store, 100)
        self.assertTrue([r for r in allrows if r["verdict"] == "sufficient"])

    def test_verdict_filter(self):
        rows = FX.list_forensics(self.p.store, 50, verdict="weak")
        self.assertTrue(rows)
        self.assertEqual({r["verdict"] for r in rows}, {"weak"})

    def test_verdict_filter_accepts_list_and_csv(self):
        a = FX.list_forensics(self.p.store, 50, verdict=["weak", "insufficient"])
        b = FX.list_forensics(self.p.store, 50, verdict="weak,insufficient")
        self.assertEqual({r["id"] for r in a}, {r["id"] for r in b})
        self.assertTrue({r["verdict"] for r in a} <= {"weak", "insufficient"})

    def test_query_text_filter(self):
        rows = FX.list_forensics(self.p.store, 50, q="테스트 질의 3")
        self.assertTrue(rows)
        for r in rows:
            self.assertIn("테스트 질의 3", r["query"])

    def test_counts_are_over_everything_not_just_the_page(self):
        """예전에는 최근 N건만 세어 'weak 2건' 처럼 축소돼 보였다 — 문제가 적은 줄 알고 넘어가게 된다."""
        cnt = FX.verdict_counts(self.p.store)
        self.assertGreaterEqual(cnt.get("sufficient", 0), 12)
        page = FX.list_forensics(self.p.store, 3)
        self.assertLessEqual(len(page), 3)
        self.assertGreater(sum(cnt.values()), len(page))

    def test_summary_reports_problem_rate(self):
        s = FX.summary(self.p.store)
        self.assertIn("n_problems", s)
        self.assertIn("problem_rate", s)
        self.assertEqual(s["n"], sum(FX.verdict_counts(self.p.store).values()))
        self.assertTrue(0.0 <= s["problem_rate"] <= 1.0)


# ---------------------------------------------------------------- 추세
class TrendTest(_Env):
    """일/주/월 추세 — "지금 얼마인가" 가 아니라 **"나아지나 나빠지나"**."""

    def test_buckets_are_registered(self):
        self.assertEqual(set(O.BUCKETS), {"day", "week", "month"})
        self.assertIn("trend", O.SECTIONS)
        self.assertIn("trend", O.SECTION_HELP)

    def test_bucket_key_shapes(self):
        ts = time.mktime((2026, 9, 20, 13, 0, 0, 0, 0, -1))
        self.assertEqual(O._bucket_key(ts, "day"), "2026-09-20")
        self.assertEqual(O._bucket_key(ts, "month"), "2026-09")
        self.assertRegex(O._bucket_key(ts, "week"), r"^\d{4}-W\d{2}$")

    def test_collect_returns_points(self):
        d = O.collect(self.p, days=30, sections=["trend"], bucket="day")
        tr = d["trend"]
        self.assertEqual(tr["bucket"], "day")
        self.assertTrue(tr["points"], "질의를 넣었는데 구간이 하나도 없다")
        p = tr["points"][-1]
        for k in ("bucket", "queries", "p50_ms", "p95_ms", "tokens_per_query", "builds", "insufficient_rate"):
            self.assertIn(k, p, k)

    def test_longer_window_for_coarser_bucket(self):
        """'월간' 을 7일치로 그리면 막대가 하나뿐이라 아무 말도 하지 못한다."""
        day = O.collect(self.p, days=7, sections=["trend"], bucket="day")["trend"]
        mon = O.collect(self.p, days=7, sections=["trend"], bucket="month")["trend"]
        self.assertGreater(mon["days"], day["days"])
        self.assertEqual(mon["days"], O.BUCKET_DEFAULT_DAYS["month"])

    def test_explicit_trend_days_wins(self):
        tr = O.collect(self.p, days=7, sections=["trend"], bucket="month", trend_days=30)["trend"]
        self.assertEqual(tr["days"], 30)

    def test_text_render_has_sparkline(self):
        txt = O.format_text(O.collect(self.p, days=30, sections=["trend"]))
        self.assertIn("[trend]", txt)
        self.assertTrue(any(ch in txt for ch in O._SPARK), "터미널에서도 추세가 보여야 한다")

    def test_spark_handles_empty_and_flat(self):
        self.assertIn("값 없음", O._spark([]))
        self.assertIn("(3 ~ 3)", O._spark([3, 3, 3]))

    def test_trend_is_read_only(self):
        before = self.p.store.conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
        O.collect(self.p, days=30, sections=["trend"], bucket="week")
        self.assertEqual(before, self.p.store.conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
