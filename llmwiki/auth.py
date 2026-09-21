# -*- coding: utf-8 -*-
"""인증·권한·감사 — Web 서버의 모든 요청은 여기서 (1) 누구인지, (2) 이 작업을 해도 되는지, (3) 무엇을 했는지 기록한다.

설계 요약 (docs/SECURITY.md):
- 로그인 세 가지를 병행: 로컬 ID/비밀번호(security.json users, PBKDF2) + SSO(OIDC 코드 플로우 또는 리버스 프록시 헤더) + API 키(Bearer, MCP/스크립트용).
- 역할 6단계: viewer < class3 < class2 < class1 < builder < admin  (구 operator = class1 별칭).
- 작업 등급 7단계: read < run < edit < index < rebuild < admin < destructive.
    read/run          : 역할만
    edit/index/admin  : 역할 + 확인(_confirm)                                   ← "되돌릴 수 있는 변경 / 설정"
    rebuild/destructive: 역할 + 확인 + 확인 문구(_phrase) + 로컬 계정이면 비밀번호(_password) ← "색인/DB 를 통째로 바꾸거나 지움"
- 등급별 최소 역할과 개별 작업(op)의 최소 역할은 security.json → permissions 로 admin 이 바꾼다 (min_role()).
- 익명 접속: anonymous_role(기본 viewer) 이 있으면 로그인 없이도 그 역할로 읽기 기능을 쓴다. "" 이면 로그인 필수.
- CLI 도 같은 표로 게이트한다 (cli.default_role, --user 승격) — cli.py 의 _cli_gate().
- 세션은 HMAC 서명 쿠키(표준 라이브러리만). CSRF 는 커스텀 헤더(X-Requested-With) + Origin 검사.
- 모드: off(로그인 없음, 로컬 개발) | on | auto(127.0.0.1 이외에 바인드하면 on). off 에서도 rebuild/destructive 는 확인 문구가 필요하다.
- 모든 비-read 작업과 로그인/거부는 logs/audit.jsonl 에 남는다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from . import atomicio
from .config import path_for, ROOT

ROLES = ("viewer", "class3", "class2", "class1", "builder", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
ROLE_ALIASES = {"operator": "class1", "guest": "viewer", "anonymous": "viewer", "op": "class1", "user": "viewer"}
ROLE_LABEL = {"viewer": "조회·질의 (DB 무영향)", "class3": "+ 토큰·시간을 쓰는 실행(eval·trial·models test)", "class2": "+ 지식 데이터 편집(규칙·pin·프롬프트·제안 승인)",
              "class1": "+ 색인 갱신(증분 빌드·verify --fix·precompute·유지보수)", "builder": "+ 전체/채널 리빌드·스냅샷 복원", "admin": "+ 설정·프로바이더·사용자·권한"}

LEVELS = ("read", "run", "edit", "index", "rebuild", "admin", "destructive")
DEFAULT_LEVEL_ROLE = {"read": "viewer", "run": "class3", "edit": "class2", "index": "class1", "rebuild": "builder", "admin": "admin", "destructive": "admin"}
LEVEL_LABEL = {"read": "읽기", "run": "실행(토큰·시간 소모)", "edit": "지식 편집(복구 가능)", "index": "색인 갱신(복구 가능)",
               "rebuild": "리빌드 — 색인 채널/전체를 다시 만듦", "admin": "관리자 설정 변경", "destructive": "파괴적 — 로그·이력·설정 삭제"}
LEVEL_CONFIRM = {"read": "", "run": "", "edit": "confirm", "index": "confirm", "rebuild": "phrase", "admin": "confirm", "destructive": "phrase"}
LEVEL_ORDER = {lv: i for i, lv in enumerate(LEVELS)}
# 구 등급 이름 호환 (warn = edit)
LEVEL_ALIASES = {"warn": "edit"}


def norm_role(role: Any, default: str = "viewer") -> str:
    r = str(role or "").strip().lower()
    r = ROLE_ALIASES.get(r, r)
    return r if r in RANK else default


def norm_level(level: Any) -> str:
    lv = str(level or "").strip().lower()
    lv = LEVEL_ALIASES.get(lv, lv)
    return lv if lv in LEVEL_ORDER else "edit"


DEFAULT_SECURITY: Dict[str, Any] = {
    "_comment": "로그인·역할·권한·파괴적 작업 정책. 설명: docs/SECURITY.md. 사용자 추가: python -m llmwiki users add <id> --role admin · 권한 변경: security perms set <level|op>=<role>",
    "mode": "auto",
    "session_hours": 12,
    "secure_cookie": "auto",
    "anonymous_role": "viewer",
    "local": {"enabled": True, "min_password_len": 8},
    "sso": {
        "enabled": False,
        "type": "oidc",
        "button_label": "사내 SSO 로 로그인",
        "issuer": "",
        "client_id": "",
        "client_secret_env": "LLMWIKI_OIDC_CLIENT_SECRET",
        "redirect_uri": "",
        "scopes": "openid profile email",
        "username_claim": "preferred_username",
        "email_claim": "email",
        "groups_claim": "groups",
        "role_map": {"admin": [], "builder": [], "class1": [], "class2": [], "class3": []},
        "default_role": "viewer",
        "allowed_domains": [],
        "ca_bundle": "",
        "header": {"user": "X-Forwarded-User", "groups": "X-Forwarded-Groups", "trusted_proxies": ["127.0.0.1", "::1"]},
    },
    "users": {},
    "api_keys": {},
    "permissions": {
        "_comment": "levels: 작업 등급별 최소 역할 (read<run<edit<index<rebuild<admin<destructive). ops: 개별 작업(감사 로그의 op 이름, 예 '/api/eval', 'cli:trial run')의 최소 역할 오버라이드.",
        "levels": dict(DEFAULT_LEVEL_ROLE),
        "ops": {},
    },
    "cli": {"default_role": "admin", "require_login": False},
    "destructive": {"confirm_phrase": "DELETE INDEX", "require_reauth": True, "snapshot_before": True, "snapshot_keep": 3},
    "warn": {"confirm": True},
    # 요청 단위 overrides(질의·평가·빌드 본문의 "overrides") 허용 조정. 기본 허용 목록은 web/server.py OVERRIDE_SAFE_KEYS
    # (토글 전부 + top_k·컨텍스트·모델/effort·역할 정책·answer_mode). URL·헤더·경로·서버 운영 키는 admin 만.
    "overrides": {"allow_extra": [], "deny": []},
}


class AuthError(Exception):
    def __init__(self, status: int, error: str, need: Optional[Dict[str, Any]] = None, redirect: str = ""):
        super().__init__(error)
        self.status = status
        self.error = error
        self.need = need or {}
        self.redirect = redirect

    def body(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"error": self.error, "status": self.status}
        if self.need:
            out["need"] = self.need
        if self.redirect:
            out["redirect"] = self.redirect
        return out


# ---------------------------------------------------------------- security.json
def security_path() -> str:
    return path_for("security")


def load_security() -> Dict[str, Any]:
    p = security_path()
    cfg = json.loads(json.dumps(DEFAULT_SECURITY))
    raw_text = atomicio.read_text(p)       # 저장 중이면 기다렸다 읽는다
    if raw_text is not None:
        _deep_update(cfg, json.loads(raw_text))   # 깨진 파일은 조용히 기본값으로 되돌리지 않고 알린다
    # 역할 이름 정규화 (operator → class1 등)
    for u in (cfg.get("users") or {}).values():
        if isinstance(u, dict) and u.get("role"):
            u["role"] = norm_role(u["role"])
    for k in (cfg.get("api_keys") or {}).values():
        if isinstance(k, dict) and k.get("role"):
            k["role"] = norm_role(k["role"])
    perms = cfg.setdefault("permissions", {})
    lv = perms.setdefault("levels", {})
    for level in LEVELS:
        lv[level] = norm_role(lv.get(level), DEFAULT_LEVEL_ROLE[level])
    perms["ops"] = {str(k): norm_role(v) for k, v in (perms.get("ops") or {}).items() if not str(k).startswith("_")}
    cfg.setdefault("cli", {})["default_role"] = norm_role((cfg.get("cli") or {}).get("default_role"), "admin")
    if cfg.get("anonymous_role"):
        cfg["anonymous_role"] = norm_role(cfg["anonymous_role"])
    return cfg


def save_security(cfg: Dict[str, Any]) -> str:
    # 여러 관리자가 동시에 권한을 바꿔도 파일이 깨지지 않도록 원자적으로 저장한다.
    return atomicio.write_json(security_path(), cfg)


def _deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict) and k not in ("users", "api_keys", "ops"):
            _deep_update(dst[k], v)
        else:
            dst[k] = v


# ---------------------------------------------------------------- passwords / secrets
def hash_password(pw: str, rounds: int = 200_000) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, rounds)
    return "pbkdf2_sha256$%d$%s$%s" % (rounds, salt.hex(), dk.hex())


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


def _secret(data_dir: str) -> bytes:
    """세션 서명 키 (data/.session_secret). 없으면 생성. 삭제하면 모든 세션이 무효화된다."""
    p = os.path.join(data_dir, ".session_secret")
    try:
        with open(p, "rb") as f:
            s = f.read().strip()
        if len(s) >= 32:
            return s
    except OSError:
        pass
    os.makedirs(data_dir, exist_ok=True)
    s = secrets.token_hex(32).encode("ascii")
    with open(p, "wb") as f:
        f.write(s)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return s


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Signer:
    def __init__(self, secret: bytes):
        self.secret = secret

    def sign(self, payload: Dict[str, Any]) -> str:
        body = _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        sig = hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).hexdigest()
        return body + "." + sig

    def verify(self, token: str) -> Optional[Dict[str, Any]]:
        try:
            body, sig = token.split(".", 1)
            exp = hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, exp):
                return None
            data = json.loads(_unb64(body).decode("utf-8"))
            if data.get("exp") and time.time() > float(data["exp"]):
                return None
            return data
        except Exception:
            return None


# ---------------------------------------------------------------- API keys (MCP / 스크립트)
API_KEY_PREFIX = "lwk_"


def new_api_key() -> Tuple[str, str, str]:
    """(token, key_id, sha256(secret)). token = lwk_<id>_<secret>. 파일에는 해시만 저장한다."""
    kid = secrets.token_hex(4)
    sec = secrets.token_urlsafe(24)
    tok = "%s%s_%s" % (API_KEY_PREFIX, kid, sec)
    return tok, kid, hashlib.sha256(sec.encode("ascii")).hexdigest()


def parse_api_key(token: str) -> Optional[Tuple[str, str]]:
    t = (token or "").strip()
    if not t.startswith(API_KEY_PREFIX):
        return None
    rest = t[len(API_KEY_PREFIX):]
    if "_" not in rest:
        return None
    kid, sec = rest.split("_", 1)
    return kid, hashlib.sha256(sec.encode("ascii")).hexdigest()


# ---------------------------------------------------------------- operation classification
# CLI: (cmd, 첫 action 또는 플래그) → 등급. 표에 없는 조합은 아래 규칙으로 결정한다.
_DESTRUCTIVE_CLI = {("build", "--purge-logs"), ("maintenance", "purge_requests"), ("config", "reset"), ("users", "remove")}
_REBUILD_CLI = {("build", "--full"), ("build", "--reset"), ("build", "fts"), ("build", "vector"), ("build", "graph"), ("snapshot", "restore")}
_READ_CLI = {"query", "search", "health", "stats", "requests", "logs", "forensic", "graph", "entity", "docs", "arch", "time",
             "system", "corpus", "rules", "graph-rules", "pin", "embed", "memory", "evolve", "preset", "prompts", "tuning", "config", "models",
             "build", "precompute", "trial", "fusion", "eval", "mcp-source", "snapshot", "users", "security", "wiki", "apikey", "analyze",
             "sweep"}     # 스윕은 rerun 재생의 반복 — 재실행과 같은 read 등급 (색인·설정을 바꾸지 않는다)
_READ_CLI_ACTIONS = {  # (cmd, 첫 action) 이 이 집합이면 read.
    "build": {"status", "verify"}, "embed": {"report", "status", "runs"}, "memory": {"status", "episodes"}, "evolve": {"status", "review", "list", "feedback"},
    "preset": {"list", "show", "diff"}, "prompts": {"list", "show", "path"}, "tuning": {"show", "doc"}, "config": {"show", "paths"},
    "models": {"show", "test"}, "precompute": {"status"}, "trial": {"list", "compare", "report", "show"}, "mcp-source": {"list", "test", "enrich", "fetch"},
    "rules": {"show", "test", "explain", "stats", "path", "lint"},
    # 그래프 빌드 규칙: 보기·점검·시험은 read, 사전을 고치는 add-entity/add-alias/fill-defaults 는 edit (아래 _EDIT_CLI)
    "graph-rules": {"show", "types", "lint", "test", "path"}, "pin": {"list", "test"}, "corpus": {"lint", "types", "schema", "example", "lint-file", "stats"},
    "snapshot": {"list"}, "users": {"list"}, "security": {"show", "audit", "perms"}, "forensic": {"last", "list", "summary", "expect", "run"},
    "apikey": {"list"},
}
_RUN_CLI = {"query", "search", "eval", "health", "fusion"}     # query/search 는 read 로 취급 (아래)
# 등급별 CLI 액션 (cmd, action) → level
_EDIT_CLI = {("evolve", "apply"), ("evolve", "reject"), ("memory", "decay"), ("memory", "consolidate"), ("pin", "add"), ("pin", "remove"),
             ("rules", "add"), ("rules", "remove"), ("prompts", "reset"), ("tuning", "set"), ("tuning", "reset"), ("preset", "apply"), ("wiki", ""),
             ("trial", "delete"),
             # 그래프 규칙 편집 — 다음 `build graph` 에서 그래프가 달라진다 (색인 자체를 지우지는 않으므로 edit)
             ("graph-rules", "add-entity"), ("graph-rules", "add-alias"), ("graph-rules", "fill-defaults")}
_INDEX_CLI = {("build", ""), ("build", "run"), ("precompute", "run"), ("precompute", "clear"), ("precompute", "doc-vectors"), ("snapshot", "create"),
              ("snapshot", "prune"), ("mcp-source", "ingest"), ("embed", "clear-cache"), ("watch", ""), ("watch", "--once")}


def classify_cli(argv: List[str]) -> Tuple[str, str]:
    """CLI argv → (level, op). Web 콘솔(/api/cli)과 CLI 게이트가 같은 표를 쓴다."""
    if not argv:
        return "read", "cli"
    cmd = argv[0]
    rest = argv[1:]
    action = next((a for a in rest if not a.startswith("-")), "")
    if cmd in ("-h", "--help") or "--help" in argv or "-h" in argv:
        return "read", "cli:help"
    for c, a in _DESTRUCTIVE_CLI:
        if cmd == c and (a in rest or a == action):
            return "destructive", "cli:%s %s" % (cmd, a)
    for c, a in _REBUILD_CLI:
        if cmd == c and (a in rest or a == action):
            return "rebuild", "cli:%s %s" % (cmd, a)
    if cmd == "build" and action in ("", "run"):
        if "--fix" in rest:
            return "index", "cli:build verify --fix"
        return "index", "cli:build"
    if cmd == "build" and action == "verify":
        return ("index", "cli:build verify --fix") if "--fix" in rest else ("read", "cli:build verify")
    if cmd == "reset":
        # `reset <범위>` 는 기본이 **미리보기**(아무것도 바꾸지 않음) 이므로 read.
        # `--apply` 가 붙어야 실제로 지우며, 그때만 destructive 등급이 된다 (2026-09-19).
        return ("destructive", "cli:reset %s" % (action or "?")) if "--apply" in rest else ("read", "cli:reset preview")
    if cmd in ("users", "security", "config", "models", "apikey") and action not in _READ_CLI_ACTIONS.get(cmd, set()):
        if cmd == "security" and action == "perms" and len([a for a in rest if not a.startswith("-")]) <= 1:
            return "read", "cli:security perms"
        return "admin", "cli:%s %s" % (cmd, action)
    if cmd == "security" and action == "perms":
        sub = [a for a in rest if not a.startswith("-")]
        return ("read", "cli:security perms") if len(sub) <= 1 or sub[1] == "show" else ("admin", "cli:security perms " + sub[1])
    if cmd == "serve" or cmd == "mcp":
        return "admin", "cli:%s" % cmd
    if cmd == "watch":
        return "index", "cli:watch"
    if cmd in ("query", "search", "time"):
        return "read", "cli:%s" % cmd
    if cmd in ("eval", "fusion"):
        return "run", "cli:%s" % cmd
    if cmd == "health":
        return "read", "cli:health"
    if cmd == "trial" and action == "run":
        return "run", "cli:trial run"
    if (cmd, action) in _EDIT_CLI or (cmd == "wiki"):
        return "edit", "cli:%s %s" % (cmd, action)
    if (cmd, action) in _INDEX_CLI:
        return "index", "cli:%s %s" % (cmd, action)
    if cmd in _READ_CLI:
        acts = _READ_CLI_ACTIONS.get(cmd)
        if acts is None or action in acts:
            return "read", "cli:%s %s" % (cmd, action)
        return "edit", "cli:%s %s" % (cmd, action)
    if cmd == "maintenance":
        return "index", "cli:maintenance %s" % action
    # 2026-09-19: 아래 다섯은 표 어디에도 없어 fallback(edit=class2)으로 떨어지고 있었다. 명시한다.
    if cmd in ("inspect", "rerun"):
        # 질의 해부·단계 재실행 — 색인도 설정도 바꾸지 않는다. Web 의 /api/debug/query·/api/query/rerun 과 같은 read.
        return "read", "cli:%s" % cmd
    if cmd == "optimize":
        # 자료 묶음은 읽기지만 `--out` 은 **임의 경로에 파일을 쓴다** — 그 경우만 admin.
        return ("admin", "cli:optimize --out") if "--out" in rest else ("read", "cli:optimize")
    if cmd == "schedule":
        # 보는 것은 read, 바꾸거나 **실행**하는 것은 admin. POST /api/schedule 이 admin 인데 콘솔로 우회되면 안 된다.
        # add/run/trigger 는 scheduler.ACTION_TYPES 의 python·cli 를 통해 임의 실행에 이른다.
        return ("read", "cli:schedule %s" % action) if action in ("list", "show", "history", "validate") \
            else ("admin", "cli:schedule %s" % (action or "list"))
    if cmd == "server":
        # 상태 보기는 read, 제어(취소·제한·차단·세션·점검모드·kick)는 admin — /api/admin/server 와 같은 등급.
        return ("read", "cli:server %s" % action) if action in ("status", "requests", "circuits") \
            else ("admin", "cli:server %s" % (action or "status"))
    # 표에 없는 명령은 **admin** 으로 떨어뜨린다 (예전에는 edit=class2 였다).
    #
    # 왜 바꿨나: `/api/cli`(Web 콘솔)가 이 표의 결과를 그대로 쓰고 `run_captured` 가 `gate=False` 로 실행한다.
    # 그런데 `schedule` 이 어느 표에도 없어서 fallback 인 edit(class2)로 떨어졌고, 스케줄 동작에는
    # `python`·`cli` 타입이 있다(scheduler.ACTION_TYPES). 즉 class2 가
    # `POST /api/cli {"argv": "schedule add --task '{…\"action\":{\"type\":\"python\"…}}'"}` 한 번으로
    # **서버 프로세스 권한 임의 실행**에 이를 수 있었다. 정작 `POST /api/schedule` 은 admin 이므로,
    # 콘솔이 admin 게이트의 뒷문이 된 셈이다.
    #
    # 안전한 기본값은 "모르는 명령은 가장 높은 등급" 이다. 새 명령을 추가하면 위 표에 등급을 적어야 하고,
    # 적지 않으면 admin 만 쓸 수 있다 — 조용히 낮은 등급으로 열리는 것보다 낫다.
    # 개별 조정은 코드가 아니라 `security perms set 'cli:<명령> <액션>=<역할>'` 로 한다.
    return "admin", ("cli:%s %s" % (cmd, action)) if action else ("cli:%s" % cmd)


# ---------------------------------------------------------------- 요청 단위 overrides 화이트리스트
# 왜 여기 있나: `apply_overrides` 는 Settings 의 **모든** 필드를 받아들인다. 읽기 등급(익명 포함)이
# `{"overrides": {"openai_base_url": "http://attacker/v1"}}` 를 보내면 서버가 .env 의 PAT 를 그 주소로 보낸다
# (CODE_REVIEW_0917 §0 P0-1). 2026-09-18 에 Web 경로에 이 검사를 넣었는데, **MCP 경로에는 없었다** —
# `mcp._query_with` 가 도구 인자의 overrides 를 그대로 `request_scope` 에 넘겼고 `/mcp` 는 read 등급이라
# `anonymous_role` 이 켜져 있으면 무인증으로 같은 일이 가능했다 (2026-09-19 발견).
# 그래서 목록과 검사를 **창구가 아니라 auth 계층**으로 옮겼다. 질의를 실행하는 길이 새로 생겨도 같은 함수를 부르면 된다.
OVERRIDE_SAFE_KEYS = frozenset({
    "top_k_fts", "top_k_vector", "top_k_graph", "top_k_final", "graph_hops", "rrf_k",
    "rerank_candidates", "rerank_chunk_chars", "context_max_chars", "context_chunk_chars", "answer_max_tokens",
    "debug_level", "llm_provider", "llm_model", "llm_effort", "answer_effort", "llm_fallbacks", "embed_provider", "embed_model",
    "llm_timeout", "llm_retries", "llm_retry_backoff_s", "llm_retry_backoff", "llm_retry_backoff_max_s", "llm_budget_s",
    "llm_roles", "answer_mode", "output_mode", "query_cache_size", "llm_graph_budget", "llm_graph_min_chars",
    "tuning",   # {"tuning": {키: 값}} — 요청 단위 튜닝 오버레이 (Pipeline.request_scope 가 T.set 으로 검증·적용, 파일에는 남지 않는다)
})
#: llm_roles 안에서 누구나 바꿀 수 있는 속성 (URL 류는 역할 속성이 아니므로 여기 없음).
#
# 2026-09-19: `circuit_failures`·`circuit_cooldown_s` 를 뺐다. 전역 철자인 `llm_circuit_failures`·
# `llm_circuit_cooldown_s` 는 admin 전용인데 역할 철자는 누구나 쓸 수 있어서, **같은 손잡이가 철자에 따라
# 권한이 달랐다**. 회로 차단은 "이 질의를 어떻게 찾을까" 가 아니라 **죽은 프로바이더를 계속 때리지 않게
# 서버를 지키는 장치**다 — 요청자가 자기 요청만 예외로 만들 수 있으면 보호가 아니다.
# 다시 열려면 코드가 아니라 `security.json` 의 `overrides.allow_extra` 에 전역 키를 넣고 admin 이 쓴다.
OVERRIDE_ROLE_ATTRS = frozenset({"provider", "model", "effort", "timeout_s", "retries", "backoff_s", "backoff", "backoff_max_s", "budget_s",
                                 "max_tokens", "ensemble", "frequency_penalty", "presence_penalty", "repeat_penalty"})


def filter_overrides(ov: Optional[Dict[str, Any]], role: str, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """요청 단위 overrides 에서 이 역할이 쓸 수 없는 키를 거른다. 금지 키가 있으면 403(AuthError).

    조용히 버리지 않고 **이유를 돌려주는** 이유: 버리면 "설정이 안 먹는다" 로 보여 파이프라인 전체를 뒤지게 된다.
    admin 은 `deny` 목록 외 전부 허용. 목록 조정은 `security.json` 의
    `overrides.allow_extra`(추가 허용) / `overrides.deny`(admin 도 금지) — 코드 수정 없이.
    """
    if not ov:
        return {}
    from .config import Settings, Toggles, split_role_key
    sec = (cfg or {}).get("overrides") or {}
    allow_extra = {str(x) for x in (sec.get("allow_extra") or [])}
    deny = {str(x) for x in (sec.get("deny") or [])}
    is_admin = str(role or "") == "admin"
    # 아예 **없는 키**는 역할과 무관하게 거절한다. `apply_overrides` 는 모르는 키를 조용히 버리므로,
    # 오타 하나면 "설정이 안 먹는다" 가 되고 단서가 남지 않는다 (overrides.tuning 이 모르는 키를 400 으로
    # 돌려주는 것과 같은 규칙). admin 도 예외가 아니다 — 오타는 권한과 관계가 없다.
    unknown = [str(k) for k in ov
               if str(k) != "tuning" and str(k) not in Settings.__dataclass_fields__
               and str(k) not in Toggles.__dataclass_fields__ and not split_role_key(str(k))]
    if unknown:
        raise AuthError(400, "요청 단위 overrides 에 알 수 없는 키: %s — `config show --effective` 로 키 이름을 확인하세요 "
                             "(튜닝 값은 overrides.tuning 안에 넣습니다)" % ", ".join(sorted(set(unknown))[:8]))
    bad: List[str] = []
    for k in ov:
        ks = str(k)
        if ks in deny:
            bad.append(ks)
            continue
        if is_admin:
            continue
        if ks in Toggles.__dataclass_fields__ or ks in OVERRIDE_SAFE_KEYS or ks in allow_extra:
            continue
        rk = split_role_key(ks)
        if rk and rk[1] in OVERRIDE_ROLE_ATTRS:
            continue
        bad.append(ks)
    if not is_admin and isinstance(ov.get("llm_roles"), dict):
        for role_name, rcfg in ov["llm_roles"].items():
            for attr in (rcfg or {}) if isinstance(rcfg, dict) else []:
                if attr not in OVERRIDE_ROLE_ATTRS:
                    bad.append("llm_roles.%s.%s" % (role_name, attr))
    if bad:
        raise AuthError(403, "요청 단위 overrides 에 허용되지 않는 키: %s — URL·헤더·경로·서버 운영 설정은 admin 이 Settings 에서 저장해야 합니다 (security.json overrides.allow_extra 로 허용 가능)"
                        % ", ".join(sorted(set(bad))[:8]))
    return ov


_READ_POST = ("/api/query", "/api/query/rerun",   # 재실행은 질의와 같은 등급 — 색인을 바꾸지 않고 읽기만 한다
              "/api/sweep",                       # 스윕 = 재실행 N회 (llmwiki/sweep.py) — 재실행과 같은 등급
              "/api/search",
              "/api/debug/query",   # 질의 해부 — LLM 도 색인도 건드리지 않는 순수 읽기
              "/api/feedback", "/api/evolve/propose", "/api/auth/login", "/api/auth/logout", "/api/auth/password",
              "/api/forensic/expect", "/api/forensic/llm", "/api/analysis/insight", "/api/time", "/mcp",
              "/api/auth/preview",   # 자기 권한을 낮춰 보는 것뿐 (올릴 수 없다)
              "/api/profile")   # 자기 화면 설정만 저장 (서버 설정을 바꾸지 않음 — llmwiki/profiles.py)
_RUN_POST = ("/api/models/test", "/api/models/test_catalog", "/api/eval", "/api/fusion/compare", "/api/evolve/review")
_EDIT_POST = ("/api/memory", "/api/evolve/apply", "/api/evolve/reject",
              "/api/evolve/auto_apply",   # 여러 제안을 한 번에 적용 — 개별 apply 와 같은 등급
              "/api/wiki/page", "/api/rules", "/api/graph_rules", "/api/presets", "/api/prompts", "/api/pins",
              "/api/query_rules", "/api/tuning")
_ADMIN_POST = ("/api/config", "/api/models/set", "/api/agents", "/api/auth/users", "/api/security", "/api/apikeys",
               "/api/admin/server", "/api/schedule", "/api/models/catalog", "/api/env")   # /api/env: .env 다시 읽기 (값은 마스킹돼도 admin 전용)


def classify_api(method: str, path: str, body: Dict[str, Any]) -> Tuple[str, str]:
    """HTTP 요청 → (level, op). 새 엔드포인트를 추가하면 여기에도 등급을 적는다 (없는 POST 는 edit 으로 취급 — 안전한 기본값)."""
    body = body or {}
    act = str(body.get("action") or "")
    if method == "GET":
        if path in ("/api/auth/users", "/api/audit", "/api/security", "/api/apikeys", "/api/admin/server", "/api/env", "/api/docacl"):
            return "admin", path
        return "read", path
    if path == "/api/activity":
        return "read", "activity cancel"      # 본인 요청 취소 (서버가 소유자 검사; admin 은 전부)
    if path == "/api/models/automap":
        # 제안만 받는 것(apply 없음)은 연결 테스트와 같은 run 등급, 실제로 config.json 에 꽂는 것은 admin.
        return ("admin", "models automap --apply") if body.get("apply") else ("run", "models automap")
    if path == "/api/build":
        if body.get("purge_logs"):
            return "destructive", "build --full --purge-logs"
        if body.get("channel"):
            return "rebuild", "build channel %s" % body.get("channel")
        if body.get("full") or body.get("reset"):
            return "rebuild", "build --full/--reset"
        return "index", "build (incremental)"
    if path == "/api/maintenance":
        return ("destructive", "maintenance purge_requests") if act == "purge_requests" else ("index", "maintenance " + act)
    if path == "/api/reset":
        # 관리자 초기화 (2026-09-19). GET 미리보기는 위에서 read 로 떨어진다 — 실행만 destructive 다.
        return "destructive", "reset %s" % (body.get("scope") or "?")
    if path == "/api/cli":
        argv = body.get("argv") or []
        if isinstance(argv, str):
            import shlex
            try:
                argv = shlex.split(argv, posix=True)
            except ValueError:
                argv = argv.split()
        lvl, op = classify_cli(list(argv))
        # 콘솔 자체는 run 등급 이상 (viewer 에게 자유 명령 입력창은 주지 않는다)
        return (lvl if LEVEL_ORDER[lvl] >= LEVEL_ORDER["run"] else "run"), op
    if path == "/api/snapshot":
        return ("rebuild", "snapshot restore") if act == "restore" else ("index", "snapshot " + (act or "create"))
    if path in _READ_POST:
        return "read", path
    if path in _RUN_POST:
        return "run", path
    if path == "/api/trials":
        return ("edit", "trials delete") if act == "delete" else ("run", "trials run")
    if path == "/api/watch":
        if act == "scan":
            return "read", "watch scan"
        return ("admin", "watch %s (save)" % act) if body.get("save") else ("index", "watch " + act)
    if path == "/api/docacl":
        # 문서 접근 제어 규칙은 보안 설정이다. 영향 확인(check)도 "어떤 문서가 가려지는가" 를 그대로 보여 주므로 admin.
        return "admin", "docacl " + (act or "check")
    if path == "/api/precompute":
        return "index", "precompute " + (act or "run")
    if path == "/api/collab":
        # 채팅·게시는 로그인한 사람이면 누구나(read). 남의 글 삭제·채팅 비우기만 admin.
        # 내 글 삭제는 action=remove_mine (서버가 작성자를 확인한다).
        if act in ("remove", "clear"):
            return "admin", "collab admin"
        return "read", "collab " + (act or "say")
    if path == "/api/config" and act == "reload":
        # 파일을 쓰지 않고 디스크의 config.json 을 다시 읽기만 한다 — 그래도 서버 전체 동작이 바뀌므로
        # 저장(admin)과 같은 등급에 두되 op 를 따로 둬서 조직이 permissions.ops 로 낮출 수 있게 한다.
        return "admin", "config reload"
    if path in _ADMIN_POST:
        return "admin", path + (" " + act if act else "")
    if path == "/api/mcp_sources":
        if act == "save":
            return "admin", "mcp_sources save"
        return ("index", "mcp_sources ingest") if act == "ingest" else ("run", "mcp_sources " + act)
    if path == "/api/build/verify":
        return ("index", "build verify --fix") if body.get("fix") else ("read", "build verify")
    if path in _EDIT_POST:
        if path == "/api/pins" and act == "test":
            return "read", "pins test"
        if path == "/api/presets" and act == "apply" and not body.get("save"):
            return "read", "presets apply (memory only)"
        if path == "/api/query_rules" and act == "test":
            return "read", "query_rules test"
        return "edit", path + (" " + act if act else "")
    return "edit", path


# ---------------------------------------------------------------- Auth
class User:
    __slots__ = ("name", "role", "via", "issued", "preview_of")

    def __init__(self, name: str, role: str, via: str, issued: float = 0.0):
        self.name, self.role, self.via, self.issued = name, norm_role(role), via, issued
        self.preview_of = ""      # 권한 미리보기로 낮춘 경우 원래 역할

    def to_dict(self) -> Dict[str, Any]:
        d = {"name": self.name, "role": self.role, "via": self.via, "rank": RANK[self.role]}
        if self.preview_of:
            d["preview_of"] = self.preview_of
        return d

    @property
    def anonymous(self) -> bool:
        return self.via == "anon"


COOKIE = "llmwiki_session"
OIDC_COOKIE = "llmwiki_oidc"
# 권한 미리보기: admin 이 "일반 사용자에게는 어떻게 보이나" 를 확인할 때 쓴다.
# **낮추기만** 하므로 서명하지 않는다 — 위조해도 자기 권한을 더 줄일 뿐 올릴 수 없다.
PREVIEW_COOKIE = "llmwiki_preview"


def preview_cookie(role: str, https: bool = False) -> str:
    if not role:
        return "%s=; Path=/; SameSite=Lax; Max-Age=0" % PREVIEW_COOKIE
    return "%s=%s; Path=/; SameSite=Lax; Max-Age=%d%s" % (PREVIEW_COOKIE, norm_role(role), 12 * 3600,
                                                          "; Secure" if https else "")


def preview_role(cookie_header: str) -> str:
    r = _cookie(cookie_header or "", PREVIEW_COOKIE)
    return norm_role(r) if r in ROLES else ""


def apply_preview(user: Optional[User], cookie_header: str) -> Optional[User]:
    """미리보기 역할이 지금 역할보다 낮으면 그 역할로 낮춘 사용자를 돌려준다. 올리지는 않는다."""
    if user is None:
        return None
    want = preview_role(cookie_header)
    if not want or RANK.get(want, 0) >= RANK.get(user.role, 0):
        return user
    u = User(user.name, want, user.via, user.issued)
    u.preview_of = user.role      # UI 가 '미리보기 중' 배너를 띄우는 근거
    return u
PUBLIC_PATHS = ("/login", "/static/", "/auth/sso/", "/api/auth/login", "/api/auth/me", "/api/auth/logout", "/api/progress", "/favicon.ico")


class Auth:
    """서버 전역 1개. security.json 이 바뀌면 reload()."""

    def __init__(self, settings: Any, host: str = "127.0.0.1"):
        self.s = settings
        self.host = host
        self.cfg: Dict[str, Any] = {}
        self.signer = Signer(_secret(settings.data_dir))
        self._oidc_meta: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()
        self.reload()

    # ---- config ----
    def reload(self) -> None:
        with self._lock:
            self.cfg = load_security()
            self._oidc_meta = None

    @property
    def mode(self) -> str:
        m = str(self.cfg.get("mode") or "auto").lower()
        if m == "auto":
            return "off" if self.host in ("127.0.0.1", "localhost", "::1") else "on"
        return "on" if m in ("on", "true", "1") else "off"

    @property
    def anonymous_role(self) -> str:
        """미로그인 접속자에게 줄 역할 ('' = 로그인 필수)."""
        return norm_role(self.cfg.get("anonymous_role") or "", "") if self.cfg.get("anonymous_role") else ""

    def has_any_login(self) -> bool:
        return bool(self.cfg.get("users")) or bool((self.cfg.get("sso") or {}).get("enabled")) or bool(self.cfg.get("api_keys"))

    def public_info(self) -> Dict[str, Any]:
        sso = self.cfg.get("sso") or {}
        return {"mode": self.mode, "local": bool((self.cfg.get("local") or {}).get("enabled", True)) and bool(self.cfg.get("users")),
                "sso": bool(sso.get("enabled")), "sso_type": sso.get("type", "oidc"), "sso_label": sso.get("button_label") or "SSO 로그인",
                "anonymous_role": self.anonymous_role,
                "confirm_phrase": (self.cfg.get("destructive") or {}).get("confirm_phrase") or "DELETE INDEX",
                "require_reauth": bool((self.cfg.get("destructive") or {}).get("require_reauth", True)),
                "warn_confirm": bool((self.cfg.get("warn") or {}).get("confirm", True)), "roles": list(ROLES), "role_labels": ROLE_LABEL,
                "levels": LEVEL_LABEL, "level_order": list(LEVELS), "permissions": self.permissions()}

    # ---- permissions ----
    def permissions(self) -> Dict[str, Any]:
        p = self.cfg.get("permissions") or {}
        return {"levels": {lv: norm_role((p.get("levels") or {}).get(lv), DEFAULT_LEVEL_ROLE[lv]) for lv in LEVELS},
                "ops": {k: norm_role(v) for k, v in (p.get("ops") or {}).items() if not str(k).startswith("_")}}

    def min_role(self, level: str, op: str = "") -> str:
        """이 작업을 하려면 최소 어떤 역할이어야 하는가: ops 오버라이드(정확 일치 → 접두 일치) → 등급 기본값."""
        level = norm_level(level)
        p = self.permissions()
        ops = p["ops"]
        if op:
            if op in ops:
                return ops[op]
            best = ""
            for k, v in ops.items():
                if k.endswith("*") and op.startswith(k[:-1]) and len(k) > len(best):
                    best = k
            if best:
                return ops[best]
        return p["levels"].get(level, DEFAULT_LEVEL_ROLE[level])

    def set_permission(self, key: str, role: str) -> Dict[str, Any]:
        """key 가 등급 이름이면 levels, 아니면 ops 오버라이드. role='' 이면 오버라이드 제거(등급은 기본값)."""
        p = self.cfg.setdefault("permissions", {"levels": dict(DEFAULT_LEVEL_ROLE), "ops": {}})
        key = str(key or "").strip()
        if not key:
            raise ValueError("key 필요")
        lv = norm_level(key) if key in LEVELS or key in LEVEL_ALIASES else ""
        if lv:
            p.setdefault("levels", {})[lv] = norm_role(role, DEFAULT_LEVEL_ROLE[lv]) if role else DEFAULT_LEVEL_ROLE[lv]
        else:
            ops = p.setdefault("ops", {})
            if role:
                if norm_role(role, "") == "" and str(role).strip().lower() not in RANK:
                    raise ValueError("role 은 %s 중 하나" % "/".join(ROLES))
                ops[key] = norm_role(role)
            else:
                ops.pop(key, None)
        save_security(self.cfg)
        return self.permissions()

    def set_permissions(self, perms: Dict[str, Any]) -> Dict[str, Any]:
        """Web 보안 탭: 전체 교체 {"levels": {...}, "ops": {...}}."""
        levels = {lv: norm_role((perms.get("levels") or {}).get(lv), DEFAULT_LEVEL_ROLE[lv]) for lv in LEVELS}
        ops = {}
        for k, v in (perms.get("ops") or {}).items():
            k = str(k).strip()
            if k and not k.startswith("_") and str(v).strip():
                ops[k] = norm_role(v)
        self.cfg["permissions"] = {"_comment": DEFAULT_SECURITY["permissions"]["_comment"], "levels": levels, "ops": ops}
        save_security(self.cfg)
        return self.permissions()

    def allowed(self, role: str, level: str, op: str = "") -> bool:
        return RANK[norm_role(role)] >= RANK[self.min_role(level, op)]

    # ---- users ----
    def list_users(self) -> List[Dict[str, Any]]:
        return [{"name": k, "role": norm_role(v.get("role", "viewer")), "display": v.get("name", ""), "created": v.get("created"),
                 "has_password": bool(v.get("pw"))} for k, v in sorted((self.cfg.get("users") or {}).items())]

    def add_user(self, name: str, password: Optional[str], role: str = "viewer", display: str = "") -> None:
        name = (name or "").strip()
        if not name or any(c in name for c in " /\\\"'<>"):
            raise ValueError("잘못된 사용자 id")
        if str(role or "").strip().lower() not in RANK and str(role or "").strip().lower() not in ROLE_ALIASES:
            raise ValueError("role 은 %s 중 하나" % "/".join(ROLES))
        role = norm_role(role)
        minlen = int((self.cfg.get("local") or {}).get("min_password_len", 8) or 0)
        if password is not None and len(password) < minlen:
            raise ValueError("비밀번호는 %d자 이상" % minlen)
        users = self.cfg.setdefault("users", {})
        rec = users.get(name) or {"created": time.time()}
        rec["role"] = role
        if display:
            rec["name"] = display
        if password is not None:
            rec["pw"] = hash_password(password)
        users[name] = rec
        save_security(self.cfg)

    def remove_user(self, name: str) -> bool:
        users = self.cfg.setdefault("users", {})
        if name not in users:
            return False
        del users[name]
        save_security(self.cfg)
        return True

    def set_role(self, name: str, role: str) -> None:
        if str(role or "").strip().lower() not in RANK and str(role or "").strip().lower() not in ROLE_ALIASES:
            raise ValueError("role 은 %s 중 하나" % "/".join(ROLES))
        users = self.cfg.setdefault("users", {})
        rec = users.setdefault(name, {"created": time.time()})
        rec["role"] = norm_role(role)
        save_security(self.cfg)

    def set_password(self, name: str, password: str) -> None:
        users = self.cfg.setdefault("users", {})
        if name not in users:
            raise ValueError("no such user")
        minlen = int((self.cfg.get("local") or {}).get("min_password_len", 8) or 0)
        if len(password) < minlen:
            raise ValueError("비밀번호는 %d자 이상" % minlen)
        users[name]["pw"] = hash_password(password)
        save_security(self.cfg)

    def check_password(self, name: str, password: str) -> bool:
        rec = (self.cfg.get("users") or {}).get(name) or {}
        return bool(rec.get("pw")) and verify_password(password or "", rec["pw"])

    def login_local(self, name: str, password: str) -> Optional[User]:
        if not (self.cfg.get("local") or {}).get("enabled", True):
            return None
        if self.check_password(name, password):
            rec = self.cfg["users"][name]
            return User(name, rec.get("role", "viewer"), "local", time.time())
        return None

    # ---- api keys ----
    def list_api_keys(self) -> List[Dict[str, Any]]:
        return [{"id": k, "name": v.get("name", ""), "role": norm_role(v.get("role", "viewer")), "created": v.get("created"), "last_used": v.get("last_used"),
                 "note": v.get("note", "")} for k, v in sorted((self.cfg.get("api_keys") or {}).items())]

    def add_api_key(self, name: str, role: str = "viewer", note: str = "") -> Dict[str, Any]:
        """토큰은 이 호출의 반환값에서만 볼 수 있다 (파일에는 해시)."""
        if str(role or "").strip().lower() not in RANK and str(role or "").strip().lower() not in ROLE_ALIASES:
            raise ValueError("role 은 %s 중 하나" % "/".join(ROLES))
        tok, kid, h = new_api_key()
        keys = self.cfg.setdefault("api_keys", {})
        keys[kid] = {"name": (name or "").strip() or kid, "role": norm_role(role), "hash": h, "created": time.time(), "note": note or ""}
        save_security(self.cfg)
        return {"id": kid, "token": tok, "name": keys[kid]["name"], "role": keys[kid]["role"]}

    def remove_api_key(self, kid: str) -> bool:
        keys = self.cfg.setdefault("api_keys", {})
        if kid not in keys:
            hit = next((k for k, v in keys.items() if v.get("name") == kid), None)
            if not hit:
                return False
            kid = hit
        del keys[kid]
        save_security(self.cfg)
        return True

    def user_from_api_key(self, token: str) -> Optional[User]:
        parsed = parse_api_key(token)
        if not parsed:
            return None
        kid, h = parsed
        rec = (self.cfg.get("api_keys") or {}).get(kid)
        if not rec or not hmac.compare_digest(str(rec.get("hash", "")), h):
            return None
        rec["last_used"] = time.time()
        return User("key:" + str(rec.get("name") or kid), rec.get("role", "viewer"), "apikey", time.time())

    # ---- sessions ----
    def make_cookie(self, user: User, https: bool = False, ip: str = "", agent: str = "") -> str:
        hours = float(self.cfg.get("session_hours") or 12)
        sid = secrets.token_hex(8)
        tok = self.signer.sign({"u": user.name, "r": user.role, "v": user.via, "iat": time.time(), "exp": time.time() + hours * 3600, "id": sid})
        try:
            sessions().register(sid, user, ip, agent, hours)
        except Exception:
            pass
        secure = self.cfg.get("secure_cookie")
        flag = "; Secure" if (secure is True or (secure == "auto" and https)) else ""
        return "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d%s" % (COOKIE, tok, int(hours * 3600), flag)

    @staticmethod
    def clear_cookie() -> str:
        return "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % COOKIE

    def end_session(self, cookie_header: str) -> None:
        tok = _cookie(cookie_header, COOKIE)
        data = self.signer.verify(tok) if tok else None
        if data and data.get("id"):
            try:
                sessions().revoke(str(data["id"]))
            except Exception:
                pass

    def user_from_cookie(self, cookie_header: str) -> Optional[User]:
        tok = _cookie(cookie_header, COOKIE)
        if not tok:
            return None
        data = self.signer.verify(tok)
        if not data:
            return None
        name, role = data.get("u"), data.get("r", "viewer")
        # 서버 측 세션 레지스트리(server.json sessions.enforce) — 강제 로그아웃·동시 세션 수 제한
        try:
            reg = sessions()
            if reg.enforce and data.get("id") and not reg.touch(str(data["id"])):
                return None
        except Exception:
            pass
        # 로컬 사용자의 역할이 그 사이 바뀌었으면 현재 값을 따른다 (강등 즉시 반영)
        rec = (self.cfg.get("users") or {}).get(name)
        if rec:
            role = rec.get("role", role)
        return User(name, role, data.get("v", "local"), float(data.get("iat") or 0))

    def user_from_headers(self, headers: Any, client_ip: str) -> Optional[User]:
        """type=header SSO: 신뢰하는 리버스 프록시가 넣어 준 사용자/그룹 헤더."""
        sso = self.cfg.get("sso") or {}
        if not (sso.get("enabled") and sso.get("type") == "header"):
            return None
        h = sso.get("header") or {}
        if client_ip not in (h.get("trusted_proxies") or []):
            return None
        name = (headers.get(h.get("user") or "X-Forwarded-User") or "").strip()
        if not name:
            return None
        groups = [g.strip() for g in (headers.get(h.get("groups") or "X-Forwarded-Groups") or "").split(",") if g.strip()]
        return User(name, self._role_for_sso(name, groups, ""), "sso", time.time())

    def _role_for_sso(self, name: str, groups: List[str], email: str) -> str:
        sso = self.cfg.get("sso") or {}
        rec = (self.cfg.get("users") or {}).get(name) or ((self.cfg.get("users") or {}).get(email) if email else None)
        if rec and rec.get("role"):
            return norm_role(rec["role"])          # 로컬 목록에 적힌 역할이 IdP 그룹보다 우선 (특정인 지정)
        rm = sso.get("role_map") or {}
        gs = set(groups or [])
        for role in reversed(ROLES):     # 높은 역할부터
            if role == "viewer":
                continue
            names = set(rm.get(role) or [])
            if role == "class1":
                names |= set(rm.get("operator") or [])
            if gs & names:
                return role
        return norm_role(sso.get("default_role") or "viewer")

    # ---- OIDC ----
    def oidc_enabled(self) -> bool:
        sso = self.cfg.get("sso") or {}
        return bool(sso.get("enabled") and sso.get("type", "oidc") == "oidc" and sso.get("issuer") and sso.get("client_id"))

    def _ssl_ctx(self) -> Optional[ssl.SSLContext]:
        ca = (self.cfg.get("sso") or {}).get("ca_bundle") or ""
        return ssl.create_default_context(cafile=ca) if ca else None

    def _http_json(self, url: str, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None, timeout: int = 15) -> Dict[str, Any]:
        req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
        with urllib.request.urlopen(req, timeout=timeout, context=self._ssl_ctx()) as r:
            return json.loads(r.read().decode("utf-8"))

    def oidc_meta(self) -> Dict[str, Any]:
        if self._oidc_meta is None:
            issuer = (self.cfg["sso"]["issuer"] or "").rstrip("/")
            self._oidc_meta = self._http_json(issuer + "/.well-known/openid-configuration")
        return self._oidc_meta

    def oidc_start(self, next_url: str = "/") -> Tuple[str, str]:
        """(redirect_url, state_cookie_header)"""
        sso = self.cfg["sso"]
        meta = self.oidc_meta()
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        q = {"response_type": "code", "client_id": sso["client_id"], "redirect_uri": sso["redirect_uri"], "scope": sso.get("scopes") or "openid profile email",
             "state": state, "nonce": nonce}
        url = meta["authorization_endpoint"] + ("&" if "?" in meta["authorization_endpoint"] else "?") + urllib.parse.urlencode(q)
        tok = self.signer.sign({"state": state, "nonce": nonce, "next": next_url[:200], "exp": time.time() + 600})
        return url, "%s=%s; Path=/auth/sso/; HttpOnly; SameSite=Lax; Max-Age=600" % (OIDC_COOKIE, tok)

    def oidc_callback(self, query: Dict[str, str], cookie_header: str) -> Tuple[User, str]:
        """code+state → 토큰 교환 → claims 검증 → User. (User, next_url)"""
        sso = self.cfg["sso"]
        st = self.signer.verify(_cookie(cookie_header, OIDC_COOKIE) or "")
        if not st or not query.get("state") or not hmac.compare_digest(str(st.get("state")), str(query.get("state"))):
            raise AuthError(400, "SSO state 불일치 (로그인 다시 시도)")
        if query.get("error"):
            raise AuthError(400, "SSO 오류: %s %s" % (query.get("error"), query.get("error_description", "")))
        meta = self.oidc_meta()
        secret = os.environ.get(sso.get("client_secret_env") or "LLMWIKI_OIDC_CLIENT_SECRET", "")
        form = {"grant_type": "authorization_code", "code": query.get("code", ""), "redirect_uri": sso["redirect_uri"], "client_id": sso["client_id"]}
        if secret:
            form["client_secret"] = secret
        try:
            tok = self._http_json(meta["token_endpoint"], urllib.parse.urlencode(form).encode("ascii"),
                                  {"content-type": "application/x-www-form-urlencoded", "accept": "application/json"})
        except urllib.error.HTTPError as e:
            raise AuthError(502, "SSO 토큰 교환 실패: HTTP %s %s" % (e.code, e.read().decode("utf-8", "ignore")[:200]))
        claims: Dict[str, Any] = {}
        idt = tok.get("id_token")
        if idt:
            claims = _jwt_payload(idt)
            _verify_jwt_signature_if_possible(idt, meta, self._ssl_ctx())
            iss = str(claims.get("iss", "")).rstrip("/")
            if iss != str(sso["issuer"]).rstrip("/"):
                raise AuthError(401, "SSO issuer 불일치: %s" % iss)
            aud = claims.get("aud")
            if not (aud == sso["client_id"] or (isinstance(aud, list) and sso["client_id"] in aud)):
                raise AuthError(401, "SSO audience 불일치")
            if claims.get("exp") and time.time() > float(claims["exp"]) + 60:
                raise AuthError(401, "SSO id_token 만료")
            if claims.get("nonce") and not hmac.compare_digest(str(claims["nonce"]), str(st.get("nonce"))):
                raise AuthError(401, "SSO nonce 불일치")
        if tok.get("access_token") and meta.get("userinfo_endpoint"):
            try:
                claims.update(self._http_json(meta["userinfo_endpoint"], headers={"authorization": "Bearer " + tok["access_token"]}))
            except Exception:
                pass
        if not claims:
            raise AuthError(401, "SSO 응답에 사용자 정보가 없습니다")
        name = str(claims.get(sso.get("username_claim") or "preferred_username") or claims.get("email") or claims.get("sub") or "").strip()
        email = str(claims.get(sso.get("email_claim") or "email") or "")
        if not name:
            raise AuthError(401, "SSO claim 에 사용자 id 가 없습니다 (username_claim 확인)")
        doms = [d.lower() for d in (sso.get("allowed_domains") or [])]
        if doms and email and email.rsplit("@", 1)[-1].lower() not in doms:
            raise AuthError(403, "허용되지 않은 도메인: %s" % email)
        groups = claims.get(sso.get("groups_claim") or "groups") or []
        if isinstance(groups, str):
            groups = [g for g in groups.replace(";", ",").split(",") if g]
        return User(name, self._role_for_sso(name, list(groups), email), "sso", time.time()), str(st.get("next") or "/")

    # ---- authorization ----
    def identify(self, headers: Any, client_ip: str) -> Optional[User]:
        """헤더 SSO → Bearer API 키 → 세션 쿠키 → (anonymous_role) 게스트. mode=off 면 항상 로컬 admin."""
        if self.mode == "off":
            return User("local", "admin", "off", time.time())
        u = self.user_from_headers(headers, client_ip)
        if u:
            return u
        authz = headers.get("Authorization") or headers.get("authorization") or ""
        if authz.lower().startswith("bearer "):
            tok = authz[7:].strip()
            u = self.user_from_api_key(tok)
            if u:
                return u
            if tok.startswith(API_KEY_PREFIX):
                # 우리 형식(lwk_)의 키를 제시했는데 맞지 않음 → 게스트로 조용히 강등하지 않고 401 (폐기된 키·오타를 호출측이 알 수 있게)
                raise AuthError(401, "API 키가 유효하지 않습니다 (폐기되었거나 잘못된 키)", need={"level": "read", "op": "apikey"})
        u = self.user_from_cookie(headers.get("Cookie") or "")
        if u:
            return u
        if self.anonymous_role:
            return User("guest", self.anonymous_role, "anon", time.time())
        return None

    def authorize(self, user: Optional[User], method: str, path: str, body: Dict[str, Any], headers: Any = None, host: str = "") -> Tuple[str, str]:
        """통과하면 (level, op) 반환, 아니면 AuthError. headers/host 는 CSRF 검사용."""
        level, op = classify_api(method, path, body or {})
        if method == "POST" and headers is not None:
            self._csrf_check(headers, host)
        if user is None:
            raise AuthError(401, "로그인이 필요합니다", redirect="/login")
        need_role = self.min_role(level, op)
        if RANK[user.role] < RANK[need_role]:
            if user.anonymous:
                raise AuthError(401, "로그인이 필요합니다: '%s' 작업(%s)은 %s 이상 (게스트 %s)" % (op, LEVEL_LABEL[level], need_role, user.role),
                                need={"level": level, "op": op, "role": need_role}, redirect="/login")
            raise AuthError(403, "권한 부족: '%s' 작업(%s)은 %s 이상만 할 수 있습니다 (현재 %s)" % (op, LEVEL_LABEL[level], need_role, user.role),
                            need={"level": level, "op": op, "role": need_role})
        body = body or {}
        d = self.cfg.get("destructive") or {}
        need: Dict[str, Any] = {"level": level, "op": op, "label": LEVEL_LABEL[level], "role": need_role}
        if method != "POST":
            return level, op          # GET 은 읽기 — 역할 검사만 (admin 전용 조회 포함)
        how = LEVEL_CONFIRM[level]
        if how == "confirm":
            if self.mode == "on" and (self.cfg.get("warn") or {}).get("confirm", True) and not body.get("_confirm"):
                raise AuthError(428, "확인이 필요합니다: %s" % op, need=dict(need, confirm=True))
        elif how == "phrase":
            phrase = d.get("confirm_phrase") or "DELETE INDEX"
            reauth = bool(d.get("require_reauth", True)) and user.via == "local"
            missing: Dict[str, Any] = {}
            if not body.get("_confirm"):
                missing["confirm"] = True
            if str(body.get("_phrase") or "").strip() != phrase:
                missing["phrase"] = phrase
            if reauth and not self.check_password(user.name, str(body.get("_password") or "")):
                missing["password"] = True
            if missing:
                raise AuthError(428, "%s 작업 확인이 필요합니다: %s" % ("파괴적" if level == "destructive" else "리빌드", op),
                                need=dict(need, **missing, snapshot=bool(d.get("snapshot_before", True))))
        return level, op

    def _csrf_check(self, headers: Any, host: str) -> None:
        origin = headers.get("Origin") or ""
        if origin:
            try:
                oh = urllib.parse.urlparse(origin).netloc
            except Exception:
                oh = ""
            if oh and host and oh.split(":")[0] not in (host.split(":")[0], "localhost", "127.0.0.1") and oh != host:
                raise AuthError(403, "cross-origin 요청 차단 (Origin %s)" % origin)
        if self.mode == "on" and not headers.get("X-Requested-With") and not (headers.get("Content-Type") or "").startswith("application/json"):
            raise AuthError(403, "CSRF 보호: X-Requested-With 헤더 또는 JSON 본문이 필요합니다")

    # ---- audit ----
    def audit(self, user: Optional[User], op: str, level: str, ok: bool, ip: str = "", detail: Any = None, error: str = "") -> None:
        write_audit(user, op, level, ok, ip, detail, error)

    @staticmethod
    def audit_tail(n: int = 100) -> List[Dict[str, Any]]:
        p = os.path.join(path_for("logs_dir"), "audit.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-n:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
        return out


# ---------------------------------------------------------------- 서버 측 세션 레지스트리 (2026-09-15)
class SessionRegistry:
    """로그인 세션(쿠키 id) 을 data/sessions.json 에 기억한다. server.json sessions: enforce(검사 여부)·max_per_user(초과 시 가장 오래된 세션 만료)·idle_timeout_min.
    enforce=False 여도 목록은 기록되어 admin 이 누가 언제 어디서 로그인했는지 볼 수 있다(강제 로그아웃은 enforce=True 일 때만 효력)."""

    def __init__(self, path: str, cfg: Optional[Dict[str, Any]] = None):
        self.path = path
        self.cfg = dict(cfg or {})
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    @property
    def enforce(self) -> bool:
        return bool(self.cfg.get("enforce"))

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            self._data = atomicio.read_json(self.path, {}) or {}
        except Exception:
            self._data = {}

    def _save(self) -> None:
        try:
            atomicio.write_json(self.path, self._data, indent=None)
        except Exception:
            pass

    def _expire(self, now: float) -> None:
        idle = float(self.cfg.get("idle_timeout_min") or 0) * 60
        for sid in [k for k, v in self._data.items() if (v.get("exp") and now > float(v["exp"])) or (idle and now - float(v.get("last_seen") or 0) > idle)]:
            self._data.pop(sid, None)

    def register(self, sid: str, user: "User", ip: str, agent: str, hours: float) -> None:
        with self._lock:
            self._load()
            now = time.time()
            self._expire(now)
            self._data[sid] = {"sid": sid, "user": user.name, "role": user.role, "via": user.via, "ip": ip, "agent": (agent or "")[:120],
                               "created": now, "last_seen": now, "exp": now + hours * 3600}
            mx = int(self.cfg.get("max_per_user") or 0)
            if mx > 0:
                mine = sorted([v for v in self._data.values() if v["user"] == user.name], key=lambda v: v["created"])
                for v in mine[:-mx]:
                    self._data.pop(v["sid"], None)
            self._save()

    def touch(self, sid: str) -> bool:
        with self._lock:
            self._load()
            v = self._data.get(sid)
            if not v:
                return False
            now = time.time()
            idle = float(self.cfg.get("idle_timeout_min") or 0) * 60
            if (v.get("exp") and now > float(v["exp"])) or (idle and now - float(v.get("last_seen") or 0) > idle):
                self._data.pop(sid, None)
                self._save()
                return False
            if now - float(v.get("last_seen") or 0) > 30:
                v["last_seen"] = now
                self._save()
            return True

    def revoke(self, sid: str) -> bool:
        with self._lock:
            self._load()
            ok = self._data.pop(sid, None) is not None
            if ok:
                self._save()
            return ok

    def revoke_user(self, user: str) -> int:
        with self._lock:
            self._load()
            sids = [k for k, v in self._data.items() if v.get("user") == user]
            for k in sids:
                self._data.pop(k, None)
            if sids:
                self._save()
            return len(sids)

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._load()
            self._expire(time.time())
            return sorted(self._data.values(), key=lambda v: v.get("last_seen") or 0, reverse=True)


_SESSIONS: Optional[SessionRegistry] = None


def sessions() -> SessionRegistry:
    """전역 세션 레지스트리 (server.json sessions 설정, data/sessions.json)."""
    global _SESSIONS
    if _SESSIONS is None:
        cfg: Dict[str, Any] = {}
        try:
            from . import reqmgr as _rq
            cfg = dict((_rq.load_config().get("sessions") or {}))
        except Exception:
            pass
        data_dir = os.path.join(ROOT, "data")
        try:
            from .config import load_settings
            data_dir = load_settings().data_dir
        except Exception:
            pass
        _SESSIONS = SessionRegistry(os.path.join(data_dir, "sessions.json"), cfg)
    return _SESSIONS


def reset_sessions() -> None:
    global _SESSIONS
    _SESSIONS = None


_AUDIT_LOCK = threading.Lock()
_AUDIT_CFG: Dict[str, Any] = {"max_mb": 20.0, "backups": 5}   # config.json audit_max_mb / audit_backups (logging_setup.setup_from_settings 가 configure_audit 로 반영)


def configure_audit(max_mb: Any = 20, backups: Any = 5) -> None:
    """audit.jsonl 로테이션 기준을 바꾼다. max_mb<=0 이면 로테이션 없음."""
    try:
        _AUDIT_CFG["max_mb"] = float(max_mb or 0)
    except (TypeError, ValueError):
        _AUDIT_CFG["max_mb"] = 20.0
    try:
        _AUDIT_CFG["backups"] = max(0, int(backups or 0))
    except (TypeError, ValueError):
        _AUDIT_CFG["backups"] = 5


def _rotate_audit(path: str, backups: int) -> None:
    """audit.jsonl → .1, .1 → .2 … (backups 개까지). backups=0 이면 비운다. RotatingFileHandler.doRollover 와 같은 규칙."""
    if backups <= 0:
        open(path, "w", encoding="utf-8").close()
        return
    last = "%s.%d" % (path, backups)
    if os.path.exists(last):
        os.remove(last)
    for i in range(backups - 1, 0, -1):
        src = "%s.%d" % (path, i)
        if os.path.exists(src):
            os.replace(src, "%s.%d" % (path, i + 1))
    os.replace(path, path + ".1")


def write_audit(user: Optional[User], op: str, level: str, ok: bool, ip: str = "", detail: Any = None, error: str = "") -> None:
    """logs/audit.jsonl 한 줄. Web(Auth.audit) 과 CLI 게이트가 함께 쓴다. audit_max_mb 를 넘으면 audit.jsonl.1 … 로 로테이션(audit_backups 개)."""
    rec = {"ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S"), "user": user.name if user else None, "role": user.role if user else None,
           "via": user.via if user else None, "ip": ip, "op": op, "level": level, "ok": ok}
    if detail is not None:
        rec["detail"] = _sanitize(detail)
    if error:
        rec["error"] = error[:300]
    try:
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        d = path_for("logs_dir")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "audit.jsonl")
        max_bytes = int(float(_AUDIT_CFG.get("max_mb") or 0) * 1024 * 1024)
        with _AUDIT_LOCK:
            if max_bytes > 0 and os.path.exists(p) and os.path.getsize(p) + len(line) > max_bytes:
                try:
                    _rotate_audit(p, int(_AUDIT_CFG.get("backups") or 0))
                except OSError:
                    pass
            with open(p, "ab") as f:
                f.write(line)
    except Exception:
        pass


# ---------------------------------------------------------------- CLI 게이트
def cli_actor(argv_user: Optional[str] = None, argv_password: Optional[str] = None, settings: Any = None) -> Tuple[str, str, str]:
    """CLI 실행자의 (name, role, via). 순서: --user/LLMWIKI_USER 로컬 계정 인증 → LLMWIKI_API_KEY → security.json cli.default_role."""
    cfg = load_security()
    name = argv_user or os.environ.get("LLMWIKI_USER") or ""
    if name:
        pw = argv_password if argv_password is not None else os.environ.get("LLMWIKI_PASSWORD")
        import sys as _sys
        if pw is None and (not getattr(_sys, "stdin", None) or not _sys.stdin.isatty()):
            raise AuthError(401, "비대화형 실행: LLMWIKI_PASSWORD 환경변수로 비밀번호를 주세요")
        if pw is None:
            import getpass
            try:
                pw = getpass.getpass("비밀번호 (%s): " % name)
            except (EOFError, KeyboardInterrupt):
                pw = ""
        rec = (cfg.get("users") or {}).get(name) or {}
        if not (rec.get("pw") and verify_password(pw or "", rec["pw"])):
            raise AuthError(401, "로그인 실패: %s" % name)
        return name, norm_role(rec.get("role", "viewer")), "local"
    tok = os.environ.get("LLMWIKI_API_KEY") or ""
    if tok:
        parsed = parse_api_key(tok)
        if parsed:
            rec = (cfg.get("api_keys") or {}).get(parsed[0])
            if rec and hmac.compare_digest(str(rec.get("hash", "")), parsed[1]):
                return "key:" + str(rec.get("name") or parsed[0]), norm_role(rec.get("role", "viewer")), "apikey"
        raise AuthError(401, "LLMWIKI_API_KEY 가 올바르지 않습니다")
    c = cfg.get("cli") or {}
    if c.get("require_login"):
        raise AuthError(401, "security.json cli.require_login=true: --user <id> 또는 LLMWIKI_USER/LLMWIKI_PASSWORD 로 로그인하세요")
    return os.environ.get("USERNAME") or os.environ.get("USER") or "cli", norm_role(c.get("default_role"), "admin"), "cli"


def cli_min_role(level: str, op: str) -> str:
    """서버 객체 없이 security.json 만으로 최소 역할 계산 (CLI 용)."""
    cfg = load_security()
    p = cfg.get("permissions") or {}
    ops = {k: norm_role(v) for k, v in (p.get("ops") or {}).items() if not str(k).startswith("_")}
    if op in ops:
        return ops[op]
    best = ""
    for k in ops:
        if k.endswith("*") and op.startswith(k[:-1]) and len(k) > len(best):
            best = k
    if best:
        return ops[best]
    return norm_role((p.get("levels") or {}).get(level), DEFAULT_LEVEL_ROLE[norm_level(level)])


# ---------------------------------------------------------------- helpers
def _cookie(header: str, name: str) -> str:
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


def _sanitize(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: ("***" if k in ("_password", "password", "client_secret", "token") or "secret" in k.lower() else _sanitize(v))
                for k, v in d.items() if k not in ("overrides",)}
    if isinstance(d, list):
        return [_sanitize(x) for x in d[:20]]
    if isinstance(d, str):
        return d[:200]
    return d


def _jwt_payload(token: str) -> Dict[str, Any]:
    try:
        return json.loads(_unb64(token.split(".")[1]).decode("utf-8"))
    except Exception:
        return {}


def _verify_jwt_signature_if_possible(token: str, meta: Dict[str, Any], ctx: Optional[ssl.SSLContext]) -> None:
    """PyJWT + cryptography 가 설치돼 있으면 서명을 검증한다 (없으면 생략 — 토큰은 TLS 로 IdP 토큰 엔드포인트에서 직접 받은 것이라 위조 경로가 없다).
    설치: pip install pyjwt[crypto]"""
    try:
        import jwt  # type: ignore
        from jwt import PyJWKClient  # type: ignore
    except Exception:
        return
    try:
        client = PyJWKClient(meta["jwks_uri"], ssl_context=ctx) if ctx else PyJWKClient(meta["jwks_uri"])
        key = client.get_signing_key_from_jwt(token).key
        jwt.decode(token, key, algorithms=["RS256", "ES256", "PS256"], options={"verify_aud": False})
    except Exception as e:
        raise AuthError(401, "SSO id_token 서명 검증 실패: %s" % str(e)[:120])
