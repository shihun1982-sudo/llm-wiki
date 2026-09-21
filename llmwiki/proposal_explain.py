# -*- coding: utf-8 -*-
"""자가진화 제안을 **사람이 읽을 수 있는 설명**으로 바꾼다.

왜 필요한가 (2026-09-19):
  Evolve 탭·CLI·MCP 는 제안을 payload JSON 원문으로만 보여 줬다. 예를 들어

      #124 entity conf=0.80  {"aliases":["HWD-PHY-TIMING-B1 · PHY 타이밍 및 레지스터 사양 rev B1"],
                              "name":"HWD-PHY-TIMING-B1 ","type":"relation"}

  이것만 보고 승인 여부를 정하려면 (1) 이게 어느 파일을 바꾸는지 (2) 바꾸면 무엇이 좋아지는지
  (3) 얼마나 비싼지(리빌드?) (4) 애초에 값이 성한지 를 전부 사람이 코드에서 알아내야 했다.
  실제로 위 제안은 `type="relation"` 이 엔티티 type 으로 존재하지 않는 값이고, 이름 끝에 공백이 있고,
  별칭은 별칭이 아니라 문서 헤딩 한 줄 통째다 — 적용해도 **조용히 아무 효과가 없다**.

그래서 이 모듈은 제안 하나를 받아 다음을 계산한다 (LLM 을 쓰지 않는다 — 순수 함수, 빠르다):
  title     한 줄 요약            what   무엇이 어떻게 바뀌는지 (평문)
  target    바뀌는 파일/테이블     diff   적용 전/후 미리보기
  impact    리빌드·영향 범위·되돌리기·위험도
  checks    값 검증 (error 면 적용해도 실패하거나 효과 없음)

세 창구가 같은 설명을 쓴다: Web `/api/evolve/describe`, CLI `evolve show <id>`, MCP `wiki_evolve(explain=true)`.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

#: 리빌드 등급 → 사람 말. apply_proposal 이 실제로 하는 일과 1:1 이다
#: (need_rebuild 이면 pipe.build(full=(kind=="chunk_params"))).
REBUILD_TEXT = {
    "none": "리빌드 없음 — 승인 즉시 다음 질의부터 반영됩니다",
    "incremental": "증분 빌드가 따라옵니다 (승인 시 자동) — 보통 수십 초",
    "full": "**전체 리빌드**가 따라옵니다 (승인 시 자동) — 모든 문서를 다시 나누고 임베딩합니다. 가장 비쌉니다",
    "none_na": "해당 없음 — 이 제안은 시스템이 적용하지 않습니다",
}

#: 종류별 고정 영향 (파일 · 리빌드 · 영향 스테이지 · 위험도 · 되돌리기)
KIND_IMPACT: Dict[str, Dict[str, str]] = {
    "synonym": {"target": "DB: synonyms 테이블", "rebuild": "none", "risk": "medium",
                "scope": "FTS 검색 — 이 낱말이 들어간 **모든** 질의가 확장됩니다",
                "revert": "`rules` 에서 동의어 삭제 또는 `evolve rollback`"},
    "alias": {"target": "data/rules.json › entities[대상].aliases", "rebuild": "incremental", "risk": "low",
              "scope": "그래프 시드 매칭 — 별칭으로 질의해도 해당 엔티티를 찾습니다",
              "revert": "`evolve rollback` (적용 전 스냅샷) 또는 rules.json 에서 별칭 제거 후 재빌드"},
    "entity": {"target": "data/rules.json › entities", "rebuild": "incremental", "risk": "low",
               "scope": "그래프 — 새 노드가 생기고 문서에서 멘션이 잡힙니다",
               "revert": "`evolve rollback` 또는 rules.json 에서 항목 제거 후 재빌드"},
    "relation": {"target": "DB: relations 테이블", "rebuild": "none", "risk": "low",
                 "scope": "그래프 확장(hop) — 한쪽 엔티티가 잡히면 다른 쪽 문서까지 끌어옵니다",
                 "revert": "`evolve rollback`"},
    "wiki_note": {"target": "wiki/<page>.md (편집 노트 블록)", "rebuild": "incremental", "risk": "low",
                  "scope": "색인 본문 — overlay 문서로 들어가 모든 채널의 검색 대상이 됩니다",
                  "revert": "wiki 페이지에서 노트 블록 삭제 후 재빌드"},
    "query_rule": {"target": "query_rules.json", "rebuild": "none", "risk": "medium",
                   "scope": "질의 규칙 확장 단계 — 질의어를 사전대로 넓히거나 좁힙니다",
                   "revert": "`rules remove <type> <term>` 또는 Web 질의규칙 탭"},
    "pin": {"target": "pins.json", "rebuild": "none", "risk": "medium",
            "scope": "부스트 단계 — 조건이 맞는 질의에서 지정 문서/청크를 끌어올립니다",
            "revert": "`pin remove <id>` 또는 Web 고정근거 화면"},
    "tuning": {"target": "tuning.json (또는 config 항목이면 config.json)", "rebuild": "none", "risk": "medium",
               "scope": "해당 스테이지 동작이 전역으로 바뀝니다",
               "revert": "`tuning set <key>=<이전값>` — 아래 '적용 전' 값 참고"},
    "chunk_params": {"target": "config.json › chunk_max_chars / chunk_overlap_chars", "rebuild": "full", "risk": "high",
                     "scope": "청킹 — 모든 문서의 청크 경계가 달라집니다 (검색 결과 전반에 영향)",
                     "revert": "`evolve rollback` (스냅샷 복원) — 되돌릴 때도 전체 리빌드가 듭니다"},
    "corpus_gap": {"target": "(없음 — 기록용)", "rebuild": "none_na", "risk": "low",
                   "scope": "적용되지 않습니다. 사람이 문서를 넣어야 해결됩니다",
                   "revert": "-"},
}

#: 종류별 필수 payload 키. apply_proposal 이 실제로 꺼내 쓰는 키와 같다 —
#: 여기가 비면 적용 시 KeyError 로 failed 가 된다 (예전에는 그 사실을 적용해 봐야만 알 수 있었다).
REQUIRED: Dict[str, List[List[str]]] = {   # [[대안1키...], [대안2키...]] — 각 묶음 중 하나는 있어야 함
    "synonym": [["term"], ["expansion"]],
    "alias": [["entity"], ["alias"]],
    "entity": [["name"]],
    "relation": [["src"], ["dst"]],
    "wiki_note": [["page"], ["note"]],
    "query_rule": [["term"], ["values", "value"]],
    "pin": [["doc", "chunk"]],
    "tuning": [["key"], ["value"]],
    "chunk_params": [["chunk_max_chars", "chunk_overlap_chars"]],
    "corpus_gap": [["topic"]],
}

_SENTENCEISH = re.compile(r"[·:：\n]|\s{2,}")

#: 제안이 어디서 왔는지 — 사람이 신뢰도를 가늠하는 가장 중요한 단서
ORIGIN_TEXT = {
    "capture": "질의 실행 중 자동 탐지 (근거 약함 — 한 번의 검색 실패에서 나옴)",
    "feedback": "사용자 👍/👎 피드백 (사람이 준 신호)",
    "forensics": "포렌식 반복 소견 (같은 문제가 여러 번 관찰됨 — 근거 강함)",
    "expectation": "기대 문서 역추적 (정답을 알고 있는 상태에서 나온 제안 — 근거 강함)",
    "llm_review": "LLM 이 질의 로그를 읽고 제안 (검증 안 된 추측 — 값을 꼭 확인)",
    "manual": "사람이 직접 등록",
    "consolidate": "메모리 통합 (반복 실패 묶음에서 생성)",
}


def _s(v: Any) -> str:
    return "" if v is None else str(v)


class Ctx:
    """설명 여러 건을 만들 때 규칙 사전·엔티티 목록을 **한 번만** 읽기 위한 문맥."""

    def __init__(self, pipe: Any = None):
        self.pipe = pipe
        try:
            from .graph_rules import load_rules, known_types
            self.rules = load_rules()
            self.types = known_types(self.rules)
        except Exception:
            self.rules, self.types = {}, []
        self.entities: Dict[str, Any] = dict((self.rules.get("entities") or {})) if self.rules else {}
        self.ent_lower = dict((k.strip().lower(), k) for k in self.entities)
        self._syn: Optional[Dict[str, Any]] = None
        self._qr: Optional[Dict[str, Any]] = None

    @property
    def synonyms(self) -> Dict[str, Any]:
        if self._syn is None:
            try:
                self._syn = dict(self.pipe.store.synonyms())
            except Exception:
                self._syn = {}
        return self._syn

    @property
    def query_rules(self) -> Dict[str, Any]:
        if self._qr is None:
            try:
                from . import query_rules as _qr
                self._qr = _qr.load_rules()
            except Exception:
                self._qr = {}
        return self._qr


# ------------------------------------------------------------------ 검증
def _chk(out: List[Dict[str, str]], level: str, text: str, fix: str = "") -> None:
    out.append({"level": level, "text": text, "fix": fix})


def _check_name(out: List[Dict[str, str]], label: str, name: str) -> None:
    if name != name.strip():
        _chk(out, "warn", "%s '%s' 앞뒤에 공백이 있습니다 (LLM 이 헤딩에서 그대로 떠온 흔적)"
             % (label, name), "승인하면 공백을 지우고 등록합니다 — 이름 자체가 맞는지만 확인하세요")
    if len(name.strip()) < 2:
        _chk(out, "error", "%s 가 너무 짧습니다(%r) — 한 글자짜리는 아무 데나 걸립니다" % (label, name), "거절하세요")


def _check_alias_value(out: List[Dict[str, str]], alias: str, owner: str) -> None:
    a = alias.strip()
    if a and owner and a.lower() == owner.strip().lower():
        _chk(out, "error", "별칭 '%s' 이(가) 이름과 같습니다 — 등록해도 새로 찾아지는 질의가 없습니다" % a, "거절하세요")
    elif owner and a.lower().startswith(owner.strip().lower()) and len(a) > len(owner.strip()) + 2:
        _chk(out, "warn", "별칭 '%s' 이(가) '이름 + 설명' 형태입니다 — 사람이 이렇게 질의하지 않습니다"
             % a[:60], "'%s' 처럼 실제로 검색할 말만 남기세요" % owner.strip())
    if len(a) > 40 or _SENTENCEISH.search(a):
        _chk(out, "warn", "별칭이 문장/헤딩 한 줄로 보입니다 (%d자) — 별칭은 질의에 쓰는 짧은 말이어야 합니다" % len(a),
             "헤딩에서 실제 약칭만 잘라 내세요")


def _check_type(out: List[Dict[str, str]], ctx: Ctx, typ: str) -> None:
    if not typ or not ctx.types:
        return
    if typ in ctx.types:
        return
    low = {t.lower(): t for t in ctx.types}
    if typ.lower() in low:
        _chk(out, "error", "type '%s' 는 대소문자가 다릅니다 — 유효한 값은 '%s' 입니다" % (typ, low[typ.lower()]),
             "type 을 '%s' 로 고쳐 다시 제안하세요" % low[typ.lower()])
    else:
        _chk(out, "error", "type '%s' 는 이 규칙 사전에 없는 값입니다 — 등록은 되지만 관계가 하나도 생기지 않습니다" % typ,
             "쓸 수 있는 type: %s" % ", ".join(ctx.types))


def _check_required(out: List[Dict[str, str]], kind: str, pl: Dict[str, Any]) -> None:
    for group in REQUIRED.get(kind, []):
        if not any(pl.get(k) not in (None, "", [], {}) for k in group):
            _chk(out, "error", "payload 에 %s 가 없습니다 — 승인해도 적용 단계에서 실패(failed)합니다"
                 % " 또는 ".join("`%s`" % k for k in group),
                 "거절하거나, 값을 채워 새 제안으로 올리세요")


# ------------------------------------------------------------------ 종류별 설명
def _describe_kind(ctx: Ctx, kind: str, pl: Dict[str, Any], checks: List[Dict[str, str]]) -> Dict[str, Any]:
    """title · what · diff 를 만든다 (검증 결과는 checks 에 덧붙인다)."""
    d: Dict[str, Any] = {"title": "", "what": "", "diff": [], "target_detail": ""}

    if kind == "synonym":
        t, e = _s(pl.get("term")), _s(pl.get("expansion"))
        d["title"] = "동의어 추가 — '%s' 로 검색하면 '%s' 도 함께 찾습니다" % (t, e)
        d["what"] = ("앞으로 질의에 '%s' 가 들어오면 FTS 검색어에 '%s' 를 OR 로 덧붙입니다. "
                     "찾는 문서가 늘어나는 대신, 관계없는 문서도 같이 올라올 수 있습니다." % (t, e))
        cur = ctx.synonyms.get(t) or []
        d["diff"] = ["- %s: %s" % (t, json.dumps(list(cur), ensure_ascii=False) if cur else "(없음)"),
                     "+ %s: %s" % (t, json.dumps(sorted(set(list(cur) + [e])), ensure_ascii=False))]
        if e in cur:
            _chk(checks, "info", "이미 등록된 동의어입니다 — 적용해도 바뀌는 것이 없습니다", "거절해도 됩니다")
        for w, lab in ((t, "동의어 대상"), (e, "확장어")):
            _check_name(checks, lab, w)
        if e.isdigit() or re.match(r"^\d{1,4}(년|월|일|분기)?$", e):
            _chk(checks, "error", "확장어 '%s' 가 날짜/숫자입니다 — 승인하면 이 낱말이 든 모든 질의가 망가집니다" % e, "거절하세요")

    elif kind == "alias":
        ent, al = _s(pl.get("entity")), _s(pl.get("alias"))
        owner = ctx.ent_lower.get(ent.strip().lower())
        d["title"] = "별칭 추가 — 엔티티 «%s» 를 '%s' 로도 찾게 합니다" % (ent or "(대상 없음)", al)
        d["what"] = (("규칙 사전의 엔티티 «%s» 에 별칭 '%s' 를 붙입니다. 붙이면 '%s' 라고만 질의해도 "
                      "그 엔티티가 그래프 시드로 잡혀, 연결된 문서까지 함께 끌어옵니다." % (ent, al, al)) if ent else
                     ("'%s' 를 어떤 엔티티의 별칭으로 붙이겠다는 제안인데, **붙일 대상이 비어 있습니다**. "
                      "'%s' 가 가리키는 엔티티가 사전에 있으면 그 이름으로 다시 제안하고, 없으면 entity 종류로 "
                      "새로 등록해야 합니다." % (al, al)))
        if owner:
            rec = ctx.entities.get(owner) or {}
            before = list(rec.get("aliases") or [])
            d["diff"] = ['  "%s": {"type": "%s",' % (owner, rec.get("type", "")),
                         "-   aliases: %s" % json.dumps(before, ensure_ascii=False),
                         "+   aliases: %s" % json.dumps(before + ([al] if al and al not in before else []), ensure_ascii=False), "  }"]
            if al in before:
                _chk(checks, "info", "이미 있는 별칭입니다 — 적용해도 바뀌는 것이 없습니다", "거절해도 됩니다")
        elif ent:
            _chk(checks, "error", "규칙 사전에 «%s» 엔티티가 없습니다 — 적용하면 'unknown entity' 로 실패합니다" % ent,
                 "먼저 entity 제안으로 «%s» 를 만들거나, 이 제안을 entity 종류로 다시 올리세요" % ent)
            d["diff"] = ["(대상 엔티티 없음 — 적용 불가)"]
        _check_alias_value(checks, al, owner or ent)

    elif kind == "entity":
        nm, typ = _s(pl.get("name")), _s(pl.get("type") or "concept")
        als = [_s(a) for a in (pl.get("aliases") or [])]
        d["title"] = "엔티티 추가 — «%s» (type=%s)%s" % (nm.strip(), typ, (" · 별칭 %d개" % len(als)) if als else "")
        d["what"] = ("규칙 사전에 «%s» 를 새 엔티티로 등록합니다. 등록하면 빌드할 때 문서에서 이 이름(과 별칭)을 찾아 "
                     "멘션으로 이어 붙이고, 관련 문서끼리 그래프로 연결됩니다." % nm.strip())
        exist = ctx.ent_lower.get(nm.strip().lower())
        rec = {"type": typ, "aliases": als}
        d["diff"] = ["- (사전에 없음)" if not exist else "- \"%s\": %s" % (exist, json.dumps(ctx.entities.get(exist), ensure_ascii=False)),
                     "+ \"%s\": %s" % (nm.strip(), json.dumps(rec, ensure_ascii=False))]
        if exist:
            _chk(checks, "info", "이미 «%s» 로 등록돼 있습니다 — 적용해도 덮어쓰지 않습니다(무효)" % exist,
                 "별칭만 추가하려면 alias 종류로 올리세요")
        _check_name(checks, "엔티티 이름", nm)
        _check_type(checks, ctx, typ)
        for a in als:
            _check_alias_value(checks, a, nm)

    elif kind == "relation":
        src, dst = _s(pl.get("src")), _s(pl.get("dst"))
        rel = _s(pl.get("rel") or "related_to")
        d["title"] = "관계 추가 — «%s» --%s--> «%s»" % (src, rel, dst)
        d["what"] = ("그래프에 «%s» 와 «%s» 사이의 '%s' 관계를 직접 넣습니다. 한쪽이 검색에 잡히면 다른 쪽 문서까지 "
                     "따라 올라옵니다(그래프 확장). 문서 근거 없이 넣는 관계이므로 틀리면 엉뚱한 문서가 섞입니다."
                     % (src, dst, rel))
        d["diff"] = ["+ %s --[%s w=%s]--> %s   (source=evolve)"
                     % (src, rel, pl.get("weight", 0.8), dst),
                     "  설명: %s" % (_s(pl.get("description")) or "(없음)")]
        for nm, lab in ((src, "src"), (dst, "dst")):
            _check_name(checks, lab, nm)
            if nm and nm.strip().lower() not in ctx.ent_lower:
                _chk(checks, "warn", "«%s» 는 규칙 사전에 없는 이름입니다 — 적용 시 concept 엔티티로 **자동 생성**됩니다" % nm,
                     "의도한 이름이 맞는지 확인하세요")
        if src.strip().lower() == dst.strip().lower():
            _chk(checks, "error", "src 와 dst 가 같습니다 (자기 자신 관계)", "거절하세요")

    elif kind == "wiki_note":
        page, note = _s(pl.get("page")), _s(pl.get("note"))
        d["title"] = "위키 노트 추가 — %s.md 에 메모 %d자" % (page, len(note))
        d["what"] = ("wiki/%s.md 끝의 편집 노트 블록에 아래 내용을 덧붙입니다. 다음 빌드에서 overlay 문서로 색인되어 "
                     "검색·인용 대상이 됩니다. 원본 코퍼스 문서는 건드리지 않습니다." % page)
        d["diff"] = ["+ " + l for l in note.splitlines()[:8]] + (["+ …"] if len(note.splitlines()) > 8 else [])
        if page == "_gaps":
            _chk(checks, "info", "'_gaps' 는 '답을 못 찾았다' 를 모아 두는 페이지입니다 — 지식이 늘지는 않습니다",
                 "여기에 쌓인 주제로 실제 문서를 보강하는 것이 본 목적입니다")
        if len(note.strip()) < 10:
            _chk(checks, "warn", "노트 내용이 거의 없습니다", "거절해도 됩니다")

    elif kind == "query_rule":
        typ, term = _s(pl.get("type") or "synonym"), _s(pl.get("term"))
        vals = [_s(v) for v in (pl.get("values") or ([pl["value"]] if pl.get("value") else []))]
        d["title"] = "질의 규칙 추가 — [%s] '%s' → %s" % (typ, term, ", ".join(vals) or "(값 없음)")
        d["what"] = ("query_rules.json 의 %s 절에 '%s' 항목을 넣습니다. 질의에 '%s' 가 보이면 규칙 종류가 정한 대로 "
                     "질의어를 넓히거나(동의어·관련어) 좁힙니다(exclude)." % (typ, term, term))
        try:
            cur = ((ctx.query_rules.get(typ) or {}).get(term)) or []
        except Exception:
            cur = []
        d["diff"] = ["- %s.%s = %s" % (typ, term, json.dumps(list(cur), ensure_ascii=False) if cur else "(없음)"),
                     "+ %s.%s = %s" % (typ, term, json.dumps(sorted(set(list(cur) + vals)), ensure_ascii=False))]
        try:
            from . import query_rules as _qr
            if typ not in _qr.RULE_TYPES:
                _chk(checks, "error", "'%s' 는 없는 규칙 종류입니다" % typ, "가능: %s" % ", ".join(sorted(_qr.RULE_TYPES)))
        except Exception:
            pass
        for v in vals:
            if v.strip().lower() == term.strip().lower():
                _chk(checks, "error", "값이 용어와 같습니다 — 확장 효과가 없습니다", "거절하세요")
            if len(v.strip()) < 2:
                _chk(checks, "warn", "값 '%s' 가 너무 짧습니다" % v, "검색이 넓어져 정확도가 떨어질 수 있습니다")

    elif kind == "pin":
        doc, ch = _s(pl.get("doc")), _s(pl.get("chunk"))
        kw = [_s(k) for k in (pl.get("keywords") or [])]
        cond = ("키워드 %s 가 모두 들어간 질의" % ", ".join("'%s'" % k for k in kw)) if kw else \
               ("질의 '%s'" % _s(pl.get("query")) if pl.get("query") else "모든 질의(always)")
        d["title"] = "고정 근거 추가 — %s 일 때 %s 를 끌어올립니다" % (cond, doc or ch)
        d["what"] = ("pins.json 에 규칙을 넣습니다. 조건이 맞는 질의에서 이 문서/청크의 점수에 가중치 %s 를 더해 "
                     "상위로 올립니다. 검색이 못 찾던 '정답 문서' 를 사람이 직접 지목하는 장치입니다."
                     % pl.get("weight", 1.0))
        d["diff"] = ["+ pin: %s" % json.dumps({k: v for k, v in pl.items() if k in
                                               ("doc", "chunk", "query", "keywords", "always", "doc_types", "weight")},
                                              ensure_ascii=False)]
        if pl.get("always"):
            _chk(checks, "warn", "always=true 는 **모든 질의**에 이 문서를 끌어올립니다", "보통은 keywords 로 조건을 거세요")
        if doc and ctx.pipe is not None:
            try:
                if not ctx.pipe.store.doc_meta_map().get(doc) and not any(
                        doc in (k or "") for k in ctx.pipe.store.doc_meta_map()):
                    _chk(checks, "warn", "색인에서 문서 '%s' 를 찾지 못했습니다 (경로 표기가 다를 수 있음)" % doc,
                         "`docs` 로 정확한 경로를 확인하세요")
            except Exception:
                pass

    elif kind == "tuning":
        key, val = _s(pl.get("key")), pl.get("value")
        sp, cur = {}, None
        try:
            from . import tuning as _tn
            sp = _tn.spec(key) or {}
            cur = _tn.T.get(key) if hasattr(_tn.T, "get") else None
        except Exception:
            pass
        if cur is None and ctx.pipe is not None:
            cur = getattr(ctx.pipe.s, key, None)
        d["title"] = "튜닝 변경 — %s: %s → %s" % (key, json.dumps(cur, ensure_ascii=False, default=str),
                                                json.dumps(val, ensure_ascii=False, default=str))
        d["what"] = ((sp.get("desc") or "튜닝 값 %s 를 바꿉니다." % key) +
                     (" 영향: " + sp["impact"] if sp.get("impact") else "") +
                     (" (스테이지: %s)" % sp["stage"] if sp.get("stage") else ""))
        d["diff"] = ["- %s = %s" % (key, json.dumps(cur, ensure_ascii=False, default=str)),
                     "+ %s = %s" % (key, json.dumps(val, ensure_ascii=False, default=str))]
        if not sp:
            _chk(checks, "error", "'%s' 는 알려진 튜닝 키가 아닙니다 — 적용 시 실패합니다" % key,
                 "`tuning list` 로 키 이름을 확인하세요")
        elif cur is not None and cur == val:
            _chk(checks, "info", "현재 값과 같습니다 — 바뀌는 것이 없습니다", "거절해도 됩니다")
        if sp.get("source") == "config":
            _chk(checks, "info", "이 키는 config.json 항목입니다 — 승인하면 config.json 이 저장됩니다", "")

    elif kind == "chunk_params":
        parts = []
        for k in ("chunk_max_chars", "chunk_overlap_chars"):
            if k in pl:
                cur = getattr(ctx.pipe.s, k, None) if ctx.pipe is not None else None
                parts.append((k, cur, pl[k]))
        d["title"] = "청킹 변경 — " + ", ".join("%s %s→%s" % (k, c, n) for k, c, n in parts)
        d["what"] = ("문서를 자르는 크기/겹침을 바꾸고 **전체 리빌드**를 돌립니다. 청크가 커지면 문맥이 넉넉해지지만 "
                     "검색 정밀도와 토큰 비용이 올라가고, 작아지면 그 반대입니다. 코퍼스 전체가 다시 임베딩됩니다.")
        d["diff"] = ["- %s = %s" % (k, c) for k, c, _n in parts] + ["+ %s = %s" % (k, n) for k, _c, n in parts]
        for k, c, n in parts:
            try:
                if int(n) <= 0:
                    _chk(checks, "error", "%s 가 0 이하입니다" % k, "거절하세요")
                elif c and abs(int(n) - int(c)) / max(1, int(c)) > 0.5:
                    _chk(checks, "warn", "%s 를 %d%% 바꿉니다 — 검색 결과가 크게 달라질 수 있습니다"
                         % (k, round((int(n) - int(c)) * 100 / max(1, int(c)))), "먼저 sweep 으로 비교해 보세요")
            except Exception:
                pass

    elif kind == "corpus_gap":
        topic = _s(pl.get("topic"))
        d["title"] = "문서 없음 — '%s' 를 다루는 자료가 코퍼스에 없습니다" % topic
        d["what"] = ("이 제안은 **적용할 수 없습니다**(승인을 눌러도 failed 로 기록됩니다). 규칙을 고쳐서 될 일이 아니라, "
                     "'%s' 를 설명하는 문서를 코퍼스에 넣어야 해결됩니다. 문서를 넣고 빌드한 뒤 이 제안을 거절하세요." % topic)
        d["diff"] = ["(해야 할 일) corpus/ 에 '%s' 문서 추가 → `build` → 이 제안 거절" % topic]
    else:
        d["title"] = "%s 제안" % kind
        d["what"] = "알 수 없는 종류입니다 — 적용 시 'unknown kind' 로 실패합니다."
        _chk(checks, "error", "알 수 없는 종류 '%s'" % kind, "거절하세요")
    return d


# ------------------------------------------------------------------ 진입점
def describe(p: Dict[str, Any], pipe: Any = None, ctx: Optional[Ctx] = None) -> Dict[str, Any]:
    """제안 한 건 → 설명 dict. `p` 는 store.proposals()/get_proposal() 의 행."""
    from . import evolve as _ev
    ctx = ctx or Ctx(pipe)
    kind = _s(p.get("kind"))
    pl = p.get("payload")
    if isinstance(pl, str):
        try:
            pl = json.loads(pl)
        except ValueError:
            pl = {}
    pl = pl if isinstance(pl, dict) else {}
    checks: List[Dict[str, str]] = []
    _check_required(checks, kind, pl)
    body = _describe_kind(ctx, kind, pl, checks)

    imp = dict(KIND_IMPACT.get(kind) or {"target": "?", "rebuild": "none", "risk": "medium", "scope": "?", "revert": "?"})
    rb = imp.get("rebuild", "none")
    conf = float(p.get("confidence") or 0)
    if conf < 0.4:
        _chk(checks, "info", "신뢰도 %.2f — 자동 수집한 약한 근거입니다" % conf, "근거 질의를 보고 판단하세요")
    origin = _s(p.get("origin"))
    queries = [_s(q) for q in (pl.get("queries") or [])][:5]
    events = pl.get("events")

    errs = [c for c in checks if c["level"] == "error"]
    out = {
        "id": p.get("id"), "kind": kind, "kind_label": _ev.KINDS.get(kind, kind),
        "title": body["title"], "what": body["what"],
        "why": _s(p.get("reason")),
        "origin": origin, "origin_text": ORIGIN_TEXT.get(origin, origin),
        "confidence": conf, "strength": p.get("strength"), "status": _s(p.get("status")),
        "target": imp["target"], "diff": body["diff"],
        "impact": {"rebuild": rb, "rebuild_text": REBUILD_TEXT.get(rb, rb), "scope": imp["scope"],
                   "risk": imp["risk"], "revert": imp["revert"],
                   "auto_apply": kind in (_ev.auto_apply_kinds(pipe.s) if pipe is not None else _ev.AUTO_APPLY_KINDS_DEFAULT)},
        "checks": checks, "applicable": not errs and kind != "corpus_gap",
        "queries": queries, "events": events,
        "commands": {"apply": "python -m llmwiki evolve apply %s" % p.get("id"),
                     "reject": "python -m llmwiki evolve reject %s \"<사유>\"" % p.get("id"),
                     "show": "python -m llmwiki evolve show %s" % p.get("id")},
        "payload": pl,
    }
    return out


def describe_many(rows: List[Dict[str, Any]], pipe: Any = None) -> List[Dict[str, Any]]:
    ctx = Ctx(pipe)
    return [describe(r, pipe, ctx) for r in rows]


_LEVEL_MARK = {"error": "[X]", "warn": "[!]", "info": "[i]"}


def format_description(d: Dict[str, Any], width: int = 100) -> str:
    """CLI 용 텍스트 렌더링 (`evolve show`)."""
    L: List[str] = []
    L.append("#%s  %s" % (d.get("id"), d.get("title")))
    L.append("-" * min(width, max(40, len(d.get("title") or "") + 8)))
    L.append("종류    : %s — %s" % (d.get("kind"), d.get("kind_label")))
    L.append("출처    : %s (신뢰도 %.2f%s)" % (d.get("origin_text") or d.get("origin"), d.get("confidence") or 0,
                                              "" if d.get("strength") is None else ", 강도 %.2f" % float(d["strength"])))
    L.append("")
    L.append("무엇이 바뀌나")
    for line in _wrap(d.get("what") or "", width - 2):
        L.append("  " + line)
    L.append("")
    L.append("바뀌는 곳: %s" % d.get("target"))
    for ln in (d.get("diff") or []):
        L.append("  " + ln)
    imp = d.get("impact") or {}
    L.append("")
    L.append("영향")
    L.append("  리빌드  : %s" % imp.get("rebuild_text"))
    L.append("  범위    : %s" % imp.get("scope"))
    L.append("  위험도  : %s%s" % (imp.get("risk"), " · 자동 적용 대상 종류" if imp.get("auto_apply") else ""))
    L.append("  되돌리기: %s" % imp.get("revert"))
    if d.get("why"):
        L.append("")
        L.append("제안 근거")
        for line in _wrap(d["why"], width - 2):
            L.append("  " + line)
    if d.get("queries"):
        L.append("  관련 질의: " + " / ".join(d["queries"]))
    if d.get("events"):
        L.append("  반복 관측: %s회" % d["events"])
    checks = d.get("checks") or []
    if checks:
        L.append("")
        L.append("점검")
        for c in checks:
            L.append("  %s %s" % (_LEVEL_MARK.get(c["level"], "   "), c["text"]))
            if c.get("fix"):
                L.append("      → %s" % c["fix"])
    L.append("")
    L.append("판정    : %s" % ("적용 가능" if d.get("applicable") else
                               "**이대로 승인하면 실패하거나 효과가 없습니다**"))
    L.append("명령    : %s   |   %s" % (d["commands"]["apply"], d["commands"]["reject"]))
    return "\n".join(L)


def _wrap(text: str, width: int) -> List[str]:
    out, cur = [], ""
    for word in str(text or "").split():
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    if cur:
        out.append(cur)
    return out or [""]
