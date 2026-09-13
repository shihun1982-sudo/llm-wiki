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
from typing import Any, Dict, Optional
from urllib.parse import urlparse, parse_qs

from ..config import apply_overrides, save_settings, Toggles, Settings, TOGGLE_HELP, SETTING_HELP, TOGGLE_GROUPS, effective_settings, all_paths, path_for
from .. import tuning as _tuning
from ..architecture import registry as _arch_registry
from ..profiler import jsonable, Profiler
from ..evalset import load_questions
from .. import evolve as ev

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_LOCK = threading.RLock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_WATCHER: Dict[str, Any] = {"thread": None, "stop": False, "log": []}
PROVIDER_KEYS = ("llm_provider", "embed_provider", "llm_model", "embed_model", "llm_roles", "embed_dim", "ollama_url", "ollama_model",
                 "openai_base_url", "openai_embed_model", "rerank_url", "rerank_model", "rerank_api_style", "embed_store_dtype")


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
                with _LOCK:
                    r = pipe.auto_build_tick(progress=lambda m: _WATCHER["log"].append("%s %s" % (time.strftime("%H:%M:%S"), m)))
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

    def log_message(self, fmt, *args):  # 조용히
        pass

    # ---------------- helpers ----------------
    def _json(self, obj: Any, code: int = 200) -> None:
        data = json.dumps(jsonable(obj), ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
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
        ctype = {"html": "text/html", "js": "application/javascript", "css": "text/css", "svg": "image/svg+xml"}.get(path.rsplit(".", 1)[-1], "application/octet-stream")
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

    # ---------------- GET ----------------
    def do_GET(self) -> None:
        u = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = self.pipe
        try:
            if u.path in ("/", "/index.html"):
                return self._static("index.html")
            if u.path.startswith("/static/"):
                return self._static(u.path[len("/static/"):])
            with _LOCK:
                if u.path == "/api/status":
                    return self._json(self._status())
                if u.path.startswith("/api/jobs/"):
                    j = _JOBS.get(u.path.rsplit("/", 1)[-1])
                    return self._json(j or {"error": "no such job"}, 200 if j else 404)
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
                    return self._json({"sources": _mcp.load_sources(), "path": _mcp.sources_path(), "enabled": p.s.toggles.mcp_sources})
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
        except Exception as e:
            self._json({"error": str(e), "trace": traceback.format_exc()}, 500)

    # ---------------- POST ----------------
    def do_POST(self) -> None:
        u = urlparse(self.path)
        body = self._body()
        p = self.pipe
        ov = body.get("overrides") or {}
        try:
            if u.path == "/api/build":
                return self._json(self._start_job("build", lambda progress: self._do_build(body, progress)))
            if u.path == "/api/eval":
                return self._json(self._start_job("eval", lambda progress: self._do_eval(body, progress)))
            with _LOCK:
                if u.path == "/api/query":
                    q = (body.get("q") or "").strip()
                    if not q:
                        return self._json({"error": "empty query"}, 400)
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
                    return self._json({"result": res, "trace": tr})
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
                        return self._json(p.test_providers(body.get("which")))
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
                    if argv and argv[0] in ("serve", "watch") and "--once" not in argv:
                        return self._json({"code": 1, "output": "%s 는 콘솔에서 실행할 수 없습니다 (서버가 이미 실행 중; watch 는 --once 로)." % argv[0]})
                    return self._json(run_captured(argv, p.s, p))
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

    # ---------------- jobs ----------------
    def _start_job(self, kind: str, fn) -> Dict[str, Any]:
        jid = uuid.uuid4().hex[:8]
        job = {"id": jid, "kind": kind, "status": "running", "started": time.time(), "log": [], "result": None, "error": None}
        _JOBS[jid] = job

        def progress(msg: str) -> None:
            job["log"].append("%s %s" % (time.strftime("%H:%M:%S"), msg))

        def runner() -> None:
            try:
                with _LOCK:
                    job["result"] = fn(progress)
                job["status"] = "done"
            except Exception as e:
                job["status"] = "error"
                job["error"] = "%s\n%s" % (e, traceback.format_exc())
            job["finished"] = time.time()
        threading.Thread(target=runner, daemon=True).start()
        return {"job": jid}

    def _do_build(self, body: Dict[str, Any], progress) -> Dict[str, Any]:
        p = self.pipe
        ov = body.get("overrides") or {}
        with _with_overrides(p, ov):
            do_reset = body.get("reset") if body.get("reset") is not None else bool(body.get("full"))
            if do_reset:
                r = p.reset_index(keep_logs=not body.get("purge_logs"))
                progress("reset: cleared %d tables, kept_logs=%s, removed_wiki_pages=%d" % (len(r["cleared_tables"]), r["kept_logs"], r["removed_wiki_pages"]))
            res, tr = p.build(full=bool(body.get("full") or body.get("reset")), progress=progress, debug=body.get("debug"))
        flag = (" --full" if do_reset else " --full --no-reset") if (body.get("full") or do_reset) else ""
        return {"result": res, "trace": tr, "cli": _cli_equiv("build", "", ov, Toggles()) + flag}

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


def serve(pipe, host: str = "127.0.0.1", port: int = 8765) -> None:
    Handler.pipe = pipe
    _ensure_watcher(pipe)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print("LLM Wiki UI: http://%s:%d/  (Ctrl+C to stop)%s" % (host, port, "  [auto_build on: every %ss]" % pipe.s.auto_build_interval if pipe.s.toggles.auto_build else ""))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _WATCHER["stop"] = True
        httpd.server_close()
