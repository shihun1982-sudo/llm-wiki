# 2026-09-15 구현 계획서 — 다중 사용자 서버화 (요청 분석 · 설계 · 대안 · 검증)

> 이 문서는 "무엇을 왜 그렇게 만들었는가"를 남긴다. 운영 절차는 [CONCURRENCY.md](../../CONCURRENCY.md) · [SCHEDULER.md](../../SCHEDULER.md),
> 검증 결과는 [VERIFICATION_0915.md](VERIFICATION_0915.md), 설정 위치 총람은 [BRINGUP_GUIDE.md](../../BRINGUP_GUIDE.md) §3.
> 앞선 세대: [IMPLEMENTATION_PLAN_0914.md](../2026-09-14/IMPLEMENTATION_PLAN_0914.md)(권한·MCP·채널 빌드·doc_expand·재시도·포렌식).

---

## 0. 요청과 판정

| # | 요청 | 판정 | 결과 |
|---|---|---|---|
| 1 | CLI·Web·MCP 요청을 병렬 처리 (read/write 를 나눠 락 구간을 짧게, 필요하면 큐) | ✅ 타당 — 당시 구조는 사실상 동시 처리 1 | 요청 격리 + 읽기/쓰기 락 + 대기열 (§1) |
| 2 | 역할별 LLM 의 timeout·retry·backoff 개별 지정, 최종 실패해도 최선의 결과 | ✅ | `llm_roles.<role>` 에 정책 8종 + 회로 차단 + 대체 경로 (§2) |
| 3 | 30명 동시, admin 이 요청·서버 상태 모니터링, IP/User/Client 제한·세션·rate | ✅ | 공통 요청 관리자 + `server.json` + 모니터 UI/CLI (§3) |
| 4 | 오래 걸리는 작업의 % · 경과 시간 표시와 중지 (CLI·Web 모두) | ✅ | 진행 레지스트리 확장 + 협조적 취소 + ETA (§4) |
| 5 | 주기 작업 (증분 빌드 · URL 수집 · 스크립트 · skill/LLM 호출) | ✅ | `schedule.json` 스케줄러 19종 동작 (§5) |
| 6 | 설정에 쓸 수 있는 LLM 목록을 두고 UI 드롭다운·CLI 로 제공 | ✅ | `models.json` 카탈로그 + 드롭다운 + `models list/catalog/discover` (§6) |
| 7 | viewer 로 접근 → 로그인 화면 → 뒤로가기 시 메뉴가 깨짐 | ✅ 재현·수정 | 원인 3가지 모두 수정 (§7) |
| 8 | DEBUG 로그로 검증, 스트레스 TC(오래 걸리는 빌드·질의, 동시 다수) 포함 | ✅ | 32개 동시성·스트레스 테스트 + 멍키 테스트 (§8) |
| 추가 | 다른 환경 터미널에서 한글이 깨짐 | ✅ 재현·수정 | 콘솔 인코딩 모듈 (§9) |
| 추가 | 실제 10MB 코퍼스로 시험, 형식 없는 문서도 넣을 수 있게 | ✅ | RFC 수집기 + 범용 변환기(무손실 검증) (§10) |
| 추가 | 무작위 입력 멍키 테스트 | ✅ | `verify_monkey.py` — 결함 6종 발견·수정 (§8.3) |
| 추가 | Web UI 사용성 (로그 유지·복사, 접기/펼치기, 복수 메뉴, 대기 표시, 계정별 프로파일) | ✅ | §11 |

---

## 1. 병렬 처리 — 격리가 먼저, 잠금은 그다음

### 그 전의 구조와 문제
`web/server.py` 의 전역 `_LOCK` 하나가 질의·빌드·조회·MCP 를 모두 감쌌다. 질의가 LLM 을 기다리는 20초 동안 모든 사용자가 대기했다.
잠금을 그냥 없애면 세 가지가 깨진다: (a) SQLite 연결 1개 공유 → 남의 트랜잭션을 commit, (b) 전역 Settings/Tuning → 요청끼리 설정 오염,
(c) 전역 프로파일 카운터 → 요청별 통계가 섞임.

### 선택한 설계
1. **요청 범위(request scope)**: `Pipeline.request_scope(overrides, presets, mode)` 가 요청마다
   Settings 사본(스레드 로컬) · 튜닝 오버레이 · 스레드 전용 DB 연결을 연다. Web/MCP/CLI 콘솔/스케줄러/워처가 모두 이걸로 감싼다.
2. **저장소**: `Store` 가 스레드별 연결 풀(WAL, `busy_timeout`)을 관리. 캐시(벡터 행렬·엔티티 인덱스·doc_meta)는 락으로 한 번만 적재해 공유.
3. **요청 관리자**(`llmwiki/reqmgr.py`): 읽기/쓰기 락(writer preference, soft 모드) · 동시 실행 슬롯 · 대기열 · 속도 제한 · 차단 · 활동 목록 · 취소.
4. **LLM 인스턴스 캐시**: (역할, provider, model, 정책) 서명 기준. 요청마다 다른 모델을 써도 서로 덮어쓰지 않는다.

### 검토했지만 택하지 않은 대안
| 대안 | 왜 안 했나 |
|---|---|
| 멀티프로세스(gunicorn 류) | 표준 라이브러리만 쓰는 이식성이 깨지고, 벡터 행렬(수백 MB)을 프로세스마다 중복 적재한다 |
| SQLite → 서버 DB(PostgreSQL) | 폴더 복사 한 번으로 옮기는 이식성을 잃는다. WAL + 연결 풀로 30명 규모는 충분 |
| 전역 락 유지 + 락 구간만 축소 | 질의 전체가 LLM 대기라 구간을 줄여도 효과가 없다. 격리 없이는 축소 자체가 위험 |
| asyncio 재작성 | 코드 전면 재작성 비용이 크고, 병목이 CPU 가 아니라 LLM 대기라 스레드로 충분 |

### 요청 단위 프리셋의 의미 변경 (호환성 주의)
`POST /api/presets {action:"apply", save:false}` 와 Web 콘솔의 `preset apply`(--save 없이)는 예전에 **서버 전역 메모리 설정**을 바꿨다.
다중 사용자에서는 한 사람이 모두의 설정을 바꾸는 셈이라 **미리보기**로 바꿨다: 응답에 적용 결과를 돌려주되 서버 상태는 그대로 두고,
영구 적용은 `save:true`(admin)로만. 질의에 프리셋을 쓰려면 `/api/query` 의 `preset`/`mode` 를 쓴다(요청 범위).

---

## 2. 역할별 LLM 정책

`Settings.role_llm(role)` 이 provider·model·effort 에 더해 **정책 8종**을 해석한다:
`timeout_s · retries · backoff(linear|exponential) · backoff_s · backoff_max_s · budget_s · circuit_failures · circuit_cooldown_s`.
우선순위는 `llm_roles.<role>.*` > `config.json` 전역(`llm_*`) > 코드 기본값이고, headless 는 `agents.json` 값이 전역보다 우선하되
역할에 명시하면 그것이 최우선이다.

- **backoff**: 기본을 지수 + 지터로 바꿨다. 선형이면 동시에 실패한 여러 요청이 같은 순간에 재시도해 게이트웨이를 다시 때린다.
- **budget_s**: 재시도까지 포함한 총 시간 상한. 없으면 최악의 경우 `(1+retries) × (1+http_retries) × timeout` 만큼 한 요청이 매달린다.
- **회로 차단**: 같은 provider/model 이 연속 n회 최종 실패하면 cooldown 동안 즉시 실패시킨다. 죽은 게이트웨이에 30명이 각각
  타임아웃을 기다리는 상황을 막는 것이 목적이다. 상태는 `server circuits` · Web 모델 탭에서 보이고 수동 해제할 수 있다.
- **최종 실패 시**: answer→추출식, rerank→로컬 휴리스틱, expand→규칙 확장만, verify→휴리스틱, extract→규칙 그래프, summary→생략.
  무엇이 실패해 무엇으로 대체했는지 `llm_report` 와 답변 상단 배너, 빌드 alerts 에 남는다(기존 메커니즘 유지·확장).

---

## 3. 요청 관리자와 운영 제어

`server.json` 한 파일로 동시성·시간 제한·속도 제한·세션·접근·모니터를 조절한다(환경변수 `LLMWIKI_SERVER_<SECTION>_<KEY>` 도 지원).
설계 원칙은 셋이다.

1. **거절은 정직하게**: 무한 대기 대신 429/503 + `Retry-After` + 한국어 사유. 클라이언트가 재시도 시점을 안다.
2. **관측은 누구나, 제어는 admin**: viewer 도 진행 중 작업을 본다(무엇 때문에 느린지 알 수 있어야 문의가 줄어든다).
   IP·오류·통계는 admin 만. `monitor.viewer_can_see_activity` / `show_user_to_viewer` 로 조절.
3. **다른 프로세스도 한 화면에**: CLI(`query`/`build`/`schedule`)와 MCP stdio 는 `data/live/<token>.json` 에 진행 상황을 발행하고,
   서버가 이를 합쳐 목록에 보여 주고 `<token>.cancel` 파일로 취소한다. 서버·CLI 를 섞어 쓰는 실제 운영을 그대로 반영했다.

세션은 서명 쿠키를 유지하되 `data/sessions.json` 에 목록을 남긴다(`sessions.enforce=true` 면 강제 로그아웃·동시 세션 수 제한 동작).

---

## 4. 진행 표시와 취소

- `progress.py` 에 `cancel/check_cancel/sleep_cancellable`, 대기열 위치, ETA, 클라이언트 정보, 외부 발행자 훅을 추가했다.
- 취소는 **협조적**이다: 단계 전환·임베딩 배치·LLM 호출 직전·재시도 대기에서 `Cancelled`(BaseException) 가 발생한다.
  `except Exception` 에 삼켜지지 않도록 BaseException 을 상속했고, 빌드는 취소 시 지금까지의 진행을 커밋한다.
- 시작 직후(티켓 생성 전) 들어온 취소는 **예약**되어 시작하자마자 반영된다(Web 에서 빌드를 누르자마자 중지하는 경우).
- CLI 는 Ctrl+C 와 서버발 취소를 모두 `cancelled` 로 처리하고, 진행 모니터가 단계·%·ETA·대기열을 한 줄로 보여 준다.

---

## 5. 스케줄러

`schedule.json` 의 작업을 `serve` 안의 스레드가 실행한다. 시점은 `every | at+days | cron`(5필드, `*/n a-b a,b` 지원, timezone 반영),
동작은 19종(§[SCHEDULER.md](../../SCHEDULER.md)). 설계 포인트:

- **CLI 전체를 덮는 탈출구**: `type: cli` 가 `run_captured` 를 쓰므로 CLI 로 되는 모든 것이 스케줄 가능하다. 그 위에 자주 쓰는 것을
  1급 유형(build·evolve·memory·precompute·eval·trial·snapshot·wiki·forensic·fetch_url·mcp_ingest·maintenance·embed_report·llm·headless·http·python)으로 올렸다.
- **요청 관리자와 통합**: 스케줄 작업도 티켓을 받아 활동 목록에 보이고 취소·시간 제한이 적용된다. 유형별로 read/soft/exclusive 가중치를 준다.
- **자동 적용의 안전장치**: `evolve auto_apply` 는 신뢰도 하한·종류·건수를 모두 요구한다(기본은 사람이 HITL 로 적용).
- **파일 감시**: 저장하면 자동 재적재. 잘못된 작업은 목록에 INVALID 로 표시되고 나머지는 계속 동작한다.
- 서버를 상시 띄우지 않는 환경을 위해 `schedule run <name>` 으로 OS 스케줄러에서도 같은 동작을 부를 수 있다.

---

## 6. 모델 카탈로그

`models.json` 에 사람이 추가/삭제하는 목록을 두고(항목: id·provider·label·roles·tags·context_k·enabled·notes),
Web 설정의 모델 드롭다운과 `models list` 가 이를 읽는다. 카탈로그에 없는 모델을 설정해도 **동작은 한다**(안내용 목록이라는 뜻) —
다만 `models list` 와 check_env 가 "카탈로그에 없음"으로 표시한다. `models discover` 는 Ollama `/api/tags` 와 OpenAI 호환 `/v1/models` 를
조회해 실제 제공 모델을 보여 주고, 클릭/명령 한 번으로 카탈로그에 넣는다.

---

## 7. 로그인 왕복 후 메뉴가 깨지던 문제

재현: 게스트(viewer)로 접속 → 상위 작업 클릭 → 로그인 화면 → 뒤로가기. 원인은 셋이었고 모두 고쳤다.

| 원인 | 수정 |
|---|---|
| `api()` 가 401 에서 `location.href` 로 이동한 뒤에도 **오류 객체를 반환** → `loadStatus()` 가 `j.providers.roles` 에서 TypeError → `buildSidebar()` 가 영영 실행되지 않아 사이드바·토글이 빈 채로 남음 | `loadStatus()` 가 응답을 검증하고 조기 종료. 401/네트워크/혼잡은 배지에 사유 표시 |
| 화면 상태(그룹/탭)가 DOM 에만 있고 URL 에 없어 왕복 후 초기화 | `#group/tab` 해시 라우팅(replaceState) + `?next=` 에 해시 포함 → 돌아오면 같은 자리 |
| `location.href` 로 이동해 히스토리에 `/login` 이 쌓여 뒤로가기가 다시 로그인으로 튕김 | `location.replace` 로 변경. `pageshow(persisted)` 에서 상태 재조회 |
| (부수) 한 번 실패하면 복구되지 않던 `togglesInit` 가드 | 토글/프리셋 목록 서명 + "비어 있으면 재구축" 으로 교체 |

`next` 는 같은 사이트 경로만 허용해 오픈 리다이렉트를 막았다.

---

## 8. 검증 전략

### 8.1 단위·통합 (`tests/test_concurrency_0915.py`, 43개)
요청 격리(동시 12질의가 서로의 top_k·토글·튜닝을 오염시키지 않음, 스레드별 연결, 카운터 분리) · RW 락 · 슬롯 · 대기열 ·
속도 제한 · 차단/점검 · 취소(질의·빌드·watchdog·sleep) · 역할 정책/backoff/budget/회로 · 스케줄러(파싱·19종 동작·이력·재적재) ·
모델 카탈로그 · 프로파일 · **스트레스**(30 동시 질의, 빌드 중 질의, 긴 작업 취소, 느린 LLM 취소, DEBUG 로그 분리, 잘못된 입력 25종).

### 8.2 한글 인코딩 (`tests/test_console_0915.py`, 8개)
좁은 인코딩(`PYTHONIOENCODING=ascii`/`cp949`)을 강제한 실제 CLI 실행으로 회귀를 막는다.

### 8.3 멍키/퍼즈 (`tools/verify/verify_monkey.py`)
무작위 경로·본문·타입·유니코드(제어문자·서러게이트·이모지·RTL)·거대 본문·잘못된 JSON 을 Web·MCP·CLI 로 동시에 쏟아붓고
**500 이 하나도 나지 않는가 / 서버가 살아 있는가 / 폭격 뒤 정상 질의가 되는가**를 본다. 찾은 결함:

| # | 증상 | 원인 | 수정 |
|---|---|---|---|
| 1 | `/api/query` 의 `overrides` 가 문자열/숫자면 500 | `apply_overrides` 가 `.items()` 호출 | 경계에서 타입 검증 → 400 |
| 2 | `/api/query_rules {rules: 3.14}` 저장 후 **모든 질의가 깨짐** | 사전이 아닌 값을 파일에 써 버림 | 저장 전 검증 + 원자적 교체 + 손상 파일이면 기본값으로 복구 |
| 3 | `query_id`·`name`·`id`·`kind` 누락 시 500 | `body["key"]` 직접 접근 | `_as_int/_as_str/_as_dict` 로 400 + 친절한 메시지 |
| 4 | `/api/cli {argv:{...}}` 500 | dict 인덱싱 | argv 타입 검증 |
| 5 | `/api/models/catalog {model:"문자열"}` 500 | dict 가정 | 타입 검증 |
| 6 | 스냅샷 복원 중 파일 잠금 실패 시 **서버 전체가 죽음** | `pipe.store` 를 교체하다 중간 실패 → 모든 요청이 closed DB | `Store.reopen()`(연결 닫기→파일 교체 재시도→재오픈), 실패해도 원래 DB 로 계속 동작 |
| 7 | 두 관리자가 동시에 설정을 저장하면 500 (`WinError 5`) | 모든 저장이 `<파일>.tmp` 고정 이름을 공유했고, Windows 는 남이 열고 있는 파일의 교체를 거부한다 | `atomicio.py` — 호출마다 다른 임시 이름 + 경로별 락(읽기도 포함) + 재시도. 설정 파일 입출력 13곳 통일 |
| 8 | 느린 CLI 두 개가 읽기 슬롯을 잡은 채 6분 넘게 실행되어 **나머지 사용자 64명이 전부 대기** | `timeouts.cli_s` 기본값이 0(무제한) | 기본 600초. 빌드 같은 soft/exclusive CLI 는 `job_s` 를 따르게 분리 |
| 9 | `analyze notanumber` 가 traceback 을 그대로 출력 | 숫자 인자를 검사 없이 `int()` | `analyze`·`logs --request`·`evolve apply/reject/feedback` 의 숫자 인자 검증 |
| 10 | 서러게이트가 섞인 질의 한 건 뒤로 **서버 모니터가 계속 400** | 그 문자열이 요청 라벨에 남아 JSON 직렬화가 매번 실패(SQLite 도 저장 불가) | 본문 경계 정화(`_scrub`) + 등록 시점 정화(`reqmgr.safe_text`) + 진행 레지스트리 정화 + JSON 응답 `errors="replace"` 안전망 |
| 11 | 3MB 본문 하나가 30초 넘게 슬롯을 잡음 | 본문 크기 상한 없음 | `concurrency.max_body_mb`(8) 초과 시 읽지 않고 413 |

부수 개선: 입력 모양 오류(TypeError/AttributeError/ValueError/KeyError)는 전부 400 으로 응답하고 원인은 로그에 남긴다 — 500 은 진짜 서버 결함만.

### 8.4 다중 프로세스 실사용에서 발견
서버(auto_build 워처)와 CLI 질의가 겹치자 `database is locked` 로 CLI 질의가 실패했다. 관측용 기록(요청 프로파일·질의 로그)은
잠금을 못 얻으면 **건너뛰고 경고만** 남기도록 바꿨다(답변은 정상 반환). `db_busy_timeout_s` 기본값도 60초로 올렸다.

---

## 9. 터미널 한글 깨짐

재현: Windows 로캘이 cp949/cp1252 인 상태에서 출력을 파일·파이프로 리디렉션하면
`UnicodeEncodeError: 'cp949' codec can't encode character '✔'` 로 **명령이 중간에 죽었다**. 콘솔 코드페이지가 UTF-8 이 아니면 한글도 깨졌다.

`llmwiki/console.py` 가 (1) Windows 콘솔이면 코드페이지를 UTF-8(65001)로 바꾸고 종료 시 복구, (2) stdout/stderr/stdin 을
UTF-8 + `errors="replace"` 로 재구성, (3) 자식 프로세스에 `PYTHONIOENCODING` 을 물려준다. 코드페이지를 못 바꾸는 환경에서는
콘솔 기본 인코딩을 유지하고 표현 불가 문자만 `?` 로 낮춘다(`console_encoding=native`). `config.json` 의 `console_encoding` ·
`console_set_codepage` 로 조절하고 `health` · `check_env.py` 가 현재 상태를 보고한다. `.bat` 은 `chcp 65001`, `.ps1` 은 UTF-8 BOM 으로 맞췄다.

---

## 10. 실제 코퍼스와 범용 변환

- `tools/fetch_rfc_corpus.py` — IETF RFC(직렬·모뎀·PPP·링크 계층 중심)를 내려받아 문서 계약 형식으로 저장. `Obsoletes/Updates` 를
  `related` 로 옮겨 **결정적 관계**가 생기므로 GraphRAG 검증에 적합하다. 163개 · 8.4MB.
- `tools/corpus_ingest.py` — 형식이 없는 자료(메모·소스코드·JSON·로그·HTML·PDF·docx)를 계약 형식으로 변환. 핵심은 **무손실**이다:
  텍스트 계열은 본문을 한 글자도 바꾸지 않고 front matter 만 붙이며(코드·로그는 펜스로 감싸되 내용은 그대로), `body_sha1` 을 기록해
  `--verify-only` 로 언제든 재검증한다. HTML/PDF/docx 는 텍스트 추출이라 비가역임을 manifest 에 명시하고 **원본을 `_originals/` 에 보관**한다
  (색인에서는 제외). 187개 · 2.5MB, 무결성 검사 151/151 일치.
- 합계 약 10.4MB(색인 대상) → 문서 352 · 청크 16,882 · 엔티티 607 · 관계 11,266.

`schemas/note.json`(자유 형식 문서 유형)을 추가해 유형을 추론할 수 없는 문서도 계약 안에서 다룬다.

---

## 11. Web UI 사용성

| 요청 | 구현 |
|---|---|
| 사이드바의 “토글을 config.json 에 저장” 위험 | 제거 — 한 사람이 모두의 기본값을 바꾸던 버튼. 서버 기본값은 Settings › config.json(admin)에서만 |
| 빌드 진행이 안 보임 | Corpus › 빌드에 “서버에서 진행 중” 패널 — CLI·스케줄러·다른 사용자의 작업까지 단계·%·ETA·로그·중지 |
| “모든 단계 펼치기”만 있고 접기가 없음 | 모든 trace 에 공통 툴바(펼치기/접기/복사) |
| 작업이 끝나면 로그 창이 사라짐 | 완료·실패·중지 상태와 전체 로그를 그대로 유지, `📋 로그 복사` 와 `✕ 닫기` 제공 (질의·빌드 동일) |
| 같은 작업이 여러 번 보임 | 이 브라우저가 폴링 중인 job 은 외부 목록에서 제외 |
| 여러 메뉴를 동시에 보고 싶다 | 탭 고정(📌) + **분할 보기**. 열 수는 자동·1·2·3·4 중에 고르고, 패널마다 두 칸 넓게·접기·좌우 이동·새로고침·해제 버튼이 있다. 머리글은 고정되고 내용만 스크롤하며 패널 높이는 4단계(낮게·보통·높게·자유). 고정한 탭은 다른 탭으로 이동해도 계속 갱신된다 |
| 질의 화면에서 대기·진행 상황 | ‘지금 서버에서’ 한 줄 요약(내 요청 강조·대기 건수·중지) |
| 어느 화면에 있든 서버가 바쁜지 보고 싶다 | 헤더 오른쪽의 **활동 표시기(HUD)** — 동시 실행 슬롯을 칸으로 그린다. 채워진 칸 = 실행 중, 초록 = 내 요청, 점선 = 대기. 숫자는 `실행/슬롯 +대기`. 클릭하면 진행 중 작업으로 간다. 질의 화면의 한 줄 요약과 **같은 2초 폴링을 공유**하므로 요청이 늘지 않는다 |
| 진행 중 작업이 표뿐이라 한눈에 안 들어온다 | **보드 보기**(기본) — 한 줄에 하나씩 압축해 나열하고 넘치면 스크롤(높이 44vh). 위에 용량 게이지(슬롯 칸·대기열 막대·락 상태), 줄마다 종류별 색 띠·아이콘·대기 순번·진행 바·중지. 토큰·IP 까지 필요하면 `표` 보기로 전환(선택은 브라우저에 저장) |
| 완료가 `0.0s` 로 보여 실행이 안 된 줄 알았다 | 완료 시간을 소수 3자리로 내려보내고 1초 미만은 `3 ms` 로 표기. 캐시·사전계산 적중은 `캐시`/`사전계산` 배지로 이유를 함께 보여 준다 |
| 서버 모니터 레이아웃 | 제한 편집·차단을 오른쪽 열로, 통계·세션을 왼쪽으로 |
| 계정별 설정 저장 | `/api/profile` + `data/profiles.json` — 토글·프리셋·오버라이드·테마·고정 탭·분할 상태를 계정에 저장(게스트는 브라우저에만). **서버 설정은 바뀌지 않는다** |

---

## 12. 신규·변경 파일

**신규**: `llmwiki/reqmgr.py`(요청 관리자) · `llmwiki/scheduler.py` · `llmwiki/models_catalog.py` · `llmwiki/console.py` · `llmwiki/profiles.py` · `llmwiki/atomicio.py`(설정 파일 원자적 저장) ·
`schemas/note.json` · `setup/server.example.json` · `setup/schedule.example.json` · `setup/models.example.json` ·
`tools/fetch_rfc_corpus.py` · `tools/corpus_ingest.py` · `tools/verify/verify_monkey.py` ·
`tests/test_concurrency_0915.py` · `tests/test_console_0915.py` · `docs/CONCURRENCY.md` · `docs/SCHEDULER.md` · 이 문서.

**주요 변경**: `pipeline.py`(request_scope·인스턴스 캐시·취소) · `store.py`(연결 풀·reopen·잠금 관용) · `providers.py`(역할 정책·회로) ·
`progress.py`(취소·ETA·외부 발행) · `profiler.py`(스레드별 카운터) · `tuning.py`(요청 오버레이) · `config.py`(역할 정책·콘솔·DB 키) ·
`auth.py`(세션 레지스트리·새 등급) · `web/server.py`(요청 관리자 연동·입력 검증·새 엔드포인트) · `cli.py`(server·schedule·models 확장·취소) ·
`web/static/*`(해시 라우팅·진행/취소·고정·분할·프로파일·모니터·스케줄) · `corpus.py`(`_originals` 제외) · `evolve.py`(reopen 사용) ·
`query_rules.py`(손상 방어) · `health.py`·`setup/check_env.py`(콘솔·동시성·스케줄·모델 카탈로그 보고) · `tools/verify/*` ·
설정 파일을 읽고 쓰는 13곳(`auth` · `config` · `tuning` · `presets` · `query_rules` · `graph_rules` · `models_catalog` · `scheduler` · `reqmgr` · `profiles` · `evalset`)을 `atomicio` 로 통일.

**신규 설정 키**: `server.json` 전체 · `schedule.json` 전체 · `models.json` 전체 ·
`config.json` 의 `console_encoding` · `console_set_codepage` · `db_busy_timeout_s` · `db_pool_size` · `llm_retry_backoff` ·
`llm_retry_backoff_max_s` · `llm_budget_s` · `llm_http_retries` · `llm_circuit_failures` · `llm_circuit_cooldown_s` ·
`llm_roles.<role>.{timeout_s,retries,backoff,backoff_s,backoff_max_s,budget_s,circuit_failures,circuit_cooldown_s}`.

---

## 13. 어느 모델이 무엇을 만들었나

사용자 요청으로 남긴다. 작업은 Claude Fable 5.1 이 시작했고, 토큰 한도에 닿은 뒤 Claude Opus 5 가 이어받았다.
파일 단위로 딱 잘리지는 않는다 — 아래는 **주도한 쪽** 기준이며, 이어받은 쪽이 같은 파일을 다시 손댄 경우가 많다.

| 모델 | 담당 |
|---|---|
| **Claude Fable 5.1** | 전체 설계와 계획 수립(§1~§7 의 구조 결정, 택하지 않은 대안 판단). 요청 격리 기반 공사 — `pipeline.request_scope` · `store.py` 연결 풀 · `profiler` 스레드별 카운터 · `tuning` 요청 오버레이. 요청 관리자 `reqmgr.py`(RW 락 · 티켓 · 대기열 · 속도 제한 · 접근 제어 · LiveRegistry). 역할별 LLM 정책과 회로 차단(`providers.py` · `config.py`). 진행률·취소(`progress.py` 확장, 협조적 취소 지점). 스케줄러 `scheduler.py` 1차(cron 파싱 · 기본 동작군). 모델 카탈로그 `models_catalog.py`. 로그인 왕복 버그 수정(해시 라우팅 · `loadStatus` 방어 · `location.replace`). `web/server.py` 의 전역 락 제거와 티켓 연동, 새 엔드포인트 1차. 동시성 테스트 1차. |
| **Claude Opus 5** | 이어받아 마무리. 터미널 한글 깨짐 재현·수정(`console.py`, `.bat`/`.ps1` 인코딩). 실데이터 코퍼스(`tools/fetch_rfc_corpus.py`)와 범용 무손실 변환기(`tools/corpus_ingest.py`, `schemas/note.json`). 멍키/퍼즈 하네스(`tools/verify/verify_monkey.py`)와 그것이 찾아낸 서버 결함 10건 수정(입력 검증 체계 · `query_rules` 손상 방어 · `Store.reopen` 스냅샷 복원 · 서러게이트 정화 · 콘솔 CLI 시간 제한). 설정 파일 원자적 저장 `atomicio.py` 와 13곳 통일. 스케줄러 동작 확장(evolve · memory · precompute · eval · trial · snapshot · wiki · forensic · embed_report). Web UI 사용성 전반 — 로그 패널 유지·복사, 접기/펼치기 툴바, 탭 고정과 화면 분할, 질의 화면의 대기열 요약, 빌드 진행 패널과 중복 제거, 서버 모니터 레이아웃, 계정별 프로파일 `profiles.py`. 스트레스·인코딩·원자적 저장 테스트. 문서 일체(이 문서 · CONCURRENCY.md · SCHEDULER.md · README · BRINGUP_GUIDE · VERIFICATION 갱신). |

## 13.1 2026-09-16 실사용 점검에서 추가된 것

하루 동안 실제로 써 보면서 나온 문제를 고치고, 같은 종류의 문제를 앞으로 자동으로 잡도록 도구를 늘렸다.
자세한 증상·원인·수정은 [VERIFICATION_0915.md](VERIFICATION_0915.md) §4.1(결함 14~25)과 §4.2(속도 실측),
화면 사용법은 [WEB_UI.md](../../WEB_UI.md).

| 무엇 | 왜 |
|---|---|
| 전체 리빌드를 청크 수에 **비례**하게 (통째로 비우고 다시 채우기 · 문서 단위 삭제 · `idx_rel_chunk`) | FTS5 의 UNINDEXED 열로 행별 삭제를 하면 매번 전체 스캔이라 O(N²) 였다. `fts_trigram` 을 켜면 특히 심했다 |
| 서버를 **HTTP/1.1 keep-alive** 로 (`concurrency.keep_alive_s`) + 화면 갱신 조회를 하나로 통합 | 브라우저의 동시 연결 6개 제한 때문에 긴 질의가 화면 갱신을 막았다 |
| 배치 작업 동시 실행 제한 (`concurrency.max_parallel_batch`) | eval 4개가 읽기 슬롯을 모두 차지해 UI 가 응답하지 않았다 |
| 답변 **반복 루프** 탐지·차단과 캐시 오염 방지, `precompute check/clear --broken` | 작은 모델이 같은 구절을 수백 번 반복한 답변이 캐시에 저장돼 계속 나왔다 |
| 질의 문자열 정수 파라미터 범위 강제(`_qint`) + `OverflowError` → 400 | 자리수가 큰 정수가 SQLite 로 넘어가 500 이 났다 |
| 프로파일 저장 API 의 action 화이트리스트 | 잘못된 action 한 번에 저장해 둔 설정이 통째로 지워졌다 |
| 분석 리포트 **LLM 소견** (`/api/analysis/insight`) | 규칙 소견은 수치까지만 알려 준다. 우선순위와 구체적인 값을 받아 HITL 제안으로 연결한다 |
| 화면: 1·2·3·4열 분할, 헤더 활동 표시기, 진행 중 작업 보드, 기본 테마 Light | 여러 메뉴를 동시에 보고, 어디서든 서버 상태를 알 수 있게 |
| **`verify_buttons.py`** (버튼 99개 전수 클릭) · `verify_click.py` · `bench_fts.py` | "눌러도 아무 일 없음" 과 "리빌드가 다시 느려짐" 을 사람 없이 잡기 위해 |

## 14. 남은 개선 여지 (다음 사람을 위해)

1. **LLM 호출 중 즉시 취소**: 지금은 전송된 HTTP 요청이 끝날 때까지 기다린다(상한은 `timeout_s`). 소켓을 직접 끊으려면 프로바이더별 처리가 필요하다.
2. **임베딩 차원**: 현재 `embed_dim=4096` 이라 16,882 청크에서 DB 가 약 380MB 다. 1024 로 줄이면 1/4 이 되고 품질 저하는 작다(`build --full` 필요).
3. **다중 서버**: 요청 관리자는 프로세스 하나 기준이다. 여러 대로 늘리려면 대기열·세션·회로 상태를 공유 저장소로 옮겨야 한다.
4. **evolve auto_apply 의 평가 연동**: 적용 후 trial 자동 실행·회귀 시 롤백까지 묶으면 무인 운영에 더 가까워진다.
