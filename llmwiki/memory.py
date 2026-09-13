# -*- coding: utf-8 -*-
"""자가진화 메모리 — episodic(질의 에피소드) · semantic(승인된 규칙/제안/pin) · decay(시간 감쇠·재사용 강화).

코퍼스의 사실은 감쇠시키지 않는다. 감쇠 대상은 '시스템이 스스로 배운 것'(제안 strength, pin strength, 피드백 부스트) 뿐이다.
  episodes         : 질의 1건 = (질문, 근거 청크, 판정/groundedness, 피드백) — query_log/forensics 의 정규화 뷰
  feedback_weights : 긍정/부정 피드백을 받은 청크의 부스트 (반감기 memory_half_life_days 로 감쇠) → fusion post-boost
  decay            : proposals.strength 감쇠, memory_archive_strength 미만의 미승인 제안은 archived
  consolidate      : 반복되는 포렌식 소견/에피소드를 묶어 corpus_gap / query_rule 제안 생성 (semantic 승격)
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional


def _decay_factor(age_s: float, half_life_days: float) -> float:
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (max(0.0, age_s) / (half_life_days * 86400.0))


def record_episode(store, request_id: Optional[int], query: str, kind: str, outcome: str, chunks: List[str], topics: List[str],
                   detail: Optional[Dict[str, Any]] = None) -> int:
    cur = store.conn.execute("INSERT INTO episodes(ts,request_id,query,kind,outcome,chunks,feedback,strength,last_reinforced,topics,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             (time.time(), request_id, query, kind, outcome, json.dumps(chunks[:20]), None, 1.0, time.time(), json.dumps(topics[:8], ensure_ascii=False),
                              json.dumps(detail or {}, ensure_ascii=False, default=str)[:4000]))
    store.conn.commit()
    return int(cur.lastrowid)


def on_feedback(store, query_id: int, feedback: int) -> Optional[int]:
    """query_log 의 피드백을 에피소드에 반영 (해당 요청의 에피소드가 없으면 생성)."""
    q = store.get_query(query_id)
    if not q:
        return None
    chunks = json.loads(q.get("top_chunks") or "[]")
    row = store.conn.execute("SELECT id FROM episodes WHERE query=? ORDER BY id DESC LIMIT 1", (q["query"],)).fetchone()
    if row:
        store.conn.execute("UPDATE episodes SET feedback=?, last_reinforced=?, strength=MIN(2.0, strength+0.5) WHERE id=?", (feedback, time.time(), row["id"]))
        store.conn.commit()
        return int(row["id"])
    return record_episode(store, None, q["query"], "feedback", "positive" if feedback > 0 else "negative", chunks, [], {"query_id": query_id, "feedback": feedback})


def feedback_weights(store, half_life_days: float = 60.0, limit: int = 2000) -> Dict[str, float]:
    """청크별 피드백 부스트 (-1~1, 감쇠). 여러 에피소드가 같은 청크를 가리키면 합산 후 클리핑."""
    out: Dict[str, float] = {}
    now = time.time()
    for r in store.conn.execute("SELECT chunks, feedback, last_reinforced, strength FROM episodes WHERE feedback IS NOT NULL ORDER BY id DESC LIMIT ?", (limit,)):
        fb = int(r["feedback"] or 0)
        if not fb:
            continue
        f = _decay_factor(now - float(r["last_reinforced"] or now), half_life_days) * float(r["strength"] or 1.0)
        try:
            chunks = json.loads(r["chunks"] or "[]")
        except Exception:
            chunks = []
        for i, cid in enumerate(chunks[:5]):
            w = (1.0 if fb > 0 else -1.0) * f * (1.0 - 0.15 * i)
            out[cid] = max(-1.0, min(1.0, out.get(cid, 0.0) + w))
    return out


def decay(store, half_life_days: float = 60.0, archive_strength: float = 0.2, pins_path: Optional[str] = None) -> Dict[str, Any]:
    """제안·pin strength 감쇠. 미승인 제안이 임계 미만이면 archived (삭제하지 않음)."""
    now = time.time()
    n_upd = n_arch = 0
    for r in store.conn.execute("SELECT id, ts, strength, last_reinforced, status FROM proposals WHERE status='proposed'").fetchall():
        last = float(r["last_reinforced"] or r["ts"] or now)
        base = float(r["strength"] or 1.0)
        new = round(base * _decay_factor(now - last, half_life_days), 4)
        if new < archive_strength:
            store.conn.execute("UPDATE proposals SET status='archived', strength=? WHERE id=?", (new, r["id"]))
            store.log_evolution(int(r["id"]), "archive", {"strength": new, "reason": "memory decay"}, "")
            n_arch += 1
        elif abs(new - base) > 1e-6:
            store.conn.execute("UPDATE proposals SET strength=? WHERE id=?", (new, r["id"]))
            n_upd += 1
    store.conn.commit()
    n_pins = 0
    try:
        from . import pins as _pins
        ps = _pins.load_pins()
        for p in ps:
            last = float(p.get("last_reinforced") or p.get("created") or now)
            new = round(max(0.1, float(p.get("strength", 1.0)) * _decay_factor(now - last, half_life_days * 2)), 4)
            if abs(new - float(p.get("strength", 1.0))) > 1e-6:
                p["strength"] = new
                n_pins += 1
        if n_pins:
            _pins.save_pins(ps)
    except Exception:
        pass
    return {"proposals_decayed": n_upd, "proposals_archived": n_arch, "pins_decayed": n_pins}


def reinforce_proposal(store, pid: int, amount: float = 0.3) -> None:
    store.conn.execute("UPDATE proposals SET strength=MIN(3.0, COALESCE(strength,1.0)+?), last_reinforced=?, hits=COALESCE(hits,1)+1 WHERE id=?", (amount, time.time(), pid))
    store.conn.commit()


def consolidate(store, min_events: int = 3, limit: int = 1000) -> Dict[str, Any]:
    """포렌식 소견을 주제/제안 종류별로 묶어 반복되는 것을 제안으로 승격 (semantic memory)."""
    from .forensic import list_forensics
    rows = list_forensics(store, limit)
    groups: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        for s in r["suggestions"]:
            kind = s.get("kind")
            pl = s.get("payload") or {}
            if kind == "corpus_gap":
                key = "corpus_gap|" + (pl.get("topic") or "")
            elif kind == "query_rule":
                key = "query_rule|%s|%s" % (pl.get("type", "synonym"), pl.get("term", ""))
            elif kind == "tuning":
                key = "tuning|%s|%s" % (pl.get("key", ""), pl.get("value", ""))
            elif kind == "alias":
                key = "alias|" + (pl.get("alias") or "")
            else:
                continue
            g = groups.setdefault(key, {"kind": kind, "payload": pl, "n": 0, "queries": [], "conf": 0.0, "detail": s.get("detail", "")})
            g["n"] += 1
            g["conf"] = max(g["conf"], float(s.get("confidence") or 0))
            if r["query"] not in g["queries"]:
                g["queries"].append(r["query"])
    created: List[int] = []
    for key, g in groups.items():
        if g["n"] < min_events:
            continue
        kind = g["kind"]
        payload = dict(g["payload"], queries=g["queries"][:10], events=g["n"])
        conf = min(0.95, g["conf"] + 0.05 * (g["n"] - min_events))
        reason = "포렌식 %d건 반복: %s" % (g["n"], g["detail"][:120])
        pid = store.add_proposal(kind, payload, reason, conf, "forensics")
        reinforce_proposal(store, pid, 0.1 * g["n"])
        created.append(pid)
    return {"groups": len(groups), "proposals": created, "min_events": min_events}


def status(store, half_life_days: float = 60.0) -> Dict[str, Any]:
    q = lambda sql, *a: store.conn.execute(sql, a).fetchone()[0]  # noqa: E731
    return {"episodes": q("SELECT COUNT(*) FROM episodes"), "episodes_with_feedback": q("SELECT COUNT(*) FROM episodes WHERE feedback IS NOT NULL"),
            "proposals": {r["status"]: int(r["n"]) for r in store.conn.execute("SELECT status, COUNT(*) n FROM proposals GROUP BY status")},
            "avg_strength_proposed": q("SELECT COALESCE(AVG(strength),0) FROM proposals WHERE status='proposed'"),
            "feedback_chunks": len(feedback_weights(store, half_life_days)), "half_life_days": half_life_days,
            "forensics": q("SELECT COUNT(*) FROM forensics")}


def episodes(store, limit: int = 50) -> List[Dict[str, Any]]:
    out = []
    for r in store.conn.execute("SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        for k in ("chunks", "topics", "detail"):
            try:
                d[k] = json.loads(d[k] or ("[]" if k != "detail" else "{}"))
            except Exception:
                pass
        out.append(d)
    return out
