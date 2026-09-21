# 최적화 가이드 — 어떤 손잡이가 어느 단계에 어떻게 작용하는가

> **이 문서는 자동 생성된다.** 코드(`llmwiki/architecture.py` 의 흐름 정의 + `llmwiki/tuning.py` 의 파라미터 레지스트리)에서
> 만들어지므로 단계나 설정이 바뀌면 여기도 바뀐다. 다시 만들려면 `python -m llmwiki arch doc`.
>
> **LLM 에게 최적화를 물을 때는 이 문서 하나만 주지 말고 `python -m llmwiki optimize last` 가 만드는 묶음을 주세요.**
> 그 묶음은 이 가이드 + 지금 설정값 + 실제 질의 한 건의 단계별 실측(시간·토큰·근거 판정)을 한 파일로 담습니다.

## 0. 세 가지 렌즈

조절은 언제나 셋 중 하나를 얻고 다른 것을 내주는 거래다. 먼저 무엇을 얻고 싶은지 정한다.

| 렌즈 | 보는 숫자 | 대표 손잡이 (효과 큰 순서) |
|---|---|---|
| **품질** | groundedness · 근거 판정(sufficient/weak/insufficient) · 인용 수 | `rerank_llm` → `doc_expand`(+`doc_expand_mode=full`) → `query_expand` → `top_k_*` → `context_max_chars` → `claim_check_llm` |
| **속도** | 총 ms · 단계별 ms · LLM 대기 시간 | `rerank_llm` 끄기 → `query_expand` 끄기 → `claim_check_llm` 끄기 → `top_k_*` 줄이기 → `llm_roles.*.timeout_s` 줄이기 → `precompute` 켜기 |
| **토큰** | 입력/출력 토큰 · LLM 호출 수 | `context_max_chars` → `top_k_final` → `doc_expand_max_chunks` → `llm_roles.*.max_tokens` → `evidence_compress` → `rerank_candidates` |

세 렌즈의 기본 조합은 프리셋으로 묶여 있다(`quality` · `speed` · `token` · `offline` · `deep_research`).
프리셋은 **요청 단위**로만 적용되며 서버 기본 설정을 바꾸지 않는다 — 먼저 프리셋으로 방향을 잡고, 그 다음 개별 손잡이를 만진다.

## 1. 설정이 사는 곳과 적용 시점

| 어디 | 무엇 | 언제 반영되나 |
|---|---|---|
| `config.json` `toggles.*` | 단계를 켜고 끔 (59개) | 즉시 (질의 단위 오버라이드 가능) |
| `config.json` 최상위 | 프로바이더·역할별 모델/정책·top_k·컨텍스트 길이·운영 수치 | 대부분 즉시, 임베딩 관련은 재빌드 |
| `config.json` `llm_roles.<role>` | 역할별 `timeout_s`·`retries`·`backoff`·`budget_s`·`circuit_*`·**`max_tokens`** | 즉시 (Settings › 모델에서 편집) |
| `tuning.json` | 알고리즘 상수 (단계별) | 즉시. `rebuild=true` 인 항목만 재빌드 필요 |
| `presets.json` | 위 셋의 묶음 | 요청 단위 |
| `server.json` | 동시성·대기열·속도 제한 (품질과 무관, 처리량) | 즉시 |

우선순위는 항상 **CLI 플래그 > 환경변수(`LLMWIKI_*`) > 파일 > 코드 기본값** 이고,
질의 한 건에는 **요청 오버라이드 → 프리셋 → 서버 기본값** 순으로 얹힌다.

## 2. 읽는 법

아래 흐름별 표에서 한 줄이 한 단계다.

- **trace 이름** = `query --trace` 출력과 상세 분석 리포트의 단계 이름. 실측 시간을 이 이름으로 찾는다.
- **토글** = 그 단계를 켜고 끄거나 동작을 바꾸는 스위치.
- **튜닝** = 그 단계의 알고리즘 상수 (`tuning set <키>=<값>`).
- **설정** = `config.json` 키.
- **영향** = 이 단계가 품질/속도/토큰에 어떻게 작용하는지.


## 3. 전체 구조

```
query   : sync_index → providers → cache_hit → time_scope → query_rules → router → query_expand → pins → fts_search → vector_search → graph_search → doc_vector_search → external_rag → rrf_fuse → doc_acl → fusion_llm → rerank → rerank_review_llm → doc_expand → context → evidence → answer → claim → forensic → evolve_capture → log → analysis
build   : health → providers → load_corpus → diff → chunk_index → embed → graph_build → wiki_pages → prune → warm_cache
evolve  : capture → hitl → snapshot → apply → regress → evolution_log
watch   : scan → build_incremental
```

각 흐름의 진입점

| 흐름 | 무엇 | 실행 |
|---|---|---|
| **query** | User Request (질의 → 답변) | python -m llmwiki query "…" · Web Query 탭 · MCP wiki_query |
| **build** | Data Build (색인) | python -m llmwiki build [--full] · Web Build 탭 · watch/auto_build |
| **evolve** | Self-Evolving (제안 → 적용 → 검증) | Web Evolve 탭 · evolve status／apply／reject／review／feedback |
| **watch** | Auto Build (워처) | serve (toggles.auto_build) · watch [--interval] |

## 4. query — User Request (질의 → 답변)

질의 → (캐시) → 라우터 → 3채널 검색 → 융합 → 리랭크 → 컨텍스트 → 답변 → 자가진화 캡처 → 로그. 모든 단계가 trace 로 기록.

| trace 이름 | 단계 | 토글 | 튜닝 | 설정 | 영향 |
|---|---|---|---|---|---|
| `sync_index` | 인덱스 동기화 | - | - | - | 재시작 없이 최신 색인 반영. |
| `providers` | 프로바이더 준비 | `llm_answer`, `rerank_llm` | - | `llm_roles.answer`, `llm_roles.rerank` | 최초 질의 지연. 이후 0ms. |
| `cache_hit, precompute_hit, precompute_miss` | 질의 캐시 · 프리컴퓨트 | `query_cache`, `precompute`, `precompute_after_build` | - | `query_cache_size` | LLM 토큰 0, 수 ms. 로그/자가진화는 건너뜀. 재빌드 시 자동 무효화. |
| `time_scope` | 시간 표현 해석 | `time_scope` | `time_mode`, `time_boost_w`, `recency_half_life_days` | `timezone`, `week_start` | 시간 조건이 있는 질문의 정밀도↑. filter 가 0건이면 boost 로 자동 완화. |
| `query_rules, expansion_profile` | 규칙 기반 질의 확장 | `query_rules`, `profile_expansion` | `syn_w`, `related_w`, `exclude_penalty`, `acronym_phrase`, `query_rules_max_rounds`, `related_symmetric` | - | LLM 없이 결정적으로 recall↑. related 를 별도 리스트로 두어 precision 보호. profile_expansion 으로 전/후 효과 기록. |
| `router` | 적응형 라우터 | `router`, `router_llm` | `router_short_kw`, `router_long_kw`, `router_entity_min`, `router_strong_seed`, `router_base_graph`, `router_kw_fts` 외 7 | - | 채널 가중치가 융합 결과를 직접 좌우. 끄면 1:1:1. |
| `query_expand` | LLM 질의 확장 (옵션) | `query_expand`, `query_decompose` | `query_expand_n`, `query_expand_w`, `query_decompose_max` | `llm_roles.expand` | 어휘 불일치 완화(recall↑). LLM 1회(토큰·지연↑). |
| `pins` | 고정 근거(pin) | `pins` | - | - | 코딩 규칙처럼 특정 질의 유형에 항상 포함할 문서, 사용자가 확인한 정답 근거 고정. |
| `fts_search, fts_search_rules, fts_search_alt, fts_search_related` | FTS(BM25) | `fts` | `top_k_fts`, `fts_mode`, `fts_and_min_hits`, `fts_w_heading`, `fts_w_body`, `fts_w_tokens` 외 6 | `top_k_fts` | 정확 키워드·숫자·날짜에 강함. 한국어 조사 변형은 토큰 컬럼으로 흡수. |
| `vector_search` | 벡터 검색 | `vector` | `top_k_vector`, `vector_min_sim` | `top_k_vector` | 의미 유사 문단 recall. hash 임베더는 표기 변형에 강하나 의미 이해는 못함. |
| `graph_search` | 그래프 검색 | `graph` | `top_k_graph`, `graph_hops`, `graph_seed_min`, `graph_max_seeds`, `graph_decay`, `graph_hub_exp` 외 9 | `top_k_graph`, `graph_hops` | 다중 홉·관계형 질문에 강함. 시드가 없으면 빈 결과. |
| `doc_vector_search` | 문서 카드 벡터 (옵션) | `doc_vector` | - | - | 긴 설계 문서의 문서 단위 recall↑. 빌드 시 doc_vectors 생성 필요. |
| `external_rag, mcp_enrich, external_inject` | 외부 RAG 채널 (옵션) | `external_rag` | `rrf_k`, `fusion_method`, `fusion_multi_bonus`, `channel_w_fts`, `channel_w_vector`, `channel_w_graph` 외 26 | - | 다른 팀 RAG·사내 검색을 코드 수정 없이 채널로 추가. 외부 지연이 더해지므로 timeout_s 와 weight 로 제어. 소스 오류는 채널 하나만 비고 질의는 계속. |
| `rrf_fuse, boost, channel_inject` | 융합 · 부스트 | `pins`, `feedback_boost` | `rrf_k`, `fusion_method`, `fusion_multi_bonus`, `channel_w_fts`, `channel_w_vector`, `channel_w_graph` 외 26 | `rrf_k` | 다중 채널 합의 후보가 상위로. 방식은 `fusion compare` 로 평가셋 비교. 모든 배율은 hit.boosts 에 기록. |
| `doc_acl` | 문서 접근 제어 | `doc_acl` | - | - | 검색은 읽기다 — 등급이 다른 문서가 섞인 위키에서 RAG 가 유출 경로가 되지 않게 한다. 규칙이 비어 있으면 아무도 막지 않고, admin 은 항상 전부 본다. 가려진 건수는 trace 와 응답 메타에 남는다. |
| `fusion_llm` | 융합 뒤 LLM 검토 (옵션) | `llm_after_fusion` | `rrf_k`, `fusion_method`, `fusion_multi_bonus`, `channel_w_fts`, `channel_w_vector`, `channel_w_graph` 외 26 | `llm_roles.fusion` | 리랭크 창에 들어갈 후보를 미리 걸러 정밀도↑·리랭크 토큰↓. LLM 1회 추가. 실패·파싱 오류면 순위를 그대로 둔다(품질을 깎지 않는 방향). 전부 drop 하라는 응답은 무시한다. |
| `rerank_llm, rerank_cross_encoder, rerank_local, rerank_api` | 리랭크 | `rerank`, `rerank_llm` | `rerank_candidates`, `rerank_chunk_chars`, `rerank_method`, `rerank_ce_model`, `rerank_w_cover`, `rerank_w_consensus` 외 4 | `rerank_candidates`, `rerank_chunk_chars`, `llm_roles.rerank` | 최종 MRR 에 가장 직접적. LLM 은 토큰 1회, 크로스인코더는 로컬 CPU, 로컬은 수 ms. |
| `rerank_review_llm` | 리랭크 뒤 LLM 선택 (옵션) | `llm_after_rerank` | `rerank_candidates`, `rerank_chunk_chars`, `rerank_method`, `rerank_ce_model`, `rerank_w_cover`, `rerank_w_consensus` 외 4 | `llm_roles.select` | 컨텍스트 구성을 LLM 이 정한다(정밀도↑). expand_docs 는 doc_expand 가 그 문서를 우선·전체 확장한다(토큰↑). LLM 1회 추가. 실패·빈 select 면 리랭크 순위 그대로. |
| `doc_expand` | 문서 단위 확장 | `doc_expand` | `top_k_final`, `context_max_chars`, `context_chunk_chars`, `context_chars_per_token`, `context_budget_reserve_tokens`, `context_min_fit_chars` 외 9 | - | 한 문서의 표·목록·후속 문단이 잘려 나가는 문제 완화(근거 완전성↑). 토큰↑ → 상한(max_chunks·min_score)으로 제어. speed/token 프리셋은 off. |
| `context` | 컨텍스트 구성 | `context_trim`, `dedupe_hits`, `context_guard` | `top_k_final`, `context_max_chars`, `context_chunk_chars`, `context_chars_per_token`, `context_budget_reserve_tokens`, `context_min_fit_chars` 외 9 | `top_k_final`, `context_max_chars`, `context_chunk_chars` | 답변 LLM 입력 토큰과 근거 완전성을 결정. |
| `evidence_check, fallback` | 근거 충분성 · fallback 루프 | `evidence_check`, `evidence_check_llm`, `fallback_loop`, `mcp_sources` | `evidence_min_score`, `evidence_min_channels`, `evidence_min_cover`, `evidence_min_chars`, `fallback_max_attempts`, `fallback_token_budget` 외 3 | `llm_roles.verify` | 근거 부족 답변을 줄임. 불충분할 때만 비용 발생. 무한 루프 방지 예산 필수. |
| `answer_llm, answer_extractive, answer_insufficient, evidence_compress` | 답변 생성 | `llm_answer`, `evidence_compress` | `answer_length_target`, `answer_max_tokens`, `answer_repeat_guard`, `answer_repeat_min_chars`, `answer_repeat_times`, `answer_effort` 외 8 | `answer_max_tokens`, `answer_effort`, `llm_roles.answer` | 품질의 최종 출력. LLM 없으면 자동 추출식. 가이드 md 를 편집해 구조/문체 변경. |
| `claim_check, answer_refine` | 답변 검증 (claim check) | `claim_check`, `claim_check_llm`, `answer_refine` | `claim_support_min`, `claim_policy`, `claim_min_groundedness` | `llm_roles.verify` | hallucination 억제. 휴리스틱은 무료, LLM 판정은 토큰↑. |
| `forensic, forensic_expect` | 포렌식 (자동 · 기대 결과) | `forensic_auto`, `llm_failure_report` | `memory_half_life_days`, `memory_archive_strength`, `forensic_min_events`, `forensic_near_miss_mult`, `forensic_term_candidates`, `forensic_term_targets` 외 3 | `llm_retries`, `llm_retry_backoff_s` | 실패 원인 추적과 자가진화 데이터 확보. LLM 호출 실패는 llm_report 로 함께 보고. |
| `evolve_capture, episode` | 자가진화 캡처 | `evolve_capture`, `evolve_auto_apply` | - | `evolve_min_confidence`, `evolve_low_score_threshold` | 제안은 Evolve 탭에서 HITL 승인. 자동 적용은 evolve_auto_apply. |
| `log` | 로그 · 요청 기록 | - | - | `keep_requests` | Requests 탭 / `requests` CLI 의 원천. |
| `analysis` | 상세 분석 리포트 (analysis_mode) | `analysis_mode` | - | `debug_level`, `keep_requests` | 품질/지연/토큰 디버깅의 출발점. LLM 에게 그대로 넘겨 튜닝을 물을 수 있다. 질의당 수십 ms·requests 행 크기 증가 → 디버깅할 때만 켠다. |

기본값과 다른 값 (지금 이 서버)

| 키 | 기본 | 현재 | 영향 |
|---|---|---|---|
| `query_expand_n` | 2 | **3** | 많을수록 recall↑ 지연↑ (질의마다 FTS+벡터 실행). |
| `prf_enabled` | false | **true** | LLM 없이 recall↑ (어휘 불일치 완화). 1차 결과가 틀리면 잘못된 방향으로 확장(query dri |
| `context_neighbors` | 0 | **1** | 표·목록이 청크 경계에서 잘린 경우 답변 완성도↑. 토큰↑. |
| `context_neighbors` | 0 | **1** | 표·목록이 청크 경계에서 잘린 경우 답변 완성도↑. 토큰↑. |
| `fallback_max_attempts` | 2 | **3** | over-retrieval 방지 (에이전틱 RAG 최대 실패 원인). |
| `answer_length_target` | "normal" | **"long"** | long 은 evidence-rich 답변, 토큰↑. |

## 5. build — Data Build (색인)

코퍼스 폴더 → 문서 → 청크(FTS5) → 벡터 → 지식 그래프 → 위키 페이지. 증분 빌드는 변경 문서만 다시 처리.

| trace 이름 | 단계 | 토글 | 튜닝 | 설정 | 영향 |
|---|---|---|---|---|---|
| `health` | Health 검사 | `health_check` | - | `build_lock_timeout`, `build_lock_stale_s` | 실패(fail) 항목이 있으면 빌드를 시작하지 않아 중간 실패를 예방. warn 은 alerts 로 보고. |
| `providers` | 프로바이더 준비 | `llm_graph`, `community_summary` | - | `llm_provider`, `llm_model`, `llm_roles`, `embed_provider`, `embed_model` | 최초 1회 지연(Ollama 탐지 ~0.8s). 프로바이더가 없으면 LLM 단계는 모두 skipped. |
| `load_corpus, mcp_ingest` | 코퍼스 로드 | `stat_skip`, `incremental` | - | `corpus_dirs` | 파일 수·PDF 파싱에 비례. stat_skip 으로 변경 없는 빌드는 수십 ms. |
| `diff` | 변경 감지 | `incremental` | - | - | 증분 범위를 결정. incremental 끄면 매번 전체 재처리. |
| `chunk_index, build_channel, reindex_fts` | 청킹 · FTS 색인 (채널 fts) | `build_fts`, `fts_trigram` | `chunk_max_chars`, `chunk_overlap_chars`, `chunk_min_chars`, `tokenizer`, `wiki_min_degree` | `chunk_max_chars`, `chunk_overlap_chars` | 청크 크기가 검색 정밀도/컨텍스트 토큰/벡터 메모리를 좌우. 값 변경 시 전체 리빌드(세 채널 모두). |
| `embed, doc_vectors` | 벡터 임베딩 (채널 vector) | `embed`, `idf_refit_incremental`, `embed_adaptive` | `embed_dim`, `embed_batch`, `hash_ngram_weight` | `embed_provider`, `embed_model`, `embed_dim`, `embed_batch` | 벡터 채널 recall 의 원천. hash 는 오프라인·비의미적, voyage/st 는 의미 검색. 메모리 = 청크×dim×4B. |
| `graph_build, rule_extract, llm_extract, degrees, doc_refs, communities, community_summary` | 그래프 추출 (채널 graph) | `rule_graph`, `llm_graph`, `communities`, `community_summary`, `incremental_communities`, `explicit_relations` | `cooccur_window`, `cooccur_scale`, `cooccur_min_w`, `dates_per_chunk`, `amounts_per_chunk`, `community_iters` 외 3 | `llm_graph_budget`, `llm_graph_min_chars`, `llm_roles.extract`, `llm_roles.summary` | 그래프 채널·위키·엔티티 상세의 원천. 규칙은 무료·결정적, LLM 은 청크당 1회 호출(토큰↑, budget 으로 제한; 실패는 llm_report). |
| `wiki_pages` | 위키 페이지 | `wiki_pages`, `wiki_full_rewrite` | - | `wiki_dir` | 사람이 편집한 `## 편집 노트` 가 다음 빌드에 overlay 문서로 재색인됨(HITL 진화 경로). |
| `prune, verify` | 정리 · 유지보수 | `fts_optimize` | - | - | 검색 속도 유지·DB 비대 방지. |
| `warm_cache, precompute` | 캐시 예열 | `warm_cache` | - | - | 빌드 직후 첫 질의 지연 제거(3만 청크면 수 초). |

## 6. evolve — Self-Evolving (제안 → 적용 → 검증)

질의 캡처·👍/👎 피드백·LLM 리뷰가 데이터 수정 제안을 만들고, 사람이 승인하면 스냅샷 → 적용 → 재색인 → 회귀 평가 → 승격/롤백.

| trace 이름 | 단계 | 토글 | 튜닝 | 설정 | 영향 |
|---|---|---|---|---|---|
| `evolve_capture` | 제안 생성 | `evolve_capture` | - | `llm_roles.review` | 데이터 전용 제안(코드/프롬프트 변경 없음). |
| `hitl` | 검토 (HITL) | `evolve_auto_apply` | - | `evolve_min_confidence` | 안전장치: 기본은 수동 승인. |
| `snapshot` | 스냅샷 | - | - | - | 롤백 가능성 확보. |
| `build` | 적용 · 재색인 | - | - | - | 빌드 흐름을 재사용 (변경 문서만). |
| `eval, run` | 회귀 평가 · 승격/롤백 | - | - | - | 품질 저하 자동 차단. |
| `evolution_log` | 이력 | - | - | - | 감사 추적. |

## 7. watch — Auto Build (워처)

주기적으로 코퍼스를 stat 스캔해 변경이 있을 때만 증분 빌드. 매일 추가되는 문서를 자동 반영.

| trace 이름 | 단계 | 토글 | 튜닝 | 설정 | 영향 |
|---|---|---|---|---|---|
| `scan` | stat 스캔 | `auto_build` | - | `auto_build_interval` | 변경 없으면 비용 거의 0. |
| `build` | 증분 빌드 | `incremental`, `stat_skip` | - | - | 빌드 흐름 참조. |

## 9. LLM 에게 최적화를 묻는 법

```bat
python -m llmwiki query "실제로 개선하고 싶은 질문" --analyze     :: 분석 모드로 한 번 돌리고
python -m llmwiki optimize last --out bundle.md                  :: 가이드+설정+실측을 한 파일로
```

`bundle.md` 를 통째로 LLM 에게 주고 이렇게 묻는다.

> 첨부한 자료에는 (1) 이 검색 엔진의 단계별 조절 손잡이 설명, (2) 지금 설정값, (3) 질의 한 건의 단계별 실측이 들어 있다.
> **품질**(또는 속도/토큰)을 올리고 싶다. 자료에 적힌 수치만 근거로, 바꿀 설정을 효과가 큰 순서로 5개까지 제안하라.
> 각 제안에 (a) 어떤 수치가 문제인지 (b) 어떤 키를 어떤 값으로 (c) 기대 효과와 부작용 을 적어라. 자료에 없는 것은 지어내지 마라.

Web UI 에서는 Ask 의 **📊 상세 분석 리포트 → 🧠 LLM 소견 받기** 가 같은 일을 서버 안에서 해 준다
(그 소견은 `소견 → 제안 등록` 으로 Evolve 의 HITL 제안으로 넘길 수 있다).

제안을 받은 뒤에는 **반드시 회귀 평가로 확인한다.** 숫자가 좋아졌는지 보지 않고 적용하면 다른 질문이 나빠질 수 있다.

```bat
python -m llmwiki trial run --name before
python -m llmwiki tuning set <키>=<값>
python -m llmwiki trial run --name after
python -m llmwiki trial compare before after        :: hit@k · MRR · term recall · ms · tokens 비교
```


---
생성: `python -m llmwiki arch doc` · 2026-09-19 13:24
