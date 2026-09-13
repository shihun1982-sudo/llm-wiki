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
| `config.json` | 코퍼스 경로, 프로바이더/**역할별 모델**(answer·rerank·extract·summary·review·expand·verify·forensic), **56개 토글**, 운영 수치(배치·WAL·로그·timezone) |
| `.env` | API 키 + 모든 설정의 env 오버라이드 (`LLMWIKI_<KEY>`, `LLMWIKI_TOGGLE_<NAME>`) |
| `tuning.json` | 알고리즘 상수 130+ (FTS·라우터·그래프·융합·근거 판정·claim·메모리…) — `tuning show`, Web › Settings › 튜닝 |
| `presets.json` | **품질/속도/토큰/offline/deep_research** 묶음 — `--preset quality`, 사이드바 체크박스 |
| `query_rules.json` | **규칙 기반 질의 확장 사전**: acronym / synonym / alias / related / exclude / compound (유형별로 다르게 적용) |
| `data/rules.json` | 그래프 사전·정규식 + **ID 패턴·결정적 링크 규칙**(CL→Issue) |
| `schemas/` | 문서 유형별 스키마(schema_version), 추론 규칙, 마이그레이션 |
| `prompts/*.md` | 역할별 프롬프트 + **answer_guide.md**(Evidence-rich · Structured · Grounded 답변 가이드) |
| `pins.json` `agents.json` `mcp_sources.json` | 고정 근거 · headless 에이전트 명령 템플릿 · 외부 MCP(Mango) 매핑 |

---

## 3. 주요 기능 (v3)

- **문서 계약**: front matter(schema_version, doc_type, id, date, tags, module, hw, related) → doc_meta · 메타 토큰 · lint(`corpus lint`).
- **결정적 관계 + provenance**: explicit(front matter) / rule(ID 패턴) / cooccur / llm / human 구분, confidence 관리, 그래프 탐색 가중(`provenance_w`), 노드 ↔ 원본 문서(doc_refs).
- **프로바이더**: Anthropic, **OpenAI-compatible**(chat/embeddings), Ollama, **rerank 전용 엔드포인트**(Cohere/Jina/vLLM/Voyage), **Generic Headless Agent**(opencode/claude/codex CLI subprocess, `agents.json`), Voyage/ST/hash 임베더.
- **빌드 견고성**: `health` 사전 검사, 파일 락, 임베딩 **내용 해시 캐시**(rename/재빌드 0 비용), **재개/체크포인트**, **적응형 배치·WAL 관리**, 진행률·coverage 리포트(`embed report`, Web), 정합성 검증 `build verify --fix`, 삭제/rename 추적.
- **한글**: 조사 제거 + bigram + 스크립트 경계 분리 + **복합어 사전** + 선택적 **kiwi 형태소** + **trigram 폴백**.
- **질의**: **한국어 상대 시간 파싱**(지난주·3일전·Q3, timezone 설정) → **규칙 확장**(유형별) → 라우터(+LLM) → **LLM 확장/분해**(원 질의 유지) → pin → fts/vector/graph/doc_vector → **융합 5방식**(rrf·weighted·zscore·dbsf·rrf_boost) + **post-boost**(문서유형·시간·최신성·pin·provenance·피드백·exclude) → 리랭크 → 컨텍스트.
- **근거**: **evidence_check**(휴리스틱/LLM) → **fallback 루프**(rules→expand→graph→wide→mcp, attempt/token/latency 예산) → **insufficient_data 응답** → **claim_check**(인용 존재 + 실제 지지 검증, groundedness/citation_precision, mark/drop/refine).
- **포렌식**: 단계별 진단 규칙으로 "왜 답을 못 만들었나" 기록(`forensic last`), 누적 → `memory consolidate` → corpus_gap/query_rule/tuning 제안(HITL).
- **메모리**: episodic(에피소드·피드백 부스트) + semantic(승인 규칙·pin) + decay(반감기).
- **평가**: eval 경로 = 사용자 경로. **trial** 저장/비교(지표 Δ, 질문별 승/패, 설정 diff), `fusion compare`.
- **로그**: `logs/` JSON Lines(정상 동작 포함), `run_id` 로 요청 프로파일과 연결(`logs grep --request <id>`).
- **Web UI**: 워크플로 기준 7그룹(Ask / Corpus / Knowledge / Quality / Evolve / Settings / Observability), 프리셋 체크박스, 토글 사이드바 자동 생성, **테마**(light/dark/high-contrast/solarized, 확장 가능), 콘솔에서 CLI 전체 실행.
- **MCP**: `wiki_query(mode=deep …)`, `wiki_search`, `wiki_related`, `wiki_doc`, `wiki_entity`, `wiki_propose`, `wiki_status` — use case(구현/코드리뷰/이슈분석/리팩토링/unified search)별 skill·agent 는 이 도구 위에 별도로 만든다.

---

## 4. CLI 요약 (`python -m llmwiki <cmd>` 또는 `run.bat <cmd>`, 상세: [CLI_FLOWS.md](docs/CLI_FLOWS.md))

| 영역 | 명령 |
|---|---|
| 빌드/운영 | `health` · `build [--full] [status|verify --fix]` · `embed report|status|clear-cache` · `corpus lint|types|example|lint-file|stats` · `mcp-source list|test|ingest|enrich` · `watch --once` · `maintenance …` · `system` |
| 질의/디버그 | `query "…" [--preset q] [--trace] [--json]` · `search fts|vector|graph` · `rules show|add|test` · `time "…"` · `pin add|list|test|remove` · `precompute run|status|clear` · `forensic last|list|summary|<id>` |
| 품질 | `eval [--matrix]` · `trial run|list|compare|report` · `fusion show|compare` |
| 진화 | `evolve status|apply|reject|review|feedback` · `memory status|decay|consolidate|episodes` · `wiki` |
| 설정 | `config show [--effective]|set|paths` · `models show|test|set` · `tuning show|set|reset|doc` · `preset list|show|apply|diff` · `prompts list|show|reset` |
| 관측 | `requests list|last|show` · `logs tail|grep|files` · `arch [--flow …]` · `graph [--provenance …]` · `entity` · `docs` · `stats` |
| 인터페이스 | `serve [--port]` · `mcp` |

---

## 5. 구조 한눈에

```
build : health → [mcp_ingest] → load_corpus(front matter) → diff(rename) → chunk_index(FTS+메타토큰+lint) → embed(캐시·재개·적응형) → graph(explicit/rule/cooccur/llm → doc_refs → communities) → [doc_vectors] → wiki → prune → verify → warm → [precompute]
query : cache → time_scope → query_rules → router(+llm) → [query_expand] → pins → fts(+rule/alt/related) | vector(+alt) | graph | [doc_vector] → fuse+boost → rerank → context → evidence_check → [fallback L1..L4] → answer(llm|extractive|insufficient) → claim_check → forensic → evolve_capture → episode → log
evolve: capture | feedback | llm_review | forensics consolidate → proposals(strength, decay) → HITL apply → snapshot → rebuild → trial → 승격|롤백
```

## 6. 폴더

```
llmwiki/            패키지 (config, presets, tuning, prompts, logging_setup, profiler, buildlock, health, corpus, schema, store, providers, headless, rerankers,
                    embed_run, graph_rules, graph_build, graph_llm, wiki, precompute, query_engine, retrieval, fusion, query_rules, timeparse, pins,
                    answer, evidence, forensic, evolve, memory, trials, evalset, pipeline, cli, mcp, mcp_client, architecture, web/)
setup/              INSTALL.md, install.bat/.sh, check_env.py, config.example.json, .env.example, requirements-optional.txt,
                    sample_corpus_modem/ (합성 모뎀 코퍼스 + questions.json — 기본 config 가 가리키는 곳), make_sample_corpus_modem.py, schedule_build.ps1/.sh
corpus/             실제 문서를 넣는 폴더 (git 제외). 넣은 뒤 config.json corpus_dirs 를 ["corpus"] 로
docs/               현행 문서(§0 참조) · legacy/ (v1 문서)
schemas/ prompts/   문서 스키마 · 프롬프트 (첫 실행 시 기본값 생성)
data/               llmwiki.sqlite3, rules.json, mcp_cache/(MCP ingest 문서), snapshots/, build.lock
logs/ wiki/ eval/   로그(JSONL) · 위키 페이지 · 평가셋
tests/              unittest (python -m unittest discover -s tests)
```

## 7. 현재 상태 (2026-09-13)

- 검증 환경: Python 3.14.7, API 키 없음(mock/추출식 경로), 합성 모뎀 코퍼스 38문서·190청크. 단위/통합 테스트 51개 통과.
- 실제 LLM·rerank API·headless 에이전트·Mango MCP 는 fake 서버/mock 으로 배선을 검증했고 실제 엔드포인트는 미검증 — 연결 후 `models test`, `mcp-source test` 로 확인.
- 규모(5,000+50/일) 설계 근거와 남은 개선 항목은 [ANALYSIS_REPORT_0913.md](docs/ANALYSIS_REPORT_0913.md) §1.4 참조.
