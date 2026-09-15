# -*- coding: utf-8 -*-
"""MCP(Model Context Protocol) 서버 — 표준 라이브러리만 사용. 전송 두 가지 + 브리지.

  (1) stdio      : python -m llmwiki mcp                       — 같은 PC 의 MCP 클라이언트(Claude Desktop/Code, Cursor, opencode …)가 자식 프로세스로 실행.
                   stdin/stdout JSON-RPC 2.0 (한 줄에 하나 또는 Content-Length 프레이밍).
  (2) HTTP       : python -m llmwiki serve  → POST /mcp        — Web 서버와 같은 포트. 또는 python -m llmwiki mcp --transport http --port 8766 (단독).
                   MCP Streamable HTTP(JSON 응답 모드): 요청 본문 = JSON-RPC 단건 또는 배열, 응답 = JSON. initialize 응답에 Mcp-Session-Id 헤더.
                   인증: Authorization: Bearer <API 키>(security.json api_keys, `apikey add`) 또는 세션 쿠키. anonymous_role 이 있으면 토큰 없이 읽기 가능.
                   여러 외부 LLM 이 동시에 붙어도 서버 스레드가 요청마다 생기고 파이프라인 접근은 락으로 직렬화된다.
  (3) 브리지     : python -m llmwiki mcp --connect http://host:8765/mcp --token lwk_…  — stdio 전용 클라이언트가 원격 서버를 쓸 때.
                   stdin 의 JSON-RPC 를 HTTP 로 넘기고 응답을 stdout 으로 되돌린다 (Windows/Linux 모두 Python 만 있으면 됨).

도구(모두 색인을 바꾸지 않음 = read 등급): wiki_query · wiki_search · wiki_related · wiki_doc · wiki_entity · wiki_propose(HITL 제안) · wiki_feedback ·
wiki_forensic(기대 결과 포렌식) · wiki_status · wiki_sources(붙어 있는 외부 RAG/소스) · wiki_external_search(외부 소스 직접 검색)

확장 (docs/RAG_FEDERATION.md):
  - 플러그인 도구: <mcp_plugins_dir>/*.py 의 register(add_tool) 가 도구를 등록 — 코드 수정 없이 도구 추가 (built-in 과 같은 tools/list·tools/call 경로).
  - 페더레이션: mcp_sources.json 의 소스에 expose 를 두면 그 서버의 tool 이 `<source>__<tool>` 로 우리 tools/list 에 나타나고 호출은 그대로 중계된다
    (토글 mcp_federation). 외부 LLM 은 우리 /mcp 하나만 붙이면 여러 RAG 를 쓴다.
  - 외부 RAG 를 검색 채널로: mcp_sources.json retrieve 매핑 + 토글 external_rag (query_engine 이 ext_<source> 채널로 융합).
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "llmwiki", "version": "0.5.0"}

TOOLS: List[Dict[str, Any]] = [
    {"name": "wiki_query", "description": "사내 LLM Wiki 에 질문하고 인용([C#]) 이 붙은 구조화 답변·근거 문단·근거 판정(groundedness)을 받는다 (FTS+Vector+Graph 하이브리드). "
                                          "mode=deep 은 확장·분해·fallback 을 최대로 한 심층 조사(unified search). 결과의 request_id 로 wiki_forensic 을 부를 수 있다.",
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
                                            "kind: synonym | alias | entity | relation | wiki_note | query_rule | corpus_gap | pin",
     "inputSchema": {"type": "object", "properties": {"kind": {"type": "string"}, "payload": {"type": "object"}, "reason": {"type": "string"},
                                                      "confidence": {"type": "number", "default": 0.7}}, "required": ["kind", "payload"]}},
    {"name": "wiki_feedback", "description": "wiki_query 답변에 대한 피드백 (+1 도움됨 / -1 틀림·부족 + 정정 메모). query_id 는 wiki_query 결과에 표시된다. 부정 피드백+메모는 위키 편집 노트 제안이 된다.",
     "inputSchema": {"type": "object", "properties": {"query_id": {"type": "integer"}, "feedback": {"type": "integer", "enum": [1, -1]}, "note": {"type": "string"}},
                     "required": ["query_id", "feedback"]}},
    {"name": "wiki_forensic", "description": "기대 결과 포렌식: 답변에 있어야 했던 문서(expected_docs: ISSUE-2003 등)·용어(expected_terms)를 주면 같은 설정으로 검색을 재실행해 "
                                             "그 근거가 fts/vector/graph → 융합 → 리랭크 → 컨텍스트 → 답변 중 어느 단계에서 탈락했는지와 수정안(규칙/pin/튜닝/코퍼스)을 돌려준다. "
                                             "request_id 는 wiki_query 결과의 것 (생략하면 마지막 질의).",
     "inputSchema": {"type": "object", "properties": {"request_id": {"type": "integer"}, "expected_docs": {"type": "array", "items": {"type": "string"}},
                                                      "expected_terms": {"type": "array", "items": {"type": "string"}}, "expected_chunks": {"type": "array", "items": {"type": "string"}},
                                                      "note": {"type": "string"}, "propose": {"type": "boolean", "default": False, "description": "수정안을 HITL 제안 큐에 등록"}},
                     "required": []}},
    {"name": "wiki_status", "description": "색인 통계와 프로바이더 상태.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "wiki_analysis", "description": "상세 분석 리포트: 질의 한 건(request_id, 생략=마지막)의 모든 단계 결과·설정 스냅샷·품질/속도/토큰 렌즈 소견과 조절점(토글/튜닝 키)을 마크다운으로 돌려준다. "
                                             "튜닝 제안을 만들 때 이 리포트를 근거로 삼는다. analysis_mode 토글이 켜진 질의는 debug·프롬프트 샘플까지 포함.",
     "inputSchema": {"type": "object", "properties": {"request_id": {"type": "integer"}, "focus": {"type": "string", "enum": ["quality", "speed", "tokens", "all"], "default": "all"}}}},
    {"name": "wiki_sources", "description": "이 서버에 붙어 있는 외부 RAG/데이터 소스(mcp_sources.json) 목록: 이름·전송(stdio/http/rest)·용도(retrieve 채널/ingest/expose)·연결 상태. "
                                            "expose 된 소스의 도구는 `<source>__<tool>` 이름으로 이 서버의 tools/list 에 함께 나온다.",
     "inputSchema": {"type": "object", "properties": {"check": {"type": "boolean", "default": False, "description": "true 면 각 소스에 실제 연결해 상태 확인"}}}},
    {"name": "wiki_external_search", "description": "외부 소스(다른 RAG) 하나 또는 전부에 직접 검색을 보내 결과(id·제목·본문·점수·URL)를 그대로 받는다 (융합 없음). "
                                                    "wiki_query 는 external_rag 토글이 켜져 있으면 이 결과를 fts/vector/graph 와 함께 융합한다.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "source": {"type": "string", "description": "소스 이름 (생략=retrieve 매핑이 있는 모든 소스)"},
                                                      "k": {"type": "integer", "default": 5}}, "required": ["query"]}},
]

# ---------------------------------------------------------------- 확장: 플러그인 도구 레지스트리 · 페더레이션
_PLUGIN_TOOLS: Dict[str, Tuple[Dict[str, Any], Any]] = {}     # name → (spec, handler(pipe, args) -> result dict | str)
_PLUGIN_STATE: Dict[str, Any] = {"dir": None, "sig": None, "errors": [], "files": []}
_FED_CACHE: Dict[str, Any] = {}                                # source → {"ts", "tools"}
FED_SEP = "__"


def register_tool(spec: Dict[str, Any], handler) -> None:
    """플러그인이 부르는 등록 함수. spec = {"name","description","inputSchema"}; handler(pipe, args) → MCP result dict 또는 str."""
    name = str(spec.get("name") or "").strip()
    if not name or FED_SEP in name:
        raise ValueError("tool name required (must not contain '%s'): %r" % (FED_SEP, name))
    if any(t["name"] == name for t in TOOLS):
        raise ValueError("built-in tool name: %s" % name)
    sp = {"name": name, "description": str(spec.get("description") or ""), "inputSchema": spec.get("inputSchema") or {"type": "object", "properties": {}}}
    _PLUGIN_TOOLS[name] = (sp, handler)


def plugins_dir(settings=None) -> str:
    from .config import ROOT, resolve_path
    d = getattr(settings, "mcp_plugins_dir", None) or "plugins/mcp_tools"
    return resolve_path(d) if not os.path.isabs(d) else d


def load_plugins(settings=None, force: bool = False) -> Dict[str, Any]:
    """<mcp_plugins_dir>/*.py (밑줄로 시작하지 않는 파일) 를 import 해 register(register_tool) 를 부른다. 파일 mtime 이 바뀌면 다시 읽는다."""
    import importlib.util
    d = plugins_dir(settings)
    files = sorted(f for f in (os.listdir(d) if os.path.isdir(d) else []) if f.endswith(".py") and not f.startswith("_"))
    sig = tuple((f, os.path.getmtime(os.path.join(d, f))) for f in files)
    if not force and _PLUGIN_STATE["dir"] == d and _PLUGIN_STATE["sig"] == sig:
        return {"dir": d, "tools": list(_PLUGIN_TOOLS), "errors": _PLUGIN_STATE["errors"], "files": files}
    _PLUGIN_TOOLS.clear()
    errors: List[Dict[str, str]] = []
    for f in files:
        path = os.path.join(d, f)
        try:
            spec = importlib.util.spec_from_file_location("llmwiki_mcp_plugin_" + os.path.splitext(f)[0], path)
            mod = importlib.util.module_from_spec(spec)   # type: ignore
            spec.loader.exec_module(mod)                   # type: ignore
            reg = getattr(mod, "register", None)
            if not callable(reg):
                raise RuntimeError("register(add_tool) 함수 없음")
            reg(register_tool)
        except Exception as e:
            errors.append({"file": f, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
    _PLUGIN_STATE.update({"dir": d, "sig": sig, "errors": errors, "files": files})
    return {"dir": d, "tools": list(_PLUGIN_TOOLS), "errors": errors, "files": files}


def _exposed_sources(settings) -> Dict[str, Dict[str, Any]]:
    if settings is None or not getattr(settings.toggles, "mcp_federation", False):
        return {}
    from . import mcp_client as _mc
    return {n: c for n, c in _mc.enabled_sources(settings, ignore_toggle=True).items() if c.get("expose")}


def federated_tools(settings, refresh: bool = False, ttl_s: int = 300) -> List[Dict[str, Any]]:
    """expose 된 소스의 tool 을 `<source>__<tool>` 로. 소스별 tools/list 는 ttl 동안 캐시; 연결 실패는 빈 목록 + _FED_CACHE[...]["error"]."""
    import time as _t
    from . import mcp_client as _mc
    out: List[Dict[str, Any]] = []
    for name, cfg in _exposed_sources(settings).items():
        ent = _FED_CACHE.get(name)
        if refresh or not ent or _t.time() - ent["ts"] > ttl_s:
            try:
                tools = _mc.remote_tools(name, cfg)
                ent = {"ts": _t.time(), "tools": tools, "error": ""}
            except Exception as e:
                ent = {"ts": _t.time(), "tools": [], "error": str(e)[:200]}
            _FED_CACHE[name] = ent
        allow = cfg.get("expose")
        for t in ent["tools"]:
            tn = str(t.get("name") or "")
            if not tn or (isinstance(allow, list) and tn not in allow):
                continue
            out.append({"name": name + FED_SEP + tn, "description": "[%s] %s" % (name, t.get("description") or ""),
                        "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}}, "_source": name})
    return out


FED_HEADER = "X-LLMWiki-Federation-Depth"     # 페더레이션 호출임을 원격에 알린다 → 원격은 자기 페더레이션을 하지 않는다 (A↔B 상호 expose 시 무한 재귀 방지)
FED_ENV = "LLMWIKI_FEDERATION_DEPTH"


def federation_allowed() -> bool:
    """이 프로세스가 다른 llmwiki 의 페더레이션 하위(stdio 자식 또는 헤더 표시)로 실행 중이면 False."""
    try:
        return int(os.environ.get(FED_ENV) or 0) <= 0
    except Exception:
        return True


def list_tools(pipe, federate: bool = True) -> List[Dict[str, Any]]:
    """tools/list = built-in + 플러그인 + (federate 이면) 페더레이션."""
    s = getattr(pipe, "s", None)
    load_plugins(s)
    tools = list(TOOLS) + [sp for sp, _ in _PLUGIN_TOOLS.values()]
    if federate and federation_allowed():
        try:
            tools += [{k: v for k, v in t.items() if not k.startswith("_")} for t in federated_tools(s)]
        except Exception:
            pass
    return tools


def _text(s: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _proxy_result(res: Any) -> Dict[str, Any]:
    """소스 tool 호출 결과(구조화 dict 또는 {"text":…}) → MCP result."""
    if isinstance(res, dict) and set(res.keys()) == {"text"}:
        return _text(str(res["text"]))
    out = _text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    if isinstance(res, dict):
        out["structuredContent"] = res
    return out


def call_extension(pipe, name: str, args: Dict[str, Any], federate: bool = True) -> Optional[Dict[str, Any]]:
    """플러그인/페더레이션 도구면 처리해서 result 를, 아니면 None."""
    s = getattr(pipe, "s", None)
    load_plugins(s)
    if name in _PLUGIN_TOOLS:
        r = _PLUGIN_TOOLS[name][1](pipe, dict(args or {}))
        return _text(str(r)) if not isinstance(r, dict) else (r if "content" in r else _proxy_result(r))
    if FED_SEP in name:
        if not federate or not federation_allowed():
            return {"content": [{"type": "text", "text": "federated tool %s is not available through a federated call (recursion guard)" % name}], "isError": True}
        src, tool = name.split(FED_SEP, 1)
        cfg = _exposed_sources(s).get(src)
        if not cfg:
            return {"content": [{"type": "text", "text": "unknown federated source %s (expose/mcp_federation 확인)" % src}], "isError": True}
        allow = cfg.get("expose")
        if isinstance(allow, list) and tool not in allow:
            return {"content": [{"type": "text", "text": "tool %s is not exposed by source %s" % (tool, src)}], "isError": True}
        from . import mcp_client as _mc
        try:
            return _proxy_result(_mc.call_source_tool(src, cfg, tool, dict(args or {})))
        except Exception as e:
            return {"content": [{"type": "text", "text": "federated call failed (%s): %s" % (name, str(e)[:300])}], "isError": True}
    return None


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


def call_tool(pipe, name: str, args: Dict[str, Any], federate: bool = True) -> Dict[str, Any]:
    ext = call_extension(pipe, name, args, federate=federate)
    if ext is not None:
        return ext
    if name == "wiki_analysis":
        from . import analysis as _an
        focus = str(args.get("focus") or "all")
        r = _an.analyze(pipe, int(args.get("request_id") or 0) or None, focus=None if focus == "all" else focus)
        if r.get("error"):
            return {"content": [{"type": "text", "text": r["error"]}], "isError": True}
        out = _text(r["markdown"])
        out["structuredContent"] = dict(r["summary"], **r["paths"])
        return out
    if name == "wiki_sources":
        from . import mcp_client as _mc
        rows = [_mc.source_summary(n, c) for n, c in _mc.load_sources().items()]
        if args.get("check"):
            st = {r["name"]: r for r in _mc.test_sources(pipe.s, [r["name"] for r in rows if r["enabled"]])}
            for r in rows:
                r["status"] = st.get(r["name"])
        info = {"sources": rows, "external_rag": bool(pipe.s.toggles.external_rag), "mcp_federation": bool(getattr(pipe.s.toggles, "mcp_federation", False)),
                "mcp_sources": bool(pipe.s.toggles.mcp_sources), "plugins": load_plugins(pipe.s)}
        out = _text(json.dumps(info, ensure_ascii=False, indent=1, default=str))
        out["structuredContent"] = info
        return out
    if name == "wiki_external_search":
        from . import mcp_client as _mc
        q = str(args.get("query", ""))
        src = args.get("source")
        rows = _mc.retrieve(pipe.s, q, int(args.get("k") or 5), names=[src] if src else None, include_fallback=True)
        lines = ["외부 검색: %s%s" % (q, (" @ " + src) if src else "")]
        for r in rows:
            if r.get("error"):
                lines.append("- [%s] 오류: %s" % (r["source"], r["error"]))
            else:
                lines.append("- [%s] %s %s (score %.3f%s)\n  %s" % (r["source"], r["id"], r["title"], r["score"], (", " + str(r["url"])) if r.get("url") else "", (r["text"] or "")[:300].replace("\n", " ")))
        out = _text("\n".join(lines))
        out["structuredContent"] = {"results": rows}
        return out
    if name == "wiki_query":
        res, _ = _query_with(pipe, str(args.get("question", "")), args.get("k"), str(args.get("mode") or "normal"), args.get("doc_types"), args.get("preset"))
        lines = [res["answer"], ""]
        ev = res.get("evidence") or {}
        lines.append("판정: %s · groundedness: %s · 모드: %s · fallback: %d회" % (ev.get("verdict", "-"), res.get("groundedness"), res.get("answer_mode"), len(res.get("fallback") or [])))
        if res.get("llm_report"):
            lines.append("LLM 실행 보고: " + " | ".join(res["llm_report"].get("summary") or []))
        lines.append("근거:")
        for h in res["hits"]:
            if h.get("in_context"):
                ext = h.get("external") or {}
                lines.append("[C%s] %s (%s %s %s)%s%s | %s: %s" % (h["n"], h["doc_id"], h.get("doc_type") or "-", h.get("ext_id") or "", h.get("date") or "",
                                                                  " [doc_expand]" if "doc_expand" in (h.get("why") or []) else "",
                                                                  (" [외부 %s%s]" % (ext.get("source"), (" " + str(ext.get("url"))) if ext.get("url") else "")) if ext else "",
                                                                  h["heading"][:60], h["text"][:200].replace("\n", " ")))
        lines.append("request_id: %s · query_id: %s  (wiki_forensic / wiki_feedback 에 사용)" % (res.get("request_id"), res.get("query_id")))
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
        if kind not in ("synonym", "alias", "entity", "relation", "wiki_note", "query_rule", "corpus_gap", "pin"):
            return {"content": [{"type": "text", "text": "unsupported kind %s" % kind}], "isError": True}
        pid = pipe.store.add_proposal(kind, dict(args.get("payload") or {}), str(args.get("reason") or "mcp"), float(args.get("confidence") or 0.7), "mcp")
        return _text(json.dumps({"proposal_id": pid, "status": "proposed", "note": "사람 승인 필요: evolve apply %d" % pid}, ensure_ascii=False))
    if name == "wiki_feedback":
        from . import evolve as _ev
        try:
            r = _ev.record_feedback(pipe, int(args.get("query_id") or 0), int(args.get("feedback") or 0), str(args.get("note") or ""))
        except Exception as e:
            return {"content": [{"type": "text", "text": "feedback error: %s" % e}], "isError": True}
        return _text(json.dumps(r, ensure_ascii=False, default=str))
    if name == "wiki_forensic":
        from . import forensic as _fx
        rid = int(args.get("request_id") or 0)
        if not rid:
            reqs = pipe.store.requests("query", 1)
            rid = int(reqs[0]["id"]) if reqs else 0
        if not rid:
            return {"content": [{"type": "text", "text": "no query request to analyze"}], "isError": True}
        rep = _fx.trace_expectation(pipe, rid, list(args.get("expected_docs") or []), list(args.get("expected_terms") or []), list(args.get("expected_chunks") or []),
                                    note=str(args.get("note") or "mcp"), propose=bool(args.get("propose")))
        out = _text(_fx.format_expectation(rep))
        out["structuredContent"] = {k: v for k, v in rep.items() if k in ("request_id", "query", "summary", "lost_counts", "suggestions", "best_target", "proposals", "forensic_id")}
        return out
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


def handle(pipe, msg: Dict[str, Any], federate: bool = True) -> Optional[Dict[str, Any]]:
    """JSON-RPC 메시지 하나 → 응답(알림이면 None). stdio·HTTP 공용. federate=False 는 페더레이션 하위 호출(재귀 방지)."""
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                                                        "capabilities": {"tools": {"listChanged": False}},
                                                        "serverInfo": SERVER_INFO,
                                                        "instructions": "사내 LLM Wiki. wiki_query 로 질문(인용 [C#] 포함 답변) → 결과가 부족하면 wiki_forensic(request_id, expected_docs/terms) 로 원인 분석, wiki_feedback 으로 피드백, wiki_propose 로 제안."}}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": list_tools(pipe, federate=federate)}}
    if method == "tools/call":
        try:
            res = call_tool(pipe, params.get("name", ""), params.get("arguments") or {}, federate=federate)
        except Exception as e:  # 도구 오류는 isError 로
            res = {"content": [{"type": "text", "text": "error: %s" % e}], "isError": True}
        return {"jsonrpc": "2.0", "id": mid, "result": res}
    if method in ("resources/list", "prompts/list"):
        return {"jsonrpc": "2.0", "id": mid, "result": {"resources": [], "prompts": []}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}}


# ---------------------------------------------------------------- stdio
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
        if isinstance(msg, list):
            resps = [r for r in (handle(pipe, m) for m in msg if isinstance(m, dict)) if r is not None]
            if resps:
                out.write(json.dumps(resps, ensure_ascii=False) + "\n")
                out.flush()
            continue
        resp = handle(pipe, msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()


# ---------------------------------------------------------------- Streamable HTTP (JSON 응답 모드)
def handle_http(pipe, method: str, body: bytes, headers: Any, session_id: Optional[str] = None) -> Tuple[int, Dict[str, str], bytes]:
    """POST /mcp 본문(JSON-RPC 단건/배열) → (status, headers, body). GET → 405(SSE 스트림 미제공), DELETE → 200.
    인증·권한은 호출자(Web 서버)가 이미 끝냈다 (read 등급)."""
    if method == "GET":
        return 405, {"Allow": "POST, DELETE", "Content-Type": "application/json"}, json.dumps({"error": "SSE stream not supported; use POST (JSON responses)"}).encode("utf-8")
    if method == "DELETE":
        return 200, {"Content-Type": "application/json"}, b"{}"
    try:
        data = json.loads(body.decode("utf-8") or "null")
    except Exception as e:
        return 400, {"Content-Type": "application/json"}, json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error: %s" % e}}).encode("utf-8")
    msgs = data if isinstance(data, list) else [data]
    if not msgs or not all(isinstance(m, dict) for m in msgs):
        return 400, {"Content-Type": "application/json"}, json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}).encode("utf-8")
    out_headers = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}
    sid = (headers.get("Mcp-Session-Id") if headers is not None else None) or session_id
    federate = not bool(headers.get(FED_HEADER)) if headers is not None else True
    resps = []
    for m in msgs:
        r = handle(pipe, m, federate=federate)
        if m.get("method") == "initialize":
            out_headers["Mcp-Session-Id"] = sid or secrets.token_hex(12)
        if r is not None:
            resps.append(r)
    if sid and "Mcp-Session-Id" not in out_headers:
        out_headers["Mcp-Session-Id"] = sid
    if not resps:
        return 202, out_headers, b""      # 알림만 있었음
    payload = resps if isinstance(data, list) else resps[0]
    return 200, out_headers, json.dumps(payload, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------- 브리지 (stdio → 원격 HTTP)
def bridge_stdio_to_http(url: str, token: str = "", timeout: int = 600, inp=None, out=None) -> int:
    """stdin 의 JSON-RPC 를 원격 /mcp 로 POST 하고 응답을 stdout 으로. 원격이 4xx/5xx 면 JSON-RPC 오류로 되돌린다."""
    inp = inp or sys.stdin
    out = out or sys.stdout
    if hasattr(out, "reconfigure"):
        try:
            out.reconfigure(encoding="utf-8")
        except Exception:
            pass
    session: Optional[str] = None
    while True:
        try:
            msg = _read_message(inp)
        except Exception as e:
            sys.stderr.write("mcp bridge parse error: %s\n" % e)
            continue
        if msg is None:
            return 0
        if not msg:
            continue
        status, hdrs, resp_body = http_post_mcp(url, msg, token, session, timeout)
        if hdrs.get("Mcp-Session-Id"):
            session = hdrs["Mcp-Session-Id"]
        if status == 202 or not resp_body:
            continue
        if status >= 400:
            mid = msg.get("id") if isinstance(msg, dict) else None
            err = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "remote MCP HTTP %s: %s" % (status, resp_body.decode("utf-8", "ignore")[:300])}}
            out.write(json.dumps(err, ensure_ascii=False) + "\n")
        else:
            out.write(resp_body.decode("utf-8") + "\n")
        out.flush()


def http_post_mcp(url: str, msg: Any, token: str = "", session: Optional[str] = None, timeout: int = 600,
                  headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, str], bytes]:
    hdrs = {"Content-Type": "application/json", "Accept": "application/json", "X-Requested-With": "llmwiki-mcp"}
    hdrs.update(headers or {})
    if token:
        hdrs["Authorization"] = "Bearer " + token
    if session:
        hdrs["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, data=json.dumps(msg, ensure_ascii=False).encode("utf-8"), headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k: v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k: v for k, v in e.headers.items()}, e.read()
    except Exception as e:
        return 599, {}, str(e).encode("utf-8")


def client_config_snippets(base_url: str, token: str = "") -> Dict[str, Any]:
    """문서/Web 보안 탭용: 대표 클라이언트 설정 예시."""
    py = sys.executable   # dict 를 json.dumps 하면 역슬래시는 자동 이스케이프된다 (수동 이중 이스케이프 금지)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mcp_url = base_url.rstrip("/") + ("" if base_url.rstrip("/").endswith("/mcp") else "/mcp")
    return {
        "_how": "stdio_json: 같은 PC 클라이언트(mcp.json/claude_desktop_config.json) · http_json: 원격/다수(serve 의 POST /mcp, API 키) · bridge_json: stdio 전용 클라이언트→원격 · 상세 docs/MCP.md, 예시 setup/mcp_clients.example.json",
        "stdio_claude_code": "claude mcp add llmwiki -- \"%s\" -m llmwiki mcp   (cwd=%s)" % (py, root),
        "stdio_json": {"mcpServers": {"llmwiki": {"command": py, "args": ["-m", "llmwiki", "mcp"], "cwd": root, "env": {"PYTHONIOENCODING": "utf-8"}}}},
        "http_claude_code": "claude mcp add --transport http llmwiki %s --header \"Authorization: Bearer %s\"" % (mcp_url, token or "<API_KEY>"),
        "http_json": {"mcpServers": {"llmwiki": {"type": "http", "url": mcp_url, "headers": {"Authorization": "Bearer " + (token or "<API_KEY>")}}}},
        "bridge_json": {"mcpServers": {"llmwiki-remote": {"command": py, "args": ["-m", "llmwiki", "mcp", "--connect", mcp_url, "--token", token or "<API_KEY>"], "cwd": root}}},
    }
