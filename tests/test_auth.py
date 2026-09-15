# -*- coding: utf-8 -*-
"""인증·권한·감사: 작업 분류, 비밀번호 해시, 세션 서명, 역할/확인/문구/재인증 게이트, 헤더 SSO, OIDC 코드 플로우(가짜 IdP),
Web 서버 통합(로그인 → 쿠키 → 428 단계 확인 → 파괴적 빌드 승인 → 스냅샷 생성 → 감사 로그), CLI 확인 프롬프트(비대화형 거부)."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles  # noqa: E402
from llmwiki import auth as A  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def test_api_levels(self):
        c = A.classify_api
        self.assertEqual(c("POST", "/api/build", {"full": True})[0], "rebuild")
        self.assertEqual(c("POST", "/api/build", {"reset": True, "full": False})[0], "rebuild")
        self.assertEqual(c("POST", "/api/build", {"full": True, "purge_logs": True})[0], "destructive")
        self.assertEqual(c("POST", "/api/build", {"channel": "fts"})[0], "rebuild")
        self.assertEqual(c("POST", "/api/build", {"full": False})[0], "index")
        self.assertEqual(c("POST", "/api/maintenance", {"action": "purge_requests"})[0], "destructive")
        self.assertEqual(c("POST", "/api/maintenance", {"action": "vacuum"})[0], "index")
        self.assertEqual(c("POST", "/api/query", {"q": "x"})[0], "read")
        self.assertEqual(c("POST", "/api/forensic/expect", {})[0], "read")
        self.assertEqual(c("POST", "/api/eval", {})[0], "run")
        self.assertEqual(c("POST", "/api/pins", {"action": "add"})[0], "edit")
        self.assertEqual(c("POST", "/api/config", {})[0], "admin")
        self.assertEqual(c("POST", "/api/cli", {"argv": "build --full"})[0], "rebuild")
        self.assertEqual(c("POST", "/api/cli", {"argv": "build vector"})[0], "rebuild")
        self.assertEqual(c("POST", "/api/cli", {"argv": ["config", "reset"]})[0], "destructive")
        self.assertEqual(c("POST", "/api/cli", {"argv": "stats"})[0], "run")     # 콘솔은 class3 이상
        self.assertEqual(c("POST", "/api/cli", {"argv": "build"})[0], "index")
        self.assertEqual(c("POST", "/api/cli", {"argv": "users add x --role admin"})[0], "admin")
        self.assertEqual(c("POST", "/api/cli", {"argv": "pin add --doc x"})[0], "edit")
        self.assertEqual(c("POST", "/api/snapshot", {"action": "restore", "name": "x"})[0], "rebuild")
        self.assertEqual(c("POST", "/api/build/verify", {"fix": True})[0], "index")
        self.assertEqual(c("POST", "/api/build/verify", {})[0], "read")
        self.assertEqual(c("GET", "/api/audit", {})[0], "admin")
        self.assertEqual(c("GET", "/api/status", {})[0], "read")
        self.assertEqual(c("POST", "/api/no/such", {})[0], "edit")   # 모르는 POST 는 안전하게 edit
        self.assertEqual(A.norm_role("operator"), "class1")
        self.assertEqual(A.norm_role("bogus"), "viewer")
        self.assertTrue(A.RANK["builder"] > A.RANK["class1"] > A.RANK["class2"] > A.RANK["class3"] > A.RANK["viewer"])

    def test_password_and_signer(self):
        h = A.hash_password("secret-1234")
        self.assertTrue(A.verify_password("secret-1234", h))
        self.assertFalse(A.verify_password("secret-123", h))
        self.assertFalse(A.verify_password("x", "garbage"))
        s = A.Signer(b"k" * 32)
        tok = s.sign({"u": "a", "exp": time.time() + 10})
        self.assertEqual(s.verify(tok)["u"], "a")
        self.assertIsNone(s.verify(tok[:-2] + "zz"))
        self.assertIsNone(s.verify(s.sign({"u": "a", "exp": time.time() - 1})))


class AuthGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["LLMWIKI_SECURITY_PATH"] = os.path.join(self.tmp, "security.json")
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(self.tmp, "logs")
        self.s = Settings(data_dir=os.path.join(self.tmp, "data"))
        A.save_security(dict(A.DEFAULT_SECURITY, mode="on"))
        self.auth = A.Auth(self.s, host="0.0.0.0")
        self.auth.add_user("alice", "alice-pass-1", "admin")
        self.auth.add_user("bob", "bob-pass-12", "operator")      # 구 역할 이름 → class1
        self.auth.add_user("vic", "vic-pass-12", "viewer")

    def tearDown(self):
        os.environ.pop("LLMWIKI_SECURITY_PATH", None)
        os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _need(self, user, path, body):
        try:
            self.auth.authorize(user, "POST", path, body)
        except A.AuthError as e:
            return e
        return None

    def test_roles_confirm_phrase_reauth(self):
        alice = self.auth.login_local("alice", "alice-pass-1")
        self.assertIsNotNone(alice)
        self.assertIsNone(self.auth.login_local("alice", "wrong"))
        bob = self.auth.login_local("bob", "bob-pass-12")
        vic = self.auth.login_local("vic", "vic-pass-12")
        self.assertEqual(bob.role, "class1")
        # viewer: 읽기만 (run 은 class3)
        self.assertIsNone(self._need(vic, "/api/query", {"q": "x"}))
        self.assertEqual(self._need(vic, "/api/build", {"full": False}).status, 403)
        self.assertEqual(self._need(vic, "/api/eval", {}).status, 403)
        # class1(구 operator): index 는 확인 필요(mode on), 확인하면 통과; rebuild 는 역할 부족
        e = self._need(bob, "/api/build", {"full": False})
        self.assertEqual(e.status, 428)
        self.assertTrue(e.need.get("confirm"))
        self.assertIsNone(self._need(bob, "/api/build", {"full": False, "_confirm": True}))
        self.assertEqual(self._need(bob, "/api/build", {"full": True, "_confirm": True}).status, 403)
        self.assertIsNone(self._need(bob, "/api/pins", {"action": "add", "_confirm": True}))     # edit 도 가능
        # permissions 오버라이드: run 을 viewer 에게, 개별 op 를 올림
        self.auth.set_permission("run", "viewer")
        self.assertIsNone(self._need(vic, "/api/eval", {}))
        self.auth.set_permission("/api/eval", "builder")
        self.assertEqual(self._need(vic, "/api/eval", {}).status, 403)
        self.assertEqual(self._need(bob, "/api/eval", {}).status, 403)
        self.assertEqual(self.auth.min_role("run", "/api/eval"), "builder")
        self.auth.set_permission("/api/eval", "")
        self.assertEqual(self.auth.min_role("run", "/api/eval"), "viewer")
        self.auth.set_permission("run", "")
        self.assertEqual(self.auth.min_role("run", "/api/eval"), "class3")
        # 익명 접속 (anonymous_role) → 게스트 viewer, 상위 작업은 401(로그인 유도)
        guest = self.auth.identify({}, "9.9.9.9")
        self.assertEqual((guest.name, guest.role, guest.via), ("guest", "viewer", "anon"))
        self.assertIsNone(self._need(guest, "/api/query", {"q": "x"}))
        self.assertEqual(self._need(guest, "/api/build", {"full": False}).status, 401)
        self.auth.cfg["anonymous_role"] = ""
        self.assertIsNone(self.auth.identify({}, "9.9.9.9"))
        self.auth.cfg["anonymous_role"] = "viewer"
        # API 키 → Bearer 로 신원 확인, 역할 부여
        k = self.auth.add_api_key("mcp-client", "class3")
        self.assertTrue(k["token"].startswith("lwk_"))
        u = self.auth.identify({"Authorization": "Bearer " + k["token"]}, "9.9.9.9")
        self.assertEqual((u.role, u.via), ("class3", "apikey"))
        self.assertIsNone(self._need(u, "/api/eval", {}))
        # 잘못된/폐기된 lwk_ 키는 게스트로 강등하지 않고 401 — 호출측(MCP 클라이언트)이 키 문제를 알 수 있어야 한다
        with self.assertRaises(A.AuthError) as cm:
            self.auth.identify({"Authorization": "Bearer lwk_bad_x"}, "9.9.9.9")
        self.assertEqual(cm.exception.status, 401)
        self.assertTrue(self.auth.remove_api_key(k["id"]))
        with self.assertRaises(A.AuthError):
            self.auth.identify({"Authorization": "Bearer " + k["token"]}, "9.9.9.9")
        # lwk_ 형식이 아닌 Bearer(프록시가 붙인 토큰 등)는 무시하고 게스트로
        self.assertEqual(self.auth.identify({"Authorization": "Bearer something-else"}, "9.9.9.9").via, "anon")
        # admin destructive: 확인 + 문구 + 비밀번호
        e = self._need(alice, "/api/build", {"full": True})
        self.assertEqual(e.status, 428)
        self.assertEqual(e.need["phrase"], "DELETE INDEX")
        self.assertTrue(e.need["password"])
        e2 = self._need(alice, "/api/build", {"full": True, "_confirm": True, "_phrase": "DELETE INDEX", "_password": "nope"})
        self.assertEqual(e2.status, 428)
        self.assertTrue(e2.need.get("password"))
        self.assertNotIn("phrase", e2.need)
        self.assertIsNone(self._need(alice, "/api/build", {"full": True, "_confirm": True, "_phrase": "DELETE INDEX", "_password": "alice-pass-1"}))
        # 로그인 안 함
        self.assertEqual(self._need(None, "/api/query", {"q": "x"}).status, 401)
        # 세션 쿠키 왕복 + 역할 변경 즉시 반영
        cookie = self.auth.make_cookie(alice).split(";")[0]
        u = self.auth.user_from_cookie(cookie)
        self.assertEqual((u.name, u.role, u.via), ("alice", "admin", "local"))
        self.auth.set_role("alice", "viewer")
        self.assertEqual(self.auth.user_from_cookie(cookie).role, "viewer")
        # 감사 로그
        self.auth.audit(alice, "build --full", "destructive", True, "1.2.3.4", detail={"_password": "x", "full": True})
        rows = A.Auth.audit_tail(10)
        self.assertEqual(rows[-1]["user"], "alice")
        self.assertEqual(rows[-1]["detail"]["_password"], "***")

    def test_mode_off_still_requires_phrase(self):
        A.save_security(dict(A.DEFAULT_SECURITY, mode="auto"))
        a = A.Auth(self.s, host="127.0.0.1")
        self.assertEqual(a.mode, "off")
        u = a.identify({}, "127.0.0.1")
        self.assertEqual(u.role, "admin")
        self.assertIsNone(self._try(a, u, {"full": False}))                     # off: warn 은 확인 없이 통과
        e = self._try(a, u, {"full": True})
        self.assertEqual(e.status, 428)
        self.assertEqual(e.need["phrase"], "DELETE INDEX")
        self.assertNotIn("password", e.need)                                   # off 모드엔 비밀번호가 없음
        self.assertIsNone(self._try(a, u, {"full": True, "_confirm": True, "_phrase": "DELETE INDEX"}))

    @staticmethod
    def _try(a, u, body):
        try:
            a.authorize(u, "POST", "/api/build", body)
        except A.AuthError as e:
            return e
        return None

    def test_header_sso_and_role_map(self):
        cfg = A.load_security()
        cfg["sso"] = dict(cfg["sso"], enabled=True, type="header", role_map={"admin": ["wiki-admins"], "operator": ["wiki-ops"]},
                          header={"user": "X-Forwarded-User", "groups": "X-Forwarded-Groups", "trusted_proxies": ["10.0.0.1"]})
        A.save_security(cfg)
        self.auth.reload()
        h = {"X-Forwarded-User": "carol", "X-Forwarded-Groups": "eng,wiki-ops"}
        u = self.auth.identify(h, "10.0.0.1")
        self.assertEqual((u.name, u.role, u.via), ("carol", "class1", "sso"))     # role_map 의 operator 그룹 → class1
        self.auth.cfg["anonymous_role"] = ""
        self.assertIsNone(self.auth.identify(h, "10.0.0.9"))        # 신뢰하지 않는 주소의 헤더는 무시
        self.auth.cfg["anonymous_role"] = "viewer"
        u2 = self.auth.identify({"X-Forwarded-User": "dave", "X-Forwarded-Groups": "wiki-admins"}, "10.0.0.1")
        self.assertEqual(u2.role, "admin")
        self.auth.set_role("dave", "viewer")                           # 로컬 지정이 IdP 그룹보다 우선
        self.assertEqual(self.auth.identify({"X-Forwarded-User": "dave", "X-Forwarded-Groups": "wiki-admins"}, "10.0.0.1").role, "viewer")
        # SSO 사용자는 재인증 대신 문구만
        e = self._need(u2, "/api/build", {"full": True})
        self.assertEqual(e.status, 428)
        self.assertNotIn("password", e.need)


class FakeIdP(BaseHTTPRequestHandler):
    """OIDC 흉내: discovery / authorize(리다이렉트 없이 code 를 돌려줌) / token / userinfo"""
    base = ""
    last_token_req = {}

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/.well-known/openid-configuration"):
            return self._send({"issuer": FakeIdP.base, "authorization_endpoint": FakeIdP.base + "/authorize", "token_endpoint": FakeIdP.base + "/token",
                               "userinfo_endpoint": FakeIdP.base + "/userinfo", "jwks_uri": FakeIdP.base + "/jwks"})
        if self.path.startswith("/userinfo"):
            return self._send({"sub": "u1", "preferred_username": "erin", "email": "erin@corp.example", "groups": ["wiki-admins"]})
        self._send({"error": "nope"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode("utf-8")))
        FakeIdP.last_token_req = form
        if self.path.startswith("/token"):
            if form.get("code") != "good-code":
                return self._send({"error": "invalid_grant"}, 400)
            nonce = form.get("_nonce_for_test", "")
            payload = {"iss": FakeIdP.base, "aud": "wiki-client", "exp": time.time() + 300, "nonce": nonce, "preferred_username": "erin", "groups": ["wiki-admins"]}
            b64 = lambda d: __import__("base64").urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")   # noqa: E731
            return self._send({"access_token": "at-1", "id_token": b64({"alg": "none"}) + "." + b64(payload) + ".sig"})
        self._send({"error": "nope"}, 404)


class OidcTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServer(("127.0.0.1", 0), FakeIdP)
        FakeIdP.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["LLMWIKI_SECURITY_PATH"] = os.path.join(self.tmp, "security.json")
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(self.tmp, "logs")
        cfg = json.loads(json.dumps(A.DEFAULT_SECURITY))
        cfg["mode"] = "on"
        cfg["sso"].update({"enabled": True, "type": "oidc", "issuer": FakeIdP.base, "client_id": "wiki-client", "redirect_uri": "http://wiki/auth/sso/callback",
                           "role_map": {"admin": ["wiki-admins"], "operator": []}, "allowed_domains": ["corp.example"]})
        A.save_security(cfg)
        os.environ["LLMWIKI_OIDC_CLIENT_SECRET"] = "s3cret"
        self.auth = A.Auth(Settings(data_dir=os.path.join(self.tmp, "data")), host="0.0.0.0")

    def tearDown(self):
        for k in ("LLMWIKI_SECURITY_PATH", "LLMWIKI_LOGS_DIR_PATH", "LLMWIKI_OIDC_CLIENT_SECRET"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_code_flow(self):
        self.assertTrue(self.auth.oidc_enabled())
        url, cookie = self.auth.oidc_start("/x")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        self.assertEqual(q["client_id"], "wiki-client")
        self.assertEqual(q["response_type"], "code")
        state, nonce = q["state"], q["nonce"]
        cookie_hdr = cookie.split(";")[0]
        # state 불일치
        with self.assertRaises(A.AuthError):
            self.auth.oidc_callback({"code": "good-code", "state": "bad"}, cookie_hdr)
        # 성공: 가짜 IdP 가 nonce 를 id_token 에 넣도록 폼에 실어 보낸다 (테스트 편의)
        orig = self.auth._http_json

        def patched(url_, data=None, headers=None, timeout=15):
            if data is not None and url_.endswith("/token"):
                data = data + ("&_nonce_for_test=" + nonce).encode("ascii")
            return orig(url_, data, headers, timeout)
        self.auth._http_json = patched
        user, nxt = self.auth.oidc_callback({"code": "good-code", "state": state}, cookie_hdr)
        self.assertEqual((user.name, user.role, user.via), ("erin", "admin", "sso"))
        self.assertEqual(nxt, "/x")
        self.assertEqual(FakeIdP.last_token_req.get("client_secret"), "s3cret")
        self.assertEqual(FakeIdP.last_token_req.get("grant_type"), "authorization_code")
        # 잘못된 code → 502
        with self.assertRaises(A.AuthError) as cm:
            self.auth.oidc_callback({"code": "bad", "state": state}, cookie_hdr)
        self.assertEqual(cm.exception.status, 502)


class WebAuthIntegrationTest(unittest.TestCase):
    """mode=on 서버: 로그인 → 쿠키 → 권한/확인 → 파괴적 빌드(스냅샷) → 감사 로그."""
    @classmethod
    def setUpClass(cls):
        from llmwiki.pipeline import Pipeline
        from llmwiki.web import server as ws
        cls.ws = ws
        cls.tmp = tempfile.mkdtemp()
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        for i in range(3):
            with open(os.path.join(corpus, "d%d.md" % i), "w", encoding="utf-8") as f:
                f.write("# ISSUE-%d RX DMA underrun\n\nRX DMA underrun 시 PHY 재시작 실패. CL-%d 로 수정.\n" % (i, i))
        for k in ("SECURITY", "LOGS_DIR", "AGENTS", "PINS", "QUERY_RULES", "PRESETS", "RULES"):
            os.environ["LLMWIKI_%s_PATH" % k] = os.path.join(cls.tmp, k.lower() + (".json" if k in ("RULES", "SECURITY", "AGENTS", "PINS", "QUERY_RULES", "PRESETS") else ""))
        from llmwiki import config as _cfg
        cls._cfg_path = _cfg.CONFIG_PATH
        _cfg.CONFIG_PATH = os.path.join(cls.tmp, "config.json")
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"), wiki_dir=os.path.join(cls.tmp, "wiki"), llm_provider="mock", embed_provider="hash", embed_dim=128)
        s.toggles = Toggles(query_cache=False, health_check=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)
        cfg = json.loads(json.dumps(A.DEFAULT_SECURITY))
        cfg["mode"] = "on"
        cfg["anonymous_role"] = ""          # 이 테스트는 로그인 필수 모드
        A.save_security(cfg)
        auth = A.Auth(s, host="0.0.0.0")
        auth.add_user("admin1", "admin-pass-1", "admin")
        auth.add_user("op1", "op-pass-123", "class1")
        auth.add_user("view1", "view-pass-1", "viewer")
        ws.Handler.pipe = cls.p
        ws.Handler.auth = auth
        ws.Handler.host = "0.0.0.0"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ws.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.p.store.close()
        cls.ws.Handler.auth = None
        cls.ws.Handler.host = "127.0.0.1"
        from llmwiki import config as _cfg
        _cfg.CONFIG_PATH = cls._cfg_path
        for k in ("SECURITY", "LOGS_DIR", "AGENTS", "PINS", "QUERY_RULES", "PRESETS", "RULES"):
            os.environ.pop("LLMWIKI_%s_PATH" % k, None)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _req(self, method, path, body=None, cookie="", raw=False):
        headers = {"Content-Type": "application/json", "X-Requested-With": "llmwiki"}
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=json.dumps(body).encode("utf-8") if body is not None else None,
                                     headers=headers, method=method)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=60) as r:
                data = r.read()
                return r.status, (data if raw else json.loads(data.decode("utf-8") or "{}")), r.headers
        except urllib.error.HTTPError as e:
            data = e.read()
            try:
                return e.code, json.loads(data.decode("utf-8")), e.headers
            except Exception:
                return e.code, data, e.headers

    def _login(self, u, p):
        code, j, h = self._req("POST", "/api/auth/login", {"username": u, "password": p})
        self.assertEqual(code, 200, j)
        return h.get("Set-Cookie").split(";")[0]

    def test_flow(self):
        # 미로그인: / 는 로그인으로 리다이렉트, API 는 401, 로그인 페이지/정적은 공개
        code, _, h = self._req("GET", "/", raw=True)
        self.assertEqual(code, 302)
        self.assertEqual(h.get("Location"), "/login")
        code, page, _ = self._req("GET", "/login", raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b"login", page)
        self.assertEqual(self._req("GET", "/api/status")[0], 401)
        code, me, _ = self._req("GET", "/api/auth/me")
        self.assertEqual((me["mode"], me["local"], me["user"]), ("on", True, None))
        self.assertEqual(self._req("POST", "/api/auth/login", {"username": "admin1", "password": "bad"})[0], 401)
        admin = self._login("admin1", "admin-pass-1")
        op = self._login("op1", "op-pass-123")
        view = self._login("view1", "view-pass-1")
        code, st, _ = self._req("GET", "/api/status", cookie=view)
        self.assertEqual(code, 200)
        self.assertEqual(st["auth"]["user"]["name"], "view1")
        # viewer: 질의 OK, 빌드 403, 사용자 목록 403
        self.assertEqual(self._req("POST", "/api/query", {"q": "RX DMA underrun", "overrides": {"llm_answer": False}}, cookie=view)[0], 200)
        self.assertEqual(self._req("POST", "/api/build", {"full": False}, cookie=view)[0], 403)
        self.assertEqual(self._req("GET", "/api/auth/users", cookie=view)[0], 403)
        # operator: 증분 빌드는 확인(428) 후 OK, 전체는 403
        code, j, _ = self._req("POST", "/api/build", {"full": False}, cookie=op)
        self.assertEqual(code, 428)
        self.assertTrue(j["need"]["confirm"])
        code, j, _ = self._req("POST", "/api/build", {"full": False, "_confirm": True}, cookie=op)
        self.assertEqual(code, 200)
        self.assertIn("job", j)
        self._wait_job(j["job"], op)
        self.assertEqual(self._req("POST", "/api/build", {"full": True, "_confirm": True}, cookie=op)[0], 403)
        # admin: 전체 초기화 = 확인 + 문구 + 비밀번호 → 스냅샷 생성 → 빌드
        code, j, _ = self._req("POST", "/api/build", {"full": True, "reset": True}, cookie=admin)
        self.assertEqual(code, 428)
        self.assertEqual(j["need"]["phrase"], "DELETE INDEX")
        self.assertTrue(j["need"]["password"])
        code, j, _ = self._req("POST", "/api/build", {"full": True, "reset": True, "_confirm": True, "_phrase": "DELETE INDEX", "_password": "wrong"}, cookie=admin)
        self.assertEqual(code, 428)
        code, j, _ = self._req("POST", "/api/build", {"full": True, "reset": True, "_confirm": True, "_phrase": "DELETE INDEX", "_password": "admin-pass-1"}, cookie=admin)
        self.assertEqual(code, 200, j)
        job = self._wait_job(j["job"], admin)
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertTrue(any("스냅샷" in ln for ln in job["log"]), job["log"])
        code, sn, _ = self._req("GET", "/api/snapshot", cookie=admin)
        self.assertTrue(sn["snapshots"] and sn["snapshots"][0]["tag"].startswith("auto:reset"))
        # 콘솔: 파괴적 argv 도 같은 게이트 (+ 승인되면 --yes 가 붙어 프롬프트 없이 실행)
        code, j, _ = self._req("POST", "/api/cli", {"argv": "maintenance purge_requests"}, cookie=admin)
        self.assertEqual(code, 428)
        code, j, _ = self._req("POST", "/api/cli", {"argv": "maintenance purge_requests", "_confirm": True, "_phrase": "DELETE INDEX", "_password": "admin-pass-1"}, cookie=admin)
        self.assertEqual(code, 200)
        self.assertEqual(j["code"], 0, j)
        self.assertEqual(self._req("POST", "/api/cli", {"argv": "stats"}, cookie=view)[0], 403)
        self.assertEqual(self._req("POST", "/api/pins", {"action": "add", "doc": "d0", "_confirm": True}, cookie=op)[0], 200)   # class1 ⊇ edit
        # 사용자 관리 (admin) + 감사 로그
        code, j, _ = self._req("POST", "/api/auth/users", {"action": "add", "name": "new1", "password": "new1-pass-1", "role": "class2", "_confirm": True}, cookie=admin)
        self.assertEqual(code, 200, j)
        self.assertTrue(any(u["name"] == "new1" and u["role"] == "class2" for u in j["users"]))
        # 권한 표 편집 (admin) → run 을 viewer 에게 → viewer 가 eval 가능
        code, j, _ = self._req("POST", "/api/security", {"action": "set_permission", "key": "run", "role": "viewer", "_confirm": True}, cookie=admin)
        self.assertEqual(code, 200, j)
        self.assertEqual(j["permissions"]["levels"]["run"], "viewer")
        code, j, _ = self._req("POST", "/api/cli", {"argv": "stats"}, cookie=view)
        self.assertEqual(code, 200, j)
        code, j, _ = self._req("POST", "/api/security", {"action": "set_permission", "key": "run", "role": "", "_confirm": True}, cookie=admin)
        self.assertEqual(j["permissions"]["levels"]["run"], "class3")
        # API 키 발급 → Bearer 로 질의
        code, j, _ = self._req("POST", "/api/apikeys", {"action": "add", "name": "t", "role": "viewer", "_confirm": True}, cookie=admin)
        self.assertEqual(code, 200, j)
        tok = j["token"]
        req = urllib.request.Request("http://127.0.0.1:%d/api/query" % self.port, data=json.dumps({"q": "RX DMA underrun", "overrides": {"llm_answer": False}}).encode("utf-8"),
                                     method="POST", headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=60) as r:
            self.assertEqual(r.status, 200)
        code, j, _ = self._req("GET", "/api/apikeys", cookie=admin)
        self.assertTrue(any(k["name"] == "t" for k in j["keys"]))
        code, au, _ = self._req("GET", "/api/audit?n=50", cookie=admin)
        self.assertEqual(code, 200, au)
        ops = [r["op"] for r in au["rows"]]
        self.assertIn("login", ops)
        self.assertTrue(any(r["op"].startswith("build --full") and r["ok"] for r in au["rows"]))
        self.assertTrue(any(r["ok"] is False and r["user"] == "view1" for r in au["rows"]))   # 거부 기록
        # CSRF: Origin 이 다르면 403
        req = urllib.request.Request("http://127.0.0.1:%d/api/query" % self.port, data=b'{"q":"x"}', method="POST",
                                     headers={"Content-Type": "application/json", "Cookie": admin, "Origin": "http://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 403)
        # 로그아웃
        code, _, h = self._req("POST", "/api/auth/logout", {}, cookie=admin)
        self.assertIn("Max-Age=0", h.get("Set-Cookie"))

    def _wait_job(self, jid, cookie):
        for _ in range(300):
            code, j, _ = self._req("GET", "/api/jobs/" + jid, cookie=cookie)
            if j.get("status") != "running":
                return j
            time.sleep(0.2)
        raise AssertionError("job timeout")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CliConfirmTest(unittest.TestCase):
    def test_non_interactive_refuses_without_yes(self):
        from llmwiki.cli import run_captured, run
        from llmwiki.pipeline import Pipeline
        tmp = tempfile.mkdtemp()
        try:
            os.environ["LLMWIKI_SECURITY_PATH"] = os.path.join(tmp, "security.json")
            os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(tmp, "logs")
            corpus = os.path.join(tmp, "corpus")
            os.makedirs(corpus)
            with open(os.path.join(corpus, "a.md"), "w", encoding="utf-8") as f:
                f.write("# A\n\nhello world\n")
            s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data"), wiki_dir=os.path.join(tmp, "wiki"), llm_provider="none", embed_provider="hash", embed_dim=64)
            s.toggles = Toggles(health_check=False, query_cache=False)
            p = Pipeline(s)
            try:
                out = run_captured(["build", "--full"], s, p)        # Web 콘솔/파이프 = 비대화형 → 거부
                self.assertEqual(out["code"], 4, out)
                self.assertIn("--yes", out["output"])
                out2 = run_captured(["build", "--full", "--yes", "--no-snapshot"], s, p)
                self.assertEqual(out2["code"], 0, out2)
                out3 = run_captured(["maintenance", "purge_requests"], s, p)
                self.assertEqual(out3["code"], 4)
                out4 = run_captured(["snapshot", "create", "--tag", "t1"], s, p)
                self.assertEqual(out4["code"], 0, out4)
                out5 = run_captured(["snapshot", "list"], s, p)
                self.assertIn("t1", out5["output"])
                out6 = run_captured(["snapshot", "restore", "x", "--yes"], s, p)
                self.assertNotEqual(out6["code"], 0)
                # ---- CLI 권한 게이트: cli.default_role=viewer 면 빌드 거부(5), --user 로 승격하면 통과 ----
                cfg = A.load_security()
                cfg["cli"] = {"default_role": "viewer", "require_login": False}
                A.save_security(cfg)
                a = A.Auth(s)
                a.add_user("b1", "builder-pass-1", "builder")
                import io as _io
                from contextlib import redirect_stdout
                buf = _io.StringIO()
                with redirect_stdout(buf):
                    code = run(["build", "--full", "--yes", "--no-snapshot"], s, p, gate=True)
                self.assertEqual(code, 5, buf.getvalue())
                self.assertIn("권한 부족", buf.getvalue())
                buf = _io.StringIO()
                with redirect_stdout(buf):
                    code = run(["stats"], s, p, gate=True)            # read 는 viewer 로 가능
                self.assertEqual(code, 0, buf.getvalue())
                os.environ["LLMWIKI_PASSWORD"] = "builder-pass-1"
                try:
                    buf = _io.StringIO()
                    with redirect_stdout(buf):
                        code = run(["--user", "b1", "build", "--full", "--yes", "--no-snapshot"], s, p, gate=True)
                    self.assertEqual(code, 0, buf.getvalue())
                    os.environ["LLMWIKI_PASSWORD"] = "wrong"
                    buf = _io.StringIO()
                    with redirect_stdout(buf):
                        code = run(["--user", "b1", "build", "--yes"], s, p, gate=True)
                    self.assertEqual(code, 5)
                finally:
                    os.environ.pop("LLMWIKI_PASSWORD", None)
                rows = A.Auth.audit_tail(20)
                self.assertTrue(any(r["via"] == "cli" and r["ok"] is False for r in rows))
                # security perms CLI
                out7 = run_captured(["security", "perms", "set", "run=viewer", "/api/eval=class2"], s, p)
                self.assertEqual(out7["code"], 0, out7)
                self.assertEqual(A.load_security()["permissions"]["levels"]["run"], "viewer")
                self.assertEqual(A.load_security()["permissions"]["ops"]["/api/eval"], "class2")
                out8 = run_captured(["apikey", "add", "k1", "--role", "class3"], s, p)
                self.assertIn("lwk_", out8["output"])
                self.assertIn("k1", run_captured(["apikey", "list"], s, p)["output"])
            finally:
                p.store.close()
        finally:
            os.environ.pop("LLMWIKI_SECURITY_PATH", None)
            os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
