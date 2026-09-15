# VERIFICATION 2026-09-15 — Web UI · CLI 전 기능 검증 보고서

> 대상: 이 시스템을 다른 환경에 bring-up 한 뒤 "모든 기능이 실제로 동작하는가"를 같은 방법으로 다시 확인하려는 엔지니어/LLM. 검증에 쓴 스크립트는 저장소에 들어 있다(`tools/verify/`, [tools/verify/README.md](../tools/verify/README.md)). 2026-09-14 에 추가한 6개 기능(권한 표·MCP·채널 빌드·doc_expand·LLM 재시도·기대 결과 포렌식)의 설계는 [IMPLEMENTATION_PLAN_0914.md](IMPLEMENTATION_PLAN_0914.md), 설정 위치 총람은 [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) §3.2.

## 0. 결과 요약

| 검증 | 방법 | 결과 |
|---|---|---|
| 단위/통합 테스트 | `python -m unittest discover -s tests` (13 파일) | **87 / 87 통과** (37초) |
| CLI 전수 | `tools/verify/verify_cli.py` — 격리 임시 환경에서 모든 하위 명령을 subprocess 로 실행 | **184 / 184 통과** |
| Web API 전수 | `tools/verify/verify_web.py` — 실제 `serve --host 0.0.0.0` 에 게스트/viewer/class1/admin/API 키로 요청 | **210 / 210 통과** |
| UI 배선(정적) | `tools/verify/verify_ui_wiring.py` — JS `$('#id')` 343개 id ↔ HTML, 탭 28 ↔ 섹션 28, JS 가 부르는 API 경로 71개 ↔ 서버 | **OK** (누락 0) |
| 브라우저 로드 | `tools/verify/verify_browser.py` — Edge headless 로 `/`·`/login` 실제 렌더, 콘솔 오류 수집 | **OK** (JS 오류 0, 28탭·신규 요소 렌더) |
| 실 LLM 연결 | 로컬 Ollama(llama3.1) `models test --live`, headless 는 mock 에이전트로 재시도/타임아웃 경로 | OK (사내 게이트웨이·opencode 는 포팅 환경에서 `models test --live` 로 재확인) |

검증 중 발견해 수정한 결함 2건 (§4). 검증 환경: Windows 11, Python 3.14.7, 합성 모뎀 코퍼스 38문서·190청크, `llm_provider=mock`, `embed_provider=hash(256d)` (격리 환경) / 실제 DB(브라우저 검사).

## 1. 검증 방법

### 1.1 격리 환경
`verify_cli.py` / `verify_web.py` 는 임시 폴더에 `config.json`(mock LLM, hash 임베딩, 샘플 코퍼스)·`security.json`·`tuning.json`·`presets.json`·`query_rules.json`·`mcp_sources.json`·`agents.json`·`pins.json`·`rules.json`·`schemas/`·`prompts/`·`questions.json` 을 복사하고, `LLMWIKI_CONFIG`·`LLMWIKI_<NAME>_PATH` 환경변수로 모든 파일 위치를 그쪽으로 돌린다. 실제 `config.json`·`data/`·`logs/` 는 읽지도 쓰지도 않는다. 끝나면 임시 폴더를 지운다. 이 방식 자체가 "설정이 전부 파일로 외부화되어 있고 경로 레지스트리(`config paths`)로 옮길 수 있다"는 bring-up 요구의 검증이기도 하다.

### 1.2 판정 기준
- CLI: 명령별 **기대 종료 코드**(정상 0, 사용 오류 1, 권한 거부 5, 브리지 오류는 0+JSON-RPC 오류 본문). 기대와 다르면 FAIL.
- Web: 요청별 **기대 HTTP 상태**(200 / 게스트 401 / 역할 부족 403 / 확인 필요 428 / 잘못된 입력 400 / 없는 경로 404 / MCP GET 405) + 200 인데 본문이 `error` 만 있으면 FAIL. 작업(job)은 완료까지 폴링해 `done` 확인. MCP 도구 호출은 `isError` 면 FAIL.
- UI: 참조하는 id 가 HTML 이나 JS 의 동적 생성(`id="…"`, `sel('…')`, `.id = '…'`)에 없으면 FAIL. 브라우저: 콘솔의 `Uncaught`/error 가 하나라도 있으면 FAIL.

## 2. CLI — 명령별 결과 (184)

`verify_cli.py` 의 순서. 모두 통과. 괄호는 확인한 변형.

| 영역 | 명령 (변형) | 확인 내용 |
|---|---|---|
| 환경/설정 | `health` (`--quick`) · `config show` (`--effective`, `paths`, `set`, `reset --yes`) · `models show|test|test --live|set` | FAIL 0, 유효값·출처(file/env) 표시, mock live 호출 |
| 튜닝/프리셋/프롬프트 | `tuning show` (`--stage`, `set`, `reset`, 잘못된 키→1, `doc`) · `preset list|show|diff|apply` (`--save`) · `prompts list|show|path|reset` | `docs/TUNING.md` 재생성, 잘못된 튜닝 키는 종료 1 |
| 코퍼스 | `corpus types|schema|example|lint|lint-file|stats` | lint errors 0 warnings 4(샘플 의도) |
| 규칙/시간 | `rules show|add|test|remove|stats|path` · `time "지난주"` | synonym 추가→test 에 반영→제거 |
| 빌드 | `build --full --yes --no-snapshot` · `build`(증분) · `build status|verify|verify --fix` · **`build fts|vector|graph`** (`--full`) · `build --channels fts,vector` · `embed report|status|clear-cache` | 채널 빌드 전후 다른 채널 행 수 불변(`channel_isolation` 경고 0), verify alerts 0, coverage 100% |
| 질의 | `query` (`--trace`, `--json`, `--preset quality|speed|token|deep_research`, `--no-doc-expand`, `--doc-types`, `--k`, `--mode fast|deep`, 시간 표현, 존재하지 않는 주제→insufficient) · `search fts|vector|graph` | `doc_expand` 단계가 trace 에 나타남, `--no-doc-expand` 면 kind=doc_expand 청크 0 |
| pin/사전계산 | `pin add --doc|--chunk|--always|list|test|remove` · `precompute run|status|clear` | pin 이 질의 결과 상단에 옴 |
| 평가/실험 | `eval` (`--k`, `--matrix`) · `trial run --name|list|compare|report` · `fusion show|compare` | hit@k/MRR 은 doc_expand 청크를 제외하고 계산(`evalset.primary_hits`) |
| 포렌식 | `forensic last|list|summary|<id>` · **`forensic expect last --doc … --term … --chunk … --note … --propose`** (없는 문서→unresolved, 용어만, 캐시된 요청) | 단계별 표(fts/vector/graph/fusion/boost/rerank/final/doc_expand/context/answer), `--propose` 로 제안 생성됨 |
| 자가진화/메모리 | `evolve status|review|apply|reject|feedback` (pin/query_rule/tuning/synonym 제안 적용) · `memory status|decay|consolidate|episodes` · `wiki` | tuning 제안 적용 시 tuning.json 갱신, corpus_gap 은 "자동 적용 불가" 안내 |
| 조회 | `docs` · `stats` · `system` · `graph` (`--provenance`) · `entity` · `requests list|last|show` · `logs tail|grep|files` · `arch` (`--flow query`, `--json`) | |
| 유지보수/스냅샷 | `maintenance vacuum|fts_optimize|wal_checkpoint|clear_cache|warm_cache|refresh_doc_refs|purge_requests --yes` · `snapshot create|list|restore --yes|prune` · `watch --once` · `mcp-source list|test|ingest --dry-run|enrich` | 복원 후 DB 재오픈, prune 이 keep 초과분만 삭제 |
| 보안 | `users add|list|set-role|passwd|remove` (잘못된 역할→1) · `security show|init|audit|perms|perms set k=v|perms reset` · `apikey add|list|remove` | 역할 6단계 문자열 검증, perms set 이 security.json 에 즉시 반영 |
| CLI 게이트 | `cli.default_role=viewer` 상태에서 `build`(→5), `pin add`(→5), `--user <class1>`+`LLMWIKI_PASSWORD` 로 `pin add`(→0), `LLMWIKI_API_KEY`(viewer 키)로 `build`(→5), admin 키로 `build`(→0), 잘못된 비밀번호(→5) | 감사 로그 `logs/audit.jsonl` 에 거부 기록 |
| MCP | `mcp`(stdio: initialize→tools/list→tools/call wiki_status 왕복, 배열 요청) · `mcp --connect http://127.0.0.1:1/mcp`(접속 불가→JSON-RPC -32000 `remote MCP HTTP 599`) · **`mcp --client-config --url --token`** | 9개 도구 나열, 브리지가 오류를 JSON-RPC 로 변환, 클라이언트 설정 5키(stdio/http/bridge/claude 한 줄 2종) · python 경로 실재·이중 이스케이프 없음 |
| 설정 외부화(09-15) | `config set web_port=8899 mcp_port=8898` → `config show --effective` 에 `web_*/mcp_*` 반영 · `tuning set forensic_near_miss_mult=4 forensic_term_targets=10` → `tuning reset` | 서버/MCP 기본값과 포렌식 임계가 파일·env 로 제어됨 |

## 3. Web — 엔드포인트별 결과 (210)

### 3.1 정적/게스트 (anonymous_role=viewer)
`/`, `/login`, `static/js/*.js` 7개, `style.css`, `themes/*`, 경로 탈출(`/static/../config.json` → 404). 게스트로 **허용(200)**: `/api/auth/me`(via=anon), `/api/query`(request_id·query_id 반환), `/api/search`, `/api/feedback`, **`/api/forensic/expect`**, `/api/forensic/llm`, `/api/evolve/propose`, `/api/pins`(test), `/api/presets`(apply, 메모리), 그리고 GET 45종(`graph, entity, docs, queries, requests, request, chunk, doc_chunks, query_trace, models, system, watch, tuning, architecture, evolve/status, evolve/proposals, wiki/list, wiki/page, eval/questions, rules, health, config/effective, presets, presets/diff, prompts, logs/files, logs, forensics, forensics/summary, forensic?rerun=1, trials, pins, query_rules, query_rules/test, embed/report, build/status, build/verify, corpus/lint, corpus/types, mcp_sources, memory, precompute, agents, themes, time, progress, snapshot`). 게스트로 **거부(401 + 로그인 안내)**: `/api/build`, `/api/eval`, `/api/pins add`, `/api/config`, `/api/cli`, GET `/api/auth/users`, `/api/audit`, `/api/security`, `/api/apikeys`. 없는 경로 404.

### 3.2 역할별
- viewer(로그인): 빌드 **403**(게스트와 달리 로그인 안내가 아님), 비밀번호 변경 200, 로그아웃 200, 잘못된 비밀번호 로그인 401.
- class1: `eval` job 200→done, `pins add` **428**(edit 등급 확인) → `_confirm` 200 → remove 200, 증분 빌드(index) 확인 후 200→done, `build full`/채널 빌드/`config`/콘솔 `build --full` **403**, 콘솔 `stats` 200.
- admin(`kh82.kim`): users/audit/security/apikeys 조회, `models/test|set`, `config`, `agents`, `mcp_sources test|ingest(dry)|enrich|save`, `tuning set|reset`, `presets save|apply --save`, `prompts set|reset`, `query_rules add|remove|save`, `rules save`, `wiki/page save`(잘못된 이름 400), `evolve review|propose|apply|reject`, `memory decay|consolidate`, `build/verify fix`, `precompute run(job)|doc_vectors|clear`, `maintenance` 6종 + `purge_requests`(428 → 문구+비밀번호 200), `trials run(job)|compare|get|delete`, `fusion/compare(job)`, `watch scan|start|tick|stop`, `snapshot create|restore(428→200)|prune`, **채널 빌드**(428 문구 → 200 job done), **전체 빌드 reset**(200 job done), `purge_logs` 잘못된 비밀번호 428, `users add|set_role|set_password|remove`(자기 자신 삭제 400), `security set_permission|set_permissions|set_anonymous|set_cli|reload`(잘못된 action 400), `apikeys add|remove`, 콘솔 `forensic summary`, 콘솔 `serve`(428 → 확인 후 "콘솔에서 실행 불가" 안내), 콘솔 `maintenance purge_requests`(428→200), CSRF(`Origin: evil` → 403), 로그아웃.

### 3.3 API 키 · MCP
발급한 viewer 키로 `/api/query` 200, `POST /mcp` initialize(키)·tools/list(게스트)·9개 도구 `wiki_query|search|related|doc|entity|propose|feedback|forensic|status` 모두 `isError` 없음, GET 405, DELETE 200. **잘못된/폐기된 `lwk_` 키 → 401**(`/mcp`, `/api/query` 모두), `lwk_` 형식이 아닌 Bearer 는 무시하고 게스트.

### 3.4 UI 배선·브라우저
- 정적: JS 8파일이 참조하는 id 전부 존재(동적 생성 포함), 탭 28 = 섹션 28, JS 가 부르는 API 71경로 모두 서버에 존재.
- Edge headless: `/` DOM 52KB, 콘솔 오류 0(정보성 `Password field is not contained in a form` 만), 사용자 배지·상태 줄 채워짐, 신규 요소(`btn-q-expect`, `q-llm-report`, `build-channels`, `bc-fts`, `sec-perms`, `sec-keys`, `fx-docs`) 렌더.

## 4. 검증 중 발견·수정한 결함

| # | 증상 | 원인 | 수정 |
|---|---|---|---|
| 1 | 잘못된/폐기된 API 키(`Bearer lwk_x_y`)로 `/mcp` 를 부르면 **200**(게스트 viewer 로 동작) | `Auth.identify()` 가 키 검증 실패를 익명 접속으로 흘려보냄 → MCP 클라이언트가 키 문제를 알 수 없고, 폐기한 키가 계속 viewer 로 동작 | `identify()`: `lwk_` 접두 토큰이 맞지 않으면 401 (`API 키가 유효하지 않습니다`). 서버 GET/POST/`/mcp` 에서 그 AuthError 를 `_deny` 로 처리. 다른 형식의 Bearer(프록시 토큰 등)는 종전대로 무시. `tests/test_auth.py` 갱신 |
| 2 | `mcp --client-config` 의 `stdio_json.command` 가 `C:\\\\Users…`(이중 이스케이프) | 문자열을 미리 `\\` 로 바꾼 뒤 `json.dumps` 가 다시 이스케이프 | `client_config_snippets` 가 원본 경로를 넣고 dumps 에 맡김; `http_claude_code` 한 줄 명령·`_how` 추가 |

그 외 하네스 자체의 기대값 오류 2건(리스트 응답 요약, 콘솔 `serve` 는 admin 등급이라 428 이 정상)은 하네스를 고쳤다.

## 5. 기능별 검증 매핑 (2026-09-14 요청 6항목)

| 요청 | 어디서 검증했나 | 근거 |
|---|---|---|
| 1. 다중 사용자 권한(6역할·권한 표·익명 viewer·admin 계정) | test_auth 8개 · Web §3.1~3.3 · CLI 게이트 9개 | 게스트 200/401 표, viewer 403, class1 428→200, admin 권한 표 편집·users·apikeys, CLI 종료 코드 5 |
| 2. MCP(다수 LLM, 같은 PC/원격, Windows/Linux) | McpHttpTest · Web §3.3 · CLI mcp stdio/브리지/`--client-config` | 9도구 호출, 401/405/DELETE, 브리지 오류 변환, 클라이언트 설정 4종 출력 |
| 3. fts/vector/graph 별도 빌드 + 토글 | ChannelBuildTest · CLI `build fts|vector|graph` · Web 채널 빌드 job | 채널 빌드 후 다른 채널 행 수 불변, verify alerts 0 |
| 4. 같은 문서 관련 청크 포함(토글) | DocExpandTest · CLI `--no-doc-expand`/프리셋 · 브라우저 렌더 | trace 의 doc_expand 단계, 결과 hits 의 kind=doc_expand, 프리셋 speed/token 은 off |
| 5. headless 재시도(5분×3, 설정 파일) | LlmRetryTest(mock `--sleep/--fail-times/--empty`) · test_providers | `agents.json timeout_s/retries/retry_on`, `config llm_retries`, 최종 실패 시 `llm_report`·답변 배너·빌드 alerts |
| 6. 포렌식 + 기대 결과 분석(모든 질의) | ForensicExpectTest · CLI `forensic expect` 6변형 · Web `/api/forensic/expect`(게스트 가능) · MCP `wiki_forensic` | 단계별 탈락 지점·수정안, `--propose` 로 제안, 캐시/사전계산 요청은 원 요청으로 추적 |

## 6. 다시 실행하는 법 (다른 환경)

```bat
python -m unittest discover -s tests            :: 87개 (약 40초)
python tools\verify\verify_cli.py               :: 184 명령 (4~6분, 임시 폴더 사용)
python tools\verify\verify_web.py               :: 210 요청 (3~5분, 포트 8792 사용)
python tools\verify\verify_ui_wiring.py         :: 1초
python tools\verify\verify_browser.py           :: Edge/Chrome 필요 (없으면 SKIP), 포트 8793
```
포트가 겹치면 스크립트 상단 `PORT` 를 바꾼다. Linux/macOS 는 `python3`. 결과 파일 `tools/verify/verify_cli_result.json`, `verify_web_result.json` 은 git 에 넣지 않는다.

## 8. 상세 분석 모드 (같은 날 추가, [ANALYSIS_MODE.md](ANALYSIS_MODE.md))

| 검증 | 내용 | 결과 |
|---|---|---|
| `tests/test_analysis.py` (6) | 토글 → debug_level 2 · `result.analysis` md/json 경로(로그 폴더 아래) · 리포트 §0~§9+부록 A/B 존재 · 샘플/타임라인/최종 근거/융합 order/LLM 호출 · 토글 off 면 없음; 저장된 요청 `analyze`(focus 별 섹션·last·없는 id 오류·캐시 결과 안내); 약한 질의의 품질 렌즈 error/warn + 조절점 현재값; CLI `query --analyze`/`analyze last|--print|--out|--json|없는 id→1`/read 등급; MCP `wiki_analysis`; Web `overrides.analysis_mode` → `GET /api/analysis` json/md(download 헤더)/full/404 | 6 / 6 |
| CLI 하네스 추가 행 | `query --analyze --focus tokens`(리포트 경로 출력) · `analyze last` · `analyze last --print --focus speed`(속도 렌즈만, 108KB) · `analyze 999999`(→1) · `analyze --json`(timeline·lenses) | 184 / 184 |
| Web 하네스 추가 행 | 게스트 `POST /api/query overrides.analysis_mode`(detail 2, md 저장) · `GET /api/analysis` json(품질 렌즈)·md download·404 · MCP `wiki_analysis(focus=speed)` | 210 / 210 |
| 실 LLM (Ollama llama3.1) | `query "ISSUE-2001 의 원인과 수정 CL 은?" --analyze` → 25.8초 중 LLM 25.3초, claim_check 46%·answer 26%, 토큰 9,910(claim_check 52%) — 렌즈가 `claim_check_llm`·`answer_refine`·`context_*` 를 조절점으로 제시 | OK |

발견·수정 1건: 실 LLM 실행에서 LLM 대기가 총 시간의 198% 로 표시 — 루트/부모 단계의 counters(자식 합계)를 함께 세던 이중 계산 → 잎 단계만 집계.

## 7. 다른 RAG 연동 · MCP 확장 (같은 날 추가, [RAG_FEDERATION.md](RAG_FEDERATION.md))

| 검증 | 내용 | 결과 |
|---|---|---|
| `tests/test_rag_federation.py` (7) | stdio 소스 retrieve → `external_rag` 채널 융합 → `external_inject` → 로컬 리랭크 → 최종 hits 에 `ext:mock:ISSUE-9001`(why `ext_mock#1`,`ext_inject`, `external.source`) · 토글 off 시 없음 · inject=0 이면 순수 RRF 경쟁 · 소스 오류(연결 불가 REST) 격리 · `when=fallback` · REST 전송(`--mock-rest` 서브프로세스: retrieve·가중 1.5·expose 로 `kb__search`·GET) · 플러그인(로드/밑줄 무시/이름 충돌 오류/재적재/예시 파일) · http 전송(우리 자신을 원격으로: `test_sources` ok, retrieve, 잘못된 토큰 401 격리, 페더레이션 `peer__wiki_status`, 하위 호출에서 페더레이션 숨김·거부) | 7 / 7 |
| CLI 하네스 추가 행 | `mcp-source tools mock` · `tools nope`(1) · `retrieve --source mock`(ext 1건) · `federated` · `query --external-rag --no-rerank-llm --json`(hits 에 ext + ext_inject) · `query --no-external-rag`(ext 없음) · stdio `mcp` 에 `LLMWIKI_TOGGLE_MCP_FEDERATION=1` → tools 12 + `mock__search` 호출 성공 + `wiki_sources` | 184 / 184 |
| Web 하네스 추가 행 | `/api/mcp_sources` retrieve(ext:mock 포함) · tools · tools(unknown)→400 · federated · 게스트 retrieve→401(run 등급) · `/api/query` overrides `{external_rag,rerank_llm:false}` 로 hits 에 `ext:mock:` · `/mcp tools/list` 12개 + `mock__search` · `mock__search` 호출(ISSUE-9002) · `wiki_external_search` · 깊이 헤더 요청에는 페더레이션 도구 없음 | 210 / 210 |

발견·수정 2건: (1) 자기 자신/상호 expose 시 `tools/list` 무한 재귀 → 타임아웃(→ `X-LLMWiki-Federation-Depth` 헤더·`LLMWIKI_FEDERATION_DEPTH` 환경변수 가드), (2) 단일 리스트인 외부 채널이 RRF 에서 내부 리스트 11개에 항상 밀려 리랭크 후보에 못 들어감(→ `external_rag_inject` 보장 주입 + 로컬 리랭크 consensus 를 소스 내 순위로 대체).
