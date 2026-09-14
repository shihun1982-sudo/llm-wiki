# -*- coding: utf-8 -*-
"""인증·권한·감사 — Web 서버의 모든 요청은 여기서 (1) 누구인지, (2) 이 작업을 해도 되는지, (3) 무엇을 했는지 기록한다.

설계 요약 (docs/SECURITY.md):
- 로그인 두 가지를 병행: 로컬 ID/비밀번호(security.json users, PBKDF2) + SSO(OIDC 표준 코드 플로우, 또는 사내 리버스 프록시가 넣어 주는 헤더).
- 역할 3단계: viewer(읽기·질의) < operator(증분 빌드 등 복구 가능한 변경) < admin(설정·사용자·전체 삭제).
- 작업 등급 5단계: read < run < warn < admin < destructive. 등급마다 필요한 역할과 확인 방식이 다르다.
    read/run   : 역할만
    warn/admin : 역할 + 확인(_confirm)          ← "경고 표시"
    destructive: admin + 확인 + 확인 문구(_phrase) + 로컬 계정이면 비밀번호 재입력(_password)  ← "전체 DB 를 날릴 수 있는 것"
- 세션은 HMAC 서명 쿠키(표준 라이브러리만). CSRF 는 커스텀 헤더(X-Requested-With) + Origin 검사.
- 모드: off(로그인 없음, 로컬 개발) | on | auto(127.0.0.1 이외에 바인드하면 on). off 에서도 destructive 는 확인 문구가 필요하다.
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

from .config import path_for

ROLES = ("viewer", "operator", "admin")
RANK = {"viewer": 0, "operator": 1, "admin": 2}
LEVELS = ("read", "run", "warn", "admin", "destructive")
LEVEL_MIN_ROLE = {"read": "viewer", "run": "operator", "warn": "operator", "admin": "admin", "destructive": "admin"}
LEVEL_LABEL = {"read": "읽기", "run": "실행(토큰·시간 소모)", "warn": "변경(복구 가능)", "admin": "관리자 설정 변경", "destructive": "파괴적 — 색인/DB 삭제"}

DEFAULT_SECURITY: Dict[str, Any] = {
    "_comment": "로그인·역할·파괴적 작업 정책. 설명: docs/SECURITY.md. 사용자 추가: python -m llmwiki users add <id> --role admin",
    "mode": "auto",
    "session_hours": 12,
    "secure_cookie": "auto",
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
        "role_map": {"admin": [], "operator": []},
        "default_role": "viewer",
        "allowed_domains": [],
        "ca_bundle": "",
        "header": {"user": "X-Forwarded-User", "groups": "X-Forwarded-Groups", "trusted_proxies": ["127.0.0.1", "::1"]},
    },
    "users": {},
    "destructive": {"confirm_phrase": "DELETE INDEX", "require_reauth": True, "snapshot_before": True, "snapshot_keep": 3},
    "warn": {"confirm": True},
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
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _deep_update(cfg, raw)
    return cfg


def save_security(cfg: Dict[str, Any]) -> str:
    p = security_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return p


def _deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict) and k != "users":
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


# ---------------------------------------------------------------- operation classification
_DESTRUCTIVE_CLI = {("build", "--full"), ("build", "--reset"), ("build", "--purge-logs"), ("maintenance", "purge_requests"),
                    ("config", "reset"), ("snapshot", "restore"), ("users", "remove")}
_READ_CLI = {"query", "search", "health", "stats", "requests", "logs", "forensic", "graph", "entity", "docs", "arch", "time",
             "system", "corpus", "rules", "pin", "embed", "memory", "evolve", "preset", "prompts", "tuning", "config", "models",
             "build", "precompute", "trial", "fusion", "eval", "mcp-source", "snapshot", "users", "security", "wiki"}
_READ_CLI_ACTIONS = {  # (cmd, 첫 action) 이 이 집합이면 read. 그 외 action 은 warn 이상
    "build": {"status", "verify"}, "embed": {"report", "status"}, "memory": {"status", "episodes"}, "evolve": {"status", "review"},
    "preset": {"list", "show", "diff"}, "prompts": {"list", "show", "path"}, "tuning": {"show", "doc"}, "config": {"show", "paths"},
    "models": {"show", "test"}, "precompute": {"status"}, "trial": {"list", "compare", "report"}, "mcp-source": {"list", "test"},
    "rules": {"show", "test"}, "pin": {"list", "test"}, "corpus": {"lint", "types", "example", "lint-file", "stats"},
    "snapshot": {"list"}, "users": {"list"}, "security": {"show"}, "forensic": {"last", "list", "summary"},
}
_RUN_CLI = {"query", "search", "eval", "health", "fusion"}


def classify_cli(argv: List[str]) -> Tuple[str, str]:
    """CLI argv → (level, op). Web 콘솔(/api/cli)과 CLI 확인 프롬프트가 같은 표를 쓴다."""
    if not argv:
        return "read", "cli"
    cmd = argv[0]
    rest = argv[1:]
    action = next((a for a in rest if not a.startswith("-")), "")
    for c, a in _DESTRUCTIVE_CLI:
        if cmd == c and (a in rest or a == action):
            return "destructive", "cli:%s %s" % (cmd, a)
    if cmd == "build" and action in ("", "run"):
        if "--fix" in rest:
            return "warn", "cli:build verify --fix"
        return "warn", "cli:build"
    if cmd == "build" and action == "verify" and "--fix" in rest:
        return "warn", "cli:build verify --fix"
    if cmd in ("users", "security", "config", "models") and action not in _READ_CLI_ACTIONS.get(cmd, set()):
        return "admin", "cli:%s %s" % (cmd, action)
    if cmd == "serve" or cmd == "watch" or cmd == "mcp":
        return "admin", "cli:%s" % cmd
    if cmd in _RUN_CLI and (cmd != "health"):
        return "run", "cli:%s" % cmd
    if cmd in _READ_CLI:
        acts = _READ_CLI_ACTIONS.get(cmd)
        if acts is None or action in acts or (cmd == "evolve" and action == "feedback"):
            return "read", "cli:%s %s" % (cmd, action)
        if cmd == "trial" and action == "run":
            return "run", "cli:trial run"
        return "warn", "cli:%s %s" % (cmd, action)
    return "warn", "cli:%s" % cmd


def classify_api(method: str, path: str, body: Dict[str, Any]) -> Tuple[str, str]:
    """HTTP 요청 → (level, op). 새 엔드포인트를 추가하면 여기에도 등급을 적는다 (없는 POST 는 warn 으로 취급 — 안전한 기본값)."""
    body = body or {}
    act = str(body.get("action") or "")
    if method == "GET":
        if path in ("/api/auth/users", "/api/audit", "/api/security"):
            return "admin", path
        return "read", path
    if path == "/api/build":
        if body.get("full") or body.get("reset") or body.get("purge_logs"):
            return "destructive", "build --full/--reset" + (" --purge-logs" if body.get("purge_logs") else "")
        return "warn", "build (incremental)"
    if path == "/api/maintenance":
        return ("destructive", "maintenance purge_requests") if act == "purge_requests" else ("warn", "maintenance " + act)
    if path == "/api/cli":
        argv = body.get("argv") or []
        if isinstance(argv, str):
            import shlex
            try:
                argv = shlex.split(argv, posix=True)
            except ValueError:
                argv = argv.split()
        lvl, op = classify_cli(list(argv))
        # 콘솔 자체는 operator 이상만 (viewer 에게 자유 명령 입력창은 주지 않는다)
        return (lvl if RANK[LEVEL_MIN_ROLE[lvl]] >= RANK["operator"] else "run"), op
    if path == "/api/snapshot":
        return ("destructive", "snapshot restore") if act == "restore" else ("warn", "snapshot " + (act or "create"))
    if path in ("/api/query", "/api/search", "/api/feedback", "/api/evolve/propose", "/api/auth/login", "/api/auth/logout", "/api/auth/password"):
        return "read", path
    if path in ("/api/models/test", "/api/eval", "/api/fusion/compare", "/api/evolve/review"):
        return "run", path
    if path == "/api/trials":
        return ("warn", "trials delete") if act == "delete" else ("run", "trials run")
    if path == "/api/watch":
        if act == "scan":
            return "read", "watch scan"
        return ("admin", "watch %s (save)" % act) if body.get("save") else ("warn", "watch " + act)
    if path == "/api/precompute":
        return "warn", "precompute " + (act or "run")
    if path in ("/api/config", "/api/models/set", "/api/agents", "/api/auth/users", "/api/security"):
        return "admin", path
    if path == "/api/mcp_sources":
        return ("admin", "mcp_sources save") if act == "save" else ("run", "mcp_sources " + act)
    if path in ("/api/build/verify", "/api/memory", "/api/evolve/apply", "/api/evolve/reject", "/api/wiki/page", "/api/rules",
                "/api/presets", "/api/prompts", "/api/pins", "/api/query_rules", "/api/tuning"):
        if path == "/api/build/verify" and not body.get("fix"):
            return "read", "build verify"
        if path == "/api/pins" and act == "test":
            return "read", "pins test"
        if path == "/api/presets" and not body.get("save"):
            return "read", "presets apply (memory only)"
        return "warn", path + (" " + act if act else "")
    return "warn", path


# ---------------------------------------------------------------- Auth
class User:
    __slots__ = ("name", "role", "via", "issued")

    def __init__(self, name: str, role: str, via: str, issued: float = 0.0):
        self.name, self.role, self.via, self.issued = name, role if role in RANK else "viewer", via, issued

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "role": self.role, "via": self.via}


COOKIE = "llmwiki_session"
OIDC_COOKIE = "llmwiki_oidc"
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

    def has_any_login(self) -> bool:
        return bool(self.cfg.get("users")) or bool((self.cfg.get("sso") or {}).get("enabled"))

    def public_info(self) -> Dict[str, Any]:
        sso = self.cfg.get("sso") or {}
        return {"mode": self.mode, "local": bool((self.cfg.get("local") or {}).get("enabled", True)) and bool(self.cfg.get("users")),
                "sso": bool(sso.get("enabled")), "sso_type": sso.get("type", "oidc"), "sso_label": sso.get("button_label") or "SSO 로그인",
                "confirm_phrase": (self.cfg.get("destructive") or {}).get("confirm_phrase") or "DELETE INDEX",
                "require_reauth": bool((self.cfg.get("destructive") or {}).get("require_reauth", True)),
                "warn_confirm": bool((self.cfg.get("warn") or {}).get("confirm", True)), "roles": list(ROLES), "levels": LEVEL_LABEL}

    # ---- users ----
    def list_users(self) -> List[Dict[str, Any]]:
        return [{"name": k, "role": v.get("role", "viewer"), "display": v.get("name", ""), "created": v.get("created"),
                 "has_password": bool(v.get("pw"))} for k, v in sorted((self.cfg.get("users") or {}).items())]

    def add_user(self, name: str, password: str, role: str = "viewer", display: str = "") -> None:
        name = (name or "").strip()
        if not name or any(c in name for c in " /\\\"'<>"):
            raise ValueError("잘못된 사용자 id")
        if role not in RANK:
            raise ValueError("role 은 %s 중 하나" % "/".join(ROLES))
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
        if role not in RANK:
            raise ValueError("role 은 %s 중 하나" % "/".join(ROLES))
        users = self.cfg.setdefault("users", {})
        rec = users.setdefault(name, {"created": time.time()})
        rec["role"] = role
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

    # ---- sessions ----
    def make_cookie(self, user: User, https: bool = False) -> str:
        hours = float(self.cfg.get("session_hours") or 12)
        tok = self.signer.sign({"u": user.name, "r": user.role, "v": user.via, "iat": time.time(), "exp": time.time() + hours * 3600,
                                "id": secrets.token_hex(8)})
        secure = self.cfg.get("secure_cookie")
        flag = "; Secure" if (secure is True or (secure == "auto" and https)) else ""
        return "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d%s" % (COOKIE, tok, int(hours * 3600), flag)

    @staticmethod
    def clear_cookie() -> str:
        return "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % COOKIE

    def user_from_cookie(self, cookie_header: str) -> Optional[User]:
        tok = _cookie(cookie_header, COOKIE)
        if not tok:
            return None
        data = self.signer.verify(tok)
        if not data:
            return None
        name, role = data.get("u"), data.get("r", "viewer")
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
            return rec["role"]          # 로컬 목록에 적힌 역할이 IdP 그룹보다 우선 (특정인 지정)
        rm = sso.get("role_map") or {}
        gs = set(groups or [])
        for role in ("admin", "operator"):
            if gs & set(rm.get(role) or []):
                return role
        return sso.get("default_role") or "viewer"

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
        if self.mode == "off":
            return User("local", "admin", "off", time.time())
        u = self.user_from_headers(headers, client_ip)
        if u:
            return u
        return self.user_from_cookie(headers.get("Cookie") or "")

    def authorize(self, user: Optional[User], method: str, path: str, body: Dict[str, Any], headers: Any = None, host: str = "") -> Tuple[str, str]:
        """통과하면 (level, op) 반환, 아니면 AuthError. headers/host 는 CSRF 검사용."""
        level, op = classify_api(method, path, body or {})
        if method == "POST" and headers is not None:
            self._csrf_check(headers, host)
        if user is None:
            raise AuthError(401, "로그인이 필요합니다", redirect="/login")
        need_role = LEVEL_MIN_ROLE[level]
        if RANK[user.role] < RANK[need_role]:
            raise AuthError(403, "권한 부족: '%s' 작업(%s)은 %s 이상만 할 수 있습니다 (현재 %s)" % (op, LEVEL_LABEL[level], need_role, user.role),
                            need={"level": level, "op": op, "role": need_role})
        body = body or {}
        d = self.cfg.get("destructive") or {}
        need: Dict[str, Any] = {"level": level, "op": op, "label": LEVEL_LABEL[level]}
        if method != "POST":
            return level, op          # GET 은 읽기 — 역할 검사만 (admin 전용 조회 포함)
        if level in ("warn", "admin"):
            if self.mode == "on" and (self.cfg.get("warn") or {}).get("confirm", True) and not body.get("_confirm"):
                raise AuthError(428, "확인이 필요합니다: %s" % op, need=dict(need, confirm=True))
        elif level == "destructive":
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
                raise AuthError(428, "파괴적 작업 확인이 필요합니다: %s" % op, need=dict(need, **missing, snapshot=bool(d.get("snapshot_before", True))))
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
        rec = {"ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S"), "user": user.name if user else None, "role": user.role if user else None,
               "via": user.via if user else None, "ip": ip, "op": op, "level": level, "ok": ok}
        if detail is not None:
            rec["detail"] = _sanitize(detail)
        if error:
            rec["error"] = error[:300]
        try:
            d = path_for("logs_dir")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "audit.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

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


# ---------------------------------------------------------------- helpers
def _cookie(header: str, name: str) -> str:
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


def _sanitize(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: ("***" if k in ("_password", "password", "client_secret") or "secret" in k.lower() else _sanitize(v))
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
