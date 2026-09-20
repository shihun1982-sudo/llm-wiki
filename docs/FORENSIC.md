# FORENSIC — 포렌식 디버깅: "왜 답이 부실했나" (자동) + "내가 기대한 문서가 왜 안 나왔나" (기대 결과 포렌식)

> 대상: 답변 품질 문제를 재현·진단해 규칙/pin/튜닝/코퍼스 중 무엇을 고칠지 결정하려는 운영자, 그리고 사용자 피드백을 받아 같은 절차를 자동으로 돌리려는 외부 LLM(MCP `wiki_forensic`). 설계 배경은 [IMPLEMENTATION_PLAN_0914.md](IMPLEMENTATION_PLAN_0914.md) §6.

## 0. 두 종류의 포렌식 (+ 상세 분석 모드)

> 세 번째 도구: **상세 분석 모드** `analysis_mode` — 질의 한 건의 모든 단계 결과·설정·품질/속도/토큰 렌즈 소견을 한 장의 마크다운으로 남겨 LLM 에게 넘길 수 있다. "왜 이 근거가 빠졌나"(포렌식)보다 넓게 "이 질의는 어디에 시간·토큰을 쓰고 어느 설정을 만져야 하나"를 묻는다 → [ANALYSIS_MODE.md](ANALYSIS_MODE.md).

| | 자동 포렌식 (`forensic_auto`) | 기대 결과 포렌식 (`forensic expect`) |
|---|---|---|
| 언제 | 질의 결과가 `verdict≠sufficient` / `insufficient` / groundedness 낮음 일 때 자동 | 사용자가 "이 문서/용어가 답에 있어야 했다" 고 알려줄 때 (모든 질의 결과에서 요청 가능) |
| 입력 | 요청의 trace(단계별 메타) | request_id + 기대 문서(ext_id/doc_id)·용어·청크 + 메모 |
| 하는 일 | 단계별 진단 규칙(FTS 0건·벡터 유사도 낮음·그래프 시드 없음·컨텍스트 상한·claim 미지원 …) → findings/suggestions | 같은 설정으로 검색을 **재실행**해 기대 청크가 fts/vector/graph → 융합 → 부스트 → 리랭크 → 최종 후보 → doc_expand → 컨텍스트 → 답변 중 **어느 단계에서 탈락했는지**와 원인·수정안 |
| 결과 | `forensics` 행(origin=auto/manual) | `forensics` 행(origin=expectation) + 에피소드 피드백(-1) + (옵션) HITL 제안(pin/query_rule/tuning/corpus_gap) |
| 어디서 | `forensic last|<id>|summary`, Web › Quality › 포렌식, Ask 결과의 🔬 | `forensic expect …`, Web › Ask 결과의 🎯, Quality › 포렌식, MCP `wiki_forensic`, `POST /api/forensic/expect` |

## 1. 자동 포렌식 (기존) — 확인 방법

```bat
python -m llmwiki query "블루투스 오디오 코덱 aptX 지연 튜닝 방법"     :: 코퍼스에 없는 주제 → insufficient → 자동 기록
python -m llmwiki forensic last                                         :: 마지막 소견: findings(단계·문제·근거) + suggestions(corpus_gap/query_rule/tuning)
python -m llmwiki forensic summary                                      :: 누적: 판정별 건수 · 문제 단계 · 주제 · 제안 종류
python -m llmwiki forensic <request_id> --llm                           :: forensic 역할 LLM 소견 추가
python -m llmwiki memory consolidate                                    :: 같은 주제 소견이 forensic_min_events 이상 반복되면 제안으로 승격
```
동작 확인은 `tests/test_phase3_5.py::test_evidence_fallback_forensic` 과 실측(README §7)으로 재검증했다. 한계: trace 만 보므로 "무엇이 나왔어야 했나"는 모른다 → §2.

## 2. 기대 결과 포렌식 (`forensic expect`)

### 2.1 사용
```bat
:: 마지막 질의에 대해 "ISSUE-2003 문서의 1.5dB 내용이 나왔어야 한다"
python -m llmwiki forensic expect last --doc ISSUE-2003 --term 1.5dB --note "TX 전력 문서를 기대"
:: request id 지정 · 여러 문서/용어 · 수정안을 제안 큐에 등록
python -m llmwiki forensic expect 526 --doc HWD-PHY-TIMING-B1 --doc RULE-REG-002 --term 8ns --term t_setup --propose
:: 청크 id 직접 지정
python -m llmwiki forensic expect 526 --chunk "sample_corpus_modem/hw_design/HWD-PHY-TIMING-B1.md#3" --json
```
Web: Ask 결과 아래 **🎯 기대 결과 포렌식** → 문서 ID/용어/메모 입력 → 분석. Quality › 포렌식 탭에서도 request id 로. API: `POST /api/forensic/expect {request_id, docs, terms, chunks, note, propose}` (read 등급 — viewer/게스트/외부 LLM 도 가능). MCP: `wiki_forensic`.

### 2.2 무엇을 하는가 (`forensic.trace_expectation`)
1. **원 요청 확인** — `requests.result` 의 `hits_brief`(청크·인용 번호·컨텍스트 포함 여부)로 기대 청크가 원 답변에서 `cited`(인용) / `candidate`(후보였으나 제외) / `absent` 였는지. 캐시·프리컴퓨트 적중 요청이면 원 요청으로 따라간다(`followed_from`).
2. **목표 청크 결정** — 기대 문서(ext_id `ISSUE-2003` 또는 doc_id 부분 문자열) 의 청크 중 기대 용어를 담은 것(없으면 문서 전체), 또는 용어만 준 경우 코퍼스 전체에서 그 용어를 담은 청크(최대 20). 용어가 코퍼스 어디에도 없으면 `corpus_gap`.
3. **재실행** — 원 요청과 같은 토글·튜닝·프리셋으로 검색만 다시 실행(LLM 답변·claim 검증·캐시·로그·자동 포렌식은 끔; `requests` 에 남지 않음). 엔진이 라운드 내부(채널별 리스트, 융합 순서, 부스트 순서, 리랭크 후보, 최종 후보, doc_expand 채택, 컨텍스트 인용, 탈락 목록)를 캡처한다.
4. **단계별 여정** — 목표 청크마다:

| 단계 | hit 이면 | miss 이면 적는 것 | 자동 수정안 |
|---|---|---|---|
| fts | 어느 FTS 리스트(원 질의/규칙/대체 질의) 몇 위 | 질의 키워드 중 청크에 있는 것/없는 것, OR 검색 전체 순위(500위까지), 청크 헤딩의 대표 용어 | `query_rule`(없는 키워드 ↔ 헤딩 용어 동의어), `tuning top_k_fts`(순위가 3배 이내) |
| vector | 몇 위 | 전체 벡터 중 순위·유사도·최고 유사도·임계 | `tuning top_k_vector`, 임베딩 없음이면 coverage 확인 |
| graph | 몇 위 | 청크의 엔티티(멘션) vs 시드 엔티티, 겹침 | `alias`(질의 키워드 → 엔티티 별칭) |
| fusion / boost | 융합 순위 / 부스트 후 순위·배율 | 후보 아님 (어느 채널에도 없음) | – |
| rerank | 후보 안·최종 순위·점수 | 리랭크 후보 밖(부스트 순위) 또는 top_k_final 밖 | `tuning rerank_candidates` / `top_k_final` |
| doc_expand | 확장으로 추가됨(부모·점수) | 같은 문서가 확장 대상이었지만 점수 미달/상한, 또는 문서가 상위 N 에 없음 | `tuning doc_expand_min_score/max_chunks` |
| context | `[C#]` | dedupe/글자 상한으로 탈락, 최종 후보 아님 | `tuning context_max_chars` |
| answer | 원 답변에서 인용 + 기대 용어 포함 | 근거는 있었으나 답변에 용어 없음(답변 생성 단계) / 후보였으나 제외 / 원 결과에 없음 | `tuning answer_length_target=long`, answer_guide 점검 |

5. **대표 원인** — 가장 멀리 간(가장 아까운) 목표 청크 기준으로 첫 탈락 단계와 원인을 요약하고, 그 청크의 수정안만 낸다(잡음 억제). 항상 `pin`(문서 고정) 제안이 붙는다.
6. **기록** — `forensics`(origin=expectation, findings=청크별 탈락 단계), `episodes`(kind=expectation, feedback −1, 메모), `--propose` 면 `proposals` 에 pin/query_rule/tuning/alias/corpus_gap 등록 → `evolve status` / `evolve apply <id>` (pin·query_rule·tuning 은 자동 적용 가능, corpus_gap 은 문서 추가 안내).

### 2.3 출력 예 (실측, 샘플 모뎀 코퍼스)
```
forensic expect — request #526  Q: HW rev B1 에서 t_setup 은 몇 ns 인가?
원 판정=sufficient groundedness=None mode=extractive · 재실행=True (1 라운드, verdict sufficient) · 기대: docs=['ISSUE-2003'] terms=['1.5dB']
  목표 청크 6개 중 원 답변 컨텍스트에 포함 0개 · 탈락 단계 분포 {'retrieval': 6}
  대표 원인 (…/ISSUE-2003.md#3, 어느 채널에도 없음): FTS(키워드) 검색 — 질의 키워드 ['hw','rev','b1','setup','ns'] 중 청크에 있는 것 ['setup'] · OR 검색 전체 순위 23 (top_k_fts=20 밖)

● …/ISSUE-2003.md#3  (TX 전력이 목표 대비 1.5dB 낮음 > 분석 · 문서+용어) 원 결과=absent → 탈락 단계=retrieval
    ✘ fts         질의 키워드 … 없는 것 ['hw','rev','b1','ns'] · OR 검색 전체 순위 23 (top_k_fts=20 밖)
    ✘ vector      #52 전체 190 벡터 중 순위 52, 유사도 0.067 (최고 0.273 …)
    ✘ graph       청크 엔티티 ['ISSUE-2003','TC-TX-POWER-003'] · 시드 ['CL-55305','HWD-PHY-TIMING-B1',…] · 겹침 []
    ✘ fusion      융합 후보 아님
    ✘ final       top_k_final=10
    ✘ doc_expand  문서가 확장 대상(상위 3 문서)에 들지 못함
    ✘ context     최종 후보 아님
    ✘ answer      원 요청 결과에 없음
제안:
  - (tuning 0.40) top_k_fts 를 23 이상으로 (FTS OR 순위 23)
  - (query_rule 0.35) 'hw' ↔ 'tx' 동의어/관련어 등록 검토 (기대 청크 헤딩 용어; rules add synonym hw tx)
  - (pin 0.60) 이 질의 유형에 문서 …/ISSUE-2003.md 를 고정 (pin add --doc ISSUE-2003 --keywords hw,rev,b1)
forensics #32 에 기록됨 (origin=expectation)
```
(이 예는 일부러 무관한 문서를 기대한 경우다. 같은 질의에 `--doc HWD-PHY-TIMING-B1 --term 8ns` 를 주면 모든 단계가 ✔ 이고 "정상 인용됨 · 기대 용어 ['8ns'] 답변 포함" 으로 끝난다.)

### 2.4 해석 가이드
- **retrieval 탈락 + fts 에 키워드 없음** → 어휘 불일치. `rules add synonym <질의어> <문서어>` 또는 acronym/alias, 아니면 문서 표기를 계약(front matter tags)에 맞춘다.
- **retrieval 탈락 + 순위가 top_k 바로 밖** → `top_k_fts/top_k_vector` 확대 또는 프리셋 `quality`. 비용이 걱정되면 `pin`.
- **rerank 탈락** → `rerank_candidates`/`top_k_final`, 또는 rerank 방식(api/cross_encoder). LLM 리랭크가 특정 문서를 계속 내리면 `rerank_llm` 끄고 비교(`trial`).
- **context 탈락** → `context_max_chars` 확대, `dedupe_hits` 확인(복붙 문단이면 정상).
- **doc_expand 미달** → `doc_expand_min_score` 낮추기/`max_chunks` 올리기(같은 문서의 다른 절에 답이 있을 때 특히 유효).
- **answer 탈락(근거는 있었음)** → `answer_length_target=long`, `prompts/answer_guide.md` 에 "수치·ID 를 빠짐없이" 지시, `claim_policy` 가 drop 이면 mark 로.
- **corpus_gap** → 문서 추가. `memory consolidate` 로 반복 여부를 본다.

#### 자주 헷갈리는 조합 — "원 판정은 sufficient 인데 기대 문서는 미해결"

두 값은 **다른 질문에 대한 답**이다. 나란히 놓여 있어서 모순처럼 보일 뿐이다.

| 보이는 것 | 뜻 | 할 일 |
|---|---|---|
| 원 판정 `sufficient` | 그 질의의 답변은 **가진 근거로 충분**했다 | — |
| 기대 항목이 **미해결**(`expected.unresolved`) | 당신이 적은 ID/청크가 **색인에 아예 없다** — 코퍼스에 없거나 ID 표기가 다르다 | 문서를 넣거나, `data/rules.json` 의 `id_patterns` 와 표기를 맞춘다. **설정을 만질 문제가 아니다** |
| 판정 `sufficient` + 기대 청크가 하나도 인용 안 됨 | 답은 했지만 **다른 문서로** 했다 | 아래 '탈락 단계' 에서 어디서 밀렸는지 본다 |

화면(Ask · Quality)은 이 세 경우를 한 문장으로 먼저 알려 준다. 미해결 항목은 빨간 글자 대신
"무엇을 해야 하는가" 가 붙은 안내 배너로 나온다.

#### 화면이 긴 경우 (목표가 수십 개 / 수정안이 여러 건)

- `--term` 만 주면 그 용어를 담은 청크가 전부 목표가 된다(최대 `forensic_term_targets`, 기본 20).
  서버가 **가장 멀리 간 것부터** 정렬해 주고, 화면은 앞의 `forensic_targets_shown`(기본 3)개만 펼친다.
  나머지는 "나머지 목표 청크 N개" 로 접혀 있다 — 같은 형식의 표가 반복되어 읽기 어려웠던 문제.
- 수정안은 **confidence 내림차순**으로 온다. 예전에는 담긴 순서(목표별 → 전역 → corpus_gap → pin) 그대로여서
  근거가 약한 0.35 짜리가 0.8 짜리보다 위에 있었다. `forensic_suggestion_min_confidence`(기본 0.5) 미만은
  버리지 않고 `low_confidence: true` 로 표시되어 화면에서 접힌다 — 위의 것부터 적용하고, 효과가 없을 때 펼쳐 본다.

### 2.4.1 튜닝 키 (`tuning.json`, `tuning show --stage forensic`)

| 키 | 기본 | 뜻 |
|---|---|---|
| `forensic_near_miss_mult` | 3 | 기대 청크가 채널 top_k 밖이지만 `top_k × 이 값` 안에 있으면 "top_k_fts/top_k_vector 를 N 으로" 튜닝 제안. 1 이면 사실상 제안 없음 |
| `forensic_term_candidates` | 6 | FTS 탈락 청크에서 뽑는 대표 용어(동의어 후보) 수 — 헤딩 용어 우선, 그다음 빈도순 |
| `forensic_term_targets` | 20 | `--term` 만 주고 `--doc` 이 없을 때 코퍼스에서 그 용어를 담은 청크를 최대 몇 개까지 목표로 삼는가 (많으면 느려짐) |
| `forensic_pin_confidence` | 0.6 | pin 제안의 confidence (Evolve 목록 정렬·자동 적용 임계 비교값) |
| `forensic_suggestion_min_confidence` | 0.5 | 수정안을 **바로 펼쳐** 보여 줄 최소 confidence. 미만은 접어 둔다(버리지 않는다) |
| `forensic_targets_shown` | 3 | 목표 청크의 '탈락 단계' 표를 바로 보여 줄 개수 (가장 멀리 간 것 순). 나머지는 접힌다 |
| `forensic_min_events` | 3 | (자동 포렌식) 같은 주제 소견 누적 → corpus_gap 제안 |
| 토글 `forensic_auto` | on | 질의마다 자동 포렌식 기록 |

### 2.5 LLM 실행 실패와의 구분
답변 상단에 `⚠ LLM 실행 보고: answer(headless:opencode/…) 4회 시도 후 실패(timeout 300s) → 추출식 답변으로 대체` 가 있으면 검색이 아니라 **LLM 호출**이 실패한 것이다(`result.llm_report`, Web Ask 배너, 빌드 alerts). 이 경우 기대 결과 포렌식은 검색 단계를 정상으로 판정할 수 있으니 먼저 `models test --live` 와 `agents.json timeout_s/retries`, `config.json llm_timeout/llm_retries` 를 본다. 상세: BRINGUP_GUIDE §4.3.

## 3. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/forensic.py` | `diagnose`(자동), `resolve_expected`, `trace_expectation`, `format_expectation`, `record/list/summary` |
| `llmwiki/query_engine.py` | 라운드 캡처(`QueryEngine.rounds`: lists·fused_order·boost_order·rerank_before·final_order·expand·context_ids), `record_request=False` 재실행 모드, `hits_brief` 저장, `llm_report` |
| `llmwiki/answer.py` | `build_context` 가 `dropped/neighbors/doc_expand` 를 돌려줌 |
| `llmwiki/evolve.py` | 제안 kind `pin`/`query_rule`/`tuning` 적용, `corpus_gap` 은 자동 적용 불가 안내 |
| `llmwiki/cli.py` `web/server.py` `mcp.py` `web/static/js/ask.js` `quality.js` | `forensic expect`, `/api/forensic/expect`, `wiki_forensic`, Ask/Quality 화면 |
| `tests/test_features_0914.py::ForensicExpectTest` | 인용된 경우/무관 문서/코퍼스 없는 용어/answer 단계 탈락/캐시 요청 따라가기/제안 적용 · **미해결 안내와 수정안·목표 정렬**(`test_expect_report_is_ordered_and_explains_unresolved`) |
