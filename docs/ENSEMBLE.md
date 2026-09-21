# ENSEMBLE — 역할 단위 앙상블: 최대 3 LLM 병렬 호출 + 취합 LLM

> 설정: `config.json` 의 `llm_roles.<role>.ensemble{…}` 와 전역 기본 `llm_ensemble_defaults{…}` · 취합 규칙 `prompts/ensemble_merge.md`
> CLI: `python -m llmwiki models ensemble show [role]` · `models ensemble set <role> …` · 확인 `models test [--live]`
> 코드: `llmwiki/providers.py` `EnsembleLLM` · `llmwiki/config.py` `Settings.effective_ensemble()` · 설계 근거: [IMPLEMENTATION_PLAN_0918.md §2.6](history/2026-09-18/IMPLEMENTATION_PLAN_0918.md)


## 앙상블로 돌았는지 **어떻게 아나** (2026-09-20)

예전에는 단서가 하나뿐이었다 — `answer_llm` 단계 meta 의
`"model": "llama3.1+llama3.1+llama3.1"` 처럼 **`+` 로 이어 붙은 이름**. 멤버가 몇 개 성공했는지,
누가 느렸는지, 취합에 얼마를 더 썼는지는 결과 안에만 있고 trace 로 올라오지 않아 어느 화면에서도
보이지 않았다. 이제 세 창구가 같은 값을 보여 준다.

| 창구 | 어디에 |
|---|---|
| **Web** | trace 의 `answer_llm` 줄 오른쪽에 **`앙상블 3/3+취합`** 글씨(배경·테두리 없는 파란 글씨). 줄을 펼치면 **멤버별 표**(모델·프로바이더·시간 막대·ms·토큰·실패 사유)와 **취합** 줄 |
| **CLI** | `query --trace` 가 `⑂ 앙상블 — 멤버 3/3 성공 · 취합 1회` 아래에 멤버별 줄 |
| **MCP** | 응답 trace 의 같은 `meta.ensemble` |

**역할 11개 어디에 켜도 보인다.** `answer` 뿐 아니라 `rerank`·`extract`·`summary`·`review`·`expand`·
`verify`·`forensic`·`fusion`·`select` 중 어디에 앙상블을 켜도, **그 역할이 도는 단계**에 같은 표시가 붙고
`meta.ensemble.role` 에 역할 이름이 들어간다. 프로바이더(`providers._note_ensemble`)가 **지금 열려 있는
단계**에 직접 적기 때문이다 — 호출 자리마다 코드를 넣는 방식이었다면 한 자리만 빠뜨려도 그 역할은
"앙상블인지 알 수 없는" 상태가 된다.

```
answer_llm              17350.0 ms   62% llm=4 tok=8200/1734
  ⑂ 앙상블 — 멤버 3/3 성공 · 취합 1회 · 대기 all
     멤버1 llama3.1/openai           15900 ms  tok 2050/560
     멤버2 llama3.1/openai           14100 ms  tok 2050/610
     멤버3 llama3.1/openai           13200 ms  tok 2050/510
     취합  llama3.1/openai            6150 ms  tok 6150/1680
     (멤버는 동시에 실행 — 단계 시간 ≈ 가장 느린 멤버 + 취합)
```

**LLM 호출 수로도 확인된다**: `counters.llm_calls` 가 **멤버 수 + 1**(취합)이다. 위 예의 `llm=4` 는
멤버 3 + 취합 1 이다. 성공한 멤버가 하나뿐이면 취합 없이 그 답을 그대로 쓰므로 `+1` 이 없고
배지가 `취합 없음` 으로 뜬다.

**막대는 가장 느린 멤버 기준**이다. 멤버는 병렬로 돌기 때문에 ms 를 더하면 단계 시간과 맞지 않는다 —
순차로 그리면 "3배 걸렸다" 로 잘못 읽힌다.

### 읽을 때 같이 보는 것

| 값 | 무엇을 뜻하나 |
|---|---|
| 멤버 모델이 **전부 같다** | 앙상블의 값어치는 서로 다른 모델이 다르게 답하는 데서 나온다. 같은 모델 여러 개는 샘플링 난수만큼만 갈라지는 반면 비용은 그대로 n배다 |
| 멤버 `실패` | `llm_report` 에도 `ensemble_member=true` 로 남는다. `min_results` 미만이면 단계 자체가 실패한다 |
| `cited` 가 비었다 | 취합 LLM 이 멤버들의 `[C#]` 인용을 버린 것이다 — 취합 프롬프트(`prompts/ensemble_merge.md`)가 인용 보존을 지시하는지 본다 |

## 0. 한 장 요약

```
pipe.llm_for("answer")  →  make_llm(settings, "answer")  →  role_llm()["ensemble"].enabled && 활성 멤버 ≥ 1 ?
                                                             ├─ 아니오: 단일 LLM (지금까지와 같음)
                                                             └─ 예:     EnsembleLLM(members ≤ 3, aggregator, wait, timeout_s, min_results, prompt)
complete(system, user) → 멤버를 스레드로 병렬 호출 → wait=all: 전부 끝날 때까지 · wait=timeout: timeout_s 뒤 도착한 것만
                        → 성공 < min_results → LLMError(kind=ensemble)   (호출부는 단일 LLM 실패와 같은 대체 경로)
                        → 성공 1개 → 그 본문 그대로 (aggregated=false)
                        → 성공 ≥ 2 → 취합 LLM 에 [원 system][원 user][후보 i: 모델·가중치·본문] + prompts/ensemble_merge.md 규칙 → 최종 본문
```

| 항목 | 값 |
|---|---|
| 적용 단위 | **역할**(`answer` `rerank` `extract` `summary` `review` `expand` `verify` `forensic` `fusion` `select`). 그 역할의 모든 LLM 호출이 앙상블로 바뀐다 — 파이프라인 단계 코드는 그대로 |
| 멤버 | 최대 **3**(`ENSEMBLE_MAX_MEMBERS`). `enabled=false` 또는 `model` 이 빈 멤버는 빠진다. provider 를 비우면 카탈로그(`models.json`) → 역할 provider 순 |
| 취합기 | `aggregator.model` 을 비우면 **첫 멤버**가 취합. 멤버와 같은 provider/model(effort 미지정)이면 인스턴스를 공유 |
| **취합 프롬프트** | **역할마다 따로**다 (2026-09-19). `prompts/ensemble_merge_<역할>.md` 가 있으면 그것, 없으면 공용 `prompts/ensemble_merge.md`. `ensemble.prompt` 에 이름을 적으면 그것이 최우선 |
| 가중치 | `weight` 는 취합 프롬프트에 "가중치 1.50" 으로 전달되는 참고값 — 수식 합산이 아니라 `ensemble_merge.md` 의 규칙이 해석한다 |
| 보이는 곳 | `models ensemble show` · `models show`(역할 인스턴스 `ensemble N members`) · `models test [--live]`(멤버·취합기별 결과) · `/api/models`(`ensemble_enabled`, describe 의 members) · 질의 결과 `llm_report`(멤버 실패 `ensemble_member=true`) · 진행 패널 메시지 |
| **미구현** (2026-09-19 기준) | 결과 `r["ensemble"]` 메타는 만들어지지만 **trace(워터폴) meta 로 옮겨지지 않는다** — `answer.py` 등 단계 코드가 `usage` 만 `st.note` 한다 |

### 자주 오해하는 세 가지 (2026-09-19)

| 질문 | 답 |
|---|---|
| 앙상블을 켜면 역할 모델 + 멤버들이 **같이** 도나? | 아니다. 켜면 **역할 모델 대신** 멤버들이 돈다. 멤버가 `provider`/`model` 칸을 비웠을 때만 역할 값을 상속한다 |
| 취합 LLM 은 항상 도나? | 아니다. 성공한 멤버가 **2개 이상**일 때만 한 번 돈다. 1개면 그 답을 그대로 쓴다 |
| 멤버마다 프롬프트가 다른가? | 아니다. 멤버는 모두 **그 단계의 같은 프롬프트**(`answer_system.md` 등)를 받는다. 자기 프롬프트를 쓰는 것은 **취합 LLM 하나**뿐이다 |

### 취합 프롬프트는 역할마다 따로다 (2026-09-19)

취합 규칙은 **그 작업의 출력 형식에 매여 있다**. `answer` 는 인용 `[C#]` 을 지켜 본문을 합쳐야 하고,
`rerank`·`select`·`fusion` 은 JSON 번호 목록을 합쳐야 하고, `extract` 는 엔티티·관계를 합집합으로 모아야 하고,
`verify` 는 "근거 부족" 판정을 함부로 뒤집으면 안 된다. 한 파일로 이 모두를 덮으면 문장이 길어지고, 한 역할을 고치면 다른 역할이 흔들린다.

| 파일 | 쓰이는 때 |
|---|---|
| `prompts/ensemble_merge_<역할>.md` | 그 파일이 **있으면** 그 역할의 취합에 쓰인다 (10개 역할 모두 기본 제공) |
| `prompts/ensemble_merge.md` | 역할 파일이 없을 때의 공용 규칙 |
| `llm_roles.<역할>.ensemble.prompt` | 이름을 적으면 **최우선**. 여러 역할이 한 파일을 공유하고 싶을 때 |

역할 파일을 **지우면** 자동으로 공용으로 돌아간다. 각 역할 파일은 공용 규칙 6개에 그 역할만의 규칙 한 문단이 붙은 모양이다.
편집은 Settings › **프롬프트** (`ensemble_merge_*` 11개가 목록에 있다) 또는 `python -m llmwiki prompts show|reset ensemble_merge_answer`.
🧭 Pipeline › 앙상블 의 각 역할 줄에서 **프롬프트 칸 옆 `편집`** 을 누르면 그 파일로 바로 간다.

**어디서 켜나** — 역할마다 따로다. 전역 토글이 아니라 `config.json` 의 `llm_roles.<역할>.ensemble.enabled` 다.

| 창구 | 자리 |
|---|---|
| Web | Settings › 모델·프로바이더 의 역할 줄 **아래 '앙상블' 줄**. 접힌 상태에서도 오른쪽 `사용` 체크로 켜고 끈다. 켜면 펼쳐져 멤버 3칸·취합 LLM·정책이 나오고, 위쪽 **저장 & 프로바이더 재로드** 로 반영 |
| Web (읽기) | 🧭 Pipeline › 단계 상세의 **'이 단계가 부르는 LLM'** 표 — 그 단계의 역할·모델·앙상블 상태와 호출 방식. `고치기` 를 누르면 위 자리로 이동 |
| CLI | `models ensemble show [역할]` · `models ensemble set <역할> --enabled true --member 1 provider=… model=… weight=1.5 --aggregator model=…` |
| 파일 | `config.json` 의 `llm_roles.<역할>.ensemble` (기본값 묶음은 `llm_ensemble_defaults`) |

## 1. 왜 역할 단위인가 (설계 근거)

| 대안 | 왜 버렸나 / 골랐나 |
|---|---|
| 파이프라인 단계마다 앙상블 코드 | LLM 단계가 12개라 누락이 생기고 "취합" 의 뜻이 단계마다 다르다(답변 본문 vs 리랭크 번호 목록 vs JSON) → 기각 |
| 프로바이더 하나에 여러 모델 | provider 가 다른 멤버(anthropic + openai 게이트웨이 + headless)를 섞을 수 없다 → 기각 |
| **`BaseLLM` 래퍼 `EnsembleLLM`** ✔ | 모든 단계가 `pipe.llm_for(role)` 를 거치므로 한 구현이 전 단계에 적용된다. 멤버는 각자의 재시도·회로 차단 정책을 그대로 쓰고 래퍼는 재시도하지 않는다(`retries=0`). JSON 모드도 취합기에 "출력은 JSON 만" 을 덧붙여 같은 스키마를 내게 한다 |

취합 규칙을 프롬프트 파일에 둔 이유: "가중치 2.0 은 다른 후보 전부와 같은 무게", "수치는 특정 모델 우선" 같은 조직별 판단은 코드가 아니라 운영자가 고쳐야 하는 값이다. `prompts/ensemble_merge.md` 는 첫 사용 때 기본 문구로 생성되며 수정하면 재시작 없이 반영된다.

## 2. 설정 키

### 2.1 `config.json` — 전체 JSON (기본값을 명시한 모양, `config fill-defaults` 가 쓰는 뼈대)

```json
{
  "llm_ensemble_defaults": {"wait": "all", "timeout_s": 120, "min_results": 1, "prompt": "ensemble_merge"},
  "llm_roles": {
    "answer": {
      "provider": "", "model": "", "effort": "", "timeout_s": "", "retries": "", "max_tokens": "",
      "ensemble": {
        "enabled": false,
        "members": [
          {"enabled": true, "provider": "", "model": "", "weight": 1.0, "effort": ""},
          {"enabled": true, "provider": "", "model": "", "weight": 1.0, "effort": ""},
          {"enabled": true, "provider": "", "model": "", "weight": 1.0, "effort": ""}
        ],
        "wait": "", "timeout_s": "", "min_results": "", "prompt": "",
        "aggregator": {"provider": "", "model": "", "effort": ""}
      }
    }
  }
}
```

켠 예(answer 역할, 두 모델 + 별도 취합기):

```json
"answer": {"provider": "anthropic", "model": "claude-sonnet-5",
  "ensemble": {"enabled": true,
    "members": [{"enabled": true, "provider": "anthropic", "model": "claude-sonnet-5", "weight": 1.5, "effort": ""},
                {"enabled": true, "provider": "openai",    "model": "gpt-4o-mini",     "weight": 1.0, "effort": "low"},
                {"enabled": false, "provider": "", "model": "", "weight": 1.0, "effort": ""}],
    "wait": "timeout", "timeout_s": 90, "min_results": 1, "prompt": "ensemble_merge",
    "aggregator": {"provider": "anthropic", "model": "claude-opus-5", "effort": ""}}}
```

| 키 | 기본 | 뜻 |
|---|---|---|
| `llm_ensemble_defaults.wait` | `"all"` | `all` = 모든 멤버가 끝날 때까지(멤버별 timeout 이 상한) · `timeout` = `timeout_s` 뒤 도착한 결과만으로 진행(늦은 멤버 결과는 버리고 incident 로 기록) |
| `llm_ensemble_defaults.timeout_s` | `120` | `wait=timeout` 의 대기 상한(초). `wait=all` 에서는 쓰이지 않는다 |
| `llm_ensemble_defaults.min_results` | `1` | 성공 결과가 이보다 적으면 앙상블 실패(`LLMError kind=ensemble`, transient 는 실패 멤버가 모두 transient 일 때) |
| `llm_ensemble_defaults.prompt` | `"ensemble_merge"` | 취합 규칙 파일 이름 → `prompts/<이름>.md` |
| `llm_roles.<role>.ensemble.enabled` | `false` | 켬. 실제 활성은 `enabled && 활성 멤버 ≥ 1` |
| `…ensemble.members[]` | 빈 뼈대 3행 | `enabled`(true) · `provider`("" = 카탈로그 → 역할 provider) · `model`("" = 이 멤버 사용 안 함) · `weight`(1.0) · `effort`("" = 호출 시점의 역할 effort) |
| `…ensemble.wait` · `timeout_s` · `min_results` · `prompt` | `""` = 상속 | 비우면 `llm_ensemble_defaults` → 코드 `ENSEMBLE_DEFAULTS` 순 |
| `…ensemble.aggregator` | `{"provider": "", "model": "", "effort": ""}` | 취합 LLM. `model` 비움 = 첫 멤버가 취합. provider 비움 = 카탈로그 → 역할 provider |

정규화: 파일 저장 형태는 `config._norm_ensemble_raw`, 해석은 `Settings.effective_ensemble(role)`(기본값 병합·빈 멤버 제거·provider 해석·`provider_source` 표기).
환경변수/단축키로는 JSON 문자열 — `LLMWIKI_ANSWER_ENSEMBLE='{"enabled":true,…}'`, `config set answer_ensemble=<json>`.

### 2.2 요청 단위 오버라이드

`llm_roles` 와 역할 키 `ensemble` 은 Web/MCP `overrides` 허용 목록(`OVERRIDE_SAFE_KEYS`)에 있다: `{"overrides": {"llm_roles": {"answer": {"ensemble": {"enabled": true, "members": [...]}}}}}`.
`Pipeline._llm_key` 가 `role_llm()` 결과(앙상블 포함)를 서명에 넣으므로 그 요청만 앙상블 인스턴스를 새로 만든다.

## 3. 동작 상세 (`EnsembleLLM._complete`)

1. 멤버를 `ThreadPoolExecutor`(≤3) 로 동시에 `complete()` — 각 스레드는 진행 토큰을 물려받아 취소가 전파되고, 자기 incident 를 모아 돌려준다. 멤버의 `effort` 가 비면 호출 시점의 역할 effort.
2. 0.25초 간격으로 완료를 모으며 `check_cancel()`. `wait=timeout` 이고 `timeout_s` 가 지나면 남은 멤버를 `timed_out` 으로 기록하고 진행.
3. 멤버 incident 를 요청 스레드로 옮긴다(`ensemble_member=true`, `ensemble_role`). 프로파일러 카운터 `llm_calls` 에 멤버 수-1 을 더해 실제 호출 수를 맞춘다.
4. 성공 < `min_results` → `LLMError`. 성공 1개 → 그 본문(`ensemble.aggregated=false`). 성공 ≥ 2 → 취합기 `complete(merge_rules, "[원래 작업의 시스템 프롬프트]…[후보 답변 N개] 후보 i — 모델 X (provider), 가중치 W: …")`.
5. 반환 `{"text", "usage"(멤버 입력·출력 토큰 합; 취합기 사용량은 `ensemble.aggregator.usage`), "ms", "model"("m1+m2" 또는 단일), "ensemble": {"members": [{i, model, provider, weight, ok, ms, chars, usage, error, attempts, transient?, timed_out?}], "aggregator": {model, provider, ms, usage, attempts, prompt_chars} | null, "policy": {wait, timeout_s, min_results, prompt}, "aggregated": bool}}`.
6. `ping()`/`live_test()` 는 멤버 전부 + (멤버가 아닌) 취합기를 각각 검사해 `members`/`aggregator` 행을 싣고, `ok = 성공 ≥ min_results && 취합기 ok`.

## 4. 세 창구

| 창구 | 내용 |
|---|---|
| CLI | `models ensemble show [role]`(raw + effective) · `models ensemble set <role> [--enabled true\|false] [--member N provider=… model=… weight=… effort=… enabled=…]… [--aggregator provider=… model=… effort=…] [--wait all\|timeout\|''] [--timeout N\|''] [--min N\|'']` (`''` = 상속으로 되돌림). 저장 후 `p.reload()` 로 즉시 적용. `models test [--live]` 가 멤버·취합기별 결과를 표시. `config fill-defaults` 가 모든 역할에 꺼진 뼈대를 채움 |
| Web UI | **Settings › 모델·프로바이더 › 역할 표**의 각 역할 행 아래 접이식 **「앙상블」** 칸 (2026-09-19 추가). 멤버 3칸을 각각 `☑ 사용 / provider / model / w(가중치)` 로 고르고, 쓰지 않을 멤버는 체크를 풀면 `✘ 사용 안 함` 으로 흐려진다. 아래 줄에 대기(`all`/`timeout`)·`timeout_s`·`min_results`·프롬프트 파일, 그 아래에 **취합 LLM**(provider·model). 맨 아래 한 줄이 **지금 유효한 값**(기본값이 합쳐지고 빈 멤버가 걸러진 것)을 보여 준다. 저장은 위쪽 「저장 & 프로바이더 재로드」 — `config.json llm_roles.<role>.ensemble` 에 들어가며 CLI 와 **같은 값**이다 |
| MCP | 전용 도구 없음. `wiki_query(overrides={"llm_roles": {"answer": {"ensemble": {…}}}})` 로 요청 단위 적용 가능. 결과 `llm_report` 에 멤버 실패가 보인다 |

## 5. 검증 명령

```powershell
python -m llmwiki models ensemble show answer
python -m llmwiki models ensemble set answer --enabled true --member 1 provider=mock model=mock weight=1.5 --member 2 provider=headless:mock model=mock --wait timeout --timeout 60
python -m llmwiki models test --live | Select-String answer          # ensemble 2/2 members ok; …
python -m llmwiki query "테스트 질문" --json --no-log | Select-String '"model"'   # "mock+mock"
python -m llmwiki models ensemble set answer --enabled false                       # 되돌리기
python -m unittest tests.test_ensemble_surface -v    # 설정 계층(기본값 합치기·빈 멤버 제거·상한 3·저장 왕복) + 세 창구 표면 10건
```

```powershell
python tools/verify/verify_ensemble_ui.py     # 앙상블 편집기를 실제 브라우저로 눌러 본다 (18건)
```

### Web UI 에서 켜는 절차 (2026-09-20 기준)

편집 자리는 **🧭 Pipeline › 앙상블** 한 곳이다. Settings › 모델·프로바이더 의 역할 표에는 *상태만* 보이고
거기에는 「앙상블 설정으로」 버튼이 있어 이 탭으로 건너뛴다 (같은 값을 두 화면에서 받으면 어느 쪽이 적용됐는지 알 수 없어서다).

1. 역할 줄의 **「사용」** 을 켠다.
2. **멤버의 「모델」 칸에서 모델을 고른다.** ← 이 단계가 필수다.
   「쓰기」체크만으로는 멤버가 켜지지 않는다. `effective_ensemble()` 이
   `if not model or not enabled: continue` 로 거르기 때문에, **모델이 비면 「쓰기」와 무관하게 그 멤버는 빠진다.**
   화면은 이 상태를 `▲ 모델을 골라야 켜집니다` 로 표시하고, 멤버가 0개면
   `「사용」은 켜져 있지만 쓸 멤버가 0개라 앙상블이 돌지 않습니다` 경고와
   **「빈 멤버를 <역할 모델> 로 채우기」** 버튼을 띄운다.
   편의를 위해 「쓰기」를 켜면 빈 모델 칸에 **역할 모델이 자동으로 채워진다** — 다른 모델로 바꾸면 된다.
3. 위쪽 **저장 & 프로바이더 재로드**. 맨 아래 「지금 유효」 줄이 바뀌면 반영된 것이다.

`provider` 칸을 비우면 카탈로그 → 역할 provider 를 상속한다(여기는 진짜 상속이다).
`모델` 칸을 비우는 것은 상속이 아니라 **그 멤버를 안 쓰는 것**이므로 드롭다운 라벨도
`(비움 — 이 멤버는 호출되지 않습니다)` 로 적혀 있다.

## 6. 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| `models ensemble show` 가 `ensemble=off` 인데 `enabled: true` 로 적었다 | 활성 멤버가 0개(`model` 비움 또는 `enabled=false`). 멤버 `model` 을 채운다 |
| `ensemble(answer): 1/2 멤버만 성공 (min_results=2)` | 한 멤버 실패. `llm_report` 의 `ensemble_member` 행에서 원인, 또는 `min_results=1` |
| 응답이 두 배로 느리다 | `wait=all` 에서 느린 멤버가 상한. `wait=timeout` + `timeout_s`, 또는 느린 멤버 `enabled=false` |
| 취합 결과가 JSON 파싱에 실패한다(리랭크·검증 역할) | 취합기 모델이 JSON 지시를 어김. 취합기를 더 큰 모델로, 또는 `ensemble_merge.md` 규칙 1 을 강조 |
| Web 에서 모델 드롭다운이 역할마다 **하나뿐**이다 | 그 하나는 앙상블을 쓰지 않을 때의 단일 모델이다. 멤버 3개는 그 행 아래 **앙상블 ▸** 를 펼쳐야 나온다 |
| 앙상블 칸에서 멤버를 고쳤는데 그대로다 | 이 화면의 다른 값과 마찬가지로 **저장 & 프로바이더 재로드** 를 눌러야 파일에 들어간다. 눌렀는데도 그대로면 `models ensemble show <role>` 로 파일 값을 확인한다 |
| 워터폴에 멤버별 시간이 안 보인다 | trace 미전파(§0). `models test --live` 의 멤버 행이나 `error.log` 의 incident 로 확인 |
| **멤버 표에 ms 는 있는데 시간 막대만 안 보인다** | 2026-09-20 이전 버그: 막대가 정의된 적 없는 CSS 변수 `--accent` 를 써서 `background` 선언이 통째로 무효가 됐다(투명). `--s1` 로 고쳤고, `tools/verify/verify_ui_wiring.py` 가 "정의 없는 CSS 변수" 를 이제 검사한다 |
| **「사용」을 켰는데 멤버 칸이 비활성처럼 보이고 켜지지 않는다** | 멤버는 **모델을 골라야** 켜진다(「쓰기」 체크만으로는 안 된다). 모델 칸에서 고르거나 「빈 멤버를 … 로 채우기」. CLI 로는 `models ensemble show <role>` 이 `! enabled=true 이지만 쓸 멤버가 0개입니다` 로 알려 준다 |
| **멤버 모델 드롭다운에 고를 것이 아무것도 없다** | 2026-09-20 이전 버그: 앙상블 탭이 카탈로그를 없는 키(`j.catalog`)로 읽어 목록이 비었다. 고쳐졌다(`j.catalog_models`). 그래도 비면 `models.json` 의 `enabled` 와 Settings › 모델 의 연결 테스트 결과를 본다 — 꺼졌거나 연결 실패한 모델은 회색으로 **고를 수 없다** |
| 앙상블 화면을 고쳤는데 서버 동작이 그대로다 | 실행 중인 `serve` 는 새 정적 JS 는 주지만 **파이썬 모듈은 옛것**이다. `serve` 를 재시작한다 |
| 카탈로그에 없는 모델을 멤버로 적었다 | 동작하지만 `models test` 힌트 없음. provider 를 명시한다 |

## 7. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/providers.py` | `ENSEMBLE_MAX_MEMBERS=3` · `EnsembleLLM`(`available/reason/policy/describe/ping/live_test/_run_member/_complete`) · `_make_ensemble()` · `make_llm()` 분기 |
| `llmwiki/config.py` | `Settings.LLM_ROLES`(10 역할) · `ENSEMBLE_DEFAULTS` · `llm_ensemble_defaults` · `effective_ensemble()` · `role_llm()["ensemble"]` · `_norm_ensemble_raw()` · `ensemble_template()` · `fill_defaults()` · `apply_overrides`(`answer_ensemble=<json>`) · `SETTING_HELP["llm_ensemble_defaults"]` |
| `llmwiki/prompts.py` | `DEFAULTS["ensemble_merge"]` → `prompts/ensemble_merge.md`(첫 사용 시 생성) |
| `llmwiki/pipeline.py` | `_llm_key()`(앙상블 포함 서명) · `provider_status()`(`ensemble_enabled`) · `test_providers()`(`live_members`, `live_aggregator`, `row["ensemble"]`) |
| `llmwiki/cli.py` | `_cmd_models_ensemble()` · `models` 파서의 `--enabled --member --aggregator --wait --timeout --min` · `show` 가 역할 모델과 "멤버 0개" 경고를 함께 출력 |
| `llmwiki/web/server.py` | `OVERRIDE_SAFE_KEYS` 에 `llm_roles`/역할 `ensemble` 허용 · `/api/models` 의 `ensemble.<role>.role`(멤버가 상속하는 역할 모델) |
| `llmwiki/web/static/js/settings.js` | `ensembleRow()`(편집기) · `refreshEnsRole()`(입력에 즉시 반응) · `wireEnsMembers()`(「쓰기」 자동 채움·「빈 멤버 채우기」) · `ensembleSettings()`(저장 dict) · `loadEnsemble()`/`saveEnsemble()` |
| `llmwiki/web/static/style.css` | `.ens-*` 편집기 스타일 · `.ens-m.need`/`.ens-eff.warnbox`(멤버 0개 경고) |
| `tools/verify/verify_ensemble_ui.py` | 편집기를 실제 브라우저로 눌러 보는 검사 18건 (켰는데 멤버 0개 상태 재현 → 자동 채움 → 저장 왕복) |
