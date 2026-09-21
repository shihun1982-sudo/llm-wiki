# REQUEST HISTORY — 지난 요청 목록과 "그때 그 답" 다시 보기

> 대상: 위키를 쓰는 사람(내가 뭘 물어봤더라)과 운영자(누가 무엇을 돌리고 있나).
> 설계 배경: [IMPLEMENTATION_PLAN_0916.md](history/2026-09-16/IMPLEMENTATION_PLAN_0916.md) §1 · 구현 `llmwiki/store.py`, `llmwiki/web/server.py`

## 0. 한 장 요약

| 어디 | 무엇 |
|---|---|
| Ask 탭 › **🕘 내 지난 요청** | 내 요청을 **대기 / 진행 중 / 완료** 한 목록으로. 한 줄을 누르면 **그때의 답변·근거·판정·프로파일**을 그대로 다시 본다 (질의를 다시 돌리지 않는다) |
| Observability › 요청 프로파일 | 서버 전체 요청 (권한이 있으면). 단계별 trace·비교·로그 연결 |
| 저장 | 목록은 SQLite `requests` 테이블, **결과 원본은 파일** `data/requests/<yyyy-mm>/req_<id>.json` |

## 1. 왜 파일로도 남기는가

`requests` 테이블은 `keep_requests`(기본 2000) 행으로 잘린다. 잘리면 사용자에게는
**"그때 그 답이 사라졌다"** 로 보이는데, 이것이 가장 나쁜 실패다. 그래서 결과와 trace 를 DB 밖 파일로도 남긴다.

- DB 는 **목록용**으로 가볍게 유지하고, 상세 조회는 **파일 우선 → 없으면 DB** 로 읽는다.
- 그래서 DB 행이 잘린 뒤에도 `/api/request?id=…` 가 답한다 (`from_archive: true` 로 표시된다).
- 파일 보존 기간은 DB 와 따로 간다 — `requests_keep_days`(기본 90일).

> 검토한 대안: 결과를 DB 에만 두고 `keep_requests` 를 크게 잡는 방법. 색인 DB 가 빠르게 커지고
> (요청당 수십~수백 KB), VACUUM·스냅샷·복사가 모두 느려져서 버렸다.

## 2. 설정 (`config.json`)

| 키 | 기본 | 뜻 |
|---|---|---|
| `keep_requests` | 2000 | `requests` **테이블**에 남기는 행 수 |
| `requests_dir` | `data/requests` | 결과 보관 폴더. `data/…` 로 시작하는 상대 경로는 `data_dir` 아래로 본다(격리 환경도 따라감). **비우면 파일 보관을 하지 않는다** |
| `requests_keep_days` | 90 | 보관 **파일** 보존 기간(일). 0 = 지우지 않음 |

정리:

```bat
python -m llmwiki maintenance prune_requests   :: requests_keep_days 를 넘긴 보관 파일만 삭제 (DB 는 그대로)
python -m llmwiki maintenance purge_requests   :: requests 테이블 전체 삭제 (파괴적 — 확인 문구 필요)
```

`prune_requests` 는 `index` 등급이라 스케줄 작업으로 걸어 둘 수 있다
([SCHEDULER.md](SCHEDULER.md) 의 `maintenance` 액션).

## 3. 누가 낸 요청인가 (권한)

`requests` 테이블에 **`user` 열**이 있다. 예전에는 `origin` 에 `"web kh82.kim"` 처럼 한 문자열로 들어가서
'내 요청만 보기' 를 걸 수 없었다(이름에 공백이 있으면 깨지고 인덱스도 못 걸었다). 기존 DB 는 첫 실행 때
`origin` 에서 이름을 뽑아 채운다.

| 보려는 것 | 필요한 권한 |
|---|---|
| 내 요청 목록·상세 | `read` (로그인한 사람) |
| **모든 사용자의** 요청 | 작업 `requests all` — 기본 `run` 등급(class3) |

조직 기준이 다르면 `security.json` 의 `permissions.ops` 에 `"requests all": "<역할>"` 로 바꾼다
([SECURITY.md](SECURITY.md) §4). 권한이 없으면 서버가 조용히 **내 것만** 돌려주고 화면이 그 사실을 알린다.

## 4. 화면에서

**Ask › 🕘 내 지난 요청**

- 종류(질의·검색·빌드·평가) 필터, 요약 검색, **모든 사용자** 체크박스.
- 위쪽에 **진행 중·대기** 요청이 먼저 나오고(활동 보드와 같은 원천), 그 아래 완료된 요청.
- 완료 행을 누르면 Ask 의 결과 영역이 **그때의 답**으로 다시 그려지고, 위에 안내 띠가 붙는다:
  *"지난 요청 #N 의 결과입니다 … 다시 실행한 것이 아니라 그때 저장된 답을 그대로 보여 줍니다"*.
  거기서 **같은 질의 다시 실행**을 누르면 새로 돈다.
- 질의가 아닌 요청(빌드·평가)은 Observability › 요청 프로파일로 보낸다.

> 근거 문단 **전문**은 보관하지 않는다(요약 `hits_brief` 만). 그래서 다시 본 화면의 근거 칸에는
> "근거 전문은 보관하지 않습니다" 가 뜬다 — 전문이 필요하면 같은 질의를 다시 실행한다.
> 이유: 근거 전문까지 저장하면 요청 하나가 수 MB 가 되고, 코퍼스가 바뀌면 어차피 옛 본문이 된다.

## 5. API

| 경로 | 설명 |
|---|---|
| `GET /api/requests?scope=mine\|all&kind=&limit=&q=` | `{rows, live, scope, me, can_all}`. `live` 는 지금 실행/대기 중인 것 |
| `GET /api/request?id=<n>` | 상세 (결과·trace). DB 에 없으면 보관 파일에서 찾는다 (`from_archive`) |
| `POST /api/maintenance {"action":"prune_requests"}` | 보관 파일 정리 (`index` 등급) |

## 6. 확인

```bat
python -m llmwiki query "테스트 질문" --no-llm-answer
dir data\requests\%date:~0,4%-%date:~5,2%          :: req_<id>.json 이 생겼는지
python -m llmwiki maintenance prune_requests
python tools\verify\verify_web.py                  :: 요청 이력 5항목 포함
```

| 증상 | 원인 |
|---|---|
| 목록이 비어 있다 | 질의할 때 **로그/자가진화 기록** 체크가 꺼져 있으면 기록되지 않는다 |
| 내 요청인데 안 보인다 | 로그인하지 않았거나(게스트는 이름이 없다) 다른 계정으로 낸 요청이다 |
| "모든 사용자" 를 켜도 내 것만 나온다 | `requests all` 권한이 없다 — 화면 아래에 안내가 뜬다 |
| 오래된 요청 상세가 404 | `keep_requests` 로 DB 행이 잘렸고 보관 파일도 `requests_keep_days` 를 넘겨 지워졌다 |
| `data/requests` 가 안 생긴다 | `requests_dir` 이 비어 있다(보관 안 함) |

## 7. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/store.py` | `requests` 의 `user`·`file` 열과 이관, `log_request(archive_dir=…)`, `archive_request` / `read_archived_request` / `prune_request_archive`, `requests(user=, q=)`, `get_request(archive_dir=)` |
| `llmwiki/config.py` | `requests_dir` · `requests_keep_days` · `Settings.requests_archive_dir()` |
| `llmwiki/pipeline.py` `query_engine.py` | 질의 기록 시 보관 폴더 전달, `maintenance prune_requests` |
| `llmwiki/web/server.py` | `GET /api/requests`(scope·live·권한) · `GET /api/request`(보관 파일 폴백·타인 요청 차단) |
| `llmwiki/web/static/js/ask.js` | `renderResult()` 분리(같은 화면을 저장된 결과로도 그린다), 내 지난 요청 목록 |
| `llmwiki/web/static/js/observability.js` | 요청 프로파일 탭이 새 응답 형식과 `user` 열을 처리 |
| `llmwiki/auth.py` | 작업 `requests all` |
