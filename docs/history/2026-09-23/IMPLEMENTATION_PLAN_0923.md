# IMPLEMENTATION PLAN 2026-09-23 — 동시 질의 DB 잠금 · 종류별 한도 · 요청 원장(Request Ledger)

> **이 문서의 지위**: 2026-09-23 사용자 요청 5건 + 대화 중 확정된 결정 사항을 **모두 취합한 단일 계획서**.
> 설계 근거(검토한 대안과 기각 사유 포함)를 남기는 것이 목적이고, 운영자용 절차는 구현과 함께
> `docs/REQUEST_LEDGER.md` · `docs/CONCURRENCY.md` · `docs/BRINGUP_GUIDE.md` §3.5 에 쓴다.
> 대상 독자: 이 저장소를 사내 환경으로 옮겨 세우는 엔지니어(또는 그 일을 대신하는 LLM).

---

## 0.0 지금 상태 한 장 (2026-09-23 마감 시점)

> **이 절이 이 문서에서 가장 먼저 읽을 것이다.** 요청 5건이 각각 어디까지 왔는지, **무엇이 아직 안 됐는지**를
> 숨기지 않고 적는다. 아래 §0.1 이후는 설계 근거이고, 여기가 현황이다.

| 요청 | 상태 | 핵심 |
|---|---|---|
| **1** 동시 질의 DB lock | **완료** | 원인이 관측 기록이 아니라 **커밋 없는 `cache_put`** 이었다(실험으로 특정). 잠금 보유 **98초 → 1ms**, 다른 연결 쓰기 **3,310ms 실패 → 0.89ms 성공** |
| **2** 질의/검색 별도 한도 | **완료** | `concurrency.classes` · `rate_limit.classes`. 질의 6 < 전체 8 로 **검색용 슬롯 2개 상시 예약**. 대기 한도는 질의 30분 / 검색 30초 |
| **3** 모든 요청 통합 화면 | **완료** | 요청 원장(JSONL) + Observability 「📋 요청 (전체)」 탭. 목록·상태 스트립·누적 시간 막대·상세(개요·타임라인·답변·프로파일·로그·거절 사유·같은 시각의 요청) |
| **4** 사라지는 요청 재현 | **완료** — 원인 9개를 코드와 실측으로 특정해 모두 고치고, 통제된 재현 하네스 `tools/verify/verify_request_ledger.py` 로 **재현했다**: 동시 질의 24건 중 **11~15건(46~50%)이 예전 구조에서는 기록 없이 사라진다**. 원장은 누락 0 | §4.1 · §0.0-재현 |
| **5** 3창구 정합 + 문서 | **완료** — 정합 `verify_surface_align` OK(CLI 49 · Web 114 · MCP 20 전수 일치). 문서는 `REQUEST_LEDGER.md` 를 이번 회차 내용으로 전면 갱신(비동기 질의·로그 통합·거절 맥락·질의 로그 통합·포팅 절차·포팅용 LLM 프롬프트) | §5.2 |

**재현 결과** (2026-09-24, 격리 환경 · mock LLM · 슬롯 4 · 대기열 8 · 대기한도 5초):

```
보낸 질의                  : 24
requests 테이블(예전 구조)  : 13      ← 나머지 11건(46%)은 아무 기록도 남지 않는다
요청 원장                  : 24      (done 13 + rejected 11 · 누락 0 · 중복 0)
점검 모드 거절             : 사유·설정 키·해제 방법까지 기록됨
강제 종료                  : 진행 중이던 6건이 '끝이 없는 기록' = 중단 으로 남음
```

**채택하지 않은 것** (계획에는 있었으나 만들지 않기로 판단한 것):

| 항목 | 왜 없는가 |
|---|---|
| §1.6-H 비동기 DB writer (신규 모듈) | 0-A·A·B 로 잠금 보유가 1ms 가 되어 **필요가 없어졌다**. 모듈을 늘리는 대신 제외 |
| §1.6-G 쓰기별 토글 (질의 기록 on/off) | 관측 기록이 잠금의 원인이 아니었으므로 **끌 이유가 사라졌다**. 상위 토글 evolve_capture 로 충분하다고 판단 |

**이번 회차에 추가로 나온 것**(계획에 없었으나 사용자 지적으로 들어간 것): 질의 **비동기 잡화**(화면 멈춤 해소) ·
로그↔요청 **양방향 연결** · 거절 **사유 맥락**(무엇이 막았나·설정 키·되돌리는 법) · 누적 시간 막대 ·
갱신 주기 설정화 · **질의 로그를 requests 로 통합** · 테스트가 실제 원장을 오염시키던 문제 수정.

---

## 0. 요청 원문과 결정 사항

### 0.1 최초 요청 (5건)

| # | 요청 |
|---|---|
| 1 | **빌드가 이미 완료된 data** 에 대해서, 동시에 접속한 사용자가 query 를 요청할 때 db lock 이 잡혀 있는 경우가 있는데 왜 잡혀 있나? 어차피 read 만 하면 되니까 lock 잡을 필요 없는 것 아닌가? |
| 2 | ask-query 는 오래 걸릴 수 있지만 ask-채널검색은 빠르게 끝난다. 둘에 **별도의 한도**를 두고 **서버 관리자가 설정**할 수 있게 해 달라 |
| 3 | Observability 에 요청 프로파일·진행 중 작업·로그·질의 로그가 있는데, **모든 서버 요청을 통합해서 볼 수 있는 것**이 있나? timeout 으로 버려진 요청은 정보가 전혀 안 남는다. **모든 요청이 목록에 뜨고, 누르면 진행 상태를 볼 수 있는 창**이 있으면 좋겠다 |
| 4 | 3번을 요청한 이유: 동시에 다수 사용자가 query 를 요청하면 **일부 query 가 기록 없이 사라진다**. 시간이 좀 흐르면 없어져서 원인을 찾을 수가 없다. **재현되는지 확인해 달라** |
| 5 | 위 내용을 **CLI · Web UI · MCP 모두 align** 되게 작성하고, 기능 문서를 상세히. 오늘 수정을 **다른 환경으로 포팅**하고 싶으니 수정과 함께 **가이드 문서**를 상세히 |

### 0.2 대화 중 확정된 결정 사항

| 시점 | 사용자 지시/질문 | 결정 |
|---|---|---|
| ① | "코드와 문서 상세히 파악하고서 답변해줘" | 추측 금지. 모든 주장은 **코드 인용 또는 실측**으로 뒷받침한다 |
| ② | 질의 끝 쓰기들이 "필수적인 요소야?" | 답변 생성에는 **불필요**, 제품 기능으로는 **필수**. 줄이지 않고 **쓰는 방식**을 고친다 |
| ③ | "toggle 로 on/off 할 수 있게 되어 있어?" | 대부분 되지만 **`requests` 기록은 끌 방법이 없고**, `evolve_capture` 하나가 쓰기 3종을 묶고 있다 → **토글을 쪼갠다**(§1.6-G) |
| ④ | "이미 빌드 완료된 이후에 다수 사용자 동시 질의" | 분석 범위를 **질의 대 질의**로 한정. 빌드·워처 경합은 이번 범위가 아니다(회귀 확인만) |
| ⑤ | "Queue size 를 8보다 16으로?" → 실측 제시 후 "8개로 그대로 두고 진행. 대기열 64 → 128" | **`max_parallel_reads` = 8 유지, `queue_max` = 128. 적용 완료**(§7.1) |
| ⑥ | "관측기능을 한꺼번에 모두 끌 수 있는 상용화 모드 스위치가 있으면 lock 을 전혀 안 잡아도 되나?" | **DB 쓰기 잠금은 없앨 수 있다.** 단 `embed_query_cache` 를 포함해야 하고, 프로세스 내부 뮤텍스는 남는다(µs). `mode.profile` 로 설계하되 **요청 원장은 상용 모드에서도 유지**(§1.7) |
| ⑦ | "embedding_cache 는 어쩔 수 없다는 거지? / 뭐 하는 기능이야? 질의에 꼭 필요해?" | **어쩔 수 없지 않다.** 캐시는 필수가 아니지만 **끄는 게 아니라 제대로 쓰는 것**이 답이다(§1.2) |

---

## 1. 요청 1 — 동시 질의 때 DB lock 이 왜 잡히나

> **범위**: 빌드가 **이미 끝난** 정상 운영 상태에서 다수 사용자가 동시에 질의하는 경우.
> 빌드·워처와의 경합은 이번 범위가 아니다 — 워처는 `check_changes()` 가 변경을 찾았을 때만 쓰기 티켓을 받는다(`web/server.py:292-295`).
> 아래 원인은 **질의끼리만 있어도** 성립한다.

### 1.1 먼저: 읽기는 잠그지 않는다 (사용자 전제는 옳다)

`store.py:188-194` 는 모든 연결을 **WAL** 로 연다. WAL 에서 읽기는 쓰기를 막지 않고 쓰기에도 막히지 않는다.
실측으로도 확인했다 — 쓰기 잠금이 걸린 상태에서 **다른 연결의 읽기는 0.02ms** 에 끝났다(§1.2).

**그러므로 "읽기만 하면 lock 이 필요 없다" 는 말은 맞다. 문제는 질의가 읽기만 하지 않는다는 것이다.**

### 1.2 원인 ⓪ — 커밋 없는 쓰기 하나가 **질의 내내** 쓰기 잠금을 쥔다 (실측 확인, 진짜 원인)

`retrieval.embed_query()` 는 질의 벡터를 `embedding_cache` 에 넣는다(토글 `embed_query_cache`, **기본 on**).

```python
# llmwiki/retrieval.py:257-261
v = embedder.embed([query])[0]
try:
    store.cache_put(name, model, [(sha, v)])     # ← INSERT 만 한다
except Exception:
    pass      # 캐시에 못 넣어도 검색은 계속된다   ← 실패가 로그에도 안 남는다
```

`Store.cache_put()`(`store.py:605-611`)은 **`commit()` 을 하지 않는다.** Python `sqlite3` 는 기본
`isolation_level=""` 이라 INSERT 앞에서 트랜잭션을 **암묵적으로 연다**. SQLite 쓰기 트랜잭션은
첫 쓰기에서 **WRITER 잠금을 잡고 COMMIT/ROLLBACK 까지 놓지 않는다.**

이 INSERT 는 `vector_search` **초반**(질의 시작 1~2초)에 일어나고, 이 연결의 **다음 커밋은 질의 맨 끝**
(`add_proposal` → `log_query`)이다. 실측 질의가 98~118초이므로:

> **질의 1건이 거의 전체 수명 동안 SQLite 쓰기 잠금을 혼자 쥔다.**

**검증** (이 저장소와 같은 조건: WAL + busy_timeout, 2026-09-23)

```
python sqlite3: 3.50.4 | py 3.14.7
autocommit/isolation_level: ''        ← 암묵적 트랜잭션 켜짐
A: INSERT 후 in_transaction = True    ← 쓰기 잠금 보유
B(다른 연결) 쓰기 → 3,310ms 대기 후 'database is locked'   (busy_timeout 3초를 그대로 소진)
C(다른 연결) 읽기 → 0.02ms             ← WAL 이라 읽기는 전혀 안 막힘
A.rollback() 후 남은 행: []            ← 캐시 항목이 통째로 버려짐
```

실서버는 `db_busy_timeout_s = 60` 이므로 **다른 동시 질의는 자기 `cache_put` 에서 최대 60초를 기다린 뒤
`database is locked` 로 실패**하고, 그 실패는 위의 `except Exception: pass` 가 **아무 말 없이 삼킨다**.
`logs/` 에 이 사건의 흔적이 없는 이유가 이것이다(요청 4의 "기록이 없다" 와 같은 뿌리).

**왜 지금까지 안 보였나**: 캐시 **적중**이면 쓰기가 없다. 2026-09-23 19:37 실측 3건(#3926~3928)은
기본 예시 질문이라 전부 적중이었고 `vector_search` 가 0.38~0.81초에 끝났다.
**서로 다른 질문을 여러 명이 동시에 던지는 실제 상황이 정확히 캐시 미스가 겹치는 상황**이다.

#### `embedding_cache` 는 무엇이고 질의에 꼭 필요한가 (결정 사항 ⑦)

**무엇인가**: `(provider, model, sha1(본문)) → 벡터` 의 **내용 주소 캐시**(`store.py:89-91`).
색인이 아니라 "이 글자열은 전에 벡터로 바꿔 봤다" 는 메모장이다.

**주된 용도는 질의가 아니라 빌드다.** 리빌드·문서 이동·이름 변경·롤백에서 안 바뀐 청크를 다시 임베딩하지 않기 위한 것이다(`embed_run.py:7`).
실측이 그대로 보여 준다:

```
DB 파일        : 460.9 MB
embedding_cache: 130.3 MB   ← DB 의 28%
embeddings     :  66.0 MB
   model=bge-m3          n=16705  65.3 MB
   model=bge-m3:latest   n=16654  65.1 MB     ← 같은 모델, 다른 이름 표기
두 이름에 중복 저장된 sha: 16654
```

행 33,359개가 전부 청크 규모(청크 16,898 × 모델 이름 2종)이고 **질의 벡터는 반올림 오차 수준**이다.
질의 쪽 사용은 2026-09-19 에 덧붙인 부수 용도다 — 원격 임베더(ollama)에서 질의 임베딩 1회가 2초를 넘기 때문.

| 질문 | 답 |
|---|---|
| 질의를 **벡터로 바꾸는 것** | 벡터 채널(`toggles.embed`)을 쓰면 **필수**. 캐시와 무관하다 |
| 그 결과를 **캐시하는 것** | **필수 아님.** 꺼도 결과는 같고 속도만 느려진다(토글 `embed_query_cache` 가 이미 있다) |
| 그럼 꺼야 하나 | **아니다.** ① 잠금은 0-A 로 1ms 가 되고 ② 아래 중복 임베딩은 **100% 적중**할 자리라 끄면 질의당 5초를 확정 손해 |
| 답은 무엇인가 | **끄는 게 아니라 질의 경로에서 DB 쓰기를 빼는 것**(0-E) |

#### 더 나쁜 사실: 이 캐시는 제대로 쓰이지도 않는다

질의 벡터를 만드는 자리가 셋인데 **캐시를 지나는 것은 하나뿐**이다.

| 자리 | 코드 | 캐시 경유 | 실측 |
|---|---|---|---|
| `vector_search` | `retrieval.py:278` → `embed_query()` | **✔** | 0.38~0.81s (적중 시) |
| `doc_vector_search` | `precompute.py:216` `embedder.embed([query])` | **✘ 매번 새로** | 3.30~3.90s |
| `doc_expand` | `query_engine.py:917` `p.embedder.embed([q])` | **✘ 매번 새로** | 2.07~2.10s |

**같은 질문 문자열을 한 질의 안에서 최대 3번 임베딩한다.** ollama 에서 1회 2초 남짓이므로
**질의 1건당 약 5~6초가 순전히 중복 임베딩**이다(실측 3.3 + 2.1 = 5.4초).

#### 고치는 법 — 0단계

| 조치 | 내용 | 효과 |
|---|---|---|
| **0-A** | `cache_put()` 뒤 **즉시 `commit()`** (또는 `embed_query` 를 `with store.write_tx():` 로 감싸기) | 잠금 보유 **≈98초 → ≈1ms** |
| **0-B** | `except Exception: pass` → **경고 로그 + 계측**(`_warn_locked` 와 같은 자리) | 조용한 실패 금지 |
| **0-C** | 세션 반납 시 `c.in_transaction` 이면 **경고**(지금은 조용히 `rollback`, `store.py:239-242`) | "커밋 없이 쓰고 나간 코드" 를 즉시 드러냄 — 회귀 방지 |
| **0-D** | `doc_vector_search`·`doc_expand` 를 **`embed_query()` 로 통과** | 질의당 **약 5초 단축** |
| **0-E** | (최종형) **읽기는 SQLite, 쓰기는 메모리 LRU + 비동기 일괄 flush** | 질의 경로의 DB 쓰기 **0**. 청크 캐시와의 sha1 공유는 유지 |
| **0-F** | **모델 이름 정규화**(`bge-m3` ≡ `bge-m3:latest`) + 1회 병합 마이그레이션 | **65.1MB 회수**. 이름 표기가 바뀌며 **전체를 다시 임베딩**한 사고의 재발 방지 |

> **이것이 사용자가 말한 "동시에 질의할 때 db lock" 의 실체다.** 빌드도, 워처도, 관측 기록도 아니다.

### 1.3 원인 ① — 질의는 끝에 **커밋을 5~6번** 한다

답을 만든 **뒤에** 여는 쓰기 트랜잭션 전수(결정 사항 ②·③의 근거 표):

| # | 쓰기 | 코드 | 게이트 | 기본 |
|---|---|---|---|---|
| 1 | `proposals` INSERT (0~수 건) | `evolve.capture_query()` ← `query_engine.py:529-532` | `toggles.evolve_capture` | on |
| 2 | `query_log` INSERT + **commit** | `store.log_query()` `store.py:1258` | `toggles.evolve_capture` | on |
| 3 | `requests` INSERT + **commit** (+50건마다 `DELETE … id <= rid-2000`) | `store.log_request()` `store.py:1099` | **토글 없음** (`record_request` 내부 플래그뿐) | 항상 |
| 3b | 보관 파일 `data/requests/YYYY-MM/req_<id>.json` | `store.archive_request()` | `requests_dir` 설정 | on |
| 4 | `query_log` UPDATE + **commit** | `store.set_query_request_id()` `store.py:1280` | query_id 있을 때 | on |
| 5 | `episodes` INSERT | `memory.record_episode()` ← `query_engine.py:578-582` | `toggles.evolve_capture` | on |
| 6 | `forensics` INSERT (근거 부족일 때만) | `forensic.record()` ← `query_engine.py:566-573` | `toggles.forensic_auto` | on |
| 7 | `data/reruns/req_<id>.json` 파일 | `rerun.save()` ← `query_engine.py:562` | `toggles.rerun_capture` | on |
| 8 | `answer_cache` INSERT | `precompute.put_cached()` ← `query_engine.py:597` | `toggles.precompute` | off |
| 9 | `logs/analysis/req_<id>.md` | `analysis.run_for_result()` ← `query_engine.py:586` | `toggles.analysis_mode` | off |

기본 설정에서 **질의 1건 = 커밋 5~6회**이고, SQLite 쓰기는 WAL 에서도 한 번에 하나다.
동시 8건이면 **40~48개 쓰기 트랜잭션이 한 줄로 선다.** 여기에 비용을 곱하는 셋:

| 곱하는 요인 | 지금 | 왜 비싼가 |
|---|---|---|
| `PRAGMA synchronous` | **설정 안 함 → SQLite 기본 `FULL`** | WAL + FULL 은 **커밋마다 WAL 을 fsync**. 질의당 fsync 6회 |
| trace JSON | `log_query`·`log_request` 가 **같은 trace 를 각각 `json.dumps`** (`store.py:1271`, `1132`) | 한 건 수십 KB 를 GIL 쥔 채 두 번 |
| `requests` 정리 | 50건마다 인라인 `DELETE`(`store.py:1145`) | 테이블이 이미 상한(실측 2028행)이라 50번째 질의만 벌크 삭제를 떠안음 |

### 1.4 원인 ② — 빌드 직후 첫 질의들이 **캐시 적재 락 하나**에 몰린다

`Store._cache_lock` 은 **RLock 하나**인데 세 캐시가 공유한다.

| 캐시 | 적재 내용 | 코드 |
|---|---|---|
| 벡터 행렬 | `SELECT chunk_id, dim, vec FROM embeddings` 전체 + `np.vstack` | `store.py:876-902` |
| 엔티티 인덱스 | `SELECT … FROM entities` 전체 | `store.py:968-984` |
| 문서 메타 맵 | `SELECT … FROM doc_meta` 전체 (문서 접근 제어가 질의마다 읽는다) | `store.py:558-572` |

빌드가 끝나면 `sync_with_db()` 가 `invalidate_caches()` 를 부른다(`pipeline.py:397-409`).
직후 동시 질의 N건이 전부 미스 → 한 스레드가 수백 MB 를 적재하는 동안 **나머지가 같은 락에서 대기**한다.
캐시가 데워진 뒤에는 락 없는 빠른 경로(`store.py:877-879`)라 문제없다 — **빌드 직후 한 번의 thundering herd**.

### 1.5 원인 ③ — 프로세스 락(RWLock). DB 잠금이 아니다

`reqmgr.py:227-299` 의 `RWLock` 은 SQLite 와 무관한 파이썬 락이다. 전체 리빌드·설정 저장·스냅샷 복원이
`exclusive` 로 잡을 때만 질의가 기다린다. **이번 범위(빌드 완료 후)에서는 해당 없음.**
설계대로이며 `concurrency.reads_during_build` 로 조절한다([CONCURRENCY.md](../../CONCURRENCY.md) §2).

### 1.6 조치 목록 (싼 것부터)

**원칙: 관측 기록을 줄이지 않는다**(결정 사항 ②). 고칠 것은 *무엇을 쓰는가* 가 아니라 *어떻게 쓰는가* 다.

| 순 | 조치 | 효과 | 파일 |
|---|---|---|---|
| **0** | §1.2 의 **0-A~0-F** | 잠금 **98초 → 1ms**, 질의 **−5초**, **−65MB** | `store.py` `retrieval.py` `precompute.py` `query_engine.py` |
| **A** | 끝 쓰기 6건을 **한 트랜잭션**으로 (`store.log_query_bundle()` 신설) | 잠금 획득 6→1회, fsync 6→1회. **`request_id` 를 같은 트랜잭션에서 얻으므로 응답에 그대로 실린다** | `store.py` `query_engine.py` |
| **B** | **`PRAGMA synchronous=NORMAL`** (설정 키 `db_synchronous`, 기본 `NORMAL`) | 커밋마다의 fsync 제거. **WAL 이면 DB 손상 없음** — 정전 시 최근 커밋 몇 건만 날아가고 그건 재빌드로 복구되는 색인/관측 기록 | `store.py` `config.py` |
| **C** | trace 를 **한 번만** 직렬화 | GIL·메모리 절반 | `store.py` |
| **D** | `requests` 정리를 인라인에서 빼 `maintenance`/스케줄러로 | 50번째 톱니 제거 | `store.py` `scheduler.py` |
| **E** | `_cache_lock` 을 캐시별 3개로 분리 + 적재를 락 밖에서 | §1.4 herd 해소 | `store.py` |
| **F** | 쓰기 대기 ms · fsync 수 · 캐시 적재 ms 를 trace(`log` 단계)와 원장에 기록 | 추측 대신 수치 | `store.py` `profiler.py` |
| **G** | ~~쓰기별 토글(결정 사항 ③): 요청 기록용 토글 신설, evolve_capture 를 셋으로 분리~~ — **미채택**: 관측 기록이 잠금의 원인이 아니었으므로 끌 이유가 사라졌다 | — | — |
| **H** | ~~(A~G 이후에도 남으면) 비동기 DB writer — request_id 가 필요 없는 것만 큐로~~ — **미채택**: 0-A·A·B 로 잠금이 1ms 가 되어 불필요 | — | — |

> **A 를 (미채택한) H 보다 먼저 두었던 이유**: `request_id` 는 응답에 실려 나가고 rerun·forensic·활동 목록 티켓이 쓴다
> (`query_engine.py:547-551`, `web/server.py:1780`). 처음부터 비동기로 빼면 응답 시점에 id 를 모른다.
> 결과적으로 A 만으로 충분해 H 는 만들지 않았다.

> **기각한 대안**
> - *`db_busy_timeout_s` 를 올린다*: 대기만 늘어난다. 읽기 슬롯을 더 오래 잡으므로 오히려 나쁘다.
> - *관측 기록을 끈다*: 요청 3·4 와 정면으로 어긋난다. G 로 **선택지**만 준다.
> - *`embed_query_cache` 를 끈다*: 질의당 5~6초 손해. 잠금은 0-A 로 이미 해결된다.
> - *별도 DB 파일로 분리*: `requests`↔`query_log` 조인이 깨진다. 병목은 파일이 아니라 **커밋 횟수**다.
> - *더 공격적인 `wal_autocheckpoint`*: 체크포인트가 오히려 읽기를 잠깐 막는다. 기본값 유지.

### 1.7 "상용화 모드" — 관측을 한꺼번에 끄는 스위치 (결정 사항 ⑥)

**질문**: 관측을 통째로 끄면 동시 질의가 겹쳐도 잠금을 전혀 안 잡나?
**답**: SQLite 쓰기 잠금은 없앨 수 있다. 단 조건이 있고, "락 0" 은 아니다.

**(1) 스위치는 `embed_query_cache` 를 반드시 포함해야 한다.** 관측 토글만 끄면 §1.2 의 잠금이 그대로 남는다.
게다가 **관측만 끄면 더 나빠진다** — `cache_put` 의 INSERT 를 커밋해 줄 주체가 사라져 세션 종료 시 `rollback` 되므로
**임베딩 캐시는 조용히 멈추면서 잠금은 계속 잡는** 최악의 조합이 된다(§1.2 실측의 마지막 줄).
→ 스위치를 만들기 **전에 0-A 가 먼저 들어가야 한다.**

**(2) 남는 락** (전부 프로세스 내부, µs 단위): `RequestManager._lock`/`RWLock`(요청당 1회) ·
`progress._LOCK`(단계마다) · `Store._pool_lock`(세션 대여/반납) · `_cache_lock`(캐시 미스 시) · GIL.
**SQLite 쓰기 잠금은 없다** ← 목표 달성.

**(3) 잃는 것**: 요청 프로파일·"그때 그 답 다시 보기"·`rerun`·`analyze`·`sweep` 기준 요청(3),
자가진화 입력·피드백·사용자별 통계(2), **자가진화 자체**(1·5), 포렌식 누적(6), 워터폴 ⟲(7).

**(4) 설계 — `mode.profile` 한 줄, 그러나 원장은 남긴다**

```jsonc
// server.json
"mode": {
  "_comment": "운영 프로파일. full(기본)=전부 기록 · lean=무거운 기록만 끔 · production=DB 관측 쓰기 전부 끔(요청 원장만 유지). 개별 토글로 덮어쓸 수 있다.",
  "profile": "full"
}
```

| 프로파일 | `requests` | `query_log` | `proposals`/`episodes` | `forensics` | `reruns` | `embedding_cache` | **요청 원장(JSONL)** |
|---|---|---|---|---|---|---|---|
| `full`(기본) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| `lean` | ✔ | ✔ | ✘ | ✔ | ✘ | ✔ | ✔ |
| `production` | ✘ | ✘ | ✘ | ✘ | ✘ | ✔(커밋 즉시) | **✔** |

**요점은 `production` 에서도 요청 원장을 켜 둔다는 것이다.** 원장은 SQLite 가 아니라 JSONL + 전용 writer 라
**쓰기 잠금을 전혀 잡지 않는다**(§3.2). 그래서 "누가 언제 무엇을 요청했고 어떻게 끝났나" 는 상용 모드에서도 남고,
없어지는 것은 "그 질의의 단계별 내부 trace" 다. **잃는 것과 지키는 것의 경계를 여기에 둔다.**
`embedding_cache` 는 관측이 아니라 **성능 캐시**이므로 `production` 에서도 켠다.

**(5) 우선순위는 낮다.** 0-A + A + B 를 넣으면 질의당 잠금 보유가 **1ms × 1회**(98초 질의의 0.001%)다.
따라서 `production` 은 **성능 대책이 아니라 정책 스위치**다 — "사내 규정상 질문 원문을 DB 에 남기지 않는다" 같은 요구에 쓴다.
문서에도 성능 목적으로 켜라고 쓰지 않는다.

---

## 2. 요청 2 — ask-query 와 ask-채널검색에 **별도 한도**

### 2.1 지금 구조

| 창구 | kind | weight | 슬롯 | 동시/분당 제한 | 시간 제한 |
|---|---|---|---|---|---|
| `POST /api/query` (Ask › 질의) | `query` | `read` | `max_parallel_reads`(8) **공용** | `max_parallel_per_user`(3) · `query_per_user_per_min`(20) | `timeouts.query_s` 900 |
| `POST /api/search` (Ask › 채널 검색) | `search` | `read` | **같은 8슬롯** | 같은 3 · 검색 전용 분당 제한 **없음** | `timeouts.search_s` 120 |

**시간 제한만 종류별로 갈라져 있고 슬롯·동시 수·대기열·분당 수는 전부 공용**이다.
그래서 90초짜리 질의 8건이 슬롯을 다 먹으면 100ms 면 끝날 채널 검색이 대기열에 선다 — 사용자가 지적한 그대로다.

### 2.2 설계 — 종류별 한도 표 + "질의 상한 < 전체 슬롯"

`server.json` 에 **종류(kind)별 표**를 추가한다. 비운 키는 지금의 공용 값을 따른다(하위 호환).

```jsonc
"concurrency": {
  "max_parallel_reads": 8,          // 전체 읽기 슬롯 (그대로)
  "classes": {
    "_comment": "요청 종류별 한도. 비운 키는 위의 공용 값을 따른다. 각 종류가 전체 슬롯을 다 먹지 못하게 하는 것이 목적이다.",
    "query":  {"max_parallel": 6, "max_parallel_per_user": 2, "queue_max": 96, "queue_timeout_s": 120},
    "search": {"max_parallel": 4, "max_parallel_per_user": 3, "queue_max": 32, "queue_timeout_s": 15},
    "mcp":    {"max_parallel": 4, "max_parallel_per_user": 2, "queue_max": 32, "queue_timeout_s": 60},
    "cli":    {"max_parallel": 2, "max_parallel_per_user": 1, "queue_max": 8,  "queue_timeout_s": 60}
  }
},
"rate_limit": {
  "classes": {
    "query":  {"per_user_per_min": 20},   // 기존 query_per_user_per_min 은 별칭으로 유지
    "search": {"per_user_per_min": 60}
  }
}
```

**핵심은 예약 효과다.** `query.max_parallel = 6 < max_parallel_reads = 8` 이므로 질의가 아무리 몰려도
**슬롯 2개는 항상 비어 있다** → 빠른 검색은 즉시 실행된다. 별도 "예약 슬롯" 개념을 만들지 않고
상한 하나로 같은 효과를 내므로 설정이 하나 줄고 설명이 쉽다.
`queue_timeout_s` 도 종류별로 나눈다 — 검색은 15초 안에 못 들어가면 기다릴 이유가 없다.

### 2.3 시간 제한은 옮기지 않는다

`timeouts.query_s`/`search_s` 는 이미 종류별이고 `architecture.py:326-329`(단계별 제한 표)와 문서가 그 이름을 참조한다.
**같은 값을 두 곳에 두지 않는다**는 원칙에 따라 `classes` 에 `timeout_s` 를 넣지 않고, 문서에서 한 표로 묶어 보여 준다.

### 2.4 관리자 설정 경로 (세 창구)

| 창구 | 방법 |
|---|---|
| Web | `Observability › 서버 모니터 › 제한` 에 **종류별 표**(행=kind, 열=동시/사용자당/대기열/대기시간/분당) → `POST /api/admin/server` |
| CLI | `python -m llmwiki server limits set concurrency.classes.search.max_parallel=6 rate_limit.classes.search.per_user_per_min=90` |
| 파일 | `server.json` 직접 편집 · 환경변수 `LLMWIKI_SERVER_CONCURRENCY_CLASSES_…` |

`reqmgr.set_limits()` 는 지금 2단계 키만 받는다(`sect.name`). **3단계 키**를 받도록 확장하고,
모르는 키는 지금처럼 `KeyError` → 400 으로 돌려준다(조용히 버리지 않는다).

### 2.5 용량 산정 — 슬롯 8 유지의 근거 (결정 사항 ⑤)

**용어**: 8은 대기열이 아니라 **동시 실행 슬롯**(`max_parallel_reads`)이다.

**이 슬롯 수가 곧 LLM 동시 호출 수다.** `providers.py` 에 프로바이더 단위 동시성 제한이 **없다**
(있는 것은 스윕 전용 `sweep_max_parallel`). 앙상블을 켠 역할이 있으면 멤버 수만큼 더 곱해진다.

**실측** (2026-09-23 19:37, requests #3926~3928)

| 요청 | 전체 | `answer_llm` | `query_expand` | `rerank_llm` | `claim_check` | LLM 호출 | SQL 문 |
|---|---|---|---|---|---|---|---|
| #3928 | 118.1s | 75.3s | 8.7s | 6.0s | 21.0s | 14 | 20,226 |
| #3927 | 99.9s | 75.5s | 9.3s | 6.6s | 3.0s | 10 | 16,324 |
| #3926 | 98.2s | 71.7s | 8.7s | 2.7s | 8.5s | 10 | 34,732 |

**질의 시간의 75~97% 가 LLM 대기다.** 검색·DB 는 병목이 아니다.

| 환경 | 판단 |
|---|---|
| **지금 이 PC** (`llm_provider=openai` + `llm_model=llama3.1`, `embed_provider=ollama` → 로컬 Ollama) | **올리면 안 된다.** Ollama 는 `OLLAMA_NUM_PARALLEL` 만큼만 처리하고 나머지는 **Ollama 안에서** 줄을 선다. 우리 대기열이 Ollama 대기열로 옮겨갈 뿐이고 그동안 슬롯·연결·메모리는 잡혀 있다. [CONCURRENCY.md](../../CONCURRENCY.md) §3 권장표도 "로컬 Ollama 1대 → **4~6**" |
| **사내 PAT 게이트웨이** | 올릴 만하다. 단 **포화점을 재고** 정한다 |

**포화점 측정** (`verify_request_ledger.py --capacity`): N = 2/4/8/16/24 로 같은 묶음을 던져
**throughput 이 더 오르지 않는 지점**을 찾는다. 그 값이 그 환경의 `max_parallel_reads` 다.

**올릴 때 같이 바꿔야 하는 것**: `db_pool_size` 16 → 32(읽기 슬롯 16이면 질의만으로 소진, 채널 검색은 하위 스레드가 더 빌린다) ·
§1.6 **A·B 를 먼저**(16 동시면 끝 커밋이 96개) · `classes.query.max_parallel` 12~13 · `max_parallel_per_user` 재검토.

**용량 계산**: 98초 ÷ 8슬롯 = **약 4.9 질의/분**. 30명이 5분에 한 번만 물어도 6/분이라 **지금 설정은 이미 용량 초과**다.
대기는 정상 동작이고 해법은 셋이다 — ① LLM 동시성(게이트웨이가 받아 줄 때만) ② **질의당 LLM 호출/시간 축소**
(`query_expand` 9% · `claim_check` 3~18% · `rerank_llm` 3~6% 는 토글이거나 `llm_roles` 로 작은 모델을 줄 수 있다)
③ **답변 캐시**(`query_cache`·`precompute` 가 지금 **둘 다 off**).

---

## 3. 요청 3 — 모든 서버 요청을 한 곳에서: **요청 원장(Request Ledger)**

### 3.1 지금 있는 것과 없는 것

| 화면 | 원천 | 수명 | 실패한 요청이 남나 |
|---|---|---|---|
| 요청 프로파일 | `requests` + `data/requests/*.json` | `keep_requests` 2000행 / 90일 | **아니오** — 성공·캐시 적중만(`query_engine.py:546-551`) |
| 진행 중 작업 | `progress._LIVE` + `reqmgr.history` | 끝난 뒤 **5분** / 최근 **500건**, 메모리 | 재시작하면 소멸 |
| 로그 | `logs/*.log` | 로테이션 | 질의 시작만. **거절은 기록 없음** |
| 질의 로그 | `query_log` | — | 답변이 만들어진 것만 |

**즉 "모든 요청" 을 담는 곳이 없다.** 거절(429/503/403/413)·대기열 시간 초과·watchdog 취소·500·잘못된 요청은
어디에도 영구 기록이 없다. 이 한계는 `docs/ACTIVITY_DETAIL.md` §3 에 이미 적혀 있던 것이기도 하다.

### 3.2 설계

**새 모듈 `llmwiki/reqledger.py`.** 요청 하나당 **두 줄**(접수 `open`, 종료 `close`)을 JSONL 에 남기고,
읽을 때 토큰으로 접어 한 건으로 만든다.

```
data/ledger/req-2026-09-23.jsonl
{"ev":"open","ts":…,"token":"r-ab12…","kind":"query","origin":"web","user":"kh82.kim","role":"class2","ip":"10.1.2.3","label":"ISSUE-2001 …","method":"POST","path":"/api/query"}
{"ev":"close","ts":…,"token":"r-ab12…","status":"rejected","http":503,"code":"queue_timeout","queue_wait_s":120.0,"ms":120004,"stage":"(대기)","db_wait_ms":0,"request_id":null,"run_id":null,"error":"대기 시간 초과 …"}
```

| 정한 것 | 이유 |
|---|---|
| **JSONL**, SQLite 아님 | 원장이 기록해야 할 대표 사건이 **"DB 가 잠겨 기록하지 못했다"** 이다(§1.2). 같은 DB 에 동기로 쓰면 같은 이유로 또 사라진다. append 라 잠금이 없다 |
| **전용 writer 스레드 + 큐** | 요청 스레드가 디스크를 기다리지 않는다 |
| **open 을 먼저** 쓴다 | 프로세스가 죽어도 "들어왔다" 는 남는다. close 없는 항목은 `unknown(중단)` 으로 보인다 — **사라지지 않는다** |
| **큐 포화 시 드롭 카운터** | 못 쓴 사실 자체를 기록한다. 조용한 유실 금지 |
| `data/ledger/` (logs/ 아님) | `logs/` 는 총량 제한(`log_total_max_mb`)의 prune 대상이다([LOG_QUOTA.md](../../LOG_QUOTA.md)) |
| 보존은 **일수 + 총량** 둘 다 | 디스크를 무한히 먹지 않게 |

**무엇을 남기나 (폴링 소음)**: `GET /api/progress`·`/api/activity` 는 브라우저마다 1.5초에 한 번 온다.
`ledger.include_get` 으로 제어한다 — `none`(POST/DELETE/MCP + 모든 거절·오류) · **`heavy`(기본, + `HEAVY_GET` 경로)** · `all`.

#### 모듈 공개 함수 (`llmwiki/reqledger.py`)

```python
open(token, *, kind, origin, user, role, via, ip, agent, label, method, path, client_token="") -> None
update(token, **fields)      # 티켓이 대기열 위치·lock_mode·limit_s·request_id 를 덧붙인다
close(token, *, status, http=None, code="", ms=None, queue_wait_s=None,
      request_id=None, run_id="", stage="", db_wait_ms=None, error="") -> None
span(token=None, **open_kwargs)   # contextmanager: open → 예외를 상태로 분류 → close 보장
read(**filters) -> Tuple[List[dict], str]   # 메모리 색인 우선, 모자라면 파일 역순 스캔 (cursor 반환)
one(token) -> Optional[dict]                # 두 줄을 접어 한 건 + 타임라인
stats(days=7) -> dict                       # 상태별·종류별·창구별 집계, 드롭 카운터, 디스크 사용량
prune() -> dict                             # keep_days + max_mb
```

내부: `queue.SimpleQueue` + **데몬 writer 스레드 1개** + `flush_ms` 배치 + 날짜별 파일 + 메모리 색인(`deque(maxlen=live_rows)`).
`status` 는 `queued|running|done|error|cancelled|timeout|rejected|unknown` 8종으로 고정한다(화면 필터·집계가 이 목록에 묶인다).

#### 후킹 지점 — 정확히 5곳 (빠짐없이 덮기)

티켓 안에만 넣으면 티켓 **이전**에 죽는 요청(차단·점검 모드·413·인증 실패·잘못된 JSON)이 빠진다.

| # | 자리 | 무엇을 보장하나 |
|---|---|---|
| 1 | `web/server.py` `do_GET`·`do_POST`·`do_DELETE`·`_mcp` 를 `reqledger.span()` 으로 감싼다 | **HTTP 로 들어온 모든 것이 정확히 1건.** 인증 실패·413·400·500 포함 |
| 2 | `reqmgr.RequestManager.ticket()` | 대기열 위치·`lock_mode`·`limit_s`·`request_id` 를 `update()` 로 덧붙이고, **`except Rejected` 에서도 반드시 close**(§4.1-C) |
| 3 | `reqmgr.install_cli_publisher()` | CLI·MCP stdio 가 **다른 프로세스**에서 같은 폴더에 쓴다(파일 append 라 프로세스 간 안전) |
| 4 | `web/server.py` `_start_job.runner()` | **백그라운드 잡의 종료 상태**(§4.1-G — 지금 sweep·trial·precompute·fusion 은 `requests` 에 아무것도 안 쓴다) |
| 5 | `scheduler` 작업 실행부 | 예약 작업도 같은 목록에 |

**키는 서버가 만든다.** `reqmgr` 의 `r-<uuid12>` 를 원장 키로 쓰고, 클라이언트가 보낸 `progress_token` 은
`client_token` 필드에 **참고로만** 담는다. 이유: 지금 `ticket(token=…)` 은 클라이언트 값을 그대로 키로 쓰는데
(`web/server.py:1772-1773`), 중복 토큰이 오면 `self.active[tok]` 가 덮어써져 한 건이 목록에서 사라진다(§4.1-J).

#### 파일 형식과 접는 규칙

- 파일: `data/ledger/req-YYYY-MM-DD.jsonl`, 한 줄 = 한 사건(`open`/`update`/`close`), 줄마다 `token`.
- 읽기: 같은 `token` 의 줄을 시간순으로 접는다. `close` 가 없으면 **`unknown`(중단)** 으로 보여 준다
  — 서버가 죽었거나 아직 실행 중이라는 뜻이고, **사라지지는 않는다**(§4.1-I).
- 한 줄이 깨져 있으면(디스크 가득 등) 그 줄만 건너뛰고 나머지는 읽는다 — JSONL 을 고른 이유의 절반이 이것이다.

### 3.3 화면 — **요청 중심의 통합 화면** (요청 3의 산출물)

> 사용자 요청 원문: *"모든 요청에 대해서 list-up 되고, 누르면 진행 상태를 볼 수 있는 창"*,
> *"요청 프로파일 · 진행 중 작업 · 로그 · 질의 로그 — **이런 것과 위 요청 포함해서 한눈에** 볼 수 있는 게 필요하다"*,
> *"서버로의 모든 요청에 대해서 list-up 되고, 클릭하면 상세 정보를 볼 수 있는 형태로 구성"*

#### 3.3.0 문제 — 지금은 한 요청을 보려면 화면 네 곳을 돌아야 한다

Observability 의 탭 7개 중 넷은 **같은 사건을 원천별로 쪼개 놓은 것**이다.

| 탭 | 원천 | 한 요청에 대해 답하는 것 |
|---|---|---|
| 진행 중 작업 | `progress._LIVE` + `reqmgr` (메모리) | 지금 어느 단계? |
| 요청 프로파일 | `requests` 테이블 + 보관 파일 | 단계별로 몇 ms? 설정은? |
| 질의 로그 | `query_log` 테이블 | 뭐라고 물었고 뭐라고 답했나? 피드백은? |
| 로그 | `logs/*.log` | 그때 무슨 경고가 났나? |

**화면이 데이터 원천을 기준으로 갈려 있다.** 그런데 사람이 던지는 질문은 "이 요청 왜 이래?" 하나다.
지금은 그 하나를 답하려고 탭 넷을 오가며 `token`·`request_id`·`run_id` 를 **눈으로 맞춰야** 한다.
게다가 **거절·시간초과·중단은 네 곳 어디에도 없다**(§4.1).

#### 3.3.1 결정 — Observability 를 **요청 중심**으로 재구성한다

탭을 하나 더 늘리지 않는다. **「요청」 탭 하나가 네 화면을 흡수하고, 나머지는 성격이 다르므로 남긴다.**

| 지금 (7탭) | 바뀐 뒤 (5탭) |
|---|---|
| 요청 프로파일 · 진행 중 작업 · 질의 로그 | → **「요청」**(첫 탭·기본 진입) 안의 **뷰 프리셋**으로 흡수 |
| 로그 | 그대로 — 요청 단위가 아니라 **줄 단위**(빌드·워처·시스템 경고 포함) |
| 서버 모니터 | 그대로 — **집계·제한 편집**(개별 요청이 아님) |
| 시스템 · 규모 | 그대로 — 색인 규모·용량 추정 |
| 콘솔 (CLI) | 그대로 — 도구 |

기존 주소(`#observability/requests` · `/activity` · `/qlog`)는 `core.js` 의 `TAB_ALIAS`(이미 있는 장치)로
**요청 탭의 해당 프리셋으로 보낸다** — 북마크와 문서 링크가 깨지지 않는다.

> **왜 흡수인가**: 탭을 더하면 "요청을 볼 수 있는 곳" 이 다섯 곳이 된다. 사용자가 말한 "한눈에" 와 정반대다.
> **왜 로그·서버 모니터는 남기나**: 로그는 요청에 속하지 않는 줄(빌드 단계·워처·디스크 경고)이 절반이고,
> 서버 모니터는 개별 요청이 아니라 **집계와 설정 편집** 화면이다. 억지로 합치면 둘 다 나빠진다.

#### 3.3.2 화면 구성 — 3단

```
┌─ ① 상태 스트립 + 시간 밀도 그래프 ─────────────────────────────┐
│  실행 12 · 대기 5 │ 최근 15분: 완료 143 / 거절 8 / 오류 2 / 중단 0 │
│  슬롯 6/8 (질의 5·검색 1) · 대기열 5/128 · 쓰기락 없음 · writer 0 │
│  ▁▂▃█▇▅▃▂▁▁▂▅█▆▃▁  ← 시간축 막대(색=상태). 드래그하면 그 구간으로 │
├─ ② 목록 (뷰 프리셋 + 필터 + 표) ───────────────────────────────┤
│ [전체][진행 중][문제만][질의][느린 것][내 요청]   기간▾ 상태칩… │
│ 상태 시각 종류/창구 사용자 라벨 대기 소요 단계 결과 🔗          │
├─ ③ 상세 패널 (행 클릭 → 아래 펼침) ────────────────────────────┤
│ A 개요 · B 타임라인 · C 실시간 · D 답변/근거 · E 단계 프로파일    │
│ F 로그 · G 포렌식 · H 환경·설정 · I 같은 시각의 요청             │
└───────────────────────────────────────────────────────────────┘
```

**① 시간 밀도 그래프가 "한눈에" 의 실체다.** 가로축 시간, 막대 높이 = 요청 수, 색 = 상태.
장애 구간(빨강 덩어리)·버스트·조용한 구간이 **그림으로** 보이고, 드래그하면 그 구간으로 필터가 걸린다.
숫자 목록만으로는 "언제 몰렸나" 를 볼 수 없다.

**② 뷰 프리셋 = 흡수한 탭들.** 프리셋은 필터 + 열 구성의 묶음이다.

| 프리셋 | 필터 | 열 구성 | 대체하는 탭 |
|---|---|---|---|
| **전체** | 없음 | 표준 | (신규) |
| **진행 중** | status ∈ running·queued | 단계·진행률·ETA·중지 버튼 | 진행 중 작업 |
| **문제만** | status ∈ rejected·timeout·error·cancelled·unknown | 결과 코드·사유 강조 | (신규 — **디버깅 기본 화면**) |
| **질의** | kind=query | 질문·답변 요약·👍👎·groundedness | 질의 로그 |
| **느린 것** | ms ≥ `slow_request_ms` | 단계별 비중 막대 | (신규) |
| **내 요청** | user=나 | 표준 | Ask › 🕘 내 지난 요청 |

필터·프리셋·선택된 행은 **URL 해시에 저장**한다 — 링크로 동료에게 그대로 보낼 수 있어야 디버깅 도구다.

**표준 열**: 상태 배지 · 시각(상대) · 종류/창구 · 사용자(역할) · 라벨 · **대기 s** · **소요 ms** ·
단계(끝났으면 마지막 단계) · 결과(HTTP + 사유 코드) · 🔗 원천 배지(📄 프로파일 · 💬 질의 · 📜 로그 · 🔍 포렌식 중 **있는 것만** 점등).
실행 중·대기 중은 맨 위 고정, 그 아래 최신순, cursor 기반 무한 스크롤.

#### 3.3.3 상세 패널 — 네 원천을 한 곳에, 접어서

행을 누르면 아래에 펼쳐진다. **처음에는 A·B·C 만 그리고, 나머지는 펼칠 때 가져온다**
(`ACTIVITY_DETAIL.md` §2 의 교훈: 전부 받으면 60KB, 필요한 것만 받으면 17KB).

| 섹션 | 내용 | 원천 |
|---|---|---|
| **A 개요** | 상태·사용자/역할·창구·IP·시각·대기·소요·결과 코드 | 원장 |
| **B 타임라인** | `접수 ─ 대기 4.2s ─ 실행 98.1s [sync│expand 8.7│검색 4.1│rerank 6.0│answer 75.3│claim 3.0│기록 0.6] ─ done` | 원장 + trace |
| **C 실시간** | 실행 중일 때만: 현재 단계·진행률·ETA·LLM 대기(모델/경과)·대기열 순번 · **■ 중지** | `/api/progress/<token>` |
| **D 답변·근거** | 답변 요약(접기)·인용·근거 수·groundedness·판정 · **👍👎 피드백** · 캐시 여부 | `query_log` + `requests.result` |
| **E 단계 프로파일** | 단계별 워터폴(ms·토큰·SQL 수)·설정 스냅샷·원본 JSON · **⟲ 그 단계부터 재실행** | `requests.trace` (+보관 파일) |
| **F 로그** | `run_id` 로 거른 로그 줄 (경고·LLM 실패·잠금 경고) | `logs/*.log` |
| **G 포렌식** | 근거를 못 찾았다면 진단·소견·제안 | `forensics` |
| **H 환경·설정** | preset/overrides · 락 모드 · 시간 제한 vs 실제 · **DB 쓰기 대기 ms**(§1.6-F) · 본문 크기 · User-Agent | 원장 |
| **I 같은 시각의 요청** | **그 요청이 실행되던 동안 서버에 함께 있던 요청들** (겹친 시간·상태·소요) | 원장 |

**상태별로 무엇을 먼저 펼치나** — 패널이 알아서 고른다.

| 상태 | 자동으로 펼치는 섹션 | 그 화면이 답하는 질문 |
|---|---|---|
| 실행 중 | C · B | 지금 어디? 얼마나 더? |
| 완료 | D · B | 그래서 뭐라고 답했나 |
| **느림** (≥ slow_request_ms) | B · **I** | 어디에 시간을 썼나 — **그리고 옆에 누가 있었나** |
| **거절** | A · **I** | 왜 거절됐나 · 그때 서버가 얼마나 차 있었나 |
| **시간초과** | B · F | 어느 단계에서 끊겼나 · 제한값은 얼마였나 |
| 오류 | F · A | 무슨 예외인가 (`ref` 클릭 → 로그) |
| **중단(unknown)** | A | close 가 없는 이유 — 서버 재시작 시각과 대조 |

**I(같은 시각의 요청)가 이 화면을 디버깅 도구로 만드는 부분이다.** "이 질의가 왜 98초 걸렸나" 의 답이
그 요청 안에 없고 **옆에서 같이 돌던 7건**에 있을 때가 많다. 지금은 그걸 볼 방법이 아예 없다.

#### 3.3.4 합치는 열쇠 — 원장 한 줄이 **조인 테이블**이 된다

네 원천을 잇는 키는 이미 다 있다. 다만 **채워지는 시점이 제각각이라 빠진다.**

| 잇는 키 | 실측 채움률 (2026-09-23) |
|---|---|
| `requests.run_id` | **2028/2028 (100%)** ✔ |
| `forensics.request_id` | **1136/1136 (100%)** ✔ |
| **`query_log.request_id`** | **48/69 (70%)** ✘ — 컬럼 추가(09-19) 이후 기준. 전체로는 48/790 |

`query_log.request_id` 는 `log_query()` **뒤에** `set_query_request_id()` 로 **나중에 UPDATE** 하는데,
그 UPDATE 가 잠금에 걸리면 `_warn_locked` 가 삼킨다(`store.py:1280-1288`). **30%가 비어 있는 것 자체가 §1.2 잠금 문제의 흔적이다.**

> **그래서 원장은 `token` · `request_id` · `run_id` · `query_id` 를 close 시점에 한 번에 기록한다.**
> 나중에 채우는 키는 반드시 빠진다는 것을 위 수치가 보여 준다. 원장 한 줄이 **네 화면의 조인 테이블** 역할을 하고,
> 상세 패널은 그 키로 각 원천을 **필요할 때만** 부른다.

#### 3.3.5 API

| 경로 | 반환 |
|---|---|
| `GET /api/ledger?view=&from=&to=&status=&kind=&origin=&user=&min_ms=&http=&q=&limit=&cursor=` | `{rows, cursor, summary, histogram, scope, can_all}` — `summary` 는 ① 스트립, `histogram` 은 시간 밀도 막대 |
| `GET /api/ledger/<token>?sections=overview,timeline,answer,trace,logs,forensic,env,concurrent` | 섹션만 골라 받는다(지연 최소화) |
| `GET /api/ledger/export?…&format=csv\|jsonl` | 현재 필터 결과 내보내기 (전체 조회 권한 필요) |

#### 3.3.6 권한

기본 **내 것만**, 전체는 기존 작업 권한 `requests all` 을 그대로 쓴다(새 권한을 늘리지 않는다).
IP·User-Agent 는 `monitor.show_user_to_viewer` 규칙을 따른다. 내보내기와 **I(같은 시각의 요청)** 는 전체 조회 권한이 필요하다
— 남의 요청 목록이 드러나기 때문이다(권한이 없으면 건수만 보여 준다).

### 3.4 설정 (`server.json` 새 `ledger` 절)

| 키 | 기본 | 뜻 |
|---|---|---|
| `ledger.enabled` | `true` | 끄면 아무것도 쓰지 않는다 |
| `ledger.dir` | `data/ledger` | 원장 폴더 (`data/…` 상대 경로는 `data_dir` 아래) |
| `ledger.keep_days` | `30` | 보존 기간(일). 0 = 지우지 않음 |
| `ledger.max_mb` | `512` | 폴더 총량 상한. 넘으면 오래된 파일부터 삭제 |
| `ledger.include_get` | `heavy` | `none` \| `heavy` \| `all` |
| `ledger.queue_max` | `10000` | writer 큐 상한. 넘으면 드롭하고 카운터를 센다 |
| `ledger.flush_ms` | `200` | writer 가 모아서 쓰는 간격 |
| `ledger.live_rows` | `500` | 목록을 빠르게 그리기 위한 메모리 색인 크기 |
| `ledger.sample_stage` | `true` | 종료 시 마지막 단계 이름을 함께 기록 |

`server.json` 이라 admin 이 Web/CLI 로 **즉시** 바꿀 수 있다. 기본값은 `setup/server.example.json` 에도 **명시**한다.

> **기각한 대안**
> - *`requests` 테이블 확장*: DB 잠금이 원인인 사건을 DB 에 적어야 해서 자기모순. `keep_requests` 가 잘라 버리기도 한다.
> - *`monitor.history_size` 확대*: 메모리라 재시작에 사라지고, 30명이면 500건이 몇 분 분량이다.
> - *`logs/audit.jsonl` 에 합치기*: audit 는 **권한 사건**의 감사 기록이다. 질의 수명 기록을 섞으면 감사 추적이 묻힌다.
> - *OpenTelemetry/외부 APM*: 표준 라이브러리만 쓴다는 이식성 원칙(폐쇄망 반입)에 어긋난다.

---

## 4. 요청 4 — "일부 query 가 기록 없이 사라진다" (재현 분석)

### 4.1 코드로 특정된 원인 — **9가지** (2026-09-23 2차 조사에서 4 → 9로 늘었다)

처음에는 A~D 넷으로 봤는데, "원인이 더 다양할 가능성은 없나" 는 물음에 따라 다시 훑어 **다섯 개를 더 찾았다.**
특히 **E 는 자동으로 일어나고 시간차가 있어서, "시간이 좀 흐르면 사라져버려" 를 가장 잘 설명한다.**

| # | 경로 | 어떤 요청이 사라지나 | 지금 활성? |
|---|---|---|---|
| **A** | `query_engine.run()` 은 **성공 경로에서만** `log_request()` 를 부른다(`query_engine.py:546-551`; 캐시 183 · 사전계산 195) | 시간 초과·취소·LLM 최종 실패로 죽은 질의 | ✔ |
| **B** | `store.log_request()` 가 `sqlite3.OperationalError` 를 **삼키고 0 을 돌려준다**(`store.py:1104-1110`) | **§1.2 의 잠금에 걸린 질의** — 답은 갔는데 기록은 없다 | ✔ |
| **C** | 거절(429/503/403/413)은 **메모리 카운터만** 올린다(`reqmgr.py:545-566`) | 대기열 초과·속도 제한·점검 모드로 못 들어온 요청 | ✔ |
| **D** | `embed_query` 의 `except Exception: pass`(`retrieval.py:260-261`) | 잠금 실패가 **로그에도 안 남아** 원인 추적이 끊긴다 | ✔ |
| **E** | **스냅샷 복원이 `db.sqlite3` 를 통째로 교체**한다(`snapshots.py:80-85` → `evolve._restore`). 그 파일에 `requests`·`query_log`·`forensics`·`episodes` 가 들어 있으므로 **기록이 스냅샷 시점으로 되감긴다.** 그리고 이것이 **자동으로 일어나는 자리가 있다** — `evolve.apply_proposal()` 은 적용 후 평가가 나빠지거나 예외가 나면 `_restore(pipe, snap)` 한다(`evolve.py:341-353`) | **적용·리빌드·평가가 도는 수 분 동안 처리된 모든 요청** | 지금 이 PC 는 **비활성**(`evolve_auto_apply=false`, schedule 전부 disabled). **사내 배포에서 켜면 활성**이고 수동 `evolve apply`·`snapshot restore` 로도 일어난다 |
| **F** | `reset logs` 가 `LOG_TABLES`(query_log·requests·forensics·episodes·trials·embed_runs·evolution_log)와 **`data/requests` 보관 폴더를 함께** 지운다(`reset.py:45-46`) | 전부. **DB 행과 보관 파일이 동시에** 사라져 복구 수단이 없다 | 수동 |
| **G** | 백그라운드 잡(**sweep·trial·precompute·fusion**)은 `log_request` 를 **아예 부르지 않는다**(grep 확인). 결과는 `_JOBS`(메모리, 끝난 것 **최근 50개**)에만 있다(`web/server.py:2331-2333`) | 그 잡들의 실행·실패·취소 전부 | ✔ |
| **H** | `log_request` 는 `UnicodeEncodeError` 도 함께 삼킨다(`store.py:1106`) | 짝 없는 서러게이트가 섞인 질의 — **잠금과 무관하게** 기록이 통째로 빠진다 | ✔ |
| **I** | 서버 재시작·프로세스 종료 | 진행 중이던 요청. `progress._LIVE`·`reqmgr.history` 는 메모리뿐이라 흔적이 없다 | ✔ |
| ~~J~~ | ~~`progress_token` 충돌~~ | **조사했으나 원인 아님** — 클라이언트가 `'q-' + Date.now(36) + Math.random(36)[6자]` 로 만들어(`ask.js:191`) 충돌 확률이 사실상 0. 다만 **토큰이 클라이언트 제공이고 서버가 검증하지 않아** 중복 토큰을 보내면 `reqmgr.active[tok]` 가 덮어써진다 → **원장 키는 서버가 만든다**(§3.2) | 설계에 반영 |

**증거**: `logs/error.log` 2026-09-20 06:57:58
`요청 프로파일 기록을 건너뜁니다 (DB 쓰기 잠금): database is locked` — B 가 실제로 일어났다.

여기에 **"시간이 흐르면 사라진다"** 의 두 번째 층이 겹친다.

| 층 | 수명 | 실측 |
|---|---|---|
| `progress._LIVE` | 끝난 뒤 **5분**(`progress.py:24`) | — |
| `reqmgr.history` | 최근 **500건**, 메모리 | — |
| `_JOBS`(잡) | 끝난 것 **최근 50개**, 메모리 | — |
| `requests` 테이블 | `keep_requests` **2000행** | id **1901~3928 = 2028행** — 이미 잘려 나가고 있다 |

#### 참고 — 스냅샷은 무엇이고 언제 만들어지나 (원인 E 를 읽기 위한 배경)

**무엇**: `data/snapshots/<이름>/` 에 **`db.sqlite3` · `rules.json` · `wiki/` · `config.json`** 을 통째로 복사한 **복원 지점**
(`snapshots.py:24-52`). 파괴적 작업을 되돌리기 위한 것이다. `meta.json` 에 태그·시각·행 수가 들어간다.

**주기적이지 않다. 사건 기반이다.**

| 계기 | 태그 | 자동? | 지금 |
|---|---|---|---|
| `build --reset` / `reset_index()` 직전 | `auto:reset` | **✔ 자동**(`snapshot=True` 기본, `pipeline.py:431`) | 활성 |
| **`evolve apply` 로 제안을 적용하기 직전** | `p<pid>_<ts>` (evolve 전용 경로, `evolve.py:388-404`) | **✔ 자동** | 활성(수동 apply 시) |
| `snapshot restore` 직전 | `auto:before-restore` | ✔ 자동(`cli.py:2974`) | 활성 |
| 스케줄 작업 `nightly-snapshot` | `auto:nightly`, keep 5 | 주기(매일 0시) | **`enabled: false` — 꺼져 있다** |
| 사람이 `snapshot create` | `manual` | 수동 | — |

**정리**: `snapshots.prune(keep)` 은 태그가 `auto:` 인 것만 최신 `keep` 개 남긴다. **수동 스냅샷은 지우지 않는다.**
evolve 전용 스냅샷(`p<pid>_…`)은 `auto:` 태그가 아니라 prune 대상이 아니고 evolve 가 직접 관리한다(`evolve.py:413`).

**실측(2026-09-23)**: `data/snapshots/` 에 1개 — `20260920-213027_auto-reset`, **500.2MB**.
즉 **스냅샷 하나가 DB 전체 크기**다. 주기 스냅샷을 켜면 `keep` 개수 × 500MB 가 디스크에 쌓인다(포팅 문서에 적을 것).

**원인 E 와의 연결**: 이 복사본에 `requests`·`query_log`·`forensics`·`episodes` 가 **함께** 들어 있다.
스냅샷은 *색인* 을 되돌리려는 것인데 파일이 하나라 *관측 기록* 까지 같이 되감긴다. 이것이 설계 문제의 핵심이다.

#### 원인별로 원장이 덮는가

| 원인 | 원장이 덮나 | 추가로 필요한 수정 |
|---|---|---|
| A·B·C·H·I | **✔ 완전히** (open 을 먼저 쓰므로 무엇이 일어나도 "들어왔다" 는 남는다) | §4.3 의 A·B·C 로 `requests` 쪽도 함께 고친다 |
| D | ✔ (`db_wait_ms` 로 기록) | §1.2 0-B |
| G | **✔** (후킹 지점 4 — `_start_job.runner()`) | — |
| **E** | **✘ 덮지 못한다** — 원장은 `data/ledger/` 라 DB 교체와 무관하지만, **되감긴 것은 DB 쪽 기록**이다 | **스냅샷 복원 전후를 원장에 사건으로 남긴다**(누가·언제·어느 스냅샷으로·되감긴 `requests` id 구간). 그러면 "왜 그 시각 이후 기록이 없나" 에 답이 나온다 |
| **F** | **✘ 기본으로는** — `reset logs` 는 DB 와 보관 파일을 지운다 | `reset logs` 의 삭제 대상에서 **원장을 제외**하고(별도 옵션 `--ledger` 로만), 초기화 사실 자체를 원장에 남긴다 |

### 4.2 재현 하네스 (신규 `tools/verify/verify_request_ledger.py`)

기존 `verify_soak.py`/`verify_monkey.py` 는 "서버가 버티는가" 를 보고 **기록의 완전성은 보지 않는다**.

**시나리오 A (주) — 빌드 완료 후, 질의만 동시에** ← 사용자가 지적한 상황

```
1. 빌드를 먼저 끝내고 serve 기동 (toggles.auto_build=false — 빌드·워처 없음)
   max_parallel_reads=4 · queue_max=8 · queue_timeout_s=5 로 낮춰 거절을 일부러 유발
2. 40건 동시 질의. **질문은 전부 서로 다르게** 만든다 (→ embedding_cache 미스가 겹치도록: §1.2 재현 조건)
3. 클라이언트가 보낸 요청 목록(토큰·질문·HTTP 응답·왕복 ms)을 기준선으로 저장
4. 대조:  보낸 N vs requests M  → 지금은 M < N (사라진 수 = 회귀 기준선)
          보낸 N vs 원장 L      → 구현 후 L == N (정확히 1건씩, 종료 상태 포함)
5. 같이 재는 것: 질의당 커밋 수 · fsync 수 · **쓰기 잠금 대기 ms** · 임베딩 호출 수 · p50/p95
   → 0-A/0-D · A · B 적용 전후 비교표를 문서에 싣는다
6. 서버 재시작 후 다시 조회 → 원장은 그대로, 진행 중 작업은 비어 있음(설계대로)
```

**시나리오 B (부)** — 빌드 직후 herd(§1.4): 빌드 완료 직후 20건 동시 → 첫 질의의 캐시 적재 ms 와 나머지의 대기 ms.
**시나리오 C (부)** — 빌드와 겹친 질의: 이번 범위는 아니지만 같은 코드 경로이므로 회귀 확인용.
**시나리오 D** — `--capacity`(§2.5 포화점 측정).

**시나리오 E — 되감기 (원인 E, 2차 조사에서 추가)**

```
1. 질의 10건을 처리해 requests 에 행이 쌓인 것을 확인 (id 구간 기록)
2. 스냅샷 생성 → 질의 10건 더 → snapshot restore (또는 evolve apply 가 회귀로 자동 롤백되게 유도)
3. 확인:  requests 에서 2단계의 10건이 **사라졌는지**  ← 지금은 사라진다 (원인 E 재현)
          원장에는 20건이 **그대로 있는지**            ← 구현 후 그래야 한다
          원장에 '복원' 사건과 되감긴 id 구간이 남았는지
```

**시나리오 F — 초기화 (원인 F)**: `reset logs` 후 원장이 남아 있는지 + 초기화 사실이 원장에 기록됐는지.
**시나리오 G — 잡 (원인 G)**: sweep·trial 을 돌리고 중간에 취소 → 지금은 아무 기록이 없고, 구현 후에는 원장에 `cancelled` 로 남아야 한다.
**시나리오 H — 깨진 문자열 (원인 H)**: 짝 없는 서러게이트(`\ud83d`)가 섞인 질의 → 기록이 빠지지 않아야 한다.
**시나리오 I — 재시작 (원인 I)**: 질의 5건이 실행 중일 때 서버를 죽이고 재기동 → 원장에 `unknown(중단)` 5건이 남아야 한다.

**합격 기준**: 원장 누락 0 · 중복 0 · 드롭 카운터 0 · `database is locked` 0 ·
상태 미상은 **시나리오 I 에서만** 나타나고 그 수가 정확히 일치.

### 4.3 원인 자체도 고친다 (원장만으로 덮지 않는다)

- **A**: `QueryEngine.run()` 을 `try/except/finally` 로 감싸 실패·취소도 `requests` 행을 남긴다(`error` 채움, 부분 trace).
- **B**: `log_request()` 실패 시 재시도하고, 그래도 안 되면 **보관 파일 + 원장**에는 반드시 남긴다. 경고에 `token`·`run_id` 포함.
- **C**: 거절·시간 초과를 원장 + `logs/error.log`(WARNING) 양쪽에. `reqmgr.stats()` 카운터와 원장 집계를 대조 가능하게.
- **D**: §1.2 의 0-B.
- **E**: `snapshots.restore()` / `evolve._restore()` 가 **복원 전후에 원장 사건**을 남긴다 — 누가·언제·어느 스냅샷·되감긴 `requests` id 구간. 또한 `evolve.apply_proposal` 의 자동 롤백은 **되감기 전에 `requests`·`query_log` 의 신규 행을 떠서 복원 후 다시 넣는 것**을 검토한다(스냅샷은 색인을 되돌리는 것이지 *관측 기록*을 되돌리려는 것이 아니다 — 이 둘이 한 파일에 있는 것이 설계 문제다).
- **F**: `reset logs` 의 삭제 대상에서 **원장을 뺀다**(별도 옵션으로만 지우게). 초기화 사실 자체도 원장에 남긴다.
- **G**: `_start_job.runner()` 에 원장 close 를 달고(후킹 4), sweep·trial·fusion·precompute 도 `log_request` 를 부를지 별도 검토(잡 결과는 각자 파일/테이블이 있으므로 **원장으로 충분할 수 있다**).
- **H**: `safe_text()` 와 같은 정화를 `log_request` 입구에도 적용해 **삼키지 말고 정화해서 기록**한다.
- **I**: 원장의 `open` 줄이 덮는다(별도 수정 없음).

---

## 5. 요청 5 — 세 창구 정합과 문서

### 5.1 정합 표에 추가할 줄 (`tools/verify/verify_surface_align.py` CAPS)

| 기능 | CLI | Web | MCP |
|---|---|---|---|
| 요청 원장(모든 요청 목록·상세) | `ledger list\|show\|stats` | `GET /api/ledger` `GET /api/ledger/<token>` | `wiki_requests(source="ledger")` |
| 종류별 동시/대기/속도 한도 | `server limits set concurrency.classes.…` | `GET/POST /api/admin/server` | `""` (설정 변경은 MCP 에 두지 않는다 — 기존 원칙) |
| 운영 프로파일(full/lean/production) | `server mode [set <profile>]` | `POST /api/admin/server {action:"mode"}` | `""` (〃) |

- MCP 는 **도구를 늘리지 않고** `wiki_requests` 에 `source`(`profile`\|`ledger`)·`status` 필터를 더한다.
  이유: 붙은 LLM 이 쓸 질문("전에 이거 물어본 적 있나", "왜 실패했나")이 같은 도구 안에서 답이 되고, 도구 수를 늘릴수록 도구 선택이 나빠진다.
- CLI `ledger` 는 **서버 없이도** 파일을 직접 읽는다(포팅·사후 분석용). 실행 중 서버 조회는 `server ledger` 로 HTTP.

### 5.2 문서 산출물

| 문서 | 내용 |
|---|---|
| **`docs/PORTING_0923.md`** (신규, **요청 5의 "포팅 가이드" — 문서 1개로 전부 파악**) | 다른 환경에서 **다른 LLM 이나 사람이 이 문서 하나만 읽고** 옮길 수 있게 자립적으로 쓴다: 무엇이 문제였나 → 무엇을 바꿨나 → 바뀐 파일 전수 → **설정 키 전수 표**(기본값·의미·3가지 변경 경로) → 단계별 적용 절차(명령 포함) → **검증 절차와 기대 출력** → 문제 해결 → **되돌리기** → 마이그레이션(0-F 캐시 병합) → 규모별 권장값. 각 항목에 **상태 열**(적용 완료 / 구현 예정)을 달아 진행에 따라 갱신한다 |
| **`docs/REQUEST_LEDGER.md`** (신규) | 원장 기능 자체의 상세: JSONL 스키마 전체 · 화면 사용법(뷰 프리셋·상세 9섹션) · CLI/MCP · 권한 · 보존/정리 · 문제 해결. 포팅 문서가 요약하고 여기를 가리킨다 |
| `docs/WEB_UI.md` | **Observability 탭 재구성**(7 → 5) 과 「요청」 탭 사용법 |
| `docs/CONCURRENCY.md` | §3 종류별 한도 절 · §8 "질의가 하는 쓰기와 잠금 보유 시간" 절 · §9 문제 해결 · §10 검증 |
| `docs/BRINGUP_GUIDE.md` | §3.5 에 새 설정 키 전부와 확인 절차, `docs/PORTING_0923.md` 링크 |
| `docs/ACTIVITY_DETAIL.md` | §3 한계 항목을 "원장이 덮는다" 로 갱신 |
| `docs/REQUEST_HISTORY.md` | 원장과의 역할 구분(프로파일 = 그때의 답 / 원장 = 모든 요청의 수명) |
| `docs/SURFACE_ALIGNMENT.md` · `WEB_UI.md` · `CLI_FLOWS.md` · `MCP.md` · `CONFIG_REFERENCE.md` · `TUNING.md` | 새 명령·탭·도구 인자·설정 키 |
| `README.md` | §0 문서 색인 + 기능/CLI 요약 |
| `setup/server.example.json` · `setup/config.example*.json` | 새 절(`classes`·`ledger`·`mode`)과 키를 **기본값 그대로 명시**. 지금 빠져 있는 `mcp`·`collab` 절도 함께 채운다 |

---

## 6. 구현 순서

| 단계 | 내용 | 얻는 것 |
|---|---|---|
| **0** | §1.2 **0-A~0-F** (커밋 한 줄 · 조용한 실패 제거 · 미커밋 세션 경고 · 중복 임베딩 통일 · 캐시 쓰기 비동기화 · 모델 이름 정규화) | **잠금 98초 → 1ms, 질의 −5초, −65MB.** 가장 싸고 가장 크다 |
| **1** | 재현 하네스 `verify_request_ledger.py` (시나리오 A~D). **0 적용 전 기준선 → 적용 후** 수치 확보 | 요청 4 "재현 확인" 의 답 |
| 2 | `llmwiki/reqledger.py` (writer 스레드 · JSONL · 회전/보존 · 메모리 색인 · 읽기 API) + 단위 테스트 | 원장 코어 |
| 3 | **후킹 5곳 연결**(§3.2): HTTP 진입점 4개 · `reqmgr.ticket()`(거절 포함) · CLI/MCP publisher · **`_start_job.runner()`**(원인 G) · 스케줄러 | **모든 요청이 남는다** |
| 3b | **되감기·초기화 대응**(원인 E·F): `snapshots.restore`/`evolve._restore` 가 복원 사건을 원장에 남기고, `reset logs` 의 삭제 대상에서 원장을 뺀다 | 자동 롤백으로도 안 사라진다 |
| 4 | `GET /api/ledger` · `/api/ledger/<token>` · `/api/ledger/export` (권한·필터·섹션) | 원장 API |
| 4b | **Observability 재구성**(§3.3.1): 「요청」 탭 신설 + 뷰 프리셋 6종으로 **요청 프로파일·진행 중 작업·질의 로그 흡수**(7탭 → 5탭), 기존 주소는 `TAB_ALIAS` 로 프리셋 리다이렉트, 상태 스트립·시간 밀도 그래프·상세 패널 9섹션 | **요청 3** |
| 5 | CLI `ledger` · `server ledger` · MCP `wiki_requests(source=ledger)` + 정합 표 갱신 | 요청 5(정합) |
| 6 | 종류별 한도(§2.2) + `set_limits` 3단계 키 + 서버 모니터 편집 UI | 요청 2 |
| 7 | §1.6 **A → B → C → D → E → F → G**. 각 단계마다 시나리오 A 를 돌려 수치를 남긴다 | 요청 1 나머지 |
| 8 | 운영 프로파일 `mode.profile`(§1.7) | 결정 사항 ⑥ |
| 9 | (필요하면) §1.6 **H** 비동기 writer — 7의 측정이 요구할 때만 | 선택 |
| 10 | 문서 일괄(§5.2) + `setup/` 예시 + `verify_surface_align`·`verify_web`·`verify_cli`·`verify_timeouts` 통과 | 요청 5(문서·포팅) |

**단계 0 만으로 요청 1 의 증상이 사라질 것으로 본다. 단계 3 까지면 요청 4 의 "사라짐" 이 없어진다.**

---

## 7. 이미 적용한 것 / 검증

### 7.1 적용 완료 (2026-09-23)

| 항목 | 내용 | 파일 |
|---|---|---|
| `max_parallel_reads` | **8 유지** (결정 사항 ⑤ — 로컬 Ollama 가 병목이라 올리면 나빠진다) | 변경 없음 |
| `queue_max` | **64 → 128** | `llmwiki/reqmgr.py` DEFAULTS · `server.json` · `setup/server.example.json` · `docs/CONCURRENCY.md` §0·§3 · `docs/BRINGUP_GUIDE.md` §3.5 |
| **0단계 전부 (0-A~0-F)** | §1.2 의 조치. 아래 실측 참고 | `store.py` · `retrieval.py` · `precompute.py` · `query_engine.py` · `pipeline.py` · `config.py` · `cli.py` |
| 회귀 테스트 | 7개 신설 | `tests/test_db_lock_0923.py` |
| 문서 | §8.0 신설(잠금의 진짜 원인과 전후 수치) · 문제 해결 3줄 · 검증 1줄 | `docs/CONCURRENCY.md` · `docs/REQUEST_LEDGER.md` |

**0단계 실측 (2026-09-23, 같은 조건 · WAL + busy_timeout)**

| 항목 | 전 | 후 |
|---|---|---|
| 질의 진행 중 **다른 연결의 쓰기** | 3,310ms 대기 후 `database is locked` | **0.89ms 성공** |
| 질의 뒤 `in_transaction` | `True` (잠금 보유) | **`False`** |
| 같은 문자열의 임베딩 호출 | 최대 3회 | **1회** (나머지 캐시 적중) |
| 모델 표기 변경 시 | 전체 캐시 미스 → 재임베딩 | **적중** (정규화 + 두 이름 조회) |
| 커밋 없이 세션 이탈 | 조용히 rollback | **경고 + `pool_info().uncommitted_exits`** |

전체 테스트 **773개 통과**(회귀 없음), 신규 7개 통과.

**아직 하지 않은 것**: `maintenance cache_merge` 실제 실행(살아 있는 DB 의 16,654행 ≈ 65MB 회수).
서버가 떠 있어 쓰기가 길어질 수 있으므로 **승인 후 한가할 때** 돌린다. dry-run 으로 대상만 확인해 두었다.

확인: `python -c "from llmwiki import reqmgr; print(reqmgr.load_config()['concurrency'])"` →
`queue_max = 128`, `max_parallel_reads = 8`.

> **주의 — 대기열은 슬롯을 대신하지 못한다**: 슬롯 8 · 질의 100초면 128번째는 이론상 1,600초를 기다리므로
> 실제로는 `queue_timeout_s`(120초)에서 503 `queue_timeout` 이 된다. 이번 변경의 효과는
> **"즉시 거절"이 "잠깐 기다렸다가 거절"로 바뀌는 것**이고 처리량은 그대로다. 처리량은 §2.5 의 세 레버로 올린다.
> **실행 중인 서버에는 재시작 또는 `python -m llmwiki server limits set concurrency.queue_max=128` 로 반영한다.**

### 7.2 검증 계획

| 무엇 | 어떻게 |
|---|---|
| 잠금 보유 시간 · 중복 임베딩 · 기록 완전성 | `tools/verify/verify_request_ledger.py` (신규, 시나리오 A~D) |
| 요청 격리·락·대기열·속도 제한·취소 | `python -m unittest tests.test_concurrency_0915` |
| 자원 누수(연결 회계·스레드 복귀) | `python -m unittest tests.test_resource_limits` + **원장 writer 스레드 복귀 검사 추가** |
| 세 창구 정합 | `python tools/verify/verify_surface_align.py` |
| Web·CLI 전 경로 | `verify_web.py` · `verify_cli.py` |
| 시간 제한·재시도 | `verify_timeouts.py` |

---

## 8. 위험과 대응

| 위험 | 대응 |
|---|---|
| **0-A 가 커밋을 늘려 오히려 느려진다** | 커밋 횟수는 같다(어차피 뒤에서 한 번 커밋됐다). 바뀌는 것은 **보유 시간**뿐이다. 그래도 §1.6-A(묶음 트랜잭션)와 상호작용하므로 A 적용 후 시나리오 A 를 다시 돌린다 |
| `synchronous=NORMAL` 의 내구성 | WAL 에서 **DB 손상은 없다**. 정전 시 최근 커밋 몇 건이 날아갈 수 있고 그 내용은 색인·관측 기록이라 재빌드로 복구된다. `FULL` 로 되돌리는 키를 남긴다 |
| 0-F 마이그레이션이 캐시를 망친다 | 병합은 **읽기 전용 대조 후** 한쪽만 남기는 방식. 실패해도 최악은 "다시 임베딩" 이고 색인은 그대로. 스냅샷 후 실행 |
| 원장이 디스크를 먹는다 | `keep_days` + `max_mb` 이중 상한, 기본 `include_get=heavy`. `ledger stats` 로 증가율 확인 |
| writer 스레드 병목/누수 | 큐 상한 + 드롭 카운터 + `server status` 에 writer 상태 노출 |
| 비동기 기록으로 순서가 뒤바뀐다 | 단일 writer 스레드라 **전역 순서 보장** |
| 종류별 한도를 잘못 넣어 질의가 굶는다 | `classes.*.max_parallel` 이 `max_parallel_reads` 보다 크면 경고 + 절단. 화면에 "질의 상한 6 / 전체 8 → 검색 예약 2" 를 문장으로 표시 |
| 개인정보(질문 원문·IP)가 원장에 남는다 | 라벨 160자 절단(기존 `safe_text`), IP·에이전트는 `show_user_to_viewer` 규칙 적용, 보존 기본 30일. 더 엄격하면 `mode.profile=production` |
