# -*- coding: utf-8 -*-
"""Web UI 서버 (표준 라이브러리 http.server 기반, 외부 의존성 없음).

- 모든 기능은 JSON API 로 노출되며, /api/cli 는 CLI argparse 를 그대로 실행해 출력(stdout)을 돌려준다.
- 빌드/평가처럼 오래 걸리는 작업은 백그라운드 job 으로 실행하고 /api/jobs/<id> 로 진행 상황을 폴링한다.
- 모든 요청의 프로파일/디버그 trace 는 requests 테이블에 저장되며 /api/requests, /api/request?id= 로 조회한다.
- auto_build 토글이 켜져 있으면 워처 스레드가 auto_build_interval 초마다 코퍼스를 stat 스캔해 변경 시 증분 빌드한다.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

from ..config import (apply_overrides, save_settings, Toggles, Settings, TOGGLE_HELP, SETTING_HELP, TOGGLE_GROUPS,
                      TOGGLE_EFFECT, TOGGLE_AXES, effective_settings, all_paths, path_for)
from .. import tuning as _tuning
from ..architecture import registry as _arch_registry
from ..profiler import jsonable, Profiler
from ..evalset import load_questions
from .. import evolve as ev
from .. import auth as _authmod
from .. import progress as _pg
from .. import snapshots as _snap
from .. import reqmgr as _rq
from ..auth import Auth, AuthError, User, RANK, ROLES, ROLE_LABEL, LEVELS, LEVEL_LABEL, DEFAULT_LEVEL_ROLE, classify_api, classify_cli


def _ops_catalog() -> List[Dict[str, str]]:
    """보안 탭 '개별 작업 오버라이드' 편집을 돕는 대표 op 목록 (감사 로그의 op 이름과 동일)."""
    rows: List[Dict[str, str]] = []
    samples = [("POST", "/api/query", {}), ("POST", "/api/search", {}), ("POST", "/api/feedback", {}), ("POST", "/api/forensic/expect", {}),
               ("POST", "/api/query/rerun", {}), ("POST", "/api/sweep", {"action": "run"}),
               ("POST", "/api/eval", {}), ("POST", "/api/trials", {"action": "run"}), ("POST", "/api/fusion/compare", {}), ("POST", "/api/models/test", {}),
               ("POST", "/api/evolve/review", {}), ("POST", "/api/mcp_sources", {"action": "test"}),
               ("POST", "/api/pins", {"action": "add"}), ("POST", "/api/query_rules", {"action": "save"}), ("POST", "/api/prompts", {}), ("POST", "/api/tuning", {"action": "set"}),
               ("POST", "/api/presets", {"action": "save"}), ("POST", "/api/wiki/page", {}), ("POST", "/api/evolve/apply", {}), ("POST", "/api/evolve/reject", {}),
               ("POST", "/api/memory", {"action": "decay"}), ("POST", "/api/rules", {}), ("POST", "/api/graph_rules", {"action": "add_entity"}),
               ("POST", "/api/trials", {"action": "delete"}),
               ("POST", "/api/build", {"full": False}), ("POST", "/api/build/verify", {"fix": True}), ("POST", "/api/precompute", {"action": "run"}),
               ("POST", "/api/maintenance", {"action": "vacuum"}), ("POST", "/api/snapshot", {"action": "create"}), ("POST", "/api/watch", {"action": "start"}),
               ("POST", "/api/mcp_sources", {"action": "ingest"}),
               ("POST", "/api/build", {"full": True}), ("POST", "/api/build", {"channel": "fts"}), ("POST", "/api/snapshot", {"action": "restore"}),
               ("POST", "/api/config", {}), ("POST", "/api/models/set", {}), ("POST", "/api/agents", {}), ("POST", "/api/auth/users", {"action": "add"}),
               ("POST", "/api/security", {"action": "set_permissions"}), ("POST", "/api/apikeys", {"action": "add"}), ("POST", "/api/mcp_sources", {"action": "save"}),
               ("POST", "/api/build", {"purge_logs": True}), ("POST", "/api/maintenance", {"action": "purge_requests"}),
               ("POST", "/api/reset", {"scope": "data"}), ("POST", "/api/reset", {"scope": "settings"}),
               ("POST", "/api/reset", {"scope": "logs"})]
    seen = set()
    for m, p, b in samples:
        lv, op = classify_api(m, p, b)
        if op not in seen:
            seen.add(op)
            rows.append({"op": op, "level": lv, "kind": "web"})
    for argv in (["query", "x"], ["eval"], ["trial", "run"], ["build"], ["build", "--full"], ["build", "fts"], ["build", "vector"], ["build", "graph"],
                 ["pin", "add"], ["rules", "add"], ["tuning", "set"], ["evolve", "apply"], ["precompute", "run"], ["snapshot", "create"], ["snapshot", "restore"],
                 ["config", "set"], ["models", "set"], ["users", "add"], ["apikey", "add"], ["security", "perms", "set"], ["maintenance", "purge_requests"],
                 ["config", "reset"], ["serve"], ["mcp"]):
        lv, op = classify_cli(argv)
        if op not in seen:
            seen.add(op)
            rows.append({"op": op, "level": lv, "kind": "cli"})
    return rows

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_WATCHER: Dict[str, Any] = {"thread": None, "stop": False, "log": []}
_SCHED: Dict[str, Any] = {"scheduler": None}


def _mgr() -> _rq.RequestManager:
    """공통 요청 관리자 (서버 프로세스당 하나). 테스트처럼 serve() 를 거치지 않으면 첫 사용 시 생성."""
    return _rq.get_manager()


class BadRequest(ValueError):
    """클라이언트 입력이 잘못됨 → 400. (서버 결함인 500 과 구분하기 위해 명시적으로 쓴다)"""


def _scrub(v: Any, depth: int = 0) -> Any:
    """요청 본문에서 짝 없는 서러게이트를 걸러 낸다.

    JSON 은 `"\\ud83d"` 처럼 짝 없는 서러게이트를 표현할 수 있지만, 파이썬이 그것을 UTF-8 로
    인코딩할 수는 없다. 그런 문자열이 안으로 들어가면 SQLite 기록·JSON 응답·로그 파일 쓰기가
    모두 UnicodeEncodeError 로 실패하고, 요청 이력에 남으면 **그 뒤 모든 모니터 조회가 깨진다**.
    그래서 경계에서 한 번 정화한다(2026-09-15 멍키 테스트로 확인).
    """
    if depth > 12:
        return v
    if isinstance(v, str):
        try:
            v.encode("utf-8")
            return v
        except UnicodeEncodeError:
            return v.encode("utf-8", "replace").decode("utf-8", "replace")
    if isinstance(v, dict):
        return {_scrub(k, depth + 1): _scrub(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [_scrub(x, depth + 1) for x in v]
    return v


def _as_dict(v: Any, name: str) -> Dict[str, Any]:
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise BadRequest("%s 는 객체(JSON object)여야 합니다 (받은 값: %s)" % (name, type(v).__name__))
    return v


def _as_list(v: Any, name: str) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, (str, bytes)):
        raise BadRequest("%s 는 배열이어야 합니다" % name)
    if not isinstance(v, (list, tuple)):
        raise BadRequest("%s 는 배열이어야 합니다 (받은 값: %s)" % (name, type(v).__name__))
    return list(v)


def _as_str(v: Any, name: str, default: str = "") -> str:
    if v is None:
        return default
    if isinstance(v, (dict, list, tuple)):
        raise BadRequest("%s 는 문자열이어야 합니다" % name)
    return str(v)


def _qint(qs: Dict[str, str], name: str, default: int, lo: int = 0, hi: int = 1_000_000) -> int:
    """질의 문자열의 정수 파라미터를 범위 안으로 자른다.

    파이썬 정수는 자리수 제한이 없어서 `?limit=9999…9999` 같은 값이 그대로 SQLite 로 넘어가면
    `OverflowError: Python int too large to convert to C…` 로 500 이 난다(2026-09-16 멍키 테스트).
    """
    raw = qs.get(name)
    if raw in (None, ""):
        return default
    try:
        n = int(str(raw)[:32])          # 자리수부터 자른다
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _as_int(v: Any, name: str, default: Optional[int] = None) -> int:
    if v is None or v == "":
        if default is None:
            raise BadRequest("%s 가 필요합니다 (정수)" % name)
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        raise BadRequest("%s 는 정수여야 합니다 (받은 값: %r)" % (name, v if not isinstance(v, (dict, list)) else type(v).__name__))


def _as_argv(v: Any) -> List[str]:
    """CLI 콘솔의 argv: 문자열이면 shlex 로 나누고, 배열이면 문자열 목록으로. 그 밖의 타입은 400."""
    if v is None:
        return []
    if isinstance(v, str):
        import shlex
        try:
            return shlex.split(v, posix=True)
        except ValueError as e:
            raise BadRequest("argv 를 해석할 수 없습니다: %s" % e)
    if isinstance(v, (list, tuple)):
        out = [str(x) for x in v]
        # NUL 이 든 인자는 파일 경로·SQLite·subprocess 어디서든 ValueError('embedded null character') 를 던진다.
        # 경계에서 400 으로 거절한다 (2026-09-16 멍키 테스트가 `analyze notanumber \x00\x01\x02` 로 찾았다).
        if any("\x00" in x for x in out):
            raise BadRequest("argv 에 NUL(\\x00) 문자가 들어 있습니다")
        return out
    raise BadRequest("argv 는 문자열 또는 배열이어야 합니다 (받은 값: %s)" % type(v).__name__)


def _with_overrides(pipe, overrides: Optional[Dict[str, Any]], actor: Optional[Dict[str, str]] = None):
    """요청 단위 토글/프로바이더 오버라이드 — 스레드 로컬 설정 사본(request_scope) 으로 격리된다 (다른 사용자의 요청에 영향 없음).

    actor 를 주면 그 역할로 **문서 단위 접근 제어**가 걸린다 (llmwiki/docacl.py).
    주지 않으면 admin 으로 본다 — 내부 호출·CLI 는 제한하지 않는다."""
    return pipe.request_scope(overrides=overrides or None, actor=actor)


def _actor_of(user) -> Dict[str, str]:
    """로그인 사용자 → docacl 이 쓰는 신분. 익명은 anonymous_role(보통 viewer)로 내려온다."""
    return {"user": getattr(user, "name", "") or "guest",
            "role": getattr(user, "role", "") or "viewer",
            "origin": "web"}


def _actor_from_client(client: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """POST 처리기의 `_client` 딕셔너리 → docacl 신분.

    `_dispatch_post` 에는 User 객체가 없고 인증 단계가 넣어 둔 `_client`({user, role, via, origin…}) 만 있다.
    역할을 못 읽으면 **가장 낮은 등급(viewer)** 으로 본다 — 모르면 열어 주는 쪽이 아니라 닫는 쪽이다."""
    c = client or {}
    return {"user": str(c.get("user") or "guest"),
            "role": str(c.get("role") or "viewer"),
            "origin": str(c.get("origin") or "web")}


def _doc_denied(p, user, doc_id: str) -> Optional[Dict[str, Any]]:
    """문서 열람 출구의 공통 관문. 볼 수 있으면 None, 못 보면 그대로 돌려줄 오류 본문.

    검색 경로(`doc_acl` 단계)만 막으면 `/api/doc?id=...` 로 직접 열어 우회할 수 있다.
    근거가 새는 길은 전부 같은 판정기를 쓴다 (llmwiki/docacl.py 의 표 참고).
    """
    from .. import docacl as _acl
    if not bool(getattr(p.s.toggles, "doc_acl", True)):
        return None
    role = getattr(user, "role", "") or "viewer"
    if role == "admin":
        return None
    acl = _acl.load()
    armed = bool(acl.get("enabled", True)) and bool(acl.get("rules") or _acl._rank(acl.get("default_min_role")) > 0)
    if not armed:
        return None           # 규칙이 하나도 없으면 예전과 똑같이 동작한다
    # 규칙이 켜져 있는데 판정에 실패하면 **막는다** — 메타를 못 읽었다고 비공개 문서를 열어 줄 수는 없다.
    try:
        f = _acl.Filter(role, p.store.doc_meta_map(), acl)
        ok, need = f.doc_ok(str(doc_id or "")), f.blocked_docs.get(str(doc_id or "")) or "?"
    except Exception as e:
        ok, need = False, "판정 실패(%s)" % str(e)[:60]
    if ok:
        return None
    return {"error": acl.get("deny_message") or "권한이 없는 문서입니다",
            "detail": "이 문서는 %s 이상만 볼 수 있습니다 (현재 %s)" % (need, role),
            "doc_id": doc_id, "min_role": need, "role": role}


# ---------------------------------------------------------------- 요청 단위 overrides 화이트리스트 (2026-09-18)
# 왜 있나: apply_overrides 는 Settings 의 **모든** 필드를 받아들인다. 그래서 예전에는 읽기 등급(익명 포함)이
# `/api/query` 에 {"overrides": {"openai_base_url": "http://attacker/v1"}} 를 보내면 서버가 .env 의 PAT 를 그 주소로
# 보냈고, `corpus_dirs` 를 바꿔 임의 폴더를 색인시킬 수도 있었다 (CODE_REVIEW_0917 §0 P0-1).
# 여기서는 "질의 한 번의 동작을 바꾸는 손잡이" 만 누구나 쓰게 하고, URL·헤더·경로·서버 운영 키는 admin 에게만 허용한다.
# 목록 조정은 security.json `overrides.allow_extra`(추가 허용) / `overrides.deny`(admin 도 금지) — 코드 수정 없이.
# 목록과 검사는 **auth 계층**에 있다 (llmwiki/auth.py). Web 만 검사하면 MCP 로 같은 일이 가능했기 때문이다.
OVERRIDE_SAFE_KEYS = _authmod.OVERRIDE_SAFE_KEYS
OVERRIDE_ROLE_ATTRS = _authmod.OVERRIDE_ROLE_ATTRS


def _filter_overrides(ov: Dict[str, Any], role: str, path: str = "") -> Dict[str, Any]:
    """요청 단위 overrides 에서 이 역할이 쓸 수 없는 키를 거른다 (`auth.filter_overrides` 로 위임)."""
    try:
        cfg = Handler.auth.cfg if Handler.auth is not None else {}
    except Exception:
        cfg = {}
    return _authmod.filter_overrides(ov, role, cfg)


def _cli_equiv(cmd: str, q: str, overrides: Dict[str, Any], base_toggles: Toggles) -> str:
    parts = ["python -m llmwiki", cmd]
    if q:
        parts.append('"%s"' % q.replace('"', '\\"'))
    for k, v in (overrides or {}).items():
        if k in Toggles.__dataclass_fields__:
            if bool(v) != getattr(base_toggles, k):
                parts.append(("--%s" if v else "--no-%s") % k.replace("_", "-"))
        elif k == "llm_provider":
            parts.append("--llm %s" % v)
        elif k == "embed_provider":
            parts.append("--embed-provider %s" % v)
        elif k == "top_k_final":
            parts.append("--k %s" % v)
        elif k == "debug_level":
            parts.append("--debug %s" % v)
        elif k == "output_mode":
            parts.append("--output %s" % v)
        elif k == "tuning" and isinstance(v, dict) and v:
            parts.append("--tuning " + ",".join("%s=%s" % (a, b) for a, b in v.items()))
        elif k.rsplit("_", 1)[0] in Settings.LLM_ROLES and k.endswith(("_model", "_provider")):
            parts.append("--%s %s" % (k.replace("_", "-"), v))
    return " ".join(parts) + " --trace"


# ---------------------------------------------------------------- watcher
def _watcher_loop(pipe) -> None:
    while not _WATCHER["stop"]:
        if pipe.s.toggles.auto_build:
            try:
                mgr = _mgr()
                with pipe.request_scope():
                    r = pipe.check_changes()
                    if r["n_changed"] or r["n_removed"]:
                        # 변경이 있을 때만 쓰기 티켓(soft: 정책상 질의는 계속 허용) 을 받아 증분 빌드
                        with mgr.ticket("build", "soft", client={"origin": "watch", "user": "watcher", "role": "builder"}, label="auto_build (watcher)", build_mode="incremental"):
                            r = pipe.auto_build_tick(progress=lambda m: _WATCHER["log"].append("%s %s" % (time.strftime("%H:%M:%S"), m)))
                if r.get("built") or r.get("error"):
                    _WATCHER["log"].append("%s scan %sms changed=%s removed=%s built=%s %s" % (
                        time.strftime("%H:%M:%S"), r["scan_ms"], r["n_changed"], r["n_removed"], r.get("built"), r.get("error") or ""))
                    del _WATCHER["log"][:-200]
            except Exception as e:
                _WATCHER["log"].append("%s watcher error: %s" % (time.strftime("%H:%M:%S"), e))
            wait = max(5, int(pipe.s.auto_build_interval or 300))
        else:
            wait = 2
        for _ in range(wait):
            if _WATCHER["stop"]:
                return
            time.sleep(1)


def _ensure_watcher(pipe) -> None:
    if _WATCHER["thread"] is None or not _WATCHER["thread"].is_alive():
        _WATCHER["stop"] = False
        t = threading.Thread(target=_watcher_loop, args=(pipe,), daemon=True)
        _WATCHER["thread"] = t
        t.start()


class Handler(BaseHTTPRequestHandler):
    pipe = None  # set by serve()
    auth: Optional[Auth] = None   # set by serve(); 테스트처럼 serve() 를 거치지 않으면 첫 요청에서 생성 (security.json 기준)
    host = "127.0.0.1"
    # HTTP/1.1 = 연결 재사용(keep-alive). 기본값인 HTTP/1.0 은 응답마다 연결을 끊는다.
    # 브라우저는 한 사이트에 동시 연결을 6개까지만 열기 때문에, 오래 걸리는 질의 몇 개가 연결을 물고 있으면
    # 2초마다 도는 갱신 요청이 브라우저 안에서 줄을 서다가 질의가 끝나는 순간 한꺼번에 처리된다
    # ("한참 멈췄다가 화면이 한 번에 갱신됨"). 연결을 재사용하면 이 줄서기가 사라진다.
    # 모든 응답 경로가 Content-Length 를 보내므로 keep-alive 가 안전하다.
    protocol_version = "HTTP/1.1"
    timeout = 30                  # 유휴 keep-alive 연결을 정리 (serve() 가 server.json 값으로 덮어쓴다)

    def log_message(self, fmt, *args):  # 조용히
        pass

    def log_error(self, fmt, *args):
        # keep-alive 연결이 유휴 시간 초과로 닫히는 것은 정상이므로 로그를 남기지 않는다
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError):
            # 사용자가 탭을 닫거나 새로고침하면 흔히 일어난다 — 서버 오류가 아니다
            self.close_connection = True

    # ---------------- auth helpers ----------------
    def _auth(self) -> Auth:
        if Handler.auth is None:
            Handler.auth = Auth(self.pipe.s, Handler.host)
        return Handler.auth

    def _ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def _user(self) -> Optional[User]:
        # 권한 미리보기(admin 이 viewer 화면을 확인)는 여기 한 곳에서 적용된다 → 모든 권한 검사에 그대로 반영.
        # 역할을 낮추기만 하므로 쿠키를 위조해도 권한이 올라가지 않는다.
        u = self._auth().identify(self.headers, self._ip())
        return _authmod.apply_preview(u, self.headers.get("Cookie") or "")

    def _client(self, user: Optional[User], origin: str = "web") -> Dict[str, Any]:
        """요청 관리자/진행 레지스트리에 남길 클라이언트 정보."""
        if user and user.via == "apikey" and origin == "web":
            origin = "api"
        return {"user": user.name if user else "guest", "role": user.role if user else "", "via": user.via if user else "anon",
                "ip": self._ip(), "origin": origin, "agent": (self.headers.get("User-Agent") or "")[:100]}

    def _can_see_users(self, user: Optional[User]) -> bool:
        """남의 사용자 id·IP·에이전트를 볼 수 있나.

        admin 은 항상 본다. 그 외에는 `server.json` 의 `monitor.show_user_to_viewer` 를 따른다 —
        진행 중 작업 목록(`/api/activity`)과 같은 규칙이라 한 화면에서 정책이 엇갈리지 않는다.
        """
        if user and getattr(user, "role", "") == "admin":
            return True
        try:
            return bool((_mgr().cfg.get("monitor") or {}).get("show_user_to_viewer", True))
        except Exception:
            return True

    def _job_cancellable(self, tok: str, user: Optional[User]) -> bool:
        """막 시작해 아직 티켓이 없는 잡이면 취소를 예약할 수 있게 한다 (요청 → 스레드 기동 사이의 경쟁 상태)."""
        with _JOBS_LOCK:
            j = _JOBS.get(tok)
        if not j or j.get("status") != "running":
            return False
        return bool(user and (user.role == "admin" or j.get("user") == user.name))

    def _rejected(self, e: _rq.Rejected) -> None:
        data = json.dumps(e.body(), ensure_ascii=False).encode("utf-8", "replace")
        self.send_response(e.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if e.retry_after is not None:
            self.send_header("Retry-After", str(int(max(1, e.retry_after))))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _https(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def _redirect(self, url: str, cookies: Optional[List[str]] = None) -> None:
        self.send_response(302)
        self.send_header("Location", url)
        for c in cookies or []:
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _deny(self, e: AuthError, user: Optional[User], op: str = "", level: str = "") -> None:
        if e.status != 428:
            self._auth().audit(user, op or self.path, level or "?", False, self._ip(), error=e.error)
        self._json(e.body(), e.status)

    # ---------------- helpers ----------------
    def _json(self, obj: Any, code: int = 200, cookies: Optional[List[str]] = None) -> None:
        # errors="replace": 사용자가 보낸 문자열에 짝 없는 서러게이트(\ud83d 같은)가 섞여 있어도
        # 응답을 만들다 죽지 않는다. 그런 값이 요청 목록·이력에 한 번 들어가면 이후 모든 조회가
        # 500/400 이 되던 문제를 막는다(2026-09-15 멍키 테스트로 확인).
        data = json.dumps(jsonable(obj), ensure_ascii=False).encode("utf-8", "replace")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for c in cookies or []:
            self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(data)

    def _max_body(self) -> int:
        try:
            return int(float((_mgr().cfg.get("concurrency") or {}).get("max_body_mb") or 0) * 1024 * 1024)
        except Exception:
            return 8 * 1024 * 1024

    def _body(self) -> Dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n == 0:
            return {}
        cap = self._max_body()
        if cap and n > cap:
            # 읽지도 않고 거절한다 — 거대한 본문을 다 받아 파싱하면 그동안 슬롯과 메모리를 잡는다.
            raise _rq.Rejected(413, "요청 본문이 너무 큽니다 (%.1fMB > %.1fMB). server.json concurrency.max_body_mb"
                               % (n / 1048576.0, cap / 1048576.0), code="body_too_large")
        try:
            return _scrub(json.loads(self.rfile.read(n).decode("utf-8")))
        except Exception:
            return {}

    def _static(self, rel: str) -> None:
        path = os.path.normpath(os.path.join(STATIC, rel.lstrip("/")))
        if not path.startswith(STATIC) or not os.path.isfile(path):
            self.send_error(404)
            return
        ctype = {"html": "text/html", "js": "application/javascript", "css": "text/css", "svg": "image/svg+xml", "json": "application/json"}.get(path.rsplit(".", 1)[-1], "application/octet-stream")
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _status(self) -> Dict[str, Any]:
        p = self.pipe
        try:
            from .. import presets as _presets
            preset_defs = _presets.load_presets()
            preset_names = list(preset_defs.keys())
        except Exception:
            preset_defs, preset_names = {}, []
        lb = p.store.kv_get("last_build") or {}
        mgr = _mgr()
        with _JOBS_LOCK:
            jobs = {k: {kk: vv for kk, vv in v.items() if kk != "result"} for k, v in _JOBS.items()}
        return {"stats": p.store.stats(), "providers": p.provider_status(), "settings": p.base_settings.to_dict(),
                "toggle_names": list(Toggles.__dataclass_fields__), "toggle_help": TOGGLE_HELP, "setting_help": SETTING_HELP, "toggle_groups": TOGGLE_GROUPS,
                "toggle_effect": TOGGLE_EFFECT, "toggle_axes": TOGGLE_AXES,
                "roles": list(Settings.LLM_ROLES), "caches": p.cache_info(), "presets": preset_names, "preset_defs": preset_defs, "paths": all_paths(),
                "doc_types": p.store.doc_type_counts(), "provenance": p.store.provenance_counts(), "lint": p.store.kv_get("lint_summary"),
                "last_build": lb, "alerts": lb.get("alerts") or [], "embed_progress": p.store.kv_get("embed_progress"),
                "watcher": dict(p.watcher, enabled=p.s.toggles.auto_build, interval=p.s.auto_build_interval,
                                thread_alive=bool(_WATCHER["thread"] and _WATCHER["thread"].is_alive()), log=_WATCHER["log"][-30:]),
                "jobs": jobs,
                "server": {"running": sum(1 for t in mgr.active.values() if t["status"] == "running"),
                           "queued": sum(1 for t in mgr.active.values() if t["status"] == "queued"),
                           "lock": mgr.rw.state(), "maintenance": bool((mgr.cfg.get("access") or {}).get("maintenance_mode")),
                           "max_parallel_reads": mgr.cfg["concurrency"]["max_parallel_reads"],
                           "scheduler": (_SCHED["scheduler"].summary() if _SCHED.get("scheduler") else None)}}

    # ---------------- MCP (Streamable HTTP) ----------------
    mcp_only = False   # serve(mcp_only=True): /mcp 와 /api/auth/me 만 제공

    def _not_my_request(self, rec: Optional[Dict[str, Any]], user, auth) -> bool:
        """이 요청 기록이 **남의 것**인가 (전체 조회 권한도 없고).

        `request_id` 는 순차 정수라 남의 번호를 추측이 아니라 **열거**할 수 있다. 그래서 지난 질의의 원문·
        답변·중간 결과를 꺼내는 길은 전부 이 검사를 지나야 한다: `/api/request` · `/api/rerun` ·
        `/api/query/rerun` · `/api/query_trace` · `/api/analysis`. 하나만 빼놓으면 나머지가 무의미해진다.
        """
        if not rec or not rec.get("user") or not user or not auth or auth.mode == "off":
            return False
        if rec["user"] == user.name:
            return False
        try:
            return not auth.allowed(user.role, "run", "requests all")
        except Exception:
            return True        # 판정에 실패하면 막는다 (남의 기록을 열어 주는 쪽으로 기울지 않는다)

    def _mcp(self, method: str) -> None:
        """POST/GET/DELETE /mcp — 인증(Bearer API 키·쿠키·익명) 과 read 등급 권한은 authorize() 로, 본문은 mcp.handle_http 로."""
        from .. import mcp as _mcp
        auth = self._auth()
        body = b""
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            cap = self._max_body()
            if cap and n > cap:
                return self._rejected(_rq.Rejected(413, "요청 본문이 너무 큽니다 (%.1fMB)" % (n / 1048576.0),
                                                   code="body_too_large"))
            body = self.rfile.read(n) if n else b""
        user = None
        try:
            user = self._user()
            level, op = auth.authorize(user, "POST", "/mcp", {}, self.headers if method == "POST" else None, self.headers.get("Host") or "")
        except AuthError as e:
            auth.audit(user, "mcp", "read", False, self._ip(), error=e.error)
            data = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": e.error, "status": e.status}}).encode("utf-8")
            self.send_response(401 if e.status in (401, 428) else e.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if e.status == 401:
                self.send_header("WWW-Authenticate", 'Bearer realm="llmwiki-mcp"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        # tools/call 만 읽기 티켓(슬롯·속도 제한·취소·활동 목록); initialize/tools/list 는 등록만
        label, heavy = "mcp", False
        if method == "POST":
            try:
                msgs = json.loads(body.decode("utf-8") or "null")
                msgs = msgs if isinstance(msgs, list) else [msgs]
                calls = [m for m in msgs if isinstance(m, dict) and m.get("method") == "tools/call"]
                if calls:
                    heavy = True
                    label = "mcp %s" % ", ".join(str((m.get("params") or {}).get("name") or "?") for m in calls)[:120]
            except Exception:
                pass
        mgr = _mgr()
        try:
            with mgr.ticket("mcp", "read" if heavy else "none", client=self._client(user, "mcp"), label=label):
                # actor 를 넘겨야 MCP 도 Web 과 **같은 문서 접근 제어**를 받는다 (llmwiki/docacl.py).
                # 넘기지 않으면 pipe.actor 기본값이 admin 이라 API 키 하나로 전 문서가 열린다.
                with self.pipe.request_scope(actor=dict(_actor_of(user), origin="mcp")):
                    status, hdrs, out = _mcp.handle_http(self.pipe, method, body, self.headers)
        except _rq.Rejected as e:
            data = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32002, "message": e.message, "status": e.status, "data": e.body()}}).encode("utf-8")
            self.send_response(e.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if e.retry_after is not None:
                self.send_header("Retry-After", str(int(max(1, e.retry_after))))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        except _pg.Cancelled as e:
            data = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32003, "message": "cancelled: %s" % e}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(status)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        if out:
            self.wfile.write(out)

    def do_DELETE(self) -> None:
        u = urlparse(self.path)
        if u.path == "/mcp":
            return self._mcp("DELETE")
        if u.path.startswith("/api/jobs/") or u.path.startswith("/api/activity/"):
            # 실행 중인 잡/요청 취소 (본인 또는 admin)
            tok = u.path.rsplit("/", 1)[-1]
            try:
                user = self._user()
            except AuthError as e:
                return self._deny(e, None, u.path, "read")
            if user is None:
                return self._json({"error": "로그인이 필요합니다"}, 401)
            r = _mgr().cancel(tok, by=user.name, reason="user request", allow_owner=None if user.role == "admin" else user.name,
                              pending_ok=self._job_cancellable(tok, user))
            self._auth().audit(user, "cancel " + tok, "read", bool(r.get("ok")), self._ip())
            return self._json(r, 200 if r.get("ok") else 404)
        self.send_error(404)

    # ---------------- GET ----------------
    def do_GET(self) -> None:
        u = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/mcp":
            return self._mcp("GET")
        if Handler.mcp_only and u.path not in ("/api/auth/me", "/api/progress", "/api/activity", "/mcp"):
            return self._json({"error": "mcp-only server: use POST /mcp"}, 404)
        try:
            with self.pipe.request_scope():      # 스레드 전용 DB 연결 + 설정 사본 (GET 은 락을 잡지 않는다)
                self._do_get(u, qs)
        except _rq.Rejected as e:
            self._rejected(e)
        except _pg.Cancelled as e:
            self._json({"error": "cancelled: %s" % e, "cancelled": True}, 499)
        except AuthError as e:      # identify() 단계의 거부 (잘못된/폐기된 API 키 등)
            self._deny(e, None, u.path, "read")
        except (BadRequest, TypeError, AttributeError, ValueError, KeyError, OverflowError) as e:
            self._bad_request(u.path, e)
        except Exception as e:
            self._server_error(u.path, e)

    def _server_error(self, path: str, e: BaseException) -> None:
        """서버 결함(500). 트레이스는 **로그(error.log)에** 남기고 클라이언트에는 참조 id 만 준다.
        예전에는 모든 사용자에게 스택트레이스(파일 경로·코드)를 돌려주면서 정작 서버 로그에는 남기지 않아
        운영자가 500 을 볼 방법이 없었다 (CODE_REVIEW_0917 §2.3 S2). admin 이거나 server.json debug.expose_trace=true 면 트레이스를 포함한다."""
        from .. import logging_setup as _ls
        ref = uuid.uuid4().hex[:8]
        tb = traceback.format_exc()
        try:
            _ls.log("error", "server error %s [%s]: %s: %s" % (path, ref, type(e).__name__, e), "web", path=path, ref=ref,
                    exc=type(e).__name__, trace=tb[-4000:])
        except Exception:
            pass
        out: Dict[str, Any] = {"error": "%s: %s" % (type(e).__name__, str(e)[:300]), "code": "internal", "ref": ref,
                               "hint": "logs grep --text %s (error.log)" % ref}
        expose = False
        try:
            expose = bool(((_mgr().cfg.get("debug") or {}).get("expose_trace")))
            if not expose:
                u = self._user()
                expose = bool(u and u.role == "admin")
        except Exception:
            pass
        if expose:
            out["trace"] = tb
        self._json(out, 500)

    # GET 중 무거운 것(분석 리포트·health ping·verify·system 통계)은 읽기 슬롯을 받는다; 나머지 조회는 등록도 하지 않는다 (폴링 비용 0)
    HEAVY_GET = ("/api/analysis", "/api/optimize/bundle", "/api/health", "/api/build/verify", "/api/system", "/api/graph", "/api/embed/report", "/api/corpus/lint", "/api/forensic")

    def _do_get(self, u, qs: Dict[str, str]) -> None:
        p = self.pipe
        auth = self._auth()
        if True:
            # ---- 공개 경로: 정적 파일, 로그인 페이지, SSO 왕복, 로그인 상태 조회 ----
            if u.path.startswith("/static/"):
                return self._static(u.path[len("/static/"):])
            if u.path == "/login":
                return self._static("login.html")
            if u.path == "/api/auth/me":
                user = self._user()
                return self._json(dict(auth.public_info(), user=user.to_dict() if user else None))
            if u.path == "/auth/sso/start":
                if not auth.oidc_enabled():
                    return self._json({"error": "SSO(OIDC) 가 설정되지 않았습니다 (security.json sso)"}, 404)
                url, cookie = auth.oidc_start(qs.get("next") or "/")
                return self._redirect(url, [cookie])
            if u.path == "/auth/sso/callback":
                try:
                    user, nxt = auth.oidc_callback(qs, self.headers.get("Cookie") or "")
                except AuthError as e:
                    auth.audit(None, "sso login", "auth", False, self._ip(), error=e.error)
                    return self._json(e.body(), e.status)
                auth.audit(user, "sso login", "auth", True, self._ip())
                return self._redirect(nxt if nxt.startswith("/") else "/", [auth.make_cookie(user, self._https())])
            user = self._user()
            mgr = _mgr()
            mgr.check_access(self._ip(), user.name if user else "", user.role if user else "")   # 차단/점검 모드 (정적 파일 제외)
            if u.path in ("/", "/index.html"):
                if user is None:
                    return self._redirect("/login")      # anonymous_role 이 비어 있으면 로그인 필수
                return self._static("index.html")
            # ---- 인가 (GET 은 read, 일부 admin 전용) ----
            try:
                level, op = auth.authorize(user, "GET", u.path, {}, None, "")
            except AuthError as e:
                return self._deny(e, user, u.path, "read")
            # ---- 활동 목록 (viewer 도 실행 중 작업을 볼 수 있다; admin 은 IP·오류까지) ----
            if u.path == "/api/activity":
                is_admin = bool(user and user.role == "admin")
                if not is_admin and not (mgr.cfg.get("monitor") or {}).get("viewer_can_see_activity", True):
                    return self._json({"error": "활동 목록은 admin 만 볼 수 있습니다 (server.json monitor.viewer_can_see_activity)"}, 403)
                return self._json(dict(mgr.activity(viewer=not is_admin, history=_qint(qs, "history", 20)), me=user.name if user else None, admin=is_admin))
            if u.path == "/api/admin/server":
                if not user or user.role != "admin":
                    return self._json({"error": "admin 전용"}, 403)
                from .. import auth as _authmod
                st = mgr.stats()
                st["sessions"] = _authmod.sessions().list()
                st["activity"] = mgr.activity(viewer=False, history=_qint(qs, "history", 50))
                st["config_path"] = mgr.path
                st["counters_process"] = __import__("llmwiki.profiler", fromlist=["totals"]).totals()
                return self._json(st)
            if u.path == "/api/schedule":
                from .. import scheduler as _sc
                sch = _SCHED.get("scheduler")
                if sch is not None:
                    # 실행 중인 스케줄러는 tick_s(기본 5초) 마다 파일 mtime 을 본다. 조회할 때도 한 번 보게 해서
                    # "파일을 고치고 새로고침했는데 예전 목록이 나온다" 를 없앤다 (getmtime 1회 — 폴링 비용은 그대로).
                    try:
                        sch._maybe_reload()
                    except Exception:
                        pass
                return self._json({"tasks": (sch.list_tasks() if sch else _sc.list_tasks_static()), "path": _sc.schedule_path(), "running": bool(sch),
                                   "history": (sch.history(_qint(qs, "n", 50)) if sch else _sc.read_history(_qint(qs, "n", 50))),
                                   "action_types": _sc.ACTION_TYPES, "help": _sc.HELP})
            if u.path == "/api/models/catalog":
                from .. import models_catalog as _mc
                return self._json(_mc.describe(p.base_settings, role=qs.get("role") or None))
            if u.path == "/api/profile":
                from .. import profiles as _pf
                anon = (user is None or user.via == "anon")
                return self._json({"user": None if anon else user.name, "anonymous": anon,
                                   "profile": {} if anon else _pf.get(p.s.data_dir, user.name),
                                   "note": "게스트는 서버에 저장하지 않습니다 (브라우저에만 유지)" if anon else ""})
            if u.path == "/api/auth/users":
                return self._json({"users": auth.list_users(), "mode": auth.mode, "sso": auth.public_info()["sso"], "roles": list(ROLES), "role_labels": ROLE_LABEL})
            if u.path == "/api/audit":
                return self._json({"rows": auth.audit_tail(_qint(qs, "n", 200))})
            if u.path == "/api/apikeys":
                return self._json({"keys": auth.list_api_keys(), "roles": list(ROLES)})
            if u.path == "/api/security":
                cfg = json.loads(json.dumps(auth.cfg))
                for v in (cfg.get("users") or {}).values():
                    v.pop("pw", None)
                for v in (cfg.get("api_keys") or {}).values():
                    v.pop("hash", None)
                return self._json({"security": cfg, "path": __import__("llmwiki.auth", fromlist=["security_path"]).security_path(), "effective_mode": auth.mode,
                                   "permissions": auth.permissions(), "defaults": DEFAULT_LEVEL_ROLE, "levels": list(LEVELS), "level_labels": LEVEL_LABEL,
                                   "roles": list(ROLES), "role_labels": ROLE_LABEL, "ops_catalog": _ops_catalog()})
            if u.path == "/api/docacl":
                # 문서 단위 접근 제어 (CLI `security docacl show` 와 같은 내용 + 토글 상태)
                from .. import docacl as _dacl
                return self._json(dict(_dacl.describe(), toggle=bool(getattr(p.s.toggles, "doc_acl", True)),
                                       example="setup/docacl.example.json", role_labels=ROLE_LABEL))
            if u.path == "/api/sweep/keys":
                # Pipeline 페이지의 스윕 폼: 스윕할 수 있는 키(type/min/max/choices/stage/point) + 재시작점 표 + 상한
                from .. import sweep as _sw, rerun as _rr
                return self._json({"keys": _sw.sweepable_keys(p.s), "points": _rr.POINTS, "stages": _sw.STAGES,
                                   "max_values": int(getattr(p.s, "sweep_max_values", 20) or 20), "max_repeats": _sw.MAX_REPEATS,
                                   "saved": _rr.list_saved(p.s, _qint(qs, "n", 20))})
            if u.path == "/api/sweep":
                # ?id=<sw id> → 기록 + 비교 · 없으면 목록
                from .. import sweep as _sw
                sid = qs.get("id") or ""
                if sid:
                    rec = _sw.load(p.s, sid)
                    if not rec:
                        return self._json({"error": "sweep not found: %s" % sid}, 404)
                    return self._json({"record": rec, "compare": _sw.compare(rec), "text": _sw.render_text(rec)})
                return self._json({"sweeps": _sw.list_sweeps(p.s, _qint(qs, "n", 50))})
            if u.path == "/api/rerun":
                # 화면이 trace 의 각 줄에 ⟲ 를 달 수 있게: 재시작점 표 + 이 요청에 저장된 중간 결과가 있는지
                from .. import rerun as _rr
                rid = qs.get("request_id") or ""
                if rid and self._not_my_request(p.store.get_request(_qint(qs, "request_id", 0), archive_dir=p.s.requests_archive_dir()), user, auth):
                    return self._json({"error": "다른 사용자의 요청입니다 (전체 조회 권한 필요: 작업 'requests all')"}, 403)
                data = _rr.load(p.s, rid) if rid else None
                ok, why = _rr.check_compatible(data or {}, str(p.store.build_version())) if rid else (False, "request_id 가 없습니다")
                return self._json({"points": _rr.POINTS, "stage_point": _rr.STAGE_POINT,
                                   "available": bool(data), "compatible": ok, "reason": why,
                                   "capture_on": bool(getattr(p.s.toggles, "rerun_capture", False)),
                                   "saved": _rr.list_saved(p.s, _qint(qs, "n", 20)),
                                   "of": ({"query": data.get("query"), "created": data.get("created"),
                                           "build_version": data.get("build_version")} if data else None)})
            if u.path == "/api/snapshot":
                return self._json({"snapshots": _snap.list_(p)})
            # ---- 락 없이 응답하는 폴링용 엔드포인트 ----
            # 빌드 job 이 _LOCK 을 잡은 채 수십 분 돌 수 있으므로, 진행 상황 조회는 절대 락 뒤에 두지 않는다
            # (예전에는 여기가 락 안에 있어서 전체 리빌드 중 화면이 'starting…' 에서 멈춘 것처럼 보였다).
            if u.path.startswith("/api/jobs/"):
                jid = u.path.rsplit("/", 1)[-1]
                with _JOBS_LOCK:
                    j = _JOBS.get(jid)
                if not j:
                    return self._json({"error": "no such job"}, 404)
                out = {k: v for k, v in j.items() if k != "result" or j.get("status") != "running"}
                out["live"] = _pg.get(jid) or {}
                out["elapsed_s"] = round((j.get("finished") or time.time()) - j["started"], 1)
                return self._json(out)
            if u.path.startswith("/api/progress"):
                tok = u.path.rsplit("/", 1)[-1] if u.path != "/api/progress" else qs.get("token", "")
                if tok and tok != "progress":
                    return self._json(_pg.get(tok) or {"status": "unknown", "token": tok})
                with _JOBS_LOCK:
                    jobs = [{k: v for k, v in j.items() if k in ("id", "kind", "status", "started", "finished")} for j in _JOBS.values()]
                return self._json({"running": _pg.all_running(), "jobs": jobs})
            heavy = any(u.path == h or u.path.startswith(h + "?") for h in self.HEAVY_GET)
            with (mgr.ticket("read", "read", client=self._client(user), label="GET " + u.path) if heavy else _noop()):
                if u.path == "/api/status":
                    st = self._status()
                    from .. import __version__ as _pkg_ver
                    st["version"] = _pkg_ver                     # = `--version` · MCP serverInfo.version · docs/RELEASE_NOTES.md 최신 절
                    st["auth"] = dict(auth.public_info(), user=user.to_dict() if user else None)
                    try:   # 로그 총량 요약 (log_check_interval_s 가 지났을 때만 다시 잰다) — 헤더 배너용
                        from .. import logging_setup as _ls
                        q = _ls.check_quota(force=False, dir_hint=path_for("logs_dir"))
                        st["log_quota"] = {k: q.get(k) for k in ("over", "stopped", "total_mb", "limit_mb", "pct", "action", "enabled")}
                    except Exception:
                        st["log_quota"] = None
                    return self._json(st)
                if u.path == "/api/opstats":
                    # 운영 통계 (읽기 전용) — CLI `stats --full` · MCP wiki_status(full=true) 와 같은 함수.
                    # 집계가 DB 를 훑으므로 기간·상한을 둔다 (큰 DB 에서 화면이 멈추지 않게).
                    from .. import opstats as _ops
                    secs = [x for x in (qs.get("sections") or "").split(",") if x.strip()]
                    d_ = _ops.collect(p, days=float(qs.get("days") or 7),
                                      sections=secs or None, top=_qint(qs, "top", 8),
                                      bucket=(qs.get("bucket") or "day"),
                                      trend_days=(float(qs["trend_days"]) if qs.get("trend_days") else None))
                    # `users` 절은 **누가 얼마나 썼나** 다 — `/api/query_users` 와 같은 민감도이므로 같은 잣대(admin)를
                    # 쓴다. 이 경로 자체는 read 등급이라, 여기서 걸러 내지 않으면 viewer 가 우회로로 같은 값을 본다
                    # (2026-09-20 정렬 감사에서 발견).
                    return self._json(_ops.redact(d_, admin=bool(user and user.role == "admin")))
                if u.path == "/api/graph":
                    lim = _qint(qs, "limit", 150)
                    comm = qs.get("community")
                    return self._json(p.graph_export(limit=lim, community=int(comm) if comm not in (None, "", "all") else None))
                if u.path == "/api/graph/profile":            # 그래프 진단 (§2.5) — 큰 그래프에서는 몇 초 걸린다 (HEAVY_GET 의 /api/graph 접두로 읽기 슬롯)
                    from .. import graph_profile as _gp
                    prof = _gp.profile(p, include_eval=qs.get("eval") in ("1", "true"))
                    path = _gp.save(prof)
                    out = dict(prof, saved=path)
                    if qs.get("compare") in ("1", "true"):
                        hist = _gp.history(p.s)
                        prev = _gp.load(hist[1]["path"]) if len(hist) > 1 else None
                        out["compare"] = _gp.compare(prev, prof) if prev else None
                    return self._json(out)
                if u.path == "/api/graph/profile/history":
                    from .. import graph_profile as _gp
                    return self._json({"history": _gp.history(p.s), "dir": _gp.profiles_dir(p.s), "keep": int(getattr(p.s, "graph_profile_keep", 30) or 30)})
                if u.path == "/api/entity":
                    # id 또는 **이름**으로 연다 (2026-09-20). 질의 결과의 그래프 관계 표는 이름(`CL-55303`)만
                    # 들고 있어서, 화면이 id 규칙(`e:cl-55303`)을 스스로 만들어 내야 했다 — 규칙을 두 곳에 두지 않는다.
                    ent_id = qs.get("id", "")
                    if not ent_id and qs.get("name"):
                        from ..graph_rules import entity_id_for
                        nm = qs.get("name", "")
                        ent_id = entity_id_for(nm)
                        if not p.store.get_entity(ent_id):        # 표기가 조금 달라도 찾아 준다
                            hits = p.store.entity_fts('"%s"' % nm.replace('"', ""), 1)
                            if hits:
                                ent_id = hits[0][0]
                    d = p.entity_detail(ent_id)
                    return self._json(d or {"error": "엔티티를 찾지 못했습니다: %s" % (qs.get("name") or ent_id)},
                                      200 if d else 404)
                if u.path == "/api/docs":
                    meta = p.store.doc_meta_map()
                    return self._json([dict(d, **{k: (meta.get(d["doc_id"]) or {}).get(k) for k in ("doc_type", "ext_id", "date", "inferred")}) for d in p.store.list_docs()])
                if u.path == "/api/chunk":
                    cid = qs.get("id", "")
                    c = p.store.get_chunk(cid)
                    den = _doc_denied(p, user, (dict(c).get("doc_id") if c else "") or str(cid).rsplit("#", 1)[0])
                    if den:
                        return self._json(den, 403)
                    return self._json(dict(c) if c else {"error": "not found"})
                if u.path == "/api/doc":
                    # 문서 한 건의 전체 모습 (메타·청크·관계·엔티티). MCP wiki_doc 와 같은 함수.
                    from .. import querydebug as _qd
                    d = _qd.doc_detail(p, qs.get("id", ""), _qint(qs, "max_chars", 20000),
                                       role=(getattr(user, "role", "") or "viewer"))
                    return self._json(d, 403 if d.get("denied") else 200)
                if u.path == "/api/doc_chunks":
                    den = _doc_denied(p, user, qs.get("id", ""))
                    if den:
                        return self._json(den, 403)
                    return self._json([dict(c) for c in p.store.all_chunks(qs.get("id", ""))])
                if u.path == "/api/queries":
                    # 누가 무엇을 물었나. user/q/origin 으로 거른다. show_user_to_viewer=false 면 admin 외에는 사용자 id 를 가린다.
                    rows = p.store.queries(_qint(qs, "limit", 50), user=qs.get("user") or None,
                                           q=qs.get("q") or None, origin=qs.get("origin") or None)
                    is_admin = bool(user and user.role == "admin")
                    see_user = self._can_see_users(user)
                    me = user.name if user else ""
                    for r in rows:
                        if not see_user and (r.get("user") or "") != me:
                            r["user"] = "(비공개)" if r.get("user") else ""
                        if not is_admin:          # IP·에이전트는 activity 와 같은 규칙으로 admin 만
                            r["ip"] = r["agent"] = ""
                    return self._json({"rows": rows, "me": me, "admin": is_admin, "show_user": see_user})
                if u.path == "/api/query_users":
                    # 사용자별 질의 집계는 항상 admin 전용 — '누가 얼마나 썼나' 는 활동 목록보다 민감하다.
                    if not (user and user.role == "admin"):
                        return self._json({"error": "admin 전용", "detail": "사용자별 질의 집계는 admin 만 볼 수 있습니다"}, 403)
                    return self._json(p.store.query_users(_qint(qs, "limit", 50)))
                if u.path == "/api/query_trace":
                    r = p.store.get_query(_qint(qs, "id", 0))
                    # 질의 로그의 trace 에는 그때의 근거 청크와 컨텍스트 요약이 들어 있다 — 남의 것이면 막는다.
                    if self._not_my_request(r, user, auth):
                        return self._json({"error": "다른 사용자의 질의입니다 (전체 조회 권한 필요: 작업 'requests all')"}, 403)
                    return self._json(json.loads(r["trace"]) if r else {})
                if u.path == "/api/requests":
                    # scope=mine(기본) | all. 남의 요청까지 보려면 'run' 등급 이상 (security.json permissions 로 조정).
                    me = (user.name if user else "") or ""
                    scope = (qs.get("scope") or "mine").lower()
                    can_all = (auth is None) or auth.mode == "off" or bool(user and auth.allowed(user.role, "run", "requests all"))
                    only = None if (scope == "all" and can_all) else (me or None)
                    rows = p.store.requests(qs.get("kind") or None, _qint(qs, "limit", 100), user=only, q=qs.get("q") or None)
                    # 대기/진행 중인 내 요청도 같은 목록에서 보이게 한다 (완료된 것만 있으면 '내가 낸 게 어디 갔지' 가 된다)
                    live = []
                    try:
                        act = _mgr().activity(viewer=True, history=0)
                        for a in (act.get("running") or []) + (act.get("queued") or []):
                            if only and (a.get("user") or "") != only:
                                continue
                            live.append({"id": None, "token": a.get("token"), "ts": a.get("submitted"), "kind": a.get("kind"),
                                         "summary": a.get("label") or a.get("kind"), "status": a.get("status"),
                                         "user": a.get("user"), "ms": (a.get("elapsed_s") or 0) * 1000, "live": True,
                                         "stage": a.get("stage"), "pct": a.get("pct")})
                    except Exception:
                        pass
                    for r in rows:
                        r["status"] = "error" if r.get("error") else "done"
                    return self._json({"rows": rows, "live": live, "scope": "all" if only is None else "mine",
                                       "me": me, "can_all": can_all})
                if u.path == "/api/request":
                    r = p.store.get_request(_qint(qs, "id", 0), archive_dir=p.s.requests_archive_dir())
                    if self._not_my_request(r, user, auth):
                        return self._json({"error": "다른 사용자의 요청입니다 (전체 조회 권한 필요: 작업 'requests all')"}, 403)
                    if r and qs.get("brief"):
                        # brief=1: **단계별 trace 를 빼고** 답변 요약만. 진행 중 작업 목록에서 한 줄을 눌렀을 때처럼
                        # "그래서 뭐라고 답했지?" 만 필요한 자리에서 쓴다. 한 건 67KB → 약 19KB 로 줄어,
                        # 느린 질의가 여러 개 떠 있어 브라우저 연결(호스트당 6개)이 붐빌 때 먼저 도착한다.
                        res = r.get("result") or {}
                        r = dict(r, trace=None, brief=True,
                                 result={k: v for k, v in res.items() if k not in ("hits", "trace", "analysis")})
                    return self._json(r or {"error": "not found"}, 200 if r else 404)
                if u.path == "/api/analysis":
                    from .. import analysis as _an
                    rid = _qint(qs, "request_id", 0) or None
                    if rid and self._not_my_request(p.store.get_request(rid, archive_dir=p.s.requests_archive_dir()), user, auth):
                        return self._json({"error": "다른 사용자의 요청입니다 (전체 조회 권한 필요: 작업 'requests all')"}, 403)
                    focus = qs.get("focus") or None
                    r = _an.analyze(p, rid, focus=None if focus in (None, "", "all") else focus)
                    if r.get("error"):
                        return self._json({"error": r["error"]}, 404)
                    if qs.get("format") == "md":
                        data = r["markdown"].encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "text/markdown; charset=utf-8")
                        if qs.get("download"):
                            self.send_header("Content-Disposition", "attachment; filename=\"analysis_req_%s.md\"" % r["summary"]["request_id"])
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    return self._json({"summary": r["summary"], "paths": r["paths"], "markdown": r["markdown"], "report": r["report"] if qs.get("full") else None})
                if u.path == "/api/optimize/bundle":
                    # 가이드 + 지금 설정 + 이 요청의 실측 + 지시문을 한 파일로 (LLM 에게 통째로 준다)
                    from .. import optimize as _opt
                    rid = _qint(qs, "request_id", 0) or None
                    focus = qs.get("focus") or "all"
                    b = _opt.bundle_markdown(p, rid, None if focus in ("", "all") else focus)
                    if (b.get("summary") or {}).get("error"):
                        return self._json({"error": b["summary"]["error"]}, 404)
                    if qs.get("format") == "json":
                        return self._json({k: b[k] for k in ("markdown", "chars", "request_id", "focus", "summary")})
                    data = b["markdown"].encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header("Content-Disposition", "attachment; filename=\"optimize_bundle_req_%s_%s.md\"" % (b.get("request_id") or 0, b["focus"]))
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if u.path == "/api/optimize/guide":
                    from .. import optimize as _opt
                    data = _opt.guide_markdown(p.s).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    if qs.get("download"):
                        self.send_header("Content-Disposition", "attachment; filename=\"OPTIMIZATION_GUIDE.md\"")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if u.path == "/api/models":
                    from .. import models_catalog as _mc
                    bs = p.base_settings
                    # 앙상블(역할 단위 다중 LLM): 화면이 **유효값**(기본값이 합쳐지고 빈 멤버가 걸러진 것)과
                    # 파일 원본을 함께 봐야 "비워 두면 무엇이 상속되는지" 를 보여 줄 수 있다 — CLI `models ensemble show` 와 같은 값.
                    ens = {}
                    for role in Settings.LLM_ROLES:
                        try:
                            # role: 멤버가 provider/model 을 비웠을 때 상속되는 **역할 모델**.
                            # 화면이 "비우면 무엇이 되는지" 를 글로 보여 주고 멤버를 켤 때 자동으로 채우는 데 쓴다.
                            _rl = bs.role_llm(role)
                            ens[role] = {"effective": bs.effective_ensemble(role),
                                         "raw": dict(((bs.llm_roles or {}).get(role) or {}).get("ensemble") or {}),
                                         "role": {"provider": _rl.get("provider", ""), "model": _rl.get("model", "")},
                                         # 최악 소요 시간 — 곱셈(재시도 × 타임아웃 + 폴백)이 눈에 안 보여서 계산해 준다
                                         "budget": bs.ensemble_time_budget(role)}
                        except Exception as e:
                            ens[role] = {"error": str(e)[:200]}
                    return self._json({"providers": p.provider_status(), "settings": bs.to_dict(), "roles": list(Settings.LLM_ROLES),
                                       "role_attrs": list(Settings.LLM_ROLE_ATTRS), "policy": bs.role_policy_table(),
                                       "ensemble": ens,
                                       "ensemble_defaults": dict(Settings.ENSEMBLE_DEFAULTS, **(bs.llm_ensemble_defaults or {})),
                                       "ensemble_max_members": Settings.ENSEMBLE_MAX_MEMBERS,
                                       "ensemble_fallback_modes": list(Settings.ENSEMBLE_FALLBACK_MODES),
                                       # 요청이 실제로 잘리는 지점 — 앙상블 최악 소요와 견줘 보라고 함께 준다 (server.json timeouts)
                                       "query_timeout_s": float((( _mgr().cfg.get("timeouts") or {}).get("query_s") or 0)),
                                       "catalog_models": _mc.describe(bs)})
                if u.path == "/api/system":
                    return self._json(p.system_info(_qint(qs, "target_docs", 3000), _qint(qs, "daily_new", 20), _qint(qs, "horizon_days", 365)))
                if u.path == "/api/watch":
                    return self._json(self._status()["watcher"])
                if u.path == "/api/tuning":
                    return self._json({"tunables": _tuning.T.describe(p.s), "stages": _tuning.STAGES, "path": _tuning.TUNING_PATH,
                                       "overrides": _tuning.T.to_dict()})
                if u.path == "/api/limits":
                    # 단계별 시간 제한만 따로 (trace 렌더가 매번 /api/architecture 전체를 받지 않도록).
                    from ..architecture import stage_limits as _stage_limits
                    return self._json(_stage_limits(p.s, _mgr().cfg or {}))
                if u.path == "/api/architecture":
                    reg = _arch_registry()
                    last = {}
                    for kind in ("query", "build", "eval"):
                        rows = p.store.requests(kind, 1)
                        if rows:
                            r = p.store.get_request(rows[0]["id"])
                            if r and r.get("trace"):
                                last[kind] = {"id": r["id"], "summary": r["summary"], "ms": r["ms"], "trace": r["trace"]}
                    # limits: 단계별 시간 제한 (실측 ms 옆 괄호와 🧭 Pipeline 단계 카드가 같은 값을 쓴다)
                    from ..architecture import stage_limits as _stage_limits
                    lim = _stage_limits(p.s, _mgr().cfg or {})
                    return self._json(dict(reg, toggles=p.s.toggles.__dict__, settings=p.s.to_dict(), tuning=_tuning.T.to_dict(), last=last,
                                           limits=lim,
                                           providers={k: v for k, v in p.provider_status().items() if k != "catalog"}))
                if u.path == "/api/evolve/status":
                    return self._json(ev.status(p))
                if u.path == "/api/evolve/proposals":
                    rows = p.store.proposals(qs.get("status") or None)
                    # 목록에도 설명을 붙인다 (payload JSON 원문만 보여 주던 문제 — 2026-09-19).
                    # 규칙 사전은 describe_proposals 안에서 한 번만 읽는다.
                    try:
                        for r_, d_ in zip(rows, ev.describe_proposals(p, rows)):
                            r_["explain"] = d_
                    except Exception:
                        pass
                    return self._json(rows)
                if u.path == "/api/evolve/describe":
                    # 제안 한 건의 사람용 설명 — CLI `evolve show <id>` · MCP wiki_evolve(explain) 와 같은 내용
                    try:
                        pid_ = int(qs.get("id") or 0)
                    except ValueError:
                        return self._json({"error": "id 가 정수가 아닙니다"}, 400)
                    d_ = ev.describe_proposal(p, pid_)
                    return self._json(d_, 404 if d_.get("error") else 200)
                if u.path == "/api/wiki/list":
                    d = p.s.wiki_dir
                    files = sorted(f for f in os.listdir(d)) if os.path.isdir(d) else []
                    return self._json([f[:-3] for f in files if f.endswith(".md")])
                if u.path == "/api/wiki/page":
                    path = os.path.join(p.s.wiki_dir, qs.get("name", "") + ".md")
                    if not os.path.isfile(path):
                        return self._json({"error": "not found"}, 404)
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        return self._json({"name": qs.get("name"), "content": f.read()})
                if u.path == "/api/eval/questions":
                    return self._json(load_questions())
                if u.path == "/api/reset":
                    # 관리자 초기화 **미리보기** (GET 은 아무것도 바꾸지 않는다). 실행은 POST.
                    from .. import reset as _rs
                    sc = (qs.get("scope") or "").strip()
                    if not sc:
                        return self._json({"scopes": _rs.SCOPES})
                    o = {k: (qs.get(k) or "").lower() in ("1", "true", "on", "yes")
                         for k in ("clear_embed_cache", "include_security", "include_env",
                                   "include_proposals", "include_sessions", "purge_wiki_notes")}
                    pl = _rs.preview(p, sc, snapshot=(qs.get("snapshot") or "1").lower() not in ("0", "false", "off"),
                                     keep_wiki_notes=not o["purge_wiki_notes"],
                                     clear_embed_cache=o["clear_embed_cache"], include_security=o["include_security"],
                                     include_env=o["include_env"], include_proposals=o["include_proposals"],
                                     include_sessions=o["include_sessions"])
                    return self._json(pl, 400 if pl.get("error") else 200)
                if u.path == "/api/graph_rules":
                    # 그래프 빌드 규칙의 **구조화된 보기** — 원문 JSON(/api/rules) 과 달리 화면이 바로 쓸 수 있는 모양.
                    # CLI `graph-rules types|lint|test` · MCP wiki_graph_rules 와 같은 내용 (2026-09-19).
                    from .. import graph_rules as _gr
                    gr_rules = _gr.load_rules()
                    gr_types = _gr.known_types(gr_rules)
                    gr_schema = _gr.Schema(gr_rules.get("schema"), gr_types)
                    act = (qs.get("action") or "summary").strip()
                    if act == "lint":
                        return self._json(_gr.lint(gr_rules))
                    if act == "test":
                        ex = _gr.RuleExtractor(gr_rules)
                        dm = {"doc_type": qs.get("doc_type") or "", "ext_id": qs.get("ext_id") or ""}
                        ents, cnts, rels = ex.extract_chunk(qs.get("q") or "", "", "test", "테스트 문서",
                                                            dm if (dm["doc_type"] or dm["ext_id"]) else None)
                        return self._json({
                            "entities": [{"name": e.name, "type": e.type, "mentions": cnts.get(k, 0)} for k, e in ents.items()],
                            "relations": [{"src": ents[r.src].name if r.src in ents else r.src, "rel": r.rel,
                                           "dst": ents[r.dst].name if r.dst in ents else r.dst,
                                           "provenance": r.provenance, "weight": r.weight} for r in rels],
                            "unknown_rels": ex.schema.unknown_rels, "unknown_types": ex.schema.unknown_types})
                    return self._json({
                        "entity_types": gr_types, "value_types": _gr.describe_value_types(),
                        "relations": gr_schema.relations, "on_unknown": gr_schema.on_unknown,
                        "entities": [{"name": k, "type": (v or {}).get("type"), "aliases": (v or {}).get("aliases") or []}
                                     for k, v in (gr_rules.get("entities") or {}).items()],
                        "counts": {k: len(gr_rules.get(k) or []) for k in ("relation_patterns", "chunk_values", "id_patterns", "link_rules")},
                        "path": _gr.rules_path(),
                        "cli": "python -m llmwiki graph-rules types|lint|test \"<문장>\""})
                if u.path == "/api/rules":
                    from ..graph_rules import load_rules
                    return self._json(load_rules())
                # ---------------- v3 ----------------
                if u.path == "/api/health":
                    from ..health import run_health
                    return self._json(run_health(p, quick=qs.get("quick", "0") in ("1", "true"), for_build=qs.get("for_build", "0") in ("1", "true")))
                if u.path == "/api/config/effective":
                    return self._json(effective_settings(p.s))
                if u.path == "/api/env":
                    # .env 가시성 (admin — classify_api 가 GET /api/env 를 admin 으로 둔다): 키 이름·설정 여부·마스킹 값·LLMWIKI_* 오버라이드
                    from ..config import env_report as _env_report
                    return self._json(_env_report())
                if u.path == "/api/presets":
                    from .. import presets as _presets
                    return self._json({"presets": _presets.load_presets(), "path": _presets.presets_path()})
                if u.path == "/api/presets/diff":
                    from .. import presets as _presets
                    return self._json(_presets.diff(p.s, qs.get("name", "")))
                if u.path == "/api/prompts":
                    from .. import prompts as _prompts
                    rows = _prompts.list_prompts()
                    if qs.get("name"):
                        return self._json({"name": qs["name"], "content": _prompts.get(qs["name"]), "default": _prompts.DEFAULTS.get(qs["name"], "")})
                    return self._json(rows)
                if u.path == "/api/logs/files":
                    from .. import logging_setup as _ls
                    d = _ls.log_dir() or path_for("logs_dir")
                    return self._json({"dir": d, "files": _ls.files(d), "quota": _ls.check_quota(force=True, dir_hint=d)})
                if u.path == "/api/logs":
                    from .. import logging_setup as _ls
                    d = _ls.log_dir() or path_for("logs_dir")
                    # `file` 은 로그 폴더 안의 **이름**이지 경로가 아니다. 예전에는 그대로 join 해서
                    # `?file=../../../어딘가/secret` 로 폴더 밖의 .log 파일을 읽을 수 있었다 (GET 은 read 등급 = 익명).
                    name = os.path.basename(str(qs.get("file") or "llmwiki")).strip()
                    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name or ""):
                        return self._json({"error": "로그 파일 이름이 올바르지 않습니다 (영문·숫자·. _ - 만)", "file": qs.get("file")}, 400)
                    path = os.path.join(d, name + ".log")
                    if os.path.normpath(os.path.abspath(path)).lower() != os.path.normpath(os.path.join(os.path.abspath(d), name + ".log")).lower():
                        return self._json({"error": "로그 폴더 밖은 읽을 수 없습니다"}, 400)
                    run_id = qs.get("run") or ""
                    if qs.get("request"):
                        r = p.store.get_request(int(qs["request"]))
                        run_id = (r or {}).get("run_id") or "-"
                    since = qs.get("since")
                    since_s = None
                    if since:
                        unit = since[-1].lower()
                        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit)
                        since_s = float(since[:-1]) * mult if mult else float(since)
                    n = _qint(qs, "n", 200)
                    if not (run_id or qs.get("text") or qs.get("level") or since_s):
                        rows = _ls.tail(path, n)
                    else:
                        rows = _ls.grep(path, run_id=run_id or None, text=qs.get("text") or None, level=qs.get("level") or None, since_s=since_s, limit=n)
                    return self._json({"file": path, "rows": rows})
                if u.path == "/api/forensics":
                    # 기본을 **문제 건만**으로 둔다 (`only=problems`). 이 저장소 실측으로 기록 1,122건 중
                    # 1,025건이 정상이라, 최근 N건을 그냥 보여 주면 "왜 부실했나" 를 보러 온 사람이
                    # 찾는 것을 하나도 못 본다. `only=all` 이면 전부 — CLI `forensic list --only` 와 같다.
                    from .. import forensic as _fx
                    only = (qs.get("only") or "problems").strip()
                    return self._json(_fx.list_forensics(
                        p.store, _qint(qs, "limit", 50),
                        verdict=(qs.get("verdict") or None),
                        q=(qs.get("q") or ""),
                        only_problems=(only not in ("all", "") and not qs.get("verdict"))))
                if u.path == "/api/forensics/summary":
                    from .. import forensic as _fx
                    return self._json(_fx.summary(p.store))
                if u.path == "/api/forensic":
                    from .. import forensic as _fx
                    rid = _qint(qs, "request_id", 0)
                    row = _fx.get_forensic(p.store, rid)
                    if row and qs.get("rerun", "0") not in ("1", "true"):
                        return self._json(row)
                    req = p.store.get_request(rid)
                    if not req or not req.get("trace"):
                        return self._json({"error": "request not found"}, 404)
                    res = req.get("result") or {"query": req["summary"]}
                    diag = _fx.diagnose(req["trace"], res, p.s)
                    fid = row["id"] if row else _fx.record(p.store, rid, req.get("run_id") or "", res.get("query", req["summary"]),
                                                            ((res.get("evidence") or {}).get("verdict", "?")), res.get("groundedness"), diag, "manual")
                    return self._json(dict(diag, id=fid, request_id=rid, run_id=req.get("run_id"), query=res.get("query", req["summary"]),
                                           verdict=(res.get("evidence") or {}).get("verdict"), groundedness=res.get("groundedness")))
                if u.path == "/api/trials":
                    from .. import trials as _tr
                    return self._json(_tr.list_trials(p.store, _qint(qs, "limit", 50)))
                if u.path == "/api/trial":
                    from .. import trials as _tr
                    t = _tr.get_trial(p.store, qs.get("id", ""))
                    return self._json(t or {"error": "not found"}, 200 if t else 404)
                if u.path == "/api/trials/compare":
                    from .. import trials as _tr
                    refs = [x for x in (qs.get("ids") or "").split(",") if x.strip()]
                    c = _tr.compare(p.store, refs)
                    c["markdown"] = _tr.report_md(c)
                    return self._json(c)
                if u.path == "/api/pins":
                    from .. import pins as _pins
                    return self._json(_pins.load_pins())
                if u.path == "/api/query_rules":
                    from .. import query_rules as _qr
                    # `types` 는 화면이 절 이름을 하드코딩하지 않게 해 준다 — 유형을 늘려도 UI 가 따라온다.
                    return self._json({"rules": _qr.load_rules(), "path": _qr.rules_path(), "stats": _qr.stats(),
                                       "types": _qr.describe_types()})
                if u.path == "/api/query_rules/test":
                    from .. import query_rules as _qr
                    return self._json(_qr.expand(qs.get("q", ""), _tuning.T.get("syn_w"), _tuning.T.get("related_w"), _tuning.T.get("acronym_phrase")))
                if u.path == "/api/eval/candidates":
                    # **비교에 쓸 과거 질의 고르기** (2026-09-20). 기간·건수·피드백으로 추린 후보를
                    # 돌려주고, 사람이 그중에서 고른다 — 고르지 못하면 비교가 관심사와 상관없는
                    # 문항 위에서 돈다. CLI `trial candidates` 와 같은 함수.
                    from ..evalset import from_query_log as _fq
                    got = _fq(p.store, days=float(qs.get("days") or 7),
                              limit=_qint(qs, "limit", 50), only=str(qs.get("only") or ""))
                    return self._json({"candidates": got["questions"], "source": got["source"]})
                if u.path == "/api/eval/check":
                    # 점수를 내기 전에 "이 숫자를 믿어도 되나" — 평가셋 오염·기대 문서 누락·표본 크기.
                    # LLM 을 부르지 않으므로 빠르다 (읽기 등급).
                    from ..evalset import health as _eh
                    return self._json(_eh(p))
                if u.path == "/api/query_rules/lint":
                    from .. import query_rules as _qr
                    return self._json(_qr.lint())
                if u.path == "/api/query_rules/effect":
                    # 규칙별 누적 효과 (발화·후보·기여·인용) — 어떤 규칙이 실제로 답에 도움이 됐나
                    from .. import ruleeffect as _re
                    return self._json(_re.stats(p.store, _qint(qs, "limit", 200), str(qs.get("order") or "fired")))
                if u.path == "/api/query_rules/explain":       # 이 말이 어느 유형·방향으로 무엇을 끌어오나 (읽기, §2.4)
                    from .. import query_rules as _qr
                    term = str(qs.get("term") or "").strip()
                    if not term:
                        # 빈 term 에 200 + 빈 껍데기를 주면 화면이 "규칙이 없다" 로 잘못 읽는다 (CLI `rules explain` 도 용어를 요구한다)
                        return self._json({"error": "term 이 필요합니다 — 예: /api/query_rules/explain?term=PDCCH", "code": "bad_request"}, 400)
                    return self._json(_qr.explain(term))
                # ---------------- 협업 (휘발성 채팅 · 게시판) — 부수 기능: 꺼져 있으면 404 ----------------
                if u.path in ("/api/collab", "/api/collab/board"):
                    from .. import collab as _cb
                    if not _cb.enabled(p.s):
                        return self._json({"error": "협업 기능이 꺼져 있습니다 (토글 collab)", "enabled": False}, 404)
                    me = (user.name if user else "") or ""
                    if u.path == "/api/collab":
                        # touch 를 먼저 한다 — 그래야 내 접속이 이번 응답의 목록에 반영된다.
                        # rev 를 주면 바뀐 게 없을 때 목록을 생략한다 (30명 × 1초 폴링의 대역을 줄인다).
                        if qs.get("touch", "1") not in ("0", "false"):
                            _cb.touch(me, ip=self._ip())
                        return self._json(_cb.state(me, _qint(qs, "since", 0), _qint(qs, "rev", -1)))
                    return self._json(_cb.board(p.s, _qint(qs, "limit", 100), qs.get("user") or "", qs.get("q") or "",
                                                qs.get("unresolved", "0") in ("1", "true")))
                if u.path == "/api/embed/report":
                    from ..embed_run import embed_report
                    return self._json(embed_report(p))
                if u.path == "/api/build/status":
                    return self._json(p.build_status())
                if u.path == "/api/build/verify":
                    return self._json(p.store.verify(p.embedder.name if p.s.toggles.embed else None, fix=False, wiki_dir=p.s.wiki_dir))
                if u.path == "/api/corpus/lint":
                    return self._json({"summary": p.store.kv_get("lint_summary"), "rows": p.store.lint_rows(only_problems=qs.get("all", "0") not in ("1", "true"), limit=_qint(qs, "limit", 200)),
                                       "doc_types": p.store.doc_type_counts()})
                if u.path == "/api/corpus/types":
                    from .. import schema as _schema
                    return self._json({"schemas": _schema.load_schemas(), "dir": _schema.schemas_dir(), "examples": {dt: _schema.example_document(dt) for dt in _schema.doc_types()}})
                if u.path == "/api/mcp_sources":
                    from .. import mcp_client as _mcp
                    srcs = _mcp.load_sources()
                    # 형식이 잘못돼 **건너뛴** 소스를 함께 알린다. 그러지 않으면 "왜 내 소스가 안 보이지?" 가 된다.
                    return self._json({"sources": srcs, "path": _mcp.sources_path(), "enabled": p.s.toggles.mcp_sources,
                                       "external_rag": p.s.toggles.external_rag, "mcp_federation": p.s.toggles.mcp_federation,
                                       "invalid": _mcp.source_errors(),
                                       "summary": [_mcp.source_summary(k, v) for k, v in srcs.items()]})
                if u.path == "/api/memory":
                    from .. import memory as _mem
                    # status() 의 episodes 는 '개수' 다. 예전에는 같은 키에 목록을 덮어써서 화면에
                    # 개수 자리에 '[object Object],[object Object]…' 가 찍혔다 (2026-09-16).
                    _hl = _tuning.T.get("memory_half_life_days")
                    return self._json(dict(
                        _mem.status(p.store, _hl),
                        recent=_mem.episodes(p.store, _qint(qs, "limit", 20), str(qs.get("only") or ""), str(qs.get("q") or "")),
                        # 2026-09-19: 숫자만 있고 내용이 없던 두 가지를 목록으로 — "내 피드백이 반영됐나",
                        # "제안이 왜 사라졌지" 가 이 화면에서 답이 되게 한다 (docs/EVOLVE.md §메모리).
                        boosts=_mem.boost_table(p.store, _hl, top=_qint(qs, "boosts", 30)),
                        decaying=_mem.decaying_proposals(p.store, _hl, _tuning.T.get("memory_archive_strength"),
                                                         _qint(qs, "decaying", 20)),
                        archive_strength=_tuning.T.get("memory_archive_strength")))
                if u.path == "/api/precompute":
                    from .. import precompute as _pc
                    return self._json(_pc.cache_status(p.store))
                if u.path == "/api/agents":
                    from .. import headless as _hl
                    return self._json({"agents": _hl.load_agents(), "path": _hl.agents_path()})
                if u.path == "/api/themes":
                    tp = os.path.join(STATIC, "themes", "themes.json")
                    try:
                        with open(tp, "r", encoding="utf-8") as f:
                            return self._json(json.load(f))
                    except Exception:
                        return self._json({"themes": [{"key": "light", "title": "Light"}]})
                if u.path == "/api/time":
                    from .. import timeparse as _tp
                    return self._json(_tp.parse(qs.get("q", ""), p.s.timezone, p.s.week_start) or {"expr": None})
            self.send_error(404)

    # ---------------- POST ----------------
    def do_POST(self) -> None:
        u = urlparse(self.path)
        if u.path == "/mcp":
            return self._mcp("POST")
        if Handler.mcp_only and u.path not in ("/api/auth/login", "/api/auth/logout"):
            return self._json({"error": "mcp-only server: use POST /mcp"}, 404)
        try:
            with self.pipe.request_scope():
                self._do_post(u)
        except _rq.Rejected as e:
            self._rejected(e)
        except _pg.Cancelled as e:
            self._json({"error": "cancelled: %s" % e, "cancelled": True}, 499)
        except AuthError as e:      # 디스패치 안의 거부 (요청 단위 overrides 화이트리스트 등) → 401/403, 500 이 아니다
            self._deny(e, None, u.path, "?")
        except (BadRequest, TypeError, AttributeError, ValueError, KeyError, OverflowError) as e:
            self._bad_request(u.path, e)
        except Exception as e:
            self._server_error(u.path, e)

    def _bad_request(self, path: str, e: BaseException) -> None:
        """입력 모양이 잘못된 요청은 400 으로 돌려준다 (500 은 서버 결함만). 원인은 로그에 남겨 진짜 버그를 놓치지 않는다."""
        from .. import logging_setup as _ls
        try:
            _ls.log("warning", "bad request %s: %s: %s" % (path, type(e).__name__, e), "web", path=path, exc=type(e).__name__,
                    trace=traceback.format_exc()[-600:])
        except Exception:
            pass
        self._json({"error": "잘못된 요청입니다: %s" % str(e)[:300], "code": "bad_request", "type": type(e).__name__}, 400)

    def _do_post(self, u) -> None:
        body = self._body()
        auth = self._auth()
        host = self.headers.get("Host") or ""
        mgr = _mgr()
        # ---- 인증 자체 (로그인/로그아웃/비밀번호) ----
        if u.path == "/api/auth/login":
            mgr.check_access(self._ip(), "", "")
            try:
                with mgr.ticket("login", "read", client={"user": str(body.get("username") or "?")[:40], "role": "", "via": "login", "ip": self._ip(), "origin": "web"}, label="login"):
                    name, pw = str(body.get("username") or "").strip(), str(body.get("password") or "")
                    user = auth.login_local(name, pw)
            except _rq.Rejected as e:
                return self._rejected(e)
            if not user:
                auth.audit(User(name or "?", "viewer", "local"), "login", "auth", False, self._ip(), error="bad credentials")
                time.sleep(0.5)   # 무차별 대입 완화
                return self._json({"error": "아이디 또는 비밀번호가 올바르지 않습니다"}, 401)
            auth.audit(user, "login", "auth", True, self._ip())
            return self._json({"ok": True, "user": user.to_dict()}, cookies=[auth.make_cookie(user, self._https(), ip=self._ip(), agent=self.headers.get("User-Agent") or "")])
        if u.path == "/api/auth/logout":
            try:
                user = self._user()
            except AuthError:
                user = None
            if user:
                auth.audit(user, "logout", "auth", True, self._ip())
                auth.end_session(self.headers.get("Cookie") or "")
            return self._json({"ok": True}, cookies=[auth.clear_cookie()])
        try:
            user = self._user()
        except AuthError as e:      # 잘못된/폐기된 API 키 → 401 (게스트로 강등하지 않음)
            return self._deny(e, None, u.path, "?")
        mgr.check_access(self._ip(), user.name if user else "", user.role if user else "")
        if u.path == "/api/auth/password":
            if not user or user.via != "local":
                return self._json({"error": "로컬 계정으로 로그인한 경우에만 비밀번호를 바꿀 수 있습니다"}, 403)
            if not auth.check_password(user.name, str(body.get("old") or "")):
                return self._json({"error": "현재 비밀번호가 올바르지 않습니다"}, 403)
            try:
                auth.set_password(user.name, str(body.get("new") or ""))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            auth.audit(user, "password change", "auth", True, self._ip())
            return self._json({"ok": True})
        # ---- 인가: 작업 등급 → 역할·확인·문구·재인증 ----
        try:
            level, op = auth.authorize(user, "POST", u.path, body, self.headers, host)
        except AuthError as e:
            lv, op0 = ("?", u.path)
            try:
                from ..auth import classify_api
                lv, op0 = classify_api("POST", u.path, body)
            except Exception:
                pass
            return self._deny(e, user, op0, lv)
        if u.path == "/api/auth/users":
            return self._users_admin(body, user)
        if u.path == "/api/activity":
            # 본인 요청 취소 (admin 은 누구의 것이든)
            act = body.get("action") or "cancel"
            if act != "cancel":
                return self._json({"error": "unknown action"}, 400)
            tok = str(body.get("token") or "")
            r = mgr.cancel(tok, by=user.name if user else "?", reason=str(body.get("reason") or ""),
                           allow_owner=None if (user and user.role == "admin") else (user.name if user else "-"),
                           pending_ok=self._job_cancellable(tok, user))
            auth.audit(user, "cancel " + tok, "read", bool(r.get("ok")), self._ip())
            return self._json(r, 200 if r.get("ok") else 404)
        if u.path == "/api/auth/preview":
            # 권한 미리보기 켜기/끄기. 낮추기만 하므로 누구나 쓸 수 있다 (자기 권한을 줄이는 것뿐).
            want = _as_str(body.get("role"), "role")
            if want and want not in ROLES:
                raise BadRequest("role 은 %s 중 하나여야 합니다" % ", ".join(ROLES))
            https = (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
            self._json({"ok": True, "preview": want, "note": "권한을 낮춰 보는 중입니다. 해제하면 원래 권한으로 돌아옵니다." if want else ""},
                       cookies=[_authmod.preview_cookie(want, https)])
            auth.audit(user, "preview %s" % (want or "off"), "read", True, self._ip())
            return
        if u.path == "/api/profile":
            from .. import profiles as _pf
            if user is None or user.via == "anon":
                return self._json({"error": "로그인한 사용자만 설정을 서버에 저장할 수 있습니다 (게스트는 브라우저에만 유지됩니다)", "anonymous": True}, 403)
            act = _as_str(body.get("action"), "action", "save")
            if act == "reset":
                return self._json({"ok": _pf.reset(self.pipe.s.data_dir, user.name), "profile": {}})
            # 알 수 없는 action 이나 profile 누락을 '빈 프로파일 저장' 으로 흘려보내지 않는다 —
            # 오타 한 번에 저장해 둔 설정이 통째로 지워진다 (불러오기는 GET /api/profile).
            if act != "save":
                raise BadRequest("알 수 없는 action: %s (save | reset — 불러오기는 GET /api/profile)" % act)
            prof = _as_dict(body.get("profile"), "profile")
            if not prof:
                raise BadRequest("profile 이 비어 있습니다. 지우려면 action=reset 을 쓰세요")
            saved = _pf.save(self.pipe.s.data_dir, user.name, prof)
            return self._json({"ok": True, "user": user.name, "profile": saved})
        if u.path == "/api/admin/server":
            return self._admin_server(body, user, level)
        if u.path == "/api/schedule":
            return self._schedule_admin(body, user, level)
        if u.path == "/api/models/catalog":
            from .. import models_catalog as _mc
            act = _as_str(body.get("action"), "action", "save")
            try:
                if act == "add":
                    r = _mc.add_model(_as_dict(body.get("model"), "model"))
                elif act == "remove":
                    r = _mc.remove_model(_as_str(body.get("id"), "id"), _as_str(body.get("provider"), "provider") or None)
                elif act == "save":
                    r = _mc.save_catalog(_as_dict(body.get("catalog"), "catalog"))
                elif act == "reload":
                    r = _mc.load_catalog(force=True)
                else:
                    return self._json({"error": "unknown action"}, 400)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            auth.audit(user, "models catalog " + act, level, True, self._ip(), detail={"id": (body.get("model") or {}).get("id") or body.get("id")})
            return self._json({"ok": True, "catalog": _mc.describe(self.pipe.base_settings)})
        if u.path == "/api/security":
            act = body.get("action") or ""
            try:
                if act == "reload":
                    auth.reload()
                    out: Dict[str, Any] = {"ok": True, "mode": auth.mode}
                elif act == "set_permissions":
                    out = {"ok": True, "permissions": auth.set_permissions(_as_dict(body.get("permissions"), "permissions"))}
                elif act == "set_permission":
                    out = {"ok": True, "permissions": auth.set_permission(str(body.get("key") or ""), str(body.get("role") or ""))}
                elif act == "set_anonymous":
                    auth.cfg["anonymous_role"] = str(body.get("role") or "")
                    from ..auth import save_security
                    save_security(auth.cfg)
                    auth.reload()
                    out = {"ok": True, "anonymous_role": auth.anonymous_role}
                elif act == "set_cli":
                    c = auth.cfg.setdefault("cli", {})
                    if "default_role" in body:
                        c["default_role"] = str(body.get("default_role") or "admin")
                    if "require_login" in body:
                        c["require_login"] = bool(body.get("require_login"))
                    from ..auth import save_security
                    save_security(auth.cfg)
                    auth.reload()
                    out = {"ok": True, "cli": auth.cfg.get("cli")}
                else:
                    return self._json({"error": "unknown action"}, 400)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            auth.audit(user, "security " + act, level, True, self._ip(), detail={k: v for k, v in body.items() if k not in ("_password",)})
            return self._json(out)
        if u.path == "/api/docacl":
            # 문서 단위 접근 제어의 규칙 저장 · 영향 확인 (CLI `security docacl check` 와 같은 함수)
            from .. import docacl as _dacl
            act = body.get("action") or "check"
            if act == "check":
                # (이 메서드에서 `p` 는 뒤쪽에서야 바인딩된다 — self.pipe 를 직접 쓴다)
                return self._json(_dacl.check(self.pipe.store, str(body.get("role") or "viewer"),
                                              int(body.get("sample") or 200)))
            if act == "save":
                cur = _dacl.load()
                new = dict(cur)
                for k in ("enabled", "default_min_role", "rules", "deny_message"):
                    if k in body:
                        new[k] = body[k]
                bad = [r for r in (new.get("rules") or [])
                       if not isinstance(r, dict) or not str(r.get("prefix") or "").strip()
                       or str(r.get("min_role") or "viewer") not in ROLES]
                if bad:
                    return self._json({"error": "규칙이 올바르지 않습니다", "detail": "prefix 는 비울 수 없고 min_role 은 %s 중 하나여야 합니다" % ", ".join(ROLES), "bad": bad[:5]}, 400)
                path = _dacl.save(new)
                auth.audit(user, "docacl save", level, True, self._ip(),
                           detail={"rules": len(new.get("rules") or []), "default_min_role": new.get("default_min_role"), "enabled": new.get("enabled")})
                return self._json({"ok": True, "path": path, **_dacl.describe(_dacl.load(force=True))})
            return self._json({"error": "unknown action"}, 400)
        if u.path == "/api/apikeys":
            act = body.get("action") or "list"
            try:
                if act == "add":
                    r = auth.add_api_key(str(body.get("name") or ""), str(body.get("role") or "viewer"), str(body.get("note") or ""))
                    auth.audit(user, "apikeys add", level, True, self._ip(), detail={"name": r["name"], "role": r["role"], "id": r["id"]})
                    return self._json(dict(r, keys=auth.list_api_keys()))
                if act == "remove":
                    ok = auth.remove_api_key(str(body.get("id") or ""))
                    auth.audit(user, "apikeys remove", level, ok, self._ip(), detail={"id": body.get("id")})
                    return self._json({"ok": ok, "keys": auth.list_api_keys()}, 200 if ok else 404)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"keys": auth.list_api_keys()})
        if u.path == "/api/snapshot":
            act = body.get("action") or "create"
            p = self.pipe
            with mgr.ticket("snapshot", "exclusive", client=self._client(user), label="snapshot " + act):
                if act == "create":
                    r = _snap.create(p, str(body.get("tag") or "manual"), actor=user.name if user else "", reason=str(body.get("reason") or ""))
                elif act == "restore":
                    r = _snap.restore(p, str(body.get("name") or ""))
                elif act == "prune":
                    r = {"removed": _snap.prune(p, int(body.get("keep") or 3))}
                else:
                    return self._json({"error": "unknown action"}, 400)
            auth.audit(user, "snapshot " + act, level, True, self._ip(), detail={"name": body.get("name"), "tag": body.get("tag")})
            return self._json(r)
        if u.path == "/api/cli" and level in ("destructive", "rebuild"):
            argv = _as_argv(body.get("argv"))
            body["argv"] = list(argv) + (["--yes"] if "--yes" not in argv else [])   # 서버 게이트가 이미 확인했으므로 CLI 프롬프트 생략
        body["_actor"] = user.name if user else ""
        body["_client"] = self._client(user)
        body["_level"] = level
        body["_op"] = op
        self._dispatch_post(u, body)
        if level != "read":
            auth.audit(user, op, level, True, self._ip(), detail={k: v for k, v in body.items() if k not in ("_password", "_actor", "_client", "_level", "_op", "content", "rules", "agents", "sources", "presets")})

    # ---------------- 서버 모니터 (admin) ----------------
    def _admin_server(self, body: Dict[str, Any], user: Optional[User], level: str) -> None:
        mgr = _mgr()
        auth = self._auth()
        act = _as_str(body.get("action"), "action")
        try:
            if act == "set_limits":
                out: Dict[str, Any] = {"ok": True, "limits": mgr.set_limits(_as_dict(body.get("values"), "values"), save=bool(body.get("save", True)))}
            elif act == "reload":
                out = {"ok": True, "limits": mgr.reload()}
            elif act == "block":
                out = {"ok": True, "list": mgr.block(str(body.get("kind") or "ip"), str(body.get("value") or "").strip(), add=bool(body.get("add", True)))}
            elif act == "cancel":
                out = mgr.cancel(str(body.get("token") or ""), by=user.name if user else "admin", reason=str(body.get("reason") or "admin"))
            elif act == "kick":
                out = {"ok": True, "cancelled": mgr.kick_user(str(body.get("user") or ""))}
            elif act == "maintenance":
                out = {"ok": True, "limits": mgr.set_limits({"access.maintenance_mode": bool(body.get("enabled")),
                                                             **({"access.maintenance_message": str(body["message"])} if body.get("message") else {})}, save=True)}
            elif act == "circuit_reset":
                from .. import providers as _prov
                _prov.circuit_reset(body.get("key") or None)
                out = {"ok": True, "circuits": _prov.circuit_all()}
            elif act == "sessions":
                from .. import auth as _authmod
                sub = str(body.get("sub") or "list")
                if sub == "revoke":
                    out = {"ok": _authmod.sessions().revoke(str(body.get("sid") or ""))}
                elif sub == "revoke_user":
                    out = {"ok": True, "revoked": _authmod.sessions().revoke_user(str(body.get("user") or ""))}
                else:
                    out = {"ok": True}
                out["sessions"] = _authmod.sessions().list()
            elif act == "log_level":
                p = self.pipe
                apply_overrides(p.s, {"log_level": str(body.get("level") or "INFO")})
                if body.get("save"):
                    save_settings(p.s)
                p.reload()
                out = {"ok": True, "log_level": p.base_settings.log_level}
            else:
                return self._json({"error": "unknown action"}, 400)
        except (KeyError, ValueError, TypeError) as e:
            return self._json({"error": str(e)}, 400)
        auth.audit(user, "server " + act, level, True, self._ip(), detail={k: v for k, v in body.items() if k not in ("_password",)})
        return self._json(out)

    def _schedule_admin(self, body: Dict[str, Any], user: Optional[User], level: str) -> None:
        from .. import scheduler as _sc
        sch = _SCHED.get("scheduler")
        act = _as_str(body.get("action"), "action")
        auth = self._auth()
        try:
            if act == "save":
                _sc.save_tasks([_as_dict(t, "tasks[]") for t in _as_list(body.get("tasks"), "tasks")])
                if sch:
                    sch.reload()
                out: Dict[str, Any] = {"ok": True}
            elif act in ("add", "update"):
                _sc.upsert_task(_as_dict(body.get("task"), "task"))
                if sch:
                    sch.reload()
                out = {"ok": True}
            elif act == "remove":
                out = {"ok": _sc.remove_task(_as_str(body.get("name"), "name"))}
                if sch:
                    sch.reload()
            elif act in ("enable", "disable"):
                out = {"ok": _sc.set_enabled(_as_str(body.get("name"), "name"), act == "enable")}
                if sch:
                    sch.reload()
            elif act == "run":
                if not sch:
                    return self._json({"error": "스케줄러가 실행 중이 아닙니다 (serve 프로세스에서만)"}, 400)
                out = {"ok": True, "job": sch.run_now(_as_str(body.get("name"), "name"), by=user.name if user else "web")}
            elif act == "reload":
                if sch:
                    sch.reload()
                out = {"ok": True}
            else:
                return self._json({"error": "unknown action"}, 400)
        except (ValueError, KeyError, TypeError) as e:
            return self._json({"error": str(e)}, 400)
        auth.audit(user, "schedule " + act, level, True, self._ip(), detail={"name": body.get("name") or (body.get("task") or {}).get("name")})
        out["tasks"] = sch.list_tasks() if sch else _sc.list_tasks_static()
        return self._json(out)

    def _users_admin(self, body: Dict[str, Any], user: Optional[User]) -> None:
        auth = self._auth()
        act = body.get("action") or "list"
        try:
            if act == "add":
                auth.add_user(str(body.get("name") or ""), body.get("password"), str(body.get("role") or "viewer"), str(body.get("display") or ""))
            elif act == "remove":
                if user and body.get("name") == user.name:
                    return self._json({"error": "자기 자신은 삭제할 수 없습니다"}, 400)
                if not auth.remove_user(str(body.get("name") or "")):
                    return self._json({"error": "no such user"}, 404)
            elif act == "set_role":
                if user and body.get("name") == user.name and str(body.get("role")) != "admin":
                    return self._json({"error": "자기 자신의 admin 권한은 내릴 수 없습니다 (다른 admin 이 변경)"}, 400)
                auth.set_role(str(body.get("name") or ""), str(body.get("role") or "viewer"))
            elif act == "set_password":
                auth.set_password(str(body.get("name") or ""), str(body.get("password") or ""))
            elif act != "list":
                return self._json({"error": "unknown action"}, 400)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        auth.audit(user, "users " + act, "admin", True, self._ip(), detail={"name": body.get("name"), "role": body.get("role")})
        return self._json({"ok": True, "users": auth.list_users()})

    def _dispatch_post(self, u, body: Dict[str, Any]) -> None:
        p = self.pipe
        client = body.get("_client") or {}
        ov = _as_dict(body.get("overrides"), "overrides")   # 문자열·숫자 등이 오면 400 (예전에는 500 이었다)
        ov = _filter_overrides(ov, str(client.get("role") or ""), u.path)   # 역할이 못 쓰는 키(URL·경로·운영) → 403
        body["overrides"] = ov                               # 잡(빌드·평가)도 걸러진 사본만 본다
        actor = str(body.get("_actor") or "")
        level = str(body.get("_level") or "read")
        op = str(body.get("_op") or u.path)
        mgr = _mgr()
        try:
            if u.path == "/api/build":
                full = bool(body.get("full") or body.get("reset"))
                if body.get("channel"):
                    ch = str(body.get("channel"))
                    return self._json(self._start_job("build", lambda progress: self._do_build_channel(body, progress), label="build %s" % ch,
                                                      client=client, weight="exclusive" if body.get("full") else "soft", build_mode="channel"))
                return self._json(self._start_job("build", lambda progress: self._do_build(body, progress, actor),
                                                  label="build --full" if full else "build (incremental)", client=client,
                                                  weight="exclusive" if full else "soft", build_mode="full" if full else "incremental"))
            if u.path == "/api/eval":
                return self._json(self._start_job("eval", lambda progress: self._do_eval(body, progress), client=client, weight="read"))
            if u.path == "/api/sweep":
                # 파라미터 스윕 (llmwiki/sweep.py · docs/SWEEP.md): 값마다 /api/query/rerun 과 같은 경로로 재생 → 잡.
                # 결과 job.result = {"record", "compare", "text"}. 등급은 재실행과 같은 read (auth._READ_POST).
                from .. import sweep as _sw, rerun as _rr
                act = str(body.get("action") or "run")
                if act != "run":
                    return self._json({"error": "unknown action (run 만)"}, 400)
                key = _as_str(body.get("key"), "key").strip()
                if not key:
                    return self._json({"error": "key 가 필요합니다 (GET /api/sweep/keys 의 목록에서)"}, 400)
                rid = body.get("request_id")
                q = _as_str(body.get("query"), "query").strip()
                if rid in (None, "") and not q:
                    return self._json({"error": "request_id(또는 last) 나 query 가 필요합니다"}, 400)
                point = _as_str(body.get("from"), "from").strip() or None
                if point and point not in _rr.POINT_IDS:
                    return self._json({"error": "from 은 %s 중 하나여야 합니다" % ", ".join(_rr.POINT_IDS)}, 400)
                try:
                    info = _sw.classify_key(key)
                    spec = {"range": body.get("range")} if body.get("range") not in (None, "") else ({"values": body.get("values")} if body.get("values") is not None else None)
                    vals = _sw.resolve_values(key, spec, int(getattr(p.s, "sweep_max_values", 20) or 20))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                if info["kind"] in ("config", "role"):
                    _filter_overrides({info["key"]: vals[0]}, str(client.get("role") or ""), u.path)   # 역할이 못 쓰는 키(URL·경로·운영) → 403
                repeats = max(1, min(_as_int(body.get("repeats"), "repeats", 1), _sw.MAX_REPEATS))
                llm = _as_dict(body.get("llm"), "llm")
                if llm:
                    _filter_overrides({"llm_roles": llm}, str(client.get("role") or ""), u.path)
                label = "스윕 %s ← %s" % (info["key"], ("#%s" % rid) if rid not in (None, "") else "새 질의")
                do_log = bool(body.get("log", False))

                def _run_sweep(progress, rid=rid, key=key, vals=vals, repeats=repeats, point=point, llm=llm, q=q, ov=ov, do_log=do_log):
                    rec = _sw.run(p, rid if rid not in (None, "") else None, key, vals, repeats=repeats, from_point=point, llm=llm or None,
                                  query=q or None, progress=progress, overrides=ov or None, log=do_log)
                    cmp_ = _sw.compare(rec)
                    return {"record": rec, "compare": cmp_, "text": _sw.render_text(rec, cmp_)}
                return self._json(self._start_job("sweep", _run_sweep, label=label, client=client, weight="read"))
            if u.path == "/api/query/rerun":
                # 저장해 둔 중간 결과로 **특정 단계부터** 다시 (docs/RERUN.md).
                # 일반 질의와 같은 티켓·같은 request_scope 를 쓴다 — 재실행만 다른 길을 타면 결과를 믿을 수 없다.
                from .. import rerun as _rr
                rid = body.get("request_id")
                point = _as_str(body.get("from"), "from", "answer_llm")
                if rid in (None, ""):
                    return self._json({"error": "request_id 가 필요합니다"}, 400)
                # 남의 request_id 로 재실행하면 그 사람의 질의와 근거를 그대로 받아 본다 (id 는 순차 정수 = 열거 가능).
                try:
                    _rec = p.store.get_request(int(rid), archive_dir=p.s.requests_archive_dir())
                except (TypeError, ValueError):
                    _rec = None
                if self._not_my_request(_rec, User(str(client.get("user") or ""), str(client.get("role") or "viewer"), ""), self._auth()):
                    return self._json({"error": "다른 사용자의 요청입니다 (전체 조회 권한 필요: 작업 'requests all')"}, 403)
                if point not in _rr.POINT_IDS:
                    return self._json({"error": "from 은 %s 중 하나여야 합니다" % ", ".join(_rr.POINT_IDS)}, 400)
                names = [x for x in str(_as_str(body.get("preset"), "preset")).replace(";", ",").split(",") if x.strip()]
                label = "재실행 %s ← #%s" % (point, rid)
                with mgr.ticket("query", "read", client=client, label=label, token=str(body.get("progress_token") or "")[:64] or None) as tk:
                    try:
                        with p.request_scope(overrides=ov or None, presets=names, mode=_as_str(body.get("mode"), "mode"),
                                             actor=_actor_from_client(client)):     # 재실행도 문서 접근 제어를 받는다
                            res, tr = p.rerun(rid, point, log=bool(body.get("log", True)), debug=body.get("debug"))
                    except ValueError as e:
                        return self._json({"error": str(e), "points": _rr.POINTS}, 400)
                    tk["note"] = "재실행 " + point
                    tk["request_id"] = res.get("request_id")
                    return self._json({"result": res, "trace": tr, "rerun": {"of": rid, "from": point}})
            if u.path == "/api/query":
                q = (body.get("q") or "").strip()
                if not q:
                    return self._json({"error": "empty query"}, 400)
                # 클라이언트가 준 progress_token 이 곧 요청 관리자의 티켓 토큰 → GET /api/progress/<token> 으로 단계·대기열을 보고 DELETE 로 취소
                tok = str(body.get("progress_token") or "")[:64] or None
                with mgr.ticket("query", "read", client=client, label=q[:120], token=tok) as tk:
                    out = self._do_query(body, q, ov, actor=_actor_from_client(client))
                    r = out.get("result") if isinstance(out, dict) else None
                    if isinstance(r, dict):
                        # 1ms 만에 끝난 요청이 왜 그런지 활동 목록에서 바로 보이게 (실행이 안 된 것으로 오해하지 않도록)
                        tk["note"] = "캐시" if r.get("cached") else ("사전계산" if r.get("precomputed") else "")
                        # 활동 목록에서 끝난 작업을 눌렀을 때 그때의 결과·프로파일로 바로 갈 수 있게
                        tk["request_id"] = r.get("request_id")
                    return self._json(out)
            # 나머지 POST: 권한 등급으로 가중치 결정 (read/run → 읽기 슬롯, index → soft, 그 밖의 쓰기 → 배타)
            weight = _rq.weight_for_level(level, op, body)
            if u.path == "/api/cli":
                argv0 = body.get("argv") or []
                if isinstance(argv0, str):
                    import shlex
                    argv0 = shlex.split(argv0, posix=True)
                if argv0 and argv0[0] in ("build",):
                    weight = "exclusive" if any(a in ("--full", "--reset", "fts", "vector", "graph") for a in argv0) else "soft"
                elif argv0 and argv0[0] in ("serve", "watch", "mcp", "server", "schedule"):
                    weight = "none"
            kind = "search" if u.path == "/api/search" else ("cli" if u.path == "/api/cli" else ("job" if weight != "read" else "read"))
            # 빌드처럼 오래 걸리는 CLI 는 timeouts.cli_s 가 아니라 job_s 를 따른다 (읽기 슬롯을 잡지 않으므로 남을 막지 않는다).
            lim = None
            if kind == "cli" and weight in ("soft", "exclusive", "none"):
                lim = float((mgr.cfg.get("timeouts") or {}).get("job_s", 0) or 0)
            with mgr.ticket(kind, weight, client=client, label=op, timeout_s=lim,
                            build_mode="incremental" if weight == "soft" else ""):
                if u.path == "/api/debug/query":
                    # 질의 해부 (LLM 없음, 수 ms). CLI `inspect` · MCP wiki_inspect 와 같은 함수.
                    from .. import querydebug as _qd
                    with _with_overrides(p, ov, actor=_actor_from_client(client)):
                        return self._json(_qd.inspect_query(p, _as_str(body.get("q"), "q", "")))
                if u.path == "/api/search":
                    # 채널 검색 디버그. channels(여러 개) + mode(or|and|rrf) 를 받는다.
                    # 예전 형식(channel 하나)도 그대로 받아 준다 — CLI `search`, MCP wiki_search 와 같은 엔진이다.
                    from ..retrieval import channel_search, parse_channels
                    q = body.get("q", "")
                    chans = parse_channels(body.get("channels") if body.get("channels") is not None else body.get("channel", "fts"))
                    mode = str(body.get("mode") or "or").lower()
                    k = int(body.get("k", 8))
                    prof = Profiler("search", debug=p.s.debug_level)
                    with _with_overrides(p, ov, actor=_actor_from_client(client)):
                        out = channel_search(p.store, p.embedder, p.s, q, chans, mode=mode, k=k, prof=prof,
                                             require=body.get("require"), exclude=body.get("exclude"),
                                             acl=p.acl_filter(), doc_types=body.get("doc_types"))
                    tr = prof.finish()
                    label = "+".join(chans) + (("/" + out["mode"]) if len(chans) > 1 else "")
                    rid = p.store.log_request("search", "%s: %s" % (label, q), tr, None,
                                              {"channels": chans, "mode": out["mode"], "k": k,
                                               "doc_types": out.get("doc_types") or []}, keep=p.s.keep_requests)
                    dt = out.get("doc_types") or []
                    return self._json({"result": out, "trace": tr, "request_id": rid,
                                       "cli": "python -m llmwiki search %s \"%s\" --mode %s --k %d%s --json"
                                              % (",".join(chans), q, out["mode"], k,
                                                 (" --doc-types " + ",".join(dt)) if dt else "")})
                if u.path in ("/api/config", "/api/models/set"):
                    # action=reload: 파일을 **쓰지 않고** 디스크의 config.json 을 다시 읽어 들인다.
                    # 설정 파일을 서버 밖에서 고쳤을 때(배포 스크립트·에디터) 화면이 파일의 값과 달라 보이던 문제.
                    if _as_str(body.get("action"), "action", "") == "reload":
                        from ..config import load_settings as _load_settings
                        p.s = _load_settings()          # 요청 범위의 사본에 넣고
                        p.reload()                      # reload 가 그것을 전역(base_settings)으로 승격한다
                        return self._json({"ok": True, "reloaded": True, "settings": p.base_settings.to_dict(),
                                           "providers": p.provider_status(), "path": path_for("config")})
                    apply_overrides(p.s, _as_dict(body.get("settings"), "settings"))
                    save_settings(p.s)
                    p.reload()
                    return self._json({"settings": p.base_settings.to_dict(), "providers": p.provider_status()})
                if u.path == "/api/models/test":
                    with _with_overrides(p, ov):
                        return self._json(p.test_providers(body.get("which"), live=bool(body.get("live"))))
                if u.path == "/api/models/test_catalog":
                    # 카탈로그(models.json) 의 enabled 모델 전부를 (provider, model) 로 ping — 계획 §0.1-d. CLI: models test --catalog [--live]
                    with _with_overrides(p, ov):
                        return self._json(p.test_catalog(live=bool(body.get("live")), kinds=body.get("kinds") or None))
                if u.path == "/api/models/automap":
                    # 연결되는 모델만 골라 역할에 자동 배정 (제안 → 적용). CLI: models automap [--live] [--apply]
                    # apply=true 는 config.json 을 바꾸므로 '설정 저장' 등급이다 (classify_api 가 /api/models/* 를 그렇게 본다).
                    with _with_overrides(p, ov):
                        return self._json(p.automap_models(live=bool(body.get("live")), apply=bool(body.get("apply"))))
                if u.path == "/api/env":
                    # .env 다시 읽기 (admin): 파일 값으로 os.environ 을 덮어쓰고 설정을 다시 해석한다 (LLMWIKI_* 오버라이드·API 키가 바뀐다). CLI: config reload --env
                    from ..config import reload_env as _reload_env, load_settings as _load_settings
                    if _as_str(body.get("action"), "action", "reload") != "reload":
                        return self._json({"error": "unknown action (reload)"}, 400)
                    rep = _reload_env()
                    p.s = _load_settings()
                    p.reload()
                    rep["ok"] = True
                    return self._json(rep)
                if u.path == "/api/maintenance":
                    return self._json(p.maintenance(body.get("action", "")))
                if u.path == "/api/reset":
                    # 관리자 초기화 실행. 등급은 destructive — 확인 문구 + 비밀번호 재입력 모달이 먼저 뜬다
                    # (auth._DESTRUCTIVE_POST). 미리보기는 GET /api/reset?scope=… 이다.
                    from .. import reset as _rs
                    sc = _as_str(body.get("scope"), "scope", "")
                    if sc not in _rs.SCOPES:
                        return self._json({"error": "scope 는 %s 중 하나입니다" % " | ".join(_rs.SCOPES)}, 400)
                    r = _rs.run(p, sc, actor=actor,
                                snapshot=body.get("snapshot", True) is not False,
                                keep_wiki_notes=not body.get("purge_wiki_notes"),
                                clear_embed_cache=bool(body.get("clear_embed_cache")),
                                include_security=bool(body.get("include_security")),
                                include_env=bool(body.get("include_env")),
                                include_proposals=bool(body.get("include_proposals")),
                                include_sessions=bool(body.get("include_sessions")))
                    r["cli"] = "python -m llmwiki reset %s --apply" % sc
                    return self._json(r)
                if u.path == "/api/tuning":
                    act = _as_str(body.get("action"), "action", "set")
                    errors = {}
                    if act == "reload":
                        # 파일을 쓰지 않고 디스크의 tuning.json 을 다시 읽는다 (서버 밖에서 고쳤을 때). 예전에는 config reload 를 눌러야 했다.
                        p.reload_tuning()
                        return self._json({"ok": True, "reloaded": True, "path": _tuning.TUNING_PATH, "overrides": _tuning.T.to_dict(),
                                           "tunables": _tuning.T.describe(p.s)})
                    if act == "reset":
                        _tuning.T.reset(_as_str(body.get("key"), "key") or None)
                    else:
                        cfg = {}
                        for k, v in _as_dict(body.get("values"), "values").items():
                            spec = _tuning._INDEX.get(k)
                            if not spec:
                                errors[k] = "unknown"
                            elif spec["source"] == "config":
                                cfg[k] = v
                            else:
                                try:
                                    _tuning.T.set(k, v)
                                except (KeyError, ValueError) as e:
                                    errors[k] = str(e)
                        if cfg:
                            apply_overrides(p.s, cfg)
                            save_settings(p.s)
                    _tuning.save_tuning(_tuning.T)
                    p.reload_tuning()
                    if any(_tuning._INDEX.get(k, {}).get("source") == "config" for k in (body.get("values") or {})):
                        p.reload()
                    return self._json({"ok": not errors, "errors": errors, "overrides": _tuning.T.to_dict(),
                                       "tunables": _tuning.T.describe(p.s)})
                if u.path == "/api/watch":
                    act = body.get("action")
                    if act == "start":
                        p.s.toggles.auto_build = True
                        if body.get("interval"):
                            p.s.auto_build_interval = int(body["interval"])
                        if body.get("save"):
                            save_settings(p.s)
                        _ensure_watcher(p)
                    elif act == "stop":
                        p.s.toggles.auto_build = False
                        if body.get("save"):
                            save_settings(p.s)
                    elif act == "scan":
                        return self._json(p.check_changes())
                    elif act == "tick":
                        r = p.auto_build_tick(progress=lambda m: _WATCHER["log"].append("%s %s" % (time.strftime("%H:%M:%S"), m)))
                        return self._json(r)
                    return self._json(self._status()["watcher"])
                if u.path == "/api/feedback":
                    return self._json(ev.record_feedback(p, _as_int(body.get("query_id"), "query_id"), _as_int(body.get("feedback"), "feedback"), _as_str(body.get("note"), "note")))
                if u.path == "/api/evolve/apply":
                    return self._json(ev.apply_proposal(p, _as_int(body.get("id"), "id"), evaluate=bool(body.get("evaluate", True))))
                if u.path == "/api/evolve/reject":
                    return self._json(ev.reject_proposal(p, _as_int(body.get("id"), "id"), _as_str(body.get("note"), "note")))
                if u.path == "/api/evolve/review":
                    with _with_overrides(p, ov):
                        return self._json(ev.llm_review(p))
                if u.path == "/api/evolve/propose":
                    kind = _as_str(body.get("kind"), "kind")
                    # 적용할 수 없는 종류를 받아 두면 나중에 apply 에서 "unknown kind" 로 실패한 채 큐에 남는다.
                    # 받는 자리에서 evolve.KINDS 로 막는다 (Web·MCP·CLI 가 같은 목록을 본다).
                    if not kind or kind not in ev.KINDS:
                        raise BadRequest("kind 는 %s 중 하나여야 합니다 (받은 값: %r)" % (" | ".join(sorted(ev.KINDS)), kind))
                    try:
                        conf = float(body.get("confidence", 0.9))
                    except (TypeError, ValueError):
                        raise BadRequest("confidence 는 숫자여야 합니다")
                    pid = p.store.add_proposal(kind, _as_dict(body.get("payload"), "payload"), _as_str(body.get("reason"), "reason", "manual"), conf, "manual")
                    return self._json({"id": pid})
                if u.path == "/api/evolve/auto_apply":
                    # 스케줄러의 auto_apply 를 손으로 한 번 (신뢰도 하한 · 종류 제한 · 건수 상한 · 미리보기)
                    allow = body.get("kinds") or ev.auto_apply_kinds(p.s)
                    if isinstance(allow, str):
                        allow = [x.strip() for x in allow.split(",") if x.strip()]
                    try:
                        min_conf = float(body.get("min_confidence", p.s.evolve_min_confidence or 0.9))
                        limit = int(body.get("max_apply", 5))
                    except (TypeError, ValueError):
                        raise BadRequest("min_confidence 는 숫자, max_apply 는 정수여야 합니다")
                    dry = bool(body.get("dry_run"))
                    evaluate = bool(body.get("evaluate", True))
                    picked, applied, errors = [], [], []
                    for pr in p.store.proposals("proposed"):
                        if len(picked) >= limit:
                            break
                        if float(pr.get("confidence") or 0) < min_conf or pr.get("kind") not in allow:
                            continue
                        picked.append({"id": pr["id"], "kind": pr["kind"], "confidence": pr["confidence"]})
                    if not dry:
                        for pr in picked:
                            try:
                                r = ev.apply_proposal(p, int(pr["id"]), evaluate=evaluate)
                                applied.append({"id": pr["id"], "kind": pr["kind"], "status": r.get("status")})
                            except Exception as e:
                                errors.append({"id": pr["id"], "error": str(e)[:200]})
                    return self._json({"picked": picked, "applied": applied, "errors": errors,
                                       "min_confidence": min_conf, "kinds": allow, "dry_run": dry, "evaluate": evaluate})
                if u.path == "/api/wiki/page":
                    name = body.get("name", "")
                    if not name or "/" in name or "\\" in name:
                        return self._json({"error": "bad name"}, 400)
                    os.makedirs(p.s.wiki_dir, exist_ok=True)
                    with open(os.path.join(p.s.wiki_dir, name + ".md"), "w", encoding="utf-8") as f:
                        f.write(body.get("content", ""))
                    return self._json({"ok": True})
                if u.path == "/api/cli":
                    from ..cli import run_captured
                    argv = _as_argv(body.get("argv"))
                    if argv and argv[0] in ("serve", "watch", "mcp") and "--once" not in argv:
                        return self._json({"code": 1, "output": "%s 는 콘솔에서 실행할 수 없습니다 (서버가 이미 실행 중; watch 는 --once 로)." % argv[0]})
                    # 콘솔에서 돌린 `query`·`search`·`docs` 도 **콘솔을 연 사람의 신분**으로 돈다.
                    # 신분을 주지 않으면 pipe.actor 기본값이 admin 이라, viewer 가 콘솔 한 줄로 등급 문서를 읽을 수 있었다.
                    with p.request_scope(actor=_actor_from_client(client)):
                        return self._json(run_captured(argv, p.s, p, actor=str(body.get("_actor") or "web")))
                if u.path == "/api/rules":
                    from .. import graph_rules as _gr
                    new_rules = _as_dict(body.get("rules"), "rules")
                    # 저장 전에 정적 점검 — 깨진 정규식 하나가 빌드 전체를 멈추기 때문이다.
                    # 오류가 있으면 막고 이유를 돌려준다 (`force: true` 로 넘길 수 있다 — 고치는 중간 상태 저장용).
                    chk = _gr.lint(new_rules)
                    errs = [i for i in chk["issues"] if i["level"] == "error"]
                    if errs and not body.get("force"):
                        return self._json({"error": "규칙에 오류 %d건이 있어 저장하지 않았습니다" % len(errs),
                                           "issues": chk["issues"], "hint": "force:true 로 강제 저장할 수 있습니다"}, 400)
                    _gr.save_rules(new_rules)
                    p.reload()
                    return self._json({"ok": True, "issues": chk["issues"], "counts": chk["counts"]})
                if u.path == "/api/graph_rules":
                    # 사전 편집 (엔티티/별칭 추가) — CLI `graph-rules add-entity|add-alias` 와 같은 동작
                    from .. import graph_rules as _gr
                    act = str(body.get("action") or "")
                    gr_rules = _gr.load_rules()
                    ents = gr_rules.setdefault("entities", {})
                    name = str(body.get("name") or "").strip()
                    if act == "add_entity":
                        types = _gr.known_types(gr_rules)
                        etype = str(body.get("type") or "concept").strip()
                        if not name:
                            return self._json({"error": "이름이 비었습니다"}, 400)
                        if types and etype not in types:
                            return self._json({"error": "없는 type '%s'" % etype, "types": types}, 400)
                        if name in ents:
                            return self._json({"error": "이미 있습니다: %s" % name}, 400)
                        ents[name] = {"type": etype, "aliases": [str(a).strip() for a in (body.get("aliases") or []) if str(a).strip()]}
                        _gr.save_rules(gr_rules)
                        p.reload()
                        return self._json({"ok": True, "name": name, "rebuild": "build graph"})
                    if act == "add_alias":
                        key = next((k for k in ents if str(k).strip().lower() == name.lower()), None)
                        if key is None:
                            return self._json({"error": "규칙 사전에 '%s' 가 없습니다" % name}, 400)
                        cur = list(ents[key].get("aliases") or [])
                        add = [str(a).strip() for a in (body.get("aliases") or [])
                               if str(a).strip() and str(a).strip() not in cur and str(a).strip().lower() != key.lower()]
                        ents[key]["aliases"] = cur + add
                        _gr.save_rules(gr_rules)
                        p.reload()
                        return self._json({"ok": True, "entity": key, "added": add, "rebuild": "build graph"})
                    if act == "fill_defaults":
                        rep = _gr.fill_defaults()
                        p.reload()
                        return self._json(rep)
                    return self._json({"error": "action 은 add_entity | add_alias | fill_defaults"}, 400)
                # ---------------- v3 ----------------
                if u.path == "/api/presets":
                    from .. import presets as _presets
                    act = body.get("action", "apply")
                    if act == "apply":
                        # save=false 는 '미리보기': 요청 범위의 설정 사본에만 적용해 결과를 보여 주고 끝난다 (다른 사용자의 설정을 바꾸지 않는다).
                        # 질의에 프리셋을 적용하려면 /api/query 의 preset/mode 를, 서버 기본값으로 굳히려면 save=true(admin).
                        save = bool(body.get("save"))
                        r = _presets.apply(p.s, _presets.parse_names(body.get("names") or ",".join(body.get("list") or [])), save=save)
                        if save:
                            _tuning.save_tuning(_tuning.T)   # 오버레이 값을 파일로 승격
                            p.reload()
                        return self._json({k: v for k, v in r.items() if k != "prev"} | {"settings": p.s.to_dict(), "preview": not save,
                                                                                         "note": "" if save else "미리보기입니다 (서버 설정은 그대로). 질의에 적용: /api/query preset=… · 영구 적용: save=true"})
                    if act == "save":
                        _presets.save_presets(_as_dict(body.get("presets"), "presets"))
                        return self._json({"ok": True})
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/prompts":
                    from .. import prompts as _prompts
                    nm = _as_str(body.get("name"), "name")
                    if not nm:
                        raise BadRequest("name 이 필요합니다 (prompts list 로 이름 확인)")
                    if body.get("action") == "reset":
                        return self._json({"path": _prompts.reset(nm), "content": _prompts.get(nm)})
                    return self._json({"path": _prompts.set_text(nm, _as_str(body.get("content"), "content")), "content": _prompts.get(nm)})
                if u.path == "/api/pins":
                    from .. import pins as _pins
                    act = body.get("action", "add")
                    if act == "add":
                        return self._json(_pins.add_pin(doc=body.get("doc"), chunk=body.get("chunk"), query=body.get("query"), keywords_=body.get("keywords"),
                                                        always=bool(body.get("always")), doc_types=body.get("doc_types"), weight=float(body.get("weight") or 1.0),
                                                        note=body.get("note", ""), source="web"))
                    if act == "remove":
                        return self._json({"ok": _pins.remove_pin(body.get("id", ""))})
                    if act == "test":
                        return self._json(_pins.match_pins(p.store, body.get("q", "")))
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/collab":
                    from .. import collab as _cb
                    if not _cb.enabled(p.s):
                        return self._json({"error": "협업 기능이 꺼져 있습니다 (토글 collab)", "enabled": False}, 404)
                    # 이 메서드는 user 객체 대신 _actor/_client 를 받는다 (do_POST 가 인가를 끝낸 뒤 넘긴다)
                    me = actor or str(client.get("user") or "") or "(익명)"
                    act = _as_str(body.get("action"), "action", "say")
                    if act == "say":
                        return self._json(_cb.say(me, _as_str(body.get("text"), "text")))
                    if act == "touch":
                        # 아이콘은 고를 수 없다 — 접속 IP 의 SHA-1 으로 정해진다 (같은 자리 = 같은 아이콘)
                        x, y = body.get("x"), body.get("y")
                        return self._json(_cb.touch(me, float(x) if x is not None else None,
                                                    float(y) if y is not None else None, self._ip()))
                    if act == "post":
                        return self._json(_cb.post(p.s, me, _as_str(body.get("title"), "title", ""), _as_str(body.get("body"), "body", ""),
                                                   body.get("request_id"), _as_str(body.get("attachment"), "attachment", ""),
                                                   _as_str(body.get("kind"), "kind", "feedback")))
                    if act == "reply":
                        return self._json(_cb.reply(p.s, _as_str(body.get("id"), "id"), me, _as_str(body.get("text"), "text")))
                    if act == "resolve":
                        return self._json(_cb.resolve(p.s, _as_str(body.get("id"), "id"), me, bool(body.get("value", True))))
                    if act in ("remove", "clear"):
                        # 여기까지 온 것은 do_POST 의 인가(권한 표 'collab admin' = admin)를 통과했다는 뜻이다.
                        # 내 글 삭제는 admin 이 아니어도 되게 별도 경로(remove_mine)로 받는다.
                        return self._json(_cb.clear() if act == "clear" else _cb.remove(p.s, _as_str(body.get("id"), "id")))
                    if act == "remove_mine":
                        rows = [x for x in _cb.load_board(p.s) if x.get("id") == body.get("id")]
                        if not rows or rows[0].get("user") != me:
                            return self._json({"error": "내가 쓴 글만 지울 수 있습니다 (남의 글은 admin)"}, 403)
                        return self._json(_cb.remove(p.s, _as_str(body.get("id"), "id")))
                    return self._json({"error": "unknown action %s" % act}, 400)
                if u.path == "/api/query_rules":
                    from .. import query_rules as _qr
                    act = _as_str(body.get("action"), "action", "save")
                    if act == "save":
                        # 저장 내용이 사전(dict)이 아니면 거절한다. 예전에는 그대로 써서 이후 모든 질의가 깨졌다 (멍키 테스트로 발견)
                        _qr.save_rules(_as_dict(body.get("rules"), "rules"))
                        p.reload_tuning()
                        return self._json({"ok": True, "stats": _qr.stats()})
                    if act == "add":
                        r = _qr.add_rule(_as_str(body.get("type"), "type"), _as_str(body.get("term"), "term"), _as_list(body.get("values"), "values"), "web")
                        p.reload_tuning()
                        return self._json(r)
                    if act == "remove":
                        return self._json({"ok": _qr.remove_rule(_as_str(body.get("type"), "type"), _as_str(body.get("term"), "term"), body.get("value"))})
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/build/verify":
                    return self._json(p.store.verify(p.embedder.name if p.s.toggles.embed else None, fix=bool(body.get("fix")), wiki_dir=p.s.wiki_dir))
                if u.path == "/api/mcp_sources":
                    from .. import mcp_client as _mcp
                    act = body.get("action", "test")
                    if act == "test":
                        return self._json(_mcp.test_sources(p.s, body.get("names")))
                    if act == "ingest":
                        return self._json(_mcp.ingest(p.s, body.get("names"), since=body.get("since"), dry_run=bool(body.get("dry_run"))))
                    if act == "save":
                        # 저장은 **엄격하게** 본다: 여기서 잡아 주지 않으면 잘못된 소스가 조용히 건너뛰어져
                        # "저장은 됐는데 왜 안 붙지?" 가 된다 (질의 경로는 반대로 관대해야 한다 — load_sources 참고).
                        new = _as_dict(body.get("sources"), "sources")
                        for _n, _c in new.items():
                            if _n.startswith("_"):
                                continue
                            try:
                                _mcp.normalize_source(_n, _c)
                            except ValueError as e:
                                return self._json({"error": str(e), "source": _n}, 400)
                        _mcp.save_sources(new)
                        return self._json({"ok": True})
                    if act == "enrich":
                        return self._json(_mcp.enrich(p.s, body.get("q", "")))
                    if act == "retrieve":
                        return self._json({"results": _mcp.retrieve(p.s, body.get("q", ""), int(body.get("k") or 5), names=body.get("names"), include_fallback=True)})
                    if act == "tools":
                        cfg = _mcp.load_sources().get(str(body.get("name") or ""))
                        if not cfg:
                            return self._json({"error": "unknown source"}, 400)
                        return self._json({"name": body.get("name"), "tools": _mcp.remote_tools(str(body.get("name")), cfg)})
                    if act == "federated":
                        from .. import mcp as _m
                        tools = _m.federated_tools(p.s, refresh=True)
                        return self._json({"mcp_federation": p.s.toggles.mcp_federation, "tools": [t["name"] for t in tools],
                                           "errors": {k: v.get("error") for k, v in _m._FED_CACHE.items() if v.get("error")}, "plugins": _m.load_plugins(p.s)})
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/memory":
                    from .. import memory as _mem
                    act = body.get("action")
                    if act == "decay":
                        return self._json(_mem.decay(p.store, _tuning.T.get("memory_half_life_days"), _tuning.T.get("memory_archive_strength")))
                    if act == "consolidate":
                        return self._json(_mem.consolidate(p.store, _tuning.T.get("forensic_min_events")))
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/precompute":
                    from .. import precompute as _pc
                    act = body.get("action")
                    if act == "clear":
                        return self._json({"removed": _pc.clear_cache(p.store, stale_only=bool(body.get("stale")))})
                    if act == "doc_vectors":
                        return self._json(_pc.build_doc_vectors(p))
                    if act == "run":
                        return self._json(self._start_job("precompute", lambda progress: _pc.run(p, questions=body.get("questions"), from_log=int(body.get("from_log", 20)), progress=progress),
                                                          client=client, weight="read"))
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/trials":
                    from .. import trials as _tr
                    act = body.get("action", "run")
                    if act == "run":
                        # 문항 원천: evalset(기본) | queries(기간·조건으로 추린 질의 이력)
                        #          | list(**사람이 고른** 질의 이력, `pick`=query_log.id 목록)
                        # CLI `trial run --source` · `--pick` 과 같다.
                        src = None
                        # 기본은 **실제 질의 이력** (CLI `--source` 기본과 같다). 대개 알고 싶은 것은
                        # "진짜로 물어본 질문에서 좋아졌나" 이고, 이 저장소에서는 평가셋이 코퍼스에
                        # 색인돼 hit@k 가 오염돼 있다(`eval --check`).
                        srck = str(body.get("source") or "queries")
                        pick = body.get("pick") or []
                        if srck == "list" or pick:
                            from ..evalset import pick_questions
                            got = pick_questions(p.store, pick)
                            qs_, src = got["questions"], got["source"]
                            if not qs_:
                                return self._json({"error": "고른 질의가 없습니다 — 아래 목록에서 비교할 질의를 고르세요"}, 400)
                        elif srck == "queries":
                            from ..evalset import from_query_log
                            got = from_query_log(p.store, days=float(body.get("days") or 7),
                                                 limit=int(body.get("limit") or 30), only=str(body.get("only") or ""))
                            qs_, src = got["questions"], got["source"]
                            if not qs_:
                                # 기본값이 실행을 막으면 안 된다 — 평가셋으로 물러나되 **어느 원천인지 알린다**
                                qs_, src = None, None
                        else:
                            qs_ = load_questions(body["questions"]) if body.get("questions") else None
                        ovr = body.get("sets") or None
                        name = body.get("name") or ("trial-%s" % time.strftime("%m%d-%H%M%S"))

                        SRCN = {"queries": "실제 질의 이력", "list": "직접 고른 질의"}

                        def _run(progress, name=name, qs_=qs_, ovr=ovr, src=src):
                            progress("trial %s 시작 — 문항 원천: %s"
                                     % (name, ("%s %d문항" % (SRCN.get((src or {}).get("kind"), "질의 이력"), len(qs_)))
                                        if src else "평가셋(쓸 만한 질의 이력이 없어 물러났거나 직접 고름)"))
                            with _with_overrides(p, ov):
                                return _tr.run_trial(p, name, qs_, k=int(body.get("k", 5)), preset=body.get("preset"),
                                                     overrides=ovr, note=body.get("note", ""), progress=progress, source=src)
                        return self._json(self._start_job("trial", _run, client=client, weight="read"))
                    if act == "delete":
                        p.store.conn.execute("DELETE FROM trials WHERE trial_id=?", (int(body["id"]),))
                        p.store.conn.commit()
                        return self._json({"ok": True})
                    return self._json({"error": "unknown action"}, 400)
                if u.path == "/api/fusion/compare":
                    from .. import fusion as _fusion
                    qs_ = load_questions(body["questions"]) if body.get("questions") else load_questions()
                    methods = body.get("methods")
                    return self._json(self._start_job("fusion", lambda progress: {"rows": _fusion.compare_methods(p, qs_, methods, int(body.get("k", 5)))},
                                                      client=client, weight="read"))
                if u.path == "/api/agents":
                    from .. import headless as _hl
                    ag = _as_dict(body.get("agents"), "agents")
                    for k, v in ag.items():
                        if str(k).startswith("_"):
                            continue                      # `_comment` 같은 설명 키는 에이전트가 아니다
                        if not isinstance(v, dict) or not v.get("command"):
                            raise BadRequest("agents.%s 는 command 를 가진 객체여야 합니다" % k)
                    _hl.save_agents(ag)
                    p.reload()
                    return self._json({"ok": True})
                if u.path == "/api/forensic/expect":
                    from .. import forensic as _fx
                    rid = int(body.get("request_id", 0) or 0)
                    if not rid:
                        reqs = p.store.requests("query", 1)
                        rid = int(reqs[0]["id"]) if reqs else 0
                    docs_ = body.get("docs") or []
                    terms_ = body.get("terms") or []
                    if isinstance(docs_, str):
                        docs_ = [x.strip() for x in docs_.replace(";", ",").split(",") if x.strip()]
                    if isinstance(terms_, str):
                        terms_ = [x.strip() for x in terms_.replace(";", ",").split(",") if x.strip()]
                    rep = _fx.trace_expectation(p, rid, docs_, terms_, body.get("chunks") or [], note=str(body.get("note") or ""), propose=bool(body.get("propose")))
                    rep["text"] = _fx.format_expectation(rep)
                    return self._json(rep)
                if u.path == "/api/forensic/llm":
                    from .. import forensic as _fx
                    rid = int(body.get("request_id", 0))
                    req = p.store.get_request(rid)
                    if not req or not req.get("trace"):
                        return self._json({"error": "request not found"}, 404)
                    res = req.get("result") or {"query": req["summary"]}
                    diag = _fx.diagnose(req["trace"], res, p.s)
                    llm = p.llm_for("forensic")
                    extra = _fx.llm_forensic(llm, res.get("query", req["summary"]), req["trace"], diag,
                                             p.s.role_llm("forensic")["effort"],
                                             max_tokens=p.s.role_max_tokens("forensic", 1200)) if llm.available else None
                    return self._json({"heuristic": diag, "llm": extra, "available": llm.available})
                if u.path == "/api/analysis/insight":
                    # 상세 분석 리포트를 LLM 에게 읽히고 '무엇을 바꾸면 좋아지는지' 제안을 받는다
                    from .. import analysis as _an
                    rid = _as_int(body.get("request_id"), "request_id", 0) or None
                    focus = _as_str(body.get("focus"), "focus") or None
                    out = _an.llm_insight(p, rid, None if focus in (None, "", "all") else focus,
                                          propose=bool(body.get("propose")))
                    # 없는 요청은 **404** 로 (다른 조회 API 와 같은 규칙). 예전에는 200 + {"error": …} 라
                    # 클라이언트가 성공으로 오해했다. LLM 이 실패한 것과 요청이 없는 것은 다른 일이다.
                    if isinstance(out, dict) and str(out.get("error") or "").endswith("not found"):
                        return self._json(out, 404)
                    return self._json(out)
            self.send_error(404)
        except (_rq.Rejected, _pg.Cancelled):
            raise      # 요청 관리자의 거부(429/503)·취소(499)는 do_POST 가 상태 코드로 변환한다
        except AuthError:
            # 권한 거부가 **핸들러 안에서** 나는 경우(예: /api/sweep 이 스윕 키를 화이트리스트로 거르는 자리)도 401/403 이어야 한다.
            # 예전에는 아래 `except Exception` 이 삼켜 500 "AuthError: …" 으로 나갔다 — 클라이언트가 권한 문제를
            # 서버 고장으로 읽고, 운영자는 error.log 에서 가짜 결함을 쫓게 된다 (2026-09-19, verify_web 이 찾음).
            raise
        except (BadRequest, TypeError, AttributeError, ValueError, KeyError, OverflowError):
            raise      # 입력 모양이 잘못된 요청 → do_POST 가 400 으로 (500 은 서버 결함만)
        except Exception as e:
            self._server_error(u.path, e)

    def _do_query(self, body: Dict[str, Any], q: str, ov: Dict[str, Any], actor: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        p = self.pipe
        preset = _as_str(body.get("preset"), "preset")
        mode = _as_str(body.get("mode"), "mode")
        names = [x for x in str(preset).replace(";", ",").split(",") if x.strip()]
        # 요청 범위: 설정 사본(+overrides) · 튜닝 오버레이(+presets/mode) · **요청자 신분** — 다른 사용자의 동시 질의와 완전히 분리.
        # actor 로 문서 단위 접근 제어(docacl)가 걸린다. 2026-09-19 이전에는 신분이 검색까지 가지 않아 viewer 가 모든 문서를 근거로 받았다.
        with p.request_scope(overrides=ov or None, presets=names, mode=mode, actor=actor):
            res, tr = p.query(q, log=bool(body.get("log", True)), debug=body.get("debug"))
        if mode == "deep":
            names.append("deep_research")
        elif mode == "fast":
            names.append("speed")
        res["cli"] = _cli_equiv("query", q, ov, Toggles()) + (" --preset %s" % ",".join(names) if names else "")
        return {"result": res, "trace": tr}

    # ---------------- jobs ----------------
    def _start_job(self, kind: str, fn, label: str = "", client: Optional[Dict[str, Any]] = None, weight: str = "read", build_mode: str = "") -> Dict[str, Any]:
        """백그라운드 잡. job id 가 곧 요청 관리자 티켓 토큰 = progress 토큰 → /api/jobs/<id> 로 진행, DELETE /api/jobs/<id> 로 취소.
        weight: read(평가·trial·precompute 처럼 색인을 읽기만) · soft(증분 빌드: 정책상 질의 허용) · exclusive(전체/채널 리빌드)."""
        jid = uuid.uuid4().hex[:8]
        job = {"id": jid, "kind": kind, "status": "running", "started": time.time(), "log": [], "result": None, "error": None,
               "label": label or kind, "user": (client or {}).get("user"), "weight": weight}
        with _JOBS_LOCK:
            _JOBS[jid] = job
            # 끝난 job 은 최근 50개만 유지 (메모리)
            for k in [k for k, v in _JOBS.items() if v.get("status") != "running"][:-50]:
                _JOBS.pop(k, None)
        mgr = _mgr()
        pipe = self.pipe

        def progress(msg: str) -> None:
            job["log"].append("%s %s" % (time.strftime("%H:%M:%S"), msg))

        def runner() -> None:
            try:
                with mgr.ticket(kind, weight, client=client, label=label or kind, token=jid, build_mode=build_mode) as t:
                    if t.get("queue_wait_s", 0) > 1:
                        progress("대기 %.0f초 후 시작" % t["queue_wait_s"])
                    # 잡도 **잡을 낸 사람의 신분**으로 돈다. 2026-09-19 이전에는 신분 없이 열려 admin 으로 동작했고,
                    # 질의를 도는 잡(eval·sweep·precompute)이 문서 접근 제어를 받지 않았다.
                    # 빌드·유지보수처럼 색인을 쓰는 잡은 이미 등급(index/rebuild)으로 걸러진다.
                    with pipe.request_scope(actor=_actor_from_client(client)):
                        job["result"] = fn(progress)
                job["status"] = "done"
            except _pg.Cancelled as e:
                job["status"] = "cancelled"
                job["error"] = str(e)
                progress("■ 취소됨: %s" % str(e)[:200])
            except _rq.Rejected as e:
                job["status"] = "error"
                job["error"] = e.message
                progress("✖ 시작 불가: %s" % e.message)
            except Exception as e:
                job["status"] = "error"
                job["error"] = "%s\n%s" % (e, traceback.format_exc())
                progress("✖ 실패: %s" % str(e)[:300])
            finally:
                job["finished"] = time.time()
        threading.Thread(target=runner, daemon=True, name="job-%s-%s" % (kind, jid)).start()
        return {"job": jid}

    def _do_build(self, body: Dict[str, Any], progress, actor: str = "") -> Dict[str, Any]:
        p = self.pipe
        ov = body.get("overrides") or {}
        with _with_overrides(p, ov):
            do_reset = body.get("reset") if body.get("reset") is not None else bool(body.get("full"))
            if do_reset:
                if p.s.toggles.health_check and not body.get("force"):
                    # CLI 와 동일: 색인을 지우기 전에 health — 코퍼스 경로가 없으면 기존 색인을 지우지 않는다
                    from ..health import run_health
                    hr = run_health(p, quick=True, for_build=True)
                    if not hr["ok"]:
                        raise RuntimeError("health check failed before reset (기존 색인 유지): %s" % ", ".join(
                            c["name"] for c in hr["checks"] if not c["ok"] and c["level"] == "fail"))
                d = (self._auth().cfg.get("destructive") or {})
                r = p.reset_index(keep_logs=not body.get("purge_logs"), snapshot=bool(d.get("snapshot_before", True)), actor=actor,
                                  snapshot_keep=int(d.get("snapshot_keep", 3) or 3))
                progress("reset: cleared %d tables, kept_logs=%s, removed_wiki_pages=%d%s" % (
                    len(r["cleared_tables"]), r["kept_logs"], r["removed_wiki_pages"], (" · 스냅샷 %s (snapshot restore 로 복원 가능)" % r["snapshot"]) if r.get("snapshot") else ""))
            channels = body.get("channels") or None
            if isinstance(channels, str):
                channels = [x.strip() for x in channels.split(",") if x.strip()]
            res, tr = p.build(full=bool(body.get("full") or body.get("reset")), progress=progress, debug=body.get("debug"), channels=channels)
        flag = (" --full" if do_reset else " --full --no-reset") if (body.get("full") or do_reset) else ""
        if channels:
            flag += " --channels " + ",".join(channels)
        return {"result": res, "trace": tr, "cli": _cli_equiv("build", "", ov, Toggles()) + flag}

    def _do_build_channel(self, body: Dict[str, Any], progress) -> Dict[str, Any]:
        p = self.pipe
        ov = body.get("overrides") or {}
        ch = str(body.get("channel") or "")
        with _with_overrides(p, ov):
            res, tr = p.build_channel(ch, full=bool(body.get("full")), progress=progress, debug=body.get("debug"), force=bool(body.get("force")))
        return {"result": res, "trace": tr, "cli": "python -m llmwiki build %s%s --trace" % (ch, " --full" if body.get("full") else "")}

    def _do_eval(self, body: Dict[str, Any], progress) -> Dict[str, Any]:
        p = self.pipe
        ov = body.get("overrides") or {}
        k = int(body.get("k", 5))
        with _with_overrides(p, ov):
            if body.get("matrix"):
                combos = [("fts", 1, 0, 0), ("vector", 0, 1, 0), ("graph", 0, 0, 1), ("fts+vector", 1, 1, 0), ("fts+graph", 1, 0, 1), ("vector+graph", 0, 1, 1), ("all", 1, 1, 1)]
                rows = []
                for name, f, v, g in combos:
                    progress("eval combo " + name)
                    p.s.toggles.fts, p.s.toggles.vector, p.s.toggles.graph = bool(f), bool(v), bool(g)
                    r, _ = p.evaluate(k=k, log=False)
                    rows.append(dict(r["summary"], combo=name, rows=r["rows"]))
                return {"matrix": rows, "cli": "python -m llmwiki eval --matrix --k %d" % k}
            ro = bool(body.get("retrieval_only"))
            progress("평가 실행 중%s" % (" (검색 전용 — LLM 없이)" if ro else ""))
            r, tr = p.evaluate(k=k, log=not ro, retrieval_only=ro)
            # 놓친 문항의 원인까지 (평가셋의 expect_docs/expect_terms 를 그대로 쓴다 — 사람이 다시 적을 필요가 없다)
            if body.get("forensic"):
                from .. import forensic as _fx
                miss = [x for x in r["rows"] if not x.get("hit") and x.get("request_id")][: int(body.get("forensic_max") or 5)]
                r["forensics"] = []
                for i, x in enumerate(miss):
                    progress("원인 분석 %d/%d: %s" % (i + 1, len(miss), str(x.get("q"))[:40]))
                    try:
                        rep = _fx.trace_expectation(p, int(x["request_id"]), list(x.get("expect_docs") or []),
                                                    list(x.get("expect_terms") or []), note="eval --forensic")
                        r["forensics"].append({"q": x["q"], "request_id": x["request_id"], "summary": rep.get("summary"),
                                               "suggestions": rep.get("suggestions"), "lost_counts": rep.get("lost_counts"),
                                               "forensic_id": rep.get("forensic_id")})
                    except Exception as e:
                        r["forensics"].append({"q": x["q"], "error": str(e)[:160]})
            cli = _cli_equiv("eval", "", ov, Toggles()).replace(" --trace", "") + " --k %d" % k
            if ro:
                cli += " --retrieval-only"
            if body.get("forensic"):
                cli += " --forensic"
            return {"result": r, "trace": tr, "cli": cli}


class _noop:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def serve(pipe, host: str = "127.0.0.1", port: int = 8765, insecure: bool = False, mcp_only: bool = False) -> None:
    Handler.pipe = pipe
    Handler.host = host
    Handler.mcp_only = bool(mcp_only)
    Handler.auth = Auth(pipe.s, host)
    auth = Handler.auth
    mgr = _mgr()
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if auth.mode == "on" and not auth.has_any_login() and not auth.anonymous_role:
        print("!! security.json 에 사용자(users)·API 키·SSO 가 없고 anonymous_role 도 비어 있어 아무도 접근할 수 없습니다. 먼저: python -m llmwiki users add <id> --role admin (또는 apikey add)")
        if not insecure:
            raise SystemExit(2)
    if auth.mode == "off" and not loopback:
        if not insecure:
            print("!! %s 에 바인드하면 네트워크의 누구나 접근합니다. security.json 의 users/sso 를 설정하고(mode auto → on) 실행하거나, 정말로 인증 없이 열려면 --insecure." % host)
            raise SystemExit(2)
        print("!! --insecure: 인증 없이 %s 에 공개합니다 (파괴적 작업은 확인 문구만 요구)" % host)
    if not mcp_only:
        _ensure_watcher(pipe)
        try:
            from .. import scheduler as _sc
            _SCHED["scheduler"] = _sc.Scheduler(pipe, mgr)
            _SCHED["scheduler"].start()
        except Exception as e:
            print("!! scheduler start failed: %s" % e)
    c = mgr.cfg["concurrency"]
    # keep_alive_s: 0 이면 예전처럼 응답마다 연결을 끊는다(HTTP/1.0). 기본 30초는 연결을 재사용하되
    # 유휴 연결이 스레드를 계속 물고 있지 않게 정리한다.
    ka = float(c.get("keep_alive_s", 30) or 0)
    Handler.protocol_version = "HTTP/1.1" if ka > 0 else "HTTP/1.0"
    Handler.timeout = ka if ka > 0 else None
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    if host in ("0.0.0.0", "::", ""):
        # 0.0.0.0 은 접속 주소가 아니다 — 동료에게 알려 줄 실제 주소(호스트명·IP)를 같이 찍는다
        import socket as _sock
        try:
            addrs = sorted({ai[4][0] for ai in _sock.getaddrinfo(_sock.gethostname(), None, _sock.AF_INET)})
        except Exception:
            addrs = []
        print("접속 주소: http://127.0.0.1:%d/ (이 PC)  ·  http://%s:%d/ (동료)%s" % (
            port, _sock.gethostname(), port, "".join("  ·  http://%s:%d/" % (a, port) for a in addrs if not a.startswith("127."))))
    print("%s: http://%s:%d/%s  (Ctrl+C to stop)%s  [auth: %s%s%s]  [parallel reads %s · per-user %s · queue %s · reads_during_build %s%s]%s" % (
        "LLM Wiki MCP (Streamable HTTP)" if mcp_only else "LLM Wiki UI", host, port, "mcp" if mcp_only else "  · MCP: POST /mcp",
        "  [auto_build on: every %ss]" % pipe.s.auto_build_interval if (pipe.s.toggles.auto_build and not mcp_only) else "",
        auth.mode, (", users=%d, api_keys=%d, sso=%s" % (len(auth.cfg.get("users") or {}), len(auth.cfg.get("api_keys") or {}), "on" if auth.public_info()["sso"] else "off")) if auth.mode == "on" else "",
        (", anonymous=%s" % auth.anonymous_role) if (auth.mode == "on" and auth.anonymous_role) else "",
        c["max_parallel_reads"], c["max_parallel_per_user"], c["queue_max"], c["reads_during_build"],
        " · MAINTENANCE MODE" if (mgr.cfg.get("access") or {}).get("maintenance_mode") else "",
        ("  [scheduler: %d tasks]" % len(_SCHED["scheduler"].list_tasks())) if _SCHED.get("scheduler") else ""))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _WATCHER["stop"] = True
        if _SCHED.get("scheduler"):
            _SCHED["scheduler"].stop()
        httpd.server_close()
