# OPS_STATS — 운영 통계: 관리자가 실제로 묻는 것

> 대상: 이 서버를 운영하는 사람. "규모" 가 아니라 **"왜 느린가 · 얼마나 쓰나 · 어디가 커지나"** 에 답한다.
> 한 번에: `python -m llmwiki stats --full` · Web 옵저빌리티 › 시스템 의 **운영 통계** · MCP `wiki_status(full=true)`.
> 실시간 진행 상황은 [CONCURRENCY.md](CONCURRENCY.md)(서버 모니터), 빌드 절차는 [REBUILD_SPEC.md](history/2026-09-17/REBUILD_SPEC.md).

## 0. 왜 만들었나

옵저빌리티 › 시스템 화면은 **색인 규모**(문서·청크·엔티티 수)와 캐시·워처·유지보수 버튼만 보여 줬다.
운영자가 실제로 묻는 것은 다른 쪽이다:

| 묻는 것 | 예전 | 지금 |
|---|---|---|
| 빌드가 왜 느린가 | 마지막 빌드 요약뿐 | 단계별 ms (느린 순) |
| 질의가 얼마나·언제 몰리나 | ✘ | 총량·하루 평균·시간대 막대·창구별 |
| 지연은 얼마나 | ✘ | p50/p95/최대 + **가장 느린 질의 목록** |
| 토큰을 어디에 쓰나 | ✘ | 총량·질의당·호출 수 + **가장 무거운 질의** |
| 근거를 못 찾은 비율 | ✘ | 근거 부족률·👍/👎·포렌식·대기 제안 |
| 누가 쓰나 | ✘ | 사용자별 상위 |
| 디스크가 어디서 커지나 | 로그 총량만 | DB·테이블·`data/` 폴더별 + **경고** |
| 임베딩은 잘 돌았나 | CLI `embed report` 만 | 최근 실행·캐시 적중률·실패 |

필요한 데이터는 **이미 DB 에 다 있었다** — `requests`(단계 trace·ms·토큰), `query_log`(피드백·창구·사용자),
`embed_runs`, `forensics`. 읽지 않고 있었을 뿐이다.

## 1. 쓰는 법

```bat
python -m llmwiki stats --full                       :: 최근 7일, 전부
python -m llmwiki stats --full --days 30             :: 기간을 늘려서
python -m llmwiki stats --full --section storage     :: 한 섹션만 (여러 번 줄 수 있다)
python -m llmwiki stats --full --json                :: 기계용
```

Web: 옵저빌리티 › 시스템 상단의 **운영 통계** — 기간을 고르고 `집계`.
MCP: `wiki_status(full=true, days=7, sections=["latency","tokens"])`.

세 창구가 `llmwiki/opstats.py` **한 곳**을 쓰므로 숫자가 갈리지 않는다.

## 2. 섹션

| 섹션 | 답하는 것 |
|---|---|
| `index` | 색인 규모 · 문서당 청크 · 문서 유형별 · **임베딩 없는 청크**(벡터 채널이 그만큼 못 본다) |
| `build` | 마지막 빌드 시각·모드·소요 · **느린 단계 순위** · 빌드 경고 · 기간 내 빌드 횟수 |
| `queries` | 총량 · 하루 평균 · **시간대 분포** · 창구(web/cli/mcp)별 |
| `latency` | p50/p95/최대/평균 + **가장 느린 질의**(요청 번호로 바로 이동) |
| `tokens` | 입력/출력/총량 · 질의당 · LLM 호출/질의 + **가장 무거운 질의** |
| `quality` | 근거 부족률 · 👍/👎 와 피드백이 달린 비율 · 포렌식 판정 분포 · 대기 중 제안 |
| `users` | 사용자별 질의 수 (상위) |
| `storage` | DB·로그·위키 크기 · 큰 테이블 · `data/` 폴더별 + **정리 힌트** |
| `embed` | 최근 실행 · 캐시 적중률 · 실패 건수 |
| `trend` | **일/주/월 추세** — 구간별 질의량·지연(p50/p95)·질의당 토큰·빌드 횟수·근거 부족률 (§2.1) |

### 2.1 추세 — "지금 얼마인가" 가 아니라 "나아지나 나빠지나"

다른 절은 한 시점을 말한다. 그런데 운영자가 실제로 묻는 것은 대개 **"요즘 느려졌나?"**, **"토큰이 늘었나?"**
이고, p95 한 값으로는 답할 수 없다. 그래서 구간으로 잘라 나란히 놓는다.

```bat
python -m llmwiki stats --full --section trend                    :: 일간 (기본 14일)
python -m llmwiki stats --full --section trend --bucket week      :: 주간 (기본 12주)
python -m llmwiki stats --full --section trend --bucket month     :: 월간 (기본 1년)
python -m llmwiki stats --full --section trend --bucket week --trend-days 200
```

| 묶음 | 기본 기간 | 왜 |
|---|---|---|
| `day` | 14일 | 최근 변화 |
| `week` | 84일(12주) | 주 단위 패턴 |
| `month` | 365일 | 계절·분기 |

**기간이 묶음마다 다른 이유**: '월간' 을 7일치로 그리면 막대가 하나뿐이라 아무 말도 하지 못한다.
`--trend-days` 로 직접 정할 수 있다.

구간마다 나오는 값: `queries` · `p50_ms` · `p95_ms` · `tokens` · `tokens_per_query` ·
`llm_calls_per_query` · `builds` · `build_ms` · `insufficient_rate` · `up`/`down`(👍/👎).
`delta_last` 는 **마지막 구간과 그 앞 구간의 차이**다 — 화면의 ▲▼ 가 이 값이다.

- **Web**: 옵저빌리티 › 시스템 › 추세. `일간 · 주간 · 월간` 버튼으로 바꾸고, 세 개의 차트
  (질의 수/빌드 · 지연 p50·p95 · 토큰/근거 부족률)와 구간 표를 함께 본다.
  차트는 **외부 라이브러리 없이 inline SVG** 로 그린다 — 나머지와 같은 이유(사내망에 폴더째 복사).
  **축이 둘이다**: 크기가 다른 두 계열(질의 364 vs 빌드 15)을 한 눈금에 올리면 한쪽이 바닥에 깔린다 —
  막대는 왼쪽, 선은 오른쪽 눈금을 쓰고 범례에 어느 쪽인지 적는다. 값이 없는 구간은 선을 잇지 않는다.
- **터미널**: 같은 값을 스파크라인(`▁▂▃▄▅▆▇█`)으로 한 줄씩 찍는다.
- **MCP**: `wiki_status(full=true, sections=["trend"], bucket="week")`.

## 3. 읽는 법 (함정)

- **표본 수를 먼저 본다.** `latency.n` 이 3이면 p95 는 사실상 최대값이다. 화면이 표본 수를 함께 보여 주는 이유다.
- **`queries.capped` 가 true 면** 표본 상한(기본 5000)에 걸린 것이다 — 기간을 줄이면 정확해진다.
- **`insufficient_rate` 가 0에 가깝다고 좋은 것은 아니다.** 근거가 약해도 답을 만들어 냈을 수 있다 —
  `quality.forensics` 의 `weak` 와 함께 본다.
- **`feedback_rate` 가 낮으면** 👍/👎 숫자 자체를 믿을 수 없다 (몇 명만 누른 것이다).
- **빌드의 느린 단계**는 마지막 빌드 한 번의 값이다. 매번 다르면 `requests` 에서 여러 번을 비교한다.

## 4. 디스크 경고

`storage.hints` 가 다음을 알려 준다 (지우지는 않는다 — 판단은 사람이):

| 경고 | 뜻 | 조치 |
|---|---|---|
| `data/snapshots` 가 DB 보다 큼 | 스냅샷은 색인 전체를 복사한다 — 금방 커진다 | `snapshot list` → `snapshot prune --keep 3` · 보관 개수는 `config.json evolve_snapshot_keep` |
| `data/requests` 파일 500개 초과 | 요청 프로파일이 쌓였다 | 보관 기간 `config.json requests_keep_days` · 정리 `reset logs` ([RESET.md](RESET.md)) |
| 로그 100MB 초과 | | 총량 한도 `config.json log_total_max_mb` ([LOG_QUOTA.md](LOG_QUOTA.md)) |

이 저장소에서 실제로 잰 값(2026-09-20): `data/snapshots` **899.4 MB**(464개)로 DB(496.3 MB)보다 컸다.
운영 통계를 붙이자마자 나온 첫 번째 발견이다.

## 4.5 누가 볼 수 있나

집계 자체는 `read` 등급이라 로그인한 사람이면 자기 화면에서 볼 수 있다. **한 절만 예외다.**

| 절 | 누가 | 왜 |
|---|---|---|
| `users` (누가 몇 건 질의했나) | **admin 만** | 같은 값을 주는 `GET /api/query_users` 가 admin 전용이다. 여기서 가려 두지 않으면 **막아 둔 문을 옆문으로 여는 것**이 된다 |
| 나머지 8절 | 로그인 사용자 | 규모·속도·용량은 쓰는 사람이 스스로 확인할 수 있어야 한다 |

admin 이 아닌 사람이 부르면 그 절이 빠지고 **`redacted` 에 이유가 담겨 온다**(가린 사실 자체를 숨기지 않는다).
MCP `wiki_status(full=true)` 도 호출한 API 키의 역할로 같은 판정을 받는다.
CLI `stats --full` 은 `security.json` 등급표로 실행자를 이미 검사한 뒤라 그대로 보여 준다.

## 5. 성능

집계는 DB 를 훑는다. 그래서:

- 기간(`days`, 기본 7)과 표본 상한(`max_rows`, 기본 5000)이 있다.
- **읽기 전용**이다 — 어떤 것도 쓰지 않는다 (회귀 테스트로 지킨다).
- Web 은 버튼을 눌렀을 때만 집계한다 (화면 자동 갱신에 얹지 않는다).
- 큰 DB 에서 느리면 `--section` 으로 필요한 것만 부른다.

## 6. 검증

```bat
python -m unittest tests.test_opstats            :: 21건 (읽기 전용 · 빈 환경 · 섹션 필터 · 표본 수 · admin 전용 절)
python -m unittest tests.test_quality_ux_0920    :: 추세(일/주/월) 8건 포함
python -m llmwiki stats --full --days 30
python tools/verify/verify_surface_align.py
```

## 7. 구현 파일

| 파일 | 역할 |
|---|---|
| `llmwiki/opstats.py` | `SECTIONS` · `SECTION_HELP` · `collect()` · **`_trend()`**(일/주/월) · `format_text()`(스파크라인) · `redact()`(admin 전용 절) |
| `llmwiki/cli.py` | `stats --full [--days --section --top --bucket --trend-days]` |
| `llmwiki/web/server.py` | `GET /api/opstats?days=&sections=&top=&bucket=&trend_days=` |
| `llmwiki/mcp.py` | `wiki_status(full, days, sections, top, bucket)` |
| `llmwiki/web/static/js/observability.js` | 옵저빌리티 › 시스템 의 운영 통계 패널 · `chart()`(inline SVG) |
| `llmwiki/web/static/style.css` | `.chart` · `.ch-*` (차트 스타일 — 탭에 스코프하지 않는다) |
| `tests/test_opstats.py` · `tests/test_quality_ux_0920.py` | 회귀 테스트 |
