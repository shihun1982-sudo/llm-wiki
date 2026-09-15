# -*- coding: utf-8 -*-
"""상세 분석 모드(analysis_mode, 2026-09-15): 질의 → logs/analysis/req_<id>.md 리포트 · analyze CLI · /api/analysis · MCP wiki_analysis."""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.test_features_0914 import _Base  # noqa: E402
from llmwiki import analysis as AN  # noqa: E402
from llmwiki import mcp as M  # noqa: E402
from llmwiki.cli import run_captured  # noqa: E402


class AnalysisModeTest(_Base):
    def test_toggle_produces_report(self):
        self.p.s.toggles.analysis_mode = True
        r, tr = self.p.query("ISSUE-2001 의 원인과 수정 CL 은?", log=True)
        self.assertEqual(tr["debug_level"], 2)                       # 토글이 debug_level 을 2 로 올린다
        an = r.get("analysis") or {}
        self.assertTrue(an.get("md"), an)
        self.assertTrue(os.path.exists(an["md"]) and os.path.exists(an["json"]))
        self.assertTrue(an["md"].startswith(os.path.join(self.tmp, "logs_dir")))   # LLMWIKI_LOGS_DIR_PATH 아래 analysis/
        self.assertEqual(an["detail_level"], 2)
        self.assertIn("quality", an["top"]); self.assertIn("speed", an["top"]); self.assertIn("tokens", an["top"])
        md = open(an["md"], encoding="utf-8").read()
        for sec in ("## 0. 요약", "## 1. 설정 스냅샷", "## 2. 단계 타임라인", "## 3. 검색 상세", "### 3.3 최종 근거", "## 4. 답변", "## 5. 품질 렌즈", "## 6. 속도 렌즈", "## 7. 토큰 렌즈",
                    "## 8. 자동 포렌식", "## 9. LLM 에게", "## 부록 A", "## 부록 B"):
            self.assertIn(sec, md, sec)
        self.assertIn("ISSUE-2001", md)
        self.assertIn("rrf_fuse", md)
        self.assertIn("answer_llm", md)
        self.assertIn("조절점", md)
        js = json.load(open(an["json"], encoding="utf-8"))
        self.assertEqual(js["request_id"], r["request_id"])
        self.assertTrue(js["samples"])                                 # debug_level 2 → 프롬프트 샘플
        self.assertTrue(any(t["stage"] == "rrf_fuse" for t in js["timeline"]))
        self.assertTrue(js["retrieval"]["final"] and js["retrieval"]["final"][0]["chunk_id"])
        self.assertTrue(js["retrieval"]["fusion"]["order"])            # debug 캡처
        self.assertTrue(js["llm_calls"])
        # 토글 off → analysis 없음, debug_level 은 설정값
        self.p.s.toggles.analysis_mode = False
        r2, tr2 = self.p.query("ISSUE-2001 의 원인과 수정 CL 은?", log=True)
        self.assertNotIn("analysis", r2)
        self.assertEqual(tr2["debug_level"], self.p.s.debug_level)

    def test_analyze_stored_request_and_focus(self):
        r, _ = self.p.query("RX DMA underrun 원인", log=True)
        out = AN.analyze(self.p, r["request_id"], focus="speed")
        self.assertFalse(out.get("error"))
        self.assertIn("## 6. 속도 렌즈", out["markdown"])
        self.assertNotIn("## 5. 품질 렌즈", out["markdown"])
        self.assertEqual(out["summary"]["request_id"], r["request_id"])
        self.assertLess(out["summary"]["detail_level"], 2)            # 토글 없이 실행한 요청은 요약 수준
        self.assertIn("요약 수준", out["markdown"])
        # last / 없는 id
        self.assertEqual(AN.analyze(self.p, None)["summary"]["request_id"], r["request_id"])
        self.assertTrue(AN.analyze(self.p, 999999).get("error"))
        # 캐시 결과는 안내
        self.p.s.toggles.query_cache = True
        self.p.query("RX DMA underrun 원인", log=True)
        rc, _ = self.p.query("RX DMA underrun 원인", log=True)
        self.assertTrue(rc.get("cached"))
        outc = AN.analyze(self.p, rc["request_id"])
        self.assertIn("캐시", outc["markdown"])
        self.p.s.toggles.query_cache = False

    def test_lenses_on_weak_query(self):
        # 코퍼스에 없는 주제 → insufficient/weak → 품질 렌즈에 error/warn 소견 + 조절점
        self.p.s.toggles.analysis_mode = True
        r, _ = self.p.query("양자 컴퓨터 큐비트 오류 정정 코드", log=True)
        an = r["analysis"]
        q = an["top"]["quality"]
        self.assertTrue(any(f["severity"] in ("error", "warn") for f in q), q)
        self.assertTrue(any(f.get("knobs") for f in q))
        rep = json.load(open(an["json"], encoding="utf-8"))
        sp = rep["lenses"]["speed"]
        self.assertTrue(sp and "총" in sp[0]["title"])
        tk = rep["lenses"]["tokens"]
        self.assertTrue(tk and "LLM 호출" in tk[0]["title"])
        # 조절점의 현재값이 붙는다 (전체 리포트의 렌즈 항목)
        f = next((f for f in rep["lenses"]["quality"] if f.get("current")), None)
        self.assertIsNotNone(f, rep["lenses"]["quality"])
        self.assertIn("fallback_loop", f["current"])

    def test_cli_analyze_and_query_flag(self):
        r = run_captured(["query", "CL-55302 는 어떤 이슈를 수정했나?", "--analyze", "--focus", "tokens"], self.p.s, self.p)
        self.assertEqual(r["code"], 0, r)
        self.assertIn("분석 리포트", r["output"])
        self.assertIn("tokens", r["output"])
        r = run_captured(["analyze", "last"], self.p.s, self.p)
        self.assertEqual(r["code"], 0, r)
        self.assertIn("req_", r["output"])
        r = run_captured(["analyze", "last", "--print", "--focus", "quality"], self.p.s, self.p)
        self.assertIn("## 5. 품질 렌즈", r["output"])
        out = os.path.join(self.tmp, "a.md")
        r = run_captured(["analyze", "last", "--out", out, "--json"], self.p.s, self.p)
        self.assertTrue(os.path.exists(out))
        self.assertIn('"summary"', r["output"])
        r = run_captured(["analyze", "999999"], self.p.s, self.p)
        self.assertEqual(r["code"], 1)
        # CLI 게이트: read 등급 (viewer 도 가능)
        from llmwiki.auth import classify_cli
        self.assertEqual(classify_cli(["analyze", "last"])[0], "read")

    def test_mcp_tool(self):
        r, _ = self.p.query("ISR 안에서 blocking 대기를 써도 되나?", log=True)
        res = M.call_tool(self.p, "wiki_analysis", {"request_id": r["request_id"], "focus": "quality"})
        self.assertFalse(res.get("isError"), res)
        self.assertIn("## 5. 품질 렌즈", res["content"][0]["text"])
        self.assertEqual(res["structuredContent"]["request_id"], r["request_id"])
        self.assertTrue(res["structuredContent"]["md"])
        self.assertIn("wiki_analysis", {t["name"] for t in M.list_tools(self.p)})


class AnalysisWebTest(_Base):
    def setUp(self):
        _Base.setUp(self)
        from llmwiki.web import server as ws
        ws.Handler.pipe = self.p
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ws.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        _Base.tearDown(self)

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=120) as r:
            return r.status, r.headers, r.read()

    def test_api_analysis(self):
        body = json.dumps({"q": "ISSUE-2001 의 원인", "overrides": {"analysis_mode": True}, "log": True}).encode("utf-8")
        req = urllib.request.Request(self.base + "/api/query", data=body, headers={"Content-Type": "application/json", "X-Requested-With": "llmwiki"})
        with urllib.request.urlopen(req, timeout=300) as r:
            j = json.loads(r.read().decode("utf-8"))
        res = j["result"]
        self.assertTrue(res.get("analysis", {}).get("md"), res.get("analysis"))
        rid = res["request_id"]
        st, h, b = self._get("/api/analysis?request_id=%d&focus=speed" % rid)
        j2 = json.loads(b.decode("utf-8"))
        self.assertEqual(j2["summary"]["request_id"], rid)
        self.assertIn("## 6. 속도 렌즈", j2["markdown"])
        self.assertIsNone(j2["report"])
        st, h, b = self._get("/api/analysis?request_id=%d&format=md&download=1" % rid)
        self.assertTrue(h.get("Content-Type", "").startswith("text/markdown"))
        self.assertIn("attachment", h.get("Content-Disposition", ""))
        self.assertIn("# 질의 상세 분석 리포트", b.decode("utf-8"))
        st, h, b = self._get("/api/analysis?request_id=%d&full=1" % rid)
        self.assertTrue(json.loads(b.decode("utf-8"))["report"]["timeline"])
        try:
            self._get("/api/analysis?request_id=999999")
            self.fail("expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


if __name__ == "__main__":
    unittest.main()
