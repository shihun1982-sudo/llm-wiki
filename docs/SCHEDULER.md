# 스케줄러 — 정해진 시각·간격마다 작업 실행 (2026-09-15)

> 서버(`serve`) 안에서 도는 작업 스케줄러. 설정은 [schedule.json](../setup/schedule.example.json) 하나이고, 코드 수정이 필요 없다.
> Web: **Settings › 스케줄** · CLI: `python -m llmwiki schedule …` · 함께 읽기: [CONCURRENCY.md](CONCURRENCY.md) · [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) §8.

---

## 0. 무엇을 할 수 있나

| 하고 싶은 일 | 작업 유형 |
|---|---|
| 30분마다 바뀐 문서만 증분 색인 | `build` |
| 매일 새벽 전체 리빌드 | `build {full:true}` |
| 사내 페이지·문서를 주기적으로 받아 코퍼스에 넣고 색인 | `fetch_url` |
| 다른 시스템(MCP 소스)에서 문서 수집 | `mcp_ingest` |
| 우리가 만든 파이썬 스크립트 실행 | `python` |
| **CLI 의 모든 기능** 실행 (Web 콘솔과 동일) | `cli` |
| 정해진 질문의 답을 미리 만들어 파일로 | `query` |
| 역할 LLM 또는 headless 에이전트(opencode 등)에게 프롬프트 실행 | `llm` · `headless` |
| 자가진화 제안 만들기 / 신뢰도 높은 제안 자동 적용 | `evolve` |
| 메모리 감쇠 · 포렌식 누적 → 제안 | `memory` · `forensic` |
| 자주 묻는 질의 답변 미리 계산 | `precompute` |
| 평가셋 회귀 · 설정 실험 기록 | `eval` · `trial` |
| 색인 스냅샷 · 위키 재생성 · DB 유지보수 · 임베딩 리포트 | `snapshot` · `wiki` · `maintenance` · `embed_report` |
| 웹훅 호출(사내 메신저 알림 등) | `http` |

---

## 1. 파일 구조

```json
{
  "tick_s": 5,
  "tasks": [
    {
      "name": "incremental-build",
      "enabled": true,
      "every": "30m",
      "action": { "type": "build", "full": false },
      "timeout_s": 3600,
      "overlap": "skip"
    }
  ]
}
```

| 필드 | 뜻 |
|---|---|
| `name` | 작업 이름 (고유). 이력·수동 실행·중지에 쓰인다 |
| `enabled` | false 면 등록만 하고 실행하지 않는다 |
| `every` \| `at`+`days` \| `cron` | **셋 중 하나**. 실행 시점 (§2) |
| `action` | 무엇을 할지 (§3) |
| `timeout_s` | 이 시간을 넘으면 자동 중지 (0/생략 = 제한 없음) |
| `overlap` | `skip`(기본) = 이전 실행이 아직 돌면 이번 차례를 건너뛴다 |
| `run_on_start` | `every` 작업에서 서버 시작 직후 한 번 바로 실행 |

파일을 저장하면 서버가 **자동으로 다시 읽는다**(mtime 감시). 잘못 쓴 작업은 목록에 `INVALID` 로 표시되고 나머지는 정상 동작한다.

---

## 2. 실행 시점

### `every` — 간격
`"30s"` · `"10m"` · `"2h"` · `"1d"` (최소 5초). 마지막 실행 시각 기준.

### `at` + `days` — 매일/요일 시각
```json
{"at": "08:30", "days": ["mon", "tue", "wed", "thu", "fri"]}
```
`days` 를 비우면 매일. 시각은 `config.json` 의 `timezone`(기본 Asia/Seoul) 기준.

### `cron` — 5필드
```
분 시 일 월 요일        예) "0 3 * * *"     매일 03:00
                        "*/15 * * * *"     15분마다
                        "0 22 * * fri"     금요일 22:00
                        "30 4 1 * *"       매월 1일 04:30
```
`*` · `*/n` · `a-b` · `a,b` · `a-b/n` 을 지원하고 요일은 숫자(0=일) 또는 `mon`~`sun`.

확인: `python -m llmwiki schedule list` 가 각 작업의 **다음 실행 시각**을 보여 준다. 표현이 틀리면 `schedule validate`.

---

## 3. 작업 유형 (action.type)

### build — 색인
```json
{"type": "build", "full": false}
{"type": "build", "full": true}                     // 전체 리빌드 (배타 실행)
{"type": "build", "channel": "fts"}                 // 채널만
{"type": "build", "channels": ["fts", "vector"]}
```
증분은 질의와 함께 돌고(정책 `reads_during_build`), 전체 리빌드는 질의가 대기한다 → **사용자가 적은 시간**에 두세요.

### fetch_url — URL 을 코퍼스에 저장
```json
{"type": "fetch_url", "urls": ["https://intranet/notice.html"], "dest": "corpus/fetched",
 "headers": {}, "timeout_s": 60, "build_after": true}
```
내용이 같으면 건너뛴다(sha1). `dest` 는 `config.json` 의 `corpus_dirs` 안이어야 색인된다(아니면 경고).
받은 파일을 문서 계약 형식으로 바꾸려면 [tools/corpus_ingest.py](../tools/corpus_ingest.py) 를 `python` 작업으로 이어서 돌린다.

### python — 스크립트 실행
```json
{"type": "python", "script": "tools/my_job.py", "args": ["--x"], "cwd": "", "env": {}, "timeout_s": 600}
```
현재 인터프리터로 **별도 프로세스** 실행. 종료 코드 0 이 아니면 실패로 기록되고 stdout/stderr 끝부분이 이력에 남는다.

### cli — CLI 의 모든 기능
```json
{"type": "cli", "argv": ["build", "verify", "--fix"]}
{"type": "cli", "argv": ["evolve", "apply", "12"]}
{"type": "cli", "argv": ["memory", "consolidate"]}
{"type": "cli", "argv": ["trial", "run", "--name", "nightly", "--preset", "quality"]}
```
Web 콘솔과 같은 경로(`run_captured`)라 **CLI 로 되는 것은 전부 된다**. 파괴적 명령은 `--yes` 를 붙인다.

### query · llm · headless — 결과를 파일로
```json
{"type": "query", "q": "지난주 리뷰한 CL 요약", "preset": "quality", "out": "logs/schedule/digest.md"}
{"type": "llm", "role": "answer", "prompt_file": "prompts/weekly_review.md", "out": "logs/schedule/review.md"}
{"type": "headless", "agent": "opencode", "model": "", "prompt_file": "prompts/weekly_review.md", "files": [], "out": "..."}
```
`headless` 는 `agents.json` 의 에이전트를 실행하므로 **사내 skill/agent 호출**을 그대로 붙일 수 있다.

### evolve — 자가진화
```json
{"type": "evolve", "op": "review"}          // LLM 이 최근 질의·포렌식을 보고 개선 제안 생성 (적용은 사람이)
{"type": "evolve", "op": "consolidate"}     // 누적 포렌식 → 제안
{"type": "evolve", "op": "auto_apply", "min_confidence": 0.95, "max_apply": 3,
 "kinds": ["pin", "query_rule", "synonym"], "evaluate": false}
{"type": "evolve", "op": "status"}
```
`auto_apply` 는 **사람 확인 없이** 색인/설정을 바꾸므로 신뢰도 하한·종류·건수를 모두 제한한다. 배타 실행이며 감사 이력에 남는다.
안전한 시작점: `review` 만 자동, 적용은 Web ‘제안 (HITL)’ 탭에서 사람이.

### memory · forensic · precompute · eval · trial · snapshot · wiki · embed_report
```json
{"type": "memory", "op": "decay"}                  // 오래된 신호 감쇠 (status | consolidate)
{"type": "forensic", "op": "summary", "out": "logs/schedule/forensic.md"}
{"type": "precompute", "op": "run", "from_log": 30}
{"type": "eval", "k": 5, "out": "logs/schedule/eval.md"}
{"type": "trial", "name": "nightly", "preset": "quality", "k": 5}
{"type": "snapshot", "op": "create", "tag": "auto:nightly", "keep": 5}
{"type": "wiki", "min_degree": 1}
{"type": "embed_report", "out": "logs/schedule/embed.md"}
```

### maintenance · http
```json
{"type": "maintenance", "action": "wal_checkpoint"}   // vacuum | fts_optimize | clear_cache | warm_cache | refresh_doc_refs
{"type": "http", "url": "https://hooks.example/notify", "method": "POST", "body": {"text": "빌드 완료"}}
```

---

## 4. 동시성과의 관계

스케줄 작업도 [요청 관리자](CONCURRENCY.md)의 티켓을 받는다. 그래서

- **진행 중 작업 목록에 보이고**(누가 봐도 “지금 서버가 야간 빌드 중”임을 안다), **중지할 수 있다**.
- 사용자 질의와의 우선순위가 정책대로 정리된다(증분 빌드는 질의와 공존, 전체 리빌드는 배타).
- `timeout_s` 를 넘으면 watchdog 이 자동으로 중지한다.

유형별 가중치: `build(full)`·`maintenance`·`snapshot`·`evolve auto_apply` → 배타 / `build(증분)`·`fetch_url`·`memory`·`wiki`·`precompute` → soft / 나머지 → read.

---

## 5. 운영

### Web (Settings › 스케줄)
목록(다음 실행·마지막 결과·소요), `+ 작업 추가` 폼(시점 종류 선택 · 동작 유형 선택 시 예시 JSON 자동 채움 · 설명 표시),
▶ 지금 실행 · 편집 · 켜기/끄기 · 삭제, 아래에 **실행 이력**(성공/실패/중지·소요·결과 요약).

### CLI
```bat
python -m llmwiki schedule list                   :: 목록 + 다음 실행 시각 + 마지막 결과
python -m llmwiki schedule show <name>
python -m llmwiki schedule validate               :: 설정 문법 검사 (CI 에 넣기 좋다)
python -m llmwiki schedule run <name>             :: 서버 없이 지금 한 번 실행 (OS 스케줄러용)
python -m llmwiki schedule trigger <name>         :: 실행 중인 서버에 지금 실행을 요청
python -m llmwiki schedule add --task "{...}"     :: JSON 으로 추가
python -m llmwiki schedule enable|disable|remove <name>
python -m llmwiki schedule history -n 30
```

### 이력과 상태
- 실행 이력: `logs/schedule.jsonl` (시각·작업·결과·소요·오류·로그 마지막 20줄)
- 마지막/다음 실행: `data/schedule_state.json`
- 서버 로그: `logs/build.log` 의 `kind=watch` 레코드

### 서버 없이 쓰기 (OS 스케줄러)
스케줄러는 `serve` 안에서만 돈다. 서버를 상시 띄우지 않는 환경이면 Windows 작업 스케줄러/cron 에서
`python -m llmwiki schedule run <name>` 을 부르면 같은 동작을 한다([setup/schedule_build.ps1](../setup/schedule_build.ps1) 참고).

---

## 6. 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| 목록에 `INVALID` | 시점/동작 문법 오류. `schedule validate` 로 메시지 확인 |
| `스케줄러가 실행 중이 아닙니다` | `serve` 가 아닌 곳에서 `run` 을 요청. `schedule run` 은 서버 없이도 동작 |
| 계속 `skipped` | 이전 실행이 아직 진행 중(`overlap: skip`). 주기를 늘리거나 `timeout_s` 를 준다 |
| 야간 빌드 때문에 아침 질의가 느림 | 전체 리빌드가 아직 진행 중. `schedule list` 의 마지막 소요를 보고 시각을 앞당긴다 |
| fetch_url 이 색인되지 않음 | `dest` 가 `corpus_dirs` 밖. 결과의 `warning` 확인 |
| evolve auto_apply 가 아무것도 안 함 | 제안 신뢰도가 `min_confidence` 미만이거나 `kinds` 에 없음. `evolve status` 로 확인 |
| 시각이 한 시간 밀림 | `config.json` 의 `timezone` 확인 (기본 Asia/Seoul) |

---

## 7. 검증

`python -m unittest tests.test_concurrency_0915.SchedulerTest` — cron/every 파싱과 다음 실행 시각, python·query·build·fetch_url·maintenance·
evolve·memory·precompute·eval·trial·snapshot·wiki·forensic·embed_report 실행, 이력 기록, 파일 편집 자동 재적재, 잘못된 작업 보고.
Web 경로는 `tools/verify/verify_web.py`(스케줄 추가·실행·이력·삭제·잘못된 작업 400), CLI 는 `tools/verify/verify_cli.py`.
