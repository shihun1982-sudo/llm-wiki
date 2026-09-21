# LLM Wiki v2 — 최종 구조 도식과 요청별 대응표

이 문서는 2026-09-11 의 6가지 후속 요청(아래 R-A ~ R-F)에 대한 응답으로 시스템이 어떻게 바뀌었는지,
각 구성 요소가 **어떤 요청에 대한 답인지** 를 한 장에 정리한 것입니다. 최초 설계는 [legacy/ARCHITECTURE.md](../../legacy/ARCHITECTURE.md), 최신(v3) 구조는 [ARCHITECTURE_V3.md](../2026-09-15/ARCHITECTURE_V3.md),
요청 원문은 [REQUESTS_AND_TRENDS.md](REQUESTS_AND_TRENDS.md) 를 참조하세요. 재구현용 상세 사양은 [REBUILD_SPEC.md](../2026-09-17/REBUILD_SPEC.md), 요구사항 중심 브리프는 [IMPLEMENTATION_BRIEF.md](../2026-09-11/IMPLEMENTATION_BRIEF.md) 입니다.

## 0. 요청 목록 (원문 요약)

| ID | 요청 | 응답 위치 (섹션) |
|---|---|---|
| **R-A** | embedding / rerank / chat LLM 모델을 설정할 수 있어야 한다. 설정이 어떤 식으로 되어 있는지 상세 설명 | §2 |
| **R-B** | GraphRAG 구성 시 각 노드마다 문서 ref 정보가 연결되어 있는가? (나중에 본문 찾을 때 유리) | §3 |
| **R-C** | md 3,000개 + 매일 20개 추가 규모를 유지할 수 있는 시스템 | §4 |
| **R-D** | 성능 / 속도 / 토큰량 개선점을 찾아 on/off 가능하게 | §5 |
| **R-E** | 각 단계별 디버깅 정보와 프로파일 정보를 단계별로 | §6 |
| **R-F** | 사용자의 각 요청에 대해 디버깅/프로파일 정보를 상세히 확인 | §7 |
| **R-G** | 위 기능을 상세히 확인할 수 있는 Web UI | §8 |
| (부수) | Chapter3 CEO/data 폴더를 코퍼스에 추가, CSV 지원 | §4.1, config.json |
| **R-H** | 검색 품질 개선 포인트 검토·적용 | §11 |
| **R-I** | 단계별 튜닝 요소를 설명·impact·예시와 함께 별도 파일로 제어 | §12, `tuning.json`, [TUNING.md](../../TUNING.md) |
| **R-J** | 전체 구조·흐름(질의/빌드/자가진화) 도식 + 토글/CLI 영향/impact 를 보여주는 Web 페이지 | §13 (Architecture · Flow 탭) |
| **R-K** | 트렌드 보고서 반영 여부 재점검 | §14 |

---

## 1. 전체 구조 (요청 ID 태그 포함)

```
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│  config.json / .env                                                                          │
│   corpus_dirs[]  chunk·top_k  llm_provider/llm_model/effort  embed_provider/model/dim        │
│   llm_roles{answer,rerank,extract,summary,review}[R-A]   toggles{26}[R-D]   debug_level[R-E] │
│   rerank_candidates·context_max_chars·answer_max_tokens·llm_graph_budget[R-D]                │
│   auto_build_interval·keep_requests[R-C,R-F]                                                 │
└───────────────┬──────────────────────────────────────────────────────────────────────────────┘
                │ load_settings()  (env LLMWIKI_* > config.json > 기본값)
                ▼
┌──────────────────────────────── Pipeline (llmwiki/pipeline.py) ──────────────────────────────┐
│                                                                                              │
│  providers.py [R-A]                                                                          │
│   make_llm(settings, role) ──► llm_for("answer") llm_for("rerank") llm_for("extract")        │
│                                llm_for("summary") llm_for("review")   (역할별 인스턴스, stats) │
│   make_embedder(settings) ──► hash | voyage | ollama | st          ping()/describe()          │
│   BaseLLM.complete() ── 호출수·토큰 → profiler.COUNTERS [R-E]                                 │
│                                                                                              │
│  ══ BUILD ═══════════════════════════════════════════════════════════════════════════════════ │
│   providers ─► load_corpus ─► diff ─► chunk_index ─► embed ─► graph_build ─► wiki ─► prune   │
│     [R-E]      [R-C stat_skip]        (FTS5)      [R-C IDF 재사용]  ├ rule_extract   [R-C 증분] └ fts_optimize │
│                 mtime/size 같으면                    변경 청크만      ├ llm_extract [R-D budget]     wal_ckpt  │
│                 읽지 않음 (스텁)                                     ├ degrees                            │
│                 .md .txt .csv .html .pdf                             ├ doc_refs  [R-B] ◄── mentions 집계   │
│                                                                      └ communities [R-C 전체 빌드만]        │
│   ─► warm_cache [R-C]  ─► requests.log_request("build") [R-F]                                │
│                                                                                              │
│  ══ QUERY ═══════════════════════════════════════════════════════════════════════════════════ │
│   sync_index ─► providers ─► (query_cache hit? [R-D]) ─► router ─► fts / vector / graph      │
│   ─► rrf_fuse ─► rerank_llm[R-A rerank 모델, R-D rerank_llm] | rerank_local                  │
│   ─► context [R-D context_trim, dedupe_hits] ─► answer_llm[R-A answer 모델] | answer_extractive │
│   ─► evolve_capture ─► query_log + requests.log_request("query") [R-F]                       │
│                                                                                              │
│  ══ AUTO BUILD [R-C] ═════════════════════════════════════════════════════════════════════════ │
│   check_changes() (stat-only scan, 3k files ≈ 수십 ms) ─► 변경 시 build(incremental)          │
│   서버 워처 스레드 (toggles.auto_build, auto_build_interval)  |  CLI: llmwiki watch            │
│                                                                                              │
│  system_info() [R-C]: 용량 전망(목표 문서 수·일일 추가), 빌드 이력, 질의 p50/p95, 캐시, 워처   │
│  maintenance() [R-C]: vacuum · fts_optimize · wal_checkpoint · clear/warm cache · doc_refs    │
└───────────────┬──────────────────────────────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────── Store (SQLite, llmwiki/store.py) ────────────────────────────────┐
│ docs(+mtime,size)[R-C]  chunks  chunks_fts(FTS5)  embeddings  entities(+doc_refs,n_docs,      │
│ n_mentions)[R-B]  entities_fts  relations(chunk_id)  mentions(entity,chunk,doc)  communities  │
│ query_log  proposals  evolution_log  synonyms  kv(hash_idf,build_version)                     │
│ requests(id,kind,summary,ms,llm_calls,tokens,sql_count,debug_level,config,result,trace)[R-F]  │
│ set_trace_callback → SQL 문 수 카운터 [R-E]   entity_index() / vector_matrix() 캐시 [R-C]      │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────── Profiler (llmwiki/profiler.py) [R-E] ────────────────────────────┐
│ Stage{ms, self_ms, offset_ms, meta, counters(sql/llm_calls/tokens), debug(≥1), samples(≥2),   │
│       logs, error}  →  trace(JSON 트리) + summary{total, 단계별 %, slowest, tokens, sql, errors} │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────── Web UI (llmwiki/web) [R-G] ──────────────────────────────────────┐
│ 사이드바: 26개 토글(툴팁) + 요청 단위 provider/answer·rerank 모델/top_k/debug 오버라이드         │
│ 탭: Query(토큰·SQL·컨텍스트 청크 통계, 워터폴) · Build(stat-skip 수, 단계별) ·                  │
│     Requests·Profile[R-F](모든 요청 목록 → 워터폴/단계표/meta/debug/samples/비교) ·             │
│     Models[R-A](임베더·전역 LLM·역할별 LLM 편집·연결 테스트) ·                                   │
│     System·Scale[R-C](용량 전망·빌드 이력·지연·워처·유지보수·성능 세부 설정) ·                     │
│     Architecture·Flow[R-J](흐름 도식 + 토글/튜닝/CLI/impact + 마지막 실행 ms) · Tuning[R-I](tuning.json 편집) · │
│     Search Debug · Graph(노드 → 문서 참조[R-B]) · Wiki · Evolve · Eval · Query Log · Console · Config │
│ CLI 동등: models · requests · system · maintenance · watch · tuning · arch · mcp · --debug N      │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────────────── 외부 인터페이스 ─────────────────────────────────────────────────┐
│ MCP 서버 (llmwiki/mcp.py, `python -m llmwiki mcp`) [R-K 트렌드 ⑤]: wiki_query · wiki_search ·      │
│ wiki_entity · wiki_status (읽기 전용, stdio JSON-RPC)                                             │
│ tuning.json [R-I] · tuning.py 레지스트리(82 파라미터: 설명·impact·예시·범위·rebuild) → docs/TUNING.md │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. [R-A] 임베딩 · 리랭크 · 답변(chat) 모델 설정 구조

### 2.1 설정 계층

```
환경변수 LLMWIKI_*  >  config.json  >  코드 기본값 (config.py Settings)
                              │
              ┌───────────────┴────────────────┐
              │ 전역 LLM                        │  임베딩
              │  llm_provider  auto|anthropic|  │   embed_provider  auto|hash|voyage|ollama|st
              │                ollama|mock|none │   embed_model     (voyage-3.5 / nomic-embed-text / BAAI/bge-m3 …)
              │  llm_model     claude-opus-5 …  │   embed_dim       hash 차원 (기본 4096)
              │  llm_effort    low (추출/리랭크) │   embed_batch     배치 크기
              │  answer_effort medium (답변)    │
              └───────────────┬────────────────┘
                              │ 역할별 오버라이드 (비어 있는 항목은 전역값 상속)
              llm_roles = {
                "answer":  {"provider": "", "model": "", "effort": ""},   ← 최종 답변 (인용 강제)
                "rerank":  {...},                                          ← 후보 재정렬 (JSON 순위)
                "extract": {...},                                          ← 그래프 엔티티/관계 추출 (빌드)
                "summary": {...},                                          ← 커뮤니티 요약 (빌드)
                "review":  {...}                                           ← 자가진화 LLM 리뷰
              }
```

- `Settings.role_llm(role)` 이 해석 결과 `{provider, model, effort}` 를 돌려주고, `Pipeline.llm_for(role)` 이 역할별 LLM 인스턴스를
  만들어 캐시합니다. 각 인스턴스는 호출 수·토큰·지연을 `stats` 에 누적합니다 (Models 탭에 표시).
- `auto` 는 `.env` 에 `ANTHROPIC_API_KEY` 가 있으면 anthropic, 없으면 Ollama 응답 시 ollama, 둘 다 없으면 `none`
  (답변은 추출식, 리랭크는 로컬 휴리스틱). Ollama 가용성 probe 는 30초 동안 공유되어 역할마다 반복되지 않습니다.
- 변경 방법 4가지 (모두 같은 키 이름):
  - `config.json` 의 `llm_roles`, `embed_provider` …
  - CLI `python -m llmwiki models set rerank_provider=ollama rerank_model=llama3.1 answer_model=claude-opus-5 embed_provider=voyage embed_model=voyage-3.5`
  - 요청 단위 오버라이드 `query "…" --rerank-model claude-haiku-4-5-20251001 --answer-model claude-sonnet-5` (Web 사이드바도 동일)
  - Web **Models** 탭 (드롭다운/카탈로그 + 연결 테스트 + 저장)
- `models test` / Models 탭 “연결 테스트” 는 토큰을 쓰지 않는 경량 호출(모델 목록 조회, 임베딩 1건)로 가용성·지연·모델 존재 여부를 확인합니다.
- 임베더나 `embed_dim` 을 바꾸면 벡터가 호환되지 않으므로 **전체 리빌드**가 필요합니다 (prune 단계가 다른 provider 의 벡터를 정리).

### 2.2 리랭크와 답변이 서로 다른 모델을 쓰는 예

```json
"llm_provider": "anthropic", "llm_model": "claude-opus-5",
"llm_roles": {
  "rerank":  {"model": "claude-haiku-4-5-20251001", "effort": "low"},
  "extract": {"model": "claude-sonnet-5"},
  "summary": {"provider": "ollama", "model": "qwen2.5:7b"}
}
```
→ 답변/리뷰는 Opus, 리랭크는 Haiku(저비용), 추출은 Sonnet, 요약은 로컬 Ollama.

---

## 3. [R-B] 그래프 노드 ↔ 문서 참조

원래도 `mentions(entity_id, chunk_id, doc_id, count)` 와 `relations.chunk_id` 로 근거 위치를 추적할 수 있었지만,
노드 자체에는 문서 정보가 없어 조인이 필요했습니다. v2 에서는 노드에 **비정규화된 문서 참조**를 저장합니다.

```
entities.doc_refs = [ {"doc_id": "data/meetings/past/2026-05-20-capex.md", "mentions": 7, "chunks": 3,
                       "first_chunk": "data/meetings/past/2026-05-20-capex.md#0"}, … ]   (상위 50개, 언급수 내림차순)
entities.n_docs, entities.n_mentions
```

- 빌드의 `graph_build ▸ doc_refs` 단계가 mentions 를 집계해 채웁니다. 전체 빌드는 전체, 증분 빌드는 **변경 청크에 연결된 엔티티만** 갱신합니다.
- 활용처: `entity_detail()` (API `/api/entity`, CLI `entity <name>`) → 문서 제목·경로까지 붙여 반환, Graph 탭 노드 클릭 시 “문서 참조” 표(클릭하면 본문 청크로),
  위키 페이지의 `## 문서 참조` 섹션, `graph_export()` 노드의 `n_docs/n_mentions`, INDEX.md 의 문서수 컬럼.
- 본문 찾기 경로: 노드 → `first_chunk` 로 즉시 청크 본문 (`/api/chunk?id=`), 또는 doc_id → `/api/doc_chunks` 로 문서 전체.
  관계(edge)는 여전히 `chunk_id` 로 근거 문단을 가리키므로 “A -[owner]-> B 는 어느 문단에서 나왔나” 도 바로 답할 수 있습니다.

---

## 4. [R-C] 3,000 문서 + 매일 20개 규모 유지

### 4.1 병목과 대응 (측정치: 이 저장소 27 문서 기준)

| 단계 | 기존 동작 (병목) | v2 동작 | 효과 |
|---|---|---|---|
| load_corpus | 매 빌드 모든 파일 읽기 + PDF 파싱 + sha1 (27 문서 3.5s, PDF 3개가 3.4s) | `stat_skip`: mtime/size 같으면 파일을 열지 않음 | 변경 없음 빌드 3,474 → 50 ms |
| embed (hash) | 무엇이든 바뀌면 IDF 재적합 + **전체 청크 재임베딩** | 증분은 저장된 IDF 재사용, 변경 청크만 임베딩 (`idf_refit_incremental` 로 복귀 가능) | 20 문서 추가 시 ~200 청크만 |
| graph_build | 변경 청크만 추출(기존) + 매번 커뮤니티 재탐지 + degree 를 엔티티 수만큼 UPDATE | 커뮤니티는 전체 빌드에서만(`incremental_communities`), degree 단일 SQL, 변경 엔티티만 doc_refs | 그래프 크기에 비례하던 비용 제거 |
| wiki_pages | 매 빌드 모든 엔티티 페이지 재작성 (85 페이지 550 ms → 수천 페이지면 수십 초) | 변경 엔티티 페이지 + INDEX 만 (`wiki_full_rewrite`) | 페이지 수 무관 |
| query 첫 호출 | 빌드 후 벡터 행렬 재적재(30k 청크면 수 초), 엔티티 5,000개 별칭 JSON 파싱 | `warm_cache` 로 빌드 직후 예열, `entity_index()` build_version 별 캐시 | 첫 질의 지연 제거, router/graph 수 ms |
| FTS | 세그먼트 누적 | 전체 빌드 후 `fts_optimize` + `PRAGMA optimize`, 매 빌드 WAL checkpoint | 검색 속도 유지, WAL 비대 방지 |
| 운영 | 수동 빌드 | `auto_build` 워처(서버) / `watch` CLI: stat 스캔(3k 파일 수십 ms) → 변경 시 증분 빌드 | 매일 20개 자동 반영 |
| 변경 없는 빌드 | 전 단계 실행 | embed/graph/wiki 를 “no changes” 로 건너뜀 | 87 ms |

CSV(`.csv`) 도 코퍼스로 읽습니다 (헤더=값 형태의 행 텍스트로 변환).

### 4.2 용량 전망 (System 탭 / `llmwiki system`)

| | docs | chunks | 벡터 행렬 RAM (hash 4096d) | DB |
|---|---|---|---|---|
| 현재 | 27 | 276 | 4.5 MB | 8.7 MB |
| 목표 | 3,000 | ≈30,700 | ≈500 MB | ≈970 MB |
| 1년 후 (+20/일) | 10,300 | ≈105,000 | ≈1.7 GB | ≈3.3 GB |

hash 임베딩은 `청크수 × dim × 4 B` 의 밀집 행렬을 메모리에 올리므로 3,000 문서 이상이면 **`embed_dim` 을 1024 로 낮추거나(≈125 MB)**
외부 임베더(voyage/ollama/st, 768~1024d)를 권장합니다. 전망 수치는 현재 코퍼스의 평균 청크 수(10.2/문서)로 외삽한 것입니다.

### 4.3 일일 운영 흐름

```
(서버 실행 중, auto_build=true, auto_build_interval=300)
  워처: 5분마다 stat 스캔 ──변경 없음──► 다음 주기
                        └─변경 20개──► build(incremental): 20 문서 읽기 → 200 청크 FTS/임베딩 → 규칙 추출 → doc_refs → 위키 20~40 페이지
                                        → requests 에 build 기록 (System 탭 빌드 이력에 표시)
(주 1회 권장) build --full : IDF 재적합 + 커뮤니티 재탐지 + FTS optimize + VACUUM
```

---

## 5. [R-D] 성능 / 속도 / 토큰 개선 토글 (config.toggles, CLI `--no-xxx`, 사이드바)

| 토글 | 기본 | 영향 | 설명 |
|---|---|---|---|
| `stat_skip` | on | 속도 | 파일 stat 이 같으면 읽지 않음 |
| `idf_refit_incremental` | off | 속도↓ 정확도↑ | 증분에서도 IDF 재적합 + 전체 재임베딩 |
| `incremental_communities` | off | 속도↓ | 증분에서도 커뮤니티 재탐지 |
| `wiki_full_rewrite` | off | 속도↓ | 증분에서도 모든 위키 페이지 재작성 |
| `warm_cache` | on | 지연 | 빌드 직후 벡터 행렬/엔티티 인덱스 예열 |
| `fts_optimize` | on | 속도 | 전체 빌드 후 FTS5 optimize |
| `rerank_llm` | on | 토큰 | 끄면 로컬 휴리스틱 리랭크만 (LLM 호출 1회 절약) |
| `query_cache` | on | 토큰·지연 | 같은 질의+설정+빌드버전이면 캐시 (LLM 0회, 수 ms) |
| `context_trim` | on | 토큰 | 긴 청크를 질의 관련 문장 위주로 압축 (`context_chunk_chars`) |
| `dedupe_hits` | on | 토큰 | 같은 문서의 오버랩 청크 제거 |
| `auto_build` | off | 운영 | 서버 워처 |

토글 외 수치 설정(System 탭 “성능/토큰 세부 설정”): `rerank_candidates`(16) · `rerank_chunk_chars`(600) · `context_max_chars`(9000) ·
`context_chunk_chars`(1200) · `answer_max_tokens`(3000) · `llm_graph_budget`(0=무제한) · `llm_graph_min_chars`(80) · `query_cache_size`(200) ·
`embed_batch`(64) · `auto_build_interval`(300) · `debug_level`(1) · `keep_requests`(2000).

토큰 절감 효과는 각 요청 trace 의 `summary.llm` 과 단계별 `counters` 로 바로 확인됩니다 (예: `rerank_llm` 끄기 전후 비교는 Requests 탭 “비교 대상 #”).

---

## 6. [R-E] 단계별 디버깅 · 프로파일 정보

프로파일러는 모든 단계에 다음을 자동 기록합니다.

| 필드 | 내용 | 레벨 |
|---|---|---|
| `ms`, `self_ms`, `offset_ms` | 소요·자체·시작 오프셋 (워터폴) | 0 |
| `meta` | 단계 요약 (hits, top, 청크 수, 모델, usage …) | 0 |
| `counters` | 단계 중 발생한 SQL 문 수, LLM 호출 수, 입력/출력 토큰, 임베딩 호출 수 | 0 |
| `debug` | 상세 (FTS match 문자열·전체 hit, 벡터 유사도 통계, 그래프 홉별 통계, 리랭크 전/후 순서, 느린 파일/청크 Top5 …) | ≥1 |
| `samples` | LLM 프롬프트/응답 원문 (리랭크·답변), 큰 페이로드 | ≥2 |
| `logs`, `error` | 타임스탬프 로그, 예외 + traceback | ≥1 / 항상 |
| `summary` (root) | 총 ms, 단계별 %, 느린 단계 Top5, skipped, errors, LLM 호출/토큰 합계, SQL 합계 | 항상 |

단계별로 추가된 정보 예: `load_corpus`(읽음/스킵/종류별 읽기 ms/가장 느린 파일), `diff`(변경/신규/삭제 id), `chunk_index`(문서당 평균·최대 청크),
`embed`(IDF 재적합 여부·이유, 배치 수·평균 ms), `rule_extract`(엔티티 유형·관계 유형 분포, 느린 청크), `llm_extract`(budget·skip 수),
`providers`(프로바이더 생성 비용 — 숨기지 않고 노출), `router`, `fts_search`(폴백 여부), `vector_search`(행렬 캐시 hit/miss·적재 ms·임베딩 ms·matmul ms),
`graph_search`(홉별 frontier/관계/신규 노드/ms), `rrf_fuse`(채널 간 overlap, 다중 채널 후보 수), `rerank_*`(전/후 순서, 이동 수, 프롬프트 크기),
`context`(원문 글자·압축·중복 제거·절약 글자·추정 토큰), `answer_llm`(프롬프트 글자·추정/실제 토큰·모델), `evolve_capture`(생성 제안).

레벨 지정: `config.debug_level`, 요청 단위 `--debug 2` / 사이드바 debug, API `overrides.debug_level`. CLI `--trace --debug 2` 는 debug/samples/logs 까지 출력합니다.

---

## 7. [R-F] 요청별 디버그/프로파일 확인

- 모든 요청(query/build/eval/search, 캐시 히트 포함)이 `requests` 테이블에 저장됩니다: 종류, 요약, ms, LLM 호출/토큰, SQL 수, debug_level,
  요청 설정(토글·가중치·모델), 결과 요약, 전체 trace, 오류. `keep_requests` 개수를 넘으면 오래된 것부터 자동 삭제.
- 확인 경로
  - Web **Requests · Profile** 탭: 목록(종류 필터) → 클릭하면 요약 카드(ms/호출/토큰/SQL) + 워터폴(단계 클릭 → meta/counters/debug/samples/logs) +
    단계 표(ms/self/%/offset/SQL/LLM/토큰) + 요청 설정 + 결과 요약 + raw JSON. “비교 대상 #id” 를 넣으면 단계별 ms Δ 를 표시.
  - Query 탭 결과의 “Requests 탭에서 상세 보기”, Build 결과의 `request` 번호.
  - CLI `requests list [--kind query]`, `requests show <id>`, `requests last`, `--json`.
- 질의 결과 자체에도 `request_id`, `tokens`, `cached`, 컨텍스트 포함 여부(`hits[].in_context`) 가 들어갑니다.

---

## 8. [R-G] Web UI 탭 ↔ 요청 대응

| 탭 | 대응 요청 | 내용 |
|---|---|---|
| Query | R-D, R-E, R-F | 토큰/SQL/컨텍스트 청크 통계, 워터폴(카운터 배지), 제외 청크 표시, 캐시 표시, 요청 단위 모델·debug 오버라이드 |
| Build | R-C, R-E | stat-skip 수, 변경/삭제, 단계별 trace, “변경 스캔만”, 문서 mtime/size |
| Requests · Profile | R-F, R-E | 전체 요청 목록 → 인스펙터(워터폴·단계표·meta·debug·samples·비교·raw) |
| Models | R-A | 임베더/전역 LLM/역할별 LLM 편집, 카탈로그, 연결 테스트, 역할별 호출·토큰 누적 |
| System · Scale | R-C, R-D | 용량 전망(목표 문서·일일 추가·기간), 빌드 이력 차트, 질의 p50/p95, 워처 제어, 캐시, 유지보수, 성능 세부 설정 |
| Graph | R-B | 노드 상세에 문서 참조 표(doc_id·제목·언급·청크·첫 청크 → 본문) |
| 사이드바 | R-D | 26 토글(성능 그룹 강조, 툴팁) + CLI 동등 명령 |
| Console | 전부 | `models`, `requests`, `system`, `maintenance`, `watch --once`, `--debug N` 등 신규 CLI 실행 |

---

## 9. 파일별 변경 요약

| 파일 | 변경 | 요청 |
|---|---|---|
| `llmwiki/profiler.py` | 디버그 레벨(0/1/2), 전역 카운터(sql/llm/tokens/embed), offset/self ms, summary, flatten | R-E |
| `llmwiki/config.py` | 11개 토글, `llm_roles`+`role_llm()`, 성능 수치 설정, `TOGGLE_HELP`/`SETTING_HELP`, 역할 단축키 오버라이드 | R-A, R-D |
| `llmwiki/providers.py` | `BaseLLM.complete` 계측 래퍼, `ping()`/`describe()`, `make_llm(settings, role)`, `MODEL_CATALOG`, Ollama probe 캐시, 임베더 계측 | R-A, R-E |
| `llmwiki/store.py` | docs.mtime/size, entities.doc_refs/n_docs/n_mentions(마이그레이션), requests 테이블, SQL 카운터, `entity_index()` 캐시, `refresh_doc_refs()`, 단일 SQL degree, `fts_optimize()`, `wal_checkpoint()` | R-B, R-C, R-F |
| `llmwiki/corpus.py` | `.csv`, stat_skip 스텁, 로드 통계, `scan_changed()` | R-C |
| `llmwiki/retrieval.py` | 엔티티 인덱스 캐시 사용, 홉별/overlap/전후 순서 디버그, `rerank(use_llm, n_cands, chunk_chars)` | R-D, R-E |
| `llmwiki/answer.py` | `context_trim`/`dedupe`/`max_chars`, 프롬프트 샘플, 토큰 추정 | R-D, R-E |
| `llmwiki/graph_build.py` | LLM budget/min_chars, touched 엔티티 반환, `doc_refs` 단계, 커뮤니티 skip 사유 | R-B, R-C, R-D |
| `llmwiki/wiki.py` | `only=` 증분 작성, 문서 참조 섹션, INDEX 문서수 | R-B, R-C |
| `llmwiki/pipeline.py` | 역할별 LLM, providers 단계, 증분 최적화, 질의 캐시, requests 기록, `auto_build_tick`, `system_info`, `maintenance`, `test_providers` | 전부 |
| `llmwiki/cli.py` | `models`, `requests`, `system`, `maintenance`, `watch`, `--debug`, `--<role>-model`, trace 출력에 %/카운터/summary | 전부 |
| `llmwiki/web/server.py` | `/api/requests`, `/api/request`, `/api/models(+test/set)`, `/api/system`, `/api/maintenance`, `/api/watch`, 워처 스레드 | R-A, R-C, R-F, R-G |
| `llmwiki/web/static/*` | Requests·Models·System 탭, 사이드바 성능 토글/모델 오버라이드, 워터폴 카운터·debug·samples, Graph 문서 참조 | R-G |
| `tests/test_scale_profile.py` | 증분/stat_skip/워처, doc_refs, 역할 LLM, 성능 토글·프로파일, requests, 프로파일러 단위 | 검증 |

---

## 10. 남은 한계와 다음 단계

- hash 임베딩의 밀집 행렬은 10만 청크 규모에서 GB 단위가 됩니다. 다음 단계는 (1) 외부 임베더 기본화, (2) float16 저장, (3) 청크 단위 ANN(예: HNSW) 도입입니다.
- LLM 그래프 추출은 청크당 1회 호출이라 3,000 문서 초기 빌드에 3만 회가 필요합니다. `llm_graph_budget` 으로 일일 신규분만 처리하거나, 규칙 추출을 기본으로 두고 LLM 은 선택 문서에만 적용하는 운영을 권장합니다.
- 워처는 폴링(stat) 방식입니다. OS 파일 이벤트(watchdog)로 바꾸면 반영 지연을 수 초로 줄일 수 있습니다.
- 질의 캐시는 프로세스 메모리에만 있습니다. 여러 서버 인스턴스를 쓰려면 SQLite 기반 캐시로 옮겨야 합니다.

---

## 11. [R-H] 검색 품질 개선 — 적용한 것과 남은 것

실습 코퍼스(12문항)로 적용 전/후를 비교했습니다 (`python -m llmwiki eval --matrix --k 5`).

| 구성 | 이전 MRR | 지금 MRR | 비고 |
|---|---|---|---|
| all (fts+vector+graph, 기본) | 0.743 | **0.799** | hit@5 1.0 유지 |
| fts+vector | 0.660 | 0.757 | |
| vector | 0.701 | 0.757 | |
| fts 단독 | 0.736 | 0.750 | tiered AND→OR 효과. heading 보너스 때문에 1문항(글로벌 발표 일정) hit 누락 — `rerank_heading_bonus=0` 이면 원복 |
| graph 단독 | 0.819 | 0.792 | 위와 같은 1문항 영향 |

적용한 개선 (모두 `tuning.json` 으로 on/off·수치 조절):

| 개선 | 단계 | 기본 | 효과/비용 |
|---|---|---|---|
| **tiered FTS** (모든 키워드 AND 우선 → OR 보충 → bigram 폴백) | fts_search | on (`fts_mode=tiered`) | 키워드가 모두 든 문단이 상위로. 비용 0 |
| **PRF** (pseudo-relevance feedback, 상위 문단 빈출어로 재검색) | fts_search | off (`prf_enabled`) | LLM 없이 어휘 불일치 완화. 소규모 평가셋에서는 중립 |
| **LLM 질의 확장** (대체 질의 n개 → 추가 FTS/벡터 → 별도 리스트 융합) | query_expand | off (`query_expand`) | recall↑, LLM 1회 토큰 |
| **weighted 융합** (min-max 정규화 가중합) 및 다중 채널 보너스 | rrf_fuse | rrf (`fusion_method`) | 의미 임베더 사용 시 실험 권장 |
| **크로스인코더 리랭크** (sentence-transformers CrossEncoder, BAAI/bge-reranker-v2-m3) | rerank | auto (`rerank_method=cross_encoder` 로 전환) | 트렌드 ① 표준 구성. Python 3.9+ 필요, 미설치 시 local 폴백 |
| **local 리랭크 헤딩 보너스** + 가중치 외부화 | rerank | 0.1 | MRR +0.056 |
| **인접 청크 확장** (상위 청크의 앞/뒤 청크 포함) | context | off (`context_neighbors`) | 표/목록 절단 보완, 토큰↑ |
| **유사 문단 중복 제거** (문서 간 토큰 Jaccard) | context | 0.85 | 반복 문장 토큰 절약 |
| 라우터 임계값/가중치, 그래프 감쇠·허브 페널티·시드 수, BM25 컬럼 가중치, 공동출현 창 등 | 전 단계 | 기존값 | 상수 → 튜닝 파라미터 |

남은 개선 여지 (우선순위 순):
1. **의미 임베더** — hash 임베딩은 의미를 이해하지 못합니다. `embed_provider=voyage`(API) 또는 `st`(로컬 bge-m3, Python 3.9+) 로 바꾸면 벡터 채널 품질이 가장 크게 오릅니다.
2. **평가셋 확장** — 12문항으로는 0.05 차이가 1문항입니다. 코퍼스별 30~50문항(정답 문서·핵심 용어)을 `eval/questions.json` 에 추가해야 튜닝이 안전합니다.
3. **부모-자식(small-to-big) 검색** — 작은 청크로 찾고 상위 섹션을 컨텍스트로. `context_neighbors` 가 그 근사치입니다.
4. **시간 가중(recency)** — 회의록·일정은 최신 문서가 더 중요할 때가 많습니다. `docs.mtime` 이 이미 있으므로 융합 단계에 감쇠 항을 추가하면 됩니다.
5. **LLM 그래프 추출 + 커뮤니티 요약** — API 키가 있으면 `--llm-graph --community-summary` 로 관계 품질과 요약 검색이 개선됩니다.

---

## 12. [R-I] 단계별 튜닝 파일 (`tuning.json`)

- 레지스트리 `llmwiki/tuning.py` 에 **82개 파라미터**를 단계별로 정의(타입·기본값·범위·설명·impact·예시·rebuild 필요 여부).
- `tuning.json` 에는 기본값과 다른 값만 저장됩니다. config.json 에 이미 있던 항목(top_k, chunk, rrf_k 등)은 `source=config` 로 같은 표에 나타나며 저장 시 config.json 에 기록됩니다.
- 제어 경로: `tuning.json` 직접 편집 · CLI `tuning show|set k=v|reset|doc` · Web **Tuning** 탭 · Architecture 탭의 단계 상세 → "Tuning 탭에서 편집".
- 문서: [TUNING.md](../../TUNING.md) (`python -m llmwiki tuning doc` 로 현재값 포함 재생성).
- 튜닝값은 질의 캐시 키와 각 요청의 `config.tuning` 에 포함되므로 Requests 탭에서 "어떤 값으로 실행됐는지" 추적됩니다.

---

## 13. [R-J] Architecture · Flow 탭

- 데이터: `llmwiki/architecture.py` 레지스트리 (`/api/architecture`, CLI `arch`). 4개 흐름(User Request / Data Build / Self-Evolving / Auto Build) 의 단계 순서와 단계별 토글·config 설정·튜닝 파라미터·CLI·impact·모듈·입출력.
- 화면: 흐름별 단계 카드 체인. 카드에 토글 칩(ON/OFF — **사이드바에서 토글을 바꾸면 즉시 반영**), 튜닝 파라미터 수, **마지막 실행의 단계별 ms·비율**(query→마지막 query 요청, build/watch→마지막 build, evolve→마지막 eval) 이 겹쳐 보입니다. 토글 OFF/skipped 단계는 회색, 오류는 빨강.
- 카드 클릭 → 오른쪽 상세: 동작 설명, impact, 토글(설명 포함), config 설정값, 튜닝 파라미터 현재값/기본값/impact, CLI, 마지막 실행 meta(SQL/LLM 토큰 포함), Requests 탭·Tuning 탭으로 바로 이동.

---

## 14. [R-K] 트렌드 보고서 반영 현황 (docs/history/2026-09-13/REQUESTS_AND_TRENDS.md B 기준)

| 트렌드 | 상태 | 구현 |
|---|---|---|
| ① 하이브리드(BM25+Dense)+Cross-encoder 리랭크 | ✔ | 3채널 + RRF/weighted 융합, `rerank_method=cross_encoder`(bge-reranker-v2-m3), LLM/로컬 리랭크 |
| ② GraphRAG 경량화 (LightRAG 이중 검색, 요약 생략, 증분) | ✔ | 엔티티→청크 이중 검색, 커뮤니티 요약 옵션, 변경 청크만 재추출, 노드 doc_refs |
| ② LazyGraphRAG/DRIFT 식 동적 커뮤니티 선택 | △ | 커뮤니티 탐지·요약은 있으나 질의 시 커뮤니티 단위 선택은 미구현 |
| ③ 스트리밍/실시간 리빌드 (위상 불일치 방지) | ✔(폴링) | stat 스캔 워처 + 증분 빌드, build_version 으로 실행 중 서버 캐시 자동 갱신. 이벤트 스트리밍(Kafka)은 범위 밖 |
| ④ DTG(코드 데이터 변환 그래프) | ✘ | 문서 코퍼스 대상이라 해당 없음 (코드 코퍼스 시 별도 추출기 필요) |
| ⑤ MCP 표준 인터페이스 | ✔ | `python -m llmwiki mcp` (stdio JSON-RPC, 읽기 전용 도구 4종) |
| ⑤ 자가진화 + 통제 샌드박스 | ✔ | 데이터 전용 제안, HITL 기본, 스냅샷/회귀평가/롤백, 체크섬 로그, MCP 는 읽기 전용 |
| 기준 시나리오 "적응형 검색 라우터" | ✔ | router (튜닝 가능) |
| 리스크 2 환각 | ✔ | 인용 강제 프롬프트, 무인용 답변 갭 기록 |
| Guardrails 계층 | △ | 프롬프트 규칙 수준. 별도 가드레일 엔진은 없음 |
