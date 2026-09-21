# -*- coding: utf-8 -*-
"""검색 단계: 라우터 → FTS / Vector / Graph(LightRAG 식 엔티티+청크 이중 검색) → RRF 융합 → 리랭크.

각 단계는 토글로 on/off 되며 Profiler 에 입력/출력 요약을 남긴다.
"""
from __future__ import annotations

import json
import time
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .config import Settings
from .profiler import Profiler
from .providers import BaseLLM, BaseEmbedder, parse_json, LLMError
from .store import Store
from .textutil import fts_query, keywords, tokenize_for_fts, words, normalize_token, STOPWORDS
from . import tuning as _tuning


def _T():
    return _tuning.T


class Hit:
    __slots__ = ("chunk_id", "scores", "ranks", "fused", "why", "rerank", "boosts")

    def __init__(self, chunk_id: str):
        self.chunk_id = chunk_id
        self.scores: Dict[str, float] = {}
        self.ranks: Dict[str, int] = {}
        self.fused = 0.0
        self.why: List[str] = []
        self.rerank: Optional[float] = None
        self.boosts: Dict[str, float] = {}

    def to_dict(self) -> Dict[str, Any]:
        return {"chunk_id": self.chunk_id, "scores": self.scores, "ranks": self.ranks, "fused": round(self.fused, 5),
                "why": self.why, "rerank": self.rerank, "boosts": self.boosts}


# ------------------------------------------------------------------ router
_REL_WORDS = ("관계", "영향", "왜", "원인", "누가", "담당", "연결", "관련", "비교", "차이", "어떻게", "흐름", "결정", "이유", "배경",
              "who", "why", "relation", "impact", "compare", "between")

#: 라우터가 고를 수 있는 질의 유형 — **한 곳에만** 둔다 (화면·CLI·MCP 설명이 같은 글을 쓴다).
#: 이 유형이 하는 일은 "어느 채널에 얼마나 무게를 둘지" 하나뿐이다. 채널을 끄거나 켜지는 않는다.
ROUTER_KINDS: Dict[str, Dict[str, str]] = {
    "keyword": {"label": "키워드형",
                "desc": "짧고 관계를 묻지 않는 질의 (예: 'CL-55302'). 정확한 용어·번호가 중요하므로 FTS 에 무게를 싣고 의미 검색은 줄인다."},
    "semantic": {"label": "의미형",
                 "desc": "길고 관계를 묻지 않는 질의 (예: 'RX DMA underrun 이 생기는 상황 설명'). 표현이 달라도 뜻이 가까운 문단을 찾도록 벡터에 무게를 더한다."},
    "relational": {"label": "관계형",
                   "desc": "'왜·원인·누가·영향·관련' 처럼 **이어진 것**을 묻거나, 아는 엔티티가 둘 이상 잡힌 질의. "
                           "문서 하나에 답이 없고 여러 문서를 이어야 하므로 **그래프 채널에 무게를 크게** 싣는다."},
    "hybrid": {"label": "혼합형",
               "desc": "특별한 신호가 없어 세 채널을 고르게 쓴다 (기본값)."},
}


def describe_router_kinds() -> List[Dict[str, str]]:
    """유형 표 — Web 디버그 화면·CLI `inspect`·MCP `wiki_inspect` 가 같은 설명을 쓴다."""
    return [{"kind": k, "label": v["label"], "desc": v["desc"]} for k, v in ROUTER_KINDS.items()]


def route(query: str, store: Store) -> Dict[str, Any]:
    """질의 특성에 따라 FTS/Vector/Graph 가중치를 결정하는 적응형 라우터(휴리스틱). 임계값/가중치는 tuning(router_*)."""
    T = _T()
    kws = keywords(query)
    ent_hits = _match_entities(store, query, k=8)
    n_ent = len([e for e in ent_hits if e[1] > T.get("router_entity_min")])
    relational = any(w in query for w in _REL_WORDS)
    has_number = bool(re.search(r"\d", query))
    short = len(kws) <= T.get("router_short_kw")
    strong = len([e for e in ent_hits if e[1] >= T.get("router_strong_seed")])
    w = {"fts": 1.0, "vector": 1.0, "graph": T.get("router_base_graph")}
    kind = "hybrid"
    if short and not relational:
        w["fts"] = T.get("router_kw_fts"); w["vector"] = T.get("router_kw_vector"); w["graph"] = T.get("router_kw_graph"); kind = "keyword"
    if n_ent >= 2 or relational:
        w["graph"] = T.get("router_rel_graph_strong") if strong >= 2 else T.get("router_rel_graph"); w["vector"] = T.get("router_rel_vector"); kind = "relational"
    if has_number:
        w["fts"] += T.get("router_num_fts_bonus")
    if len(kws) >= T.get("router_long_kw") and not relational:
        w["vector"] += T.get("router_sem_vector_bonus"); kind = "semantic"
    # 왜 이 유형으로 정해졌는지를 **사람 말로** 함께 돌려준다 (2026-09-20).
    # 예전에는 `kind: "relational"` 이라는 낱말만 화면에 떠서, 그게 무엇을 뜻하고 무엇 때문에 그렇게
    # 정해졌는지 알 수 없었다. 판정에 쓰인 신호가 그대로 있으므로 설명은 여기서 만드는 것이 맞다.
    hit_rel = [x for x in _REL_WORDS if x in query]
    why = []
    if relational:
        why.append("관계를 묻는 말이 있음: %s" % ", ".join(hit_rel[:4]))
    if n_ent >= 2:
        why.append("아는 엔티티가 %d개 잡힘" % n_ent)
    if strong:
        why.append("확실한 시드 %d개" % strong)
    if short and not relational:
        why.append("키워드가 %d개로 짧음 (≤%s)" % (len(kws), T.get("router_short_kw")))
    if has_number:
        why.append("숫자가 있어 FTS 가중 +%s" % T.get("router_num_fts_bonus"))
    if len(kws) >= T.get("router_long_kw") and not relational:
        why.append("키워드가 %d개로 길어 의미 검색 가중 +%s" % (len(kws), T.get("router_sem_vector_bonus")))
    return {"kind": kind, "kind_label": ROUTER_KINDS.get(kind, {}).get("label", kind),
            "kind_desc": ROUTER_KINDS.get(kind, {}).get("desc", ""),
            "why": why or ["특별한 신호가 없어 기본(hybrid) 가중치"],
            "weights": w, "keywords": kws, "entities": [(e, round(s, 2)) for e, s in ent_hits[:6]],
            "matched_rel_words": hit_rel[:6],
            "signals": {"n_keywords": len(kws), "n_entities": n_ent, "strong_seeds": strong, "relational": relational, "has_number": has_number}}


def _match_entities(store: Store, query: str, k: int = 8) -> List[Tuple[str, float]]:
    kws = keywords(query)
    if not kws:
        return []
    match = " OR ".join('"%s"' % w.replace('"', "") for w in kws)
    hits = store.entity_fts(match, k)
    # 정확 별칭 매칭 보너스 (조사 제거 토큰이 엔티티 이름/별칭과 동일) — build_version 별 캐시된 엔티티 인덱스 사용
    out: Dict[str, float] = {e: s for e, s in hits}
    ql = query.lower()
    for e in store.entity_index():
        for n in e["names"]:
            if n in ql:
                out[e["entity_id"]] = max(out.get(e["entity_id"], 0), 5.0 + len(n) * 0.2)
                break
    return sorted(out.items(), key=lambda kv: -kv[1])[:k]


# ------------------------------------------------------------------ searches
def _merge_rows(primary: List[Tuple[str, float, str]], secondary: List[Tuple[str, float, str]], k: int) -> List[Tuple[str, float, str]]:
    seen = {c for c, _, _ in primary}
    out = list(primary)
    for r in secondary:
        if r[0] not in seen:
            out.append(r)
            seen.add(r[0])
        if len(out) >= k:
            break
    return out[:k]


def _prf_terms(store: Store, rows: List[Tuple[str, float, str]], kws: List[str], n_docs: int, n_terms: int) -> List[str]:
    """PRF: 상위 문단들의 빈출 정규화 토큰 중 질의에 없는 것 (2개 이상 문단에 등장 우선)."""
    df: Dict[str, int] = defaultdict(int)
    tf: Dict[str, int] = defaultdict(int)
    chunks = store.get_chunks([c for c, _, _ in rows[:n_docs]])
    for c in chunks.values():
        toks = [normalize_token(w) for w in words(c["heading"] + " " + c["text"])]
        seen = set()
        for t in toks:
            if len(t) < 2 or t in STOPWORDS or t in kws or t.isdigit():
                continue
            tf[t] += 1
            if t not in seen:
                df[t] += 1
                seen.add(t)
    ranked = sorted(tf.keys(), key=lambda t: (-(df[t]), -tf[t]))
    return ranked[:n_terms]


def fts_search(store: Store, query: str, k: int, synonyms: Dict[str, List[str]], prof: Profiler,
               rule_match: Optional[str] = None, stage_name: str = "fts_search", mode: Optional[str] = None) -> List[Tuple[str, float, str]]:
    """FTS5 BM25. tuning: fts_mode(tiered AND→OR | or), 컬럼 가중치, bigram 폴백, 동의어 확장, PRF.
    rule_match: query_rules 가 만든 확장 MATCH 식 (acronym/synonym OR 그룹, alias 치환, exclude NOT) — OR 티어 대신/추가로 실행."""
    T = _T()
    weights = (T.get("fts_w_heading"), T.get("fts_w_body"), T.get("fts_w_tokens"))
    snip = T.get("fts_snippet_tokens")
    mode = mode or T.get("fts_mode")
    with prof.stage(stage_name, k=k, mode=mode, weights=weights, rule_match=bool(rule_match)) as st:
        q = query
        expanded: List[str] = []
        if T.get("fts_synonym_expand"):
            for term, exps in synonyms.items():
                if term.lower() in query.lower():
                    expanded.extend(exps)
        if expanded:
            q = query + " " + " ".join(expanded)
        kws = keywords(q)
        tiers: List[str] = []
        rows: List[Tuple[str, float, str]] = []
        if mode == "tiered" and len(kws) >= 2:
            and_match = " AND ".join('"%s"' % w.replace('"', "") for w in kws)
            rows = store.fts_search(and_match, k, weights, snip)
            tiers.append("AND:%d" % len(rows))
        match = rule_match or fts_query(q, "OR")
        if len(rows) < max(1, T.get("fts_and_min_hits")) or len(rows) < k:
            or_rows = store.fts_search(match, k, weights, snip)
            tiers.append(("RULE:%d" if rule_match else "OR:%d") % len(or_rows))
            rows = _merge_rows(rows, or_rows, k)
            if rule_match and len(rows) < k:   # 규칙 확장식이 너무 좁으면 기본 OR 로 보충
                base_rows = store.fts_search(fts_query(q, "OR"), k, weights, snip)
                tiers.append("OR:%d" % len(base_rows))
                rows = _merge_rows(rows, base_rows, k)
        fallback = False
        if not rows and T.get("fts_bigram_fallback"):  # 조사 제거 + bigram 폴백
            fallback = True
            toks = tokenize_for_fts(q).split()
            match = " OR ".join('"%s"' % t for t in toks if t.startswith("_") or len(t) > 1)
            rows = store.fts_search(match, k, weights, snip) if match else []
            tiers.append("bigram:%d" % len(rows))
        if not rows and store._has_trigram():   # trigram 폴백 (한글 부분 문자열, toggles.fts_trigram 으로 색인된 경우)
            fallback = True
            rows = store.trigram_search(q, k)
            tiers.append("trigram:%d" % len(rows))
        prf_added: List[str] = []
        if T.get("prf_enabled") and rows:
            prf_added = _prf_terms(store, rows, kws, T.get("prf_docs"), T.get("prf_terms"))
            if prf_added:
                prf_match = fts_query(q + " " + " ".join(prf_added), "OR")
                prf_rows = store.fts_search(prf_match, k, weights, snip)
                rows = _merge_rows(rows, prf_rows, k)
                tiers.append("PRF:%d" % len(prf_rows))
        st.note(hits=len(rows), synonyms=expanded[:10], fallback_bigram=fallback, tiers=tiers, prf_terms=prf_added,
                top=[(cid, round(s, 3)) for cid, s, _ in rows[:5]])
        st.debug(match=match[:400], expanded_query=q if expanded else None, keywords=kws,
                 all_hits=[(cid, round(s, 3)) for cid, s, _ in rows])
        return rows


def matmul_sims(mat: np.ndarray, qv: np.ndarray, block: int = 16384) -> np.ndarray:
    """행렬이 float16 이면 블록 단위로 float32 변환해 내적 (RAM 절반 유지, BLAS 속도 유지)."""
    qv = np.asarray(qv, dtype=np.float32)
    if mat.dtype == np.float32:
        return mat @ qv
    out = np.empty(mat.shape[0], dtype=np.float32)
    for i in range(0, mat.shape[0], block):
        out[i:i + block] = mat[i:i + block].astype(np.float32) @ qv
    return out


def embed_query(store: Store, embedder: BaseEmbedder, query: str, cache: bool = True) -> Tuple[Any, bool]:
    """질의 벡터 — **내용 주소 캐시**를 먼저 본다. 반환 (vector, 캐시 적중 여부).

    2026-09-19: 예전에는 질의를 매번 처음부터 임베딩했다. 청크는 빌드 때 `embedding_cache` 에 넣으면서
    질의는 넣지 않았기 때문이다. 원격 임베더(ollama·voyage)에서는 이 한 번이 **2초**가 넘는데,
    한 질의가 규칙 대체 질의까지 4번 임베딩하므로 검색만 해도 9초가 걸렸다.
    회귀 평가·trial 은 **같은 질문 25개를 매번 다시** 임베딩했다 — 튜닝을 반복할수록 비용이 쌓인다.

    캐시는 `sha1(text)` 로 잡히므로 청크 캐시와 자연스럽게 공유된다(같은 글 = 같은 벡터).
    hash 임베더는 IDF 에 의존해 결과가 빌드마다 달라질 수 있으므로 캐시하지 않는다(어차피 로컬·즉시).
    끄려면 토글 `embed_query_cache`.
    """
    import hashlib
    name = getattr(embedder, "name", "") or ""
    model = str(getattr(embedder, "model", "") or "")
    if not cache or name in ("hash", "none", ""):
        return embedder.embed([query])[0], False
    sha = hashlib.sha1(query.encode("utf-8")).hexdigest()
    try:
        hit = store.cache_get(name, model, [sha])
        if sha in hit:
            return hit[sha], True
    except Exception:
        pass
    v = embedder.embed([query])[0]
    try:
        store.cache_put(name, model, [(sha, v)])
    except Exception:
        pass      # 캐시에 못 넣어도 검색은 계속된다
    return v, False


def vector_search(store: Store, embedder: BaseEmbedder, query: str, k: int, prof: Profiler,
                  cache_query: bool = True) -> List[Tuple[str, float]]:
    """`cache_query` 는 토글 `embed_query_cache` — 질의 벡터를 내용으로 캐시할지 (§embed_query)."""
    with prof.stage("vector_search", k=k, provider=embedder.name) as st:
        cached = store.vector_cache_info().get("loaded", False)
        t0 = time.perf_counter()
        ids, mat = store.vector_matrix(embedder.name)
        load_ms = (time.perf_counter() - t0) * 1000
        st.note(matrix_cache="hit" if cached else "miss (loaded %.0f ms)" % load_ms, n_vectors=len(ids))
        if not ids:
            st.note(hits=0, reason="no embeddings for provider %s (run build)" % embedder.name)
            return []
        t1 = time.perf_counter()
        qv, q_cached = embed_query(store, embedder, query, cache_query)
        embed_ms = (time.perf_counter() - t1) * 1000
        if qv.shape[0] != mat.shape[1]:
            st.note(hits=0, reason="dim mismatch %s vs %s" % (qv.shape[0], mat.shape[1]))
            return []
        t2 = time.perf_counter()
        sims = matmul_sims(mat, qv)
        top = np.argsort(-sims)[:k]
        min_sim = _T().get("vector_min_sim")
        out = [(ids[i], float(sims[i])) for i in top if sims[i] > max(0.0, min_sim)]
        st.note(query_embed_cache="hit" if q_cached else "miss")
        st.note(hits=len(out), top=[(c, round(s, 3)) for c, s in out[:5]], dim=int(mat.shape[1]),
                embed_ms=round(embed_ms, 1), matmul_ms=round((time.perf_counter() - t2) * 1000, 1),
                matrix_mb=round(mat.nbytes / 1e6, 1))
        st.debug(all_hits=[(c, round(s, 3)) for c, s in out], sim_stats={"max": round(float(sims.max()), 3),
                 "mean": round(float(sims.mean()), 4)} if len(sims) else {})
        return out


def graph_search(store: Store, query: str, k: int, hops: int, prof: Profiler,
                 seed_entities: Optional[List[Tuple[str, float]]] = None) -> Dict[str, Any]:
    """LightRAG 식 이중 검색: (1) 질의→엔티티 매칭 (2) n-hop 확장 (3) 엔티티 멘션 청크 수집 + 관계 텍스트."""
    T = _T()
    with prof.stage("graph_search", k=k, hops=hops) as st:
        seeds = seed_entities or _match_entities(store, query, 8)
        seeds = [(e, s) for e, s in seeds if s > T.get("graph_seed_min")][:T.get("graph_max_seeds")]
        if not seeds:
            st.note(hits=0, reason="no entity matched")
            return {"chunks": [], "entities": [], "relations": [], "paths": []}
        ent_score: Dict[str, float] = {e: s for e, s in seeds}
        cap = max(s for _, s in seeds)  # 확장 노드 점수는 시드 최고점을 넘지 못함 (허브 폭증 방지)
        deg = {e["entity_id"]: max(1.0, e["degree"]) for e in store.entity_index()}   # 캐시된 인덱스 (전체 SELECT 회피)
        frontier = [e for e, _ in seeds]
        visited = set(frontier)
        rel_used: List[Dict[str, Any]] = []
        hop_stats: List[Dict[str, Any]] = []
        prov_w = parse_weight_map(T.get("provenance_w"), 0.5)
        for h in range(hops):
            t_h = time.perf_counter()
            nbrs = store.neighbors(frontier)
            snapshot = dict(ent_score)          # 홉 내 점수는 홉 시작 시점 기준 (누적 폭증 방지)
            gained: Dict[str, float] = defaultdict(float)
            decay = T.get("graph_decay") ** (h + 1)
            hub_exp = T.get("graph_hub_exp")
            revisit = T.get("graph_revisit_factor")
            for r in nbrs:
                if r["rel"] in ("mentions_date", "mentions_amount"):
                    continue
                other = r["dst"] if r["src"] in visited else r["src"]
                base = max(snapshot.get(r["src"], 0), snapshot.get(r["dst"], 0))
                # 허브 페널티: 차수^hub_exp 로 정규화 (문서/역할 노드가 모든 것을 연결하는 문제 완화) · provenance 가중
                pw = prov_w.get(r.get("provenance") or "", 0.5)
                gain = base * decay * float(r["weight"] or 0.1) * pw / (deg.get(other, 1.0) ** hub_exp)
                gained[other] += gain if other not in visited else gain * revisit
                rel_used.append(dict(r, gain=round(gain, 3)))
            nxt = sorted((o for o in gained if o not in visited), key=lambda o: -gained[o])[:T.get("graph_frontier")]
            for o, g in gained.items():
                ent_score[o] = min(cap, ent_score.get(o, 0) + g)
            visited.update(nxt)
            hop_stats.append({"hop": h + 1, "frontier_in": len(frontier), "relations": len(nbrs), "new_nodes": len(nxt),
                              "ms": round((time.perf_counter() - t_h) * 1000, 1)})
            frontier = nxt
            if not frontier:
                break
        # 시드 엔티티가 2개 이상 등장하는 청크 우선 (multi-hop 근거)
        seed_ids = [e for e, _ in seeds]
        seed_hits = store.chunks_for_entities(seed_ids, limit=k * 6)
        top_ents = sorted(ent_score.items(), key=lambda kv: -kv[1])[:T.get("graph_top_entities")]
        ext_hits = store.chunks_for_entities([e for e, _ in top_ents], limit=k * 6)
        chunk_score: Dict[str, float] = defaultdict(float)
        for cid, s in seed_hits:
            chunk_score[cid] += T.get("graph_seed_chunk_w") * s
        for cid, s in ext_hits:
            chunk_score[cid] += s
        # 노드 → 원본 문서(doc_refs): 시드/상위 엔티티가 참조하는 문서의 첫 청크를 문서 단위 후보로 추가 (ID 노드는 문서 자체가 정답인 경우가 많음)
        n_refs = T.get("graph_doc_refs_n")
        doc_ref_added: List[str] = []
        if n_refs > 0:
            for eid, sc in (seeds + top_ents[:10]):
                e = store.get_entity(eid) or {}
                try:
                    refs = json.loads(e.get("doc_refs") or "[]")
                except Exception:
                    refs = []
                for ref in refs[:n_refs]:
                    fc = ref.get("first_chunk")
                    if fc and fc not in chunk_score:
                        chunk_score[fc] += 0.5 * sc * (1.0 if eid in seed_ids else 0.5)
                        doc_ref_added.append(fc)
        # 관계가 근거로 삼는 청크에 순위 기반 보너스 (gain 절대값은 쓰지 않음)
        for i, r in enumerate(sorted(rel_used, key=lambda r: -r["gain"])[:T.get("graph_rel_bonus_n")]):
            if r.get("chunk_id"):
                chunk_score[r["chunk_id"]] += 1.0 / (i + 1) + 0.2
        # 이중 검색(dual-level): 엔티티 기반 후보를 질의 키워드 커버리지로 재가중 (LightRAG 의 low-level keyword 매칭에 해당)
        kws = keywords(query)
        if kws and chunk_score:
            cand = sorted(chunk_score.items(), key=lambda kv: -kv[1])[: max(k * 4, 30)]
            texts = store.get_chunks([c for c, _ in cand])
            rescored: Dict[str, float] = {}
            for cid, sc in cand:
                c = texts.get(cid)
                if not c:
                    continue
                low = (c["heading"] + " " + c["text"]).lower()
                toks = set(normalize_token(w) for w in words(low))
                cover = sum(1 for kw in kws if kw in toks or kw in low) / float(len(kws))
                rescored[cid] = sc * (T.get("graph_cover_base") + cover) + cover * T.get("graph_cover_w")
            chunk_score = rescored
        chunks = sorted(chunk_score.items(), key=lambda kv: -kv[1])[:k]
        ents_out = [{"entity_id": e, "score": round(s, 3), **_ent_brief(store, e)} for e, s in top_ents[:15]]
        rels_out = [{"src": _name(store, r["src"]), "dst": _name(store, r["dst"]), "rel": r["rel"],
                     "description": r["description"], "weight": r["weight"], "chunk_id": r["chunk_id"], "gain": r["gain"],
                     "provenance": r.get("provenance") or "", "confidence": r.get("confidence")}
                    for r in sorted(rel_used, key=lambda r: -r["gain"])[:25]]
        prov_used: Dict[str, int] = defaultdict(int)
        for r in rel_used:
            prov_used[r.get("provenance") or "?"] += 1
        st.note(seeds=[(_name(store, e), round(s, 2)) for e, s in seeds], expanded_entities=len(ent_score),
                relations_traversed=len(rel_used), hits=len(chunks), top=[(c, round(s, 2)) for c, s in chunks[:5]],
                hops=hop_stats, provenance=dict(prov_used), doc_ref_candidates=len(doc_ref_added))
        st.debug(seed_chunk_hits=len(seed_hits), expanded_chunk_hits=len(ext_hits), keyword_cover_rescored=bool(kws),
                 top_entities=[(e["name"], e["score"]) for e in ents_out[:10]], doc_ref_chunks=doc_ref_added[:10])
        return {"chunks": chunks, "entities": ents_out, "relations": rels_out,
                "seeds": [(_name(store, e), round(s, 2)) for e, s in seeds], "provenance": dict(prov_used)}


CHANNELS: Tuple[str, ...] = ("fts", "vector", "graph")
CHANNEL_MODES: Tuple[str, ...] = ("or", "and", "rrf")


def parse_channels(value: Any) -> List[str]:
    """'fts,vector' · ['fts','graph'] · 'all' → 정규화된 채널 목록 (순서는 CHANNELS 기준, 중복 제거)."""
    if value is None or value == "":
        return ["fts"]
    if isinstance(value, str):
        parts = [x.strip().lower() for x in value.replace("+", ",").split(",") if x.strip()]
    else:
        parts = [str(x).strip().lower() for x in value if str(x).strip()]
    if "all" in parts or "*" in parts:
        return list(CHANNELS)
    return [c for c in CHANNELS if c in parts] or ["fts"]


def channel_search(store: Store, embedder: BaseEmbedder, settings: Settings, query: str,
                   channels: Any = "fts", mode: str = "or", k: int = 8,
                   prof: Optional[Profiler] = None,
                   require: Any = None, exclude: Any = None, acl: Any = None,
                   doc_types: Any = None) -> Dict[str, Any]:
    """**여러 채널을 한 번에 돌려 조합한다** — 채널 검색 디버그의 공용 엔진 (2026-09-19).

    예전에는 CLI·Web·MCP 모두 채널을 **하나만** 볼 수 있었다. 그래서 "이 청크는 FTS 는 찾았는데
    벡터는 못 찾았나?" 처럼 정작 알고 싶은 것을 보려면 세 번 돌려 눈으로 맞춰야 했다.

    조합 방식(mode)
      - `or`  합집합. 고른 채널 중 **하나라도** 찾은 청크. 커버리지(recall)를 본다.
      - `and` 교집합. 고른 채널이 **모두** 찾은 청크. 채널 합의가 강한 근거를 본다.
      - `rrf` 실제 질의 경로와 **같은 가중 RRF 융합**. 라우터 가중치를 그대로 쓴다.

    or/and 의 정렬은 "채널 수 많은 순 → 채널별 최고 순위가 앞선 순" 이라 설명 가능하다.
    rrf 는 fusion 단계의 점수를 그대로 쓴다.

    `doc_types` 는 **거르는** 조건이다 (2026-09-19). 질의 경로의 `doc_types` 가 *가중치를 올리는* 것과
    다르다 — 여기서는 "이슈 문서만 보고 싶다" 가 목적이라 나머지를 빼는 것이 맞다. 채널별 원본 목록에서
    먼저 빼므로 `per_channel` 의 발췌로도 새어 나가지 않는다 (문서 접근 제어와 같은 자리).

    반환: {query, channels, mode, k, per_channel{ch:{n,ms,rows}}, rows[], graph?, counts, weights, doc_types}
    """
    prof = prof or Profiler("search")
    req = parse_channels(require) if require else []
    exc = parse_channels(exclude) if exclude else []
    any_ = parse_channels(channels) if channels else []
    # 복합 조건: 필수(AND) · 포함(OR) · 제외(NOT) 를 섞을 수 있다.
    #   (포함 중 하나라도) 그리고 (필수 전부) 그리고 (제외에 없음)
    # 예: require=[graph], any=[fts,vector] → "(FTS 또는 Vector) 그리고 Graph"
    # 제외 채널도 결과를 알아야 뺄 수 있으므로 실제로 돌린다.
    chans = [c for c in CHANNELS if c in set(any_) | set(req) | set(exc)] or ["fts"]
    mode = str(mode or "or").lower()
    if mode not in CHANNEL_MODES:
        mode = "or"
    composite = bool(req or exc)
    k = max(1, int(k or 8))

    per: Dict[str, Dict[str, Any]] = {}
    lists: Dict[str, List[Tuple[str, float]]] = {}
    snippets: Dict[str, str] = {}
    graph_out: Optional[Dict[str, Any]] = None

    if "fts" in chans:
        t0 = time.perf_counter()
        rows = fts_search(store, query, k, store.synonyms(), prof)
        for cid, sc, sn in rows:
            snippets.setdefault(cid, sn or "")
        lists["fts"] = [(cid, sc) for cid, sc, _ in rows]
        per["fts"] = {"n": len(rows), "ms": round((time.perf_counter() - t0) * 1000, 1),
                      "rows": [{"chunk_id": cid, "score": round(float(sc), 4), "rank": i + 1, "snippet": (sn or "")[:200]}
                               for i, (cid, sc, sn) in enumerate(rows)]}
    if "vector" in chans:
        t0 = time.perf_counter()
        rows_v = vector_search(store, embedder, query, k, prof,
                               bool(getattr(getattr(settings, "toggles", None), "embed_query_cache", True)))
        lists["vector"] = list(rows_v)
        per["vector"] = {"n": len(rows_v), "ms": round((time.perf_counter() - t0) * 1000, 1),
                         "provider": embedder.name,
                         "rows": [{"chunk_id": cid, "score": round(float(sc), 4), "rank": i + 1}
                                  for i, (cid, sc) in enumerate(rows_v)]}
    if "graph" in chans:
        t0 = time.perf_counter()
        g = graph_search(store, query, k, int(getattr(settings, "graph_hops", 2) or 2), prof)
        lists["graph"] = [(cid, sc) for cid, sc in (g.get("chunks") or [])]
        graph_out = {"seeds": g.get("seeds"), "entities": (g.get("entities") or [])[:10],
                     "relations": (g.get("relations") or [])[:10], "provenance": g.get("provenance")}
        per["graph"] = {"n": len(lists["graph"]), "ms": round((time.perf_counter() - t0) * 1000, 1),
                        "rows": [{"chunk_id": cid, "score": round(float(sc), 4), "rank": i + 1}
                                 for i, (cid, sc) in enumerate(lists["graph"])]}

    # 문서 유형 필터 — 접근 제어와 같은 자리에서 **채널별 원본 목록부터** 뺀다.
    # 나중에 최종 행에서만 빼면 per_channel 의 건수·발췌가 실제와 달라 보인다.
    want_types = [str(t).strip() for t in (doc_types if isinstance(doc_types, (list, tuple, set))
                                           else str(doc_types or "").split(",")) if str(t).strip()]
    type_removed = 0
    if want_types:
        meta = store.doc_meta_map()
        allow = set(want_types)

        def _type_ok(cid: str) -> bool:
            did = str(cid or "").rsplit("#", 1)[0]
            m = meta.get(did) or {}
            return str((m or {}).get("doc_type") or "") in allow
        for ch in list(lists):
            keep_rows = [(cid, sc) for cid, sc in lists[ch] if _type_ok(cid)]
            type_removed += len(lists[ch]) - len(keep_rows)
            lists[ch] = keep_rows
            if ch in per:
                per[ch]["rows"] = [r for r in per[ch]["rows"] if _type_ok(r.get("chunk_id") or "")]
                per[ch]["n"] = len(per[ch]["rows"])
                per[ch]["doc_type_filtered"] = True

    # 문서 단위 접근 제어 — 채널별 원본 목록에서 먼저 뺀다 (llmwiki/docacl.py).
    # 여기서 빼지 않으면 per_channel 의 snippet 으로 본문이 그대로 새어 나간다.
    acl_removed = 0
    if acl is not None and getattr(acl, "enabled", False):
        for ch in list(lists):
            keep_rows = [(cid, sc) for cid, sc in lists[ch] if acl.chunk_ok(cid)]
            acl_removed += len(lists[ch]) - len(keep_rows)
            lists[ch] = keep_rows
            if ch in per:
                per[ch]["rows"] = [r for r in per[ch]["rows"] if acl.chunk_ok(r.get("chunk_id") or "")]
                per[ch]["n"] = len(per[ch]["rows"])
                per[ch]["acl_blocked"] = True

    rank_of: Dict[str, Dict[str, Dict[str, float]]] = {}
    for ch, rows_c in lists.items():
        for i, (cid, sc) in enumerate(rows_c):
            rank_of.setdefault(cid, {})[ch] = {"rank": i + 1, "score": round(float(sc), 4)}

    def keep(cid: str) -> bool:
        chm = rank_of.get(cid) or {}
        if any(c in chm for c in exc):          # 제외 채널이 찾은 것은 뺀다
            return False
        if not all(c in chm for c in req):      # 필수 채널은 모두 찾아야 한다
            return False
        if any_:                                # 포함 채널이 있으면 그중 하나는 찾아야 한다
            return any(c in chm for c in any_)
        return bool(req)                        # 포함이 없으면 필수만으로 판단

    weights: Dict[str, float] = {}
    if mode == "rrf" and not composite:
        r = route(query, store)
        weights = {ch: float((r.get("weights") or {}).get(ch, 1.0)) for ch in lists}
        fused = rrf_fuse({ch: rows_c for ch, rows_c in lists.items()}, weights, k, prof)
        order = [(h.chunk_id, float(h.fused)) for h in fused]
    elif composite:
        cand = [cid for cid in rank_of if keep(cid)]
        cand.sort(key=lambda cid: (-len(rank_of[cid]), min(v["rank"] for v in rank_of[cid].values()), cid))
        order = [(cid, round(1.0 / min(v["rank"] for v in rank_of[cid].values()), 4)) for cid in cand]
    else:
        need = len(chans) if mode == "and" else 1
        cand = [cid for cid, chm in rank_of.items() if len(chm) >= need]
        cand.sort(key=lambda cid: (-len(rank_of[cid]), min(v["rank"] for v in rank_of[cid].values()), cid))
        order = [(cid, round(1.0 / min(v["rank"] for v in rank_of[cid].values()), 4)) for cid in cand]

    rows_out: List[Dict[str, Any]] = []
    for cid, score in order[:k if mode != "or" else max(k, len(order))]:
        c = dict(store.get_chunk(cid) or {})      # sqlite3.Row → dict (Row 는 .get 이 없다)
        # 이중 방어: 위에서 이미 걸렀지만 doc_id 가 chunk_id 와 다른 경우를 대비해 한 번 더 본다.
        if acl is not None and getattr(acl, "enabled", False) and not acl.chunk_ok(cid, c.get("doc_id") or ""):
            acl_removed += 1
            continue
        rows_out.append({"chunk_id": cid, "score": score, "channels": rank_of.get(cid, {}),
                         "n_channels": len(rank_of.get(cid, {})),
                         "doc_id": c.get("doc_id"), "heading": c.get("heading"),
                         "snippet": snippets.get(cid) or (str(c.get("text") or "")[:200])})
    inter = sum(1 for chm in rank_of.values() if len(chm) == len(chans))
    if composite:
        expr = " 그리고 ".join(filter(None, [
            ("(" + " 또는 ".join(any_) + ")") if any_ else "",
            " 그리고 ".join(req) if req else "",
            ("(제외: " + ", ".join(exc) + ")") if exc else ""]))
    else:
        expr = (" 또는 " if mode == "or" else " 그리고 " if mode == "and" else " + ").join(chans) + \
               (" [가중 RRF]" if mode == "rrf" else "")
    if want_types:
        expr += " · 문서유형 %s" % ",".join(want_types)
    return {"query": query, "channels": chans, "mode": ("composite" if composite else mode), "k": k,
            "require": req, "any": any_, "exclude": exc, "expr": expr, "doc_types": want_types,
            "per_channel": per, "rows": rows_out, "graph": graph_out, "weights": weights,
            "acl": (dict(acl.summary(), removed=acl_removed)
                    if acl is not None and getattr(acl, "enabled", False) else {"enabled": False}),
            "counts": {"union": len(rank_of), "intersection": inter, "returned": len(rows_out),
                       "acl_blocked": acl_removed, "doc_type_filtered": type_removed}}


def parse_weight_map(s: Any, default: float = 1.0) -> Dict[str, float]:
    """'a:1.0,b:0.5' → {a:1.0, b:0.5}"""
    out: Dict[str, float] = {}
    for part in str(s or "").split(","):
        if ":" in part:
            k, _, v = part.partition(":")
            try:
                out[k.strip()] = float(v)
            except ValueError:
                pass
    return out


def _ent_brief(store: Store, eid: str) -> Dict[str, Any]:
    e = store.get_entity(eid) or {}
    return {"name": e.get("name", eid), "type": e.get("type", "?"), "community": e.get("community")}


def _name(store: Store, eid: str) -> str:
    e = store.get_entity(eid)
    return e["name"] if e else eid


# ------------------------------------------------------------------ fusion
def rrf_fuse(lists: Dict[str, List[Tuple[str, float]]], weights: Dict[str, float], k: int, prof: Profiler) -> List[Hit]:
    """채널 결과 융합. tuning: fusion_method(rrf | weighted min-max), fusion_multi_bonus."""
    T = _T()
    method = T.get("fusion_method")
    bonus = T.get("fusion_multi_bonus")
    with prof.stage("rrf_fuse", rrf_k=k, method=method, weights=weights, sources={n: len(v) for n, v in lists.items()}) as st:
        hits: Dict[str, Hit] = {}
        for name, lst in lists.items():
            w = weights.get(name, weights.get(name.split("_")[0], 1.0))
            if method == "weighted" and lst:
                vals = [s for _, s in lst]
                lo, hi = min(vals), max(vals)
                span = (hi - lo) or 1.0
            for rank, (cid, score) in enumerate(lst):
                h = hits.setdefault(cid, Hit(cid))
                h.scores[name] = round(score, 4)
                h.ranks[name] = rank + 1
                if method == "weighted":
                    h.fused += w * ((score - lo) / span) / 50.0   # rrf 와 비슷한 크기(≈0.02)로 맞춤
                else:
                    h.fused += w * 1.0 / (k + rank + 1)
        if bonus:
            for h in hits.values():
                if len(h.ranks) > 1:
                    h.fused += bonus * (len(h.ranks) - 1)
        out = sorted(hits.values(), key=lambda h: -h.fused)
        for h in out:
            h.why = ["%s#%d" % (n, r) for n, r in sorted(h.ranks.items(), key=lambda kv: kv[1])]
        names = list(lists.keys())
        overlap = {}
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a = {c for c, _ in lists[names[i]]}
                b = {c for c, _ in lists[names[j]]}
                overlap["%s∩%s" % (names[i], names[j])] = len(a & b)
        st.note(candidates=len(out), multi_source=sum(1 for h in out if len(h.ranks) > 1), overlap=overlap,
                top=[(h.chunk_id, round(h.fused, 4), h.why) for h in out[:6]])
        return out


# ------------------------------------------------------------------ rerank
def RERANK_SYSTEM() -> str:   # prompts/rerank.md
    from . import prompts as _prompts
    return _prompts.get("rerank")


_CE_CACHE: Dict[str, Any] = {}


def _cross_encoder(model: str):
    """sentence-transformers CrossEncoder (지연 로드, 프로세스당 1회). 실패 시 None."""
    if model in _CE_CACHE:
        return _CE_CACHE[model]
    try:
        from sentence_transformers import CrossEncoder  # noqa
        _CE_CACHE[model] = CrossEncoder(model)
    except Exception as e:  # 미설치/다운로드 실패
        _CE_CACHE[model] = None
        _CE_CACHE[model + ":error"] = str(e)[:200]
    return _CE_CACHE[model]


def final_order(cands: List[Hit], w_fused: float, ranked: Optional[List[Hit]] = None) -> List[Hit]:
    """리랭크 결과를 최종 순위로 정렬한다 — **네 가지 리랭크 방식이 모두 이 함수를 쓴다**.

    왜 한 곳에 모았나: 예전에는 방식마다 정렬이 달랐다(api·local 은 fused 를 동점 처리에만,
    cross_encoder 는 아예 무시, llm 은 준 순서 그대로). 그래서 "리랭크 방식을 바꿨더니 pin 이
    사라졌다" 같은 일이 생겨도 어디를 봐야 할지 알 수 없었다.

    `w_fused`(tuning `rerank_fused_w`) 가 0 이면 예전 동작 그대로 — 리랭크 점수로만 줄을 세우고
    fused 는 동점일 때만 본다. 0보다 크면 후보 집합 안에서 두 점수를 각각 0~1 로 정규화해
    `rerank + w × fused` 로 합친다. 정규화하는 이유는 척도가 제각각이기 때문이다
    (api 0~1 · local 0~2 · llm 은 남은 개수).

    ranked 를 주면 그 순서를 리랭크 점수 대신 쓴다 (LLM 이 순위만 돌려주는 경우).
    """
    if ranked is None:
        ranked = cands
    if w_fused <= 0:
        return sorted(ranked, key=lambda h: (-(h.rerank or 0), -h.fused))
    rr = [(h.rerank or 0.0) for h in ranked]
    fu = [h.fused for h in ranked]
    def _norm(vals):
        lo, hi = min(vals), max(vals)
        rng = hi - lo
        return [1.0] * len(vals) if rng <= 0 else [(v - lo) / rng for v in vals]
    rn, fn = _norm(rr) if rr else [], _norm(fu) if fu else []
    score = {id(h): rn[i] + w_fused * fn[i] for i, h in enumerate(ranked)}
    return sorted(ranked, key=lambda h: (-score[id(h)], -(h.rerank or 0), -h.fused))


def rerank(hits: List[Hit], chunks: Dict[str, Any], query: str, llm: Optional[BaseLLM], top_n: int, prof: Profiler,
           effort: str = "low", use_llm: bool = True, n_cands: int = 0, chunk_chars: int = 600, settings: Any = None,
           doc_tokens: Optional[Dict[str, str]] = None) -> List[Hit]:
    """리랭크. tuning: rerank_method(auto|api|llm|cross_encoder|local), local 가중치, heading 보너스, 크로스인코더 모델.
    settings 가 주어지고 rerank_url 이 있으면 auto 는 api 를 우선한다.
    doc_tokens: doc_id → 메타 토큰(문서 ID·유형 등) — local 리랭크 커버리지가 청크 본문에 없는 문서 ID 도 인식하도록."""
    T = _T()
    method = T.get("rerank_method")
    w_fused = float(T.get("rerank_fused_w") or 0.0)   # 융합·부스트 점수를 최종 순위에 섞는 비중 (0 = 예전 동작)
    cands = hits[: (n_cands or max(top_n * 2, 10))]
    before = [h.chunk_id for h in cands]
    if settings is not None and (method == "api" or (method == "auto" and settings.rerank_url)):
        from .rerankers import rerank_api
        with prof.stage("rerank_api", candidates=len(cands), url=settings.rerank_url, model=settings.rerank_api_model) as st:
            docs = [((chunks[h.chunk_id]["heading"] + "\n" + chunks[h.chunk_id]["text"][:chunk_chars]) if h.chunk_id in chunks else "") for h in cands]
            try:
                order, meta = rerank_api(settings, query, docs, top_n=len(cands))
                sc = {i: s for i, s in order}
                for i, h in enumerate(cands):
                    h.rerank = round(sc.get(i, 0.0), 4)
                ranked = final_order(cands, w_fused)
                st.note(ms_api=round(meta.get("ms", 0)), model=meta.get("model"), fused_w=w_fused,
                        top=[(h.chunk_id, h.rerank) for h in ranked[:5]])
                st.debug(before=before, after=[h.chunk_id for h in ranked[:top_n]])
                return ranked[:top_n] + hits[len(cands):]
            except LLMError as e:
                st.note(error=str(e)[:200], fallback="local" if method == "api" else "llm/local")
                if method == "api":
                    method = "local"
    if method == "cross_encoder":
        ce_model = T.get("rerank_ce_model")
        with prof.stage("rerank_cross_encoder", candidates=len(cands), model=ce_model) as st:
            ce = _cross_encoder(ce_model)
            if ce is not None:
                pairs = [(query, (chunks[h.chunk_id]["heading"] + "\n" + chunks[h.chunk_id]["text"][:chunk_chars]) if h.chunk_id in chunks else "") for h in cands]
                scores = [float(x) for x in ce.predict(pairs)]
                for h, sc in zip(cands, scores):
                    h.rerank = round(sc, 4)
                ranked = final_order(cands, w_fused)
                st.note(fused_w=w_fused, top=[(h.chunk_id, h.rerank) for h in ranked[:5]])
                st.debug(before=before, after=[h.chunk_id for h in ranked[:top_n]])
                return ranked[:top_n] + hits[len(cands):]
            st.note(error=_CE_CACHE.get(ce_model + ":error", "unavailable"), fallback="local")
    want_llm = (method == "auto" and use_llm) or method == "llm"
    if want_llm and llm and llm.available:
        with prof.stage("rerank_llm", candidates=len(cands), model=getattr(llm, "model", llm.name), role=getattr(llm, "role", "")) as st:
            lines = ["질문: " + query, ""]
            for i, h in enumerate(cands):
                c = chunks.get(h.chunk_id)
                if not c:
                    continue
                lines.append("[%d] (%s) %s" % (i, c["heading"][:60], c["text"][:chunk_chars].replace("\n", " ")))
            prompt = "\n\n".join(lines)
            sys_p = RERANK_SYSTEM()
            st.note(prompt_chars=len(prompt) + len(sys_p), est_input_tokens=(len(prompt) + len(sys_p)) // 3)
            st.sample(prompt=prompt[:6000])
            try:
                mt = settings.role_max_tokens("rerank", 400) if settings is not None and hasattr(settings, "role_max_tokens") else 400
                r = llm.complete(sys_p, prompt, max_tokens=mt, effort=effort, json_mode=True)
                st.sample(response=r["text"][:2000])
                data = parse_json(r["text"]) or {}
                order = [int(i) for i in data.get("ranking", []) if isinstance(i, (int, float)) and 0 <= int(i) < len(cands)]
                seen = set()
                ranked: List[Hit] = []
                for i in order:
                    if i not in seen:
                        seen.add(i)
                        cands[i].rerank = float(len(order) - len(ranked))
                        ranked.append(cands[i])
                rest = [h for i, h in enumerate(cands) if i not in seen]
                for h in rest:
                    h.rerank = 0.0
                # rerank_fused_w 가 0 이면 LLM 이 준 순서 그대로, 0보다 크면 융합·부스트를 섞는다
                out = final_order(cands, w_fused, ranked=(ranked + rest)) if w_fused > 0 else (ranked + rest)
                st.note(usage=r.get("usage"), ms_llm=round(r.get("ms", 0)), order=order[:top_n], fused_w=w_fused)
                st.debug(before=before, after=[h.chunk_id for h in out[:top_n]])
                return out[:top_n] + hits[len(cands):]
            except (LLMError, ValueError) as e:
                st.note(error=str(e)[:200], fallback="local")
    elif want_llm and llm is not None and not llm.available:
        prof.skipped("rerank_llm", "LLM provider unavailable → local heuristic")
    elif method in ("auto", "llm") and not use_llm:
        prof.skipped("rerank_llm", "rerank_llm off → local heuristic (0 tokens)")
    elif method == "local":
        prof.skipped("rerank_llm", "rerank_method=local")
    w_cover, w_cons, w_len, w_head = T.get("rerank_w_cover"), T.get("rerank_w_consensus"), T.get("rerank_w_length"), T.get("rerank_heading_bonus")
    with prof.stage("rerank_local", candidates=len(cands),
                    weights={"cover": w_cover, "consensus": w_cons, "length": w_len, "heading": w_head, "fused": w_fused}) as st:
        kws = [k for k in keywords(query)]
        n_src = max(1, len(set(s for h in cands for s in h.ranks)))
        for h in cands:
            c = chunks.get(h.chunk_id)
            if not c:
                h.rerank = 0.0
                continue
            text = (c["heading"] + " " + c["text"] + " " + ((doc_tokens or {}).get(c["doc_id"], ""))).lower()
            toks = set(normalize_token(w) for w in words(text))
            cover = sum(1 for k in kws if k in toks or k in text) / max(1, len(kws))
            head = (c["heading"] or "").lower()
            head_hit = any(k in head for k in kws)
            # 다중 소스 합의 보너스 + 커버리지 + 길이 패널티(너무 짧은 청크) + 헤딩 일치 보너스
            consensus = len(h.ranks) / float(n_src)
            if h.chunk_id.startswith("ext:"):
                # 외부 RAG 결과는 리스트가 하나뿐이라 내부 채널 합의와 비교할 수 없다 → 그 소스 안의 순위(1/rank)를 합의값으로 쓴다
                ext_ranks = [r for n_, r in h.ranks.items() if n_.startswith("ext_")]
                consensus = 1.0 / float(min(ext_ranks)) if ext_ranks else 0.0
            h.rerank = round(w_cover * cover + w_cons * consensus + w_len * min(1.0, len(c["text"]) / 400.0) + (w_head if head_hit else 0.0), 4)
        ranked = final_order(cands, w_fused)
        st.note(top=[(h.chunk_id, h.rerank) for h in ranked[:5]], keywords=kws, fused_w=w_fused)
        st.debug(before=before, after=[h.chunk_id for h in ranked[:top_n]],
                 moved=sum(1 for i, h in enumerate(ranked[:top_n]) if i >= len(before) or before[i] != h.chunk_id))
        return ranked[:top_n] + hits[len(cands):]
