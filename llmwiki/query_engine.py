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

from .answer import (build_context, generate_answer, check_claims, check_claims_llm, apply_claim_policy, refine_answer, guard_repeat,
                     normalize_answer_mode, result_type_of, normalize_output_mode, render_candidates_table, OUTPUT_STOP_AFTER,
                     RESULT_CANDIDATES_FUSED, RESULT_CANDIDATES_RERANKED, RESULT_CONTEXT)
from .profiler import Profiler
from .providers import parse_json, LLMError
from . import providers as _providers
from .retrieval import fts_search, vector_search, graph_search, rerank, route, Hit, parse_weight_map
from .textutil import keywords, words, normalize_token
from . import evidence as _ev
from . import fusion as _fusion
from . import logging_setup as _log
from . import prompts as _prompts
from . import timeparse as _time
from . import tuning as _tuning

# LLM 역할이 최종 실패했을 때 파이프라인이 자동으로 타는 대체 경로 (llm_report 설명용)
ROLE_FALLBACK = {"answer": "추출식 답변(원문 문장 구조화)으로 대체", "rerank": "로컬 휴리스틱 리랭크로 대체", "expand": "LLM 질의 확장 생략(규칙 확장만)",
                 "verify": "휴리스틱 근거/claim 판정만 사용", "forensic": "휴리스틱 포렌식 소견만", "extract": "규칙 기반 그래프만", "summary": "커뮤니티 요약 생략",
                 "review": "LLM 리뷰 생략", "fusion": "융합 뒤 LLM 검토 생략(융합·부스트 순위 유지)", "select": "리랭크 뒤 LLM 선택 생략(리랭크 순위 유지)"}


def llm_report_from_incidents(incidents: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """providers 의 incident 목록 → 결과에 실을 보고서 {failures, fallbacks, summary}."""
    if not incidents:
        return None
    fallbacks = []
    lines = []
    for f in incidents:
        fb = ROLE_FALLBACK.get(f.get("role"), "해당 단계 생략")
        fallbacks.append({"role": f.get("role"), "fallback": fb})
        last = (f.get("errors") or ["?"])[-1]
        lines.append("%s(%s/%s) %d회 시도 후 실패%s — %s → %s" % (f.get("role"), f.get("provider"), f.get("model"), f.get("attempts"),
                                                               " (timeout %ss)" % f.get("timeout_s") if "timeout" in str(last).lower() else "", str(last)[:160], fb))
    return {"failures": incidents, "fallbacks": fallbacks, "summary": lines}

DOC_TYPE_HINTS = {
    "issue": ("이슈", "issue", "문제", "장애", "결함", "버그", "현상", "원인"),
    "cl": ("cl", "change list", "체인지리스트", "수정 반영", "커밋", "패치"),
    "sw_design": ("설계", "아키텍처", "code map", "코드맵", "구조", "인터페이스"),
    "hw_design": ("hw", "하드웨어", "타이밍", "레지스터", "revision", "리비전", "rev"),
    "coding_rule": ("코딩 규칙", "코딩룰", "coding rule", "규칙", "리뷰 규칙"),
    "weekly_report": ("주간", "weekly", "보고", "업무 요약"),
    "tc_list": ("tc", "테스트 케이스", "검증 항목", "test case"),
}


def _row_dict(c: Any) -> Dict[str, Any]:
    """청크 행(sqlite3.Row | dict | None) → dict. Row 는 .get 이 없다."""
    if c is None:
        return {}
    return c if isinstance(c, dict) else dict(c)


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
        # output_mode (§2.3): None = 끝까지 · "boost" = 융합·부스트 뒤 멈춤(리랭크 전) · "rerank" = 리랭크 뒤 · "context" = 컨텍스트까지
        self.stop_after: Optional[str] = None
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
        self.rounds: List[Dict[str, Any]] = []     # 검색 라운드 내부 상태 캡처 (forensic expect 가 단계별 순위를 읽는다)
        self.record_request = True                 # False 면 requests 테이블에 남기지 않는다 (forensic expect 의 재실행)
        self.capture: Optional[Any] = None         # rerun.Capture — 단계 재실행용 중간 결과 수집 (None 이면 수집 안 함)
        self.resume: Optional[Any] = None          # rerun.Resume — 저장해 둔 중간 결과를 재생하며 도는 중

    # ---------------------------------------------------------------- 진입
    def run(self, q: str, log: bool = True, debug: Optional[int] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        p = self.pipe
        s, t, T = p.s, p.s.toggles, _tuning.T
        if debug is None and t.analysis_mode:
            debug = max(2, int(s.debug_level or 0))      # 상세 분석 모드: 단계별 debug + 프롬프트/응답 샘플까지 남긴다
        prof = Profiler("query", debug=s.debug_level if debug is None else debug, log=t.log_stages)
        _providers.reset_incidents()
        self.rounds = []
        # 단계 재실행용 중간 결과 수집. 재실행 결과도 **새 request_id 로** 다시 저장한다 —
        # 원본 파일은 건드리지 않으면서, 방금 재실행한 결과에서 또 이어서 실험할 수 있다.
        if self.capture is None and self.record_request and getattr(t, "rerun_capture", False):
            from . import rerun as _rerun
            self.capture = _rerun.Capture(q, build_version=str(p.store.build_version()), run_id=prof.run_id)
        _log.log("info", "query: %s" % q[:200], "query")
        with prof.stage("sync_index") as st:
            st.note(build_version=p.store.build_version(), reloaded_caches=p.sync_with_db())
        roles = ["answer", "rerank"]
        if t.query_expand or t.query_decompose:
            roles.append("expand")
        if t.evidence_check_llm or t.claim_check_llm:
            roles.append("verify")
        if getattr(t, "llm_after_fusion", False):
            roles.append("fusion")
        if getattr(t, "llm_after_rerank", False):
            roles.append("select")
        p._ensure_providers(prof, tuple(roles))
        # 답변 모드 (grounded | best_effort) — 모르는 값은 grounded 로 (Web overrides 가 임의 문자열을 보낼 수 있다)
        answer_mode = normalize_answer_mode(getattr(s, "answer_mode", "grounded"))
        # 출력 모드 (answer | fused | reranked | context, §2.3) — answer 가 아니면 그 지점에서 멈추고 중간 산출물을 그대로 돌려준다.
        output_mode = normalize_output_mode(getattr(s, "output_mode", "answer"))
        is_answer = output_mode == "answer"

        # ---- 캐시: query_cache (메모리) → precompute (영속) ----
        # 단계 재실행 중에는 캐시를 **읽지 않는다**. 재실행은 "설정을 바꿔 가며 뒤 단계를 다시 본다" 는 것인데,
        # 캐시가 맞으면 아무 단계도 돌지 않은 예전 답이 그대로 돌아와 "눌러도 그대로다" 가 된다.
        # output_mode ≠ answer 도 캐시를 읽지 않는다 — 캐시에는 완성된 답이 들어 있고(키에 output_mode 가 없다), 중간 산출물은 LLM 없이 싸게 다시 만든다.
        if self.resume is not None:
            prof.skipped("cache_hit", "단계 재실행 — 캐시를 쓰지 않는다")
        elif not is_answer:
            prof.skipped("cache_hit", "output_mode=%s — 캐시를 쓰지 않는다" % output_mode)
        key = p._cache_key(q) if (t.query_cache and self.resume is None and is_answer) else None
        cached = p.qcache_get(key) if key else None
        if cached:
            with prof.stage("cache_hit", key=key[:12]) as st:
                st.note(saved_ms=cached["result"].get("ms"), saved_tokens=cached["trace"].get("summary", {}).get("llm", {}).get("total_tokens", 0))
            trace = prof.finish()
            res = dict(cached["result"], cached=True, ms=trace["ms"], query_id=None, proposals=[], run_id=trace.get("run_id"))
            res["request_id"] = p.store.log_request("query", "[cache] " + q, trace, {"cached_from": cached["result"].get("request_id")}, res.get("config"), None, keep=s.keep_requests)
            return res, trace
        pkey = None
        if t.precompute and self.resume is None and is_answer:
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
        # 단계 재실행(rerun): 재시작점이 '계획' 뒤라면 계획 단계들은 **계산하지 않고 저장해 둔 값을 재생**한다.
        # 특히 query_expand 는 LLM 호출이라 여기서 아끼는 시간과 토큰이 크다.
        plan_replay: Optional[Dict[str, Any]] = None
        if self.resume is not None:
            plan_replay = self.resume.get("plan")
        plan: Dict[str, Any] = {}
        q_search = q
        scope = None
        if plan_replay is not None:
            q_search = str(plan_replay.get("q_search") or q)
            scope = plan_replay.get("scope")
            prof.replayed("time_scope", query=q_search, expr=(scope or {}).get("expr") if scope else None)
        elif t.time_scope:
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
        if plan_replay is not None:
            qr = plan_replay.get("qr")
            prof.replayed("query_rules", fired=len((qr or {}).get("fired") or []), alt=len((qr or {}).get("alt_queries") or []))
        elif t.query_rules:
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
        if plan_replay is not None:
            route_info = dict(plan_replay.get("route_info") or {})
            weights = dict(route_info.get("weights") or weights)
            prof.replayed("router", intent=route_info.get("intent"), doc_types=route_info.get("doc_types"))
        elif t.router:
            with prof.stage("router") as st:
                route_info = route(q_search, p.store)
                route_info["doc_types"] = doc_type_hints(q)
                if t.router_llm:
                    rl = p.llm_for("expand")
                    if rl.available:
                        try:
                            r = rl.complete(_prompts.get("router"), q, max_tokens=s.role_max_tokens("router", 200),
                                            effort=s.role_llm("expand")["effort"], json_mode=True)
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
        if plan_replay is not None:
            alt_llm = [x for x in (plan_replay.get("alt_llm") or []) if isinstance(x, str)]
            sub_queries = [x for x in (plan_replay.get("sub_queries") or []) if isinstance(x, str)]
            prof.replayed("query_expand", alt_queries=alt_llm, sub_queries=sub_queries)
        elif t.query_expand or t.query_decompose:
            rl = p.llm_for("expand")
            if rl.available:
                with prof.stage("query_expand", model=rl.model, n=T.get("query_expand_n"), decompose=t.query_decompose) as st:
                    try:
                        r = rl.complete(_prompts.get("expand"), "N=%d\n%s" % (T.get("query_expand_n"), q_search),
                                        max_tokens=s.role_max_tokens("expand", 400),
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
        if plan_replay is not None:
            pin_info = dict(plan_replay.get("pin_info") or pin_info)
            prof.replayed("pins", matched=len(pin_info.get("matched") or []), inject=len(pin_info.get("inject") or []))
        elif t.pins:
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

        if self.capture is not None:
            # 계획은 **통째로** 남긴다. plan["query_rules"] 는 화면용으로 추려진 것이라
            # 재생에 필요한 fts_query/seeds 가 빠져 있다.
            self.capture.put("plan", {"q_search": q_search, "scope": scope, "qr": qr, "route_info": route_info,
                                      "alt_llm": alt_llm, "sub_queries": sub_queries, "pin_info": pin_info,
                                      "feedback_w": feedback_w})

        # ---- 검색 라운드 ----
        base_cfg = RoundConfig(stop_after=OUTPUT_STOP_AFTER.get(output_mode))
        if self.resume is not None and self.resume.wants("ctx"):
            # '답변부터' · '검증부터' — 검색과 컨텍스트 구성을 통째로 재생한다 (여기가 가장 크게 아끼는 지점)
            R = self._replay_retrieval(prof, base_cfg, q_search)
        else:
            R = self._retrieve(prof, q, q_search, qr, route_info, weights, alt_llm + sub_queries, pin_info, scope, feedback_w, base_cfg)
        t_start = time.perf_counter()

        # ---- 근거 판정 + fallback ----
        ev: Optional[Dict[str, Any]] = None
        llm_ev: Optional[Dict[str, Any]] = None
        verdict = "sufficient"
        rounds: List[Dict[str, Any]] = []
        replay_ctx = bool(self.resume is not None and self.resume.wants("ctx"))
        if output_mode in ("fused", "reranked"):
            # 컨텍스트를 만들지 않았으므로 판정할 것이 없다 (context 모드는 정상적으로 판정·fallback 까지 간다)
            prof.skipped("evidence_check", "output_mode=%s" % output_mode)
            if t.fallback_loop:
                prof.skipped("fallback", "output_mode=%s" % output_mode)
        elif t.evidence_check:
            ev, llm_ev, verdict = self._assess(prof, q_search, R, stage="evidence_check")
            # 재생 중에는 fallback 루프를 돌지 않는다: fallback 은 **검색을 다시 하는** 것이라
            # "앞 단계를 그대로 두고 뒤만 바꿔 본다" 는 재실행의 목적과 어긋난다.
            if replay_ctx and verdict != "sufficient" and t.fallback_loop:
                prof.skipped("fallback", "단계 재실행(재생) 중 — 검색을 다시 하지 않는다")
            elif verdict != "sufficient" and t.fallback_loop:
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
        if self.capture is not None:
            self.capture.put("evidence", {"verdict": verdict, "ev": ev, "llm_ev": llm_ev, "rounds": rounds})
        # ---- 답변 ----
        al = p.llm_for("answer")
        answer_replay = self.resume.get("answer") if self.resume is not None else None
        cands_out: List[Dict[str, Any]] = []
        if not is_answer:
            # output_mode=fused|reranked|context — 답변 LLM 을 부르지 않는다. answer 칸에는 세 창구(CLI/Web/MCP)가 같은 본문을 보이도록
            # 후보 표(마크다운) 또는 컨텍스트 본문을 넣는다. claim 검증·재작성은 ans.mode ≠ llm 이라 아래에서 자연히 건너뛴다.
            prof.skipped("answer_llm", "output_mode=%s — 답변을 만들지 않는다" % output_mode)
            if output_mode == "context":
                ans = {"answer": ctx.get("text") or "", "mode": "context", "result_type": RESULT_CONTEXT, "cited": [], "model": None}
            else:
                cands_out = self._candidates(R, int(T.get("output_chunk_chars") or 0))
                title = "## %s 후보 %d건 (%s)" % ("리랭크" if output_mode == "reranked" else "융합·부스트", len(cands_out),
                                              "리랭크 뒤 · 문서 확장·컨텍스트 전" if output_mode == "reranked" else "리랭크 전")
                ans = {"answer": render_candidates_table(cands_out, title), "mode": "candidates",
                       "result_type": RESULT_CANDIDATES_RERANKED if output_mode == "reranked" else RESULT_CANDIDATES_FUSED, "cited": [], "model": None}
        elif answer_replay is not None:
            # '검증부터' — 답변을 다시 만들지 않고 그때의 답변을 그대로 쓴다 (검증 설정만 바꿔 볼 때)
            ans = dict(answer_replay)
            prof.replayed("answer_llm", chars=len(str(ans.get("answer") or "")), mode=ans.get("mode"), model=ans.get("model"))
        elif t.evidence_check and verdict == "insufficient" and answer_mode == "grounded":
            # grounded 에서만 "근거가 부족하니 답하지 않는다". best_effort 는 이 분기를 타지 않고 아래에서 LLM 을 부른다
            # (프롬프트가 answer_best_effort.md 로 바뀌고 [BK] 로 배경 지식을 표시한다 — docs/ANSWER_MODES.md §4).
            with prof.stage("answer_insufficient", reasons=(ev or {}).get("reasons")) as st:
                hd = self._hit_dicts(final, chunks, ctx, fts_snips)
                text = _ev.insufficient_text(q, ev or {}, llm_ev, rounds, hd)
                ans = {"answer": text, "mode": "insufficient", "cited": [], "model": None}
                st.note(answer_chars=len(text))
            prof.skipped("answer_llm", "evidence insufficient → insufficient_data 응답 (LLM 호출 생략)")
        else:
            if t.evidence_compress and al.available and t.llm_answer:
                ctx = self._compress(prof, q_search, ctx, chunks, p, s)
            ans = generate_answer(q, ctx, al, t.llm_answer, prof, s.role_llm("answer")["effort"], chunks, final,
                                  max_tokens=s.role_max_tokens("answer", s.answer_max_tokens),
                                  meta=p.store.doc_meta_map(), graph=graph_res,
                                  mode=answer_mode, verdict=verdict, reasons=(ev or {}).get("reasons"),
                                  degrade=bool(getattr(t, "degrade_on_llm_failure", True)))
            if verdict == "weak" and ans["mode"] == "llm":
                ans["answer"] = "> ⚠ 근거가 약합니다 (%s). 아래 답변은 제한된 근거에 기반합니다.\n\n" % "; ".join((ev or {}).get("reasons") or []) + ans["answer"]
        if self.capture is not None:
            self.capture.put("answer", {k: ans.get(k) for k in ("answer", "mode", "cited", "model")})

        # ---- claim check ----
        claims_info: Optional[Dict[str, Any]] = None
        if t.claim_check and ans["mode"] == "llm":
            with prof.stage("claim_check", llm=bool(t.claim_check_llm), policy=T.get("claim_policy")) as st:
                claims_info = check_claims(ans["answer"], ctx["citations"], chunks, float(T.get("claim_support_min")))
                if t.claim_check_llm and claims_info["n_factual"]:
                    vl = p.llm_for("verify")
                    if vl.available:
                        try:
                            li = check_claims_llm(vl, q, claims_info["claims"], ctx["citations"], chunks,
                                                  s.role_llm("verify")["effort"], max_tokens=s.role_max_tokens("verify", 1500))
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
                        rr = refine_answer(al, q, ctx, ans["answer"], claims_info["claims"], s.role_llm("answer")["effort"],
                                           s.role_max_tokens("answer", s.answer_max_tokens))
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
            prof.skipped("claim_check", ("output_mode=%s" % output_mode) if not is_answer else ("answer mode %s" % ans["mode"]))
        else:
            prof.skipped("claim_check")
        # 재작성(refine)·claim 정책이 답변을 다시 쓸 수 있으므로 마지막으로 한 번 더 반복 루프를 본다.
        # 이미 잘린 답변에는 반복이 남아 있지 않으므로 두 번 실행해도 결과가 달라지지 않는다.
        if ans["mode"] == "llm" and _tuning.T.get("answer_repeat_guard"):
            ans["answer"], _rp = guard_repeat(ans["answer"], int(_tuning.T.get("answer_repeat_min_chars")),
                                              int(_tuning.T.get("answer_repeat_times")))
            if _rp:
                ans["repeat_loop"] = _rp
        ans["cited"] = sorted(set(int(n) for n in __import__("re").findall(r"\[C(\d+)\]", ans["answer"])))

        # ---- LLM 실패 보고 (재시도 후에도 실패한 호출과 그때 탄 대체 경로) ----
        llm_report = llm_report_from_incidents(_providers.drain_incidents())
        if llm_report and t.llm_failure_report:
            if any(f.get("role") == "answer" for f in llm_report["failures"]) and ans["mode"] != "llm":
                ans["answer"] = ("> ⚠ LLM 실행 보고: %s\n> 지금까지의 검색 결과(근거 %d건)로 아래 답변을 구성했습니다. 설정: agents.json timeout_s/retries · config.json llm_timeout/llm_retries.\n\n"
                                 % (" · ".join(llm_report["summary"]), len(ctx["citations"]))) + ans["answer"]
            _log.log("warning", "llm failures in query: %s" % "; ".join(llm_report["summary"]), "query")

        # ---- 결과 ----
        hit_dicts = self._hit_dicts(final, chunks, ctx, fts_snips)
        groundedness = claims_info["groundedness"] if claims_info else None
        # 규칙 효과 누적: 어느 확장 규칙이 실제로 컨텍스트에 기여했나 (llmwiki/ruleeffect.py, kv 한 줄)
        rule_effect = None
        if qr and (qr.get("fired") or []) and (R.get("rule_src") or {}):
            try:
                from . import ruleeffect as _re
                rule_effect = _re.record(p.store, qr["fired"], R.get("rule_src") or {}, hit_dicts, ans.get("answer") or "")
            except Exception as e:      # 관측용이라 질의를 실패시키지 않는다
                rule_effect = {"error": str(e)[:120]}
        result: Dict[str, Any] = {
            "query": q, "answer": ans["answer"], "answer_mode": ans["mode"], "cited": ans["cited"], "model": ans.get("model"),
            # result_type: grounded | best_effort | extractive | insufficient | error | candidates_fused | candidates_reranked | context
            "result_type": ans.get("result_type") or result_type_of(ans["mode"]), "output_mode": output_mode,
            # refs: LLM 에 실제로 전달된(컨텍스트에 들어간) 근거 목록 — output_mode=fused|reranked 는 컨텍스트가 없으므로 빈 목록
            "refs": self._refs(ctx, chunks, int(T.get("refs_preview_chars") or 0)),
            "hits": hit_dicts, "route": route_info, "graph": {k: v for k, v in graph_res.items() if k != "chunks"},
            "plan": plan, "evidence": dict(ev or {}, llm=llm_ev, verdict=verdict) if t.evidence_check else None, "fallback": rounds,
            "claims": {k: v for k, v in (claims_info or {}).items() if k != "claims"} if claims_info else None, "groundedness": groundedness,
            "boosts": R.get("boost_stats"), "doc_expand": R.get("expand_info"), "llm_report": llm_report,
            "rule_effect": rule_effect,
            "config": {"toggles": dict(t.__dict__), "weights": R["weights"], "llm": al.name, "llm_model": al.model,
                       "rerank_llm": p.llm_for("rerank").describe().get("model"), "embedder": p.embedder.name,
                       "tuning": _tuning.T.to_dict(), "alt_queries": alt_llm, "round": R["cfg"].to_dict()},
            "cached": False, "precomputed": False,
        }
        if ans.get("repeat_loop"):
            # 모델이 같은 구절을 되풀이한 고장 답변 — 상단 배너로 알리고 캐시에는 넣지 않는다
            result["repeat_loop"] = ans["repeat_loop"]
        if not is_answer:
            # 중간 산출물 (§2.3). stages 는 단계별 순서(id) — "리랭크 입력 전후" 를 한 응답에서 비교할 수 있다
            n_list = int(T.get("output_list_n") or 0)
            result["stages"] = {k: list(R.get(k) or []) for k in ("fused_order", "boost_order", "rerank_before", "final_order")}
            result["stages"]["inject"] = R.get("inject") or {}
            if output_mode == "context":
                result["context"] = {"text": ctx.get("text") or "", "chars": int(ctx.get("chars") or 0), "citations": list(ctx.get("citations") or [])}
            else:
                result["candidates"] = cands_out
                result["lists"] = {name: [[cid, round(float(sc), 6)] for cid, sc in lst[:n_list]] for name, lst in (R.get("lists") or {}).items()}
        if log and t.evolve_capture and is_answer:
            from .evolve import capture_query
            with prof.stage("evolve_capture") as st:
                result["proposals"] = capture_query(p, q, result, final)
                st.note(proposals=result["proposals"])
        else:
            prof.skipped("evolve_capture", "log off" if not log else ("output_mode=%s" % output_mode if not is_answer else "disabled"))
        trace = prof.finish()
        result["ms"] = trace["ms"]
        result["tokens"] = trace["summary"]["llm"]
        result["run_id"] = trace.get("run_id")
        if log and t.evolve_capture and is_answer:
            result["query_id"] = p.store.log_query(q, result["config"], [h.chunk_id for h in final], ans["answer"],
                                                   {"top_fused": final[0].fused if final else 0, "n_hits": len(final), "verdict": verdict, "groundedness": groundedness}, trace)
        # requests 에는 hits 전문 대신 요약(hits_brief) 을 남긴다 — forensic expect 가 '원 요청에서 이 청크가 어디까지 갔나' 를 읽는다
        result["hits_brief"] = [{"chunk_id": h["chunk_id"], "n": h.get("n"), "in_context": bool(h.get("in_context")), "why": h.get("why"),
                                 "fused": h.get("fused"), "rerank": h.get("rerank")} for h in hit_dicts]
        if self.record_request:
            result["request_id"] = p.store.log_request("query", q, trace, {k: v for k, v in result.items() if k not in ("hits",)}, result["config"], None,
                                                      keep=s.keep_requests, archive_dir=s.requests_archive_dir())
            # 질의 로그 ↔ 요청 기록 연결: Observability 질의·로그에서 바로 전체 trace 로 넘어갈 수 있게.
            if result.get("query_id"):
                p.store.set_query_request_id(int(result["query_id"]), int(result["request_id"] or 0))
        else:
            result["request_id"] = None
        # ---- 단계 재실행용 중간 결과 저장 ----
        # trace 를 이미 닫은 **뒤에** 저장한다: 저장 자체는 질의 결과가 아니므로 단계로 잡히지 않아야 하고,
        # 실패해도 (디스크 가득·권한) 질의는 성공으로 끝나야 한다.
        if self.capture is not None:
            from . import rerun as _rerun
            self.capture.put("request_id", result["request_id"])
            self.capture.put("q", q)
            self.capture.put("tuning", _tuning.T.to_dict())
            result["rerun"] = _rerun.save(s, result["request_id"], self.capture)
        elif self.resume is not None:
            result["rerun"] = self.resume.summary()
        # ---- 포렌식 자동 ---- (fused/reranked 는 판정 자체가 없으므로 대상이 아니다; context 는 근거 판정이 있으니 그대로)
        need_forensic = self.record_request and t.forensic_auto and output_mode in ("answer", "context") and \
            (verdict != "sufficient" or ans["mode"] == "insufficient" or
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
        if log and t.evolve_capture and self.record_request and is_answer:
            from . import memory as _mem
            try:
                _mem.record_episode(p.store, result["request_id"], q, "query", ans["mode"] if ans["mode"] != "llm" else verdict, [h.chunk_id for h in final],
                                    keywords(q)[:6], {"groundedness": groundedness, "fallback_rounds": len(rounds)})
            except Exception:
                pass
        # ---- 상세 분석 리포트 (analysis_mode) ----
        if t.analysis_mode and self.record_request and is_answer:
            from . import analysis as _an
            result["analysis"] = _an.run_for_result(p, result, trace)
            if result["analysis"].get("md"):
                _log.log("info", "analysis report: %s" % result["analysis"]["md"], "query", request_id=result["request_id"])
        # 반복 루프로 망가진 답변은 캐시에 넣지 않는다 — 한 번 들어가면 그 질문은 리빌드 전까지
        # 계속 같은 고장 답변을 1ms 만에 돌려준다 (2026-09-16).
        # output_mode ≠ answer 는 key/pkey 가 None 이라 여기 오지 않는다 — 중간 산출물은 캐시·사전계산에 넣지 않는다
        # (완성 답변 자리를 후보 표가 차지하면 다음 일반 질의가 그 표를 답으로 받는다).
        if key and self.record_request and not result.get("repeat_loop"):
            p.qcache_put(key, {"result": dict(result), "trace": trace})
        if pkey and t.precompute and ans["mode"] != "insufficient" and self.record_request:
            from . import precompute as _pc
            try:
                _pc.put_cached(p.store, pkey, q, result, source="query")
            except Exception:
                pass
        return result, trace

    # ---------------------------------------------------------------- 단계 재실행 (재생)
    def _replay_retrieval(self, prof: Profiler, cfg: "RoundConfig", q_search: str) -> Dict[str, Any]:
        """검색~컨텍스트 구성을 저장해 둔 결과로 대신한다 ('답변부터'·'검증부터' 재실행).

        청크 본문은 저장돼 있지 않으므로 색인에서 id 로 다시 읽는다. 색인이 바뀌어 사라진 id 는
        조용히 버리지 않고 trace 에 남긴다 — 근거가 줄어든 채 답이 나오면 원인을 알 수 없기 때문이다.
        """
        from . import rerun as _rerun
        rp = self.resume
        d = rp.data
        store = self.pipe.store
        ctx = dict(d.get("ctx") or {})
        final = _rerun.hits_from(d, "final")
        ext_chunks = {str(k): v for k, v in (d.get("ext_chunks") or {}).items() if isinstance(v, dict)}
        want = [h.chunk_id for h in final] + [str(c.get("chunk_id")) for c in (ctx.get("citations") or [])]
        want = [c for c in dict.fromkeys(want) if c and c not in ext_chunks]
        chunks: Dict[str, Any] = dict(store.get_chunks(want))
        chunks.update(ext_chunks)
        missing = [c for c in want if c not in chunks]
        # 문서 접근 제어는 **재생 경로에도** 걸어야 한다 (2026-09-19). 저장본에는 그때의 컨텍스트 본문이
        # 통째로 들어 있어서, 여기서 막지 않으면 등급이 낮은 사용자가 남의(또는 예전 admin 실행의) 근거를
        # 그대로 받아 본다. 정상 경로의 `doc_acl` 단계와 같은 판정기를 쓴다.
        af = None
        try:
            af = self.pipe.acl_filter()
        except Exception:
            af = None
        if af is not None and af.enabled:
            with prof.stage("doc_acl", role=af.role, replay=True) as st:
                final, removed = af.filter_hits(final, chunks)
                chunks = {k: v for k, v in chunks.items() if af.chunk_ok(k, af.doc_id_of(v, k))}
                cites = [c for c in (ctx.get("citations") or []) if af.chunk_ok(str(c.get("chunk_id") or ""))]
                if removed or len(cites) != len(ctx.get("citations") or []):
                    # 근거가 빠졌으면 저장된 컨텍스트 **본문을 그대로 쓸 수 없다** — 다시 조립한다.
                    from .answer import build_context
                    from . import models_catalog as _mc
                    t_ = self.pipe.s.toggles
                    ctx = build_context(final, chunks, None, _mc.context_budget(self.pipe.s, "answer")["chars"],
                                        query=q_search, trim=t_.context_trim, dedupe=t_.dedupe_hits,
                                        chunk_chars=self.pipe.s.context_chunk_chars,
                                        guard=bool(getattr(t_, "context_guard", True)))
                    rebuilt = True
                else:
                    rebuilt = False
                st.note(**dict(af.summary(), removed=removed, context_rebuilt=rebuilt))
                st.debug(blocked=dict(list(af.blocked_docs.items())[:20]))
        elif af is not None:
            prof.skipped("doc_acl", "규칙 없음" if af.role != "admin" else "admin")
        prof.replayed("fts_search", source="checkpoint")
        prof.replayed("vector_search", source="checkpoint")
        prof.replayed("graph_search", source="checkpoint")
        prof.replayed("rrf_fuse", source="checkpoint")
        prof.replayed("boost", source="checkpoint")
        prof.replayed("rerank", source="checkpoint", n=len(final))
        prof.replayed("context", source="checkpoint", chars=ctx.get("chars"), citations=len((ctx.get("citations") or [])),
                      missing_chunks=missing[:5] or None)
        rp.used["ctx"] = True
        if self.capture is not None:
            # 재생한 값도 그대로 다시 저장해 둔다 — 이 재실행 결과에서 또 이어서 실험할 수 있게.
            for k in ("lists", "list_weights", "fts_snips", "graph_res", "ext_chunks", "fused", "boosted", "reranked", "final", "boost_stats"):
                if d.get(k) is not None:
                    self.capture.put(k, d[k])
            self.capture.put("ctx", ctx)
        R = {"lists": d.get("lists") or {}, "weights": d.get("list_weights") or {}, "hits": final, "final": final,
             "chunks": chunks, "ctx": ctx, "graph_res": d.get("graph_res") or {}, "fts_snips": d.get("fts_snips") or {},
             "cfg": cfg, "boost_stats": d.get("boost_stats") or {}, "expand": [], "expand_info": {}, "ext_chunks": ext_chunks,
             "fused_order": [h["chunk_id"] for h in (d.get("fused") or [])], "boost_order": [h["chunk_id"] for h in (d.get("boosted") or [])],
             "rerank_before": [h["chunk_id"] for h in (d.get("boosted") or [])], "final_order": [h.chunk_id for h in final],
             "context_ids": [c.get("chunk_id") for c in (ctx.get("citations") or [])], "q_search": q_search,
             "replayed": True, "missing_chunks": missing}
        self.rounds.append(R)
        return R

    # ---------------------------------------------------------------- 보조
    def _expand_now(self, prof: Profiler, q_search: str, s: Any, T: Any) -> List[str]:
        rl = self.pipe.llm_for("expand")
        with prof.stage("query_expand", model=rl.model, n=T.get("query_expand_n"), reason="fallback") as st:
            try:
                r = rl.complete(_prompts.get("expand"), "N=%d\n%s" % (T.get("query_expand_n"), q_search),
                                max_tokens=s.role_max_tokens("expand", 400), effort=s.role_llm("expand")["effort"], json_mode=True)
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
                        llm_ev = _ev.assess_llm(vl, q_search, R["ctx"], s.role_llm("verify")["effort"],
                                                max_tokens=s.role_max_tokens("verify", 500))
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
                r = al.complete(_prompts.get("compress"), "## 질문\n%s\n\n## 문단\n%s" % (q_search, ctx["text"]),
                                max_tokens=s.role_max_tokens("compress", max(800, ctx["chars"] // 2)), effort="low")
                text = r["text"].strip()
                if text and len(text) < ctx["chars"] and "[C1]" in text:
                    st.note(chars_after=len(text), saved=ctx["chars"] - len(text), usage=r.get("usage"))
                    return dict(ctx, text=text, chars=len(text))
                st.note(kept_original=True, reason="compressed text lost citations or not shorter")
            except (LLMError, ValueError) as e:
                st.note(error=str(e)[:200])
        return ctx

    def _refs(self, ctx: Dict[str, Any], chunks: Dict[str, Any], preview_chars: int) -> List[Dict[str, Any]]:
        """LLM 에 실제로 전달된 근거 목록 [{n, chunk_id, doc_id, ext_id, heading, kind, chars, preview}] — 컨텍스트 인용 순서 그대로."""
        meta = self.pipe.store.doc_meta_map()
        out = []
        for cit in ctx.get("citations") or []:
            c = _row_dict(chunks.get(cit["chunk_id"]))     # 색인 청크는 sqlite3.Row, 외부 청크는 dict
            dm = meta.get(c.get("doc_id") or "") or {}
            text = c.get("text") or ""
            out.append({"n": cit.get("n"), "chunk_id": cit["chunk_id"], "doc_id": c.get("doc_id") or cit.get("doc_id") or "",
                        "ext_id": dm.get("ext_id") or ((c.get("external") or {}).get("id") if isinstance(c, dict) else None),
                        "heading": c.get("heading") or cit.get("heading") or "", "kind": cit.get("kind") or "hit",
                        "chars": len(text), "preview": text[:preview_chars] if preview_chars > 0 else ""})
        return out

    def _candidates(self, R: Dict[str, Any], chunk_chars: int) -> List[Dict[str, Any]]:
        """output_mode=fused|reranked 의 candidates[] — R['final'] 순서대로 후보 + 청크 메타. text 는 output_chunk_chars 로 자른다 (0 = 전문)."""
        meta = self.pipe.store.doc_meta_map()
        chunks = R["chunks"]
        out = []
        for i, h in enumerate(R["final"]):
            c = _row_dict(chunks.get(h.chunk_id))
            dm = meta.get(c.get("doc_id") or "") or {}
            ext = c.get("external")
            text = c.get("text") or ""
            out.append(dict(h.to_dict(), rank=i + 1, doc_id=c.get("doc_id") or "", ext_id=dm.get("ext_id") or ((ext or {}).get("id")),
                            heading=c.get("heading") or "", doc_type=dm.get("doc_type") or (ext or {}).get("doc_type"), date=dm.get("date"),
                            text=(text[:chunk_chars] if chunk_chars > 0 else text), chars=len(text), **({"external": ext} if ext else {})))
        return out

    def _hit_dicts(self, final: List[Hit], chunks: Dict[str, Any], ctx: Dict[str, Any], fts_snips: Dict[str, str]) -> List[Dict[str, Any]]:
        cite_n = {c["chunk_id"]: c["n"] for c in ctx["citations"]}
        meta = self.pipe.store.doc_meta_map()
        out = []
        for h in final:
            c = chunks.get(h.chunk_id)
            dm = meta.get(c["doc_id"]) if c else None
            ext = c.get("external") if isinstance(c, dict) else None
            out.append(dict(h.to_dict(), n=cite_n.get(h.chunk_id), in_context=h.chunk_id in cite_n, doc_id=c["doc_id"] if c else "",
                            heading=c["heading"] if c else "", text=c["text"] if c else "", snippet=fts_snips.get(h.chunk_id, ""),
                            doc_type=(dm or {}).get("doc_type") or (ext or {}).get("doc_type"), ext_id=(dm or {}).get("ext_id"), date=(dm or {}).get("date"),
                            **({"external": ext} if ext else {})))
        for cit in ctx["citations"]:
            if cit.get("kind") in ("neighbor", "doc_expand") and cit["chunk_id"] in chunks:
                c = chunks[cit["chunk_id"]]
                dm = meta.get(c["doc_id"]) or {}
                out.append({"chunk_id": cit["chunk_id"], "scores": {}, "ranks": {}, "fused": 0.0, "why": [cit["kind"]], "rerank": cit.get("score"), "boosts": {},
                            "n": cit["n"], "in_context": True, "doc_id": c["doc_id"], "heading": c["heading"], "text": c["text"], "snippet": "",
                            "doc_type": dm.get("doc_type"), "ext_id": dm.get("ext_id"), "date": dm.get("date"), "parent": cit.get("parent")})
        out.sort(key=lambda d: (d["n"] is None, d["n"] or 0))
        return out

    # ---------------------------------------------------------------- 융합 뒤 · 리랭크 뒤 LLM (토글 llm_after_fusion / llm_after_rerank)
    # 두 단계의 공통 규칙 (docs/ANSWER_MODES.md §4):
    #   - 후보 번호는 **1부터**. 프롬프트 본문에 그렇게 그리고, 파싱은 n-1 로 한다(범위 밖 번호는 버린다).
    #   - LLM 이 실패하거나(LLMError) JSON 이 깨지면 **순위를 그대로 둔다** — 품질을 깎는 방향으로는 실패하지 않는다.
    #   - 역할은 fusion / select 라 모델·정책·앙상블이 역할 단위 설정을 그대로 따른다(config.json llm_roles).
    def _cand_lines(self, cands: List[Hit], chunks: Dict[str, Any], chunk_chars: int, with_doc: bool = False) -> List[str]:
        lines: List[str] = []
        for i, h in enumerate(cands):
            c = _row_dict(chunks.get(h.chunk_id))
            head = (c.get("heading") or "")[:70]
            text = (c.get("text") or "")[:chunk_chars].replace("\n", " ")
            if with_doc:
                lines.append("[%d] 문서=%s · %s\n%s" % (i + 1, c.get("doc_id") or "?", head, text))
            else:
                lines.append("[%d] (%s) %s" % (i + 1, head, text))
        return lines

    def _fusion_llm(self, prof: Profiler, q: str, hits: List[Hit], chunks: Dict[str, Any], T: Any, n_cands: int) -> None:
        """융합·부스트 직후(리랭크 전) LLM(역할 fusion)이 명백히 무관한 후보를 고른다 → drop 은 fused × fusion_llm_drop_penalty
        (0 이면 목록에서 제거, why=llm_drop). hits 를 제자리에서 고치고 다시 정렬한다."""
        p = self.pipe
        n = int(T.get("fusion_llm_candidates") or 0) or n_cands
        cands = hits[:n]
        if not cands:
            prof.skipped("fusion_llm", "후보가 없다")
            return
        fl = p.llm_for("fusion")
        if not fl.available:
            prof.skipped("fusion_llm", "LLM provider unavailable → 융합 순위 유지")
            return
        penalty = float(T.get("fusion_llm_drop_penalty") or 0.0)
        with prof.stage("fusion_llm", candidates=len(cands), model=getattr(fl, "model", fl.name), role="fusion", drop_penalty=penalty) as st:
            sys_p = _prompts.get("fusion_review")
            prompt = "\n\n".join(["질문: " + q, ""] + self._cand_lines(cands, chunks, int(p.s.rerank_chunk_chars)))
            st.note(prompt_chars=len(prompt) + len(sys_p), est_input_tokens=(len(prompt) + len(sys_p)) // 3)
            st.sample(system=sys_p, prompt=prompt[:6000])
            try:
                r = fl.complete(sys_p, prompt, max_tokens=p.s.role_max_tokens("fusion", 400), effort=p.s.role_llm("fusion")["effort"], json_mode=True)
                st.sample(response=r["text"][:2000])
                data = parse_json(r["text"]) or {}
                drop = sorted({int(x) - 1 for x in (data.get("drop") or []) if isinstance(x, (int, float)) and 0 < int(x) <= len(cands)})
                if len(drop) >= len(cands):
                    # 전부 버리라는 응답은 따르지 않는다 (근거가 통째로 사라지면 답을 만들 수 없다)
                    st.note(usage=r.get("usage"), ms_llm=round(r.get("ms", 0)), dropped=[], ignored="LLM 이 모든 후보를 drop 했다 — 순위 유지")
                    return
                removed: List[str] = []
                for i in drop:
                    h = cands[i]
                    h.why.append("llm_drop")
                    h.boosts["llm_drop"] = penalty
                    if penalty <= 0:
                        removed.append(h.chunk_id)
                    else:
                        h.fused *= penalty
                if removed:
                    rm = set(removed)
                    hits[:] = [h for h in hits if h.chunk_id not in rm]
                hits.sort(key=lambda h: -h.fused)
                st.note(usage=r.get("usage"), ms_llm=round(r.get("ms", 0)), dropped=[cands[i].chunk_id for i in drop], removed=len(removed),
                        reason=str(data.get("reason") or "")[:200], top=[h.chunk_id for h in hits[:5]])
                st.debug(after=[h.chunk_id for h in hits[:60]])
            except (LLMError, ValueError) as e:
                st.note(error=str(e)[:200], fallback="융합·부스트 순위 유지")

    def _rerank_review_llm(self, prof: Profiler, q: str, hits: List[Hit], chunks: Dict[str, Any], T: Any, top_k_final: int) -> List[str]:
        """리랭크 직후(문서 확장·컨텍스트 전) LLM(역할 select)이 컨텍스트에 넣을 청크 순서와 통째로 읽을 문서를 고른다.
        반환: expand_docs(문서 id 목록 — doc_expand 가 우선·전체 확장). hits 는 제자리에서 재정렬한다."""
        p = self.pipe
        n = int(T.get("post_rerank_llm_k") or 0) or max(top_k_final * 2, 10)
        cands = hits[:n]
        if not cands:
            prof.skipped("rerank_review_llm", "후보가 없다")
            return []
        sl = p.llm_for("select")
        if not sl.available:
            prof.skipped("rerank_review_llm", "LLM provider unavailable → 리랭크 순위 유지")
            return []
        with prof.stage("rerank_review_llm", candidates=len(cands), model=getattr(sl, "model", sl.name), role="select") as st:
            sys_p = _prompts.get("rerank_review")
            prompt = "\n\n".join(["질문: " + q, ""] + self._cand_lines(cands, chunks, int(p.s.rerank_chunk_chars), with_doc=True))
            st.note(prompt_chars=len(prompt) + len(sys_p), est_input_tokens=(len(prompt) + len(sys_p)) // 3)
            st.sample(system=sys_p, prompt=prompt[:6000])
            try:
                r = sl.complete(sys_p, prompt, max_tokens=p.s.role_max_tokens("select", 400), effort=p.s.role_llm("select")["effort"], json_mode=True)
                st.sample(response=r["text"][:2000])
                data = parse_json(r["text"]) or {}
                sel: List[int] = []
                for x in (data.get("select") or []):
                    if isinstance(x, (int, float)) and 0 < int(x) <= len(cands) and (int(x) - 1) not in sel:
                        sel.append(int(x) - 1)
                docs = {str(c.get("doc_id") or "") for c in (_row_dict(chunks.get(h.chunk_id)) for h in cands)}
                expand_docs = [str(d) for d in (data.get("expand_docs") or []) if str(d) in docs][:max(1, top_k_final)]
                if sel:
                    picked = [cands[i] for i in sel]
                    rest = [h for i, h in enumerate(cands) if i not in set(sel)]
                    for h in picked:
                        h.why.append("llm_select")
                    hits[:len(cands)] = picked + rest
                st.note(usage=r.get("usage"), ms_llm=round(r.get("ms", 0)), selected=[cands[i].chunk_id for i in sel][:10],
                        expand_docs=expand_docs, note=str(data.get("note") or "")[:200])
                st.debug(after=[h.chunk_id for h in hits[:60]])
                return expand_docs
            except (LLMError, ValueError) as e:
                st.note(error=str(e)[:200], fallback="리랭크 순위 유지")
                return []

    # ---------------------------------------------------------------- 문서 단위 확장 (doc_expand)
    def _doc_expand(self, store, q: str, final: List[Hit], chunks: Dict[str, Any], T: Any,
                    prefer_docs: Optional[List[str]] = None) -> Tuple[List[Tuple[str, str, float]], Dict[str, Any]]:
        """final 의 상위 문서들에서 아직 컨텍스트에 없는 청크를 질의 관련도로 점수화해 추가 후보를 고른다.
        반환: [(chunk_id, parent_chunk_id, score)], 진단 메타. 점수 = keyword(커버리지) | vector(부모 청크 대비 정규화 코사인) | hybrid."""
        import numpy as np
        p = self.pipe
        kws = keywords(q)
        top_docs, max_chunks = int(T.get("doc_expand_top_docs")), int(T.get("doc_expand_max_chunks"))
        min_score, mode, w = float(T.get("doc_expand_min_score")), str(T.get("doc_expand_mode")), float(T.get("doc_expand_w"))
        have = {h.chunk_id for h in final}
        docs_order: List[str] = []
        parent_of: Dict[str, str] = {}
        for h in final:
            c = chunks.get(h.chunk_id)
            if not c:
                continue
            if c["doc_id"] not in parent_of:
                parent_of[c["doc_id"]] = h.chunk_id
                docs_order.append(c["doc_id"])
        # llm_after_rerank 의 expand_docs: 그 문서를 앞으로 당기고(top_docs 안에 반드시 들어오게) 통째로 읽는다(mode 무시).
        force_full = {d for d in (prefer_docs or []) if d in parent_of}
        if force_full:
            docs_order = [d for d in docs_order if d in force_full] + [d for d in docs_order if d not in force_full]
        docs_order = docs_order[:max(top_docs, len(force_full))]
        qv = None
        idx: Dict[str, int] = {}
        mat = None
        if mode in ("vector", "hybrid") and p.s.toggles.embed:
            try:
                ids, mat = store.vector_matrix(p.embedder.name)
                if ids:
                    qv = np.asarray(p.embedder.embed([q])[0], dtype=np.float32)
                    if qv.shape[0] != mat.shape[1]:
                        qv = None
                    else:
                        idx = {cid: i for i, cid in enumerate(ids)}
            except Exception:
                qv = None

        def _sim(cid: str) -> Optional[float]:
            if qv is None or cid not in idx:
                return None
            return float(np.asarray(mat[idx[cid]], dtype=np.float32) @ qv)

        out: List[Tuple[str, str, float]] = []
        per_doc: Dict[str, Any] = {}
        cand_total = 0
        for d in docs_order:
            parent = parent_of[d]
            psim = _sim(parent)
            scored = []
            for r in store.all_chunks(d):
                cid = r["chunk_id"]
                if cid in have:
                    continue
                text = ((r["heading"] or "") + " " + (r["text"] or "")).lower()
                toks = set(normalize_token(x) for x in words(text))
                kw = (sum(1 for k in kws if k in toks or k in text) / float(len(kws))) if kws else 0.0
                sim = _sim(cid)
                vs = None
                if sim is not None:
                    vs = min(1.0, max(0.0, (sim / psim) if (psim and psim > 0) else sim))
                if mode == "full" or d in force_full:
                    score = 1.0          # 점수로 거르지 않는다 — 문서 전체를 문서 순서대로
                elif mode == "keyword" or vs is None:
                    score = kw
                elif mode == "vector":
                    score = vs
                else:
                    score = w * vs + (1.0 - w) * kw
                scored.append((round(score, 4), int(r["ordinal"] or 0), cid, round(kw, 3), None if vs is None else round(vs, 3)))
            cand_total += len(scored)
            if mode == "full" or d in force_full:
                # 근거가 나온 문서는 통째로 읽는다. 순서를 지키되 max_chunks · context_max_chars 로만 제한.
                pick = sorted(scored, key=lambda x: x[1])[:max_chunks]
            else:
                pick = sorted([x for x in scored if x[0] >= min_score], key=lambda x: -x[0])[:max_chunks]
            pick.sort(key=lambda x: x[1])    # 문서 순서(ordinal)로 컨텍스트에 넣는다
            for sc, _o, cid, kw, vs in pick:
                out.append((cid, parent, sc))
                have.add(cid)
            per_doc[d] = {"parent": parent, "candidates": len(scored), "added": len(pick),
                          "picked": [{"chunk": cid, "score": sc, "kw": kw, "vec": vs} for sc, _o, cid, kw, vs in pick],
                          "best_rejected": max([x[0] for x in scored if x[0] < min_score] or [0.0])}
        need = [cid for cid, _, _ in out if cid not in chunks]
        if need:
            chunks.update(store.get_chunks(need))
        return out, {"docs": len(docs_order), "candidates": cand_total, "added": len(out), "mode": mode, "vector": qv is not None,
                     "min_score": min_score, "max_chunks": max_chunks, "per_doc": per_doc,
                     **({"llm_expand_docs": sorted(force_full)} if force_full else {})}

    # ---------------------------------------------------------------- 검색 라운드
    def _retrieve(self, prof: Profiler, q: str, q_search: str, qr: Optional[Dict[str, Any]], route_info: Dict[str, Any], weights: Dict[str, float],
                  alt_llm: List[str], pin_info: Dict[str, Any], scope: Optional[Dict[str, Any]], feedback_w: Dict[str, float], cfg: RoundConfig) -> Dict[str, Any]:
        p = self.pipe
        s, t, T = p.s, p.s.toggles, _tuning.T
        store = p.store
        # 단계 재실행: 첫 라운드에서만 재생한다. fallback 라운드는 **다시 계산**해야
        # 바뀐 설정(넓힌 k, 다른 확장 질의)이 반영된다.
        rp = self.resume
        if rp is not None:
            if rp.round > 0:
                rp = None
            else:
                self.resume.round += 1
        lists: Dict[str, List[Tuple[str, float]]] = {}
        w = dict(weights)
        k_fts, k_vec, k_gr = int(s.top_k_fts * cfg.k_mult), int(s.top_k_vector * cfg.k_mult), int(s.top_k_graph * cfg.k_mult)
        fts_snips: Dict[str, str] = {}
        syn = store.synonyms()
        # 규칙이 만든 대체 질의·관련어는 **어느 규칙이 만들었는지**(4번째/3번째 원소)를 함께 들고 다닌다.
        # 그래야 나중에 "이 규칙이 실제로 답변 근거에 기여했나" 를 정확히 셀 수 있다 (llmwiki/ruleeffect.py).
        rule_src: Dict[str, str] = {}
        rule_alts: List[Tuple] = list(qr["alt_queries"]) if qr else []
        related: List[Tuple] = [tuple([r[0], r[1] * cfg.related_mult] + list(r[2:])) for r in (qr["related"] if qr else [])]
        alt_all: List[Tuple] = rule_alts + [(a, float(T.get("query_expand_w")), "llm") for a in (alt_llm if cfg.use_llm_alts else [])] + list(cfg.extra_queries)
        # 중복 제거
        seen = {q_search}
        alts: List[Tuple] = []
        for a in alt_all:
            txt = a[0]
            if txt and txt not in seen:
                seen.add(txt)
                alts.append(tuple(a))
        alts = alts[:6]
        # 채널 검색을 통째로 재생할 것인가 (융합·부스트·리랭크부터 다시 돌 때)
        lists_replay = rp.get("lists") if rp is not None else None
        graph_replay: Dict[str, Any] = {}
        ext_replay: Dict[str, Any] = {}
        if lists_replay is not None:
            lists = {str(k): [(str(c), float(sc)) for c, sc in v] for k, v in lists_replay.items() if isinstance(v, list)}
            w = {str(k): float(v) for k, v in (rp.data.get("list_weights") or {}).items()} or w
            fts_snips = {str(k): str(v) for k, v in (rp.data.get("fts_snips") or {}).items()}
            graph_replay = rp.data.get("graph_res") or {}
            ext_replay = rp.data.get("ext_chunks") or {}
            prof.replayed("fts_search", channels=sorted(lists), hits={k: len(v) for k, v in lists.items()})
            prof.replayed("vector_search")
            prof.replayed("graph_search", chunks=len(graph_replay.get("chunks") or []))
        if lists_replay is not None:
            pass
        elif t.fts:
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
            for i, a in enumerate(alts):
                aq, wgt = a[0], a[1]
                arows = fts_search(store, aq, k_fts, syn, prof, stage_name="fts_search_alt")
                lists["fts_alt%d" % (i + 1)] = [(cid, sc) for cid, sc, _ in arows]
                w["fts_alt%d" % (i + 1)] = w.get("fts", 1.0) * wgt
                if len(a) > 3:
                    rule_src["fts_alt%d" % (i + 1)] = a[3]      # 이 리스트를 만든 규칙 (효과 집계용)
            for i, rr in enumerate(related[:3]):
                rq, wgt = rr[0], rr[1]
                rrows = fts_search(store, rq, max(3, k_fts // 2), {}, prof, stage_name="fts_search_related")
                lists["fts_rel%d" % (i + 1)] = [(cid, sc) for cid, sc, _ in rrows]
                w["fts_rel%d" % (i + 1)] = w.get("fts", 1.0) * wgt
                if len(rr) > 2:
                    rule_src["fts_rel%d" % (i + 1)] = rr[2]
        else:
            prof.skipped("fts_search")
        if lists_replay is not None:
            pass
        elif t.vector:
            lists["vector"] = vector_search(store, p.embedder, q_search, k_vec, prof,
                                            bool(getattr(t, "embed_query_cache", True)))
            for i, a in enumerate(alts[:3]):
                aq, wgt = a[0], a[1]
                lists["vector_alt%d" % (i + 1)] = vector_search(store, p.embedder, aq, k_vec, prof,
                                                                bool(getattr(t, "embed_query_cache", True)))
                w["vector_alt%d" % (i + 1)] = w.get("vector", 1.0) * wgt
                if len(a) > 3:
                    rule_src["vector_alt%d" % (i + 1)] = a[3]
        else:
            prof.skipped("vector_search")
        graph_res: Dict[str, Any] = dict(graph_replay)
        if lists_replay is not None:
            pass
        elif t.graph:
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
        if lists_replay is not None:
            pass
        elif t.doc_vector:
            from . import precompute as _pc
            lists["doc_vector"] = _pc.doc_vector_search(store, p.embedder, q_search, max(3, k_vec // 2), prof)
            w["doc_vector"] = float(T.get("channel_w_doc_vector"))
        else:
            prof.skipped("doc_vector_search")
        # ---- 외부 RAG 채널 (mcp_sources.json retrieve 매핑) — 결과는 가상 청크 ext:<source>:<id> 로 융합에 참여 ----
        ext_chunks: Dict[str, Dict[str, Any]] = {str(k): v for k, v in ext_replay.items() if isinstance(v, dict)}
        if lists_replay is not None:
            if ext_chunks:
                prof.replayed("external_rag", results=len(ext_chunks))
        elif t.external_rag:
            from . import mcp_client as _mcp
            with prof.stage("external_rag", k=int(T.get("external_rag_k") * cfg.k_mult), fallback=bool(cfg.mcp_enrich)) as st:
                try:
                    rows = _mcp.retrieve(s, q_search, max(1, int(T.get("external_rag_k") * cfg.k_mult)), include_fallback=bool(cfg.mcp_enrich))
                    errs = [r for r in rows if r.get("error")]
                    per: Dict[str, int] = {}
                    for r in rows:
                        if r.get("error"):
                            continue
                        ch = "ext_" + r["source"]
                        lists.setdefault(ch, []).append((r["chunk_id"], float(r["score"])))
                        w[ch] = float(T.get("channel_w_external")) * float(r.get("weight") or 1.0)
                        ext_chunks[r["chunk_id"]] = {"chunk_id": r["chunk_id"], "doc_id": "ext:%s:%s" % (r["source"], r["id"]), "ordinal": 0, "heading": r["title"],
                                                     "text": r["text"], "start": 0, "end": len(r["text"] or ""),
                                                     "external": {"source": r["source"], "id": r["id"], "url": r.get("url"), "score": r["score"], "doc_type": r.get("doc_type")}}
                        per[r["source"]] = per.get(r["source"], 0) + 1
                    st.note(results=len(ext_chunks), per_source=per, errors=[{"source": e["source"], "error": e["error"]} for e in errs][:3])
                    st.debug(items=[(c["chunk_id"], c["heading"][:40], round(c["external"]["score"], 3)) for c in list(ext_chunks.values())[:8]])
                except Exception as e:
                    st.note(error=str(e)[:200])
        else:
            prof.skipped("external_rag")
        if lists_replay is not None:
            pass
        elif cfg.mcp_enrich and t.mcp_sources:
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
        from . import rerun as _rerun
        method = T.get("fusion_method")
        fused_replay = rp.get("fused") if rp is not None else None
        if fused_replay is not None:
            hits = _rerun.hits_from(rp.data, "fused")
            prof.replayed("rrf_fuse", n=len(hits), top=[h.chunk_id for h in hits[:6]])
        else:
            topk = _fusion.topk_map(T)      # 채널별 top-k 구간 가중 (<채널>_topk_n/_topk_w/_tail_w, 모두 기본이면 빈 dict)
            with prof.stage("rrf_fuse", rrf_k=s.rrf_k, method=method, weights=w, sources={n: len(v) for n, v in lists.items()},
                            topk=topk or None) as st:
                hits, fmeta = _fusion.fuse(lists, w, s.rrf_k, method, T.get("fusion_multi_bonus"), topk=topk)
                st.note(**fmeta, top=[(h.chunk_id, round(h.fused, 4), h.why) for h in hits[:6]])
                st.debug(order=[h.chunk_id for h in hits[:60]])
        fused_order = [h.chunk_id for h in hits]
        if self.capture is not None and not self.rounds:
            self.capture.put("lists", {k: [[c, round(float(sc), 6)] for c, sc in v] for k, v in lists.items()})
            self.capture.put("list_weights", {k: round(float(v), 6) for k, v in w.items()})
            self.capture.put("fts_snips", fts_snips)
            self.capture.put("graph_res", graph_res)
            self.capture.put("ext_chunks", ext_chunks)
            self.capture.hits("fused", hits)
        # pin 주입 (후보에 없으면 추가)
        have = {h.chunk_id for h in hits}
        for cid in pin_info.get("inject", []):
            if cid not in have:
                h = Hit(cid)
                h.fused = 0.5 / (s.rrf_k + 1)
                h.why = ["pin"]
                hits.append(h)
                have.add(cid)
        chunks: Dict[str, Any] = dict(store.get_chunks([h.chunk_id for h in hits if h.chunk_id not in ext_chunks]))
        for cid in (h.chunk_id for h in hits):
            if cid in ext_chunks:
                chunks[cid] = ext_chunks[cid]
        # ---- 부스트 ----
        prov_chunks: Dict[str, str] = {}
        for r in graph_res.get("relations", []) or []:
            if r.get("chunk_id") and r.get("provenance"):
                prov_chunks.setdefault(r["chunk_id"], r["provenance"])
        time_mode = T.get("time_mode") if cfg.time_filter else "boost"
        boosted_replay = rp.get("boosted") if rp is not None else None
        stats: Dict[str, Any] = {}
        if boosted_replay is not None:
            hits = _rerun.hits_from(rp.data, "boosted")
            # 재생한 순위에 맞춰 청크 본문을 다시 읽는다 (본문은 저장하지 않는다 — 색인에서 읽으면 된다)
            chunks = dict(store.get_chunks([h.chunk_id for h in hits if h.chunk_id not in ext_chunks]))
            for cid in (h.chunk_id for h in hits):
                if cid in ext_chunks:
                    chunks[cid] = ext_chunks[cid]
            stats = dict(rp.data.get("boost_stats") or {})
            prof.replayed("boost", n=len(hits), top=[h.chunk_id for h in hits[:5]])
        else:
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
                st.debug(order=[h.chunk_id for h in hits[:60]])
        # ---- 문서 단위 접근 제어 (2026-09-19) ----
        # 역할이 낮은 사용자에게는 그 문서를 **근거로 주지 않는다**. 부스트 뒤·리랭크 앞에 두는 이유:
        #   · 융합·부스트 통계는 원래 후보 기준으로 남겨 "무엇이 걸러졌나" 를 볼 수 있게 하고,
        #   · 리랭크·컨텍스트·답변·인용 어디에도 가려진 문서가 닿지 않게 한다 (뒤쪽 출구가 전부 이 아래에 있다).
        acl_info: Dict[str, Any] = {"enabled": False}
        af = None
        try:
            af = p.acl_filter()
        except Exception as e:
            acl_info = {"enabled": False, "error": str(e)[:160]}
            prof.skipped("doc_acl", "판정기 생성 실패: %s" % str(e)[:80])
        if af is not None and af.enabled:
            with prof.stage("doc_acl", role=af.role) as st:
                try:
                    hits, removed = af.filter_hits(hits, chunks)
                except Exception as e:
                    # **열어 주지 않는다.** 판정이 깨지면 chunk_id 만으로 되짚어 보수적으로 거른다 —
                    # 예외 한 번에 비공개 문서가 근거로 들어가는 길을 남기지 않기 위해서다.
                    kept = [h for h in hits if af.doc_ok(str(getattr(h, "chunk_id", "") or "").rsplit("#", 1)[0])]
                    removed, hits = len(hits) - len(kept), kept
                    st.note(fallback=str(e)[:120])
                acl_info = dict(af.summary(), removed=removed)
                st.note(**acl_info)
                st.debug(blocked=dict(list(af.blocked_docs.items())[:20]))
            if acl_info.get("removed"):
                chunks = {k: v for k, v in chunks.items()
                          if af.chunk_ok(k, af.doc_id_of(v, k))}
        elif af is not None:
            prof.skipped("doc_acl", "규칙 없음" if af.role != "admin" else "admin")
        boost_order = [h.chunk_id for h in hits]
        if self.capture is not None and not self.rounds:
            self.capture.hits("boosted", hits)
            self.capture.put("boost_stats", stats)
        # ---- 리랭크 · 컨텍스트 ----
        n_cands = int(s.rerank_candidates * cfg.k_mult)
        win = n_cands or max(s.top_k_final * 2, 10)     # 리랭크 후보 창 (external_inject · channel_inject 가 여기로 올린다)
        # output_mode=fused|reranked 의 후보 수: output_candidates_n (0 = 리랭크 후보 수)
        n_out = int(T.get("output_candidates_n") or 0) or win
        empty_ctx = {"text": "", "citations": [], "chars": 0, "hits_used": [], "dropped": [], "neighbors": [], "doc_expand": []}
        # 융합·부스트 직후 LLM 검토 (토글 llm_after_fusion, 역할 fusion) — fused 점수 자체를 바꾸므로
        # **output_mode=fused 의 조기 반환보다 앞**에 둔다(주입과 달리 리랭크가 없어도 뜻이 있다).
        # 재생 중(리랭크 결과가 저장돼 있으면)에는 그 효과가 이미 저장본에 들어 있으므로 다시 부르지 않는다 (rerun.STAGE_POINT 주석 참고).
        if getattr(t, "llm_after_fusion", False) and (rp is None or rp.get("reranked") is None):
            self._fusion_llm(prof, q_search, hits, chunks, T, win)
        elif getattr(t, "llm_after_fusion", False):
            prof.replayed("fusion_llm", note="리랭크 결과를 재생 — 융합 검토는 저장본에 이미 반영돼 있다")
        else:
            prof.skipped("fusion_llm", "토글 llm_after_fusion off")
        if cfg.stop_after == "boost":
            # output_mode=fused — 융합·부스트 결과를 그대로 돌려준다. 뒤 단계는 계산하지 않는다 (리랭크 전 순위를 보려는 것이므로 주입도 하지 않는다)
            for name in ("external_inject", "channel_inject", "rerank", "doc_expand", "context"):
                prof.skipped(name, "output_mode=fused")
            final = hits[:n_out]
            if self.capture is not None and not self.rounds:
                self.capture.hits("final", final)
                self.capture.put("ctx", empty_ctx)
            R = {"lists": lists, "weights": w, "hits": hits, "final": final, "chunks": chunks, "ctx": empty_ctx, "graph_res": graph_res, "fts_snips": fts_snips,
                 "cfg": cfg, "boost_stats": stats, "expand": [], "expand_info": {}, "ext_chunks": ext_chunks, "inject": {},
                 "fused_order": fused_order, "boost_order": boost_order, "rerank_before": [h.chunk_id for h in hits[:win]], "final_order": [h.chunk_id for h in final],
                 "context_ids": [], "q_search": q_search, "rule_src": rule_src}
            self.rounds.append(R)
            return R
        if ext_chunks and int(T.get("external_rag_inject") or 0) > 0:
            # 외부 소스별 상위 n개를 리랭크 후보 창 안으로 (창 끝 요소 바로 위의 fused 로) — 최종 순위는 리랭커가 결정
            per_src: Dict[str, int] = {}
            moved: List[str] = []
            pos = {h.chunk_id: i for i, h in enumerate(hits)}
            for ch, lst in lists.items():
                if not ch.startswith("ext_"):
                    continue
                for cid, _sc in lst:
                    if per_src.get(ch, 0) >= int(T.get("external_rag_inject")):
                        break
                    per_src[ch] = per_src.get(ch, 0) + 1
                    i = pos.get(cid)
                    if i is not None and i >= win and win - 1 < len(hits):
                        hits[i].fused = hits[win - 1].fused + 1e-6
                        hits[i].why.append("ext_inject")
                        moved.append(cid)
            if moved:
                hits.sort(key=lambda h: -h.fused)
                with prof.stage("external_inject", moved=moved, window=win) as st:
                    st.note(n=len(moved))
        # 채널별 리랭크 창 보장 주입 (tuning channel_inject 'fts:2,vector:2,graph:1', §2.2) — 재생 중(리랭크 결과를 재생)이면 의미 없으므로 건너뛴다
        inject_map = _fusion.parse_inject_map(T.get("channel_inject"))
        inject_moved: Dict[str, List[str]] = {}
        if inject_map and (rp is None or rp.get("reranked") is None):
            inject_moved = _fusion.inject_channels(hits, lists, inject_map, win)
            with prof.stage("channel_inject", inject=inject_map, window=win, moved=inject_moved) as st:
                st.note(n=sum(len(v) for v in inject_moved.values()))
        rerank_before = [h.chunk_id for h in hits[:win]]
        reranked_replay = rp.get("reranked") if rp is not None else None
        if reranked_replay is not None:
            hits = _rerun.hits_from(rp.data, "reranked")
            need = [h.chunk_id for h in hits if h.chunk_id not in chunks and h.chunk_id not in ext_chunks]
            if need:
                chunks.update(store.get_chunks(need))
            for cid in (h.chunk_id for h in hits):
                if cid in ext_chunks:
                    chunks[cid] = ext_chunks[cid]
            prof.replayed("rerank", n=len(hits), top=[h.chunk_id for h in hits[:5]])
        elif t.rerank:
            rl = p.llm_for("rerank")
            meta = store.doc_meta_map()
            doc_tokens = {d: "%s %s" % (m.get("ext_id") or "", m.get("doc_type") or "") for d, m in meta.items() if m.get("ext_id") or m.get("doc_type")}
            # output_mode=reranked 면 리랭크된 후보를 top_k_final 로 자르지 않고 n_out 개까지 그대로 본다 (rerank() 는 top_n 뒤의 리랭크 후보를 버린다)
            top_n = max(s.top_k_final, n_out) if cfg.stop_after == "rerank" else s.top_k_final
            hits = rerank(hits, chunks, q_search, rl, top_n, prof, s.role_llm("rerank")["effort"], use_llm=t.rerank_llm,
                          n_cands=n_cands, chunk_chars=s.rerank_chunk_chars, settings=s, doc_tokens=doc_tokens)
        else:
            prof.skipped("rerank")
        # 리랭크 직후 LLM 선택 (토글 llm_after_rerank, 역할 select) — 컨텍스트에 넣을 청크 순서와 통째로 읽을 문서(expand_docs).
        llm_expand_docs: List[str] = []
        if getattr(t, "llm_after_rerank", False) and reranked_replay is None and cfg.stop_after != "rerank":
            llm_expand_docs = self._rerank_review_llm(prof, q_search, hits, chunks, T, s.top_k_final)
        elif getattr(t, "llm_after_rerank", False) and reranked_replay is not None:
            prof.replayed("rerank_review_llm", note="리랭크 결과를 재생 — 선택은 저장본에 이미 반영돼 있다")
        else:
            prof.skipped("rerank_review_llm", "토글 llm_after_rerank off" if not getattr(t, "llm_after_rerank", False) else "output_mode=reranked — 리랭크 순위를 그대로 보여 준다")
        final = hits[: s.top_k_final]
        if self.capture is not None and not self.rounds:
            self.capture.hits("reranked", hits[: max(s.top_k_final * 3, 30)])
        if cfg.stop_after == "rerank":
            # output_mode=reranked — 리랭크 결과(리랭크 입력 전후)를 그대로 돌려준다. 문서 확장·컨텍스트는 계산하지 않는다
            final = hits[:n_out]
            for name in ("doc_expand", "context"):
                prof.skipped(name, "output_mode=reranked")
            if self.capture is not None and not self.rounds:
                self.capture.hits("final", final)
                self.capture.put("ctx", empty_ctx)
            R = {"lists": lists, "weights": w, "hits": hits, "final": final, "chunks": chunks, "ctx": empty_ctx, "graph_res": graph_res, "fts_snips": fts_snips,
                 "cfg": cfg, "boost_stats": stats, "expand": [], "expand_info": {}, "ext_chunks": ext_chunks, "inject": inject_moved,
                 "fused_order": fused_order, "boost_order": boost_order, "rerank_before": rerank_before, "final_order": [h.chunk_id for h in final],
                 "context_ids": [], "q_search": q_search, "rule_src": rule_src}
            self.rounds.append(R)
            return R
        if self.capture is not None and not self.rounds:
            self.capture.hits("final", final)
        # ---- 문서 단위 확장 ----
        extra: List[Tuple[str, str, float]] = []
        expand_info: Dict[str, Any] = {}
        if t.doc_expand:
            with prof.stage("doc_expand", top_docs=T.get("doc_expand_top_docs"), max_chunks=T.get("doc_expand_max_chunks"), min_score=T.get("doc_expand_min_score"),
                            mode=T.get("doc_expand_mode")) as st:
                try:
                    extra, expand_info = self._doc_expand(store, q_search, final, chunks, T, prefer_docs=llm_expand_docs)
                    st.note(docs=expand_info.get("docs"), candidates=expand_info.get("candidates"), added=expand_info.get("added"), vector=expand_info.get("vector"),
                            picked=[(cid, sc) for cid, _, sc in extra[:8]])
                    st.debug(per_doc=expand_info.get("per_doc"))
                except Exception as e:
                    st.note(error=str(e)[:200])
                    extra, expand_info = [], {"error": str(e)[:200]}
        else:
            prof.skipped("doc_expand")
        # 컨텍스트 상한은 **설정값과 모델 창 중 작은 쪽**이다 (llmwiki/models_catalog.context_budget).
        # 창을 넘기면 프롬프트가 조용히 잘려 뒤쪽 근거가 사라지므로, 잘리기 전에 우리가 줄이고 그 사실을 남긴다.
        from . import models_catalog as _mc
        budget = _mc.context_budget(s, "answer")
        with prof.stage("context", max_chars=budget["chars"], configured=budget["configured"],
                        model_window=budget["window_tokens"], budget_limited=budget["limited"],
                        trim=t.context_trim, dedupe=t.dedupe_hits, neighbors_extra=cfg.neighbors_extra) as st:
            if budget["limited"]:
                st.note(budget=budget["reason"])
            ctx = build_context(final, chunks, graph_res if t.graph else None, budget["chars"], query=q_search, trim=t.context_trim, dedupe=t.dedupe_hits,
                                chunk_chars=s.context_chunk_chars, stage=st, store=store, neighbors=(T.get("context_neighbors") + cfg.neighbors_extra), extra=extra,
                                guard=bool(getattr(t, "context_guard", True)))
            st.note(chars=ctx["chars"], citations=len(ctx["citations"]), doc_expand_in_context=sum(1 for c in ctx["citations"] if c.get("kind") == "doc_expand"))
        if self.capture is not None and not self.rounds:
            self.capture.put("ctx", ctx)
        R = {"lists": lists, "weights": w, "hits": hits, "final": final, "chunks": chunks, "ctx": ctx, "graph_res": graph_res, "fts_snips": fts_snips,
             "cfg": cfg, "boost_stats": stats, "expand": extra, "expand_info": expand_info, "ext_chunks": ext_chunks, "inject": inject_moved,
             "fused_order": fused_order, "boost_order": boost_order, "rerank_before": rerank_before, "final_order": [h.chunk_id for h in final],
             "context_ids": [c["chunk_id"] for c in ctx["citations"]], "q_search": q_search, "rule_src": rule_src}
        self.rounds.append(R)
        return R
