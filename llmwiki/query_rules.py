# -*- coding: utf-8 -*-
"""규칙 기반 질의 확장 사전 (query_rules.json) — LLM 없이 결정적으로 질의를 확장한다.

유형별 의미와 적용 방식 (같은 방식으로 섞어 넣지 않는다):
  acronym  : 완전 동치·양방향 (PDCCH ⇄ Physical Downlink Control Channel). FTS 에 구문(phrase) OR 로 동일 가중, 벡터는 치환 대체 질의, 그래프 시드 별칭.
  synonym  : 준동치 (재시작 ≈ 리셋 ≈ restart). FTS OR 확장(syn_w), 벡터 대체 질의.
  alias    : 표기 정규화 (모뎀B → MDM9x-B1). canonical 로 치환(+원 표기 OR), 그래프 시드는 canonical.
  related  : 연관어 (DMA underrun ~ FIFO overflow). 주 질의에 넣지 않고 별도 보조 리스트(related_w)로 융합 → precision 보호.
  exclude  : 잡음 배제. FTS NOT + 후보 후처리 페널티(exclude_penalty) — 벡터 채널까지 막기 위해 후처리 필요.
  compound : 복합어 분리 사전 (재전송타이머 → 재전송 타이머). 색인·질의 토크나이저에 주입 (textutil.set_compounds).
파일 구조: {"acronym": {"PDCCH": ["Physical Downlink Control Channel"]}, "synonym": {...}, "alias": {"모뎀B": "MDM9x-B1"}, "related": {...},
           "exclude": {"시뮬레이터": ["simulator"]}, "compound": {"재전송타이머": ["재전송", "타이머"]}}
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .config import path_for
from .textutil import words, normalize_token, set_compounds

TYPES = ("acronym", "synonym", "alias", "related", "exclude", "compound")

DEFAULT_RULES: Dict[str, Any] = {
    "_comment": "규칙 기반 질의 확장 사전. 유형별 적용 방식이 다름 (acronym=동치 구문, synonym=OR 가중, alias=정규화 치환, related=보조 리스트, exclude=NOT+페널티, compound=복합어 분리).",
    "acronym": {
        "PDCCH": ["Physical Downlink Control Channel"], "PDSCH": ["Physical Downlink Shared Channel"], "PUSCH": ["Physical Uplink Shared Channel"],
        "AGC": ["Automatic Gain Control", "자동 이득 제어"], "DMA": ["Direct Memory Access"], "PA": ["Power Amplifier", "전력 증폭기"],
        "PHY": ["Physical Layer", "물리 계층"], "HARQ": ["Hybrid ARQ"], "RF": ["Radio Frequency"], "FIFO": ["First In First Out"],
        "ISR": ["Interrupt Service Routine", "인터럽트 서비스 루틴"], "CL": ["Change List"], "TC": ["Test Case", "테스트 케이스"],
        "VP": ["Virtual Platform", "가상 플랫폼"],
    },
    "synonym": {
        "재시작": ["리셋", "restart", "reset", "재기동"], "오류": ["에러", "error", "실패", "fault"], "지연": ["딜레이", "delay", "latency"],
        "수정": ["fix", "패치", "patch"], "검증": ["verify", "테스트", "validation"], "타이밍": ["timing", "시간"],
        "레지스터": ["register", "reg"], "인터럽트": ["interrupt", "IRQ"], "이슈": ["issue", "문제", "결함", "버그"],
    },
    "alias": {"모뎀B": "MDM9x-B1", "modem b1": "MDM9x-B1", "코딩룰": "coding rule", "코딩 규칙": "coding rule"},
    "related": {"DMA underrun": ["FIFO overflow", "버퍼 언더런", "DMA 타임아웃"], "AGC": ["RSSI", "gain table"], "PA": ["TX power", "송신 전력"],
                "PHY 재시작": ["PHY init", "PHY reset sequence"]},
    "exclude": {"시뮬레이터": ["simulator", "simulation"], "예제": ["sample", "example"]},
    "compound": {"재전송타이머": ["재전송", "타이머"], "전력제어": ["전력", "제어"], "이득테이블": ["이득", "테이블"], "인터럽트핸들러": ["인터럽트", "핸들러"]},
}

_CACHE: Dict[str, Any] = {"mtime": None, "rules": None, "index": None}


def rules_path() -> str:
    return path_for("query_rules")


def save_rules(data: Dict[str, Any]) -> str:
    p = rules_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _CACHE["mtime"] = None
    return p


def load_rules() -> Dict[str, Any]:
    p = rules_path()
    if not os.path.exists(p):
        save_rules(DEFAULT_RULES)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        mt = 0
    if _CACHE["mtime"] == mt and _CACHE["rules"] is not None:
        return _CACHE["rules"]
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = json.loads(json.dumps(DEFAULT_RULES))
    rules = {t: (data.get(t) or {}) for t in TYPES}
    _CACHE.update(mtime=mt, rules=rules, index=_build_index(rules))
    set_compounds(rules.get("compound") or {})
    return rules


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _build_index(rules: Dict[str, Any]) -> Dict[str, Any]:
    """소문자 용어 → (type, canonical, values). acronym/synonym 은 양방향."""
    idx: Dict[str, List[Tuple[str, str, List[str]]]] = {}

    def put(term: str, typ: str, canon: str, vals: List[str]) -> None:
        idx.setdefault(_norm(term), []).append((typ, canon, vals))
    for term, vals in (rules.get("acronym") or {}).items():
        vals = [str(v) for v in (vals if isinstance(vals, list) else [vals])]
        put(term, "acronym", term, vals)
        for v in vals:
            put(v, "acronym", term, [term] + [x for x in vals if x != v])
    for term, vals in (rules.get("synonym") or {}).items():
        vals = [str(v) for v in (vals if isinstance(vals, list) else [vals])]
        put(term, "synonym", term, vals)
        for v in vals:
            put(v, "synonym", term, [term] + [x for x in vals if x != v])
    for term, canon in (rules.get("alias") or {}).items():
        put(term, "alias", str(canon), [str(canon)])
    for term, vals in (rules.get("related") or {}).items():
        put(term, "related", term, [str(v) for v in (vals if isinstance(vals, list) else [vals])])
    for term, vals in (rules.get("exclude") or {}).items():
        put(term, "exclude", term, [str(v) for v in (vals if isinstance(vals, list) else [vals])])
    return idx


def _find_terms(query: str, idx: Dict[str, Any]) -> List[Tuple[str, str, str, List[str]]]:
    """질의에 등장하는 사전 용어 (긴 용어 우선, 겹침 제거). 반환 (matched_text, type, canonical, values)."""
    ql = query.lower()
    hits: List[Tuple[int, int, str, str, str, List[str]]] = []
    for term, entries in idx.items():
        if not term:
            continue
        pat = re.escape(term)
        # 영문/숫자 용어는 단어 경계(하이픈 결합 ID 'ISSUE-2001' 'CL-55301' 안의 조각은 제외), 한글은 포함 매칭(조사 결합)
        if re.match(r"^[a-z0-9 _\-]+$", term):
            rx = re.compile(r"(?<![a-z0-9\-])" + pat + r"(?![a-z0-9\-])")
        else:
            rx = re.compile(pat)
        for m in rx.finditer(ql):
            for typ, canon, vals in entries:
                hits.append((m.start(), m.end(), query[m.start():m.end()], typ, canon, vals))
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    out: List[Tuple[str, str, str, List[str]]] = []
    taken: List[Tuple[int, int]] = []
    for s, e, txt, typ, canon, vals in hits:
        if any(s < te and e > ts and typ != "exclude" for ts, te in taken):   # 겹치는 더 긴 매치 우선 (exclude 는 겹쳐도 유지)
            continue
        taken.append((s, e))
        out.append((txt, typ, canon, vals))
    return out


def expand(query: str, syn_w: float = 0.8, related_w: float = 0.4, acronym_phrase: bool = True) -> Dict[str, Any]:
    """규칙 확장 결과:
      fts_query   : FTS 용 확장 질의 (원 질의 + acronym/synonym OR 항, alias 치환)
      alt_queries : [(text, weight, kind)] 벡터/FTS 추가 리스트 (synonym 치환, acronym 치환)
      related     : [(text, weight)] 보조 리스트 (별도 융합)
      exclude     : [용어] NOT/페널티 대상
      seeds       : 그래프 시드에 추가할 canonical 이름
      fired       : 발화한 규칙 목록 (프로파일)
    """
    load_rules()
    idx = _CACHE["index"] or {}
    fired: List[Dict[str, Any]] = []
    or_groups: List[List[str]] = []
    alt: List[Tuple[str, float, str]] = []
    related: List[Tuple[str, float]] = []
    exclude: List[str] = []
    seeds: List[str] = []
    q_alias = query
    for txt, typ, canon, vals in _find_terms(query, idx):
        fired.append({"type": typ, "matched": txt, "canonical": canon, "values": vals[:6]})
        if typ == "acronym":
            phr = ['"%s"' % v.replace('"', "") if (acronym_phrase and " " in v) else v for v in vals]
            or_groups.append([txt] + phr)
            for v in vals[:2]:
                alt.append((query.replace(txt, v), 1.0, "acronym"))
            seeds.extend([canon] + vals)
        elif typ == "synonym":
            or_groups.append([txt] + vals)
            for v in vals[:2]:
                alt.append((query.replace(txt, v), syn_w, "synonym"))
        elif typ == "alias":
            q_alias = q_alias.replace(txt, canon)
            or_groups.append([txt, canon])
            seeds.append(canon)
        elif typ == "related":
            for v in vals[:3]:
                related.append((v, related_w))
        elif typ == "exclude":
            exclude.extend([txt] + vals)
    base_terms = [normalize_token(w) for w in words(q_alias)]
    parts: List[str] = []
    for t in base_terms:
        parts.append('"%s"' % t.replace('"', ""))
    for g in or_groups:
        toks = []
        for v in g:
            v = v.strip()
            if not v:
                continue
            if v.startswith('"'):
                toks.append(v)
            else:
                toks.extend('"%s"' % normalize_token(w) for w in words(v))
        if toks:
            parts.append("(" + " OR ".join(dict.fromkeys(toks)) + ")")
    fts_query = " OR ".join(dict.fromkeys(parts)) if parts else ""
    if exclude:
        ex_toks = []
        for v in exclude:
            ex_toks.extend('"%s"' % normalize_token(w) for w in words(v))
        if ex_toks and fts_query:
            fts_query = "(" + fts_query + ") NOT (" + " OR ".join(dict.fromkeys(ex_toks)) + ")"
    # 대체 질의는 가중치(acronym 1.0 > synonym) 순으로 상위 4개만 (alt 리스트 폭증 방지)
    alt = sorted(alt, key=lambda a: -a[1])
    return {"query": query, "query_alias": q_alias, "fts_query": fts_query if fired else "", "alt_queries": alt[:4],
            "related": related[:4], "exclude": [e.lower() for e in exclude], "seeds": list(dict.fromkeys(seeds)), "fired": fired}


def add_rule(typ: str, term: str, values: Any, source: str = "manual") -> Dict[str, Any]:
    if typ not in TYPES:
        raise KeyError("type must be one of %s" % (TYPES,))
    data = _read_raw()
    sect = data.setdefault(typ, {})
    if typ == "alias":
        sect[term] = str(values if not isinstance(values, list) else values[0])
    else:
        cur = sect.get(term)
        vals = values if isinstance(values, list) else [str(values)]
        if isinstance(cur, list):
            for v in vals:
                if v not in cur:
                    cur.append(v)
        else:
            sect[term] = list(dict.fromkeys(vals))
    save_rules(data)
    return {"type": typ, "term": term, "values": sect[term], "source": source}


def remove_rule(typ: str, term: str, value: Optional[str] = None) -> bool:
    data = _read_raw()
    sect = data.get(typ) or {}
    if term not in sect:
        return False
    if value and isinstance(sect[term], list):
        sect[term] = [v for v in sect[term] if v != value]
        if not sect[term]:
            del sect[term]
    else:
        del sect[term]
    save_rules(data)
    return True


def _read_raw() -> Dict[str, Any]:
    p = rules_path()
    if not os.path.exists(p):
        return json.loads(json.dumps(DEFAULT_RULES))
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def stats() -> Dict[str, int]:
    r = load_rules()
    return {t: len(r.get(t) or {}) for t in TYPES}
