# 2026-09-18 구현 계획 (2차) — 이전 회차 잔여 작업 + 신규 요청 6건

> 입력: [IMPLEMENTATION_PLAN_0918.md](IMPLEMENTATION_PLAN_0918.md) 의 미완 단계(I·J·K + 문서 병합) + 사용자 신규 요청 6건(§2).
> 규칙(변함없음): 모든 새 설정은 파일(config/tuning/agents/server)에 **기본값까지 명시**하고 `setup/*.example.*` 과
> [BRINGUP_GUIDE.md §3.2](../../BRINGUP_GUIDE.md) 에 같이 적는다. **모든 기능은 CLI · Web UI · MCP 세 창구에 같은 이름으로 존재**해야 하며
> `tools/verify/verify_surface_align.py` 의 CAPS 표에 행을 추가한다.

## 0. 이전 회차(1차) 상태 — 이어받은 시점의 판정

| 단계 | 상태 | 남은 것 |
|---|---|---|
| A 불용어 · B 로그 총량 · C 프론트(복사·사이드바 접기·반응형 CSS) · D 외부 바인드+전제 · E 앙상블 · F 질의 경로(llm_after_*, answer_mode, refs) · G MCP · H headless | **코드 반영됨** (컴파일·JSON 파싱 OK) | 문서 병합(초안 2건이 `docs/_drafts/` 에만 있음), ENSEMBLE.md 등 참조만 있고 파일이 없는 문서, README §0 · BRINGUP §3.2 행 |
| I 연동·기본값·카탈로그 테스트 | 미착수 | `config fill-defaults` · `/api/models/test_catalog` · `verify_settings_sync.py` · `/api/env` · 새 기능의 Settings UI |
| J 스윕 | 미착수 | §2.6 의 파이프라인 페이지와 합쳐 구현 |
| K 릴리스 노트 · 테스트 가이드 · 검증 · 정렬 | 미착수 | `RELEASE_NOTES.md` · `TESTING_GUIDE.md` · `VERIFICATION_0918.md` · `verify_surface_align.py` 확장 · `__version__` 3.1.0 |
| 단위 테스트 | 258 중 2 실패 | 아래 |

**실패 2건의 원인과 조치**

1. `test_rag_federation.test_plugin_tools_dir` — G 단계가 넣은 `plugin_rescan_s`(5초) 폴더 재검사 주기 때문에, 테스트가 새 플러그인 파일을 쓴 직후 `load_plugins()` 를 부르면 5초 안이라 스냅샷을 그대로 돌려준다. **제품은 의도대로**(tools/list 마다 listdir 를 돌지 않기 위한 것)이고 테스트가 새 동작을 모른 것이다. 조치: 테스트에서 `_PLUGIN_STATE["checked"] = 0` 으로 "주기가 지났다" 를 흉내 내어 **시그니처 변화 감지 경로**를 그대로 검증한다(`force=True` 로 우회하면 그 경로를 잃는다).
2. `test_console_0915.test_health_reports_console` — 이 PC 의 `config.json` 은 `llm_roles.rerank.model="anthropic/claude-sonnet-4-5"` 라 카탈로그 provider 자동 해석이 `headless:opencode` 를 고르는데 opencode 가 PATH 에 없어 `health` 가 FAIL(exit 1) 이다. 테스트의 목적은 "좁은 콘솔 인코딩에서 죽지 않고 `console_encoding` 행을 낸다" 이므로 exit 0 을 요구하지 않고 **완주(`health:` 요약 줄 존재) + Traceback 없음** 으로 바꾼다. 환경 자체는 [IMPLEMENTATION_PLAN_0918.md §0.1](IMPLEMENTATION_PLAN_0918.md) 의 안내대로 사용자가 고른다.

## 1. 요청 판정표

| # | 요청 | 판정 | 크기 | 설계 |
|---|---|---|---|---|
| 1 | headless 전달 시 WinError 206 (context_chars 39,719) | 진행 | S | §2.1 — 원인은 프롬프트를 **argv** 로 넘긴 것. stdin 기본 + 길이 초과 자동 전환 가드 |
| 2 | 채널별 top-k 안/밖 가중치 (rerank 로 가는 후보) | 진행 | M | §2.2 — 융합 단계에 채널×순위 구간 가중 + 채널별 리랭크 창 보장 주입, 모두 tuning.json |
| 3 | RRF 뒤 / 리랭크 뒤 값을 그대로 응답으로 (client LLM 이 처리) | 진행 | M | §2.3 — `output_mode = answer \| fused \| reranked \| context` |
| 4 | 규칙(동의어) 방향 질문 | 답변 + 소규모 | S | §2.4 — acronym/synonym 양방향, alias/related/exclude 일방. `rules explain` + `related_symmetric` |
| 5 | 그래프 연결 상태 프로파일·평가 도구 | 진행 | L | §2.5 — `graph profile` (규모·연결성·허브·규칙 기여·질의 활용·제안 + 이력 비교) |
| 6 | 튜닝·토글 통합 페이지 (flow 세로 블록 + 우측 상세 + 범위 스윕) | 진행 | L | §2.6 — Pipeline 페이지 = 1차 J(스윕) 포함, 사이드바 토글 이관 |

## 2. 설계

### 2.1 headless WinError 206 (요청 1)

**진단.** `WinError 206 (The filename or extension is too long)` 은 Windows `CreateProcess` 의 **명령줄 32,767자 한계**다.
2026-09-16 의 요청(req_2794)에서 agents.json 의 opencode 항목이 `"{prompt}"` 를 인자로 두고 `prompt_mode` 가 없어 코드 기본값 `arg` 를 탔고,
시스템 프롬프트 + 컨텍스트 39,719자가 argv 에 들어가 한계를 넘었다. 1차 회차 H 단계가 이미 (a) `with_defaults()` 에서 `prompt_mode` 기본을
`stdin` 으로, (b) 저장소 `agents.json` 4개 항목을 모두 `stdin` 으로 바꿨으므로 **같은 파일을 쓰는 한 재발하지 않는다**. 남은 구멍은 셋이다.

| 구멍 | 조치 |
|---|---|
| `HeadlessLLM._render/_complete` 의 코드 기본값이 여전히 `"arg"` (agents.json 을 거치지 않고 dict 로 만들면 arg) | 코드 기본값을 `"stdin"` 으로 |
| 운영자가 `arg` 를 고른 채 긴 프롬프트가 오면 OSError 로 죽고 원인 문구가 없다 | agents.json 키 **`arg_max_chars`**(기본 30000; Windows 한계 32767 에서 여유) — argv 총 길이가 넘으면 **자동으로 stdin 으로 전환**(템플릿에 `{prompt_file}` 이 있으면 file), trace/로그에 `prompt_mode_fallback: arg→stdin (N chars > arg_max_chars)` 를 남긴다. `0` = 가드 끔 |
| stdin 을 받지 않는 에이전트가 있을 수 있다 | 문서에 `file` 모드 예시(`opencode run -m {model} -f {prompt_file} "첨부 파일의 지시를 따르라"`) 와 `models test --live` 로 확인하는 절차 |

- 더 나은 방향인가: **stdin 이 가장 낫다** — 길이 한계 없음, 프로세스 목록에 프롬프트가 노출되지 않음, 임시 파일 정리 불필요. opencode/claude/codex 모두 파이프 입력을 받는다. `file` 은 stdin 을 못 받는 에이전트의 차선.
- 검증: `tests/test_headless_switch.py` — mock 에이전트로 (1) `arg` + 40,000자 프롬프트 → 자동 stdin 전환 후 정상 응답 + 폴백 표시, (2) `arg_max_chars=0` 이면 전환하지 않음(짧은 프롬프트로 확인), (3) config.json 만 바꿔 API(mock provider) ↔ headless(mock agent) 왕복.

### 2.2 채널별 top-k 가중치 (요청 2)

지금 `fusion.fuse()` 는 채널 가중치 `w_c` 하나를 모든 순위에 같게 곱한다(`w_c/(k+rank)`). 요구는 "**그 채널의 top-k 안에 든 후보와 밖의 후보를
다르게** 대접하라" 이다. 두 장치를 넣는다.

1. **구간 가중** (`fusion.fuse`): 채널 `c` 의 순위 `r` 에 대해 `w = w_c × (topk_w_c if r ≤ topk_n_c else tail_w_c)`. `topk_n_c = 0` 이면 끔.
   보조 리스트(`fts_rule`, `fts_alt1`, `vector_alt1`, `fts_rel1`, `ext_<src>`)는 이름 앞부분의 기본 채널 값을 물려받는다.
   적용 결과는 `Hit.boosts["topk_fts"]=1.5` 처럼 남겨 근거 표·워터폴에서 보인다(`apply_boosts` 는 이제 boosts 를 **덮지 않고 합친다**).
2. **리랭크 창 보장 주입** (`channel_inject`): 채널마다 상위 n개는 RRF 순위와 무관하게 리랭크 후보 창 안으로 올린다 — `external_rag_inject` 와 같은
   방식(창 끝 요소 바로 위의 fused). `why` 에 `inject:fts` 를 남긴다.

| tuning.json 키 (stage `rrf_fuse`) | 기본 | 뜻 |
|---|---|---|
| `fts_topk_n` · `vector_topk_n` · `graph_topk_n` · `doc_vector_topk_n` · `external_topk_n` | 0 | 그 채널에서 "top-k 안" 으로 볼 순위 (0 = 구간 가중 끔) |
| `fts_topk_w` · `vector_topk_w` · `graph_topk_w` · `doc_vector_topk_w` · `external_topk_w` | 1.0 | top-k 안 후보의 채널 가중 배율 |
| `fts_tail_w` · `vector_tail_w` · `graph_tail_w` · `doc_vector_tail_w` · `external_tail_w` | 1.0 | top-k 밖 후보의 배율 (0 = 밖은 버림) |
| `channel_inject` | `""` | `fts:2,vector:2,graph:1` 형식 — 채널별 리랭크 창 보장 주입 수 |

- 세 창구: `tuning set fts_topk_n=5 fts_topk_w=1.5` · Web 튜닝 표/Pipeline 페이지(rrf_fuse 블록) · MCP `wiki_query(overrides={"tuning": {...}})`
  (요청 단위 튜닝 오버라이드가 MCP 에도 통하는지 확인하고 없으면 연다).
- 버린 대안: 리랭크 입력에서 채널별로 top-k 만 남기는 것 — RRF 의 "합의" 신호를 버리게 되고 fusion_method 와 겹친다. 가중 배율이 더 작고 되돌리기 쉽다.

### 2.3 출력 모드 — 중간 산출물을 그대로 응답 (요청 3)

`answer_mode`(grounded|best_effort) 는 "답을 **어떻게** 만들까" 이고, 새 **`output_mode`** 는 "어디까지 만들고 **무엇을** 돌려줄까" 다. 서로 독립.

| `output_mode` | 멈추는 곳 | 응답에 담는 것 | `result_type` |
|---|---|---|---|
| `answer` (기본) | 끝까지 | 지금과 같음 | grounded / best_effort / … |
| `fused` | 융합 · 부스트 뒤 (리랭크 **전**) | `candidates[]` = 상위 `output_candidates_n` 후보: `chunk_id, doc_id, ext_id, heading, text, scores{채널:점수}, ranks{채널:순위}, fused, boosts, why`; `lists{채널: [(chunk_id, score)]}`(채널별 상위 `output_list_n`); `stages.fused_order / boost_order` | `candidates_fused` |
| `reranked` | 리랭크 뒤 (문서 확장·컨텍스트 **전**) | 위 + `rerank` 점수, `stages.rerank_before`(리랭크 입력 순서) / `final_order`(출력 순서) — "리랭크 입력 전후" 를 한 응답에 | `candidates_reranked` |
| `context` | 컨텍스트 구성 뒤 (답변 LLM **전**) | `context.text`(그대로 LLM 에 넣을 수 있는 [C#] 블록) + `refs` + `evidence` 판정 | `context` |

- 구현: `RoundConfig.stop_after ∈ {None, "boost", "rerank", "context"}` 를 두고 `_retrieve` 가 그 지점에서 나머지를 `prof.skipped(…, "output_mode=…")` 로
  건너뛴다. `run()` 은 output_mode ≠ answer 면 evidence(→ `context` 만 수행)·answer·claim·evolve 를 건너뛰고 `answer` 필드에는 후보 표를 마크다운으로 렌더해
  넣는다(세 창구가 같은 본문을 보이도록). `hits` 는 유지. `rerun_capture` 는 그대로 동작하므로 fused 결과에서 ⟲ 로 리랭크부터 이어 볼 수 있다.
- 설정: `config.json output_mode`(answer) · tuning `output_candidates_n`(0 = rerank_candidates) · `output_list_n`(20) · `output_chunk_chars`(0 = 전문).
- 세 창구: CLI `query "…" --output fused|reranked|context` (+ `--json`) · Web Ask 의 "출력" 드롭다운(답변 / 융합 후보 / 리랭크 후보 / 컨텍스트) + 후보 표 렌더 + 복사 ·
  MCP `wiki_query(output_mode=…)` → `structuredContent.candidates / context`.
- 버린 대안: `answer_mode` 에 값을 추가 — best_effort 와 조합이 안 된다("best_effort 로 컨텍스트만" 은 유효한 조합).

### 2.4 규칙 방향 (요청 4) — 답

`query_rules.py` 의 색인(`_build_index`)을 기준으로:

| 유형 | 방향 | 이유 |
|---|---|---|
| `acronym` `A: [B, C]` | **양방향** — A 가 있으면 B·C 로, B 가 있으면 A·C 로 확장 | 완전 동치 |
| `synonym` `A: [B, C]` | **양방향** (같은 방식) | 준동치 |
| `alias` `A: B` | **일방** A → B 치환 (FTS 에는 A OR B 를 넣지만 시드는 B, B 가 질의에 있어도 A 로 넓히지 않는다) | 표기 **정규화**가 목적 |
| `related` `A: [B]` | **일방** A → B 보조 리스트 | 정밀도 보호 |
| `exclude` | 일방 | — |

양방향이 필요하면: 동치면 `acronym`/`synonym` 에 넣는다(정답). `related` 를 양쪽으로 쓰고 싶으면 새 튜닝 **`related_symmetric`**(기본 false) 을 켠다 —
B 가 질의에 있을 때 A 를 보조 리스트로 넣는다. `alias` 는 일부러 일방으로 둔다(양방향이면 "정규 표기" 가 없어진다).

- 보이게 하기: **`rules explain <용어>`** — 그 말이 어느 유형·어느 방향으로 무엇을 끌어오는지 표로. CLI `rules explain` · `GET /api/query_rules/explain?term=` ·
  Web 질의 규칙 사전 탭의 "이 말은 어떻게 퍼지나" 칸 + 유형 설명에 방향 열 · MCP `wiki_rules(action=explain|test, term|q)` (읽기 전용).

### 2.5 그래프 프로파일 · 평가 (요청 5)

목적: "규칙을 바꾸면 그래프가 어떻게 달라지는가" 를 숫자로 보고, 어디를 고칠지 제안받는다. 모듈 `llmwiki/graph_profile.py`.

| 절 | 지표 | 어디서 |
|---|---|---|
| 규모 | 엔티티/관계/멘션/커뮤니티 수, 엔티티 유형별, 관계 유형(rel)별, **출처(source: explicit/rule/human/llm/cooccur)별** | entities · relations · mentions |
| 연결성 | 연결 성분 수, 최대 성분 비율, 고립 엔티티(degree 0) 수·예시, 차수 분포(중앙값·p90·최대), **허브** 상위 10(유형 포함 — 날짜/역할 노드 경고) | relations BFS |
| 문서 커버리지 | 엔티티가 하나도 없는 문서 수·예시(유형별), 문서당 평균 엔티티, 청크당 멘션 | mentions × docs |
| 품질 신호 | 이름/별칭이 사실상 같은 엔티티 쌍(합치기 후보), 끊긴 관계(src/dst 없음), 자기 관계, cooccur 비중, 관계 weight 분포 | — |
| 규칙 기여 | `data/rules.json` 의 id_patterns · link_rules · rel 패턴 · 사전 엔티티마다 **만든 엔티티/관계 수** (0 인 규칙 = 죽은 규칙) | relations.rel · source 매핑 |
| 질의 활용 | 최근 요청 N건에서 그래프 시드가 있었던 비율, 최종 근거 중 `graph#` 로 들어온 비율, **시드가 없던 질의의 상위 키워드**(엔티티 후보) | store.requests |
| 제안 | 규칙 기반: 고립 비율↑ → id_patterns/사전 추가, 허브가 날짜형 → `dates_per_chunk` 하향, 커버리지 낮은 문서 유형 → 그 유형 사전, cooccur 비중↑ → link_rules, 중복 쌍 → alias, 시드 없는 질의 키워드 → 엔티티 사전 | `suggestions[] {kind, detail, action(어느 파일·키)}` |
| 이력 | 실행마다 `data/graph_profiles/gp_<ts>.json` 저장(보관 `graph_profile_keep`=30) → **`--compare`** 로 직전 실행과 핵심 지표 diff (규칙 바꾸고 `build graph` 뒤 비교) | — |
| (옵션) 평가 | `--eval` 이면 그래프 채널만 켠 `pipe.evaluate` hit@k 를 함께 (기존 matrix 재사용) | evalset |

- 세 창구: CLI `graph profile [--json] [--eval] [--compare] [--out md]` · Web Knowledge › **그래프 진단** 탭(지표 카드 · 허브/고립/커버리지 표 · 규칙 기여 표 · 제안 목록에 "규칙 편집으로" 버튼 · 이전 실행과 비교) · `GET /api/graph/profile[?compare=1&eval=1]` · MCP `wiki_graph_profile` (읽기 전용).
- 설정: `config.json graph_profile_keep`(30) · `graph_profile_requests`(질의 활용 표본 수, 200) · `graph_profile_hubs`(10).

### 2.6 Pipeline 페이지 — 토글·튜닝 통합 + 범위 스윕 (요청 6, 1차 J 포함)

**형태.** 새 최상위 그룹 **🧭 Pipeline** (탭 `pipeline`). 세 열:

```
┌ 흐름 ┐ ┌──────── 블록 (위→아래) ────────┐ ┌────────── 상세 ──────────┐
│ build│ │ ▣ time_scope   [on]  tune 3   12ms │ │ 제목 · 모듈 · 입출력       │
│ query│ │      ↓                            │ │ 동작 / impact              │
│evolve│ │ ▣ query_rules  [on]  tune 5    3ms │ │ 토글: [스위치] 이름 설명    │
│ watch│ │      ↓                            │ │ config 값: [입력] (admin 저장)│
│      │ │ ▣ router       [on]  tune 13   1ms │ │ 튜닝: 키 현재 기본 [입력]   │
│      │ │      ↓  …                         │ │   [요청에만] [tuning.json 저장]│
│      │ │                                    │ │ 스윕: 키 ▾ 범위 시작/끝/증가│
│      │ │                                    │ │   또는 값 목록 · 기준 요청   │
│      │ │                                    │ │   [실행] → 단계×값 격자     │
└──────┘ └────────────────────────────────────┘ └──────────────────────────┘
```

- 데이터는 `/api/architecture`(architecture.py 레지스트리 = 단계별 toggles/settings/tunables/trace) 를 그대로 쓴다. 블록마다 토글 스위치·튜닝 수·마지막 실행 ms.
- **토글 이관**: 토글 묶음(`#toggle-groups`, `buildSidebar` 가 그림)은 Pipeline 페이지로 옮긴다. 사이드바에는 프리셋 + **"이번 요청 오버라이드 요약"**(바뀐 토글·튜닝 n개, "Pipeline 에서 편집" 버튼) 만 남긴다. 프로파일 키 `sidebar_toggles: "compact"|"full"`(기본 compact) 로 예전 배치를 되살릴 수 있다. 상태는 하나(`LW.overrides`)이므로 어느 쪽에서 바꿔도 Ask 질의에 같이 적용된다.
- **튜닝 편집 두 갈래**: "이번 요청에만"(overrides.tuning — 저장 안 함) / "tuning.json 저장"(class1 이상, `/api/tuning set`). config 값(top_k 등)은 admin 만 `/api/config` 저장.
- **스윕** (= 1차 J): `llmwiki/sweep.py`. 입력: 기준 요청(`last` 또는 id; 없으면 질의 문자열로 1회 실행해 기준을 만든다), 재시작점(자동: 그 키의 단계 → `rerun.STAGE_POINT`), 키, 값(`start:stop:step` 또는 목록; 토글이면 `[false,true]`), `repeats`(1), 역할 LLM 오버라이드. 값마다 **`rerun` 재생**으로 그 단계부터만 다시 돈다. 결과 `data/sweeps/sw_<id>.json`: 값별 단계 요약(ms · 상위 N 순위 · 컨텍스트 id · 답변 · claim 수치 · result_type). `compare` = 기준(첫 값) 대비 값마다 단계별 diff.
- 세 창구: CLI `sweep run <request_id|last> --key rrf_k --range 10:100:10 [--values a,b] [--repeats N] [--from point]` · `sweep list|show|compare <id>` · Web Pipeline 상세의 스윕 폼 → 잡 → 격자(기준과 다른 셀 강조, 셀 클릭 = diff) · MCP `wiki_sweep(request_id, key, values|range, repeats)` (질의와 같은 읽기 등급, `sweep_max_values` 로 제한).
- 설정: `config.json sweep_dir`(data/sweeps) · `sweep_keep`(30) · `sweep_max_values`(20) · `sweep_max_parallel`(1) · `data/profiles.json sidebar_toggles`.
- 버린 대안: Settings › 튜닝 표에 단계 필터만 추가 — "흐름 위에서 값을 본다" 는 요구(어느 단계가 어느 값에 묶이는지)를 못 채운다. 기존 Observability › 구조·흐름 탭은 **읽기 전용**이었으므로 그것을 확장하는 대신 새 페이지로 옮기고 구조·흐름 탭은 Pipeline 으로 안내만 한다.

## 3. 실행 순서와 파일 소유

| 단계 | 작업 | 주요 파일 |
|---|---|---|
| A1 | §0 테스트 2건 + §2.1 headless | headless.py · tests/test_rag_federation.py · tests/test_console_0915.py · tests/test_headless_switch.py · agents.json · setup/agents.example.json |
| A2 | §2.2 + §2.3 (질의 경로) | fusion.py · retrieval.py(Hit) · tuning.py · query_engine.py · config.py(output_mode) · cli.py(query 플래그) · mcp.py(wiki_query) · ask.js · server.py(OVERRIDE_SAFE_KEYS) · tests |
| A3 | §2.4 + §2.5 | query_rules.py · graph_profile.py(신규) · pipeline.py · cli.py(rules/graph) · server.py(GET) · mcp.py(2 도구) · knowledge.js · settings.js(qrules) · index.html(그래프 진단 탭) · tests |
| A4 | 1차 I 단계 | cli.py(config fill-defaults · models test --catalog) · server.py(/api/models/test_catalog · /api/env) · settings.js · models_catalog.py · config.py(fill_defaults) · setup/*.example.json · config.json/tuning.json/query_rules.json · verify_settings_sync.py |
| A5 | 스윕 백엔드(1차 J) | sweep.py(신규) · server.py(/api/sweep) · cli.py(sweep) · mcp.py(wiki_sweep) · config.py(sweep_*) · tests |
| B1 | §2.6 Pipeline 페이지 | index.html · core.js · pipeline.js(신규) · style.css · profiles.py · architecture.py(단계 메타 보강) |
| B2 | 문서 | docs/_drafts 병합 → STOPWORDS.md · LOG_QUOTA.md · ENSEMBLE.md · ANSWER_MODES.md · GRAPH_PROFILE.md · PIPELINE_PAGE.md(스윕 포함) · HEADLESS 절(BRINGUP §4.3) · MCP.md · README · BRINGUP §3.2 · RELEASE_NOTES.md · TESTING_GUIDE.md · `__version__` |
| C | 검증(1차 K) | verify_surface_align.py 확장 · 단위/CLI/Web/MCP/UI 하네스 · VERIFICATION_0918.md |

A1~A5 는 병렬(같은 파일은 다른 영역만), B1 은 A2·A5 뒤, B2 는 A 뒤 B1 과 병렬, C 는 마지막.

## 4. 새 설정 키 총람 (BRINGUP §3.2 에 복사)

| 파일 | 키 | 기본 | 요청 |
|---|---|---|---|
| agents.json | `prompt_mode`(코드 기본 stdin) · `arg_max_chars` | stdin · 30000 | 1 |
| tuning.json | `{fts,vector,graph,doc_vector,external}_topk_n / _topk_w / _tail_w` · `channel_inject` | 0 / 1.0 / 1.0 · "" | 2 |
| config.json | `output_mode` | answer | 3 |
| tuning.json | `output_candidates_n` · `output_list_n` · `output_chunk_chars` | 0 · 20 · 0 | 3 |
| tuning.json | `related_symmetric` | false | 4 |
| config.json | `graph_profile_keep` · `graph_profile_requests` · `graph_profile_hubs` | 30 · 200 · 10 | 5 |
| config.json | `sweep_dir` · `sweep_keep` · `sweep_max_values` · `sweep_max_parallel` | data/sweeps · 30 · 20 · 1 | 6 |
| data/profiles.json | `sidebar_toggles` | compact | 6 |
