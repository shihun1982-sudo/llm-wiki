# -*- coding: utf-8 -*-
"""파일 로깅 (logs/ 폴더, JSON Lines, 로테이션) + 요청 단위 run_id 컨텍스트.

- 모든 로그 레코드는 {ts, level, logger, run_id, kind, msg, data} 한 줄 JSON.
- run_id 는 Profiler 가 요청(query/build/eval/…)마다 발급하고 requests 테이블에 함께 저장되므로
  `requests show <id>` ↔ `logs grep --request <id>` 로 프로파일과 로그를 서로 추적할 수 있다.
- 파일: llmwiki.log(전체) · error.log(WARNING↑) · build.log(kind=build) · query.log(kind=query|search|eval)
- 정상 동작도 남긴다: 요청 시작/종료, 단계 종료(이름·ms·요약), 프로바이더 호출, 빌드 진행.
- 총량 제한(2026-09-18, 요청 11): logs/ 폴더 합계(로그+백업+audit.jsonl+analysis/)가 `log_total_max_mb` 를 넘으면
  `log_limit_action` 에 따라 warn(error.log 경고) | prune(오래된 백업·리포트 삭제) | stop(error.log 만 남기고 기록 중단).
  점검은 레코드가 남을 때 `log_check_interval_s` 마다 한 번만 폴더를 훑는다 (_QuotaFilter). 상태는 quota_status() / `logs status`.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

LOGGER_NAME = "llmwiki"
_local = threading.local()
_STATE: Dict[str, Any] = {"dir": None, "handlers": [], "level": None, "params": None}
_FILES = ("llmwiki.log", "error.log", "build.log", "query.log")
QUOTA_ACTIONS = ("warn", "prune", "stop")
_LOW_WATER = 0.8   # prune 목표 / stop 해제 기준 = 상한의 80%
# 총량 제한 상태 (configure_quota 로 설정, _check_locked 가 갱신). pruned = 최근 지운 파일 이름(최대 50).
_QUOTA: Dict[str, Any] = {"limit_mb": 500, "action": "warn", "interval_s": 60, "last_check": 0.0, "total_bytes": 0, "files": 0,
                          "over": False, "stopped": False, "pruned": [], "last_warn": 0.0, "last_event": ""}
_QUOTA_LOCK = threading.Lock()   # 비재진입: 점검 중에 낸 경고 로그가 다시 점검을 타지 않게 한다
_BACKUP_RE = re.compile(r".+\.log\.\d+$")        # llmwiki.log.3 같은 로테이션 백업
_AUDIT_BACKUP_RE = re.compile(r"audit\.jsonl\.\d+$")


# ---------------------------------------------------------------- run 컨텍스트
def push_run(run_id: str, kind: str) -> None:
    stack: List[Tuple[str, str]] = getattr(_local, "stack", None) or []
    stack.append((run_id, kind))
    _local.stack = stack


def pop_run(run_id: Optional[str] = None) -> None:
    stack: List[Tuple[str, str]] = getattr(_local, "stack", None) or []
    if not stack:
        return
    if run_id is None or stack[-1][0] == run_id:
        stack.pop()
    else:
        for i in range(len(stack) - 1, -1, -1):
            if stack[i][0] == run_id:
                del stack[i]
                break
    _local.stack = stack


def current_run() -> Tuple[Optional[str], Optional[str]]:
    stack = getattr(_local, "stack", None) or []
    return stack[-1] if stack else (None, None)


class _CtxFilter(logging.Filter):
    def filter(self, rec: logging.LogRecord) -> bool:
        rid, kind = current_run()
        rec.run_id = rid or ""
        rec.kind = kind or ""
        return True


class _KindFilter(logging.Filter):
    def __init__(self, kinds: Tuple[str, ...]):
        logging.Filter.__init__(self)
        self.kinds = kinds

    def filter(self, rec: logging.LogRecord) -> bool:
        return getattr(rec, "kind", "") in self.kinds


class JsonFormatter(logging.Formatter):
    def format(self, rec: logging.LogRecord) -> str:
        d: Dict[str, Any] = {"ts": round(rec.created, 3), "t": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec.created)),
                             "level": rec.levelname, "logger": rec.name, "run_id": getattr(rec, "run_id", ""),
                             "kind": getattr(rec, "kind", ""), "msg": rec.getMessage()}
        data = getattr(rec, "data", None)
        if data:
            d["data"] = data
        if rec.exc_info:
            d["exc"] = self.formatException(rec.exc_info)[-2000:]
        try:
            return json.dumps(d, ensure_ascii=False, default=str)
        except Exception:
            d["data"] = str(data)[:2000]
            return json.dumps(d, ensure_ascii=False, default=str)


# ---------------------------------------------------------------- 총량 제한 (log_total_max_mb / log_limit_action)
class _QuotaFilter(logging.Filter):
    """파일 핸들러마다 붙는다. 레코드가 올 때 (싸게) 점검 시점인지 보고, stop 상태면 error.log 이외 핸들러의 레코드를 버린다.
    로거가 아니라 핸들러에 붙이는 이유: 로거 필터는 자식 로거(llmwiki.pipeline …)의 레코드에는 적용되지 않기 때문."""

    def __init__(self, stoppable: bool):
        logging.Filter.__init__(self)
        self.stoppable = stoppable

    def filter(self, rec: logging.LogRecord) -> bool:
        _maybe_check(rec.created)
        if self.stoppable and _QUOTA["stopped"]:
            return False
        return True


def configure_quota(total_max_mb: int = 500, action: str = "warn", interval_s: int = 60) -> None:
    """총량 제한 설정을 바꾼다 (핸들러 재구성 없이). 다음 레코드에서 바로 다시 점검한다."""
    act = str(action or "warn").lower()
    if act not in QUOTA_ACTIONS:
        act = "warn"
    try:
        limit = int(total_max_mb or 0)
    except (TypeError, ValueError):
        limit = 500
    try:
        interval = max(0.0, float(interval_s or 0))
    except (TypeError, ValueError):
        interval = 60.0
    _QUOTA.update({"limit_mb": limit, "action": act, "interval_s": interval, "last_check": 0.0})
    if act != "stop" or limit <= 0:
        _QUOTA["stopped"] = False
    if limit <= 0:
        _QUOTA["over"] = False


def _maybe_check(now: float) -> None:
    q = _QUOTA
    if not _STATE["dir"] or int(q["limit_mb"] or 0) <= 0:
        return
    if now - q["last_check"] < q["interval_s"]:
        return
    if not _QUOTA_LOCK.acquire(blocking=False):   # 다른 스레드가 점검 중이거나, 점검 안에서 낸 경고 로그(재진입) → 건너뜀
        return
    try:
        _check_locked(now, _STATE["dir"], act=True)
    finally:
        _QUOTA_LOCK.release()


def _scan(d: str) -> List[Dict[str, Any]]:
    """logs/ 아래 모든 파일 (하위 폴더 포함): {rel, path, bytes, mtime}"""
    out: List[Dict[str, Any]] = []
    if not d or not os.path.isdir(d):
        return out
    for base, _dirs, fns in os.walk(d):
        for fn in fns:
            p = os.path.join(base, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({"rel": os.path.relpath(p, d).replace("\\", "/"), "path": p, "bytes": st.st_size, "mtime": st.st_mtime})
    return out


def _prune_candidates(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """지울 순서: ① 로테이션 백업(*.log.N, 오래된 것부터) ② analysis/ 리포트(오래된 것부터) ③ audit.jsonl.N (오래된 것부터).
    현재 쓰는 중인 파일(llmwiki.log·error.log·build.log·query.log·audit.jsonl·기타)은 건드리지 않는다."""
    backups = sorted((e for e in entries if "/" not in e["rel"] and _BACKUP_RE.match(e["rel"])), key=lambda e: (e["mtime"], e["rel"]))
    reports = sorted((e for e in entries if e["rel"].startswith("analysis/")), key=lambda e: (e["mtime"], e["rel"]))
    audits = sorted((e for e in entries if "/" not in e["rel"] and _AUDIT_BACKUP_RE.match(e["rel"])), key=lambda e: (e["mtime"], e["rel"]))
    return backups + reports + audits


def _prune(entries: List[Dict[str, Any]], total: int, low: int) -> List[Tuple[str, int]]:
    """total 이 low 이하가 될 때까지 후보를 지운다. 핸들러 락을 잡고 지워 RotatingFileHandler 의 rollover(백업 이름 바꾸기)와 겹치지 않게 한다."""
    removed: List[Tuple[str, int]] = []
    hs = list(_STATE["handlers"])
    for h in hs:
        h.acquire()
    try:
        for e in _prune_candidates(entries):
            if total <= low:
                break
            try:
                os.remove(e["path"])
            except OSError:
                continue
            total -= e["bytes"]
            removed.append((e["rel"], e["bytes"]))
    finally:
        for h in reversed(hs):
            h.release()
    return removed


def _emit(level: int, msg: str, **data: Any) -> None:
    try:
        logging.getLogger(LOGGER_NAME + ".logs").log(level, msg, extra={"data": data})
    except Exception:
        pass


def _check_locked(now: float, d: str, act: bool) -> None:
    """폴더를 훑어 총량을 재고 action 을 적용한다 (_QUOTA_LOCK 을 잡은 채로 호출). act=False 면 재기만 한다(핸들러 없는 CLI)."""
    q = _QUOTA
    q["last_check"] = now
    entries = _scan(d)
    total = sum(e["bytes"] for e in entries)
    limit = int(q["limit_mb"] or 0) * 1024 * 1024
    low = int(limit * _LOW_WATER)
    action = q["action"]
    if act and limit > 0 and total > limit and action == "prune":
        removed = _prune(entries, total, low)
        if removed:
            total -= sum(b for _, b in removed)
            q["pruned"] = (q["pruned"] + [n for n, _ in removed])[-50:]
            q["last_event"] = "%s prune %d files" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), len(removed))
            _emit(logging.WARNING, "log quota prune", removed=[n for n, _ in removed], freed_mb=round(sum(b for _, b in removed) / 1048576.0, 2),
                  total_mb=round(total / 1048576.0, 2), limit_mb=int(q["limit_mb"]))
    over = bool(limit > 0 and total > limit)
    if act and limit > 0 and action == "stop":
        if over and not q["stopped"]:
            q["stopped"] = True
            q["last_event"] = "%s stop" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        elif q["stopped"] and total <= low:
            q["stopped"] = False
            q["last_event"] = "%s resumed" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            _emit(logging.WARNING, "log quota resumed", total_mb=round(total / 1048576.0, 2), limit_mb=int(q["limit_mb"]))
    elif action != "stop":
        q["stopped"] = False
    q["total_bytes"], q["files"], q["over"] = total, len(entries), over
    if act and over and (now - q["last_warn"]) >= q["interval_s"]:   # 경고는 점검 주기당 한 줄
        q["last_warn"] = now
        _emit(logging.WARNING, "log quota exceeded", total_mb=round(total / 1048576.0, 2), limit_mb=int(q["limit_mb"]), action=action,
              stopped=q["stopped"], hint="logs status · log_total_max_mb 상향 또는 log_limit_action=prune")


def check_quota(force: bool = False, dir_hint: Optional[str] = None) -> Dict[str, Any]:
    """총량 점검을 지금 실행하고 상태를 돌려준다. force=False 면 log_check_interval_s 가 지났을 때만 다시 잰다.
    로깅이 구성되지 않은 프로세스(dir 없음)는 dir_hint 폴더를 재기만 하고 action 은 적용하지 않는다."""
    d = _STATE["dir"] or dir_hint
    now = time.time()
    if d and int(_QUOTA["limit_mb"] or 0) > 0 and (force or now - _QUOTA["last_check"] >= _QUOTA["interval_s"]):
        with _QUOTA_LOCK:
            _check_locked(now, d, act=bool(_STATE["dir"]))
    elif d and int(_QUOTA["limit_mb"] or 0) <= 0 and (force or not _QUOTA["last_check"]):
        entries = _scan(d)
        _QUOTA.update({"total_bytes": sum(e["bytes"] for e in entries), "files": len(entries), "last_check": now, "over": False, "stopped": False})
    return quota_status(d)


def quota_status(dir_hint: Optional[str] = None) -> Dict[str, Any]:
    """마지막 점검 결과 스냅샷 (폴더를 다시 훑지 않는다 — 다시 재려면 check_quota)."""
    q = _QUOTA
    limit = int(q["limit_mb"] or 0)
    total_mb = q["total_bytes"] / 1048576.0
    return {"dir": _STATE["dir"] or dir_hint, "total_mb": round(total_mb, 2), "limit_mb": limit,
            "pct": (round(100.0 * total_mb / limit, 1) if limit > 0 else None), "low_water_mb": (round(limit * _LOW_WATER, 1) if limit > 0 else None),
            "action": q["action"], "interval_s": q["interval_s"], "over": bool(q["over"]), "stopped": bool(q["stopped"]),
            "files": q["files"], "last_check": q["last_check"],
            "last_check_t": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(q["last_check"])) if q["last_check"] else None,
            "last_event": q["last_event"] or None, "pruned": list(q["pruned"][-20:]), "enabled": limit > 0}


def format_quota(q: Dict[str, Any]) -> str:
    """`logs status` 텍스트."""
    if not q.get("enabled"):
        return "log quota: 제한 없음 (log_total_max_mb=0)  총량 %.1f MB, 파일 %s개, 폴더 %s" % (q.get("total_mb") or 0, q.get("files"), q.get("dir"))
    state = "STOPPED(error.log 만 기록 중)" if q.get("stopped") else ("OVER(상한 초과)" if q.get("over") else "ok")
    lines = ["log quota: %s" % state,
             "  총량   %.1f / %d MB (%s%%)  파일 %s개  폴더 %s" % (q["total_mb"], q["limit_mb"], q.get("pct"), q.get("files"), q.get("dir")),
             "  동작   log_limit_action=%s  점검 주기 %ss  prune/재개 기준 %s MB" % (q["action"], q["interval_s"], q.get("low_water_mb")),
             "  점검   %s%s" % (q.get("last_check_t") or "-", ("  마지막 사건: " + q["last_event"]) if q.get("last_event") else "")]
    if q.get("pruned"):
        lines.append("  지움   " + ", ".join(q["pruned"]))
    if q.get("over") or q.get("stopped"):
        lines.append("  조치   config.json log_total_max_mb 를 올리거나 log_limit_action=prune, 또는 logs/ 의 오래된 *.log.N · analysis/ 를 직접 삭제")
    return "\n".join(lines)


# ---------------------------------------------------------------- setup
def setup_logging(log_dir: str, level: str = "INFO", max_mb: int = 10, backups: int = 10, console: bool = False,
                  total_max_mb: int = 500, limit_action: str = "warn", check_interval_s: int = 60) -> str:
    """logs/ 핸들러 구성. 폴더·레벨·max_mb·backups·console 이 모두 같으면 재구성하지 않는다 (총량 설정은 항상 반영)."""
    lvl = getattr(logging, str(level).upper(), logging.INFO)
    configure_quota(total_max_mb, limit_action, check_interval_s)
    params = (log_dir, lvl, int(max_mb), int(backups), bool(console))
    if _STATE["dir"] == log_dir and _STATE["params"] == params:
        return log_dir
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger(LOGGER_NAME)
    for h in _STATE["handlers"]:
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    _STATE["handlers"] = []
    root.setLevel(min(lvl, logging.INFO))
    root.propagate = False
    fmt = JsonFormatter()
    ctx = _CtxFilter()

    def _fh(name: str, hlevel: int, kinds: Optional[Tuple[str, ...]] = None) -> logging.Handler:
        h = logging.handlers.RotatingFileHandler(os.path.join(log_dir, name), maxBytes=int(max_mb) * 1024 * 1024,
                                                 backupCount=int(backups), encoding="utf-8")
        h.setLevel(hlevel)
        h.setFormatter(fmt)
        h.addFilter(ctx)
        if kinds:
            h.addFilter(_KindFilter(kinds))
        h.addFilter(_QuotaFilter(stoppable=(name != "error.log")))   # stop 상태에서도 error.log 는 계속 남긴다
        root.addHandler(h)
        _STATE["handlers"].append(h)
        return h

    _fh("llmwiki.log", lvl)
    _fh("error.log", logging.WARNING)
    _fh("build.log", lvl, ("build", "watch"))
    _fh("query.log", lvl, ("query", "search", "eval", "trial"))
    if console:
        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)
        ch.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        ch.addFilter(ctx)
        root.addHandler(ch)
        _STATE["handlers"].append(ch)
    _STATE["dir"], _STATE["level"], _STATE["params"] = log_dir, lvl, params
    _QUOTA["last_check"] = 0.0
    return log_dir


def shutdown_logging() -> None:
    """핸들러를 닫고 상태를 초기화한다 (테스트·재구성용)."""
    root = logging.getLogger(LOGGER_NAME)
    for h in _STATE["handlers"]:
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    _STATE.update({"dir": None, "handlers": [], "level": None, "params": None})
    _QUOTA.update({"last_check": 0.0, "total_bytes": 0, "files": 0, "over": False, "stopped": False, "pruned": [], "last_warn": 0.0, "last_event": ""})


def setup_from_settings(s: Any) -> str:
    from .config import path_for
    d = path_for("logs_dir")
    try:
        from . import auth as _auth
        _auth.configure_audit(getattr(s, "audit_max_mb", 20), getattr(s, "audit_backups", 5))
    except Exception:
        pass
    return setup_logging(d, getattr(s, "log_level", "INFO"), getattr(s, "log_max_mb", 10), getattr(s, "log_backups", 10),
                         getattr(s, "log_console", False), getattr(s, "log_total_max_mb", 500), getattr(s, "log_limit_action", "warn"),
                         getattr(s, "log_check_interval_s", 60))


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(LOGGER_NAME + ("." + name if name else ""))


def log(level: str, msg: str, logger: str = "", **data: Any) -> None:
    """구조화 로그 한 줄. level: debug|info|warning|error"""
    lg = get_logger(logger)
    lg.log(getattr(logging, level.upper(), logging.INFO), msg, extra={"data": data} if data else {})


def log_dir() -> Optional[str]:
    return _STATE["dir"]


# ---------------------------------------------------------------- 조회 (CLI logs)
def _iter_lines(path: str):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                yield {"msg": line, "level": "RAW"}


def tail(path: str, n: int = 50) -> List[Dict[str, Any]]:
    rows = list(_iter_lines(path))
    return rows[-n:]


def grep(path: str, run_id: Optional[str] = None, text: Optional[str] = None, level: Optional[str] = None,
         since_s: Optional[float] = None, limit: int = 500) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    now = time.time()
    order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
    min_lvl = order.get((level or "").upper(), 0)
    for r in _iter_lines(path):
        if run_id and r.get("run_id") != run_id:
            continue
        if since_s is not None and (now - float(r.get("ts") or 0)) > since_s:
            continue
        if min_lvl and order.get(r.get("level", ""), 0) < min_lvl:
            continue
        if text and text.lower() not in json.dumps(r, ensure_ascii=False).lower():
            continue
        out.append(r)
    return out[-limit:]


def files(dir_path: str) -> List[Dict[str, Any]]:
    out = []
    if not os.path.isdir(dir_path):
        return out
    for fn in sorted(os.listdir(dir_path)):
        p = os.path.join(dir_path, fn)
        if os.path.isfile(p):
            out.append({"file": fn, "bytes": os.path.getsize(p), "mtime": os.path.getmtime(p)})
    return out
