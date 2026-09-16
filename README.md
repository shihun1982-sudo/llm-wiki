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
| **Web UI 를 처음 쓴다 / 화면 기능이 궁금하다** | [WEB_UI.md](docs/WEB_UI.md) §0 한 장 요약 → 필요한 절만. 버튼이 안 먹는 것 같으면 §9 자가 점검 |
| **일반 사용자(viewer)에게 어떻게 보이는지 확인하고 싶다** | 헤더의 `👁 권한 보기` 에서 viewer 선택 — 서버가 실제로 그 권한으로 처리한다(권한은 낮추기만 한다). 실제 로그인 흐름까지 보려면 `serve --host 0.0.0.0` — [WEB_UI.md](docs/WEB_UI.md) §8 |
| **내 화면 설정을 계정에 저장하고 어디서나 쓰고 싶다** | [WEB_UI.md](docs/WEB_UI.md) §4 — 헤더의 `💾 내 설정 저장`. 서버 설정은 바뀌지 않는다 |
| **30명이 동시에 쓰는데 느리거나 거절당한다 / 관리자로 제어하고 싶다** | [CONCURRENCY.md](docs/CONCURRENCY.md) §0 한 장 요약 → §3 `server.json` 권장값 → Web 관리 › 서버 모니터 (또는 `python -m llmwiki server stats`) → §9 문제 해결표 |
| **정해진 시각·주기로 빌드·수집·evolve 를 돌리고 싶다** | [SCHEDULER.md](docs/SCHEDULER.md) §2 시점 지정 → §3 동작 19종 예시 → `setup/schedule.example.json` 복사 → Web 설정 › 스케줄 또는 `python -m llmwiki schedule add` |
| **오래 걸리는 빌드·질의의 진행률을 보고 중간에 멈추고 싶다** | [CONCURRENCY.md](docs/CONCURRENCY.md) §4 시간 제한과 취소 (Web 진행 패널의 중지 버튼, CLI `Ctrl+C`, `server activity` / `server cancel <token>`) |
| **답에 기대한 문서가 왜 없는지 알고 싶다** | [FORENSIC.md](docs/FORENSIC.md) → `forensic expect last --doc <ID> --term <용어>` (Web Ask 의 🎯, MCP `wiki_forensic`) |
| **품질·속도·토큰이 마음에 안 드는데 어느 설정을 만질지 모르겠다** | [ANALYSIS_MODE.md](docs/ANALYSIS_MODE.md) → `query "…" --analyze --focus quality|speed|tokens` → `logs/analysis/req_<id>.md` 를 LLM 에게 첨부 (Web 토글 `analysis_mode` + 📊, MCP `wiki_analysis`) |
| **어떤 손잡이가 어느 단계에 작용하는지 한 장으로 보고, 그 자료를 통째로 LLM 에게 주고 싶다** | [OPTIMIZATION_GUIDE.md](docs/OPTIMIZATION_GUIDE.md)(자동 생성: `arch doc`) → `python -m llmwiki optimize last --out bundle.md` 로 **가이드+지금 설정+질의 실측+지시문**을 한 파일로 → 그 파일을 LLM 에게 첨부 (Web Ask 의 📦 최적화 자료 묶음 다운로드) |
| **다른 RAG·검색 API·MCP 서버를 붙이고 싶다 / 외부 LLM 이 우리 MCP 하나로 여러 RAG 를 쓰게 하고 싶다** | [RAG_FEDERATION.md](docs/RAG_FEDERATION.md) §0 결정표 → §5 절차 → `setup/mcp_sources.example.json` → BRINGUP_GUIDE §4.6 |
| **옮겨 세운 뒤 전부 정상인지 확인하고 싶다** | [VERIFICATION_0915.md](docs/VERIFICATION_0915.md) §6 → `tools/verify/` 스크립트(CLI 184 · Web 210 · UI 배선 · 브라우저 · 버튼 · 몽키 · MCP) → 실패 행만 BRINGUP_GUIDE §10 |
| **이 작업을 이어받는다 / 지금 무엇이 남아 있는지 알고 싶다** | [HANDOVER_0916.md](docs/HANDOVER_0916.md) — 2026-09-16 시점의 완료 작업, 남은 작업과 그 실행 방법, 알려진 하네스 결함, 아직 답하지 못한 사용자 질문 |
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
문서를 어떻게 써야 색인·검색이 잘 되는지에 대한 규칙. 파일 형식, 공통 front matter 필드(schema_version·doc_type·id·title·date·tags·module·hw·related), 7가지 문서 유형(issue·cl·sw_design·hw_design·coding_rule·weekly_report·tc_list)별 필수 필드와 권장 섹션, ID 규칙(ISSUE-nnnn, CL-nnnnn)과 그것이 만들어내는 결정적 관계, 완성 예시 문서, lint 규칙, 빌드에서 front matter 가 어떻게 쓰이는지, MCP 로 가져온 raw data 가 계약 문서로 변환되는 방식, **§8 형식이 없는 문서를 넣는 범용 변환기**(`tools/corpus_ingest.py` — 본문을 그대로 두고 `body_sha1` 로 무손실을 재검증, 텍스트 추출이 불가역인 형식은 원본을 `_originals/` 에 보관, 유형 불명은 `doc_type: note`).

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

**[docs/OPTIMIZATION_GUIDE.md](docs/OPTIMIZATION_GUIDE.md) — 손잡이 지도: 어떤 토글·튜닝·설정이 어느 단계에 어떻게 작용하는가 (자동 생성)**
`python -m llmwiki arch doc` 이 코드의 구조 레지스트리(`llmwiki/architecture.py`)와 튜닝 레지스트리(`llmwiki/tuning.py`)에서 생성하므로 단계나 설정이 바뀌면 문서도 바뀐다. §0 세 가지 렌즈(품질·속도·토큰)와 렌즈별 손잡이 우선순위, §1 설정이 사는 곳과 적용 시점(config/tuning/presets/server.json), §2 읽는 법, §3 전체 구조(query·build·evolve·watch 흐름의 단계 나열), §4~§7 흐름별 단계 표(trace 이름 ↔ 토글 ↔ 튜닝 키 ↔ config 키 ↔ 품질/속도/토큰 영향), §9 LLM 에게 최적화를 묻는 법.
최적화를 물을 때는 이 문서만 주지 말고 **`python -m llmwiki optimize last --out bundle.md`** 가 만드는 묶음을 준다 — A 지금 설정 스냅샷 · B 질의 한 건의 단계별 실측(분석 리포트) · C 요청 지시문 · D 이 손잡이 지도가 한 파일에 담긴다. Web 에서는 Ask › 📊 상세 분석 리포트 › **📦 최적화 자료 묶음 다운로드**(또는 묶음 복사), API 로는 `GET /api/optimize/bundle?request_id=<id>&focus=quality|speed|tokens`, 지도만 보려면 `GET /api/optimize/guide`.

**[docs/FORENSIC.md](docs/FORENSIC.md) — 포렌식 디버깅 (자동 + 기대 결과)**
자동 포렌식 확인 방법과, 사용자가 "이 문서/용어가 답에 있어야 했다"고 알려주면 같은 설정으로 검색을 재실행해 fts/vector/graph → 융합 → 리랭크 → 컨텍스트 → 답변 중 어느 단계에서 탈락했는지와 수정안(규칙/pin/튜닝/코퍼스)을 내는 `forensic expect` 의 동작·출력 예·해석 가이드·LLM 실패 보고와의 구분.

**[docs/ANALYSIS_MODE.md](docs/ANALYSIS_MODE.md) — 상세 분석 모드 (2026-09-15)**
토글 `analysis_mode`(또는 `query --analyze`)로 질의 한 건의 모든 단계 결과·설정 스냅샷·채널/융합/리랭크/컨텍스트 상세·답변 판정·**품질/속도/토큰 세 렌즈의 소견과 조절점(토글·튜닝 키=현재값)**·자동 포렌식·프롬프트 샘플을 `logs/analysis/req_<id>.md` 한 장으로 남긴다. LLM 에게 그대로 첨부해 튜닝을 묻는 절차, 렌즈 규칙 표, `analyze` CLI · Web 📊 · MCP `wiki_analysis` · `GET /api/analysis`.

**[docs/RAG_FEDERATION.md](docs/RAG_FEDERATION.md) — 다른 RAG 연동과 MCP 확장 (2026-09-15)**
`mcp_sources.json` 한 파일로 다른 팀의 LLM Wiki(http)·MCP 를 제공하는 사내 RAG·REST 검색 API(rest)·stdio MCP 서버를 붙이는 방법. 외부 결과를 검색 채널 `ext_<source>` 로 융합하는 `external_rag`, 외부 도구를 우리 `/mcp` 에 `<source>__<tool>` 로 노출하는 `mcp_federation`(재귀 방지 포함), 코드 수정 없이 도구를 늘리는 플러그인 폴더 `plugins/mcp_tools/`. 결정표·설정 필드 표·동작 상세·절차·검증·문제 해결.

**[docs/VERIFICATION_0915.md](docs/VERIFICATION_0915.md) — 2026-09-15 전 기능 검증 보고서**
Web UI 와 CLI 가 제공하는 모든 기능을 하나씩 실행해 얻은 결과: 단위 테스트 87, CLI 184 명령(격리 임시 환경), Web 210 요청(게스트/viewer/class1/admin/API 키/MCP 9도구/CSRF), UI 배선 정적 검사, Edge headless 렌더. 검증 중 고친 결함 2건(잘못된 API 키가 게스트로 강등되던 문제, `mcp --client-config` 경로 이스케이프), 요청 6항목 ↔ 검증 매핑, 다른 환경에서 재실행하는 법(`tools/verify/`).

**[docs/WEB_UI.md](docs/WEB_UI.md) — Web UI 사용 설명서 (2026-09-16)**
화면을 실제로 쓰는 사람과 "이 화면이 원래 이렇게 동작하는 게 맞나"를 확인하는 엔지니어용. §1 헤더 활동 표시기(작업 하나 = 막대 하나, 내 요청은 초록), §2 탭 고정과 1·2·3·4열 분할 보기(패널별 넓게·접기·이동·새로고침), §3 진행 중 작업 보드와 용량 게이지·중지, §4 **계정별 Web UI 프로파일**(저장되는 항목·서버 설정과의 분리·API), §5 상세 분석 리포트 다운로드와 **LLM 소견 받기**, §6 답변 반복 루프 자동 차단과 캐시 정리, §7 테마(기본 Light), §8 **viewer 화면으로 보는 법**, §9 증상별 자가 점검과 클릭 검증 도구.

**[docs/CONCURRENCY.md](docs/CONCURRENCY.md) — 다중 사용자 동시성·요청 관리·취소 (2026-09-15)**
30명이 함께 쓰는 서버의 운영 문서. §0 한 장 요약표, 예전 전역 락 구조의 문제와 지금 구조(요청 격리 · 읽기/쓰기 락 · 동시 실행 슬롯 · 대기열), `server.json` 키 전부와 30명 기준 권장값, 거절 응답(429/503)의 의미와 대응, 진행률·경과 시간·ETA 표시와 취소(Web·CLI·다른 프로세스 작업까지), 누가 무엇을 보고 제어할 수 있는지, IP/사용자 차단·점검 모드, 역할별 LLM 타임아웃·재시도·백오프·회로 차단과 LLM 이 최종 실패해도 답을 내는 대체 경로, SQLite 동시성(WAL·연결 풀·`database is locked` 대처), 증상별 문제 해결표, 검증 명령.

**[docs/SCHEDULER.md](docs/SCHEDULER.md) — 서버 스케줄러 (2026-09-15)**
`schedule.json` 하나로 정해진 시각·주기에 작업을 돌린다. 시점 지정 3가지(`every` · `at`+`days` · 5필드 `cron`), 동작 19종(증분/전체 빌드, URL 수집, 파이썬 스크립트, 임의 CLI 명령, 질의, LLM·headless 호출, MCP 수집, 유지보수, HTTP 호출, **evolve · memory · precompute · eval · trial · snapshot · wiki · forensic · embed_report**)의 JSON 예시, 동시성·취소와의 관계, Web(설정 › 스케줄)·CLI(`schedule list|add|run|enable`) 조작, 실행 이력과 상태 파일, 서버를 상시 띄우지 않는 환경에서 OS 스케줄러로 같은 동작을 부르는 법, 문제 해결, 검증.

**[docs/IMPLEMENTATION_PLAN_0915.md](docs/IMPLEMENTATION_PLAN_0915.md) — 2026-09-15 구현 계획서 (다중 사용자 서버화의 판정·대안·설계·검증)**
요청 8항목(병렬 처리 · 역할별 LLM 정책 · 30명 동시 사용과 관리자 제어 · 진행률과 취소 · 스케줄러 · 모델 목록 · 로그인 뒤로가기 버그 · DEBUG 검증)에 대한 판정표, 선택한 설계와 **택하지 않은 대안과 그 이유**(멀티프로세스 · PostgreSQL · asyncio 재작성), 호환성이 바뀐 지점(요청 단위 프리셋 미리보기), 멍키 테스트와 스트레스 테스트로 찾은 서버 결함 10건의 원인과 수정, 터미널 한글 깨짐의 원인과 해법, 실제 10MB 코퍼스 구성과 무손실 변환, Web UI 사용성 항목별 구현, 신규 파일·설정 키 목록, 남은 개선 여지.

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
| `config.json` | 코퍼스 경로, 프로바이더/**역할별 모델과 LLM 정책**(answer·rerank·extract·summary·review·expand·verify·forensic 마다 `timeout_s`·`retries`·`backoff`·`budget_s`·`circuit_failures` 를 따로), **59개 토글**(+`build_fts`·`doc_expand`·`llm_failure_report`), 운영 수치(배치·WAL·로그·timezone·`llm_timeout`·`llm_retries`·`llm_retry_backoff`·`db_busy_timeout_s`·`db_pool_size`), **터미널 인코딩**(`console_encoding`·`console_set_codepage`), **서버/MCP 기본값**(`web_host`·`web_port`·`mcp_transport`·`mcp_host`·`mcp_port`·`mcp_url` — `serve`/`mcp` 플래그 생략 시) — 원본 `setup/config.example.json` |
| `server.json` | **동시 사용자 제어**: 동시 읽기 수와 사용자/IP별 상한(`concurrency`)·대기열 크기와 대기 시간·빌드 중 읽기 허용 방식·질의/검색/MCP 시간 제한(`timeouts`)·분당 속도 제한(`rate_limit`)·세션 수와 유휴 시간(`sessions`)·IP 및 사용자 차단과 점검 모드(`access`)·모니터 공개 범위(`monitor`) — [docs/CONCURRENCY.md](docs/CONCURRENCY.md). 원본 `setup/server.example.json`. 환경변수 `LLMWIKI_SERVER_<섹션>_<키>` 로도 덮어쓴다 |
| `schedule.json` | **정해진 시각·주기에 돌릴 작업**: 증분/전체 빌드, URL 수집, 파이썬 스크립트, 임의 CLI 명령, 질의, LLM·headless 호출, MCP 수집, evolve·memory·precompute·eval·trial·snapshot·wiki·forensic·embed_report — [docs/SCHEDULER.md](docs/SCHEDULER.md). 원본 `setup/schedule.example.json`. 저장하면 서버가 자동으로 다시 읽는다 |
| `models.json` | **쓸 수 있는 LLM 목록**(id·provider·label·역할·태그·context·사용 여부). Web 설정의 모델 드롭다운과 `models list` 가 이 목록을 보여 준다. `models discover` 로 Ollama·OpenAI 호환 게이트웨이에서 실제 제공 모델을 가져와 추가. 원본 `setup/models.example.json` |
| `data/profiles.json` | **계정별 Web UI 설정**(테마·토글·프리셋·오버라이드·고정 탭·분할 보기). 서버 기본 설정과 분리되어 있어 한 사람이 바꿔도 남에게 영향이 없다 |
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

2026-09-14/15 기능(권한·MCP·채널 빌드·doc_expand·재시도·포렌식·동시성·스케줄러·모델 목록)의 **파일·키·기본값·예시·확인 명령 총람**은 [BRINGUP_GUIDE.md §3.2](docs/BRINGUP_GUIDE.md). `setup/check_env.py` 가 security/agents/서버 포트/재시도/동시성/스케줄/모델 카탈로그/터미널 인코딩 설정을 한 줄씩 보고한다.

설정 파일은 모두 **원자적으로 저장**된다(`llmwiki/atomicio.py`). 여러 관리자가 같은 순간에 저장해도 파일이 반쪽으로 남거나 서로의 내용을 덮어쓰지 않고, 저장 중에 읽는 요청은 항상 이전 내용을 온전히 본다.

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
- **문서 단위 확장 (doc_expand)**: 리랭크 상위 청크가 속한 문서의 나머지 청크를 문서 순서로 컨텍스트에 추가(`[C#]` 인용, 상한 `doc_expand_*` 튜닝). 고르는 방식은 `doc_expand_mode` 로 keyword·vector·hybrid 중에 정하고, **`full` 로 두면 근거가 나온 문서를 통째로** 읽힌다(점수로 거르지 않고 `doc_expand_max_chunks`·`context_max_chars` 로만 제한 — 설계 문서·절차서처럼 문서 하나를 끝까지 봐야 하는 질문에). 토글 on/off, speed/token 프리셋은 off. — BRINGUP_GUIDE §7.1
- **LLM 재시도·실패 보고**: headless(`agents.json timeout_s=300, retries=3`)와 모든 프로바이더의 timeout/네트워크 오류를 `llm_retries` 회 재시도하고, 최종 실패는 결과 `llm_report` + 답변 상단 `⚠ LLM 실행 보고`(역할·시도 횟수·오류·대체 경로) + 빌드 alerts 로 남긴다. — BRINGUP_GUIDE §4.3
- **기대 결과 포렌식** ([FORENSIC.md](docs/FORENSIC.md)): `forensic expect <id|last> --doc … --term …` — 사용자가 기대한 문서/용어가 fts/vector/graph → 융합 → 리랭크 → 컨텍스트 → 답변 중 어디서 탈락했는지 단계별 표 + 수정안(규칙/pin/튜닝/코퍼스, `--propose` 로 HITL 등록). 모든 질의 결과에서 Web(🎯)·CLI·MCP 로 요청 가능.
- **동시 사용 (30명 기준)** ([CONCURRENCY.md](docs/CONCURRENCY.md)): 질의는 서로 막지 않고 병렬로 처리된다(요청마다 설정 사본·전용 DB 연결·독립 프로파일 카운터). 쓰기만 짧게 배타 구간을 잡고, 증분 빌드는 읽기와 함께 돌아간다. Web·CLI·MCP·스케줄러가 **하나의 요청 관리자**를 공유해 동시 실행 슬롯·대기열·속도 제한·차단·점검 모드가 모든 경로에 똑같이 적용된다. 용량을 넘으면 기다리게 두지 않고 `429`/`503` 과 재시도 시점을 돌려준다. 설정은 `server.json`.
- **역할별 LLM 정책**: answer·rerank·extract 처럼 역할마다 `timeout_s`·`retries`·`backoff`(지수/선형+지터)·`budget_s`(재시도 포함 총 시간)·회로 차단(`circuit_failures`·`circuit_cooldown_s`)·**`max_tokens`**(출력 토큰 상한)을 따로 준다. 기본 배포값은 보조 단계를 빨리 포기하고(expand 30초·rerank 60초·verify 90초, 각 1회 재시도) 사용자가 기다리는 answer 는 넉넉히(240초·2회), 빌드·배치는 길게(extract 120초·review 300초) 잡는다. 같은 모델이 연속 실패하면 잠시 호출을 끊어 모두가 타임아웃을 기다리는 상황을 막고, **LLM 이 끝내 실패해도 추출식 답변·로컬 리랭크·규칙 그래프로 최선의 결과를 낸다**(무엇을 무엇으로 대체했는지 답변 상단과 `llm_report` 에 표시).
- **진행률과 취소**: 빌드·질의의 단계·%·경과·남은 예상 시간·대기열 위치를 Web 진행 패널과 CLI 한 줄 표시로 보여 주고, 언제든 중지할 수 있다(단계·임베딩 배치·LLM 호출 직전에서 협조적으로 끊고 빌드는 진행분을 저장). CLI·스케줄러·MCP 작업도 `data/live/` 를 통해 같은 화면에 보이고 서버에서 취소된다.
- **스케줄러** ([SCHEDULER.md](docs/SCHEDULER.md)): `schedule.json` 에 시각·주기(`every` / `at`+요일 / 5필드 `cron`)와 동작 19종을 적으면 서버가 실행한다. 증분 빌드·URL 수집·파이썬 스크립트·임의 CLI 명령은 물론 evolve·memory·precompute·eval·trial·snapshot·wiki·forensic·embed_report 까지 포함한다. 파일을 저장하면 자동 재적재되고, 실행 이력과 다음 실행 시각을 Web 설정 › 스케줄과 `schedule list` 에서 본다.
- **모델 목록**: `models.json` 에 쓸 수 있는 모델을 넣고 빼면 Web 설정의 역할별 드롭다운과 `models list` 에 그대로 반영된다. `models discover` 로 Ollama·OpenAI 호환 게이트웨이의 실제 제공 모델을 조회해 추가할 수 있다.
- **관리자 모니터**: Web 관리 › 서버 모니터(또는 `server stats|activity|limits|block|kick`)에서 진행 중·대기 중 요청, 사용자·IP별 사용량, 거절 사유, 세션 목록, LLM 회로 상태를 보고 제한값을 바꾸거나 특정 IP·사용자를 차단·강제 로그아웃한다. 일반 사용자도 "지금 서버에서 무엇이 돌고 있는지"는 볼 수 있어(공개 범위는 `server.json monitor` 로 조절) 느린 이유를 스스로 확인한다.
- **계정별 Web UI 프로파일** ([WEB_UI.md](docs/WEB_UI.md) §4): 테마·토글·프리셋·요청 단위 오버라이드·질의 모드·고정 탭·화면 분할 상태(열 수·높이·넓게·접힘)·마지막 화면을 계정에 저장해 어느 PC에서 접속하든 같은 화면으로 시작한다. 로그인하면 자동으로 불러온다. 서버 공용 설정과 완전히 분리되어 있고, 게스트는 브라우저에만 남는다.
- **Web UI 화면 구성** ([WEB_UI.md](docs/WEB_UI.md)): 탭 고정 + 1·2·3·4열 분할 보기(패널별 넓게·접기·순서 이동·개별 새로고침, 머리글 고정·내용만 스크롤), 헤더의 항상 보이는 활동 표시기(작업 하나 = 막대 하나, 내 요청은 초록, 대기는 점선), 진행 중 작업 보드와 용량 게이지. 화면 갱신 조회는 **하나로 합쳐** 여러 패널이 나눠 쓴다.
- **답변 반복 루프 차단** ([WEB_UI.md](docs/WEB_UI.md) §6): 작은 모델이 같은 구절을 되풀이하는 고장을 탐지해 잘라내고 이유를 표시하며, 그런 답변은 캐시에 넣지 않는다. 이미 저장된 것은 `precompute check` · `precompute clear --broken` 으로 정리한다.
- **분석 리포트 LLM 소견** ([WEB_UI.md](docs/WEB_UI.md) §5): 상세 분석 리포트를 LLM 에게 읽히고 "어떤 설정을 어떤 값으로 바꾸면 좋아지는지"를 표로 받는다. 그대로 Evolve 제안(HITL)으로 등록할 수 있다.
- **터미널 한글**: CLI 가 시작할 때 콘솔 코드페이지와 표준 입출력 인코딩을 UTF-8 로 맞춰, 로캘이 다른 PC 나 출력을 파일로 넘길 때 한글이 깨지거나 명령이 `UnicodeEncodeError` 로 죽던 문제를 막는다(`console_encoding`).

---

## 4. CLI 요약 (`python -m llmwiki <cmd>` 또는 `run.bat <cmd>`, 상세: [CLI_FLOWS.md](docs/CLI_FLOWS.md))

| 영역 | 명령 |
|---|---|
| 빌드/운영 | `health` · `build [--full] [--channels fts,vector,graph] [status|verify --fix]` · **`build fts|vector|graph [--full]`**(채널 리빌드) · `embed report|status|clear-cache` · `corpus lint|types|example|lint-file|stats` · `mcp-source list|test|tools|retrieve|federated|ingest|enrich|fetch`(다른 RAG/검색 API 연동) · `watch --once` · `maintenance …` · `system` |
| 질의/디버그 | `query "…" [--preset q] [--trace] [--json] [--no-doc-expand] [--analyze --focus quality|speed|tokens]` · **`analyze <id|last> [--focus] [--print] [--out]`**(상세 분석 리포트) · `search fts|vector|graph` · `rules show|add|test` · `time "…"` · `pin add|list|test|remove` · `precompute run|status|clear` · `forensic last|list|summary|<id>` · **`forensic expect <id|last> --doc … --term … [--propose]`** |
| 품질 | `eval [--matrix]` · `trial run|list|compare|report` · `fusion show|compare` |
| 진화 | `evolve status|apply|reject|review|feedback` · `memory status|decay|consolidate|episodes` · `wiki` |
| 설정 | `config show [--effective]|set|paths` · `models show|test [--live]|set` · **`models list [--role r] [--provider p]`·`models catalog add|remove`·`models discover`·`models policy`**(쓸 수 있는 모델 목록과 역할별 타임아웃/재시도 정책) · `tuning show|set|reset|doc` · `preset list|show|apply|diff` · `prompts list|show|reset` |
| 서버 운영 | **`server stats|activity|limits [set k=v]|block|unblock|kick|maintenance|circuits|cancel <token>`** — 진행 중·대기 중 요청 보기, 동시성·속도 제한 변경(`server.json` 저장), IP/사용자 차단, 세션 강제 종료, LLM 회로 해제, 작업 중지. `--server URL` 로 원격 서버에도 사용 ([CONCURRENCY.md](docs/CONCURRENCY.md)) |
| 스케줄 | **`schedule list|show|add|remove|enable|disable|run <name>|history|validate`** — 정해진 시각·주기 작업. 서버가 떠 있으면 서버가 돌리고, `schedule run` 은 OS 스케줄러에서 단발로 부를 때 쓴다 ([SCHEDULER.md](docs/SCHEDULER.md)) |
| 보안 | `users add|list|set-role|passwd|remove` (역할 viewer/class3/class2/class1/builder/admin) · `security show|init|audit|perms [set k=v|reset]` · `apikey add|list|remove` · `snapshot list|create|restore|prune` — 리빌드/파괴적 명령(`build --full`, `build fts|vector|graph`, `maintenance purge_requests`, `config reset`, `snapshot restore`)은 확인 문구 또는 `--yes`. 전역 `--user <id>`(+`LLMWIKI_PASSWORD`) 로 CLI 실행자 로그인 |
| 관측 | `requests list|last|show` · `logs tail|grep|files` · `arch [show|doc] [--flow …]`(**`arch doc` = [OPTIMIZATION_GUIDE.md](docs/OPTIMIZATION_GUIDE.md) 재생성**) · **`optimize <id|last> [--focus quality|speed|tokens] [--out bundle.md]`**(가이드+설정+실측+지시문을 한 파일로 — LLM 에게 그대로 준다) · `graph [--provenance …]` · `entity` · `docs` · `stats` |
| 인터페이스 | `serve [--port] [--host] [--insecure]` (Web UI + `POST /mcp`; 기본값 `config.json web_host/web_port`) · `mcp [--transport stdio|http --host --port] [--connect URL --token …] [--client-config [--url]] [--doctor [--check-sources]]` (기본값 `mcp_transport/mcp_host/mcp_port/mcp_url`; **`--doctor` 는 도구·스키마·플러그인·외부 소스·페더레이션·인증을 한 번에 자가 점검**) |
| 검증 | `run.bat test`(unittest 135) · `python tools/verify/verify_cli.py|verify_web.py|verify_ui_wiring.py|verify_browser.py|verify_buttons.py|verify_monkey.py`(무작위 입력 내성) · **`verify_mcp.py`**(MCP 전송 3종·도구·확장·동시성) — [docs/VERIFICATION_0915.md](docs/VERIFICATION_0915.md) |
| 코퍼스 도구 | `python tools/corpus_ingest.py <경로> --out corpus/imported`(형식 없는 문서를 계약 형식으로 **무손실** 변환, `--verify-only` 로 재검증) · `python tools/fetch_rfc_corpus.py --max-mb 8`(공개 RFC 로 실데이터 코퍼스 구성) |

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

- **2026-09-16 실사용 점검과 Web UI 정리** ([WEB_UI.md](docs/WEB_UI.md) · [VERIFICATION_0915.md](docs/VERIFICATION_0915.md) §4.1): 실제로 써 보면서 나온 문제를 모두 고쳤다. **전체 리빌드가 청크 수의 제곱으로 느려지던 것**(`fts_trigram` 을 켜면 16,882 청크 기준 색인 단계만 약 17분 → 몇 초, §4.2 실측표), **답변이 같은 구절을 수십 번 반복하던 것**(자동 차단 + 캐시 오염 방지 + 이미 저장된 것 정리 도구), **화면이 한참 멈췄다 한꺼번에 갱신되던 것**(서버를 HTTP/1.1 keep-alive 로, 화면 갱신 조회를 하나로 통합), 메모리 화면의 `[object Object]`, 포렌식 `진단 실행` 무반응, 프로파일이 잘못된 요청 한 번에 지워지던 것. 화면은 1·2·3·4열 분할 보기, 헤더 활동 표시기(작업 하나 = 막대 하나, 내 요청은 초록), 진행 중 작업 보드, 분석 리포트 **LLM 소견**, 기본 테마 Light 로 정리했다. 버튼을 **실제로 눌러** 확인하는 `verify_click.py` 와 리빌드 속도를 지키는 `bench_fts.py` 를 검증 도구에 추가했다.
- **2026-09-15 다중 사용자 서버화** ([IMPLEMENTATION_PLAN_0915.md](docs/IMPLEMENTATION_PLAN_0915.md) · [CONCURRENCY.md](docs/CONCURRENCY.md) · [SCHEDULER.md](docs/SCHEDULER.md)): 전역 락 하나로 한 번에 한 요청만 처리하던 서버를 **30명 동시 사용**을 견디는 구조로 바꿨다. 요청마다 설정 사본·전용 DB 연결·독립 카운터를 갖는 요청 격리, Web/CLI/MCP/스케줄러가 공유하는 요청 관리자(읽기·쓰기 락, 동시 실행 슬롯, 대기열, 속도 제한, 차단, 점검 모드, 감시 스레드), 역할별 LLM 타임아웃·재시도·백오프·회로 차단과 실패 시 대체 경로, 진행률·ETA 표시와 취소, `schedule.json` 스케줄러(동작 19종), `models.json` 모델 목록, 계정별 Web UI 프로파일, 로그인 뒤로가기 버그 수정, 터미널 한글 깨짐 수정. 신규 설정 파일 `server.json`·`schedule.json`·`models.json`(원본은 `setup/*.example.json`).
- **2026-09-15 스트레스·멍키 검증**: 동시성·스트레스 테스트 **43개** 추가(30 동시 질의 평균 43ms·p95 62ms, 빌드 중 질의, 긴 작업 취소, 잘못된 입력 25종, 설정 파일 동시 저장) + 터미널 인코딩 테스트 8개. 무작위 입력 하네스 `tools/verify/verify_monkey.py` 로 Web·MCP·CLI 에 제어문자·서러게이트·거대 본문·잘못된 JSON 을 쏟아부어 **서버 결함 13건**을 찾아 고쳤다. 특히 한 사람의 잘못된 입력이 **모두에게 오래 남는** 세 가지였다: `query_rules` 에 사전이 아닌 값이 저장되면 이후 모든 질의가 깨지던 것, 짝 없는 서러게이트가 섞인 질의 한 건 뒤로 서버 모니터가 계속 400 을 내던 것, 느린 콘솔 CLI 두 개가 읽기 슬롯을 잡은 채 다른 64명을 대기열에 쌓던 것. 최종 재실행 결과 500 오류 0.
- **2026-09-15 실데이터 코퍼스**: 공개 RFC 163편(8.4MB)과 형식 없는 프로젝트 문서 187개(2.5MB)를 계약 형식으로 변환해 색인 — 문서 352 · 청크 16,882 · 엔티티 607 · 관계 11,266. 변환기 `tools/corpus_ingest.py` 는 본문을 그대로 보존하고 `body_sha1` 로 **무손실을 재검증**하며(151/151 일치), 텍스트 추출이 불가역인 형식(HTML·PDF·docx)은 원본을 `_originals/` 에 보관한다.
- **2026-09-15 전 기능 검증** ([VERIFICATION_0915.md](docs/VERIFICATION_0915.md)): 단위 테스트 87/87 · CLI 184/184 명령(격리 환경) · Web 210/210 요청(게스트·viewer·class1·admin·API 키·MCP 11도구+페더레이션·CSRF) · UI 배선 OK · Edge headless JS 오류 0. 발견·수정 2건: 잘못된/폐기된 API 키가 게스트로 강등되던 것 → 401, `mcp --client-config` 경로 이중 이스케이프. 하네스는 `tools/verify/` 에 있어 다른 환경에서 그대로 재실행.
- **2026-09-15 상세 분석 모드** ([ANALYSIS_MODE.md](docs/ANALYSIS_MODE.md)): `analysis_mode` 토글·`query --analyze`·`analyze`·Web 📊·MCP `wiki_analysis`·`GET /api/analysis`, 리포트 §0~§9+부록, 렌즈 규칙 표. 테스트 6개 추가.
- **2026-09-15 다른 RAG 연동·MCP 확장** ([RAG_FEDERATION.md](docs/RAG_FEDERATION.md)): 외부 소스 전송 stdio/http/rest, 검색 채널 `external_rag`(가상 청크 융합·inject·인용), 페더레이션 `mcp_federation`(`<source>__<tool>`, 재귀 방지), 플러그인 `plugins/mcp_tools/`, 도구 `wiki_sources`/`wiki_external_search`. 테스트 7개 추가(87/87), 하네스 CLI 184/184 · Web 210/210.
- **2026-09-15 설정 외부화**: `serve`/`mcp` 기본값 `config.json web_host/web_port/mcp_transport/mcp_host/mcp_port/mcp_url`, 기대 결과 포렌식 임계 `tuning.json forensic_near_miss_mult/term_candidates/term_targets/pin_confidence`, 원본 예시 `setup/security.example.json`·`agents.example.json`·`mcp_clients.example.json`, `install.*` 가 security/agents 도 생성, `check_env.py` 가 보안/에이전트/서버/재시도 설정 보고, `mcp --client-config` — 총람 [BRINGUP_GUIDE §3.2](docs/BRINGUP_GUIDE.md).
- 검증 환경: Python 3.14.7, 합성 모뎀 코퍼스 38문서·190청크. 단위/통합 테스트 **87개** 통과(`python -m unittest discover -s tests`): 프로바이더 PAT 헤더·Anthropic 게이트웨이·headless `.cmd`·인증/SSO/감사 + **권한 표·익명·API 키·CLI 게이트(test_auth) · 채널 빌드 독립성·doc_expand·LLM 재시도/실패 보고·기대 결과 포렌식·MCP HTTP/브리지(test_features_0914)**.
- 실제 엔드포인트: 로컬 Ollama(llama3.1)로 `models test --live`, `build --full --llm-graph` 진행 표시까지 확인. 실서버 스모크(`serve --host 0.0.0.0`): 게스트 질의/기대 결과 포렌식 200, 게스트 빌드 401(로그인 안내), `kh82.kim` 로그인 → 권한 표 조회 → 채널 리빌드 428→승인→job 완료 → API 키 발급 → `POST /mcp` initialize/`wiki_forensic` → 감사 로그. 사내 PAT 게이트웨이·opencode·Mango MCP 는 fake 서버/mock 으로 배선을 검증했고 실제 연결은 포팅 환경에서 `models test --live`, `mcp-source test` 로 확인.
- 2026-09-14 변경 (2차, [IMPLEMENTATION_PLAN_0914.md](docs/IMPLEMENTATION_PLAN_0914.md)): (1) 역할 6단계·등급 7단계·admin 편집 권한 표·익명 viewer·CLI 게이트·API 키(SECURITY.md), (2) MCP Streamable HTTP(`/mcp`)·브리지·도구 `wiki_feedback`/`wiki_forensic`(MCP.md), (3) 채널별 빌드 `build fts|vector|graph`·`--channels`·`build_fts` 토글, (4) 문서 단위 확장 `doc_expand`(+튜닝 5개), (5) headless/LLM 재시도(`agents.json timeout_s/retries`, `llm_retries`)·`llm_report`, (6) 기대 결과 포렌식 `forensic expect`(FORENSIC.md), 제안 kind `pin/query_rule/tuning` 적용.
- 2026-09-14 변경 (1차): 빌드/질의 실시간 진행 표시와 "전체 리빌드가 starting… 에서 멈춤" 수정 · `llm_timeout`, PAT 게이트웨이 헤더 설정·`models test --live`·opencode Windows 실행 수정·`rerank_model`/`rerank_api_model` 분리, 로그인·역할·파괴적 작업 보호·스냅샷·감사 로그.
- 규모(5,000+50/일) 설계 근거와 남은 개선 항목은 [ANALYSIS_REPORT_0913.md](docs/ANALYSIS_REPORT_0913.md) §1.4 참조. `docs/llmwiki_guide.html`(인터랙티브 가이드)은 2차 변경 이전 구조를 보여 주며, 2차 기능은 위 문서들이 기준이다.
