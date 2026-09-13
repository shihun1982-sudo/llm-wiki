# -*- coding: utf-8 -*-
"""빌드 락 — 서버 워처·CLI·OS 스케줄러가 같은 색인을 동시에 빌드하지 않도록 파일 락을 건다.

data/build.lock 에 {pid, host, ts, cmd} 를 기록한다. 소유 프로세스가 죽었거나(stale) 오래됐으면 회수한다.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from typing import Any, Dict, Optional


class BuildLockedError(RuntimeError):
    def __init__(self, holder: Dict[str, Any]):
        RuntimeError.__init__(self, "another build is running: pid=%s host=%s since=%s" % (
            holder.get("pid"), holder.get("host"), time.strftime("%H:%M:%S", time.localtime(holder.get("ts", 0)))))
        self.holder = holder


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            import ctypes
            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            k32.CloseHandle(h)
            return bool(ok) and code.value == 259   # STILL_ACTIVE
        except Exception:
            return True   # 판단 불가 → 살아있다고 가정 (안전)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


class BuildLock:
    def __init__(self, path: str, timeout: float = 0.0, stale_after_s: float = 6 * 3600, cmd: str = ""):
        self.path = path
        self.timeout = float(timeout or 0)
        self.stale_after_s = stale_after_s
        self.cmd = cmd or " ".join(sys.argv[:3])
        self.held = False

    def holder(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _is_stale(self, h: Optional[Dict[str, Any]]) -> bool:
        if not h:
            return True
        if h.get("host") == socket.gethostname() and not _pid_alive(int(h.get("pid") or 0)):
            return True
        return (time.time() - float(h.get("ts") or 0)) > self.stale_after_s

    def acquire(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(), "host": socket.gethostname(), "ts": time.time(), "cmd": self.cmd}, f)
                self.held = True
                return
            except FileExistsError:
                h = self.holder()
                if self._is_stale(h):
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    continue
                if time.time() >= deadline:
                    raise BuildLockedError(h or {})
                time.sleep(0.5)

    def release(self) -> None:
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.held = False

    def __enter__(self) -> "BuildLock":
        self.acquire()
        return self

    def __exit__(self, *a: Any) -> None:
        self.release()
