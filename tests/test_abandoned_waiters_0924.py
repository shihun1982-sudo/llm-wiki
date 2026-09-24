# -*- coding: utf-8 -*-
"""대기열에서 기다리는 요청의 클라이언트가 연결을 끊으면 자리를 비우는가 (2026-09-24, CODE_REVIEW_0924 §2.9).

무엇이 문제였나
  멍키 테스트(1,200건 폭격, 그중 166건은 클라이언트가 중간에 끊음) 직후 정상 질의가 5분 넘게
  503 `queue_full`(128/128) 을 받았다. 끊긴 클라이언트의 요청이 `queue_timeout_s`(질의 1800초)까지
  대기열을 차지했기 때문이다 — 서버는 소켓 EOF 로 알 수 있었지만 보지 않았다.

무엇을 확인하나
  1. `alive` 콜백이 False 를 돌려주면 대기 중인 티켓이 1~2초 안에 `progress.Cancelled` 로 빠지고,
     활동 목록에서 사라지며, `abandoned_queue` 카운터가 오른다.
  2. `concurrency.drop_disconnected_waiters=false` 면 예전처럼 그대로 기다린다 (되돌리기 경로).
  3. 콜백이 예외를 내면 '모른다' 로 보고 끊지 않는다 (잘못 끊는 쪽이 더 나쁘다).
  4. HTTP 핸들러의 `_client_alive` 가 실제 소켓의 EOF 를 알아본다 (socketpair 로 확인).
"""
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest

from llmwiki import progress as pg
from llmwiki import reqmgr as rq
from llmwiki.web import server as ws


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lwabandon_")
        self.path = os.path.join(self.tmp, "server.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"concurrency": {"max_parallel_reads": 1, "max_parallel_per_user": 30, "max_parallel_per_ip": 60,
                                       "queue_max": 10, "queue_timeout_s": 60},
                       "rate_limit": {"enabled": False},
                       "monitor": {"live_dir": os.path.join(self.tmp, "live")},
                       "ledger": {"dir": os.path.join(self.tmp, "ledger")}}, f)
        self.mgr = rq.RequestManager(path=self.path, data_dir=self.tmp)
        self._release = threading.Event()
        self._held = threading.Event()

    def tearDown(self):
        self._release.set()
        try:
            self.mgr.stop()
        except Exception:
            pass
        pg.set_publisher(None, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _hog_slot(self):
        """읽기 슬롯 1개를 다른 스레드가 잡아 둔다 → 다음 티켓은 대기열로 간다."""
        def run():
            with self.mgr.ticket("query", "read", client={"user": "hog", "origin": "web"}, label="hog"):
                self._held.set()
                self._release.wait(60)
        threading.Thread(target=run, daemon=True).start()
        self.assertTrue(self._held.wait(5), "슬롯을 잡지 못했다")


class AbandonedWaiterTest(_Base):
    def test_waiter_whose_client_left_is_dropped(self):
        self._hog_slot()
        state = {"alive": True}
        threading.Timer(0.5, lambda: state.update(alive=False)).start()   # 0.5초 뒤 '클라이언트가 끊었다'
        t0 = time.time()
        with self.assertRaises(pg.Cancelled) as cm:
            with self.mgr.ticket("query", "read", client={"user": "left", "origin": "web"}, label="left",
                                 alive=lambda: state["alive"]):
                self.fail("슬롯이 없으므로 시작되면 안 된다")
        took = time.time() - t0
        self.assertLess(took, 5.0, "끊긴 뒤 1~2초 안에 빠져야 한다 (%.1fs)" % took)
        self.assertIn("연결을 끊", str(cm.exception))
        self.assertEqual(self.mgr.counters.get("abandoned_queue", 0), 1)
        # 활동 목록에서 사라졌고, 이력에는 cancelled 로 남는다 (원장·화면이 이미 아는 상태 이름)
        self.assertFalse([t for t in self.mgr.activity()["queued"] if t["label"] == "left"])
        hist = [h for h in self.mgr.history if h["label"] == "left"]
        self.assertTrue(hist and hist[0]["status"] == "cancelled", hist)
        # 자리가 비었으므로 다음 사람은 hog 만 앞에 두고 기다린다 (대기열이 새지 않았다)
        self.assertEqual(sum(1 for t in self.mgr.active.values() if t["status"] == "queued"), 0)

    def test_alive_waiter_keeps_waiting(self):
        """살아 있는 클라이언트는 끊지 않는다 — 콜백이 True 인 동안 대기하다가 슬롯이 나면 실행된다."""
        self._hog_slot()
        threading.Timer(1.5, self._release.set).start()      # 1.5초 뒤 슬롯이 난다
        ran = {"v": False}
        with self.mgr.ticket("query", "read", client={"user": "ok", "origin": "web"}, label="ok", alive=lambda: True) as tk:
            ran["v"] = True
            self.assertGreaterEqual(tk["queue_wait_s"], 1.0)
        self.assertTrue(ran["v"])
        self.assertEqual(self.mgr.counters.get("abandoned_queue", 0), 0)

    def test_disabled_flag_restores_old_behaviour(self):
        """되돌리기: drop_disconnected_waiters=false 면 끊겨도 queue_timeout_s 까지 기다린다."""
        self.mgr.cfg["concurrency"]["drop_disconnected_waiters"] = False
        self._hog_slot()
        threading.Timer(2.5, self._release.set).start()
        with self.mgr.ticket("query", "read", client={"user": "old", "origin": "web"}, label="old", alive=lambda: False) as tk:
            self.assertGreaterEqual(tk["queue_wait_s"], 2.0)     # 끊겼다고 해도 빠지지 않고 슬롯을 기다렸다
        self.assertEqual(self.mgr.counters.get("abandoned_queue", 0), 0)

    def test_broken_callback_does_not_drop(self):
        """콜백이 예외를 내면 '모른다' 이므로 끊지 않는다."""
        self._hog_slot()
        threading.Timer(1.5, self._release.set).start()

        def boom():
            raise RuntimeError("peek 불가")
        with self.mgr.ticket("query", "read", client={"user": "x", "origin": "web"}, label="x", alive=boom) as tk:
            self.assertGreaterEqual(tk["queue_wait_s"], 1.0)
        self.assertEqual(self.mgr.counters.get("abandoned_queue", 0), 0)

    def test_setting_is_file_configurable(self):
        """설정 키가 기본값과 함께 파일·기본값 표에 있고, `server limits set` 경로로 바꿀 수 있다."""
        self.assertIs(rq.DEFAULTS["concurrency"]["drop_disconnected_waiters"], True)
        self.assertIs(self.mgr.cfg["concurrency"]["drop_disconnected_waiters"], True)
        self.mgr.set_limits({"concurrency.drop_disconnected_waiters": "false"}, save=False)
        self.assertIs(self.mgr.cfg["concurrency"]["drop_disconnected_waiters"], False)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ("server.json", os.path.join("setup", "server.example.json")):
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                self.assertIn("drop_disconnected_waiters", json.load(f)["concurrency"], rel)


class ClientAliveProbeTest(unittest.TestCase):
    """`Handler._client_alive` — 소켓 하나로 '붙어 있음 / 끊김 / 데이터 있음' 을 구분하는가."""

    class _Fake:
        def __init__(self, conn):
            self.connection = conn

    def test_open_idle_socket_is_alive(self):
        a, b = socket.socketpair()
        try:
            self.assertTrue(ws.Handler._client_alive(self._Fake(a)))
        finally:
            a.close(); b.close()

    def test_peer_close_is_detected(self):
        a, b = socket.socketpair()
        try:
            b.close()                                   # 클라이언트가 끊음 → EOF
            time.sleep(0.05)
            self.assertFalse(ws.Handler._client_alive(self._Fake(a)))
        finally:
            a.close()

    def test_pending_bytes_mean_alive(self):
        a, b = socket.socketpair()
        try:
            b.sendall(b"GET / HTTP/1.1\r\n")            # 파이프라이닝: 다음 요청이 와 있다 → 살아 있음
            time.sleep(0.05)
            self.assertTrue(ws.Handler._client_alive(self._Fake(a)))
        finally:
            a.close(); b.close()

    def test_missing_connection_is_treated_as_alive(self):
        self.assertTrue(ws.Handler._client_alive(self._Fake(None)))


if __name__ == "__main__":
    unittest.main()
