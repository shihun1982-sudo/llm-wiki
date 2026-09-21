# RELEASE NOTES — 버전별 변경 요약

> 버전 문자열은 `llmwiki/__init__.py` 의 `__version__` 하나이며 `python -m llmwiki --version` · `GET /api/status` 의 `version` ·
> MCP `initialize` 응답의 `serverInfo.version` 이 모두 그 값을 낸다. 이 문서의 **맨 위 절이 현재 버전**이다.
> 각 절은 "무엇이 바뀌었나 → 운영자가 할 일(설정 파일에 추가된 키) → 상세 문서" 순서다. 설계 근거는 해당 날짜의 `IMPLEMENTATION_PLAN_*.md`,
> 검증 결과는 `VERIFICATION_*.md` 에 있다.

## 3.3.0 — 2026-09-20 (화면 사용성: Trial 비교 · 포렌식 · 운영 통계 추세 차트)

실제로 화면을 써 본 뒤 나온 지적 세 가지를 고쳤다. 공통점은 **기능은 있는데 화면이 볼 것을 보여 주지
않았다**는 것이고, 셋 다 **숫자를 만드는 쪽**에서 고쳤다 — 화면에서만 가리면 CLI·MCP 와 다른 말을 하게 된다.

| 지적 | 실제로 무엇이었나 | 고친 것 | 문서 |
|---|---|---|---|
| "Trial 비교가 실제 데이터를 비교하는 게 맞나?" | 질의 이력으로 돌린 trial 이 **`hit@k 0.000`** 으로 저장돼 완전히 실패한 설정처럼 보였다. 채점 엔진은 기대 문서가 없으면 "못 맞혔다" 로 세는데, 실제 질의에는 정답이 없다 | 정답이 없으면 **`—`(채점 불가)** 로 저장·표시(저장·읽기 양쪽에 같은 규칙, 옛 기록도 포함). 목록에 **원천** 칸, 원천이 다른 비교는 **경고**, 비교 화면에 *"읽을 수 있는 지표"* 명시, **`A/B 비교 (기준↔변경) 한 번에`** 버튼 | [EVAL_TRIAL.md §5.1](EVAL_TRIAL.md) |
| "포렌식을 의도에 맞게" | 기록 **1,122건 중 1,025건이 정상 건**이라 최근 60건 목록이 정상 건으로 덮였다 — "왜 부실했나" 를 보러 온 사람이 찾는 줄을 하나도 못 봤다 | 기본 보기를 **문제 건만**으로(`insufficient·weak·expectation·error`). 판정 칩(전체 건수 포함)·질의문 검색·문제 비율·소견이 잡힌 단계. CLI `forensic list --only\|--q` 가 같은 기준 | [FORENSIC.md §1.1](FORENSIC.md) |
| "빌드·질의 통계에 주간·월간 차트가 있으면" | 한 시점 값만 있어 **"나아지나 나빠지나"** 에 답할 수 없었다 | `trend` 절 신설 — 구간별 질의량·지연(p50/p95)·질의당 토큰·빌드·근거 부족률 + 직전 구간 대비 ▲▼. Web 은 **일간·주간·월간 버튼과 차트 3종**(외부 라이브러리 없이 inline SVG), 터미널은 스파크라인, MCP 는 `bucket` 인자 | [OPS_STATS.md §2.1](OPS_STATS.md) |

기간은 묶음에 맞춰 자동으로 넓어진다(일 14일 · 주 12주 · 월 1년) — 월간을 7일치로 그리면 막대가 하나뿐이라 뜻이 없다.

### 같은 날 다시 고친 것 (화면을 실제로 보고)

| 무엇 | 왜 |
|---|---|
| **차트를 다시 그렸다** | 첫 판은 좌표계를 `preserveAspectRatio="none"` 으로 가로 15배·세로 4배 늘려서 **점이 타원이 되고 선이 면으로 칠해졌다**(CSS 의 계열 색이 `fill:none` 을 덮어썼다). 축·눈금선도 없어 값을 읽을 수 없었다. 지금은 비율을 유지해 그리고 **왼쪽·오른쪽 두 축**을 둔다 — 질의 수(364)와 빌드 횟수(15)를 한 눈금에 올리면 한쪽이 바닥에 깔린다. 값이 없는 구간은 선을 잇지 않는다 |
| **비교에 쓸 과거 질의를 고른다** | `--source queries` 는 기간·건수로 **뭉뚱그려** 가져갔다. 비교하고 싶은 질의는 대개 *"이 세 질문"* 처럼 정해져 있는데 고를 방법이 없어, 비교가 관심사와 상관없는 문항 위에서 돌았다. CLI `trial candidates`(번호·시각·👍/👎·근거 못 찾음) → `trial run --pick 773,772` · Web 은 Quality › Trial 비교의 **`질의 고르기…`** 체크박스 목록 |

새 경로 `GET /api/eval/candidates`, 새 CLI `trial candidates` · `trial run --pick`. 없는 번호는 **조용히 버리지 않고** `missing` 으로 알린다.

| 또 고친 것 | 왜 |
|---|---|
| **문항 원천 기본이 `실제 질의 이력`** (CLI·Web 모두) | 대개 알고 싶은 것은 "진짜로 물어본 질문에서 좋아졌나" 이고, 이 저장소에서는 평가셋 자체가 코퍼스에 색인돼 hit@k 가 오염돼 있다. 쓸 만한 이력이 없으면 **평가셋으로 물러나되 어느 원천인지 알린다** |
| **`--only insufficient` 필터가 죽어 있었다** | `query_log.scores` 에 없는 키(`answer_mode`)를 보고 있어 **항상 0건**이었다. 실제 필드는 `verdict` — 고치고 `weak`(근거 약함) 선택지를 더했다 |
| 거르기를 걸면 **넓게 훑는다** | 문제 질의는 드물어 최근 200건 안에 없을 수 있다 → "그런 질의가 없다" 고 잘못 답했다. 필터가 있으면 2,000건까지 |
| 후보 목록에 **판정·근거** 표시 | `ms` 는 `query_log` 에 없어 늘 빈 칸이었다(화면이 고장난 것처럼 보였다). 실제로 있는 `verdict`·`groundedness` 로 바꿨다 |
| Web 에 **`다음:` 안내 줄** | 비교는 "고른다 → 설정을 바꾼다 → 두 번 돌린다 → 비교" 인데 고르고 나서 무엇을 누를지 알 수 없었다. 상태에 맞춰 할 일 하나를 말해 준다 |

### 앙상블로 돌았는지 **보인다** (2026-09-20)

`answer_llm` 단계에 `"model": "llama3.1+llama3.1+llama3.1"` 만 남아, 앙상블로 돌았는지를 **`+` 로 유추**해야 했다.
멤버가 몇 개 성공했는지·누가 느렸는지·취합에 얼마를 더 썼는지는 결과 안에만 있고 trace 에 올라오지 않았다.
이제 `answer.summarize_ensemble()` 이 단계 meta 에 요약을 싣고 세 창구가 같은 값을 보여 준다 —
Web 은 그 줄 오른쪽에 **`앙상블 3/3+취합`** 글씨(배경·테두리 없는 파란 글씨 — 알약 모양이면 워터폴에서
막대 조각처럼 읽혀 "단계가 이어지지 않는다" 로 보인다)와 펼쳤을 때 **멤버별 표**(모델·프로바이더·시간 막대·ms·토큰·실패 사유),
CLI `query --trace` 는 멤버별 줄, MCP 는 응답 trace 의 같은 `meta.ensemble`.
막대는 **가장 느린 멤버 기준**이다 — 멤버는 병렬이라 ms 를 더하면 "3배 걸렸다" 로 잘못 읽힌다.
멤버 본문은 싣지 않는다(trace 는 요청마다 저장되므로 크기가 배로 는다).
**역할 11개 어디에 켜도 보인다** — 요약을 프로바이더 한 곳(`providers._note_ensemble` → `profiler.note_current`)에서
지금 열려 있는 단계에 적기 때문이다. 호출 자리마다 넣었다면 한 자리만 빠뜨려도 그 역할은 계속 "알 수 없는" 상태였다. — [ENSEMBLE.md](ENSEMBLE.md)

### 세 창구 정렬 · 검증
- `verify_tri_surface.py` 에 **§2.5 추세**(세 묶음 × 3창구)와 **§2.6 포렌식 목록**(CLI ↔ Web 같은 목록, 기본 보기에 정상 건이 섞이지 않는가)을 더해 **58건**으로(§2.7 과거 질의 후보 포함).
- `verify_buttons.py` 의 `btn-ops` 는 이제 **차트 SVG 와 일/주/월 버튼이 실제로 그려졌는지**까지 본다. `btn-fx-refresh` 는 **판정 칩**이 그려졌는지 본다 — 숫자 표만 남거나 목록이 옛 방식으로 돌아가면 실패한다.
- `tests/test_quality_ux_0920.py` **30건** (채점 불가 처리 · 포렌식 필터 · 추세 묶음/기간/스파크라인/읽기 전용).

### 운영자가 할 일
없다. 설정이 늘지 않았고 저장 형식도 그대로다(옛 trial 기록은 **읽을 때** 보정된다).
`python -m llmwiki stats --full --section trend --bucket week` 로 바로 확인할 수 있다.

## 3.2.0 — 2026-09-20 (요청 14건: 그래프 규칙 스키마 · 관리자 초기화 · 포팅 · Ask 화면 · Trial 원천 · 근거 링크 · 운영 통계 · 세 창구 정렬 감사)

설계: [IMPLEMENTATION_PLAN_0919.md](history/2026-09-19/IMPLEMENTATION_PLAN_0919.md) — 항목마다 조사한 사실 · 택하지 않은 대안 · 검증이 적혀 있다.

### 지식 규칙 · 빌드
| 변경 | 요약 | 문서 |
|---|---|---|
| **그래프 규칙 스키마** | `data/rules.json` 에 `schema` 절 — 타입·관계 어휘를 정하고 `uses`/`utilizes` 를 표준 이름으로 모으며 `inverse` 로 `fixes`↔`fixed_by` 를 한 번만 적는다. 어휘 밖 이름은 `on_unknown` 정책대로 처리하고 **빌드 보고서에 남는다**(예전에는 LLM 이 낸 `type`/`rel` 에 검증이 전혀 없었다) | [GRAPH_RULES.md](GRAPH_RULES.md) |
| **값 종류 레지스트리** | `relation_patterns[*].value` 에 `measure`(`4 ns`→`metric` 노드)·`version`(`rev B1`) 추가. 새 종류는 `register_value_type()` 한 번이면 CLI·Web·MCP 설명에 함께 나타난다 | 〃 |
| **빌드 전 정적 점검** | `graph-rules lint`(깨진 정규식·없는 유형·**가려진 `link_rules`**·겹치는 별칭·`inverse` 짝) · `graph-rules test "<문장>"` · Web 지식 › 그래프 규칙 · MCP `wiki_graph_rules` | 〃 |
| **빌드 중 서비스 (결함 수정)** | `reads_during_build=always` 가 **전체 리빌드에서 무효**였다(완화 분기가 `weight == "write"` 만 봤는데 리빌드는 `"exclusive"` 로 들어온다). 고친 뒤 같은 조건에서 빌드 중 질의 54건 → **893건**, p95 5,548ms → **376ms** | [BUILD_UNDER_LOAD.md](BUILD_UNDER_LOAD.md) |

### 포팅 · 관리
| 변경 | 요약 | 문서 |
|---|---|---|
| **관리자 초기화 3종** | `reset data\|settings\|logs [--apply]` · `POST /api/reset` · Web 설정 › 시스템. **미리보기가 기본**(지우는 목록·유지하는 목록·용량), `corpus/` 원본과 `security.json`·`.env` 는 기본으로 지키며 data 범위는 자동 스냅샷 | [RESET.md](RESET.md) |
| **설정 한 폴더로** | `config bundle --out conf \| --from conf` + `LLMWIKI_CONF_DIR` — 파일을 **옮기지 않고 가리킨다**(옮기면 기존 설치·문서·예시 경로가 전부 깨진다). `config paths` 가 파일별 실제 출처를 보여 준다 | [PORTING.md](PORTING.md) |
| **LLM 연결 샘플** | API 게이트웨이+PAT · Anthropic 호환 · 로컬 Ollama · opencode 전부 · **역할별 혼합** 다섯 가지. 전환은 `llm_provider`·`llm_model` 두 줄이고 코드는 고치지 않는다 — 하네스 `verify_llm_switch.py` 가 18개 검사로 그것을 지킨다 | [LLM_CONNECT.md](LLM_CONNECT.md) |

### 화면 · 품질
| 변경 | 요약 | 문서 |
|---|---|---|
| **Ask 채널 검색** | 채널 조건표 · 문서 유형 칩(**거르는** 조건) · 최근 검색어 · 발췌 강조. CLI `search --doc-types` · MCP `wiki_search(doc_types=)` 와 같은 엔진 | [WEB_UI.md §0.67](WEB_UI.md) |
| **Ask 디버그 = 실제 질의** | 해부가 실제 질의와 **같은 순서**로 돌도록 고쳤다(시간 표현을 먼저 떼고 확장·라우팅·pin). 채널 라우팅 유형(`relational` 등)과 낱개 도구 줄에 설명을 붙이고 그 자리에서 시험할 수 있게 했다 | 〃 |
| **근거 → 원본 연결** | 답변의 근거 문단 `[C1]` 과 그래프 관계 표의 항목을 누르면 원본 문서로 간다. `GET /api/entity?name=` 을 열어 화면이 엔티티 id 규칙을 따로 만들지 않게 했고, 이때 **한 번도 정의되지 않은 채 불리던 `LW.openEntity`** 를 찾아 고쳤다 | [WEB_UI.md §0.675](WEB_UI.md) |
| **Trial 문항 원천** | `trial run --source evalset\|queries [--days --limit --only negative\|feedback\|insufficient]` — **실제 질의 이력**으로 비교할 수 있다. 비교 결과에 단계별 표(질의 1건당 ms·토큰·호출수, 달라진 단계 우선)와 "이 지표는 왜 못 내는가" 가 함께 나온다 | [EVAL_TRIAL.md §5](EVAL_TRIAL.md) |
| **제안 설명(HITL)** | `evolve show <id>` · Web 제안 카드 · MCP `wiki_evolve(id=)` 가 **무엇이 · 어느 파일에서 · 어떻게(before/after) · 영향 · 리빌드가 드는가 · 왜 적용 못 하는가**를 한 화면에. 적용 불가 제안은 평가 전에 걸러 낸다(124건 중 43건이 그랬다) | [EVOLVE.md §1.5](EVOLVE.md) |

### 운영 · 검증
| 변경 | 요약 | 문서 |
|---|---|---|
| **운영 통계** | `stats --full [--days --section --top]` · `GET /api/opstats` · `wiki_status(full=true)` — 단계별 빌드 ms · 질의 시간대/창구 분포 · p50/p95 와 가장 느린 질의 · 토큰 · 근거 부족률 · 폴더별 용량과 **정리 힌트** · 임베딩 캐시 적중률. 읽기 전용 | [OPS_STATS.md](OPS_STATS.md) |
| **세 창구 전수 정렬 감사** | 정렬 검사의 기준을 **표에서 코드로** 옮겼다 — 이제 CLI 명령·Web 경로·MCP 도구를 만들고 대조표에 줄을 더하지 않으면 실패한다(그 전에는 36개가 표 밖에 있었다). 새 하네스 `verify_tri_surface.py` 가 `serve`·CLI 프로세스·`POST /mcp` 를 **실제로 띄워** 42건을 비교한다 | [SURFACE_ALIGNMENT.md](SURFACE_ALIGNMENT.md) |
| **문서 재배치 — 현행 / 기록 분리** | `docs/` 바로 아래 = **지금의 사실**(날짜 없는 이름, 낡으면 고친다) · `docs/history/<날짜>/` = **그날의 사실**(불변). 기록 26개를 회차 폴더로 옮기고 참조 70개 파일을 맞췄다. 구조를 설명하는 현행 문서는 [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) **하나로 통합**(핵심 용어 사전과 "한 질의의 여정" 을 흡수). 검증 결과는 [VERIFICATION.md](VERIFICATION.md) 로 옮겨 `verify_all.py` 가 **날짜 박힌 회차 보고서를 덮어쓰던 것**을 멈췄다. 입구는 [DOC_MAP.md](DOC_MAP.md), 회차 색인은 [history/README.md](history/README.md) | [DOCS_REORG_0920.md](history/2026-09-20/DOCS_REORG_0920.md) |
| **문서가 낡으면 실패하게** | `verify_docs.py` 에 네 검사 추가 — ①현행 자리에 날짜 붙은 파일명 금지 ②기록이 회차 색인에 있는가 ③**모든 CLI 명령·MCP 도구·설정 키가 현행 문서에 설명돼 있는가**(기록에 적힌 것은 세지 않는다) ④모든 하네스가 목록에 있는가(켜 보니 25개 중 14개가 빠져 있었다). 규모 숫자는 `<!--live:키-->` 로 **표시한 것만** 코드와 대조한다 | [TESTING_GUIDE.md](TESTING_GUIDE.md) · [DOC_MAP.md §8](DOC_MAP.md) |
| **MCP 도구 <!--live:mcp-->20개** | + `wiki_graph_rules` · `wiki_evolve`(설명) · `wiki_status(full)` 확장 (모두 읽기 전용) | [MCP.md §2](MCP.md) |

### 고친 결함 (조용히 아무 일도 하지 않던 것들)

이 회차에서 반복해 나온 유형이다 — **설정은 되는데 동작하지 않는 기능**. 각각 재발 방지 장치를 함께 넣었다.

| 결함 | 증상 | 장치 |
|---|---|---|
| `reads_during_build=always` 가 죽은 분기 | 문서가 권하는 설정이 정작 전체 리빌드에서 무효 | 정책 회귀 테스트가 구현 소스를 읽어 분기를 확인 |
| `ask.js` 의 `loaders` 미정의 | 한 줄 오류로 IIFE 전체가 죽어 **모든 핸들러가 등록되지 않음**(버튼·API 검사는 통과하고 있었다) | `verify_browser.py` 의 콘솔 오류 수집 |
| 앙상블 CSS 가 `#tab-models` 에 갇힘 | 마크업은 `#tab-ensemble` 에 있어 설명이 깨져 보임 | `verify_ui_wiring.py` 의 탭 범위 CSS 정적 검사 |
| `LW.openEntity` 미정의 | 문서 페이지의 엔티티 링크가 눌러도 아무 일 없음 | `tests/test_evidence_links.py` |
| 적용 불가 제안 43건 | 회귀 평가까지 돌고 나서야 실패 | `evolve.apply` 앞의 오류 게이트 |
| `rules explain ""` 종료코드 0 | Web 400 · MCP 오류인데 CLI 만 성공을 보고 | `verify_tri_surface.py` §13(실패 정렬) |

### 운영자가 할 일 (기존 환경을 3.2.0 으로 올릴 때)
1. `python -m llmwiki config fill-defaults --all` — 새 키를 기본값으로 파일에 명시한다(있는 값은 유지, `--dry-run` 으로 미리 보기).
2. `python -m llmwiki graph-rules fill-defaults` 뒤 `graph-rules lint` — `data/rules.json` 에 `schema`·`chunk_values` 절이 생긴다. 어휘 밖 관계 이름이 있으면 여기서 보인다.
3. 그래프 규칙의 `schema` 를 넣었다면 **`build graph --full`** 로 그래프 채널만 다시 만든다(전체 리빌드 불필요).
4. `python -m llmwiki stats --full --section storage` — `data/snapshots` 가 색인보다 커져 있으면 `snapshot prune --keep 3`.
5. `python tools/verify/verify_all.py` — `align` · `tri_surface` 두 줄이 OK 여야 한다.

전체 키 표: [BRINGUP_GUIDE.md §3.2](BRINGUP_GUIDE.md) · 회귀 테스트 순서: [TESTING_GUIDE.md](TESTING_GUIDE.md) · 문서 지도: [DOC_MAP.md](DOC_MAP.md)

## 3.1.0 — 2026-09-18 (요청 17건 + 6건: 질의 경로 옵션 · 앙상블 · 스윕 · 그래프 진단 · Pipeline 페이지 · 운영 한도)

설계: [IMPLEMENTATION_PLAN_0918.md](history/2026-09-18/IMPLEMENTATION_PLAN_0918.md) · [IMPLEMENTATION_PLAN_0918_2.md](history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md) · 검증: [VERIFICATION_0918.md](history/2026-09-19/VERIFICATION_0918.md)

### 질의 경로
| 변경 | 요약 | 문서 |
|---|---|---|
| **답변 모드** `answer_mode` | `grounded`(기존) \| `best_effort`(근거가 부족해도 LLM 을 불러 문서 사실 `[C#]` + 배경 지식 `[BK]` 로 답함). 결과에 항상 `result_type` 과 `refs`(LLM 에 실제 전달된 근거 목록) | [ANSWER_MODES.md](ANSWER_MODES.md) |
| **출력 모드** `output_mode` | `answer` \| `fused`(융합·부스트 뒤 후보) \| `reranked`(리랭크 뒤 후보 + 전후 순서) \| `context`(컨텍스트 블록까지만). 클라이언트 LLM 이 중간 산출물을 직접 처리할 때. CLI `query --output` · Web Ask 출력 드롭다운 · MCP `wiki_query(output_mode=)` | 〃 |
| **융합 뒤 LLM 2단계** | 토글 `llm_after_fusion`(역할 `fusion`, `prompts/fusion_review.md`) · `llm_after_rerank`(역할 `select`, `prompts/rerank_review.md`). 기본 off | 〃 |
| **실패 시 대체 경로 토글** `degrade_on_llm_failure` | 기본 true(추출식 답변으로 계속). false 면 `result_type=error`. 재시도·`llm_fallbacks`·`fallback_loop` 와 별개 장치 | 〃 §0.2 표 |
| **채널별 top-k 안/밖 가중** | `tuning.json` `{fts,vector,graph,doc_vector,external}_topk_n/_topk_w/_tail_w` + 리랭크 창 보장 주입 `channel_inject` | [FUSION_TOPK.md](FUSION_TOPK.md) |
| **요청 단위 튜닝 오버라이드** | `overrides.tuning{…}` 가 Web·MCP 에서도 동작 (파일에 남지 않음) | [FUSION_TOPK.md](FUSION_TOPK.md) · [SWEEP.md](SWEEP.md) |
| **규칙 방향** | acronym/synonym 양방향, alias/related/exclude 일방(설계 의도). `related_symmetric`(기본 false) · `rules explain <용어>` · `GET /api/query_rules/explain` · MCP `wiki_rules` | [QUERY_RULES.md](QUERY_RULES.md) |

### LLM 연결
| 변경 | 요약 | 문서 |
|---|---|---|
| **역할 단위 앙상블** | `llm_roles.<role>.ensemble{members[≤3], wait, timeout_s, min_results, aggregator, prompt}` + 전역 `llm_ensemble_defaults`. 병렬 호출 → 취합 LLM(`prompts/ensemble_merge.md`). `models ensemble show|set` · Web 역할 표 "앙상블 ▸" | [ENSEMBLE.md](ENSEMBLE.md) |
| **headless 일반화** | `prompt_mode` 코드 기본 `stdin`, `arg_max_chars`(30000) 가드로 Windows `WinError 206` 방지(자동 전환 + `prompt_mode_fallback` 표시), `env_passthrough` 허용 목록, config.json 만으로 API ↔ headless 전환(카탈로그 provider 자동 해석) | [HEADLESS.md](HEADLESS.md) · [BRINGUP_GUIDE.md §4.3](BRINGUP_GUIDE.md) |
| **카탈로그 전체 연결 테스트** | `models test --catalog [--live]` · `POST /api/models/test_catalog` · Settings › 카탈로그 버튼. `health` 가 provider↔model 짝 불일치를 한 줄로 안내 | [SETTINGS_SYNC.md](SETTINGS_SYNC.md) |

### 도구 · 화면
| 변경 | 요약 | 문서 |
|---|---|---|
| **파라미터 스윕** | `sweep run <id|last> --key K --range a:b:s|--values …` · `/api/sweep` · MCP `wiki_sweep`. 값마다 재실행(rerun) 재생 위에서 그 단계부터만 다시 → 값별 단계 비교 격자 | [SWEEP.md](SWEEP.md) |
| **그래프 진단 프로파일** | `graph profile [--eval] [--compare]` · `/api/graph/profile` · Knowledge › 그래프 진단 · MCP `wiki_graph_profile`. 규모·연결성·커버리지·품질·규칙 기여·질의 활용·제안·이력 비교 | [GRAPH_PROFILE.md](GRAPH_PROFILE.md) |
| **🧭 Pipeline 페이지** | 흐름 세로 블록 + 단계 상세(토글·config·튜닝 편집 두 갈래·스윕 폼·격자). 사이드바 토글은 요약(`sidebar_toggles=compact`)으로, `full` 로 예전 배치 | [PIPELINE_PAGE.md](PIPELINE_PAGE.md) |
| **Web UI 사용성** | 사이드바 블록 접기/펴기(`sidebar_collapsed`), 토글 배지를 이름 뒤로, 단계별/전체 **복사 버튼**(`LW.copyText` 3단 폴백 — http 에서도 동작), 창 폭 360~1920 반응형 점검 | [WEB_UI.md](WEB_UI.md) |
| **설정 ↔ 서버 연동** | `config fill-defaults [--tuning --rules --all --examples --dry-run]`(모든 키를 기본값으로 파일에 명시), `config env` / `GET /api/env`(`.env` 가시성, 마스킹), 카탈로그에서 모델 고르면 provider 자동 채움, 하네스 `verify_settings_sync.py`(양방향) | [SETTINGS_SYNC.md](SETTINGS_SYNC.md) |

### 운영 · 보안
| 변경 | 요약 | 문서 |
|---|---|---|
| **불용어 파일** | `stopwords.json`(루트, 없으면 생성, mtime 재로딩) · `setup/stopwords.example.json` · `LLMWIKI_STOPWORDS_PATH` | [STOPWORDS.md](STOPWORDS.md) |
| **로그 총량 제한** | `log_total_max_mb`(500) · `log_limit_action`(warn\|prune\|stop) · `log_check_interval_s`(60) · `audit_max_mb`(20)/`audit_backups`(5) · `analysis_keep`(200). `logs status` · health `log_quota` | [LOG_QUOTA.md](LOG_QUOTA.md) |
| **기본 외부 바인드** | `web_host` 기본 **0.0.0.0**. 전제로 (a) 요청 단위 `overrides` 화이트리스트(URL·경로·헤더 계열은 admin 화면/CLI 에서만; `security.json overrides.allow_extra/deny`), (b) 500 응답 트레이스 마스킹(`server.json debug.expose_trace`, 기본 false) | [SECURITY.md](SECURITY.md) · [BRINGUP_GUIDE.md §4.4](BRINGUP_GUIDE.md) |
| **MCP 공존·확장성** | params 형 검사(-32600/-32602), `expose:true` 허용 목록, `${ENV}` 치환 순서, annotations 보존, SSE 응답 파싱, stdio 리더 스레드, 플러그인·페더레이션 락, `server.json mcp` 절(`fed_cache_ttl_s` 등 8키) | [MCP.md](MCP.md) |
| **MCP 도구 17개** | + `wiki_sweep` · `wiki_rules` · `wiki_graph_profile` (모두 읽기 전용) | [MCP.md §2](MCP.md) |

### 운영자가 할 일 (기존 환경을 3.1.0 으로 올릴 때)
1. `python -m llmwiki config fill-defaults --all` — config.json · tuning.json · query_rules.json · data/rules.json 에 새 키를 **기본값으로 명시**한다(있는 값은 유지). 무엇이 추가되는지만 보려면 `--dry-run`.
2. `python -c "from llmwiki import headless as hl; hl.save_agents(hl.load_agents())"` — agents.json 에 `prompt_mode`/`arg_max_chars`/`env_passthrough` 명시.
3. `web_host` 가 0.0.0.0 이 되므로 서버 공개 전 [SECURITY.md §7](SECURITY.md) 체크리스트(기본 admin 비밀번호 변경, `security.json mode`) 를 다시 본다. 루프백만 쓰려면 `config set web_host=127.0.0.1`.
4. `python -m llmwiki health` · `models test --live` · `python tools/verify/verify_settings_sync.py` 로 확인.

전체 키 표: [BRINGUP_GUIDE.md §3.2](BRINGUP_GUIDE.md) · 회귀 테스트 순서: [TESTING_GUIDE.md](TESTING_GUIDE.md)

## 3.0.x — 2026-09-15 ~ 2026-09-17 (버전 문자열 도입 전)

`__version__` 이 3.1.0 으로 처음 관리되기 전의 변경은 날짜별 문서에 있다.

| 날짜 | 무엇 | 문서 |
|---|---|---|
| 2026-09-17 | 단계 재실행(rerun) · 작업 상세 · 전면 코드/품질 리뷰와 결함 수정 · `verify_all.py`/`verify_docs.py` | [RERUN.md](RERUN.md) · [ACTIVITY_DETAIL.md](ACTIVITY_DETAIL.md) · [CODE_REVIEW_0917.md](history/2026-09-17/CODE_REVIEW_0917.md) · [QUALITY_REVIEW_0917.md](history/2026-09-17/QUALITY_REVIEW_0917.md) · [VERIFICATION_0917.md](history/2026-09-17/VERIFICATION_0917.md) |
| 2026-09-16 | 요청 이력 · 모델 화면 · 답변 페르소나 · 규칙 확장 라운드 · headless 무응답 대책 · 협업 채팅/게시판 · Web UI 정리 | [IMPLEMENTATION_PLAN_0916.md](history/2026-09-16/IMPLEMENTATION_PLAN_0916.md) · [REQUEST_HISTORY.md](REQUEST_HISTORY.md) · [COLLAB.md](COLLAB.md) · [WEB_UI.md](WEB_UI.md) · [VERIFICATION_0916.md](history/2026-09-16/VERIFICATION_0916.md) · [VERIFICATION_0916_2.md](history/2026-09-16/VERIFICATION_0916_2.md) |
| 2026-09-15 | 다중 사용자 서버화(요청 격리·요청 관리자·역할별 LLM 정책·진행률/취소·스케줄러·모델 목록·프로파일) · 상세 분석 모드 · 다른 RAG 연동 | [IMPLEMENTATION_PLAN_0915.md](history/2026-09-15/IMPLEMENTATION_PLAN_0915.md) · [CONCURRENCY.md](CONCURRENCY.md) · [SCHEDULER.md](SCHEDULER.md) · [ANALYSIS_MODE.md](ANALYSIS_MODE.md) · [RAG_FEDERATION.md](RAG_FEDERATION.md) · [VERIFICATION_0915.md](history/2026-09-15/VERIFICATION_0915.md) |
| 2026-09-14 | 권한 표·API 키·MCP HTTP/브리지·채널별 빌드·doc_expand·LLM 재시도·기대 결과 포렌식 | [IMPLEMENTATION_PLAN_0914.md](history/2026-09-14/IMPLEMENTATION_PLAN_0914.md) · [SECURITY.md](SECURITY.md) · [MCP.md](MCP.md) · [FORENSIC.md](FORENSIC.md) |
| 2026-09-13 | v3 재구현(문서 계약 · provenance · 융합 5방식 · 근거 판정 · claim_check · 자가진화) | [ARCHITECTURE_V3.md](history/2026-09-15/ARCHITECTURE_V3.md) · [IMPLEMENTATION_PLAN_0913.md](history/2026-09-13/IMPLEMENTATION_PLAN_0913.md) |

## 버전 올리는 절차 (다음 회차용)

1. `llmwiki/__init__.py` 의 `__version__` 을 올린다 (기능 추가 = 두 번째 자리, 수정만 = 세 번째 자리).
2. 이 문서 맨 위에 새 절을 쓴다: 변경 표 + "운영자가 할 일" + 새 키의 BRINGUP §3.2 링크.
3. `python -m llmwiki --version` 과 `tools/verify/verify_web.py` 의 `/api/status.version` 검사가 새 값을 확인한다.
