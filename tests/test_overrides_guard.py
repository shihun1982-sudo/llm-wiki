# -*- coding: utf-8 -*-
"""요청 단위 overrides 화이트리스트 + 500 트레이스 마스킹 (2026-09-18, CODE_REVIEW_0917 P0-1 · S2).

예전에는 익명(viewer)이 /api/query 본문의 overrides 로 `openai_base_url` 을 바꿔 서버가 .env 의 PAT 를 임의 주소로
보내게 할 수 있었고, 500 응답에 스택트레이스가 그대로 실렸다."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles  # noqa: E402
from llmwiki import auth as A  # noqa: E402
from llmwiki.web import server as ws  # noqa: E402


class FilterUnitTest(unittest.TestCase):
    def setUp(self):
        self._auth = ws.Handler.auth
        ws.Handler.auth = None

    def tearDown(self):
        ws.Handler.auth = self._auth

    def test_viewer_allowed_keys(self):
        ov = {"llm_answer": False, "top_k_final": 3, "answer_model": "m", "llm_roles": {"answer": {"model": "x", "effort": "low"}}, "answer_mode": "grounded"}
        self.assertEqual(ws._filter_overrides(dict(ov), "viewer"), ov)
        self.assertEqual(ws._filter_overrides({}, ""), {})

    def test_viewer_denied_url_and_paths(self):
        for k in ("openai_base_url", "anthropic_base_url", "corpus_dirs", "data_dir", "openai_extra_headers", "openai_api_key_header", "mcp_plugins_dir", "web_host"):
            with self.assertRaises(A.AuthError, msg=k) as cm:
                ws._filter_overrides({k: "x", "llm_answer": False}, "viewer")
            self.assertEqual(cm.exception.status, 403)
            self.assertIn(k, cm.exception.error)
        with self.assertRaises(A.AuthError):
            ws._filter_overrides({"llm_roles": {"answer": {"base_url": "http://x"}}}, "class3")

    def test_admin_allowed_unless_denied(self):
        self.assertEqual(ws._filter_overrides({"openai_base_url": "http://gw/v1"}, "admin")["openai_base_url"], "http://gw/v1")

    def test_security_json_allow_extra_and_deny(self):
        tmp = tempfile.mkdtemp()
        try:
            os.environ["LLMWIKI_SECURITY_PATH"] = os.path.join(tmp, "security.json")
            cfg = json.loads(json.dumps(A.DEFAULT_SECURITY))
            cfg["overrides"] = {"allow_extra": ["corpus_exclude"], "deny": ["openai_base_url"]}
            A.save_security(cfg)
            ws.Handler.auth = A.Auth(Settings(data_dir=os.path.join(tmp, "data")), host="0.0.0.0")
            self.assertIn("corpus_exclude", ws._filter_overrides({"corpus_exclude": ["x/"]}, "viewer"))
            with self.assertRaises(A.AuthError):
                ws._filter_overrides({"openai_base_url": "http://x"}, "admin")   # deny 는 admin 도 금지
        finally:
            ws.Handler.auth = None
            os.environ.pop("LLMWIKI_SECURITY_PATH", None)
            shutil.rmtree(tmp, ignore_errors=True)


class WebGuardTest(unittest.TestCase):
    """mode=on 서버에서 viewer 가 URL 오버라이드를 보내면 403, 서버 결함은 트레이스 없이 ref 만."""
    @classmethod
    def setUpClass(cls):
        from llmwiki.pipeline import Pipeline
        cls.tmp = tempfile.mkdtemp()
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        with open(os.path.join(corpus, "d.md"), "w", encoding="utf-8") as f:
            f.write("# ISSUE-1 RX DMA underrun\n\nRX DMA underrun 시 PHY 재시작 실패. CL-1 로 수정.\n")
        for k in ("SECURITY", "LOGS_DIR", "AGENTS", "PINS", "QUERY_RULES", "PRESETS", "RULES", "STOPWORDS"):
            os.environ["LLMWIKI_%s_PATH" % k] = os.path.join(cls.tmp, k.lower() + (".json" if k != "LOGS_DIR" else ""))
        from llmwiki import config as _cfg
        cls._cfg_path = _cfg.CONFIG_PATH
        _cfg.CONFIG_PATH = os.path.join(cls.tmp, "config.json")
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"), wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(query_cache=False, health_check=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)
        cfg = json.loads(json.dumps(A.DEFAULT_SECURITY))
        cfg["mode"] = "on"
        A.save_security(cfg)
        auth = A.Auth(s, host="0.0.0.0")
        auth.add_user("admin1", "admin-pass-1", "admin")
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
        ws.Handler.auth = None
        ws.Handler.host = "127.0.0.1"
        from llmwiki import config as _cfg
        _cfg.CONFIG_PATH = cls._cfg_path
        for k in ("SECURITY", "LOGS_DIR", "AGENTS", "PINS", "QUERY_RULES", "PRESETS", "RULES", "STOPWORDS"):
            os.environ.pop("LLMWIKI_%s_PATH" % k, None)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _req(self, method, path, body=None, cookie=""):
        headers = {"Content-Type": "application/json", "X-Requested-With": "llmwiki"}
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=json.dumps(body).encode("utf-8") if body is not None else None,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}"), r.headers
        except urllib.error.HTTPError as e:
            data = e.read()
            try:
                return e.code, json.loads(data.decode("utf-8")), e.headers
            except Exception:
                return e.code, data, e.headers

    def test_guest_cannot_redirect_provider(self):
        code, j, _ = self._req("POST", "/api/query", {"q": "RX DMA underrun", "overrides": {"openai_base_url": "http://attacker/v1", "llm_provider": "openai"}})
        self.assertEqual(code, 403, j)
        self.assertIn("openai_base_url", j.get("error", ""))
        # 허용 키만 있으면 정상 (익명 viewer)
        code, j, _ = self._req("POST", "/api/query", {"q": "RX DMA underrun", "overrides": {"llm_answer": False, "top_k_final": 2}})
        self.assertEqual(code, 200, j)
        # 잡(빌드)도 같은 필터를 거친다 — viewer 는 어차피 403 이지만 필터가 먼저 걸리는지 확인
        code, j, _ = self._req("POST", "/api/models/test", {"which": ["answer"], "overrides": {"openai_base_url": "http://attacker/v1"}})
        self.assertIn(code, (401, 403), j)

    def test_admin_can_test_unsaved_url(self):
        code, j, h = self._req("POST", "/api/auth/login", {"username": "admin1", "password": "admin-pass-1"})
        self.assertEqual(code, 200, j)
        cookie = h.get("Set-Cookie").split(";")[0]
        code, j, _ = self._req("POST", "/api/models/test", {"which": ["answer"], "overrides": {"openai_base_url": "http://127.0.0.1:9/v1", "answer_provider": "openai"}}, cookie=cookie)
        self.assertEqual(code, 200, j)

    def test_500_is_masked_and_logged(self):
        orig = ws.Handler._dispatch_post

        def boom(self_, u, body):
            raise RuntimeError("secret internal detail")
        ws.Handler._dispatch_post = boom
        try:
            code, j, _ = self._req("POST", "/api/query", {"q": "x"})
        finally:
            ws.Handler._dispatch_post = orig
        self.assertEqual(code, 500)
        self.assertEqual(j.get("code"), "internal")
        self.assertNotIn("trace", j)
        self.assertTrue(j.get("ref"))
        logp = os.path.join(os.environ["LLMWIKI_LOGS_DIR_PATH"], "error.log")
        self.assertTrue(os.path.exists(logp))
        with open(logp, "r", encoding="utf-8", errors="ignore") as f:
            self.assertIn(j["ref"], f.read())


if __name__ == "__main__":
    unittest.main()
