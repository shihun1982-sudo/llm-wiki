# 검증 보고서 — 2026-09-16 (MCP 종단 검증 완주 · 포렌식 화면 정리)

> 이 문서는 **다른 환경에서 같은 검증을 다시 돌려 같은 결론에 이르기 위한** 기록이다.
> 직전 회차는 [VERIFICATION_0915.md](VERIFICATION_0915.md), 남은 작업 목록은 [HANDOVER_0916.md](HANDOVER_0916.md).
> 전체 구조·운영은 [README.md](../README.md) → [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md).

## 0. 한 장 요약

| 무엇 | 명령 | 결과 |
|---|---|---|
| 단위 테스트 | `python -m unittest discover -s tests` (= `run.bat test`) | **163/163 통과** (88s) |
| CLI 전수 | `python tools\verify\verify_cli.py` | **222/222 통과** |
| Web API 전수 | `python tools\verify\verify_web.py` | **259/259 통과** |
| UI 배선 정적 검사 | `python tools\verify\verify_ui_wiring.py` | **OK** (누락 참조 0 · 서버에 없는 API 경로 0) |
| **MCP 종단** | `python tools\verify\verify_mcp.py` | **98/98 통과** (`--quick` 는 87/87) |
| 브라우저 렌더 | `python tools\verify\verify_browser.py` | **OK** (31탭, 콘솔 오류 0) |
| 버튼 전수 | `python tools\verify\verify_buttons.py` | **76/76 OK** |
| 무작위 입력 내성 | `python tools\verify\verify_monkey.py` | **OK** — 1,205 요청 · **500 오류 0 · 서버 생존 · 결함 0**. 폭격 직후 거절에 관해서는 §2.6 의 미해결 관찰 하나 |

이 회차의 핵심은 **[HANDOVER_0916.md](HANDOVER_0916.md) §2.1 의 "완주하지 못한 MCP 종단 검증" 을 끝낸 것**이다.
완주시키는 과정에서 제품 결함 2건과 하네스 결함 5건이 나왔고 모두 고쳤다(§1).

## 1. 이 회차에 찾아 고친 결함

### 1.1 제품 결함 (고객 환경에서 실제로 문제가 되는 것)

| # | 무엇 | 증상 | 원인 | 고친 곳 |
|---|---|---|---|---|
| P1 | **stdio `Content-Length` 프레이밍이 한글 본문에서 깨진다** | 클라이언트가 Content-Length 로 한글 인자를 보내면 **응답이 오지 않고 그 뒤 스트림이 통째로 어긋난다**(다음 메시지까지 삼켜 `Extra data` 파싱 오류) | 헤더의 Content-Length 는 **바이트 수**인데 `sys.stdin` 은 텍스트 스트림이라 `read(n)` 이 **문자 수**를 센다. 한글은 바이트 > 문자이므로 매번 초과해서 읽었다 | `llmwiki/mcp.py` `_read_message()` — 인코딩 길이를 세며 필요한 만큼만 읽는다 |
| P2 | **`method` 가 없는 JSON-RPC 본문을 조용히 버린다** | 클라이언트가 오타 난 본문을 보내면 서버가 202 로 아무것도 돌려주지 않아 **클라이언트가 응답을 기다리며 멈춘다** | `id` 가 없으면 알림으로 보고 `None` 반환 → HTTP 202 | `llmwiki/mcp.py` `handle()` — `method` 없는 본문은 `-32600 invalid request`(id 없으면 `id: null`) |

재현과 확인:

```bat
python -m unittest tests.test_features_0914.McpStdioFramingTest    :: P1·P2 회귀 테스트 3개
python tools\verify\verify_mcp.py --quick                          :: [1] 에 한글 프레이밍·-32600 항목
```

P1 은 한글 코퍼스를 쓰는 이 프로젝트에서 **실제로 걸리는** 결함이다 — 기대 문서 ID나 질문에 한글이 들어간
`tools/call` 을 Content-Length 로 보내는 클라이언트(LSP 계열 프레이밍을 쓰는 구현)는 첫 호출부터 멈춘다.
줄 단위(`{…}\n`) 프레이밍만 쓰는 클라이언트는 영향이 없어서 지금까지 드러나지 않았다.

### 1.2 하네스 결함 (검증 도구 자체의 문제 — 제품은 정상이었다)

| # | 무엇 | 왜 문제였나 | 고친 것 |
|---|---|---|---|
| H1 | `Stdio._read()` 가 `proc.stdout.readline()` 을 직접 호출 | 블로킹이라 `while time.time() < end` 가 다시 평가되지 않는다 → **타임아웃이 동작하지 않고 영원히 멈춘다** | 읽기 전용 스레드 → `queue.Queue`, `q.get(timeout=…)`. stderr 도 함께 비운다(파이프가 차면 서버가 stderr 쓰기에서 멈춘다) |
| H2 | 자식 stdin 이 `text=True` 기본 개행 변환 | Windows 에서 `"\n"` → `"\r\n"` 이라 우리가 적은 `"\r\n\r\n"` 이 `"\r\r\n\r\r\n"` 이 되고, 자식의 universal-newline 변환을 거치면 **빈 줄이 하나 더 생겨 본문이 두 칸 밀린다** | `proc.stdin.reconfigure(newline="")` |
| H3 | 잘못된 Bearer 토큰에 한글(`lwk_없는키_000`)을 썼다 | HTTP 헤더는 latin-1 만 담을 수 있다 → 클라이언트에서 `UnicodeEncodeError` 로 **검증이 중단**된다 | ASCII 토큰으로 |
| H4 | 격리 서버를 `127.0.0.1` 에 띄우면서 `security.json mode` 를 `auto` 로 두었다 | `auto` + 루프백 = **인증이 꺼진다**(모든 요청이 로컬 admin) → "잘못된 키 거부" 같은 인증 항목이 전부 무의미하게 통과 | 격리 환경의 `security.json` 에 `mode: "on"` 을 명시 |
| H5 | API 키를 **서버 기동 뒤**에 발급했다 | 서버는 기동 시 `security.json` 을 읽어 들고 있으므로 나중에 추가한 키를 모른다 → 정상 키가 401 | 키를 먼저 발급하고 서버를 띄운다 |
| H6 | **API 키 하나로 N 개 클라이언트**를 흉내 냈다 | 동시성 제한이 사용자(키) 단위라 `max_parallel_per_user`(기본 3)에 걸려 4번째부터 429 — 제품이 옳고 검증이 틀렸다 | 클라이언트마다 키를 따로 발급(실제 모습). 더해서 "한 키로 한계를 넘기면 **429 로만** 거부되고 5xx·크래시가 없다" 를 별도 항목으로 확인 |
| H7 | 깨진 플러그인을 일부러 심어 놓고 `doctor` 의 `ok is True` 를 기대했다 | doctor 가 그 결함을 잡아내는 것이 정상이므로 모순된 기대 | 결함 환경은 `ok=false` + 오류 항목이 `플러그인` 하나뿐인지, 결함 없는 환경은 `ok=true` 인지 각각 확인 |

H4 는 검증 도구만의 문제가 아니라 **운영에서도 오해하기 쉬운 동작**이라 문서에 적었다
([MCP.md](MCP.md) §3, [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) §4.5).

## 2. 항목별 실행과 결과

### 2.1 MCP 종단 검증 — `tools/verify/verify_mcp.py`

```bat
python tools\verify\verify_mcp.py              :: 전체 98 항목
python tools\verify\verify_mcp.py --quick      :: 87 항목 (HTTP 쪽 도구 반복·동시성 규모 축소)
python tools\verify\verify_mcp.py --keep       :: 임시 폴더를 남긴다 (실패 원인 추적용)
```

격리 환경에서 돈다 — 설정과 색인 DB 를 임시 폴더로 복사하고 **mock LLM** 으로 서버를 띄우므로 실제 데이터가 바뀌지 않는다.
결과는 `tools/verify/verify_mcp_result.json` 에 남고, 실패가 있으면 종료 코드 1.

| 구간 | 확인한 것 | 결과 |
|---|---|---|
| [1] 프로토콜 (stdio) | initialize · **버전 협상 3종**(지원/미지원/구버전) · capabilities · instructions · ping · `tools/list` 12종 · 모든 inputSchema 가 object · **annotations 12/12** · `wiki_propose` 만 `readOnlyHint=false` · 이름 중복 없음 · `resources/list`·`prompts/list` 빈 목록 · 없는 메서드 `-32601` · **두 가지 프레이밍(줄 단위·Content-Length, 한글 포함)** · 배치 · 깨진 JSON 뒤에도 생존 | 20/20 |
| [2] 도구 호출 (stdio·HTTP) | 13회 호출 전부 `isError` 아님 + 기대 문자열 포함, `wiki_query` 의 `request_id`/`query_id` 로 `wiki_feedback` 연계 | 통과 |
| [3] 잘못된 호출 (stdio·HTTP) | 필수 인자 누락 · 공백 문자열 · enum 위반 · 타입 위반 · 없는 kind · 없는 도구 → **크래시 없이 `isError`** / 없는 문서는 오류가 아니라 `not found` | 7/7 |
| [4] Streamable HTTP · 인증 | 익명(anonymous_role) initialize · `Mcp-Session-Id` 발급과 유지 · 알림만 → 202 · 배치 · **`method` 없는 본문 → -32600** · `GET /mcp` → 405 · **잘못된 Bearer → 401** · API 키로 `tools/list` | 9/9 |
| [5] 동시 접속 | 서로 다른 API 키 6개가 동시에 `wiki_query` → 전부 성공. 한 키로 한계를 넘기면 429(`per_user_limit`)로만 거부 | 2/2 |
| [6] 브리지 | stdio→원격 HTTP 의 initialize·tools/list·tools/call, 원격 인증 실패를 JSON-RPC 오류로 | 4/4 |
| [7] 확장 | 플러그인 등록·호출·인자 검증, **깨진 플러그인이 다른 플러그인을 막지 않음**, 페더레이션 `mock__search` 노출·중계, expose 목록 밖 도구 거부, 없는 소스 거부, `wiki_sources` 의 `plugins.errors`·`federated_tools`·소스 연결 상태, 외부 RAG 직접 검색과 질의 융합, **재귀 방지**(하위 프로세스는 `__` 도구를 내놓지도 중계하지도 않음) | 14/14 |
| [8] 자가 점검 | 결함 없는 환경 `ok=true` · 결함 환경은 `ok=false` 이고 오류가 `플러그인` 하나뿐 · 도구 목록에 확장 포함 · 페더레이션 보고 · 사람이 읽는 출력 · `--client-config` 3종 | 7/7 |

참고 실측(mock LLM 기준): 전체 실행이 수십 초. 실 LLM 환경에서는 `wiki_query` 한 건이 20초 내외이므로
질의 호출 수가 곧 전체 시간이다 — 빠르게 보려면 `--quick`.

### 2.2 단위 테스트

`python -m unittest discover -s tests` → **163/163 통과**. 이 회차에 추가된 것:

| 테스트 | 무엇을 막는가 |
|---|---|
| `test_features_0914.McpStdioFramingTest.test_line_and_content_length_framing` | 두 프레이밍이 모두 파싱되는가 |
| `…::test_content_length_counts_bytes_not_characters` | **P1** — 한글 프레임이 다음 메시지를 삼키지 않는가 |
| `…::test_message_without_method_is_invalid_request` | **P2** — `method` 없는 본문이 `-32600` 인가 |
| `test_features_0914.ForensicExpectTest.test_expect_report_is_ordered_and_explains_unresolved` | 포렌식 리포트의 미해결 안내·수정안 정렬·목표 정렬(§3) |

### 2.3 CLI 전수 — 222/222

`python tools\verify\verify_cli.py`. 격리 임시 환경에서 모든 CLI 명령을 실행한다.

### 2.4 Web API 전수 — 259/259

`python tools\verify\verify_web.py`. 이 회차에 **최적화 자료 묶음** 4항목을 추가했다
(Ask 의 📦 버튼 3종이 부르는 경로):

| 항목 | 확인 |
|---|---|
| `GET /api/optimize/guide` | 손잡이 지도 12,650자 |
| `GET /api/optimize/bundle?request_id&focus` | 49,510자, **코드펜스 밖 최상위 제목이 정확히 1개**(끼워 넣은 문서들의 목차가 충돌하지 않는다) |
| `…&format=json` | `markdown` 키 |
| 없는 request_id | 404 |

> 주의: 묶음에 `analysis_mode` 질의의 리포트가 들어가면 **프롬프트 샘플 코드블록 안에 `# ` 로 시작하는 줄**이 있다.
> 제목 개수를 셀 때 코드펜스를 건너뛰지 않으면 잘못 세게 된다(이 회차에 실제로 한 번 잘못 셌다).

### 2.5 UI 배선 — OK

`python tools\verify\verify_ui_wiring.py`. JS 가 참조하는 `#id` 누락 0, 서버에 없는 API 경로 0.

### 2.6 브라우저 · 버튼 · 몽키

```bat
python tools\verify\verify_browser.py    :: Edge headless 렌더
python tools\verify\verify_buttons.py    :: 정적 버튼 전수 클릭
python tools\verify\verify_monkey.py     :: 무작위·악의적 입력 내성
```

결과: 브라우저 **OK**(31탭 렌더, 콘솔 오류 0) · 버튼 **76/76 OK** · 몽키 **RESULT OK**
(1,205 요청 / 292초, 상태 코드 `200×572 · 400×203 · 404×164 · 503×129 · 501×68 · 연결끊김×63 · 405×6`,
**500 오류 0 · 서버 생존 · 결함 0건**).

#### 미해결 관찰 — 폭격 직후의 `queue_full` 과 활동 목록의 불일치

폭격이 끝난 뒤 하네스가 `/api/activity` 로 **대기열이 비었음(진행 0 · 대기 0 · 쓰기 락 없음)** 을 확인하고
정상 질의를 던졌는데, 서버가 `503 queue_full — 대기열이 가득 찼습니다 (64)` 로 거절했다
(같은 시드 재현 2회 중 1회는 503, 1회는 180초 타임아웃).

- **판정에는 넣지 않는다.** `verify_monkey.py` 는 설계상 폭격 직후의 거절을 판정에서 제외하고
  ([VERIFICATION_0915.md](VERIFICATION_0915.md) §5 의 근거 — 버려진 요청의 서버 스레드가 `queue_timeout_s`(120초)로
  정리될 때까지 슬롯을 물고 있다), 이번에도 500 오류 0 · 서버 생존 · 결함 0 으로 `RESULT OK` 였다.
  연결이 끊긴 요청이 63건이고 거절 메시지의 대기 수가 64 인 것이 이 설명과 맞는다.
- **그래도 남는 의문**: 그 시점의 `/api/activity` 는 대기 0 으로 보고했다. 즉 **관리자가 "진행 중 작업" 화면에서
  보는 대기열과 서버가 실제로 세는 대기열이 어긋난다.** 사용자는 "서버가 바쁩니다(64)" 를 받는데 관리자 화면은
  비어 있으므로, 회사에서 여러 사람이 쓸 때 원인을 찾지 못한다.
- 다음 작업으로 넘긴다 — 요청 이력·활동 목록을 손볼 때(사용자 요청 1번) `reqmgr.activity()` 가 세는 대상과
  `queue_max` 검사(`reqmgr.py` 의 `queued = sum(... status == "queued")`)가 같은 집합을 보게 맞추고,
  버려진 요청(클라이언트 연결 끊김)을 목록에 `abandoned` 로 드러낼지 검토한다.

**버튼 검증의 범위를 명확히 했다**: `verify_buttons.py` 는 `index.html` 안의 `<button id=…>` 만 수집한다.
질의 뒤 JS 가 만들어 넣는 Ask 패널 버튼(`qa-*`, 그리고 링크 모양의 `<a class="button">` 2개)은 여기에 잡히지 않으므로,
`verify_ui_wiring.py`(동적 id·핸들러 배선) + `verify_web.py`(그 버튼이 부르는 API 응답) 로 나누어 확인한다.
스크립트 첫머리 주석에 이 범위를 적어 두었다 — "모든 버튼" 은 **정적 버튼 전수**를 뜻한다.

## 3. 기대 결과 포렌식 화면 개선 (사용자 질문에 대한 답)

질문: *"Ask 탭에서 기대 문서 ID·기대 용어·메모를 넣고 '수정안을 제안 큐에 등록' 을 켜고 분석을 실행했다 — 이렇게 쓰는 게 맞나? 개선할 점은?"*

**사용법은 맞다.** 그 화면은 "내가 기대한 문서/용어가 왜 답에 없었나" 를 같은 설정으로 검색만 다시 돌려 단계별로 보여 주는 것이고,
`propose` 를 켜면 나온 수정안이 HITL 제안 큐에 쌓여 Evolve 탭에서 사람이 승인한다. 의도대로 쓴 것이다.

다만 **화면이 읽히지 않는** 문제가 세 가지 있었고, 서버가 미리 정리해 주도록 고쳤다
(상세: [FORENSIC.md](FORENSIC.md) §2.4).

| 무엇이 문제였나 | 어떻게 바꿨나 |
|---|---|
| 원 판정이 `sufficient` 인데 기대 문서는 빨간 글씨로 **"미해결"** 이라 모순처럼 읽힌다 | 둘은 다른 질문에 대한 답이다. 요약 첫 줄에 *"답변 자체는 충분했지만 기대한 X 는 색인에 그런 문서가 없다 — 설정이 아니라 문서/ID 표기 문제다"* 를 넣고, 화면은 빨간 글씨 대신 **할 일이 적힌 안내 배너**로 보여 준다. 기대 근거가 하나도 인용되지 않은 경우(=다른 문서로 답했다)도 한 문장으로 구분한다 |
| 목표 청크가 수십 개면 **같은 형식의 탈락 단계 표가 그만큼 반복**된다 (`--term` 만 주면 최대 `forensic_term_targets`=20개) | 서버가 **가장 멀리 간 것부터** 정렬해 주고, 화면은 `forensic_targets_shown`(기본 3)개만 펼친다. 나머지는 "나머지 목표 청크 N개" 로 접힌다 |
| 수정안이 담긴 순서 그대로여서 **confidence 0.35 가 0.8 위**에 있었다 | confidence 내림차순 정렬. `forensic_suggestion_min_confidence`(기본 0.5) 미만은 **버리지 않고** `low_confidence: true` 로 표시해 화면이 접는다 |

두 임계값 모두 `tuning.json` 키로, `tuning show --stage forensic` · Web › Settings › 튜닝에서 바꾼다
(코드 수정 없이 환경마다 다르게 둘 수 있어야 한다는 원칙).

바뀐 파일: `llmwiki/forensic.py`(정렬·안내·임계), `llmwiki/tuning.py`(키 2개),
`llmwiki/web/static/js/ask.js`(`renderExpect` — Quality 탭도 이 함수를 쓴다), `docs/FORENSIC.md`.

## 4. 다른 환경에서 다시 돌리는 법

```bat
:: 0) 전제 — 색인이 이미 있고(build 완료), config.json 의 llm_provider 가 연결되어 있을 것
python -m llmwiki mcp --doctor                :: 붙기 전에 서버 쪽 상태부터

:: 1) 빠른 것부터
python -m unittest discover -s tests
python tools\verify\verify_ui_wiring.py
python tools\verify\verify_mcp.py --quick

:: 2) 전수
python tools\verify\verify_cli.py
python tools\verify\verify_web.py
python tools\verify\verify_mcp.py
python tools\verify\verify_browser.py
python tools\verify\verify_buttons.py
python tools\verify\verify_monkey.py
```

- 모두 **격리 임시 환경**에서 돌며 실제 색인·설정을 바꾸지 않는다(`verify_buttons.py --live` 만 예외이며 읽기 버튼만 누른다).
- 브라우저가 필요한 둘(`verify_browser`·`verify_buttons`)은 Edge/Chrome 을 찾지 못하면 SKIP 하고 0 으로 끝난다
  (`LLMWIKI_BROWSER` 로 경로 지정).
- 실패한 행만 [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) §10 문제 해결과 대조한다.

## 5. 이 회차에 바뀐 파일

| 파일 | 변경 |
|---|---|
| `llmwiki/mcp.py` | **P1** `_read_message()` 가 Content-Length 를 바이트로 센다 · **P2** `method` 없는 본문 → `-32600` |
| `llmwiki/forensic.py` | 수정안 confidence 정렬 + `low_confidence` 표시, 목표 청크 정렬, `sufficient`/미해결 조합 안내 문장 |
| `llmwiki/tuning.py` | `forensic_suggestion_min_confidence`(0.5) · `forensic_targets_shown`(3) |
| `llmwiki/web/static/js/ask.js` | `renderExpect` — 미해결 안내 배너, 목표 접기, 수정안 정렬·약한 것 접기 |
| `tools/verify/verify_mcp.py` | H1~H7 수정 — 완주 (98/98) |
| `tools/verify/verify_web.py` | `/api/optimize/guide`·`/api/optimize/bundle` 4항목 |
| `tools/verify/verify_buttons.py` | 수집 범위(정적 버튼)와 동적 버튼을 어디서 확인하는지 주석 |
| `tests/test_features_0914.py` | `McpStdioFramingTest`(3) · `ForensicExpectTest.test_expect_report_is_ordered_and_explains_unresolved`(1) |
| `docs/MCP.md` | 도구 12개·annotations·인자 검증·버전 협상·프레이밍 · §3.1 429/503 · §4 `mcp --doctor` · §6 다른 RAG 붙이는 세 방법 · §6.1 플러그인 · §7 검증 |
| `docs/RAG_FEDERATION.md` | 그림의 도구 수·검증 절·doctor 안내 |
| `docs/FORENSIC.md` | §2.4 헷갈리는 조합과 화면이 접는 기준, 튜닝 키 2개 |
| `docs/BRINGUP_GUIDE.md` | §4.5 연결 확인(`mcp --doctor`)·키 분리·mode=auto 주의 · §4.6 doctor 와 도구 12종 |
| `docs/ANALYSIS_MODE.md` `docs/WEB_UI.md` | 리포트 → 최적화 묶음으로 넘어가는 안내, 📦 버튼 3종 설명 |
| `README.md` | MCP·FORENSIC 문서 설명, MCP 기능 요약 |
| `docs/VERIFICATION_0916.md` | 이 문서 |
