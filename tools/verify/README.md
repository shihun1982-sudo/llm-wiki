# tools/verify — 전 기능 검증 하네스 (2026-09-15)

다른 환경에서 bring-up 한 뒤 **모든 CLI 명령·모든 Web 엔드포인트·UI 배선·브라우저 로드**를 자동으로 다시 확인하는 스크립트. 결과와 해석은 [docs/VERIFICATION_0915.md](../../docs/VERIFICATION_0915.md).
프로젝트 루트에서 실행한다. 모두 표준 라이브러리만 쓰며, 실제 `config.json`/`data/` 를 건드리지 않는다(브라우저 검사만 실제 DB 를 127.0.0.1 전용 서버로 읽는다).

| 스크립트 | 무엇을 | 소요 | 결과 |
|---|---|---|---|
| `python tools/verify/verify_cli.py` | 임시 폴더에 설정·샘플 코퍼스를 복사하고 `LLMWIKI_*_PATH` 로 격리한 뒤 **184개 CLI 명령**을 실제 subprocess 로 실행(빌드·채널 빌드·질의·평가·trial·포렌식 expect·evolve·snapshot·users/security/apikey·권한 게이트·mcp stdio·브리지 오류…) | 4~6분 | `CLI 검증: N 명령 중 M 통과` + 명령별 종료 코드/첫 줄, `verify_cli_result.json` |
| `python tools/verify/verify_web.py` | 같은 격리 환경으로 `serve --host 0.0.0.0` 를 띄우고 **210개 요청**: 정적 파일·게스트(viewer) 허용/거부(401)·viewer 403·class1 428→확인·admin 전 POST(설정/프롬프트/규칙/위키/진화/메모리/유지보수/trial/fusion/watch/snapshot/채널·전체 빌드/users/security/apikeys)·API 키·`/mcp` 9개 도구·잘못된 키 401·CSRF | 3~5분 | `WEB 검증: N 요청 중 M 통과`, `verify_web_result.json` |
| `python tools/verify/verify_ui_wiring.py` | 정적 검사: JS 의 `$('#id')` 참조가 index.html/login.html 또는 JS 가 동적으로 만드는 id 에 있는지, 탭↔섹션 대응, JS 가 부르는 `/api/...` 경로가 서버에 있는지 | 1초 | `RESULT OK` |
| `python tools/verify/verify_browser.py` | Edge/Chrome headless 로 `/` 와 `/login` 을 실제 로드(JS 실행) → 콘솔 오류 0, 사용자 배지/상태/탭 28개/신규 요소 렌더 확인. 브라우저가 없으면 SKIP | 20초 | `BROWSER OK` |

실패 행은 `FAIL` 로 시작한다. 기대 종료 코드/HTTP 상태가 스크립트 안에 명령별로 적혀 있으므로, 새 명령·엔드포인트를 추가하면 그 줄을 함께 추가한다.
