# -*- coding: utf-8 -*-
"""Web UI 서버 (표준 라이브러리 http.server 기반, 외부 의존성 없음).

- 모든 기능은 JSON API 로 노출되며, /api/cli 는 CLI argparse 를 그대로 실행해 출력(stdout)을 돌려준다.
- 빌드/평가처럼 오래 걸리는 작업은 백그라운드 job 으로 실행하고 /api/jobs/<id> 로 진행 상황을 폴링한다.
- 모든 요청의 프로파일/디버그 trace 는 requests 테이블에 저장되며 /api/requests, /api/request?id= 로 조회한다.
- auto_build 토글이 켜져 있으면 워처 스레드가 auto_build_interval 초마다 코퍼스를 stat 스캔해 변경 시 증분 빌드한다.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

from ..config import apply_overrides, save_settings, Toggles, Settings, TOGGLE_HELP, SETTING_HELP, TOGGLE_GROUPS, effective_settings, all_paths, path_for
from .. import tuning as _tuning
from ..architecture import registry as _arch_registry
from ..profiler import jsonable, Profiler
from ..evalset import load_questions
from .. import evolve as ev
from .. import progress as _pg
from .. import snapshots as _snap
from ..auth import Auth, AuthError, User, RANK, ROLES, ROLE_LABEL, LEVELS, LEVEL_LABEL, DEFAULT_LEVEL_ROLE, classify_api, classify_cli


def _ops_catalog() -> List[Dict[str, str]]:
    """보안 탭 '개별 작업 오버라이드' 편집을 돕는 대표 op 목록 (감사 로그의 op 이름과 동일)."""
    rows: List[Dict[str, str]] = []
    samples = [("POST", "/api/query", {}), ("POST", "/api/search", {}), ("POST", "/api/feedback", {}), ("POST", "/api/forensic/expect", {}),
               ("POST", "/api/eval", {}), ("POST", "/api/trials", {"action": "run"}), ("POST", "/api/fusion/compare", {}), ("POST", "/api/models/test", {}),
               ("POST", "/api/evolve/review", {}), ("POST", "/api/mcp_sources", {"action": "test"}),
               ("POST", "/api/pins", {"action": "add"}), ("POST", "/api/query_rules", {"action": "save"}), ("POST", "/api/prompts", {}), ("POST", "/api/tuning", {"action": "set"}),
               ("POST", "/api/presets", {"action": "save"}), ("POST", "/api/wiki/page", {}), ("POST", "/api/evolve/apply", {}), ("POST", "/api/evolve/reject", {}),
               ("POST", "/api/memory", {"action": "decay"}), ("POST", "/api/rules", {}), ("POST", "/api/trials", {"action": "delete"}),
               ("POST", "/api/build", {"full": False}), ("POST", "/api/build/verify", {"fix": True}), ("POST", "/api/precompute", {"action": "run"}),
               ("POST", "/api/maintenance", {"action": "vacuum"}), ("POST", "/api/snapshot", {"action": "create"}), ("POST", "/api/watch", {"action": "start"}),
               ("POST", "/api/mcp_sources", {"action": "ingest"}),
               ("POST", "/api/build", {"full": True}), ("POST", "/api/build", {"channel": "fts"}), ("POST", "/api/snapshot", {"action": "restore"}),
               ("POST", "/api/config", {}), ("POST", "/api/models/set", {}), ("POST", "/api/agents", {}), ("POST", "/api/auth/users", {"action": "add"}),
               ("POST", "/api/security", {"action": "set_permissions"}), ("POST", "/api/apikeys", {"action": "add"}), ("POST", "/api/mcp_sources", {"action": "save"}),
               ("POST", "/api/build", {"purge_logs": True}), ("POST", "/api/maintenance", {"action": "purge_requests"})]
    seen = set()
    for m, p, b in samples:
        lv, op = classify_api(m, p, b)
        if op not in seen:
            seen.add(op)
            rows.append({"op": op, "level": lv, "kind": "web"})
    for argv in (["query", "x"], ["eval"], ["trial", "run"], ["build"], ["build", "--full"], ["build", "fts"], ["build", "vector"], ["build", "graph"],
                 ["pin", "add"], ["rules", "add"], ["tuning", "set"], ["evolve", "apply"], ["precompute", "run"], ["snapshot", "create"], ["snapshot", "restore"],
                 ["config", "set"], ["models", "set"], ["users", "add"], ["apikey", "add"], ["security", "perms", "set"], ["maintenance", "purge_requests"],
                 ["config", "reset"], ["serve"], ["mcp"]):
        lv, op = classify_cli(argv)
        if op not in seen:
            seen.add(op)
            rows.append({"op": op, "level": lv, "kind": "cli"})
    return rows

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_LOCK = threading.RLock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_WATCHER: Dict[str, Any] = {"thread": None, "stop": False, "log": []}
PROVIDER_KEYS = ("llm_provider", "embed_provider", "llm_model", "embed_model", "llm_roles", "embed_dim", "ollama_url", "ollama_model",
                 "openai_base_url", "openai_api_key_header", "openai_extra_headers", "openai_embed_base_url", "openai_embed_model", "anthropic_base_url",
                 "rerank_url", "rerank_api_model", "rerank_api_style", "embed_store_dtype", "llm_timeout")


def _touches_providers(overrides: Dict[str, Any]) -> bool:
    for k in overrides or {}:
        if k in PROVIDER_KEYS or (k.rsplit("_", 1)[0] in Settings.LLM_ROLES and k.rsplit("_", 1)[-1] in ("provider", "model", "effort")):
            return True
    return False


def _with_overrides(pipe, overrides: Optional[Dict[str, Any]]):
    """요청 단위 토글/프로바이더 오버라이드를 적용하고 복원하는 컨텍스트."""
    class _Ctx:
        def __enter__(self):
            self.saved = pipe.s.to_dict()
            self.reloaded = False
            if overrides:
                apply_overrides(pipe.s, overrides)
                if _touches_providers(overrides):
                    pipe.reload()
                    self.reloaded = True
            return pipe

        def __exit__(self, *a):
            ns = Settings.from_dict(self.saved)
            for k, v in ns.to_dict().items():
                setattr(pipe.s, k, v)
            pipe.s.toggles = ns.toggles
            if self.reloaded:
                pipe.reload()
    return _Ctx()


def _cli_equiv(cmd: str, q: str, overrides: Dict[str, Any], base_toggles: Toggles) -> str:
    parts = ["python -m llmwiki", cmd]
    if q:
        parts.append('"%s"' % q.replace('"', '\\"'))
    for k, v in (overrides or {}).items():
        if k in Toggles.__dataclass_fields__:
            if bool(v) != getattr(base_toggles, k):
                parts.append(("--%s" if v else "--no-%s") % k.replace("_", "-"))
        elif k == "llm_provider":
            parts.append("--llm %s" % v)
        elif k == "embed_provider":
            parts.append("--embed-provider %s" % v)
        elif k == "top_k_final":
            parts.append("--k %s" % v)
        elif k == "debug_level":
            parts.append("--debug %s" % v)
        elif k.rsplit("_", 1)[0] in Settings.LLM_ROLES and k.endswith(("_model", "_provider")):
            parts.append("--%s %s" % (k.replace("_", "-"), v))
    return " ".join(parts) + " --trace"


# ---------------------------------------------------------------- watcher
def _watcher_loop(pipe) -> None:
    while not _WATCHER["stop"]:
        if pipe.s.toggles.auto_build:
            try:
                # 다른 작업(빌드 job·질의)이 락을 잡고 있으면 이번 스캔은 건너뛴다 — 워처가 락을 선점해 Web job 을 굶기지 않도록
                if not _LOCK.acquire(blocking=False):
                    r = {"skipped": "busy"}
                else:
                    try:
                        r = pipe.auto_build_tick(progress=lambda m: _WATCHER["log"].append("%s %s" % (time.strftime("%H:%M:%S"), m)))
                    finally:
                        _LOCK.release()
                if r.get("built") or r.get("error"):
                    _WATCHER["log"].append("%s scan %sms changed=%s removed=%s built=%s %s" % (
                        time.strftime("%H:%M:%S"), r["scan_ms"], r["n_changed"], r["n_removed"], r.get("built"), r.get("error") or ""))
                    del _WATCHER["log"][:-200]
            except Exception as e:
                _WATCHER["log"].append("%s watcher error: %s" % (time.strftime("%H:%M:%S"), e))
            wait = max(5, int(pipe.s.auto_build_interval or 300))
        else:
            wait = 2
        for _ in range(wait):
            if _WATCHER["stop"]:
                return
            time.sleep(1)


def _ensure_watcher(pipe) -> None:
    if _WATCHER["thread"] is None or not _WATCHER["thread"].is_alive():
        _WATCHER["stop"] = False
        t = threading.Thread(target=_watcher_loop, args=(pipe,), daemon=True)
        _WATCHER["thread"] = t
        t.start()


class Handler(BaseHTTPRequestHandler):
    pipe = None  # set by serve()
    auth: Optional[Auth] = None   # set by serve(); 테스트처럼 serve() 를 거치지 않으면 첫 요청에서 생성 (security.json 기준)
    host = "127.0.0.1"

    def log_message(self, fmt, *args):  # 조용히
        pass

    # ---------------- auth helpers ----------------
    def _auth(self) -> Auth:
        if Handler.auth is None:
            Handler.auth = Auth(self.pipe.s, Handler.host)
        return Handler.auth

    def _ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def _user(self) -> Optional[User]:
        return self._auth().identify(self.headers, self._ip())

    def _https(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def _redirect(self, url: str, cookies: Optional[List[str]] = None) -> None:
        self.send_response(302)
        self.send_header("Location", url)
        for c in cookies or []:
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _deny(self, e: AuthError, user: Optional[User], op: str = "", level: str = "") -> None:
        if e.status != 428:
            self._auth().audit(user, op or self.path, level or "?", False, self._ip(), error=e.error)
        self._json(e.body(), e.status)

    # ---------------- helpers ----------------
    def _json(self, obj: Any, code: int = 200, cookies: Optional[List[str]] = None) -> None:
        data = json.dumps(jsonable(obj), ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for c in cookies or []:
            self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> Dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n == 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _static(self, rel: str) -> None:
        path = os.path.normpath(os.path.join(STATIC, rel.lstrip("/")))
        if not path.startswith(STATIC) or not os.path.isfile(path):
            self.send_error(404)
            return
        ctype = {"html": "text/html", "js": "application/javascript", "css": "text/css", "svg": "image/svg+xml", "json": "application/json"}.get(path.rsplit(".", 1)[-1], "application/octet-stream")
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _status(self) -> Dict[str, Any]:
        p = self.pipe
        try:
            from .. import presets as _presets
            preset_defs = _presets.load_presets()
            preset_names = list(preset_defs.keys())
        except Exception:
            preset_defs, preset_names = {}, []
        lb = p.store.kv_get("last_build") or {}
        return {"stats": p.store.stats(), "providers": p.provider_status(), "settings": p.s.to_dict(),
                "toggle_names": list(Toggles.__dataclass_fields__), "toggle_help": TOGGLE_HELP, "setting_help": SETTING_HELP, "toggle_groups": TOGGLE_GROUPS,
                "roles": list(Settings.LLM_ROLES), "caches": p.cache_info(), "presets": preset_names, "preset_defs": preset_defs, "paths": all_paths(),
                "doc_types": p.store.doc_type_counts(), "provenance": p.store.provenance_counts(), "lint": p.store.kv_get("lint_summary"),
                "last_build": lb, "alerts": lb.get("alerts") or [], "embed_progress": p.store.kv_get("embed_progress"),
                "watcher": dict(p.watcher, enabled=p.s.toggles.auto_build, interval=p.s.auto_build_interval,
                                thread_alive=bool(_WATCHER["thread"] and _WATCHER["thread"].is_alive()), log=_WATCHER["log"][-30:]),
                "jobs": {k: {kk: vv for kk, vv in v.items() if kk != "result"} for k, v in _JOBS.items()}}

    # ---------------- MCP (Streamable HTTP) ----------------
    mcp_only = False   # serve(mcp_only=True): /mcp 와 /api/auth/me 만 제공

    def _mcp(self, method: str) -> None:
        """POST/GET/DELETE /mcp — 인증(Bearer API 키·쿠키·익명) 과 read 등급 권한은 authorize() 로, 본문은 mcp.handle_http 로."""
        from .. import mcp as _mcp
        auth = self._auth()
        body = b""
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
        user = None
        try:
            user = self._user()
            level, op = auth.authorize(user, "POST", "/mcp", {}, self.headers if method == "POST" else None, self.headers.get("Host") or "")
        except AuthError as e:
            auth.audit(user, "mcp", "read", False, self._ip(), error=e.error)
            data = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": e.error, "status": e.status}}).encode("utf-8")
            self.send_response(401 if e.status in (401, 428) else e.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if e.status == 401:
                self.send_header("WWW-Authenticate", 'Bearer realm="llmwiki-mcp"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        with _LOCK:
            status, hdrs, out = _mcp.handle_http(self.pipe, method, body, self.headers)
        self.send_response(status)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        if out:
            self.wfile.write(out)

    def do_DELETE(self) -> None:
        if urlparse(self.path).path == "/mcp":
            return self._mcp("DELETE")
        self.send_error(404)

    # ---------------- GET ----------------
    def do_GET(self) -> None:
        u = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = self.pipe
        auth = self._auth()
        if u.path == "/mcp":
            return self._mcp("GET")
        if Handler.mcp_only and u.path not in ("/api/auth/me", "/api/progress", "/mcp"):
            return self._json({"error": "mcp-only server: use POST /mcp"}, 404)
        try:
            # ---- 공개 경로: 정적 파일, 로그인 페이지, SSO 왕복, 로그인 상태 조회 ----
            if u.path.startswith("/static/"):
                return self._static(u.path[len("/static/"):])
            if u.path == "/login":
                return self._static("login.html")
            if u.path == "/api/auth/me":
                user = self._user()
                return self._json(dict(auth.public_info(), user=user.to_dict() if user else None))
            if u.path == "/auth/sso/start":
                if not auth.oidc_enabled():
                    return self._json({"error": "SSO(OIDC) 가 설정되지 않았습니다 (security.json sso)"}, 404)
                url, cookie = auth.oidc_start(qs.get("next") or "/")
                return self._redirect(url, [cookie])
            if u.path == "/auth/sso/callback":
                try:
                    user, nxt = auth.oidc_callback(qs, self.headers.get("Cookie") or "")
                except AuthError as e:
                    auth.audit(None, "sso login", "auth", False, self._ip(), error=e.error)
                    return self._json(e.body(), e.status)
                auth.audit(user, "sso login", "auth", True, self._ip())
                return self._redirect(nxt if nxt.startswith("/") else "/", [auth.make_cookie(user, self._https())])
            user = self._user()
            if u.path in ("/", "/index.html"):
                if user is None:
                    return self._redirect("/login")      # anonymous_role 이 비어 있으면 로그인 필수
                return self._static("index.html")
            # ---- 인가 (GET 은 read, 일부 admin 전용) ----
            try:
                level, op = auth.authorize(user, "GET", u.path, {}, None, "")
            except AuthError as e:
                return self._deny(e, user, u.path, "read")
            if u.path == "/api/auth/users":
                return self._json({"users": auth.list_users(), "mode": auth.mode, "sso": auth.public_info()["sso"], "roles": list(ROLES), "role_labels": ROLE_LABEL})
            if u.path == "/api/audit":
                return self._json({"rows": auth.audit_tail(int(qs.get("n", 200)))})
            if u.path == "/api/apikeys":
                return self._json({"keys": auth.list_api_keys(), "roles": list(ROLES)})
            if u.path == "/api/security":
                cfg = json.loads(json.dumps(auth.cfg))
                for v in (cfg.get("users") or {}).values():
                    v.pop("pw", None)
                for v in (cfg.get("api_keys") or {}).values():
                    v.pop("hash", None)
                return self._json({"security": cfg, "path": __import__("llmwiki.auth", fromlist=["security_path"]).security_path(), "effective_mode": auth.mode,
                                   "permissions": auth.permissions(), "defaults": DEFAULT_LEVEL_ROLE, "levels": list(LEVELS), "level_labels": LEVEL_LABEL,
                                   "roles": list(ROLES), "role_labels": ROLE_LABEL, "ops_catalog": _ops_catalog()})
            if u.path == "/api/snapshot":
                return self._json({"snapshots": _snap.list_(p)})
            # ---- 락 없이 응답하는 폴링용 엔드포인트 ----
            # 빌드 job 이 _LOCK 을 잡은 채 수십 분 돌 수 있으므로, 진행 상황 조회는 절대 락 뒤에 두지 않는다
            # (예전에는 여기가 락 안에 있어서 전체 리빌드 중 화면이 'starting…' 에서 멈춘 것처럼 보였다).
            if u.path.startswith("/api/jobs/"):
                jid = u.path.rsplit("/", 1)[-1]
                j = _JOBS.get(jid)
                if not j:
                    return self._json({"error": "no such job"}, 404)
                out = {k: v for k, v in j.items() if k != "result" or j.get("status") != "running"}
                out["live"] = _pg.get(jid) or {}
                out["elapsed_s"] = round((j.get("finished") or time.time()) - j["started"], 1)
                return self._json(out)
            if u.path.startswith("/api/progress"):
                tok = u.path.rsplit("/", 1)[-1] if u.path != "/api/progress" else qs.get("token", "")
                if tok and tok != "progress":
                    return self._json(_pg.get(tok) or {"status": "unknown", "token": tok})
                return self._json({"running": _pg.all_running(), "jobs": [{k: v for k, v in j.items() if k in ("id", "kind", "status", "started", "finished")} for j in _JOBS.values()]})
            with _LOCK:
                if u.path == "/api/status":
                    st = self._status()
                    st["auth"] = dict(auth.public_info(), user=user.to_dict() if user else None)
                    return self._json(st)
                if u.path == "/api/graph":
                    lim = int(qs.get("limit", 150))
                    comm = qs.get("community")
                    return self._json(p.graph_export(limit=lim, community=int(comm) if comm not in (None, "", "all") else None))
                if u.path == "/api/entity":
                    return self._json(p.entity_detail(qs.get("id", "")))
                if u.path == "/api/docs":
                    meta = p.store.doc_meta_map()
                    return self._json([dict(d, **{k: (meta.get(d["doc_id"]) or {}).get(k) for k in ("doc_type", "ext_id", "date", "inferred")}) for d in p.store.list_docs()])
                if u.path == "/api/chunk":
                    c = p.store.get_chunk(qs.get("id", ""))
                    return self._json(dict(c) if c else {"error": "not found"})
                if u.path == "/api/doc_chunks":
                    return self._json([dict(c) for c in p.store.all_chunks(qs.get("id", ""))])
                if u.path == "/api/queries":
                    return self._json(p.store.queries(int(qs.get("limit", 50))))
                if u.path == "/api/query_trace":
                    r = p.store.get_query(int(qs.get("id", 0)))
                    return self._json(json.loads(r["trace"]) if r else {})
                if u.path == "/api/requests":
                    return self._json(p.store.requests(qs.get("kind") or None, int(qs.get("limit", 100))))
                if u.path == "/api/request":
                    r = p.store.get_request(int(qs.get("id", 0)))
                    return self._json(r or {"error": "not found"}, 200 if r else 404)
                if u.path == "/api/analysis":
                    from .. import analysis as _an
                    rid = int(qs.get("request_id", 0) or 0) or None
                    focus = qs.get("focus") or None
                    with _LOCK:
                        r = _an.analyze(p, rid, focus=None if focus in (None, "", "all") else focus)
                    if r.get("error"):
                        return self._json({"error": r["error"]}, 404)
                    if qs.get("format") == "md":
                        data = r["markdown"].encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "text/markdown; charset=utf-8")
                        if qs.get("download"):
                            self.send_header("Content-Disposition", "attachment; filename=\"analysis_req_%s.md\"" % r["summary"]["request_id"])
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    return self._json({"summary": r["summary"], "paths": r["paths"], "markdown": r["markdown"], "report": r["report"] if qs.get("full") else None})
                if u.path == "/api/models":
                    return self._json({"providers": p.provider_status(), "settings": p.s.to_dict(), "roles": list(Settings.LLM_ROLES)})
                if u.path == "/api/system":
                    return self._json(p.system_info(int(qs.get("target_docs", 3000)), int(qs.get("daily_new", 20)), int(qs.get("horizon_days", 365))))
                if u.path == "/api/watch":
                    return self._json(self._status()["watcher"])
                if u.path == "/api/tuning":
                    return self._json({"tunables": _tuning.T.describe(p.s), "stages": _tuning.STAGES, "path": _tuning.TUNING_PATH,
                                       "overrides": _tuning.T.to_dict()})
                if u.path == "/api/architecture":
                    reg = _arch_registry()
                    last = {}
                    for kind in ("query", "build", "eval"):
                        rows = p.store.requests(kind, 1)
                        if rows:
                            r = p.store.get_request(rows[0]["id"])
                            if r and r.get("trace"):
                                last[kind] = {"id": r["id"], "summary": r["summary"], "ms": r["ms"], "trace": r["trace"]}
                    return self._json(dict(reg, toggles=p.s.toggles.__dict__, settings=p.s.to_dict(), tuning=_tuning.T.to_dict(), last=last,
                                           providers={k: v for k, v in p.provider_status().items() if k != "catalog"}))
                if u.path == "/api/evolve/status":
                    return self._json(ev.status(p))
                if u.path == "/api/evolve/proposals":
                    return self._json(p.store.proposals(qs.get("status") or None))
                if u.path == "/api/wiki/list":
                    d = p.s.wiki_dir
                    files = sorted(f for f in os.listdir(d)) if os.path.isdir(d) else []
                    return self._json([f[:-3] for f in files if f.endswith(".md")])
                if u.path == "/api/wiki/page":
                    path = os.path.join(p.s.wiki_dir, qs.get("name", "") + ".md")
                    if not os.path.isfile(path):
                        return self._json({"error": "not found"}, 404)
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        return self._json({"name": qs.get("name"), "content": f.read()})
                if u.path == "/api/eval/questions":
                    return self._json(load_questions())
                if u.path == "/api/rules":
                    from ..graph_rules import load_rules
                    return self._json(load_rules())
                # ---------------- v3 ----------------
                if u.path == "/api/health":
                    from ..health import run_health
                    return self._json(run_health(p, quick=qs.get("quick", "0") in ("1", "true"), for_build=qs.get("for_build", "0") in ("1", "true")))
                if u.path == "/api/config/effective":
                    return self._json(effective_settings(p.s))
                if u.path == "/api/presets":
                    from .. import presets as _presets
                    return self._json({"presets": _presets.load_presets(), "path": _presets.presets_path()})
                if u.path == "/api/presets/diff":
                    from .. import presets as _presets
                    return self._json(_presets.diff(p.s, qs.get("name", "")))
                if u.path == "/api/prompts":
                    from .. import prompts as _prompts
                    rows = _prompts.list_prompts()
                    if qs.get("name"):
                        return self._json({"name": qs["name"], "content": _prompts.get(qs["name"]), "default": _prompts.DEFAULTS.get(qs["name"], "")})
                    return self._json(rows)
                if u.path == "/api/logs/files":
                    from .. import logging_setup as _ls
                    d = _ls.log_dir() or path_for("logs_dir")
                    return self._json({"dir": d, "files": _ls.files(d)})
                if u.path == "/api/logs":
                    from .. import logging_setup as _ls
                    d = _ls.log_dir() or path_for("logs_dir")
                    path = os.path.join(d, (qs.get("file") or "llmwiki") + ".log")
                    run_id = qs.get("run") or ""
                    if qs.get("request"):
                        r = p.store.get_request(int(qs["request"]))
                        run_id = (r or {}).get("run_id") or "-"
                    since = qs.get("since")
                    since_s = None
                    if since:
                        unit = since[-1].lower()
                        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit)
                        since_s = float(since[:-1]) * mult if mult else float(since)
                    n = int(qs.get("n", 200))
                    if not (run_id or qs.get("text") or qs.get("level") or since_s):
                        rows = _ls.tail(path, n)
                    else:
                        rows = _ls.grep(path, run_id=run_id or None, text=qs.get("text") or None, level=qs.get("level") or None, since_s=since_s, limit=n)
                    return self._json({"file": path, "rows": rows})
                if u.path == "/api/forensics":
                    from .. import forensic as _fx
                    return self._json(_fx.list_forensics(p.store, int(qs.get("limit", 50))))
                if u.path == "/api/forensics/summary":
                    from .. import forensic as _fx
                    return self._json(_fx.summary(p.store))
                if u.path == "/api/forensic":
                    from .. import forensic as _fx
                    rid = int(qs.get("request_id", 0))
                    row = _fx.get_forensic(p.store, rid)
                    if row and qs.get("rerun", "0") not in ("1", "true"):
                        return self._json(row)
                    req = p.store.get_request(rid)
                    if not req or not req.get("trace"):
                        return self._json({"error": "request not found"}, 404)
                    res = req.get("result") or {"query": req["summary"]}
                    diag = _fx.diagnose(req["trace"], res, p.s)
                    fid = row["id"] if row else _fx.record(p.store, rid, req.get("run_id") or "", res.get("query", req["summary"]),
                                                            ((res.get("evidence") or {}).get("verdict", "?")), res.get("groundedness"), diag, "manual")
                    return self._json(dict(diag, id=fid, request_id=rid, run_id=req.get("run_id"), query=res.get("query", req["summary"]),
                                           verdict=(res.get("evidence") or {}).get("verdict"), groundedness=res.get("groundedness")))
                if u.path == "/api/trials":
                    from .. import trials as _tr
                    return self._json(_tr.list_trials(p.store, int(qs.get("limit", 50))))
                if u.path == "/api/trial":
                    from .. import trials as _tr
                    t = _tr.get_trial(p.store, qs.get("id", ""))
                    return self._json(t or {"error": "not found"}, 200 if t else 404)
                if u.path == "/api/trials/compare":
                    from .. import trials as _tr
                    refs = [x for x in (qs.get("ids") or "").split(",") if x.strip()]
                    c = _tr.compare(p.store, refs)
                    c["markdown"] = _tr.report_md(c)
                    return self._json(c)
                if u.path == "/api/pins":
                    from .. import pins as _pins
                    return self._json(_pins.load_pins())
                if u.path == "/api/query_rules":
                    from .. import query_rules as _qr
                    return self._json({"rules": _qr.load_rules(), "path": _qr.rules_path(), "stats": _qr.stats()})
                if u.path == "/api/query_rules/test":
                    from .. import query_rules as _qr
                    return self._json(_qr.expand(qs.get("q", ""), _tuning.T.get("syn_w"), _tuning.T.get("related_w"), _tuning.T.get("acronym_phrase")))
                if u.path == "/api/embed/report":
                    from ..embed_run import embed_report
                    return self._json(embed_report(p))
                if u.path == "/api/build/status":
                    return self._json(p.build_status())
                if u.path == "/api/build/verify":
                    return self._json(p.store.verify(p.embedder.name if p.s.toggles.embed else None, fix=False, wiki_dir=p.s.wiki_dir))
                if u.path == "/api/corpus/lint":
                    return self._json({"summary": p.store.kv_get("lint_summary"), "rows": p.store.lint_rows(only_problems=qs.get("all", "0") not in ("1", "true"), limit=int(qs.get("limit", 200))),
                                       "doc_types": p.store.doc_type_counts()})
                if u.path == "/api/corpus/types":
                    from .. import schema as _schema
                    return self._json({"schemas": _schema.load_schemas(), "dir": _schema.schemas_dir(), "examples": {dt: _schema.example_document(dt) for dt in _schema.doc_types()}})
                if u.path == "/api/mcp_sources":
                    from .. import mcp_client as _mcp
                    return self._json({"sources": _mcp.load_sources(), "path": _mcp.sources_path(), "enabled": p.s.toggles.mcp_sources,
                                       "external_rag": p.s.toggles.external_rag, "mcp_federation": p.s.toggles.mcp_federation,
                                       "summary": [_mcp.source_summary(k, v) for k, v in _mcp.load_sources().items()]})
                if u.path == "/api/memory":
                    from .. import memory as _mem
                    return self._json(dict(_mem.status(p.store, _tuning.T.get("memory_half_life_days")), episodes=_mem.episodes(p.store, int(qs.get("limit", 20)))))
                if u.path == "/api/precompute":
                    from .. import precompute as _pc
                    return self._json(_pc.cache_status(p.store))
                if u.path == "/api/agents":
                    from .. import headless as _hl
                    return self._json({"agents": _hl.load_agents(), "path": _hl.agents_path()})
                if u.path == "/api/themes":
                    tp = os.path.join(STATIC, "themes", "themes.json")
                    try:
                        with open(tp, "r", encoding="utf-8") as f:
                            return self._json(json.load(f))
                    except Exception:
                        return self._json({"themes": [{"key": "light", "title": "Light"}]})
                if u.path == "/api/time":
                    from .. import timeparse as _tp
                    return self._json(_tp.parse(qs.get("q", ""), p.s.timezone, p.s.week_start) or {"expr": None})
            self.send_error(404)
        except AuthError as e:      # identify() 단계의 거부 (잘못된/폐기된 API 키 등)
            self._deny(e, None, u.path, "read")
        except Exception as e:
            self._json({"error": str(e), "trace": traceback.format_exc()}, 500)

    # ---------------- POST ----------------
    def do_POST(self) -> None:
        u = urlparse(self.path)
        if u.path == "/mcp":
            return self._mcp("POST")
        if Handler.mcp_only and u.path not in ("/api/auth/login", "/api/auth/logout"):
            return self._json({"error": "mcp-only server: use POST /mcp"}, 404)
        body = self._body()
        auth = self._auth()
        host = self.headers.get("Host") or ""
        # ---- 인증 자체 (로그인/로그아웃/비밀번호) ----
        if u.path == "/api/auth/login":
            name, pw = str(body.get("username") or "").strip(), str(body.get("password") or "")
            user = auth.login_local(name, pw)
            if not user:
                auth.audit(User(name or "?", "viewer", "local"), "login", "auth", False, self._ip(), error="bad credentials")
                time.sleep(0.5)   # 무차별 대입 완화
                return self._json({"error": "아이디 또는 비밀번호가 올바르지 않습니다"}, 401)
            auth.audit(user, "login", "auth", True, self._ip())
            return self._json({"ok": True, "user": user.to_dict()}, cookies=[auth.make_cookie(user, self._https())])
        if u.path == "/api/auth/logout":
            try:
                user = self._user()
            except AuthError:
                user = None
            if user:
                auth.audit(user, "logout", "auth", True, self._ip())
            return self._json({"ok": True}, cookies=[auth.clear_cookie()])
        try:
            user = self._user()
        except AuthError as e:      # 잘못된/폐기된 API 키 → 401 (게스트로 강등하지 않음)
            return self._deny(e, None, u.path, "?")
        if u.path == "/api/auth/password":
            if not user or user.via != "local":
                return self._json({"error": "로컬 계정으로 로그인한 경우에만 비밀번호를 바꿀 수 있습니다"}, 403)
            if not auth.check_password(user.name, str(body.get("old") or "")):
                return self._json({"error": "현재 비밀번호가 올바르지 않습니다"}, 403)
            try:
                auth.set_password(user.name, str(body.get("new") or ""))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            auth.audit(user, "password change", "auth", True, self._ip())
            return self._json({"ok": True})
        # ---- 인가: 작업 등급 → 역할·확인·문구·재인증 ----
        try:
            level, op = auth.authorize(user, "POST", u.path, body, self.headers, host)
        except AuthError as e:
            lv, op0 = ("?", u.path)
            try:
                from ..auth import classify_api
                lv, op0 = classify_api("POST", u.path, body)
            except Exception:
                pass
            return self._deny(e, user, op0, lv)
        if u.path == "/api/auth/users":
            return self._users_admin(body, user)
        if u.path == "/api/security":
            act = body.get("action") or ""
            try:
                if act == "reload":
                    auth.reload()
                    out: Dict[str, Any] = {"ok": True, "mode": auth.mode}
                elif act == "set_permissions":
                    out = {"ok": True, "permissions": auth.set_permissions(body.get("permissions") or {})}
                elif act == "set_permission":
                    out = {"ok": True, "permissions": auth.set_permission(str(body.get("key") or ""), str(body.get("role") or ""))}
                elif act == "set_anonymous":
                    auth.cfg["anonymous_role"] = str(body.get("role") or "")
                    from ..auth import save_security
                    save_security(auth.cfg)
                    auth.reload()
                    out = {"ok": True, "anonymous_role": auth.anonymous_role}
                elif act == "set_cli":
                    c = auth.cfg.setdefault("cli", {})
                    if "default_role" in body:
                        c["default_role"] = str(body.get("default_role") or "admin")
                    if "require_login" in body:
                        c["require_login"] = bool(body.get("require_login"))
                    from ..auth import save_security
                    save_security(auth.cfg)
                    auth.reload()
                    out = {"ok": True, "cli": auth.cfg.get("cli")}
                else:
                    return self._json({"error": "unknown action"}, 400)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            auth.audit(user, "security " + act, level, True, self._ip(), detail={k: v for k, v in body.items() if k not in ("_password",)})
            return self._json(out)
        if u.path == "/api/apikeys":
            act = body.get("action") or "list"
            try:
                if act == "add":
                    r = auth.add_api_key(str(body.get("name") or ""), str(body.get("role") or "viewer"), str(body.get("note") or ""))
                    auth.audit(user, "apikeys add", level, True, self._ip(), detail={"name": r["name"], "role": r["role"], "id": r["id"]})
                    return self._json(dict(r, keys=auth.list_api_keys()))
                if act == "remove":
                    ok = auth.remove_api_key(str(body.get("id") or ""))
                    auth.audit(user, "apikeys remove", level, ok, self._ip(), detail={"id": body.get("id")})
                    return self._json({"ok": ok, "keys": auth.list_api_keys()}, 200 if ok else 404)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"keys": auth.list_api_keys()})
        if u.path == "/api/snapshot":
            act = body.get("action") or "create"
            p = self.pipe
            with _LOCK:
                if act == "create":
                    r = _snap.create(p, str(body.get("tag") or "manual"), actor=user.name if user else "", reason=str(body.get("reason") or ""))
                elif act == "restore":
                    r = _snap.restore(p, str(body.get("name") or ""))
                elif act == "prune":
                    r = {"removed": _snap.prune(p, int(body.get("keep") or 3))}
                else:
                    return self._json({"error": "unknown action"}, 400)
            auth.audit(user, "snapshot " + act, level, True, self._ip(), detail={"name": body.get("name"), "tag": body.get("tag")})
            return self._json(r)
        if u.path == "/api/cli" and level in ("destructive", "rebuild"):
            argv = body.get("argv") or []
            if isinstance(argv, str):
                import shlex
                argv = shlex.split(argv, posix=True)
            body["argv"] = list(argv) + (["--yes"] if "--yes" not in argv else [])   # 서버 게이트가 이미 확인했으므로 CLI 프롬프트 생략
        body["_actor"] = user.name if user else ""
        self._dispatch_post(u, body)
        if level != "read":
            auth.audit(user, op, level, True, self._ip(), detail={k: v for k, v in body.items() if k not in ("_password", "_actor", "content", "rules", "agents", "sources", "presets")})

    def _users_admin(self, body: Dict[str, Any], user: Optional[User]) -> None:
        auth = self._auth()
        act = body.get("action") or "list"
        try:
            if act == "add":
                auth.add_user(str(body.get("name") or ""), body.get("password"), str(body.get("role") or "viewer"), str(body.get("display") or ""))
            elif act == "remove":
                if user and body.get("name") == user.name:
                    return self._json({"error": "자기 자신은 삭제할 수 없습니다"}, 400)
                if not auth.remove_user(str(body.get("name") or "")):
                    return self._json({"error": "no such user"}, 404)
            elif act == "set_role":
                if user and body.get("name") == user.name and str(body.get("role")) != "admin":
                    return self._json({"error": "자기 자신의 admin 권한은 내릴 수 없습니다 (다른 admin 이 변경)"}, 400)
                auth.set_role(str(body.get("name") or ""), str(body.get("role") or "viewer"))
            elif act == "set_password":
                auth.set_password(str(body.get("name") or ""), str(body.get("password") or ""))
            elif act != "list":
                return self._json({"error": "unknown action"}, 400)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        auth.audit(user, "users " + act, "admin", True, self._ip(), detail={"name": body.get("name"), "role": body.get("role")})
        return self._json({"ok": True, "users": auth.list_users()})

    def _dispatch_post(self, u, body: Dict[str, Any]) -> None:
        p = self.pipe
        ov = body.get("overrides") or {}
        actor = str(body.get("_actor") or "")
        try:
            if u.path == "/api/build":
                if body.get("channel"):
                    ch = str(body.get("channel"))
                    return self._json(self._start_job("build", lambda progress: self._do_build_channel(body, progress), label="build %s" % ch))
                return self._json(self._start_job("build", lambda progress: self._do_build(body, progress, actor),
                                                  label="build --full" if (body.get("full") or body.get("reset")) else "build (incremental)"))
            if u.path == "/api/eval":
                return self._json(self._start_job("eval", lambda progress: self._do_eval(body, progress)))
            if u.path == "/api/query":
                q = (body.get("q") or "").strip()
                if not q:
                    return self._json({"error": "empty query"}, 400)
                # 클라이언트가 준 progress_token 으로 bind → 응답을 기다리는 동안 GET /api/progress/<token> 으로 단계를 볼 수 있다
                tok = str(body.get("progress_token") or "")[:64]
                _pg.bind(tok, "query", q[:120])
                status = "error"
                try:
                    if not _LOCK.acquire(blocking=False):
                        _pg.note("다른 작업(빌드/평가/질의)이 끝나기를 기다리는 중…")
                        _LOCK.acquire()
                    try:
                        out = self._do_query(body, q, ov)
                    finally:
                        _LOCK.release()
                    status = "done"
                    return self._json(out)
                finally:
                    _pg.unbind(status)
            with _LOCK:
                if u.path == "/api/search":
                    from ..retrieval import fts_search, vector_search, graph_search
                    q, ch, k = body.get("q", ""), body.get("channel", "fts"), int(body.get("k", 8))
                    prof = Profiler("search", debug=p.s.debug_level)
                    with _with_overrides(p, ov):
                        if ch == "fts":
                            out = [{"chunk_id": c, "score": s, "snippet": sn} for c, s, sn in fts_search(p.store, q, k, p.store.synonyms(), prof)]
                        elif ch == "vector":
                            out = [{"chunk_id": c, "score": s} for c, s in vector_search(p.store, p.embedder, q, k, prof)]
                        else:
                            out = graph_search(p.store, q, k, p.s.graph_hops, prof)
                    tr = prof.finish()
                    rid = p.store.log_request("search", "%s: %s" % (ch, q), tr, None, {"channel": ch, "k": k}, keep=p.s.keep_requests)
                    return self._json({"result": out, "trace": tr, "request_id": rid,
                                       "cli": "python -m llmwiki search %s \"%s\" --k %d --json" % (ch, q, k)})
                if u.path in ("/api/config", "/api/models/set"):
                    apply_overrides(p.s, body.get("settings") or {})
                    save_settings(p.s)
                    p.reload()
                    return self._json({"settings": p.s.to_dict(), "providers": p.provider_status()})
                if u.path == "/api/models/test":
                    with _with_overrides(p, ov):
                        return self._json(p.test_providers(body.get("which"), live=bool(body.get("live"))))
                if u.path == "/api/maintenance":
                    return self._json(p.maintenance(body.get("action", "")))
                if u.path == "/api/tuning":
                    act = body.get("action", "set")
                    errors = {}
                    if act == "reset":
                        _tuning.T.reset(body.get("key"))
                    else:
                        cfg = {}
                        for k, v in (body.get("values") or {}).items():
                            spec = _tuning._INDEX.get(k)
                            if not spec:
                                errors[k] = "unknown"
                            elif spec["source"] == "config":
                                cfg[k] = v
                            else:
                                try:
                                    _tuning.T.set(k, v)
                                except (KeyError, ValueError) as e:
                                    errors[k] = str(e)
                        if cfg:
                            apply_overrides(p.s, cfg)
                            save_settings(p.s)
                    _tuning.save_tuning(_tuning.T)
                    p.reload_tuning()
                    if any(_tuning._INDEX.get(k, {}).get("source") == "config" for k in (body.get("values") or {})):
                        p.reload()
                    return self._json({"ok": not errors, "errors": errors, "overrides": _tuning.T.to_dict(),
                                       "tunables": _tuning.T.describe(p.s)})
                if u.path == "/api/watch":
                    act = body.get("action")
                    if act == "start":
                        p.s.toggles.auto_build = True
                        if body.get("interval"):
                            p.s.auto_build_interval = int(body["interval"])
                        if body.get("save"):
                            save_settings(p.s)
                        _ensure_watcher(p)
                    elif act == "stop":
                        p.s.toggles.auto_build = False
                        if body.get("save"):
                            save_settings(p.s)
                    elif act == "scan":
                        return self._json(p.check_changes())
                    elif act == "tick":
                        r = p.auto_build_tick(progress=lambda m: _WATCHER["log"].append("%s %s" % (time.strftime("%H:%M:%S"), m)))
                        return self._json(r)
                    return self._json(self._status()["watcher"])
                if u.path == "/api/feedback":
                    return self._json(ev.record_feedback(p, int(body["query_id"]), int(body["feedback"]), body.get("note", "")))
                if u.path == "/api/evolve/apply":
                    return self._json(ev.apply_proposal(p, int(body["id"]), evaluate=bool(body.get("evaluate", True))))
                if u.path == "/api/evolve/reject":
                    return self._json(ev.reject_proposal(p, int(body["id"]), body.get("note", "")))
                if u.path == "/api/evolve/review":
                    with _with_overrides(p, ov):
                        return self._json(ev.llm_review(p))
                if u.path == "/api/evolve/propose":
                    pid = p.store.add_proposal(body["kind"], body["payload"], body.get("reason", "manual"), float(body.get("confidence", 0.9)), "manual")
                    return self._json({"id": pid})
                if u.path == "/api/wiki/page":
                    name = body.get("name", "")
                    if not name or "/" in name or "\\" in name:
                        return self._json({"error": "bad name"}, 400)
                    os.makedirs(p.s.wiki_dir, exist_ok=True)
                    with open(os.path.join(p.s.wiki_dir, name + ".md"), "w", encoding="utf-8") as f:
                        f.write(body.get("content", ""))
                    return self._json({"ok": True})
                if u.path == "/api/cli":
                    from ..cli import run_captured
                    argv = body.get("argv") or []
                    if isinstance(argv, str):
                        import shlex
                        argv = shlex.split(argv, posix=True)
                    if argv and argv[0] in ("serve", "watch", "mcp") and "--once" not in argv:
                        return self._json({"code": 1, "output": "%s 는 콘솔에서 실행할 수 없습니다 (서버가 이미 실행 중; watch 는 --once 로)." % argv[0]})
                    return self._json(run_captured(argv, p.s, p, actor=str(body.get("_actor") or "web")))
                if u.path == "/api/rules":
                    from ..graph_rules import save_rules
                    save_rules(body.get("rules") or {})
                    p.reload()
                    return self._json({"ok": True})
                # ---------------- v3 ----------------
                if u.path == "/api/presets":
                    from .. import presets as _presets
                    act = body.get("action", "apply")
                    if act == "apply":
                        r = _presets.apply(p.s, _presets.parse_names(body.get("names") or ",".join(body.get("list") or [])), save=bool(body.get("save")))
                        p.reload_tuning(from_file=False)
                        if body.get("save"):
                            p.reload()
                        return self._json({k: v for k, v in r.items() if k != "prev"} | {"settings": p.s.to_dict()})
                    if act == "save":
                        _presets.save_presets(body.get("presets") or {})
                        return self._json({"ok": True})
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/prompts":
                    from .. import prompts as _prompts
                    if body.get("action") == "reset":
                        return self._json({"path": _prompts.reset(body["name"]), "content": _prompts.get(body["name"])})
                    return self._json({"path": _prompts.set_text(body["name"], body.get("content", "")), "content": _prompts.get(body["name"])})
                if u.path == "/api/pins":
                    from .. import pins as _pins
                    act = body.get("action", "add")
                    if act == "add":
                        return self._json(_pins.add_pin(doc=body.get("doc"), chunk=body.get("chunk"), query=body.get("query"), keywords_=body.get("keywords"),
                                                        always=bool(body.get("always")), doc_types=body.get("doc_types"), weight=float(body.get("weight") or 1.0),
                                                        note=body.get("note", ""), source="web"))
                    if act == "remove":
                        return self._json({"ok": _pins.remove_pin(body.get("id", ""))})
                    if act == "test":
                        return self._json(_pins.match_pins(p.store, body.get("q", "")))
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/query_rules":
                    from .. import query_rules as _qr
                    act = body.get("action", "save")
                    if act == "save":
                        _qr.save_rules(body.get("rules") or {})
                        p.reload_tuning()
                        return self._json({"ok": True, "stats": _qr.stats()})
                    if act == "add":
                        r = _qr.add_rule(body["type"], body["term"], body.get("values") or [], "web")
                        p.reload_tuning()
                        return self._json(r)
                    if act == "remove":
                        return self._json({"ok": _qr.remove_rule(body["type"], body["term"], body.get("value"))})
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/build/verify":
                    return self._json(p.store.verify(p.embedder.name if p.s.toggles.embed else None, fix=bool(body.get("fix")), wiki_dir=p.s.wiki_dir))
                if u.path == "/api/mcp_sources":
                    from .. import mcp_client as _mcp
                    act = body.get("action", "test")
                    if act == "test":
                        return self._json(_mcp.test_sources(p.s, body.get("names")))
                    if act == "ingest":
                        return self._json(_mcp.ingest(p.s, body.get("names"), since=body.get("since"), dry_run=bool(body.get("dry_run"))))
                    if act == "save":
                        _mcp.save_sources(body.get("sources") or {})
                        return self._json({"ok": True})
                    if act == "enrich":
                        return self._json(_mcp.enrich(p.s, body.get("q", "")))
                    if act == "retrieve":
                        return self._json({"results": _mcp.retrieve(p.s, body.get("q", ""), int(body.get("k") or 5), names=body.get("names"), include_fallback=True)})
                    if act == "tools":
                        cfg = _mcp.load_sources().get(str(body.get("name") or ""))
                        if not cfg:
                            return self._json({"error": "unknown source"}, 400)
                        return self._json({"name": body.get("name"), "tools": _mcp.remote_tools(str(body.get("name")), cfg)})
                    if act == "federated":
                        from .. import mcp as _m
                        tools = _m.federated_tools(p.s, refresh=True)
                        return self._json({"mcp_federation": p.s.toggles.mcp_federation, "tools": [t["name"] for t in tools],
                                           "errors": {k: v.get("error") for k, v in _m._FED_CACHE.items() if v.get("error")}, "plugins": _m.load_plugins(p.s)})
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/memory":
                    from .. import memory as _mem
                    act = body.get("action")
                    if act == "decay":
                        return self._json(_mem.decay(p.store, _tuning.T.get("memory_half_life_days"), _tuning.T.get("memory_archive_strength")))
                    if act == "consolidate":
                        return self._json(_mem.consolidate(p.store, _tuning.T.get("forensic_min_events")))
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/precompute":
                    from .. import precompute as _pc
                    act = body.get("action")
                    if act == "clear":
                        return self._json({"removed": _pc.clear_cache(p.store, stale_only=bool(body.get("stale")))})
                    if act == "doc_vectors":
                        return self._json(_pc.build_doc_vectors(p))
                    if act == "run":
                        return self._json(self._start_job("precompute", lambda progress: _pc.run(p, questions=body.get("questions"), from_log=int(body.get("from_log", 20)), progress=progress)))
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/trials":
                    from .. import trials as _tr
                    act = body.get("action", "run")
                    if act == "run":
                        qs_ = load_questions(body["questions"]) if body.get("questions") else None
                        ovr = body.get("sets") or None
                        name = body.get("name") or ("trial-%s" % time.strftime("%m%d-%H%M%S"))

                        def _run(progress, name=name, qs_=qs_, ovr=ovr):
                            progress("trial %s 시작" % name)
                            with _with_overrides(p, ov):
                                return _tr.run_trial(p, name, qs_, k=int(body.get("k", 5)), preset=body.get("preset"), overrides=ovr, note=body.get("note", ""))
                        return self._json(self._start_job("trial", _run))
                    if act == "delete":
                        p.store.conn.execute("DELETE FROM trials WHERE trial_id=?", (int(body["id"]),))
                        p.store.conn.commit()
                        return self._json({"ok": True})
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/fusion/compare":
                    from .. import fusion as _fusion
                    qs_ = load_questions(body["questions"]) if body.get("questions") else load_questions()
                    methods = body.get("methods")
                    return self._json(self._start_job("fusion", lambda progress: {"rows": _fusion.compare_methods(p, qs_, methods, int(body.get("k", 5)))}))
                if u.path == "/api/agents":
                    from .. import headless as _hl
                    _hl.save_agents(body.get("agents") or {})
                    p.reload()
                    return self._json({"ok": True})
                if u.path == "/api/forensic/expect":
                    from .. import forensic as _fx
                    rid = int(body.get("request_id", 0) or 0)
                    if not rid:
                        reqs = p.store.requests("query", 1)
                        rid = int(reqs[0]["id"]) if reqs else 0
                    docs_ = body.get("docs") or []
                    terms_ = body.get("terms") or []
                    if isinstance(docs_, str):
                        docs_ = [x.strip() for x in docs_.replace(";", ",").split(",") if x.strip()]
                    if isinstance(terms_, str):
                        terms_ = [x.strip() for x in terms_.replace(";", ",").split(",") if x.strip()]
                    rep = _fx.trace_expectation(p, rid, docs_, terms_, body.get("chunks") or [], note=str(body.get("note") or ""), propose=bool(body.get("propose")))
                    rep["text"] = _fx.format_expectation(rep)
                    return self._json(rep)
                if u.path == "/api/forensic/llm":
                    from .. import forensic as _fx
                    rid = int(body.get("request_id", 0))
                    req = p.store.get_request(rid)
                    if not req or not req.get("trace"):
                        return self._json({"error": "request not found"}, 404)
                    res = req.get("result") or {"query": req["summary"]}
                    diag = _fx.diagnose(req["trace"], res, p.s)
                    llm = p.llm_for("forensic")
                    extra = _fx.llm_forensic(llm, res.get("query", req["summary"]), req["trace"], diag, p.s.role_llm("forensic")["effort"]) if llm.available else None
                    return self._json({"heuristic": diag, "llm": extra, "available": llm.available})
            self.send_error(404)
        except Exception as e:
            self._json({"error": str(e), "trace": traceback.format_exc()}, 500)

    def _do_query(self, body: Dict[str, Any], q: str, ov: Dict[str, Any]) -> Dict[str, Any]:
        p = self.pipe
        preset = body.get("preset") or ""
        mode = body.get("mode") or ""
        names = [x for x in str(preset).replace(";", ",").split(",") if x.strip()]
        if mode == "deep":
            names.append("deep_research")
        elif mode == "fast":
            names.append("speed")
        with _with_overrides(p, ov):
            prev = None
            if names:
                from .. import presets as _presets
                prev = _presets.apply(p.s, names, save=False)["prev"]
                p.reload_tuning(from_file=False)   # 프리셋 튜닝값은 메모리에만 있음
            try:
                res, tr = p.query(q, log=bool(body.get("log", True)), debug=body.get("debug"))
            finally:
                if prev is not None:
                    from .. import presets as _presets
                    _presets.restore(p.s, prev)
                    p.reload_tuning(from_file=False)
        res["cli"] = _cli_equiv("query", q, ov, Toggles()) + (" --preset %s" % ",".join(names) if names else "")
        return {"result": res, "trace": tr}

    # ---------------- jobs ----------------
    def _start_job(self, kind: str, fn, label: str = "") -> Dict[str, Any]:
        jid = uuid.uuid4().hex[:8]
        job = {"id": jid, "kind": kind, "status": "running", "started": time.time(), "log": [], "result": None, "error": None}
        _JOBS[jid] = job
        # 끝난 job 은 최근 50개만 유지 (메모리)
        for k in [k for k, v in _JOBS.items() if v.get("status") != "running"][:-50]:
            _JOBS.pop(k, None)

        def progress(msg: str) -> None:
            job["log"].append("%s %s" % (time.strftime("%H:%M:%S"), msg))

        def runner() -> None:
            # job id 가 곧 progress token: /api/jobs/<id> 가 progress.get(id) 를 합쳐 준다
            _pg.bind(jid, kind, label or kind)
            status = "error"
            try:
                if not _LOCK.acquire(blocking=False):
                    progress("다른 작업(빌드/평가/질의/워처)이 끝나기를 기다리는 중…")
                    _pg.note("다른 작업이 끝나기를 기다리는 중…")
                    _LOCK.acquire()
                try:
                    job["result"] = fn(progress)
                finally:
                    _LOCK.release()
                job["status"] = status = "done"
            except Exception as e:
                job["status"] = "error"
                job["error"] = "%s\n%s" % (e, traceback.format_exc())
                progress("✖ 실패: %s" % str(e)[:300])
            finally:
                job["finished"] = time.time()
                _pg.unbind(status, "" if status == "done" else (job.get("error") or "")[:200])
        threading.Thread(target=runner, daemon=True).start()
        return {"job": jid}

    def _do_build(self, body: Dict[str, Any], progress, actor: str = "") -> Dict[str, Any]:
        p = self.pipe
        ov = body.get("overrides") or {}
        with _with_overrides(p, ov):
            do_reset = body.get("reset") if body.get("reset") is not None else bool(body.get("full"))
            if do_reset:
                if p.s.toggles.health_check and not body.get("force"):
                    # CLI 와 동일: 색인을 지우기 전에 health — 코퍼스 경로가 없으면 기존 색인을 지우지 않는다
                    from ..health import run_health
                    hr = run_health(p, quick=True, for_build=True)
                    if not hr["ok"]:
                        raise RuntimeError("health check failed before reset (기존 색인 유지): %s" % ", ".join(
                            c["name"] for c in hr["checks"] if not c["ok"] and c["level"] == "fail"))
                d = (self._auth().cfg.get("destructive") or {})
                r = p.reset_index(keep_logs=not body.get("purge_logs"), snapshot=bool(d.get("snapshot_before", True)), actor=actor,
                                  snapshot_keep=int(d.get("snapshot_keep", 3) or 3))
                progress("reset: cleared %d tables, kept_logs=%s, removed_wiki_pages=%d%s" % (
                    len(r["cleared_tables"]), r["kept_logs"], r["removed_wiki_pages"], (" · 스냅샷 %s (snapshot restore 로 복원 가능)" % r["snapshot"]) if r.get("snapshot") else ""))
            channels = body.get("channels") or None
            if isinstance(channels, str):
                channels = [x.strip() for x in channels.split(",") if x.strip()]
            res, tr = p.build(full=bool(body.get("full") or body.get("reset")), progress=progress, debug=body.get("debug"), channels=channels)
        flag = (" --full" if do_reset else " --full --no-reset") if (body.get("full") or do_reset) else ""
        if channels:
            flag += " --channels " + ",".join(channels)
        return {"result": res, "trace": tr, "cli": _cli_equiv("build", "", ov, Toggles()) + flag}

    def _do_build_channel(self, body: Dict[str, Any], progress) -> Dict[str, Any]:
        p = self.pipe
        ov = body.get("overrides") or {}
        ch = str(body.get("channel") or "")
        with _with_overrides(p, ov):
            res, tr = p.build_channel(ch, full=bool(body.get("full")), progress=progress, debug=body.get("debug"), force=bool(body.get("force")))
        return {"result": res, "trace": tr, "cli": "python -m llmwiki build %s%s --trace" % (ch, " --full" if body.get("full") else "")}

    def _do_eval(self, body: Dict[str, Any], progress) -> Dict[str, Any]:
        p = self.pipe
        ov = body.get("overrides") or {}
        k = int(body.get("k", 5))
        with _with_overrides(p, ov):
            if body.get("matrix"):
                combos = [("fts", 1, 0, 0), ("vector", 0, 1, 0), ("graph", 0, 0, 1), ("fts+vector", 1, 1, 0), ("fts+graph", 1, 0, 1), ("vector+graph", 0, 1, 1), ("all", 1, 1, 1)]
                rows = []
                for name, f, v, g in combos:
                    progress("eval combo " + name)
                    p.s.toggles.fts, p.s.toggles.vector, p.s.toggles.graph = bool(f), bool(v), bool(g)
                    r, _ = p.evaluate(k=k, log=False)
                    rows.append(dict(r["summary"], combo=name, rows=r["rows"]))
                return {"matrix": rows, "cli": "python -m llmwiki eval --matrix --k %d" % k}
            r, tr = p.evaluate(k=k, log=False)
            return {"result": r, "trace": tr, "cli": _cli_equiv("eval", "", ov, Toggles()).replace(" --trace", "") + " --k %d" % k}


def serve(pipe, host: str = "127.0.0.1", port: int = 8765, insecure: bool = False, mcp_only: bool = False) -> None:
    Handler.pipe = pipe
    Handler.host = host
    Handler.mcp_only = bool(mcp_only)
    Handler.auth = Auth(pipe.s, host)
    auth = Handler.auth
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if auth.mode == "on" and not auth.has_any_login() and not auth.anonymous_role:
        print("!! security.json 에 사용자(users)·API 키·SSO 가 없고 anonymous_role 도 비어 있어 아무도 접근할 수 없습니다. 먼저: python -m llmwiki users add <id> --role admin (또는 apikey add)")
        if not insecure:
            raise SystemExit(2)
    if auth.mode == "off" and not loopback:
        if not insecure:
            print("!! %s 에 바인드하면 네트워크의 누구나 접근합니다. security.json 의 users/sso 를 설정하고(mode auto → on) 실행하거나, 정말로 인증 없이 열려면 --insecure." % host)
            raise SystemExit(2)
        print("!! --insecure: 인증 없이 %s 에 공개합니다 (파괴적 작업은 확인 문구만 요구)" % host)
    if not mcp_only:
        _ensure_watcher(pipe)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print("%s: http://%s:%d/%s  (Ctrl+C to stop)%s  [auth: %s%s%s]" % (
        "LLM Wiki MCP (Streamable HTTP)" if mcp_only else "LLM Wiki UI", host, port, "mcp" if mcp_only else "  · MCP: POST /mcp",
        "  [auto_build on: every %ss]" % pipe.s.auto_build_interval if (pipe.s.toggles.auto_build and not mcp_only) else "",
        auth.mode, (", users=%d, api_keys=%d, sso=%s" % (len(auth.cfg.get("users") or {}), len(auth.cfg.get("api_keys") or {}), "on" if auth.public_info()["sso"] else "off")) if auth.mode == "on" else "",
        (", anonymous=%s" % auth.anonymous_role) if (auth.mode == "on" and auth.anonymous_role) else ""))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _WATCHER["stop"] = True
        httpd.server_close()
