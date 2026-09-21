# tools/verify — 전 기능 검증 하네스

다른 환경에서 bring-up 한 뒤 **모든 CLI 명령·모든 Web 엔드포인트·설정 양방향·UI 배선·브라우저 로드·창 크기**를 자동으로 다시 확인하는 스크립트.
한 줄로 전부 돌리려면 **`python tools/verify/verify_all.py`** (최신 결과 표를 [docs/VERIFICATION.md](../../docs/VERIFICATION.md) §1 에 직접 갱신한다 — 부분 실행은 문서를 건드리지 않는다).
**무엇을 고쳤을 때 무엇을 돌리나** 는 [docs/TESTING_GUIDE.md](../../docs/TESTING_GUIDE.md), **검증 체계와 최신 결과**는 [docs/VERIFICATION.md](../../docs/VERIFICATION.md). 회차별 검증 보고서(그때 무엇을 찾아 고쳤나)는 [docs/history/](../../docs/history/README.md) 아래 날짜 폴더에 있다.
프로젝트 루트에서 실행한다. 모두 표준 라이브러리만 쓰며, 실제 `config.json`/`data/` 를 건드리지 않는다(브라우저 검사만 실제 DB 를 127.0.0.1 전용 서버로 읽는다).

| 스크립트 | 무엇을 | 소요 | 결과 |
|---|---|---|---|
| `python tools/verify/verify_cli.py` | 임시 폴더에 설정·샘플 코퍼스를 복사하고 `LLMWIKI_*_PATH` 로 격리한 뒤 **222개 CLI 명령**을 실제 subprocess 로 실행(빌드·채널 빌드·질의·평가·trial·포렌식 expect·evolve·snapshot·users/security/apikey·권한 게이트·mcp stdio·브리지 오류·server·schedule·models 카탈로그…) | 4~6분 | `CLI 검증: N 명령 중 M 통과` + 명령별 종료 코드/첫 줄, `verify_cli_result.json` |
| `python tools/verify/verify_web.py` | 같은 격리 환경으로 `serve --host 0.0.0.0` 를 띄우고 **255개 요청**: 정적 파일·게스트(viewer) 허용/거부(401)·viewer 403·class1 428→확인·admin 전 POST(설정/프롬프트/규칙/위키/진화/메모리/유지보수/trial/fusion/watch/snapshot/채널·전체 빌드/users/security/apikeys/서버 모니터/스케줄/프로파일)·API 키·`/mcp` 도구·잘못된 키 401·CSRF·작업 취소와 취소 후 복구 | 3~5분 | `WEB 검증: N 요청 중 M 통과`, `verify_web_result.json` |
| `python tools/verify/verify_ui_wiring.py` | 정적 검사: JS 의 `$('#id')` 참조가 index.html/login.html 또는 JS 가 동적으로 만드는 id 에 있는지, 탭↔섹션 대응, JS 가 부르는 `/api/...` 경로가 서버에 있는지 | 1초 | `RESULT OK` |
| `python tools/verify/verify_browser.py` | Edge/Chrome headless 로 `/` 와 `/login` 을 실제 로드(JS 실행) → 콘솔 오류 0, 사용자 배지/상태/탭 31개/신규 요소 렌더 확인. 브라우저가 없으면 SKIP | 20초 | `BROWSER OK` |
| `python tools/verify/verify_settings_sync.py` | **설정 양방향 정합** (2026-09-18): 표면 11종(config · llm_roles · tuning · query_rules · rules.json · models.json · prompts · agents · schedule · server.json · `.env`)마다 ① **UI → 파일 → 유효값**(화면이 쓰는 API 로 POST → 디스크 파일 → GET 과 새 프로세스의 `config show --effective`) ② **파일 → 재적재 → UI**(파일 직접 편집 → mtime 자동 또는 reload API → GET). `config fill-defaults --all` 이 실제로 파일을 채우고 두 번째 실행에서 추가할 키가 0인지까지. 한 방향만 보는 검사는 "저장은 되는데 안 먹는" 상태를 통과시킨다 | 2분 | `검사 N개 중 M개 통과`, `verify_settings_sync_result.json` |
| `python tools/verify/verify_responsive.py` | **창 크기 대응** (2026-09-18): 폭 360·768·1024·1366·1920 × 대표 탭 6종에서 ① 페이지 가로 넘침(`scrollWidth > innerWidth`) 0 ② 화면 밖으로 나간 요소 0(단, `.tbl-wrap` 처럼 **가로 스크롤이 허용된 조상** 안의 넓은 표는 정상 패턴이라 세지 않는다) ③ **가로 스크롤 칸 수 ≤ `--max-scrollers`(기본 2)** — 실제로 스크롤 중인 칸을 세어 "한 화면에 스크롤바가 여러 개" 를 잡는다(2026-09-19 추가) ④ 콘솔 오류 0 ⑤ 960px 미만에서 사이드바가 본문 위로 접히는가. 측정은 페이지에 잠시 주입한 probe 가 `<html data-rsp>` 에 적고 하네스가 회수한다(끝나면 `index.html` 을 반드시 원상 복구). 브라우저가 없으면 SKIP | 2~3분 | `검사 N개 중 M개 통과`, `verify_responsive_result.json` |
| `python tools/verify/verify_live_models.py` | **실제 모델 종단 확인** (2026-09-19): 다른 하네스는 전부 `llm_provider=mock` 이라 "배선은 맞는데 진짜 모델에서는 안 되는" 것(모델 이름 표기 `llama3.1` vs `llama3.1:latest`, 임베딩 차원 불일치, 긴 프롬프트)을 못 잡는다. 이 PC 에서 붙는 것을 **자동 탐지**(Ollama 태그·API 키·headless 실행 파일)해 격리 환경에 진짜 임베딩으로 색인하고, `models test --live` → 질의(grounded / best_effort) → LLM 리랭크·융합 뒤 검토 단계가 실제로 돌았는지 trace 확인 → `eval` 까지 돌린다. **배선 실패**와 **모델 품질 경고**(작은 로컬 모델이 인용을 빠뜨리는 등)를 구분해 품질은 `WARN` 으로만 남긴다. 붙는 모델이 없으면 SKIP(exit 0) | 5~25분 | `검사 N개 중 M개 통과`, `verify_live_models_result.json` · 목록만: `--list` |
| `python tools/verify/verify_monkey.py` | 실제 DB 를 복사한 격리 서버에 **무작위 입력을 동시에 쏟아붓는다** — 무작위 경로·본문·타입, 제어문자·짝 없는 서러게이트·이모지·거대 본문·잘못된 JSON·이상한 HTTP 메서드, MCP JSON-RPC 퍼징, CLI argv 퍼징. 판정은 **500 오류·연결 끊김·CLI traceback 유출이 0 인가**(429·503 은 정상 거절이므로 결함 아님) + 폭격 뒤 서버 생존과 정상 질의 복구 | 3분 | `500 오류: N`, `verify_monkey_result.json` |

실패 행은 `FAIL` 로 시작한다. 기대 종료 코드/HTTP 상태가 스크립트 안에 명령별로 적혀 있으므로, 새 명령·엔드포인트를 추가하면 그 줄을 함께 추가한다.

멍키 하네스 옵션: `--seed <숫자>`(재현), `--real-llm`(mock 대신 실제 프로바이더), `--destructive`(스냅샷 복원·config reset 같은 파괴적 엔드포인트까지 포함), `--keep`(임시 폴더 보존), `--duration <초>`. 포트 8794 를 이미 쓰고 있으면 시작하지 않고 알려 준다 — 예전에 남은 서버 프로세스가 옛 코드를 서빙해 "고쳤는데 결과가 그대로"로 보이는 일을 막기 위한 것이다.

| `python tools/verify/verify_buttons.py` | **Web UI 의 모든 버튼(105개)을 하나씩 실제로 눌러** 본다. 설정·DB 를 임시 폴더로 복사하고 mock LLM 으로 서버를 띄우므로 저장·초기화 버튼을 눌러도 실제 데이터가 바뀌지 않는다. 판정: ① 콘솔/프라미스 오류가 나면 FAIL ② **아무 일도 일어나지 않으면**(서버 호출도·화면 변화도·알림도 없음) FAIL ③ 버튼별 기대 조건(있으면) 불충족 시 FAIL. 입력이 필요한 버튼은 미리 채워 준다 | 6~8분 | `버튼 N개 중 M개 통과`, `verify_buttons_result.json` |
| `python tools/verify/verify_click.py` | **버튼을 실제로 눌러** 화면이 바뀌는지 확인한다(Chrome DevTools Protocol, 표준 라이브러리만). `--dump-dom` 검사는 "그려지는가" 만 보므로, 눌러도 아무 일도 안 일어나는 버튼(핸들러가 조용히 return 하거나 예외로 죽는 경우)은 못 잡는다. 요청 프로파일 행 클릭 · 포렌식 진단 실행 · 질의 로그 trace · 로그 조회 등을 클릭하고 DOM 변화와 콘솔 오류를 본다 | 40초 | `RESULT OK` |
| `python tools/verify/verify_ensemble_ui.py` | **앙상블 편집기(🧭 Pipeline › 앙상블)를 실제 브라우저로 눌러** 본다. `llm_roles.expand.ensemble.enabled=true` + 멤버 3칸 모두 모델 없음 — 즉 "켰는데 앙상블이 안 도는" 상태를 만들어 놓고 시작한다. 확인: ① 멤버 드롭다운에 고를 수 있는 모델이 실제로 있는가(카탈로그를 못 읽으면 칸이 잠긴 것처럼 보인다) ② 빈 칸 라벨이 "상속" 이라고 거짓말하지 않는가(비우면 그 멤버는 빠진다) ③ 멤버 0개일 때 "앙상블이 돌지 않는다" 고 경고하는가 ④ 「쓰기」를 켜면 역할 모델이 자동으로 채워지는가 ⑤ 저장이 **UI → 파일 → 유효값**으로 왕복하는가 | 1~2분 | `N개 중 M개 통과`, `ENSEMBLE-UI OK` |
| `python tools/verify/verify_trace_waterfall.py` | **워터폴(trace) 그림을 픽셀로 재서** 읽을 수 있는지 본다. 합성 trace 를 `LW.renderTrace` 로 그린 뒤 `getBoundingClientRect()` 로 측정: ① 단계 막대가 앞 단계 끝에서 시작하는가 ② 막대 왼쪽 끝이 `offset_ms` 비율과 맞는가 ③ 배지가 오른쪽에 **한 덩어리로** 붙는가(배지마다 `margin-left:auto` 면 flex 가 남는 공간을 배지 *사이에* 나눠 첫 배지가 가운데로 밀린다) ④ 배지가 막대와 같은 색으로 채워져 있지 않은가(막대 조각으로 오해된다). 이 네 가지는 DOM·API·버튼 검사를 모두 통과하면서도 틀릴 수 있다 | 1~2분 | `N개 중 M개 통과`, `WATERFALL OK` |
| `python tools/verify/bench_fts.py` | **전체 리빌드 색인 단계가 청크 수에 비례하는지**(제곱이 아닌지) 측정. `fts_trigram` on/off 를 함께 잰다. 청크를 2배로 했을 때 시간이 2배 근처여야 하고, 4배가 되면 `PROBLEMS` 로 알린다 | 20초 (`--no-old`) | `RESULT OK` |

## 정합 하네스 (정적 · 빠름 — 저장할 때마다 돌려도 된다)

무거운 하네스가 "동작하는가" 를 본다면, 이쪽은 **"같은 것을 여러 곳에 적어 두고 한 곳만 고치지 않았나"** 를 본다.
이 저장소에서 가장 자주 잡히는 결함 유형이다.

| 스크립트 | 무엇을 | 소요 |
|---|---|---|
| `verify_docs.py` | 문서가 주장하는 것을 코드/파일에서 확인 — 링크 · `python -m llmwiki <명령>` · 설정 키/토글 · 저장소 파일 · `/api` 경로 · **`<!--live:키-->` 로 표시한 규모 숫자** · README/BRINGUP 에서 도달 못 하는 **고아 문서** | 1초 |
| `verify_surface_align.py` | CLI·Web·MCP **정렬** 두 방향 — ①표의 이름이 실제로 있는가 ②**코드에 있는 명령·경로·도구가 빠짐없이 표에 있는가**(2026-09-20 추가) ③비워 둔 칸에 이유가 적혀 있는가. `--inventory` 는 코드에서 뽑은 전수 목록, `--md` 는 문서용 표 | 1초 |
| `verify_tri_surface.py` | 세 창구가 **같은 답을 주는가** — `build` → `serve` 기동 → **`python -m llmwiki …` 를 실제 실행** → 같은 서버의 `POST /mcp` 호출. 상태·운영 통계·해부·시간·검색·유형 필터·엔티티·문서·그래프 규칙·규칙 설명·질의(인용 매핑)·오버라이드, 그리고 **실패 정렬**(빈 용어를 셋 다 거절하는가)까지 42건. `--json` 출력에 안내 줄이 섞이면 그 자체로 실패 — [docs/SURFACE_ALIGNMENT.md](../../docs/SURFACE_ALIGNMENT.md) | 4초 |
| `verify_stage_align.py` | 파이프라인 단계 이름이 **코드·레지스트리·화면 라벨·손잡이** 네 곳에서 같은가 | 1초 |

## 연결·부하 하네스

| 스크립트 | 무엇을 | 소요 |
|---|---|---|
| `verify_llm_switch.py` | **`config.json` 두 줄만 바꿔 API ↔ headless 로 전환**되는가를 말이 아니라 실행으로. API 모드에서 CLI·Web·MCP 세 창구를 돌리고, 전환 뒤 다른 설정 파일과 `llmwiki/*.py` 의 **수정 시각이 그대로인지**(= 코드를 안 고쳤는지) 확인한 다음 같은 세 창구를 다시 돌린다. 역할 하나만 바꾸기·되돌리기까지 18건 — [docs/LLM_CONNECT.md](../../docs/LLM_CONNECT.md) | 5초 |
| `verify_build_load.py` | **사람들이 쓰는 중에 빌드해도 되는가** — 30명이 계속 질의하는 동안 빌드를 걸고 **빌드가 도는 구간에 시작된 질의만** 추려 잰다. 증분·채널(`fts`/`vector`/`graph`)·전체 리빌드 × `reads_during_build` 정책별 26건 — [docs/BUILD_UNDER_LOAD.md](../../docs/BUILD_UNDER_LOAD.md) | 17초 |
| `verify_timeouts.py` | 실패를 **일부러 일으켜** 버티는지 본다(mock 훅 `LLMWIKI_MOCK_FAIL`·`LLMWIKI_MOCK_DELAY_MS`) — LLM 무응답·느림·외부 RAG 다운·폭주 24항목 | 2분 |
| `verify_soak.py` | 오래 돌렸을 때의 자원 누수(연결 풀·스레드·메모리) | 수 분 |
| `verify_collab_many.py` | 협업 채팅·게시판에 여러 명이 동시에(`--people 10`) | 1분 |
| `verify_mcp.py` | MCP 종단 — 전송 3종(stdio·Streamable HTTP·브리지) · 프로토콜 적합성 · **도구 전부 실제 호출** · 잘못된 호출 · 인증 · 페더레이션 · 동시성. `--quick` 은 3분 | 3~10분 |

## 화면별 하네스

| 스크립트 | 무엇을 |
|---|---|
| `verify_rerun_ui.py` | 워터폴의 각 단계 **⟲** 가 실제로 그 단계부터 다시 도는가 |
| `verify_schedule_ui.py` | 스케줄 화면 ↔ `schedule.json` ↔ 서버 |
| `verify_security_ui.py` | 보안 화면 ↔ `security.json` ↔ 서버 (권한 표·사용자·API 키) |
| `verify_act_detail_load.py` | 진행 중 작업 목록에서 한 줄을 눌렀을 때 상세가 실제로 실리는가 |

`verify_buttons.py` 옵션: `--only evolve,memory`(탭 지정) · `--heavy`(빌드·평가처럼 오래 걸리는 버튼도) ·
`--live`(격리하지 않고 지금 떠 있는 서버에 — 상태를 바꾸는 버튼은 자동 제외) · `--wait <초>`(클릭 후 대기).
기본으로 건너뛰는 버튼은 스크립트 상단의 `HEAVY`(전체 빌드·평가·스냅샷 생성 등)에 적혀 있다.
새 버튼을 추가하면 자동으로 목록에 들어오고, 출력이 특정 영역에 그려지는 버튼은 `EXPECT` 에 기대 조건을 한 줄 추가한다.

`bench_fts.py` 가 지키는 것: `chunks_fts`·`chunks_tri` 는 `chunk_id` 가 UNINDEXED 라 청크별 `DELETE ... WHERE chunk_id=?` 가
매번 FTS 전체 스캔이 된다. 예전에 그렇게 하다가 16,882 청크 기준 색인 단계가 수십 분 걸렸다(§4.2). 새 코드가 다시
청크별 삭제로 돌아가면 이 스크립트가 잡는다. 예전 방식과 나란히 비교하려면 `--no-old` 를 빼고 실행한다(오래 걸린다).

동시 사용·취소·인코딩은 하네스가 아니라 단위 테스트로 확인한다:

```bat
python -m unittest tests.test_concurrency_0915    :: 43개 — 요청 격리·RW 락·대기열·속도 제한·취소·역할 정책·스케줄러·30 동시 질의
python -m unittest tests.test_console_0915        :: 8개 — 좁은 인코딩에서도 한글이 깨지지 않는가
python -m unittest tests.test_pipeline.FtsRebuildTest  :: 4개 — 전체/증분 리빌드가 FTS·trigram 행을 정확히 갈아 끼우는가
```
