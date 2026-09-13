# -*- coding: utf-8 -*-
"""근거 충분성 판정 + 단계적 fallback 루프 정의.

assess()      : 휴리스틱 — 상위 융합 점수 · 채널 합의 · 질의 키워드 커버리지 · 컨텍스트 글자수 · ID 매칭 → sufficient | weak | insufficient
assess_llm()  : LLM(verify 역할) 판정 — {"verdict","missing","useful","followup_queries"}
FALLBACK_LEVELS: 값싼 단계부터. 각 단계는 retrieve 라운드 설정(RoundConfig) 을 어떻게 바꾸는지 기술한다.
Budget        : attempts / tokens / latency 상한 (over-retrieval 방지).
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from .providers import BaseLLM, LLMError, parse_json
from .textutil import keywords, normalize_token, words
from . import prompts as _prompts

_ID_RE = re.compile(r"\b(?:ISSUE|CL|TC|SWD|HWD|RULE|WR)[-_ ]?[A-Z0-9][A-Z0-9_-]*\b", re.I)


def assess(query: str, final: List[Any], ctx: Dict[str, Any], chunks: Dict[str, Any], T: Any) -> Dict[str, Any]:
    """휴리스틱 충분성 판정. 반환: verdict, score(0~1), signals, reasons."""
    kws = keywords(query)
    ctx_text = (ctx.get("text") or "").lower()
    toks = set(normalize_token(w) for w in words(ctx_text))
    cover = (sum(1 for k in kws if k in toks or k in ctx_text) / float(len(kws))) if kws else 1.0
    top = float(final[0].fused) if final else 0.0
    n_ch = len(final[0].ranks) if final else 0
    ids = [m.group(0).upper() for m in _ID_RE.finditer(query)]
    id_hit = all(i.lower().replace(" ", "-") in ctx_text or i.lower() in ctx_text for i in ids) if ids else True
    chars = int(ctx.get("chars") or 0)
    reasons: List[str] = []
    verdict = "sufficient"
    # insufficient: 근거 자체가 없거나, 질의 키워드 대부분이 근거에 없거나, 질의의 문서 ID 가 근거에 없음
    if not final or chars == 0:
        verdict = "insufficient"
        reasons.append("no evidence")
    if kws and cover < float(T.get("evidence_min_cover")):
        verdict = "insufficient"
        reasons.append("keyword coverage %.2f < %.2f" % (cover, T.get("evidence_min_cover")))
    if ids and not id_hit:
        verdict = "insufficient"
        reasons.append("document id %s not in evidence" % ids)
    # weak: 근거는 있으나 짧거나(min_chars) 일부 키워드 누락, 융합 점수 낮음, 채널 합의 부족
    if verdict == "sufficient":
        if chars < int(T.get("evidence_min_chars")):
            verdict = "weak"
            reasons.append("context short (%d chars)" % chars)
        if kws and cover < 1.0:
            verdict = "weak"
            reasons.append("keyword coverage %.2f" % cover)
        if top < float(T.get("evidence_min_score")):
            verdict = "weak"
            reasons.append("top fused %.4f < %.4f" % (top, T.get("evidence_min_score")))
        if n_ch < int(T.get("evidence_min_channels")):
            verdict = "weak"
            reasons.append("top hit from %d channel(s)" % n_ch)
    score = round(min(1.0, 0.5 * cover + 0.3 * min(1.0, top / max(1e-9, float(T.get("evidence_min_score")))) + 0.2 * (1.0 if id_hit else 0.0)), 3)
    return {"verdict": verdict, "score": score, "reasons": reasons,
            "signals": {"top_fused": round(top, 4), "channels": n_ch, "cover": round(cover, 3), "chars": chars, "n_hits": len(final),
                        "ids": ids, "id_hit": id_hit, "keywords": kws[:10]}}


def assess_llm(llm: BaseLLM, query: str, ctx: Dict[str, Any], effort: str = "low") -> Dict[str, Any]:
    user = "## 질문\n%s\n\n## 근거\n%s" % (query, (ctx.get("text") or "")[:12000])
    r = llm.complete(_prompts.get("evidence_check"), user, max_tokens=500, effort=effort, json_mode=True)
    data = parse_json(r["text"]) or {}
    v = str(data.get("verdict") or "").lower()
    if v not in ("sufficient", "weak", "insufficient"):
        v = "weak"
    return {"verdict": v, "missing": [str(x) for x in (data.get("missing") or [])][:6],
            "useful": [x for x in (data.get("useful") or []) if isinstance(x, int)][:12],
            "followup_queries": [str(x) for x in (data.get("followup_queries") or []) if str(x).strip()][:4],
            "usage": r.get("usage"), "raw": r["text"][:800]}


def merge_verdicts(heur: Dict[str, Any], llm: Optional[Dict[str, Any]]) -> str:
    order = {"sufficient": 2, "weak": 1, "insufficient": 0}
    if not llm:
        return heur["verdict"]
    # 더 보수적인 판정을 채택 (LLM 이 insufficient 라면 신뢰)
    return min(heur["verdict"], llm["verdict"], key=lambda v: order[v])


# ---------------------------------------------------------------- fallback
FALLBACK_LEVELS: Dict[str, Dict[str, Any]] = {
    "rules": {"desc": "규칙 확장 강화: related 가중↑, FTS or 모드, top_k×widen, bigram/trigram 폴백", "cost": "0 LLM"},
    "expand": {"desc": "LLM 질의 확장/분해 (아직 안 했으면) + LLM 판정의 followup 질의 추가", "cost": "1 LLM"},
    "graph": {"desc": "그래프 hops+1, 인접 청크 확대, doc_refs 문서 후보↑", "cost": "0 LLM"},
    "wide": {"desc": "광역: top_k×widen², 시간 filter→boost, 문서유형 부스트 해제, exclude 완화", "cost": "0 LLM"},
    "mcp": {"desc": "외부 MCP 소스 enrich (mcp_sources 토글 필요)", "cost": "MCP 호출"},
}


def plan_levels(levels_str: str, toggles: Any) -> List[str]:
    out: List[str] = []
    for lv in [x.strip() for x in str(levels_str or "").split(",") if x.strip()]:
        if lv not in FALLBACK_LEVELS:
            continue
        if lv == "mcp" and not getattr(toggles, "mcp_sources", False):
            continue
        out.append(lv)
    return out


class Budget:
    def __init__(self, attempts: int, tokens: int, latency_ms: int, t0: float, tokens_used_fn):
        self.attempts, self.tokens, self.latency_ms, self.t0 = int(attempts), int(tokens), int(latency_ms), t0
        self.used_attempts = 0
        self.tokens_used_fn = tokens_used_fn
        self.stop_reason = ""

    def allow(self) -> bool:
        if self.used_attempts >= self.attempts:
            self.stop_reason = "max attempts %d" % self.attempts
            return False
        if self.latency_ms and (time.perf_counter() - self.t0) * 1000 > self.latency_ms:
            self.stop_reason = "latency budget %dms" % self.latency_ms
            return False
        if self.tokens and self.tokens_used_fn() > self.tokens:
            self.stop_reason = "token budget %d" % self.tokens
            return False
        return True

    def spend(self) -> None:
        self.used_attempts += 1


def insufficient_text(query: str, ev: Dict[str, Any], llm_ev: Optional[Dict[str, Any]], rounds: List[Dict[str, Any]], hits: List[Dict[str, Any]]) -> str:
    lines = ["**근거 부족 (insufficient data)** — 제공된 문서만으로는 질문에 확신 있게 답할 수 없습니다.", ""]
    lines.append("## 찾은 것")
    if hits:
        for h in hits[:5]:
            lines.append("- [C%s] %s — %s" % (h.get("n") or "?", h.get("doc_id"), (h.get("heading") or "")[:60]))
    else:
        lines.append("- 관련 근거 문단을 찾지 못했습니다.")
    lines.append("")
    lines.append("## 부족한 것")
    for r in ev.get("reasons", []):
        lines.append("- " + r)
    for m in (llm_ev or {}).get("missing", []):
        lines.append("- " + m)
    if rounds:
        lines.append("")
        lines.append("## 시도한 확장 검색")
        for r in rounds:
            lines.append("- %s → %s (hits %s)" % (r.get("level"), r.get("verdict"), r.get("n_hits")))
    lines.append("")
    lines.append("## 제안")
    lines.append("- 질문에 문서 ID(ISSUE-/CL-)나 모듈명·레지스터명을 포함하거나, 관련 문서를 코퍼스에 추가하세요.")
    lines.append("- `forensic last` 로 단계별 원인을 확인할 수 있습니다.")
    return "\n".join(lines)
