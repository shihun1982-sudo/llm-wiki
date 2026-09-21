# 2026-09-18 구현 계획 — 요청 17건의 판정 · 설계 · 검증

> 입력: 사용자 요청 17건(아래 §1) + 화면 질문 1건(§0.1) + 질문 1건(§0.2).
> 전제: [CODE_REVIEW_0917.md](../2026-09-17/CODE_REVIEW_0917.md) 의 P0 보안 항목 중 **요청 14(외부 바인드 기본)** 와 직접 충돌하는 두 가지
> (요청 단위 overrides 로 PAT 유출 · 500 트레이스 노출)는 이 회차에 함께 막는다(§2.14). 나머지 P0/P1 은 그 문서의 §6 순서대로 후속.
> 규칙: 모든 새 설정은 파일(config/tuning/server/prompts/…)에 **기본값까지 명시**하고 `setup/*.example.*` 과 BRINGUP §3.2 에 같이 적는다.

## 0. 먼저 답해야 할 두 가지

### 0.1 "이렇게 설정했는데 왜 연결이 하나도 안 되나" (연결 테스트 화면)

화면의 역할 표는 전부 `openai/claude-sonnet-5`, `openai/anthropic/claude-sonnet-4-5`, `openai/claude-haiku-4-5-…` 이고, detail 은
`connected http://localhost:11434/v1; model … not in list · models: llama3.1:latest` 다. 뜻은 이렇다.

| 무엇 | 지금 값 | 결과 |
|---|---|---|
| 역할의 provider | `openai` (전역 `llm_provider=openai` 를 상속) | "OpenAI **호환** 엔드포인트" 를 뜻한다 — OpenAI/Anthropic 클라우드가 아니라 `openai_base_url` 에 적힌 서버 |
| `openai_base_url` | `http://localhost:11434/v1` | 로컬 Ollama 의 OpenAI 호환 포트. **연결은 됐다**(2초 만에 목록을 받아 왔다) |
| 역할의 model | `claude-sonnet-5` 등 | 그 Ollama 에는 `llama3.1:latest` 하나뿐이라 "not in list" |
| `anthropic/claude-sonnet-4-5` | opencode 식 모델 id | 카탈로그에는 `provider: headless:opencode` 로 등록된 항목인데 역할 provider 는 `openai` 로 남았다 |

즉 **연결 실패가 아니라 provider 와 model 의 짝이 틀린 것**이다. 카탈로그(`models.json`)에는 `claude-sonnet-5 → anthropic`,
`anthropic/claude-sonnet-4-5 → headless:opencode` 로 provider 가 적혀 있는데, 화면에서 모델만 고르면 provider 는 바뀌지 않는다.
고치는 길은 셋 중 하나다.

1. Anthropic 모델을 쓰려면 역할 provider 를 `anthropic` 으로 바꾸고 `.env ANTHROPIC_API_KEY`(또는 `anthropic_base_url` + `ANTHROPIC_AUTH_TOKEN` PAT)를 둔다.
2. 사내 게이트웨이가 그 모델 id 를 제공하면 `openai_base_url` 을 게이트웨이 주소로 바꾸고 `OPENAI_API_KEY` 에 PAT 를 둔다.
3. opencode 로 쓰려면 provider 를 `headless:opencode`, model 을 `anthropic/claude-sonnet-4-5` 로 둔다(요청 8).
4. 로컬 Ollama 만 쓰려면 model 을 `llama3.1`(또는 `ollama pull` 한 모델)로 둔다.

**제품에서 고칠 것(요청 10 의 일부)**: (a) 카탈로그에서 모델을 고르면 그 항목의 provider 를 역할 provider 에 자동으로 채운다,
(b) 서버도 역할 provider 가 비어 있고 model 이 카탈로그에 한 provider 로만 있으면 그 provider 를 쓴다(config.json 에 model 만 적어도 동작),
(c) `health`/연결 테스트가 "provider 와 model 의 짝이 카탈로그와 다르다" 를 한 줄로 알려 준다,
(d) 카탈로그 전체 연결 테스트 버튼.

### 0.2 "retry 를 0 으로 하면 fallback 이 동작하지 않는 게 맞나"

아니다. **재시도와 fallback 은 서로 다른 세 가지 장치**이고 각각 따로 끈다.

| 장치 | 무엇 | 끄는 법 |
|---|---|---|
| 재시도 | 같은 LLM 호출을 timeout/네트워크 오류 때 다시 시도 | `llm_retries=0` 또는 `llm_roles.<role>.retries=0` (headless 는 `agents.json retries`) |
| `llm_fallbacks` | Anthropic 이 서버 측에서 거부했을 때 대체 모델로 1회 | `config.json llm_fallbacks=false` |
| `fallback_loop` 토글 | 근거가 부족하면 **검색을 다시** 하는 L1~L4 확장 루프 | 토글 `fallback_loop=false` (기본 false) |
| 실패 시 대체 경로 | LLM 이 끝내 실패하면 추출식 답변·로컬 리랭크·규칙 그래프로 계속 | 지금은 끌 수 없음 → **토글 `degrade_on_llm_failure`(기본 true)** 를 추가한다. false 면 그 단계는 오류로 끝나고 결과 `result_type=error` + `llm_report` 로 보고 |

retries=0 이면 재시도만 없어지고 위 셋은 그대로다. 문서: BRINGUP §4.3 표에 이 네 줄을 넣는다.

## 1. 요청 판정표

| # | 요청 | 판정 | 크기 | 비고 |
|---|---|---|---|---|
| 1 | 릴리스 노트 | 진행 | S | `docs/RELEASE_NOTES.md` + `__version__` 3.1.0 을 `--version`·`/api/status`·MCP serverInfo 에 |
| 2 | STOPWORDS 별도 파일 | 진행 | S | `stopwords.json`(루트) + `setup/stopwords.example.json`, 경로 레지스트리 `stopwords` |
| 3 | 다른 MCP RAG 와 공존·확장성 | 진행 | M | 리뷰의 MCP 결함(params 가드·expose 허용목록·`${ENV}` 순서·annotations 보존·SSE/Accept·stdio 클라이언트 리더 스레드·락) + `server.json mcp` 절 |
| 4 | Web UI 창 크기 대응 | 진행 | M | CSS 점검 + `verify_responsive.py`(폭 5종에서 가로 넘침 0 검사) |
| 5 | RRF 뒤 LLM 개입 2단계 토글 + 프롬프트 md | 진행 | M | `llm_after_fusion`(prompts/fusion_review.md) · `llm_after_rerank`(prompts/rerank_review.md), 역할 `fusion`·`select` |
| 6 | 단계별 최대 3 LLM 병렬 + 취합 LLM | 진행 | L | 역할 단위 `ensemble` 설정 → 모든 LLM 단계에 동일 적용, prompts/ensemble_merge.md |
| 7 | retry=0 이면 fallback 안 되나 | 답변 | S | §0.2 + 토글 `degrade_on_llm_failure` |
| 8 | headless ↔ API 를 config.json 만으로 | 진행 | M | mock 에이전트로 종단 검증, 카탈로그 provider 자동 해석, `models test` 힌트, 예제 2종 |
| 9 | 답변 모드 3종(결과 유형 · 배경지식 best-effort · REF 목록) | 진행 | M | `answer_mode=grounded|best_effort`, 결과에 `result_type`·`refs` 항상, prompts/answer_best_effort.md |
| 10 | Web UI ↔ 서버 값 연동 점검 · 기본값 파일 명시 · 카탈로그 전체 테스트 | 진행 | L | `verify_settings_sync.py`(양방향), `config fill-defaults`, `/api/models/test_catalog` |
| 11 | 로그 총량 제한 | 진행 | M | `log_total_max_mb` · `log_limit_action=warn|prune|stop`, audit/analysis/requests 포함, health 경고 |
| 12 | 수정마다 돌릴 테스트 문서 | 진행 | S | `docs/TESTING_GUIDE.md` (변경 영역 → 테스트·하네스 표) |
| 13 | 사이드바 접기/펴기 · 배지 뒤로 | 진행 | S | 섹션마다 접기 + 전체 접기, 배지를 이름 뒤로 |
| 14 | 기본 0.0.0.0 바인드 | 진행(전제 有) | S | `web_host` 기본 `0.0.0.0` — 먼저 overrides 화이트리스트·500 트레이스 마스킹(§2.14) |
| 15 | 단계별 파라미터 스윕 + 비교 창 | 진행 | L | `sweep.py`(rerun 재생 위에서 값만 바꿔 N회) + 단계×값 비교 격자(diff 강조) |
| 16 | 단계별/전체 복사 버튼 | 진행 | S | `LW.copyText` 3단 폴백(clipboard → execCommand → 선택 모달), http(비보안 컨텍스트)에서도 동작 |
| 17 | CLI/MCP/Web 정렬 + 전체·스트레스·멍키 | 진행 | M | `verify_surface_align.py` 확장, `verify_all.py`, VERIFICATION_0918.md |

## 2. 설계

### 2.2 불용어 파일 (요청 2)
- 파일 `stopwords.json` `{"_comment": …, "stopwords": [...]}`. 없으면 코드 기본값으로 **생성**한다(prompts 와 같은 방식). mtime 캐시로 재시작 없이 반영.
- `config.py _PATH_DEFAULTS["stopwords"]`, `config paths`·`check_env.py` 에 표시. `textutil.keywords()` 가 파일 집합을 쓴다.
- 대안(query_rules.json 안의 절)은 버렸다 — 질의 규칙은 "확장", 불용어는 "제거" 로 성격이 달라 파일을 나누는 편이 운영자가 찾기 쉽다.

### 2.3 MCP 공존·확장성 (요청 3)
다른 RAG 의 MCP 를 붙이거나(페더레이션/외부 채널) 같은 호스트에서 다른 MCP 서버와 나란히 돌 때 깨지는 지점을 없앤다.
- **입력 방어**: `handle()` 첫머리에서 `jsonrpc/params/id` 형 검사(-32600/-32602), stdio·HTTP 의 `handle` 호출을 try 로 감싸 -32603. Content-Length 파싱 실패 시 빈 줄까지 버림. `stdin.reconfigure(utf-8)`.
- **상호운용**: 클라이언트가 `Accept: application/json, text/event-stream` 과 `MCP-Protocol-Version` 을 보내고 SSE 응답(`data:` 줄)을 파싱. 서버는 배치·헤더를 검사만.
- **안전**: `expose:true` 도 tools/list 이름 집합만 통과, REST 도구 이름 `^[A-Za-z0-9_.-]+$`; `_subst` 는 `${ENV}` 를 먼저 평가하고 사용자 값을 나중에 넣는다.
- **힌트 보존**: 플러그인·원격 `annotations/title` 유지. 중복 이름을 `wiki_sources`·doctor 에 보고.
- **동시성**: `_PLUGIN_TOOLS`/`_FED_CACHE` 락, 플러그인 mtime 검사 주기화, 죽은 소스 백오프, tools/list 티켓.
- **stdio 클라이언트**: 리더 스레드 + `queue.get(timeout)`, stderr 배출 스레드, 서버→클라 요청엔 -32601 즉답, 풀 락 밖에서 `start()`.
- **설정** `server.json mcp`: `fed_cache_ttl_s`(300) · `fed_list_timeout_s`(5) · `source_timeout_s_default`(retrieve 10) · `bridge_timeout_s`(120) · `max_k`(50) · `max_doc_chars`(20000) · `plugin_rescan_s`(5) · `instructions`(initialize 문구). `mcp_sources.json` 저장 시 `sys.executable` 대신 `{python}`.
- 검증: `verify_mcp.py` 에 "잘못된 params · SSE 응답 목업 · expose:true 경로 조작 거부 · `${ENV}` 유출 거부" 항목 추가.

### 2.4 Web UI 창 크기 (요청 4)
- 점검 폭: 360 · 768 · 1024 · 1366 · 1920, 그리고 최대화/복원. 기준: 가로 스크롤 0, 헤더·사이드바·분할 보기·표가 잘리지 않음.
- 수정 방향: 표는 `.tbl-wrap{overflow:auto}` 로 감싸기, flex 자식 `min-width:0`, 900px 이하에서 사이드바를 접힘 상태로 시작(요청 13 의 접기 재사용), 헤더 활동 표시기 줄바꿈, 분할 보기 열 수를 폭에 따라 자동 축소, 긴 토큰 `overflow-wrap:anywhere`.
- 하네스 `tools/verify/verify_responsive.py`: Edge headless 로 각 폭에서 렌더 후 `scrollWidth<=innerWidth` 와 화면 밖 요소 수를 JSON 으로 남긴다.

### 2.5 RRF 뒤 LLM 두 단계 (요청 5)
| 단계 | 토글 | 역할 | 프롬프트 | 입력 → 출력 |
|---|---|---|---|---|
| 융합 직후 (리랭크 전) | `llm_after_fusion` (기본 off) | `fusion` | `prompts/fusion_review.md` | 융합·부스트 상위 `fusion_llm_candidates`(튜닝, 기본 = rerank_candidates) 후보의 제목/발췌 → `{"keep":[번호], "drop":[번호], "reason":…}`. drop 은 `fusion_llm_drop_penalty`(0.3) 배율(0 = 제거) |
| 리랭크 직후 (문서 확장·컨텍스트 전) | `llm_after_rerank` (기본 off) | `select` | `prompts/rerank_review.md` | 리랭크 상위 `post_rerank_llm_k`(top_k_final×2) → `{"select":[번호], "expand_docs":[문서id], "note":…}` — 컨텍스트에 넣을 청크와 통째로 읽힐 문서를 LLM 이 고른다 |
- trace 단계 `fusion_llm`·`rerank_review_llm`, `rerun.STAGE_POINT`(둘 다 `rerank` 재시작점), `architecture.py` 단계 표, `analysis` 잎 단계 목록, `TOGGLE_HELP/GROUPS/EFFECT` 등록.
- `Settings.LLM_ROLES` 에 `fusion`·`select` 추가 → 역할별 모델·정책·앙상블(요청 6)이 그대로 적용된다.
- 실패(LLMError/JSON 파싱 실패) 시 그 단계는 `skipped(error)` 로 남기고 순위는 그대로(품질을 깎지 않는 방향).

### 2.6 앙상블 — 단계별 최대 3 LLM 병렬 + 취합 (요청 6)
프로바이더 계층에 `EnsembleLLM(BaseLLM)` 을 두어 **역할 단위**로 켠다. 모든 LLM 단계가 `pipe.llm_for(role)` 를 거치므로 한 구현이 전 단계에 적용된다.
```jsonc
"llm_roles": {
  "answer": {
    "ensemble": {
      "enabled": true,
      "members": [
        {"enabled": true,  "provider": "anthropic", "model": "claude-sonnet-5", "weight": 1.5},
        {"enabled": true,  "provider": "openai",    "model": "gpt-4o-mini",      "weight": 1.0},
        {"enabled": false, "provider": "",          "model": "",                 "weight": 1.0}   // ✗ = 사용 안 함
      ],
      "wait": "all",            // all = 전부 올 때까지 · timeout = timeout_s 지나면 온 것만으로
      "timeout_s": 120,
      "min_results": 1,         // 이보다 적게 오면 앙상블 실패 → 단일(첫 멤버) 결과 또는 오류
      "aggregator": {"provider": "anthropic", "model": "claude-sonnet-5"},
      "prompt": "ensemble_merge"   // prompts/ensemble_merge.md — 가중치·취합 규칙을 여기에 적는다
    }
  }
}
```
- 전역 기본 `llm_ensemble_defaults` (config.json: wait/timeout_s/min_results/prompt) — 역할에서 생략한 키가 상속.
- 동작: 멤버를 스레드로 병렬 호출 → 정책대로 수집 → 결과 1개면 취합 없이 그대로 → 2개 이상이면 취합 LLM 에 `(원 system, 원 user, 후보 i: 모델·가중치·본문)` 을 `ensemble_merge.md` 규칙과 함께 보내 최종 본문을 받는다. `json_mode` 면 취합기도 같은 JSON 스키마를 내야 한다(프롬프트에 명시).
- 결과 `r["ensemble"] = {members:[{model, ms, ok, chars, usage}], aggregator:{model, ms}, policy}` 를 Profiler 단계 meta 에 남겨 워터폴에서 보인다. `llm_report` 에도 멤버별 실패가 들어간다.
- `pipeline._llm_key` 가 ensemble 설정을 서명에 포함(요청 단위 오버라이드 가능). `_ensure_providers` 는 멤버까지 만든다.
- Web: 역할 표 각 행에 "앙상블 ▸" 펼침 — 멤버 3행(✓/provider/model/weight) · wait/timeout/min · 취합기. CLI: `models ensemble show|set <role> --member N provider=… model=… weight=… [--off]|--aggregator …|--wait all|timeout`. 
- 버린 대안: 파이프라인 단계마다 앙상블 코드를 넣는 것 — 단계가 12개라 누락이 생기고, "취합" 의미가 단계마다 달라진다. 역할 단위 LLM 래퍼가 가장 작고 일관된다.

### 2.8 headless 일반화 (요청 8)
- `config.json` 만으로 전환: 예제 `setup/config.example.headless.json` 을 현행 키로 갱신, BRINGUP §4.3 에 "API ↔ headless 전환은 `llm_provider`/`llm_roles.<role>.provider` 두 줄" 표.
- 카탈로그 provider 자동 해석(§0.1-b) 으로 `llm_roles.answer.model="anthropic/claude-sonnet-4-5"` 만 적어도 `headless:opencode` 가 선택된다.
- 리뷰 결함 수정: 자식 환경 `env_passthrough` 허용 목록(agents.json, 기본 `["PATH","HOME","USERPROFILE","TEMP","TMP","SYSTEMROOT","LANG","LC_*","PYTHONIOENCODING"]` + 에이전트 `env`), `prompt_mode` 기본 `stdin`(opencode/claude/codex 모두 stdin 프롬프트 지원 — 지원하지 않는 에이전트는 파일에서 `arg` 로), 로그 argv 의 프롬프트 마스킹, 줄바꿈 없는 긴 출력의 stall 오탐 수정(바이트 단위 활동 감지).
- 종단 검증: mock 에이전트로 격리 환경에서 `config.json` 만 바꿔 `query`·`models test --live`·Web 역할 테스트 → 다시 API(mock provider) 로 되돌리기까지를 `tests/test_headless_switch.py` 로 고정. 실제 opencode 는 `which` 로 존재 여부만 확인하고 없으면 "설치/PATH" 힌트.

### 2.9 답변 모드 (요청 9)
- 설정 `answer_mode` (config.json, 요청 오버라이드·CLI `--answer-mode`·MCP `wiki_query(answer_mode=)`·Ask 드롭다운): `grounded`(지금 동작) | `best_effort`.
- `best_effort`: 근거 판정이 insufficient 여도 LLM 을 부른다. 프롬프트 `prompts/answer_best_effort.md`: 문서 사실은 [C#], **배경 지식 문장은 [BK] 표기**, 문서와 배경이 다르면 문서를 우선하고 차이를 적는다, 없는 값을 지어내지 않는다. claim_check 는 [BK] 문장을 `background` 로 분류해 미지원으로 세지 않는다(`claims_info.background` 수).
- 결과에 항상: `result_type` ∈ `grounded | best_effort | extractive | insufficient | error`, `refs` = LLM 에 실제로 전달된 근거 목록 `[{n, chunk_id, doc_id, ext_id, heading, chars, preview}]`(`refs_preview_chars` 튜닝, 기본 200). Web 은 결과 상단 배지 + "REF 목록" 접이식 + 복사(요청 16), CLI `--json` 과 MCP 도 같은 필드.
- `answer_guide.md` 는 그대로 두고 best_effort 전용 뼈대(핵심 / 문서 근거 / 배경 지식 / 미확인)를 새 md 에 둔다.

### 2.10 Web UI ↔ 서버 연동 (요청 10)
- 하네스 `tools/verify/verify_settings_sync.py`: 표면 10종(config · 역할 · 튜닝 · query_rules · rules.json · 카탈로그 · prompts · agents · schedule · server.json)마다 **UI 경로(POST) → 파일 → `config show --effective`/GET** 과 **파일 편집 → reload → GET** 을 확인한다. 어긋나는 것을 고친다.
- 알려진 어긋남: 모델만 골라도 provider 가 안 바뀜(§0.1) · `.env` 는 UI 가 없음 → `/api/env`(admin: 키 이름·설정 여부·마스킹 값·`LLMWIKI_*` 오버라이드) + "`.env` 다시 읽기"(`config reload --env`, 기존 값 덮어씀) · `api_keys.last_used` 미저장 · 프리셋 미리보기와 저장 구분 표시.
- `python -m llmwiki config fill-defaults [--tuning --rules]`: config.json 에 모든 Settings 키(역할별 `llm_roles.<role>` 의 provider/model/effort/정책/max_tokens/ensemble 포함), tuning.json 에 모든 튜닝 키, query_rules.json 에 모든 type 절, rules.json 에 모든 절을 **기본값으로 채워** 쓴다(있는 값은 유지). 저장소의 실제 파일과 `setup/*.example.*` 에 실행해 둔다.
- 카탈로그 전체 테스트: `POST /api/models/test_catalog` = 카탈로그의 enabled 모델마다 (provider, model) 로 ping(+`live=true` 면 완성 1회) → 표. CLI `models test --catalog [--live]`. 버튼은 Settings › 카탈로그 상단.

### 2.11 로그 총량 제한 (요청 11)
- 설정: `log_total_max_mb`(기본 500) · `log_limit_action`(`warn` | `prune` | `stop`) · `log_check_interval_s`(60) · `audit_max_mb`(20)/`audit_backups`(5) · `analysis_keep`(200). 대상 = `logs/`(llmwiki/error/build/query + audit.jsonl + analysis/) 합계; `data/requests`·`data/reruns` 는 이미 `requests_keep_days`·`rerun_keep` 로 제한(health 표에 함께 표시).
- 동작: 로거가 `log_check_interval_s` 마다 합계를 재고 넘으면 `warn` = error.log 한 줄 + health 경고 + Web 헤더 배너; `prune` = 오래된 백업(`*.log.N`, analysis 오래된 것)부터 지워 80% 아래로; `stop` = error.log 만 남기고 다른 핸들러를 잠근다(경고는 남긴다). `audit.jsonl` 은 RotatingFileHandler 로.
- CLI `logs status`, Web Observability › 로그에 총량·상한·상태.

### 2.13 사이드바 (요청 13)
- 사이드바의 모든 블록(프리셋 · 토글 그룹 · 오버라이드 · 협업 …)에 제목 클릭으로 접기/펴기, 상단 "모두 접기/펴기", 상태를 프로파일(`sidebar_collapsed`)에 저장.
- 토글 행: `[체크] 이름  품↑ 속↓ 토↓` — 배지를 이름 **뒤**로(`toggleRow` 순서 + CSS). 툴팁 문구는 유지.

### 2.14 기본 외부 바인드 (요청 14) — 전제 2건 포함
- `web_host` 기본 `0.0.0.0` (Settings·config.json·examples·BRINGUP). `security.mode=auto` 는 비-루프백 바인드에서 로그인을 켜므로 **첫 admin 생성 절차**를 INSTALL/BRINGUP 맨 앞에 둔다(`install.*` 이 `users add` 안내 출력).
- 전제 1 — overrides 화이트리스트: 요청 단위로 허용하는 키 = `Toggles` 전부 + `top_k_*`·`rerank_candidates`·`context_*`·`answer_*`·`debug_level`·`*_model`·`*_provider`·`*_effort`·`llm_roles`(model/provider/effort/ensemble 만)·`answer_mode`. URL·헤더·경로 계열(`*_base_url`, `*_url`, `openai_extra_headers`, `openai_api_key_header`, `corpus_dirs`, `data_dir`, `wiki_dir`, `*_dir`, `mcp_plugins_dir`)은 admin 역할 + `/api/models/test`·`/api/config` 에서만. 목록은 `security.json overrides.allow_extra`/`deny` 로 조정 가능.
- 전제 2 — 500 응답: 트레이스는 로그(`error.log`)에 남기고 클라이언트에는 `{"error","code":"internal","ref":run_id}`; admin 이거나 `server.json debug.expose_trace=true` 일 때만 트레이스 포함.

### 2.15 단계별 파라미터 스윕 (요청 15)
- 모듈 `llmwiki/sweep.py`. 입력: `request_id|last`, 재시작점(`rerun.POINTS` 중 하나), 역할 LLM 오버라이드(provider/model/effort), 파라미터 `key` 와 `values`(`start:stop:step` 또는 명시 목록, 토글이면 `[false,true]`), `repeats`(1). 각 값마다 **`rerun` 재생**으로 그 단계부터만 다시 돈다(앞 단계는 동일 → 바뀐 값의 효과만 분리). 결과 `data/sweeps/sw_<id>.json`: 값별로 단계 요약(단계 이름·ms·meta 핵심·순위 상위 N·컨텍스트 id·답변 본문·claim 수치·result_type).
- 비교: `sweep compare <id>` → 기준(첫 값) 대비 값마다 단계별 diff(`difflib`: 순위 목록·컨텍스트 id 집합·답변 텍스트·수치). Web Quality › "단계 스윕": 폼(요청 선택 · 재시작점 · 파라미터(레지스트리에서 그 단계 것만) · 범위/증가 · LLM 설정) → 잡 → **단계 × 값 격자**(셀 = 요약, 기준과 다르면 강조; 셀 클릭 = 전체 diff 패널; 값별 최종 산출물 열). CLI `sweep run|list|show|compare`, MCP `wiki_sweep`.
- 설정: `sweep_dir`(data/sweeps) · `sweep_keep`(30) · `sweep_max_values`(20) · `sweep_max_parallel`(1; LLM 호출 폭주 방지).

### 2.16 복사 버튼 (요청 16)
- `LW.copyText(text, label)`: ① `navigator.clipboard.writeText`(보안 컨텍스트) → ② 숨은 textarea + `document.execCommand('copy')` → ③ 실패 시 모달에 본문을 선택된 상태로 보여 Ctrl+C. 요청 14 로 `http://호스트` 접속이 기본이 되면 ①이 막히므로 ②③이 실제 경로다.
- 위치: Ask 결과 상단 "전체 복사"(답변 + REF + 단계 요약 markdown), 워터폴 각 줄 "⧉"(그 단계 meta/debug JSON 또는 markdown), REF 목록 복사, 분석 리포트 복사(기존)와 통일. `verify_browser.py` 에 복사 경로 ②를 headless 로 확인하는 항목.

### 2.17 정렬·검증 (요청 17)
- `verify_surface_align.py` 를 확장해 새 기능(answer_mode·llm_after_*·ensemble·sweep·stopwords·test_catalog·fill-defaults·logs status)이 CLI/Web/MCP 세 표면에 모두 있는지 표로 검사.
- 순서: 단위 → `verify_settings_sync` → `verify_cli` → `verify_web` → `verify_mcp` → `verify_responsive` → `verify_buttons` → `verify_browser` → 스트레스(test_concurrency) → `verify_soak` → `verify_monkey`. 결과 [VERIFICATION_0918.md](../2026-09-19/VERIFICATION_0918.md).

## 3. 실행 순서와 파일 소유(충돌 방지)

| 단계 | 작업 | 주요 파일 |
|---|---|---|
| A | 요청 2 불용어 | textutil.py · config.py(경로표) · setup/stopwords.example.json · check_env.py · docs |
| B | 요청 11 로그 제한 | logging_setup.py · config.py(로그 키) · health.py · auth.py(audit 로테이션) · analysis.py(prune) · server.py(GET logs) · cli.py(`logs status`) |
| C | 요청 4·13·16 프론트 | style.css · core.js · ask.js · observability.js · index.html · verify_responsive.py |
| E | 요청 6 앙상블 + 카탈로그 provider 해석 | providers.py · config.py(llm_roles/role_llm) · pipeline.py(`_llm_key`) · prompts.py · models_catalog.py · cli.py(`models ensemble`) |
| F | 요청 5·9·7 (질의 경로) | query_engine.py · answer.py · evidence.py · prompts.py · config.py(토글·answer_mode) · tuning.py · rerun.py · architecture.py · cli.py(플래그) · mcp.py(`wiki_query` 인자) |
| G | 요청 3 MCP | mcp.py · mcp_client.py · server.py(`_mcp`) · reqmgr DEFAULTS(mcp 절) · docs/MCP.md · verify_mcp.py |
| H | 요청 8 headless | headless.py · setup/config.example.headless.json · tests · docs |
| D | 요청 14 + 전제 2건 | config.py(apply_overrides 화이트리스트) · server.py(오류 응답) · security.example.json · docs |
| I | 요청 10 연동·기본값·카탈로그 테스트 + 새 기능 UI | settings.js · ask.js · server.py · cli.py(`config fill-defaults`) · verify_settings_sync.py |
| J | 요청 15 스윕 | sweep.py(신규) · server.py · quality.js · cli.py · mcp.py |
| K | 요청 1·12·17 | RELEASE_NOTES.md · TESTING_GUIDE.md · verify_surface_align.py · VERIFICATION_0918.md · README/BRINGUP |

A·B·C·E·F·G·H 는 병렬(같은 파일은 다른 영역만 편집), D 는 A·B 뒤, I·J 는 E·F 뒤, K 는 마지막.

## 4. 새 설정 키 총람 (구현하면서 확정 — BRINGUP §3.2 에 복사)

| 파일 | 키 | 기본 | 요청 |
|---|---|---|---|
| stopwords.json | `stopwords` | 코드 기본 목록 | 2 |
| server.json | `mcp.fed_cache_ttl_s / fed_list_timeout_s / source_timeout_s_default / bridge_timeout_s / max_k / max_doc_chars / plugin_rescan_s / instructions` | 300 / 5 / 10 / 120 / 50 / 20000 / 5 / (문구) | 3 |
| config.json toggles | `llm_after_fusion`, `llm_after_rerank`, `degrade_on_llm_failure` | false, false, true | 5, 7 |
| tuning.json | `fusion_llm_candidates`, `fusion_llm_drop_penalty`, `post_rerank_llm_k`, `refs_preview_chars` | 0(=rerank_candidates), 0.3, 0(=top_k_final×2), 200 | 5, 9 |
| config.json | `llm_roles.<role>.ensemble{…}`, `llm_ensemble_defaults{wait,timeout_s,min_results,prompt}` | 비활성, all/120/1/ensemble_merge | 6 |
| prompts/ | `fusion_review.md`, `rerank_review.md`, `ensemble_merge.md`, `answer_best_effort.md` | 기본 문구 생성 | 5, 6, 9 |
| agents.json | `env_passthrough`, `prompt_mode` 기본 | 목록, stdin | 8 |
| config.json | `answer_mode` | grounded | 9 |
| config.json | `log_total_max_mb`, `log_limit_action`, `log_check_interval_s`, `audit_max_mb`, `audit_backups`, `analysis_keep` | 500, warn, 60, 20, 5, 200 | 11 |
| config.json | `web_host` | **0.0.0.0** | 14 |
| security.json | `overrides.allow_extra`, `overrides.deny` | [], [] | 14 |
| server.json | `debug.expose_trace` | false | 14 |
| config.json | `sweep_dir`, `sweep_keep`, `sweep_max_values`, `sweep_max_parallel` | data/sweeps, 30, 20, 1 | 15 |
| data/profiles.json | `sidebar_collapsed` | {} | 13 |
