# -*- coding: utf-8 -*-
"""색인 스냅샷 — 파괴적 작업(전체 초기화·로그 삭제·복원) 직전에 DB/rules/wiki/config 를 data/snapshots/ 에 복사해 둔다.

evolve.apply_proposal 이 쓰던 롤백 스냅샷과 같은 레이아웃(db.sqlite3, rules.json, wiki/, config.json)에 meta.json 을 더한 것.
- create(pipe, tag, actor, reason) → dir
- list_(pipe) → [{name, tag, ts, bytes, ...}]
- restore(pipe, name) : evolve._restore 재사용 (DB 파일 교체 + wiki/rules/config 복원 + reload)
- prune(pipe, keep)   : 자동 스냅샷(tag 가 'auto:' 로 시작)을 최신 keep 개만 남긴다. 수동 스냅샷은 지우지 않는다.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from typing import Any, Dict, List


def snapshots_dir(pipe) -> str:
    return os.path.join(pipe.s.data_dir, "snapshots")


def create(pipe, tag: str = "manual", actor: str = "", reason: str = "") -> Dict[str, Any]:
    s = pipe.s
    name = "%s_%s" % (time.strftime("%Y%m%d-%H%M%S"), "".join(c if c.isalnum() or c in "-_" else "-" for c in tag)[:40])
    d = os.path.join(snapshots_dir(pipe), name)
    os.makedirs(d, exist_ok=True)
    pipe.store.conn.commit()
    try:
        pipe.store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    if os.path.exists(s.db_path):
        shutil.copy2(s.db_path, os.path.join(d, "db.sqlite3"))
    from .graph_rules import rules_path as _rules_path
    rp = _rules_path()
    if os.path.exists(rp):
        shutil.copy2(rp, os.path.join(d, "rules.json"))
    if os.path.isdir(s.wiki_dir):
        shutil.copytree(s.wiki_dir, os.path.join(d, "wiki"))
    from .config import CONFIG_PATH
    if os.path.exists(CONFIG_PATH):
        shutil.copy2(CONFIG_PATH, os.path.join(d, "config.json"))
    try:
        stats = pipe.store.stats()
        counts = {k: stats.get(k) for k in ("docs", "chunks", "embeddings", "entities", "relations", "requests")}
    except Exception:
        counts = {}
    meta = {"name": name, "tag": tag, "ts": time.time(), "actor": actor, "reason": reason, "counts": counts,
            "bytes": _du(d)}
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return dict(meta, dir=d)


def list_(pipe) -> List[Dict[str, Any]]:
    root = snapshots_dir(pipe)
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root), reverse=True):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        meta = {"name": name, "tag": "evolve" if name.startswith("p") else "?", "ts": os.path.getmtime(d), "actor": "", "reason": ""}
        mp = os.path.join(d, "meta.json")
        if os.path.exists(mp):
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    meta.update(json.load(f))
            except Exception:
                pass
        meta["bytes"] = meta.get("bytes") or _du(d)
        meta["has_db"] = os.path.exists(os.path.join(d, "db.sqlite3"))
        meta["dir"] = d
        out.append(meta)
    return out


def restore(pipe, name: str) -> Dict[str, Any]:
    d = os.path.join(snapshots_dir(pipe), name)
    if not os.path.isdir(d) or not os.path.exists(os.path.join(d, "db.sqlite3")):
        raise FileNotFoundError("snapshot not found or has no db: %s" % name)
    from .evolve import _restore
    _restore(pipe, {"dir": d})
    return {"restored": name, "stats": pipe.store.stats()}


def prune(pipe, keep: int = 3) -> List[str]:
    auto = [m for m in list_(pipe) if str(m.get("tag", "")).startswith("auto:")]
    removed = []
    for m in auto[max(0, int(keep)):]:
        shutil.rmtree(m["dir"], ignore_errors=True)
        removed.append(m["name"])
    return removed


def _du(d: str) -> int:
    total = 0
    for root, _, files in os.walk(d):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    return total
