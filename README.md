# LLM Wiki v3 — FTS + Vector + GraphRAG · Self-Evolving (모뎀 HW 제어 임베디드 SW 조직용)

로컬 문서(Issue · Change List · SW/HW 설계 · 코딩 규칙 · 주간 보고 · TC 등)를 색인해 **FTS(BM25) + 벡터 + 그래프** 로 검색하고,
**근거가 있는 내용만** 구조화된 답변으로 돌려주는 위키형 RAG 시스템입니다. 근거가 부족하면 단계적으로 확장 검색(fallback)하고, 그래도 없으면
`insufficient data` 로 답하며 **포렌식**을 남깁니다. 누적된 포렌식·피드백은 자가진화 **제안**(사람 승인)이 됩니다.

외부 API 없이 완전히 동작합니다(hash 임베딩 + 규칙 그래프 + 추출식 답변). 키/서버를 붙이면 LLM 답변·확장·검증·리랭크가 켜집니다.

---

## 0. 문서 안내 — 무엇을 어떤 순서로 읽나

### 목적별 읽는 순서

| 나는 … | 이 순서로 |
|---|---|
| **처음 접했고 전체가 궁금하다** | 이 README §1~§5 → `docs/llmwiki_guide.html`(브라우저에서 클릭하며 구조 파악) → [ARCHITECTURE_V3.md](docs/ARCHITECTURE_V3.md) §0 용어·§1 그림·§9 "한 질의의 여정" |
| **내 PC/서버에 설치해 써보고 싶다** | [setup/INSTALL.md](setup/INSTALL.md) → [BRINGUP_GUIDE.md](docs/BRINGUP_GUIDE.md) §0 체크리스트부터 순서대로 → 막히면 BRINGUP_GUIDE §10 문제 해결 |
| **여러 사람·여러 외부 LLM 이 쓰는 서버로 열고 싶다** | [IMPLEMENTATION_PLAN_0914.md](docs/IMPLEMENTATION_PLAN_0914.md) §0 → [SECURITY.md](docs/SECURITY.md)(역할·권한 표·API 키) → [MCP.md](docs/MCP.md)(원격/다수 LLM) → BRINGUP_GUIDE §4.4~4.5 |
| **답에 기대한 문서가 왜 없는지 알고 싶다** | [FORENSIC.md](docs/FORENSIC.md) → `forensic expect last --doc <ID> --term <용어>` (Web Ask 의 🎯, MCP `wiki_forensic`) |
| **품질·속도·토큰이 마음에 안 드는데 어느 설정을 만질지 모르겠다** | [ANALYSIS_MODE.md](docs/ANALYSIS_MODE.md) → `query "…" --analyze --focus quality|speed|tokens` → `logs/analysis/req_<id>.md` 를 LLM 에게 첨부 (Web 토글 `analysis_mode` + 📊, MCP `wiki_analysis`) |
| **다른 RAG·검색 API·MCP 서버를 붙이고 싶다 / 외부 LLM 이 우리 MCP 하나로 여러 RAG 를 쓰게 하고 싶다** | [RAG_FEDERATION.md](docs/RAG_FEDERATION.md) §0 결정표 → §5 절차 → `setup/mcp_sources.example.json` → BRINGUP_GUIDE §4.6 |
| **옮겨 세운 뒤 전부 정상인지 확인하고 싶다** | [VERIFICATION_0915.md](docs/VERIFICATION_0915.md) §6 → `tools/verify/` 4개 스크립트(CLI 184 · Web 210 · UI 배선 · 브라우저) → 실패 행만 BRINGUP_GUIDE §10 |
| **우리 팀 문서를 넣고 싶다** | [CORPUS_CONTRACT.md](docs/CORPUS_CONTRACT.md) → `corpus lint` → BRINGUP_GUIDE §5 코퍼스 계약 적용 |
| **명령 하나가 내부에서 무엇을 하는지 알고 싶다** | [CLI_FLOWS.md](docs/CLI_FLOWS.md) §3 해당 명령 (예제 → 내부 단계 → 실제 출력 → 오류) |
| **품질을 올리고 싶다(튜닝)** | ARCHITECTURE_V3 §5 질의 단계 표 → [TUNING.md](docs/TUNING.md) → CLI_FLOWS 의 `trial` / `fusion compare` / `forensic` |
| **코드를 고치고 싶다** | ARCHITECTURE_V3 §2 모듈 지도·§3 데이터 모델 → 해당 모듈 → `tests/` |
| **왜 이렇게 설계됐는지 알고 싶다** | [ANALYSIS_REPORT_0913.md](docs/ANALYSIS_REPORT_0913.md) → [IMPLEMENTATION_PLAN_0913.md](docs/IMPLEMENTATION_PLAN_0913.md) → (이전 세대) ARCHITECTURE_V2 · legacy/ |

### 현행(v3) 문서 — 각 문서가 담고 있는 것

**[docs/ARCHITECTURE_V3.md](docs/ARCHITECTURE_V3.md) — 전체 구조 (설계 기준 문서)**
시스템 전체를 한 장에 담은 문서. §0 핵심 용어(청크·채널·provenance·groundedness·HITL·토글/튜닝/프리셋·request_id/run_id), §1 Mermaid 전체 구성도(입력 → Build → SQLite → Query → Self-evolving → 인터페이스)와 한 줄 요약, §2 모듈 지도(어느 파일이 무슨 역할인지), §3 SQLite 테이블 전부, §4 빌드 단계 표와 §5 질의 단계 표(단계 이름이 곧 `--trace`/Web 프로파일에 나오는 이름이며 각 단계의 토글·튜닝 키를 함께 적음), §6 자가진화·메모리 흐름, §7 CLI/Web/MCP 인터페이스, §8 5,000문서+50/일 규모 설계 근거, §9 실제 질문 하나가 14단계를 거치는 여정 예시, §10 요청 추적(어디에 무엇이 기록되고 어떻게 보는지).

**[docs/BRINGUP_GUIDE.md](docs/BRINGUP_GUIDE.md) — 새 환경 포팅 절차서**
다른 PC·서버·조직으로 옮겨 세우는 엔지니어용. §0 체크리스트, §1 환경 요구, §2 설치와 폴더 구조, §3 설정 파일 하나하나 채우는 법(config.json 키·역할별 모델·토글·`LLMWIKI_*` 환경변수·경로 레지스트리), §4 프로바이더 연결(Anthropic / OpenAI-compatible / Ollama / rerank API / headless 에이전트 / MCP 소스 각각의 설정과 `models test`), §5 코퍼스 계약 적용, §6 첫 빌드와 검증(`health` → `build --full` → `build verify` → 확인 포인트), §7 평가 기준선과 튜닝, §8 OS 스케줄러 등록, §9 일상 운영, §10 증상별 문제 해결 표, §11 "포팅 시 코드 변경이 필요한 곳(없어야 정상)".

**[docs/CLI_FLOWS.md](docs/CLI_FLOWS.md) — 모든 CLI 명령의 단계별 동작 (운영 참고서, 가장 긴 문서)**
40여 개 하위 명령 전부를 다룬다. §1 개요(실행 방식, 공통 옵션, 설정 우선순위, PowerShell 주의점), §2 명령 카탈로그 표, §3 명령별 상세 — 각 명령마다 "예제 입력 → 내부에서 실행되는 단계와 모듈/함수 → 격리 샌드박스에서 실제로 실행해 얻은 출력 → 관련 토글·튜닝 → 흔한 오류와 종료 코드" 순서, §4 end-to-end 시나리오 3개(새 환경 bring-up / 매일 증분 운영 / 답변 품질 디버깅과 trial 회귀 확인), §5 종료 코드와 자동화 팁. 출력 예가 모두 실측이므로 자기 환경의 출력과 비교하며 읽을 수 있다.

**[docs/CORPUS_CONTRACT.md](docs/CORPUS_CONTRACT.md) — 문서 계약**
문서를 어떻게 써야 색인·검색이 잘 되는지에 대한 규칙. 파일 형식, 공통 front matter 필드(schema_version·doc_type·id·title·date·tags·module·hw·related), 7가지 문서 유형(issue·cl·sw_design·hw_design·coding_rule·weekly_report·tc_list)별 필수 필드와 권장 섹션, ID 규칙(ISSUE-nnnn, CL-nnnnn)과 그것이 만들어내는 결정적 관계, 완성 예시 문서, lint 규칙, 빌드에서 front matter 가 어떻게 쓰이는지, MCP 로 가져온 raw data 가 계약 문서로 변환되는 방식.

**docs/llmwiki_guide.html — 인터랙티브 가이드 (브라우저로 열기)**
외부 의존 없는 단일 HTML. 구조 지도(모듈 카드를 클릭하면 역할·주요 함수 표시), Build/Query/Evolve 플로우 스테퍼(단계를 하나씩 넘기며 예제 명령·출력·확인 포인트·토글·튜닝 확인), CLI 카탈로그(검색·영역 필터), 설정 파일 지도, 데이터 모델, Web UI 7그룹과 MCP 도구, 운영·문제 해결, light/dark 테마. 발표나 온보딩 때 화면에 띄워 설명하기 좋다.

**[docs/TUNING.md](docs/TUNING.md) — 튜닝 파라미터 표 (자동 생성)**
`python -m llmwiki tuning doc` 이 코드의 튜닝 레지스트리에서 생성한다. 단계별로 키·타입·기본값·범위·영향(impact)·전체 리빌드 필요 여부·설명. 값을 바꾸는 세 가지 방법(tuning.json / `tuning set` / Web) 안내 포함. 코드가 바뀌면 다시 생성한다.

**[docs/ANALYSIS_REPORT_0913.md](docs/ANALYSIS_REPORT_0913.md) — 요구사항 분석·판정 리포트**
`docs/user-req.0913.,txt`(사용자 요구 원문 22항목)에 대해 항목별로 타당성·현재 코드 대비 갭·impact·진행 여부(✅/🟡/🔵/⛔)를 판정한 문서. §0 판정표, §1 당시 코드 상태 진단(규모 한계 포함), §2 고려사항(코퍼스 계약·use case 분리·5,000문서 규모), §3 요청별 상세 분석, §4 참고한 최신 동향, §5 사용자 확인이 필요했던 결정 사항 D1~D15 와 그 결론. "왜 이렇게 만들었나"의 근거.

**[docs/IMPLEMENTATION_PLAN_0913.md](docs/IMPLEMENTATION_PLAN_0913.md) — 구현 계획서**
분석 리포트를 바탕으로 무엇을 어떤 순서로 만들지 정한 문서(Phase 0~8, 신규/변경 파일 목록, 신규 토글·튜닝 이름, 리스크와 대응). 상단에 구현 완료 상태와 결정 사항 확정 내용을 적어 두었다. 구현 결과와 계획을 대조할 때 사용.

**[docs/IMPLEMENTATION_PLAN_0914.md](docs/IMPLEMENTATION_PLAN_0914.md) — 2026-09-14 구현 계획서 (요청 6항목의 판정·대안·설계·검증)**
다중 사용자 권한(6역할·7등급·admin 편집 권한 표·익명 viewer·CLI 게이트·API 키), MCP 원격/다수 LLM(Streamable HTTP·브리지), 채널별 빌드(fts/vector/graph 독립성 근거), 문서 단위 확장(doc_expand), headless/LLM 재시도와 실패 보고, 기대 결과 포렌식. 사용자 제안과 다르게 한 곳(§0.1)과 신규 설정 키 표(§8) 포함. **다른 LLM 이 bring-up 할 때 이 문서 → 아래 세 문서 순서로 읽는다.**

**[docs/SECURITY.md](docs/SECURITY.md) — 다중 사용자 서버의 로그인·역할·권한 표·API 키·파괴적 작업 보호**
왜 "관리 암호 하나"가 아니라 계정·역할·권한 표인지(검토한 대안), 역할 6단계(`viewer < class3 < class2 < class1 < builder < admin`)와 작업 등급 7단계(read/run/edit/index/rebuild/admin/destructive)의 기본 최소 역할·확인 방식, admin 이 편집하는 `permissions`(등급별 최소 역할 + 개별 작업 오버라이드), 익명(게스트) 접속, 로컬 ID/비밀번호·SSO(OIDC·프록시 헤더)·API 키 병행, CLI 권한 게이트(`--user`, `cli.default_role`), 확인 문구·비밀번호 재입력·자동 스냅샷, 감사 로그, 서버 공개 체크리스트(기본 admin `kh82.kim` 비밀번호 변경 포함).

**[docs/MCP.md](docs/MCP.md) — 외부 LLM(여러 개, 같은 PC/원격) 연결**
stdio(같은 PC) · Streamable HTTP(`serve` 의 `POST /mcp`, Bearer API 키, 다수 클라이언트) · 브리지(stdio 전용 클라이언트 → 원격) · 단독 HTTP 서버의 결정표와 설정 예(Windows/Linux, Claude Code/Desktop/Cursor/opencode JSON), 도구 9개(`wiki_query`…`wiki_forensic`), 보안, 동시성, 문제 해결.

**[docs/FORENSIC.md](docs/FORENSIC.md) — 포렌식 디버깅 (자동 + 기대 결과)**
자동 포렌식 확인 방법과, 사용자가 "이 문서/용어가 답에 있어야 했다"고 알려주면 같은 설정으로 검색을 재실행해 fts/vector/graph → 융합 → 리랭크 → 컨텍스트 → 답변 중 어느 단계에서 탈락했는지와 수정안(규칙/pin/튜닝/코퍼스)을 내는 `forensic expect` 의 동작·출력 예·해석 가이드·LLM 실패 보고와의 구분.

**[docs/ANALYSIS_MODE.md](docs/ANALYSIS_MODE.md) — 상세 분석 모드 (2026-09-15)**
토글 `analysis_mode`(또는 `query --analyze`)로 질의 한 건의 모든 단계 결과·설정 스냅샷·채널/융합/리랭크/컨텍스트 상세·답변 판정·**품질/속도/토큰 세 렌즈의 소견과 조절점(토글·튜닝 키=현재값)**·자동 포렌식·프롬프트 샘플을 `logs/analysis/req_<id>.md` 한 장으로 남긴다. LLM 에게 그대로 첨부해 튜닝을 묻는 절차, 렌즈 규칙 표, `analyze` CLI · Web 📊 · MCP `wiki_analysis` · `GET /api/analysis`.

**[docs/RAG_FEDERATION.md](docs/RAG_FEDERATION.md) — 다른 RAG 연동과 MCP 확장 (2026-09-15)**
`mcp_sources.json` 한 파일로 다른 팀의 LLM Wiki(http)·MCP 를 제공하는 사내 RAG·REST 검색 API(rest)·stdio MCP 서버를 붙이는 방법. 외부 결과를 검색 채널 `ext_<source>` 로 융합하는 `external_rag`, 외부 도구를 우리 `/mcp` 에 `<source>__<tool>` 로 노출하는 `mcp_federation`(재귀 방지 포함), 코드 수정 없이 도구를 늘리는 플러그인 폴더 `plugins/mcp_tools/`. 결정표·설정 필드 표·동작 상세·절차·검증·문제 해결.

**[docs/VERIFICATION_0915.md](docs/VERIFICATION_0915.md) — 2026-09-15 전 기능 검증 보고서**
Web UI 와 CLI 가 제공하는 모든 기능을 하나씩 실행해 얻은 결과: 단위 테스트 87, CLI 184 명령(격리 임시 환경), Web 210 요청(게스트/viewer/class1/admin/API 키/MCP 9도구/CSRF), UI 배선 정적 검사, Edge headless 렌더. 검증 중 고친 결함 2건(잘못된 API 키가 게스트로 강등되던 문제, `mcp --client-config` 경로 이스케이프), 요청 6항목 ↔ 검증 매핑, 다른 환경에서 재실행하는 법(`tools/verify/`).

**[setup/INSTALL.md](setup/INSTALL.md) — 설치와 최소 설정**
setup/ 폴더의 각 파일 용도, 요구사항 표, Windows/macOS/Linux 설치 명령, 최소 설정 3단계, 실행 명령, 자기 코퍼스에 맞추는 4단계, 문제 해결 요약, 폴더 통째 이식 방법.

### 이전 세대·참고 문서 (읽지 않아도 v3 사용에는 지장 없음)

| 문서 | 무엇인가 |
|---|---|
| [docs/ARCHITECTURE_V2.md](docs/ARCHITECTURE_V2.md) | v2(2026-09-11) 구조 도식과 당시 6개 후속 요청(R-A~R-F)별 대응표. v3 의 바탕 |
| [docs/REBUILD_SPEC.md](docs/REBUILD_SPEC.md) | v2 시점의 재구현 상세 사양서(다른 개발자/LLM 이 동일 동작을 재현하기 위한 명세). v3 기능은 포함하지 않음 |
| [docs/IMPLEMENTATION_BRIEF.md](docs/IMPLEMENTATION_BRIEF.md) | v2 시점의 요구사항 중심 브리프("무엇을 왜" 정의, 구현 방식은 재량) |
| [docs/REQUESTS_AND_TRENDS.md](docs/REQUESTS_AND_TRENDS.md) · [docs/TRENDS_REFERENCE.md](docs/TRENDS_REFERENCE.md) | v1~v2 를 만들 때 입력으로 삼은 사용자 요청 원문·트렌드 보고서와 반영 위치 |
| `docs/user-req.0913.,txt` · [docs/requirement-0913.md](docs/requirement-0913.md) | v3 요구사항 원문(같은 내용의 txt/md) |
| [docs/legacy/](docs/legacy/) | 1차 구현(v1) 시점의 ARCHITECTURE · DESIGN_REVIEW · MULTI_AGENT 제안. 역사 기록용 |

---

## 1. 5분 안에 시작하기

```bat
:: Windows
cd llm-wiki-rag-selfevolving
setup\install.bat                      :: 패키지 설치 + config.json/.env 생성 + 환경 진단
python -m llmwiki health               :: 환경·프로바이더·DB·코퍼스 점검
python -m llmwiki build --full --trace :: 색인 (기본 코퍼스: setup/sample_corpus_modem — 합성 모뎀 문서 38개)
python -m llmwiki query "ISSUE-2001 의 원인과 수정 CL 은?" --trace
python -m llmwiki serve                :: Web UI → http://127.0.0.1:8765/
```
```bash
# macOS / Linux
bash setup/install.sh && python3 -m llmwiki build --full --trace && python3 -m llmwiki serve
```

자기 코퍼스로 바꾸려면 `config.json` 의 `corpus_dirs` 를 수정하고 `build --full`. 문서 형식은 [CORPUS_CONTRACT.md](docs/CORPUS_CONTRACT.md) 를 따르면 ID 노드·결정적 관계·시간 검색이 켜집니다.

### 요구사항
| 항목 | 최소 | 권장 |
|---|---|---|
| Python | 3.9 | 3.11+ (검증: 3.14) |
| 패키지 | numpy, pypdf | + anthropic, sentence-transformers, kiwipiepy, pyyaml (`setup/requirements-optional.txt`) |
| 외부 서비스 | 없음 | Anthropic · OpenAI-compatible(vLLM/LM Studio/Ollama/OpenRouter) · rerank API · headless 에이전트(opencode 등) · MCP 소스 |

---

## 2. 설정 파일 (모두 파일로 외부화, `config paths`)

| 파일 | 내용 |
|---|---|
| `config.json` | 코퍼스 경로, 프로바이더/**역할별 모델**(answer·rerank·extract·summary·review·expand·verify·forensic), **59개 토글**(+`build_fts`·`doc_expand`·`llm_failure_report`), 운영 수치(배치·WAL·로그·timezone·`llm_timeout`·`llm_retries`·`llm_retry_backoff_s`), **서버/MCP 기본값**(`web_host`·`web_port`·`mcp_transport`·`mcp_host`·`mcp_port`·`mcp_url` — `serve`/`mcp` 플래그 생략 시) — 원본 `setup/config.example.json` |
| `.env` | API 키/PAT (`OPENAI_API_KEY`·`LLM_API_KEY`, `ANTHROPIC_API_KEY`·`ANTHROPIC_AUTH_TOKEN` …) + 모든 설정의 env 오버라이드 (`LLMWIKI_<KEY>`, `LLMWIKI_TOGGLE_<NAME>`) |
| `security.json` | **로그인(로컬 ID/비밀번호 + SSO + API 키)·역할 6단계·권한 표(permissions)·익명 접속·CLI 게이트·파괴적 작업 정책** — [docs/SECURITY.md](docs/SECURITY.md). 저장소에는 admin `kh82.kim/1234qwer` 가 들어 있다(공개 전 변경). 원본 `setup/security.example.json`(users 비어 있음, 키별 설명 포함) |
| `tuning.json` | 알고리즘 상수 130+ (FTS·라우터·그래프·융합·근거 판정·claim·메모리·`doc_expand_*`·`forensic_*`) — `tuning show`, Web › Settings › 튜닝, [docs/TUNING.md](docs/TUNING.md) |
| `presets.json` | **품질/속도/토큰/offline/deep_research** 묶음 — `--preset quality`, 사이드바 체크박스 |
| `query_rules.json` | **규칙 기반 질의 확장 사전**: acronym / synonym / alias / related / exclude / compound (유형별로 다르게 적용) |
| `data/rules.json` | 그래프 사전·정규식 + **ID 패턴·결정적 링크 규칙**(CL→Issue) |
| `schemas/` | 문서 유형별 스키마(schema_version), 추론 규칙, 마이그레이션 |
| `prompts/*.md` | 역할별 프롬프트 + **answer_guide.md**(Evidence-rich · Structured · Grounded 답변 가이드) |
| `pins.json` `agents.json` | 고정 근거 · headless 에이전트 명령 템플릿 + **재시도 정책**(`timeout_s` 300 · `retries` 3 · `retry_on`; 원본 `setup/agents.example.json`) |
| `mcp_sources.json` | **다른 RAG · MCP 서버 · REST 검색 API 연결** (전송 stdio/http/rest; `retrieve` 검색 채널 · `expose` 도구 페더레이션 · `ingest` 색인) — [docs/RAG_FEDERATION.md](docs/RAG_FEDERATION.md). 원본 `setup/mcp_sources.example.json` |
| `plugins/mcp_tools/*.py` | MCP 플러그인 도구 (`register(add_tool)`; 예시 `_example_echo.py`). 위치 `config.json mcp_plugins_dir` |
| `setup/mcp_clients.example.json` | (LLM Wiki 가 읽지 않음) 외부 LLM 클라이언트에 붙여 넣는 MCP 설정 블록 4종. 환경 값이 채워진 버전은 `python -m llmwiki mcp --client-config` |

2026-09-14/15 기능(권한·MCP·채널 빌드·doc_expand·재시도·포렌식)의 **파일·키·기본값·예시·확인 명령 총람**은 [BRINGUP_GUIDE.md §3.2](docs/BRINGUP_GUIDE.md). `setup/check_env.py` 가 security/agents/서버 포트/재시도 설정을 한 줄씩 보고한다.

---

## 3. 주요 기능 (v3)

- **문서 계약**: front matter(schema_version, doc_type, id, date, tags, module, hw, related) → doc_meta · 메타 토큰 · lint(`corpus lint`).
- **결정적 관계 + provenance**: explicit(front matter) / rule(ID 패턴) / cooccur / llm / human 구분, confidence 관리, 그래프 탐색 가중(`provenance_w`), 노드 ↔ 원본 문서(doc_refs).
- **프로바이더**: Anthropic(직접 또는 **Anthropic-compatible 게이트웨이 + PAT**, `anthropic_base_url`), **OpenAI-compatible**(chat/embeddings, **사내 게이트웨이 + PAT** — 헤더 형식 `openai_api_key_header`), Ollama, **rerank 전용 엔드포인트**(Cohere/Jina/vLLM/Voyage), **Generic Headless Agent**(opencode/claude/codex CLI subprocess, `agents.json`, Windows `.cmd` 셸 지원), Voyage/ST/hash 임베더. 연결 확인은 `models test --live`(실제 호출 1회). 설정 예: `setup/config.example.pat-gateway.json`, `config.example.headless.json`.
- **진행 표시**: 빌드/질의처럼 오래 걸리는 작업은 CLI 에 `⏳ 단계 › 진도율 · LLM 응답 대기 Ns` 를, Web 에 진행 패널(단계 경로·%·LLM 대기·최근 로그)을 실시간으로 보여 준다. 멈춘 것처럼 보이던 "전체 리빌드 + llm_graph" 는 서버 락 뒤에 있던 폴링을 락 밖으로 옮겨 해결.
- **빌드 견고성**: `health` 사전 검사, 파일 락, 임베딩 **내용 해시 캐시**(rename/재빌드 0 비용), **재개/체크포인트**, **적응형 배치·WAL 관리**, 진행률·coverage 리포트(`embed report`, Web), 정합성 검증 `build verify --fix`, 삭제/rename 추적.
- **한글**: 조사 제거 + bigram + 스크립트 경계 분리 + **복합어 사전** + 선택적 **kiwi 형태소** + **trigram 폴백**.
- **질의**: **한국어 상대 시간 파싱**(지난주·3일전·Q3, timezone 설정) → **규칙 확장**(유형별) → 라우터(+LLM) → **LLM 확장/분해**(원 질의 유지) → pin → fts/vector/graph/doc_vector → **융합 5방식**(rrf·weighted·zscore·dbsf·rrf_boost) + **post-boost**(문서유형·시간·최신성·pin·provenance·피드백·exclude) → 리랭크 → 컨텍스트.
- **근거**: **evidence_check**(휴리스틱/LLM) → **fallback 루프**(rules→expand→graph→wide→mcp, attempt/token/latency 예산) → **insufficient_data 응답** → **claim_check**(인용 존재 + 실제 지지 검증, groundedness/citation_precision, mark/drop/refine).
- **포렌식**: 단계별 진단 규칙으로 "왜 답을 못 만들었나" 기록(`forensic last`), 누적 → `memory consolidate` → corpus_gap/query_rule/tuning 제안(HITL).
- **메모리**: episodic(에피소드·피드백 부스트) + semantic(승인 규칙·pin) + decay(반감기).
- **평가**: eval 경로 = 사용자 경로. **trial** 저장/비교(지표 Δ, 질문별 승/패, 설정 diff), `fusion compare`.
- **로그**: `logs/` JSON Lines(정상 동작 포함), `run_id` 로 요청 프로파일과 연결(`logs grep --request <id>`).
- **Web UI**: 워크플로 기준 7그룹(Ask / Corpus / Knowledge / Quality / Evolve / Settings / Observability), 프리셋 체크박스, 토글 사이드바 자동 생성, **테마**(light/dark/high-contrast/solarized, 확장 가능), 콘솔에서 CLI 전체 실행.
- **다중 사용자 권한** ([SECURITY.md](docs/SECURITY.md)): 로컬 ID/비밀번호 + SSO(OIDC · 프록시 헤더) + API 키 병행, **역할 6단계 `viewer < class3 < class2 < class1 < builder < admin`**, 작업 등급 7단계(read/run/edit/index/rebuild/admin/destructive)에 **admin 이 편집하는 권한 표**(`security perms`, Web 보안 탭), **익명 접속 = viewer**(DB 무영향 기능 전부), CLI 도 같은 표로 게이트(`--user`), 리빌드/파괴적 작업은 확인 문구 + 비밀번호 + 자동 스냅샷, 감사 로그.
- **MCP** ([MCP.md](docs/MCP.md)): stdio(같은 PC) + **Streamable HTTP**(`serve` 의 `POST /mcp`, Bearer API 키, 여러 외부 LLM 동시 접속, Windows/Linux) + 브리지(stdio 전용 클라이언트 → 원격). 도구 `wiki_query(mode=deep …)`, `wiki_search`, `wiki_related`, `wiki_doc`, `wiki_entity`, `wiki_propose`, `wiki_feedback`, `wiki_forensic`, `wiki_status`, `wiki_sources`, `wiki_external_search` — 모두 읽기/제안(색인 불변). use case(구현/코드리뷰/이슈분석/리팩토링/unified search)별 skill·agent 는 이 도구 위에 별도로 만든다.
- **상세 분석 모드** ([ANALYSIS_MODE.md](docs/ANALYSIS_MODE.md)): 토글 `analysis_mode` / `query --analyze` → 질의가 debug_level 2 로 실행되고 설정 스냅샷·단계 타임라인·채널별 상위·융합/부스트/리랭크 전후·doc_expand·컨텍스트·fallback·최종 근거 표·답변 판정·claim·**품질/속도/토큰 렌즈 소견 + 조절점(현재값)**·포렌식·프롬프트 샘플이 `logs/analysis/req_<id>.md` 한 장으로. `analyze <id|last> [--focus] [--print]`, Web 📊(초점·다운로드·복사), MCP `wiki_analysis`, `GET /api/analysis`. LLM 에게 첨부하는 지시문 포함.
- **다른 RAG 연동·MCP 확장** ([RAG_FEDERATION.md](docs/RAG_FEDERATION.md)): `mcp_sources.json` 에 소스(전송 stdio/http/rest)를 적으면 (1) **`external_rag`** — 외부 검색 결과가 가상 청크 `ext:<source>:<id>` 로 fts/vector/graph 와 함께 RRF 융합·리랭크·인용(`[C#]`, `hits.external`), (2) **`mcp_federation`** — 외부 서버의 tool 이 우리 `/mcp` 에 `<source>__<tool>` 로 노출·중계(외부 LLM 은 우리 서버 하나만 등록; A↔B 상호 연결도 재귀 방지), (3) **플러그인** `plugins/mcp_tools/*.py` 의 `register(add_tool)` 로 코드 수정 없이 도구 추가. 리허설용 목업 `--mock-server`(stdio)·`--mock-rest`(REST) 포함.
- **채널별 빌드**: `build fts|vector|graph [--full]` 로 한 채널만 다시 만든다(세 채널은 `chunks` 만 읽어 서로 독립; 끝나면 verify 로 결손 보고). `build --channels fts,vector`, 토글 `build_fts`/`embed`/`rule_graph`, Web 빌드 탭 버튼. — BRINGUP_GUIDE §6.1
- **문서 단위 확장 (doc_expand)**: 리랭크 상위 청크가 속한 문서의 나머지 청크 중 질의 관련(키워드+벡터 hybrid) 청크를 문서 순서로 컨텍스트에 추가(`[C#]` 인용, 상한 `doc_expand_*` 튜닝). 토글 on/off, speed/token 프리셋은 off. — BRINGUP_GUIDE §7.1
- **LLM 재시도·실패 보고**: headless(`agents.json timeout_s=300, retries=3`)와 모든 프로바이더의 timeout/네트워크 오류를 `llm_retries` 회 재시도하고, 최종 실패는 결과 `llm_report` + 답변 상단 `⚠ LLM 실행 보고`(역할·시도 횟수·오류·대체 경로) + 빌드 alerts 로 남긴다. — BRINGUP_GUIDE §4.3
- **기대 결과 포렌식** ([FORENSIC.md](docs/FORENSIC.md)): `forensic expect <id|last> --doc … --term …` — 사용자가 기대한 문서/용어가 fts/vector/graph → 융합 → 리랭크 → 컨텍스트 → 답변 중 어디서 탈락했는지 단계별 표 + 수정안(규칙/pin/튜닝/코퍼스, `--propose` 로 HITL 등록). 모든 질의 결과에서 Web(🎯)·CLI·MCP 로 요청 가능.

---

## 4. CLI 요약 (`python -m llmwiki <cmd>` 또는 `run.bat <cmd>`, 상세: [CLI_FLOWS.md](docs/CLI_FLOWS.md))

| 영역 | 명령 |
|---|---|
| 빌드/운영 | `health` · `build [--full] [--channels fts,vector,graph] [status|verify --fix]` · **`build fts|vector|graph [--full]`**(채널 리빌드) · `embed report|status|clear-cache` · `corpus lint|types|example|lint-file|stats` · `mcp-source list|test|tools|retrieve|federated|ingest|enrich|fetch`(다른 RAG/검색 API 연동) · `watch --once` · `maintenance …` · `system` |
| 질의/디버그 | `query "…" [--preset q] [--trace] [--json] [--no-doc-expand] [--analyze --focus quality|speed|tokens]` · **`analyze <id|last> [--focus] [--print] [--out]`**(상세 분석 리포트) · `search fts|vector|graph` · `rules show|add|test` · `time "…"` · `pin add|list|test|remove` · `precompute run|status|clear` · `forensic last|list|summary|<id>` · **`forensic expect <id|last> --doc … --term … [--propose]`** |
| 품질 | `eval [--matrix]` · `trial run|list|compare|report` · `fusion show|compare` |
| 진화 | `evolve status|apply|reject|review|feedback` · `memory status|decay|consolidate|episodes` · `wiki` |
| 설정 | `config show [--effective]|set|paths` · `models show|test [--live]|set` · `tuning show|set|reset|doc` · `preset list|show|apply|diff` · `prompts list|show|reset` |
| 보안 | `users add|list|set-role|passwd|remove` (역할 viewer/class3/class2/class1/builder/admin) · `security show|init|audit|perms [set k=v|reset]` · `apikey add|list|remove` · `snapshot list|create|restore|prune` — 리빌드/파괴적 명령(`build --full`, `build fts|vector|graph`, `maintenance purge_requests`, `config reset`, `snapshot restore`)은 확인 문구 또는 `--yes`. 전역 `--user <id>`(+`LLMWIKI_PASSWORD`) 로 CLI 실행자 로그인 |
| 관측 | `requests list|last|show` · `logs tail|grep|files` · `arch [--flow …]` · `graph [--provenance …]` · `entity` · `docs` · `stats` |
| 인터페이스 | `serve [--port] [--host] [--insecure]` (Web UI + `POST /mcp`; 기본값 `config.json web_host/web_port`) · `mcp [--transport stdio|http --host --port] [--connect URL --token …] [--client-config [--url]]` (기본값 `mcp_transport/mcp_host/mcp_port/mcp_url`) |
| 검증 | `run.bat test`(unittest 87) · `python tools/verify/verify_cli.py|verify_web.py|verify_ui_wiring.py|verify_browser.py` — [docs/VERIFICATION_0915.md](docs/VERIFICATION_0915.md) |

---

## 5. 구조 한눈에

```
build : health → [mcp_ingest] → load_corpus(front matter) → diff(rename) → chunk_index(FTS+메타토큰+lint; 채널 fts) → embed(캐시·재개·적응형; 채널 vector) → graph(explicit/rule/cooccur/llm → doc_refs → communities; 채널 graph) → [doc_vectors] → wiki → prune → verify → warm → [precompute]
        채널만 다시: build fts | vector | graph  (chunks 불변, 다른 채널 불변, verify 로 결손 보고)
query : cache → time_scope → query_rules → router(+llm) → [query_expand] → pins → fts(+rule/alt/related) | vector(+alt) | graph | [doc_vector] | [external_rag: ext_<source> 가상 청크] → fuse+boost → [external_inject] → rerank → doc_expand(같은 문서 관련 청크) → context → evidence_check → [fallback L1..L4] → answer(llm|extractive|insufficient; LLM 실패 시 llm_report) → claim_check → forensic → evolve_capture → episode → log
mcp   : tools/list = built-in 11 + plugins/mcp_tools/*.py + [mcp_federation: <source>__<tool> 중계]  ←  외부 LLM (stdio | POST /mcp | 브리지)
        기대 결과 포렌식: forensic expect <id> --doc --term → 같은 설정으로 재실행 → 단계별 탈락 지점 + 수정안
evolve: capture | feedback | llm_review | forensics consolidate | forensic expect → proposals(strength, decay; pin/query_rule/tuning 적용 가능) → HITL apply → snapshot → rebuild → trial → 승격|롤백
access: 게스트(viewer) / 로컬 ID·PW / SSO / API 키 → 등급표(read<run<edit<index<rebuild<admin<destructive) × 권한 표(permissions) → Web · CLI(--user) · MCP(stdio | POST /mcp | 브리지)
```

## 6. 폴더

```
llmwiki/            패키지 (config, presets, tuning, prompts, logging_setup, profiler, buildlock, health, corpus, schema, store, providers, headless, rerankers,
                    embed_run, graph_rules, graph_build, graph_llm, wiki, precompute, query_engine, retrieval, fusion, query_rules, timeparse, pins,
                    answer, evidence, forensic, evolve, memory, trials, evalset, pipeline, cli, auth, snapshots, mcp(stdio+HTTP+브리지), mcp_client, architecture, web/)
security.json       로그인·역할·권한 표·API 키 (기본 admin kh82.kim — 공개 전 변경)
setup/              INSTALL.md, install.bat/.sh, check_env.py, config.example.json (+ .pat-gateway / .headless), .env.example,
                    security.example.json, agents.example.json, mcp_clients.example.json, requirements-optional.txt,
                    sample_corpus_modem/ (합성 모뎀 코퍼스 + questions.json — 기본 config 가 가리키는 곳), make_sample_corpus_modem.py, schedule_build.ps1/.sh
tools/verify/       전 기능 검증 하네스 (verify_cli / verify_web / verify_ui_wiring / verify_browser) — docs/VERIFICATION_0915.md
plugins/mcp_tools/  MCP 플러그인 도구 폴더 (_example_echo.py 예시, README) — docs/RAG_FEDERATION.md §3
corpus/             실제 문서를 넣는 폴더 (git 제외). 넣은 뒤 config.json corpus_dirs 를 ["corpus"] 로
docs/               현행 문서(§0 참조) · legacy/ (v1 문서)
schemas/ prompts/   문서 스키마 · 프롬프트 (첫 실행 시 기본값 생성)
data/               llmwiki.sqlite3, rules.json, mcp_cache/(MCP ingest 문서), snapshots/, build.lock
logs/ wiki/ eval/   로그(JSONL) · 위키 페이지 · 평가셋
tests/              unittest (python -m unittest discover -s tests)
```

## 7. 현재 상태 (2026-09-15)

- **2026-09-15 전 기능 검증** ([VERIFICATION_0915.md](docs/VERIFICATION_0915.md)): 단위 테스트 87/87 · CLI 184/184 명령(격리 환경) · Web 210/210 요청(게스트·viewer·class1·admin·API 키·MCP 11도구+페더레이션·CSRF) · UI 배선 OK · Edge headless JS 오류 0. 발견·수정 2건: 잘못된/폐기된 API 키가 게스트로 강등되던 것 → 401, `mcp --client-config` 경로 이중 이스케이프. 하네스는 `tools/verify/` 에 있어 다른 환경에서 그대로 재실행.
- **2026-09-15 상세 분석 모드** ([ANALYSIS_MODE.md](docs/ANALYSIS_MODE.md)): `analysis_mode` 토글·`query --analyze`·`analyze`·Web 📊·MCP `wiki_analysis`·`GET /api/analysis`, 리포트 §0~§9+부록, 렌즈 규칙 표. 테스트 6개 추가.
- **2026-09-15 다른 RAG 연동·MCP 확장** ([RAG_FEDERATION.md](docs/RAG_FEDERATION.md)): 외부 소스 전송 stdio/http/rest, 검색 채널 `external_rag`(가상 청크 융합·inject·인용), 페더레이션 `mcp_federation`(`<source>__<tool>`, 재귀 방지), 플러그인 `plugins/mcp_tools/`, 도구 `wiki_sources`/`wiki_external_search`. 테스트 7개 추가(87/87), 하네스 CLI 184/184 · Web 210/210.
- **2026-09-15 설정 외부화**: `serve`/`mcp` 기본값 `config.json web_host/web_port/mcp_transport/mcp_host/mcp_port/mcp_url`, 기대 결과 포렌식 임계 `tuning.json forensic_near_miss_mult/term_candidates/term_targets/pin_confidence`, 원본 예시 `setup/security.example.json`·`agents.example.json`·`mcp_clients.example.json`, `install.*` 가 security/agents 도 생성, `check_env.py` 가 보안/에이전트/서버/재시도 설정 보고, `mcp --client-config` — 총람 [BRINGUP_GUIDE §3.2](docs/BRINGUP_GUIDE.md).
- 검증 환경: Python 3.14.7, 합성 모뎀 코퍼스 38문서·190청크. 단위/통합 테스트 **87개** 통과(`python -m unittest discover -s tests`): 프로바이더 PAT 헤더·Anthropic 게이트웨이·headless `.cmd`·인증/SSO/감사 + **권한 표·익명·API 키·CLI 게이트(test_auth) · 채널 빌드 독립성·doc_expand·LLM 재시도/실패 보고·기대 결과 포렌식·MCP HTTP/브리지(test_features_0914)**.
- 실제 엔드포인트: 로컬 Ollama(llama3.1)로 `models test --live`, `build --full --llm-graph` 진행 표시까지 확인. 실서버 스모크(`serve --host 0.0.0.0`): 게스트 질의/기대 결과 포렌식 200, 게스트 빌드 401(로그인 안내), `kh82.kim` 로그인 → 권한 표 조회 → 채널 리빌드 428→승인→job 완료 → API 키 발급 → `POST /mcp` initialize/`wiki_forensic` → 감사 로그. 사내 PAT 게이트웨이·opencode·Mango MCP 는 fake 서버/mock 으로 배선을 검증했고 실제 연결은 포팅 환경에서 `models test --live`, `mcp-source test` 로 확인.
- 2026-09-14 변경 (2차, [IMPLEMENTATION_PLAN_0914.md](docs/IMPLEMENTATION_PLAN_0914.md)): (1) 역할 6단계·등급 7단계·admin 편집 권한 표·익명 viewer·CLI 게이트·API 키(SECURITY.md), (2) MCP Streamable HTTP(`/mcp`)·브리지·도구 `wiki_feedback`/`wiki_forensic`(MCP.md), (3) 채널별 빌드 `build fts|vector|graph`·`--channels`·`build_fts` 토글, (4) 문서 단위 확장 `doc_expand`(+튜닝 5개), (5) headless/LLM 재시도(`agents.json timeout_s/retries`, `llm_retries`)·`llm_report`, (6) 기대 결과 포렌식 `forensic expect`(FORENSIC.md), 제안 kind `pin/query_rule/tuning` 적용.
- 2026-09-14 변경 (1차): 빌드/질의 실시간 진행 표시와 "전체 리빌드가 starting… 에서 멈춤" 수정 · `llm_timeout`, PAT 게이트웨이 헤더 설정·`models test --live`·opencode Windows 실행 수정·`rerank_model`/`rerank_api_model` 분리, 로그인·역할·파괴적 작업 보호·스냅샷·감사 로그.
- 규모(5,000+50/일) 설계 근거와 남은 개선 항목은 [ANALYSIS_REPORT_0913.md](docs/ANALYSIS_REPORT_0913.md) §1.4 참조. `docs/llmwiki_guide.html`(인터랙티브 가이드)은 2차 변경 이전 구조를 보여 주며, 2차 기능은 위 문서들이 기준이다.
