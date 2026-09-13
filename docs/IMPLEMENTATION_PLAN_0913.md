# 구현 계획서 (2026-09-13)

> 분석·판정 근거는 [ANALYSIS_REPORT_0913.md](ANALYSIS_REPORT_0913.md). 이 문서는 **무엇을 어떤 순서로 어떻게 만들지**만 다룬다.
> **상태: 확정·구현 완료 (2026-09-13).** D3 은 "tool 스펙 없음 → 범용 어댑터 + mock" 으로, D14 는 "4096d 기본 유지 + 모든 차원 지원 + float16 옵션 + 캐시" 로 확정. 결과 문서: [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) · [ARCHITECTURE_V3.md](ARCHITECTURE_V3.md) · [CLI_FLOWS.md](CLI_FLOWS.md) · [CORPUS_CONTRACT.md](CORPUS_CONTRACT.md) · `llmwiki_guide.html`.

## 1. 원칙
1. **호환성**: 기존 CLI/Web/MCP 인터페이스와 DB 는 유지·확장한다. 스키마 변경은 `MIGRATIONS`(ALTER ADD COLUMN) + 신규 테이블로만 하고, 기존 색인은 재빌드 없이 열린다(신규 기능은 재빌드 시 채워짐).
2. **모든 신규 동작은 토글 + 튜닝 + 프로파일 단계**를 갖는다: `Toggles` 필드 → CLI `--x/--no-x` 자동 생성, Web 사이드바 자동 노출, `architecture.py` 플로우에 단계 등록, trace 에 stage 기록.
3. **동일 경로**: CLI·Web·MCP·eval·trial 모두 `Pipeline.query/build` 하나를 호출한다.
4. **표준 라이브러리 위주**(porting): 신규 필수 의존성 없음. 선택 의존성(kiwipiepy, pyyaml, sentence-transformers) 은 없으면 폴백.
5. **테스트 우선**: Phase 마다 unittest 추가, mock 프로바이더/mock headless/mock MCP 로 네트워크 없이 통과. 기존 21개 테스트는 계속 통과해야 한다.
6. **문서 동기화**: `arch`/`tuning doc`/TOGGLE_HELP 처럼 코드에서 문서를 생성하는 부분은 코드가 원천이다.

## 2. 신규/변경 파일 구조
```
config.json            (+ log, debug, timezone, openai_*, rerank_*, presets 기본, 신규 토글)
.env                   (+ OPENAI_*, RERANK_API_KEY, MCP 인증, 모든 키 LLMWIKI_* 오버라이드 규칙)
presets.json           NEW  품질/속도/토큰/offline/deep_research 묶음
query_rules.json       NEW  acronym/synonym/alias/related/exclude/compound + 시간 지역
mcp_sources.json       NEW  외부 MCP 소스(Mango 등) 정의
agents.json            NEW  headless agent 명령 템플릿(opencode, claude, codex …)
schemas/               NEW  common.json, issue.json, cl.json, sw_design.json, hw_design.json, coding_rule.json, weekly_report.json, tc_list.json, infer.json, migrations/
prompts/               NEW  answer_guide.md, expand.md, evidence_check.md, claim_check.md, forensic.md, review.md, extract.md, summarize.md, rerank.md
logs/                  NEW  (런타임 생성) llmwiki.log, error.log, build.log, query.log
data/mcp_cache/        NEW  MCP 소스에서 가져온 문서(front matter 포함)
setup/sample_corpus_modem/  NEW  합성 모뎀 코퍼스 6유형 + questions.json
setup/schedule_build.ps1|.sh NEW  OS 스케줄러 등록 예시
llmwiki/
  config.py            (+ 신규 Settings/Toggles, 제너릭 env 오버라이드, 파일 경로 레지스트리)
  logging_setup.py     NEW  JSONL 로테이션 로거, request_id 컨텍스트
  providers.py         (+ OpenAICompatLLM/Embedder, RerankAPI, HeadlessAgentLLM, provider 문자열 파서 "headless:opencode")
  headless.py          NEW  subprocess 실행·ndjson 파서·템플릿
  rerankers.py         NEW  rerank 방식 통합(api/llm/ce/local) — retrieval.rerank 에서 위임
  schema.py            NEW  front matter 파서(미니 YAML), 스키마 로더/검증(lint), 유형 추론
  corpus.py            (+ front matter 추출, doc_type/date/id 메타, rename 감지 힌트)
  textutil.py          (+ 스크립트 경계 분리, 복합어 사전, analyze() 통합, kiwi 플러그인 훅)
  timeparse.py         NEW  한국어 상대 시간 → 날짜 범위
  query_rules.py       NEW  규칙 사전 로드·유형별 확장·프로파일
  fusion.py            NEW  rrf/wrrf/minmax/zscore/dbsf + post-boost
  retrieval.py         (+ doc_refs 문서 후보, exclude 페널티, related 리스트, 시간 boost/filter 훅)
  evidence.py          NEW  충분성 판정(휴리스틱/LLM), fallback 단계 정의·예산
  answer.py            (+ 가이드 로딩, 구조화 답변, claim_check, refine, insufficient 응답)
  forensic.py          NEW  trace 진단 규칙, forensics 저장, 집계→제안
  memory.py            NEW  episodes/strength/decay/consolidate, feedback boost
  pins.py              NEW  pins.json 관리 + 주입
  precompute.py        NEW  답변 사전 계산, 문서 카드 임베딩(doc_vector 채널)
  trials.py            NEW  trial 저장/비교/리포트
  presets.py           NEW  preset 로드/적용/diff
  health.py            NEW  health 검사 항목
  buildlock.py         NEW  파일 락
  mcp_client.py        NEW  범용 MCP stdio 클라이언트 + 소스 어댑터 + ingest
  graph_rules.py       (+ id_patterns, link_rules, provenance)
  graph_build.py       (+ explicit 관계, provenance 결합, doc_meta 노드)
  store.py             (+ 테이블: doc_meta, embedding_cache, embed_runs, forensics, episodes, trials, answer_cache, doc_vectors; relations.provenance; verify())
  pipeline.py          (+ health 선행, 적응형 embed 루프, verify, 확장 질의 파이프라인: time_scope→query_rules→…→evidence_check→fallback→claim_check)
  evolve.py            (+ forensic/corpus_gap/query_rule/tuning 제안 kind, decay 연동)
  evalset.py           (+ groundedness/citation/insufficient 지표, request_id)
  tuning.py            (+ 신규 스테이지·파라미터)
  architecture.py      (+ 신규 스테이지/플로우: forensic, trial, precompute)
  cli.py               (+ health, verify, embed, forensic, trial, preset, pin, precompute, rules, schema/lint, mcp-source, logs, memory)
  mcp.py               (+ wiki_related, wiki_doc, wiki_propose, mode/doc_types/time 인자)
  web/server.py        (+ 신규 API, 로그/포렌식/트라이얼/프리셋/테마 엔드포인트)
  web/static/          index.html(7그룹 IA 재배치), app.js(모듈 분할: core.js, ask.js, corpus.js, knowledge.js, quality.js, evolve.js, settings.js, observability.js), style.css, themes/*.css, themes.json
tests/                 test_providers.py, test_schema_rules.py, test_korean.py, test_query_rules_time.py, test_fusion_pins.py, test_evidence_loop.py, test_forensic_memory.py, test_trials_presets.py, test_headless_mcp.py, test_build_robust.py, test_web_api.py (+ 기존 3개 유지)
docs/                  BRINGUP_GUIDE.md, ARCHITECTURE_V3.md(도식), CLI_FLOWS.md(단계별 예), CORPUS_CONTRACT.md, llmwiki_guide.html(인터랙티브), TUNING.md(재생성)
```

## 3. 단계별 계획

### Phase 0 — 기반 (설정·로그·프롬프트·락·health)
| 작업 | 내용 | 검증 |
|---|---|---|
| 0.1 설정 확장 | `Settings` 에 `log_*`, `debug`, `timezone`, `openai_base_url`, `rerank_*`, `embed_batch_max`, `wal_checkpoint_mb`, 파일 경로 레지스트리(`paths.*`); `.env` 의 **모든 키** `LLMWIKI_<KEY>` / `LLMWIKI_TOGGLE_<NAME>` 오버라이드; `config show --effective` 로 출처(기본/파일/env) 표시 | test: env 오버라이드 우선순위 |
| 0.2 로깅 | `logging_setup.py`: JSONL 로테이션, `request_id` threading.local, 프로파일러 note/log → 로거 연동, LLM/임베딩/HTTP 호출 로그, CLI `logs tail\|grep --request --since --level` | test: 질의 후 request_id 로 grep 가능 |
| 0.3 프롬프트 외부화 | `prompts/*.md` 로더(mtime 재로드), 기존 하드코딩 SYSTEM 문자열 이관, `answer_guide.md` 초안(§3.22 구조) | test: 파일 수정 → 프롬프트 반영 |
| 0.4 빌드 락 | `buildlock.py`(pid+ts 파일, stale 감지), CLI/서버/watch 공용 | test: 동시 빌드 거부 |
| 0.5 health | `health.py` + CLI `health [--json]`, `build` 선행 실행(`--no-health/--force`), Web 상태 배지 | test: mock 환경에서 항목별 ok/fail |
| 0.6 preset | `presets.json` + `presets.py` + CLI `preset list\|show\|apply\|diff` + 쿼리/eval `--preset` | test: apply 후 토글/튜닝 값 |

### Phase 1 — 프로바이더
| 작업 | 내용 | 검증 |
|---|---|---|
| 1.1 OpenAI-compatible | `OpenAICompatLLM`(chat, json_object 옵션, 재시도, usage), `OpenAICompatEmbedder`(/v1/embeddings 배치), `ping=/v1/models`, 카탈로그·`models` CLI·Web 반영 | test: 로컬 fake HTTP 서버(threading) 로 왕복 |
| 1.2 rerank API | `rerankers.py`: `api(cohere\|voyage 스타일)`, 기존 llm/ce/local 통합, `rerank_method=api`, 설정/키, 프로파일 stage `rerank_api` | test: fake 서버 |
| 1.3 headless agent | `headless.py` + `HeadlessAgentLLM`, `agents.json`(opencode/claude/codex 템플릿), provider 문자열 `headless:<name>`, 파일 첨부, 타임아웃, ndjson/json/text 파서, `models test` 지원 | test: mock 스크립트(python) 가 ndjson 출력 |
| 1.4 임베더 기본값 | `embed_dim` 기본 1024, `auto` 가 Ollama `bge-m3`/`nomic-embed-text` 감지, float16 저장 옵션(`embed_store_dtype`) | test: dtype 왕복 |

### Phase 2 — 빌드 견고성·코퍼스 계약·그래프 provenance·한글
| 작업 | 내용 | 검증 |
|---|---|---|
| 2.1 임베딩 캐시/재개 | `embedding_cache(provider, model, text_sha1, dim, vec)`; embed 루프가 캐시 조회 → 미스만 호출; N 배치마다 commit + `embed_progress`; `embed_runs` 이력; `build status`, `embed report`(coverage 전체/유형별, 실패 목록) | test: 중간 예외 후 재실행 시 남은 것만 |
| 2.2 적응형 배치/WAL/alerts | 실패 시 절반·성공 시 증가, 지연 목표, WAL 임계 체크포인트, `alerts` 수집·로그·Web 배너 | test: 실패 주입 임베더 |
| 2.3 정합성 검증 | `store.verify()` + CLI `build verify`(FTS↔chunks, 임베딩 coverage, 댕글링, 고아, 위키↔엔티티, 커뮤니티 미배정, doc_meta 누락) + `--fix`(prune·재계산); 증분 빌드에서도 위키 stale 페이지 정리; rename 감지(동일 해시 다른 경로 → 메타만 이동) | test: 삭제/rename 시나리오 후 verify 0 |
| 2.4 문서 계약 | `schema.py`(미니 YAML front matter, 스키마 로더, 검증, 추론), `schemas/*.json`, `corpus lint [--strict] [--fix-suggest]`, 빌드 시 `doc_meta` 저장 + FTS 토큰(id/tags/module), `docs.meta` 확장, `CORPUS_CONTRACT.md` | test: 유형별 유효/무효 문서 |
| 2.5 결정적 관계/provenance | `relations.provenance` 컬럼, `rules.json` `id_patterns/link_rules`, front matter `related.*` → explicit, 동일 관계 confidence 결합, graph_search `provenance_w`, `graph --provenance`, 위키/UI 표시 | test: CL→Issue fixes 관계 생성·가중 |
| 2.6 한글 | `analyze()` 통합(스크립트 경계, 숫자+단위, 복합어 사전, 조사, bigram), kiwi 플러그인 훅(`tokenizer=auto\|heuristic\|kiwi`), trigram 폴백 테이블(토글 `fts_trigram`), 질의/색인 동일 경로 | test: `PDCCH디코딩` 검색, kiwi 미설치 폴백 |
| 2.7 MCP 소스 | `mcp_client.py`(stdio JSON-RPC 클라이언트), `mcp_sources.json`, 토글 `mcp_sources`, CLI `mcp-source list\|test\|fetch\|ingest`, ingest → `data/mcp_cache` 문서(front matter) → 일반 색인 | test: mock MCP 서버 스크립트 |
| 2.8 합성 모뎀 코퍼스 | `setup/sample_corpus_modem/`(Issue 10, CL 10, SW 4, HW 3, CodingRule 2, Weekly 4 + questions.json 25문항, 상대시간/ID/약어 질문 포함) | eval 기준선 기록 |

### Phase 3 — 검색 품질
| 작업 | 내용 | 검증 |
|---|---|---|
| 3.1 query_rules | `query_rules.json` + `query_rules.py`(유형별 확장·exclude 페널티·related 보조 리스트·프로파일 전/후·`profile_expansion` 디버그), evolve synonyms 통합, CLI `rules show\|add\|remove\|test "질의"` | test: 유형별 적용 차이 |
| 3.2 시간 파싱 | `timeparse.py`, `time_scope` 단계, `doc_meta.date` boost/filter, 지역 설정 | test: 기준 시각 고정 파싱 20케이스 |
| 3.3 fusion | `fusion.py`(5방식 + post-boost: doc_type/recency/pin/provenance/feedback/multi), 튜닝 `channel_w`, `doc_type_w`, `recency_half_life_days`; CLI `fusion compare` | test: 방식별 결정성, eval 매트릭스 |
| 3.4 doc_refs 활용 | graph_search 문서 단위 후보, 컨텍스트 "관련 문서" 표기, ID 노드 직접 매칭(ISSUE-2041 → 문서) | test: ID 질의 hit@1 |
| 3.5 pins | `pins.json`, `pins.py`, 주입 stage `pins`, CLI `pin add\|remove\|list`, Web 📌 | test: pin 된 문서 항상 근거 포함 |
| 3.6 precompute | `answer_cache`(build_version 키, 영속), `precompute run\|status\|clear`, 빌드 후 자동(토글), 문서 카드 임베딩 `doc_vector` 채널(토글) | test: 캐시 적중, 재빌드 무효화 |

### Phase 4 — 답변·검증·fallback loop·포렌식
| 작업 | 내용 | 검증 |
|---|---|---|
| 4.1 구조화 답변 | `answer_guide.md` 적용, `answer_length_target`, deep 모드(`--mode deep`), 근거 표·미확인 항목 생성, MCP/CLI/Web 출력 형식 | test: mock 답변 구조 검사 |
| 4.2 query_expand 개선 | 역할 `expand` 분리, 분해(`query_decompose`), 한/영/약어 변형, 예산 | test: 대체 질의 별도 리스트 융합 |
| 4.3 evidence_check | 휴리스틱 판정 + LLM 판정(토글), trace 기록 | test: 근거 없는 질의 → insufficient |
| 4.4 fallback loop | `evidence.py` L1~L4 단계, 예산(attempts/token/latency), 각 라운드 trace, 최종 insufficient 응답 | test: 예산 초과 시 중단, L1 에서 회복되는 케이스 |
| 4.5 claim_check | 문장 분해·사실 문장 판별·인용 존재·지원 검증(휴리스틱/LLM)·groundedness·미지원 처리 정책·`answer_refine` | test: 인용 없는 숫자 문장 → unsupported |
| 4.6 evidence_compress / router_llm | 토글, 저비용 역할 | test: 토글 off 시 stage skipped |
| 4.7 forensic | `forensic.py` 진단 규칙, `forensics` 테이블, CLI `forensic <request_id>\|last\|summary`, 자동 실행 토글, 길이 포렌식 | test: FTS 0건 시나리오 진단 |

### Phase 5 — 자가진화·메모리
| 작업 | 내용 | 검증 |
|---|---|---|
| 5.1 제안 확장 | kind `corpus_gap`, `query_rule`, `tuning`(회귀 평가 필수, 자동 적용 제외), `schema`; forensics 집계 → 제안(`evolve review --from forensics`) | test: 반복 insufficient → corpus_gap 제안 |
| 5.2 memory | `episodes`, strength/decay(`memory_half_life_days`), 강화(재사용·피드백), `memory consolidate\|decay\|status`, 미승인 제안 자동 보관, `feedback_boost`(fusion post-boost) | test: 시간 주입으로 감쇠 확인 |
| 5.3 MCP 확장 | `wiki_related`, `wiki_doc`, `wiki_propose`, `wiki_query(mode, doc_types, time)`; 문서 갱신 | test: mcp.handle 왕복 |

### Phase 6 — 평가·trial
| 작업 | 내용 | 검증 |
|---|---|---|
| 6.1 지표 확장 | groundedness, citation_precision, insufficient_rate, fallback_rate, latency p50/p95, tokens, coverage; eval 행 request_id | test: 지표 계산 |
| 6.2 trials | `trials` 테이블, CLI `trial run\|list\|compare\|report`, evolve eval 을 trial 로 기록, 설정 diff | test: 2 trial 비교 Δ |
| 6.3 fusion compare | Phase 3.3 과 trial 연동 | – |

### Phase 7 — Web UI
| 작업 | 내용 | 검증 |
|---|---|---|
| 7.1 IA 재배치 | 7그룹 네비게이션 + 서브탭, 기존 패널 이관, app.js 모듈 분할 | 수동 + `test_web_api.py`(서버 스레드로 API 왕복) |
| 7.2 신규 화면 | Ask(preset 체크박스·deep·포렌식·pin), Corpus(진행률·coverage·lint·MCP·스케줄 안내), Quality(trials 비교·히트맵), Observability(로그 뷰어·request 연결), Settings(query_rules 편집·preset·테마·agents/mcp 설정) | |
| 7.3 테마 | `data-theme`, `themes/*.css`(light/dark/high-contrast/solarized), `themes.json`, 자동/저장 | |
| 7.4 실행 중 알림 | alerts 배너, 빌드 개입 필요 표시 | |

### Phase 8 — 문서·마무리
| 산출물 | 내용 |
|---|---|
| `docs/BRINGUP_GUIDE.md` | 새 환경 포팅: 요구사항, 설치, 설정 파일 6종 채우기, 프로바이더 연결(Anthropic/OpenAI-compat/Ollama/headless), 코퍼스 계약 적용, 첫 빌드, health, 스케줄 등록, 검증(eval/trial), 운영(로그/포렌식/evolve), 문제 해결, 체크리스트 |
| `docs/ARCHITECTURE_V3.md` | 전체 구조 도식(Mermaid), 데이터 모델, 각 플로우(build/query/evolve/watch/trial/forensic) 단계 설명, 토글·튜닝·프로파일 대응표 |
| `docs/CLI_FLOWS.md` | **모든 CLI 명령**을 예제 입력 → 단계별 내부 동작 → 출력 예로 설명 |
| `docs/CORPUS_CONTRACT.md` | 유형별 스키마·예시 문서·lint 규칙·schema_version 마이그레이션 |
| `docs/llmwiki_guide.html` | 인터랙티브 단일 HTML: 구조도(클릭 → 설명), 플로우 스테퍼(단계별 예제 재생), CLI 카탈로그(필터/검색), 설정 파일 탐색기, 테마 |
| README/INSTALL/TUNING 갱신, `arch`·`tuning doc` 재생성, 전체 테스트 통과 |

## 4. 신규 토글·튜닝 요약 (이름 확정용)
- 토글(query): `time_scope`, `query_rules`, `query_expand`, `query_decompose`, `evidence_check`, `evidence_check_llm`, `fallback_loop`, `claim_check`, `claim_check_llm`, `answer_refine`, `evidence_compress`, `router_llm`, `pins`, `precompute`, `doc_vector`, `feedback_boost`, `forensic_auto`, `mcp_sources`
- 토글(build): `health_check`, `embed_adaptive`, `fts_trigram`, `schema_lint`, `explicit_relations`, `precompute_after_build`, `verify_after_build`
- 토글(evolve): `memory_decay`, `evolve_from_forensics` (+ 기존 `evolve_capture`, `evolve_auto_apply`)
- 튜닝(신규 스테이지 `time_scope`, `query_rules`, `evidence`, `fallback`, `claim`, `forensic`, `memory`, `fusion` 확장): `time_mode`, `recency_half_life_days`, `syn_w`, `related_w`, `exclude_penalty`, `fusion_method`(확장), `channel_w_*`, `doc_type_w`, `provenance_w_*`, `evidence_min_score`, `evidence_min_channels`, `fallback_max_attempts`, `fallback_token_budget`, `fallback_latency_ms`, `claim_support_min`, `claim_policy(mark|drop|refine)`, `answer_length_target`, `memory_half_life_days`, `memory_archive_strength`, `feedback_boost_w`, `embed_batch_max`, `embed_batch_target_ms`, `wal_checkpoint_mb`, `embed_commit_every`, `tokenizer`, `wiki_min_degree`

## 5. 리스크와 대응
| 리스크 | 대응 |
|---|---|
| app.js 대규모 재구성 중 기존 기능 회귀 | 패널 단위 이관 + API 계약 유지 + `test_web_api.py` 로 엔드포인트 회귀 검사 |
| LLM 개입 단계가 많아져 지연/토큰 증가 | 모든 단계 기본값은 "저비용 휴리스틱 on, LLM 판정 off", preset `quality` 에서만 LLM 판정 on, 예산 상한 |
| Mango MCP / opencode 실제 스펙 불일치 | 어댑터 매핑 파일로 흡수, mock 으로 테스트, 첫 실행 로그로 보정 |
| front matter 없는 기존 문서 | 경로/파일명 추론 + lint 리포트로 점진 적용, 없어도 기존 검색은 동작 |
| co_occurs 관계 폭증(5천 문서) | `cooccur_min_w` 상향·청크당 상한 튜닝 기본값 조정, 합성 코퍼스 ×10 스케일 테스트 |
| 스키마 마이그레이션 | 신규 컬럼/테이블만 추가, `build verify` 가 구버전 DB 를 감지해 안내 |

## 6. 진행 방식
- Phase 순서대로 구현하고, 각 Phase 끝에 테스트 결과와 변경 요약을 보고한다. 큰 설계 변경이 필요해지면 그 시점에 확인을 받는다.
- 합성 모뎀 코퍼스로 Phase 2 이후 매 Phase 의 eval/trial 기준선을 기록해 회귀를 감시한다.
- 최종 산출: 코드 + 테스트 + 문서 5종 + 인터랙티브 HTML.

## 7. 확정 요청
[ANALYSIS_REPORT_0913.md §5](ANALYSIS_REPORT_0913.md#5-확인이-필요한-결정-사항-decision-points) 의 **D1~D15** 에 대해 "권장안대로" 또는 항목별 변경 의견을 주시면 그대로 착수합니다. 특히 D3(Mango MCP tool 스펙), D4(pin/precompute 의미), D6(Web UI 전면 재배치) 은 결과물 형태를 크게 바꾸므로 확인이 필요합니다.
