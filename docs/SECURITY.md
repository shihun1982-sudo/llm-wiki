# SECURITY — 다중 사용자 서버의 로그인·권한·파괴적 작업 보호 (계획 + 구현)

> 대상: LLM Wiki 를 사내 서버에 올려 여러 사람이 쓰게 할 운영자/관리자. "누가 실수로 색인 DB 를 날리는 것"을 막고, 권한이 있는 사람이 **자기가 무엇을 하는지 알고** 실행하게 하는 것이 목표다.
> 사내 표준인 **아이디/비밀번호 로그인과 SSO 로그인의 병행**을 기본으로 설계했고, 다른 회사/환경으로 옮길 때 코드가 아니라 `security.json` 한 파일만 바꾸면 되게 했다.

## 0. 요약 (한 장)

| 질문 | 답 |
|---|---|
| 로그인 방식 | **로컬 ID/비밀번호**(security.json 의 users, PBKDF2 해시) **+ SSO**(OIDC 표준 코드 플로우, 또는 사내 리버스 프록시가 넣는 헤더). 두 방식이 한 로그인 화면에 나란히 뜬다 |
| 권한 | 역할 3단계 `viewer < operator < admin`. SSO 사용자는 IdP 그룹 → 역할 매핑, 로컬 목록에 적힌 역할이 우선 |
| 작업 등급 | `read < run < warn < admin < destructive` (§2 표). 서버가 모든 요청을 이 표로 분류한다 |
| "경고만" | `warn`/`admin` 등급 = 역할 검사 + 확인 대화상자(`_confirm`) |
| "암호를 요청" | `destructive` 등급(전체 초기화·로그 삭제·스냅샷 복원·config reset) = **admin + 확인 + 확인 문구(`DELETE INDEX`) + 로컬 계정이면 비밀번호 재입력**. SSO 계정은 비밀번호가 없으니 문구로 대신 |
| 실수 보험 | 파괴적 작업 직전에 **자동 스냅샷**(`data/snapshots/`, 최근 3개 보존) → `snapshot restore` 로 되돌림 |
| 감사 | 누가·언제·무엇을·허용/거부 → `logs/audit.jsonl` (Web › Settings › 보안 탭, `security audit`) |
| CLI 도 | `build --full`, `maintenance purge_requests`, `config reset`, `snapshot restore` 는 확인 문구를 타이핑해야 하고, 비대화형(스케줄러/파이프)이면 `--yes` 가 없으면 거부 |
| 이식 | `security.json` 하나(+ `.env` 의 OIDC client secret). 코드 변경 없음. 표준 라이브러리만 사용 |

## 1. 왜 "암호 하나"가 아니라 계정·역할인가 (검토한 대안)

사용자 요청은 "전체 DB 를 날리는 것은 암호를 요청하면 좋겠다. 암호 말고 더 좋은 방법이 있을까? 사용자 계정별로 제어해야 하나?" 였다. 검토한 세 가지:

| 방식 | 장점 | 단점 | 결론 |
|---|---|---|---|
| A. 공유 관리 암호 하나 | 구현이 가장 단순 | 누가 했는지 모른다(감사 불가) · 한 명이 유출하면 전원 교체 · 퇴사자 회수 불가 · "자기가 무엇을 하는지" 와 무관하게 암호만 알면 됨 | 단독으로는 부적합. **재인증 수단**으로만 채택(로컬 계정의 비밀번호 재입력) |
| B. 사용자 계정 + 역할 (RBAC) | 감사 가능 · 퇴사/이동 시 계정만 정리 · viewer 는 아예 위험 버튼을 못 누름 | 계정 관리 부담 | **채택**. 회사 표준(ID/PW + SSO)과 같은 모양이라 사용자가 익숙 |
| C. 계정 없이 확인 문구만 (GitHub 의 "repo 이름 타이핑" 식) | "알고 한다" 는 보장 | 누구나 할 수 있음 | **B 위에 얹음**. destructive 는 역할이 있어도 문구를 타이핑해야 한다 |

즉 최종안은 **B + C + (로컬 계정이면 A 로 재인증) + 자동 스냅샷 + 감사 로그**다. 실수는 "권한 없는 사람이 누름 / 권한 있는 사람이 잘못 누름 / 잘 눌렀는데 후회" 세 층인데, 각각 역할·문구/재인증·스냅샷이 막는다.

## 2. 작업 등급표 (서버가 요청을 분류하는 기준 = `llmwiki/auth.py: classify_api / classify_cli`)

| 등급 | 필요한 역할 | 확인 | 대표 작업 |
|---|---|---|---|
| `read` | viewer | 없음 | 질의·검색·조회 전부, 피드백, 제안 등록, `build verify`(fix 없음), pin test, 프리셋 임시 적용 |
| `run` | operator | 없음 | 토큰·시간을 쓰지만 데이터를 바꾸지 않는 실행: `eval`, `trial run`, `fusion compare`, `models test`, `evolve review`, 콘솔의 읽기 명령 |
| `warn` | operator | `_confirm` (mode=on 일 때) | **복구 가능한 변경**: 증분 빌드, `verify --fix`, precompute, memory decay/consolidate, evolve apply/reject, pin/규칙/프롬프트/튜닝/프리셋 저장, 위키 페이지 편집, 워처 시작/중지, maintenance(vacuum 등), 스냅샷 만들기, trial 삭제 |
| `admin` | admin | `_confirm` (mode=on) | **설정 변경**: config.json/모델 저장(경로를 바꾸면 색인이 사실상 사라지므로), agents.json, mcp_sources 저장, 사용자 관리, 워처 설정 저장 |
| `destructive` | admin | `_confirm` + `_phrase` + (로컬) `_password` | **색인/DB 를 지우거나 통째로 바꿈**: `build --full/--reset` (`--purge-logs` 포함), `maintenance purge_requests`, `snapshot restore`, `config reset`, 콘솔에서 같은 명령 |

- 모르는 새 POST 엔드포인트는 자동으로 `warn` 으로 취급된다(안전한 기본값). 새 기능을 붙일 때 표에 한 줄 추가하면 된다.
- Web 콘솔(`/api/cli`)은 **입력한 argv 를 같은 표로 분류**한다. `build --full` 을 콘솔에 쳐도 화면 버튼과 똑같이 문구·비밀번호를 요구하고, 승인되면 `--yes` 를 붙여 CLI 프롬프트 없이 실행된다. 콘솔 자체는 operator 이상만 쓸 수 있다.
- MCP 도구(`mcp.py`)는 조회·제안만 있고 색인을 바꾸는 도구가 없다(변경 없음).

## 3. 흐름

```
브라우저 ──POST /api/build {full:true}──▶ 서버 authorize()
                                          ├─ 미로그인 → 401 (→ /login)
                                          ├─ 역할 부족 → 403 (감사 로그: DENY)
                                          ├─ 확인 필요 → 428 + need{confirm, phrase:"DELETE INDEX", password:true, snapshot:true}
                                          │      브라우저 core.js api() 가 모달을 띄워 문구/비밀번호를 받고 같은 요청을 다시 보냄
                                          └─ 통과 → (destructive) health 사전 검사 → 자동 스냅샷 → reset → 빌드 job  → 감사 로그(ok)
```
- 모달은 `core.js` 의 `api()` 안에서 자동으로 처리되므로 각 화면 코드에 `confirm()` 을 흩뿌리지 않는다. 서버가 정책의 단일 진실이고, 정책을 바꾸면(예: 문구 변경) UI 가 따라온다.
- 세션: HMAC-SHA256 서명 쿠키(`llmwiki_session`, HttpOnly, SameSite=Lax, HTTPS 면 Secure). 서명 키는 `data/.session_secret`(자동 생성, 지우면 전원 로그아웃). 만료 `session_hours`(기본 12h). 로컬 사용자의 역할을 바꾸면 기존 세션에도 즉시 반영된다.
- CSRF: 모든 API 호출에 `X-Requested-With: llmwiki` 헤더(브라우저는 cross-origin 에서 이 헤더를 못 붙인다) + `Origin` 검사. curl/스크립트는 JSON 본문이면 통과.
- 무차별 대입: 로그인 실패 시 0.5s 지연 + 감사 로그. (IP 차단은 리버스 프록시에 맡긴다 — §7)

## 4. 로그인 방식 셋 — `security.json`

파일: 프로젝트 루트 `security.json` (`LLMWIKI_SECURITY_PATH` 로 위치 변경). 없으면 기본값(= mode auto, 사용자 없음). `python -m llmwiki security init` 으로 생성.

```jsonc
{
  "mode": "auto",                 // off | on | auto (auto: 127.0.0.1 에 바인드하면 off, 그 외(0.0.0.0 등)는 on)
  "session_hours": 12,
  "local":  {"enabled": true, "min_password_len": 8},
  "sso":    { … §4.2 / §4.3 … },
  "users":  {                     // 로컬 계정 + SSO 사용자의 역할 지정
    "alice": {"role": "admin", "pw": "pbkdf2_sha256$200000$…", "name": "Alice"},
    "erin":  {"role": "operator"}                 // pw 없음 = SSO 전용, 역할만 고정
  },
  "destructive": {"confirm_phrase": "DELETE INDEX", "require_reauth": true, "snapshot_before": true, "snapshot_keep": 3},
  "warn": {"confirm": true}
}
```

### 4.1 로컬 ID/비밀번호 (항상 가능)
```bat
python -m llmwiki users add alice --role admin        :: 비밀번호 프롬프트 (또는 --password, 환경변수 LLMWIKI_PASSWORD)
python -m llmwiki users add bob --role operator
python -m llmwiki users list | set-role bob admin | passwd bob | remove bob
```
Web › Settings › **보안 · 사용자** 탭에서도 admin 이 추가/역할 변경/비밀번호 재설정을 할 수 있고, 각 사용자는 자기 비밀번호를 바꿀 수 있다. 해시는 PBKDF2-HMAC-SHA256 200,000회 + 16바이트 salt (표준 라이브러리).

### 4.2 SSO — OIDC (Azure AD/Entra · Okta · Keycloak · Google Workspace · 사내 IdP)
IdP 에 "웹 앱(confidential client)" 을 등록하고 redirect URI 를 `https://<wiki-host>/auth/sso/callback` 으로 넣는다.
```jsonc
"sso": {
  "enabled": true, "type": "oidc", "button_label": "사내 SSO 로 로그인",
  "issuer": "https://login.corp.example/realms/modem",      // + /.well-known/openid-configuration 로 자동 검색
  "client_id": "llm-wiki",
  "client_secret_env": "LLMWIKI_OIDC_CLIENT_SECRET",         // 비밀은 .env 에: LLMWIKI_OIDC_CLIENT_SECRET=…
  "redirect_uri": "https://wiki.corp.example/auth/sso/callback",
  "scopes": "openid profile email",
  "username_claim": "preferred_username",                     // 없으면 email → sub
  "groups_claim": "groups",                                   // IdP 가 그룹을 넣어 주는 claim 이름 (Azure: groups / Okta: groups / Keycloak: 매퍼로 추가)
  "role_map": {"admin": ["wiki-admins"], "operator": ["wiki-ops", "modem-sw"]},
  "default_role": "viewer",
  "allowed_domains": ["corp.example"],                        // (선택) email 도메인 제한
  "ca_bundle": ""                                             // (선택) 사내 CA PEM 경로
}
```
동작: `/auth/sso/start` → IdP 로그인 → `/auth/sso/callback?code&state` → 서버가 토큰 엔드포인트에 code 교환(client_secret) → id_token 의 `iss/aud/exp/nonce` 검증 + userinfo 병합 → `username_claim` 으로 사용자 id, `groups_claim` → `role_map` 으로 역할 → 세션 쿠키. `users` 에 같은 id 가 있으면 그 역할이 우선한다(특정인 지정). id_token 서명 검증은 `pip install pyjwt[crypto]` 가 있으면 수행하고, 없으면 생략한다(토큰을 TLS 로 IdP 에서 직접 받으므로 위조 경로가 없다 — 그래도 가능하면 설치 권장).

### 4.3 SSO — 리버스 프록시 헤더 (가장 이식하기 쉬운 방식)
회사가 이미 Apache/nginx/게이트웨이에 SAML/Kerberos/OIDC 모듈로 SSO 를 걸어 두고 뒤로 `X-Forwarded-User` 같은 헤더를 넘기는 환경이면 코드에 IdP 를 붙일 필요가 없다.
```jsonc
"sso": {"enabled": true, "type": "header",
        "header": {"user": "X-Forwarded-User", "groups": "X-Forwarded-Groups", "trusted_proxies": ["10.1.2.3"]},
        "role_map": {"admin": ["wiki-admins"]}, "default_role": "viewer"}
```
`trusted_proxies` 에 적힌 주소에서 온 요청의 헤더만 믿는다(다른 곳에서 헤더를 흉내 내도 무시). 이 방식에서는 로그인 화면 없이 바로 접속된다(로컬 계정은 여전히 `/login` 에서 쓸 수 있다).

### 4.4 병행
로그인 화면(`/login`)은 `local` 이 켜져 있고 사용자가 있으면 ID/비밀번호 폼을, `sso.enabled` 면 "SSO 로 로그인" 버튼을 함께 보여 준다. 헤더 SSO 는 자동. 세 가지가 모두 같은 세션 쿠키와 역할 체계를 쓴다.

## 5. 파괴적 작업 정책 상세

| 항목 | 값 | 설명 |
|---|---|---|
| `confirm_phrase` | `DELETE INDEX` | 사용자가 그대로 타이핑해야 하는 문구. 조직에 맞게 바꿔도 됨(예: `"모뎀위키 삭제"`) |
| `require_reauth` | true | 로컬 계정은 비밀번호 재입력. SSO 계정은 비밀번호가 없어 문구만 (IdP 재인증은 §8 향후) |
| `snapshot_before` | true | reset 직전에 `data/snapshots/<시각>_auto:reset/` 에 db/wiki/rules/config 복사 |
| `snapshot_keep` | 3 | 자동 스냅샷 보존 개수(수동 스냅샷은 지우지 않음) |

스냅샷: `snapshot list | create --tag t | restore <name> | prune --keep N` (CLI), Web › 보안 탭. 복원 직전에도 자동 스냅샷을 남기므로 복원 자체도 되돌릴 수 있다. 크기는 DB 파일 + wiki 폴더 ≈ 색인 크기(샘플 코퍼스 16MB).

`reset_index` 는 이제 **빌드 파일 락 안에서** 실행되고(다른 프로세스의 빌드와 겹치지 않음), Web 도 CLI 처럼 초기화 전에 health(코퍼스 경로 존재)를 먼저 확인해 코퍼스 경로가 틀린 상태에서 색인만 지워지는 일을 막는다.

## 6. 감사 로그

`logs/audit.jsonl` 한 줄 = `{time, user, role, via(local|sso|off), ip, op, level, ok, detail, error}`. 기록 대상: 로그인/로그아웃(성공·실패), `read` 를 제외한 모든 작업(성공), 모든 거부(401/403). 비밀번호·시크릿 값은 `***` 로 가린다. 보기: `python -m llmwiki security audit --n 100`, Web › Settings › 보안 탭, `GET /api/audit`(admin).

## 7. 서버 공개 절차 (bring-up 체크리스트)

1. `python -m llmwiki security init` → `security.json` 생성.
2. `python -m llmwiki users add <관리자id> --role admin` (최소 1명). SSO 를 쓰면 §4.2/4.3 항목을 채우고 `.env` 에 `LLMWIKI_OIDC_CLIENT_SECRET=`.
3. `mode` 는 `auto` 그대로 두면 `--host 0.0.0.0` 로 띄울 때 자동으로 on. 로컬 개발(`127.0.0.1`)은 로그인 없이 쓰되 파괴적 작업은 문구를 요구한다.
4. `python -m llmwiki serve --host 0.0.0.0 --port 8765` — 사용자도 SSO 도 없으면 서버가 **기동을 거부**한다(`--insecure` 로 강제 가능하나 권장하지 않음).
5. HTTPS: 서버는 HTTP 만 말하므로 **리버스 프록시(nginx/IIS/사내 게이트웨이)** 뒤에 두고 TLS 종료 + `X-Forwarded-Proto: https` 를 넘기면 쿠키에 Secure 가 붙는다. 프록시에서 IP 별 rate limit 을 걸면 로그인 무차별 대입도 막힌다.
6. 확인: `security show`, 다른 브라우저로 viewer 계정 로그인 → 전체 리빌드 버튼이 403 인지, admin 은 모달(문구+비밀번호)이 뜨는지, `security audit` 에 남는지.
7. 운영: 퇴사/이동은 `users remove` 또는 IdP 그룹 정리. 문구·정책 변경은 `security.json` 수정 후 Web 보안 탭 "다시 읽기"(또는 서버 재시작).

## 8. 한계와 다음 단계

- **SSO 사용자의 재인증**: 현재 문구만 요구한다. IdP 가 `prompt=login`/`max_age` 를 지원하면 파괴적 작업 직전에 SSO 재인증을 강제하는 옵션을 추가할 수 있다(`destructive.sso_reauth_max_age_s`).
- **세션 즉시 폐기**: 서명 쿠키라 개별 세션을 서버에서 지우는 목록이 없다. `data/.session_secret` 삭제 = 전원 로그아웃. 필요하면 `users` 에 `session_epoch` 를 두어 사용자 단위 폐기로 확장.
- **2단계 승인(4-eyes)**: 전체 초기화에 두 번째 admin 의 승인을 요구하는 방식은 `destructive.require_second_admin` 로 확장 가능(요청을 보류 큐에 넣고 다른 admin 이 승인).
- **HTTPS 자체 종료** 는 넣지 않았다(표준 라이브러리 `ssl` 로 가능하지만 인증서 관리는 프록시가 맞다).
- CLI 는 서버를 실행하는 OS 계정의 권한 = 관리자 권한으로 본다(문구 확인·`--yes`·스냅샷·감사 로그는 CLI 에도 적용). MCP 는 읽기·제안 전용.

## 9. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/auth.py` | security.json 로드/저장 · 비밀번호 해시 · 서명 세션 · 작업 분류표(`classify_api/classify_cli`) · `Auth.authorize()` · 로컬 로그인 · 헤더 SSO · OIDC(start/callback) · 감사 로그 |
| `llmwiki/snapshots.py` | 스냅샷 생성/목록/복원/정리 |
| `llmwiki/web/server.py` | 요청마다 identify → authorize → 실행 → audit. `/login`, `/api/auth/*`, `/auth/sso/*`, `/api/audit`, `/api/security`, `/api/snapshot`. `serve()` 기동 시 보안 상태 검사 |
| `llmwiki/web/static/login.html` | 로그인 화면(ID/PW + SSO 버튼) |
| `llmwiki/web/static/js/core.js` | `api()` 의 401/403/428 처리, 단계 확인 모달(`stepUp`), 헤더 사용자 배지 |
| `llmwiki/web/static/js/settings.js` | 보안 탭: 사용자·스냅샷·감사 로그 |
| `llmwiki/cli.py` | `users`, `security`, `snapshot` 명령, 파괴적 명령의 확인 문구/`--yes`, `serve --insecure` |
| `llmwiki/pipeline.py` | `reset_index` 락 + 자동 스냅샷 |
| `tests/test_auth.py` | 분류표 · 해시/서명 · 역할/확인/문구/재인증 · 헤더 SSO · OIDC(가짜 IdP) · Web 통합(로그인→428→승인→스냅샷→감사) · CLI 비대화형 거부 |
