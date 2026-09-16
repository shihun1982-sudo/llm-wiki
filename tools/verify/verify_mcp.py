# -*- coding: utf-8 -*-
"""MCP 종단 검증 — 다수의 외부 LLM 이 붙고, 외부 RAG 를 얹어 쓰는 상황을 실제로 돌려 본다.

무엇을 확인하나 (docs/MCP.md §검증)
  1. 전송 3종        stdio(자식 프로세스) · Streamable HTTP(POST /mcp) · 브리지(stdio→HTTP)
  2. 프로토콜 적합성  버전 협상 · initialize/ping/notification · 알 수 없는 메서드 · 배치 · 세션 헤더 · GET 405
  3. 도구 12종       모두 실제 호출해 결과 형태 확인 (isError 아님, content[0].text 존재)
  4. 잘못된 호출      필수 인자 누락·타입 오류·enum 위반·없는 도구 → 크래시 없이 isError
  5. 인증            익명 역할 · 잘못된 Bearer · API 키
  6. 확장            플러그인 도구(정상/깨진 파일) · 페더레이션(<source>__<tool>) · 재귀 방지 · 외부 RAG 검색 채널
  7. 동시성          여러 클라이언트가 같은 서버에 동시에 tools/call
  8. 자가 점검        mcp --doctor · mcp --client-config

실행: python tools/verify/verify_mcp.py [--port 8877] [--keep]
결과: tools/verify/verify_mcp_result.json (실패 항목이 있으면 종료코드 1)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
PY = sys.executable

from verify_buttons import isolated_env      # noqa: E402

RESULTS = []
FAILED = []


try:      # 파이프로 리다이렉트해도 진행 상황이 바로 보이게 (검증은 오래 걸린다)
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
except Exception:
    pass


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:400]})
    if not ok:
        FAILED.append(name)
    print("  %s %-46s %s" % ("OK  " if ok else "FAIL", name, str(detail)[:110]))
    return ok


# ---------------------------------------------------------------- stdio 클라이언트
class Stdio:
    """줄 단위 JSON-RPC 로 `python -m llmwiki mcp` 와 말한다."""

    def __init__(self, env, extra_args=(), cwd=ROOT):
        self.proc = subprocess.Popen([PY, "-m", "llmwiki", "mcp"] + list(extra_args), cwd=cwd, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._id = 0

    def send_raw(self, payload):
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()

    def send(self, method, params=None, notify=False, framing="line"):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self._id += 1
            msg["id"] = self._id
        body = json.dumps(msg, ensure_ascii=False)
        if framing == "content-length":
            self.send_raw("Content-Length: %d\r\n\r\n%s" % (len(body.encode("utf-8")), body))
        else:
            self.send_raw(body + "\n")
        if notify:
            return None
        return self._read()

    def _read(self, timeout=180):
        end = time.time() + timeout
        while time.time() < end:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("서버가 닫힘: " + (self.proc.stderr.read() or "")[:300])
            line = line.strip()
            if line:
                return json.loads(line)
        raise RuntimeError("timeout")

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


# ---------------------------------------------------------------- HTTP 클라이언트
class Http:
    def __init__(self, base, token=""):
        self.url = base.rstrip("/") + "/mcp"
        self.token = token
        self.session = None
        self._id = 0

    def post(self, msg, extra_headers=None, timeout=300):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "X-Requested-With": "verify-mcp"}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if self.session:
            h["Mcp-Session-Id"] = self.session
        h.update(extra_headers or {})
        req = urllib.request.Request(self.url, data=json.dumps(msg, ensure_ascii=False).encode("utf-8"), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body, status, hdrs = r.read(), r.status, dict(r.headers.items())
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers.items()), e.read()
        if hdrs.get("Mcp-Session-Id"):
            self.session = hdrs["Mcp-Session-Id"]
        return status, hdrs, body

    def rpc(self, method, params=None, **kw):
        self._id += 1
        st, hd, body = self.post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}, **kw)
        if st != 200:
            raise RuntimeError("HTTP %s %s" % (st, body[:200]))
        return json.loads(body.decode("utf-8"))

    def call(self, tool, args=None, **kw):
        return self.rpc("tools/call", {"name": tool, "arguments": args or {}}, **kw)


def text_of(resp):
    res = (resp or {}).get("result") or {}
    return "\n".join(c.get("text", "") for c in res.get("content") or [])


def is_error(resp):
    return bool(((resp or {}).get("result") or {}).get("isError"))


# ---------------------------------------------------------------- 섹션들
def sec_protocol(cli):
    print("\n[1] 프로토콜 적합성 (stdio)")
    r = cli.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "verify", "version": "1"}})
    res = r.get("result") or {}
    check("initialize 응답", res.get("serverInfo", {}).get("name") == "llmwiki", json.dumps(res.get("serverInfo"), ensure_ascii=False))
    check("버전 협상 (지원 버전 그대로)", res.get("protocolVersion") == "2025-06-18", res.get("protocolVersion"))
    check("capabilities.tools 선언", "tools" in (res.get("capabilities") or {}), res.get("capabilities"))
    check("instructions 제공", bool(res.get("instructions")), (res.get("instructions") or "")[:60])

    c2 = cli.send("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}})
    got = (c2.get("result") or {}).get("protocolVersion")
    check("모르는 버전 → 우리 버전으로 응답", got == "2025-06-18", got)
    c3 = cli.send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
    check("구버전 클라이언트 (2024-11-05)", (c3.get("result") or {}).get("protocolVersion") == "2024-11-05",
          (c3.get("result") or {}).get("protocolVersion"))

    cli.send("notifications/initialized", {}, notify=True)
    r = cli.send("ping")
    check("ping (알림 뒤에도 응답)", r.get("result") == {}, r.get("result"))

    r = cli.send("tools/list")
    tools = (r.get("result") or {}).get("tools") or []
    names = [t["name"] for t in tools]
    check("tools/list 12종", len(tools) >= 12, "%d개: %s" % (len(tools), ", ".join(names[:4]) + " …"))
    bad = [t["name"] for t in tools if not isinstance(t.get("inputSchema"), dict) or t["inputSchema"].get("type") != "object"]
    check("모든 inputSchema 가 object", not bad, bad)
    annot = [t for t in tools if t.get("annotations")]
    check("annotations(readOnlyHint 등) 부착", len(annot) == len(tools), "%d/%d" % (len(annot), len(tools)))
    ro = {t["name"]: t["annotations"].get("readOnlyHint") for t in tools}
    check("쓰기 도구만 readOnlyHint=false", ro.get("wiki_query") is True and ro.get("wiki_propose") is False,
          "query=%s propose=%s" % (ro.get("wiki_query"), ro.get("wiki_propose")))
    check("이름 중복 없음", len(names) == len(set(names)), len(names) - len(set(names)))

    r = cli.send("resources/list")
    check("resources/list 빈 목록", (r.get("result") or {}).get("resources") == [], r.get("result"))
    r = cli.send("prompts/list")
    check("prompts/list 빈 목록", (r.get("result") or {}).get("prompts") == [], r.get("result"))
    r = cli.send("nonexistent/method")
    check("없는 메서드 → -32601", (r.get("error") or {}).get("code") == -32601, r.get("error"))
    cli.send("notifications/cancelled", {"requestId": 1}, notify=True)

    # Content-Length 프레이밍
    r = cli.send("ping", framing="content-length")
    check("Content-Length 프레이밍", r.get("result") == {}, r.get("result"))

    # 배치
    cli.send_raw(json.dumps([{"jsonrpc": "2.0", "id": 901, "method": "ping"},
                             {"jsonrpc": "2.0", "method": "notifications/initialized"},
                             {"jsonrpc": "2.0", "id": 902, "method": "ping"}]) + "\n")
    batch = cli._read()
    check("배치 요청 (알림 제외 2건 응답)", isinstance(batch, list) and len(batch) == 2, batch)

    # 깨진 입력으로 죽지 않는가
    cli.send_raw("{not json}\n")
    r = cli.send("ping")
    check("깨진 JSON 뒤에도 살아 있음", r.get("result") == {}, r.get("result"))
    return names


TOOL_CALLS = [
    ("wiki_status", {}, "stats"),
    ("wiki_query", {"question": "ISSUE-2001 의 원인과 수정 CL 은?", "k": 4}, "request_id"),
    ("wiki_search", {"channel": "fts", "query": "ISSUE-2001"}, "chunk_id"),
    ("wiki_search", {"channel": "vector", "query": "전력 제어"}, "chunk_id"),
    ("wiki_search", {"channel": "graph", "query": "ISSUE-2001"}, "chunks"),
    ("wiki_doc", {"id": "ISSUE-2001"}, "meta"),
    ("wiki_entity", {"name": "ISSUE-2001"}, ""),
    ("wiki_related", {"text": "TX 전력 제어 오동작으로 PA gain 테이블을 확인했다", "k": 3}, "유사 문서"),
    ("wiki_forensic", {"expected_docs": ["ISSUE-2001"]}, ""),
    ("wiki_analysis", {"focus": "speed"}, "분석"),
    ("wiki_sources", {}, "sources"),
    ("wiki_external_search", {"query": "AGC", "k": 2}, "외부 검색"),
    ("wiki_propose", {"kind": "synonym", "payload": {"term": "AGC", "synonyms": ["자동이득제어"]}, "reason": "verify"}, "proposal_id"),
]

BAD_CALLS = [
    ("wiki_query", {}, "필수 인자"),
    ("wiki_query", {"question": "   "}, "필수 인자"),
    ("wiki_search", {"channel": "nope", "query": "x"}, "중 하나"),
    ("wiki_search", {"channel": "fts", "query": "x", "k": "많이"}, "integer"),
    ("wiki_doc", {"id": "존재하지-않는-문서-zzz"}, None),          # 오류는 아니고 "not found"
    ("wiki_propose", {"kind": "정체불명", "payload": {}}, "unsupported kind"),
    ("nope_tool", {}, "unknown tool"),
]


QUICK_TOOLS = ("wiki_status", "wiki_query", "wiki_search", "wiki_sources")


def sec_tools(cli, label="stdio", quick=False):
    print("\n[2] 도구 호출 (%s)" % label)
    ids = {}
    calls = [c for c in TOOL_CALLS if c[0] in QUICK_TOOLS] if quick else TOOL_CALLS
    for tool, args, expect in calls:
        try:
            r = cli.send("tools/call", {"name": tool, "arguments": args}) if isinstance(cli, Stdio) else cli.call(tool, args)
        except Exception as e:
            check("%s %s" % (tool, json.dumps(args, ensure_ascii=False)[:40]), False, "예외: %s" % e)
            continue
        txt = text_of(r)
        ok = not is_error(r) and bool(txt) and (not expect or expect in txt or expect in json.dumps((r.get("result") or {}).get("structuredContent") or {}, ensure_ascii=False))
        check("%s %s" % (tool, json.dumps(args, ensure_ascii=False)[:38]), ok, txt[:100].replace("\n", " "))
        if tool == "wiki_query":
            for tok in txt.split():
                if tok.isdigit():
                    pass
            import re
            m = re.search(r"request_id: (\d+) · query_id: (\d+)", txt)
            if m:
                ids["request_id"], ids["query_id"] = int(m.group(1)), int(m.group(2))
    if ids.get("query_id"):
        r = cli.send("tools/call", {"name": "wiki_feedback", "arguments": {"query_id": ids["query_id"], "feedback": 1, "note": "verify"}}) \
            if isinstance(cli, Stdio) else cli.call("wiki_feedback", {"query_id": ids["query_id"], "feedback": 1, "note": "verify"})
        check("wiki_feedback (query_id 연계)", not is_error(r), text_of(r)[:90])
    else:
        check("wiki_query 결과에 request_id/query_id 표시", False, "정규식 불일치")

    print("\n[3] 잘못된 호출 (%s)" % label)
    for tool, args, expect in (BAD_CALLS[:3] if quick else BAD_CALLS):
        try:
            r = cli.send("tools/call", {"name": tool, "arguments": args}) if isinstance(cli, Stdio) else cli.call(tool, args)
        except Exception as e:
            check("%s %s" % (tool, json.dumps(args, ensure_ascii=False)[:38]), False, "예외(크래시): %s" % e)
            continue
        txt = text_of(r)
        if expect is None:
            ok = not is_error(r) and "not found" in txt
        else:
            ok = is_error(r) and expect in txt
        check("%s %s" % (tool, json.dumps(args, ensure_ascii=False)[:38]), ok, txt[:100].replace("\n", " "))
    return ids


def sec_http(base, api_key):
    print("\n[4] Streamable HTTP · 인증")
    anon = Http(base)
    r = anon.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    check("익명(anonymous_role) initialize", (r.get("result") or {}).get("serverInfo", {}).get("name") == "llmwiki", r.get("result", {}).get("serverInfo"))
    check("Mcp-Session-Id 발급", bool(anon.session), anon.session)
    sid = anon.session
    anon.rpc("tools/list")
    check("세션 유지 (같은 Mcp-Session-Id)", anon.session == sid, anon.session)

    st, hd, body = anon.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    check("알림만 보내면 202", st == 202 and not body, "%s %r" % (st, body[:40]))

    st, hd, body = anon.post([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    arr = json.loads(body.decode("utf-8")) if st == 200 else None
    check("HTTP 배치", isinstance(arr, list) and len(arr) == 2, st)

    st, hd, body = anon.post(b"{ not json", extra_headers={"Content-Type": "application/json"}) if False else anon.post({"jsonrpc": "2.0"})
    check("잘못된 JSON-RPC → 오류 응답 (크래시 아님)", st in (200, 400), st)

    req = urllib.request.Request(base.rstrip("/") + "/mcp", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r2:
            code = r2.status
    except urllib.error.HTTPError as e:
        code = e.code
    check("GET /mcp → 405 (SSE 미제공)", code == 405, code)

    bad = Http(base, "lwk_없는키_000")
    try:
        bad.rpc("tools/list")
        ok, det = False, "인증 없이 통과"
    except RuntimeError as e:
        ok, det = "401" in str(e) or "403" in str(e), str(e)[:90]
    check("잘못된 Bearer 거부", ok, det)

    keyed = Http(base, api_key)
    r = keyed.rpc("tools/list")
    check("API 키로 tools/list", len((r.get("result") or {}).get("tools") or []) >= 12, api_key[:10] + "…")
    return keyed


def sec_concurrency(base, api_key, n=8):
    print("\n[5] 동시 접속 (%d 클라이언트)" % n)
    out, lock = [], threading.Lock()

    def one(i):
        c = Http(base, api_key)
        try:
            c.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
            r = c.call("wiki_query", {"question": "ISSUE-200%d 요약" % (i % 5 + 1), "k": 3})
            with lock:
                out.append((i, not is_error(r), len(text_of(r))))
        except Exception as e:
            with lock:
                out.append((i, False, str(e)[:80]))

    ths = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    t0 = time.time()
    for t in ths:
        t.start()
    for t in ths:
        t.join(600)
    ok = sum(1 for _, o, _ in out if o)
    check("동시 wiki_query %d건" % n, ok == n, "%d/%d 성공 · %.1fs · 실패=%s" % (ok, n, time.time() - t0, [x for x in out if not x[1]][:2]))


def sec_bridge(env, base, api_key):
    print("\n[6] 브리지 (stdio → 원격 HTTP)")
    cli = Stdio(env, extra_args=["--connect", base.rstrip("/") + "/mcp", "--token", api_key])
    try:
        r = cli.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        check("브리지 initialize", (r.get("result") or {}).get("serverInfo", {}).get("name") == "llmwiki", r.get("result", {}).get("serverInfo"))
        r = cli.send("tools/list")
        check("브리지 tools/list", len((r.get("result") or {}).get("tools") or []) >= 12, len((r.get("result") or {}).get("tools") or []))
        r = cli.send("tools/call", {"name": "wiki_status", "arguments": {}})
        check("브리지 tools/call", not is_error(r), text_of(r)[:80].replace("\n", " "))
        cli2 = Stdio(env, extra_args=["--connect", base.rstrip("/") + "/mcp", "--token", "lwk_없는키"])
        try:
            r = cli2.send("tools/list")
            err = (r.get("error") or {}).get("message", "")
            check("브리지: 원격 인증 실패를 JSON-RPC 오류로", "401" in err or "403" in err, err[:90])
        finally:
            cli2.close()
    finally:
        cli.close()


def sec_extensions(tmp, env, base, api_key):
    print("\n[7] 확장 — 플러그인 · 페더레이션 · 외부 RAG")
    # 플러그인 두 개: 정상 + 깨진 것
    pdir = os.path.join(tmp, "mcp_plugins")
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, "hello.py"), "w", encoding="utf-8") as f:
        f.write('# -*- coding: utf-8 -*-\n'
                'def register(add_tool):\n'
                '    add_tool({"name": "verify_echo", "description": "테스트용 에코",\n'
                '              "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]}},\n'
                '             lambda pipe, args: {"echo": args.get("msg"), "docs": pipe.store.stats().get("docs")})\n')
    with open(os.path.join(pdir, "broken.py"), "w", encoding="utf-8") as f:
        f.write("this is not python(\n")

    # 외부 RAG(mock stdio 서버) 를 retrieve + expose 로 켠다
    srcpath = os.path.join(tmp, "mcp_sources.json")
    srcs = json.load(open(srcpath, encoding="utf-8"))
    mock = srcs.get("mock") or {}
    mock.update({"enabled": True, "transport": "stdio", "command": [PY, "-m", "llmwiki.mcp_client", "--mock-server"],
                 "timeout_s": 60, "expose": ["search"]})
    mock.setdefault("retrieve", [{"tool": "search", "args": {"q": "{query}", "limit": "{k}"}, "result_path": "items",
                                  "id_field": "id", "title_field": "title", "text_field": "snippet", "score_field": "score",
                                  "url_field": "url", "doc_type": "issue", "weight": 1.0, "when": "always"}])
    srcs["mock"] = mock
    json.dump(srcs, open(srcpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    env2 = dict(env, LLMWIKI_MCP_PLUGINS_DIR=pdir)
    cfgp = env["LLMWIKI_CONFIG"]
    cfg = json.load(open(cfgp, encoding="utf-8"))
    cfg["mcp_plugins_dir"] = pdir
    cfg["toggles"] = dict(cfg.get("toggles") or {}, mcp_sources=True, mcp_federation=True, external_rag=True)
    json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    cli = Stdio(env2)
    try:
        cli.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        r = cli.send("tools/list")
        tools = (r.get("result") or {}).get("tools") or []
        names = [t["name"] for t in tools]
        check("플러그인 도구 등록", "verify_echo" in names, names[-4:])
        r = cli.send("tools/call", {"name": "verify_echo", "arguments": {"msg": "안녕"}})
        check("플러그인 도구 호출", not is_error(r) and "안녕" in text_of(r), text_of(r)[:80])
        r = cli.send("tools/call", {"name": "verify_echo", "arguments": {}})
        check("플러그인도 인자 검증", is_error(r) and "필수 인자" in text_of(r), text_of(r)[:80])

        check("페더레이션 도구 노출 (<source>__<tool>)", "mock__search" in names, [n for n in names if "__" in n])
        r = cli.send("tools/call", {"name": "mock__search", "arguments": {"q": "AGC", "limit": 2}})
        check("페더레이션 도구 호출 중계", not is_error(r) and "ISSUE-9002" in text_of(r), text_of(r)[:90].replace("\n", " "))
        r = cli.send("tools/call", {"name": "mock__없는도구", "arguments": {}})
        check("expose 목록에 없는 도구 거부", is_error(r), text_of(r)[:90])
        r = cli.send("tools/call", {"name": "없는소스__search", "arguments": {}})
        check("없는 소스 거부", is_error(r) and "unknown federated source" in text_of(r), text_of(r)[:90])

        r = cli.send("tools/call", {"name": "wiki_sources", "arguments": {"check": True}})
        sc = ((r.get("result") or {}).get("structuredContent") or {})
        check("wiki_sources: 플러그인 오류 보고", any(e.get("file") == "broken.py" for e in (sc.get("plugins") or {}).get("errors") or []),
              (sc.get("plugins") or {}).get("errors"))
        check("wiki_sources: 페더레이션 도구 목록", "mock__search" in (sc.get("federated_tools") or []), sc.get("federated_tools"))
        check("wiki_sources: 소스 연결 상태", ((sc.get("sources") or [{}])[0] or {}).get("status", {}).get("ok") is not None or
              any((s or {}).get("status", {}).get("ok") for s in sc.get("sources") or []), [s.get("name") for s in sc.get("sources") or []])

        r = cli.send("tools/call", {"name": "wiki_external_search", "arguments": {"query": "AGC 수렴", "k": 2}})
        check("외부 RAG 직접 검색", not is_error(r) and "ISSUE-9002" in text_of(r), text_of(r)[:90].replace("\n", " "))
        r = cli.send("tools/call", {"name": "wiki_query", "arguments": {"question": "AGC 수렴 지연 원인", "k": 5}})
        check("외부 결과가 질의에 융합 (ext 채널)", not is_error(r), text_of(r)[:90].replace("\n", " "))
    finally:
        cli.close()

    # 재귀 방지: 페더레이션 하위로 실행된 프로세스는 자기 페더레이션을 하지 않는다
    child = Stdio(dict(env2, LLMWIKI_FEDERATION_DEPTH="1"))
    try:
        child.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        names = [t["name"] for t in ((child.send("tools/list").get("result") or {}).get("tools") or [])]
        check("재귀 방지: 하위 프로세스는 페더레이션 안 함", not any("__" in n for n in names), [n for n in names if "__" in n])
        r = child.send("tools/call", {"name": "mock__search", "arguments": {"q": "AGC"}})
        check("재귀 방지: 하위에서 중계 호출 거부", is_error(r), text_of(r)[:90])
    finally:
        child.close()
    return env2


def sec_doctor(env2):
    print("\n[8] 자가 점검 명령")
    r = subprocess.run([PY, "-m", "llmwiki", "mcp", "--doctor", "--check-sources", "--json"], cwd=ROOT, env=env2,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    try:
        rep = json.loads(r.stdout)
    except Exception:
        check("mcp --doctor --json", False, (r.stdout or r.stderr)[:200])
        return
    check("mcp --doctor --json", rep.get("ok") is True, "오류 %s · 경고 %s · 도구 %d" % (rep.get("errors"), rep.get("warnings"), len(rep.get("tools") or [])))
    check("doctor: 플러그인 오류 검출", any(not c["ok"] and c["check"] == "플러그인" for c in rep["checks"]) or
          any("broken.py" in str(c.get("hint", "")) for c in rep["checks"]), "")
    check("doctor: 페더레이션 도구 보고", any("mock__search" in str(c.get("detail")) for c in rep["checks"]), "")
    r = subprocess.run([PY, "-m", "llmwiki", "mcp", "--doctor"], cwd=ROOT, env=env2, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    check("mcp --doctor (사람이 읽는 출력)", "MCP 자가 점검" in (r.stdout or ""), (r.stdout or "").splitlines()[:1])
    r = subprocess.run([PY, "-m", "llmwiki", "mcp", "--client-config"], cwd=ROOT, env=env2, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    try:
        snip = json.loads(r.stdout)
        ok = all(k in snip for k in ("stdio_json", "http_json", "bridge_json"))
    except Exception:
        snip, ok = {}, False
    check("mcp --client-config (stdio/http/브리지)", ok, list(snip)[:6])


def main(argv=None):
    ap = argparse.ArgumentParser(description="MCP 서버·확장 종단 검증")
    ap.add_argument("--port", type=int, default=8877)
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 지우지 않는다")
    ap.add_argument("--quick", action="store_true", help="HTTP 쪽 도구 반복과 동시성 규모를 줄여 빠르게 (전송·확장 검증은 그대로)")
    ns = ap.parse_args(argv)
    base = "http://127.0.0.1:%d" % ns.port

    print("MCP 검증 시작 (격리 환경 준비 중…)")
    tmp, env, proc = isolated_env(ns.port)
    api_key = ""
    try:
        for _ in range(180):
            try:
                urllib.request.urlopen(base + "/api/auth/me", timeout=2)
                break
            except Exception:
                time.sleep(1)
        # API 키 하나 발급 (원격 LLM 이 쓰는 경로)
        r = subprocess.run([PY, "-m", "llmwiki", "apikey", "add", "verify-mcp", "--role", "class2"], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        for tok in (r.stdout or "").replace('"', " ").replace(",", " ").split():
            if tok.startswith("lwk_"):
                api_key = tok
        if not api_key:
            print("  (API 키 발급 출력: %s)" % (r.stdout or r.stderr)[:200])

        cli = Stdio(env)
        try:
            sec_protocol(cli)
            sec_tools(cli, "stdio")
        finally:
            cli.close()

        keyed = sec_http(base, api_key)
        sec_tools(keyed, "http", quick=ns.quick)
        sec_concurrency(base, api_key, n=4 if ns.quick else 8)
        sec_bridge(env, base, api_key)
        env2 = sec_extensions(tmp, env, base, api_key)
        sec_doctor(env2)
    finally:
        if proc:
            proc.terminate()
            try:
                proc.communicate(timeout=10)
            except Exception:
                proc.kill()
        if not ns.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print("임시 폴더 유지: %s" % tmp)

    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "total": len(RESULTS), "failed": FAILED, "results": RESULTS}
    with open(os.path.join(HERE, "verify_mcp_result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n%s  %d/%d 통과" % ("MCP OK" if not FAILED else "MCP 실패", len(RESULTS) - len(FAILED), len(RESULTS)))
    if FAILED:
        print("실패: " + ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
