# TESTING GUIDE — 무엇을 고쳤을 때 무엇을 돌리나

> 대상: 코드를 고친 사람(또는 LLM). "수정마다 어떤 테스트를 돌려야 하나" 를 한 표로. 전체 검증 절차와 실측 숫자는
> [VERIFICATION.md](VERIFICATION.md), 하네스 각각의 설명은 [tools/verify/README.md](../tools/verify/README.md).
> 모든 테스트·하네스는 **격리 임시 환경**(임시 폴더 + `LLMWIKI_*_PATH`)에서 돌아 실제 색인·설정·로그를 건드리지 않는다.
>
> 지금 규모: 단위 테스트 <!--live:tests-->758개 · 검증 하네스 <!--live:harness-->27종.
> **테스트를 새로 쓸 거면 §2.5 를 먼저 읽는다** — 진짜 폴더를 건드리지 않는 법과, 그 테스트가 정말 무언가를 지키는지 확인하는 법.

## 0. 세 단계 규칙

| 단계 | 언제 | 명령 | 시간 |
|---|---|---|---|
| **A. 즉시** | 파일을 저장할 때마다 | `python -m compileall -q llmwiki tests tools` + 아래 §1 표의 **그 영역 단위 테스트** | 초~1분 |
| **B. 기능 완료** | 기능/수정 하나를 끝냈을 때 | §1 표의 **그 영역 하네스** + `python tools/verify/verify_surface_align.py` + `python tools/verify/verify_docs.py` | 2~10분 |
| **C. 회차 마감** | 문서까지 쓴 뒤, 검증 보고서를 쓰기 전 | `python tools/verify/verify_all.py` (약 20~30분; `--quick` 8분은 문서를 갱신하지 않음) | 20~30분 |

Windows PowerShell 에서 한글 출력이 깨지면 `$env:PYTHONIOENCODING='utf-8'` 을 먼저 둔다(하네스는 스스로 설정한다).

## 1. 변경 영역 → 테스트 · 하네스

| 바꾼 것 (모듈) | A. 단위 테스트 (`python -m unittest …`) | B. 하네스 (`python tools/verify/…`) | 함께 볼 문서 |
|---|---|---|---|
| **질의 경로** `query_engine.py` `retrieval.py` `fusion.py` `answer.py` `evidence.py` | `tests.test_pipeline` `tests.test_output_mode` `tests.test_fusion_topk` `tests.test_rerun_0917` `tests.test_features_0914` | `verify_cli.py`(query/rerun/sweep 명령) · `verify_web.py`(`/api/query` output_mode·overrides) · `verify_mcp.py --quick` | [ANSWER_MODES.md](ANSWER_MODES.md) · [FUSION_TOPK.md](FUSION_TOPK.md) |
| **규칙 확장** `query_rules.py` `textutil.py` (불용어) | `tests.test_query_rules_explain` `tests.test_stopwords` `tests.test_features_0916` | `verify_cli.py`(rules explain/test/lint) | [QUERY_RULES.md](QUERY_RULES.md) · [STOPWORDS.md](STOPWORDS.md) |
| **튜닝 레지스트리 · 구조 레지스트리** `tuning.py` `architecture.py` `config.py`(토글·설정 도움말) `progress.py`(단계 라벨) | `tests.test_tuning_arch` | **`verify_stage_align.py`**(단계 이름이 코드·레지스트리·라벨·손잡이 네 곳에서 같은가) · `verify_surface_align.py` · `python -m llmwiki tuning doc` / `arch doc` 재생성 뒤 `verify_docs.py` | [TUNING.md](TUNING.md) · [PIPELINE_PAGE.md §4.7](PIPELINE_PAGE.md) · [OPTIMIZATION_GUIDE.md](OPTIMIZATION_GUIDE.md) |
| **설정 파일·기본값** `config.py`(Settings 키·`fill_defaults`·`apply_overrides`) | `tests.test_overrides_guard` `tests.test_phase0` | `verify_settings_sync.py`(UI ↔ 파일 ↔ 서버 양방향) · `verify_cli.py`(`config fill-defaults --dry-run`, `config env`) | [SETTINGS_SYNC.md](SETTINGS_SYNC.md) · [BRINGUP_GUIDE.md §3.2](BRINGUP_GUIDE.md) |
| **프로바이더 · 앙상블 · 카탈로그** `providers.py` `models_catalog.py` `prompts.py` (+ `answer.summarize_ensemble`) | `tests.test_providers` `tests.test_features_0914`(LlmRetryTest) · **`tests.test_ensemble_visible`**(앙상블로 돌았다는 것이 trace 에 보이는가 · 단일 호출에는 안 붙는가) | `verify_cli.py`(`models test --catalog`, `models ensemble`) · `verify_web.py`(`/api/models/test_catalog`) · 실환경: `python -m llmwiki models test --live` | [ENSEMBLE.md](ENSEMBLE.md) · [BRINGUP_GUIDE.md §4](BRINGUP_GUIDE.md) |
| **headless 에이전트** `headless.py` `agents.json` | `tests.test_headless_switch` `tests.test_providers`(-k headless) `tests.test_console_0915` | 목업: `python -m llmwiki.headless --mock --stall 30` · 실환경 `models test --live` | [HEADLESS.md](HEADLESS.md) |
| **스윕 · 재실행** `sweep.py` `rerun.py` | `tests.test_sweep` `tests.test_rerun_0917` | `verify_cli.py`(sweep run/list/show/compare) · `verify_web.py`(`/api/sweep`) · `verify_rerun_ui.py`(브라우저) | [SWEEP.md](SWEEP.md) · [RERUN.md](RERUN.md) |
| **그래프** `graph_rules.py` `graph_build.py` `graph_profile.py` | `tests.test_graph_profile` `tests.test_phase2` `tests.test_pipeline` | `verify_cli.py`(`graph profile`, `build graph`) · `verify_web.py`(`/api/graph/profile`) | [GRAPH_PROFILE.md](GRAPH_PROFILE.md) |
| **빌드 · 색인 · 저장소** `pipeline.py`(build) `store.py` `corpus.py` `embed_run.py` | `tests.test_pipeline`(FtsRebuildTest 포함) `tests.test_phase0` `tests.test_phase2` `tests.test_scale_profile` | `verify_cli.py`(build/verify/채널 빌드) · `bench_fts.py`(리빌드 속도 회귀) | [SYSTEM_ARCHITECTURE.md §4](SYSTEM_ARCHITECTURE.md) |
| **자가진화 · 메모리 · 포렌식** `evolve.py` `memory.py` `forensic.py` `analysis.py` | `tests.test_phase3_5` `tests.test_phase6` `tests.test_analysis` | `verify_cli.py`(evolve/memory/forensic/analyze) | [FORENSIC.md](FORENSIC.md) · [ANALYSIS_MODE.md](ANALYSIS_MODE.md) |
| **인증 · 권한 · 감사** `auth.py` `security.json` | `tests.test_auth` `tests.test_overrides_guard` | `verify_web.py`(게스트/viewer/class1/admin/API 키/CSRF) · `verify_security_ui.py`(브라우저) | [SECURITY.md](SECURITY.md) |
| **요청 관리자 · 동시성 · 로그** `reqmgr.py` `logging_setup.py` `server.py`(잡·슬롯) | `tests.test_concurrency_0915` (스트레스 포함) `tests.test_log_quota` `tests.test_phase0` | `verify_timeouts.py` · `verify_soak.py` · `verify_monkey.py` · `verify_collab_many.py` | [CONCURRENCY.md](CONCURRENCY.md) · [LOG_QUOTA.md](LOG_QUOTA.md) |
| **Web API** `web/server.py` | `tests.test_web_api` `tests.test_features_0916` `tests.test_review_0917` | `verify_web.py` · `verify_ui_wiring.py`(JS 가 부르는 경로가 서버에 있는가) · `verify_surface_align.py` | [WEB_UI.md](WEB_UI.md) |
| **Web 프론트** `web/static/*.html` `js/*.js` `style.css` | (없음 — 정적 검사와 브라우저로) | `verify_ui_wiring.py`(1초) → `verify_browser.py`(콘솔 오류 0) → **`verify_responsive.py`**(폭 360~1920 에서 가로 넘침 0) → `verify_buttons.py`(모든 버튼 클릭) → 화면별 `verify_rerun_ui.py` `verify_schedule_ui.py` `verify_security_ui.py` `verify_act_detail_load.py` | [WEB_UI.md](WEB_UI.md) · [PIPELINE_PAGE.md](PIPELINE_PAGE.md) |
| **MCP 서버 · 클라이언트 · 페더레이션** `mcp.py` `mcp_client.py` | `tests.test_tuning_arch`(`test_mcp_tools` — 도구 집합) `tests.test_rag_federation` `tests.test_features_0914` | `verify_mcp.py`(`--quick` 3분 / 전체) · `python -m llmwiki mcp --doctor` | [MCP.md](MCP.md) · [RAG_FEDERATION.md](RAG_FEDERATION.md) |
| **스케줄러** `scheduler.py` `schedule.json` | `tests.test_concurrency_0915`(SchedulerTest) | `verify_cli.py`(schedule) · `verify_schedule_ui.py` | [SCHEDULER.md](SCHEDULER.md) |
| **협업** `collab.py` | `tests.test_features_0916` | `verify_collab_many.py --people 10` | [COLLAB.md](COLLAB.md) |
| **CLI 명령 추가/변경** `cli.py` | 그 명령이 부르는 모듈의 테스트 | `verify_cli.py`(명령 표에 **행을 추가**) · `verify_surface_align.py`(CAPS 표에 행 추가) | [CLI_FLOWS.md](CLI_FLOWS.md) |
| **문서 접근 제어 · 인젝션 방어** `docacl.py` `ctxguard.py` (+ `query_engine.py` 의 `doc_acl` 단계, `retrieval.channel_search(acl=)`, `querydebug.doc_detail(role=)`, `mcp.py` 의 actor) | `tests.test_doc_acl` `tests.test_prompt_injection` | `verify_cli.py`(`security docacl`) · `verify_security_ui.py`(화면↔파일↔서버) · `verify_surface_align.py` | [SECURITY.md §6.2](SECURITY.md) · [QA_HARDENING_0919.md §4](history/2026-09-19/QA_HARDENING_0919.md) |
| **컨텍스트 예산 · claim 검증** `models_catalog.context_budget` `answer.py`(`build_context`·`split_claims`·`check_claims`) | `tests.test_context_budget` `tests.test_rag_edge_cases` `tests.test_answer_modes` | `verify_web.py`(`/api/query` 의 claims) · `verify_mcp.py --quick` | [QA_HARDENING_0919.md §2](history/2026-09-19/QA_HARDENING_0919.md) · [TUNING.md](TUNING.md) |
| **창구 정합(값까지)** 세 창구 중 하나의 응답 모양을 바꿨을 때 | `tests.test_surface_consistency`(17건) | `verify_surface_align.py`(①존재 + ②전수) · **`verify_tri_surface.py`**(③동작 — `serve`·CLI 프로세스·`POST /mcp` 를 진짜 띄워 54건) · `verify_mcp.py` | [SURFACE_ALIGNMENT.md](SURFACE_ALIGNMENT.md) · [QA_HARDENING_0919.md §1](history/2026-09-19/QA_HARDENING_0919.md) · [MCP.md §2.01](MCP.md) |
| **연결 풀 · 자원 누수** `store.py`(`session`/`pool_info`) | `tests.test_resource_limits` `tests.test_concurrency_0915` | `verify_soak.py` · `verify_monkey.py` | [CONCURRENCY.md §8.1](CONCURRENCY.md) |
| **운영 통계 · 추세 차트** `opstats.py` (+ `observability.js` 의 `chart()`) | `tests.test_opstats`(21건 — **읽기 전용**·빈 환경·섹션 필터·표본 수·**admin 전용 절 가리기**) · `tests.test_quality_ux_0920.TrendTest`(일/주/월 묶음·기간·스파크라인) | `verify_cli.py`(`stats --full`) · `verify_web.py`(게스트에게 `users` 절이 안 나가는가) · `verify_buttons.py`(`btn-ops` 가 **차트 SVG·일/주/월 버튼**까지) · `verify_tri_surface.py` §2.5 | [OPS_STATS.md](OPS_STATS.md) · [SECURITY.md §2.2](SECURITY.md) |
| **관리자 초기화** `reset.py` | `tests.test_reset`(29건 — 미리보기·지키는 것·로그 폴더 격리) | `verify_cli.py`(`reset … --apply` 는 임시 환경에서만) · `verify_buttons.py` | [RESET.md](RESET.md) |
| **제안 설명** `proposal_explain.py` `evolve.describe_proposal` | `tests.test_proposal_explain`(29건) | `verify_cli.py`(`evolve show`) · `verify_tri_surface.py` | [EVOLVE.md §1.5](EVOLVE.md) |
| **Trial 문항 원천 · 단계별 비교 · 채점 불가 처리** `trials.py` `evalset.py` | `tests.test_trial_sources`(20건) · `tests.test_quality_ux_0920.UngradedTrialTest`(정답이 없으면 0 이 아니라 공백) | `verify_cli.py`(`trial run --source queries`) · `verify_web.py`(`/api/trials`) | [EVAL_TRIAL.md §5](EVAL_TRIAL.md) |
| **포렌식 목록·필터** `forensic.py`(`list_forensics`·`verdict_counts`·`summary`) | `tests.test_quality_ux_0920.ForensicFilterTest`(기본이 문제 건만인가·건수가 전체인가) | `verify_tri_surface.py` §2.6(CLI ↔ Web 같은 목록) · `verify_buttons.py`(`btn-fx-refresh` 가 판정 칩까지) | [FORENSIC.md §1.1](FORENSIC.md) |
| **질의 해부(디버그)** `querydebug.py` | `tests.test_debug_parity`(14건 — 화면 디버그가 **실제 질의와 같은 순서**로 도는가) | `verify_tri_surface.py`(§3 해부) · `verify_web.py`(`/api/debug/query`) | [WEB_UI.md §0.67](WEB_UI.md) |
| **검색 필터 · 근거 링크** `retrieval.channel_search(doc_types=)` `web/static/js/ask.js` | `tests.test_search_filters`(14건) `tests.test_evidence_links`(8건) | `verify_tri_surface.py`(§5·§6) · `verify_browser.py`(콘솔 오류 0) · `verify_buttons.py` | [WEB_UI.md §0.67·§0.675](WEB_UI.md) |
| **문서만** `docs/*.md` `README.md` | — | `verify_docs.py` — ①문서가 주장하는 것이 코드에 있나(링크·명령·설정 키·API 경로) ②**거꾸로**: 모든 CLI 명령·MCP 도구·설정 키가 **현행 문서에 설명돼 있나**, 모든 하네스가 목록에 있나 ③배치(현행 자리에 날짜 붙은 이름 금지·고아 문서·회차 색인) ④`<!--live:-->` 규모 숫자 | [DOC_MAP.md §1·§8](DOC_MAP.md) |

## 2. 새 기능을 넣을 때 반드시 늘려야 하는 것 (없으면 하네스가 FAIL)

| 무엇 | 어디 |
|---|---|
| MCP 도구를 추가 | `tests/test_tuning_arch.py::test_mcp_tools` 의 도구 집합 · `tools/verify/verify_surface_align.py` CAPS 표 · `docs/MCP.md §2` 표 · `verify_mcp.py` 호출 항목 |
| CLI 명령 / Web 경로 추가 | `verify_surface_align.py` CAPS 표(세 창구 정렬 — 하나가 빠지면 이유를 `note` 열에). **2026-09-20부터 코드가 기준이다**: 새 명령·경로·도구를 만들고 표에 줄을 더하지 않으면 `표 밖` 으로 FAIL 하고, 칸을 비우고 이유를 안 쓰면 그것도 FAIL 이다. 칸에는 이름을 **공백으로 여러 개** 적을 수 있다(`/api/doc_chunks /api/doc /api/docs`) · `verify_cli.py` / `verify_web.py` 항목 · 세 창구에 다 있는 기능이면 `verify_tri_surface.py` 에 비교 추가 — [SURFACE_ALIGNMENT.md §6](SURFACE_ALIGNMENT.md) |
| 설정 키 추가 | `config.py`(기본값·도움말) + **config.json · setup/config.example.json 에 기본값 명시** + `BRINGUP_GUIDE.md §3.2` 행 + `verify_settings_sync.py`(그 표면이 왕복하는가) |
| 튜닝 키 추가 | `tuning.py` 레지스트리 + tuning.json 명시 + `python -m llmwiki tuning doc` 재생성 |
| 토글 추가 | `config.py Toggles` + `TOGGLE_HELP` + `TOGGLE_GROUPS` + `TOGGLE_EFFECT` + `architecture.py` 단계 표 |
| **파이프라인 단계 추가** (`prof.stage("새이름")`) | `architecture.py` 의 `FLOWS`(단계 또는 그 단계의 `trace` 목록) + `STAGE_PHASE` + (LLM 을 부르면) `STAGE_ROLES` + `progress.py` 의 `STAGE_LABELS`. 빠지면 `verify_stage_align.py` 가 이름을 찍어 FAIL |
| 화면 요소 추가 | id 를 `index.html` 과 JS 양쪽에 (verify_ui_wiring) · 버튼은 눌렀을 때 **무언가 일어나야** 한다 (verify_buttons 는 무반응을 FAIL 로 본다) |
| 문서 추가 | **현행 문서**(`docs/` 바로 아래, 날짜 없는 이름)는 README §0 색인이나 BRINGUP_GUIDE 에서 링크한다. **기록 문서**(`docs/history/<날짜>/`)는 [history/README.md](history/README.md) 회차 표에 줄을 더한다. 둘 다 빠지면 고아로 잡힌다 — [DOC_MAP.md §1·§8](DOC_MAP.md) |
| **기능을 추가** (CLI 명령·MCP 도구·설정 키) | 코드만 넣고 끝내면 `verify_docs.py` 가 **"현행 문서 어디에도 없다"** 로 실패한다. 그 기능을 설명할 현행 문서에 한 줄이라도 적어야 한다 — 문서만 보고 올리는 사람에게는 적히지 않은 기능이 **없는 기능**이다 |
| 지금의 규모를 문서에 숫자로 씀 | `<!--live:키-->` 표시를 붙인다 (`CLI 명령 <!--live:cli-->48개`). 화면에는 안 보이고, 코드와 어긋나면 `verify_docs.py` 가 잡는다. 키: `cli`·`api`·`mcp`·`tests`·`harness`·`docs` |

## 2.5 테스트를 **새로 쓸 때**의 규칙 (사람이든 LLM 이든)

이 저장소에서 반복해 나온 사고 두 가지가 그대로 규칙이 됐다.

### (1) 테스트는 **진짜 폴더를 건드리면 안 된다**

2026-09-19 에 `reset` 테스트가 **이 저장소의 `logs/` 를 비웠다** — `audit.jsonl`(감사 기록)까지 지워져
복구하지 못했다. 원인은 로그 폴더 경로가 프로세스 전역(`config.path_for("logs_dir")`)이라 임시 설정을
만들어도 로그만 실제 폴더를 가리킨 것이다.

그래서 **폴더를 만들거나 지우는 테스트는 경로 환경변수를 먼저 덮어쓴다.**

```python
os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(tmp, "logs")   # setUpClass 에서 가장 먼저
...
os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)                      # tearDownClass 에서 되돌린다
```

같은 이유로 `config.CONFIG_PATH` 를 몽키패치하는 대신 **`LLMWIKI_CONFIG_PATH` 환경변수**를 쓴다.
경로 해석을 한 곳으로 모으는 리팩터링이 몽키패치를 조용히 무력화해 `config.json` 이 두 번 깨졌다
([IMPLEMENTATION_PLAN_0919.md](history/2026-09-19/IMPLEMENTATION_PLAN_0919.md) §2.4).

### (2) 테스트가 **정말 무언가를 지키는지** 확인한다 (변이 시험)

통과하는 테스트가 아무것도 지키지 않는 경우가 이 저장소에서 여러 번 나왔다 — 양쪽 다 `None` 이라
같다고 판정하거나, 조건이 절대 참이 되지 않거나, `skipTest` 로 조용히 넘어간다.

**새 테스트를 넣었으면 고친 코드를 일부러 되돌리거나 값을 바꿔 보고, 그 테스트가 실패하는지 확인한다.**
실패하지 않으면 그 테스트는 없는 것과 같다. 예:

```powershell
# 예) 세 창구 동등성 하네스가 정말 비교하고 있는지
#     cli.py 의 stats 응답에서 docs 를 999 로 바꾼 뒤
python tools/verify/verify_tri_surface.py
#     → FAIL 상태 docs  창구별 값이 다르다: {"cli": 999, "web": 38, "mcp": 38}   ← 이것이 보여야 한다
#     확인했으면 반드시 되돌린다
```

비교 테스트에는 **"비교 대상이 비어 있지 않다"** 는 주장을 먼저 넣는다. `verify_tri_surface.py` 의
`same()` 은 값이 하나뿐이거나 비어 있으면 통과가 아니라 **실패**로 본다.

### (3) 셋 다 있는 기능이면 세 창구를 모두 건드린다

CLI·Web·MCP 에 모두 있는 기능을 고쳤다면 `tests/test_surface_consistency.py`(엔진 동일성)와
`tools/verify/verify_tri_surface.py`(실제 프로세스) 양쪽에 비교를 더한다 —
단위 테스트만으로는 **CLI 가 그 함수를 그렇게 부르는지**를 보지 못한다([SURFACE_ALIGNMENT.md §5](SURFACE_ALIGNMENT.md)).

## 3. 자주 쓰는 명령

```powershell
# A. 단위 (전체 약 3분 · 테스트 711개)
python -m unittest discover -s tests
python -m unittest tests.test_sweep tests.test_output_mode -v          # 영역만

# B. 하네스 (격리 환경, 서버는 8792~8934 포트)
python tools/verify/verify_surface_align.py          # CLI·Web·MCP 정렬: 존재 + 전수 (1초)
python tools/verify/verify_surface_align.py --inventory   # 코드에서 뽑은 전수 목록 (명령·하위 동작·경로·도구)
python tools/verify/verify_tri_surface.py            # 세 창구가 같은 답을 주는가 (실제 3프로세스, 4초)
python tools/verify/verify_stage_align.py            # 파이프라인 단계 정렬 (1초)
python tools/verify/verify_docs.py                   # 문서 ↔ 코드 (수 초)
python tools/verify/verify_ui_wiring.py              # HTML ↔ JS ↔ API 경로 (1초)
python tools/verify/verify_settings_sync.py          # 설정 UI ↔ 파일 ↔ 서버 양방향
python tools/verify/verify_cli.py                    # CLI 전수 (4~6분)
python tools/verify/verify_web.py                    # Web API 전수 (3~5분)
python tools/verify/verify_mcp.py --quick            # MCP 종단 (3분)
python tools/verify/verify_browser.py                # Edge headless 렌더 + 콘솔 오류
python tools/verify/verify_responsive.py             # 창 크기 360·768·1024·1366·1920 (가로 넘침 0)
python tools/verify/verify_buttons.py                # 모든 버튼 클릭 (6~8분)

# C. 전체 (최신 결과 표를 docs/VERIFICATION.md 에 자동 기록)
python tools/verify/verify_all.py
python tools/verify/verify_all.py --only unit,web,mcp
```

## 4. 실패를 읽는 법

| 증상 | 대개의 원인 | 어디를 본다 |
|---|---|---|
| `verify_surface_align.py` 가 "표 밖" | 도구/명령/경로를 추가하고 CAPS 표를 안 늘림 | §2 첫 두 행 |
| `verify_surface_align.py` 가 "칸을 비웠는데 이유(note)가 없다" | 세 창구 중 하나를 일부러 비웠으면 **왜** 없는지를 `note` 에 적어야 한다 (이유 없는 공백 = 정렬이 깨진 자리) | [SURFACE_ALIGNMENT.md §4](SURFACE_ALIGNMENT.md) |
| `verify_tri_surface.py` 가 "창구별 값이 다르다" | 한 창구의 응답 모양/기본값만 바뀜 | 표시된 값 세 개를 비교 — 대개 CLI 가 기본값을 하나 더 얹었거나 응답 감싸는 키가 다르다 |
| `verify_tri_surface.py` 가 "비교 대상이 비어 있다" | 세 창구가 나란히 빈 값을 준다 — **빈 비교는 통과가 아니다** | 비교에 쓰는 입력(용어·질의·문서)이 이 코퍼스에 실제로 있는지 확인 |
| `verify_tri_surface.py` 가 "--json 인데 앞에 다른 출력이 섞였다" | `--json` 경로에 안내/진행 줄을 `print` 했다 | 그 줄을 `stderr` 로 보내거나 `as_json` 일 때 빼라 |
| `verify_stage_align.py` 가 "레지스트리에 없는 이름" | `prof.stage("x")` 를 새로 넣고 `architecture.py` 의 `trace` 목록과 `progress.STAGE_LABELS` 를 안 늘림 | §2 의 '파이프라인 단계 추가' 행 |
| `verify_docs.py` 가 "없는 문서" | 코드 주석·도움말이 `docs/X.md` 를 가리키는데 파일이 없다 | 그 문서를 쓰거나 링크를 고친다 |
| `verify_docs.py` 가 "**설명 없음**" | 새 CLI 명령·MCP 도구·설정 키를 만들고 **현행 문서에 안 적었다** | 그 기능의 문서에 한 줄 추가 (기록 문서에 적는 것은 인정되지 않는다) |
| `verify_docs.py` 가 "**레이아웃**" | 현행 자리(`docs/`)에 날짜 붙은 파일 이름이 있다 | 기록이면 `docs/history/<날짜>/` 로 옮기고, 현행이면 날짜를 뺀 이름으로 |
| `verify_docs.py` 가 "회차 색인에 없음" | 기록 문서를 넣고 `docs/history/README.md` 표에 안 적었다 | 그 표에 줄 추가 |
| `verify_ui_wiring.py` 가 "id 없음" | JS `$('#x')` 의 x 가 HTML 에 없다(또는 반대) | index.html ↔ js |
| `verify_buttons.py` 가 "무반응" | 핸들러가 조용히 return 하거나 예외로 죽음 | 브라우저 콘솔 항목(결과 JSON 의 `console`) |
| `verify_settings_sync.py` 가 한 방향만 실패 | UI 저장이 파일에 안 가거나, 파일 편집이 reload 뒤 GET 에 안 보임 | 그 표면의 POST 핸들러 / `config reload` 경로 — 제품 결함이므로 고친다 |
| `verify_responsive.py` 가 "화면 밖 요소" | 넓은 표·긴 입력이 칸을 밀어냄 | 표는 `.tbl-wrap`(가로 스크롤 허용 — 이 안은 검사에서 제외된다)로 감싸고, flex 자식에 `min-width:0`, 긴 `select` 에 `max-width` |
| `verify_responsive.py` 가 "probe 결과 없음" | 페이지가 렌더되기 전에 DOM 을 떴다 | 느린 PC 에서는 `--widths` 를 줄이거나 다시 실행. 하네스는 끝나면 `index.html` 을 **반드시 원상 복구**한다 |
| `test_console_0915` 가 이 PC 에서만 실패 | 로컬 config.json 의 provider 가 PATH 에 없는 에이전트(opencode) | 테스트는 완주만 요구한다(2026-09-18 이후) — 환경은 [IMPLEMENTATION_PLAN_0918.md §0.1](history/2026-09-18/IMPLEMENTATION_PLAN_0918.md) |
| 하네스가 포트 충돌로 시작 실패 | 이전 실행의 서버가 남아 있음 | `Get-Process python | Stop-Process` (자기 것만) 뒤 재실행 |
| `verify_all.py --quick` 뒤 문서 숫자가 안 바뀜 | 의도된 동작 — 부분 실행은 문서를 덮지 않는다 | 전체 실행으로만 기록 |
