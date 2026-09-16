# -*- coding: utf-8 -*-
"""그래프 빌드: 규칙/LLM 추출 결과 병합 → 저장 → degree/커뮤니티(label propagation) → 커뮤니티 요약(옵션)."""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from .graph_rules import RuleExtractor, entity_id_for
from .graph_llm import llm_extract, llm_summarize_community
from .providers import BaseLLM
from .profiler import Profiler
from .store import Store
from . import tuning as _tuning
from . import progress as _pg


def build_graph_for_chunks(store: Store, chunks: List[Any], doc_titles: Dict[str, str], prof: Profiler,
                           rule_ex: Optional[RuleExtractor], llm: Optional[BaseLLM], use_llm: bool,
                           effort: str = "low", budget: int = 0, min_chars: int = 0,
                           doc_meta: Optional[Dict[str, Dict[str, Any]]] = None, explicit: bool = True,
                           report=None, extract_tokens: int = 4000, summary_tokens: int = 800) -> Dict[str, Any]:
    """chunks: sqlite Row 목록. 해당 청크의 기존 그래프 산출물은 지우고 재추출.
    budget: 이번 빌드의 LLM 추출 호출 상한(0=무제한), min_chars: 이보다 짧은 청크는 LLM 추출 생략.
    doc_meta: doc_id → 정규화 메타 (doc_type/ext_id/related). explicit=True 면 front matter related.* 를 explicit 관계로 (문서당 첫 청크에서 1회).
    report: 진행 메시지 콜백(빌드 로그). LLM 추출은 청크당 LLM 1회라 가장 오래 걸리므로 시작 시 규모를, 이후 3초마다 i/n 을 보고한다.
    반환 stats 에 touched(이번에 갱신된 엔티티 id 목록) 포함 → 증분 위키/doc_refs 갱신에 사용."""
    report = report or (lambda m: None)
    stats: Dict[str, Any] = {"rule_entities": 0, "rule_relations": 0, "explicit_relations": 0, "id_relations": 0,
                             "llm_entities": 0, "llm_relations": 0, "llm_calls": 0,
                             "llm_input_tokens": 0, "llm_output_tokens": 0, "llm_failures": 0, "llm_skipped_short": 0,
                             "llm_skipped_budget": 0}
    chunk_ids = [c["chunk_id"] for c in chunks]
    touched: Set[str] = set(store.entities_for_chunks(chunk_ids))   # 이전 산출물에 연결돼 있던 엔티티도 갱신 대상
    store.clear_graph_for_chunks(chunk_ids)
    known: List[str] = [e["name"] for e in store.entities(200)]
    doc_meta = doc_meta or {}
    explicit_done: Set[str] = set()

    with prof.stage("rule_extract", chunks=len(chunks), enabled=bool(rule_ex)) as st:
        if rule_ex:
            slow: List[Any] = []
            type_counts: Dict[str, int] = defaultdict(int)
            rel_counts: Dict[str, int] = defaultdict(int)
            prov_counts: Dict[str, int] = defaultdict(int)
            for i, c in enumerate(chunks):
                if i % 25 == 0:
                    _pg.tick(i, len(chunks), c["chunk_id"])
                t0 = _now()
                title = doc_titles.get(c["doc_id"], c["doc_id"])
                dm = doc_meta.get(c["doc_id"])
                ents, counts, rels = rule_ex.extract_chunk(c["text"], c["heading"] or "", c["doc_id"], title, dm)
                doc_eid = next(iter(ents)) if ents else None
                if explicit and dm and doc_eid and c["doc_id"] not in explicit_done and (dm.get("related") or {}):
                    explicit_done.add(c["doc_id"])
                    e_ents, e_rels = rule_ex.explicit_relations(dm, doc_eid)
                    for eid, e in e_ents.items():
                        ents.setdefault(eid, e)
                        counts.setdefault(eid, 0)
                    rels.extend(e_rels)
                    stats["explicit_relations"] += len(e_rels)
                for eid, e in ents.items():
                    store.upsert_entity(eid, e.name, e.type, e.description, e.aliases, "rule", e.confidence)
                    touched.add(eid)
                    type_counts[e.type] += 1
                    if counts.get(eid, 0) > 0:
                        store.add_mention(eid, c["chunk_id"], c["doc_id"], counts[eid], "rule")
                for r in rels:
                    if r.src in ents and r.dst in ents or r.rel in ("owner", "attendee", "source", "responsible", "comments_on"):
                        store.add_relation(r.src, r.dst, r.rel, r.description, r.weight, "rule" if r.provenance != "explicit" else "rule+explicit",
                                           r.confidence, c["chunk_id"], provenance=r.provenance)
                        rel_counts[r.rel] += 1
                        prov_counts[r.provenance] += 1
                        if r.provenance == "rule" and r.rel not in ("owner", "deadline", "amount", "attendee", "source", "responsible", "comments_on", "decides"):
                            stats["id_relations"] += 1
                stats["rule_entities"] += len(ents)
                stats["rule_relations"] += len(rels)
                slow.append(((_now() - t0) * 1000, c["chunk_id"], len(ents), len(rels)))
            slow.sort(reverse=True)
            st.note(**{k: v for k, v in stats.items() if k.startswith(("rule", "explicit", "id_"))}, provenance=dict(prov_counts))
            st.debug(entity_types=dict(type_counts), relation_types=dict(rel_counts),
                     slowest_chunks=[{"chunk_id": cid, "ms": round(ms, 1), "entities": ne, "relations": nr} for ms, cid, ne, nr in slow[:5]])

    if use_llm and llm and llm.available:
        with prof.stage("llm_extract", chunks=len(chunks), model=getattr(llm, "model", llm.name), role=getattr(llm, "role", ""),
                        budget=budget or "unlimited", min_chars=min_chars) as st:
            eligible = sum(1 for c in chunks if not (min_chars and len(c["text"]) < min_chars))
            planned = min(eligible, budget) if budget else eligible
            report("llm_extract: %d chunks → LLM 호출 예정 %d회 (%s/%s, budget=%s, timeout %ss/call) — 청크당 LLM 1회라 가장 오래 걸리는 단계"
                   % (len(chunks), planned, llm.name, getattr(llm, "model", ""), budget or "unlimited", getattr(llm, "timeout", "?")))
            t_report = _now()
            for i, c in enumerate(chunks):
                _pg.tick(i, len(chunks), "%s (LLM %d/%d)" % (c["chunk_id"], stats["llm_calls"], planned))
                if _now() - t_report > 3:
                    t_report = _now()
                    report("llm_extract %d/%d chunks · LLM 호출 %d · 실패 %d" % (i, len(chunks), stats["llm_calls"], stats["llm_failures"]))
                if min_chars and len(c["text"]) < min_chars:
                    stats["llm_skipped_short"] += 1
                    continue
                if budget and stats["llm_calls"] >= budget:
                    stats["llm_skipped_budget"] += 1
                    continue
                data = llm_extract(llm, c["text"], c["heading"] or "", known, effort, _tuning.T.get("llm_known_entities"),
                                   max_tokens=extract_tokens)
                stats["llm_calls"] += 1
                if not data:
                    stats["llm_failures"] += 1
                    continue
                u = data.get("_usage", {})
                stats["llm_input_tokens"] += int(u.get("input_tokens", 0) or 0)
                stats["llm_output_tokens"] += int(u.get("output_tokens", 0) or 0)
                name_to_id: Dict[str, str] = {}
                for e in data.get("entities", []):
                    name = (e.get("name") or "").strip()
                    if not name:
                        continue
                    eid = _resolve(store, rule_ex, name)
                    name_to_id[name] = eid
                    touched.add(eid)
                    store.upsert_entity(eid, store.get_entity(eid)["name"] if store.get_entity(eid) else name,
                                        e.get("type") or "concept", e.get("description") or "", [name], "llm", 0.75)
                    store.add_mention(eid, c["chunk_id"], c["doc_id"], 1, "llm")
                    if name not in known:
                        known.append(name)
                    stats["llm_entities"] += 1
                for r in data.get("relations", []):
                    s, d = (r.get("src") or "").strip(), (r.get("dst") or "").strip()
                    if not s or not d:
                        continue
                    sid = name_to_id.get(s) or _resolve(store, rule_ex, s)
                    did = name_to_id.get(d) or _resolve(store, rule_ex, d)
                    for nm, eid in ((s, sid), (d, did)):
                        touched.add(eid)
                        if not store.get_entity(eid):
                            store.upsert_entity(eid, nm, "concept", "", [nm], "llm", 0.6)
                    store.add_relation(sid, did, r.get("rel") or "related_to", r.get("description") or "",
                                       float(r.get("weight") or 0.5), "llm", 0.7, c["chunk_id"], provenance="llm")
                    stats["llm_relations"] += 1
            _pg.tick(len(chunks), len(chunks), "")
            report("llm_extract done: LLM 호출 %d · 엔티티 %d · 관계 %d · 실패 %d · 생략(짧음 %d, budget %d)" % (
                stats["llm_calls"], stats["llm_entities"], stats["llm_relations"], stats["llm_failures"], stats["llm_skipped_short"], stats["llm_skipped_budget"]))
            st.note(**{k: v for k, v in stats.items() if k.startswith("llm")})
    elif use_llm:
        prof.skipped("llm_extract", "LLM provider unavailable (%s)" % (llm.name if llm else "none"))
    else:
        prof.skipped("llm_extract")
    store.commit()
    stats["touched_entities"] = len(touched)
    stats["touched"] = sorted(touched)
    return stats


def _now() -> float:
    import time
    return time.perf_counter()


def _resolve(store: Store, rule_ex: Optional[RuleExtractor], name: str) -> str:
    """LLM 이 낸 이름을 규칙 사전 별칭 → 기존 엔티티 FTS 순으로 기존 노드에 매핑."""
    if rule_ex:
        canon = rule_ex.alias_map.get(name.lower())
        if canon:
            return entity_id_for(canon)
    eid = entity_id_for(name)
    if store.get_entity(eid):
        return eid
    hits = store.entity_fts('"%s"' % name.replace('"', ""), 1)
    if hits and hits[0][1] > 3.0:
        return hits[0][0]
    return eid


def finalize_graph(store: Store, prof: Profiler, do_communities: bool, llm: Optional[BaseLLM], summarize: bool,
                   effort: str = "low", touched: Optional[List[str]] = None, skip_reason: str = "",
                   summary_tokens: int = 800) -> Dict[str, Any]:
    """degree 갱신 → 노드별 문서 참조(doc_refs) 비정규화 → (옵션) 커뮤니티 탐지/요약.
    touched 가 주어지면 doc_refs 는 해당 엔티티만 갱신(증분), None 이면 전체."""
    out: Dict[str, Any] = {}
    with prof.stage("degrees"):
        store.update_degrees()
    with prof.stage("doc_refs", scope="touched(%d)" % len(touched) if touched is not None else "all") as st:
        n = store.refresh_doc_refs(touched)
        st.note(entities_updated=n)
        out["doc_refs_updated"] = n
    if do_communities:
        with prof.stage("communities") as st:
            comms = label_propagation(store, iters=_tuning.T.get("community_iters"))
            out["communities"] = len(comms)
            st.note(communities=len(comms), largest=max([len(v) for v in comms.values()] or [0]))
            ents = {e["entity_id"]: e for e in store.entities()}
            rels = store.relations_all()
            rel_by_comm: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
            for r in rels:
                ca = ents.get(r["src"], {}).get("community")
                cb = ents.get(r["dst"], {}).get("community")
                if ca is not None and ca == cb:
                    rel_by_comm[ca].append(r)
            n_sum = 0
            for ci, (cid, members) in enumerate(comms.items()):
                _pg.tick(ci, len(comms), "community %s (%d nodes)" % (cid, len(members)))
                mem = sorted((ents[m] for m in members if m in ents), key=lambda e: -(e.get("degree") or 0))
                top = [m["name"] for m in mem[:8]]
                summary = "핵심 엔티티: " + ", ".join(top)
                src = "rule"
                if summarize and llm and llm.available and len(members) >= 3:
                    s = llm_summarize_community(llm, mem, sorted(rel_by_comm[cid], key=lambda r: -r["weight"]), effort,
                                                max_tokens=summary_tokens)   # 역할별 llm_roles.summary.max_tokens
                    if s:
                        summary, src = s, "llm"
                        n_sum += 1
                store.put_community(cid, len(members), top, summary, src)
            st.note(llm_summaries=n_sum)
    else:
        prof.skipped("communities", skip_reason or "disabled")
    store.commit()
    return out


def label_propagation(store: Store, iters: int = 20, seed: int = 7) -> Dict[int, Set[str]]:
    """가중 label propagation. 문서/날짜/금액 노드는 허브가 되어 커뮤니티를 뭉개므로 제외."""
    ents = store.entities()
    skip_types = {"document", "date", "amount"}
    nodes = [e["entity_id"] for e in ents if e["type"] not in skip_types]
    node_set = set(nodes)
    adj: Dict[str, Dict[str, float]] = defaultdict(dict)
    for r in store.relations_all():
        a, b = r["src"], r["dst"]
        if a in node_set and b in node_set and a != b:
            w = float(r["weight"] or 0.1)
            adj[a][b] = adj[a].get(b, 0) + w
            adj[b][a] = adj[b].get(a, 0) + w
    label = {n: i for i, n in enumerate(nodes)}
    rnd = random.Random(seed)
    for _ in range(iters):
        order = list(nodes)
        rnd.shuffle(order)
        changed = 0
        for n in order:
            if not adj[n]:
                continue
            score: Dict[int, float] = defaultdict(float)
            for m, w in adj[n].items():
                score[label[m]] += w
            best = max(score.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            if best != label[n]:
                label[n] = best
                changed += 1
        if changed == 0:
            break
    # 재번호
    remap: Dict[int, int] = {}
    comms: Dict[int, Set[str]] = defaultdict(set)
    for n in nodes:
        c = remap.setdefault(label[n], len(remap))
        comms[c].add(n)
        store.set_community(n, c)
    for e in ents:
        if e["type"] in skip_types:
            store.set_community(e["entity_id"], -1)
    return comms
