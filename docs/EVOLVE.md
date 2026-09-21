# EVOLVE — 자가진화: 제안을 모으고, 사람이 승인하고, 나빠지면 되돌린다

> 구현: `llmwiki/evolve.py` (제안·적용·롤백) · `llmwiki/memory.py` (에피소드·decay·consolidate) · `llmwiki/scheduler.py` (예약 실행)
> 화면: Evolve › **제안 (HITL)** · **메모리** · Settings › **스케줄**

## 0. 한 장 요약

```
질의 실행 ─┬─ 점수 낮음 · 시드 없음 · 인용 없음 ──► capture      ─┐
           ├─ 👍/👎 + 정정 메모 ─────────────────► feedback     ─┤
           └─ (옵션) LLM 이 질의 로그 검토 ────────► llm_review  ─┤
                                                                  ▼
                                                            proposals 큐 (status=proposed)
                                                                  │
                          ┌───────────────────────────────────────┴──────────────────────┐
                          ▼ 사람이 승인 (기본)                                            ▼ 자동 (토글 · 스케줄)
                  Web 승인·적용 / CLI evolve apply                              evolve_auto_apply
                          │                                                     또는 evolve auto-apply
                          ▼
            ① 스냅샷 ② 적용 ③ (필요하면) 리빌드 ④ 회귀 평가 ⑤ 나빠졌으면 롤백
                          │
                          ▼
                  evolution_log (체크섬과 함께 영구 기록)
```

**원칙 하나**: 제안은 **데이터만** 바꾼다. 코드와 프롬프트는 건드리지 않는다. 모든 적용은 되돌릴 수 있고 이력이 남는다.

## 1. 제안 종류 (`evolve.KINDS`)

목록은 **한 곳**에만 있다 — `llmwiki/evolve.py` 의 `KINDS`. Web 드롭다운도, MCP `wiki_propose` 검증도, `apply_proposal` 도 같은 목록을 본다.
(2026-09-19 이전에는 세 곳이 각자 목록을 들고 있어서, 화면에서 만들 수 있는데 적용하면 `unknown kind` 로 실패하는 종류가 있었다.)

| kind | 무엇을 바꾸나 | 리빌드 | 자동 적용 기본 |
|---|---|---|---|
| `synonym` | FTS 질의 확장 (synonyms 테이블) | 없음 | ✔ |
| `query_rule` | 질의 규칙 사전(`query_rules.json`) 항목 | 없음 | ✔ |
| `pin` | 고정 근거(`pins.json`) | 없음 | ✔ |
| `wiki_note` | 위키 페이지 편집 노트 → 다음 빌드에 overlay 색인 | 다음 빌드 | ✔ |
| `alias` | 규칙 사전에 별칭 | **그래프 재빌드** | ✘ |
| `entity` | 규칙 사전에 새 엔티티 | **그래프 재빌드** | ✘ |
| `relation` | 그래프에 관계 직접 추가 | 없음 | ✘ |
| `tuning` | 튜닝 값 변경 (`tuning.json`) | 없음 | ✘ |
| `chunk_params` | 청킹 파라미터 | **전체 리빌드** | ✘ |
| `corpus_gap` | 문서가 없다는 기록 | — | 자동 적용 불가 (사람이 문서를 추가) |

`python -m llmwiki evolve kinds` 가 이 표를 그대로 출력한다.

## 1.5 제안 설명 — 승인할지 말지를 화면에서 판단한다 (2026-09-19)

### 왜 만들었나

예전에는 세 창구 모두 제안을 payload JSON 원문으로만 보여 줬다. 실제로 화면에 이렇게 떴다.

```
#124 entity conf=0.80 strength=1.00 [llm_review] 2026-09-19 21:29:37
{"aliases":["HWD-PHY-TIMING-B1 · PHY 타이밍 및 레지스터 사양 rev B1"],"name":"HWD-PHY-TIMING-B1 ","type":"relation"}
```

이것만 보고 승인 여부를 정하려면 (1) 어느 파일이 바뀌는지 (2) 바꾸면 무엇이 좋아지는지 (3) 얼마나 비싼지(리빌드가 붙나)
(4) 값이 애초에 성한지 를 전부 사람이 코드를 읽어 알아내야 했다. 위 제안은 네 가지가 모두 문제였다 —
`type="relation"` 은 존재하지 않는 엔티티 type 이고, 이름 끝에 공백이 있고, 별칭은 별칭이 아니라 문서 헤딩 한 줄 통째다.
**승인해도 조용히 아무 효과가 없는 제안**이었는데, 화면에는 그 사실이 전혀 드러나지 않았다.

운영 DB(대기 124건)를 실제로 훑어 보니 이런 제안이 예외가 아니었다.

| 문제 | 건수 | 승인하면 |
|---|---|---|
| `alias` 인데 붙일 대상(`entity`)이 payload 에 없음 | 24 | `KeyError` → `failed` (회귀평가 수십 초를 쓴 뒤에) |
| 없는 엔티티 `type` (예: `relation`, 대문자 `CL`) | 11 | 등록은 되지만 관계가 하나도 안 생김 (무효) |
| 별칭이 이름과 같음 | 6 | 새로 찾아지는 질의가 없음 (무효) |
| 이름 앞뒤 공백 · 별칭이 헤딩 한 줄 | 2 | 어떤 질의와도 매칭되지 않음 (무효) |

### 무엇을 보여 주나

`llmwiki/proposal_explain.py` 가 제안 하나를 받아 **LLM 없이** (규칙 사전·튜닝 스펙·DB 만 보고) 다음을 계산한다.

| 항목 | 내용 |
|---|---|
| `title` | 한 줄 요약 — 예: `별칭 추가 — 엔티티 «RX DMA» 를 'rx-dma' 로도 찾게 합니다` |
| `what` | 무엇이 어떻게 바뀌고 왜 좋아지는지 (평문) |
| `target` | 바뀌는 파일/테이블 — `data/rules.json › entities[대상].aliases` 처럼 경로까지 |
| `diff` | 적용 전/후 미리보기 (`-` 현재 값 / `+` 바뀔 값) |
| `impact` | `rebuild`(none/incremental/full) · `scope`(어느 단계가 영향받나) · `risk` · `revert`(되돌리는 법) · `auto_apply` |
| `checks` | 값 검증 — `error`(적용해도 실패하거나 무효) / `warn`(확인 필요) / `info` |
| `applicable` | `error` 가 하나도 없고 적용 가능한 종류인가 |
| `origin_text` | 제안이 어디서 왔나 — 근거의 세기를 가늠하는 단서 (`llm_review` = 검증 안 된 추측, `forensics` = 반복 관측) |

검증 규칙은 종류마다 다르다. 공통으로 보는 것: 필수 키 누락(종류별 `REQUIRED` — `apply_proposal` 이 실제로 꺼내 쓰는 키와 같다),
이름 앞뒤 공백, 너무 짧은 이름, 이미 있는 값(무효), 엔티티 `type` 유효성. `type` 목록은 `graph_rules.known_types()` 가
규칙 사전에서 **계산한다** — `types_for_cooccur` + `id_patterns[*].type` + `related_key_type` 값 + `link_rules[*].target_type`
+ 이미 쓰이고 있는 type 의 합집합이다. 이 목록 밖의 type 을 가진 엔티티는 등록되어도 관계가 하나도 생기지 않으므로 `error` 로 본다.

### 적용 전 차단 · 자동 정리

- `apply_proposal` 이 **가장 먼저** 이 설명을 계산하고, `error` 급 점검이 하나라도 있으면 스냅샷·회귀평가를 시작하지 않고
  바로 `failed` 로 기록한다. 이유를 `error` 문자열과 `checks` 로 돌려준다. (예전에는 수십 초짜리 회귀평가를 돌린 뒤에야
  `KeyError` 로 실패했다.)
- 이름류 값의 앞뒤 공백은 적용할 때 `_clean_payload()` 가 지운다 (`name` `entity` `alias` `term` `expansion` `src` `dst`
  `rel` `page` `topic` `key` `type` 와 `aliases`·`values` 의 각 원소). LLM 이 헤딩에서 이름을 떠 올 때 꼬리 공백이 붙는데,
  그대로 등록하면 공백까지가 이름이 되어 어떤 질의와도 맞지 않는다.

### 생산자 쪽에서 고친 것

설명만 붙이면 "나쁜 제안을 예쁘게 보여 주는" 데서 끝난다. 나쁜 제안이 **만들어지는** 자리도 함께 고쳤다.

- `forensic.py` — 그래프에 시드 엔티티가 하나도 없을 때 `alias` 종류로 `{"alias": k}` 만 올리고 있었다. `alias` 적용은
  붙일 대상(`entity`)을 반드시 쓰므로 이 제안은 전부 실패했다. 애초에 "맞는 엔티티가 하나도 없다" 는 상황이라 붙일 대상이
  없으므로, 올바른 종류는 **새 엔티티 등록**(`entity`)이다. `trace_expectation` 의 제안 승격 목록에도 `entity` 를 넣었다.
- `memory.py` `consolidate()` — `alias` 제안을 별칭 문자열만으로 묶고 있었다. 별칭은 *어느 엔티티에* 붙이는지가 정체성이므로
  `entity|alias` 로 묶고, 대상이 없는 payload 는 아예 승격하지 않는다. `entity` 종류도 묶음 대상에 넣었다.

### 세 창구

| 창구 | 어디에 |
|---|---|
| CLI | `evolve show <번호>` (설명 전문) · `evolve status` 는 payload 대신 한 줄 요약 + `[X]`/`[!]` 표시 |
| Web | Evolve › 제안 — 카드마다 제목·설명·바뀌는 곳·diff·영향·점검이 펼쳐져 있고, 근거 질의를 누르면 Ask 탭에서 재현된다. 원문 JSON 은 '제안 근거 · 원문' 접기 안 |
| MCP | `wiki_evolve` — 목록에 `explain` 이 붙는다 (`explain=false` 로 끌 수 있다). `wiki_evolve(id=<번호>)` 는 설명 전문 |

셋 다 `llmwiki/proposal_explain.py` 한 곳을 쓴다. 검증: `tools/verify/verify_surface_align.py` 의 "자가진화 제안 설명" 행,
테스트 `tests/test_proposal_explain.py` (29건).

## 2. 자동 적용의 안전장치 (2026-09-19)

자동 적용은 **사람이 안 보는 사이에 도는 경로**다. 그래서 세 겹으로 막는다.

| 장치 | 설정 | 기본 |
|---|---|---|
| 신뢰도 하한 | `config.json` `evolve_min_confidence` | 0.8 |
| **종류 제한** | `config.json` `evolve_auto_apply_kinds` (비우면 되돌리기 쉬운 것만) | `synonym · query_rule · pin · wiki_note` |
| 건수 상한 | 스케줄 태스크의 `max_apply` · CLI `--max-apply` | 5 |
| 회귀 평가 | 적용 전후 `hit@k`·`term_recall` 비교, 나빠지면 스냅샷 복원 | **켬** |

> **무엇이 바뀌었나**: 예전에는 토글(`evolve_auto_apply`) 경로에 종류 제한이 **없어서**, 신뢰도만 높으면
> `chunk_params` 제안 하나가 사람 없이 **전체 리빌드**를 돌릴 수 있었다. 스케줄러 경로는 원래 종류를 제한하고 있었는데
> 토글 경로만 빠져 있었다. 또 스케줄러의 `evaluate` 기본값이 `false` 라 회귀 평가 없이 적용됐다 — 나빠져도 롤백이 돌지 않았다.
> 지금은 두 경로가 같은 목록(`evolve.auto_apply_kinds`)을 쓰고, 평가는 기본으로 돈다.

**스냅샷 보관**: 적용마다 DB + 위키 트리 전체 복사본이 `data/snapshots/p<제안번호>_<시각>` 에 생긴다.
자동 적용을 켜 두면 계속 쌓이므로 `evolve_snapshot_keep`(기본 20) 개만 남기고 오래된 것부터 지운다. 0 이면 무제한(예전 동작).

## 2.5 메모리 — 시스템이 **스스로 배운 것** (2026-09-19 개편)

제안 화면(§2)이 "무엇을 바꿀까" 라면, 메모리 화면은 **"지금 무엇을 배워 두고 있나"** 다.
문서에 적힌 사실이 아니라 사람이 누른 👍/👎 와 반복된 실패에서 나온 것이라, 시간이 지나면 흐려지고
(감쇠) 다시 쓰이면 강해진다. 코퍼스의 사실은 **감쇠시키지 않는다** — 감쇠 대상은 학습한 것뿐이다.

### 무엇이 들어 있나

| 것 | 어디서 오나 | 어디에 쓰이나 |
|---|---|---|
| **에피소드** | 질의 1건 (토글 `evolve_capture`) + 👍/👎 | 피드백 부스트의 원천 |
| **피드백 부스트** | 👍/👎 를 받은 답변의 **근거 청크** | 융합 뒤 post-boost — 다음 질의의 순위를 바꾼다 |
| **제안 strength** | 제안이 올라온 뒤 시간·재사용 | 임계 아래로 내려가면 보관(archived) |
| **포렌식 소견** | "왜 못 찾았나" 기록 | `consolidate` 로 규칙 제안으로 승격 |

### 화면이 답하는 질문

예전에는 통계 숫자 한 줄과 에피소드 표 하나뿐이라 **무엇을 하는 화면인지 알 수 없었고, 눌러도 아무 데도
가지 않았다**(사용자 보고). 사람이 여기 와서 묻는 것은 숫자가 아니라 목록이다.

| 질문 | 어디를 보나 |
|---|---|
| 내가 누른 👎 가 검색에 반영됐나? | **피드백 부스트** 표 — 청크별 가중치와 **그렇게 만든 질문**이 함께 |
| 왜 이 문서가 자꾸 위로 오지? | 같은 표에서 그 문서를 찾는다 (양수 = 위로, 음수 = 아래로) |
| 올렸던 제안이 왜 사라졌지? | **감쇠 중인 제안** — 사라지기 **전에** 남은 날을 보여 준다 |
| 같은 실패가 반복되는데 규칙이 됐나? | `consolidate` 버튼 — 몇 묶음에서 몇 건이 나왔는지 문장으로 |

행은 전부 **눌러서 건너뛴다**: 부스트 행 → 그 문서, 에피소드의 청크 → 그 문서,
`↩` → 같은 질문을 Ask 에 채워 넣기, `📄` → 그때의 요청 프로파일(단계별 실측).
에피소드는 `전부 / 피드백 있는 것 / 👎 만 / 👍 만` 으로 거르고 질문 본문으로 찾을 수 있다.

> **화면의 부스트 = 검색이 쓰는 부스트.** 둘을 따로 계산하면 화면이 거짓말을 하게 되므로
> `boost_table` 과 `feedback_weights` 는 같은 함수(`_feedback_contributions`)를 쓴다.
> `tests/test_memory_view.py` 가 두 값이 같은지 매번 확인한다.

### 읽는 법

- 가중치는 **−1 ~ +1** 로 잘린다. 한 에피소드 안에서도 상위 근거가 더 강하다(순위마다 15%씩 감쇠).
- 반감기(`memory_half_life_days`, 기본 60일)가 지나면 영향이 절반이 된다.
- **“색인에 없음”** 은 재빌드로 그 청크가 사라졌다는 뜻이다. 무해하며 가중치는 아무 데도 걸리지 않는다.
- `감쇠 지금 실행` 은 되돌릴 수 없지만 **삭제가 아니다** — 보관된 제안은 Evolve 에서 `status=archived` 로 계속 보인다.

## 3. 세 창구

| 하는 일 | CLI | Web | MCP |
|---|---|---|---|
| 상태·대기 목록 | `evolve status [--json]` · `evolve list [상태]` | Evolve › 제안 (상태 선택) | `wiki_evolve` (읽기 전용) |
| 종류 목록·설명 | `evolve kinds` | 수동 제안 추가의 드롭다운 툴팁 | `wiki_evolve` 응답의 `kinds` |
| 수동 제안 등록 | `evolve propose <kind> '<payload JSON>' [--reason] [--confidence]` | 수동 제안 추가 폼 | `wiki_propose` |
| 승인·적용 | `evolve apply <id> [--no-eval]` | 제안 줄의 `승인·적용` | — (적용은 사람이) |
| 거절 (+사유) | `evolve reject <id> 사유` | 제안 줄의 `거절` → 사유 입력 | — |
| 자동 적용 1회 | `evolve auto-apply [--dry-run] [--max-apply N] [--kinds a,b]` | `자동 적용 실행` (미리보기 체크) | — |
| LLM 리뷰 | `evolve review` | `LLM 리뷰 실행` | — |
| 피드백 | `evolve feedback <qid> ±1 [메모]` | 답변 아래 👍/👎 + 메모 | `wiki_feedback` |
| 메모리 상태 | `memory status` | Evolve › 메모리 의 요약 줄 | — |
| 피드백 부스트 (§2.5) | `memory boosts [--limit N]` | 메모리 › **피드백 부스트** 표 (행 클릭 → 문서) | — |
| 감쇠 중인 제안 | `memory decaying [--limit N]` | 메모리 › **감쇠 중인 제안** 표 | — |
| 에피소드 찾기 | `memory episodes [--only feedback\|negative\|positive] [--q 검색]` | 메모리 › 필터 + 검색칸 | — |
| 감쇠·승격 실행 | `memory decay` · `memory consolidate` | `감쇠 지금 실행` · `반복 실패 → 제안 만들기` | — |

MCP 에 적용·거절을 두지 않는 것은 의도다 — 붙어 있는 LLM 이 자기 제안을 스스로 승인하면 HITL 이 아니다.
대신 `wiki_evolve` 로 **자기가 올린 제안이 어떻게 됐는지** 볼 수 있다.

## 4. 예약 실행 (스케줄러)

`schedule.json` 의 태스크로 돌린다. 관련 액션은 넷이다.

| action | 무엇 |
|---|---|
| `{"type":"evolve","op":"review"}` | LLM 이 최근 질의 로그를 검토해 제안 생성 |
| `{"type":"evolve","op":"auto_apply","min_confidence":0.9,"max_apply":5,"kinds":[…],"evaluate":true}` | 위 §2 규칙으로 자동 적용 |
| `{"type":"evolve","op":"consolidate"}` | 포렌식 누적 → 제안 (memory.consolidate 와 같은 동작) |
| `{"type":"memory","op":"decay"}` | 오래된 에피소드의 strength 감쇠 |

`kinds` 를 적지 않으면 `evolve_auto_apply_kinds` 를 따른다. `evaluate` 를 적지 않으면 **평가를 돈다**.

**목록에서 보이는 것** (Settings › 스케줄): 다음 실행 · 마지막 실행과 결과 · 실패 메시지 · **건너뜀 시각**.
마지막 것은 `overlap:"skip"` 으로 앞 실행이 안 끝나 건너뛴 경우인데, 예전에는 `logs/schedule.jsonl` 을 열어야만 보여서
"왜 내 태스크가 안 돌지" 를 알 수 없었다.

**편집 시 주의**: 화면 편집기는 이제 **모르는 필드를 지우지 않는다**. 예전에는 폼 입력만으로 작업을 새로 만들어서,
한 번 편집하면 `run_on_start` 와 각 샘플 작업의 `_note` 설명이 사라졌다. 이름을 바꿀 때도 **새 이름으로 먼저 넣고**
성공했을 때만 옛 이름을 지운다 (예전에는 지우고 나서 넣다가 검증에 막히면 작업이 사라졌다).

## 5. 설정 키

| 파일 | 키 | 기본 | 뜻 |
|---|---|---|---|
| `config.json` | `toggles.evolve_capture` | true | 질의에서 갭을 찾아 제안을 만든다 |
| `config.json` | `toggles.evolve_auto_apply` | false | 고신뢰 제안을 사람 승인 없이 적용 |
| `config.json` | `evolve_min_confidence` | 0.8 | 자동 적용 신뢰도 하한 |
| `config.json` | `evolve_auto_apply_kinds` | `[]` (= synonym·query_rule·pin·wiki_note) | 자동 적용을 허용할 종류 |
| `config.json` | `evolve_snapshot_keep` | 20 | 적용마다 만드는 스냅샷 보관 개수 (0 = 무제한) |
| `config.json` | `evolve_low_score_threshold` | 0.05 | 이 점수 아래를 '개선 거리' 로 본다 |
| `tuning.json` | `memory_half_life_days` · `memory_archive_strength` | — | 에피소드 감쇠 |

## 6. 검증

```bat
python -m unittest tests.test_phase3_5 tests.test_phase6
python -m unittest tests.test_proposal_explain  :: 제안 설명·점검·적용 전 차단 (§1.5)
python tools/verify/verify_cli.py            :: evolve/memory 명령
python tools/verify/verify_web.py            :: /api/evolve/*
python tools/verify/verify_surface_align.py  :: 세 창구 정렬
```

대기 중인 제안이 성한지 한 번에 보려면:

```bat
python -m llmwiki evolve status              :: [X] 가 붙은 줄이 '이대로는 적용 실패/무효' 인 제안
python -m llmwiki evolve show <번호>          :: 그 이유와 고치는 법
```

## 7. 문제 해결

| 증상 | 원인 | 확인 |
|---|---|---|
| 제안이 `failed` 로 남는다 | `corpus_gap` 은 문서를 추가해야 닫히는 종류다 (자동 적용 불가) | 문서를 넣고 제안을 거절 처리 |
| 승인을 눌렀는데 "적용할 수 없는 제안입니다" | 값 점검에서 `error` 가 나왔다 (§1.5) — 필수 키 누락 · 없는 엔티티 type 등 | `evolve show <번호>` 의 **점검** 절에 이유와 고치는 법이 있다 |
| 적용은 됐는데 아무 변화가 없다 | 없는 `type` 으로 등록됐거나 별칭이 이름과 같았을 수 있다 | `evolve show` 의 `[!]`/`[i]` 점검 · `rules` 로 실제 등록 값 확인 |
| 적용했는데 검색이 그대로 | `alias`·`entity` 는 그래프 재빌드가 필요하다 | `build graph` |
| 자동 적용이 안 돈다 | 종류가 `evolve_auto_apply_kinds` 에 없거나 신뢰도 미달 | `evolve auto-apply --dry-run` 으로 대상 확인 |
| `data` 폴더가 계속 커진다 | 적용마다 스냅샷이 쌓인다 | `evolve_snapshot_keep` 을 줄인다 |
| 예약 태스크가 안 돈다 | 앞 실행이 안 끝나 건너뛰었다 | Settings › 스케줄의 **건너뜀** 표시 · `schedule history` |

## 8. 구현 파일

| 파일 | 역할 |
|---|---|
| `llmwiki/evolve.py` | `KINDS` · `capture_query` · `record_feedback` · `llm_review` · `apply_proposal` · `_clean_payload` · `describe_proposal(s)` · `_snapshot`/`_restore`/`_prune_snapshots` · `status` |
| `llmwiki/proposal_explain.py` | 제안 설명 (§1.5) — `describe` · `describe_many` · `format_description` · 종류별 `KIND_IMPACT`/`REQUIRED` · 검증 |
| `llmwiki/graph_rules.py` | `known_types()` — 규칙 사전에서 뜻이 있는 엔티티 type 목록 (설명의 type 검증이 쓴다) |
| `llmwiki/memory.py` | 에피소드 · `decay` · `consolidate` |
| `llmwiki/scheduler.py` | `evolve`/`memory` 액션 · 실행 이력 · 건너뜀 기록 |
| `llmwiki/web/static/js/evolve.js` | Evolve · 메모리 화면 |
| `llmwiki/web/server.py` | `/api/evolve/*` · `/api/memory` |
| `llmwiki/mcp.py` | `wiki_propose` · `wiki_feedback` · `wiki_evolve` |
