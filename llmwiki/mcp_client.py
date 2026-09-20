# -*- coding: utf-8 -*-
"""외부 소스(다른 RAG · MCP 서버 · REST 검색 API) 클라이언트 — mcp_sources.json 한 파일로 붙인다 (docs/RAG_FEDERATION.md).

전송(transport) 3종:
  stdio : 같은 PC 에서 자식 프로세스로 띄우는 MCP 서버 (command/cwd/env)
  http  : 원격 MCP Streamable HTTP 서버 (url + token/headers) — 예: 다른 팀의 llmwiki `POST /mcp`, 사내 RAG 의 MCP 엔드포인트
  rest  : MCP 가 아닌 일반 HTTP JSON 검색 API (base_url + headers; tool 이름 = 엔드포인트 경로, 예 "/search")
용도(소스마다 여러 개 조합):
  ingest   : 빌드 때 raw data → data/mcp_cache/<source>/<doc_type>/<id>.md (front matter) → 일반 문서처럼 색인
  retrieve : 질의 때 외부 검색 결과를 **검색 채널**(ext_<source>) 로 융합(rrf) — 토글 external_rag, 가중치 channel_w_external × weight, when=always|fallback
  enrich   : (구) fallback mcp 단계에서 컨텍스트 꼬리에 텍스트로만 첨부
  expose   : 외부 서버의 tool 을 우리 MCP 에 `<source>__<tool>` 로 그대로 노출(페더레이션) — 토글 mcp_federation

mcp_sources.json 으로 서버 실행 방법과 tool 매핑을 선언한다. 실제 tool 이름/스키마는 매핑 파일만 고치면 된다.
  {"mango": {
     "desc": "...", "enabled": true, "transport": "stdio",
     "command": ["python", "-m", "mango_mcp"], "cwd": "", "env": {"MANGO_TOKEN": "${MANGO_MCP_TOKEN}"}, "timeout_s": 60,
     "ingest": [  # build 시 raw data → data/mcp_cache/<source>/<doc_type>/<id>.md (front matter 포함) → 일반 문서처럼 색인
        {"tool": "list_issues", "args": {"since": "{since}"}, "result_path": "items", "doc_type": "issue",
         "id_field": "id", "title_field": "title", "text_field": "description", "date_field": "updated_at",
         "fields": {"status": "status", "tags": "labels", "related.cls": "cl_ids"}}],
     "enrich": [  # 질의 fallback 마지막 단계에서 호출 (mcp_sources 토글 + fallback_levels 에 mcp)
        {"tool": "search", "args": {"q": "{query}", "limit": 5}, "result_path": "items", "id_field": "id",
         "title_field": "title", "text_field": "snippet", "doc_type": "issue"}]}}
값 치환: {since}(마지막 ingest 시각 ISO) {query} {project_root} ${ENV} .
테스트: `python -m llmwiki.mcp_client --mock-server` 는 위 매핑으로 동작하는 목업 MCP 서버.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from .config import ROOT, path_for

EXT_PREFIX = "ext:"      # 외부 검색 결과의 가상 청크/문서 id 접두: ext:<source>:<id>


def ext_chunk_id(source: str, rid: str) -> str:
    return "%s%s:%s" % (EXT_PREFIX, source, rid)


def is_ext_id(cid: str) -> bool:
    return str(cid or "").startswith(EXT_PREFIX)

DEFAULT_SOURCES: Dict[str, Any] = {
    "_comment": "외부 MCP 소스. enabled=false 이거나 toggles.mcp_sources=false 면 사용하지 않음. 값 치환: {since} {query} {project_root} ${ENV}.",
    "mango": {
        "desc": "Mango MCP — issue / CL / build binary raw data (tool 이름·스키마는 실제 서버에 맞게 수정)",
        "enabled": False, "transport": "stdio",
        "command": ["python", "-m", "mango_mcp"], "cwd": "", "env": {"MANGO_TOKEN": "${MANGO_MCP_TOKEN}"}, "timeout_s": 60,
        "ingest": [
            {"tool": "list_issues", "args": {"since": "{since}"}, "result_path": "items", "doc_type": "issue",
             "id_field": "id", "title_field": "title", "text_field": "description", "date_field": "updated_at",
             "fields": {"status": "status", "tags": "labels", "related.cls": "cl_ids"}},
            {"tool": "list_cls", "args": {"since": "{since}"}, "result_path": "items", "doc_type": "cl",
             "id_field": "id", "title_field": "subject", "text_field": "message", "date_field": "merged_at",
             "fields": {"related.issues": "issue_ids", "author": "author"}},
            {"tool": "list_builds", "args": {"since": "{since}"}, "result_path": "items", "doc_type": "build",
             "id_field": "build_id", "title_field": "name", "text_field": "summary", "date_field": "built_at",
             "fields": {"related.cls": "cl_ids", "tags": "targets"}},
        ],
        "enrich": [
            {"tool": "search", "args": {"q": "{query}", "limit": 5}, "result_path": "items", "id_field": "id",
             "title_field": "title", "text_field": "snippet", "doc_type": "issue"},
        ],
    },
    "mock": {
        "desc": "테스트용 목업 MCP 서버 (네트워크 없음)", "enabled": False, "transport": "stdio",
        "command": [sys.executable, "-m", "llmwiki.mcp_client", "--mock-server"], "cwd": "", "env": {}, "timeout_s": 30,
        "ingest": [
            {"tool": "list_issues", "args": {"since": "{since}"}, "result_path": "items", "doc_type": "issue",
             "id_field": "id", "title_field": "title", "text_field": "description", "date_field": "updated_at",
             "fields": {"status": "status", "tags": "labels", "related.cls": "cl_ids"}},
            {"tool": "list_cls", "args": {"since": "{since}"}, "result_path": "items", "doc_type": "cl",
             "id_field": "id", "title_field": "subject", "text_field": "message", "date_field": "merged_at",
             "fields": {"related.issues": "issue_ids"}},
        ],
        "enrich": [{"tool": "search", "args": {"q": "{query}", "limit": 3}, "result_path": "items", "id_field": "id",
                    "title_field": "title", "text_field": "snippet", "doc_type": "issue"}],
        "retrieve": [{"tool": "search", "args": {"q": "{query}", "limit": "{k}"}, "result_path": "items", "id_field": "id", "title_field": "title",
                      "text_field": "snippet", "score_field": "score", "url_field": "url", "doc_type": "issue", "weight": 1.0, "when": "always"}],
        "expose": ["search"],
    },
}


def sources_path() -> str:
    return path_for("mcp_sources")


#: `retrieve`/`ingest`/`enrich` 는 **여러 개**를 둘 수 있어서 목록이다. 하나만 쓸 때 객체로 적는 것이
#: 자연스러워 보이므로 실수가 잦다 — 그대로 두면 dict 를 순회해 **문자열 키**가 spec 자리에 들어가고
#: `'str' object has no attribute 'get'` 같은 엉뚱한 오류로 나타난다. 읽을 때 한 번에 바로잡는다.
_SPEC_LISTS = ("retrieve", "ingest", "enrich")


def normalize_source(name: str, cfg: Any) -> Dict[str, Any]:
    """소스 설정 하나를 안전한 형태로 다듬는다. 잘못된 형태는 **어디가 잘못됐는지** 말해 준다."""
    if not isinstance(cfg, dict):
        raise ValueError("mcp_sources.json: 소스 '%s' 는 객체여야 합니다 (받은 값: %s)" % (name, type(cfg).__name__))
    out = dict(cfg)
    for key in _SPEC_LISTS:
        v = out.get(key)
        if v is None:
            continue
        if isinstance(v, dict):
            out[key] = [v]                     # 하나만 적은 흔한 형태를 받아 준다
            continue
        if not isinstance(v, list):
            raise ValueError("mcp_sources.json: 소스 '%s' 의 '%s' 는 목록이어야 합니다 (받은 값: %s)"
                             % (name, key, type(v).__name__))
        bad = [i for i, sp in enumerate(v) if not isinstance(sp, dict)]
        if bad:
            raise ValueError("mcp_sources.json: 소스 '%s' 의 '%s'[%s] 항목은 객체여야 합니다 "
                             "(목록 안에는 {\"tool\": …} 형태가 들어갑니다)" % (name, key, ", ".join(str(i) for i in bad)))
        miss = [i for i, sp in enumerate(v) if not str(sp.get("tool") or "").strip()]
        if miss:
            raise ValueError("mcp_sources.json: 소스 '%s' 의 '%s'[%s] 에 'tool' 이 없습니다"
                             % (name, key, ", ".join(str(i) for i in miss)))
    return out


#: 마지막으로 읽을 때 형식이 잘못돼 **건너뛴** 소스 {이름: 이유}. doctor·CLI·Web 이 보여 준다.
_LOAD_ERRORS: Dict[str, str] = {}


def source_errors() -> Dict[str, str]:
    """형식이 잘못돼 건너뛴 소스와 그 이유 (마지막 load_sources 기준)."""
    return dict(_LOAD_ERRORS)


def load_sources(strict: bool = False) -> Dict[str, Dict[str, Any]]:
    """소스 설정을 읽어 형태를 다듬는다.

    `strict=False`(기본): 형식이 잘못된 소스는 **건너뛰고** 이유를 `source_errors()` 에 남긴다.
    여러 사람이 쓰는 서버에서 소스 하나의 오타가 **모두의 질의를 멈추게 해서는 안 되기** 때문이다.
    `strict=True`: 저장·점검처럼 "지금 이 파일이 올바른가" 를 묻는 자리에서 쓴다 (예외를 던진다).
    """
    p = sources_path()
    if not os.path.exists(p):
        save_sources(DEFAULT_SOURCES)
        data = json.loads(json.dumps(DEFAULT_SOURCES))
    else:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("mcp_sources.json 의 최상위는 {소스이름: 설정} 객체여야 합니다")
    out: Dict[str, Dict[str, Any]] = {}
    errs: Dict[str, str] = {}
    for k, v in data.items():
        if k.startswith("_"):
            continue
        try:
            out[k] = normalize_source(k, v)
        except ValueError as e:
            if strict:
                raise
            errs[k] = str(e)
            try:
                from . import logging_setup as _ls
                _ls.log("error", "mcp_sources: 소스 '%s' 를 건너뜁니다 — %s" % (k, e), "mcp")
            except Exception:
                pass
    _LOAD_ERRORS.clear()
    _LOAD_ERRORS.update(errs)
    return out


def save_sources(data: Dict[str, Any]) -> str:
    p = sources_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


def cache_dir(name: str) -> str:
    return os.path.join(ROOT, "data", "mcp_cache", name)


def enabled_sources(settings=None, ignore_toggle: bool = False) -> Dict[str, Dict[str, Any]]:
    """enabled=true 인 소스. settings 가 있으면 toggles.mcp_sources 가 꺼진 경우 빈 dict (ignore_toggle=True 면 토글 무시 — external_rag/federation 은 자기 토글로 제어)."""
    if settings is not None and not ignore_toggle and not settings.toggles.mcp_sources:
        return {}
    return {k: v for k, v in load_sources().items() if v.get("enabled")}


def source_summary(name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """문서/UI/MCP wiki_sources 용 한 줄 요약."""
    rt = cfg.get("retrieve") or []
    return {"name": name, "enabled": bool(cfg.get("enabled")), "transport": cfg.get("transport") or "stdio", "desc": cfg.get("desc", ""),
            "target": cfg.get("url") or cfg.get("base_url") or " ".join(str(x) for x in (cfg.get("command") or [])),
            "ingest": len(cfg.get("ingest") or []), "enrich": len(cfg.get("enrich") or []),
            "retrieve": [{"tool": r.get("tool"), "when": r.get("when", "always"), "weight": r.get("weight", 1.0)} for r in rt],
            "expose": cfg.get("expose") or False}


def _subst(v: Any, ctx: Dict[str, str]) -> Any:
    if isinstance(v, str):
        for k, val in ctx.items():
            v = v.replace("{%s}" % k, val)
        if v.startswith("${") and v.endswith("}"):
            v = os.environ.get(v[2:-1], "")
        return v
    if isinstance(v, dict):
        return {k: _subst(x, ctx) for k, x in v.items()}
    if isinstance(v, list):
        return [_subst(x, ctx) for x in v]
    return v


def _dig(obj: Any, path: str) -> Any:
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        else:
            return None
    return cur


class MCPClient:
    """stdio JSON-RPC 2.0 클라이언트 (줄 단위 JSON). 우리 mcp.py 서버와 대칭."""

    def __init__(self, name: str, cfg: Dict[str, Any]):
        self.name, self.cfg = name, cfg
        self.proc: Optional[subprocess.Popen] = None
        self._id = 0
        self._lock = threading.Lock()
        self.tools: List[Dict[str, Any]] = []

    def start(self) -> "MCPClient":
        ctx = {"project_root": ROOT, "python": sys.executable}
        cmd = [_subst(a, ctx) for a in self.cfg.get("command") or []]
        if not cmd:
            raise RuntimeError("source %s: command 없음" % self.name)
        env = dict(os.environ)
        env.update({k: str(_subst(v, ctx)) for k, v in (self.cfg.get("env") or {}).items()})
        env["LLMWIKI_FEDERATION_DEPTH"] = str(int(os.environ.get("LLMWIKI_FEDERATION_DEPTH") or 0) + 1)   # 자식이 llmwiki 면 재페더레이션 금지
        cwd = _subst(self.cfg.get("cwd") or "", ctx) or None
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     encoding="utf-8", errors="replace", cwd=cwd if cwd and os.path.isdir(cwd) else None, env=env, bufsize=1)
        r = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "llmwiki", "version": "0.3"}})
        self.notify("notifications/initialized", {})
        self.tools = (self.request("tools/list", {}).get("result") or {}).get("tools") or []
        return self

    def _send(self, msg: Dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        with self._lock:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        assert self.proc and self.proc.stdout
        with self._lock:
            self._id += 1
            mid = self._id
            self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
            deadline = time.time() + float(self.cfg.get("timeout_s") or 60)
            while time.time() < deadline:
                line = self.proc.stdout.readline()
                if not line:
                    err = (self.proc.stderr.read() if self.proc.stderr else "")[:300]
                    raise RuntimeError("source %s: server closed (%s)" % (self.name, err))
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError("source %s: %s" % (self.name, msg["error"]))
                    return msg
            raise RuntimeError("source %s: timeout waiting %s" % (self.name, method))

    def call_tool(self, name: str, args: Dict[str, Any]) -> Any:
        r = self.request("tools/call", {"name": name, "arguments": args})
        res = r.get("result") or {}
        if res.get("isError"):
            raise RuntimeError("tool %s error: %s" % (name, res))
        # structuredContent 우선, 없으면 text 를 JSON 으로
        if "structuredContent" in res:
            return res["structuredContent"]
        texts = [c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text"]
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except Exception:
            return {"text": joined}

    def close(self) -> None:
        if self.proc:
            try:
                self.proc.stdin.close()  # type: ignore
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    def __enter__(self) -> "MCPClient":
        return self.start()

    def __exit__(self, *a: Any) -> None:
        self.close()


def _headers_of(cfg: Dict[str, Any]) -> Dict[str, str]:
    """cfg.headers (${ENV} 치환) + token/token_env → Authorization: Bearer."""
    ctx = {"project_root": ROOT}
    h = {str(k): str(_subst(v, ctx)) for k, v in (cfg.get("headers") or {}).items()}
    tok = _subst(cfg.get("token") or "", ctx) or (os.environ.get(cfg.get("token_env") or "", "") if cfg.get("token_env") else "")
    if tok and not any(k.lower() == "authorization" for k in h):
        h["Authorization"] = "Bearer " + tok
    return h


class HttpMCPClient:
    """원격 MCP Streamable HTTP(JSON 응답 모드) 클라이언트 — 우리 서버의 POST /mcp 와 대칭. cfg: url, token|token_env|headers, timeout_s."""

    def __init__(self, name: str, cfg: Dict[str, Any]):
        self.name, self.cfg = name, cfg
        self.url = str(_subst(cfg.get("url") or "", {"project_root": ROOT}))
        self.session: Optional[str] = None
        self._id = 0
        self._lock = threading.Lock()
        self.tools: List[Dict[str, Any]] = []

    def start(self) -> "HttpMCPClient":
        if not self.url:
            raise RuntimeError("source %s: url 없음" % self.name)
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "llmwiki", "version": "0.4"}})
        self.notify("notifications/initialized", {})
        self.tools = (self.request("tools/list", {}).get("result") or {}).get("tools") or []
        return self

    def _post(self, msg: Dict[str, Any]) -> Any:
        from .mcp import http_post_mcp, FED_HEADER
        h = _headers_of(self.cfg)
        h[FED_HEADER] = str(int(os.environ.get("LLMWIKI_FEDERATION_DEPTH") or 0) + 1)   # 원격이 llmwiki 면 재페더레이션 금지
        status, hdrs, body = http_post_mcp(self.url, msg, "", self.session, int(self.cfg.get("timeout_s") or 60), headers=h)
        if hdrs.get("Mcp-Session-Id"):
            self.session = hdrs["Mcp-Session-Id"]
        if status == 202 or not body:
            return None
        if status >= 400:
            raise RuntimeError("source %s: HTTP %s %s" % (self.name, status, body.decode("utf-8", "ignore")[:200]))
        return json.loads(body.decode("utf-8"))

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        with self._lock:
            self._post({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._id += 1
            msg = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}) or {}
        if "error" in msg:
            raise RuntimeError("source %s: %s" % (self.name, msg["error"]))
        return msg

    call_tool = MCPClient.call_tool

    def close(self) -> None:
        self.session = None

    def __enter__(self) -> "HttpMCPClient":
        return self.start()

    def __exit__(self, *a: Any) -> None:
        self.close()


class RestClient:
    """MCP 가 아닌 일반 HTTP JSON API. tool 이름 = 엔드포인트 경로("/search"), args = JSON 본문(POST) 또는 쿼리스트링(GET).
    cfg: base_url, headers|token|token_env, method(기본 POST), timeout_s, tools(선택: expose 용 tool 스키마 선언)."""

    def __init__(self, name: str, cfg: Dict[str, Any]):
        self.name, self.cfg = name, cfg
        self.base = str(_subst(cfg.get("base_url") or cfg.get("url") or "", {"project_root": ROOT})).rstrip("/")
        self.tools: List[Dict[str, Any]] = list(cfg.get("tools") or [])

    def start(self) -> "RestClient":
        if not self.base:
            raise RuntimeError("source %s: base_url 없음" % self.name)
        if self.cfg.get("ping"):
            self.call_tool(str(self.cfg["ping"]), {}, method="GET")
        return self

    def call_tool(self, name: str, args: Dict[str, Any], method: Optional[str] = None) -> Any:
        m = (method or self.cfg.get("method") or "POST").upper()
        url = self.base + ("/" + name.lstrip("/") if name else "")
        h = {"Accept": "application/json"}
        h.update(_headers_of(self.cfg))
        data = None
        if m == "GET":
            if args:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in args.items()})
        else:
            h["Content-Type"] = "application/json"
            data = json.dumps(args, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=h, method=m)
        try:
            with urllib.request.urlopen(req, timeout=float(self.cfg.get("timeout_s") or 30)) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError("source %s: HTTP %s %s" % (self.name, e.code, e.read().decode("utf-8", "ignore")[:200]))
        except Exception as e:
            raise RuntimeError("source %s: %s" % (self.name, str(e)[:200]))
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            return {"text": body.decode("utf-8", "ignore")}

    def close(self) -> None:
        pass

    def __enter__(self) -> "RestClient":
        return self.start()

    def __exit__(self, *a: Any) -> None:
        self.close()


def open_source(name: str, cfg: Dict[str, Any]):
    """transport 에 맞는 클라이언트 (start 전). with open_source(...) as c: c.call_tool(...)"""
    tr = str(cfg.get("transport") or "stdio").lower()
    if tr == "http":
        return HttpMCPClient(name, cfg)
    if tr == "rest":
        return RestClient(name, cfg)
    return MCPClient(name, cfg)


# 질의 경로용 클라이언트 풀: stdio 는 매 질의마다 프로세스를 띄우면 수백 ms 가 들므로 살려 둔다 (오류 나면 버리고 다시 연다)
_POOL: Dict[str, Any] = {}
_POOL_LOCK = threading.Lock()


def get_client(name: str, cfg: Dict[str, Any]):
    with _POOL_LOCK:
        c = _POOL.get(name)
        if c is not None:
            proc = getattr(c, "proc", None)
            if proc is None or proc.poll() is None:
                return c
            _POOL.pop(name, None)
        c = open_source(name, cfg).start()
        _POOL[name] = c
        return c


def drop_client(name: str) -> None:
    with _POOL_LOCK:
        c = _POOL.pop(name, None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass


def close_all() -> None:
    for n in list(_POOL):
        drop_client(n)


def test_sources(settings=None, names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    out = []
    srcs = load_sources()
    for name, cfg in srcs.items():
        if names and name not in names:
            continue
        if not names and not cfg.get("enabled"):
            continue
        t0 = time.perf_counter()
        try:
            with open_source(name, cfg) as c:
                out.append({"name": name, "ok": True, "transport": cfg.get("transport") or "stdio", "tools": [t.get("name") for t in c.tools],
                            "ms": round((time.perf_counter() - t0) * 1000)})
        except Exception as e:
            out.append({"name": name, "ok": False, "transport": cfg.get("transport") or "stdio", "error": str(e)[:300], "ms": round((time.perf_counter() - t0) * 1000)})
    return out


def remote_tools(name: str, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """소스가 제공하는 tool 스키마 (MCP: tools/list, REST: cfg.tools 선언)."""
    with open_source(name, cfg) as c:
        return list(c.tools)


def call_source_tool(name: str, cfg: Dict[str, Any], tool: str, args: Dict[str, Any]) -> Any:
    """소스의 tool 을 한 번 호출 (풀 사용; 실패 시 풀에서 제거 후 예외)."""
    c = get_client(name, cfg)
    try:
        return c.call_tool(tool, args)
    except Exception:
        drop_client(name)
        raise


def _first(rec: Dict[str, Any], *paths: str) -> Any:
    for p_ in paths:
        if p_:
            v = _dig(rec, p_)
            if v not in (None, "", []):
                return v
    return None


def retrieve(settings, query: str, k: int = 5, names: Optional[List[str]] = None, include_fallback: bool = False) -> List[Dict[str, Any]]:
    """외부 소스의 retrieve 매핑을 호출해 검색 채널용 결과를 돌려준다.
    반환 항목: {source, id, chunk_id(ext:<source>:<id>), title, text, score(없으면 1/(1+rank)), url, doc_type, weight, rank}
    오류는 {source, error} 항목으로 (채널 하나가 죽어도 질의는 계속). when=fallback 매핑은 include_fallback 일 때만."""
    out: List[Dict[str, Any]] = []
    for name, cfg in enabled_sources(settings, ignore_toggle=True).items():
        if names and name not in names:
            continue
        specs = [sp for sp in (cfg.get("retrieve") or []) if (sp.get("when", "always") == "always" or include_fallback)]
        if not specs:
            continue
        ctx = {"query": query, "k": str(int(k)), "project_root": ROOT, "since": ""}
        for spec in specs:
            t0 = time.perf_counter()
            try:
                c = get_client(name, cfg)
                args = _subst(spec.get("args") or {}, ctx)
                # "{k}" 치환이 문자열이 되므로 정수 인자로 되돌린다
                for ak, av in list(args.items()):
                    if isinstance(av, str) and av.isdigit():
                        args[ak] = int(av)
                res = c.call_tool(spec["tool"], args, **({"method": spec["method"]} if spec.get("method") and isinstance(c, RestClient) else {}))
            except Exception as e:
                drop_client(name)
                out.append({"source": name, "error": str(e)[:200], "tool": spec.get("tool")})
                continue
            items = _dig(res, spec.get("result_path", "")) if spec.get("result_path") else res
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                items = []
            n = 0
            for rank, rec in enumerate(items):
                if not isinstance(rec, dict):
                    continue
                rid = str(_first(rec, spec.get("id_field", "id"), "id", "doc_id", "chunk_id") or "").strip() or "%s-%d" % (spec.get("tool", "r"), rank + 1)
                text = _first(rec, spec.get("text_field", "text"), "text", "snippet", "content", "chunk", "page_content")
                if isinstance(text, (dict, list)):
                    text = json.dumps(text, ensure_ascii=False)
                title = str(_first(rec, spec.get("title_field", "title"), "title", "heading", "name") or rid)
                sc = _first(rec, spec.get("score_field", "score"), "score", "similarity", "relevance")
                try:
                    sc = float(sc) if sc is not None else None
                except Exception:
                    sc = None
                if sc is None or sc <= 0:
                    sc = 1.0 / (1.0 + rank)
                out.append({"source": name, "id": rid, "chunk_id": ext_chunk_id(name, rid), "title": title[:200], "text": str(text or "")[:int(spec.get("max_chars") or 2000)],
                            "score": sc, "rank": rank + 1, "url": _first(rec, spec.get("url_field", "url"), "url", "link", "href"),
                            "doc_type": spec.get("doc_type") or cfg.get("doc_type") or "external", "weight": float(spec.get("weight", cfg.get("weight", 1.0)) or 1.0),
                            "ms": round((time.perf_counter() - t0) * 1000)})
                n += 1
                if n >= int(k):
                    break
    return out


def _record_to_doc(rec: Dict[str, Any], spec: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    rid = str(_dig(rec, spec.get("id_field", "id")) or "").strip()
    if not rid:
        return None
    title = str(_dig(rec, spec.get("title_field", "title")) or rid)
    text = _dig(rec, spec.get("text_field", "text"))
    if isinstance(text, (dict, list)):
        text = json.dumps(text, ensure_ascii=False, indent=1)
    text = str(text or "")
    date = _dig(rec, spec.get("date_field", "date"))
    meta: Dict[str, Any] = {"schema_version": 1, "doc_type": spec.get("doc_type", "mcp"), "id": rid, "title": title,
                            "date": str(date)[:10] if date else time.strftime("%Y-%m-%d"), "source": "mcp:%s" % source}
    for fk, path in (spec.get("fields") or {}).items():
        val = _dig(rec, path)
        if val in (None, "", []):
            continue
        if fk.startswith("related."):
            meta.setdefault("related", {})[fk.split(".", 1)[1]] = val if isinstance(val, list) else [val]
        else:
            meta[fk] = val
    return {"id": rid, "meta": meta, "text": text, "raw": rec}


def ingest(settings, names: Optional[List[str]] = None, since: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """소스별 ingest tool 을 호출해 data/mcp_cache/<source>/<doc_type>/<id>.md 로 저장. 이후 build 가 색인한다."""
    from .schema import dump_front_matter
    out: Dict[str, Any] = {"sources": {}, "written": 0, "skipped": 0, "errors": []}
    srcs = load_sources()
    for name, cfg in srcs.items():
        if names and name not in names:
            continue
        if not names and not cfg.get("enabled"):
            continue
        d = cache_dir(name)
        state_path = os.path.join(d, "_state.json")
        state = {}
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = {}
        ctx = {"since": since or state.get("last_ingest") or "1970-01-01T00:00:00", "project_root": ROOT, "query": ""}
        per: Dict[str, Any] = {"tools": {}, "written": 0, "skipped": 0}
        try:
            with open_source(name, cfg) as c:
                for spec in cfg.get("ingest") or []:
                    tool = spec["tool"]
                    try:
                        res = c.call_tool(tool, _subst(spec.get("args") or {}, ctx))
                    except Exception as e:
                        out["errors"].append({"source": name, "tool": tool, "error": str(e)[:300]})
                        continue
                    items = _dig(res, spec.get("result_path", "")) if spec.get("result_path") else res
                    if isinstance(items, dict):
                        items = [items]
                    if not isinstance(items, list):
                        items = []
                    n = 0
                    for rec in items:
                        if not isinstance(rec, dict):
                            continue
                        doc = _record_to_doc(rec, spec, name)
                        if not doc:
                            continue
                        n += 1
                        if dry_run:
                            continue
                        sub = os.path.join(d, str(spec.get("doc_type", "mcp")))
                        os.makedirs(sub, exist_ok=True)
                        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in doc["id"])[:120]
                        path = os.path.join(sub, safe + ".md")
                        content = dump_front_matter(doc["meta"]) + "\n# %s %s\n\n%s\n" % (doc["id"], doc["meta"]["title"], doc["text"])
                        if os.path.exists(path):
                            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                                if f.read() == content:
                                    per["skipped"] += 1
                                    continue
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(content)
                        per["written"] += 1
                    per["tools"][tool] = n
        except Exception as e:
            out["errors"].append({"source": name, "error": str(e)[:300]})
        if not dry_run:
            os.makedirs(d, exist_ok=True)
            state["last_ingest"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f)
        out["sources"][name] = per
        out["written"] += per["written"]
        out["skipped"] += per["skipped"]
    return out


def enrich(settings, query: str, names: Optional[List[str]] = None, limit: int = 5) -> List[Dict[str, Any]]:
    """질의 시 외부 소스 검색 → [{source, id, title, text, doc_type}] (fallback L4 / MCP 단계에서 사용)."""
    out: List[Dict[str, Any]] = []
    for name, cfg in enabled_sources(settings).items():
        if names and name not in names:
            continue
        try:
            with open_source(name, cfg) as c:
                for spec in cfg.get("enrich") or []:
                    res = c.call_tool(spec["tool"], _subst(spec.get("args") or {}, {"query": query, "project_root": ROOT, "since": ""}))
                    items = _dig(res, spec.get("result_path", "")) if spec.get("result_path") else res
                    for rec in (items if isinstance(items, list) else [items]):
                        if not isinstance(rec, dict):
                            continue
                        doc = _record_to_doc(rec, spec, name)
                        if doc:
                            out.append({"source": name, "id": doc["id"], "title": doc["meta"]["title"], "text": doc["text"][:2000],
                                        "doc_type": doc["meta"]["doc_type"]})
                        if len(out) >= limit:
                            break
        except Exception as e:
            out.append({"source": name, "error": str(e)[:200]})
    return out


def ingest_dirs(settings) -> List[str]:
    """빌드가 corpus_dirs 에 추가로 스캔할 MCP 캐시 폴더."""
    return [cache_dir(n) for n in enabled_sources(settings) if os.path.isdir(cache_dir(n))]


# ---------------------------------------------------------------- mock server (tests)
def _mock_server() -> int:
    issues = [{"id": "ISSUE-9001", "title": "TX 전력 제어 오동작", "description": "TX power control 루프에서 PA gain 테이블 인덱스 오류. CL-7001 로 수정.",
               "updated_at": "2026-09-01T10:00:00", "status": "fixed", "labels": ["tx", "pa"], "cl_ids": ["CL-7001"]},
              {"id": "ISSUE-9002", "title": "RX AGC 수렴 지연", "description": "AGC 수렴이 3ms 이상 걸림. 원인 분석 중.",
               "updated_at": "2026-09-05T10:00:00", "status": "analyzing", "labels": ["rx", "agc"], "cl_ids": []}]
    cls = [{"id": "CL-7001", "subject": "PA gain 테이블 인덱스 수정", "message": "ISSUE-9001 수정: 인덱스 off-by-one.", "merged_at": "2026-09-02", "issue_ids": ["ISSUE-9001"]}]
    tools = [{"name": "list_issues", "description": "issues since", "inputSchema": {"type": "object", "properties": {"since": {"type": "string"}}}},
             {"name": "list_cls", "description": "cls since", "inputSchema": {"type": "object", "properties": {"since": {"type": "string"}}}},
             {"name": "search", "description": "search", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}, "limit": {"type": "integer"}}}}]
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            resp = {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "mock-mango", "version": "0"}}}
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name == "list_issues":
                data = {"items": issues}
            elif name == "list_cls":
                data = {"items": cls}
            elif name == "search":
                q = str(args.get("q", "")).lower()
                hits = [i for i in issues if any(w in (i["title"] + i["description"]).lower() for w in q.split())]
                data = {"items": [{"id": i["id"], "title": i["title"], "snippet": i["description"], "score": round(1.0 - 0.1 * n, 3),
                                   "url": "mock://issues/%s" % i["id"]} for n, i in enumerate(hits)][: int(args.get("limit") or 10)]}
            else:
                resp = {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "unknown tool"}], "isError": True}}
                out.write(json.dumps(resp) + "\n")
                out.flush()
                continue
            resp = {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}}
        elif mid is None:
            continue
        else:
            resp = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
        out.write(json.dumps(resp, ensure_ascii=False) + "\n")
        out.flush()
    return 0


def _mock_rest_server(port: int) -> int:
    """테스트/문서용 REST 검색 API 목업: POST /search {"query","k"} → {"results":[{id,title,content,score,url}]}, GET /health."""
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    docs = [{"id": "KB-1", "title": "RX AGC 수렴 가이드", "content": "AGC 수렴 시간은 gain step 과 loop filter 계수로 결정된다. 3ms 이상이면 step 을 키운다."},
            {"id": "KB-2", "title": "PA gain 테이블", "content": "PA gain 테이블 인덱스는 0 부터 시작한다. off-by-one 이면 TX 전력 제어가 오동작한다."}]

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def _json(self, obj: Any, code: int = 200) -> None:
            b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self) -> None:
            self._json({"ok": True} if self.path.startswith("/health") else {"error": "not found"}, 200 if self.path.startswith("/health") else 404)

        def do_POST(self) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            if self.path.rstrip("/") == "/search":
                q = str(body.get("query", "")).lower()
                hits = [dict(d, score=round(0.9 - 0.1 * i, 2), url="rest://kb/%s" % d["id"]) for i, d in enumerate(docs) if any(w in (d["title"] + d["content"]).lower() for w in q.split())]
                return self._json({"results": hits[: int(body.get("k") or 5)]})
            self._json({"error": "not found"}, 404)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    print("mock REST RAG on http://127.0.0.1:%d  (POST /search, GET /health)" % httpd.server_address[1], flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    if "--mock-server" in sys.argv:
        sys.exit(_mock_server())
    if "--mock-rest" in sys.argv:
        i = sys.argv.index("--mock-rest")
        sys.exit(_mock_rest_server(int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 8799))
    print("usage: python -m llmwiki.mcp_client --mock-server | --mock-rest [port]")
