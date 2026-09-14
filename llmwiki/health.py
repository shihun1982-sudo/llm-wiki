# -*- coding: utf-8 -*-
"""Health check — 빌드/운영 전에 환경·프로바이더·DB·디스크·코퍼스를 점검한다.

각 항목: {"name", "ok", "level": "fail|warn|info", "detail", "ms", "fix"}.
`python -m llmwiki health` · 빌드 시 자동(toggles.health_check) · Web /api/health.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional


def _check(name: str, fn, level: str = "fail", fix: str = "") -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        ok, detail = fn()
        out = {"name": name, "ok": bool(ok), "level": level if not ok else "info", "detail": detail}
    except Exception as e:
        out = {"name": name, "ok": False, "level": level, "detail": "%s: %s" % (type(e).__name__, str(e)[:300])}
    out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    if not out["ok"] and fix:
        out["fix"] = fix
    return out


def run_health(pipe, quick: bool = False, for_build: bool = False) -> Dict[str, Any]:
    """quick=True 면 네트워크 ping(LLM/임베더/rerank/MCP) 생략. for_build=True 면 빌드에 필요한 역할만 ping."""
    s, t = pipe.s, pipe.s.toggles
    checks: List[Dict[str, Any]] = []

    # ---- 환경 ----
    checks.append(_check("python", lambda: (sys.version_info >= (3, 9), "Python %s" % sys.version.split()[0]),
                         "warn", "Python 3.9+ 권장"))
    def _fts5():
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(a)")
        return True, "sqlite %s, FTS5 ok" % sqlite3.sqlite_version
    checks.append(_check("sqlite_fts5", _fts5))

    # ---- DB ----
    def _db():
        r = pipe.store.conn.execute("PRAGMA quick_check").fetchone()[0]
        st = pipe.store.stats()
        return r == "ok", "quick_check=%s docs=%s chunks=%s embeddings=%s" % (r, st["docs"], st["chunks"], st["embeddings"])
    checks.append(_check("db_integrity", _db, fix="DB 손상: 백업 후 build --full"))
    wal = s.db_path + "-wal"
    wal_mb = os.path.getsize(wal) / 1e6 if os.path.exists(wal) else 0.0
    checks.append(_check("wal_size", lambda: (wal_mb < max(4 * s.wal_checkpoint_mb, 256), "wal=%.1fMB (임계 %sMB)" % (wal_mb, s.wal_checkpoint_mb)),
                         "warn", "maintenance wal_checkpoint"))
    def _disk():
        d = s.data_dir if os.path.isdir(s.data_dir) else os.path.dirname(s.db_path) or "."
        u = shutil.disk_usage(d)
        free_gb = u.free / 1e9
        return free_gb > 1.0, "free %.1f GB at %s" % (free_gb, d)
    checks.append(_check("disk_free", _disk, "warn", "디스크 여유 1GB 미만"))

    # ---- 코퍼스 ----
    def _corpus():
        missing = [d for d in s.corpus_dirs if not os.path.isdir(d)]
        n = 0
        for d in s.corpus_dirs:
            if os.path.isdir(d):
                for _b, _dirs, files in os.walk(d):
                    n += sum(1 for f in files if f.lower().endswith((".md", ".txt", ".csv", ".html", ".htm", ".pdf")))
        return (not missing and n > 0), "files=%d dirs=%d missing=%s" % (n, len(s.corpus_dirs), missing)
    checks.append(_check("corpus_dirs", _corpus, fix="config set corpus_dirs=경로"))

    # ---- 벡터 메모리 전망 ----
    def _mem():
        st = pipe.store.stats()
        emb = pipe.embedder
        dim = int(getattr(emb, "dim", 0) or s.embed_dim)
        bytes_per = 2 if s.embed_store_dtype == "float16" else 4
        mb = st["chunks"] * dim * bytes_per / 1e6
        return mb < 2000, "chunks=%d dim=%d dtype=%s → matrix≈%.0fMB" % (st["chunks"], dim, s.embed_store_dtype, mb)
    checks.append(_check("vector_memory", _mem, "warn", "embed_dim 축소 또는 embed_store_dtype=float16"))

    # ---- 저장된 임베딩 차원 vs 현재 임베더 ----
    def _dim():
        row = pipe.store.conn.execute("SELECT provider, dim, COUNT(*) n FROM embeddings GROUP BY provider, dim ORDER BY n DESC").fetchall()
        if not row:
            return True, "no embeddings yet"
        emb = pipe.embedder
        cur = (emb.name, int(getattr(emb, "dim", 0) or 0))
        stored = [(r["provider"], int(r["dim"]), int(r["n"])) for r in row]
        match = any(p == cur[0] and (cur[1] == 0 or d == cur[1]) for p, d, _ in stored)
        return match, "stored=%s current=%s" % (stored, cur)
    checks.append(_check("embedding_dim", _dim, "warn", "임베더/차원이 바뀌었으면 build --full"))

    if not quick:
        # ---- 프로바이더 ping ----
        roles: List[str] = []
        if for_build:
            if t.llm_graph:
                roles.append("extract")
            if t.community_summary:
                roles.append("summary")
        else:
            roles = ["answer", "rerank"]
            if t.query_expand or t.query_decompose:
                roles.append("expand")
            if t.evidence_check_llm or t.claim_check_llm:
                roles.append("verify")
        for r in roles:
            llm = pipe.llm_for(r)
            if llm.name == "none":
                checks.append({"name": "llm_%s" % r, "ok": True, "level": "info", "detail": "provider=none (폴백 사용)", "ms": 0})
                continue
            def _ping(llm=llm):
                p = llm.ping()
                return p.get("ok", False), "%s/%s %s" % (llm.name, llm.model, p.get("detail", ""))
            checks.append(_check("llm_%s" % r, _ping, "warn", ".env 키 / 모델명 / 엔드포인트 확인 (models test)"))
        if t.embed:
            def _emb():
                p = pipe.embedder.ping()
                return p.get("ok", False), "%s/%s dim=%s %s" % (pipe.embedder.name, getattr(pipe.embedder, "model", None), p.get("dim"), p.get("detail", ""))
            checks.append(_check("embedder", _emb, fix="embed_provider/모델/키 확인 (models test)"))
        if s.rerank_url:
            def _rr():
                from .rerankers import ping_rerank_api
                p = ping_rerank_api(s)
                return p.get("ok", False), p.get("detail", "")
            checks.append(_check("rerank_api", _rr, "warn", "rerank_url/rerank_api_model/RERANK_API_KEY 확인"))
        if t.mcp_sources:
            def _mcp():
                from .mcp_client import test_sources
                rs = test_sources(s)
                bad = [r["name"] for r in rs if not r.get("ok")]
                return not bad, "sources=%d failed=%s" % (len(rs), bad)
            checks.append(_check("mcp_sources", _mcp, "warn", "mcp_sources.json 명령/인증 확인"))

    # ---- 스키마 lint 요약 (있으면) ----
    try:
        from . import schema as _schema
        if t.schema_lint and hasattr(_schema, "lint_summary"):
            def _lint():
                r = _schema.lint_summary(pipe)
                return r.get("errors", 0) == 0, "docs=%s errors=%s warnings=%s" % (r.get("docs"), r.get("errors"), r.get("warnings"))
            checks.append(_check("schema_lint", _lint, "warn", "corpus lint 로 상세 확인"))
    except ImportError:
        pass

    fails = [c for c in checks if not c["ok"] and c["level"] == "fail"]
    warns = [c for c in checks if not c["ok"] and c["level"] == "warn"]
    return {"ok": not fails, "fails": len(fails), "warnings": len(warns), "checks": checks, "ts": time.time(),
            "python": sys.version.split()[0]}


def format_health(r: Dict[str, Any]) -> str:
    lines = ["health: %s (fail=%d warn=%d)" % ("OK" if r["ok"] else "FAIL", r["fails"], r["warnings"])]
    for c in r["checks"]:
        mark = "✔" if c["ok"] else ("✘" if c["level"] == "fail" else "△")
        lines.append("  %s %-16s %s%s" % (mark, c["name"], c["detail"], ("  → " + c["fix"]) if c.get("fix") else ""))
    return "\n".join(lines)
