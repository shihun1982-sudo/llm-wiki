# HEADLESS — CLI 에이전트(opencode · claude · codex)를 LLM 프로바이더로: 전환 · 프롬프트 전달 · 격리 · WinError 206

> 설정: `agents.json`(에이전트 템플릿, 예제 `setup/agents.example.json`) + `config.json` 의 `llm_provider` / `llm_roles.<role>.provider` (예제 `setup/config.example.headless.json`)
> 코드: `llmwiki/headless.py` · 확인: `python -m llmwiki health`(headless_agents) · `python -m llmwiki models test --live`
> 설계 근거: [IMPLEMENTATION_PLAN_0918.md §2.8](history/2026-09-18/IMPLEMENTATION_PLAN_0918.md) · [IMPLEMENTATION_PLAN_0918_2.md §2.1](history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md) · 검증: `tests/test_headless_switch.py`
> 설치·경로 문제(opencode.cmd, PATH)는 [BRINGUP_GUIDE.md §4.3](BRINGUP_GUIDE.md) 이 다룬다. 이 문서는 그 뒤의 운영 규칙이다.

## 0. 한 장 요약

```
config.json  llm_roles.answer.provider = "headless:opencode"   ← API ↔ headless 전환은 이 한 줄 (전역은 llm_provider)
agents.json  opencode = { command: ["opencode","run","--format","json","-m","{model}","{prompt}"],
                          prompt_mode: "stdin", arg_max_chars: 30000, env_passthrough: [...], timeout_s: 300, retries: 3, ... }
질의 → HeadlessAgentLLM._complete(): 프롬프트(system+user[+첨부 인라인]) → prompt_mode 결정 → subprocess(스트리밍 감시) → ndjson/json/text 파싱 → text/usage
```

| 항목 | 값 |
|---|---|
| 전환 | `config.json` 만 고친다: 전체 `llm_provider: "headless:<agent>"`, 역할 하나만 `llm_roles.<role>.provider`. 되돌릴 때도 같은 키. 종단 테스트 `ConfigSwitchTest` 가 mock 에이전트 ↔ mock API 왕복을 고정 |
| 카탈로그 자동 해석 | provider 를 비우고 `model` 만 적어도 `models.json` 에 그 id 가 **한 provider** 로만 있으면 그 provider 를 쓴다(`provider_source=catalog`). 예 `"model": "anthropic/claude-sonnet-4-5"` → `headless:opencode` |
| 프롬프트 전달 | `prompt_mode` 기본 **`stdin`**(코드·파일 모두). `arg` 는 argv 총 길이가 `arg_max_chars`(30000) 를 넘으면 자동으로 `stdin`(템플릿에 `{prompt_file}` 이 있으면 `file`)으로 전환 + `prompt_mode_fallback` 표시 |
| 자식 환경 | `env_passthrough` 허용 목록에 맞는 변수 + `env` 만 넘긴다. `.env` 의 PAT·`LLMWIKI_PASSWORD` 는 기본으로 넘어가지 않는다 |
| 목업 | `headless:mock` = `python -m llmwiki.headless --mock` — 네트워크 없이 배선·재시도·무응답·arg 전환을 재현 |
| 확인 | `health`(실행 파일 존재) → `models test`(ping = 실행 파일만) → `models test --live`(실제 완성 1회) → `models test --catalog [--live]`(카탈로그 전체) |

## 1. 진단 — `OSError: [WinError 206] The filename or extension is too long`

| 항목 | 내용 |
|---|---|
| 증상 | 2026-09-16 요청(req_2794): opencode 답변 단계에서 `WinError 206`, context_chars 39,719 |
| 원인 | Windows `CreateProcess` 의 **명령줄 32,767자 한계**. 당시 agents.json 항목이 `"{prompt}"` 인자를 두고 `prompt_mode` 키가 없어 코드 기본값 `arg` 를 탔다 → 시스템 프롬프트+컨텍스트 39,719자가 argv 로 들어갔다 |
| 남아 있던 구멍 | (a) `_render/_complete` 의 코드 기본값이 `arg` 라 agents.json 을 거치지 않은 dict 항목은 arg 를 탔다 (b) 운영자가 `arg` 를 고르면 긴 프롬프트에서 원인 문구 없이 죽었다 (c) stdin 을 못 받는 에이전트 안내가 없었다 |
| 왜 stdin 이 기본인가 | 길이 한계 없음 · 프로세스 목록에 프롬프트(코퍼스 발췌)가 노출되지 않음 · 임시 파일 정리 불필요. opencode run / claude -p / codex exec 모두 파이프 입력을 프롬프트로 받는다. `file` 은 stdin 을 못 받는 에이전트의 차선, `arg` 는 짧은 프롬프트 전용 |

조치 3건(`headless.py`): ① `_prompt_mode()` — 코드 기본값도 `stdin`, `_render(tpl, prompt, prompt_file, mode)` 가 모드를 인자로 받음 ② `arg_max_chars` 가드 — argv 총 길이(각 인자 길이+1 의 합)가 넘으면 자동 전환, 결과 `prompt_mode`/`prompt_mode_fallback="arg→stdin (N chars > arg_max_chars=30000)"`, **`usage.prompt_mode_fallback`** 에도 같은 문자열(질의 단계는 `usage` 만 trace meta 로 옮기므로 이것이 trace 에 보이는 유일한 통로: `trace.children[answer_llm].meta.usage.prompt_mode_fallback`), warning 로그 `headless prompt_mode fallback: …`(logger `llm`, hint 포함) ③ 목업 `--prompt-arg TEXT` 옵션 + §5 의 `file` 모드 예시.

첨부가 있고 `files_flag` 가 없는 에이전트는 모드와 무관하게 첨부를 프롬프트 본문 끝에 인라인(`inline_attach_chars` 까지)한 **뒤** 모드를 정한다 — 길이 판정에 첨부가 포함되고 전환 후 본문에도 들어간다. 로그 argv 의 `_mask_argv` 는 프롬프트가 더 긴 인자 안에 있어도 `<prompt:N chars>` 로 가린다.

## 2. `agents.json` 키 (항목마다 명시 — `save_agents`/`load_agents` 의 `with_defaults` 가 빠진 키를 채운다)

| 키 | 기본(`RETRY_DEFAULTS` / `DEFAULT_AGENTS`) | 뜻 |
|---|---|---|
| `command` | 에이전트별 | 템플릿. 치환 `{model}` `{prompt}` `{prompt_file}` `{project_root}` `{python}`. `command[0]` 은 `shutil.which` 로 해석(Windows `.cmd` 포함), 없으면 절대 경로 |
| `prompt_mode` | `"stdin"` | `stdin`(파이프, 권장) \| `arg`(`{prompt}` 인자 — 노출·32K 한계) \| `file`(임시 파일 경로를 `{prompt_file}` 에) |
| `arg_max_chars` | `30000` | `arg` 모드 가드. argv 총 길이가 넘으면 `stdin`(템플릿에 `{prompt_file}` 있으면 `file`)으로 자동 전환. `0` = 끔(넘치면 `LLMError kind=exec`, Windows 에서 WinError 206) |
| `files_flag` | 에이전트별 (`-f` · `""` · `--file`) | 첨부 파일마다 `<flag> <path>`. 비우면 첨부를 프롬프트에 인라인 |
| `output` · `text_paths` · `usage_paths` | 에이전트별 | 출력 파싱: `ndjson` \| `json` \| `text`, 텍스트/토큰을 뽑을 점 표기 경로 |
| `model` | 에이전트별 (`""`, claude 는 `claude-sonnet-5`) | 역할 `model` 이 비었을 때의 기본 |
| `timeout_s` · `retries` · `retry_backoff_s` · `retry_on` | `300` · `3` · `5` · `[timeout, exec, exit, empty, stall]` | 1회 실행 전체 제한 · 재시도 횟수(최대 1+retries 회) · 대기(초)×시도 번호 · 재시도 사유. `with_defaults` 는 구 파일의 `retry_on` 에 `stall` 을 추가한다(`"-stall"` 로 명시적으로 끔). agents.json 값이 config.json `llm_timeout/llm_retries` 보다 우선하되 `llm_roles.<role>.timeout_s/retries` 가 명시되면 그것이 덮는다 |
| `stall_timeout_s` · `first_output_timeout_s` | `60` · `120` | 마지막 출력 뒤 이만큼 조용하면 죽이고 재시도(바이트 단위 감지) · 첫 출력까지의 대기(0 = stall 값) |
| `keep_partial_on_timeout` · `failure_log_chars` | `true` · `2000` | 멎기 전 파서가 찾은 텍스트가 있으면 답으로 사용(`partial=true`) · 실패 로그의 stdout/stderr 꼬리 길이 |
| `env_passthrough` | `["PATH","HOME","USERPROFILE","APPDATA","LOCALAPPDATA","TEMP","TMP","SYSTEMROOT","COMSPEC","LANG","LC_ALL","PYTHONIOENCODING","HTTP_PROXY","HTTPS_PROXY","NO_PROXY"]` | 자식에게 넘길 환경변수 **허용 목록**(fnmatch, 대소문자 무시). `"*"` = 전부(예전 동작, 안전하지 않음). 명시된 `[]` 는 존중된다(에이전트 `env` 만 넘김). 계획 §2.8 의 목록(`LC_*` 등)과 다르다 — 코드가 기준 |
| `env` | `{}` | 허용 목록과 별개로 항상 넘기는 값 (예 `{"ANTHROPIC_API_KEY": "…"}`) |
| `inline_attach_chars` | `60000` | `files_flag` 가 없을 때 첨부 파일당 인라인 상한 |
| `cwd` · `max_output_chars` | `"{project_root}"` · `400000`(mock 100000) | 작업 폴더 · stdout 상한 |

기본 항목 4개: `opencode`(ndjson, `-f`) · `claude`(json) · `codex`(ndjson) · `mock`(`{python} -m llmwiki.headless --mock`, 항상 제공). 저장소의 `agents.json`·`setup/agents.example.json` 은 4개 항목 모두 위 키를 명시하고 있다(`prompt_mode=stdin`, `arg_max_chars=30000`).
기존 파일에 기본값을 채우려면 `python -c "from llmwiki import headless as hl; hl.save_agents(hl.load_agents())"` (추가만 되고 값은 바뀌지 않는다).

## 3. `config.json` 만으로 API ↔ headless 전환

| 바꾸는 것 | 키 | 예 |
|---|---|---|
| 전 역할 | `llm_provider` (+ `llm_model`) | `"llm_provider": "headless:opencode", "llm_model": "anthropic/claude-sonnet-4-5"` |
| 역할 하나 | `llm_roles.<role>.provider` (+ `.model`) | `"llm_roles": {"answer": {"provider": "headless:opencode", "model": "anthropic/claude-sonnet-4-5"}}` |
| model 만 | `llm_roles.<role>.model` (provider 비움) | 카탈로그(`models.json`)에 그 id 가 한 provider 로만 등록돼 있으면 그 provider. 전역 `llm_provider` 가 `mock`/`none` 이면 카탈로그 해석을 하지 않는다(테스트·오프라인 보호) |
| 되돌리기 | 같은 키를 `openai`/`anthropic` 등으로 | `setup/config.example.headless.json` 의 `_example_back_to_api` |

적용: 파일 저장 → `python -m llmwiki config reload`(Web 설정 › 다시 읽기). `Pipeline.reload()` 가 `_llm_key`(역할 해석 결과 서명)를 다시 계산해 새 인스턴스를 만든다.
`models test` 는 provider/model 짝이 카탈로그와 어긋나면 `↳` 힌트 한 줄을 붙인다(`_catalog_hint`: "provider 가 비어 있어 카탈로그의 provider … 를 사용했습니다" / "카탈로그에는 … 이 provider … 로 등록되어 있습니다").
`setup/config.example.headless.json` 은 answer/expand/verify 만 opencode 로 두고 빌드 역할(extract/summary)은 `none` 으로 두는 권장 배치다(`llm_graph` 는 청크마다 프로세스 1개라 매우 느리다).

## 4. 검증 명령

```powershell
python -m unittest tests.test_headless_switch -v
#   ArgGuardTest: defaults_are_explicit · arg_mode_long_prompt_falls_back_to_stdin · …_to_file_when_template_has_prompt_file
#                 · arg_max_chars_zero_disables_guard · arg_max_chars_zero_long_prompt_reproduces_winerror_206(Windows) · mask_argv_hides_inlined_prompt · stdin_mode_unchanged
#   ConfigSwitchTest: switch_headless_and_api_by_config_only   (총 8건 — 임시 LLMWIKI_AGENTS_PATH·임시 config.json 만 사용)
python -m unittest -k headless tests.test_providers tests.test_features_0914 -v
python -m llmwiki health                       # headless_agents: 실행 파일 존재 (fail 등급)
python -m llmwiki models test                  # ping = 실행 파일만 확인 + 카탈로그 힌트
python -m llmwiki models test --live           # 실제 완성 1회 (stdin 프롬프트로 응답하는지)
python -m llmwiki models test --catalog --live # models.json 의 enabled 모델 전부 (headless 항목 포함)
python -m llmwiki config set answer_provider=headless:mock; python -m llmwiki query "테스트" --json --no-log | Select-String prompt_mode
```

## 5. 문제 해결

| 증상 | 원인 | 조치 |
|---|---|---|
| `OSError: [WinError 206]` / `LLMError … exec failed` (Windows) | `prompt_mode=arg` + 긴 프롬프트 + `arg_max_chars=0` | `prompt_mode` 를 `stdin`(권장)으로, 또는 `arg_max_chars` 를 기본 30000 으로 |
| 로그에 `headless prompt_mode fallback: arg→stdin (…)` 이 자주 보인다 | `arg` 를 골랐고 프롬프트가 늘 가드를 넘는다 | 동작은 정상(자동 전환). 잡음을 없애려면 `prompt_mode=stdin` |
| 에이전트가 stdin 을 받지 않아 빈 출력 / `empty` 재시도 | 일부 CLI 는 파이프 입력을 프롬프트로 보지 않는다 | `file` 모드: `"command": ["opencode","run","-m","{model}","-f","{prompt_file}","첨부 파일의 지시를 따르라"], "prompt_mode": "file"` → `models test --live` 로 확인. (`arg` 를 유지하면서 템플릿에 `{prompt_file}` 을 두면 길이 초과 시 `file` 로 전환된다) |
| trace 에 `usage.prompt_mode_fallback` 이 있다 | 그 호출이 arg 에서 전환됐다 | 정보 표시. 프롬프트 전문이 stdin/파일로 전달됐으므로 품질 영향 없음 |
| `health` 의 `headless_agents` FAIL | 실행 파일이 PATH 에 없음(서버 실행 계정 기준) | `agents.json command[0]` 에 절대 경로(Windows `…\npm\opencode.cmd`), 또는 provider 를 API 로 |
| 에이전트가 키를 못 찾는다(`auth` 오류) | `env_passthrough` 가 그 변수를 막았다 | `env_passthrough` 에 패턴(`"ANTHROPIC_*"`, `"OPENCODE_*"`) 추가 또는 `env` 에 값. `--dump-env` 목업으로 넘어가는 이름 확인 |
| `stall` 로 실패한다 | 출력 없이 오래 매달림 | `stall_timeout_s`/`first_output_timeout_s` 조정. 줄바꿈 없는 긴 출력은 바이트 단위 감지라 오탐하지 않는다 |
| `models test` 는 OK 인데 질의가 실패 | ping 은 실행 파일만 본다 | `models test --live` 로 실제 호출. 실패 로그(`error.log`, `headless agent <kind>`)의 `stdout_tail/stderr_tail` 확인 |
| 결과에 `partial=true`, `note: … 중단했지만 그때까지 받은 답을 사용` | timeout/stall 전 부분 출력 사용 | `keep_partial_on_timeout=false` 면 실패로 처리해 재시도 |

## 6. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/headless.py` | `ENV_PASSTHROUGH_DEFAULT` · `RETRY_DEFAULTS` · `DEFAULT_AGENTS` · `with_defaults()`/`load_agents()`/`save_agents()` · `HeadlessAgentLLM`(`_exe`, `_child_env`, `_mask_argv`, `_prompt_mode`, `_render`, `_run_streaming`, `_complete`, `_log_failure`, `ping`) · `_mock_main()`(`--sleep --fail-times --empty --stall --partial --fail --dump-env --prompt-file --long-line --prompt-arg`) |
| `llmwiki/config.py` | `Settings._resolve_provider()`(카탈로그 자동 해석) · `role_llm()` |
| `llmwiki/models_catalog.py` | `provider_for(model_id)` — enabled LLM 항목에서 provider 가 하나일 때만 |
| `llmwiki/pipeline.py` | `_llm_key()`/`llm_for()`/`reload()` · `test_providers()`(`_catalog_hint`) · `test_catalog()` |
| `llmwiki/providers.py` | `make_llm()` → `_make_llm("headless:<agent>")` · `apply_policy()`(agents.json 값 우선 표식 `_*_fixed`) |
| `llmwiki/health.py` | `headless_agents` 항목 |
| `llmwiki/cli.py` | `models test [--live] [--catalog]` · `config reload` |
| `setup/config.example.headless.json` · `setup/agents.example.json` | 전환 예제 · 에이전트 템플릿 예제 |
| `tests/test_headless_switch.py` | 8건 (§4) |
