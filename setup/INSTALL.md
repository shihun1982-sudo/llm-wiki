# 새 환경 설치 가이드 (setup/)

상세한 포팅 절차(설정 파일 채우기 · 프로바이더 연결 · 코퍼스 계약 · 첫 빌드 검증 · 스케줄 · 운영)는 **[docs/BRINGUP_GUIDE.md](../docs/BRINGUP_GUIDE.md)** 를 보세요. 이 문서는 설치와 최소 설정만 다룹니다.

| 파일 | 용도 |
|---|---|
| `install.bat` / `install.sh` | 원클릭 설치: 패키지 설치 → config.json/.env/security.json/agents.json/server.json/schedule.json/models.json 생성(없을 때만) → 환경 진단 |
| `check_env.py` | 환경 진단 (Python 버전, 패키지, SQLite FTS5, 설정, 키, Ollama, 코퍼스, **security.json admin/익명/API 키 · agents.json 재시도 · serve/mcp 기본 포트 · LLM 재시도 · 동시성 한도 · 스케줄 작업 · 모델 카탈로그 · 터미널 인코딩**) — 더 자세한 점검은 `python -m llmwiki health` |
| `config.example.json` | 사용자 설정 원본 — 코퍼스 경로, LLM/임베딩 모델, 역할별 모델, 토글, 운영 수치 |
| `.env.example` | API 키와 `LLMWIKI_*` 환경변수 오버라이드 원본 |
| `requirements-optional.txt` | 선택 패키지 (anthropic, sentence-transformers, kiwipiepy, pyyaml) |
| `sample_corpus_modem/` | 합성 모뎀 코퍼스 38문서 + `questions.json` 평가셋 (`make_sample_corpus_modem.py --scale N` 으로 규모 테스트용 확장). 기본 `config.json` 이 가리키는 폴더 |
| (루트) `corpus/` | **실제 문서를 넣는 폴더** (git 제외). 문서를 넣은 뒤 `config.json` 의 `corpus_dirs` 를 `["corpus"]` 로 바꾸고 `build --full` |
| `schedule_build.ps1` / `.sh` | OS 스케줄러(작업 스케줄러/cron)에 증분 빌드 등록 |
| (루트) `tuning.json` `presets.json` `query_rules.json` `pins.json` `agents.json` `mcp_sources.json` `schemas/` `prompts/` | 첫 실행 시 기본값으로 자동 생성되는 설정/규칙/프롬프트 파일 |
| `config.example.pat-gateway.json` / `config.example.headless.json` | 사내 LLM 게이트웨이(URL+PAT) / opencode headless 설정 예시 — BRINGUP_GUIDE §4.1~4.3 |
| (루트) `security.json` | 서버를 여러 사람이 쓸 때의 로그인(로컬 ID/비밀번호 + SSO + API 키)·역할 6단계·권한 표(permissions)·익명 접속·CLI 게이트·파괴적 작업 정책. 저장소에는 admin `kh82.kim/1234qwer` 가 들어 있으니 **공개 전 비밀번호 변경** — [docs/SECURITY.md](../docs/SECURITY.md) |
| (루트) `agents.json` | headless 에이전트(opencode 등) 명령 템플릿 + **재시도**(`timeout_s` 300 · `retries` 3 · `retry_backoff_s` · `retry_on`) — BRINGUP_GUIDE §4.3 |
| `security.example.json` | `security.json` 원본 — users 비어 있음, 키마다 `_how` 설명. `install.*` 가 없을 때 복사. 첫 admin: `python -m llmwiki users add <id> --role admin` |
| `agents.example.json` | `agents.json` 원본(재시도 정책 포함). `install.*` 가 없을 때 복사 |
| `server.example.json` | `server.json` 원본 — **여러 사람이 동시에 쓸 때의 제어**: 동시 실행 슬롯·대기열·시간 제한·속도 제한·세션 수·IP/사용자 차단·점검 모드·모니터 공개 범위. 30명 기준 권장값과 계산 근거는 docs/CONCURRENCY.md |
| `schedule.example.json` | `schedule.json` 원본 — **정해진 시각·주기에 돌릴 작업** 17가지 예시. 30분마다 증분 빌드 하나만 켜져 있고 나머지는 `enabled: false` 이므로, 필요한 것만 `schedule enable <name>` 으로 켠다. 전체 빌드·URL 수집·스크립트·CLI 명령·evolve·memory·precompute·eval·snapshot 등 — docs/SCHEDULER.md |
| `models.example.json` | `models.json` 원본 — **쓸 수 있는 LLM/임베딩 모델 목록**. Web 설정의 역할별 드롭다운과 `models list` 가 이 목록을 보여 준다. 환경의 실제 제공 모델은 `python -m llmwiki models discover` 로 가져온다 |
| `mcp_clients.example.json` | 외부 LLM 클라이언트(Claude Code/Desktop · Cursor · opencode · Codex)에 붙여 넣는 MCP 설정 블록 4종. 환경 값이 채워진 버전: `python -m llmwiki mcp --client-config` — docs/MCP.md |
| `mcp_sources.example.json` | `mcp_sources.json` 원본 — **다른 RAG · MCP 서버 · REST 검색 API** 를 붙이는 소스 4종 예시(peer_wiki http · kb_rest rest · mango stdio · mock). 토글 `external_rag`(검색 채널)·`mcp_federation`(도구 노출) — docs/RAG_FEDERATION.md |
| (루트) `plugins/mcp_tools/` | MCP 플러그인 도구 폴더(`_example_echo.py` 예시; 밑줄을 지우면 활성) — docs/RAG_FEDERATION.md §3 |
| (루트) `tools/verify/` | 전 기능 검증 하네스 25종(`verify_all.py` 한 줄) — docs/VERIFICATION.md |

## 1. 요구사항

| 항목 | 최소 | 권장 |
|---|---|---|
| Python | 3.9 | 3.11+ (검증: 3.14.7) |
| 패키지 | numpy, pypdf | + anthropic, sentence-transformers, kiwipiepy, pyyaml |
| SQLite | FTS5 포함 (대부분의 Python 배포판) | trigram 토크나이저는 SQLite 3.34+ |
| OS | Windows / macOS / Linux | |
| 외부 서비스 | 없음 (완전 오프라인 동작) | Anthropic / OpenAI-compatible / Ollama / rerank API / headless 에이전트 / MCP 소스 |

## 2. 설치

```bat
:: Windows
cd llm-wiki-rag-selfevolving
setup\install.bat            :: 현재 python 사용
setup\install.bat venv       :: .venv 생성
pip install -r setup\requirements-optional.txt   :: 선택
```
```bash
# macOS / Linux
bash setup/install.sh            # 또는  bash setup/install.sh venv
```

## 3. 최소 설정

1. `config.json` 의 `corpus_dirs` 를 자기 문서 폴더로 (기본값은 `setup/sample_corpus_modem`). 여러 폴더 가능, 상대 경로는 프로젝트 루트 기준.
2. `.env` 에 키를 채운다 (없으면 추출식 답변·hash 임베딩·로컬 리랭크로 동작).
3. `python -m llmwiki health` → FAIL 항목이 없으면 `python -m llmwiki build --full --trace`.

역할별 모델(`llm_roles`), OpenAI-compatible 엔드포인트(`openai_base_url`), rerank 엔드포인트(`rerank_url`), headless 에이전트(`agents.json`), 외부 MCP(`mcp_sources.json`) 는 BRINGUP_GUIDE §3~4 참조.
우선순위: **환경변수(LLMWIKI_*) > config.json > 코드 기본값** (`config show --effective`).

## 4. 실행

```bat
run.bat build --full --trace        :: 색인
run.bat query "질문" --trace         :: 질의 (--preset quality|speed|token|deep_research)
run.bat eval                        :: 회귀 평가 (eval/questions.json)
run.bat serve                       :: Web UI http://127.0.0.1:8765/  (+ MCP: POST /mcp — docs/MCP.md)
run.bat build fts                   :: 채널만 다시 만들기 (fts | vector | graph) — BRINGUP_GUIDE §6.1
run.bat forensic expect last --doc ISSUE-2003 --term 1.5dB   :: 기대 결과 포렌식 — docs/FORENSIC.md
run.bat query "질문" --analyze --focus speed                :: 상세 분석 리포트 logs\analysis\req_<id>.md — docs/ANALYSIS_MODE.md
run.bat test                        :: 단위 테스트 (87개)
```

## 5. 자기 코퍼스에 맞추기

1. 문서에 front matter 를 붙이거나(`docs/CORPUS_CONTRACT.md`) `schemas/infer.json` 의 경로/파일명 규칙을 맞춘다 → `corpus lint`.
2. 문서 ID 체계에 맞게 `data/rules.json → id_patterns / link_rules`, 도메인 용어를 `query_rules.json` 에.
3. `eval/questions.json` 을 자기 질문으로 교체 → `trial run --name baseline`.
4. LLM 을 붙였다면 `build --full --llm-graph --community-summary`, 질의 프리셋 `quality`.

## 6. 문제 해결 (요약 — 전체 표는 BRINGUP_GUIDE §10)

| 증상 | 조치 |
|---|---|
| 한글이 `???` 로 출력 | `set PYTHONIOENCODING=utf-8` (run.bat 은 자동) |
| `build aborted: health check failed` | `health` 출력의 ✘ 항목 조치 (코퍼스 경로 등). 초기화 전에 검사하므로 기존 색인은 안전 |
| LLM 답변 대신 `(추출식 답변)` | `models test` — 키/모델/엔드포인트, Ollama 는 `ollama pull` |
| `no embeddings for provider` / dim mismatch | 임베더·차원 변경 후 `build --full` |
| 근거 부족(insufficient) 답변 | `forensic last` → 문서 추가 / `rules add` / `fallback_loop` 토글 |
| 포트 사용 중 | `serve --port 8899` 또는 `config.json web_port` (mcp 단독 서버는 `mcp_port`) |
| 옮긴 뒤 전부 정상인지 | `python tools/verify/verify_cli.py` · `verify_web.py` · `verify_ui_wiring.py` · `verify_browser.py`, 또는 `verify_all.py` 한 줄 — docs/VERIFICATION.md §4 |

## 7. 폴더 이식

프로젝트 폴더를 통째로 복사하면 된다. `data/`(색인 DB·캐시)·`wiki/`·`logs/` 는 재생성 가능하므로 제외해도 되고, 함께 복사하면 빌드 없이 바로 검색된다. 설정은 상대 경로로 저장되며 절대 경로 코퍼스만 새 위치로 수정한다. 구버전 색인이면 `build verify` 가 `doc_meta_missing` 을 알려주므로 `build --full` 한다.
