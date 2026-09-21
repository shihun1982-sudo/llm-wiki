# 인수인계 — 2026-09-16 시점의 완료/미완료 작업

이 문서는 **이 작업을 이어받는 사람(또는 다른 LLM)** 을 위한 것이다. 2026-09-16 세션에서 무엇을 끝냈고,
무엇이 남았고, 남은 것을 어떤 순서로 어떻게 확인하면 되는지를 적는다.
전체 구조·운영 방법은 [README.md](../../../README.md) → [BRINGUP_GUIDE.md](../../BRINGUP_GUIDE.md) 를 먼저 읽고 이 문서를 본다.

- 직전 세션의 검증 기록: [VERIFICATION_0915.md](../2026-09-15/VERIFICATION_0915.md) (결함 29건과 수정 내역)
- 이 세션에서 새로 만든 문서: [OPTIMIZATION_GUIDE.md](../../OPTIMIZATION_GUIDE.md) (자동 생성)

> **갱신 (2026-09-16, 이어받은 세션)** — 아래 §2 의 남은 작업 **2.1~2.5 를 모두 끝냈다.**
> 결과와 그 과정에서 찾은 결함(제품 2건 · 하네스 7건)은 **[VERIFICATION_0916.md](VERIFICATION_0916.md)** 에 있다.
> 현재 상태: 단위 163/163 · CLI 222/222 · Web 259/259 · **MCP 종단 98/98** · UI 배선 OK · 브라우저 OK.
> 아래 §2 본문은 "무엇이 왜 남아 있었는가" 의 기록으로 남겨 둔다 — 각 항목의 결론은 소제목의 ✅ 줄을 본다.

---

## 1. 이 세션에서 완료한 것

### 1.1 최적화 자료 묶음 — "볼 수 있는 모든 정보를 LLM 에게 한 파일로"

사용자 요구는 "전체 구조 그림과 각 흐름의 토글·튜닝 설정이 어느 단계에 어떻게 영향을 주는지 적힌 가이드를
만들고, analysis mode 결과와 함께 LLM 에게 주면 LLM 이 개선점을 찾아 주게 하라" 였다.

| 무엇 | 어디 |
|---|---|
| 가이드 생성기 (구조 + 흐름별 단계표 + 렌즈별 조절 순서) | `llmwiki/optimize.py` `guide_markdown()` |
| 묶음 생성기 (A 설정 스냅샷 · B 질의 실측 · C 지시문 · D 손잡이 지도) | `llmwiki/optimize.py` `bundle_markdown()` |
| 끼워 넣는 문서의 제목 단계 낮추기 (목차 충돌 방지, 코드펜스 보호) | `llmwiki/optimize.py` `demote()` |
| 생성된 가이드 문서 | `docs/OPTIMIZATION_GUIDE.md` (134줄, `arch doc` 로 재생성) |
| CLI | `python -m llmwiki arch doc` · `python -m llmwiki optimize <id\|last> [--focus] [--out]` |
| Web API | `GET /api/optimize/bundle?request_id&focus[&format=json]` · `GET /api/optimize/guide` |
| Web UI 버튼 | Ask › 📊 상세 분석 리포트 › **📦 최적화 자료 묶음 다운로드** / **묶음 복사** / **손잡이 지도만 보기** (`llmwiki/web/static/js/ask.js`) |
| 프롬프트 샘플이 코드블록을 깨거나 지시문으로 읽히지 않게 | `llmwiki/analysis.py` `_max_backticks()` + 부록 A 앞의 경고 문장 |
| 회귀 테스트 3개 | `tests/test_tuning_arch.py` `test_guide_markdown` · `test_demote_keeps_code_fences` · `test_optimize_bundle` |

검증 결과: `python -m unittest tests.test_tuning_arch` 11개 통과. 실제 서버에서 `/api/optimize/guide`(12,650자),
`/api/optimize/bundle`(52,971자, `Content-Disposition` 첨부 파일명 포함), `format=json`, 없는 요청 → 404 확인.
묶음의 최상위 제목이 하나뿐인 것(목차 충돌 없음)도 확인했다.

### 1.2 프리셋 점검 (사용자 요청 1번)

프리셋(체크 → 아래 토글에 즉시 반영, 요청 단위)은 **정상 동작**한다. 버그 없음.
격리 서버에서 확인한 실측: `speed` 프리셋은 29단계 → 26단계, 962ms → 48ms 로 줄었고,
프리셋을 쓴 뒤에도 `config.json` 의 서버 기본 토글은 그대로였다(요청 단위 적용이 지켜짐).
Web UI 쪽도 체크 시 토글 반영 · 해제 시 원복 · `by-preset` 표시 · CLI 등가 문자열 갱신이 모두 동작했다.

### 1.3 MCP 개선 (사용자 요청: "다수 LLM 이 붙고, 다른 RAG 를 MCP 로 확장해 쓸 것")

`llmwiki/mcp.py` 에 다음을 넣었다. **모두 코드에는 반영됐고 단위 동작은 확인했으나, 종단 검증은 1.4 의 이유로 중단했다.**

| 개선 | 내용 | 왜 |
|---|---|---|
| 프로토콜 버전 협상 | `SUPPORTED_PROTOCOLS = (2025-06-18, 2025-03-26, 2024-11-05)`. 클라이언트가 요청한 버전이 이 안에 있으면 그대로, 아니면 우리 최신을 돌려준다 | 예전에는 클라이언트가 보낸 값을 그대로 되돌려 줘서, 우리가 지원하지 않는 버전도 지원한다고 답했다 |
| 도구 annotations | `ANNOTATIONS` 표 → `tools/list` 의 각 도구에 `title` 과 `readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint` | 붙는 LLM 이 "이 도구가 무엇을 바꾸는가" 를 스스로 판단한다. 읽기 도구 10종은 readOnly, `wiki_propose`/`wiki_feedback` 만 쓰기 |
| 인자 검증 | `validate_args()` — `inputSchema` 의 required·type·enum 위반을 `isError` 로 되돌린다 (문자열 숫자는 허용) | 예전에는 `wiki_query` 를 빈 질문으로 부르면 조용히 빈 결과가 나와, 붙는 LLM 이 실패 원인을 알 수 없었다 |
| 도구 이름 중복 제거 | `list_tools()` 가 중복 이름을 버리고 `_FED_CACHE["_list"]` 에 기록 | 플러그인·페더레이션이 같은 이름을 만들면 클라이언트가 어느 것을 부를지 알 수 없다 |
| 페더레이션 오류 노출 | `wiki_sources` 결과에 `federated_tools`·`federation_errors`, `check=true` 면 페더레이션 캐시도 갱신 | 예전에는 원격이 죽으면 도구가 조용히 사라졌다 |
| 부가 메서드 응답 | `resources/list`·`resources/templates/list`·`prompts/list`·`logging/setLevel` 을 각각 올바른 형태로 | capabilities 에 없어도 그냥 부르는 클라이언트가 오류로 멈추지 않게 |
| 자가 점검 | `python -m llmwiki mcp --doctor [--check-sources] [--json]` — 도구 목록·스키마·설명·플러그인 적재·외부 소스 선언과 연결·검색 채널 토글·페더레이션·재귀 방지·인증 수단·전송 설정을 한 번에 점검 (`llmwiki/mcp.py` `doctor()`, 출력 렌더러 `llmwiki/cli.py` `_mcp_doctor_text()`) | bring-up 때 "왜 안 붙는가" 를 서버 쪽에서 먼저 답하기 위한 것 |

`mcp --doctor` 를 실제 환경에서 실행해 11개 항목 전부 OK 를 확인했다.

### 1.4 MCP 종단 검증 하네스 (신규, **미완성**)

`tools/verify/verify_mcp.py` 를 새로 만들었다. 검증 범위는 파일 상단 주석에 적혀 있다 —
전송 3종(stdio/HTTP/브리지), 프로토콜 적합성, 도구 12종 호출, 잘못된 호출, 인증, 확장(플러그인·페더레이션·외부 RAG),
동시 접속, 자가 점검 명령.

지원 변경: `tools/verify/verify_buttons.py` 의 `isolated_env(port, extra_cfg, extra_files, serve)` 에
설정 덮어쓰기·추가 파일·서버 없이 env 만 받기 옵션을 넣었다(기존 호출부는 그대로 동작).

**실행 결과: [1] 프로토콜 적합성 15개 항목 전부 OK.** 그 뒤 구간은 아래 결함 때문에 완주하지 못했다.

---

## 2. 남은 작업 (우선순위 순)

### 2.1 `verify_mcp.py` 를 완주시키기 — 먼저 하네스의 결함을 고칠 것

> ✅ **완료 (2026-09-16)** — 98/98 통과. 아래 1번 외에 하네스 결함이 6건 더 있었고, **제품 결함도 2건** 나왔다
> (stdio `Content-Length` 가 한글 본문에서 다음 메시지를 삼키던 것, `method` 없는 본문을 조용히 버려
> 클라이언트가 응답을 기다리며 멈추던 것). 전부 [VERIFICATION_0916.md](VERIFICATION_0916.md) §1 에 있다.
> 아래 3번(느림)은 `--quick`(87항목) 으로 갈음했다 — mock LLM 환경에서는 전체도 수십 초라 전용 코퍼스까지는 필요 없었다.

**알려진 결함 (제품이 아니라 하네스의 문제).**

1. `Stdio._read()` 가 `self.proc.stdout.readline()` 을 그대로 부른다. `readline()` 은 블로킹이라
   `while time.time() < end` 가 다시 평가되지 않는다 → **타임아웃이 동작하지 않고 영원히 멈춘다.**
   고치는 법: 읽기 전용 스레드가 `queue.Queue` 에 줄을 넣게 하고 `q.get(timeout=…)` 으로 받는다.
   참고 구현이 이미 있다 — 이 세션에서 쓴 진단 스크립트와 같은 구조(`threading.Thread(target=lambda: [q.put(l) for l in proc.stdout])`).
2. 첫 실행이 17분 넘게 진행되었는데 `print` 가 블록 버퍼링돼 진행 상황이 전혀 보이지 않았다.
   → `sys.stdout.reconfigure(line_buffering=True)` 를 넣어 고쳤다(반영 완료).
3. 전체 색인(636MB)을 임시 폴더로 복사하고 그 위에서 `wiki_query` 를 stdio·HTTP 양쪽으로 13종씩 돌리므로 느리다.
   `--quick`(HTTP 쪽 도구를 4종으로, 동시성 8→4) 을 넣어 두었지만, 더 줄이려면
   **작은 전용 코퍼스로 빌드한 격리 환경** 옵션을 추가하는 편이 낫다. 다만 `wiki_doc ISSUE-2001` 같은
   기대값(`TOOL_CALLS`)이 실제 코퍼스에 의존하므로 같이 바꿔야 한다.

**참고 실측**(실 환경, 실제 LLM): stdio `initialize` 0.1s · `tools/list` 0.0s · `wiki_status` 0.4s ·
`wiki_search` 0.0s · `wiki_query` 20.8s. 즉 느린 것은 `wiki_query` 뿐이므로, 질의 호출 수를 줄이면 전체가 빨라진다.

**완주시킨 뒤 확인할 것** — 하네스가 자동으로 판정하지만, 아래는 특히 눈으로 확인할 값이다.

- `annotations` 가 12/12 도구에 붙는가, `wiki_propose` 만 `readOnlyHint=false` 인가
- 잘못된 호출 7종이 **크래시 없이** `isError` 로 돌아오는가 (`필수 인자`, `중 하나`, `integer`, `unsupported kind`, `unknown tool`)
- HTTP: 익명 역할로 initialize, 잘못된 Bearer 거부, `Mcp-Session-Id` 유지, 알림만 보내면 202, GET /mcp → 405
- 확장: 플러그인 `verify_echo` 등록·호출·인자 검증, 깨진 플러그인이 다른 플러그인을 막지 않는지,
  페더레이션 `mock__search` 노출·중계, expose 목록 밖 도구 거부, 없는 소스 거부
- 재귀 방지: `LLMWIKI_FEDERATION_DEPTH=1` 로 띄운 프로세스는 `__` 도구를 내놓지 않고 중계도 거부하는가
- 동시성: N 클라이언트 동시 `wiki_query` 전부 성공

결과는 `tools/verify/verify_mcp_result.json` 에 남는다. 실패 항목이 있으면 종료 코드 1.

### 2.2 MCP 문서 갱신

> ✅ **완료** — [MCP.md](../../MCP.md) 는 §2 도구 12개와 힌트 열, §2.1 인자 검증(메시지 예 표), §2.2 annotations·버전 협상·프레이밍,
> §2.3 키를 클라이언트마다, §3.1 429/503 의 뜻, §4 `mcp --doctor`(실제 출력 + 항목별 대처), §5 문제 해결 확장,
> §6 다른 RAG 를 붙이는 세 방법(비교표), §6.1 플러그인 작성법, §7 검증으로 다시 썼다.
> [RAG_FEDERATION.md](../../RAG_FEDERATION.md) 는 그림의 도구 수·인자 검증 줄, §6 검증(=`verify_mcp.py` 가 확장 경로를 종단으로 돈다),
> §7 첫 행(doctor) 을 넣었다. [BRINGUP_GUIDE.md](../../BRINGUP_GUIDE.md) §4.5/§4.6 에 "연결 확인" 단계로 doctor 를 넣었다.

[docs/MCP.md](../../MCP.md) 와 [docs/RAG_FEDERATION.md](../../RAG_FEDERATION.md) 에 아직 **1.3 의 개선이 반영되어 있지 않다.**
넣어야 할 것:

- 도구가 12종이라는 것(문서에는 9개로 적혀 있다) 과 각 도구의 annotations(읽기/쓰기 구분)
- 인자 검증이 하는 일과 오류 메시지 형태 — 붙는 LLM 이 실패를 어떻게 읽는지
- 프로토콜 버전 협상 범위
- **`mcp --doctor`** 절 (bring-up 체크리스트에 넣을 것. 출력 예시를 그대로 붙일 것)
- 확장 절차를 "다른 RAG 를 붙이는 3가지 방법" 으로 정리:
  ① `retrieve` 매핑 + 토글 `external_rag` → 우리 질의의 검색 채널로 융합
  ② `expose` + 토글 `mcp_federation` → 그 서버의 도구를 `<source>__<tool>` 로 우리 tools/list 에 중계
  ③ `ingest` 매핑 → raw data 를 문서로 내려받아 일반 색인
  각각 "언제 쓰나 / 설정 예 / 확인 명령 / 실패 시 증상" 을 표로.
- 플러그인 도구 작성법(`plugins/mcp_tools/*.py` 의 `register(add_tool)`, 인자 스키마 필수)
- 검증: `python tools/verify/verify_mcp.py [--quick]` 를 [VERIFICATION_0915.md](../2026-09-15/VERIFICATION_0915.md) 의 스크립트 목록과
  README §4 검증 행(이미 추가함)에 맞춰 설명

그리고 [BRINGUP_GUIDE.md](../../BRINGUP_GUIDE.md) §4.4~4.6 에 `mcp --doctor` 를 "연결 확인" 단계로 넣는다.

### 2.3 최적화 묶음 문서화 마무리

> ✅ **완료** — [BRINGUP_GUIDE.md](../../BRINGUP_GUIDE.md) §7.0 은 이미 있었고, [ANALYSIS_MODE.md](../../ANALYSIS_MODE.md) §3-3 에
> "리포트만으로는 부족한 이유 → `optimize` 묶음" 안내를, [WEB_UI.md](../../WEB_UI.md) §5 에 📦 버튼 3종과
> "📊 리포트와 📦 묶음의 차이" 를 넣었다. 더해서 `verify_web.py` 에 `/api/optimize/guide`·`/api/optimize/bundle`
> 4항목을 추가해 그 버튼들이 부르는 경로가 검증에 들어오게 했다.

README 는 이미 갱신했다(문서 안내 §0 의 읽는 순서 행과 문서 설명, §4 CLI 표의 `arch doc`/`optimize`/`mcp --doctor`/검증 스크립트).
남은 것:

- [BRINGUP_GUIDE.md](../../BRINGUP_GUIDE.md) §7(평가 기준선과 튜닝)에 "최적화 묶음을 만들어 LLM 에게 주는 절차" 를 추가
- [ANALYSIS_MODE.md](../../ANALYSIS_MODE.md) 에서 `optimize` 묶음으로 넘어가는 링크 한 줄
- [WEB_UI.md](../../WEB_UI.md) Ask 절에 📦 버튼 3종 설명

### 2.4 전체 검증 스위트 재실행

> ✅ **완료** — 결과는 [VERIFICATION_0916.md](VERIFICATION_0916.md) §0·§2.
> 아래에서 걱정한 `verify_buttons.py` 의 새 버튼 문제는 **수집 대상이 `index.html` 의 정적 `<button id=…>` 뿐**이라
> 동적으로 그려지는 Ask 패널 버튼(`qa-*`·`<a class="button">`)은 애초에 잡히지 않는 것이었다. 넓히는 대신
> 범위를 스크립트 주석에 명시하고, 그 버튼들이 부르는 API 를 `verify_web.py` 로 옮겨 확인했다(§2.3).
> 참고: `run.bat test` 는 프로젝트 루트에서 `.\run.bat test` 로 부르거나 `python -m unittest discover -s tests` 를 쓴다.

이 세션에서 `llmwiki/mcp.py`·`llmwiki/analysis.py`·`llmwiki/web/server.py`·`llmwiki/cli.py`·
`llmwiki/web/static/js/ask.js`·`tools/verify/verify_buttons.py` 를 고쳤다. 아래를 순서대로 돌려
[VERIFICATION_0915.md](../2026-09-15/VERIFICATION_0915.md) §6 과 같은 형식으로 결과를 기록할 것.

```bat
run.bat test                              :: unittest (test_tuning_arch 에 3개 추가됨)
python tools\verify\verify_cli.py
python tools\verify\verify_web.py
python tools\verify\verify_ui_wiring.py
python tools\verify\verify_buttons.py     :: 웹 UI 버튼 전수 (새 📦 버튼 3개가 EXPECT 에 없으므로 추가 필요)
python tools\verify\verify_browser.py
python tools\verify\verify_monkey.py
python tools\verify\verify_mcp.py --quick :: 2.1 의 결함을 고친 뒤
```

`verify_buttons.py` 의 `EXPECT`/`HEAVY` 표에 새 버튼(`qa-bundle-copy` 와 두 개의 `<a class="button">`)을
넣어야 "모든 버튼을 눌러 본다" 는 기준이 유지된다. 링크는 `<a>` 라 버튼 수집 정규식(`<button id=…>`)에
잡히지 않는다 — 수집 대상을 넓히든지, 문서에 "이 둘은 링크라 별도 확인" 이라고 적을 것.

### 2.5 사용자가 물었지만 아직 답하지 못한 것

> ✅ **답함 (2026-09-16)** — 결론: **사용법은 맞다.** 다만 화면이 읽히지 않는 세 가지를 고쳤다 —
> (1) "원 판정 sufficient + 기대 문서 미해결" 이 모순처럼 보이던 것을 한 문장 안내와 배너로,
> (2) 목표 청크가 수십 개일 때 같은 표가 반복되던 것을 `forensic_targets_shown`(3) 만 펼치도록,
> (3) 수정안을 confidence 내림차순으로 정렬하고 `forensic_suggestion_min_confidence`(0.5) 미만은 접도록.
> 두 임계값은 `tuning.json` 키다. 상세 [VERIFICATION_0916.md](VERIFICATION_0916.md) §3 · [FORENSIC.md](../../FORENSIC.md) §2.4.

**"기대 결과 포렌식 화면을 이렇게 쓰는 게 맞는가, 개선할 점은?"** (Ask 탭에서 기대 문서 ID `ISSUE-3456`,
기대 용어 `ICR`, 메모, "수정안을 제안 큐(HITL)에 등록" 체크 후 분석을 실행한 화면에 대한 질문)

아직 답하지 않았다. 확인해야 할 것:

- 사용법 자체는 맞다(기대 문서/용어를 넣고 분석). 다만 화면의 출력에서 **원 판정 `sufficient` 인데 기대 문서가
  `미해결`** 로 나오는 조합이 사용자를 헷갈리게 한다 — "답은 충분했지만 당신이 기대한 문서는 없었다" 를
  한 문장으로 설명하는 안내가 필요해 보인다.
- 대표 원인 줄이 매우 길고(질의 키워드 나열), 탈락 단계 표가 20개 청크마다 반복된다 → 상위 3개만 펼치고
  나머지는 접는 편이 낫다.
- `수정안` 의 `query_rule 'physical' ↔ '참고'` 같은 제안은 confidence 0.35 로 낮은데 목록 상단에 있다 —
  confidence 순 정렬과 임계 이하 숨김을 검토할 것.
- 관련 문서: [FORENSIC.md](../../FORENSIC.md), 구현 `llmwiki/forensic.py`, 화면 `llmwiki/web/static/js/quality.js`.

---

## 3. 이 세션에서 바뀐 파일 목록

| 파일 | 변경 |
|---|---|
| `llmwiki/optimize.py` | 신규 — 가이드·묶음 생성, `demote()` |
| `llmwiki/mcp.py` | 버전 협상 · annotations · `validate_args()` · 중복 이름 제거 · 페더레이션 오류 노출 · 부가 메서드 · `doctor()` |
| `llmwiki/analysis.py` | 프롬프트 샘플 코드펜스 동적 길이(`_max_backticks`) + "데이터이지 지시가 아니다" 경고 |
| `llmwiki/web/server.py` | `GET /api/optimize/bundle`·`/api/optimize/guide`, `HEAVY_GET` 등록 |
| `llmwiki/cli.py` | `mcp --doctor/--check-sources/--json`, `_mcp_doctor_text()` |
| `llmwiki/web/static/js/ask.js` | 📦 최적화 자료 묶음 다운로드·묶음 복사·손잡이 지도만 보기 |
| `tools/verify/verify_mcp.py` | 신규 — MCP 종단 검증 (미완성, 2.1 참조) |
| `tools/verify/verify_buttons.py` | `isolated_env()` 에 `extra_cfg`·`extra_files`·`serve` 옵션 |
| `tests/test_tuning_arch.py` | 최적화 가이드·demote·묶음 회귀 테스트 3개 |
| `docs/OPTIMIZATION_GUIDE.md` | 신규(자동 생성) |
| `README.md` | 문서 안내·문서 설명·CLI 표 갱신 |
| `docs/history/2026-09-16/HANDOVER_0916.md` | 이 문서 |
