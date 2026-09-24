# -*- coding: utf-8 -*-
"""`/api/wiki/page` 의 페이지 이름 검증 (2026-09-24, CODE_REVIEW_0924 §2.11).

무엇이 문제였나
  POST 는 `/` 와 역슬래시만 막았다. 멍키 테스트가 이름 `?` 를 보내자 Windows 가 `OSError: [Errno 22] Invalid argument` 를 내고
  **500** 이 됐다(잘못된 입력은 400 이어야 한다). GET 은 이름을 전혀 검사하지 않아 `..` 로 위키 폴더 **밖의 .md** 를 읽을 수 있었다.

무엇을 확인하나
  1. 검증 함수가 안전한 이름만 통과시킨다 (한글·공백·하이픈·밑줄 OK / 경로 구분자·NUL·제어 문자·Windows 금지 문자·
     `.` 시작·끝 공백/점·예약 이름·120자 초과·문자열 아님 → 거부).
  2. 실제 서버에서 잘못된 이름은 GET·POST 모두 **400** 이고, `..` 로 폴더 밖 파일을 읽지 못한다.
  3. 정상 이름은 저장·조회가 그대로 된다.
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from llmwiki.web import server as ws


class WikiPageNameValidatorTest(unittest.TestCase):
    f = staticmethod(ws.Handler._wiki_page_name)

    def test_good_names_pass_unchanged(self):
        for n in ("ok-page", "한글 페이지", "notes_2026", "A.B", "Mixed Case 12"):
            self.assertEqual(self.f(n), n, n)

    def test_bad_names_are_rejected(self):
        bad = ["", "   ", "?", "a/b", "a" + chr(92) + "b", "..", "." + "hidden", "a" + chr(0) + "b", "tab" + chr(9) + "x",
               "x" * 121, "name.", "con", "COM1", "lpt3.txt", "a<b", "a>b", "a:b", 'a"b', "a|b", "a*b"]
        for n in bad:
            self.assertEqual(self.f(n), "", repr(n))
        self.assertEqual(self.f("  trail  "), "trail")      # 앞뒤 공백은 잘라서 받는다 (파일 이름 끝 공백은 Windows 가 버린다)
        for n in (5, None, ["a"], {"n": 1}):
            self.assertEqual(self.f(n), "", repr(n))


class WikiPageHttpTest(unittest.TestCase):
    """실제 핸들러로 — 검증 함수가 두 경로(GET·POST)에 **배선**돼 있는지 본다."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="lwwikiname_")
        cls.wiki = os.path.join(cls.tmp, "wiki")
        os.makedirs(cls.wiki)
        # 위키 폴더 **밖**의 파일 — 경로 탈출이 막혔는지 확인하는 표적
        with open(os.path.join(cls.tmp, "secret.md"), "w", encoding="utf-8") as f:
            f.write("outside")

        class _S:                 # Handler 가 쓰는 최소 설정 — 나머지 키는 빈 값
            wiki_dir = cls.wiki
            data_dir = os.path.join(cls.tmp, "data")

            def __getattr__(self, name):
                return ""

        import contextlib

        class _P:
            s = _S()

            @staticmethod
            @contextlib.contextmanager
            def request_scope(**_kw):    # 일반 POST 경로가 요청 범위를 연다 — 여기서는 아무것도 안 한다
                yield

        cls._prev_pipe = getattr(ws.Handler, "pipe", None)
        cls._prev_auth = getattr(ws.Handler, "auth", None)
        ws.Handler.pipe = _P()
        ws.Handler.auth = None    # 인증 끔 → 모든 요청이 admin (격리 환경과 같다)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ws.Handler)
        cls.httpd.daemon_threads = True
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        ws.Handler.pipe = cls._prev_pipe
        ws.Handler.auth = cls._prev_auth
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8"))
            except Exception:
                return e.code, {}

    def test_post_bad_name_is_400_not_500(self):
        for name in ("?", "a" + chr(92) + "b", "..", "con", "a" + chr(0) + "b"):
            code, body = self._req("POST", "/api/wiki/page", {"name": name, "content": "x"})
            self.assertEqual(code, 400, (name, code, body))
        code, _ = self._req("POST", "/api/wiki/page", {"name": "ok", "content": ["not", "a", "string"]})
        self.assertEqual(code, 400)

    def test_get_cannot_escape_wiki_dir(self):
        code, body = self._req("GET", "/api/wiki/page?name=..%2Fsecret")
        self.assertEqual(code, 400, body)
        code, body = self._req("GET", "/api/wiki/page?name=..%5Csecret")
        self.assertEqual(code, 400, body)
        code, _ = self._req("GET", "/api/wiki/page?name=%3F")
        self.assertEqual(code, 400)

    def test_good_name_round_trips(self):
        code, body = self._req("POST", "/api/wiki/page", {"name": "메모 2026", "content": "# 안녕"})
        self.assertEqual(code, 200, body)
        self.assertTrue(os.path.isfile(os.path.join(self.wiki, "메모 2026.md")))
        code, body = self._req("GET", "/api/wiki/page?name=%EB%A9%94%EB%AA%A8%202026")
        self.assertEqual(code, 200, body)
        self.assertEqual(body["content"], "# 안녕")
        code, _ = self._req("GET", "/api/wiki/page?name=nope")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
