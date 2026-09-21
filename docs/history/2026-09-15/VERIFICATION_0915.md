# VERIFICATION 2026-09-15 — Web UI · CLI 전 기능 검증 보고서

> 대상: 이 시스템을 다른 환경에 bring-up 한 뒤 "모든 기능이 실제로 동작하는가"를 같은 방법으로 다시 확인하려는 엔지니어/LLM. 검증에 쓴 스크립트는 저장소에 들어 있다(`tools/verify/`, [tools/verify/README.md](../../../tools/verify/README.md)). 2026-09-14 에 추가한 6개 기능(권한 표·MCP·채널 빌드·doc_expand·LLM 재시도·기대 결과 포렌식)의 설계는 [IMPLEMENTATION_PLAN_0914.md](../2026-09-14/IMPLEMENTATION_PLAN_0914.md), 설정 위치 총람은 [BRINGUP_GUIDE.md](../../BRINGUP_GUIDE.md) §3.2.

## 0. 결과 요약

| 검증 | 방법 | 결과 |
|---|---|---|
| 단위/통합 테스트 | `python -m unittest discover -s tests -p "test_*.py"` (15 파일) | **148 / 148 통과** (88초) |
| CLI 전수 | `tools/verify/verify_cli.py` — 격리 임시 환경에서 모든 하위 명령을 subprocess 로 실행 | **222 / 222 통과** |
| Web API 전수 | `tools/verify/verify_web.py` — 실제 `serve --host 0.0.0.0` 에 게스트/viewer/class1/admin/API 키로 요청 | **255 / 255 통과** |
| UI 배선(정적) | `tools/verify/verify_ui_wiring.py` — JS `$('#id')` id ↔ HTML, 탭 ↔ 섹션, JS 가 부르는 API 경로 ↔ 서버 | **OK** (누락 0) |
| 브라우저 로드 | `tools/verify/verify_browser.py` — Edge headless 로 `/`·`/login` 실제 렌더, 탭 31개, 콘솔 오류 수집 | **OK** (JS 오류 0) |
| **Web UI 버튼 전수** | `tools/verify/verify_buttons.py` — 격리 환경(설정·DB 사본 + mock LLM)에서 **버튼을 하나씩 실제로 클릭**. 콘솔 오류·"아무 일도 안 일어남"·기대 조건을 본다 | **99 / 99 통과** (`--heavy` 로 전체 빌드·평가·스냅샷까지 포함) |
| 무작위 입력 내성 | `tools/verify/verify_monkey.py` — Web·MCP·CLI 에 무작위 경로·본문·타입·유니코드·거대 본문을 동시에 투입 (1,205 요청) | **500 오류 0 · 서버 생존 · CLI traceback 0** (§4.1 에서 결함 14건을 찾아 수정한 뒤). 폭격 직후의 503 은 대기열이 빠지는 중의 정상 거절이라 판정에서 제외 |
| 동시 사용 스트레스 | `tests/test_concurrency_0915.py` — 30 동시 질의, 빌드 중 질의, 긴 작업 취소, 설정 파일 동시 저장, 본문 크기 상한 | **43 / 43 통과** (30 동시 질의 평균 43ms · p95 62ms) |
| 터미널 인코딩 | `tests/test_console_0915.py` — 좁은 인코딩(`PYTHONIOENCODING=ascii`/`cp949`)을 강제한 실제 CLI 실행 | **8 / 8 통과** |
| 실 LLM 연결 | 로컬 Ollama(llama3.1) `models test --live`, headless 는 mock 에이전트로 재시도/타임아웃 경로 | OK (사내 게이트웨이·opencode 는 포팅 환경에서 `models test --live` 로 재확인) |

검증 중 발견해 수정한 결함은 1차 2건(§4)과 다중 사용자 서버화 이후 2차 13건(§4.1)이다. 검증 환경: Windows 11, Python 3.14.7. 격리 하네스는 합성 모뎀 코퍼스 38문서·190청크에 `llm_provider=mock`·`embed_provider=hash(256d)`; 브라우저·멍키 검사는 실제 DB 사본(RFC 163편 + 변환 문서 187개, 문서 352·청크 16,882)을 쓴다.

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

### 4.1 다중 사용자 서버화 이후 추가 검증에서 발견·수정한 결함 (같은 날 2차)

무작위 입력 하네스(`verify_monkey.py`)와 30명 동시 사용 스트레스 테스트를 돌려 찾은 것들이다. 모두 회귀 테스트로 고정했다.

| # | 증상 | 원인 | 수정 | 회귀 테스트 |
|---|---|---|---|---|
| 1 | `/api/query` 의 `overrides` 에 문자열·숫자를 보내면 **500** | `apply_overrides` 가 검증 없이 `.items()` 호출 | 경계에서 타입 검증(`_as_dict`) → 400 과 사유 | `test_malformed_input_no_500` |
| 2 | `/api/query_rules` 에 `{"rules": 3.14}` 를 저장하면 **이후 모든 질의가 깨짐** | 사전이 아닌 값이 `query_rules.json` 에 그대로 저장됨 | 저장 전 검증 + 원자적 교체 + 손상된 파일은 기본 규칙으로 복구하고 error 로그 | 같은 테스트의 `query_rules` 행 |
| 3 | `query_id`·`name`·`id`·`kind` 를 빼고 부르면 **500** | `body["key"]` 직접 접근 | `_as_int`/`_as_str`/`_as_dict` 로 400 + 어떤 필드가 필요한지 안내 | 같은 테스트 |
| 4 | `/api/cli` 에 `argv` 를 객체로 주면 **500** | dict 를 리스트처럼 인덱싱 | `_as_argv` 검증 | 같은 테스트 |
| 5 | `/api/models/catalog` 에 `model` 을 문자열로 주면 **500** | dict 가정 | 타입 검증 | 같은 테스트 |
| 6 | 스냅샷 복원 중 파일 잠금으로 복사가 실패하면 **서버 전체가 죽음**(이후 모든 요청이 `Cannot operate on a closed database`) | `evolve._restore` 가 Store 를 닫은 뒤 교체에 실패하고 재할당하지 않음 | `Store.reopen(before=…)` — 연결을 모두 닫고 파일 교체를 재시도한 뒤 세대 번호를 올려 스레드별로 다시 연결. 실패해도 원래 DB 로 계속 동작 | Web 하네스의 스냅샷 복원 행 25개 |
| 7 | 서버 워처가 쓰기 중일 때 CLI 질의가 `database is locked` 로 **실패** | 관측용 기록(요청 프로파일·질의 로그)이 잠금을 못 얻으면 예외를 그대로 던짐 | 관측 기록은 실패해도 경고만 남기고 질의는 정상 반환. `db_busy_timeout_s` 기본값 60초 | `test_console_0915`(실제 서버 가동 중 실행) |
| 8 | 두 관리자가 같은 순간에 설정을 저장하면 **500** (`[WinError 5] 액세스가 거부되었습니다`) | 모든 저장이 `<파일>.tmp` 라는 **고정된 이름**을 써서 서로의 임시 파일을 덮었고, Windows 는 다른 스레드가 열고 있는 파일의 교체를 거부한다 | `llmwiki/atomicio.py` — 임시 파일 이름을 호출마다 다르게, 경로별 락으로 읽기·쓰기 직렬화, 교체 실패 시 재시도. 설정 파일 읽기·쓰기 13곳을 전부 이 경로로 통일 | `AtomicWriteTest` 2개 |
| 9 | `/api/agents` 저장이 `_comment` 키 때문에 **400** | 새로 넣은 검증이 설명용 `_comment` 를 에이전트 항목으로 취급 | `_` 로 시작하는 키는 건너뛴다 | Web 하네스 `admin agents save` |
| 10 | 느린 CLI 명령 두 개가 읽기 슬롯을 6분 넘게 잡아 **다른 사용자 64명이 전부 대기열에 쌓임** | Web 콘솔이 부르는 CLI 의 시간 제한 `timeouts.cli_s` 기본값이 0(무제한)이었다 | 기본값을 600초로. 빌드처럼 오래 걸리는 CLI 는 weight 가 soft/exclusive 이므로 `job_s`(기본 무제한)를 따르게 분리 | 멍키 하네스의 폭격 후 정상 질의 |
| 11 | `analyze notanumber` 가 **파이썬 traceback 을 그대로 출력** | `int(ns.target)` 을 검사 없이 호출 | `analyze` · `logs --request` · `evolve apply/reject/feedback` 의 숫자 인자를 검사해 사용법을 안내하고 종료 코드 1 | 멍키 하네스의 CLI 퍼징 |
| 12 | 짝 없는 서러게이트(`\ud83d`)가 섞인 질의 **한 건 뒤로 서버 모니터가 계속 400** | 그 문자열이 요청 라벨로 저장되고, `/api/activity` 를 JSON 으로 만들 때마다 `UnicodeEncodeError` — 이력에서 빠질 때까지 admin 이 아무것도 볼 수 없었다. SQLite 도 이런 문자열을 저장하지 못한다 | 네 겹으로 막았다: 요청 본문을 경계에서 정화(`_scrub`, MCP 는 `_scrub_surrogates`), 요청 라벨·클라이언트 정보를 등록 시점에 정화(`reqmgr.safe_text`), 진행 레지스트리도 동일, 마지막 안전망으로 JSON 응답을 `errors="replace"` 로 인코딩 | `SurrogateSafetyTest` 3개 |
| 13 | 3MB 본문 하나가 **30초 넘게 슬롯을 잡음** | 요청 본문 크기 상한이 없어 전부 읽고 파싱한 뒤에야 거절 | `concurrency.max_body_mb`(기본 8MB) — 넘으면 본문을 읽지 않고 413 `body_too_large` | `test_body_size_is_capped` |
| 14 | **전체 리빌드가 청크 수의 제곱으로 느려짐** (`fts_trigram` 을 켜면 특히). 16,882 청크에서 색인 단계만 수십 분 | `chunks_fts`·`chunks_tri` 는 `chunk_id` 가 UNINDEXED 라 `DELETE … WHERE chunk_id=?` 가 매번 **FTS 전체 스캔**이다. 청크마다 두 번(삭제 + 재삽입 전 정리) 실행하니 O(N²). `relations.chunk_id` 에는 인덱스가 아예 없어 같은 문제가 한 번 더 있었다 | ① 전체 리빌드는 `clear_fts()` 로 한 번에 비운다 ② 증분은 `WHERE doc_id=?` 로 **문서당 한 번** ③ `idx_rel_chunk` 인덱스 추가 ④ 부수 테이블 삭제를 인덱스를 타는 서브쿼리로 | `FtsRebuildTest` 4개 (전체 리빌드 2회 후 행 수 동일, 증분 후 옛 본문 미잔존, 삭제 문서 잔존 0, 관계 삭제가 인덱스 사용) |
| 15 | 완료된 질의가 활동 목록에 **0.0s** 로 보여 "실행이 안 된 것 아니냐"는 오해 | 답변 캐시·사전계산 적중은 실제로 1ms 안팎인데, 서버가 초 단위 소수 1자리로 반올림해 0.0 이 되고 이유도 표시되지 않았다 | 완료 시간을 소수 3자리로 전달하고, UI 는 1초 미만을 `3 ms` 로 표기. 캐시·사전계산 적중은 `캐시`/`사전계산` 배지로 이유를 함께 표시 | 실측: 캐시 적중 `elapsed_s=0.003 note=사전계산` |
| 17 | 답변에 **같은 구절이 수십 번 반복**되어 나옴 | 작은 모델(llama3.1)이 긴 컨텍스트에서 반복 루프에 빠지는 고장. 스트리밍을 쓰지 않으므로 클라이언트 누적 오류가 아니라 모델 출력 자체였다. 게다가 그 답변이 **사전계산 캐시에 저장**되어 같은 질문이 계속 1ms 만에 고장 답변을 돌려줬다 | ① `answer.find_repeat_loop` 로 줄·구절 반복을 탐지해 잘라내고 왜 잘렸는지 답변과 상단 배너에 표시 ② 그런 답변은 메모리·영속 캐시 **양쪽 모두에 저장하지 않는다** ③ 반복 억제 파라미터를 기본 적용(`llm_frequency_penalty` 0.3 · `llm_repeat_penalty` 1.1) ④ 이미 저장된 것은 `precompute check` 로 찾고 `precompute clear --broken` 으로 지운다 | `RepeatLoopGuardTest` 4개(정상 답변·표·목록을 오판하지 않는지 포함). 실측: 실제 캐시에서 고장 답변 3건 발견·제거 |
| 18 | 메모리 화면의 '에피소드' 수 자리에 `[object Object],[object Object]…` | `/api/memory` 가 `dict(status, episodes=목록)` 으로 **개수 키에 목록을 덮어썼다** | 목록은 `recent` 로 분리하고 `episodes` 는 개수로 유지 | Web 하네스 `/api/memory` |
| 19 | 포렌식 `진단 실행` 버튼을 눌러도 아무 일도 없음 | request id 입력칸이 비면 `if (!id) return;` 으로 **조용히 종료** | 목록에서 고른 행 → 마지막 질의 → 최근 기록 순으로 id 를 찾아 쓰고, 무엇을 썼는지 입력칸에 채워 준다. 정말 없으면 안내 토스트 | 실측: 목록 첫 행 request #2235 로 진단 실행 → 판정 sufficient·소견 1건 |
| 20 | `query_cache` 를 껐는데도 응답이 계속 0초 | 캐시가 **둘**이다. `query_cache`(메모리)와 `precompute`(SQLite answer_cache, 영속). 하나만 꺼서는 다른 하나가 계속 답한다 | 동작은 의도된 2단 구조라 유지하고, 두 토글의 설명에 서로의 존재를 명시 | 실측: query_cache 만 끄면 2회차 0.85ms(precomputed=true), 둘 다 끄면 매번 31~42초 실측 |
| 21 | 질의 로그의 `trace` 버튼을 눌러도 아무 일도 없어 보임 | 동작은 했지만 결과가 60행짜리 표 **아래**에 그려져 화면 밖이었다 | 그린 뒤 결과 위치로 스크롤 | `verify_click.py` |
| 22 | `POST /api/profile` 에 잘못된 action 을 보내면 **저장해 둔 설정이 통째로 지워짐** | action 이 `reset` 이 아니면 전부 '저장' 으로 흘러가, `profile` 이 없으면 빈 프로파일을 덮어썼다 | `save`/`reset` 만 허용하고 빈 `profile` 은 400. 불러오기는 GET | 실측: 잘못된 action·빈 profile 모두 400, 그 뒤에도 설정 7개 키 그대로 |
| 24 | `?limit=999…999` 처럼 자리수가 큰 정수를 주면 **500** | 파이썬 정수는 자리수 제한이 없어 그대로 SQLite 로 넘어가 `OverflowError: Python int too large to convert to C…` | 질의 문자열의 정수 파라미터 20곳을 `_qint` 로 통일해 범위를 자르고(0~1,000,000), `OverflowError` 도 400 으로 매핑 | `test_query_string_ints_are_clamped` · 실측: 거대 값 10종 모두 200/404 |
| 27 | 출력 토큰 상한을 **answer 만** 조절할 수 있었다 | 라우터 200·확장 400·리랭크 400·검증 500/1500·요약 800·추출 4000 이 코드에 박혀 있어, 단계별로 품질·비용을 조절할 수 없었다 | `llm_roles.<role>.max_tokens` 추가. 지정하지 않으면 단계 기본값을 그대로 쓴다(동작 불변) | `RoleTokensAndDocExpandTest` 2개 + 실측: expand/rerank/verify/answer 에 설정값이 그대로 실림 |
| 28 | 역할별 시간 제한이 **전부 600초** | 전역 기본값만 쓰고 있어, 라우터 한 번이 멈추면 질의가 10분 붙잡혔다(재시도까지 40분) | 단계 성격에 맞게 배포 기본값을 넣었다 — 보조 단계는 짧게(expand 30s/1회) 핵심은 넉넉히(answer 240s/2회) 배치는 길게(review 300s/2회), `budget_s` 로 재시도 포함 상한 | `models policy` 표 · LLM 을 모두 실패시켜도 추출식 답변 1,260자·근거 8건 반환 |
| 29 | 근거 문서를 **통째로 읽힐 방법이 없었다** | `doc_expand` 는 점수로 고른 일부만 넣는다. 설계 문서·절차서처럼 문서 하나를 끝까지 봐야 하는 질문에 부족 | `doc_expand_mode=full` 추가 — 점수로 거르지 않고 문서 순서대로 전부(상한까지) | `test_doc_expand_full_reads_whole_document` (full 이 hybrid 보다 많이 넣고, 남은 청크를 상한까지 모두 넣는지) |
| 26 | admin 이 **일반 사용자 화면을 확인할 방법이 없었다** | `mode: auto` 는 127.0.0.1 접속에서 인증을 꺼 항상 admin 이라, viewer 화면을 보려면 서버를 다시 띄워야 했다 | 헤더의 `👁 권한 보기` — 역할을 **낮추기만** 하는 미리보기(`/api/auth/preview`). `_user()` 한 곳에서 적용되어 화면뿐 아니라 실제 권한 검사에 반영된다 | `RolePreviewTest` 4개 + 실측: admin→viewer 시 빌드 403·질의 200, 진짜 viewer 의 `preview=admin` 과 **쿠키 위조** 모두 viewer 유지 |
| 25 | 평가(eval) 4개가 동시에 돌아 **Web UI 가 응답하지 않음** | eval·trial 은 안에서 질의를 여러 번 도는 배치 작업인데, 한 번에 수십 분 읽기 슬롯을 물고 있어 대화형 질의가 전부 대기열로 밀렸다(실측: 4개가 19분째 점유, 대기 6건) | `concurrency.max_parallel_batch`(기본 1) — 배치 작업만 따로 세어 제한한다. 대화형 질의는 그 동안에도 슬롯을 받는다 | `test_batch_jobs_do_not_hog_read_slots` |
| 23 | 분석 리포트 LLM 소견이 **엉뚱한 형식**(질의 확장·claim 검증 JSON)으로 옴 | 리포트 전문에 다른 작업의 프롬프트와 출력 예시가 들어 있어, 작은 모델이 지시 대신 그 예시를 따라갔다 | 리포트 전문 대신 **우리가 계산한 수치·소견만 추린 요약(≤5KB)** 을 넘기고, 모델마다 다른 필드 이름을 정규화. 형식을 못 지키면 원문을 함께 보여 준다 | 실측: llama3.1 이 실제 수치·설정 이름을 근거로 소견 5건 반환 |
| 16 | 질의를 던지면 **화면이 한참 멈췄다가 한꺼번에 갱신**됨 | 서버가 `BaseHTTPRequestHandler` 기본값인 **HTTP/1.0** 이라 응답마다 연결을 끊었다. 브라우저는 한 사이트에 동시 연결을 6개까지만 열기 때문에, 오래 걸리는 질의 3개가 연결을 물고 있으면 2초 폴링이 브라우저 안에서 줄을 서다가 질의가 끝나는 순간 한꺼번에 처리됐다. 화면 4곳(헤더 HUD·질의 요약·진행 중 작업·빌드)이 **각자** `/api/activity` 를 부르고 있어 더 빨리 포화됐다 | ① `protocol_version = "HTTP/1.1"` + 유휴 연결 정리(`concurrency.keep_alive_s`, 기본 30초)로 연결 재사용 ② `/api/activity` 폴링을 **하나로 합쳐** 구독자에게 나눠 준다(4회 → 1회) ③ 모든 주기 갱신에 "응답이 늦으면 건너뛰기" 가드 | 실측: 한 연결에서 연속 4요청 성공, 질의 3개가 도는 중 `/api/activity` 응답 13~23 ms (이전에는 질의가 끝날 때까지 대기) |

### 4.2 전체 리빌드 속도 (결함 #14 수정 전후 실측)

`tools/verify/bench_fts.py` 로 전체 리빌드의 색인 단계(문서 재삽입)만 떼어 측정했다. 숫자는 초.

| 청크 | fts_trigram | 수정 전 | 수정 후 | 배수 |
|---|---|---|---|---|
| 2,880 | off | 17.2 | 0.3 | 57× |
| 2,880 | **on** | 31.2 | 0.7 | 43× |
| 5,760 | off | 80.7 | 0.9 | 90× |
| 5,760 | **on** | 121.7 | 1.2 | 98× |
| 11,520 | off | 310.7 | 1.7 | 182× |
| 11,520 | **on** | 477.4 | 2.6 | 185× |

수정 전은 청크가 2배면 시간이 4배가 된다(제곱). 수정 후는 청크에 비례한다. 실제 코퍼스(16,882 청크, trigram on)로 환산하면
색인 단계가 약 17분에서 몇 초로 줄어든다. `fts_trigram` 이 특히 느렸던 이유는 trigram 색인이 본문보다 2~3배 커서
스캔 한 번의 비용 자체가 컸기 때문이지, trigram 생성이 느려서가 아니다.

멍키 하네스 자체도 두 곳 고쳤다. 짝 없는 서러게이트를 URL 로 만들 때 **클라이언트**가 `UnicodeEncodeError` 로 죽던 것(대체 문자로 낮춰 서버에는 그대로 전달), 그리고 폭격 직후의 사후 확인이 아직 배수되지 않은 대기열 때문에 503 을 받던 것(대기열이 빌 때까지 기다린 뒤 확인하고, 실패하면 진행/대기 건수를 근거로 남김).

> 폭격 중에 나오는 429·503 은 결함이 아니라 **정상 동작**이다. 용량을 넘은 요청을 무한정 기다리게 하지 않고 재시도 시점과 함께 거절하는 것이 설계다. 결함으로 세는 것은 500(서버 예외), 서버 사망, 연결 끊김(시간 초과 제외), CLI 의 traceback 유출 네 가지다.
>
> 폭격 **직후**의 503 도 같다. 1,200건을 쏟아붓고 수십 건을 중간에 끊으면 버려진 요청의 서버 스레드가 `queue_timeout_s`(120초)로 정리될 때까지 대기열이 차 있다. 그래서 '폭격 후 정상 질의' 는 보고만 하고 판정에는 넣지 않는다. 활동 목록이 대기열을 정확히 보고하는지는 따로 실측했다 — 40스레드 부하에서 `/api/activity` 의 running 8 · queued 32 가 `server stats` 와 정확히 일치했다.

## 5. 기능별 검증 매핑 (2026-09-14 요청 6항목)

| 요청 | 어디서 검증했나 | 근거 |
|---|---|---|
| 1. 다중 사용자 권한(6역할·권한 표·익명 viewer·admin 계정) | test_auth 8개 · Web §3.1~3.3 · CLI 게이트 9개 | 게스트 200/401 표, viewer 403, class1 428→200, admin 권한 표 편집·users·apikeys, CLI 종료 코드 5 |
| 2. MCP(다수 LLM, 같은 PC/원격, Windows/Linux) | McpHttpTest · Web §3.3 · CLI mcp stdio/브리지/`--client-config` | 9도구 호출, 401/405/DELETE, 브리지 오류 변환, 클라이언트 설정 4종 출력 |
| 3. fts/vector/graph 별도 빌드 + 토글 | ChannelBuildTest · CLI `build fts|vector|graph` · Web 채널 빌드 job | 채널 빌드 후 다른 채널 행 수 불변, verify alerts 0 |
| 4. 같은 문서 관련 청크 포함(토글) | DocExpandTest · CLI `--no-doc-expand`/프리셋 · 브라우저 렌더 | trace 의 doc_expand 단계, 결과 hits 의 kind=doc_expand, 프리셋 speed/token 은 off |
| 5. headless 재시도(5분×3, 설정 파일) | LlmRetryTest(mock `--sleep/--fail-times/--empty`) · test_providers | `agents.json timeout_s/retries/retry_on`, `config llm_retries`, 최종 실패 시 `llm_report`·답변 배너·빌드 alerts |
| 6. 포렌식 + 기대 결과 분석(모든 질의) | ForensicExpectTest · CLI `forensic expect` 6변형 · Web `/api/forensic/expect`(게스트 가능) · MCP `wiki_forensic` | 단계별 탈락 지점·수정안, `--propose` 로 제안, 캐시/사전계산 요청은 원 요청으로 추적 |

## 5.1 기능별 검증 매핑 (2026-09-15 다중 사용자 요청 8항목)

설계는 [IMPLEMENTATION_PLAN_0915.md](IMPLEMENTATION_PLAN_0915.md), 운영은 [CONCURRENCY.md](../../CONCURRENCY.md)·[SCHEDULER.md](../../SCHEDULER.md).

| 요청 | 어디서 검증했나 | 근거 |
|---|---|---|
| 1. CLI·Web·MCP 병렬 처리 (읽기/쓰기 분리, 필요하면 큐) | `IsolationTest` 6개 · `RequestManagerTest` 11개 · `StressTest` | 동시 12질의가 서로의 top_k·토글·튜닝·카운터를 오염시키지 않음, 스레드별 DB 연결, 읽기는 병렬·쓰기는 배타, 증분 빌드 중에도 질의 200, 대기열이 차면 503 |
| 2. 역할별 LLM 타임아웃·재시도·백오프 + 최종 실패해도 최선의 결과 | `RolePolicyTest` 7개 | `llm_roles.<role>` 이 실제 LLM 인스턴스에 반영, 지수 백오프 간격, `budget_s` 초과 시 중단, 연속 실패 n회 후 회로 열림·cooldown 후 복귀, LLM 을 전부 실패시켜도 답변이 나오고 `llm_report` 에 대체 경로가 기록 |
| 3. 30명 동시 + 관리자 모니터링·제한·세션·속도 제한 | `StressTest` · Web 하네스 `server` 계열 행 · `RequestManagerTest` | 30 동시 질의 평균 43ms·p95 62ms, 속도 제한 초과 429, 점검 모드 503, IP·사용자 차단, viewer 는 활동 목록만 보이고 IP·통계는 admin 만, 제한 변경이 `server.json` 에 저장 |
| 4. 진행률(%·경과)과 중지 (CLI·Web) | `CancelTest` 4개 · Web 하네스 빌드/취소 행 · 브라우저 렌더 | 실행 중 질의·빌드 취소가 `Cancelled` 로 끊기고 빌드는 진행분을 저장, watchdog 시간 초과, 취소 직후 빌드·질의가 정상 복구, 진행 패널의 단계·%·ETA·로그 렌더 |
| 5. 스케줄러 (증분 빌드·URL 수집·스크립트·skill/LLM) | `SchedulerTest` 9개 · CLI `schedule *` · Web 설정 › 스케줄 | 5필드 cron 파싱(`*/n` `a-b` `a,b` 요일명), 동작 19종 각각 실행, 잘못된 작업은 INVALID 로 표시하되 나머지는 계속, 실행 이력 기록, 파일 저장 시 자동 재적재 |
| 6. 쓸 수 있는 LLM 목록 → Web 드롭다운 · CLI | `ModelCatalogTest` · CLI `models list/catalog/discover/policy` · 브라우저 렌더 | 카탈로그 추가·삭제·역할/제공자 필터, 설정된 모델이 카탈로그에 없으면 표시, 드롭다운에 목록이 채워짐 |
| 7. viewer 로그인 왕복 후 메뉴가 깨지던 버그 | `verify_browser.py`(`/`·`/login` 실제 렌더, 콘솔 오류 0) · Web 하네스 401 행 | 401 이후에도 사이드바가 재구축되고, `#group/tab` 해시로 돌아온 자리가 유지되며, `/login` 이 히스토리에 쌓이지 않음 |
| 8. DEBUG 로그 기반 검증 + 스트레스 TC | `test_debug_logs_separate_requests` · `StressTest` 전체 | 동시 8질의의 run_id 가 겹치지 않고 로그가 요청별로 분리되며 DEBUG 레코드가 남음 |
| 추가. 터미널 한글 깨짐 | `tests/test_console_0915.py` 8개 | `PYTHONIOENCODING=ascii`/`cp949` 를 강제해도 CLI 가 종료 코드 0 으로 끝나고 출력이 유효한 UTF-8, `chcp 437` 에서도 한글 보존 |
| 추가. 무작위 입력 내성 | `tools/verify/verify_monkey.py` | Web·MCP·CLI 동시 폭격에서 500 오류 0, 서버 생존, 폭격 후 정상 질의 복구 |
| 추가. 범용 문서 변환의 무손실 | `tools/corpus_ingest.py --verify-only` | 변환 후 본문 SHA-1 재계산 151/151 일치, 텍스트 추출이 불가역인 형식은 `_originals/` 에 원본 보관 + manifest 에 명시 |

## 6. 다시 실행하는 법 (다른 환경)

```bat
python -m unittest discover -s tests -p "test_*.py"   :: 148개 (약 90초)
python tools\verify\verify_buttons.py           :: Web UI 버튼 전수 클릭 (6~8분, 격리 환경)
python tools\verify\verify_click.py             :: 중요한 클릭 흐름만 (40초)
python tools\verify\bench_fts.py --no-old       :: 리빌드가 청크 수에 비례하는지 (20초)
python tools\verify\verify_cli.py               :: 190 명령 (4~6분, 임시 폴더 사용)
python tools\verify\verify_web.py               :: 228 요청 (3~5분, 포트 8792 사용)
python tools\verify\verify_ui_wiring.py         :: 1초
python tools\verify\verify_browser.py           :: Edge/Chrome 필요 (없으면 SKIP), 포트 8793
python tools\verify\verify_monkey.py            :: 무작위 입력 내성 (약 3분, 포트 8794)
```
포트가 겹치면 스크립트 상단 `PORT` 를 바꾼다. Linux/macOS 는 `python3`. 결과 파일 `tools/verify/verify_cli_result.json`, `verify_web_result.json`, `verify_monkey_result.json` 은 git 에 넣지 않는다.

멍키 테스트는 기본적으로 **실제 DB 를 복사한 격리 서버**에 `llm_provider=mock` 으로 붙는다. 실제 LLM 으로 돌리려면 `--real-llm`, 파괴적 엔드포인트(스냅샷 복원·config reset 등)까지 포함하려면 `--destructive` 를 준다. 시드를 고정해 재현하려면 `--seed <숫자>`.

## 8. 상세 분석 모드 (같은 날 추가, [ANALYSIS_MODE.md](../../ANALYSIS_MODE.md))

| 검증 | 내용 | 결과 |
|---|---|---|
| `tests/test_analysis.py` (6) | 토글 → debug_level 2 · `result.analysis` md/json 경로(로그 폴더 아래) · 리포트 §0~§9+부록 A/B 존재 · 샘플/타임라인/최종 근거/융합 order/LLM 호출 · 토글 off 면 없음; 저장된 요청 `analyze`(focus 별 섹션·last·없는 id 오류·캐시 결과 안내); 약한 질의의 품질 렌즈 error/warn + 조절점 현재값; CLI `query --analyze`/`analyze last|--print|--out|--json|없는 id→1`/read 등급; MCP `wiki_analysis`; Web `overrides.analysis_mode` → `GET /api/analysis` json/md(download 헤더)/full/404 | 6 / 6 |
| CLI 하네스 추가 행 | `query --analyze --focus tokens`(리포트 경로 출력) · `analyze last` · `analyze last --print --focus speed`(속도 렌즈만, 108KB) · `analyze 999999`(→1) · `analyze --json`(timeline·lenses) | 184 / 184 |
| Web 하네스 추가 행 | 게스트 `POST /api/query overrides.analysis_mode`(detail 2, md 저장) · `GET /api/analysis` json(품질 렌즈)·md download·404 · MCP `wiki_analysis(focus=speed)` | 210 / 210 |
| 실 LLM (Ollama llama3.1) | `query "ISSUE-2001 의 원인과 수정 CL 은?" --analyze` → 25.8초 중 LLM 25.3초, claim_check 46%·answer 26%, 토큰 9,910(claim_check 52%) — 렌즈가 `claim_check_llm`·`answer_refine`·`context_*` 를 조절점으로 제시 | OK |

발견·수정 1건: 실 LLM 실행에서 LLM 대기가 총 시간의 198% 로 표시 — 루트/부모 단계의 counters(자식 합계)를 함께 세던 이중 계산 → 잎 단계만 집계.

## 7. 다른 RAG 연동 · MCP 확장 (같은 날 추가, [RAG_FEDERATION.md](../../RAG_FEDERATION.md))

| 검증 | 내용 | 결과 |
|---|---|---|
| `tests/test_rag_federation.py` (7) | stdio 소스 retrieve → `external_rag` 채널 융합 → `external_inject` → 로컬 리랭크 → 최종 hits 에 `ext:mock:ISSUE-9001`(why `ext_mock#1`,`ext_inject`, `external.source`) · 토글 off 시 없음 · inject=0 이면 순수 RRF 경쟁 · 소스 오류(연결 불가 REST) 격리 · `when=fallback` · REST 전송(`--mock-rest` 서브프로세스: retrieve·가중 1.5·expose 로 `kb__search`·GET) · 플러그인(로드/밑줄 무시/이름 충돌 오류/재적재/예시 파일) · http 전송(우리 자신을 원격으로: `test_sources` ok, retrieve, 잘못된 토큰 401 격리, 페더레이션 `peer__wiki_status`, 하위 호출에서 페더레이션 숨김·거부) | 7 / 7 |
| CLI 하네스 추가 행 | `mcp-source tools mock` · `tools nope`(1) · `retrieve --source mock`(ext 1건) · `federated` · `query --external-rag --no-rerank-llm --json`(hits 에 ext + ext_inject) · `query --no-external-rag`(ext 없음) · stdio `mcp` 에 `LLMWIKI_TOGGLE_MCP_FEDERATION=1` → tools 12 + `mock__search` 호출 성공 + `wiki_sources` | 184 / 184 |
| Web 하네스 추가 행 | `/api/mcp_sources` retrieve(ext:mock 포함) · tools · tools(unknown)→400 · federated · 게스트 retrieve→401(run 등급) · `/api/query` overrides `{external_rag,rerank_llm:false}` 로 hits 에 `ext:mock:` · `/mcp tools/list` 12개 + `mock__search` · `mock__search` 호출(ISSUE-9002) · `wiki_external_search` · 깊이 헤더 요청에는 페더레이션 도구 없음 | 210 / 210 |

발견·수정 2건: (1) 자기 자신/상호 expose 시 `tools/list` 무한 재귀 → 타임아웃(→ `X-LLMWiki-Federation-Depth` 헤더·`LLMWIKI_FEDERATION_DEPTH` 환경변수 가드), (2) 단일 리스트인 외부 채널이 RRF 에서 내부 리스트 11개에 항상 밀려 리랭크 후보에 못 들어감(→ `external_rag_inject` 보장 주입 + 로컬 리랭크 consensus 를 소스 내 순위로 대체).
