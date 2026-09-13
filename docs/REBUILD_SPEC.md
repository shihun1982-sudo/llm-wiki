# LLM Wiki RAG — 재구현 상세 사양서 (Rebuild Specification)

> 목적: 이 문서만으로 다른 LLM/개발자가 **현재 시스템과 동일한 동작**을 재구현할 수 있도록 모든 구성 요소·데이터 구조·알고리즘·인터페이스를 명세한다.
> 기준 코드: `llmwiki/` 약 7,800 라인 (Python 3.7 호환, 표준 라이브러리 + numpy + pypdf). 검증 상태: 단위 테스트 21개 통과, 실습 코퍼스 29문서.
> 관련 문서: [ARCHITECTURE_V2.md](ARCHITECTURE_V2.md)(구조·요청 대응), [TUNING.md](TUNING.md)(튜닝 82개 전체 표), [REQUESTS_AND_TRENDS.md](REQUESTS_AND_TRENDS.md)(프롬프트 원문 D절).

---

## 0. 한 줄 요약과 제약

로컬 문서(.md .txt .csv .html .pdf) 를 색인해 **FTS5(BM25) + 벡터 + 지식 그래프(LightRAG 식 이중 검색)** 로 하이브리드 검색하고, LLM(인용 강제) 또는 추출식으로 답하며, 질의 로그/피드백으로 **자가 진화(제안 → HITL 승인 → 스냅샷 → 적용 → 회귀 평가 → 롤백)** 하는 위키형 RAG. 모든 단계는 **토글(26)**, **튜닝 파라미터(82)**, **단계별 프로파일 trace** 를 가지며, CLI = Web API = Web UI 기능 집합이 동일하다.

제약 조건 (반드시 지킬 것):
- Python 3.7+ 에서 동작 (walrus/`dict |` 금지, `from __future__ import annotations`). 필수 패키지: `numpy`, `pypdf`. 선택: `anthropic`, `sentence-transformers`.
- 외부 API 키가 없어도 **완전히 동작**해야 한다 (hash 임베딩 + 규칙 그래프 + 추출식 답변 + 로컬 리랭크).
- 단일 SQLite 파일(WAL) 에 모든 상태 저장. 웹 서버는 `http.server` 표준 라이브러리.
- Windows 경로/한글 파일명/UTF-8 콘솔(`PYTHONIOENCODING=utf-8`) 고려.

---

## 1. 저장소 구조

```
llmwiki/
  __init__.py  __main__.py(cli.main)
  config.py        Settings/Toggles dataclass, config.json·.env 로드, TOGGLE_HELP/SETTING_HELP
  tuning.py        TUNABLES 레지스트리(82), Tuning 클래스, tuning.json 로드/저장, TUNING.md 렌더
  architecture.py  FLOWS(build/query/evolve/watch) 단계 레지스트리 → UI/CLI 공용
  profiler.py      Stage/Profiler, 전역 COUNTERS(sql, llm_calls, tokens, embed)
  textutil.py      토크나이저, 조사 제거, n-gram, FTS 질의식, 키워드, 문장 분리, sha1/해시
  corpus.py        Document/Chunk, 로더(.md .txt .csv .html .pdf), stat_skip, 헤딩 인지 청커, scan_changed
  store.py         SQLite 스키마/마이그레이션, FTS, 임베딩 행렬 캐시, 그래프, 요청/질의 로그, 제안
  providers.py     LLM(None/Mock/AnthropicHTTP/AnthropicSDK/Ollama) + 임베더(Hash/Voyage/Ollama/ST), 역할별 생성, ping, MODEL_CATALOG
  graph_rules.py   규칙 기반 엔티티/관계 추출기 (data/rules.json 사전)
  graph_llm.py     LLM 추출/커뮤니티 요약 프롬프트
  graph_build.py   추출 병합 → degree → doc_refs → 커뮤니티(label propagation) → 요약
  retrieval.py     router, fts_search(tiered/PRF), vector_search, graph_search, rrf_fuse, rerank(llm/cross_encoder/local)
  answer.py        build_context(trim/dedupe/neighbors), generate_answer(llm | extractive)
  wiki.py          엔티티 위키 페이지(+문서 참조, 편집 노트 보존), INDEX
  evolve.py        capture_query, record_feedback, llm_review, apply/reject(스냅샷·회귀평가·롤백), status
  evalset.py       eval/questions.json, hit@k/MRR/term recall
  pipeline.py      Pipeline: build/query/evaluate/graph_export/entity_detail/auto_build_tick/system_info/maintenance
  mcp.py           MCP stdio JSON-RPC 서버 (읽기 전용 도구 4종)
  cli.py           argparse (20 서브커맨드), run_captured(웹 콘솔용)
  web/server.py    ThreadingHTTPServer, JSON API, job 스레드, 워처 스레드
  web/static/      index.html, app.js(바닐라 JS, 빌드 없음), style.css
config.json  tuning.json  .env          사용자 설정 (루트)
data/  llmwiki.sqlite3, rules.json, snapshots/, server.log
wiki/  자동 생성 페이지     eval/questions.json     tests/  unittest 3개 파일
docs/  ARCHITECTURE_V2.md TUNING.md REQUESTS_AND_TRENDS.md TRENDS_REFERENCE.md REBUILD_SPEC.md IMPLEMENTATION_BRIEF.md
setup/ INSTALL.md install.bat/.sh check_env.py config.example.json .env.example requirements-optional.txt sample_corpus/
```

---

## 2. 설정 (config.py)

### 2.1 Settings 필드와 기본값

| 키 | 기본 | 설명 |
|---|---|---|
| corpus_dirs | ["corpus"] | 코퍼스 폴더 목록 (상대 경로는 프로젝트 루트 기준, `;` 구분 문자열도 허용) |
| data_dir / wiki_dir / db_name | data / wiki / llmwiki.sqlite3 | |
| chunk_max_chars / chunk_overlap_chars | 900 / 120 | |
| top_k_fts / top_k_vector / top_k_graph / top_k_final | 12 / 12 / 12 / 8 | |
| graph_hops / rrf_k | 2 / 60 | |
| llm_provider / llm_model / llm_effort / answer_effort / llm_fallbacks | auto / claude-opus-5 / low / medium / True | provider: auto\|anthropic\|ollama\|mock\|none |
| ollama_url / ollama_model | http://localhost:11434 / llama3.1 | |
| embed_provider / embed_model / embed_dim / embed_batch | auto / "" / 4096 / 64 | provider: auto\|hash\|voyage\|ollama\|st |
| llm_roles | {} | `{"rerank": {"provider","model","effort"}, …}` 역할: answer, rerank, extract, summary, review |
| rerank_candidates / rerank_chunk_chars | 16 / 600 | |
| context_max_chars / context_chunk_chars / answer_max_tokens | 9000 / 1200 / 3000 | |
| llm_graph_budget / llm_graph_min_chars | 0 / 80 | |
| query_cache_size / auto_build_interval / debug_level / keep_requests | 200 / 300 / 1 / 2000 | |
| evolve_min_confidence / evolve_low_score_threshold | 0.8 / 0.05 | |

`Settings.role_llm(role)` → `{provider, model, effort}`: llm_roles[role] 의 값이 비면 전역값(effort 는 answer 면 answer_effort, 그 외 llm_effort).

### 2.2 Toggles (26) 와 기본값

빌드: rule_graph=T, llm_graph=F, embed=T, communities=T, community_summary=F, wiki_pages=T, incremental=T, stat_skip=T, idf_refit_incremental=F, incremental_communities=F, wiki_full_rewrite=F, warm_cache=T, fts_optimize=T
질의: fts=T, vector=T, graph=T, router=T, rerank=T, llm_answer=T, rerank_llm=T, query_cache=T, context_trim=T, dedupe_hits=T
진화/시스템: evolve_capture=T, evolve_auto_apply=F, auto_build=F
각 토글의 설명 문자열은 `TOGGLE_HELP` dict (UI 툴팁). CLI 는 모든 토글에 `--<name>` / `--no-<name>` 플래그(언더스코어→하이픈).

### 2.3 로드/저장 규칙
- `load_settings()`: `.env` 를 os.environ 에 주입(기존 값 우선) → config.json 없으면 setup/config.example.json 복사 → JSON → `LLMWIKI_LLM_MODEL/LLM_PROVIDER/EMBED_PROVIDER/EMBED_MODEL/OLLAMA_URL/OLLAMA_MODEL/DEBUG_LEVEL` 환경변수 오버라이드 → `LLMWIKI_CORPUS_DIRS`(`;` 구분). 다른 파일: `LLMWIKI_CONFIG`, `LLMWIKI_ENV_FILE`, `LLMWIKI_TUNING`.
- `save_settings()`: 루트 하위 경로는 상대 경로로 저장(이식성).
- `apply_overrides(settings, dict)`: 토글은 bool 변환("1/true/yes/on"), 필드 타입에 맞게 캐스팅, `llm_roles`(dict 또는 JSON 문자열), 단축 키 `<role>_provider|model|effort` (빈 문자열이면 제거).

---

## 3. 텍스트 유틸 (textutil.py)

- `words(text)`: 정규식 `[0-9]+(?:[.,][0-9]+)*%?|[A-Za-z][A-Za-z0-9\-\+]*|[가-힣]+|[一-龥]+`.
- `strip_josa(tok)`: 한글 토큰 길이≥3 일 때 38개 조사(긴 것 우선: 으로부터, 에서는, …, 은/는/이/가/을/를/의/에/와/과/도/로/만/께/랑/나/며/다) 를 접미에서 제거, 남는 길이≥2 일 때만.
- `normalize_token(t)`: lower, 콤마 제거, strip_josa.
- `tokenize_for_fts(text)`: 각 단어에 대해 [lower, (다르면) normalized, 한글 normalized 길이≥3 이면 `_`+bigram…] 을 공백으로 join → FTS `tokens` 컬럼.
- `fts_query(text, mode)`: 각 단어의 {lower, normalized} 를 중복 제거해 `"tok"` 로 감싸 ` OR ` / ` AND ` 로 join. 빈 질의는 `""`.
- `keywords(text, min_len=2)`: normalized 토큰 중 길이≥2, 불용어(51개: 무엇/어떤/어떻게/…/the/a/of/…/것/수/등/…) 제외, 순서 유지 중복 제거.
- `sentences(text)`: `(?<=[.!?。])\s+|\n+` 로 분리.
- `sha1`, `stable_hash(s, mod)` = md5 앞 8 hex → int % mod.

---

## 4. 코퍼스 로더와 청커 (corpus.py)

- `Document(doc_id, path, title, text, kind, hash, meta, mtime, size, skipped)`; `doc_id = <루트 폴더명>/<루트 기준 상대경로('/' 구분)>`.
- 로더: `.pdf` → pypdf 페이지별 `\n\n[page n]\n텍스트`; `.html/.htm` → script/style 제거, `<h1..6>` → `#`×n, 블록 태그 → 줄바꿈, 태그 제거, 엔티티 unescape; `.csv` → `# 파일명`, `컬럼: a, b`, 행마다 `- a=v; b=v`; `.md/.txt` → 그대로. 빈 텍스트는 제외. 제목 = 첫 `#` 헤딩 또는 12자 넘는 첫 줄.
- `iter_corpus(dirs, known=None, stats=None)`: 재귀(숨김 폴더 제외, `.claude` 허용), 정렬. `known[doc_id]={hash,mtime,size,title,kind}` 가 있고 mtime(1e-6 이내)·size 가 같으면 파일을 **열지 않고** `skipped=True` 스텁(text="") 반환. stats 에 files_seen/unsupported/skipped_stat/read/empty/read_ms_by_kind/slowest(5)/missing_dirs.
- `scan_changed(dirs, known)`: stat 만으로 changed/removed 목록.
- `chunk_document(doc, max_chars=900, overlap=120, min_chars=20)`: 줄 단위로 `#{1,6}` 헤딩을 만나면 섹션 분리, 헤딩 경로 `title > H1 > H2` (레벨에 따라 스택 자름). 섹션이 max 를 넘으면 문단(`\n\s*\n` 구분자 포함 분할) 을 누적하다가 max 를 넘기 직전에 조각을 내보내고, 다음 조각은 이전 조각 끝 `overlap` 글자(오프셋 보정)를 앞에 붙임; 누적 길이가 `max×1.6` 을 넘으면 `max` 단위로 강제 분할(다음 시작은 `max−overlap`). `min_chars` 미만(strip 후)은 버림. `Chunk(chunk_id=doc_id#n, ordinal, heading, text, start, end)`.

---

## 5. 저장소 (store.py) — SQLite 스키마

```sql
docs(doc_id PK, path, title, kind, hash, meta JSON, n_chunks, built_at, mtime, size)
chunks(chunk_id PK, doc_id, ordinal, heading, text, start, end)  + idx(doc_id)
chunks_fts  FTS5(chunk_id UNINDEXED, doc_id UNINDEXED, heading, body, tokens, tokenize='unicode61')
embeddings(chunk_id PK, provider, dim, vec BLOB float32)
entities(entity_id PK, name, type, description, aliases JSON, source, confidence, community, degree,
         doc_refs JSON, n_docs, n_mentions)
entities_fts FTS5(entity_id UNINDEXED, name, aliases, description)
relations(rel_id PK = src|rel|dst|chunk_id, src, dst, rel, description, weight, source, confidence, chunk_id) + idx(src), idx(dst)
mentions(entity_id, chunk_id, doc_id, count, source, PK(entity_id, chunk_id)) + idx(chunk_id)
communities(community PK, size, top_entities JSON, summary, source)
query_log(id, ts, query, config JSON, top_chunks JSON, answer, scores JSON, trace JSON, feedback, note)
proposals(id, ts, kind, payload JSON, reason, confidence, status, origin, applied_at, eval_before, eval_after)
evolution_log(id, ts, proposal_id, action, detail JSON, checksum)
synonyms(term, expansion, source, PK(term, expansion))
kv(k PK, v JSON)          -- build_version, hash_idf, last_build
requests(id, ts, kind, summary, ms, llm_calls, input_tokens, output_tokens, sql_count, debug_level,
         config JSON, result JSON, trace JSON, error, origin) + idx(kind,id)
```
- 마이그레이션: 시작 시 `PRAGMA table_info` 로 없는 컬럼 `ALTER TABLE ADD COLUMN` (docs.mtime/size, entities.doc_refs/n_docs/n_mentions).
- `PRAGMA journal_mode=WAL`, `check_same_thread=False`, `set_trace_callback` 으로 SQL 문 수를 전역 카운터에 누적.
- 핵심 메서드: `doc_stats()`, `delete_doc(doc_id)`(청크·fts·임베딩·멘션·관계 삭제), `upsert_doc(doc, chunks, tokens_fn)`, `fts_search(match, k, weights=(2.0,1.0,1.5), snippet_tokens=18)` → `bm25(chunks_fts,0,0,wh,wb,wt)` 오름차순, 반환 점수는 부호 반전(양수), `snippet(body)`; `neighbor_chunks(chunk_id, n)`; `put_embeddings/missing_embeddings/vector_matrix(provider)`(build_version 별 캐시, `last_vec_load_ms`); `upsert_entity`(기존 행과 별칭 합집합, 긴 description 유지, source 연결 `rule+llm`, confidence max, **doc_refs/n_docs/n_mentions 보존**); `add_mention/add_relation`; `entity_index()`(id, name, type, degree, names(lower 별칭 목록) — build_version 캐시); `entity_fts(match,k)` = `bm25(entities_fts,0,3,2,0.5)`; `neighbors(ids)`; `chunks_for_entities(ids, limit)` → `(chunk_id, n_distinct_entities + 0.1*sum(count))` 정렬 n desc, c desc; `update_degrees()`(단일 UPDATE 서브쿼리); `refresh_doc_refs(entity_ids|None)` → mentions 를 (entity, doc) 로 집계해 `[{doc_id, mentions, chunks, first_chunk}]` 상위 50개, n_docs, n_mentions 저장; `entities_for_chunks(chunk_ids)`(멘션·관계 양끝); `prune_orphan_entities()`(멘션도 관계도 없고 source 에 evolve 없는 것), `prune_embeddings(keep_provider)`, `prune_dangling()`; `fts_optimize()`(`INSERT INTO x(x) VALUES('optimize')` + `PRAGMA optimize`), `wal_checkpoint(TRUNCATE)`; `log_request(kind, summary, trace, result, config, error, origin, keep)`(id%50==0 마다 오래된 행 삭제), `requests(kind, limit)`, `get_request(id)`, `request_series(kind, limit)`; `log_query/queries/get_query/set_feedback`; `add_proposal`(같은 kind+payload 가 proposed/applied 면 기존 id), `proposals/get_proposal/set_proposal_status/log_evolution/evolution_log`; `synonyms()/add_synonym`; `stats()`.

---

## 6. 프로바이더 (providers.py)

- `BaseLLM.complete(system, user, max_tokens, effort, json_mode)` 는 래퍼: 전역 카운터 llm_calls/입출력 토큰 증가, `self.stats` 누적, 결과에 `provider`, `prompt_chars` 추가 후 `_complete()` 반환 `{text, usage{input_tokens,output_tokens}, ms, model}`. `ping()`, `describe()`.
- `NoneLLM`(available=False). `MockLLM`(결정적): system 의 `TASK=extract` → 대문자 단어/한글 조직명 최대 12개 엔티티 + 인접 related_to 관계 JSON; `TASK=rerank` → 입력 순서 유지 `{"ranking":[…]}`; `TASK=summarize` → `(mock summary) …`; `TASK=rewrite` → `{"queries":[q+" 관련 내용", q 상세],"keywords":[]}`; 기타 → `(mock answer) … [C1] [C2]`.
- `AnthropicHTTPLLM`: urllib POST `https://api.anthropic.com/v1/messages`, 헤더 `x-api-key`, `anthropic-version: 2023-06-01`, body `{model, max_tokens, system, messages:[user], output_config:{effort}}`; 모델이 `claude-opus-5`/`claude-fable` 로 시작하고 llm_fallbacks 면 `anthropic-beta: server-side-fallback-2026-07-01` + `fallbacks:"default"`; `stop_reason==refusal` → LLMError; 408/409/429/5xx·네트워크 오류 3회 백오프(1.5s×n). `ping()` = GET `/v1/models?limit=100` 로 모델 존재 확인. `AnthropicSDKLLM`: 3.9+ 에 `anthropic` 설치·키 있을 때 자동 선택.
- `OllamaLLM`: `/api/generate`(stream false, num_predict, json_mode 면 format=json); 가용성 probe `/api/tags` 0.4s 타임아웃, **URL 별 30초 캐시**(`_OLLAMA_PROBE`).
- `make_llm(settings, role=None)`: role 있으면 `role_llm(role)`; provider 해석: none→NoneLLM, mock→MockLLM, anthropic|auto→Anthropic(키 없고 auto 면 다음), ollama|auto→Ollama(provider=ollama 면 역할 모델명, auto 면 ollama_model; 응답 없고 auto 면 NoneLLM).
- 임베더: `BaseEmbedder.embed()` 래퍼(카운터) → `_embed()`; `ping()` 은 1문장 임베딩. `HashEmbedder(dim, ngram_weight=0.5)`: 특성 = 단어 `w:tok`(+1), 한글 토큰 문자 2·3-gram `g:`(+ngram_weight), 영문 길이>4 3-gram(+0.3); 벡터값 `1+log(count)`, 저장된 IDF `log((N+1)/(df+1))+1` 곱, L2 정규화. `fit_idf(texts)`. `VoyageEmbedder`(POST /v1/embeddings, 64 배치, 정규화), `OllamaEmbedder`(/api/embed, 32 배치), `STEmbedder`(sentence-transformers, normalize). `make_embedder`: voyage|auto(키 있을 때) → st → ollama → hash(`hash_ngram_weight` 튜닝).
- `MODEL_CATALOG`: llm{anthropic:[claude-fable-5-1, claude-opus-5, claude-sonnet-5, claude-haiku-4-5-20251001], ollama:[…]}, embed{hash, voyage:[voyage-3.5,…], ollama:[nomic-embed-text,…], st:[paraphrase-multilingual-MiniLM-L12-v2, BAAI/bge-m3,…]}, effort[low,medium,high], roles 설명.

---

## 7. 프로파일러 (profiler.py)

- `Profiler(name, debug=0|1|2)`; `with prof.stage(name, **meta) as st:`; `st.note(**)`(항상), `st.debug(**)`(≥1), `st.sample(**)`(≥2), `st.log(msg)`(≥1, 경과 ms 접두), 예외 시 `error` + traceback 꼬리 800자 기록 후 재발생. `prof.skipped(name, reason)` 은 enabled=False, ms=0 노드.
- Stage 는 `offset_ms`(루트 기준), `ms`, `self_ms`, 진입/종료 시점 전역 카운터 차이 `counters{sql, llm_calls, llm_input_tokens, llm_output_tokens, embed_calls, embed_texts}`.
- `finish()` → 트리 JSON + `summary{total_ms, stages[{name,ms,pct,enabled}](depth1), slowest[5], skipped[], errors[], llm{calls,input_tokens,output_tokens,total_tokens}, sql_statements}` + `debug_level`. `flatten_trace(trace)` 유틸.

---

## 8. 튜닝 레지스트리 (tuning.py)

- `TUNABLES`: 82개 `{key, stage, type(int|float|bool|choice|str), default, min, max, choices, desc, impact, example, rebuild, source(tuning|config)}`. 단계: chunk_index, embed, graph_build, router, query_expand, fts_search, vector_search, graph_search, rrf_fuse, rerank, context, answer. 전체 목록·기본값·설명은 **[TUNING.md](TUNING.md)** 를 그대로 사용한다(이 사양의 일부).
- `Tuning.get(key)`: 오버라이드 없으면 default. `set()` 은 타입 캐스팅·범위·choices 검증, 기본값과 같으면 오버라이드 제거. source=config 키는 `set()` 불가(KeyError; Settings 가 진실).
- 파일 `tuning.json`(루트, `_comment` + 오버라이드만). `load_tuning()` 이 모듈 전역 `T` 를 교체; 코드에서는 `from . import tuning; tuning.T.get(...)` 로 매 호출 시 참조. `render_doc(settings)` 가 TUNING.md 표 생성.

---

## 9. 빌드 파이프라인 (pipeline.build)

입력: `full: bool`. `incremental = not full and toggles.incremental`. Profiler("build", debug_level). 전 과정을 RLock 으로 직렬화.

1. **providers** (llm_graph 또는 community_summary 켜졌을 때만 extract/summary 역할 LLM + 임베더 생성; 생성 비용을 stage 로 기록).
2. **load_corpus**: `known = store.doc_stats()` (incremental & stat_skip 일 때만) → `iter_corpus`. 위키 편집 노트(`wiki/*.md` 의 `## 편집 노트` 이하 텍스트, placeholder 제외)를 `wiki/<name>.md` doc_id 의 overlay Document 로 추가. note: docs, kinds, files_seen, read, skipped_by_stat, unsupported, read_ms_by_kind; debug: slowest_files, missing_dirs, empty.
3. **diff**: old=doc_hashes. removed = old − new_ids. changed = full 이면 skipped 아닌 전부, 아니면 skipped 아니고 hash 다른 것. note changed/unchanged/removed/new; debug 목록 50개.
4. **chunk_index**: removed 마다 (삭제 전) `entities_for_chunks` 로 removed_touched 수집 → `delete_doc`. changed 마다 delete_doc → chunk_document(max, overlap, chunk_min_chars) → upsert_doc(tokenize_for_fts). note chunks, avg_chunks_per_doc, largest_doc.
5. **embed** (toggle embed, 그리고 변경이 있거나 full): HashEmbedder 면 `refit = full or idf_refit_incremental or IDF 없음` → refit 시 전체 청크로 fit_idf, kv `hash_idf` 저장, todo=전체; 아니면 todo = missing_embeddings ∪ 변경 문서 청크. 외부 임베더는 missing ∪ 변경. `embed_batch` 단위로 `heading\ntext` 임베딩 → put_embeddings. note embedded, dim, batches, avg_batch_ms, idf_refit, idf_reason. 변경 없으면 `skipped("embed","no changes")`.
6. **graph_build** (rule_graph 또는 llm_graph): full 이면 `clear_graph()` + 전체 청크, 아니면 변경 문서 청크. `build_graph_for_chunks(store, chunks, titles, prof, rules|None, llm_for("extract")|None, use_llm, effort, llm_graph_budget, llm_graph_min_chars)`:
   - touched = 이전 산출물에 연결된 엔티티(`entities_for_chunks`) → `clear_graph_for_chunks`.
   - **rule_extract**: 청크마다 `RuleExtractor.extract_chunk(text, heading, doc_id, title)` → 엔티티 upsert(source "rule"), count>0 이면 mention, 관계는 양끝이 추출 엔티티이거나 rel ∈ {owner, attendee, source, responsible, comments_on} 일 때 add_relation(chunk_id). debug: entity_types, relation_types, slowest_chunks.
   - **llm_extract**(LLM 가용 시): 청크 길이 < min_chars 스킵, budget 초과 스킵; `llm_extract(llm, text, heading, known[:llm_known_entities], effort)` → 이름을 `_resolve`(규칙 별칭 → 기존 id → entity_fts 점수>3) 로 기존 노드에 매핑, source "llm" confidence 0.75, mention count 1, 관계 weight 기본 0.5.
   - 반환 stats + `touched`.
   - `finalize_graph(store, prof, do_communities, llm_for("summary"), community_summary, effort, touched, skip_reason)`: **degrees** → **doc_refs**(touched 또는 전체) → **communities**(`do_communities = communities and (full or incremental_communities)`): `label_propagation(iters=community_iters, seed=7)` — document/date/amount 타입 제외, 무방향 가중 인접, 라벨을 이웃 가중 최다 라벨로 갱신(동률 시 작은 라벨), 변화 없으면 조기 종료, 커뮤니티 0..n 재번호, 제외 타입은 -1. 커뮤니티마다 degree 상위 8개 이름을 `top_entities`, summary 기본 "핵심 엔티티: …", community_summary & LLM & 크기≥3 이면 `llm_summarize_community` 로 대체(source "llm").
   - 변경 없으면 `skipped("graph_build","no changes")`.
7. **wiki_pages**: `write_wiki(store, wiki_dir, prof, prune=only is None, only=touched|None)` — full 또는 wiki_full_rewrite 면 전체(+오래된 페이지 중 편집 노트 없는 것 삭제), 아니면 touched 엔티티 페이지 + INDEX 만. 페이지 형식은 §14.
8. **prune**: orphan entities, dangling, (full & embed) 다른 provider 임베딩 삭제, (full & fts_optimize) FTS optimize, WAL checkpoint.
9. kv `last_build{ts, mode, docs, changed}`, `build_version += 1`, 질의 캐시 clear.
10. **warm_cache**(toggle): vector_matrix 적재 + entity_index 적재. note vectors, matrix_mb, load_ms, entities_indexed.
11. `finally`: trace 완성, `result.ms/tokens/request_id`(requests kind=build, summary "full|incremental build: N docs, C changed, R removed").

반환 `(result, trace)`; result 에 mode, docs, changed, removed, chunks_indexed, embedded, graph{…}, wiki{pages, removed_stale, total_entities}, pruned, build_version, stats.

### 9.1 규칙 추출기 (graph_rules.py)

- 사전 `data/rules.json`(없으면 DEFAULT_RULES 저장): `entities{정규명: {type, aliases[]}}`(기본 50개: org/role/org_unit/product/tech/topic/meeting/event/material/metric/concept), `relation_patterns[{name, regex, rel}]`(owner `**담당**: …`, deadline `**(마감|적용|일정)**: …`, amount `**금액**: …`, attendee `## 참석\n…`, source `출처: …`), `analyst_pattern` `**이름 (m/d)**: "…"`, `decision_pattern` `### D\d. 제목`, `date_patterns`(YYYY.MM.DD 류, YYYY년 M월, M/D), `money_pattern`(억원/조/만원/달러/USD/$), `percent_pattern`, `types_for_cooccur`.
- 컴파일: 별칭 map(lower→정규명), 긴 별칭 우선 단일 정규식(대소문자 무시).
- `extract_chunk`: (1) 문서 노드 `add(title, "document")`(count 0) (2) 사전 엔티티 매칭 → 엔티티(conf .95)+위치 (3) 결정 노드 `"D1 제목[:60] (문서제목[:30])"` type decision, 관계 doc -decides-> D, 결정 블록(다음 `\n### ` 까지) 안의 담당→owner(엔티티), 마감→date 노드 deadline, 금액→amount 노드 amount (4) 청크 전체의 attendee/source/owner(→responsible) 관계(doc→엔티티) (5) 애널리스트 코멘트 → org -comments_on-> doc (6) 날짜 ≤dates_per_chunk, 금액 ≤amounts_per_chunk → doc -mentions_date/mentions_amount-> 노드 (w .3) (7) 공동출현: 위치 정렬 후 i 와 i+1..i+cooccur_window 쌍(양쪽 타입이 cooccur 타입 또는 decision), `w=min(1, cooccur_scale/거리)`, `w<cooccur_min_w` 버림, rel co_occurs (8) doc -mentions-> 엔티티 (w = min(1, .3+.1*count)).
- `entity_id_for(name) = "e:" + lower + 공백→_`.

---

## 10. 질의 파이프라인 (pipeline.query)

Profiler("query", debug). 단계 순서와 정확한 동작:

1. **sync_index**: build_version 이 마지막 본 값과 다르면 임베더·규칙·벡터/엔티티 캐시·질의 캐시 폐기.
2. **providers**: answer/rerank 역할 LLM, 임베더 최초 생성 (이미 있으면 stage 생략).
3. **cache_hit**(query_cache): 키 = sha1(json{q, evolve 제외 토글, top_k*, graph_hops, rrf_k, context/rerank/answer 수치, answer/rerank 모델, 임베더명, build_version, synonyms 수, tuning 오버라이드}). 히트 시 이전 result 복사(cached=True, query_id=None, proposals=[]) + requests 기록 후 반환. LRU(query_cache_size).
4. **router**(toggle): `kws=keywords(q)`, `ent_hits=_match_entities(store,q,8)`(entities_fts OR 검색 + 캐시된 별칭이 질의에 부분 문자열로 포함되면 `5.0+0.2*len`), `n_ent=#(score>router_entity_min)`, `relational = 관계어(관계/영향/왜/원인/누가/담당/연결/관련/비교/차이/어떻게/흐름/결정/이유/배경/who/why/relation/impact/compare/between) 포함`, `has_number`, `short = len(kws)≤router_short_kw`, `strong=#(score≥router_strong_seed)`. w={fts:1, vector:1, graph:router_base_graph}, kind hybrid; short&!relational → (router_kw_fts, router_kw_vector, router_kw_graph), keyword; n_ent≥2 or relational → graph = strong≥2 ? router_rel_graph_strong : router_rel_graph, vector=router_rel_vector, relational; has_number → fts += router_num_fts_bonus; len(kws)≥router_long_kw & !relational → vector += router_sem_vector_bonus, semantic. 반환 kind, weights, keywords, entities[:6], signals.
5. **query_expand**(tuning query_expand, rerank 역할 LLM 가용): 프롬프트 REWRITE_SYSTEM(§13) + `N=n\n질의` → JSON queries 최대 n개(원 질의 제외) → 이후 FTS/벡터를 대체 질의마다 추가 실행해 `fts_alt1…`, `vector_alt1…` 리스트, 가중치 = 원 채널 가중치 × query_expand_w.
6. **fts_search**(toggle fts): 동의어 확장(fts_synonym_expand: term 이 질의에 포함되면 expansions 추가) → kws → `fts_mode=tiered & len(kws)≥2` 면 `"k1" AND "k2"…` 로 먼저 검색, 결과 < max(1, fts_and_min_hits) 또는 < k 면 OR(fts_query) 결과로 보충(중복 제거, k 까지) → 결과 없고 fts_bigram_fallback 이면 tokenize_for_fts 토큰(`_`bigram 또는 길이>1) OR 재검색 → prf_enabled 면 상위 prf_docs 문단의 정규화 토큰 중 (질의 키워드·불용어·숫자 제외) df 내림차순 prf_terms 개를 붙여 OR 재검색 후 보충. BM25 가중치 (fts_w_heading, fts_w_body, fts_w_tokens), snippet fts_snippet_tokens. note hits, tiers(["AND:n","OR:n","bigram:n","PRF:n"]), prf_terms, top5; debug match, keywords, all_hits.
7. **vector_search**(toggle vector): 행렬 캐시 hit/miss(+적재 ms) → 질의 임베딩 → `sims = mat @ qv` → argsort 상위 k 중 `sim > max(0, vector_min_sim)`. note embed_ms, matmul_ms, matrix_mb, n_vectors, top5; debug all_hits, sim_stats.
8. **graph_search**(toggle graph): seeds = 라우터 엔티티(없으면 `_match_entities`) 중 `score > graph_seed_min` 상위 graph_max_seeds. `ent_score=seeds`, `cap=max seed`, deg = entity_index degree. 홉 h=0..hops-1: nbrs=neighbors(frontier), snapshot 점수 기준, mentions_date/amount 관계 제외, `gain = base(양끝 snapshot max) × graph_decay^(h+1) × weight(기본 .1) / deg(other)^graph_hub_exp`; 미방문이면 그대로, 방문이면 ×graph_revisit_factor 누적; `nxt` = 미방문 gain 상위 graph_frontier; `ent_score[o] = min(cap, +gain)`; hop_stats 기록. 청크 점수: `seed_hits=chunks_for_entities(seeds, k*6)` × graph_seed_chunk_w + `ext_hits`(top graph_top_entities 엔티티) × 1 + 관계 gain 상위 graph_rel_bonus_n 의 chunk_id 에 `1/(i+1)+0.2`. 이중 검색 재가중: 후보 상위 max(k*4,30) 의 `cover = 질의 키워드 포함 비율` → `score×(graph_cover_base+cover) + cover×graph_cover_w`. 상위 k. 반환 chunks, entities(15: id,score,name,type,community), relations(25: src/dst 이름, rel, description, weight, chunk_id, gain), seeds. note seeds, expanded_entities, relations_traversed, hops.
9. **rrf_fuse**: 리스트마다 가중치 `weights[name]`(없으면 `name.split('_')[0]`, 기본 1). `fusion_method=rrf`: `fused += w/(rrf_k+rank+1)`; `weighted`: 리스트 점수 min-max 정규화 후 `w×norm/50`. `fusion_multi_bonus × (채널 수−1)` 가산. fused 내림차순, `why=["fts#1","vector#3",…]`. note candidates, multi_source, overlap(채널 쌍 교집합), top6.
10. **rerank**(toggle rerank): 후보 = 상위 rerank_candidates. `rerank_method`: cross_encoder → sentence-transformers CrossEncoder(rerank_ce_model, 프로세스 캐시) `predict([(q, heading\ntext[:rerank_chunk_chars])])` 점수 정렬(실패 시 local 폴백, stage 에 error 기록); auto(rerank_llm 토글 필요)|llm → **rerank_llm**: RERANK_SYSTEM + `질문: q` + `[i] (heading[:60]) text[:rerank_chunk_chars]` → JSON ranking 순서(누락은 뒤에, rerank 점수 = 남은 순위 수), 실패 시 local; **rerank_local**: `cover=키워드 포함 비율`, `consensus = len(ranks)/채널 수`, `rerank = rerank_w_cover×cover + rerank_w_consensus×consensus + rerank_w_length×min(1, len/400) + (헤딩에 키워드 있으면 rerank_heading_bonus)`, (rerank desc, fused desc) 정렬. 결과 = 재정렬 상위 top_k_final + 후보 밖 나머지. debug before/after/moved. 건너뛴 경우 `skipped("rerank_llm", 사유)`.
11. **context**: `build_context(final, chunks, graph|None, context_max_chars, query, trim=context_trim, dedupe=dedupe_hits, chunk_chars=context_chunk_chars, store)`: 상위 context_neighbor_top 청크의 앞/뒤 context_neighbors 개 인접 청크(같은 문서, 후보에 없는 것) 추가(kind neighbor). dedupe: 같은 문서에서 오프셋 겹침 > 60% 이면 제거, 토큰 Jaccard ≥ dedupe_similarity 이면 제거. trim: 청크가 chunk_chars 초과 시 키워드 포함 문장 우선(원래 순서)으로 `" … "` 연결. 블록 `[C{n}] ({doc_id} | {heading[:80]})\n{text}`, 누적 max_chars 초과 시 중단. 그래프 관계 `## 그래프 관계 (참고)` + `- src -[rel]-> dst: desc[:90]` × context_graph_relations. note text_chars, dropped_duplicates, trimmed_chunks, neighbors_added, saved_chars, est_tokens(chars//3), chars, citations.
12. **answer**: llm_answer & answer 역할 LLM 가용 → **answer_llm**: ANSWER_SYSTEM + `## 질문\n q\n\n## 컨텍스트\n ctx`, max_tokens=answer_max_tokens, effort=answer 역할 effort; `[C\d+]` 인용 번호 추출; sample(system, prompt[:12000], response[:6000]); LLMError 시 폴백. **answer_extractive**: 인용 문단들의 문장 중 키워드 포함 수>0, 길이 extractive_min_len..max_len, 점수 `hits + 0.001×len` 내림차순, 앞 40자 중복 제거, extractive_sentences 개 → `(추출식 답변 — LLM 미사용) 질문과 관련된 핵심 문장:\n- 문장 [C#]…`, 없으면 안내 문구.
13. **evolve_capture**(log & evolve_capture): §12.
14. trace 완성 → `query_log`(log 시) → `requests`(kind query). result: query, answer, answer_mode(llm|extractive), cited, model, hits[{chunk_id, scores, ranks, fused, why, rerank, n, in_context, doc_id, heading, text, snippet}](인접 청크는 why=["neighbor"]), route, graph{entities, relations, seeds}, config{toggles, weights, llm, llm_model, rerank_llm, embedder, tuning, alt_queries}, ms, tokens, cached, query_id, proposals, request_id.

---

## 11. 평가 (evalset.py)

`eval/questions.json` = `[{q, expect_docs[부분문자열], expect_terms[]}]`(기본 12문항 자동 생성). 질문마다 query(log=False) → `rank` = top-k 중 chunk_id 에 expect_docs 중 하나가 포함되는 첫 순위; `hit`, `rr=1/rank`, `term_recall` = expect_terms 중 top-k 청크 텍스트에 포함된 비율, `answer_term_recall` = 답변에 포함된 비율. 집계 hit@k, mrr, term_recall, answer_term_recall, total_tokens, avg_ms. `--matrix` 는 fts/vector/graph 조합 6~7개를 순회. eval 결과도 requests(kind eval) 에 기록.

---

## 12. 자가 진화 (evolve.py)

- `capture_query`: (1) graph 켜짐 & 시드 없음 → 키워드(최대 4, 숫자 제외) 가 기존 엔티티 이름의 부분 문자열이면 `alias{entity, alias}`(conf .55) 아니면 코퍼스 FTS 에 존재할 때 `entity{name, type concept}`(conf .45); (2) 상위 fused < evolve_low_score_threshold 이고 단일 채널이면 `synonym{term, expansion(상위 청크 헤딩 키워드)}`(conf .35); (3) LLM 답변에 인용 없음 → `wiki_note{page:"_gaps", note}`(conf .5). evolve_auto_apply 면 conf ≥ evolve_min_confidence 인 것 자동 적용. origin "capture".
- `record_feedback(qid, ±1, note)`: query_log 갱신; 👎 + 정정문 → `wiki_note{page: 질의 관련 페이지, note}` 제안.
- `llm_review`: review 역할 LLM 에 REVIEW_SYSTEM + 기존 엔티티 80개 + 최근 질의 로그 30건 → `proposals[]` 를 add_proposal(origin llm_review).
- `apply_proposal(pid, evaluate=True)`: 이미 applied 면 그대로 반환. 스냅샷 `data/snapshots/p<pid>_<ts>/` 에 DB 파일 복사(WAL checkpoint 후), rules.json, wiki/, config → kind 별 적용: synonym → synonyms 테이블(즉시, 재빌드 없음), alias/entity/relation → rules.json 갱신 + 증분 빌드(그래프 재추출), wiki_note → 위키 페이지 `## 편집 노트` 에 추가 + 증분 빌드(overlay 재색인), chunk_params → config 갱신 + 전체 빌드 → evaluate 면 적용 전/후 eval 비교: `(after.hit@k + after.term_recall) < (before 합)` 이면 스냅샷 복원 + status `rejected_regression`, 아니면 `applied`(before/after 저장); 예외 시 `failed`. 모든 행위는 evolution_log(action, detail, checksum).
- `reject_proposal`, `status(pipe)` → {auto_apply, min_confidence, pending, applied, recent_log, synonyms}.

---

## 13. LLM 프롬프트 (system 문자열, `TASK=` 접두)

EXTRACT_SYSTEM, SUMMARY_SYSTEM, RERANK_SYSTEM, ANSWER_SYSTEM, REVIEW_SYSTEM 원문은 [REQUESTS_AND_TRENDS.md §D](REQUESTS_AND_TRENDS.md) 를 그대로 사용한다. 추가:

```
TASK=rewrite
당신은 검색 질의 재작성기입니다. 사용자의 질문을 사내 문서(회의록·일정·리포트·논문)에서 쓰일 법한 표현으로 바꾼 대체 질의 N개와
핵심 키워드를 JSON 으로만 답하세요. 의미를 바꾸지 말고, 동의어·약어·한/영 표기 변형을 활용하세요.
형식: {"queries":["...","..."],"keywords":["...","..."]}
```
JSON 파싱은 `parse_json`(``` 블록 → 전체 → 첫 `{`~마지막 `}` 순으로 복구).

---

## 14. 위키 페이지 (wiki.py)

파일명 `slug(name)`(비허용 문자 → `_`, 80자). 본문: `# 이름` / `- 유형: … · 출처 · 신뢰도 · 연결수 · 커뮤니티 · 문서수` / `- 별칭:` / `## 설명` / `## 문서 참조`(doc_refs 20개: doc_id, 언급 수, 청크 수, 첫 청크) / `## 관계`(40개: `→|← **rel** [[slug]] (w=, source) — desc[:100]`) / `## 근거 문단`(멘션 8개: chunk_id, heading, 160자) / `## 편집 노트` + 노트 또는 placeholder `(이 섹션은 빌드 시 보존됩니다…)`. INDEX.md: 표(엔티티|유형|연결수|문서수|커뮤니티) + 커뮤니티 목록. date/amount 타입과 degree<min_degree(1) 제외. 편집 노트는 다음 빌드에서 overlay 문서(`wiki/<name>.md`)로 재색인.

---

## 15. 운영 기능

- **워처**: `check_changes()` = scan_changed(stat) + scan_ms; `auto_build_tick()` = 변경/삭제 있으면 build(incremental), `watcher{builds, errors, last_scan, last_result, last_build}`. 서버는 데몬 스레드가 `toggles.auto_build` 일 때 `auto_build_interval`(최소 5s) 마다 tick, 아니면 2초마다 플래그만 확인. CLI `watch [--interval] [--once]`.
- **system_info(target_docs, daily_new, horizon_days)**: index stats, avg_chunks_per_doc, embedding{provider, dim, stored, matrix_mb_now}, projection{current, target, after_horizon}(chunks = docs×avg, vector_matrix_mb = chunks×dim×4/1e6, db_mb = db_bytes/docs×n), build_history(request_series build 30), query_latency{n, avg, p50, p95}, caches(query_cache{size,max,hits,misses}, vector_matrix, entity_index), watcher, files{db_mb, wal_mb}, corpus_dirs, toggles, perf_settings.
- **maintenance(action)**: vacuum | fts_optimize | wal_checkpoint | clear_cache | warm_cache | refresh_doc_refs | purge_requests.
- **reset_index(keep_logs, keep_wiki_notes)**: 색인 테이블(docs, chunks, chunks_fts, embeddings, entities, entities_fts, relations, mentions, communities, kv) 비우고 VACUUM, build_version 유지, 위키 페이지 삭제(편집 노트 있는 것 보존). `build --full` 의 기본 동작(`--no-reset` 으로 해제, `--purge-logs` 로 로그 테이블도 삭제).
- **test_providers(which)**: 역할별 LLM ping + 임베더 ping.

---

## 16. CLI (cli.py) — 서브커맨드 20개

| 명령 | 인자/옵션 |
|---|---|
| build | `--full [--reset/--no-reset] [--purge-logs]` + 공통 |
| query "질문…" | `--k N --no-log` + 공통 |
| eval | `--k 5 --matrix --questions PATH` + 공통 |
| search fts\|vector\|graph "질문" | `--k 8 --json` |
| graph | `--limit 40 --community N --json` |
| entity <이름 또는 e:id> | `--json` |
| evolve status\|apply <id>\|reject <id> [note]\|review\|feedback <qid> <±1> [note]\|list [status] | `--no-eval --json` |
| wiki | `--min-degree 1 --json` |
| docs / stats | `--json` |
| config show\|set k=v…\|reset | `--json` |
| models [show\|test\|set k=v…] | `--json` (set 키: `<role>_provider|model|effort`, embed_provider, embed_model, llm_model …) |
| requests [list\|show <id>\|last] | `--kind --limit --json` |
| system | `--target-docs 3000 --daily-new 20 --horizon-days 365 --json` |
| maintenance <action> | `--json` |
| watch | `--interval N --once` + 공통 |
| tuning [show\|set k=v…\|reset [k]\|doc] | `--stage --json` |
| arch | `--flow query\|build\|evolve\|watch --json` |
| mcp | (stdio) |
| serve | `--port 8765 --host 127.0.0.1` |

공통(build/query/eval/watch): 모든 토글 `--x/--no-x`, `--llm`, `--embed-provider`, `--model`, `--<role>-model`, `--<role>-provider`, `--debug 0|1|2`, `--trace`, `--json`. `--trace` 출력은 depth 별 들여쓰기 + ms + depth1 % + `llm=n tok=in/out` + `sql=n` + skipped 사유 + meta 요약(220자, `--debug 2` 면 2000자 + debug/samples/logs) + summary 줄. `run_captured(argv, settings, pipe)` 는 stdout 을 캡처해 웹 콘솔에 반환(serve/watch 는 거부, `watch --once` 허용).

---

## 17. Web API (web/server.py)

GET: `/`, `/static/*`, `/api/status`{stats, providers, settings, toggle_names, toggle_help, setting_help, roles, caches, watcher, jobs}, `/api/jobs/<id>`, `/api/graph?limit&community`, `/api/entity?id`, `/api/docs`, `/api/chunk?id`, `/api/doc_chunks?id`, `/api/queries?limit`, `/api/query_trace?id`, `/api/requests?kind&limit`, `/api/request?id`, `/api/models`, `/api/system?target_docs&daily_new&horizon_days`, `/api/watch`, `/api/tuning`{tunables, stages, path, overrides}, `/api/architecture`{flows, toggle_help, setting_help, tuning_stages, toggles, settings, tuning, last{query,build,eval}, providers}, `/api/evolve/status`, `/api/evolve/proposals?status`, `/api/wiki/list`, `/api/wiki/page?name`, `/api/eval/questions`, `/api/rules`.

POST(JSON body): `/api/build`{full, reset, purge_logs, overrides, debug} → {job}; `/api/eval`{k, matrix, overrides} → {job}; `/api/query`{q, overrides, log, debug} → {result, trace}; `/api/search`{q, channel, k, overrides} → {result, trace, request_id, cli}; `/api/config` 및 `/api/models/set`{settings(flat)} → 저장·재로드; `/api/models/test`{which[]}; `/api/maintenance`{action}; `/api/watch`{action: start|stop|scan|tick, interval, save}; `/api/tuning`{action: set|reset, values{}, key}; `/api/feedback`{query_id, feedback, note}; `/api/evolve/apply`{id, evaluate}; `/api/evolve/reject`{id, note}; `/api/evolve/review`{overrides}; `/api/evolve/propose`{kind, payload, reason, confidence}; `/api/wiki/page`{name, content}; `/api/cli`{argv} → {code, output}; `/api/rules`{rules}.

- `overrides` 는 요청 단위 적용 후 복원(`_with_overrides`); 프로바이더 관련 키(llm_provider, embed_*, llm_roles, `<role>_*`, ollama_*) 면 reload. 모든 핸들러는 전역 RLock. job 은 스레드 + `_JOBS[id]{status running|done|error, log[], result, error}`. 응답에 `cli` 동등 명령 문자열 포함.

---

## 18. Web UI (static/) — 탭과 동작

사이드바: 26 토글(그룹: Build / Build·속도·확장 / Query / Query·토큰·지연 / Evolve·System, 툴팁 = TOGGLE_HELP), LLM provider·answer 모델·rerank 모델·Embed provider·top_k·debug 오버라이드, "config.json 저장/되돌리기", CLI 동등 명령 표시.

탭: **Architecture·Flow**(흐름 선택, 단계 카드 체인: 토글 칩 ON/OFF·tune n·마지막 실행 ms/%, 클릭 → 상세: 설명/impact/토글/설정/튜닝 표/CLI/마지막 실행 meta, Tuning·Requests 탭 이동) · **Query**(샘플 질문, 통계 카드 ms/LLM/토큰/SQL/컨텍스트 청크/request#, 답변(인용 클릭 → 근거 스크롤), 피드백, 라우터/모델 정보, 근거 문단(제외 표시), 워터폴 trace(단계 클릭 → meta/counters/debug/samples/logs), 그래프 관계, 제안) · **Build**(증분/전체/완전초기화, 변경 스캔, job 로그, 결과 카드, trace, 문서 표) · **Requests·Profile**(목록/필터/비교 id, 인스펙터: 요약 카드·워터폴·단계 표(ms/self/%/offset/SQL/LLM/토큰/Δ)·설정·결과·raw) · **Tuning**(단계 필터, 표 편집·저장·초기화) · **Models**(임베더/전역 LLM/역할별 표, 카탈로그 datalist, 연결 테스트) · **System·Scale**(전망 표, 빌드 이력 바, 지연, 워처 제어, 캐시, 유지보수 버튼, 성능 설정) · **Search Debug** · **Graph**(canvas force-layout, 노드 상세 + 문서 참조 표) · **Wiki**(목록/편집/저장 후 빌드) · **Evolve**(대기 제안 승인/거절, 이력, 동의어, LLM 리뷰, 수동 제안) · **Eval**(단일/matrix) · **Query Log** · **Console**(CLI 실행) · **Config**(config.json/rules.json 편집).

---

## 19. MCP (mcp.py)

stdio JSON-RPC 2.0(줄 단위 또는 Content-Length 프레이밍). `initialize` → protocolVersion 에코, capabilities.tools, serverInfo{llmwiki 0.2.0}; `notifications/initialized` 무응답; `ping`; `tools/list`; `tools/call` name ∈ {wiki_query(question, k), wiki_search(channel, query, k), wiki_entity(name), wiki_status()} → `{content:[{type:text,text}]}`(오류는 isError). 알 수 없는 method → -32601.

---

## 20. 테스트 (tests/) — 재구현 시 동일하게 통과해야 하는 시나리오

- textutil: 조사 제거(`하이닉스의`→`하이닉스`), FTS 토큰(`sk`, `하이닉스`, `수율`, `hbm4`), 질의식 안전성, keywords 순서.
- 청커: 샘플 회의록 → 3개 이상 청크, `D1` 헤딩 포함.
- 규칙 추출: CFO/NVIDIA/D1 엔티티, owner/amount/attendee/comments_on 관계.
- 파이프라인(mock LLM, hash 512d): full build 2문서 → entities>5, embeddings>0, graph_build 단계 존재; 증분 변경 0; 질의 결과 1위가 capex.md, answer_mode llm, 단계 router/fts/vector/graph/rrf/rerank_llm/answer_llm 존재; vector·llm_answer 끄면 skipped + extractive; 피드백 → wiki_note 제안 → apply → applied & `wiki/` overlay 문서 색인; synonym 제안 즉시 적용.
- 확장: stat_skip(변경 없음 → skipped_by_stat=N, read=0, embed/graph/wiki "no changes"), 20문서 추가 → 그 문서만 읽음·IDF 재사용·변경 청크만 임베딩·커뮤니티 skipped·doc_refs 갱신·위키 incremental; 수정 1/삭제 1 감지; 워처 tick; 토글 3종 켜면 증분에서도 수행; stat_skip 끄면 전부 읽음.
- doc_refs: CFO 노드에 capex.md 참조·first_chunk·title, 위키 `## 문서 참조`, graph_export n_docs.
- 역할 LLM: rerank=none → rerank_llm skipped/local, answer=mock → llm 답변; 단축 키 오버라이드; provider_status.roles.overridden; test_providers.
- 프로파일/토글: summary.llm.calls>0, sql>0, debug 2 → answer_llm samples.prompt, fts debug.match, counters, graph hops, rrf overlap, context est_tokens, offset_ms; 캐시 히트(cache_hit 단계, llm 0), 토글 변경 시 미스, query_cache off; debug 0 → samples 없음; context_trim 효과.
- requests: build/query/eval 기록, get_request.trace.summary, flatten; system_info projection; maintenance.
- profiler 단위: 레벨별 필드 유무, 카운터 합, LLM 호출 카운트.
- tuning: 키 유일, coerce/범위/choices, config 키 set 불가, 기본값 저장 안 함, load/save, render_doc.
- architecture: 4 흐름, 토글명 유효, 튜닝 단계가 모두 연결, render_text OFF 표시.
- 품질 옵션: tiered tiers "AND:", fts_mode=or, PRF terms; weighted 융합·router 튜닝; 인접 청크(top_k_final=1 로 추가됨, why ["neighbor"]), 유사 dedupe(임계 0.7); rerank_method local/cross_encoder 폴백; query_expand(mock) alt 2개·fts_alt1 리스트; 캐시 키에 tuning 포함; MCP handle 전 도구.

---

## 21. 측정 기준값 (재구현 검수용, 실습 코퍼스 29문서·12문항)

| 항목 | 값 |
|---|---|
| 전체 빌드 | ≈ 8.8 s (load_corpus 43%, embed 25%, graph_build 21%) |
| 변경 없는 증분 빌드 | ≈ 90 ms |
| 질의(프로바이더 예열 후) | 50~130 ms |
| eval k=5 (all) | hit@5 1.0, MRR 0.799, term_recall 1.0 |
| eval fts 단독 / vector / graph | MRR 0.750 / 0.757 / 0.792 |
| 색인 | 319 청크, 184 엔티티, 2,899 관계, 1,079 멘션, 2 커뮤니티 |
