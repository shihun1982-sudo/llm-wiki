# -*- coding: utf-8 -*-
"""질의 해부 (LLM 없이) — "이 질문이 검색에 들어가기 전에 무엇으로 변했나" 를 한 번에 보여 준다.

왜 따로 있나: 답이 이상할 때 사람들은 먼저 "내 질문이 제대로 이해됐나" 를 보고 싶어 한다.
그런데 그 정보는 토큰화(textutil) · 규칙 확장(query_rules) · 시간 해석(timeparse) ·
채널 라우팅(retrieval.route) · 고정 근거(pins) 에 흩어져 있어서, 예전에는 화면마다 조각으로만 보였다.
여기서는 **LLM 을 한 번도 부르지 않고** 그 조각을 모아 한 딕셔너리로 돌려준다 (수 ms, 토큰 0).

세 창구가 같은 함수를 쓴다:
  CLI  `python -m llmwiki inspect "질의"`
  Web  Ask › 디버그 탭 (POST /api/debug/query)
  MCP  wiki_inspect

반환
  {query, normalized, tokens, keywords, stopwords_removed,
   fts:{match_expr, rule_match, alt_queries, related, exclude, seeds, fired},
   time:{scope|null, mode},
   router:{kind, weights, reason},
   pins:[…], stats:{…}}
"""
from __future__ import annotations

from typing import Any, Dict, List

from .config import Settings
from .store import Store


def _acl_filter(pipe: Any, role: str = ""):
    """열람 출구에서 쓸 문서 접근 판정기. role 을 주면 그 역할, 비우면 pipe 의 요청 신분.

    규칙이 없거나 토글이 꺼져 있으면 `enabled=False` 인 판정기가 나와 예전과 똑같이 동작한다.
    """
    from . import docacl as _acl
    try:
        if not bool(getattr(pipe.s.toggles, "doc_acl", True)):
            return None
        if role:
            return _acl.Filter(role, pipe.store.doc_meta_map())
        return pipe.acl_filter()
    except Exception:
        return None


def inspect_query(pipe: Any, query: str) -> Dict[str, Any]:
    """질의 하나를 LLM 없이 해부한다. pipe 는 Pipeline (store·settings·tuning 을 쓴다)."""
    from . import query_rules as _qr
    from . import timeparse as _tp
    from . import tuning as _tuning
    from .retrieval import route
    from .textutil import fts_query, keywords, tokenize_for_fts, words, STOPWORDS

    s: Settings = pipe.s
    store: Store = pipe.store
    T = _tuning.T
    q = str(query or "")
    out: Dict[str, Any] = {"query": q}

    # ---- 1. 토큰화: 무엇이 검색어로 남는가 ----
    raw_words = words(q)
    _t = tokenize_for_fts(q)
    toks = _t.split() if isinstance(_t, str) else list(_t)   # 구현에 따라 문자열/리스트 둘 다 온다
    kws = keywords(q)
    removed = [w for w in raw_words if w.lower() in STOPWORDS]
    out["tokens"] = toks
    out["keywords"] = kws
    out["stopwords_removed"] = removed
    out["normalized"] = " ".join(toks)

    # ---- 3. 시간 표현 ----
    # **순서가 중요하다**: 실제 질의 경로(query_engine)는 시간 표현을 먼저 떼어 내고, 남은 말(q_search)로
    # 규칙 확장과 채널 라우팅을 한다. 예전에는 이 화면이 셋 다 **원문 q** 로 계산해서, "지난주 ISSUE-2001"
    # 같은 질의에서 화면과 실제가 달랐다 — 디버그 화면이 거짓말을 한 셈이다 (2026-09-20 수정).
    try:
        scope = _tp.parse(q, getattr(s, "timezone", "Asia/Seoul"), getattr(s, "week_start", "mon"))
    except Exception:
        scope = None
    q_search = q
    if s.toggles.time_scope and scope and scope.get("query"):
        q_search = scope["query"]
    out["time"] = {"scope": scope, "mode": T.get("time_mode"), "enabled": bool(s.toggles.time_scope),
                   "q_search": q_search, "stripped": q_search != q}

    # ---- 2. 규칙 확장: 사전이 무엇을 들여오는가 (실제 경로와 같이 q_search 로) ----
    try:
        r = _qr.expand(q_search, T.get("syn_w"), T.get("related_w"), T.get("acronym_phrase"))
    except Exception as e:      # 규칙 파일이 깨져도 나머지는 보여 준다
        r = {"error": str(e)}
    out["fts"] = {
        "match_expr": fts_query(q_search),
        "rule_match": r.get("fts_query"),
        "alt_queries": r.get("alt_queries") or [],
        "related": r.get("related") or [],
        "exclude": r.get("exclude") or [],
        "seeds": r.get("seeds") or [],
        "fired": r.get("fired") or [],
        "error": r.get("error"),
        "input": q_search,
    }

    # ---- 4. 채널 라우팅: 어느 채널에 얼마나 무게를 두는가 ----
    try:
        rt = route(q_search, store)
    except Exception as e:
        rt = {"error": str(e)}
    out["router"] = rt

    # ---- 5. 고정 근거(pin) ----
    # 실제 경로는 라우터가 고른 문서 유형까지 넘긴다 — pin 의 doc_types 조건이 그것으로 판정된다.
    try:
        from . import pins as _pins
        out["pins"] = _pins.match_pins(store, q, (rt or {}).get("doc_types"))
    except Exception as e:
        out["pins"] = {"error": str(e)}

    out["stats"] = {"chars": len(q), "words": len(raw_words), "tokens": len(toks),
                    "keywords": len(kws), "rules_fired": len(out["fts"]["fired"] or [])}
    out["toggles"] = {k: bool(getattr(s.toggles, k, False)) for k in ("query_rules", "time_scope", "router", "pins", "fts", "vector", "graph")}
    return out


def doc_detail(pipe: Any, ident: str, max_chars: int = 20000, role: str = "") -> Dict[str, Any]:
    """문서 한 건의 전체 모습 — 메타 · 청크 목록 · 연결된 엔티티/관계 · 위키 페이지 이름.

    `ident` 는 doc_id(경로) 또는 문서 ID(ISSUE-2041, CL-55321) 둘 다 받는다.
    Web 의 문서 화면(Knowledge › 문서)과 MCP `wiki_doc` 가 같은 정보를 쓴다.

    `role` 을 주면 **문서 단위 접근 제어**(llmwiki/docacl.py)를 여기서 건다. 열람은 검색과 다른 출구라서
    `doc_acl` 단계만으로는 막히지 않는다 — 같은 판정기를 여기서도 쓴다. 비우면 pipe.actor 의 역할
    (내부 호출은 admin)을 쓴다. 후보 목록(alternatives)도 함께 걸러야 "무엇이 있는지" 조차 새지 않는다.
    """
    store = pipe.store
    ident = str(ident or "").strip()
    if not ident:
        return {"error": "문서 id 를 주세요"}
    docs = store.docs_by_ext_id(ident.upper()) or [d["doc_id"] for d in store.list_docs() if ident in d["doc_id"]]
    if not docs:
        return {"error": "문서를 찾지 못했습니다: %s" % ident, "query": ident}
    af = _acl_filter(pipe, role)
    if af is not None and af.enabled:
        allowed = [d for d in docs if af.doc_ok(d)]
        if not allowed:
            from . import docacl as _acl
            need, _why = _acl.min_role_for(docs[0], store.doc_meta_map().get(docs[0]), af.acl)
            return {"error": af.acl.get("deny_message") or "권한이 없는 문서입니다",
                    "detail": "이 문서는 %s 이상만 볼 수 있습니다 (현재 %s)" % (need, af.role),
                    "query": ident, "min_role": need, "role": af.role, "denied": True}
        docs = allowed
    doc_id = docs[0]
    chunks = [dict(c) for c in store.all_chunks(doc_id)]
    dm = dict(store.get_doc_meta(doc_id) or {})
    out: Dict[str, Any] = {
        "doc_id": doc_id, "alternatives": docs[1:10],
        "meta": {k: dm.get(k) for k in ("doc_type", "ext_id", "date", "status", "tags", "modules", "related", "hw_rev", "title")},
        "n_chunks": len(chunks),
        "chunks": [{"chunk_id": c.get("chunk_id") or c.get("id"), "heading": c.get("heading"),
                    "n": c.get("n"), "chars": len(str(c.get("text") or "")),
                    "text": str(c.get("text") or "")[:max_chars]} for c in chunks],
        "relations": [], "entities": [],
    }
    if dm.get("ext_id"):
        try:
            from .graph_rules import entity_id_for
            eid = entity_id_for(dm["ext_id"])
            rels = [r for r in store.relations_of(eid)
                    if r["rel"] not in ("co_occurs", "mentions", "mentions_date", "mentions_amount")][:25]
            out["relations"] = [{"src": (store.get_entity(r["src"]) or {}).get("name", r["src"]), "rel": r["rel"],
                                 "dst": (store.get_entity(r["dst"]) or {}).get("name", r["dst"]),
                                 "provenance": r.get("provenance")} for r in rels]
            e = store.get_entity(eid)
            if e:
                out["entities"] = [{"id": eid, "name": e.get("name"), "type": e.get("type")}]
        except Exception as ex:
            out["relations_error"] = str(ex)
    # 이 문서에서 나온 엔티티(멘션) — 위키 페이지로 건너가는 길
    try:
        ids = [c["chunk_id"] for c in out["chunks"] if c.get("chunk_id")]
        seen = {}
        for cid in ids[:40]:
            for m in (store.mentions_of(cid) if hasattr(store, "mentions_of") else []):
                ent = store.get_entity(m["entity_id"]) if isinstance(m, dict) else None
                if ent:
                    seen.setdefault(ent["id"], {"id": ent["id"], "name": ent.get("name"), "type": ent.get("type")})
        out["mentioned_entities"] = list(seen.values())[:30]
    except Exception:
        out["mentioned_entities"] = []
    return out


def render_text(d: Dict[str, Any]) -> str:
    """CLI 용 텍스트. Web 디버그 탭과 같은 순서로 읽히게 한다."""
    L: List[str] = []
    L.append("질의: %s" % d.get("query"))
    st = d.get("stats") or {}
    L.append("  %d자 · 단어 %d · 토큰 %d · 키워드 %d · 발화한 규칙 %d"
             % (st.get("chars", 0), st.get("words", 0), st.get("tokens", 0), st.get("keywords", 0), st.get("rules_fired", 0)))
    L.append("")
    L.append("[1] 토큰화")
    L.append("  검색 토큰 : %s" % " ".join(d.get("tokens") or []))
    L.append("  키워드    : %s" % ", ".join(d.get("keywords") or []))
    L.append("  불용어 제거: %s" % (", ".join(d.get("stopwords_removed") or []) or "(없음)"))
    t0 = d.get("time") or {}
    if t0.get("stripped"):
        L.append("")
        L.append("  ※ 시간 표현을 떼어 낸 뒤 **'%s'** 로 아래를 계산합니다 (실제 질의 경로와 같은 순서)" % t0.get("q_search"))
    f = d.get("fts") or {}
    L.append("")
    L.append("[2] 규칙 확장 (query_rules)")
    L.append("  입력        : %s" % (f.get("input") or d.get("query")))
    L.append("  FTS 식      : %s" % (f.get("match_expr") or "-"))
    L.append("  규칙 적용 식 : %s" % (f.get("rule_match") or "(규칙 없음)"))
    for a in (f.get("alt_queries") or [])[:6]:
        L.append("  대체 질의   : %s" % (a if isinstance(a, str) else " · ".join(str(x) for x in a)))
    for a in (f.get("related") or [])[:6]:
        L.append("  관련어      : %s" % (a if isinstance(a, str) else " · ".join(str(x) for x in a)))
    if f.get("exclude"):
        L.append("  제외        : %s" % ", ".join(str(x) for x in f["exclude"]))
    for x in (f.get("fired") or [])[:8]:
        L.append("  발화        : %s" % (x.get("matched") if isinstance(x, dict) else x))
    t = d.get("time") or {}
    L.append("")
    L.append("[3] 시간 표현 (mode=%s, 토글 %s)" % (t.get("mode"), "on" if t.get("enabled") else "off"))
    L.append("  %s" % ("해석된 범위 없음" if not t.get("scope") else str(t["scope"])))
    r = d.get("router") or {}
    L.append("")
    L.append("[4] 채널 라우팅")
    if r.get("error"):
        L.append("  오류: %s" % r["error"])
    else:
        w = r.get("weights") or {}
        L.append("  유형  : %s (%s)" % (r.get("kind"), r.get("kind_label") or ""))
        if r.get("kind_desc"):
            L.append("        %s" % r["kind_desc"])
        for why in (r.get("why") or []):
            L.append("  근거  : %s" % why)
        L.append("  가중치: %s   ← 이 유형이 하는 일은 이 숫자 하나뿐입니다 (채널을 끄거나 켜지 않습니다)"
                 % ", ".join("%s=%.2f" % (k, v) for k, v in w.items()))
        if r.get("reason"):
            L.append("  비고  : %s" % r["reason"])
    p = d.get("pins")
    L.append("")
    L.append("[5] 고정 근거(pin)")
    L.append("  %s" % (("%d건" % len(p)) if isinstance(p, list) else str(p)))
    tg = d.get("toggles") or {}
    L.append("")
    L.append("토글: " + " · ".join("%s=%s" % (k, "on" if v else "OFF") for k, v in tg.items()))
    return "\n".join(L)
