# RESET — 관리자 초기화 세 가지 (다른 환경으로 옮길 때)

> 대상: 이 폴더를 새 환경에 올리거나, 코퍼스를 통째로 바꾸거나, 시험용으로 쌓인 것을 치우려는 관리자.
> **되돌릴 수 없는 동작이다.** 그래서 이 기능은 *먼저 보여 주고 나중에 지운다.*

## 0. 한 장 요약

| 범위 | 지우는 것 | 지우지 **않는** 것 | 다음에 할 일 |
|---|---|---|---|
| `data` | 색인·빌드 산출물 — 문서·청크·임베딩·그래프·위키 페이지, `data/{requests,reruns,sweeps,graph_profiles,live}` | **`corpus/` 원본** · 설정 파일 전부 · 질의 로그/이력 · 임베딩 캐시 | `build --full` |
| `settings` | 설정 파일을 `setup/*.example.*` 또는 코드 기본값으로 — `config.json` `tuning.json` `presets.json` `query_rules.json` `data/rules.json` `agents.json` `models.json` `pins.json` `mcp_sources.json` `schedule.json` `server.json` `stopwords.json` `prompts/` | **`security.json`(계정·권한) · `docacl.json` · `.env`(키·PAT)** — 옵션으로 켜야 초기화된다 · 색인 · 로그 | `corpus_dirs`·모델 지정 → `models test --live` → **서버 재시작** |
| `logs` | `logs/*` 파일 내용 · `audit.jsonl` · 질의 로그 · 요청 프로파일 · 포렌식 · 에피소드 · trial · 임베딩 실행 기록 · 자가진화 적용 이력 · `data/` 부산물 폴더 | 색인 · 설정 · **자가진화 제안(proposals)** · 로그인 세션 | `logs tail` 로 새 로그 확인 |

```bat
python -m llmwiki reset                      :: 범위 설명
python -m llmwiki reset data                 :: 미리보기 (아무것도 지우지 않는다)
python -m llmwiki reset data --apply         :: 확인 문구를 입력해야 실행
```

Web: **설정 › 시스템 › 초기화** 의 버튼 세 개. 누르면 미리보기 표가 뜨고, 거기서 한 번 더 눌러야 실행되며
그때 확인 문구 + 비밀번호 재입력 모달이 나온다. MCP 에는 두지 않는다 — 붙어 있는 LLM 이 색인이나 설정을
지울 수 있으면 안 된다.

## 1. 왜 만들었나

옮길 때 지워야 할 것이 여기저기 흩어져 있었다. 색인은 `build --reset`, 설정은 `config reset`(그런데 `config.json`
하나뿐), 요청 이력은 `maintenance purge_requests`, 로그 파일은 손으로. 그리고 `data/` 밑의 부산물 폴더는
**어느 명령도 건드리지 않아** 옮긴 환경에 그대로 따라갔다. 이 저장소에서 실제로 잰 값:

```
data/requests      562개 · 60.6 MB   (요청 프로파일 — 지운 색인의 청크 id 를 가리킨다)
data/reruns         50개 ·  4.8 MB
logs/                9개 · 106.8 MB
```

합쳐서 **170 MB 넘는 앞 환경의 흔적**이 폴더를 복사할 때마다 따라갔다.

## 2. 설계 원칙

1. **미리보기가 먼저다.** 모든 범위가 `preview()` 로 "무엇을 얼마나" 를 먼저 돌려준다. CLI 기본이 미리보기이고
   `--apply` 가 있어야 실행된다. 지우는 명령이 기본으로 지워 버리면 안 되기 때문이다.
2. **되돌릴 수 있게.** `data` 는 지우기 전에 자동 스냅샷을 만든다 — `snapshot list` 로 확인, `snapshot restore` 로 복원.
3. **스스로 잠기지 않게.** `security.json` 과 `.env` 는 **기본으로 건드리지 않는다.** 원격에서 설정을 초기화하다
   계정이 사라지면 다시 들어갈 길이 없고, `.env` 의 키는 복구할 수 없다.
4. **코퍼스 원본은 절대 지우지 않는다.** `corpus/` 는 사람이 넣은 자료다. 색인만 지운다.
5. **로그 파일은 지우지 않고 비운다.** Windows 에서 돌고 있는 서버가 열어 둔 파일을 지우면 그 뒤 모든 로그가
   조용히 사라진다.

## 3. 옵션

| 범위 | 옵션 | 기본 | 뜻 |
|---|---|---|---|
| data | `--no-snapshot` | 끔 | 스냅샷 없이 — 되돌릴 수 없게 된다. 권장하지 않는다 |
| data | `--purge-wiki-notes` | 끔 | 사람이 쓴 위키 편집 노트까지 (정정·보강 메모가 사라진다) |
| data | `--clear-embed-cache` | 끔 | 임베딩 캐시까지. **임베더를 바꿀 때만** — 지우면 다음 빌드에서 전부 다시 임베딩한다 |
| settings | `--include-security` | 끔 | `security.json`·`docacl.json` 도 (계정·권한·문서 접근 제어가 초기화) |
| settings | `--include-env` | 끔 | `.env` 삭제 (API 키·PAT — 복구 불가) |
| logs | `--include-proposals` | 끔 | 자가진화 제안도 (사람이 검토할 후보) |
| logs | `--include-sessions` | 끔 | 로그인 세션도 (모두 다시 로그인) |

## 4. 옮기기 절차에서의 자리

새 환경으로 폴더를 복사한 뒤, 앞 환경의 것을 털어내는 순서:

```bat
:: 1) 이력과 로그부터 (가장 안전하고 가장 크다)
python -m llmwiki reset logs --apply

:: 2) 코퍼스가 바뀐다면 색인도
python -m llmwiki reset data --apply

:: 3) 설정을 새로 잡겠다면 (계정은 유지된다)
python -m llmwiki reset settings --apply
::    → config.json 의 corpus_dirs·모델을 이 환경에 맞게 고치고
python -m llmwiki models test --live
::    → 서버 재시작

:: 4) 다시 세운다
python -m llmwiki corpus lint
python -m llmwiki build --full --trace
python -m llmwiki build verify
```

무엇을 가져가고 무엇을 두고 갈지의 전체 표는 [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) §복사 대상에 있다.

## 5. 등급과 감사

| 동작 | 등급 | 누가 |
|---|---|---|
| `reset <범위>` (미리보기) · `GET /api/reset?scope=` | read | 로그인한 사람 |
| `reset <범위> --apply` · `POST /api/reset` | **destructive** | admin — 확인 문구 + 비밀번호 재입력 |

실행은 `logs/llmwiki.log` 에 `reset <범위> by <사람> (<옵션>)` 으로 남고, Web 경로는 `logs/audit.jsonl` 에도 남는다.
등급 표 전체는 [SECURITY.md](SECURITY.md) §2.3.

## 6. 문제 해결

| 증상 | 원인 | 확인 |
|---|---|---|
| 초기화했는데 서버가 옛 설정으로 돈다 | 설정 파일은 되돌렸지만 **파이썬 모듈은 다시 읽히지 않는다** | 서버 재시작 (`config reload` 로는 부족하다) |
| `data` 초기화 뒤 질의가 근거를 못 찾는다 | 색인이 비었다 — 다시 빌드해야 한다 | `build --full` → `build verify` |
| 잘못 지웠다 | `data` 범위는 스냅샷이 있다 | `snapshot list` → `snapshot restore <이름>` |
| 로그를 비웠는데 파일이 다시 커진다 | 정상 — 돌고 있는 서버가 계속 쓴다 | 총량은 `log_total_max_mb` 로 제한 ([LOG_QUOTA.md](LOG_QUOTA.md)) |
| `settings` 초기화 뒤 로그인이 안 된다 | `--include-security` 를 켰다 | `setup/security.example.json` 의 기본 계정으로 로그인 후 `users add` |

## 7. 검증

```bat
python -m unittest tests.test_reset          :: 29건 — '지우지 않아야 할 것' 위주
python -m llmwiki reset data                 :: 미리보기가 실제 환경에서 무엇을 보고하는지
python tools/verify/verify_surface_align.py
```

**테스트를 쓸 때 주의** — 로그 폴더는 `Settings` 가 아니라 프로세스 전역(`config.path_for("logs_dir")`)이다.
`logs` 범위를 테스트하려면 `LLMWIKI_LOGS_DIR_PATH` 를 임시 폴더로 바꿔야 한다. 그렇게 하지 않으면
**프로젝트의 진짜 로그와 `audit.jsonl` 을 비운다** (2026-09-19 에 실제로 그렇게 됐다).

## 8. 구현 파일

| 파일 | 역할 |
|---|---|
| `llmwiki/reset.py` | `SCOPES` · `preview` · `run` · `format_preview` · 범위별 목록 상수 |
| `llmwiki/pipeline.py` | `reset_index()` — `data` 범위가 부르는 색인 비우기 + 자동 스냅샷 |
| `llmwiki/auth.py` | 등급 (`classify_cli` 의 `reset`, `classify_api` 의 `/api/reset`) |
| `llmwiki/web/static/js/observability.js` | Web 설정 › 시스템 › 초기화 |
| `tests/test_reset.py` | 회귀 테스트 |
