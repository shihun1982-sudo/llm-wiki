# SYSTEM ARCHITECTURE — 현재 구조 전체 지도

> **이 문서를 먼저 읽는 사람**: 이 시스템을 다른 환경에 올리거나, 코드를 고치러 온 사람(또는 LLM).
> 여기에는 **무엇이 어디에 있고 왜 그렇게 되어 있는지**가 들어 있다. 설치 절차는 [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md),
> 손잡이(토글·튜닝) 사전은 [OPTIMIZATION_GUIDE.md](OPTIMIZATION_GUIDE.md)·[TUNING.md](TUNING.md)(둘 다 코드에서 자동 생성),
> 보안은 [SECURITY.md](SECURITY.md), 동시성은 [CONCURRENCY.md](CONCURRENCY.md) 에 있다. 이 문서는 그것들을 잇는 **지도**다.
>
> **구조를 설명하는 현행 문서는 이것 하나다.** 예전에 따로 있던 `ARCHITECTURE_V2`·`ARCHITECTURE_V3`·`REBUILD_SPEC` 는
> 그때의 설계 기록이라 [docs/history/](history/README.md) 로 옮겼고, 그중 지금도 유효한 것(용어 사전 · 한 질의의 여정)은
> 아래 §0.1 과 §5.1 로 흡수했다. 문서 배치 규칙은 [DOC_MAP.md §1](DOC_MAP.md).
>
> 표시가 붙은 규모 숫자(`<!--live:…-->`)는 하네스가 코드와 대조한다. 나머지는 §17 의 세는 법으로 확인한다.

---

## 0. 한 장 요약

| 질문 | 답 |
|---|---|
| 무엇인가 | 사내 위키·설계문서·이슈를 색인해 **근거를 인용하는 답변**을 주고, 실패에서 **스스로 규칙을 제안**하는 RAG 시스템 |
| 규모 | `llmwiki/` 66개 모듈 · 약 37,400줄 · 프론트 JS 10개 파일 약 5,700줄 |
| 의존성 | **표준 라이브러리 + numpy + pypdf**. 그 외 없음 |
| 저장소 | SQLite 하나 (`data/llmwiki.sqlite3`, 테이블 41개, WAL) + numpy 인메모리 벡터 행렬 |
| 검색 | FTS5(BM25) · 벡터(코사인) · 그래프(엔티티 n-hop) **세 채널을 가중 RRF 로 융합** |
| 창구 | CLI 명령 <!--live:cli-->48개 · Web 경로 <!--live:api-->113개 · MCP 도구 <!--live:mcp-->20개 — **같은 기능은 셋 다 있어야 한다** ([SURFACE_ALIGNMENT.md](SURFACE_ALIGNMENT.md)) |
| 흐름 | build(10단계) · query(27단계) · evolve(6단계) · watch(2단계) |
| LLM 역할 | 10개(answer·rerank·extract·summary·review·expand·verify·forensic·fusion·select), 역할마다 모델·프로바이더·앙상블을 따로 |
| 설정 | 파일 4층(`config.json` 109키 · `tuning.json` 166키 · `presets.json` · 요청 단위 overrides) — **코드 수정 없이 이식** |
| 권한 | 역할 6단계 × 작업 등급 7단계 + **문서 단위 접근 제어**(무엇을 읽을 수 있는가) |
| 관측 | 모든 질의가 단계별 trace(이름 82종) · 진행 표시 · 분석 리포트 · 기대 결과 포렌식을 남긴다 |
| 검증 | 단위 테스트 <!--live:tests-->758개 + 검증 하네스 <!--live:harness-->27종(`verify_all.py` 한 줄) |

> 이 표의 굵은 규모 숫자 다섯 개(<!--live:--> 표시가 붙은 것)는 **하네스가 지킨다** — 코드와 어긋나면
> `verify_docs.py` 가 "지금은 N 다" 라고 찍는다. 표시가 없는 숫자는 사람이 §17 의 세는 법으로 확인한다.

### 0.1 핵심 용어

나머지 문서가 이 말들을 설명 없이 쓴다. 처음 오는 사람은 여기부터.

| 용어 | 뜻 |
|---|---|
| 코퍼스 / 문서 / 청크 | 색인 대상 폴더 / 파일 하나 / 헤딩 단위로 자른 **검색 단위**(답변에서 `[C1]` 로 인용된다) |
| 문서 계약 (front matter) | 문서 맨 위 `---` 블록의 `doc_type`·`ext_id`·`date`·`related` 등. 지키면 ID 노드·결정적 관계·시간 검색이 켜진다 — [CORPUS_CONTRACT.md](CORPUS_CONTRACT.md) |
| 채널 | 후보를 내는 검색 방식: **FTS**(키워드, BM25) · **Vector**(의미 임베딩) · **Graph**(엔티티·관계 탐색) · doc_vector(문서 카드) · external_rag(붙인 다른 RAG) |
| 융합 / 부스트 / 리랭크 | 채널별 순위를 하나로 합침(RRF 등) → 문서유형·시간·pin 배율 → 상위 후보 재정렬 |
| provenance | 그래프 간선의 **출처**: explicit(front matter) · rule(ID 패턴) · cooccur(공동출현) · llm · human. 신뢰도 순으로 탐색 가중 |
| 근거 판정(evidence_check) / fallback | 검색 결과가 답하기에 충분한지 판정 → 부족하면 규칙확장 → LLM확장 → 그래프 → 광역 → MCP 순으로 재검색 |
| groundedness / claim_check | 답변 문장 중 인용 근거가 실제로 뒷받침하는 비율 / 그 검증 절차. 미지원 문장은 `[미확인]` 표기·제거·재작성 |
| 포렌식 | "왜 답을 못 만들었나" 를 단계별로 진단해 남긴 기록. 누적되면 제안의 재료 — [FORENSIC.md](FORENSIC.md) |
| 제안 / HITL | 시스템이 만든 개선안(동의어·관계·코퍼스 갭·튜닝). **사람이 승인해야** 적용된다(Human-in-the-loop) — [EVOLVE.md](EVOLVE.md) |
| 토글 / 튜닝 / 프리셋 | 단계 on/off 스위치 69개 / 알고리즘 상수 166개 / 둘을 묶은 이름(quality·speed·token·offline·deep_research) |
| request_id / run_id | 요청별 프로파일 기록 번호(`requests` 명령) / 같은 요청의 로그 줄을 묶는 ID(`logs grep --request`) |
| trial | 설정 스냅샷 + 평가 지표를 저장한 것. 두 trial 을 비교해 튜닝 효과를 판단 — [EVAL_TRIAL.md](EVAL_TRIAL.md) |
| 창구 | 같은 기능에 닿는 세 입구 — CLI · Web UI · MCP. **같은 기능은 셋 다 있어야 한다** — [SURFACE_ALIGNMENT.md](SURFACE_ALIGNMENT.md) |

**이 시스템을 이해하는 열쇠는 하나다: 모든 것이 "무엇이 일어났는지 되짚을 수 있게" 만들어져 있다.**
답이 이상할 때 사람이 물을 수 있는 질문 — "내 질문이 제대로 이해됐나", "왜 이 문서가 안 나왔나",
"어느 단계가 느렸나", "이 문장의 근거가 진짜 있나" — 에 전부 대답하는 자리가 코드 안에 있다.
그 요구가 구조의 상당 부분을 결정했다.

---

## 1. 설계 제약 — 왜 이런 모양인가

이 시스템은 **다른 회사 내부망에 폴더째 복사해서 돌아가야 한다**. 그 한 줄이 나머지를 거의 다 결정했다.

| 제약 | 왜 | 결과 |
|---|---|---|
| **표준 라이브러리만** | 사내망은 PyPI 접근이 막혀 있거나 심사가 필요하다. 의존성 하나가 반입 절차 몇 주가 된다 | 웹 서버도 `http.server`, 벡터도 numpy 행렬, 테스트도 `unittest`. numpy·pypdf 만 예외 |
| **설정은 전부 파일** | 환경이 다르면 코드를 고치는 것이 아니라 파일을 고쳐야 한다 | `config.json`·`tuning.json`·`presets.json`·`security.json`·`docacl.json`·`agents.json`·`models.json`·`query_rules.json`·`rules.json`·`pins.json`·`schedule.json`·`server.json`·`mcp_sources.json` + `setup/*.example.json` |
| **기본값도 파일에 쓴다** | 다음 운영자가 "이런 키가 있는 줄도 몰랐다" 가 되지 않게 | `config fill-defaults --all` 이 코드 기본값을 파일에 명시적으로 써 넣는다 |
| **세 창구 정합** | 사람은 화면, 스크립트는 CLI, 외부 LLM 은 MCP 로 온다. 한 곳에만 있는 기능은 나머지 둘에서 "없는 기능" 이다 | 기능마다 CLI·Web·MCP 를 표로 고정(`verify_surface_align.py`)하고, **값까지 같은지**도 테스트(`test_surface_consistency.py`) |
| **관측 가능성** | LLM 이 낀 파이프라인은 "왜 이렇게 나왔는지" 를 못 보면 고칠 수 없다 | 모든 단계가 `prof.stage()` 로 시간·입출력·건너뛴 이유를 남긴다 |
| **다중 사용자** | 한 사람의 무거운 작업이 나머지를 막으면 안 된다 | 요청마다 설정 사본·DB 연결·프로파일러가 분리된다(스레드 로컬) |

### 1.1 채택하지 않은 것과 이유

좋아 보이지만 이 프로젝트에서는 틀린 선택들이다. 다음 사람이 같은 제안을 다시 하지 않도록 적어 둔다.

| 안 쓴 것 | 왜 |
|---|---|
| FastAPI / Flask | 의존성. `BaseHTTPRequestHandler` + `ThreadingHTTPServer` 로 충분하고, 요청 하나당 스레드 하나라 동시성 모델이 단순하다 |
| Chroma / Milvus / FAISS | 문서 수만 건 규모에서 numpy 행렬 한 번의 행렬곱이 충분히 빠르다. 별도 프로세스·인덱스 파일·버전 호환 문제가 없다 |
| LangChain / LlamaIndex | 추상화가 우리 파이프라인(단계별 trace·재실행·스윕)과 맞지 않는다. 단계를 직접 쥐고 있어야 관측과 재실행이 된다 |
| pytest | 의존성. `unittest` 로 <!--live:tests-->758개를 돌리고 있고 모자란 적이 없다 |
| 스트리밍(SSE) | 답변을 흘리면 **인용 검증(claim_check)을 답변 완성 뒤에 할 수 없다**. 대신 `/api/progress` 폴링으로 단계 진행을 보여 준다. SSE 요청은 명시적으로 405 |
| ORM | 쿼리가 곧 성능이다. SQL 을 직접 보는 편이 진단에 낫다 |
| 마이크로서비스 | 사내 한 대에 올리는 시스템이다. 프로세스 하나가 운영·백업·이식이 가장 쉽다 |

---

## 2. 모듈 지도

`llmwiki/` 66개 모듈. 역할별로 묶으면 아홉 덩어리다.

### 2.1 뼈대

| 파일 | 역할 |
|---|---|
| `pipeline.py` | **중심축**. 설정·저장소·프로바이더·요청 범위를 쥐고 build/query/rerun 을 조립한다. `request_scope()` 가 요청 하나의 격리(설정 사본·튜닝 오버레이·DB 세션·요청자 신분)를 만든다 |
| `config.py` | `Settings`(109키)·`Toggles`(69개)·도움말·경로 해석(`path_for`)·overrides 적용·`fill_defaults` |
| `store.py` | SQLite 저장소. 테이블 41개, 스레드별 연결 풀, 벡터 행렬/엔티티 인덱스/doc_meta 캐시 |
| `architecture.py` | **구조 레지스트리**. 흐름 4개와 단계 45개의 이름·설명·토글·튜닝·CLI 예시·시간 제한. 화면·문서·검증이 전부 여기서 나온다 |
| `tuning.py` | **튜닝 레지스트리**. 166개 파라미터의 타입·범위·설명·영향·단계. 요청 단위 오버레이(push/pop) 지원 |
| `profiler.py` | 단계별 시간·SQL 수·LLM 호출·메모를 나무 구조로 기록 (= trace) |
| `progress.py` | 실행 중인 요청의 진행 표시(단계 라벨 82종)·취소·대기열 위치 |

### 2.2 색인(build)

| 파일 | 역할 |
|---|---|
| `corpus.py` | 문서 적재(md/txt/pdf)·front matter 파싱·헤딩 기준 청킹 |
| `schema.py` | 문서 유형 스키마(`schemas/*.json`)·메타 정규화·마이그레이션·린트 |
| `graph_rules.py` | 규칙 기반 엔티티/관계 추출(정규식·ID 패턴·front matter 관계) |
| `graph_build.py` | 그래프 구축·커뮤니티 탐지(label propagation)·요약 |
| `embed_run.py` | 임베딩 실행·배치·캐시·적응 배치 |
| `wiki.py` | 엔티티별 위키 페이지 생성(색인에서 파생된 뷰) |
| `watch.py` | 코퍼스 변경 감시 → 증분 빌드 |

### 2.3 질의(query)

| 파일 | 역할 |
|---|---|
| `query_engine.py` | **질의 경로 전체**. 27단계를 순서대로 돌리고 fallback 루프·출력 모드·재실행을 처리한다 |
| `retrieval.py` | 세 채널 검색(`fts_search`·`vector_search`·`graph_search`)·라우터·`channel_search`(디버그 공용 엔진) |
| `fusion.py` | 가중 RRF 융합·top-k 가중 |
| `boost.py` | 시간·피드백·고정 근거 부스트 |
| `rerank.py` | 휴리스틱/LLM/CE 리랭크 |
| `answer.py` | 컨텍스트 조립(`build_context`)·답변 생성·**claim 검증**(`split_claims`/`check_claims`) |
| `evidence.py` | 근거 충분성 판정 + 4단계 fallback(규칙 확장 → LLM 확장 → 그래프 확대 → 광역) |
| `query_rules.py` | 질의 확장 사전(약어·동의어·별칭·관련어·제외·복합어) |
| `timeparse.py` | 한국어 시간 표현 → 날짜 범위 |
| `textutil.py` | 토큰화·조사 제거·FTS 질의 생성·불용어 |
| `querydebug.py` | 질의 해부(LLM 없이)·문서 상세 — 세 창구 공용 |

### 2.4 LLM

| 파일 | 역할 |
|---|---|
| `providers.py` | anthropic·openai 호환·ollama·headless·mock. 재시도·백오프·회로 차단·예산 |
| `ensemble.py` | 역할 단위 다중 LLM 병렬 실행 + 집계 |
| `headless.py` | opencode/claude/codex 를 CLI 로 부르는 어댑터(`agents.json`) |
| `models_catalog.py` | 쓸 수 있는 모델 목록·연결 테스트·자동 매핑·**컨텍스트 예산**(`context_budget`) |
| `prompts.py` | 프롬프트 27종(파일 `prompts/*.md` 로 덮어쓸 수 있다) |

### 2.5 창구

| 파일 | 역할 |
|---|---|
| `cli.py` | 명령 <!--live:cli-->48개 |
| `web/server.py` | HTTP 경로 <!--live:api-->113개 + 정적 파일 + 잡 + 워처 |
| `web/static/js/*.js` | 프론트 10개 모듈(`core` 공용, 화면별 9개) |
| `mcp.py` | MCP 서버(stdio·Streamable HTTP·브리지), 도구 <!--live:mcp-->20개, 플러그인, 페더레이션 |
| `mcp_client.py` | 외부 RAG·MCP 서버·REST 검색 API 연결(수집·검색 채널·도구 중계) |

### 2.6 운영·보안

| 파일 | 역할 |
|---|---|
| `auth.py` | 역할 6단계·작업 등급 7단계·권한 표·로컬 로그인·SSO(OIDC/헤더)·API 키·감사 |
| `docacl.py` | **문서 단위 접근 제어** — 어떤 역할이 어떤 문서를 근거로 볼 수 있는가 |
| `ctxguard.py` | **프롬프트 인젝션 방어** — 컨텍스트 본문의 구조 흉내 무력화 |
| `reqmgr.py` | 요청 관리자(티켓·읽기/쓰기 락·대기열·동시 수·속도 제한·취소·차단) |
| `scheduler.py` | 크론/주기 작업 |
| `snapshots.py` | 파괴적 작업 전 자동 스냅샷·복원 |
| `atomicio.py` | 원자적 파일 쓰기(임시 파일 + 교체) |
| `logging_setup.py` | JSON Lines 로그·총량 한도 |

### 2.7 품질·진화

| 파일 | 역할 |
|---|---|
| `evolve.py` | 제안 10종의 캡처·HITL 승인·적용·회귀 검증·스냅샷 |
| `memory.py` | 제안 강화/감쇠(memory decay) |
| `forensic.py` | **기대 결과 포렌식** — "이 문서가 나왔어야 하는데 어느 단계에서 탈락했나" |
| `analysis.py` | 상세 분석 리포트(설정·타임라인·단계별 상세·세 렌즈 소견과 조절점) |
| `ruleeffect.py` | 규칙이 실제로 발화했고 도움이 됐는지 집계(버퍼링 후 일괄 기록) |
| `evalset.py`·`trials.py` | 평가셋·시험 실행과 비교 |
| `sweep.py`·`rerun.py` | 파라미터 스윕·단계 재실행 |
| `optimize.py` | LLM 에게 최적화를 물을 때 줄 자료 묶음 생성 |
| `graph_profile.py` | 그래프 진단(연결성·허브·죽은 규칙·질의 활용) |

---

## 3. 데이터 모델

### 3.1 SQLite (테이블 41개)

| 묶음 | 테이블 | 담는 것 |
|---|---|---|
| 문서 | `docs` `doc_meta` `chunks` | 원문·정규화 메타(유형·ID·날짜·태그·**acl**)·청크 |
| 검색 | `chunks_fts` `chunks_tri` `embeddings` `doc_vectors` `embedding_cache` `embed_runs` | FTS5 인덱스·트라이그램·청크/문서 벡터·임베딩 캐시와 실행 이력 |
| 그래프 | `entities` `entities_fts` `relations` `mentions` `communities` | 엔티티·관계(provenance 포함)·멘션·커뮤니티 |
| 질의 | `query_log` `requests` `answer_cache` | 질의 기록(누가·어디서)·요청 프로파일·답변 캐시 |
| 진화 | `proposals` `evolution_log` `synonyms` `episodes` | 제안·적용 이력·동의어(즉시 적용)·에피소드 |
| 품질 | `forensics` `trials` | 포렌식 보고·시험 결과 |
| 기타 | `kv` | 빌드 버전·린트 요약·규칙 효과 등 작은 값 |

**설계 요점**
- **청크 ID = `<doc_id>#<n>`** — 문서와 청크의 관계를 문자열만으로 되짚을 수 있다(접근 제어의 fallback 경로가 이걸 쓴다).
- **벡터는 BLOB 으로 저장하고 메모리에서 행렬로** 올린다(`build_version` 기준 캐시). 질의마다 행렬곱 한 번.
- **`build_version`** 이 캐시(벡터 행렬·엔티티 인덱스·doc_meta)의 열쇠다. 빌드가 끝나면 올라가고 캐시가 통째로 무효가 된다.
- **WAL 모드** — 읽기가 쓰기를 막지 않는다. 빌드 중에도 질의가 된다.

### 3.2 파일 (경로는 `LLMWIKI_<NAME>_PATH` 로 바꿀 수 있다 — `config paths`)

`config` `tuning` `presets` `query_rules` `rules` `pins` `security` `docacl` `agents` `models`
`mcp_sources` `schedule` `server` `stopwords` `eval` `env` + 디렉터리 `prompts_dir` `schemas_dir` `logs_dir` `themes`

전부 `setup/*.example.json` 에 주석(`_how_*`)이 붙은 원본이 있다.

---

## 4. 네 가지 흐름

`architecture.py` 의 레지스트리가 단일 진실이다. 화면(🧭 Pipeline)·문서·검증이 모두 여기서 나온다.

### 4.1 build — 색인 (10단계)

```
health → providers → load_corpus → diff → chunk_index → embed → graph_build → wiki_pages → prune → warm_cache
```

- `diff` 가 mtime·크기·해시로 바뀐 문서만 고른다(증분). 전체 리빌드는 배타 락을 잡는다.
- `chunk_index` 는 헤딩 단위로 자르고 오버랩을 준다. 메타 토큰(ID·유형·태그)을 모든 청크에 붙여
  `ISSUE-2041 원인?` 같은 질의가 문서 전체를 찾게 한다.
- 채널별로 따로 돌릴 수 있다: `build fts|vector|graph`.
- 빌드 시간 제한은 기본 **48시간**(대규모 초기 색인 때문). 질의의 읽기 대기는 그와 별개로 짧게(900초) 둔다 —
  안 그러면 전체 리빌드 중에 질의 스레드가 쌓인다.

### 4.2 query — 질의 (27단계)

```
sync_index → providers → cache_hit → time_scope → query_rules → router
  → query_expand → pins
  → [fts_search | vector_search | graph_search | doc_vector_search | external_rag]   (병렬 채널)
  → rrf_fuse → doc_acl → fusion_llm → rerank → rerank_review_llm → doc_expand
  → context → evidence → answer → claim
  → forensic → evolve_capture → log → analysis
```

단계마다 **끌 수 있고(토글), 조절할 수 있고(튜닝), 시간 제한이 있고, trace 에 남는다.** §5 에 상세.

### 4.3 evolve — 자가진화 (6단계)

```
capture → hitl → snapshot → apply → regress → evolution_log
```

- 질의가 실패하거나(근거 부족·낮은 groundedness) 사용자가 👎 를 누르면 **제안**이 쌓인다(10종: 동의어·별칭·엔티티·관계·위키노트·질의규칙·핀·튜닝·청킹·코퍼스 공백).
- **사람이 승인한다(HITL).** 자동 적용은 안전한 4종(`synonym`·`query_rule`·`pin`·`wiki_note`)만 기본 허용 —
  `chunk_params` 같은 제안이 무인 전체 리빌드를 촉발하지 않게 하기 위해서다.
- 적용 전 스냅샷, 적용 후 회귀 평가.

### 4.4 watch — 자동 빌드 (2단계)

```
scan → build_incremental
```

---

## 5. 질의 경로 상세 — 각 단계가 무엇을 하고 무엇으로 조절하나

### 5.1 질문을 이해하는 단계

| 단계 | 하는 일 | 주요 손잡이 |
|---|---|---|
| `cache_hit` | 같은 질의의 저장된 답을 돌려준다 | `query_cache`, `query_cache_size` |
| `time_scope` | "지난주", "5월" 같은 표현 → 날짜 범위. boost 또는 filter | `time_scope`, `time_mode`, `time_boost_w`, `recency_half_life_days` |
| `query_rules` | 약어·동의어·별칭·관련어·제외·복합어 확장. **유형마다 방향이 다르다**(약어/동의어는 양방향, 별칭/관련어/제외는 일방) | `query_rules`, `query_rules.json` |
| `router` | 질의 성격(keyword/semantic/relational/hybrid)을 판정해 **채널 가중치**를 정한다 | `router`, `router_llm`, `router_*` 12개 |
| `query_expand` | LLM 으로 대체 질의·키워드 생성, 필요하면 분해 | `query_expand`, `query_decompose`, 역할 `expand` |
| `pins` | 이 질의에 항상 넣을 고정 근거 | `pins`, `pins.json` |

> 이 여섯 단계는 **LLM 없이도 전부 관측 가능하다** — `inspect`(CLI) / Ask › 디버그(Web) / `wiki_inspect`(MCP)가
> 같은 함수(`querydebug.inspect_query`)로 토큰화·확장·시간 범위·라우팅·핀을 한 번에 보여 준다.
> "답이 이상하다" 는 신고를 받으면 여기를 먼저 본다. 비용 0, 수 ms.

### 5.2 찾는 단계 (채널)

| 채널 | 방식 | 강한 질문 | 손잡이 |
|---|---|---|---|
| `fts_search` | SQLite FTS5 BM25 + 동의어 확장 + (옵션) 트라이그램 | 정확한 ID·수치·고유명사 | `top_k_fts`, `fts_topk_*` |
| `vector_search` | numpy 코사인 (hash / voyage / ollama 임베더) | 표현이 다른 같은 뜻 | `top_k_vector`, `embed_*` |
| `graph_search` | 질의→엔티티 매칭 후 n-hop 확장, 멘션 청크 수집 | "A 와 B 는 무슨 관계" 같은 다중 홉 | `top_k_graph`, `graph_hops`, `graph_*` 10여 개 |
| `doc_vector_search` | 문서 단위 벡터 | 주제 수준 매칭 | `doc_vector` |
| `external_rag` | 다른 RAG·검색 API·MCP 서버 | 우리 색인 밖 | `external_rag`, `mcp_sources.json` |

### 5.3 고르는 단계

| 단계 | 하는 일 | 왜 이 순서인가 |
|---|---|---|
| `rrf_fuse` | 채널별 순위를 **가중 RRF** 로 합친다. 라우터 가중치를 쓴다 | 점수 스케일이 다른 채널을 순위로 통일한다 |
| `boost` | 시간·피드백·핀 부스트 | 융합 점수 위에 얹는다 |
| **`doc_acl`** | 요청자의 역할로 **볼 수 없는 문서의 청크를 뺀다** | 융합·부스트 통계는 원래 후보 기준으로 남겨 "무엇이 걸러졌나" 를 볼 수 있게 하고, **리랭크·컨텍스트·답변·인용이 전부 이 아래**에 오게 한다 |
| `fusion_llm` | (옵션) 융합 결과를 LLM 이 검토해 무관한 후보를 감점 | 리랭크 전에 후보 자체를 정리 |
| `rerank` | 휴리스틱(커버리지·합의·길이) 또는 LLM 또는 CE | 상위 k 를 다시 줄 세운다 |
| `rerank_review_llm` | (옵션) 리랭크 뒤 LLM 이 최종 선택 + 통째로 읽을 문서 지정 | 선택된 청크 수는 `top_k_final` 에 매이지 않는다 |
| `doc_expand` | 근거가 나온 문서에서 이웃 청크를 더 가져온다 (keyword/vector/hybrid/**full**) | 표·목록이 청크 경계에서 잘린 문제를 메운다 |

### 5.4 답하는 단계

| 단계 | 하는 일 | 핵심 |
|---|---|---|
| `context` | `[C#]` 블록으로 조립. 중복 제거·문장 압축·인접 청크·그래프 관계 첨부 | 상한은 `context_max_chars` 와 **모델 창** 중 작은 쪽(§6.2). 상한에 걸린 근거는 **잘라서 넣거나** 건너뛰고 다음 순위를 계속 본다. 본문은 **데이터로 취급**해 펜스로 감싼다(`context_guard`) |
| `evidence` | 근거가 질문에 답하기 충분한지 판정. 부족하면 **fallback 4단계**(규칙 확장 → LLM 확장/분해 → 그래프 확대 → 광역) | 시도·토큰·시간 예산으로 무한 루프를 막는다 |
| `answer` | LLM 답변(인용 강제) 또는 추출식. 근거가 부족하면 `grounded` 모드에서 **LLM 을 부르지 않는다** | `answer_mode`: `grounded`(기본) / `best_effort`(배경 지식 `[BK]` 표시) |
| `claim` | 답변을 문장으로 쪼개 **인용이 실제 근거로 지지되는지** 확인 | 없는 인용 번호(`[C99]`)는 `fabricated_citation` 으로 잡고 `bad_citations` 로 보고한다 |

### 5.5 남기는 단계

`forensic`(자동 포렌식) · `evolve_capture`(제안 수집) · `log`(요청·질의 기록) · `analysis`(상세 리포트).
전부 토글로 끌 수 있고, 켜 두면 나중에 되짚을 수 있다.

### 5.6 출력 모드 — 중간 산출물로 멈추기

`output_mode` 로 **답변까지 가지 않고 중간에서 멈출 수 있다**: `fused`(융합 직후 후보) ·
`reranked`(리랭크 직후) · `context`(모델에게 준 컨텍스트 본문) · `answer`(기본).
세 창구가 같은 뜻으로 받는다. 튜닝할 때 LLM 비용 없이 검색 품질만 보는 용도다.

### 5.7 한 질의의 여정 (실제 예)

위 표를 한 번에 이해하려면 **한 질문이 통과하는 길**을 따라가는 편이 빠르다.
`python -m llmwiki query "ISSUE-2001 의 원인과 수정 CL 은?" --trace` (샘플 모뎀 코퍼스, LLM 없이).

| # | 단계 | 이 질문에서 일어난 일 | 결과 |
|---|---|---|---|
| 1 | `cache_hit` | 같은 질문·설정·빌드 버전의 캐시 없음 | miss |
| 2 | `time_scope` | 시간 표현 없음 | 범위 없음 |
| 3 | `query_rules` | "원인"·"수정" 동의어 규칙 발화, `ISSUE-2001` 은 ID 토큰이라 내부 매칭 제외 | 확장식 1개 + 대체 질의 |
| 4 | `router` | 엔티티(ISSUE-2001 노드) 매칭 + 관계어("원인") → **`relational`** | fts 1.3 · vector 0.9 · graph 0.85, 유형 힌트 issue/cl |
| 5 | `fts_search`(+rules/alt) | 원 질의 tiered FTS, 규칙 확장식, 대체 질의를 **각각 별도 리스트**로 | ISSUE-2001 청크 상위 |
| 6 | `vector_search` | hash 임베딩 내적 | 유사 이슈(ISSUE-2010) 포함 |
| 7 | `graph_search` | 시드 `ISSUE-2001` → explicit 관계(CL-55301 fixes ISSUE-2001) 1홉, doc_refs 로 원본 문서 후보 | CL-55301 문서 추가 |
| 8 | `rrf_fuse` → `boost` | RRF 융합 → 라우터 유형 힌트(issue/cl) ×1.2, provenance 부스트 | 상위 6개 |
| 9 | `rerank_local` | 키워드 커버리지·채널 합의·문서 ID 토큰 | `ISSUE-2001.md#원인` 1위 |
| 10 | `context` | `[C1]…[C6]` 블록 조립 + 그래프 관계 한 줄(provenance 표기) | 1.5k chars |
| 11 | `evidence_check` | ID 매칭·키워드 커버리지·상위 점수 충족 | **`sufficient`** (fallback 없음) |
| 12 | `answer_extractive` | LLM 없음 → 근거 원문 문장으로 구조화 답변 | "**ISSUE-2001 · DMA underrun …** — CL-55301 에서 FIFO 임계값 설정 오류 수정 반영 [C2]" |
| 13 | `claim_check` | 문장별 인용 존재·지지 확인 | groundedness 1.0 |
| 14 | 기록 | `requests`(trace) · `query_log` · `episodes` · `logs/query.log`(run_id) | `requests last` · `logs grep --request <id>` |

**같은 질문을 Web Ask 탭이나 MCP `wiki_query` 로 던져도 단계와 기록이 같다** — 세 창구가 한 파이프라인을
쓰기 때문이고, 그것을 [`verify_tri_surface.py`](../tools/verify/verify_tri_surface.py) 가 인용 매핑까지 대조해 지킨다.

근거가 부족한 질문이면 11 에서 `insufficient` → `fallback`(rules → expand → graph → wide → mcp) 라운드가
끼어들고, 그래도 부족하면 12 가 `answer_insufficient` 가 되며 `forensic_auto` 가 원인을 기록한다.

---

## 6. LLM 을 부르는 구조

### 6.1 역할 10개

`answer` `rerank` `extract` `summary` `review` `expand` `verify` `forensic` `fusion` `select`.

역할마다 **모델·프로바이더·effort·타임아웃·재시도·최대 토큰·앙상블**을 따로 정한다(`config.json llm_roles.<role>`).
답변은 큰 모델, 리랭크·확장은 싸고 빠른 모델 — 같은 파이프라인에서 섞어 쓰는 것이 목적이다.

### 6.2 컨텍스트 예산 — 모델 창을 넘지 않게

`models.json` 의 `context_k`(천 토큰)가 실제 상한에 연결된다.

```
여유 토큰 = 창 − 출력 상한(answer_max_tokens) − 예비(context_budget_reserve_tokens)
상한(글자) = min(context_max_chars, 여유 토큰 × context_chars_per_token)
```

창을 모르면 설정값을 그대로 쓴다(모른다고 근거를 줄이지 않는다). 같은 id 가 여러 provider 에 있으면
**가장 좁은 창**을 쓴다 — 넘겨서 잘리는 쪽이 조금 덜 넣는 쪽보다 나쁘기 때문이다.

### 6.3 앙상블

역할 하나에 모델을 최대 3개까지 두고 **병렬로 부른 뒤 합친다**. 집계(aggregator)는 **2개 이상 성공했을 때만** 돈다
(1개면 합칠 것이 없다). 구성원은 그 단계의 프롬프트를 공유하고, 병합 프롬프트는 역할별 파일
(`prompts/ensemble_merge_<role>.md`, 없으면 `ensemble_merge.md`)을 쓴다. 자세히는 [ENSEMBLE.md](ENSEMBLE.md).

### 6.4 실패를 다루는 방식

| 장치 | 동작 |
|---|---|
| 재시도 | `llm_retries` × 지수 백오프(`llm_retry_backoff_s`, 상한 `llm_retry_backoff_max_s`). transient 오류만 |
| 예산 | `llm_budget_s` — 한 요청이 LLM 에 쓸 총 시간 |
| 회로 차단 | 같은 모델이 연속 실패하면 일정 시간 건너뛴다(`server circuits`) |
| 강등 | `degrade_on_llm_failure` — LLM 이 죽어도 **이미 찾은 근거로 추출식 답**을 준다. 500 이 아니다 |
| 보고 | `llm_failure_report` — 어느 역할이 왜 실패했는지 응답과 trace 에 남는다 |
| headless | `agents.json` 의 CLI 에이전트(opencode 등)도 같은 정책을 받는다 |

**프로바이더를 바꾸는 것은 `config.json` 편집뿐이다.** 게이트웨이 + PAT 든 로컬 ollama 든 headless 든
코드는 그대로다.

---

## 7. 설정 체계 — 4층과 우선순위

```
① config.json (109키) + toggles(68)      ← 서버 기본값. 파일에 기본값까지 명시되어 있다
② tuning.json (163키)                     ← 알고리즘 상수. 단계별로 묶여 있고 범위 검증이 붙는다
③ presets.json (quality/speed/token/offline/deep_research)  ← 묶음 전환
④ 요청 단위 overrides                      ← 이 질의 한 번만. 파일에 남지 않는다
```

**우선순위는 아래가 이긴다.** `.env` 의 `LLMWIKI_<KEY>` / `LLMWIKI_TOGGLE_<NAME>` / `LLMWIKI_<ROLE>_MODEL` 은
①을 프로세스 시작 때 덮는다.

### 7.1 요청 단위 overrides 의 안전장치

`apply_overrides` 는 `Settings` 의 **모든** 필드를 받는다. 그래서 읽기 등급(익명 포함)이
`{"overrides": {"openai_base_url": "http://attacker/v1"}}` 를 보내면 서버가 `.env` 의 PAT 를 그 주소로 보낼 수 있었다.
지금은 **화이트리스트**(`OVERRIDE_SAFE_KEYS`)로 "질의 한 번의 동작을 바꾸는 손잡이" 만 열고,
URL·헤더·경로·서버 운영 키는 admin 에게만 허용한다. 목록 조정은 `security.json` 의
`overrides.allow_extra` / `overrides.deny` — 코드 수정 없이. ([SECURITY.md §6.1](SECURITY.md))

### 7.2 Web UI ↔ 파일 ↔ 서버 양방향

화면에서 바꾼 값이 파일에 쓰이고 **돌아가는 서버에 반영되는지**, 그리고 파일을 직접 고쳤을 때 화면이
그것을 반영하는지 — 둘 다 자동 검증한다(`verify_settings_sync.py`). 한쪽만 되는 상태가 가장 나쁘다.

---

## 8. 세 창구

### 8.1 배치 원칙

| 원칙 | 뜻 |
|---|---|
| **기능은 셋 다** | 기능마다 CLI 명령·Web 경로·MCP 도구가 있어야 한다. 없으면 **왜 없는지**를 표에 적는다 |
| **MCP 는 읽기 전용** | 색인을 바꾸는 도구는 없다. `wiki_propose`/`wiki_feedback` 도 제안 큐에 쌓을 뿐이다 |
| **설정 편집은 admin 화면/CLI** | 외부 LLM 에게 설정 쓰기 권한을 주지 않는다 |
| **같은 값을 돌려준다** | 존재만으로는 부족하다. 인용 매핑·근거 순서·판정이 같아야 한다 |

검증: `verify_surface_align.py`(존재, 기능 59개) + `tests/test_surface_consistency.py`(값).

### 8.2 CLI (46개 명령)

질의·검색·해부·재실행·스윕 / 빌드·검증·코퍼스·임베딩 / 규칙·핀·프롬프트·튜닝·프리셋·설정 /
평가·시험·분석·포렌식·최적화 / 사용자·보안·API키·스냅샷·서버·로그·스케줄 / MCP·외부소스·워처·유지보수.
전역 옵션 `--user/--password` 로 권한 게이트를 받고, 거부는 종료 코드 5.

### 8.3 Web UI (경로 107개, 화면 8그룹)

Ask / **🧭 Pipeline** / Corpus / Knowledge / Quality / Evolve / Settings / Observability.
프론트는 의존성 없는 바닐라 JS 10개 모듈(`core.js` 가 공용 헬퍼·API·모달·테마).

- **Ask** — 질의, 채널 검색(FTS/Vector/Graph 를 AND/OR/제외로 조합), 디버그(질의 해부), 상세 분석 리포트.
- **🧭 Pipeline** — 단계 구조를 **읽는 자리와 고치는 자리가 하나**다. 각 단계의 토글·튜닝·시간 제한·실측이
  한 화면에 있고, 거기서 바로 스윕을 돌린다.
- **Settings** — 모델·프로바이더(연결 테스트·자동 매핑), 튜닝, 프리셋, 프롬프트, 설정 파일, 보안(권한 표·
  API 키·**문서 접근 제어**·스냅샷·감사 로그).
- **Observability** — 진행 중 작업, 질의·로그(누가 무엇을 물었나), 서버 모니터(연결 풀·회로·제한).

### 8.4 MCP (도구 <!--live:mcp-->20개)

stdio(같은 PC) · Streamable HTTP(`POST /mcp`, Bearer API 키, 여러 LLM 동시) · 브리지(stdio 전용 클라이언트 → 원격).
도구마다 `annotations`(읽기/쓰기·외부 접근)를 주고, `tools/call` 은 실행 전에 인자를 검증해 **실패 이유와 스키마**를
돌려준다(붙은 LLM 이 스스로 고쳐 다시 부른다). 모든 응답은 사람이 읽는 `text` 와 기계가 읽는
`structuredContent` 를 **둘 다** 준다 — 필드 이름은 Web `/api/query` 와 같다.

플러그인(`plugins/mcp_tools/*.py`)으로 코드 수정 없이 도구를 추가할 수 있고,
페더레이션으로 다른 팀의 MCP 서버 도구를 `<source>__<tool>` 로 중계할 수 있다.

---

## 9. 동시성 모델

### 9.1 요청 하나의 격리

```python
with pipe.request_scope(overrides=…, presets=…, mode=…, actor=…):
    ...
```

이 한 줄이 만드는 것: **설정 사본**(다른 요청과 안 섞임) · **튜닝 오버레이**(push/pop) ·
**스레드 전용 DB 연결** · **요청자 신분**(문서 접근 제어가 쓴다) · **프로파일러 카운터**.
Web·MCP·CLI 콘솔·스케줄러·워처가 전부 이걸로 감싼다.

### 9.2 용량 제어 (`reqmgr.py`, `server.json`)

| 장치 | 뜻 |
|---|---|
| 티켓 | 모든 요청이 티켓을 받는다. 토큰이 곧 진행 표시·취소의 열쇠 |
| RWLock | 읽기는 동시, 쓰기는 배타. 증분 빌드는 `soft`(읽기와 공존), 전체 리빌드는 `exclusive` |
| 동시 수 | 전체 / 사용자별 / IP별 / 배치 작업 |
| 대기열 | 길이·대기 시간 상한. 넘으면 503 `queue_full` |
| 속도 제한 | 분당 요청 수(사용자·IP) |
| 취소 | 진행 중 작업을 토큰으로 중지. LLM 대기 중에도 깨어난다 |
| 시간 제한 | 종류별(질의·CLI·빌드·잡) |

거부는 **429/503 + `Retry-After` + `error.data.code`** 로 무엇에 걸렸는지 구분할 수 있게 온다.

### 9.3 락 획득 순서

```
RequestManager._lock  →  RWLock._cv  →  progress._LOCK  →  Store._pool_lock
```

DB 세션은 **가장 안쪽**에서만 연다. 새 락은 이 사슬의 **끝**에 붙인다. ([CONCURRENCY.md §8.2](CONCURRENCY.md))

### 9.4 연결 풀

`db_pool_size`(16)는 **놀고 있는** 연결만 제한한다. 빌려 나간 연결은 `db_max_live_connections`(64)로
**부드럽게** 제한하고 `live`/`peak_live`/`created`/`overflow` 를 센다 — 한가한데 `live` 가 0 이 아니면 세션 누수다.
상한을 넘어도 요청을 죽이지 않는다(기다렸다 만들고 `overflow` 로 센다). 하드 캡이면 채널 검색처럼
상위 스레드가 연결을 쥔 채 하위 스레드가 연결을 더 쓰는 자리에서 교착이 나기 때문이다.

---

## 10. 보안

네 겹이다. 자세히는 [SECURITY.md](SECURITY.md).

| 겹 | 무엇을 막나 | 어디 |
|---|---|---|
| **인증** | 누구인지 모르는 접근 | 로컬 ID/비밀번호(PBKDF2) + SSO(OIDC·프록시 헤더) + API 키. **병행** |
| **작업 권한** | 할 수 없는 일을 하는 것 | 역할 6단계 × 등급 7단계 + admin 이 편집하는 권한 표. 리빌드/파괴적 작업은 확인 문구 + 비밀번호 재입력 + 자동 스냅샷 |
| **문서 접근 제어** | 볼 수 없는 문서를 **근거로 받는 것** | `docacl.json` 경로 규칙 + 문서 front matter `acl:` (높은 쪽 적용). 질의 근거·채널 검색·문서 열람·MCP **네 출구 전부** |
| **인젝션 방어** | 문서가 모델에게 직접 지시하는 것 | `ctxguard.py` — 구획·역할·인용 흉내를 무력화해 펜스로 감싼다. **내용은 지우지 않는다** |

**요청자 신분이 검색까지 간다**는 것이 문서 접근 제어의 핵심이다(`Pipeline.actor`, 스레드 로컬).
CLI·스케줄러·내부 호출은 신분을 주지 않아 admin 으로 동작한다(로컬 운영자 도구).

감사는 `logs/audit.jsonl` — 로그인/로그아웃, `read` 를 제외한 모든 작업, 모든 거부.

---

## 11. 관측 — "왜 이렇게 나왔나" 에 답하는 다섯 가지

| 도구 | 무엇을 보여 주나 | 어디서 |
|---|---|---|
| **trace** (`profiler.py`) | 단계별 시간·입출력·건너뛴 이유·SQL 수·LLM 호출. 이름 82종 | 모든 질의 응답 · `--trace` · Web 폭포 그림 |
| **진행 표시** (`progress.py`) | 실행 중 단계·대기열 위치·LLM 대기·취소 | `/api/progress/<token>` 폴링 · CLI 모니터 |
| **질의 해부** (`querydebug.py`) | LLM 없이, 질문이 무엇으로 변했나 | `inspect` / Ask › 디버그 / `wiki_inspect` |
| **분석 리포트** (`analysis.py`) | 설정 스냅샷 + 단계 타임라인 + 채널별 상위 + 융합/리랭크 전후 + 컨텍스트 + 판정 + **세 렌즈(품질·속도·토큰) 소견과 조절점** | `analyze` / Ask › 📊 / `wiki_analysis` |
| **포렌식** (`forensic.py`) | "이 문서가 나왔어야 하는데 **어느 단계에서 탈락했나**" + 수정안 | `forensic expect` / `wiki_forensic` |

여기에 **재실행**(`rerun` — 특정 단계부터 다시, 앞 단계는 저장된 결과를 재생)과
**스윕**(`sweep` — 키 하나의 값을 바꿔 가며 값별로 재생 재실행하고 비교)이 붙는다.
두 기능 덕분에 "이 값을 바꾸면 어떻게 되나" 를 LLM 비용 거의 없이 확인할 수 있다.

---

## 12. 검색 품질을 올리는 장치

| 장치 | 무엇 | 파일 |
|---|---|---|
| 질의 확장 사전 | 약어·동의어·별칭·관련어·제외·복합어. **유형마다 방향이 다르다** | `query_rules.json` |
| 규칙 효과 집계 | 어떤 규칙이 실제로 발화했고 도움이 됐나. 죽은 규칙을 찾는다 | `ruleeffect.py` |
| 불용어 | 질의 키워드 추출에서 제거 | `stopwords.json` |
| 그래프 규칙 | 엔티티 사전·관계 정규식·ID 패턴·결정적 링크 규칙 | `data/rules.json` |
| 고정 근거(pin) | 특정 질의에 항상 넣을 근거 | `pins.json` |
| 피드백 부스트 | 👍 받은 근거를 다음에 올린다 | `feedback_boost` |
| 문서 단위 확장 | 근거가 나온 문서를 더 읽는다(`full` 이면 통째로) | `doc_expand_*` |
| 스키마 린트 | 문서 계약(유형별 필수 필드·섹션·ID 패턴)을 빌드에서 점검 | `schemas/*.json` |

---

## 13. 확장 지점 — 코드를 어디에 넣나

| 하고 싶은 것 | 어디 | 규모 |
|---|---|---|
| 새 검색 채널 | `query_engine._retrieve` 의 `lists[...]` 에 리스트 추가 → 융합·부스트·프로파일에 **자동 포함** | 수십 줄 |
| 새 LLM 프로바이더 | `providers.py` 에 `BaseLLM` 서브클래스 + `_make_llm` 등록 | ~30줄 |
| 새 임베더 | `providers.py` 에 `BaseEmbedder` + `make_embedder` 등록 | ~30줄 |
| 새 MCP 도구 | `plugins/mcp_tools/*.py` 에 `register(add_tool)` — **코드 수정 없이** | 파일 하나 |
| 새 외부 RAG 전송 | `mcp_client.py` 에 `call_tool(name, args)` 클라이언트 + `open_source` 등록 | ~50줄 |
| 새 문서 유형 | `schemas/<type>.json` — 코드 수정 없음 | 파일 하나 |
| 새 헤드리스 에이전트 | `agents.json` 에 명령 템플릿 — 코드 수정 없음 | 파일 한 줄 |
| 새 단계 | §14 의 네 곳을 **모두** 맞춰야 한다 | — |

---

## 14. 정합 불변식 — 깨지면 어디서 잡히나

이 프로젝트가 반복해서 겪은 실패는 "설정은 있는데 배선이 없는 기능" 과 "한 창구에만 있는 기능" 이다.
그래서 불변식을 검증으로 고정해 두었다.

| 불변식 | 무엇이 어긋나면 | 잡는 곳 |
|---|---|---|
| **단계 이름 4곳 일치** — 코드 `prof.stage()` ↔ `architecture.FLOWS[*].trace` ↔ `progress.STAGE_LABELS` ↔ 토글/설정/튜닝 | Pipeline 화면에서 단계가 안 보이거나, 토글이 화면에 안 뜨거나, 진행 표시가 영문 키로 뜬다 | `verify_stage_align.py` (16검사) |
| **기능 3창구 존재** | 한 창구에만 있는 기능 | `verify_surface_align.py` (기능 59개) |
| **3창구 값 일치** | 인용 번호·근거 순서·판정이 창구마다 다름 | `tests/test_surface_consistency.py` |
| **설정 양방향** | 화면에서 바꾼 값이 서버에 반영 안 됨, 또는 파일 수정이 화면에 안 보임 | `verify_settings_sync.py` |
| **모든 토글이 그룹·효과표에 있음** | 사이드바에서 '기타' 로 밀리거나 품질/속도/토큰 배지가 없음 | `tests/test_review_0917.py` |
| **모든 설정 키에 도움말** | 화면에 설명 없는 입력칸 | 같은 파일 |
| **JS 가 부르는 경로가 서버에 있음** | 버튼이 조용히 죽음 | `verify_ui_wiring.py` |
| **문서 링크·숫자 정합** | 없는 문서 참조, 아무도 링크하지 않는 고아 문서 | `verify_docs.py` |

---

## 15. 검증 체계

```bat
python -m unittest discover -s tests      :: 단위 716개 (약 3분)
python tools\verify\verify_all.py         :: 하니스 22종 (약 30~60분, 몽키 포함)
```

| 하니스 | 무엇 |
|---|---|
| `verify_cli.py` | CLI 명령 전수 왕복 |
| `verify_web.py` | Web 엔드포인트 377검사(권한·overrides 가드·출력 모드·CSRF 포함) |
| `verify_mcp.py` | MCP 154검사(stdio·HTTP·브리지·페더레이션·동시 키) |
| `verify_surface_align.py` · `verify_stage_align.py` | 정합 불변식 |
| `verify_settings_sync.py` | 설정 양방향 |
| `verify_ui_wiring.py` · `verify_browser.py` · `verify_responsive.py` · `verify_buttons.py` | 프론트 |
| `verify_security_ui.py` · `verify_rerun_ui.py` · `verify_schedule_ui.py` | 화면별 왕복 |
| `verify_timeouts.py` · `verify_soak.py` · `verify_collab_many.py` | 실패 경로·장시간·다중 접속 |
| `verify_monkey.py` | 무작위 입력 폭격(Web·MCP·CLI) |
| `verify_docs.py` | 문서 정합 |

**무엇을 고쳤을 때 무엇을 돌리나**는 [TESTING_GUIDE.md](TESTING_GUIDE.md) §1 의 표에 있다.

---

## 16. 한계와 다음 단계 (정직하게)

| 항목 | 지금 | 필요해지는 조건 |
|---|---|---|
| 토큰 계산 | 글자 수 × 환산 비율(`context_chars_per_token`). 진짜 토크나이저 없음 | 토큰 과금을 정확히 맞춰야 할 때 |
| 벡터 규모 | numpy 인메모리 행렬. 문서 수만 건까지 편안 | 수십만 건 이상 |
| 그래프 엔티티 이름 | 접근 제어 대상이 아니다(본문이 아니라 추출된 이름) | 엔티티 이름 자체가 비밀일 때 |
| HTTPS | 리버스 프록시에서 종료 | 항상 |
| 세션 즉시 폐기 | 서명 쿠키라 개별 세션 목록이 없다. `data/.session_secret` 삭제 = 전원 로그아웃 | 개별 강제 로그아웃이 필요할 때 |
| 스트리밍 답변 | 없음(인용 검증을 완성 뒤에 하기 위해) | 체감 지연이 문제가 될 때 — 그때도 claim_check 를 어떻게 할지 먼저 정해야 한다 |

---

## 17. 이 문서의 숫자를 다시 세는 법

```bat
python -m llmwiki arch                    :: 흐름·단계 (레지스트리)
python -m llmwiki arch limits             :: 단계별 시간 제한
python -m llmwiki tuning show             :: 튜닝 키
python -m llmwiki config show --effective :: 설정 키와 현재 값
python -m llmwiki mcp --doctor            :: MCP 도구 수·상태
python tools\verify\verify_surface_align.py --md   :: 기능 × 3창구 표 (마크다운)
python -m llmwiki tuning doc              :: docs/TUNING.md 재생성
python -m llmwiki arch doc                :: docs/OPTIMIZATION_GUIDE.md 재생성
```

**`docs/TUNING.md` 와 `docs/OPTIMIZATION_GUIDE.md` 는 코드에서 자동 생성된다** — 단계나 설정이 바뀌면
문서도 바뀐다. 손으로 고치지 말고 재생성한다.

---

## 18. 문서 지도

| 알고 싶은 것 | 문서 |
|---|---|
| 새 환경에 올리기 | [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) |
| 손잡이 사전(어떤 토글·튜닝이 어느 단계에) | [OPTIMIZATION_GUIDE.md](OPTIMIZATION_GUIDE.md) · [TUNING.md](TUNING.md) |
| 로그인·권한·문서 접근 제어·인젝션 | [SECURITY.md](SECURITY.md) |
| 동시성·락·연결 풀·시간 제한 | [CONCURRENCY.md](CONCURRENCY.md) |
| 외부 LLM 연결 | [MCP.md](MCP.md) · [RAG_FEDERATION.md](RAG_FEDERATION.md) |
| Web 화면 구조 | [WEB_UI.md](WEB_UI.md) · [PIPELINE_PAGE.md](PIPELINE_PAGE.md) |
| CLI 흐름 | [CLI_FLOWS.md](CLI_FLOWS.md) |
| 답변 모드·근거 판정 | [ANSWER_MODES.md](ANSWER_MODES.md) |
| 검색 품질 규칙 | [QUERY_RULES.md](QUERY_RULES.md) · [STOPWORDS.md](STOPWORDS.md) |
| 자가진화 | [EVOLVE.md](EVOLVE.md) |
| 진단·튜닝 도구 | [ANALYSIS_MODE.md](ANALYSIS_MODE.md) · [FORENSIC.md](FORENSIC.md) · [RERUN.md](RERUN.md) · [SWEEP.md](SWEEP.md) · [GRAPH_PROFILE.md](GRAPH_PROFILE.md) |
| 앙상블 | [ENSEMBLE.md](ENSEMBLE.md) |
| 무엇을 고쳤을 때 무엇을 돌리나 | [TESTING_GUIDE.md](TESTING_GUIDE.md) |
| 최근 QA 강화와 찾은 결함 | [QA_HARDENING_0919.md](history/2026-09-19/QA_HARDENING_0919.md) |
| v3 엔진의 설계 배경 | [ARCHITECTURE_V3.md](history/2026-09-15/ARCHITECTURE_V3.md) |
