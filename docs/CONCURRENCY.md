# CONCURRENCY — 다중 사용자 동시 처리 · 요청 관리 · 모니터링

> 대상: 30명 안팎이 Web UI · MCP · CLI 로 동시에 쓰는 사내 서버를 운영·포팅하는 엔지니어.
> 설정 파일 [server.json](../setup/server.example.json) 하나로 동시성·대기열·속도 제한·차단·모니터를 조절한다. 코드 수정은 필요 없다.
> 함께 읽기: [SECURITY.md](SECURITY.md)(누가 무엇을 할 수 있는가) · [SCHEDULER.md](SCHEDULER.md)(주기 작업) · [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) §3.5.

---

## 0. 한 장 요약

| 질문 | 답 |
|---|---|
| 질의가 한 번에 하나씩 처리되나? | 아니다. 기본 **동시 8건**(`concurrency.max_parallel_reads`). LLM 응답 대기가 대부분이라 코어 수보다 크게 잡아도 된다 |
| 빌드 중에 질의가 되나? | **증분 빌드 중에는 된다**(기본). 전체 리빌드는 배타 실행이라 질의가 대기한다. `concurrency.reads_during_build` 로 never/incremental/always |
| 한 사람이 서버를 독점할 수 있나? | 없다. 사용자당 동시 3건 · 분당 60건 · 질의 분당 20건 (초과 → 429 + Retry-After) |
| 대기가 길어지면? | 대기열 128건·120초를 넘으면 **503 + 안내**. 무한 대기하지 않는다 |
| 진행 중인 작업을 볼 수 있나? | 누구나 `GET /api/activity`(Web ‘진행 중 작업’ 탭). admin 은 IP·오류까지 |
| 오래 걸리는 작업을 멈출 수 있나? | 본인 요청은 누구나, 남의 것은 admin. Web ■ 중지 · `DELETE /api/jobs/<id>` · `server cancel <token>` |
| CLI 로 돌린 빌드도 서버에서 보이나? | 보인다. CLI 가 `data/live/` 에 진행 상황을 발행하고 서버가 합쳐 보여 준다(취소도 가능) |
| RAG 품질에 영향이 있나? | 없다. 티켓 발급은 딕셔너리 갱신 + 락 몇 개(µs)이고 검색·LLM 경로는 그대로다 |

---

## 1. 왜 이렇게 만들었나 (그 전의 문제)

2026-09-14 까지 Web 서버는 **전역 잠금 하나**(`_LOCK`)로 모든 것을 감쌌다. 질의 1건이 LLM 응답을 기다리는 20초 동안
다른 29명의 질의·대시보드 조회·MCP 호출이 전부 줄을 섰다. 사실상 동시 처리 수는 1이었다.

단순히 잠금을 없앨 수는 없었다. 그 잠금이 아래 셋을 동시에 막고 있었기 때문이다.

1. **SQLite 연결 하나를 모든 스레드가 공유** → 한 스레드의 `commit()` 이 다른 스레드의 반쯤 쓴 트랜잭션을 확정한다.
2. **설정(Settings)·튜닝(T)이 전역 하나** → A 가 `--preset speed` 로 질의하는 동안 B 의 질의도 speed 로 돌고, 나중에 끝난 쪽이 남의 값을 복원한다.
3. **프로파일 카운터가 전역** → 요청별 SQL/토큰 수가 서로 섞인다.

그래서 잠금을 없애기 전에 **요청을 격리**했다.

| 무엇 | 어떻게 |
|---|---|
| DB 연결 | 스레드별 연결 풀 (`Store.session()`, WAL + `busy_timeout`). 쓰기끼리만 SQLite 수준에서 기다린다 |
| 설정 | `Pipeline.request_scope()` 가 요청마다 Settings **사본** 을 만든다 (스레드 로컬). 전역은 불변 |
| 튜닝 | `Tuning` 의 요청 단위 **오버레이** (프리셋·mode 는 여기에만 적용) |
| 프로파일 카운터 | 스레드 로컬 누적 (`profiler.count`) |
| LLM 인스턴스 | (역할, provider, model, 정책) 서명별 캐시 — 요청마다 다른 모델을 써도 서로 덮어쓰지 않음 |

그 위에 **공통 요청 관리자**(`llmwiki/reqmgr.py`)를 두어 Web · MCP · CLI 콘솔 · 스케줄러 · 워처가 같은 규칙을 쓴다.

---

## 2. 읽기와 쓰기 — 무엇이 무엇을 막는가

작업은 권한 등급(`auth.classify_api`)에 따라 세 가지 **가중치** 중 하나를 받는다.

| 가중치 | 해당 작업 | 다른 읽기 | 다른 쓰기 |
|---|---|---|---|
| `read` | 질의 · 검색 · MCP 도구 호출 · 평가 · trial · precompute · 조회 | 동시 실행 (슬롯 수만큼) | 기다림 |
| `soft` | 증분 빌드 · 채널 빌드 · MCP ingest · 메모리/위키 갱신 | **동시 실행 허용**(정책에 따라) | 배타 |
| `exclusive` | 전체 리빌드 · 설정 저장 · 스냅샷 복원 · 유지보수 · 제안 자동 적용 | 기다림 | 배타 |

`concurrency.reads_during_build` 가 이 정책을 정한다.

- `incremental`(기본): 증분·채널 빌드 중에는 질의 허용, 전체 리빌드는 배타.
- `never`: 어떤 빌드 중에도 질의를 막는다(색인 일관성을 최우선으로 할 때).
- `always`: 전체 리빌드 중에도 질의 허용. 전체 리빌드는 색인을 비우고 다시 채우므로 그 사이 질의는
  **아직 덜 채워진 색인**을 읽는다 — 답이 틀리는 게 아니라 "근거를 찾지 못했다" 가 늘어난다.

30명 기준 실측(빌드가 도는 구간만): 기본 `incremental` 에서 전체 리빌드 5.7초 동안 질의 54건·p95 5,548ms,
`always` 에서 같은 조건 893건·p95 376ms. 표 전체와 채널별 값은 [BUILD_UNDER_LOAD.md](BUILD_UNDER_LOAD.md) §1.
**주의**: 2026-09-19 이전에는 `always` 가 전체 리빌드에 적용되지 않았다(완화 분기가 `weight == "write"` 만
보았고 리빌드는 `"exclusive"` 로 들어왔다). 회귀 방지는 `tests/test_build_concurrency.py`.

쓰기는 **writer preference** 다. 쓰기가 기다리기 시작하면 새 읽기는 뒤에 줄을 서므로, 질의가 계속 들어와도 빌드가 굶지 않는다.
그래서 채널 빌드처럼 읽기를 막지 않는 작업에서도 락을 잡는 **순간**에는 p95 가 수백 ms 까지 튈 수 있다 — 빌드 길이와는 무관하다.

---

## 3. 용량 제어 — 대기열 · 동시 수 · 속도 제한

```
요청 도착
  → 차단 목록 / 점검 모드 확인        (403 / 503)
  → 분당 속도 제한 확인               (429 + Retry-After)
  → 동시 수 제한 확인 (사용자·IP)     (429)
  → 대기열 진입 (상한·시간 초과)      (503)
  → 읽기 슬롯 획득 → 실행
```

| 키 | 기본 | 뜻 |
|---|---|---|
| `concurrency.max_parallel_reads` | 8 | 동시에 실행할 질의/검색/MCP 도구 호출 수 |
| `concurrency.max_parallel_per_user` | 3 | 한 사용자의 동시 실행+대기 수 |
| `concurrency.max_parallel_per_ip` | 6 | 한 IP 의 동시 수 (프록시 뒤면 크게) |
| `concurrency.max_parallel_batch` | 1 | 평가·trial·사전계산처럼 **안에서 질의를 여러 번 도는 배치 작업**의 동시 실행 수. 이런 작업은 수 분~수십 분 읽기 슬롯을 물고 있어서, 여러 개가 동시에 돌면 대화형 질의가 전부 대기열로 밀린다. 0 = 무제한 |
| `concurrency.max_body_mb` | 8 | 요청 본문 상한(MB). 초과하면 본문을 읽지도 않고 413 `body_too_large` (0 = 무제한) |
| `concurrency.keep_alive_s` | 30 | HTTP 연결 재사용(keep-alive) 유휴 시간(초). 0 이면 응답마다 연결을 끊는다(HTTP/1.0). **0 으로 두지 마세요** — 브라우저는 한 사이트에 동시 연결을 6개까지만 열기 때문에, 오래 걸리는 질의 몇 개가 연결을 물고 있으면 화면 갱신이 브라우저 안에서 줄을 서다가 한꺼번에 처리된다 |
| `concurrency.queue_max` | 128 | 대기열 상한 (초과 → 503 `queue_full`). 2026-09-23 에 64 → 128 로 올렸다: 질의 1건이 100초 가까이 걸리는 환경(로컬 LLM)에서 64는 30명이 두 번씩만 물어도 가득 찬다. 대기열이 길어도 수명은 `queue_timeout_s` 가 끊으므로, 즉시 `queue_full` 로 돌려보내는 것보다 FIFO 로 기다리게 하는 편이 낫다. **대기열은 슬롯을 대신하지 못한다** — 슬롯 8 · 질의 100초면 대기열 128번째는 이론상 1,600초를 기다리므로 실제로는 `queue_timeout_s`(120초)에서 503 이 된다 |
| `concurrency.queue_timeout_s` | 120 | 대기 최대 시간 (초과 → 503 `queue_timeout`) |
| `concurrency.write_wait_timeout_s` | 172800 (48시간) | 쓰기(빌드)가 진행 중인 읽기를 기다리는 최대 시간. 짧으면 긴 빌드가 시작도 못 하고 503 `write_wait_timeout` 으로 거부된다 |
| `concurrency.read_wait_timeout_s` | 900 | 읽기가 배타 작업을 기다리는 최대 시간. **이 값은 빌드가 아니라 질의의 수명**이라 일부러 짧다 — 크게 잡으면 전체 재빌드 동안 질의 스레드가 쌓여 서버가 마비된다. 빌드 중에도 질의를 받으려면 `reads_during_build` 를 쓴다 |
| `concurrency.reads_during_build` | `incremental` | 빌드 중 읽기 허용 범위: `always`(항상) · `incremental`(증분 빌드 중에만) · `never` |
| `rate_limit.enabled` | true | 속도 제한 전체 on/off |
| `rate_limit.per_user_per_min` | 60 | 사용자별 분당 요청 수 (0 = 무제한) |
| `rate_limit.per_ip_per_min` | 120 | IP 별 분당 요청 수 |
| `rate_limit.query_per_user_per_min` | 20 | 사용자별 분당 **질의** 수 (LLM 비용 보호) |
| `rate_limit.exempt_roles` | `["admin"]` | 제한을 받지 않는 역할 |
| `timeouts.query_s` | 900 | 질의 하나의 최대 시간 |
| `timeouts.search_s` | 120 | 검색(`/api/search`)의 최대 시간 |
| `timeouts.mcp_s` | 900 | MCP 도구 호출의 최대 시간 |
| `timeouts.job_s` | 172800 (48시간) | 백그라운드 작업(빌드·평가·스냅샷) 최대 시간. Web 콘솔의 `build` 명령도 `cli_s` 가 아니라 이 값을 따른다. 0 = 무제한 |
| `timeouts.cli_s` | 600 | Web 콘솔이 부르는 CLI 의 최대 시간. 이 CLI 는 읽기 슬롯을 잡은 채 실행되므로 0(무제한)으로 두면 느린 한 명령이 다른 사용자를 모두 대기열에 밀어 넣는다. 빌드처럼 오래 걸리는 CLI 는 `job_s` 를 따른다 |
| `sessions.enforce` | false | true 면 아래 두 값을 실제로 강제한다 |
| `sessions.max_per_user` | 5 | 한 계정의 동시 세션 수 (초과 시 가장 오래된 세션 만료) |
| `sessions.idle_timeout_min` | 720 | 이 시간 동안 활동이 없으면 세션 만료 |
| `monitor.history_size` | 500 | 서버가 기억하는 최근 요청 수 |
| `monitor.slow_request_ms` | 30000 | 이보다 오래 걸린 요청을 '느린 요청'으로 표시 |
| `monitor.stats_window_min` | 15 | 통계 집계 구간(분) |
| `monitor.live_dir` | `data/live` | CLI·MCP·스케줄러가 진행 상황을 적는 폴더 (서버가 읽어 한 화면에 합친다) |
| `monitor.live_stale_s` | 90 | 이 시간 동안 갱신이 없는 외부 작업은 죽은 것으로 보고 목록에서 뺀다 |

거절은 항상 **정직한 상태 코드**로 돌아간다: 429(속도) · 503(혼잡/점검) + `Retry-After` 헤더 + 한국어 사유.
Web UI 는 이를 토스트로 보여 주고 화면을 버리지 않는다.

### 30명 규모 권장값

| 상황 | max_parallel_reads | per_user | query/min |
|---|---|---|---|
| 로컬 LLM(Ollama) 1대 | 4~6 | 2 | 10 |
| 사내 게이트웨이(동시성 여유) | 8~16 | 3 | 20~30 |
| mock/추출식(LLM 없음) | 16~32 | 5 | 60 |

기준: 동시 실행 수는 **LLM 엔드포인트가 감당하는 동시 요청 수**에 맞춘다. 그보다 크게 잡으면 LLM 쪽에서 429/타임아웃이 난다.

---

## 4. 시간 제한과 취소

오래 걸리는 작업은 **협조적 취소**로 멈춘다. 취소 요청이 오면 다음 경계(단계 전환 · 임베딩 배치 · LLM 호출 직전 · 재시도 대기)에서
`progress.Cancelled` 가 발생해 지금까지의 결과를 유지한 채 정리하고 끝난다.

- **사용자**: Web 진행 패널의 `■ 중지`, 질의 화면의 ‘지금 서버에서’ 줄의 ✕, `DELETE /api/jobs/<id>`.
- **admin**: 남의 요청도 중지(`server cancel <token>`), 사용자 단위 일괄 중지(`server kick <user>`).
- **자동**: `timeouts.query_s`(기본 900) · `search_s` · `job_s`(기본 172800 = 48시간) · `mcp_s` 를 넘으면 watchdog 이 취소한다. 0 = 제한 없음.

### 빌드에 걸리는 시간 제한 한눈에 (기본 48시간)

빌드 하나를 중간에 끊을 수 있는 값은 아래 다섯 개뿐이고, 모두 기본 172800초(48시간)다. 더 오래 걸리는 코퍼스라면 다섯 개를 같이 올린다.

| 값 | 파일 | 무엇을 끊나 |
|---|---|---|
| `timeouts.job_s` | `server.json` | 빌드 작업 자체 (watchdog 협조적 취소) |
| `concurrency.write_wait_timeout_s` | `server.json` | 빌드가 **시작**을 기다리는 시간 (진행 중 질의가 끝나기를) |
| `build_lock_timeout` | `config.json` | 다른 빌드가 락을 잡고 있을 때 기다리는 시간 (0 = 즉시 실패) |
| `build_lock_stale_s` | `config.json` | 빌드 락을 죽은 락으로 보고 회수하기까지 — 빌드 1회의 최대 수명 |
| 스케줄 태스크의 `timeout_s` | `schedule.json` | 예약 빌드 1회 (`incremental-build`, `nightly-full-build`) |

빌드 **안에서** 부르는 LLM 한 번의 제한은 `config.json` 의 `llm_timeout`(기본 600초)과 역할별 `llm_roles.<role>.timeout_s` 이고, headless 는 `agents.json` 의 `timeout_s` 다.
이 값들은 일부러 짧게 둔다 — 멈춘 엔드포인트를 48시간 기다리는 대신 빨리 실패하고 재시도해야 빌드 전체가 끝난다.

빌드를 취소해도 **지금까지의 진행은 보존**된다(임베딩 체크포인트·커밋). 다음 빌드가 이어서 끝낸다.

> 한계: 이미 전송된 LLM HTTP 요청 자체는 중간에 끊지 못한다. 최악의 대기는 그 호출의 `timeout_s` 로 제한된다
> (역할별 설정은 [BRINGUP_GUIDE](BRINGUP_GUIDE.md) §4.3 / 아래 §7).

### 4.1 timeout·retry 를 **한 번만** 바꿔 보기 — 세 창구가 같은 길 (2026-09-19)

값을 바꿔 보려고 `config.json` 을 고치면 **모든 사용자**의 기본값이 바뀐다. 한 번의 실행에만 적용하려면
요청 단위 오버라이드를 쓴다. 세 창구가 **같은 화이트리스트**(`auth.filter_overrides`)를 지나므로 결과가 같다.

```bat
:: CLI — 이번 실행에만 (config.json 은 그대로)
python -m llmwiki query "질문" --set llm_timeout=7,llm_retries=1
python -m llmwiki query "질문" --set answer_timeout_s=30 --set rerank_retries=0   :: 역할 단축키, 여러 번 가능
```
```jsonc
// Web  POST /api/query
{"q": "질문", "overrides": {"llm_timeout": 7, "llm_retries": 1,
                            "llm_roles": {"answer": {"timeout_s": 30}}}}
// MCP  wiki_query
{"question": "질문", "overrides": {"llm_timeout": 7, "llm_retries": 1}}
```

| 규칙 | 동작 |
|---|---|
| 모르는 키 | **400** — 조용히 버리지 않는다. 오타 하나로 "설정이 안 먹는다" 가 되지 않게 (admin 도 예외 없음) |
| 역할이 못 쓰는 키 | **403** + 이유 (URL·경로·서버 운영 키는 admin. 목록 조정은 `security.json overrides.allow_extra/deny`) |
| 튜닝 키 | `overrides.tuning` 안에 넣는다 (CLI 는 `--tuning`). 범위 밖이면 400 |
| 전역 철자 ↔ 역할 철자 | `llm_timeout` ↔ `llm_roles.<role>.timeout_s` 는 **권한 등급이 같다**. 회로 차단(`llm_circuit_failures`·`circuit_cooldown_s`)은 서버를 지키는 장치라 양쪽 모두 admin |

**효과 확인**: LLM 이 실패하면 응답의 `llm_report.failures[]` 에 `max_attempts`(= 1 + retries)와 `timeout_s` 가
그대로 남는다. 세 창구가 같은 값을 돌려주는지는 `tools/verify/verify_timeouts.py` §1.5 가 매번 확인한다
(Web=CLI=MCP 비교 포함). MCP 는 기본 모드 응답의 `structuredContent.llm_report` 로 같은 것을 읽는다.

---

## 5. 누가 무엇을 보고 제어하는가

| 기능 | viewer(게스트 포함) | admin |
|---|---|---|
| 진행 중/대기 작업 목록 (`/api/activity`) | ✔ (설정으로 끌 수 있음) | ✔ + IP · 오류 · 외부 프로세스 |
| 자기 요청 중지 | ✔ | ✔ |
| 남의 요청 중지 · 사용자 강제 종료 | ✘ | ✔ |
| 서버 통계·제한·세션 (`/api/admin/server`) | ✘ | ✔ |
| 제한값 변경 · 차단 · 점검 모드 | ✘ | ✔ (감사 로그 기록) |

`monitor.viewer_can_see_activity=false` 로 일반 사용자에게서 목록을 감출 수 있고, `monitor.show_user_to_viewer=false` 면
사용자 id 대신 역할만 보인다.

### Web UI
- **Observability › 진행 중 작업**: 실행/대기/외부(CLI·MCP·스케줄러)/최근 완료, 단계·%·ETA·LLM 대기, 중지 버튼.
- **Observability › 서버 모니터**(admin): 처리량·지연 p50/p95·거절 카운터·클라이언트별 통계·회로 차단·세션·제한 편집·차단 목록·점검 모드·로그 레벨.
- **Corpus › 빌드**: 이 브라우저에서 시작하지 않은 빌드(CLI·스케줄러·다른 사용자)도 같은 자리에서 보인다.
- **Ask › 질의**: ‘지금 서버에서’ 한 줄 요약 (내 요청은 색으로 구분, 대기 건수, 중지).

### CLI
```bat
python -m llmwiki server status            :: 처리량·지연·제한·클라이언트·회로
python -m llmwiki server requests          :: 실행/대기/외부/최근
python -m llmwiki server cancel <token>    :: 중지
python -m llmwiki server limits set concurrency.max_parallel_reads=16 rate_limit.query_per_user_per_min=30
python -m llmwiki server block add ip 10.1.2.3   |  server block add user bob
python -m llmwiki server maintenance on "정기 점검 중"   |  server maintenance off
python -m llmwiki server kick bob          :: 그 사용자의 실행/대기 요청 모두 취소
python -m llmwiki server sessions          :: 로그인 세션 (revoke <sid> 로 강제 로그아웃)
python -m llmwiki server circuits reset    :: LLM 회로 차단 해제
python -m llmwiki server log-level DEBUG   :: 실행 중 서버의 로그 레벨 변경
```
`--url`(기본 config 의 web_host/web_port) 과 `--token`(admin API 키) 또는 `--user/--password` 로 접속한다.

---

## 6. 접근 제어 · 점검 모드 · 세션

```json
"access": {
  "block_ips": ["10.9.9.", "203.0.113.7"],
  "block_users": ["intern-temp"],
  "allow_ips": [],
  "maintenance_mode": false,
  "maintenance_message": "서버 점검 중입니다. 잠시 후 다시 시도하세요.",
  "maintenance_allow_roles": ["admin"]
}
```
- `block_ips` 는 정확히 일치하거나 `10.9.9.` 처럼 **접두어**로 적는다.
- `allow_ips` 가 비어 있지 않으면 **그 목록만** 접속할 수 있다(폐쇄망 운용).
- 점검 모드에서는 허용 역할 외 모든 요청이 503 + 안내 문구. 정적 파일과 로그인 화면은 계속 열린다.
- `sessions.enforce=true` 면 서버가 로그인 세션을 기억해 **강제 로그아웃**과 **사용자당 동시 세션 수 제한**이 동작한다
  (false 여도 목록은 기록되어 누가 언제 어디서 로그인했는지 admin 이 볼 수 있다).

---

## 7. LLM 실패가 서버를 막지 않게 — 역할별 정책과 회로 차단

역할(answer · rerank · extract · summary · review · expand · verify · forensic)마다 타임아웃·재시도·backoff·예산을 따로 준다.

```json
"llm_roles": {
  "answer":  {"model": "claude-sonnet-5", "timeout_s": 300, "retries": 2, "backoff": "exponential", "budget_s": 600},
  "rerank":  {"model": "claude-haiku-4-5-20251001", "timeout_s": 60, "retries": 1, "backoff_s": 1},
  "verify":  {"timeout_s": 45, "retries": 0},
  "extract": {"timeout_s": 120, "retries": 1, "circuit_failures": 5}
}
```

| 키 | 전역 기본값 | 뜻 |
|---|---|---|
| `timeout_s` | `llm_timeout` 600 | 호출 1회의 HTTP 타임아웃 |
| `retries` | `llm_retries` 3 | transient(타임아웃·네트워크·headless 실행) 재시도 횟수 |
| `backoff` / `backoff_s` / `backoff_max_s` | `exponential` / 2.0 / 60 | 재시도 대기 (지터 포함) |
| `budget_s` | `llm_budget_s` 0 | 재시도까지 포함한 **총 시간 예산**. 넘으면 재시도를 멈추고 대체 경로 |
| `circuit_failures` / `circuit_cooldown_s` | 3 / 60 | 연속 실패 n회 → cooldown 동안 그 provider/model 호출을 **즉시 실패**시켜 30명이 각각 타임아웃을 기다리지 않게 |

최종 실패해도 **결과는 나온다**: answer→추출식 답변, rerank→로컬 휴리스틱, expand→규칙 확장만, verify→휴리스틱 판정,
extract→규칙 그래프만. 무엇이 실패했고 무엇으로 대체했는지는 결과의 `llm_report` 와 답변 상단 `⚠ LLM 실행 보고`,
빌드 alerts 에 남는다. 확인: `models policy`(역할별 표) · `server circuits`(차단 상태) · Web 모델 탭의 ‘재시도·타임아웃 열 보기’.

---

## 8. SQLite 동시성

- **WAL** 모드라 읽기는 쓰기를 막지 않는다. (실측: 쓰기 잠금이 걸린 상태에서 다른 연결의 읽기 **0.02ms**)
- 스레드마다 연결을 빌려 쓴다(`db_pool_size`, 기본 16 — 동시 실행 수 이상으로).
- 쓰기끼리는 `db_busy_timeout_s`(기본 60초)만큼 기다린다. 다른 **프로세스**(CLI 빌드·서버 워처)와도 이 규칙이 적용된다.
- 커밋 내구성은 `db_synchronous`(기본 `NORMAL`). WAL 에서 `NORMAL` 은 **DB 손상이 없고** 체크포인트에서만 fsync 한다 — 질의 1건이 커밋을 여러 번 하므로 `FULL`(SQLite 기본값)로 두면 동시 질의가 몰릴 때 그대로 지연이 된다.

### 8.0 "읽기만 하는데 왜 잠기나" — 질의는 읽기 전용이 아니다 (2026-09-23)

읽기는 잠그지 않는다. 그런데도 동시 질의가 서로를 막던 이유는 **질의가 쓰기도 하기 때문**이었고,
그중 하나가 **커밋하지 않는 쓰기**였다.

`retrieval.embed_query()` 는 질의 벡터를 `embedding_cache` 에 넣는데 `Store.cache_put()` 이 `commit()` 을 하지 않았다.
Python `sqlite3` 는 INSERT 앞에서 트랜잭션을 암묵적으로 열고, SQLite 는 첫 쓰기에서 **WRITER 잠금을 잡아 COMMIT 까지 놓지 않는다.**
이 INSERT 는 질의 **초반**(벡터 검색)에 일어나고 그 연결의 다음 커밋은 질의 **맨 끝**이었다 —

> **질의 1건이 자기 수명(실측 98~118초) 내내 SQLite 쓰기 잠금을 혼자 쥐고 있었다.**

다른 동시 질의는 자기 `cache_put` 에서 `db_busy_timeout_s`(60초)까지 기다린 뒤 `database is locked` 로 실패했고,
그 실패는 `except Exception: pass` 가 조용히 삼켜 **로그에도 남지 않았다**.
캐시가 **적중**하면 쓰기가 없으므로 같은 질문을 반복할 때는 멀쩡했고, **서로 다른 질문을 여러 명이 동시에 던질 때** 터졌다.

고친 뒤 (실측, 같은 조건):

| | 전 | 후 |
|---|---|---|
| 질의 진행 중 다른 연결의 쓰기 | 3,310ms 대기 후 `database is locked` | **0.89ms 성공** |
| 질의 1건의 임베딩 호출 | 최대 3회(같은 문자열을 `vector_search`·`doc_vector_search`·`doc_expand` 가 각각) | **1회** (나머지는 캐시 적중 — 실측 약 5초 단축) |

**재발 방지**: 커밋 없이 세션을 벗어나면 이제 경고를 남기고 센다 —
`Observability › 서버 모니터` 의 `db_pool.uncommitted_exits` 가 **0 이 아니면 그런 코드가 있다는 뜻**이다.
회귀 테스트는 `tests/test_db_lock_0923.py`. 자세한 배경과 포팅 절차는 [REQUEST_LEDGER.md](REQUEST_LEDGER.md).
- 관측용 기록(요청 프로파일·질의 로그)이 잠금을 못 얻으면 **건너뛰고 경고만 남긴다** — 답변은 정상 반환된다.
- 스냅샷 복원은 모든 연결을 닫고 파일을 바꾼 뒤 다시 여는 `Store.reopen()` 으로 처리한다(Windows 파일 잠금 재시도 포함).

### 8.1 연결 회계와 부드러운 상한 (2026-09-19)

`db_pool_size` 는 **놀고 있는** 연결만 제한한다. 빌려 나간 연결 수에는 상한이 없었고 아무도 세지 않아서,
세션을 닫지 않는 코드가 하나 생기면 연결이 조용히 늘어나도(메모리·파일 핸들) 알 길이 없었다.
지금은 `Store.pool_info()` 가 다음을 함께 돌려준다 — Web 의 **서버 모니터**와 `server` CLI, `/api/admin/server` 에서 보인다.

| 값 | 뜻 | 이상 신호 |
|---|---|---|
| `live` | 지금 빌려 나가 있는 연결 수 | 한가한데도 0 이 아니면 **세션 누수** |
| `peak_live` | 최고 기록 | 동시 실행 슬롯보다 훨씬 크면 어딘가가 세션을 겹쳐 연다 |
| `idle` / `max` | 풀에 남은 연결 / `db_pool_size` | `idle` 이 늘 0 이면 풀이 작다(매 요청 새 연결) |
| `created` | 새로 만든 누적 수 | 요청 수만큼 늘면 풀이 사실상 동작하지 않는 것 |
| `overflow` | 상한을 넘겨서 만든 횟수 | 0 이 정상. 늘면 누수 또는 과부하 |

상한(`db_max_live_connections`, 기본 64)은 **부드럽다**. 넘으면 `db_pool_wait_timeout_s`(기본 2초)만큼
반납을 기다렸다가, 그래도 없으면 **만들어서라도 진행하고** `overflow` 를 센다. 딱딱하게 막지 않는 이유:
채널 검색처럼 상위 스레드가 연결을 쥔 채 하위 스레드가 연결을 더 쓰는 자리가 있어서, 하드 캡은 교착이 된다.
정상 운영에서는 64 에 닿지 않으므로 이 값은 차단 장치가 아니라 **경보**다. 0 = 상한 없음(예전 동작).

### 8.2 락 획득 순서

서로 다른 락을 겹쳐 잡는 자리는 셋뿐이고, **항상 이 순서**로만 잡는다. 반대로 잡으면 교착이 난다.

```
RequestManager._lock  →  RWLock._cv  →  progress._LOCK  →  Store._pool_lock
```

- 티켓을 만들며(`_lock`) 읽기/쓰기 락을 얻고(`_cv`), 진행 표시에 등록한다(`progress._LOCK`).
- DB 세션은 **가장 안쪽**에서만 연다. `Store._pool_lock` 을 쥔 채 다른 락을 기다리는 코드는 없어야 한다
  (풀 대기는 `Condition.wait` 이라 그 사이 락을 놓는다).
- `Pipeline._lock` / `_prov_lock` / `_qlock` 은 서로 겹치지 않는 독립 락이며, 잡은 채 LLM 을 부르지 않는다.
- 새 락을 넣을 때는 이 사슬의 **끝**에 붙인다. 중간에 끼워야 한다면 세 자리를 모두 다시 본다.

---

## 9. 문제 해결

| 증상 | 원인 | 조치 |
|---|---|---|
| 429 `rate_limited` 가 자주 뜬다 | 분당 상한이 낮다 | `server limits set rate_limit.query_per_user_per_min=40` |
| 413 `body_too_large` | 요청 본문이 `concurrency.max_body_mb`(기본 8MB)를 넘었다 | 큰 문서는 본문에 붙이지 말고 코퍼스로 넣는다(`tools/corpus_ingest.py`). 꼭 필요하면 `server limits set concurrency.max_body_mb=32` |
| 한 사람이 콘솔에서 무거운 명령을 돌리면 모두가 느려진다 | 콘솔 CLI 는 읽기 슬롯을 잡고 실행된다 | `timeouts.cli_s`(기본 600초)로 상한을 두고, 오래 걸리는 작업은 콘솔 대신 스케줄러에 등록한다([SCHEDULER.md](SCHEDULER.md)) |
| Web UI 가 아예 반응하지 않는다 | 평가(eval)·trial 처럼 오래 도는 배치 작업이 읽기 슬롯을 다 차지했을 수 있다. `server activity` 나 헤더 활동 표시기로 확인하고, 필요하면 `server cancel <token>` 또는 진행 중 작업의 중지 버튼으로 끊는다. 기본값 `max_parallel_batch=1` 이 동시에 여러 개 도는 것을 막는다 |
| 질의를 던지면 화면이 한참 멈췄다가 한꺼번에 갱신된다 | 브라우저가 한 사이트에 여는 동시 연결이 6개뿐이라, 오래 걸리는 질의가 연결을 물고 있으면 갱신 요청이 브라우저 안에서 줄을 선다 | `concurrency.keep_alive_s` 가 0 이 아닌지 확인한다(기본 30). 리버스 프록시를 뒀다면 프록시도 keep-alive 를 끄지 않았는지 본다. 화면 갱신 요청은 이미 한 번만 보내 여러 패널이 나눠 쓴다 |
| 503 `queue_full` / `queue_timeout` | 동시 실행 슬롯보다 요청이 많다 | `max_parallel_reads` 를 LLM 동시 수만큼 올리거나 `queue_max`·`queue_timeout_s` 조정 |
| 질의가 갑자기 다 느려짐 | 전체 리빌드가 배타 실행 중 — *빌드 시간이 곧 모든 사용자의 대기 시간*이다 | `진행 중 작업` 탭에서 확인. 야간으로 옮기거나(SCHEDULER) `reads_during_build=always`. 30명 기준 실측과 정책별 비교는 [BUILD_UNDER_LOAD.md](BUILD_UNDER_LOAD.md) (2026-09-19 이전에는 이 설정이 전체 리빌드에 **동작하지 않았다** — 같은 문서 §3) |
| “다른 작업이 끝나기를 기다리는 중…” 이 길다 | 배타 작업(빌드/복원) 대기 | 진행 패널에서 남은 시간 확인, 필요하면 중지 |
| 특정 모델만 계속 실패 | 게이트웨이 장애 | `server circuits` 로 차단 확인 → 원인 해결 후 `server circuits reset` |
| CLI 질의가 `database is locked` | 서버 워처/빌드와 겹침 | 기본값으로 이미 건너뛰고 응답한다. 계속되면 `db_busy_timeout_s` ↑ 또는 `auto_build_interval` ↑ |
| **동시 질의가 서로를 막는다 / `database is locked`** | 빌드가 없는데도 나면 **커밋하지 않는 쓰기**다 (§8.0) | 서버 모니터의 `db_pool.uncommitted_exits` 를 본다 — 0 이 아니면 그런 코드가 있다. 2026-09-23 에 `embed_query` 에서 고쳤다 |
| 질의가 임베딩에 오래 걸린다 | 같은 문자열을 한 요청에서 여러 번 임베딩 | `embed_query` 를 지나는지 확인. 단계 note 의 `query_embed_cached` 가 `false` 로만 나오면 캐시가 안 먹는 것 |
| 임베딩 캐시가 통째로 빗나갔다 | 모델 이름 표기 변경(`bge-m3` ↔ `bge-m3:latest`) | 정규화가 들어가 더는 빗나가지 않는다. 중복 회수는 `maintenance cache_merge`([REQUEST_LEDGER.md](REQUEST_LEDGER.md) §3.1) |
| 며칠 켜 두면 점점 느려지고 메모리가 는다 | SQLite 세션 누수 | 서버 모니터의 `db_pool` 을 본다 — 한가한데 `live` 가 0 이 아니거나 `overflow` 가 늘면 누수다(§8.1). 임시 완화는 재시작, 원인은 `with store.session():` 을 벗어나지 못하는 경로 |
| 특정 IP 가 서버를 점유 | 스크립트 폭주 | `server block add ip <주소>` · `max_parallel_per_ip` ↓ |

---

## 10. 검증

| 무엇 | 어떻게 |
|---|---|
| 요청 격리·락·대기열·속도 제한·취소·스케줄러·카탈로그 | `python -m unittest tests.test_concurrency_0915` (32개) |
| **질의 경로가 쓰기 잠금을 쥐지 않는가** (§8.0) · 임베딩 캐시 적중·이름 정규화·미커밋 감지 | `python -m unittest tests.test_db_lock_0923` (7개) |
| **자원 누수** — 연결 회계·부드러운 상한·스레드/진행 표시 복귀 | `python -m unittest tests.test_resource_limits` (9개, §8.1) |
| **timeout·retry 동작과 세 창구 정합** | `python tools/verify/verify_timeouts.py` (47개) — 재시도로 살아나기·회로 차단·강등·취소·죽은 외부 소스, 그리고 §1.5 의 **Web=CLI=MCP 같은 값** 비교(§4.1) |
| 동시 30건 질의 · 빌드 중 질의 · 긴 작업 취소 · DEBUG 로그 분리 | 같은 파일의 `StressTest` |
| 무작위 입력(Web·MCP·CLI)에 서버가 버티는가 | `python tools/verify/verify_monkey.py --requests 1500 --threads 16` |
| 모든 Web 엔드포인트 | `python tools/verify/verify_web.py` (255 요청) |
| 모든 CLI 명령 | `python tools/verify/verify_cli.py` (222 명령) |

결과와 해석: [VERIFICATION_0915.md](history/2026-09-15/VERIFICATION_0915.md).
