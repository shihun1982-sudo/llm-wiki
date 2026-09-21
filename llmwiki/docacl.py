# -*- coding: utf-8 -*-
"""문서 단위 접근 제어 — 어떤 역할이 **어떤 문서를 근거로 볼 수 있는가** (2026-09-19).

## 왜 필요한가

이 시스템의 역할(viewer/class3/class2/class1/builder/admin)은 지금까지 **무엇을 실행할 수 있는가**만 정했다.
질의를 낼 수 있는지, 빌드를 돌릴 수 있는지 같은 것들이다. 그런데 **무엇을 읽을 수 있는가**는 아무도 정하지 않았다.
검색 경로에는 신분이 전달조차 되지 않아서(`_do_query()` 가 사용자 정보를 티켓·로그에만 쓴다),
색인된 문서가 하나라도 있으면 viewer 도 그 내용을 근거로 받아 볼 수 있었다.

사내 위키에는 등급이 다른 문서가 섞인다 — 인사·보안 사고·미공개 로드맵·고객사 이름. 그것까지 전부 같은 답변에
인용되면 RAG 가 곧 정보 유출 경로가 된다. **검색은 읽기다.** 읽기 권한을 문서 단위로 나눌 수 있어야 한다.

## 어떻게 정하나 — 두 곳, 좁은 쪽이 이긴다

1. **문서 자신** — front matter 의 `acl:` (예 `acl: class1` 또는 `acl: [class1, admin]`).
   문서를 쓰는 사람이 스스로 등급을 매긴다. 가장 정확하지만 빠뜨리기 쉽다.
2. **경로 규칙** — `docacl.json` 의 `rules[]`. `{"prefix": "corpus/hr/", "min_role": "class1"}` 처럼
   폴더째 묶는다. 문서를 하나하나 고치지 않아도 되고, 새로 들어온 문서에도 바로 걸린다.

둘 다 걸리면 **더 높은 등급**을 요구한다 (안전한 쪽). 아무것도 안 걸리면 `default_min_role`(기본 `viewer` = 모두 공개).

## 어디서 막나 — 한 군데가 아니라 모든 출구

근거가 새어 나갈 수 있는 길은 여러 개다. 하나만 막으면 나머지로 샌다.

| 출구 | 막는 자리 |
|---|---|
| 질의 답변의 근거 | `query_engine` 의 `doc_acl` 단계 (융합 직후, 부스트 전) |
| 채널 검색 디버그 | `retrieval.channel_search` |
| 문서 열람 | `/api/doc` · `/api/doc_chunks` · `/api/chunk` · MCP `wiki_doc` |
| 그래프·위키 | 엔티티 상세의 문서 참조 |

## 무엇을 하지 않나

- **암호화하지 않는다.** DB 파일을 직접 여는 사람은 다 본다. 이것은 애플리케이션 계층의 접근 제어다.
- **admin 을 막지 않는다.** admin 은 모든 문서를 본다 (운영·감사 목적).
- 기본값은 **아무도 막지 않음**이다. 규칙을 적지 않으면 예전과 똑같이 동작한다 — 켜는 순간 막히면
  "왜 갑자기 근거가 줄었지" 가 되므로, 막힌 건수는 항상 trace 와 응답 메타에 남긴다.

파일: `docacl.json` (원본 `setup/docacl.example.json`) · 설정 `config.json` 의 `doc_acl`(토글).
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import path_for

#: 역할 순서 (낮은 쪽 → 높은 쪽). auth.ROLES 와 같은 순서를 쓴다 — 여기서 다시 정의하지 않고 가져온다.
try:
    from .auth import ROLES as _ROLES
except Exception:      # auth 가 없는 최소 환경 (테스트 등)
    _ROLES = ("viewer", "class3", "class2", "class1", "builder", "admin")

RANK: Dict[str, int] = {r: i for i, r in enumerate(_ROLES)}

DEFAULTS: Dict[str, Any] = {
    "_comment": "문서 단위 접근 제어. 어떤 역할이 어떤 문서를 '근거로' 볼 수 있는지 정합니다. "
                "설명: docs/SECURITY.md · 원본 setup/docacl.example.json. "
                "규칙을 비워 두면 아무도 막지 않습니다(예전 동작).",
    "enabled": True,
    "default_min_role": "viewer",     # 어떤 규칙에도 안 걸리는 문서의 최소 역할 (viewer = 모두 공개)
    "rules": [],                      # [{"prefix": "corpus/hr/", "min_role": "class1", "note": "인사 문서"}]
    "deny_message": "권한이 없는 문서입니다",
}

_CACHE: Dict[str, Any] = {"mtime": None, "data": None}
_LOCK = threading.Lock()


def acl_path() -> str:
    return path_for("docacl")


def load(force: bool = False) -> Dict[str, Any]:
    """docacl.json 을 읽는다 (mtime 캐시). 파일이 없으면 기본값 — 아무도 막지 않는다."""
    p = acl_path()
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return dict(DEFAULTS)
    with _LOCK:
        if not force and _CACHE["mtime"] == mt and _CACHE["data"] is not None:
            return _CACHE["data"]
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return dict(DEFAULTS)
    d = dict(DEFAULTS)
    d.update({k: v for k, v in raw.items() if k in DEFAULTS or k == "rules"})
    d["rules"] = [r for r in (d.get("rules") or []) if isinstance(r, dict) and r.get("prefix")]
    with _LOCK:
        _CACHE["mtime"], _CACHE["data"] = mt, d
    return d


def save(data: Dict[str, Any]) -> str:
    """docacl.json 저장 (admin). 저장하면 캐시가 무효가 된다."""
    from . import atomicio
    p = acl_path()
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (data or {}).items() if k in DEFAULTS or k == "rules"})
    out["rules"] = [r for r in (out.get("rules") or []) if isinstance(r, dict) and r.get("prefix")]
    atomicio.write_text(p, json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    with _LOCK:
        _CACHE["mtime"], _CACHE["data"] = None, None
    return p


def _rank(role: Any) -> int:
    return RANK.get(str(role or "viewer"), 0)


def min_role_for(doc_id: str, meta: Optional[Dict[str, Any]] = None, acl: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """이 문서를 보려면 최소 어떤 역할이어야 하나. 반환: (역할, 근거)

    문서의 `acl` 과 경로 규칙이 둘 다 걸리면 **더 높은 쪽**을 쓴다 (안전한 쪽으로).
    """
    acl = acl if acl is not None else load()
    need, why = str(acl.get("default_min_role") or "viewer"), "default"
    # 1) 경로 규칙
    did = str(doc_id or "").replace("\\", "/")
    for r in acl.get("rules") or []:
        pref = str(r.get("prefix") or "").replace("\\", "/")
        if pref and did.startswith(pref):
            mr = str(r.get("min_role") or "viewer")
            if _rank(mr) > _rank(need):
                need, why = mr, "rule:%s" % pref
    # 2) 문서 자신의 acl (front matter)
    raw = (meta or {}).get("acl")
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",") if x.strip()]
    if isinstance(raw, (list, tuple)) and raw:
        # 목록의 **가장 낮은** 역할이 그 문서를 볼 수 있는 하한이다 (acl: [class1, admin] = class1 이상)
        lows = sorted((str(x) for x in raw if str(x) in RANK), key=_rank)
        if lows and _rank(lows[0]) > _rank(need):
            need, why = lows[0], "doc"
    return need, why


def can_see(role: Any, doc_id: str, meta: Optional[Dict[str, Any]] = None, acl: Optional[Dict[str, Any]] = None) -> bool:
    acl = acl if acl is not None else load()
    if not acl.get("enabled", True):
        return True
    if str(role or "") == "admin":
        return True                       # admin 은 운영·감사를 위해 전부 본다
    need, _ = min_role_for(doc_id, meta, acl)
    return _rank(role) >= _rank(need)


class Filter:
    """한 요청 동안 쓰는 판정기. doc_id 판정을 캐시해 청크 수백 개를 훑어도 빠르다."""

    def __init__(self, role: Any, doc_meta: Optional[Dict[str, Dict[str, Any]]] = None, acl: Optional[Dict[str, Any]] = None):
        self.acl = acl if acl is not None else load()
        self.role = str(role or "viewer")
        self.doc_meta = doc_meta or {}
        self.enabled = bool(self.acl.get("enabled", True)) and self.role != "admin" and bool(self.acl.get("rules") or
                                                                                             _rank(self.acl.get("default_min_role")) > 0)
        self._cache: Dict[str, bool] = {}
        self.blocked_docs: Dict[str, str] = {}      # doc_id -> 필요한 역할

    def doc_ok(self, doc_id: str) -> bool:
        if not self.enabled:
            return True
        v = self._cache.get(doc_id)
        if v is None:
            need, _why = min_role_for(doc_id, self.doc_meta.get(doc_id), self.acl)
            v = _rank(self.role) >= _rank(need)
            self._cache[doc_id] = v
            if not v:
                self.blocked_docs[doc_id] = need
        return v

    def chunk_ok(self, chunk_id: str, doc_id: str = "") -> bool:
        return self.doc_ok(doc_id or str(chunk_id or "").rsplit("#", 1)[0])

    @staticmethod
    def doc_id_of(chunk: Any, chunk_id: str = "") -> str:
        """청크 레코드에서 doc_id 를 꺼낸다.

        파이프라인이 넘겨 주는 청크는 자리에 따라 dict 이기도 하고 **sqlite3.Row** 이기도 하다.
        Row 에는 `.get` 이 없어서 `c.get("doc_id")` 가 AttributeError 로 터지고, 그러면 접근 제어가
        통째로 건너뛰어진다(= 전부 통과). 접근 제어에서 '예외 하나에 열려 버리는' 길은 없어야 하므로
        여기서 두 모양을 모두 받고, 못 찾으면 chunk_id 앞부분으로 되짚는다.
        """
        did = ""
        try:
            if isinstance(chunk, dict):
                did = chunk.get("doc_id") or ""
            elif chunk is not None:
                did = (chunk["doc_id"] if "doc_id" in getattr(chunk, "keys", lambda: [])() else "") or ""
        except Exception:
            did = ""
        return str(did or str(chunk_id or "").rsplit("#", 1)[0])

    def filter_hits(self, hits: List[Any], chunks: Optional[Dict[str, Any]] = None) -> Tuple[List[Any], int]:
        """Hit 목록에서 볼 수 없는 문서의 청크를 뺀다. 반환: (남은 것, 뺀 수)"""
        if not self.enabled:
            return hits, 0
        chunks = chunks or {}
        out, removed = [], 0
        for h in hits:
            cid = getattr(h, "chunk_id", None) or (h.get("chunk_id") if isinstance(h, dict) else "")
            if self.doc_ok(self.doc_id_of(chunks.get(cid), cid)):
                out.append(h)
            else:
                removed += 1
        return out, removed

    def summary(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "role": self.role,
                "blocked_docs": len(self.blocked_docs),
                "needs": sorted(set(self.blocked_docs.values()))}


def describe(acl: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """화면·CLI 용 설명: 규칙 목록과 역할 순서."""
    acl = acl if acl is not None else load()
    return {"path": acl_path(), "exists": os.path.exists(acl_path()), "enabled": bool(acl.get("enabled", True)),
            "default_min_role": acl.get("default_min_role"), "roles": list(_ROLES),
            "rules": list(acl.get("rules") or []),
            "note": "문서의 front matter `acl:` 과 경로 규칙 중 **더 높은 등급**이 적용됩니다. admin 은 항상 전부 봅니다."}


def check(store: Any, role: str, sample: int = 200) -> Dict[str, Any]:
    """지금 색인된 문서에 규칙을 대 보고 몇 개가 가려지는지 — 규칙을 넣기 전에 영향을 본다."""
    acl = load(force=True)
    meta = store.doc_meta_map() if hasattr(store, "doc_meta_map") else {}
    f = Filter(role, meta, acl)
    rows, blocked = [], 0
    for i, doc_id in enumerate(sorted(meta) or []):
        ok = f.doc_ok(doc_id)
        if not ok:
            blocked += 1
            if len(rows) < sample:
                need, why = min_role_for(doc_id, meta.get(doc_id), acl)
                rows.append({"doc_id": doc_id, "min_role": need, "why": why})
    return {"role": role, "docs": len(meta), "blocked": blocked, "visible": len(meta) - blocked,
            "examples": rows, "enabled": f.enabled, "ts": time.time()}
