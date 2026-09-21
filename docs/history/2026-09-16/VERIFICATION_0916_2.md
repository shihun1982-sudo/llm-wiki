# 검증 보고서 — 2026-09-16 (2차) : 요청 이력 · 모델 화면 · 답변 페르소나 · 규칙 확장 · headless 내성 · 협업

> 이 문서는 **다른 환경에서 같은 검증을 다시 돌려 같은 결론에 이르기 위한** 기록이다.
> 무엇을 왜 그렇게 만들었는지는 [IMPLEMENTATION_PLAN_0916.md](IMPLEMENTATION_PLAN_0916.md),
> 같은 날 1차(MCP 종단 검증)는 [VERIFICATION_0916.md](VERIFICATION_0916.md).

## 0. 한 장 요약

| 무엇 | 명령 | 결과 |
|---|---|---|
| 단위 테스트 | `python -m unittest discover -s tests` | **181/181 통과** (98s) — 신규 17건 포함 |
| 스트레스 (30명 동시) | `python -m unittest tests.test_concurrency_0915.StressTest` | **9/9 통과** — 30건 동시 질의 1.1s (avg 53ms · p95 110ms) |
| CLI 전수 | `python tools\verify\verify_cli.py` | **222/222 통과** |
| Web API 전수 | `python tools\verify\verify_web.py` | **285/285 통과** (신규 26항목 포함) |
| MCP 종단 | `python tools\verify\verify_mcp.py --quick` | **87/87 통과** |
| UI 배선 · 브라우저 | `verify_ui_wiring.py` · `verify_browser.py` | **OK** · **OK** (32탭, 콘솔 오류 0) |
| 버튼 전수 (정적 + **동적**) | `python tools\verify\verify_buttons.py` | **90/90 통과** (정적 79 · 동적 11) |
| 무작위 입력 내성 | `python tools\verify\verify_monkey.py` | **RESULT OK** — 1,205 요청 · **500 오류 0 · 서버 생존 · 결함 0** |
| 보안 · 사용자 화면 | `python tools\verify\verify_security_ui.py` | **46/46 통과** (인증 꺼짐 · admin 로그인 · admin 아님 세 가지 상태) — §2.8 |
| 단계 재실행 · 작업 상세 | `python tools\verify\verify_rerun_ui.py` | **20/20 통과** (⟲ 재실행 · 대화상자 레이아웃 · 완료 항목 클릭 · 게시 대화상자) — §2.9 |

**이 회차에 찾아 고친 결함 2건** (검증이 없었으면 놓쳤을 것들) — §2.7.1.

## 1. 이 회차에 구현한 것과 확인 방법

### 1.1 요청 이력과 결과 보관 (사용자 요청 1)

문서: [REQUEST_HISTORY.md](../../REQUEST_HISTORY.md)

| 확인한 것 | 어디서 |
|---|---|
| `requests` 에 `user` 열이 생기고 **내 요청만** 걸러진다 (예전에는 `origin` 에 `"web alice"` 한 문자열이라 불가능) | `RequestHistoryTest.test_user_column_and_filter` |
| 결과가 `data/requests/<yyyy-mm>/req_<id>.json` 으로 보관되고, **DB 행이 잘려도** 그 파일에서 복원된다 | `…test_result_is_archived_and_survives_db_prune` |
| `requests_keep_days` 를 넘긴 보관 파일만 지운다 (0 이면 지우지 않음) | `…test_prune_archive_respects_keep_days` |
| `data/requests` 같은 상대 경로가 `data_dir` 아래로 풀린다 (격리 환경도 따라감) | `…test_requests_dir_resolves_under_data_dir` |
| `GET /api/requests` 가 `{rows, live, scope, me, can_all}` 를 주고, 권한이 없으면 **조용히 내 것만** 준다 | `verify_web` 5항목 |
| 상세 조회가 보관 파일로 폴백하고, 남의 요청은 403 | 〃 |

Ask 탭의 **🕘 내 지난 요청** 은 한 줄을 누르면 **질의를 다시 실행하지 않고** 저장된 결과로 화면을 다시 그린다.
이를 위해 `ask.js` 의 결과 렌더링을 `renderResult(result, trace, q)` 로 분리했다(예전에는 `runQuery` 안에 인라인이었다).

> 설계상 **근거 문단 전문은 보관하지 않는다**(요약 `hits_brief` 만). 요청 하나가 수 MB 가 되고, 코퍼스가 바뀌면
> 어차피 옛 본문이 되기 때문이다. 다시 본 화면의 근거 칸이 그 사실을 알린다.

### 1.2 Settings › 모델·프로바이더 (사용자 요청 2)

**확인한 사실**: 선택 가능한 모델은 이미 `models.json` 카탈로그에 있다(`id·provider·label·roles[]·tags·context_k·enabled`).
화면도 `/api/models` 로 **현재 설정값을 읽어서** 그리고 있었다. 빠진 것은 세 가지였고 모두 고쳤다.

| 문제 | 고친 것 |
|---|---|
| 임베딩 모델은 `datalist` 자동완성, 리랭크 API 모델은 맨 텍스트 입력 → **무엇을 고를 수 있는지 안 보였다** | 카탈로그에 `embed` · `rerank` 절을 두고 **같은 드롭다운**으로 (`ModelCatalogTest`) |
| 설정이 **세 곳에 흩어져** 있었다 — 위쪽 "지금 쓰는 모델" 요약표, 왼쪽 임베딩/엔드포인트 폼, 오른쪽 역할별 표 | **역할별 표 하나로 합쳤다.** 표 이름을 `모델 · 역할 전체` 로 바꾸고 마지막에 **`embed` 행과 `rerank(API)` 행**을 붙여, provider·model·dim·dtype·batch·url·style 을 그 표에서 바로 고친다. 왼쪽 폼에는 "여기서 고칩니다" 안내와 현재 상태만 남겼다(입력칸을 두 군데 두면 어느 쪽이 적용되는지 헷갈린다) |
| 표를 보다가 저장·새로고침하려면 맨 위로 올라가야 했다 | 표 머리글에 **저장 · ↻ 다시 그리기 · ↻ config.json** 을 같이 뒀다. `재시도·타임아웃 열 보기` 는 기본 켜짐 |
| 해시 없이 처음 열면 활성 탭의 loader 가 돌지 않아 **빈/오래된 화면**이 남았다 | `core.js boot()` 이 활성 탭 loader 를 실행 |
| 파일을 서버 밖에서 고치면 화면과 파일이 달라 보였다 | **↻ config.json 다시 읽기** 버튼 + `POST /api/config {"action":"reload"}` (admin, 작업 `config reload`) |

예전 `models.json`(rerank 절 없음)도 파일을 고치지 않고 기본 목록으로 채워 준다.

### 1.3 답변 페르소나 (사용자 요청 3)

`prompts/answer_system.md` + `prompts/answer_guide.md` 를 다시 썼다.

- **읽는 사람**을 먼저 규정한다 — 3GPP 규격과 C/레지스터/DMA/타이밍을 **동시에** 다루는 모뎀 PHY 임베디드 개발자.
  질문의 목적이 (1) 증상 원인 (2) 어디를 고치나 (3) 왜 그렇게 짰나 셋 중 하나임을 명시.
- **근거 누락 방지**: 검증 가능한 문장마다 `[C#]`, 값은 원문 그대로(레지스터·비트·0x·단위·ID·함수명),
  없는 값은 쓰지 말고 "제공된 문서에서 확인되지 않음", **근거에 있는 사실을 빠뜨리지 않는다**.
- **약한 모델 대책**: 지시를 짧은 명령문으로, **출력 뼈대를 그대로 제시**, 예시를 코드펜스 하나로 제한.
- **추출식 답변과 제목을 맞췄다** (`## 핵심 / ## 상세 / ## 근거 / ## 미확인 · 추가 조사 필요`) — LLM 이 실패해
  추출식으로 대체돼도 같은 모양이라 나란히 비교할 수 있다.

파일이 원천이고 `llmwiki/prompts.py` 의 기본값도 같이 바꿨다(새 환경에서 파일이 없어도 같은 프롬프트).

### 1.4 규칙 확장성 (사용자 요청 4)

사용자가 겪은 문제 두 가지를 재현하고 고쳤다.

| 문제 | 원인 | 고친 것 |
|---|---|---|
| **acronym 으로 바뀐 말이 synonym 에 있으면 동작하지 않는다** (`TAT → Turn Around Time → 응답시간`) | 확장이 **1회만** 적용됐다 | 여러 번 접어 적용(fixed point). 상한은 `query_rules_max_rounds`(기본 2) |
| 한 낱말이 **acronym 이면서 synonym** 이면 하나만 발화 (`AGC`) | 같은 구간에 먼저 걸린 유형이 나머지를 막았다 | 같은 구간이면 유형이 달라도 **함께 발화**. 겹치는 더 긴 매치는 여전히 우선 |

과확장을 막는 안전장치도 같이 넣었다 — 2라운드 이후의 말로는 **치환 질의를 만들지 않고**(원 질의에 없으므로),
같은 `(유형, 대표어)` 묶음은 한 번만 적용한다(사전이 양방향이라 같은 OR 묶음이 두 번 생겼다).
실측: `AGC 수렴 지연 원인` → 규칙 4개 발화, FTS 질의 195자(폭증 없음).

새 명령:

```bat
python -m llmwiki rules lint                 :: 중복 · 자기참조 · 빈 값 · 여러 유형 발화 · alias 사슬 · 사슬 깊이
python -m llmwiki rules merge <파일> [--graph] [--replace]
```

`lint` 의 사슬 깊이는 **`expand` 의 라운드와 같은 방식**으로 센다(같은 동의어 묶음 안을 오가는 것은 라운드가 아니다).

**그래프 규칙의 하드코딩 제거**: `relation_patterns` 가 `value`(entity·date·money·percent·text·id)와
`in_decision`/`in_chunk` 를 파일에서 선언하게 바꿨다. 예전에는 `rel` 이름이 코드에 있는 다섯 개
(owner/deadline/amount/attendee/source)일 때만 동작해서, 파일에 줄을 추가해도 아무 일도 일어나지 않았다.
예전 형식 파일은 이름으로 기본 동작을 채워 준다(호환).

모뎀/임베디드 **기본 사전**도 만들었다 — `setup/query_rules.example.modem.json`(약어 68 · 동의어 35 · 별칭 · 연관어 · 복합어),
`setup/rules.example.modem.json`(엔티티·ID 패턴·관계 패턴·링크 규칙). `rules merge` 로 적용한다.

### 1.5 기본값 (사용자 요청 5)

`precompute` · `query_cache` 를 **기본 off** 로 (`config.py` 기본값 · `config.json` · `setup/config.example.json`).
이유를 토글 설명에 적었다 — 켜 두면 설정을 바꿔 가며 확인할 때 **예전 답이 그대로 돌아와** "고쳤는데 그대로다" 로 보인다.
캐시 동작 자체를 보는 테스트는 명시적으로 켜도록 고쳤다(`test_scale_profile`).

### 1.6 opencode headless 무응답 (사용자 요청 6)

**증상**: 붙었다가 아무 것도 내놓지 않고 매달린다. 예전에는 `subprocess.run(timeout=timeout_s)` 이라
**300초(기본)를 꼬박 기다린 뒤에야** 실패했고, `retries=3` 까지 더하면 한 질의가 20분을 날렸다.

바꾼 것 — 실행을 **스트리밍 + 감시**로:

| 무엇 | 값(`agents.json`, 에이전트별) |
|---|---|
| 마지막 출력 뒤 이만큼 조용하면 죽이고 재시도 | `stall_timeout_s` (60) |
| 첫 출력까지 기다리는 시간 (기동이 느린 에이전트용) | `first_output_timeout_s` (120) |
| 멎기 전 받은 답이 있으면 버리지 않는다 | `keep_partial_on_timeout` (true) |
| 실패 시 로그에 남길 stdout/stderr 꼬리 | `failure_log_chars` (2000) |
| 재시도 사유에 `stall` 추가 | `retry_on` — 예전 파일은 **자동 이관**, 끄려면 `"-stall"` |

부수 효과: 진행 상황(`N줄 수신 · 마지막 출력 3s 전`)이 활동 보드/로그에 흐르고, 실패 로그가
`warning`/`error` 수준으로 argv·종료 코드·줄 수·stdout/stderr 꼬리와 함께 남는다(예전에는 `debug` 에 stderr 300자).

**실측** (mock 에이전트, 전체 제한 60초 · 무출력 제한 3초):

| 상황 | 예전 | 지금 |
|---|---|---|
| 아무 것도 안 내놓고 매달림 | 60초 뒤 timeout | **3.2초**에 `stall` 로 실패 → 바로 재시도 |
| 부분 답을 내고 매달림 | 그 답까지 버림 | **3.2초**에 멈추고 **그 답을 사용** (`partial: true`) |
| 정상 | 0.2초 | 0.2초 (변화 없음) |

주의 하나: 부분 결과는 **파서가 실제 텍스트를 찾았을 때만** 쓴다. 원문 폴백(프로토콜 잡음 JSON)을 답변으로
쓰면 "헛소리를 답으로 돌려주는" 더 나쁜 실패가 되기 때문이다(구현 중 실제로 한 번 그렇게 동작했다).

### 1.7 협업 — 채팅과 게시판 (사용자 요청 7)

문서: [COLLAB.md](../../COLLAB.md)

- 채팅은 **휘발성**(서버 메모리, `retain_min` 뒤 소멸). `/게시 제목` 으로 올린 것만 `data/collab/board.json` 에 남는다.
- 게시할 때 **내 최근 작업 20건**을 골라 연결한다 → 나중에 "무슨 질의에 대한 피드백인가" 를 따라갈 수 있다(요청 1번과 이어진다).
- 채팅창은 **사이드바 최상단 고정**(프리셋은 그 아래), 스크롤해도 남고, 접으면 최근 한 줄만.
- 접속자는 **캐릭터(이모지)** 로 뜨고 마지막 말이 말풍선으로 붙는다. 내 캐릭터는 **드래그**로 옮기며 위치는 화면 비율로 저장된다.
  머문 시간이 길수록 글씨가 커진다 — **시작 크기·증가 폭·주기·상한이 전부 관리자 설정**(`server.json collab`).

**본체에 영향을 주지 않는지**를 따로 확인했다:

| 확인 | 어디서 |
|---|---|
| 토글 `collab` 하나로 완전히 꺼진다 (API 404, 화면에서 사라짐) | `CollabTest.test_disabled_toggle_turns_it_off` |
| 질의 경로(`pipeline·query_engine·mcp·store·answer·retrieval`)가 이 모듈을 **import 하지 않는다** | `…test_collab_is_not_imported_by_query_path` |
| 채팅 폴링 40건이 동시 질의 8건을 **막지도 느리게 하지도 않는다** (읽기 슬롯을 잡지 않는다) | `StressTest.test_collab_traffic_does_not_disturb_queries` |
| 캐릭터 레이어가 아래 화면 클릭을 가로채지 않는다 | `pointer-events` 가 캐릭터에만 (CSS) |
| 폴링이 연속 5회 실패하면 스스로 멈춘다 | `collab.js` |

권한: 채팅·게시·댓글·해결 표시는 `read`, 남의 글 삭제·채팅 비우기는 `admin`(작업 `collab admin`),
내 글 삭제는 `remove_mine`(서버가 작성자를 확인).

## 2. 검증 상세

### 2.1 단위 테스트 — 181/181

신규 `tests/test_features_0916.py` 17건 + 기존 164건. 추가한 것:

| 분류 | 건수 | 무엇을 막는가 |
|---|---|---|
| `RequestHistoryTest` | 4 | 사용자 열·보관 파일·DB 정리 후 복원·경로 해석 |
| `CollabTest` | 6 | 휘발성·명령 해석·게시판 영속·말풍선 크기·좌표 범위·토글 off·**본체 비침투** |
| `QueryRulesExpansionTest` | 4 | 사슬 확장·여러 유형 동시 발화·lint·merge |
| `ModelCatalogTest` | 3 | 세 종류 카탈로그·임베딩/리랭크 in_use·예전 파일 호환 |
| `LlmRetryTest`(기존 파일) | +1 | headless **무응답 감지**와 부분 결과 |
| `StressTest`(기존 파일) | +1 | 협업 트래픽이 질의를 방해하지 않음 |

### 2.2 스트레스 — 9/9

`tests/test_concurrency_0915.StressTest`. 30명 동시 질의 **1.1초**(avg 53ms · p95 110ms), 대기열 유실 0,
빌드 중 질의, 취소, 속도 제한/점검 모드, 잘못된 입력 400(500 아님), 프로파일 왕복, **협업 트래픽 격리**.

### 2.3 CLI — 222/222 · 2.4 Web API — 285/285

Web 에 이번 회차로 추가한 검사(26항목): 요청 이력 5 · 모델 카탈로그/`config reload` 8 · 협업 13.

### 2.5 MCP 종단 — 87/87 (`--quick`)

이번 변경이 MCP 를 건드리지 않았음을 확인. 전체(98항목)는 [VERIFICATION_0916.md](VERIFICATION_0916.md) §2.1.

### 2.6 버튼 — 정적 + **동적** (90/90)

사용자가 물은 **복사·펼치기·접기** 같은 버튼은 질의한 뒤 JS 가 그려 넣는 것이라 정적 수집
(`index.html` 의 `<button id=…>`)에 잡히지 않았다. 그래서 `verify_buttons.py` 에 **동적 단계**를 추가했다 —
격리 서버에서 실제로 질의를 한 번 돌린 뒤 Ask 패널의 버튼을 눌러 본다.

대상: 모든 단계 펼치기 · 모두 접기 · trace 복사 · 근거 문단 펼치기 · 내 지난 요청(열기·새로고침) ·
채팅(접기·펼치기·보내기) · 게시판(열기·새로고침).

> 판정 기준이 정적 버튼과 다르다: 복사·접기처럼 **화면만 바꾸는** 버튼은 서버 호출이 없어도 정상이므로,
> "무언가 일어났는가" 대신 **버튼별 기대 조건**(예: `#trace .tr-row.open` 이 0개)으로 본다.

결과: **90/90 통과** (정적 79 · 동적 11). `trace 복사` 는 헤드리스 브라우저가 클립보드를 막아
"복사 실패" 알림이 뜨는데, **버튼이 실패를 사용자에게 알린다**는 뜻이므로 정상 동작이다(실제 브라우저에서는 복사된다).

### 2.7 무작위 입력 내성

`verify_monkey.py` 의 경로 목록에 이번에 생긴 엔드포인트를 넣었다 —
`/api/collab`, `/api/collab/board`, `/api/query_rules/lint`.

#### 2.7.1 그래서 찾은 결함 2건

**(1) 협업 폴링이 읽기 슬롯을 잡아 질의를 밀어냈다** — 이 회차에서 가장 중요한 발견.

`/api/collab` POST 는 권한 등급이 `read` 라 `weight_for_level()` 이 **읽기 슬롯**을 배정했다.
채팅은 접속자마다 몇 초에 한 번씩 폴링하므로, 30명이 쓰면 그것만으로 슬롯(`max_parallel_reads`, 기본 8)이 차서
**질의가 대기열로 밀린다.** "부수 기능이 본체에 영향을 주면 안 된다" 는 요구를 정면으로 어긴다.

- 증상: 멍키 폭격 중 정상 질의 성공률이 **0/22**, 503 이 308건 (협업 경로를 넣기 전에는 6/28, 503 129건).
- **같은 시드(791257228)로 전후 비교** — 고친 것이 실제로 효과가 있었는지 숫자로 확인했다:

  | | 고치기 전 | 고친 뒤 |
  |---|---|---|
  | 200 응답 | 436 | **505** |
  | 503 거절 | 308 | **236** |
  | 폭격 중 정상 질의 | **0/22** | **4/24** |
  | 결함 | 1건 (NUL) | **0건** |
  | 500 오류 · 서버 생존 | 0 · 생존 | 0 · 생존 |
- 고친 것: `reqmgr.weight_for_level()` 이 `op` 가 `collab…` 이면 **`none`**(락 밖)을 준다.
  권한 등급은 그대로 `read`/`admin` 이다 — 여기서 정하는 것은 락 가중치뿐이다.
  GET 폴링은 원래 `HEAVY_GET` 이 아니라 슬롯을 잡지 않았다(POST 만 문제였다).
- 확인: 30명 동시 질의가 **1.1초 → 0.7초**로 빨라졌고, `StressTest.test_collab_traffic_does_not_disturb_queries`
  가 가중치 자체를 못 박는다(`weight_for_level("read", "collab say") == "none"`).

**(2) `argv` 에 NUL 문자가 들어오면 예외가 샜다**

멍키가 `analyze notanumber \x00\x01\x02` 로 찾았다. NUL 은 파일 경로·SQLite·subprocess 어디서든
`ValueError('embedded null character')` 를 던진다. 경계(`_as_argv`)에서 **400** 으로 거절하도록 고쳤다.

> 다만 이 발견의 절반은 **하네스의 문제**였다: NUL 은 실제 명령줄에 넣을 수 없으므로(OS 가 금지),
> CLI 를 subprocess 로 띄우는 경로에서는 제품이 시작도 못 하고 하네스가 실패한다. 하네스가 CLI 인자에서는
> NUL 을 빼도록 고쳤다. Web 경로(`/api/cli`)는 JSON 으로 NUL 이 들어올 수 있으므로 서버 쪽 방어를 남겼다.

#### 2.7.2 남아 있는 관찰 (1차와 동일)

폭격 직후 `/api/activity` 는 대기열이 비었다고 보고하는데 서버는 `queue_full` 로 거절하는 현상은
[VERIFICATION_0916.md](VERIFICATION_0916.md) §2.6 에 적은 것과 같다. 판정에는 넣지 않는다
(버려진 요청의 서버 스레드가 `queue_timeout_s` 로 정리될 때까지 슬롯을 물고 있다). 관리자 화면과
실제 대기열을 같은 집합으로 맞추는 일은 다음 작업으로 남아 있다.

### 2.8 보안 · 사용자 탭 전수 확인 (`verify_security_ui.py`)

`설정 ▸ 보안 · 사용자` 에서 **사용자 추가 · API 키 발급이 동작하지 않는 것 같다** 는 보고를 받고
그 화면만 따로 전수 확인했다. 결론부터: **배선과 서버는 정상**이었고, 문제는 이 화면이
"안 되는 이유를 말하지 않는" 자리가 다섯 군데 있었다는 것이다.

#### 2.8.1 왜 기존 검사로 못 잡았나

`verify_buttons.py` 는 정적 버튼을 **빈 입력으로** 누른다. `btn-user-add` 의 핸들러는
`if (!name) return;` 이라 id 가 비면 조용히 끝나는데, 그 사이에도 배경 폴링(`/api/collab` 등)이
돌아 `fetch` 수가 0 이 아니었다 → 판정 기준("아무 일도 일어나지 않음")을 빠져나가 **가짜 OK** 가 됐다.
즉 이 버튼은 한 번도 **실제로** 눌린 적이 없었다.

#### 2.8.2 새 검사: 세 가지 접속 상태에서 46항목

| 시나리오 | 무엇을 보나 |
|---|---|
| `auto` | `security.json mode:"auto"` + 127.0.0.1 → 인증 꺼짐(로컬 관리자). 개발 PC 에서 보는 화면 |
| `on` | `mode:"on"` + 로컬 admin 로그인 → 변경마다 **428 단계 확인 모달**을 거쳐도 끝까지 되는지 |
| `nonadmin` | `mode:"on"` + **admin 이 아닌** 계정 → 못 쓰는 기능이 *왜* 못 쓰는지 화면에 적히는지 |

각 항목은 화면만 보지 않고 **서버에 반영되었는지**까지 확인한다(추가한 사용자가 `/api/auth/users` 에
남는가, 발급한 키가 `/api/apikeys` 에 남는가, 바꾼 권한이 `security.json` 에 들어갔는가).

```bat
python tools\verify\verify_security_ui.py                  :: auto,on,nonadmin 모두 — 46/46 통과
python tools\verify\verify_security_ui.py --mode on        :: 한 가지만
```

> 하네스 함정 하나: 읽기 확인을 **브라우저 밖**에서 `urllib` 로 하면 세션 쿠키가 없어 `mode:"on"` 에서
> 익명(viewer)으로 취급되어 목록이 빈 배열로 온다. 화면은 멀쩡한데 검사만 실패하는 가짜 결함이 된다.
> 그래서 읽기 확인도 **페이지 안에서** `fetch` 로 한다.

#### 2.8.3 고친 것 5건 (모두 "조용히 실패" 였다)

| # | 증상 | 고친 내용 |
|---|---|---|
| 1 | id 를 비우고 **추가** → 아무 반응 없음 (버튼이 죽은 것처럼 보임) | `사용자 id 를 입력하세요` 안내 + 입력칸 포커스 |
| 2 | 짧은 비밀번호로 추가 → 서버가 400 을 주기까지 왕복 | 보내기 전에 `비밀번호는 N자 이상` 안내. N 은 `security.json` 의 `local.min_password_len` 을 읽어 표시(입력칸 placeholder 에도) |
| 3 | **API 키 발급** → 토큰은 뜨는데 위 표에 새 키가 없음 → "발급이 안 됐다" 로 보임 | 발급 직후 표를 다시 그린다. 토큰은 한 번만 볼 수 있으므로 **다시 그린 뒤** 다시 표시 |
| 4 | 발급이 거절되면(권한·검증) 아무 일도 안 일어남 (`if (r.token)` 에 else 가 없었다) | 실패 사유를 알림으로. 이름이 비면 보내기 전에 안내 |
| 5 | admin 이 아닌데도 **추가 폼이 그대로 보임** → 눌러야 401/403 | 폼을 감추고, 현재 역할·접속 방식과 "admin 으로 로그인해야 한다" 를 그 자리에 표시 |

5번이 실제 사내 배포에서 가장 흔한 정체다. `mode:"auto"` 는 127.0.0.1 에서만 인증을 끄므로,
**다른 PC 에서 접속하면 익명(viewer)** 이 되어 이 화면의 관리 기능이 전부 막힌다.

#### 2.8.4 설정 파일 정리

실제 `security.json` 의 `permissions.ops` 에 `op0`…`op7` 8개가 남아 있었다. 실제 op 이름은
`/api/eval`·`cli:trial run` 처럼 생겼으므로 이것들은 어떤 작업과도 매칭되지 않는 **멍키 테스트 잔여물**이다.
동작에는 영향이 없지만 화면의 "개별 작업 오버라이드" 칸을 어지럽혀서 지웠다(원본은 `security.json.bak`).

### 2.9 단계 재실행 · 진행 중 작업 상세 (2026-09-17)

문서: [RERUN.md](../../RERUN.md) · [ACTIVITY_DETAIL.md](../../ACTIVITY_DETAIL.md)

| 확인한 것 | 어디서 |
|---|---|
| 재시작점별로 **어디까지 재생하고 어디부터 다시 계산하는지** | `tests/test_rerun_0917.py` (16건) |
| 재실행에서 바꾼 설정·토글이 뒤 단계에 반영된다 | `test_override_takes_effect_after_restart_point` · `test_toggle_change_takes_effect_after_restart_point` |
| 색인이 바뀌면 재생을 거부한다 (`plan` 은 허용) | `test_stale_index_is_refused` |
| 원본 중간 결과를 덮어쓰지 않고, 재실행 결과에서 또 이어서 돌 수 있다 | `test_rerun_saves_its_own_checkpoint_and_keeps_original` |
| API 왕복 (정보 조회 · 답변부터 · 설정 바꿔서 · 잘못된 입력 3종) | `verify_web.py` 7건 (총 **292/292**) |
| 브라우저에서 ⟲ → 창 → 실행 → 재생 표시 · 완료 항목 클릭 → 저장된 답변 → Ask 복원 | `verify_rerun_ui.py` **20/20** |

#### 2.9.1 이 회차에 찾아 고친 결함 4건

**(1) 재실행이 캐시로 응답했다 — 기능이 조용히 무력화되던 것**

`query_cache` 가 켜져 있으면 재실행 요청도 캐시에 먼저 맞아 `cache_hit` 한 단계만 찍고 **예전 답이
그대로** 돌아왔다. 설정을 바꿔 가며 ⟲ 를 눌러도 화면이 그대로여서, 사용자에게는 "재실행 버튼이
안 먹는다" 로 보인다. 오류도 나지 않으므로 로그로도 안 잡힌다.

`verify_web.py` 에 넣은 "재생 단계 수" 검사가 `재생 0단계 | 단계 sync_index,cache_hit` 로 잡아냈다.
고침: 재실행 중에는 `query_cache`·`precompute` 를 **읽지 않는다**(trace 에 이유를 남긴다).
회귀 방지: `test_query_cache_never_serves_a_rerun`.

**(2) 최근 완료 항목의 클릭이 죽어 있었다**

카드의 **본문 텍스트**에서 토큰을 찾아 작업과 짝지었는데, 최근 완료 카드는 토큰을 표시하지 않는다
→ 짝을 못 찾아 클릭 핸들러가 아예 붙지 않았다. 실행 중 카드에는 취소 버튼(`data-cancel`)이 있어
우연히 동작했으므로, 실행 중인 것만 보고 넘어갔으면 놓쳤을 결함이다.
고침: 카드·표 행마다 `data-token` 을 달고 그것으로 잇는다.

> 덧붙여, 저장된 결과를 Ask 화면에 복원할 때 `renderResult()` 를 직접 부르면 터진다 — 저장 결과에는
> 근거 전문(`hits`)이 없고 요약(`hits_brief`)만 있기 때문이다. Ask 탭의 `openPastRequest()` 가 이미
> 그 복원을 알고 있어 **그 함수를 공개해 재사용**했다. 같은 일을 두 곳에서 다르게 하지 않는다.

**(3) CSS 클래스 이름 충돌로 대화상자가 가로로 흩어져 겹쳐 보였다** (사용자 보고)

`style.css` 에 `.modal` 이 **두 번** 정의되어 있었다 — 154줄은 "가운데 떠 있는 카드"(`.modal-bg` 안의
상자), 402줄은 게시 대화상자를 만들며 추가한 "화면 전체 덮개". 뒤에 선언된 덮개 규칙이 카드를 덮어써
`position:fixed; inset:0; display:flex` 가 카드에 붙었고, 카드의 자식(제목·입력칸·버튼)이 **가로로
나열**되면서 내용이 서로 겹쳤다. 단계 재실행 창에서 처음 드러났지만, 같은 카드 클래스를 쓰는
**권한 단계 확인 모달(`stepUp`)도 이미 같은 상태**였다 — `mode:"on"` 에서만 뜨는 창이라 그동안 보이지
않았을 뿐이다.

고침: 덮개를 `.modal-overlay` 로 분리하고(`index.html` 의 `#post-modal` 도 함께), 카드용 `.modal` 은
원래 역할로 되돌렸다. `.modal label` 이 `input` 만 다루고 `select`/`textarea` 는 놓치던 것도 함께 고쳤다.
회귀 방지: `verify_rerun_ui.py` 가 **계산된 스타일**로 확인한다 — 카드는 `position:fixed` 가 아니고
`display:block` 이며 너비가 300~560px, 덮개는 `position:fixed` 이고 그 안의 상자는 300px 이상.

**(4) 완료 항목 상세의 '전체 보기' 가 누르자마자 도로 접혔다** (사용자 보고)

상세를 열면 1.5초마다 진행 상황을 받아 다시 그리는데, 멈추는 조건이 "상태가 running 이 아니면" 이었다.
**진행 기록이 아예 없는 작업**(끝난 지 오래되어 서버가 정리한 것)은 상태 자체가 없어 이 조건이 false →
타이머가 계속 돌며 1.5초마다 화면을 새로 그렸고, 펼친 답변이 곧바로 도로 접혔다.
고침: "진행 정보가 없거나 running 이 아니면" 멈춘다 + 펼침 상태를 기억해 다시 그려도 유지한다.
회귀 방지: 완료 항목을 연 뒤 **5초 동안 `/api/progress` 호출이 0건**인지 확인한다.

## 3. 다른 환경에서 다시 돌리는 법

```bat
python -m unittest discover -s tests
python -m unittest tests.test_concurrency_0915.StressTest    :: 30명 동시 (스트레스)
python tools\verify\verify_ui_wiring.py
python tools\verify\verify_cli.py
python tools\verify\verify_web.py
python tools\verify\verify_mcp.py --quick
python tools\verify\verify_browser.py
python tools\verify\verify_buttons.py                        :: 정적 + 동적
python tools\verify\verify_security_ui.py                    :: 보안·사용자 화면 (세 가지 접속 상태)
python tools\verify\verify_rerun_ui.py                       :: 단계 재실행 ⟲ · 진행 중 작업 상세
python tools\verify\verify_monkey.py
python -m llmwiki rules lint                                 :: 규칙 사전 점검
python -m llmwiki mcp --doctor                               :: MCP 자가 점검
```

## 4. 이 회차에 바뀐 파일

| 파일 | 변경 |
|---|---|
| `llmwiki/collab.py` | **신규** — 휘발성 채팅 · 접속 표시 · 말풍선 크기 · 게시판 · `/게시` 해석 |
| `llmwiki/web/static/js/collab.js` | **신규** — 사이드바 채팅, 캐릭터 드래그, 게시판 |
| `llmwiki/store.py` | `requests` 의 `user`·`file` 열과 이관, 결과 보관 파일(쓰기·읽기·정리), `requests(user=, q=)` |
| `llmwiki/config.py` | `requests_dir`·`requests_keep_days`·`requests_archive_dir()`, 토글 `collab`, `query_cache`/`precompute` 기본 off |
| `llmwiki/headless.py` | 스트리밍 실행 + 무출력 감시 · 부분 결과 · 실패 로그 강화 · mock `--stall` |
| `llmwiki/query_rules.py` | 다단 확장 · 같은 구간 여러 유형 · `lint()` · `merge_rules()` |
| `llmwiki/graph_rules.py` | 관계 패턴 해석을 **파일 선언**으로(`value`/`in_decision`/`in_chunk`), 예전 형식 호환 |
| `llmwiki/models_catalog.py` | `rerank` 절 · `kind` 처리 · `describe()` 가 임베딩·리랭크까지 보고 |
| `llmwiki/tuning.py` | `query_rules_max_rounds` · `forensic_suggestion_min_confidence` · `forensic_targets_shown` |
| `llmwiki/pipeline.py` `query_engine.py` | 보관 폴더 전달, `maintenance prune_requests` |
| `llmwiki/web/server.py` | `/api/collab`(GET/POST) · `/api/collab/board` · `/api/requests` 확장 · `/api/config action=reload` · `/api/query_rules/lint` |
| `llmwiki/auth.py` | 작업 분류 `collab …` · `collab admin` · `config reload` · `requests all` |
| `llmwiki/reqmgr.py` | `server.json` 의 `collab` 절 |
| `llmwiki/cli.py` | `rules lint|merge` · `maintenance prune_requests` |
| `llmwiki/web/static/index.html` `style.css` `js/ask.js` `js/core.js` `js/observability.js` `js/settings.js` | 채팅창·게시판 탭·캐릭터 레이어, 내 지난 요청, `renderResult()` 분리, 모델 화면, 첫 로드 loader |
| `prompts/answer_system.md` `answer_guide.md` `llmwiki/prompts.py` | 모뎀 PHY 페르소나 답변 프롬프트 |
| `setup/query_rules.example.modem.json` `setup/rules.example.modem.json` | **신규** — 모뎀/임베디드 기본 사전 |
| `setup/config.example.json` `server.example.json` `agents.example.json` | 새 키 반영 |
| `tests/test_features_0916.py` | **신규** 17건 |
| `tests/test_features_0914.py` `test_concurrency_0915.py` `test_scale_profile.py` `test_phase0.py` | headless stall · 협업 격리 · 캐시 기본값 · 프롬프트 |
| `tools/verify/verify_web.py` `verify_buttons.py` `verify_monkey.py` | 신규 검사 26항목 · 동적 버튼 단계 · 새 엔드포인트 |
| `tools/verify/verify_security_ui.py` | **신규** — 보안·사용자 화면 46항목 (§2.8) |
| `llmwiki/rerun.py` | **신규** — 단계 재실행: 재시작점 표 · 중간 결과 저장/조회/정리 · 재생 계획 (§2.9, [RERUN.md](../../RERUN.md)) |
| `llmwiki/query_engine.py` | 단계별 재생 분기 · 캡처 지점 · `_replay_retrieval()` · 재실행 중 캐시 차단 |
| `llmwiki/profiler.py` | `Profiler.replayed()` — 재생한 단계를 trace 에 표시 (건너뛴 것과 구분) |
| `llmwiki/pipeline.py` `web/server.py` `cli.py` `auth.py` | `Pipeline.rerun()` · `GET /api/rerun` · `POST /api/query/rerun` · `rerun` 명령 · 작업 등급 분류 |
| `llmwiki/web/static/style.css` | `.modal` 이름 충돌 해소(덮개 → `.modal-overlay`), `.modal label` 이 select·textarea 도 다루게 |
| `llmwiki/web/static/js/core.js` `observability.js` `ask.js` | 워터폴 ⟲(클릭=바로 실행, Shift+클릭=설정 창) 와 재실행 창 · 진행 중 작업 상세 · `LW.openPastRequest` 공개 |
| `llmwiki/reqmgr.py` | 활동 스냅샷에 `request_id` (완료 항목 → 저장된 결과 연결) |
| `tests/test_rerun_0917.py` | **신규** 16건 |
| `tools/verify/verify_rerun_ui.py` | **신규** — 브라우저 20항목 (§2.9) |
| `docs/RERUN.md` `docs/ACTIVITY_DETAIL.md` | **신규** |
| `llmwiki/web/static/js/settings.js` | 보안 화면의 "조용히 실패" 5건 (§2.8.3) — 빈 입력 안내, 최소 비밀번호 길이, 키 발급 후 표 갱신, 발급 실패 알림, admin 아닐 때 폼 감춤 |
| `docs/COLLAB.md` `docs/REQUEST_HISTORY.md` `docs/history/2026-09-16/IMPLEMENTATION_PLAN_0916.md` `docs/history/2026-09-16/VERIFICATION_0916_2.md` | **신규** |
| `README.md` `docs/BRINGUP_GUIDE.md` `docs/FORENSIC.md` | 문서 연결과 설정 총람 |
