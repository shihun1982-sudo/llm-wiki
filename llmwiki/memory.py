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


def _feedback_contributions(store, half_life_days: float, limit: int, with_source: bool = False):
    """피드백 에피소드 → (chunk_id, 가중치, 출처) 하나씩. `feedback_weights` 와 `boost_table` 의 공용 계산.

    같은 수식을 두 벌로 두면 화면에 보이는 값과 실제로 검색에 쓰이는 값이 갈라진다 — 그러면 이 화면이
    거짓말을 하게 되므로 한 자리에서만 계산한다.
    """
    now = time.time()
    cols = "id, ts, query, chunks, feedback, last_reinforced, strength" if with_source else "chunks, feedback, last_reinforced, strength"
    for r in store.conn.execute("SELECT %s FROM episodes WHERE feedback IS NOT NULL ORDER BY id DESC LIMIT ?" % cols, (limit,)):
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
            src = ({"episode": int(r["id"]), "query": r["query"], "feedback": fb, "ts": r["ts"], "rank": i + 1,
                    "decay": round(f, 3)} if with_source else None)
            yield cid, w, src


def feedback_weights(store, half_life_days: float = 60.0, limit: int = 2000) -> Dict[str, float]:
    """청크별 피드백 부스트 (-1~1, 감쇠). 여러 에피소드가 같은 청크를 가리키면 합산 후 클리핑."""
    out: Dict[str, float] = {}
    for cid, w, _src in _feedback_contributions(store, half_life_days, limit):
        out[cid] = max(-1.0, min(1.0, out.get(cid, 0.0) + w))
    return out


def boost_table(store, half_life_days: float = 60.0, limit: int = 2000, top: int = 50) -> List[Dict[str, Any]]:
    """**지금 검색이 실제로 받고 있는 피드백 부스트** — 청크별 가중치 + 그렇게 된 근거 에피소드.

    왜 필요한가: 메모리 화면에는 오래도록 '부스트 청크 N개' 라는 숫자만 있었다. 그런데 사람이 묻는 것은
    "내가 누른 👎 가 반영됐나", "왜 이 문서가 자꾸 위로 오나" 이고, 그 답은 숫자가 아니라 **목록**이다.
    여기서 `feedback_weights` 와 **같은 계산**(_feedback_contributions)을 쓰되 출처를 함께 돌려준다.

    반환: [{chunk_id, doc_id, heading, weight, n_episodes, episodes:[{episode,query,feedback,ts,rank,decay}]}]
          weight 의 절댓값이 큰 순. 문서 정보는 색인에서 찾지 못하면 비운다(빌드로 사라진 청크).
    """
    agg: Dict[str, Dict[str, Any]] = {}
    for cid, w, src in _feedback_contributions(store, half_life_days, limit, with_source=True):
        e = agg.setdefault(cid, {"chunk_id": cid, "weight": 0.0, "episodes": []})
        e["weight"] = max(-1.0, min(1.0, e["weight"] + w))
        if len(e["episodes"]) < 5:
            e["episodes"].append(src)
    rows = sorted(agg.values(), key=lambda x: -abs(x["weight"]))[:top]
    for e in rows:
        e["weight"] = round(e["weight"], 4)
        e["n_episodes"] = len(e["episodes"])
        e["doc_id"] = str(e["chunk_id"]).rsplit("#", 1)[0]
        e["heading"], e["exists"] = "", False
        try:
            c = store.get_chunk(e["chunk_id"])
            if c:
                c = dict(c)
                e["heading"], e["doc_id"], e["exists"] = (c.get("heading") or ""), (c.get("doc_id") or e["doc_id"]), True
        except Exception:
            pass
    return rows


def decaying_proposals(store, half_life_days: float = 60.0, archive_strength: float = 0.2, limit: int = 30) -> List[Dict[str, Any]]:
    """감쇠 중인 미승인 제안 — 약한 것부터. "제안이 왜 사라졌지?" 의 답을 **사라지기 전에** 보여 준다.

    `days_left` 는 지금 속도로 감쇠했을 때 `archive_strength` 아래로 내려가기까지 남은 날이다(대략).
    0 이하면 다음 `decay` 실행에서 archived 가 된다 — 살리려면 승인하거나 다시 쓰이게 해야 한다.
    """
    import math
    now = time.time()
    out: List[Dict[str, Any]] = []
    for r in store.conn.execute("SELECT id, kind, payload, reason, confidence, strength, ts, last_reinforced, hits, origin "
                                "FROM proposals WHERE status='proposed' ORDER BY COALESCE(strength,1.0) ASC LIMIT ?", (limit,)):
        d = dict(r)
        last = float(d.get("last_reinforced") or d.get("ts") or now)
        base = float(d.get("strength") or 1.0)
        cur = base * _decay_factor(now - last, half_life_days)
        days = None
        if half_life_days > 0 and archive_strength > 0 and cur > 0:
            days = round(max(0.0, half_life_days * math.log(cur / archive_strength, 2)), 1) if cur > archive_strength else 0.0
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except Exception:
            pass
        d["strength_now"] = round(cur, 4)
        d["days_left"] = days
        d["at_risk"] = bool(days is not None and days <= 7)
        out.append(d)
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
                # 별칭은 '어느 엔티티에' 붙이는지가 정체성이다. 예전에는 alias 만으로 묶어서, 대상이 다른 제안이
                # 한 덩어리가 되거나 대상 없는(entity 키가 빠진) payload 가 그대로 승격됐다 (2026-09-19).
                if not pl.get("entity"):
                    continue
                key = "alias|%s|%s" % (pl.get("entity"), pl.get("alias") or "")
            elif kind == "entity":
                key = "entity|" + (pl.get("name") or "")
                if not pl.get("name"):
                    continue
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


def episodes(store, limit: int = 50, only: str = "", q: str = "") -> List[Dict[str, Any]]:
    """최근 에피소드. `only`: ""(전부) | "feedback"(피드백 있는 것) | "negative"(👎) | "positive"(👍).
    `q` 를 주면 질문 본문에서 찾는다 — 에피소드가 쌓이면 목록만으로는 찾을 수 없다."""
    where, args = [], []
    if only == "feedback":
        where.append("feedback IS NOT NULL")
    elif only == "negative":
        where.append("feedback < 0")
    elif only == "positive":
        where.append("feedback > 0")
    if q:
        where.append("query LIKE ?")
        args.append("%" + q + "%")
    sql = "SELECT * FROM episodes%s ORDER BY id DESC LIMIT ?" % ((" WHERE " + " AND ".join(where)) if where else "")
    out = []
    for r in store.conn.execute(sql, tuple(args) + (limit,)):
        d = dict(r)
        for k in ("chunks", "topics", "detail"):
            try:
                d[k] = json.loads(d[k] or ("[]" if k != "detail" else "{}"))
            except Exception:
                pass
        # 화면이 "요청 프로파일로 가기"·"질의 로그로 가기" 를 걸 수 있게 id 를 꺼내 준다
        det = d.get("detail") if isinstance(d.get("detail"), dict) else {}
        d["query_id"] = det.get("query_id")
        out.append(d)
    return out
