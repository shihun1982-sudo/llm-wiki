# REQUEST LEDGER — 서버로의 **모든 요청**을 한 곳에서 (동시 질의 · 한도 · 관측 · 포팅)

> **이 문서 하나로 충분하게 썼다.** 다른 환경(사내 서버·다른 PC)에 2026-09-23 변경을 옮기는 사람이나 LLM 은
> 이 문서만 읽고 ① 무엇이 문제였고 ② 무엇을 바꿨고 ③ 어떤 설정을 어디서 만지고 ④ 어떻게 확인하고
> ⑤ 잘못되면 어떻게 되돌리는지를 모두 알 수 있어야 한다.
> **이 일을 다른 LLM 에게 시킨다면 §13 의 프롬프트를 그대로 붙여 넣으면 된다.** 더 깊은 배경은 §14 의 링크로.
>
> 함께 읽을 것(필수는 아님): [CONCURRENCY.md](CONCURRENCY.md) · [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) ·
> [PORTING.md](PORTING.md)(LLM 연결 정보) · 설계 근거와 기각한 대안:
> [IMPLEMENTATION_PLAN_0923.md](history/2026-09-23/IMPLEMENTATION_PLAN_0923.md)

---

## 0. 이 문서가 다루는 변경 — 전부 **적용 완료**

| # | 변경 | 무엇이 좋아지나 | 실측 |
|---|---|---|---|
| 1 | **질의 중 DB 쓰기 잠금 제거** | 동시 질의가 서로를 최대 60초까지 막던 것이 사라진다 | 다른 연결의 쓰기 **3,310ms 실패 → 0.89ms 성공** |
| 2 | **중복 임베딩 제거** | 같은 질문을 한 요청에서 최대 3번 임베딩하던 것을 1번으로 | 질의당 **약 5초** 단축 |
| 3 | **임베딩 캐시 모델 이름 정규화 + 병합** | 표기 변경 시 전체 재임베딩 방지 | DB **460.9 → 368.1MB** (−92.8MB) |
| 4 | **대기열 상한 64 → 128, 대기 한도 종류별** | 즉시 거절이 줄고, 검색은 짧게 질의는 길게 | 질의 30분 / 검색 30초 |
| 5 | **종류별 한도**(질의·검색·MCP·CLI) | 빠른 검색이 느린 질의 뒤에 갇히지 않는다 | 질의 6 < 전체 8 → **슬롯 2개 상시 예약** |
| 6 | **요청 원장** — 모든 요청을 파일에 | 거절·시간초과·취소·중단이 **더 이상 사라지지 않는다** | 재현 실험에서 **30건 중 15건(50%)** 유실을 잡아냄 |
| 7 | **Observability 통합 화면** | 한 요청을 보려고 화면 넷을 오갈 필요가 없다 | 「📋 요청 (전체)」 탭 |
| 8 | **질의를 비동기 잡으로** | 질의를 연속으로 보내도 **화면이 멈추지 않는다** | POST **250초 → 26ms** |
| 9 | **질의 로그를 requests 로 통합** | 같은 내용을 두 표에 저장하던 중복 제거 | trace 바이트까지 동일했다 |
| 10 | **거절 사유 맥락** | "무엇이 막았나 · 설정 키 · 되돌리는 법" 이 그 자리에 | 한도 4종 + 접근 제어 4종 + 권한 |

---

## 1. 배경 — 무슨 문제였나

### 1.1 "읽기만 하는데 DB 가 잠긴다"

이 서버의 SQLite 는 **WAL** 모드라 읽기는 아무것도 막지 않는다(실측: 잠금 상태에서 다른 연결의 읽기 **0.02ms**).
문제는 **질의가 읽기만 하지 않는다**는 것이었다.

질의 벡터를 `embedding_cache` 에 넣는 `Store.cache_put()` 이 **`commit()` 을 하지 않았다.**
Python `sqlite3` 는 INSERT 앞에서 트랜잭션을 암묵적으로 열고, SQLite 는 첫 쓰기에서 **WRITER 잠금을 잡아
COMMIT 까지 놓지 않는다.** 그 INSERT 는 질의 **초반**(벡터 검색)에 일어나고 다음 커밋은 질의 **맨 끝**이었다 —

> **질의 1건이 자기 수명(실측 98~118초) 내내 SQLite 쓰기 잠금을 혼자 쥐었다.**

다른 동시 질의는 `db_busy_timeout_s`(60초)까지 기다린 뒤 `database is locked` 로 실패했고,
그 실패는 `except Exception: pass` 가 조용히 삼켜 **로그에도 남지 않았다.**
캐시가 **적중**하면 쓰기가 없으므로 같은 질문을 반복할 때는 멀쩡했고,
**서로 다른 질문을 여러 명이 동시에 던질 때** 터졌다.

같은 조건으로 재현한 실험:

```
python sqlite3 3.50.4
A: INSERT 후 in_transaction = True        ← 쓰기 잠금 보유
B(다른 연결) 쓰기 → 3,310ms 대기 후 'database is locked'
C(다른 연결) 읽기 → 0.02ms                 ← WAL 이라 읽기는 안 막힌다
A.rollback() 후 남은 행: []                ← 캐시 항목이 통째로 버려진다
```

### 1.2 "요청이 기록 없이 사라진다"

요청이 사라지는 경로가 **9가지** 확인됐다.

| # | 원인 | 어떤 요청이 사라지나 |
|---|---|---|
| A | `query_engine.run()` 이 **성공 경로에서만** 기록 | 시간 초과·취소·실패한 질의 |
| B | 잠금 실패를 삼키고 0 반환 | §1.1 의 잠금에 걸린 질의 |
| C | 거절(429/503/403/413)이 **메모리 카운터만** | 대기열 초과·속도 제한·점검 모드 |
| D | `embed_query` 의 조용한 실패 | 원인 추적이 끊긴다 |
| E | **스냅샷 복원이 `db.sqlite3` 를 통째로 교체** — `evolve apply` 가 회귀 시 **자동으로** 한다 | 복원·리빌드·평가가 도는 수 분 동안의 모든 요청 |
| F | `reset logs` 가 DB 행과 보관 파일을 **동시에** 삭제 | 전부 |
| G | 백그라운드 잡(sweep·trial·precompute·fusion)이 기록을 남기지 않음 | 그 잡들의 실행·실패·취소 |
| H | `UnicodeEncodeError` 도 함께 삼킴 | 짝 없는 서러게이트가 섞인 질의 |
| I | 서버 재시작 | 진행 중이던 요청 |

여기에 **수명**이 겹친다: 진행 정보 **5분** · 최근 목록 **500건**(메모리) · `requests` **2,000행**(실측 8일).

**재현 결과** (`verify_request_ledger.py`, 격리 환경 · mock LLM · 슬롯 4 · 대기열 8):

```
보낸 질의                  : 30
requests 테이블(예전 구조)  : 15   ← 절반이 기록 없이 사라진다
요청 원장                  : 30   (done 15 + rejected 15 · 누락 0 · 중복 0)
```

### 1.3 "질의와 검색이 같은 줄에 선다"

채널 검색은 100ms 면 끝나는데 90초짜리 질의가 슬롯을 다 차지하면 대기열에 섰다.
시간 제한만 종류별로 갈라져 있고 **슬롯·동시 수·대기열·분당 수는 전부 공용**이었다.

### 1.4 "요청을 연속으로 보내면 화면이 멈춘다"

`POST /api/query` 가 **동기**였다. 브라우저는 한 사이트에 연결을 6개까지만 열기 때문에,
질의 몇 건이 연결을 물고 있으면 **화면 갱신 폴링이 브라우저 안에서 출발조차 못 했다.**
서버는 멀쩡한데 화면만 멈춘 것처럼 보였다. **슬롯을 낮추는 것으로는 해결되지 않는다** — 대기 중에도 연결은 잡혀 있다.

---

## 2. 용량 먼저 — 슬롯 수는 **LLM 엔드포인트**가 정한다

설정을 만지기 전에 이것부터 이해해야 한다. **잘못 잡으면 다른 모든 조정이 의미가 없다.**

이 서버에는 **LLM 동시 호출을 제한하는 별도 설정이 없다.** `concurrency.max_parallel_reads` 가 곧 LLM 동시 호출 수다.

실측(로컬 Ollama · llama3.1):

| 항목 | 값 |
|---|---|
| 질의 1건 전체 | 98~118초 (혼잡 시 250~386초) |
| 그중 LLM 대기 | **75~97%** (`answer_llm` 만 72~75초) |
| 질의당 LLM 호출 | **10~14회** |

**포화의 증거**: 질의 6건이 도는 동안 Ollama 에 `"1+1?"`(8토큰)을 **직접** 보냈더니 20초 안에 응답이 없었다.
`OLLAMA_NUM_PARALLEL` 이 설정되지 않아 `llama-server` 가 한 번에 한 요청만 처리하기 때문이다.

| 환경 | `max_parallel_reads` |
|---|---|
| 로컬 Ollama 1대 (기본 설정) | **2~4** — 그 이상은 Ollama 안에서 줄을 설 뿐이고 슬롯·연결·메모리만 잡는다 |
| 사내 게이트웨이(동시성 여유) | 8~16 — 단 **포화점을 재고** 정한다 |
| mock/추출식(LLM 없음) | 16~32 |

**포화점 재는 법**: `python tools/verify/verify_request_ledger.py --capacity`
— 동시 실행 수를 1·2·4·8 로 올리며 **완료/분**을 잰다. 더 오르지 않는 지점이 그 환경의 상한이다.

**용량 계산**: `처리량(질의/분) ≈ 60 ÷ 질의소요초 × 슬롯수`.
예) 98초 · 슬롯 8 → **약 4.9 질의/분**. 30명이 5분에 한 번씩만 물어도 6/분이라 **이미 용량 초과**다.
해법은 셋 — ① LLM 동시성 확대(게이트웨이가 받아 줄 때만) ② **질의당 LLM 호출 축소**
(`query_expand`·`claim_check`·`rerank_llm` 토글, 또는 `llm_roles` 에 작은 모델) ③ 답변 캐시(`query_cache`·`precompute`).

> **대기열은 슬롯을 대신하지 못한다.** 슬롯 8 · 질의 100초면 대기열 128번째는 이론상 1,600초를 기다린다.
> 대기열 확대의 효과는 **"즉시 거절"이 "잠깐 기다렸다 거절"로 바뀌는 것**이지 처리량 증가가 아니다.

---

## 3. 변경 1·2·3 — DB 잠금과 중복 임베딩

새 설정 키는 **하나뿐**이다.

| 키 | 파일 | 기본 | 뜻 |
|---|---|---|---|
| `db_synchronous` | `config.json` | `NORMAL` | SQLite 커밋 내구성. WAL 에서 `NORMAL` 은 **DB 손상이 없고** 체크포인트에서만 fsync 한다. 정전 시 최근 몇 건의 커밋(색인·관측 기록)이 날아갈 수 있고 재빌드로 복구된다. 규정상 더 엄격해야 하면 `FULL` |

수정 내용:

| 항목 | 내용 | 파일 |
|---|---|---|
| 0-A | `cache_put(..., commit=True)` — 질의 경로에서 즉시 커밋 | `store.py` · `retrieval.py` |
| 0-B | `except Exception: pass` → **경고 로그**(첫 5회) | `retrieval.py` |
| 0-C | 커밋 없이 세션 이탈 시 **경고 + 카운터**(`pool_info().uncommitted_exits`) | `store.py` |
| 0-D | `doc_vector_search`·`doc_expand` 를 임베딩 캐시 경유로 | `precompute.py` · `query_engine.py` |
| 0-E | `db_synchronous=NORMAL` | `store.py` · `config.py` |
| 0-F | 캐시 키 모델 이름 정규화(`bge-m3` ≡ `bge-m3:latest`) + 병합 명령 | `store.py` · `pipeline.py` · `cli.py` |
| A | 질의 끝 쓰기(`requests`·`query_log`·연결)를 **한 트랜잭션**으로 | `store.write_bundle()` · `query_engine.py` |

**빌드 경로는 바뀌지 않았다**: `cache_put` 의 `commit` 기본값은 `False` 이고, 빌드는 예전처럼 배치 단위로 커밋한다.

회귀 방지: `python -m unittest tests.test_db_lock_0923` (7개)

### 3.1 마이그레이션 ① 임베딩 캐시 병합

같은 모델이 두 이름으로 저장돼 있으면 절반이 낭비다. **정규화만으로는 기존 행이 사라지지 않는다** —
조회는 두 이름을 함께 보므로 재임베딩은 일어나지 않고, 디스크를 회수하려면 아래를 **한 번** 돌린다.

```bat
python -m llmwiki maintenance cache_merge_dry --json    :: 무엇이 합쳐지는지만 (읽기 전용)
python -m llmwiki snapshot create --tag manual:before-cache-merge   :: 걱정되면 백업
python -m llmwiki maintenance cache_merge                :: 실행
python -m llmwiki maintenance vacuum                     :: 파일 크기 회수
```

겹치는 sha 는 버리고 **옛 이름에만 있는 sha 는 이름만 바꿔 보존**한다(그래야 재임베딩이 없다).
이 저장소 실측: 16,654행 병합 → DB **460.9 → 368.1MB**.

### 3.2 마이그레이션 ② `query_log` 의 중복 trace 회수

```bat
python -m llmwiki maintenance trim_query_log_dry --json  :: 몇 건·몇 MB 인지
python -m llmwiki maintenance trim_query_log             :: 실행
```

**`requests` 에 같은 trace 가 있는 행만** 지운다. 짝이 없는 행(연결 컬럼이 생기기 전의 옛 행)은
그 trace 가 **유일한 사본**이므로 건드리지 않는다. 이 저장소 실측: 824행 중 82행(3.7MB) 회수, 742행 보존.

> **서버가 한가할 때 돌린다** — 쓰기 트랜잭션이라 도는 동안 다른 쓰기가 기다린다.

---

## 4. 변경 4·5 — 대기열과 종류별 한도

### 4.1 개념

전체 읽기 슬롯(`max_parallel_reads`) 안에서 **종류마다 상한**을 둔다.
**질의 상한을 전체보다 작게** 잡으면 그 차이가 빠른 요청(검색)의 **예약 슬롯**이 된다.

```
전체 슬롯 8
├ 질의(query)  최대 6  ← 질의가 아무리 몰려도 6을 넘지 못한다
└ 검색(search) 최대 4     → 슬롯 2개는 항상 비어 있어 검색이 즉시 실행된다
```

별도 "예약 슬롯" 설정을 만들지 않고 상한 하나로 같은 효과를 낸다 — 설정이 하나 줄고 설명이 쉽다.

### 4.2 설정 (`server.json`)

```jsonc
"concurrency": {
  "max_parallel_reads": 8,
  "max_parallel_per_user": 3,      // 사용자당 동시 수는 **여기 한 곳**에서 정한다
  "queue_max": 128,                // 64 → 128 (질의가 100초 가까이 걸리는 환경에서 64는 금방 찬다)
  "queue_timeout_s": 1800,         // 30분
  "classes": {
    "query":  {"max_parallel": 6, "queue_max": 96, "queue_timeout_s": 1800},
    "search": {"max_parallel": 4, "max_parallel_per_user": 3, "queue_max": 32, "queue_timeout_s": 30},
    "mcp":    {"max_parallel": 4, "max_parallel_per_user": 2, "queue_max": 32, "queue_timeout_s": 300},
    "cli":    {"max_parallel": 2, "max_parallel_per_user": 1, "queue_max": 8,  "queue_timeout_s": 300}
  }
},
"rate_limit": {
  "classes": { "query": {"per_user_per_min": 20}, "search": {"per_user_per_min": 60} }
}
```

**비운 키는 공용 값을 따른다.** `max_parallel_per_user` 는 전역에 두고 **달라야 할 때만** 종류에 적는다 —
같은 성격의 값이 두 곳에 있으면 어느 쪽이 먹는지 알 수 없기 때문이다.
**시간 제한(질의 수명)은 `timeouts.<kind>_s` 가 따로 정한다** — `classes` 에 넣지 않는다.

`queue_timeout_s` 를 종류마다 다르게 두는 이유: 질의는 30분을 기다릴 만하지만
**검색은 30초 안에 못 들어가면 기다릴 이유가 없다**(거절이 낫다).

### 4.3 규모별 권장값

| 상황 | reads | query.max_parallel | search.max_parallel | query/분 |
|---|---|---|---|---|
| 로컬 Ollama 1대 | 3 | 2 | 2 | 10 |
| 사내 게이트웨이 (30명) | 8 | 6 | 4 | 20 |
| 사내 게이트웨이 (100명) | 16 | 12 | 6 | 30 |
| mock/추출식 | 24 | 20 | 8 | 60 |

`query.max_parallel` 은 항상 `max_parallel_reads` 보다 **작게** 잡는다(그 차이가 예약분이다).

### 4.4 admin 은 한도를 받지 않는다

`rate_limit.exempt_roles`(기본 `["admin"]`)에 든 역할은 **동시 수·분당 수 검사를 건너뛴다.**
그리고 **게스트는 IP 로 묶인다** — 로그인하지 않으면 브라우저 탭 여러 개가 한 바구니를 쓴다.
테스트할 때 한도가 답답하면 **로그인**하는 것이 정답이다.

---

## 5. 변경 6 — 요청 원장

### 5.1 무엇인가

서버로 들어온 **모든 요청**을 접수 시점과 종료 시점에 한 줄씩 **파일**에 append 한다.

```
data/ledger/req-2026-09-23.jsonl
{"ev":"open","ts":…,"token":"r-ab12…","kind":"query","origin":"web","user":"kh82.kim","role":"class2",
 "ip":"10.1.2.3","label":"ISSUE-2001 …","method":"POST","path":"/api/query"}
{"ev":"close","ts":…,"token":"r-ab12…","status":"rejected","http":503,"code":"queue_timeout",
 "queue_wait_s":120.0,"ms":120004,"stage":"(대기)","request_id":null,"run_id":null,
 "limit_json":"{\"what\":\"대기열에서 기다릴 수 있는 시간\",\"key\":\"concurrency.classes.query.queue_timeout_s\",…}"}
```

| 정한 것 | 이유 |
|---|---|
| **JSONL**, SQLite 아님 | 원장이 기록해야 할 대표 사건이 **"DB 가 잠겨 기록하지 못했다"** 이다. 같은 DB 에 동기로 쓰면 같은 이유로 또 사라진다. append 는 잠금이 없고 **스냅샷 복원(DB 파일 교체)에도 되감기지 않는다** |
| **전용 writer 스레드 + 큐** | 요청 스레드가 디스크를 기다리지 않는다 |
| **`open` 을 먼저** 쓴다 | 프로세스가 죽어도 "들어왔다" 는 남는다. `close` 가 없는 항목은 **`unknown`(중단)** 으로 보인다 |
| **큐 포화 시 드롭 카운터** | 못 쓴 사실 자체를 기록한다 (조용한 유실 금지) |
| `data/ledger/` (logs/ 아님) | `logs/` 는 총량 제한(`log_total_max_mb`)의 정리 대상이다 |
| **명시적으로 켠 경우에만 기록** | 그러지 않으면 Pipeline 만 만드는 테스트·도구가 프로젝트 원장을 오염시킨다(실측: 실행당 1,000건 이상 유입) |

**상태 8종**: `queued` · `running` · `done` · `error` · `cancelled` · `timeout` · `rejected` · `unknown`

**시작과 끝이 한 쌍으로 남는다 — 예외 넷**(운영 중에 꼭 알아야 하는 경계다):

| 경우 | 시작 | 끝 | 설명 |
|---|:--:|:--:|---|
| 가벼운 GET 폴링이 **성공** | ✗ | ✗ | 화면 갱신 폴링이 1.5초마다 오는데 다 남기면 원장이 폴링으로 덮인다. `include_get` 이 정한다 |
| 그 폴링이 **실패** | ✓ | ✓ | 400 이상이거나 `done` 이 아니면 **소급해서 `open` 까지 쓴다**. "버려진 요청이 기록에 없다" 가 출발점이므로 실패는 절대 빠지지 않는다 |
| 프로세스 **강제 종료** | ✓ | ✗ | `close` 를 쓸 주체가 죽었으니 불가능하다. 끝이 없는 항목은 `running_grace_s` 뒤 **`unknown`(중단)** 으로 보인다 — 사라지는 것이 아니라 중단으로 드러난다 |
| writer 큐 **포화** | ✗ | ✗ | `queue_max` 를 넘으면 버리되 **버린 수를 센다**(`dropped`). 서버 모니터에서 0인지 본다 |

정상 종료(Ctrl+C)에서는 서버가 writer 를 **마저 비우고** 끝낸다(`web/server.py` 의 `serve()` 종료 처리).
이것이 없으면 daemon 스레드가 들고 있던 줄이 통째로 사라진다 — 포팅할 때 이 호출을 빠뜨리지 않는다.

### 5.2 어디서 기록하나 — 5곳

티켓 안에만 넣으면 티켓 **이전**에 죽는 요청(차단·점검 모드·413·인증 실패·잘못된 JSON)이 빠진다.

| # | 자리 | 보장하는 것 |
|---|---|---|
| 1 | `web/server.py` 의 `do_GET`·`do_POST`·`do_DELETE`·`_mcp` | HTTP 로 들어온 **모든 것이 정확히 1건** |
| 2 | `reqmgr.RequestManager.ticket()` | 대기열·락·`request_id`·`run_id`, **거절도 반드시 close** |
| 3 | `progress.cli_monitor()` | CLI·MCP stdio (다른 프로세스에서 같은 폴더에 append). 질의가 끝난 뒤 `progress.set_result()` 로 **`request_id`·`run_id` 를 붙인다** — 없으면 원장에서 📄 프로파일·📜 로그로 갈 수 없다 |
| 4 | `web/server.py` `_start_job.runner()` | 백그라운드 잡(sweep·trial·precompute·fusion) |
| 5 | `evolve._restore` / `reset.run` | **스냅샷 복원·로그 초기화 사건** (§5.4) |

**키는 서버가 만든다**(`r-<uuid12>`). 클라이언트가 보낸 `progress_token` 은 중복될 수 있으므로 `client_token` 에 참고로만 담는다.

### 5.3 설정 (`server.json` 의 `ledger` 절)

| 키 | 기본 | 뜻 |
|---|---|---|
| `ledger.enabled` | `true` | 끄면 아무것도 쓰지 않는다 |
| `ledger.dir` | `data/ledger` | 원장 폴더 (`data/…` 상대 경로는 `data_dir` 아래) |
| `ledger.keep_days` | `30` | 보존 기간(일). 0 = 지우지 않음 |
| `ledger.max_mb` | `512` | 폴더 총량 상한. 넘으면 오래된 파일부터 삭제 |
| `ledger.include_get` | `heavy` | 어떤 GET 을 남길지: `none` · `heavy`(+무거운 조회) · `all`(폴링 포함). **실패는 정책과 무관하게 항상 남는다** |
| `ledger.queue_max` | `10000` | writer 큐 상한. 넘으면 드롭하고 **드롭 수를 센다** |
| `ledger.flush_ms` | `200` | writer 가 모아서 쓰는 간격 |
| `ledger.live_rows` | `500` | 목록을 빠르게 그리기 위한 메모리 색인 크기 |
| `ledger.running_grace_s` | `900` | close 없는 항목을 '중단' 으로 볼 때까지의 시간 |
| `ledger.refresh_ms` | `5000` | **화면 갱신 주기**. 0 = 끔. 목록·상태 줄·시간 막대가 한 번의 요청으로 함께 갱신된다 |
| `ledger.refresh_choices_ms` | `[2000,5000,10000,30000,0]` | 화면 드롭다운에 보일 값 |
| `ledger.histogram_buckets` | `60` | 시간 막대를 몇 칸으로 나눌지 |

**환경변수 `LLMWIKI_LEDGER_DIR_PATH`** 는 위 `ledger.dir` 보다 **세다**. 두 가지로 쓴다 —
① 운영: `server.json` 을 고치지 않고 원장만 다른 디스크로 보낸다.
② 격리: 테스트·검증 하네스가 실사용 원장을 오염시키지 않게 한 줄로 막는다.
이 저장소의 `tests/__init__.py` 가 바로 이 목적으로 임시 폴더를 지정한다(§11 의 '테스트가 원장을 오염시킨다' 참고).

**디스크 예상**: 요청 1건 ≈ 2줄 × 약 300B. 하루 3,000건 ≈ **2MB/일**, 30일 ≈ 60MB.
`include_get=all` 은 폴링 때문에 10배 이상 늘 수 있다.

### 5.4 원장만으로 덮이지 않는 두 가지

| 경우 | 조치 |
|---|---|
| **스냅샷 복원** (`snapshot restore`, `evolve apply` 의 자동 롤백) | 복원 **전후**로 원장에 사건을 남긴다(누가·언제·어느 스냅샷·그때의 최대 `requests` id). 원장 파일 자체는 되감기지 않는다 |
| **`reset logs`** | 삭제 대상에서 원장을 **제외**했다. 지우려면 `--include-ledger` 를 명시해야 하고, 초기화 사실도 원장에 남는다 |

---

## 6. 변경 7 — Observability 통합 화면

### 6.1 탭 구성

| 탭 | 역할 |
|---|---|
| **📋 요청 (전체)** *(신설·첫 탭)* | 서버로의 **모든 요청** — 목록·필터·상세 |
| 요청 프로파일 | 한 건의 **단계 워터폴**과 두 요청 비교 |
| 진행 중 작업 | **지금** 도는 것 (실시간 보드) |
| 서버 모니터 | 집계·제한 편집 |
| **로그 파일** *(이름 변경)* | 로그 **원본**. 요청별 로그는 요청(전체)에서 본다 |
| 시스템 · 규모 / 질의 로그 / 콘솔 | 그대로 |

기존 탭을 지우지 않았다 — 두 요청 비교, ⟲ 단계 재실행, 사용자별 집계처럼 **요청 하나의 수명과 성격이 다른 것**은
그 화면에 남기는 편이 낫다. 무엇이 어디로 갔는지는 `verify_ledger_merge.py` 가 표로 검사한다(§9).

### 6.2 「요청 (전체)」 구성

1. **상태 스트립** — 실행/대기·슬롯·대기열·쓰기락·writer 큐. 숫자를 누르면 그 상태로 필터.
2. **시간 밀도 막대** — 한 칸이 한 시간 구간이고 **상태별로 색을 쌓는다**(완료·진행/대기·느림·취소·시간초과·거절·오류·중단 8색 + 범례). 한 구간에 여러 상태가 섞이는 것이 정상이므로 누적이어야 한다. 막대를 누르면 그 구간만 본다.
3. **뷰 프리셋** — `전체` · `진행 중` · **`문제만`** · `질의` · `느린 것` · `내 요청`.
4. **필터** — 기간·상태·종류·창구·사용자·최소 소요·검색어 · **`로그에서`**(로그 본문으로 요청 찾기).
5. **목록** — 상태 배지 · 시각 · 종류/창구 · 사용자 · 라벨 · 대기 · 소요(실행 중이면 **경과**) · 단계(실행 중이면 **실시간**) · 결과 · 링크(📄 프로파일 · 📜 로그).
6. **상세** — 줄을 누르면 **바로 아래**에 펼쳐진다.

| 상세 섹션 | 내용 | 원천 |
|---|---|---|
| 개요 | 상태·사용자·창구·IP·경로·시각·대기·소요·락·시간 제한 | 원장 |
| **왜 거절됐나** | 무엇이 막았나 · 대상 · 그때 값 · **설정 키** · 되돌리는 명령 | 원장 `limit_json` |
| 사건 타임라인 | open → update → close 전부 | 원장 |
| 답변·근거 | 답변·인용·근거 수·판정·groundedness·👍👎 | `requests.result` + `query_feedback` |
| 단계 프로파일 | LLM 호출·토큰·SQL·run_id + **워터폴 펼치기** | `requests.trace` |
| 로그 | 그 요청의 `run_id` 로 거른 줄 (경고 이상이면 자동 펼침) | `logs/*.log` |
| **같은 시각의 요청** | 함께 돌던 요청들 (기본 **접힘**) | 원장 |

**로그 줄의 98%가 요청에 속한다**(실측 57,331줄 중 56,278). 그래서 요청↔로그를 양방향으로 이었다.

### 6.3 세 창구 정합

| 기능 | CLI | Web | MCP |
|---|---|---|---|
| 원장 목록·상세 | `ledger list [--problems] \| show <token> \| stats \| prune` (**서버가 꺼져 있어도** 파일을 직접 읽는다) · `server ledger`(실행 중 서버) | `GET /api/ledger` · `/api/ledger/<token>` · `/api/ledger/export` | `wiki_requests(source="ledger", status=…, token=…)` |
| 종류별 한도 | `server limits set concurrency.classes.search.max_parallel=6` | Observability › 서버 모니터 | 없음(설정 변경은 MCP 에 두지 않는다) |

MCP 는 **도구를 늘리지 않고** 기존 `wiki_requests` 에 `source` 를 더했다 — 붙은 LLM 의 질문
("전에 물어봤나" / "왜 실패했나")이 한 도구에서 답이 되게.

### 6.4 권한

기본 **내 것만**. 전체 조회는 기존 작업 권한 **`requests all`**(기본 `run` 등급 = class3)을 그대로 쓴다.
IP·User-Agent 는 `monitor.show_user_to_viewer` 를 따르고, **내보내기와 「같은 시각의 요청」은 전체 조회 권한이 필요**하다.

---

## 7. 변경 8 — 질의를 비동기 잡으로

`POST /api/query` 에 **`"async": true`** 를 주면 서버가 **즉시 잡 토큰만** 돌려주고,
클라이언트는 `GET /api/jobs/<id>` 로 폴링한다. Web UI 는 이 경로를 쓴다.

```
POST /api/query (async) 반환까지: 26 ms      ← 예전에는 질의가 끝날 때까지(250초)
질의 진행 중 /api/activity        : 2 ms      ← 예전에는 브라우저 연결 포화로 출발조차 못 함
질의 진행 중 /api/progress/<tok>  : 1 ms
```

**동기 경로는 그대로 둔다** — `async` 를 주지 않으면 예전과 똑같다(CLI·MCP·기존 클라이언트 호환).

---

## 8. 변경 9 — 질의 로그를 `requests` 로 통합

두 표가 질문·답변·근거·판정·`trace` 를 **내용까지 똑같이** 들고 있었다(trace 는 바이트까지 동일:
43,072 / 43,072). `requests` 가 이미 `result` 를 통째로 담으므로 `query_log` 의 고유한 값은 **피드백뿐**이었다.

| 지금 | 역할 | 보존 |
|---|---|---|
| `requests` | **질의 이력의 유일한 원천** (`kind='query'`) | `keep_requests` 2,000행 (실측 8일) |
| `query_feedback` *(신설)* | 👍👎 와 정정 메모만 | **영구** (피드백이 달린 것만 들어가 행이 매우 적다) |
| `query_log` | **쓰기 중단**. 옛 행은 읽기만(하위 호환) | 그대로 |
| `data/requests/*.json` | 그때의 답변·trace | `requests_keep_days` 90일 |
| `data/ledger/*.jsonl` | 모든 요청의 수명 | `ledger.keep_days` 30일 |

`store.queries()`·`get_query()`·`query_users()` 는 **모양을 그대로 두고 원천만 바꿨다**(부르는 곳이 14군데다).
전환기에는 `requests` 에 짝이 없는 옛 `query_log` 행도 함께 보여 준다.

**`query_id` 는 이제 `request_id` 와 같은 값이다.** 다만 완성 답변이 아닌 출력 모드
(`fused`·`reranked`·`context`)나 기록을 끈 실행에서는 `null` 이다 — 피드백을 달 수 없다는 신호다.

---

## 9. 옮기는 절차

### 9.1 옮길 것

| 무엇 | 비고 |
|---|---|
| `llmwiki/` 코드 전체 | 표준 라이브러리만 쓴다(추가 의존성 없음) |
| `server.json` | §4.2 · §5.3 의 절을 추가하거나 `setup/server.example.json` 을 복사 |
| `config.json` | `db_synchronous: "NORMAL"` 추가 |
| `docs/` | 이 문서 포함 |
| **`data/ledger/`** | **옮기지 않는다** — 그 환경의 기록이다 |
| `data/llmwiki.sqlite3` | 색인을 함께 옮길 때만. 옮기면 §3.1·§3.2 마이그레이션 실행 |

### 9.2 첫 기동 순서

```bat
:: 1) 설정 확인 — 모르는 키는 서버가 400 으로 거부하므로 먼저 로드해 본다
python -c "from llmwiki import reqmgr; c=reqmgr.load_config(); print(c['concurrency']['queue_max'], c['ledger']['enabled'])"

:: 2) 이 환경의 LLM 포화점을 잰다 (§2) — 슬롯 수를 여기서 정한다
python tools\verify\verify_request_ledger.py --capacity

:: 3) 슬롯·한도 적용
python -m llmwiki server limits set concurrency.max_parallel_reads=<측정값>
python -m llmwiki server limits set concurrency.classes.query.max_parallel=<그보다 작게>

:: 4) 마이그레이션 (색인을 옮겨 왔을 때만)
python -m llmwiki maintenance cache_merge_dry --json
python -m llmwiki maintenance cache_merge
python -m llmwiki maintenance trim_query_log_dry --json
python -m llmwiki maintenance trim_query_log
python -m llmwiki maintenance vacuum

:: 5) 기동 후 확인
python -m llmwiki ledger stats --days 1
```

### 9.3 설정을 바꾸는 세 가지 경로 (모두 같은 결과)

```bat
:: ① 파일 — 서버 재시작 또는 아래 ②로 반영
notepad server.json

:: ② CLI (실행 중인 서버에 즉시 반영 + 파일 저장; admin 권한 필요)
python -m llmwiki server limits set concurrency.queue_max=128
python -m llmwiki server limits set ledger.refresh_ms=10000

:: ③ Web UI — Observability › 서버 모니터 (admin)
```

모든 키는 환경변수로도 덮어쓴다: `LLMWIKI_SERVER_<섹션>_<키>` (예 `LLMWIKI_SERVER_CONCURRENCY_QUEUE_MAX=128`).

---

## 10. 검증

```bat
:: 이번 변경 전용 — 사라지는 요청을 **재현**하고 원장이 잡는지 확인 (격리 환경 · mock LLM · 실제 서버를 건드리지 않는다)
python tools\verify\verify_request_ledger.py                :: 시나리오 A~C
python tools\verify\verify_request_ledger.py --capacity     :: LLM 포화점

:: 30명이 **Web·CLI·MCP 로 동시에** 쓸 때 (사내 환경에 올리기 전 반드시 한 번)
python tools\verify\verify_three_surface_load.py            :: 기본 30명 (Web 18 · MCP 6 · CLI 6)
python tools\verify\verify_three_surface_load.py --users 50 :: 더 세게

:: 원장이 기존 세 화면의 정보를 하나도 잃지 않았는지 항목별 대조
python tools\verify\verify_ledger_merge.py
python tools\verify\verify_ledger_merge.py --live

:: 질의 경로가 쓰기 잠금을 쥐지 않는가 (7개)
python -m unittest tests.test_db_lock_0923

:: 기존 회귀
python -m unittest discover -s tests -p "test_*.py"
python tools\verify\verify_surface_align.py
python tools\verify\verify_docs.py
```

**합격 기준**: 원장 누락 0 · 중복 0 · 드롭 0 · `database is locked` 0 ·
상태 미상(`unknown`)은 강제 종료 시나리오에서만.

**실측 기준선** (이 저장소, 2026-09-24):

| 검사 | 결과 |
|---|---|
| 재현 A — 보낸 24건 중 예전 구조에서 사라졌을 것 | **12건 (50%)** · 원장 누락 0 · 중복 0 · 종료 기록 24/24 |
| 재현 B — 점검 모드 거절 | HTTP 503 · 사유·설정 키·해제 방법까지 기록 |
| 재현 C — 강제 종료 | 진행 중이던 **6건이 '중단' 으로** 남음 |
| 잠금 — 질의 진행 중 다른 연결의 쓰기 | 3,310ms 실패 → **0.89ms 성공** |
| **30명 동시 (Web 18 · MCP 6 · CLI 6)** | 436회 · **5xx 0건 · 거절 0건** · 보낸 수 ↔ 원장 **창구마다 일치**(CLI 18↔18 · Web 질의 54↔54) · 깨진 줄 0 · `database is locked` 0 |
| 그때의 응답 시간 | 질의 p95 **4.1초**(끝까지) · 접수 p95 **0.5초** · 검색 p95 **2.2초** · **화면 폴링 p95 27ms** |
| 단위 테스트 | **785개 통과** (원장 격리 3개 + 프로세스 간 쓰기 1개 포함) |
| 세 창구 정합 | CLI 49 · Web 114 · MCP 20 **전수 일치** |
| 문서 검사 | 어긋남 0 · 고아 문서 0 |
| 테스트가 실사용 원장에 쓴 줄 | **0줄** (수정 전에는 한 번에 700~800줄) |

---

## 11. 문제 해결

| 증상 | 원인 | 조치 |
|---|---|---|
| 질의가 서로를 막고 `database is locked` | §1.1 의 미커밋 트랜잭션 | §3 이 적용됐는지 확인. 서버 모니터의 `db_pool.uncommitted_exits` 가 **0 이 아니면** 그런 코드가 또 생긴 것 |
| **화면이 멈췄다가 한꺼번에 갱신된다** | 동기 질의가 브라우저 연결 6개를 물고 있다 | §7(비동기 질의)이 적용됐는지. UI 가 `async: true` 를 보내는지 |
| 질의가 250초씩 걸린다 | **LLM 포화** (서버 문제 아님) | `/api/activity` 로 단계를 본다. 전부 `answer_llm`·`claim 검증` 이면 LLM 이 병목이다 → §2 |
| `503 queue_timeout` | 대기가 한도를 넘었다 | 종류별 `queue_timeout_s`(§4.2). **슬롯부터 본다** — 대기열만 늘리면 거절이 지연될 뿐 |
| `429 per_user_limit_kind` | 사용자(게스트는 IP)당 동시 수 | **로그인**하면 admin 은 면제(§4.4). 아니면 `concurrency.max_parallel_per_user` |
| 검색이 질의 뒤에 갇힌다 | 종류별 한도 미설정 | `classes.query.max_parallel` 을 전체보다 작게(§4.1) |
| 원장이 비어 있다 | `ledger.enabled=false` 또는 `include_get=none` 이고 GET 만 보냄 | `ledger stats` 로 확인 |
| 원장 드롭 수가 0 이 아니다 | writer 큐 포화 | `ledger.queue_max` ↑ · `include_get` 을 `heavy` 로 · 디스크 확인 |
| **원장에 읽을 수 없는 줄이 있다** (`stats().writer.corrupt` 가 0이 아니다) | 여러 프로세스(서버 + CLI 여러 개 + MCP stdio)의 쓰기가 한 줄 안에서 섞였다. **Windows 의 `O_APPEND` 는 프로세스 간 원자적이지 않다** | 파일 잠금으로 고쳤다(`reqledger._write_batch`). 여전히 0이 아니면 원장 폴더가 네트워크 드라이브인지 확인한다 — 잠금이 동작하지 않는 공유에서는 `ledger.dir` 을 로컬 디스크로 옮긴다. 회귀 시험: `python -m unittest tests.test_ledger_multiprocess` |
| **특정 시각 이후 요청 기록이 통째로 없다** | 스냅샷 복원이 DB 를 되감았다 | 원장의 '복원' 사건을 본다(§5.4). 원장 자체는 남아 있다 |
| 요청 기록이 전부 사라졌다 | `reset logs` | 원장은 삭제 대상이 아니다(§5.4). `ledger list` 로 확인 |
| **테스트가 원장을 오염시킨다** | `get_manager()` 가 **프로세스당 하나**를 만들어 두고 재사용하므로, `data_dir` 없이 먼저 만든 테스트 하나가 그 프로세스의 나머지 전부를 프로젝트 원장으로 보낸다 | 묶음 전체에 `LLMWIKI_LEDGER_DIR_PATH` 를 지정한다(이 저장소는 `tests/__init__.py`). 개별 테스트는 `ledger.dir` 을 임시 폴더로. 회귀 시험: `python -m unittest tests.test_ledger_isolation` |
| 완료된 질의인데 📄 프로파일 링크가 없다 | 그 경로가 `request_id` 를 원장에 싣지 않았다 | 웹 비동기 잡은 `_start_job` 이, CLI 는 `progress.set_result()` 가 싣는다. 2026-09-24 이전에 남은 줄은 비어 있을 수 있다(그때의 코드가 싣지 않았다) |
| 서버를 Ctrl+C 로 껐더니 마지막 몇 건이 없다 | 종료 때 writer 를 비우지 않았다 | `serve()` 종료 처리에서 원장 writer 를 멈춘다(§5.1 끝). 강제 종료(kill)는 구조상 최대 `flush_ms` 만큼 잃을 수 있다 |
| **`unittest` 가 3건 실패하거나 `verify_surface_align.py` 가 `UnicodeEncodeError` 로 죽는다** (실패 메시지에 `�`·깨진 한글) | 콘솔이 cp949 등 UTF-8 이 아닌 환경. 2026-09-24 이전 코드는 목업 자식 프로세스가 로케일로 쓰고 부모가 UTF-8 로 읽어 한글이 깨졌다 | 2026-09-24 이후 코드는 콘솔과 무관하게 통과한다(목업 UTF-8 고정 · 검증 스크립트 UTF-8 출력). 예전 코드라면 `set PYTHONUTF8=1` 후 재실행. 건강 점검의 `console_encoding` 경고는 정상 동작이다 — 운영 콘솔은 `chcp 65001` 또는 config.json `console_encoding=utf-8` |

---

## 12. 되돌리기 (롤백)

각 변경은 **독립적으로** 끌 수 있다. 코드를 되돌릴 필요는 거의 없다.

| 변경 | 되돌리는 법 |
|---|---|
| 대기열 128 | `server limits set concurrency.queue_max=64` |
| 대기 한도 30분 | `server limits set concurrency.queue_timeout_s=120` |
| `db_synchronous` | `config.json` 에서 `"FULL"` 로 |
| 종류별 한도 | `server.json` 의 `concurrency.classes` / `rate_limit.classes` 절을 **통째로 삭제** |
| 요청 원장 | `server limits set ledger.enabled=false` |
| 비동기 질의 | 클라이언트가 `async` 를 보내지 않으면 동기 경로로 돈다 |
| 통합 화면 | 기존 탭이 그대로 있으므로 기능 손실 없음 |
| 임베딩 캐시 병합 | 되돌릴 필요 없음(내용 주소 캐시라 없으면 다시 만든다). 걱정되면 §3.1 의 스냅샷으로 복원 |
| `query_log` 통합 | 옛 행과 읽기 경로가 남아 있어 쓰기만 멈춘 상태다. 되돌리려면 `query_engine.py` 의 `write_bundle` 절에 `log_query` 를 되살린다 |

---

## 13. 이 일을 LLM 에게 시킬 때 — 그대로 붙여 넣는 프롬프트

포팅을 사람이 직접 하지 않고 다른 LLM(사내 코딩 에이전트·Claude·Copilot 등)에게 시킬 때 쓴다.
**3단계로 나누어** 준다. 한 번에 다 시키면 무엇이 왜 바뀌었는지 확인할 수 없고, 실패해도 어디서 틀렸는지 모른다.

### 13.1 1단계 — 파악만 (아무것도 고치지 않게)

```text
당신은 사내 서버에 LLM Wiki 를 옮겨 세우는 일을 맡았습니다. 이번 단계에서는 **아무 파일도 고치지 마세요.**

읽을 것: docs/REQUEST_LEDGER.md 한 편이면 충분합니다. 다른 문서는 필요할 때만 보세요.

하세요:
1. §2 를 읽고, 이 환경의 LLM 엔드포인트(사내 게이트웨이 / 로컬 Ollama / opencode headless 중 무엇인지)를
   config.json 에서 확인해 알려 주세요.
2. 아래를 실행해 지금 상태를 보고하세요. 실행만 하고 해석은 그대로 붙여 주세요.
     python -c "from llmwiki import reqmgr; c=reqmgr.load_config(); print(c['concurrency']); print(c.get('ledger'))"
     python -m llmwiki ledger stats --days 1
     python tools/verify/verify_request_ledger.py --capacity
3. 3번 명령의 결과에서 **완료/분이 더 오르지 않는 동시 실행 수**를 찾아, 이 환경에 맞는
   concurrency.max_parallel_reads 와 concurrency.classes.query.max_parallel 값을 제안하세요.
   (질의 상한은 전체보다 반드시 작아야 합니다. 그 차이가 검색용 예약 슬롯입니다.)

지켜야 할 것:
- 표준 라이브러리만 씁니다. 새 패키지를 설치하거나 requirements 를 추가하지 마세요.
- 설정은 전부 파일(config.json / server.json / security.json)에 있습니다. 코드를 고쳐 값을 바꾸지 마세요.
- 서버가 모르는 설정 키를 받으면 400 으로 거부합니다. 문서에 없는 키를 추측해서 넣지 마세요.

보고: ① 이 환경의 LLM 연결 방식 ② 측정한 포화점과 근거 숫자 ③ 제안하는 설정값과 이유.
```

### 13.2 2단계 — 적용

```text
1단계에서 합의한 값을 적용합니다. **아래 3가지만** 바꾸고 그 외에는 손대지 마세요.

1. config.json 에 db_synchronous 를 추가합니다 (없으면 "NORMAL", 규정상 더 엄격해야 하면 "FULL").
2. server.json 에 docs/REQUEST_LEDGER.md §4.2 의 concurrency.classes / rate_limit.classes 절과
   §5.3 의 ledger 절을 넣습니다. **모든 키를 기본값까지 명시적으로 적습니다** — 나중 운영자가
   "키를 새로 발견해서 추가" 하는 대신 "있는 줄을 고치는" 것으로 끝나야 합니다.
3. 색인을 함께 옮겨 왔다면 §3.1·§3.2 의 마이그레이션을 dry-run → 실행 순서로 돌립니다.
   dry-run 결과(합쳐질 행 수·회수될 MB)를 먼저 보고하고 승인받은 뒤 실행하세요.

금지: llmwiki/ 아래 .py 파일 수정. 이번 작업은 설정과 데이터 이관만입니다.
      코드를 고쳐야만 된다고 판단되면 고치지 말고 그 이유를 보고하세요 — 그건 포팅 문제가 아니라 버그입니다.

보고: 바꾼 파일과 줄, dry-run 결과, 적용 후 값.
```

### 13.3 3단계 — 검증 (여기까지 해야 끝난 것입니다)

```text
아래를 순서대로 돌리고 **각각의 결과를 그대로** 보고하세요. 하나라도 RESULT OK 가 아니면 멈추고 알리세요.

  python -m unittest discover -s tests -p "test_*.py"     기대: OK (실패 0)
  python tools/verify/verify_request_ledger.py            기대: RESULT OK
                                                          A: 원장 누락 0 · 종료 기록 = 보낸 수
                                                          B: HTTP 503 이고 거절에 사유·설정 키가 붙는다
                                                          C: 강제 종료 시 '종료가 없는 항목' 이 1건 이상
  python tools/verify/verify_three_surface_load.py        기대: RESULT OK  (30명이 Web·CLI·MCP 로 동시에)
                                                          5xx 0 · 보낸 수 ↔ 원장이 창구마다 일치
                                                          깨진 원장 줄 0 · database is locked 0
                                                          화면 폴링 p95 가 5초 미만
  python tools/verify/verify_ledger_merge.py              기대: RESULT OK
  python tools/verify/verify_surface_align.py             기대: RESULT OK (CLI·Web·MCP 전수 일치)
  python tools/verify/verify_docs.py                      기대: RESULT OK
  python -m llmwiki models test --live                    기대: 이 환경의 LLM·임베딩이 실제로 응답

그 다음 사람 눈으로 한 번 확인합니다:
  1. 서버를 띄우고 Observability › 📋 요청 (전체) 을 엽니다.
  2. 질의를 한 건 보내고, 목록에 뜬 줄을 눌러 상세가 **바로 아래** 펼쳐지는지 봅니다.
  3. 상세에 📄 요청 프로파일 · 📜 로그 링크가 있고 실제로 열리는지 봅니다.
  4. 한도를 일부러 낮춰(server limits set concurrency.classes.query.max_parallel=1) 거절을 만들고,
     그 줄의 상세에 '왜 거절됐나'(무엇이 막았나·설정 키·되돌리는 명령)가 나오는지 봅니다. 확인 후 되돌립니다.

보고: 각 명령의 마지막 요약 줄, 위 4가지 눈 확인의 결과, 그리고 **기대와 달랐던 것 전부**.
기대와 다른데 원인을 모르겠으면 추측으로 고치지 말고 그 상태 그대로 보고하세요.
```

### 13.4 LLM 이 자주 틀리는 곳 (프롬프트에 함께 붙이면 좋다)

```text
주의:
- 대기열(queue_max)을 늘리는 것은 처리량을 늘리지 않습니다. 느려지면 슬롯과 LLM 동시성을 먼저 보세요.
- concurrency.classes.query.max_parallel 을 max_parallel_reads 와 같게 두면 예약 슬롯이 사라져
  빠른 검색이 느린 질의 뒤에 갇힙니다. 반드시 더 작게 두세요.
- 점검 모드를 켤 때 maintenance_allow_roles 에서 admin 을 빼면 **관리 API 자신도 막혀**
  설정으로 되돌릴 수 없게 됩니다. 그때는 server.json 을 직접 고치고 서버를 다시 띄우세요.
- 테스트를 돌리기 전에 LLMWIKI_LEDGER_DIR_PATH 가 임시 폴더를 가리키는지 확인하세요.
  그러지 않으면 단위 테스트가 실사용 원장에 수백 줄을 섞어 넣습니다.
- data/ledger 는 그 환경의 기록입니다. 다른 환경에서 복사해 오지 마세요.
```

---

## 14. 더 읽을 것

| 문서 | 무엇 |
|---|---|
| [IMPLEMENTATION_PLAN_0923.md](history/2026-09-23/IMPLEMENTATION_PLAN_0923.md) | **설계 근거** — 원인을 어떻게 특정했는지(실험 포함), 검토하고 기각한 대안, 결정 사항 |
| [CONCURRENCY.md](CONCURRENCY.md) | 동시성 전반 — 읽기/쓰기 락, 빌드 중 질의, 접근 제어, §8.0 잠금의 진짜 원인 |
| [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) | `config.json` 키 하나하나의 뜻 |
| [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) | 새 환경 기동 전체 절차 |
| [PORTING.md](PORTING.md) | LLM·임베딩·MCP **연결 정보**와 자격증명 위치 |
| [REQUEST_HISTORY.md](REQUEST_HISTORY.md) | 요청 프로파일과 "그때 그 답 다시 보기" |
| [ACTIVITY_DETAIL.md](ACTIVITY_DETAIL.md) | 진행 중 작업 상세 패널 |
| [SECURITY.md](SECURITY.md) | 역할·권한(`requests all` 포함) |
| [LOG_QUOTA.md](LOG_QUOTA.md) | `logs/` 총량 제한 — 원장이 `data/` 에 있는 이유 |
