# -*- coding: utf-8 -*-
"""요청 원장 — 서버로 들어온 **모든 요청**의 수명을 파일에 남긴다 (2026-09-23).

왜 있나
  요청이 기록 없이 사라지는 경로가 9가지 있었다(docs/history/2026-09-23/IMPLEMENTATION_PLAN_0923.md §4.1):
  성공했을 때만 `requests` 에 쓰고, 잠금 실패는 삼키고, 거절(429/503/403/413)은 메모리 카운터만 올리고,
  스냅샷 복원은 DB 를 통째로 되감고, `reset logs` 는 DB 행과 보관 파일을 함께 지우고,
  백그라운드 잡은 아예 기록하지 않고, 진행 정보는 5분·최근 목록은 500건(메모리)만 산다.

왜 SQLite 가 아니라 JSONL 인가
  원장이 기록해야 할 **대표 사건이 "DB 가 잠겨서 기록하지 못했다"** 이다. 같은 DB 에 동기로 쓰면 같은 이유로 또 사라진다.
  append 는 쓰기 잠금이 없고, 스냅샷 복원(DB 파일 교체)에도 되감기지 않으며, 한 줄이 깨져도 나머지는 읽힌다.

어떻게 쓰나 (요청 하나 = 두 줄)
  open  : 접수 즉시. **프로세스가 죽어도 "들어왔다" 는 남는다.**
  close : 종료 시. close 가 없는 항목은 읽을 때 `unknown`(중단)으로 보인다 — 사라지지 않는다.
  update: 그 사이 덧붙임(대기열 위치·락 모드·request_id …). 없어도 된다.

설정: server.json 의 `ledger` 절 (llmwiki/reqmgr.py DEFAULTS). 설명: docs/REQUEST_LEDGER.md
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import threading
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import ROOT

#: 요청이 끝나는 방식. 화면 필터·집계가 이 목록에 묶이므로 늘릴 때는 UI/문서도 같이 본다.
STATUSES = ("queued", "running", "done", "error", "cancelled", "timeout", "rejected", "unknown")

#: 한 줄이 이보다 길면 잘라 쓴다 (질문 원문이 통째로 들어오는 것을 막는다).
_MAX_FIELD = 300
#: 이 필드들은 잘리면 뜻이 깨지므로 더 길게 허용한다 (거절 사유의 설정 키·힌트 등).
_LONG_FIELDS = {"limit_json", "error"}

_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "cfg": None,            # dict — reqmgr 의 ledger 절
    "dir": "",
    "q": None,              # queue.SimpleQueue
    "thread": None,
    "stop": None,           # threading.Event
    "index": {},            # token -> 접힌 레코드 (최근 것만)
    "order": [],            # token 순서 (오래된 것부터)
    "dropped": 0,           # 큐가 가득 차 버린 줄 수 — 조용한 유실을 만들지 않는다
    "emitted": 0,           # 큐에 넣은 줄 수 (flush 가 written 과 비교한다)
    "written": 0,
    "corrupt": 0,           # 읽다가 만난 깨진 줄 수 — 0 이 아니면 원장 자체를 의심해야 한다
    "errors": 0,
    "last_error": "",
    "atexit": False,        # 종료 시 writer 를 비우는 처리를 걸었는가 (한 번만)
}


# ---------------------------------------------------------------- 설정 · 수명
def configure(cfg: Optional[Dict[str, Any]], data_dir: str = "") -> None:
    """server.json 의 `ledger` 절을 반영한다 (RequestManager 가 기동·reload 때 부른다)."""
    cfg = dict(cfg or {})
    with _LOCK:
        _STATE["cfg"] = cfg
        # 환경변수가 **가장 세다**. 두 가지 쓰임이 있다:
        #  ① 운영 — server.json 을 고치지 않고 원장을 다른 디스크로 보낸다.
        #  ② 격리 — 테스트·검증 하네스가 프로젝트의 data/ledger 를 오염시키지 않게 한 줄로 막는다.
        #     (RequestManager 는 임시 설정으로 만들어져도 data_dir 을 모르면 프로젝트 폴더로 떨어진다.)
        # `LLMWIKI_LOGS_DIR_PATH`·`LLMWIKI_DATA_DIR_PATH` 와 같은 장치다.
        env_dir = os.environ.get("LLMWIKI_LEDGER_DIR_PATH", "").strip()
        d = env_dir or str(cfg.get("dir") or "data/ledger")
        if env_dir:
            if not os.path.isabs(d):
                d = os.path.join(ROOT, d)
        elif not os.path.isabs(d):
            base = data_dir or ROOT
            # 'data/…' 로 시작하면 data_dir 아래로 본다 (requests_dir 과 같은 규칙)
            if d.startswith("data/") and data_dir:
                d = os.path.join(data_dir, d[len("data/"):])
            else:
                d = os.path.join(base, d)
        _STATE["dir"] = os.path.normpath(d)
        cap = int(cfg.get("live_rows") or 500)
        if len(_STATE["order"]) > cap:
            _trim_index(cap)
    if enabled():
        _ensure_writer()


def enabled() -> bool:
    """원장을 쓸지. **명시적으로 `configure()` 된 경우에만** 켜진다 (2026-09-23).

    예전에는 설정이 없으면(cfg is None) 켜진 것으로 봤다. 그래서 Pipeline 만 만드는 테스트·도구가
    `progress.cli_monitor` 를 지날 때 **프로젝트의 data/ledger 에 기록**했고, 실사용 기록 사이에
    테스트 픽스처가 섞였다. 원장을 켜는 곳은 둘뿐이다 — 서버(`RequestManager`)와 CLI(`install_cli_publisher`).
    둘 다 configure() 를 부르므로, 그 외에는 조용히 꺼져 있는 것이 맞다.
    """
    cfg = _STATE.get("cfg")
    return bool(cfg is not None and cfg.get("enabled", True))


def _cfg(key: str, default: Any) -> Any:
    cfg = _STATE.get("cfg") or {}
    v = cfg.get(key)
    return default if v is None else v


def health() -> Dict[str, Any]:
    """원장 자체가 성한가 — 파일을 다시 읽지 않는 가벼운 상태(`stats()` 는 전부 읽는다).

    `dropped`(큐 포화로 버린 줄)·`corrupt`(읽을 수 없는 줄)가 0이 아니면 **원장을 근거로 쓸 수 없다.**
    세 창구가 모두 이 값을 보여 준다 — CLI `ledger stats`, Web 요청(전체) 상태 줄, MCP `wiki_requests`.
    `corrupt` 는 마지막으로 파일을 읽었을 때의 값이다.
    """
    return {"written": _STATE["written"], "dropped": _STATE["dropped"], "corrupt": _STATE["corrupt"],
            "errors": _STATE["errors"], "last_error": _STATE["last_error"],
            "alive": bool(_STATE["thread"] and _STATE["thread"].is_alive())}


def ledger_dir() -> str:
    return _STATE.get("dir") or os.path.join(ROOT, "data", "ledger")


def _ensure_writer() -> None:
    with _LOCK:
        if _STATE["thread"] is not None and _STATE["thread"].is_alive():
            return
        _STATE["q"] = queue.SimpleQueue()
        _STATE["stop"] = threading.Event()
        th = threading.Thread(target=_writer_loop, name="ledger-writer", daemon=True)
        _STATE["thread"] = th
        first = not _STATE["atexit"]
        _STATE["atexit"] = True
    th.start()
    if first:
        # writer 는 daemon 이라 프로세스가 끝나면 **버퍼에 들고 있던 줄과 함께 그냥 죽는다.**
        # CLI 처럼 짧게 살다 가는 프로세스에서 이것이 곧 '기록 없이 사라진 요청' 이 된다
        # (2026-09-24 실측: 30명 부하에서 CLI 질의 18건 중 1건이 이렇게 빠졌다).
        # atexit 은 daemon 스레드가 정리되기 **전에** 돌므로 여기서 마저 비울 수 있다.
        # 제한 시간은 넉넉해야 한다 — 프로세스가 어차피 끝나는 중이고, 몇 초 더 기다리는 것이
        # 기록을 잃는 것보다 낫다. 여러 프로세스가 파일 잠금을 다툴 때 3초로는 부족했다(실측).
        atexit.register(lambda: stop(15.0))


def stop(timeout: float = 2.0) -> None:
    """writer 를 멈추고 남은 줄을 마저 쓴다 (테스트·종료용)."""
    ev, th = _STATE.get("stop"), _STATE.get("thread")
    if ev is not None:
        ev.set()
    if th is not None and th.is_alive():
        th.join(timeout)
    with _LOCK:
        _STATE["thread"] = None


def flush(timeout: float = 3.0) -> None:
    """**디스크에 다 쓰일 때까지** 기다린다 (검증 하네스·CLI 가 바로 읽을 수 있게).

    큐가 비었는지만 보면 안 된다 — writer 가 큐에서 꺼내 **버퍼에 들고 있는** 동안에도 큐는 비어 있다.
    그래서 넣은 수(`emitted`)와 쓴 수(`written`)를 비교한다.
    """
    if _STATE.get("q") is None:
        return
    end = time.time() + timeout
    while time.time() < end:
        with _LOCK:
            done = _STATE["written"] + _STATE["dropped"] >= _STATE["emitted"]
        if done:
            return
        time.sleep(0.01)


# ---------------------------------------------------------------- 기록
def _safe(v: Any, limit: int = _MAX_FIELD) -> Any:
    """JSON 으로 나갈 수 있는 값으로 만든다 (짝 없는 서러게이트 포함)."""
    if v is None or isinstance(v, (int, float, bool)):
        return v
    s = str(v)[:limit]
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        s = s.encode("utf-8", "replace").decode("utf-8", "replace")
    return s


def _emit(rec: Dict[str, Any]) -> None:
    """큐에 넣는다. **호출자에게 예외를 올리지 않는다** — 관측이 본체를 죽이면 안 된다."""
    if not enabled():
        return
    try:
        q = _STATE.get("q")
        if q is None:
            _ensure_writer()
            q = _STATE.get("q")
            if q is None:
                return
        with _LOCK:
            _STATE["emitted"] += 1
        if q.qsize() >= int(_cfg("queue_max", 10000)):
            with _LOCK:
                _STATE["dropped"] += 1      # 못 쓴 사실 자체는 센다
            return
        q.put(rec)
        _index(rec)
    except Exception:
        pass


def new_token(prefix: str = "r") -> str:
    """원장 키. **서버가 만든다** — 클라이언트가 준 progress_token 은 중복될 수 있어 키로 쓰지 않는다."""
    return "%s-%s" % (prefix, uuid.uuid4().hex[:12])


def open_(token: str, **fields: Any) -> str:
    """요청 접수. 반환값은 token (없으면 만들어 준다)."""
    token = token or new_token()
    rec = {"ev": "open", "ts": time.time(), "token": token, "pid": os.getpid()}
    for k, v in fields.items():
        if v is not None and v != "":
            rec[k] = _safe(v)
    rec.setdefault("status", "running")
    _emit(rec)
    return token


def update(token: str, **fields: Any) -> None:
    if not token or not fields:
        return
    rec = {"ev": "update", "ts": time.time(), "token": token}
    for k, v in fields.items():
        if v is not None:
            rec[k] = _safe(v)
    _emit(rec)


def close(token: str, status: str = "done", **fields: Any) -> None:
    if not token:
        return
    rec = {"ev": "close", "ts": time.time(), "token": token,
           "status": status if status in STATUSES else "done"}
    for k, v in fields.items():
        if v is not None:
            rec[k] = _safe(v, 1200 if k in _LONG_FIELDS else _MAX_FIELD)
    _emit(rec)


def note_event(kind: str, **fields: Any) -> str:
    """요청이 아닌 **서버 사건**을 원장에 남긴다 — 스냅샷 복원·로그 초기화처럼
    "이 시각 이후 기록이 왜 없나" 에 답해야 하는 일들(§4.1 원인 E·F)."""
    token = new_token("ev")
    open_(token, kind=kind, origin="server", status="running", **fields)
    close(token, "done")
    return token


class span:
    """요청 하나를 감싼다: open → (예외를 상태로 분류) → close 보장.

    HTTP 진입점 4곳(do_GET/do_POST/do_DELETE/_mcp)이 이것으로 감싸므로
    **티켓을 받기 전에 죽는 요청**(차단·점검 모드·413·인증 실패·잘못된 JSON)도 빠짐없이 남는다.
    """

    def __init__(self, token: str = "", record_ok: bool = True, **fields: Any):
        self.token = token or new_token()
        self.fields = fields
        self.t0 = time.time()
        self.extra: Dict[str, Any] = {}
        self.status: Optional[str] = None
        self.http: Optional[int] = None
        # record_ok=False: **끝이 정상이면 아무것도 쓰지 않는다.** 폴링 GET(/api/progress·/api/activity)이
        # 1.5초마다 오는데 그것까지 남기면 원장이 폴링으로 뒤덮인다. 대신 거절·오류·취소는 **반드시** 남긴다
        # ("timeout 으로 버려진 요청이 기록에 없다" 가 이 기능의 출발점이므로 실패 쪽을 놓치면 안 된다).
        self.record_ok = record_ok
        self._opened = False

    def set(self, **fields: Any) -> None:
        """핸들러가 도중에 알게 된 것(사용자·라벨·request_id·HTTP 코드)을 덧붙인다."""
        self.extra.update({k: v for k, v in fields.items() if v is not None})

    def promote(self) -> None:
        """도중에 '이건 남겨야 한다' 고 판단됐을 때 (예: 티켓을 받는 무거운 요청)."""
        if not self._opened:
            self.record_ok = True
            open_(self.token, **self.fields)
            self._opened = True

    def __enter__(self) -> "span":
        if self.record_ok:
            open_(self.token, **self.fields)
            self._opened = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        status = self.status
        http = self.http
        err = ""
        if exc is not None and status is None:
            name = type(exc).__name__
            if name == "Cancelled":
                status, http = "cancelled", 499
            elif name == "Rejected":
                status = "rejected"
                http = getattr(exc, "status", 503)
                self.extra.setdefault("code", getattr(exc, "code", ""))
            else:
                status, http = "error", self.extra.get("http") or 500
            err = "%s: %s" % (name, exc)
        status = status or "done"
        bad = status != "done" or int(self.extra.get("http") or http or 200) >= 400
        if not self._opened:
            if not bad:
                return False            # 조용한 성공 폴링 — 남기지 않는다
            open_(self.token, **self.fields)   # 실패는 반드시 남긴다
            self._opened = True
        close(self.token, status, http=http, ms=round((time.time() - self.t0) * 1000, 1),
              error=err or self.extra.pop("error", ""), **self.extra)
        return False        # 예외를 삼키지 않는다


# ---------------------------------------------------------------- writer 스레드
def _day_path(ts: float) -> str:
    return os.path.join(ledger_dir(), "req-%s.jsonl" % time.strftime("%Y-%m-%d", time.localtime(ts)))


def _writer_loop() -> None:
    q, ev = _STATE["q"], _STATE["stop"]
    flush_s = max(0.02, float(_cfg("flush_ms", 200)) / 1000.0)
    buf: List[Dict[str, Any]] = []
    last = time.time()
    while True:
        try:
            try:
                buf.append(q.get(timeout=flush_s))
            except queue.Empty:
                pass
            # 종료 중이면 **남은 것을 한꺼번에** 꺼내 쓴다. 작은 배치로 쪼개 쓰면 쓰기마다 파일 잠금을
            # 새로 잡아야 해서, CPU 가 바쁠 때 종료 제한 시간 안에 다 비우지 못한다
            # (실측: 전체 테스트와 함께 돌릴 때 1,800줄 중 24줄이 이렇게 남았다).
            if ev.is_set():
                while len(buf) < 5000:
                    try:
                        buf.append(q.get_nowait())
                    except queue.Empty:
                        break
            if buf and (len(buf) >= 200 or time.time() - last >= flush_s or (ev.is_set() and q.empty())):
                _write_batch(buf)
                buf = []
                last = time.time()
            if ev.is_set() and q.empty() and not buf:
                return
        except Exception as e:      # writer 가 죽으면 원장이 조용히 멈춘다 — 살려 두고 센다
            with _LOCK:
                _STATE["errors"] += 1
                _STATE["last_error"] = str(e)[:200]
            buf = []
            time.sleep(0.2)


def _lock_file(fd: int) -> bool:
    """파일 0번 바이트를 **프로세스 간 뮤텍스**로 쓴다. 표준 라이브러리만 쓴다."""
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)      # 최대 10초 재시도 후 예외
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        return True
    except Exception:
        return False


def _unlock_file(fd: int, locked: bool) -> None:
    if not locked:
        return
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass


def _write_batch(rows: List[Dict[str, Any]]) -> None:
    """**여러 프로세스가 같은 파일에 동시에** 쓴다 (서버 + CLI 여러 개 + MCP stdio).

    그래서 평범한 `open(path, "a")` + `f.write(...)` 로는 안 된다. 두 가지가 겹친다:
      ① 텍스트 모드는 8KB 버퍼를 쓰므로 큰 배치가 여러 번의 OS 쓰기로 쪼개진다.
      ② **Windows 의 `O_APPEND` 는 프로세스 간 원자적이지 않다** — CRT 가 "끝으로 seek 한 뒤 write"
         를 하므로, 두 프로세스가 같은 끝 위치를 잡으면 **한쪽이 다른 쪽의 줄을 덮어쓴다.**
    결과는 깨진 줄이고, 깨진 줄은 읽을 때 버려지므로 **요청이 기록 없이 사라진다** —
    이 기능이 막으려는 바로 그것이다. (2026-09-24 실측: 30명 동시 부하에서 CLI 질의 18건 중
    3~7건의 접수 기록이 이렇게 사라졌고, 파일에는 `ing"}` 같은 꼬리 조각만 남았다.)

    그래서 **파일 잠금을 잡고 → 끝으로 이동 → 한 번에 쓰고 → 푼다.** 프로세스마다 파일을 나누는 방법도
    있지만, CLI 는 호출마다 새 프로세스라 하루에 파일이 수천 개가 되어 택하지 않았다.
    """
    d = ledger_dir()
    os.makedirs(d, exist_ok=True)
    by_day: Dict[str, bytearray] = {}
    for r in rows:
        line = json.dumps(r, ensure_ascii=False, default=str) + "\n"
        by_day.setdefault(_day_path(float(r.get("ts") or time.time())), bytearray()).extend(
            line.encode("utf-8", "replace"))
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0)
    for path, payload in by_day.items():
        fd = os.open(path, flags, 0o644)
        locked = False
        try:
            locked = _lock_file(fd)
            if not locked:              # 잠그지 못해도 기록은 남긴다 (잃는 것보다 낫다) — 대신 센다
                with _LOCK:
                    _STATE["errors"] += 1
                    _STATE["last_error"] = "ledger file lock failed: %s" % os.path.basename(path)
            os.lseek(fd, 0, os.SEEK_END)
            os.write(fd, bytes(payload))
        finally:
            _unlock_file(fd, locked)
            os.close(fd)
    with _LOCK:
        _STATE["written"] += len(rows)


# ---------------------------------------------------------------- 메모리 색인 (최근 목록을 빠르게)
def _index(rec: Dict[str, Any]) -> None:
    tok = rec.get("token")
    if not tok:
        return
    with _LOCK:
        cur = _STATE["index"].get(tok)
        if cur is None:
            cur = {"token": tok, "opened": rec.get("ts"), "status": "unknown"}
            _STATE["index"][tok] = cur
            _STATE["order"].append(tok)
        _fold_into(cur, rec)
        _trim_index(int(_cfg("live_rows", 500)))


def _trim_index(cap: int) -> None:
    order, idx = _STATE["order"], _STATE["index"]
    while len(order) > max(10, cap):
        idx.pop(order.pop(0), None)


def _fold_into(cur: Dict[str, Any], rec: Dict[str, Any]) -> None:
    """open/update/close 한 줄을 접힌 레코드에 합친다."""
    ev = rec.get("ev")
    for k, v in rec.items():
        if k in ("ev", "token"):
            continue
        if k == "ts":
            if ev == "open":
                cur["opened"] = v
            elif ev == "close":
                cur["closed"] = v
            continue
        if k == "status" and ev != "close" and cur.get("closed"):
            continue                    # 끝난 뒤에 온 update 가 종료 상태를 덮지 않게
        cur[k] = v
    if ev == "close":
        cur.setdefault("status", "done")
    elif "status" not in cur:
        cur["status"] = "running"


def _finalize(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """접힌 레코드에 파생 값(경과·미완료 판정)을 채운다."""
    now = time.time()
    out = []
    for r in rows:
        r = dict(r)
        if not r.get("closed"):
            # close 가 없다 = 아직 실행 중이거나, 서버가 죽어 못 남긴 것.
            # 오래된 것은 '중단(unknown)' 으로 보여 준다 — 사라지게 두지 않는다.
            age = now - float(r.get("opened") or now)
            r["status"] = "running" if age < float(_cfg("running_grace_s", 900)) else "unknown"
            r["elapsed_s"] = round(age, 1)
        else:
            r["elapsed_s"] = round(float(r["closed"]) - float(r.get("opened") or r["closed"]), 3)
        out.append(r)
    return out


# ---------------------------------------------------------------- 읽기
def _iter_files(newest_first: bool = True) -> List[str]:
    d = ledger_dir()
    if not os.path.isdir(d):
        return []
    fs = [os.path.join(d, f) for f in os.listdir(d) if f.startswith("req-") and f.endswith(".jsonl")]
    return sorted(fs, reverse=newest_first)


def _load(since: float = 0.0, until: float = 0.0, max_rows: int = 200000) -> Dict[str, Dict[str, Any]]:
    """파일에서 접힌 레코드를 만든다. 깨진 줄은 건너뛰되 **몇 줄이었는지 센다**.

    조용히 버리면 안 된다 — 깨진 줄은 곧 사라진 요청이고, 그 수가 0이 아니면 원장 자체를 의심해야 한다
    (`stats()` 의 `writer.corrupt` 로 보인다).
    """
    folded: Dict[str, Dict[str, Any]] = {}
    n, corrupt = 0, 0
    for path in _iter_files(newest_first=True):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        corrupt += 1
                        continue
                    ts = float(rec.get("ts") or 0)
                    if since and ts < since:
                        continue
                    if until and ts > until:
                        continue
                    tok = rec.get("token")
                    if not tok:
                        continue
                    cur = folded.get(tok)
                    if cur is None:
                        cur = folded[tok] = {"token": tok, "opened": ts, "status": "unknown"}
                    _fold_into(cur, rec)
                    n += 1
                    if n >= max_rows:
                        with _LOCK:
                            _STATE["corrupt"] = corrupt
                        return folded
        except OSError:
            continue
    with _LOCK:
        _STATE["corrupt"] = corrupt
    return folded


def read(status: Optional[Iterable[str]] = None, kind: str = "", origin: str = "", user: str = "",
         q: str = "", min_ms: float = 0.0, http: Optional[int] = None,
         since: float = 0.0, until: float = 0.0, limit: int = 100, offset: int = 0,
         token: str = "", include_sub: bool = False) -> Tuple[List[Dict[str, Any]], int]:
    """필터에 맞는 요청 목록 (최신순) 과 전체 건수.

    메모리 색인만으로 답할 수 있으면 파일을 읽지 않는다 (폴링이 잦은 화면을 위해).

    `include_sub=False`(기본)는 **부수 줄을 접는다** — 잡을 띄우기만 한 POST 처럼 한 동작이 두 줄로
    보이는 것을 막는다. 기록에서 지우는 것이 아니라 목록에서만 접는 것이라, 필요하면 True 로 전부 본다.
    """
    want = set(status or [])
    use_mem = (not since and not until and (offset + limit) <= len(_STATE["order"]))
    if use_mem:
        with _LOCK:
            rows = [dict(_STATE["index"][t]) for t in reversed(_STATE["order"]) if t in _STATE["index"]]
    else:
        rows = list(_load(since, until).values())
        rows.sort(key=lambda r: float(r.get("opened") or 0), reverse=True)
    rows = _finalize(rows)

    def ok(r: Dict[str, Any]) -> bool:
        if token and r.get("token") != token:
            return False
        # 부수 줄(잡을 띄운 POST)은 기본으로 접는다 — 단, **실패했다면 보여 준다**
        # (POST 자체가 거절·오류인 것은 그 자체로 봐야 할 사건이다).
        if not include_sub and r.get("sub") and r.get("status") == "done":
            return False
        if want and r.get("status") not in want:
            return False
        if kind and r.get("kind") != kind:
            return False
        if origin and r.get("origin") != origin:
            return False
        if user and r.get("user") != user:
            return False
        if http is not None and int(r.get("http") or 0) != int(http):
            return False
        if min_ms and float(r.get("ms") or (r.get("elapsed_s") or 0) * 1000) < float(min_ms):
            return False
        if q:
            hay = " ".join(str(r.get(k) or "") for k in ("label", "path", "user", "code", "error", "token"))
            if q.lower() not in hay.lower():
                return False
        return True

    hit = [r for r in rows if ok(r)]
    return hit[offset:offset + max(1, int(limit))], len(hit)


def one(token: str) -> Optional[Dict[str, Any]]:
    """한 건 + 사건 타임라인 (상세 패널용)."""
    if not token:
        return None
    with _LOCK:
        cur = dict(_STATE["index"].get(token) or {}) or None
    events: List[Dict[str, Any]] = []
    for path in _iter_files(newest_first=True):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if token not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("token") == token:
                        events.append(rec)
        except OSError:
            continue
        if events:
            break
    if events:
        folded = {"token": token, "opened": float(events[0].get("ts") or 0), "status": "unknown"}
        for rec in events:
            _fold_into(folded, rec)
        cur = folded
    if not cur:
        return None
    out = _finalize([cur])[0]
    out["events"] = sorted(events, key=lambda r: float(r.get("ts") or 0))
    return out


def concurrent_with(token: str, limit: int = 30) -> List[Dict[str, Any]]:
    """그 요청이 실행되던 **동안 서버에 같이 있던** 요청들 — 경합 디버깅의 핵심."""
    me = one(token)
    if not me:
        return []
    a0 = float(me.get("opened") or 0)
    a1 = float(me.get("closed") or time.time())
    rows, _n = read(limit=5000, since=a0 - 3600, until=a1 + 1)
    out = []
    for r in rows:
        if r.get("token") == token:
            continue
        b0 = float(r.get("opened") or 0)
        b1 = float(r.get("closed") or time.time())
        overlap = min(a1, b1) - max(a0, b0)
        if overlap > 0:
            out.append(dict(r, overlap_s=round(overlap, 2)))
    out.sort(key=lambda r: -r["overlap_s"])
    return out[:limit]


def stats(days: float = 1.0) -> Dict[str, Any]:
    since = time.time() - max(0.01, days) * 86400
    rows = _finalize(list(_load(since).values()))
    by_status: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    by_origin: Dict[str, int] = {}
    slow = 0
    for r in rows:
        by_status[r.get("status") or "?"] = by_status.get(r.get("status") or "?", 0) + 1
        by_kind[r.get("kind") or "?"] = by_kind.get(r.get("kind") or "?", 0) + 1
        by_origin[r.get("origin") or "?"] = by_origin.get(r.get("origin") or "?", 0) + 1
        if float(r.get("ms") or 0) >= 30000:
            slow += 1
    d = ledger_dir()
    files = _iter_files()
    size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
    return {"days": days, "total": len(rows), "by_status": by_status, "by_kind": by_kind,
            "by_origin": by_origin, "slow": slow, "dir": d, "files": len(files),
            "bytes": size, "mb": round(size / 1048576.0, 2),
            "writer": dict(health(), queued=_STATE["q"].qsize() if _STATE.get("q") else 0),
            "enabled": enabled()}


#: 막대에 쌓을 순서 (아래 → 위). 한 구간에 여러 상태가 섞이는 것이 정상이므로 **누적 막대**로 그린다 —
#: 예전에는 구간마다 색 하나만 골라(bad > slow > 나머지) 칠해서, 같은 1분 안에 완료 50건과 거절 1건이 있으면
#: 전부 빨강으로 보였다 (2026-09-23 사용자 지적).
HIST_KEYS = ("done", "running", "slow", "cancelled", "timeout", "rejected", "error", "unknown")


def histogram(since: float, until: float, buckets: int = 60) -> List[Dict[str, Any]]:
    """시간 밀도 막대. 구간마다 **상태별 건수를 모두** 돌려준다 (화면이 누적 막대로 그린다).

    `slow` 는 상태가 아니라 '완료됐지만 느림' 이라 done 에서 떼어 따로 센다 (합은 여전히 n 과 같다).
    """
    until = until or time.time()
    since = since or (until - 3600)
    width = max(1.0, (until - since) / max(1, buckets))
    out = [dict({"t": since + i * width, "n": 0}, **{k: 0 for k in HIST_KEYS}) for i in range(buckets)]
    slow_ms = 30000.0
    for r in _finalize(list(_load(since, until).values())):
        i = int((float(r.get("opened") or since) - since) / width)
        if not (0 <= i < buckets):
            continue
        st = r.get("status") or "unknown"
        if st == "done" and float(r.get("ms") or 0) >= slow_ms:
            st = "slow"
        elif st == "queued":
            st = "running"          # 대기도 '아직 안 끝난 것' 으로 같은 칸에 (칸이 8개를 넘지 않게)
        if st not in HIST_KEYS:
            st = "unknown"
        out[i][st] += 1
        out[i]["n"] += 1
    return out


def prune() -> Dict[str, Any]:
    """보존 기간(keep_days) + 총량(max_mb) 으로 오래된 파일부터 지운다."""
    keep_days = float(_cfg("keep_days", 30) or 0)
    max_mb = float(_cfg("max_mb", 512) or 0)
    removed: List[str] = []
    files = _iter_files(newest_first=False)     # 오래된 것부터
    now = time.time()
    if keep_days > 0:
        for f in list(files):
            try:
                if now - os.path.getmtime(f) > keep_days * 86400:
                    os.remove(f)
                    removed.append(os.path.basename(f))
                    files.remove(f)
            except OSError:
                pass
    if max_mb > 0:
        total = sum(os.path.getsize(f) for f in files if os.path.exists(f))
        for f in list(files):
            if total <= max_mb * 1048576:
                break
            try:
                total -= os.path.getsize(f)
                os.remove(f)
                removed.append(os.path.basename(f))
                files.remove(f)
            except OSError:
                pass
    return {"removed": removed, "kept": len(files), "dir": ledger_dir(),
            "keep_days": keep_days, "max_mb": max_mb}


def should_record_get(path: str, heavy: bool) -> bool:
    """GET 을 남길지 — 폴링(1.5초마다 오는 /api/progress·/api/activity)이 원장을 뒤덮지 않게.

    none: POST/DELETE/MCP + 모든 거절·오류만 · heavy(기본): + 무거운 조회 · all: 전부
    """
    mode = str(_cfg("include_get", "heavy") or "heavy").lower()
    if mode == "all":
        return True
    if mode == "none":
        return False
    return bool(heavy)
