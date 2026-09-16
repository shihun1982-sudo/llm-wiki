# -*- coding: utf-8 -*-
"""터미널 출력 인코딩 정리 — 다른 환경(특히 Windows)에서 한글·기호가 깨지거나 명령이 죽지 않게 한다. (2026-09-15)

왜 필요한가 (실제로 재현된 증상)
  - Windows 의 로캘 코드페이지는 보통 cp949(한국어) 나 cp1252(영문)다. 파이썬은 **출력이 파일/파이프로 리디렉션되면** 그 로캘 인코딩을 쓴다.
    이때 이 프로그램이 쓰는 기호(⏳ ✔ ✘ · → ⚠ 📊 ■ ▶)는 cp949 에 없어서 `UnicodeEncodeError: 'cp949' codec can't encode character`
    로 **명령이 중간에 죽는다**. (`llmwiki ... > log.txt`, `| findstr`, CI 로그 캡처, 다른 도구 안에서 실행할 때)
  - 콘솔에 직접 출력할 때도 코드페이지가 UTF-8 이 아니면 한글이 깨져 보이는 환경이 있다.
  - 영문 Windows(cp1252)에서는 기호뿐 아니라 **한글 자체**가 인코딩되지 않는다.

무엇을 하는가
  1. Windows 이고 표준 출력이 진짜 콘솔이면 콘솔 코드페이지를 UTF-8(65001)로 바꾼다 (`chcp 65001` 과 같음). 프로세스가 끝나면 원래대로 되돌린다.
  2. stdout / stderr / stdin 을 UTF-8 + errors="replace" 로 다시 연다. 리디렉션·파이프·비한국어 로캘에서도 죽지 않고,
     표현할 수 없는 글자는 '?' 로 바뀔 뿐이다.
  3. 자식 프로세스(headless 에이전트, 스케줄 python 스크립트, mcp stdio)가 같은 규칙을 쓰도록 PYTHONIOENCODING 을 물려준다.

설정 (config.json · 환경변수가 우선)
  console_encoding    : auto(기본) | utf-8 | native | off     — LLMWIKI_CONSOLE_ENCODING
  console_set_codepage: true(기본) | false                    — LLMWIKI_CONSOLE_SET_CODEPAGE
    auto      : 아래 규칙대로 (콘솔이면 CP 를 UTF-8 로, 아니면 UTF-8 로 인코딩)
    utf-8     : 항상 UTF-8 (코드페이지는 console_set_codepage 에 따름)
    native    : 코드페이지를 바꾸지 않고 터미널의 기본 인코딩을 쓰되 errors=replace 만 적용 (기호는 ? 로)
    off       : 아무것도 하지 않음 (예전 동작)
"""
from __future__ import annotations

import atexit
import json
import os
import sys
from typing import Any, Dict, Optional

FILE_TYPE_CHAR = 2      # GetFileType: 콘솔
_STD_OUTPUT_HANDLE = -11
_STD_ERROR_HANDLE = -12

STATE: Dict[str, Any] = {"applied": False, "mode": None, "console": False, "codepage_before": None,
                         "codepage_now": None, "stdout": None, "stderr": None, "note": ""}


def _config_values() -> Dict[str, Any]:
    """config.json 에서 콘솔 설정만 가볍게 읽는다 (Settings 전체 로딩 전에 호출되므로 직접 읽는다)."""
    out: Dict[str, Any] = {"console_encoding": "auto", "console_set_codepage": True}
    path = os.environ.get("LLMWIKI_CONFIG")
    if not path:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for k in out:
            if k in raw:
                out[k] = raw[k]
    except Exception:
        pass
    env = os.environ.get("LLMWIKI_CONSOLE_ENCODING")
    if env:
        out["console_encoding"] = env
    env2 = os.environ.get("LLMWIKI_CONSOLE_SET_CODEPAGE")
    if env2:
        out["console_set_codepage"] = str(env2).strip().lower() in ("1", "true", "yes", "on")
    return out


def _kernel32():
    import ctypes
    return ctypes.windll.kernel32   # type: ignore[attr-defined]


def is_console() -> bool:
    if os.name != "nt":
        try:
            return bool(sys.stdout and sys.stdout.isatty())
        except Exception:
            return False
    try:
        k = _kernel32()
        return k.GetFileType(k.GetStdHandle(_STD_OUTPUT_HANDLE)) == FILE_TYPE_CHAR
    except Exception:
        return False


def _restore_codepage() -> None:
    cp = STATE.get("codepage_before")
    if not cp or os.name != "nt":
        return
    try:
        k = _kernel32()
        k.SetConsoleOutputCP(int(cp))
        k.SetConsoleCP(int(cp))
        STATE["codepage_now"] = int(cp)
    except Exception:
        pass


def _reconfigure(name: str, encoding: Optional[str]) -> str:
    st = getattr(sys, name, None)
    if st is None:
        return "none"
    try:
        if encoding:
            st.reconfigure(encoding=encoding, errors="replace")
        else:
            st.reconfigure(errors="replace")
        return "%s/%s" % (getattr(st, "encoding", "?"), getattr(st, "errors", "?"))
    except Exception:
        # reconfigure 를 지원하지 않는 스트림(리디렉션된 StringIO, 일부 임베디드 환경)
        return "unchanged:%s" % getattr(st, "encoding", "?")


def setup(force: bool = False) -> Dict[str, Any]:
    """CLI 진입점에서 한 번 호출. 두 번째부터는 아무것도 하지 않는다 (force=True 면 다시 적용)."""
    if STATE["applied"] and not force:
        return STATE
    STATE["applied"] = True
    cfg = _config_values()
    mode = str(cfg.get("console_encoding") or "auto").strip().lower()
    STATE["mode"] = mode
    if mode in ("off", "none", "disabled"):
        STATE["note"] = "console_encoding=off — 인코딩을 건드리지 않음"
        return STATE
    console = is_console()
    STATE["console"] = console
    if os.name == "nt":
        try:
            STATE["codepage_now"] = _kernel32().GetConsoleOutputCP()
        except Exception:
            STATE["codepage_now"] = None
    # 1) Windows 콘솔 코드페이지 → UTF-8
    if os.name == "nt" and console and mode != "native" and cfg.get("console_set_codepage", True):
        try:
            k = _kernel32()
            cur = int(k.GetConsoleOutputCP())
            if cur != 65001:
                if k.SetConsoleOutputCP(65001):
                    STATE["codepage_before"] = cur
                    STATE["codepage_now"] = 65001
                    try:
                        k.SetConsoleCP(65001)
                    except Exception:
                        pass
                    atexit.register(_restore_codepage)
                else:
                    mode = "native"      # 코드페이지를 못 바꿈 → 콘솔 기본 인코딩 유지 (UTF-8 바이트를 흘리면 오히려 깨진다)
                    STATE["note"] = "콘솔 코드페이지를 UTF-8 로 바꾸지 못했습니다 (cp%d 유지, 표현 불가 문자는 ?)" % cur
        except Exception:
            mode = "native"
    # 2) 스트림 재구성
    enc = None if mode == "native" else "utf-8"
    STATE["stdout"] = _reconfigure("stdout", enc)
    STATE["stderr"] = _reconfigure("stderr", enc)
    _reconfigure("stdin", enc)
    # 3) 자식 프로세스
    if enc:
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    return STATE


def describe() -> Dict[str, Any]:
    """health / system 에서 보여 줄 현재 상태."""
    d = dict(STATE)
    d["locale_encoding"] = _preferred()
    d["stdout_encoding"] = getattr(sys.stdout, "encoding", None)
    d["stderr_encoding"] = getattr(sys.stderr, "encoding", None)
    d["PYTHONIOENCODING"] = os.environ.get("PYTHONIOENCODING")
    d["safe"] = bool(_can_encode(sys.stdout))
    return d


def _preferred() -> str:
    try:
        import locale
        return locale.getpreferredencoding(False)
    except Exception:
        return "?"


def _can_encode(stream) -> bool:
    """이 스트림으로 한글과 이 프로그램이 쓰는 기호를 오류 없이 출력할 수 있는가."""
    enc = getattr(stream, "encoding", None)
    if not enc:
        return False
    try:
        SAMPLE.encode(enc, errors=getattr(stream, "errors", "strict") or "strict")
        return True
    except Exception:
        return False


SAMPLE = "한글 ⏳ ✔ ✘ · → ⚠ ■ ▶ 📊"


def safe(text: str, stream=None) -> str:
    """이 터미널에서 표현할 수 없는 글자를 ASCII 로 바꿔 준다 (native 모드·구형 터미널 대비)."""
    stream = stream or sys.stdout
    enc = getattr(stream, "encoding", None) or "utf-8"
    try:
        text.encode(enc)
        return text
    except Exception:
        pass
    for a, b in (("⏳", "..."), ("✔", "OK"), ("✘", "X"), ("⚠", "!"), ("■", "#"), ("▶", ">"), ("›", ">"), ("·", "-"),
                 ("→", "->"), ("←", "<-"), ("📊", "[report]"), ("🔬", "[forensic]"), ("🎯", "[expect]"), ("ℹ", "i"),
                 ("…", "..."), ("─", "-"), ("│", "|"), ("✅", "OK"), ("❌", "X")):
        text = text.replace(a, b)
    try:
        return text.encode(enc, errors="replace").decode(enc, errors="replace")
    except Exception:
        return text.encode("ascii", errors="replace").decode("ascii")
