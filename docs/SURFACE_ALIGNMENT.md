# SURFACE_ALIGNMENT — CLI · Web UI · MCP 정렬을 어떻게 보장하나

> 요청: *"현재 수정 및 code base 기준으로 web ui, mcp, cli 기능과 동작이 모두 align 되어 있는지 반드시 빠짐없이 모두 확인해줘."*
> 결론부터: **코드에 있는 CLI 명령 48 · Web 경로 112 · MCP 도구 20 이 이제 전부 대조표 안에 있고, 세 창구를 실제로 띄워 돌린 동등성 검사 54건이 모두 통과한다.**
> 감사 중에 **찾아 고친 것 3가지**(그중 하나는 권한이 창구마다 달랐던 것)와 **의도적으로 남긴 비대칭 목록**이 §3·§4 에 있다.

관련 문서: [MCP.md](MCP.md)(도구 계약) · [WEB_UI.md](WEB_UI.md)(화면) · [CLI_FLOWS.md](CLI_FLOWS.md)(명령 흐름) · [TESTING_GUIDE.md](TESTING_GUIDE.md)(무엇을 고치면 무엇을 돌리나)

## 0. 정렬이란 무엇이고, 왜 세 겹으로 보나

"정렬돼 있다" 는 말은 세 가지 다른 뜻으로 쓰인다. 이 감사는 셋을 갈라서 본다.

| 층 | 묻는 것 | 지키는 것 | 깨지면 |
|---|---|---|---|
| **① 존재** | 기능마다 CLI 명령·Web 경로·MCP 도구가 있는가 | `tools/verify/verify_surface_align.py` (정적) | "CLI 에서는 되는데 화면에 없다" |
| **② 전수** | 코드에 있는 것이 **빠짐없이** 표에 들어 있는가 | 같은 파일의 역방향 검사 (2026-09-20 신설) | 표에 안 적은 기능은 **검사 자체가 안 된다** |
| **③ 동작** | 같은 입력에 **같은 답**을 주는가 | `tools/verify/verify_tri_surface.py`(실제 3프로세스, 신설) · `tests/test_surface_consistency.py`(엔진 동일성) | 화면에서 본 근거와 LLM 이 받은 근거가 다르다 |

②가 이번 감사의 핵심이다. 그 전까지 하네스는 **사람이 적은 표**를 기준으로만 대조했다. 표에 줄이 없으면
검사 대상이 아니었으므로 "표에 없으니 통과" 가 성립했다 — 그리고 실제로 그런 것이 36개 있었다(§2).

## 1. 지금 상태 (요약)

```
$ python tools/verify/verify_surface_align.py
기능 77개 · CLI 명령 48 · Web 경로 112(표에 기재 98) · MCP 도구 20
세 창구 모두 제공: 23개 · MCP 는 읽기 전용이라 의도적으로 뺀 것: 53개
전수 대조: CLI 48/48 · Web 112/112 · MCP 20/20 가 표에 들어 있다
RESULT OK

$ python tools/verify/verify_tri_surface.py
검사 54건 · 통과 54 · 실패 0 · 4.9s
RESULT OK
```

전수 목록 자체를 보고 싶으면 `python tools/verify/verify_surface_align.py --inventory`
(명령별 하위 동작 choices 까지 찍는다).

## 2. 표 밖에 있던 36개

역방향 검사를 켜자 **CLI 명령 3개 · Web 경로 33개**가 표 밖으로 드러났다. 하나하나 확인한 결과
"기능이 없는 것" 은 하나도 없었고 — **표가 따라가지 못한 것**이었다. 세 갈래였다.

**(a) 같은 기능이 여러 경로로 갈라져 있었다** — 표는 대표 경로 하나만 적고 있었다.

| 기능 | 표에 있던 것 | 실제로 더 있던 것 |
|---|---|---|
| 문서 | `/api/doc_chunks` | `/api/docs`(목록) · `/api/doc`(한 건 전체) |
| 포렌식 | `/api/forensic` | `/api/forensics`(목록) · `/api/forensics/summary` |
| 지난 요청 | `/api/requests` | `/api/request`(한 건, `brief=1`) |
| 단계 재실행 | `/api/query/rerun` | `/api/rerun`(재시작점 표) |
| Trial | `/api/trials` | `/api/trial`(한 건) |
| 위키 | `/api/wiki/list` | `/api/wiki/page` |
| 자가진화 | `/api/evolve/proposals` | `/propose` `/apply` `/reject` `/review` |
| 질의 로그 | `/api/queries` | `/api/query_trace` · `/api/query_users` |
| 최적화 | `/api/optimize/bundle` | `/api/optimize/guide` |
| 코퍼스 | `/api/corpus/lint` | `/api/corpus/types` |
| 설정 | `/api/config` | `/api/env` |
| 진행 중 작업 | `/api/activity` | `/api/progress` · `/api/jobs/` |

→ 표의 칸이 **이름을 공백으로 여러 개** 담을 수 있게 바꾸고(첫 번째가 대표), 갈라진 경로를 한 줄로 묶었다.

**(b) 줄이 아예 없던 기능 6개** — 셋은 CLI 명령까지 통째로 빠져 있었다.

| 기능 | CLI | Web | MCP |
|---|---|---|---|
| 규모 추정(목표 문서 수 → 디스크·시간·토큰) | `system` | `/api/system` | – (가정을 넣어 계산하는 것) |
| 융합 방식 비교 | `fusion` | `/api/fusion/compare` | – (설정 실험) |
| 시간 표현 해석 | `time` | `/api/time` | – (`wiki_inspect` 안에 같은 값) |
| 로그인·세션·역할 미리보기 | `users` | `/api/auth/*` 5개 | – (MCP 는 API 키) |
| 보안 정책·감사 로그 | `security` | `/api/security` `/api/audit` | – (감사 기록은 admin 전용) |
| headless 에이전트 정의 | – (파일 직접) | `/api/agents` | – |

**(c) Web 에만 있는 것이 맞는 기능 4개** — 없는 것이 아니라 **다른 창구에 뜻이 없는** 것이다.

| 기능 | 왜 Web 뿐인가 |
|---|---|
| 계정별 화면 프로파일 `/api/profile` | 저장 대상이 그 사람의 **화면 상태**다(`data/profiles.json`) |
| 테마 목록 `/api/themes` | 화면 표현 |
| 화면에서 CLI 실행 `/api/cli` | **터미널을 못 여는 사람**을 위한 창구. 권한은 우회되지 않는다(같은 등급표·확인 문구·비밀번호) |
| 협업 채팅·게시판 `/api/collab` | 토글 `collab` 로 끄는 부수 기능 |

이제 이 판단들이 **표의 `note` 칸에 글로 남아 있고**, 비워 둔 칸에 이유가 없으면 하네스가 실패한다.

## 3. 감사 중에 찾아 고친 것

### 3.1 같은 입력을 CLI 만 받아들였다 (`rules explain ""`)

| 창구 | 빈 용어를 주면 |
|---|---|
| Web `GET /api/query_rules/explain?term=` | **400** + "term 이 필요합니다" |
| MCP `wiki_rules(action=explain, term="")` | **isError** + 같은 뜻의 메시지 |
| CLI `rules explain ""` | **종료코드 0** + "없는 용어" 결과 |

원인은 한 줄이었다 — CLI 는 `if not ns.args:` 로만 봤고, `""` 를 **준** 경우 `ns.args` 는 `[""]` 라
"인자가 있다" 로 통과했다. 스크립트에서 변수가 비어 그대로 넘어가는 흔한 상황인데, 그때 CLI 만 0 을
돌려주므로 호출한 쪽은 성공으로 읽는다. `" ".join(ns.args).strip()` 으로 고쳤다
([llmwiki/cli.py](../llmwiki/cli.py) `rules explain`). 하네스 §13 이 세 창구의 **거절**을 함께 본다.

> 정렬은 성공 경로만의 이야기가 아니다. **실패도 정렬돼야 한다** — 한쪽만 조용히 성공하면 그쪽을 쓰는 사람이 오류를 못 본다.

### 3.2 한 창구에서 막아 둔 값을 다른 창구가 내주고 있었다 (`/api/opstats` 의 `users` 절)

정렬은 "같은 기능이 세 창구에 있는가" 만이 아니다. **같은 값에 세 창구가 같은 잣대를 대는가**도 정렬이다.

새로 넣은 운영 통계(§C)의 `users` 절은 "누가 몇 건 질의했나" 를 담는다. 그런데:

| 경로 | 등급 | 실제로 주는 것 |
|---|---|---|
| `GET /api/query_users` | **admin 전용** (코드 주석: *"활동 목록보다 민감하다"*) | 사용자별 질의 집계 |
| `GET /api/opstats?sections=users` | `read` (viewer 도 호출 가능) | **같은 값** |

막아 둔 문 옆에 문을 하나 더 낸 셈이었다. MCP `wiki_status(full=true)` 도 같았다.

고친 방식은 **내보내는 자리에서 한 번, 명시적으로** 거르는 것이다 — `opstats.redact(d, admin=…)` 를
Web 핸들러와 MCP 도구가 응답 직전에 부른다. 집계 함수(`collect`) 안에 넣지 않은 이유는 CLI 도 그것을
쓰기 때문이다. CLI 는 이미 `security.json` 등급표로 실행자를 검사한 뒤라 다시 판단할 필요가 없고,
판단을 안쪽에 두면 "역할을 넘기지 않은 호출이 조용히 전부 보여 주는" 쪽으로 기울기 쉽다.

가린 사실 자체는 숨기지 않는다 — 응답의 `redacted` 에 어느 절이 왜 빠졌는지가 담긴다.
회귀는 `tests/test_opstats.py`(5건)와 `verify_web.py`(게스트 ↔ admin 두 줄)가 지킨다.

> 얻은 규칙: **경로의 등급만 보고 끝내지 않는다.** 같은 값을 주는 다른 경로가 더 엄격하면 그쪽이 기준이다.
> [SECURITY.md §2.2](SECURITY.md) 에 admin 전용 조회 목록과 함께 적어 두었다.

### 3.3 비교가 빈 채로 통과하던 자리

`tests/test_surface_consistency.py` 에 붙인 새 비교 중 두 개가 **양쪽 다 `None`** 이라 통과하고 있었다.

- 시간 표현 비교가 `start`/`end` 를 봤는데 파서는 `from`/`to` 를 준다 → 양쪽 `None` == `None`.
- 제안 설명 비교가 `structuredContent.id` 를 봤는데 `wiki_propose` 는 번호를 본문 JSON 으로 준다 → `skipTest` 로 조용히 넘어갔다.

둘 다 "값이 있어야 한다" 를 먼저 주장하도록 고쳤고, 새 하네스의 `same()` 도 **비교 대상이 하나뿐이거나
비어 있으면 실패**로 본다. 빈 비교는 통과가 아니다.

## 4. 의도적으로 남긴 비대칭 (그리고 그 이유)

MCP 도구는 20개뿐이다. 나머지 53개 기능을 MCP 에 두지 **않은** 것은 누락이 아니라 결정이고, 이유는 네 가지다.

| 이유 | 해당 | 예 |
|---|---|---|
| **붙은 LLM 에 쓰기 권한을 주지 않는다** | 빌드·초기화·적용·설정 변경 | `build` · `reset` · `evolve apply` · `config` · `models` · `agents` |
| **HITL 이 깨진다** — 자기 설정을 스스로 평가·강화 | 평가·trial·메모리·규칙 편집 | `eval` · `trial` · `memory` · `rules add` |
| **비싼 배치는 사람이 시작한다** | 평가·trial·스윕 배치 | `eval --matrix` · `trial run` |
| **관측·운영 화면이라 대상이 사람이다** | 감사 로그·서버 제어·프로파일·테마 | `/api/audit` · `server block` · `/api/profile` |

MCP 가 **받는** 쪽 제약도 함께 확인했다: 문서 접근 제어(docacl)는 `wiki_query`·`wiki_search`·`wiki_doc`·`wiki_related`
네 도구 모두에 적용되고, `wiki_doc` 은 id 로 바로 읽는 우회로가 막혀 있다([llmwiki/mcp.py](../llmwiki/mcp.py) `_af.doc_ok`).

### 4.1 창구마다 **모양**이 다른 것은 정상이다

같은 값을 주더라도 표현은 다르다. 이것을 어긋남으로 세지 않는다.

| 도구 | 본문(`content[0].text`) | `structuredContent` |
|---|---|---|
| `wiki_doc` | 사람(LLM)이 읽는 **글** — 머리·메타·본문·관계 | 없음 |
| `wiki_graph_rules` | 전문 JSON | **요약**(`n_types` 등) |
| `wiki_status` | 전문 JSON(`stats`, `ops`) | 없음 |
| `wiki_propose` | 번호와 안내 | 없음 |
| `wiki_query`·`wiki_search`·`wiki_inspect` | 표/JSON | **전문** |

그래서 동등성 비교는 **투영(projection)** 으로 한다 — 문서 수·청크 id 목록·인용 매핑·정규화형처럼
"다르면 같은 시스템이 아닌" 값만 본다. 시각·요청 번호·ms·표현 형식은 달라도 된다.

### 4.2 남겨 둔 관찰 (고치지 않음)

- **채널 검색 결과 행에 `doc_type` 이 없다.** 유형 필터는 세 창구에서 똑같이 동작하지만(하네스 §6),
  행 자체는 유형을 들고 있지 않아 화면이 문서 목록으로 따로 이어 붙인다. 지금 동작에 문제는 없어 그대로 두되,
  행에 유형을 실으면 세 창구가 모두 자기 설명적이 된다 — 다음에 검색 응답을 손볼 때 같이.

## 5. 동등성 하네스가 실제로 비교하는 것

`python tools/verify/verify_tri_surface.py` — **빌드 → `serve` 기동 → CLI 프로세스 실행 → 같은 서버의 `POST /mcp` 호출**
을 임시 폴더(mock LLM · hash 임베더 · 캐시 off)에서 한다. 5초.

| # | 기능 | 비교하는 값 |
|---|---|---|
| 1 | 색인 상태 | `docs` · `chunks` |
| 2 | 운영 통계 | `index.docs` |
| 2.5 | **운영 통계 추세** | 일·주·월 각각의 묶음 이름과 구간 수 · 묶음이 굵으면 기간도 넓어지는지 |
| 2.6 | **포렌식 목록** | CLI `--only problems` ↔ Web `?only=problems` 가 같은 기록 id · 기본 보기에 정상 건이 섞이지 않는지 · 요약 건수가 페이지가 아니라 전체를 세는지 |
| 3 | 질의 해부 | `normalized` · `tokens` · `keywords` · 시간 범위 |
| 4 | 시간 표현 | `from` · `to` · `kind` |
| 5 | 채널 검색 | chunk_id 목록 · `mode` |
| 6 | 문서 유형 필터 | 필터 후 chunk_id 목록 · **실제로 걸렀는지** · 남은 행이 모두 그 유형인지 |
| 7 | 엔티티 | 이름으로 열었을 때의 entity_id |
| 8 | 문서 상세 | 청크 수 · 같은 문서를 여는지 |
| 9 | 그래프 빌드 규칙 | 엔티티 유형 목록 |
| 10 | 질의 규칙 설명 | 무엇으로 퍼지나 · 정규화형 |
| 11 | 질의 | **인용 `[C#]` → chunk_id** · 답변 본문 · `result_type` |
| 12 | 요청 단위 오버라이드 | `output_mode=fused` 의 `result_type` |
| 13 | **실패 정렬** | 빈 용어를 세 창구가 모두 거절하는지 |

왜 프로세스를 진짜 띄우나: 단위 테스트는 `p.query(...)` 를 직접 불러 "CLI 도 이렇게 부를 것이다" 를
전제한다. 어긋남은 대개 **그 전제가 깨질 때** 생긴다 — CLI 가 기본값을 하나 더 얹거나, `--json` 출력에
안내 줄을 섞어 파싱을 깨뜨린다(2026-09-20 `trial run --source queries` 에서 실제로 있었다).
그래서 `cli_json()` 은 **`--json` 앞에 다른 출력이 섞이면 그 자체로 실패**로 본다.

### 5.1 하네스가 빈 통과를 하지 않는다는 증명

`stats --json` 의 문서 수만 999 로 바꾼 뒤 돌려 확인했다.

```
FAIL 상태 docs    창구별 값이 다르다: {"cli": 999, "web": 38, "mcp": 38}
검사 54건 · 통과 53 · 실패 1
```

정렬표의 역방향 검사도 같은 방법으로 확인했다 — 줄 하나를 지우면
`Web 경로 /api/themes 가 정렬표에 없다`, 비고를 지우면 `CLI 칸을 비웠는데 이유(note)가 없다` 가 뜬다.

§2.6(포렌식)은 **빈 목록끼리 비교**하고 있던 적이 있다 — 갓 세운 색인에는 포렌식 기록이 없기 때문이다.
지금은 비교 전에 질의 두 건을 던져 기록을 만들고, 기록이 0이면 그 자리에서 실패한다.

## 6. 새 기능을 넣을 때 해야 하는 것

새 명령·경로·도구를 만들면 **정렬표에 줄을 더하기 전까지 `verify_surface_align.py` 가 실패한다.** 순서는:

1. `tools/verify/verify_surface_align.py` 의 `CAPS` 에 줄을 더한다 — 세 칸을 채우거나, 비우고 **이유를 `note` 에 쓴다**.
2. 세 창구 모두에 있는 기능이면 `verify_tri_surface.py` 에 비교를 더한다 (투영은 "다르면 같은 시스템이 아닌" 값으로).
3. MCP 도구를 늘렸으면 [MCP.md](MCP.md) 에 적는다 (하네스가 대조한다).
4. `python tools/verify/verify_all.py` — `align` · `tri_surface` 두 줄이 OK 여야 한다.

## 7. 실행

```bat
python tools/verify/verify_surface_align.py              :: ①존재 + ②전수 (정적, 1초)
python tools/verify/verify_surface_align.py --inventory  :: 코드에서 뽑은 전수 목록
python tools/verify/verify_surface_align.py --md         :: 표를 마크다운으로
python tools/verify/verify_tri_surface.py                :: ③동작 (실제 3프로세스, 4초)
python -m unittest tests.test_surface_consistency        :: ③동작 (엔진 동일성, 17건)
python tools/verify/verify_all.py                        :: 전부
```
