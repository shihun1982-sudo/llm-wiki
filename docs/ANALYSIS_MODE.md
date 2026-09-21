# ANALYSIS MODE — 상세 분석 모드: 질의 한 건의 모든 단계를 한 장의 리포트로 (품질 · 속도 · 토큰)

> 대상: 검색 품질/지연/토큰량이 마음에 들지 않을 때 "어느 단계를 어떤 설정으로 고쳐야 하나"를 알아내려는 운영자, 그리고 그 리포트를 받아 튜닝을 제안할 LLM. 자동/기대 결과 포렌식([FORENSIC.md](FORENSIC.md))이 "왜 이 근거가 빠졌나"에 답한다면, 이 모드는 **한 질의의 전 단계 실행 기록·설정·수치를 빠짐없이 문서화**해 세 관점(품질·속도·토큰)에서 조절점을 짚는다. 2026-09-15 구현, 설계 근거 [IMPLEMENTATION_PLAN_0914.md](history/2026-09-14/IMPLEMENTATION_PLAN_0914.md) §10, 검증 [VERIFICATION_0915.md](history/2026-09-15/VERIFICATION_0915.md) §8.

## 0. 한 장 요약

```
토글 analysis_mode ON  →  query (CLI/Web/MCP)  →  debug_level 2 로 실행 (단계별 debug + 프롬프트/응답 샘플)
                                               →  logs/analysis/req_<request_id>.md (+ .json)   ← LLM 에게 그대로 첨부
                                               →  결과 result.analysis (경로 · 렌즈별 상위 소견)   ← CLI 출력 / Web Ask 배너 / MCP wiki_analysis
이미 실행한 요청도:  analyze <request_id|last> [--focus quality|speed|tokens] [--print] [--out …]   (요약 수준; 토글 없이 실행된 요청은 샘플이 없다)
```

| 어디서 | 켜기 | 보기 |
|---|---|---|
| CLI | `query "…" --analyze [--focus tokens] [--print-analysis]` 또는 `config set analysis_mode=true` | 출력 끝에 `📊 분석 리포트: logs/analysis/req_123.md` + 렌즈별 상위 소견. `analyze last --print` 로 전문 |
| Web | 사이드바 토글 **analysis_mode**(Query · 근거/답변 그룹) 켜고 질의 | Ask 결과 상단 배너 → "전문 보기" / 버튼 **📊 상세 분석 리포트**(토글 없이도 요약 수준으로 생성) → 초점 선택 · md 열기 · 다운로드 · 클립보드 복사 |
| MCP | 도구 `wiki_query` 뒤 `wiki_analysis(request_id, focus)` | 마크다운 텍스트 + structuredContent(경로·상위 소견) |
| API | `POST /api/query {"overrides": {"analysis_mode": true}}` · `GET /api/analysis?request_id=..&focus=..[&format=md][&download=1][&full=1]` | JSON(summary·markdown) 또는 text/markdown |

권한: `analyze`/`GET /api/analysis`/`wiki_analysis` 는 모두 **read** 등급 — 게스트(viewer)도 쓴다(리포트는 로그 폴더에 쓰이지만 색인·설정은 바꾸지 않는다).

## 1. 리포트 구성 (`logs/analysis/req_<id>.md`)

| § | 내용 | 원천 |
|---|---|---|
| 0 요약 | 답변 모드·모델·근거 판정·groundedness·claim·fallback·총 지연(LLM 대기)·토큰·컨텍스트·LLM 실패 보고 + **세 렌즈의 상위 소견 3개씩** | result, trace.summary |
| 1 설정 스냅샷 | 켜진/꺼진 토글, **기본값과 다른 tuning.json 값**, 핵심 설정(top_k_*, rerank_candidates, context_max_chars, answer_max_tokens, embed_*, llm_timeout/retries), 역할별 모델, 융합/리랭크 방식, 채널 가중, 라운드 | result.config, Settings, tuning |
| 2 단계 타임라인 | 단계별 ms·self·%·실행/스킵 사유·LLM 호출·토큰·SQL·오류 표 + **LLM 호출 표**(역할·모델·입출력 토큰·ms·프롬프트 크기·오류) | trace(프로파일 트리) |
| 3 검색 상세 | 라우터(kind·가중·키워드·엔티티) · 규칙 확장(fired/alt/related/exclude/seeds) · 시간 범위 · LLM 확장 · pin · **채널별 표**(fts/rules/alt/related/vector/graph/doc_vector/external_rag: meta + 상위 id) · 융합(방식·채널별 후보 수·겹침·상위) · 부스트 통계 · 외부 주입 · **리랭크 전→후 이동** · doc_expand · 컨텍스트(chars·인용·중복 제거·트림·탈락 목록) · fallback 라운드 · **최종 근거 표**(인용·청크·유형·헤딩·채널(순위)·fused·rerank·boosts·외부) | trace 단계 meta/debug, hits_brief(+청크 조회) |
| 4 답변 | 모드·모델·길이·인용, 근거 판정 전체, claim 통계·미지원 문장 예, LLM 실행 보고, 답변 본문 | result |
| 5 품질 렌즈 | 소견(🔴🟠🔵🟢) + 상세 + **조절점(토글/튜닝/설정 키 = 현재값)** + 근거 | §2~§4 수치 규칙 (§2 아래) |
| 6 속도 렌즈 | 총/LLM/검색 시간, 느린 단계 Top 과 단계별 조절점·기대 효과, fallback 비용, LLM 오류/재시도 | trace |
| 7 토큰 렌즈 | 호출별 입출력 토큰, 컨텍스트 자수≈토큰, 비중 큰 호출의 조절점, 상한 근접 경고, 절약량 | trace counters·meta |
| 8 자동 포렌식 소견 | `forensic.diagnose` findings/suggestions (판정과 무관하게 항상 실행) | forensic.py |
| 9 LLM 에게 넘길 때 | 값 바꾸는 명령(config set/tuning set)·검증 절차(재실행 비교, trial) + **복사용 지시문** | 고정 |
| 부록 A | 프롬프트/응답 샘플 (debug_level 2 — analysis_mode 로 실행했을 때만) | trace.samples |
| 부록 B | 단계별 meta/debug 원 데이터 요약 | trace |

같은 내용의 `req_<id>.json` 이 함께 저장된다(프로그램/LLM 도구 입력용, `GET /api/analysis?…&full=1` 과 동일).

## 2. 렌즈 규칙 (무엇을 보고 무엇을 짚는가)

| 렌즈 | 규칙(트리거) | 조절점 |
|---|---|---|
| 품질 | 근거 판정 weak/insufficient (reasons) | `fallback_loop` `top_k_*` `evidence_min_cover/chars` `query_expand` `query_rules` |
| 품질 | insufficient_data 응답 | `query_rules`(동의어) `fallback_levels` `external_rag` · 코퍼스 갭 |
| 품질 | groundedness < `claim_min_groundedness` (미지원 문장 n) | `claim_policy` `claim_check_llm` `answer_refine` `answer_length_target` `context_max_chars` `evidence_compress` |
| 품질 | 1위 근거가 단일 채널 | `query_rules` `query_expand` `channel_w_*` `embed_provider` |
| 품질 | FTS∩vector 겹침 0 | `embed_provider/model` `fusion_method` `rrf_k` `fusion_multi_bonus` |
| 품질 | 그래프 시드 없음 / 외부 소스 오류 | `graph_hops` `top_k_graph` · alias 제안 / `external_rag` |
| 품질 | 리랭커가 로컬로 대체됨 / 리랭크가 순위를 크게 바꿈 | `rerank_llm` `rerank_method` `llm_roles.rerank` `rerank_url` / `rerank_candidates` `rerank_w_*` |
| 품질 | 중복 제거 ≥3, doc_expand 후보 있으나 추가 0, 보조 청크가 상한으로 탈락 | `dedupe_*` `chunk_overlap_chars` / `doc_expand_min_score/max_chunks` / `context_max_chars` `context_trim` |
| 품질 | fallback 전부 개선 없음 | 코퍼스 갭 → 문서 추가·`external_rag` |
| 품질 | (합류) 자동 포렌식 error/warn 소견 | 해당 단계 튜닝 키 |
| 속도 | 총/LLM 대기/검색 시간, 느린 단계 Top(≥15%) | `rerank_llm`·후보/청크 길이, `answer_max_tokens`·effort·모델·컨텍스트, `query_expand`, `evidence_check_llm`, `claim_check_llm`, `fallback_*`, `embed_dim/store_dtype`, `graph_hops`, `external_rag` timeout, `warm_cache` |
| 속도 | fallback 라운드 비용, LLM 오류/재시도 | `fallback_max_attempts` `fallback_latency_ms` / `llm_timeout` `llm_retries` |
| 토큰 | 호출별 비중 ≥20%(≥50% 경고) | answer: `context_max_chars` `context_chunk_chars` `top_k_final` `doc_expand_max_chunks` `context_trim` `dedupe_hits` `evidence_compress` `answer_max_tokens` `context_graph_relations` · rerank: `rerank_candidates` `rerank_chunk_chars` · 확장/판정/claim: 해당 토글 |
| 토큰 | 컨텍스트가 상한의 90% 이상 | `context_max_chars` `top_k_final` `context_chunk_chars` |

각 소견에는 조절점의 **현재값**이 붙는다(예: `rerank_candidates=16`). 규칙은 `llmwiki/analysis.py` 의 `_lens_quality/_lens_speed/_lens_tokens` 에 있고, 조절점 목록은 `architecture.py` 레지스트리(단계 ↔ 튜닝 키)와 합쳐진다.

## 3. 사용 절차

1. **재현**: 문제 질의를 `analysis_mode` 로 실행한다. `python -m llmwiki query "…" --analyze` (Web 은 토글). 캐시 히트면 검색 단계가 없으므로 `query_cache`/`precompute` 를 끄고 다시 실행(리포트가 안내).
2. **읽기**: §0 의 렌즈 상위 소견 → §5~7 의 조절점 → §2/§3 수치로 확인. 사람이 볼 때는 §3.2(융합·리랭크 전후)와 §3.3(최종 근거 표)이 핵심.
3. **LLM 에게**: `req_<id>.md` 를 첨부하고 §9 지시문을 붙인다(초점을 품질/속도/토큰 중 하나로 좁히려면 `--focus`). MCP 클라이언트라면 `wiki_analysis(request_id, focus)` 한 번으로 같은 문서를 받는다.
   - 이 리포트는 **"이번 질의에서 무슨 일이 있었나"** 만 담는다. LLM 이 "그래서 어느 손잡이를 어떻게 돌릴까" 까지 답하려면 전체 구조와 설정 스냅샷이 더 필요하다 — `python -m llmwiki optimize <id|last> --focus <렌즈> --out bundle.md` 가 이 리포트에 **A 설정 스냅샷 · D 손잡이 지도([OPTIMIZATION_GUIDE.md](OPTIMIZATION_GUIDE.md)) · C 지시문**을 묶어 한 파일로 만든다. Web 은 Ask › 📊 상세 분석 리포트 › **📦 최적화 자료 묶음 다운로드**, API 는 `GET /api/optimize/bundle?request_id=<id>&focus=<렌즈>`. 절차는 [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) §7.0.
4. **적용·검증**: 제안된 값을 `tuning set k=v` / `config set k=v`(Web › Settings) 로 바꾸고 같은 질의를 다시 `--analyze` 로 실행해 §2(ms·토큰)·§3(순위)·§0(판정) 변화를 비교. 회귀는 `trial run --name <이름>` → `trial compare baseline <이름>`.
5. **정리**: 문제가 "문서가 없다/표기가 다르다"면 설정 대신 문서 추가·`rules add synonym`·`pin` 을 쓴다(§5 품질 렌즈가 코퍼스 갭을 표시).

## 4. 설정

| 파일 | 키 | 기본 | 뜻 |
|---|---|---|---|
| config.json toggles | `analysis_mode` | off | 켜면 질의가 debug_level 2 로 실행되고 리포트를 저장. 질의당 수십 ms 와 requests 행 크기(샘플 포함)가 늘어나므로 디버깅할 때만 |
| config.json | `debug_level` | 1 | 토글이 꺼진 일반 질의의 상세도. `analyze` 는 이 수준의 trace 로도 리포트를 만든다(샘플 없음) |
| config.json | `keep_requests` | 2000 | requests 보존 수 — 오래된 요청은 `analyze` 할 수 없다 |
| .env / 경로 | `LLMWIKI_LOGS_DIR_PATH` | logs | 리포트는 `<logs_dir>/analysis/req_<id>.md/.json` |
| CLI | `query --analyze --focus … --print-analysis`, `analyze <id|last> --focus --print --out --json` | | |

## 5. 검증 (실측)
`tests/test_analysis.py` 6개: 토글 → debug_level 2·리포트 파일(md/json)·모든 섹션·샘플·타임라인·final; 저장된 요청 분석·focus·last·없는 id·캐시 결과 안내; 약한 질의의 렌즈 소견과 조절점 현재값; CLI `query --analyze`/`analyze last|--print|--out|--json`/종료 코드/read 등급; MCP `wiki_analysis`; Web `/api/query overrides.analysis_mode` → `/api/analysis` json/md/download/full/404. 하네스 행은 [VERIFICATION_0915.md](history/2026-09-15/VERIFICATION_0915.md) §8.

## 6. 문제 해결
| 증상 | 조치 |
|---|---|
| 리포트에 "요약 수준" 이라고 나옴 | 토글 없이 실행된 요청. `analysis_mode` 를 켜고 다시 질의(또는 `query --analyze`) |
| 부록 A(샘플)가 비어 있음 | debug_level 2 로 실행되지 않았거나 LLM 호출이 없는 경로(추출식/캐시) |
| `analyze` 가 request not found | `keep_requests` 초과로 정리됨, 또는 `--no-log`/`record_request=False`(forensic expect 재실행)로 남지 않은 요청 |
| 렌즈 소견이 "품질 소견 없음" 인데 답이 이상함 | 기대 문서를 알고 있다면 `forensic expect --doc …` 가 정확하다(단계별 탈락 지점). 리포트 §3.3 최종 근거 표와 §4 claim 미지원 문장을 직접 본다 |
| Web 에서 md 가 열리지 않음 | `GET /api/analysis?request_id=..&format=md` 를 직접 열어 본다. 게스트 차단(anonymous_role="") 이면 로그인 |

## 7. 구현 파일
| 파일 | 내용 |
|---|---|
| `llmwiki/analysis.py` | `build_report`(trace/result → dict) · 렌즈 `_lens_quality/_lens_speed/_lens_tokens` · `render_markdown` · `save_report` · `summarize` · `run_for_result`(질의 직후) · `analyze`(저장된 요청) |
| `llmwiki/query_engine.py` | `analysis_mode` 면 debug 2 로 Profiler 생성, 요청 기록 후 `result["analysis"]` |
| `llmwiki/cli.py` | `analyze` 명령, `query --analyze/--focus/--print-analysis` |
| `llmwiki/web/server.py` `web/static/js/ask.js` `index.html` | `GET /api/analysis`(json/md), Ask 의 📊 버튼·배너 |
| `llmwiki/mcp.py` | 도구 `wiki_analysis` |
| `llmwiki/config.py` `auth.py` | 토글·설명·그룹, `analyze` read 등급 |
