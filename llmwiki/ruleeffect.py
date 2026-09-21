# -*- coding: utf-8 -*-
"""규칙 효과 측정 — "이 확장 규칙이 **실제로 답에 기여했나**" 를 누적한다 (2026-09-19).

왜 필요한가: `query_rules.json` 의 동의어·약어·관련어는 검색 품질을 올리는 가장 값싼 손잡이다
(LLM 을 부르지 않고 결정적으로 recall 을 올린다). 그런데 규칙은 **쌓이기만 하고 줄지 않는다** —
한 번 넣은 동의어가 실제로 쓸모 있는지 아무도 모르니 지울 근거가 없고, 잘못 넣은 규칙은
엉뚱한 문단을 계속 끌어와 정밀도를 갉아먹는다. 규칙이 수백 개가 되면 어느 것이 도움이 되는지 사람이 알 수 없다.

그래서 질의마다 이렇게 센다.

  발화(fired)   그 규칙이 질의에 걸렸다
  후보(cand)    그 규칙이 만든 대체 질의·관련어 검색이 **후보를 하나라도** 가져왔다
  기여(helped)  그 후보 중 하나가 **최종 컨텍스트에 들어갔다** (= 답변의 근거가 됐다)
  인용(cited)   그 근거가 답변에 **[C#] 로 인용**됐다

정확도: 대체 질의 리스트마다 출처 규칙을 달아 두므로(`query_rules.expand` 의 4번째 원소),
"여러 규칙이 동시에 걸렸을 때 누구 덕인지" 를 뭉개지 않는다. 한 청크가 두 규칙 리스트에 모두
들어 있으면 두 규칙 모두 기여로 센다 — 실제로 둘 다 그 청크를 찾았기 때문이다.

저장: `kv` 테이블의 `rule_effect` 한 줄(JSON). 스키마 변경이 없고, 질의당 쓰기 1회다.
읽기: `stats()` → CLI `rules stats`, Web 설정 › 질의 규칙 사전, MCP `wiki_rules`.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

KV_KEY = "rule_effect"
MAX_TERMS = 2000          # 규칙 수가 이보다 많아지면 오래된 것부터 버린다 (kv 한 줄이 무한정 커지지 않게)

# 질의마다 DB 에 쓰지 않는다 (2026-09-19).
# 왜: 이것은 관측용 통계인데, 질의 경로에 **쓰기**를 하나 더 넣으면 빌드가 쓰기 락을 잡고 있는 동안
# 질의마다 SQLite busy_timeout(기본 60초)을 기다릴 수 있다. 30명이 쓰는 서버에서 통계 때문에 질의가 느려지면 안 된다.
# 그래서 메모리에 모았다가 FLUSH_EVERY_S 초 / FLUSH_EVERY_N 건마다 한 번만 쓴다. 프로세스가 죽으면 그만큼만 잃는다.
FLUSH_EVERY_S = 30.0
FLUSH_EVERY_N = 20
_BUF: Dict[str, Dict[str, Any]] = {}
_BUF_LOCK = threading.Lock()
_LAST_FLUSH = [0.0]
_PENDING = [0]


def _load(store: Any) -> Dict[str, Any]:
    # store.kv_get 은 이미 JSON 을 풀어서 돌려준다 (store.py) — 문자열로 들어온 옛 값도 받아 준다.
    try:
        raw = store.kv_get(KV_KEY)
    except Exception:
        return {}
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _save(store: Any, d: Dict[str, Any]) -> None:
    if len(d) > MAX_TERMS:
        keep = sorted(d.items(), key=lambda kv: -float((kv[1] or {}).get("last_ts") or 0))[:MAX_TERMS]
        d = dict(keep)
    try:
        store.kv_set(KV_KEY, d)
    except Exception:
        pass      # 관측용 통계다 — 못 써도 질의를 실패시키지 않는다


def record(store: Any, fired: Iterable[Dict[str, Any]], rule_src: Dict[str, str],
           hits: Iterable[Dict[str, Any]], answer: str = "") -> Dict[str, Any]:
    """질의 하나의 결과로 규칙별 카운터를 올린다.

    fired     : `query_rules.expand()` 의 `fired` — [{type, matched, canonical, values, round}]
    rule_src  : 검색 리스트 이름 → 그 리스트를 만든 규칙 대표어 (예: {"fts_alt1": "CL"})
    hits      : 최종 후보 목록. 각 항목은 `why`(어느 리스트에서 왔는지)와 `in_context` 를 가진다.
    answer    : 답변 본문 (인용 [C#] 여부를 보기 위함, 없으면 인용은 세지 않는다)
    반환: 이번 질의에서 규칙별로 무엇이 올랐는지 (trace/meta 에 실어 화면에서 볼 수 있게)
    """
    fired = list(fired or [])
    if not fired:
        return {}
    hits = list(hits or [])
    # 리스트 이름 → 그 리스트에서 온 청크들
    by_src: Dict[str, Dict[str, bool]] = {}
    for h in hits:
        why = h.get("why") or []
        if isinstance(why, str):
            why = [why]
        in_ctx = bool(h.get("in_context"))
        cited = bool(in_ctx and answer and str(h.get("cite") or "") and str(h.get("cite")) in answer)
        for wname in why:
            base = str(wname).split("#")[0]
            term = rule_src.get(base)
            if not term:
                continue
            slot = by_src.setdefault(term, {"cand": False, "helped": False, "cited": False})
            slot["cand"] = True
            if in_ctx:
                slot["helped"] = True
            if cited:
                slot["cited"] = True

    now = time.time()
    delta: Dict[str, Any] = {}
    with _BUF_LOCK:
        for f in fired:
            term = str(f.get("canonical") or f.get("matched") or "").strip()
            if not term:
                continue
            row = _BUF.setdefault(term, {"type": f.get("type"), "fired": 0, "cand": 0, "helped": 0, "cited": 0, "last_ts": 0})
            row["type"] = f.get("type") or row.get("type")
            row["fired"] = int(row.get("fired") or 0) + 1
            got = by_src.get(term) or {}
            for k in ("cand", "helped", "cited"):
                if got.get(k):
                    row[k] = int(row.get(k) or 0) + 1
            row["last_ts"] = now
            delta[term] = {"type": row["type"], **{k: bool(got.get(k)) for k in ("cand", "helped", "cited")}}
        _PENDING[0] += 1
        due = (_PENDING[0] >= FLUSH_EVERY_N) or (now - _LAST_FLUSH[0] >= FLUSH_EVERY_S)
    if due:
        flush(store)
    return delta


def flush(store: Any) -> int:
    """모아 둔 카운터를 kv 에 한 번에 더한다. 반환: 반영한 규칙 수.

    쓰기가 실패하면(빌드가 락을 잡고 있는 등) **버퍼를 비우지 않는다** — 다음 기회에 다시 시도한다.
    """
    with _BUF_LOCK:
        if not _BUF:
            _LAST_FLUSH[0] = time.time()
            return 0
        pend = {k: dict(v) for k, v in _BUF.items()}
    try:
        d = _load(store)
        for term, row in pend.items():
            cur = d.setdefault(term, {"type": row.get("type"), "fired": 0, "cand": 0, "helped": 0, "cited": 0, "last_ts": 0})
            cur["type"] = row.get("type") or cur.get("type")
            for k in ("fired", "cand", "helped", "cited"):
                cur[k] = int(cur.get(k) or 0) + int(row.get(k) or 0)
            cur["last_ts"] = max(float(cur.get("last_ts") or 0), float(row.get("last_ts") or 0))
        _save(store, d)
    except Exception:
        return 0            # 다음에 다시 — 버퍼는 그대로 둔다
    with _BUF_LOCK:
        for term, row in pend.items():
            b = _BUF.get(term)
            if not b:
                continue
            for k in ("fired", "cand", "helped", "cited"):
                b[k] = int(b.get(k) or 0) - int(row.get(k) or 0)
            if not any(int(b.get(k) or 0) for k in ("fired", "cand", "helped", "cited")):
                _BUF.pop(term, None)
        _PENDING[0] = 0
        _LAST_FLUSH[0] = time.time()
    return len(pend)


def _merged(store: Any) -> Dict[str, Any]:
    """저장된 값 + 아직 안 쓴 버퍼 (화면이 방금 돌린 질의도 바로 보게)."""
    d = _load(store)
    with _BUF_LOCK:
        for term, row in _BUF.items():
            cur = d.setdefault(term, {"type": row.get("type"), "fired": 0, "cand": 0, "helped": 0, "cited": 0, "last_ts": 0})
            for k in ("fired", "cand", "helped", "cited"):
                cur[k] = int(cur.get(k) or 0) + int(row.get(k) or 0)
            cur["type"] = row.get("type") or cur.get("type")
            cur["last_ts"] = max(float(cur.get("last_ts") or 0), float(row.get("last_ts") or 0))
    return d


def stats(store: Any, limit: int = 200, order: str = "fired") -> Dict[str, Any]:
    """규칙별 누적 효과. order: fired | helped | useless(발화했는데 기여 0) | rate(기여율)"""
    d = _merged(store)
    rows: List[Dict[str, Any]] = []
    for term, r in d.items():
        fired = int(r.get("fired") or 0)
        helped = int(r.get("helped") or 0)
        rows.append({"term": term, "type": r.get("type"), "fired": fired, "cand": int(r.get("cand") or 0),
                     "helped": helped, "cited": int(r.get("cited") or 0), "last_ts": r.get("last_ts"),
                     "help_rate": round(helped / fired, 3) if fired else 0.0})
    if order == "helped":
        rows.sort(key=lambda x: (-x["helped"], -x["fired"]))
    elif order == "rate":
        rows.sort(key=lambda x: (-x["help_rate"], -x["fired"]))
    elif order == "useless":
        rows = [x for x in rows if x["fired"] >= 3 and x["helped"] == 0]
        rows.sort(key=lambda x: -x["fired"])
    else:
        rows.sort(key=lambda x: (-x["fired"], x["term"]))
    total_fired = sum(x["fired"] for x in rows)
    return {"rows": rows[:limit], "n": len(d), "shown": min(limit, len(rows)), "order": order,
            "total_fired": total_fired,
            "never_helped": sum(1 for x in rows if x["fired"] >= 3 and x["helped"] == 0),
            "note": "fired=규칙이 걸린 질의 수 · cand=후보를 가져온 질의 수 · helped=그 후보가 최종 컨텍스트에 들어간 질의 수 · cited=답변이 인용한 질의 수"}


def reset(store: Any, term: Optional[str] = None) -> Dict[str, Any]:
    """누적치 초기화 (규칙을 고친 뒤 다시 재고 싶을 때). term 을 주면 그 규칙만."""
    with _BUF_LOCK:
        if term:
            _BUF.pop(term, None)
        else:
            _BUF.clear()
        _PENDING[0] = 0
    d = _load(store)
    if term:
        removed = 1 if d.pop(term, None) is not None else 0
    else:
        removed, d = len(d), {}
    _save(store, d)
    return {"removed": removed, "left": len(d)}
