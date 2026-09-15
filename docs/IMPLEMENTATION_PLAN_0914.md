# 구현 계획서 (2026-09-14) — 다중 사용자 권한 · MCP 원격 · 채널 빌드 · 문서 단위 확장 · headless 재시도 · 기대 결과 포렌식

> 대상: 이 시스템을 사내 서버에 올려 여러 사람과 여러 외부 LLM 이 쓰게 하려는 운영자, 그리고 이 문서만 보고 같은 동작을 재현·검증해야 하는 다른 LLM/개발자.
> 사용자 요청 6항목(§0)에 대해 **왜 이렇게 만들었는지(대안 검토 포함)**, **무엇을 어디에 만들었는지(파일·CLI·API·설정 키)**, **어떻게 검증했는지**를 적는다.
> 상태: **구현 완료 · 검증 완료** (§7). 운영 절차는 [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md), 권한 상세는 [SECURITY.md](SECURITY.md), MCP 는 [MCP.md](MCP.md), 포렌식은 [FORENSIC.md](FORENSIC.md).

## 0. 요청 요약과 판정

| # | 요청 (요약) | 판정 | 채택안 (한 줄) |
|---|---|---|---|
| 1 | 다수 사용자 서버. DB 에 영향을 주는 CLI/Web 기능을 역할로 제한. 역할 `admin, builder, class1, class2, class3, viewer`. 역할별 권한을 admin 이 추가/변경. 기본 접근자는 viewer(DB 무영향 기능 전부). admin 계정 `kh82.kim / 1234qwer` (추후 변경 가능) | ✅ 그대로 + 보강 | 6단계 역할 + **7단계 작업 등급** + `security.json → permissions` 로 등급·개별 작업의 최소 역할을 admin 이 편집. **익명 접속 = viewer**(`anonymous_role`). CLI 도 같은 표로 게이트(`cli.default_role`, `--user`). MCP/스크립트용 **API 키**(역할 부여). 기본 admin 계정 생성. |
| 2 | 다수의 외부 LLM 이 요청하는 MCP. Windows/Linux, 같은 PC/외부 PC 모두 | ✅ 그대로 + 보강 | 기존 stdio 유지 + **Streamable HTTP 전송**(`POST /mcp`, Web 서버와 같은 포트 또는 단독 포트) + **Bearer API 키** + stdio 전용 클라이언트용 **브리지**(`mcp --connect URL`). 표준 라이브러리만. |
| 3 | fts / vector / graph 를 각각 빌드. 토글 on/off, CLI 제공. "서로 영향 없나?" | ✅ 그대로 (의존성 명시) | `build fts|vector|graph [--full]` + 토글 `build_fts`/`embed`/`rule_graph`·`llm_graph` + `--channels`. 세 채널은 모두 **chunks 테이블을 읽기만** 하므로 서로 독립. 단, 청킹(문서 변경·chunk_* 설정)이 바뀌면 세 채널 모두 stale → `build verify` 가 알려줌(§3.3). |
| 4 | chunk 단위 검색 후, 선택된 chunk 와 같은 문서의 관련 chunk 를 추가 포함(토글) | ✅ 그대로 + 상한 | `doc_expand` 토글 + 튜닝 4개. 상위 N 문서에 대해 같은 문서의 나머지 청크를 **키워드 커버리지 + 벡터 유사도(hybrid)** 로 점수화해 임계 이상만 문서 순서대로 컨텍스트에 추가(`[C#]` 인용 가능, why=doc_expand). 토큰 상한 존중. |
| 5 | headless 모델 재시도: 5분 timeout, timeout 시 3회 재시도, 최종 실패 시 fail 처리 + 지금까지 결과로 상황·결과 report. 설정 파일로 제어 | ✅ 그대로 + 일반화 | headless(`agents.json timeout_s=300, retries=3`) 뿐 아니라 **모든 프로바이더**의 timeout/네트워크 오류에 공통 재시도(`config.json llm_retries`, `llm_retry_backoff_s`). 실패는 요청 결과에 `llm_report`(역할·시도 횟수·오류·대체 경로)로 남고 답변 상단/빌드 알림에 표시. |
| 6 | 포렌식 검증. 사용자가 "어떤 결과가 필요했는지" 피드백 → 왜 그 결과가 포함되지 않았는지 단계별(어떤 evidence/검색 결과가 어느 단계에서 빠졌는지) 분석. 모든 질의 결과에서 요청 가능 | ✅ 그대로 | 기존 자동 포렌식(왜 답이 부실했나) 위에 **기대 결과 포렌식**(`forensic expect`) 추가: 기대 문서/용어를 주면 같은 설정으로 검색을 재실행해 **기대 청크가 fts/vector/graph → 융합·부스트 → 리랭크 → 컨텍스트 → 답변** 의 어느 단계에서 탈락했는지와 원인·수정안(규칙/pin/튜닝/코퍼스)을 표로 준다. Web(Ask 결과 하단) · CLI · MCP(`wiki_forensic`) 모두 제공. |

### 0.1 사용자 제안과 다르게 한 곳 (검토 결과)

1. **역할 6단계 + "등급표" 유지**: 기능 하나하나에 역할을 붙이는 대신, 서버가 이미 쓰는 **작업 등급표**(read < run < edit < index < rebuild < admin < destructive)에 **등급별 최소 역할**을 두고, 예외는 **개별 작업 오버라이드**로 둔다. 이유: 엔드포인트가 60개가 넘어 낱개 편집은 실수가 잦고, 새 기능이 추가될 때 표에 한 줄만 넣으면 권한이 자동으로 따라온다. admin 은 Web 보안 탭이나 `security perms set` 으로 두 층 모두 바꿀 수 있어 요청하신 "admin 이 추가" 요구를 만족한다.
2. **class1/2/3 의 기본 의미를 정의**: 요청에는 등급 이름만 있고 의미가 없어, 실무 흐름(조회 → 실행 → 지식 편집 → 색인 갱신 → 리빌드 → 설정)에 맞춰 기본값을 정했다(§1.2). 조직에 맞지 않으면 `permissions` 만 고치면 된다(코드 변경 없음).
3. **"DB 에 영향 없는 기능 = viewer" 의 경계**: 질의·검색·조회·피드백·제안 등록은 viewer 이지만, `eval`/`trial run`/`models test --live` 처럼 **토큰·시간을 크게 쓰는 실행**은 class3 부터로 두었다(DB 는 안 바꾸지만 비용을 쓴다). viewer 에게도 주려면 `permissions.levels.run = viewer`.
4. **익명 접속**: "Web UI 에 접근하는 모든 사람은 viewer" 를 문자 그대로 구현하려면 로그인 없이도 viewer 여야 한다. `anonymous_role: "viewer"` 를 기본으로 두고, 로그인이 필요한 조직은 `""` 로 바꾸면 종전처럼 로그인 화면이 먼저 뜬다.
5. **CLI 권한**: CLI 는 서버 OS 계정으로 실행되므로 종전에는 admin 으로 봤다. 공용 서버에서 누구나 셸을 가질 수 있으면 `security.json → cli.default_role` 을 `viewer` 로 낮추고 `--user`/`LLMWIKI_USER`+`LLMWIKI_PASSWORD` 로 승격하게 했다. 기본값은 호환을 위해 `admin`.
6. **MCP 는 Web 서버에 통합**: 포트를 하나만 열면 되고(방화벽·리버스 프록시 규칙 1개), 인증·감사·락을 Web 과 공유한다. 단독 포트가 필요하면 `mcp --transport http --port 8766` 로도 띄울 수 있다.
7. **doc_expand 기본 ON, 상한 있음**: 품질 향상 기대는 동의하지만 무제한 확장은 토큰·지연을 키운다. 문서 3개 × 청크 3개 × 임계 0.2 를 기본으로 두고 `trial` 로 on/off 를 비교했다(§7). 문서 전체를 넣고 싶으면 `doc_expand_max_chunks` 를 올리거나 `doc_expand_min_score=0`.
8. **재시도는 전 프로바이더 공통**: 게이트웨이 PAT 경로도 같은 timeout 문제가 있으므로 headless 전용이 아니라 `BaseLLM` 에서 한 번 처리한다. 내부에서 이미 재시도하는 HTTP 429/5xx 는 이중 재시도하지 않도록 "transient" 표식으로 구분한다.

## 1. 권한 (요청 1)

### 1.1 설계
- 역할(낮→높): `viewer < class3 < class2 < class1 < builder < admin`. 구 역할 `operator` 는 `class1` 의 별칭으로 계속 인식(기존 security.json 호환).
- 작업 등급 7단계와 기본 최소 역할·확인 방식:

| 등급 | 뜻 | 기본 최소 역할 | 확인 | 대표 작업 |
|---|---|---|---|---|
| `read` | 조회·질의 (DB 무영향; 로그만 남음) | viewer | 없음 | query, search, 문서/그래프/위키 조회, 피드백, 제안 등록, `forensic expect`, pins test, 규칙/시간 테스트 |
| `run` | 토큰·시간을 쓰는 실행 (DB 무영향) | class3 | 없음 | eval, trial run, fusion compare, models test, evolve review, Web 콘솔(읽기 명령), MCP 소스 test/enrich |
| `edit` | 지식 데이터 편집 (되돌릴 수 있음, 색인 불변) | class2 | 대화상자 | pin/규칙/프롬프트/튜닝/프리셋 저장, 위키 편집, 제안 apply/reject, memory decay/consolidate, trial 삭제 |
| `index` | 색인 갱신 (증분·복구 가능) | class1 | 대화상자 | 증분 build, verify --fix, precompute, maintenance(vacuum 등), 스냅샷 생성/prune, 워처 시작/중지, MCP ingest |
| `rebuild` | 색인 전체/채널 리빌드, 복원 | builder | 대화상자 + 확인 문구 + (로컬) 비밀번호 | build --full/--reset, `build fts|vector|graph`, snapshot restore |
| `admin` | 설정·프로바이더·사용자 | admin | 대화상자 | config/models/agents/mcp_sources 저장, users, security(perms/api keys), 워처 설정 저장 |
| `destructive` | 로그·이력까지 삭제, 설정 초기화 | admin | 대화상자 + 확인 문구 + 비밀번호 | build --purge-logs, maintenance purge_requests, config reset, users remove |

- `security.json`:
  ```jsonc
  "anonymous_role": "viewer",                 // 로그인 없이 접속한 사람의 역할. "" 이면 로그인 필수
  "permissions": {
    "levels": {"read": "viewer", "run": "class3", "edit": "class2", "index": "class1", "rebuild": "builder", "admin": "admin", "destructive": "admin"},
    "ops": {"/api/eval": "viewer", "cli:trial run": "class2"}   // 개별 작업 오버라이드 (op 문자열은 감사 로그의 op 와 동일)
  },
  "cli": {"default_role": "admin", "require_login": false},     // CLI 실행자의 기본 역할. viewer 로 두면 --user 로 승격
  "api_keys": {"k1": {"name": "claude-desktop-kim", "role": "viewer", "hash": "sha256…", "created": …}},
  "users": {"kh82.kim": {"role": "admin", "pw": "pbkdf2_sha256$…"}}
  ```
- 판정 함수: `auth.min_role(level, op)` = `permissions.ops[op]` → `permissions.levels[level]` → 코드 기본값. `Auth.authorize()` 가 이 값을 쓴다. 확인 방식은 등급에 고정(`LEVEL_CONFIRM`)이며 문구·재인증 여부는 종전 `destructive` 설정을 그대로 쓴다.
- 신원 확인 순서: 리버스 프록시 헤더 SSO → `Authorization: Bearer lwk_…`(API 키) → 세션 쿠키 → (없으면) `anonymous_role`.
- CLI: `cli.run()` 이 `classify_cli(argv)` → `min_role` → 실행자 역할(기본 `cli.default_role`; `--user`/`LLMWIKI_USER` + `LLMWIKI_PASSWORD` 또는 프롬프트로 로컬 계정 인증)과 비교. 부족하면 종료 코드 5 + 감사 로그 DENY. Web 콘솔(`/api/cli`)은 서버가 이미 판정했으므로 CLI 게이트를 다시 타지 않는다.

### 1.2 왜 이 기본값인가
- viewer 가 "DB 무영향 기능 전부" 를 갖는다는 요구를 `read` 로 만족시키고, 비용이 큰 실행은 한 단계 위(class3)에 둔다.
- class2 는 "지식을 다듬는 사람"(규칙·pin·프롬프트·제안 승인), class1 은 "색인을 갱신하는 사람"(증분 빌드·정합성 정리), builder 는 "색인을 새로 만드는 사람"(전체/채널 리빌드·복원), admin 은 "시스템을 바꾸는 사람".
- 모든 경계는 `permissions` 로 이동 가능하므로 조직마다 재해석해도 코드 변경이 없다.

### 1.3 파일
`llmwiki/auth.py`(역할·등급·permissions·API 키·익명), `llmwiki/cli.py`(CLI 게이트, `security perms|apikey`, `users` 역할 목록), `llmwiki/web/server.py`(identify 순서, `/api/security` action=set_permissions|apikeys, 익명 처리), `web/static/js/core.js`(게스트 배지·로그인 링크), `settings.js`(권한 표·API 키 편집), `login.html`(게스트 상태에서도 로그인 가능), `security.json`(기본 admin 계정), `docs/SECURITY.md`, `tests/test_permissions.py`.

## 2. MCP 다중 클라이언트·원격 (요청 2)

### 2.1 설계
- 전송 두 가지:
  1. **stdio** (종전): 같은 PC 의 클라이언트가 `python -m llmwiki mcp` 를 자식 프로세스로 띄움. 클라이언트마다 프로세스 1개(각자 SQLite 를 읽기 전용에 가깝게 열어도 안전 — WAL).
  2. **Streamable HTTP** (신규, MCP 2025-03-26/06-18 규격의 JSON 응답 모드): `POST /mcp` 에 JSON-RPC 2.0(단건/배열) → JSON 응답. `initialize` 응답에 `Mcp-Session-Id` 헤더를 돌려주고 이후 요청의 해당 헤더를 받아들인다(상태는 서버가 갖지 않으므로 어떤 값이든 허용). `GET /mcp` 는 405(서버 푸시 스트림 미지원 — 도구가 모두 요청-응답형이라 필요 없음), `DELETE /mcp` 는 200.
- 위치: Web 서버(`serve`)가 `/mcp` 를 함께 제공한다(포트 하나). 단독으로 띄우려면 `python -m llmwiki mcp --transport http --host 0.0.0.0 --port 8766`(같은 핸들러, `/mcp` 와 `/api/auth/me` 만).
- 인증: `Authorization: Bearer lwk_<id>_<secret>`(API 키, 역할 부여) 또는 브라우저 세션 쿠키. `anonymous_role` 이 viewer 면 토큰 없이도 읽기 도구 사용 가능(사내망 전용일 때). 모든 MCP 도구는 `read` 등급(색인을 바꾸지 않음), `wiki_propose`/`wiki_feedback`/`wiki_forensic` 도 read(제안·피드백·진단 기록만).
- 동시성: `ThreadingHTTPServer` 가 요청마다 스레드를 쓰고, 파이프라인 접근은 서버 전역 `RLock` 으로 직렬화된다(한 프로세스에 SQLite 연결 1개, 질의 중 프리셋이 파이프라인 설정을 잠시 바꾸기 때문). 질의 1건이 수 초이므로 외부 LLM 수십 개가 동시에 붙어도 큐잉만 될 뿐 실패하지 않는다. 처리량이 더 필요하면 서버 프로세스를 여러 개 띄우고 프록시로 분배(각 프로세스는 같은 DB 파일을 읽는다).
- stdio 전용 클라이언트가 원격 서버를 써야 할 때: **브리지** `python -m llmwiki mcp --connect http://host:8765/mcp --token lwk_…` (또는 `LLMWIKI_MCP_URL`/`LLMWIKI_MCP_TOKEN`). stdin 의 JSON-RPC 를 HTTP 로 넘기고 응답을 stdout 으로 되돌린다. Windows/Linux 모두 Python 만 있으면 된다.
- 도구 추가: `wiki_forensic(request_id, expected_docs, expected_terms, note)`, `wiki_feedback(query_id, feedback, note)`.

### 2.2 파일
`llmwiki/mcp.py`(HTTP 핸들러 `handle_http`, 브리지 `bridge_stdio_to_http`, 도구 2개), `llmwiki/web/server.py`(`/mcp` 라우팅, `mcp_only`), `llmwiki/cli.py`(`mcp --transport|--connect|--token|--host|--port`), `llmwiki/auth.py`(API 키), `docs/MCP.md`, `tests/test_mcp_http.py`.

## 3. 채널별 빌드 (요청 3)

### 3.1 설계
- 채널과 산출물: **fts** → `chunks_fts`(+`chunks_tri`) · **vector** → `embeddings`(+`embedding_cache`, `doc_vectors`) · **graph** → `entities/relations/mentions/communities`(+ 위키 페이지). 세 채널의 **입력은 모두 `chunks` + `doc_meta`** 이고 서로의 테이블을 읽지 않는다.
- CLI:
  - `build fts [--full]` — 모든 청크의 FTS 행을 다시 씀(토크나이저·복합어·메타 토큰 변경 후). 임베딩·그래프 불변.
  - `build vector [--full]` — 임베딩 없는 청크만(기본) 또는 전부(`--full`, hash 는 IDF 재적합) 임베딩. FTS·그래프 불변.
  - `build graph` — 그래프 테이블을 비우고 전체 청크에서 재추출 + 커뮤니티 + doc_refs + 위키. FTS·임베딩 불변.
  - `build --channels fts,vector` — 일반(증분/전체) 빌드에서 지정 채널 단계만 실행(나머지는 skipped 로 기록).
- 토글: `build_fts`(신규, 기본 on: chunk_index 단계에서 FTS 행을 쓸지) · `embed`(vector) · `rule_graph`/`llm_graph`(graph). 끄면 해당 단계가 `skipped` 로 남고 `build verify` 가 `fts_missing`/`embedding_coverage`/`entity_*` 로 결손을 보고한다.
- Web: Corpus › 빌드 탭에 채널 체크박스와 "채널 리빌드" 버튼. `POST /api/build {"channel": "fts|vector|graph", "full": bool}`.
- 등급: 채널 리빌드는 `rebuild`(builder, 문구 확인). `--channels` 를 붙인 증분 빌드는 `index`.

### 3.2 "서로 영향을 줘서 문제가 되지 않나" 에 대한 답
- **독립**: 한 채널을 다시 만들어도 다른 두 채널의 행은 읽히지도 지워지지도 않는다(종전 코드는 `delete_doc` 이 임베딩까지 지웠으므로 FTS 만 다시 쓰는 `store.reindex_fts()` 를 새로 두었다).
- **공통 상위 의존**: 세 채널 모두 청크 ID(`doc_id#n`)에 매달려 있다. 문서가 바뀌거나 `chunk_max_chars`/`chunk_overlap_chars` 가 바뀌면 청크가 다시 나뉘고, 이때는 증분/전체 빌드가 세 채널을 함께 처리한다(채널 빌드는 "청크는 그대로, 산출물만 다시" 인 경우에 쓰는 것).
- **정합성 검사**: 각 채널 빌드 끝에 `verify` 가 실행되어 `fts_rows/fts_missing`(fts), `embedding_coverage/embedding_other_provider`(vector), `mention_dangling/relation_dangling/entity_orphans/wiki_stale_pages`(graph)를 보고한다. 문제가 있으면 `build verify --fix` 또는 해당 채널 재빌드.
- **build_version**: 채널 빌드도 버전을 올려 질의 캐시·벡터 행렬·엔티티 인덱스가 무효화된다(다른 프로세스의 서버도 다음 질의에서 재적재).

### 3.3 파일
`llmwiki/pipeline.py`(`build_channel`, `--channels` 처리), `llmwiki/store.py`(`reindex_fts`), `llmwiki/config.py`(`build_fts` 토글), `llmwiki/cli.py`, `llmwiki/web/server.py`, `web/static/js/corpus.js`, `llmwiki/auth.py`(등급), `tests/test_build_channels.py`.

## 4. 문서 단위 확장 doc_expand (요청 4)

### 4.1 설계
- 위치: 리랭크 후 `final`(top_k_final) 이 정해진 다음, 컨텍스트 조립 직전. 새 프로파일 단계 `doc_expand`.
- 절차: `final` 의 문서를 순서대로 `doc_expand_top_docs` 개까지 보고, 각 문서의 나머지 청크(이미 final 에 있는 것 제외)를 점수화:
  - `keyword` = 질의 키워드 커버리지(청크 토큰 ∩ 키워드 / 키워드 수)
  - `vector` = 질의 벡터와 청크 벡터의 코사인(임베딩이 있을 때), 부모 청크 유사도로 정규화(부모 = 1.0)
  - `hybrid`(기본) = `doc_expand_w × vector + (1 − doc_expand_w) × keyword`
- `doc_expand_min_score` 이상인 청크를 점수순으로 `doc_expand_max_chunks` 개까지 고르고, 컨텍스트에는 **부모 청크 바로 뒤에 문서 순서(ordinal)대로** 넣는다(`[C#]` 번호가 붙어 인용 가능, citation kind=`doc_expand`). `context_max_chars` 를 넘으면 잘린다.
- 결과: `hits` 에 why=`["doc_expand"]` 로 표시되고 `in_context`/`n` 을 갖는다. trace `doc_expand` 단계에 문서별 후보 수·채택 수·점수가 남는다.
- 튜닝(stage `context`): `doc_expand_top_docs`(3) · `doc_expand_max_chunks`(3) · `doc_expand_min_score`(0.2) · `doc_expand_mode`(hybrid|keyword|vector) · `doc_expand_w`(0.5). 토글 `doc_expand`(기본 on; speed/token 프리셋은 off).
- 근거 판정(evidence_check)은 확장 후 컨텍스트로 수행하므로, 확장이 키워드 커버리지를 올리면 verdict 가 sufficient 로 바뀔 수 있다(fallback 횟수 감소 효과).

### 4.2 파일
`llmwiki/query_engine.py`(`_doc_expand`, 단계), `llmwiki/answer.py`(`build_context(extra=…)`), `llmwiki/tuning.py`, `llmwiki/config.py`(토글·설명), `llmwiki/architecture.py`·`progress.py`(단계 등록), `presets.json`, `tests/test_doc_expand.py`.

## 5. headless 재시도와 실패 보고 (요청 5)

### 5.1 설계
- `agents.json`(에이전트별): `timeout_s`(300 = 5분), `retries`(3), `retry_backoff_s`(5), `retry_on`(`["timeout", "exec", "exit", "empty"]`). 파일에 없는 키는 기본값으로 채운다.
- `config.json`(공통): `llm_retries`(3), `llm_retry_backoff_s`(2.0), `llm_timeout`(600, headless 는 agents.json 값이 우선). `llm_failure_report`(토글, 기본 on).
- 동작: `BaseLLM.complete()` 가 `_complete()` 를 최대 `1 + retries` 회 호출한다. 재시도 대상은 `LLMError(transient=True)` 로 표시된 오류(subprocess timeout, exec 실패, 종료 코드≠0, 빈 출력, HTTP read timeout, 연결 실패). HTTP 429/5xx 는 프로바이더 내부 backoff 가 이미 처리하므로 바깥에서 다시 돌지 않는다. 각 시도는 진행 패널/CLI 에 `LLM 재시도 2/3 (timeout 300s)` 로 보인다.
- 최종 실패: `LLMError` 를 던지되, 스레드 로컬 **incident 목록**에 `{role, provider, model, attempts, elapsed_ms, errors[], transient}` 를 남긴다. 질의 엔진은 끝에서 이를 모아 `result["llm_report"] = {"failures": [...], "fallbacks": [...]}` 로 넣고, answer 역할이 실패했으면 답변 맨 위에 `> ⚠ LLM 실행 보고: answer(headless:opencode/…) 4회 시도 후 실패(timeout 300s×4) → 추출식 답변으로 대체` 를 붙인다. 빌드는 `result["alerts"]` 와 `llm_report` 에 같은 정보(llm_extract 실패 수 포함).
- 기존 폴백 경로는 그대로다: answer 실패 → 추출식, rerank 실패 → local, expand/verify 실패 → 해당 단계 생략. 즉 "현재까지의 결과를 종합해 보고" 는 항상 답변이 나오되 무엇이 실패해 어떤 대체 경로를 탔는지가 결과에 적히는 형태다.

### 5.2 파일
`llmwiki/providers.py`(`LLMError.transient`, 재시도 루프, incident), `llmwiki/headless.py`(기본값·timeout 처리·kill), `llmwiki/config.py`, `llmwiki/query_engine.py`·`pipeline.py`(llm_report), `web/static/js/ask.js`(보고 표시), `agents.json`, `config.json`, `tests/test_llm_retry.py`.

## 6. 기대 결과 포렌식 (요청 6)

### 6.1 기존 포렌식 검증
- 자동 포렌식(`forensic_auto`)은 verdict≠sufficient 또는 groundedness 낮음일 때 trace 를 규칙으로 진단해 `forensics` 에 남긴다. 테스트 `test_phase3_5.test_evidence_fallback_forensic` 와 실측(§7)으로 동작을 재확인했다. 한계: "왜 부실했나" 는 말하지만 **"내가 기대한 문서가 왜 안 나왔나"** 는 사용자가 기대를 알려주기 전에는 알 수 없다 → 6.2.

### 6.2 기대 결과 포렌식 (`forensic expect`)
- 입력: `request_id`(또는 `last`) + 기대 문서(`--doc ISSUE-2003`, ext_id·doc_id 부분 문자열) 및/또는 기대 용어(`--term "0x40"`) 및/또는 청크 id, 자유 메모.
- 절차 (`forensic.trace_expectation`):
  1. 기대 문서 → 청크 목록. 기대 용어가 있으면 그 용어를 담은 청크를 **목표 청크**로, 없으면 문서의 모든 청크.
  2. 원 요청의 결과(`requests.result`)에서 목표 청크의 실제 상태: `cited`(컨텍스트 포함) / `candidate`(후보였으나 컨텍스트 제외) / `absent`.
  3. 같은 토글·튜닝·프리셋으로 검색을 **다시 실행**(LLM 답변·claim 은 끄고, 로그 없음) 하되 엔진이 라운드 내부(`lists`·융합 후·부스트 후·리랭크 후·final·citations)를 캡처하도록 한다.
  4. 목표 청크마다 단계별 여정을 만든다: fts(순위/누락 이유 = 질의 키워드 중 청크에 없는 것, 청크의 대표 용어) · vector(순위·유사도·상위 유사도) · graph(문서 엔티티가 시드였는지, 확장 도달 여부) · fusion(후보 순위) · boost(배율) · rerank(전/후 순위) · context(포함/제외 이유: top_k_final 밖·dedupe·글자 상한) · answer(인용 여부, 기대 용어가 답변에 있는지).
  5. 첫 탈락 단계를 "원인" 으로 요약하고 수정안을 낸다: `rules add synonym/acronym …`(어휘 불일치), `pin add --doc …`(항상 포함), `tuning set top_k_*/vector_min_sim/doc_expand_*`(후보 부족), 코퍼스 갭(용어가 코퍼스 어디에도 없음), 답변 단계(근거는 있었으나 답변에 반영 안 됨 → `answer_length_target=long`, answer_guide 지시).
  6. `forensics` 테이블에 origin=`expectation` 으로 기록, 에피소드에 부정 피드백 + 메모, `--propose` 면 pin/query_rule 제안을 HITL 큐에 넣는다.
- 인터페이스: CLI `forensic expect <id|last> --doc … --term … [--note …] [--propose] [--json]`, Web Ask 결과의 "🎯 기대 결과 포렌식" 패널과 Quality › 포렌식 탭, API `POST /api/forensic/expect`, MCP `wiki_forensic`. 등급 `read`.

### 6.3 파일
`llmwiki/forensic.py`(`trace_expectation`, `format_expectation`), `llmwiki/query_engine.py`(`capture` 훅), `llmwiki/cli.py`, `llmwiki/web/server.py`, `web/static/js/ask.js`·`quality.js`, `llmwiki/mcp.py`, `docs/FORENSIC.md`, `tests/test_forensic_expect.py`.

## 7. 검증 계획과 결과 (2026-09-14 실측)

| 항목 | 방법 | 결과 |
|---|---|---|
| 회귀 | `python -m unittest discover -s tests` (Python 3.14.7, Windows 11) | **87/87 통과** (기존 63 + 신규 11; `test_auth.py` 는 새 등급 체계로 갱신) |
| 권한 | `tests/test_auth.py`: 7등급 분류표 · 역할 정규화(operator→class1) · 등급 기본값과 `permissions` 오버라이드(run→viewer, `/api/eval`→builder) · 익명 게스트(viewer 질의 OK, 빌드 401) · API 키 발급/검증/삭제 · 헤더 SSO role_map · OIDC · Web 통합(viewer 403, class1 증분 빌드 428→200, 전체 리빌드 403, admin 문구+비밀번호 → 스냅샷, 권한 표 편집 후 viewer 콘솔 200, API 키로 질의 200) · CLI 게이트(cli.default_role=viewer → build 거부 exit 5, `--user` builder 승격, 잘못된 비밀번호 거부, `security perms set`, `apikey add`) | 통과 |
| MCP | `tests/test_features_0914.py::McpHttpTest`: 로그인 필수 모드에서 토큰 없음/잘못된 토큰 401(`WWW-Authenticate`), API 키로 initialize(`Mcp-Session-Id`)·알림 202·tools/list(9개)·wiki_query·wiki_forensic(structuredContent)·배열 요청, 세션 쿠키로도 호출, GET 405/DELETE 200, 감사 로그 거부 기록, 브리지 stdin→HTTP→stdout 왕복과 오류 변환, 익명 viewer 토큰 없이 호출, `mcp_only` 서버의 /api 404 | 통과 |
| 채널 빌드 | `ChannelBuildTest`: full 빌드 후 fts/vector(일부 삭제 후 복구, --full 전부)/graph(비운 뒤 복구) 각각 리빌드 → 다른 채널 행 수 불변·verify OK·build_version 증가·질의 정상; `--channels fts` 로 embed/graph 단계 skipped 후 `build vector` 로 coverage 보충; `build_fts` off 빌드 → verify `fts_missing` → `build fts` 복구; CLI `build fts --yes`, 비대화형 `--yes` 없음 → exit 4 | 통과 |
| doc_expand | `DocExpandTest`: on → `doc_expand` 단계, why=doc_expand 청크가 부모와 같은 문서·컨텍스트 포함·`[C#]`, 문서당 max_chunks·top_docs 상한; off → 단계 skipped; keyword 모드+min_score 1.0 → 추가 0; speed 프리셋 off | 통과 |
| 재시도 | `LlmRetryTest`: mock 에이전트 2회 실패 후 성공 → attempts 3·retry_errors 2·incident 없음; 항상 실패(retries 2) → 3회 시도 후 transient LLMError + incident; `--sleep 5`+timeout 1s → kind=timeout 2회 시도; retry_on 에서 exit 제외 → 재시도 없음; 질의에서 answer 역할 실패 → 추출식 + `llm_report`(attempts 2) + 답변 상단 배너, 토글 off 면 배너 없음; llm_graph 빌드 실패 → alerts `llm_failures`; HTTP read timeout → transient 3회 시도; `make_llm` 가 config `llm_retries`/agents.json 우선순위 반영 | 통과 |
| 기대 결과 포렌식 | `ForensicExpectTest`: 인용된 문서 → 모든 단계 hit·lost_at none·재실행이 requests 에 남지 않음; 무관 문서 → fts miss + pin 제안 + `--propose` 로 proposals → pin 제안 apply → pins.json 등록 → 같은 질의에서 상단; 코퍼스에 없는 용어 → corpus_gap; mock LLM 답변에 용어 없음 → lost_at answer + `answer_length_target` 제안; 캐시 적중 요청 → 원 요청으로 따라감(`followed_from`); CLI `forensic expect`; 제안 kind query_rule/tuning 적용, corpus_gap 은 failed | 통과 |
| 실서버 스모크 | `serve --host 0.0.0.0` 를 띄우고 HTTP 왕복: 게스트 `/api/auth/me`(viewer/anon) · `/api/status` 200 · 질의 200(doc_expand added=6) · `/api/forensic/expect` 200 · 빌드 401(role=class1 안내) · eval 401 · `kh82.kim` 로그인 → `/api/security`(levels·ops_catalog 64) → 채널 리빌드 428(phrase+password) → 승인 → job done(counts 불변) → API 키 발급 → `POST /mcp` initialize(세션 헤더)·wiki_forensic → 게스트 tools/list 9개 → 키 삭제 → 감사 로그 6건 | 통과 |
| CLI 실측 | `build fts|vector|graph --yes`(before/after 동일, verify True, build_version 32→34), `build --channels fts --json`, `query … --trace`(doc_expand docs=3 candidates=7 added=5), `forensic expect last --doc HWD-PHY-TIMING-B1 --term 8ns`(정상 인용됨) / `--doc ISSUE-2003 --term 1.5dB`(retrieval 탈락·제안), `security perms`, `users list`, `--user kh82.kim --password … security show` | 통과 (출력은 BRINGUP_GUIDE·CLI_FLOWS §3.33~3.36·FORENSIC.md §2.3) |

### 7.1 doc_expand on/off 회귀 비교 (`trial`)

`python -m llmwiki trial run --name de-on3 --set doc_expand=true --set llm_answer=false --set rerank_llm=false --set query_expand=false --set query_decompose=false --set evidence_check_llm=false --set fallback_loop=false --set precompute=false --set query_cache=false --k 5` 와 같은 조건의 `de-off3`(doc_expand=false) 비교 — 결과는 아래 §7.2. 평가 지표 계산도 이번에 고쳤다: `evalset.score_result` 는 hit@k/MRR 을 **주 후보(doc_expand/neighbor 보조 청크 제외)** 상위 k 로, term_recall 은 주 후보 상위 k + 그 부모에 붙은 보조 청크(컨텍스트 포함)의 본문으로 계산한다(보조 청크가 부모 뒤에 끼어들어 k 를 잠식해 순위 지표가 왜곡되는 것을 막음). 주의: `precompute`/`query_cache` 가 켜져 있으면 같은 설정의 두 번째 trial 이 캐시를 적중해 지연·fallback 지표가 왜곡되므로 비교 trial 은 둘 다 끈다.

### 7.2 doc_expand 실측 (샘플 모뎀 코퍼스 25문항, LLM 없음, 캐시·fallback 끔)

| 지표 | doc_expand on | off | 해석 |
|---|---|---|---|
| hit@5 / MRR / term_recall / answer_term_recall | 0.92 / 0.756 / 0.96 / 0.92 | 0.92 / 0.756 / 0.96 / 0.92 | 주 후보 순위는 변하지 않는다(설계대로: 확장은 리랭크 뒤에 붙는다) |
| 컨텍스트 청크 수 (평균) | 13.5 | 10.2 | 문서당 최대 3개, 상위 3문서에서 평균 4.4개 추가 |
| 컨텍스트 글자 수 (평균) | 2,085 | 1,609 | +30% (≈ +160 토큰/질의). `context_max_chars` 상한은 그대로 |
| 컨텍스트 기대 용어 포함률 | 1.00 | 1.00 | 이 평가셋의 기대 용어는 이미 주 후보 청크에 있어 차이가 없음. 효과는 답이 같은 문서의 **다른 절**에 있을 때 나타난다(FORENSIC.md §2.4 의 doc_expand 행으로 확인) |
| 평균 지연 | 29.5 ms | 28.7 ms | +0.8 ms (벡터 행렬 캐시 재사용) |

결론: 기본 on 을 유지하되, 토큰 예산이 빠듯한 조직은 `speed`/`token` 프리셋(off) 또는 `doc_expand_max_chunks=1` 로 줄인다. 자기 코퍼스에서는 같은 명령으로 on/off 를 비교해 기록해 두기를 권한다.

## 8. 신규/변경 설정 키 요약

| 파일 | 키 | 기본 | 뜻 |
|---|---|---|---|
| security.json | `anonymous_role` | `viewer` | 미로그인 접속자 역할 (`""` = 로그인 필수) |
| security.json | `permissions.levels`, `permissions.ops` | §1.1 | 등급/작업별 최소 역할 |
| security.json | `cli.default_role`, `cli.require_login` | `admin`, false | CLI 실행자 기본 역할 |
| security.json | `api_keys` | `{}` | MCP/스크립트용 Bearer 키 (해시 저장) |
| config.json | `llm_retries`, `llm_retry_backoff_s` | 3, 2.0 | 모든 LLM 호출의 timeout/네트워크 재시도 |
| config.json toggles | `build_fts`, `doc_expand`, `llm_failure_report` | on, on, on | FTS 색인 쓰기 / 문서 단위 확장 / 실패 보고 |
| tuning.json | `doc_expand_top_docs/max_chunks/min_score/mode/w` | 3/3/0.2/hybrid/0.5 | §4.1 |
| agents.json | `timeout_s`, `retries`, `retry_backoff_s`, `retry_on` | 300, 3, 5, [timeout,exec,exit,empty] | headless 재시도 |
| config.json (09-15) | `web_host`, `web_port` | 127.0.0.1, 8765 | `serve` 기본 바인드/포트 (플래그 우선) |
| config.json (09-15) | `mcp_transport`, `mcp_host`, `mcp_port`, `mcp_url` | stdio, 127.0.0.1, 8766, "" | `mcp` 기본 전송/바인드/포트, 브리지 대상(`LLMWIKI_MCP_URL` 과 동일) |
| tuning.json (09-15) | `forensic_near_miss_mult`, `forensic_term_candidates`, `forensic_term_targets`, `forensic_pin_confidence` | 3, 6, 20, 0.6 | 기대 결과 포렌식의 제안 임계·후보 수 (이전엔 코드 상수) |
| setup/ (09-15) | `security.example.json`, `agents.example.json`, `mcp_clients.example.json` | – | 새 환경용 원본; `install.*` 가 security/agents 를 생성, `mcp --client-config` 가 클라이언트 설정을 환경 값으로 출력 |

검증 결과(CLI·Web·UI·브라우저)와 수정 결함은 [VERIFICATION_0915.md](VERIFICATION_0915.md), 설정 위치 총람은 [BRINGUP_GUIDE.md §3.2](BRINGUP_GUIDE.md).

## 9. (2026-09-15 추가) MCP 확장성 — 다른 RAG 연동 · 외부 LLM 에 한 곳으로

### 9.1 요청과 판정
사용자 요청: "지금 만든 MCP 가 다른 RAG 를 붙일 수 있는 확장성을 갖는가? 추후 다른 RAG 도 붙이고, 외부 LLM 클라이언트도 쓰게 하고 싶다." **판정(구현 전)**: 부분적. 인바운드(외부 LLM → 우리 MCP)는 도구 9개가 코드에 고정된 if/elif 라 플러그인 구조가 아니었고, 아웃바운드(우리 → 외부)는 `mcp_sources.json` 으로 stdio MCP 서버만 붙일 수 있었으며 결과는 ingest(문서화) 또는 fallback 단계의 enrich(컨텍스트 꼬리 텍스트)에 그쳤다. HTTP MCP·REST RAG 는 붙일 수 없었고 외부 결과는 검색 채널로 융합되지 않았다.

### 9.2 채택안 (상세 [RAG_FEDERATION.md](RAG_FEDERATION.md))
| 축 | 만든 것 | 대안과 기각 이유 |
|---|---|---|
| 아웃바운드 전송 | `mcp_client.py` 에 `HttpMCPClient`(MCP Streamable HTTP) · `RestClient`(일반 JSON API) 추가, `open_source(transport)` 팩토리, 질의 경로용 클라이언트 풀 | 프레임워크(LangChain 등) 도입 → 이식성 원칙 위배. 소켓/gRPC → 필요 시 클래스 하나 추가로 확장 가능하게만 |
| 외부 RAG 를 검색에 반영 | `retrieve` 매핑 → 가상 청크 `ext:<source>:<id>` → 채널 `ext_<source>` 로 RRF 융합, `external_rag_inject` 로 리랭크 후보 보장, 로컬 리랭크 consensus 보정, 인용·`hits.external`·trace | (a) 컨텍스트 꼬리 첨부(종전 enrich) → 인용·claim 검증 불가; (b) 질의 시 자동 색인 → DB 변경이라 viewer 권한·캐시와 충돌 |
| 인바운드 확장 | 플러그인 레지스트리(`register_tool`, `plugins/mcp_tools/*.py`, mtime 재적재) + 페더레이션(`expose` → `<source>__<tool>` 중계, 300초 스키마 캐시) + 도구 `wiki_sources`/`wiki_external_search` | RAG 마다 클라이언트에 MCP 를 따로 등록 → 설정·권한·감사 분산 |
| 안전장치 | 재귀 방지(`X-LLMWiki-Federation-Depth` 헤더 / `LLMWIKI_FEDERATION_DEPTH` 환경변수: 하위 호출은 자기 페더레이션을 하지 않음), 소스 오류 격리(채널만 비고 질의 계속, 풀에서 제거), 잘못된 토큰 401 격리 | — |
| 설정 | 토글 `external_rag`·`mcp_federation`(기본 off), `mcp_plugins_dir`, 튜닝 `channel_w_external`·`external_rag_k`·`external_rag_inject`, 소스별 `weight/when/timeout_s/expose`, 원본 `setup/mcp_sources.example.json`, 목업 `--mock-rest` | — |

## 10. (2026-09-15 추가) 상세 분석 모드 — 품질·속도·토큰 디버깅 리포트

### 10.1 요청과 판정
사용자 제안: "검색 품질·속도·토큰량이 마음에 들지 않을 때, 질의 전에 상세 분석 모드를 켜면 모든 단계의 결과를 상세히 남기고, 그 결과가 잘 정리된 문서로 나와 LLM 에게 줄 수 있으면 좋겠다." **판정: 채택.** 프로파일러(단계별 meta/debug/samples/counters)·requests 테이블·포렌식 진단·llm_report·architecture 레지스트리(단계↔튜닝 키)가 이미 데이터를 갖고 있어, 부족한 것은 (1) 한 질의에 대해 이것들을 **한 문서로 묶는 것**, (2) 사람이 아닌 LLM 이 바로 쓸 수 있도록 **조절점(설정 키·현재값)과 검증 절차**를 함께 적는 것, (3) 세 관점(품질/속도/토큰)의 **규칙 기반 소견**이었다.

### 10.2 채택안 (상세 [ANALYSIS_MODE.md](ANALYSIS_MODE.md))
| 축 | 만든 것 | 대안과 기각 이유 |
|---|---|---|
| 켜는 방법 | 토글 `analysis_mode`(사이드바/`config set`/`--analyze`/`overrides`) → 그 질의만 debug_level 2 | 항상 debug 2 → requests 행 비대·지연. `--trace` 만으로 → 사람이 읽기엔 되지만 설정·조절점·렌즈가 없다 |
| 산출물 | `logs/analysis/req_<id>.md` + `.json`, `result.analysis`(경로·상위 소견) | DB 테이블 → 파일이 첨부·공유·diff 에 편하고 LLM 에 그대로 넘긴다 |
| 리포트 구조 | §0 요약 → §1 설정 스냅샷(기본값과 다른 튜닝 강조) → §2 타임라인/LLM 호출 → §3 검색 상세(채널·융합·부스트·리랭크 전후·doc_expand·컨텍스트·fallback·최종 표) → §4 답변/판정/claim → §5~7 렌즈(소견+조절점=현재값+근거) → §8 포렌식 → §9 LLM 지시문 → 부록(샘플·원 데이터) | — |
| 렌즈 | 규칙 기반(trace 수치)로 재현 가능하고 LLM 없이 0 토큰. LLM 판단은 사용자가 리포트를 넘겨서 받는다 | LLM 이 소견을 쓰게 → 토큰·재현성 문제; 우선 규칙으로 두고 필요하면 `llm_roles.forensic` 으로 확장 가능 |
| 과거 요청 | `analyze <id>` 로 요약 수준 리포트(샘플 없음, 안내 표기) | — |
| 인터페이스 | CLI `analyze`/`query --analyze`, Web Ask 📊(초점·md·다운로드·복사)+배너, MCP `wiki_analysis`, `GET /api/analysis` | — |
| 권한 | read (게스트 가능; 색인·설정 불변) | — |

### 10.3 검증
`tests/test_analysis.py` 6개(전체 87개 통과), 하네스 CLI(`query --analyze`, `analyze last|--print|--json|없는 id`)·Web(`overrides.analysis_mode` → `/api/analysis` json/md/download/404, MCP `wiki_analysis`) — [VERIFICATION_0915.md](VERIFICATION_0915.md) §8.

### 9.3 검증
`tests/test_rag_federation.py` 7개(전체 87개 통과), 하네스 `verify_cli.py`(mcp-source tools/retrieve/federated, query ±external-rag, stdio 페더레이션)·`verify_web.py`(`/api/mcp_sources` retrieve/tools/federated, external_rag 오버라이드, `/mcp` 의 `mock__search`·`wiki_external_search`·깊이 헤더 가드) — [VERIFICATION_0915.md](VERIFICATION_0915.md) §7. 발견·수정: 자기 자신/상호 expose 시 tools/list 무한 재귀(→ 깊이 헤더), 단일 리스트인 외부 채널이 RRF 에서 항상 밀리는 문제(→ inject + consensus 보정).
