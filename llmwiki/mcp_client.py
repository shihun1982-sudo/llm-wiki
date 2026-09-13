# -*- coding: utf-8 -*-
"""외부 MCP 소스 클라이언트 (예: Mango MCP — issue / CL / build binary raw data).

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
from typing import Any, Dict, List, Optional

from .config import ROOT, path_for

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
    },
}


def sources_path() -> str:
    return path_for("mcp_sources")


def load_sources() -> Dict[str, Dict[str, Any]]:
    p = sources_path()
    if not os.path.exists(p):
        save_sources(DEFAULT_SOURCES)
        return {k: v for k, v in json.loads(json.dumps(DEFAULT_SOURCES)).items() if not k.startswith("_")}
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def save_sources(data: Dict[str, Any]) -> str:
    p = sources_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


def cache_dir(name: str) -> str:
    return os.path.join(ROOT, "data", "mcp_cache", name)


def enabled_sources(settings=None) -> Dict[str, Dict[str, Any]]:
    if settings is not None and not settings.toggles.mcp_sources:
        return {}
    return {k: v for k, v in load_sources().items() if v.get("enabled")}


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
        ctx = {"project_root": ROOT}
        cmd = [_subst(a, ctx) for a in self.cfg.get("command") or []]
        if not cmd:
            raise RuntimeError("source %s: command 없음" % self.name)
        env = dict(os.environ)
        env.update({k: str(_subst(v, ctx)) for k, v in (self.cfg.get("env") or {}).items()})
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
            with MCPClient(name, cfg) as c:
                out.append({"name": name, "ok": True, "tools": [t.get("name") for t in c.tools], "ms": round((time.perf_counter() - t0) * 1000)})
        except Exception as e:
            out.append({"name": name, "ok": False, "error": str(e)[:300], "ms": round((time.perf_counter() - t0) * 1000)})
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
            with MCPClient(name, cfg) as c:
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
            with MCPClient(name, cfg) as c:
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
                data = {"items": [{"id": i["id"], "title": i["title"], "snippet": i["description"]} for i in issues if any(w in (i["title"] + i["description"]).lower() for w in q.split())]}
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


if __name__ == "__main__":
    if "--mock-server" in sys.argv:
        sys.exit(_mock_server())
    print("usage: python -m llmwiki.mcp_client --mock-server")
