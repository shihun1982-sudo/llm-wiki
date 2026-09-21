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
    def __init__(self, path: str, timeout: float = 0.0, stale_after_s: float = 48 * 3600, cmd: str = ""):
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
        """락을 잡는다. 이미 다른 빌드가 잡고 있으면 `timeout` 초까지 기다린다 (0 = 즉시 실패).

        2026-09-19: 기본 대기가 48시간이 되면서 **기다리는 동안 아무 말도 없으면** 멈춘 것처럼 보인다.
        그래서 5초마다 진행 레지스트리에 한 줄 남긴다 — Web 진행 패널·CLI 모니터·`server activity` 에 그대로 보인다.
        기다리지 않고 바로 실패하려면 `build_lock_timeout=0`.
        """
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        deadline = time.time() + self.timeout
        t0 = time.time()
        said = 0.0
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
                now = time.time()
                if now - said >= 5.0:
                    said = now
                    try:
                        from . import progress as _pg
                        _pg.note("다른 빌드가 끝나기를 기다리는 중… %.0f초 (pid=%s, 최대 %.0f분 — build_lock_timeout)"
                                 % (now - t0, (h or {}).get("pid"), self.timeout / 60.0))
                    except Exception:
                        pass
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
