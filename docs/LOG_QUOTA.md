# LOG_QUOTA — `logs/` 총량 제한 · 감사 로그 로테이션 · 분석 리포트 보관 수

> 설정: `config.json` 의 `log_total_max_mb` · `log_limit_action` · `log_check_interval_s` · `audit_max_mb` · `audit_backups` · `analysis_keep`
> CLI: `python -m llmwiki logs status [--json]` · health 항목 `log_quota` · API: `GET /api/logs/files` 의 `quota`, `GET /api/status` 의 `log_quota`
> 설계 근거: [IMPLEMENTATION_PLAN_0918.md §2.11](history/2026-09-18/IMPLEMENTATION_PLAN_0918.md) · 코드: `llmwiki/logging_setup.py` · 검증: `tests/test_log_quota.py`

## 0. 한 장 요약

```
logs/  = llmwiki.log · error.log · build.log · query.log (+ *.log.N 백업)  +  audit.jsonl (+ .N)  +  analysis/req_<id>.md|.json  + 기타
       └─ 합계 > log_total_max_mb (기본 500 MB)  → log_limit_action:  warn  (error.log 경고 + health △ + API 필드)
                                                                      prune (오래된 *.log.N → analysis/ → audit.jsonl.N 순으로 지워 80% 아래로)
                                                                      stop  (error.log 만 남기고 다른 로그 기록 중단, 80% 아래면 자동 재개)
점검은 로그 레코드가 남을 때 log_check_interval_s(60초) 마다 한 번만 폴더를 훑는다 (_QuotaFilter). 상태: logs status / quota_status().
audit.jsonl 은 audit_max_mb(20) 초과 시 .1 … audit_backups(5) 로 로테이션. logs/analysis 는 analysis_keep(200) 건만 보관.
data/requests · data/reruns 는 기존 requests_keep_days · rerun_keep 이 담당 (health 의 log_quota 행에 함께 표시).
```

| 어디서 | 무엇 |
|---|---|
| CLI | `logs status` — 지금 다시 재고(제한 동작도 적용) 총량/상한/%/동작/점검 시각/지운 목록/조치 안내. `--json` 은 `quota_status()` 그대로 |
| health | `python -m llmwiki health` · `/api/health` · 빌드 전 자동 — 항목 `log_quota`(warn 등급) |
| Web API | `GET /api/logs/files` → `{dir, files, quota}` · `GET /api/status` → `log_quota: {over, stopped, total_mb, limit_mb, pct, action, enabled}` |
| Web 화면 | **미구현**(2026-09-18): Observability › 로그는 `dir`·`files` 만 그리고 `quota` 는 쓰지 않는다. 헤더 배너도 없다. 데이터는 위 두 API 에 이미 있다 |
| error.log | `logs tail --file error` — `log quota exceeded` · `log quota prune` · `log quota resumed` (logger `llmwiki.logs`) |

## 1. 왜 이렇게 만들었나 (설계 근거)

- **파일별 `log_max_mb`·`log_backups` 만으로는 부족하다.** 그 둘은 로그 4종에만 걸리고 `audit.jsonl`·`logs/analysis/` 는 무제한이었다. 폴더 합계를 보장하려면 폴더 단위 상한이 필요하다 → `log_total_max_mb`.
- **핸들러 필터로 점검한다(타이머 스레드 아님).** 표준 라이브러리만으로, 로그가 실제로 쌓일 때만 점검한다. 타이머 스레드는 CLI 단발 실행에서 프로세스 종료를 붙잡고 유휴 서버에서 헛돈다. 로거가 아니라 **핸들러**에 붙인 이유: 로거 필터는 자식 로거(`llmwiki.pipeline` …)의 레코드에 적용되지 않는다.
- **80% 히스테리시스(`_LOW_WATER = 0.8`, 상수).** 상한 바로 아래에서 넘었다/안 넘었다를 반복하면 경고가 폭주하고 `stop` 이 잠금/해제로 진동한다. prune 목표와 stop 해제 기준을 모두 80% 로 둔다.
- **`stop` 은 WARNING 도 버린다.** `llmwiki.log` 에 WARNING 을 남기면 stop 상태에서도 그 파일이 자란다. 경고는 `error.log`(WARNING↑, stoppable=False)에 반드시 남으므로 정보 손실은 없다. 대안 "레벨 상향" 은 INFO 만 막고 WARNING 이 계속 쌓여 초과 상태가 유지되므로 기각.
- **audit 은 `RotatingFileHandler` 가 아니라 수동 로테이션.** 계획 §2.11 은 RotatingFileHandler 를 적었지만, `write_audit` 은 Web 과 CLI(다른 프로세스)가 같은 파일에 append 하므로 프로세스마다 핸들러를 열어 두는 것보다 "쓸 때 열고 닫는" 기존 방식에 크기 검사(`_rotate_audit`, 같은 `.1→.2` 규칙)만 얹는 편이 안전하다.

## 2. 설정 키 (`config.json` — `setup/config.example.json` 에 같은 기본값 수록)

| 키 | 기본 | 뜻 |
|---|---|---|
| `log_total_max_mb` | `500` | `logs/` 총량 상한(MB). 하위 폴더 포함 합계. **`0` = 제한 없음**(재기만 한다) |
| `log_limit_action` | `"warn"` | 초과 시 동작 `warn` \| `prune` \| `stop` (모르는 값은 warn) |
| `log_check_interval_s` | `60` | 총량을 다시 재는 최소 간격(초). `0` = 매 레코드(테스트용) |
| `audit_max_mb` | `20` | `logs/audit.jsonl` 파일당 최대 MB. 초과 시 `audit.jsonl.1`, `.2` … 로 로테이션. `0` 이하 = 로테이션 없음 |
| `audit_backups` | `5` | audit 로테이션 보관 개수. `0` = 초과 시 파일을 비운다 |
| `analysis_keep` | `200` | `logs/analysis` 리포트(`req_<id>.md` + `.json` 한 쌍 = 1건) 보관 개수. 새 리포트를 쓸 때 오래된 것부터 지움. `0` = 무제한 |

기존 키 `log_level`(INFO) · `log_max_mb`(10) · `log_backups`(10) · `log_console`(false) 는 그대로. 모두 `LLMWIKI_<KEY 대문자>` 환경변수로 덮어쓸 수 있고
`config show --effective` 에 출처가 나온다. 변경 반영: `config reload`(Web 설정 › 다시 읽기) → `Pipeline.reload()` 가 `setup_from_settings()` 를 다시 부른다.
총량 설정(`configure_quota`)과 audit 설정(`auth.configure_audit`)은 핸들러 재구성 없이 즉시 바뀌고, `log_max_mb`/`log_backups`/`log_console`/`log_level`/폴더가 바뀌면 핸들러를 다시 만든다.

## 3. 동작

- **점검 시점** — 파일 핸들러 4개마다 `_QuotaFilter` 가 붙어 있다. 레코드가 올 때 `now - last_check >= interval_s` 이면 비재진입 락(`_QUOTA_LOCK`, `acquire(blocking=False)`)을 잡고 `os.walk(logs/)` 로 합계를 잰다. 점검 중에 낸 경고 로그는 락 획득에 실패해 재귀하지 않는다. 다른 스레드가 점검 중이면 건너뛴다.
- **warn** — 넘으면 `error.log` 에 `{"msg":"log quota exceeded","data":{total_mb,limit_mb,action,stopped,hint}}` 한 줄(점검 주기당 1회) + `over=True`. 지우지도 멈추지도 않는다.
- **prune** — 넘으면 **상한의 80%** 이하가 될 때까지 다음 순서로 지운다: ① 최상위의 로테이션 백업 `*.log.N`(mtime 오래된 것부터) ② `analysis/` 아래 모든 파일(오래된 것부터) ③ `audit.jsonl.N`(오래된 것부터). 현재 쓰는 파일(`llmwiki.log` · `error.log` · `build.log` · `query.log` · `audit.jsonl` · 그 밖의 파일)은 건드리지 않는다. 지우는 동안 모든 핸들러 락을 잡아 `RotatingFileHandler` 의 rollover 와 겹치지 않게 한다. 결과는 `error.log` 의 `log quota prune`(removed·freed_mb) 과 `quota_status()["pruned"]`(최근 20개 표시, 내부 50개 보관)에 남는다. 지울 것이 없어 80% 아래로 못 내려가면 `over` 로 남고 warn 과 같은 경고를 낸다.
- **stop** — 넘으면 `stopped=True`: `llmwiki.log` · `build.log` · `query.log` 핸들러가 모든 레코드를 버린다(WARNING 포함). `error.log` 는 계속 WARNING↑ 를 받는다(콘솔 핸들러는 디스크를 쓰지 않아 영향 없음). 이후 점검에서 총량이 80% 이하가 되면 `stopped=False` 로 재개하고 `error.log` 에 `log quota resumed` 를 남긴다. `log_limit_action` 을 다른 값으로 바꾸거나 `log_total_max_mb=0` 으로 두면 즉시 해제된다.
- **audit.jsonl** — `write_audit` 이 락 안에서 `파일 크기 + 새 줄 > audit_max_mb` 이면 `audit.jsonl → .1 → .2 …`(`audit_backups` 개) 로 이름을 바꾼 뒤 쓴다. `audit_tail`(Web 감사 뷰)은 현재 파일만 읽는다.
- **analysis_keep** — `analysis.save_report(rep, focus, keep=s.analysis_keep)` 가 리포트를 쓴 뒤 `prune_reports(keep)` 로 오래된 리포트(mtime → id 순)를 지운다. `analyze <id>` 와 `analysis_mode` 질의 양쪽 모두. `keep<=0` 이면 지우지 않는다.
- **핸들러가 없는 프로세스**(로깅을 구성하지 않은 스크립트)에서 `check_quota(dir_hint=…)` 는 재기만 하고 동작은 적용하지 않는다.
- **`/api/status`** 는 `check_quota(force=False)` — 간격이 지났을 때만 다시 잰다(폴링 부하 방지). `/api/logs/files` 와 `logs status` 와 `health` 는 `force=True` 로 지금 다시 잰다.

## 4. `logs status --json` 필드

`dir, total_mb, limit_mb, pct(제한 없으면 null), low_water_mb, action, interval_s, over, stopped, files, last_check, last_check_t, last_event, pruned[], enabled(limit>0)`.
텍스트 출력은 `log quota: ok | OVER(상한 초과) | STOPPED(error.log 만 기록 중)` 첫 줄 + 총량/동작/점검/지움/조치 줄.

## 5. 검증 명령

```powershell
python -m unittest tests.test_log_quota -v
#   warn · prune(오래된 백업부터 80% 까지) · stop(INFO 차단, error.log 유지) · 핸들러 없는 프로세스는 재기만
#   · audit 로테이션 · analysis_keep · setup_logging 재구성 조건 · (총 8건)
python -m llmwiki logs status
python -m llmwiki logs status --json
python -m llmwiki health --quick | Select-String log_quota
python -m llmwiki config show --effective | Select-String "log_total_max_mb|log_limit_action|log_check_interval_s|audit_|analysis_keep"
Invoke-RestMethod http://127.0.0.1:8765/api/logs/files | Select-Object -ExpandProperty quota      # 서버 실행 중일 때
```

동작을 눈으로 보려면: `config.json` 에 `log_total_max_mb=1`, `log_limit_action=prune`, `log_check_interval_s=0` 을 두고 `logs/` 에 2 MB 짜리 `llmwiki.log.1` 을 만든 뒤
질의 한 번 → `logs status` 의 `지움` 줄과 `error.log` 의 `log quota prune`. 끝나면 값을 되돌리고 `config reload`.

## 6. 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| `logs status` 가 `STOPPED` | `stop` 상태에서 상한 초과. `logs/` 의 `*.log.N` · `analysis/` 오래된 것을 지우거나, `log_total_max_mb` 를 올리거나, `log_limit_action=prune` 으로 바꾸고 `config reload`. 다음 로그 레코드에서 재점검·재개 |
| `prune` 인데 계속 `OVER` | 지울 백업·리포트가 없고 현재 파일만으로 상한을 넘는 경우. 기본값 기준 현재 파일 상한 = 로그 4개×10 MB + audit 20 MB = 60 MB 라 500 MB 에서는 일어나지 않는다. 상한을 아주 작게 잡았다면 `log_max_mb`·`audit_max_mb` 도 함께 줄인다 |
| 점검이 안 도는 것 같다 | 점검은 로그가 남을 때만 일어난다. 유휴 서버는 `/api/status` 폴링(간격 경과 시) 또는 `logs status` 가 재준다 |
| Web 에서 총량이 안 보인다 | 2026-09-18 현재 화면 미구현. `GET /api/logs/files` 의 `quota` 또는 `logs status` 로 확인 |
| `audit.jsonl.1` 이 안 생긴다 | `audit_max_mb` 이하이거나 `0`(로테이션 끔). `logs files` 로 크기 확인 |
| 분석 리포트가 사라졌다 | `analysis_keep` 초과로 정리됨. 오래 보관할 리포트는 다른 폴더로 복사하거나 값을 키운다(`0` = 무제한) |

## 7. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/logging_setup.py` | `_QUOTA`/`_QuotaFilter` · `configure_quota()` · `_maybe_check()`/`_check_locked()` · `_scan()`/`_prune_candidates()`/`_prune()` · `check_quota()`/`quota_status()`/`format_quota()` · `setup_logging(total_max_mb, limit_action, check_interval_s)` · `setup_from_settings()`(audit 설정도 반영) |
| `llmwiki/auth.py` | `_AUDIT_CFG` · `configure_audit()` · `_rotate_audit()` · `write_audit()`(크기 검사 → 로테이션 → append) |
| `llmwiki/analysis.py` | `prune_reports(keep)` · `save_report(rep, focus, keep)` (호출부가 `s.analysis_keep` 전달) |
| `llmwiki/health.py` | 항목 `log_quota`(warn) — `logs 12.3/500 MB (2.5%) action=warn ok \| requests_keep_days=90 rerun_keep=50 analysis_keep=200` |
| `llmwiki/config.py` | `log_total_max_mb` · `log_limit_action` · `log_check_interval_s` · `audit_max_mb` · `audit_backups` · `analysis_keep` + `SETTING_HELP` |
| `llmwiki/cli.py` | `logs status [--json]` (`check_quota(force=True)` → `format_quota`) |
| `llmwiki/web/server.py` | `GET /api/logs/files` 의 `quota` · `GET /api/status` 의 `log_quota` |
| `llmwiki/pipeline.py` | `reload()` 가 `setup_from_settings()` 를 다시 불러 설정 변경을 반영 |
| `tests/test_log_quota.py` | 8건 (임시 `logs/` 폴더, 실제 파일 크기로 warn/prune/stop 재현) |
