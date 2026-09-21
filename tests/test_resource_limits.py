# -*- coding: utf-8 -*-
"""자원 한계와 누수 — "부하가 끝난 뒤 원래 자리로 돌아오는가".

## 무엇을 보는가

기능 테스트는 "한 번 돌렸을 때 맞는 답이 나오는가" 를 본다. 오래 켜 두는 서버에서 무너지는 것은 그쪽이 아니라
**되돌아오지 않는 것들**이다. 연결·스레드·락·진행 표시가 요청이 끝난 뒤에도 조금씩 남으면, 하루쯤 뒤에
"갑자기 느려졌다 / 파일 핸들이 모자란다" 로 나타나고 그때는 원인을 되짚기 어렵다.

여기서 고정하는 것은 넷이다.

  1. **SQLite 연결** — `session()` 이 끝나면 빌린 연결(`live`)이 0 으로 돌아오고, 최고치(`peak_live`)가
     동시 실행 수를 넘지 않는다. 예전에는 빌려 나간 연결 수를 아무도 세지 않아 누수가 보이지 않았다.
  2. **부드러운 상한** — `db_max_live_connections` 를 넘겨도 **요청이 죽지 않고**, 넘긴 사실이 `overflow` 로 남는다.
     (상한을 딱딱하게 걸면 채널 검색처럼 하위 스레드가 연결을 더 쓰는 자리에서 교착이 난다.)
  3. **스레드** — 질의를 많이 돌려도 스레드 수가 기준선으로 돌아온다.
  4. **진행 표시(progress)** — 끝난 요청의 토큰이 계속 쌓이지 않는다.

참고: `llmwiki/store.py: Store.session/pool_info` · `llmwiki/reqmgr.py` · `docs/CONCURRENCY.md`
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki.config import Settings, Toggles     # noqa: E402
from llmwiki.pipeline import Pipeline            # noqa: E402
from llmwiki.store import Store                  # noqa: E402

DOC = """# ISSUE-2001 수신 DMA 오버런

## 원인
링 버퍼 크기가 작아 버스트에서 오버런. 담당은 모뎀SW팀.

## 조치
CL-55321 에서 버퍼를 2배로 늘렸다.
"""


class PoolAccountingTest(unittest.TestCase):
    """연결 풀 회계 — 누수는 '세지 않으면 보이지 않는다'."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_live_returns_to_zero(self):
        st = Store(self.db, pool_size=4, max_live=0)
        self.addCleanup(st.close)
        self.assertEqual(st.pool_info()["live"], 0)
        with st.session():
            self.assertEqual(st.pool_info()["live"], 1)
        self.assertEqual(st.pool_info()["live"], 0)
        self.assertEqual(st.pool_info()["peak_live"], 1)

    def test_nested_session_does_not_double_count(self):
        """중첩 세션은 바깥 연결을 그대로 쓴다 — 두 번 세면 누수처럼 보인다."""
        st = Store(self.db, pool_size=4, max_live=0)
        self.addCleanup(st.close)
        with st.session():
            with st.session():
                self.assertEqual(st.pool_info()["live"], 1)
        self.assertEqual(st.pool_info()["live"], 0)

    def test_parallel_sessions_peak_and_return(self):
        st = Store(self.db, pool_size=4, max_live=0)
        self.addCleanup(st.close)
        n, gate = 8, threading.Barrier(8)

        def run():
            with st.session():
                gate.wait(10)                       # 8개가 동시에 빌린 상태를 만든다
                st.conn.execute("SELECT 1").fetchone()
        ths = [threading.Thread(target=run) for _ in range(n)]
        [t.start() for t in ths]
        [t.join(20) for t in ths]
        info = st.pool_info()
        self.assertEqual(info["live"], 0, "세션이 끝났는데 빌린 연결이 남았다")
        self.assertEqual(info["peak_live"], n)
        self.assertLessEqual(info["idle"], info["max"], "놀고 있는 연결이 풀 크기를 넘었다")

    def test_pool_reuses_connections(self):
        """순차 요청은 새 연결을 계속 만들지 않는다 (created 가 요청 수만큼 늘면 풀이 죽은 것)."""
        st = Store(self.db, pool_size=4, max_live=0)
        self.addCleanup(st.close)
        for _ in range(20):
            with st.session():
                st.conn.execute("SELECT 1").fetchone()
        self.assertLessEqual(st.pool_info()["created"], 2)

    def test_soft_cap_never_fails_a_request(self):
        """상한을 넘겨도 요청은 성공한다 — 상한은 경보이지 차단이 아니다."""
        st = Store(self.db, pool_size=2, max_live=2, wait_timeout_s=0.1)
        self.addCleanup(st.close)
        n, gate, errors, done = 6, threading.Barrier(6), [], []

        def run():
            try:
                with st.session():
                    gate.wait(10)
                    st.conn.execute("SELECT 1").fetchone()
                    done.append(1)
            except Exception as e:      # noqa: BLE001 — 어떤 예외든 실패로 본다
                errors.append(repr(e))
        ths = [threading.Thread(target=run) for _ in range(n)]
        [t.start() for t in ths]
        [t.join(30) for t in ths]
        self.assertEqual(errors, [], "부드러운 상한이 요청을 죽였다")
        self.assertEqual(len(done), n)
        info = st.pool_info()
        self.assertEqual(info["live"], 0)
        self.assertGreater(info["overflow"], 0, "상한을 넘긴 사실이 기록되지 않았다 — 누수를 발견할 수 없다")

    def test_soft_cap_waits_before_overflowing(self):
        """여유가 곧 생기면 새로 만들지 않고 기다렸다 재사용한다 (버스트 평탄화)."""
        st = Store(self.db, pool_size=4, max_live=1, wait_timeout_s=5.0)
        self.addCleanup(st.close)
        order = []

        def hold():
            with st.session():
                order.append("a-in")
                time.sleep(0.3)
            order.append("a-out")
        t = threading.Thread(target=hold)
        t.start()
        time.sleep(0.05)
        with st.session():                 # 상한 1 이라 a 가 반납할 때까지 기다린다
            order.append("b-in")
        t.join(10)
        self.assertEqual(order, ["a-in", "a-out", "b-in"])
        self.assertEqual(st.pool_info()["overflow"], 0)

    def test_close_wakes_waiters(self):
        """닫는 중에 연결을 기다리던 스레드가 매달려 있으면 종료가 안 된다."""
        st = Store(self.db, pool_size=1, max_live=1, wait_timeout_s=30.0)
        held = threading.Event()
        release = threading.Event()
        state = {}

        def hold():
            with st.session():
                held.set()
                release.wait(10)

        def waiter():
            t0 = time.time()
            with st.session():
                state["waited"] = time.time() - t0
        h = threading.Thread(target=hold)
        h.start()
        self.assertTrue(held.wait(10))
        w = threading.Thread(target=waiter)
        w.start()
        time.sleep(0.1)
        st.close()                      # 대기 중인 스레드를 깨워야 한다
        release.set()
        h.join(15)
        w.join(15)
        self.assertFalse(w.is_alive(), "close() 뒤에도 연결 대기 스레드가 살아 있다")


class QuerySoakTest(unittest.TestCase):
    """질의를 반복해 돌린 뒤 자원이 제자리로 돌아오는가 (짧은 soak)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        for i in range(6):
            with open(os.path.join(corpus, "d%d.md" % i), "w", encoding="utf-8") as f:
                f.write(DOC.replace("2001", "200%d" % i))
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

    def test_connections_and_threads_return_to_baseline(self):
        base_threads = threading.active_count()
        errors = []

        def run(i):
            try:
                with self.p.request_scope():
                    self.p.query("ISSUE-200%d 원인은?" % (i % 6), log=False)
            except Exception as e:      # noqa: BLE001
                errors.append(repr(e))
        for _round in range(3):
            ths = [threading.Thread(target=run, args=(i,)) for i in range(8)]
            [t.start() for t in ths]
            [t.join(120) for t in ths]
        self.assertEqual(errors, [])
        info = self.p.store.pool_info()
        self.assertEqual(info["live"], 0, "질의가 모두 끝났는데 빌린 연결이 남았다: %s" % info)
        self.assertLessEqual(info["idle"], info["max"])
        # 스레드는 즉시 사라지지 않을 수 있으므로 잠깐 기다린다
        for _ in range(50):
            if threading.active_count() <= base_threads + 2:
                break
            time.sleep(0.1)
        self.assertLessEqual(threading.active_count(), base_threads + 2,
                             "질의 뒤 스레드가 남았다 (%d → %d)" % (base_threads, threading.active_count()))

    def test_progress_entries_do_not_pile_up(self):
        """끝난 요청은 '실행 중' 목록에서 빠지고, 최근 목록은 상한 안에서만 늘어난다."""
        from llmwiki import progress as pg
        for i in range(12):
            with self.p.request_scope():
                self.p.query("ISSUE-200%d 조치" % (i % 6), log=False)
        self.assertEqual(pg.all_running(), [], "끝난 요청이 '실행 중' 으로 남아 있다")
        self.assertLessEqual(len(pg.all_recent(1000)), 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
