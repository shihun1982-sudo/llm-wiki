# 검증 보고서 — 2026-09-17 : 전면 재검토 (코드 · 문서 · 전 기능 · 동시성 · 실패 경로 · MCP 확장성)

> 이 회차는 **새 기능을 만드는 회차가 아니라, 이미 만든 것을 다시 의심해 보는 회차**다.
> 요청: ① 코드·문서 전수 재검토 ② README 를 시작점으로 전체 기능·포팅 파악 ③ 모든 기능 동작 확인
> ④ 다중 사용자(CLI·Web·MCP) 안정성·최적화 ⑤ MCP 확장 방향 재검토 ⑥ 대량 검증·스트레스·타임아웃·멍키.

## 0. 한 장 요약

**한 줄로 돌리는 법** — 이 표의 숫자는 아래 명령이 만든다 (손으로 옮겨 적지 않는다):

```bat
python tools\verify\verify_all.py          :: 전체 (결과는 tools/verify/verify_all_result.json)
python tools\verify\verify_all.py --quick  :: 브라우저·멍키 제외 (빠른 확인)
python tools\verify\verify_all.py --full   :: MCP 전체 + 30명 협업 시뮬레이션까지
```

<!-- VERIFY_ALL_TABLE -->

> 2026-09-17 05:54 기준 · Python 3.14.7 · 모두 격리 임시 환경에서 실행 (실제 색인·설정은 건드리지 않는다)

| 무엇 | 명령 | 결과 |
|---|---|---|
| 단위 테스트 | `python -m unittest discover -s tests` | **240/240 통과** (107s) |
| 스트레스 (30명 동시) | `python -m unittest tests.test_concurrency_0915.StressTest` | **9/9 통과** (24s) |
| 문서 ↔ 코드 정합 | `python tools/verify/verify_docs.py` | **OK** (0s) |
| CLI · Web · MCP 정렬 | `python tools/verify/verify_surface_align.py` | **OK** (0s) |
| CLI 전수 | `python tools/verify/verify_cli.py` | **222/222 통과** (63s) |
| Web API 전수 | `python tools/verify/verify_web.py` | **307/307 통과** (12s) |
| MCP 종단 | `python tools/verify/verify_mcp.py --quick` | **92/92 통과** (8s) |
| UI 배선 | `python tools/verify/verify_ui_wiring.py` | **OK** (0s) |
<!-- /VERIFY_ALL_TABLE -->

### 이 회차에 찾아 고친 결함 5건

| # | 무엇이 잘못됐나 | 어떻게 드러났나 | 고친 것 |
|---|---|---|---|
| 1 | **질의 캐시가 단계 재실행을 가로챘다** — 설정을 바꿔 ⟲ 를 눌러도 `cache_hit` 하나만 찍고 예전 답이 그대로 | `verify_web.py` 의 "재생 단계 수" 검사가 `재생 0단계 \| 단계 sync_index,cache_hit` 로 | 재실행 중에는 `query_cache`·`precompute` 를 읽지 않는다 (§2.1) |
| 2 | **`mcp_sources.json` 오타가 엉뚱한 오류로** — `retrieve` 를 객체로 적으면 질의 도중 `'str' object has no attribute 'get'` | `verify_timeouts.py` 에서 죽은 소스를 흉내 내다 실제로 밟음 | 읽을 때 형태를 바로잡고, 틀렸으면 **어느 소스의 어느 항목**인지 말한다 (§4) |
| 3 | **소스 하나의 오타가 모두의 질의를 멈출 뻔** — 위 검증을 엄격하게만 하면 그렇게 된다 | 고치는 도중 스스로 발견 | 읽기는 관대(건너뛰고 이유 기록), 저장·점검은 엄격 (§4) |
| 4 | **그래프 채널이 비어 있는 채로 방치** — 실제 색인의 엔티티 0개. 질의마다 `graph_search` 는 돌지만 늘 0건 | 색인 내용을 직접 들여다보다 발견 (오류도 로그도 없다) | `health` 에 `channels_populated` 추가 (§3.3) |
| 5 | **큰 색인이 hash 임베딩으로 도는 것을 아무도 몰랐다** | 같은 조사 중 | `health` 에 `embedder_quality` 추가 (§3.3) |

4번과 5번은 **오류가 아니라 조용한 품질 저하**라 기존 검증으로는 영원히 잡히지 않았다. 이 회차의 가장 큰 수확이다.

## 1. 문서 ↔ 코드 정합을 기계로 확인한다 (`verify_docs.py`)

문서가 30개를 넘는다. 사람이 다시 읽는 방식으로는 낡은 곳을 못 찾는다. 그리고 이 프로젝트는
**다른 LLM 이 문서만 보고 사내에 올리는 것**이 목표라, 문서의 오류가 그대로 사고가 된다.

확인하는 것 (모두 "문서가 주장하는 것" 을 코드·파일에서 확인하는 방향):

| # | 무엇 | 왜 |
|---|---|---|
| 1 | 문서 간 링크 · 저장소 파일 링크 | 끊긴 링크는 문서만 보고 따라오는 사람을 멈춰 세운다 |
| 2 | 인용한 `python -m llmwiki <명령>` 이 CLI 에 실제로 있는가 | 없는 명령을 적어 두면 첫 시도에서 막힌다 |
| 3 | 인용한 `toggles.<키>` 가 `config.py` 에 있는가 | 이름이 바뀐 설정은 조용히 무시된다 |
| 4 | 인용한 `tools/`·`setup/`·`schemas/` 파일이 있는가 | "이 파일을 복사하세요" 가 거짓이면 절차가 끊긴다 |
| 5 | 인용한 API 경로가 서버에 있는가 | |
| 6 | **모든 문서가 README·BRINGUP_GUIDE 에서 도달 가능한가** | 링크되지 않은 문서는 없는 문서다 |
| 7 | 코드가 가리키는 `docs/XXX.md` 가 실제 파일인가 | 코드 주석의 문서 참조도 낡는다 |

> 하네스를 만들며 배운 것: 처음엔 `tools/list`·`tools/call`·`prompts/list` 를 "없는 파일" 로 34건 신고했다.
> 그것들은 **MCP JSON-RPC 메서드 이름**이지 경로가 아니다. 마지막 조각에 확장자가 있는 것만 파일로 보도록 고쳤다.
> 검증 도구가 틀리면 진짜 문제가 소음에 묻힌다 — 하네스의 거짓 양성은 제품 결함만큼 해롭다.

찾은 진짜 문제 1건: `REBUILD_SPEC.md` 의 `stages[{…}](depth1)` 가 **마크다운 링크 문법으로 잘못 해석**되어
렌더링이 깨지고 있었다. 순서를 바꿔 `stages(depth1)[{…}]` 로.

## 2. 단계 재실행 ([RERUN.md](../../RERUN.md))

### 2.1 캐시가 재실행을 가로채던 결함

`query_cache` 가 켜져 있으면 재실행 요청도 캐시에 먼저 맞아 **아무 단계도 돌지 않고** 예전 답이 돌아왔다.
사용자에게는 "설정을 바꿔도 화면이 그대로" 로 보인다. 오류가 없으니 로그로도 안 잡힌다.

고침: 재실행 중에는 캐시를 읽지 않는다(`trace` 에 건너뛴 이유가 남는다).
회귀 방지: `tests/test_rerun_0917.py::test_query_cache_never_serves_a_rerun` + `verify_web.py` 의 재생 단계 수 검사.

### 2.2 ⟲ 는 **바로 실행**한다

처음엔 누를 때마다 설정 창을 띄웠는데, 값 하나 바꿔 보는 일을 반복하는 도구에서 매번 창이 뜨면
단계가 하나씩 늘어난다. 지금은 **클릭 = 지금 사이드바 설정으로 바로 실행**, **Shift/Alt + 클릭 = 설정 창**.

### 2.3 대화상자가 깨져 보이던 CSS 이름 충돌

`style.css` 에 `.modal` 이 **두 번** 정의되어 있었다 — 하나는 "가운데 카드", 다른 하나는 나중에 추가된
"화면 전체 덮개". 뒤엣것이 카드를 덮어써 `position:fixed; display:flex` 가 카드에 붙고 내용이 가로로 흩어졌다.
같은 카드 클래스를 쓰는 **권한 단계 확인 모달도 이미 같은 상태**였다(`mode:"on"` 에서만 보여 그동안 눈에 안 띄었다).

고침: 덮개를 `.modal-overlay` 로 분리. 회귀 방지: `verify_rerun_ui.py` 가 **계산된 스타일**로 확인한다
(카드는 `position:fixed` 가 아니고 너비 300~560px, 덮개는 `position:fixed`).

## 3. 전 기능 동작 확인

### 3.1 무엇으로 확인하나

| 층 | 하네스 | 무엇을 |
|---|---|---|
| 모듈 | `python -m unittest discover -s tests` | 단위·통합 |
| CLI | `verify_cli.py` | 모든 명령을 격리 환경에서 실제 실행 |
| Web API | `verify_web.py` | 게스트/viewer/class1/admin/API 키/MCP/CSRF 왕복 |
| MCP | `verify_mcp.py` | 전송 3종·프로토콜·도구·인증·동시성·페더레이션·doctor |
| 화면 | `verify_browser.py` · `verify_buttons.py` · `verify_security_ui.py` · `verify_rerun_ui.py` | 렌더 · 모든 버튼 클릭 · 보안 화면 3상태 · 재실행/작업 상세 |
| 문서 | `verify_docs.py` | §1 |
| 실패 경로 | `verify_timeouts.py` | §3.2 |
| 무작위 | `verify_monkey.py` | 제어문자·거대 본문·잘못된 JSON 폭격 |
| 다중 접속 | `verify_collab_many.py` | 10·30명 동시 접속 시뮬레이션 |

### 3.2 실패할 때 어떻게 버티는가 (`verify_timeouts.py`, 24항목)

정상 경로는 다른 하네스가 다 본다. 사람을 괴롭히는 것은 **실패 경로**인데 이건 실패를 만들어야 볼 수 있다.
그래서 mock 프로바이더에 **테스트 훅**을 뒀다 (환경 변수가 없으면 아무 일도 하지 않는다):

| 환경 변수 | 뜻 |
|---|---|
| `LLMWIKI_MOCK_DELAY_MS=2000` | 호출마다 지연 — 느린 LLM·취소·대기열 시험 |
| `LLMWIKI_MOCK_FAIL=timeout` | 매번 타임아웃 — 재시도·회로 차단·대체 경로 |
| `LLMWIKI_MOCK_FAIL=timeout:2` | 처음 2회만 실패 — 재시도 성공 경로 |

지키려는 약속은 하나다: **한 요청의 실패가 다른 사용자에게 번지지 않는다.**

| 상황 | 확인한 결과 |
|---|---|
| LLM 이 두 번 실패 후 성공 | 재시도로 살아나 `llm` 답변으로 끝난다 |
| LLM 이 계속 실패 | **500 이 아니라 200** · 추출식 답변 · `llm_report` 로 이유 보고 · `health` 는 계속 응답 |
| 연속 실패 뒤 | 회로 차단으로 **0.2초 만에** 다음 질의 처리 (기다리지 않는다) |
| 느린 LLM(2초) 이 도는 중 | 활동·진행 조회가 **0.03초** (읽기 락에 갇히지 않는다) |
| 진행 중 취소 | 499 로 깔끔히 끝나고, **직후 질의가 정상** |
| 12건 동시 폭주 (동시 상한 2, 대기열 3) | 처리 2 · 거절 10 · **예상 밖 오류 0** · 폭주 뒤 정상 복구 |
| 외부 RAG 소스가 죽어 있음 | 질의는 0.9초에 정상 완료 · 실패 이유가 `trace` 에 남음 |
| LLM 이 죽은 상태의 MCP | `tools/call` 이 결과를 돌려주고 `tools/list` 는 영향 없음 |

### 3.3 조용한 품질 저하를 잡는 새 health 검사 2개

실제 색인을 들여다보다 발견한 것들이다. **오류가 아니어서** 기존 검증 어디에도 걸리지 않았다.

**(1) `channels_populated`** — 토글은 켜져 있는데 색인이 비어 있는 채널

```
△ channels_populated  비어 있는 채널: graph(entities=0) → `build graph`
```

실제 색인에 **엔티티 0개·관계 0개**였다. 코퍼스를 넣은 뒤 증분 빌드만 돌아(`changed=0`) 그래프가
한 번도 만들어지지 않은 것이다. 질의마다 `graph_search` 는 돌지만 **늘 0건**이라 아무도 몰랐다.
복사본에서 `build graph` 를 돌려 보니 **13초에 엔티티 617 · 관계 13,411 · 커뮤니티 35** 가 생겼다.

**(2) `embedder_quality`** — `auto` 가 임베딩 모델을 못 찾아 hash 로 내려앉은 경우

```
△ embedder_quality  embed_provider=auto 인데 임베딩 모델을 찾지 못해 hash 로 동작 중 (청크 16892개)
```

hash 임베딩은 **의미가 아니라 어휘**를 본다. 오프라인 모드로는 정당하지만, `auto` 로 두고 모델이 없어서
hash 가 된 경우에는 벡터 채널이 FTS 와 거의 같은 신호가 되어 **하이브리드의 이점이 사라진다**.
명시적으로 `embed_provider=hash` 를 고른 사람에게는 경고하지 않는다(소음 방지).

> 두 경고를 합치면 이 환경의 실제 상태는 "FTS + 어휘기반 벡터 + 빈 그래프" 였다.
> 조치는 `build graph`(13초) 와 임베딩 모델 도입(`ollama pull bge-m3` → `build vector --full`).

## 4. MCP 확장성 재검토 — 다른 RAG 를 붙이는 길 ([RAG_FEDERATION.md](../../RAG_FEDERATION.md))

### 방향은 맞다

새 RAG 를 붙일 때 **코드를 고치지 않는다**. `mcp_sources.json` 한 파일에 전송 방식과 도구 매핑을 선언한다.

| 축 | 선택지 | 왜 이 구조가 맞나 |
|---|---|---|
| 전송 | `stdio` · `http` · `rest` | 사내 RAG 가 MCP 를 말하지 않아도(`rest`) 붙는다 — 이게 없으면 대부분의 사내 API 는 못 붙인다 |
| 용도 | `ingest` · `retrieve` · `enrich` · `expose` | "우리 색인에 넣을지", "검색 채널로 섞을지", "그쪽 도구를 그대로 빌려줄지" 가 서로 다른 결정이다 |
| 필드 매핑 | `id_field`·`text_field`·`score_field`·`result_path` … | 남의 API 응답 모양을 우리가 정할 수 없다. 매핑을 **데이터로** 두는 것이 유일하게 확장 가능한 방식이다 |
| 이름 충돌 | `<source>__<tool>` | 여러 RAG 의 같은 이름 도구가 섞여도 외부 LLM 이 구분한다 |
| 결과 합류 | 가상 청크 `ext:<source>:<id>` + RRF 융합 | 외부 결과가 **인용 가능한 근거**가 된다 (텍스트 첨부로 끝나지 않는다) |

대안으로 "소스마다 어댑터 클래스를 하나씩 짠다" 를 생각해 볼 수 있지만, 그러면 붙일 때마다 배포가 필요하고
사내 환경에서는 그 배포가 가장 어려운 일이다. 선언형이 맞다.

### 고친 것 — 첫 시도에서 막히지 않도록

새 RAG 를 붙이는 사람이 가장 먼저 만나는 파일이 `mcp_sources.json` 이다. `retrieve` 는 여러 개를 둘 수 있어
목록인데, **하나만 쓸 때 객체로 적는 것이 자연스러워 보여** 실수가 잦다. 예전에는 그 실수가
질의 도중 `'str' object has no attribute 'get'` 로 나타났다 — 어느 소스의 무엇이 문제인지 알 수 없었다.

| 지금 동작 | |
|---|---|
| 객체 하나로 적으면 | **받아 준다** (목록으로 바꿔 읽는다) |
| 목록 안에 문자열·`tool` 누락 등 | `mcp_sources.json: 소스 'peer_wiki' 의 'retrieve'[0] 에 'tool' 이 없습니다` |
| 읽을 때(질의 경로) | 잘못된 소스만 **건너뛰고** 나머지는 그대로 쓴다 — 오타 하나가 모두의 질의를 멈추지 않는다 |
| 저장할 때(Web·CLI) | **엄격하게** 막는다 — 여기서 안 잡으면 "저장은 됐는데 왜 안 붙지?" 가 된다 |
| `mcp --doctor` | 건너뛴 소스를 이유와 함께 보고한다 |

## 5. 다중 사용자 동시성 — 이미 되어 있던 것과 확인한 것

새로 최적화할 곳을 찾지 못했다. 확인한 것들:

| 무엇 | 상태 |
|---|---|
| 벡터 행렬(청크 16,892 × 4096차 = 277MB) | **프로세스 1회 적재** 후 공유. 동시 미스는 락으로 한 번만 적재하고 결과를 나눠 쓴다. `build_version` 으로 무효화. float16 이면 그대로 유지 |
| 읽기/쓰기 락 | 질의는 읽기 슬롯, 증분 빌드는 soft, 전체 리빌드만 배타 |
| 진행·활동 조회 | 슬롯을 잡지 않는다 → 느린 질의 중에도 0.03초 응답 (§3.2) |
| 협업(채팅) 폴링 | 읽기 슬롯을 잡지 않는다 (2026-09-16 회차에서 고친 것) |
| 폭주 | 대기열 상한 초과는 429/503 으로 **거절**하고 서버는 산다 |

## 6. 이 회차에 바뀐 파일

| 파일 | 변경 |
|---|---|
| `tools/verify/verify_docs.py` | **신규** — 문서↔코드 정합 7종 |
| `tools/verify/verify_all.py` | **신규** — 모든 하네스를 돌려 한 표로 (`verify_all_result.json`) |
| `tools/verify/verify_timeouts.py` | **신규** — 실패 경로 24항목 |
| `llmwiki/providers.py` | mock 테스트 훅(`LLMWIKI_MOCK_DELAY_MS`·`LLMWIKI_MOCK_FAIL`) — 환경 변수가 없으면 무동작 |
| `llmwiki/mcp_client.py` | `normalize_source()` · `load_sources(strict=)` · `source_errors()` |
| `llmwiki/mcp.py` | doctor 가 건너뛴 소스를 보고 |
| `llmwiki/health.py` | `channels_populated` · `embedder_quality` |
| `llmwiki/query_engine.py` | 재실행 중 캐시 차단 |
| `llmwiki/web/server.py` | `/api/mcp_sources` 가 `invalid` 를 함께, 저장 시 엄격 검증 |
| `llmwiki/web/static/style.css` | `.modal` 이름 충돌 해소(`.modal-overlay`), `.modal label` 이 select·textarea 도 |
| `llmwiki/web/static/js/core.js` | ⟲ 클릭 = 바로 실행 / Shift+클릭 = 설정 창 |
| `tests/test_review_0917.py` | **신규** 18건 |
| `docs/BRINGUP_GUIDE.md` | **§2.2 이미 빌드해 둔 색인을 가져다 쓰기** (신규) |
| `docs/history/2026-09-17/VERIFICATION_0917.md` `docs/RERUN.md` `docs/ACTIVITY_DETAIL.md` | **신규** |
| `README.md` | 색인 재사용·실패 내성·`verify_all` 안내, 새 문서 연결 |
