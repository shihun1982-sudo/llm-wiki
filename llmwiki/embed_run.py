# -*- coding: utf-8 -*-
"""임베딩 실행기 — 재개/체크포인트 · 내용 해시 캐시 · 적응형 배치 · WAL 관리 · 진행률 · 알림.

품질/질의 지연과는 무관한 '실행 안정성' 계층이다. 유일한 품질 영향은 임베딩 실패로 coverage 가 낮아지는 것이며,
그래서 coverage 를 health/embed report/trial 지표에 노출한다.

- 캐시: embedding_cache(provider, model, sha1(text)) — 같은 내용은 재임베딩하지 않음 (rename/이동/재빌드/롤백 비용 0).
  hash 임베더는 IDF 에 의존해 캐시하지 않는다(어차피 로컬·즉시).
- 재개: N 배치마다 commit + kv embed_progress 저장. 중단되면 다음 build 가 missing_embeddings 만 다시 처리한다.
- 적응형 배치: 실패/타임아웃/느린 배치 → 절반, 연속 성공 → 1.5배(상한 embed_batch_max). 1건 배치도 실패하면 그 청크만 failed 로 기록하고 계속.
- WAL: 커밋 후 -wal 파일이 wal_checkpoint_mb 를 넘으면 PASSIVE 체크포인트. 반복 실패는 alert.
- 알림(alerts): 연속 실패·429 지속·차원 불일치·중단 → result["alerts"] + 로그 WARN + Web 배너.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from .textutil import sha1
from . import logging_setup as _log


class EmbedRunner:
    def __init__(self, store, embedder, settings, run_id: str, report: Optional[Callable[[str], None]] = None, adaptive: bool = True):
        self.store, self.emb, self.s = store, embedder, settings
        self.run_id = run_id
        self.report = report or (lambda m: None)
        self.adaptive = adaptive
        self.alerts: List[Dict[str, str]] = []
        self.provider = embedder.name
        self.model = str(getattr(embedder, "model", "") or "")
        self.use_cache = embedder.name != "hash"

    # ---- 진행률 ----
    def _progress(self, st: Dict[str, Any], status: str) -> None:
        st = dict(st, run_id=self.run_id, status=status, updated=time.time(), provider=self.provider, model=self.model, alerts=self.alerts[-5:])
        el = max(0.001, st["updated"] - st["started"])
        st["rate_per_s"] = round(st["done"] / el, 2)
        left = max(0, st["total"] - st["done"] - st["failed"])
        st["eta_s"] = round(left / st["rate_per_s"], 1) if st["rate_per_s"] > 0 else None
        try:
            self.store.kv_set("embed_progress", st)
        except Exception:
            pass

    def _alert(self, level: str, msg: str, **data: Any) -> None:
        self.alerts.append({"level": level, "msg": msg})
        _log.log("warning" if level != "fail" else "error", "embed alert: " + msg, "build", **data)

    def _wal_check(self, st: Dict[str, Any]) -> None:
        wal = self.s.db_path + "-wal"
        try:
            mb = os.path.getsize(wal) / 1e6 if os.path.exists(wal) else 0.0
        except OSError:
            return
        if mb > float(self.s.wal_checkpoint_mb or 64):
            try:
                row = self.store.conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                busy = bool(row and row[0] == 1)
                st["wal_checkpoints"] = st.get("wal_checkpoints", 0) + 1
                if busy:
                    st["wal_busy"] = st.get("wal_busy", 0) + 1
                    if st["wal_busy"] >= 3:
                        self._alert("warn", "WAL 체크포인트가 반복 실패 (다른 연결이 읽는 중). wal=%.0fMB" % mb, wal_mb=mb)
            except Exception as e:
                self._alert("warn", "WAL 체크포인트 오류: %s" % e)

    # ---- 실행 ----
    def run(self, todo: List[Any], dtype: str = "float32") -> Dict[str, Any]:
        s = self.s
        texts = [c["heading"] + "\n" + c["text"] for c in todo]
        ids = [c["chunk_id"] for c in todo]
        shas = [sha1(t) for t in texts]
        st: Dict[str, Any] = {"total": len(todo), "done": 0, "failed": 0, "cache_hits": 0, "batches": 0, "batch": int(s.embed_batch or 64),
                              "started": time.time(), "failed_ids": [], "batch_ms": [], "wal_checkpoints": 0, "resized": 0}
        # 1) 캐시 조회
        remaining = list(range(len(todo)))
        if self.use_cache and todo:
            cached = self.store.cache_get(self.provider, self.model, shas)
            if cached:
                items = []
                for i in remaining:
                    v = cached.get(shas[i])
                    if v is not None:
                        items.append((ids[i], v))
                if items:
                    # 차원 일치 확인 (모델은 같아도 서버 설정이 바뀌었을 수 있음)
                    dims = {int(v.shape[0]) for _, v in items}
                    if len(dims) == 1:
                        self.store.put_embeddings(self.provider, items, dtype=dtype)
                        hit_shas = {shas[i] for i in remaining if shas[i] in cached}
                        remaining = [i for i in remaining if shas[i] not in hit_shas]
                        st["cache_hits"] = len(items)
                        st["done"] += len(items)
                        self.store.commit()
        self._progress(st, "running")
        # 2) 적응형 배치 루프
        B = max(1, min(int(s.embed_batch or 64), int(s.embed_batch_max or 256)))
        B_max = max(B, int(s.embed_batch_max or 256))
        target_ms = float(s.embed_batch_target_ms or 8000)
        commit_every = max(1, int(s.embed_commit_every or 10))
        streak = 0
        consecutive_item_fail = 0
        rate_limited = 0
        pos = 0
        last_report = time.time()
        aborted = False
        dim_seen: Optional[int] = None
        while pos < len(remaining):
            idx = remaining[pos:pos + B]
            part_texts = [texts[i] for i in idx]
            t0 = time.perf_counter()
            try:
                vecs = self.emb.embed(part_texts)
                ms = (time.perf_counter() - t0) * 1000
                if vecs.shape[0] != len(idx):
                    raise RuntimeError("embedder returned %d vectors for %d texts" % (vecs.shape[0], len(idx)))
                d = int(vecs.shape[1])
                if dim_seen is None:
                    dim_seen = d
                elif d != dim_seen:
                    self._alert("fail", "임베딩 차원이 실행 중 바뀜 (%d → %d). 프로바이더/모델 설정 확인 후 build --full" % (dim_seen, d))
                    aborted = True
                    break
                self.store.put_embeddings(self.provider, [(ids[i], vecs[j]) for j, i in enumerate(idx)], dtype=dtype)
                if self.use_cache:
                    self.store.cache_put(self.provider, self.model, [(shas[i], vecs[j]) for j, i in enumerate(idx)], dtype=dtype)
                st["done"] += len(idx)
                st["batches"] += 1
                st["batch_ms"].append(ms)
                pos += len(idx)
                streak += 1
                consecutive_item_fail = 0
                if self.adaptive:
                    if ms > target_ms and B > 1:
                        B = max(1, B // 2)
                        streak = 0
                        st["resized"] += 1
                    elif streak >= 3 and ms < target_ms / 2 and B < B_max:
                        B = min(B_max, int(B * 1.5) + 1)
                        streak = 0
                        st["resized"] += 1
            except Exception as e:
                msg = str(e)[:300]
                st["errors"] = st.get("errors", 0) + 1
                is_rate = "429" in msg or "rate" in msg.lower()
                if is_rate:
                    rate_limited += 1
                    time.sleep(min(30.0, 2.0 * rate_limited))
                    if rate_limited >= 5:
                        self._alert("warn", "429/rate limit 이 5회 이상 지속 — embed_batch 축소 또는 잠시 후 재실행 권장", error=msg)
                if B > 1 and self.adaptive:
                    B = max(1, B // 2)
                    streak = 0
                    st["resized"] += 1
                    _log.log("warning", "embed batch failed → batch %d" % B, "build", error=msg, batch=len(idx))
                    continue   # 같은 위치 재시도
                # 배치 1 도 실패 → 이 청크만 실패 처리
                st["failed"] += 1
                st["failed_ids"].append(ids[idx[0]])
                pos += 1
                consecutive_item_fail += 1
                _log.log("warning", "embed chunk failed: %s" % ids[idx[0]], "build", error=msg)
                if consecutive_item_fail >= 10:
                    self._alert("fail", "임베딩이 10건 연속 실패 — 프로바이더 장애로 판단해 중단 (남은 청크는 다음 build 에서 재개)", error=msg)
                    aborted = True
                    break
            st["batch"] = B
            if st["batches"] and st["batches"] % commit_every == 0:
                self.store.commit()
                self._wal_check(st)
                self._progress(st, "running")
            if time.time() - last_report > 3 or (st["done"] + st["failed"]) == len(todo):
                last_report = time.time()
                self.report("embedding %d/%d (batch %d, failed %d, cache %d)" % (st["done"], len(todo), B, st["failed"], st["cache_hits"]))
        self.store.commit()
        self._wal_check(st)
        status = "aborted" if aborted else ("done" if st["failed"] == 0 else "done_with_failures")
        if st["failed"] and not aborted:
            self._alert("warn", "임베딩 실패 %d건 (coverage 저하 → 벡터 recall 영향). embed report 로 확인, 다음 build 에서 재시도" % st["failed"])
        self._progress(st, status)
        avg = sum(st["batch_ms"]) / len(st["batch_ms"]) if st["batch_ms"] else 0.0
        row = {"run_id": self.run_id, "ts_start": st["started"], "ts_end": time.time(), "provider": self.provider, "model": self.model,
               "dim": dim_seen or int(getattr(self.emb, "dim", 0) or 0), "total": st["total"], "done": st["done"], "failed": st["failed"],
               "cache_hits": st["cache_hits"], "batches": st["batches"], "avg_batch_ms": round(avg, 1), "final_batch": B, "status": status,
               "alerts": self.alerts, "failed_ids": st["failed_ids"]}
        try:
            self.store.put_embed_run(row)
            self.store.commit()
        except Exception:
            pass
        return {"embedded": st["done"], "failed": st["failed"], "cache_hits": st["cache_hits"], "batches": st["batches"], "final_batch": B,
                "avg_batch_ms": round(avg, 1), "resized": st["resized"], "wal_checkpoints": st["wal_checkpoints"], "status": status,
                "alerts": self.alerts, "failed_ids": st["failed_ids"][:20], "dim": row["dim"]}


def embed_report(pipe) -> Dict[str, Any]:
    """coverage · 실패 · 캐시 · 실행 이력 · 진행 상태."""
    emb = pipe.embedder
    cov = pipe.store.embed_coverage(emb.name)
    return {"embedder": {"provider": emb.name, "model": getattr(emb, "model", None), "dim": getattr(emb, "dim", None),
                         "store_dtype": pipe.s.embed_store_dtype},
            "coverage": cov, "cache": pipe.store.cache_stats() if emb.name != "hash" else {"entries": 0, "note": "hash 임베더는 캐시 미사용"},
            "progress": pipe.store.kv_get("embed_progress"), "runs": pipe.store.embed_runs(10),
            "settings": {k: getattr(pipe.s, k) for k in ("embed_batch", "embed_batch_max", "embed_batch_target_ms", "embed_commit_every", "wal_checkpoint_mb")},
            "toggles": {"embed": pipe.s.toggles.embed, "embed_adaptive": pipe.s.toggles.embed_adaptive}}
