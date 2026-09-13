# -*- coding: utf-8 -*-
"""질의 엔진 (v3) — Pipeline.query 가 위임한다.

sync_index → providers → cache(query_cache → precompute) → [plan] time_scope → query_rules → router(+llm) → query_expand → pins
→ [retrieve round] fts(+rule/alt/related 리스트) | vector(+alt) | graph(+ID 시드·doc_refs) | doc_vector → fuse → boost → rerank → context
→ evidence_check → (fallback 루프: rules → expand → graph → wide → mcp, 예산 제한) → answer(LLM|extractive|insufficient)
→ claim_check(+llm) → policy/refine → forensic_auto → evolve_capture → memory episode → log
CLI/Web/MCP/eval/trial 모두 이 하나의 경로를 쓴다.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from .answer import build_context, generate_answer, check_claims, check_claims_llm, apply_claim_policy, refine_answer
from .profiler import Profiler
from .providers import parse_json, LLMError
from .retrieval import fts_search, vector_search, graph_search, rerank, route, Hit, parse_weight_map
from .textutil import keywords
from . import evidence as _ev
from . import fusion as _fusion
from . import logging_setup as _log
from . import prompts as _prompts
from . import timeparse as _time
from . import tuning as _tuning

DOC_TYPE_HINTS = {
    "issue": ("이슈", "issue", "문제", "장애", "결함", "버그", "현상", "원인"),
    "cl": ("cl", "change list", "체인지리스트", "수정 반영", "커밋", "패치"),
    "sw_design": ("설계", "아키텍처", "code map", "코드맵", "구조", "인터페이스"),
    "hw_design": ("hw", "하드웨어", "타이밍", "레지스터", "revision", "리비전", "rev"),
    "coding_rule": ("코딩 규칙", "코딩룰", "coding rule", "규칙", "리뷰 규칙"),
    "weekly_report": ("주간", "weekly", "보고", "업무 요약"),
    "tc_list": ("tc", "테스트 케이스", "검증 항목", "test case"),
}


def doc_type_hints(q: str) -> List[str]:
    ql = q.lower()
    out = []
    for dt, words_ in DOC_TYPE_HINTS.items():
        if any(w in ql for w in words_):
            out.append(dt)
    return out


class RoundConfig:
    def __init__(self, **kw: Any):
        self.k_mult = 1.0
        self.fts_mode: Optional[str] = None
        self.hops_extra = 0
        self.neighbors_extra = 0
        self.related_mult = 1.0
        self.time_filter = True          # False 면 filter → boost 로 완화
        self.type_boost = True
        self.exclude = True
        self.extra_queries: List[Tuple[str, float, str]] = []
        self.use_llm_alts = True
        self.mcp_enrich = False
        self.level = "base"
        for k, v in kw.items():
            setattr(self, k, v)

    def derive(self, level: str, followups: Optional[List[str]] = None, widen: float = 2.0) -> "RoundConfig":
        c = RoundConfig(**self.__dict__)
        c.level = level
        if level == "rules":
            c.k_mult *= widen
            c.fts_mode = "or"
            c.related_mult *= 2.0
        elif level == "expand":
            c.k_mult *= widen
            c.extra_queries = list(self.extra_queries) + [(f, 0.7, "followup") for f in (followups or [])]
        elif level == "graph":
            c.hops_extra += 1
            c.neighbors_extra += 1
            c.k_mult *= widen
        elif level == "wide":
            c.k_mult *= widen * widen
            c.time_filter = False
            c.type_boost = False
            c.exclude = False
            c.fts_mode = "or"
        elif level == "mcp":
            c.mcp_enrich = True
        return c

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["extra_queries"] = [x[0] for x in self.extra_queries]
        return d


class QueryEngine:
    def __init__(self, pipe):
        self.pipe = pipe

    # ---------------------------------------------------------------- 진입
    def run(self, q: str, log: bool = True, debug: Optional[int] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        p = self.pipe
        s, t, T = p.s, p.s.toggles, _tuning.T
        prof = Profiler("query", debug=s.debug_level if debug is None else debug, log=t.log_stages)
        _log.log("info", "query: %s" % q[:200], "query")
        with prof.stage("sync_index") as st:
            st.note(build_version=p.store.build_version(), reloaded_caches=p.sync_with_db())
        roles = ["answer", "rerank"]
        if t.query_expand or t.query_decompose:
            roles.append("expand")
        if t.evidence_check_llm or t.claim_check_llm:
            roles.append("verify")
        p._ensure_providers(prof, tuple(roles))

        # ---- 캐시: query_cache (메모리) → precompute (영속) ----
        key = p._cache_key(q) if t.query_cache else None
        if key and key in p._qcache:
            p._qcache_stats["hits"] += 1
            cached = p._qcache[key]
            p._qcache.move_to_end(key)
            with prof.stage("cache_hit", key=key[:12]) as st:
                st.note(saved_ms=cached["result"].get("ms"), saved_tokens=cached["trace"].get("summary", {}).get("llm", {}).get("total_tokens", 0))
            trace = prof.finish()
            res = dict(cached["result"], cached=True, ms=trace["ms"], query_id=None, proposals=[], run_id=trace.get("run_id"))
            res["request_id"] = p.store.log_request("query", "[cache] " + q, trace, {"cached_from": cached["result"].get("request_id")}, res.get("config"), None, keep=s.keep_requests)
            return res, trace
        if key:
            p._qcache_stats["misses"] += 1
        pkey = None
        if t.precompute:
            from . import precompute as _pc
            pkey = _pc.cache_key(q, p.store.build_version(), p.answer_signature())
            hit = _pc.get_cached(p.store, pkey)
            if hit:
                with prof.stage("precompute_hit", key=pkey[:12], source=hit.get("precomputed_source")) as st:
                    st.note(saved_ms=hit.get("ms"), saved_tokens=(hit.get("tokens") or {}).get("total_tokens", 0))
                trace = prof.finish()
                res = dict(hit, cached=False, precomputed=True, ms=trace["ms"], query_id=None, proposals=[], run_id=trace.get("run_id"))
                res["request_id"] = p.store.log_request("query", "[precomputed] " + q, trace, {"precomputed_from": hit.get("request_id")}, res.get("config"), None, keep=s.keep_requests)
                return res, trace
            with prof.stage("precompute_miss", key=pkey[:12]):
                pass

        # ---- 계획 ----
        plan: Dict[str, Any] = {}
        q_search = q
        scope = None
        if t.time_scope:
            with prof.stage("time_scope", timezone=s.timezone) as st:
                scope = _time.parse(q, s.timezone, s.week_start)
                if scope:
                    q_search = scope["query"]
                    st.note(expr=scope["expr"], range=[scope["from"], scope["to"]], kind=scope["kind"], mode=T.get("time_mode"), query=q_search)
                else:
                    st.note(expr=None)
        else:
            prof.skipped("time_scope")
        plan["time_scope"] = scope
        qr = None
        if t.query_rules:
            from . import query_rules as _qr
            with prof.stage("query_rules") as st:
                qr = _qr.expand(q_search, T.get("syn_w"), T.get("related_w"), T.get("acronym_phrase"))
                st.note(fired=len(qr["fired"]), types=sorted({f["type"] for f in qr["fired"]}), alt=len(qr["alt_queries"]), related=len(qr["related"]),
                        exclude=qr["exclude"], seeds=qr["seeds"][:6])
                st.debug(fired=qr["fired"], fts_query=qr["fts_query"][:400], query_alias=qr["query_alias"], alt_queries=[a[0] for a in qr["alt_queries"]],
                         related=qr["related"])
        else:
            prof.skipped("query_rules")
        plan["query_rules"] = {k: qr[k] for k in ("fired", "alt_queries", "related", "exclude", "seeds", "query_alias")} if qr else None

        weights = {"fts": 1.0, "vector": 1.0, "graph": 1.0}
        route_info: Dict[str, Any] = {}
        if t.router:
            with prof.stage("router") as st:
                route_info = route(q_search, p.store)
                route_info["doc_types"] = doc_type_hints(q)
                if t.router_llm:
                    rl = p.llm_for("expand")
                    if rl.available:
                        try:
                            r = rl.complete(_prompts.get("router"), q, max_tokens=200, effort=s.role_llm("expand")["effort"], json_mode=True)
                            data = parse_json(r["text"]) or {}
                            route_info["llm"] = {"intent": data.get("intent"), "doc_types": data.get("doc_types"), "time_sensitive": data.get("time_sensitive")}
                            for dt in (data.get("doc_types") or []):
                                if isinstance(dt, str) and dt not in route_info["doc_types"]:
                                    route_info["doc_types"].append(dt)
                        except (LLMError, ValueError) as e:
                            route_info["llm"] = {"error": str(e)[:100]}
                weights = route_info["weights"]
                st.note(**route_info)
        else:
            prof.skipped("router")
        # 사용자 채널 가중 배율
        for ch in ("fts", "vector", "graph"):
            weights[ch] = weights.get(ch, 1.0) * float(T.get("channel_w_%s" % ch))

        alt_llm: List[str] = []
        sub_queries: List[str] = []
        if t.query_expand or t.query_decompose:
            rl = p.llm_for("expand")
            if rl.available:
                with prof.stage("query_expand", model=rl.model, n=T.get("query_expand_n"), decompose=t.query_decompose) as st:
                    try:
                        r = rl.complete(_prompts.get("expand"), "N=%d\n%s" % (T.get("query_expand_n"), q_search), max_tokens=400,
                                        effort=s.role_llm("expand")["effort"], json_mode=True)
                        data = parse_json(r["text"]) or {}
                        if t.query_expand:
                            alt_llm = [x for x in (data.get("queries") or []) if isinstance(x, str) and x.strip() and x.strip() != q_search][:T.get("query_expand_n")]
                        if t.query_decompose:
                            sub_queries = [x for x in (data.get("sub_queries") or []) if isinstance(x, str) and x.strip() and x.strip() != q_search][:T.get("query_decompose_max")]
                        st.note(alt_queries=alt_llm, sub_queries=sub_queries, keywords=(data.get("keywords") or [])[:10], usage=r.get("usage"))
                        st.sample(response=r["text"][:1000])
                    except (LLMError, ValueError) as e:
                        st.note(error=str(e)[:200])
            else:
                prof.skipped("query_expand", "LLM provider unavailable")
        else:
            prof.skipped("query_expand")
        pin_info: Dict[str, Any] = {"weights": {}, "inject": [], "matched": []}
        if t.pins:
            from . import pins as _pins
            with prof.stage("pins") as st:
                try:
                    pin_info = _pins.match_pins(p.store, q, route_info.get("doc_types"))
                except Exception as e:
                    st.note(error=str(e)[:100])
                st.note(matched=len(pin_info["matched"]), inject=len(pin_info["inject"]))
                st.debug(pins=pin_info["matched"])
        else:
            prof.skipped("pins")
        plan["doc_types"] = route_info.get("doc_types") or []
        plan["alt_llm"] = alt_llm
        plan["sub_queries"] = sub_queries
        plan["pins"] = pin_info["matched"]
        feedback_w: Dict[str, float] = {}
        if t.feedback_boost:
            from . import memory as _mem
            feedback_w = _mem.feedback_weights(p.store, T.get("memory_half_life_days"))

        # ---- 검색 라운드 ----
        base_cfg = RoundConfig()
        R = self._retrieve(prof, q, q_search, qr, route_info, weights, alt_llm + sub_queries, pin_info, scope, feedback_w, base_cfg)
        t_start = time.perf_counter()

        # ---- 근거 판정 + fallback ----
        ev: Optional[Dict[str, Any]] = None
        llm_ev: Optional[Dict[str, Any]] = None
        verdict = "sufficient"
        rounds: List[Dict[str, Any]] = []
        if t.evidence_check:
            ev, llm_ev, verdict = self._assess(prof, q_search, R, stage="evidence_check")
            if verdict != "sufficient" and t.fallback_loop:
                tokens_fn = lambda: int(prof.summary()["llm"]["total_tokens"])  # noqa: E731
                budget = _ev.Budget(T.get("fallback_max_attempts"), T.get("fallback_token_budget"), T.get("fallback_latency_ms"), t_start, tokens_fn)
                levels = _ev.plan_levels(T.get("fallback_levels"), t)
                cfg = base_cfg
                order = {"sufficient": 2, "weak": 1, "insufficient": 0}
                for level in levels:
                    if verdict == "sufficient" or not budget.allow():
                        break
                    if level == "expand" and not (p.llm_for("expand").available and (t.query_expand or t.query_decompose or t.fallback_loop)):
                        continue
                    budget.spend()
                    cfg = cfg.derive(level, (llm_ev or {}).get("followup_queries"), T.get("fallback_widen_factor"))
                    with prof.stage("fallback", level=level, attempt=budget.used_attempts, cfg=cfg.to_dict()) as st:
                        extra_alts = list(alt_llm + sub_queries)
                        if level == "expand" and not alt_llm and p.llm_for("expand").available:
                            extra_alts += self._expand_now(prof, q_search, s, T)
                        R2 = self._retrieve(prof, q, q_search, qr, route_info, weights, extra_alts, pin_info, scope, feedback_w, cfg)
                        ev2, llm_ev2, verdict2 = self._assess(prof, q_search, R2, stage="evidence_check_%d" % budget.used_attempts)
                        improved = order[verdict2] > order[verdict] or (order[verdict2] == order[verdict] and ev2["score"] > (ev or {}).get("score", 0))
                        st.note(verdict=verdict2, score=ev2["score"], n_hits=len(R2["final"]), improved=improved, stop_reason=budget.stop_reason)
                        rounds.append({"level": level, "verdict": verdict2, "score": ev2["score"], "n_hits": len(R2["final"]), "improved": improved})
                        if improved:
                            R, ev, llm_ev, verdict = R2, ev2, llm_ev2, verdict2
                if verdict != "sufficient" and budget.stop_reason and rounds:
                    rounds[-1]["stop_reason"] = budget.stop_reason
        else:
            prof.skipped("evidence_check")

        final, chunks, ctx, graph_res, fts_snips = R["final"], R["chunks"], R["ctx"], R["graph_res"], R["fts_snips"]
        # ---- 답변 ----
        al = p.llm_for("answer")
        if t.evidence_check and verdict == "insufficient":
            with prof.stage("answer_insufficient", reasons=(ev or {}).get("reasons")) as st:
                hd = self._hit_dicts(final, chunks, ctx, fts_snips)
                text = _ev.insufficient_text(q, ev or {}, llm_ev, rounds, hd)
                ans = {"answer": text, "mode": "insufficient", "cited": [], "model": None}
                st.note(answer_chars=len(text))
            prof.skipped("answer_llm", "evidence insufficient → insufficient_data 응답 (LLM 호출 생략)")
        else:
            if t.evidence_compress and al.available and t.llm_answer:
                ctx = self._compress(prof, q_search, ctx, chunks, p, s)
            ans = generate_answer(q, ctx, al, t.llm_answer, prof, s.role_llm("answer")["effort"], chunks, final, max_tokens=s.answer_max_tokens,
                                  meta=p.store.doc_meta_map(), graph=graph_res)
            if verdict == "weak" and ans["mode"] == "llm":
                ans["answer"] = "> ⚠ 근거가 약합니다 (%s). 아래 답변은 제한된 근거에 기반합니다.\n\n" % "; ".join((ev or {}).get("reasons") or []) + ans["answer"]

        # ---- claim check ----
        claims_info: Optional[Dict[str, Any]] = None
        if t.claim_check and ans["mode"] == "llm":
            with prof.stage("claim_check", llm=bool(t.claim_check_llm), policy=T.get("claim_policy")) as st:
                claims_info = check_claims(ans["answer"], ctx["citations"], chunks, float(T.get("claim_support_min")))
                if t.claim_check_llm and claims_info["n_factual"]:
                    vl = p.llm_for("verify")
                    if vl.available:
                        try:
                            li = check_claims_llm(vl, q, claims_info["claims"], ctx["citations"], chunks, s.role_llm("verify")["effort"])
                            claims_info.update({k: li[k] for k in ("supported", "partial", "unsupported", "groundedness")})
                            claims_info["llm"] = {"changed": li["changed"], "usage": li.get("usage")}
                            st.sample(llm_response=li.get("raw"))
                        except (LLMError, ValueError) as e:
                            claims_info["llm"] = {"error": str(e)[:200]}
                unsup = [c["text"][:120] for c in claims_info["claims"] if c.get("verdict") == "unsupported"]
                policy = T.get("claim_policy")
                refined = False
                if claims_info["unsupported"] and (policy == "refine" or t.answer_refine) and al.available:
                    try:
                        rr = refine_answer(al, q, ctx, ans["answer"], claims_info["claims"], s.role_llm("answer")["effort"], s.answer_max_tokens)
                        ans["answer"] = rr["answer"]
                        refined = True
                        ci2 = check_claims(ans["answer"], ctx["citations"], chunks, float(T.get("claim_support_min")))
                        claims_info = dict(ci2, refined=True, before_groundedness=claims_info["groundedness"])
                        unsup = [c["text"][:120] for c in claims_info["claims"] if c.get("verdict") == "unsupported"]
                    except (LLMError, ValueError) as e:
                        st.note(refine_error=str(e)[:200])
                if claims_info["unsupported"] and not refined:
                    ans["answer"], n_marked = apply_claim_policy(ans["answer"], claims_info["claims"], "drop" if policy == "drop" else "mark")
                    claims_info["policy_applied"] = n_marked
                if claims_info["groundedness"] < float(T.get("claim_min_groundedness")):
                    ans["answer"] = "> ⚠ groundedness %.2f — 일부 문장이 근거로 확인되지 않습니다.\n\n" % claims_info["groundedness"] + ans["answer"]
                st.note(n_factual=claims_info["n_factual"], supported=claims_info["supported"], partial=claims_info["partial"], unsupported=claims_info["unsupported"],
                        groundedness=claims_info["groundedness"], citation_precision=claims_info.get("citation_precision"), refined=refined,
                        unsupported_samples=unsup[:3])
                st.debug(claims=[{k: c.get(k) for k in ("i", "verdict", "cites", "support", "factual")} for c in claims_info["claims"]])
        elif t.claim_check:
            prof.skipped("claim_check", "answer mode %s" % ans["mode"])
        else:
            prof.skipped("claim_check")
        ans["cited"] = sorted(set(int(n) for n in __import__("re").findall(r"\[C(\d+)\]", ans["answer"])))

        # ---- 결과 ----
        hit_dicts = self._hit_dicts(final, chunks, ctx, fts_snips)
        groundedness = claims_info["groundedness"] if claims_info else None
        result: Dict[str, Any] = {
            "query": q, "answer": ans["answer"], "answer_mode": ans["mode"], "cited": ans["cited"], "model": ans.get("model"),
            "hits": hit_dicts, "route": route_info, "graph": {k: v for k, v in graph_res.items() if k != "chunks"},
            "plan": plan, "evidence": dict(ev or {}, llm=llm_ev, verdict=verdict) if t.evidence_check else None, "fallback": rounds,
            "claims": {k: v for k, v in (claims_info or {}).items() if k != "claims"} if claims_info else None, "groundedness": groundedness,
            "boosts": R.get("boost_stats"),
            "config": {"toggles": dict(t.__dict__), "weights": R["weights"], "llm": al.name, "llm_model": al.model,
                       "rerank_llm": p.llm_for("rerank").describe().get("model"), "embedder": p.embedder.name,
                       "tuning": _tuning.T.to_dict(), "alt_queries": alt_llm, "round": R["cfg"].to_dict()},
            "cached": False, "precomputed": False,
        }
        if log and t.evolve_capture:
            from .evolve import capture_query
            with prof.stage("evolve_capture") as st:
                result["proposals"] = capture_query(p, q, result, final)
                st.note(proposals=result["proposals"])
        else:
            prof.skipped("evolve_capture", "log off" if not log else "disabled")
        trace = prof.finish()
        result["ms"] = trace["ms"]
        result["tokens"] = trace["summary"]["llm"]
        result["run_id"] = trace.get("run_id")
        if log and t.evolve_capture:
            result["query_id"] = p.store.log_query(q, result["config"], [h.chunk_id for h in final], ans["answer"],
                                                   {"top_fused": final[0].fused if final else 0, "n_hits": len(final), "verdict": verdict, "groundedness": groundedness}, trace)
        result["request_id"] = p.store.log_request("query", q, trace, {k: v for k, v in result.items() if k not in ("hits",)}, result["config"], None, keep=s.keep_requests)
        # ---- 포렌식 자동 ----
        need_forensic = t.forensic_auto and (verdict != "sufficient" or ans["mode"] == "insufficient" or
                                             (groundedness is not None and groundedness < float(T.get("claim_min_groundedness"))))
        if need_forensic:
            from . import forensic as _fx
            try:
                diag = _fx.diagnose(trace, result, s)
                fid = _fx.record(p.store, result["request_id"], result["run_id"], q, verdict if ans["mode"] != "insufficient" else "insufficient", groundedness, diag, "auto")
                result["forensic"] = {"id": fid, "severity": diag["severity"], "findings": len(diag["findings"]), "suggestions": len(diag["suggestions"])}
                _log.log("warning", "forensic recorded #%s (%s)" % (fid, diag["severity"]), "query", request_id=result["request_id"], verdict=verdict)
            except Exception as e:
                result["forensic"] = {"error": str(e)[:200]}
        if log and t.evolve_capture:
            from . import memory as _mem
            try:
                _mem.record_episode(p.store, result["request_id"], q, "query", ans["mode"] if ans["mode"] != "llm" else verdict, [h.chunk_id for h in final],
                                    keywords(q)[:6], {"groundedness": groundedness, "fallback_rounds": len(rounds)})
            except Exception:
                pass
        if key:
            p._qcache[key] = {"result": dict(result), "trace": trace}
            while len(p._qcache) > max(1, s.query_cache_size):
                p._qcache.popitem(last=False)
        if pkey and t.precompute and ans["mode"] != "insufficient":
            from . import precompute as _pc
            try:
                _pc.put_cached(p.store, pkey, q, result, source="query")
            except Exception:
                pass
        return result, trace

    # ---------------------------------------------------------------- 보조
    def _expand_now(self, prof: Profiler, q_search: str, s: Any, T: Any) -> List[str]:
        rl = self.pipe.llm_for("expand")
        with prof.stage("query_expand", model=rl.model, n=T.get("query_expand_n"), reason="fallback") as st:
            try:
                r = rl.complete(_prompts.get("expand"), "N=%d\n%s" % (T.get("query_expand_n"), q_search), max_tokens=400, effort=s.role_llm("expand")["effort"], json_mode=True)
                data = parse_json(r["text"]) or {}
                alts = [x for x in (data.get("queries") or []) + (data.get("sub_queries") or []) if isinstance(x, str) and x.strip() and x.strip() != q_search][:5]
                st.note(alt_queries=alts, usage=r.get("usage"))
                return alts
            except (LLMError, ValueError) as e:
                st.note(error=str(e)[:200])
                return []

    def _assess(self, prof: Profiler, q_search: str, R: Dict[str, Any], stage: str = "evidence_check"):
        p = self.pipe
        s, t, T = p.s, p.s.toggles, _tuning.T
        with prof.stage(stage, llm=bool(t.evidence_check_llm)) as st:
            ev = _ev.assess(q_search, R["final"], R["ctx"], R["chunks"], T)
            llm_ev = None
            if t.evidence_check_llm and ev["verdict"] != "sufficient":
                vl = p.llm_for("verify")
                if vl.available:
                    try:
                        llm_ev = _ev.assess_llm(vl, q_search, R["ctx"], s.role_llm("verify")["effort"])
                        st.sample(llm_response=llm_ev.get("raw"))
                    except (LLMError, ValueError) as e:
                        st.note(llm_error=str(e)[:200])
            verdict = _ev.merge_verdicts(ev, llm_ev)
            st.note(verdict=verdict, heuristic=ev["verdict"], score=ev["score"], reasons=ev["reasons"], signals=ev["signals"],
                    missing=(llm_ev or {}).get("missing"), followups=(llm_ev or {}).get("followup_queries"))
        return ev, llm_ev, verdict

    def _compress(self, prof: Profiler, q_search: str, ctx: Dict[str, Any], chunks: Dict[str, Any], p, s) -> Dict[str, Any]:
        al = p.llm_for("answer")
        with prof.stage("evidence_compress", model=al.model, chars_before=ctx["chars"]) as st:
            try:
                r = al.complete(_prompts.get("compress"), "## 질문\n%s\n\n## 문단\n%s" % (q_search, ctx["text"]), max_tokens=max(800, ctx["chars"] // 2), effort="low")
                text = r["text"].strip()
                if text and len(text) < ctx["chars"] and "[C1]" in text:
                    st.note(chars_after=len(text), saved=ctx["chars"] - len(text), usage=r.get("usage"))
                    return dict(ctx, text=text, chars=len(text))
                st.note(kept_original=True, reason="compressed text lost citations or not shorter")
            except (LLMError, ValueError) as e:
                st.note(error=str(e)[:200])
        return ctx

    def _hit_dicts(self, final: List[Hit], chunks: Dict[str, Any], ctx: Dict[str, Any], fts_snips: Dict[str, str]) -> List[Dict[str, Any]]:
        cite_n = {c["chunk_id"]: c["n"] for c in ctx["citations"]}
        meta = self.pipe.store.doc_meta_map()
        out = []
        for h in final:
            c = chunks.get(h.chunk_id)
            dm = meta.get(c["doc_id"]) if c else None
            out.append(dict(h.to_dict(), n=cite_n.get(h.chunk_id), in_context=h.chunk_id in cite_n, doc_id=c["doc_id"] if c else "",
                            heading=c["heading"] if c else "", text=c["text"] if c else "", snippet=fts_snips.get(h.chunk_id, ""),
                            doc_type=(dm or {}).get("doc_type"), ext_id=(dm or {}).get("ext_id"), date=(dm or {}).get("date")))
        for cit in ctx["citations"]:
            if cit.get("kind") == "neighbor" and cit["chunk_id"] in chunks:
                c = chunks[cit["chunk_id"]]
                dm = meta.get(c["doc_id"]) or {}
                out.append({"chunk_id": cit["chunk_id"], "scores": {}, "ranks": {}, "fused": 0.0, "why": ["neighbor"], "rerank": None, "boosts": {},
                            "n": cit["n"], "in_context": True, "doc_id": c["doc_id"], "heading": c["heading"], "text": c["text"], "snippet": "",
                            "doc_type": dm.get("doc_type"), "ext_id": dm.get("ext_id"), "date": dm.get("date")})
        out.sort(key=lambda d: (d["n"] is None, d["n"] or 0))
        return out

    # ---------------------------------------------------------------- 검색 라운드
    def _retrieve(self, prof: Profiler, q: str, q_search: str, qr: Optional[Dict[str, Any]], route_info: Dict[str, Any], weights: Dict[str, float],
                  alt_llm: List[str], pin_info: Dict[str, Any], scope: Optional[Dict[str, Any]], feedback_w: Dict[str, float], cfg: RoundConfig) -> Dict[str, Any]:
        p = self.pipe
        s, t, T = p.s, p.s.toggles, _tuning.T
        store = p.store
        lists: Dict[str, List[Tuple[str, float]]] = {}
        w = dict(weights)
        k_fts, k_vec, k_gr = int(s.top_k_fts * cfg.k_mult), int(s.top_k_vector * cfg.k_mult), int(s.top_k_graph * cfg.k_mult)
        fts_snips: Dict[str, str] = {}
        syn = store.synonyms()
        rule_alts: List[Tuple[str, float, str]] = list(qr["alt_queries"]) if qr else []
        related: List[Tuple[str, float]] = [(txt, wgt * cfg.related_mult) for txt, wgt in (qr["related"] if qr else [])]
        alt_all: List[Tuple[str, float, str]] = rule_alts + [(a, float(T.get("query_expand_w")), "llm") for a in (alt_llm if cfg.use_llm_alts else [])] + list(cfg.extra_queries)
        # 중복 제거
        seen = {q_search}
        alts: List[Tuple[str, float, str]] = []
        for txt, wgt, kind in alt_all:
            if txt and txt not in seen:
                seen.add(txt)
                alts.append((txt, wgt, kind))
        alts = alts[:6]
        if t.fts:
            # 원 질의는 항상 그대로 검색 (규칙/LLM 확장은 별도 리스트로 융합 → 원 질의 신호를 희석하지 않음)
            rows = fts_search(store, q_search, k_fts, syn, prof, mode=cfg.fts_mode)
            lists["fts"] = [(cid, sc) for cid, sc, _ in rows]
            fts_snips = {cid: sn for cid, _, sn in rows}
            if qr and qr["fts_query"]:
                rrows = fts_search(store, q_search, k_fts, {}, prof, rule_match=qr["fts_query"], stage_name="fts_search_rules", mode="or")
                lists["fts_rule"] = [(cid, sc) for cid, sc, _ in rrows]
                w["fts_rule"] = w.get("fts", 1.0) * float(T.get("syn_w"))
                for cid, _, sn in rrows:
                    fts_snips.setdefault(cid, sn)
                if t.profile_expansion:
                    base_ids = {cid for cid, _, _ in rows}
                    added = [cid for cid, _, _ in rrows if cid not in base_ids]
                    with prof.stage("expansion_profile") as st:
                        st.note(baseline_hits=len(rows), rule_hits=len(rrows), added_by_rules=len(added), added_ids=added[:8], fired=[f["matched"] for f in qr["fired"]])
            for i, (aq, wgt, kind) in enumerate(alts):
                arows = fts_search(store, aq, k_fts, syn, prof, stage_name="fts_search_alt")
                lists["fts_alt%d" % (i + 1)] = [(cid, sc) for cid, sc, _ in arows]
                w["fts_alt%d" % (i + 1)] = w.get("fts", 1.0) * wgt
            for i, (rq, wgt) in enumerate(related[:3]):
                rrows = fts_search(store, rq, max(3, k_fts // 2), {}, prof, stage_name="fts_search_related")
                lists["fts_rel%d" % (i + 1)] = [(cid, sc) for cid, sc, _ in rrows]
                w["fts_rel%d" % (i + 1)] = w.get("fts", 1.0) * wgt
        else:
            prof.skipped("fts_search")
        if t.vector:
            lists["vector"] = vector_search(store, p.embedder, q_search, k_vec, prof)
            for i, (aq, wgt, kind) in enumerate(alts[:3]):
                lists["vector_alt%d" % (i + 1)] = vector_search(store, p.embedder, aq, k_vec, prof)
                w["vector_alt%d" % (i + 1)] = w.get("vector", 1.0) * wgt
        else:
            prof.skipped("vector_search")
        graph_res: Dict[str, Any] = {}
        if t.graph:
            seeds = [(e, sc) for e, sc in route_info.get("entities", [])] if route_info.get("entities") else []
            if qr and qr["seeds"]:
                idx = {n: e["entity_id"] for e in store.entity_index() for n in e["names"]}
                for name in qr["seeds"]:
                    eid = idx.get(name.lower())
                    if eid and all(eid != e for e, _ in seeds):
                        seeds.append((eid, 5.0))
            graph_res = graph_search(store, q_search, k_gr, s.graph_hops + cfg.hops_extra, prof, seeds or None)
            lists["graph"] = graph_res["chunks"]
        else:
            prof.skipped("graph_search")
        if t.doc_vector:
            from . import precompute as _pc
            lists["doc_vector"] = _pc.doc_vector_search(store, p.embedder, q_search, max(3, k_vec // 2), prof)
            w["doc_vector"] = float(T.get("channel_w_doc_vector"))
        else:
            prof.skipped("doc_vector_search")
        if cfg.mcp_enrich and t.mcp_sources:
            from . import mcp_client as _mcp
            with prof.stage("mcp_enrich") as st:
                try:
                    en = _mcp.enrich(s, q_search, limit=5)
                    st.note(results=len(en), sources=sorted({e.get("source") for e in en}))
                    st.debug(items=en[:5])
                    graph_res["mcp_enrich"] = en
                except Exception as e:
                    st.note(error=str(e)[:200])
        # ---- 융합 ----
        method = T.get("fusion_method")
        with prof.stage("rrf_fuse", rrf_k=s.rrf_k, method=method, weights=w, sources={n: len(v) for n, v in lists.items()}) as st:
            hits, fmeta = _fusion.fuse(lists, w, s.rrf_k, method, T.get("fusion_multi_bonus"))
            st.note(**fmeta, top=[(h.chunk_id, round(h.fused, 4), h.why) for h in hits[:6]])
        # pin 주입 (후보에 없으면 추가)
        have = {h.chunk_id for h in hits}
        for cid in pin_info.get("inject", []):
            if cid not in have:
                h = Hit(cid)
                h.fused = 0.5 / (s.rrf_k + 1)
                h.why = ["pin"]
                hits.append(h)
                have.add(cid)
        chunks = store.get_chunks([h.chunk_id for h in hits])
        # ---- 부스트 ----
        prov_chunks: Dict[str, str] = {}
        for r in graph_res.get("relations", []) or []:
            if r.get("chunk_id") and r.get("provenance"):
                prov_chunks.setdefault(r["chunk_id"], r["provenance"])
        time_mode = T.get("time_mode") if cfg.time_filter else "boost"
        with prof.stage("boost", time_mode=time_mode if scope else None, doc_types=route_info.get("doc_types"), pins=len(pin_info.get("weights", {})),
                        feedback=len(feedback_w), exclude=(qr["exclude"] if (qr and cfg.exclude) else [])) as st:
            stats = _fusion.apply_boosts(
                hits, chunks, store.doc_meta_map(), time_scope=scope, time_mode=time_mode, time_w=float(T.get("time_boost_w")),
                recency_half_life_days=int(T.get("recency_half_life_days")), doc_type_boost=parse_weight_map(T.get("doc_type_boost"), 1.0) if cfg.type_boost else {},
                router_doc_types=route_info.get("doc_types") if cfg.type_boost else [], pinned=pin_info.get("weights") or {}, pin_w=float(T.get("pin_boost")),
                provenance_chunks=prov_chunks, provenance_w=float(T.get("provenance_boost")), feedback=feedback_w, feedback_w=float(T.get("feedback_boost_w")),
                exclude_terms=(qr["exclude"] if (qr and cfg.exclude) else []), exclude_penalty=float(T.get("exclude_penalty")))
            if scope and time_mode == "filter" and not hits:
                st.note(filter_relaxed=True)
                # filter 로 0건 → boost 로 완화 재적용은 다음 라운드(wide) 에서; 여기서는 통계만
            st.note(**stats, top=[(h.chunk_id, round(h.fused, 4), h.boosts) for h in hits[:5]])
        # ---- 리랭크 · 컨텍스트 ----
        if t.rerank:
            rl = p.llm_for("rerank")
            meta = store.doc_meta_map()
            doc_tokens = {d: "%s %s" % (m.get("ext_id") or "", m.get("doc_type") or "") for d, m in meta.items() if m.get("ext_id") or m.get("doc_type")}
            hits = rerank(hits, chunks, q_search, rl, s.top_k_final, prof, s.role_llm("rerank")["effort"], use_llm=t.rerank_llm,
                          n_cands=int(s.rerank_candidates * cfg.k_mult), chunk_chars=s.rerank_chunk_chars, settings=s, doc_tokens=doc_tokens)
        else:
            prof.skipped("rerank")
        final = hits[: s.top_k_final]
        with prof.stage("context", max_chars=s.context_max_chars, trim=t.context_trim, dedupe=t.dedupe_hits, neighbors_extra=cfg.neighbors_extra) as st:
            ctx = build_context(final, chunks, graph_res if t.graph else None, s.context_max_chars, query=q_search, trim=t.context_trim, dedupe=t.dedupe_hits,
                                chunk_chars=s.context_chunk_chars, stage=st, store=store, neighbors=(T.get("context_neighbors") + cfg.neighbors_extra))
            st.note(chars=ctx["chars"], citations=len(ctx["citations"]))
        return {"lists": lists, "weights": w, "hits": hits, "final": final, "chunks": chunks, "ctx": ctx, "graph_res": graph_res, "fts_snips": fts_snips,
                "cfg": cfg, "boost_stats": stats}
