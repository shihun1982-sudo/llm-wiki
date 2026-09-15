# -*- coding: utf-8 -*-
"""MCP 플러그인 도구 예시 — 이 파일 이름의 앞 밑줄(_)을 지우면 활성화된다 (밑줄로 시작하는 파일은 로더가 건너뛴다).

규칙 (docs/RAG_FEDERATION.md §3):
- 파일: <config.json mcp_plugins_dir>/<이름>.py  (기본 plugins/mcp_tools/). 표준 라이브러리만 쓰면 폴더 복사만으로 이식된다.
- 반드시 register(add_tool) 함수를 둔다. add_tool(spec, handler) 를 도구마다 한 번 부른다.
    spec    = {"name": "도구이름", "description": "LLM 이 읽는 설명", "inputSchema": {JSON Schema}}
    handler = def f(pipe, args) -> str | dict
              · str 을 돌려주면 text content 로 감싸진다
              · dict 를 돌려주면 JSON 텍스트 + structuredContent 로 보낸다 (이미 {"content": [...]} 형식이면 그대로)
              · 예외를 던지면 MCP isError 로 바뀐다
- pipe 는 서버의 Pipeline (pipe.store, pipe.s(설정), pipe.query(), pipe.embedder …). 색인을 바꾸는 작업은 넣지 않는다
  (MCP 도구는 모두 read 등급으로 권한 검사되므로 viewer 도 부를 수 있다).
- 이름은 built-in(wiki_*) 과 겹치면 안 되고 '__' 를 포함할 수 없다(페더레이션 구분자).
- 파일을 고치면 다음 tools/list 때 자동으로 다시 읽는다 (mtime 감시). 오류는 `mcp-source federated` / wiki_sources 의 plugins.errors 에 나온다.
"""


def _echo(pipe, args):
    text = str(args.get("text") or "")
    return {"echo": text, "chars": len(text), "docs_in_index": pipe.store.stats().get("docs")}


def _count_docs_by_type(pipe, args):
    """예: 색인 통계를 도구로 노출 (pipe.store 읽기 전용 사용)."""
    return {"doc_types": pipe.store.doc_type_counts()}


def register(add_tool):
    add_tool({"name": "echo", "description": "입력 텍스트를 그대로 돌려주는 예시 도구 (플러그인 배선 확인용).",
              "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}, _echo)
    add_tool({"name": "doc_type_counts", "description": "색인의 문서 유형별 문서 수.", "inputSchema": {"type": "object", "properties": {}}}, _count_docs_by_type)
