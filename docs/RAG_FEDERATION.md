# RAG FEDERATION — 다른 RAG · 검색 API · MCP 서버를 붙이고, 외부 LLM 클라이언트에 한 곳으로 내주기

> 대상: (1) 이 LLM Wiki 에 **다른 팀의 RAG / 사내 검색 API / 다른 MCP 서버**를 검색 소스로 붙이려는 운영자, (2) 외부 LLM 클라이언트(Claude Code/Desktop · Cursor · opencode · 자체 에이전트)가 **우리 `/mcp` 하나로 여러 RAG 를 쓰게** 하려는 사람, (3) 코드 수정 없이 MCP 도구를 늘리려는 사람. 인바운드(외부 LLM → 우리 MCP) 의 전송·인증은 [MCP.md](MCP.md), 권한은 [SECURITY.md](SECURITY.md). 2026-09-15 구현, 설계 근거는 [IMPLEMENTATION_PLAN_0914.md](history/2026-09-14/IMPLEMENTATION_PLAN_0914.md) §9, 검증은 [VERIFICATION_0915.md](history/2026-09-15/VERIFICATION_0915.md) §7.

## 0. 한 장 요약

```
                 ┌──────────────── 외부 LLM 클라이언트 (Claude Code/Desktop · Cursor · opencode · 자체 에이전트) ────────────────┐
                 │   stdio (같은 PC)  ·  Streamable HTTP  POST /mcp (Bearer lwk_… / 게스트 viewer)  ·  브리지                   │
                 └──────────────────────────────────────────────┬───────────────────────────────────────────────────────────┘
                                                                ▼
   ┌──────────────────────────────── llmwiki MCP 서버 (llmwiki/mcp.py) ─────────────────────────────────────┐
   │ tools/list = built-in 12개 (wiki_query … wiki_sources · wiki_external_search) + 도구별 annotations(읽기/쓰기)   │
   │            + 플러그인  <mcp_plugins_dir>/*.py  register(add_tool)                                            │
   │            + 페더레이션  <source>__<tool>   (mcp_sources.json expose, 토글 mcp_federation)  ── 호출 그대로 중계 ──┐
   │ tools/call ─► inputSchema 의 required·type·enum 검증 (위반은 실행 없이 isError) ── 이름 중복은 뒤엣것을 버린다 ──   │
   │ wiki_query ─► Pipeline.query ─► fts | vector | graph | doc_vector | ext_<source> (토글 external_rag) ─► rrf ─► rerank │
   └───────────────────────────────────────────────────────────────────────────────────────────────────────┘   │
                                                                ▲ retrieve 매핑                                     │
   ┌──────────────────────────── 외부 소스 (mcp_sources.json, llmwiki/mcp_client.py) ◄──────────────────────────────┘
   │  stdio  : 자식 프로세스 MCP 서버 (Mango 등)          http : 원격 MCP Streamable HTTP (다른 llmwiki, 사내 RAG 의 MCP)  │
   │  rest   : MCP 가 없는 일반 JSON 검색 API (POST /search)                                                        │
   │  용도   : retrieve(검색 채널) · expose(도구 노출) · ingest(문서로 색인) · enrich(구, fallback 첨부)                  │
   └───────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

| "다른 RAG" 가 이런 형태다 | 붙이는 방법 | 결과 |
|---|---|---|
| **같은 소프트웨어(llmwiki)** 를 쓰는 다른 팀 서버 | `transport: http`, `url: http://host:8765/mcp`, 그쪽에서 `apikey add` 받은 토큰 → `token_env` | `retrieve` 로 `wiki_search` 결과를 우리 검색 채널에 융합, `expose` 로 그쪽 `wiki_query/wiki_doc` 를 우리 도구 목록에 |
| **MCP 를 제공하는** 사내 RAG/검색 시스템 | `transport: http`(원격) 또는 `stdio`(로컬 실행 파일) | 위와 같음. tool 이름·인자·결과 경로만 `retrieve` 매핑에 적는다 |
| MCP 가 없는 **REST 검색 API** (`POST /search` → JSON) | `transport: rest`, `base_url`, 헤더에 API 키 | `retrieve` 채널. `tools` 배열로 스키마를 적으면 `expose` 도 가능 |
| raw data 를 주는 시스템 (이슈 트래커·CL·빌드 서버) | `ingest` 매핑 (기존 Mango 방식) | 문서로 변환해 **우리 색인**에 포함(오프라인, 그래프·시간 검색 가능) |
| 우리 서버 안에 새 **도구**를 추가하고 싶다 | `plugins/mcp_tools/<이름>.py` 의 `register(add_tool)` | tools/list 에 바로 나타남 (코드 수정·재배포 없음) |

세 토글로 켠다: `mcp_sources`(ingest/enrich, 종전) · **`external_rag`**(retrieve 채널) · **`mcp_federation`**(expose). 모두 기본 off — 파일에 소스를 적어 두어도 토글을 켜기 전에는 질의 지연이나 도구 목록이 변하지 않는다.

## 1. 개념

- **소스(source)**: `mcp_sources.json` 의 항목 하나. 전송(stdio/http/rest) + 용도 매핑(retrieve/expose/ingest/enrich). 이름이 곧 채널 이름(`ext_<source>`)과 도구 접두(`<source>__`)가 된다.
- **retrieve → 검색 채널**: 질의마다 소스에 검색을 보내고 결과를 **가상 청크** `ext:<source>:<id>` 로 만들어 다른 채널과 함께 RRF 융합한다. 인용 `[C#]` 이 붙고 결과 `hits[].external = {source,id,url,score}` 로 출처가 남는다. 코퍼스에는 저장하지 않는다(색인 불변, viewer 도 사용 가능).
- **expose → 페더레이션**: 소스 서버의 tool 을 `<source>__<tool>` 이름으로 우리 tools/list 에 넣고 호출을 그대로 중계한다. 외부 LLM 은 우리 `/mcp` 하나만 등록하면 된다. 결과는 가공하지 않는다.
- **ingest → 색인**: 빌드 때 raw data 를 마크다운(front matter) 으로 만들어 일반 문서처럼 색인 (BRINGUP_GUIDE §4 "외부 MCP"). 그래프·시간 검색·pin 이 모두 되지만 실시간은 아니다.
- **enrich (구)**: fallback `mcp` 단계에서 컨텍스트 꼬리에 텍스트로 첨부. `retrieve` 의 `when: "fallback"` 이 이를 대체한다(융합·인용이 되므로 권장).

### 1.1 왜 이렇게 만들었나 (대안 검토)
| 대안 | 채택 여부 | 이유 |
|---|---|---|
| 외부 결과를 컨텍스트 꼬리에 텍스트로만 붙인다 (종전 enrich) | 보조로만 유지 | 인용이 안 되고 리랭크·claim 검증을 거치지 않아 답변이 외부 문장을 근거 없이 쓰거나 무시한다 |
| 외부 결과를 우리 코퍼스에 자동 색인 | ingest 로만 | 질의 시점 색인은 DB 를 바꾸므로 viewer 권한과 충돌하고 build_version·캐시가 흔들린다 |
| **가상 청크로 융합** (채택) | ✅ | 검색 파이프라인(융합·부스트·리랭크·컨텍스트·claim 검증·포렌식)을 그대로 통과. 코퍼스 불변 |
| 각 RAG 마다 클라이언트에 MCP 서버를 따로 등록 | 가능하지만 | 클라이언트 설정이 RAG 수만큼 늘고 권한·감사가 분산된다. 페더레이션은 우리 `/mcp` 의 API 키·감사 로그 하나로 모인다 |
| LangChain 류 프레임워크 도입 | ✗ | 표준 라이브러리만 쓰는 이식성 원칙. 필요한 것은 JSON-RPC/HTTP 클라이언트 두 개뿐 |

## 2. 설정 — `mcp_sources.json`

원본은 `setup/mcp_sources.example.json`(예시 4개: `peer_wiki`(http) · `kb_rest`(rest) · `mango`(stdio, ingest) · `mock`(stdio, 테스트)). 값 치환: `{query}` `{k}` `{since}` `{project_root}` `{python}` `${ENV}`.

### 2.1 공통 필드
| 필드 | 뜻 |
|---|---|
| `enabled` | false 면 어디에도 쓰지 않는다 (`test`/`tools`/`fetch` 는 이름을 지정하면 예외적으로 실행) |
| `transport` | `stdio` \| `http` \| `rest` |
| `desc`, `timeout_s` | 설명, 호출 타임아웃(초; 외부 지연이 질의 지연에 더해지므로 retrieve 소스는 짧게) |
| `retrieve` | 검색 채널 매핑 배열 (§2.3) |
| `expose` | `true`(모든 tool) \| `["tool", …]` \| `false` |
| `ingest`, `enrich` | 종전 매핑 (BRINGUP_GUIDE §4, CLI_FLOWS §3.30) |

### 2.2 전송별 필드
| transport | 필드 | 비고 |
|---|---|---|
| `stdio` | `command`(배열), `cwd`, `env` | 자식 프로세스에 `LLMWIKI_FEDERATION_DEPTH=1` 이 전달된다(자식이 llmwiki 면 재페더레이션 금지). `{python}` = 현재 인터프리터 |
| `http` | `url`(…/mcp), `token` 또는 `token_env`(권장, .env 에) 또는 `headers`{…} | MCP Streamable HTTP JSON 모드. `initialize`→`tools/list` 로 연결 확인. 요청마다 `X-LLMWiki-Federation-Depth` 헤더를 실어 원격이 우리를 다시 페더레이션하지 않게 한다 |
| `rest` | `base_url`, `headers`(`${ENV}` 치환), `method`(기본 POST), `ping`(연결 테스트용 GET 경로), `tools`(expose 용 스키마 선언) | tool 이름 = 경로(`/search`). POST 는 args 를 JSON 본문으로, GET(`method: "GET"` 또는 매핑의 `method`) 은 쿼리스트링으로 |

### 2.3 `retrieve` 매핑 필드
| 필드 | 기본 | 뜻 |
|---|---|---|
| `tool` | 필수 | MCP tool 이름 또는 REST 경로 |
| `args` | `{}` | 인자. `"{query}"`, `"{k}"`(정수로 변환) |
| `result_path` | `""` | 결과 배열의 경로 (`items`, `data.results`, 빈 문자열 = 응답 자체) |
| `id_field` `title_field` `text_field` `score_field` `url_field` | `id/title/text/score/url` | 항목 안의 경로. 없으면 `id·doc_id·chunk_id`, `title·heading·name`, `text·snippet·content·chunk·page_content`, `score·similarity·relevance`, `url·link·href` 순으로 추정 |
| `doc_type` | `external` | hits 에 표시되는 유형 (부스트 `doc_type_boost` 에도 쓸 수 있다) |
| `weight` | 1.0 | 채널 가중 = `channel_w_external` × weight |
| `when` | `always` | `always` = 매 질의 · `fallback` = fallback 루프의 `mcp` 단계에서만 |
| `max_chars` | 2000 | 항목 본문 절단 |
| `method` | (rest) | 이 매핑만 GET 으로 |

점수가 없거나 0 이하면 `1/(1+rank)` 로 대체한다(순위 기반 RRF 에는 영향 없음).

### 2.4 config.json · tuning.json
| 파일 | 키 | 기본 | 뜻 |
|---|---|---|---|
| config.json toggles | `external_rag` | off | retrieve 채널 사용 |
| config.json toggles | `mcp_federation` | off | expose 도구 노출·중계 |
| config.json toggles | `mcp_sources` | off | ingest/enrich (종전) |
| config.json | `mcp_plugins_dir` | `plugins/mcp_tools` | 플러그인 폴더 |
| tuning.json | `channel_w_external` | 1.0 | 외부 채널 가중 배율 |
| tuning.json | `external_rag_k` | 5 | 소스당 요청 건수 (`{k}`) |
| tuning.json | `external_rag_inject` | 2 | 소스별 상위 n개를 리랭크 후보에 보장 주입 (§4.2) |
| .env | `LLMWIKI_TOGGLE_EXTERNAL_RAG=1` 등 | | 토글 env 오버라이드, `token_env` 가 가리키는 토큰 |

## 3. 플러그인 도구 (코드 수정 없이 도구 추가)

`plugins/mcp_tools/_example_echo.py` 를 복사해 밑줄 없는 이름으로 두고 `register(add_tool)` 를 작성한다.
```python
def _handler(pipe, args):            # pipe = Pipeline (pipe.store / pipe.s / pipe.query …). 색인을 바꾸지 않는다
    return {"docs": pipe.store.stats()["docs"]}       # dict → JSON + structuredContent, str → text
def register(add_tool):
    add_tool({"name": "doc_count", "description": "…", "inputSchema": {"type": "object", "properties": {}}}, _handler)
```
- 파일 mtime 이 바뀌면 다음 `tools/list` 때 자동 재적재. 오류(문법·이름 충돌)는 `mcp-source federated` 의 `plugins.errors`, MCP `wiki_sources`, Web › Corpus › MCP 소스 › "페더레이션 도구 목록" 에 나온다.
- 이름 규칙: built-in(`wiki_*`)과 겹치지 않고 `__` 를 포함하지 않는다. 밑줄로 시작하는 파일은 무시.
- 권한: 모든 MCP 도구는 `read` 등급이므로 게스트(viewer)도 부른다. 비용이 큰 작업은 넣지 말고, DB 를 바꾸는 작업은 제안(`pipe.store.add_proposal`)으로 만든다.

## 4. 동작 상세

### 4.1 페더레이션 (expose)
1. `tools/list` 요청 → `mcp.list_tools`: built-in + 플러그인 + `federated_tools()`. 소스별 `tools/list` 결과는 300초 캐시(`_FED_CACHE`), 연결 실패는 빈 목록 + 오류 기록(우리 도구는 정상).
2. 이름 `<source>__<tool>`, 설명 앞에 `[<source>]`, inputSchema 는 원격 것 그대로. `expose` 가 배열이면 그 tool 만.
3. `tools/call <source>__<tool>` → `mcp_client.call_source_tool` (클라이언트 풀 사용; 오류 시 풀에서 제거) → 원격 결과의 `structuredContent` 또는 text 를 그대로 반환. 실패는 `isError`.
4. **재귀 방지**: HTTP 호출에는 `X-LLMWiki-Federation-Depth`, stdio 자식에는 `LLMWIKI_FEDERATION_DEPTH` 를 실어 보낸다. 이를 받은 llmwiki 는 자기 페더레이션 도구를 목록에 넣지 않고 `<a>__<b>` 호출을 거부한다(A↔B 상호 expose, 자기 자신 expose 에도 안전).
5. 감사: 우리 서버의 `/mcp` 인증·감사 규칙이 그대로 적용된다(원격 토큰은 서버 설정 파일에만 있고 클라이언트에는 보이지 않는다).

### 4.2 외부 RAG 검색 채널 (retrieve)
```
query → … → fts | vector | graph | doc_vector | external_rag(소스마다 retrieve 호출, 가상 청크 ext:<src>:<id>)
      → rrf_fuse (채널 ext_<src>, 가중 channel_w_external × weight)
      → boost → external_inject (소스별 상위 n개를 리랭크 후보 창 안으로)
      → rerank (LLM/API: 본문으로 판단 · local: 외부 청크의 consensus = 1/소스 내 순위)
      → doc_expand (외부 문서는 확장 대상 없음) → context [C#] → answer → claim_check
```
- 외부 채널은 리스트가 하나라 fts/alt/vector 여러 리스트와 RRF 로 경쟁하면 후보 밖으로 밀리기 쉽다. 그래서 `external_rag_inject`(기본 2)가 소스별 상위 n개를 리랭크 후보에 넣고 **최종 판단은 리랭커**가 한다. 0 이면 순수 RRF 경쟁.
- 로컬 휴리스틱 리랭커의 "채널 합의" 항은 외부 청크에는 소스 내 순위(1/rank)로 대체한다(합의 0 으로 불리해지지 않게).
- 결과 표시: CLI `--json`/Web/MCP 의 `hits[].why` 에 `ext_<src>#<rank>`(+`ext_inject`), `hits[].external`; Web 근거 카드의 출처 칩. trace: `external_rag`(건수·소스별·오류), `external_inject`(moved), `rrf_fuse.sources`.
- 소스 오류(연결 실패·401·타임아웃)는 trace `external_rag.errors` 에만 남고 질의는 내부 채널로 계속된다. 풀에서 그 클라이언트를 버리고 다음 질의에 다시 연다.
- 평가/포렌식: `eval` 의 hit@k 는 문서 id 매칭이라 외부 청크는 정답으로 잡히지 않는다(원 코퍼스 기준 지표 유지). `forensic expect --doc` 도 코퍼스 문서만 목표로 한다. 외부 채널의 효과는 `trial run` 으로 external_rag on/off 를 비교한다.
- 캐시: 질의 캐시 키에 토글이 포함되므로 on/off 는 별도 캐시. 외부 결과 자체는 캐시하지 않는다.

## 5. 절차 (bring-up)

### A. 다른 팀의 LLM Wiki 를 붙인다 (http)
```bat
:: 그쪽 서버 admin 이 발급: python -m llmwiki apikey add team-a-reader --role viewer   → lwk_…
:: 우리 .env
PEER_WIKI_TOKEN=lwk_…
:: 우리 mcp_sources.json — setup/mcp_sources.example.json 의 peer_wiki 를 복사, enabled=true, url 수정
python -m llmwiki mcp-source test peer_wiki            :: ok=true, tools=[wiki_query, …]
python -m llmwiki mcp-source retrieve "RX AGC 수렴" --source peer_wiki
python -m llmwiki config set external_rag=true         :: 또는 사이드바 토글 / --external-rag
python -m llmwiki query "RX AGC 수렴 지연 원인" --trace  :: external_rag 단계 · hits 의 ext:peer_wiki:… 확인
python -m llmwiki config set mcp_federation=true       :: 그쪽 wiki_query/wiki_doc 를 우리 /mcp 에 peer_wiki__wiki_query 로
python -m llmwiki mcp-source federated
```
### B. REST 검색 API 를 붙인다 (rest)
```bat
python -m llmwiki.mcp_client --mock-rest 8799          :: 리허설용 목업 (POST /search {query,k} → {results:[…]})
:: mcp_sources.json 의 kb_rest 예시: base_url, headers(X-Api-Key=${KB_API_KEY}), retrieve 의 result_path/필드 이름을 실제 응답에 맞춘다
python -m llmwiki mcp-source fetch kb_rest /search "{\"query\":\"AGC\",\"k\":2}"   :: 원 응답 확인 → 필드 경로 결정
python -m llmwiki mcp-source retrieve "AGC 수렴" --source kb_rest --json          :: 매핑 확인 (id/title/text/score/url)
```
### C. stdio MCP 서버를 붙인다 (Mango 등)
종전과 같다(BRINGUP_GUIDE §4 외부 MCP). `retrieve` 매핑을 추가하면 ingest(색인) + 실시간 검색을 같이 쓴다. `when: "fallback"` 으로 두면 근거가 부족할 때만 부른다.
### D. 외부 LLM 클라이언트에 내준다
[MCP.md](MCP.md) §1: `apikey add` → 클라이언트 설정(`mcp --client-config`). 페더레이션을 켜면 클라이언트는 아무 것도 바꾸지 않아도 `tools/list` 에 `<source>__<tool>` 이 늘어난다. 도구 설명에 `[source]` 접두가 있어 LLM 이 출처를 구분한다.

## 6. 검증 (실측, 2026-09-15 / 2026-09-16)
- **먼저 `python -m llmwiki mcp --doctor --check-sources`** — 소스 선언·연결·토글·페더레이션 이름·플러그인 적재를 한 번에 본다. 항목별 뜻은 [MCP.md](MCP.md) §4.
- `tests/test_rag_federation.py` 7개: stdio retrieve·융합·inject·off 비교, 소스 오류 격리, when=fallback, rest 전송(retrieve·가중·expose·GET), 플러그인(로드·오류·재적재·예시 파일), http 전송(다른 llmwiki 를 원격으로; 잘못된 토큰 401 격리; 페더레이션; 재귀 방지 헤더).
- 하네스: `tools/verify/verify_cli.py` 에 `mcp-source tools|retrieve|federated`, `query --external-rag/--no-external-rag`, stdio `mcp` 의 `mock__search`·`wiki_sources`; `verify_web.py` 에 `/api/mcp_sources retrieve|tools|federated`(게스트 401), `/api/query` external_rag 오버라이드, `/mcp` 의 `mock__search`·`wiki_external_search`·깊이 헤더 가드.
- **`tools/verify/verify_mcp.py`** (2026-09-16 추가, 98/98 통과 — [VERIFICATION_0916.md](history/2026-09-16/VERIFICATION_0916.md)): 이 문서의 확장 경로를 종단으로 돌린다 — 플러그인 등록·호출·인자 검증, **깨진 플러그인이 다른 플러그인을 막지 않음**, `mock__search` 중계, expose 목록 밖 도구 거부, 없는 소스 거부, 외부 RAG 직접 검색과 질의 융합, 재귀 방지(하위 프로세스는 `__` 도구를 내놓지도 중계하지도 않음), `wiki_sources` 의 `federated_tools`/`plugins.errors` 보고.

## 7. 문제 해결
| 증상 | 조치 |
|---|---|
| 무엇부터 봐야 할지 모르겠다 | `python -m llmwiki mcp --doctor --check-sources` ([MCP.md](MCP.md) §4) |
| `mcp-source test` 가 `HTTP 401` | 그쪽 서버의 API 키(`token_env` 의 환경변수가 .env 에 있는지, 폐기되지 않았는지). llmwiki 는 잘못된 `lwk_` 키를 게스트로 강등하지 않는다 |
| `HTTP 599 timed out` | url/방화벽. 원격이 llmwiki 인데 서로 expose 했다면 재귀 방지 헤더가 있는 버전(0.5.0+)인지 |
| retrieve 결과 0건 | `mcp-source fetch <src> <tool> '{…}'` 로 원 응답을 보고 `result_path`/`*_field` 수정. 소스 `enabled` 와 `when` 확인 |
| 외부 결과가 답변에 안 보임 | `--trace` 의 `external_rag`(건수) → `rrf_fuse.sources`(채널) → `external_inject.moved` → `rerank_*` 순으로 어디서 빠졌는지. `external_rag_inject` 올리기, `weight`/`channel_w_external` 조정, `rerank_llm` 켜기 |
| 페더레이션 도구가 목록에 없다 | 토글 `mcp_federation`, 소스 `enabled` + `expose`, `mcp-source federated` 의 `errors`. 페더레이션 하위 호출(헤더/환경변수)에서는 의도적으로 안 보인다 |
| 플러그인이 안 잡힌다 | 파일명이 밑줄로 시작하지 않는지, `register` 함수가 있는지, `plugins.errors` |
| 질의가 느려졌다 | 외부 지연이 더해진 것. `timeout_s` 단축, `when: "fallback"`, `external_rag_k` 축소, 또는 토글 off. trace 의 `external_rag.ms` |

## 8. 한계와 다음 단계
- 외부 결과는 질의 시점 스냅샷이라 pin/피드백 부스트·그래프 관계·시간 필터가 적용되지 않는다(필요하면 ingest 로 색인).
- 페더레이션은 도구를 그대로 중계할 뿐 결과를 합치지 않는다. "여러 RAG 의 답을 하나로" 는 `wiki_query` + `external_rag` 가 담당한다.
- SSE 스트리밍 MCP 서버(원격 push)는 지원하지 않는다(JSON 응답 모드만).
- 클라이언트 풀은 프로세스당 하나씩이며 stdio 소스는 서버 프로세스 수만큼 자식이 뜬다.

## 9. 구현 파일
| 파일 | 내용 |
|---|---|
| `llmwiki/mcp_client.py` | `MCPClient`(stdio) · `HttpMCPClient` · `RestClient` · `open_source` · 풀(`get_client/drop_client/close_all`) · `retrieve` · `remote_tools` · `call_source_tool` · `source_summary` · 목업 `--mock-server`/`--mock-rest` |
| `llmwiki/mcp.py` | `register_tool`/`load_plugins`(플러그인) · `federated_tools`/`call_extension`(페더레이션, 재귀 방지 `FED_HEADER`/`FED_ENV`) · `list_tools`(이름 중복 제거) · `validate_args`(플러그인 도구에도 적용) · `doctor` · 도구 `wiki_sources`, `wiki_external_search` |
| `llmwiki/query_engine.py` | `external_rag` 단계(가상 청크·채널) · `external_inject` · `_hit_dicts` 의 `external` |
| `llmwiki/retrieval.py` | 로컬 리랭크의 외부 청크 consensus |
| `llmwiki/config.py` `tuning.py` | 토글 `external_rag`/`mcp_federation`, `mcp_plugins_dir`, `channel_w_external`/`external_rag_k`/`external_rag_inject` |
| `llmwiki/cli.py` `web/server.py` `web/static/*` | `mcp-source tools|retrieve|federated`, `/api/mcp_sources` action `retrieve|tools|federated`, Corpus › MCP 소스 탭 버튼 |
| `setup/mcp_sources.example.json` `plugins/mcp_tools/` | 설정 원본 · 플러그인 예시 |
| `tests/test_rag_federation.py` `tools/verify/verify_mcp.py` | 검증 (단위 · 종단) |
