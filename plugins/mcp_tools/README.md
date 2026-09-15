# plugins/mcp_tools — MCP 플러그인 도구 폴더

`python -m llmwiki mcp` / `serve` 의 MCP 서버(`/mcp`)가 이 폴더의 `*.py` 를 읽어 도구를 추가한다. 코드(llmwiki/) 를 고치지 않고 도구를 늘리는 방법이다.

- 위치 변경: `config.json` → `mcp_plugins_dir` (기본 `plugins/mcp_tools`).
- 예시: `_example_echo.py` — 앞의 밑줄을 지워 `example_echo.py` 로 바꾸면 `echo`, `doc_type_counts` 도구가 `tools/list` 에 나타난다.
- 확인: `python -m llmwiki mcp-source federated` (plugins.tools / plugins.errors), MCP 도구 `wiki_sources`, Web › Corpus › MCP 소스 › "페더레이션 도구 목록".
- 규칙과 handler 시그니처는 `_example_echo.py` 상단 주석과 [docs/RAG_FEDERATION.md](../../docs/RAG_FEDERATION.md) §3.
