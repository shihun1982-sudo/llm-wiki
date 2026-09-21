# -*- coding: utf-8 -*-
r"""규칙 기반 질의 확장 사전 (query_rules.json) — LLM 없이 결정적으로 질의를 확장한다.

## 왜 규칙인가

질의 확장에는 두 길이 있다. LLM 에게 다시 쓰게 하거나(query_expand), 사전으로 넓히거나.
사전 쪽은 **토큰 0, 수 ms, 결정적**이고 무엇보다 **왜 그렇게 넓어졌는지 설명된다**. 사내 위키처럼 용어가
정해져 있는 곳에서는 이쪽이 비용 대비 효과가 가장 크다. 그래서 이 파일이 검색 품질의 첫 번째 손잡이다.

## 유형 = 레지스트리 (2026-09-19)

예전에는 유형 6개가 코드 곳곳에 하드코딩돼 있었다 — 유형 하나를 늘리려면 `TYPES` 튜플, `DIRECTION`,
`HOW`, `_build_index` 의 전용 루프, `expand` 의 `if typ ==` 분기, `lint`·`explain`·`add_rule` 의 목록까지
아홉 자리를 고쳐야 했고, 바깥(CLI·Web·MCP·문서)까지 합치면 파일 15개였다. 한 자리만 빠뜨리면 그 유형은
**조용히 무시**된다 — 이 저장소가 반복해서 겪은 실패 모양이다.

지금은 유형이 **아래 `RULE_TYPES` 레지스트리의 항목 하나**다. 새 유형은 `register_type(RuleType(...))`
한 번이면 색인·확장·린트·설명·통계·병합·CLI·Web·MCP 가 전부 따라온다 (§확장하는 법).

| 유형 | 방향 | 뜻 | 어디로 들어가나 |
|---|---|---|---|
| `acronym` | 양방향 | 완전 동치 (PDCCH ⇄ Physical Downlink Control Channel) | FTS 구문 OR (동일 가중) · 벡터 치환 질의 · 그래프 시드 |
| `synonym` | 양방향 | 준동치 (재시작 ≈ 리셋 ≈ restart) | FTS OR (syn_w) · 벡터 치환 질의 |
| `alias` | 일방 | 표기 정규화 (모뎀B → MDM9x-B1) | canonical 치환 (+원 표기 OR) · 그래프 시드 |
| `related` | 일방 | 연관어 (DMA underrun ~ FIFO overflow) | 보조 리스트 (related_w) — 주 질의에 섞지 않는다 |
| `exclude` | 일방 | 잡음 배제 | FTS NOT + 후보 페널티 |
| `compound` | 분리 | 복합어 (재전송타이머 → 재전송 타이머) | 색인·질의 토크나이저 |
| `context` | 문맥 | **뜻이 갈리는 말** (PA = Power Amplifier \| Product Area) | 조건이 맞을 때만 OR (context_w) |
| `hypernym` | 상하 | 상위어 ⊃ 하위어 (메모리 오류 ⊃ DMA 오버런) | 보조 리스트, 내려갈 때와 올라갈 때 가중이 다르다 |
| `unit` | 수치 | 단위 동치 (4KB ⇄ 4096 byte) | 질의의 `숫자+단위` 를 찾아 환산값 OR |

파일 구조 예:

    {"acronym": {"PDCCH": ["Physical Downlink Control Channel"]},
     "alias":   {"모뎀B": "MDM9x-B1"},
     "context": {"PA": [{"when": ["RF", "전력"], "then": ["Power Amplifier"]},
                        {"when": ["일정", "조직"], "then": ["Product Area"]}]},
     "hypernym":{"메모리 오류": ["DMA 오버런", "FIFO 언더런"]},
     "unit":    {"KB": {"factor": 1024, "base": "byte", "aliases": ["킬로바이트"]}}}

## 확장하는 법 (새 유형 추가)

`RuleType` 하나를 만들어 `register_type()` 하면 끝이다. 나머지는 전부 레지스트리를 돈다.

    register_type(RuleType(
        name="antonym", label="반의어", direction="일방",
        how="후보 페널티 (exclude 와 같은 길)", value="list",
        apply=lambda acc, txt, canon, vals, rnd, in_q: acc.exclude.extend(vals) or [],
    ))

`match="scan"` 으로 두면 사전 용어 매칭 대신 **직접 질의 문자열을 훑는** 유형을 만들 수 있다
(`unit` 이 그 예 — 숫자+단위는 사전 키로 못 적는다).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import atomicio
from .config import path_for
from .textutil import words, normalize_token, set_compounds

_CACHE: Dict[str, Any] = {"mtime": None, "rules": None, "index": None, "sym": None}


def _t(key: str, fallback: float) -> float:
    """튜닝 값 (tuning.py 가 아직 없거나 키가 없으면 기본값). 규칙 유형이 자기 가중치를 읽는 통로."""
    try:
        from .tuning import T
        v = T.get(key)
        return float(fallback if v is None else v)
    except Exception:
        return float(fallback)


class Acc:
    """확장 결과 누적기 — 규칙 유형의 `apply` 가 여기에 기여한다.

    채널이 다섯이고, 각 채널의 뜻이 다르다. 유형을 새로 만들 때 **어디에 넣느냐**가 곧 설계다.
      or_groups : FTS 질의에 `( A OR B )` 로 들어간다 — 주 질의를 넓힌다 (recall↑, precision 위험)
      alt       : 벡터/FTS 의 **별도 질의** (text, weight, kind, canonical) — 주 질의를 오염시키지 않는다
      related   : 보조 리스트 (text, weight, canonical) — 융합에만 참여한다 (precision 보호)
      exclude   : FTS NOT + 후보 페널티
      seeds     : 그래프 시드 엔티티 이름
    """

    __slots__ = ("query", "q_alias", "or_groups", "alt", "related", "exclude", "seeds",
                 "syn_w", "related_w", "acronym_phrase")

    def __init__(self, query: str, syn_w: float, related_w: float, acronym_phrase: bool):
        self.query = query
        self.q_alias = query
        self.or_groups: List[List[str]] = []
        self.alt: List[Tuple[str, float, str, str]] = []
        self.related: List[Tuple[str, float, str]] = []
        self.exclude: List[str] = []
        self.seeds: List[str] = []
        self.syn_w, self.related_w, self.acronym_phrase = syn_w, related_w, acronym_phrase


class RuleType:
    """규칙 유형 하나의 **선언**. 색인 방식·방향·적용 함수·값 모양을 한자리에 모은다.

    name      : query_rules.json 의 절 이름
    label     : 화면·CLI 에 보이는 한글 이름
    direction : 양방향 | 일방 | 분리 | 문맥 | 상하 | 수치 (설명용)
    how       : 어떻게 적용되는지 한 줄 (explain·화면이 그대로 보여 준다)
    value     : 값 모양 — "list"(문자열 목록) | "str"(문자열 하나) | "cond"(조건 항목 목록) | "map"(객체)
    match     : "term" 이면 사전 용어를 질의에서 찾는다. "scan" 이면 `scan(acc, rules)` 가 직접 훑는다
    symmetric : 값도 색인한다 (값이 질의에 있으면 키 쪽으로도 발화)
    chains    : 이 유형이 들여온 말을 다음 라운드의 입력으로 쓰는가 (연쇄 확장)
    indexed   : 질의 구간 매칭 대상인가 (compound 는 토크나이저용이라 False)
    apply     : (acc, matched, canonical, values, round, in_query) -> 새로 들여온 말 목록. None 을 돌려주면 '발화하지 않음'
    scan      : match="scan" 일 때 (acc, section) -> [fired 레코드]
    """

    __slots__ = ("name", "label", "direction", "how", "value", "match", "symmetric",
                 "back", "chains", "indexed", "apply", "scan", "since")

    def __init__(self, name: str, label: str, direction: str, how: str, *, value: str = "list",
                 match: str = "term", symmetric: bool = False, back: str = "siblings",
                 chains: bool = True, indexed: bool = True,
                 apply: Optional[Callable] = None, scan: Optional[Callable] = None, since: str = ""):
        self.name, self.label, self.direction, self.how = name, label, direction, how
        self.value, self.match, self.symmetric, self.back = value, match, symmetric, back
        self.chains, self.indexed = chains, indexed
        self.apply, self.scan, self.since = apply, scan, since

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "label": self.label, "direction": self.direction, "how": self.how,
                "value": self.value, "match": self.match, "symmetric": self.symmetric,
                "chains": self.chains, "indexed": self.indexed, "since": self.since}


RULE_TYPES: Dict[str, RuleType] = {}


def register_type(rt: RuleType) -> RuleType:
    """규칙 유형 등록. 플러그인·사내 확장도 이걸 부르면 전 창구에 자동으로 나타난다."""
    RULE_TYPES[rt.name] = rt
    globals()["TYPES"] = tuple(RULE_TYPES)
    globals()["DIRECTION"] = {k: v.direction for k, v in RULE_TYPES.items()}
    globals()["HOW"] = {k: v.how for k, v in RULE_TYPES.items()}
    _CACHE["mtime"] = None      # 유형이 늘면 색인을 다시 만든다
    return rt


# ---------------------------------------------------------------- 유형별 적용 함수
def _ap_acronym(acc: Acc, txt: str, canon: str, vals: Any, rnd: int, in_query: bool) -> List[str]:
    phr = ['"%s"' % v.replace('"', "") if (acc.acronym_phrase and " " in v) else v for v in vals]
    acc.or_groups.append([txt] + phr)
    if in_query:
        for v in vals[:2]:
            # 4번째 원소 = 이 대체 질의를 만든 규칙의 대표어. "이 규칙이 실제로 답에 기여했나" 를 따지려면
            # 출처가 있어야 한다 (llmwiki/ruleeffect.py). 앞 3개 원소는 그대로라 기존 코드는 안 깨진다.
            acc.alt.append((acc.query.replace(txt, v), 1.0, "acronym", canon))
    acc.seeds.extend([canon] + list(vals))
    return [canon] + list(vals)


def _ap_synonym(acc: Acc, txt: str, canon: str, vals: Any, rnd: int, in_query: bool) -> List[str]:
    acc.or_groups.append([txt] + list(vals))
    if in_query:
        for v in vals[:2]:
            acc.alt.append((acc.query.replace(txt, v), acc.syn_w, "synonym", canon))
    return [canon] + list(vals)


def _ap_alias(acc: Acc, txt: str, canon: str, vals: Any, rnd: int, in_query: bool) -> List[str]:
    acc.q_alias = acc.q_alias.replace(txt, canon)
    acc.or_groups.append([txt, canon])
    acc.seeds.append(canon)
    return [canon]


def _ap_related(acc: Acc, txt: str, canon: str, vals: Any, rnd: int, in_query: bool) -> List[str]:
    for v in list(vals)[:3]:
        acc.related.append((v, acc.related_w, canon))     # 3번째 = 출처 규칙 대표어
    return []                     # 연관어는 정밀도 보호를 위해 다시 펼치지 않는다


def _ap_exclude(acc: Acc, txt: str, canon: str, vals: Any, rnd: int, in_query: bool) -> List[str]:
    acc.exclude.extend([txt] + list(vals))
    return []


def _ap_context(acc: Acc, txt: str, canon: str, vals: Any, rnd: int, in_query: bool) -> Optional[List[str]]:
    """**뜻이 갈리는 말**을 문맥이 맞을 때만 넓힌다 (word-sense disambiguation).

    사내 위키에서 정밀도를 가장 많이 깎는 것은 같은 약어가 팀마다 다른 뜻인 경우다.
    `PA` 를 acronym 에 넣으면 조직 문서 질의에도 "Power Amplifier" 가 딸려 와 엉뚱한 문단을 끌어온다.
    그래서 `when` 중 하나라도 질의에 있을 때만 `then` 으로 넓힌다. 조건이 하나도 안 맞으면 **발화하지 않는다**
    (None 을 돌려준다) — 억지로 넓히느니 가만히 있는 편이 낫다.
    """
    ql = acc.query.lower()
    out: List[str] = []
    for c in (vals if isinstance(vals, list) else []):
        if not isinstance(c, dict):
            continue
        when = [str(w).lower() for w in (c.get("when") or []) if str(w).strip()]
        then = [str(t) for t in (c.get("then") or []) if str(t).strip()]
        if not then:
            continue
        if when and not any(w in ql for w in when):
            continue
        acc.or_groups.append([txt] + then)
        if in_query:
            for v in then[:2]:
                acc.alt.append((acc.query.replace(txt, v), _t("context_w", 0.9), "context", canon))
        acc.seeds.extend(then)
        out.extend(then)
    return out or None


def _ap_hypernym(acc: Acc, txt: str, canon: str, vals: Any, rnd: int, in_query: bool) -> List[str]:
    """상위어 ⊃ 하위어. **방향에 따라 가중이 다르다.**

    · 내려가기(질의에 상위어 → 하위어를 본다): 넓히기다. recall 은 오르지만 정밀도 위험이 있어
      주 질의가 아니라 **보조 리스트**로 넣는다 (`hypernym_down_w`).
    · 올라가기(질의에 하위어 → 상위어를 본다): 더 위험하다(상위어 문서는 대개 일반론이다) → 더 낮은 가중
      (`hypernym_up_w`).
    동의어처럼 같은 가중으로 섞으면 분류 체계가 곧 잡음이 된다 — 그래서 유형을 따로 둔다.
    """
    down = _norm(txt) == _norm(canon)
    w = _t("hypernym_down_w", 0.35) if down else _t("hypernym_up_w", 0.2)
    for v in list(vals)[:4]:
        acc.related.append((v, w, canon))
    return []                     # 분류 체계는 연쇄로 펼치지 않는다 (금방 전 우주가 된다)


_NUM_UNIT_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*([A-Za-z가-힣]{1,12})(?![\w])")


def _scan_unit(acc: Acc, section: Dict[str, Any]) -> List[Dict[str, Any]]:
    """질의의 `숫자+단위` 를 찾아 **같은 값의 다른 표기**를 OR 로 넣는다.

    왜 사전 용어로 못 하나: 키가 숫자와 함께 와야 뜻이 생긴다. "KB" 만 넓혀 봐야 소용없고
    "4KB" 가 "4096" 과 같다는 것이 필요하다. HW·모뎀 문서에서는 같은 값을 `4KB` · `4096바이트` ·
    `4096 byte` 로 제각각 적어서, 숫자가 든 질의가 문서를 못 찾는 일이 흔하다.

    값 모양: {"KB": {"factor": 1024, "base": "byte", "aliases": ["킬로바이트", "kilobyte"]}}
    환산값이 정수면 정수로 적는다 (4096.0 이 아니라 4096 — 문서에 그렇게 쓰여 있다).
    """
    fired: List[Dict[str, Any]] = []
    if not section:
        return fired
    lut = {_norm(k): (k, v) for k, v in section.items() if isinstance(v, dict)}
    for m in _NUM_UNIT_RE.finditer(acc.query):
        num_s, unit = m.group(1), m.group(2)
        hit = lut.get(_norm(unit))
        if not hit:
            continue
        key, spec = hit
        try:
            num = float(num_s.replace(",", ""))
        except ValueError:
            continue
        forms: List[str] = [m.group(0)]
        for a in (spec.get("aliases") or []):
            forms.append("%s %s" % (num_s, a))
        factor, base = spec.get("factor"), str(spec.get("base") or "")
        if isinstance(factor, (int, float)) and factor:
            conv = num * float(factor)
            conv_s = str(int(conv)) if abs(conv - int(conv)) < 1e-9 else ("%g" % conv)
            forms.append(conv_s)
            if base:
                forms.append("%s %s" % (conv_s, base))
        forms = [f for f in dict.fromkeys(forms) if f.strip()]
        if len(forms) < 2:
            continue
        acc.or_groups.append(forms)
        fired.append({"type": "unit", "matched": m.group(0), "canonical": key, "values": forms[1:6], "round": 1})
    return fired


# ---------------------------------------------------------------- 유형 등록 (순서 = 화면·문서 순서)
register_type(RuleType("acronym", "약어", "양방향",
                       "FTS 구문(phrase) OR · 벡터 치환 질의 · 그래프 시드 (동일 가중)",
                       symmetric=True, apply=_ap_acronym))
register_type(RuleType("synonym", "동의어", "양방향",
                       "FTS OR (syn_w) · 벡터 치환 질의",
                       symmetric=True, apply=_ap_synonym))
register_type(RuleType("alias", "별칭(정규화)", "일방",
                       "canonical 로 치환 (+원 표기 OR) · 그래프 시드는 canonical — 반대 방향으로는 넓히지 않는다",
                       value="str", apply=_ap_alias))
register_type(RuleType("related", "관련어", "일방",
                       "보조 리스트 (related_w) 로 따로 융합 — 주 질의에 섞지 않는다",
                       back="key", chains=False, apply=_ap_related))
register_type(RuleType("exclude", "제외어", "일방",
                       "FTS NOT + 후보 페널티 (exclude_penalty)",
                       chains=False, apply=_ap_exclude))
register_type(RuleType("compound", "복합어 분리", "분리",
                       "색인·질의 토크나이저에서 부분으로 분리",
                       indexed=False, chains=False))
# ---- 2026-09-19 추가: 검색 품질을 더 끌어올리는 세 가지 ----
register_type(RuleType("context", "문맥 의존 (뜻 가르기)", "문맥",
                       "`when` 중 하나가 질의에 있을 때만 `then` 으로 OR 확장 (context_w). 조건이 안 맞으면 발화하지 않는다",
                       value="cond", apply=_ap_context, since="2026-09-19"))
register_type(RuleType("hypernym", "상위어 ⊃ 하위어", "상하",
                       "보조 리스트. 내려갈 때 hypernym_down_w, 올라갈 때 hypernym_up_w — 방향에 따라 가중이 다르다",
                       symmetric=True, back="key", chains=False, apply=_ap_hypernym, since="2026-09-19"))
register_type(RuleType("unit", "단위·수치 동치", "수치",
                       "질의의 `숫자+단위` 를 찾아 환산값·다른 표기를 OR (4KB ⇄ 4096 ⇄ 4096 byte)",
                       value="map", match="scan", indexed=False, chains=False,
                       scan=_scan_unit, since="2026-09-19"))

#: 하위 호환 — 예전 코드가 `TYPES` / `DIRECTION` / `HOW` 를 그대로 쓴다 (register_type 이 갱신한다)
TYPES: Tuple[str, ...] = tuple(RULE_TYPES)
DIRECTION: Dict[str, str] = {k: v.direction for k, v in RULE_TYPES.items()}
HOW: Dict[str, str] = {k: v.how for k, v in RULE_TYPES.items()}

DEFAULT_RULES: Dict[str, Any] = {
    "_comment": "규칙 기반 질의 확장 사전. 유형마다 적용 방식이 다릅니다 — `python -m llmwiki rules types` 로 전체 표를 보세요.",
    "acronym": {
        "PDCCH": ["Physical Downlink Control Channel"], "PDSCH": ["Physical Downlink Shared Channel"], "PUSCH": ["Physical Uplink Shared Channel"],
        "AGC": ["Automatic Gain Control", "자동 이득 제어"], "DMA": ["Direct Memory Access"],
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
    "related": {"DMA underrun": ["FIFO overflow", "버퍼 언더런", "DMA 타임아웃"], "AGC": ["RSSI", "gain table"],
                "PHY 재시작": ["PHY init", "PHY reset sequence"]},
    "exclude": {"시뮬레이터": ["simulator", "simulation"], "예제": ["sample", "example"]},
    "compound": {"재전송타이머": ["재전송", "타이머"], "전력제어": ["전력", "제어"], "이득테이블": ["이득", "테이블"], "인터럽트핸들러": ["인터럽트", "핸들러"]},
    # PA 는 팀마다 뜻이 다르다 — acronym 에 넣으면 조직 문서 질의까지 오염된다. 그래서 문맥으로 가른다.
    "context": {
        "PA": [{"when": ["rf", "전력", "송신", "tx", "증폭"], "then": ["Power Amplifier", "전력 증폭기"], "note": "RF 문맥"},
               {"when": ["일정", "조직", "담당", "제품"], "then": ["Product Area"], "note": "조직/일정 문맥"}],
        "MAC": [{"when": ["계층", "프로토콜", "phy", "rlc"], "then": ["Medium Access Control"], "note": "프로토콜 문맥"},
                {"when": ["주소", "이더넷", "네트워크"], "then": ["MAC address", "하드웨어 주소"], "note": "네트워크 문맥"}],
    },
    "hypernym": {
        "메모리 오류": ["DMA 오버런", "FIFO 언더런", "버퍼 오버플로", "힙 손상"],
        "타이밍 이슈": ["클럭 스큐", "셋업 홀드 위반", "지터"],
        "전력 문제": ["전류 누설", "전압 강하", "발열"],
    },
    "unit": {
        "KB": {"factor": 1024, "base": "byte", "aliases": ["킬로바이트", "kilobyte", "kbyte"]},
        "MB": {"factor": 1048576, "base": "byte", "aliases": ["메가바이트", "megabyte"]},
        "ms": {"factor": 0.001, "base": "초", "aliases": ["밀리초", "millisecond", "밀리세컨드"]},
        "us": {"factor": 0.000001, "base": "초", "aliases": ["마이크로초", "microsecond"]},
        "MHz": {"factor": 1000000, "base": "Hz", "aliases": ["메가헤르츠", "megahertz"]},
    },
}


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


def fill_defaults(path: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """query_rules.json 에 빠진 type 절(acronym/synonym/alias/related/exclude/compound)을 빈 사전으로 채워 쓴다 (있는 값 유지).
    파일이 없으면 DEFAULT_RULES 로 만든다. 반환 {path, added, written}."""
    p = path or rules_path()
    prev = atomicio.read_json(p)
    if prev is None:
        prev = json.loads(json.dumps(DEFAULT_RULES))
        created = True
    else:
        created = False
    if not isinstance(prev, dict):
        raise ValueError("%s 는 사전(JSON object) 이어야 합니다" % p)
    out: Dict[str, Any] = {}
    if "_comment" not in prev:
        out["_comment"] = DEFAULT_RULES["_comment"]
    out.update(prev)
    added: List[str] = []
    for t in TYPES:
        if not isinstance(out.get(t), dict):
            out[t] = {}
            added.append(t)
    rep = {"path": p, "added": added, "created": created, "dry_run": dry_run, "written": False, "changed": bool(added or created)}
    if rep["changed"] and not dry_run:
        atomicio.write_json(p, out)
        if p == rules_path():
            _CACHE["mtime"] = None
        rep["written"] = True
    return rep


def load_rules() -> Dict[str, Any]:
    p = rules_path()
    if not os.path.exists(p):
        save_rules(DEFAULT_RULES)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        mt = 0
    sym = _related_symmetric()
    if _CACHE["mtime"] == mt and _CACHE["rules"] is not None and _CACHE.get("sym") == sym:
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
    # 튜닝 related_symmetric 이 바뀌면 파일이 그대로여도 색인을 다시 만든다 (캐시 키에 플래그 포함)
    _CACHE.update(mtime=mt, rules=rules, index=_build_index(rules, sym), sym=sym)
    set_compounds(rules.get("compound") or {})
    return rules


def _related_symmetric() -> bool:
    """튜닝 `related_symmetric` (기본 false). related 를 값→키 방향으로도 색인할지."""
    try:
        from .tuning import T
        return bool(T.get("related_symmetric"))
    except Exception:
        return False


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _as_list(vals: Any) -> List[str]:
    return [str(v) for v in (vals if isinstance(vals, list) else [vals])]


def _build_index(rules: Dict[str, Any], related_symmetric: bool = False) -> Dict[str, Any]:
    """소문자 용어 → [(type, canonical, values)]. **레지스트리를 돈다** — 유형을 늘려도 여기는 그대로다.

    `symmetric` 유형은 값 쪽도 색인해 "값이 질의에 있으면 키로도 넓힌다"(양방향). `related` 는 기본이 일방이고
    튜닝 `related_symmetric` 으로만 양방향이 된다 (정밀도 보호 — 연관어를 양쪽으로 쓰면 금세 번진다).
    """
    idx: Dict[str, List[Tuple[str, str, Any]]] = {}

    def put(term: str, typ: str, canon: str, vals: Any) -> None:
        idx.setdefault(_norm(term), []).append((typ, canon, vals))

    for name, rt in RULE_TYPES.items():
        if not rt.indexed:
            continue                      # compound(토크나이저) · unit(scan) 은 구간 매칭 대상이 아니다
        section = rules.get(name) or {}
        for term, raw in section.items():
            if str(term).startswith("_"):
                continue                  # _comment_* 같은 설명 키는 규칙이 아니다
            if rt.value == "str":
                canon = str(raw if not isinstance(raw, list) else (raw or [""])[0])
                put(term, name, canon, [canon])
                continue
            if rt.value == "cond":
                put(term, name, term, raw if isinstance(raw, list) else [raw])
                continue
            vals = _as_list(raw)
            put(term, name, term, vals)
            sym = rt.symmetric or (name == "related" and related_symmetric)
            if sym:
                for v in vals:
                    if _norm(v) == _norm(term):
                        continue
                    # 값에서 거꾸로 걸릴 때 무엇을 들여올지.
                    #   siblings = 키 + 형제 값들 (동치 묶음이므로 서로 통한다 — acronym/synonym)
                    #   key      = 키만 (상하·연관은 형제끼리 같은 뜻이 아니다 — hypernym/related)
                    back = [term] + [x for x in vals if x != v] if rt.back == "siblings" else [term]
                    put(v, name, term, back)
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
      fts_query   : FTS 용 확장 질의 (원 질의 + acronym/synonym/context/unit OR 항, alias 치환)
      alt_queries : [(text, weight, kind, canonical)] 벡터/FTS 추가 리스트 (치환 질의)
      related     : [(text, weight, canonical)] 보조 리스트 (별도 융합 — related·hypernym)
      exclude     : [용어] NOT/페널티 대상
      seeds       : 그래프 시드에 추가할 canonical 이름
      fired       : 발화한 규칙 목록 (프로파일; round = 몇 번째 접기에서 나왔는지)

    유형별 동작은 `RULE_TYPES` 레지스트리가 쥐고 있다 — 이 함수는 **순서와 중복 방지**만 담당한다.

    **여러 번 접어 적용한다(fixed point, 상한 `query_rules_max_rounds`).** 1회만 적용하면
    `TAT → Turn Around Time`(acronym) 뒤에 `Turn Around Time → 응답시간`(synonym) 이 걸려 있어도
    두 번째 규칙이 조용히 무시된다 — 사용자가 실제로 겪은 문제다 (2026-09-16).
    """
    rules = load_rules()
    idx = _CACHE["index"] or {}
    fired: List[Dict[str, Any]] = []
    acc = Acc(query, syn_w, related_w, acronym_phrase)
    done: set = set()            # (유형, 대표어) — 같은 규칙을 두 번 적용하지 않는다

    def apply(txt: str, typ: str, canon: str, vals: Any, rnd: int) -> List[str]:
        """규칙 하나를 반영하고, 이 규칙이 **새로 들여온 말** 을 돌려준다(다음 라운드의 입력).

        유형별 동작은 레지스트리의 `apply` 가 한다 — 여기는 중복 방지·발화 기록만 본다.
        """
        rt = RULE_TYPES.get(typ)
        if rt is None or rt.apply is None:
            return []
        # 같은 (유형, 대표어) 는 한 번만. 사전은 양방향이라 'TAT→Turn Around Time' 과
        # 'Turn Around Time→TAT' 이 같은 OR 묶음을 두 번 만든다.
        key = (typ, _norm(canon))
        if key in done:
            return []
        in_query = txt.lower() in query.lower()      # 2라운드 이후의 말은 원 질의에 없다 → 치환 질의를 만들지 않는다
        out = rt.apply(acc, txt, canon, vals, rnd, in_query)
        if out is None:
            return []                 # 발화하지 않음 (예: context 의 조건 불일치) — `done` 에도 넣지 않는다
        done.add(key)
        # 발화 기록의 `values` 는 **실제로 들여온 말**이어야 한다. 조건형(context)의 원본 값은
        # 조건 객체라서 그대로 보여 주면 화면·로그가 읽히지 않고, 어느 조건이 맞았는지도 알 수 없다.
        if rt.value == "cond":
            shown = list(out)[:6]
        elif isinstance(vals, list):
            shown = list(vals)[:6]
        else:
            shown = [str(vals)]
        fired.append({"type": typ, "matched": txt, "canonical": canon, "values": shown, "round": rnd})
        return out if rt.chains else []

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
    # 사전 용어가 아니라 **패턴**으로 걸리는 유형 (match="scan") — 숫자+단위처럼 키로 적을 수 없는 것들
    for name, rt in RULE_TYPES.items():
        if rt.match == "scan" and rt.scan is not None:
            try:
                fired.extend(rt.scan(acc, rules.get(name) or {}))
            except Exception:
                pass                  # 규칙 하나가 깨져도 질의는 계속된다
    or_groups, alt, related, exclude, seeds, q_alias = (
        acc.or_groups, acc.alt, acc.related, acc.exclude, acc.seeds, acc.q_alias)
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
    """규칙 한 줄 추가. 값 모양은 **유형의 선언**(`RuleType.value`)을 따른다.

      list : 문자열 목록 — 기존 값에 합친다 (지우지 않는다)
      str  : 문자열 하나 — 덮어쓴다 (alias 의 canonical)
      cond : 조건 항목 목록 [{"when": [...], "then": [...]}] — 목록에 덧붙인다 (context)
      map  : 객체 하나 — 병합한다 (unit 의 {"factor":…, "base":…})
    """
    rt = RULE_TYPES.get(typ)
    if rt is None:
        raise KeyError("type must be one of %s" % (TYPES,))
    data = _read_raw()
    sect = data.setdefault(typ, {})
    if rt.value == "str":
        sect[term] = str(values if not isinstance(values, list) else (values or [""])[0])
    elif rt.value == "cond":
        cur = sect.get(term)
        items = values if isinstance(values, list) else [values]
        items = [v for v in items if isinstance(v, dict) and (v.get("then"))]
        if not items:
            raise ValueError("%s 는 [{\"when\": [...], \"then\": [...]}] 모양이어야 합니다 (then 은 필수)" % typ)
        sect[term] = (cur if isinstance(cur, list) else []) + items
    elif rt.value == "map":
        if not isinstance(values, dict):
            raise ValueError("%s 는 객체여야 합니다 (예 {\"factor\": 1024, \"base\": \"byte\"})" % typ)
        cur = sect.get(term)
        sect[term] = dict(cur if isinstance(cur, dict) else {}, **values)
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
            kind = (RULE_TYPES.get(typ).value if RULE_TYPES.get(typ) else "list")
            if kind == "str":
                v = str(vals if not isinstance(vals, list) else (vals or [""])[0])
                if term not in sect:
                    added += 1
                elif sect[term] != v:
                    grown += 1
                if replace or term not in sect:
                    sect[term] = v
                continue
            if kind in ("cond", "map"):
                # 조건 목록·객체는 항목 단위로 합치기가 모호하다 — 새로 넣거나(added) replace 일 때만 덮어쓴다.
                if term not in sect:
                    sect[term] = vals
                    added += 1
                elif replace and sect[term] != vals:
                    sect[term] = vals
                    grown += 1
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

    # 유형 안의 중복 · 자기참조 · 빈 값 · 값 모양 (모양 검사는 유형 선언을 따른다)
    for typ in TYPES:
        rt = RULE_TYPES[typ]
        seen: Dict[str, str] = {}
        for term, vals in (rules.get(typ) or {}).items():
            n = _norm(term)
            if n in seen and seen[n] != term:
                add("error", "duplicate", term, "같은 유형 '%s' 에 '%s' 와 사실상 같은 항목 (대소문자·공백만 다름)" % (typ, seen[n]))
            seen[n] = term
            if rt.value == "cond":
                items = vals if isinstance(vals, list) else []
                if not items:
                    add("error", "empty", term, "유형 '%s' 는 [{\"when\": [...], \"then\": [...]}] 목록이어야 합니다" % typ)
                for c in items:
                    if not isinstance(c, dict) or not (c.get("then") or []):
                        add("error", "shape", term, "유형 '%s' 의 항목에 `then` 이 없습니다 — 무엇으로 넓힐지 적어야 합니다" % typ)
                    elif not (c.get("when") or []):
                        add("warn", "always_on", term, "`when` 이 비어 있어 **항상** 발화합니다 — 그럴 거면 synonym 이 맞습니다")
                continue
            if rt.value == "map":
                if not isinstance(vals, dict):
                    add("error", "shape", term, "유형 '%s' 는 객체여야 합니다 (예 {\"factor\": 1024, \"base\": \"byte\"})" % typ)
                elif not vals.get("factor") and not (vals.get("aliases") or []):
                    add("warn", "noop", term, "`factor` 도 `aliases` 도 없어 아무 것도 넓히지 않습니다")
                continue
            lst = vals if isinstance(vals, list) else [vals]
            lst = [str(v) for v in lst]
            if not [v for v in lst if str(v).strip()]:
                add("error", "empty", term, "유형 '%s' 의 값이 비어 있습니다" % typ)
            if any(_norm(v) == n for v in lst):
                add("warn", "self_ref", term, "유형 '%s' 의 값이 자기 자신을 포함합니다" % typ)

    # 같은 말을 acronym 과 context 에 **둘 다** 넣으면 문맥 가르기가 무의미해진다 (acronym 이 항상 넓힌다)
    ctx_terms = {_norm(k) for k in (rules.get("context") or {})}
    for t in ("acronym", "synonym"):
        for term in (rules.get(t) or {}):
            if _norm(term) in ctx_terms:
                add("warn", "context_shadowed", term,
                    "'%s' 에도 있고 context 에도 있습니다 — %s 가 문맥과 무관하게 항상 넓히므로 문맥 가르기가 무의미해집니다. 한쪽을 지우세요" % (t, t))

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
                    rt = RULE_TYPES.get(typ)
                    # 연쇄하지 않는 유형(related·exclude·hypernym)과 치환(alias)은 사슬이 아니다.
                    # 값이 문자열 목록이 아닌 유형(context 의 조건 객체)도 여기서는 따라가지 않는다 —
                    # 예전에는 그대로 frontier 에 넣어 `dict.fromkeys` 가 터졌다 (2026-09-19).
                    if rt is None or not rt.chains or rt.value != "list" or typ == "alias":
                        continue
                    key = (typ, _norm(canon))
                    if key in applied:
                        continue
                    applied.add(key)
                    grew = True
                    nxt.extend([canon] + [str(v) for v in vals])
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


def explain(term: str) -> Dict[str, Any]:
    """이 말이 어느 유형·어느 방향으로 무엇을 끌어오는지 (요청 4).

    entries       : 색인에서 이 말로 발화하는 규칙 전부 [{type, direction, canonical, values, how, reverse}]
                    (reverse=True 는 값 쪽에서 거꾸로 걸린 항목 — acronym/synonym 의 양방향, related_symmetric 의 related)
    expands_to    : 이 말이 질의에 있을 때 **들여오는 말** (유형별)
    expanded_from : 다른 말이 질의에 있을 때 **이 말을 들여오는 규칙** — 즉 이 말이 값(value) 으로 적힌 곳.
                    alias/related(일방) 은 여기에만 나오고 entries 에는 없다 → "왜 반대로는 안 넓혀지나" 의 답.
    """
    rules = load_rules()
    idx = _CACHE["index"] or {}
    sym = bool(_CACHE.get("sym"))
    n = _norm(term)
    entries: List[Dict[str, Any]] = []
    expands_to: List[Dict[str, Any]] = []
    #: 유형별 "어디로 들어가나" 한 낱말 (explain·화면이 그대로 보여 준다)
    as_of = {"exclude": "NOT", "alias": "치환", "related": "보조 리스트", "hypernym": "보조 리스트",
             "context": "조건부 OR", "unit": "OR (환산값)"}
    for typ, canon, vals in idx.get(n) or []:
        rt = RULE_TYPES.get(typ)
        reverse = _norm(canon) != n and bool(rt and (rt.symmetric or typ == "related"))
        direction = DIRECTION.get(typ, "일방")
        if typ == "related" and sym:
            direction = "양방향 (related_symmetric)"
        if typ == "hypernym":
            direction = "하위어로 내려감" if _norm(canon) == n else "상위어로 올라감"
        # cond 값(context)은 조건 자체를 보여 준다 — 목록으로 뭉개면 "언제 발화하나" 가 사라진다
        shown = ([{"when": c.get("when") or [], "then": c.get("then") or [], "note": c.get("note") or ""}
                  for c in vals if isinstance(c, dict)] if (rt and rt.value == "cond") else list(vals))
        entries.append({"type": typ, "direction": direction, "canonical": canon, "values": shown,
                        "how": HOW.get(typ, ""), "reverse": reverse, "label": (rt.label if rt else typ)})
        flat = [t for c in shown for t in (c.get("then") or [])] if (rt and rt.value == "cond") else list(shown)
        expands_to.append({"type": typ, "terms": ([canon] if typ == "alias" else flat), "as": as_of.get(typ, "OR")})
    # 색인에 없는 유형(compound·unit)은 사전에서 직접 찾아 보여 준다 — 화면에서 "왜 안 보이지" 가 되지 않게
    for typ in [t for t, r in RULE_TYPES.items() if not r.indexed]:
        for k, raw in (rules.get(typ) or {}).items():
            if _norm(k) != n:
                continue
            rt = RULE_TYPES[typ]
            shown = raw if isinstance(raw, dict) else [str(x) for x in (raw if isinstance(raw, list) else [raw])]
            entries.append({"type": typ, "direction": DIRECTION[typ], "canonical": k, "values": shown,
                            "how": HOW[typ], "reverse": False, "label": rt.label})
    # 이 말이 값으로 적힌 곳 — 어떤 말이 질의에 있으면 이 말이 딸려 오는가
    expanded_from: List[Dict[str, Any]] = []
    for typ in [t for t, r in RULE_TYPES.items() if r.indexed and r.value == "list"] + ["alias"]:
        for key, vals in (rules.get(typ) or {}).items():
            lst = [str(v) for v in (vals if isinstance(vals, list) else [vals])]
            if not any(_norm(v) == n for v in lst) or _norm(key) == n:
                continue
            if typ == "alias":
                note = "'%s' 가 질의에 있으면 '%s' 로 치환된다. 반대('%s' 가 있을 때 '%s' 로 넓히기)는 하지 않는다 — 표기 정규화가 목적" % (key, term, term, key)
                pulls = True
            elif typ == "related":
                pulls = sym
                note = ("related_symmetric=true 이므로 '%s' 가 질의에 있으면 '%s' 도 보조 리스트에 들어간다" % (term, key) if sym
                        else "'%s' 가 질의에 있을 때만 '%s' 를 보조 리스트에 넣는다. 반대는 하지 않는다 (related_symmetric=false)" % (key, term))
            elif typ == "exclude":
                pulls = False
                note = "'%s' 가 질의에 있으면 '%s' 를 NOT 으로 뺀다" % (key, term)
            elif typ == "hypernym":
                pulls = True
                note = "'%s' 는 '%s' 의 상위어다 — '%s' 가 질의에 있으면 '%s' 가 보조 리스트로 내려오고(hypernym_down_w), " \
                       "'%s' 가 질의에 있으면 '%s' 가 더 낮은 가중으로 올라온다(hypernym_up_w)" % (key, term, key, term, term, key)
            else:
                pulls = True
                note = "양방향 — '%s' 가 질의에 있어도 '%s' 로 넓힌다 (entries 에도 있다)" % (term, key)
            expanded_from.append({"type": typ, "direction": DIRECTION[typ] if not (typ == "related" and sym) else "양방향 (related_symmetric)",
                                  "key": key, "values": lst, "reverse_applies": pulls, "note": note})
    # context 는 값이 조건 객체라 위 루프에 안 걸린다 — 따로 찾아서 "언제 이 말이 딸려 오나" 를 보여 준다
    for key, conds in (rules.get("context") or {}).items():
        for c in (conds if isinstance(conds, list) else []):
            if not isinstance(c, dict) or not any(_norm(t) == n for t in (c.get("then") or [])):
                continue
            expanded_from.append({"type": "context", "direction": "문맥", "key": key,
                                  "values": [str(t) for t in (c.get("then") or [])], "reverse_applies": False,
                                  "when": [str(w) for w in (c.get("when") or [])],
                                  "note": "'%s' 가 질의에 있고 %s 중 하나가 함께 있을 때만 '%s' 를 넣는다%s"
                                          % (key, (c.get("when") or ["(조건 없음)"]), term,
                                             (" — " + str(c.get("note"))) if c.get("note") else "")})
    return {"term": term, "normalized": n, "found": bool(entries or expanded_from), "entries": entries,
            "expands_to": expands_to, "expanded_from": expanded_from,
            "related_symmetric": sym, "directions": dict(DIRECTION), "how": dict(HOW),
            "types": [rt.describe() for rt in RULE_TYPES.values()],
            "note": "acronym/synonym 은 양방향(동치), alias 는 일방 치환(정규 표기를 지키기 위해 일부러), related 는 일방 보조 리스트"
                    "(정밀도 보호; 양쪽으로 쓰려면 tuning related_symmetric=true), exclude 는 일방 NOT. "
                    "뜻이 문맥에 따라 갈리면 context, 분류 체계는 hypernym, 숫자+단위는 unit 을 쓴다.",
            "path": rules_path()}


def describe_types() -> List[Dict[str, Any]]:
    """규칙 유형 전체 표 (CLI `rules types` · Web 규칙 화면 · MCP `wiki_rules(action=types)` 가 같은 표를 쓴다)."""
    r = load_rules()
    return [dict(rt.describe(), count=len(r.get(name) or {})) for name, rt in RULE_TYPES.items()]
