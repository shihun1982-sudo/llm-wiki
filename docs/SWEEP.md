# SWEEP — 저장된 질의 위에서 **값 하나만 바꿔 N회** 돌리고 단계별로 비교하기

> CLI: `python -m llmwiki sweep run <request_id|last> --key rrf_k --range 10:100:30`
> API: `GET /api/sweep/keys` · `GET /api/sweep[?id=]` · `POST /api/sweep` (잡)
> MCP: `wiki_sweep`
> 화면: 🧭 Pipeline 페이지의 스윕 폼 ([PIPELINE_PAGE.md](PIPELINE_PAGE.md) — 다른 작업에서 작성 중)
> 설계 원문: [IMPLEMENTATION_PLAN_0918.md §2.15](history/2026-09-18/IMPLEMENTATION_PLAN_0918.md) · [IMPLEMENTATION_PLAN_0918_2.md §2.6](history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md) · 재생 원리는 [RERUN.md](RERUN.md)

## 0. 한 장 요약

| 항목 | 내용 |
|---|---|
| 무엇 | 지난 질의(`request_id`, `last` 가능)를 기준으로 **키 하나**(`rrf_k` · 토글 `rerank` · `top_k_final` · `answer_model` …)의 값을 바꿔 가며 값마다 재실행 |
| 어떻게 | 값마다 `Pipeline.rerun(request_id, point)` — 그 키가 영향을 주는 **첫 단계(재시작점) 앞은 저장값을 재생**, 뒤만 다시 계산. `/api/query/rerun` 과 같은 경로 |
| 비교 | **첫 값(의 첫 성공 실행)이 기준**. 값마다 최종 순위·컨텍스트 id 집합·답변 텍스트·수치(ms, groundedness, 인용 수, 토큰)를 diff |
| 저장 | `<sweep_dir>/sw_<id>.json` (기본 `data/sweeps/`), `sweep_keep`(30) 건 보관 |
| 상한 | 값 개수 `sweep_max_values`(20) · 반복 `MAX_REPEATS`=10 · 동시 실행 `sweep_max_parallel`(1) |
| 권한 | 재실행과 같은 **read** (CLI `sweep`, `POST /api/sweep`). 색인·설정 파일을 바꾸지 않는다 |

## 1. 왜 이렇게 만들었나

"rrf_k 를 10, 40, 70 으로 두면 뭐가 달라지나" 를 보려고 질의를 세 번 처음부터 돌리면 LLM 질의 확장·리랭크가 매번
조금씩 달라져서, 차이가 **내가 바꾼 값 때문인지 검색이 흔들려서인지** 알 수 없다. 그래서 값마다 `rerun` 재생을 쓴다 —
앞 단계는 저장값 그대로, 바꾼 값이 처음 영향을 주는 단계부터만 다시 계산하므로 값별 차이는 그 값의 효과로 분리된다.

| 대안 | 왜 안 골랐나 |
|---|---|
| 값마다 `query` 를 처음부터 실행 | 위의 이유 — 비교 대상이 아닌 앞 단계까지 흔들린다. LLM 호출도 N배 |
| `trial run` / `eval --matrix` 로 비교 | 질문셋 전체의 평균 지표를 본다. "이 질의 하나에서 단계별로 무엇이 달라졌나" 를 보여 주지 않는다 |
| 엔진을 단계 함수로 쪼개 중간부터 이어 붙이기(resume) | RERUN.md §1 에서 기각한 그 이유 그대로. 스윕은 재실행 위에 얹혀 **엔진 로직을 하나도 복제하지 않는다** |

## 2. 무엇이 재생되고 무엇이 다시 계산되나

키를 주면 `sweep.classify_key()` 가 종류(kind)와 재시작점(point)을 정한다. `--from` 으로 강제할 수 있다(`rerun --points` 의 id 중 하나).

| 키 표기 | kind | 재시작점 결정 | 예 |
|---|---|---|---|
| 토글 이름 · `toggles.<name>` | `toggle` | `_TOGGLE_POINT` 표 → 없으면 `architecture.FLOWS["query"]` 에서 그 토글을 가진 첫 단계 | `rerank` → `rerank`, `claim_check` → `claim_check`, `external_rag` → `retrieve` |
| 튜닝 레지스트리 키 · `tuning.<key>` | `tuning`(tuning.json) 또는 `config`(config.json 값) | 키 접두 표(`doc_expand_*`→`doc_expand`, `pin_boost`→`boost`, `fusion_llm_*`→`rerank` …) → 없으면 레지스트리 stage → 재시작점 표 | `rrf_k`(config, stage rrf_fuse) → `rrf_fuse`, `top_k_final` → `context`, `answer_effort` → `answer_llm` |
| `<role>_model` · `<role>_provider` · `<role>_effort` · `llm_roles.<role>.<attr>` | `role` | answer/verify → `answer_llm`, rerank/fusion/select → `rerank`, expand/router → `plan` | `answer_model` → `answer_llm` |
| 그 밖의 config.json 스칼라 키 | `config` | `_CONFIG_POINT`(`answer_mode`→`answer_llm`, `rerank_url`→`rerank`, `graph_hops`→`retrieve`) → 없으면 `plan`(전체 재실행) | `debug_level` → `plan` |

스윕할 수 **없는** 키는 이유와 함께 거부된다(ValueError → CLI 종료 코드 2, API 400):
빌드·포렌식 단계 튜닝 값("build 뒤 eval/trial 로 비교하세요"), `rebuild` 표시 값, 결과를 바꾸지 않는 토글
(`rerun_capture`, `log_stages`, `auto_build` …: "질의 결과를 바꾸지 않아 스윕할 수 없습니다"), 질의 경로에 없는 역할
(`extract_model`: "answer/rerank/verify/expand/fusion/select 만"), 목록/객체 설정(`corpus_dirs`). 목록은 `sweep keys`.

재생 범위는 RERUN.md §2 의 표와 같다(뒤로 갈수록 누적). 각 재실행은 `rerun_capture=True` 로 자기 체크포인트
(`data/reruns/req_<새 id>.json`)를 남기고, 스윕은 거기서 융합/부스트/리랭크/최종 순위(상위 `ORDER_TOP`=12)와 컨텍스트 id 를 읽는다 —
trace 에는 순서가 남지 않기 때문이다. 재시작점이 `plan` 이 아니면 기준 요청의 `build_version` 이 지금 색인과 같아야 한다(다르면 거부, RERUN.md §3).

## 3. 값 지정

| 형태 | 예 | 결과 |
|---|---|---|
| 범위 `start:stop:step` | `--range 10:100:30` | `[10, 40, 70, 100]` — 모두 정수 표기면 int, 하나라도 소수점/지수면 float(`0.1:0.9:0.2` → `[0.1, 0.3, 0.5, 0.7, 0.9]`) |
| 범위 `start:stop` | `1:5` | step 1 |
| 목록 | `--values rrf,zscore` · API `"values": [10, 60]` | 종류에 맞게 변환(튜닝 키는 레지스트리의 min/max/choices 검사) · 중복 제거 |
| 생략 | 토글 | `[false, true]` |
| 생략 | choice 키(`fusion_method`, `answer_effort`) | choices 전부 |

거부 조건: step 0 · 방향 불일치 · 10,000개 초과 · 값이 `sweep_max_values` 초과("config.json 에서 올리거나 범위를 줄이세요") · 숫자 아님.
`repeats`(기본 1, 최대 10)를 2~3 으로 두면 같은 값을 반복해 **LLM 흔들림**을 볼 수 있다.

## 4. 기준과 `compare`

`compare(record)` 는 오류 없는 실행 중 **첫 번째**(보통 첫 값의 첫 반복)를 기준으로 삼는다. 그래서 **첫 값에 지금 쓰는 값을
넣어 두는 것이 좋다** — 예: 지금 `rrf_k=60` 이면 `--values 60,10,40,100`. 첫 값이 실패하면 다음 성공 실행이 기준이 된다(`baseline` 에 표시).

| diff 대상 | 방법 | 결과 필드 |
|---|---|---|
| 최종 순위(`orders.final`, 상위 12) · 융합/부스트/리랭크 순위 | 순서 있는 목록 diff — 유사도(`difflib.SequenceMatcher.ratio`), 추가/제거/이동, `top1_same` | `hits`, `stages.<st>.order` |
| 컨텍스트 id 집합 | 집합 diff — jaccard, 추가/제거 | `context`, `stages.context.order` |
| 답변 텍스트 | unified diff(최대 `ANSWER_DIFF_LINES`=40줄) + 유사도 + 글자 수 차 | `answer` |
| 수치 | 기준 대비 Δ: `ms`, `groundedness`, `n_citations`, `tokens` | `*_delta` |
| 단계 상태 | `STAGES` 9열(`rrf_fuse boost channel_inject rerank doc_expand context evidence_check answer_llm claim_check`)마다 present/replayed/skipped/meta 변화 | `stages.<st>.changed` |
| best 힌트 | groundedness 최고 · 가장 빠름 · 인용 최다 · 토큰 최소 | `best` |

텍스트 격자(`render_text`) 표기: `ms` · `⟲`=재생(저장값) · `–`=건너뜀 · `·`=없음 · `★`=기준과 다름.
`STAGES` 의 열은 trace 단계 이름의 **가족**이다(예: `rerank` 열 = `rerank`/`rerank_llm`/`rerank_local`/`rerank_api` …) — 재생되면 이름이 달라지기 때문.

## 5. 설정 (`config.json`)

| 키 | 기본 | 뜻 | 확인 |
|---|---|---|---|
| `sweep_dir` | `"data/sweeps"` | 결과 폴더. `data/…` 는 `data_dir` 아래로 본다(격리 환경도 따라감). 절대 경로 가능 | `config show --effective` |
| `sweep_keep` | `30` | 최근 몇 건을 남길지(저장할 때마다 정리). `0` = 무제한 | `sweep list` |
| `sweep_max_values` | `20` | 한 스윕의 값 개수 상한 — 값마다 재실행 = LLM 호출이므로 폭주 방지 | `GET /api/sweep/keys` 의 `max_values` |
| `sweep_max_parallel` | `1` | 값을 동시에 몇 개 돌릴지(`ThreadPoolExecutor`). 1 = 순차. 결과 순서는 값 순서로 고정 | `sweep show <id> --json` 의 `parallel` |

네 키 모두 `config.json` 과 `setup/config.example.json` 에 기본값으로 적혀 있다. `setup/config.example.headless.json` ·
`setup/config.example.pat-gateway.json` 에는 없다(→ §10).

## 6. 세 창구

### CLI
```bat
python -m llmwiki sweep keys                                          :: 스윕할 수 있는 키 (kind · type · point · 범위/choices)
python -m llmwiki sweep run last --key rrf_k --range 10:100:30        :: 마지막 저장 요청 기준, 융합부터 4회
python -m llmwiki sweep run 3255 --key rerank                         :: 토글 → false,true, 리랭크부터
python -m llmwiki sweep run 3255 --key answer_effort --repeats 2      :: choice 전부 × 2회
python -m llmwiki sweep run --query "ISSUE-2001 근본 원인" --key top_k_final --values 4,8,12   :: 기준 요청이 없으면 한 번 실행해 만든다
python -m llmwiki sweep run 3255 --key claim_support_min --range 0.4:0.8:0.2 --from claim_check --log
python -m llmwiki sweep list --n 10
python -m llmwiki sweep show 20260918-101500-ab12
python -m llmwiki sweep compare 20260918-101500-ab12                  :: 격자 + 값마다 답변 unified diff
```
`--json` 이면 `{record, compare}`. 종료 코드: 성공 실행 1건 이상 0, 전부 실패 1, 키/값 오류 2. `--log` 를 주지 않으면 각 재실행은 요청 기록만 남기고 query_log·자가진화 캡처에는 넣지 않는다.

### Web
- `GET /api/sweep/keys[?n=20]` → `{keys[], points[], stages[], max_values, max_repeats, saved[]}` — 폼이 쓸 목록.
- `GET /api/sweep` → `{sweeps[]}` · `GET /api/sweep?id=<sw id>` → `{record, compare, text}` (없으면 404).
- `POST /api/sweep {"action":"run","key","request_id"|"query","range"|"values","repeats","from","llm":{"answer":{"model","provider","effort"}},"log"}` → **잡**으로 시작(다른 잡과 같은 진행/결과 API). 잡 결과 = `{record, compare, text}`.
  요청 단위 `overrides`(사이드바 토글 등)도 모든 값에 공통으로 적용된다.
- 역할 제한: `kind` 가 `config`/`role` 인 키와 `llm` 오버라이드는 `_filter_overrides` 를 거친다 — admin 이 아니면 `OVERRIDE_SAFE_KEYS`
  (`top_k_*`, `rrf_k`, `rerank_candidates`, `context_*`, `answer_*`, `debug_level`, `*_model/provider/effort`, `answer_mode`, `output_mode` …)와
  `security.json overrides.allow_extra` 에 있는 키만 허용, 아니면 **403** 과 이유. 토글·튜닝 키는 제한 없음.
- 화면: 🧭 Pipeline 페이지의 스윕 폼([PIPELINE_PAGE.md](PIPELINE_PAGE.md)). 1차 계획(§2.15)의 "Quality › 단계 스윕" 위치는 2차 계획 §2.6 에서 Pipeline 페이지로 바뀌었다.

### MCP
`wiki_sweep(request_id?, key, values?|range?, repeats?, from?)` — 읽기 등급. `key` 없이 부르면 스윕할 수 있는 키 목록 + 재시작점 + 저장된 요청 10건 + `max_values`.
결과 `content` 는 `render_text` 격자, `structuredContent = {record(답변 2,000자로 잘림), compare}`. `log` 는 항상 false. 역할 LLM 오버라이드(`llm`)는 MCP 인자에 없다(Web/Python 만).

## 7. 실제 예

```bat
python -m llmwiki query "DMA underrun 이 왜 발생하나" --trace      :: 기준 요청 (rerun_capture 켜져 있어야 함)
python -m llmwiki sweep run last --key rrf_k --values 60,10,40,100  :: 첫 값 60 = 지금 값 → 기준
```
격자에서 `rrf_fuse` 열부터 값이 나오고 그 앞은 재생(`⟲`)이다. `boost`/`rerank`/`context` 열의 `★` 는 순위·컨텍스트가 기준과 달라졌다는 뜻이고,
아래 줄 "최종 순위 유사도 0.83 (추가 1 · 제거 1 · 이동 3) · 컨텍스트 jaccard 0.75 · 답변 유사도 0.62" 가 값별 차이 요약이다.

```bat
python -m llmwiki sweep run last --key rerank                        :: [false, true], 재시작점 rerank
```
`false` 행의 `rerank` 열은 `–`(건너뜀), `true` 행은 시간이 찍힌다. 리랭크를 끈 쪽이 groundedness 가 얼마나 떨어지는지 `best` 힌트와 Δ 로 본다.

## 8. 검증

```bat
python -m llmwiki sweep keys | Select-String "^rrf_k|^rerank |^answer_model"
python -c "from llmwiki import sweep as s; print(s.resolve_values('rrf_k','10:100:30'), s.resolve_values('rerank'), s.classify_key('rrf_k')['point'])"
python -m llmwiki sweep run last --key rrf_k --range 10:100:30 --json | python -c "import json,sys; d=json.load(sys.stdin); print(d['record']['n_ok'], d['compare']['baseline'])"
python tools\verify\verify_surface_align.py        :: CAPS 표에 sweep / /api/sweep / wiki_sweep 행
```
스윕 전용 단위 테스트 파일은 아직 없다(`tests/test_sweep*.py` 없음). `tests/test_rerun_0917.py` 가 재생 경로를, `tests/test_tuning_arch.py` 가 MCP 도구 목록(`wiki_sweep` 포함)을 검사한다.

## 9. 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| `저장된 중간 결과가 없습니다` | `toggles.rerun_capture` 가 꺼져 있거나 `rerun_keep` 을 넘어 정리됨 → 질의를 한 번 실행하거나 `--query` 로 기준 생성 |
| `색인이 바뀌어 …`(build_version 불일치) | 기준 요청 뒤에 빌드가 있었다. `--from plan` 은 허용되나 사실상 전체 재실행 |
| `값이 N개 — sweep_max_values=20 를 넘습니다` | 범위를 줄이거나 `config.json sweep_max_values` 를 올린다 |
| `토글 X 는 질의 결과를 바꾸지 않아 …` / `빌드/운영용이라 …` | 스윕 대상이 아니다. 빌드 값은 `build` 뒤 `eval`/`trial` 로 |
| Web 403 `요청 단위 overrides 에 허용되지 않는 키` | config/role 키를 admin 이 아닌 역할이 스윕 — `security.json overrides.allow_extra` 에 키를 넣거나 admin 으로 |
| 값별 순위 열이 비어 있다(`orders` 없음) | 재실행의 체크포인트 저장 실패(`rerun_max_mb` 초과 등). `run.rerun.reason` 확인 |
| 결과 파일이 사라졌다 | `sweep_keep` 초과 정리. 붙들 스윕은 파일을 복사해 두거나 값을 키운다 |

## 10. 파일에 없는 키

| 파일 | 키 | 기본값 |
|---|---|---|
| `setup/config.example.headless.json` | `sweep_dir` · `sweep_keep` · `sweep_max_values` · `sweep_max_parallel` | `data/sweeps` · 30 · 20 · 1 |
| `setup/config.example.pat-gateway.json` | 같은 네 키 | 같음 |

`python -m llmwiki config fill-defaults --examples` 가 채운다([SETTINGS_SYNC.md](SETTINGS_SYNC.md)).

## 11. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/sweep.py` | `classify_key` · `point_for_key` · `resolve_values`/`_parse_range` · `sweepable_keys` · 저장/조회/`prune` · `run`(값마다 `request_scope + pipe.rerun`) · `stage_summary` · `compare`/`_diff_run` · `render_text` · `brief` |
| `llmwiki/config.py` | `Settings.sweep_dir/sweep_keep/sweep_max_values/sweep_max_parallel` · `sweep_record_dir()` · `SETTING_HELP` |
| `llmwiki/rerun.py` | 재시작점 표(`POINTS`/`STAGE_POINT`) · 체크포인트 저장/조회 · `check_compatible` — 스윕이 그대로 쓴다 |
| `llmwiki/cli.py` | `sweep run|list|show|compare|keys` |
| `llmwiki/web/server.py` | `GET /api/sweep/keys` · `GET /api/sweep` · `POST /api/sweep`(잡, read) · `_filter_overrides` |
| `llmwiki/auth.py` | `_READ_POST` 의 `/api/sweep` · `_READ_CLI` 의 `sweep` |
| `llmwiki/mcp.py` | `wiki_sweep` (readOnlyHint) |
| `tools/verify/verify_surface_align.py` | 세 창구 정렬 표의 스윕 행 |
