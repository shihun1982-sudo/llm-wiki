# LLM Wiki CLI 운영 흐름 가이드 (CLI_FLOWS)

이 문서는 `llmwiki` CLI 의 모든 하위 명령을 **포팅·운영 엔지니어** 관점에서 정리한 것이다.
모든 예제 출력은 격리된 샌드박스(합성 모뎀 코퍼스 37문서, `LLMWIKI_LLM_PROVIDER=mock`, `LLMWIKI_EMBED_PROVIDER=hash`, `LLMWIKI_EMBED_DIM=512`)에서 실제로 실행해 얻은 것이며, 긴 경로는 `<tmp>` 로 줄였다. 내부 단계 설명은 `llmwiki/cli.py`, `pipeline.py`, `query_engine.py` 를 기준으로 한다.

---

## 1. 개요

### 1.1 실행 방식

| 방법 | 설명 |
|---|---|
| `python -m llmwiki <cmd> …` | 기본. `llmwiki/__main__.py` → `cli.main()` → `cli.run()`. 시작 시 `sys.stdout.reconfigure(encoding="utf-8")`. |
| `run.bat <cmd> …` | Windows 도우미. `PYTHONIOENCODING=utf-8` 설정 후 프로젝트 루트로 이동해 `python -m llmwiki %*` 실행. `run.bat test` 는 `python -m unittest discover -s tests -v`. |
| Web UI 콘솔 탭 | `cli.run_captured()` 가 **같은 argparse 를 in-process 로** 실행하고 stdout 을 캡처한다. 즉 CLI = Web 기능 집합. |
| MCP 서버 (`mcp`) | stdio JSON-RPC. `wiki_query` 등 도구가 같은 `Pipeline` 을 호출. |

PowerShell 에서 직접 실행할 때는 `$env:PYTHONIOENCODING='utf-8'` 을 먼저 설정한다 (한글 출력 깨짐 방지).

**PowerShell 주의(실측)**: 쉼표 목록 인자는 반드시 따옴표로 감싼다. `--methods rrf,zscore,dbsf` 처럼 쓰면 PowerShell 이 배열로 해석해 세 개의 인자로 쪼개고 `llmwiki: error: unrecognized arguments: zscore dbsf` (exit 2) 가 난다. `--methods "rrf,zscore,dbsf"`, `--preset "quality,token"` 형태로 쓴다.

### 1.2 명령 실행의 공통 순서 (`cli.run`)

1. `build_parser()` 로 파싱. 하위 명령이 없으면 도움말 출력, exit 0.
2. `load_settings()` : `.env` 로드 → `config.json` (없으면 `setup/config.example.json` 복사) → `LLMWIKI_*` 환경변수 오버라이드.
3. `_overrides_from_ns()` : CLI 토글/모델/`--debug` 값을 `apply_overrides()` 로 **이번 프로세스에만** 반영. `query --k N` 은 `top_k_final` 로 매핑된다 (eval/trial 의 `--k` 는 평가 k).
4. `Pipeline(s)` 생성: 로깅 설정(`logs/`), `tuning.json` 로드, 토크나이저/복합어 사전 초기화, `Store(db_path)` 열기(SQLite WAL).
5. `--preset` 이 있으면 `presets.apply(save=False)` 로 임시 적용 → `_run_cmd()` 실행 → `finally` 에서 `presets.restore()`.
6. `_run_cmd()` 의 반환값이 프로세스 종료 코드.

### 1.3 공통 옵션

`_add_toggle_flags()` 가 붙는 명령(**build, health, precompute, trial, query, eval, watch**)에는 다음이 자동 생성된다.

| 옵션 | 의미 |
|---|---|
| `--<toggle>` / `--no-<toggle>` | `config.Toggles` 의 **모든 필드**에 대해 자동 생성 (`_` → `-`). 예: `--no-graph`, `--llm-graph`, `--no-health-check`, `--fallback-loop`. 기본값 `None` = 덮어쓰지 않음. |
| `--llm auto|anthropic|ollama|mock|none` | 전역 LLM 프로바이더 |
| `--embed-provider auto|hash|voyage|ollama|st` | 임베더 |
| `--model` | 전역 모델명 |
| `--<role>-model`, `--<role>-provider` | 역할별 (answer, rerank, extract, summary, review, expand, verify, forensic) |
| `--debug 0|1|2` | 프로파일 상세도. 0 요약, 1 디버그 메타/로그, 2 프롬프트·응답 원문 샘플까지 (`--trace` 출력에 `⚙` `▶` 줄 추가) |
| `--preset a,b` | presets.json 프리셋을 이번 프로세스에만 적용 (뒤가 우선). 모르는 이름은 `WARNING: unknown preset(s)` |
| `--trace` | Profiler 트리를 텍스트로 출력 (단계명 · ms · 비율 · llm/sql 카운터 · `↳` 메타 · skipped 사유) |
| `--json` | 결과를 JSON 으로 출력 (query 는 `{"result", "trace"}`) |

그 외 명령(graph, entity, search, docs, stats, config, …)은 `--json` 만 가진다.

### 1.4 설정 파일과 우선순위

```
환경변수 LLMWIKI_*  >  config.json  >  코드 기본값 (config.Settings / Toggles)
CLI 플래그 / --preset 은 그 위에 "프로세스 한정" 으로 덮어씀 (--save 나 config set 을 쓰지 않는 한 저장되지 않음)
```

- 설정 키 → `LLMWIKI_<KEY 대문자>` (예 `LLMWIKI_TOP_K_FINAL`), 토글 → `LLMWIKI_TOGGLE_<NAME>` 또는 `LLMWIKI_<NAME>`, 역할 모델 → `LLMWIKI_ANSWER_MODEL` 등, 코퍼스 폴더 → `LLMWIKI_CORPUS_DIRS` (세미콜론 구분). `config show --effective` 가 키별 출처(default/file/env)와 env 이름을 보여준다.
- 파일 위치 레지스트리(`config.path_for`): `LLMWIKI_<NAME>_PATH` 로 개별 이동 가능. 이름: config, env, tuning, presets, query_rules, mcp_sources, agents, pins, rules(`data/rules.json`), schemas_dir, prompts_dir, eval, logs_dir, themes. 구 별칭 `LLMWIKI_CONFIG`, `LLMWIKI_ENV_FILE`, `LLMWIKI_TUNING` 도 인식.
- **포팅 시 주의 (코드 확인)**
  - `tuning.py` 와 `evalset.py` 도 `path_for()` 를 쓰므로 `LLMWIKI_TUNING_PATH` / `LLMWIKI_EVAL_PATH` 가 실제로 반영된다 (이 문서 작성 중 발견해 수정; 구 별칭 `LLMWIKI_TUNING` 도 계속 인식). 일회성 질문셋은 `eval/trial/fusion` 의 `--questions <경로>` 로 준다.
  - 데이터 파일이 없으면 기본값으로 **자동 생성**된다: presets.json, query_rules.json, pins.json, mcp_sources.json, rules.json, schemas/*.json, prompts/*.md, eval/questions.json (샘플 모뎀 코퍼스의 `setup/sample_corpus_modem/questions.json` 이 있으면 그것을 복사).
- 저장 명령: `config set` → config.json, `tuning set|reset` → tuning.json, `models set` → config.json(llm_roles), `preset apply --save` → config.json + tuning.json, `rules add|remove` → query_rules.json, `pin add|remove` → pins.json, `prompts reset` → prompts/*.md.

### 1.5 request_id / run_id / query_id

| 식별자 | 발급 | 용도 |
|---|---|---|
| `run_id` | `Profiler` 생성 시 12자리 hex (`profiler._new_run_id`). build/query/eval/search/wiki 등 요청마다 1개 | `logs/*.log` 모든 줄과 `requests` 행에 기록. `logs grep --run <run_id>` |
| `request_id` | `store.log_request()` 가 반환하는 `requests` 테이블 autoincrement | `requests show <id>`, `forensic <id>`, `logs grep --request <id>` (→ run_id 로 변환해 grep) |
| `query_id` | `store.log_query()` 의 `query_log` id. `log=True` 이고 `evolve_capture` 토글이 켜진 질의만 | `evolve feedback <query_id> +1|-1` |

`build`, `query`(캐시 히트 포함), `eval`(질문마다 1건 + eval 자체 1건), `trial run`(질문마다), `precompute run`, `fusion compare` 가 모두 requests 를 남긴다. `keep_requests`(기본 2000) 초과분은 자동 삭제되며 `maintenance purge_requests` 로 비울 수 있다. `search` 는 requests 를 남기지 않고 logs 만 남긴다.

---

## 2. 명령 카탈로그

| 명령 | 한 줄 목적 | 주요 옵션 | 건드리는 파일/테이블 |
|---|---|---|---|
| `health [--quick] [--for-build]` | 환경·프로바이더·DB·디스크·코퍼스 점검 | 토글 플래그 | 읽기만 (db quick_check, 임베더 ping 1건) |
| `build [run] [--full [--no-reset]] [--purge-logs] [--force]` | 코퍼스 색인 (FTS/Vector/Graph/Wiki) | 토글 플래그, `--trace` | docs, chunks, chunks_fts, embeddings, entities, relations, mentions, communities, doc_meta, kv, embed_runs, requests, `wiki/*.md`, `data/build.lock` |
| `build status` | 락/마지막 빌드/임베딩 진행률 | `--json` | kv(last_build, embed_progress), build.lock |
| `build verify [--fix]` | 색인 정합성 검사 | `--fix` | 읽기 (fix 시 댕글링·고아·n_chunks·stale 위키 정리) |
| `query "질문" [--k N] [--no-log]` | 하이브리드 검색 + 답변 | 토글, `--trace`, `--json`, `--preset`, `--debug` | query_log, requests, episodes, forensics, proposals, answer_cache(precompute 시) |
| `search fts|vector|graph "질문" [--k]` | 단일 채널 디버그 | `--json` | 읽기, logs 만 |
| `eval [--k 5] [--matrix] [--questions F]` | 회귀 평가 hit@k/MRR | 토글 | requests |
| `trial run|list|compare|report|show` | 설정 전후 회귀 비교 | `--name --preset --set k=v --questions --note --md` | trials, requests |
| `graph [--limit] [--community] [--provenance] [--types]` | 그래프 요약/내보내기 | `--json` | 읽기 |
| `entity <name|e:id>` | 엔티티 상세 | `--json` | 읽기 (doc_refs 없으면 계산·저장) |
| `corpus lint|types|schema|example|lint-file|stats` | 문서 계약(front matter) | `--all --limit` | doc_meta, kv(lint_summary), schemas/ |
| `embed report|status|runs|clear-cache` | 임베딩 coverage/진행률 | `--json` | embeddings, embed_runs, embedding_cache |
| `rules show|add|remove|test|stats|path` | 규칙 기반 질의 확장 사전 | | query_rules.json |
| `pin list|add|remove|test` | 고정 근거 | `--doc --chunk --query --keywords --always --doc-types --weight --note` | pins.json |
| `precompute run|status|clear|doc-vectors` | 답변 사전 계산 캐시 | `--from-log --stale` | answer_cache, doc_vectors, requests |
| `forensic last|list|summary|<request_id> [--llm]` | 질의 포렌식 진단 | `--limit` | forensics |
| `time "표현"` | 한국어 시간 표현 파싱 | | 없음 |
| `fusion show|compare` | 융합 방식 비교 | `--methods --k --questions` | requests |
| `memory status|decay|consolidate|episodes` | 자가진화 메모리 | `--limit` | episodes, proposals, pins.json(decay) |
| `preset list|show|apply|diff` | 설정 프리셋 | `--save` | presets.json, (--save) config.json/tuning.json |
| `prompts list|show|reset|path` | LLM 프롬프트 파일 | | prompts/*.md |
| `logs tail|grep|files|dir` | logs/ 조회 | `-n --file --request --run --text --level --since` | logs/*.log |
| `requests list|last|show <id>` | 요청별 프로파일 trace | `--kind --limit` | requests |
| `config show [--effective]|paths|set k=v|reset` | 설정 | | config.json |
| `models show|test|set` | 역할별 LLM/임베더 | | config.json(llm_roles) |
| `tuning show|set|reset|doc` | 단계별 튜닝 파라미터 | `--stage` | tuning.json, (doc) docs/TUNING.md |
| `arch [--flow query|build|evolve|watch]` | 구조·흐름 도식 | `--json` | 없음 |
| `evolve status|list|apply|reject|review|feedback` | 자가진화 제안 | `--no-eval` | proposals, evolution_log, synonyms, rules.json, wiki 노트, query_log(feedback) |
| `wiki [--min-degree]` | 위키 페이지 재생성 | | `wiki/*.md` |
| `docs` | 색인된 문서 목록 | `--json` | 읽기 |
| `stats` | 인덱스 통계/프로바이더/토글 | `--json` | 읽기 |
| `system [--target-docs --daily-new --horizon-days]` | 확장성 추정/지연 통계 | `--json` | 읽기 |
| `maintenance vacuum|fts_optimize|wal_checkpoint|clear_cache|warm_cache|refresh_doc_refs|purge_requests` | DB 유지보수 | | DB 파일, requests |
| `mcp-source list|test|ingest|enrich|fetch` | 외부 MCP 소스 | `--since --dry-run` | mcp_sources.json, `data/mcp_cache/*`(ingest) |
| `watch [--interval] [--once]` | 코퍼스 변경 감시 → 증분 빌드 | 토글 | build 와 동일 |
| `mcp` | MCP stdio 서버 (블로킹) | | query 와 동일 |
| `serve [--port 8765] [--host]` | Web UI (블로킹) | | 전부 |

---

## 3. 명령별 상세

### 3.1 health

**예제**
```powershell
python -m llmwiki health            # 네트워크 ping 포함
python -m llmwiki health --quick    # LLM/임베더/rerank/MCP ping 생략
python -m llmwiki health --for-build --json
```

**내부 단계** (`health.run_health` → `format_health`)
1. `python`(≥3.9), `sqlite_fts5`, `db_integrity`(quick_check + docs/chunks/embeddings 수), `wal_size`, `disk_free`(warn), `corpus_dirs`(누락 폴더), `vector_memory`(warn), `embedding_dim`(저장 벡터 차원 vs 현재, warn).
2. `--quick` 이 아니면 역할별 LLM ping (`llm_answer`, `llm_rerank`; `--for-build` 면 빌드에 필요한 역할만), `embedder`(텍스트 1건 임베딩), rerank_url 있으면 `rerank_api`, mcp_sources 토글 시 `mcp_sources`.
3. `schema_lint` 요약(warn).
4. level=fail 인 항목이 하나라도 실패면 `ok=false`. 빌드 시작 시 `toggles.health_check` 가 켜져 있으면 같은 검사가 자동 실행된다.

**출력**
```
health: OK (fail=0 warn=0)
  ✔ python           Python 3.14.7
  ✔ sqlite_fts5      sqlite 3.50.4, FTS5 ok
  ✔ db_integrity     quick_check=ok docs=0 chunks=0 embeddings=0
  ✔ wal_size         wal=0.0MB (임계 64MB)
  ✔ disk_free        free 562.3 GB at <tmp>\data
  ✔ corpus_dirs      files=37 dirs=1 missing=[]
  ✔ vector_memory    chunks=0 dim=512 dtype=float32 → matrix≈0MB
  ✔ embedding_dim    no embeddings yet
  ✔ llm_answer       mock/mock mock (deterministic, no network)
  ✔ llm_rerank       mock/mock mock (deterministic, no network)
  ✔ embedder         hash/hash-ngram-512 dim=512 embedded 1 text
  ✔ schema_lint      docs=0 errors=0 warnings=0
[exit=0]
```
실패 예 (코퍼스 폴더가 없을 때):
```
health: FAIL (fail=1 warn=0)
  ✘ corpus_dirs      files=0 dirs=1 missing=['C:\\nonexistent_corpus_dir']  → config set corpus_dirs=경로
[exit=1]
```

**관련 토글/설정**: `health_check`(빌드 전 자동), `wal_checkpoint_mb`, `embed_dim`, `embed_store_dtype`, `rerank_url`, `mcp_sources`.

**흔한 오류**: `corpus_dirs` 실패 → `config set corpus_dirs=경로` 또는 `LLMWIKI_CORPUS_DIRS`; `embedding_dim` warn → 임베더/차원을 바꿨으면 `build --full`; `llm_*` warn → `.env` 키/모델명/엔드포인트 확인 (`models test`).

### 3.2 build (run / status / verify)

**예제**
```powershell
python -m llmwiki build --full --trace        # DB 테이블 비우고(reset) 전체 빌드
python -m llmwiki build --full --no-reset     # 테이블 재생성 없이 전체 재색인
python -m llmwiki build --full --purge-logs   # 질의 로그/제안/동의어/requests 까지 삭제
python -m llmwiki build                       # 증분 (기본)
python -m llmwiki build --force               # health 실패해도 강행
python -m llmwiki build --no-health-check --no-embed --llm-graph
python -m llmwiki build status
python -m llmwiki build verify [--fix]
```

**내부 단계** (`cli._run_cmd` → `Pipeline.build` → `_build`)
0. `--full` 이면 기본으로 `reset_index()` : 색인 테이블(docs, chunks, chunks_fts, embeddings, entities, entities_fts, relations, mentions, communities, kv, doc_meta, doc_vectors, answer_cache) DELETE + VACUUM. `--purge-logs` 면 로그 테이블(query_log, proposals, evolution_log, synonyms, requests, forensics, episodes, trials, embed_runs)도 삭제. 편집 노트 없는 `wiki/*.md` 삭제. `--no-reset` 이면 생략.
1. `BuildLock(data/build.lock)` 획득 (`build_lock_timeout` 초 대기, 0 = 즉시 실패 → exit 2). 소유 pid 가 죽었거나 6시간 지난 락은 회수.
2. trace 단계 (`--trace` 에 그대로 나타남):
   - `health` (토글 `health_check`) → 실패 시 `RuntimeError("health check failed…")` → exit 3
   - `mcp_ingest` (토글 `mcp_sources`) → `data/mcp_cache/<name>` 폴더가 스캔 대상에 추가
   - `load_corpus` : `iter_corpus()`; 증분 + `stat_skip` 이면 mtime/size 가 같은 파일은 읽지 않음. `wiki/` 편집 노트가 `wiki_note` 문서로 overlay
   - `diff` : 저장된 해시와 비교해 changed/removed/new/renamed
   - `chunk_index` : 삭제 문서 제거 → `chunk_document()` → FTS upsert → front matter 정규화(`schema.normalize_meta`) + lint → `doc_meta`, `kv.lint_summary`
   - `embed` : `EmbedRunner` (적응형 배치, `embed_commit_every` 마다 진행률 저장, 재개 가능). hash 임베더는 전체 빌드에서 IDF 재적합(`idf_refit`), 증분에서는 저장된 IDF 재사용
   - `graph_build` → 하위 `rule_extract` / `llm_extract`(토글 `llm_graph`) / `degrees` / `doc_refs` / `communities`(전체 빌드 또는 `incremental_communities`)
   - `doc_vectors` (토글 `doc_vector`)
   - `wiki_pages` : 전체는 모든 페이지, 증분은 touched 엔티티만 (`wiki_full_rewrite` 로 전체)
   - `prune` : 고아 엔티티·댕글링 정리, stale 위키 페이지 삭제, 전체 빌드 시 다른 프로바이더 벡터 삭제 + `fts_optimize`, WAL 체크포인트
   - `verify` (토글 `verify_after_build`) : `store.verify()`; `embedding_coverage`, `community_unassigned` 를 제외한 문제는 ALERTS 로
   - `kv.last_build` 저장, `build_version` +1, 질의 캐시 무효화
   - `warm_cache` (벡터 행렬·엔티티 인덱스 적재), `precompute` (토글 `precompute_after_build`)
3. `finally` : trace 완성, `requests` 에 kind=build 기록.

**출력 — 전체 빌드**
```
  · reset: cleared 13 tables, kept_logs=True, removed_wiki_pages=0
  · health: ok (fail=0 warn=0)
  · loaded 37 docs (37 read, 0 skipped by stat)
  · indexed 185 chunks (FTS)
  · embedding 185/185 (batch 97, failed 0, cache 0)
  · embedded 185 chunks (cache 0, failed 0)
  · graph: entities touched=57 explicit=51 id_links=92
build                     201.7 ms       sql=11759
  health                      1.6 ms    1% sql=40
  mcp_ingest                  0.0 ms        (skipped: disabled)
  load_corpus                11.3 ms    6%
  diff                        0.0 ms    0% sql=1
    ↳ {"changed": 37, "unchanged": 0, "removed": 0, "new": 37, "renamed": 0}
  chunk_index                62.5 ms   31% sql=2917
    ↳ {"docs": 37, "chunks": 185, "avg_chunks_per_doc": 5.0, "doc_types": {"(none)": 2, "cl": 10, …
  embed                      45.4 ms   23% sql=202
    ↳ {"provider": "hash", "idf_refit": true, "idf_reason": "full/first build", "todo": 185, "embedded": 185, …
  graph_build                38.4 ms   19% sql=7701
    rule_extract               28.3 ms       sql=7177
      ↳ {"rule_entities": 353, "rule_relations": 299, "explicit_relations": 51, "id_relations": 92, "provenance": {"cooccur": 156, "explicit": 51, "rule": 92}}
    llm_extract                 0.0 ms        (skipped: disabled)
    communities                 2.2 ms       sql=71
      ↳ {"communities": 10, "largest": 12, "llm_summaries": 0}
  wiki_pages                 30.5 ms   15% sql=721
    ↳ {"entities": 46, "mode": "full", "written": 46, "removed_stale": 0, "total_entities": 46}
  prune                       5.3 ms    3% sql=117
  verify                      0.9 ms    0% sql=35
    ↳ {"ok": true, "problems": [], "counts": {"docs": 37, "chunks": 185, "entities": 57, "relations": 299, "embeddings": 185}}
  warm_cache                  0.8 ms    0% sql=4
  precompute                  0.0 ms        (skipped: disabled)
summary: total=201.7 ms, llm calls=0 tokens=0 (in 0 / out 0), sql=11738, slowest=chunk_index 31%, embed 22%, graph_build 19%
build done (full): {"docs": 37, "chunks": 185, "embeddings": 185, "entities": 57, "relations": 299, …}
[exit=0]
```

**출력 — 변경 없는 증분 빌드** (37 파일 stat 만 확인, 17.8 ms)
```
  · loaded 37 docs (0 read, 37 skipped by stat)
  · indexed 0 chunks (FTS)
build                      17.8 ms       sql=355
  load_corpus                 6.5 ms   36% sql=1
  diff                        0.1 ms    0% sql=1
    ↳ {"changed": 0, "unchanged": 37, "removed": 0, "new": 0, "renamed": 0}
  embed                       0.0 ms        (skipped: no changes)
  graph_build                 0.0 ms        (skipped: no changes)
  wiki_pages                  0.0 ms        (skipped: no changes)
build done (incremental): {…"requests": 1 …}
```

**출력 — 문서 1개 수정 + 1개 추가 후 증분**
```
  · loaded 38 docs (2 read, 36 skipped by stat)
  · indexed 13 chunks (FTS)
  · embedded 13 chunks (cache 0, failed 0)
  · graph: entities touched=8 explicit=2 id_links=4
  diff   ↳ {"changed": 2, "unchanged": 36, "removed": 0, "new": 1, "renamed": 0}
  embed  ↳ {"idf_refit": false, "idf_reason": "reused stored IDF", "todo": 13, "embedded": 13, …
    communities                 0.0 ms        (skipped: incremental build (incremental_communities off))
  wiki_pages ↳ {"mode": "incremental(7)", "written": 7, …}
  verify ↳ {"ok": false, "problems": [["community_unassigned", 1]], "counts": {"docs": 38, "chunks": 192, …}}
build done (incremental): {"docs": 38, "chunks": 192, … "last_build": {… "build_version" 3}}
```
`community_unassigned` 는 증분 빌드 후 정상 상태(ALERTS 로 올리지 않음). 다음 `build --full` 또는 `--incremental-communities` 로 해소.

**출력 — 문서 삭제 후 증분**
```
  · loaded 37 docs (0 read, 37 skipped by stat)
  diff   ↳ {"changed": 0, "unchanged": 37, "removed": 1, "new": 0, "renamed": 0}
  prune  ↳ {"orphan_entities": 1, "dangling": {…0…}, "stale_wiki_pages": 1}
  verify ↳ {"ok": true, "problems": [], "counts": {"docs": 37, "chunks": 186, "entities": 58, …}}
```

**출력 — build status / verify**
```
running=False
last_build: 2026-09-13 21:53:20 mode=incremental docs=38 changed=2  build_version=3
embed: done 13/13 failed=0 cache=0 batch=64 rate=3255.09/s eta=0.0s
lint: {"docs": 38, "errors": 0, "warnings": 4, "inferred": 2, "ts": …}
index: {"docs": 38, "chunks": 192, "embeddings": 192, "entities": 59, "relations": 303}

verify: OK (problems=0)
  ✔ fts_rows                 0      chunks=185 chunks_fts=185
  ✔ fts_orphans              0      chunks 에 없는 FTS 행
  ✔ embedding_coverage       0      provider=hash missing=0/185
  ✔ doc_meta_missing         0      doc_meta 없는 문서 (구버전 색인 → build --full)
  ✔ community_unassigned     0      커뮤니티 미배정 엔티티 (증분 빌드 후 정상; …)
  ✔ wiki_stale_pages         0      엔티티가 사라진 위키 페이지(편집 노트 없음)
counts: {"docs": 37, "chunks": 185, "entities": 57, "relations": 299, "embeddings": 185}
```
(총 18개 검사: fts_rows, fts_orphans, fts_missing, chunk_orphans, doc_nchunks, embedding_orphans, embedding_coverage, embedding_other_provider, mention_dangling, mention_no_entity, relation_dangling, relation_no_entity, entity_orphans, entity_fts, doc_meta_missing, doc_meta_orphans, community_unassigned, wiki_stale_pages. 문제가 있으면 exit 1, `[--fix 가능]` 표시 항목은 `--fix` 로 정리.)

**관련 토글/튜닝/설정**: build 토글 `rule_graph, llm_graph, embed, communities, community_summary, wiki_pages, incremental, explicit_relations, schema_lint, doc_vector, fts_trigram, mcp_sources`; 속도 토글 `stat_skip, idf_refit_incremental, incremental_communities, wiki_full_rewrite, warm_cache, fts_optimize, embed_adaptive, health_check, verify_after_build, precompute_after_build`; 설정 `chunk_max_chars, chunk_overlap_chars, embed_batch/embed_batch_max/embed_commit_every, embed_store_dtype, llm_graph_budget, build_lock_timeout, wal_checkpoint_mb`; 튜닝 `chunk_min_chars, tokenizer(변경 시 --full), wiki_min_degree, cooccur_*`.

**흔한 오류와 조치**
- `build aborted: health check failed: corpus_dirs (build --force 로 강행, --no-health-check 로 생략)` → exit 3. 원인 항목을 고치거나 `--force`.
- `build refused: another build is running: pid=… host=… since=…` → exit 2. `build status` 로 락 확인. 죽은 프로세스 락은 자동 회수(같은 호스트, pid 없음 또는 6h 경과). `build_lock_timeout` 으로 대기.
- `ALERTS: [{"check": "schema_lint", …}]` → `corpus lint` 로 확인.
- 임베더/차원 변경 후 `embedding_dim` warn, 검색 품질 급락 → `build --full`.
- 토크나이저(`tuning set tokenizer=kiwi`) 나 chunk 파라미터 변경 후에는 반드시 `build --full` (tuning show 에 `rebuild` 표시).

### 3.3 query

**예제**
```powershell
python -m llmwiki query "ISSUE-2001 의 원인과 수정 CL 은?" --trace
python -m llmwiki query "…" --json
python -m llmwiki query "…" --preset speed
python -m llmwiki query "…" --no-graph
python -m llmwiki query "…" --debug 2 --trace
python -m llmwiki query "…" --k 5 --no-log --no-rerank-llm --fallback-loop
```

**내부 단계** (`Pipeline.query` → `QueryEngine.run`; trace 단계명 그대로)
1. `sync_index` : `build_version` 이 바뀌었으면 메모리 캐시(벡터 행렬·IDF·규칙·질의 캐시) 폐기.
2. `providers` : answer/rerank(+expand, verify) 역할 LLM 과 임베더 최초 생성.
3. `cache_hit` (토글 `query_cache`, **프로세스 메모리**) → `precompute_hit|precompute_miss` (토글 `precompute`, 영속 `answer_cache`).
4. 계획: `time_scope`(한국어 시간 표현 → 날짜 범위, 질의에서 제거) → `query_rules`(acronym/synonym/alias/related/exclude 확장, 시드 후보) → `router`(keyword/relational/semantic/hybrid 분류 → 채널 가중치, `router_llm` 옵션, `doc_type_hints`) → 채널 배율 `channel_w_*` → `query_expand`(LLM, 옵션) → `pins`(pins.json 매칭).
5. 검색 라운드 `_retrieve` : `fts_search` + `fts_search_rules` + `fts_search_alt`×n + `fts_search_related` | `vector_search`×(1+alt) | `graph_search`(라우터 시드 + 규칙 시드, n-hop) | `doc_vector_search` | `mcp_enrich` → `rrf_fuse`(`fusion_method`) → pin 주입 → `boost`(문서유형·시간·최신성·pin·provenance·피드백·exclude) → `rerank_llm`|`rerank_local`(토글 `rerank`, `rerank_llm`) → `context`(top_k_final, trim/dedupe/neighbors).
6. `evidence_check` : 휴리스틱 `evidence.assess` → verdict `sufficient|weak|insufficient` (근거 없음 / 키워드 커버리지 < `evidence_min_cover` / 질의의 문서 ID 가 근거에 없음 → insufficient; 컨텍스트 짧음·커버리지<1·top fused 낮음·채널 합의 부족 → weak). `fallback_loop` 토글이면 `fallback`(rules→expand→graph→wide→mcp) 라운드를 예산 내에서 반복.
7. 답변: insufficient 면 `answer_insufficient`(LLM 생략), 아니면 `evidence_compress`(옵션) → `answer_llm`|`answer_extractive`(`generate_answer`). weak 면 답변 앞에 `> ⚠ 근거가 약합니다 …` 를 붙임.
8. `claim_check` : 답변 문장별 인용·지원 검증 → groundedness; `claim_policy`(mark/drop/refine), `claim_min_groundedness` 미만이면 `> ⚠ groundedness …` 경고 삽입.
9. `evolve_capture`(log & `evolve_capture`) → 제안 생성; `query_log`, `requests` 기록; `forensic_auto` (verdict≠sufficient 또는 groundedness 낮음) → `forensics` 자동 기록 + WARNING 로그; `memory.record_episode`; 캐시 저장.

**출력 — `--trace`** (앞부분 요약 + trace)
```
Q: ISSUE-2001 의 원인과 수정 CL 은?
route: "relational" weights: {'fts': 1.3, 'vector': 0.9, 'graph': 0.85, 'fts_rule': 1.04, 'fts_alt1': 1.04, …}
----------------------------------------------------------------------
(mock answer) 컨텍스트 기반 요약입니다. [C1] [C2] [C3]
----------------------------------------------------------------------
[C1] corpus/tc/TC-RX-DMA-001.md#1 | RX DMA 검증 TC > 목적 | fused=0.1228 rerank=16.0 via fts#2,fts_alt3#2,…
[C2] corpus/issues/ISSUE-2001.md#2 | DMA underrun 발생 후 PHY 재시작 실패 > 원인 | fused=0.1210 rerank=15.0 via fts_alt2#2,fts#3,…
…
graph seeds: [('ISSUE-2001', 7.0), ('CL-55309', 4.26), …]
total 21.9 ms | llm=mock embed=hash | tokens=1562 | query_id=1 request_id=3
======================================================================
query                      21.9 ms       llm=2 tok=1526/36 sql=1315
  sync_index                  0.0 ms    0% sql=2
  providers                   0.2 ms    1% sql=1
    ↳ {"created": ["answer", "rerank", "embedder"], "answer": "mock/mock", "rerank": "mock/mock", "embedder": "hash d=512"}
  time_scope                  4.3 ms   20%
    ↳ {"timezone": "Asia/Seoul", "expr": null}
  query_rules                 1.5 ms    7%
    ↳ {"fired": 3, "types": ["acronym", "synonym"], "alt": 4, "related": 0, "exclude": [], "seeds": ["CL", "Change List"]}
  router                      0.6 ms    3% sql=39
    ↳ {"kind": "relational", "weights": {"fts": 1.3, "vector": 0.9, "graph": 0.85}, "keywords": ["issue-2001", "원인", "수정", "cl"], …
  query_expand                0.0 ms        (skipped: disabled)
  pins                        0.2 ms    1%
  fts_search                  0.7 ms    3% sql=185
    ↳ {"k": 12, "mode": "tiered", "hits": 12, "tiers": ["AND:0", "OR:12"], …
  fts_search_rules            0.7 ms    3% sql=227
  fts_search_alt              0.5 ms    2% sql=193      (×4)
  vector_search               1.6 ms    7% sql=2
    ↳ {"k": 12, "provider": "hash", "matrix_cache": "miss (loaded 1 ms)", "n_vectors": 185, …
  graph_search                3.5 ms   16% sql=99
    ↳ {"k": 12, "hops": [{"hop": 1, "frontier_in": 6, "relations": 86, "new_nodes": 25, …}, {"hop": 2, …}], "seeds": …
  rrf_fuse                    0.2 ms    1%
  boost                       0.1 ms    1% sql=2
    ↳ {"time_mode": null, "doc_types": ["issue", "cl"], "pins": 0, "router_type": 23, "provenance": 5, …
  rerank_llm                  0.4 ms    2% llm=1 tok=624/22
    ↳ {"candidates": 16, "model": "mock", "role": "rerank", "prompt_chars": 2011, "order": [0, 1, 2, 3, 4, 5, 6, 7]}
  context                     0.2 ms    1%
    ↳ {"max_chars": 9000, "trim": true, "dedupe": true, "chars": 1299, "citation…
  evidence_check              0.2 ms    1%
    ↳ {"llm": false, "verdict": "sufficient", "heuristic": "sufficient", "score": 1.0, "reasons": [], …
  answer_llm                  0.1 ms    1% llm=1 tok=902/14
  claim_check                 0.1 ms    1%
    ↳ {"llm": false, "policy": "mark", "n_factual": 0, … "groundedness": 1.0, …
  evolve_capture              0.0 ms    0%
summary: total=21.9 ms, llm calls=2 tokens=1562 (in 1526 / out 36), sql=1309, slowest=time_scope 20%, graph_search 16%, vector_search 7%
```

**출력 — 같은 질의 재실행** : CLI 는 프로세스마다 새로 뜨므로 메모리 질의 캐시는 비어 있다. 두 번째 실행도 `query_id=2 request_id=4` 로 정상 실행되며 `[cached]` 가 붙지 않는다 (캐시 히트는 `serve`/`mcp` 처럼 한 프로세스가 오래 살 때만). 영속 캐시가 필요하면 `--precompute` 토글 + `precompute run`.

**출력 — `--preset speed`** : LLM 리랭크 대신 로컬 리랭크(rerank 값이 0~1 점수), top_k 축소, 토큰 1562 → 846.
```
[C1] corpus/weekly/WR-2026-W33.md#2 | … | fused=0.0656 rerank=0.5796 via fts_rule#1,graph#2,fts_alt1#4,fts#10
[C2] corpus/issues/ISSUE-2001.md#4 | DMA underrun 발생 후 PHY 재시작 실패 > 수정 | fused=0.0706 rerank=0.5223 via …
[C3] corpus/cls/CL-55301.md#1 | rx_dma 수정: FIFO 임계값 설정 오류 > 변경 내용 | fused=0.0649 rerank=0.5088 via …
total 20.5 ms | llm=mock embed=hash | tokens=846 | query_id=3 request_id=5
```

**출력 — `--no-graph`** : 그래프 채널이 빠지자 키워드 커버리지 0.75 로 verdict=weak, groundedness 경고와 함께 답변. 이 요청은 `forensic_auto` 로 forensics #1 에 자동 기록됐다(§3.14).
```
> ⚠ groundedness 0.00 — 일부 문장이 근거로 확인되지 않습니다.

> ⚠ 근거가 약합니다 (keyword coverage 0.75). [미확인: 근거에서 확인되지 않음] 아래 답변은 제한된 근거에 기반합니다.

(mock answer) 컨텍스트 기반 요약입니다. [C1] [C2] [C3]
…
total 17.7 ms | llm=mock embed=hash | tokens=1056 | query_id=4 request_id=6
```

**출력 — `--json`** (`{"result": {...}, "trace": {...}}`; result 에 query, answer, answer_mode, cited, hits[{chunk_id, scores, ranks, fused, why, boosts, n, in_context, doc_type, ext_id, date, …}], route, graph, plan, evidence, fallback, claims, groundedness, config, request_id, query_id, run_id, forensic)
```
{
  "result": {
    "query": "CL-55302 는 어떤 이슈를 수정했나?",
    "answer": "> ⚠ groundedness 0.00 — …\n\n(mock answer) 컨텍스트 기반 요약입니다. [C1] [C2] [C3]",
    "answer_mode": "llm",
    "cited": [1, 2, 3],
    "hits": [
      { "chunk_id": "corpus/issues/ISSUE-2002.md#3",
        "scores": {"fts": 9.6495, "fts_rule": 12.7108, "vector": 0.1272, "graph": 2.22, …},
        "ranks": {"fts": 1, "fts_rule": 1, …}, …
```

**출력 — `--debug 2 --trace`** : trace 의 각 단계 아래에 `⚙`(debug 메타 원문: fired 규칙, FTS match 식, all_hits …) 와 `▶`(프롬프트/응답 샘플) 줄이 추가된다. `requests show <id>` 는 항상 verbose 로 출력하므로 나중에도 볼 수 있다.
```
  query_rules …
    ⚙ {"fired": [{"type": "acronym", "matched": "CL", "canonical": "CL", "values": ["Change List"]}, {"type": "synonym", "matched": "이슈", …
  fts_search …
    ⚙ {"match": "\"cl-55302\" OR \"는\" OR \"어떤\" OR \"이슈\" …", "expanded_query": null, "keywords": ["cl-55302", "이슈", "수정했"], "all_hits": [[…
```

**관련 토글/튜닝/설정**: 검색 `fts, vector, graph, router, router_llm, time_scope, query_rules, query_expand, query_decompose, pins, doc_vector, rerank, rerank_llm`; 근거/답변 `evidence_check, evidence_check_llm, fallback_loop, llm_answer, evidence_compress, claim_check, claim_check_llm, answer_refine, forensic_auto`; 토큰/지연 `query_cache, precompute, context_trim, dedupe_hits, feedback_boost`; 설정 `top_k_*, graph_hops, rrf_k, rerank_candidates, rerank_chunk_chars, context_max_chars, context_chunk_chars, answer_max_tokens, timezone, week_start`; 튜닝 단계 `time_scope, query_rules, router, fts_search, vector_search, graph_search, rrf_fuse, rerank, context, evidence, answer, claim` (`tuning show --stage <단계>`).

**흔한 오류와 조치**
- 최종 hit 중 컨텍스트에 들어가지 못한 청크(`dedupe_hits` 로 중복 제거되거나 `context_max_chars` 초과)는 `n=None` 이며 텍스트 출력에서 `[--]` 로 표시된다 (`--json` 에서는 `"n": null, "in_context": false`). 이 문서 작성 중 이 경우 `TypeError: %d format …` 로 출력이 끊기는 버그가 실측되어 수정했다.
- `> ⚠ 근거가 약합니다` / `insufficient` 응답 → `--trace` 의 `evidence_check ↳ reasons` 확인 → §4(c) 절차.
- 결과가 빌드 전 상태로 보임 → `sync_index ↳ reloaded_caches` 확인; 서버 프로세스라면 `maintenance clear_cache`.
- LLM 사용 불가 → `answer_extractive` 폴백(`llm=none`): 답변 위에 `ℹ LLM 미사용` 안내가 붙고, 근거 원문 문장으로 `## 핵심 → ## 상세 (근거 문서별) → ## 관련 관계 → ## 근거(표) → ## 미확인` 구조를 채운다. 문서별 문장 수는 `answer_length_target`(short 1 / normal 3 / long 5), 핵심 문장 수는 `extractive_sentences`. `models test` 로 LLM 연결 확인.

### 3.4 search fts | vector | graph

**예제**
```powershell
python -m llmwiki search fts    "RX DMA underrun PHY 재시작" --k 3
python -m llmwiki search vector "RX DMA underrun PHY 재시작" --k 3
python -m llmwiki search graph  "RX DMA underrun PHY 재시작" --k 3 --json
```
**내부 단계**: `Profiler("search")` 하나에 `retrieval.fts_search`(동의어 = DB synonyms 테이블, 라우터/규칙 확장 없음) / `vector_search(embedder)` / `graph_search(graph_hops)` 단일 채널만 실행. 텍스트 모드는 result 만, `--json` 은 `{"result","trace"}`. requests 는 남기지 않고 logs 에 `search` 줄만 남는다.

**출력**
```
[ {"chunk_id": "corpus/issues/ISSUE-2009.md#0", "score": 14.375310455241227, "snippet": "# ISSUE-2009 [DMA] [underrun] 발생 후 [PHY] [재시작] 실패"},
  {"chunk_id": "corpus/issues/ISSUE-2001.md#0", "score": 14.337117463258274, "snippet": "# ISSUE-2001 [DMA] [underrun] …"}, … ]
# vector
[ {"chunk_id": "corpus/issues/ISSUE-2009.md#0", "score": 0.5356692671775818}, {"chunk_id": "corpus/issues/ISSUE-2001.md#0", "score": 0.533…}, … ]
# graph
{"chunks": [["corpus/issues/ISSUE-2009.md#3", 13.898…], ["corpus/issues/ISSUE-2001.md#3", 13.26125], ["corpus/cls/CL-55301.md#1", 8.36…]],
 "seeds": [["ISSUE-2009", 12.9], ["ISSUE-2001", 12.9], ["TC-RX-DMA-009", 7.13], …], "entities": [...], "relations": [...]}
```
**관련 튜닝**: `fts_search`(fts_mode, prf_*, fts_w_*), `vector_search`, `graph_search`(hop 감쇠·허브 페널티) 단계. **오류**: graph 에서 `seeds` 가 비면 결과도 빈 배열 → 엔티티 사전(`data/rules.json`)/`explicit_relations` 확인.

### 3.5 eval

**예제**
```powershell
python -m llmwiki eval --k 5 --questions <corpus>\questions.json
python -m llmwiki eval --matrix --questions <corpus>\questions.json
python -m llmwiki eval --json --no-rerank-llm
```
**내부 단계** (`Pipeline.evaluate`): 질문셋(`--questions` 또는 `<ROOT>/eval/questions.json`) 각 항목 `{"q","expect_docs","expect_terms"}` 에 대해 `query(log=False)` 실행 → `evalset.score_result` : top-k `chunk_id` 에 `expect_docs` 부분 문자열이 있으면 hit/rank(→MRR), top-k 청크 텍스트에 `expect_terms` 포함 비율 = term_recall, 답변 텍스트 포함 비율 = answer_term_recall → `aggregate` + total_tokens/avg_ms → `requests` 에 kind=eval 1건 (+ 질문마다 query 1건). `--matrix` 는 `(fts, vector, graph)` 6조합으로 토글을 바꿔가며 반복.

**출력**
```
✔ rank=2 term=0.50  ISSUE-2001 의 원인과 수정 CL 은?
✔ rank=2 term=1.00  RX DMA underrun 발생 시 PHY 재시작 실패 원인
✘ rank=None term=0.00  CL-55302 는 어떤 이슈를 수정했나?
✔ rank=3 term=1.00  AGC 수렴 지연 이슈의 원인은?
…
summary: {"n": 25, "hit@k": 0.96, "mrr": 0.813, "term_recall": 0.86, "answer_term_recall": 0.04, "total_tokens": 36352, "avg_ms": 8.5}

# --matrix
fts          hit@5=0.920 mrr=0.833 term_recall=0.900
vector       hit@5=0.920 mrr=0.803 term_recall=0.960
graph        hit@5=0.840 mrr=0.669 term_recall=0.760
fts+vector   hit@5=0.920 mrr=0.807 term_recall=0.900
fts+graph    hit@5=0.880 mrr=0.789 term_recall=0.820
all          hit@5=0.960 mrr=0.813 term_recall=0.860
```
**오류**: `FileNotFoundError: questions file not found` → 경로 확인. `--questions` 를 생략하면 프로젝트의 `eval/questions.json` 이 쓰이며, 없으면 HBM 예제 질문셋이 생성된다(모뎀 코퍼스와 무관 → 전부 ✘).

### 3.6 trial run / list / compare / report / show

**예제**
```powershell
python -m llmwiki trial run --name baseline --questions Q.json --note "initial"
python -m llmwiki trial run --name speed --preset speed --questions Q.json
python -m llmwiki trial run --name k6 --set top_k_final=6 --set rerank_llm=false --questions Q.json
python -m llmwiki trial list
python -m llmwiki trial compare baseline speed        # markdown 표
python -m llmwiki trial report baseline               # 질문별 행
python -m llmwiki trial show baseline --json          # 설정 스냅샷 포함 원본
```
**내부 단계** (`trials.run_trial`): 현재 settings/tuning 스냅샷 → `--preset`/`--set`(settings·toggles·tuning 키 모두 허용) 임시 적용 → 질문마다 `query()` → 지표 12종(hit@k, mrr, term_recall, answer_term_recall, groundedness, citation_precision, insufficient_rate, fallback_rate, avg_ms, p95_ms, tokens_per_query, embed_coverage) → `trials` 테이블에 설정 스냅샷·build_version·질문셋 해시·질문별 행(request_id 포함) 저장 → 설정 복원. `compare` 는 첫 trial 기준 Δ, 질문별 승/패, 설정 diff, 추천(`HIGHER_BETTER` 기준 ★).

**출력**
```
trial #1 baseline: {"n": 25, "hit@k": 0.96, "mrr": 0.813, "term_recall": 0.86, … "groundedness": 0.625, "insufficient_rate": 0.04, "p95_ms": 10.53, "tokens_per_query": 1454.1, "embed_coverage": 1.0}
trial #2 speed:    {"n": 25, "hit@k": 0.96, "mrr": 0.786, "term_recall": 0.8, … "tokens_per_query": 672.0, …}

#2    speed                    v2   n=25  hit=0.96  mrr=0.786 g=0.652 insuf=0.08  ms=7.3     09-13 21:51
#1    baseline                 v2   n=25  hit=0.96  mrr=0.813 g=0.625 insuf=0.04  ms=8.4     09-13 21:51

# Trial 비교: baseline vs speed
| 지표 | baseline | speed | Δ(vs baseline) |
|---|---|---|---|
| hit@k | 0.96 | 0.96 | +0.000 |
| mrr | 0.813 | 0.786 | -0.027 |
| groundedness ★ | 0.625 | 0.652 | +0.027 |
| avg_ms ★ | 8.4 | 7.3 | -1.100 |
| tokens_per_query ★ | 1454.1 | 672.0 | -782.100 |
## 질문별 승/패 (vs baseline)
- speed: win 3 / loss 6 / tie 16
## 설정 차이
- preset: "" → "speed"
- settings.top_k_final: 8 → 6
- toggles.rerank_llm: true → false
- tuning.rerank_method: null → "local"
## 추천
- groundedness: speed 가 최선 (0.652)
```
**오류**: `trial not found` (exit 1), `error: need ≥2 trials` (compare 에 존재하지 않는 이름, exit 1). 이름은 최신 trial 을 가리키므로 같은 이름을 재사용하면 이전 것은 id 로만 접근.

### 3.7 graph

**예제**
```powershell
python -m llmwiki graph --limit 10
python -m llmwiki graph --provenance explicit --limit 8     # 관계 출처 필터 + 엣지 출력
python -m llmwiki graph --community 2 --types issue,cl --json
```
**내부 단계** (`Pipeline.graph_export`): `sync_with_db()` → 엔티티 상위 limit×3 에서 date/amount 제외 → community/types 필터 → 두 노드가 모두 포함된 관계 중 `mentions_date/amount` 제외, `--provenance`(explicit,rule,human,llm,cooccur) 필터 → (src,dst,rel) 병합(weight 합, confidence max) → communities, provenance_counts.

**출력**
```
nodes=10 edges=46 communities=10 provenance={"cooccur": 156, "explicit": 51, "rule": 92}
  ISSUE-2002                               issue      deg=30  C0 [rule]
  RULE-REG-002                             coding_rule deg=29  C1 [rule]
  ISSUE-2001                               issue      deg=27  C2 [rule]
  …
C1 (n=12): 핵심 엔티티: RULE-REG-002, WR-2026-W34, ISSUE-2006, …
C2 (n=4): 핵심 엔티티: ISSUE-2001, WR-2026-W33, CL-55301, TC-RX-DMA-001

# --provenance explicit (엣지 40개까지 출력)
  e:cl-55301 -[fixes]-> e:issue-2001  (explicit w=1.00 conf=0.98)
  e:issue-2001 -[fixed_by]-> e:cl-55301  (explicit w=1.00 conf=0.98)
  e:wr-2026-w33 -[references]-> e:issue-2001  (explicit w=1.00 conf=0.98)
```
**관련 토글/튜닝**: `explicit_relations`(front matter related.*/ID 패턴 → explicit/rule), `rule_graph`, `llm_graph`, `communities`; 튜닝 `graph_build` 단계, `provenance_boost`.

### 3.8 entity

```powershell
python -m llmwiki entity ISSUE-2001
python -m llmwiki entity e:issue-2001 --json
```
`graph_rules.entity_id_for(name)` 로 id 변환 → `Pipeline.entity_detail` (doc_refs 없으면 즉시 계산·저장) → 관계 60개·멘션 10개.
```
ISSUE-2001 (issue) src=rule deg=27 C2
문서: corpus/issues/ISSUE-2001.md · DMA underrun 발생 후 PHY 재시작 실패
  ISSUE-2001 -[fixed_by]-> CL-55301  w=1.00 front matter related.cls
  ISSUE-2001 -[related_issue]-> ISSUE-2004  w=1.00 front matter related.issues
  ISSUE-2001 -[co_occurs]-> CL-55301  w=1.00 주간 업무 보고 WR-2026-W33 > 이슈 요약
  CL-55301 -[fixes]-> ISSUE-2001  w=1.00 CL-55301 본문에서 ISSUE-2001 언급 (…)
  TC-RX-DMA-001 -[verifies]-> ISSUE-2001  w=1.00 front matter related.issues
```
없으면 `not found. candidates: [...]` (entities_fts 검색) 후 exit 1.

### 3.9 corpus lint / types / schema / example / lint-file / stats

```powershell
python -m llmwiki corpus lint [--all] [--limit 100]
python -m llmwiki corpus types
python -m llmwiki corpus schema issue
python -m llmwiki corpus example issue > new_issue.md
python -m llmwiki corpus lint-file corpus\issues\ISSUE-2001.md
python -m llmwiki corpus stats
```
**내부**: `lint` 는 빌드가 `doc_meta` 에 저장한 lint 결과(`store.lint_rows`) 와 `kv.lint_summary` 를 읽는다(빌드 후에만 의미 있음). `types/schema/example` 은 `schema.load_schemas()`(schemas/*.json, common.json 상속). `lint-file` 은 파일을 직접 읽어 `normalize_meta` + `lint_document` 를 즉시 수행(빌드 불필요, error 있으면 exit 1). `stats` 는 doc_type 분포·lint 요약·provenance 수.
```
lint summary: {"docs": 37, "errors": 0, "warnings": 4, "inferred": 2, "ts": …}
△ corpus/README.md                                   type=-             id=-              err=0 warn=2 (inferred)
      [warn] front_matter: front matter 없음 (유형/ID/날짜는 추론됨: ? / ? / mtime)
      [warn] doc_type: doc_type 을 알 수 없음

cl             Change List (수정 반영)                      id=^CL-\d{3,8}$
                required: schema_version, doc_type, id, title, date, related
issue          이슈 문서 (현상 · 원인 · 분석 · 수정)                id=^ISSUE-\d{3,7}$
                required: schema_version, doc_type, id, title, date, status
…(coding_rule, hw_design, sw_design, tc_list, weekly_report)

doc_type=issue id=ISSUE-2001 date=2026-06-04(front_matter) inferred=False
  (no issues)
doc_type= id= date=2026-09-02(filename) inferred=True
  [warn] front_matter: front matter 없음 (…)
```
**토글**: `schema_lint`(빌드 시 검사). **오류**: `cannot read <path>` (exit 1) → 지원 확장자/경로 확인.

### 3.10 embed report / status / runs / clear-cache

```powershell
python -m llmwiki embed report
python -m llmwiki embed status     # kv.embed_progress (진행 중 빌드 모니터링)
python -m llmwiki embed runs       # embed_runs 최근 20건
python -m llmwiki embed clear-cache
```
`embed_run.embed_report` : 임베더 정보, doc_type 별 coverage(embedded/chunks), 누락 샘플, embedding_cache 상태, 진행률, 최근 run.
```
embedder: hash/hash-ngram-512 dim=512 dtype=float32
coverage: 186/186 = 100.0%
  cl                40/40    100.0%
  issue             61/61    100.0%
  …
cache: {"entries": 0, "note": "hash 임베더는 캐시 미사용"}
progress: done 4/4 failed=0 batch=64
run 345ae5c8a07d done total=4 done=4 failed=0 cache=0 batches=1 avg=0.5ms final_batch=64 alerts=0
run c69ce981b3f7 done total=185 done=185 failed=0 cache=0 batches=3 avg=6.3ms final_batch=97 alerts=0
```
coverage < 100% 이면 다음 `build` 가 누락분만 재임베딩(`resume_missing`). 실패가 반복되면 `embed_batch` 축소, `embed_adaptive` 확인.

### 3.11 rules show / add / test / remove / stats / path

```powershell
python -m llmwiki rules show
python -m llmwiki rules add related "블루투스" "BT" "bluetooth"     # <type> <term> <values…>
python -m llmwiki rules test "블루투스 페어링"
python -m llmwiki rules remove related "블루투스" [value]
```
type ∈ acronym | synonym | alias | related | exclude | compound. `test` 는 `query_rules.expand()` 를 튜닝값(`syn_w`, `related_w`, `acronym_phrase`)으로 실행해 질의 엔진의 `query_rules` 단계와 같은 결과를 보여준다.
```
{"type": "related", "term": "블루투스", "values": ["BT", "bluetooth"], "source": "manual"}

{ "query": "블루투스 페어링", "fts_query": "\"블루투스\" OR \"페어링\"", "alt_queries": [],
  "related": [["BT", 0.4], ["bluetooth", 0.4]], "exclude": [], "seeds": [],
  "fired": [{"type": "related", "matched": "블루투스", "values": ["BT", "bluetooth"]}] }

removed        # exit 0
not found      # exit 1
```
compound 사전은 토크나이저에도 쓰이므로 compound 변경 후에는 `build --full`. `usage: rules add <acronym|…>` (인자 부족, exit 1).

### 3.12 pin add / list / test / remove

```powershell
python -m llmwiki pin add --doc CL-55302 --keywords "CL-55302" --note "…"
python -m llmwiki pin add --chunk "corpus/coding_rules/RULE-ISR-001.md#1" --doc-types coding_rule --always --weight 2
python -m llmwiki pin list
python -m llmwiki pin test "CL-55302 는 어떤 이슈를 수정했나?"
python -m llmwiki pin remove p1
```
`pins.add_pin` 은 `p<n>` id 로 pins.json 에 저장. 조건 `when` = always | keywords | query | doc_types. `test` = 질의 엔진 `pins` 단계의 `match_pins` 결과(`weights`, `inject`, `matched`).
```
p1    doc=CL-55302 chunk=None when={"keywords": ["CL-55302"]} w=1.00 strength=1.00 hits=0 CL-55302 질의는 …
{ "weights": {"corpus/cls/CL-55302.md#0": 1.0, "corpus/cls/CL-55302.md#1": 1.0, …},
  "inject": ["corpus/cls/CL-55302.md#0", "corpus/cls/CL-55302.md#1", "corpus/cls/CL-55302.md#2"],
  "matched": [{"id": "p1", "doc": "CL-55302", …}] }
```
질의에서는 `why` 에 `pin` 이 추가되고 `boosts.pin` 배율(실측 10.5; 튜닝 `pin_boost`=10.0 과 pin weight 에서 계산)이 fused 점수에 곱해져 상위로 올라온다. `--doc 또는 --chunk 필요` (exit 1).

### 3.13 precompute run / status / clear / doc-vectors

```powershell
python -m llmwiki precompute run --from-log 20     # 평가셋 + 최근 질의 로그 N개 사전 계산
python -m llmwiki precompute status
python -m llmwiki precompute clear [--stale]       # stale = 현재 build_version 이 아닌 항목만
python -m llmwiki precompute doc-vectors           # 문서 카드 임베딩 재생성 (doc_vector 채널)
```
`precompute.run` 은 각 질문을 `query()` 로 실행해 `answer_cache(build_version, signature)` 에 저장. 질의 시 `toggles.precompute` 가 켜져 있어야 `precompute_hit` 로 사용된다.
```
  · precompute 1/26: ISSUE-2001 의 원인과 수정 CL 은?
  …
{"entries": 50, "current_version": 50, "hits": 0, "build_version": 2}
{"removed": 0}      # --stale
{"removed": 50}
{"doc_vectors": 37, "provider": "hash"}
```

### 3.14 forensic last / list / summary / <request_id> [--llm]

```powershell
python -m llmwiki forensic last
python -m llmwiki forensic list --limit 30
python -m llmwiki forensic summary
python -m llmwiki forensic 7 --llm
```
**내부**: `forensics` 테이블은 (a) 질의의 `forensic_auto` 가 자동 기록, (b) `forensic <request_id>` 가 수동 기록. `forensic.diagnose(trace, result)` 가 trace 의 단계별 메타(evidence_check reasons, claim_check unsupported, answer 길이, 채널 hit 수 …)를 규칙으로 해석해 findings/suggestions 를 만든다. `--llm` 은 forensic 역할 LLM 소견을 추가. `last` 는 forensics 가 비어 있으면 마지막 query 요청을 즉시 진단한다.
```
forensic #4 request=9 run=b07b5a526234 verdict=weak groundedness=0.0
Q: CL-55302 는 어떤 이슈를 수정했나?
findings:
  [warn] evidence_check 근거 판정 weak: keyword coverage 0.67  ← {"top_fused": 0.234, "channels": 10, "cover": 0.667, "chars": 1239, "n_hits": 8, "ids": ["CL-55302"], "id_hit": true, …
  [info] answer_llm     답변이 근거 대비 매우 짧음 (43자 / 근거 1239자)  ← {"length_target": "normal"}
  [error] claim_check    근거가 지지하지 않는 문장 1개 (groundedness 0.00)  ← ["> ⚠ 근거가 약합니다 (keyword coverage 0.67)."]
suggestions:
  - (tuning 0.40) answer_length_target=long 또는 answer_guide.md 상세도 지시 확인
  - (tuning 0.50) claim_policy=drop 또는 answer_refine 활성화, answer_guide 강화

#4    req=9     weak         g=0.0   CL-55302 는 어떤 이슈를 수정했나?
#1    req=6     weak         g=0.0   ISSUE-2001 의 원인과 수정 CL 은?

{"n": 4, "by_verdict": {"weak": 4}, "top_topics": [["cl-55302", 2], …], "suggestion_kinds": {"tuning": 8},
 "problem_stages": [["evidence_check", 4], ["claim_check", 4]]}
```
**오류**: `request 99999 not found` (exit 1). `forensic run <request_id>` 와 `forensic <request_id>` 는 동일하다 (이 문서 작성 중 `run` 형태가 `unrecognized arguments` 로 실패하는 버그가 실측되어 파서에 `args` 를 추가했다).

### 3.15 time

```powershell
python -m llmwiki time "지난주 리뷰한 CL"
python -m llmwiki time "2026년 8월 AGC 이슈"
```
`timeparse.parse(text, timezone, week_start)` = 질의 엔진 `time_scope` 단계와 동일.
```
{"from": "2026-08-31", "to": "2026-09-06", "expr": "지난주", "query": "리뷰한 CL", "kind": "week-1", "from_ts": …, "to_ts": …}
{"from": "2026-08-01", "to": "2026-08-31", "expr": "2026년 8월", "query": "AGC 이슈", "kind": "ym", …}
{"expr": null}       # 시간 표현 없음
```

### 3.16 fusion show / compare

```powershell
python -m llmwiki fusion show
python -m llmwiki fusion compare --methods "rrf,zscore,dbsf" --k 5 --questions Q.json
```
`show` 는 현재 `fusion_method` 와 부스트 튜닝값. `compare` 는 방식별로 `fusion.compare_methods` 가 평가셋을 실행.
```
{"method": "rrf", "choices": ["rrf", "weighted", "minmax", "zscore", "dbsf", "rrf_boost"],
 "boosts": {"doc_type_boost": "", "pin_boost": 10.0, "provenance_boost": 0.2, "feedback_boost_w": 0.15, "time_boost_w": 0.5, "recency_half_life_days": 0, "exclude_penalty": 0.5}}

rrf        hit@5=0.960 mrr=0.813 term_recall=0.860 avg_ms=8.4
zscore     hit@5=0.960 mrr=0.830 term_recall=0.820 avg_ms=8.0
dbsf       hit@5=1.000 mrr=0.843 term_recall=0.820 avg_ms=8.0
```
채택: `tuning set fusion_method=dbsf`. (PowerShell 에서 `--methods` 값은 반드시 따옴표.)

### 3.17 memory status / decay / consolidate / episodes

```powershell
python -m llmwiki memory status
python -m llmwiki memory decay          # proposals/pins strength 감쇠 (memory_half_life_days, memory_archive_strength)
python -m llmwiki memory consolidate    # 반복 포렌식 소견 → 제안 승격 (forensic_min_events)
python -m llmwiki memory episodes --limit 30
```
```
{"episodes": 0, "episodes_with_feedback": 0, "proposals": {}, "avg_strength_proposed": 0, "feedback_chunks": 0, "half_life_days": 60, "forensics": 0}
{"proposals_decayed": 0, "proposals_archived": 0, "pins_decayed": 0}
{"groups": 8, "proposals": [1, 2, 3, 4, 5, 6], "min_events": 3}      # consolidate → evolve status 에 corpus_gap/alias/tuning 제안
```
토글 `memory_decay`, `evolve_from_forensics`, `feedback_boost`.

### 3.18 preset list / show / diff / apply

```powershell
python -m llmwiki preset list
python -m llmwiki preset show speed
python -m llmwiki preset diff speed
python -m llmwiki preset apply speed              # 이번 프로세스만 (사실상 확인용)
python -m llmwiki preset apply quality token --save   # config.json + tuning.json 에 저장
```
```
quality        품질 최적화 — LLM 개입 단계(확장·근거 판정·claim 검증·fallback)를 모두 켜고 …
speed          속도 최적화 — LLM 호출을 답변 1회로 줄이고 후보 수·홉 수를 낮춤. 캐시·프리컴퓨트 사용.
token          토큰 최적화 — …
offline        오프라인 — LLM/외부 API 없이 추출식 답변·로컬 리랭크·규칙 확장만 사용.
deep_research  심층 조사(unified search) — …

* toggles.rerank_llm               True → False
* toggles.precompute               False → True
* top_k_final                      8 → 6
* graph_hops                       2 → 1
* tuning.fts_mode                  tiered → or
* tuning.rerank_method             auto → local

applied ['speed']: toggles=13 tuning=5 settings=8 conflicts=0 (이번 프로세스만; --save 로 저장)
applied ['nosuch']: … unknown: ['nosuch']
```
`--save` 없는 `preset apply` 는 프로세스 종료와 함께 사라진다. 한 번만 쓰려면 다른 명령에 `--preset speed` 를 붙인다.

### 3.19 prompts list / show / reset / path

```powershell
python -m llmwiki prompts list
python -m llmwiki prompts show answer_system
python -m llmwiki prompts reset rerank
python -m llmwiki prompts path rerank
```
이름: answer_system, answer_guide, rerank, expand, extract, summarize, review, evidence_check, claim_check, forensic, router, compress. 파일이 없으면 기본값으로 생성. 답변 시스템 프롬프트 = answer_system + answer_guide.
```
answer_system       457 chars (default) <tmp>\prompts\answer_system.md
answer_guide        824 chars (default) <tmp>\prompts\answer_guide.md
rerank              139 chars (default) …
```
`prompts show <없는이름>` 은 빈 줄만 출력(exit 0) — 이름 오타에 주의. `name 필요` (exit 1).

### 3.20 logs tail / grep / files / dir

```powershell
python -m llmwiki logs files
python -m llmwiki logs tail -n 50 [--file llmwiki|error|build|query]
python -m llmwiki logs grep --request 3 -n 8
python -m llmwiki logs grep --run 345ae5c8a07d
python -m llmwiki logs grep --file build --text loaded --since 10m
python -m llmwiki logs grep --level WARNING --json
python -m llmwiki logs dir
```
`logging_setup`: llmwiki.log(전체), error.log(WARNING+), build.log(build/watch), query.log(query/search/eval/trial). 각 줄 = 시각·레벨·kind·run_id·메시지·JSON data. `--request <id>` 는 requests 행의 run_id 로 변환(구버전 기록이면 `request N has no run_id`, exit 1). `--since` 는 `30m|2h|1d`.
```
build.log           10625 B  09-13 21:49:38
error.log               0 B  09-13 21:48:56
llmwiki.log         14144 B  09-13 21:49:50
query.log            2672 B  09-13 21:49:50

2026-09-13 21:50:28 INFO    query  622f0912a916 query start {"run_id": "622f0912a916"}
2026-09-13 21:50:28 INFO    query  622f0912a916 query: ISSUE-2001 의 원인과 수정 CL 은?
2026-09-13 21:50:28 INFO    query  622f0912a916 router done 0.6ms {"stage": "router", "ms": 0.6, "enabled": true, "meta": "{'kind': 'relational'}", …}
2026-09-13 21:50:28 INFO    query  622f0912a916 llm call {"provider": "mock", "model": "mock", "role": "rerank", "ms": 0.1, "input_tokens": 624, …}
2026-09-13 21:50:28 INFO    query  622f0912a916 query finish 21.9ms {"run_id": "622f0912a916", "total_ms": 21.91, "llm_calls": 2, "tokens": 1562, "sql": 1309, "errors": [], "skipped": ["query_expand", "doc_vector_search"]}

2026-09-13 21:50:29 WARNING                     forensic recorded #1 (error) {"request_id": 6, "verdict": "weak"}
```
설정 `log_level, log_max_mb, log_backups, log_console`, 토글 `log_stages`(단계 종료 로그).

### 3.21 requests list / last / show

```powershell
python -m llmwiki requests list [--kind query|build|eval|search] [--limit 30]
python -m llmwiki requests last
python -m llmwiki requests show 9 [--json]
```
```
#9     09-13 21:51:10 query      20.0 ms llm=2 tok=1614/36 sql=843   CL-55302 는 어떤 이슈를 수정했나?
#7     09-13 21:51:09 query      19.2 ms llm=2 tok=1473/36 sql=444   블루투스 페어링 실패 원인은?
#2     09-13 21:49:38 build      17.8 ms llm=0 tok=0/0 sql=335   incremental build: 37 docs, 0 changed, 0 removed
#1     09-13 21:49:18 build     201.7 ms llm=0 tok=0/0 sql=11738 full build: 37 docs, 37 changed, 0 removed

#9 query  CL-55302 는 어떤 이슈를 수정했나?  (20.0 ms, debug_level=2)
query                      20.0 ms       llm=2 tok=1614/36 sql=849
  sync_index                  0.0 ms    0% sql=2
  …(항상 verbose: ⚙ debug, ▶ samples 포함)
```
`show --json` 의 result 에는 `hits` 가 저장되지 않는다(크기 절감; `log_request` 가 제외). `request N not found` / `no requests` (exit 1).

### 3.22 config show / --effective / paths / set / reset

```powershell
python -m llmwiki config show
python -m llmwiki config show --effective
python -m llmwiki config paths
python -m llmwiki config set top_k_final=6 llm_provider=anthropic
python -m llmwiki config reset          # Settings() 기본값으로 config.json 덮어씀
```
```
corpus_dirs                        = ["C:\\…\\corpus"]                [env] (기본 ["C:\\Users\\user\\p)  LLMWIKI_CORPUS_DIRS
llm_provider                       = "mock"                         [env] (기본 "auto")  LLMWIKI_LLM_PROVIDER
embed_dim                          = 512                            [env] (기본 4096)  LLMWIKI_EMBED_DIM
top_k_final                        = 8                              [file]  LLMWIKI_TOP_K_FINAL

config         <tmp>\config.json
env            C:\…\llm-wiki-rag-selfevolving\.env
tuning         <tmp>\tuning.json
…
eval           <tmp>\corpus\questions.json     ← 표시만; evalset 은 <ROOT>/eval/questions.json 고정 (§1.4)
logs_dir       <tmp>\logs
```
`config set` 은 `apply_overrides` 로 타입 변환(bool/int/float/list) 후 `save_settings` (경로는 ROOT 하위면 상대경로로 저장). 첫 실행 시 config.json 이 없으면 `setup/config.example.json` 이 복사된다(llm_roles 의 rerank/extract 모델 지정 포함).

### 3.23 models show / test / set

```powershell
python -m llmwiki models show
python -m llmwiki models test
python -m llmwiki models set answer_effort=low rerank_provider=ollama rerank_model=llama3.1 embed_provider=voyage
python -m llmwiki models set answer_effort=        # 빈 값 = 역할 오버라이드 제거
```
```
embedder: hash model=hash-ngram-512 dim=512 available=True (embed_provider=hash embed_model='')
global llm: provider=mock model=claude-opus-5 effort(extract/rerank)=low effort(answer)=medium
  answer   -> mock/claude-opus-5 effort=medium available=True (global)  [최종 답변 생성 (인용 강제)]
  rerank   -> mock/claude-haiku-4-5-20251001 effort=low available=True (role override)  [후보 문단 재정렬]
  extract  -> mock/claude-sonnet-5 effort=low available=True (role override)  [그래프 엔티티/관계 추출 (빌드)]
  …(summary, review, expand, verify, forensic)

# models test (역할별 ping + 임베더 1건)
{ "answer": {"ok": true, "ms": 0.0, "detail": "mock (deterministic, no network)", "provider": "mock", "model": "mock", "available": true}, … }
```
`set` 은 `<role>_<provider|model|effort>` 와 일반 설정 키를 받아 config.json 에 저장하고 `Pipeline.reload()`.

### 3.24 tuning show / set / reset / doc

```powershell
python -m llmwiki tuning show [--stage rrf_fuse]
python -m llmwiki tuning set fusion_method=dbsf wiki_min_degree=2
python -m llmwiki tuning reset [키]
python -m llmwiki tuning doc          # docs/TUNING.md 재생성 (파일 쓰기; 실행 시 현재 설정 기준으로 덮어씀)
```
단계: chunk_index, embed, graph_build, router, time_scope, query_rules, query_expand, fts_search, vector_search, graph_search, rrf_fuse, rerank, context, evidence, answer, claim, forensic. `source=config` 항목(chunk_max_chars, rrf_k, embed_dim …)은 config.json 값이며 `config set` 으로 바꾼다. `rebuild` 표시 항목은 변경 후 `build --full`.
```
[rrf_fuse] 융합 · 부스트 (질의)
  rrf_k                    = 60                           (기본 60, config.json)
  fusion_method            = rrf                          (기본 rrf, tuning.json)
      융합 방식. rrf = 순위 기반 … `fusion compare` 로 평가셋에서 비교.
  wiki_min_degree          = 2                            * (기본 1, tuning.json)     ← * = 오버라이드됨
ERROR: 'unknown tunable: no_such_key'                                            ← 모르는 키/범위 밖 값 → exit 1
```
`set`/`reset` 이 성공하면 곧바로 전체 `show` 가 출력된다.

### 3.25 arch

```powershell
python -m llmwiki arch                 # build / query / evolve / watch 흐름 전체
python -m llmwiki arch --flow query
python -m llmwiki arch --json
```
`architecture.registry()` 의 정적 정의를 현재 토글 상태와 함께 렌더링 — 각 단계의 설명·impact·관련 토글(OFF 표시)·settings·cli·tunables 수.
```
== query — User Request (질의 → 답변)
   entry: python -m llmwiki query "…" · Web Query 탭 · MCP wiki_query
   ▸ sync_index         인덱스 동기화
   ▸ cache_hit          질의 캐시 · 프리컴퓨트
       toggles: query_cache, precompute(OFF), precompute_after_build(OFF) | settings: query_cache_size | cli: query … --no-query-cache, precompute run, maintenance clear_cache | tunables: 0
   ▸ query_rules        규칙 기반 질의 확장
       toggles: query_rules, profile_expansion(OFF) | … | cli: rules test "질의", --no-query-rules | tunables: 4
   ▸ graph_search       그래프 검색
       toggles: graph | settings: top_k_graph, graph_hops | cli: --no-graph, search graph "…", entity <name> | tunables: 15
   ▸ rerank             리랭크
       toggles: rerank, rerank_llm | settings: rerank_candidates, rerank_chunk_chars, llm_roles.rerank | cli: --no-rerank, --no-rerank-llm, tuning set rerank_method=cross_encoder | tunables: 8
```

### 3.26 evolve status / list / apply / reject / review / feedback

```powershell
python -m llmwiki evolve status
python -m llmwiki evolve list [proposed|applied|rejected]
python -m llmwiki evolve feedback <query_id> +1 "정답 근거 맞음"
python -m llmwiki evolve feedback <query_id> -1 "정정 내용…"
python -m llmwiki evolve apply <id> [--no-eval]
python -m llmwiki evolve reject <id> [메모]
python -m llmwiki evolve review        # review 역할 LLM 이 질의 로그를 보고 synonym/alias/entity/relation 제안
```
**내부**: 제안 출처 = 질의 `capture_query`(시드 없는 그래프 → alias/entity, 낮은 융합 점수 → synonym, 인용 없음 → wiki_note), `feedback`(+1 → synonym 0.6, −1 + 정정문 → wiki_note 0.9), `memory consolidate`(포렌식 반복 → corpus_gap/alias/tuning), `llm_review`. `apply_proposal` = (evaluate 시) 평가 before → 스냅샷 → 적용(synonym→synonyms 테이블, alias/entity→rules.json + 리빌드, relation→relations, wiki_note→wiki 노트 + 리빌드, chunk_params→config + 전체 리빌드) → 평가 after → `hit@k+term_recall` 이 나빠지면 롤백(`rejected_regression`). `evolve_auto_apply` 토글이면 `evolve_min_confidence` 이상 제안을 질의 중 자동 적용.
```
{"query_id": 1, "feedback": 1, "proposals": [7], "episode": 4}
{"query_id": 4, "feedback": -1, "proposals": [8], "episode": 4}

auto_apply=False min_conf=0.80 pending=8 applied=0 synonyms=0
  #8 wiki_note  conf=0.90 {"note": "[정정 2026-09-13] Q: ISSUE-2001 의 원인과 수정 CL 은?\n근거 부족", "page": "ISSUE-2…  <- 사용자 부정 피드백 + 정정 텍스트
  #7 synonym    conf=0.60 {"expansion": "rx", "term": "issue-2001"}  <- 긍정 피드백: 'issue-2001' → 'RX DMA 검증 TC > 목적' 문단이 정답
  #5 corpus_gap conf=0.95 {"events": 13, "queries": ["Physical Downlink Control Channel 디코더 문제"], …}  <- 포렌식 13건 반복 …
  #2 tuning     conf=0.95 {"events": 123, "key": "claim_policy", …}  <- 포렌식 123건 반복: claim_policy=drop 또는 answer_refine 활성화 …

# evolve apply 7 --no-eval
{"status": "applied", "before": null, "after": null, "rebuilt": false}
# evolve reject 8 "불필요"
{"status": "rejected"}
auto_apply=False min_conf=0.80 pending=6 applied=1 synonyms=1
```
**주의**: `--no-eval` 없이 apply 하면 `evaluate()` 가 `<ROOT>/eval/questions.json` 을 쓴다(코퍼스와 맞는 질문셋을 그 위치에 두어야 회귀 판정이 의미 있음). `evolve review` 가 mock 에서는 `{"proposals": []}` 만 반환.

### 3.27 wiki

```powershell
python -m llmwiki wiki --min-degree 1
```
`wiki.write_wiki(store, wiki_dir, prof, min_degree)` 로 엔티티 페이지 전체 재작성(prune 포함, 편집 노트는 보존). 빌드의 `wiki_pages` 단계와 동일하나 항상 전체 모드.
```
wiki pages written: 46 -> <tmp>\wiki
```

### 3.28 docs / stats / system

```powershell
python -m llmwiki docs
python -m llmwiki stats
python -m llmwiki system --target-docs 3000 --daily-new 20 --horizon-days 365
```
```
corpus/README.md                                                       md    chunks=1
corpus/cls/CL-55301.md                                                 md    chunks=4
…
{ "stats": {"docs": 37, "chunks": 185, "embeddings": 185, "entities": 57, "relations": 299, "mentions": 299, "communities": 10,
            "queries": 0, "requests": 2, "proposals_pending": 0, "synonyms": 0, "db_bytes": 1589248, "last_build": {…}},
  "providers": {…models show 와 동일…}, "toggles": {…} }

index: docs=37 chunks=185 embeddings=185 entities=57 relations=299 db=1.6MB wal=0.0MB
  current        docs=37     chunks=185     vector_matrix=   0.4MB db≈   1.6MB
  target         docs=3000   chunks=15000   vector_matrix=  30.7MB db≈ 128.9MB
  after_horizon  docs=10300  chunks=51500   vector_matrix= 105.5MB db≈ 442.4MB
  note: hash 임베딩은 dim×4 bytes/청크. 3만 청크×4096d ≈ 490MB → embed_dim 1024 권장 (≈120MB) 또는 외부 임베더(1024d)
query latency (last 0): avg=None p50=None p95=None ms
builds: 202ms, 18ms
caches: {"query_cache": {"size": 0, "max": 200, "hits": 0, "misses": 0}, "vector_matrix": {"loaded": false}, "entity_index": {"loaded": false, "n": 0}}
watcher: {"enabled": false, "last_scan": null, …, "interval": 300}
```
`system` 의 caches/watcher 는 **현재 프로세스** 값이므로 CLI 에서는 항상 비어 있다(서버 프로세스의 상태는 Web UI/`wiki_status` 로 본다).

### 3.29 maintenance

```powershell
python -m llmwiki maintenance vacuum | fts_optimize | wal_checkpoint | clear_cache | warm_cache | refresh_doc_refs | purge_requests
```
```
{"ok": true, "action": "vacuum", "ms": 94.2, "stats": {…}}
```
`clear_cache`/`warm_cache` 는 프로세스 메모리 캐시라 CLI 에서는 즉시 소멸(서버에서 의미). `purge_requests` 는 requests 테이블 전체 삭제.

### 3.30 mcp-source list / test / ingest / enrich / fetch

```powershell
python -m llmwiki mcp-source list
python -m llmwiki mcp-source test mock
python -m llmwiki mcp-source ingest mock --dry-run [--since 2026-09-01]
python -m llmwiki mcp-source enrich "DMA underrun"
python -m llmwiki mcp-source fetch mock search '{\"query\":\"dma\"}'
```
`mcp_sources.json` 이 없으면 기본(mango: 비활성, mock: 비활성)으로 생성된다. `mock` 을 쓰려면 파일에서 `"mock": {"enabled": true, …}` 로 바꾼다. `test` 는 stdio 로 서버를 띄워 `tools/list`. `ingest` 는 각 ingest 도구 결과를 `doc_type`/`id_field` 매핑으로 `data/mcp_cache/<name>/` 에 마크다운으로 저장(빌드가 `mcp_sources` 토글일 때 자동 실행·스캔). `enrich` 는 질의 시 `mcp_enrich` 단계(fallback level `mcp`)와 같은 호출. `fetch` 는 도구 직접 호출.
```
mango    enabled=False Mango MCP — issue / CL / build binary raw data (…)
          command=['python', '-m', 'mango_mcp'] ingest=3 enrich=1
mock     enabled=True  테스트용 목업 MCP 서버 (네트워크 없음)
          command=['…\\python.exe', '-m', 'llmwiki.mcp_client', '--mock-server'] ingest=2 enrich=1
토글 mcp_sources=False  파일: <tmp>\mcp_sources.json

[{"name": "mock", "ok": true, "tools": ["list_issues", "list_cls", "search"], "ms": 47}]
{"sources": {"mock": {"tools": {"list_issues": 2, "list_cls": 1}, "written": 0, "skipped": 0}}, "written": 0, "skipped": 0, "errors": []}   # dry-run
[]                    # enrich (mock 은 빈 결과)
{"items": []}         # fetch
```
**흔한 오류(실측)**: `json.decoder.JSONDecodeError: Unexpected UTF-8 BOM` — Windows PowerShell 5.1 의 `Set-Content -Encoding utf8` 이 BOM 을 붙이면 `json.load` 가 실패한다. BOM 없는 UTF-8 로 저장(`[IO.File]::WriteAllText(path, text, (New-Object Text.UTF8Encoding($false)))` 또는 에디터). 같은 문제가 config.json, tuning.json, pins.json, query_rules.json 편집 시에도 난다.

### 3.31 watch

```powershell
python -m llmwiki watch --once                 # 1회 스캔, 변경 있으면 증분 빌드 후 종료
python -m llmwiki watch --interval 120         # 블로킹 루프 (Ctrl+C 로 종료) — 문서 예제에서는 실행하지 않음
```
`Pipeline.auto_build_tick` : `scan_changed()` 로 stat 만 비교(1 ms 수준) → 변경/삭제가 있으면 `build(full=False)`. 서버의 `auto_build` 토글이 같은 함수를 `auto_build_interval` 초마다 호출.
```
watching ['<tmp>\\corpus'] every 300s (Ctrl+C to stop)
21:53:21 scan 1ms changed=0 removed=0 built=False

watching ['<tmp>\\corpus'] every 300s (Ctrl+C to stop)
  · health: ok (fail=0 warn=0)
  · loaded 37 docs (1 read, 36 skipped by stat)
  · indexed 4 chunks (FTS)
  · embedded 4 chunks (cache 0, failed 0)
21:53:21 scan 1ms changed=1 removed=0 built=True (48 ms)
```
빌드 실패 시 `built=False` 와 `error` 가 결과에 담기고 루프는 계속된다. 다른 프로세스가 빌드 중이면 `BuildLockedError` 가 error 로 기록된다.

### 3.32 mcp / serve (설명만, 블로킹)

- `python -m llmwiki mcp` : `mcp.serve_stdio(pipe)` — stdin/stdout JSON-RPC. 도구: `wiki_query`(question, k, mode=deep 옵션), `wiki_search`(channel, query, k), `wiki_related`(text, doc_types, k), `wiki_doc`(id), `wiki_entity`(name), `wiki_propose`(kind, payload, reason → proposals, HITL), `wiki_status`. Claude Desktop/Code 의 MCP 서버 설정에 `command: python`, `args: ["-m","llmwiki","mcp"]`, `cwd: <프로젝트 루트>` 로 등록한다. 터미널에서 직접 실행하면 입력 대기로 멈춘다.
- `python -m llmwiki serve --port 8765 --host 127.0.0.1` : `web.server.serve(pipe, host, port)`. Web UI(Query/Requests/Architecture/콘솔 탭)와 `auto_build` 워처 스레드가 이 프로세스 안에서 돌며, 질의 캐시·warm_cache 가 실제로 효과를 낸다.

---

## 4. End-to-end 시나리오

### (a) 새 환경 bring-up

1. 설정 파일 준비 — `.env` 에 API 키(없으면 `--llm mock` 또는 `offline` 프리셋), `config.json` 은 첫 실행 시 자동 생성.
   ```powershell
   $env:PYTHONIOENCODING='utf-8'
   python -m llmwiki config show            # config.json 생성·확인 (corpus_dirs, data_dir, wiki_dir 경로)
   python -m llmwiki config paths           # 데이터 파일 위치 확인
   python -m llmwiki models show            # 역할별 provider/model, available=True 인지
   ```
2. `python -m llmwiki health` → 모든 항목 ✔ 인지. ✘ 가 있으면 `→` 뒤의 조치대로 고친다 (exit 1 이면 다음 단계 빌드가 exit 3 으로 중단된다).
3. `python -m llmwiki build --full --trace` → `build done (full)` 과 `verify ↳ ok: true`, `ALERTS` 가 없는지. 볼 것: `load_corpus ↳ docs/unsupported`, `chunk_index ↳ doc_types/lint_errors`, `embed ↳ embedded/failed`, `graph_build ↳ provenance`.
4. `python -m llmwiki build verify` → `verify: OK (problems=0)`; `corpus lint` 로 front matter 경고 확인; `embed report` 로 coverage 100%.
5. 질문셋을 `eval/questions.json` 에 두고(또는 `--questions`) `python -m llmwiki eval --k 5` → hit@k/mrr 기준선. `eval --matrix` 로 채널별 기여도 확인(그래프 채널이 0.84 로 낮으면 엔티티 사전/explicit 관계 점검).
6. `python -m llmwiki trial run --name baseline --note "bring-up"` → 이후 모든 튜닝은 `trial compare baseline <이름>` 으로 판단.
7. (운영 진입) `serve` 또는 OS 스케줄러로 `build`/`watch --once` 를 주기 실행.

### (b) 매일 증분 운영

1. 문서 추가/수정/삭제는 코퍼스 폴더에서 그대로 수행. front matter 는 `corpus example <type>` 템플릿을 따르고 저장 전에 `corpus lint-file <파일>` (error 면 exit 1).
2. `python -m llmwiki build --trace` (증분). 볼 것:
   - `load_corpus ↳ read / skipped_by_stat` — 바뀐 파일만 읽었는지 (실측: 38 docs 중 2 read, 36 skipped)
   - `diff ↳ changed / removed / new / renamed`
   - `embed ↳ idf_reason: reused stored IDF`, `embedded == todo`, `failed 0`
   - `graph_build ↳ communities (skipped: incremental …)` 와 `verify ↳ community_unassigned` 는 정상
   - 삭제 시 `prune ↳ orphan_entities, stale_wiki_pages`
   - `ALERTS:` 줄이 있으면 `corpus lint` / `build verify --fix`
3. `python -m llmwiki build status` → `last_build … changed=N build_version=K`, `embed: done`.
4. `python -m llmwiki embed report` → coverage 100% 유지, 최근 `run … failed=0`.
5. 문제 추적: `logs grep --file build --text loaded --since 1d` (일별 읽은 파일 수), `logs grep --level WARNING`, `requests list --kind build` → `logs grep --request <id>` 로 해당 빌드의 단계별 로그.
6. 주 1회: `build --full` (커뮤니티 재탐지·IDF 재적합·FTS optimize), `maintenance vacuum`, `precompute clear --stale`, `memory decay`, `memory consolidate` → `evolve status` 검토.

### (c) 답변 품질 디버깅

1. 문제 질의를 trace 로 실행:
   ```powershell
   python -m llmwiki query "CL-55302 는 어떤 이슈를 수정했나?" --trace
   ```
   답변 앞의 `> ⚠ 근거가 약합니다 (keyword coverage 0.67)` 와 trace 의 `evidence_check ↳ verdict/reasons/signals` 를 본다. `[C#] … via …` 로 어느 채널이 어떤 순위로 후보를 냈는지, `graph seeds` 로 시드 엔티티를 확인.
2. 근거 부족 판정의 원인 분류:
   - 기대 문서가 후보에 없음 → `search fts|vector|graph "…"` 로 채널별 확인, `rules test "…"` 로 확장 규칙이 발화했는지.
   - 문서 ID 가 근거에 없음(`document id … not in evidence`) → 해당 문서의 front matter `id`/related 확인, `entity <ID>` 로 그래프 연결 확인.
   - 시간 조건 오해석 → `time "…"`.
3. `python -m llmwiki forensic last` (또는 `forensic <request_id>`) → findings/suggestions. 반복 소견은 `forensic summary` 의 `problem_stages`, `top_topics`.
4. 조치 후보 적용:
   - 어휘 문제: `rules add synonym|acronym|related <term> <values…>` → `rules test` 로 확인.
   - 특정 문서를 항상 근거로: `pin add --doc CL-55302 --keywords "CL-55302"` → `pin test "질의"` 로 inject 확인.
   - 튜닝: forensic 제안대로 `tuning set answer_length_target=long`, `tuning set claim_policy=drop`, 또는 `--fallback-loop` 토글.
5. 재질의:
   ```powershell
   python -m llmwiki query "CL-55302 는 어떤 이슈를 수정했나?" --json
   ```
   실측: pin 적용 후 `[C1] corpus/cls/CL-55302.md#0 fused=1.2572 why=…,pin boosts={"router_type":1.2,"pin":10.5,"provenance":1.2}` 로 대상 문서가 1위. (mock LLM 이라 groundedness 는 그대로 0.0 — 실제 LLM 에서는 답변 문장의 인용 지원 여부가 바뀐다.)
6. 회귀 확인:
   ```powershell
   python -m llmwiki trial run --name after-pin --questions Q.json
   python -m llmwiki trial compare baseline after-pin
   ```
   실측: `hit@k 0.96 → 1.0 ★`, `mrr 0.813 → 0.853 ★`, `win 1 / loss 0 / tie 24`. 나빠진 지표(★ 없음, Δ 음수)가 있으면 `pin remove`/`rules remove`/`tuning reset` 으로 되돌린다.
7. 사용자 피드백을 남겨 자가진화에 반영: `evolve feedback <query_id> +1|-1 "메모"` → `evolve status` 에서 제안 검토 → `evolve apply <id>` (평가 후 악화 시 자동 롤백).

---

## 5. 종료 코드와 자동화 팁

### 5.1 종료 코드 (실측 + `cli._run_cmd` 기준)

| 코드 | 상황 |
|---|---|
| 0 | 정상. `health` 는 `ok=true` 일 때, `build verify` 는 problems=0 일 때 |
| 1 | `health` 실패(fail 항목), `build verify` 문제 있음, `entity/requests/forensic/trial` not found, `rules remove`/`pin remove` not found, `tuning set` 잘못된 키, `corpus lint-file` error, `logs grep --request` run_id 없음, `mcp-source fetch` 인자 부족, 처리되지 않은 예외(traceback; 위의 query `%d` 버그 포함), `trial compare` 오류 |
| 2 | `build` 락 충돌(`build refused: another build is running`), argparse 오류(잘못된 인자·PowerShell 쉼표 배열 분리) |
| 3 | `build` 가 health 검사 실패로 중단(`build aborted: health check failed`) |

PowerShell 에서는 `$LASTEXITCODE` 로 확인한다. `--json` 을 쓰면 오류도 `{"error": "..."}` 형태로 나와 파싱하기 쉽다(`build --json` 실측: `{"error": "health check failed: corpus_dirs (…)"}` exit 3).

### 5.2 OS 스케줄러 팁

- **증분 빌드 잡** (예: Windows 작업 스케줄러, 5분 간격): `run.bat build` 또는 `run.bat watch --once`. 락(`data/build.lock`)이 있어 서버 워처와 겹쳐도 한쪽은 exit 2 로 빠진다. 겹침을 기다리게 하려면 `config set build_lock_timeout=120`.
- **야간 전체 빌드**: `run.bat build --full` → 이어서 `run.bat build verify` (exit≠0 이면 알림) → `run.bat maintenance vacuum`.
- **품질 게이트**: 배포 전 `run.bat trial run --name nightly-<date>` 후 `trial compare baseline nightly-<date> --json` 을 파싱해 `hit@k`/`mrr` Δ 가 음수면 실패 처리.
- **로그 로테이션**은 내장(`log_max_mb`, `log_backups`). 장기 보관은 `logs/` 폴더를 외부로 복사. requests 는 `keep_requests` 로 자동 정리.
- **환경 격리**: 서비스 계정에서 `LLMWIKI_CONFIG`, `LLMWIKI_DATA_DIR`, `LLMWIKI_WIKI_DIR`, `LLMWIKI_CORPUS_DIRS`, `LLMWIKI_LOGS_DIR_PATH`, `LLMWIKI_TUNING` 등을 환경변수로 고정하면 코드 트리를 건드리지 않고 여러 인스턴스를 운용할 수 있다(이 문서의 샌드박스가 같은 방식). 실제 LLM 을 쓰지 않는 CI 에서는 `LLMWIKI_LLM_PROVIDER=mock LLMWIKI_EMBED_PROVIDER=hash`.
- **PowerShell 파이프 주의**: `python -m llmwiki … | Select-Object -First N` 처럼 파이프가 일찍 닫히면 python 이 exit 255 로 끝난다(출력 자체는 정상). 결과를 변수에 받은 뒤 자르거나 `Out-File` 로 저장한다.
- **블로킹 명령**은 스케줄러에 넣지 않는다: `serve`, `mcp`, `watch`(`--once` 없이).
