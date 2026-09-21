# SETTINGS SYNC — 설정을 **파일에 기본값까지 명시**하고, UI ↔ 파일 ↔ 유효값이 어긋나지 않게 확인하기

> CLI: `python -m llmwiki config fill-defaults [--tuning --rules --all --examples --dry-run]` · `config env` · `config reload [--env]` · `config show --effective` · `models test --catalog [--live]`
> API: `GET /api/env` · `POST /api/env {action: reload}` · `GET /api/config/effective` · `POST /api/config {action: reload}` · `POST /api/models/test_catalog`
> 화면: Settings › config.json 탭(유효 설정 · `.env` 패널) · Settings › 모델 · 프로바이더(카탈로그 "전체 카탈로그 테스트")
> 설계 원문: [IMPLEMENTATION_PLAN_0918.md §2.10](history/2026-09-18/IMPLEMENTATION_PLAN_0918.md) · 설정 우선순위는 [BRINGUP_GUIDE.md §3](BRINGUP_GUIDE.md)

## 0. 한 장 요약

| 기능 | 무엇을 푸나 | 어디서 |
|---|---|---|
| `config fill-defaults` | "이 키가 있는 줄 몰라서 못 바꿨다" — config.json / tuning.json / query_rules.json / data/rules.json 에 **모든 키를 기본값으로 명시**(있는 값 유지) | CLI(admin) |
| `config env` / `/api/env` | `.env` 에 무엇이 있는지(값은 마스킹), OS 환경변수와 겹치는지, 어떤 `LLMWIKI_*` 오버라이드가 config.json 을 이기고 있는지 | CLI(admin) · `GET /api/env`(admin) · Settings › config.json › `.env` |
| `config reload --env` / `POST /api/env reload` | 파일을 고친 뒤 서버 프로세스의 `os.environ` 을 `.env` 값으로 맞춘다 | CLI(admin) · API(admin) |
| `models test --catalog` / `/api/models/test_catalog` | `models.json` 의 enabled 모델 **전부**를 (provider, model) 짝으로 ping(+`--live` 완성 1회) — 설정에 쓰기 전에 무엇이 실제로 붙는지 | CLI(read) · API(run) · Settings › 카탈로그 버튼 |
| 카탈로그 provider 자동 해석 | 역할에 `model` 만 적어도 카탈로그가 가리키는 provider 를 쓴다(`provider_source=catalog`) · 짝이 다르면 `models test` 가 한 줄 힌트 | `Settings.role_llm` · `models test` |
| 양방향 확인 절차 | UI 저장 → 파일 → 유효값, 파일 편집 → reload → UI 를 표면마다 확인 | §6 · `tools/verify/verify_settings_sync.py` |

## 1. 왜 이렇게 만들었나

2026-09-18 사용자 지적 두 가지: (1) Web UI 에서 바꾼 값이 서버 동작을 바꾸지 않거나 파일 편집이 UI 에 안 보인다, (2) 나중에 운영자가
값을 바꾸려면 **기존 줄을 고치는 것**이어야 하고 키가 있는지 코드를 뒤져 알아내는 것이면 안 된다.

| 대안 | 왜 안 골랐나 |
|---|---|
| 기본값은 코드에만 두고 파일은 바뀐 값만(sparse) 유지 | 지금까지의 방식. 키 존재를 모르면 못 바꾼다. 문서와 파일이 어긋난다 → 채우는 명령을 두고, 저장 로직이 채워진 모양을 **유지**하게 했다(`_explicit_defaults`, `save_settings` 의 뼈대 유지) |
| 저장할 때 항상 모든 키를 쓰기(강제 explicit) | 예전 파일·격리 테스트·짧은 예시 파일이 모두 뚱뚱해진다 → 파일에 표식이 있을 때만 explicit |
| `.env` 를 Web 에서 편집 | 비밀값을 브라우저에 보내야 한다 → 읽기(마스킹) + "다시 읽기" 만. 편집은 파일에서 |
| provider/model 불일치를 자동으로 고쳐 저장 | 운영자가 모르는 사이 파일이 바뀐다 → provider 가 **비어 있을 때만** 카탈로그로 해석하고, 명시값이 카탈로그와 다르면 고치지 않고 힌트만 |

## 2. `config fill-defaults`

```bat
python -m llmwiki config fill-defaults --dry-run              :: config.json 에 무엇이 추가될지만
python -m llmwiki config fill-defaults                        :: config.json
python -m llmwiki config fill-defaults --all                  :: + tuning.json + query_rules.json + data/rules.json
python -m llmwiki config fill-defaults --all --examples       :: + setup/*.example.* (저장소 정비용)
python -m llmwiki config fill-defaults --all --json
```

| 대상 | 플래그 | 채우는 것 | 구현 |
|---|---|---|---|
| `config.json` | 항상 | 빠진 `Settings` 키 전부(dataclass 순서로 뒤에 추가) · `toggles` 전부 · `llm_roles.<role>` 뼈대 — 10개 역할(`answer rerank extract summary review expand verify forensic fusion select`)마다 `provider model effort timeout_s retries max_tokens`(빈 문자열 = 전역 상속) + `ensemble` 템플릿(멤버 3칸) | `config.fill_defaults` |
| `tuning.json` | `--tuning` | 레지스트리의 `source=tuning` 키 전부를 기본값으로 + `_explicit_defaults: true` 표식 + 설명 `_comment`. 모르는 키는 지우지 않고 `unknown` 으로 보고. config.json 항목이 섞여 있으면 "여기서는 무시됨" | `tuning.fill_defaults` |
| `query_rules.json` | `--rules` | 빠진 type 절(`acronym synonym alias related exclude compound`)을 빈 사전으로. 파일이 없으면 `DEFAULT_RULES` 로 생성 | `query_rules.fill_defaults` |
| `data/rules.json` | `--rules` | 빠진 최상위 절(`entities relation_patterns analyst_pattern decision_pattern date_patterns money_pattern percent_pattern types_for_cooccur id_patterns link_rules explicit_rels related_key_type`)을 `DEFAULT_RULES` 값으로 | `graph_rules.fill_defaults` |
| `setup/…` | `--examples` | `config.example.json` · `config.example.headless.json` · `config.example.pat-gateway.json` · (`--tuning`) `tuning.example.json`(없으면 만든다) · (`--rules`) `query_rules.example*.json` · `rules.example*.json` | 같은 함수 |

`--all` = `--tuning --rules`. 출력 한 줄 = `[write|same|dry ] 종류 경로 — 추가 N개` + 추가된 키/토글/역할 목록. 종료 코드: 어느 파일이든 오류(JSON 객체가 아님 등)면 1.
있는 값은 절대 바꾸지 않는다 — 되돌리기는 "필요 없는 줄을 지우면 기본값".

표식의 효과: `tuning.json` 에 `_explicit_defaults: true` 가 있으면 이후 `tuning set` · Web 튜닝 저장도 **모든 키를 유지**한다(`save_tuning`).
`config.json` 의 역할에 `ensemble` 뼈대가 있으면 `save_settings` 가 저장 뒤에도 뼈대를 유지한다(Web 저장 한 번에 명시 줄이 사라지지 않게).
권한: CLI `config` 의 read action 은 `show`/`paths` 만이라 `fill-defaults` · `reload` · `env` 는 **admin** 등급이다.

## 3. `.env` 가시성 — `config env` · `GET /api/env` · 다시 읽기

```bat
python -m llmwiki config env            :: key · set · source · value(masked) 표 + 활성 LLMWIKI_* 오버라이드
python -m llmwiki config env --json
python -m llmwiki config reload --env   :: config.json + .env 다시 읽기 (이 프로세스 / Web 콘솔이면 서버)
```

`env_report()` 반환 `{path, exists, keys[], overrides[], mtime, note}`:

| 필드 | 뜻 |
|---|---|
| `keys[]` | `.env` 의 키 + 항상 보여 주는 `KNOWN_ENV_KEYS`(`OPENAI_API_KEY LLM_API_KEY OPENAI_EMBED_API_KEY ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN VOYAGE_API_KEY RERANK_API_KEY COHERE_API_KEY JINA_API_KEY LLMWIKI_MCP_TOKEN LLMWIKI_API_KEY PYTHONIOENCODING`). 각각 `set`(os.environ 에 값 있음) · `masked` · `in_file` · `file_empty`(`KEY=` 빈 값) · `in_env` · `source` = `file` / `env` / `both` / **`env(os 가 우선)`**(OS 환경변수가 .env 값을 이기고 있음 — 헷갈리는 지점) · `secret` |
| `overrides[]` | 값이 있는 모든 `LLMWIKI_*` 환경변수 → 어느 설정 키를 덮는지(`toggles.<name>`, `(경로) …`, `(설정 키 아님) …`). **config.json 보다 우선**이므로 "파일을 고쳤는데 안 바뀐다" 의 첫 용의자 |
| 마스킹 | `mask_secret`: 9자 이상은 앞 3자 + `…` + 뒤 4자, 8자 이하는 `***`. 이름에 KEY/TOKEN/SECRET/PASSWORD/PASS 가 있으면 비밀로 본다 |

다시 읽기(`reload_env` → `load_dotenv(override=True)`): 파일 값으로 덮어쓰되 **OS 환경변수로 미리 잡힌 키는 덮어쓰지 않고**, 파일에서 사라진 키 중 예전에 .env 가 넣은 것은 지운다. 그 뒤 설정을 다시 해석한다(`load_settings` + `Pipeline.reload`).
API: `GET /api/env`(admin) → 같은 dict · `POST /api/env {"action":"reload"}`(admin) → dict + `reloaded[]` + `ok`.
화면: Settings › config.json 탭 하단 `.env` 패널 — `.env 다시 읽기` · `새로고침` 버튼(`index.html #btn-env-reload/#btn-env-refresh/#env-panel`).
**주의(2026-09-18 확인 시점)**: 버튼과 패널 요소는 `index.html` 에 있으나 이를 채우는 핸들러가 `settings.js` 등 어느 JS 에도 없다. 화면이 비어 있으면 CLI `config env` 또는 `GET /api/env` 로 확인한다.

## 4. 카탈로그 전체 테스트 — `models test --catalog`

```bat
python -m llmwiki models test --catalog           :: enabled 항목마다 (provider, model) ping
python -m llmwiki models test --catalog --live    :: + 항목마다 실제 완성 호출 1회 (토큰 소량, 수십 초)
python -m llmwiki models test                     :: 역할별 (answer/rerank/…) — 예전 그대로
```
`Pipeline.test_catalog(live, kinds)`: `kinds` 기본 `["llm","embed"]`(+ `rerank_url` 이 있으면 `rerank`). 같은 provider/model 은 한 번만 호출하고 나머지 행은 "(위와 같은 provider/model)".
반환 `{rows[]{id, provider, kind, label, ok, ms, detail, live_ok?, live_ms?, live_detail?}, n, ok_n, live, path, note}`. CLI 종료 코드: 전부 OK 0, 아니면 1.
API: `POST /api/models/test_catalog {"live": bool, "kinds": [...]}` — **run** 등급(`/api/models/test` 와 같음). 요청 단위 overrides 를 적용해 테스트할 수 있다.
화면: Settings › 모델 · 프로바이더 › "사용 가능한 모델 카탈로그" 머리말의 **`전체 카탈로그 테스트`** 버튼 + `--live` 체크(`#btn-cat-test`, `#cat-test-live`, 결과 `#cat-test-result`).
**주의(2026-09-18 확인 시점)**: 위 `.env` 패널과 같이 버튼 요소만 있고 JS 핸들러가 없다. 동작하지 않으면 CLI/API 로 확인한다.

## 5. 카탈로그 provider 자동 해석과 불일치 힌트

`Settings.role_llm(role)` → `_resolve_provider(explicit, model, 전역 llm_provider)`:

| 경우 | provider | `provider_source` |
|---|---|---|
| `llm_roles.<role>.provider` 가 있음 | 그 값 | `role` |
| 비어 있고 `model` 이 `models.json` 의 enabled LLM 항목에 **정확히 하나의 provider** 로만 있음(`models_catalog.provider_for`) | 카탈로그의 provider | `catalog` |
| 그 밖 | 전역 `llm_provider` | `global` |

전역 provider 가 `mock`/`none` 이면 카탈로그 해석을 하지 않는다(테스트·오프라인 설정이 네트워크 provider 로 바뀌지 않게). 같은 id 가 두 provider 에 있으면 판단하지 않는다.
불일치 힌트(`Pipeline._catalog_hint`): `models test` 의 역할 행 아래 `↳ …` 한 줄, `/api/models/test` 결과 행의 `hint`. (a) `provider_source=catalog` 면 "provider 가 비어 있어 카탈로그의 provider X 를 사용했습니다", (b) 명시 provider 가 카탈로그와 다르면 그 사실. `provider_source` 는 `/api/models` 의 `roles.<role>.provider_source` · `models show --json` 에서도 보인다.
**계획과 다른 점**: 계획 §2.10/§0.1 은 이 줄을 `health` 에 두는 것으로 읽힐 수 있으나 코드에서는 `models test` 에 있다. `health` 는 `headless:*` provider 의 실행 파일 존재(`headless_agents`)만 본다.

## 6. UI ↔ 파일 ↔ 유효값을 양방향으로 확인하는 절차

우선순위는 항상 **요청 단위 overrides > 환경변수 `LLMWIKI_*` > 파일 > 코드 기본값**. 어긋남은 대부분 (1) 환경변수가 파일을 덮고 있거나 (2) 서버가 다시 읽지 않았거나 (3) UI 가 다른 GET 을 읽고 있어서다.

| 표면 | UI → 파일 → 유효값 | 파일 → reload → UI |
|---|---|---|
| `config.json` | Settings › config.json 저장(`POST /api/config {settings}`, admin) → 파일 diff → `config show --effective`(`source=file`) / `GET /api/config/effective` | 파일 편집 → `config reload` 또는 Settings `다시 읽기`(`POST /api/config {"action":"reload"}`) → UI 는 `GET /api/status` 의 `settings` 로 다시 그림 |
| 역할 모델 (`llm_roles`) | Settings › 모델 저장(`POST /api/config`) → 파일 → `models show`(`(role override)` · `[catalog …]`) | 파일 편집 → `config reload` → `GET /api/models` 의 `roles` |
| `tuning.json` | Settings › 튜닝 저장(`POST /api/tuning {"action":"set"}`, edit) → 파일 → `tuning show --stage <단계>` | 파일 편집 → `POST /api/tuning {"action":"reload"}`(예전에는 config reload 가 필요했다) → `GET /api/tuning` |
| `query_rules.json` | Settings › 질의 규칙 사전 저장(`POST /api/query_rules`) → 파일 → `rules test "…"` | 파일 편집 → 즉시(파일 mtime 캐시) → `GET /api/query_rules` |
| `data/rules.json` | Knowledge › 그래프 규칙 저장(`POST /api/rules`) → 파일 → `build graph` 뒤 `graph profile` 규칙 기여 | 파일 편집 → 다음 빌드 |
| `.env` | (편집은 파일에서만) | 파일 편집 → `config reload --env` / `POST /api/env reload` → `config env` 의 `source`/`masked` |
| `models.json` | Settings › 카탈로그 추가/삭제(`POST /api/models/catalog`, admin) → 파일 → `models list` | 파일 편집 → 카탈로그 `새로고침`(`GET /api/models/catalog`) · `models test --catalog` |

명령 예:
```bat
python -m llmwiki config show --effective | Select-String "^rrf_k|^web_host|^sweep_"      :: 키 = 값 [source] (기본 …) LLMWIKI_…
python -m llmwiki config set rrf_k=40; python -m llmwiki config show --effective | Select-String "^rrf_k"
python -m llmwiki config env                                                               :: LLMWIKI_* 오버라이드가 있으면 그 키는 파일을 고쳐도 안 바뀐다
python -m llmwiki config reload --env
```
자동 점검: `python tools\verify\verify_settings_sync.py`(표면마다 위 두 방향을 확인하는 하네스 — 다른 작업에서 작성 중, 이름만 참조).

## 7. 설정 키

이 절의 기능은 **새 config 키를 추가하지 않는다**. 파일에 생기는 표식·절만 정리한다.

| 파일 | 키/절 | 기본 | 뜻 | 확인 |
|---|---|---|---|---|
| `tuning.json` | `_explicit_defaults` | (없음) | `true` 면 저장 시 모든 튜닝 키 유지. `config fill-defaults --tuning` 이 넣는다. 지우면 다음 저장부터 바뀐 값만 | `Get-Content tuning.json -TotalCount 3` |
| `config.json` | `llm_roles.<role>.{provider,model,effort,timeout_s,retries,max_tokens}` | `""` (전역 상속) | 채워진 뼈대. 빈 문자열은 "지정 안 함" | `models show` 의 `(global)` |
| `config.json` | `llm_roles.<role>.ensemble` | `ensemble_template()` (enabled false, 멤버 3칸) | 앙상블 뼈대(`models ensemble` · 계획 B2 의 ENSEMBLE.md 는 아직 없음) | `models ensemble show <role>` |
| `.env` | `KNOWN_ENV_KEYS` | (비어 있으면 미설정) | 프로바이더가 실제로 읽는 이름. `setup/.env.example` 과 같은 목록 | `config env` |

## 8. 검증

```bat
python -m llmwiki config fill-defaults --all --dry-run          :: 추가될 키 목록만 (파일 변경 없음)
python -m llmwiki config fill-defaults --all --json | python -c "import json,sys; [print(r['kind'], r['path'], len(r.get('added') or [])) for r in json.load(sys.stdin)]"
python -m llmwiki config show --effective | Select-String "\[env\]"    :: 환경변수가 이기고 있는 키
python -m llmwiki config env --json | python -c "import json,sys; d=json.load(sys.stdin); print(d['path'], d['exists'], [o['env'] for o in d['overrides']])"
python -m llmwiki models test --catalog --json | python -c "import json,sys; d=json.load(sys.stdin); print(d['ok_n'], '/', d['n'], d['path'])"
python -m llmwiki models test | Select-String "↳"                  :: provider/model 불일치·카탈로그 자동 해석 힌트
python tools\verify\verify_settings_sync.py
```
전용 단위 테스트 파일은 없다(`fill_defaults`/`env_report`/`test_catalog` 를 직접 검사하는 `tests/*` 없음). `tests/test_console_0915.py` 가 카탈로그 provider 자동 해석이 `headless:opencode` 를 고르는 환경에서 `health` 가 완주함을 확인한다.

## 9. 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| 파일을 고쳤는데 `config show --effective` 가 `[env]` | `LLMWIKI_<KEY>` 환경변수가 우선. `config env` 의 오버라이드 표에서 확인 후 변수 제거 |
| Web 에서 저장했는데 서버 동작이 그대로 | 요청 단위 overrides(사이드바)가 덮고 있거나 다른 프로세스. `GET /api/config/effective` 의 `source` 확인 |
| `.env` 를 바꿨는데 키가 안 바뀐다 | 서버가 다시 읽지 않았다 → `config reload --env` / `POST /api/env reload`. `source=env(os 가 우선)` 이면 OS 환경변수가 이긴다 — 그 변수를 지운다 |
| `fill-defaults` 뒤 Web 저장으로 명시 줄이 사라졌다 | tuning 은 `_explicit_defaults` 표식이 있어야 유지된다(`--tuning` 으로 채웠는지). config 는 역할 `ensemble` 뼈대가 있으면 유지 |
| `[ERR ] … JSON 객체여야 합니다` | 파일이 배열/문자열. 백업 후 `{}` 로 시작해 다시 채운다 |
| `models test --catalog` 가 어떤 행은 FAIL | 그 (provider, model) 짝이 실제로 없다 — `models catalog remove <id>` 또는 provider 수정. `--live` 실패는 PAT 권한/모델명 |
| 역할 provider 가 내가 적지 않은 값으로 나온다 | `provider_source=catalog` — 카탈로그가 골랐다. 다르게 쓰려면 `llm_roles.<role>.provider` 를 적는다(`models set answer_provider=…`) |
| Settings 의 `전체 카탈로그 테스트` / `.env 다시 읽기` 버튼이 반응 없음 | §3·§4 주의 — JS 핸들러 미구현(확인 시점). CLI/API 로 대신 |

## 10. 계획과 다른 점

- 계획 §2.10 의 "provider/model 불일치 줄" 은 `health` 가 아니라 `models test`(`hint`) 에 있다(§5).
- Web 설정 조회에 `GET /api/config` 는 없다 — 화면은 `GET /api/status`(`settings`) 와 `GET /api/config/effective` 를 쓴다.
- `.env` 패널과 카탈로그 전체 테스트 버튼은 HTML 만 있고 JS 핸들러가 없다(2026-09-18 확인). `api_keys.last_used` 저장 · 프리셋 미리보기/저장 구분(계획 §2.10 알려진 어긋남)은 이 문서 범위 밖.
- `setup/tuning.example.json` 은 저장소에 없다 — `config fill-defaults --tuning --examples` 가 처음 만든다.

## 11. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/config.py` | `fill_defaults` · `ROLE_TEMPLATE_KEYS` · `ensemble_template` · `env_report` · `reload_env`/`load_dotenv(override)` · `mask_secret` · `KNOWN_ENV_KEYS` · `effective_settings` · `Settings._resolve_provider`/`_catalog_provider`/`role_llm` · `save_settings` 뼈대 유지 |
| `llmwiki/tuning.py` | `EXPLICIT_MARK` · `save_tuning(explicit)` · `fill_defaults` |
| `llmwiki/query_rules.py` · `llmwiki/graph_rules.py` | 각 `fill_defaults` |
| `llmwiki/models_catalog.py` | `provider_for` |
| `llmwiki/pipeline.py` | `test_catalog` · `_catalog_hint` · `test_providers` 의 `hint` |
| `llmwiki/cli.py` | `_cmd_config_fill_defaults` · `config env|reload [--env]|show --effective` · `models test --catalog` |
| `llmwiki/web/server.py` | `GET/POST /api/env` · `GET /api/config/effective` · `POST /api/config reload` · `POST /api/models/test_catalog` · `POST /api/tuning reload` |
| `llmwiki/auth.py` | `_ADMIN_POST`/GET admin 의 `/api/env` · `_RUN_POST` 의 `/api/models/test_catalog` · `_READ_CLI_ACTIONS["config"]={show, paths}` |
| `llmwiki/web/static/index.html` | `#tab-config` 의 `.env` 패널 · `#btn-cat-test` (핸들러 미구현) |
| `tools/verify/verify_settings_sync.py` | 양방향 자동 점검 하네스(작성 중) |
