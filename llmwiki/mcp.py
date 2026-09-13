# -*- coding: utf-8 -*-
"""최소 MCP(Model Context Protocol) 서버 — stdio JSON-RPC, 표준 라이브러리만 사용.

트렌드 ⑤ "MCP 로 지식 그래프/도구에 표준 인터페이스 제공" 에 대응한다. Claude Desktop / Claude Code 등 MCP 클라이언트가
이 서버를 실행하면 아래 도구를 호출할 수 있다 (읽기 전용: 색인을 바꾸는 도구는 노출하지 않음 → 자가진화 통제 샌드박스 원칙).

  python -m llmwiki mcp            (stdin/stdout 으로 JSON-RPC 2.0, 한 줄에 하나의 메시지 또는 Content-Length 프레이밍)

도구: wiki_query(question) · wiki_search(channel, query, k) · wiki_entity(name) · wiki_status()
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "2025-06-18"

TOOLS: List[Dict[str, Any]] = [
    {"name": "wiki_query", "description": "사내 LLM Wiki 에 질문하고 인용([C#]) 이 붙은 구조화 답변·근거 문단·근거 판정(groundedness)을 받는다 (FTS+Vector+Graph 하이브리드). "
                                          "mode=deep 은 확장·분해·fallback 을 최대로 한 심층 조사(unified search).",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}, "k": {"type": "integer", "description": "근거 문단 수", "default": 8},
                                                      "mode": {"type": "string", "enum": ["fast", "normal", "deep"], "default": "normal"},
                                                      "doc_types": {"type": "array", "items": {"type": "string"}, "description": "우선할 문서 유형 (issue, cl, sw_design, hw_design, coding_rule, weekly_report, tc_list)"},
                                                      "preset": {"type": "string", "description": "presets.json 이름 (quality|speed|token|deep_research…)"}},
                     "required": ["question"]}},
    {"name": "wiki_search", "description": "단일 채널 검색 디버그: fts | vector | graph.",
     "inputSchema": {"type": "object", "properties": {"channel": {"type": "string", "enum": ["fts", "vector", "graph"]},
                                                      "query": {"type": "string"}, "k": {"type": "integer", "default": 8}},
                     "required": ["channel", "query"]}},
    {"name": "wiki_related", "description": "입력 텍스트(예: 이슈 분석 결과)와 유사한 문서 + 그래프로 연결된 CL/Issue/TC 를 함께 반환 (이슈 분석 use case).",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "doc_types": {"type": "array", "items": {"type": "string"}, "default": ["issue", "cl"]},
                                                      "k": {"type": "integer", "default": 8}}, "required": ["text"]}},
    {"name": "wiki_doc", "description": "문서 전문 + 정규화 메타(front matter) + 연결 노드. doc_id(경로) 또는 문서 ID(ISSUE-2041, CL-55321) 로 조회.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "max_chars": {"type": "integer", "default": 20000}}, "required": ["id"]}},
    {"name": "wiki_entity", "description": "지식 그래프 엔티티 상세(관계·provenance, 문서 참조, 근거 문단).",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "wiki_propose", "description": "분석 결과·정정·새 관계·코퍼스 갭을 자가진화 제안으로 기록한다 (색인을 직접 바꾸지 않음, 사람 승인 HITL). "
                                            "kind: synonym | alias | entity | relation | wiki_note | query_rule | corpus_gap",
     "inputSchema": {"type": "object", "properties": {"kind": {"type": "string"}, "payload": {"type": "object"}, "reason": {"type": "string"},
                                                      "confidence": {"type": "number", "default": 0.7}}, "required": ["kind", "payload"]}},
    {"name": "wiki_status", "description": "색인 통계와 프로바이더 상태.", "inputSchema": {"type": "object", "properties": {}}},
]


def _text(s: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _query_with(pipe, question: str, k: Optional[int], mode: str, doc_types: Optional[List[str]], preset: Optional[str]):
    from .config import Settings
    saved = pipe.s.to_dict()
    prev = None
    try:
        if k:
            pipe.s.top_k_final = int(k)
        names = []
        if preset:
            names.append(preset)
        if mode == "deep":
            names.append("deep_research")
        elif mode == "fast":
            names.append("speed")
        if names:
            from . import presets as _presets
            prev = _presets.apply(pipe.s, names, save=False)["prev"]
            pipe.reload_tuning(from_file=False)
        if doc_types:
            from . import tuning as _tn
            _tn.T.values["doc_type_boost"] = ",".join("%s:1.3" % d for d in doc_types)
        res, tr = pipe.query(question, log=True)
    finally:
        if prev is not None:
            from . import presets as _presets
            _presets.restore(pipe.s, prev)
        ns = Settings.from_dict(saved)
        for kk, vv in ns.to_dict().items():
            if kk != "toggles":
                setattr(pipe.s, kk, vv)
        pipe.s.toggles = ns.toggles
        from . import tuning as _tn
        _tn.T.values.pop("doc_type_boost", None)
        pipe.reload_tuning(from_file=False)
    return res, tr


def call_tool(pipe, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name == "wiki_query":
        res, _ = _query_with(pipe, str(args.get("question", "")), args.get("k"), str(args.get("mode") or "normal"), args.get("doc_types"), args.get("preset"))
        lines = [res["answer"], ""]
        ev = res.get("evidence") or {}
        lines.append("판정: %s · groundedness: %s · 모드: %s · fallback: %d회" % (ev.get("verdict", "-"), res.get("groundedness"), res.get("answer_mode"), len(res.get("fallback") or [])))
        lines.append("근거:")
        for h in res["hits"]:
            if h.get("in_context"):
                lines.append("[C%s] %s (%s %s %s) | %s: %s" % (h["n"], h["doc_id"], h.get("doc_type") or "-", h.get("ext_id") or "", h.get("date") or "", h["heading"][:60],
                                                             h["text"][:200].replace("\n", " ")))
        lines.append("request_id: %s" % res.get("request_id"))
        return _text("\n".join(lines))
    if name == "wiki_related":
        text = str(args.get("text", ""))
        types = args.get("doc_types") or ["issue", "cl"]
        k = int(args.get("k") or 8)
        from .profiler import Profiler
        from .retrieval import fts_search, vector_search
        from .textutil import keywords
        prof = Profiler("search", log=False)
        q = " ".join(keywords(text)[:20]) or text[:200]
        rows = [(c, s_) for c, s_, _ in fts_search(pipe.store, q, k * 2, pipe.store.synonyms(), prof)]
        rows += vector_search(pipe.store, pipe.embedder, text[:2000], k * 2, prof)
        meta = pipe.store.doc_meta_map()
        seen: Dict[str, float] = {}
        for cid, s_ in rows:
            doc = cid.rsplit("#", 1)[0]
            dm = meta.get(doc) or {}
            if types and dm.get("doc_type") not in types:
                continue
            seen[doc] = seen.get(doc, 0.0) + 1.0
        docs = sorted(seen.items(), key=lambda kv: -kv[1])[:k]
        lines = ["유사 문서 (%s):" % ", ".join(types)]
        from .graph_rules import entity_id_for
        for doc, sc in docs:
            dm = meta.get(doc) or {}
            title = next((d["title"] for d in pipe.store.list_docs() if d["doc_id"] == doc), doc)
            lines.append("- %s [%s %s %s] %s" % (doc, dm.get("doc_type"), dm.get("ext_id") or "", dm.get("date") or "", title[:60]))
            if dm.get("ext_id"):
                for r in pipe.store.relations_of(entity_id_for(dm["ext_id"]))[:8]:
                    if r["rel"] in ("co_occurs", "mentions", "mentions_date", "mentions_amount"):
                        continue
                    other = r["dst"] if r["src"] == entity_id_for(dm["ext_id"]) else r["src"]
                    oe = pipe.store.get_entity(other) or {}
                    lines.append("    ↳ %s %s (%s, %s)" % (r["rel"], oe.get("name", other), r.get("provenance"), ", ".join(pipe.store.docs_by_ext_id(oe.get("name", ""))[:2])))
        pipe.store.log_request("search", "related: %s" % q[:80], prof.finish(), None, {"doc_types": types}, keep=pipe.s.keep_requests)
        return _text("\n".join(lines))
    if name == "wiki_doc":
        ident = str(args.get("id", ""))
        mx = int(args.get("max_chars") or 20000)
        docs = pipe.store.docs_by_ext_id(ident.upper()) or [d["doc_id"] for d in pipe.store.list_docs() if ident in d["doc_id"]]
        if not docs:
            return _text("not found: %s" % ident)
        doc_id = docs[0]
        chunks = pipe.store.all_chunks(doc_id)
        dm = pipe.store.get_doc_meta(doc_id) or {}
        text = "\n\n".join(c["text"] for c in chunks)
        lines = ["# %s" % doc_id, "meta: %s" % json.dumps({k: dm.get(k) for k in ("doc_type", "ext_id", "date", "status", "tags", "modules", "related", "hw_rev")}, ensure_ascii=False),
                 "chunks: %d" % len(chunks), "", text[:mx]]
        if dm.get("ext_id"):
            from .graph_rules import entity_id_for
            rels = [r for r in pipe.store.relations_of(entity_id_for(dm["ext_id"])) if r["rel"] not in ("co_occurs", "mentions", "mentions_date", "mentions_amount")][:15]
            if rels:
                lines += ["", "관계:"] + ["- %s -[%s]-> %s (%s)" % ((pipe.store.get_entity(r["src"]) or {}).get("name", r["src"]), r["rel"],
                                                                     (pipe.store.get_entity(r["dst"]) or {}).get("name", r["dst"]), r.get("provenance")) for r in rels]
        return _text("\n".join(lines))
    if name == "wiki_propose":
        kind = str(args.get("kind", ""))
        if kind not in ("synonym", "alias", "entity", "relation", "wiki_note", "query_rule", "corpus_gap"):
            return {"content": [{"type": "text", "text": "unsupported kind %s" % kind}], "isError": True}
        pid = pipe.store.add_proposal(kind, dict(args.get("payload") or {}), str(args.get("reason") or "mcp"), float(args.get("confidence") or 0.7), "mcp")
        return _text(json.dumps({"proposal_id": pid, "status": "proposed", "note": "사람 승인 필요: evolve apply %d" % pid}, ensure_ascii=False))
    if name == "wiki_search":
        from .profiler import Profiler
        from .retrieval import fts_search, vector_search, graph_search
        ch, q, k = args.get("channel", "fts"), str(args.get("query", "")), int(args.get("k") or 8)
        prof = Profiler("search")
        if ch == "fts":
            out: Any = [{"chunk_id": c, "score": round(s, 3), "snippet": sn} for c, s, sn in fts_search(pipe.store, q, k, pipe.store.synonyms(), prof)]
        elif ch == "vector":
            out = [{"chunk_id": c, "score": round(s, 3)} for c, s in vector_search(pipe.store, pipe.embedder, q, k, prof)]
        else:
            g = graph_search(pipe.store, q, k, pipe.s.graph_hops, prof)
            out = {"chunks": g["chunks"], "seeds": g.get("seeds"), "entities": g["entities"][:10], "relations": g["relations"][:10]}
        return _text(json.dumps(out, ensure_ascii=False, indent=1))
    if name == "wiki_entity":
        from .graph_rules import entity_id_for
        nm = str(args.get("name", ""))
        d = pipe.entity_detail(nm if nm.startswith("e:") else entity_id_for(nm))
        if not d:
            hits = pipe.store.entity_fts('"%s"' % nm.replace('"', ""), 5)
            return _text("not found. candidates: %s" % [pipe.store.get_entity(e)["name"] for e, _ in hits])
        e = d["entity"]
        lines = ["%s (%s) degree=%s docs=%s" % (e["name"], e["type"], e["degree"], e.get("n_docs")), e.get("description") or "", "문서 참조:"]
        lines += ["- %s (언급 %s, 첫 청크 %s)" % (r["doc_id"], r["mentions"], r.get("first_chunk")) for r in e.get("doc_refs", [])[:15]]
        lines += ["관계:"] + ["- %s -[%s]-> %s (%s conf=%.2f %s)" % (r["src_name"], r["rel"], r["dst_name"], r.get("provenance") or "?", float(r.get("confidence") or 0), r.get("chunk_id") or "")
                            for r in d["relations"][:25]]
        return _text("\n".join(lines))
    if name == "wiki_status":
        return _text(json.dumps({"stats": pipe.store.stats(), "providers": {k: v for k, v in pipe.provider_status().items() if k not in ("catalog", "agents")},
                                 "doc_types": pipe.store.doc_type_counts(), "provenance": pipe.store.provenance_counts()},
                                ensure_ascii=False, indent=1, default=str))
    return {"content": [{"type": "text", "text": "unknown tool %s" % name}], "isError": True}


def handle(pipe, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                                                        "capabilities": {"tools": {}},
                                                        "serverInfo": {"name": "llmwiki", "version": "0.3.0"}}}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            res = call_tool(pipe, params.get("name", ""), params.get("arguments") or {})
        except Exception as e:  # 도구 오류는 isError 로
            res = {"content": [{"type": "text", "text": "error: %s" % e}], "isError": True}
        return {"jsonrpc": "2.0", "id": mid, "result": res}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}}


def _read_message(stream) -> Optional[Dict[str, Any]]:
    """Content-Length 프레이밍 또는 줄 단위 JSON 둘 다 허용."""
    line = stream.readline()
    if not line:
        return None
    if line.lower().startswith("content-length:"):
        n = int(line.split(":", 1)[1].strip())
        while True:
            l2 = stream.readline()
            if not l2 or l2.strip() == "":
                break
        body = stream.read(n)
        return json.loads(body)
    line = line.strip()
    if not line:
        return {}
    return json.loads(line)


def serve_stdio(pipe) -> None:
    inp = sys.stdin
    out = sys.stdout
    if hasattr(out, "reconfigure"):
        try:
            out.reconfigure(encoding="utf-8")
        except Exception:
            pass
    while True:
        try:
            msg = _read_message(inp)
        except Exception as e:
            sys.stderr.write("mcp parse error: %s\n" % e)
            continue
        if msg is None:
            break
        if not msg:
            continue
        resp = handle(pipe, msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()
