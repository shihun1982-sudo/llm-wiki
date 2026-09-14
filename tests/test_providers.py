# -*- coding: utf-8 -*-
"""Phase 1: OpenAI-compatible LLM/임베더 · rerank API · headless agent provider · float16 저장 (로컬 fake HTTP 서버, 네트워크 없음)."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki.config import Settings, Toggles  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402
from llmwiki.providers import OpenAICompatLLM, OpenAICompatEmbedder, make_llm, make_embedder, LLMError  # noqa: E402
from llmwiki.rerankers import rerank_api, ping_rerank_api  # noqa: E402
from llmwiki import headless as hl  # noqa: E402
from llmwiki import tuning as tn  # noqa: E402

CALLS = {"chat": 0, "embed": 0, "rerank": 0, "json_mode": [], "headers": []}


class FakeAPI(BaseHTTPRequestHandler):
    style = "cohere"
    require = None   # ("header-name", "expected value") 이면 그 헤더가 없을 때 401 (PAT 게이트웨이 흉내)

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth_ok(self):
        CALLS["headers"].append({k.lower(): v for k, v in self.headers.items()})
        if FakeAPI.require and self.headers.get(FakeAPI.require[0]) != FakeAPI.require[1]:
            self._send({"error": {"message": "unauthorized"}}, 401)
            return False
        return True

    def do_GET(self):
        if not self._auth_ok():
            return
        if self.path.endswith("/models") or self.path.startswith("/v1/models"):
            return self._send({"data": [{"id": "test-model"}, {"id": "embed-model"}]})
        self._send({"error": "nope"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        if not self._auth_ok():
            return
        if self.path.endswith("/v1/messages"):   # Anthropic-compatible 게이트웨이
            CALLS["chat"] += 1
            user = body["messages"][-1]["content"]
            return self._send({"model": body.get("model"), "stop_reason": "end_turn", "content": [{"type": "text", "text": "anthropic-echo: " + user[:30]}],
                               "usage": {"input_tokens": 7, "output_tokens": 3}})
        if self.path.endswith("/chat/completions"):
            CALLS["chat"] += 1
            CALLS["json_mode"].append("response_format" in body)
            if "response_format" in body and body.get("model") == "no-json":
                return self._send({"error": {"message": "response_format not supported"}}, 400)
            user = body["messages"][-1]["content"]
            text = json.dumps({"ranking": [0, 1]}) if "response_format" in body else "echo: " + user[:40] + " [C1]"
            return self._send({"model": body.get("model"), "choices": [{"message": {"role": "assistant", "content": text}}],
                               "usage": {"prompt_tokens": 12, "completion_tokens": 5}})
        if self.path.endswith("/embeddings"):
            CALLS["embed"] += 1
            vecs = []
            for i, t in enumerate(body["input"]):
                v = [float((hash(t) >> s) % 7) for s in range(0, 32, 4)]
                vecs.append({"index": i, "embedding": v})
            return self._send({"data": list(reversed(vecs)), "model": body.get("model")})
        if self.path.endswith("/rerank"):
            CALLS["rerank"] += 1
            q = body["query"]
            rows = []
            for i, d in enumerate(body["documents"]):
                sc = sum(1 for w in q.lower().split() if w in d.lower()) / max(1, len(q.split()))
                rows.append({"index": i, "relevance_score": round(sc, 3)})
            rows.sort(key=lambda r: -r["relevance_score"])
            if FakeAPI.style == "voyage":
                return self._send({"data": rows, "model": body.get("model", "rerank-2")})
            return self._send({"results": rows, "model": body.get("model", "bge")})
        self._send({"error": "unknown"}, 404)


class ProvidersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServer(("127.0.0.1", 0), FakeAPI)
        cls.port = cls.httpd.server_address[1]
        cls.base = "http://127.0.0.1:%d/v1" % cls.port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["LLMWIKI_AGENTS_PATH"] = os.path.join(self.tmp, "agents.json")
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(self.tmp, "logs")

    def tearDown(self):
        os.environ.pop("LLMWIKI_AGENTS_PATH", None)
        os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
        tn.load_tuning(os.path.join(self.tmp, "none.json"))
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self, **kw):
        corpus = os.path.join(self.tmp, "corpus")
        os.makedirs(corpus, exist_ok=True)
        with open(os.path.join(corpus, "a.md"), "w", encoding="utf-8") as f:
            f.write("# ISSUE-1 RX DMA underrun\n\nRX DMA underrun 시 PHY 재시작 실패. CL-5 로 수정.\n\n## 원인\nFIFO 임계값 오류\n")
        with open(os.path.join(corpus, "b.md"), "w", encoding="utf-8") as f:
            f.write("# 주간 보고\n\n이번 주 TX 전력 제어 검증 완료.\n")
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(self.tmp, "data"), wiki_dir=os.path.join(self.tmp, "wiki"), **kw)
        s.toggles = Toggles(query_cache=False, health_check=False)
        return s

    def test_openai_compat_llm(self):
        llm = OpenAICompatLLM(self.base, "test-model", api_key="k")
        p = llm.ping()
        self.assertTrue(p["ok"])
        r = llm.complete("sys", "hello world")
        self.assertTrue(r["text"].startswith("echo:"))
        self.assertEqual(r["usage"]["input_tokens"], 12)
        r2 = llm.complete("TASK=rerank", "[0] a [1] b", json_mode=True)
        self.assertIn("ranking", r2["text"])
        # json_object 미지원 서버 → 400 후 재시도(response_format 제거)
        llm2 = OpenAICompatLLM(self.base, "no-json")
        r3 = llm2.complete("sys", "x", json_mode=True)
        self.assertTrue(r3["text"])
        self.assertIs(llm2._json_mode_ok, False)
        # 팩토리: llm_provider=openai, 역할별
        s = Settings(llm_provider="openai", llm_model="test-model", openai_base_url=self.base,
                     llm_roles={"rerank": {"provider": "openai", "model": "r-model"}})
        self.assertEqual(make_llm(s).name, "openai")
        self.assertEqual(make_llm(s, "rerank").model, "r-model")
        bad = OpenAICompatLLM("http://127.0.0.1:1/v1", "m")
        self.assertFalse(bad.ping()["ok"])
        with self.assertRaises(LLMError):
            bad.complete("s", "u")

    def test_openai_compat_embedder_and_pipeline(self):
        emb = OpenAICompatEmbedder(self.base, "embed-model", batch=2)
        m = emb.embed(["a", "b", "c"])
        self.assertEqual(m.shape, (3, 8))
        self.assertAlmostEqual(float(np.linalg.norm(m[0])), 1.0, places=5)
        s = self._settings(llm_provider="mock", embed_provider="openai", openai_embed_model="embed-model", openai_base_url=self.base)
        self.assertEqual(make_embedder(s).name, "openai")
        p = Pipeline(s)
        try:
            res, _ = p.build(full=True)
            self.assertGreater(res["embedded"], 0)
            r, t = p.query("RX DMA underrun", log=False)
            st = {c["name"]: c for c in t["children"]}
            self.assertEqual(st["vector_search"]["meta"]["provider"], "openai")
            self.assertTrue(r["hits"])
        finally:
            p.store.close()

    def test_rerank_api_styles_and_pipeline(self):
        s = self._settings(llm_provider="mock", embed_provider="hash", embed_dim=256, rerank_url=self.base + "/rerank", rerank_api_model="bge")
        order, meta = rerank_api(s, "rx dma underrun", ["RX DMA underrun 발생", "주간 보고", "dma"], top_n=3)
        self.assertEqual(order[0][0], 0)
        self.assertEqual(meta["model"], "bge")
        FakeAPI.style = "voyage"
        try:
            s.rerank_api_style = "voyage"
            order2, _ = rerank_api(s, "rx dma underrun", ["주간", "RX DMA underrun"], top_n=2)
            self.assertEqual(order2[0][0], 1)
        finally:
            FakeAPI.style = "cohere"
            s.rerank_api_style = "cohere"
        self.assertTrue(ping_rerank_api(s)["ok"])
        p = Pipeline(s)
        try:
            p.build(full=True)
            tn.T.set("rerank_method", "api")
            r, t = p.query("RX DMA underrun 원인", log=False)
            st = {c["name"]: c for c in t["children"]}
            self.assertIn("rerank_api", st)
            self.assertEqual(st["rerank_api"]["meta"]["model"], "bge")
            self.assertEqual(t["summary"]["llm"]["calls"], 1)      # rerank 는 API, 답변만 LLM(mock)
            # auto + rerank_url 있으면 api 우선
            tn.T.set("rerank_method", "auto")
            _, t2 = p.query("FIFO 임계값", log=False)
            self.assertIn("rerank_api", {c["name"]: c for c in t2["children"]})
            # 엔드포인트 오류 → local 폴백
            p.s.rerank_url = "http://127.0.0.1:1/v1/rerank"
            tn.T.set("rerank_method", "api")
            _, t3 = p.query("FIFO 임계값", log=False)
            st3 = {c["name"]: c for c in t3["children"]}
            self.assertIn("error", st3["rerank_api"]["meta"])
            self.assertIn("rerank_local", st3)
            tn.T.reset("rerank_method")
            status = p.provider_status()
            self.assertIn("rerank", status)
            self.assertIn("agents", status)
        finally:
            p.store.close()

    def test_headless_agent_mock(self):
        agents = hl.load_agents()
        self.assertIn("opencode", agents)
        self.assertIn("mock", agents)
        self.assertTrue(os.path.exists(hl.agents_path()))
        s = Settings(llm_provider="headless:mock", llm_model="")
        llm = make_llm(s, "answer")
        self.assertEqual(llm.name, "headless:mock")
        self.assertTrue(llm.available)
        self.assertTrue(llm.ping()["ok"])
        r = llm.complete("TASK=answer\n규칙", "질문 [C1] [C2]", max_tokens=100)
        self.assertIn("(mock answer)", r["text"])
        self.assertIn("[C1]", r["text"])
        self.assertGreater(r["usage"]["input_tokens"], 0)
        self.assertEqual(r["exit_code"], 0)
        # JSON 역할 + 파일 첨부
        fp = os.path.join(self.tmp, "evidence.md")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("evidence")
        r2 = llm.complete("TASK=rerank", "[0] a\n[1] b", json_mode=True, files=[fp])
        self.assertIn("ranking", r2["text"])
        self.assertIn("files=1", r2["text"])
        # 존재하지 않는 실행 파일 → unavailable + LLMError
        bad = hl.HeadlessAgentLLM("opencode" if not __import__("shutil").which("opencode") else "nope-agent", "m")
        self.assertFalse(bad.available)
        with self.assertRaises(LLMError):
            bad.complete("s", "u")
        # 파서 단위
        ev = hl.parse_output('{"type":"text","part":{"text":"ab"}}\n{"type":"text","part":{"text":"abcd"}}\n{"type":"tool","part":{"text":"zzz"}}\n', "ndjson")
        self.assertEqual(hl.extract_text(ev, ["part.text", "text"]), "abcd")      # delta 누적형
        ev2 = hl.parse_output('{"result":"final","usage":{"input_tokens":3,"output_tokens":4}}', "json")
        self.assertEqual(hl.extract_text(ev2, ["result"]), "final")
        self.assertEqual(hl.extract_usage(ev2, {"input": ["usage.input_tokens"], "output": ["usage.output_tokens"]}), {"input_tokens": 3, "output_tokens": 4})
        # 파이프라인에서 headless 를 answer 역할로
        s2 = self._settings(llm_provider="mock", embed_provider="hash", embed_dim=256, llm_roles={"answer": {"provider": "headless:mock"}})
        p = Pipeline(s2)
        try:
            p.build(full=True)
            r, t = p.query("RX DMA underrun", log=False)
            self.assertEqual(r["answer_mode"], "llm")
            self.assertEqual(r["config"]["llm"], "headless:mock")
        finally:
            p.store.close()

    def test_pat_gateway_headers_and_live(self):
        """사내 게이트웨이(URL + PAT): 헤더 이름/형식이 설정대로 나가고, 401 이면 ping/live 가 실패로 드러나야 한다."""
        from llmwiki.providers import AnthropicHTTPLLM, auth_headers, openai_api_key
        # authorization → Bearer
        FakeAPI.require = ("authorization", "Bearer pat-123")
        try:
            llm = OpenAICompatLLM(self.base, "test-model", api_key="pat-123")
            self.assertTrue(llm.ping()["ok"])
            lv = llm.live_test()
            self.assertTrue(lv["ok"], lv)
            self.assertIn("reply=", lv["detail"])
            bad = OpenAICompatLLM(self.base, "test-model", api_key="wrong")
            pb = bad.ping()
            self.assertFalse(pb["ok"])
            self.assertIn("401", pb["detail"])
            self.assertFalse(bad.live_test()["ok"])
        finally:
            FakeAPI.require = None
        # api-key 헤더 스타일 + 고정 헤더
        FakeAPI.require = ("api-key", "pat-xyz")
        try:
            llm = OpenAICompatLLM(self.base, "test-model", api_key="pat-xyz", key_header="api-key", extra_headers={"X-Tenant": "modem"})
            r = llm.complete("s", "u")
            self.assertTrue(r["text"].startswith("echo:"))
            self.assertEqual(CALLS["headers"][-1].get("x-tenant"), "modem")
            self.assertNotIn("authorization", CALLS["headers"][-1])
            emb = OpenAICompatEmbedder(self.base, "embed-model", api_key="pat-xyz", key_header="api-key", batch=8)
            self.assertEqual(emb.embed(["a"]).shape[0], 1)
            self.assertEqual(CALLS["headers"][-1].get("api-key"), "pat-xyz")
        finally:
            FakeAPI.require = None
        # 팩토리가 settings 의 헤더 설정과 임베딩 전용 URL 을 반영
        s = Settings(llm_provider="openai", llm_model="test-model", openai_base_url=self.base, openai_api_key_header="x-api-key",
                     openai_extra_headers={"X-A": "1"}, embed_provider="openai", openai_embed_base_url=self.base + "/emb", openai_embed_model="embed-model", llm_timeout=33)
        l2 = make_llm(s)
        self.assertEqual(l2.key_header, "x-api-key")
        self.assertEqual(l2.timeout, 33)
        self.assertEqual(make_embedder(s).base_url, self.base + "/emb")
        # 키 alias: LLM_API_KEY
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ["LLM_API_KEY"] = "alias-key"
        try:
            self.assertEqual(openai_api_key(), "alias-key")
            self.assertEqual(auth_headers("k", "authorization")["authorization"], "Bearer k")
            self.assertEqual(auth_headers("k", "x-api-key")["x-api-key"], "k")
        finally:
            os.environ.pop("LLM_API_KEY", None)
        # Anthropic-compatible 게이트웨이: base_url + Bearer PAT(ANTHROPIC_AUTH_TOKEN) / x-api-key
        root = self.base[:-3]
        FakeAPI.require = ("authorization", "Bearer apat")
        saved = {k: os.environ.pop(k, None) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        try:
            os.environ["ANTHROPIC_AUTH_TOKEN"] = "apat"
            a = AnthropicHTTPLLM("test-model", fallbacks=False, base_url=root)
            self.assertTrue(a.available)
            self.assertTrue(a.ping()["ok"], a.ping())
            r = a.complete("s", "hello")
            self.assertTrue(r["text"].startswith("anthropic-echo"))
            self.assertEqual(r["usage"]["input_tokens"], 7)
            sa = Settings(llm_provider="anthropic", llm_model="test-model", anthropic_base_url=root, llm_fallbacks=False)
            fa = make_llm(sa, "answer")
            self.assertEqual(fa.name, "anthropic")
            self.assertEqual(getattr(fa, "base_url", root).rstrip("/"), root)
        finally:
            FakeAPI.require = None
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_rerank_model_override_routes_to_role(self):
        """rerank_model=… 는 역할 rerank 의 LLM 모델 단축키, rerank API 모델은 rerank_api_model (구 config 의 rerank_model 은 자동 이관)."""
        from llmwiki.config import apply_overrides
        s = Settings()
        apply_overrides(s, {"rerank_model": "llama3.1", "rerank_api_model": "BAAI/bge-reranker-v2-m3", "rerank_url": "http://gw/v1/rerank",
                            "openai_extra_headers": '{"X-T": "1"}'})
        self.assertEqual(s.llm_roles["rerank"]["model"], "llama3.1")
        self.assertEqual(s.rerank_api_model, "BAAI/bge-reranker-v2-m3")
        self.assertEqual(s.openai_extra_headers, {"X-T": "1"})
        old = Settings.from_dict({"rerank_model": "bge-old"})
        self.assertEqual(old.rerank_api_model, "bge-old")
        self.assertNotIn("rerank", old.llm_roles)

    def test_headless_resolves_shell_shim(self):
        """Windows 의 npm/bun 설치 CLI 는 .cmd 셸 — PATH 에서 찾아 절대 경로로 실행해야 한다 ({python} 치환 포함)."""
        import stat
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        if os.name == "nt":
            exe = os.path.join(bindir, "fakeagent.cmd")
            with open(exe, "w", encoding="utf-8") as f:
                f.write('@echo off\r\necho {"type":"text","part":{"text":"shim-ok %1"}}\r\n')
        else:
            exe = os.path.join(bindir, "fakeagent")
            with open(exe, "w", encoding="utf-8") as f:
                f.write('#!/bin/sh\necho "{\\"type\\":\\"text\\",\\"part\\":{\\"text\\":\\"shim-ok $1\\"}}"\n')
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        agents = hl.load_agents()
        agents["fake"] = {"command": ["fakeagent", "{model}"], "prompt_mode": "stdin", "output": "ndjson", "text_paths": ["part.text"], "model": "m1", "timeout_s": 30}
        hl.save_agents(agents)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = bindir + os.pathsep + old_path
        try:
            llm = hl.HeadlessAgentLLM("fake", "")
            self.assertTrue(llm.available)
            self.assertTrue(llm.ping()["ok"])
            r = llm.complete("s", "u")
            self.assertIn("shim-ok", r["text"])
            self.assertTrue(llm.live_test()["ok"])
            # {python} 치환: mock 에이전트 명령이 절대 경로가 아니어도 실행된다
            self.assertEqual(hl.load_agents()["mock"]["command"][0], "{python}")
            self.assertTrue(hl.HeadlessAgentLLM("mock", "").complete("TASK=answer", "q [C1]")["text"])
        finally:
            os.environ["PATH"] = old_path

    def test_float16_store(self):
        s = self._settings(llm_provider="mock", embed_provider="hash", embed_dim=256, embed_store_dtype="float16")
        p = Pipeline(s)
        try:
            p.build(full=True)
            row = p.store.conn.execute("SELECT dim, LENGTH(vec) n FROM embeddings LIMIT 1").fetchone()
            self.assertEqual(row["n"], row["dim"] * 2)
            ids, mat = p.store.vector_matrix("hash")
            self.assertEqual(mat.dtype, np.float16)
            r, t = p.query("RX DMA underrun", log=False)
            self.assertTrue(r["hits"])
            self.assertGreater({c["name"]: c for c in t["children"]}["vector_search"]["meta"]["hits"], 0)
            # float32 로 되돌리면 혼재 없이 재저장
            p.s.embed_store_dtype = "float32"
            p.build(full=True)
            row2 = p.store.conn.execute("SELECT dim, LENGTH(vec) n FROM embeddings LIMIT 1").fetchone()
            self.assertEqual(row2["n"], row2["dim"] * 4)
        finally:
            p.store.close()


if __name__ == "__main__":
    unittest.main()
