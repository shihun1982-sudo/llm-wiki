# -*- coding: utf-8 -*-
"""그래프 진단 프로파일 (요청 5, docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md §2.5).

"규칙(data/rules.json)을 바꾸면 그래프가 어떻게 달라지는가" 를 숫자로 보고, 어디를 고칠지 제안받는다.
  profile(pipe)   → {size, connectivity, coverage, quality, rules, usage, suggestions, eval?, generated_at, build_version}
  save(profile)   → <data_dir>/graph_profiles/gp_<ts>.json (graph_profile_keep 개 보관)
  history(s)      → 저장된 실행 목록(최신 우선)  ·  compare(a, b) → 핵심 지표 diff  ·  render_text / render_markdown
개선 루프: graph profile → rules.json 편집 → build graph → graph profile --compare.
세 창구가 같은 dict 를 쓴다: CLI `graph profile` · GET /api/graph/profile · MCP wiki_graph_profile.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from . import atomicio
from . import graph_rules as _gr
from . import graph_findings as _gf
from .graph_rules import entity_id_for
from .textutil import keywords

# 허브가 이 유형이면 경고 — 날짜/금액/문서 노드는 어디에나 붙어 커뮤니티를 뭉개고 그래프 검색을 흐린다 (label_propagation 도 이 유형을 뺀다)
HUB_WARN_TYPES = ("date", "amount", "percent", "document", "role")
# 약한 근거 유형 — 문서 커버리지 계산에서 "엔티티가 있다" 로 치지 않는다
WEAK_TYPES = ("date", "amount", "percent")
# 제안 임계값 (suggestions[].thresholds 로 결과에 그대로 실린다)
THRESHOLDS: Dict[str, float] = {"isolated_ratio": 0.30, "coverage_pct": 50.0, "coverage_min_docs": 3, "cooccur_share": 0.70,
                                "no_seed_share": 0.50, "keyword_min_count": 2, "hub_warn_min_degree": 5}
PROFILE_DIRNAME = "graph_profiles"


# ---------------------------------------------------------------- 도우미
def profiles_dir(s) -> str:
    return os.path.join(getattr(s, "data_dir", "data"), PROFILE_DIRNAME)


def _quant(vals: List[float], q: float) -> float:
    if not vals:
        return 0.0
    i = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
    return float(vals[i])


def _ratio(a: float, b: float) -> float:
    return round(a / b, 3) if b else 0.0


def _norm_name(s: str) -> str:
    return re.sub(r"[\s\-_·.,()/]+", "", str(s or "").lower())


def _uf_find(parent: Dict[str, str], x: str) -> str:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


# ---------------------------------------------------------------- 프로파일
def profile(pipe, *, include_eval: bool = False, requests_n: Optional[int] = None) -> Dict[str, Any]:
    s = pipe.s
    store = pipe.store
    try:
        pipe.sync_with_db()
    except Exception:
        pass
    hubs_n = max(1, int(getattr(s, "graph_profile_hubs", 10) or 10))
    req_n = int(requests_n if requests_n is not None else (getattr(s, "graph_profile_requests", 200) or 200))
    t0 = time.perf_counter()

    ents = store.entities(10_000_000)
    rels = store.relations_all()
    mentions = [dict(r) for r in store.conn.execute("SELECT entity_id, chunk_id, doc_id, count, source FROM mentions")]
    comms = store.communities_all()
    docs = store.list_docs()
    meta = store.doc_meta_map()
    st = store.stats()
    rules = _gr.load_rules()
    by_id: Dict[str, Dict[str, Any]] = {e["entity_id"]: e for e in ents}
    ids = set(by_id)

    # ---- 규모
    size = {
        "entities": len(ents), "relations": len(rels), "mentions": len(mentions), "communities": len(comms),
        "docs": len(docs), "chunks": int(st.get("chunks") or 0),
        "entities_by_type": dict(Counter(e["type"] or "?" for e in ents).most_common()),
        "entities_by_source": dict(Counter(e.get("source") or "?" for e in ents).most_common()),
        "relations_by_rel": dict(Counter(r["rel"] or "?" for r in rels).most_common()),
        "relations_by_provenance": dict(Counter((r.get("provenance") or "?") for r in rels).most_common()),
        "relations_by_source": dict(Counter(r.get("source") or "?" for r in rels).most_common()),
    }

    # ---- 연결성: union-find 성분 · 차수 · 고립 · 허브
    parent = {i: i for i in ids}
    deg: Counter = Counter()
    dangling: List[Dict[str, Any]] = []
    self_loops = 0
    for r in rels:
        a, b = r["src"], r["dst"]
        if a not in ids or b not in ids:
            if len(dangling) < 10:
                dangling.append({"rel_id": r.get("rel_id"), "src": a, "dst": b, "rel": r["rel"], "missing": "src" if a not in ids else "dst"})
            continue
        if a == b:
            self_loops += 1
            continue
        deg[a] += 1
        deg[b] += 1
        ra, rb = _uf_find(parent, a), _uf_find(parent, b)
        if ra != rb:
            parent[ra] = rb
    n_dangling = sum(1 for r in rels if r["src"] not in ids or r["dst"] not in ids)
    comp_sizes = Counter(_uf_find(parent, i) for i in ids)
    largest = max(comp_sizes.values()) if comp_sizes else 0
    isolated = [by_id[i] for i in ids if deg[i] == 0]
    isolated.sort(key=lambda e: (e["type"] or "", e["name"] or ""))
    degs = sorted(deg[i] for i in ids)
    hubs = []
    for eid, d in deg.most_common(hubs_n):
        e = by_id[eid]
        hubs.append({"entity_id": eid, "name": e["name"], "type": e["type"], "degree": d, "community": e.get("community"),
                     "warning": (e["type"] in HUB_WARN_TYPES) and d >= THRESHOLDS["hub_warn_min_degree"]})
    connectivity = {
        "components": len(comp_sizes), "largest_component": largest, "largest_component_ratio": _ratio(largest, len(ids)),
        "isolated": len(isolated), "isolated_ratio": _ratio(len(isolated), len(ids)),
        "isolated_samples": [{"entity_id": e["entity_id"], "name": e["name"], "type": e["type"], "source": e.get("source")} for e in isolated[:12]],
        "isolated_by_type": dict(Counter(e["type"] or "?" for e in isolated).most_common(8)),
        "degree": {"median": _quant(degs, 0.5), "p90": _quant(degs, 0.9), "max": float(degs[-1]) if degs else 0.0,
                   "mean": round(sum(degs) / len(degs), 2) if degs else 0.0},
        "hubs": hubs, "hub_warnings": sum(1 for h in hubs if h["warning"]),
    }

    # ---- 문서 커버리지: 문서 노드 자신과 날짜/금액은 "엔티티가 있다" 로 치지 않는다
    doc_node: Dict[str, str] = {}
    for d in docs:
        m = meta.get(d["doc_id"]) or {}
        doc_node[d["doc_id"]] = entity_id_for(str(m.get("ext_id") or "").upper() or (d.get("title") or d["doc_id"]))
    covered_pairs: set = set()
    for m in mentions:
        e = by_id.get(m["entity_id"])
        if not e or e["type"] in WEAK_TYPES or m["entity_id"] == doc_node.get(m["doc_id"]):
            continue
        covered_pairs.add((m["doc_id"], m["entity_id"]))
    covered_docs = {d for d, _ in covered_pairs}
    per_type: Dict[str, Dict[str, Any]] = {}
    for d in docs:
        dt = str((meta.get(d["doc_id"]) or {}).get("doc_type") or "(none)")
        row = per_type.setdefault(dt, {"total": 0, "covered": 0, "uncovered_samples": []})
        row["total"] += 1
        if d["doc_id"] in covered_docs:
            row["covered"] += 1
        elif len(row["uncovered_samples"]) < 5:
            row["uncovered_samples"].append(d["doc_id"])
    for row in per_type.values():
        row["pct"] = round(100.0 * row["covered"] / row["total"], 1) if row["total"] else 0.0
    uncovered = [d["doc_id"] for d in docs if d["doc_id"] not in covered_docs]
    coverage = {
        "docs": len(docs), "covered": len(covered_docs), "uncovered": len(uncovered), "pct": round(100.0 * len(covered_docs) / len(docs), 1) if docs else 0.0,
        "uncovered_samples": uncovered[:12], "by_doc_type": dict(sorted(per_type.items(), key=lambda kv: kv[1]["pct"])),
        "entities_per_doc": round(len(covered_pairs) / len(docs), 2) if docs else 0.0,
        "mentions_per_chunk": _ratio(len(mentions), size["chunks"]),
    }

    # ---- 품질 신호
    groups: Dict[str, List[str]] = defaultdict(list)
    alias_of: Dict[str, List[str]] = defaultdict(list)
    for e in ents:
        groups[_norm_name(e["name"])].append(e["entity_id"])
        try:
            als = json.loads(e.get("aliases") or "[]")
        except Exception:
            als = []
        for a in als:
            na = _norm_name(a)
            if na and na != _norm_name(e["name"]):
                alias_of[na].append(e["entity_id"])
    dups: List[Dict[str, Any]] = []
    seen_pairs: set = set()
    for nn, lst in groups.items():
        if len(lst) > 1 and nn:
            key = tuple(sorted(lst))
            if key not in seen_pairs:
                seen_pairs.add(key)
                dups.append({"reason": "이름 동일(정규화)", "key": nn, "names": [by_id[i]["name"] for i in lst], "ids": lst,
                             "types": [by_id[i]["type"] for i in lst]})
    for na, lst in alias_of.items():
        owners = set(lst) | set(groups.get(na) or [])
        if len(owners) > 1:
            key = tuple(sorted(owners))
            if key not in seen_pairs:
                seen_pairs.add(key)
                dups.append({"reason": "별칭 겹침 '%s'" % na, "key": na, "names": [by_id[i]["name"] for i in key], "ids": list(key),
                             "types": [by_id[i]["type"] for i in key]})
    weights = sorted(float(r.get("weight") or 0.0) for r in rels)
    cooccur = sum(1 for r in rels if (r.get("provenance") or "") == "cooccur" or r["rel"] in ("co_occurs", "mentions", "mentions_date", "mentions_amount"))
    quality = {
        "duplicate_candidates": len(dups), "duplicates": dups[:30],
        "dangling_relations": n_dangling, "dangling_samples": dangling, "self_loops": self_loops,
        "cooccur_relations": cooccur, "cooccur_share": _ratio(cooccur, len(rels)),
        "weight": {"min": _quant(weights, 0.0), "p25": _quant(weights, 0.25), "median": _quant(weights, 0.5), "p75": _quant(weights, 0.75),
                   "max": _quant(weights, 1.0), "mean": round(sum(weights) / len(weights), 3) if weights else 0.0},
    }

    # ---- 규칙 기여: rules.json 의 항목마다 만든 엔티티/관계 수
    mention_cnt: Counter = Counter()
    for m in mentions:
        mention_cnt[m["entity_id"]] += int(m.get("count") or 0)
    type_cnt = Counter(e["type"] for e in ents)
    rows: List[Dict[str, Any]] = []
    for p in rules.get("id_patterns") or []:
        t = str(p.get("type") or "")
        n_rel = sum(1 for r in rels if (r.get("provenance") or "") == "rule" and (by_id.get(r["dst"]) or {}).get("type") == t)
        rows.append({"kind": "id_pattern", "name": t, "detail": str(p.get("regex") or ""), "entities": type_cnt.get(t, 0), "relations": n_rel})
    for lr in rules.get("link_rules") or []:
        w, tgt, rel = str(lr.get("when_doc_type", "*")), str(lr.get("target_type", "*")), str(lr.get("rel") or "")
        n_rel = sum(1 for r in rels if r["rel"] == rel and (r.get("provenance") or "") == "rule"
                    and (w == "*" or (by_id.get(r["src"]) or {}).get("type") == w) and (tgt == "*" or (by_id.get(r["dst"]) or {}).get("type") == tgt))
        rows.append({"kind": "link_rule", "name": "%s → %s [%s]" % (w, tgt, rel), "detail": "", "entities": 0, "relations": n_rel})
    for p in rules.get("relation_patterns") or []:
        rel_names = {str((p.get(k) or {}).get("rel") or "") for k in ("in_decision", "in_chunk") if isinstance(p.get(k), dict)}
        if not rel_names:
            rel_names = {str(p.get("rel") or p.get("name") or "")}
        n_rel = sum(1 for r in rels if r["rel"] in rel_names and (r.get("provenance") or "") == "rule")
        rows.append({"kind": "relation_pattern", "name": str(p.get("name") or "?"), "detail": " / ".join(sorted(rel_names)), "entities": 0, "relations": n_rel})
    for key, rel in (("analyst_pattern", "comments_on"), ("decision_pattern", "decides")):
        if rules.get(key):
            rows.append({"kind": key, "name": rel, "detail": "", "entities": type_cnt.get("decision", 0) if rel == "decides" else 0,
                         "relations": sum(1 for r in rels if r["rel"] == rel)})
    for dt, keys in (rules.get("explicit_rels") or {}).items():
        for k, rel in (keys or {}).items():
            n_rel = sum(1 for r in rels if r["rel"] == rel and (r.get("provenance") or "") == "explicit"
                        and (dt == "*" or (by_id.get(r["src"]) or {}).get("type") == dt))
            rows.append({"kind": "explicit_rel", "name": "%s.%s [%s]" % (dt, k, rel), "detail": "front matter related.%s" % k, "entities": 0, "relations": n_rel})
    dict_rows: List[Dict[str, Any]] = []
    for canon, info in (rules.get("entities") or {}).items():
        if str(canon).startswith("_"):
            continue
        eid = entity_id_for(str(canon))
        e = by_id.get(eid)
        dict_rows.append({"name": canon, "type": str((info or {}).get("type") or ""), "present": bool(e), "mentions": mention_cnt.get(eid, 0),
                          "degree": deg.get(eid, 0)})
    dead_dict = [d["name"] for d in dict_rows if d["mentions"] == 0]
    dead_rules = [{"kind": r["kind"], "name": r["name"], "action": "data/rules.json %s" % _rule_section(r["kind"])}
                  for r in rows if r["entities"] == 0 and r["relations"] == 0]
    rules_sec = {
        "path": _gr.rules_path(), "rows": rows,
        "dictionary": {"total": len(dict_rows), "active": sum(1 for d in dict_rows if d["mentions"] > 0), "dead": len(dead_dict),
                       "dead_names": dead_dict[:100], "top": sorted(dict_rows, key=lambda d: -d["mentions"])[:15]},
        "dead_rules": dead_rules,
    }

    # ---- 질의 활용: 최근 N 건의 질의 요청
    ent_names: set = set()
    try:
        for e in store.entity_index():
            ent_names.update(e.get("names") or [])
    except Exception:
        pass
    n_req = with_seed = req_graph_hit = total_hits = graph_hits = 0
    kw_cnt: Counter = Counter()
    no_seed_samples: List[str] = []
    try:
        req_rows = store.conn.execute("SELECT id, result FROM requests WHERE kind='query' ORDER BY id DESC LIMIT ?", (max(0, req_n),)).fetchall()
    except Exception:
        req_rows = []
    for r in req_rows:
        try:
            res = json.loads(r["result"]) if r["result"] else None
        except Exception:
            res = None
        if not isinstance(res, dict):
            continue
        n_req += 1
        seeds = (res.get("graph") or {}).get("seeds") or (res.get("route") or {}).get("entities") or []
        hb = res.get("hits_brief") or []
        gh = sum(1 for h in hb if any(str(w).startswith("graph") for w in (h.get("why") or [])))
        total_hits += len(hb)
        graph_hits += gh
        if gh:
            req_graph_hit += 1
        if seeds:
            with_seed += 1
            continue
        q = str(res.get("query") or "")
        if len(no_seed_samples) < 8 and q:
            no_seed_samples.append(q)
        for kw in keywords(q):
            if kw.lower() not in ent_names:
                kw_cnt[kw] += 1
    usage = {
        "requests": n_req, "sample_limit": req_n, "with_seeds": with_seed, "seed_share": _ratio(with_seed, n_req),
        "requests_with_graph_hit": req_graph_hit, "requests_with_graph_hit_share": _ratio(req_graph_hit, n_req),
        "final_hits": total_hits, "graph_hits": graph_hits, "graph_hit_share": _ratio(graph_hits, total_hits),
        "no_seed_keywords": [{"keyword": k, "count": c} for k, c in kw_cnt.most_common(15)], "no_seed_samples": no_seed_samples,
    }

    out: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(), "build_version": store.build_version(),
        "dir": profiles_dir(s), "keep": int(getattr(s, "graph_profile_keep", 30) or 0),
        "size": size, "connectivity": connectivity, "coverage": coverage, "quality": quality, "rules": rules_sec, "usage": usage,
    }
    out["suggestions"] = suggest(out)
    out["thresholds"] = dict(THRESHOLDS)
    # 소견 (2026-09-24): 지표를 "무엇이 잘못됐고 무엇을 고칠지" 로 — 증거·원인·처방(붙여 넣을 조각)·확인. graph_findings.py
    try:
        out["findings"] = _gf.compute(pipe, out, rules)
    except Exception as e:  # 관측은 본체를 막지 않는다
        out["findings"] = {"findings": [], "errors": [{"step": "compute", "error": "%s: %s" % (type(e).__name__, str(e)[:200])}],
                           "thresholds": dict(_gf.THRESHOLDS), "by_area": {}, "by_severity": {}}

    # ---- (옵션) 그래프 채널만 켠 평가 — eval --matrix 의 graph 조합과 같다
    if include_eval:
        t = s.toggles
        saved = (t.fts, t.vector, t.graph)
        try:
            t.fts, t.vector, t.graph = False, False, True
            r, _ = pipe.evaluate(k=5, log=False)
            out["eval"] = dict(r["summary"], channel="graph", k=5, request_id=r.get("request_id"))
        except Exception as e:
            out["eval"] = {"error": str(e)[:200], "channel": "graph"}
        finally:
            t.fts, t.vector, t.graph = saved
    out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def _rule_section(kind: str) -> str:
    return {"id_pattern": "id_patterns", "link_rule": "link_rules", "relation_pattern": "relation_patterns", "explicit_rel": "explicit_rels",
            "analyst_pattern": "analyst_pattern", "decision_pattern": "decision_pattern"}.get(kind, kind)


# ---------------------------------------------------------------- 제안 (규칙 기반, §2.5 표)
def suggest(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    """각 항목: kind · severity(info|warn|error) · detail · action(어느 파일·키) · target(rules|tuning|build|query_rules)."""
    T = THRESHOLDS
    out: List[Dict[str, Any]] = []
    c, cov, q, ru, us = p["connectivity"], p["coverage"], p["quality"], p["rules"], p["usage"]
    if p["size"]["entities"] == 0:
        out.append({"kind": "empty", "severity": "error", "detail": "그래프에 엔티티가 없습니다 — 그래프 빌드가 안 됐거나 규칙이 아무것도 잡지 못했습니다.",
                    "action": "python -m llmwiki build graph (토글 rule_graph 확인) → data/rules.json entities/id_patterns", "target": "build"})
        return out
    if q["dangling_relations"]:
        out.append({"kind": "dangling", "severity": "error", "detail": "끝점이 없는 관계 %d개 — 부분 빌드/삭제 뒤 남은 찌꺼기." % q["dangling_relations"],
                    "action": "python -m llmwiki build graph (전체 그래프 재생성)", "target": "build"})
    if c["isolated_ratio"] >= T["isolated_ratio"]:
        top = ", ".join("%s(%d)" % kv for kv in list(c["isolated_by_type"].items())[:3])
        out.append({"kind": "isolated", "severity": "warn", "detail": "고립 엔티티 비율 %.0f%% (%d/%d) — 유형별: %s. 이름만 잡히고 관계가 없다."
                    % (c["isolated_ratio"] * 100, c["isolated"], p["size"]["entities"], top),
                    "action": "data/rules.json id_patterns(문서 ID 패턴) · link_rules(문서 유형↔대상 유형 관계) · relation_patterns 추가; 사전 엔티티면 types_for_cooccur 에 유형 포함",
                    "target": "rules"})
    for h in c["hubs"]:
        if h["warning"]:
            key = {"date": "dates_per_chunk", "amount": "amounts_per_chunk"}.get(h["type"])
            out.append({"kind": "hub_" + h["type"], "severity": "warn", "detail": "허브 '%s' (%s, degree %d) — %s 노드가 허브면 그래프 검색이 모든 문서로 번진다."
                        % (h["name"], h["type"], h["degree"], h["type"]),
                        "action": ("tuning.json %s 하향 (재빌드 필요)" % key) if key else "data/rules.json types_for_cooccur 에서 '%s' 제외 또는 relation_patterns 정리" % h["type"],
                        "target": "tuning" if key else "rules"})
    for dt, row in cov["by_doc_type"].items():
        if row["total"] >= T["coverage_min_docs"] and row["pct"] < T["coverage_pct"]:
            out.append({"kind": "coverage", "severity": "warn", "detail": "문서 유형 '%s' 커버리지 %.0f%% (%d/%d) — 예: %s" % (dt, row["pct"], row["covered"], row["total"], ", ".join(row["uncovered_samples"][:3])),
                        "action": "data/rules.json entities 에 그 유형 문서의 용어(제품·모듈·팀) 추가, 또는 그 유형의 ID 를 잡는 id_patterns", "target": "rules"})
    if q["cooccur_share"] >= T["cooccur_share"] and p["size"]["relations"] >= 20:
        out.append({"kind": "cooccur", "severity": "info", "detail": "관계의 %.0f%% 가 공동출현(cooccur) — 결정적 관계(rule/explicit)가 적어 그래프 검색의 정밀도가 낮다." % (q["cooccur_share"] * 100),
                    "action": "data/rules.json link_rules · relation_patterns(담당/마감/모듈 같은 필드) 추가, 문서 front matter related.* 채우기", "target": "rules"})
    if q["duplicate_candidates"]:
        ex = q["duplicates"][0]
        out.append({"kind": "duplicate", "severity": "warn", "detail": "합치기 후보 %d쌍 — 예: %s (%s)" % (q["duplicate_candidates"], " / ".join(ex["names"][:3]), ex["reason"]),
                    "action": "data/rules.json entities.<대표어>.aliases 에 다른 표기를 넣어 한 노드로; 질의 쪽은 query_rules.json alias", "target": "rules"})
    if ru["dictionary"]["dead"]:
        out.append({"kind": "dead_dictionary", "severity": "info", "detail": "사전 엔티티 %d/%d 개가 코퍼스에 한 번도 안 나온다 — 예: %s" % (ru["dictionary"]["dead"], ru["dictionary"]["total"], ", ".join(ru["dictionary"]["dead_names"][:5])),
                    "action": "data/rules.json entities 에서 지우거나 aliases 에 실제 표기를 추가", "target": "rules"})
    for d in ru["dead_rules"][:8]:
        out.append({"kind": "dead_rule", "severity": "info", "detail": "%s '%s' 가 만든 엔티티/관계 0 — 정규식이 코퍼스 표기와 다르거나 대상 문서 유형이 없다." % (d["kind"], d["name"]),
                    "action": d["action"], "target": "rules"})
    if us["requests"] >= 5 and (1 - us["seed_share"]) >= T["no_seed_share"]:
        kws = ", ".join(k["keyword"] for k in us["no_seed_keywords"][:6] if k["count"] >= T["keyword_min_count"])
        out.append({"kind": "no_seed_queries", "severity": "warn", "detail": "최근 질의 %d건 중 %.0f%% 가 그래프 시드 없이 실행 — 엔티티가 아닌 상위 키워드: %s"
                    % (us["requests"], (1 - us["seed_share"]) * 100, kws or "-"),
                    "action": "data/rules.json entities 에 그 키워드를 엔티티(또는 별칭)로 추가 → build graph; 질의 쪽 표기 차이면 query_rules.json alias/acronym", "target": "rules"})
    if us["final_hits"] >= 20 and us["graph_hit_share"] < 0.05 and us["seed_share"] > 0.5:
        out.append({"kind": "graph_channel_weak", "severity": "info", "detail": "시드는 있는데 최종 근거의 %.1f%% 만 graph 채널 — 그래프가 융합에서 밀린다." % (us["graph_hit_share"] * 100),
                    "action": "tuning.json channel_w_graph · graph_hops(config.json) · top_k_graph 조정", "target": "tuning"})
    return out


# ---------------------------------------------------------------- 저장 · 이력 · 비교
def save(prof: Dict[str, Any]) -> str:
    d = prof.get("dir") or os.path.join("data", PROFILE_DIRNAME)
    os.makedirs(d, exist_ok=True)
    # 이름 = 시각(초) + 마이크로초 — 같은 초에 여러 번 저장해도 파일명 정렬이 곧 시간 순이 되게 (보관 개수 정리가 이름 정렬에 의존한다)
    ts = float(prof.get("ts") or time.time())
    base = time.strftime("gp_%Y%m%d_%H%M%S", time.localtime(ts))
    us = int((ts - int(ts)) * 1_000_000)
    path = os.path.join(d, "%s_%06d.json" % (base, us))
    while os.path.exists(path):
        us += 1
        path = os.path.join(d, "%s_%06d.json" % (base, us))
    atomicio.write_json(path, prof)
    prune(d, int(prof.get("keep") or 0))
    return path


def prune(d: str, keep: int) -> List[str]:
    if keep <= 0 or not os.path.isdir(d):
        return []
    files = sorted(f for f in os.listdir(d) if f.startswith("gp_") and f.endswith(".json"))
    gone = []
    for f in files[:-keep] if len(files) > keep else []:
        try:
            os.remove(os.path.join(d, f))
            gone.append(f)
        except OSError:
            pass
    return gone


def load(path: str) -> Optional[Dict[str, Any]]:
    got = atomicio.read_json(path)
    return got if isinstance(got, dict) else None


def history(s_or_dir) -> List[Dict[str, Any]]:
    """저장된 실행 목록 (최신 우선). 각 행: path·file·generated_at·build_version·핵심 지표."""
    d = s_or_dir if isinstance(s_or_dir, str) else profiles_dir(s_or_dir)
    if not os.path.isdir(d):
        return []
    rows = []
    for f in sorted((f for f in os.listdir(d) if f.startswith("gp_") and f.endswith(".json")), reverse=True):
        p = load(os.path.join(d, f))
        if not p:
            continue
        rows.append(dict(key_metrics(p), path=os.path.join(d, f), file=f, generated_at=p.get("generated_at"), build_version=p.get("build_version")))
    return rows


KEY_METRICS: List[Tuple[str, str, str]] = [   # (키, 한글 이름, 경로 a.b.c)
    ("entities", "엔티티", "size.entities"), ("relations", "관계", "size.relations"), ("mentions", "멘션", "size.mentions"),
    ("communities", "커뮤니티", "size.communities"), ("components", "연결 성분", "connectivity.components"),
    ("largest_component_ratio", "최대 성분 비율", "connectivity.largest_component_ratio"),
    ("isolated", "고립 엔티티", "connectivity.isolated"), ("isolated_ratio", "고립 비율", "connectivity.isolated_ratio"),
    ("hub_max_degree", "허브 최대 차수", "connectivity.degree.max"), ("hub_warnings", "허브 경고", "connectivity.hub_warnings"),
    ("coverage_pct", "문서 커버리지 %", "coverage.pct"), ("entities_per_doc", "문서당 엔티티", "coverage.entities_per_doc"),
    ("cooccur_share", "cooccur 비중", "quality.cooccur_share"), ("duplicates", "중복 후보", "quality.duplicate_candidates"),
    ("findings_error", "소견 error", "findings.by_severity.error"), ("findings_warn", "소견 warn", "findings.by_severity.warn"),
    ("dangling", "끊긴 관계", "quality.dangling_relations"), ("dead_rules", "죽은 규칙", "rules.dead_rules"),
    ("dead_dictionary", "죽은 사전 항목", "rules.dictionary.dead"),
    ("seed_share", "시드 있는 질의 비율", "usage.seed_share"), ("graph_hit_share", "graph 근거 비율", "usage.graph_hit_share"),
    ("eval_hit", "graph 채널 hit@k", "eval.hit@k"), ("eval_mrr", "graph 채널 MRR", "eval.mrr"),
]


def _get(p: Dict[str, Any], path: str) -> Any:
    cur: Any = p
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return len(cur) if isinstance(cur, list) else cur


def key_metrics(p: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _get(p, path) for k, _n, path in KEY_METRICS}


def compare(a: Optional[Dict[str, Any]], b: Dict[str, Any]) -> Dict[str, Any]:
    """a(이전) → b(이번) 핵심 지표 diff. a 가 없으면 deltas 는 비고 before=None."""
    ka = key_metrics(a) if a else {}
    kb = key_metrics(b)
    deltas: Dict[str, Any] = {}
    for k, name, _path in KEY_METRICS:
        va, vb = ka.get(k), kb.get(k)
        if va is None and vb is None:
            continue
        delta = round(vb - va, 3) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None
        deltas[k] = {"name": name, "before": va, "after": vb, "delta": delta}
    return {"before": {"generated_at": (a or {}).get("generated_at"), "build_version": (a or {}).get("build_version")},
            "after": {"generated_at": b.get("generated_at"), "build_version": b.get("build_version")},
            "deltas": deltas, "changed": [k for k, d in deltas.items() if d["delta"]]}


# ---------------------------------------------------------------- 렌더
def _table(head: List[str], rows: List[List[Any]], md: bool) -> List[str]:
    rows = [[("" if c is None else str(c)) for c in r] for r in rows]
    if md:
        return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(r) + " |" for r in rows]
    if not rows:
        return ["  (없음)"]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(head)]
    fmt = "  " + "  ".join("%%-%ds" % min(w, 48) for w in widths)
    return [fmt % tuple(head)] + [fmt % tuple(c[:48] for c in r) for r in rows]


def _render(p: Dict[str, Any], md: bool) -> str:
    H = (lambda t: "## " + t) if md else (lambda t: "\n[" + t + "]")
    L: List[str] = []
    sz, c, cov, q, ru, us = p["size"], p["connectivity"], p["coverage"], p["quality"], p["rules"], p["usage"]
    L.append(("# " if md else "") + "그래프 진단 프로파일 — %s (build_version %s, %.0f ms)" % (p.get("generated_at"), p.get("build_version"), p.get("ms") or 0))
    L.append(H("규모"))
    L.append("엔티티 %d · 관계 %d · 멘션 %d · 커뮤니티 %d · 문서 %d · 청크 %d" % (sz["entities"], sz["relations"], sz["mentions"], sz["communities"], sz["docs"], sz["chunks"]))
    L += _table(["엔티티 유형", "수"], [[k, v] for k, v in list(sz["entities_by_type"].items())[:12]], md)
    L += [""] + _table(["관계 rel", "수"], [[k, v] for k, v in list(sz["relations_by_rel"].items())[:12]], md)
    L += [""] + _table(["출처(provenance)", "수"], [[k, v] for k, v in sz["relations_by_provenance"].items()], md)
    L.append(H("연결성"))
    L.append("연결 성분 %d · 최대 성분 %d (%.0f%%) · 고립 %d (%.0f%%) · 차수 중앙값 %.0f / p90 %.0f / 최대 %.0f"
             % (c["components"], c["largest_component"], c["largest_component_ratio"] * 100, c["isolated"], c["isolated_ratio"] * 100,
                c["degree"]["median"], c["degree"]["p90"], c["degree"]["max"]))
    L += _table(["허브", "유형", "차수", "경고"], [[h["name"], h["type"], h["degree"], "△ 허브 부적합 유형" if h["warning"] else ""] for h in c["hubs"]], md)
    if c["isolated_samples"]:
        L.append("고립 예시: " + ", ".join("%s(%s)" % (e["name"], e["type"]) for e in c["isolated_samples"][:8]))
    L.append(H("문서 커버리지"))
    L.append("엔티티(문서 노드·날짜·금액 제외)가 있는 문서 %d/%d (%.0f%%) · 문서당 엔티티 %.1f · 청크당 멘션 %.2f"
             % (cov["covered"], cov["docs"], cov["pct"], cov["entities_per_doc"], cov["mentions_per_chunk"]))
    L += _table(["문서 유형", "문서", "커버", "%", "미커버 예시"], [[dt, r["total"], r["covered"], r["pct"], ", ".join(r["uncovered_samples"][:2])] for dt, r in cov["by_doc_type"].items()], md)
    L.append(H("품질 신호"))
    L.append("합치기 후보 %d · 끊긴 관계 %d · 자기 관계 %d · cooccur 비중 %.0f%% · weight 중앙값 %.2f (p25 %.2f · p75 %.2f)"
             % (q["duplicate_candidates"], q["dangling_relations"], q["self_loops"], q["cooccur_share"] * 100, q["weight"]["median"], q["weight"]["p25"], q["weight"]["p75"]))
    if q["duplicates"]:
        L += _table(["중복 후보", "이유", "유형"], [[" / ".join(d["names"][:3]), d["reason"], ",".join(d["types"][:3])] for d in q["duplicates"][:10]], md)
    L.append(H("규칙 기여 (%s)" % ru["path"]))
    L += _table(["종류", "규칙", "엔티티", "관계"], [[r["kind"], r["name"], r["entities"], r["relations"]] for r in ru["rows"]], md)
    L.append("사전 엔티티 %d · 코퍼스에 나온 것 %d · 죽은 것 %d%s" % (ru["dictionary"]["total"], ru["dictionary"]["active"], ru["dictionary"]["dead"],
                                                     (" (예: " + ", ".join(ru["dictionary"]["dead_names"][:6]) + ")") if ru["dictionary"]["dead_names"] else ""))
    if ru["dead_rules"]:
        L.append("죽은 규칙: " + ", ".join("%s '%s'" % (d["kind"], d["name"]) for d in ru["dead_rules"][:10]))
    L.append(H("질의 활용 (최근 %d건)" % us["requests"]))
    L.append("그래프 시드가 있던 질의 %d (%.0f%%) · graph 근거가 있던 질의 %d (%.0f%%) · 최종 근거 중 graph 채널 %.1f%%"
             % (us["with_seeds"], us["seed_share"] * 100, us["requests_with_graph_hit"], us["requests_with_graph_hit_share"] * 100, us["graph_hit_share"] * 100))
    if us["no_seed_keywords"]:
        L.append("시드 없던 질의의 키워드(엔티티 후보): " + ", ".join("%s(%d)" % (k["keyword"], k["count"]) for k in us["no_seed_keywords"][:10]))
    if p.get("eval"):
        ev = p["eval"]
        L.append(H("평가 (graph 채널만)"))
        L.append(("오류: " + ev["error"]) if ev.get("error") else "hit@%s %.3f · MRR %.3f · term_recall %.3f · n=%s" % (ev.get("k"), ev.get("hit@k", 0), ev.get("mrr", 0), ev.get("term_recall", 0), ev.get("n")))
    L.append(H("제안"))
    if not p["suggestions"]:
        L.append("  (없음 — 임계값 안)")
    for sg in p["suggestions"]:
        L.append(("- " if md else "  ") + "[%s] %s %s\n%s→ %s" % (sg["severity"], sg["kind"], sg["detail"], "  " if md else "      ", sg["action"]))
    fr = p.get("findings") or {}
    L.append(H("소견 %d건 — 무엇이 잘못됐고 무엇을 고칠지 (영역: %s)" % (len(fr.get("findings") or []),
                                                          ", ".join("%s %d" % kv for kv in (fr.get("by_area") or {}).items() if kv[1]) or "-")))
    L += _gf.render(fr, md)
    if p.get("compare"):
        cp = p["compare"]
        L.append(H("직전 실행과 비교 (%s → %s)" % (cp["before"].get("generated_at"), cp["after"].get("generated_at"))))
        L += _table(["지표", "이전", "이번", "Δ"], [[d["name"], d["before"], d["after"], ("%+g" % d["delta"]) if d["delta"] is not None else ""]
                                                for d in cp["deltas"].values()], md)
    if p.get("saved"):
        L.append(("\n" if not md else "") + "저장: %s" % p["saved"])
    return "\n".join(L)


def render_text(p: Dict[str, Any]) -> str:
    return _render(p, md=False)


def render_markdown(p: Dict[str, Any]) -> str:
    return _render(p, md=True)
