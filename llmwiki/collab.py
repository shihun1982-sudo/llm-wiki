# -*- coding: utf-8 -*-
"""협업 — 휘발성 채팅 + 게시판 (Web UI 부수 기능).

설계 원칙 (docs/history/2026-09-16/IMPLEMENTATION_PLAN_0916.md §7)
- **본체에 영향을 주지 않는다.** 토글 `collab` 하나로 완전히 끌 수 있고, 이 모듈이 통째로 실패해도
  질의·빌드·MCP 는 그대로 동작해야 한다. 그래서 여기서는 예외를 밖으로 던지지 않고 값으로 돌려준다.
- **채팅은 휘발성**: 서버 메모리에만 있고 `retain_min` 이 지나면 사라진다. 서버를 재시작하면 비어 있다.
  남겨야 하는 것은 `/게시` 로 게시판에 올린다 — 그것만 파일(`data/collab/board.json`)에 쌓인다.
- **게시글은 요청에 연결할 수 있다**: 게시할 때 내 최근 작업(requests)을 골라 붙이면, 나중에 그 글을 보고
  "무슨 질의에 대한 피드백인가" 를 바로 따라갈 수 있다.

설정은 전부 `collab` 절(`server.json`)에 있다 — 코드에 상수를 두지 않는다:
  enabled · retain_min · max_messages · max_chars · board_max · board_keep_days
  bubble_font_start_px · bubble_font_step_px · bubble_font_step_min · bubble_font_max_px · idle_hide_min
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from . import atomicio

DEFAULTS: Dict[str, Any] = {
    "_comment": "Web UI 협업(채팅·게시판) 설정. 채팅은 서버 메모리에만 남는 휘발성이고 /게시 한 것만 board.json 에 쌓인다. "
                "끄려면 enabled=false (그러면 Web UI 에서 채팅창이 사라지고 API 는 404). 설명: docs/COLLAB.md",
    "enabled": True,
    "retain_min": 120,              # 채팅 메시지를 메모리에 두는 시간(분). 지나면 사라진다
    "max_messages": 500,            # 메모리에 두는 최대 메시지 수 (넘으면 오래된 것부터 버린다)
    "max_chars": 2000,              # 메시지 한 건의 최대 길이
    "board_max": 2000,              # 게시판 글 최대 수
    "board_keep_days": 365,         # 게시판 글 보존 기간(일). 0 = 지우지 않음
    # ---- 말풍선(캐릭터 위) 표시 ----
    "bubble_font_start_px": 12,     # 시작 글자 크기
    "bubble_font_step_px": 1,       # 얼마씩 키울지
    "bubble_font_step_min": 30,     # 몇 분마다 키울지 (기본 30분에 1px)
    "bubble_font_max_px": 28,       # 상한
    "idle_hide_min": 240,           # 이 시간 동안 아무 것도 안 하면 캐릭터를 숨긴다(분). 0 = 계속 표시
}

_LOCK = threading.RLock()
_MSGS: List[Dict[str, Any]] = []          # 휘발성 채팅 (서버 메모리)
_PRESENCE: Dict[str, Dict[str, Any]] = {}  # 사용자 → {x, y, since, last, emoji, text}
_SEQ = {"n": 0}
# 접속자 목록의 개정 번호. 누가 움직이거나 말하거나 들어오고 나갈 때만 올라간다.
# 화면이 `rev` 를 같이 보내면 **바뀐 게 없을 때 목록을 통째로 다시 보내지 않는다**
# (30명이 1초마다 폴링하면 그것만으로 초당 수백 KB가 된다).
_REV = {"n": 0}

# 접속자 아이콘 — 모뎀/임베디드 개발자가 쓰는 물건들. 고르지 않으면 **이름 해시**로 정해진다
# (랜덤이 아니므로 같은 사람은 늘 같은 아이콘이고, 새로고침해도 바뀌지 않는다).
# 고르면 그 값이 저장된다(`POST /api/collab {action:"touch", emoji:"🛠"}`).
# 동물·캐릭터는 넣지 않는다 (엔지니어 도구·계측기·부품만).
EMOJI = ["🛠", "🔧", "🔩", "⚙️", "🖥", "💻", "⌨️", "🖱", "🔌", "🔋", "📟", "📡", "🛰", "📶", "📈", "🧪",
         "🔬", "🧰", "🧲", "💾", "💿", "🖨", "🕹", "🧮", "⏱", "📐", "🔦", "🪛", "🪫", "🧑‍💻", "🤖", "📋"]
MAX_EMOJI_LEN = 8          # 이모지 하나(변형 선택자·ZWJ 포함) 정도만 허용 — 화면에 긴 문자열이 뜨지 않게


# ---------------------------------------------------------------- 설정
def config(settings: Any = None) -> Dict[str, Any]:
    """server.json 의 collab 절 + 기본값. reqmgr 의 설정 파일을 같이 쓴다 (운영 설정은 한 곳에)."""
    cfg = dict(DEFAULTS)
    try:
        from . import reqmgr as _rq
        got = (_rq.load_config() or {}).get("collab")
        if isinstance(got, dict):
            cfg.update({k: v for k, v in got.items() if not k.startswith("_")})
    except Exception:
        pass
    return cfg


def enabled(settings: Any = None) -> bool:
    if settings is not None:
        try:
            if not bool(getattr(settings.toggles, "collab", True)):
                return False
        except Exception:
            pass
    return bool(config().get("enabled", True))


def board_path(settings: Any) -> str:
    base = getattr(settings, "data_dir", "data")
    d = os.path.join(base, "collab")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "board.json")


# ---------------------------------------------------------------- 채팅 (휘발성)
def _prune(cfg: Dict[str, Any]) -> None:
    cutoff = time.time() - float(cfg.get("retain_min") or 120) * 60
    dropped = []
    while _MSGS and (_MSGS[0]["ts"] < cutoff or len(_MSGS) > int(cfg.get("max_messages") or 500)):
        dropped.append(_MSGS.pop(0))
    hide = float(cfg.get("idle_hide_min") or 0) * 60
    gone = []
    if hide > 0:
        gone = [n for n, p in _PRESENCE.items() if time.time() - p.get("last", 0) > hide]
        for name in gone:
            _PRESENCE.pop(name, None)
    # 보관 시간이 지난 말은 말풍선에서도 지운다
    for m in dropped:
        p = _PRESENCE.get(m["user"])
        if p and p.get("text_id") == m["id"]:
            p["text"], p["text_id"] = "", 0
            gone.append(m["user"])
    if gone:
        _REV["n"] += 1


def say(user: str, text: str, settings: Any = None) -> Dict[str, Any]:
    """채팅 한 줄. 반환에 `command` 가 있으면 화면이 그 명령(예: /게시)을 이어서 처리한다."""
    cfg = config()
    text = str(text or "").strip()[: int(cfg.get("max_chars") or 2000)]
    if not text:
        return {"error": "빈 메시지"}
    with _LOCK:
        _SEQ["n"] += 1
        m = {"id": _SEQ["n"], "ts": time.time(), "user": user or "(익명)", "text": text}
        _MSGS.append(m)
        touch(user)
        # 말풍선에 쓸 마지막 말은 여기서 기록해 둔다 — 조회할 때마다 메시지를 거꾸로 훑으면
        # 30명 × 500건 = 매 폴링 15,000회 비교가 된다 (O(N×M) → O(1)).
        _PRESENCE[user]["text"] = text
        _PRESENCE[user]["text_id"] = m["id"]
        _REV["n"] += 1
        _prune(cfg)
    return {"ok": True, "message": m, "command": parse_command(text)}


def parse_command(text: str) -> Optional[Dict[str, Any]]:
    """`/게시 [제목]` · `/post [제목]` 만 명령이다. 그 외는 평범한 채팅."""
    m = re.match(r"^/(게시|post)\b\s*(.*)$", str(text or "").strip(), re.S)
    if not m:
        return None
    return {"name": "post", "title": (m.group(2) or "").strip()}


def default_emoji(key: str) -> str:
    """아이콘은 **고르지 않는다** — 접속 IP 의 SHA-1 으로 정해진다.

    왜 IP 인가: 같은 자리(같은 PC)에서 오면 늘 같은 아이콘이라 "저 아이콘은 누구 자리" 가 눈에 익는다.
    로그인하지 않은 게스트도 서로 구분된다. 파이썬의 hash() 는 프로세스마다 시드가 달라
    서버를 재시작하면 값이 바뀌므로 쓰지 않는다.
    """
    import hashlib
    h = hashlib.sha1((key or "").encode("utf-8")).digest()
    return EMOJI[h[0] % len(EMOJI)]


def touch(user: str, x: Optional[float] = None, y: Optional[float] = None, ip: str = "") -> Dict[str, Any]:
    """접속 표시(캐릭터). x/y 는 화면 비율(0~1)로 저장해 해상도가 달라도 자리를 지킨다.
    아이콘은 `ip` 로 정해진다 (사용자가 고를 수 없다)."""
    with _LOCK:
        p = _PRESENCE.get(user)
        key = ip or user
        if not p:
            p = {"user": user, "since": time.time(), "emoji": default_emoji(key), "ip_key": key,
                 "x": 0.06, "y": 0.35, "text": "", "text_id": 0}
            _PRESENCE[user] = p
            _REV["n"] += 1
        if x is not None:
            nx = max(0.0, min(1.0, float(x)))
            if nx != p["x"]:
                _REV["n"] += 1
            p["x"] = nx
        if y is not None:
            ny = max(0.0, min(1.0, float(y)))
            if ny != p["y"]:
                _REV["n"] += 1
            p["y"] = ny
        # IP 가 바뀌었거나(자리를 옮겼다) 예전 목록의 아이콘이 남아 있으면 지금 기준으로 다시 정한다
        if p.get("ip_key") != key or p.get("emoji") not in EMOJI:
            p["ip_key"] = key
            p["emoji"] = default_emoji(key)
            _REV["n"] += 1
        p["last"] = time.time()
        return dict(p)


def font_px(since: float, cfg: Optional[Dict[str, Any]] = None) -> int:
    """머문 시간이 길수록 말풍선 글씨가 커진다 (기본 30분에 1px, 상한까지). 설정은 관리자 모드에서."""
    cfg = cfg or config()
    start = float(cfg.get("bubble_font_start_px") or 12)
    step = float(cfg.get("bubble_font_step_px") or 1)
    per = max(1.0, float(cfg.get("bubble_font_step_min") or 30))
    cap = float(cfg.get("bubble_font_max_px") or 28)
    mins = max(0.0, (time.time() - float(since or time.time())) / 60.0)
    return int(min(cap, start + step * int(mins / per)))


def state(user: str = "", since_id: int = 0, rev: int = -1) -> Dict[str, Any]:
    """폴링용: since_id 이후 메시지 + 접속자(캐릭터) 목록.

    `rev` 를 주면 **바뀐 것이 없을 때 목록을 생략**한다(`people_unchanged: true`).
    30명이 1초마다 폴링하는 상황에서 대부분의 응답이 이 경로로 빠진다 — 대역과 직렬화 비용을 크게 줄인다.
    말풍선 글자 크기는 시간에 따라 커지므로, 목록을 생략해도 화면이 이미 가진 값으로 계산할 수 있다.
    """
    cfg = config()
    with _LOCK:
        _prune(cfg)
        msgs = [m for m in _MSGS if m["id"] > int(since_id or 0)]
        out: Dict[str, Any] = {"messages": msgs, "last_id": _SEQ["n"], "rev": _REV["n"], "me": user,
                               "config": {k: cfg.get(k) for k in
                                          ("retain_min", "bubble_font_start_px", "bubble_font_step_px",
                                           "bubble_font_step_min", "bubble_font_max_px", "idle_hide_min")}}
        if rev >= 0 and int(rev) == _REV["n"]:
            out["people_unchanged"] = True
            return out
        now = time.time()
        people = [dict(p, font_px=font_px(p.get("since"), cfg), last_text=str(p.get("text") or "")[:200],
                       minutes=int((now - float(p.get("since") or now)) / 60))
                  for p in _PRESENCE.values()]
        for p in people:
            p.pop("text", None)
            p.pop("text_id", None)
            p.pop("ip_key", None)          # 남의 IP 를 화면에 내보내지 않는다
        out["people"] = sorted(people, key=lambda x: x["user"])
        return out


def clear() -> Dict[str, Any]:
    with _LOCK:
        n = len(_MSGS)
        _MSGS.clear()
        return {"ok": True, "removed": n}


# ---------------------------------------------------------------- 게시판 (영속)
def load_board(settings: Any) -> List[Dict[str, Any]]:
    d = atomicio.read_json(board_path(settings))
    rows = d.get("posts") if isinstance(d, dict) else d
    return [r for r in (rows or []) if isinstance(r, dict)]


def _save_board(settings: Any, posts: List[Dict[str, Any]]) -> str:
    cfg = config()
    keep_days = float(cfg.get("board_keep_days") or 0)
    if keep_days > 0:
        cutoff = time.time() - keep_days * 86400
        posts = [p for p in posts if float(p.get("ts") or 0) >= cutoff]
    posts = posts[-int(cfg.get("board_max") or 2000):]
    return atomicio.write_json(board_path(settings), {"_comment": "Web UI 게시판. 채팅에서 /게시 로 올린 글. 설정: server.json 의 collab 절",
                                                      "posts": posts})


def post(settings: Any, user: str, title: str, body: str, request_id: Optional[int] = None,
         attachment: str = "", kind: str = "feedback") -> Dict[str, Any]:
    """게시글 작성. request_id 를 주면 그 요청(질의·빌드)과 연결한다."""
    title = str(title or "").strip()[:200]
    body = str(body or "").strip()[:20000]
    if not title and not body:
        return {"error": "제목이나 내용 중 하나는 있어야 합니다"}
    posts = load_board(settings)
    row = {"id": uuid.uuid4().hex[:10], "ts": time.time(), "user": user or "(익명)", "kind": kind,
           "title": title or (body.splitlines() or [""])[0][:60], "body": body,
           "request_id": int(request_id) if request_id else None,
           "attachment": str(attachment or "")[:20000], "replies": [], "resolved": False}
    # 연결한 요청의 요약을 함께 남긴다 — 나중에 그 요청이 정리(keep_requests)돼도 무슨 작업이었는지 남게
    if row["request_id"]:
        try:
            r = settings_store(settings).requests(limit=1000)
            for x in r:
                if x["id"] == row["request_id"]:
                    row["request_summary"] = x.get("summary")
                    row["request_kind"] = x.get("kind")
                    break
        except Exception:
            pass
    posts.append(row)
    _save_board(settings, posts)
    return {"ok": True, "post": row}


def settings_store(settings: Any):
    """게시 시 요청 요약을 붙이기 위한 store — 실패하면 그냥 건너뛴다(부수 기능이 본체를 막지 않는다)."""
    from .store import Store
    return Store(settings.db_path)


def reply(settings: Any, post_id: str, user: str, text: str) -> Dict[str, Any]:
    posts = load_board(settings)
    for p in posts:
        if p.get("id") == post_id:
            p.setdefault("replies", []).append({"ts": time.time(), "user": user or "(익명)", "text": str(text or "")[:4000]})
            _save_board(settings, posts)
            return {"ok": True, "post": p}
    return {"error": "없는 글: %s" % post_id}


def resolve(settings: Any, post_id: str, user: str, value: bool = True) -> Dict[str, Any]:
    posts = load_board(settings)
    for p in posts:
        if p.get("id") == post_id:
            p["resolved"] = bool(value)
            p["resolved_by"] = user
            p["resolved_at"] = time.time()
            _save_board(settings, posts)
            return {"ok": True, "post": p}
    return {"error": "없는 글: %s" % post_id}


def remove(settings: Any, post_id: str) -> Dict[str, Any]:
    posts = load_board(settings)
    left = [p for p in posts if p.get("id") != post_id]
    if len(left) == len(posts):
        return {"error": "없는 글: %s" % post_id}
    _save_board(settings, left)
    return {"ok": True, "removed": post_id}


def board(settings: Any, limit: int = 100, user: str = "", q: str = "", unresolved_only: bool = False) -> Dict[str, Any]:
    posts = load_board(settings)
    if user:
        posts = [p for p in posts if p.get("user") == user]
    if q:
        ql = q.lower()
        posts = [p for p in posts if ql in str(p.get("title", "")).lower() or ql in str(p.get("body", "")).lower()]
    if unresolved_only:
        posts = [p for p in posts if not p.get("resolved")]
    posts = list(reversed(posts))[:max(1, int(limit or 100))]
    return {"posts": posts, "total": len(load_board(settings)), "path": board_path(settings)}
