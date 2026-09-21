# ANSWER_MODES — 질의 경로의 선택지: `answer_mode` · `output_mode` · 융합/리랭크 뒤 LLM 토글 · `degrade_on_llm_failure`

> 설정: `config.json` 의 `answer_mode`(grounded) · `output_mode`(answer) · `toggles.llm_after_fusion`(false) · `toggles.llm_after_rerank`(false) · `toggles.degrade_on_llm_failure`(true)
> 튜닝: `refs_preview_chars`(200) · `output_candidates_n`(0) · `output_list_n`(20) · `output_chunk_chars`(0) · `fusion_llm_candidates`(0) · `fusion_llm_drop_penalty`(0.3) · `post_rerank_llm_k`(0)
> 설계 근거: [IMPLEMENTATION_PLAN_0918.md §0.2 · §2.5 · §2.9](history/2026-09-18/IMPLEMENTATION_PLAN_0918.md) · [IMPLEMENTATION_PLAN_0918_2.md §2.3](history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md)
> 검증: `tests/test_output_mode.py`(출력 모드 9건) · `tests/test_answer_modes.py`(답변 모드·실패 처리·두 LLM 단계 11건)

## 0. 한 장 요약

```
output_mode  "어디까지 만들고 무엇을 돌려줄까"   answer(끝까지) | fused(융합·부스트 뒤) | reranked(리랭크 뒤) | context(컨텍스트까지, 답변 LLM 생략)
answer_mode  "답을 어떻게 만들까"               grounded(근거만; insufficient 면 LLM 생략) | best_effort(근거 부족해도 LLM, [C#]+[BK])
두 값은 독립 — "best_effort 로 컨텍스트만" 도 유효한 조합. 결과에는 항상 result_type 과 refs(LLM 에 실제로 전달된 근거) 가 있다.
```

| 창구 | output_mode | answer_mode | 튜닝 오버라이드 |
|---|---|---|---|
| CLI | `query "…" --output fused\|reranked\|context [--json]` | `query "…" --answer-mode best_effort` | `query "…" --tuning "refs_preview_chars=400"` |
| Web | Ask 의 **출력** 드롭다운(`#ov-output`: 답변 / 융합 후보 / 리랭크 후보 / 컨텍스트만) → 후보 표 + ⧉ 후보 복사 · JSON 복사 / ⧉ 컨텍스트 복사 + REF 목록 | 사이드바 **answer_mode** 드롭다운(`#ov-answer-mode`) → `overrides.answer_mode` | Pipeline 페이지 "이번 요청에만" → `overrides.tuning` |
| MCP | `wiki_query(output_mode=…)` → `structuredContent.candidates/lists/stages` 또는 `context/refs` | `wiki_query(overrides={"answer_mode": "best_effort"})` (전용 인자 없음) | `wiki_query(overrides={"tuning": {…}})` |

> **현재 코드 상태(2026-09-19 실측)** — 이 문서의 모든 항목이 **동작한다**. `tests/test_answer_modes.py` 가 세 가지를 고정한다:
> best_effort 가 insufficient 에서도 LLM 을 부르는지, `degrade_on_llm_failure` 를 끄면 `result_type=error` 로 끝나는지,
> 두 LLM 토글이 trace 에 단계를 만들고 순위·컨텍스트를 실제로 바꾸는지.
>
> 2026-09-18 시점에는 셋 다 **설정·프롬프트·결과 필드만 있고 `QueryEngine.run()` 이 연결하지 않아** 켜도 아무 일도 일어나지 않았다.
> 연결 지점은 `query_engine.py` 의 답변 호출부(`mode`/`verdict`/`reasons`/`degrade` 전달)와 `_retrieve()` 안의 두 단계 호출이다.
> 다음 회차에 같은 실수를 반복하지 않도록: 새 토글은 `config.py` 등록 + 단계 구현 + `architecture.py` 행 + 그 단계가 실제로 도는 것을
> 확인하는 테스트까지가 한 묶음이다([TESTING_GUIDE.md §2](TESTING_GUIDE.md)).

## 1. 왜 이렇게 나눴나 (설계 근거)

| 결정 | 이유 · 버린 대안 |
|---|---|
| `output_mode` 를 `answer_mode` 값으로 넣지 않고 별도 키로 | best_effort 와 조합이 안 된다("best_effort 로 컨텍스트만" 은 유효). 중간 산출물은 답변 방식과 직교 |
| 중간 산출물 모드에서 `answer` 칸에 마크다운 표를 넣는다 | 세 창구(CLI 본문·Web 답변 칸·MCP text)가 **같은 본문**을 보이도록. 구조화 데이터는 `candidates/lists/stages/context/refs` 에 따로 |
| 중간 산출물은 캐시를 읽지도 쓰지도 않는다 | 캐시에는 완성된 답이 들어 있고 키에 output_mode 가 없다. 중간 산출물은 LLM 없이 싸게 다시 만든다. 반대로 후보 표가 캐시에 들어가면 다음 일반 질의가 표를 답으로 받는다 |
| `best_effort` 는 별도 프롬프트 파일 | `answer_system.md`+`answer_guide.md` 는 "컨텍스트 밖은 쓰지 말라" 가 규칙이라 문구 몇 줄로 뒤집을 수 없다. `answer_best_effort.md` 는 자체 뼈대(핵심 / 문서 근거 / 배경 지식 / 미확인)와 `[BK]` 규칙을 갖는다 |
| `[BK]` 문장은 claim 검증에서 `background` | 배경 지식 문장에 문서 근거를 요구하면 전부 미지원으로 세어 groundedness 가 무너진다. 세지 않되 `claims.background` 로 개수는 남긴다 |
| 융합 뒤·리랭크 뒤 LLM 을 토글 2개로 | 융합 직후 검토(무관한 후보 제거)와 리랭크 직후 선택(컨텍스트에 넣을 것·통째로 읽을 문서)은 입력·출력·비용이 다르다. 실패하면 순위 그대로(품질을 깎지 않는 방향) |

## 2. 설정 키

| 파일 | 키 | 기본 | 뜻 |
|---|---|---|---|
| config.json | `answer_mode` | `"grounded"` | `grounded` \| `best_effort`. 모르는 값은 grounded(`normalize_answer_mode`) |
| config.json | `output_mode` | `"answer"` | `answer` \| `fused` \| `reranked` \| `context`. 모르는 값은 answer(`normalize_output_mode`) |
| config.json toggles | `llm_after_fusion` | `false` | 융합·부스트 직후 LLM(역할 `fusion`, `prompts/fusion_review.md`) 검토 (§4) |
| config.json toggles | `llm_after_rerank` | `false` | 리랭크 직후 LLM(역할 `select`, `prompts/rerank_review.md`) 선택 (§4) |
| config.json toggles | `degrade_on_llm_failure` | `true` | 답변 LLM 최종 실패 시 추출식으로 계속(true) / `result_type=error` 로 끝냄(false) (§5) |
| tuning.json (`answer`) | `refs_preview_chars` | `200` | `refs[].preview` 글자 수. 0 = 미리보기 없음 |
| tuning.json (`answer`) | `output_candidates_n` | `0` | `candidates[]` 개수. 0 = 리랭크 후보 창(`rerank_candidates × k_mult`, 없으면 `max(top_k_final×2, 10)`) |
| tuning.json (`answer`) | `output_list_n` | `20` | `lists{채널: [[chunk_id, score]]}` 채널별 개수 |
| tuning.json (`answer`) | `output_chunk_chars` | `0` | `candidates[].text` 자르기. 0 = 전문 |
| tuning.json (`rrf_fuse`) | `fusion_llm_candidates` | `0` | 융합 뒤 LLM 검토에 보낼 상위 후보 수. 0 = `rerank_candidates` |
| tuning.json (`rrf_fuse`) | `fusion_llm_drop_penalty` | `0.3` | drop 후보의 fused 배율. 0 = 제거(`why=llm_drop`) |
| tuning.json (`rerank`) | `post_rerank_llm_k` | `0` | 리랭크 뒤 LLM 선택에 보낼 상위 후보 수. 0 = `top_k_final × 2` |

모두 요청 단위 오버라이드 가능(`OVERRIDE_SAFE_KEYS` 에 `answer_mode`·`output_mode`·토글 전부·`tuning`). 파일 반영: `config set answer_mode=best_effort` / `tuning set refs_preview_chars=400` / Web Settings.
저장소 `config.json`·`setup/config.example.json` 에는 `output_mode` 만 명시돼 있고 `answer_mode` 와 토글 3개는 없다(→ `python -m llmwiki config fill-defaults` 로 채운다). `tuning.json` 은 기본값과 같은 값을 쓰지 않는 희소 파일이다(`config fill-defaults --tuning` 으로 전부 명시 가능).

## 3. `output_mode` — 어디서 멈추고 무엇을 돌려주나 (동작함)

| 값 | 멈추는 곳 (`RoundConfig.stop_after`) | trace 에 `skipped` 로 남는 단계 | 응답 필드 | `result_type` |
|---|---|---|---|---|
| `answer` | 끝까지 (`None`) | — | 지금과 같음 | `grounded` \| `best_effort` \| `extractive` \| `insufficient` \| `error` |
| `fused` | 융합·부스트 뒤 (`"boost"`) | external_inject · channel_inject · rerank · doc_expand · context · evidence_check · fallback · answer_llm · claim_check · evolve_capture · cache_hit | `candidates[]` · `lists{}` · `stages{fused_order, boost_order, rerank_before, final_order, inject}` · `answer` = 후보 표(마크다운) · `refs=[]` | `candidates_fused` |
| `reranked` | 리랭크 뒤 (`"rerank"`) | doc_expand · context · (위와 같은 뒤 단계) | 위 + `candidates[].rerank`, `stages.rerank_before`(리랭크 입력 순서) / `final_order`(출력 순서), `stages.inject`(channel_inject 로 올린 id) | `candidates_reranked` |
| `context` | 컨텍스트 뒤 (`"context"`) | answer_llm · claim_check · evolve_capture · cache_hit | `context{text, chars, citations}` · `refs[]` · `evidence`(근거 판정·fallback 은 정상 수행) · `answer` = 컨텍스트 본문 | `context` |

- `candidates[]` 항목: `Hit.to_dict()`(`chunk_id, scores{채널:점수}, ranks{채널:순위}, fused, why[], rerank, boosts{}`) + `rank, doc_id, ext_id, heading, doc_type, date, text, chars` (+ 외부 청크면 `external`). `reranked` 에서는 리랭커가 `top_k_final` 로 자르지 않고 `max(top_k_final, output_candidates_n)` 까지 본다.
- `fused` 는 리랭크 전 순위를 보려는 것이므로 `external_rag_inject`·`channel_inject` 도 적용하지 않는다(`stages.inject={}`).
- `refs[]`(모든 모드): 컨텍스트 인용 순서로 `{n, chunk_id, doc_id, ext_id, heading, kind(hit|neighbor|doc_expand), chars, preview}`. `fused`/`reranked` 는 컨텍스트가 없어 빈 목록.
- 캐시·사전계산·자가진화 기록·forensic_auto(`context` 는 예외로 포렌식 수행)를 하지 않는다. `rerun_capture` 는 그대로라 fused 결과에서 ⟲ 로 리랭크부터 이어 볼 수 있다.
- CLI 텍스트 출력은 표 아래 `output_mode: reranked result_type: candidates_reranked` 한 줄; `--json` 은 `result.candidates/lists/stages` 또는 `result.context/refs`. Web 은 후보 표에 `주입(channel_inject): …` 주석과 복사 버튼, 컨텍스트 모드는 `⧉ 컨텍스트 복사` + REF 건수. MCP 는 text = 같은 표/본문, `structuredContent` = `{query, output_mode, result_type, candidates|context, lists|refs, stages|evidence, request_id, run_id, ms}`; `output_mode` 가 enum 밖이면 `isError`.

## 4. `answer_mode=best_effort` 와 두 LLM 토글

**best_effort** (`answer.generate_answer(mode="best_effort", verdict, reasons)`): 시스템 프롬프트가 `prompts/answer_best_effort.md`(+길이 힌트)로 바뀌고, 근거 판정이 `sufficient` 가 아니면 프롬프트 첫머리에 "근거가 %s 로 판정되었습니다 — 문서에 없는 부분은 배경 지식으로 보완하되 [BK] 를 붙이라" 를 넣는다. 결과 `result_type=best_effort`, trace `answer_llm.meta.background_marks`(=[BK] 개수). `claim_check`(`answer.check_claims`)는 `[BK]` 가 있고 `[C#]` 가 없는 문장을 `verdict=background` 로 두어 미지원으로 세지 않고 `[미확인]` 표기/제거 대상에서도 뺀다(`claims.background` 수). `insufficient_text`(근거 부족 응답)는 "best_effort 로 다시 질의하라" 안내 줄을 넣는다.
`grounded` 는 근거가 `insufficient` 면 답변 LLM 을 아예 부르지 않는다(`answer_insufficient` 단계 + `answer_llm` skipped). `best_effort` 는 그 분기를 타지 않고 LLM 을 부른다.

**llm_after_fusion**(역할 `fusion`, trace `fusion_llm`, 재시작점 `rerank`): 융합·부스트 상위 `fusion_llm_candidates` 후보의 제목·발췌를 **1번부터** 번호를 붙여 보내 `{"keep":[…],"drop":[…],"reason":…}` 을 받는다. drop 은 fused × `fusion_llm_drop_penalty`(0 이면 목록에서 제거), 표시는 `why=llm_drop` · `boosts.llm_drop`. 그 뒤 fused 순서로 다시 정렬하므로 **주입·리랭크 창·`output_mode=fused` 후보 목록에 모두 반영**된다. 안전장치 둘: LLM 이 *모든* 후보를 drop 하라고 하면 무시하고(근거가 통째로 사라지면 답을 만들 수 없다), 실패·JSON 오류면 순위를 그대로 둔다.

**llm_after_rerank**(역할 `select`, trace `rerank_review_llm`, 재시작점 `rerank`): 리랭크 상위 `post_rerank_llm_k` 후보(문서 id 포함, 1번부터)에서 `{"select":[번호…],"expand_docs":[문서id…],"note":…}` — select 순서가 그대로 앞쪽 순서가 되고(`why=llm_select`), 고르지 않은 후보는 뒤로 밀린다. `expand_docs` 로 지목된 문서는 `doc_expand` 가 **우선·통째로**(그 문서에 한해 `doc_expand_mode` 를 무시하고 `full` 처럼) 읽어 컨텍스트에 넣는다 — 결과의 `doc_expand.llm_expand_docs` 로 확인한다. 빈 select 면 리랭크 순서 그대로.

`output_mode=reranked` 는 "리랭크 순위를 그대로 보여 준다" 가 목적이므로 이 단계를 건너뛴다(trace 에 이유가 남는다). 두 프롬프트는 `prompts.DEFAULTS` 에 있고 파일(`prompts/fusion_review.md`·`rerank_review.md`)은 첫 `prompts.get()` 때 생성되므로 문구를 고쳐 쓸 수 있다.

## 5. 재시도 · `llm_fallbacks` · `fallback_loop` · `degrade_on_llm_failure` — 서로 다른 네 장치

"`llm_retries=0` 이면 fallback 이 동작하지 않는가" → 아니다. 재시도만 없어지고 나머지 셋은 그대로다.

| 장치 | 무엇 | 끄는 법 | 상태 |
|---|---|---|---|
| 재시도 | 같은 LLM 호출을 timeout/네트워크/빈 출력 때 다시 시도 | `llm_retries=0` 또는 `llm_roles.<role>.retries=0` (headless 는 `agents.json retries`) | 동작 |
| `llm_fallbacks` | Anthropic 이 서버 측에서 거부했을 때 대체 모델로 1회 | `config.json llm_fallbacks=false` | 동작 |
| `fallback_loop` 토글 | 근거가 부족하면 **검색을 다시** 하는 L1~L4 확장 루프 (`fallback_levels`, `fallback_max_attempts`) | `toggles.fallback_loop=false` (기본 false) | 동작 |
| `degrade_on_llm_failure` 토글 | 답변 LLM 이 끝내 실패하면 추출식 답변으로 계속(true). false 면 `result_type=error`, 답변 본문은 오류 한 줄, `llm_report` 로만 보고. 리랭크의 로컬 폴백·규칙 그래프 폴백은 값싸므로 이 토글과 무관 | `toggles.degrade_on_llm_failure=false` | 동작 |

## 6. 검증 명령

```powershell
python -m unittest tests.test_output_mode -v
#   normalize · fused · reranked_and_candidate_options · context · not_cached_and_answer_unchanged · settings_default_and_invalid
#   · cli_output_flag(--output + --tuning) · mcp_output_mode(enum·overrides.tuning 검증) · web_override_whitelist   (9건)
python -m unittest tests.test_answer_modes -v
#   grounded 기본 · best_effort 가 insufficient 에서도 답함 · 모르는 값 폴백 · degrade true/false
#   · 두 토글의 기본 off · fusion_llm 감점/제거 · rerank_review 재정렬+expand_docs · reranked 에서 건너뜀 · 레지스트리 등록  (11건)
python -m llmwiki query "PDCCH 디코딩 실패 담당" --output fused --json --no-log | Select-String '"result_type"'
python -m llmwiki query "PDCCH 디코딩 실패 담당" --output context --no-log          # [C#] 블록 본문
python -m llmwiki query "PDCCH 디코딩 실패 담당" --answer-mode best_effort --json --no-log | Select-String '"result_type"'
# 두 LLM 단계를 이번 질의에만 켜고 trace 에서 확인 (역할 fusion·select 의 모델은 config.json llm_roles)
python -m llmwiki query "PDCCH 디코딩 실패 담당" --llm-after-fusion --llm-after-rerank --trace --no-log
python -m llmwiki config show --effective | Select-String "answer_mode|output_mode|llm_after_fusion|llm_after_rerank|degrade_on_llm_failure"
python -m llmwiki tuning show --stage answer | Select-String "refs_preview_chars|output_"
```

## 7. 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| `--answer-mode best_effort` 를 줘도 `result_type=grounded` | 근거가 **충분**했다는 뜻이다(정상). best_effort 는 근거가 부족할 때만 배경 지식을 섞는다 — 충분하면 grounded 와 같은 답을 내고 `[BK]` 도 없다 |
| `best_effort` 인데 `result_type=extractive` | 답변 LLM 이 실패해 대체 경로를 탔다. 답변 상단 `⚠ LLM 실행 보고` 와 `llm_report` 를 본다 |
| 토글 `llm_after_fusion` 을 켰는데 trace `fusion_llm` 이 `skipped` | 역할 `fusion` 의 프로바이더가 없다(`models test` 로 확인) 또는 후보가 0건. `meta.reason` 에 이유가 있다 |
| trace `fusion_llm.meta.error` 에 circuit open | 그 모델이 연속 실패해 회로가 열렸다(`server circuits`). 이 단계는 실패해도 순위를 그대로 두므로 답변에는 영향이 없다 |
| `llm_after_rerank` 를 켰는데 순서가 그대로 | LLM 이 빈 `select` 를 줬다(정상 폴백). `meta.selected` 로 확인하고, 프롬프트(`prompts/rerank_review.md`)나 `post_rerank_llm_k` 를 조절한다 |
| `output_mode=reranked` 에서 `rerank_review_llm` 이 안 돈다 | 의도된 동작 — 그 모드는 리랭크 순위 자체를 보여 준다. 선택 결과를 보려면 `answer` 나 `context` 로 |
| `output_mode=fused` 결과가 매번 같다/캐시가 안 된다 | 정상 — 중간 산출물은 캐시를 쓰지 않는다 |
| 후보 표에 `rerank` 열이 `-` | `fused` 모드는 리랭크 전. `reranked` 로 |
| `candidates` 가 너무 적다/많다 | `output_candidates_n`(0 = 리랭크 후보 창). `reranked` 는 `top_k_final` 이 아니라 이 값까지 |
| `refs` 가 비어 있다 | `fused`/`reranked` 는 컨텍스트가 없다. `context` 또는 `answer` 로 |
| MCP `isError: invalid overrides: …` | `overrides.tuning` 의 모르는 키/범위 밖 값. `tuning show` 로 키·범위 확인 |
| Web 출력 드롭다운을 바꿨는데 답변이 그대로 | 드롭다운 값이 `overrides.output_mode` 로 실리는지 사이드바 CLI 미리보기(`--output …`)로 확인. 빈 값 = config.json 값 |

## 8. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/answer.py` | `ANSWER_MODES`/`normalize_answer_mode` · `OUTPUT_MODES`/`OUTPUT_STOP_AFTER`/`RESULT_*`/`normalize_output_mode` · `render_candidates_table()` · `ANSWER_SYSTEM_BEST_EFFORT()` · `_BK_RE`/`check_claims`(background) · `result_type_of()` · `generate_answer(mode, verdict, reasons, degrade)` |
| `llmwiki/query_engine.py` | `RoundConfig.stop_after` · `run()`(캐시 차단, 모드별 skipped, `answer` 칸 구성, `result_type/output_mode/refs/stages/candidates/lists/context`, `generate_answer(mode/verdict/reasons/degrade)` 전달, grounded 에서만 insufficient 분기) · `_retrieve()`(boost/rerank 지점의 조기 반환, 두 LLM 단계 호출) · `_fusion_llm()` · `_rerank_review_llm()` · `_cand_lines()` · `_doc_expand(prefer_docs=)` · `_candidates()` · `_refs()` |
| `llmwiki/architecture.py` | 단계 `fusion_llm` · `rerank_review_llm` (Pipeline 화면·`arch doc`·분석 리포트가 이 표를 읽는다) |
| `llmwiki/providers.py` | `MockLLM` 이 `TASK=fusion_review`(마지막 후보 drop) · `TASK=rerank_review`(역순 선택 + expand_docs) 에 결정적으로 답한다 — 배선 테스트용 |
| `llmwiki/config.py` | `answer_mode` · `output_mode` · 토글 3개 + `TOGGLE_HELP/GROUPS/EFFECT` · `LLM_ROLES` 의 `fusion`/`select` |
| `llmwiki/tuning.py` | `refs_preview_chars` · `output_*` · `fusion_llm_*` · `post_rerank_llm_k` |
| `llmwiki/prompts.py` | `answer_best_effort` · `fusion_review` · `rerank_review` 기본 문구 |
| `llmwiki/cli.py` | `query --output --answer-mode --tuning` · 텍스트 출력의 `output_mode: … result_type: …` 줄 |
| `llmwiki/web/server.py` | `OVERRIDE_SAFE_KEYS`(`answer_mode`, `output_mode`, `tuning`) |
| `llmwiki/web/static/index.html` · `js/core.js` · `js/ask.js` | `#ov-answer-mode` · `#ov-output` → overrides · 후보 표/컨텍스트/REF 렌더와 복사 |
| `tests/test_output_mode.py` · `tests/test_answer_modes.py` | 출력 모드 9건 · 답변 모드/실패 처리/두 LLM 단계 11건 |
| `llmwiki/mcp.py` | `wiki_query` 의 `output_mode` enum · `overrides` · `structuredContent` 분기 |
| `llmwiki/evidence.py` | `insufficient_text` 의 best_effort 안내 줄 |
| `tests/test_output_mode.py` | 9건 |
