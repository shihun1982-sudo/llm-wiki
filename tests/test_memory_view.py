# -*- coding: utf-8 -*-
"""메모리 화면이 보여 주는 것 — "시스템이 스스로 배운 것" 을 **목록으로** 설명할 수 있는가.

## 왜 이 테스트가 있는가

메모리 화면에는 오래도록 통계 숫자 한 줄과 에피소드 표 하나뿐이었다. 그래서 사용자가 물었다 —
"여기 나열된 게 뭐야? 누르면 연결되어야 하는 거 아니야? 어떤 동작을 하는 페이지인지 모르겠어."

맞는 지적이었다. 사람이 이 화면에 와서 묻는 것은 숫자가 아니라 **목록**이다.

  · "내가 누른 👎 가 검색에 반영됐나?"   → 어느 청크가 얼마나 눌렸는지
  · "왜 이 문서가 자꾸 위로 오지?"       → 그 문서가 부스트를 받고 있는지
  · "올렸던 제안이 왜 사라졌지?"         → 강도가 얼마나 남았는지 (사라지기 **전에**)

그래서 `boost_table`·`decaying_proposals`·`episodes(only=, q=)` 를 만들었다. 이 테스트가 고정하는 것:

  1. 화면이 보여 주는 부스트가 **검색이 실제로 쓰는 값과 같다** (계산이 두 벌이면 화면이 거짓말을 한다)
  2. 피드백의 방향(👍/👎)과 순위 감쇠가 가중치에 그대로 반영된다
  3. 제안이 사라지기 전에 **남은 날**을 알려 준다
  4. 에피소드를 걸러 찾을 수 있다 (쌓이면 목록만으로는 못 찾는다)

참고: `llmwiki/memory.py` · `docs/EVOLVE.md` · Web Evolve › 메모리
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

from llmwiki import memory as mem                # noqa: E402
from llmwiki.config import Settings, Toggles     # noqa: E402
from llmwiki.pipeline import Pipeline            # noqa: E402

DOC = """# ISSUE-2001 수신 DMA 오버런

## 원인
링 버퍼가 작아 버스트에서 오버런. 담당은 모뎀SW팀.

## 조치
CL-55321 에서 버퍼를 늘렸다.
"""


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        for i in range(3):
            with open(os.path.join(corpus, "d%d.md" % i), "w", encoding="utf-8") as f:
                f.write(DOC.replace("2001", "200%d" % i))
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"),
                     wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)
        cls.chunks = [c["chunk_id"] for c in cls.p.store.all_chunks(
            next(d["doc_id"] for d in cls.p.store.list_docs()))][:4]

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.p.store.conn.execute("DELETE FROM episodes")
        self.p.store.conn.commit()

    def ep(self, query, chunks, feedback=None, kind="query", outcome="sufficient", age_days=0.0):
        eid = mem.record_episode(self.p.store, None, query, kind, outcome, chunks, [])
        if feedback is not None or age_days:
            ts = time.time() - age_days * 86400.0
            self.p.store.conn.execute("UPDATE episodes SET feedback=?, ts=?, last_reinforced=? WHERE id=?",
                                      (feedback, ts, ts, eid))
            self.p.store.conn.commit()
        return eid


# ---------------------------------------------------------------- 1. 부스트 표
class BoostTableTest(_Base):
    def test_matches_what_search_actually_uses(self):
        """화면이 보여 주는 값과 융합이 쓰는 값이 **같아야** 한다 — 계산이 두 벌이면 화면이 거짓말을 한다."""
        self.ep("링 버퍼", self.chunks[:3], feedback=1)
        self.ep("오버런", self.chunks[1:3], feedback=-1)
        used = mem.feedback_weights(self.p.store, 60.0)
        shown = {r["chunk_id"]: r["weight"] for r in mem.boost_table(self.p.store, 60.0)}
        for cid, w in used.items():
            self.assertIn(cid, shown, "검색은 쓰는데 화면에 없는 청크: %s" % cid)
            self.assertAlmostEqual(shown[cid], round(w, 4), places=4)

    def test_direction_follows_feedback(self):
        self.ep("좋은 답", [self.chunks[0]], feedback=1)
        self.ep("나쁜 답", [self.chunks[1]], feedback=-1)
        w = {r["chunk_id"]: r["weight"] for r in mem.boost_table(self.p.store, 60.0)}
        self.assertGreater(w[self.chunks[0]], 0, "👍 인데 위로 밀지 않는다")
        self.assertLess(w[self.chunks[1]], 0, "👎 인데 아래로 밀지 않는다")

    def test_rank_decay_within_one_episode(self):
        """한 에피소드 안에서도 상위 근거가 더 강해야 한다 (1순위 > 3순위)."""
        self.ep("순위", self.chunks[:3], feedback=1)
        w = {r["chunk_id"]: r["weight"] for r in mem.boost_table(self.p.store, 60.0)}
        self.assertGreater(w[self.chunks[0]], w[self.chunks[2]])

    def test_old_feedback_fades(self):
        self.ep("최근", [self.chunks[0]], feedback=1, age_days=0)
        self.ep("오래됨", [self.chunks[1]], feedback=1, age_days=120)   # 반감기 60일 → 1/4
        w = {r["chunk_id"]: r["weight"] for r in mem.boost_table(self.p.store, 60.0)}
        self.assertGreater(w[self.chunks[0]], w[self.chunks[1]] * 2,
                           "오래된 피드백이 최근 것과 비슷하게 남아 있다 (감쇠가 안 걸렸다)")

    def test_carries_source_so_the_screen_can_explain_why(self):
        """가중치만 보여 주면 "왜" 를 알 수 없다 — 그렇게 만든 질문이 함께 와야 한다."""
        self.ep("이 질문 때문이다", [self.chunks[0]], feedback=-1)
        row = mem.boost_table(self.p.store, 60.0)[0]
        self.assertTrue(row["episodes"])
        self.assertEqual(row["episodes"][0]["query"], "이 질문 때문이다")
        self.assertEqual(row["episodes"][0]["feedback"], -1)
        self.assertGreaterEqual(row["n_episodes"], 1)

    def test_resolves_document_for_linking(self):
        """행을 눌러 문서로 가려면 doc_id 가 있어야 한다. 색인에서 사라진 청크는 그렇다고 표시한다."""
        self.ep("있는 청크", [self.chunks[0]], feedback=1)
        self.ep("없는 청크", ["corpus/사라진문서.md#9"], feedback=1)
        rows = {r["chunk_id"]: r for r in mem.boost_table(self.p.store, 60.0)}
        self.assertTrue(rows[self.chunks[0]]["exists"])
        self.assertTrue(rows[self.chunks[0]]["doc_id"])
        gone = rows["corpus/사라진문서.md#9"]
        self.assertFalse(gone["exists"])
        self.assertEqual(gone["doc_id"], "corpus/사라진문서.md")   # 링크는 걸 수 있게 chunk_id 로 되짚는다

    def test_sorted_by_impact(self):
        self.ep("약함", [self.chunks[2]], feedback=1, age_days=90)
        self.ep("강함", [self.chunks[0]], feedback=-1)
        rows = mem.boost_table(self.p.store, 60.0)
        self.assertEqual(rows[0]["chunk_id"], self.chunks[0], "영향이 큰 것이 위에 와야 한다")

    def test_empty_is_empty_not_an_error(self):
        self.assertEqual(mem.boost_table(self.p.store, 60.0), [])


# ---------------------------------------------------------------- 2. 감쇠 중인 제안
class DecayingProposalsTest(_Base):
    def test_reports_days_left_before_it_disappears(self):
        """"제안이 왜 사라졌지" 가 아니라 "곧 사라진다" 를 먼저 보여 줘야 한다."""
        pid = self.p.store.add_proposal("synonym", {"term": "x"}, "t", 0.5, "test")
        self.p.store.conn.execute("UPDATE proposals SET strength=1.0, last_reinforced=? WHERE id=?",
                                  (time.time(), pid))
        self.p.store.conn.commit()
        rows = {r["id"]: r for r in mem.decaying_proposals(self.p.store, 60.0, 0.2)}
        r = rows[pid]
        self.assertAlmostEqual(r["strength_now"], 1.0, places=2)
        self.assertGreater(r["days_left"], 100)      # 1.0 → 0.2 까지 log2(5)*60 ≈ 139일
        self.assertFalse(r["at_risk"])

    def test_marks_at_risk(self):
        pid = self.p.store.add_proposal("synonym", {"term": "y"}, "t", 0.5, "test")
        self.p.store.conn.execute("UPDATE proposals SET strength=0.21, last_reinforced=? WHERE id=?",
                                  (time.time(), pid))
        self.p.store.conn.commit()
        r = {x["id"]: x for x in mem.decaying_proposals(self.p.store, 60.0, 0.2)}[pid]
        self.assertTrue(r["at_risk"], "임계 바로 위인데 경고하지 않는다")
        self.assertLessEqual(r["days_left"], 7)

    def test_payload_is_readable(self):
        pid = self.p.store.add_proposal("query_rule", {"type": "synonym", "term": "지연"}, "t", 0.5, "test")
        r = {x["id"]: x for x in mem.decaying_proposals(self.p.store, 60.0, 0.2)}[pid]
        self.assertIsInstance(r["payload"], dict)      # 화면이 JSON 문자열을 다시 파싱하지 않게
        self.assertEqual(r["payload"]["term"], "지연")

    def test_only_unapproved(self):
        pid = self.p.store.add_proposal("synonym", {"term": "z"}, "t", 0.5, "test")
        self.p.store.conn.execute("UPDATE proposals SET status='applied' WHERE id=?", (pid,))
        self.p.store.conn.commit()
        self.assertNotIn(pid, [r["id"] for r in mem.decaying_proposals(self.p.store, 60.0, 0.2)])


# ---------------------------------------------------------------- 3. 에피소드 찾기
class EpisodeFilterTest(_Base):
    def setUp(self):
        super().setUp()
        self.ep("링 버퍼 오버런 원인", self.chunks[:2], feedback=1)
        self.ep("전력 제어 이슈", self.chunks[1:3], feedback=-1)
        self.ep("피드백 없는 질의", self.chunks[:1])

    def test_only_feedback(self):
        self.assertEqual(len(mem.episodes(self.p.store, 50, "feedback")), 2)

    def test_only_negative_and_positive(self):
        neg = mem.episodes(self.p.store, 50, "negative")
        pos = mem.episodes(self.p.store, 50, "positive")
        self.assertEqual([e["query"] for e in neg], ["전력 제어 이슈"])
        self.assertEqual([e["query"] for e in pos], ["링 버퍼 오버런 원인"])

    def test_search_by_text(self):
        self.assertEqual([e["query"] for e in mem.episodes(self.p.store, 50, "", "오버런")], ["링 버퍼 오버런 원인"])
        self.assertEqual(mem.episodes(self.p.store, 50, "", "없는말"), [])

    def test_filters_combine(self):
        self.assertEqual(mem.episodes(self.p.store, 50, "negative", "오버런"), [])

    def test_exposes_ids_for_linking(self):
        """화면이 '그때의 요청·질의로 가기' 를 걸 수 있게 id 가 나와야 한다."""
        eid = mem.record_episode(self.p.store, 42, "링크용", "feedback", "negative", self.chunks[:1], [],
                                 {"query_id": 7})
        e = {x["id"]: x for x in mem.episodes(self.p.store, 50)}[eid]
        self.assertEqual(e["request_id"], 42)
        self.assertEqual(e["query_id"], 7)
        self.assertIsInstance(e["chunks"], list)      # 문자열이면 화면이 문자마다 링크를 건다


if __name__ == "__main__":
    unittest.main(verbosity=2)
