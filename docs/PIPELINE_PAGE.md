# PIPELINE 페이지 — 구조를 읽고, 그 자리에서 고치고, 값을 바꿔 가며 비교한다

> 설계 근거: [IMPLEMENTATION_PLAN_0918_2.md §2.6](history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md) · 스윕 엔진: [SWEEP.md](SWEEP.md) · 손잡이 지도: [OPTIMIZATION_GUIDE.md](OPTIMIZATION_GUIDE.md)
> 구현: `llmwiki/web/static/js/pipeline.js` · `index.html` 의 `#tab-pipeline` · 데이터는 `GET /api/architecture`(레지스트리 `llmwiki/architecture.py`) · `/api/tuning` · `/api/sweep/keys` · `/api/sweep`

## 0. 한 장 요약

| 어디 | 무엇 |
|---|---|
| 헤더 **🧭 Pipeline** › 흐름 · 튜닝 · 스윕 | 왼쪽 **흐름**(query · build · evolve · watch) + 페이즈 목차 → 가운데 **페이즈 → 단계** → 오른쪽 **선택한 국면/단계의 상세** |
| 계층 | **흐름 → 페이즈(국면) → 단계 → trace 노드** 네 단. 질의 흐름만 26단계라 단계만 나열하면 어디가 검색이고 어디가 답변인지 보이지 않는다 |
| 보기 3종 | **블록**(한 흐름 세로·기본) · **지도**(한 흐름 가로) · **전체 흐름**(네 흐름 한 화면) |
| 상세에서 하는 일 | 토글 on/off · `config.json` 값 편집(admin) · 튜닝 값 편집(**이번 요청에만** / **tuning.json 저장**) · 마지막 실행 시간과 meta · 그 단계/국면 키로 **스윕** |
| 스윕 | 값마다 그 키의 단계부터만 다시 실행 → **단계 × 값 격자**(★ = 기준과 다름, 셀 클릭 = diff) |
| 사이드바 | 토글 묶음이 기본으로 **요약 한 줄**(`sidebar_toggles=compact`)이 되고, 편집은 이 페이지에서. `full` 로 예전 배치 복귀 |

### 왜 한 페이지인가 (2026-09-19 통합)

2026-09-18 까지는 같은 레지스트리(`/api/architecture`)를 **두 화면**이 나눠 그렸다.

| 예전 화면 | 무엇 | 문제 |
|---|---|---|
| Observability › **구조·흐름** | 흐름 전체 가로 지도 · 단계 카드 · 마지막 실행 시간 겹쳐 보기 · **읽기 전용** | 보고 나서 고치려면 다른 화면으로 옮겨야 했다 |
| 🧭 **Pipeline** | 한 흐름 세로 블록 · 토글/튜닝 편집 · 스윕 | 흐름 전체를 한눈에 보거나 상위 국면으로 훑을 수 없었다 |

같은 것을 두 벌 그리면 한쪽만 고쳐져 서로 다른 말을 하게 된다. 그래서 **구조·흐름을 Pipeline 에 흡수하고 탭을 없앴다**.
흡수한 것: 흐름 머리(진입 명령·최근 실행 요약·전체 trace 링크), 가로 지도 보기, 전체 흐름 보기, 시간 비중 막대, 범례,
`마지막 실행 시간` / `요청 토글 상태 반영` 두 옵션, 단계 상세의 trace meta·오류 표시.
여기에 **페이즈(상위 단계)** 를 새로 넣어 "지금 어느 국면인가" 를 먼저 읽고 필요한 단계로 내려가게 했다.
예전 주소 `#observability/arch` 로 들어오면 `core.js` 의 `TAB_ALIAS` 가 이 페이지로 보낸다.

## 1. 상태는 하나다 (사이드바 ↔ Pipeline)

| 무엇 | 어디에 저장 | 언제까지 |
|---|---|---|
| 토글 | 사이드바의 `[data-toggle]` 체크박스 **하나** (Pipeline 의 스위치는 그것을 비춘다) | 이번 요청 (`LW.overrides()`) |
| 튜닝 "이번 요청에만" | `core.js` 의 요청 오버라이드(`overrides.tuning` / 평면 `overrides`) | 이번 요청 |
| 튜닝 "tuning.json 저장" | `tuning.json` (`POST /api/tuning` · 등급 `edit`) | 영구 · 모든 사용자 |
| config 값 저장 | `config.json` (`POST /api/config` · **admin**) | 영구 · 모든 사용자 |

그래서 Pipeline 에서 켠 토글이 Ask 질의·⟲ 재실행·평가·스윕에 그대로 실린다. 사이드바 CLI 미리보기에도 같은 플래그가 나타난다. 파일에 남는 것은 아래 두 버튼뿐이다.

## 2. 화면

```
┌ 흐름 ─────┐ ┌───── 페이즈 → 단계 ─────────┐ ┌────────── 상세 ──────────┐
│ ▸ query   │ │ ▾ 질의 이해 · 확장  5단계 12ms│ │ (국면을 고르면) 국면 설명   │
│   build   │ │   ▣ time_scope  on  tune 3  │ │   합계 · 포함 단계 표 · 키   │
│   evolve  │ │        ↓                    │ │ (단계를 고르면)            │
│   watch   │ │   ▣ query_rules on  tune 6  │ │ 제목 · 모듈 · 입출력        │
│           │ │ ▾ 채널 검색       5단계 41ms │ │ 동작 / impact              │
│ 진입 명령  │ │   ▣ fts_search  on  tune 12 │ │ 토글 스위치 (이번 요청)      │
│ 최근 #123 │ │        ↓  …                 │ │ config 값 [입력] (admin)    │
│ 범례       │ │ ▾ 융합 · 선별     4단계 18ms │ │ 튜닝 표 [요청만][파일 저장]  │
│ 페이즈 목차 │ │   …                         │ │ CLI · 마지막 실행 · 🔁 스윕  │
└───────────┘ └─────────────────────────────┘ └──────────────────────────┘
```

**위 — 보기와 옵션.**

| 컨트롤 | 하는 일 |
|---|---|
| **보기: 블록 / 지도 / 전체 흐름** | 블록 = 고른 흐름을 세로로(기본) · 지도 = 같은 흐름을 **가로 체인**으로(예전 구조·흐름의 모양) · 전체 흐름 = 네 흐름을 한 화면에 가로로 |
| **마지막 실행 시간** | 끄면 ms·비중 막대를 모두 뺀다 — 구조만 읽을 때 |
| **요청 토글 상태 반영** | 켜면 사이드바/이 화면에서 바꾼 **이번 요청** 상태로, 끄면 서버 `config.json` 값으로 그린다(배포 기본값이 어떤 모습인지) |

**왼쪽 — 흐름과 국면.** 흐름 버튼 4개(`/api/architecture` 의 flows), 그 아래 **흐름 머리**(진입 명령 `entry`, 최근 실행 `#id · ms`, 전체 trace 로 가는 버튼), 범례, 그리고 **페이즈 목차**. 목차의 한 줄을 누르면 가운데가 그 국면으로 스크롤되고 오른쪽에 국면 상세가 열린다.

**가운데 — 페이즈(상위 단계) → 단계.** 페이즈 머리에는 국면 이름·키와 합계가 붙는다: 단계 수, 켜짐/꺼짐 토글 수, 튜닝 수(요청 오버라이드가 있으면 `●`), 건너뛴 단계 수, **합계 ms 와 흐름 전체 대비 비중**(머리 아래 얇은 막대). `▸/▾` 로 국면을 접고, 제목을 누르면 국면 상세가 열린다. 그 아래 단계 블록은 예전과 같다 — 켜진/꺼진 토글 수, `tune n`, 마지막 실행 ms, 비중 막대. 꺼졌거나 건너뛴 단계는 흐리게, 오류가 난 단계는 빨갛게 나온다. 시간의 출처는 이 화면에서 방금 한 질의(`STATE.lastTrace`)이고, 없으면 서버의 최근 같은 종류 요청이다.

**오른쪽 — 국면 상세 (페이즈를 골랐을 때).**

| 칸 | 내용 |
|---|---|
| 이 국면이 하는 일 | 레지스트리의 페이즈 설명 |
| 합계 | 단계 수 · on/off 토글 · 튜닝 수(요청 오버라이드) · 합계 ms 와 비중 · 건너뛴 단계 수 |
| 포함 단계 표 | 단계마다 ms · 토글 · 튜닝 수 · impact. **행을 누르면 그 단계 상세로** |
| 이 국면의 튜닝 키 | 국면 전체의 키를 한 줄로. 키를 누르면 아래 스윕 폼이 그 키로 채워진다 |
| 버튼 | 첫 단계 열기 · 국면 접기 |

국면을 고른 상태에서 스윕 폼의 키 목록은 **그 국면의 모든 단계** 키로 좁혀진다 — 단계 하나보다 넓고 전체보다 좁은 자리다.

**오른쪽 — 단계 상세 (단계를 골랐을 때).**

| 칸 | 내용 | 권한 |
|---|---|---|
| 머리 | 단계 제목 · 키 · 흐름 · 구현 모듈 · 입출력 · 튜닝 단계 이름 | – |
| 동작 / impact | 레지스트리의 설명과 "이걸 켜면 무엇이 좋아지고 무엇을 내주나" | – |
| **토글** | 스위치 + config.json 기본값과 다르면 `≠ config` 배지. 프리셋이 정한 값은 표시가 붙는다(손으로 바꿔도 서버가 프리셋을 다시 얹는다는 경고 포함) | 누구나 (이번 요청) |
| **config.json 설정** | 이 단계가 읽는 config 키와 현재 값. 객체 값(`llm_roles` 등)은 읽기 전용으로 보여 주고 Settings 로 안내 | 보기: 누구나 · 저장: **admin** |
| **튜닝 파라미터** | 키 · 파일 값 · 기본값 · 새 값 입력 · 설명/impact. 파일 값이 기본과 다르면 강조, 이번 요청 오버라이드가 걸려 있으면 `요청 <값>` 칩과 해제(✕) | 보기: 누구나 · 요청 적용: 누구나 · 저장: `edit` 등급(기본 class2) |
| CLI | 같은 일을 하는 CLI 명령 | – |
| 마지막 실행 | 그 단계의 ms·비중, trace 하위 노드별 ms·LLM 호출·토큰·재생 여부·오류·meta. "요청 프로파일에서 전체 trace" 버튼 | – |

튜닝 표의 버튼 네 개:

| 버튼 | 하는 일 |
|---|---|
| **이번 요청에만** | 입력한 값을 요청 오버라이드로. 파일은 그대로. `rebuild` 표시가 붙은 키는 제외되고 그 사실을 알린다(전체 리빌드가 필요한 값이라 요청 단위로 뜻이 없다) |
| **tuning.json 저장** | `POST /api/tuning {action:"set"}`. 권한이 없으면 버튼이 비활성이고 필요한 역할을 툴팁에 적는다(눌러서 403 을 보게 하지 않는다) |
| **요청 오버라이드 해제** | 이 단계의 요청 값만 전부 해제 |
| **Settings › 튜닝** | 같은 단계로 필터를 건 Settings 튜닝 표로 이동(범위·예시 열까지 보고 싶을 때) |

## 3. 스윕 (🔁 범위 스윕)

값마다 **그 키가 영향을 주는 단계부터만** 다시 실행한다(앞 단계는 저장된 중간 결과를 재생). 엔진과 CLI·MCP 는 [SWEEP.md](SWEEP.md).

| 입력 칸 | 뜻 |
|---|---|
| **키** | 선택한 단계의 스윕 가능한 키(튜닝·config·토글·역할 LLM). 옆의 **모든 단계의 키** 를 켜면 질의 경로 전체 |
| (키 설명 줄) | 종류·형·범위 또는 선택지·기본값·현재값·**자동 재시작점**·설명 |
| **값** | `범위 start:stop:step` 또는 `값 목록`(쉼표). bool·choice 키는 비우면 가능한 값 전부 |
| **기준 요청** | `last` 또는 요청 id. 저장된 중간 결과(`rerun_capture`)가 있어야 한다 |
| **또는 질의** | 기준 요청이 없을 때 이 질의를 1회 실행해 기준을 만든다(기준 요청 칸을 비운다) |
| **반복** | 값마다 반복 횟수(LLM 흔들림 확인, 상한 `sweep.MAX_REPEATS`) |
| **재시작점** | 기본은 자동(키가 속한 단계). 더 앞에서부터 다시 돌리고 싶을 때만 고른다 |
| **역할 LLM** | 모든 값에 공통으로 적용할 역할 오버라이드. `answer.model=qwen2.5:14b, rerank.effort=low` 형식 |

실행하면 백그라운드 잡이 되고(Observability › 진행 중 작업에도 보인다) 진행 로그가 폼 아래에 흐른다. 상한은 폼에 표시된다(`sweep_max_values` · 반복).

**결과 격자** — 행 = 단계, 열 = 값(첫 값의 첫 실행이 **기준**).

| 셀 표기 | 뜻 |
|---|---|
| 숫자 | 그 단계의 ms |
| `⟲` | 재생(저장값 그대로 — 이 값의 영향 밖) |
| `★` | 기준과 다름(순위·집합·meta·답변 중 하나가) |
| `–` | 건너뜀 · `·` 이 실행의 trace 에 그 단계가 없음 |
| 마지막 **결과** 행 | 총 ms(Δ) · groundedness · 인용 수 · `result_type` · 답변 앞부분 |

머리말에는 **최적 힌트**가 나온다 — groundedness 최고 / 가장 빠름 / 인용 최다 / 토큰 최소인 값. 셀을 누르면 아래 **diff 패널**: 단계 셀은 순위·컨텍스트 집합 diff 와 meta, 결과 셀은 최종 순위·컨텍스트·답변 텍스트 diff 와 답변 전문. **⧉ 텍스트 복사** 는 `sweep show` 와 같은 표를 클립보드로(http 접속에서도 동작 — [WEB_UI.md](WEB_UI.md) 의 복사 3단 폴백).

**지난 스윕** 목록은 `data/sweeps` 의 기록이다(`sweep_keep` 개 보관). 한 줄을 누르면 그 기록과 비교를 격자로 되살린다.

## 4. 사이드바 토글 배치 — `sidebar_toggles`

| 값 | 사이드바의 "기능 토글" 블록 |
|---|---|
| `compact` (기본) | 요약 한 줄 **"이번 요청 오버라이드: 토글 n · 튜닝 m"** + `🧭 Pipeline 에서 편집` 버튼. 묶음·범례·축 필터는 숨는다 |
| `full` | 2026-09-17 까지의 배치(묶음 전체 + 축 필터) |

- 바꾸는 곳: 블록 제목 줄의 **전체 보기 / 간단히** 버튼(`#sb-toggles-mode`).
- 저장: 이 브라우저(`localStorage llmwiki.sidebar_toggles`)에 즉시, 헤더의 **💾 내 설정 저장** 을 누르면 계정 프로파일(`data/profiles.json` 의 `sidebar_toggles`)에도. 기본값은 `compact`.
- 어느 배치에서도 체크박스는 DOM 에 남아 있다(숨김만). 그래서 프리셋·요청 오버라이드·프로파일 저장이 두 배치에서 똑같이 동작한다.
- 블록 접기/펴기(`sidebar_collapsed`)는 그대로다 — [WEB_UI.md](WEB_UI.md).

## 4.5 페이즈(상위 단계)를 늘리거나 바꾸려면

페이즈는 화면이 아니라 **레지스트리**에 있다 — `llmwiki/architecture.py` 의 `PHASES`(흐름별 국면의 순서·제목·설명)와 `STAGE_PHASE`(단계 → 국면). Web 화면과 CLI `arch` 가 같은 표를 읽으므로 한 곳만 고치면 둘 다 바뀐다.

| 하고 싶은 것 | 고칠 곳 |
|---|---|
| 국면의 제목·설명을 바꾼다 | `PHASES["<흐름>"]` 의 해당 항목 |
| 국면을 새로 만든다 | `PHASES["<흐름>"]` 에 **순서에 맞는 자리**에 넣고, 그 국면에 들어갈 단계를 `STAGE_PHASE` 에서 그 키로 바꾼다 |
| 새 단계를 추가한다 | `FLOWS` 에 단계를 넣고 `STAGE_PHASE` 에 한 줄 |

페이즈는 **연속된 단계 묶음**이라 단계 순서를 바꾸지 않는다. 배치를 빠뜨린 단계는 `(기타)` 국면으로 모이고, `tests/test_tuning_arch.py::test_phases_cover_every_stage` 가 그 사실을 실패로 알려 준다(모든 단계가 순서대로 정확히 한 국면에 들어가는지 검사한다).

현재 배치 (`python -m llmwiki arch --flow query` 로도 확인):

| 흐름 | 국면 |
|---|---|
| **query** | 준비·캐시 → 질의 이해·확장 → 채널 검색 → 융합·선별 → 컨텍스트·답변 → 사후 기록·학습 |
| **build** | 준비·점검 → 수집·변경 감지 → 채널 색인 → 파생물 생성 → 정리·예열 |
| **evolve** | 제안 수집 → 검토·적용 → 검증·이력 |
| **watch** | 변경 감지 → 증분 반영 |

## 4.6 단계별 시간 제한 (2026-09-19)

실측 ms 만 보면 그 숫자가 여유 있는 값인지 제한에 거의 닿은 값인지 알 수 없다. 그래서 **실측 옆에 그 단계를 끊을 수 있는 제한**을 같이 보여 준다.

| 자리 | 무엇이 보이나 |
|---|---|
| 가운데 단계 블록 | `≤ 10m` 처럼 점선 칩. 마지막 실행이 제한의 80% 를 넘으면 주황, 넘겼으면 빨강 |
| 흐름 머리(왼쪽) | 그 흐름 전체에 걸리는 제한 (`query_s ≤ 15m`, 빌드는 `job_s ≤ 48h` 등) |
| 단계 상세(오른쪽) | **시간 제한** 표 — 제한 값 · 무엇을 끊나 · 종류 · **어느 파일의 어느 키인지** · 마지막 실행이 제한의 몇 % 였는지 |
| 국면 상세의 포함 단계 표 | `제한` 열 |
| Observability 의 trace 폭포 | 오른쪽 실측 ms 바로 밑에 `(≤ 10m)`. 단계 표에는 `제한` 열 |

정의는 서버 한 곳뿐이다 — `llmwiki/architecture.py` 의 `stage_limits()`. 화면은 `GET /api/limits`(또는 `/api/architecture` 의 `limits`)로 받고, 터미널에서는 `python -m llmwiki arch limits [--flow query]` 가 같은 값을 출력한다.

제한의 종류는 다섯 가지다.

| 종류 | 어디서 오나 | 예 |
|---|---|---|
| `LLM 1회` | `config.json` 의 `llm_roles.<역할>.timeout_s` (비우면 `llm_timeout`) | answer · rerank · expand · verify · fusion · select · extract · summary |
| `재시도 합계` | `llm_roles.<역할>.budget_s` | 재시도까지 합쳐 이 시간을 넘기면 포기 |
| `요청 전체` | `server.json` 의 `timeouts.query_s` | 질의 흐름의 모든 단계 |
| `작업 전체` | `server.json` 의 `timeouts.job_s` | 빌드·평가·스냅샷 (기본 48시간) |
| `락 대기` · `DB 잠금 대기` | `config.json` 의 `build_lock_timeout` · `build_lock_stale_s` · `db_busy_timeout_s` | 빌드 시작 대기, 빌드 1회의 최대 수명 |

단계가 어느 역할의 LLM 을 부르는지는 `architecture.py` 의 `STAGE_ROLES` 에 있다. **새 LLM 단계를 추가하면 여기에도 한 줄 넣어야** 화면에 제한이 뜬다.
빌드 쪽 값 전체와 그 이유는 [CONCURRENCY.md](CONCURRENCY.md) 의 '빌드에 걸리는 시간 제한' 절에 있다.

## 4.7 단계는 네 곳에 나뉘어 산다 — 정렬 검증 (2026-09-19)

한 단계(`fts_search`, `answer_llm` …)는 서로 다른 네 곳에 이름이 적힌다. 하나만 빠져도 화면에서 조용히 어긋난다.

| 어디 | 무엇을 결정하나 |
|---|---|
| **코드** — `prof.stage("이름")` | 실제 trace 노드. 폭포 그림의 한 줄 |
| **레지스트리** — `architecture.py` 의 `FLOWS[*].stages[*].trace` | 이 페이지의 블록, 토글·config·튜닝 연결, 시간 제한 |
| **라벨표** — `progress.py` 의 `STAGE_LABELS` | 실행 중 진행 패널에 뜨는 한국어 이름 |
| **손잡이** — `TOGGLE_HELP` · `SETTING_HELP` · `tuning.TUNABLES` | 단계 상세의 스위치·값·설명 |

실제로 2026-09-19 에 `fts_search_alt` 와 `fts_search_rules` 는 trace 에는 나오는데 레지스트리에 없어서,
폭포 그림에는 보이지만 이 페이지에서는 토글·튜닝·시간 제한이 하나도 붙지 않았다. 같은 날 config 키 25개가 설명 없이 비어 있었다.

`python tools/verify/verify_stage_align.py` 가 아홉 가지를 검사한다: 코드가 만드는 이름이 레지스트리에 있는가,
라벨표가 양방향으로 맞는가, 모든 단계가 페이즈에 배치됐는가, 단계가 가리키는 config 키·토글이 실재하고 설명이 있는가,
튜닝 키 160개가 모두 어떤 단계에 속하는가, LLM 역할 10개가 모두 단계에 연결됐는가, 시간 제한 표가 모든 이름을 덮는가, 프리셋이 실재하는 키만 건드리는가.
`verify_all.py` 의 빠른 묶음에 들어 있다.

**새 단계를 추가할 때** 고칠 곳: `architecture.py` 의 `FLOWS`(+`trace` 목록) · `STAGE_PHASE` · (LLM 을 부르면) `STAGE_ROLES` · `progress.py` 의 `STAGE_LABELS`.

## 5. 설정

| 파일 | 키 | 기본 | 뜻 |
|---|---|---|---|
| `data/profiles.json` (계정별) | `sidebar_toggles` | `compact` | 사이드바 토글 블록 배치 (`compact` \| `full`) |
| `config.json` | `sweep_dir` · `sweep_keep` · `sweep_max_values` · `sweep_max_parallel` | `data/sweeps` · 30 · 20 · 1 | 스윕 저장·보관·상한 — [SWEEP.md](SWEEP.md) |

Pipeline 페이지 자체는 새 서버 설정을 두지 않는다. 화면이 읽는 값은 모두 기존 레지스트리(`architecture.py` · `tuning.py`)와 현재 설정이다.
보기 3종·두 옵션·국면 접힘은 화면 상태라 저장하지 않는다(새로 고치면 기본값으로 돌아온다).

## 6. 권한

| 하는 일 | 필요 등급 (security.json `permissions.levels`) |
|---|---|
| 페이지 열기 · 토글/튜닝 "이번 요청에만" · 스윕 실행 | `read` (기본 viewer — 익명 포함) |
| `tuning.json 저장` | `edit` (기본 class2) |
| `config.json 저장` | `admin` |

권한이 없으면 **버튼이 비활성**이고 필요한 역할과 현재 역할이 툴팁에 적힌다. 서버도 같은 표로 다시 검사한다([SECURITY.md](SECURITY.md)).

## 7. 확인

```powershell
python -m llmwiki serve                      # 브라우저에서 🧭 Pipeline
python tools/verify/verify_ui_wiring.py      # id ↔ JS ↔ API 경로 정합
python tools/verify/verify_browser.py        # 콘솔 오류 0 · 탭 렌더
python tools/verify/verify_buttons.py        # 모든 버튼을 실제로 눌러 본다 (Pipeline 포함)
python -m llmwiki arch --flow query          # 같은 레지스트리를 CLI 로 (블록 목록과 일치해야 한다)
python -m llmwiki sweep run last --key rrf_k --range 10:100:30      # 같은 스윕을 CLI 로
```

## 8. 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| 블록의 ms 가 모두 `—` | 이 브라우저에서 아직 질의하지 않았고 서버에도 최근 요청이 없다. Ask 에서 한 번 질의한다 |
| 토글을 바꿨는데 답이 그대로 | 요청 단위라 **다음 질의부터** 적용된다. 사이드바 CLI 미리보기에 플래그가 붙었는지 확인 |
| 토글 스위치가 비활성 | 그 토글이 사이드바 목록에 없다(서버가 모르는 이름). `/api/status` 의 `toggle_names` 확인 |
| `tuning.json 저장` 이 비활성 | 역할이 `edit` 등급 미만. 툴팁에 필요한 역할이 적혀 있다 |
| 스윕 실행이 "기준 요청이 없다" | `rerun_capture` 토글이 꺼진 채로 질의했다. 켜고 다시 질의하거나 **또는 질의** 칸을 쓴다 — [RERUN.md](RERUN.md) |
| 격자가 전부 `⟲` | 재시작점이 그 키의 단계보다 뒤다. 재시작점을 `(자동)` 으로 두거나 더 앞으로 |
| 셀이 전부 `★` 인데 값 차이가 안 보인다 | LLM 이 매번 다른 답을 낸다. **반복** 을 2 이상으로 두고 같은 값끼리 비교하거나, LLM 단계를 끄고(`--no-rerank-llm` 등) 검색 단계만 본다 |
| 사이드바에 토글이 안 보인다 | `sidebar_toggles=compact` (기본). 제목 줄의 **전체 보기** 를 누르거나 Pipeline 에서 편집한다 |
| Observability 에 **구조·흐름** 탭이 없다 | 2026-09-19 에 이 페이지로 흡수됐다. 예전 주소(`#observability/arch`)로 들어와도 여기로 온다 |
| 국면 합계 ms 가 단계 ms 합과 다르다 | 건너뛴(`skip`) 단계는 합계에 넣지 않는다. 비중(%)의 분모는 그 실행의 **전체 trace 시간**이라 단계 밖 시간(직렬화·대기)도 포함한다 |
| `(기타)` 국면이 보인다 | 새 단계를 넣고 `architecture.STAGE_PHASE` 에 배치를 빠뜨렸다 (§4.5) |
| 전체 흐름 보기에서 상세가 안 열린다 | 열린다 — 단계를 누르면 그 흐름으로 바뀌면서 오른쪽에 뜬다. 흐름 제목을 누르면 그 흐름만 보는 **블록** 보기로 간다 |

## 9. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/web/static/js/pipeline.js` | 흐름·**페이즈**·블록·상세 렌더, 보기 3종, 시간/토글 옵션, 토글·튜닝·config 편집, 스윕 폼·격자·diff·지난 목록 |
| `llmwiki/web/static/index.html` | `#tab-pipeline` 구조(`#pl-view` · `#pl-last` · `#pl-live` · `#pl-flow-head` · `#pl-phase-nav`), 사이드바 `#sb-toggle-summary` · `#sb-toggles-mode` |
| `llmwiki/architecture.py` | 단계 레지스트리 + **`PHASES`·`STAGE_PHASE`**(상위 단계) — Web·CLI·`arch doc` 이 공유 |
| `llmwiki/web/static/js/observability.js` | 구조·흐름 코드가 **없다**(삭제됨). 이 페이지가 유일한 단계 지도 |
| `llmwiki/web/static/js/core.js` | `TAB_ALIAS` — 없어진 `observability/arch` 주소를 이 페이지로 |
| `llmwiki/web/static/js/core.js` | `sidebar_toggles` 상태·저장·프로파일 왕복, 요청 오버라이드(`setTuningOverride`/`tuningOverrides`), 요약 갱신 |
| `llmwiki/web/static/style.css` | `.pl-*` 3열 그리드·블록·격자·diff, compact 모드 |
| `llmwiki/profiles.py` | 프로파일 허용 키에 `sidebar_toggles` |
| `llmwiki/architecture.py` | 단계 레지스트리(이 화면이 그리는 원본) |
| `llmwiki/web/server.py` | `GET /api/architecture` · **`/api/limits`**(단계별 시간 제한) · `/api/tuning` · `/api/config` · `/api/sweep/keys` · `/api/sweep` |
