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
# 클라이언트가 요청한 버전이 이 안에 있으면 그대로 돌려준다(협상). 없으면 우리 최신을 돌려주고 클라이언트가 판단한다.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "llmwiki", "version": "0.5.0"}

# 도구 힌트 (MCP annotations) — 붙는 LLM 이 "이 도구가 무엇을 바꾸는가" 를 스스로 판단한다.
#   readOnlyHint   : 색인·설정을 바꾸지 않음
#   destructiveHint: 되돌릴 수 없는 변경 (우리 도구에는 없음 — 제안은 사람 승인 전까지 큐에만 쌓인다)
#   idempotentHint : 같은 인자로 다시 불러도 같은 상태
#   openWorldHint  : 외부 시스템(다른 RAG)에 나간다
ANNOTATIONS: Dict[str, Dict[str, Any]] = {
    "wiki_query": {"title": "위키에 질문", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "wiki_search": {"title": "채널 검색 디버그", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "wiki_related": {"title": "유사 문서·연결", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "wiki_doc": {"title": "문서 전문", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "wiki_entity": {"title": "엔티티 상세", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "wiki_propose": {"title": "제안 등록 (사람 승인 필요)", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    "wiki_feedback": {"title": "답변 피드백", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    "wiki_forensic": {"title": "기대 결과 포렌식", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "wiki_status": {"title": "색인·프로바이더 상태", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "wiki_analysis": {"title": "상세 분석 리포트", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "wiki_sources": {"title": "붙어 있는 외부 소스", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": True},
    "wiki_external_search": {"title": "외부 RAG 직접 검색", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": True},
}

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


def _annotated(t: Dict[str, Any]) -> Dict[str, Any]:
    """built-in 도구에 MCP annotations 를 붙인다 (플러그인은 자기 spec 의 것을 그대로 쓴다)."""
    a = ANNOTATIONS.get(t["name"])
    if not a or t.get("annotations"):
        return t
    out = dict(t)
    out["title"] = a.get("title") or t["name"]
    out["annotations"] = {k: v for k, v in a.items() if k != "title"}
    out["annotations"]["title"] = out["title"]
    return out


def list_tools(pipe, federate: bool = True) -> List[Dict[str, Any]]:
    """tools/list = built-in + 플러그인 + (federate 이면) 페더레이션."""
    s = getattr(pipe, "s", None)
    load_plugins(s)
    tools = [_annotated(t) for t in TOOLS] + [sp for sp, _ in _PLUGIN_TOOLS.values()]
    if federate and federation_allowed():
        try:
            tools += [{k: v for k, v in t.items() if not k.startswith("_")} for t in federated_tools(s)]
        except Exception as e:      # 페더레이션이 죽어도 우리 도구는 계속 보여야 한다 (원인은 wiki_sources 로 확인)
            _FED_CACHE.setdefault("_list", {})["error"] = str(e)[:200]
    # 이름 중복은 클라이언트가 어느 것을 부를지 알 수 없다 — 뒤에 온 것을 버리고 알린다
    seen: Dict[str, int] = {}
    out = []
    for t in tools:
        n = str(t.get("name") or "")
        if not n or n in seen:
            _FED_CACHE.setdefault("_list", {})["duplicate"] = n
            continue
        seen[n] = 1
        out.append(t)
    return out


def validate_args(spec: Dict[str, Any], args: Dict[str, Any]) -> Optional[str]:
    """inputSchema 의 required / type / enum 만 확인한다 (전체 JSON Schema 검증이 아니라, LLM 이 자주 틀리는 것만).

    빈 질문으로 wiki_query 를 부르면 예전에는 조용히 빈 결과가 나왔다 — 붙는 LLM 이 왜 실패했는지 알 수 없었다.
    """
    sch = spec.get("inputSchema") or {}
    props = sch.get("properties") or {}
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
    for req in sch.get("required") or []:
        v = args.get(req)
        if v is None or (isinstance(v, str) and not v.strip()):
            return "필수 인자 '%s' 가 없습니다. inputSchema: %s" % (req, json.dumps(sch, ensure_ascii=False)[:400])
    for key, val in (args or {}).items():
        p = props.get(key)
        if not isinstance(p, dict) or val is None:
            continue
        want = types.get(str(p.get("type") or ""))
        if want and not isinstance(val, want):
            if want is not bool and isinstance(val, bool):
                return "인자 '%s' 는 %s 여야 합니다 (받은 값: %r)" % (key, p.get("type"), val)
            if want in (int, (int, float)) and isinstance(val, str) and val.strip().lstrip("-").replace(".", "", 1).isdigit():
                continue          # "8" 처럼 문자열로 보내는 클라이언트는 받아 준다 (아래에서 숫자로 캐스팅)
            if not isinstance(val, want):
                return "인자 '%s' 는 %s 여야 합니다 (받은 값: %r)" % (key, p.get("type"), val)
        if p.get("enum") and val not in p["enum"]:
            return "인자 '%s' 는 %s 중 하나여야 합니다 (받은 값: %r)" % (key, p["enum"], val)
    return None


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
        bad = validate_args(_PLUGIN_TOOLS[name][0], dict(args or {}))
        if bad:
            return _err("invalid arguments for %s: %s" % (name, bad))
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
    """요청 범위(설정 사본·튜닝 오버레이) 안에서 질의 — 다른 클라이언트의 동시 호출과 격리된다."""
    names = [preset] if preset else []
    ov = {"top_k_final": int(k)} if k else None
    with pipe.request_scope(overrides=ov, presets=names, mode=mode or ""):
        if doc_types:
            from . import tuning as _tn
            _tn.T.values["doc_type_boost"] = ",".join("%s:1.3" % d for d in doc_types)   # 오버레이에만 기록
        res, tr = pipe.query(question, log=True)
    return res, tr


def _err(msg: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def call_tool(pipe, name: str, args: Dict[str, Any], federate: bool = True) -> Dict[str, Any]:
    args = dict(args or {})
    spec = next((t for t in TOOLS if t["name"] == name), None)
    if spec is not None:
        bad = validate_args(spec, args)
        if bad:
            return _err("invalid arguments for %s: %s" % (name, bad))
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
        fed = federated_tools(pipe.s, refresh=bool(args.get("check")))
        if args.get("check"):
            st = {r["name"]: r for r in _mc.test_sources(pipe.s, [r["name"] for r in rows if r["enabled"]])}
            for r in rows:
                r["status"] = st.get(r["name"])
        info = {"sources": rows, "external_rag": bool(pipe.s.toggles.external_rag), "mcp_federation": bool(getattr(pipe.s.toggles, "mcp_federation", False)),
                "mcp_sources": bool(pipe.s.toggles.mcp_sources), "plugins": load_plugins(pipe.s),
                "federated_tools": [t["name"] for t in fed],
                "federation_errors": {k: v.get("error") for k, v in _FED_CACHE.items() if isinstance(v, dict) and v.get("error")}}
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
    return _err("unknown tool %s — 사용 가능: %s" % (name, ", ".join(t["name"] for t in list_tools(pipe, federate=federate))))


def handle(pipe, msg: Dict[str, Any], federate: bool = True) -> Optional[Dict[str, Any]]:
    """JSON-RPC 메시지 하나 → 응답(알림이면 None). stdio·HTTP 공용. federate=False 는 페더레이션 하위 호출(재귀 방지)."""
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        want = str(params.get("protocolVersion") or "")
        # 우리가 지원하는 버전이면 그대로, 아니면 우리 최신을 돌려준다 (클라이언트가 계속할지 판단한다 — 스펙 권고)
        agreed = want if want in SUPPORTED_PROTOCOLS else PROTOCOL_VERSION
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": agreed,
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
    # capabilities 에 선언하지 않았지만 그냥 부르는 클라이언트가 있다 — 빈 목록으로 답해 준다 (오류로 멈추지 않게)
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"resources": []}}
    if method == "resources/templates/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"resourceTemplates": []}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"prompts": []}}
    if method == "logging/setLevel":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
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
def _scrub_surrogates(v: Any, depth: int = 0) -> Any:
    """JSON-RPC 본문에서 UTF-8 로 인코딩할 수 없는 문자열(짝 없는 서러게이트)을 걸러 낸다."""
    if depth > 12:
        return v
    if isinstance(v, str):
        try:
            v.encode("utf-8")
            return v
        except UnicodeEncodeError:
            return v.encode("utf-8", "replace").decode("utf-8", "replace")
    if isinstance(v, dict):
        return {_scrub_surrogates(k, depth + 1): _scrub_surrogates(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [_scrub_surrogates(x, depth + 1) for x in v]
    return v


def handle_http(pipe, method: str, body: bytes, headers: Any, session_id: Optional[str] = None) -> Tuple[int, Dict[str, str], bytes]:
    """POST /mcp 본문(JSON-RPC 단건/배열) → (status, headers, body). GET → 405(SSE 스트림 미제공), DELETE → 200.
    인증·권한은 호출자(Web 서버)가 이미 끝냈다 (read 등급)."""
    if method == "GET":
        return 405, {"Allow": "POST, DELETE", "Content-Type": "application/json"}, json.dumps({"error": "SSE stream not supported; use POST (JSON responses)"}).encode("utf-8")
    if method == "DELETE":
        return 200, {"Content-Type": "application/json"}, b"{}"
    try:
        # errors="replace": JSON 은 짝 없는 서러게이트를 표현할 수 있지만 파이썬은 그것을 UTF-8 로
        # 인코딩하지 못한다. 안으로 들여보내면 SQLite 기록·응답 직렬화가 전부 실패한다.
        data = _scrub_surrogates(json.loads(body.decode("utf-8", "replace") or "null"))
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


# ---------------------------------------------------------------- 자가 점검 (bring-up)
def doctor(pipe, check_sources: bool = False) -> Dict[str, Any]:
    """MCP 설정을 한 번에 점검한다 — `python -m llmwiki mcp --doctor`.

    붙이려는 LLM 이 여럿이고 외부 RAG 까지 얹는 환경에서, "무엇이 안 붙는가" 를 서버 쪽에서 먼저 답하기 위한 것.
    확인: 도구 목록과 스키마 · 플러그인 적재 · 외부 소스 선언과 연결 · 페더레이션 이름 · 인증(누가 붙을 수 있는가) · 전송 설정.
    """
    from . import mcp_client as _mc
    s = pipe.s
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, hint: str = "", warn: bool = False) -> None:
        checks.append({"check": name, "ok": bool(ok), "level": "warn" if (warn and not ok) else ("ok" if ok else "error"),
                       "detail": detail, "hint": hint})

    tools = list_tools(pipe)
    builtin = [t["name"] for t in TOOLS]
    add("도구 목록", len(tools) >= len(builtin), "%d개 (built-in %d)" % (len(tools), len(builtin)))
    bad_schema = [t["name"] for t in tools if not isinstance(t.get("inputSchema"), dict) or t["inputSchema"].get("type") != "object"]
    add("inputSchema", not bad_schema, "모든 도구가 object 스키마" if not bad_schema else "스키마 이상: %s" % bad_schema,
        "플러그인 spec 의 inputSchema 를 {\"type\":\"object\",\"properties\":{…}} 형태로 고치세요")
    no_desc = [t["name"] for t in tools if not str(t.get("description") or "").strip()]
    add("도구 설명", not no_desc, "모두 있음" if not no_desc else "설명 없음: %s" % no_desc,
        "설명이 없으면 붙는 LLM 이 그 도구를 고르지 못합니다", warn=True)

    pl = load_plugins(s, force=True)
    add("플러그인", not pl["errors"], "%s — 파일 %d개, 도구 %s" % (pl["dir"], len(pl["files"]), pl["tools"] or "없음"),
        "오류: %s" % pl["errors"] if pl["errors"] else "")

    srcs = _mc.load_sources()
    on = {k: v for k, v in srcs.items() if v.get("enabled")}
    add("외부 소스 선언", True, "%d개 선언, %d개 enabled (%s)" % (len(srcs), len(on), ", ".join(on) or "-"))
    for name, cfg in on.items():
        tr = str(cfg.get("transport") or "stdio")
        target = cfg.get("url") or cfg.get("base_url") or " ".join(str(x) for x in (cfg.get("command") or []))
        ok = bool(target)
        add("소스 %s 설정" % name, ok, "%s → %s" % (tr, target or "(대상 없음)"),
            "transport 가 stdio 면 command, http 면 url, rest 면 base_url 이 필요합니다")
    if check_sources:
        for r in _mc.test_sources(s, list(on)):
            add("소스 %s 연결" % r["name"], r["ok"], "%s %sms tools=%s" % (r["transport"], r["ms"], r.get("tools") or r.get("error", "")),
                "`python -m llmwiki mcp-source test %s` 로 재현" % r["name"])

    retr = [n for n, c in srcs.items() if c.get("enabled") and c.get("retrieve")]
    add("검색 채널(external_rag)", not retr or bool(s.toggles.external_rag),
        "retrieve 소스 %s · 토글 external_rag=%s" % (retr or "없음", s.toggles.external_rag),
        "retrieve 매핑이 있는데 토글이 꺼져 있으면 질의에 섞이지 않습니다 (`config set toggles.external_rag=true`)", warn=True)

    exposed = [n for n, c in srcs.items() if c.get("enabled") and c.get("expose")]
    fed = federated_tools(s, refresh=True) if s.toggles.mcp_federation else []
    fed_err = {k: v.get("error") for k, v in _FED_CACHE.items() if isinstance(v, dict) and v.get("error")}
    add("페더레이션", not exposed or bool(s.toggles.mcp_federation),
        "expose 소스 %s · 토글 mcp_federation=%s · 중계 도구 %s" % (exposed or "없음", s.toggles.mcp_federation, [t["name"] for t in fed] or "없음"),
        "expose 가 선언됐는데 토글이 꺼져 있으면 tools/list 에 나오지 않습니다", warn=True)
    add("페더레이션 연결", not fed_err, "오류 없음" if not fed_err else json.dumps(fed_err, ensure_ascii=False),
        "소스가 살아 있는지 `mcp-source test <name>` 로 확인하세요")
    add("재귀 방지", True, "이 프로세스는 %s" % ("최상위 (페더레이션 가능)" if federation_allowed() else "페더레이션 하위 (자기 페더레이션 안 함)"))

    try:
        from . import auth as _auth
        sec = _auth.load_security()
        anon = sec.get("anonymous_role") or ""
        nkeys = len(sec.get("api_keys") or [])
        add("인증", bool(anon or nkeys or sec.get("users")),
            "anonymous_role=%s · API 키 %d개 · 계정 %d개" % (anon or "(없음)", nkeys, len(sec.get("users") or [])),
            "원격 LLM 은 Authorization: Bearer <API 키> 로 붙습니다 — `python -m llmwiki apikey add <이름> --role class2`")
        if not anon and not nkeys:
            add("원격 접속 수단", False, "익명 역할도 API 키도 없음 — HTTP 로는 아무도 붙을 수 없습니다",
                "`apikey add` 로 키를 만들거나 security.json 의 anonymous_role 을 정하세요", warn=True)
    except Exception as e:
        add("인증", False, "security 설정을 읽지 못함: %s" % e, "", warn=True)

    add("전송", True, "stdio: `python -m llmwiki mcp` · http: serve 의 POST /mcp (mcp_host=%s mcp_port=%s) · 브리지: mcp --connect <url>"
        % (getattr(s, "mcp_host", "-"), getattr(s, "mcp_port", "-")))

    errors = [c for c in checks if c["level"] == "error"]
    warns = [c for c in checks if c["level"] == "warn"]
    return {"ok": not errors, "errors": len(errors), "warnings": len(warns), "checks": checks,
            "tools": [t["name"] for t in tools], "protocol": PROTOCOL_VERSION, "supported_protocols": list(SUPPORTED_PROTOCOLS)}


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
