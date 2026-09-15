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


# =====================================================================
# 기대 결과 포렌식 (forensic expect) — "내가 기대한 문서/용어가 왜 답에 없었나" 를 단계별로 추적
# =====================================================================
STAGE_ORDER = ("fts", "vector", "graph", "fusion", "boost", "rerank", "final", "doc_expand", "context", "answer")
STAGE_KO = {"fts": "FTS(키워드) 검색", "vector": "벡터 검색", "graph": "그래프 검색", "fusion": "융합(rrf)", "boost": "부스트", "rerank": "리랭크",
            "final": "최종 후보(top_k_final)", "doc_expand": "문서 단위 확장", "context": "컨텍스트", "answer": "답변"}


def resolve_expected(store, docs: List[str], terms: List[str], chunk_ids: List[str]) -> Dict[str, Any]:
    """기대 문서(ext_id·doc_id 부분 문자열)/용어/청크 → 목표 청크 목록.
    용어가 있으면 그 용어를 담은 청크가 목표(문서 지정이 있으면 그 문서 안에서), 없으면 문서의 모든 청크."""
    all_docs = store.list_docs()
    resolved: Dict[str, List[str]] = {}
    unresolved: List[str] = []
    for d in [x.strip() for x in (docs or []) if x and x.strip()]:
        ids = store.docs_by_ext_id(d.upper()) or store.docs_by_ext_id(d)
        if not ids:
            dl = d.lower()
            ids = [x["doc_id"] for x in all_docs if dl in x["doc_id"].lower()]
        if ids:
            resolved[d] = ids
        else:
            unresolved.append(d)
    terms = [t.strip() for t in (terms or []) if t and t.strip()]
    targets: Dict[str, Dict[str, Any]] = {}
    for cid in [x.strip() for x in (chunk_ids or []) if x and x.strip()]:
        c = store.get_chunk(cid)
        if c:
            targets[cid] = {"chunk_id": cid, "doc_id": c["doc_id"], "heading": c["heading"], "why": "chunk 지정", "terms": []}
        else:
            unresolved.append(cid)
    doc_ids = [x for ids in resolved.values() for x in ids]
    for did in dict.fromkeys(doc_ids):
        rows = store.all_chunks(did)
        if terms:
            hit_any = False
            for r in rows:
                text = ((r["heading"] or "") + "\n" + (r["text"] or "")).lower()
                found = [t for t in terms if t.lower() in text]
                if found:
                    hit_any = True
                    targets.setdefault(r["chunk_id"], {"chunk_id": r["chunk_id"], "doc_id": did, "heading": r["heading"], "why": "문서+용어", "terms": found})
            if not hit_any:
                for r in rows:
                    targets.setdefault(r["chunk_id"], {"chunk_id": r["chunk_id"], "doc_id": did, "heading": r["heading"], "why": "문서(용어는 이 문서에 없음)", "terms": []})
        else:
            for r in rows:
                targets.setdefault(r["chunk_id"], {"chunk_id": r["chunk_id"], "doc_id": did, "heading": r["heading"], "why": "문서 전체", "terms": []})
    if terms and not doc_ids and not chunk_ids:
        # 문서 지정 없이 용어만: 코퍼스 전체에서 용어를 담은 청크 (상위 forensic_term_targets 개, tuning.json)
        from .tuning import T
        lim = int(T.get("forensic_term_targets") or 20)
        for t in terms:
            for r in store.conn.execute("SELECT chunk_id, doc_id, heading FROM chunks WHERE lower(text) LIKE ? OR lower(heading) LIKE ? LIMIT ?",
                                        ("%" + t.lower() + "%", "%" + t.lower() + "%", lim)):
                targets.setdefault(r["chunk_id"], {"chunk_id": r["chunk_id"], "doc_id": r["doc_id"], "heading": r["heading"], "why": "용어", "terms": []})
                targets[r["chunk_id"]]["terms"] = sorted(set(targets[r["chunk_id"]]["terms"]) | {t})
    term_corpus: Dict[str, int] = {}
    for t in terms:
        term_corpus[t] = int(store.conn.execute("SELECT COUNT(*) FROM chunks WHERE lower(text) LIKE ? OR lower(heading) LIKE ?",
                                                ("%" + t.lower() + "%", "%" + t.lower() + "%")).fetchone()[0])
    return {"docs": resolved, "unresolved": unresolved, "terms": terms, "targets": list(targets.values()), "term_in_corpus": term_corpus}


def _rank_in(lst: List[Any], cid: str) -> Optional[int]:
    for i, x in enumerate(lst):
        c = x[0] if isinstance(x, (list, tuple)) else x
        if c == cid:
            return i + 1
    return None


def _chunk_terms(store, cid: str, kws: List[str]) -> Dict[str, Any]:
    """청크 본문에 질의 키워드가 있는지 + 청크의 대표 용어(동의어 후보)."""
    from .textutil import words, normalize_token, STOPWORDS
    c = store.get_chunk(cid)
    if not c:
        return {"present": [], "missing": list(kws), "candidates": []}
    text = ((c["heading"] or "") + " " + (c["text"] or "")).lower()
    toks = [normalize_token(w) for w in words(text)]
    tokset = set(toks)
    present = [k for k in kws if k in tokset or k in text]
    missing = [k for k in kws if k not in present]
    freq: Dict[str, int] = {}
    for t in toks:
        if len(t) >= 2 and t not in STOPWORDS and not t.isdigit() and t not in kws:
            freq[t] = freq.get(t, 0) + 1
    from .tuning import T
    head = [normalize_token(w) for w in words((c["heading"] or "").lower()) if len(w) >= 2]
    cands = list(dict.fromkeys(head + [t for t, _ in sorted(freq.items(), key=lambda kv: -kv[1])]))[:int(T.get("forensic_term_candidates") or 6)]
    return {"present": present, "missing": missing, "candidates": cands}


def trace_expectation(pipe, request_id: int, docs: Optional[List[str]] = None, terms: Optional[List[str]] = None, chunk_ids: Optional[List[str]] = None,
                      note: str = "", propose: bool = False, rerun: bool = True) -> Dict[str, Any]:
    """기대 결과 포렌식. 원 요청(requests.result)과 같은 설정으로 검색만 다시 실행해(LLM 답변·claim 은 끔) 목표 청크가
    fts/vector/graph → 융합 → 부스트 → 리랭크 → 최종 후보 → (doc_expand) → 컨텍스트 → 답변 중 어느 단계에서 탈락했는지와 원인·수정안을 만든다."""
    from .textutil import keywords, fts_query
    from . import tuning as _tuning
    from .config import Settings, apply_overrides
    store = pipe.store
    req = store.get_request(int(request_id))
    if not req or not req.get("result"):
        return {"error": "request %s not found (or no result)" % request_id}
    # 캐시/프리컴퓨트 적중 요청이면 원 요청으로 따라간다 (적중 기록에는 후보 목록·설정이 없다)
    followed: List[int] = []
    while req and req.get("result") and (req["result"].get("cached_from") or req["result"].get("precomputed_from")) and len(followed) < 5:
        src = req["result"].get("cached_from") or req["result"].get("precomputed_from")
        followed.append(int(request_id))
        try:
            nxt = store.get_request(int(src))
        except (TypeError, ValueError):
            nxt = None
        if not nxt or not nxt.get("result"):
            break
        request_id, req = int(src), nxt
    result = req["result"]
    q = result.get("query") or re.sub(r"^\[(cache|precomputed)\]\s*", "", req.get("summary") or "")
    exp = resolve_expected(store, docs or [], terms or [], chunk_ids or [])
    report: Dict[str, Any] = {"request_id": int(request_id), "run_id": req.get("run_id"), "query": q, "expected": {k: v for k, v in exp.items() if k != "targets"},
                              "targets": [], "note": note, "verdict": (result.get("evidence") or {}).get("verdict"), "groundedness": result.get("groundedness"),
                              "answer_mode": result.get("answer_mode"), "rerun": False, "fallback_rounds": len(result.get("fallback") or []),
                              "followed_from": followed}
    if not exp["targets"]:
        report["error"] = "기대 문서/용어/청크를 찾지 못했습니다: %s" % (exp["unresolved"] or "(입력 없음)")
        report["summary"] = [report["error"]]
        report["suggestions"] = []
        if exp["terms"] and all(v == 0 for v in exp["term_in_corpus"].values()):
            report["suggestions"].append({"kind": "corpus_gap", "detail": "용어 %s 가 코퍼스 어디에도 없음 — 문서 추가 필요" % exp["terms"], "confidence": 0.8,
                                          "payload": {"topic": " ".join(exp["terms"])}})
        return report
    # ---- 원 요청에서의 실제 상태 (requests.result 의 hits_brief; 구버전 요청은 hits 가 없어 absent 로 보인다) ----
    orig_hits = {h["chunk_id"]: h for h in (result.get("hits_brief") or result.get("hits") or [])}
    report["original_hits_known"] = bool(result.get("hits_brief") or result.get("hits"))
    answer_text = result.get("answer") or ""
    # ---- 같은 설정으로 검색 재실행 (엔진 내부 라운드 캡처) ----
    R: Optional[Dict[str, Any]] = None
    R_last: Optional[Dict[str, Any]] = None
    cfg = result.get("config") or {}
    saved = pipe.s.to_dict()
    saved_tuning = dict(_tuning.T.values)
    q_search = q
    try:
        if rerun:
            ov = dict(cfg.get("toggles") or {})
            ov.update({"llm_answer": False, "claim_check": False, "claim_check_llm": False, "answer_refine": False, "forensic_auto": False,
                       "evolve_capture": False, "query_cache": False, "precompute": False, "evidence_compress": False})
            apply_overrides(pipe.s, ov)
            for k, v in (cfg.get("tuning") or {}).items():
                try:
                    _tuning.T.set(k, v)
                except Exception:
                    pass
            pipe.reload_tuning(from_file=False)
            from .query_engine import QueryEngine
            eng = QueryEngine(pipe)
            eng.record_request = False      # 재실행은 requests/캐시/포렌식에 남기지 않는다
            with _silence_progress():
                res2, tr2 = eng.run(q, log=False, debug=1)
            if eng.rounds:
                R = eng.rounds[0]
                R_last = eng.rounds[-1]
                q_search = R.get("q_search") or q
            report["rerun"] = True
            report["rerun_verdict"] = (res2.get("evidence") or {}).get("verdict")
            report["rerun_rounds"] = len(eng.rounds)
    finally:
        ns = Settings.from_dict(saved)
        for k, v in ns.to_dict().items():
            if k != "toggles":
                setattr(pipe.s, k, v)
        pipe.s.toggles = ns.toggles
        _tuning.T.values = saved_tuning
        pipe.reload_tuning(from_file=False)
    kws = keywords(q_search)
    s = pipe.s
    T = _tuning.T
    n_vec = 0
    sims: Dict[str, float] = {}
    top_sim = 0.0
    if R is not None and s.toggles.vector:
        try:
            import numpy as np
            ids, mat = store.vector_matrix(pipe.embedder.name)
            if ids:
                qv = np.asarray(pipe.embedder.embed([q_search])[0], dtype=np.float32)
                if qv.shape[0] == mat.shape[1]:
                    from .retrieval import matmul_sims
                    arr = matmul_sims(mat, qv)
                    order = sorted(range(len(ids)), key=lambda i: -float(arr[i]))
                    n_vec = len(ids)
                    top_sim = float(arr[order[0]]) if order else 0.0
                    rank_of = {ids[i]: r + 1 for r, i in enumerate(order)}
                    sims = {cid: (rank_of[cid], float(arr[ids.index(cid)])) for cid in [t["chunk_id"] for t in exp["targets"]] if cid in rank_of}
        except Exception:
            pass
    fts_all: Dict[str, int] = {}
    if R is not None and s.toggles.fts:
        try:
            rows = store.fts_search(fts_query(q_search, "OR"), 500)
            fts_all = {cid: i + 1 for i, (cid, _sc, _sn) in enumerate(rows)}
        except Exception:
            pass
    seeds = [x[0] for x in ((R or {}).get("graph_res") or {}).get("seeds") or []] if R else []
    suggestions: List[Dict[str, Any]] = []
    summary: List[str] = []
    lost_counts: Dict[str, int] = {}
    per_target_suggestions: Dict[str, List[Dict[str, Any]]] = {}

    def suggest(kind: str, detail: str, conf: float, payload: Dict[str, Any], _cid: List[str] = []) -> None:
        bucket = per_target_suggestions.setdefault(_cid[0] if _cid else "_global", [])
        if not any(x["kind"] == kind and x["payload"] == payload for x in bucket):
            bucket.append({"kind": kind, "detail": detail, "confidence": conf, "payload": payload})

    for tgt in exp["targets"]:
        cid = tgt["chunk_id"]
        cur_cid = [cid]
        oh = orig_hits.get(cid)
        journey: List[Dict[str, Any]] = []
        status_orig = "cited" if (oh and oh.get("in_context")) else ("candidate" if oh else "absent")
        lost_at = ""
        if R is not None:
            lists = R.get("lists") or {}
            # 채널별
            fts_ranks = {n: _rank_in(l, cid) for n, l in lists.items() if n.startswith("fts")}
            fts_hit = {n: r for n, r in fts_ranks.items() if r}
            if not s.toggles.fts:
                journey.append({"stage": "fts", "status": "off", "detail": "fts 토글 off"})
            elif fts_hit:
                journey.append({"stage": "fts", "status": "hit", "rank": min(fts_hit.values()), "detail": ", ".join("%s#%d" % (n, r) for n, r in sorted(fts_hit.items(), key=lambda kv: kv[1]))})
            else:
                ct = _chunk_terms(store, cid, kws)
                r500 = fts_all.get(cid)
                det = "질의 키워드 %s 중 청크에 있는 것 %s · 없는 것 %s" % (kws[:8], ct["present"], ct["missing"])
                if r500:
                    det += " · OR 검색 전체 순위 %d (top_k_fts=%d 밖)" % (r500, s.top_k_fts)
                    if r500 <= int(T.get("forensic_near_miss_mult") or 3) * s.top_k_fts:
                        suggest("tuning", "top_k_fts 를 %d 이상으로 (FTS OR 순위 %d)" % (r500, r500), 0.4, {"key": "top_k_fts", "value": r500}, cur_cid)
                else:
                    det += " · OR 검색 500위 안에도 없음"
                journey.append({"stage": "fts", "status": "miss", "detail": det, "missing": ct["missing"], "candidates": ct["candidates"]})
                # 어휘 불일치 후보: 질의에 없는 키워드 ↔ 청크 헤딩의 대표 용어 (헤딩 용어만, 최대 2개)
                head_terms = ct["candidates"][:2]
                for m in ct["missing"][:1]:
                    for cnd in head_terms:
                        if cnd != m and len(cnd) >= 2:
                            suggest("query_rule", "'%s' ↔ '%s' 동의어/관련어 등록 검토 (기대 청크 헤딩 용어; rules add synonym %s %s)" % (m, cnd, m, cnd), 0.35,
                                    {"type": "synonym", "term": m, "values": [cnd]}, cur_cid)
            vr = _rank_in(lists.get("vector") or [], cid)
            if not s.toggles.vector:
                journey.append({"stage": "vector", "status": "off", "detail": "vector 토글 off"})
            elif vr:
                journey.append({"stage": "vector", "status": "hit", "rank": vr, "detail": "vector#%d" % vr})
            else:
                rk, sm = sims.get(cid, (None, None))
                det = ("전체 %d 벡터 중 순위 %s, 유사도 %.3f (최고 %.3f, 임계 vector_min_sim=%s, top_k_vector=%d)" % (n_vec, rk, sm, top_sim, T.get("vector_min_sim"), s.top_k_vector)) if rk else "임베딩 없음(coverage) 또는 차원 불일치"
                journey.append({"stage": "vector", "status": "miss", "rank": rk, "sim": sm, "detail": det})
                if rk and rk <= int(T.get("forensic_near_miss_mult") or 3) * s.top_k_vector:
                    suggest("tuning", "top_k_vector 를 %d 이상으로 (벡터 순위 %d)" % (rk, rk), 0.35, {"key": "top_k_vector", "value": rk}, cur_cid)
                if not rk:
                    suggest("tuning", "embed report 로 coverage 확인 (기대 청크 임베딩 없음)", 0.5, {"key": "embed_coverage"}, cur_cid)
            gr = _rank_in(lists.get("graph") or [], cid)
            if not s.toggles.graph:
                journey.append({"stage": "graph", "status": "off", "detail": "graph 토글 off"})
            elif gr:
                journey.append({"stage": "graph", "status": "hit", "rank": gr, "detail": "graph#%d" % gr})
            else:
                ents = [store.get_entity(m["entity_id"]) or {} for m in store.conn.execute("SELECT entity_id FROM mentions WHERE chunk_id=?", (cid,)).fetchall()]
                names = [e.get("name") for e in ents if e.get("name") and e.get("type") not in ("date", "amount")][:8]
                overlap = [n for n in names if n in seeds]
                det = "청크 엔티티 %s · 시드 %s · 겹침 %s" % (names, seeds[:6], overlap) if names else "청크에 그래프 엔티티(멘션) 없음"
                journey.append({"stage": "graph", "status": "miss", "detail": det})
                if names and not overlap and kws:
                    suggest("alias", "질의 키워드 '%s' 를 엔티티 %s 의 별칭으로 등록 검토 (data/rules.json)" % (kws[0], names[0]), 0.3, {"entity": names[0], "alias": kws[0]}, cur_cid)
            any_channel = bool(fts_hit or vr or gr)
            if not any_channel:
                lost_at = lost_at or "retrieval"
            fr = _rank_in(R.get("fused_order") or [], cid)
            br = _rank_in(R.get("boost_order") or [], cid)
            hit_obj = next((h for h in (R.get("hits") or []) if h.chunk_id == cid), None)
            journey.append({"stage": "fusion", "status": "hit" if fr else "miss", "rank": fr, "detail": ("융합 후보 %d/%d" % (fr, len(R.get("fused_order") or []))) if fr else "융합 후보 아님"})
            journey.append({"stage": "boost", "status": "hit" if br else "miss", "rank": br, "detail": ("부스트 후 %d위 · 배율 %s" % (br, (hit_obj.boosts if hit_obj else {}))) if br else "-"})
            n_cands = len(R.get("rerank_before") or [])
            rr_in = cid in (R.get("rerank_before") or [])
            fi = _rank_in(R.get("final_order") or [], cid)
            if not rr_in and br:
                lost_at = lost_at or "rerank_candidates"
                journey.append({"stage": "rerank", "status": "miss", "detail": "리랭크 후보 %d개 밖 (부스트 순위 %d) → rerank_candidates 확대 또는 채널 가중" % (n_cands, br)})
                suggest("tuning", "rerank_candidates 를 %d 이상으로 (부스트 순위 %d)" % (br, br), 0.45, {"key": "rerank_candidates", "value": br}, cur_cid)
            elif rr_in:
                journey.append({"stage": "rerank", "status": "hit", "rank": fi, "detail": "리랭크 후보 %d개 안 (%s)" % (n_cands, ("최종 %d위, rerank score %s" % (fi, hit_obj.rerank if hit_obj else None)) if fi else "top_k_final=%d 밖으로 밀림 (rerank score %s)" % (s.top_k_final, hit_obj.rerank if hit_obj else None))})
                if not fi:
                    lost_at = lost_at or "rerank"
                    suggest("tuning", "top_k_final 확대 또는 pin (리랭크 후 top_k_final=%d 밖)" % s.top_k_final, 0.45, {"key": "top_k_final", "value": s.top_k_final + 4}, cur_cid)
            journey.append({"stage": "final", "status": "hit" if fi else "miss", "rank": fi, "detail": "top_k_final=%d" % s.top_k_final})
            ctx = R.get("ctx") or {}
            cit = next((c for c in ctx.get("citations") or [] if c["chunk_id"] == cid), None)
            ex = next((x for x in (R.get("expand") or []) if x[0] == cid), None)
            if ex:
                journey.append({"stage": "doc_expand", "status": "hit", "detail": "문서 단위 확장으로 추가 (부모 %s, 점수 %.2f)" % (ex[1], ex[2])})
            elif s.toggles.doc_expand and not fi:
                pd = ((R.get("expand_info") or {}).get("per_doc") or {}).get(tgt["doc_id"])
                if pd:
                    journey.append({"stage": "doc_expand", "status": "miss", "detail": "같은 문서가 확장 대상이었으나 점수 미달/상한 (후보 %d, 채택 %d, 탈락 최고 %.2f < min %.2f)" % (pd["candidates"], pd["added"], pd.get("best_rejected", 0), (R.get("expand_info") or {}).get("min_score", 0))})
                    suggest("tuning", "doc_expand_min_score 를 %.2f 이하로 또는 doc_expand_max_chunks 확대" % max(0.0, pd.get("best_rejected", 0) - 0.01), 0.4, {"key": "doc_expand_min_score", "value": round(max(0.0, pd.get("best_rejected", 0) - 0.01), 2)}, cur_cid)
                else:
                    journey.append({"stage": "doc_expand", "status": "miss", "detail": "문서가 확장 대상(상위 %s 문서)에 들지 못함" % T.get("doc_expand_top_docs")})
            if cit:
                journey.append({"stage": "context", "status": "hit", "rank": cit["n"], "detail": "[C%d] (%s)" % (cit["n"], cit.get("kind"))})
            else:
                dropped = [d for d in (ctx.get("dropped") or []) if d.startswith(cid)]
                why = "중복 제거/글자 상한으로 탈락 (%s)" % dropped[0] if dropped else ("최종 후보 아님" if not fi else "컨텍스트 글자 상한(context_max_chars=%d)" % s.context_max_chars)
                journey.append({"stage": "context", "status": "miss", "detail": why})
                if fi and not dropped:
                    lost_at = lost_at or "context"
                    suggest("tuning", "context_max_chars 확대 (최종 후보였으나 컨텍스트 상한)", 0.5, {"key": "context_max_chars"}, cur_cid)
                elif dropped:
                    lost_at = lost_at or "context"
        # 답변 단계 (원 요청 기준)
        want = tgt.get("terms") or exp["terms"]
        in_ans = [t for t in want if t.lower() in answer_text.lower()]
        if status_orig == "cited":
            if want and not in_ans:
                journey.append({"stage": "answer", "status": "miss", "detail": "근거 [C%s] 로 컨텍스트에 있었으나 답변에 용어 %s 가 없음 → 답변 생성 단계(길이·프롬프트·claim 정책)" % (oh.get("n"), want)})
                lost_at = lost_at or "answer"
                suggest("tuning", "answer_length_target=long (근거는 있었으나 답변에 반영 안 됨)", 0.5, {"key": "answer_length_target", "value": "long"}, cur_cid)
            else:
                journey.append({"stage": "answer", "status": "hit", "detail": "원 답변에서 [C%s] 인용, 용어 %s" % (oh.get("n"), in_ans or "(용어 미지정)")})
        elif status_orig == "candidate":
            journey.append({"stage": "answer", "status": "miss", "detail": "원 요청에서 후보였으나 컨텍스트 제외 (%s)" % ",".join(oh.get("why") or [])})
        elif not report["original_hits_known"]:
            journey.append({"stage": "answer", "status": "unknown", "detail": "구버전 요청 기록이라 원 결과의 후보 목록을 알 수 없음 (재실행 결과로 판단)"})
        else:
            journey.append({"stage": "answer", "status": "miss", "detail": "원 요청 결과에 없음"})
        if not lost_at:
            if status_orig == "cited" and (not want or in_ans):
                lost_at = "none"
            elif status_orig == "candidate":
                lost_at = "context"
            elif not report["original_hits_known"] and R is not None:
                lost_at = "none" if any(j["stage"] == "context" and j["status"] == "hit" for j in journey) else "retrieval"
            else:
                lost_at = "retrieval"
        lost_counts[lost_at] = lost_counts.get(lost_at, 0) + 1
        reach = max([STAGE_ORDER.index(j["stage"]) for j in journey if j["status"] == "hit" and j["stage"] in STAGE_ORDER] or [-1])
        report["targets"].append({"chunk_id": cid, "doc_id": tgt["doc_id"], "heading": tgt.get("heading"), "why": tgt.get("why"), "terms": tgt.get("terms"),
                                  "original": status_orig, "lost_at": lost_at, "reach": reach, "journey": journey})
    # ---- 요약·제안: 가장 멀리 간(가장 '아까운') 목표 청크 기준으로만 제안 → 잡음 억제 ----
    best = max(report["targets"], key=lambda x: (x["reach"], -len(x["journey"]))) if report["targets"] else None
    if best:
        suggestions.extend(per_target_suggestions.get(best["chunk_id"], []))
    suggestions.extend(per_target_suggestions.get("_global", []))
    for t_, n_ in exp["term_in_corpus"].items():
        if n_ == 0:
            suggestions.append({"kind": "corpus_gap", "detail": "용어 '%s' 가 코퍼스 어디에도 없음 — 문서 추가/표기 확인" % t_, "confidence": 0.8, "payload": {"topic": t_}})
    if best and best["lost_at"] != "none":
        doc_key = next(iter(exp["docs"].keys()), None) or best["doc_id"].rsplit("/", 1)[-1]
        suggestions.append({"kind": "pin", "detail": "이 질의 유형에 문서 %s 를 고정 (pin add --doc %s --keywords %s)" % (best["doc_id"], doc_key, ",".join(kws[:3])),
                            "confidence": float(T.get("forensic_pin_confidence") or 0.6), "payload": {"doc": doc_key, "keywords": kws[:3], "query": q, "note": "forensic expect #%s" % request_id}})
    n_cited = sum(1 for t in report["targets"] if t["original"] == "cited")
    if report["original_hits_known"]:
        summary.append("목표 청크 %d개 중 원 답변 컨텍스트에 포함 %d개 · 탈락 단계 분포 %s" % (len(report["targets"]), n_cited, lost_counts))
    else:
        summary.append("목표 청크 %d개 (원 요청 기록에 후보 목록이 없어 재실행 결과로 판단) · 탈락 단계 분포 %s" % (len(report["targets"]), lost_counts))
    if best:
        first_miss = next((j for j in best["journey"] if j["status"] == "miss"), None)
        if best["lost_at"] == "none":
            summary.append("가장 멀리 간 청크 (%s): 정상 인용됨%s" % (best["chunk_id"], "" if not exp["terms"] else " · 기대 용어 %s 답변 포함" % exp["terms"]))
        else:
            summary.append("대표 원인 (%s, %s 까지 도달): %s — %s" % (best["chunk_id"], STAGE_KO.get(STAGE_ORDER[best["reach"]], "-") if best["reach"] >= 0 else "어느 채널에도 없음",
                                                         STAGE_KO.get(_lost_stage(best["lost_at"]), best["lost_at"]), (first_miss or {}).get("detail", "")))
    report["lost_counts"] = lost_counts
    report["summary"] = summary
    report["suggestions"] = suggestions
    report["best_target"] = best["chunk_id"] if best else None
    report["keywords"] = kws
    # ---- 기록: forensics(origin=expectation) + 에피소드 피드백 ----
    findings = [{"stage": _lost_stage(t["lost_at"]), "problem": "기대 청크 %s 가 %s 단계에서 탈락" % (t["chunk_id"], STAGE_KO.get(_lost_stage(t["lost_at"]), t["lost_at"])) if t["lost_at"] != "none" else "기대 청크 %s 는 정상 인용됨" % t["chunk_id"],
                 "evidence": (next((j["detail"] for j in t["journey"] if j["status"] == "miss"), "") or "")[:300], "severity": "info" if t["lost_at"] == "none" else "error"} for t in report["targets"]]
    diag = {"findings": findings, "suggestions": suggestions, "topics": kws[:5], "severity": "error" if any(f["severity"] == "error" for f in findings) else "info"}
    try:
        report["forensic_id"] = record(store, int(request_id), req.get("run_id") or "", q, "expectation", result.get("groundedness"), diag, "expectation")
    except Exception as e:
        report["forensic_error"] = str(e)[:200]
    try:
        from . import memory as _mem
        report["episode_id"] = _mem.record_episode(store, int(request_id), q, "expectation", "negative" if lost_counts.get("none", 0) < len(report["targets"]) else "positive",
                                                   [t["chunk_id"] for t in report["targets"]][:20], kws[:6],
                                                   {"note": note, "expected": {k: v for k, v in exp.items() if k != "targets"}, "lost_counts": lost_counts})
        store.conn.execute("UPDATE episodes SET feedback=? WHERE id=?", (-1 if lost_counts.get("none", 0) < len(report["targets"]) else 1, report["episode_id"]))
        store.conn.commit()
    except Exception:
        pass
    if propose:
        ids = []
        for sg in suggestions:
            if sg["kind"] in ("pin", "query_rule", "corpus_gap", "tuning", "alias"):
                ids.append(store.add_proposal(sg["kind"], dict(sg["payload"]), "forensic expect #%s: %s" % (request_id, sg["detail"][:120]), float(sg["confidence"]), "expectation"))
        report["proposals"] = ids
    return report


def _lost_stage(lost_at: str) -> str:
    return {"retrieval": "fts", "rerank_candidates": "rerank", "rerank": "rerank", "context": "context", "answer": "answer", "none": "answer"}.get(lost_at, lost_at)


class _silence_progress:
    """재실행 중 진행 표시(progress)가 원 요청의 토큰에 섞이지 않게 잠시 unbind."""

    def __enter__(self):
        from . import progress as _pg
        self.tok = _pg.current_token()
        if self.tok:
            _pg._TL.token = None
        return self

    def __exit__(self, *a):
        from . import progress as _pg
        if self.tok:
            _pg._TL.token = self.tok


def format_expectation(rep: Dict[str, Any]) -> str:
    if rep.get("error") and not rep.get("targets"):
        return "forensic expect: %s" % rep["error"]
    lines = ["forensic expect — request #%s  Q: %s" % (rep.get("request_id"), rep.get("query")),
             "원 판정=%s groundedness=%s mode=%s · 재실행=%s%s · 기대: docs=%s terms=%s%s" % (
                 rep.get("verdict"), rep.get("groundedness"), rep.get("answer_mode"), rep.get("rerun"),
                 (" (%d 라운드, verdict %s)" % (rep.get("rerun_rounds", 0), rep.get("rerun_verdict"))) if rep.get("rerun") else "",
                 list((rep.get("expected") or {}).get("docs", {}).keys()), (rep.get("expected") or {}).get("terms"),
                 (" · 미해결 %s" % (rep.get("expected") or {}).get("unresolved")) if (rep.get("expected") or {}).get("unresolved") else "")]
    for s_ in rep.get("summary", []):
        lines.append("  " + s_)
    for t in rep.get("targets", []):
        lines.append("")
        lines.append("● %s  (%s · %s) 원 결과=%s → 탈락 단계=%s" % (t["chunk_id"], (t.get("heading") or "")[:50], t.get("why"), t["original"], t["lost_at"]))
        for j in t["journey"]:
            mark = {"hit": "✔", "miss": "✘", "off": "—", "unknown": "?"}.get(j["status"], "?")
            lines.append("    %s %-11s %s%s" % (mark, j["stage"], ("#%s " % j["rank"]) if j.get("rank") else "", j.get("detail", "")))
    lines.append("")
    lines.append("제안:")
    for sg in rep.get("suggestions", []):
        lines.append("  - (%s %.2f) %s" % (sg["kind"], float(sg.get("confidence") or 0), sg["detail"]))
    if not rep.get("suggestions"):
        lines.append("  (없음)")
    if rep.get("proposals"):
        lines.append("등록된 제안 id: %s (evolve status / apply)" % rep["proposals"])
    if rep.get("forensic_id"):
        lines.append("forensics #%s 에 기록됨 (origin=expectation)" % rep["forensic_id"])
    return "\n".join(lines)


def format_forensic(row: Dict[str, Any]) -> str:
    lines = ["forensic #%s request=%s run=%s verdict=%s groundedness=%s" % (row.get("id"), row.get("request_id"), row.get("run_id"), row.get("verdict"), row.get("groundedness")),
             "Q: %s" % row.get("query"), "findings:"]
    for f in row.get("findings", []):
        lines.append("  [%s] %-14s %s  %s" % (f.get("severity"), f.get("stage"), f.get("problem"), ("← " + str(f.get("evidence"))[:120]) if f.get("evidence") else ""))
    lines.append("suggestions:")
    for s in row.get("suggestions", []):
        lines.append("  - (%s %.2f) %s" % (s.get("kind"), float(s.get("confidence") or 0), s.get("detail")))
    return "\n".join(lines)
