# -*- coding: utf-8 -*-
"""그래프 진단 **소견(findings)** — "무엇이 잘못됐고 무엇을 어떻게 고쳐야 하는지" (2026-09-24).

`graph_profile.profile()` 이 만든 지표(숫자)를 받아, 각 소견을 **증거 → 원인 → 처방 → 확인** 한 묶음으로 낸다.
처방은 문장이 아니라 **붙여 넣을 수 있는 것**이다: rules.json 조각(`rules_patch`) · 고칠 파일 목록(`corpus_edit`) ·
튜닝 키(`tuning_set`) · 실행할 명령(`build_cmd`) · 질의 규칙 조각(`query_rules_patch`).

왜 따로 있나
  기존 `suggestions` 는 임계값 하나에 한 줄 문장이라 (1) 원인을 말하지 않고 (2) 실데이터의 큰 문제 몇 가지를 아예
  보지 못했다 — 짧은 별칭이 단어 안에서 매칭돼 만든 가짜 허브(IR본부 degree 7,748), front matter `related.*` 키가
  규칙에 없어 생긴 가짜 타입(obsolete/update), 쓰레기 제목이 만든 중복 후보, 죽은 규칙의 획일적 설명, 커뮤니티 0.
  세 창구(CLI `graph profile` · Web 그래프 진단 · MCP wiki_graph_profile)는 같은 dict 를 받는다.

소견 하나의 모양 (모든 소견이 같은 키를 가진다)
  {id, severity(error|warn|info), area(rules|corpus|tuning|build|query_rules), title, why,
   evidence: {numbers: {…}, samples: […]},
   fix: {kind, section?, snippet?, files?, steps: […], commands: […]},
   verify: {metric, expect, command}}
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from . import graph_rules as _gr
from .graph_rules import entity_id_for

#: 소견 종류 — 안정된 이름 (화면 필터·테스트·문서가 쓴다)
FINDING_IDS = ("alias_false_positive", "related_key_unmapped", "doc_type_without_rules", "dead_rule_no_docs", "dead_rule_no_match",
               "junk_titles", "no_communities", "structure_share_low", "uncovered_docs", "isolated_by_type", "no_seed_queries",
               "hub_type_policy_mismatch", "id_missing")
AREAS = ("rules", "corpus", "tuning", "build", "query_rules")
COOCCUR_RELS = ("co_occurs", "mentions", "mentions_date", "mentions_amount")
#: 임계값 — 결과에 그대로 실린다 (사람이 왜 이 소견이 났는지 볼 수 있게)
THRESHOLDS: Dict[str, float] = {
    "alias_embedded_share": 0.50, "alias_min_occurrences": 10, "alias_sample_chunks": 80,
    "related_min_docs": 1, "doc_type_min_docs": 3, "structure_share": 0.15, "structure_min_relations": 100,
    "uncovered_pct": 50.0, "isolated_ratio": 0.30, "no_seed_share": 0.50, "keyword_min_count": 2,
    "junk_title_min_len": 3, "id_missing_share": 0.30,
}
_JUNK_TITLE = re.compile(r"^(#!|-\*-|<\?|import |from |\{|\[|/\*|//|\s*$)")
_FIELD_LINE = re.compile(r"^\s*[-*]?\s*([가-힣A-Za-z_][가-힣A-Za-z0-9_ ./]{1,24}?)\s*[:：]\s*\S", re.M)


def _ratio(a: float, b: float) -> float:
    return round(a / b, 3) if b else 0.0


def _finding(fid: str, severity: str, area: str, title: str, why: str, numbers: Dict[str, Any], samples: List[Any],
             fix: Dict[str, Any], verify: Dict[str, Any]) -> Dict[str, Any]:
    fix = dict(fix)
    fix.setdefault("steps", [])
    fix.setdefault("commands", [])
    return {"id": fid, "severity": severity, "area": area, "title": title, "why": why,
            "evidence": {"numbers": numbers, "samples": samples}, "fix": fix, "verify": verify}


# ---------------------------------------------------------------- 개별 소견
def _alias_false_positive(store, prof: Dict[str, Any], rules: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """허브·상위 mention 엔티티의 실제 매칭 문맥을 뽑아, 단어 **안**에서 잡힌 비율과 대소문자 불일치 비율을 잰다."""
    T = THRESHOLDS
    cands: List[str] = [h["entity_id"] for h in prof["connectivity"]["hubs"]]
    for d in (prof["rules"]["dictionary"].get("top") or [])[:10]:
        eid = entity_id_for(str(d.get("name") or ""))
        if eid in by_id and eid not in cands:
            cands.append(eid)
    mo = _gr.matching_options(rules)
    try:
        ex = _gr.RuleExtractor(rules)          # **지금 규칙**의 매처 — 실제로 무엇이 잡히는지는 이것이 정한다
    except Exception:
        ex = None
    bad_entities: List[Dict[str, Any]] = []      # 지금 규칙으로도 단어 안에서 잡힌다 → 규칙을 고쳐야 한다
    stale_entities: List[Dict[str, Any]] = []    # 지금 규칙은 안 잡는데 그래프에는 남아 있다 → 예전 매처로 빌드된 그래프, 재빌드
    for eid in cands[:20]:
        e = by_id.get(eid)
        if not e:
            continue
        try:
            all_names = [e["name"]] + list(json.loads(e.get("aliases") or "[]"))
        except Exception:
            all_names = [e["name"]]
        all_names_low = sorted({str(a).lower() for a in all_names if a}, key=len, reverse=True)
        aliases = [str(a) for a in all_names if a and _gr._is_ascii_word(str(a))]     # 경계 규칙이 뜻을 갖는 별칭만 잰다
        if not aliases:
            continue
        rows = store.conn.execute("SELECT chunk_id, count FROM mentions WHERE entity_id=? LIMIT ?", (eid, int(T["alias_sample_chunks"]))).fetchall()
        stored = sum(int(r["count"] or 0) for r in rows)
        chunks = store.get_chunks([r["chunk_id"] for r in rows])
        # (1) 예전 방식(경계 없음·대소문자 무시)으로 별칭 문자열이 등장하는 자리 — 그래프가 어떻게 만들어졌는지 재구성
        legacy_total = legacy_embedded = case_mismatch = 0
        samples: List[str] = []
        per_alias: Counter = Counter()
        # (2) 지금 매처가 실제로 잡는 자리
        now_total = now_embedded = 0
        for ch in chunks.values():
            text = ch["text"] or ""
            low = text.lower()
            for a in aliases:
                al = a.lower()
                for m in re.finditer(re.escape(al), low):
                    st, en = m.start(), m.end()
                    legacy_total += 1
                    inside = (st > 0 and text[st - 1].isascii() and text[st - 1].isalnum()) or (en < len(text) and text[en].isascii() and text[en].isalnum())
                    if inside:
                        legacy_embedded += 1
                        per_alias[a] += 1
                        if len(samples) < 6:
                            samples.append(text[max(0, st - 8):st].replace("\n", " ") + "[" + text[st:en] + "]" + text[en:en + 8].replace("\n", " "))
                    elif text[st:en] != a:
                        case_mismatch += 1
            if ex is not None:
                for canon, st in ex.find_entities(text):
                    if canon != e["name"]:
                        continue
                    now_total += 1
                    al = next((x for x in all_names_low if low.startswith(x, st)), None)
                    if al is None or not _gr._is_ascii_word(al):
                        continue                    # 한글 이름·띄어쓴 이름(RX DMA)은 경계 규칙 대상이 아니다
                    en = st + len(al)
                    if (st > 0 and text[st - 1].isascii() and text[st - 1].isalnum()) or (en < len(text) and text[en].isascii() and text[en].isalnum()):
                        now_embedded += 1
        row = {"entity_id": eid, "name": e["name"], "type": e["type"], "degree": e.get("degree"),
               "occurrences": legacy_total, "embedded": legacy_embedded, "embedded_share": _ratio(legacy_embedded, legacy_total),
               "case_mismatch": case_mismatch, "aliases": [a for a, _ in per_alias.most_common(4)], "samples": samples,
               "matches_now": now_total, "embedded_now": now_embedded, "embedded_now_share": _ratio(now_embedded, now_total),
               "stored_mentions": stored}
        if now_total >= T["alias_min_occurrences"] and _ratio(now_embedded, now_total) >= T["alias_embedded_share"]:
            bad_entities.append(row)
        elif (legacy_total >= T["alias_min_occurrences"] and _ratio(legacy_embedded, legacy_total) >= T["alias_embedded_share"]
              and stored >= 2 * max(1, now_total)):
            stale_entities.append(row)
    out: List[Dict[str, Any]] = []
    if stale_entities:
        names = ", ".join("%s(그래프 멘션 %d · 지금 매처 %d)" % (b["name"], b["stored_mentions"], b["matches_now"]) for b in stale_entities[:5])
        out.append(_finding(
            "alias_false_positive", "error", "build",
            "그래프가 예전 매처로 빌드됐다 — 짧은 별칭의 단어 내부 오탐이 그대로 남아 있다",
            "지금 규칙(matching 절)은 이 별칭들을 단어 안에서 잡지 않지만, 그래프에는 예전 매처가 만든 멘션·관계가 남아 있다. "
            "재빌드해야 허브가 내려가고 그래프 검색이 이 엔티티로 번지지 않는다.",
            {"entities": len(stale_entities), "detail": names, "matching_enabled": bool(mo["ascii_word_boundary"])},
            stale_entities,
            {"kind": "build_cmd", "steps": ["`build graph` 로 그래프 채널을 다시 만든다 (FTS·임베딩은 그대로)", "`graph profile --compare` 로 허브 degree 가 내려갔는지 본다"],
             "commands": ["python -m llmwiki build graph --yes", "python -m llmwiki graph profile --compare"]},
            {"metric": "connectivity.hubs[%s].degree" % stale_entities[0]["name"], "expect": "재빌드 뒤 크게 감소 (실측: IR본부 7,748 → 허브 밖)", "command": "graph profile --compare"}))
    if not bad_entities:
        return out
    snippet = {"matching": {"ascii_word_boundary": True, "case_sensitive_max_len": int(mo["case_sensitive_max_len"] or 3)},
               "entities": {b["name"]: {"match": {"whole_word": True, "case_sensitive": True}} for b in bad_entities}}
    names = ", ".join("%s(%s, degree %s, 지금 매처로도 단어 내부 %.0f%%)" % (b["name"], b["type"], b["degree"], b["embedded_now_share"] * 100) for b in bad_entities[:5])
    matching_on = bool(mo["ascii_word_boundary"])
    out.append(_finding(
        "alias_false_positive", "error", "rules",
        "짧은 별칭이 단어 안에서 매칭돼 가짜 허브를 만든다",
        "사전 별칭을 단어 경계 없이 찾으면 'first' 의 IR, 'director' 의 CTO 처럼 영단어 안의 글자가 엔티티로 잡힌다. "
        "그 엔티티는 모든 문서에 붙어 허브가 되고, 그래프 검색이 어디로든 번져 정밀도가 떨어진다. "
        + ("rules.json 의 matching 절이 켜져 있으므로 이 그래프는 예전 매처로 빌드된 것이다 — 다시 빌드하면 사라진다." if matching_on
           else "rules.json 의 matching.ascii_word_boundary 가 꺼져 있다."),
        {"entities": len(bad_entities), "worst_embedded_share": max(b["embedded_now_share"] for b in bad_entities),
         "matching_enabled": matching_on, "detail": names},
        bad_entities,
        {"kind": "rules_patch", "section": "matching · entities.<name>.match", "snippet": snippet,
         "steps": ["rules.json 에 matching 절이 있는지 확인한다 (없으면 `graph-rules fill-defaults` 가 기본값을 넣는다)",
                   "위 엔티티처럼 짧은 ASCII 별칭은 whole_word · case_sensitive 를 켠다 (조각 참고)",
                   "그래프를 다시 빌드하고 허브 degree 가 내려갔는지 비교한다"],
         "commands": ["python -m llmwiki graph-rules fill-defaults", "python -m llmwiki build graph", "python -m llmwiki graph profile --compare"]},
        {"metric": "connectivity.hubs[%s].degree" % bad_entities[0]["name"], "expect": "재빌드 뒤 크게 감소 (예: 7,748 → 수십)", "command": "graph profile --compare"}))
    return out


def _related_key_unmapped(store, rules: Dict[str, Any], meta_rows: List[Dict[str, Any]], by_id: Dict[str, Dict[str, Any]],
                          known_types: List[str]) -> List[Dict[str, Any]]:
    """front matter `related.<key>` 중 규칙(related_key_type · explicit_rels)에 없는 키 — 관계 이름이 references 로 뭉개지고 타입이 지어진다."""
    rkt = rules.get("related_key_type") or {}
    er = rules.get("explicit_rels") or {}
    used: Dict[Tuple[str, str], int] = Counter()
    targets: Dict[str, List[str]] = defaultdict(list)
    for m in meta_rows:
        rel = m.get("related")
        try:
            rel = json.loads(rel) if isinstance(rel, str) else (rel or {})
        except Exception:
            rel = {}
        if not isinstance(rel, dict):
            continue
        for k, ids in rel.items():
            if not ids:
                continue
            used[(str(m.get("doc_type") or ""), str(k))] += 1
            for i in (ids if isinstance(ids, list) else [ids])[:3]:
                targets[str(k)].append(str(i))
    unmapped: List[Dict[str, Any]] = []
    for (dt, key), n in sorted(used.items(), key=lambda kv: -kv[1]):
        mapped = key in rkt or key in (er.get(dt) or {}) or key in (er.get("*") or {})
        if mapped or n < THRESHOLDS["related_min_docs"]:
            continue
        fake_type = key.rstrip("s")
        fake_n = sum(1 for e in by_id.values() if e.get("type") == fake_type)
        # 대상 타입 추정: 참조 id 가 이미 엔티티면 그 타입, 아니면 그 ext_id 를 가진 문서의 doc_type
        guess: Counter = Counter()
        ext_to_type = {str(m.get("ext_id") or "").upper(): str(m.get("doc_type") or "") for m in meta_rows if m.get("ext_id")}
        for i in targets.get(key, [])[:20]:
            e = by_id.get(entity_id_for(i.upper()))
            if e and e.get("type") and e["type"] != fake_type:
                guess[e["type"]] += 1
            elif i.upper() in ext_to_type:
                guess[ext_to_type[i.upper()]] += 1
        tgt = guess.most_common(1)[0][0] if guess else dt or "document"
        unmapped.append({"doc_type": dt, "key": key, "docs": n, "fallback_rel": "references", "fake_type": fake_type,
                         "fake_type_entities": fake_n, "fake_type_known": fake_type in known_types, "guessed_target_type": tgt,
                         "sample_targets": targets.get(key, [])[:3]})
    if not unmapped:
        return []
    snippet: Dict[str, Any] = {"related_key_type": {}, "explicit_rels": {}, "schema": {"relations": {}}}
    for u in unmapped:
        snippet["related_key_type"][u["key"]] = u["guessed_target_type"]
        snippet["explicit_rels"].setdefault(u["doc_type"] or "*", {})[u["key"]] = u["key"]
        snippet["schema"]["relations"][u["key"]] = {"desc": "front matter related.%s" % u["key"], "src": u["doc_type"] or "*", "dst": u["guessed_target_type"]}
    fake = [u for u in unmapped if u["fake_type_entities"]]
    return [_finding(
        "related_key_unmapped", "warn" if not fake else "error", "rules",
        "front matter related.* 키가 규칙에 없어 관계 이름이 뭉개지고 없는 타입이 생긴다",
        "`related.<key>` 는 explicit_rels 로 관계 이름을, related_key_type 으로 대상 타입을 정한다. 둘 다 없으면 관계는 전부 "
        "'references' 가 되고 대상 타입은 키에서 s 를 뗀 이름(%s)으로 **지어진다** — 그 타입은 어휘에 없어 검색·화면 어디에서도 뜻이 없다."
        % ", ".join(sorted({u["fake_type"] for u in unmapped})[:4]),
        {"keys": len(unmapped), "docs": sum(u["docs"] for u in unmapped), "fake_type_entities": sum(u["fake_type_entities"] for u in unmapped)},
        unmapped,
        {"kind": "rules_patch", "section": "related_key_type · explicit_rels · schema.relations", "snippet": snippet,
         "steps": ["대상 타입 추정값(guessed_target_type)이 맞는지 확인한다", "조각을 rules.json 에 넣고 lint 로 점검한다", "그래프를 다시 빌드한다"],
         "commands": ["python -m llmwiki graph-rules lint", "python -m llmwiki build graph", "python -m llmwiki graph profile --compare"]},
        {"metric": "size.entities_by_type[%s]" % (fake[0]["fake_type"] if fake else unmapped[0]["fake_type"]), "expect": "0", "command": "graph profile --compare"})]


def _doc_type_without_rules(rules: Dict[str, Any], type_counts: Counter) -> List[Dict[str, Any]]:
    """코퍼스에 있는 문서 유형인데 link_rules·explicit_rels·id_patterns 어디에도 없는 것 — 그 유형은 문서 노드 말고 아무 관계도 못 만든다."""
    covered = {str(lr.get("when_doc_type") or "") for lr in (rules.get("link_rules") or [])}
    covered |= set((rules.get("explicit_rels") or {}).keys())
    covered |= {str(p.get("type") or "") for p in (rules.get("id_patterns") or [])}
    wildcard = "*" in covered
    missing = [(dt, n) for dt, n in type_counts.most_common() if dt and dt != "(none)" and n >= THRESHOLDS["doc_type_min_docs"] and dt not in covered]
    if not missing or wildcard and not any(dt not in covered for dt, _ in missing):
        return []
    snippet = {"link_rules": [{"when_doc_type": dt, "target_type": "issue", "rel": "references", "weight": 0.8, "confidence": 0.9} for dt, _ in missing[:5]],
               "explicit_rels": {dt: {"issues": "references"} for dt, _ in missing[:5]}}
    return [_finding(
        "doc_type_without_rules", "warn", "rules",
        "규칙이 하나도 없는 문서 유형이 있다",
        "문서 유형마다 '본문의 ID 언급 → 어떤 관계'(link_rules)와 'front matter related.* → 어떤 관계'(explicit_rels)를 정해야 "
        "구조 관계가 생긴다. 없으면 그 유형 문서는 공동출현(cooccur) 관계만 갖고, 그래프 검색에서 방향이 없다.",
        {"doc_types": len(missing), "docs": sum(n for _, n in missing)},
        [{"doc_type": dt, "docs": n} for dt, n in missing],
        {"kind": "rules_patch", "section": "link_rules · explicit_rels", "snippet": snippet,
         "steps": ["그 유형 문서가 본문에서 어떤 ID(이슈·CL·스펙)를 언급하는지 본다 → link_rules 의 target_type/rel 을 정한다",
                   "front matter 에 related.<key> 를 쓴다면 explicit_rels 에 키를 적는다", "빌드 뒤 규칙 기여 표에서 관계 수가 0 이 아닌지 본다"],
         "commands": ["python -m llmwiki corpus types", "python -m llmwiki graph-rules lint", "python -m llmwiki build graph"]},
        {"metric": "rules.rows[link_rule %s → *].relations" % missing[0][0], "expect": "> 0", "command": "graph profile"})]


def _dead_rules(store, prof: Dict[str, Any], rules: Dict[str, Any], type_counts: Counter, ent_type_counts: Counter,
                meta_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """죽은 규칙을 **원인별로** 나눈다: 그 doc_type 문서 0건 / 정규식이 안 맞음(실제 필드 이름 첨부) / 대상 타입 없음 / related 키 없음."""
    dead = prof["rules"].get("dead_rules") or []
    if not dead:
        return []
    no_docs: List[Dict[str, Any]] = []
    no_match: List[Dict[str, Any]] = []
    # 정규식 검사용 샘플 청크 (문서 유형별 최대 40개)
    sample_text: Dict[str, List[str]] = defaultdict(list)
    try:
        for r in store.conn.execute("SELECT c.doc_id, c.text, m.doc_type FROM chunks c LEFT JOIN doc_meta m ON m.doc_id=c.doc_id ORDER BY c.chunk_id LIMIT 4000"):
            dt = str(r["doc_type"] or "(none)")
            if len(sample_text[dt]) < 40:
                sample_text[dt].append(r["text"] or "")
    except Exception:
        pass
    all_text = [t for lst in sample_text.values() for t in lst]
    field_names: Counter = Counter()
    for t in all_text[:400]:
        for m in _FIELD_LINE.finditer(t):
            field_names[m.group(1).strip()] += 1
    rel_by_name = {str(p.get("name") or ""): p for p in (rules.get("relation_patterns") or [])}
    related_keys_used: Dict[str, set] = defaultdict(set)
    for m in meta_rows:
        try:
            rel = json.loads(m["related"]) if isinstance(m.get("related"), str) else (m.get("related") or {})
        except Exception:
            rel = {}
        if isinstance(rel, dict):
            related_keys_used[str(m.get("doc_type") or "")] |= {k for k, v in rel.items() if v}
    for d in dead:
        kind, name = d.get("kind"), str(d.get("name") or "")
        if kind == "link_rule":
            m = re.match(r"(.+?) → (.+?) \[(.+)\]", name)
            w, tgt = (m.group(1), m.group(2)) if m else ("*", "*")
            if w != "*" and type_counts.get(w, 0) == 0:
                no_docs.append({"kind": kind, "name": name, "cause": "doc_type '%s' 문서가 0건" % w, "doc_type": w})
            elif tgt != "*" and ent_type_counts.get(tgt, 0) == 0:
                no_match.append({"kind": kind, "name": name, "cause": "대상 타입 '%s' 엔티티가 0개 — 그 ID 를 잡는 id_patterns 가 없거나 본문에 ID 언급이 없다" % tgt,
                                 "hint": "id_patterns 에 '%s' 유형의 ID 정규식이 있는지, 문서 본문이 그 ID 를 쓰는지 본다" % tgt})
            else:
                no_match.append({"kind": kind, "name": name, "cause": "문서도 대상 타입도 있는데 본문에서 대상 ID 언급이 잡히지 않았다",
                                 "hint": "`graph-rules test --doc-type %s --ext-id <id> \"본문 한 줄\"` 로 실제 추출을 확인" % w})
        elif kind == "relation_pattern":
            p = rel_by_name.get(name) or {}
            rx = str(p.get("regex") or "")
            n_hit = 0
            try:
                cre = re.compile(rx)
                n_hit = sum(1 for t in all_text if cre.search(t))
            except re.error:
                pass
            similar = [f for f, _ in field_names.most_common(40) if name.lower().split("_")[0] in f.lower()][:3]
            no_match.append({"kind": kind, "name": name, "regex": rx, "cause": "샘플 청크 %d개 중 정규식이 맞은 것 %d개" % (len(all_text), n_hit),
                             "hint": "코퍼스에 실제로 있는 필드 이름: %s" % ", ".join("%s(%d)" % kv for kv in field_names.most_common(8)),
                             "similar_fields": similar})
        elif kind == "explicit_rel":
            m = re.match(r"(.+?)\.(.+?) \[", name)
            dt, key = (m.group(1), m.group(2)) if m else ("*", name)
            if dt != "*" and type_counts.get(dt, 0) == 0:
                no_docs.append({"kind": kind, "name": name, "cause": "doc_type '%s' 문서가 0건" % dt, "doc_type": dt})
            elif key not in related_keys_used.get(dt, set()) and not (dt == "*" and any(key in s for s in related_keys_used.values())):
                no_docs.append({"kind": kind, "name": name, "cause": "'%s' 문서의 front matter 에 related.%s 가 없다" % (dt, key), "doc_type": dt,
                                "corpus_fix": "related:\n  %s: [<대상 ID>]" % key})
            else:
                no_match.append({"kind": kind, "name": name, "cause": "related.%s 는 있는데 대상 ID 가 엔티티로 없다(끊긴 참조)" % key,
                                 "hint": "대상 문서가 코퍼스에 있는지, ID 표기가 같은지 본다"})
        elif kind == "id_pattern":
            if type_counts.get(name, 0) == 0:
                no_docs.append({"kind": kind, "name": name, "cause": "doc_type '%s' 문서가 0건이고 본문 언급도 없다" % name, "doc_type": name})
            else:
                no_match.append({"kind": kind, "name": name, "cause": "'%s' 문서는 있는데 ID 정규식이 ext_id·본문과 맞지 않는다" % name,
                                 "hint": "`corpus lint` 의 id_pattern 오류를 본다"})
        else:
            no_match.append({"kind": kind, "name": name, "cause": "만든 것이 없다", "hint": ""})
    out: List[Dict[str, Any]] = []
    if no_docs:
        out.append(_finding(
            "dead_rule_no_docs", "info", "corpus",
            "규칙은 있는데 그 규칙이 볼 문서가 코퍼스에 없다",
            "규칙 자체의 문제가 아니다. 그 문서 유형이 아직 코퍼스에 없거나, front matter 에 해당 related 키를 쓰지 않는다. "
            "그 유형을 쓸 계획이 없으면 규칙을 지워도 되고, 쓸 계획이면 문서를 규약대로 만들면 살아난다.",
            {"rules": len(no_docs)}, no_docs,
            {"kind": "corpus_edit", "files": [], "steps": ["docs/CORPUS_CONTRACT.md §3 의 유형별 규약(ID·front matter·related)을 따라 문서를 만든다",
                                                            "이미 있는 문서면 front matter 에 related.<key> 를 넣는다", "빌드(증분) 뒤 규칙 기여 표를 다시 본다"],
             "commands": ["python -m llmwiki corpus types", "python -m llmwiki corpus lint", "python -m llmwiki build"]},
            {"metric": "rules.dead_rules", "expect": "해당 규칙이 목록에서 사라짐", "command": "graph profile"}))
    if no_match:
        out.append(_finding(
            "dead_rule_no_match", "warn", "rules",
            "문서는 있는데 규칙이 아무것도 잡지 못했다",
            "정규식이 코퍼스의 실제 표기와 다르거나, 대상 ID 를 만드는 id_patterns 가 없다. 각 항목에 원인과 코퍼스에 실제로 있는 필드 이름을 붙였다.",
            {"rules": len(no_match)}, no_match,
            {"kind": "rules_patch", "section": "relation_patterns · link_rules · id_patterns", "snippet": None,
             "steps": ["항목의 cause/hint 대로 정규식이나 대상 타입을 고친다", "`graph-rules test` 로 문장 하나를 넣어 추출을 확인한다", "빌드 뒤 규칙 기여 표에서 0 이 아닌지 본다"],
             "commands": ["python -m llmwiki graph-rules test \"<본문 한 줄>\" --doc-type <type>", "python -m llmwiki build graph"]},
            {"metric": "rules.dead_rules", "expect": "해당 규칙이 목록에서 사라짐", "command": "graph profile"}))
    return out


def _junk_titles(docs: List[Dict[str, Any]], meta: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """제목이 코드·shebang·기호·너무 짧음·여러 문서에 동일 → 문서 노드 별칭이 오염돼 중복 후보·검색 잡음이 생긴다."""
    by_title: Counter = Counter(str(d.get("title") or "").strip() for d in docs)
    junk: List[Dict[str, Any]] = []
    for d in docs:
        t = str(d.get("title") or "").strip()
        why = ""
        if not t or len(t) < THRESHOLDS["junk_title_min_len"]:
            why = "비었거나 너무 짧음"
        elif _JUNK_TITLE.match(t):
            why = "코드·주석·shebang 줄"
        elif sum(1 for ch in t if not ch.isalnum() and not ch.isspace()) > len(t) * 0.5:
            why = "기호가 절반 이상"
        elif by_title[t] >= 3:
            why = "같은 제목 문서 %d개" % by_title[t]
        if why:
            junk.append({"doc_id": d["doc_id"], "path": d.get("path") or "", "title": t[:60], "why": why,
                         "doc_type": str((meta.get(d["doc_id"]) or {}).get("doc_type") or "")})
    if not junk:
        return []
    return [_finding(
        "junk_titles", "warn", "corpus",
        "문서 제목이 쓰레기라 문서 노드 별칭이 오염된다",
        "문서 노드는 제목을 별칭으로 갖는다. 가져온 파일의 첫 줄이 제목이 되면 '-*- coding -*-' 같은 것이 별칭이 되어 여러 문서가 한 이름을 "
        "공유하고(합치기 후보 오탐), 검색 결과의 제목도 읽을 수 없다.",
        {"docs": len(junk), "of": len(docs)}, junk[:20],
        {"kind": "corpus_edit", "files": [j["path"] or j["doc_id"] for j in junk[:20]],
         "steps": ["각 파일 맨 위 front matter 에 `title: <사람이 읽을 제목>` 을 넣는다 (없으면 첫 줄이 제목이 된다)",
                   "가져오기(corpus ingest)로 만든 NOTE 문서면 원본 파일의 첫 줄을 고치거나 --title 을 준다", "증분 빌드"],
         "commands": ["python -m llmwiki corpus lint", "python -m llmwiki build"]},
        {"metric": "quality.duplicate_candidates", "expect": "감소", "command": "graph profile --compare"})]


def _no_communities(prof: Dict[str, Any], toggles: Any) -> List[Dict[str, Any]]:
    if prof["size"]["communities"] > 0 or prof["size"]["entities"] == 0:
        return []
    on = bool(getattr(toggles, "communities", True))
    inc = bool(getattr(toggles, "incremental_communities", False))
    return [_finding(
        "no_communities", "warn", "build",
        "커뮤니티(무리)가 하나도 없다",
        ("토글 communities 는 켜져 있는데 0개다. 증분 빌드는 incremental_communities 가 꺼져 있으면 무리 탐지를 건너뛴다 — 전체 빌드나 `build graph` 뒤에만 생긴다."
         if on else "토글 communities 가 꺼져 있다."),
        {"communities": 0, "toggle_communities": on, "incremental_communities": inc}, [],
        {"kind": "build_cmd", "steps": ["`build graph` 로 그래프 채널만 다시 만든다 (무리 탐지 포함)",
                                        "증분 빌드마다 갱신하려면 config.json toggles.incremental_communities=true (그래프가 크면 느리다)"] if on
         else ["config.json toggles.communities=true 로 켜고 `build graph`"],
         "commands": ["python -m llmwiki build graph"]},
        {"metric": "size.communities", "expect": "> 0", "command": "graph profile"})]


def _structure_share(prof: Dict[str, Any], rels: List[Dict[str, Any]], meta: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """mentions·co_occurs 를 뺀 **구조 관계**(explicit·rule·human·llm) 비율 — cooccur_share 는 문서→엔티티 mentions 가 부풀린다."""
    n = len(rels)
    if n < THRESHOLDS["structure_min_relations"]:
        return []
    struct = [r for r in rels if (r.get("provenance") or "") in ("explicit", "rule", "human", "llm") and r["rel"] not in COOCCUR_RELS]
    share = _ratio(len(struct), n)
    by_prov = Counter((r.get("provenance") or "?") for r in struct)
    by_rel = Counter(r["rel"] for r in struct)
    if share >= THRESHOLDS["structure_share"]:
        return []
    return [_finding(
        "structure_share_low", "warn", "corpus",
        "구조 관계(규칙·front matter·사람·LLM)가 전체의 %.0f%% 뿐이다" % (share * 100),
        "관계의 대부분이 '같은 청크에 함께 나옴'(co_occurs)과 '문서가 언급함'(mentions)이다. 이런 관계는 방향도 뜻도 없어 그래프 검색이 "
        "이웃을 고를 때 아무 근거가 없다. 구조 관계는 문서 front matter 의 related.* 와 본문의 ID 언급(link_rules)·필드 정규식(relation_patterns)에서 온다.",
        {"structural": len(struct), "total": n, "share": share, "by_provenance": dict(by_prov), "top_rels": dict(by_rel.most_common(6))}, [],
        {"kind": "corpus_edit", "files": [],
         "steps": ["문서 front matter 에 related.<key>: [ID…] 를 채운다 (이슈↔CL, 설계↔스펙처럼 문서끼리의 관계)",
                   "본문에 다른 문서의 ID 를 표기 규약대로 적는다 (link_rules 가 잡는다)",
                   "반복되는 필드(담당·모듈·마감)는 relation_patterns 로 관계를 만든다"],
         "commands": ["python -m llmwiki corpus lint", "python -m llmwiki build", "python -m llmwiki graph profile --compare"]},
        {"metric": "findings[structure_share_low].evidence.share", "expect": "≥ %.2f" % THRESHOLDS["structure_share"], "command": "graph profile --compare"})]


def _uncovered_docs(store, prof: Dict[str, Any], docs: List[Dict[str, Any]], meta: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """커버리지 미달 유형의 미커버 문서를 **왜** 로 나눈다: ID 없음 / 본문에 사전 엔티티 없음 / 본문이 짧음."""
    cov = prof["coverage"]["by_doc_type"]
    low = {dt: r for dt, r in cov.items() if r["total"] >= 3 and r["pct"] < THRESHOLDS["uncovered_pct"]}
    if not low:
        return []
    doc_by_id = {d["doc_id"]: d for d in docs}
    samples: List[Dict[str, Any]] = []
    why_cnt: Counter = Counter()
    for dt, r in low.items():
        for did in (r.get("uncovered_samples") or [])[:5]:
            m = meta.get(did) or {}
            d = doc_by_id.get(did) or {}
            why = []
            if not m.get("ext_id") or m.get("inferred"):
                why.append("ext_id 없음/추론")
            if int(d.get("n_chunks") or 0) <= 1 and int(d.get("size") or 0) < 400:
                why.append("본문이 짧음")
            n_m = store.conn.execute("SELECT COUNT(*) FROM mentions WHERE doc_id=?", (did,)).fetchone()[0]
            if not n_m:
                why.append("사전 엔티티가 한 번도 안 나옴")
            for w in why or ["이유 불명"]:
                why_cnt[w] += 1
            samples.append({"doc_id": did, "doc_type": dt, "path": d.get("path") or "", "why": ", ".join(why) or "이유 불명"})
    return [_finding(
        "uncovered_docs", "warn", "corpus",
        "엔티티가 하나도 없는 문서가 많은 유형이 있다: %s" % ", ".join("%s %.0f%%" % (dt, r["pct"]) for dt, r in list(low.items())[:4]),
        "문서에 엔티티가 없으면 그래프 채널은 그 문서를 절대 찾지 못한다(FTS·벡터만 남는다). 원인은 셋 중 하나다 — 문서 ID 가 없어 문서 노드가 "
        "제목 노드로만 남거나, 본문 용어가 사전(entities)에 없거나, 본문이 너무 짧다.",
        {"doc_types": len(low), "why": dict(why_cnt)}, samples[:20],
        {"kind": "corpus_edit", "files": [s["path"] or s["doc_id"] for s in samples[:20]],
         "steps": ["'ext_id 없음' → front matter 에 id: 를 규약대로 넣는다 (CORPUS_CONTRACT.md §3)",
                   "'사전 엔티티 없음' → 그 유형 문서의 핵심 용어(제품·모듈·팀)를 rules.json entities 에 넣는다",
                   "'본문이 짧음' → 문서를 합치거나 요약 필드를 채운다"],
         "commands": ["python -m llmwiki corpus lint", "python -m llmwiki build", "python -m llmwiki graph profile --compare"]},
        {"metric": "coverage.by_doc_type[%s].pct" % list(low)[0], "expect": "≥ %.0f" % THRESHOLDS["uncovered_pct"], "command": "graph profile --compare"})]


def _isolated_by_type(prof: Dict[str, Any], rules: Dict[str, Any], by_id: Dict[str, Dict[str, Any]], deg: Counter) -> List[Dict[str, Any]]:
    c = prof["connectivity"]
    if c["isolated_ratio"] < THRESHOLDS["isolated_ratio"] or prof["size"]["entities"] < 20:
        return []
    dict_names = {entity_id_for(str(k)) for k in (rules.get("entities") or {}) if not str(k).startswith("_")}
    tfc = set(rules.get("types_for_cooccur") or [])
    iso = [e for eid, e in by_id.items() if deg.get(eid, 0) == 0]
    from_dict = [e for e in iso if e["entity_id"] in dict_names]
    by_type = Counter(e["type"] or "?" for e in iso)
    types_not_cooccur = [t for t, _ in by_type.most_common() if t not in tfc and t not in ("date", "amount", "percent", "document")]
    snippet = {"types_for_cooccur": sorted(tfc | set(types_not_cooccur[:3]))} if types_not_cooccur else None
    return [_finding(
        "isolated_by_type", "warn", "rules",
        "고립 엔티티가 %.0f%% — 이름만 잡히고 관계가 없다" % (c["isolated_ratio"] * 100),
        "고립 엔티티는 그래프 검색의 시드가 돼도 갈 곳이 없다. 사전 엔티티가 고립이면 그 유형이 types_for_cooccur 에 없어 공동출현 관계조차 안 생기는 것이고, "
        "규칙이 만든 엔티티(ID·값)가 고립이면 link_rules/relation_patterns 가 그것을 문서에 잇지 않는 것이다.",
        {"isolated": c["isolated"], "ratio": c["isolated_ratio"], "from_dictionary": len(from_dict), "by_type": dict(by_type.most_common(8)),
         "types_not_in_cooccur": types_not_cooccur[:6]},
        [{"name": e["name"], "type": e["type"], "source": e.get("source")} for e in (from_dict or iso)[:12]],
        {"kind": "rules_patch", "section": "types_for_cooccur · link_rules", "snippet": snippet,
         "steps": ["사전 엔티티 유형이 types_for_cooccur 에 있는지 본다 (조각 참고)", "ID·값 노드면 link_rules/relation_patterns 로 문서에 잇는다", "빌드 뒤 고립 비율을 비교한다"],
         "commands": ["python -m llmwiki build graph", "python -m llmwiki graph profile --compare"]},
        {"metric": "connectivity.isolated_ratio", "expect": "< %.2f" % THRESHOLDS["isolated_ratio"], "command": "graph profile --compare"})]


def _no_seed_queries(prof: Dict[str, Any]) -> List[Dict[str, Any]]:
    us = prof["usage"]
    if us["requests"] < 5 or (1 - us["seed_share"]) < THRESHOLDS["no_seed_share"]:
        return []
    kws = [k for k in us["no_seed_keywords"] if k["count"] >= THRESHOLDS["keyword_min_count"]][:10]
    snippet = {"alias": {k["keyword"]: "<대표 엔티티 이름>" for k in kws[:6]}}
    return [_finding(
        "no_seed_queries", "warn", "query_rules",
        "최근 질의의 %.0f%% 가 그래프 시드 없이 실행됐다" % ((1 - us["seed_share"]) * 100),
        "질의의 키워드가 어느 엔티티 이름·별칭과도 맞지 않으면 그래프 채널은 아무것도 하지 않는다. 사람들이 실제로 쓰는 표기(약어·한글 표기)가 "
        "사전에 없는 것이다. 엔티티라면 rules.json entities 의 aliases 에, 표기 차이라면 query_rules.json alias/acronym 에 넣는다.",
        {"requests": us["requests"], "no_seed_share": round(1 - us["seed_share"], 3), "keywords": [k["keyword"] for k in kws]},
        [{"keyword": k["keyword"], "count": k["count"]} for k in kws] + [{"sample_query": q} for q in (us.get("no_seed_samples") or [])[:4]],
        {"kind": "query_rules_patch", "section": "alias (query_rules.json) · entities.<name>.aliases (rules.json)", "snippet": snippet,
         "steps": ["키워드가 기존 엔티티의 다른 표기면 rules.json entities.<name>.aliases 에 넣고 build graph",
                   "질의 쪽 표기 차이(약어·띄어쓰기)면 query_rules.json alias/acronym 에 넣는다 (재빌드 불필요)",
                   "새 개념이면 entities 에 새 항목으로 — evolve 제안 목록에도 같은 후보가 올라와 있다"],
         "commands": ["python -m llmwiki rules test \"<질의>\"", "python -m llmwiki evolve list"]},
        {"metric": "usage.seed_share", "expect": "≥ %.2f" % (1 - THRESHOLDS["no_seed_share"]), "command": "graph profile"})]


def _hub_type_policy(rules: Dict[str, Any]) -> List[Dict[str, Any]]:
    tfc = list(rules.get("types_for_cooccur") or [])
    weak = [t for t in tfc if t in ("date", "amount", "percent", "document")]
    if not weak:
        return []
    return [_finding(
        "hub_type_policy_mismatch", "warn", "rules",
        "types_for_cooccur 에 날짜·금액·문서 같은 약한 유형이 들어 있다",
        "이런 유형은 어느 청크에나 있어 모든 엔티티와 공동출현 관계를 맺고 허브가 된다. 커뮤니티 탐지는 이 유형을 이미 제외하는데 관계 생성이 제외하지 않으면 그래프 검색만 흐려진다.",
        {"weak_types": weak, "types_for_cooccur": tfc}, [],
        {"kind": "rules_patch", "section": "types_for_cooccur", "snippet": {"types_for_cooccur": [t for t in tfc if t not in weak]},
         "steps": ["조각처럼 약한 유형을 뺀다", "build graph"], "commands": ["python -m llmwiki build graph"]},
        {"metric": "connectivity.hub_warnings", "expect": "0", "command": "graph profile --compare"})]


def _id_missing(meta_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_type: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    samples: List[Dict[str, Any]] = []
    for m in meta_rows:
        dt = str(m.get("doc_type") or "(none)")
        by_type[dt][1] += 1
        if not m.get("ext_id") or int(m.get("inferred") or 0):
            by_type[dt][0] += 1
            if len(samples) < 12:
                samples.append({"doc_id": m["doc_id"], "doc_type": dt, "ext_id": m.get("ext_id") or "", "inferred": bool(m.get("inferred"))})
    bad = {dt: v for dt, v in by_type.items() if v[1] >= 3 and dt != "note" and _ratio(v[0], v[1]) >= THRESHOLDS["id_missing_share"]}
    if not bad:
        return []
    return [_finding(
        "id_missing", "info", "corpus",
        "문서 ID(ext_id)가 없거나 추론된 문서가 많다: %s" % ", ".join("%s %d/%d" % (dt, v[0], v[1]) for dt, v in list(bad.items())[:4]),
        "문서 ID 는 문서 노드의 이름이고 다른 문서가 이 문서를 가리키는 열쇠다. ID 가 없으면 제목이 노드가 되어 다른 문서의 related.* 와 본문 언급이 이 문서에 닿지 못한다.",
        {"by_doc_type": {dt: {"missing": v[0], "total": v[1]} for dt, v in bad.items()}}, samples,
        {"kind": "corpus_edit", "files": [s["doc_id"] for s in samples],
         "steps": ["front matter 에 `id: <유형 규약의 ID>` 를 넣는다 (예: ISSUE-2001, CL-55321 — CORPUS_CONTRACT.md §3)", "증분 빌드"],
         "commands": ["python -m llmwiki corpus lint", "python -m llmwiki build"]},
        {"metric": "findings[id_missing].evidence.by_doc_type", "expect": "missing 감소", "command": "graph profile --compare"})]


# ---------------------------------------------------------------- 진입점
def compute(pipe, prof: Dict[str, Any], rules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """소견 목록을 만든다. 반환 {findings: […], errors: […], thresholds: {…}, by_area: {…}}.

    소견 하나가 죽어도 나머지는 살린다 — 진단은 관측이라 실패해도 프로파일을 막지 않는다."""
    store = pipe.store
    rules = rules if isinstance(rules, dict) else _gr.load_rules()
    ents = store.entities(10_000_000)
    by_id = {e["entity_id"]: e for e in ents}
    rels = store.relations_all()
    deg: Counter = Counter()
    for r in rels:
        if r["src"] in by_id and r["dst"] in by_id and r["src"] != r["dst"]:
            deg[r["src"]] += 1
            deg[r["dst"]] += 1
    docs = store.list_docs()
    meta = store.doc_meta_map()
    try:
        meta_rows = [dict(r) for r in store.conn.execute("SELECT doc_id, doc_type, ext_id, inferred, related FROM doc_meta")]
    except Exception:
        meta_rows = []
    type_counts: Counter = Counter(str(m.get("doc_type") or "(none)") for m in meta_rows)
    ent_type_counts: Counter = Counter(e["type"] or "?" for e in ents)
    known = _gr.known_types(rules)
    toggles = getattr(pipe.s, "toggles", None)
    steps = [
        ("alias_false_positive", lambda: _alias_false_positive(store, prof, rules, by_id)),
        ("related_key_unmapped", lambda: _related_key_unmapped(store, rules, meta_rows, by_id, known)),
        ("doc_type_without_rules", lambda: _doc_type_without_rules(rules, type_counts)),
        ("dead_rules", lambda: _dead_rules(store, prof, rules, type_counts, ent_type_counts, meta_rows)),
        ("junk_titles", lambda: _junk_titles(docs, meta)),
        ("no_communities", lambda: _no_communities(prof, toggles)),
        ("structure_share_low", lambda: _structure_share(prof, rels, meta)),
        ("uncovered_docs", lambda: _uncovered_docs(store, prof, docs, meta)),
        ("isolated_by_type", lambda: _isolated_by_type(prof, rules, by_id, deg)),
        ("no_seed_queries", lambda: _no_seed_queries(prof)),
        ("hub_type_policy_mismatch", lambda: _hub_type_policy(rules)),
        ("id_missing", lambda: _id_missing(meta_rows)),
    ]
    findings: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for name, fn in steps:
        try:
            findings.extend(fn())
        except Exception as e:  # noqa: BLE001 — 관측은 본체를 막지 않는다
            errors.append({"step": name, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
    order = {"error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 9), FINDING_IDS.index(f["id"]) if f["id"] in FINDING_IDS else 99))
    by_area = Counter(f["area"] for f in findings)
    return {"findings": findings, "errors": errors, "thresholds": dict(THRESHOLDS),
            "by_area": {a: by_area.get(a, 0) for a in AREAS}, "by_severity": dict(Counter(f["severity"] for f in findings))}


def render(fr: Dict[str, Any], md: bool = False) -> List[str]:
    """CLI 텍스트 / 마크다운 보고서용 소견 절."""
    L: List[str] = []
    fs = (fr or {}).get("findings") or []
    if not fs:
        L.append("  (소견 없음 — 임계값 안)" if not md else "(소견 없음 — 임계값 안)")
        return L
    sev = {"error": "⛔", "warn": "⚠", "info": "ℹ"}
    for i, f in enumerate(fs, 1):
        head = "%s %d. [%s] %s" % (sev.get(f["severity"], "•"), i, f["area"], f["title"])
        L.append(("### " + head) if md else head)
        nums = f["evidence"].get("numbers") or {}
        L.append(("- " if md else "     ") + "증거: " + json.dumps(nums, ensure_ascii=False)[:300])
        L.append(("- " if md else "     ") + "원인: " + f["why"])
        fx = f["fix"]
        L.append(("- " if md else "     ") + "처방(%s%s): %s" % (fx["kind"], (" · " + fx["section"]) if fx.get("section") else "", " → ".join(fx.get("steps") or [])))
        if fx.get("snippet"):
            snip = json.dumps(fx["snippet"], ensure_ascii=False, indent=2)
            L.append(("```json\n" + snip + "\n```") if md else "     조각:\n" + "\n".join("       " + ln for ln in snip.splitlines()[:30]))
        if fx.get("files"):
            L.append(("- " if md else "     ") + "파일: " + ", ".join(str(x) for x in fx["files"][:8]) + (" …" if len(fx["files"]) > 8 else ""))
        if fx.get("commands"):
            L.append(("- " if md else "     ") + "명령: " + " ; ".join(fx["commands"]))
        v = f.get("verify") or {}
        L.append(("- " if md else "     ") + "확인: %s → %s (%s)" % (v.get("metric"), v.get("expect"), v.get("command")))
        if md:
            L.append("")
    if fr.get("errors"):
        L.append(("- " if md else "  ") + "계산 실패: " + "; ".join("%s: %s" % (e["step"], e["error"]) for e in fr["errors"]))
    return L
