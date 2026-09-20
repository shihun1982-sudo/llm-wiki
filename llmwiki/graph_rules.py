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
        self.alias_map: Dict[str, str] = {}
        for canon, info in ents.items():
            self.alias_map[canon.lower()] = canon
            for a in info.get("aliases", []):
                self.alias_map[a.lower()] = canon
        # 긴 별칭 우선 매칭
        alts = sorted(self.alias_map.keys(), key=len, reverse=True)
        self._ent_re = re.compile("|".join(re.escape(a) for a in alts), re.I) if alts else None
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
            for mm in rx.finditer(text):
                val = (mm.group(1) if (mm.lastindex or 0) >= 1 else mm.group(0)).strip()
                if not val:
                    continue
                desc = desc_pre + val[:120]
                if value == "entity":
                    for canon, _pos in self.find_entities(val):
                        rels.append(RuleRelation(src_eid, entity_id_for(canon), rel, desc, w, conf))
                elif value == "date":
                    for d in self._dates(val):
                        rels.append(RuleRelation(src_eid, add(d, "date", "", 0.9), rel, desc, w, conf))
                elif value == "money":
                    for a in self._money_re.findall(val):
                        rels.append(RuleRelation(src_eid, add(a.strip(), "amount", "", 0.9), rel, desc, w, conf))
                elif value == "percent":
                    for a in self._pct_re.findall(val):
                        rels.append(RuleRelation(src_eid, add(a.strip(), "percent", "", 0.9), rel, desc, w, conf))
                elif value == "id":
                    for cid, ctype, _pos in self.find_ids(val):
                        rels.append(RuleRelation(src_eid, add(cid, ctype, "", 0.95), rel, desc, w, conf))
                else:                                   # text — 잡은 문자열 자체가 노드 (split 이 있으면 나눠서)
                    parts = [x.strip() for x in val.split(split)] if split else [val]
                    for name in [x for x in parts if x][:12]:
                        rels.append(RuleRelation(src_eid, add(name, node_type, "", conf), rel, desc, w, conf))

    # ------------------------------------------------------------------
    def find_entities(self, text: str) -> List[Tuple[str, int]]:
        """(canonical_name, position) 목록."""
        out: List[Tuple[str, int]] = []
        if not self._ent_re:
            return out
        for m in self._ent_re.finditer(text):
            canon = self.alias_map.get(m.group(0).lower())
            if canon:
                out.append((canon, m.start()))
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

        def add(name: str, etype: str, desc: str = "", conf: float = 0.9, aliases: Optional[List[str]] = None) -> str:
            eid = entity_id_for(name)
            if eid not in ents:
                ents[eid] = RuleEntity(eid, name, etype, list(aliases or []), desc, conf)
            counts[eid] = counts.get(eid, 0) + 1
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

        # 날짜 / 금액 / 퍼센트 → 문서에 연결 (경량 멘션)
        for d in self._dates(chunk_text)[:self.dates_per_chunk]:
            did = add(d, "date", "", 0.7)
            rels.append(RuleRelation(doc_eid, did, "mentions_date", heading[:80], 0.3, 0.6, "cooccur"))
        for a in list(dict.fromkeys(self._money_re.findall(chunk_text)))[:self.amounts_per_chunk]:
            aid = add(a.strip(), "amount", "", 0.7)
            rels.append(RuleRelation(doc_eid, aid, "mentions_amount", heading[:80], 0.3, 0.6, "cooccur"))

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
        return ents, counts, rels

    def _dates(self, text: str) -> List[str]:
        out: List[str] = []
        for rx in self._date_res:
            for m in rx.finditer(text):
                d = re.sub(r"\s+", "", m.group(0))
                if d not in out:
                    out.append(d)
        return out
