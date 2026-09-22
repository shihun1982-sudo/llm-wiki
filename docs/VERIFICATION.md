# VERIFICATION — 검증 체계와 **최신 전체 결과**

> 이 문서는 **지금**을 말한다. 아래 §1 표는 `python tools/verify/verify_all.py` 가 전체 실행을 마칠 때마다
> **스스로 갱신**하므로, 표에 적힌 날짜가 곧 마지막 전체 검증 시각이다.
> 회차별 검증 **보고서**(그때 무엇을 찾아 고쳤나)는 [history/](history/README.md) 아래에 날짜별로 있다.
>
> 무엇을 고쳤을 때 무엇을 돌리나 → [TESTING_GUIDE.md](TESTING_GUIDE.md) · 하네스 하나하나의 설명 → [tools/verify/README.md](../tools/verify/README.md)

## 0. 한 줄

```bat
python tools\verify\verify_all.py          :: 전체 (20~40분, 아래 표를 갱신한다)
python tools\verify\verify_all.py --quick  :: 빠른 묶음 (문서는 갱신하지 않는다)
python tools\verify\verify_all.py --only unit,web,mcp
```

**부분 실행(`--quick`/`--only`)은 이 문서를 건드리지 않는다.** 일부만 돌린 결과가 표에 덮이면
"검증 범위가 좁아진 것" 을 알아챌 수 없기 때문이다(실제로 한 번 그렇게 됐다).

모든 하네스는 **격리 임시 환경**(임시 폴더 + `LLMWIKI_*_PATH`, mock LLM, hash 임베더)에서 돈다 —
실제 색인·설정·로그를 건드리지 않는다. 브라우저 검사만 실제 DB 사본을 127.0.0.1 전용 서버로 읽는다.

## 1. 최신 전체 결과

<!-- VERIFY_ALL_TABLE -->

> 2026-09-20 14:01 기준 · Python 3.14.7 · 모두 격리 임시 환경에서 실행 (실제 색인·설정은 건드리지 않는다)

| 무엇 | 명령 | 결과 |
|---|---|---|
| 단위 테스트 | `python -m unittest discover -s tests` | **758/758 통과** (161s) |
| 스트레스 (30명 동시) | `python -m unittest tests.test_concurrency_0915.StressTest` | **9/9 통과** (24s) |
| 문서 ↔ 코드 정합 | `python tools/verify/verify_docs.py` | **OK** (0s) |
| CLI · Web · MCP 정렬 | `python tools/verify/verify_surface_align.py` | **OK** (0s) |
| 세 창구 동작 동등성 (CLI 프로세스 · Web · MCP) | `python tools/verify/verify_tri_surface.py --port 8879` | **58/58 통과** (5s) |
| 단계 정렬 (코드 ↔ 레지스트리 ↔ 라벨 ↔ 손잡이) | `python tools/verify/verify_stage_align.py` | **16/16 통과** (0s) |
| 설정 UI ↔ 파일 ↔ 서버 양방향 | `python tools/verify/verify_settings_sync.py --port 8934` | **64/64 통과** (3s) |
| LLM 연결 전환 (API ↔ headless, 세 창구) | `python tools/verify/verify_llm_switch.py --port 8973` | **18/18 통과** (5s) |
| 빌드 중 서비스 (30명 동시 · 채널별) | `python tools/verify/verify_build_load.py --port 8975 --users 30` | **26/26 통과** (18s) |
| CLI 전수 | `python tools/verify/verify_cli.py` | **361/361 통과** (163s) |
| Web API 전수 | `python tools/verify/verify_web.py` | **379/379 통과** (40s) |
| MCP 종단 | `python tools/verify/verify_mcp.py --quick` | **129/129 통과** (7s) |
| UI 배선 | `python tools/verify/verify_ui_wiring.py` | **OK** (0s) |
| 브라우저 렌더 | `python tools/verify/verify_browser.py --port 8901 --cdp 9401` | **OK** (24s) |
| 창 크기 대응 (폭 5종) | `python tools/verify/verify_responsive.py --port 8904` | **30/30 통과** (20s) |
| 버튼 전수 | `python tools/verify/verify_buttons.py --port 8902 --cdp 9402` | **136/136 통과** (428s) |
| 앙상블 편집기 (실제 클릭) | `python tools/verify/verify_ensemble_ui.py` | **18/18 통과** (6s) |
| 워터폴 기하 (픽셀 측정) | `python tools/verify/verify_trace_waterfall.py` | **11/11 통과** (2s) |
| 보안 · 사용자 화면 | `python tools/verify/verify_security_ui.py --port 8903 --cdp 9403` | **58/58 통과** (140s) |
| 단계 재실행 · 작업 상세 | `python tools/verify/verify_rerun_ui.py --port 8905 --cdp 9405` | **27/27 통과** (44s) |
| 협업 다중 접속 (10명) | `python tools/verify/verify_collab_many.py --people 10 --port 8906 --cdp 9406` | **20/20 통과** (32s) |
| 작업 상세 로딩 시간 | `python tools/verify/verify_act_detail_load.py --port 8909 --cdp 9409` | **7/7 통과** (18s) |
| 스케줄 화면 | `python tools/verify/verify_schedule_ui.py --port 8910 --cdp 9410` | **14/14 통과** (20s) |
| 타임아웃 · 취소 내성 | `python tools/verify/verify_timeouts.py --port 8907` | **47/47 통과** (140s) |
| 다중 클라이언트 혼합 부하 | `python tools/verify/verify_soak.py --port 8971 --seconds 60 --clients 16` | **10/10 통과** (87s) |
| 무작위 입력 내성 | `python tools/verify/verify_monkey.py` | **OK** (1849s) |
<!-- /VERIFY_ALL_TABLE -->

## 2. 무엇을 보는 검사인가 (층별로)

같은 것을 여러 번 보는 게 아니라, **서로 다른 종류의 고장**을 잡는다. 이 저장소에서 실제로 겪은
사고가 각 층의 존재 이유다.

| 층 | 잡는 고장 | 대표 하네스 | 겪은 사고 |
|---|---|---|---|
| **단위** | 로직이 틀렸다 | `unittest discover -s tests` | — |
| **정합(정적)** | 같은 것을 여러 곳에 적어 두고 한 곳만 고쳤다 | `verify_docs` · `verify_surface_align` · `verify_stage_align` · `verify_ui_wiring` | 새 MCP 도구를 만들고 문서·정렬표에 안 넣음 |
| **창구 동등성** | 창구마다 다른 답을 준다 | `verify_tri_surface`(실제 3프로세스) · `tests.test_surface_consistency` | `rules explain ""` 이 Web 400·MCP 오류인데 **CLI 만 성공** |
| **설정 왕복** | 저장은 되는데 안 먹는다 / 파일을 고쳤는데 화면에 없다 | `verify_settings_sync`(양방향) | `schedule.json` 을 고쳐도 새로고침에 예전 목록 |
| **창구 전수** | 한 창구의 어떤 경로가 죽었다 | `verify_cli` · `verify_web` · `verify_mcp` | 권한 거부가 500 으로 나감 |
| **화면 실제 동작** | 그려지기는 하는데 **눌러도 아무 일이 없다** | `verify_browser`(콘솔 오류 0) · `verify_buttons`(전 버튼 클릭) · `verify_responsive` | `ask.js` 한 줄 오류로 **모든 핸들러 등록이 죽음** — 버튼·API 검사는 그때도 통과하고 있었다 |
| **부하·실패 내성** | 사람이 몰리거나 LLM 이 죽으면 무너진다 | `verify_build_load`(빌드 중 30명) · `verify_timeouts`(실패를 일부러 일으킴) · `verify_soak` · `verify_monkey` | 전체 리빌드 중 질의가 5.5초 대기 |
| **연결 이식성** | 다른 환경에서 LLM 에 못 붙는다 | `verify_llm_switch`(API ↔ headless 전환) · `verify_live_models`(실모델) | mock 에서만 되던 모델 이름 표기 |

> **세 층이 서로를 대신하지 못한다.** 2026-09-20 에 버튼 클릭 검사와 API 검사가 **둘 다 통과하는데도**
> 화면의 모든 핸들러가 죽어 있던 일이 있었다. 콘솔 오류를 모으는 층이 없었으면 못 찾았다.

## 3. 하네스를 새로 만들 때의 규칙

1. **격리부터.** 임시 폴더 + `LLMWIKI_*_PATH`. 특히 `LLMWIKI_LOGS_DIR_PATH` — 로그 폴더는 프로세스
   전역이라 임시 설정을 만들어도 로그만 실제 폴더를 가리킨다(그래서 한 번 감사 기록을 잃었다).
2. **빈 통과를 실패로 본다.** 비교 대상이 없거나 양쪽 다 비어 있으면 통과가 아니다.
3. **변이로 확인한다.** 새 검사를 넣었으면 고친 것을 일부러 되돌려 **실패하는지** 본다.
4. **목록에 올린다.** `tools/verify/README.md` 에 줄을 더한다 — 빠지면 `verify_docs` 가 잡는다.
5. **`verify_all.py` 의 `SUITES` 에 등록한다.** 등록하지 않으면 회차 마감 때 돌지 않는다.

자세한 내용과 예시는 [TESTING_GUIDE.md §2.5](TESTING_GUIDE.md).

## 4. 다른 환경에서 처음 돌릴 때

```bat
python -m llmwiki health                     :: 환경·프로바이더·DB·디스크
python -m llmwiki build --full --trace       :: 색인
python -m llmwiki build verify               :: 색인 정합
python tools\verify\verify_all.py            :: 전부
```

실패한 줄만 [BRINGUP_GUIDE.md §10](BRINGUP_GUIDE.md) 의 증상표로 간다.
브라우저(Edge/Chrome)가 없는 서버에서는 화면 검사가 SKIP 되며, 그것은 실패가 아니다.

## 5. 회차별 검증 보고서 (기록)

| 회차 | 무엇을 확인했나 |
|---|---|
| [2026-09-19](history/2026-09-19/VERIFICATION_0918.md) | 요청 23건의 구현·확인, 설정 양방향 하네스 신설, "켜도 아무 일도 없던 기능 3건" |
| [2026-09-17](history/2026-09-17/VERIFICATION_0917.md) | 전면 재검토 — 코드·문서·전 기능·동시성·실패 경로·MCP 확장성 |
| [2026-09-16](history/2026-09-16/VERIFICATION_0916_2.md) · [(1차)](history/2026-09-16/VERIFICATION_0916.md) | 요청 이력·모델 화면·협업 / MCP 종단 완주 |
| [2026-09-15](history/2026-09-15/VERIFICATION_0915.md) | Web UI·CLI 전 기능 검증의 출발점 |
