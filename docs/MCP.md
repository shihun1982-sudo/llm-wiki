# MCP — 외부 LLM(여러 개, 같은 PC 또는 원격) 이 LLM Wiki 를 도구로 쓰기

> 대상: Claude Desktop/Claude Code/Cursor/opencode 같은 MCP 클라이언트나, 자체 에이전트에서 이 위키를 검색·질의 도구로 붙이려는 사람. Windows/Linux, 같은 PC/원격 서버 모두 다룬다. 권한·API 키는 [SECURITY.md](SECURITY.md) §4.4, 설계 배경은 [IMPLEMENTATION_PLAN_0914.md](IMPLEMENTATION_PLAN_0914.md) §2.

## 0. 어떤 방식을 쓰나 (결정표)

| 상황 | 방식 | 설정 한 줄 |
|---|---|---|
| 클라이언트가 **같은 PC**, 1~2개 | **stdio** (클라이언트가 `python -m llmwiki mcp` 를 자식 프로세스로 실행) | `claude mcp add llmwiki -- python -m llmwiki mcp` (cwd=프로젝트 루트) |
| 클라이언트가 **다른 PC** / 외부 LLM **여러 개** / 서버가 이미 `serve` 중 | **Streamable HTTP** — Web 서버가 같은 포트에서 `POST /mcp` 제공, Bearer API 키 | 클라이언트 설정에 `{"type":"http","url":"http://wiki-host:8765/mcp","headers":{"Authorization":"Bearer lwk_…"}}` |
| 원격 서버인데 클라이언트가 **stdio 만 지원** | **브리지** — 로컬에서 `python -m llmwiki mcp --connect URL --token …` 을 stdio 서버처럼 등록 | `claude mcp add llmwiki-remote -- python -m llmwiki mcp --connect http://wiki-host:8765/mcp --token lwk_…` |
| Web UI 없이 MCP 만 열고 싶다 | **단독 HTTP** | `python -m llmwiki mcp --transport http --host 0.0.0.0 --port 8766` |
| **다른 RAG / 검색 API / MCP 서버를 이 서버 뒤에 붙이고** 싶다 (클라이언트는 우리 `/mcp` 하나만) | **페더레이션 + 외부 RAG 채널** — `mcp_sources.json` 에 소스(stdio/http/rest) 선언, 토글 `mcp_federation`(도구 노출) · `external_rag`(검색 채널) | [RAG_FEDERATION.md](RAG_FEDERATION.md) |
| 코드 수정 없이 **도구를 추가**하고 싶다 | **플러그인** `plugins/mcp_tools/<이름>.py` 의 `register(add_tool)` | RAG_FEDERATION.md §3 |

세 방식 모두 같은 도구(§2)와 같은 파이프라인(`Pipeline.query`)을 쓴다. HTTP 는 표준 라이브러리 `http.server` 만 사용한다(추가 패키지 없음). 확장성: 도구 목록은 built-in 11개 + 플러그인 + 페더레이션(`<source>__<tool>`)으로 늘어나며, 모두 같은 `tools/list`·`tools/call`·인증·감사 경로를 탄다.

## 1. 설치·기동

### 1.1 stdio (같은 PC)
```bat
:: Windows — Claude Code
claude mcp add llmwiki -- "C:\Users\me\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m llmwiki mcp
:: 또는 클라이언트의 mcp.json / claude_desktop_config.json
{"mcpServers": {"llmwiki": {"command": "C:\\Users\\me\\...\\python.exe", "args": ["-m", "llmwiki", "mcp"], "cwd": "C:\\project\\llm-wiki-rag-selfevolving"}}}
```
```bash
# macOS / Linux
claude mcp add llmwiki -- python3 -m llmwiki mcp
{"mcpServers": {"llmwiki": {"command": "python3", "args": ["-m", "llmwiki", "mcp"], "cwd": "/srv/llm-wiki-rag-selfevolving"}}}
```
- `cwd` 는 프로젝트 루트(config.json 이 있는 곳). Windows 는 `python.exe` 절대 경로를 적으면 PATH 문제가 없다.
- **자기 환경 값이 채워진 블록을 얻으려면** `python -m llmwiki mcp --client-config [--url http://wiki-host:8765] [--token lwk_…]` — `stdio_json`(위 블록, python 절대 경로·cwd 자동), `http_json`, `bridge_json`, `stdio_claude_code`/`http_claude_code`(한 줄 명령) 을 JSON 으로 출력한다. 클라이언트별 붙여 넣는 위치와 opencode/Codex 형식은 `setup/mcp_clients.example.json`.
- 프로세스마다 SQLite 연결을 하나씩 열며(WAL), 읽기 전용에 가까워 여러 클라이언트가 동시에 붙어도 안전하다. 색인은 별도 `build`/`serve` 가 갱신하고, 각 MCP 프로세스는 `build_version` 변화를 다음 질의에서 감지해 캐시를 다시 적재한다.
- 터미널에서 직접 실행하면 stdin 을 기다리며 멈춘 것처럼 보인다(정상).

### 1.2 Streamable HTTP (원격 · 다수 LLM)
1. 서버: `python -m llmwiki serve --host 0.0.0.0 --port 8765` — Web UI 와 함께 `POST /mcp` 가 열린다. MCP 만 열려면 `python -m llmwiki mcp --transport http --host 0.0.0.0 --port 8766`. 플래그를 생략하면 `config.json` 의 `web_host/web_port`(serve) · `mcp_transport/mcp_host/mcp_port`(mcp) 가 쓰이므로, 서버마다 다른 포트/바인드는 파일에 적어 두고 스케줄러/서비스 등록은 `serve` 한 단어로 한다(환경변수 `LLMWIKI_WEB_HOST` 등도 가능).
2. 키 발급(admin): `python -m llmwiki apikey add claude-desktop-kim --role viewer` → `lwk_…` 토큰(한 번만 표시). 외부 LLM 은 viewer 로 충분하다(도구가 모두 읽기/제안).
3. 클라이언트:
```jsonc
{"mcpServers": {"llmwiki": {"type": "http", "url": "http://wiki-host:8765/mcp", "headers": {"Authorization": "Bearer lwk_3f9a1c2e_…"}}}}
```
   Claude Code: `claude mcp add --transport http llmwiki http://wiki-host:8765/mcp --header "Authorization: Bearer lwk_…"`.
4. 확인(curl):
```bash
curl -s http://wiki-host:8765/mcp -H "Authorization: Bearer lwk_…" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```
- `security.json anonymous_role` 이 `viewer`(기본) 면 토큰 없이도 읽기 도구를 쓸 수 있다(사내망 전용일 때). 로그인을 강제하려면 `""`.
- 프로토콜: MCP Streamable HTTP 의 **JSON 응답 모드**. 요청 본문은 JSON-RPC 2.0 단건 또는 배열, 응답은 JSON. `initialize` 응답에 `Mcp-Session-Id` 헤더를 돌려주고 이후 요청의 헤더를 받아들인다(서버는 세션 상태를 갖지 않으므로 어떤 값이든 허용). 알림만 있으면 202. `GET /mcp` 는 405(서버 푸시 스트림 없음 — 도구가 모두 요청-응답형), `DELETE /mcp` 는 200.
- 인증 실패는 401 + `WWW-Authenticate: Bearer` + JSON-RPC 오류 본문. 모든 거부는 `logs/audit.jsonl` 에 `op=mcp` 로 남는다.
- 동시성: 요청마다 서버 스레드가 생기고 파이프라인 접근은 서버 전역 락으로 직렬화된다. 질의 1건이 수 초이므로 수십 개 LLM 이 동시에 붙어도 큐잉만 된다. 더 큰 처리량은 서버 프로세스를 여러 개(포트 여러 개) 띄우고 프록시로 분배(같은 DB 파일을 읽는다).

### 1.3 브리지 (stdio 전용 클라이언트 → 원격 HTTP)
```bat
claude mcp add llmwiki-remote -- python -m llmwiki mcp --connect http://wiki-host:8765/mcp --token lwk_…
:: 또는 환경변수: LLMWIKI_MCP_URL=http://wiki-host:8765/mcp  LLMWIKI_MCP_TOKEN=lwk_…  → python -m llmwiki mcp
:: 또는 config.json: "mcp_url": "http://wiki-host:8765/mcp"  (토큰은 .env LLMWIKI_MCP_TOKEN 에만)  → python -m llmwiki mcp
```
우선순위: `--connect` > `LLMWIKI_MCP_URL` > `config.json mcp_url`. `mcp_url` 이 비어 있지 않으면 그 폴더의 `mcp` 명령은 항상 브리지로 동작하므로, 색인 DB 없이 설정 파일만 복사한 "클라이언트 전용 폴더"를 만들 때 쓴다.
로컬 Python 이 stdin 의 JSON-RPC 를 원격으로 POST 하고 응답을 stdout 으로 되돌린다. 원격이 4xx/5xx 면 JSON-RPC 오류(`-32000`)로 바꿔 준다. 로컬에는 색인 DB 가 필요 없다(설정 파일만 있으면 됨).

## 2. 도구

| 도구 | 입력 | 돌려주는 것 | 등급 |
|---|---|---|---|
| `wiki_query` | `question`, `k`, `mode=fast|normal|deep`, `doc_types[]`, `preset` | 인용 `[C#]` 답변 + 판정/groundedness + 근거 목록(doc_expand 청크 표시) + **request_id / query_id** + (있으면) LLM 실행 보고 | read |
| `wiki_search` | `channel=fts|vector|graph`, `query`, `k` | 단일 채널 결과 | read |
| `wiki_related` | `text`, `doc_types[]`, `k` | 유사 문서 + 그래프 연결(CL↔Issue↔TC) | read |
| `wiki_doc` | `id`(ISSUE-2041 / doc_id) | 문서 전문 + 메타 + 관계 | read |
| `wiki_entity` | `name` | 엔티티 상세(관계·provenance·문서 참조) | read |
| `wiki_propose` | `kind`, `payload`, `reason`, `confidence` | 자가진화 제안 id (HITL; `pin`/`query_rule` 등) | read |
| `wiki_feedback` | `query_id`, `feedback=+1/-1`, `note` | 피드백 기록(부정+메모 → 위키 노트 제안) | read |
| `wiki_forensic` | `request_id`(생략=마지막), `expected_docs[]`, `expected_terms[]`, `expected_chunks[]`, `note`, `propose` | **기대 결과 포렌식** 표(어느 단계에서 탈락했나 + 수정안) — [FORENSIC.md](FORENSIC.md) | read |
| `wiki_status` | – | 색인 통계·프로바이더 | read |
| `wiki_analysis` | `request_id`(생략=마지막), `focus=quality|speed|tokens|all` | **상세 분석 리포트**(마크다운): 설정 스냅샷·단계 타임라인·검색 상세·답변 판정·세 렌즈 소견과 조절점 — 튜닝 제안의 근거 자료 — [ANALYSIS_MODE.md](ANALYSIS_MODE.md) | read |
| `wiki_sources` | `check` | 붙어 있는 외부 소스(다른 RAG) 목록·용도·(check) 연결 상태 + 토글·플러그인 상태 — [RAG_FEDERATION.md](RAG_FEDERATION.md) | read |
| `wiki_external_search` | `query`, `source`, `k` | 외부 소스에 직접 검색(융합 없음, 원 결과 id/제목/본문/점수/URL) | read |
| `<source>__<tool>` | 원격 스키마 그대로 | 페더레이션: `mcp_sources.json` 에서 `expose` 한 외부 서버의 tool 을 그대로 중계 (토글 `mcp_federation`) | read |
| (플러그인) | 플러그인 정의 | `plugins/mcp_tools/*.py` 가 등록한 도구 | read |

외부 LLM 의 권장 사용 순서: `wiki_query` → 답변이 부족하면 `wiki_forensic(request_id, expected_docs/terms)` 로 원인 확인 → `wiki_propose`/`wiki_feedback` 으로 개선 제안. 색인을 바꾸는 도구는 없다(변경은 사람이 Web/CLI 로 승인).

## 3. 보안 정리

- 인증: `Authorization: Bearer <API 키>` > 세션 쿠키 > 익명(`anonymous_role`). 키마다 역할이 있고 파일에는 해시만 저장(`apikey list|remove`). **잘못된/폐기된 `lwk_` 키는 익명으로 강등되지 않고 401** (`WWW-Authenticate: Bearer` + JSON-RPC 오류) — 클라이언트 로그에서 키 문제가 바로 드러난다.
- 권한: MCP 도구는 모두 `read` 등급 → `permissions.levels.read`(기본 viewer). 특정 클라이언트에게만 열려면 `anonymous_role: ""` + 키 발급.
- 전송 보안: HTTPS 는 리버스 프록시에서(SECURITY.md §8). 사내망 밖으로 열 때는 반드시 프록시 + 키.
- 감사: 모든 인증 실패와 도구 호출(질의는 requests 테이블, 거부는 audit.jsonl)이 남는다.

## 4. 운영·문제 해결

| 증상 | 조치 |
|---|---|
| 클라이언트가 서버를 못 찾음 (stdio) | `cwd` 가 프로젝트 루트인지, Windows 는 `python.exe` 절대 경로인지. 터미널에서 `python -m llmwiki mcp` 를 띄우고 `{"jsonrpc":"2.0","id":1,"method":"tools/list"}` 를 붙여넣어 응답 확인 |
| HTTP 401 | 토큰 오타/삭제됨(`apikey list`) — `lwk_` 키가 맞지 않으면 익명 역할과 무관하게 401. 또는 `anonymous_role` 이 비어 있는데 토큰 없이 호출 |
| 클라이언트 설정을 어떻게 적어야 할지 모르겠다 | `python -m llmwiki mcp --client-config --url http://wiki-host:8765 --token lwk_…` 출력을 붙여 넣는다. 형식 원본: `setup/mcp_clients.example.json` |
| HTTP 405 on GET | 정상 — 이 서버는 SSE 스트림을 제공하지 않으므로 클라이언트가 JSON 응답 모드로 동작해야 한다(대부분 자동) |
| 응답이 느리다 | 다른 빌드/질의가 서버 락을 잡고 있음(`/api/progress` 로 확인). 빌드는 OS 스케줄러 시간대로 |
| 여러 LLM 이 동시에 붙어 큐잉 | 정상. 처리량이 필요하면 `mcp --transport http --port 8766/8767 …` 로 프로세스를 늘리고 프록시 분배 |
| 브리지가 `remote MCP HTTP 599` | 원격 URL 접속 불가(방화벽/프록시). `curl` 로 §1.2 4번 확인 |
| 도구 결과에 `LLM 실행 보고` | 답변 LLM 이 재시도 후 실패해 추출식으로 대체됨. `agents.json timeout_s/retries`, `config llm_timeout/llm_retries`, `models test --live` |

## 5. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/mcp.py` | 도구 정의/실행(`call_tool`), JSON-RPC 처리(`handle`), stdio 루프(`serve_stdio`), Streamable HTTP(`handle_http`), 브리지(`bridge_stdio_to_http`, `http_post_mcp`), 클라이언트 설정 예시(`client_config_snippets`), **확장**: 플러그인 레지스트리(`register_tool`, `load_plugins`)·페더레이션(`federated_tools`, `call_extension`, 재귀 방지 `FED_HEADER`) |
| `llmwiki/mcp_client.py` | 외부 소스 클라이언트 stdio/http/rest, 검색 채널 `retrieve`, 페더레이션 중계 `call_source_tool` — [RAG_FEDERATION.md](RAG_FEDERATION.md) |
| `llmwiki/web/server.py` | `/mcp` 라우팅(POST/GET/DELETE) + Bearer/쿠키/익명 인증 + 감사, `serve(mcp_only=True)` |
| `llmwiki/cli.py` | `mcp --transport stdio|http --host --port --connect --token --insecure --client-config --url` (생략 시 `config.json mcp_*`/`web_*`) |
| `llmwiki/config.py` | Settings `web_host/web_port/mcp_transport/mcp_host/mcp_port/mcp_url` + 설명(`SETTING_HELP`) |
| `setup/mcp_clients.example.json` | 클라이언트 설정 원본 4종 + 붙여 넣는 위치 |
| `tools/verify/verify_web.py` | `/mcp` 9도구·401/405/DELETE·잘못된 키 401 실측 ([VERIFICATION_0915.md](VERIFICATION_0915.md) §3.3) |
| `llmwiki/auth.py` | API 키 발급/검증(`add_api_key/user_from_api_key`), `identify()` 의 Bearer 처리 |
| `tests/test_features_0914.py::McpHttpTest` | 401/토큰/세션/배열 요청/GET 405/DELETE/쿠키/감사/브리지/익명/mcp_only |
