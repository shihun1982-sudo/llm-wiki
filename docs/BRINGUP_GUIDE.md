# Bring-up Guide — 새 환경으로 포팅하기

> 대상: 이 시스템(LLM Wiki v3)을 다른 PC/서버/조직 환경에 옮겨 세우는 엔지니어. 설치 → 설정 파일 채우기 → 프로바이더 연결 → 코퍼스 계약 적용 → 첫 빌드 → 검증 → 스케줄 등록 → 운영까지 순서대로 따라 하면 된다.
> CLI 명령의 단계별 동작과 실제 출력 예는 [CLI_FLOWS.md](CLI_FLOWS.md), 전체 구조는 [ARCHITECTURE_V3.md](ARCHITECTURE_V3.md), 문서 형식은 [CORPUS_CONTRACT.md](CORPUS_CONTRACT.md), 인터랙티브 가이드는 `docs/llmwiki_guide.html` 을 브라우저로 여세요.

## 0. 체크리스트 (요약)

| 단계 | 명령/파일 | 확인 |
|---|---|---|
| 1 | Python 3.9+ (권장 3.11+), `pip install -r requirements.txt` (+ optional) | `python setup/check_env.py` |
| 2 | 폴더 복사 (`data/ wiki/ logs/` 제외 가능) | 상대 경로 설정이라 그대로 동작 |
| 3 | `config.json` (`setup/config.example.json` 복사) — `corpus_dirs`, 모델 | `python -m llmwiki config show --effective` |
| 4 | `.env` (`setup/.env.example` 복사) — API 키/PAT. 게이트웨이·opencode 는 §4.1~4.3, 예시 `setup/config.example.pat-gateway.json` · `config.example.headless.json` | `python -m llmwiki models test --live` |
| 5 | 문서 계약: `schemas/`, 기존 문서에 front matter 추가 또는 `schemas/infer.json` 규칙 | `python -m llmwiki corpus lint` |
| 6 | 어휘/규칙: `query_rules.json`, `data/rules.json`(id_patterns, link_rules), `prompts/answer_guide.md` | `rules test "…"` |
| 7 | `python -m llmwiki health` → `build --full --trace` → `build verify` | alerts 0, coverage 100% |
| 8 | 평가셋 `eval/questions.json` 교체 → `eval` → `trial run --name baseline` | hit@k, groundedness 기준선 기록 |
| 9 | 스케줄 등록 `setup/schedule_build.ps1 -Register` (또는 cron) | `build status`, `logs tail --file build` |
| 10 | `security init` → `users add <id> --role admin` (+ SSO 설정) → `serve --host 0.0.0.0`, `claude mcp add llmwiki -- python -m llmwiki mcp` — §4.4 | 로그인 화면, viewer 로 전체 리빌드가 403 인지, `security audit` |

## 1. 환경

- **Python 3.9 이상** (개발·검증 환경: 3.14.7). 표준 라이브러리 위주라 필수 패키지는 `numpy`, `pypdf` 뿐이다.
- 선택 패키지 (`setup/requirements-optional.txt`): `anthropic`(Claude SDK; 없으면 raw HTTP), `sentence-transformers`(로컬 임베더/크로스인코더), `kiwipiepy`(한국어 형태소 분석기), `pyyaml`(front matter; 없으면 내장 파서).
- SQLite 는 FTS5 가 포함된 빌드여야 한다 (`health` 가 검사). Windows/macOS/Linux 모두 동작, 콘솔 한글은 `PYTHONIOENCODING=utf-8`(run.bat 이 설정).
- 외부 서비스는 모두 선택: Anthropic / OpenAI-compatible(vLLM·LM Studio·Ollama·OpenRouter·사내 게이트웨이) / Ollama / headless 에이전트 CLI(opencode 등) / rerank API / Voyage / Mango 등 MCP.
- 규모 기준(5,000 문서 + 50/일): SQLite 단일 파일로 충분. 벡터 RAM ≈ 청크 수 × dim × 4B (float16 이면 2B). 예) 6만 청크 × 4096d ≈ 980MB(float16 490MB) — `embed_dim` 은 자유롭게 바꿀 수 있고 `health`/`system` 이 전망을 경고한다.

## 2. 설치와 폴더

```bat
:: Windows
cd llm-wiki-rag-selfevolving
setup\install.bat            :: pip 설치 + config.json/.env 생성 + 샘플 복사 + 환경 진단
pip install -r setup\requirements-optional.txt   :: 선택
```
```bash
# macOS / Linux
bash setup/install.sh && pip install -r setup/requirements-optional.txt
```

프로젝트 폴더 전체를 복사하면 된다. 재생성 가능한 폴더(`data/`, `wiki/`, `logs/`)는 빼도 되고, 같이 복사하면 빌드 없이 바로 검색된다(단, 구버전 색인이면 `build --full` 권장 — `build verify` 가 `doc_meta_missing` 으로 알려준다).

## 3. 설정 파일 (모두 프로젝트 루트, 위치는 `LLMWIKI_<NAME>_PATH` 로 변경 가능 — `config paths`)

| 파일 | 역할 | 언제 바꾸나 | 반영 |
|---|---|---|---|
| `config.json` | 코퍼스 경로, 프로바이더/역할별 모델, 토글, 운영 수치(배치·WAL·로그·timezone) | 새 환경 필수 | 즉시(서버 reload) |
| `.env` | API 키/PAT(`OPENAI_API_KEY`·`LLM_API_KEY`, `ANTHROPIC_API_KEY`·`ANTHROPIC_AUTH_TOKEN`, `VOYAGE_API_KEY`, `RERANK_API_KEY`, MCP 토큰) + **모든 설정의 env 오버라이드** `LLMWIKI_<KEY>` / `LLMWIKI_TOGGLE_<NAME>` / `LLMWIKI_<ROLE>_MODEL` | 키 발급 후 | 프로세스 시작 |
| `security.json` | 로그인 방식(로컬 ID/비밀번호 · SSO), 역할, 파괴적 작업 정책 — [SECURITY.md](SECURITY.md) | 서버 공개 전 | 즉시(서버 reload) |
| `tuning.json` | 알고리즘 상수(FTS·라우터·그래프·융합·근거 판정·claim·메모리…) 오버라이드만 | 품질 튜닝 | 즉시 |
| `presets.json` | quality / speed / token / offline / deep_research 묶음 | 조직 정책 | `--preset`, `preset apply` |
| `query_rules.json` | acronym / synonym / alias / related / exclude / compound 사전 | 도메인 용어 | 즉시 |
| `data/rules.json` | 그래프 엔티티 사전, 관계 정규식, **ID 패턴(id_patterns)**, **결정적 링크 규칙(link_rules)**, front matter 관계 매핑 | 문서 ID 체계 | 재빌드 |
| `schemas/*.json` | 문서 유형별 스키마 + 추론 규칙 + 마이그레이션 | 새 문서 유형 | lint/빌드 |
| `prompts/*.md` | 역할별 LLM 프롬프트/답변 가이드 | 답변 스타일 | 즉시(mtime) |
| `pins.json` | 고정 근거 | 운영 중 | 즉시 |
| `agents.json` | headless 에이전트 명령 템플릿 (opencode/claude/codex/mock) | 에이전트 도입 | 즉시 |
| `mcp_sources.json` | 외부 MCP 소스(Mango 등) 명령·tool 매핑 | MCP 연결 | 즉시 |
| `eval/questions.json` | 회귀 평가셋 | 자기 코퍼스 질문으로 교체 | – |

우선순위: **환경변수 > config.json > 코드 기본값**. 현재 유효값과 출처는 `config show --effective`.

### 3.1 config.json 에서 반드시 볼 것
```json
{
  "corpus_dirs": ["D:/wiki/issues", "D:/wiki/cls", "D:/wiki/design"],
  "llm_provider": "auto",  "llm_model": "claude-opus-5",
  "llm_roles": {"rerank": {"model": "claude-haiku-4-5-20251001"}, "expand": {"model": "claude-haiku-4-5-20251001"}, "verify": {"model": "claude-haiku-4-5-20251001"}},
  "embed_provider": "auto", "embed_model": "", "embed_dim": 4096, "embed_store_dtype": "float32",
  "timezone": "Asia/Seoul"
}
```
- 역할: `answer`(답변) `rerank` `extract`(그래프) `summary` `review`(자가진화) `expand`(질의 확장/분해/LLM 라우터) `verify`(근거·claim 판정) `forensic`. 비우면 전역값 상속. 저비용 모델을 expand/verify/rerank 에 두는 것을 권장.
- `llm_provider`: `auto`(Anthropic 키 → Ollama(모델이 받아져 있을 때) → none) | `anthropic` | `openai` | `ollama` | `headless:<agent>` | `mock` | `none`.
- `embed_provider`: `auto`(Voyage 키 → Ollama bge-m3/nomic → hash) | `hash` | `voyage` | `openai` | `ollama` | `st`. 임베더/차원/dtype 을 바꾸면 `build --full`. 내용 해시 캐시(`embedding_cache`) 덕분에 같은 모델·차원으로 되돌리면 재임베딩이 없다.

## 4. 프로바이더 연결

| 방식 | 설정 | 확인 |
|---|---|---|
| **사내 게이트웨이 (URL + PAT), OpenAI-compatible** | `llm_provider=openai`, `openai_base_url=https://gateway.corp/v1`, `.env OPENAI_API_KEY=<PAT>`(또는 `LLM_API_KEY`), 헤더 형식 `openai_api_key_header` — §4.1 | `models test --live` |
| **사내 게이트웨이 (URL + PAT), Anthropic-compatible** | `llm_provider=anthropic`, `anthropic_base_url=https://gateway.corp`, `.env ANTHROPIC_AUTH_TOKEN=<PAT>`(Bearer) 또는 `ANTHROPIC_API_KEY`(x-api-key), `llm_fallbacks=false` — §4.2 | `models test --live` |
| Anthropic 직접 | `.env ANTHROPIC_API_KEY`, `llm_provider=auto|anthropic` | `models test` |
| OpenAI-compatible 로컬 (vLLM · LM Studio · Ollama /v1) | `openai_base_url` (…/v1), 키는 비워도 됨, `llm_provider=openai`, 모델 id | `models test` (`/v1/models`) |
| Ollama | `ollama_url`, `ollama_model`, `ollama pull <model>` | `models test` — 모델이 없으면 unavailable 로 표시되고 auto 는 선택하지 않음 |
| **Headless 에이전트 (opencode 등)** | `agents.json` 의 command 템플릿, `llm_roles.<role>.provider = "headless:opencode"`, 실행 파일 PATH — §4.3 | `models test --live` (실제로 프로세스를 띄워 응답 확인), `headless:mock` 으로 배선 확인 |
| rerank API | `rerank_url`, `rerank_api_model`, `rerank_api_style`, `.env RERANK_API_KEY`; 튜닝 `rerank_method=auto|api` | `models test` (`rerank_api` 항목) |
| 임베딩 API | Voyage(`VOYAGE_API_KEY`), OpenAI-compat(`openai_embed_model`, 필요 시 `openai_embed_base_url`·`OPENAI_EMBED_API_KEY`), Ollama(`embed_model=bge-m3`) | `models test` (`embedder`) |
| 외부 MCP | `mcp_sources.json` (command/env/tool 매핑), 토글 `mcp_sources` | `mcp-source test`, `mcp-source ingest --dry-run` |

전부 없어도 동작한다: 추출식 답변 + 규칙 그래프 + hash 임베딩 + 로컬 리랭크 (`preset apply offline`).

공통 주의:
- `llm_provider=auto` 는 Anthropic 키 → Ollama 순으로만 고르며 **openai / headless 는 절대 고르지 않는다**. 게이트웨이나 opencode 를 쓰려면 `llm_provider` 또는 `llm_roles.<role>.provider` 에 명시한다.
- `models test` 는 토큰을 쓰지 않는 ping(모델 목록 조회)만 한다. 게이트웨이가 `/models` 를 막아 두었거나 PAT 권한·헤더 이름·모델 id 가 틀린 경우는 **`models test --live`** (역할별 provider/model 당 실제 완성 호출 1회, "OK" 한 단어 응답)로만 드러난다. Web 은 Settings › 모델 › "실제 호출 테스트 (--live)".
- LLM 호출 1회의 HTTP 타임아웃은 `llm_timeout`(기본 600초). 게이트웨이가 멈춰도 빨리 실패하게 하려면 120~180 으로 줄인다. 진행 중인 호출은 CLI 의 `⏳ … LLM 응답 대기 <provider>/<model> Ns` 줄과 Web 의 진행 패널에서 보인다.
- 설정 후 순서: `python setup/check_env.py`(키·URL·실행 파일 존재) → `models test --live` → `health` → `build`.

### 4.1 OpenAI-compatible 게이트웨이 + PAT

게이트웨이가 `/v1/chat/completions`(+ `/v1/embeddings`) 를 제공하고 PAT(Personal Access Token)로 인증하는 경우. 예시 파일: `setup/config.example.pat-gateway.json`.

```jsonc
// config.json (해당 키만)
"llm_provider": "openai",
"llm_model": "gpt-4o-mini",                      // 게이트웨이가 노출하는 모델 id 그대로
"openai_base_url": "https://gateway.corp/v1",    // …/v1 까지. 경로 뒤에 /chat/completions 가 붙는다
"openai_api_key_header": "authorization",        // PAT 를 어떤 헤더에 싣나 (아래 표)
"openai_extra_headers": {},                      // 게이트웨이가 요구하는 고정 헤더 {"X-Tenant": "modem"}
"embed_provider": "openai", "openai_embed_model": "text-embedding-3-small",   // 임베딩도 게이트웨이로 보낼 때
"llm_timeout": 180
```
```ini
# .env
OPENAI_API_KEY=<PAT>          # 또는 LLM_API_KEY=<PAT> (이름만 다름). 임베딩 키가 다르면 OPENAI_EMBED_API_KEY
```

| 게이트웨이가 요구하는 인증 | `openai_api_key_header` | 실제로 나가는 헤더 |
|---|---|---|
| `Authorization: Bearer <PAT>` (대부분) | `authorization` (기본) | `authorization: Bearer <PAT>` |
| `api-key: <PAT>` (Azure OpenAI 스타일) | `api-key` | `api-key: <PAT>` |
| `X-API-Key: <PAT>` | `x-api-key` | `x-api-key: <PAT>` |
| 그 외 이름 | 그 헤더 이름 | `<이름>: <PAT>` (값 그대로) |

확인용 curl (설정과 같은 요청):
```bash
curl -s https://gateway.corp/v1/chat/completions -H "authorization: Bearer $PAT" -H "content-type: application/json" \
  -d '{"model":"gpt-4o-mini","max_tokens":8,"messages":[{"role":"user","content":"ping"}]}'
```
이 curl 이 되면 `python -m llmwiki models test --live` 도 된다. 401/403 이면 PAT 또는 헤더 이름, 404 면 `openai_base_url`(…/v1 누락) 또는 모델 id, 400 에 `max_tokens`/`temperature` 가 언급되면 게이트웨이가 신형 파라미터만 받는 경우이니 게이트웨이 담당자에게 호환 모드를 요청한다(`response_format` 미지원은 자동으로 재시도한다).

### 4.2 Anthropic-compatible 게이트웨이 + PAT

게이트웨이가 Anthropic Messages API(`/v1/messages`)를 제공하는 경우.
```jsonc
"llm_provider": "anthropic",
"llm_model": "claude-sonnet-5",
"anthropic_base_url": "https://gateway.corp",   // /v1 없이 호스트까지. /v1/messages, /v1/models 가 뒤에 붙는다
"llm_fallbacks": false                          // 게이트웨이가 anthropic-beta 헤더를 거부하면 false
```
```ini
ANTHROPIC_AUTH_TOKEN=<PAT>    # Bearer 방식 (authorization: Bearer <PAT>)
# 또는 ANTHROPIC_API_KEY=<PAT>  # x-api-key 방식
```
`anthropic` SDK 가 설치돼 있으면 SDK 로, 없으면 내장 urllib 구현으로 같은 URL/헤더를 쓴다(둘 다 `anthropic_base_url` 을 따른다). 게이트웨이가 `/v1/models` 를 제공하지 않으면 ping 은 "models 목록 미제공" 으로 통과 처리되고 `--live` 로 실제 호출을 확인한다.

### 4.3 opencode 를 headless 로 쓰기

opencode(또는 claude / codex CLI)를 비대화형 subprocess 로 실행해 LLM 역할로 쓴다. 예시 파일: `setup/config.example.headless.json`.

1. opencode 설치 + 인증(`opencode auth login` 등)을 **서버를 실행할 계정으로** 마친다. 실행에 필요한 환경변수는 `.env` 에 두면 subprocess 에 그대로 전달되고, `agents.json` 의 `opencode.env` 로도 줄 수 있다.
2. `config.json`:
   ```jsonc
   "llm_roles": {
     "answer": {"provider": "headless:opencode", "model": "anthropic/claude-sonnet-4-5"},   // model 은 opencode 의 provider/model 표기
     "expand": {"provider": "headless:opencode", "model": "anthropic/claude-haiku-4-5"},
     "verify": {"provider": "headless:opencode", "model": "anthropic/claude-haiku-4-5"}
   }
   ```
   전역 `llm_provider` 를 `headless:opencode` 로 두면 빌드 역할(extract/summary)까지 모두 프로세스 실행이 되어 매우 느리므로, **질의 역할에만** 두는 것을 권장한다(`llm_graph` 는 청크당 프로세스 1개).
3. `agents.json` 의 `opencode` 항목이 명령 템플릿이다: `["opencode", "run", "--format", "json", "-m", "{model}", "{prompt}"]`, 출력 `ndjson`, `timeout_s` 300. 설치된 opencode 버전이 다른 플래그/출력을 쓰면 여기만 고친다(출력이 일반 텍스트면 `"output": "text"`).
4. **Windows**: npm/bun 으로 설치한 opencode 는 `opencode.cmd` 셸 스크립트다. `command[0]` 은 PATH 에서 `.cmd/.bat` 까지 찾아 절대 경로로 실행하므로 그대로 두면 되고, 안 찾히면 `"C:\\Users\\<me>\\AppData\\Roaming\\npm\\opencode.cmd"` 처럼 절대 경로를 적는다. (예전 버전은 ping 은 통과하는데 실제 호출이 `WinError 2` 로 실패했다 — 수정됨.)
5. 확인: `python -m llmwiki models test --live` → `answer headless:opencode/… live: OK reply='OK'`. 배선만 먼저 보려면 `headless:mock`.

`agents.json` 은 `{python}`(현재 인터프리터)·`{project_root}` 치환을 쓰므로 다른 PC 로 복사해도 그대로 동작한다. opencode 의 로컬 HTTP 서버(`opencode serve`) 는 OpenAI-compatible 이 아니라 현재 지원하지 않는다 — 필요하면 앞에 OpenAI 호환 shim 을 두거나 §11 의 방법으로 30줄짜리 프로바이더를 추가한다.

### 4.4 서버를 여러 사람에게 공개하기 전: 로그인·역할·파괴적 작업 보호

상세 설계와 `security.json` 전체 항목은 [SECURITY.md](SECURITY.md). 최소 절차:
```bat
python -m llmwiki security init                         :: security.json 생성 (mode auto: 127.0.0.1 은 로그인 없음, 그 외 바인드는 로그인 필수)
python -m llmwiki users add alice --role admin          :: 관리자 1명 이상 (비밀번호 프롬프트)
python -m llmwiki users add bob --role operator         :: 증분 빌드·제안 적용 등 복구 가능한 변경까지
python -m llmwiki serve --host 0.0.0.0 --port 8765      :: 사용자/SSO 가 없으면 기동 거부
```
- 사내 SSO 병행: `security.json` 의 `sso` 에 OIDC(issuer/client_id/redirect_uri, 비밀은 `.env LLMWIKI_OIDC_CLIENT_SECRET`) 또는 리버스 프록시 헤더 방식을 적고 `role_map` 으로 IdP 그룹 → 역할. 로그인 화면에 ID/비밀번호 폼과 SSO 버튼이 함께 뜬다.
- 정책: 증분 빌드 등 복구 가능한 변경은 확인 대화상자, **전체 초기화·로그 삭제·스냅샷 복원·config reset 은 admin + 확인 문구(`DELETE INDEX`) + 비밀번호 재입력**, 실행 직전 자동 스냅샷(`snapshot list|restore`), 전부 `logs/audit.jsonl` 에 기록.
- HTTPS 와 IP 제한은 리버스 프록시에서(SECURITY.md §7).

## 5. 코퍼스 계약 적용

1. `python -m llmwiki corpus types` 로 유형과 필수 필드를 확인하고, 조직 문서 유형이 다르면 `schemas/<type>.json` 을 추가/수정한다.
2. 기존 5,000개 문서에 front matter 를 한 번에 붙일 수 없다면 `schemas/infer.json` 의 경로/파일명 규칙을 조직 폴더 구조에 맞춘다(`*/issues/*` → issue 등). 추론된 문서는 lint 에 warn 으로 표시된다.
3. 문서 ID 체계가 다르면(`ISSUE-` 가 아니라 `JIRA-`, `MR-` 등) `data/rules.json → id_patterns` 의 regex/canonical 과 `schemas/<type>.json → id_pattern`, `schemas/infer.json → id_from_text` 를 함께 바꾼다. 관계 이름은 `link_rules`, front matter 키 매핑은 `explicit_rels`.
4. `corpus lint-file <파일>` 로 샘플 문서를 검사하고, 생성 도구(사람/LLM)에는 `corpus example <type>` 출력을 템플릿으로 준다.
5. 도메인 용어를 `query_rules.json` 에 넣는다 (`rules add acronym PDCCH "Physical Downlink Control Channel"`). 복합어는 `compound` 에.

## 6. 첫 빌드와 검증

```bat
python -m llmwiki health                 :: 프로바이더 ping 포함. FAIL 항목이 있으면 build 가 시작되지 않음
python -m llmwiki build --full --trace   :: 초기화 → 색인 → 임베딩(재개/캐시/적응형 배치) → 그래프(explicit/rule/cooccur) → 위키 → prune → verify
python -m llmwiki build verify           :: 정합성 (FTS↔청크, coverage, 댕글링, 고아, 위키 stale) — 문제 시 build verify --fix
python -m llmwiki embed report           :: coverage 100% 인지, 실패 청크 없는지
python -m llmwiki corpus lint            :: 스키마 오류 목록
python -m llmwiki graph --provenance explicit --limit 20   :: CL→Issue 관계가 생겼는지
```
빌드가 중단되면(네트워크·API 장애) 그냥 다시 `build` 하면 된다. 남은 청크만 임베딩하고(`resume_missing`), 진행률은 `build status` / Web › Corpus › 임베딩.

## 7. 평가 기준선과 튜닝

1. `eval/questions.json` 을 자기 코퍼스 질문(기대 문서 id 부분 문자열 + 기대 용어) 25~50개로 교체.
2. `trial run --name baseline` → `trial run --name t1 --set fusion_method=zscore --set top_k_final=10` → `trial compare baseline t1`.
   지표: hit@k, MRR, term_recall, groundedness, citation_precision, insufficient_rate, fallback_rate, p95_ms, tokens/query, embed_coverage.
3. 프리셋 비교: `trial run --name q --preset quality`, `trial run --name s --preset speed`.
4. 융합 방식: `fusion compare`. 채널 가중: 튜닝 `channel_w_*`, 문서 유형 부스트 `doc_type_boost`, 시간 `time_mode/time_boost_w`, provenance `provenance_w`.
5. Web › Quality › Trial 비교에서 질문별 승/패, 설정 diff, 추천을 본다.

## 8. 스케줄 (매일 증분)

권장: OS 스케줄러가 CLI 를 호출. 파일 락으로 서버 워처와 충돌하지 않는다.
```powershell
.\setup\schedule_build.ps1 -Register -Time 02:30    # Windows 작업 스케줄러 (현재 사용자)
```
```bash
30 2 * * * /path/setup/schedule_build.sh >> /path/logs/cron.log 2>&1    # cron
```
개발 중 "저장 즉시 반영" 이 필요하면 `serve` 의 워처(`auto_build` 토글)나 `watch --interval 300` 을 보조로 쓴다.
삭제/이름 변경도 추적된다: 삭제된 문서의 청크·FTS·임베딩·멘션·관계·위키 페이지가 정리되고, rename 은 임베딩 캐시로 재임베딩 없이 재색인된다.

## 9. 운영

| 할 일 | 방법 |
|---|---|
| Web UI | `python -m llmwiki serve --port 8765` → Ask / Corpus / Knowledge / Quality / Evolve / Settings / Observability. 테마는 헤더 셀렉터(light/dark/high-contrast/solarized, `themes/` 에 CSS 추가로 확장) |
| MCP 로 노출 | `claude mcp add llmwiki -- python -m llmwiki mcp` (cwd = 프로젝트 루트). 도구: `wiki_query(mode, doc_types, preset)`, `wiki_search`, `wiki_related`, `wiki_doc`, `wiki_entity`, `wiki_propose`(HITL 제안), `wiki_status` |
| 로그 | `logs/` (llmwiki.log · error.log · build.log · query.log, JSON Lines, 로테이션). `logs grep --request <id>` 로 프로파일과 연결 |
| 프로파일 | `requests last`, Web › Observability › 요청 프로파일 (run_id → 로그) |
| 근거 부족/품질 문제 | `forensic last`, `forensic summary`, Web › Quality › 포렌식. 누적 소견은 `memory consolidate` 로 제안(corpus_gap/query_rule/tuning) 생성 |
| 자가진화 | `evolve status/apply/reject`, Web › Evolve. `evolve_auto_apply` 는 기본 OFF(HITL). tuning 제안은 자동 적용 대상 아님 |
| 메모리 decay | `memory decay` (반감기 `memory_half_life_days`) — 스케줄에 주 1회 넣어도 됨 |
| 캐시 | `precompute run` (평가셋+빈번 질의 사전 계산), 재빌드 시 자동 무효화 |
| 유지보수 | `maintenance vacuum|fts_optimize|wal_checkpoint`, `embed clear-cache`(캐시 초기화) |

## 10. 문제 해결

| 증상 | 원인 / 조치 |
|---|---|
| `build aborted: health check failed: corpus_dirs` | 코퍼스 경로 없음. `config set corpus_dirs=경로`. 초기화 전에 검사하므로 기존 색인은 유지됨 |
| `build refused: another build is running` | 다른 프로세스가 빌드 중(락). 죽은 프로세스의 락은 자동 회수. `build status` 로 pid 확인 |
| 답변 맨 위에 `ℹ LLM 미사용 — 근거 문서의 원문 문장을 골라 구조화한 답변` | LLM 없음/모델 미설치. 근거 표·관계는 나오지만 서술형 설명은 없음. `models test --live`. Ollama 는 `ollama pull <model>` |
| `models test` 는 OK 인데 실제 질의는 LLM 미사용/오류 | ping(모델 목록)만 통과한 것. `models test --live` 로 실제 호출 — 401/403 = PAT·헤더(`openai_api_key_header`), 404 = base_url/모델 id, headless 는 실행 파일·인증 |
| `llm_provider=auto` 인데 게이트웨이/opencode 가 안 잡힘 | auto 는 openai/headless 를 고르지 않는다. `llm_provider=openai` 또는 `llm_roles.<role>.provider=headless:opencode` 로 명시 |
| headless: `executable not found` / `WinError 2` | PATH 에 없음. `agents.json` `command[0]` 에 절대 경로(Windows 는 `...\npm\opencode.cmd`) |
| 빌드/질의가 오래 걸리는데 멈춘 건지 모르겠다 | CLI 는 `⏳ 단계 › 진도 · LLM 응답 대기 Ns` 줄, Web 은 진행 패널(단계·%·LLM 대기 시간·최근 로그)을 본다. LLM 대기가 `llm_timeout` 을 넘으면 실패로 기록되고 다음 청크로 넘어간다 |
| 답변이 "근거 부족(insufficient)" | 코퍼스에 없거나 표기 불일치. `forensic last` → `rules add synonym …` / 문서 추가 / `fallback_loop` 토글 |
| `[미확인: 근거에서 확인되지 않음]` 표기 | claim_check 가 인용 근거와 대조해 지지되지 않는 문장. `claim_policy`(mark/drop/refine), `answer_refine` |
| coverage < 100% | 임베딩 실패(429/타임아웃). `embed report` 로 실패 청크 확인 → 다음 `build` 에서 자동 재개 |
| `dim mismatch` / `embedding_dim` warn | 임베더/차원 변경 → `build --full` |
| 시간 질의가 안 맞음 | `timezone`, `week_start` 확인, `time "지난주"` 로 파싱 확인. 문서 `date` 가 없으면 파일명/mtime 추론 |
| 그래프에 CL→Issue 가 없음 | CL 문서 `related.issues` 누락(lint error) 또는 `id_patterns` 불일치 |
| 한글 검색 recall 낮음 | `query_rules.json` compound/synonym 추가, `tuning set tokenizer=kiwi`(+`pip install kiwipiepy`) 후 `build --full`, `fts_trigram` 토글 |
| Web UI 가 옛 화면 | 브라우저 캐시 — 새로고침(Ctrl+F5). 정적 파일은 `Cache-Control: no-store` |

## 11. 포팅 시 코드 변경이 필요한 곳 (없어야 정상)

설정·규칙·프롬프트·스키마는 모두 파일로 외부화되어 있으므로 코드를 고치지 않아도 된다. 새 프로바이더(예: 사내 전용 API)를 붙일 때만 `llmwiki/providers.py` 에 `BaseLLM`/`BaseEmbedder` 서브클래스를 추가하고 `_make_llm`/`make_embedder` 에 이름을 등록한다(30줄 내외). 새 검색 채널은 `query_engine._retrieve` 의 `lists[...]` 에 리스트를 추가하면 융합·부스트·프로파일에 자동으로 포함된다.
