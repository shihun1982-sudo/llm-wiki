# 새 환경 설치 가이드 (setup/)

상세한 포팅 절차(설정 파일 채우기 · 프로바이더 연결 · 코퍼스 계약 · 첫 빌드 검증 · 스케줄 · 운영)는 **[docs/BRINGUP_GUIDE.md](../docs/BRINGUP_GUIDE.md)** 를 보세요. 이 문서는 설치와 최소 설정만 다룹니다.

| 파일 | 용도 |
|---|---|
| `install.bat` / `install.sh` | 원클릭 설치: 패키지 설치 → config.json/.env 생성 → 환경 진단 |
| `check_env.py` | 환경 진단 (Python 버전, 패키지, SQLite FTS5, 설정, 키, Ollama, 코퍼스) — 더 자세한 점검은 `python -m llmwiki health` |
| `config.example.json` | 사용자 설정 원본 — 코퍼스 경로, LLM/임베딩 모델, 역할별 모델, 토글, 운영 수치 |
| `.env.example` | API 키와 `LLMWIKI_*` 환경변수 오버라이드 원본 |
| `requirements-optional.txt` | 선택 패키지 (anthropic, sentence-transformers, kiwipiepy, pyyaml) |
| `sample_corpus_modem/` | 합성 모뎀 코퍼스 38문서 + `questions.json` 평가셋 (`make_sample_corpus_modem.py --scale N` 으로 규모 테스트용 확장). 기본 `config.json` 이 가리키는 폴더 |
| (루트) `corpus/` | **실제 문서를 넣는 폴더** (git 제외). 문서를 넣은 뒤 `config.json` 의 `corpus_dirs` 를 `["corpus"]` 로 바꾸고 `build --full` |
| `schedule_build.ps1` / `.sh` | OS 스케줄러(작업 스케줄러/cron)에 증분 빌드 등록 |
| (루트) `tuning.json` `presets.json` `query_rules.json` `pins.json` `agents.json` `mcp_sources.json` `schemas/` `prompts/` | 첫 실행 시 기본값으로 자동 생성되는 설정/규칙/프롬프트 파일 |

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
run.bat serve                       :: Web UI http://127.0.0.1:8765/
run.bat test                        :: 단위 테스트 (51개)
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
| 포트 사용 중 | `serve --port 8899` |

## 7. 폴더 이식

프로젝트 폴더를 통째로 복사하면 된다. `data/`(색인 DB·캐시)·`wiki/`·`logs/` 는 재생성 가능하므로 제외해도 되고, 함께 복사하면 빌드 없이 바로 검색된다. 설정은 상대 경로로 저장되며 절대 경로 코퍼스만 새 위치로 수정한다. 구버전 색인이면 `build verify` 가 `doc_meta_missing` 을 알려주므로 `build --full` 한다.
