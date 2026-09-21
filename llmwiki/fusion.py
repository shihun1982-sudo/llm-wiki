# -*- coding: utf-8 -*-
"""채널 융합 + post-fusion boost.

방식(fusion_method):
  rrf       : Σ w_c / (k + rank)                      — 점수 척도 무관, 강건 (기본)
  weighted / minmax : Σ w_c · minmax(score) / 50      — 한 채널의 확신을 살림, outlier 민감
  zscore    : Σ w_c · sigmoid(z(score))/50            — 채널별 분포 정규화
  dbsf      : Σ w_c · clip((s-μ)/(3σ)+0.5, 0, 1)/50   — 3σ 정규화(distribution-based score fusion)
  rrf_boost : rrf + 0.3 · minmax 가중합                — 순위 안정성 + 점수 신호 소량
post-fusion boost(방식 무관, 곱셈): 다중 채널 합의 · 문서유형(doc_type_boost, 라우터 힌트) · 시간(time_scope) · 최신성(recency)
  · pin · provenance(그래프 후보) · 피드백 · exclude 페널티. 모든 배율은 Hit.boosts 에 기록되어 프로파일/설명에 쓰인다.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .retrieval import Hit, parse_weight_map
from . import tuning as _tuning


def _norm_lists(lst: List[Tuple[str, float]], method: str) -> Dict[str, float]:
    vals = [s for _, s in lst]
    if not vals:
        return {}
    if method in ("weighted", "minmax", "rrf_boost"):
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        return {c: (s - lo) / span for c, s in lst}
    if method == "zscore":
        mu = sum(vals) / len(vals)
        sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        return {c: 1.0 / (1.0 + math.exp(-(s - mu) / sd)) for c, s in lst}
    if method == "dbsf":
        mu = sum(vals) / len(vals)
        sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        return {c: max(0.0, min(1.0, (s - mu) / (3 * sd) + 0.5)) for c, s in lst}
    return {}


BASE_CHANNELS = ("fts", "vector", "graph", "doc_vector", "external")


def base_channel(name: str) -> str:
    """리스트 이름 → 기본 채널. ext_<src> → external, doc_vector → doc_vector, 그 밖은 '_' 앞부분 (fts_alt1 → fts, vector_alt2 → vector)."""
    if name.startswith("ext_"):
        return "external"
    if name == "doc_vector" or name.startswith("doc_vector_"):
        return "doc_vector"
    return name.split("_")[0]


def topk_map(T: Any) -> Dict[str, Tuple[int, float, float]]:
    """tuning 의 <채널>_topk_n / _topk_w / _tail_w 를 fuse(topk=) 형태로. n=0 인 채널은 뺀다 (= 구간 가중 끔)."""
    out: Dict[str, Tuple[int, float, float]] = {}
    for ch in BASE_CHANNELS:
        n = int(T.get("%s_topk_n" % ch) or 0)
        if n > 0:
            out[ch] = (n, float(T.get("%s_topk_w" % ch)), float(T.get("%s_tail_w" % ch)))
    return out


def fuse(lists: Dict[str, List[Tuple[str, float]]], weights: Dict[str, float], k: int, method: str = "rrf",
         multi_bonus: float = 0.0, topk: Optional[Dict[str, Tuple[int, float, float]]] = None) -> Tuple[List[Hit], Dict[str, Any]]:
    """채널 리스트 융합 → Hit[] (fused 내림차순) + 메타(overlap 등).

    topk: {기본채널: (n, topk_w, tail_w)} — 그 채널(보조 리스트 포함)의 순위 r 에 r ≤ n 이면 topk_w, 아니면 tail_w 를 곱한다.
    tail_w == 0 이면 top-k 밖 항목은 그 리스트에서 아예 참여하지 않는다. 적용 배율은 Hit.boosts["topk_<채널>"] 에 남긴다 (채널당 한 번).
    """
    hits: Dict[str, Hit] = {}
    topk = topk or {}
    topk_in = topk_out = topk_dropped = 0
    for name, lst in lists.items():
        w = weights.get(name, weights.get(name.split("_")[0], 1.0))
        norm = _norm_lists(lst, method)
        seg = topk.get(base_channel(name)) if topk else None
        for rank, (cid, score) in enumerate(lst):
            factor = 1.0
            if seg and seg[0] > 0:
                inside = (rank + 1) <= seg[0]
                factor = seg[1] if inside else seg[2]
                if inside:
                    topk_in += 1
                elif factor == 0:
                    topk_dropped += 1
                    continue
                else:
                    topk_out += 1
            h = hits.setdefault(cid, Hit(cid))
            h.scores[name] = round(score, 4)
            h.ranks[name] = rank + 1
            if factor != 1.0:
                h.boosts.setdefault("topk_" + base_channel(name), round(factor, 3))
            wf = w * factor
            if method in ("weighted", "minmax", "zscore", "dbsf"):
                h.fused += wf * norm.get(cid, 0.0) / 50.0
            elif method == "rrf_boost":
                h.fused += wf * (1.0 / (k + rank + 1) + 0.3 * norm.get(cid, 0.0) / 50.0)
            else:
                h.fused += wf * 1.0 / (k + rank + 1)
    if multi_bonus:
        for h in hits.values():
            if len(h.ranks) > 1:
                h.fused += multi_bonus * (len(h.ranks) - 1)
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
    meta = {"candidates": len(out), "multi_source": sum(1 for h in out if len(h.ranks) > 1), "overlap": overlap, "method": method}
    if topk:
        meta.update(topk={ch: {"n": v[0], "topk_w": v[1], "tail_w": v[2]} for ch, v in topk.items()},
                    topk_in=topk_in, topk_out=topk_out, topk_dropped=topk_dropped)
    return out, meta


def apply_boosts(hits: List[Hit], chunks: Dict[str, Any], doc_meta: Dict[str, Dict[str, Any]], *, time_scope: Optional[Dict[str, Any]] = None,
                 time_mode: str = "boost", time_w: float = 0.5, recency_half_life_days: int = 0, doc_type_boost: Optional[Dict[str, float]] = None,
                 router_doc_types: Optional[List[str]] = None, pinned: Optional[Dict[str, float]] = None, pin_w: float = 10.0,
                 provenance_chunks: Optional[Dict[str, str]] = None, provenance_w: float = 0.2,
                 feedback: Optional[Dict[str, float]] = None, feedback_w: float = 0.15,
                 exclude_terms: Optional[List[str]] = None, exclude_penalty: float = 0.5) -> Dict[str, Any]:
    """Hit.fused 에 곱셈 배율을 적용하고 Hit.boosts 에 기록. 반환: 적용 통계."""
    stats: Dict[str, int] = defaultdict(int)
    now = time.time()
    dtb = doc_type_boost or {}
    router_types = set(router_doc_types or [])
    ex = [e.lower() for e in (exclude_terms or []) if e]
    filtered: List[Hit] = []
    for h in hits:
        c = chunks.get(h.chunk_id)
        doc_id = c["doc_id"] if c else h.chunk_id.rsplit("#", 1)[0]
        dm = doc_meta.get(doc_id) or {}
        mult = 1.0
        boosts: Dict[str, float] = {}
        # exclude
        if ex and c:
            text = (c["heading"] + " " + c["text"]).lower()
            if any(e in text for e in ex):
                if exclude_penalty <= 0:
                    stats["excluded_removed"] += 1
                    continue
                boosts["exclude"] = exclude_penalty
                mult *= exclude_penalty
                stats["excluded_penalized"] += 1
        # doc_type
        dt = dm.get("doc_type") or ""
        if dt and dt in dtb and dtb[dt] != 1.0:
            boosts["doc_type"] = dtb[dt]
            mult *= dtb[dt]
        if dt and dt in router_types:
            boosts["router_type"] = 1.2
            mult *= 1.2
        # time scope
        ts = float(dm.get("ts") or 0)
        if time_scope:
            inside = bool(ts) and time_scope["from_ts"] <= ts <= time_scope["to_ts"]
            if inside:
                boosts["time"] = 1.0 + time_w
                mult *= 1.0 + time_w
            elif time_mode == "filter" and ts:
                stats["time_filtered"] += 1
                continue
        # recency
        if recency_half_life_days and ts:
            age_days = max(0.0, (now - ts) / 86400.0)
            r = 0.5 ** (age_days / float(recency_half_life_days))
            boosts["recency"] = round(1.0 + 0.3 * r, 3)
            mult *= 1.0 + 0.3 * r
        # pins
        if pinned:
            pw = pinned.get(h.chunk_id) or pinned.get(doc_id)
            if pw:
                boosts["pin"] = pin_w * pw
                mult *= pin_w * pw
                h.why.append("pin")
        # provenance
        if provenance_chunks and h.chunk_id in provenance_chunks and provenance_chunks[h.chunk_id] in ("explicit", "rule", "human"):
            boosts["provenance"] = 1.0 + provenance_w
            mult *= 1.0 + provenance_w
        # feedback
        if feedback and h.chunk_id in feedback:
            fb = max(-1.0, min(1.0, feedback[h.chunk_id]))
            boosts["feedback"] = round(1.0 + feedback_w * fb, 3)
            mult *= 1.0 + feedback_w * fb
        if boosts:
            h.boosts.update(boosts)     # 덮지 않고 합친다 — fuse() 의 topk_<채널> 배율이 남아 있어야 근거 표에서 보인다
            h.fused *= mult
            for k_ in boosts:
                stats[k_] += 1
        filtered.append(h)
    filtered.sort(key=lambda h: -h.fused)
    hits[:] = filtered
    return dict(stats)


def parse_inject_map(s: Any) -> Dict[str, int]:
    """channel_inject 'fts:2,vector:2,graph:1' → {fts:2, vector:2, graph:1}. 모르는 채널·0 이하는 버린다."""
    out: Dict[str, int] = {}
    for ch, v in parse_weight_map(s, 0.0).items():
        n = int(v)
        if ch in BASE_CHANNELS and n > 0:
            out[ch] = n
    return out


def inject_channels(hits: List[Hit], lists: Dict[str, List[Tuple[str, float]]], inject: Dict[str, int], win: int) -> Dict[str, List[str]]:
    """채널별 주 리스트 상위 n개를 리랭크 후보 창(win) 안으로 올린다 — external_rag_inject 와 같은 방식(창 끝 요소 바로 위의 fused).
    주 리스트: fts/vector/graph/doc_vector 는 같은 이름, external 은 모든 ext_<src>. 옮긴 뒤 hits 를 다시 정렬한다. 반환 {채널: 옮긴 id}."""
    moved: Dict[str, List[str]] = {}
    if not inject or win <= 0 or win > len(hits):
        return moved
    pos = {h.chunk_id: i for i, h in enumerate(hits)}
    for ch, n in inject.items():
        names = [k for k in lists if k.startswith("ext_")] if ch == "external" else ([ch] if ch in lists else [])
        for name in names:
            for cid, _sc in lists[name][:n]:
                i = pos.get(cid)
                if i is None or i < win:
                    continue
                hits[i].fused = hits[win - 1].fused + 1e-6
                hits[i].why.append("inject:" + ch)
                moved.setdefault(ch, []).append(cid)
    if moved:
        hits.sort(key=lambda h: -h.fused)
    return moved


def compare_methods(pipe, questions: List[Dict[str, Any]], methods: Optional[List[str]] = None, k: int = 5) -> List[Dict[str, Any]]:
    """평가셋을 fusion 방식별로 실행해 지표 비교 (fusion compare)."""
    methods = methods or ["rrf", "weighted", "zscore", "dbsf", "rrf_boost"]
    rows: List[Dict[str, Any]] = []
    prev = _tuning.T.values.get("fusion_method")
    try:
        for m in methods:
            _tuning.T.set("fusion_method", m)
            pipe.reload_tuning(from_file=False)
            _tuning.T.set("fusion_method", m)
            r, _ = pipe.evaluate(k=k, questions=questions, log=False)
            rows.append(dict(r["summary"], method=m))
    finally:
        if prev is None:
            _tuning.T.reset("fusion_method")
        else:
            _tuning.T.values["fusion_method"] = prev
        pipe.reload_tuning(from_file=False)
    return rows
