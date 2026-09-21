# QA 강화 — 사각지대 점검과 결함 수정 (2026-09-19)

> 대상: 이 시스템을 사내 서버로 올려 운영할 사람, 그리고 다음에 이 코드를 손볼 LLM.
> 목적: 기존 테스트가 **보지 않던 자리**를 찾아, 다수 사용자 환경에서 실제로 사람을 잃는 결함을 고치는 것.
> 관련 문서: [SECURITY.md §6.2](../../SECURITY.md) · [CONCURRENCY.md §8.1~8.2](../../CONCURRENCY.md) · [MCP.md §2.01](../../MCP.md) · [TUNING.md](../../TUNING.md)

## 0. 한 장 요약

기존 테스트(단위 413개 + 검증 하니스 21종)는 "기능이 **동작하는가**" 를 아주 촘촘히 본다.
이번 점검은 네 가지 다른 질문을 던졌다.

| 영역 | 던진 질문 | 찾은 결함 | 상태 |
|---|---|---|---|
| ① 창구 정합 | 세 창구가 **같은 것을 돌려주는가** (존재하는가가 아니라) | 2건 | 고침 |
| ② RAG 품질 | 모델이 **잘못 답할 때** 그것이 드러나는가 | 4건 | 고침 |
| ③ 동시성·자원 | 부하가 끝난 뒤 **제자리로 돌아오는가** | 2건 | 고침 |
| ④ 보안 | 권한 없는 문서가 **근거로 새지 않는가** | 4건 | 고침 |
| **2차 (§9)** | 위 수정들도 **한 문 앞의 자물쇠가 아닌가** (별도 리뷰) | 6건 | 고침 |

새로 추가된 자동 테스트: **85항목** (`test_doc_acl` 24 · `test_prompt_injection` 15 · `test_context_budget` 14 ·
`test_rag_edge_cases` 15 · `test_resource_limits` 9 · `test_surface_consistency` 8) **+ 2차 20항목**
(`test_privilege_paths` — 함수가 아니라 **경로**를 센다, §9.1).
전체: 단위 448개 통과.

핵심 관점 하나: **찾은 결함은 대부분 "조용히 잘못되는" 것들**이었다. 예외도 안 나고, 로그도 안 남고,
화면에는 그럴듯한 답이 보인다. 그래서 기존 테스트가 못 봤고, 운영에서도 한참 뒤에야 드러난다.
수정의 절반은 동작을 바꾼 것이고, 나머지 절반은 **그 일이 일어났다는 사실을 보이게 만든 것**이다.

---

## 1. 영역 ① — 세 창구(Web·CLI·MCP)가 같은 것을 돌려주는가

### 왜 기존 점검으로 부족했나

`tools/verify/verify_surface_align.py` 는 "기능마다 CLI 명령·Web 경로·MCP 도구가 **존재하는가**" 를 본다.
존재는 정렬의 절반이다. 나머지 절반은 **같은 값을 돌려주는가** 이고, 그쪽은 조용히 어긋난다.

### 결함 1-A · MCP `wiki_query` 기본 모드에 구조화 결과가 없었다

- **증상**: `output_mode` 가 `fused`/`context` 일 때만 `structuredContent` 를 줬다. 정작 가장 많이 쓰는
  기본(answer) 경로에서는 붙은 LLM 이 **한국어 산문**에서 인용과 판정을 되짚어야 했다.
- **왜 위험한가**: 붙은 LLM 이 인용 번호를 잘못 읽으면 근거 없는 문장을 근거 있는 것처럼 인용한다.
  다른 도구는 전부 구조화 결과를 주므로 이 도구만 예외라는 것도 알기 어렵다.
- **수정**: 기본 모드에도 `answer`·`citations[]`·`evidence`·`groundedness`·`result_type`·`request_id` 등을 준다.
  **필드 이름은 Web `/api/query` 의 `result` 와 같게** 맞췄다 — 창구마다 다른 이름을 외우게 하지 않는다.
  (`llmwiki/mcp.py` · 계약 표는 [MCP.md §2.01](../../MCP.md))

### 결함 1-B · `Pipeline.query(overrides=…)` 가 인자를 조용히 버렸다

- **증상**: 함수가 `overrides` 를 **받아 놓고 쓰지 않았다**. 넘긴 쪽은 설정이 먹은 줄 알지만 아무 일도 안 일어난다.
- **왜 위험한가**: 값을 조용히 버리는 인자는 디버깅할 단서를 남기지 않는다. "왜 이 설정이 안 먹지" 를
  파이프라인 전체에서 찾게 된다. (이 프로젝트가 반복해서 겪은 실패 모양이다 — 설정만 있고 배선이 없는 기능.)
- **수정**: 주면 안쪽 `request_scope` 로 그 호출 동안만 적용한다. 바깥 범위는 그대로 둔다.

### 이후 고정한 것 — `tests/test_surface_consistency.py` (8항목)

같은 질문을 **결정적인 조건**(mock LLM · hash 임베더 · 캐시 off)에서 세 창구로 보내고,
"달라도 되는 것"과 "달라서는 안 되는 것"을 갈라서 본다.

| 항목 | 같아야 하나 | 이유 |
|---|---|---|
| 답변 본문 | ✔ | 같은 컨텍스트 → 같은 답 |
| 컨텍스트 근거 목록과 순서 | ✔ | 근거가 다르면 같은 시스템이 아니다 |
| `[C#] → chunk_id` 매핑 | ✔ | 화면에서 본 번호와 LLM 이 받은 번호가 같아야 한다 |
| 판정(verdict)·`result_type` | ✔ | 창구마다 다르면 품질 지표를 믿을 수 없다 |
| 채널 검색 결과·`mode` | ✔ | 한 엔진(`retrieval.channel_search`)이다 |
| 질의 해부(`normalized`·`tokens`·`keywords`) | ✔ | 한 함수(`querydebug.inspect_query`)다 |
| `request_id`·`query_id`·시각·ms | ✘ | 실행마다 다르다 |
| 표현(텍스트 표 vs JSON) | ✘ | 읽는 대상이 다르다 |

---

## 2. 영역 ② — RAG 품질과 LLM 신뢰성

### 결함 2-A · 모델 입력 창을 아무도 보지 않았다

- **증상**: `models.json` 에 `context_k`(모델 창)가 있는데 **어디서도 쓰이지 않았다**.
  컨텍스트 상한은 사람이 정한 `context_max_chars` 뿐이었다.
- **왜 위험한가**: 창이 작은 모델(예 `context_k: 8`)에 `deep_research` 프리셋(20000자)을 걸면
  프롬프트가 **조용히 잘린 채** 호출된다. 뒤쪽 근거가 통째로 사라지지만 모델은 그 사실을 말해 주지 않는다.
  결과는 "인용 번호는 맞는데 내용이 빈" 답이고, 원인을 짚을 단서가 없다.
- **수정**: `models_catalog.context_budget()` 이 상한을 정한다.

      여유 토큰 = 창 − 출력 상한(answer_max_tokens) − 예비(context_budget_reserve_tokens)
      상한(글자) = min(설정값, 여유 토큰 × context_chars_per_token)

  창을 모르면(카탈로그 밖 모델·`context_k: 0`) **설정값을 그대로** 쓴다 — 모른다고 근거를 줄이지 않는다.
  같은 id 가 여러 provider 에 있으면 **가장 좁은 창**을 쓴다(잘리는 쪽이 더 나쁘다).
  줄어들면 그 이유가 trace 의 `context` 단계에 문장으로 남는다.
- **새 설정**: `context_chars_per_token`(2.0) · `context_budget_reserve_tokens`(2000) — [TUNING.md](../../TUNING.md).
  기본 2.0 은 한글·혼합 문서에서 토큰을 **넉넉히 잡는**(= 안전한) 값이다. 영문 위주면 3~4 로 올려 더 넣을 수 있다.

### 결함 2-B · 상한에 걸리면 거기서 멈췄다 (순위가 아니라 길이 때문에 탈락)

- **증상**: 컨텍스트 조립 루프가 상한을 처음 넘기는 청크에서 `break` 했다.
  2위에 큰 청크 하나가 있으면 **3위 이하는 자리가 남아 있어도 통째로 빠졌다**.
- **수정**: 남은 자리가 `context_min_fit_chars`(400) 이상이면 **잘라서** 넣고(`truncated` 로 보고),
  아니면 건너뛰고 다음 순위를 계속 본다. 잘라 넣은 것과 아예 빠진 것을 **구분해서** 보고한다 —
  진단이 다르기 때문이다(상한을 늘릴 일인가, 청크를 줄일 일인가).

### 결함 2-C · 마침표 뒤의 인용을 잃었다

- **증상**: LLM 은 `문장입니다. [C1]` 처럼 쓰는 일이 아주 흔하다. 문장 분리에서 `[C1]` 이
  8자 미만의 별개 조각이 되어 **통째로 버려졌다**.
- **왜 위험한가**: 두 방향으로 틀린다. 제대로 인용한 답이 '인용 없음(uncited)' 으로 깎여 groundedness 가
  낮게 나오고(품질 지표를 믿을 수 없게 된다), 반대로 **없는 번호를 인용해도 검사 대상이 되지 않았다**.
- **수정**: 인용 표시만 남은 조각은 **같은 줄의 앞 문장**에 합친다.

### 결함 2-D · 없는 인용 번호를 검증하지 않았다

- **증상**: 모델이 `[C99]`(존재하지 않는 근거)를 인용해도 `citation_precision` 이 **1.0** 으로 나왔다.
  "인용이 있다" 는 것만으로 맞는 것처럼 취급된 셈이다.
- **수정**: 인용 번호가 실제 근거 목록에 있는지 본다. 없으면 `verdict: "fabricated_citation"`,
  요약에 `bad_citations` 건수와 `max_citation`(실제 존재하는 최대 번호)을 남기고 Web 의 claim 요약에 표시한다.
  위 예는 이제 `groundedness 0.0 · citation_precision 0.0 · bad_citations 1` 로 나온다.

### 이후 고정한 것 — `tests/test_rag_edge_cases.py` (15항목)

두 방향에서 본다.

- **A. 입력이 이상하다** — 빈 질문·공백·구두점만·아주 긴 질문·FTS5 구문 문자(`"unclosed`, `AND OR NEAR`,
  `a*(b)`, `; DROP TABLE`)·한영 혼합·조각 질의·색인에 없는 주제.
  원하는 것은 **터지지 않고 정직하게 모른다고 말하는 것**이다.
- **B. 모델이 이상하게 답한다** — 없는 인용·인용 없는 단정·빈 답·프롬프트 되뱉기·답변 LLM 실패.
  원하는 것은 **거짓이 검출되어 표시되는 것**이고, LLM 이 죽어도 이미 찾은 근거는 버리지 않는 것이다.

모델이 잘못 답하는 경우는 진짜 LLM 으로 재현할 수 없다. `MockLLM` 에 환경변수 훅
`LLMWIKI_MOCK_ANSWER`(답변 역할의 출력을 지정한 문자열로 대체)를 추가했다 — 기존 `LLMWIKI_MOCK_FAIL`·
`LLMWIKI_MOCK_DELAY_MS` 와 같은 자리, 기본 동작에는 영향이 없다.

---

## 3. 영역 ③ — 동시성과 자원 한계

### 결함 3-A · 빌려 나간 SQLite 연결을 아무도 세지 않았다

- **증상**: `db_pool_size`(16)는 **놀고 있는** 연결만 제한한다. 빌려 나간 연결 수에는 상한이 없었고
  계측도 없었다. `with store.session():` 을 벗어나지 못하는 경로가 하나라도 생기면 연결이 무한히 늘어난다.
- **왜 위험한가**: 며칠 켜 두면 메모리·파일 핸들이 늘어 "갑자기 느려졌다" 로 나타나는데, 그때는
  원인을 되짚을 숫자가 하나도 없다. 오래 켜 두는 서버에서 무너지는 것은 기능이 아니라 **돌아오지 않는 것들**이다.
- **수정**: `pool_info()` 가 `live`(지금 빌린 수) · `peak_live`(최고 기록) · `created` · `overflow` 를 함께 준다.
  서버 모니터·`/api/admin/server` 에서 보인다. 한가한데 `live` 가 0 이 아니면 누수다.
  그리고 **부드러운 상한** `db_max_live_connections`(64)를 뒀다 — 넘으면 `db_pool_wait_timeout_s`(2초)만큼
  반납을 기다렸다가, 그래도 없으면 **만들어서라도 진행하고** `overflow` 를 센다.

  딱딱하게 막지 않은 이유: 채널 검색처럼 상위 스레드가 연결을 쥔 채 하위 스레드가 연결을 더 쓰는 자리가 있다.
  하드 캡은 거기서 교착이 된다. 정상 운영에서는 64 에 닿지 않으므로 이 값은 **차단 장치가 아니라 경보**다.

### 결함 3-B · 락 획득 순서가 코드에만 있었다

- **증상**: 서로 다른 락을 겹쳐 잡는 자리가 셋 있는데 순서가 문서화되어 있지 않았다.
  다음 사람이 새 락을 중간에 끼우면 교착이 난다.
- **수정**: [CONCURRENCY.md §8.2](../../CONCURRENCY.md) 에 정식 순서를 적었다.

      RequestManager._lock  →  RWLock._cv  →  progress._LOCK  →  Store._pool_lock

  DB 세션은 **가장 안쪽**에서만 연다. 새 락은 이 사슬의 **끝**에 붙인다.

### 이후 고정한 것 — `tests/test_resource_limits.py` (9항목)

연결 회계(중첩 세션 이중 계수 없음·병렬 최고치·재사용률), 부드러운 상한(요청을 죽이지 않음·기다렸다 재사용·
`close()` 가 대기 스레드를 깨움), 짧은 soak(질의 24건 뒤 `live`=0, 스레드 기준선 복귀, 진행 표시 비축적).

---

## 4. 영역 ④ — 보안 (프롬프트 인젝션 · 문서 접근 격리)

상세 설계는 [SECURITY.md §6.2](../../SECURITY.md). 여기에는 **무엇이 사각지대였는지**만 적는다.

### 결함 4-A · 검색 경로에 "누가 묻는가" 가 전달되지 않았다

역할(viewer/class3/…/admin)은 지금까지 **무엇을 실행할 수 있는가**만 정했다. **무엇을 읽을 수 있는가**는
아무도 정하지 않았고, `_do_query()` 는 사용자 정보를 티켓·로그에만 썼다. 색인된 문서가 하나라도 있으면
익명 viewer 도 인사·보안 문서를 근거로 받아 볼 수 있었다. **RAG 에서 검색은 곧 읽기다.**

→ `Pipeline.actor`(스레드 로컬) + `request_scope(actor=…)` 로 신분을 검색까지 보내고,
`doc_acl` 단계(부스트 직후·리랭크 직전)에서 역할로 근거를 거른다.

### 결함 4-B · 막을 자리가 한 군데가 아니었다

질의 근거만 막으면 나머지로 샌다. 네 출구를 **같은 판정기**로 막았다.

| 출구 | 막지 않았을 때 무엇이 새나 |
|---|---|
| 질의 답변의 근거 | 비공개 문서 내용이 답변에 인용된다 |
| 채널 검색 디버그 | `per_channel` 의 snippet 으로 본문이 그대로 보인다 |
| 문서 열람(`/api/doc`·`/api/chunk`·MCP `wiki_doc`) | 검색을 막아도 doc_id 를 알면 전문이 열린다 |
| 후보·유사 문서 목록 | 제목·ID 만으로도 "무엇이 있는지" 가 샌다 |

### 결함 4-C · 예외 하나에 접근 제어가 통째로 열렸다 (fail-open)

`doc_acl` 단계가 `chunks[cid].get("doc_id")` 를 불렀는데, 파이프라인은 자리에 따라 청크를 `dict` 로도
**`sqlite3.Row`** 로도 넘긴다. `Row` 에는 `.get` 이 없어 `AttributeError` 가 났고, 단계 전체가
예외 처리로 빠져 **전부 통과**시켰다. 화면에는 "doc_acl: 오류" 라는 작은 표시만 남는다.

→ `Filter.doc_id_of()` 가 두 모양을 모두 받고, 그래도 실패하면 `chunk_id` 로 되짚어 **보수적으로 거른다**.
문서 열람은 규칙이 켜져 있는데 판정에 실패하면 **막는다**. 접근 제어에서 '예외 하나에 열려 버리는' 길은 두지 않는다.

### 결함 4-D · MCP 는 항상 admin 이었다

`/mcp` 핸들러가 `request_scope()` 를 신분 없이 열어 `pipe.actor` 기본값(admin)이 적용됐다.
API 키 하나로 전 문서가 열렸다. → `actor=dict(_actor_of(user), origin="mcp")` 로 키의 역할을 넘긴다.

### 프롬프트 인젝션

사내 위키는 누구나 문서를 올리고 MCP 로 외부 글도 들어온다. 그 본문이 **그대로** 답변 프롬프트의
컨텍스트 구획에 들어가므로, 문서 하나로 모든 질의의 답변을 조종하려 시도할 수 있다.
`llmwiki/ctxguard.py` 가 구획(`<<</C1>>>`)·역할(`system:`)·인용(`[C7]`) 흉내 조각의 **표시를 바꾸고**
본문을 펜스로 감싼다. **내용은 지우지 않는다** — 보안이 근거 품질을 깎으면 안 되기 때문이다.
시도한 흔적은 `injection_marks` 로 trace 에 남는다.

---

## 5. TC 시나리오 (사람이 손으로 확인할 때)

자동 테스트는 아래 표의 **판정 기준**을 그대로 구현한 것이다. 새 환경에 올린 뒤 손으로 확인할 때 이 표를 쓴다.

### TC-1. 창구 정합

| TC | 절차 | 기대 |
|---|---|---|
| TC-1.1 | 같은 질문을 Web Ask · `python -m llmwiki query "<질문>" --json` · MCP `wiki_query` 로 각각 | 답변 본문·근거 목록·`[C#]` 매핑·`result_type`·판정이 같다 |
| TC-1.2 | MCP `wiki_query` 응답의 `structuredContent` 확인 | `answer`·`citations[]`·`evidence`·`request_id` 가 있고, `citations[].n` 이 Web 의 인용 번호와 같다 |
| TC-1.3 | 채널 검색을 세 창구로 (`search fts,vector --mode and` / Ask › 채널 검색 / `wiki_search`) | `rows[].chunk_id` 순서와 `mode` 가 같다 |
| TC-1.4 | 질의 해부를 세 창구로 (`inspect` / Ask › 디버그 / `wiki_inspect`) | `normalized`·`tokens`·`keywords` 가 같다 |
| TC-1.5 | `overrides.output_mode=fused` 를 세 창구로 | 모두 `result_type=candidates_fused` |

### TC-2. RAG 품질

| TC | 절차 | 기대 |
|---|---|---|
| TC-2.1 | `context_k` 가 작은 모델(예 로컬 7B)을 answer 역할로 두고 `--preset deep_research` 질의 | trace 의 `context` 단계에 `budget_limited=true` 와 줄어든 이유가 보인다. 프롬프트가 잘리지 않는다 |
| TC-2.2 | 아주 긴 청크가 상위에 오는 질의 | `context` 단계에 `truncated_chunks` 가 보이고, **뒤 순위 근거가 계속 들어간다**(`dropped` 만 있고 인용이 1건뿐이면 이상) |
| TC-2.3 | 색인에 없는 주제를 묻는다 | 판정 `insufficient` + `result_type` 에 insufficient 가 드러난다. 그럴듯한 문장을 지어내지 않는다 |
| TC-2.4 | 한·영 혼합 문서를 한국어로/영어로 각각 묻는다 | 둘 다 그 문서를 찾는다 |
| TC-2.5 | 검색창에 조각으로 입력(`링버퍼 오버런`) | 완전한 문장과 같은 문서를 찾는다 |
| TC-2.6 | FTS 구문 문자를 넣는다 (`"unclosed`, `a*(b)`, `AND OR NEAR`) | 500 이 아니라 정상 응답 |
| TC-2.7 | `LLMWIKI_MOCK_ANSWER='문장. [C99]'` 로 질의(mock 프로바이더) | claim 요약에 `bad_citations ≥ 1`, `citation_precision 0.0` |
| TC-2.8 | `LLMWIKI_MOCK_FAIL=timeout` 으로 질의 | 500 이 아니라 근거를 유지한 추출식 답 + LLM 실행 보고 |

### TC-3. 동시성·자원

| TC | 절차 | 기대 |
|---|---|---|
| TC-3.1 | 동시 질의 30건 (`verify_monkey.py` 또는 부하 스크립트) | 전부 응답(429 는 `Retry-After` 와 함께), 5xx 없음 |
| TC-3.2 | 부하 뒤 서버 모니터의 `db_pool` 확인 | `live` 가 0, `overflow` 가 0 |
| TC-3.3 | 하루 이상 켜 둔 뒤 같은 확인 | `live` 가 여전히 0 (0 이 아니면 세션 누수) |
| TC-3.4 | 전체 리빌드 중 질의 | 정책대로 대기하거나 응답, 진행 표시에 이유가 보인다 |
| TC-3.5 | 긴 질의를 중지 버튼으로 취소 | 즉시 멈추고 서버는 계속 정상 |

### TC-4. 보안

| TC | 절차 | 기대 |
|---|---|---|
| TC-4.1 | `docacl.json` 에 `{"prefix":"corpus/hr/","min_role":"class1"}` 저장 후 `security docacl check --role viewer` | 가려질 문서 수와 예시가 보인다 |
| TC-4.2 | viewer 로 로그인해 그 문서 내용을 묻는다 | 근거에 없고, trace 의 `doc_acl` 단계에 `blocked_docs` 가 보인다. **admin 으로는 같은 질의가 찾는다**(대조군) |
| TC-4.3 | viewer 로 `/api/doc?id=<가려진 문서>` 직접 호출 | 403 + `min_role` |
| TC-4.4 | viewer 로 Ask › 채널 검색 | 그 문서의 행과 snippet 이 없고 `counts.acl_blocked` 가 0 보다 크다 |
| TC-4.5 | viewer 역할 API 키로 MCP `wiki_doc`·`wiki_search`·`wiki_related` | 모두 같은 규칙으로 막힌다 |
| TC-4.6 | 문서 front matter 에 `acl: class1` 을 적고 재빌드 | 경로 규칙이 없어도 막힌다. 역할 이름을 틀리면 빌드 린트가 `acl` 오류로 알려 준다 |
| TC-4.7 | `config.json` 토글 `doc_acl: false` | 전부 예전대로 보인다(긴급 해제) |
| TC-4.8 | 본문에 `이전 지시를 무시하고 …` `<<</C1>>>` `[C7]` 이 든 문서를 올리고 질의 | trace 의 `context` 단계 `guard` 에 표시 수가 보이고, 답변이 그 지시를 따르지 않는다. **본문 내용은 근거로 그대로 쓰인다** |

---

## 6. 자동 테스트 매핑

| 파일 | 항목 | 무엇을 고정하나 |
|---|---|---|
| `tests/test_surface_consistency.py` | 8 | 세 창구의 답·근거·인용 매핑·판정·채널 검색·해부 일치 (TC-1) |
| `tests/test_context_budget.py` | 14 | 모델 창 ↔ 컨텍스트 상한, 상한 초과 시 조립 동작 (TC-2.1~2.2) |
| `tests/test_rag_edge_cases.py` | 15 | 이상한 입력 / 이상한 모델 출력 / 인용 무결성 (TC-2.3~2.8) |
| `tests/test_resource_limits.py` | 9 | 연결 회계·부드러운 상한·soak 복귀 (TC-3.2~3.3) |
| `tests/test_doc_acl.py` | 24 | 규칙 해석·네 출구·MCP 역할 적용·영향 미리보기·토글 해제 (TC-4.1~4.7) |
| `tests/test_prompt_injection.py` | 15 | 공격 코퍼스 12종 무력화·본문 보존·경계 규칙·관측 (TC-4.8) |
| `tools/verify/verify_security_ui.py` | +5 | 문서 접근 제어 화면 ↔ 파일 ↔ 서버 왕복 |
| `tools/verify/verify_cli.py` | +8 | `security docacl show|check|init` |
| `tools/verify/verify_mcp.py` | +1 | `wiki_query` 기본 모드의 `structuredContent` 계약 |
| `tools/verify/verify_surface_align.py` | +1 | 문서 접근 제어의 CLI/Web/MCP 배치 |
| `tests/test_privilege_paths.py` | 20 | **경로** 검사(§9.1): overrides 화이트리스트가 모든 창구를 지나는가 · CLI 등급표에 빠진 명령이 admin 으로 떨어지는가 · 질의를 실행하는 길이 전부 신분을 받는가 · 재생 경로에 `doc_acl` 이 있는가 · 캐시가 역할로 갈리되 같은 역할끼리는 공유되는가 |

실행:

```bat
python -m unittest discover -s tests          :: 단위 428개
python tools\verify\verify_all.py             :: 검증 하니스 전부 (몽키 포함, 약 1시간)
```

---

## 7. 새로 생긴 설정 (전부 파일로, 기본값은 파일에 명시되어 있다)

| 파일 | 키 | 기본 | 뜻 |
|---|---|---|---|
| `docacl.json` | `enabled` · `default_min_role` · `rules[]` · `deny_message` | on · viewer · `[]` | 문서 단위 접근 제어 ([SECURITY.md §6.2](../../SECURITY.md)) |
| `config.json` `toggles` | `doc_acl` | on | 위 규칙 전체 사용 여부(긴급 해제) |
| `config.json` `toggles` | `context_guard` | on | 컨텍스트 인젝션 방어 |
| `config.json` | `db_max_live_connections` | 64 | 빌린 SQLite 연결의 부드러운 상한(0=무제한) |
| `config.json` | `db_pool_wait_timeout_s` | 2.0 | 상한 초과 시 반납 대기 시간 |
| `tuning.json` | `context_chars_per_token` | 2.0 | 모델 창(토큰) → 글자 환산 비율 |
| `tuning.json` | `context_budget_reserve_tokens` | 2000 | 시스템 프롬프트·질문 몫으로 남기는 토큰 |
| `tuning.json` | `context_min_fit_chars` | 400 | 상한에 걸린 근거를 잘라서라도 넣을 최소 남은 자리 |

원본 예시: `setup/docacl.example.json` · `setup/config.example.json` · `setup/tuning.example.json`
(모두 키마다 `_how_*` 설명이 붙어 있다).

---

## 8. 남은 것 (이번에 하지 않은 판단)

정직하게 적어 둔다. 지금 하지 않은 이유가 있고, 필요해지는 조건이 있다.

| 항목 | 지금 하지 않은 이유 | 필요해지는 조건 |
|---|---|---|
| 진짜 토크나이저로 토큰 수 계산 | 표준 라이브러리만 쓰는 방침. `context_chars_per_token` 의 보수적 환산으로 창 초과는 막힌다 | 토큰 과금을 정확히 맞춰야 할 때 |
| 그래프 엔티티 이름의 접근 제어 | 엔티티는 문서 본문이 아니라 추출된 이름이라 별도 축이다. 완전히 가리려면 색인에서 빼는 편이 맞다 | 엔티티 이름 자체가 비밀일 때(고객사명 등) |
| 락 순서 런타임 검증기 | 계측을 넣는 위험이 지금 얻는 것보다 크다. 순서를 문서로 고정하고 혼합 부하 테스트로 교착을 잡는다 | 락을 새로 추가하거나 중간에 끼울 때 |
| 리랭크 프롬프트의 창 예산 | 답변 컨텍스트와 달리 후보 수·청크 길이로 이미 상한이 있다 | 후보를 크게 늘리는 설정을 쓸 때 |
| API 키별 rate limit | 프록시에서 거는 편이 맞다([MCP.md](../../MCP.md)) | 외부 LLM 이 많아 키 단위 과금·제한이 필요할 때 |

---

## 9. 2차 점검 — 별도 리뷰에서 나온 것 (같은 날)

§1~§4 를 끝낸 뒤, **이 코드를 처음 보는 리뷰어**(별도 모델 세션)에게 전체 코드베이스를 읽히고 분석 리포트를
받았다 — [CODEBASE_REVIEW_0919.md](CODEBASE_REVIEW_0919.md). 거기서 나온 심각도 '높음' 항목을 **전부 코드로 직접
재확인**한 뒤 고쳤다. 리뷰어의 주장 중 확인되지 않은 것은 고치지 않았다.

이 2차 점검의 교훈은 하나로 모인다. **§1~§4 에서 고친 것들도 "한 문 앞의 자물쇠" 였다.**

| # | 결함 | 왜 1차에서 못 봤나 | 수정 |
|---|---|---|---|
| 9-A | **MCP 가 `overrides` 화이트리스트를 타지 않는다** — `mcp._query_with` 가 도구 인자를 그대로 `request_scope` 로 넘긴다. `/mcp` 는 read 등급이라 `anonymous_role` 이 켜져 있으면 **무인증으로** `openai_base_url` 을 바꿔 `.env` 의 PAT 를 외부로 보낼 수 있다 | 화이트리스트를 **Web 계층**(`web/server.py`)에 두었다. Web 의 테스트는 통과했고, MCP 에 같은 길이 있다는 것을 아무도 세지 않았다 | 목록과 검사를 `auth.filter_overrides` 로 옮기고 Web·MCP 가 **같은 함수**를 쓰게 했다. MCP 는 호출자 역할(`pipe.actor`)로 거른다 — stdio(로컬 운영자)는 admin 이라 예전과 같다 |
| 9-B | **Web 콘솔이 admin 게이트의 뒷문** — `classify_cli` 의 fallback 이 `edit`(class2)이고 `schedule` 이 어느 표에도 없었다. `POST /api/schedule` 은 admin 인데 `POST /api/cli {"argv":"schedule add …"}` 는 class2 로 통과하고, 스케줄 동작의 `python`·`cli` 타입이 **서버 프로세스 권한 임의 실행**에 이른다 | 분류표의 **빠진 항목**은 테스트가 없다. "표에 있는 것이 맞나" 만 봤지 "표에 없으면 어디로 떨어지나" 를 안 봤다 | fallback 을 **admin** 으로 바꾸고(모르는 명령은 가장 높은 등급), `schedule`·`server`·`optimize`·`inspect`·`rerun` 을 명시적으로 분류했다. `optimize` 는 `--out` 이 임의 경로에 파일을 쓰므로 그때만 admin |
| 9-C | **`actor` 가 질의를 실행하는 네 경로에서 빠졌다** — `/api/query/rerun`·잡(eval·sweep·precompute)·`/api/cli`·재생 경로. `Pipeline.actor` 기본값이 admin 이라 **조용히 열린다** | §4 에서 "네 출구" 를 막았는데, 그 출구들은 **근거가 나가는 문**이었다. 질의를 **실행하는 문**은 따로 세지 않았다 | 네 자리 모두 `actor=_actor_from_client(client)` 를 넘긴다. 재생 경로(`_replay_retrieval`)에는 `doc_acl` 단계를 넣고, 근거가 빠지면 저장된 컨텍스트를 **다시 조립**한다(저장본 본문을 그대로 쓰면 의미가 없다) |
| 9-D | **지난 요청을 남의 것도 꺼낼 수 있다** — `request_id` 는 순차 정수라 추측이 아니라 **열거**다. `/api/request` 는 소유자를 검사했지만 `/api/rerun`·`/api/query/rerun`·`/api/query_trace`·`/api/analysis` 는 안 했다 | 소유자 검사가 **한 곳에만** 있었다(같은 모양의 반복) | 공용 판정 `_not_my_request()` 를 만들어 다섯 경로 전부에 걸었다. 판정 실패 시 **막는** 쪽으로 기운다 |
| 9-E | **캐시 키에 신분이 없다** — 캐시가 맞으면 `doc_acl` 단계는 **실행조차 되지 않는다**(조기 반환). admin 이 한 번 물은 답이 그대로 viewer 에게 돌아간다. `answer_cache` 는 재시작을 넘어 영속 | 두 토글이 기본 off 라 테스트 경로에 없었다. 그러나 30명 환경에서 토큰을 아끼려고 **가장 먼저 켜는 손잡이**다 | 키에 `visibility_key()` 를 넣었다. **사용자별이 아니라 가시성 등급(역할)별**로 나눈다 — 사용자별로 쪼개면 30명 환경에서 적중률이 1/30 이 되어 캐시를 켜는 의미가 사라진다. 규칙이 없으면 구획이 하나(`all`)라 예전과 같은 적중률 |
| 9-F | **`/api/logs?file=` 경로 순회** — `os.path.join(로그폴더, file + ".log")` 에 검증이 없어 폴더 밖의 `.log` 를 읽을 수 있었다. GET 은 read 등급 = 익명 | 멍키 테스트는 무작위 문자열을 넣지 `../` 를 겨냥하지 않는다 | 이름을 `[A-Za-z0-9._-]{1,64}` 로 제한하고 정규화 후 폴더 안인지 다시 확인한다 |

### 9.1 그래서 무엇을 바꿨나 — 테스트의 성격

리뷰어의 가장 뼈아픈 지적은 결함 목록이 아니라 이것이었다.

> "이 저장소의 실패 패턴은 늘 같다(설정만 있고 배선 없음 / 한 출구만 막음 / 인자를 조용히 버림).
> 그런데 검증기는 **이름의 존재**만 보고, `test_doc_acl` 은 **테스트가 직접 `actor` 를 주입**해 배선을 건너뛴다.
> 즉 고쳐도 **다음에 또 같은 모양으로 깨진다**."

맞는 지적이다. 그래서 `tests/test_privilege_paths.py`(20항목)는 **함수가 아니라 경로를 센다**.

- 위험한 overrides 키가 **모든 역할**에서 거부되는가 (값 검사)
- Web·MCP 가 **같은 필터를 부르는가** (배선 검사 — 원본 코드에서 호출 자리를 센다)
- `request_scope()` 를 **신분 없이** 여는 자리가 몇 개인가 (늘어나면 실패)
- `cli.py` 의 모든 서브커맨드가 등급표에 **적혀 있는가** (빠지면 admin 으로 떨어지고 테스트가 알려 준다)
- 재생 경로에 `doc_acl` 단계가 **있는가**
- 캐시 키가 역할로 갈리는가, 그러면서 **같은 역할끼리는 공유되는가**

이 테스트들이 무의미하지 않다는 것도 확인했다 — MCP 의 필터 호출을 되돌리면 실제로 실패한다(변이 검사).

### 9.2 리뷰에서 확인했으나 이번에 고치지 않은 것

| 항목 | 왜 미뤘나 |
|---|---|
| GET 이 티켓(속도 제한·동시 수·대기열)을 건너뛴다 | 사실이다. 다만 고치면 폴링 엔드포인트가 대기열에 들어가 화면이 멈출 수 있다 — 별도 설계가 필요하다. `CONCURRENCY.md §3` 의 흐름도가 이 점에서 부정확하다는 것을 먼저 고쳐야 한다 |
| `classify_api` 의 GET 기본값이 `read`(fail-open) | 새 GET 엔드포인트가 자동 공개된다. 기본값을 admin 으로 바꾸면 기존 화면이 대거 막히므로, 경로 목록을 전수 분류한 뒤에 바꾸는 편이 안전하다 |
| 질의마다 `threading.Lock` 이 영구 누적 | 누수 속도가 매우 느리고(질의당 하나) `test_resource_limits` 가 연결 쪽은 이미 본다. 다음 회차 |
| 저장형 XSS 2곳 | 둘 다 기본 off 토글에 의존한다. 별도 회차에서 프런트 전반을 한 번에 |
| 함수 길이(`cli._run_cmd` 982줄 등) | 기능 변경 없는 리팩터링은 이번 회차의 위험을 키운다 |

## 9.4 3차 — 운영 중 보고된 두 건 (같은 날)

사용자가 화면을 쓰다가 보고한 것 둘. 둘 다 "동작은 하는데 **쓸 수 없는**" 종류였다.

### 9.4-A 표가 세로로 한 글자씩 쌓여 읽을 수 없었다 (자동 매핑 제안)

- **증상**: Settings › 모델·프로바이더의 **자동 매핑 제안** 표에서 머리글은 화면 전체에 퍼지고 본문은
  왼쪽 240px 에 뭉쳐, 모델 이름이 `a/n/t/h/r/o/p/i/c` 처럼 세로로 쌓였다.
- **원인**: 전역 규칙 두 줄의 **상호작용**이었다.

      table{display:block;overflow-x:auto}    /* 넓은 표가 페이지를 밀지 않게 */
      table>tbody{display:table;width:100%}   /* 그 안에서 표 모양을 유지하려고 */
      .tbl-wrap>table{display:table}          /* 감싼 표는 wrapper 가 스크롤하므로 되돌림 */

  세 번째 줄이 `table` 은 되돌렸는데 **`tbody` 는 그대로 `display:table`** 이었다. 그러면 바깥 표 안에서
  tbody 가 **중첩 표**가 되어 익명 행/칸 하나에 통째로 들어간다. `<thead>` 가 없는 표는 표 하나가 폭을
  채워 멀쩡해 보이지만, `<thead>` 가 있으면 머리글 5칸은 **바깥 표의 열**로 퍼지고 본문은 **익명 칸 하나**
  안에서 제 내용 크기로만 그려진다 — 화면에서 본 그대로다.
- **수정**: `.tbl-wrap>table>tbody{display:table-row-group;width:auto}` (`style.css`).
  영향 범위는 `.tbl-wrap` + `<thead>` 조합 **6개 표**(자동 매핑·카탈로그 테스트·규칙 효과·규칙 유형 등).
- **재발 방지**: `verify_responsive.py` 가 매번 **대조 표본**(같은 모양의 표)을 잠깐 심어 머리글과 첫 본문 행의
  열 위치·폭이 일치하는지 잰다. 문제의 표들은 **버튼을 눌러야** 그려져서 탭 스캔만으로는 헛돌기 때문이다
  — 실제로 처음 넣은 검사는 무의미했고(수정을 되돌려도 30/30 통과), 대조 표본을 넣은 뒤에야 30개 전부 실패로 잡혔다.

### 9.4-B timeout·retry 가 세 창구에서 같은 뜻인가

기존 `verify_timeouts.py` 는 "서버가 재시도하고 버틴다" 는 **동작**만 봤다. 운영자가 실제로 묻는 것은
"그 값을 어디서 바꾸나, 셋이 같은 뜻인가" 다. 확인해 보니 세 가지가 어긋나 있었다.

| 어긋남 | 수정 |
|---|---|
| **CLI 에만 요청 단위 오버라이드가 없었다** — Web·MCP 는 `overrides` 를 보낼 수 있는데 CLI 는 `config set`(모든 사용자에게 영향)뿐이었다 | `query --set llm_timeout=7,llm_retries=1` 추가. 같은 화이트리스트를 지난다 (§[CONCURRENCY §4.1](../../CONCURRENCY.md)) |
| **같은 손잡이가 철자에 따라 권한이 달랐다** — 전역 `llm_circuit_failures` 는 admin 인데 역할 철자 `circuit_failures` 는 누구나 | 역할 철자도 admin 으로. 회로 차단은 "이 질의를 어떻게 찾을까" 가 아니라 **죽은 프로바이더를 계속 때리지 않게 서버를 지키는 장치**다 — 요청자가 자기 요청만 예외로 만들 수 있으면 보호가 아니다 |
| **모르는 키가 조용히 버려졌다** — `--set nope_zzz=1` 이 성공으로 끝났다(`apply_overrides` 가 모르는 키를 무시). admin 은 화이트리스트도 통과하므로 아무 단서가 없었다 | 역할과 무관하게 **400**. 오타는 권한과 관계가 없다 (`overrides.tuning` 이 모르는 튜닝 키를 400 으로 돌려주는 것과 같은 규칙) |
| MCP 기본 모드 응답에 `llm_report` 가 없었다 | `structuredContent.llm_report` 추가 — 붙은 LLM 이 "재시도 몇 번, timeout 몇 초, 무엇으로 대체했는지" 를 산문에서 긁지 않는다 |

**고정**: `verify_timeouts.py` §1.5(10항목) — LLM 을 항상 실패시켜 `llm_report.failures[].max_attempts`
(= 1 + retries)를 재고, 같은 `overrides` 를 **Web·CLI·MCP** 로 보내 세 값이 같은지 비교한다.
회로 차단은 (프로바이더, 모델) 단위로 요청을 넘어 공유되므로, 이 구간에서는 회로를 꺼야 재시도 자체를 잴 수 있다
(회로 차단은 §2 가 따로 본다).

## 9.3 리뷰 리포트 자체

[CODEBASE_REVIEW_0919.md](CODEBASE_REVIEW_0919.md) — 외부 시각의 전체 분석(§0 요약 · §1 강점 · §2 약점 16건 ·
§3 코드 품질 · §4 테스트 사각지대 · §5 성능 · §6 제안 P0~P2 · §7 **하지 말아야 할 것** · §8 확인 못 한 것 ·
§9 문서 드리프트). 이번에 고치지 않은 항목의 근거가 거기 있다.

## 10. 관련 문서

- [SECURITY.md](../../SECURITY.md) §6.2 문서 단위 접근 제어 · 프롬프트 인젝션 · §10 구현 파일
- [CONCURRENCY.md](../../CONCURRENCY.md) §8.1 연결 회계와 부드러운 상한 · §8.2 락 획득 순서
- [MCP.md](../../MCP.md) §2.01 `structuredContent` 계약 · §3 보안 정리(문서 접근 제어 적용)
- [BRINGUP_GUIDE.md](../../BRINGUP_GUIDE.md) §4.4.1 문서 접근 제어 설정 절차 · §3 설정 파일 표
- [TUNING.md](../../TUNING.md) `context_*` 키 (자동 생성)
- [TESTING_GUIDE.md](../../TESTING_GUIDE.md) 변경 영역 → 어떤 테스트를 돌리나
