# Bring-up Guide — 새 환경으로 포팅하기

> 대상: 이 시스템(LLM Wiki v3)을 다른 PC/서버/조직 환경에 옮겨 세우는 엔지니어. 설치 → 설정 파일 채우기 → 프로바이더 연결 → 코퍼스 계약 적용 → 첫 빌드 → 검증 → 스케줄 등록 → 운영까지 순서대로 따라 하면 된다.
> CLI 명령의 단계별 동작과 실제 출력 예는 [CLI_FLOWS.md](CLI_FLOWS.md), 전체 구조는 [ARCHITECTURE_V3.md](ARCHITECTURE_V3.md), 문서 형식은 [CORPUS_CONTRACT.md](CORPUS_CONTRACT.md), 인터랙티브 가이드는 `docs/llmwiki_guide.html` 을 브라우저로 여세요.
> 2026-09-14 추가(다중 사용자 권한 표·MCP 원격/다수 LLM·채널별 빌드·문서 단위 확장·LLM 재시도/실패 보고·기대 결과 포렌식)의 설계와 근거는 [IMPLEMENTATION_PLAN_0914.md](IMPLEMENTATION_PLAN_0914.md), 운영 상세는 [SECURITY.md](SECURITY.md) · [MCP.md](MCP.md) · [FORENSIC.md](FORENSIC.md). 이 가이드의 §0 체크리스트 10~14, **§3.2 설정 위치 총람**, §4.3~4.6, §6.1, §7.1, §9, §10 에 반영되어 있다. 2026-09-15 전 기능 검증 결과와 재실행 방법은 [VERIFICATION_0915.md](VERIFICATION_0915.md). **다른 RAG·검색 API·MCP 서버를 붙이고 외부 LLM 에 한 곳으로 내주는 방법**은 [RAG_FEDERATION.md](RAG_FEDERATION.md)(§4.6 요약), **품질·속도·토큰 디버깅용 상세 분석 모드**는 [ANALYSIS_MODE.md](ANALYSIS_MODE.md)(§9 운영 표).

## 0. 체크리스트 (요약)

| 단계 | 명령/파일 | 확인 |
|---|---|---|
| 1 | Python 3.9+ (권장 3.11+), `pip install -r requirements.txt` (+ optional) | `python setup/check_env.py` |
| 2 | 폴더 복사 (`data/ wiki/ logs/` 제외 가능) | 상대 경로 설정이라 그대로 동작 |
| 3 | `config.json` (`setup/config.example.json` 복사) — `corpus_dirs`, 모델 | `python -m llmwiki config show --effective` |
| 4 | `.env` (`setup/.env.example` 복사) — API 키/PAT. 게이트웨이·opencode 는 §4.1~4.3, 예시 `setup/config.example.pat-gateway.json` · `config.example.headless.json` | `python -m llmwiki models test --live` |
| 5 | 문서 계약: `schemas/`, 기존 문서에 front matter 추가 또는 `schemas/infer.json` 규칙 | `python -m llmwiki corpus lint` |
| 6 | 어휘/규칙: `query_rules.json`, `data/rules.json`(id_patterns, link_rules), `prompts/answer_guide.md` | `rules test "…"` |
| 7 | `python -m llmwiki health` → `build --full --trace` → `build verify` | alerts 0, coverage 100% |
| 8 | 평가셋 `eval/questions.json` 교체 → `eval` → `trial run --name baseline` | hit@k, groundedness 기준선 기록 |
| 9 | 스케줄 등록 `setup/schedule_build.ps1 -Register` (또는 cron) | `build status`, `logs tail --file build` |
| 10 | `security.json` 확인(기본 admin `kh82.kim` 비밀번호 변경) → `users add <id> --role <viewer|class3|class2|class1|builder|admin>` → `security perms`(권한 표) → `serve --host 0.0.0.0` — §4.4 | 게스트로 질의 가능, viewer 로 리빌드가 로그인 안내/403, builder 는 채널 리빌드 문구 모달, `security audit` |
| 11 | 외부 LLM 연결: `apikey add <이름> --role viewer` → 클라이언트에 `{"type":"http","url":"http://host:8765/mcp","headers":{"Authorization":"Bearer lwk_…"}}` (같은 PC 는 stdio) — §4.5, [MCP.md](MCP.md) | **`python -m llmwiki mcp --doctor`**(도구·스키마·플러그인·외부 소스·페더레이션·인증 자가 점검) 이 "정상", 그리고 `curl …/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'` |
| 12 | 채널별 빌드·문서 단위 확장·LLM 재시도·기대 결과 포렌식 확인 — §6.1, §7.1, §4.3, §9 | `build fts|vector|graph`, `query … --trace` 의 `doc_expand` 단계, `forensic expect last --doc …` |
| 2.5 | **이미 빌드해 둔 색인이 있으면** 다시 빌드하지 말고 가져다 쓴다 — `data/llmwiki.sqlite3` + `data/rules.json` + `corpus/` 를 **타임스탬프 보존해서** 복사 — 색인 파일만이면 §2.2, **코퍼스와 색인을 통째로**면 §2.3 (복사 명령 · 받는 쪽에서 바꿀 것 표) | `health`(embedding_dim 일치·corpus_dirs 존재·channels_populated) → `build verify` → `build` 가 `changed=0` |
| 13 | 전 기능 재검증 — `tools/verify/` 스크립트 ([VERIFICATION_0915.md](VERIFICATION_0915.md) §6, 남은 항목은 [HANDOVER_0916.md](HANDOVER_0916.md) §2.4) | `verify_cli.py`, `verify_web.py` 전부 OK, `verify_ui_wiring.py` OK, `verify_browser.py` OK, `verify_mcp.py --quick` OK, **`verify_security_ui.py` OK**(보안·사용자 화면을 인증 꺼짐/admin/admin 아님 세 상태로 눌러 본다 — 증상별 원인은 [SECURITY.md §8.1](SECURITY.md)) |
| 15 | 품질/속도/토큰 디버깅 준비 — `query "대표 질의" --analyze` 로 `logs/analysis/req_<id>.md` 가 생기는지, 렌즈 소견에 조절점이 붙는지 — [ANALYSIS_MODE.md](ANALYSIS_MODE.md), 그 자료를 LLM 에게 통째로 줄 때는 `optimize last --out bundle.md` — §7.0, [OPTIMIZATION_GUIDE.md](OPTIMIZATION_GUIDE.md) | `analyze last --print` 에 §0~§9, `optimize last` 가 A~D 절을 만든다 |
| 14 | (선택) 다른 RAG / 검색 API / MCP 서버 연결 — `mcp_sources.json`(`setup/mcp_sources.example.json`), 토글 `external_rag`·`mcp_federation` — §4.6, [RAG_FEDERATION.md](RAG_FEDERATION.md) | `mcp-source test <src>`, `mcp-source retrieve "…"`, `query … --external-rag --trace` 에 `external_rag` 단계, `/mcp tools/list` 에 `<src>__<tool>` |

## 1. 환경

- **Python 3.9 이상** (개발·검증 환경: 3.14.7). 표준 라이브러리 위주라 필수 패키지는 `numpy`, `pypdf` 뿐이다.
- 선택 패키지 (`setup/requirements-optional.txt`): `anthropic`(Claude SDK; 없으면 raw HTTP), `sentence-transformers`(로컬 임베더/크로스인코더), `kiwipiepy`(한국어 형태소 분석기), `pyyaml`(front matter; 없으면 내장 파서).
- SQLite 는 FTS5 가 포함된 빌드여야 한다 (`health` 가 검사). Windows/macOS/Linux 모두 동작, 콘솔 한글은 `PYTHONIOENCODING=utf-8`(run.bat 이 설정).
- 외부 서비스는 모두 선택: Anthropic / OpenAI-compatible(vLLM·LM Studio·Ollama·OpenRouter·사내 게이트웨이) / Ollama / headless 에이전트 CLI(opencode 등) / rerank API / Voyage / Mango 등 MCP.
- 규모 기준(5,000 문서 + 50/일): SQLite 단일 파일로 충분. 벡터 RAM ≈ 청크 수 × dim × 4B (float16 이면 2B). 예) 6만 청크 × 4096d ≈ 980MB(float16 490MB) — `embed_dim` 은 자유롭게 바꿀 수 있고 `health`/`system` 이 전망을 경고한다.

## 2. 설치와 폴더

```bat
:: Windows
cd llm-wiki-rag-selfevolving
setup\install.bat            :: pip 설치 + config.json/.env 생성 + 샘플 복사 + 환경 진단
pip install -r setup\requirements-optional.txt   :: 선택
```
```bash
# macOS / Linux
bash setup/install.sh && pip install -r setup/requirements-optional.txt
```

프로젝트 폴더 전체를 복사하면 된다. 재생성 가능한 폴더(`data/`, `wiki/`, `logs/`)는 빼도 되고, 같이 복사하면 빌드 없이 바로 검색된다(단, 구버전 색인이면 `build --full` 권장 — `build verify` 가 `doc_meta_missing` 으로 알려준다).

### 2.1 색인·임베딩을 빼고 옮기기 (새 환경에서 다시 빌드)

**임베딩은 별도 폴더가 없다.** 벡터는 색인 DB `data/llmwiki.sqlite3` 의 `embeddings` 테이블(문서 카드 벡터는 `doc_vectors`,
재사용 캐시는 `embedding_cache`)에 들어 있다. 그래서 "임베딩만 지운다" = `data/` 를 두고 가거나 아래 명령으로 그 채널만 비우는 것이다.

| 대상 | 크기 예 | 가져가나 | 새 환경에서 |
|---|---|---|---|
| `data/llmwiki.sqlite3` (+`-wal`, `-shm`) | 수백 MB | ✕ (빼면 새로 빌드) | `build --full` 이 다시 만든다 |
| `data/snapshots/` | **가장 큼** — 리빌드/파괴적 명령 전 자동 백업이 쌓인다 | ✕ | 필요 없음. 지금 정리: `snapshot list` → `snapshot prune --keep 1` |
| `data/mcp_cache/` | 외부 소스에서 받아 온 문서 | 선택 | `mcp-source ingest` 로 다시 받는다 |
| `logs/` (특히 `logs/analysis/`) | 수백 MB 까지 | ✕ | 자동 생성 |
| `wiki/` | 작음 | ✕ | 빌드의 `wiki_pages` 단계가 다시 만든다 |
| **`data/rules.json`** | 작음 | **○ 반드시** | id_patterns·link_rules — 설정 파일인데 `data/` 안에 산다 |
| **`data/profiles.json`** | 작음 | ○ (쓰고 있다면) | 계정별 Web UI 화면 설정 |
| `corpus/`, 루트의 `*.json` 설정, `.env`, `schemas/`, `prompts/`, `eval/` | 작음 | **○** | 그대로 쓴다 |

```bat
:: 옮기기 전, 이 환경에서 자리를 줄이고 싶을 때
python -m llmwiki snapshot list
python -m llmwiki snapshot prune --keep 1          :: 자동 백업 정리 (보통 여기서 가장 많이 줄어든다)

:: 색인만 버리고 새 환경에서 다시 빌드 (rules.json·profiles.json 은 살린다)
robocopy . ..\llmwiki-port /E /XD data logs wiki .git __pycache__
copy data\rules.json ..\llmwiki-port\data\
copy data\profiles.json ..\llmwiki-port\data\      :: 쓰고 있다면

:: 새 환경에서
python -m llmwiki health
python -m llmwiki build --full --trace
python -m llmwiki build verify
```

임베딩 채널만 다시 만들고 싶을 때는 전체 리빌드 대신 채널 리빌드를 쓴다 — `python -m llmwiki build vector --full`
(FTS·그래프는 그대로 두고 벡터만 재생성). 임베딩 모델이나 `embed_dim` 을 바꿨다면 반드시 이 명령이 필요하다.
캐시만 비우려면 `python -m llmwiki embed clear-cache`, 현황은 `embed report`.

### 2.2 **이미 빌드해 둔 색인을 가져다 쓰기** (빌드를 다시 하지 않는다)

다른 PC/서버에서 이미 `build --full` 을 끝낸 색인이 있다면 **파일 복사만으로** 그대로 쓸 수 있다.
352 문서·16,892 청크 색인 기준으로 빌드는 수십 분이지만 복사는 수 분이다.

#### 무엇을 복사하나

색인은 **파일 하나**다 — `data/llmwiki.sqlite3`. FTS·벡터·그래프·문서 메타·캐시가 모두 그 안에 있다
(임베딩은 별도 폴더가 없다 — §2.1).

```bat
:: [보내는 쪽] WAL 을 본체로 합치고 나서 복사하면 파일 하나만 들고 가면 된다
python -m llmwiki maintenance wal_checkpoint
python -m llmwiki snapshot prune --keep 1      :: 스냅샷은 가져갈 필요가 없다 (가장 큰 용량)

:: [받는 쪽] 색인과 '설정처럼 쓰이는' data 파일만
copy  <보내는쪽>\data\llmwiki.sqlite3  data\
copy  <보내는쪽>\data\rules.json       data\
```

| 파일/폴더 | 가져가나 | 이유 |
|---|---|---|
| `data/llmwiki.sqlite3` | **○ 반드시** | 색인 본체 (FTS·`embeddings`·`doc_vectors`·엔티티/관계·`doc_meta`·캐시) |
| `data/llmwiki.sqlite3-wal`, `-shm` | △ | `wal_checkpoint` 를 돌렸다면 필요 없다. 안 돌렸다면 **셋 다** 함께 복사 (하나만 빠지면 최근 커밋이 사라진다) |
| `data/rules.json` | **○ 반드시** | id 패턴·링크 규칙. 설정 파일인데 `data/` 안에 산다 |
| `corpus/` | **○ 반드시** | 아래 "코퍼스가 있어야 한다" 참고 |
| 루트의 `*.json`, `.env`, `schemas/`, `prompts/`, `eval/` | **○** | 설정·문서 계약·프롬프트 |
| `data/snapshots/` | ✕ | 보내는 쪽의 백업. 용량만 크다 |
| `data/.session_secret`, `data/sessions.json` | **✕ 절대** | 로그인 세션이 그대로 넘어간다. 환경마다 새로 만들어야 한다 |
| `data/requests/`, `data/reruns/`, `logs/` | ✕ (선택) | 보내는 쪽의 기록. 없어도 동작한다 |
| `wiki/` | ✕ | 빌드의 `wiki_pages` 단계가 다시 만든다 |

#### 맞아야 하는 것 (안 맞으면 조용히 품질이 떨어진다)

| 맞춰야 할 것 | 왜 | 확인 방법 |
|---|---|---|
| **`embed_provider` · `embed_dim`** | `embeddings` 행마다 provider·dim 이 함께 저장된다. 다르면 벡터 검색이 무의미해진다 | `health` 의 `embedding_dim` — `stored=[('hash', 4096, 16896)] current=('hash', 4096)` 처럼 **양쪽이 같아야** 한다 |
| **`embed_provider` 가 `auto` 인 채로 옮기는 것** ⚠ | `auto` 는 *그 환경에서* 고른다 — `VOYAGE_API_KEY` 가 있으면 voyage, Ollama 에 `bge-m3` 등이 받아져 있으면 ollama, 없으면 hash (`providers.make_embedder`). 즉 **보내는 쪽과 받는 쪽이 다른 임베더를 고를 수 있다**. 기본값이 `auto` 라서 가장 흔히 밟는 함정이다 | 색인을 가져갈 때는 config.json 에 **실제로 쓰인 값을 고정**해 적는다 (`"embed_provider": "hash"`). `health` 의 `embedding_dim` 이 stored/current 불일치로 잡아 준다. 바꿀 생각이면 옮긴 뒤 `build vector --full` |
| **코퍼스가 실제로 있을 것** | 문서 id 는 코퍼스 폴더 기준 **상대 경로**라 절대 경로는 달라도 되지만, 폴더 자체가 없으면 다음 빌드가 "전부 삭제됨" 으로 본다 | `health` 의 `corpus_dirs` 가 **fail** 로 막아 준다. 이때 `--force` 를 붙이지 말 것 |
| 청크 설정 (`chunk_chars`·`chunk_overlap`) | 새로 넣는 문서만 다른 규칙으로 쪼개져 섞인다 | 보내는 쪽 `config.json` 을 그대로 쓰면 자동으로 맞는다 |
| `schemas/`, `data/rules.json` | 문서 계약과 그래프 규칙. 다르면 이후 빌드부터 메타·엔티티가 달라진다 | 같이 복사 |

> **복사할 때 타임스탬프를 보존하라.** 증분 빌드는 파일의 **mtime·크기**로 변경을 판단한다
> (경로가 아니라). 타임스탬프가 바뀌면 다음 빌드가 **전 문서를 다시 읽고 다시 임베딩**한다 —
> 색인을 가져온 의미가 없어진다. `robocopy /E /COPY:DAT` (Windows) 또는 `cp -a` / `rsync -a` (Linux) 를 쓴다.

#### 받는 쪽에서 확인 (3분)

```bat
python -m llmwiki health                 :: embedding_dim 일치 · corpus_dirs 존재 · channels_populated
python -m llmwiki stats                  :: docs/chunks/embeddings/entities 가 보내는 쪽과 같은지
python -m llmwiki build verify           :: 색인 무결성 (FTS·벡터·그래프 개수 정합)
python -m llmwiki query "대표 질의" --trace   :: fts/vector/graph 세 채널이 모두 결과를 내는지
python -m llmwiki build                  :: 증분 — 여기서 changed=0 이면 복사가 깨끗하게 끝난 것
```

마지막 `build` 가 `changed=0 removed=0` 이면 성공이다. `changed` 가 문서 수만큼 나오면
**타임스탬프가 보존되지 않은 것**이므로, 그대로 두면 다시 임베딩한다(결과는 같지만 시간이 든다).

#### 채널이 비어 있는 채로 넘어오는 경우

`health` 의 **`channels_populated`** 가 이걸 잡는다. 토글은 켜져 있는데 색인이 비어 있으면
그 채널은 질의마다 돌지만 **늘 0건**이라, 오류도 로그도 없이 품질만 떨어진다.

```
△ channels_populated  비어 있는 채널: graph(entities=0) → `build graph`
```

이때는 전체 리빌드 없이 **그 채널만** 다시 만든다 (실측: 352문서·16,892청크에서 `build graph` 13초,
엔티티 617 · 관계 13,411 · 커뮤니티 35 생성).

```bat
python -m llmwiki build graph            :: 그래프만 (FTS·벡터는 그대로)
python -m llmwiki build vector --full    :: 임베더/차원을 바꿨을 때
python -m llmwiki build fts              :: 토크나이저·trigram 설정을 바꿨을 때
```

### 2.3 **코퍼스와 색인을 통째로 가져가기** (가장 간단한 방법 · 권장)

§2.1 은 "색인을 버리고 다시 빌드", §2.2 는 "색인 파일만 가져오기" 다.
**코퍼스도 색인도 그대로 옮기고 싶다**면 폴더를 통째로 복사하는 것이 가장 확실하다.
경로 설정이 전부 **상대 경로**(`config.json` 의 `corpus_dirs: ["corpus"]`, `data_dir: "data"`,
`wiki_dir: "wiki"`)라서 복사한 폴더 위치가 달라도 고칠 것이 없다.

#### ① 보내는 쪽에서 먼저 줄인다

복사본 용량의 대부분은 **가져갈 필요 없는** 자동 백업이다 (실측: `data/snapshots/` 1,472 MB 대
색인 본체 480 MB + 코퍼스 13 MB).

```bat
python -m llmwiki snapshot prune --keep 1        :: 자동 백업 정리 — 여기서 가장 많이 줄어든다
python -m llmwiki maintenance wal_checkpoint     :: WAL 을 본체로 합쳐 sqlite3 파일 하나로
```

#### ② 복사 — **타임스탬프를 반드시 보존**

```bat
:: Windows — /COPY:DAT 가 mtime 을 보존한다. 이게 빠지면 다음 빌드가 전 문서를 다시 임베딩한다
robocopy . \\서버\llmwiki /E /COPY:DAT /XD .git __pycache__ logs data\snapshots data\requests data\reruns
```

```bash
# macOS / Linux — -a 가 타임스탬프를 보존한다
rsync -a --exclude .git --exclude __pycache__ --exclude logs \
      --exclude data/snapshots --exclude data/requests --exclude data/reruns \
      ./ user@서버:/opt/llmwiki/
```

| 폴더 | 실측 | 가져가나 |
|---|---|---|
| `corpus/` | 503 파일 · 13 MB | **○ 반드시** — 없으면 다음 빌드가 "전 문서 삭제" 로 본다 |
| `data/llmwiki.sqlite3` | 480 MB | **○ 반드시** — FTS·벡터·그래프·`doc_meta`·캐시가 전부 이 파일 하나에 있다 |
| `data/rules.json`, `data/profiles.json` | 작음 | **○** — 설정 파일인데 `data/` 안에 산다 |
| 루트 `*.json`, `schemas/`, `prompts/`, `eval/`, `setup/`, `llmwiki/`, `tools/` | 작음 | **○** |
| `data/snapshots/` | 1,472 MB | ✕ — 보내는 쪽 백업 |
| `data/requests/`(20 MB) · `data/reruns/`(6 MB) · `logs/` | | ✕ — 보내는 쪽 실행 기록 |
| `wiki/` | 작음 | 선택 — 빌드의 `wiki_pages` 단계가 다시 만든다 |

#### ③ 받는 쪽에서 **반드시 바꾸는 것** (경로는 안 바꿔도 된다)

| 파일 | 무엇을 | 왜 |
|---|---|---|
| `data/.session_secret`, `data/sessions.json` | **지운다** | 로그인 세션이 그대로 넘어간다. 서버가 새로 만든다 |
| `.env` | 그 환경의 API 키·PAT 로 교체 | 키는 환경마다 다르다. 게이트웨이는 §4.1 |
| `config.json` 의 `embed_provider` | `"auto"` → **실제로 쓰인 값**(`"hash"`) 로 고정 | `auto` 는 환경마다 다른 임베더를 고른다 — §2.2 의 ⚠ 행 |
| `security.json` | 계정·SSO·역할을 그 환경 기준으로 | 사람이 다르다. `setup/security.example.json` 참고 |
| `server.json` | `host`/`port`, 동시 실행 슬롯 | 여러 명이 쓰면 [CONCURRENCY.md](CONCURRENCY.md) §3 |
| `mcp_sources.json` | 외부 RAG 주소 | 사내 주소가 다르면 |

#### ④ 확인 (3분) — §2.2 "받는 쪽에서 확인" 과 같다

```bat
python -m llmwiki health                 :: embedding_dim 일치 · corpus_dirs 존재 · channels_populated
python -m llmwiki stats                  :: docs/chunks/embeddings 가 보내는 쪽과 같은지
python -m llmwiki build verify           :: 색인 무결성
python -m llmwiki build                  :: changed=0 removed=0 이면 복사가 깨끗하게 끝난 것
```

`changed` 가 문서 수만큼(예: 352) 나오면 **타임스탬프가 보존되지 않은 것**이다.
결과는 같지만 전 문서를 다시 읽고 다시 임베딩하므로 색인을 가져온 의미가 없어진다 — ② 로 돌아간다.

## 3. 설정 파일 (모두 프로젝트 루트, 위치는 `LLMWIKI_<NAME>_PATH` 로 변경 가능 — `config paths`)

| 파일 | 역할 | 언제 바꾸나 | 반영 |
|---|---|---|---|
| `config.json` | 코퍼스 경로, 프로바이더/역할별 모델, 토글, 운영 수치(배치·WAL·로그·timezone), **LLM 재시도**(`llm_timeout`·`llm_retries`·`llm_retry_backoff_s`), **서버/MCP 기본값**(`web_host`·`web_port`·`mcp_transport`·`mcp_host`·`mcp_port`·`mcp_url`) — 원본 `setup/config.example.json` | 새 환경 필수 | 즉시(서버 reload); 서버/MCP 키는 다음 기동 |
| `.env` | API 키/PAT(`OPENAI_API_KEY`·`LLM_API_KEY`, `ANTHROPIC_API_KEY`·`ANTHROPIC_AUTH_TOKEN`, `VOYAGE_API_KEY`, `RERANK_API_KEY`, MCP 토큰) + **모든 설정의 env 오버라이드** `LLMWIKI_<KEY>` / `LLMWIKI_TOGGLE_<NAME>` / `LLMWIKI_<ROLE>_MODEL` | 키 발급 후 | 프로세스 시작 |
| `security.json` | 로그인 방식(로컬 ID/비밀번호 · SSO · API 키), **역할 6단계 · 권한 표(permissions) · 익명 접속(anonymous_role) · CLI 게이트(cli)**, 파괴적 작업 정책 — [SECURITY.md](SECURITY.md). 원본 `setup/security.example.json`(users 비어 있음 + 각 키 설명 `_how`) | 서버 공개 전 | 즉시(서버 reload) |
| `tuning.json` | 알고리즘 상수(FTS·라우터·그래프·융합·근거 판정·claim·메모리…) 오버라이드만. 문서 단위 확장 `doc_expand_*`, 기대 결과 포렌식 `forensic_near_miss_mult`·`forensic_term_candidates`·`forensic_term_targets`·`forensic_pin_confidence` 포함 — [TUNING.md](TUNING.md) | 품질 튜닝 | 즉시 |
| `presets.json` | quality / speed / token / offline / deep_research 묶음 | 조직 정책 | `--preset`, `preset apply` |
| `query_rules.json` | acronym / synonym / alias / related / exclude / compound 사전 | 도메인 용어 | 즉시 |
| `data/rules.json` | 그래프 엔티티 사전, 관계 정규식, **ID 패턴(id_patterns)**, **결정적 링크 규칙(link_rules)**, front matter 관계 매핑 | 문서 ID 체계 | 재빌드 |
| `schemas/*.json` | 문서 유형별 스키마 + 추론 규칙 + 마이그레이션 | 새 문서 유형 | lint/빌드 |
| `prompts/*.md` | 역할별 LLM 프롬프트/답변 가이드 | 답변 스타일 | 즉시(mtime) |
| `pins.json` | 고정 근거 | 운영 중 | 즉시 |
| `agents.json` | headless 에이전트 명령 템플릿 (opencode/claude/codex/mock) + **재시도 정책**(`timeout_s` 300=5분 · `retries` 3 · `retry_backoff_s` · `retry_on`) — §4.3. 원본 `setup/agents.example.json` | 에이전트 도입 | 즉시 |
| `mcp_sources.json` | **외부 소스 = 다른 RAG · MCP 서버 · REST 검색 API** (전송 stdio/http/rest): `retrieve`(검색 채널, 토글 `external_rag`) · `expose`(도구 페더레이션, 토글 `mcp_federation`) · `ingest`(문서로 색인, 토글 `mcp_sources`) — [RAG_FEDERATION.md](RAG_FEDERATION.md). 원본 `setup/mcp_sources.example.json` | 다른 RAG 연결 | 즉시 |
| `plugins/mcp_tools/*.py` | MCP 플러그인 도구(`register(add_tool)`) — 코드 수정 없이 도구 추가. 위치 `config.json mcp_plugins_dir` | 도구 추가 | 다음 tools/list |
| (클라이언트 쪽) `setup/mcp_clients.example.json` | 외부 LLM 클라이언트(Claude Code/Desktop, Cursor, opencode, Codex)에 붙여 넣는 MCP 설정 블록 4종(stdio·HTTP·브리지·opencode). 자기 환경 값이 채워진 버전은 `python -m llmwiki mcp --client-config` — §4.5 | 외부 LLM 연결 | 클라이언트 재시작 |
| `eval/questions.json` | 회귀 평가셋 | 자기 코퍼스 질문으로 교체 | – |

우선순위: **환경변수 > config.json > 코드 기본값**. 현재 유효값과 출처는 `config show --effective`.

### 3.1 config.json 에서 반드시 볼 것
```json
{
  "corpus_dirs": ["D:/wiki/issues", "D:/wiki/cls", "D:/wiki/design"],
  "llm_provider": "auto",  "llm_model": "claude-opus-5",
  "llm_roles": {"rerank": {"model": "claude-haiku-4-5-20251001"}, "expand": {"model": "claude-haiku-4-5-20251001"}, "verify": {"model": "claude-haiku-4-5-20251001"}},
  "embed_provider": "auto", "embed_model": "", "embed_dim": 4096, "embed_store_dtype": "float32",
  "timezone": "Asia/Seoul"
}
```
- 역할: `answer`(답변) `rerank` `extract`(그래프) `summary` `review`(자가진화) `expand`(질의 확장/분해/LLM 라우터) `verify`(근거·claim 판정) `forensic`. 비우면 전역값 상속. 저비용 모델을 expand/verify/rerank 에 두는 것을 권장.
- `llm_provider`: `auto`(Anthropic 키 → Ollama(모델이 받아져 있을 때) → none) | `anthropic` | `openai` | `ollama` | `headless:<agent>` | `mock` | `none`.
- `embed_provider`: `auto`(Voyage 키 → Ollama bge-m3/nomic → hash) | `hash` | `voyage` | `openai` | `ollama` | `st`. 임베더/차원/dtype 을 바꾸면 `build --full`. 내용 해시 캐시(`embedding_cache`) 덕분에 같은 모델·차원으로 되돌리면 재임베딩이 없다.

### 3.2 2026-09-14/15 기능의 설정 위치 총람 (코드 수정 없이 파일만으로 이식)

각 기능의 값은 아래 파일·키에 있고, 원본 예시는 `setup/` 에 있다. 새 환경에서는 예시를 복사해 값을 채우고(§0 체크리스트 3·4·10·11), `python setup/check_env.py` 가 security/agents/서버/재시도 설정을 한 줄씩 보고한다. 우선순위는 항상 **CLI 플래그 > 환경변수(`LLMWIKI_<KEY>`) > 파일 > 코드 기본값**.

| 기능 | 파일 | 키 (기본값) | 원본 예시 | 확인 명령 |
|---|---|---|---|---|
| 다중 사용자 권한 | `security.json` | `mode`(auto) · `anonymous_role`(viewer) · `permissions.levels`(read=viewer, run=class3, edit=class2, index=class1, rebuild=builder, admin/destructive=admin) · `permissions.ops`({}) · `cli.default_role`(admin) · `cli.require_login`(false) · `users` · `api_keys` · `sso` · `destructive` | `setup/security.example.json` | `security show`, `security perms`, `users list`, `apikey list` |
| CLI 실행자 로그인 | `.env` | `LLMWIKI_USER`/`LLMWIKI_PASSWORD` 또는 `LLMWIKI_API_KEY` (전역 `--user` 가 우선) | `setup/.env.example` §D | `--user <id> stats` |
| MCP 서버(같은 PC/원격/다수) | `config.json` | `web_host`(127.0.0.1) · `web_port`(8765) — `serve` 기본값; `mcp_transport`(stdio) · `mcp_host`(127.0.0.1) · `mcp_port`(8766) — `mcp` 기본값 | `setup/config.example.json` | `serve`, `mcp --transport http`, `curl …/mcp` |
| MCP 브리지(stdio 클라이언트 → 원격) | `config.json` + `.env` | `mcp_url`("") 또는 `LLMWIKI_MCP_URL`; 토큰 `LLMWIKI_MCP_TOKEN` (플래그 `--connect/--token` 우선) | `.env.example` §D | `mcp --client-config` 의 `bridge_json` |
| MCP 클라이언트 설정 | 클라이언트 파일 | `mcpServers.llmwiki.{command,args,cwd}` (stdio) / `{type:http,url,headers.Authorization}` (HTTP) | `setup/mcp_clients.example.json`, `mcp --client-config [--url] [--token]` | 클라이언트에서 `tools/list` |
| 채널별 빌드 | `config.json toggles` | `build_fts`(on) · `embed`(on) · `rule_graph`(on) · `llm_graph`(off) · `communities`(on) · `wiki_pages`(on) | `config.example.json` | `build fts|vector|graph`, `build verify` |
| 문서 단위 확장 | `config.json toggles` + `tuning.json` + `presets.json` | `doc_expand`(on); `doc_expand_top_docs`(3) · `doc_expand_max_chunks`(3) · `doc_expand_min_score`(0.2) · **`doc_expand_mode`(keyword·vector·hybrid·`full`)** · `doc_expand_w`(0.5); 프리셋 speed/token 은 off. **`full` = 근거가 나온 문서를 통째로 읽힌다** — 점수로 거르지 않고 문서 순서대로, `doc_expand_max_chunks` 와 `context_max_chars` 로만 제한(품질↑·토큰↑, max_chunks 를 함께 키운다) | `tuning show --stage context`, `preset show quality` | `query … --trace` 의 `doc_expand`, `--no-doc-expand` |
| LLM 재시도(HTTP 프로바이더) | `config.json` | `llm_timeout`(600) · `llm_retries`(3) · `llm_retry_backoff_s`(2.0) · 토글 `llm_failure_report`(on) | `config.example.json` | `models test --live`, 답변 상단 `⚠ LLM 실행 보고` |
| headless 재시도·**무응답 대책** | `agents.json` (에이전트별) | `timeout_s`(300, 1회 전체 제한) · `retries`(3) · `retry_backoff_s`(5) · `retry_on`([timeout, exec, exit, empty, **stall**]) · **`stall_timeout_s`(60 — 마지막 출력 뒤 이만큼 조용하면 죽이고 재시도)** · **`first_output_timeout_s`(120)** · **`keep_partial_on_timeout`(true — 멎기 전 받은 답을 쓴다)** · **`failure_log_chars`(2000)** · `command`/`cwd`/`env` | `setup/agents.example.json`, `setup/config.example.headless.json` | `models test --live`, mock: `python -m llmwiki.headless --mock --stall 30` |
| 기대 결과 포렌식 | `tuning.json` | `forensic_near_miss_mult`(3) · `forensic_term_candidates`(6) · `forensic_term_targets`(20) · `forensic_pin_confidence`(0.6) · **`forensic_suggestion_min_confidence`(0.5)** · **`forensic_targets_shown`(3)** · (기존) `forensic_min_events`(3) · 토글 `forensic_auto`(on) | `tuning show --stage forensic` | `forensic expect last --doc …` |
| **요청 이력·결과 보관** | `config.json` | `keep_requests`(2000, DB 행) · **`requests_dir`(`data/requests`)** · **`requests_keep_days`(90)** | `config.example.json` | `maintenance prune_requests`, Ask › 🕘 내 지난 요청 — [REQUEST_HISTORY.md](REQUEST_HISTORY.md) |
| **규칙 확장 라운드** | `tuning.json` | **`query_rules_max_rounds`(2)** — 약어→정식명→동의어 사슬을 몇 번 접어 적용할지 | `tuning show --stage query_rules` | `rules test "…"`, `rules lint` |
| **협업(채팅·게시판)** | `config.json toggles` + `server.json` | 토글 **`collab`**(on); `collab.enabled`(true) · `retain_min`(120) · `max_messages`(500) · `board_keep_days`(365) · `bubble_font_start_px`(12) · `bubble_font_step_px`(1) · `bubble_font_step_min`(30) · `bubble_font_max_px`(28) · `idle_hide_min`(240) | `setup/server.example.json` | `curl …/api/collab` — [COLLAB.md](COLLAB.md) |
| 다른 RAG 연동(검색 채널) | `mcp_sources.json` + `config.json toggles` + `tuning.json` | 소스 `transport`(stdio/http/rest) · `retrieve`(tool/args/result_path/*_field/weight/when); 토글 `external_rag`(off); `channel_w_external`(1.0) · `external_rag_k`(5) · `external_rag_inject`(2) | `setup/mcp_sources.example.json` | `mcp-source test|retrieve`, `query … --external-rag --trace` |
| 도구 페더레이션(외부 LLM 에 한 곳으로) | `mcp_sources.json` + `config.json toggles` | 소스 `expose`(true/목록); 토글 `mcp_federation`(off) | 같은 예시 | `mcp-source federated`, `/mcp tools/list` 의 `<source>__<tool>` |
| MCP 플러그인 도구 | `config.json` + `plugins/mcp_tools/*.py` | `mcp_plugins_dir`(plugins/mcp_tools) | `plugins/mcp_tools/_example_echo.py` | `mcp-source federated` 의 plugins |
| 상세 분석 모드 | `config.json toggles` | `analysis_mode`(off); 관련 `debug_level`(1) · `keep_requests`(2000) · 리포트 위치 `LLMWIKI_LOGS_DIR_PATH/analysis` | — | `query "…" --analyze`, `analyze last --print` — [ANALYSIS_MODE.md](ANALYSIS_MODE.md) |
| 검증 하네스 | `tools/verify/*.py` 상단 `PORT` | 8792(web) · 8793(browser) · 8794(monkey); 브라우저 실행 파일 `LLMWIKI_BROWSER` | [tools/verify/README.md](../tools/verify/README.md) | [VERIFICATION_0915.md](VERIFICATION_0915.md) §6 |
| **동시 사용자 제어 (30명)** | `server.json` | `concurrency.max_parallel_reads`(8) · `max_parallel_per_user`(3) · `max_parallel_per_ip`(6) · `max_parallel_batch`(1 — 평가·trial 등 배치 작업 동시 실행 수) · `max_body_mb`(8) · `keep_alive_s`(30 — 0 이면 HTTP/1.0, 화면 갱신이 밀린다) · `queue_max`(64) · `queue_timeout_s`(120) · `reads_during_build`(incremental) · `write_wait_timeout_s`(600) · `read_wait_timeout_s`(900); `timeouts.query_s`(900) · `search_s`(120) · `mcp_s`(900) · `job_s`(0=무제한) · `cli_s`(600); `rate_limit.enabled`(true) · `per_user_per_min`(60) · `per_ip_per_min`(120) · `query_per_user_per_min`(20) · `exempt_roles`([admin]); `sessions.enforce`(false) · `max_per_user`(5) · `idle_timeout_min`(720); `access.block_ips`([]) · `block_users`([]) · `allow_ips`([]) · `maintenance_mode`(false) · `maintenance_message` · `maintenance_allow_roles`([admin]); `monitor.history_size`(500) · `viewer_can_see_activity`(true) · `show_user_to_viewer`(true) · `slow_request_ms`(30000) · `stats_window_min`(15) · `live_dir`(data/live) · `live_stale_s`(90) | `setup/server.example.json` | `server stats`, `server activity`, `server limits`, Web 관리 › 서버 모니터 — [CONCURRENCY.md](CONCURRENCY.md) |
| **역할별 LLM 정책** | `config.json llm_roles.<role>` | 역할마다 `timeout_s` · `retries` · `backoff`(exponential) · `backoff_s`(2.0) · `backoff_max_s`(60.0) · `budget_s`(0=무제한) · `circuit_failures`(3) · `circuit_cooldown_s`(60) · `max_tokens`. **기본 배포값**(config.example.json): 보조 단계는 빨리 포기 — expand 30s/1회·rerank 60s/1회·verify 90s/1회, 사용자가 기다리는 answer 240s/2회, 빌드·배치는 길게 — extract 120s/2회·summary 90s/2회·review 300s/2회·forensic 120s/1회. `budget_s` 는 재시도까지 합친 상한. 지정하지 않으면 전역값을 쓴다: `llm_timeout`(600) · `llm_retries`(3) · `llm_retry_backoff`(exponential) · `llm_retry_backoff_s`(2.0) · `llm_retry_backoff_max_s`(60.0) · `llm_budget_s`(0) · `llm_http_retries`(2) · `llm_circuit_failures`(3) · `llm_circuit_cooldown_s`(60) | `setup/config.example.json` | `models policy`, `server circuits`, 답변 상단 `⚠ LLM 실행 보고` — [CONCURRENCY.md](CONCURRENCY.md) §7 |
| **스케줄러** | `schedule.json` | `tick_s`(5) · `timezone`(config 따름) · `tasks[]`(`name` · `enabled` · `when`{`every`/`at`+`days`/`cron`} · `action`{`type` 19종 · 유형별 인자} · `on_error`(log) · `max_runtime_s` · `catch_up`(false)). 저장하면 서버가 자동으로 다시 읽는다 | `setup/schedule.example.json` | `schedule list`, `schedule validate`, `schedule run <name>`, `schedule history`, Web 설정 › 스케줄 — [SCHEDULER.md](SCHEDULER.md) |
| **쓸 수 있는 LLM 목록** | `models.json` | `models[]`(`id` · `provider` · `label` · `roles`[] · `tags`[] · `context_k` · `enabled` · `notes`). Web 설정의 역할별 드롭다운과 `models list` 의 목록이 된다. 카탈로그에 없는 모델을 설정해도 동작은 하며 "카탈로그에 없음"으로 표시된다 | `setup/models.example.json` | `models list`, `models discover`, `models catalog add/remove` |
| **터미널 한글 인코딩** | `config.json` | `console_encoding`(auto \| utf-8 \| native \| off) · `console_set_codepage`(true — Windows 콘솔을 65001 로 바꾸고 종료 시 복구). 자식 프로세스에는 `PYTHONIOENCODING` 이 전달된다 | `setup/config.example.json` | `health` 의 콘솔 줄, `python setup/check_env.py` |
| **SQLite 동시성** | `config.json` | `db_busy_timeout_s`(60 — 잠금 대기) · `db_pool_size`(0=스레드마다 하나) · 기존 `wal`(true) · `synchronous`(NORMAL) | `setup/config.example.json` | `server stats` 의 `db`, `health` |
| **계정별 Web UI 프로파일** | `data/profiles.json` (자동 생성) | 사용자당 `theme` · `toggles` · `presets` · `overrides` · `pins` · `pinview`(열 수·높이·넓게·접힘) · `mode` · `tab` · `group` (사용자당 32KB · 최대 1000명). 서버 공용 설정과 분리되고 로그인 시 자동 적용 | — | Web 헤더의 `💾 내 설정 저장`, 불러오기 `GET /api/profile` · 저장 `POST {action:"save",profile:{…}}` — [WEB_UI.md](WEB_UI.md) §4 |
| **화면 테마 기본값** | `llmwiki/web/static/themes/themes.json` | `default`(light) · `themes[]`(light·dark·high-contrast·solarized) · `auto`(시스템 설정 매핑). 사용자가 고른 값이 우선 | — | Web 헤더의 테마 선택 — [WEB_UI.md](WEB_UI.md) §7 |
| **답변 반복 루프 차단** | `tuning.json` + `config.json` | `answer_repeat_guard`(true) · `answer_repeat_min_chars`(12) · `answer_repeat_times`(4); 억제 강도 `llm_frequency_penalty`(0.3) · `llm_presence_penalty`(0) · `llm_repeat_penalty`(1.1) | `setup/config.example.json` | `precompute check`, `precompute clear --broken` — [WEB_UI.md](WEB_UI.md) §6 |
| **역할별 출력 토큰 상한** | `config.json llm_roles.<role>.max_tokens` | 단계마다 뱉는 길이가 다르다. 지정하지 않으면 단계 기본값을 쓴다: router 200 · expand 400 · rerank 400 · verify 500(근거)/1500(claim) · summary 800 · forensic 1200 · review 3000 · extract 4000 · answer `answer_max_tokens`. 품질을 올리려면 answer·verify 를, 비용을 줄이려면 extract·rerank 를 조절한다 | `setup/config.example.json` | `models policy` 의 `max` 열 — [WEB_UI.md](WEB_UI.md), [ANALYSIS_MODE.md](ANALYSIS_MODE.md) |
| **반복 억제 (LLM 고장 예방)** | `config.json` | `llm_frequency_penalty`(0.3) · `llm_presence_penalty`(0) — OpenAI 호환; `llm_repeat_penalty`(1.1) — Ollama 네이티브. 역할별로도 `llm_roles.<role>.frequency_penalty` 등으로 지정 | `setup/config.example.json` | 답변에 같은 구절이 반복되면 올린다 |
| **파일·폴더 위치** | `config.json` + `LLMWIKI_<NAME>_PATH` | `data_dir`(data) · `wiki_dir`(wiki) · `db_name`(llmwiki.sqlite3) · `corpus_dirs`. 설정 파일 18종은 `LLMWIKI_CONFIG`·`LLMWIKI_TUNING_PATH` 처럼 경로를 통째로 옮길 수 있다 | `setup/config.example.json` | `config paths` 가 지금 쓰는 18개 경로를 모두 출력 |
| **로그** | `config.json` | `log_level`(INFO — DEBUG 로 올리면 단계별 상세) · `log_max_mb`(10) · `log_backups`(10) · `log_console`(false) | `setup/config.example.json` | `logs files`, Web 관리 › 서버 모니터의 로그 레벨 |
| **빌드 운영 수치** | `config.json` | `auto_build_interval`(30초) · `build_lock_timeout`(600) · `embed_batch`/`embed_batch_max`/`embed_batch_target_ms`(적응형 배치) · `embed_commit_every` · `wal_checkpoint_mb` · `query_cache_size` | `setup/config.example.json` | `build status`, `system` |
| **자가진화 임계** | `config.json` | `evolve_min_confidence`(0.7 — 이 이상만 자동 적용 후보) · `evolve_low_score_threshold` | `setup/config.example.json` | `evolve status` |
| **설정 파일 원자적 저장** | (코드) `llmwiki/atomicio.py` | 환경변수 `LLMWIKI_ATOMIC_RETRIES`(10) · `LLMWIKI_ATOMIC_RETRY_MS`(20) — 동시 저장·백신 잠금으로 교체가 막힐 때의 재시도 | — | `python -m unittest tests.test_concurrency_0915.AtomicWriteTest` |

`install.bat`/`install.sh` 는 `config.json`·`.env` 에 더해 `security.json`·`agents.json`·`server.json`·`schedule.json`·`models.json` 도 예시에서 생성한다(없을 때만).

## 4. 프로바이더 연결

| 방식 | 설정 | 확인 |
|---|---|---|
| **사내 게이트웨이 (URL + PAT), OpenAI-compatible** | `llm_provider=openai`, `openai_base_url=https://gateway.corp/v1`, `.env OPENAI_API_KEY=<PAT>`(또는 `LLM_API_KEY`), 헤더 형식 `openai_api_key_header` — §4.1 | `models test --live` |
| **사내 게이트웨이 (URL + PAT), Anthropic-compatible** | `llm_provider=anthropic`, `anthropic_base_url=https://gateway.corp`, `.env ANTHROPIC_AUTH_TOKEN=<PAT>`(Bearer) 또는 `ANTHROPIC_API_KEY`(x-api-key), `llm_fallbacks=false` — §4.2 | `models test --live` |
| Anthropic 직접 | `.env ANTHROPIC_API_KEY`, `llm_provider=auto|anthropic` | `models test` |
| OpenAI-compatible 로컬 (vLLM · LM Studio · Ollama /v1) | `openai_base_url` (…/v1), 키는 비워도 됨, `llm_provider=openai`, 모델 id | `models test` (`/v1/models`) |
| Ollama | `ollama_url`, `ollama_model`, `ollama pull <model>` | `models test` — 모델이 없으면 unavailable 로 표시되고 auto 는 선택하지 않음 |
| **Headless 에이전트 (opencode 등)** | `agents.json` 의 command 템플릿, `llm_roles.<role>.provider = "headless:opencode"`, 실행 파일 PATH — §4.3 | `models test --live` (실제로 프로세스를 띄워 응답 확인), `headless:mock` 으로 배선 확인 |
| rerank API | `rerank_url`, `rerank_api_model`, `rerank_api_style`, `.env RERANK_API_KEY`; 튜닝 `rerank_method=auto|api` | `models test` (`rerank_api` 항목) |
| 임베딩 API | Voyage(`VOYAGE_API_KEY`), OpenAI-compat(`openai_embed_model`, 필요 시 `openai_embed_base_url`·`OPENAI_EMBED_API_KEY`), Ollama(`embed_model=bge-m3`) | `models test` (`embedder`) |
| 외부 MCP (ingest) | `mcp_sources.json` (command/env/tool 매핑), 토글 `mcp_sources` | `mcp-source test`, `mcp-source ingest --dry-run` |
| 다른 RAG / 검색 API (검색 채널·페더레이션) | `mcp_sources.json` (`transport` stdio/http/rest, `retrieve`, `expose`), 토글 `external_rag`·`mcp_federation` — §4.6 | `mcp-source retrieve "…"`, `mcp-source federated` |

전부 없어도 동작한다: 추출식 답변 + 규칙 그래프 + hash 임베딩 + 로컬 리랭크 (`preset apply offline`).

공통 주의:
- `llm_provider=auto` 는 Anthropic 키 → Ollama 순으로만 고르며 **openai / headless 는 절대 고르지 않는다**. 게이트웨이나 opencode 를 쓰려면 `llm_provider` 또는 `llm_roles.<role>.provider` 에 명시한다.
- `models test` 는 토큰을 쓰지 않는 ping(모델 목록 조회)만 한다. 게이트웨이가 `/models` 를 막아 두었거나 PAT 권한·헤더 이름·모델 id 가 틀린 경우는 **`models test --live`** (역할별 provider/model 당 실제 완성 호출 1회, "OK" 한 단어 응답)로만 드러난다. Web 은 Settings › 모델 › "실제 호출 테스트 (--live)".
- LLM 호출 1회의 HTTP 타임아웃은 `llm_timeout`(기본 600초). 게이트웨이가 멈춰도 빨리 실패하게 하려면 120~180 으로 줄인다. 진행 중인 호출은 CLI 의 `⏳ … LLM 응답 대기 <provider>/<model> Ns` 줄과 Web 의 진행 패널에서 보인다.
- **재시도**: timeout·네트워크·headless 실행 실패(transient)는 `llm_retries`(기본 3) 회 재시도한다(`llm_retry_backoff_s` × 시도 번호 대기; HTTP 401/404 같은 설정 오류는 재시도하지 않음). headless 는 `agents.json` 의 `timeout_s`/`retries` 가 우선. 진행 패널에 `LLM 재시도 2/4 (…)` 로 보인다.
- **최종 실패 보고**: 재시도 후에도 실패하면 답변 상단에 `⚠ LLM 실행 보고: answer(…) 4회 시도 후 실패(timeout 300s) → 추출식 답변으로 대체` 가 붙고, 결과 JSON 의 `llm_report`(역할·시도 횟수·오류·대체 경로), 빌드는 `alerts[llm_failures]` 에 남는다. 즉 LLM 이 죽어도 지금까지의 검색 결과로 답변은 나오며 무엇이 실패했는지가 적힌다. 토글 `llm_failure_report` 로 배너를 끌 수 있다(보고서는 남음).
- 설정 후 순서: `python setup/check_env.py`(키·URL·실행 파일 존재) → `models test --live` → `health` → `build`.

### 4.1 OpenAI-compatible 게이트웨이 + PAT

게이트웨이가 `/v1/chat/completions`(+ `/v1/embeddings`) 를 제공하고 PAT(Personal Access Token)로 인증하는 경우. 예시 파일: `setup/config.example.pat-gateway.json`.

```jsonc
// config.json (해당 키만)
"llm_provider": "openai",
"llm_model": "gpt-4o-mini",                      // 게이트웨이가 노출하는 모델 id 그대로
"openai_base_url": "https://gateway.corp/v1",    // …/v1 까지. 경로 뒤에 /chat/completions 가 붙는다
"openai_api_key_header": "authorization",        // PAT 를 어떤 헤더에 싣나 (아래 표)
"openai_extra_headers": {},                      // 게이트웨이가 요구하는 고정 헤더 {"X-Tenant": "modem"}
"embed_provider": "openai", "openai_embed_model": "text-embedding-3-small",   // 임베딩도 게이트웨이로 보낼 때
"llm_timeout": 180
```
```ini
# .env
OPENAI_API_KEY=<PAT>          # 또는 LLM_API_KEY=<PAT> (이름만 다름). 임베딩 키가 다르면 OPENAI_EMBED_API_KEY
```

| 게이트웨이가 요구하는 인증 | `openai_api_key_header` | 실제로 나가는 헤더 |
|---|---|---|
| `Authorization: Bearer <PAT>` (대부분) | `authorization` (기본) | `authorization: Bearer <PAT>` |
| `api-key: <PAT>` (Azure OpenAI 스타일) | `api-key` | `api-key: <PAT>` |
| `X-API-Key: <PAT>` | `x-api-key` | `x-api-key: <PAT>` |
| 그 외 이름 | 그 헤더 이름 | `<이름>: <PAT>` (값 그대로) |

확인용 curl (설정과 같은 요청):
```bash
curl -s https://gateway.corp/v1/chat/completions -H "authorization: Bearer $PAT" -H "content-type: application/json" \
  -d '{"model":"gpt-4o-mini","max_tokens":8,"messages":[{"role":"user","content":"ping"}]}'
```
이 curl 이 되면 `python -m llmwiki models test --live` 도 된다. 401/403 이면 PAT 또는 헤더 이름, 404 면 `openai_base_url`(…/v1 누락) 또는 모델 id, 400 에 `max_tokens`/`temperature` 가 언급되면 게이트웨이가 신형 파라미터만 받는 경우이니 게이트웨이 담당자에게 호환 모드를 요청한다(`response_format` 미지원은 자동으로 재시도한다).

### 4.2 Anthropic-compatible 게이트웨이 + PAT

게이트웨이가 Anthropic Messages API(`/v1/messages`)를 제공하는 경우.
```jsonc
"llm_provider": "anthropic",
"llm_model": "claude-sonnet-5",
"anthropic_base_url": "https://gateway.corp",   // /v1 없이 호스트까지. /v1/messages, /v1/models 가 뒤에 붙는다
"llm_fallbacks": false                          // 게이트웨이가 anthropic-beta 헤더를 거부하면 false
```
```ini
ANTHROPIC_AUTH_TOKEN=<PAT>    # Bearer 방식 (authorization: Bearer <PAT>)
# 또는 ANTHROPIC_API_KEY=<PAT>  # x-api-key 방식
```
`anthropic` SDK 가 설치돼 있으면 SDK 로, 없으면 내장 urllib 구현으로 같은 URL/헤더를 쓴다(둘 다 `anthropic_base_url` 을 따른다). 게이트웨이가 `/v1/models` 를 제공하지 않으면 ping 은 "models 목록 미제공" 으로 통과 처리되고 `--live` 로 실제 호출을 확인한다.

### 4.3 opencode 를 headless 로 쓰기

opencode(또는 claude / codex CLI)를 비대화형 subprocess 로 실행해 LLM 역할로 쓴다. 예시 파일: `setup/config.example.headless.json`.

1. opencode 설치 + 인증(`opencode auth login` 등)을 **서버를 실행할 계정으로** 마친다. 실행에 필요한 환경변수는 `.env` 에 두면 subprocess 에 그대로 전달되고, `agents.json` 의 `opencode.env` 로도 줄 수 있다.
2. `config.json`:
   ```jsonc
   "llm_roles": {
     "answer": {"provider": "headless:opencode", "model": "anthropic/claude-sonnet-4-5"},   // model 은 opencode 의 provider/model 표기
     "expand": {"provider": "headless:opencode", "model": "anthropic/claude-haiku-4-5"},
     "verify": {"provider": "headless:opencode", "model": "anthropic/claude-haiku-4-5"}
   }
   ```
   전역 `llm_provider` 를 `headless:opencode` 로 두면 빌드 역할(extract/summary)까지 모두 프로세스 실행이 되어 매우 느리므로, **질의 역할에만** 두는 것을 권장한다(`llm_graph` 는 청크당 프로세스 1개).
3. `agents.json` 의 `opencode` 항목이 명령 템플릿이다: `["opencode", "run", "--format", "json", "-m", "{model}", "{prompt}"]`, 출력 `ndjson`. 설치된 opencode 버전이 다른 플래그/출력을 쓰면 여기만 고친다(출력이 일반 텍스트면 `"output": "text"`).
   **재시도 정책**(같은 항목): `"timeout_s": 300`(1회 실행 5분 제한 — 넘으면 프로세스를 죽임), `"retries": 3`(최대 4회 실행), `"retry_backoff_s": 5`(대기 5s·10s·15s), `"retry_on": ["timeout", "exec", "exit", "empty"]`(타임아웃 / 실행 파일 오류 / 종료 코드≠0 / 빈 출력). 4회 모두 실패하면 그 역할은 대체 경로(answer→추출식, rerank→로컬, expand/verify→생략)를 타고 결과의 `llm_report` 와 답변 상단 `⚠ LLM 실행 보고` 에 적힌다. 배선만 확인하려면 `headless:mock` (`python -m llmwiki.headless --mock --sleep 400` 같은 인자로 타임아웃 재현 가능).
4. **Windows**: npm/bun 으로 설치한 opencode 는 `opencode.cmd` 셸 스크립트다. `command[0]` 은 PATH 에서 `.cmd/.bat` 까지 찾아 절대 경로로 실행하므로 그대로 두면 되고, 안 찾히면 `"C:\\Users\\<me>\\AppData\\Roaming\\npm\\opencode.cmd"` 처럼 절대 경로를 적는다. (예전 버전은 ping 은 통과하는데 실제 호출이 `WinError 2` 로 실패했다 — 수정됨.)
5. 확인: `python -m llmwiki models test --live` → `answer headless:opencode/… live: OK reply='OK'`. 배선만 먼저 보려면 `headless:mock`.

`agents.json` 은 `{python}`(현재 인터프리터)·`{project_root}` 치환을 쓰므로 다른 PC 로 복사해도 그대로 동작한다. opencode 의 로컬 HTTP 서버(`opencode serve`) 는 OpenAI-compatible 이 아니라 현재 지원하지 않는다 — 필요하면 앞에 OpenAI 호환 shim 을 두거나 §11 의 방법으로 30줄짜리 프로바이더를 추가한다.

### 4.4 서버를 여러 사람에게 공개하기 전: 로그인·역할·권한 표·파괴적 작업 보호

상세 설계와 `security.json` 전체 항목은 [SECURITY.md](SECURITY.md). 최소 절차:
```bat
python -m llmwiki security show                          :: 저장소의 security.json 에는 admin kh82.kim/1234qwer 가 있다 → 공개 전 반드시 변경
python -m llmwiki users passwd kh82.kim                  :: 비밀번호 변경 (프롬프트)
python -m llmwiki users add bob --role class1            :: 역할: viewer < class3 < class2 < class1 < builder < admin (누적)
python -m llmwiki users add carol --role builder         :: 전체/채널 리빌드·복원까지
python -m llmwiki security perms                         :: 등급별 최소 역할 표 (read=viewer, run=class3, edit=class2, index=class1, rebuild=builder, admin/destructive=admin)
python -m llmwiki security perms set run=viewer          :: (예) eval/trial 을 viewer 에게도 → Web 보안 탭에서도 편집
python -m llmwiki serve --host 0.0.0.0 --port 8765       :: 사용자·API 키·SSO 가 없고 익명도 꺼져 있으면 기동 거부
```
- **기본 접속자 = 게스트(viewer)**: `anonymous_role: "viewer"` 라 로그인 없이도 질의·검색·조회·피드백·기대 결과 포렌식이 된다(DB 무영향). 상위 작업을 누르면 "로그인 필요(class1 이상)" 안내. 로그인을 강제하려면 `""`.
- 역할 의미(기본값): viewer 조회 · class3 +실행(eval·trial·models test) · class2 +지식 편집(규칙·pin·프롬프트·제안 승인) · class1 +색인 갱신(증분 빌드·verify --fix·유지보수) · builder +전체/채널 리빌드·복원 · admin +설정·사용자·권한. 경계는 `permissions.levels/ops` 로 조직에 맞게 옮긴다(코드 변경 없음).
- 사내 SSO 병행: `security.json` 의 `sso` 에 OIDC(issuer/client_id/redirect_uri, 비밀은 `.env LLMWIKI_OIDC_CLIENT_SECRET`) 또는 리버스 프록시 헤더 방식을 적고 `role_map` 으로 IdP 그룹 → 역할(6단계 이름). 로그인 화면에 ID/비밀번호 폼·SSO 버튼·"게스트로 계속" 이 함께 뜬다.
- CLI 도 같은 표: 공용 서버면 `"cli": {"default_role": "viewer"}` 로 낮추고 `--user <id>`(또는 `LLMWIKI_USER`/`LLMWIKI_PASSWORD`, `LLMWIKI_API_KEY`)로 승격. 거부는 종료 코드 5, 감사 로그 DENY.
- 정책: edit/index/admin 등급은 확인 대화상자, **rebuild(전체/채널 리빌드·스냅샷 복원)와 destructive(로그 삭제·config reset·사용자 삭제)는 확인 문구(`DELETE INDEX`) + 로컬 계정 비밀번호 재입력**, 실행 직전 자동 스냅샷(`snapshot list|restore`), 전부 `logs/audit.jsonl` 에 기록.
- HTTPS 와 IP 제한은 리버스 프록시에서(SECURITY.md §8).

### 4.5 외부 LLM 을 MCP 로 붙이기 (같은 PC · 원격 · 다수)

상세는 [MCP.md](MCP.md). 요약:
```bat
:: 같은 PC (stdio): 클라이언트가 자식 프로세스로 실행 — cwd 는 프로젝트 루트, Windows 는 python.exe 절대 경로 권장
claude mcp add llmwiki -- python -m llmwiki mcp
:: 원격 / 여러 LLM (Streamable HTTP): serve 가 같은 포트에 POST /mcp 를 연다. API 키로 인증
python -m llmwiki apikey add claude-desktop-kim --role viewer          :: 토큰 lwk_… 은 이때 한 번만 표시
:: 클라이언트 설정: {"type":"http","url":"http://wiki-host:8765/mcp","headers":{"Authorization":"Bearer lwk_…"}}
:: stdio 전용 클라이언트가 원격을 쓸 때 (브리지)
claude mcp add llmwiki-remote -- python -m llmwiki mcp --connect http://wiki-host:8765/mcp --token lwk_…
:: MCP 만 단독 포트로
python -m llmwiki mcp --transport http --host 0.0.0.0 --port 8766
:: 이 환경 값(python 절대 경로·프로젝트 루트·서버 URL)이 채워진 클라이언트 설정 4종(stdio/HTTP/브리지/claude 한 줄 명령) 출력
python -m llmwiki mcp --client-config --url http://wiki-host:8765 --token lwk_…
```
플래그를 생략하면 `config.json` 의 `web_host/web_port`(serve), `mcp_transport/mcp_host/mcp_port`(mcp), `mcp_url`(브리지 대상) 이 기본값이다 — 서버마다 다른 포트/바인드는 파일에 적어 두고 명령은 `serve` / `mcp` 만 치면 된다. 클라이언트 쪽 설정 원본은 `setup/mcp_clients.example.json`.

**연결 확인 (클라이언트를 건드리기 전에 서버에서 먼저)** — [MCP.md](MCP.md) §4:
```bat
python -m llmwiki mcp --doctor                     :: 도구 12종·스키마·플러그인·소스·토글·인증·전송을 한 번에 (오류가 있으면 종료코드 1)
python -m llmwiki mcp --doctor --check-sources     :: 외부 소스에 실제로 접속까지 (느림)
curl -s http://wiki-host:8765/mcp -H "Authorization: Bearer lwk_…" -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}"
```
- 잘못되거나 폐기된 `lwk_` 키는 게스트로 강등되지 않고 **401** 이므로 클라이언트 로그에서 바로 드러난다.
  단 `security.json` 의 `mode` 가 `auto` 인 채로 **`127.0.0.1` 에 바인드하면 인증 자체가 꺼진다** — 루프백에서 키 동작을 시험하려면 `mode: "on"` 으로 두고 봐야 한다.
- **붙는 클라이언트마다 키를 따로 발급한다.** 동시성 제한(`server.json` 의 `max_parallel_per_user`, 기본 3)이 키 단위라, 여러 LLM 이 한 키를 공유하면 네 번째 동시 호출부터 429(`per_user_limit`)를 받는다 ([CONCURRENCY.md](CONCURRENCY.md)).
- 도구 호출이 `isError` 로 돌아오면 메시지에 `inputSchema` 가 함께 오므로 붙는 LLM 이 스스로 고쳐 다시 부를 수 있다 ([MCP.md](MCP.md) §2.1).

### 4.6 다른 RAG · 검색 API · MCP 서버를 붙이기 (외부 LLM 은 우리 /mcp 하나만)

상세는 [RAG_FEDERATION.md](RAG_FEDERATION.md). 요약: `mcp_sources.json` 에 소스를 적고(원본 `setup/mcp_sources.example.json` — `peer_wiki`(다른 llmwiki, http) · `kb_rest`(REST 검색 API) · `mango`(stdio) · `mock`(테스트)), 토글 두 개를 켠다.
```bat
:: (1) 연결 확인 — enabled 와 무관하게 이름을 주면 실행
python -m llmwiki mcp-source test peer_wiki                          :: ok, tools=[…]
python -m llmwiki mcp-source fetch kb_rest /search "{\"query\":\"AGC\",\"k\":2}"   :: REST 원 응답 → retrieve 매핑의 result_path/필드 결정
:: (2) 검색 채널: 질의마다 외부 결과를 ext_<source> 채널로 융합 (인용 [C#], hits.external 에 출처)
python -m llmwiki mcp-source retrieve "RX AGC 수렴" --source kb_rest    :: 매핑 확인
python -m llmwiki config set external_rag=true                        :: 또는 --external-rag / 사이드바 토글
python -m llmwiki query "RX AGC 수렴 지연 원인" --trace                :: external_rag → rrf_fuse.sources 의 ext_kb_rest → external_inject → rerank
:: (3) 페더레이션: 외부 서버의 tool 을 우리 /mcp 에 <source>__<tool> 로 노출 (클라이언트 설정 변경 없음)
python -m llmwiki config set mcp_federation=true
python -m llmwiki mcp-source federated                                :: tools=[peer_wiki__wiki_query, kb_rest__search, …], plugins
:: (4) 코드 수정 없이 도구 추가: plugins/mcp_tools/<이름>.py 의 register(add_tool)  (예시 _example_echo.py)
```
```bat
:: (5) 확인 — 무엇이 붙었고 무엇이 안 붙었는지 한 번에
python -m llmwiki mcp --doctor --check-sources     :: 소스 선언·연결·토글·페더레이션 이름·플러그인 적재
```
리허설용 목업: `python -m llmwiki.mcp_client --mock-rest 8799`(REST) · `mcp_sources.json` 의 `mock`(stdio). 튜닝 `channel_w_external`·`external_rag_k`·`external_rag_inject`, 소스별 `weight`·`when(always|fallback)`·`timeout_s`. 외부 소스 오류는 trace `external_rag.errors` 에만 남고 질의는 계속된다. 서로 expose 한 두 서버도 재귀 방지 헤더로 안전하다.

도구는 **12종**이며 모두 read 등급(색인을 바꾸지 않음): `wiki_query`, `wiki_search`, `wiki_related`, `wiki_doc`, `wiki_entity`, `wiki_propose`(제안 큐), `wiki_feedback`, `wiki_forensic`(기대 결과 포렌식), `wiki_status`, `wiki_analysis`(상세 분석 리포트), `wiki_sources`, `wiki_external_search`. `tools/list` 는 각 도구에 `annotations`(읽기/쓰기 구분)를 함께 준다 — 붙는 LLM 이 "이 도구가 무엇을 바꾸는가" 를 스스로 판단한다. 여러 LLM 이 동시에 붙으면 서버 락으로 직렬화되어 큐잉되고, 제한을 넘으면 429 + `Retry-After` 로 거부된다([MCP.md](MCP.md) §3.1).

## 5. 코퍼스 계약 적용

1. `python -m llmwiki corpus types` 로 유형과 필수 필드를 확인하고, 조직 문서 유형이 다르면 `schemas/<type>.json` 을 추가/수정한다.
2. 기존 5,000개 문서에 front matter 를 한 번에 붙일 수 없다면 `schemas/infer.json` 의 경로/파일명 규칙을 조직 폴더 구조에 맞춘다(`*/issues/*` → issue 등). 추론된 문서는 lint 에 warn 으로 표시된다.
3. 문서 ID 체계가 다르면(`ISSUE-` 가 아니라 `JIRA-`, `MR-` 등) `data/rules.json → id_patterns` 의 regex/canonical 과 `schemas/<type>.json → id_pattern`, `schemas/infer.json → id_from_text` 를 함께 바꾼다. 관계 이름은 `link_rules`, front matter 키 매핑은 `explicit_rels`.
4. `corpus lint-file <파일>` 로 샘플 문서를 검사하고, 생성 도구(사람/LLM)에는 `corpus example <type>` 출력을 템플릿으로 준다.
5. 도메인 용어를 `query_rules.json` 에 넣는다 (`rules add acronym PDCCH "Physical Downlink Control Channel"`). 복합어는 `compound` 에.

## 6. 첫 빌드와 검증

```bat
python -m llmwiki health                 :: 프로바이더 ping 포함. FAIL 항목이 있으면 build 가 시작되지 않음
python -m llmwiki build --full --trace   :: 초기화 → 색인 → 임베딩(재개/캐시/적응형 배치) → 그래프(explicit/rule/cooccur) → 위키 → prune → verify
python -m llmwiki build verify           :: 정합성 (FTS↔청크, coverage, 댕글링, 고아, 위키 stale) — 문제 시 build verify --fix
python -m llmwiki embed report           :: coverage 100% 인지, 실패 청크 없는지
python -m llmwiki corpus lint            :: 스키마 오류 목록
python -m llmwiki graph --provenance explicit --limit 20   :: CL→Issue 관계가 생겼는지
```
빌드가 중단되면(네트워크·API 장애) 그냥 다시 `build` 하면 된다. 남은 청크만 임베딩하고(`resume_missing`), 진행률은 `build status` / Web › Corpus › 임베딩.

### 6.1 채널별 빌드 (fts / vector / graph 를 따로)

세 채널의 산출물(`chunks_fts` · `embeddings` · `entities/relations/mentions`)은 모두 **`chunks` 테이블만 읽고 서로의 테이블은 읽지도 지우지도 않는다**. 그래서 한 채널만 다시 만들 수 있고, 다른 채널은 그대로 남는다.
```bat
python -m llmwiki build fts               :: 토크나이저·복합어(query_rules compound)·메타 토큰을 바꾼 뒤: FTS 행만 전부 다시 (임베딩·그래프 불변)
python -m llmwiki build vector            :: 임베딩 없는 청크만 임베딩 (coverage 보충). --full 이면 전부 다시 (hash 는 IDF 재적합; API 임베더는 캐시 적중이면 비용 0)
python -m llmwiki build graph             :: data/rules.json·스키마 관계 규칙을 바꾼 뒤: 그래프를 비우고 전체 청크에서 재추출 + 커뮤니티 + 위키 (FTS·임베딩 불변)
python -m llmwiki build --channels fts,vector   :: 일반(증분/전체) 빌드에서 지정 채널 단계만 (나머지는 skipped 로 기록)
python -m llmwiki build --no-embed / --no-build-fts / --no-rule-graph   :: 토글로 채널 단계 끄기 (config.json toggles 에도 같은 이름)
```
- 채널 리빌드는 `rebuild` 등급(builder, 확인 문구 또는 `--yes`) — chunks 를 지우지 않으므로 스냅샷은 만들지 않는다. Web: Corpus › 빌드 탭의 채널 체크박스와 "FTS 재색인 / 벡터 재임베딩 / 그래프 재구축" 버튼.
- **서로 영향을 주지 않는 이유와 예외**: 세 채널의 공통 상위 의존은 청크 ID(`doc_id#n`)다. 문서가 바뀌거나 `chunk_max_chars`/`chunk_overlap_chars` 가 바뀌면 청크가 다시 나뉘고, 그때는 증분/전체 빌드가 세 채널을 함께 처리한다(채널 빌드는 "청크는 그대로, 산출물만 다시" 인 경우용). 채널 빌드가 끝나면 `verify` 가 자동 실행되어 `fts_missing`(fts) / `embedding_coverage`(vector) / `mention_dangling·entity_orphans·wiki_stale_pages`(graph) 로 결손을 알려주고, 결과의 `counts_before/after` 로 다른 채널 행 수가 그대로임을 보여준다(달라지면 `channel_isolation` 경고). `build_version` 이 올라가 질의 캐시·벡터 행렬·엔티티 인덱스가 무효화된다.
- 토글을 끈 채 빌드하면(예 `build_fts: false`) 그 채널은 stale 이 되고 `build verify` 가 `fts_missing` 을 보고한다 → `build fts` 로 보충.

## 7. 평가 기준선과 튜닝

1. `eval/questions.json` 을 자기 코퍼스 질문(기대 문서 id 부분 문자열 + 기대 용어) 25~50개로 교체.
2. `trial run --name baseline` → `trial run --name t1 --set fusion_method=zscore --set top_k_final=10` → `trial compare baseline t1`.
   지표: hit@k, MRR, term_recall, groundedness, citation_precision, insufficient_rate, fallback_rate, p95_ms, tokens/query, embed_coverage.
3. 프리셋 비교: `trial run --name q --preset quality`, `trial run --name s --preset speed`.
4. 융합 방식: `fusion compare`. 채널 가중: 튜닝 `channel_w_*`, 문서 유형 부스트 `doc_type_boost`, 시간 `time_mode/time_boost_w`, provenance `provenance_w`.
5. Web › Quality › Trial 비교에서 질문별 승/패, 설정 diff, 추천을 본다.

### 7.0 어느 손잡이를 만질지 모를 때 — 최적화 자료 묶음을 LLM 에게 준다

어떤 토글·튜닝·설정이 어느 단계에 어떻게 작용하는지는 [OPTIMIZATION_GUIDE.md](OPTIMIZATION_GUIDE.md) 에 표로 있다
(코드의 구조·튜닝 레지스트리에서 자동 생성 — 단계가 바뀌면 `python -m llmwiki arch doc` 로 다시 만든다).

그 가이드만 주지 말고, **지금 이 서버의 설정값 + 실제 질의 한 건의 단계별 실측 + 지시문까지 한 파일로 묶어** LLM 에게 준다.

```bat
python -m llmwiki query "실제로 개선하고 싶은 질문" --analyze        :: 분석 모드로 한 번 실행
python -m llmwiki optimize last --focus quality --out bundle.md    :: A 설정 · B 실측 · C 요청 · D 손잡이 지도
```

`bundle.md` 를 통째로 LLM 대화에 붙여 넣으면 C 절의 지시문에 따라 "바꿀 설정을 효과가 큰 순서로" 답한다.
`--focus` 는 `quality|speed|tokens|all`. Web UI 에서는 Ask › 📊 상세 분석 리포트 › **📦 최적화 자료 묶음 다운로드**(또는 묶음 복사),
API 는 `GET /api/optimize/bundle?request_id=<id>&focus=quality`. 제안을 받은 뒤에는 반드시 §7 의 `trial run` → `tuning set` → `trial run` → `trial compare` 로 회귀를 확인한다.

### 7.1 문서 단위 확장 (doc_expand)

리랭크로 뽑힌 청크가 속한 문서의 **나머지 청크 중 질의와 관련 있는 것**을 컨텍스트에 추가한다(표·목록·후속 절이 청크 경계에서 잘리는 문제 완화). 토글 `doc_expand`(기본 on; `speed`/`token` 프리셋은 off), 튜닝(stage `context`): `doc_expand_top_docs`(3 문서) · `doc_expand_max_chunks`(문서당 3) · `doc_expand_min_score`(0.2) · `doc_expand_mode`(hybrid = 키워드 커버리지 + 벡터 유사도 가중합, keyword | vector) · `doc_expand_w`(0.5).
```bat
python -m llmwiki query "ISSUE-2001 의 원인과 수정 CL 은?" --trace       :: doc_expand 단계: docs=3 candidates=7 added=5 …, 근거 목록의 why=doc_expand
python -m llmwiki query "…" --no-doc-expand                            :: 끄고 비교
python -m llmwiki trial run --name de-on  && python -m llmwiki trial run --name de-off --set doc_expand=false && python -m llmwiki trial compare de-on de-off
python -m llmwiki tuning set doc_expand_max_chunks=6 doc_expand_min_score=0.1   :: 문서를 더 넓게
python -m llmwiki tuning set doc_expand_mode=full doc_expand_max_chunks=20      :: 근거가 나온 문서를 통째로
```

**`doc_expand_mode=full` — 근거 문서 전체 읽기.** 점수로 거르지 않고 그 문서의 나머지 청크를 문서 순서대로 전부 넣는다.
"한 청크가 걸리면 그 문서를 통째로 확인" 하고 싶을 때 쓴다. 상한은 `doc_expand_max_chunks`(문서당)와 `context_max_chars`(전체)
두 개뿐이므로, full 로 바꿀 때는 `doc_expand_max_chunks` 를 함께 키우고 `context_max_chars` 로 총량을 잡는다.
품질은 올라가지만 입력 토큰이 늘어난다 — `token`/`speed` 프리셋에서는 쓰지 않는 것이 좋다.

| 모드 | 무엇을 넣나 | 언제 |
|---|---|---|
| `keyword` | 질의 키워드가 실제로 들어 있는 청크 | 해시 임베더처럼 벡터를 못 믿을 때 |
| `vector` | 질의와 의미가 가까운 청크 | 의미 임베더를 쓸 때 |
| `hybrid`(기본) | 위 둘의 가중합(`doc_expand_w`) | 보통 |
| **`full`** | **그 문서의 나머지 전부**(상한까지, 문서 순서) | 문서 하나를 끝까지 읽혀야 하는 질문(설계 문서·회의록·절차서) |
추가 청크는 부모 청크 바로 뒤에 문서 순서로 들어가며 `[C#]` 로 인용된다. 컨텍스트 상한(`context_max_chars`)은 그대로 적용되므로 토큰이 무한히 늘지 않는다. 확장이 왜 안 됐는지는 `forensic expect … ` 의 `doc_expand` 행에서 확인한다. 실측(샘플 코퍼스 25문항): 주 후보 순위 지표(hit@5·MRR)는 동일, 컨텍스트 청크 10.2→13.5개(+4.4), 글자 1,609→2,085(+30%), 지연 +0.8ms — [IMPLEMENTATION_PLAN_0914.md](IMPLEMENTATION_PLAN_0914.md) §7.2. 평가 지표는 보조 청크를 순위에서 제외하고 계산한다(`evalset.primary_hits`).

## 8. 스케줄 (매일 증분)

서버를 상시 띄워 두는 환경이면 **서버 내장 스케줄러**가 가장 간단하다. `setup/schedule.example.json` 을 `schedule.json` 으로 복사하고 필요한 작업만 `enabled: true` 로 두면 된다. 파일을 저장하는 즉시 서버가 다시 읽는다.

```powershell
copy setup\schedule.example.json schedule.json
python -m llmwiki schedule validate      # 작업 정의 검사
python -m llmwiki schedule list          # 다음 실행 시각 확인
python -m llmwiki schedule run nightly_build   # 한 번 즉시 실행해 보기
```

가장 자주 쓰는 세 가지:

```json
{"name": "증분빌드", "when": {"cron": "*/30 * * * *"}, "action": {"type": "build"}}
{"name": "새벽전체빌드", "when": {"at": "02:30", "days": ["sun"]}, "action": {"type": "build", "full": true}}
{"name": "주간자가진화", "when": {"at": "03:30", "days": ["mon"]}, "action": {"type": "evolve", "op": "review"}}
```

동작은 19종이며(증분/전체 빌드 · URL 수집 · 파이썬 스크립트 · 임의 CLI 명령 · 질의 · LLM/headless 호출 · MCP 수집 · 유지보수 · HTTP 호출 · evolve · memory · precompute · eval · trial · snapshot · wiki · forensic · embed_report) 각각의 인자와 예시는 [SCHEDULER.md](SCHEDULER.md) §3 에 있다. 스케줄 작업도 요청 관리자의 티켓을 받으므로 Web 관리 › 서버 모니터에 진행률이 보이고 중지할 수 있다.

서버를 상시 띄우지 않는 환경이면 OS 스케줄러가 CLI 를 호출한다. 파일 락으로 서버 워처와 충돌하지 않는다.
```powershell
.\setup\schedule_build.ps1 -Register -Time 02:30    # Windows 작업 스케줄러 (현재 사용자)
```
```bash
30 2 * * * /path/setup/schedule_build.sh >> /path/logs/cron.log 2>&1    # cron
```
개발 중 "저장 즉시 반영" 이 필요하면 `serve` 의 워처(`auto_build` 토글)나 `watch --interval 300` 을 보조로 쓴다.
삭제/이름 변경도 추적된다: 삭제된 문서의 청크·FTS·임베딩·멘션·관계·위키 페이지가 정리되고, rename 은 임베딩 캐시로 재임베딩 없이 재색인된다.

## 9. 운영

| 할 일 | 방법 |
|---|---|
| Web UI | `python -m llmwiki serve --port 8765` → Ask / Corpus / Knowledge / Quality / Evolve / Settings / Observability. 화면 기능(탭 고정·분할 보기·활동 표시기·계정별 프로파일·분석 리포트)은 [WEB_UI.md](WEB_UI.md). 테마는 헤더 셀렉터(기본 Light, `themes/` 에 CSS 추가로 확장) |
| 일반 사용자에게 어떻게 보이는지 확인 | `serve --host 0.0.0.0` 로 띄우면 인증이 켜져 로그인 전에는 `guest/viewer` 로 보인다 (127.0.0.1 로 띄우면 `mode: auto` 가 인증을 꺼서 항상 admin) — [WEB_UI.md](WEB_UI.md) §8 |
| 사용자마다 화면 설정을 따로 | 헤더의 `💾 내 설정 저장`. `data/profiles.json` 에 계정별로 저장되고 로그인 시 자동 적용된다. 서버 설정은 바뀌지 않는다 — [WEB_UI.md](WEB_UI.md) §4 |
| MCP 로 노출 | `claude mcp add llmwiki -- python -m llmwiki mcp` (cwd = 프로젝트 루트). 도구: `wiki_query(mode, doc_types, preset)`, `wiki_search`, `wiki_related`, `wiki_doc`, `wiki_entity`, `wiki_propose`(HITL 제안), `wiki_status` |
| 로그 | `logs/` (llmwiki.log · error.log · build.log · query.log, JSON Lines, 로테이션). `logs grep --request <id>` 로 프로파일과 연결 |
| 프로파일 | `requests last`, Web › Observability › 요청 프로파일 (run_id → 로그) |
| 근거 부족/품질 문제 | `forensic last`, `forensic summary`, Web › Quality › 포렌식. 누적 소견은 `memory consolidate` 로 제안(corpus_gap/query_rule/tuning) 생성 |
| 품질·지연·토큰이 마음에 안 든다 (어느 설정을 만질지 모르겠다) | **상세 분석 모드** — 사이드바 토글 `analysis_mode` 또는 `query "…" --analyze [--focus quality|speed|tokens]` → `logs/analysis/req_<id>.md` (설정 스냅샷·단계 타임라인·채널/융합/리랭크/컨텍스트 상세·세 렌즈 소견과 조절점·프롬프트 샘플) → LLM 에게 첨부해 튜닝 제안을 받는다. 지난 요청은 `analyze <id|last> --print`, Web Ask 📊, MCP `wiki_analysis` — [ANALYSIS_MODE.md](ANALYSIS_MODE.md) |
| "이 문서/수치가 답에 있어야 했다" (사용자 피드백) | **기대 결과 포렌식** `forensic expect <request_id|last> --doc ISSUE-2003 --term 1.5dB [--propose]` — 같은 설정으로 검색을 재실행해 기대 근거가 fts/vector/graph → 융합 → 리랭크 → 컨텍스트 → 답변 중 어디서 탈락했는지 + 수정안(규칙/pin/튜닝/코퍼스). Web › Ask 결과의 🎯, Quality › 포렌식, MCP `wiki_forensic` — [FORENSIC.md](FORENSIC.md) |
| LLM 이 느리거나 죽음 | 답변 상단 `⚠ LLM 실행 보고` / 결과 `llm_report` / 빌드 alerts `llm_failures` 를 본다. `agents.json timeout_s·retries`, `config.json llm_timeout·llm_retries`, `models test --live` |
| 외부 LLM(MCP) 관리 | `apikey add|list|remove`, Web 보안 탭 "API 키", `security audit`(op=mcp 거부 기록) — [MCP.md](MCP.md) |
| 권한 조정 | `security perms [set …]`, Web 보안 탭 "권한 표" — [SECURITY.md](SECURITY.md) §2.3 |
| 자가진화 | `evolve status/apply/reject`, Web › Evolve. `evolve_auto_apply` 는 기본 OFF(HITL). tuning 제안은 자동 적용 대상 아님 |
| 메모리 decay | `memory decay` (반감기 `memory_half_life_days`) — 스케줄에 주 1회 넣어도 됨 |
| 캐시 | `precompute run` (평가셋+빈번 질의 사전 계산), 재빌드 시 자동 무효화 |
| 유지보수 | `maintenance vacuum|fts_optimize|wal_checkpoint`, `embed clear-cache`(캐시 초기화) |
| 여러 사람이 동시에 쓴다 (30명 기준) | `setup/server.example.json` → `server.json` 으로 복사해 동시 실행 슬롯·대기열·속도 제한을 환경에 맞춘다. 권장값과 계산 근거는 [CONCURRENCY.md](CONCURRENCY.md) §3. 사용자가 `429`(속도 제한) 또는 `503`(대기열 꽉 참)을 본다면 §9 문제 해결표 |
| 지금 서버가 무엇을 하고 있나 | Web 관리 › 서버 모니터, 또는 `python -m llmwiki server stats` · `server activity`. 진행 중·대기 중 요청, 사용자·IP별 사용량, 거절 사유, 세션, LLM 회로 상태. 원격 서버는 `--server http://host:8765 --user <id>` |
| 특정 사용자/IP 를 막거나 내보내야 한다 | `server block --ip 10.1.2.3 --reason "…"` · `server block --user hong` · `server kick --user hong`(세션 종료) · 해제는 `server unblock`. 전체 정지는 `server maintenance on --message "…"`(admin 은 계속 사용 가능) |
| 오래 걸리는 빌드·질의를 멈추고 싶다 | Web 진행 패널의 `중지`, CLI 는 `Ctrl+C`, 다른 곳에서 돌고 있으면 `server activity` 로 토큰을 찾아 `server cancel <token>`. 빌드는 중지해도 그때까지의 진행분을 저장하므로 다음 증분 빌드가 이어서 한다 |
| LLM 게이트웨이가 죽어서 전부 느려졌다 | 역할별 회로 차단이 자동으로 끊는다(`server circuits` 로 상태 확인, `server circuits --reset` 으로 해제). 그 동안에도 답변은 추출식으로 계속 나오며 상단에 `⚠ LLM 실행 보고` 가 붙는다. 정책 조정은 `config.json llm_roles.<role>` — [CONCURRENCY.md](CONCURRENCY.md) §7 |
| 사용자마다 Web 화면 설정을 따로 두고 싶다 | Web 우상단 `프로파일 저장` — 테마·토글·프리셋·오버라이드·고정 탭·화면 분할이 계정에 저장된다(`data/profiles.json`). 서버 공용 설정은 바뀌지 않는다. 게스트는 브라우저에만 남는다 |
| 터미널에서 한글이 깨진다 | 기본값(`console_encoding: auto`)이면 CLI 가 알아서 맞춘다. 콘솔 코드페이지를 바꿀 수 없는 환경이면 `config.json` 에 `"console_encoding": "native"` 를 넣어 표현 불가 문자만 낮춘다. 현재 상태는 `health` 와 `python setup/check_env.py` 가 보고 |

## 10. 문제 해결

| 증상 | 원인 / 조치 |
|---|---|
| `build aborted: health check failed: corpus_dirs` | 코퍼스 경로 없음. `config set corpus_dirs=경로`. 초기화 전에 검사하므로 기존 색인은 유지됨 |
| `build refused: another build is running` | 다른 프로세스가 빌드 중(락). 죽은 프로세스의 락은 자동 회수. `build status` 로 pid 확인 |
| 답변 맨 위에 `ℹ LLM 미사용 — 근거 문서의 원문 문장을 골라 구조화한 답변` | LLM 없음/모델 미설치. 근거 표·관계는 나오지만 서술형 설명은 없음. `models test --live`. Ollama 는 `ollama pull <model>` |
| `models test` 는 OK 인데 실제 질의는 LLM 미사용/오류 | ping(모델 목록)만 통과한 것. `models test --live` 로 실제 호출 — 401/403 = PAT·헤더(`openai_api_key_header`), 404 = base_url/모델 id, headless 는 실행 파일·인증 |
| `llm_provider=auto` 인데 게이트웨이/opencode 가 안 잡힘 | auto 는 openai/headless 를 고르지 않는다. `llm_provider=openai` 또는 `llm_roles.<role>.provider=headless:opencode` 로 명시 |
| headless: `executable not found` / `WinError 2` | PATH 에 없음. `agents.json` `command[0]` 에 절대 경로(Windows 는 `...\npm\opencode.cmd`) |
| 빌드/질의가 오래 걸리는데 멈춘 건지 모르겠다 | CLI 는 `⏳ 단계 › 진도 · LLM 응답 대기 Ns` 줄, Web 은 진행 패널(단계·%·LLM 대기 시간·최근 로그)을 본다. LLM 대기가 `llm_timeout` 을 넘으면 실패로 기록되고 다음 청크로 넘어간다 |
| `fts_trigram` 을 켰더니 전체 리빌드가 아주 오래 걸린다 | **2026-09-15 에 고쳤다.** 예전에는 청크마다 FTS 행을 지우면서 매번 색인 전체를 훑어 빌드가 청크 수의 제곱으로 늘어났다(16,882 청크·trigram 기준 색인 단계만 약 17분). 지금은 전체 리빌드에서 색인을 한 번에 비우고 다시 채운다(같은 조건에서 몇 초). 구버전에서 올라왔다면 코드를 갱신하고 `build --full` 을 한 번 돌린다. 되돌아가지 않았는지는 `python tools/verify/bench_fts.py --no-old` 로 확인한다(청크가 2배면 시간도 2배여야 한다) |
| 전체 리빌드 중에는 질의가 전부 대기에 걸린다 | 설계상 그렇다. `build --full` 은 배타 작업이라 색인이 반쯤 바뀐 상태를 읽지 않도록 읽기를 막는다. 빌드가 끝나면 대기열이 한꺼번에 빠진다. 빌드 중에도 질의를 받으려면 `server.json` 의 `concurrency.reads_during_build` 를 `always` 로 두되, 그때는 일부 질의가 재색인 중인 문서를 놓칠 수 있다. 증분 빌드(기본)는 원래 질의를 막지 않는다 |
| 활동 목록에 완료가 `3 ms` 로 찍힌다 | 실행이 안 된 것이 아니라 **답변 캐시·사전계산 적중**이다. 같은 줄의 `캐시`/`사전계산` 배지가 이유를 알려 준다 |
| `query_cache` 를 껐는데도 계속 0초로 답한다 | **캐시가 둘이다.** `query_cache` 는 메모리 캐시, `precompute` 는 SQLite 의 영속 answer_cache 다. 매번 새로 계산하려면 **둘 다** 꺼야 한다(실측: 하나만 끄면 2회차가 0.85ms, 둘 다 끄면 매번 30초대). 저장된 내용은 `precompute status`, 비우기는 `precompute clear` |
| 답변에 같은 구절이 수십 번 반복된다 | 작은 모델이 긴 컨텍스트에서 빠지는 **반복 루프** 고장이다. 지금은 자동으로 잘라내고 상단에 이유를 표시하며 그 답변은 캐시에 넣지 않는다. 이미 저장된 고장 답변은 `precompute check` 로 찾아 `precompute clear --broken` 으로 지운다. 억제 강도는 `config.json` 의 `llm_frequency_penalty`(0.3)·`llm_repeat_penalty`(1.1), 탐지 기준은 `tuning.json` 의 `answer_repeat_*` |
| 로그에 DEBUG 줄이 안 보인다 | 서버가 INFO 로 돌고 있으면 DEBUG 기록 자체가 없다. Web 관리 › 서버 모니터의 로그 레벨을 DEBUG 로 바꾸거나 CLI 에 `--log-level DEBUG` 를 주고 **그 뒤에** 질의를 다시 돌린 다음 조회한다 |
| 질의를 하면 화면이 한참 멈췄다가 한꺼번에 갱신된다 | 브라우저가 한 사이트에 동시 연결을 6개까지만 열기 때문이다. **2026-09-15 에 고쳤다** — 서버를 HTTP/1.1 keep-alive 로 바꾸고(`concurrency.keep_alive_s`, 기본 30초) 화면 갱신 요청을 하나로 합쳤다. 그래도 느리면 리버스 프록시가 keep-alive 를 끊고 있는지 확인한다 |
| 답변이 "근거 부족(insufficient)" | 코퍼스에 없거나 표기 불일치. `forensic last` → `rules add synonym …` / 문서 추가 / `fallback_loop` 토글 |
| `[미확인: 근거에서 확인되지 않음]` 표기 | claim_check 가 인용 근거와 대조해 지지되지 않는 문장. `claim_policy`(mark/drop/refine), `answer_refine` |
| coverage < 100% | 임베딩 실패(429/타임아웃). `embed report` 로 실패 청크 확인 → 다음 `build` 에서 자동 재개 |
| `dim mismatch` / `embedding_dim` warn | 임베더/차원 변경 → `build --full` |
| 시간 질의가 안 맞음 | `timezone`, `week_start` 확인, `time "지난주"` 로 파싱 확인. 문서 `date` 가 없으면 파일명/mtime 추론 |
| 그래프에 CL→Issue 가 없음 | CL 문서 `related.issues` 누락(lint error) 또는 `id_patterns` 불일치 |
| 한글 검색 recall 낮음 | `query_rules.json` compound/synonym 추가, `tuning set tokenizer=kiwi`(+`pip install kiwipiepy`) 후 `build fts`(FTS 만 재색인; 임베딩·그래프 불변), `fts_trigram` 토글 |
| 질의가 느리다 / 토큰을 많이 쓴다 / 품질이 들쭉날쭉한데 원인을 모르겠다 | `query "…" --analyze --focus speed|tokens|quality` → 리포트 §6/§7/§5 의 소견과 조절점(현재값 포함) → `tuning set`/`config set` 후 재실행 비교, `trial run` 으로 회귀 확인 — [ANALYSIS_MODE.md](ANALYSIS_MODE.md) |
| 기대한 문서가 답에 없다 | `forensic expect last --doc <ID> --term <용어>` → 탈락 단계별 원인과 수정안. retrieval 탈락+키워드 없음 = 어휘 불일치(`rules add synonym`), 순위가 top_k 바로 밖 = `top_k_*`/pin, rerank/context 탈락 = `rerank_candidates`/`context_max_chars`, answer 탈락 = `answer_length_target=long` — [FORENSIC.md](FORENSIC.md) §2.4 |
| `⚠ LLM 실행 보고: … N회 시도 후 실패` | LLM 호출이 재시도 후에도 실패해 대체 경로(추출식 등)로 답변. headless: `agents.json timeout_s`(5분)·`retries`(3), 실행 파일·인증(`models test --live`); HTTP: `llm_timeout`, 게이트웨이 상태. 배너만 끄려면 토글 `llm_failure_report` |
| `!! 권한 부족: 'cli:build' …` (종료 코드 5) | `security.json cli.default_role` 이 낮음. `--user <id>` / `LLMWIKI_USER`+`LLMWIKI_PASSWORD` / `LLMWIKI_API_KEY` 로 승격, 또는 `security perms set index=viewer` 처럼 표를 조정 |
| Web 에서 버튼이 "로그인 필요" | 게스트(anonymous_role=viewer)는 DB 무영향 기능만. 계정을 받아 로그인(헤더 "로그인 →") |
| MCP 401 | `lwk_` 키 오타/폐기(`apikey list`; 잘못된 키는 게스트로 강등되지 않고 항상 401) 또는 `anonymous_role=""` 인데 토큰 없음 — [MCP.md](MCP.md) §4 |
| 서버가 엉뚱한 포트/주소로 뜸 | `config.json web_host/web_port`(serve) · `mcp_host/mcp_port`(mcp --transport http) 가 기본값. 플래그 `--host/--port` 가 우선. `setup/check_env.py` 가 현재 기본값을 출력 |
| 외부 RAG 결과가 답변에 안 나옴 / `mcp-source test` 401·599 | [RAG_FEDERATION.md](RAG_FEDERATION.md) §7: 토큰(`token_env`)·url·`result_path` 매핑, trace `external_rag`→`rrf_fuse.sources`→`external_inject`→`rerank_*` 순으로 탈락 지점 확인, `external_rag_inject`/`weight` 조정 |
| `/mcp tools/list` 에 `<source>__<tool>` 이 없음 | 토글 `mcp_federation` + 소스 `enabled`/`expose`. `mcp-source federated` 의 `errors`. 페더레이션 하위 호출(깊이 헤더)에서는 의도적으로 숨김 |
| 채널 빌드 후 `channel_isolation` 경고 | 다른 채널 행 수가 바뀜(비정상). `build verify --fix` 후 해당 채널 재빌드, 지속되면 `build --full` |
| Web UI 가 옛 화면 | 브라우저 캐시 — 새로고침(Ctrl+F5). 정적 파일은 `Cache-Control: no-store` |

## 11. 포팅 시 코드 변경이 필요한 곳 (없어야 정상)

설정·규칙·프롬프트·스키마·권한 표·재시도 정책·서버 포트·포렌식 임계는 모두 파일로 외부화되어 있으므로 코드를 고치지 않아도 된다. 2026-09-14/15 추가 기능의 설정 위치는 §3.2 표 한 장에 모아 두었다: 권한/익명/CLI 게이트/API 키 → `security.json`(SECURITY.md), 서버·MCP 바인드/포트/전송/브리지 → `config.json web_*/mcp_*`, MCP 클라이언트 설정 → `setup/mcp_clients.example.json` 또는 `mcp --client-config`(MCP.md), 채널 빌드 토글 → `config.json toggles.build_fts/embed/rule_graph`, 문서 단위 확장 → `toggles.doc_expand` + `tuning.json doc_expand_*`, 재시도 → `config.json llm_timeout/llm_retries/llm_retry_backoff_s` + `agents.json timeout_s/retries/retry_backoff_s/retry_on`, 실패 보고 → `toggles.llm_failure_report`, 기대 결과 포렌식 임계 → `tuning.json forensic_*`, 다른 RAG/검색 API/MCP 서버 연결 → `mcp_sources.json`(transport·retrieve·expose) + `toggles.external_rag/mcp_federation` + `tuning.json channel_w_external/external_rag_*`, MCP 도구 추가 → `plugins/mcp_tools/*.py`. 새 종류의 외부 시스템이 MCP 도 REST/JSON 도 아니라면(예: gRPC) `llmwiki/mcp_client.py` 에 `call_tool(name, args)` 를 가진 클라이언트 클래스를 추가하고 `open_source` 에 transport 이름을 등록한다(50줄 내외). 새 프로바이더(예: 사내 전용 API)를 붙일 때만 `llmwiki/providers.py` 에 `BaseLLM`/`BaseEmbedder` 서브클래스를 추가하고 `_make_llm`/`make_embedder` 에 이름을 등록한다(30줄 내외). 새 검색 채널은 `query_engine._retrieve` 의 `lists[...]` 에 리스트를 추가하면 융합·부스트·프로파일에 자동으로 포함된다.
