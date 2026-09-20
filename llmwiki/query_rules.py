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

from . import atomicio
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


def _guard_dict(data: Any, what: str) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("%s 는 사전(JSON object) 이어야 합니다 (받은 값: %s)" % (what, type(data).__name__))
    return data


def save_rules(data: Dict[str, Any]) -> str:
    """규칙 사전 저장. 사전이 아니면 거절한다 — 잘못 저장하면 이후 모든 질의가 깨지기 때문(2026-09-15 멍키 테스트로 발견)."""
    _guard_dict(data, "query_rules")
    # 쓰는 도중 다른 요청이 읽어도 깨진 파일을 보지 않도록 원자적 교체
    p = atomicio.write_json(rules_path(), data)
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
    data = atomicio.read_json(p)
    if data is None:
        data = json.loads(json.dumps(DEFAULT_RULES))
    if not isinstance(data, dict):
        # 파일이 손상되었더라도(잘못 저장·수동 편집) 질의가 통째로 죽지 않도록 기본값으로 되돌린다
        try:
            from . import logging_setup as _ls
            _ls.log("error", "query_rules 파일이 사전 형식이 아닙니다 → 기본 규칙 사용: %s" % p, "query")
        except Exception:
            pass
        data = json.loads(json.dumps(DEFAULT_RULES))
    rules = {t: (data.get(t) if isinstance(data.get(t), dict) else {}) for t in TYPES}
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
    seen: set = set()
    for s, e, txt, typ, canon, vals in hits:
        key = (s, e, typ, canon)
        if key in seen:
            continue
        # 겹치는 **더 긴** 매치가 이미 있으면 버린다 (예: 'DMA underrun' 이 있으면 'DMA' 는 버린다).
        # 다만 **같은 구간**이면 유형이 달라도 함께 발화시킨다 — 한 낱말이 acronym 이면서 synonym 일 수 있고,
        # 예전에는 먼저 걸린 유형 하나만 살아남아 나머지 규칙이 조용히 무시됐다 (2026-09-16).
        if any((s, e) != (ts, te) and s < te and e > ts and typ != "exclude" for ts, te in taken):
            continue
        seen.add(key)
        taken.append((s, e))
        out.append((txt, typ, canon, vals))
    return out


def _max_rounds(explicit: Optional[int] = None) -> int:
    if explicit is not None:
        return max(1, int(explicit))
    try:
        from .tuning import T
        return max(1, int(T.get("query_rules_max_rounds") or 2))
    except Exception:
        return 2


def expand(query: str, syn_w: float = 0.8, related_w: float = 0.4, acronym_phrase: bool = True,
           max_rounds: Optional[int] = None) -> Dict[str, Any]:
    """규칙 확장 결과:
      fts_query   : FTS 용 확장 질의 (원 질의 + acronym/synonym OR 항, alias 치환)
      alt_queries : [(text, weight, kind)] 벡터/FTS 추가 리스트 (synonym 치환, acronym 치환)
      related     : [(text, weight)] 보조 리스트 (별도 융합)
      exclude     : [용어] NOT/페널티 대상
      seeds       : 그래프 시드에 추가할 canonical 이름
      fired       : 발화한 규칙 목록 (프로파일; round = 몇 번째 접기에서 나왔는지)

    **여러 번 접어 적용한다(fixed point, 상한 `query_rules_max_rounds`).** 1회만 적용하면
    `TAT → Turn Around Time`(acronym) 뒤에 `Turn Around Time → 응답시간`(synonym) 이 걸려 있어도
    두 번째 규칙이 조용히 무시된다 — 사용자가 실제로 겪은 문제다 (2026-09-16).
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
    done: set = set()            # (normalized term, type, canonical) — 같은 규칙을 두 번 적용하지 않는다

    def apply(txt: str, typ: str, canon: str, vals: List[str], rnd: int) -> List[str]:
        """규칙 하나를 반영하고, 이 규칙이 **새로 들여온 말** 을 돌려준다(다음 라운드의 입력)."""
        nonlocal q_alias
        # 같은 (유형, 대표어) 는 한 번만. 사전은 양방향이라 'TAT→Turn Around Time' 과
        # 'Turn Around Time→TAT' 이 같은 OR 묶음을 두 번 만든다.
        key = (typ, _norm(canon))
        if key in done:
            return []
        done.add(key)
        fired.append({"type": typ, "matched": txt, "canonical": canon, "values": vals[:6], "round": rnd})
        in_query = txt.lower() in query.lower()      # 2라운드 이후의 말은 원 질의에 없다 → 치환 질의를 만들지 않는다
        if typ == "acronym":
            phr = ['"%s"' % v.replace('"', "") if (acronym_phrase and " " in v) else v for v in vals]
            or_groups.append([txt] + phr)
            if in_query:
                for v in vals[:2]:
                    alt.append((query.replace(txt, v), 1.0, "acronym"))
            seeds.extend([canon] + vals)
            return [canon] + list(vals)
        if typ == "synonym":
            or_groups.append([txt] + vals)
            if in_query:
                for v in vals[:2]:
                    alt.append((query.replace(txt, v), syn_w, "synonym"))
            return [canon] + list(vals)
        if typ == "alias":
            q_alias = q_alias.replace(txt, canon)
            or_groups.append([txt, canon])
            seeds.append(canon)
            return [canon]
        if typ == "related":
            for v in vals[:3]:
                related.append((v, related_w))
            return []                     # 연관어는 정밀도 보호를 위해 다시 펼치지 않는다
        if typ == "exclude":
            exclude.extend([txt] + vals)
            return []
        return []

    # 1라운드: 질의 문자열에서 구간 매칭. 이후 라운드: 앞 라운드가 들여온 말을 사전에서 **정확히** 찾는다.
    pending: List[str] = []
    for txt, typ, canon, vals in _find_terms(query, idx):
        pending.extend(apply(txt, typ, canon, vals, 1))
    for rnd in range(2, _max_rounds(max_rounds) + 1):
        nxt: List[str] = []
        for term in dict.fromkeys(pending):
            for typ, canon, vals in idx.get(_norm(term)) or []:
                if typ == "alias":
                    continue          # 치환은 원 질의에만 적용한다 (연쇄 치환은 의미를 바꾼다)
                nxt.extend(apply(term, typ, canon, vals, rnd))
        if not nxt:
            break
        pending = nxt
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
    raw = atomicio.read_json(rules_path())
    return raw if isinstance(raw, dict) else json.loads(json.dumps(DEFAULT_RULES))


def stats() -> Dict[str, int]:
    r = load_rules()
    return {t: len(r.get(t) or {}) for t in TYPES}


def merge_rules(incoming: Dict[str, Any], replace: bool = False) -> Dict[str, Any]:
    """다른 사전 파일을 **합친다**. 기본은 값 합치기(기존 항목을 지우지 않음) — 조직 사전을 조금씩 키우는 용도.

    replace=True 면 같은 용어의 값을 통째로 바꾼다. `_` 로 시작하는 키(_comment 등)는 무시한다.
    반환: 유형별 (추가된 용어 수, 값이 늘어난 용어 수).
    """
    _guard_dict(incoming, "merge 대상")
    data = _read_raw()
    report: Dict[str, Dict[str, int]] = {}
    for typ in TYPES:
        sect_in = incoming.get(typ)
        if not isinstance(sect_in, dict):
            continue
        sect = data.setdefault(typ, {})
        added = grown = 0
        for term, vals in sect_in.items():
            if str(term).startswith("_"):
                continue                     # _comment_* 같은 설명 키는 규칙이 아니다
            if typ == "alias":
                v = str(vals if not isinstance(vals, list) else (vals or [""])[0])
                if term not in sect:
                    added += 1
                elif sect[term] != v:
                    grown += 1
                if replace or term not in sect:
                    sect[term] = v
                continue
            new_vals = [str(v) for v in (vals if isinstance(vals, list) else [vals])]
            cur = sect.get(term)
            if not isinstance(cur, list):
                sect[term] = list(dict.fromkeys(new_vals))
                added += 1
                continue
            if replace:
                if cur != new_vals:
                    grown += 1
                sect[term] = list(dict.fromkeys(new_vals))
                continue
            before = len(cur)
            for v in new_vals:
                if v not in cur:
                    cur.append(v)
            if len(cur) > before:
                grown += 1
        report[typ] = {"added": added, "grown": grown, "total": len(sect)}
    save_rules(data)
    return {"merged": report, "path": rules_path(), "replace": replace}


def lint(max_rounds: Optional[int] = None) -> Dict[str, Any]:
    """사전 자체의 문제를 찾는다 — 사전이 커질수록 손으로는 못 잡는다.

    무엇을 보는가
      duplicate   : 같은 말이 같은 유형에 두 번(대소문자·공백만 다른 경우 포함)
      cross_type  : 같은 말이 여러 유형에 걸쳐 있다 (이제는 **함께 발화**하므로 오류가 아니라 알림)
      self_ref    : 값이 자기 자신을 가리킨다 (PDCCH → PDCCH)
      alias_chain : alias 가 또 다른 alias 의 출발점을 가리킨다 (A→B, B→C — 치환은 한 번만 하므로 B 에서 멈춘다)
      deep_chain  : max_rounds 안에 다 펼쳐지지 않는 사슬 (A→B→C→D 인데 rounds=2)
      empty       : 값이 비었다
    반환의 `ok` 는 error 가 없을 때 True. warn 은 알림이다.
    """
    rules = load_rules()
    idx = _CACHE["index"] or {}
    rounds = _max_rounds(max_rounds)
    issues: List[Dict[str, str]] = []

    def add(level: str, kind: str, term: str, detail: str) -> None:
        issues.append({"level": level, "kind": kind, "term": term, "detail": detail})

    # 유형 안의 중복 · 자기참조 · 빈 값
    for typ in TYPES:
        seen: Dict[str, str] = {}
        for term, vals in (rules.get(typ) or {}).items():
            n = _norm(term)
            if n in seen and seen[n] != term:
                add("error", "duplicate", term, "같은 유형 '%s' 에 '%s' 와 사실상 같은 항목 (대소문자·공백만 다름)" % (typ, seen[n]))
            seen[n] = term
            lst = vals if isinstance(vals, list) else [vals]
            lst = [str(v) for v in lst]
            if not [v for v in lst if str(v).strip()]:
                add("error", "empty", term, "유형 '%s' 의 값이 비어 있습니다" % typ)
            if any(_norm(v) == n for v in lst):
                add("warn", "self_ref", term, "유형 '%s' 의 값이 자기 자신을 포함합니다" % typ)

    # 한 말이 여러 유형에 걸침 — 이제는 함께 발화한다(알림). 어떤 유형들인지 알려 준다.
    for term, entries in idx.items():
        kinds = sorted({t for t, _c, _v in entries})
        if len(kinds) > 1:
            add("warn", "cross_type", term, "여러 유형에서 발화합니다: %s (의도한 것이면 그대로 두세요)" % ", ".join(kinds))

    # alias 사슬 — 치환은 한 번만 한다
    aliases = {_norm(k): str(v) for k, v in (rules.get("alias") or {}).items()}
    for term, canon in (rules.get("alias") or {}).items():
        if _norm(canon) in aliases:
            add("error", "alias_chain", term, "'%s' → '%s' 인데 '%s' 도 별칭입니다 → '%s' 까지 가지 않습니다. 최종 표기로 직접 적으세요"
                % (term, canon, canon, aliases[_norm(canon)]))

    # 사슬 깊이 — max_rounds 안에 다 펼쳐지는가
    def depth(start: str) -> int:
        """`expand` 의 라운드와 **같은 방식**으로 센다: 한 라운드 = (유형, 대표어) 묶음 하나를 새로 적용하는 것.
        같은 동의어 묶음 안의 낱말끼리는 오가도 라운드가 늘지 않는다 (예전에는 이것을 세서 깊이가 부풀었다)."""
        applied: set = set()
        frontier = [start]
        rnd = 0
        while frontier and rnd < 8:
            nxt: List[str] = []
            grew = False
            for term in dict.fromkeys(frontier):
                for typ, canon, vals in idx.get(_norm(term)) or []:
                    if typ in ("related", "exclude", "alias"):
                        continue
                    key = (typ, _norm(canon))
                    if key in applied:
                        continue
                    applied.add(key)
                    grew = True
                    nxt.extend([canon] + list(vals))
            if not grew:
                break
            rnd += 1
            frontier = nxt
        return rnd
    for typ in ("acronym", "synonym"):
        for term in (rules.get(typ) or {}):
            dp = depth(term)
            if dp > rounds:
                add("warn", "deep_chain", term, "사슬 깊이 %d 인데 query_rules_max_rounds=%d — 끝까지 펼쳐지지 않습니다" % (dp, rounds))

    errors = [i for i in issues if i["level"] == "error"]
    return {"ok": not errors, "errors": len(errors), "warnings": len(issues) - len(errors),
            "issues": issues, "stats": {t: len(rules.get(t) or {}) for t in TYPES},
            "max_rounds": rounds, "path": rules_path()}
