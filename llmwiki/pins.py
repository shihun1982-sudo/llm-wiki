# -*- coding: utf-8 -*-
"""Pin — 고정 근거.

pins.json:
  {"pins": [
     {"id": "p1", "doc": "corpus/coding_rules/RULE-ISR-001.md", "when": {"keywords": ["코드 리뷰", "ISR"], "doc_types": []}, "weight": 1.0, "note": "...", "strength": 1.0},
     {"id": "p2", "chunk": "corpus/issues/ISSUE-2001.md#2", "when": {"query": "RX DMA underrun 원인"}, "weight": 1.0},
     {"id": "p3", "doc": "corpus/hw_design/HWD-PHY-TIMING-B1.md", "when": {"always": true}, "weight": 0.5}]}
when: always | keywords(하나라도 질의에 포함) | query(정규화 후 동일/포함) | doc_types(라우터 힌트) — 조건이 맞으면 해당 문서/청크가
검색 결과 상단에 주입되고 fusion 에서 pin_boost 배율을 받는다. strength 는 memory decay 로 감쇠·강화된다.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from .config import path_for
from .textutil import keywords


def pins_path() -> str:
    return path_for("pins")


def load_pins() -> List[Dict[str, Any]]:
    p = pins_path()
    if not os.path.exists(p):
        save_pins([])
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("pins") or [])
    except Exception:
        return []


def save_pins(pins: List[Dict[str, Any]]) -> str:
    p = pins_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"_comment": "고정 근거. when: always | keywords[] | query | doc_types[]. doc(문서 id 부분 문자열) 또는 chunk(청크 id). pin add … 로 관리.",
                   "pins": pins}, f, ensure_ascii=False, indent=2)
    return p


def add_pin(doc: Optional[str] = None, chunk: Optional[str] = None, query: Optional[str] = None, keywords_: Optional[List[str]] = None,
            always: bool = False, doc_types: Optional[List[str]] = None, weight: float = 1.0, note: str = "", source: str = "manual") -> Dict[str, Any]:
    pins = load_pins()
    pid = "p%d" % (max([int(re.sub(r"\D", "", p.get("id", "0")) or 0) for p in pins] + [0]) + 1)
    when: Dict[str, Any] = {}
    if always:
        when["always"] = True
    if query:
        when["query"] = query
    if keywords_:
        when["keywords"] = list(keywords_)
    if doc_types:
        when["doc_types"] = list(doc_types)
    if not when:
        when["always"] = True
    pin = {"id": pid, "doc": doc, "chunk": chunk, "when": when, "weight": float(weight), "note": note, "source": source,
           "created": time.time(), "strength": 1.0, "last_reinforced": time.time(), "hits": 0}
    pins.append(pin)
    save_pins(pins)
    return pin


def remove_pin(pid: str) -> bool:
    pins = load_pins()
    new = [p for p in pins if p.get("id") != pid]
    if len(new) == len(pins):
        return False
    save_pins(new)
    return True


def _match(pin: Dict[str, Any], query: str, qkw: List[str], router_types: List[str]) -> bool:
    w = pin.get("when") or {}
    if w.get("always"):
        return True
    if w.get("query"):
        a = set(keywords(w["query"]))
        return bool(a) and a <= set(qkw)
    if w.get("keywords"):
        ql = query.lower()
        return any(k.lower() in ql or k.lower() in qkw for k in w["keywords"])
    if w.get("doc_types"):
        return bool(set(w["doc_types"]) & set(router_types or []))
    return False


def match_pins(store, query: str, router_types: Optional[List[str]] = None) -> Dict[str, Any]:
    """질의에 해당하는 pin → {chunk_id: weight} 와 주입할 청크 목록."""
    pins = load_pins()
    qkw = keywords(query)
    matched: List[Dict[str, Any]] = []
    weights: Dict[str, float] = {}
    inject: List[str] = []
    docs = None
    for pin in pins:
        if not _match(pin, query, qkw, router_types or []):
            continue
        w = float(pin.get("weight") or 1.0) * float(pin.get("strength") or 1.0)
        if pin.get("chunk"):
            cid = pin["chunk"]
            if store.get_chunk(cid):
                weights[cid] = max(weights.get(cid, 0), w)
                inject.append(cid)
                matched.append(pin)
        elif pin.get("doc"):
            if docs is None:
                docs = store.list_docs()
            for d in docs:
                if pin["doc"] in d["doc_id"]:
                    rows = store.all_chunks(d["doc_id"])
                    for c in rows[:3]:      # 문서 pin 은 앞 3개 청크
                        weights[c["chunk_id"]] = max(weights.get(c["chunk_id"], 0), w)
                        inject.append(c["chunk_id"])
                    weights[d["doc_id"]] = w
                    matched.append(pin)
    if matched:
        _reinforce([p["id"] for p in matched])
    return {"weights": weights, "inject": list(dict.fromkeys(inject)), "matched": [{"id": p["id"], "doc": p.get("doc"), "chunk": p.get("chunk"), "when": p.get("when")} for p in matched]}


def _reinforce(ids: List[str]) -> None:
    pins = load_pins()
    changed = False
    for p in pins:
        if p.get("id") in ids:
            p["hits"] = int(p.get("hits", 0)) + 1
            p["last_reinforced"] = time.time()
            p["strength"] = min(1.5, float(p.get("strength", 1.0)) + 0.05)
            changed = True
    if changed:
        save_pins(pins)
