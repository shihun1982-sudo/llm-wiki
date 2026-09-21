# 로그 총량 제한 (2026-09-18, 요청 11) — 초안

`logs/` 폴더 전체(llmwiki/error/build/query 로그와 그 백업 `*.log.N` + `audit.jsonl`(+백업) + `analysis/` 리포트)의 합계가
`log_total_max_mb` 를 넘으면 `log_limit_action` 에 따라 **warn | prune | stop** 으로 동작한다. `audit.jsonl` 은 크기 로테이션,
`logs/analysis` 는 개수 상한을 갖는다. `data/requests` · `data/reruns` 는 기존 `requests_keep_days` · `rerun_keep` 이 그대로 담당한다(health 표에 함께 표시).

## 1. 설정 키 (config.json — `setup/config.example.json` 에 기본값 수록)

| 키 | 기본 | 의미 |
|---|---|---|
| `log_total_max_mb` | 500 | `logs/` 총량 상한(MB). 하위 폴더 포함 합계. **0 = 제한 없음** |
| `log_limit_action` | `warn` | 초과 시 동작: `warn`(error.log 경고 + health 경고 + Web 상태 필드) · `prune`(오래된 파일 삭제 → 상한의 80% 아래로) · `stop`(error.log 만 남기고 다른 로그 기록 중단, 80% 아래로 내려가면 자동 재개) |
| `log_check_interval_s` | 60 | 총량을 다시 재는 최소 간격(초). 로그가 남을 때 이 간격이 지났는지만 확인하므로 평소 부하는 0 에 가깝다. 0 = 매 레코드(테스트용) |
| `audit_max_mb` | 20 | `logs/audit.jsonl` 파일당 최대 MB. 초과 시 `audit.jsonl.1`, `.2` … 로 로테이션 |
| `audit_backups` | 5 | audit 로테이션 보관 개수. 0 = 초과 시 비운다 |
| `analysis_keep` | 200 | `logs/analysis` 리포트 보관 개수(`req_<id>.md` + `.json` 한 쌍 = 1건). 새 리포트를 쓸 때 오래된 것부터 지움. 0 = 무제한 |

기존 키(`log_level` · `log_max_mb` · `log_backups` · `log_console`)는 그대로. 모두 `LLMWIKI_<KEY>` 환경변수로 덮어쓸 수 있고 `config show --effective` 에 나온다.
설정 변경은 서버 재시작 없이 `config reload`(Pipeline 이 `setup_from_settings` 를 다시 부름)로 반영된다 — 이제 `log_max_mb`/`log_backups`/`log_console` 이 바뀌어도 핸들러를 다시 만든다(이전에는 폴더·레벨이 같으면 무시했다).

## 2. 동작

- **점검 시점**: 파일 핸들러마다 붙은 `_QuotaFilter` 가 레코드가 올 때 `time.time() - last_check >= log_check_interval_s` 인지만 본다. 지났으면 (비재진입 락을 잡고) `os.walk(logs/)` 로 합계를 잰다. 로거가 아니라 핸들러에 붙인 이유: 로거 필터는 자식 로거(`llmwiki.pipeline` …)의 레코드에는 적용되지 않기 때문. 점검 중에 낸 경고 로그는 락 획득에 실패해 재귀 점검을 타지 않는다.
- **warn**: 넘으면 `error.log` 에 `{"msg":"log quota exceeded","data":{total_mb,limit_mb,action,stopped,hint}}` 한 줄(점검 주기당 1회) + `quota_status()["over"]=True`. 지우지도 멈추지도 않는다.
- **prune**: 넘으면 다음 순서로 지워 **상한의 80%** 이하가 될 때까지: ① 로테이션 백업 `*.log.N`(오래된 것부터) ② `analysis/` 리포트(오래된 것부터) ③ `audit.jsonl.N`(오래된 것부터). 현재 쓰는 파일(`llmwiki.log` · `error.log` · `build.log` · `query.log` · `audit.jsonl` · `schedule_history` 등)은 건드리지 않는다. 지우는 동안 모든 핸들러 락을 잡아 `RotatingFileHandler` 의 rollover(백업 이름 바꾸기)와 겹치지 않게 한다. 지운 목록은 `error.log` 에 `log quota prune` 한 줄과 `quota_status()["pruned"]`(최근 50개)에 남는다. 백업을 다 지워도 80% 아래로 못 내려가면(현재 파일 자체가 큰 경우) `over` 로 남고 warn 과 같이 경고한다.
- **stop**: 넘으면 `stopped=True` — `llmwiki.log` · `build.log` · `query.log` 핸들러가 모든 레코드를 버린다(**WARNING 도**). `error.log` 는 계속 WARNING↑ 를 받는다(콘솔 핸들러도 디스크를 안 쓰므로 영향 없음). 이후 점검에서 총량이 80% 이하가 되면 `stopped=False` 로 재개하고 `error.log` 에 `log quota resumed` 를 남긴다. 운영자가 `logs/` 의 오래된 백업을 지우거나 `log_limit_action=prune` 으로 바꾸면(설정만 바뀌면 핸들러 재구성 없이 즉시 반영, 다음 레코드에서 재점검) 풀린다.
- **audit.jsonl**: `write_audit` 이 (락 안에서) 파일 크기 + 새 줄 > `audit_max_mb` 이면 `audit.jsonl → .1 → .2 …`(`audit_backups` 개) 로 이름을 바꾼 뒤 쓴다. `audit_tail`(Web 감사 뷰)은 현재 파일만 읽는다.
- **analysis_keep**: `analysis.save_report(rep, focus, keep=s.analysis_keep)` 이 리포트를 쓴 뒤 `prune_reports(keep)` 로 오래된 리포트(mtime → id 순)를 지운다. `analyze <id>` · analysis_mode 질의 양쪽 모두.
- **핸들러가 없는 프로세스**(예: 로깅을 구성하지 않은 스크립트)에서 `check_quota(dir_hint=…)` 는 재기만 하고 action 은 적용하지 않는다.

## 3. 표면

| 표면 | 위치 | 내용 |
|---|---|---|
| CLI | `python -m llmwiki logs status [--json]` | 지금 다시 재고(action 도 적용) 총량/상한/%/동작/점검 시각/지운 목록/조치 안내. JSON 은 `quota_status()` 그대로: `dir, total_mb, limit_mb, pct, low_water_mb, action, interval_s, over, stopped, files, last_check, last_check_t, last_event, pruned, enabled` |
| health | `python -m llmwiki health` · `/api/health` · 빌드 전 자동 | 항목 `log_quota`(warn 등급): `logs 12.3/500 MB (2.5%) action=warn ok \| requests_keep_days=90 rerun_keep=50 analysis_keep=200`. 초과·stopped 면 △ + 조치 `logs status 로 확인 · log_total_max_mb 를 올리거나 log_limit_action=prune` |
| Web | `GET /api/logs/files` | 기존 `{dir, files}` 에 `quota`(= `logs status --json`) 추가 — Observability › 로그 화면에 총량·상한·상태를 붙일 데이터 |
| Web | `GET /api/status` | `log_quota: {over, stopped, total_mb, limit_mb, pct, action, enabled}` — 헤더 배너용(`log_check_interval_s` 가 지났을 때만 다시 잰다). JS 는 아직 손대지 않았다 |
| error.log | `logs tail --file error` | `log quota exceeded` · `log quota prune` · `log quota resumed` (logger `llmwiki.logs`) |

## 4. 확인

```powershell
python -m unittest tests.test_log_quota -v      # warn / prune / stop / audit 로테이션 / analysis_keep / 재구성 조건 (7건)
python -m unittest tests.test_phase0 -v         # 기존 로깅·run_id 연결
python -m llmwiki logs status                   # 현재 총량·상한·동작
python -m llmwiki logs status --json
python -m llmwiki health --quick | Select-String log_quota
python -m llmwiki config show --effective | Select-String "log_total_max_mb|log_limit_action|audit_|analysis_keep"
# 동작을 눈으로 보려면: config.json log_total_max_mb=1, log_limit_action=prune, log_check_interval_s=0 로 두고 logs/ 에 큰 *.log.1 을 만든 뒤 질의 → logs status 의 pruned
```

문제 해결
- `logs status` 가 `STOPPED` — `log_limit_action=stop` 상태에서 상한 초과. `logs/` 의 `*.log.N` · `analysis/` 오래된 것을 지우거나 `log_total_max_mb` 를 올리거나 `prune` 으로 바꾸고 `config reload`.
- `prune` 인데 계속 `OVER` — 지울 수 있는 백업·리포트가 없고 현재 파일(`llmwiki.log` 등)만으로 상한을 넘는 경우. `log_max_mb`×(`log_backups`+1)×4 + `audit_max_mb`×(`audit_backups`+1) 이 `log_total_max_mb` 보다 큰지 확인(기본값: 10×11×4 + 20×6 = 560 MB > 500 이므로 백업이 가득 차면 prune 이 오래된 백업을 지워 상한 아래를 유지한다. 현재 파일 4개 + audit.jsonl 만으로는 60 MB 라 항상 지울 여지가 있다).
- 점검이 안 도는 것 같다 — 점검은 로그가 남을 때만 일어난다. 유휴 서버는 `/api/status` 폴링(간격 경과 시) 또는 `logs status` 가 재준다.

## 5. BRINGUP_GUIDE §3.2 에 붙일 행

| 영역 | 파일 | 키(기본) | 예시 | 확인 |
|---|---|---|---|---|
| **로그 총량** | `config.json` | `log_total_max_mb`(500, 0=무제한) · `log_limit_action`(warn \| prune \| stop) · `log_check_interval_s`(60) | `setup/config.example.json` | `logs status`, `health` 의 `log_quota`, `/api/status.log_quota`, `/api/logs/files.quota` |
| **감사 로그 로테이션** | `config.json` | `audit_max_mb`(20) · `audit_backups`(5) | `setup/config.example.json` | `logs files` 에 `audit.jsonl.1 …` |
| **분석 리포트 보관** | `config.json` | `analysis_keep`(200, 0=무제한) | `setup/config.example.json` | `logs/analysis` 파일 수, `health` 의 `log_quota` 행 끝 |

README §0 문서 색인·기능 요약, `docs/CLI_FLOWS.md` 의 `logs` 절(`logs status` 추가), `docs/ANALYSIS_MODE.md`(analysis_keep), `docs/SECURITY.md`(audit 로테이션) 에 한 줄씩 링크/언급을 추가할 것.

## 6. 구현 메모 (설계 근거)

- **왜 핸들러 필터인가**: 별도 스레드/타이머 없이 표준 라이브러리만으로, 로그가 실제로 쌓일 때만 점검하기 위해. 타이머 스레드는 CLI 단발 실행에서 프로세스 종료를 붙잡을 수 있고, 유휴 서버에서 불필요하게 돈다.
- **왜 80%**: 상한 바로 아래에서 넘었다/안 넘었다를 반복(경고 폭주, stop 의 잠금/해제 진동)하지 않도록 히스테리시스. 상수 `_LOW_WATER` (필요하면 설정화 가능).
- **왜 stop 에서 WARNING 도 버리나**: `llmwiki.log` 에 WARNING 을 남기면 stop 상태에서도 그 파일이 자라기 때문. 경고는 `error.log` 에 반드시 남으므로 정보 손실은 없다.
- **audit 은 `RotatingFileHandler` 대신 수동 로테이션**: `write_audit` 은 Web 과 CLI(다른 프로세스)가 같은 파일에 append 하므로 logging 핸들러를 프로세스마다 열어 두는 것보다 "쓸 때 열고 닫는" 기존 방식에 크기 검사만 얹는 편이 안전하다(같은 규칙: `.1 → .2`, `backups` 개).
- **대안 검토**: (1) 파일별 `log_max_mb`·`log_backups` 만으로 제한 — audit/analysis 가 빠지고 폴더 합계를 보장하지 못해 기각. (2) `stop` 대신 레벨 상향 — INFO 만 막고 WARNING 이 계속 쌓이면 상한을 넘은 상태가 유지되므로 기각.
