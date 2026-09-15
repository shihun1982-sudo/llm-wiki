# SECURITY — 다중 사용자 서버의 로그인·역할·권한 표·API 키·파괴적 작업 보호

> 대상: LLM Wiki 를 사내 서버에 올려 여러 사람과 여러 외부 LLM(MCP)이 쓰게 할 운영자/관리자. "누가 실수로 색인 DB 를 날리는 것"을 막고, 권한이 있는 사람이 **자기가 무엇을 하는지 알고** 실행하게 하는 것이 목표다.
> 사내 표준인 **아이디/비밀번호 로그인과 SSO 로그인의 병행**을 기본으로 설계했고, 다른 회사/환경으로 옮길 때 코드가 아니라 `security.json` 한 파일만 바꾸면 되게 했다.
> 2026-09-14: 역할 6단계(viewer·class3·class2·class1·builder·admin), 작업 등급 7단계, **admin 이 편집하는 권한 표(permissions)**, 익명(게스트) 접속, CLI 권한 게이트, API 키(MCP/스크립트) 추가. 설계 배경은 [IMPLEMENTATION_PLAN_0914.md](IMPLEMENTATION_PLAN_0914.md) §1.

## 0. 요약 (한 장)

| 질문 | 답 |
|---|---|
| 로그인 방식 | **로컬 ID/비밀번호**(security.json 의 users, PBKDF2 해시) **+ SSO**(OIDC 코드 플로우 또는 사내 리버스 프록시 헤더) **+ API 키**(`Authorization: Bearer lwk_…`, MCP/스크립트용). 한 로그인 화면에 ID/PW 폼과 SSO 버튼이 같이 뜬다 |
| 역할 | 6단계 `viewer < class3 < class2 < class1 < builder < admin` (누적). 구 역할 `operator` 는 `class1` 로 자동 해석 |
| 작업 등급 | 7단계 `read < run < edit < index < rebuild < admin < destructive` (§2 표). 서버가 모든 요청을 이 표로 분류한다 |
| 권한 표 | 등급별 최소 역할(`permissions.levels`) + 개별 작업 오버라이드(`permissions.ops`) 를 **admin 이 Web 보안 탭 / `security perms set`** 으로 바꾼다. 코드 변경 없음 |
| 기본 접속자 | `anonymous_role: "viewer"` — 로그인 없이 접속한 사람은 게스트(viewer)로 **DB 에 영향 없는 기능(질의·검색·조회·피드백·제안·기대 결과 포렌식)** 을 쓴다. `""` 로 두면 로그인 필수 |
| "확인만" | `edit`/`index`/`admin` 등급 = 역할 검사 + 확인 대화상자 |
| "문구 + 비밀번호" | `rebuild`(전체/채널 리빌드·스냅샷 복원) 와 `destructive`(로그 삭제·config reset·사용자 삭제) = 역할 + 확인 + 확인 문구(`DELETE INDEX`) + 로컬 계정이면 비밀번호 재입력. SSO/API 키 계정은 문구만 |
| 실수 보험 | 파괴적 작업 직전에 **자동 스냅샷**(`data/snapshots/`, 최근 3개 보존) → `snapshot restore` 로 되돌림 |
| 감사 | 누가·언제·무엇을·허용/거부 → `logs/audit.jsonl` (Web › Settings › 보안 탭, `security audit`). CLI 게이트 거부도 기록 |
| CLI | 같은 표로 게이트한다. 실행자 역할 = `--user`/`LLMWIKI_USER`+`LLMWIKI_PASSWORD` 로컬 계정 > `LLMWIKI_API_KEY` > `security.json cli.default_role`(기본 admin). 거부는 종료 코드 5 |
| 기본 admin | `kh82.kim / 1234qwer` (프로젝트 루트 security.json 에 생성됨). 변경: `users passwd kh82.kim` 또는 Web 보안 탭 |
| 이식 | `security.json` 하나(+ `.env` 의 OIDC client secret). 표준 라이브러리만 사용 |

## 1. 왜 "암호 하나"가 아니라 계정·역할·권한 표인가 (검토한 대안)

| 방식 | 장점 | 단점 | 결론 |
|---|---|---|---|
| A. 공유 관리 암호 하나 | 구현이 가장 단순 | 누가 했는지 모른다 · 유출 시 전원 교체 · 퇴사자 회수 불가 | **재인증 수단**으로만 채택(로컬 계정의 비밀번호 재입력) |
| B. 사용자 계정 + 역할 (RBAC) | 감사 가능 · 퇴사/이동 시 계정만 정리 · viewer 는 위험 버튼을 못 누름 | 계정 관리 부담 | **채택**. 회사 표준(ID/PW + SSO)과 같은 모양 |
| C. 확인 문구만 (GitHub 식) | "알고 한다" 보장 | 누구나 할 수 있음 | **B 위에 얹음**. rebuild/destructive 는 역할이 있어도 문구 |
| D. 기능마다 역할을 낱개로 지정 | 세밀함 | 엔드포인트 60개+ 를 일일이 편집 → 실수, 새 기능마다 누락 | **등급표 + 오버라이드**로 대체(§2). 등급 7개에 최소 역할을 두고 예외만 op 단위로 |

최종안 = **B + C + (로컬 계정이면 A) + 등급표/오버라이드 + 자동 스냅샷 + 감사 로그**.

## 2. 역할과 작업 등급표 (= `llmwiki/auth.py: classify_api / classify_cli`)

### 2.1 역할 (누적: 위 역할은 아래 역할의 작업을 모두 할 수 있다)

| 역할 | 뜻 | 대표 작업 |
|---|---|---|
| `viewer` | 조회·질의 (DB 무영향; 로그만 남음) | query, search, 문서/그래프/위키 조회, 👍👎 피드백, 제안 등록, **forensic expect**, pin test, 규칙/시간 테스트, MCP 도구 전부 |
| `class3` | + 토큰·시간을 쓰는 실행 | eval, trial run, fusion compare, models test(--live), evolve review, Web 콘솔(읽기 명령), MCP 소스 test/enrich |
| `class2` | + 지식 데이터 편집 (되돌릴 수 있음, 색인 불변) | pin/규칙(query_rules)/프롬프트/튜닝/프리셋 저장, 위키 편집, 제안 apply/reject, memory decay/consolidate, trial 삭제 |
| `class1` | + 색인 갱신 (증분·복구 가능) | 증분 build, `build --channels …`, verify --fix, precompute, maintenance(vacuum·fts_optimize·wal_checkpoint·clear_cache·warm_cache·refresh_doc_refs), 스냅샷 생성/prune, 워처 시작/중지, MCP ingest, embed clear-cache |
| `builder` | + 색인 전체/채널 리빌드, 복원 | `build --full/--reset`, `build fts|vector|graph`, snapshot restore |
| `admin` | + 설정·프로바이더·사용자·권한 | config/models/agents/mcp_sources 저장, users, security(perms/anonymous/cli), apikey, 워처 설정 저장, `build --purge-logs`, `maintenance purge_requests`, `config reset`, `users remove` |

구 이름 호환: `operator` → `class1` (security.json 의 users/role_map/ops 에 남아 있어도 그대로 읽힌다).

### 2.2 작업 등급과 기본 최소 역할

| 등급 | 뜻 | 기본 최소 역할 | 확인 방식 (mode=on) | 대표 작업 |
|---|---|---|---|---|
| `read` | 조회·질의 | viewer | 없음 | GET 전부(admin 전용 조회 제외), `/api/query`·`/api/search`·`/api/feedback`·`/api/evolve/propose`·`/api/forensic/expect`·`/mcp`, `build verify`(fix 없음), pins test, presets 임시 적용, query_rules test |
| `run` | 토큰·시간 소모 실행 | class3 | 없음 | `/api/eval`, `/api/trials`(run), `/api/fusion/compare`, `/api/models/test`, `/api/evolve/review`, `/api/cli`(읽기 명령; 콘솔 자체가 run 이상), mcp_sources test/enrich |
| `edit` | 지식 편집 | class2 | 확인 대화상자 | `/api/pins` `/api/query_rules` `/api/prompts` `/api/tuning` `/api/presets`(save) `/api/wiki/page` `/api/rules` `/api/evolve/apply|reject` `/api/memory` trials delete |
| `index` | 색인 갱신 | class1 | 확인 대화상자 | `/api/build`(증분, `channels` 포함), `/api/build/verify`(fix), `/api/precompute`, `/api/maintenance`(purge 제외), `/api/snapshot`(create/prune), `/api/watch`(start/stop/tick), mcp_sources ingest |
| `rebuild` | 리빌드·복원 | builder | 확인 + 문구 + (로컬) 비밀번호 + 자동 스냅샷 | `/api/build`(full/reset), `/api/build {channel}`, `/api/snapshot`(restore), CLI `build --full`, `build fts|vector|graph`, `snapshot restore` |
| `admin` | 설정·사용자·권한 | admin | 확인 대화상자 | `/api/config` `/api/models/set` `/api/agents` `/api/mcp_sources`(save) `/api/auth/users` `/api/security` `/api/apikeys` `/api/watch`(save) |
| `destructive` | 로그·이력·설정 삭제 | admin | 확인 + 문구 + 비밀번호 + 스냅샷 | `/api/build {purge_logs}`, `/api/maintenance purge_requests`, CLI `config reset`, `users remove` |

- 모르는 새 POST 엔드포인트는 자동으로 `edit` 으로 취급된다(안전한 기본값). 새 기능을 붙일 때 표(`classify_api`/`classify_cli`)에 한 줄 추가하면 권한이 따라온다 — ad-hoc `confirm()` 을 흩뿌리지 않는다.
- Web 콘솔(`/api/cli`)은 **입력한 argv 를 같은 표로 분류**한다. `build fts` 를 콘솔에 쳐도 화면 버튼과 똑같이 문구·비밀번호를 요구하고, 승인되면 `--yes` 를 붙여 실행된다.
- MCP 도구는 모두 `read` (색인을 바꾸지 않음). `wiki_propose`/`wiki_feedback`/`wiki_forensic` 은 제안·피드백·진단 기록만 남긴다.

### 2.3 권한 표 편집 (`security.json → permissions`)

```jsonc
"anonymous_role": "viewer",     // 미로그인 접속자 역할 ("" = 로그인 필수)
"permissions": {
  "levels": {"read": "viewer", "run": "class3", "edit": "class2", "index": "class1", "rebuild": "builder", "admin": "admin", "destructive": "admin"},
  "ops": {                       // 개별 작업 오버라이드: op 이름 = 감사 로그의 op (Web 보안 탭 '대표 op 목록' 참고). 접미 * 는 접두 일치
    "/api/eval": "viewer",       // 예: eval 은 viewer 도
    "cli:trial run": "class2",   // 예: CLI trial run 은 class2 부터
    "cli:build*": "admin"        // 예: 모든 CLI build 계열은 admin 만
  }
},
"cli": {"default_role": "admin", "require_login": false}   // CLI 실행자 기본 역할 / 로그인 강제
```
판정 순서: `ops`(정확 일치 → `*` 접두 일치) → `levels[등급]` → 코드 기본값. 확인 방식(대화상자/문구/비밀번호)은 등급에 고정이며 역할을 낮춰도 바뀌지 않는다.

```bat
python -m llmwiki security perms                       :: 현재 표 + 역할 설명
python -m llmwiki security perms set run=viewer        :: 등급의 최소 역할 변경
python -m llmwiki security perms set "/api/eval=class2" "cli:trial run=class2"   :: 개별 작업 오버라이드
python -m llmwiki security perms set "/api/eval="      :: 오버라이드 제거 (빈 값)
python -m llmwiki security perms reset                 :: 기본값
```
Web: Settings › **보안 · 사용자** › "권한 표" — 등급별 최소 역할 드롭다운, 익명(게스트) 역할, CLI 기본 역할/로그인 필수, 개별 오버라이드 편집란(`op = 역할` 한 줄에 하나), 대표 op 목록. 저장 즉시 반영(재시작 불필요).

## 3. 흐름

```
브라우저/외부 LLM ──POST /api/build {channel:"fts"}──▶ 서버 identify()
     ├─ 프록시 헤더 SSO → Bearer API 키 → 세션 쿠키 → (anonymous_role) 게스트
     └─ authorize(): classify_api → (level, op) → min_role(level, op) 와 역할 비교
          ├─ 게스트가 부족 → 401 + need{role} (화면은 '로그인 필요' 안내)
          ├─ 로그인 사용자가 부족 → 403 (감사 로그: DENY)
          ├─ 확인 필요 → 428 + need{confirm | phrase:"DELETE INDEX", password:true, snapshot:true}
          │      core.js api() 가 모달을 띄워 문구/비밀번호를 받고 같은 요청을 다시 보냄
          └─ 통과 → 실행 → 감사 로그(ok)
```
- 세션: HMAC-SHA256 서명 쿠키(`llmwiki_session`, HttpOnly, SameSite=Lax, HTTPS 면 Secure). 서명 키는 `data/.session_secret`(자동 생성, 지우면 전원 로그아웃). 만료 `session_hours`(기본 12h). 로컬 사용자의 역할을 바꾸면 기존 세션에도 즉시 반영된다.
- CSRF: 모든 API 호출에 `X-Requested-With: llmwiki` 헤더 + `Origin` 검사. curl/스크립트는 JSON 본문이면 통과.
- 무차별 대입: 로그인 실패 시 0.5s 지연 + 감사 로그. (IP 차단은 리버스 프록시에 맡긴다 — §7)

## 4. 로그인 방식 — `security.json`

파일: 프로젝트 루트 `security.json` (`LLMWIKI_SECURITY_PATH` 로 위치 변경). 없으면 기본값(mode auto, 익명 viewer, 사용자 없음). `python -m llmwiki security init` 으로 생성.

```jsonc
{
  "mode": "auto",                 // off | on | auto (auto: 127.0.0.1 에 바인드하면 off, 그 외(0.0.0.0 등)는 on)
  "session_hours": 12,
  "anonymous_role": "viewer",
  "local":  {"enabled": true, "min_password_len": 8},
  "sso":    { … §4.2 / §4.3 … },
  "users":  {
    "kh82.kim": {"role": "admin", "pw": "pbkdf2_sha256$200000$…", "name": "kh82.kim"},
    "erin":     {"role": "class2"}                 // pw 없음 = SSO 전용, 역할만 고정
  },
  "api_keys": {"3f9a1c2e": {"name": "claude-desktop-kim", "role": "viewer", "hash": "sha256…", "created": …}},
  "permissions": { … §2.3 … },
  "cli": {"default_role": "admin", "require_login": false},
  "destructive": {"confirm_phrase": "DELETE INDEX", "require_reauth": true, "snapshot_before": true, "snapshot_keep": 3},
  "warn": {"confirm": true}
}
```

### 4.1 로컬 ID/비밀번호 (항상 가능)
```bat
python -m llmwiki users add kh82.kim --role admin        :: 비밀번호 프롬프트 (또는 --password, 환경변수 LLMWIKI_PASSWORD)
python -m llmwiki users add bob --role class1
python -m llmwiki users list | set-role bob builder | passwd bob | remove bob
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
  "groups_claim": "groups",                                   // IdP 가 그룹을 넣어 주는 claim 이름
  "role_map": {"admin": ["wiki-admins"], "builder": ["wiki-builders"], "class1": ["wiki-ops", "modem-sw"], "class2": [], "class3": []},
  "default_role": "viewer",
  "allowed_domains": ["corp.example"],                        // (선택) email 도메인 제한
  "ca_bundle": ""                                             // (선택) 사내 CA PEM 경로
}
```
동작: `/auth/sso/start` → IdP 로그인 → `/auth/sso/callback?code&state` → 서버가 토큰 엔드포인트에 code 교환(client_secret) → id_token 의 `iss/aud/exp/nonce` 검증 + userinfo 병합 → `username_claim` 으로 사용자 id, `groups_claim` → `role_map`(높은 역할부터 매칭) 으로 역할 → 세션 쿠키. `users` 에 같은 id 가 있으면 그 역할이 우선한다. id_token 서명 검증은 `pip install pyjwt[crypto]` 가 있으면 수행한다.

### 4.3 SSO — 리버스 프록시 헤더 (가장 이식하기 쉬운 방식)
```jsonc
"sso": {"enabled": true, "type": "header",
        "header": {"user": "X-Forwarded-User", "groups": "X-Forwarded-Groups", "trusted_proxies": ["10.1.2.3"]},
        "role_map": {"admin": ["wiki-admins"], "class1": ["wiki-ops"]}, "default_role": "viewer"}
```
`trusted_proxies` 에 적힌 주소에서 온 요청의 헤더만 믿는다. 이 방식에서는 로그인 화면 없이 바로 접속된다(로컬 계정은 여전히 `/login`).

### 4.4 API 키 (MCP HTTP · 스크립트 · CI)
```bat
python -m llmwiki apikey add claude-desktop-kim --role viewer     :: 토큰 lwk_<id>_<secret> 은 이때 한 번만 표시
python -m llmwiki apikey list | remove <id|name>
```
사용: `Authorization: Bearer lwk_…` 헤더 (MCP 클라이언트 설정의 `headers`, curl, `LLMWIKI_API_KEY` 환경변수로 CLI 게이트에도). 파일에는 secret 의 SHA-256 해시만 저장되고, 키마다 역할이 있어 필요한 만큼만 준다(외부 LLM 은 viewer 로 충분). Web 보안 탭 "API 키" 에서도 발급/삭제. 상세는 [MCP.md](MCP.md).
**잘못되거나 폐기된 `lwk_` 키는 게스트로 강등되지 않고 401** 을 돌려준다(2026-09-15 수정) — 폐기한 키가 `anonymous_role=viewer` 덕에 계속 동작하거나, 클라이언트가 키 오타를 모른 채 쓰는 일을 막기 위해서다. `lwk_` 형식이 아닌 Bearer(프록시가 붙인 토큰 등)는 무시하고 다음 단계(쿠키 → 익명)로 넘어간다. 거부는 `audit.jsonl` 에 남는다.

### 4.5 병행
로그인 화면(`/login`)은 `local` 이 켜져 있고 사용자가 있으면 ID/비밀번호 폼을, `sso.enabled` 면 "SSO 로 로그인" 버튼을, `anonymous_role` 이 있으면 "게스트로 계속" 링크를 함께 보여 준다. 네 가지(로컬·SSO·API 키·게스트)가 같은 세션/역할 체계를 쓴다.

## 5. CLI 권한 게이트

CLI 는 서버 OS 계정으로 실행되므로 종전에는 admin 으로 봤다. 공용 서버에서 여러 사람이 셸을 쓰면:
```jsonc
"cli": {"default_role": "viewer", "require_login": false}     // 기본 viewer: query/search/조회만. 상위 작업은 --user 로 승격
```
```bat
python -m llmwiki build                             :: !! 권한 부족: 'cli:build'(색인 갱신)은 class1 이상 … (종료 코드 5, audit DENY)
python -m llmwiki --user bob build                  :: 비밀번호 프롬프트 → class1 로 실행
set LLMWIKI_USER=bob& set LLMWIKI_PASSWORD=…        :: 스케줄러/스크립트 (비대화형)
set LLMWIKI_API_KEY=lwk_…                           :: 또는 API 키의 역할로
```
`require_login: true` 면 `--user`/`LLMWIKI_USER`/`LLMWIKI_API_KEY` 없이는 read 등급도 거부한다. Web 콘솔(`/api/cli`)은 서버가 이미 판정했으므로 CLI 게이트를 다시 타지 않는다. 통과한 비-read 작업도 감사 로그에 `via=cli` 로 남는다.

## 6. 파괴적 작업 정책 상세

| 항목 | 값 | 설명 |
|---|---|---|
| `confirm_phrase` | `DELETE INDEX` | rebuild/destructive 에서 그대로 타이핑해야 하는 문구. 조직에 맞게 바꿔도 됨 |
| `require_reauth` | true | 로컬 계정은 비밀번호 재입력. SSO/API 키 계정은 문구만 |
| `snapshot_before` | true | reset/복원 직전에 `data/snapshots/<시각>_auto:reset/` 에 db/wiki/rules/config 복사 |
| `snapshot_keep` | 3 | 자동 스냅샷 보존 개수(수동 스냅샷은 지우지 않음) |

스냅샷: `snapshot list | create --tag t | restore <name> | prune --keep N` (CLI), Web › 보안 탭. 복원 직전에도 자동 스냅샷을 남기므로 복원 자체도 되돌릴 수 있다. 채널 리빌드(`build fts|vector|graph`)는 chunks 를 지우지 않으므로 스냅샷 없이 문구 확인만 한다.

## 7. 감사 로그

`logs/audit.jsonl` 한 줄 = `{time, user, role, via(local|sso|apikey|anon|cli|off), ip, op, level, ok, detail, error}`. 기록 대상: 로그인/로그아웃(성공·실패), `read` 를 제외한 모든 작업(성공), 모든 거부(401/403, CLI 게이트, MCP 인증 실패). 비밀번호·시크릿·토큰 값은 `***` 로 가린다. 보기: `python -m llmwiki security audit --n 100`, Web › Settings › 보안 탭, `GET /api/audit`(admin).

## 8. 서버 공개 절차 (bring-up 체크리스트)

1. `security.json` 확인 — 이 저장소에는 admin `kh82.kim / 1234qwer` 가 들어 있다. **공개 전에 반드시** `python -m llmwiki users passwd kh82.kim` 으로 바꾸고, 필요한 사용자를 `users add <id> --role <역할>` 로 추가한다. 새 환경에서 깨끗이 시작하려면 `setup/security.example.json`(users 비어 있음, 키마다 `_how` 설명) 을 `security.json` 으로 복사하고 admin 을 만든다 — `install.bat/.sh` 가 파일이 없을 때 자동으로 복사하며, `setup/check_env.py` 가 admin 수·기본 admin 잔존·익명 역할·API 키 수를 보고한다.
2. 익명 접속을 허용할지 결정: 사내망 전용이면 `anonymous_role: "viewer"`(기본), 아니면 `""`.
3. SSO 를 쓰면 §4.2/4.3 항목을 채우고 `.env` 에 `LLMWIKI_OIDC_CLIENT_SECRET=`. 그룹 → 역할은 `role_map`.
4. 권한 표 조정(선택): `security perms set …` 또는 Web 보안 탭. 예) eval 을 viewer 에게 열기 `run=viewer`.
5. MCP 로 외부 LLM 을 붙이면 `apikey add <이름> --role viewer` 로 키를 발급해 준다 ([MCP.md](MCP.md)).
6. `python -m llmwiki serve --host 0.0.0.0 --port 8765` — 사용자·API 키·SSO 가 없고 익명도 꺼져 있으면 서버가 **기동을 거부**한다(`--insecure` 로 강제 가능하나 권장하지 않음).
7. HTTPS: 서버는 HTTP 만 말하므로 **리버스 프록시(nginx/IIS/사내 게이트웨이)** 뒤에 두고 TLS 종료 + `X-Forwarded-Proto: https` 를 넘기면 쿠키에 Secure 가 붙는다. 프록시에서 IP 별 rate limit 을 걸면 로그인 무차별 대입도 막힌다.
8. 확인: `security show`, `security perms`; 다른 브라우저에서 게스트로 질의가 되는지, viewer 계정이 빌드 버튼에서 403/로그인 안내를 받는지, builder 가 채널 리빌드에서 문구 모달을 보는지, `security audit` 에 남는지.
9. 운영: 퇴사/이동은 `users remove` / `apikey remove` 또는 IdP 그룹 정리. 정책 변경은 `security.json` 수정 후 Web 보안 탭 "다시 읽기"(또는 서버 재시작).

## 9. 한계와 다음 단계

- **SSO 사용자의 재인증**: 현재 문구만 요구한다. IdP 가 `prompt=login`/`max_age` 를 지원하면 리빌드 직전에 SSO 재인증을 강제하는 옵션을 추가할 수 있다.
- **세션 즉시 폐기**: 서명 쿠키라 개별 세션을 서버에서 지우는 목록이 없다. `data/.session_secret` 삭제 = 전원 로그아웃. API 키는 `apikey remove` 로 즉시 무효.
- **2단계 승인(4-eyes)**: 전체 초기화에 두 번째 admin 의 승인을 요구하는 방식은 `destructive.require_second_admin` 로 확장 가능.
- **HTTPS 자체 종료**는 넣지 않았다(인증서 관리는 프록시가 맞다).
- **rate limit**: API 키별 호출 제한은 없다. 외부 LLM 이 많으면 프록시에서 키/IP 별 제한을 건다.

## 10. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/auth.py` | 역할(ROLES/RANK/별칭)·등급(LEVELS/LEVEL_CONFIRM)·`permissions`(min_role/set_permission)·API 키(발급/검증)·익명·비밀번호 해시·서명 세션·작업 분류표(`classify_api/classify_cli`)·`Auth.authorize()`·로컬 로그인·헤더 SSO·OIDC·감사 로그·CLI 게이트(`cli_actor/cli_min_role`) |
| `llmwiki/snapshots.py` | 스냅샷 생성/목록/복원/정리 |
| `llmwiki/web/server.py` | 요청마다 identify → authorize → 실행 → audit. `/login`, `/api/auth/*`, `/auth/sso/*`, `/api/audit`, `/api/security`(reload/set_permissions/set_permission/set_anonymous/set_cli), `/api/apikeys`, `/api/snapshot`, `/mcp`(Bearer). `serve()` 기동 시 보안 상태 검사 |
| `llmwiki/web/static/login.html` | 로그인 화면(ID/PW + SSO 버튼 + 게스트 링크) |
| `llmwiki/web/static/js/core.js` | `api()` 의 401/403/428 처리(게스트 안내 포함), 단계 확인 모달(`stepUp`), 헤더 사용자/게스트 배지 |
| `llmwiki/web/static/js/settings.js` | 보안 탭: 역할 설명·사용자·권한 표·API 키·스냅샷·감사 로그 |
| `llmwiki/cli.py` | `--user/--password` 전역 옵션, `_cli_gate`, `users`, `security show|init|audit|perms`, `apikey`, `snapshot`, 파괴적/리빌드 명령의 확인 문구/`--yes` |
| `llmwiki/pipeline.py` | `reset_index` 락 + 자동 스냅샷 |
| `tests/test_auth.py` | 분류표(7등급) · 해시/서명 · 역할/확인/문구/재인증 · permissions 오버라이드 · 익명 · API 키 · 헤더 SSO · OIDC(가짜 IdP) · Web 통합(로그인→428→승인→스냅샷→감사→권한 표 편집→API 키 질의) · CLI 게이트(viewer 거부, --user 승격, perms/apikey CLI) |
| `tests/test_features_0914.py` (McpHttpTest) | MCP HTTP 의 Bearer/쿠키/익명 인증, 감사 로그 |
