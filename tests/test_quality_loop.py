# -*- coding: utf-8 -*-
"""품질 루프 — 평가·비교·원인분석이 **실제로 쓸 만한가**.

## 왜 이 파일이 있는가

이 저장소의 실제 개발 색인에서 회귀 평가를 돌렸더니 `hit@k = 0.6` 이 나왔다. 그 숫자를 믿고 검색을
튜닝했다면 며칠을 버렸을 것이다 — 진짜 원인은 검색 품질이 아니라 **평가셋 파일 자신이 코퍼스에
색인돼 있었던 것**이었다(`corpus/imported/eval/NOTE-questions.md` 가 질문과 글자 그대로 일치해
상위 6건을 독식). 같은 색인에서 `term_recall` 은 25문항 전부 1.0 이라 **무엇을 바꿔도 안 움직이는
눈금**이었고, 답변 LLM 을 꺼도 문항당 9초가 걸려 아무도 자주 돌리지 않았다.

그래서 세 가지를 고정한다.

  1. **믿어도 되는 숫자인가** — 평가셋 오염·기대 문서 누락·표본 크기를 점수보다 먼저 말한다
  2. **차이가 우연과 구분되는가** — 25문항에서 한 문항 뒤집힌 것을 "더 좋다" 고 하지 않는다
  3. **비용을 함께 말하는가** — 품질이 올라도 토큰·지연이 얼마나 늘었는지 같이 보여 준다

참고: `llmwiki/evalset.py`(health·discriminating) · `llmwiki/trials.py`(_sign_test·compare) ·
`llmwiki/retrieval.py`(embed_query) · `docs/EVAL_TRIAL.md`
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import evalset as ev                 # noqa: E402
from llmwiki import trials as tr                  # noqa: E402
from llmwiki.config import Settings, Toggles      # noqa: E402
from llmwiki.pipeline import Pipeline             # noqa: E402

ANSWER_DOC = """# ISSUE-2001 수신 DMA 오버런

## 원인
링 버퍼가 4KB 로 작아 버스트에서 FIFO 오버런이 났다.

## 조치
CL-55301 에서 버퍼를 8KB 로 늘렸다.
"""

QUESTIONS = [{"q": "ISSUE-2001 의 원인과 수정 CL 은?", "expect_docs": ["ISSUE-2001"], "expect_terms": ["FIFO"]}]


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(cls.corpus)
        with open(os.path.join(cls.corpus, "issue.md"), "w", encoding="utf-8") as f:
            f.write("---\nid: ISSUE-2001\ndoc_type: issue\n---\n" + ANSWER_DOC)
        cls.qpath = os.path.join(cls.tmp, "questions.json")
        with open(cls.qpath, "w", encoding="utf-8") as f:
            json.dump(QUESTIONS, f, ensure_ascii=False)
        cls.p = cls._build(cls, cls.corpus)

    def _build(self, corpus):
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(self.tmp, "data"),
                     wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False, query_cache=False)
        p = Pipeline(s)
        p.build(full=True)
        return p

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)


# ---------------------------------------------------------------- 1. 이 숫자를 믿어도 되나
class EvalHealthTest(_Base):
    def test_clean_index_passes(self):
        h = ev.health(self.p, QUESTIONS)
        kinds = {i["kind"] for i in h["issues"]}
        self.assertNotIn("contaminated", kinds, "깨끗한 색인을 오염이라고 했다")
        self.assertNotIn("missing_docs", kinds)

    def test_detects_missing_expected_doc(self):
        """기대 문서가 색인에 없으면 그 문항은 **무엇을 해도 실패**한다 — 평가가 아니라 설정 오류다."""
        h = ev.health(self.p, [{"q": "없는 것", "expect_docs": ["ISSUE-9999"], "expect_terms": []}])
        self.assertEqual(h["level"], "bad")
        self.assertIn("missing_docs", {i["kind"] for i in h["issues"]})

    def test_detects_contamination(self):
        """평가셋이 코퍼스에 색인되면 질문 파일이 정답 문서를 밀어낸다 — 실제로 겪은 사고다."""
        tmp2 = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp2, True)
        corpus2 = os.path.join(tmp2, "corpus")
        os.makedirs(corpus2)
        with open(os.path.join(corpus2, "issue.md"), "w", encoding="utf-8") as f:
            f.write("---\nid: ISSUE-2001\ndoc_type: issue\n---\n" + ANSWER_DOC)
        # 평가셋을 그대로 코퍼스에 흘린다 (tools/corpus_ingest 가 프로젝트 폴더를 통째로 넣을 때 생긴다)
        with open(os.path.join(corpus2, "NOTE-questions.md"), "w", encoding="utf-8") as f:
            f.write("# 평가 질문 모음\n\n" + "\n".join("- " + q["q"] for q in QUESTIONS) * 6)
        s = Settings(corpus_dirs=[corpus2], data_dir=os.path.join(tmp2, "data"), wiki_dir=os.path.join(tmp2, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False)
        p2 = Pipeline(s)
        self.addCleanup(p2.store.close)
        p2.build(full=True)
        h = ev.health(p2, QUESTIONS)
        self.assertEqual(h["level"], "bad", "평가셋 유출을 잡지 못했다: %s" % h["issues"])
        c = next(i for i in h["issues"] if i["kind"] == "contaminated")
        self.assertTrue(any("questions" in q.lower() for q in c["questions"]),
                        "오염시킨 문서를 지목하지 못했다: %s" % c["questions"])

    def test_small_sample_is_warned(self):
        h = ev.health(self.p, QUESTIONS)
        small = next((i for i in h["issues"] if i["kind"] == "too_small"), None)
        self.assertIsNotNone(small, "문항 1개인데 표본 경고가 없다")
        self.assertIn("1.000", small["detail"])   # 한 문항 = 1.0


# ---------------------------------------------------------------- 2. 변별력
class DiscriminatingTest(unittest.TestCase):
    def test_flags_a_metric_that_never_moves(self):
        rows = [{"hit": True, "rr": 1.0, "term_recall": 1.0}, {"hit": False, "rr": 0.0, "term_recall": 1.0}]
        d = ev.discriminating(rows)
        self.assertTrue(d["hit"]["useful"])
        self.assertFalse(d["term_recall"]["useful"], "전 문항 같은 값인데 변별력이 있다고 했다")
        self.assertIn("같은 값", d["term_recall"]["note"])

    def test_ignores_missing_values(self):
        """검색 전용 모드에서 답변 지표는 None — 0 으로 세면 '나빠졌다' 로 잘못 읽힌다."""
        rows = [{"hit": True, "rr": 1.0, "answer_term_recall": None}] * 2
        self.assertNotIn("answer_term_recall", ev.discriminating(rows))

    def test_aggregate_keeps_none_as_none(self):
        agg = ev.aggregate([{"hit": True, "rr": 1.0, "term_recall": 0.5, "answer_term_recall": None}])
        self.assertIsNone(agg["answer_term_recall"], "뜻이 없는 값을 0 으로 평균냈다")
        self.assertEqual(agg["term_recall"], 0.5)


# ---------------------------------------------------------------- 3. 차이가 우연과 구분되는가
class SignificanceTest(unittest.TestCase):
    def test_one_question_flip_is_not_a_difference(self):
        """25문항에서 한 칸(0.04)이 움직인 것을 '더 좋다' 고 하면 노이즈를 쫓게 된다."""
        self.assertFalse(tr._sign_test(1, 0)["significant"])
        self.assertFalse(tr._sign_test(3, 2)["significant"])

    def test_consistent_wins_are_a_difference(self):
        self.assertTrue(tr._sign_test(8, 1)["significant"])
        self.assertTrue(tr._sign_test(12, 0)["significant"])

    def test_no_change_at_all(self):
        r = tr._sign_test(0, 0)
        self.assertFalse(r["significant"])
        self.assertEqual(r["p"], 1.0)

    def test_symmetric(self):
        self.assertEqual(tr._sign_test(8, 1)["p"], tr._sign_test(1, 8)["p"])


# ---------------------------------------------------------------- 4. 비교가 비용을 같이 말하는가
class CompareVerdictTest(_Base):
    def _fake_trial(self, name, rows, summary):
        cur = self.p.store.conn.execute(
            "INSERT INTO trials(name,ts,build_version,config,questions_hash,summary,rows,note,request_id) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (name, time.time(), self.p.store.build_version(), json.dumps({"toggles": {}}), "H",
             json.dumps(summary), json.dumps(rows), "", None))
        self.p.store.conn.commit()
        return int(cur.lastrowid)

    def test_says_no_difference_when_wins_are_noise(self):
        base_rows = [{"q": "q%d" % i, "hit": i < 12, "rank": 1 if i < 12 else None} for i in range(25)]
        # 한 문항만 뒤집힌 경우 — hit@k 는 +0.04 이지만 우연과 구분되지 않는다
        alt_rows = [dict(r) for r in base_rows]
        alt_rows[12] = {"q": "q12", "hit": True, "rank": 1}
        a = self._fake_trial("base", base_rows, {"hit@k": 0.48, "tokens_per_query": 100, "p95_ms": 1000})
        b = self._fake_trial("cand", alt_rows, {"hit@k": 0.52, "tokens_per_query": 100, "p95_ms": 1000})
        c = tr.compare(self.p.store, [a, b])
        rec = c["recommendation"][0]
        self.assertFalse(rec["significant"])
        self.assertIn("확인할 수 없습니다", rec["text"])
        self.assertEqual(c["n_questions"], 25)
        self.assertAlmostEqual(c["one_question"], 0.04, places=3)

    def test_reports_the_cost_of_a_win(self):
        """품질이 올라도 토큰·지연이 얼마나 늘었는지 **같이** 말해야 고를 수 있다."""
        base_rows = [{"q": "q%d" % i, "hit": False, "rank": None} for i in range(25)]
        alt_rows = [{"q": "q%d" % i, "hit": i < 10, "rank": 1 if i < 10 else None} for i in range(25)]
        a = self._fake_trial("base2", base_rows, {"hit@k": 0.0, "tokens_per_query": 100, "p95_ms": 1000})
        b = self._fake_trial("cand2", alt_rows, {"hit@k": 0.4, "tokens_per_query": 320, "p95_ms": 4200})
        rec = tr.compare(self.p.store, [a, b])["recommendation"][0]
        self.assertTrue(rec["significant"])
        self.assertIn("토큰", rec["cost"])
        self.assertIn("대가", rec["text"])

    def test_different_question_sets_are_not_compared(self):
        rows = [{"q": "q", "hit": True, "rank": 1}]
        a = self._fake_trial("x", rows, {"hit@k": 1.0})
        cur = self.p.store.conn.execute(
            "INSERT INTO trials(name,ts,build_version,config,questions_hash,summary,rows,note,request_id) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("y", time.time(), self.p.store.build_version(), json.dumps({"toggles": {}}), "DIFFERENT",
             json.dumps({"hit@k": 0.0}), json.dumps(rows), "", None))
        self.p.store.conn.commit()
        c = tr.compare(self.p.store, [a, int(cur.lastrowid)])
        self.assertFalse(c["same_questions"])
        self.assertIn("비교할 수 없습니다", c["recommendation"][0]["text"])


# ---------------------------------------------------------------- 5. 검색 전용 · 기대값 전달
class RetrievalOnlyTest(_Base):
    def test_carries_expectations_into_rows(self):
        """놓친 문항에서 바로 원인 분석으로 넘어가려면 기대값이 결과에 있어야 한다."""
        r, _ = self.p.evaluate(k=5, questions=QUESTIONS, log=False)
        row = r["rows"][0]
        self.assertEqual(row["expect_docs"], ["ISSUE-2001"])
        self.assertEqual(row["expect_terms"], ["FIFO"])
        self.assertIsNotNone(row.get("request_id"))

    def test_retrieval_only_skips_answer_metrics(self):
        r, _ = self.p.evaluate(k=5, questions=QUESTIONS, log=False, retrieval_only=True)
        self.assertTrue(r["summary"]["retrieval_only"])
        self.assertIsNone(r["rows"][0]["answer_term_recall"])
        self.assertIsNone(r["summary"]["answer_term_recall"])
        self.assertIsNotNone(r["summary"]["hit@k"], "검색 지표는 그대로 나와야 한다")

    def test_toggles_are_restored_afterwards(self):
        before = dict(self.p.s.toggles.__dict__)
        self.p.evaluate(k=5, questions=QUESTIONS, log=False, retrieval_only=True)
        self.assertEqual(self.p.s.toggles.__dict__, before, "검색 전용 모드가 토글을 되돌리지 않았다")

    def test_discriminating_is_reported(self):
        r, _ = self.p.evaluate(k=5, questions=QUESTIONS, log=False)
        self.assertIn("discriminating", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
