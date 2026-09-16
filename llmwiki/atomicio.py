"""원자적 파일 저장 — 여러 요청이 같은 파일을 동시에 저장해도 깨지지 않게.

서버가 병렬로 동작하면서 같은 설정 파일(security.json, server.json, profiles.json,
query_rules.json, schedule_state.json …)을 두 요청이 같은 순간에 저장할 수 있게 됐다.
예전처럼 `path + ".tmp"` 라는 **고정된 이름**을 쓰면 두 가지가 깨진다.

1. A가 임시 파일을 쓰는 도중 B가 같은 이름을 열어 truncate → A가 쓴 내용이 사라진다.
2. Windows 에서는 B가 아직 열고 있는 임시 파일을 A가 os.replace 하려다
   ``PermissionError: [WinError 5] 액세스가 거부되었습니다`` 로 실패한다.
   백신·검색 색인·탐색기 미리보기가 방금 만든 파일을 잠깐 잡고 있을 때도 같은 오류가 난다.

그래서 (a) 임시 파일 이름에 pid·스레드 id·난수를 붙여 호출마다 다르게 만들고,
(b) 같은 프로세스 안에서는 경로별 락으로 순서를 세우고,
(c) 그래도 실패하면 짧게 기다렸다 다시 시도한다.

기본값은 환경 변수로 조절한다(서버 재시작 필요):

| 환경 변수 | 기본값 | 뜻 |
|---|---|---|
| `LLMWIKI_ATOMIC_RETRIES` | 6 | os.replace 재시도 횟수 |
| `LLMWIKI_ATOMIC_RETRY_MS` | 40 | 첫 재시도 대기(ms). 시도마다 2배씩 늘어난다 |
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Any, Dict


def _env_int(name: str, default: int) -> int:
    try:
        v = int(str(os.environ.get(name, "")).strip())
        return v if v >= 0 else default
    except Exception:
        return default


RETRIES = _env_int("LLMWIKI_ATOMIC_RETRIES", 10)
RETRY_MS = _env_int("LLMWIKI_ATOMIC_RETRY_MS", 20)

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    key = os.path.abspath(path).lower()
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.Lock()
        return lk


def _tmp_name(path: str) -> str:
    return "%s.%d.%d.%04x.tmp" % (path, os.getpid(), threading.get_ident() & 0xFFFF,
                                  random.getrandbits(16))


def read_text(path: str, encoding: str = "utf-8", default: Any = None) -> Any:
    """설정 파일을 읽는다. 저장 중이면 끝날 때까지 기다린다.

    Windows 는 다른 스레드가 **열어 두고만 있어도** os.replace 를 거부한다(FILE_SHARE_DELETE 없음).
    그래서 읽기도 같은 경로 락을 잡아 저장과 겹치지 않게 한다. 없는 파일이면 default 를 돌려준다.
    """
    with _lock_for(path):
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except FileNotFoundError:
            return default


def read_json(path: str, default: Any = None) -> Any:
    """JSON 설정 파일을 읽는다. 없거나 깨졌으면 default 를 돌려준다(호출자가 복구 처리)."""
    s = read_text(path)
    if s is None:
        return default
    try:
        return json.loads(s)
    except ValueError:
        return default


def write_text(path: str, text: str, encoding: str = "utf-8", newline: str = "") -> str:
    """text 를 path 에 원자적으로 쓴다. 성공하면 path 를 돌려준다."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    with _lock_for(path):
        tmp = _tmp_name(path)
        try:
            with open(tmp, "w", encoding=encoding, newline=newline) as f:
                f.write(text)
                f.flush()
                try:
                    os.fsync(f.fileno())   # 정전·강제 종료에도 반쪽 파일이 남지 않도록
                except OSError:
                    pass
            last: Exception | None = None
            delay = RETRY_MS / 1000.0
            for attempt in range(RETRIES + 1):
                try:
                    os.replace(tmp, path)
                    return path
                except PermissionError as e:        # WinError 5 / 32 — 누가 잠깐 잡고 있음
                    last = e
                except OSError as e:
                    last = e
                    if getattr(e, "winerror", None) not in (5, 32):
                        raise
                if attempt < RETRIES:
                    time.sleep(delay + random.random() * delay * 0.25)
                    delay = min(delay * 2, 1.0)
            raise last if last else OSError("원자적 교체 실패: %s" % path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def write_json(path: str, obj: Any, indent: int | None = 2, ensure_ascii: bool = False,
               sort_keys: bool = False, default: Any = None) -> str:
    """JSON 을 원자적으로 저장한다. 직렬화가 실패하면 기존 파일은 그대로 둔다."""
    text = json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent, sort_keys=sort_keys,
                      default=default)
    return write_text(path, text)


def cleanup_stale(path: str, older_than_s: float = 3600.0) -> int:
    """죽은 프로세스가 남긴 임시 파일을 치운다. 지운 개수를 돌려준다."""
    d = os.path.dirname(path) or "."
    base = os.path.basename(path)
    n = 0
    now = time.time()
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for name in names:
        if not (name.startswith(base + ".") and name.endswith(".tmp")):
            continue
        p = os.path.join(d, name)
        try:
            if now - os.path.getmtime(p) > older_than_s:
                os.remove(p)
                n += 1
        except OSError:
            pass
    return n
