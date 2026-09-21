# LLM_CONNECT — LLM 에 닿는 두 가지 방법과 `config.json` 샘플

> 이 시스템은 두 가지로 LLM 에 닿는다.
> **(A) LLM API** — OpenAI 호환/Anthropic 호환 엔드포인트에 HTTP. 사내 게이트웨이 + PAT 가 여기.
> **(B) headless 에이전트** — `opencode` 같은 CLI 를 비대화형 자식 프로세스로 실행.
> 둘 사이 전환은 **`config.json` 두 줄**이고, 코드는 고치지 않는다. 이 문서는 그 샘플과 **증명**이다.
> 에이전트 실행 세부(명령 템플릿·출력 파싱·타임아웃)는 [HEADLESS.md](HEADLESS.md), 키 전체 뜻은 [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md).

## 0. 한눈에 — 무엇이 달라지나

| | (A) LLM API | (B) headless opencode |
|---|---|---|
| `llm_provider` | `"openai"` (호환 게이트웨이 포함) 또는 `"anthropic"` · `"ollama"` | `"headless:opencode"` |
| `llm_model` | 서버가 아는 모델 id (`gpt-4o-mini` · `claude-sonnet-5` · `llama3.1`) | opencode 표기 `provider/model` (`anthropic/claude-sonnet-4-5`) |
| 주소 | `openai_base_url` / `anthropic_base_url` | 없음 (프로세스 실행) |
| 자격증명 | `.env` 의 `OPENAI_API_KEY`(또는 `LLM_API_KEY`) | opencode 자체 인증 (`opencode auth login`) |
| 실행법 정의 | — | `agents.json` 의 `opencode` 항목 |
| 비용 특성 | 호출당 HTTP 1회 | 호출당 **프로세스 1개** — 느리고 무겁다 |

## 1. 샘플 — 바꾸는 키만

아래는 **덮어쓸 키만** 보여 준다. 나머지는 `setup/config.example.json` 기본값 그대로 두면 된다.
(`setup/config.example.pat-gateway.json` · `setup/config.example.headless.json` 은 모든 키가 든 전체 파일이라
"무엇을 바꿔야 하나" 가 잘 안 보인다 — 그래서 이 표를 따로 둔다.)

### 1.1 (A-1) 사내 LLM 게이트웨이 — OpenAI 호환 + PAT

```jsonc
{
  "llm_provider": "openai",
  "llm_model": "claude-sonnet-5",
  "openai_base_url": "https://llm-gw.corp/v1",
  "openai_api_key_header": "authorization",     // 게이트웨이가 x-api-key 면 그렇게
  "openai_extra_headers": {},                    // 고정 헤더가 필요하면 {"X-Tenant":"team-a"}

  "embed_provider": "openai",
  "embed_model": "text-embedding-3-large",
  "openai_embed_base_url": "https://llm-gw.corp/v1",
  "embed_dim": 3072
}
```
```ini
# .env  — PAT 는 여기에만
OPENAI_API_KEY=<PAT>
OPENAI_EMBED_API_KEY=<PAT>
```

### 1.2 (A-2) 게이트웨이가 Anthropic Messages API 일 때

```jsonc
{
  "llm_provider": "anthropic",
  "anthropic_base_url": "https://llm-gw.corp",
  "llm_model": "claude-sonnet-5"
}
```
```ini
ANTHROPIC_API_KEY=<PAT>        # 또는 ANTHROPIC_AUTH_TOKEN
```

### 1.3 (A-3) 로컬 Ollama (오프라인)

```jsonc
{
  "llm_provider": "openai",                       // Ollama 의 OpenAI 호환 엔드포인트
  "openai_base_url": "http://localhost:11434/v1",
  "llm_model": "llama3.1",
  "embed_provider": "ollama",
  "embed_model": "bge-m3",
  "ollama_url": "http://localhost:11434"
}
```
키는 필요 없다.

### 1.4 (B-1) headless opencode — 전부

```jsonc
{
  "llm_provider": "headless:opencode",
  "llm_model": "anthropic/claude-sonnet-4-5",
  "toggles": { "llm_graph": false }               // 빌드는 청크당 프로세스 1개가 된다 — 끈 채로
}
```
`agents.json` 의 `opencode` 항목이 실행법을 정한다 (기본값이 이미 들어 있다):
```jsonc
"opencode": {
  "command": ["opencode", "run", "--format", "json", "-m", "{model}", "{prompt}"],
  "prompt_mode": "stdin", "output": "json", "timeout_s": 300, "retries": 1
}
```

### 1.5 (B-2) 권장 — 질의 역할만 headless, 빌드 역할은 API

빌드 역할(`extract`·`summary`)까지 headless 로 두면 문서 수만큼 프로세스가 뜬다. 역할별로 섞는다.

```jsonc
{
  "llm_provider": "openai",
  "openai_base_url": "https://llm-gw.corp/v1",
  "llm_model": "gpt-4o-mini",

  "llm_roles": {
    "answer": { "provider": "headless:opencode", "model": "anthropic/claude-sonnet-4-5", "timeout_s": 300 },
    "extract": { "provider": "openai", "model": "gpt-4o-mini" },
    "summary": { "provider": "openai", "model": "gpt-4o-mini" }
  }
}
```

### 1.6 되돌리기

```jsonc
{ "llm_provider": "openai", "llm_model": "gpt-4o-mini", "llm_roles": {} }
```

## 2. "코드 수정 없이 된다" 의 증명

말이 아니라 **하네스**로 지킨다.

```bat
python tools/verify/verify_llm_switch.py
```

`tools/verify/verify_llm_switch.py` 가 하는 일 (네트워크 없이 — API 쪽은 `mock` 프로바이더,
headless 쪽은 `agents.json` 의 `mock` 에이전트):

1. API 모드로 **CLI `query` · Web `POST /api/query` · MCP `wiki_query`** 를 모두 돌리고, 세 창구가
   같은 프로바이더를 보고하는지 확인
2. **`config.json` 의 `llm_provider`·`llm_model` 두 키만** 바꿔 headless 로 전환
3. 전환 전후로 **다른 설정 파일과 `llmwiki/*.py` 의 수정 시각이 그대로인지** 확인 (= 코드를 안 고쳤다)
4. 같은 세 창구가 그대로 동작하고 셋 다 headless 를 보고하는지 확인
5. **역할 하나만** headless 로 바꾸는 것(§1.5)도 되는지
6. 되돌리면 원래대로 (단방향이 아니다)

검사 18개. `verify_all.py` 에 들어 있어 전체 검증 때마다 돈다.

실제 게이트웨이·실제 opencode 로 확인하려면:

```bat
python -m llmwiki models test --live           :: 역할별로 실제 1회 호출 (PAT·헤더·모델명·opencode 실행까지)
python -m llmwiki models test --catalog --live :: models.json 의 enabled 모델 전부
```

## 3. 세 창구에서 어떻게 보이나 (정렬)

| | CLI | Web | MCP |
|---|---|---|---|
| 지금 무엇이 붙어 있나 | `models show [--json]` | 설정 › 모델·프로바이더 | `wiki_status` |
| 연결 테스트 | `models test [--live] [--catalog]` | 설정 › 모델 › 연결 테스트 | — (쓰기/비용이라 제외) |
| 바꾸기 | `models set answer_provider=… answer_model=…` | 설정 › 모델·프로바이더 (저장) | — (설정 변경은 사람이) |
| 쓸 수 있는 모델 | `models list` · `models catalog add|remove` | 설정 › 카탈로그 | — |
| 역할별 정책(타임아웃·재시도) | `models policy` | 설정 › 모델 표의 역할 행 | — |
| 자동 배정 | `models automap [--live] [--apply]` | 설정 › 카탈로그 › 자동 매핑 | — |

MCP 에 **변경**을 두지 않는 것은 의도다 — 붙어 있는 LLM 이 자기가 쓰는 모델을 바꾸게 하면 안 된다.
읽기(`wiki_status`)는 열려 있다. 이 표는 `tools/verify/verify_surface_align.py` 가 지킨다.

## 4. 주의할 점

| | 내용 |
|---|---|
| `llm_provider: "auto"` | `openai`·`headless` 를 **고르지 않는다**. 게이트웨이나 opencode 를 쓰려면 반드시 명시한다 |
| headless 와 빌드 | `toggles.llm_graph` 를 켜면 청크마다 프로세스가 뜬다. 끈 채로 두거나 빌드 역할만 API 로 (§1.5) |
| Windows 의 opencode | npm/bun 설치본은 `opencode.cmd` — PATH 에서 자동 해석된다. 안 되면 `agents.json` 의 `command[0]` 에 절대 경로 |
| 자식 프로세스 환경 | `agents.json` 의 `env_passthrough` 허용 목록만 넘어간다 (기본 `PATH`·`HOME`·`USERPROFILE`·`APPDATA`…) |
| 임베딩은 별개다 | `llm_provider` 를 headless 로 바꿔도 임베딩은 `embed_provider` 가 따로 정한다 (headless 임베딩은 없다) |
| 타임아웃 | headless 는 프로세스 실행이라 API 보다 길게 — `agents.json` 의 `timeout_s` 가 역할 기본보다 우선한다 |

## 5. 문제 해결

| 증상 | 확인 |
|---|---|
| 게이트웨이 401/403 | 헤더 이름이 다르다 — `openai_api_key_header` 를 `x-api-key`/`api-key` 로. `models test --live` |
| 모델명을 모른다고 한다 | 게이트웨이가 아는 id 로. `models discover` 가 서버 목록을 물어본다 |
| opencode 가 안 뜬다 | `agents.json` 의 `command[0]` 경로 · `models test --live` 가 실행 오류를 그대로 보여 준다 |
| 빌드가 몇 시간씩 | 빌드 역할이 headless 다 — §1.5 처럼 나누거나 `llm_graph` 끄기 |
| 바꿨는데 안 바뀐다 | 서버는 모듈을 다시 읽지 않는다 — **재시작**. `models show` 로 유효값 확인 |

## 6. 구현 파일

| 파일 | 역할 |
|---|---|
| `llmwiki/providers.py` | 프로바이더 해석(`headless:` 접두 포함) · 재시도 · 회로 차단 |
| `llmwiki/headless.py` | 자식 프로세스 실행 · 출력 파싱 (+ `--mock` 테스트 에이전트) |
| `agents.json` | 에이전트별 명령 템플릿 — [HEADLESS.md](HEADLESS.md) |
| `tools/verify/verify_llm_switch.py` | 이 문서 §2 의 증명 |
| `setup/config.example.pat-gateway.json` · `setup/config.example.headless.json` | 전체 키가 든 원본 예시 |
