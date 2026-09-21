# -*- coding: utf-8 -*-
"""운영 통계 — 관리자가 실제로 묻는 것에 답한다 (읽기 전용).

왜 이 모듈이 있나 (2026-09-20):
  옵저빌리티 › 시스템 화면은 **색인 규모**(문서·청크·엔티티 수)와 캐시·워처·유지보수 버튼만 보여 줬다.
  그런데 운영자가 실제로 묻는 것은 다른 쪽이다 — "빌드가 왜 느린가", "질의가 얼마나 몰리나",
  "토큰을 어디에 쓰나", "근거를 못 찾은 질의가 얼마나 되나", "디스크가 어디서 커지나".
  그 답에 필요한 데이터는 **이미 DB 에 다 있었다**(`requests` 의 단계 trace·토큰, `query_log` 의 피드백,
  `embed_runs`, `forensics`). 읽지 않고 있었을 뿐이다.

설계
  - **읽기 전용.** 어떤 것도 바꾸지 않는다.
  - **기간을 받는다**(기본 7일). 큰 DB 에서 화면이 멈추지 않도록 집계에 상한을 둔다.
  - 계산에 쓴 표본 수를 같이 돌려준다 — "3건으로 낸 p95" 를 숫자만 보고 믿지 않도록.
  - 섹션을 골라 부를 수 있다(`sections`) — 화면은 필요한 것만, CLI `stats --full` 은 전부.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

SECTIONS = ("index", "build", "queries", "latency", "tokens", "quality", "users", "storage", "embed", "trend")

#: 추세 묶음 — 일/주/월. 화면이 막대·꺾은선으로 그리고, CLI 는 같은 값을 스파크라인으로 찍는다.
BUCKETS = ("day", "week", "month")
#: 묶음별 기본 기간(일) — "월간" 을 7일 데이터로 그리면 막대가 하나뿐이라 뜻이 없다.
BUCKET_DEFAULT_DAYS = {"day": 14, "week": 84, "month": 365}


def _bucket_key(ts: float, bucket: str) -> str:
    """타임스탬프 → 묶음 키. 주는 ISO 주(월요일 시작), 월은 YYYY-MM."""
    lt = time.localtime(ts)
    if bucket == "month":
        return time.strftime("%Y-%m", lt)
    if bucket == "week":
        iso = time.strftime("%G-W%V", lt)      # ISO 연도-주 (연말 걸침을 바르게 처리한다)
        return iso
    return time.strftime("%Y-%m-%d", lt)

#: 섹션마다 "무엇을 답하나" — 화면·CLI·MCP 가 같은 설명을 쓴다.
SECTION_HELP = {
    "index": "색인 규모 — 문서·청크·임베딩·그래프가 얼마나 있나",
    "build": "빌드 — 마지막 빌드가 언제·얼마나 걸렸고 어느 단계가 느린가",
    "queries": "질의량 — 하루에 몇 건, 어디서(창구), 언제 몰리나",
    "latency": "지연 — p50/p95/최대와 가장 느린 질의",
    "tokens": "토큰 — 질의당 평균과 총량, 어느 단계가 쓰나",
    "quality": "품질 신호 — 근거 부족·fallback·👎 피드백·포렌식",
    "users": "사용자 — 누가 얼마나 쓰나 (상위)",
    "storage": "디스크 — DB 테이블별·데이터 폴더별 크기",
    "embed": "임베딩 — 최근 실행의 처리량·캐시 적중·실패",
    "trend": "추세 — 일/주/월 단위로 질의량·지연·토큰·빌드가 어떻게 변해 왔나",
}


def _rows(store, sql: str, args=()) -> List[Any]:
    try:
        return store.conn.execute(sql, args).fetchall()
    except Exception:
        return []


def _one(store, sql: str, args=(), default=0):
    r = _rows(store, sql, args)
    return r[0][0] if r and r[0][0] is not None else default


def _pct(vals: List[float], q: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(len(s) * q))], 1)


def _dir_size(path: str) -> int:
    try:
        return sum(os.path.getsize(os.path.join(dp, f))
                   for dp, _dn, fs in os.walk(path) for f in fs)
    except OSError:
        return 0


def collect(pipe, days: float = 7.0, sections: Optional[List[str]] = None, top: int = 8,
            max_rows: int = 5000, bucket: str = "day", trend_days: Optional[float] = None) -> Dict[str, Any]:
    """운영 통계. `sections` 를 주면 그것만 (기본 전부).

    bucket/trend_days 는 `trend` 절에만 쓴다 — 추세는 다른 절보다 **긴 기간**을 봐야 뜻이 생겨서
    기간을 따로 둔다(주간은 12주, 월간은 1년이 기본).
    """
    store = pipe.store
    want = [s for s in (sections or SECTIONS) if s in SECTIONS] or list(SECTIONS)
    since = time.time() - max(0.0, float(days)) * 86400.0
    out: Dict[str, Any] = {"days": days, "since": since, "generated": time.time(),
                           "sections": want, "help": {k: SECTION_HELP[k] for k in want}}

    if "index" in want:
        st = store.stats()
        out["index"] = {k: st.get(k) for k in ("docs", "chunks", "embeddings", "entities", "relations",
                                               "mentions", "communities", "synonyms", "db_bytes")}
        out["index"]["doc_types"] = store.doc_type_counts()
        out["index"]["chunks_per_doc"] = round((st.get("chunks") or 0) / max(1, st.get("docs") or 1), 1)
        # 색인은 있는데 임베딩이 빠진 청크 — 벡터 채널이 조용히 반쪽이 되는 자리
        out["index"]["chunks_without_embedding"] = max(0, (st.get("chunks") or 0) - (st.get("embeddings") or 0))

    if "build" in want:
        # `stats()["last_build"]` 가 정식 자리다 — kv_get("last_build") 는 모양이 달라 필드가 비어 보인다.
        lb = (store.stats() or {}).get("last_build") or store.kv_get("last_build") or {}
        b = {"last": {k: lb.get(k) for k in ("ts", "mode", "docs", "changed", "removed")},
             "alerts": lb.get("alerts") or []}
        # 빌드 요청의 단계별 시간 (가장 최근 빌드 trace)
        rr = _rows(store, "SELECT id, ts, ms, trace FROM requests WHERE kind='build' ORDER BY id DESC LIMIT 1")
        if rr:
            b["last_request_id"], b["last_ms"] = rr[0][0], rr[0][2]
            try:
                tr = json.loads(rr[0][3]) if isinstance(rr[0][3], str) else rr[0][3]
            except Exception:
                tr = None
            stages = []
            for ch in ((tr or {}).get("children") or []):
                stages.append({"stage": ch.get("name"), "ms": round(float(ch.get("ms") or 0), 1)})
            stages.sort(key=lambda x: -(x["ms"] or 0))
            b["slowest_stages"] = stages[:top]
        hist = _rows(store, "SELECT ts, ms FROM requests WHERE kind='build' AND ts>=? ORDER BY ts DESC LIMIT 50", (since,))
        b["recent"] = [{"ts": r[0], "ms": r[1]} for r in hist]
        b["n_builds"] = len(hist)
        out["build"] = b

    qrows = _rows(store, "SELECT ts, origin, user, feedback, request_id FROM query_log WHERE ts>=? "
                         "ORDER BY id DESC LIMIT ?", (since, max_rows)) if ({"queries", "quality", "users"} & set(want)) else []

    if "queries" in want:
        by_origin: Dict[str, int] = {}
        by_day: Dict[str, int] = {}
        by_hour = [0] * 24
        for ts, origin, _u, _f, _r in qrows:
            by_origin[str(origin or "?")] = by_origin.get(str(origin or "?"), 0) + 1
            d = time.strftime("%Y-%m-%d", time.localtime(ts))
            by_day[d] = by_day.get(d, 0) + 1
            by_hour[int(time.strftime("%H", time.localtime(ts)))] += 1
        out["queries"] = {"total": len(qrows), "per_day": round(len(qrows) / max(1.0, days), 1),
                          "by_origin": dict(sorted(by_origin.items(), key=lambda kv: -kv[1])),
                          "by_day": dict(sorted(by_day.items())), "by_hour": by_hour,
                          "busiest_hour": (by_hour.index(max(by_hour)) if any(by_hour) else None),
                          "capped": len(qrows) >= max_rows}

    if {"latency", "tokens"} & set(want):
        rq = _rows(store, "SELECT id, ts, kind, summary, ms, llm_calls, input_tokens, output_tokens FROM requests "
                          "WHERE ts>=? AND kind IN ('query','search') ORDER BY id DESC LIMIT ?", (since, max_rows))
        ms_vals = [float(r[4] or 0) for r in rq if r[4] is not None]
        if "latency" in want:
            slow = sorted(rq, key=lambda r: -(r[4] or 0))[:top]
            out["latency"] = {"n": len(ms_vals), "p50_ms": _pct(ms_vals, 0.5), "p95_ms": _pct(ms_vals, 0.95),
                              "max_ms": round(max(ms_vals), 1) if ms_vals else None,
                              "avg_ms": round(sum(ms_vals) / len(ms_vals), 1) if ms_vals else None,
                              "slowest": [{"request_id": r[0], "ts": r[1], "kind": r[2],
                                           "summary": str(r[3] or "")[:80], "ms": r[4]} for r in slow]}
        if "tokens" in want:
            tin = sum(int(r[6] or 0) for r in rq)
            tout = sum(int(r[7] or 0) for r in rq)
            calls = sum(int(r[5] or 0) for r in rq)
            n = max(1, len(rq))
            top_tok = sorted(rq, key=lambda r: -((r[6] or 0) + (r[7] or 0)))[:top]
            out["tokens"] = {"n_requests": len(rq), "input": tin, "output": tout, "total": tin + tout,
                             "per_query": round((tin + tout) / n, 1), "llm_calls": calls,
                             "calls_per_query": round(calls / n, 2),
                             "heaviest": [{"request_id": r[0], "summary": str(r[3] or "")[:80],
                                           "tokens": int(r[6] or 0) + int(r[7] or 0)} for r in top_tok]}

    if "quality" in want:
        neg = sum(1 for _t, _o, _u, f, _r in qrows if isinstance(f, (int, float)) and f < 0)
        pos = sum(1 for _t, _o, _u, f, _r in qrows if isinstance(f, (int, float)) and f > 0)
        insuf = _one(store, "SELECT COUNT(*) FROM requests WHERE ts>=? AND kind='query' AND result LIKE '%\"answer_mode\": \"insufficient\"%'", (since,))
        nq = _one(store, "SELECT COUNT(*) FROM requests WHERE ts>=? AND kind='query'", (since,))
        fx = _rows(store, "SELECT verdict, COUNT(*) FROM forensics WHERE ts>=? GROUP BY verdict", (since,))
        out["quality"] = {"queries": nq, "insufficient": insuf,
                          "insufficient_rate": round(insuf / max(1, nq), 3),
                          "feedback_positive": pos, "feedback_negative": neg,
                          "feedback_rate": round((pos + neg) / max(1, len(qrows)), 3),
                          "forensics": {str(r[0] or "?"): r[1] for r in fx},
                          "proposals_pending": _one(store, "SELECT COUNT(*) FROM proposals WHERE status='proposed'")}

    if "users" in want:
        by_user: Dict[str, int] = {}
        for _t, _o, u, _f, _r in qrows:
            by_user[str(u or "(익명)")] = by_user.get(str(u or "(익명)"), 0) + 1
        out["users"] = {"n": len(by_user),
                        "top": [{"user": k, "queries": v} for k, v in sorted(by_user.items(), key=lambda kv: -kv[1])[:top]]}

    if "storage" in want:
        s = pipe.s
        tables = []
        for t in ("chunks", "chunks_fts_data", "chunks_tri_data", "embeddings", "embedding_cache",
                  "relations", "mentions", "entities", "requests", "query_log", "forensics", "episodes"):
            n = _one(store, "SELECT COUNT(*) FROM %s" % t, default=None)
            if n:
                tables.append({"table": t, "rows": n})
        tables.sort(key=lambda x: -x["rows"])
        dirs = []
        for name in ("requests", "reruns", "sweeps", "graph_profiles", "snapshots", "live"):
            p = os.path.join(s.data_dir, name)
            if os.path.isdir(p):
                b = _dir_size(p)
                if b:
                    dirs.append({"dir": name, "bytes": b,
                                 "files": sum(len(fs) for _dp, _dn, fs in os.walk(p))})
        dirs.sort(key=lambda x: -x["bytes"])
        try:
            from .config import path_for
            logs = _dir_size(path_for("logs_dir"))
        except Exception:
            logs = 0
        db_b = (store.stats() or {}).get("db_bytes") or 0
        hints = []
        for x in dirs:
            # 스냅샷은 색인 전체를 복사하므로 금방 DB 보다 커진다 — 초기화의 안전망이라 지우지는 않지만
            # 알려는 준다 (`snapshot prune --keep N` · config.json evolve_snapshot_keep).
            if x["dir"] == "snapshots" and x["bytes"] > db_b:
                hints.append("data/snapshots 가 %s 로 DB(%s)보다 큽니다 — `snapshot list` 확인 후 "
                             "`snapshot prune --keep 3` (보관 개수는 config.json evolve_snapshot_keep)"
                             % (_mb(x["bytes"]), _mb(db_b)))
            elif x["dir"] == "requests" and x["files"] > 500:
                hints.append("data/requests 파일 %d개(%s) — 보관 기간은 config.json requests_keep_days, "
                             "정리는 `reset logs`" % (x["files"], _mb(x["bytes"])))
        if logs > 100 * 1048576:
            hints.append("로그 %s — 총량 한도는 config.json log_total_max_mb, 정리는 `reset logs`" % _mb(logs))
        out["storage"] = {"db_bytes": db_b,
                          "tables": tables[:12], "data_dirs": dirs, "logs_bytes": logs,
                          "wiki_bytes": _dir_size(s.wiki_dir), "hints": hints}

    if "embed" in want:
        er = _rows(store, "SELECT run_id, ts_start, ts_end, provider, model, total, done, failed, cache_hits, "
                          "batches, avg_batch_ms, status FROM embed_runs ORDER BY rowid DESC LIMIT ?", (top,))
        runs = [{"run_id": r[0], "ts": r[1], "provider": r[3], "model": r[4], "total": r[5], "done": r[6],
                 "failed": r[7], "cache_hits": r[8], "batches": r[9], "avg_batch_ms": r[10], "status": r[11],
                 "elapsed_s": (round(float(r[2]) - float(r[1]), 1) if (r[1] and r[2]) else None)} for r in er]
        done = sum(int(x["done"] or 0) for x in runs)
        hits = sum(int(x["cache_hits"] or 0) for x in runs)
        out["embed"] = {"runs": runs, "cache_hit_rate": round(hits / max(1, done + hits), 3),
                        "failed": sum(int(x["failed"] or 0) for x in runs)}

    if "trend" in want:
        out["trend"] = _trend(store, bucket=bucket, days=trend_days, max_rows=max_rows)
    return out


def _trend(store, bucket: str = "day", days: Optional[float] = None, max_rows: int = 20000) -> Dict[str, Any]:
    """일/주/월 추세 — 질의 수 · 지연(p50/p95) · 토큰 · 빌드 횟수·소요.

    왜 따로 두나: 다른 절은 "지금 어떤가" 를 말하지만, 운영자가 실제로 묻는 것은 대개 **"나아지고
    있나, 나빠지고 있나"** 다. 한 시점의 p95 만으로는 답할 수 없다.

    기간은 묶음에 맞춘다 — '월간' 을 7일치로 그리면 막대가 하나뿐이라 아무 말도 하지 못한다
    (BUCKET_DEFAULT_DAYS).
    """
    bucket = bucket if bucket in BUCKETS else "day"
    days = float(days if days else BUCKET_DEFAULT_DAYS[bucket])
    since = time.time() - days * 86400.0
    q: Dict[str, Dict[str, Any]] = {}

    def slot(key):
        return q.setdefault(key, {"bucket": key, "queries": 0, "ms": [], "in_tok": 0, "out_tok": 0,
                                  "llm_calls": 0, "builds": 0, "build_ms": 0.0, "insufficient": 0, "down": 0, "up": 0})

    for ts, fb in _rows(store, "SELECT ts, feedback FROM query_log WHERE ts>=? ORDER BY id DESC LIMIT ?", (since, max_rows)):
        s = slot(_bucket_key(ts, bucket))
        s["queries"] += 1
        if fb is not None:
            if int(fb or 0) < 0:
                s["down"] += 1
            elif int(fb or 0) > 0:
                s["up"] += 1
    for ts, kind, ms, calls, itok, otok, summ in _rows(
            store, "SELECT ts, kind, ms, llm_calls, input_tokens, output_tokens, summary FROM requests "
                   "WHERE ts>=? AND kind IN ('query','build') ORDER BY id DESC LIMIT ?", (since, max_rows)):
        s = slot(_bucket_key(ts, bucket))
        if kind == "build":
            s["builds"] += 1
            s["build_ms"] += float(ms or 0)
            continue
        if ms is not None:
            s["ms"].append(float(ms))
        s["llm_calls"] += int(calls or 0)
        s["in_tok"] += int(itok or 0)
        s["out_tok"] += int(otok or 0)
        if summ and "insufficient" in str(summ):
            s["insufficient"] += 1

    points = []
    for key in sorted(q):
        s = q[key]
        n = max(1, s["queries"])
        points.append({"bucket": key, "queries": s["queries"],
                       "p50_ms": _pct(s["ms"], 0.5), "p95_ms": _pct(s["ms"], 0.95),
                       "tokens": s["in_tok"] + s["out_tok"],
                       "tokens_per_query": round((s["in_tok"] + s["out_tok"]) / n, 1),
                       "llm_calls_per_query": round(s["llm_calls"] / n, 2),
                       "builds": s["builds"], "build_ms": round(s["build_ms"], 1),
                       "insufficient": s["insufficient"],
                       "insufficient_rate": round(s["insufficient"] / n, 3),
                       "up": s["up"], "down": s["down"]})
    # 마지막 구간 vs 그 앞 구간 — "요즘 어떤가" 를 한 줄로
    trend_delta = {}
    if len(points) >= 2:
        a, b = points[-2], points[-1]
        for kk in ("queries", "p50_ms", "p95_ms", "tokens_per_query", "insufficient_rate"):
            if a.get(kk) is not None and b.get(kk) is not None:
                trend_delta[kk] = round(b[kk] - a[kk], 3)
    return {"bucket": bucket, "days": days, "points": points, "n_points": len(points),
            "delta_last": trend_delta,
            "note": ("구간이 %d개뿐입니다 — 기간을 늘리면 추세가 보입니다" % len(points)) if len(points) < 3 else ""}


# ------------------------------------------------------------------ 표시
def _mb(b) -> str:
    try:
        return "%.1f MB" % (float(b) / 1048576.0)
    except (TypeError, ValueError):
        return "-"


#: 관리자만 볼 수 있는 절 — 그 절이 없으면 무엇이 가려졌는지 화면이 말해 줄 수 있게 이유를 함께 둔다.
ADMIN_ONLY_SECTIONS = {
    "users": "사용자별 질의 집계는 admin 만 볼 수 있습니다 (활동 목록보다 민감합니다 — GET /api/query_users 와 같은 기준)",
}


def redact(d: Dict[str, Any], admin: bool) -> Dict[str, Any]:
    """역할에 따라 볼 수 없는 절을 덜어 낸다 (서버·MCP 가 내보내기 직전에 부른다).

    왜 집계 함수 안이 아니라 밖인가: `collect()` 는 CLI 도 쓴다. CLI 는 이미 실행자 역할을
    `security.json` 등급표로 검사한 뒤라 여기서 다시 판단할 필요가 없고, 판단을 안쪽에 두면
    "역할을 넘기지 않은 호출" 이 조용히 전부 보여 주는 쪽으로 기울기 쉽다.
    내보내는 자리에서 한 번, 명시적으로 건다.

    왜 필요했나 (2026-09-20 정렬 감사): `/api/opstats` 는 read 등급이라 viewer 도 부를 수 있는데
    `users` 절이 **누가 몇 건 질의했나**를 담고 있었다. 같은 값을 주는 `/api/query_users` 는
    admin 전용이라, 막아 둔 문을 옆문으로 여는 셈이었다.
    """
    if admin:
        return d
    out = dict(d)
    hidden = []
    for k, why in ADMIN_ONLY_SECTIONS.items():
        if k in out:
            out.pop(k, None)
            hidden.append({"section": k, "reason": why})
    if hidden:
        out["redacted"] = hidden
        out["sections"] = [s for s in (out.get("sections") or []) if s not in ADMIN_ONLY_SECTIONS]
    return out


def format_text(d: Dict[str, Any], width: int = 100) -> str:
    """CLI 용. 화면과 같은 숫자를 같은 이름으로 보여 준다."""
    L: List[str] = ["운영 통계 — 최근 %g일" % d.get("days", 0), ""]

    def head(key, title):
        L.append("[%s] %s" % (key, title))

    if "index" in d:
        i = d["index"]
        head("index", SECTION_HELP["index"])
        L.append("  문서 %s · 청크 %s (문서당 %s) · 임베딩 %s · 엔티티 %s · 관계 %s"
                 % (i.get("docs"), i.get("chunks"), i.get("chunks_per_doc"), i.get("embeddings"),
                    i.get("entities"), i.get("relations")))
        if i.get("chunks_without_embedding"):
            L.append("  ⚠ 임베딩 없는 청크 %s개 — 벡터 채널이 그만큼 못 본다 (`build vector`)" % i["chunks_without_embedding"])
        L.append("  문서 유형: %s" % ", ".join("%s %s" % (k, v) for k, v in sorted((i.get("doc_types") or {}).items())))
        L.append("")
    if "build" in d:
        b = d["build"]
        head("build", SECTION_HELP["build"])
        last = b.get("last") or {}
        if last.get("ts"):
            L.append("  마지막: %s · %s · 문서 %s (변경 %s)%s"
                     % (time.strftime("%m-%d %H:%M", time.localtime(last["ts"])), last.get("mode"),
                        last.get("docs"), last.get("changed"),
                        (" · %sms" % b.get("last_ms")) if b.get("last_ms") else ""))
        elif b.get("last_request_id"):
            # kv 의 last_build 요약이 없어도(초기화 뒤 등) 요청 이력에는 빌드가 남아 있다
            L.append("  마지막 빌드 기록(kv)이 없습니다 — 요청 이력 #%s 로 대신 봅니다%s"
                     % (b["last_request_id"], (" · %sms" % b.get("last_ms")) if b.get("last_ms") else ""))
        else:
            L.append("  빌드 기록이 없습니다 (아직 빌드하지 않았거나 초기화됨)")
        for s in (b.get("slowest_stages") or [])[:5]:
            L.append("    %-22s %8.1fms" % (s["stage"], s["ms"]))
        if b.get("alerts"):
            L.append("  ⚠ alerts: %s" % ", ".join(str(a.get("detail"))[:60] for a in b["alerts"][:3]))
        L.append("  최근 %g일 빌드 %d회" % (d.get("days", 0), b.get("n_builds", 0)))
        L.append("")
    if "queries" in d:
        q = d["queries"]
        head("queries", SECTION_HELP["queries"])
        L.append("  총 %s건 · 하루 평균 %s%s" % (q["total"], q["per_day"], " (표본 상한에 걸림)" if q.get("capped") else ""))
        L.append("  창구: %s" % (", ".join("%s %s" % (k, v) for k, v in q["by_origin"].items()) or "-"))
        if q.get("busiest_hour") is not None:
            L.append("  가장 몰리는 시간대: %02d시" % q["busiest_hour"])
        L.append("")
    if "latency" in d:
        la = d["latency"]
        head("latency", SECTION_HELP["latency"])
        L.append("  표본 %s건 · p50 %sms · p95 %sms · 최대 %sms" % (la["n"], la["p50_ms"], la["p95_ms"], la["max_ms"]))
        for s in (la.get("slowest") or [])[:3]:
            L.append("    #%-5s %8sms  %s" % (s["request_id"], s["ms"], s["summary"][:56]))
        L.append("")
    if "tokens" in d:
        t = d["tokens"]
        head("tokens", SECTION_HELP["tokens"])
        L.append("  총 %s (입력 %s · 출력 %s) · 질의당 %s · LLM 호출 질의당 %s회"
                 % (t["total"], t["input"], t["output"], t["per_query"], t["calls_per_query"]))
        for s in (t.get("heaviest") or [])[:3]:
            L.append("    #%-5s %8s토큰  %s" % (s["request_id"], s["tokens"], s["summary"][:56]))
        L.append("")
    if "quality" in d:
        qa = d["quality"]
        head("quality", SECTION_HELP["quality"])
        L.append("  질의 %s건 중 근거 부족 %s건 (%.1f%%)" % (qa["queries"], qa["insufficient"], 100 * qa["insufficient_rate"]))
        L.append("  피드백: 👍 %s · 👎 %s (달린 비율 %.1f%%)" % (qa["feedback_positive"], qa["feedback_negative"], 100 * qa["feedback_rate"]))
        if qa.get("forensics"):
            L.append("  포렌식: %s" % ", ".join("%s %s" % (k, v) for k, v in qa["forensics"].items()))
        L.append("  대기 중 자가진화 제안 %s건" % qa.get("proposals_pending"))
        L.append("")
    if "users" in d:
        u = d["users"]
        head("users", SECTION_HELP["users"])
        L.append("  사용자 %s명" % u["n"])
        for x in u["top"][:5]:
            L.append("    %-20s %s건" % (x["user"][:20], x["queries"]))
        L.append("")
    if "storage" in d:
        s = d["storage"]
        head("storage", SECTION_HELP["storage"])
        L.append("  DB %s · 로그 %s · 위키 %s" % (_mb(s.get("db_bytes")), _mb(s.get("logs_bytes")), _mb(s.get("wiki_bytes"))))
        for x in (s.get("data_dirs") or [])[:5]:
            L.append("    data/%-16s %10s (%s개)" % (x["dir"], _mb(x["bytes"]), x["files"]))
        L.append("  큰 테이블: %s" % ", ".join("%s %s행" % (x["table"], x["rows"]) for x in (s.get("tables") or [])[:5]))
        for h in (s.get("hints") or []):
            L.append("  ⚠ %s" % h)
        L.append("")
    if "embed" in d:
        e = d["embed"]
        head("embed", SECTION_HELP["embed"])
        L.append("  캐시 적중률 %.1f%% · 실패 %s건" % (100 * e["cache_hit_rate"], e["failed"]))
        for r in (e.get("runs") or [])[:3]:
            L.append("    %-10s %-8s done %s / 실패 %s / 캐시 %s · %ss"
                     % (str(r["run_id"])[:10], str(r["model"])[:8], r["done"], r["failed"], r["cache_hits"], r["elapsed_s"]))
        L.append("")
    if "trend" in d:
        tr = d["trend"]
        head("trend", SECTION_HELP["trend"])
        BLABEL = {"day": "일간", "week": "주간", "month": "월간"}
        pts = tr.get("points") or []
        L.append("  %s · 구간 %d개 · 최근 %g일" % (BLABEL.get(tr.get("bucket"), tr.get("bucket")), len(pts), tr.get("days", 0)))
        if tr.get("note"):
            L.append("  %s" % tr["note"])
        if pts:
            L.append("  질의   %s" % _spark([p["queries"] for p in pts]))
            L.append("  p95ms  %s" % _spark([p["p95_ms"] for p in pts]))
            L.append("  토큰/질의 %s" % _spark([p["tokens_per_query"] for p in pts]))
            L.append("  %-12s %7s %8s %8s %9s %6s" % ("구간", "질의", "p50ms", "p95ms", "토큰/질의", "빌드"))
            for p in pts[-12:]:
                L.append("  %-12s %7s %8s %8s %9s %6s"
                         % (p["bucket"], p["queries"], p["p50_ms"] if p["p50_ms"] is not None else "-",
                            p["p95_ms"] if p["p95_ms"] is not None else "-", p["tokens_per_query"], p["builds"]))
        dl = tr.get("delta_last") or {}
        if dl:
            L.append("  직전 구간 대비: %s" % " · ".join("%s %s%s" % (k, "+" if v > 0 else "", v) for k, v in dl.items()))
    return "\n".join(L)


#: 스파크라인 — 터미널에서도 추세를 한 줄로 본다 (화면의 막대 차트와 같은 값).
_SPARK = "▁▂▃▄▅▆▇█"


def _spark(vals) -> str:
    nums = [float(v) for v in vals if isinstance(v, (int, float))]
    if not nums:
        return "(값 없음)"
    lo, hi = min(nums), max(nums)
    span = (hi - lo) or 1.0
    out = []
    for v in vals:
        if not isinstance(v, (int, float)):
            out.append(" ")
            continue
        out.append(_SPARK[min(len(_SPARK) - 1, int((float(v) - lo) / span * (len(_SPARK) - 1)))])
    return "".join(out) + "  (%g ~ %g)" % (lo, hi)
