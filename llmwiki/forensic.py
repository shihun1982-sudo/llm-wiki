# -*- coding: utf-8 -*-
"""포렌식 — 왜 답변이 부실했는지 trace 를 단계별 진단 규칙으로 분석하고 누적한다.

diagnose(trace, result) → findings[{stage, problem, evidence, severity}] + suggestions[{kind, detail, confidence, payload}] + topics
record()  → forensics 테이블 (request_id/run_id 로 프로파일·로그와 연결)
summary() → 누적 집계 (주제별 insufficient 횟수, 제안 종류) → evolve 가 corpus_gap/query_rule/tuning 제안으로 변환
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from .profiler import flatten_trace
from .textutil import keywords


def _stage(flat: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for f in flat:
        if f["name"] == name:
            return f
    return None


def _stages(flat: List[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    return [f for f in flat if f["name"] == name]


def diagnose(trace: Dict[str, Any], result: Dict[str, Any], settings: Any = None) -> Dict[str, Any]:
    flat = flatten_trace(trace)
    findings: List[Dict[str, Any]] = []
    suggestions: List[Dict[str, Any]] = []
    q = result.get("query") or ""
    kws = keywords(q)
    topics = kws[:5]

    def add(stage: str, problem: str, evidence: Any, severity: str = "warn") -> None:
        findings.append({"stage": stage, "problem": problem, "evidence": evidence if isinstance(evidence, str) else json.dumps(evidence, ensure_ascii=False)[:300], "severity": severity})

    def suggest(kind: str, detail: str, confidence: float, payload: Optional[Dict[str, Any]] = None) -> None:
        suggestions.append({"kind": kind, "detail": detail, "confidence": confidence, "payload": payload or {}})

    # ---- 검색 채널 ----
    fts = _stage(flat, "fts_search")
    if fts and fts["enabled"]:
        m = fts["meta"]
        if m.get("hits", 0) == 0:
            add("fts_search", "FTS 0건 — 질의 용어가 코퍼스에 없거나 표기가 다름", {"keywords": m.get("keywords") or kws, "tiers": m.get("tiers")}, "error")
            for k in (m.get("keywords") or kws)[:3]:
                suggest("query_rule", "'%s' 의 동의어/약어/별칭을 query_rules.json 에 추가 검토" % k, 0.5, {"type": "synonym", "term": k})
            suggest("corpus_gap", "주제 '%s' 문서가 코퍼스에 없을 수 있음" % " ".join(kws[:3]), 0.5, {"topic": " ".join(kws[:3])})
        elif "AND:0" in (m.get("tiers") or []) and m.get("hits", 0) < 3:
            add("fts_search", "모든 키워드를 포함한 문단 없음 (AND 0건) — 다중 주제 질문이거나 표기 불일치", m.get("tiers"))
            suggest("tuning", "fts_mode=or 또는 query_decompose 로 다중 주제 질문 분해", 0.4, {"key": "fts_mode", "value": "or"})
    vec = _stage(flat, "vector_search")
    if vec and vec["enabled"]:
        m = vec["meta"]
        if m.get("hits", 0) == 0:
            add("vector_search", "벡터 0건 — 임베딩 없음/차원 불일치/유사도 임계", m.get("reason") or m, "error")
            suggest("tuning", "embed coverage 확인(embed report), vector_min_sim 낮추기", 0.6, {"key": "vector_min_sim"})
        else:
            top = (m.get("top") or [[None, 0]])[0]
            if isinstance(top, (list, tuple)) and len(top) > 1 and float(top[1] or 0) < 0.15 and m.get("provider") != "hash":
                add("vector_search", "벡터 최고 유사도가 낮음 (%.3f) — 의미상 가까운 문서 없음" % float(top[1]), m.get("top"))
    gr = _stage(flat, "graph_search")
    if gr and gr["enabled"] and gr["meta"].get("hits", 0) == 0:
        reason = gr["meta"].get("reason", "")
        add("graph_search", "그래프 0건 — %s" % (reason or "시드 엔티티 없음"), gr["meta"])
        if "no entity" in reason:
            for k in kws[:2]:
                suggest("alias", "'%s' 를 엔티티/별칭으로 등록 (data/rules.json)" % k, 0.45, {"alias": k})
    # ---- 확장 ----
    qr = _stage(flat, "query_rules")
    if qr and qr["enabled"] and not (qr["meta"].get("fired") or 0):
        add("query_rules", "규칙 확장 미발화 — 사전에 없는 용어", {"keywords": kws}, "info")
    ex = _stage(flat, "query_expand")
    if ex and not ex["enabled"] and "unavailable" in str(ex["meta"].get("reason", "")):
        add("query_expand", "LLM 확장 불가 (프로바이더 없음)", ex["meta"].get("reason"), "info")
    # ---- 융합/리랭크/컨텍스트 ----
    fu = _stage(flat, "rrf_fuse") or _stage(flat, "fusion")
    if fu and fu["enabled"] and fu["meta"].get("candidates", 0) <= 2:
        add("fusion", "융합 후보가 매우 적음 (%s)" % fu["meta"].get("candidates"), fu["meta"].get("overlap"))
    ctx = _stage(flat, "context")
    if ctx and ctx["enabled"]:
        m = ctx["meta"]
        if m.get("saved_chars", 0) > 0 and m.get("chars", 0) >= 0.95 * (getattr(settings, "context_max_chars", 9000) if settings else 9000):
            add("context", "컨텍스트 상한에 도달해 근거가 잘림", {"chars": m.get("chars"), "saved_chars": m.get("saved_chars")})
            suggest("tuning", "context_max_chars 확대 또는 top_k_final 축소", 0.5, {"key": "context_max_chars"})
        if m.get("citations", 0) == 0:
            add("context", "컨텍스트에 근거 문단 0개", m, "error")
    # ---- 근거 판정 / fallback ----
    ev = _stage(flat, "evidence_check")
    if ev and ev["enabled"]:
        m = ev["meta"]
        if m.get("verdict") in ("weak", "insufficient"):
            add("evidence_check", "근거 판정 %s: %s" % (m.get("verdict"), "; ".join(m.get("reasons") or [])), m.get("signals"), "warn" if m.get("verdict") == "weak" else "error")
            if m.get("missing"):
                suggest("corpus_gap", "부족한 정보: %s" % "; ".join(m["missing"][:3]), 0.6, {"topic": " ".join(kws[:3]), "missing": m["missing"][:3]})
            elif m.get("verdict") == "insufficient":
                sig = m.get("signals") or {}
                suggest("corpus_gap", "주제 '%s' 에 대한 근거 문서 없음 (coverage %s)" % (" ".join(kws[:3]), sig.get("cover")), 0.55, {"topic": " ".join(kws[:3])})
    fb = _stages(flat, "fallback")
    if fb:
        last = fb[-1]["meta"]
        if last.get("stop_reason"):
            add("fallback", "fallback 루프 종료: %s (라운드 %d)" % (last.get("stop_reason"), len(fb)), [f["meta"].get("level") for f in fb], "info")
        if all(f["meta"].get("verdict") != "sufficient" for f in fb):
            suggest("corpus_gap", "확장 검색 %d회 후에도 근거 부족 — 주제 '%s' 문서 추가 필요" % (len(fb), " ".join(kws[:3])), 0.7, {"topic": " ".join(kws[:3])})
    # ---- 답변 / claim ----
    an = _stage(flat, "answer_llm")
    if an and an["enabled"]:
        m = an["meta"]
        out_tok = (m.get("usage") or {}).get("output_tokens") or 0
        mx = m.get("max_tokens") or 0
        if mx and out_tok >= 0.95 * mx:
            add("answer_llm", "답변이 max_tokens 에 도달해 잘렸을 가능성", {"output_tokens": out_tok, "max_tokens": mx})
            suggest("tuning", "answer_max_tokens 확대", 0.7, {"key": "answer_max_tokens"})
        ac, cc = m.get("answer_chars") or 0, m.get("context_chars") or 0
        if cc and ac < 0.05 * cc and ac < 300:
            add("answer_llm", "답변이 근거 대비 매우 짧음 (%d자 / 근거 %d자)" % (ac, cc), {"length_target": m.get("length_target")}, "info")
            suggest("tuning", "answer_length_target=long 또는 answer_guide.md 상세도 지시 확인", 0.4, {"key": "answer_length_target", "value": "long"})
        if cc and ac > 1.5 * cc and ac > 2000:
            add("answer_llm", "답변이 근거보다 훨씬 김 — 근거 밖 서술/반복 의심", {"answer_chars": ac, "context_chars": cc})
        if not m.get("cited"):
            add("answer_llm", "답변에 인용 [C#] 없음", m.get("cited"), "error")
    elif an and not an["enabled"]:
        add("answer_llm", "LLM 답변 생략 (%s) → 추출식" % an["meta"].get("reason"), "", "info")
    cl = _stage(flat, "claim_check")
    if cl and cl["enabled"]:
        m = cl["meta"]
        if m.get("unsupported", 0):
            add("claim_check", "근거가 지지하지 않는 문장 %d개 (groundedness %.2f)" % (m.get("unsupported"), m.get("groundedness") or 0), m.get("unsupported_samples"),
                "error" if (m.get("groundedness") or 0) < 0.5 else "warn")
            if (m.get("groundedness") or 0) < 0.5:
                suggest("tuning", "claim_policy=drop 또는 answer_refine 활성화, answer_guide 강화", 0.5, {"key": "claim_policy", "value": "drop"})
    if not findings:
        findings.append({"stage": "-", "problem": "특이 소견 없음", "evidence": "", "severity": "info"})
    sev = "error" if any(f["severity"] == "error" for f in findings) else ("warn" if any(f["severity"] == "warn" for f in findings) else "info")
    return {"findings": findings, "suggestions": suggestions, "topics": topics, "severity": sev}


def record(store, request_id: Optional[int], run_id: str, query: str, verdict: str, groundedness: Optional[float], diag: Dict[str, Any], origin: str = "auto") -> int:
    cur = store.conn.execute("INSERT INTO forensics(ts,request_id,run_id,query,verdict,groundedness,findings,suggestions,topics,origin) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (time.time(), request_id, run_id or "", query, verdict, groundedness, json.dumps(diag["findings"], ensure_ascii=False),
                              json.dumps(diag["suggestions"], ensure_ascii=False), json.dumps(diag["topics"], ensure_ascii=False), origin))
    store.conn.commit()
    return int(cur.lastrowid)


def list_forensics(store, limit: int = 50) -> List[Dict[str, Any]]:
    out = []
    for r in store.conn.execute("SELECT * FROM forensics ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        for k in ("findings", "suggestions", "topics"):
            try:
                d[k] = json.loads(d[k] or "[]")
            except Exception:
                d[k] = []
        out.append(d)
    return out


def get_forensic(store, request_id: int) -> Optional[Dict[str, Any]]:
    rows = [r for r in list_forensics(store, 5000) if r["request_id"] == request_id]
    return rows[0] if rows else None


def summary(store, limit: int = 500) -> Dict[str, Any]:
    rows = list_forensics(store, limit)
    by_verdict: Dict[str, int] = {}
    topics: Dict[str, int] = {}
    kinds: Dict[str, int] = {}
    stages: Dict[str, int] = {}
    for r in rows:
        by_verdict[r["verdict"] or "?"] = by_verdict.get(r["verdict"] or "?", 0) + 1
        for t in r["topics"]:
            topics[t] = topics.get(t, 0) + 1
        for s in r["suggestions"]:
            kinds[s.get("kind", "?")] = kinds.get(s.get("kind", "?"), 0) + 1
        for f in r["findings"]:
            if f.get("severity") in ("warn", "error"):
                stages[f.get("stage", "?")] = stages.get(f.get("stage", "?"), 0) + 1
    return {"n": len(rows), "by_verdict": by_verdict, "top_topics": sorted(topics.items(), key=lambda kv: -kv[1])[:20],
            "suggestion_kinds": kinds, "problem_stages": sorted(stages.items(), key=lambda kv: -kv[1])}


def llm_forensic(llm, query: str, trace: Dict[str, Any], diag: Dict[str, Any], effort: str = "low") -> Optional[Dict[str, Any]]:
    """옵션: LLM 이 프로파일 요약을 보고 추가 소견/제안 (forensic 역할)."""
    from .providers import parse_json, LLMError
    from . import prompts as _prompts
    flat = flatten_trace(trace)
    lines = ["## 질문", query, "", "## 단계 요약"]
    for f in flat:
        if f["depth"] == 1:
            meta = {k: v for k, v in (f["meta"] or {}).items() if isinstance(v, (int, float, str, bool))}
            lines.append("- %s %s %.0fms %s" % (f["name"], "" if f["enabled"] else "(skipped)", f["ms"], json.dumps(meta, ensure_ascii=False)[:300]))
    lines += ["", "## 휴리스틱 소견", json.dumps(diag["findings"], ensure_ascii=False)[:2000]]
    try:
        r = llm.complete(_prompts.get("forensic"), "\n".join(lines), max_tokens=1200, effort=effort, json_mode=True)
    except LLMError:
        return None
    data = parse_json(r["text"]) or {}
    return {"findings": data.get("findings") or [], "suggestions": data.get("suggestions") or [], "usage": r.get("usage")}


def format_forensic(row: Dict[str, Any]) -> str:
    lines = ["forensic #%s request=%s run=%s verdict=%s groundedness=%s" % (row.get("id"), row.get("request_id"), row.get("run_id"), row.get("verdict"), row.get("groundedness")),
             "Q: %s" % row.get("query"), "findings:"]
    for f in row.get("findings", []):
        lines.append("  [%s] %-14s %s  %s" % (f.get("severity"), f.get("stage"), f.get("problem"), ("← " + str(f.get("evidence"))[:120]) if f.get("evidence") else ""))
    lines.append("suggestions:")
    for s in row.get("suggestions", []):
        lines.append("  - (%s %.2f) %s" % (s.get("kind"), float(s.get("confidence") or 0), s.get("detail")))
    return "\n".join(lines)
