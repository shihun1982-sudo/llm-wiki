# -*- coding: utf-8 -*-
"""다른 RAG 연동(2026-09-15): 외부 소스 전송 3종(stdio/http/rest) · 외부 RAG 검색 채널 융합(external_rag) · 페더레이션(mcp_federation) · 플러그인 도구."""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.test_features_0914 import _Base, _st  # noqa: E402
from llmwiki import mcp_client as mc  # noqa: E402
from llmwiki import mcp as M  # noqa: E402
from llmwiki import auth as A  # noqa: E402


def _mock_stdio_cfg(**over):
    cfg = json.loads(json.dumps(mc.DEFAULT_SOURCES["mock"]))
    cfg["enabled"] = True
    cfg.update(over)
    return cfg


class ExternalRagChannelTest(_Base):
    def test_stdio_source_retrieve_and_fused_channel(self):
        srcs = mc.load_sources()
        srcs["mock"] = _mock_stdio_cfg()
        mc.save_sources(srcs)
        try:
            # retrieve 단독
            rows = mc.retrieve(self.s, "TX 전력 제어 PA gain 테이블", 3)
            self.assertTrue(rows and not any(r.get("error") for r in rows), rows)
            self.assertTrue(rows[0]["chunk_id"].startswith("ext:mock:"))
            self.assertEqual(rows[0]["doc_type"], "issue")
            self.assertGreater(rows[0]["score"], 0)
            self.assertTrue(str(rows[0]["url"]).startswith("mock://"))
            # 채널 융합: external_rag 토글. 외부 채널은 리스트 1개라 RRF 로는 후보 밖으로 밀리지만 external_rag_inject 가 리랭크 후보에 보장 주입하고
            # 로컬 리랭커(커버리지)가 최종 순위를 정한다 (mock LLM 리랭크는 순서를 보존하므로 여기선 끔)
            self.p.s.toggles.external_rag = True
            self.p.s.toggles.rerank_llm = False
            r, tr = self.p.query("TX 전력 제어 PA gain 테이블 인덱스 오류", log=False)
            st = _st(tr)
            self.assertTrue(st["external_rag"]["enabled"])
            self.assertGreaterEqual(st["external_rag"]["meta"]["results"], 1)
            self.assertIn("ext_mock", st["rrf_fuse"]["meta"]["sources"])
            self.assertIn("ext:mock:ISSUE-9001", st["external_inject"]["meta"]["moved"])
            self.assertIn("ext:mock:ISSUE-9001", st["rerank_local"]["debug"]["before"])
            ext = [h for h in r["hits"] if h["doc_id"].startswith("ext:mock:")]
            self.assertTrue(ext, [h["doc_id"] for h in r["hits"]])
            self.assertTrue(any(w.startswith("ext_mock") for w in ext[0]["why"]), ext[0]["why"])
            self.assertIn("ext_inject", ext[0]["why"])
            self.assertEqual(ext[0]["external"]["source"], "mock")
            self.assertTrue(any(h.get("in_context") for h in ext))
            # 순수 RRF 경쟁 (inject=0) 이면 후보에 못 들어가는 것이 정상 동작
            from llmwiki import tuning as tn
            tn.T.set("external_rag_inject", 0)
            try:
                r0, tr0 = self.p.query("TX 전력 제어 PA gain 테이블 인덱스 오류", log=False)
                self.assertNotIn("external_inject", _st(tr0))
            finally:
                tn.T.reset("external_rag_inject")
            # 토글 off → 채널 없음, 결과에 ext 없음
            self.p.s.toggles.external_rag = False
            r2, tr2 = self.p.query("TX 전력 제어 PA gain 테이블 인덱스 오류", log=False)
            self.assertFalse(any(h["doc_id"].startswith("ext:") for h in r2["hits"]))
            self.assertFalse(_st(tr2)["external_rag"]["enabled"])
        finally:
            mc.close_all()

    def test_source_error_does_not_break_query(self):
        srcs = mc.load_sources()
        srcs["broken"] = {"enabled": True, "transport": "rest", "base_url": "http://127.0.0.1:1", "timeout_s": 2,
                          "retrieve": [{"tool": "/search", "args": {"query": "{query}"}, "result_path": "results"}]}
        mc.save_sources(srcs)
        self.p.s.toggles.external_rag = True
        r, tr = self.p.query("ISSUE-2001 의 원인", log=False)
        meta = _st(tr)["external_rag"]["meta"]
        self.assertTrue(meta.get("errors"))
        self.assertEqual(meta["errors"][0]["source"], "broken")
        self.assertTrue(r["hits"])          # 내부 채널 결과는 그대로

    def test_weight_and_fallback_when(self):
        srcs = mc.load_sources()
        cfg = _mock_stdio_cfg()
        cfg["retrieve"][0]["when"] = "fallback"
        srcs["mock"] = cfg
        mc.save_sources(srcs)
        try:
            self.assertEqual(mc.retrieve(self.s, "PA gain", 3), [])                       # when=fallback 은 기본 호출에서 제외
            self.assertTrue(mc.retrieve(self.s, "PA gain", 3, include_fallback=True))
            summ = mc.source_summary("mock", cfg)
            self.assertEqual(summ["retrieve"][0]["when"], "fallback")
            self.assertEqual(summ["transport"], "stdio")
        finally:
            mc.close_all()


class RestSourceTest(_Base):
    def setUp(self):
        _Base.setUp(self)
        self.httpd = None

    def _rest(self):
        import subprocess
        # mock REST 서버를 별도 프로세스로 (문서의 --mock-rest 와 동일 경로)
        self.proc = subprocess.Popen([sys.executable, "-m", "llmwiki.mcp_client", "--mock-rest", "0"], cwd=ROOT, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        line = self.proc.stdout.readline()
        port = int(line.rsplit(":", 1)[1].split()[0])
        return "http://127.0.0.1:%d" % port

    def tearDown(self):
        try:
            self.proc.terminate()
        except Exception:
            pass
        _Base.tearDown(self)

    def test_rest_transport_retrieve_and_expose(self):
        base = self._rest()
        srcs = mc.load_sources()
        srcs["kb"] = {"enabled": True, "transport": "rest", "base_url": base, "method": "POST", "timeout_s": 10, "ping": "/health",
                      "retrieve": [{"tool": "/search", "args": {"query": "{query}", "k": "{k}"}, "result_path": "results", "id_field": "id", "title_field": "title",
                                    "text_field": "content", "score_field": "score", "url_field": "url", "doc_type": "kb", "weight": 1.5}],
                      "tools": [{"name": "search", "description": "kb search", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}}],
                      "expose": True}
        mc.save_sources(srcs)
        ts = mc.test_sources(self.s, ["kb"])
        self.assertTrue(ts[0]["ok"], ts)
        self.assertEqual(ts[0]["transport"], "rest")
        rows = mc.retrieve(self.s, "off-by-one", 5)
        self.assertEqual([r["id"] for r in rows], ["KB-2"])
        self.assertEqual(rows[0]["weight"], 1.5)
        self.assertEqual(rows[0]["doc_type"], "kb")
        # 융합 가중치 = channel_w_external × weight, 보장 주입 → 로컬 리랭크
        self.p.s.toggles.external_rag = True
        self.p.s.toggles.rerank_llm = False
        r, tr = self.p.query("PA gain 테이블 off-by-one 인덱스", log=False)
        self.assertIn("ext_kb", _st(tr)["rrf_fuse"]["meta"]["weights"])
        self.assertAlmostEqual(_st(tr)["rrf_fuse"]["meta"]["weights"]["ext_kb"], 1.5, places=3)
        self.assertTrue(any(h["doc_id"] == "ext:kb:KB-2" for h in r["hits"]), [h["doc_id"] for h in r["hits"]])
        # 페더레이션: rest 소스는 tools 선언으로 노출
        self.p.s.toggles.mcp_federation = True
        names = {t["name"] for t in M.list_tools(self.p)}
        self.assertIn("kb__search", names)
        res = M.call_tool(self.p, "kb__search", {"query": "AGC", "k": 2})
        self.assertFalse(res.get("isError"), res)
        self.assertEqual(res["structuredContent"]["results"][0]["id"], "KB-1")
        # GET 방식 호출도 지원
        c = mc.RestClient("kb", srcs["kb"])
        self.assertEqual(c.call_tool("/health", {}, method="GET"), {"ok": True})
        mc.close_all()


class FederationAndPluginTest(_Base):
    def test_stdio_federation_and_tool_naming(self):
        srcs = mc.load_sources()
        srcs["mock"] = _mock_stdio_cfg(expose=["search"])
        mc.save_sources(srcs)
        try:
            self.p.s.toggles.mcp_federation = False
            self.assertNotIn("mock__search", {t["name"] for t in M.list_tools(self.p)})
            self.p.s.toggles.mcp_federation = True
            tools = M.list_tools(self.p)
            names = {t["name"] for t in tools}
            self.assertIn("mock__search", names)
            self.assertNotIn("mock__list_issues", names)          # expose 목록 밖
            spec = next(t for t in tools if t["name"] == "mock__search")
            self.assertTrue(spec["description"].startswith("[mock]"))
            self.assertIn("q", spec["inputSchema"]["properties"])
            # 호출 중계 (JSON-RPC 경로 전체)
            r = M.handle(self.p, {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "mock__search", "arguments": {"q": "AGC", "limit": 2}}})
            self.assertFalse(r["result"].get("isError"), r)
            self.assertEqual(r["result"]["structuredContent"]["items"][0]["id"], "ISSUE-9002")
            r = M.handle(self.p, {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "mock__list_issues", "arguments": {}}})
            self.assertTrue(r["result"].get("isError"))
            r = M.handle(self.p, {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "nope__x", "arguments": {}}})
            self.assertTrue(r["result"].get("isError"))
            # wiki_sources / wiki_external_search built-in
            r = M.call_tool(self.p, "wiki_sources", {"check": True})
            sc = r["structuredContent"]
            self.assertTrue(sc["mcp_federation"])
            mock = next(x for x in sc["sources"] if x["name"] == "mock")
            self.assertTrue(mock["status"]["ok"])
            self.assertEqual(mock["expose"], ["search"])
            r = M.call_tool(self.p, "wiki_external_search", {"query": "PA gain", "source": "mock", "k": 2})
            self.assertEqual(r["structuredContent"]["results"][0]["id"], "ISSUE-9001")
        finally:
            mc.close_all()

    def test_plugin_tools_dir(self):
        d = os.path.join(self.tmp, "plugins")
        os.makedirs(d)
        with open(os.path.join(d, "hello.py"), "w", encoding="utf-8") as f:
            f.write("def register(add_tool):\n"
                    "    add_tool({'name': 'hello', 'description': 'hi', 'inputSchema': {'type': 'object', 'properties': {'who': {'type': 'string'}}}},\n"
                    "             lambda pipe, args: 'hello ' + str(args.get('who')) + ' docs=' + str(pipe.store.stats()['docs']))\n"
                    "    add_tool({'name': 'stats', 'description': 'd'}, lambda pipe, args: {'docs': pipe.store.stats()['docs']})\n")
        with open(os.path.join(d, "_skipped.py"), "w", encoding="utf-8") as f:
            f.write("raise RuntimeError('must not load')\n")
        with open(os.path.join(d, "bad.py"), "w", encoding="utf-8") as f:
            f.write("def register(add_tool):\n    add_tool({'name': 'wiki_query'}, lambda p, a: 1)\n")   # built-in 이름 충돌 → 오류로 기록
        self.p.s.mcp_plugins_dir = d
        info = M.load_plugins(self.p.s, force=True)
        self.assertEqual(sorted(info["tools"]), ["hello", "stats"])
        self.assertEqual(info["errors"][0]["file"], "bad.py")
        names = {t["name"] for t in M.list_tools(self.p)}
        self.assertIn("hello", names)
        r = M.handle(self.p, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "hello", "arguments": {"who": "kim"}}})
        self.assertTrue(r["result"]["content"][0]["text"].startswith("hello kim docs="))
        r = M.call_tool(self.p, "stats", {})
        self.assertEqual(r["structuredContent"]["docs"], self.p.store.stats()["docs"])
        # 파일 수정 → 자동 재적재
        with open(os.path.join(d, "hello.py"), "a", encoding="utf-8") as f:
            f.write("\n")
        os.utime(os.path.join(d, "hello.py"), None)
        # 폴더 재검사는 plugin_rescan_s(server.json mcp, 기본 5초)마다 한 번만 한다 — tools/list 마다 listdir 를 돌지 않기 위한 것.
        # 파일을 쓴 직후 5초 안에 부르면 스냅샷을 그대로 돌려주므로 "주기가 지났다" 를 흉내 내어 시그니처 변화 감지 경로를 그대로 검증한다
        # (force=True 로 우회하면 그 경로를 잃는다).
        M._PLUGIN_STATE["checked"] = 0
        M.load_plugins(self.p.s)
        self.assertIn("hello", {t["name"] for t in M.list_tools(self.p)})
        # 저장소 예시 파일은 밑줄이라 로드되지 않고, 이름을 바꾸면 로드된다
        ex = os.path.join(ROOT, "plugins", "mcp_tools", "_example_echo.py")
        self.assertTrue(os.path.exists(ex))
        with open(os.path.join(d, "example_echo.py"), "w", encoding="utf-8") as f, open(ex, encoding="utf-8") as src:
            f.write(src.read())
        M._PLUGIN_STATE["checked"] = 0      # 위와 같이 plugin_rescan_s 주기 경과를 흉내
        info = M.load_plugins(self.p.s)
        self.assertIn("echo", info["tools"])
        self.assertEqual(M.call_tool(self.p, "echo", {"text": "abc"})["structuredContent"]["chars"], 3)


class HttpPeerSourceTest(_Base):
    """다른 llmwiki(같은 소프트웨어)를 http 전송 소스로 붙인다 — 우리 자신을 원격으로 띄워 검증."""

    def setUp(self):
        _Base.setUp(self)
        from llmwiki.web import server as ws
        cfg = json.loads(json.dumps(A.DEFAULT_SECURITY))
        cfg["mode"], cfg["anonymous_role"] = "on", ""
        A.save_security(cfg)
        auth = A.Auth(self.s, host="0.0.0.0")
        self.key = auth.add_api_key("peer-client", "viewer")
        ws.Handler.pipe, ws.Handler.auth, ws.Handler.host = self.p, auth, "0.0.0.0"
        self.ws = ws
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ws.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/mcp" % self.httpd.server_address[1]

    def tearDown(self):
        try:
            self.httpd.shutdown()
            self.ws.Handler.auth = None
            self.ws.Handler.host = "127.0.0.1"
        except Exception:
            pass
        mc.close_all()
        _Base.tearDown(self)

    def test_http_transport_source(self):
        os.environ["PEER_TOKEN_TEST"] = self.key["token"]
        try:
            srcs = mc.load_sources()
            srcs["peer"] = {"enabled": True, "transport": "http", "url": self.url, "token_env": "PEER_TOKEN_TEST", "timeout_s": 30,
                            "retrieve": [{"tool": "wiki_search", "args": {"channel": "fts", "query": "{query}", "k": "{k}"}, "result_path": "", "id_field": "chunk_id",
                                          "title_field": "chunk_id", "text_field": "snippet", "score_field": "score", "doc_type": "peer", "weight": 0.8}],
                            "expose": ["wiki_status", "wiki_doc"]}
            mc.save_sources(srcs)
            ts = mc.test_sources(self.s, ["peer"])
            self.assertTrue(ts[0]["ok"], ts)
            self.assertIn("wiki_query", ts[0]["tools"])
            rows = mc.retrieve(self.s, "ISSUE-2001 FIFO", 3)
            self.assertTrue(rows and not rows[0].get("error"), rows)
            self.assertTrue(rows[0]["chunk_id"].startswith("ext:peer:"))
            # 잘못된 토큰 → 오류 항목 (질의는 계속)
            os.environ["PEER_TOKEN_TEST"] = "lwk_bad_x"
            mc.close_all()
            rows = mc.retrieve(self.s, "ISSUE-2001", 3)
            self.assertTrue(rows[0].get("error") and "401" in rows[0]["error"], rows)
            os.environ["PEER_TOKEN_TEST"] = self.key["token"]
            # 페더레이션으로 peer 의 wiki_status 를 우리 도구로. (peer 는 사실 우리 자신이므로 재귀 방지 헤더가 없으면 무한 루프 → 타임아웃이 난다)
            self.p.s.toggles.mcp_federation = True
            M._FED_CACHE.clear()
            names = {t["name"] for t in M.list_tools(self.p)}
            self.assertIn("peer__wiki_status", names, M._FED_CACHE)
            self.assertNotIn("peer__wiki_query", names)
            r = M.call_tool(self.p, "peer__wiki_status", {})
            self.assertFalse(r.get("isError"), r)
            self.assertIn("stats", r["content"][0]["text"])
            # 원격(페더레이션 하위)에서 본 tools/list 에는 페더레이션 도구가 없다 (헤더로 판별)
            c = mc.HttpMCPClient("peer", srcs["peer"]).start()
            self.assertFalse(any("__" in t["name"] for t in c.tools))
            r = c.request("tools/call", {"name": "peer__wiki_status", "arguments": {}})
            self.assertTrue(r["result"].get("isError"))
        finally:
            os.environ.pop("PEER_TOKEN_TEST", None)


if __name__ == "__main__":
    unittest.main()
