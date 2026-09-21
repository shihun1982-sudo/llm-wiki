# CONFIG REFERENCE — 사용자가 고치는 모든 파일과 키 (자동 생성)

> **이 문서는 `python -m llmwiki config doc` 이 코드의 레지스트리에서 생성한다.** 손으로 고치지 말고 코드를 고친 뒤 다시 생성한다.
> 설정을 *어떻게* 바꾸는지(절차·권한·검증)는 [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) 와 [SETTINGS_SYNC.md](SETTINGS_SYNC.md),
> 단계별로 어떤 값이 어디에 작용하는지는 [OPTIMIZATION_GUIDE.md](OPTIMIZATION_GUIDE.md) 와 [PIPELINE_PAGE.md](PIPELINE_PAGE.md) 에 있다.

## 0. 원칙 네 가지

1. **모든 설정은 파일에 있다.** 코드를 고쳐야 바뀌는 값은 없다 — 새 환경으로 폴더를 복사하고 파일만 채우면 동작한다.
2. **기본값도 파일에 적는다.** `python -m llmwiki config doc` 로 이 표를 보고, `config fill-defaults --all` 로 빠진 키를 기본값째 채운다. 나중에 값을 바꿀 때 *줄을 고치기만* 하면 된다.
3. **우선순위는 항상 같다**: CLI 플래그 > 환경변수(`LLMWIKI_<KEY>`) > 파일 > 코드 기본값. 지금 유효한 값과 출처는 `config show --effective`.
4. **요청 단위 변경은 파일을 바꾸지 않는다.** 사이드바 토글·🧭 Pipeline 의 '이번 요청에만'·`overrides` 는 그 요청에만 적용된다.

## 1. 파일 지도

| 파일 | 무엇을 정하나 | 어디서 바꾸나 | 언제 반영되나 | 원본 예시 |
|---|---|---|---|---|
| **config.json** | 코퍼스 위치 · 역할별 LLM · 임베딩 · 토글 66개 · 운영 수치(로그·DB·서버 바인드 등) — **가장 많이 고치는 파일** | Web Settings › config.json / 모델·프로바이더 · CLI `config show|set`, `models set` · 파일 직접 | 즉시 (서버는 `config reload` 또는 Settings 의 `↻ config.json 다시 읽기`). `web_*`·`mcp_*` 는 다음 기동부터 | setup/config.example.json · setup/config.example.pat-gateway.json · setup/config.example.headless.json |
| **.env** | API 키·PAT 과 모든 설정의 환경변수 오버라이드(`LLMWIKI_<KEY>`, `LLMWIKI_TOGGLE_<NAME>`) | 파일 직접 (값은 화면에 **마스킹**되어 보인다: Settings › config.json 탭 하단 `.env` 패널 · CLI `config env`) | `config reload --env` 또는 Web 의 `.env 다시 읽기`. 프로세스 환경변수가 파일보다 우선 | setup/.env.example |
| **tuning.json** | 알고리즘 상수 141개 (청킹·임베딩·그래프·라우터·검색·융합·리랭크·컨텍스트·근거·claim·포렌식·메모리) | Web 🧭 Pipeline(단계별) · Settings › 튜닝 · CLI `tuning show|set|reset` · 파일 직접 | 즉시 (`tuning reload` 또는 Web). `rebuild` 표시가 붙은 키는 **전체 리빌드** 필요 | setup/tuning.example.json (`config fill-defaults --tuning --examples` 로 생성) |
| **query_rules.json** | 질의 확장 사전 — acronym · synonym(양방향) / alias · related · exclude(일방) / compound | Web Settings › 질의 규칙 사전 · CLI `rules show|add|remove|test|explain|lint` · 파일 직접 | 즉시 (파일 mtime 을 보고 다시 읽는다) | setup/query_rules.example.modem.json |
| **data/rules.json** | 지식 그래프 규칙 — 엔티티 사전 · 관계 정규식 · ID 패턴(`id_patterns`) · 결정적 링크(`link_rules`) | Web Knowledge › 그래프 규칙 · CLI `rules`(그래프) · 파일 직접 | **그래프 재빌드** (`build graph`) 뒤 반영. 진단은 `graph profile --compare` | setup/rules.example.modem.json |
| **stopwords.json** | 질의 키워드 추출에서 제거할 불용어 | 파일 직접 | 즉시 (mtime). 없으면 코드 기본값으로 생성 | setup/stopwords.example.json |
| **presets.json** | 토글+튜닝+config 묶음 (quality · speed · token · offline · deep_research) | Web Settings › 프리셋 · 사이드바 체크(요청 단위) · CLI `preset list|show|apply|diff` | 즉시. 사이드바 체크는 **그 요청에만**, `preset apply --save` 는 파일에 기록 | (저장소 기본값) |
| **models.json** | 쓸 수 있는 모델 카탈로그 (LLM · 임베딩 · 리랭크). 화면 드롭다운의 원천이며 provider 자동 해석에도 쓰인다 | Web Settings › 카탈로그(추가·삭제·전체 연결 테스트) · CLI `models list|catalog add|remove|discover` | 즉시 (mtime) | setup/models.example.json |
| **agents.json** | headless 에이전트 실행 명령과 재시도 정책 (opencode · claude · codex · mock) | Web Settings › 모델 하단 `agents.json` · 파일 직접 | 즉시 | setup/agents.example.json |
| **security.json** | 로그인(로컬·SSO·API 키) · 역할 6단계 · 권한 표 · 익명 접속 · CLI 게이트 · 파괴적 작업 정책 · 요청 overrides 허용 목록 | Web Settings › 보안·사용자 · CLI `users`, `security`, `apikey` | 즉시 (`security reload` 또는 화면의 '다시 읽기') | setup/security.example.json |
| **server.json** | 동시성·대기열·시간 제한·속도 제한·세션·차단·점검 모드·모니터 공개 범위, `mcp` 절(페더레이션 캐시·타임아웃·상한), `debug.expose_trace` | Web 관리 › 서버 모니터 · CLI `server limits|block|maintenance` | `server` 명령·화면은 즉시. 파일을 직접 고쳤으면 **reload** 필요 | setup/server.example.json |
| **schedule.json** | 정해진 시각·주기에 돌릴 작업 (동작 19종) | Web Settings › 스케줄 · CLI `schedule add|enable|run|validate` | 즉시 (서버가 mtime 을 보고 다시 읽는다) | setup/schedule.example.json |
| **mcp_sources.json** | 다른 RAG·MCP 서버·REST 검색 API 연결 (retrieve 채널 · expose 도구 중계 · ingest) | Web Corpus › MCP 소스 · CLI `mcp-source list|test|retrieve|federated` | 즉시 | setup/mcp_sources.example.json |
| **pins.json** | 고정 근거 — 특정 질의·문서를 항상 후보에 넣는다 | Web Settings › Pin · CLI `pin add|list|test|remove` | 즉시 | (저장소 기본값) |
| **prompts/*.md** | 역할별 LLM 프롬프트 (답변·리랭크·추출·검증·융합 검토·리랭크 선택·앙상블 취합·best_effort 등) | Web Settings › 프롬프트 · CLI `prompts list|show|reset` · 파일 직접 | 즉시 (mtime). 없으면 코드 기본값으로 생성 | (첫 실행 시 생성) |
| **schemas/*.json** | 문서 유형별 front matter 스키마 · 추론 규칙 · 마이그레이션 | 파일 직접 | lint·빌드 때 | (저장소 기본값) |
| **eval/questions.json** | 회귀 평가셋 — 자기 코퍼스 질문으로 바꾼다 | Web Quality › 평가 · 파일 직접 | 즉시 | (저장소 기본값) |
| **data/profiles.json** | 계정별 Web UI 설정 (테마·토글·사이드바 배치·고정 탭 등). 서버 공용 설정과 분리 | Web 헤더 `💾 내 설정 저장` | 즉시 (로그인 시 자동 적용) | (자동 생성) |

모든 파일은 위치를 옮길 수 있다 — `LLMWIKI_<이름>_PATH` 환경변수. 지금 쓰는 경로는 `python -m llmwiki config paths`:

| 이름 | 기본 위치 | 환경변수 |
|---|---|---|
| `agents` | `agents.json` | `LLMWIKI_AGENTS_PATH` |
| `config` | `config.json` | `LLMWIKI_CONFIG_PATH` |
| `env` | `.env` | `LLMWIKI_ENV_PATH` |
| `eval` | `eval/questions.json` | `LLMWIKI_EVAL_PATH` |
| `logs_dir` | `logs` | `LLMWIKI_LOGS_DIR_PATH` |
| `mcp_sources` | `mcp_sources.json` | `LLMWIKI_MCP_SOURCES_PATH` |
| `models` | `models.json` | `LLMWIKI_MODELS_PATH` |
| `pins` | `pins.json` | `LLMWIKI_PINS_PATH` |
| `presets` | `presets.json` | `LLMWIKI_PRESETS_PATH` |
| `prompts_dir` | `prompts` | `LLMWIKI_PROMPTS_DIR_PATH` |
| `query_rules` | `query_rules.json` | `LLMWIKI_QUERY_RULES_PATH` |
| `rules` | `data/rules.json` | `LLMWIKI_RULES_PATH` |
| `schedule` | `schedule.json` | `LLMWIKI_SCHEDULE_PATH` |
| `schemas_dir` | `schemas` | `LLMWIKI_SCHEMAS_DIR_PATH` |
| `security` | `security.json` | `LLMWIKI_SECURITY_PATH` |
| `server` | `server.json` | `LLMWIKI_SERVER_PATH` |
| `stopwords` | `stopwords.json` | `LLMWIKI_STOPWORDS_PATH` |
| `themes` | `llmwiki/web/static/themes/themes.json` | `LLMWIKI_THEMES_PATH` |
| `tuning` | `tuning.json` | `LLMWIKI_TUNING_PATH` |

> 저장은 모두 **원자적**이다(`llmwiki/atomicio.py`) — 여러 관리자가 같은 순간에 저장해도 파일이 반쪽으로 남지 않는다.

## 2. `config.json` — 설정 키 106개

비워 두면 코드 기본값을 쓴다. 역할별 LLM(`llm_roles.<role>`)은 §3, 토글은 §4 에 따로 있다.

| 키 | 기본값 | 설명 |
|---|---|---|
| `analysis_keep` | `200` | logs/analysis 의 리포트(req_<id>.md + .json = 1건) 보관 개수. 새 리포트를 저장할 때 오래된 것부터 지운다. 0 = 무제한. |
| `answer_effort` | `"medium"` | answer 역할의 추론 강도 기본값 (llm_effort 대신 쓰인다). |
| `answer_max_tokens` | `3000` | 답변 LLM 출력 토큰 상한. |
| `answer_mode` | `"grounded"` | 답변 모드. grounded(기본) = 문서 근거만으로 답하고 근거 판정이 insufficient 면 LLM 을 부르지 않는다. best_effort = 근거가 부족해도 LLM(prompts/answer_best_effort.md)을 불러 문서 사실 [C#] + 배경 지식 [BK] 로 답한다. 결과의 result_type 으로 어느 쪽이었는지 알 수 있다. 요청 단위: CLI --answer-mode · MCP wiki_query(answer_mode=) · Web overrides.answer_mode. |
| `anthropic_base_url` | `""` | Anthropic 호환 게이트웨이 URL (비우면 api.anthropic.com). 키: ANTHROPIC_API_KEY(x-api-key) 또는 ANTHROPIC_AUTH_TOKEN(Bearer PAT). |
| `audit_backups` | `5` | audit.jsonl 로테이션 보관 개수. 0 = 초과 시 비운다. |
| `audit_max_mb` | `20` | logs/audit.jsonl 파일당 최대 크기(MB). 초과 시 audit.jsonl.1, .2 … 로 로테이션. |
| `auto_build_interval` | `300` | auto_build 스캔 주기(초). |
| `build_lock_stale_s` | `172800` | 빌드 락의 주인이 살아 있어도 이 시간(초)이 지나면 죽은 락으로 보고 회수한다. 빌드 1회의 최대 수명이므로 가장 오래 걸리는 빌드보다 길게 (기본 172800=48시간). |
| `build_lock_timeout` | `172800` | 다른 프로세스가 빌드 중일 때 락을 기다릴 초 (0=즉시 실패). 기본 172800(48시간) — 앞 빌드가 끝나면 이어서 시작한다. |
| `chunk_max_chars` | `900` | 청크 하나의 최대 글자 수. 크면 문맥이 풍부하지만 검색 정밀도와 토큰이 나빠진다. 바꾸면 `build --full`. |
| `chunk_overlap_chars` | `120` | 이웃 청크가 겹치는 글자 수. 경계에서 잘린 문장을 살린다. 바꾸면 `build --full`. |
| `console_encoding` | `"auto"` | 터미널 출력 인코딩: auto(기본) \| utf-8 \| native \| off. 한글·기호(⏳ ✔ ·)가 깨지거나 UnicodeEncodeError 로 죽는 환경에서 조정. native = 코드페이지를 바꾸지 않고 표현 불가 문자만 ? 로. |
| `console_set_codepage` | `true` | Windows 콘솔 출력 코드페이지를 UTF-8(65001)로 바꿀지 (chcp 65001 과 동일, 종료 시 복구). 콘솔을 바꾸면 안 되는 환경이면 false + console_encoding=native. |
| `context_chunk_chars` | `1200` | context_trim 시 청크당 글자 상한. |
| `context_max_chars` | `9000` | 답변 컨텍스트 총 글자 상한 (≈ 토큰 ÷ 3). |
| `corpus_dirs` | `["C:\\Users\\user\\project\\llm-wiki-rag-selfevolving\\co…` | 색인할 코퍼스 폴더 목록. 재귀 스캔하며 .md .txt .csv .html .pdf 를 읽는다. 바꾸면 `build --full`. |
| `corpus_exclude` | `[]` | 코퍼스 안에 있어도 색인하지 않을 경로 패턴 목록 (doc_id = 코퍼스 루트 기준 상대 경로). 예: ["imported/llmwiki/", "**/NOTE-*.md"]. 폴더는 `경로/` 또는 `경로/**`, 파일은 fnmatch 패턴. 색인하면 안 되는 것이 섞이면 도메인 문서를 밀어낸다 — 실측: 도구 자신의 소스 150개를 제외하니 hit@k 0.64→0.88. 바꾸면 다음 빌드에서 그 문서들이 색인에서 빠진다. |
| `data_dir` | `"C:\Users\user\project\llm-wiki-rag-selfevolving\data"` | DB·캐시·스냅샷·진행 파일이 사는 폴더 (기본 data). 폴더째 옮기면 색인도 함께 간다. |
| `db_busy_timeout_s` | `60.0` | SQLite 쓰기 잠금 대기(초). CLI 빌드가 쓰는 동안 서버 질의의 로그 기록이 기다리는 시간 (WAL 이라 읽기는 기다리지 않음). |
| `db_name` | `"llmwiki.sqlite3"` | data_dir 안의 SQLite 파일 이름 (기본 llmwiki.sqlite3). |
| `db_pool_size` | `16` | 서버 스레드별 SQLite 연결 풀 크기 (server.json concurrency.max_parallel_reads 이상 권장). |
| `debug_level` | `1` | 프로파일 상세도: 0 요약 · 1 디버그 메타/로그 · 2 프롬프트/응답 원문 샘플. |
| `embed_batch` | `64` | 임베딩 배치 크기 (API 임베더는 64 이하 권장). |
| `embed_batch_max` | `256` | 적응형 임베딩 배치 상한 (성공이 이어지면 embed_batch 에서 이 값까지 증가). |
| `embed_batch_target_ms` | `8000` | 배치 1회 목표 지연(ms). 초과하면 배치 축소. |
| `embed_commit_every` | `10` | N 배치마다 commit·진행률 저장 (중단 후 재개 단위). |
| `embed_dim` | `4096` | hash 임베딩 차원 (메모리 = 청크수×dim×4B; float16 저장 시 절반). 모든 차원 지원, 변경 시 build --full. |
| `embed_model` | `""` | 임베딩 모델 이름. 비우면 프로바이더 기본값. 바꾸면 차원이 달라질 수 있어 `build vector --full`. |
| `embed_provider` | `"auto"` | 임베딩 프로바이더 (auto \| hash \| ollama \| voyage \| openai \| st). auto 는 키·서버를 탐지한다. 바꾸면 `build vector --full`. |
| `embed_store_dtype` | `"float32"` | 벡터 저장/행렬 dtype: float32 \| float16 (메모리 절반). |
| `evolve_auto_apply_kinds` | `[]` | 자동 적용을 허용할 제안 종류 목록. 비우면 되돌리기 쉬운 것만(synonym·query_rule·pin·wiki_note). chunk_params(전체 리빌드)·alias/entity(그래프 재빌드)는 일부러 빼 두었다 — 넣으려면 여기에 적는다. |
| `evolve_low_score_threshold` | `0.05` | 이 점수 아래로 떨어진 질의를 '개선 거리' 로 보고 제안을 만든다. |
| `evolve_min_confidence` | `0.8` | 자가진화 제안을 자동 적용 후보로 볼 최소 신뢰도 (evolve_auto_apply 가 켜져 있을 때). |
| `evolve_snapshot_keep` | `20` | 적용(apply)마다 만드는 자동 스냅샷을 몇 개까지 남길지. 0 = 무제한(예전 동작). 자동 적용을 켜 두면 스냅샷이 계속 쌓여 data 폴더가 커진다. |
| `graph_hops` | `2` | 그래프 검색에서 시드 엔티티로부터 확장할 홉 수. 늘리면 recall↑·노이즈↑. |
| `graph_profile_hubs` | `10` | 그래프 진단의 허브(차수 상위) 표 행 수. 날짜/금액/문서 유형 허브는 경고로 표시된다. |
| `graph_profile_keep` | `30` | 그래프 진단 프로파일(data/graph_profiles/gp_<ts>.json) 보관 개수. `graph profile --compare` 가 직전 파일과 비교한다. 0 = 무제한. |
| `graph_profile_requests` | `200` | 그래프 진단의 '질의 활용' 절이 보는 최근 질의 요청 수 (그래프 시드 비율·graph# 근거 비율·시드 없는 질의 키워드). |
| `keep_requests` | `2000` | requests 테이블 보존 개수. |
| `llm_budget_s` | `0` | 한 LLM 호출의 재시도 포함 총 시간 예산(초). 넘으면 재시도를 멈추고 대체 경로(추출식 답변 등)로. 0=무제한. 역할별: llm_roles.<role>.budget_s |
| `llm_circuit_cooldown_s` | `60` | 회로 차단 후 재시도까지 대기(초). 역할별: llm_roles.<role>.circuit_cooldown_s |
| `llm_circuit_failures` | `3` | 같은 provider/model 이 연속 n회 최종 실패하면 회로 차단(circuit open): cooldown 동안 호출을 즉시 실패시켜 30명이 각각 timeout 을 기다리지 않게. 0=끔. 역할별: llm_roles.<role>.circuit_failures |
| `llm_effort` | `"low"` | 기본 추론 강도 (low \| medium \| high). 지원하는 모델에서만 의미가 있다. |
| `llm_ensemble_defaults` | `{"wait": "all", "timeout_s": 120, "min_results": 1, "prom…` | 역할별 앙상블(llm_roles.<role>.ensemble)에서 생략한 키의 기본값 JSON: wait(all\|timeout) · timeout_s · min_results · prompt(prompts/<name>.md). 앙상블 자체는 역할마다 enabled=true 로 켠다 — docs/ENSEMBLE.md, CLI `models ensemble show\|set`. |
| `llm_fallbacks` | `true` | 기본 모델이 실패할 때 차례로 시도할 대체 모델 목록. |
| `llm_frequency_penalty` | `0.3` | 같은 토큰을 다시 쓸 때의 벌점(OpenAI 호환). 작은 모델이 한 구절을 수십 번 되풀이하는 고장을 줄인다. 0=끔, 0.2~0.6 권장. |
| `llm_graph_budget` | `0` | 빌드당 LLM 추출 호출 상한 (0=무제한). 신규 문서 20개/일 × 10청크 ≈ 200회. |
| `llm_graph_min_chars` | `80` | 이보다 짧은 청크는 LLM 추출 생략. |
| `llm_http_retries` | `2` | HTTP 429/5xx 에 대한 프로바이더 내부 짧은 재시도 횟수 (llm_retries 와 곱해짐). |
| `llm_model` | `"claude-opus-5"` | 기본 LLM 모델 이름. 역할별로 llm_roles.<role>.model 이 우선한다. |
| `llm_presence_penalty` | `0.0` | 이미 나온 토큰에 주는 벌점(OpenAI 호환). 보통 0 으로 둔다. |
| `llm_provider` | `"auto"` | 기본 LLM 프로바이더 (auto \| openai \| anthropic \| ollama \| headless:<agent> \| mock). 역할별로 llm_roles 에서 덮어쓴다. |
| `llm_repeat_penalty` | `1.1` | Ollama 네이티브(/api/generate) 의 repeat_penalty. 1.0=억제 없음, 1.1 권장. |
| `llm_retries` | `3` | LLM 호출이 timeout/네트워크/headless 실행 실패(transient)면 재시도할 횟수 (최대 1+n 회). HTTP 4xx(인증·모델명) 는 재시도하지 않음. headless 는 agents.json retries 우선. |
| `llm_retry_backoff` | `"exponential"` | 재시도 대기 증가 방식 linear \| exponential. 역할별: llm_roles.<role>.backoff |
| `llm_retry_backoff_max_s` | `60.0` | 재시도 대기 상한(초). 역할별: llm_roles.<role>.backoff_max_s |
| `llm_retry_backoff_s` | `2.0` | 재시도 사이 기본 대기 초. linear 면 ×시도번호, exponential 이면 ×2^(시도-1) (+지터). 역할별: llm_roles.<role>.backoff_s |
| `llm_roles` | `{}` | 역할별 LLM 설정 묶음 (answer·rerank·extract·summary·review·expand·verify·forensic·fusion·select). 역할마다 provider/model/effort 와 timeout_s·retries·budget_s·max_tokens·ensemble 을 둔다. |
| `llm_timeout` | `600` | LLM 호출 1회의 HTTP 타임아웃(초). 로컬 모델(Ollama)이 느리면 늘리고, 멈춘 서버를 빨리 감지하려면 줄인다 (예: 120). headless 는 agents.json timeout_s(기본 300) 가 우선. |
| `log_backups` | `10` | 로테이션 보관 파일 수. |
| `log_check_interval_s` | `60` | 총량을 다시 재는 최소 간격(초). 로그가 남을 때마다 이 간격이 지났는지만 확인하므로 부하가 거의 없다. 0 = 매 레코드(테스트용). |
| `log_console` | `false` | 콘솔(stderr)에도 WARNING 이상 출력. |
| `log_level` | `"INFO"` | logs/ 파일 로그 레벨 (DEBUG 면 단계별 상세까지). |
| `log_limit_action` | `"warn"` | 총량 초과 시 동작: warn(error.log 에 경고 + health/Web 배너) \| prune(오래된 *.log.N → analysis 리포트 → audit 백업 순으로 지워 80% 아래로) \| stop(error.log 만 남기고 다른 로그 기록을 멈춤, 80% 아래로 내려가면 재개). |
| `log_max_mb` | `10` | 로그 파일당 최대 크기(MB). 초과 시 로테이션. |
| `log_total_max_mb` | `500` | logs/ 폴더 총량 상한(MB). llmwiki/error/build/query 로그 + 백업 + audit.jsonl + analysis/ 합계. 0 = 제한 없음. 확인: `logs status`, health log_quota. |
| `mcp_host` | `"127.0.0.1"` | mcp --transport http 기본 바인드 주소. |
| `mcp_plugins_dir` | `"plugins/mcp_tools"` | MCP 플러그인 도구 폴더. 이 폴더의 *.py(밑줄로 시작하지 않는 파일) 가 register(add_tool) 로 도구를 등록하면 tools/list 에 나타난다. 예시: plugins/mcp_tools/_example_echo.py (밑줄을 지우면 활성). |
| `mcp_port` | `8766` | mcp --transport http 기본 포트 (serve 와 다른 포트로 MCP 만 열 때). |
| `mcp_transport` | `"stdio"` | mcp 명령 기본 전송: stdio \| http (--transport 가 우선). |
| `mcp_url` | `""` | 비어 있지 않으면 `mcp` 명령이 stdio→이 URL 로 중계하는 브리지로 동작 (예 http://wiki-host:8765/mcp). 우선순위: --connect > LLMWIKI_MCP_URL > 이 값. 토큰은 --token > LLMWIKI_MCP_TOKEN. |
| `ollama_model` | `"llama3.1"` | Ollama 를 쓸 때의 기본 모델 이름 (`ollama list` 의 이름과 정확히 같아야 한다). |
| `ollama_url` | `"http://localhost:11434"` | Ollama 서버 주소 (기본 http://localhost:11434). OpenAI 호환 경로 /v1 는 자동으로 붙는다. |
| `openai_api_key_header` | `"authorization"` | PAT/키를 싣는 헤더: authorization(Bearer <key>) \| api-key \| x-api-key \| 임의 헤더명(값은 키 그대로). |
| `openai_base_url` | `"http://localhost:11434/v1"` | OpenAI-compatible 서버 base URL (…/v1). vLLM·LM Studio·Ollama·OpenRouter·사내 게이트웨이(PAT). 키는 .env OPENAI_API_KEY 또는 LLM_API_KEY. |
| `openai_embed_base_url` | `""` | 임베딩 전용 base URL (비우면 openai_base_url). 키는 OPENAI_EMBED_API_KEY 가 있으면 그것, 없으면 LLM 키. |
| `openai_embed_model` | `""` | embed_provider=openai 일 때 임베딩 모델명. |
| `openai_extra_headers` | `{}` | 게이트웨이가 요구하는 고정 헤더 JSON (예 {"X-Tenant": "modem"}). |
| `output_mode` | `"answer"` | 출력 모드 — 어디까지 만들고 무엇을 돌려줄지 (answer_mode 와 독립). answer(기본) = 끝까지. fused = 융합·부스트 뒤(리랭크 전) 후보 candidates[]·채널별 lists·stages 순서를 그대로 (result_type=candidates_fused). reranked = 리랭크 뒤 후보 + 리랭크 입력 전후 순서 (candidates_reranked). context = 컨텍스트([C#] 블록)·refs·근거 판정까지만, 답변 LLM 은 부르지 않는다 (context). answer 가 아니면 답변 LLM·claim 검증·자가진화 기록·캐시 저장을 하지 않는다. 튜닝 output_candidates_n / output_list_n / output_chunk_chars. 요청 단위: CLI --output · MCP wiki_query(output_mode=) · Web Ask 출력 드롭다운(overrides.output_mode). |
| `query_cache_size` | `200` | 질의 캐시 항목 수. |
| `requests_dir` | `"data/requests"` | 요청 결과/trace 를 따로 보관할 폴더 (월별 하위 폴더 + req_<id>.json). data_dir 기준 상대 경로 가능. 비우면 파일로 남기지 않는다 — 그러면 keep_requests 로 DB 행이 잘릴 때 '그때 그 답' 을 다시 볼 수 없다. |
| `requests_keep_days` | `90` | 요청 보관 파일의 보존 기간(일). 0 = 지우지 않음. 정리: `python -m llmwiki maintenance prune_requests` 또는 스케줄 작업. |
| `rerank_api_model` | `""` | rerank API 모델명 (rerank_url 용). rerank_model 은 역할 rerank 의 LLM 모델 단축키이므로 다른 값. |
| `rerank_api_style` | `"cohere"` | rerank 응답 포맷: cohere(=jina/vLLM results[].relevance_score) \| voyage(data[].relevance_score). |
| `rerank_candidates` | `16` | 리랭크 후보 수 (LLM 리랭크 프롬프트 크기 ∝ 후보 수 × rerank_chunk_chars). |
| `rerank_chunk_chars` | `600` | 리랭크 프롬프트에 넣는 청크당 글자 수. |
| `rerank_url` | `""` | rerank_method=api 엔드포인트 URL (Cohere/Jina/vLLM /v1/rerank, Voyage /v1/rerank). |
| `rerun_dir` | `"data/reruns"` | 단계 재실행용 중간 결과 폴더 (req_<request_id>.json). data_dir 기준 상대 경로 가능. 화면의 단계별 ⟲ 버튼이 이 파일을 읽는다 — docs/RERUN.md. |
| `rerun_keep` | `50` | 재실행용 중간 결과를 최근 몇 건까지 남길지. 오래된 것부터 지운다. 0 = 무제한. |
| `rerun_max_mb` | `4.0` | 재실행용 중간 결과 한 건의 상한(MB). 넘으면 저장하지 않고 trace 에 이유를 남긴다. |
| `rrf_k` | `60` | RRF 융합 상수. 작을수록 각 채널 1위를 강하게 믿고, 클수록 순위 차이를 완만하게 본다. |
| `sweep_dir` | `"data/sweeps"` | 파라미터 스윕 결과 폴더 (sw_<id>.json — 값별 단계 요약·순위·답변). data_dir 기준 상대 경로 가능. `sweep run\|list\|show\|compare` · /api/sweep · wiki_sweep 이 읽고 쓴다 — docs/SWEEP.md. |
| `sweep_keep` | `30` | 스윕 결과를 최근 몇 건까지 남길지. 오래된 것부터 지운다. 0 = 무제한. |
| `sweep_max_parallel` | `1` | 스윕에서 값을 동시에 몇 개 돌릴지. 1(기본) = 순차. 게이트웨이 한도가 넉넉하면 2~4. 결과 순서는 값 순서로 고정된다. |
| `sweep_max_values` | `20` | 한 스윕에서 돌릴 값의 최대 개수 (range·values 가 이보다 많으면 거부). 값마다 재실행 = LLM 호출이므로 폭주 방지. |
| `timezone` | `"Asia/Seoul"` | 상대 시간 표현(지난주·어제·3일전) 해석 기준 시간대. 기본 Asia/Seoul. |
| `top_k_final` | `8` | 리랭크 뒤 컨텍스트에 넣는 최종 청크 수. 답변 품질과 입력 토큰을 가장 직접 좌우한다. |
| `top_k_fts` | `12` | FTS(키워드) 채널이 융합에 넘기는 후보 수. |
| `top_k_graph` | `12` | 그래프 채널이 융합에 넘기는 후보 수. |
| `top_k_vector` | `12` | 벡터 채널이 융합에 넘기는 후보 수. |
| `wal_checkpoint_mb` | `64` | 빌드 중 WAL 파일이 이 크기(MB)를 넘으면 체크포인트. |
| `web_host` | `"0.0.0.0"` | serve 기본 바인드 주소 (플래그 --host 가 우선). 사내 공개는 0.0.0.0 — security.json 의 로그인 설정이 있어야 허용. |
| `web_port` | `8765` | serve 기본 포트 (--port 가 우선). Web UI 와 MCP Streamable HTTP(POST /mcp) 가 같은 포트. |
| `week_start` | `"mon"` | 주 시작 요일 (mon\|sun) — '지난주' 범위 계산. |
| `wiki_dir` | `"C:\Users\user\project\llm-wiki-rag-selfevolving\wiki"` | 생성된 위키 페이지(`wiki/*.md`)가 사는 폴더. 사람이 고친 `## 편집 노트` 는 다음 빌드에 다시 색인된다. |

## 3. 역할별 LLM — `config.json` 의 `llm_roles.<role>`

역할 10개: `answer`, `rerank`, `extract`, `summary`, `review`, `expand`, `verify`, `forensic`, `fusion`, `select`

각 역할에 줄 수 있는 값: `provider`, `model`, `effort`, `timeout_s`, `retries`, `backoff_s`, `backoff`, `backoff_max_s`, `budget_s`, `circuit_failures`, `circuit_cooldown_s`, `ensemble`. **비우면 전역값을 상속**한다.

| 속성 | 뜻 | 비우면 |
|---|---|---|
| `provider` | `auto` \| `anthropic` \| `openai` \| `ollama` \| `headless:<agent>` \| `mock` \| `none` | 모델이 `models.json` 에 한 provider 로만 있으면 **그 provider**, 아니면 전역 `llm_provider` |
| `model` | 모델 id (카탈로그에 없어도 동작) | 전역 `llm_model` |
| `effort` | `low` \| `medium` \| `high` | `llm_effort` (answer 역할은 `answer_effort`) |
| `timeout_s` · `retries` · `backoff` · `backoff_s` · `backoff_max_s` · `budget_s` | 호출 1회의 시간 제한과 재시도 정책 | `llm_timeout` · `llm_retries` · `llm_retry_backoff*` · `llm_budget_s` |
| `circuit_failures` · `circuit_cooldown_s` | 연속 실패 시 잠시 호출을 끊는다 | `llm_circuit_failures` · `llm_circuit_cooldown_s` |
| `max_tokens` | 출력 토큰 상한 | 단계 기본값: answer 3000, expand 400, extract 4000, forensic 1200, fusion 400, rerank 400, review 3000, select 400, summary 800, verify 1500 |
| `ensemble` | **한 역할에 LLM 최대 3개 병렬 + 취합** — [ENSEMBLE.md](ENSEMBLE.md) | 꺼짐(단일 LLM) |

`ensemble` 의 모양 (기본값: `wait`=all, `timeout_s`=120, `min_results`=1, `prompt`=ensemble_merge):

```jsonc
"llm_roles": {
  "answer": {
    "model": "claude-sonnet-5",
    "ensemble": {
      "enabled": true,
      "members": [                       // 최대 3개 · enabled:false 이거나 model 이 비면 쓰지 않는다
        {"enabled": true,  "provider": "anthropic", "model": "claude-sonnet-5", "weight": 1.5},
        {"enabled": true,  "provider": "openai",    "model": "gpt-4o-mini",     "weight": 1.0},
        {"enabled": false, "provider": "",          "model": "",                "weight": 1.0}
      ],
      "wait": "all",                     // all = 전부 기다림 · timeout = timeout_s 까지 온 것만
      "timeout_s": 120,
      "min_results": 1,                  // 이보다 적게 오면 앙상블 실패 → 단일 결과
      "aggregator": {"provider": "anthropic", "model": "claude-sonnet-5"},   // 비우면 역할 모델
      "prompt": "ensemble_merge"         // prompts/ensemble_merge.md — 가중치 해석 규칙을 여기에 쓴다
    }
  }
}
```

## 4. 토글 66개 — `config.json` 의 `toggles`

요청 단위로도 바꾼다: 사이드바 체크 · 🧭 Pipeline · CLI 플래그(`--rerank` / `--no-rerank`) · `overrides`.
`품질↑` 는 켰을 때 그 축이 좋아진다는 뜻, `속도↓` 는 느려진다는 뜻이다.

### ① 검색 — 어디서 찾나

채널을 켜고 끄고, 질의를 넓히는 단계. 채널을 끄면 그 신호는 아예 없다.

| 토글 | 기본 | 켜면 | 설명 |
|---|---|---|---|
| `fts` | on | 품질↑ | FTS5(BM25) 키워드 검색 채널. |
| `vector` | on | 품질↑ · 속도↓ | 벡터 유사도 검색 채널. |
| `graph` | on | 품질↑ | 그래프(엔티티 시드 → n-hop 확장) 검색 채널. |
| `external_rag` | off | 품질↑ · 속도↓ | [품질/지연] mcp_sources.json 의 retrieve 매핑(다른 RAG·검색 API)을 질의마다 호출해 결과를 검색 채널 ext_<source> 로 융합(rrf). 가중치 channel_w_external × 소스 weight, 건수 external_rag_k. 외부 응답 지연이 질의 지연에 더해진다. |
| `router` | on | 품질↑ | 질의 유형에 따라 채널 가중치를 조정하는 휴리스틱 라우터. |
| `router_llm` | off | 품질↑ · 속도↓ · 토큰↓ | [품질/토큰] LLM 이 질의 의도·문서 유형을 분류해 라우터/부스트에 반영. |
| `time_scope` | on | 품질↑ | [품질] '지난주·어제·3일전·2026년 8월' 등 시간 표현을 날짜 범위로 바꿔 문서 날짜로 boost/filter. |
| `query_rules` | on | 품질↑ | [품질] query_rules.json 의 acronym/synonym/alias/related/exclude 규칙으로 질의 확장 (LLM 불필요, 결정적). |
| `query_expand` | off | 품질↑ · 속도↓ · 토큰↓ | [품질/토큰] LLM 이 추가 검색 질의를 생성 (원 질의는 항상 유지). LLM 1회. |
| `query_decompose` | off | 품질↑ · 속도↓ · 토큰↓ | [품질/토큰] 다중 홉 질문을 sub-query 로 분해해 각각 검색 (query_expand 와 같은 호출에서 수행). |
| `pins` | on | 품질↑ | [품질] pins.json 의 고정 근거(조건부 문서/청크, 질의별 정답)를 검색 결과 상단에 주입. |
| `llm_after_fusion` | off | 품질↑ · 속도↓ · 토큰↓ | [품질/토큰] 융합·부스트 직후(리랭크 전) LLM(역할 fusion, prompts/fusion_review.md)이 상위 fusion_llm_candidates 후보의 제목·발췌를 보고 명백히 무관한 것을 keep/drop 으로 골라낸다. drop 은 fused × fusion_llm_drop_penalty(0 이면 제거, why=llm_drop). 실패/파싱 오류면 순위 그대로. LLM 1회. trace: fusion_llm. |
| `rerank` | on | 품질↑ · 속도↓ | 융합 후보 재정렬 (LLM 가능 시 LLM, 아니면 로컬 휴리스틱). |
| `llm_after_rerank` | off | 품질↑ · 속도↓ · 토큰↓ | [품질/토큰] 리랭크 직후(문서 확장·컨텍스트 전) LLM(역할 select, prompts/rerank_review.md)이 상위 post_rerank_llm_k 후보 중 컨텍스트에 넣을 청크(select, 그 순서가 최종 순서)와 통째로 읽힐 문서(expand_docs → doc_expand 가 그 문서를 우선·전체 확장)를 고른다. 실패면 리랭크 순위 그대로. LLM 1회. trace: rerank_review_llm. |

### ② 근거와 답변 — 어떻게 답하나

찾은 것을 검증하고 답을 만드는 단계. LLM 호출이 몰려 있어 시간·토큰의 대부분을 쓴다.

| 토글 | 기본 | 켜면 | 설명 |
|---|---|---|---|
| `doc_expand` | on | 품질↑ · 토큰↓ | [품질/토큰] 리랭크 후 상위 문서(doc_expand_top_docs)의 나머지 청크 중 질의 관련(키워드+벡터 hybrid ≥ doc_expand_min_score) 청크를 문서 순서로 컨텍스트에 추가. 인용 [C#] 가능, why=doc_expand. |
| `evidence_check` | on | 품질↑ | [품질] 리랭크 후 근거 충분성 판정(휴리스틱: 점수·채널 합의·키워드 커버리지). 부족하면 fallback_loop 트리거. |
| `evidence_check_llm` | off | 품질↑ · 속도↓ · 토큰↓ | [품질/토큰] 충분성 판정을 LLM 으로 (JSON: sufficient\|weak\|insufficient + 부족 항목 + 후속 질의). |
| `fallback_loop` | off | 품질↑ · 속도↓ · 토큰↓ | [품질/지연/토큰] 근거 부족 시 L1 규칙 확장 → L2 LLM 확장 → L3 그래프 확장 → L4 광역 검색 순으로 재시도. attempt/token/latency 예산으로 제한. |
| `llm_answer` | on | 품질↑ · 속도↓ · 토큰↓ | LLM 답변 생성 (불가 시 추출식 답변 폴백). |
| `evidence_compress` | off | 속도↓ · 토큰↑ | [토큰] LLM 이 근거 문단에서 질문 관련 문장만 남김 (context_trim 의 LLM 판). |
| `claim_check` | on | 품질↑ · 속도↓ | [품질] 답변의 사실 문장마다 인용 존재 + 인용 근거가 실제로 지지하는지(키워드·수치·ID 대조) 검증 → groundedness. |
| `claim_check_llm` | off | 품질↑ · 속도↓ · 토큰↓ | [품질/토큰] claim 지원 검증을 LLM/NLI 판정으로 (supported\|partial\|unsupported). |
| `answer_refine` | off | 품질↑ · 속도↓ · 토큰↓ | [품질/토큰] 미지원 문장이 있으면 LLM 이 답변을 1회 재작성 (근거 밖 내용 제거). |

### ③ 질의 속도 · 토큰

켜면 빨라지고 토큰을 아끼는 것들 (일부는 품질을 조금 내준다).

| 토글 | 기본 | 켜면 | 설명 |
|---|---|---|---|
| `rerank_llm` | on | 품질↑ · 속도↓ · 토큰↓ | [토큰] 리랭크에 LLM 사용. 끄면 로컬 휴리스틱만 (토큰 0, 수 ms). |
| `query_cache` | off | 속도↑ · 토큰↑ | [토큰/지연] 동일 질의+설정+빌드버전 결과를 메모리 캐시 (LLM 호출 생략). **기본 off** — 켜 두면 설정을 바꿔 가며 확인할 때 예전 답이 그대로 돌아와 '고쳤는데 그대로다' 로 보인다. 운영에 올린 뒤 토큰을 아끼려면 켠다. ※ 캐시는 둘이다 — 이것을 꺼도 precompute(영속 answer_cache)가 계속 답을 돌려주므로, 매번 새로 계산하려면 precompute 도 함께 끈다. |
| `precompute` | off | 속도↑ · 토큰↑ | [속도/토큰] 사전 계산된 답변 캐시(answer_cache, build_version 키)를 우선 사용. **기본 off** (query_cache 와 같은 이유 — 바꾼 설정이 반영되지 않은 답이 돌아온다). query_cache 와 별개의 영속 캐시라, 매번 새로 계산하려면 둘 다 꺼야 한다. 내용 확인·정리: `precompute status\|check\|clear`. |
| `context_trim` | on | 속도↑ · 토큰↑ | [토큰] 긴 청크를 질의 관련 문장 위주로 압축해 답변 프롬프트 입력 토큰 절감. |
| `dedupe_hits` | on | 속도↑ · 토큰↑ | [토큰] 같은 문서의 겹치는(오버랩) 청크를 컨텍스트에서 제거. |
| `feedback_boost` | off | 품질↑ | [품질] 긍정 피드백을 받은 청크에 감쇠하는 소량 boost. |

### ④ 디버깅 · 기록

답이 왜 그렇게 나왔는지 되짚기 위한 기록. 품질은 그대로이고 약간의 시간·디스크를 쓴다.

| 토글 | 기본 | 켜면 | 설명 |
|---|---|---|---|
| `analysis_mode` | off | 속도↓ · 토큰↓ | [디버그] 상세 분석 모드. 질의를 debug_level 2(단계별 debug·프롬프트 샘플)로 실행하고 모든 단계 결과·설정 스냅샷·품질/속도/토큰 렌즈 소견과 조절점을 한 장의 마크다운(logs/analysis/req_<id>.md)으로 남긴다. LLM 에게 그대로 주어 튜닝을 물을 수 있다. 질의가 느려지고 requests 행이 커지므로 디버깅할 때만 켠다. |
| `forensic_auto` | on | 속도↓ | [운영] 근거 부족·미지원 답변이 나오면 포렌식 진단을 자동 실행해 forensics 테이블에 누적. |
| `llm_failure_report` | on | — | [운영] LLM 호출이 재시도(llm_retries / agents.json retries) 후에도 실패하면 결과 llm_report 와 답변 상단에 역할·시도 횟수·오류·대체 경로(추출식 등)를 보고. |
| `degrade_on_llm_failure` | on | — | [운영] 답변 LLM 이 재시도(llm_retries)·llm_fallbacks 뒤에도 실패했을 때 추출식 답변으로 **계속할지**. 켜짐(기본) = 지금까지의 근거로 추출식 답변 + llm_report. 끄면 그 질의는 result_type=error 로 끝나고(답변 본문은 오류 한 줄) llm_report 만 남는다 — '틀린 답보다 실패가 낫다' 는 운영에서. 리랭크의 로컬 폴백·규칙 그래프 폴백은 값싸므로 이 토글과 무관하게 계속 동작한다. 재시도(llm_retries)·llm_fallbacks·fallback_loop 와는 별개의 장치(docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918.md §0.2). |
| `rerun_capture` | on | 속도↓ | [디버깅] 질의마다 단계별 중간 결과(순서·점수·컨텍스트·답변)를 data/reruns 에 남겨, 워터폴의 ⟲ 로 **그 단계부터만** 다시 돌릴 수 있게 한다. 청크 본문은 저장하지 않아 한 건 수십 KB. 끄면 ⟲ 가 '저장된 중간 결과 없음' 으로 막힌다. 보관 개수·상한: rerun_keep · rerun_max_mb. 설명: docs/RERUN.md |
| `log_stages` | on | — | [디버그] 프로파일 단계 종료(이름·ms·요약)를 logs/ 파일에 기록. 정상 동작도 추적 가능. |
| `profile_expansion` | off | — | [디버그] 확장 전 질의로도 검색을 실행해 규칙/LLM 확장이 추가한 hit 를 프로파일에 기록 (지연 2배). |

### ⑤ 빌드 — 무엇을 색인하나

색인에 무엇을 넣을지. 여기서 끈 것은 질의에서 켜도 쓸 것이 없다.

| 토글 | 기본 | 켜면 | 설명 |
|---|---|---|---|
| `build_fts` | on | 품질↑ · 속도↓ | [빌드] FTS 색인(chunks_fts) 쓰기. 끄면 이번 빌드에서 FTS 채널이 갱신되지 않음 (build verify 의 fts_missing 으로 확인, `build fts` 로 따로 재색인). |
| `embed` | on | 품질↑ · 속도↓ | 청크 벡터 임베딩 생성 (벡터 검색 채널의 전제). |
| `rule_graph` | on | 품질↑ · 속도↓ | 규칙(사전+정규식) 기반 엔티티/관계 추출. 비용 0, 결정적. |
| `llm_graph` | off | 품질↑ · 속도↓ · 토큰↓ | LLM 기반 엔티티/관계 추출 (빌드 시 청크마다 1회 호출 → 토큰 소비 큼; llm_graph_budget 으로 상한). |
| `communities` | on | 품질↑ · 속도↓ | 그래프 커뮤니티 탐지 (label propagation). 전체 빌드에서 실행. |
| `community_summary` | off | 품질↑ · 속도↓ · 토큰↓ | 커뮤니티별 LLM 요약 (토큰 소비). |
| `wiki_pages` | on | 속도↓ | 엔티티별 위키 마크다운 페이지 생성. |
| `incremental` | on | 속도↑ | 문서 해시 기반 증분 빌드 (끄면 매번 전체 재색인). |
| `explicit_relations` | on | 품질↑ | [품질] front matter related.* 와 ID 패턴(ISSUE-/CL-) 규칙으로 결정적(explicit/rule) 관계 생성. |
| `schema_lint` | on | 품질↑ | [데이터] 빌드 시 front matter 스키마 검사 결과를 리포트 (필수 필드·enum·ID 형식). |
| `doc_vector` | off | 품질↑ · 속도↓ | [품질] 문서 카드(제목·메타·헤딩 개요) 임베딩 채널 — 긴 설계 문서의 문서 단위 검색. |
| `fts_trigram` | off | 품질↑ · 속도↓ | [품질/크기] 한글 trigram 폴백 색인 (0-hit 시에만 사용). 색인 크기 2~3배. |

### ⑥ 빌드 속도 · 안정성

빌드 시간을 줄이거나, 빌드 전후에 스스로 점검하게 한다.

| 토글 | 기본 | 켜면 | 설명 |
|---|---|---|---|
| `stat_skip` | on | 속도↑ | [속도] 파일 mtime/size 가 이전 빌드와 같으면 읽지도 해시하지도 않음. 3,000 파일도 수십 ms 에 스캔. |
| `idf_refit_incremental` | off | 품질↑ · 속도↓ | [속도↓/정확도↑] 증분 빌드에서도 hash IDF 를 재적합하고 전체 청크를 재임베딩. 기본은 전체 빌드에서만. |
| `incremental_communities` | off | 속도↑ | [속도↓] 증분 빌드에서도 커뮤니티 재탐지. 그래프가 크면 수 초~수십 초. |
| `wiki_full_rewrite` | off | 속도↓ | [속도↓] 증분 빌드에서도 모든 위키 페이지 재작성. 기본은 변경된 엔티티 페이지만. |
| `warm_cache` | on | 속도↑ | [지연] 빌드 직후 벡터 행렬·엔티티 인덱스를 메모리에 적재해 첫 질의 지연 제거. |
| `fts_optimize` | on | 속도↑ | [속도] 전체 빌드 후 FTS5 세그먼트 병합(optimize) + PRAGMA optimize. |
| `embed_adaptive` | on | 속도↑ | [운영] 임베딩 배치 크기를 실패/지연에 따라 자동 조절하고 WAL 크기를 관리. 품질과 무관. |
| `health_check` | on | 속도↓ | [운영] 빌드 전에 프로바이더·DB·디스크·코퍼스 health 검사. 실패 항목이 있으면 빌드를 시작하지 않음 (build --force 로 강행). |
| `verify_after_build` | on | 속도↓ | [운영] 빌드 후 색인 정합성(FTS↔청크·임베딩 coverage·댕글링) 요약 검증. |
| `precompute_after_build` | off | 속도↓ | [속도] 빌드 완료 후 평가셋·빈번 질의 답변을 사전 계산. |

### ⑦ 연동 · 자동화

다른 RAG·MCP 연결, 자동 빌드, 자가진화, 화면 부가 기능.

| 토글 | 기본 | 켜면 | 설명 |
|---|---|---|---|
| `mcp_sources` | off | 품질↑ | [데이터] mcp_sources.json 의 외부 MCP(예: Mango) 에서 raw data 를 가져와 색인(ingest)/질의 보강(enrich). |
| `mcp_federation` | off | — | [MCP] mcp_sources.json 에서 expose 한 외부 서버의 tool 을 우리 MCP tools/list 에 <source>__<tool> 로 노출하고 호출을 중계 — 외부 LLM 은 /mcp 하나로 여러 RAG 를 쓴다. |
| `auto_build` | off | — | [운영] 서버가 auto_build_interval 초마다 코퍼스를 stat 스캔, 변경 시 증분 빌드. |
| `evolve_capture` | on | 품질↑ | 질의 갭/피드백을 자가진화 제안으로 기록. |
| `evolve_auto_apply` | off | — | 고신뢰 제안 자동 적용 (기본 HITL 은 꺼짐). |
| `evolve_from_forensics` | on | 품질↑ | [진화] 누적 포렌식 소견을 집계해 corpus_gap/query_rule/tuning 제안 생성. |
| `memory_decay` | on | — | [진화] 제안·규칙·pin 의 strength 를 시간 감쇠/재사용 강화 (memory decay 잡). |
| `collab` | on | — | [협업] Web UI 의 휘발성 채팅 + 게시판(/게시). 부수 기능이라 끄면 화면에서 사라지고 API 는 404 가 되며 질의·빌드·MCP 에는 영향이 없다. 세부 설정은 server.json 의 collab 절. 설명: docs/COLLAB.md |

## 5. `tuning.json` — 단계별 알고리즘 상수 141개

단계 순서대로. `리빌드` 열이 ✔ 이면 값을 바꾼 뒤 **전체 리빌드**(`build --full`)가 필요하다 — 색인에 그 값이 박혀 있기 때문이다.
값을 바꿔 가며 효과를 보려면 🧭 Pipeline 의 **범위 스윕**([SWEEP.md](SWEEP.md)).

### `chunk_index` — 청킹 · FTS 색인 (빌드)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `chunk_min_chars` | `20` | 0~200 | ✔ | 이보다 짧은 조각은 청크로 만들지 않음. — 표 구분선·빈 헤딩 같은 잡음 청크를 제거. 너무 크면 짧은 결정사항이 사라짐. |
| `tokenizer` | `"heuristic"` | `heuristic` \| `kiwi` \| `auto` | ✔ | 한국어 토크나이저. heuristic = 조사 제거 + 문자 bigram + 복합어 사전(query_rules compound). kiwi = kiwipiepy 형태소 분석기(설치 시) 명사·외래어·숫자 추가. auto = kiwi 있으면 kiwi. — kiwi 는 조사·어미 변형과 복합 명사에 강하지만 색인 시간↑. 색인과 질의가 같은 토크나이저를 써야 하므로 변경 시 전체 리빌드. |
| `wiki_min_degree` | `1` | 0~100 |  | 위키 페이지를 만들 엔티티의 최소 연결 수. 대규모 코퍼스에서 페이지 수 억제. — 높이면 위키 파일 수↓. |

### `embed` — 임베딩 (빌드)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `hash_ngram_weight` | `0.5` | 0.0~2.0 | ✔ | hash 임베딩에서 한글 문자 n-gram 특성 가중치 (단어 1.0 대비). — 높이면 표기 변형(띄어쓰기·조사)에 강해지고 낮추면 정확 단어 매칭에 가까워짐. |

### `graph_build` — 그래프 추출 · 커뮤니티 (빌드)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `cooccur_window` | `8` | 1~30 | ✔ | 규칙 추출에서 공동출현 관계를 만들 때 한 엔티티가 보는 뒤쪽 엔티티 수. — 클수록 관계 수↑(그래프 밀도↑, 검색 시 확장 노드↑), 작을수록 희소. |
| `cooccur_scale` | `120.0` | 10.0~2000.0 | ✔ | 공동출현 가중치 = min(1, cooccur_scale / 문자거리). — 클수록 멀리 떨어진 엔티티도 강하게 연결. |
| `cooccur_min_w` | `0.15` | 0.0~1.0 | ✔ | 이보다 작은 공동출현 가중치는 버림. — 높이면 약한 연결이 사라져 그래프 검색 잡음↓ recall↓. |
| `dates_per_chunk` | `8` | 0~50 | ✔ | 청크당 날짜 노드 최대 수. — 날짜 노드는 허브가 되기 쉬워 제한. |
| `amounts_per_chunk` | `6` | 0~50 | ✔ | 청크당 금액 노드 최대 수. — 위와 동일. |
| `community_iters` | `20` | 1~100 | ✔ | label propagation 반복 횟수. — 많을수록 커뮤니티가 안정되지만 큰 그래프에서 시간↑. 보통 10회 안에 수렴. |
| `llm_known_entities` | `60` | 0~300 |  | LLM 추출 프롬프트에 넣는 '이미 알려진 엔티티' 수. — 많을수록 이름 정규화(같은 대상 같은 이름)↑, 입력 토큰↑. |

### `time_scope` — 시간 표현 해석 (질의)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `time_mode` | `"boost"` | `boost` \| `filter` |  | 시간 표현이 있을 때 날짜 범위를 boost(가산) 로 쓸지 filter(범위 밖 제외) 로 쓸지. filter 가 0건이면 boost 로 자동 완화. — filter 는 정밀하지만 문서 날짜 메타가 없는 문서를 놓침. boost 는 안전. |
| `time_boost_w` | `0.5` | 0.0~3.0 |  | 날짜 범위 안 문서에 곱하는 부스트 (fused × (1+w)). |
| `recency_half_life_days` | `0` | 0~3650 |  | 최신성 부스트 반감기(일). 0 이면 끔. 시간 표현이 없어도 최신 문서를 약간 우대. — 주간 보고·이슈처럼 최신이 중요한 코퍼스에서 유용. 설계 문서 위주면 0. |

### `query_rules` — 규칙 기반 질의 확장 (질의)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `syn_w` | `0.8` | 0.0~2.0 |  | synonym 확장 리스트 가중치 (원 질의 1.0 대비). |
| `related_w` | `0.4` | 0.0~2.0 |  | related(관련어) 보조 리스트 가중치 — 주 질의에 섞지 않고 별도 리스트로 융합. — 높이면 관련 주제가 상위로 올라와 precision↓. |
| `exclude_penalty` | `0.5` | 0.0~1.0 |  | exclude 용어를 포함한 후보의 fused 점수 배율 (0=완전 제거). |
| `acronym_phrase` | `true` | `True` \| `False` |  | acronym 확장어를 구문(phrase) 검색으로 넣을지 (false 면 토큰 OR). |
| `query_rules_max_rounds` | `2` | 1~5 |  | 규칙을 몇 번 접어 적용할지. 1 이면 한 번만 — 'TAT→Turn Around Time'(acronym) 뒤에 걸린 'Turn Around Time→응답시간'(synonym) 이 무시된다. — 크게 하면 사슬이 긴 사전에서 확장어가 폭증해 precision↓·지연↑. 연관어(related)와 별칭(alias)은 다시 펼치지 않는다. |
| `related_symmetric` | `false` | `True` \| `False` |  | related(연관어)를 양방향으로 쓸지. false(기본) 는 일방 — 키 A 가 질의에 있을 때만 값 B 를 보조 리스트에 넣는다. true 면 B 가 질의에 있을 때 A 도 보조 리스트에 넣는다 (acronym/synonym 은 원래 양방향, alias 는 늘 일방). `rules explain <용어>` 로 확인. — 켜면 recall↑ 이지만 연관어가 서로를 끌어와 precision↓. 동치라면 related 대신 acronym/synonym 에 넣는 것이 정답. |

### `router` — 적응형 라우터 (질의)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `router_short_kw` | `2` | 0~10 |  | 키워드 수가 이 이하이면 'keyword' 질의로 분류 (FTS 가중↑). — 짧은 질의를 키워드 검색 위주로. 너무 크면 대부분이 keyword 로 분류돼 벡터/그래프가 약해짐. |
| `router_long_kw` | `5` | 1~30 |  | 키워드 수가 이 이상이면 'semantic' 질의로 분류 (벡터 가중↑). — 긴 서술형 질문에 의미 검색 비중↑. |
| `router_entity_min` | `1.0` | 0.0~20.0 |  | 엔티티 매칭 점수가 이 이상인 것만 '엔티티 있음' 으로 셈. — 낮추면 약한 FTS 매칭도 relational 판단에 포함. |
| `router_strong_seed` | `5.0` | 0.0~50.0 |  | 정확 별칭 매칭(강한 시드) 판단 점수. — 강한 시드가 2개 이상이면 그래프 가중치를 FTS 와 동등하게. |
| `router_base_graph` | `0.7` | 0.0~3.0 |  | hybrid 질의의 기본 그래프 가중치 (FTS·벡터는 1.0). — 그래프 채널의 기본 영향력. 그래프가 잡음이면 낮춤. |
| `router_kw_fts` | `1.4` | 0.0~3.0 |  | keyword 질의의 FTS 가중치. |
| `router_kw_vector` | `0.8` | 0.0~3.0 |  | keyword 질의의 벡터 가중치. |
| `router_kw_graph` | `0.6` | 0.0~3.0 |  | keyword 질의의 그래프 가중치. |
| `router_rel_graph` | `0.85` | 0.0~3.0 |  | relational 질의(엔티티 2개↑ 또는 관계어)의 그래프 가중치. |
| `router_rel_graph_strong` | `1.0` | 0.0~3.0 |  | relational + 강한 시드 2개↑ 일 때 그래프 가중치. |
| `router_rel_vector` | `0.9` | 0.0~3.0 |  | relational 질의의 벡터 가중치. |
| `router_num_fts_bonus` | `0.3` | 0.0~2.0 |  | 질의에 숫자가 있으면 FTS 가중치에 더함. — 날짜·금액 질문은 정확 매칭이 중요. |
| `router_sem_vector_bonus` | `0.3` | 0.0~2.0 |  | semantic 질의의 벡터 가중치 보너스. |

### `query_expand` — LLM 질의 확장 (질의, 옵션)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `query_expand_n` | `2` | 1~5 |  | 생성할 대체 질의 수. — 많을수록 recall↑ 지연↑ (질의마다 FTS+벡터 실행). |
| `query_expand_w` | `0.6` | 0.0~2.0 |  | 대체 질의 결과 리스트의 융합 가중치 (원 질의 채널 가중치 대비 배율). — 1.0 이면 원 질의와 동등. |
| `query_decompose_max` | `3` | 1~6 |  | 분해 sub-query 최대 수. |

### `fts_search` — FTS(BM25) 검색 + PRF (질의)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `fts_mode` | `"tiered"` | `tiered` \| `or` |  | 질의 토큰 결합 방식. tiered = 모든 키워드 AND 로 먼저 검색해 정밀 결과를 앞세우고 부족하면 OR 로 보충. or = OR 만. — tiered 는 키워드가 모두 들어있는 문단을 최상위로 올려 MRR↑. 키워드가 많고 문서가 짧으면 AND 결과가 0 이라 OR 로 폴백. |
| `fts_and_min_hits` | `3` | 0~50 |  | tiered 모드에서 AND 결과가 이보다 적으면 OR 결과로 보충. |
| `fts_w_heading` | `2.0` | 0.0~10.0 |  | BM25 헤딩 컬럼 가중치. — 헤딩(섹션 제목) 일치를 얼마나 중시할지. 회의록 '결정사항 > D1' 처럼 헤딩이 정보량이 크면 높게. |
| `fts_w_body` | `1.0` | 0.0~10.0 |  | BM25 본문 컬럼 가중치. |
| `fts_w_tokens` | `1.5` | 0.0~10.0 |  | BM25 정규화 토큰(조사 제거·bigram) 컬럼 가중치. — 한국어 조사 변형 매칭의 비중. 영문 위주면 낮춤. |
| `fts_bigram_fallback` | `true` | `True` \| `False` |  | 결과가 없으면 조사 제거 + 문자 bigram 으로 재검색. — 오타·띄어쓰기 변형에 강해짐. 잡음 hit 가 생길 수 있음. |
| `fts_synonym_expand` | `true` | `True` \| `False` |  | 동의어 사전(자가진화 synonyms) 으로 질의 확장. |
| `fts_snippet_tokens` | `18` | 5~64 |  | 스니펫 길이(토큰). — UI 표시용. |
| `prf_enabled` | `false` | `True` \| `False` |  | PRF(pseudo-relevance feedback, RM3 식): 1차 FTS 상위 문단의 빈출 용어로 질의를 확장해 재검색. — LLM 없이 recall↑ (어휘 불일치 완화). 1차 결과가 틀리면 잘못된 방향으로 확장(query drift) 위험 → 상위 문단 수를 작게. |
| `prf_docs` | `3` | 1~10 |  | PRF 가 참조할 상위 문단 수. |
| `prf_terms` | `4` | 1~20 |  | PRF 로 추가할 용어 수. |

### `vector_search` — 벡터 검색 (질의)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `vector_min_sim` | `0.0` | -1.0~1.0 |  | 이 코사인 유사도 이하의 후보는 버림. — hash 임베딩은 0.1~0.6 범위. 낮은 유사도 잡음을 잘라 융합 품질↑. 너무 높으면 벡터 채널이 비어 버림. |

### `graph_search` — 그래프 검색 (질의)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `graph_seed_min` | `0.5` | 0.0~20.0 |  | 시드로 쓸 최소 엔티티 매칭 점수. — 낮추면 약한 매칭도 시드가 되어 recall↑ 잡음↑. |
| `graph_max_seeds` | `6` | 1~30 |  | 시드 엔티티 최대 수. |
| `graph_decay` | `0.5` | 0.05~1.0 |  | 홉마다 점수 감쇠 (decay^hop). — 작을수록 먼 노드 영향↓. |
| `graph_hub_exp` | `0.5` | 0.0~2.0 |  | 허브 페널티 지수: gain /= degree^exp. — 0 이면 페널티 없음(문서/역할 노드가 모든 것을 연결), 1 이면 강한 페널티. |
| `graph_frontier` | `60` | 5~500 |  | 홉당 확장 노드 상한. — 지연과 recall 의 트레이드오프. |
| `graph_revisit_factor` | `0.3` | 0.0~1.0 |  | 이미 방문한 노드에 더해지는 gain 배율. |
| `graph_seed_chunk_w` | `2.0` | 0.0~10.0 |  | 시드 엔티티가 직접 언급된 청크 점수 배율 (확장 노드 청크 = 1.0). — 높이면 시드 근거 문단 우선, 낮추면 다중 홉 문단도 부상. |
| `graph_top_entities` | `30` | 5~200 |  | 확장 후 청크 수집에 쓰는 상위 엔티티 수. |
| `graph_rel_bonus_n` | `40` | 0~200 |  | 관계 근거 청크에 순위 보너스를 주는 상위 관계 수. |
| `graph_cover_w` | `2.0` | 0.0~10.0 |  | 이중 검색 재가중: 질의 키워드 커버리지 가산 배율 (score*(base+cover) + cover*w). — 키워드가 실제로 들어있는 문단을 앞세움(LightRAG low-level). |
| `graph_cover_base` | `0.3` | 0.0~1.0 |  | 커버리지 0 인 문단이 유지하는 점수 비율. — 0 이면 키워드 없는 문단 완전 제거. |
| `provenance_w` | `"explicit:1.0,rule:0.9,human:0.9,llm:0.6,cooccur:0.35"` | — |  | 관계 출처(provenance)별 확장 gain 배율. explicit=front matter 명시, rule=ID/정규식 규칙, human=승인된 제안, llm=LLM 추출, cooccur=공동출현. — 결정적 관계(CL→Issue)를 공동출현보다 강하게 따라감. cooccur 를 0 으로 두면 사전 엔티티 공동출현 확장이 사라짐. |
| `graph_doc_refs_n` | `3` | 0~20 |  | 확장 상위 엔티티마다 doc_refs 로 추가할 문서 수 (문서의 첫 청크/최다 언급 청크를 후보로). 0 이면 끔. — ID 노드(ISSUE-2041)의 원본 문서를 바로 후보에 올려 문서 단위 검색 품질↑. |

### `rrf_fuse` — 융합 · 부스트 (질의)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `fusion_method` | `"rrf"` | `rrf` \| `weighted` \| `minmax` \| `zscore` \| `dbsf` \| `rrf_boost` |  | 융합 방식. rrf = 순위 기반(점수 척도 무관, 안정적). weighted/minmax = 채널 점수 min-max 정규화 가중합. zscore = 채널별 z-정규화 가중합. dbsf = 3σ 정규화(분포 기반). rrf_boost = rrf + 후보 점수 소량 반영. — rrf 는 채널별 점수 척도가 달라도 안전(OpenSearch 벤치: 튜닝된 정규화 대비 NDCG -3~4%, 강건). 정규화 계열은 한 채널이 압도적으로 확신할 때 그 결과를 살리지만 outlier 에 민감. `fusion compare` 로 평가셋에서 비교. |
| `fusion_multi_bonus` | `0.0` | 0.0~1.0 |  | 2개 이상 채널에 동시에 나온 후보에 더하는 보너스 (fused 점수 단위, rrf 는 ~0.01 스케일). — 채널 합의(consensus)를 직접 보상. 리랭크 local 의 consensus 와 중복될 수 있음. |
| `channel_w_fts` | `1.0` | 0.0~3.0 |  | 사용자 채널 가중치 배율(FTS) — 라우터 가중치에 곱함. — 특정 채널을 전역적으로 강/약화. |
| `channel_w_vector` | `1.0` | 0.0~3.0 |  | 사용자 채널 가중치 배율(벡터). |
| `channel_w_graph` | `1.0` | 0.0~3.0 |  | 사용자 채널 가중치 배율(그래프). |
| `channel_w_doc_vector` | `0.7` | 0.0~3.0 |  | 문서 카드 벡터 채널 가중치 배율. |
| `channel_w_external` | `1.0` | 0.0~3.0 |  | 외부 RAG 채널(ext_<source>, external_rag 토글) 가중치 배율. 소스별 weight(mcp_sources.json retrieve.weight) 와 곱한다. — 외부 결과를 내부 채널보다 앞세우려면 >1, 참고용이면 0.5 이하. |
| `external_rag_k` | `5` | 1~50 |  | 외부 RAG 소스마다 요청할 결과 수 (retrieve.args 의 {k}). fallback 라운드에서는 k_mult 배. — 많을수록 외부 지연·토큰↑. |
| `external_rag_inject` | `2` | 0~20 |  | 외부 소스별 상위 n개 결과를 (RRF 순위와 무관하게) 리랭크 후보 창에 보장 주입. 외부 채널은 리스트가 하나뿐이라 내부 리스트 여러 개(fts/alt/vector…)와 RRF 로 경쟁하면 후보 밖으로 밀리기 쉬우므로, 최종 판단은 리랭커에 맡긴다. — 0 이면 순수 RRF 경쟁. 크면 외부 결과가 항상 리랭크를 받는다(리랭크 비용↑). |
| `fts_topk_n` | `0` | 0~200 |  | FTS 채널에서 'top-k 안' 으로 볼 순위 (0 = 구간 가중 끔). 이 순위까지는 fts_topk_w, 밖은 fts_tail_w 를 채널 가중치에 곱한다. — FTS 상위만 믿고 싶을 때(정확 매칭이 강한 코퍼스) n 을 작게·topk_w 를 크게. 보조 리스트(fts_rule/fts_alt/fts_rel)에도 같은 값이 적용된다. |
| `fts_topk_w` | `1.0` | 0.0~5.0 |  | FTS 채널 top-k 안 후보의 채널 가중 배율. — 1.5 면 상위 n개가 RRF 합산에서 1.5배. 1.0 이면 변화 없음. |
| `fts_tail_w` | `1.0` | 0.0~5.0 |  | FTS 채널 top-k 밖 후보의 배율 (0 = 밖은 후보에서 버림). — 0.5 면 하위 후보의 기여가 절반. 0 이면 그 채널의 하위 후보는 융합에 참여하지 않는다(다른 채널에서 나오면 살아남는다). |
| `vector_topk_n` | `0` | 0~200 |  | 벡터 채널에서 'top-k 안' 으로 볼 순위 (0 = 끔). vector_alt 리스트에도 적용. — 의미 임베더의 상위 결과가 신뢰도가 높을 때 상위를 우대. |
| `vector_topk_w` | `1.0` | 0.0~5.0 |  | 벡터 채널 top-k 안 후보의 배율. |
| `vector_tail_w` | `1.0` | 0.0~5.0 |  | 벡터 채널 top-k 밖 후보의 배율 (0 = 버림). — hash 임베딩처럼 하위 순위가 잡음이면 0.5 이하. |
| `graph_topk_n` | `0` | 0~200 |  | 그래프 채널에서 'top-k 안' 으로 볼 순위 (0 = 끔). — 시드 직결 청크(상위)와 다중 홉 청크(하위)를 다르게 대접할 때. |
| `graph_topk_w` | `1.0` | 0.0~5.0 |  | 그래프 채널 top-k 안 후보의 배율. |
| `graph_tail_w` | `1.0` | 0.0~5.0 |  | 그래프 채널 top-k 밖 후보의 배율 (0 = 버림). |
| `doc_vector_topk_n` | `0` | 0~200 |  | 문서 카드 벡터 채널에서 'top-k 안' 으로 볼 순위 (0 = 끔). |
| `doc_vector_topk_w` | `1.0` | 0.0~5.0 |  | 문서 카드 벡터 채널 top-k 안 후보의 배율. |
| `doc_vector_tail_w` | `1.0` | 0.0~5.0 |  | 문서 카드 벡터 채널 top-k 밖 후보의 배율 (0 = 버림). |
| `external_topk_n` | `0` | 0~200 |  | 외부 RAG 채널(모든 ext_<source>)에서 'top-k 안' 으로 볼 순위 (0 = 끔). — 외부 소스의 상위 결과만 신뢰할 때. 소스마다 따로 세지 않고 각 ext_ 리스트에 같은 n 을 적용한다. |
| `external_topk_w` | `1.0` | 0.0~5.0 |  | 외부 RAG 채널 top-k 안 후보의 배율. |
| `external_tail_w` | `1.0` | 0.0~5.0 |  | 외부 RAG 채널 top-k 밖 후보의 배율 (0 = 버림). |
| `channel_inject` | `""` | — |  | 채널별 리랭크 창 보장 주입 수 `fts:2,vector:2,graph:1` (채널: fts \| vector \| graph \| doc_vector \| external). 그 채널 주 리스트의 상위 n개를 RRF 순위와 무관하게 리랭크 후보 창 안으로 올린다(창 끝 요소 바로 위의 fused, why=inject:<채널>). external_rag_inject 와 같은 방식. — '벡터 1위인데 리랭크 후보에도 못 들었다' 를 막는다. 최종 순위는 리랭커가 정하므로 부작용은 리랭크 후보 수 증가뿐. |
| `doc_type_boost` | `""` | — |  | 문서 유형 부스트 맵 `issue:1.2,cl:1.1` (fused × 값). 라우터가 힌트를 잡으면 해당 유형 추가 ×1.2. — 질문 유형과 문서 유형이 맞을 때 상위로. |
| `pin_boost` | `10.0` | 1.0~100.0 |  | pin 된 청크의 fused 점수 배율 (사실상 최상위 고정). |
| `provenance_boost` | `0.2` | 0.0~2.0 |  | 그래프 후보 중 explicit/rule 관계로 도달한 청크의 추가 배율(1+w). |
| `feedback_boost_w` | `0.15` | 0.0~1.0 |  | 긍정 피드백 청크 부스트 최대 배율(1+w×strength). |
| `fusion_llm_candidates` | `0` | 0~100 |  | 융합 뒤 LLM 검토(토글 llm_after_fusion, 역할 fusion, prompts/fusion_review.md)에 보낼 상위 후보 수. 0 = rerank_candidates 와 같게. — 많을수록 프롬프트(후보 × rerank_chunk_chars)와 토큰↑, 검토 범위↑. 리랭크 후보 수보다 크게 두면 리랭크 창 밖 후보까지 걸러 준다. |
| `fusion_llm_drop_penalty` | `0.3` | 0.0~1.0 |  | 융합 뒤 LLM 검토가 drop 으로 고른 후보의 fused 점수 배율. 0 이면 후보에서 제거한다(why=llm_drop). — 0.3 은 감점만 하므로 LLM 이 틀려도 리랭크가 되살릴 수 있다. 0 은 확실히 제거하지만 LLM 오판이 그대로 결과가 된다. |

### `rerank` — 리랭크 (질의)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `rerank_method` | `"auto"` | `auto` \| `api` \| `llm` \| `cross_encoder` \| `local` |  | 리랭크 방식. auto = rerank_url 이 있으면 api, 아니면 LLM 가능 시 LLM(rerank_llm 토글), 아니면 local. api = 전용 rerank 엔드포인트(Cohere/Jina/vLLM/Voyage). cross_encoder = sentence-transformers CrossEncoder(로컬). llm = LLM 순위. local = 휴리스틱. — api/cross_encoder 는 토큰 0·품질 높음(bge-reranker-v2-m3 등 다국어). 실패 시 local 폴백. |
| `rerank_ce_model` | `"BAAI/bge-reranker-v2-m3"` | — |  | 크로스인코더 모델명 (sentence-transformers CrossEncoder). |
| `rerank_w_cover` | `0.6` | 0.0~1.0 |  | local 리랭크: 키워드 커버리지 가중치. |
| `rerank_w_consensus` | `0.3` | 0.0~1.0 |  | local 리랭크: 채널 합의(등장 채널 수/3) 가중치. |
| `rerank_w_length` | `0.1` | 0.0~1.0 |  | local 리랭크: 길이 보정(400자 미만 감점) 가중치. |
| `rerank_fused_w` | `0.0` | 0.0~5.0 |  | 리랭크 최종 순위에 **융합·부스트 점수(fused)** 를 얼마나 섞을지. 0 = 리랭크 점수만으로 정렬(기본, 예전 동작). — 리랭크는 후보를 다시 줄 세우면서 그 앞 단계(채널 융합 rrf_k·채널 가중치·시간/문서유형/pin/provenance 부스트)가 매긴 점수를 **버린다**(동점일 때만 참고). 그래서 rrf_k·fusion_method 를 아무리 바꿔도 최종 순위가 거의 그대로다 (2026-09-17 실측: rrf_k 2~60 에서 MRR 0.467~0.477). 이 값을 올리면 융합·부스트가 최종 순위에 실제로 반영되어 그 손잡이들이 의미를 갖는다. 특히 **pin_boost 로 고정한 근거가 리랭크에 밀리는 것**을 막을 때 쓴다. 리랭크 점수와 fused 는 척도가 달라 후보 집합 안에서 각각 0~1 로 정규화한 뒤 `rerank + w × fused` 로 합친다. |
| `rerank_heading_bonus` | `0.1` | 0.0~1.0 |  | local 리랭크: 헤딩에 질의 키워드가 있으면 더하는 보너스. — 섹션 제목이 곧 주제인 문서(회의록 결정사항)에서 MRR↑ (실습 코퍼스 all 채널 MRR 0.743→0.799). 단, 제목만 맞고 본문에 답이 없는 문단(일정표)이 올라올 수 있어 단일 채널 평가에서는 1문항 하락 — 0 으로 끄면 원복. |
| `post_rerank_llm_k` | `0` | 0~100 |  | 리랭크 뒤 LLM 선택(토글 llm_after_rerank, 역할 select, prompts/rerank_review.md)에 보낼 리랭크 상위 후보 수. 0 = top_k_final × 2. — LLM 이 이 안에서 컨텍스트에 넣을 청크(select)와 통째로 읽을 문서(expand_docs)를 고른다. 선택된 청크 수는 top_k_final 에 매이지 않는다(컨텍스트 상한 context_max_chars 로만 제한). |

### `context` — 컨텍스트 구성 (질의)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `context_neighbors` | `0` | 0~3 |  | 상위 context_neighbor_top 개 청크의 앞/뒤 인접 청크를 n개씩 추가(같은 문서). — 표·목록이 청크 경계에서 잘린 경우 답변 완성도↑. 토큰↑. |
| `context_neighbor_top` | `2` | 1~10 |  | 인접 청크를 붙일 상위 청크 수. |
| `dedupe_similarity` | `0.85` | 0.5~1.0 |  | dedupe_hits 시 토큰 Jaccard 유사도가 이 이상인 문단(다른 문서 포함)을 중복으로 제거. — 일정표와 회의록에 같은 문장이 반복되는 코퍼스에서 토큰 절약. 너무 낮으면 관련 문단이 사라짐. |
| `context_graph_relations` | `15` | 0~100 |  | 컨텍스트 끝에 붙이는 그래프 관계 수. — 관계 텍스트는 다중 홉 답변에 도움, 토큰↑. |
| `doc_expand_top_docs` | `3` | 1~20 |  | 문서 단위 확장(doc_expand 토글): 리랭크 상위 청크가 속한 문서 중 앞에서 몇 개 문서를 확장할지. — 많을수록 여러 문서의 보조 청크가 들어와 근거 완전성↑ 토큰↑. |
| `doc_expand_max_chunks` | `3` | 1~50 |  | 문서 단위 확장: 문서당 추가할 최대 청크 수. — 문서 전체를 넣으려면 크게 (컨텍스트 상한 context_max_chars 는 그대로 적용). |
| `doc_expand_min_score` | `0.2` | 0.0~1.0 |  | 문서 단위 확장: 이 점수(0~1) 이상인 청크만 추가. 점수 = 키워드 커버리지·벡터 유사도(모드별). — 낮추면 관련 없는 청크까지 들어와 토큰 낭비, 높이면 확장이 거의 안 됨. 0 이면 상한까지 무조건 추가. |
| `doc_expand_mode` | `"hybrid"` | `keyword` \| `vector` \| `hybrid` \| `full` |  | 문서 단위 확장 점수 방식: keyword(질의 키워드 커버리지) \| vector(질의-청크 코사인, 부모 청크 대비 정규화) \| hybrid(가중합) \| full(근거가 나온 문서를 통째로 — 점수로 거르지 않고 문서 순서대로, doc_expand_max_chunks 와 context_max_chars 로만 제한). — hash 임베더에서는 keyword 비중이 안전. 의미 임베더면 vector/hybrid. full 은 근거 문서 전체를 읽히므로 품질↑·토큰↑ (doc_expand_max_chunks 를 함께 키운다). |
| `doc_expand_w` | `0.5` | 0.0~1.0 |  | hybrid 모드에서 벡터 점수 가중 (키워드는 1-w). |

### `evidence` — 근거 충분성 판정 · fallback 루프 (질의)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `evidence_min_score` | `0.015` | 0.0~1.0 |  | 충분성 휴리스틱: 상위 fused 점수가 이 미만이면 weak. — rrf 스케일(1/(60+r)): 단일 채널 1위 ≈0.0164, 채널 2개 합의 ≈0.03. 0.02 로 올리면 단일 채널 근거는 모두 weak. |
| `evidence_min_channels` | `1` | 0~4 |  | 충분성 휴리스틱: 상위 후보가 등장한 채널 수가 이 미만이면 weak. — 2 로 올리면 채널 합의를 요구. |
| `evidence_min_cover` | `0.5` | 0.0~1.0 |  | 충분성 휴리스틱: 컨텍스트가 질의 키워드를 이 비율 미만으로 커버하면 insufficient (1.0 미만이면 weak). |
| `evidence_min_chars` | `200` | 0~5000 |  | 컨텍스트 글자수가 이 미만이면 weak. |
| `fallback_max_attempts` | `2` | 0~6 |  | fallback 루프 최대 재시도 횟수 (L1→L4 순으로 1회씩). — over-retrieval 방지 (에이전틱 RAG 최대 실패 원인). |
| `fallback_token_budget` | `20000` | 0~500000 |  | fallback 루프에서 쓸 수 있는 LLM 토큰 총량. |
| `fallback_latency_ms` | `30000` | 0~600000 |  | fallback 루프 전체 지연 상한(ms). |
| `fallback_widen_factor` | `2.0` | 1.0~5.0 |  | fallback 라운드마다 top_k 를 곱하는 배율. |
| `fallback_levels` | `"rules,expand,graph,wide"` | — |  | fallback 단계 순서 (rules \| expand \| graph \| wide \| mcp). — 값싼 단계부터. mcp 는 mcp_sources 토글 필요. |

### `answer` — 답변 생성 (질의)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `answer_length_target` | `"normal"` | `short` \| `normal` \| `long` |  | 답변 상세도 목표: short(핵심만) \| normal \| long(근거 전체를 상세 설명). 프롬프트에 반영. — long 은 evidence-rich 답변, 토큰↑. |
| `answer_repeat_guard` | `true` | — |  | 답변이 같은 구절을 무한 반복하는 LLM 고장(반복 루프)을 잡아 잘라내고 경고를 붙인다. — 끄면 반복된 답변이 그대로 나가고 캐시에도 저장된다. |
| `answer_repeat_min_chars` | `12` | 4~400 |  | 반복으로 판정할 최소 구절 길이(글자). — 너무 작으면 정상적인 짧은 반복도 잡는다. |
| `answer_repeat_times` | `4` | 3~50 |  | 같은 구절이 연속으로 이 횟수 이상 나오면 반복 루프로 본다. — 표·목록에는 정상적인 반복이 있으므로 3 미만은 권하지 않는다. |
| `extractive_sentences` | `6` | 1~30 |  | 추출식 답변 문장 수. |
| `extractive_min_len` | `15` | 1~200 |  | 추출식 답변 후보 문장 최소 길이. |
| `extractive_max_len` | `400` | 20~2000 |  | 추출식 답변 후보 문장 최대 길이. |
| `refs_preview_chars` | `200` | 0~2000 |  | 결과 refs(LLM 에 실제로 전달된 근거 목록)의 항목마다 붙이는 본문 미리보기 글자 수. CLI --json · Web REF 목록 · MCP structuredContent.refs 에 같은 값. — 화면·응답 크기에만 영향. 답변 품질과 무관. |
| `output_candidates_n` | `0` | 0~500 |  | output_mode=fused\|reranked 에서 응답 candidates[] 에 담을 후보 수. 0 = rerank_candidates 와 같게. — output_mode=answer 에는 영향 없음. 크게 두면 리랭크 창 밖 후보(리랭크 점수 없음)까지 보인다. |
| `output_list_n` | `20` | 0~500 |  | output_mode=fused\|reranked 에서 응답 lists{채널: [(chunk_id, score)]} 에 담을 채널별 상위 개수. — 화면·응답 크기에만 영향. |
| `output_chunk_chars` | `0` | 0~20000 |  | output_mode=fused\|reranked 에서 candidates[].text 를 이 글자 수로 자른다. 0 = 전문. — 응답 크기 제어. MCP 로 붙는 LLM 에 넘길 때 300~600. |

### `claim` — 답변 검증 (질의)

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `claim_support_min` | `0.5` | 0.0~1.0 |  | 휴리스틱 claim 검증: 문장의 핵심 토큰(키워드·수치·ID) 중 인용 근거에 있는 비율이 이 미만이면 unsupported. |
| `claim_policy` | `"mark"` | `mark` \| `drop` \| `refine` |  | 미지원 문장 처리: mark(문장 끝에 [미확인] 표기) \| drop(제거) \| refine(answer_refine 토글 시 LLM 재작성, 아니면 mark). — drop 은 답변이 짧아질 수 있음. |
| `claim_min_groundedness` | `0.6` | 0.0~1.0 |  | groundedness 가 이 미만이면 답변 상단에 경고 + 포렌식 자동 기록. |

### `forensic` — 포렌식 · 자가진화 메모리

| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |
|---|---|---|---|---|
| `memory_half_life_days` | `60` | 1~3650 |  | 제안·규칙·pin strength 반감기(일). 재사용/긍정 피드백 시 strength 강화. |
| `memory_archive_strength` | `0.2` | 0.0~1.0 |  | 미승인 제안의 strength 가 이 미만이면 자동 보관(archive). |
| `forensic_min_events` | `3` | 1~100 |  | 같은 주제의 포렌식 소견이 이 횟수 이상 누적되면 corpus_gap 제안 생성. |
| `forensic_near_miss_mult` | `3` | 1~20 |  | 기대 결과 포렌식(forensic expect): 기대 청크가 채널 top_k 밖이지만 top_k × 이 배수 안에 있으면 'top_k 상향' 튜닝 제안을 낸다. — 크면 먼 순위까지 상향 제안(잡음↑), 1 이면 제안 없음에 가깝다. |
| `forensic_term_candidates` | `6` | 1~30 |  | 기대 결과 포렌식: FTS 탈락 청크에서 뽑는 대표 용어(동의어 후보) 수. 헤딩 용어가 먼저, 그다음 빈도순. |
| `forensic_term_targets` | `20` | 1~200 |  | 기대 결과 포렌식: 문서 지정 없이 용어만 준 경우 코퍼스에서 그 용어를 담은 청크를 최대 몇 개까지 목표로 삼을지. — 많으면 재실행 판정이 느려진다. |
| `forensic_pin_confidence` | `0.6` | 0.0~1.0 |  | 기대 결과 포렌식이 내는 pin 제안의 confidence (evolve 목록 정렬·자동 적용 임계와 비교되는 값). |
| `forensic_suggestion_min_confidence` | `0.5` | 0.0~1.0 |  | 기대 결과 포렌식 화면에서 '수정안' 을 바로 펼쳐 보여 줄 최소 confidence. 이 미만은 접어 둔다(버리지는 않는다). — 낮추면 근거가 약한 제안까지 먼저 보이고, 높이면 확실한 것만 남는다. |
| `forensic_targets_shown` | `3` | 1~200 |  | 기대 결과 포렌식 화면에서 목표 청크의 '탈락 단계' 표를 바로 보여 줄 개수 (가장 멀리 간 것 순). 나머지는 접어 둔다. — 용어만 주고 실행하면 목표가 수십 개가 되어 같은 표가 반복된다. |

### 튜닝 표에 함께 보이지만 `config.json` 에 저장되는 키

화면·문서에서 한자리에 보여 주려고 함께 등재한 것이다. **`tuning.json` 에 적으면 반영되지 않는다** — `config.json` 에 적는다.

| 키 | 단계 | 기본 | 설명 |
|---|---|---|---|
| `chunk_max_chars` | `chunk_index` | `900` | 청크 최대 글자 수 (헤딩 단위 섹션을 문단 경계로 분할). |
| `chunk_overlap_chars` | `chunk_index` | `120` | 인접 청크 간 겹치는 글자 수. |
| `embed_dim` | `embed` | `4096` | hash 임베딩 차원 (외부 임베더는 무시). |
| `embed_batch` | `embed` | `64` | 임베딩 배치 크기. |
| `llm_graph_budget` | `graph_build` | `0` | 빌드당 LLM 추출 호출 상한 (0=무제한). |
| `llm_graph_min_chars` | `graph_build` | `80` | 이보다 짧은 청크는 LLM 추출 생략. |
| `top_k_fts` | `fts_search` | `12` | FTS 후보 수. |
| `top_k_vector` | `vector_search` | `12` | 벡터 후보 수. |
| `top_k_graph` | `graph_search` | `12` | 그래프 후보 수. |
| `graph_hops` | `graph_search` | `2` | 시드 엔티티에서 확장할 홉 수. |
| `rrf_k` | `rrf_fuse` | `60` | RRF 상수 (1/(k+rank)). |
| `rerank_candidates` | `rerank` | `16` | 리랭크 후보 수. |
| `rerank_chunk_chars` | `rerank` | `600` | LLM/크로스인코더 입력 청크 글자 수. |
| `top_k_final` | `context` | `8` | 최종 컨텍스트 후보 수. |
| `context_max_chars` | `context` | `9000` | 컨텍스트 총 글자 상한. |
| `context_chunk_chars` | `context` | `1200` | context_trim 시 청크당 글자 상한. |
| `answer_max_tokens` | `answer` | `3000` | 답변 LLM 출력 토큰 상한. |
| `answer_effort` | `answer` | `"medium"` | 답변 LLM effort. |
| `llm_effort` | `answer` | `"low"` | 추출/리랭크/요약/리뷰 LLM effort (역할별 llm_roles 로 개별 지정 가능). |

## 6. 값을 바꾼 뒤 무엇을 해야 하나

| 바꾼 것 | 해야 할 일 | 확인 |
|---|---|---|
| `config.json` 의 대부분 | 서버면 `config reload`(또는 Web 의 `↻ config.json 다시 읽기`) | `config show --effective` |
| `web_host`·`web_port`·`mcp_*` | **서버 재시작** | 기동 로그의 주소 |
| `.env` | `config reload --env` (또는 Web 의 `.env 다시 읽기`) | `config env` |
| `tuning.json` 의 `리빌드 ✔` 키 · `embed_provider`·`embed_model`·`embed_dim`·청킹 키 | **`build --full`** | `build verify` · `health` |
| `data/rules.json` | **`build graph`** | `graph profile --compare` |
| `query_rules.json` · `stopwords.json` · `prompts/*.md` · `models.json` · `agents.json` · `schedule.json` | 없음 (즉시) | `rules explain`, `prompts show`, `models list`, `schedule list` |
| `security.json` | `security reload` 또는 화면의 '다시 읽기' | `security show`, `security perms` |
| `server.json` | `server` 명령/화면은 즉시, 파일 직접 편집은 **reload** | `server limits` |

바꾼 값이 **실제로 서버에 먹었는지** 양방향으로 확인하는 하네스가 있다: `python tools/verify/verify_settings_sync.py` ([SETTINGS_SYNC.md](SETTINGS_SYNC.md)).

## 7. 자주 하는 변경 — 어디를 고치나

| 하고 싶은 것 | 고칠 곳 |
|---|---|
| 내 문서를 색인한다 | `config.json` 의 `corpus_dirs` → `build --full` |
| 사내 게이트웨이(PAT)로 LLM 을 쓴다 | `config.json` 의 `llm_provider=openai` + `openai_base_url`, `.env` 의 `OPENAI_API_KEY` — [BRINGUP_GUIDE.md §4.1](BRINGUP_GUIDE.md) |
| Anthropic 을 쓴다 | `llm_roles.<role>.provider=anthropic` + `.env` 의 `ANTHROPIC_API_KEY` |
| opencode 같은 CLI 를 LLM 으로 쓴다 | `llm_roles.<role>.provider=headless:opencode` — [HEADLESS.md](HEADLESS.md) |
| 한 단계에 모델 여러 개를 물어본다 | `llm_roles.<role>.ensemble` — [ENSEMBLE.md](ENSEMBLE.md) |
| 답이 느리다 | 사이드바 `speed` 프리셋 → `llm_after_*`·`claim_check_llm`·`rerank_llm` 끄기 · `top_k_*` 줄이기 |
| 근거가 부족해도 답을 받고 싶다 | `answer_mode=best_effort` — [ANSWER_MODES.md](ANSWER_MODES.md) |
| 중간 산출물(융합·리랭크 결과)만 받고 싶다 | `output_mode=fused\|reranked\|context` |
| 채널별 가중치를 바꾼다 | `tuning.json` 의 `channel_w_*`, `*_topk_n/_topk_w/_tail_w` — [FUSION_TOPK.md](FUSION_TOPK.md) |
| 로그가 너무 쌓인다 | `log_total_max_mb`·`log_limit_action` — [LOG_QUOTA.md](LOG_QUOTA.md) |
| 외부에 서버를 연다 | `web_host`(기본 `0.0.0.0`) + `security.json` — [SECURITY.md](SECURITY.md) |

---

생성: `python -m llmwiki config doc` · 기준 경로: `config.json`
