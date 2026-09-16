# tools/verify — 전 기능 검증 하네스 (2026-09-15)

다른 환경에서 bring-up 한 뒤 **모든 CLI 명령·모든 Web 엔드포인트·UI 배선·브라우저 로드**를 자동으로 다시 확인하는 스크립트. 결과와 해석은 [docs/VERIFICATION_0915.md](../../docs/VERIFICATION_0915.md).
프로젝트 루트에서 실행한다. 모두 표준 라이브러리만 쓰며, 실제 `config.json`/`data/` 를 건드리지 않는다(브라우저 검사만 실제 DB 를 127.0.0.1 전용 서버로 읽는다).

| 스크립트 | 무엇을 | 소요 | 결과 |
|---|---|---|---|
| `python tools/verify/verify_cli.py` | 임시 폴더에 설정·샘플 코퍼스를 복사하고 `LLMWIKI_*_PATH` 로 격리한 뒤 **222개 CLI 명령**을 실제 subprocess 로 실행(빌드·채널 빌드·질의·평가·trial·포렌식 expect·evolve·snapshot·users/security/apikey·권한 게이트·mcp stdio·브리지 오류·server·schedule·models 카탈로그…) | 4~6분 | `CLI 검증: N 명령 중 M 통과` + 명령별 종료 코드/첫 줄, `verify_cli_result.json` |
| `python tools/verify/verify_web.py` | 같은 격리 환경으로 `serve --host 0.0.0.0` 를 띄우고 **255개 요청**: 정적 파일·게스트(viewer) 허용/거부(401)·viewer 403·class1 428→확인·admin 전 POST(설정/프롬프트/규칙/위키/진화/메모리/유지보수/trial/fusion/watch/snapshot/채널·전체 빌드/users/security/apikeys/서버 모니터/스케줄/프로파일)·API 키·`/mcp` 도구·잘못된 키 401·CSRF·작업 취소와 취소 후 복구 | 3~5분 | `WEB 검증: N 요청 중 M 통과`, `verify_web_result.json` |
| `python tools/verify/verify_ui_wiring.py` | 정적 검사: JS 의 `$('#id')` 참조가 index.html/login.html 또는 JS 가 동적으로 만드는 id 에 있는지, 탭↔섹션 대응, JS 가 부르는 `/api/...` 경로가 서버에 있는지 | 1초 | `RESULT OK` |
| `python tools/verify/verify_browser.py` | Edge/Chrome headless 로 `/` 와 `/login` 을 실제 로드(JS 실행) → 콘솔 오류 0, 사용자 배지/상태/탭 31개/신규 요소 렌더 확인. 브라우저가 없으면 SKIP | 20초 | `BROWSER OK` |
| `python tools/verify/verify_monkey.py` | 실제 DB 를 복사한 격리 서버에 **무작위 입력을 동시에 쏟아붓는다** — 무작위 경로·본문·타입, 제어문자·짝 없는 서러게이트·이모지·거대 본문·잘못된 JSON·이상한 HTTP 메서드, MCP JSON-RPC 퍼징, CLI argv 퍼징. 판정은 **500 오류·연결 끊김·CLI traceback 유출이 0 인가**(429·503 은 정상 거절이므로 결함 아님) + 폭격 뒤 서버 생존과 정상 질의 복구 | 3분 | `500 오류: N`, `verify_monkey_result.json` |

실패 행은 `FAIL` 로 시작한다. 기대 종료 코드/HTTP 상태가 스크립트 안에 명령별로 적혀 있으므로, 새 명령·엔드포인트를 추가하면 그 줄을 함께 추가한다.

멍키 하네스 옵션: `--seed <숫자>`(재현), `--real-llm`(mock 대신 실제 프로바이더), `--destructive`(스냅샷 복원·config reset 같은 파괴적 엔드포인트까지 포함), `--keep`(임시 폴더 보존), `--duration <초>`. 포트 8794 를 이미 쓰고 있으면 시작하지 않고 알려 준다 — 예전에 남은 서버 프로세스가 옛 코드를 서빙해 "고쳤는데 결과가 그대로"로 보이는 일을 막기 위한 것이다.

| `python tools/verify/verify_buttons.py` | **Web UI 의 모든 버튼(105개)을 하나씩 실제로 눌러** 본다. 설정·DB 를 임시 폴더로 복사하고 mock LLM 으로 서버를 띄우므로 저장·초기화 버튼을 눌러도 실제 데이터가 바뀌지 않는다. 판정: ① 콘솔/프라미스 오류가 나면 FAIL ② **아무 일도 일어나지 않으면**(서버 호출도·화면 변화도·알림도 없음) FAIL ③ 버튼별 기대 조건(있으면) 불충족 시 FAIL. 입력이 필요한 버튼은 미리 채워 준다 | 6~8분 | `버튼 N개 중 M개 통과`, `verify_buttons_result.json` |
| `python tools/verify/verify_click.py` | **버튼을 실제로 눌러** 화면이 바뀌는지 확인한다(Chrome DevTools Protocol, 표준 라이브러리만). `--dump-dom` 검사는 "그려지는가" 만 보므로, 눌러도 아무 일도 안 일어나는 버튼(핸들러가 조용히 return 하거나 예외로 죽는 경우)은 못 잡는다. 요청 프로파일 행 클릭 · 포렌식 진단 실행 · 질의 로그 trace · 로그 조회 등을 클릭하고 DOM 변화와 콘솔 오류를 본다 | 40초 | `RESULT OK` |
| `python tools/verify/bench_fts.py` | **전체 리빌드 색인 단계가 청크 수에 비례하는지**(제곱이 아닌지) 측정. `fts_trigram` on/off 를 함께 잰다. 청크를 2배로 했을 때 시간이 2배 근처여야 하고, 4배가 되면 `PROBLEMS` 로 알린다 | 20초 (`--no-old`) | `RESULT OK` |

`verify_buttons.py` 옵션: `--only evolve,memory`(탭 지정) · `--heavy`(빌드·평가처럼 오래 걸리는 버튼도) ·
`--live`(격리하지 않고 지금 떠 있는 서버에 — 상태를 바꾸는 버튼은 자동 제외) · `--wait <초>`(클릭 후 대기).
기본으로 건너뛰는 버튼은 스크립트 상단의 `HEAVY`(전체 빌드·평가·스냅샷 생성 등)에 적혀 있다.
새 버튼을 추가하면 자동으로 목록에 들어오고, 출력이 특정 영역에 그려지는 버튼은 `EXPECT` 에 기대 조건을 한 줄 추가한다.

`bench_fts.py` 가 지키는 것: `chunks_fts`·`chunks_tri` 는 `chunk_id` 가 UNINDEXED 라 청크별 `DELETE ... WHERE chunk_id=?` 가
매번 FTS 전체 스캔이 된다. 예전에 그렇게 하다가 16,882 청크 기준 색인 단계가 수십 분 걸렸다(§4.2). 새 코드가 다시
청크별 삭제로 돌아가면 이 스크립트가 잡는다. 예전 방식과 나란히 비교하려면 `--no-old` 를 빼고 실행한다(오래 걸린다).

동시 사용·취소·인코딩은 하네스가 아니라 단위 테스트로 확인한다:

```bat
python -m unittest tests.test_concurrency_0915    :: 43개 — 요청 격리·RW 락·대기열·속도 제한·취소·역할 정책·스케줄러·30 동시 질의
python -m unittest tests.test_console_0915        :: 8개 — 좁은 인코딩에서도 한글이 깨지지 않는가
python -m unittest tests.test_pipeline.FtsRebuildTest  :: 4개 — 전체/증분 리빌드가 FTS·trigram 행을 정확히 갈아 끼우는가
```
