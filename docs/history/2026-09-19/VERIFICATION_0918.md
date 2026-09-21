# 2026-09-18/19 검증 보고서 — 요청 23건(17 + 6)의 구현과 확인

> 대상 회차: [IMPLEMENTATION_PLAN_0918.md](../2026-09-18/IMPLEMENTATION_PLAN_0918.md)(요청 17건) + [IMPLEMENTATION_PLAN_0918_2.md](../2026-09-18/IMPLEMENTATION_PLAN_0918_2.md)(잔여 + 신규 6건).
> 릴리스: [RELEASE_NOTES.md](../../RELEASE_NOTES.md) **3.1.0** · 회귀 절차: [TESTING_GUIDE.md](../../TESTING_GUIDE.md)
> 모든 하네스는 **격리 임시 환경**(임시 폴더 + `LLMWIKI_*_PATH` + mock 프로바이더)에서 돌아 실제 색인·설정·로그를 건드리지 않는다.

## 0. 한 장 요약

**한 줄로 돌리는 법** — 아래 표의 숫자는 이 명령이 직접 갱신한다(손으로 옮겨 적지 않는다):

```bat
python tools\verify\verify_all.py          :: 기본 묶음
python tools\verify\verify_all.py --quick  :: 빠른 묶음 (문서는 갱신하지 않는다)
python tools\verify\verify_all.py --full   :: MCP 전체 + 30명 협업 시뮬레이션까지
```

<!-- VERIFY_ALL_TABLE -->

> 2026-09-20 09:02 기준 · Python 3.14.7 · 모두 격리 임시 환경에서 실행 (실제 색인·설정은 건드리지 않는다)

| 무엇 | 명령 | 결과 |
|---|---|---|
| 단위 테스트 | `python -m unittest discover -s tests` | **711/711 통과** (170s) |
| 스트레스 (30명 동시) | `python -m unittest tests.test_concurrency_0915.StressTest` | **9/9 통과** (24s) |
| 문서 ↔ 코드 정합 | `python tools/verify/verify_docs.py` | **OK** (0s) |
| CLI · Web · MCP 정렬 | `python tools/verify/verify_surface_align.py` | **OK** (0s) |
| 세 창구 동작 동등성 (CLI 프로세스 · Web · MCP) | `python tools/verify/verify_tri_surface.py --port 8879` | **42/42 통과** (4s) |
| 단계 정렬 (코드 ↔ 레지스트리 ↔ 라벨 ↔ 손잡이) | `python tools/verify/verify_stage_align.py` | **16/16 통과** (0s) |
| 설정 UI ↔ 파일 ↔ 서버 양방향 | `python tools/verify/verify_settings_sync.py --port 8934` | **64/64 통과** (2s) |
| LLM 연결 전환 (API ↔ headless, 세 창구) | `python tools/verify/verify_llm_switch.py --port 8973` | **18/18 통과** (5s) |
| 빌드 중 서비스 (30명 동시 · 채널별) | `python tools/verify/verify_build_load.py --port 8975 --users 30` | **26/26 통과** (17s) |
| CLI 전수 | `python tools/verify/verify_cli.py` | **361/361 통과** (162s) |
| Web API 전수 | `python tools/verify/verify_web.py` | **377/377 통과** (40s) |
| MCP 종단 | `python tools/verify/verify_mcp.py --quick` | **129/129 통과** (7s) |
| UI 배선 | `python tools/verify/verify_ui_wiring.py` | **OK** (0s) |
| 브라우저 렌더 | `python tools/verify/verify_browser.py --port 8901 --cdp 9401` | **OK** (25s) |
| 창 크기 대응 (폭 5종) | `python tools/verify/verify_responsive.py --port 8904` | **30/30 통과** (20s) |
| 버튼 전수 | `python tools/verify/verify_buttons.py --port 8902 --cdp 9402` | **135/135 통과** (425s) |
| 보안 · 사용자 화면 | `python tools/verify/verify_security_ui.py --port 8903 --cdp 9403` | **58/58 통과** (140s) |
| 단계 재실행 · 작업 상세 | `python tools/verify/verify_rerun_ui.py --port 8905 --cdp 9405` | **27/27 통과** (44s) |
| 협업 다중 접속 (10명) | `python tools/verify/verify_collab_many.py --people 10 --port 8906 --cdp 9406` | **20/20 통과** (32s) |
| 작업 상세 로딩 시간 | `python tools/verify/verify_act_detail_load.py --port 8909 --cdp 9409` | **7/7 통과** (18s) |
| 스케줄 화면 | `python tools/verify/verify_schedule_ui.py --port 8910 --cdp 9410` | **14/14 통과** (20s) |
| 타임아웃 · 취소 내성 | `python tools/verify/verify_timeouts.py --port 8907` | **47/47 통과** (140s) |
| 다중 클라이언트 혼합 부하 | `python tools/verify/verify_soak.py --port 8971 --seconds 60 --clients 16` | **10/10 통과** (64s) |
| 무작위 입력 내성 | `python tools/verify/verify_monkey.py` | **OK** (462s) |
<!-- /VERIFY_ALL_TABLE -->

### 0.1 이 회차의 개별 실측 (2026-09-19)

| 무엇 | 명령 | 결과 |
|---|---|---|
| 단위 테스트 | `python -m unittest discover -s tests` | **328/328 통과** (109s) |
| CLI 전수 | `python tools/verify/verify_cli.py` | **303/303 통과** |
| Web API 전수 | `python tools/verify/verify_web.py` | **370/370 통과** |
| MCP 종단 | `python tools/verify/verify_mcp.py --quick` | **124/124 통과** |
| **설정 양방향** (신규) | `python tools/verify/verify_settings_sync.py` | **59/59 통과** |
| 버튼 전수 | `python tools/verify/verify_buttons.py` | **111/111 통과** |
| 브라우저 렌더 | `python tools/verify/verify_browser.py` | **OK** (콘솔 오류 0 · 탭 34종) |
| **창 크기 대응** (신규) | `python tools/verify/verify_responsive.py` | **30/30 통과** (폭 5종 × 탭 6종) |
| UI 배선 | `python tools/verify/verify_ui_wiring.py` | **OK** (JS→API 경로 92개 모두 존재) |
| CLI·Web·MCP 정렬 | `python tools/verify/verify_surface_align.py` | **OK** (기능 50 · MCP 도구 17) |
| 문서 ↔ 코드 정합 | `python tools/verify/verify_docs.py` | **OK** (문서 52 · 링크 385 · 파일 500 · API 162 · 고아 0) |

새 단위 테스트: `tests/test_sweep.py`(22) · `tests/test_answer_modes.py`(11) · 기존 `test_output_mode`·`test_fusion_topk`·`test_query_rules_explain`·`test_graph_profile`·`test_headless_switch`·`test_stopwords`·`test_log_quota`·`test_overrides_guard`.

## 1. 이 회차에 찾아 고친 결함

하네스를 새로 쓰거나 확장하면 반드시 제품 결함이 나온다. 이번에 나온 것들이다.

### 1.1 켜도 아무 일도 일어나지 않던 기능 3건 (가장 나빴다)

`config.py` 에 토글이 있고, 프롬프트 파일이 생성되고, 튜닝 키가 문서에 나오고, `rerun`·`sweep`·`analysis` 레지스트리에도 등록돼 있는데 **파이프라인이 그 기능을 호출하지 않았다**. 설정 화면에서는 멀쩡해 보이므로 사용자가 "켰는데 왜 그대로지?" 를 겪는다.

| 기능 | 증상 | 원인 | 조치 |
|---|---|---|---|
| `answer_mode=best_effort` | 항상 `result_type=grounded`, `[BK]` 없음 | `run()` 이 값을 읽지만 `generate_answer(...)` 에 `mode=`/`verdict=`/`reasons=` 를 넘기지 않았고, 근거 부족 분기가 모드를 보지 않았다 | 인자 전달 + 근거 부족 분기를 `grounded` 일 때만 |
| `toggles.degrade_on_llm_failure` | 꺼도 추출식 답변으로 이어감 | `generate_answer(degrade=)` 는 구현돼 있는데 호출부가 넘기지 않았다 | 토글을 넘긴다 |
| `toggles.llm_after_fusion` · `llm_after_rerank` | trace 에 단계가 아예 없음 | **단계 구현 자체가 없었다** | `_fusion_llm()` · `_rerank_review_llm()` 구현, `_retrieve()` 에 연결, `architecture.py` 에 단계 등록 |

재발 방지: [TESTING_GUIDE.md §2](../../TESTING_GUIDE.md) 에 "새 토글은 등록 + 단계 구현 + 레지스트리 행 + **그 단계가 실제로 도는 것을 확인하는 테스트**까지가 한 묶음" 을 적었고, `tests/test_answer_modes.py` 가 세 가지를 모두 고정한다. mock 프로바이더가 두 새 TASK(`fusion_review`·`rerank_review`)에 결정적으로 답하도록 해 LLM 없이도 배선을 시험한다.

### 1.2 권한 거부가 500 으로 나가던 것

`_dispatch_post` 의 넓은 `except Exception` 이 `AuthError` 를 삼켜, **핸들러 안에서** 난 권한 거부가 `500 {"error": "AuthError: …"}` 로 나갔다. `/api/sweep` 이 스윕 키를 화이트리스트로 거르는 자리가 그랬다(본문 `overrides` 를 거르는 자리는 try 밖이라 정상 403 이었다).

- 영향: 클라이언트가 권한 문제를 서버 고장으로 읽고, 운영자는 `error.log` 에서 가짜 결함을 쫓는다.
- 조치: `except AuthError: raise` 를 재-raise 목록에 추가 → 401/403 으로 정상 변환.
- 회귀 고정: `verify_web.py` 의 "class1 POST /api/sweep URL 키 → 403", "guest POST /api/sweep llm(URL 속성) → 403".

### 1.3 Settings 화면의 버튼 2개가 HTML 에만 있고 핸들러가 없던 것

`전체 카탈로그 테스트`(`#btn-cat-test`)와 `.env 다시 읽기`/`새로고침`(`#btn-env-reload`/`#btn-env-refresh`, 패널 `#env-panel`)이 `index.html` 에만 있고 어느 JS 에도 핸들러가 없었다. 눌러도 아무 일이 없고 `.env` 패널은 영원히 비어 있었다.

- 조치: `settings.js` 에 카탈로그 전체 테스트 렌더(`/api/models/test_catalog`)와 `.env` 패널 렌더(`/api/env`, 키·상태·마스킹 값·출처 + 활성 `LLMWIKI_*` 오버라이드), 다시 읽기(`POST /api/env {action:"reload"}`)를 붙였다. `config` 탭을 열면 패널이 자동으로 채워진다.
- 회귀 고정: `verify_buttons.py`(무반응 버튼을 FAIL 로 본다) 111/111.

### 1.4 빈 `term` 에 200 을 주던 explain 엔드포인트

`GET /api/query_rules/explain` 이 `term` 없이도 200 + 빈 껍데기를 줘서 화면이 "규칙이 없다" 로 잘못 읽을 수 있었다. → `400 {"error": "term 이 필요합니다 …"}`.

### 1.5 schedule.json 을 고치고 새로고침해도 예전 목록이 나오던 것

스케줄러는 mtime 을 보지만 **백그라운드 틱(`tick_s`, 기본 5초)** 에서만 봤다. 파일을 고치고 화면을 새로고침하면 최대 5초 동안 예전 목록이 보였다. → `GET /api/schedule` 이 조회할 때도 한 번 mtime 을 보게 했다(`getmtime` 1회).

### 1.6 단위 테스트 2건 (제품은 정상, 테스트가 새 동작을 몰랐던 것)

| 테스트 | 원인 | 조치 |
|---|---|---|
| `test_rag_federation.test_plugin_tools_dir` | `plugin_rescan_s`(5초) 폴더 재검사 주기 때문에 파일을 쓴 직후 `load_plugins()` 가 스냅샷을 돌려줬다 | 주기 경과를 흉내 내어 **시그니처 변화 감지 경로**를 그대로 검증 (`force=True` 로 우회하면 그 경로를 잃는다) |
| `test_console_0915.test_health_reports_console` | 이 PC 의 config 가 PATH 에 없는 headless 에이전트를 가리켜 `health` 가 정당하게 FAIL | exit 0 을 요구하지 않고 **완주 + Traceback 없음 + `console_encoding` 행** 으로 판정 |
| `test_tuning_arch.test_mcp_tools` | MCP 도구가 17개로 늘었는데 기대 집합이 16개 | `wiki_sweep` 추가 (도구를 늘리면 여기·`verify_surface_align` CAPS·`docs/MCP.md` 를 함께 고친다) |

## 2. 새 하네스 — `verify_settings_sync.py` (설정 **양방향**)

사용자 보고: *"Web UI 에서 편집한 값이 항상 실제 서버에 반영되지는 않았고, 반대로 파일을 직접 고친 것이 화면에 안 보이는 경우도 있었다."* 한 방향만 보는 검사는 "저장은 되는데 안 먹는" 상태를 통과시키므로, 표면마다 두 번 확인한다.

| 방향 | 확인 |
|---|---|
| **A. UI → 파일 → 유효값** | 화면이 쓰는 그 API 로 POST → 디스크 파일에 값이 있나 → GET 과 **새 프로세스**의 `config show --effective` 가 새 값인가 |
| **B. 파일 → 재적재 → UI** | 파일을 직접 고침 → (필요하면) reload API → GET 이 새 값인가 |

검사한 표면 11종과 B 방향의 재적재 방식(이 구분 자체가 운영자에게 필요한 정보라 결과 표의 `how` 열에 남는다):

| 표면 | B 방향 | 비고 |
|---|---|---|
| `config.json` · `llm_roles` | `reload` | `POST /api/config {action:"reload"}` |
| `tuning.json` | `reload` | `POST /api/tuning {action:"reload"}` · 유효값이 **질의에도** 쓰이는지까지 확인 |
| `query_rules.json` · `data/rules.json` · `models.json` · `prompts/*.md` · `agents.json` | `auto` | mtime 캐시 — 저장만 하면 다음 요청부터 |
| `schedule.json` | `auto` | §1.5 수정 뒤 즉시 |
| `server.json` | `reload` | `POST /api/admin/server {action:"reload"}` |
| `.env` | `reload` | `GET /api/env`(admin·마스킹·`LLMWIKI_*` 오버라이드) + `{action:"reload"}` |
| `config fill-defaults --all` | – | 실행 → 파일에 키가 명시되나 → **다시 돌리면 추가할 키가 0** 인가 → 서버가 그 파일을 읽나 |

결과 **59/59 통과**. 이 하네스가 §1.5 를 찾았고, `rrf_k` 가 튜닝 표에 보이지만 실제로는 `config.json` 항목(`source=config`)이라는 점도 여기서 드러났다(문서 [SETTINGS_SYNC.md](../../SETTINGS_SYNC.md) 에 반영).

## 3. 기능별 확인

| 요청 | 확인 방법 | 결과 |
|---|---|---|
| 불용어 파일 | `tests.test_stopwords`(3) · `check_env.py` 줄 | OK — [STOPWORDS.md](../../STOPWORDS.md) |
| 로그 총량 제한 | `tests.test_log_quota`(7) · `logs status` · health `log_quota` | OK — [LOG_QUOTA.md](../../LOG_QUOTA.md) |
| MCP 공존·확장성 | `verify_mcp.py` 124항목(잘못된 params · SSE · `expose` 경로 조작 거부 · `${ENV}` 유출 거부 · 브리지 인증 오류) | OK — [MCP.md](../../MCP.md) |
| Web UI 창 크기·복사·사이드바 접기 | `verify_browser.py` · `verify_buttons.py` 111/111 | OK — [WEB_UI.md](../../WEB_UI.md) |
| 융합/리랭크 뒤 LLM 2단계 | `tests.test_answer_modes`(6건이 이 둘) | OK(§1.1 에서 새로 연결) — [ANSWER_MODES.md](../../ANSWER_MODES.md) |
| 역할 단위 앙상블 | `tests.test_providers` · `models ensemble show` | OK — [ENSEMBLE.md](../../ENSEMBLE.md) |
| retry ↔ fallback 질문 | 문서 §5 표 + `degrade_on_llm_failure` 동작 테스트 2건 | 답변 완료 — [ANSWER_MODES.md §5](../../ANSWER_MODES.md) |
| headless 일반화(WinError 206) | `tests.test_headless_switch` · `models test --live` | OK — [HEADLESS.md](../../HEADLESS.md) |
| 답변 모드 3종 | `tests.test_answer_modes` · `verify_web` 의 `overrides.answer_mode` | OK — [ANSWER_MODES.md](../../ANSWER_MODES.md) |
| UI ↔ 서버 연동 · 기본값 명시 · 카탈로그 테스트 | `verify_settings_sync.py` 59/59 (§2) | OK — [SETTINGS_SYNC.md](../../SETTINGS_SYNC.md) |
| 기본 0.0.0.0 바인드 + 전제 2건 | `verify_web` 화이트리스트 6항목 · 500 마스킹 | OK — [SECURITY.md §6.1](../../SECURITY.md) |
| 단계별 파라미터 스윕 | `tests.test_sweep`(22) · `verify_cli` · `verify_web`(잡 완주까지) · `wiki_sweep` | OK — [SWEEP.md](../../SWEEP.md) |
| 채널별 top-k 가중 | `tests.test_fusion_topk` | OK — [FUSION_TOPK.md](../../FUSION_TOPK.md) |
| 출력 모드(중간 산출물) | `tests.test_output_mode`(9) · 세 창구 | OK — [ANSWER_MODES.md §3](../../ANSWER_MODES.md) |
| 규칙 방향 · `rules explain` | `tests.test_query_rules_explain`(3) | OK — [QUERY_RULES.md](../../QUERY_RULES.md) |
| 그래프 진단 프로파일 | `tests.test_graph_profile`(5) · `graph profile --compare` | OK — [GRAPH_PROFILE.md](../../GRAPH_PROFILE.md) |
| Pipeline 페이지 | `verify_ui_wiring` · `verify_browser` · `verify_buttons` | OK — [PIPELINE_PAGE.md](../../PIPELINE_PAGE.md) |
| 릴리스 노트 · 테스트 가이드 · 정렬 | `--version`=3.1.0 세 창구 일치 · `verify_surface_align` OK | OK — [RELEASE_NOTES.md](../../RELEASE_NOTES.md) · [TESTING_GUIDE.md](../../TESTING_GUIDE.md) |

## 4. 다른 환경에서 다시 돌리는 순서

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m unittest discover -s tests            # 1. 단위 (약 2분)
python tools/verify/verify_surface_align.py     # 2. 세 창구 정렬 (1초)
python tools/verify/verify_docs.py              # 3. 문서 ↔ 코드 (수 초)
python tools/verify/verify_settings_sync.py     # 4. 설정 양방향 (빌드 포함 2분)
python tools/verify/verify_cli.py               # 5. CLI 전수
python tools/verify/verify_web.py               # 6. Web 전수
python tools/verify/verify_mcp.py --quick       # 7. MCP 종단
python tools/verify/verify_ui_wiring.py         # 8. UI 배선
python tools/verify/verify_browser.py           # 9. 렌더 (Edge/Chrome 필요)
python tools/verify/verify_buttons.py           # 10. 버튼 전수
python tools/verify/verify_all.py               # 전부 + 이 문서 §0 표 갱신
```

브라우저가 없는 환경에서는 9·10 이 SKIP 된다. 실패를 읽는 법은 [TESTING_GUIDE.md §4](../../TESTING_GUIDE.md).

## 5. 남은 것 · 알려진 한계

| 항목 | 상태 |
|---|---|
| `server.json` 은 파일을 고친 뒤 **reload 가 필요**하다(자동 mtime 재적재 아님) | 문서화됨([SETTINGS_SYNC.md](../../SETTINGS_SYNC.md)) — 자동화는 다음 회차 후보 |
| `api_keys.last_used` 미저장, 프리셋 미리보기/저장 구분 표시 | 1차 계획 §2.10 의 "알려진 어긋남" 중 남은 2건 |
| 실제 사내 게이트웨이·opencode 연결 | 목업·mock 으로만 검증. 포팅 환경에서 `models test --live` · `mcp-source test` 로 확인 — [BRINGUP_GUIDE.md §4](../../BRINGUP_GUIDE.md) |
| `verify_all.py --full`(30명 협업·장시간 부하) | 이 회차에는 개별 하네스만 실행 |
