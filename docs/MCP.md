# MCP — 외부 LLM(여러 개, 같은 PC 또는 원격) 이 LLM Wiki 를 도구로 쓰기

> 대상: Claude Desktop/Claude Code/Cursor/opencode 같은 MCP 클라이언트나, 자체 에이전트에서 이 위키를 검색·질의 도구로 붙이려는 사람. Windows/Linux, 같은 PC/원격 서버 모두 다룬다. 권한·API 키는 [SECURITY.md](SECURITY.md) §4.4, 설계 배경은 [IMPLEMENTATION_PLAN_0914.md](history/2026-09-14/IMPLEMENTATION_PLAN_0914.md) §2.

## 0. 어떤 방식을 쓰나 (결정표)

| 상황 | 방식 | 설정 한 줄 |
|---|---|---|
| 클라이언트가 **같은 PC**, 1~2개 | **stdio** (클라이언트가 `python -m llmwiki mcp` 를 자식 프로세스로 실행) | `claude mcp add llmwiki -- python -m llmwiki mcp` (cwd=프로젝트 루트) |
| 클라이언트가 **다른 PC** / 외부 LLM **여러 개** / 서버가 이미 `serve` 중 | **Streamable HTTP** — Web 서버가 같은 포트에서 `POST /mcp` 제공, Bearer API 키 | 클라이언트 설정에 `{"type":"http","url":"http://wiki-host:8765/mcp","headers":{"Authorization":"Bearer lwk_…"}}` |
| 원격 서버인데 클라이언트가 **stdio 만 지원** | **브리지** — 로컬에서 `python -m llmwiki mcp --connect URL --token …` 을 stdio 서버처럼 등록 | `claude mcp add llmwiki-remote -- python -m llmwiki mcp --connect http://wiki-host:8765/mcp --token lwk_…` |
| Web UI 없이 MCP 만 열고 싶다 | **단독 HTTP** | `python -m llmwiki mcp --transport http --host 0.0.0.0 --port 8766` |
| **다른 RAG / 검색 API / MCP 서버를 이 서버 뒤에 붙이고** 싶다 (클라이언트는 우리 `/mcp` 하나만) | **페더레이션 + 외부 RAG 채널** — `mcp_sources.json` 에 소스(stdio/http/rest) 선언, 토글 `mcp_federation`(도구 노출) · `external_rag`(검색 채널) | [RAG_FEDERATION.md](RAG_FEDERATION.md) |
| 코드 수정 없이 **도구를 추가**하고 싶다 | **플러그인** `plugins/mcp_tools/<이름>.py` 의 `register(add_tool)` | RAG_FEDERATION.md §3 |

세 방식 모두 같은 도구(§2)와 같은 파이프라인(`Pipeline.query`)을 쓴다. HTTP 는 표준 라이브러리 `http.server` 만 사용한다(추가 패키지 없음). 확장성: 도구 목록은 built-in **18개** + 플러그인 + 페더레이션(`<source>__<tool>`)으로 늘어나며, 모두 같은 `tools/list`·`tools/call`·인증·감사 경로를 탄다.

**붙기 전에 서버 쪽에서 먼저 확인**: `python -m llmwiki mcp --doctor` (§4). "왜 안 붙는가" 를 클라이언트 로그 대신 서버에서 답한다.

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

built-in <!--live:mcp-->20개 (2026-09-18: `wiki_sweep` · `wiki_rules` · `wiki_graph_profile` · 2026-09-19: `wiki_graph_rules` · 2026-09-20: `wiki_evolve` 설명 인자와 `wiki_status(full)` 확장). 아래 표의 **힌트** 열은 `tools/list` 가 각 도구에 함께 돌려주는 MCP `annotations` 이며, 붙는 LLM 이
"이 도구를 불러도 되는가" 를 스스로 판단하는 근거다(§2.2).

| 도구 | 입력 | 돌려주는 것 | 등급 | 힌트 |
|---|---|---|---|---|
| `wiki_query` | `question`, `k`, `mode=fast|normal|deep`, `doc_types[]`, `preset` | 인용 `[C#]` 답변 + 판정/groundedness + 근거 목록(doc_expand 청크 표시) + **request_id / query_id** + (있으면) LLM 실행 보고 | read | 읽기 |
| `wiki_search` | `channels[]`(fts/vector/graph, 또는 `channel` 하나), `mode=or\|and\|rrf`, `query`, `k`, `require[]`, `exclude[]`, **`doc_types[]`** | 고른 채널을 한 번에 돌려 조합한 결과. 행마다 **어느 채널이 몇 위로 찾았는지**가 함께 온다. `or` 합집합(커버리지) · `and` 교집합(채널 합의) · `rrf` 질의 경로와 같은 가중 융합. `doc_types` 는 여기서 **거르는** 조건이다(`wiki_query` 의 `doc_types` 는 가중치) — CLI `search --doc-types` · Web Ask › 채널 검색의 유형 칩과 같은 엔진 | read | 읽기 |
| `wiki_evolve` | `status`(proposed 기본), `limit`, **`id`**, **`explain`** | **자가진화 제안 보기 (읽기 전용)**: 그 상태의 제안 목록 · 최근 적용 이력 · 자동 적용 설정과 허용 종류 · 제안 종류 목록. **`id=<번호>` 또는 `explain=true`** 를 주면 제안마다 **무엇이 · 어느 파일에서 · 어떻게(before/after) · 영향 · 리빌드가 드는가 · 왜 적용 못 하는가**가 붙는다(CLI `evolve show` · Web 제안 카드와 같은 모듈). `wiki_propose` 로 올린 제안이 어떻게 됐는지 확인한다. 적용·거절은 사람이 한다 — [EVOLVE.md §1.5](EVOLVE.md) | read | 읽기 |
| `wiki_inspect` | `query` | **질의 해부 (LLM 없음)**: 토큰화·키워드·불용어, 규칙 확장(동의어·약어·별칭·제외), 시간 표현 범위, 채널 라우팅 가중치, 고정 근거(pin). 답이 이상할 때 "질문이 제대로 이해됐는지" 를 먼저 본다 | read | 읽기 |
| `wiki_related` | `text`, `doc_types[]`, `k` | 유사 문서 + 그래프 연결(CL↔Issue↔TC) | read | 읽기 |
| `wiki_doc` | `id`(ISSUE-2041 / doc_id) | 문서 전문 + 메타 + 관계 | read | 읽기 |
| `wiki_entity` | `name` | 엔티티 상세(관계·provenance·문서 참조) | read | 읽기 |
| `wiki_propose` | `kind`, `payload`, `reason`, `confidence` | 자가진화 제안 id (HITL; `pin`/`query_rule` 등) | read | **쓰기**(비파괴) |
| `wiki_feedback` | `query_id`, `feedback=+1/-1`, `note` | 피드백 기록(부정+메모 → 위키 노트 제안) | read | **쓰기**(비파괴) |
| `wiki_forensic` | `request_id`(생략=마지막), `expected_docs[]`, `expected_terms[]`, `expected_chunks[]`, `note`, `propose` | **기대 결과 포렌식** 표(어느 단계에서 탈락했나 + 수정안) — [FORENSIC.md](FORENSIC.md) | read | 읽기 |
| `wiki_status` | `full`, `days`(기본 7), `sections[]`, `bucket`, `top` | 색인 통계·프로바이더. **`full=true` 면 `ops` 아래에 운영 통계**가 함께 온다 — 단계별 빌드 ms · 질의 시간대/창구 분포 · p50/p95 와 가장 느린 질의 · 토큰 · 근거 부족률 · 폴더별 용량과 정리 힌트 · 임베딩 캐시 적중률. 섹션: `index·build·queries·latency·tokens·quality·users·storage·embed·trend`. **`sections=["trend"]` + `bucket=day\|week\|month`** 은 구간별 추세(질의량·지연·토큰·빌드·근거 부족률)를 주며 기간은 묶음에 맞춰 자동으로 넓어진다. `users` 절은 **admin 키에만** 나가고 아니면 `redacted` 에 이유가 담긴다. CLI `stats --full` · Web 옵저빌리티 › 시스템 과 **같은 함수**(읽기 전용, 기간·표본 상한 있음) — [OPS_STATS.md](OPS_STATS.md) | read | 읽기 |
| `wiki_analysis` | `request_id`(생략=마지막), `focus=quality|speed|tokens|all` | **상세 분석 리포트**(마크다운): 설정 스냅샷·단계 타임라인·검색 상세·답변 판정·세 렌즈 소견과 조절점 — 튜닝 제안의 근거 자료 — [ANALYSIS_MODE.md](ANALYSIS_MODE.md) | read | 읽기 |
| `wiki_sources` | `check` | 붙어 있는 외부 소스(다른 RAG) 목록·용도·(check) 연결 상태 + 토글·플러그인 상태 — [RAG_FEDERATION.md](RAG_FEDERATION.md) | read | 읽기 · **외부** |
| `wiki_external_search` | `query`, `source`, `k` | 외부 소스에 직접 검색(융합 없음, 원 결과 id/제목/본문/점수/URL) | read | 읽기 · **외부** |
| `wiki_requests` | `request_id`(생략=목록), `q`, `kind`, `limit` | **지난 요청과 그때의 답**: "전에 물어본 적 있나?" 를 확인하거나, 같은 질문을 다시 돌리지 않고 저장된 답을 가져온다 — [REQUEST_HISTORY.md](REQUEST_HISTORY.md) | read | 읽기 |
| `wiki_rerun` | `request_id`, `from`, `overrides` | 지난 질의를 **특정 단계부터** 다시 (앞 단계는 저장된 결과를 재생). 인자 없이 부르면 재시작점 목록을 돌려준다 — [RERUN.md](RERUN.md) | read | 읽기 (색인을 바꾸지 않음) |
| `wiki_sweep` | `request_id`(또는 `"last"`), `key`, `values[]` 또는 `range="a:b:s"`, `repeats`, `from` | **파라미터 스윕**: 키 하나의 값을 바꿔 가며 값마다 그 키의 단계부터 재생 재실행하고 값별 단계 시간·순위·컨텍스트·답변·groundedness 를 기준(첫 값)과 비교. `key` 없이 부르면 스윕 가능한 키 목록. 값 개수는 `sweep_max_values` 로 제한 — [SWEEP.md](SWEEP.md) | read | 읽기 (색인을 바꾸지 않음; `data/sweeps` 에 기록 저장) |
| `wiki_rules` | `action=explain\|test`, `term`, `q` | 질의 확장 사전(query_rules.json) 읽기 전용. `explain` = 용어 하나가 어느 유형·어느 **방향**(acronym/synonym 양방향 · alias/related/exclude 일방)으로 무엇을 끌어오나, `test` = 질의 전체의 확장 결과. 사전을 바꾸지 않는다 — [QUERY_RULES.md](QUERY_RULES.md) | read | 읽기 |
| `wiki_graph_profile` | `eval`, `compare` | **그래프 진단 프로파일**: 규모·연결성(성분/고립/허브)·문서 커버리지·품질 신호·규칙 기여(죽은 규칙)·질의 활용·개선 제안(어느 파일·키). `compare` 는 직전 실행과 diff, `eval` 은 그래프 채널만 켠 hit@k — [GRAPH_PROFILE.md](GRAPH_PROFILE.md) | read | 읽기 (이력을 `data/graph_profiles` 에 저장) |
| `wiki_graph_rules` | `action=types\|lint\|test`, `q`, `doc_type`, `ext_id` | **그래프 빌드 규칙**(`data/rules.json`) 읽기 전용 — `wiki_rules`(질의 확장)와 짝. `types` = 엔티티 type 목록·값 종류(`relation_patterns[*].value` 에 쓸 수 있는 것)·관계 어휘(`schema.relations`, inverse 포함), `lint` = 빌드 전 정적 점검(깨진 정규식·없는 type·가려진 `link_rules`·겹치는 별칭·inverse 짝), `test` = 문장 하나를 실제로 추출해 생기는 노드·관계. 규칙을 바꾸지 않는다 — [GRAPH_RULES.md](GRAPH_RULES.md) | read | 읽기 |
| `<source>__<tool>` | 원격 스키마 그대로 | 페더레이션: `mcp_sources.json` 에서 `expose` 한 외부 서버의 tool 을 그대로 중계 (토글 `mcp_federation`) | read | 원격 spec 그대로 |
| (플러그인) | 플러그인 정의 | `plugins/mcp_tools/*.py` 가 등록한 도구 (§6) | read | 플러그인 spec 그대로 |

외부 LLM 의 권장 사용 순서: `wiki_query` → 답변이 부족하면 `wiki_forensic(request_id, expected_docs/terms)` 로 원인 확인 → `wiki_propose`/`wiki_feedback` 으로 개선 제안. **색인을 바꾸는 도구는 없다** — `wiki_propose`/`wiki_feedback` 도 제안 큐에 쌓을 뿐이고 반영은 사람이 Web/CLI 로 승인한다.

### 2.01 `structuredContent` — 산문을 파싱하지 않아도 되게 (2026-09-19)

도구 응답은 사람이 읽는 `content[0].text` 와 기계가 읽는 `structuredContent` 를 **둘 다** 준다.
예전에는 `wiki_query` 만 `output_mode` 가 `answer` 가 **아닐 때만** 구조화 결과를 줘서, 정작 가장 많이 쓰는
기본 경로에서 붙은 LLM 이 한국어 산문에서 인용과 판정을 되짚어야 했다. 지금은 기본 모드에도 준다.

`wiki_query`(기본 모드)의 `structuredContent` — 필드 이름은 Web `/api/query` 의 `result` 와 같다:

| 필드 | 뜻 |
|---|---|
| `answer` | 인용 `[C#]` 이 들어간 답변 본문 |
| `citations[]` | `{n, chunk_id, doc_id, heading, doc_type, ext_id, date, why}` — **컨텍스트에 들어간 근거만**. `n` 이 답변의 `[C#]` 번호다 |
| `evidence` | 근거 판정(`verdict`: sufficient/weak/insufficient 등) |
| `groundedness` · `result_type` · `answer_mode` | 품질 지표와 어떤 경로로 답했는지 |
| `n_hits` · `fallback` | 후보 수 · fallback 라운드 수 |
| `request_id` · `query_id` · `run_id` · `ms` | `wiki_forensic`·`wiki_feedback`·`wiki_rerun` 에 그대로 넘긴다 |

같은 질문을 Web·CLI·MCP 로 물으면 **인용 매핑(`[C#] → chunk_id`)·근거 순서·판정이 같아야 한다.**
그것을 고정하는 테스트가 `tests/test_surface_consistency.py`(8항목)다 — 도구가 **존재하는가**는
`tools/verify/verify_surface_align.py`, **같은 것을 돌려주는가**는 이 테스트가 본다.

### 2.05 다른 팀의 stdio MCP 서버를 붙일 때 — 우리가 멈추지 않는다 (2026-09-19)

`mcp_sources.json` 에 `transport: "stdio"` 로 남의 서버를 붙이면 그 서버는 **우리 프로세스의 자식**이 된다. 자식이 이상하게 굴어도 우리는 살아 있어야 한다.

| 자식의 행동 | 예전 | 지금 |
|---|---|---|
| 응답하지 않는다 | `stdout.readline()` 이 락 안에서 **영원히** 멈췄다(마감은 줄 사이에서만 검사돼 `timeout_s` 가 무의미) | 리더 스레드가 읽고 `request()` 는 큐에서 기다린다 → **`timeout_s` 안에 오류** |
| stderr 에 많이 쓴다 | stderr 파이프를 세션 중 비우지 않아 버퍼가 차면 자식이 write 에서 멈추고 우리는 stdout 을 기다려 **교착** | stderr 전용 리더 스레드가 계속 비우고 마지막 줄들을 오류 메시지에 붙인다 |
| 도중에 죽는다 | 남은 시간만큼 기다렸다 | `poll()` 로 즉시 알아채고 **stderr 꼬리와 함께** 보고 |
| 우리에게 요청을 보낸다(`sampling/*` 등) | 무시 → 상대가 기다린다 | `-32601 method not found` 로 **즉답** |

설정: 소스별 `timeout_s`(없으면 `server.json` 의 `mcp.source_timeout_s_default`, ingest 는 `ingest_timeout_s_default`).
검증: `python -m unittest tests.test_mcp_stdio_client` — 네 경우를 진짜 자식 프로세스로 재현한다.

### 2.1 인자 검증 — 붙는 LLM 이 실패 이유를 읽을 수 있게

`tools/call` 은 도구를 실행하기 전에 `inputSchema` 의 **required · type · enum** 을 확인하고, 위반이면 실행하지 않고
`isError: true` 와 사람이 읽는 메시지 + 스키마 전문을 돌려준다(`llmwiki/mcp.py` `validate_args()`). built-in 도구와
플러그인 도구에 똑같이 적용된다. 예전에는 빈 질문으로 `wiki_query` 를 부르면 조용히 빈 결과가 나와, 붙은 LLM 이
"검색 결과가 없다" 와 "인자를 잘못 줬다" 를 구별하지 못했다.

| 부른 모습 | 돌아오는 메시지(요지) |
|---|---|
| `wiki_query {}` · `{"question": "   "}` | `필수 인자 'question' 가 없습니다. inputSchema: {…}` |
| `wiki_search {"channel": "nope", …}` | `인자 'channel' 는 ['fts', 'vector', 'graph'] 중 하나여야 합니다 (받은 값: 'nope')` — `channels` 로 준 알 수 없는 이름은 조용히 버려지고, 하나도 남지 않으면 `fts` 로 떨어진다 |
| `wiki_search {"k": "많이"}` | `인자 'k' 는 integer 여야 합니다 (받은 값: '많이')` — 다만 `"8"` 처럼 **숫자로 읽히는 문자열은 받아 준다** |
| `wiki_propose {"kind": "정체불명"}` | `unsupported kind 정체불명` |
| 없는 도구 | `unknown tool <이름> — 사용 가능: wiki_query, …` |
| 없는 문서 (`wiki_doc`) | `not found: <id>` — 이것은 오류가 아니라 **정상 응답**(`isError` 아님) |

프로토콜 수준의 오류는 JSON-RPC 코드로 온다: 없는 메서드 `-32601`, `method` 가 없는 본문 `-32600`,
깨진 JSON `-32700`, 서버가 요청을 받지 않음(동시성·속도 제한) `-32002` + HTTP 429/503(§3.1).

### 2.2 도구 힌트(annotations)와 프로토콜 버전

- `tools/list` 의 각 도구에는 `title` 과 MCP `annotations` 가 붙는다 — `readOnlyHint`(색인·설정을 바꾸지 않음),
  `destructiveHint`(되돌릴 수 없음 — **우리 도구에는 없다**), `idempotentHint`, `openWorldHint`(외부 시스템에 나간다).
  14개 중 `wiki_propose`·`wiki_feedback` 만 `readOnlyHint=false` 이고, 그 둘도 `destructiveHint=false` 다(사람 승인 전까지 큐에만 쌓인다).
  플러그인·페더레이션 도구는 자기 spec 의 annotations 를 그대로 쓴다. 정의: `llmwiki/mcp.py` `ANNOTATIONS`.
- **프로토콜 버전 협상**: 지원 목록은 `2025-06-18`(기본) · `2025-03-26` · `2024-11-05`.
  클라이언트가 `initialize` 에 보낸 버전이 이 안에 있으면 그대로 돌려주고, 아니면 우리 최신(`2025-06-18`)을 돌려준다.
  예전에는 클라이언트가 보낸 값을 그대로 되돌려 주어, 지원하지 않는 버전도 지원한다고 답했다.
- **도구 이름 중복**: 플러그인과 페더레이션이 같은 이름을 만들면 뒤에 온 것을 버린다(클라이언트가 어느 것을 부를지 알 수 없으므로).
  버려진 이름은 `wiki_sources` 와 `mcp --doctor` 에 보고된다.
- **stdio 프레이밍**: 한 줄에 하나(`{…}\n`) 또는 `Content-Length: <바이트 수>\r\n\r\n<본문>`.
  헤더의 길이는 **UTF-8 바이트 수**이며(문자 수가 아니다), 한글 인자를 이 방식으로 보내는 클라이언트도 정확히 잘린다.

### 2.3 붙는 LLM 이 여럿일 때 — 키는 클라이언트마다 따로

동시성 제한(`server.json concurrency`)은 **사용자(=API 키)별**로 걸린다. 여러 LLM 이 **같은 키**를 쓰면
`max_parallel_per_user`(기본 3) 하나만 나눠 쓰게 되어, 네 번째 동시 호출부터 429(`code=per_user_limit`)를 받는다.
클라이언트마다 `apikey add <이름>` 으로 키를 따로 발급하면 각자 그 제한을 갖고, IP 전체는
`max_parallel_per_ip`(기본 6)와 `rate_limit` 에 걸린다. 값과 조정 방법은 [CONCURRENCY.md](CONCURRENCY.md).

## 3. 보안 정리

- 인증: `Authorization: Bearer <API 키>` > 세션 쿠키 > 익명(`anonymous_role`). 키마다 역할이 있고 파일에는 해시만 저장(`apikey list|remove`). **잘못된/폐기된 `lwk_` 키는 익명으로 강등되지 않고 401** (`WWW-Authenticate: Bearer` + JSON-RPC 오류) — 클라이언트 로그에서 키 문제가 바로 드러난다.
- ⚠ `security.json` 의 `mode` 가 기본값 `auto` 면 **`127.0.0.1` 에 바인드한 서버는 인증을 하지 않는다**(개발 편의 — 모든 요청이 로컬 admin). 사내에 내놓을 때는 `0.0.0.0` 바인드라 자동으로 켜지지만, 루프백에서 인증 동작을 확인하려면 `mode: "on"` 으로 두고 봐야 한다. 그렇지 않으면 "잘못된 키가 거부되는가" 를 시험해도 늘 통과한다.
- **키는 붙는 클라이언트마다 따로** 발급한다 — 역할 분리와 폐기뿐 아니라 동시성 제한이 키 단위이기 때문이다(§2.3).
- 권한: MCP 도구는 모두 `read` 등급 → `permissions.levels.read`(기본 viewer). 특정 클라이언트에게만 열려면 `anonymous_role: ""` + 키 발급.
- **문서 단위 접근 제어 적용** (2026-09-19): `read` 등급을 통과해도 **키의 역할로 근거가 걸러진다**. `wiki_query` 의 인용, `wiki_search` 의 행·채널별 snippet, `wiki_doc` 의 전문, `wiki_related` 의 문서 목록이 모두 `docacl.json` 의 경로 규칙과 문서 front matter 의 `acl:` 로 제한된다 — 키 하나로 전 문서가 열리지 않는다. 규칙 **편집**은 MCP 에 노출하지 않는다(admin 전용 설정: CLI `security docacl`, Web Settings › 보안). 설계는 [SECURITY.md §6.2](SECURITY.md). 예전처럼 전부 보여 주려면 그 키의 역할을 `admin` 으로 두거나 규칙을 비운다.
- **프롬프트 인젝션**: MCP 로 들어온 외부 문서도 답변 컨텍스트에 들어갈 때 같은 펜스·무력화를 거친다(토글 `context_guard`). 수집한 글이 모델에게 직접 지시하지 못한다.
- 전송 보안: HTTPS 는 리버스 프록시에서(SECURITY.md §8). 사내망 밖으로 열 때는 반드시 프록시 + 키.
- 감사: 모든 인증 실패와 도구 호출(질의는 requests 테이블, 거부는 audit.jsonl)이 남는다.

### 3.1 서버가 요청을 받지 않을 때 (429 / 503)

동시 접속·속도 제한에 걸리면 도구 실패가 아니라 **요청 자체가 거부**되고, JSON-RPC `-32002` + HTTP 429/503 +
`Retry-After` 헤더가 온다. 본문 `error.data.code` 로 무엇에 걸렸는지 구분한다.

| `data.code` | HTTP | 뜻 | 설정 키 (`server.json`) |
|---|---|---|---|
| `per_user_limit` | 429 | 같은 키(사용자)의 동시 요청 초과 | `concurrency.max_parallel_per_user` (기본 3) |
| `per_ip_limit` | 429 | 같은 IP 의 동시 요청 초과 | `concurrency.max_parallel_per_ip` (기본 6) |
| `rate_limited` | 429 | 분당 요청 수 초과 | `rate_limit.per_user_per_min` (60) · `per_ip_per_min` (120) |
| `queue_full` | 503 | 대기열이 가득 참 | `concurrency.queue_max` (64) |

클라이언트는 `Retry-After` 만큼 기다렸다 재시도하면 된다. 상세와 조정 기준은 [CONCURRENCY.md](CONCURRENCY.md).

## 4. 자가 점검 — `mcp --doctor`

붙이려는 LLM 이 여럿이고 외부 RAG 까지 얹으면 "왜 안 붙는가" 의 원인이 클라이언트·서버·소스에 흩어진다.
`--doctor` 는 **서버 쪽에서 답할 수 있는 것을 한 번에** 점검한다. bring-up 체크리스트의 "연결 확인" 단계로 쓴다.

```bat
python -m llmwiki mcp --doctor                     :: 사람이 읽는 출력
python -m llmwiki mcp --doctor --check-sources     :: 외부 소스에 실제로 접속해 본다 (느림)
python -m llmwiki mcp --doctor --json              :: 스크립트/CI 용 (ok, errors, warnings, checks[], tools[])
```

<!-- 실제 출력 (2026-09-16, 확장 없는 기본 환경) -->
```text
MCP 자가 점검 — 프로토콜 2025-06-18 (지원 2025-06-18, 2025-03-26, 2024-11-05)

  OK  도구 목록              14개 (built-in 14)
  OK  inputSchema        모든 도구가 object 스키마
  OK  도구 설명              모두 있음
  OK  플러그인               C:\...\plugins\mcp_tools — 파일 0개, 도구 없음
  OK  외부 소스 선언           4개 선언, 0개 enabled (-)
  OK  검색 채널(external_rag) retrieve 소스 없음 · 토글 external_rag=False
  OK  페더레이션              expose 소스 없음 · 토글 mcp_federation=False · 중계 도구 없음
  OK  페더레이션 연결           오류 없음
  OK  재귀 방지              이 프로세스는 최상위 (페더레이션 가능)
  OK  인증                 anonymous_role=viewer · API 키 0개 · 계정 1개
  OK  전송                 stdio: `python -m llmwiki mcp` · http: serve 의 POST /mcp (mcp_host=127.0.0.1 mcp_port=8766) · 브리지: mcp --connect <url>

도구 20개: wiki_query, wiki_search, wiki_inspect, wiki_evolve, wiki_related, wiki_doc, wiki_entity, wiki_propose, wiki_feedback, wiki_forensic, wiki_status, wiki_analysis, wiki_sources, wiki_external_search, wiki_requests, wiki_rerun, wiki_sweep, wiki_rules, wiki_graph_profile, wiki_graph_rules

결과: 정상 (오류 0 · 경고 0)
클라이언트 설정: python -m llmwiki mcp --client-config   · 문서 docs/MCP.md
```

| 항목 | 실패하면 보는 곳 |
|---|---|
| 도구 목록 / inputSchema / 도구 설명 | 플러그인 spec 의 `inputSchema` 를 `{"type":"object","properties":{…}}` 로. 설명이 없으면 붙는 LLM 이 그 도구를 고르지 못한다(경고) |
| 플러그인 | `<mcp_plugins_dir>/*.py` 의 import·`register()` 실패. **깨진 파일 하나가 다른 플러그인을 막지는 않는다** — 힌트에 파일명과 예외가 나온다 |
| 외부 소스 선언 / 소스 `<이름>` 설정·연결 | `mcp_sources.json` 의 `transport` 별 필수 필드(stdio=`command`, http=`url`, rest=`base_url`). 재현: `python -m llmwiki mcp-source test <이름>` |
| 검색 채널(external_rag) / 페더레이션 | `retrieve`·`expose` 를 선언해 놓고 토글을 켜지 않은 흔한 실수 (경고) |
| 페더레이션 연결 | 원격이 죽었거나 인증 실패 — 예전에는 도구가 조용히 사라졌다 |
| 재귀 방지 | 이 프로세스가 다른 llmwiki 의 페더레이션 하위로 실행 중이면 자기 페더레이션을 하지 않는다(A↔B 상호 expose 무한 재귀 방지) |
| 인증 | 익명 역할도 API 키도 없으면 HTTP 로는 아무도 붙을 수 없다 |

종료 코드는 오류가 있으면 1 이므로 배포 스크립트에서 그대로 쓸 수 있다.

## 5. 운영·문제 해결

**먼저 `python -m llmwiki mcp --doctor`** (§4) — 아래 증상의 절반은 여기서 원인이 바로 나온다.

| 증상 | 조치 |
|---|---|
| 클라이언트가 서버를 못 찾음 (stdio) | `cwd` 가 프로젝트 루트인지, Windows 는 `python.exe` 절대 경로인지. 터미널에서 `python -m llmwiki mcp` 를 띄우고 `{"jsonrpc":"2.0","id":1,"method":"tools/list"}` 를 붙여넣어 응답 확인 |
| 도구를 불렀는데 `isError` 와 "필수 인자 …" | 인자 검증(§2.1). 메시지에 `inputSchema` 가 함께 오므로 그대로 고쳐 다시 부르면 된다 |
| 429 / 503 으로 거부됨 | 동시성·속도 제한(§3.1). `Retry-After` 만큼 기다렸다 재시도. 여러 LLM 이 같은 키를 쓰고 있지 않은지 확인(§2.3) |
| 잘못된 키인데도 통과한다 | `security.json mode=auto` + `127.0.0.1` 바인드는 인증을 하지 않는다(§3). `mode: "on"` 으로 두고 확인 |
| 플러그인 도구가 안 보임 | `mcp --doctor` 의 "플러그인" 행에 파일명과 예외가 나온다. 파일명이 밑줄로 시작하면 무시된다 |
| 페더레이션 도구가 사라짐 | 원격 연결 실패. `wiki_sources {"check": true}` 의 `federation_errors` 와 `mcp --doctor --check-sources` |
| 한글 인자를 보내면 응답이 멈춘다 | 클라이언트가 `Content-Length` 를 **문자 수**로 적고 있다. 헤더 값은 UTF-8 **바이트 수**여야 한다(§2.2) |
| HTTP 401 | 토큰 오타/삭제됨(`apikey list`) — `lwk_` 키가 맞지 않으면 익명 역할과 무관하게 401. 또는 `anonymous_role` 이 비어 있는데 토큰 없이 호출 |
| 클라이언트 설정을 어떻게 적어야 할지 모르겠다 | `python -m llmwiki mcp --client-config --url http://wiki-host:8765 --token lwk_…` 출력을 붙여 넣는다. 형식 원본: `setup/mcp_clients.example.json` |
| HTTP 405 on GET | 정상 — 이 서버는 SSE 스트림을 제공하지 않으므로 클라이언트가 JSON 응답 모드로 동작해야 한다(대부분 자동) |
| 응답이 느리다 | 다른 빌드/질의가 서버 락을 잡고 있음(`/api/progress` 로 확인). 빌드는 OS 스케줄러 시간대로 |
| 여러 LLM 이 동시에 붙어 큐잉 | 정상. 처리량이 필요하면 `mcp --transport http --port 8766/8767 …` 로 프로세스를 늘리고 프록시 분배 |
| 브리지가 `remote MCP HTTP 599` | 원격 URL 접속 불가(방화벽/프록시). `curl` 로 §1.2 4번 확인 |
| 도구 결과에 `LLM 실행 보고` | 답변 LLM 이 재시도 후 실패해 추출식으로 대체됨. `agents.json timeout_s/retries`, `config llm_timeout/llm_retries`, `models test --live` |

## 6. 다른 RAG 를 이 서버 뒤에 붙이기 — 세 가지 방법

붙는 LLM 은 우리 `/mcp` **하나만** 설정하면 되고, 뒤에 무엇이 몇 개 붙어 있는지는 몰라도 된다.
셋은 배타적이지 않다 — 같은 소스에 `retrieve` 와 `expose` 를 함께 둘 수 있다.
소스 선언 파일은 `mcp_sources.json`(예시 `setup/mcp_sources.example.json`), 필드 전체 설명은 [RAG_FEDERATION.md](RAG_FEDERATION.md) §2.

| | ① 검색 채널로 융합 | ② 도구를 그대로 중계 | ③ 문서를 내려받아 색인 |
|---|---|---|---|
| **설정** | 소스에 `retrieve` 매핑 + 토글 `external_rag` | 소스에 `expose: ["tool", …]` + 토글 `mcp_federation` | 소스에 `ingest` 매핑 |
| **언제 쓰나** | 그쪽 결과가 **우리 답변의 근거**가 되어야 할 때 (인용·groundedness 에 함께 들어간다) | 그쪽 도구를 **LLM 이 직접 고르게** 할 때 (티켓 생성 조회 등 우리 검색과 성격이 다른 도구) | 그쪽 원본이 **거의 안 변하고** 우리 그래프·엔티티까지 태우고 싶을 때 |
| **LLM 에게 보이는 모습** | 도구는 그대로 14개. `wiki_query` 결과의 근거에 `ext_<source>` 채널이 섞인다 | `tools/list` 에 `<source>__<tool>` 이 늘어난다 | 평범한 우리 문서 (`wiki_doc`/`wiki_search` 로 나온다) |
| **설정 예** | `"retrieve": [{"tool": "search", "args": {"q": "{query}", "limit": "{k}"}, "result_path": "items", "id_field": "id", "title_field": "title", "text_field": "snippet"}]` | `"expose": ["search", "get_issue"]` | `"ingest": {…}` — [RAG_FEDERATION.md](RAG_FEDERATION.md) §2.3 |
| **확인 명령** | `python -m llmwiki mcp-source test <이름>` → `wiki_external_search` 도구 → `wiki_query` 근거에 `ext_` 채널 | `mcp --doctor` 의 "페더레이션" 행 → `tools/list` 에 `<source>__<tool>` | `build` 후 `wiki_status` 의 `docs` 증가 |
| **안 될 때 증상** | 근거에 외부 결과가 하나도 안 섞임 → 토글 `external_rag` 가 꺼져 있거나 `result_path`/필드 이름이 틀림 | `tools/list` 에 안 나옴 → 토글 `mcp_federation` 이 꺼짐 · `expose` 목록 밖 · 원격 연결 실패(`wiki_sources` 의 `federation_errors`) | 문서가 안 늘어남 → `ingest` 매핑/권한 |

공통 안전장치: **expose 목록 밖의 도구 호출은 거부**되고(`tool X is not exposed by source Y`), 없는 소스도 거부되며
(`unknown federated source`), 페더레이션 하위로 실행된 프로세스는 자기 페더레이션을 하지 않는다(A↔B 상호 expose 무한 재귀 방지).

### 6.1 코드를 고치지 않고 도구 추가하기 (플러그인)

`<mcp_plugins_dir>`(기본 `plugins/mcp_tools`, `config.json` 으로 변경) 의 `*.py` 중 밑줄로 시작하지 않는 파일이
`register(add_tool)` 을 제공하면 그 도구가 built-in 과 **똑같은** `tools/list`·`tools/call`·인자 검증·인증·감사 경로를 탄다.
파일 mtime 이 바뀌면 다시 읽으므로 서버를 재시작하지 않아도 된다.

```python
# plugins/mcp_tools/hello.py
def register(add_tool):
    add_tool(
        {"name": "team_echo", "description": "무엇을 하는 도구인지 — 붙는 LLM 은 이 문장만 보고 고른다",
         "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]}},
        lambda pipe, args: {"echo": args["msg"], "docs": pipe.store.stats()["docs"]},
    )
```

- `inputSchema` 는 **필수**다. 없으면 인자 검증이 걸리지 않고, `mcp --doctor` 가 경고한다.
- 도구 이름에 `__` 를 쓸 수 없다(페더레이션 이름과 충돌). built-in 과 같은 이름도 거부된다.
- handler 는 `dict`(구조화 결과) 또는 문자열을 돌려주면 된다. 예외를 던지면 `isError` 로 감싸인다.
- 한 파일이 깨져도 나머지 플러그인은 그대로 적재되고, 오류는 `wiki_sources` 와 `mcp --doctor` 에 파일명과 함께 보고된다.
- 예시 원본: `plugins/mcp_tools/_example_echo.py` (밑줄을 지우면 활성).

## 7. 검증

```bat
python tools\verify\verify_mcp.py            :: 전체 (98 항목)
python tools\verify\verify_mcp.py --quick    :: HTTP 쪽 도구 반복·동시성 규모를 줄여 빠르게 (87 항목)
python -m unittest tests.test_features_0914  :: McpHttpTest · McpStdioFramingTest
```

무엇을 보는가 — 전송 3종(stdio/HTTP/브리지) · 프로토콜 적합성(버전 협상, 두 가지 프레이밍, 배치, 깨진 입력) ·
도구 13회 호출 · 잘못된 호출 7종 · 인증(익명·잘못된 Bearer·API 키) · 동시 접속 · 확장(플러그인 정상/깨짐,
페더레이션 중계와 거부, 외부 RAG 융합, 재귀 방지) · `mcp --doctor`.
결과는 `tools/verify/verify_mcp_result.json` 에 남고 실패가 있으면 종료 코드 1.
실측 기록은 [VERIFICATION_0916.md](history/2026-09-16/VERIFICATION_0916.md).

## 8. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/mcp.py` | 도구 정의(`TOOLS`)·힌트(`ANNOTATIONS`)·실행(`call_tool`), 인자 검증(`validate_args`), JSON-RPC 처리(`handle`), stdio 프레이밍(`_read_message`)과 루프(`serve_stdio`), Streamable HTTP(`handle_http`), 브리지(`bridge_stdio_to_http`, `http_post_mcp`), 자가 점검(`doctor`), 클라이언트 설정 예시(`client_config_snippets`), **확장**: 플러그인 레지스트리(`register_tool`, `load_plugins`)·페더레이션(`federated_tools`, `call_extension`, 재귀 방지 `FED_HEADER`) |
| `llmwiki/mcp_client.py` | 외부 소스 클라이언트 stdio/http/rest, 검색 채널 `retrieve`, 페더레이션 중계 `call_source_tool` — [RAG_FEDERATION.md](RAG_FEDERATION.md) |
| `llmwiki/web/server.py` | `/mcp` 라우팅(POST/GET/DELETE) + Bearer/쿠키/익명 인증 + 감사, `serve(mcp_only=True)` |
| `llmwiki/cli.py` | `mcp --transport stdio|http --host --port --connect --token --insecure --client-config --url --doctor --check-sources --json` (생략 시 `config.json mcp_*`/`web_*`), doctor 출력 렌더러 `_mcp_doctor_text()` |
| `llmwiki/reqmgr.py` | 동시성·속도 제한(`server.json`) — MCP 거부 시의 429/503 과 `data.code` (§3.1) |
| `plugins/mcp_tools/` | 플러그인 도구 폴더 (`README.md`, `_example_echo.py`) — §6.1 |
| `llmwiki/config.py` | Settings `web_host/web_port/mcp_transport/mcp_host/mcp_port/mcp_url` + 설명(`SETTING_HELP`) |
| `setup/mcp_clients.example.json` | 클라이언트 설정 원본 4종 + 붙여 넣는 위치 |
| `tools/verify/verify_web.py` | `/mcp` 도구·401/405/DELETE·잘못된 키 401 실측 ([VERIFICATION_0915.md](history/2026-09-15/VERIFICATION_0915.md) §3.3) |
| `tools/verify/verify_mcp.py` | **MCP 종단 검증** — 전송 3종·프로토콜·도구·잘못된 호출·인증·동시성·확장·doctor (§7) |
| `llmwiki/auth.py` | API 키 발급/검증(`add_api_key/user_from_api_key`), `identify()` 의 Bearer 처리 |
| `tests/test_features_0914.py::McpHttpTest` | 401/토큰/세션/배열 요청/GET 405/DELETE/쿠키/감사/브리지/익명/mcp_only |
| `tests/test_features_0914.py::McpStdioFramingTest` | stdio 두 프레이밍, `Content-Length` 가 바이트 수라는 것, `method` 없는 본문 → -32600 |
