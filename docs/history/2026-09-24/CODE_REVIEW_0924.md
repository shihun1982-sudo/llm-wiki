# CODE REVIEW 2026-09-24 — 요청 원장 회차의 **사후 코드 리뷰와 전체 검증**

> **이 문서의 지위**: 2026-09-23 회차(동시 질의 DB 잠금 · 종류별 한도 · 요청 원장)가 **의도대로 구현됐는지,
> 부수 피해는 없는지**를 코드와 실측으로 되짚은 기록이다. 계획과 설계 근거는
> [IMPLEMENTATION_PLAN_0923.md](../2026-09-23/IMPLEMENTATION_PLAN_0923.md), 운영·포팅 절차는
> [REQUEST_LEDGER.md](../../REQUEST_LEDGER.md) 에 있다. 여기에는 **리뷰에서 나온 결함 15건과 그 처리**를 남긴다(1~6 은 첫 리뷰, 7~9 는 세 창구 동시 부하 시험을 만들면서, 10 은 다른 콘솔 인코딩에서 전체 검증을 되풀이하면서, 11~13 은 멍키 테스트의 결과를 의심하고 서버 안을 들여다보면서, 14 는 하네스 전수 실행에서, 15 는 옛 스냅샷 폴더 삭제 뒤 동작 검토에서 드러난 것).

---

## 0. 결론 한 장

| # | 리뷰에서 나온 것 | 성격 | 처리 |
|---|---|---|---|
| 1 | **정상 종료(Ctrl+C) 때 원장 writer 를 비우지 않았다** | 기능의 목적을 정면으로 깨는 결함 | `serve()` 종료 처리에서 `reqledger.stop()` 호출 |
| 2 | **단위 테스트가 실사용 원장에 한 번에 700~800줄을 썼다** | 관측 데이터 신뢰성 파괴 | 환경변수 `LLMWIKI_LEDGER_DIR_PATH` 신설 + `tests/__init__.py` 에서 묶음 전체 격리 + 회귀 시험 3건 |
| 3 | **CLI 질의가 원장에 `request_id`·`run_id` 를 싣지 않았다** | 요청한 기능(상세로 가는 링크)이 CLI 경로에서만 끊김 | `progress.set_result()` 신설, CLI 질의·포렌식에서 호출 |
| 4 | **재현 하네스가 관리 API 를 잘못 호출**해 시나리오 B·C 가 사실상 검사하지 않았다 | 검증이 거짓 OK | 행동 이름 수정(`set_limits`/`values`) · 점검 모드 복구 경로 추가 |
| 5 | **`verify_ledger_merge` 의 지표가 헛경보**를 낸다(거절된 요청까지 분모에 넣음) | 검증 설계 오류 | 완료된 요청만 분모로, 실패 시 **해당 줄을 출력** |
| 6 | 계획 문서가 **미채택 항목을 미구현처럼** 적어 두었다 | 문서-코드 불일치 | 미채택으로 명시하고 이유를 적음 |
| 7 | **여러 프로세스가 같은 원장 파일에 쓰면 줄이 깨진다** — Windows 의 `O_APPEND` 는 프로세스 간 원자적이지 않다 | 기능의 목적을 정면으로 깨는 결함 | 파일 잠금(`msvcrt.locking`/`fcntl.flock`) + 깨진 줄 카운터 + 회귀 테스트 |
| 8 | **짧게 살다 가는 프로세스(CLI)가 종료 시 버퍼를 버렸다** | 같은 결함의 다른 얼굴 | `atexit` 으로 모든 프로세스에서 비우고, 종료 중에는 남은 것을 한꺼번에 쓴다 |
| 9 | 세 창구 부하 시험 자체가 **없었다** | 검증 공백 | `verify_three_surface_load.py` 신설 (30명이 Web·CLI·MCP 로 동시에) |
| 15 | **단위 테스트가 실사용 `logs/` 에 회당 약 6,800줄을 쓰고, 한 테스트는 실사용 `requests` 표에 행을 남긴다** — 62개 모듈 중 18개만 로그 폴더를 격리했고, `discover -s tests` 는 `tests/__init__.py` 를 임포트하지 않으며, 17개 모듈의 tearDown 이 환경변수를 `pop` 으로 지워 묶음 기본값까지 없앴고, 콘솔 인코딩 테스트는 CLI `query` 를 실제 config·색인으로 돌렸다(결함 2 와 같은 종류) | 관측 데이터 신뢰성 | 이름순 첫 모듈 `tests/test_00_isolate.py` + `config.set_path_fallback`(pop 에 지워지지 않는 대체 기본값) · 콘솔 테스트를 임시 config 로 · 실측: 로그·원장·requests 전후 변화 0 |
| 14 | **admin 질의 이력의 IP·에이전트가 새 행부터 빈다** — 09-23 에 질의 로그 원천을 `requests` 로 합쳤는데 INSERT 가 role·via·ip·agent 를 쓰지 않았다(옛 행 마이그레이션만 채움) | 09-23 회차의 회귀 | `store.log_request` 가 진행 레지스트리의 client 에서 네 값을 함께 기록 · 회귀 테스트 2건 · `verify_web` 379/379 |
| 13 | **`/api/wiki/page` 가 이상한 이름에 500 을 내고, GET 은 `..` 로 위키 폴더 밖 .md 를 읽는다** — 멍키가 이름 `?` 를 보내자 `OSError: Invalid argument` | 입력 검증 누락 + 경로 탈출 | 이름 검증 함수 하나를 GET·POST 양쪽에 배선(경로 구분자·NUL·제어 문자·`<>:"|?*`·`.` 시작·예약 이름·120자 초과 → 400) · content 는 문자열만 · 회귀 테스트 5건 |
| 12 | **stderr 가 막히면 서버 전체가 멎는다** — 멍키 하네스가 서버를 `stdout=PIPE` 로 띄우고 읽지 않았고, Web 콘솔 CLI 의 argparse 오류가 서버 stderr 로 나갔다. 파이프가 찬 순간 그 쓰기가 영원히 막혔고 그 스레드가 **배타 잠금**을 쥐고 있었다(실측 28분, 뒤에 36건 대기) | 검증 환경 결함 + 제품 결함(콘솔 오류가 응답에 없음) | 하네스 4개가 파이프를 스레드로 비움 · `run_captured` 가 stderr 도 응답에 담음 · 회귀 테스트 3건 · 기동 문서에 "파이프를 읽지 않고 띄우지 말 것" |
| 11 | **대기열에서 기다리던 요청의 클라이언트가 끊어도 자리를 비우지 않는다** — 폭주 뒤 `queue_full` 128/128 이 5분 넘게 그대로. 멍키 하네스는 200 본문을 버려 "대기열 비움" 을 거짓 OK | 기능의 목적(산 사용자가 거절되지 않게)을 깨는 결함 + 검증이 거짓 OK | 소켓 EOF 를 1초마다 보고 자리를 비움(`concurrency.drop_disconnected_waiters`) · 하네스 `req()` 가 본문을 돌려줌 · 회귀 테스트 9건 |
| 10 | **콘솔이 cp949 인 환경에서 테스트 3건과 검증 스크립트가 실패**한다 — 목업 자식 프로세스가 로케일 인코딩으로 쓰고 부모는 UTF-8 로 읽는다 | 환경 의존 검증 (포팅 환경에서 거짓 FAIL) | 목업(headless·MCP)의 표준 입출력을 UTF-8 로 고정 · `PYTHONUTF8` 허용 목록 추가 · 검증 스크립트 4개 UTF-8 출력 · 건강 점검 순서 의존 단언 제거 |

**전체 검증 결과**(2026-09-24):

```
python -m unittest discover -s tests               Ran 784 tests — OK
python tools/verify/verify_request_ledger.py       RESULT OK   (A·B·C 전부)
python tools/verify/verify_three_surface_load.py   RESULT OK   (30명이 Web·CLI·MCP 로 동시에)
python tools/verify/verify_surface_align.py        RESULT OK   (CLI 49 · Web 114 · MCP 20 전수 일치)
python tools/verify/verify_docs.py                 RESULT OK   (어긋남 0 · 고아 문서 0)
python tools/verify/verify_ledger_merge.py         항목 26/26 대조 OK · 지표 1건 잔여(§4)
테스트가 실사용 원장에 쓴 줄                       수정 전 700~800줄/회 → **0줄**
```

**재검증**(2026-09-24, 결함 10 처리 뒤 — `PYTHONUTF8`·`PYTHONIOENCODING` 없는 **cp949 콘솔**에서):

```
python -m unittest discover -s tests               Ran 784 tests — OK   (수정 전 같은 콘솔에서 failures=3)
python tools/verify/verify_request_ledger.py       RESULT OK
python tools/verify/verify_three_surface_load.py   RESULT OK   (CLI 12↔12 · Web 질의 36↔36 · 깨진 줄 0 · locked 0 · 폴링 p95 28ms)
python tools/verify/verify_surface_align.py        RESULT OK   (수정 전 UnicodeEncodeError)
python tools/verify/verify_stage_align.py          RESULT OK
python tools/verify/verify_docs.py                 RESULT OK
python tools/verify/verify_ledger_merge.py         RESULT PROBLEMS — §4 의 잔여 픽스처 10줄뿐, 새 문제 없음
실사용 원장                                        테스트·하네스 실행 뒤에도 변경 없음
```

**하네스 전수 실행**(2026-09-24 저녁, 사용자 질문 "Web·MCP·CLI 동시 접근 멍키·스트레스 다 했나" 에 답하며 — 결함 11·12·13 을 고친 뒤):

```
verify_monkey            RESULT OK (500 0건 · 폭격 뒤 정상 질의 OK 0.5s · 대기열 비움 실측)   ← 결함 13 수정 뒤 재확인
verify_soak              RESULT OK (60초 혼합 부하 4,002건 · 5xx 0 · 거절 0 · p95 974ms)
verify_three_surface_load RESULT OK  verify_request_ledger RESULT OK  verify_build_load RESULT OK
verify_tri_surface       RESULT OK  verify_mcp 154/154   verify_collab_many 20/20   verify_timeouts RESULT OK
verify_llm_switch        RESULT OK  verify_buttons RESULT OK  verify_settings_sync RESULT OK  verify_web 379/379 (§2.12 수정 뒤)
verify_ui_wiring         RESULT OK (09-23 잔여 3건 정리 뒤)  verify_stage_align RESULT OK  verify_surface_align RESULT OK  verify_docs RESULT OK
python -m unittest discover -s tests   Ran 803 tests — OK
```

**결함 15 처리 뒤 격리 실측** (2026-09-25 00:0x, `discover -s tests` 826건 OK 전후로 실사용 파일 비교):

```
logs/llmwiki.log  20430 → 20430   error.log 7050 → 7050   query.log 13752 → 13752
data/ledger        3046 → 3046 줄   requests 표 최대 id 4165 → 4165
(수정 전 같은 실행: llmwiki +6,796 · query +4,572 · error +94 · requests +1)
```

---

## 1. 요청 5건이 의도대로 구현됐는가

리뷰의 첫 기준은 "코드가 도는가" 가 아니라 **사용자가 말한 것이 그대로 됐는가** 다.

| 요청(원문 요지) | 구현 | 의도와 맞는가 |
|---|---|---|
| ① "읽기만 하는데 왜 lock 이 잡히나" | 원인이 관측 기록이 아니라 **커밋 없는 `cache_put`** 이었다. 질의 경로에서 즉시 커밋 + 끝 쓰기를 한 트랜잭션으로 | **맞다.** 사용자의 가설("읽기만 하면 lock 이 필요 없지 않나")도 옳았다 — WAL 에서 읽기는 실제로 아무것도 막지 않았고(0.02ms), 막은 것은 **질의가 몰래 하던 쓰기**였다 |
| ② "질의와 채널 검색에 별도 한도" | `concurrency.classes` · `rate_limit.classes` | **맞다.** 다만 "별도 한도" 를 슬롯 분할이 아니라 **상한 + 예약** 으로 구현했다(질의 6 < 전체 8). 설정이 하나 줄고, 질의가 없을 때 검색이 8개까지 쓸 수 있다 |
| ③ "모든 요청이 list-up 되고 누르면 진행 상태" | 요청 원장 + 「📋 요청 (전체)」 탭 | **맞다.** 중간에 사용자가 덧붙인 "요청 프로파일·진행 중 작업·질의 로그를 합쳐 한눈에" 까지 반영했고, 정보 유실이 없음을 `verify_ledger_merge.py` 가 26항목으로 대조한다 |
| ④ "사라지는 요청 재현해 봐" | `verify_request_ledger.py` | **맞다.** 24건 중 **12건(50%)이 예전 구조에서는 기록이 없었을 것**임을 격리 환경에서 재현했다 |
| ⑤ "세 창구 정합 + 포팅 문서" | `verify_surface_align` 전수 일치 · `REQUEST_LEDGER.md` | **맞다.** 2026-09-24 에 §13 **포팅을 LLM 에게 시키는 프롬프트**(파악→적용→검증 3단계)를 추가했다 |

---

## 2. 결함별 상세

### 2.1 정상 종료에서 원장이 잘렸다 (가장 심각)

원장 writer 는 **daemon 스레드**이고 `flush_ms`(200ms) 동안 줄을 버퍼에 들고 있다.
그런데 `serve()` 의 종료 처리는 watcher·scheduler·소켓만 정리하고 **writer 를 비우지 않았다.**
Ctrl+C 로 서버를 내리면 마지막 수백 ms 의 기록이 통째로 사라진다.

> 이 기능의 존재 이유가 "요청이 기록 없이 사라지지 않게" 하는 것이므로, **종료 때 기록을 잃는 것은
> 단순한 버그가 아니라 기능의 부정**이다. 그래서 다른 어떤 항목보다 먼저 고쳤다.

`reqledger.stop()` 은 이미 "멈추고 남은 줄을 마저 쓴다" 로 구현돼 있었는데 **아무도 부르지 않았다** —
만들어 두고 배선하지 않은 전형적인 경우다. CLI 경로(`progress._led_close`)는 `flush(1.0)` 을 부르고 있어
문제가 없었고, 그래서 CLI 로 확인할 때는 드러나지 않았다.

### 2.2 테스트가 실사용 원장을 오염시켰다

`reqmgr.get_manager()` 는 **프로세스당 하나**의 RequestManager 를 만들어 두고 재사용하며,
`data_dir` 은 **처음 만들 때만** 쓰인다. 그래서 테스트 하나가 `data_dir` 없이 먼저 만들면
그 프로세스의 **나머지 전부**가 프로젝트의 `data/ledger` 로 간다.

실측: 전체 테스트 1회에 **770~802줄**이 실사용 원장에 섞여 들어갔다(사용자 `a`/`b`/`c`,
라벨 `eval 1`·`q`·`질의 ? 그리고 ? 끝` 등). 원장은 장애 분석의 근거이므로 **섞이면 못 쓴다.**

고친 방식은 세 겹이다 — 앞의 두 겹만으로는 "새 테스트가 하나 빠뜨리면 다시 샌다" 를 막지 못한다.

1. 개별 테스트 3곳이 `ledger.dir` 을 임시 폴더로 지정하게 했다.
2. **`LLMWIKI_LEDGER_DIR_PATH`** 를 신설했다 — 설정보다 세고, 운영에서도 원장만 다른 디스크로 보낼 때 쓴다.
3. `tests/__init__.py` 가 묶음 전체에 그 변수를 걸어 **한 줄로 못을 박았다.**

회귀 시험 `tests/test_ledger_isolation.py`(3건)는 ①설정으로 격리되는가 ②환경변수가 설정을 이기는가
③`configure()` 하지 않은 프로세스는 아무 데도 쓰지 않는가 를 보고, **매번 실사용 원장의 바이트 수가
늘지 않았는지 확인**한다. 이 시험은 `tests/__init__.py` 의 안전망을 일부러 걷어내고 돈다 —
안전망에 기대면 진짜 회귀를 놓치기 때문이다.

### 2.3 CLI 질의는 상세로 갈 수 없었다

원장 목록에서 📄 요청 프로파일과 📜 로그로 가는 열쇠가 `request_id`·`run_id` 다.
웹 비동기 잡 경로는 회차 중에 이것을 싣도록 고쳤지만 **CLI 경로(`progress.cli_monitor`)는 빠져 있었다.**
실측으로 완료된 질의의 10%가 링크 없는 채로 남았고, 전부 CLI 발 요청이었다.

`progress.set_result(token, **fields)` 를 만들어 "끝나고 나서야 알 수 있는 식별자" 를 붙이고,
`_led_close` 가 그것을 함께 닫도록 했다. 격리 환경에서 CLI 질의 1건을 돌려 확인했다:

```
CLI 질의 원장 1건
  status=done   request_id=1   run_id=8cc540abfd39
```

덤으로 `forensic expect` 의 원장 종류를 `query` → `forensic` 으로 바꿨다. 그것은 사용자 질의가 아니라
**이미 남은 질의를 다시 따라가는 분석**이라, 질의로 세면 "완료됐는데 request_id 가 없는 질의" 로
잘못 집계된다. 대신 분석 대상 요청의 id 를 실어 그 요청으로 갈 수 있게 했다.

### 2.4 재현 하네스가 거짓 OK 를 냈다

시나리오 B(점검 모드 거절)는 `/api/admin/server` 를 `{"action": "limits", "set": {...}}` 로 불렀는데
실제 이름은 `{"action": "set_limits", "values": {...}}` 였다. 400 이 돌아왔지만 하네스는
**응답을 보지 않고** "권한이 없어 안 걸렸을 수 있다" 며 통과시켰다.

같은 오타가 `--capacity` 측정에도 있었다. 즉 **동시 실행 수를 바꾸지 않고 처리량을 재고 있었다** —
그 환경의 슬롯 수를 정하는 근거로 쓰라고 만든 측정이 아무 의미가 없었다.

거기에 더해, 격리 환경에는 `security.json` 이 없어 인증이 `off` 이고 그러면 모든 요청이 **admin 으로
취급**된다. 점검 모드의 기본 허용 역할이 admin 이므로 **점검 모드를 켜도 그냥 통과한다.**
허용 역할에서 admin 을 빼는 방식으로 바꿨는데, 이때 **관리 API 자신도 막혀** 되돌릴 수 없게 된다
(`check_access` 는 관리 경로를 예외로 두지 않는다). 실제로 그 상태가 시나리오 C 까지 오염시켜,
C 는 "강제 종료된 요청" 이 아니라 "거절된 요청" 을 세고 있었다.
복구는 설정 파일을 직접 고치고 서버를 다시 띄우는 경로를 넣었다. **운영에서도 같은 함정이므로**
[REQUEST_LEDGER.md](../../REQUEST_LEDGER.md) §13.4 에 주의로 적었다.

고친 뒤의 결과:

```
[A] 보낸 24 · requests 테이블 12 · 원장 24 (done 12 + rejected 12) · 누락 0
[B] HTTP 503 · 거절에 '무엇이 막았나 · 설정 키 · 해제 방법' 이 함께 기록됨
[C] 강제 종료 시 진행 중이던 6건이 '끝이 없는 기록' = 중단 으로 남음
```

### 2.5 검증 지표가 헛경보를 냈다

`verify_ledger_merge.py` 가 "query 요청 중 `request_id` 보유율" 을 보는데, **거절·시간초과·취소된
요청까지 분모**에 넣고 있었다. 그것들은 파이프라인에 들어가지도 못했으니 id 가 없는 것이 정상이고,
결국 **부하가 걸릴수록 비율이 떨어져** 정상 동작을 경보로 만든다.

완료된 요청만 분모로 삼고, 실패하면 **해당 줄(시각·창구·라벨)을 출력**하게 했다. 지금 남은 10건은
모두 2.2 를 고치기 전에 들어온 테스트 픽스처이며 보존 기간(30일)이 지나면 사라진다.

### 2.6 문서-코드 불일치

계획 문서가 §1.6-G(쓰기별 토글)와 §1.6-H(비동기 DB writer)를 "미구현" 으로 적어 두어,
읽는 사람이 **아직 할 일**로 오해할 수 있었다. 둘 다 **하지 않기로 판단한 것**이다 —
잠금의 원인이 관측 기록이 아니었으므로 토글은 끌 이유가 없어졌고, 잠금 보유가 1ms 가 되어
비동기 writer 도 필요가 없어졌다. 미채택과 그 이유로 고쳐 적었다.

---

### 2.7 세 창구 동시 부하 — 시험이 없었고, 만들자마자 결함이 나왔다

사용자가 "30명이 Web·CLI·MCP 로 동시에 요청하는 경우도 시험했나" 라고 물었을 때 답은 **아니오**였다.
기존 하네스는 셋 중 하나만 밀거나(`verify_request_ledger.py`·`verify_build_load.py` = HTTP 질의),
세 창구를 쓰지만 **순차 실행**이었다(`verify_tri_surface.py` = 같은 답을 주는가).

그래서 `tools/verify/verify_three_surface_load.py` 를 만들었다 — 브라우저 18명(비동기 질의 + 1.5초 폴링 + 검색),
MCP 6명(`POST /mcp` 도구 호출), CLI 6명(**별도 프로세스**로 `python -m llmwiki query`)이 동시에 돈다.
세 창구를 함께 미는 것이 중요한 이유: **CLI 는 별도 프로세스라 같은 SQLite 파일과 같은 원장 파일에
다른 연결로 붙는다.** 프로세스 안에서만 돌리는 시험은 이 경계를 건드리지 못한다.

돌리자마자 **CLI 질의 18건 중 3~7건의 접수 기록이 사라졌다.** 두 가지 원인이 겹쳐 있었다.

**(가) Windows 의 `O_APPEND` 는 프로세스 간 원자적이지 않다.**
CRT 가 "파일 끝으로 seek → write" 를 하므로, 두 프로세스가 같은 끝 위치를 잡으면 **한쪽이 다른 쪽의 줄을
덮어쓴다.** 파일에 `ing"}` · `nning"}` 같은 **꼬리 조각**이 남은 것이 증거였다(`"status": "running"}` 의 끝).
깨진 줄은 읽을 때 조용히 버려지므로 결과는 "요청이 기록 없이 사라진다" — 이 기능이 막으려는 바로 그것이다.

고친 방법은 **파일 잠금**이다(`msvcrt.locking` / `fcntl.flock`, 둘 다 표준 라이브러리).
프로세스마다 파일을 나누는 방법도 검토했지만 **CLI 는 호출마다 새 프로세스라 하루에 파일이 수천 개**가 되어
택하지 않았다. 그리고 깨진 줄을 **세기 시작했다**(`stats().writer.corrupt`) — 조용히 버리는 것이
문제의 절반이었기 때문이다.

**(나) 짧게 살다 가는 프로세스가 종료 시 버퍼를 버렸다.**
잠금을 고친 뒤에도 18건 중 1건이 빠졌다. writer 는 daemon 스레드라 프로세스가 끝나면 들고 있던 줄과 함께
죽는다. §2.1 의 서버 종료 문제와 **같은 결함의 다른 얼굴**이다. `atexit` 에 비우기를 걸어
**모든 프로세스**에 적용했고, 종료 중에는 남은 것을 잘게 쪼개지 않고 한꺼번에 쓰도록 했다
(작은 배치마다 파일 잠금을 새로 잡아야 해서 CPU 가 바쁠 때 제한 시간 안에 다 비우지 못했다 —
전체 테스트와 함께 돌릴 때 1,800줄 중 24줄이 그렇게 남았다).

회귀 테스트 `tests/test_ledger_multiprocess.py` 는 6개 프로세스가 **같은 시각에** 각 150건을 쓰게 한다.
처음 만든 판은 자식들을 순서대로 띄워 쓰기가 겹치지 않았고 **결함을 놓쳤다** — 잠금을 끄고 돌려서
실제로 깨지는지 확인한 뒤에야 시험에 이가 생겼다(잠금 없이 깨진 줄 2개 → 잠금 있으면 0개).

최종 결과(30명 · 436회):

```
5xx 0건 · 거절 0건 · 깨진 원장 줄 0개 · database is locked 0건
보낸 수 ↔ 원장: CLI 18↔18 · Web 질의 54↔54   (창구마다 일치)
질의 p95 4.1초(끝까지) · 접수 p95 0.5초 · 검색 p95 1.9초 · 화면 폴링 p95 28ms
```

화면 폴링 p95 **28ms** 가 이 표에서 가장 중요한 숫자다 — 질의가 30명분 몰려 있어도
브라우저 화면은 계속 응답한다는 뜻이고, 그것이 비동기 질의로 바꾼 이유였다.

### 2.8 검증이 환경(콘솔 인코딩)에 따라 결과가 달랐다

이 리뷰를 이어서 하던 세션이 **`PYTHONUTF8` 도 `PYTHONIOENCODING` 도 없는 cp949 콘솔**에서 시작됐고,
그 조건에서 같은 코드가 `Ran 784 tests — FAILED (failures=3)` 을 냈다. 앞 세션의 OK 는 콘솔이 UTF-8 이었기 때문이다.
"어느 환경에서 돌리느냐로 결과가 갈리는 검증" 은 포팅 문서의 기대 결과(§10 "RESULT OK")를 믿을 수 없게 하므로 결함으로 다룬다.

| 실패 | 원인 | 처리 |
|---|---|---|
| `test_headless_stall…` — 부분 답변 "부분 답변" 이 U+FFFD 로 | `python -m llmwiki.headless --mock` 자식이 stdout 이 파이프일 때 **로케일(cp949)로 쓰고**, 부모 `_run_streaming` 은 언제나 UTF-8 로 해석한다 | 목업 진입점에서 `sys.stdout/stderr/stdin.reconfigure(encoding="utf-8")` (`_utf8_stdio`). 실제 opencode 가 UTF-8 로 말하므로 대역도 그래야 한다 |
| `test_mcp_source_mock` — ISSUE-9001 이 상위 5건에 없음 | 같은 원인. 목업 MCP 서버(`mcp_client --mock-server`)의 한글 제목·본문이 깨져 색인돼 검색이 안 맞았다 | 같은 처리 |
| `test_health_and_build_gate` — `alerts[0]` 이 `corpus_dirs` 가 아니라 `console_encoding` | 건강 점검이 cp949 콘솔에 **정상적으로** 경고를 냈고, 테스트가 경고 순서에 의존했다 | 순서가 아니라 포함 여부를 단언 |
| `verify_surface_align.py` 가 `UnicodeEncodeError` 로 죽음 | 다른 verify_* 는 stdout 을 UTF-8 로 재설정하는데 4개(`surface_align`·`three_surface_load`·`docs`·`stage_align`)는 빠져 있었다 | 같은 재설정 추가 |

덧붙여 헤드리스 자식 환경변수 허용 목록에 `PYTHONUTF8` 을 넣었다 — 운영자가 `PYTHONUTF8=1` 로 콘솔을 맞춰 두었을 때
파이썬 기반 에이전트에도 같은 설정이 이어지도록. 목록의 `PYTHONIOENCODING` 과 같은 성격이다.

### 2.9 끊긴 클라이언트의 요청이 대기열을 30분 동안 차지했다 — 그리고 하네스는 그것을 보지 못했다

사용자가 "Web·MCP·CLI 동시 접근 멍키·스트레스 테스트를 다 했느냐" 고 물어 안 돌린 하네스를 마저 돌리다 나왔다.
`verify_monkey.py`(1,200건 폭격 · 16 스레드 · CLI 15) 는 `RESULT OK` 였지만 한 줄이 이상했다:

```
사후 정리: 대기열 비움 (진행/대기 (0, 0, 'writer:-/0'))
사후 정리: 튜닝 기본값 복구 실패 503
사후 확인: 서버 살아 있음 · 정상 질의 FAIL code=503 (0.0s)
  거절 사유 단서: {"running": [], "queued": [], "queued_n": 0, …, "lock": null, "limits": null,
                  "response": "… 대기열이 가득 찼습니다 (128) … current: 128"}
```

활동 목록은 비어 있다는데 서버는 128명이 기다린다고 한다. 둘 다 같은 `RequestManager` 인스턴스의 같은 `active` 사전을
보므로 있을 수 없는 조합이다. 단서는 `"lock": null, "limits": null` — `activity()` 는 그 두 키를 **항상** 채운다. 즉 하네스가
본 것은 활동 목록이 아니라 **빈 문자열**이었다. `req()` 가 200 응답이면 `r.read()` 를 하고도 `""` 를 돌려주고 있었다.
`drain()` 은 `{}` 를 파싱해 "진행 0 · 대기 0 → 비움" 으로, `capacity_note()` 도 같은 빈 것을 보고했다.
이 하네스의 "폭격 뒤 정상 질의" 판정 주석에는 "대기열이 빠지는 중이면 정상" 이라고 적혀 있었다 — **빠지는지 본 적이 없으면서.**

그래서 진짜 상태는 이렇다. 폭격 중 166건이 클라이언트 쪽 시간 초과로 끊겼다(`conn-err×166`). 서버는 그 요청들을
대기열에 넣어 둔 채 슬롯을 기다리게 했고, 클라이언트가 떠난 것을 **알아볼 방법이 있는데(소켓 EOF) 보지 않았다.**
질의의 `queue_timeout_s` 는 09-23 회차에서 **1800초**로 늘렸으므로, 그 128건은 30분 동안 자리를 지키며 산 사용자를
`queue_full` 로 돌려보낸다. 하네스의 사후 확인 12회(약 5분)가 전부 503 이었던 것이 그 증거다.
09-23 의 "대기열을 길게, 수명은 `queue_timeout_s` 가 끊는다" 는 결정은 **끊긴 클라이언트도 수명을 다 쓴다** 는 점에서
반쪽이었다. 두 결정이 만나 "폭주 한 번 → 30분 마비" 가 됐다.

| 고친 것 | 어디 | 어떻게 |
|---|---|---|
| 대기 중 연결 끊김 감지 | `reqmgr.ticket(alive=…)` · `_wait_read_slot` | 대기 루프가 1초마다 `alive()` 를 묻고, False 면 읽기 락을 놓고 `Cancelled(by=server, reason=클라이언트가 연결을 끊었습니다)`. 원장에는 `cancelled`, `counters.abandoned_queue` 증가 |
| 소켓 EOF 판정 | `server.Handler._client_alive` | `select` 로 읽을 수 있는지 보고, 있으면 `MSG_PEEK` 1바이트 — `b""` 면 끊김, 데이터면 파이프라이닝(살아 있음), TLS 처럼 peek 이 안 되면 '모른다'(끊지 않음) |
| 어디서 넘기나 | 동기 질의 · 재실행 · `/api/search`·`/api/cli` 등 일반 POST · `/mcp` | 잡 러너·watcher·CLI stdio 는 소켓이 없어 대상이 아니다 |
| 설정 | `concurrency.drop_disconnected_waiters` (기본 true) | `server.json` · `setup/server.example.json` · `server limits set` · 문서 4곳 |
| 하네스 | `verify_monkey.req()` | 200 본문을 돌려준다. 이제 `drain()` 이 실제 대기열을 본다 |
| 회귀 | `tests/test_abandoned_waiters_0924.py` 9개 | 끊기면 2초 안에 빠짐 · 살아 있으면 기다림 · 끄면 예전 동작 · 콜백 예외는 끊지 않음 · socketpair 로 EOF/데이터/유휴 구분 |

같은 김에 `headless.py` 의 `Cancelled("headless agent 취소됨 …")` 도 고쳤다 — `Cancelled(token, by, reason)` 인데
문구를 token 자리에 넣어 `str(e)` 가 그냥 `cancelled` 였다(원장·화면에 이유가 남지 않았다).

택하지 않은 것: `queue_timeout_s` 를 다시 짧게 — 살아 있는 사용자의 대기까지 끊어 09-23 결정을 되돌리는 셈이라 아니다.
"클라이언트가 끊으면 서버 스레드에서 예외" 는 파이썬 `http.server` 에는 없다(응답을 쓸 때야 안다).

### 2.10 stderr 가 막히자 서버 전체가 멎었다 — 하네스가 판 함정에 제품이 빠졌다

결함 11 을 고치고 멍키 테스트를 다시 돌렸더니 이번에는 **33분이 지나도 끝나지 않았다**(첫 실행 14분). 격리 서버의
활동 목록을 읽어 보니 `cli:nonexistent`(멍키가 일부러 보내는 존재하지 않는 CLI 명령) 한 건이 **배타 잠금을 28분째** 쥐고
"running" 이고, 뒤에 36건이 줄 서 있었다. 수명은 `job_s` = 48시간이니 그대로 두면 이틀을 멎어 있을 상태다.

스택을 떠 보니(`py-spy dump`) 그 스레드는 `argparse._print_message` → `sys.stderr.write()` 에서 멈춰 있었다.
argparse 가 "invalid choice: nonexistent (choose from build, users, …)" 를 **서버 프로세스의 stderr** 에 쓰는데, 하네스가 서버를
`stdout=PIPE, stderr=STDOUT` 으로 띄우고 **한 번도 읽지 않았다.** Windows 파이프 버퍼가 차자 write 가 영원히 막혔고,
`run_captured` 는 stdout 만 잡고 있었으므로 이 출력은 콘솔 응답에도 없었다(사용자는 code=2 에 빈 화면만 봤다).

| 고친 것 | 어디 | 어떻게 |
|---|---|---|
| Web 콘솔 CLI 가 stderr 도 응답에 담는다 | `cli.run_captured` | `redirect_stdout` 과 함께 `redirect_stderr` — 단 **다른 버퍼**로. `--json` 명령은 안내문을 stderr 로 보내 stdout 을 순수 JSON 으로 유지하므로 한 버퍼에 섞으면 파싱이 깨진다(첫 시도에서 `test_presets_apply_restore_diff` 가 잡았다). 응답은 `{code, output, stderr}` 이고, stdout 이 비고 code≠0 이면 stderr 내용을 `output` 으로 보여 준다(사용법 오류가 빈 화면이 아니게) |
| 하네스가 파이프를 비운다 | `verify_monkey` · `verify_build_load` · `verify_llm_switch` · `verify_settings_sync` | Popen 직후 `threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True)`. 나머지 하네스는 원래 `DEVNULL` |
| 회귀 | `tests/test_web_console_stderr_0924.py` 3개 | 알 수 없는 명령·잘못된 플래그의 오류가 `output` 에 있고 프로세스 stderr 에는 아무것도 없다 · `--help` 는 그대로 |
| 기동 문서 | `BRINGUP_GUIDE.md` · `REQUEST_LEDGER.md` §11 | "서버를 감싸는 스크립트가 stdout/stderr 를 파이프로 받으면 **반드시 읽어야** 한다 — 아니면 로그 파일로 보낸다" |

첫 실행(결함 11 을 찾은 그 실행)의 "60초 초과 182건" 과 "폭격 중 정상 질의 지연 최대 180초" 도 이 결함이 만든 숫자였다.
즉 결함 11(끊긴 요청이 대기열 점유)과 결함 12(stderr 막힘)가 **겹쳐서** 나타났고, 11 을 고친 뒤에야 12 가 따로 보였다.
"검증 결과가 이상하면 서버 안을 본다" — 활동 목록과 스택 덤프 두 가지로 15분 만에 특정했다.

**고친 뒤 멍키 재실행** (같은 강도 1,205건 · 16 스레드 · CLI 15):

```
요청 1205건 / 352초 (3 req/s)              ← 첫 실행 838초, 두 번째는 33분 넘게 멈춤
상태 코드: 200×636, 400×234, 404×225, 501×53, conn-err×48, 405×8, 500×1
폭격 중 정상 질의: 13/13 성공 (첫 실행 6/9)
30초 초과 요청 63건                          ← 첫 실행 182건
사후 정리: 대기열 비움 (진행/대기 (0, 0))      ← 이번에는 실제 활동 목록을 읽은 결과다
사후 정확인: 서버 살아 있음 · 정상 질의 OK (0.5s)  ← 첫 실행 FAIL 503 (5분간)
```

남은 `500×1` 이 결함 13 이다.

### 2.11 이상한 위키 페이지 이름에 500 — 그리고 `..` 로 폴더 밖 읽기

멍키의 유일한 500: `POST /api/wiki/page` 에 이름 `?` → Windows 가 파일을 만들 수 없어 `OSError: [Errno 22] Invalid argument`.
잘못된 입력은 400 이어야 하고 500 은 서버 결함이다. 코드를 보니 POST 는 `/` 와 역슬래시만 막았고, **GET 은 이름을 아예
검사하지 않아** `?name=../secret` 로 위키 폴더 밖의 .md 를 읽을 수 있었다(read 권한만 있으면). 파일 이름으로 쓰는 값은
파일 이름 규칙으로 검사한다: 문자열 · 1~120자 · 경로 구분자·NUL·제어 문자·Windows 금지 문자 없음 · `.` 시작 아님 ·
끝이 공백/점 아님 · CON/PRN/AUX/NUL/COMn/LPTn 아님. `Handler._wiki_page_name()` 하나를 GET·POST 가 같이 쓴다.
`tests/test_wiki_page_name_0924.py` 는 검증 함수와 **실제 핸들러**(GET·POST 가 400 · `..` 탈출 불가 · 정상 이름 왕복) 를 본다.

### 2.13 테스트가 실사용 로그를 오염시켰다 — 결함 2 의 로그판

사용자가 작업 폴더 안의 중첩된 옛 스냅샷 폴더(09-22 복사본, 713MB)를 지운 뒤 "동작에 문제 없는지" 를 검토하다 나왔다.
삭제 자체는 문제가 없었다(파일 단위 비교 0건 차이, 참조 0건, 정적 검사·health·단위 테스트·하네스 4종 전부 통과).
그런데 검토 중 `logs/llmwiki.log` 에 23:43:45~47 세 초 동안 **984줄**이 mock 프로바이더로 찍힌 것을 봤다 — 실서버는 ollama 를 쓰므로
이것은 그 시각에 돌던 **단위 테스트**의 흔적이다. 62개 테스트 모듈 중 `LLMWIKI_LOGS_DIR_PATH` 를 지정한 것은 18개뿐이었다.
결함 2(원장 오염)를 고칠 때 "전역 상태를 쓰는 기능은 테스트 격리를 기능의 일부로" 라고 적어 두고 로그는 빠뜨린 것이다.
먼저 `tests/__init__.py` 에 원장과 같은 방식으로 한 줄을 더했다 — 그런데 전체 테스트 뒤 로그가 **여전히 6,795줄** 늘었다.
원인: `python -m unittest discover -s tests` 는 시작 폴더가 곧 최상위라 **패키지 `__init__` 을 임포트하지 않는다**
(실측: discover 뒤 `'tests' in sys.modules` 가 False). 즉 결함 2 때 넣은 원장 격리도 이 실행 방식에서는 `__init__` 이 아니라
개별 테스트 3곳의 수정이 막고 있었던 것이다. 그래서 discover 가 **가장 먼저 임포트하는 모듈** `tests/test_00_isolate.py` 에
같은 환경변수 설정을 두었다(모듈은 이름순으로 임포트되고, 파이프라인은 fixture 에서 만들어지므로 그 전에 걸린다).
패키지로 부르는 경우(`python -m unittest tests.test_x`)는 `__init__` 이 같은 일을 한다.

그래도 로그가 **또 6,796줄** 늘었다. 세 번째 원인: 17개 모듈이 setUpClass 에서 자기 임시 폴더를 환경변수로 걸고
tearDownClass 에서 `os.environ.pop("LLMWIKI_LOGS_DIR_PATH")` 로 **지운다** — 묶음 전체의 기본값도 그때 함께 사라져
그 뒤의 모듈이 실사용 logs/ 로 떨어진 것이다. 환경변수 하나로는 "누가 지워도 남는 기본값" 을 만들 수 없다.
그래서 `config.path_for()` 에 **대체 기본값 표**(`set_path_fallback(name, path)` — 환경변수보다 약하고 코드 기본값보다 세다)를
두고 `test_00_isolate.py`·`__init__.py` 가 `logs_dir` 을 건다. 운영 코드는 이 표를 채우지 않는다.
이 모듈의 테스트 3개가 "경로가 프로젝트 밖을 가리키는가 · `path_for("logs_dir")` 가 환경변수를 따르는가 ·
**환경변수를 지워도** 실사용 logs/ 로 떨어지지 않는가" 를 확인한다.

그래도 33줄이 남았고, 그것이 가장 나쁜 것이었다: `tests/test_console_0915.py` 의 진행 표시 인코딩 테스트가 CLI `query` 를
**프로젝트의 실제 config.json·색인**으로 돌려(자식 프로세스는 부모의 대체 기본값을 물려받지 못한다) 실사용 `requests` 표에
**실행마다 행을 1건씩** 남기고 있었다(`ISSUE-2001 원인`, origin cli — 지난 1시간에 4건). 그 테스트에 임시 config(문서 1개 · mock · hash
임베더 · 임시 data/wiki)를 만들어 `build` → `query` 로 바꾸고, 그 파일의 모든 자식 프로세스에 로그·원장 환경변수를 강제했다.
확인: 전체 테스트 전후로 `logs/llmwiki.log`·`error.log`·`query.log`·원장 줄 수와 `requests` 의 최대 id 가 같다(§0 재검증 표).

### 2.12 admin 질의 이력의 IP·에이전트가 비었다 — 09-23 통합의 회귀

`verify_web` 379건 중 1건 실패: "admin GET queries (IP·에이전트 보임)". `/api/queries` 는 admin 에게 IP·에이전트를 보여 주게
돼 있는데 값이 전부 빈 문자열이었다. 09-23 에 질의 로그 원천을 `query_log` → `requests` 로 합치면서 `store.queries()` 는
`requests.role/via/ip/agent` 를 읽도록 바꿨지만, **`requests` INSERT 는 그 네 열을 쓰지 않았다.** 마이그레이션이 옛 행을
`query_log` 에서 옮겨 채웠기 때문에 09-23 검증 시점에는 값이 보였고, 그 뒤 새로 쌓인 행부터 비었다 — "합칠 때 읽는 쪽만
고치고 쓰는 쪽을 빠뜨린" 전형이다. 진행 레지스트리의 client(서버 핸들러 `_client()` 가 넣는 user·role·via·ip·origin·agent)는
이미 같은 자리에서 origin·user 를 읽는 데 쓰고 있었으므로 네 값을 더 읽어 함께 쓰게 했다. CLI·테스트처럼 client 가 없으면 빈 값.

같은 자리에서 `verify_ui_wiring.py` 가 지적한 09-23 잔여 3건도 정리했다: 원장 화면의 📜 링크가 채우는 로그 필터 id 가
없는 `#log-grep` 이었던 것(→ `#log-run`), 정의 없는 CSS 변수 `--line`(→ `--grid`), 그리고 검사기가 `startswith("/api/ledger")`
로 받는 접두 경로를 "서버에 없는 경로" 로 오판하던 것(검사기에 접두 경로 인식 추가).

---

## 3. 부수 피해(side effect) 점검

의도적으로 바꾸지 않은 것들이 정말 그대로인지 확인한 항목이다.

| 걱정 | 확인 방법 | 결과 |
|---|---|---|
| 빌드가 느려지지 않았나 (`cache_put` 즉시 커밋) | `commit` 기본값은 `False` — 빌드는 예전처럼 배치 커밋 | 경로가 갈려 있어 영향 없음 |
| `db_synchronous=NORMAL` 이 DB 를 위험하게 하나 | WAL 모드에서 `NORMAL` 은 **손상을 일으키지 않는다**. 정전 시 최근 커밋 몇 건(색인·관측 기록)만 날아가고 재빌드로 복구된다 | 허용 가능. 규정이 엄격하면 `FULL` 로 되돌릴 수 있게 설정 키로 뺐다 |
| 동기 질의 호출자(CLI·MCP·기존 클라이언트)가 깨지나 | `async` 를 주지 않으면 예전 경로 그대로 | 783개 테스트 통과 |
| `query_log` 를 읽던 14군데가 깨지나 | `store.queries()`·`get_query()`·`query_users()` 의 **모양은 그대로 두고 원천만** 바꿨다. 짝 없는 옛 행도 함께 보여 준다 | 통과 |
| 기존 Observability 탭이 없어졌나 | 지우지 않았다. 두 요청 비교·⟲ 단계 재실행처럼 성격이 다른 것은 그 화면에 남겼다 | `verify_ledger_merge` 26항목 대조 |
| 원장이 디스크를 잡아먹나 | 요청 1건 ≈ 2줄 × 약 300B → 하루 3,000건이면 **2MB/일**. `keep_days` 30 · `max_mb` 512 | 허용 가능. `include_get=all` 은 10배 이상 늘 수 있어 기본은 `heavy` |
| 원장이 요청 경로를 느리게 하나 | 전용 writer 스레드 + 큐. 요청 스레드는 디스크를 기다리지 않는다 | 비동기 질의 POST 26ms |
| 로그 총량 제한(`log_total_max_mb`)이 원장을 지우나 | 원장은 `logs/` 가 아니라 `data/ledger` 에 둔다 | 정리 대상 아님 |
| `reset logs` 가 원장을 지우나 | 기본 제외. `--include-ledger` 를 명시해야 지운다 | 초기화 사실도 원장에 남는다 |
| 스냅샷 복원이 원장을 되감나 | 원장은 DB 파일이 아니라 append 파일 | 복원 전후 사건을 원장에 남긴다 |

---

## 4. 남은 것

| 항목 | 상태 |
|---|---|
| 실사용 원장에 남은 테스트 픽스처 10건(2026-09-24 00:09~00:21) | 새로 유입되지 않는다(2.2). 보존 기간이 지나면 사라진다. 즉시 지우려면 `ledger prune` 을 쓰되 **같은 기간의 실사용 기록도 함께 지워진다** |
| 스트레스·멍키 테스트를 격리 환경에서 | `verify_request_ledger.py` 가 동시 부하(24~40건)와 강제 종료를 다루므로 이 회차의 목적은 덮었다. 장시간 자원 누수는 `verify_soak.py` 의 몫 |

---

## 5. 이 리뷰에서 얻은 규칙

앞으로 같은 종류의 결함을 만들지 않기 위해 남긴다.

1. **만들어 둔 정리 함수는 호출부까지 확인한다.** `reqledger.stop()` 은 존재했지만 배선되지 않았다.
   "기능이 있다" 와 "그 기능이 불린다" 는 다른 문제다.
2. **검증 코드가 응답을 버리면 검증이 아니다.** 하네스가 관리 API 의 400 을 무시하는 바람에
   두 시나리오가 아무것도 검사하지 않으면서 OK 를 냈다. 상태 코드를 확인하고, 아니면 FAIL 한다.
3. **지표의 분모를 의심한다.** "실행되지도 못한 요청" 을 분모에 넣으면 정상 동작이 경보가 된다.
4. **전역 상태를 쓰는 기능은 테스트 격리를 기능의 일부로 설계한다.** 프로세스당 하나인 관리자와
   모듈 전역 설정이 만나면, 테스트 하나의 실수가 프로세스 전체로 번진다.
5. **격리 환경은 인증이 꺼져 있어 모든 요청이 admin 이다.** 권한·차단·점검 모드를 시험하려면
   그 전제를 먼저 깨야 한다.
6. **부하 시험은 세 창구를 동시에 밀어야 한다.** 한 창구만 밀면 프로세스 경계(같은 DB 파일·같은 원장 파일에
   다른 프로세스가 붙는 것)를 건드리지 못한다. 이번에 나온 두 결함은 모두 그 경계에 있었다.
7. **동시성 시험은 "정말 겹치게" 만들어야 한다.** 자식들을 순서대로 띄우면 쓰기가 겹치지 않아 시험이
   통과해 버린다. 공통 시작 시각을 주고, **고친 것을 일부러 되돌려 실패하는지 확인**한 뒤에야
   그 시험을 믿을 수 있다.
8. **파일 append 가 원자적이라고 가정하지 않는다.** POSIX 의 `O_APPEND` 와 달리 Windows CRT 는
   seek+write 라서 프로세스 간 경쟁에 진다. 여러 프로세스가 한 파일에 쓰는 설계에는 잠금이 필요하다.
11. **서버 프로세스의 표준 출력은 반드시 누군가 읽는다.** 파이프로 받았으면 스레드로 비우고, 아니면 `DEVNULL`/파일로 보낸다.
    쓰기가 막히면 어느 스레드가 막힐지 고를 수 없고, 그 스레드가 배타 잠금을 쥐고 있으면 서버가 통째로 멎는다.
    제품 쪽도 같은 규칙이다 — 요청 처리 경로에서 stderr 에 쓰지 않는다(콘솔 CLI 의 오류는 응답 본문으로).
10. **"정상이면 그렇다" 는 주석은 그렇다는 것을 본 뒤에 쓴다.** 멍키 하네스는 "대기열이 빠지는 중이면 정상" 이라고 적어 두고
    빠지는지 본 적이 없었다(본문을 버렸으므로). 폭주 뒤 회복은 **측정 항목**이지 가정이 아니다 — 회복까지 걸린 시간을 숫자로 남긴다.
9. **검증은 콘솔 인코딩과 무관하게 같은 결과를 내야 한다.** 자식 프로세스를 두는 목업은 표준 입출력을 UTF-8 로
   고정하고, 검증 스크립트는 stdout 을 UTF-8 로 재설정하며, 테스트는 환경에 따라 달라지는 경고의 **순서**에 기대지 않는다.
   "내 콘솔에서 OK" 는 포팅 환경의 OK 가 아니다.
