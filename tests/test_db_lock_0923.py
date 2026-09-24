# -*- coding: utf-8 -*-
"""질의 경로가 SQLite 쓰기 잠금을 오래 쥐지 않는가 (2026-09-23 회귀 방지).

무엇을 막는 테스트인가
  `Store.cache_put()` 은 예전에 `commit()` 을 하지 않았다. Python sqlite3 는 INSERT 앞에서 트랜잭션을
  암묵적으로 열고 SQLite 는 첫 쓰기에서 WRITER 잠금을 잡아 COMMIT 까지 놓지 않는다. 그런데 질의 경로에서
  이 INSERT 는 **벡터 검색 초반**에 일어나고 다음 커밋은 **질의 맨 끝**이었다 — 즉 질의 1건이 자기 수명
  (실측 98~118초) 내내 쓰기 잠금을 혼자 쥐었고, 동시에 들어온 다른 질의는 db_busy_timeout_s(60초)만큼
  기다린 뒤 'database is locked' 로 실패했다. 그 실패는 `except Exception: pass` 가 조용히 삼켰다.

  같이 지키는 것:
    - 임베딩 캐시 적중 (같은 질의를 한 요청 안에서 두 번 임베딩하지 않는다)
    - 모델 이름 정규화 (`bge-m3` ≡ `bge-m3:latest` — 표기가 바뀌어도 전체 재임베딩이 일어나지 않는다)
    - 커밋 없이 세션을 벗어나면 **경고 카운터가 는다** (같은 사고를 다시 조용히 넘기지 않게)

설명: docs/REQUEST_LEDGER.md §1.1 · docs/history/2026-09-23/IMPLEMENTATION_PLAN_0923.md §1.2
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import unittest

import numpy as np

from llmwiki.retrieval import embed_query
from llmwiki.store import Store


class _Emb:
    """원격 임베더 흉내 — 호출 횟수를 센다 (캐시가 실제로 먹는지 보려고)."""

    name = "ollama"
    dim = 8

    def __init__(self, model: str = "bge-m3:latest"):
        self.model = model
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [np.ones(self.dim, dtype=np.float32) * (len(t) % 7 + 1) for t in texts]


class DbLockTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="llmwiki-lock-")
        self.path = os.path.join(self.dir, "t.sqlite3")
        self.store = Store(self.path, busy_timeout_s=3.0)

    def tearDown(self):
        try:
            self.store.close()
        finally:
            shutil.rmtree(self.dir, ignore_errors=True)

    # ---------------------------------------------------------------- 핵심
    def test_query_embedding_does_not_hold_write_lock(self):
        """embed_query 뒤에 트랜잭션이 열려 있으면 안 된다 — 열려 있으면 잠금을 쥔 것이다."""
        with self.store.session():
            embed_query(self.store, _Emb(), "잠금을 쥐면 안 되는 질의")
            self.assertFalse(self.store.conn.in_transaction,
                             "cache_put 이 커밋하지 않았다 — 질의가 끝날 때까지 쓰기 잠금을 쥔다")

    def test_other_writer_is_not_blocked_during_query(self):
        """질의가 진행 중인 동안 **다른 연결의 쓰기**가 막히지 않아야 한다.

        예전 코드에서는 busy_timeout 을 전부 소진한 뒤 'database is locked' 로 실패했다.
        """
        result = {}

        def other_writer():
            other = Store(self.path, busy_timeout_s=3.0)
            t0 = time.perf_counter()
            try:
                with other.session():
                    other.conn.execute("INSERT OR REPLACE INTO kv(k,v) VALUES('probe','1')")
                    other.conn.commit()
                result["ok"] = True
            except Exception as e:      # noqa: BLE001 — 실패 사유를 그대로 보고한다
                result["err"] = str(e)
            finally:
                result["ms"] = (time.perf_counter() - t0) * 1000
                other.close()

        with self.store.session():
            embed_query(self.store, _Emb(), "동시성 확인용 질의")
            th = threading.Thread(target=other_writer)
            th.start()
            th.join(10)

        self.assertTrue(result.get("ok"), "다른 연결의 쓰기가 막혔다: %s" % result.get("err"))
        self.assertLess(result["ms"], 1000, "다른 쓰기가 %.0fms 기다렸다 — 잠금을 쥐고 있다" % result["ms"])

    # ---------------------------------------------------------------- 캐시
    def test_same_query_is_embedded_once(self):
        """같은 질의 문자열은 두 번째부터 캐시 적중 — 한 요청 안의 중복 임베딩(실측 5초)을 없앤다."""
        emb = _Emb()
        with self.store.session():
            _, first = embed_query(self.store, emb, "같은 질문")
            calls = emb.calls
            _, second = embed_query(self.store, emb, "같은 질문")
        self.assertFalse(first)
        self.assertTrue(second, "두 번째 호출이 캐시에 적중하지 않았다")
        self.assertEqual(emb.calls, calls, "캐시 적중인데 임베더를 다시 불렀다")

    def test_model_name_tag_does_not_miss_cache(self):
        """`bge-m3:latest` 로 저장된 것을 `bge-m3` 로 찾아도 적중해야 한다.

        예전에는 이름 표기가 바뀌자 캐시가 통째로 빗나가 **전체를 다시 임베딩**했다(실측 65MB 중복).
        """
        with self.store.session():
            embed_query(self.store, _Emb("bge-m3:latest"), "표기 무관 질의")
            models = [r[0] for r in self.store.conn.execute("SELECT DISTINCT model FROM embedding_cache")]
            self.assertEqual(models, ["bge-m3"], "정규화된 이름으로 저장되지 않았다")

            plain = _Emb("bge-m3")
            calls = plain.calls
            _, cached = embed_query(self.store, plain, "표기 무관 질의")
        self.assertTrue(cached, "이름 표기가 바뀌자 캐시가 빗나갔다 — 전체 재임베딩이 일어난다")
        self.assertEqual(plain.calls, calls)

    def test_cache_merge_dedupes_and_keeps_unique(self):
        """병합은 겹치는 sha 만 버리고, 옛 이름에만 있는 sha 는 이름을 바꿔 **보존**한다."""
        now = time.time()
        vec = np.ones(8, dtype=np.float32).tobytes()
        with self.store.session():
            for model, sha in (("bge-m3:latest", "dup"), ("bge-m3", "dup"), ("bge-m3:latest", "only-old")):
                self.store.conn.execute(
                    "INSERT OR REPLACE INTO embedding_cache(provider,model,sha,dim,vec,ts) VALUES(?,?,?,?,?,?)",
                    ("ollama", model, sha, 8, vec, now))
            self.store.conn.commit()

            preview = self.store.cache_merge_models(dry_run=True)
            self.assertEqual(preview["moved"], 0, "dry-run 이 실제로 바꿨다")

            merged = self.store.cache_merge_models()
            self.assertEqual(merged["dropped"], 1)
            self.assertEqual(merged["moved"], 1)
            models = [r[0] for r in self.store.conn.execute("SELECT DISTINCT model FROM embedding_cache")]
            kept = self.store.conn.execute("SELECT COUNT(*) FROM embedding_cache WHERE sha='only-old'").fetchone()[0]
        self.assertEqual(models, ["bge-m3"])
        self.assertEqual(kept, 1, "옛 이름에만 있던 항목이 사라졌다 — 그만큼 다시 임베딩하게 된다")

    # ---------------------------------------------------------------- 가드
    def test_uncommitted_write_is_counted(self):
        """커밋 없이 세션을 벗어나면 조용히 넘기지 않고 센다 (같은 사고의 재발을 드러낸다)."""
        before = self.store.uncommitted_exits
        with self.store.session():
            self.store.conn.execute("INSERT OR REPLACE INTO kv(k,v) VALUES('leak','1')")
        self.assertEqual(self.store.uncommitted_exits, before + 1)
        self.assertEqual(self.store.pool_info()["uncommitted_exits"], before + 1)

    def test_synchronous_pragma_applied(self):
        """db_synchronous 가 실제 연결에 적용되는가 (기본 NORMAL = 1)."""
        self.assertEqual(self.store.pool_info()["synchronous"], "NORMAL")
        self.assertEqual(int(self.store.conn.execute("PRAGMA synchronous").fetchone()[0]), 1)

        full = Store(os.path.join(self.dir, "full.sqlite3"), synchronous="FULL")
        try:
            self.assertEqual(int(full.conn.execute("PRAGMA synchronous").fetchone()[0]), 2)
        finally:
            full.close()

        bogus = Store(os.path.join(self.dir, "bogus.sqlite3"), synchronous="쓰레기값")
        try:
            self.assertEqual(bogus.synchronous, "NORMAL", "모르는 값은 NORMAL 로 떨어져야 한다")
        finally:
            bogus.close()


if __name__ == "__main__":
    unittest.main()
