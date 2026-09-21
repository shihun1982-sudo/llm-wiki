# EVAL · TRIAL · FORENSIC — 품질 루프를 **믿을 수 있게** 돌리는 법

> CLI: `eval [--check] [--retrieval-only] [--forensic] [--matrix]` · `trial run|list|compare` · `forensic expect`
> API: `GET /api/eval/check` · `POST /api/eval {retrieval_only, forensic}` · `POST /api/trials` · `GET /api/trials/compare`
> 화면: Quality › 평가 · trial · 포렌식
> 관련: [ANALYSIS_MODE.md](ANALYSIS_MODE.md)(한 질의 심층) · [FORENSIC.md](FORENSIC.md)(포렌식 상세) · [SWEEP.md](SWEEP.md)(값 하나 쓸어보기)

## 0. 한 장 요약

| 도구 | 답하는 질문 | 비용 |
|---|---|---|
| `eval --check` | **이 점수를 믿어도 되나** (평가셋 오염·기대 문서 누락·표본 크기) | LLM 없음, 몇 초 |
| `eval --retrieval-only` | 검색이 정답 문서를 **찾아는 오나** (hit@k·MRR) | **토큰 0** |
| `eval` | 답변까지 포함한 전체 품질 (groundedness·인용 정확도) | 문항 수 × LLM 호출 |
| `eval --forensic` | 놓친 문항이 **왜** 놓쳤나 + 수정안 | 실패 문항 × 검색 재실행 |
| `trial run` + `compare` | 설정 A 와 B 중 **어느 쪽이 나은가**, 그 대가는 | trial 2회 |
| `forensic expect` | 이 문서가 나왔어야 하는데 **어느 단계에서 탈락**했나 | 검색 1회 |

**순서**: `eval --check` → (통과하면) `eval --retrieval-only` 로 빠르게 반복 튜닝 →
좋아 보이면 `trial` 로 A/B → 놓친 문항은 `eval --forensic` 으로 원인 → 고치고 다시.

---

## 1. 먼저 `eval --check` — 점수보다 신뢰도가 먼저다

평가는 "몇 점인가" 를 알려 주지만 **"그 점수가 뜻이 있나"** 는 알려 주지 않는다.

> **실제로 있었던 일.** 이 저장소의 개발 색인에서 `hit@k = 0.6` 이 나왔다. 검색이 나쁜 줄 알았는데,
> 진짜 원인은 `tools/corpus_ingest.py` 로 프로젝트 폴더를 통째로 색인하면서 **평가셋 파일 자신이
> 코퍼스에 들어간 것**이었다. 질문과 글자 그대로 일치하는 `corpus/imported/eval/NOTE-questions.md`
> 가 FTS 점수 37.3 으로 상위 6건을 독식해 정답 문서를 밀어냈다. 그 상태로 튜닝을 시작했다면
> 며칠을 버렸을 것이다.

```bat
python -m llmwiki eval --check
```

| 검사 | 뜻 | 수준 |
|---|---|---|
| `contaminated` | 질문과 거의 같은 글이 검색 상위를 차지 — **평가셋이 코퍼스에 색인됨** | bad |
| `missing_docs` | 기대 문서가 색인에 없다 — 그 문항은 무엇을 해도 실패한다(평가가 아니라 설정 오류) | bad |
| `too_small` | 문항이 적어 지표 한 칸이 크다 (25문항이면 한 칸 = 0.04) | warn/bad |

오염이 잡히면 그 문서를 `config.json` 의 `corpus_exclude` 로 빼고 다시 빌드한 뒤 평가한다.
Web 에서는 **신뢰도 점검** 버튼이 같은 것을 보여 주고, 평가를 돌릴 때도 문제가 있으면 점수 위에 함께 뜬다.

---

## 2. `--retrieval-only` — 검색 튜닝은 토큰 0으로

`hit@k`·`MRR`·`term_recall` 은 **LLM 없이 전부 계산된다**. 그런데 예전에는 평가가 늘 전체 파이프라인을
돌려서 25문항에 5분·수천 토큰이 들었고, 그래서 아무도 자주 돌리지 않았다 — 품질 루프가 거기서 끊긴다.

```bat
python -m llmwiki eval --retrieval-only          :: LLM 단계 전부 off · 토큰 0
```

끄는 것: `llm_answer` `claim_check` `claim_check_llm` `evidence_check_llm` `answer_refine`
`query_expand` `query_decompose` `router_llm` `rerank_llm` `llm_after_fusion` `llm_after_rerank`
`evidence_compress` `external_rag` (`Pipeline.EVAL_LLM_TOGGLES` 한 곳에 모여 있다).

답변 지표(`answer_term_recall`)는 이 모드에서 뜻이 없으므로 **None** 으로 둔다 — 0 으로 두면
"나빠졌다" 로 잘못 읽힌다.

> **임베더도 LLM 이다.** LLM 을 다 꺼도 원격 임베더(ollama·voyage)는 질의 하나를 벡터로 바꾸는 데
> 2초를 쓰고, 규칙 대체 질의까지 4번 임베딩한다. 그래서 **질의 임베딩 캐시**(토글 `embed_query_cache`,
> 기본 on)를 뒀다 — 같은 글은 같은 벡터라 결과는 그대로이고, 같은 질문을 반복하는 평가·trial·스윕에서
> 실측 `vector_search` **8.4초 → 0.13초**가 됐다. 비우기: `embed clear-cache`.

---

## 3. 변별력 — 만점이라고 좋은 게 아니다

같은 색인에서 `term_recall` 이 25문항 **전부 1.0** 이었다. 요약에 다른 지표와 같은 크기로 찍히니
사람은 "용어 재현 100%" 를 성과로 읽지만, 실은 **무엇을 바꿔도 안 움직이는 눈금**이다.

이제 평가 결과에 `discriminating` 이 함께 오고, 전 문항이 같은 값인 지표는 이렇게 표시된다.

```
△ 변별력 없음: term_recall — 모든 문항이 같은 값이라 이 지표로는 튜닝 효과를 볼 수 없습니다
```

그런 지표는 무시하고 `hit@k`·`MRR` 을 본다.

---

## 4. `--forensic` — 놓친 문항의 원인까지 한 번에

평가셋에는 이미 `expect_docs`·`expect_terms` 가 적혀 있다. 예전에는 그 값을 **사람이 다시 손으로 적어**
`forensic expect` 를 돌려야 했는데, 이제 평가가 그대로 넘긴다.

```bat
python -m llmwiki eval --retrieval-only --forensic --forensic-max 5
```

놓친 문항마다 어느 단계에서 탈락했는지와 수정안이 붙는다. 포렌식 본체는 구체적이다 — 예:

```
✘ ISSUE-2001 의 원인과 수정 CL 은?
   ✘ fts      질의 키워드 중 청크에 있는 것 [] · OR 검색 500위 안에도 없음
   ✘ vector   #23 전체 16898 벡터 중 순위 23, 유사도 0.498 (임계 vector_min_sim=0.0, top_k_vector=20)
   → (tuning 0.35) top_k_vector 를 23 이상으로 (벡터 순위 23)
   → (query_rule 0.35) 'tc-rx-dma-001' ↔ 'dma' 동의어 등록 검토
```

> **주의: `forensic expect last` 의 `last` 는 마지막 요청이다.** 평가를 돌린 직후라면 마지막 요청은
> 평가의 25번째 질문이지 당신이 생각한 질문이 아니다. 기대값과 질의가 어긋나면 포렌식은 **그래도
> 자신 있게 엉뚱한 제안**을 낸다. `--forensic` 이나 화면의 버튼을 쓰면 문항과 기대값이 항상 짝이 맞는다.

---

## 5. trial 비교 — "한 문항 뒤집힌 것" 을 개선이라 하지 않는다

`trial run` 으로 설정을 바꿔 가며 기록하고 `compare` 로 비교한다. 2026-09-19 에 판정 방식을 바꿨다.

**예전**: 지표 값이 제일 높은 trial 을 그냥 "최선" 이라고 했다. 문항이 25개면 `hit@k` 한 칸이 0.04 라서,
**한 문항이 뒤집힌 것**을 "더 좋다" 고 추천해 버린다 — 노이즈를 쫓게 된다.

**지금**: 질문별 승/패로 **부호 검정**(정확 이항 검정, 표준 라이브러리만)을 하고, **대가**를 함께 말한다.

```
판정  문항 25 · 한 문항 = 0.040 (이보다 작은 차이는 우연과 구분되지 않습니다)
  cand: 차이 확인 안 됨 · hit@k +0.040 · 승 1 / 패 0 / 무 24 (p≈1.00)
  cand2: 차이 있음 · hit@k +0.400 · 승 10 / 패 0 (p≈0.00) · 대가: 토큰 +220% · p95 +320%
```

| 규칙 | 왜 |
|---|---|
| 무승부는 세지 않는다 | 부호 검정의 관례 — 바뀌지 않은 문항은 어느 쪽 증거도 아니다 |
| p < 0.05 일 때만 "차이 있음" | 문항이 적으면 어지간한 차이로는 유의하지 않게 나오는데, **그게 맞다** |
| 질문셋이 다르면 비교 거부 | `questions_hash` 가 다르면 숫자를 나란히 놓는 것 자체가 틀렸다 |
| 품질과 함께 **토큰·p95** 를 5% 이상 변할 때 표시 | 품질만 보고 고르면 운영에서 감당 못 할 설정을 고른다 |

### 5.0 비교하는 순서 (고르고 나서 무엇을 누르나)

trial 비교는 걸음이 여러 개라 처음에는 "골랐는데 이제 뭘 누르지?" 에서 막힌다. 순서는 이렇다.

| # | 무엇 | Web | CLI |
|---|---|---|---|
| ① | **어떤 질의로** 비교할지 정한다 | 문항 원천 = *실제 질의 이력*(기본) → `질의 고르기…` 에서 체크 | `trial candidates` 로 번호 확인 |
| ② | **무엇을 바꿔** 볼지 적는다 | `설정 오버라이드`(예 `top_k_final=10`) 또는 프리셋 | `--set top_k_final=10` 또는 `--preset` |
| ③ | **둘을 돌리고 비교한다** | **`A/B 비교 (기준 ↔ 변경) 한 번에`** — 같은 질의로 지금 설정과 바꾼 설정을 연달아 돌리고 바로 비교 | `trial run` 두 번 → `trial compare A B` |

```bat
:: CLI 로 ①②③ 을 손으로 하면
python -m llmwiki trial candidates --days 30 --only negative
python -m llmwiki trial run --pick 343,1 --name 기준
python -m llmwiki trial run --pick 343,1 --name 변경 --set top_k_final=10
python -m llmwiki trial compare 기준 변경
```

Web 화면 위쪽의 **`다음:`** 줄이 지금 상태에 맞춰 **할 일 하나**를 알려 준다 —
질의를 고르면 "설정을 적고 A/B 비교", 설정을 적으면 "A/B 비교를 누르세요",
trial 을 2개 체크하면 "비교를 누르세요".

> **한 번만 돌려 보려면** `Trial 실행` 이다. 그러면 trial 이 하나 생기고, 설정을 바꿔 한 번 더 돌린 뒤
> 목록에서 **두 줄을 체크하고 `비교`** 를 누르면 같은 결과를 본다. `A/B 비교` 는 그 과정을 한 번에 한다.

### 5.1 무엇을 비교하나 — 문항 원천 (2026-09-20)

예전에는 `eval/questions.json` 의 **고정 25문항**만 썼고 고를 방법이 없었다. 이제 원천을 고른다.

| 원천 | 무엇 | 언제 |
|---|---|---|
| **`queries`(기본)** | **실제 질의 이력**(`query_log`) — 기간·건수·피드백으로 고르거나 **직접 고른다** | 진짜 사용 패턴 위에서 **지연·토큰·근거 부족률**을 볼 때 |
| `evalset` | `eval/questions.json` 의 고정 문항 (정답 있음) | 검색 품질을 **정답 대비**(hit@k·MRR)로 재고 싶을 때 |

**기본이 실제 이력인 이유** (2026-09-20 에 바꿨다): 대개 알고 싶은 것은 *"진짜로 물어본 질문에서
좋아졌나"* 이고, 이 저장소에서는 평가셋 자체가 코퍼스에 색인돼 `hit@k` 가 오염돼 있다(§1 `eval --check`).
쓸 만한 이력이 없으면 **평가셋으로 물러나되 어느 원천으로 돌았는지 반드시 알린다** — 조용히 바뀌면
숫자를 잘못 읽는다. CLI·Web 의 기본이 같다.

```bat
python -m llmwiki trial run --name hist --source queries --days 7 --limit 30
python -m llmwiki trial run --name neg  --source queries --days 30 --only negative   :: 👎 받은 질의만
```

Web 은 Quality › Trial 비교의 **문항 원천** 줄에서 같은 것을 고른다.

**실제 질의 이력에는 정답이 없다.** 그래서 `hit@k` · `mrr` · `term_recall` · `answer_term_recall` 은
**계산되지 않고**, 비교 화면·markdown 이 *"계산할 수 없음 — 0점이 아닙니다"* 라고 못 박는다.
빈칸을 0점으로 읽으면 "이력으로 돌렸더니 품질이 폭락했다" 는 잘못된 결론이 나오기 때문이다.

#### 그 값을 **0 이 아니라 공백으로 저장한다** (2026-09-20에 고침)

처음에는 비교 화면에서만 "계산 불가" 라고 알려 줬다. 그런데 채점 엔진(`evaluate`)은 기대 문서가
없으면 *"못 맞혔다"* 로 세므로 **`hit@k = 0.0` 이 그대로 저장**됐고, 목록 화면과 `trial list` 는
그 0 을 보여 줬다. 결과적으로 한 화면은 "계산 불가", 다른 화면은 "0점" 이라 말했다 — 실제로 이 저장소의
질의 이력 trial 두 건이 `hit@k 0.000` 으로 남아 **완전히 실패한 설정처럼** 보였다.

이제 **저장할 때**(`run_trial`)와 **읽을 때**(`list_trials`·`get_trial`) 양쪽에서 같은 규칙을 건다.

| 어디 | 보이는 것 |
|---|---|
| `trial list` (CLI) | `hit=—  mrr=—`  + 아래에 "— 는 계산하지 않은 것입니다(0점이 아닙니다)" 안내 |
| Web 목록 | `—` (마우스를 올리면 이유) + **원천** 칸(`평가셋`/`질의 이력`) |
| Web 비교 | 경고 배너 + **"이 비교에서 읽을 수 있는 지표"** 목록 |

판단은 정답이 필요 없는 지표로 한다 — **`groundedness` · `citation_precision` · `insufficient_rate` ·
`avg_ms`/`p95_ms` · `tokens_per_query`**, 그리고 §5.2 의 단계별 표.

> 2026-09-20 이전에 0 으로 저장된 trial 도 읽을 때 공백이 된다 — 옛 기록 때문에 두 화면이 다른 말을
> 하는 일이 없도록.

#### 원천이 다르면 비교 자체가 성립하지 않는다

평가셋 trial 과 질의 이력 trial 은 **문항이 다르다.** 나란히 놓으면 숫자가 비교처럼 보이지만
같은 잣대가 아니다. 그래서 고를 때 확인을 받고, 비교 결과 맨 위에 `원천이 다른 trial 을 비교하고
있습니다` 배너를 띄운다(API `mixed_sources`/`mixed_why`). 같은 원천끼리 비교한다.

#### 비교에 쓸 **과거 질의를 직접 고른다** (2026-09-20)

`--source queries` 는 기간·건수·피드백으로 **뭉뚱그려** 가져간다. 그런데 비교하고 싶은 질의는 대개
몇 개로 정해져 있다 — *"이 세 질문이 느린데 설정을 바꾸면 나아지나"*. 고르지 못하면 비교가
내 관심사와 상관없는 문항 위에서 돈다.

```bat
python -m llmwiki trial candidates --days 30 --limit 20        :: 번호와 함께 후보를 본다
python -m llmwiki trial candidates --days 30 --only negative   :: 👎 받은 것만
python -m llmwiki trial run --pick 773,772,771 --name 내비교   :: 고른 질의로만 돌린다
```

`trial candidates` 는 줄마다 **번호 · 시각 · 👍/👎 · 근거 못 찾음 · 질의문**을 보여 준다 —
무엇을 고를지 판단할 재료다. 마지막 줄에 바로 쓸 수 있는 `trial run --pick …` 명령을 찍어 준다.

Web 은 Quality › Trial 비교에서 **문항 원천 = 실제 질의 이력**을 고르면 `질의 고르기…` 버튼이 나온다.
같은 목록이 체크박스와 함께 뜨고, 고른 것이 있으면 그것만 쓴다(고르지 않으면 위 조건으로 자동 선택).
`전체 선택 · 전체 해제 · 해제` 가 있고, 몇 개를 골랐는지 버튼 옆에 남는다.

고른 질의로 돌린 trial 은 원천이 **`직접 고른 문항`** 으로 기록된다. 정답은 여전히 없으므로
hit@k·MRR·term 은 `—` 이고, 없는 번호를 주면 **조용히 버리지 않고** `missing` 으로 알려 준다.

#### A/B 한 번에 (Web)

trial 비교의 실제 목적은 **"이 설정을 바꾸면 좋아지나?"** 하나다. 예전에는 그러려면
① 기준 실행 ② 설정 바꾸기 ③ 두 번째 실행 ④ 체크 ⑤ 비교 — 다섯 걸음이었다.
Quality › Trial 비교의 **`A/B 비교 (기준 ↔ 변경) 한 번에`** 는 **같은 문항**으로
지금 설정(기준)과 위에 적은 오버라이드·프리셋을 적용한 변경본을 연달아 돌리고 바로 비교한다.
CLI 로는 `trial run` 두 번 + `trial compare` 와 같은 일이라 새 기능은 아니다.

같은 질문은 한 번만 넣고 최근 것부터 고른다. `--only` 는 `negative`(👎) · `feedback`(평가가 달린 것) ·
`insufficient`(근거를 못 찾은 것).

왜 이것이 필요한가: 평가셋은 사람이 미리 적어 둔 목록이라 (a) 실제 질문과 다르고 (b) 이 저장소에서는
**평가셋 자체가 코퍼스에 색인돼 있어** `hit@k` 가 "오염을 얼마나 피했나" 를 재고 있다(§1 `eval --check`).
실제 질의로 돌리면 둘 다 피한다.

### 5.2 단계별 비교 — 어느 단계가 그 값을 치렀나 (2026-09-20)

예전 trial 은 **파이프라인 최종 결과 12개**만 저장했다. 그래서 "rerank 를 켜서 느려진 만큼 값어치를 했나",
"토큰이 어디서 늘었나" 에 답할 수 없었다. 각 문항의 요청 trace 에 단계별 ms·토큰·호출수가 이미 있으므로
(profiler), 문항 전체에 걸쳐 합쳐 저장하고 비교한다.

```
## 단계별 (질의 1건당 · 낮을수록 좋다)
| 단계          | A ms    | B ms    | Δms      | Δ토큰   |
| vector_search | 5008.4  | 31.8    | -4976.6  | +0     |
| claim_check   | 18028.5 | 13987.8 | -4040.7  | -10529 |
```

- **달라진 단계가 앞으로** 온다 (질의당 ±5ms 또는 ±50토큰 기준). 나머지는 감춘다 — 26개를 다 보면 아무것도 안 보인다.
- 한쪽에서만 돈 단계는 `—` 로 (설정 때문에 꺼진 것이지 0ms 가 아니다).
- 2026-09-20 **이전** trial 에는 단계 기록이 없다. 그럴 때는 비교가 깨지지 않고 *"다시 돌리면 채워집니다"* 라고 적는다.

이 저장소의 실제 질의 5건으로 재 보면 한 질의당 `answer_llm` 15.2초/53,442토큰,
**`claim_check` 14.0초/41,869토큰** — 답변 자체와 맞먹는 비용이다. 최종 지표만 보면 보이지 않는 사실이다.

---

## 6. 세 창구

| 하는 일 | CLI | Web | MCP |
|---|---|---|---|
| 신뢰도 점검 | `eval --check` | Quality › 평가 의 **신뢰도 점검** | — |
| 평가 | `eval [--k N] [--retrieval-only]` | **현재 토글로 평가** + `검색 전용` 체크 | — |
| 놓친 문항 원인 | `eval --forensic` | `놓친 문항 원인 분석` 체크 | — |
| 채널 조합 비교 | `eval --matrix` | **채널 조합 비교** | — |
| trial 실행·비교 | `trial run` · `trial compare a b` | Quality › trial | — |
| **문항 원천 고르기** | `trial run --source evalset\|queries [--days --limit --only]` | Quality › trial 의 **문항 원천** 줄 | — |
| **단계별 비교** | `trial compare a b` 출력의 `## 단계별` | 비교 결과의 **단계별** 표 | — |
| 기대 결과 포렌식 | `forensic expect <id> --doc --term` | Quality › 포렌식 | `wiki_forensic` |

평가·trial 을 MCP 에 두지 않는 것은 의도다 — 붙은 LLM 이 자기 설정을 평가해 스스로 바꾸면 HITL 이 아니다.
원인 분석(`wiki_forensic`)은 읽기라서 열어 둔다.

---

## 7. 지표 읽는 법

| 지표 | 뜻 | 주의 |
|---|---|---|
| `hit@k` | 기대 문서가 상위 k 안에 든 문항 비율 | 가장 변별력이 크다. 한 칸 = 1/문항수 |
| `mrr` | 기대 문서의 순위 역수 평균 | 순위가 1위인지 5위인지까지 본다 — hit@k 보다 민감 |
| `term_recall` | 기대 용어가 **근거 본문**에 있는 비율 | 아무 문서에서나 나와도 1.0 → 쉽게 포화된다 |
| `answer_term_recall` | 기대 용어가 **답변**에 있는 비율 | 검색 전용에서는 None |
| `groundedness` | 답변 문장이 인용 근거로 지지되는 비율 | [QA_HARDENING §2](history/2026-09-19/QA_HARDENING_0919.md) 의 claim 검증 |
| `citation_precision` | 인용한 문장 중 실제로 지지된 비율 | 없는 인용 `[C99]` 는 0 점 처리 |
| `insufficient_rate` · `fallback_rate` | 근거 부족·재시도 비율 | 낮을수록 좋다 |
| `avg_ms` · `p95_ms` · `tokens_per_query` | 속도·비용 | trial 비교의 **대가** 열 |

---

## 8. 검증

| 무엇 | 어떻게 |
|---|---|
| 신뢰도 점검·변별력·유의성·대가·검색 전용 | `python -m unittest tests.test_quality_loop` (18개) |
| 평가·trial·포렌식 CLI 전수 | `python tools/verify/verify_cli.py` |
| Web 평가·trial 화면 | `python tools/verify/verify_buttons.py` |
