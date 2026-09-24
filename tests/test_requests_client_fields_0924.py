# -*- coding: utf-8 -*-
"""새 요청 행에 role·via·ip·agent 가 기록되는가 (2026-09-24, CODE_REVIEW_0924 §2.12).

무엇이 문제였나
  2026-09-23 에 질의 로그의 원천을 `query_log` → `requests` 로 합쳤다. 그런데 `requests` INSERT 는 origin·user 만 썼고
  role·via·ip·agent 는 **옛 행을 옮기는 마이그레이션에서만** 채워졌다. 그래서 admin 이 보는 질의 이력(`/api/queries`)의
  IP·에이전트 열이 새 행부터 비었다 — `verify_web` 의 "admin GET queries (IP·에이전트 보임)" 가 잡았다.

무엇을 확인하나
  1. 진행 레지스트리에 client 가 묶여 있으면 `log_request` 가 네 값을 함께 쓴다.
  2. `store.queries()` 가 그 값을 돌려준다 (admin 화면이 읽는 경로).
  3. client 가 없으면(CLI·테스트) 빈 문자열로 남고 실패하지 않는다.
"""
import os
import shutil
import tempfile
import unittest

from llmwiki import progress as pg
from llmwiki.store import Store


class RequestClientFieldsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lwreqcl_")
        self.store = Store(os.path.join(self.tmp, "t.sqlite3"))

    def tearDown(self):
        try:
            pg.unbind("done")
        except Exception:
            pass
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_client_fields_are_written_and_listed(self):
        pg.bind("t-1", "query", "q", client={"user": "kim", "role": "admin", "via": "cookie", "ip": "10.1.2.3",
                                               "origin": "web", "agent": "Mozilla/5.0 test"})
        try:
            rid = self.store.log_request("query", "PPP 협상?", {"ms": 12, "summary": {}}, result={"answer": "x"})
        finally:
            pg.unbind("done")
        self.assertGreater(rid, 0)
        row = self.store.conn.execute("SELECT user, role, via, ip, agent, origin FROM requests WHERE id=?", (rid,)).fetchone()
        self.assertEqual(tuple(row)[:5], ("kim", "admin", "cookie", "10.1.2.3", "Mozilla/5.0 test"))
        rows = self.store.queries(10)
        mine = [r for r in rows if r.get("id") == rid]
        self.assertTrue(mine, rows)
        self.assertEqual(mine[0].get("ip"), "10.1.2.3")
        self.assertEqual(mine[0].get("agent"), "Mozilla/5.0 test")

    def test_without_client_it_still_works(self):
        rid = self.store.log_request("query", "no client", {"ms": 1, "summary": {}}, result={}, origin="cli", user="me")
        self.assertGreater(rid, 0)
        row = self.store.conn.execute("SELECT user, role, via, ip, agent FROM requests WHERE id=?", (rid,)).fetchone()
        self.assertEqual(tuple(row), ("me", "", "", "", ""))


if __name__ == "__main__":
    unittest.main()
