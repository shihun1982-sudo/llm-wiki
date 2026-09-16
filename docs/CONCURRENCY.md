# 다중 사용자 동시 처리 · 요청 관리 · 모니터링 (2026-09-15)

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
| 대기가 길어지면? | 대기열 64건·120초를 넘으면 **503 + 안내**. 무한 대기하지 않는다 |
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
- `always`: 전체 리빌드 중에도 질의 허용(리빌드 중 결과가 섞여 보일 수 있음).

쓰기는 **writer preference** 다. 쓰기가 기다리기 시작하면 새 읽기는 뒤에 줄을 서므로, 질의가 계속 들어와도 빌드가 굶지 않는다.

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
| `concurrency.queue_max` | 64 | 대기열 상한 (초과 → 503 `queue_full`) |
| `concurrency.queue_timeout_s` | 120 | 대기 최대 시간 (초과 → 503 `queue_timeout`) |
| `concurrency.write_wait_timeout_s` | 600 | 쓰기가 진행 중인 읽기를 기다리는 최대 시간 |
| `concurrency.read_wait_timeout_s` | 900 | 읽기가 배타 작업을 기다리는 최대 시간 |
| `concurrency.reads_during_build` | `incremental` | 빌드 중 읽기 허용 범위: `always`(항상) · `incremental`(증분 빌드 중에만) · `never` |
| `rate_limit.enabled` | true | 속도 제한 전체 on/off |
| `rate_limit.per_user_per_min` | 60 | 사용자별 분당 요청 수 (0 = 무제한) |
| `rate_limit.per_ip_per_min` | 120 | IP 별 분당 요청 수 |
| `rate_limit.query_per_user_per_min` | 20 | 사용자별 분당 **질의** 수 (LLM 비용 보호) |
| `rate_limit.exempt_roles` | `["admin"]` | 제한을 받지 않는 역할 |
| `timeouts.query_s` | 900 | 질의 하나의 최대 시간 |
| `timeouts.search_s` | 120 | 검색(`/api/search`)의 최대 시간 |
| `timeouts.mcp_s` | 900 | MCP 도구 호출의 최대 시간 |
| `timeouts.job_s` | 0 | 백그라운드 작업(빌드 등) 최대 시간. 0 = 무제한 |
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
- **자동**: `timeouts.query_s`(기본 900) · `search_s` · `job_s` · `mcp_s` 를 넘으면 watchdog 이 취소한다. 0 = 제한 없음.

빌드를 취소해도 **지금까지의 진행은 보존**된다(임베딩 체크포인트·커밋). 다음 빌드가 이어서 끝낸다.

> 한계: 이미 전송된 LLM HTTP 요청 자체는 중간에 끊지 못한다. 최악의 대기는 그 호출의 `timeout_s` 로 제한된다
> (역할별 설정은 [BRINGUP_GUIDE](BRINGUP_GUIDE.md) §4.3 / 아래 §7).

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

- **WAL** 모드라 읽기는 쓰기를 막지 않는다.
- 스레드마다 연결을 빌려 쓴다(`db_pool_size`, 기본 16 — 동시 실행 수 이상으로).
- 쓰기끼리는 `db_busy_timeout_s`(기본 60초)만큼 기다린다. 다른 **프로세스**(CLI 빌드·서버 워처)와도 이 규칙이 적용된다.
- 관측용 기록(요청 프로파일·질의 로그)이 잠금을 못 얻으면 **건너뛰고 경고만 남긴다** — 답변은 정상 반환된다.
- 스냅샷 복원은 모든 연결을 닫고 파일을 바꾼 뒤 다시 여는 `Store.reopen()` 으로 처리한다(Windows 파일 잠금 재시도 포함).

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
| 질의가 갑자기 다 느려짐 | 전체 리빌드가 배타 실행 중 | `진행 중 작업` 탭에서 확인. 야간으로 옮기거나(SCHEDULER) `reads_during_build=always` |
| “다른 작업이 끝나기를 기다리는 중…” 이 길다 | 배타 작업(빌드/복원) 대기 | 진행 패널에서 남은 시간 확인, 필요하면 중지 |
| 특정 모델만 계속 실패 | 게이트웨이 장애 | `server circuits` 로 차단 확인 → 원인 해결 후 `server circuits reset` |
| CLI 질의가 `database is locked` | 서버 워처/빌드와 겹침 | 기본값으로 이미 건너뛰고 응답한다. 계속되면 `db_busy_timeout_s` ↑ 또는 `auto_build_interval` ↑ |
| 특정 IP 가 서버를 점유 | 스크립트 폭주 | `server block add ip <주소>` · `max_parallel_per_ip` ↓ |

---

## 10. 검증

| 무엇 | 어떻게 |
|---|---|
| 요청 격리·락·대기열·속도 제한·취소·스케줄러·카탈로그 | `python -m unittest tests.test_concurrency_0915` (32개) |
| 동시 30건 질의 · 빌드 중 질의 · 긴 작업 취소 · DEBUG 로그 분리 | 같은 파일의 `StressTest` |
| 무작위 입력(Web·MCP·CLI)에 서버가 버티는가 | `python tools/verify/verify_monkey.py --requests 1500 --threads 16` |
| 모든 Web 엔드포인트 | `python tools/verify/verify_web.py` (255 요청) |
| 모든 CLI 명령 | `python tools/verify/verify_cli.py` (222 명령) |

결과와 해석: [VERIFICATION_0915.md](VERIFICATION_0915.md).
