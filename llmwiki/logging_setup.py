# -*- coding: utf-8 -*-
"""파일 로깅 (logs/ 폴더, JSON Lines, 로테이션) + 요청 단위 run_id 컨텍스트.

- 모든 로그 레코드는 {ts, level, logger, run_id, kind, msg, data} 한 줄 JSON.
- run_id 는 Profiler 가 요청(query/build/eval/…)마다 발급하고 requests 테이블에 함께 저장되므로
  `requests show <id>` ↔ `logs grep --request <id>` 로 프로파일과 로그를 서로 추적할 수 있다.
- 파일: llmwiki.log(전체) · error.log(WARNING↑) · build.log(kind=build) · query.log(kind=query|search|eval)
- 정상 동작도 남긴다: 요청 시작/종료, 단계 종료(이름·ms·요약), 프로바이더 호출, 빌드 진행.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

LOGGER_NAME = "llmwiki"
_local = threading.local()
_STATE: Dict[str, Any] = {"dir": None, "handlers": [], "level": None}
_FILES = ("llmwiki.log", "error.log", "build.log", "query.log")


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


# ---------------------------------------------------------------- setup
def setup_logging(log_dir: str, level: str = "INFO", max_mb: int = 10, backups: int = 10, console: bool = False) -> str:
    """logs/ 핸들러 구성 (같은 폴더/레벨이면 재구성하지 않음)."""
    lvl = getattr(logging, str(level).upper(), logging.INFO)
    if _STATE["dir"] == log_dir and _STATE["level"] == lvl:
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
    _STATE["dir"], _STATE["level"] = log_dir, lvl
    return log_dir


def setup_from_settings(s: Any) -> str:
    from .config import path_for
    d = getattr(s, "log_dir", None) or path_for("logs_dir")
    return setup_logging(d, getattr(s, "log_level", "INFO"), getattr(s, "log_max_mb", 10), getattr(s, "log_backups", 10),
                         getattr(s, "log_console", False))


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
