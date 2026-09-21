# 2026-09-18 (2차) 단계 A1 — headless WinError 206 · 단위 테스트 2건 수정 (초안)

> 설계: [IMPLEMENTATION_PLAN_0918_2.md §0, §2.1](../IMPLEMENTATION_PLAN_0918_2.md). 이 초안은 문서 병합 단계에서
> [BRINGUP_GUIDE.md §3.2·§4.3](../BRINGUP_GUIDE.md) 과 [README.md](../../README.md) 에 옮긴다. 코드: `llmwiki/headless.py`, 테스트: `tests/test_headless_switch.py`.

## 1. 진단 — `WinError 206 (The filename or extension is too long)`

| 항목 | 내용 |
|---|---|
| 증상 | 2026-09-16 요청(req_2794): headless(opencode) 답변 단계에서 `OSError: [WinError 206]`, context_chars 39,719 |
| 원인 | Windows `CreateProcess` 의 **명령줄 32,767자 한계**. 당시 agents.json 의 opencode 항목이 `"{prompt}"` 를 인자로 두고 `prompt_mode` 키가 없어 **코드 기본값 `arg`** 를 탔다 → 시스템 프롬프트 + 컨텍스트 39,719자가 argv 로 들어가 한계를 넘었다 |
| 왜 파일 기본값만으로 부족했나 | 1차 회차 H 단계가 `with_defaults()` 와 저장소 agents.json 을 `stdin` 으로 바꿔 **같은 파일을 쓰는 한 재발하지 않았지만**, (a) `HeadlessAgentLLM._render/_complete` 의 코드 기본값이 여전히 `arg` 라 agents.json 을 거치지 않고 dict 로 만든 항목은 arg 를 탔고, (b) 운영자가 일부러 `arg` 를 고르면 긴 프롬프트에서 원인 문구 없이 OSError 로 죽었고, (c) stdin 을 못 받는 에이전트에 대한 안내가 없었다 |
| 왜 stdin 이 기본인가 | 길이 한계 없음 · 프로세스 목록에 프롬프트(코퍼스 발췌)가 노출되지 않음 · 임시 파일 정리 불필요. opencode run / claude -p / codex exec 모두 파이프 입력을 프롬프트로 받는다. `file` 은 stdin 을 못 받는 에이전트의 차선 |

## 2. 조치 3건 (`llmwiki/headless.py`)

| # | 구멍 | 조치 |
|---|---|---|
| 1 | 코드 기본값 `arg` | `_prompt_mode()` 를 두고 `_render`/`_complete` 모두 **`stdin`** 을 기본으로. `_render(tpl, prompt, prompt_file, mode="")` 는 모드를 인자로 받는다(전환 시 재렌더링용) |
| 2 | `arg` 에서 긴 프롬프트 → 원인 없는 OSError | 새 키 **`arg_max_chars`**(기본 30000, `RETRY_DEFAULTS` 에 있어 `save_agents` 가 파일에 명시). `arg` 모드에서 argv 총 길이(각 인자 길이+1 의 합)가 이를 넘으면 **자동 전환**: 템플릿에 `{prompt_file}` 이 있으면 `file`, 없으면 `stdin`. 전환하면 (i) `_complete` 결과에 `prompt_mode`(실제 사용 모드) 와 `prompt_mode_fallback="arg→stdin (N chars > arg_max_chars=30000)"`, (ii) **`usage.prompt_mode_fallback`** 에도 같은 문자열, (iii) `logging_setup` warning 1줄(`headless prompt_mode fallback: …`, hint 포함). `0` = 가드 끔 |
| 3 | stdin 을 못 받는 에이전트 | 문서(§5 문제 해결) 에 `file` 모드 예시와 `models test --live` 확인 절차. 목업 에이전트에 `--prompt-arg TEXT` 옵션 추가(값이 없으면 opencode 처럼 stdin 으로 떨어짐) → arg 경로를 테스트로 고정 |

**trace 에 보이는 위치.** 질의 단계(answer/rerank/expand/compress)는 LLM 결과 중 `usage` 만 `st.note(usage=…)` 로 trace meta 에 옮긴다.
그래서 query_engine/answer.py 를 건드리지 않고도 전환 사실이 trace 에 보이도록 `usage["prompt_mode_fallback"]` 에 같은 문자열을 넣었다
(이미 `usage["estimated"]=true` 같은 비토큰 표식이 같은 방식으로 실려 있고, 토큰 합산은 `input_tokens`/`output_tokens` 키만 읽는다).
답변 결과 JSON: `trace.children[answer_llm].meta.usage.prompt_mode_fallback`.

**첨부 인라인.** `files_flag` 가 없는 에이전트의 첨부는 모드와 무관하게 프롬프트 본문 끝에 붙인 뒤 모드를 정한다 — 길이 판정에 첨부가 포함되고,
전환 후 stdin/file 로 가는 본문에도 첨부가 들어간다. 로그 argv 의 `_mask_argv` 는 프롬프트가 더 긴 인자 안에 인라인돼도 `<prompt:N chars>` 로 가린다.

## 3. agents.json 키 (항목마다 명시, `setup/agents.example.json` 동일)

| 키 | 기본값 | 뜻 |
|---|---|---|
| `prompt_mode` | `"stdin"` | 프롬프트 전달 방식. `stdin` = 파이프(권장) · `arg` = 템플릿의 `{prompt}` 인자(프로세스 목록 노출·Windows 32K 한계) · `file` = 임시 파일 경로를 `{prompt_file}` 에 치환. 코드 기본값도 `stdin` |
| `arg_max_chars` | `30000` | `arg` 모드 가드. argv 총 길이가 넘으면 `stdin`(템플릿에 `{prompt_file}` 이 있으면 `file`)으로 자동 전환하고 결과/trace/로그에 `prompt_mode_fallback` 을 남긴다. Windows 한계 32,767 에서 여유. `0` = 끔(넘치면 예전처럼 WinError 206 → `LLMError kind=exec`) |

`python -c "from llmwiki import headless as hl; hl.save_agents(hl.load_agents())"` 로 기존 파일에 기본값을 명시적으로 채울 수 있다(추가만 되고 값은 바뀌지 않는다).

## 4. 검증

```
python -m compileall -q llmwiki tests
python -m unittest tests.test_headless_switch -v          # (1) arg+40,000자 → stdin 전환 · file 전환 · (2) arg_max_chars=0 · Windows 에서 206 재현 · (3) config.json 왕복
python -m unittest tests.test_rag_federation tests.test_console_0915 -v
python -m unittest -k headless -k LlmRetryTest tests.test_providers tests.test_features_0914 -v
python -m llmwiki models test --live                       # 실제 에이전트(opencode 등)가 stdin 프롬프트로 응답하는지 (설치된 환경에서)
```

`tests/test_headless_switch.py` 는 임시 `LLMWIKI_AGENTS_PATH` 와 임시 config.json 만 쓰며 서버·네트워크·저장소 파일을 건드리지 않는다.

**§0 의 단위 테스트 2건 (제품은 정상, 테스트가 새 동작을 몰랐던 것)**

| 테스트 | 원인 | 조치 |
|---|---|---|
| `test_rag_federation.test_plugin_tools_dir` | G 단계의 `plugin_rescan_s`(server.json mcp, 기본 5초) — 파일을 쓴 직후 `load_plugins()` 를 부르면 5초 안이라 스냅샷을 돌려준다 | 파일 변경 뒤 호출 전에 `M._PLUGIN_STATE["checked"] = 0` 으로 주기 경과를 흉내 내어 **시그니처 변화 감지 경로**를 그대로 검증 (`force=True` 는 그 경로를 잃으므로 쓰지 않음) |
| `test_console_0915.test_health_reports_console` | 이 PC 의 config.json 이 `headless:opencode` 로 해석되는데 opencode 가 PATH 에 없어 `health` 가 정당하게 FAIL(exit 1) | exit 0 을 요구하지 않고 **완주(`health:` 요약 줄) + Traceback 없음 + `console_encoding` 행** 으로 판정. 환경 자체는 [IMPLEMENTATION_PLAN_0918.md §0.1](../IMPLEMENTATION_PLAN_0918.md) 대로 사용자가 고른다 |

## 5. 문제 해결

| 증상 | 원인 | 조치 |
|---|---|---|
| `OSError: [WinError 206]` / `LLMError … exec failed` (Windows) | `prompt_mode=arg` + 긴 프롬프트, 그리고 `arg_max_chars=0` | `prompt_mode` 를 `stdin` 으로(권장) 또는 `arg_max_chars` 를 기본 30000 으로 되돌린다 |
| 로그에 `headless prompt_mode fallback: arg→stdin (…)` 이 자주 보인다 | 운영자가 `arg` 를 골랐고 프롬프트가 늘 가드를 넘는다 | 동작은 정상(자동 전환). 잡음을 없애려면 `prompt_mode` 를 `stdin` 으로 |
| 에이전트가 stdin 을 받지 않아 빈 출력/`empty` 재시도 | 일부 CLI 는 파이프 입력을 프롬프트로 보지 않는다 | `file` 모드: `"command": ["opencode", "run", "-m", "{model}", "-f", "{prompt_file}", "첨부 파일의 지시를 따르라"], "prompt_mode": "file"` 처럼 템플릿에 `{prompt_file}` 을 두고 `python -m llmwiki models test --live` 로 확인. (`arg` 를 유지하면서 템플릿에 `{prompt_file}` 을 두면 길이 초과 시 `file` 로 전환된다) |
| trace 에 `usage.prompt_mode_fallback` 이 있다 | 그 호출이 arg 에서 전환됐다 | 정보 표시. 답변 품질에는 영향 없음(프롬프트 전문이 stdin/파일로 전달됨) |
| `health` 가 `headless_agents` 에서 FAIL | 에이전트 실행 파일이 PATH 에 없음 | `agents.json command[0]` 에 절대 경로, 또는 provider 를 API 모델로 (config.json `llm_roles.<role>.provider`) |

## 6. BRINGUP §3.2 에 추가할 행

| 파일 | 키 | 기본값 | 설명 |
|---|---|---|---|
| agents.json | `<agent>.prompt_mode` | `stdin` | 프롬프트 전달 방식 stdin / arg / file. 코드 기본값도 stdin (2026-09-18) |
| agents.json | `<agent>.arg_max_chars` | `30000` | arg 모드 argv 길이 가드 — 넘으면 stdin(`{prompt_file}` 있으면 file)으로 자동 전환 + `prompt_mode_fallback` 표시. 0 = 끔. Windows 명령줄 32,767자 한계(WinError 206) 방지 |

README §0 문서 색인·§4.3 headless 절에는 "prompt_mode 기본 stdin · arg_max_chars 가드 · 검증 `tests/test_headless_switch.py`" 한 줄과 이 문서(병합 후 위치) 링크를 넣는다.
