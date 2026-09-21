# PORTING — 다른 환경에 올릴 때 건드려야 하는 **모든 연결 정보**

> 대상: 이 폴더를 회사/다른 PC/다른 서버에 올리는 사람. `config`·`tuning`·규칙 말고도 **환경에 묶인 것**이
> 어디에 있는지를 한 장에 모았다. 기동 절차 자체는 [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md), 키 하나하나의 뜻은
> [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md).

## 0. 결론부터 — 한 폴더에 모으는 게 나은가

**"파일을 옮기지는 말고, 한 폴더를 *가리킬 수 있게* 하라"** 가 답이고, 그렇게 구현해 두었다.

- 실제로 옮기면(`conf/` 로 이사) 기존 설치·문서·`setup/` 예시·스크립트의 경로가 전부 깨진다. `.env` 는
  편집기·CI·도구들이 프로젝트 루트에서 찾는 관례가 있어서 옮기면 오히려 헷갈린다.
- 그렇다고 지금처럼 루트에 15개가 흩어져 있으면 **무엇을 들고 가야 하는지** 가 안 보인다. 실제로
  `data/rules.json` 은 설정인데 데이터 폴더에 살아서, 색인만 지우다 같이 날릴 뻔한 자리다.

그래서 **환경변수 하나**로 해결한다.

```bat
python -m llmwiki config bundle --out conf      :: 설정 17종을 conf\ 한 곳에 모은다 (.env 는 키 이름만)
set LLMWIKI_CONF_DIR=conf                       :: 이제 이 폴더의 설정이 먼저 쓰인다
python -m llmwiki config paths                  :: 어느 파일이 어디에서 오는지 확인
```

- 폴더를 **안 쓰면 지금과 완전히 똑같이** 동작한다 (기존 설치가 깨지지 않는다).
- 폴더에 **일부만** 넣어도 된다 — 없는 파일은 원래 자리를 쓴다.
- 개별 지정(`LLMWIKI_<NAME>_PATH`)이 폴더보다 세다 — 한 파일만 다른 곳에 두는 경우를 위해.
- 모을 때 `data/rules.json` 은 `rules.json` 이 되어, "설정이 데이터 폴더에 있는" 이상함이 사라진다.
- **옮길 때는 이 폴더 하나 + `corpus/` 만 들고 가면 된다.**

되돌리기: `python -m llmwiki config bundle --from conf` (원래 자리로 복사).

`logs/`·`themes`·`eval/questions.json` 은 일부러 대상에서 뺐다 — 운영 산출물, 앱 자원, 코퍼스에 가까운 자료다.

## 1. 연결 정보 지도 (이 표가 이 문서의 본체다)

### 1.1 LLM 연결

| 무엇 | 어디에 | 키 | 비고 |
|---|---|---|---|
| 기본 프로바이더·모델 | `config.json` | `llm_provider` · `llm_model` | `openai`(OpenAI 호환 게이트웨이 포함) · `anthropic` · `ollama` · `headless:<이름>` · `mock` |
| **역할별** LLM (10역할) | `config.json` › `llm_roles.<역할>` | `provider` `model` `effort` `max_tokens` `timeout_s` `retries` `backoff` `backoff_s` `backoff_max_s` `budget_s` `circuit_failures` `circuit_cooldown_s` `ensemble` | 역할: `answer rerank extract summary review expand verify forensic fusion select` — [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) |
| OpenAI 호환 게이트웨이 주소 | `config.json` | `openai_base_url` | 사내 게이트웨이면 여기. 예 `https://llm-gw.corp/v1` |
| 게이트웨이 **인증 헤더 이름** | `config.json` | `openai_api_key_header` | 기본 `authorization`. PAT 게이트웨이가 `x-api-key` 등을 쓰면 여기서 바꾼다 |
| 게이트웨이 추가 헤더 | `config.json` | `openai_extra_headers` | 예 `{"X-Tenant":"team-a"}` |
| Anthropic 주소 | `config.json` | `anthropic_base_url` | |
| Ollama 주소·모델 | `config.json` | `ollama_url` · `ollama_model` | |
| **자격증명** | **`.env`** | `OPENAI_API_KEY` · `LLM_API_KEY` · `ANTHROPIC_API_KEY` · `ANTHROPIC_AUTH_TOKEN` | **config.json 에 넣지 말 것** — 화면·로그·묶음에 노출된다 |
| headless 에이전트 실행법 | `agents.json` | `command` 템플릿 · `timeout_s` · `retries` · `arg_max_chars` · `env` | `opencode` · `claude` · `codex` · `mock`. 치환: `{model}` `{prompt}` `{prompt_file}` `{project_root}` `{python}` — [HEADLESS.md](HEADLESS.md) |
| 모델 카탈로그(드롭다운) | `models.json` | `models[]` · `embed[]` | `id` `provider` `label` `roles` `enabled` — 없는 모델을 화면에서 고르지 못하게 |
| 앙상블(역할당 여러 LLM) | `config.json` › `llm_roles.<역할>.ensemble` | `enabled` `members[]` `aggregator` | [ENSEMBLE.md](ENSEMBLE.md) |

### 1.2 임베딩·리랭크 연결

| 무엇 | 어디에 | 키 |
|---|---|---|
| 임베딩 프로바이더·모델 | `config.json` | `embed_provider`(`hash`/`ollama`/`openai`/`voyage`) · `embed_model` · `embed_dim` · `embed_store_dtype` |
| OpenAI 호환 임베딩 주소·모델 | `config.json` | `openai_embed_base_url` · `openai_embed_model` |
| 임베딩 키 | `.env` | `OPENAI_EMBED_API_KEY` · `VOYAGE_API_KEY` |
| 리랭크 API | `config.json` | `rerank_url` · `rerank_api_model` |
| 리랭크 키 | `.env` | `RERANK_API_KEY` · `COHERE_API_KEY` · `JINA_API_KEY` |

### 1.3 MCP — 이쪽이 서버가 될 때 / 남의 서버에 붙을 때

| 방향 | 어디에 | 키 |
|---|---|---|
| **이쪽이 MCP 서버** (외부 LLM 이 붙음) | `config.json` | `mcp_transport`(`stdio`/`http`) · `mcp_host` · `mcp_port` |
| 〃 접속 키 | `security.json` › `api_keys` | `apikey add <이름> --role viewer` 로 발급 (`lwk_…`) |
| **이쪽이 원격 MCP 의 브리지** | `config.json` | `mcp_url` (+ `.env` `LLMWIKI_MCP_TOKEN`) |
| **다른 RAG/MCP/REST 를 붙임** | `mcp_sources.json` | 소스마다 `transport`(`stdio`/`http`/`rest`) · `command`/`cwd`/`env` · `url`/`token`/`token_env`/`headers` · `base_url` · `expose` · `retrieve` — [RAG_FEDERATION.md](RAG_FEDERATION.md) |
| 클라이언트 쪽 붙이는 설정 | (밖) | `python -m llmwiki mcp --client-config` 가 환경에 맞춰 생성 · 예시 `setup/mcp_clients.example.json` |
| MCP 도구 플러그인 | `config.json` | `mcp_plugins_dir` (기본 `plugins/mcp_tools`) |

### 1.4 웹 서버·접근 제어

| 무엇 | 어디에 | 키 |
|---|---|---|
| 바인드 주소·포트 | `config.json` | `web_host`(기본 `0.0.0.0`) · `web_port` |
| 로그인 방식 | `security.json` | `mode` · `local`(ID/비밀번호) · **`sso`**(OIDC 또는 리버스 프록시 헤더) · `anonymous_role` · `session_hours` · `secure_cookie` — [SECURITY.md](SECURITY.md) |
| 계정·역할 | `security.json` › `users` | 역할 `viewer < class3 < class2 < class1 < builder < admin` |
| 등급별 권한 | `security.json` › `permissions` · `cli` · `destructive` | 7단계 게이트 |
| 문서 단위 접근 제어 | `docacl.json` | `enabled` · `default_min_role` · `rules[]` |
| 동시성·대기열·속도 제한 | `server.json` | `concurrency` · `timeouts` · `rate_limit` · `sessions` · `access` · `monitor` — [CONCURRENCY.md](CONCURRENCY.md) |

### 1.5 코퍼스·색인 경로

| 무엇 | 키 (`config.json`) | 비고 |
|---|---|---|
| 코퍼스 폴더 | `corpus_dirs` | 여러 개 가능. **여기만 바꾸면 되도록** 나머지는 상대 경로 |
| 색인 DB·데이터 | `data_dir` | |
| 위키 overlay | `wiki_dir` | |
| 부산물 | `requests_dir` · `rerun_dir` · `sweep_dir` | 기본 `data/` 밑 |

### 1.6 지식 규칙·문구 (환경마다 다르게 만드는 것)

| 무엇 | 파일 | 문서 |
|---|---|---|
| 질의 확장 규칙 (동의어·약어·별칭…) | `query_rules.json` | [QUERY_RULES.md](QUERY_RULES.md) |
| 그래프 빌드 규칙 (엔티티 사전·ID 패턴·관계) | `data/rules.json` | [GRAPH_RULES.md](GRAPH_RULES.md) |
| 문서 계약 (front matter 스키마) | `schemas/` | [CORPUS_CONTRACT.md](CORPUS_CONTRACT.md) |
| 프롬프트 | `prompts/*.md` | 역할마다 한 파일 |
| 불용어 | `stopwords.json` | [STOPWORDS.md](STOPWORDS.md) |
| 고정 근거 | `pins.json` | |
| 프리셋 | `presets.json` | |
| 튜닝 상수 | `tuning.json` | [TUNING.md](TUNING.md) |
| 예약 작업 | `schedule.json` | [SCHEDULER.md](SCHEDULER.md) |
| 평가셋 | `eval/questions.json` | [EVAL_TRIAL.md](EVAL_TRIAL.md) |

### 1.7 환경변수로 덮어쓰기

모든 `config.json` 키는 `LLMWIKI_<키 대문자>` 로 덮어쓸 수 있다 (`config show --effective` 의 `env` 열).
컨테이너·CI 에서 파일을 안 만들고 띄울 때 쓴다. 파일 위치 자체를 바꾸는 것은 따로다:

| 환경변수 | 뜻 |
|---|---|
| `LLMWIKI_CONF_DIR` | **설정 17종을 모아 둔 폴더** (§0) |
| `LLMWIKI_<NAME>_PATH` | 파일 하나의 위치 (`LLMWIKI_SECURITY_PATH` 등). 폴더보다 세다 |
| `LLMWIKI_CONFIG` · `LLMWIKI_ENV_FILE` · `LLMWIKI_TUNING` | 예전 이름 (계속 동작) |

`config env` 가 `.env` 의 키 이름·설정 여부·마스킹 값과 `LLMWIKI_*` 덮어쓰기를 한 표로 보여 준다.

## 2. 자격증명을 어디에 두는가 (규칙)

1. **키·토큰·비밀번호는 `.env` 에만.** `config.json` 에는 *주소와 헤더 이름*까지만.
2. `.env` 는 절대 저장소에 넣지 않는다. `config bundle` 도 기본으로 **키 이름만** 복사한다.
3. 사내 게이트웨이 PAT 은 `.env` 의 `OPENAI_API_KEY`(또는 `LLM_API_KEY`) 에 넣고, 헤더 이름이 다르면
   `openai_api_key_header` 로 맞춘다 — 코드를 고칠 일이 없다.
4. MCP 로 남에게 이 서버를 열 때는 `apikey add` 로 발급한 `lwk_…` 를 쓴다 (사람 계정 비밀번호가 아니라).

## 3. 새 환경에서 확인하는 순서

```bat
python -m llmwiki config paths          :: 어느 파일을 어디서 읽고 있나 (+ CONF_DIR 사용 여부)
python -m llmwiki config env            :: .env 키가 채워졌나 (값은 마스킹)
python -m llmwiki config show --effective   :: 파일/환경변수/기본값 중 무엇이 이겼나
python -m llmwiki models test --live    :: 역할별 LLM 이 실제로 응답하나
python -m llmwiki models test --catalog --live  :: 카탈로그의 모델 전부
python -m llmwiki mcp --doctor          :: MCP 로 붙는 쪽이 왜 안 붙는지 (서버에서 답한다)
python -m llmwiki mcp-source test       :: 붙여 놓은 외부 RAG 연결
python -m llmwiki health                :: 경로·프로바이더·색인 종합
python -m llmwiki graph-rules lint      :: 그래프 규칙이 빌드 가능한 상태인가
python -m llmwiki rules lint            :: 질의 규칙 중복·순환
```

## 4. 옮기기 절차 (요약)

```bat
:: [보내는 환경]
python -m llmwiki reset logs --apply             :: 앞 환경의 이력·로그 (docs/RESET.md)
python -m llmwiki config bundle --out conf       :: 설정 17종을 한 폴더로 (.env 는 키 이름만)

:: 복사: conf\  +  corpus\  (+ 색인을 그대로 쓸 거면 data\llmwiki.sqlite3)

:: [받는 환경]
set LLMWIKI_CONF_DIR=conf
notepad conf\.env                                :: 키 값을 채운다
notepad conf\config.json                         :: corpus_dirs · 게이트웨이 주소 · 모델
python -m llmwiki config paths
python -m llmwiki models test --live
python -m llmwiki build --full --trace
python -m llmwiki build verify
python -m llmwiki serve
```

전체 단계표(문서 계약·평가셋·스케줄·보안까지)는 [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) §0.

## 5. 문제 해결

| 증상 | 원인 | 확인 |
|---|---|---|
| 설정을 고쳤는데 안 먹는다 | 다른 파일을 읽고 있다 (CONF_DIR·`LLMWIKI_*_PATH`) | `config paths` — **실제로 읽는 경로**가 나온다 |
| `.env` 를 채웠는데 인증 실패 | 헤더 이름이 다르다 (PAT 게이트웨이) | `openai_api_key_header` · `openai_extra_headers` → `models test --live` |
| 묶음(`conf`)을 줬는데 상대가 LLM 을 못 붙인다 | `.env` 값은 일부러 비워 보낸다 | 받는 쪽에서 채운다. 값째 보내려면 `--include-secrets`(권장하지 않음) |
| 일부 설정만 폴더에 넣었더니 헷갈린다 | 없는 파일은 원래 자리를 쓴다(의도) | `config paths` 로 파일별 출처 확인 |
| 서버가 옛 설정으로 돈다 | 파이썬 모듈은 다시 읽히지 않는다 | 서버 재시작 (`config reload` 로는 부족) |
| `config.json` 이 갑자기 임시 폴더를 가리킨다 | 직접 만든 `Settings` 를 경로 없이 저장한 코드가 있다 | 이제 `StraySettingsWrite` 예외로 막힌다(§7). 이미 덮어써졌다면 `config bundle` 로 떠 둔 묶음이나 `config.json.bak-*` 에서 복구 |

**테스트·스크립트를 쓸 때** — 설정 격리는 **환경변수**(`LLMWIKI_CONFIG_PATH` 등)로 한다.
`config.CONFIG_PATH` 같은 모듈 상수를 바꾸는 방식은 지원되지 않는다. 실제로 2026-09-19 에 경로 해석을
`path_for()` 로 모으면서 그 몽키패치가 조용히 무력해졌고, 테스트가 프로젝트 `config.json` 을 덮어썼다.

## 6. 검증

```bat
python -m unittest tests.test_conf_dir      :: 19건 — 안 쓰면 안 바뀐다 · 값이 실제로 먹는다 · 부분 모으기 · 비밀 제외 · 오염 방지 가드
python -m llmwiki config bundle --out %TEMP%\conf-test
python tools/verify/verify_settings_sync.py :: UI ↔ 파일 ↔ 유효값 양방향
```

### 7.1 설정 오염 방지 가드

`config._guard_stray_write()` 는 **읽어 온 자리를 모르는 `Settings`** 가 `data_dir` 이 기본값과 다른 채로
**프로젝트 `config.json`** 에 쓰이려 할 때 `StraySettingsWrite` 예외를 던진다. `load_settings()` 로 읽은
설정은 자기 자리를 기억하므로 걸리지 않고, `config reset`(기본값 저장)과 격리 경로 저장도 통과한다.
조용히 성공하던 사고를 시끄러운 실패로 바꾼 장치다.

## 7. 구현 파일

| 파일 | 역할 |
|---|---|
| `llmwiki/config.py` | `_PATH_DEFAULTS` · `CONF_DIR_FILES` · `conf_dir()` · `path_for()` · `bundle()` |
| `llmwiki/cli.py` | `config paths` · `config bundle --out|--from` |
| `tests/test_conf_dir.py` | 회귀 테스트 |
