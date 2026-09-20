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

    def _console():
        from . import console as _c
        d = _c.describe()
        detail = "stdout=%s locale=%s%s%s mode=%s" % (
            d.get("stdout_encoding"), d.get("locale_encoding"),
            (" console_cp=%s" % d["codepage_now"]) if d.get("codepage_now") else "",
            " (콘솔)" if d.get("console") else " (리디렉션)", d.get("mode"))
        return bool(d.get("safe")), detail + ("" if d.get("safe") else " — 한글/기호를 출력할 수 없습니다")
    checks.append(_check("console_encoding", _console, "warn",
                         "config.json console_encoding=utf-8 (또는 native) · Windows 는 chcp 65001 · Linux 는 LANG=ko_KR.UTF-8 / C.UTF-8"))

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

    # ---- 채널이 켜져 있는데 **비어 있지는 않은가** ----
    # 조용한 품질 손실을 잡는다: 토글은 켜져 있어 질의마다 graph_search 가 돌지만 색인에 엔티티가 없으면
    # 그 채널은 늘 0건이다. 오류가 아니라 아무 일도 일어나지 않으므로 로그로도 드러나지 않는다.
    # 실제로 겪은 경우: 코퍼스를 넣은 뒤 증분 빌드만 돌아(changed=0) 그래프가 한 번도 만들어지지 않았다.
    def _channels():
        st = pipe.store.stats()
        empty = []
        if t.graph and not st.get("entities"):
            empty.append("graph(entities=0) → `build graph`")
        if t.vector and st.get("chunks") and not st.get("embeddings"):
            empty.append("vector(embeddings=0) → `build vector`")
        if t.doc_vector:
            try:
                n = pipe.store.conn.execute("SELECT COUNT(*) FROM doc_vectors").fetchone()[0]
            except Exception:
                n = 0
            if not n:
                empty.append("doc_vector(doc_vectors=0) → `precompute run` 또는 `build vector`")
        detail = "켜진 채널이 모두 채워져 있음" if not empty else "비어 있는 채널: " + " · ".join(empty)
        return not empty, detail
    checks.append(_check("channels_populated", _channels, "warn",
                         "토글은 켜져 있는데 색인이 비어 있으면 그 채널은 늘 0건입니다 — 해당 채널만 다시 빌드하세요 (`build graph|vector`)"))

    # ---- auto 가 진짜 임베딩 모델을 못 찾아 hash 로 내려앉았는가 ----
    # hash 임베딩은 **의미가 아니라 어휘**를 본다. 오프라인 모드로는 정당한 선택이지만,
    # `auto` 로 두고 모델이 없어서 hash 가 된 경우에는 본인도 모르는 사이에 벡터 채널이
    # FTS 와 거의 같은 신호가 된다 (하이브리드의 이점이 사라진다). 명시적으로 hash 를 고른 사람은 건드리지 않는다.
    def _embed_quality():
        emb = pipe.embedder
        st = pipe.store.stats()
        auto = str(s.embed_provider or "auto").strip().lower() == "auto"
        if not (auto and getattr(emb, "name", "") == "hash" and t.vector):
            return True, "embedder=%s (provider=%s)" % (getattr(emb, "name", "?"), s.embed_provider)
        big = int(st.get("chunks") or 0) >= 500
        detail = ("embed_provider=auto 인데 임베딩 모델을 찾지 못해 hash 로 동작 중 "
                  "(청크 %d개). hash 는 어휘 기반이라 벡터 채널이 FTS 와 비슷해집니다" % st.get("chunks", 0))
        return (not big), detail
    # ---- 코퍼스에 **이 도구 자신**이 섞여 있는가 ----
    # 자기 소스를 색인하면 도메인 문서를 밀어낸다. 오류가 아니라 순위가 조용히 나빠질 뿐이라
    # 아무도 눈치채지 못한다. 실측(2026-09-17): 이 도구의 소스·문서 150개를 제외했더니
    # hit@k 0.64 → 0.88, MRR 0.396 → 0.676.
    def _self_index():
        rows = pipe.store.conn.execute(
            "SELECT doc_id FROM docs WHERE doc_id LIKE '%llmwiki/%' OR doc_id LIKE '%/js/NOTE-%' "
            "OR doc_id LIKE '%tools/verify%' OR doc_id LIKE '%/schemas/NOTE-%' OR doc_id LIKE '%/prompts/NOTE-%'").fetchall()
        n = len(rows)
        total = int((pipe.store.stats() or {}).get("docs") or 0)
        if not n:
            return True, "코퍼스에 도구 자신의 파일 없음 (문서 %d개)" % total
        pct = 100.0 * n / max(1, total)
        ex = ", ".join(r[0] for r in rows[:3])
        return (pct < 5), ("이 도구 자신의 소스·문서로 보이는 문서 %d개 (%.0f%%): %s …" % (n, pct, ex))
    checks.append(_check("corpus_self_index", _self_index, "warn",
                         "코퍼스에서 빼거나 config.json 의 corpus_exclude 에 추가하세요 "
                         "(예: [\"imported/llmwiki/\", \"imported/js/\", \"imported/tools/\"]) → 다음 빌드에서 색인에서 빠집니다"))

    checks.append(_check("embedder_quality", _embed_quality, "warn",
                         "Ollama 면 `ollama pull bge-m3` 후 `build vector --full` · 게이트웨이면 embed_provider=openai + openai_embed_model · "
                         "의도적으로 오프라인이면 embed_provider=hash 로 명시하세요 (이 경고가 사라집니다)"))

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
