# -*- coding: utf-8 -*-
"""사용자별 Web UI 설정 프로파일 — <data_dir>/profiles.json. (2026-09-15)

사람마다 즐겨 쓰는 화면 상태가 다르다: 켜 두는 토글, 체크하는 프리셋, 요청 단위 모델 오버라이드, 테마, 고정해 둔 탭, 분할 보기.
브라우저 localStorage 에만 두면 PC·브라우저를 바꾸면 사라지므로, 로그인 사용자별로 서버에 저장한다.

- **서버 설정(config.json)을 바꾸지 않는다.** 저장되는 값은 그 사람의 화면 상태와 '요청 단위' 오버라이드뿐이라,
  다른 사용자의 질의 결과에 영향을 주지 않는다 (요청 격리: llmwiki/pipeline.py request_scope).
- 게스트(비로그인)는 저장하지 않는다 — 브라우저 localStorage 로만 유지된다.
- 크기 상한(기본 32KB/사용자)과 사용자 수 상한을 두어 파일이 무한히 커지지 않게 한다.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional

from . import atomicio

MAX_BYTES = 32 * 1024
MAX_USERS = 1000
_LOCK = threading.Lock()

# 저장을 허용하는 키 (알 수 없는 키는 버린다 — 임의의 데이터 저장소로 쓰이지 않게)
# pinview: 고정 탭 화면의 배치 상태 {cols, height, wide{}, collapsed{}} — split 은 이전 버전(2열 on/off) 호환용
ALLOWED = ("theme", "toggles", "presets", "overrides", "pins", "pinview", "split", "mode", "tab", "group", "columns", "note")


def profiles_path(data_dir: str) -> str:
    return os.path.join(data_dir, "profiles.json")


def _load(path: str) -> Dict[str, Any]:
    try:
        d = atomicio.read_json(path, {})
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(path: str, data: Dict[str, Any]) -> None:
    atomicio.write_json(path, data, indent=1)


def sanitize(profile: Any) -> Dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError("profile 은 객체(JSON object)여야 합니다")
    out: Dict[str, Any] = {}
    for k in ALLOWED:
        if k in profile:
            out[k] = profile[k]
    raw = json.dumps(out, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("프로파일이 너무 큽니다 (%d바이트 > %d)" % (len(raw.encode("utf-8")), MAX_BYTES))
    return out


def get(data_dir: str, user: str) -> Dict[str, Any]:
    with _LOCK:
        d = _load(profiles_path(data_dir))
    return dict((d.get(user) or {}))


def save(data_dir: str, user: str, profile: Any) -> Dict[str, Any]:
    clean = sanitize(profile)
    clean["_saved"] = time.time()
    path = profiles_path(data_dir)
    with _LOCK:
        d = _load(path)
        d[user] = clean
        if len(d) > MAX_USERS:      # 가장 오래된 것부터 정리
            for k in sorted(d, key=lambda x: (d[x] or {}).get("_saved", 0))[: len(d) - MAX_USERS]:
                d.pop(k, None)
        _save(path, d)
    return clean


def reset(data_dir: str, user: str) -> bool:
    path = profiles_path(data_dir)
    with _LOCK:
        d = _load(path)
        ok = d.pop(user, None) is not None
        if ok:
            _save(path, d)
    return ok


def list_users(data_dir: str) -> Dict[str, Any]:
    with _LOCK:
        d = _load(profiles_path(data_dir))
    return {u: {"saved": (p or {}).get("_saved"), "keys": sorted(k for k in (p or {}) if not k.startswith("_"))} for u, p in d.items()}
