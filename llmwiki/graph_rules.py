# -*- coding: utf-8 -*-
"""규칙 기반(파이썬) 엔티티/관계 추출기.

설계 원칙
- 비용 0, 결정적(deterministic), 코퍼스 도메인(반도체/경영회의/일정/논문)에 맞춘 사전 + 정규식.
- 사전(alias 사전)은 data/rules.json 으로 외부화되어 self-evolving 단계에서 별칭 추가/수정이 가능하다.
- 출력은 Entity / Relation 후보이며, LLM 추출 결과와 graph_build 에서 병합된다.

관계 규칙 (예)
  '**담당**: CFO실'        -> (decision) -[owner]-> (CFO실)
  '**마감**: 2026.05.22'   -> (decision) -[deadline]-> (date)
  '## 참석\n대표이사, CFO' -> (meeting/doc) -[attendee]-> (role)
  '- **미래에셋 (5/15)**: "…"' -> (미래에셋) -[comments_on]-> (doc topic)
  같은 청크 내 공동 출현       -> co_occurs (weight = 1/거리)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .textutil import sha1

RULES_PATH_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "rules.json")


def rules_path() -> str:
    """data/rules.json 위치 (LLMWIKI_RULES_PATH 로 변경 가능)."""
    try:
        from .config import path_for
        return path_for("rules")
    except Exception:
        return RULES_PATH_DEFAULT

DEFAULT_RULES: Dict[str, object] = {
    # 사전 매칭 방식 (2026-09-24). 예전에는 모든 별칭을 **경계 없이 · 대소문자 무시**로 찾아서 "f-ir-st" 의 IR, "dire-cto-r" 의 CTO,
    # "a-bb-reviation" 의 BB 가 잡혔고 그 오탐이 실데이터 허브 1~5위를 차지했다 (degree 7,748 / 2,244 / …).
    #   ascii_word_boundary  : ASCII 별칭은 앞뒤가 영숫자가 아니어야 매칭. 한글 별칭은 조사가 붙으므로(베이스밴드는) 적용하지 않는다.
    #   case_sensitive_max_len: 이 길이 이하의 ASCII 별칭(IR·BB·CTO)은 대소문자를 구분한다. 0 = 모두 무시(예전 동작).
    # 엔티티마다 "match": {"whole_word": bool, "case_sensitive": bool} 로 덮어쓸 수 있다.
    "matching": {"ascii_word_boundary": True, "case_sensitive_max_len": 3},
    "entities": {
        # canonical name: {type, aliases}
        "NVIDIA": {"type": "org", "aliases": ["엔비디아", "Nvidia", "NVIDIA Data Center"]},
        "SK하이닉스": {"type": "org", "aliases": ["SK hynix", "하이닉스", "SK 하이닉스"]},
        "TSMC": {"type": "org", "aliases": ["티에스엠씨"]},
        "Micron": {"type": "org", "aliases": ["마이크론"]},
        "AMD": {"type": "org", "aliases": []},
        "Google": {"type": "org", "aliases": ["구글", "Google TPU"]},
        "Samsung": {"type": "org", "aliases": ["삼성", "삼성전자"]},
        "S-Foundry": {"type": "org", "aliases": ["당사", "S Foundry"]},
        "미래에셋": {"type": "org", "aliases": ["미래에셋증권", "미래에셋 리서치"]},
        "한투": {"type": "org", "aliases": ["한국투자증권"]},
        "JPM": {"type": "org", "aliases": ["JP모건", "JPMorgan"]},
        "PwC": {"type": "org", "aliases": []},
        "BIS": {"type": "org", "aliases": ["미 상무부", "상무부"]},
        "Reuters": {"type": "org", "aliases": ["로이터"]},
        "Bloomberg": {"type": "org", "aliases": ["블룸버그"]},
        "ASML": {"type": "org", "aliases": []},
        "IBM": {"type": "org", "aliases": []},
        "Intel": {"type": "org", "aliases": ["인텔"]},
        "대표이사": {"type": "role", "aliases": ["대표이사실"]},
        "CEO": {"type": "role", "aliases": []},
        "CFO": {"type": "role", "aliases": ["CFO실", "재무팀장", "재무팀"]},
        "COO": {"type": "role", "aliases": ["COO실"]},
        "CTO": {"type": "role", "aliases": ["CTO실"]},
        "CHRO": {"type": "role", "aliases": []},
        "IR본부": {"type": "org_unit", "aliases": ["IR본부장", "IR팀", "IR팀장", "IR"]},
        "R&D본부": {"type": "org_unit", "aliases": ["R&D본부장", "R&D", "R&D 본부"]},
        "양산기획팀": {"type": "org_unit", "aliases": ["양산기획팀장", "양산기획"]},
        "사외이사": {"type": "role", "aliases": ["사외이사 김", "사외이사 박", "사외이사 이", "사외이사 정", "사외이사 김ㅇㅇ", "사외이사 박ㅇㅇ", "사외이사 이ㅇㅇ", "사외이사 정ㅇㅇ"]},
        "리스크위원회": {"type": "meeting", "aliases": ["분기 리스크위원회", "리스크 위원회"]},
        "임원회의": {"type": "meeting", "aliases": ["임원 회의"]},
        "글로벌 발표": {"type": "event", "aliases": ["7월 글로벌 발표", "글로벌 IT 컨퍼런스", "7/18 글로벌 발표", "7/16 글로벌 발표"]},
        "IR 리허설": {"type": "event", "aliases": ["IR리허설", "글로벌 IR 리허설"]},
        "HBM4": {"type": "product", "aliases": ["HBM4 12-Hi", "HBM4 양산"]},
        "HBM3E": {"type": "product", "aliases": ["HBM3E 12-Hi"]},
        "HBM": {"type": "product", "aliases": ["고대역폭메모리"]},
        "12-Hi": {"type": "tech", "aliases": ["12Hi", "12단"]},
        "Blackwell B300": {"type": "product", "aliases": ["B300", "Blackwell"]},
        "캐파 확장": {"type": "topic", "aliases": ["캐파 확장 투자", "캐파확장", "HBM4 캐파 확장"]},
        "ROI 회수기간": {"type": "topic", "aliases": ["ROI 회수기간 가이드", "ROI 4년", "회수기간"]},
        "수출통제": {"type": "topic", "aliases": ["對中 수출통제", "수출 통제", "대중 수출통제"]},
        "수율": {"type": "topic", "aliases": ["양산 수율"]},
        "가이던스": {"type": "topic", "aliases": ["매출 가이던스", "수요 가이던스"]},
        "GAA": {"type": "tech", "aliases": ["Gate-All-Around", "나노시트", "3GAE"]},
        "FinFET": {"type": "tech", "aliases": []},
        "EUV": {"type": "tech", "aliases": ["High-NA EUV", "High-NA", "극자외선"]},
        "HfO2": {"type": "material", "aliases": ["hafnium oxide", "high-κ HfO2"]},
        "MoS2": {"type": "material", "aliases": ["2D MoS2"]},
        "EOT": {"type": "metric", "aliases": ["equivalent oxide thickness"]},
        "UCIe": {"type": "tech", "aliases": ["chiplet", "칩렛"]},
        "technological sovereignty": {"type": "concept", "aliases": ["기술 주권", "technology sovereignty"]},
    },
    # 관계 패턴. `value` 가 잡은 문자열을 **무엇으로 읽을지**, `in_decision`/`in_chunk` 가 **어디서 적용할지**를 정한다.
    #   value: entity(사전 엔티티 찾기) | date | money | percent | text(잡은 문자열 자체를 노드로, node_type 사용) | id(문서 ID 패턴)
    #   in_decision: '### D1.' 결정 블록 안에서 (출발 노드 = 그 결정)   in_chunk: 청크 전체에서 (출발 노드 = 문서)
    # 이 세 가지가 파일에 있으므로 **코드를 고치지 않고** 새 패턴을 추가할 수 있다
    # (예전에는 rel 이름이 owner/deadline/amount/attendee/source 다섯 중 하나일 때만 동작했다 — 2026-09-16).
    "relation_patterns": [
        {"name": "owner", "regex": r"\*\*담당\*\*\s*[:：]\s*([^\n]+)", "value": "entity",
         "in_decision": {"rel": "owner", "weight": 1.0, "confidence": 0.9, "desc": "담당: "},
         "in_chunk": {"rel": "responsible", "weight": 0.8, "confidence": 0.7, "desc": "담당: "}},
        {"name": "deadline", "regex": r"\*\*(?:마감|적용|일정)\*\*\s*[:：]\s*([^\n]+)", "value": "date",
         "in_decision": {"rel": "deadline", "weight": 1.0, "confidence": 0.85, "desc": "마감/적용: "}},
        {"name": "amount", "regex": r"\*\*금액\*\*\s*[:：]\s*([^\n]+)", "value": "money",
         "in_decision": {"rel": "amount", "weight": 1.0, "confidence": 0.9, "desc": "금액: "}},
        {"name": "attendee", "regex": r"##\s*참석(?:\s*예정)?\s*\n([^\n]+)", "value": "entity",
         "in_chunk": {"rel": "attendee", "weight": 1.0, "confidence": 0.9, "desc": "참석: "}},
        {"name": "source", "regex": r"출처\s*[:：]\s*([^\n]+)", "value": "entity",
         "in_chunk": {"rel": "source", "weight": 0.7, "confidence": 0.8, "desc": "출처: "}},
        # ---- 모뎀/임베디드 (조직에 맞게 고치거나 지우세요) ----
        {"name": "affected_module", "regex": r"\*\*(?:영향\s*모듈|모듈)\*\*\s*[:：]\s*([^\n]+)", "value": "text", "node_type": "module",
         "in_chunk": {"rel": "affects_module", "weight": 0.8, "confidence": 0.8, "desc": "영향 모듈: "}, "split": ","},
        {"name": "register", "regex": r"\*\*(?:레지스터|register)\*\*\s*[:：]\s*([^\n]+)", "value": "text", "node_type": "register",
         "in_chunk": {"rel": "touches_register", "weight": 0.8, "confidence": 0.8, "desc": "레지스터: "}, "split": ","},
        {"name": "hw_block", "regex": r"\*\*(?:HW\s*블록|hw_block)\*\*\s*[:：]\s*([^\n]+)", "value": "text", "node_type": "hw_block",
         "in_chunk": {"rel": "in_block", "weight": 0.7, "confidence": 0.8, "desc": "HW 블록: "}, "split": ","},
        {"name": "root_cause_of", "regex": r"\*\*(?:근본\s*원인|root\s*cause)\*\*\s*[:：]\s*([^\n]+)", "value": "id",
         "in_chunk": {"rel": "root_cause_of", "weight": 1.0, "confidence": 0.9, "desc": "근본 원인: "}},
    ],
    "analyst_pattern": r"\*\*([가-힣A-Za-z]+)\s*\((\d{1,2}/\d{1,2})\)\*\*\s*[:：]\s*[\"“]([^\"”]+)[\"”]",
    "decision_pattern": r"###\s*(D\d)\.\s*([^\n]+)",
    "date_patterns": [
        r"\d{4}[.\-/년]\s?\d{1,2}[.\-/월]\s?\d{1,2}일?",
        r"\d{4}년\s?\d{1,2}월",
        r"(?<![\d/])\d{1,2}/\d{1,2}(?![\d/])",
    ],
    "money_pattern": r"[\d,]+(?:\.\d+)?\s?(?:억\s?원|억원|억|조\s?원|조|만\s?원|달러|USD|\$)",
    "percent_pattern": r"[+\-−]?\d+(?:\.\d+)?\s?(?:%|%p|pp)",
    # 수치+단위 (value: "measure") — 사양값이 그래프에 남게 한다. 단위를 늘리려면 이 정규식만 고치면 된다.
    "measure_pattern": r"[+\-−]?\d+(?:\.\d+)?\s?(?:[nuµmkKMG]?(?:s|Hz|V|A|W|B|bps)|dB|dBm|ppm|°C|℃|bit|byte|cycle)\b",
    # 리비전/버전 (value: "version")
    "version_pattern": r"\b(?:[Rr]ev\.?\s?[A-Z]\d?|v\d+(?:\.\d+){0,2})\b",
    # ---- 청크마다 남길 스칼라 값 (2026-09-19) ----
    # 예전에는 '날짜와 금액은 청크마다 노드로 남긴다' 가 코드에 박혀 있었고(관계명 mentions_date/mentions_amount 포함),
    # percent 는 relation_patterns 안에서만 쓰여 청크 단위로는 남지 않았다. 이제 이 절에 한 줄을 더하면 된다.
    # per_chunk = 청크당 최대 개수 (0 이면 끄기). 빈 절/없는 절이면 아래 기본값을 쓴다.
    "chunk_values": [
        {"name": "date", "value": "date", "rel": "mentions_date", "per_chunk": 8, "weight": 0.3, "confidence": 0.6},
        {"name": "amount", "value": "money", "rel": "mentions_amount", "per_chunk": 6, "weight": 0.3, "confidence": 0.6},
        {"name": "measure", "value": "measure", "rel": "mentions_measure", "per_chunk": 8, "weight": 0.4, "confidence": 0.7},
        {"name": "version", "value": "version", "rel": "mentions_version", "per_chunk": 4, "weight": 0.5, "confidence": 0.8},
    ],
    # ---- 스키마: 타입·관계 어휘 (2026-09-19) ----
    # 규칙과 LLM 이 만들어 내는 type/rel 문자열을 여기에 맞춘다. 없으면 on_unknown 정책대로 처리하고
    # **빌드 보고서에 개수와 예시를 남긴다** (예전에는 이상한 type/rel 이 들어와도 아무도 몰랐다).
    "schema": {
        "on_unknown": "keep",       # keep(그대로 두되 보고) | map(별칭이면 고치고 나머지는 보고) | drop(버림)
        # 추출기가 스스로 만드는 노드 type 도 여기에 **선언**해 둔다. types_for_cooccur 와 역할이 다르다 —
        # 저쪽은 "공동출현 관계를 만들 type", 이쪽은 "이 그래프에 존재해도 되는 type" 이다.
        # (date/amount/version 을 cooccur 에 넣으면 허브가 되어 그래프 검색이 모든 문서로 번진다.)
        "entity_types": {
            "document": {"desc": "제목만 있는 문서 노드"}, "decision": {"desc": "### D1. 결정 블록"},
            "date": {"desc": "날짜"}, "amount": {"desc": "금액"}, "percent": {"desc": "비율"},
            "metric": {"desc": "수치+단위 사양값 (value: measure)"}, "version": {"desc": "리비전/버전 (value: version)"},
            "term": {"desc": "value: text 가 만든 일반 용어"},
        },
        "relations": {
            "fixes": {"inverse": "fixed_by", "src": ["cl"], "dst": ["issue"], "desc": "이 CL 이 이 이슈를 고쳤다"},
            "fixed_by": {"inverse": "fixes", "src": ["issue"], "dst": ["cl"]},
            "verifies": {"inverse": "verified_by", "desc": "이 TC 가 이 이슈를 검증한다"},
            "verified_by": {"inverse": "verifies"},
            "related_issue": {"symmetric": True},
            "reports": {"desc": "주간 보고가 이슈를 다룬다"},
            "reviews": {"desc": "주간 보고가 CL 을 리뷰했다"},
            "motivated_by": {"desc": "이 코딩 규칙이 이 이슈 때문에 생겼다"},
            "references": {"desc": "본문에서 언급 (가장 약한 결정적 관계)"},
            "follows": {"desc": "이 코딩 규칙을 따른다"},
            "owner": {"aliases": ["responsible", "owned_by"], "desc": "담당"},
            "deadline": {"desc": "마감/적용일"},
            "amount": {"desc": "금액"},
            "attendee": {"desc": "참석"},
            "source": {"desc": "출처"},
            "decides": {"desc": "문서에 담긴 결정사항"},
            "comments_on": {"desc": "애널리스트 코멘트"},
            "affects_module": {"aliases": ["affects"], "desc": "영향 모듈"},
            "touches_register": {"desc": "건드리는 레지스터"},
            "in_block": {"aliases": ["part_of_block"], "desc": "속한 HW 블록"},
            "root_cause_of": {"desc": "근본 원인"},
            "uses": {"aliases": ["use", "used", "utilizes", "using"], "desc": "사용한다"},
            # 이 저장소의 data/rules.json 이 실제로 쓰는 관계들 (graph-rules lint 가 드리프트를 찾아 채웠다)
            "on_chip": {"desc": "이 칩/리비전에서"},
            "conforms_to": {"inverse": "specified_by", "desc": "이 사양을 따른다"},
            "specified_by": {"inverse": "conforms_to"},
            "violates_spec": {"desc": "사양을 어긴다"},
            "implements": {"inverse": "implemented_by", "desc": "설계/사양을 구현한다"},
            "implemented_by": {"inverse": "implements"},
            "measured_value": {"desc": "측정값"},
            "part_of": {"inverse": "has_part", "desc": "상위-하위"},
            "has_part": {"inverse": "part_of"},
            "mentions": {"desc": "문서가 엔티티를 언급"},
            "mentions_date": {}, "mentions_amount": {}, "mentions_measure": {}, "mentions_version": {},
            "co_occurs": {"symmetric": True, "desc": "같은 청크에 가까이 나옴"},
            "related_to": {"symmetric": True, "desc": "종류를 특정하지 못한 관계 (LLM 기본값)"},
        },
    },
    "types_for_cooccur": ["org", "role", "org_unit", "product", "topic", "tech", "meeting", "event", "material", "concept",
                          "issue", "cl", "tc", "sw_design", "hw_design", "coding_rule", "module", "register", "hw_block", "feature", "component"],
    # ---- 결정적(deterministic) 관계: 문서 ID 패턴 + 링크 규칙 (provenance=rule), front matter related.* (provenance=explicit) ----
    "id_patterns": [
        {"type": "issue", "regex": r"\bISSUE[-_ ]?(\d{3,7})\b", "canonical": "ISSUE-{1}"},
        {"type": "cl", "regex": r"\bCL[-_ ]?(\d{3,8})\b", "canonical": "CL-{1}"},
        {"type": "tc", "regex": r"\bTC-([A-Z0-9_-]{2,})\b", "canonical": "TC-{1}"},
        {"type": "sw_design", "regex": r"\bSWD-([A-Z0-9_-]{2,})\b", "canonical": "SWD-{1}"},
        {"type": "hw_design", "regex": r"\bHWD-([A-Z0-9_-]{2,})\b", "canonical": "HWD-{1}"},
        {"type": "coding_rule", "regex": r"\bRULE-([A-Z0-9_-]{2,})\b", "canonical": "RULE-{1}"},
        {"type": "weekly_report", "regex": r"\bWR-(\d{4}-W\d{2})\b", "canonical": "WR-{1}"},
    ],
    "link_rules": [
        {"when_doc_type": "cl", "target_type": "issue", "rel": "fixes", "weight": 1.0, "confidence": 0.95},
        {"when_doc_type": "issue", "target_type": "cl", "rel": "fixed_by", "weight": 1.0, "confidence": 0.9},
        {"when_doc_type": "issue", "target_type": "issue", "rel": "related_issue", "weight": 0.7, "confidence": 0.7},
        {"when_doc_type": "issue", "target_type": "tc", "rel": "verified_by", "weight": 0.8, "confidence": 0.8},
        {"when_doc_type": "tc_list", "target_type": "issue", "rel": "verifies", "weight": 0.8, "confidence": 0.8},
        {"when_doc_type": "weekly_report", "target_type": "issue", "rel": "reports", "weight": 0.6, "confidence": 0.85},
        {"when_doc_type": "weekly_report", "target_type": "cl", "rel": "reviews", "weight": 0.6, "confidence": 0.85},
        {"when_doc_type": "coding_rule", "target_type": "issue", "rel": "motivated_by", "weight": 0.6, "confidence": 0.7},
        {"when_doc_type": "*", "target_type": "*", "rel": "references", "weight": 0.5, "confidence": 0.7},
    ],
    "explicit_rels": {
        "*": {"issues": "references", "cls": "references", "docs": "references", "tcs": "verified_by", "rules": "follows"},
        "cl": {"issues": "fixes"},
        "issue": {"cls": "fixed_by", "issues": "related_issue"},
        "tc_list": {"issues": "verifies"},
    },
    "related_key_type": {"issues": "issue", "cls": "cl", "tcs": "tc", "docs": "document", "rules": "coding_rule"},
}


# ------------------------------------------------------------------ 값 종류 레지스트리 (2026-09-19)
#: `relation_patterns[*].value` 와 `chunk_values[*].value` 가 고르는 **잡은 문자열을 무엇으로 읽을지**.
#:
#: 왜 레지스트리인가: 예전에는 `_apply_rel_patterns()` 안의 `if value == "entity": … elif value == "date": …`
#: 사슬이었다. 그래서 "규칙을 data/rules.json 으로 외부화했다" 가 절반만 사실이었다 — 새 값 종류(예: `4 ns`,
#: `rev B1`)를 잡으려면 파이썬을 고쳐야 했다. 질의 규칙(query_rules.RULE_TYPES)과 같은 등록형으로 바꿔,
#: 종류를 늘릴 때 여기 한 줄을 등록하면 파일·화면·설명이 따라오게 한다.
class ValueType:
    """`name` 종류의 값을 노드로 바꾸는 법.

    resolve(ex, val, pat, add) → 대상 entity_id 목록. `add(name, type, desc, conf)` 는 호출자가 주는
    '노드를 만들고 id 를 돌려주는' 함수다(청크 지역 사전에 등록된다).
    """
    __slots__ = ("name", "label", "desc", "resolve", "node_type")

    def __init__(self, name: str, label: str, desc: str, resolve, node_type: str = ""):
        self.name, self.label, self.desc, self.resolve, self.node_type = name, label, desc, resolve, node_type


VALUE_TYPES: Dict[str, ValueType] = {}


def register_value_type(vt: ValueType) -> ValueType:
    VALUE_TYPES[vt.name] = vt
    return vt


def _v_entity(ex, val, pat, add):
    """잡은 문자열 안의 사전 엔티티. 노드로 **등록은 하되 멘션은 세지 않는다**(count=False).

    실무상 이 등록은 대개 중복이다 — 잡은 문자열은 청크의 부분이므로, 같은 엔티티를 청크 전체 스캔이
    이미 찾아 등록해 둔다. 그래도 등록하는 이유는 **끝점이 반드시 존재한다**는 것을 이 함수가 스스로
    보장하기 위해서다. 예전에는 여기서 id 만 돌려줬고, graph_build 가 그 구멍을 '관계 이름 화이트리스트'
    (owner·attendee·source·responsible·comments_on 은 끝점이 없어도 저장)로 막고 있었다. 그 화이트리스트가
    dangling 관계의 출처였다. 이제 끝점이 보장되므로 저장 조건에서 이름을 볼 필요가 없다 (2026-09-19).
    멘션까지 세면 청크 전체 스캔과 이중 계수가 되므로 등록만 한다.
    """
    ents_info: Dict[str, Dict] = ex.rules.get("entities") or {}      # type: ignore
    out = []
    for canon, _pos in ex.find_entities(val):
        info = ents_info.get(canon) or {}
        out.append(add(canon, str(info.get("type") or "concept"), "", 0.95, info.get("aliases") or [], False))
    return out


def _nc(pat, default: float) -> float:
    """만들 노드의 confidence. `node_conf` 로 덮을 수 있다 —
    청크 스칼라(chunk_values)는 0.7, 관계 패턴 안에서 잡힌 값은 0.9 로 예전 동작을 그대로 지킨다."""
    try:
        return float(pat.get("node_conf", default))
    except (TypeError, ValueError):
        return default


def _v_date(ex, val, pat, add):
    return [add(d, "date", "", _nc(pat, 0.9)) for d in ex._dates(val)]


def _v_money(ex, val, pat, add):
    return [add(a.strip(), "amount", "", _nc(pat, 0.9)) for a in ex._money_re.findall(val)]


def _v_percent(ex, val, pat, add):
    return [add(a.strip(), "percent", "", _nc(pat, 0.9)) for a in ex._pct_re.findall(val)]


def _v_id(ex, val, pat, add):
    return [add(cid, ctype, "", _nc(pat, 0.95)) for cid, ctype, _pos in ex.find_ids(val)]


def _v_text(ex, val, pat, add):
    node_type = str(pat.get("node_type") or "term")
    conf = float(pat.get("confidence", 0.7) or 0.7)
    split = str(pat.get("split") or "")
    parts = [x.strip() for x in val.split(split)] if split else [val]
    return [add(name, node_type, "", conf) for name in [x for x in parts if x][:12]]


def _v_measure(ex, val, pat, add):
    """`4 ns` · `1.5 dB` · `100 MHz` — 수치 + 단위를 하나의 `metric` 노드로 (공백을 지워 표기를 통일).

    왜 필요한가: 이 코퍼스의 실제 질문이 "HW rev B1 에서 t_setup 은 몇 ns 인가?" 다. 예전에는 숫자에 걸리는
    값 종류가 money·percent 뿐이라, 단위가 붙은 사양값은 그래프에 노드로 남지 않았다.
    """
    return [add(re.sub(r"\s+", "", m.group(0)), pat.get("node_type") or "metric", "", _nc(pat, 0.85))
            for m in ex._measure_re.finditer(val)]


def _v_version(ex, val, pat, add):
    """`rev B1` · `v1.2` · `Rev. A2` — 리비전/버전을 `version` 노드로.

    같은 사실이 리비전마다 다른 코퍼스(HW rev A2/B1/B2)에서, 버전이 노드로 있어야 "rev B1 에서" 를 좁힐 수 있다.
    """
    out = []
    for m in ex._version_re.finditer(val):
        name = re.sub(r"\s+", " ", m.group(0)).strip().replace("Rev.", "rev").replace("REV", "rev")
        out.append(add(name, pat.get("node_type") or "version", "", _nc(pat, 0.85)))
    return out


for _vt in (
    ValueType("entity", "사전 엔티티", "잡은 문자열 안에서 entities 사전의 이름/별칭을 찾는다", _v_entity),
    ValueType("date", "날짜", "date_patterns 로 날짜를 뽑아 date 노드로", _v_date, "date"),
    ValueType("money", "금액", "money_pattern 으로 금액을 뽑아 amount 노드로", _v_money, "amount"),
    ValueType("percent", "퍼센트", "percent_pattern 으로 비율을 뽑아 percent 노드로", _v_percent, "percent"),
    ValueType("id", "문서 ID", "id_patterns 로 ISSUE-…/CL-… 같은 ID 를 뽑아 그 type 노드로", _v_id),
    ValueType("text", "문자열 그대로", "잡은 문자열 자체가 노드 (node_type 지정 · split 으로 나눌 수 있다)", _v_text, "term"),
    ValueType("measure", "수치+단위", "`4 ns` `1.5 dB` `100 MHz` 를 metric 노드로 (measure_pattern)", _v_measure, "metric"),
    ValueType("version", "리비전/버전", "`rev B1` `v1.2` 를 version 노드로 (version_pattern)", _v_version, "version"),
):
    register_value_type(_vt)


def describe_value_types() -> List[Dict[str, str]]:
    """화면·CLI·MCP 가 같은 설명을 쓰도록 (query_rules.describe_types 와 같은 역할)."""
    return [{"name": v.name, "label": v.label, "desc": v.desc, "node_type": v.node_type} for v in VALUE_TYPES.values()]


@dataclass
class RuleEntity:
    entity_id: str
    name: str
    type: str
    aliases: List[str] = field(default_factory=list)
    description: str = ""
    confidence: float = 0.9


@dataclass
class RuleRelation:
    src: str
    dst: str
    rel: str
    description: str
    weight: float
    confidence: float = 0.8
    provenance: str = "rule"      # explicit | rule | cooccur


def canonical_id(pattern: Dict[str, object], m: "re.Match") -> str:
    tpl = str(pattern.get("canonical") or "{0}")
    out = tpl
    for i in range(0, (m.lastindex or 0) + 1):
        out = out.replace("{%d}" % i, (m.group(i) or "").upper())
    return out.upper()


def entity_id_for(name: str) -> str:
    return "e:" + re.sub(r"\s+", "_", name.strip().lower())


# 예전 형식(`{"name","regex","rel"}` 만 있는 파일)의 기본 동작. 새 파일은 value/in_decision/in_chunk 를 직접 적는다.
_LEGACY_REL_DEFAULTS: Dict[str, Dict[str, object]] = {
    "owner": {"value": "entity", "in_decision": {"rel": "owner", "weight": 1.0, "confidence": 0.9, "desc": "담당: "},
              "in_chunk": {"rel": "responsible", "weight": 0.8, "confidence": 0.7, "desc": "담당: "}},
    "deadline": {"value": "date", "in_decision": {"rel": "deadline", "weight": 1.0, "confidence": 0.85, "desc": "마감/적용: "}},
    "amount": {"value": "money", "in_decision": {"rel": "amount", "weight": 1.0, "confidence": 0.9, "desc": "금액: "}},
    "attendee": {"value": "entity", "in_chunk": {"rel": "attendee", "weight": 1.0, "confidence": 0.9, "desc": "참석: "}},
    "source": {"value": "entity", "in_chunk": {"rel": "source", "weight": 0.7, "confidence": 0.8, "desc": "출처: "}},
}


def _norm_rel_pattern(p: Dict[str, object]) -> Dict[str, object]:
    """관계 패턴 한 줄을 정규화한다 — 없으면 예전 동작으로 채우고, 그래도 없으면 '청크 전체에서 엔티티 연결'."""
    if "in_decision" not in p and "in_chunk" not in p:
        legacy = _LEGACY_REL_DEFAULTS.get(str(p.get("rel") or p.get("name") or ""))
        if legacy:
            p = dict(legacy, **{k: v for k, v in p.items() if k in ("name", "regex")})
        else:
            p.setdefault("value", "entity")
            p["in_chunk"] = {"rel": str(p.get("rel") or p.get("name") or "references"),
                             "weight": float(p.get("weight", 0.7) or 0.7), "confidence": float(p.get("confidence", 0.7) or 0.7),
                             "desc": str(p.get("desc") or ((p.get("name") or "") and str(p.get("name")) + ": "))}
    p.setdefault("value", "entity")
    p.setdefault("node_type", str(p.get("name") or "term"))
    return p


def _norm_chunk_values(rules: Dict[str, object], dates_per_chunk: int, amounts_per_chunk: int) -> List[Dict[str, object]]:
    """`chunk_values` 절을 정규화한다. 절이 없는 **예전 파일**이면 예전 동작(날짜·금액만)을 그대로 만든다.

    `dates_per_chunk`/`amounts_per_chunk` 는 오래전부터 있던 튜닝 값이라 계속 존중한다 —
    파일에 `per_chunk` 를 적으면 그 값이 이긴다(파일이 더 구체적인 선언이므로).
    """
    raw = rules.get("chunk_values")
    if raw is None:
        return [{"name": "date", "value": "date", "rel": "mentions_date", "per_chunk": dates_per_chunk,
                 "weight": 0.3, "confidence": 0.6, "node_conf": 0.7},
                {"name": "amount", "value": "money", "rel": "mentions_amount", "per_chunk": amounts_per_chunk,
                 "weight": 0.3, "confidence": 0.6, "node_conf": 0.7}]
    out: List[Dict[str, object]] = []
    legacy_cap = {"date": dates_per_chunk, "amount": amounts_per_chunk, "money": amounts_per_chunk}
    for cv in raw:                                   # type: ignore
        if not isinstance(cv, dict) or not cv.get("value"):
            continue
        d = dict(cv)
        d.setdefault("per_chunk", legacy_cap.get(str(d.get("name") or d.get("value")), 8))
        d.setdefault("node_conf", 0.7)               # 청크 스칼라 노드는 약한 근거 (예전 값 유지)
        out.append(d)
    return out


class Schema:
    """타입·관계 어휘 (`rules["schema"]`). 없으면 '아무것도 막지 않는' 빈 스키마가 된다.

    왜 있나 (2026-09-19): 규칙과 LLM 이 만든 `type`/`rel` 이 아무 검증 없이 그대로 저장돼 왔다. 그래서
    같은 뜻의 관계가 `uses`/`used`/`utilizes` 로 갈라지고, 엔티티 type 에 `relation` 같은 값이 들어왔다.
    스키마는 **표준 이름으로 모으고(aliases), 모르는 것은 정책대로 처리하며, 무엇이 있었는지 보고**한다.

    정책(`on_unknown`)
      keep  기본 — 그대로 저장하되 보고서에 남긴다 (코퍼스가 바뀌는 환경에서 관계를 잃지 않기 위해)
      map   별칭이면 표준 이름으로 고치고, 모르는 것은 그대로 두되 보고
      drop  모르는 관계는 버린다 (스키마가 안정된 운영 환경에서만)
    """
    __slots__ = ("raw", "relations", "rel_alias", "on_unknown", "unknown_rels", "unknown_types", "_types")

    def __init__(self, raw: object = None, types: Optional[List[str]] = None):
        self.raw: Dict[str, object] = dict(raw) if isinstance(raw, dict) else {}
        self.relations: Dict[str, Dict[str, object]] = {}
        for name, spec in (self.raw.get("relations") or {}).items():          # type: ignore
            self.relations[str(name)] = dict(spec) if isinstance(spec, dict) else {}
        self.rel_alias: Dict[str, str] = {}
        for name, spec in self.relations.items():
            for a in (spec.get("aliases") or []):                             # type: ignore
                self.rel_alias[str(a).strip().lower()] = name
        self.on_unknown = str(self.raw.get("on_unknown") or "keep")
        self._types = list(types or [])
        self.unknown_rels: Dict[str, int] = {}
        self.unknown_types: Dict[str, int] = {}

    # ---- 정규화 ----
    def rel(self, name: str) -> Optional[str]:
        """관계 이름을 표준형으로. `None` 이면 '버려라'(on_unknown=drop 인 모르는 관계)."""
        n = str(name or "").strip()
        if not n:
            return None
        if n in self.relations:
            return n
        canon = self.rel_alias.get(n.lower())
        if canon and self.on_unknown in ("map", "drop"):
            return canon
        if canon:                       # keep 정책에서도 별칭은 모아 준다 — 갈라진 이름을 두는 이득이 없다
            return canon
        if not self.relations:          # 스키마를 안 쓰는 설치 — 아무것도 막지 않는다
            return n
        self.unknown_rels[n] = self.unknown_rels.get(n, 0) + 1
        return None if self.on_unknown == "drop" else n

    def etype(self, t: str) -> str:
        """엔티티 type 을 표준형으로 (대소문자만 맞춰 준다 — 버리지는 않는다)."""
        n = str(t or "").strip()
        if not n or not self._types:
            return n
        if n in self._types:
            return n
        low = {x.lower(): x for x in self._types}
        if n.lower() in low:
            return low[n.lower()]
        self.unknown_types[n] = self.unknown_types.get(n, 0) + 1
        return n

    def inverse(self, rel: str) -> str:
        spec = self.relations.get(str(rel)) or {}
        if spec.get("symmetric"):
            return str(rel)
        return str(spec.get("inverse") or "")

    def report(self) -> Dict[str, object]:
        """빌드 보고용 — 무엇이 어휘 밖이었나 (상위 8개씩)."""
        def top(d: Dict[str, int]) -> List[List[object]]:
            return [[k, v] for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:8]]
        return {"on_unknown": self.on_unknown, "relations_declared": len(self.relations),
                "unknown_rel_kinds": len(self.unknown_rels), "unknown_rel_hits": sum(self.unknown_rels.values()),
                "unknown_rels": top(self.unknown_rels),
                "unknown_type_kinds": len(self.unknown_types), "unknown_types": top(self.unknown_types)}


_ASCII_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9&._+-]*")

MATCHING_DEFAULTS: Dict[str, object] = {"ascii_word_boundary": True, "case_sensitive_max_len": 3}
MATCHING_KEYS = ("ascii_word_boundary", "case_sensitive_max_len")
ENTITY_MATCH_KEYS = ("whole_word", "case_sensitive")


def _is_ascii_word(a: str) -> bool:
    """영숫자로 시작하는 ASCII 별칭인가 — 단어 경계·대소문자 규칙은 이런 별칭에만 뜻이 있다 (한글엔 조사가 붙는다)."""
    return bool(_ASCII_WORD.fullmatch(a or ""))


def matching_options(rules: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """rules.json 의 `matching` 절 (없으면 기본값). 값이 이상하면 기본값으로 떨어진다."""
    r = rules if isinstance(rules, dict) else load_rules()
    m = r.get("matching") if isinstance(r.get("matching"), dict) else {}
    out = dict(MATCHING_DEFAULTS)
    if "ascii_word_boundary" in m:
        out["ascii_word_boundary"] = bool(m.get("ascii_word_boundary"))
    try:
        out["case_sensitive_max_len"] = max(0, int(m.get("case_sensitive_max_len", out["case_sensitive_max_len"])))
    except (TypeError, ValueError):
        pass
    return out


def name_in_text(name: str, text: str, ascii_word_boundary: bool = True) -> bool:
    """질의 쪽 별칭 매칭 — 빌드 쪽(`find_entities`)과 같은 경계 규칙. name·text 는 소문자로 들어온다.

    2026-09-24: 예전 `n in ql` 은 "first step" 에서 IR본부(별칭 ir)를 시드로 잡았다 — 빌드 쪽과 같은 오탐이 질의 쪽에도 있었다."""
    if not name or name not in text:
        return False
    if not ascii_word_boundary or not _is_ascii_word(name):
        return True
    for m in re.finditer(re.escape(name), text):
        st, en = m.start(), m.end()
        before_ok = st == 0 or not (text[st - 1].isascii() and text[st - 1].isalnum())
        after_ok = en == len(text) or not (text[en].isascii() and text[en].isalnum())
        if before_ok and after_ok:
            return True
    return False


def load_rules(path: Optional[str] = None) -> Dict[str, object]:
    from . import atomicio
    path = path or rules_path()
    got = atomicio.read_json(path)
    if isinstance(got, dict):
        return got
    save_rules(DEFAULT_RULES, path)
    return json.loads(json.dumps(DEFAULT_RULES))


def save_rules(rules: Dict[str, object], path: Optional[str] = None) -> None:
    from . import atomicio
    atomicio.write_json(path or rules_path(), rules)


def known_types(rules: Optional[Dict[str, object]] = None) -> List[str]:
    """이 규칙 사전에서 **뜻이 있는 엔티티 type** 목록 (정렬).

    왜 필요한가: `entities[*].type` 은 자유 문자열이라 아무 값이나 들어간다. 하지만 값이 실제로 쓰이는 곳은
    정해져 있다 — `types_for_cooccur`(동시출현 관계를 만들 type), `id_patterns[*].type`(ID 패턴이 만드는 type),
    `related_key_type`(문서 front-matter 의 관련 키가 만드는 type), `link_rules[*].target_type`. 여기에 없는 type 을
    가진 엔티티는 사전에 **등록은 되지만 관계가 하나도 생기지 않는다**. 실제로 자가진화 제안에 `type="relation"`,
    `type="CL"`(대문자 — 유효값은 `cl`) 같은 값이 올라와 있었고, 적용해도 조용히 아무 효과가 없었다.
    그래서 제안 설명(proposal_explain)과 규칙 린트가 이 목록으로 검증한다.
    """
    r = rules if isinstance(rules, dict) else load_rules()
    declared = (r.get("schema") or {}).get("entity_types") or {}              # type: ignore
    out = set(str(t) for t in (r.get("types_for_cooccur") or []) if t)
    out.update(str(t) for t in declared)
    for p in (r.get("id_patterns") or []):
        if isinstance(p, dict) and p.get("type"):
            out.add(str(p["type"]))
    for v in (r.get("related_key_type") or {}).values():
        if v:
            out.add(str(v))
    for lr in (r.get("link_rules") or []):
        if isinstance(lr, dict):
            # target_type 뿐 아니라 when_doc_type 도 유효한 type 이다 — 문서 자체가 그 유형의 노드가 된다
            # (`doc_type: tc_list` 인 문서는 type=tc_list 인 문서 노드를 만든다). 이걸 빠뜨려서 실제 빌드에서
            # "어휘 밖 타입 tc_list×15" 가 보고됐다 (2026-09-19 verify_cli 로그에서 발견).
            for k in ("target_type", "when_doc_type"):
                if lr.get(k) and lr[k] != "*":
                    out.add(str(lr[k]))
    for dt in (r.get("explicit_rels") or {}):         # front matter 규칙의 키도 문서 유형이다
        if dt and dt != "*":
            out.add(str(dt))
    if not declared:
        # 선언(schema.entity_types)이 없는 **예전 파일**에서만 사전에 쓰인 type 을 유효한 것으로 본다.
        # 선언이 있으면 이 줄을 타지 않는다 — 안 그러면 사전에 오타로 들어간 type 이 스스로를 정당화해서
        # ("entities 에 쓰였으니 유효한 type") 검증이 아무것도 못 잡는다 (2026-09-19 테스트로 발견).
        for e in (r.get("entities") or {}).values():
            if isinstance(e, dict) and e.get("type"):
                out.add(str(e["type"]))
    out.discard("*")        # link_rules 의 '아무 type 이나' 와일드카드 — 엔티티 type 으로는 쓸 수 없다
    return sorted(out)


def lint(rules: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """그래프 규칙 **파일**을 정적으로 점검한다 (빌드하지 않는다).

    graph_profile.suggest() 와 역할이 다르다 — 저쪽은 *빌드된 그래프*를 보고 "이 규칙이 아무것도 못 만들었다"
    같은 동적 진단을 하고, 이쪽은 *파일만* 보고 "이 규칙은 애초에 돌 수 없다" 를 찾는다. 빌드 전에 쓴다.

    찾는 것: 깨진 정규식 · 없는 값 종류 · 어휘 밖 type/rel · 가려진 link_rules · 겹치는 별칭 · inverse 짝 불일치.
    반환 {issues:[{level,where,detail,fix}], counts:{…}}
    """
    r = rules if isinstance(rules, dict) else load_rules()
    issues: List[Dict[str, str]] = []
    types = known_types(r)
    schema = Schema(r.get("schema"), types)

    def bad(level: str, where: str, detail: str, fix: str = "") -> None:
        issues.append({"level": level, "where": where, "detail": detail, "fix": fix})

    # --- 엔티티 사전 ---
    ents: Dict[str, Dict] = r.get("entities") or {}          # type: ignore
    alias_owner: Dict[str, List[str]] = {}
    for canon, info in ents.items():
        if canon != canon.strip():
            bad("warn", "entities[%s]" % canon, "이름 앞뒤에 공백이 있습니다", "공백을 지우세요 — 공백까지가 이름이 되어 매칭되지 않습니다")
        t = str((info or {}).get("type") or "")
        if t and types and t not in types:
            low = {x.lower(): x for x in types}
            bad("error", "entities[%s].type" % canon, "없는 type '%s' — 등록돼도 관계가 생기지 않습니다" % t,
                ("'%s' 로 고치세요" % low[t.lower()]) if t.lower() in low else "쓸 수 있는 type: %s" % ", ".join(types))
        for a in ((info or {}).get("aliases") or []):
            key = str(a).strip().lower()
            if not key:
                continue
            alias_owner.setdefault(key, []).append(canon)
            if key == canon.strip().lower():
                bad("warn", "entities[%s].aliases" % canon, "별칭 '%s' 이 이름과 같습니다 (효과 없음)" % a, "지우세요")
    for key, owners in alias_owner.items():
        if len(owners) > 1:
            bad("error", "entities.aliases", "별칭 '%s' 을 %s 가 함께 가지고 있습니다 — 어느 쪽으로 붙을지 정해지지 않습니다"
                % (key, ", ".join(owners)), "한 곳만 남기고 나머지는 지우세요")
    # --- 매칭 옵션 (2026-09-24) ---
    mt = r.get("matching")
    if mt is not None and not isinstance(mt, dict):
        bad("error", "matching", "객체여야 합니다: {\"ascii_word_boundary\": true, \"case_sensitive_max_len\": 3}")
    elif isinstance(mt, dict):
        for k in mt:
            if k not in MATCHING_KEYS and not str(k).startswith("_"):
                bad("warn", "matching.%s" % k, "모르는 키 (무시됩니다)", "쓸 수 있는 키: %s" % ", ".join(MATCHING_KEYS))
    for canon, info in ents.items():
        opt = (info or {}).get("match")
        if opt is None:
            continue
        if not isinstance(opt, dict):
            bad("error", "entities[%s].match" % canon, "객체여야 합니다: {\"whole_word\": true, \"case_sensitive\": true}")
            continue
        for k in opt:
            if k not in ENTITY_MATCH_KEYS:
                bad("warn", "entities[%s].match.%s" % (canon, k), "모르는 키 (무시됩니다)", "쓸 수 있는 키: %s" % ", ".join(ENTITY_MATCH_KEYS))

    # --- 정규식 절 ---
    for sec in ("analyst_pattern", "decision_pattern", "money_pattern", "percent_pattern", "measure_pattern", "version_pattern"):
        pat = r.get(sec)
        if pat is None:
            continue
        try:
            re.compile(str(pat))
        except re.error as e:
            bad("error", sec, "정규식이 깨졌습니다: %s" % e, "패턴을 고치세요 — 이 절은 빌드 시작 때 컴파일되어 빌드가 멈춥니다")
    for i, pat in enumerate(r.get("date_patterns") or []):   # type: ignore
        try:
            re.compile(str(pat))
        except re.error as e:
            bad("error", "date_patterns[%d]" % i, "정규식이 깨졌습니다: %s" % e, "패턴을 고치세요")

    # --- 관계 패턴 ---
    for i, p in enumerate(r.get("relation_patterns") or []):  # type: ignore
        where = "relation_patterns[%d] %s" % (i, (p or {}).get("name") or "")
        try:
            re.compile(str((p or {}).get("regex") or ""))
        except re.error as e:
            bad("error", where, "정규식이 깨졌습니다: %s — 이 패턴만 조용히 무시됩니다" % e, "패턴을 고치세요")
            continue
        v = str((p or {}).get("value") or "entity")
        if v not in VALUE_TYPES:
            bad("error", where, "없는 값 종류 '%s' — text 로 처리됩니다" % v,
                "쓸 수 있는 값 종류: %s" % ", ".join(VALUE_TYPES))
        for scope in ("in_decision", "in_chunk"):
            spec = (p or {}).get(scope)
            if not isinstance(spec, dict):
                continue
            rel = str(spec.get("rel") or (p or {}).get("name") or "")
            if schema.relations and rel not in schema.relations and rel.lower() not in schema.rel_alias:
                bad("warn", where + "." + scope, "관계 '%s' 이 schema.relations 에 없습니다" % rel,
                    "schema.relations 에 추가하거나, 같은 뜻의 표준 관계의 aliases 에 넣으세요")

    # --- chunk_values ---
    for i, cv in enumerate(r.get("chunk_values") or []):      # type: ignore
        v = str((cv or {}).get("value") or "")
        if v not in VALUE_TYPES:
            bad("error", "chunk_values[%d]" % i, "없는 값 종류 '%s' — 이 줄은 무시됩니다" % v,
                "쓸 수 있는 값 종류: %s" % ", ".join(VALUE_TYPES))

    # --- id_patterns ---
    for i, p in enumerate(r.get("id_patterns") or []):        # type: ignore
        try:
            re.compile(str((p or {}).get("regex") or ""))
        except re.error as e:
            bad("error", "id_patterns[%d]" % i, "정규식이 깨졌습니다: %s" % e, "패턴을 고치세요")
        t = str((p or {}).get("type") or "")
        if t and types and t not in types:
            bad("warn", "id_patterns[%d].type" % i, "type '%s' 이 어디에도 선언돼 있지 않습니다" % t,
                "types_for_cooccur 또는 schema.entity_types 에 넣으세요 — 아니면 공동출현 관계가 생기지 않습니다")

    # --- link_rules: 앞선 규칙에 가려진 줄 ---
    seen: List[Tuple[str, str]] = []
    for i, lr in enumerate(r.get("link_rules") or []):        # type: ignore
        w, t = str((lr or {}).get("when_doc_type", "*")), str((lr or {}).get("target_type", "*"))
        for j, (pw, pt) in enumerate(seen):
            if (pw in (w, "*")) and (pt in (t, "*")) and (pw, pt) != (w, t):
                bad("warn", "link_rules[%d]" % i, "앞의 link_rules[%d] (%s→%s) 에 가려져 영원히 걸리지 않습니다" % (j, pw, pt),
                    "이 줄을 link_rules[%d] 앞으로 옮기세요 (먼저 맞는 규칙이 이깁니다)" % j)
                break
        seen.append((w, t))
        if schema.relations:
            rel = str((lr or {}).get("rel") or "")
            if rel and rel not in schema.relations and rel.lower() not in schema.rel_alias:
                bad("warn", "link_rules[%d].rel" % i, "관계 '%s' 이 schema.relations 에 없습니다" % rel, "schema.relations 에 추가하세요")

    # --- 스키마 자체: inverse 짝 ---
    for name, spec in schema.relations.items():
        inv = str(spec.get("inverse") or "")
        if not inv:
            continue
        back = schema.relations.get(inv)
        if back is None:
            bad("warn", "schema.relations[%s].inverse" % name, "역관계 '%s' 이 선언돼 있지 않습니다" % inv,
                "schema.relations 에 '%s': {\"inverse\": \"%s\"} 를 추가하세요" % (inv, name))
        elif str(back.get("inverse") or "") != name:
            bad("warn", "schema.relations[%s].inverse" % name, "'%s' 의 inverse 가 '%s' 를 가리키지 않습니다 (짝이 어긋남)" % (inv, name),
                "양쪽을 서로 가리키게 맞추세요")
    if schema.on_unknown not in ("keep", "map", "drop"):
        bad("error", "schema.on_unknown", "'%s' 는 없는 정책입니다" % schema.on_unknown, "keep | map | drop 중 하나")

    counts = {"entities": len(ents), "aliases": len(alias_owner), "types": len(types),
              "relation_patterns": len(r.get("relation_patterns") or []),          # type: ignore
              "chunk_values": len(r.get("chunk_values") or []),                     # type: ignore
              "id_patterns": len(r.get("id_patterns") or []),                       # type: ignore
              "link_rules": len(r.get("link_rules") or []),                         # type: ignore
              "schema_relations": len(schema.relations),
              "errors": sum(1 for i in issues if i["level"] == "error"),
              "warns": sum(1 for i in issues if i["level"] == "warn")}
    return {"issues": issues, "counts": counts, "types": types, "value_types": list(VALUE_TYPES)}


def fill_defaults(path: Optional[str] = None, dry_run: bool = False) -> Dict[str, object]:
    """data/rules.json 에 빠진 최상위 절(entities · relation_patterns · *_pattern(s) · types_for_cooccur · id_patterns · link_rules ·
    explicit_rels · related_key_type)을 DEFAULT_RULES 의 값으로 채워 쓴다 (있는 절은 유지). 파일이 없으면 통째로 만든다. 반환 {path, added, written}."""
    from . import atomicio
    p = path or rules_path()
    prev = atomicio.read_json(p)
    created = prev is None
    prev = prev if isinstance(prev, dict) else {}
    out: Dict[str, object] = dict(prev)
    added: List[str] = []
    for k, v in DEFAULT_RULES.items():
        if k not in out:
            out[k] = json.loads(json.dumps(v))
            added.append(k)
    # `schema` 는 절이 이미 있어도 **안에 빠진 표준 어휘**를 채운다 (2026-09-19).
    # 왜: 새 관계/타입이 기본값에 추가돼도, schema 절을 한 번 만든 설치는 영원히 옛 어휘로 남아
    # lint 가 "어휘에 없다" 를 계속 낸다. 사람이 적은 항목은 건드리지 않고 **없는 것만** 넣는다.
    if not created and isinstance(out.get("schema"), dict):
        sch: Dict[str, object] = out["schema"]                                  # type: ignore
        dsch: Dict[str, object] = DEFAULT_RULES["schema"]                       # type: ignore
        sch.setdefault("on_unknown", dsch["on_unknown"])
        for sub in ("relations", "entity_types"):
            cur = sch.setdefault(sub, {})
            if not isinstance(cur, dict):
                continue
            for name, spec in (dsch.get(sub) or {}).items():                    # type: ignore
                if name not in cur:
                    cur[name] = json.loads(json.dumps(spec))
                    added.append("schema.%s.%s" % (sub, name))
    rep: Dict[str, object] = {"path": p, "added": added, "created": created, "dry_run": dry_run, "written": False, "changed": bool(added or created)}
    if rep["changed"] and not dry_run:
        atomicio.write_json(p, out)
        rep["written"] = True
    return rep


class RuleExtractor:
    def __init__(self, rules: Optional[Dict[str, object]] = None, cooccur_window: int = 8, cooccur_scale: float = 120.0,
                 cooccur_min_w: float = 0.15, dates_per_chunk: int = 8, amounts_per_chunk: int = 6):
        self.rules = rules or load_rules()
        self.cooccur_window = int(cooccur_window)
        self.cooccur_scale = float(cooccur_scale)
        self.cooccur_min_w = float(cooccur_min_w)
        self.dates_per_chunk = int(dates_per_chunk)
        self.amounts_per_chunk = int(amounts_per_chunk)
        self._compile()

    def _compile(self) -> None:
        ents: Dict[str, Dict] = self.rules["entities"]  # type: ignore
        mo = matching_options(self.rules)
        self._ascii_wb, self._cs_max = bool(mo["ascii_word_boundary"]), int(mo["case_sensitive_max_len"])
        self.alias_map: Dict[str, str] = {}          # 소문자 별칭 → 대표어 (다른 모듈이 쓴다: graph_build · 결정 패턴)
        self._canon_cs: Dict[str, str] = {}          # 원문 그대로 → 대표어 (대소문자 구분 별칭)
        cs_alts: List[str] = []
        ci_alts: List[str] = []
        for canon, info in ents.items():
            if str(canon).startswith("_"):
                continue
            opt = info.get("match") if isinstance(info.get("match"), dict) else {}
            for a in [canon] + list(info.get("aliases", []) or []):
                a = str(a)
                if not a:
                    continue
                self.alias_map[a.lower()] = canon
                cs = opt.get("case_sensitive")
                if cs is None:
                    cs = _is_ascii_word(a) and 0 < len(a) <= self._cs_max
                ww = opt.get("whole_word")
                if ww is None:
                    ww = self._ascii_wb and _is_ascii_word(a)
                pat = re.escape(a)
                if ww:
                    pat = "(?<![A-Za-z0-9])" + pat + "(?![A-Za-z0-9])"
                if cs:
                    self._canon_cs[a] = canon
                    cs_alts.append((a, pat))
                else:
                    ci_alts.append((a, pat))
        # 긴 별칭 우선 매칭
        cs_alts.sort(key=lambda t: -len(t[0]))
        ci_alts.sort(key=lambda t: -len(t[0]))
        self._ent_re_cs = re.compile("|".join(p_ for _, p_ in cs_alts)) if cs_alts else None
        self._ent_re = re.compile("|".join(p_ for _, p_ in ci_alts), re.I) if ci_alts else None
        # 관계 패턴: 파일의 선언을 그대로 쓴다. `rel` 만 있는 예전 파일은 이름으로 기본 동작을 채워 준다(호환).
        self._rel_pats: List[Tuple[Dict[str, object], "re.Pattern"]] = []
        for p in self.rules.get("relation_patterns", []):    # type: ignore
            try:
                self._rel_pats.append((_norm_rel_pattern(dict(p)), re.compile(str(p["regex"]))))
            except re.error:
                continue                                     # 정규식이 깨져도 나머지 패턴은 살린다
        self._analyst_re = re.compile(self.rules["analyst_pattern"])  # type: ignore
        self._decision_re = re.compile(self.rules["decision_pattern"])  # type: ignore
        self._date_res = [re.compile(p) for p in self.rules["date_patterns"]]  # type: ignore
        self._money_re = re.compile(self.rules["money_pattern"])  # type: ignore
        self._pct_re = re.compile(self.rules["percent_pattern"])  # type: ignore
        # 새 스칼라 패턴 — 없는 파일(예전 rules.json)이면 기본값으로 채운다
        self._measure_re = re.compile(str(self.rules.get("measure_pattern") or DEFAULT_RULES["measure_pattern"]))
        self._version_re = re.compile(str(self.rules.get("version_pattern") or DEFAULT_RULES["version_pattern"]))
        self._chunk_values = _norm_chunk_values(self.rules, self.dates_per_chunk, self.amounts_per_chunk)
        self._schema = Schema(self.rules.get("schema"), known_types(self.rules))
        self._id_pats = [(p, re.compile(p["regex"], re.I)) for p in self.rules.get("id_patterns", DEFAULT_RULES["id_patterns"])]  # type: ignore
        self._link_rules = list(self.rules.get("link_rules", DEFAULT_RULES["link_rules"]))  # type: ignore
        self._explicit_rels = dict(self.rules.get("explicit_rels", DEFAULT_RULES["explicit_rels"]))  # type: ignore
        self._related_key_type = dict(self.rules.get("related_key_type", DEFAULT_RULES["related_key_type"]))  # type: ignore

    # ------------------------------------------------------------------ 결정적 관계
    def find_ids(self, text: str) -> List[Tuple[str, str, int]]:
        """(canonical_id, type, position) — 본문에 언급된 문서 ID."""
        out: List[Tuple[str, str, int]] = []
        seen = set()
        for pat, rx in self._id_pats:
            for m in rx.finditer(text):
                cid = canonical_id(pat, m)
                key = (cid, m.start())
                if key in seen:
                    continue
                seen.add(key)
                out.append((cid, str(pat["type"]), m.start()))
        return out

    def link_rule(self, doc_type: str, target_type: str) -> Optional[Dict[str, object]]:
        for r in self._link_rules:
            if str(r.get("when_doc_type", "*")) in (doc_type, "*") and str(r.get("target_type", "*")) in (target_type, "*"):
                return r
        return None

    def explicit_rel(self, doc_type: str, key: str) -> str:
        by_type = self._explicit_rels.get(doc_type) or {}
        return str(by_type.get(key) or (self._explicit_rels.get("*") or {}).get(key) or "references")

    def explicit_relations(self, doc_meta: Dict[str, object], doc_eid: str) -> Tuple[Dict[str, RuleEntity], List[RuleRelation]]:
        """front matter related.* → (대상 ID 엔티티, explicit 관계). 문서당 1회(첫 청크)에 호출."""
        ents: Dict[str, RuleEntity] = {}
        rels: List[RuleRelation] = []
        dt = str(doc_meta.get("doc_type") or "")
        for key, ids in (doc_meta.get("related") or {}).items():   # type: ignore
            ttype = str(self._related_key_type.get(key) or key.rstrip("s"))
            rel = self.explicit_rel(dt, str(key))
            for raw in ids or []:   # type: ignore
                name = str(raw).strip().upper()
                if not name:
                    continue
                eid = entity_id_for(name)
                if eid == doc_eid:
                    continue
                ents.setdefault(eid, RuleEntity(eid, name, ttype, [], "", 0.95))
                rels.append(RuleRelation(doc_eid, eid, rel, "front matter related.%s" % key, 1.0, 0.98, "explicit"))
        return ents, rels

    def _apply_rel_patterns(self, text: str, scope: str, src_eid: str, add, rels: List[RuleRelation]) -> None:
        """관계 패턴을 **파일의 선언대로** 적용한다 (코드에 rel 이름을 적지 않는다).

        scope = 'in_decision'(결정 블록 안, 출발=결정 노드) | 'in_chunk'(청크 전체, 출발=문서 노드).
        value = entity | date | money | percent | text | id.
        """
        for pat, rx in self._rel_pats:
            spec = pat.get(scope)
            if not isinstance(spec, dict):
                continue
            rel = str(spec.get("rel") or pat.get("name") or "references")
            w = float(spec.get("weight", 0.7) or 0.7)
            conf = float(spec.get("confidence", 0.7) or 0.7)
            desc_pre = str(spec.get("desc") or "")
            value = str(pat.get("value") or "entity")
            node_type = str(pat.get("node_type") or "term")
            split = str(pat.get("split") or "")
            # 값 종류는 레지스트리에서 (VALUE_TYPES). 모르는 종류는 text 로 떨어뜨린다 —
            # 규칙 파일에 오타가 있어도 빌드가 죽지 않게. lint() 가 그 오타를 따로 알려 준다.
            vt = VALUE_TYPES.get(value) or VALUE_TYPES["text"]
            spec_pat = dict(pat, node_type=node_type, confidence=conf, split=split)
            for mm in rx.finditer(text):
                val = (mm.group(1) if (mm.lastindex or 0) >= 1 else mm.group(0)).strip()
                if not val:
                    continue
                desc = desc_pre + val[:120]
                for dst in vt.resolve(self, val, spec_pat, add):
                    rels.append(RuleRelation(src_eid, dst, rel, desc, w, conf))

    # ------------------------------------------------------------------
    def find_entities(self, text: str) -> List[Tuple[str, int]]:
        """(canonical_name, position) 목록. 대소문자 구분 별칭과 무시 별칭을 따로 찾아 합치고, 겹치면 **먼저 시작하고 더 긴** 것만 남긴다."""
        found: List[Tuple[int, int, str]] = []
        if self._ent_re_cs is not None:
            for m in self._ent_re_cs.finditer(text):
                canon = self._canon_cs.get(m.group(0)) or self.alias_map.get(m.group(0).lower())
                if canon:
                    found.append((m.start(), m.end(), canon))
        if self._ent_re is not None:
            for m in self._ent_re.finditer(text):
                canon = self.alias_map.get(m.group(0).lower())
                if canon:
                    found.append((m.start(), m.end(), canon))
        found.sort(key=lambda t: (t[0], -(t[1] - t[0])))
        out: List[Tuple[str, int]] = []
        last_end = -1
        for st, en, canon in found:
            if st < last_end:
                continue
            out.append((canon, st))
            last_end = en
        return out

    def extract_chunk(self, chunk_text: str, heading: str, doc_id: str, doc_title: str, doc_meta: Optional[Dict[str, object]] = None
                      ) -> Tuple[Dict[str, RuleEntity], Dict[str, int], List[RuleRelation]]:
        """청크 하나에서 엔티티(+멘션 카운트)와 관계를 추출.
        doc_meta(doc_type, ext_id …) 가 있으면 문서 노드는 ext_id(예 CL-55321, type=cl) 가 되고, 본문의 ID 언급은 link_rules 로 결정적 관계가 된다."""
        ents: Dict[str, RuleEntity] = {}
        counts: Dict[str, int] = {}
        rels: List[RuleRelation] = []
        einfo: Dict[str, Dict] = self.rules["entities"]  # type: ignore
        dm = doc_meta or {}
        doc_type = str(dm.get("doc_type") or "")
        ext_id = str(dm.get("ext_id") or "").upper()

        def add(name: str, etype: str, desc: str = "", conf: float = 0.9, aliases: Optional[List[str]] = None,
                count: bool = True) -> str:
            """노드를 청크 지역 사전에 등록하고 id 를 돌려준다.
            `count=False` 면 멘션으로 세지 않는다 — 이미 청크 전체 스캔이 센 엔티티를 관계 패턴 안에서 다시 만났을 때."""
            eid = entity_id_for(name)
            if eid not in ents:
                ents[eid] = RuleEntity(eid, name, self._schema.etype(etype), list(aliases or []), desc, conf)
            if count:
                counts[eid] = counts.get(eid, 0) + 1
            else:
                counts.setdefault(eid, 0)
            return eid

        # 문서 자체를 노드로 (문서 <-> 엔티티 연결에 사용). ID 가 있으면 ID 노드가 곧 문서 노드이며
        # 모든 청크에 멘션을 남겨 doc_refs/그래프 검색이 원본 문서를 바로 찾게 한다 (제목 노드는 멘션 없음: 허브 방지).
        if ext_id:
            doc_eid = add(ext_id, doc_type or "document", "문서: %s · %s" % (doc_id, doc_title[:80]), 1.0, [doc_title] if doc_title else [])
            counts[doc_eid] = 1
        else:
            doc_eid = add(doc_title, doc_type or "document", "문서: " + doc_id, 1.0)
            counts[doc_eid] = 0

        # 사전 엔티티
        positions: List[Tuple[str, int]] = []
        for canon, pos in self.find_entities(chunk_text):
            info = einfo[canon]
            eid = add(canon, info["type"], "", 0.95, info.get("aliases", []))
            positions.append((eid, pos))

        # 문서 ID 언급 (ISSUE-2041, CL-55321 …) → ID 노드 + link_rules 결정적 관계 (provenance=rule)
        for cid, ctype, pos in self.find_ids(chunk_text):
            if cid == ext_id:
                continue
            id_eid = add(cid, ctype, "", 0.95)
            positions.append((id_eid, pos))
            lr = self.link_rule(doc_type, ctype)
            if lr:
                rels.append(RuleRelation(doc_eid, id_eid, str(lr["rel"]), "%s 본문에서 %s 언급 (%s)" % (ext_id or doc_title[:30], cid, heading[:40]),
                                         float(lr.get("weight", 0.5)), float(lr.get("confidence", 0.7)), "rule"))

        # 결정사항 노드 (D1. …)
        for m in self._decision_re.finditer(chunk_text):
            dname = "%s %s (%s)" % (m.group(1), m.group(2).strip()[:60], doc_title[:30])
            deid = add(dname, "decision", m.group(2).strip(), 0.9)
            rels.append(RuleRelation(doc_eid, deid, "decides", "문서에 포함된 결정사항", 1.0, 0.9))
            positions.append((deid, m.start()))
            # 결정 블록 범위 안의 담당/마감/금액
            block_end = chunk_text.find("\n### ", m.end())
            block = chunk_text[m.start(): block_end if block_end > 0 else len(chunk_text)]
            self._apply_rel_patterns(block, "in_decision", deid, add, rels)

        # 결정 블록 밖, 청크 전체 — 출발 노드는 문서
        self._apply_rel_patterns(chunk_text, "in_chunk", doc_eid, add, rels)

        # 애널리스트 코멘트: **미래에셋 (5/15)**: "…"
        for m in self._analyst_re.finditer(chunk_text):
            canon = self.alias_map.get(m.group(1).lower(), m.group(1))
            info = einfo.get(canon, {"type": "org"})
            aid = add(canon, info["type"], "", 0.9)
            rels.append(RuleRelation(aid, doc_eid, "comments_on", "%s (%s): %s" % (canon, m.group(2), m.group(3)[:120]), 1.0, 0.9))

        # 청크마다 남기는 스칼라 값 (날짜 · 금액 · 수치+단위 · 버전 …) → 문서에 연결 (경량 멘션).
        # 무엇을 남길지는 `chunk_values` 절이 정한다 — 코드에 종류가 박혀 있지 않다 (2026-09-19).
        for cv in self._chunk_values:
            cap = int(cv.get("per_chunk") or 0)
            if cap <= 0:
                continue
            vt = VALUE_TYPES.get(str(cv.get("value") or "")) or None
            if vt is None:
                continue
            seen_v: List[str] = []
            for dst in vt.resolve(self, chunk_text, cv, add):
                if dst in seen_v:
                    continue
                seen_v.append(dst)
                if len(seen_v) > cap:
                    break
                rels.append(RuleRelation(doc_eid, dst, str(cv.get("rel") or ("mentions_" + str(cv.get("name") or "value"))),
                                         heading[:80], float(cv.get("weight", 0.3) or 0.3),
                                         float(cv.get("confidence", 0.6) or 0.6), "cooccur"))

        # 공동출현 관계 (사전 엔티티/결정 간, 거리 기반 가중치)
        cooc_types = set(self.rules.get("types_for_cooccur", []))  # type: ignore
        positions.sort(key=lambda x: x[1])
        for i in range(len(positions)):
            for j in range(i + 1, min(i + self.cooccur_window, len(positions))):
                a, pa = positions[i]
                b, pb = positions[j]
                if a == b:
                    continue
                if ents[a].type not in cooc_types and ents[a].type != "decision":
                    continue
                if ents[b].type not in cooc_types and ents[b].type != "decision":
                    continue
                dist = max(1, pb - pa)
                w = round(min(1.0, self.cooccur_scale / dist), 3)
                if w < self.cooccur_min_w:
                    continue
                rels.append(RuleRelation(a, b, "co_occurs", heading[:80], w, 0.6, "cooccur"))

        # 문서-엔티티 mentions 관계
        for eid, c in counts.items():
            if eid != doc_eid and c > 0 and ents[eid].type in cooc_types:
                rels.append(RuleRelation(doc_eid, eid, "mentions", heading[:80], min(1.0, 0.3 + 0.1 * c), 0.7, "cooccur"))
        # 관계 이름을 스키마 어휘로 정규화 (별칭 → 표준명, 모르는 이름은 정책대로). 스키마가 없으면 그대로 통과한다.
        return ents, counts, self.normalize_relations(rels)

    def normalize_relations(self, rels: List[RuleRelation]) -> List[RuleRelation]:
        if not self._schema.relations:
            return rels
        out: List[RuleRelation] = []
        for r in rels:
            canon = self._schema.rel(r.rel)
            if canon is None:                 # on_unknown="drop"
                continue
            if canon != r.rel:
                r = RuleRelation(r.src, r.dst, canon, r.description, r.weight, r.confidence, r.provenance)
            out.append(r)
        return out

    @property
    def schema(self) -> "Schema":
        return self._schema

    def field_rel_names(self) -> set:
        """`relation_patterns` 가 만드는 관계 이름들 (+ 결정 노드의 `decides`).

        빌드 통계에서 '필드에서 나온 관계' 와 'ID 언급에서 나온 결정적 관계(id_relations)' 를 가르는 데 쓴다.
        예전에는 이 목록이 graph_build.py 에 문자열 튜플로 박혀 있어, 파일에 새 패턴을 넣으면 통계가 틀어졌다.
        """
        out = {"decides"}
        for pat, _rx in self._rel_pats:
            for scope in ("in_decision", "in_chunk"):
                spec = pat.get(scope)
                if isinstance(spec, dict):
                    out.add(str(spec.get("rel") or pat.get("name") or "references"))
        # 스키마 별칭도 같은 편으로 (uses↔used 처럼 정규화된 뒤 이름이 달라질 수 있다)
        for name in list(out):
            inv = self._schema.inverse(name)
            if inv:
                out.add(inv)
        return out

    def _dates(self, text: str) -> List[str]:
        out: List[str] = []
        for rx in self._date_res:
            for m in rx.finditer(text):
                d = re.sub(r"\s+", "", m.group(0))
                if d not in out:
                    out.append(d)
        return out
