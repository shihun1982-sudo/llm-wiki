# -*- coding: utf-8 -*-
"""Precompute — (a) 답변 사전 계산 캐시(answer_cache, 영속, build_version 키) (b) 문서 카드 임베딩(doc_vectors) 채널.

(a) precompute run : 평가셋 + 최근 질의 로그 상위 N 질의를 미리 실행해 저장. 질의 시 toggles.precompute 면 캐시 우선.
    캐시 키 = sha1(정규화 질의 + build_version + 답변에 영향을 주는 설정 요약). 재빌드(build_version 증가) 시 자동 무효화.
(b) doc_vector    : 문서 단위 카드(제목·front matter 요약·헤딩 개요·첫 문단) 를 임베딩해 doc_vectors 에 저장.
    질의 시 문서 카드 유사도 → 문서의 대표 청크(첫 청크 또는 키워드 최다 청크) 를 'doc_vector' 채널 후보로. 긴 설계 문서에 유리.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .textutil import keywords, normalize_token, words

_HEAD = re.compile(r"^#{1,6}\s+(.*)$", re.M)


def _norm_q(q: str) -> str:
    return " ".join(keywords(q)) or q.strip().lower()


def cache_key(q: str, build_version: int, sig: Dict[str, Any]) -> str:
    raw = json.dumps({"q": _norm_q(q), "v": build_version, "sig": sig}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get_cached(store, key: str) -> Optional[Dict[str, Any]]:
    r = store.conn.execute("SELECT result, build_version, source FROM answer_cache WHERE key=?", (key,)).fetchone()
    if not r:
        return None
    if int(r["build_version"]) != store.build_version():
        return None
    store.conn.execute("UPDATE answer_cache SET hits = hits + 1 WHERE key=?", (key,))
    try:
        res = json.loads(r["result"])
    except Exception:
        return None
    res["precomputed_source"] = r["source"]
    return res


def put_cached(store, key: str, q: str, result: Dict[str, Any], source: str = "query") -> None:
    # 반복 루프로 망가진 답변은 저장하지 않는다. 한 번 들어가면 그 질문은 리빌드 전까지
    # 계속 같은 고장 답변을 1ms 만에 돌려주게 된다 (2026-09-16).
    if result.get("repeat_loop"):
        return
    slim = {k: v for k, v in result.items() if k not in ("proposals",)}
    store.conn.execute("INSERT OR REPLACE INTO answer_cache(key,build_version,query,result,ts,hits,source) VALUES(?,?,?,?,?,COALESCE((SELECT hits FROM answer_cache WHERE key=?),0),?)",
                       (key, store.build_version(), q, json.dumps(slim, ensure_ascii=False, default=str), time.time(), key, source))
    store.conn.commit()


def cache_status(store) -> Dict[str, Any]:
    v = store.build_version()
    rows = store.conn.execute("SELECT COUNT(*) n, SUM(CASE WHEN build_version=? THEN 1 ELSE 0 END) cur, SUM(hits) hits FROM answer_cache", (v,)).fetchone()
    return {"entries": int(rows["n"] or 0), "current_version": int(rows["cur"] or 0), "hits": int(rows["hits"] or 0), "build_version": v}


def find_broken(store) -> List[Dict[str, Any]]:
    """캐시에 들어앉은 '고장난 답변'(같은 구절 반복 루프)을 찾는다.

    반복 루프 탐지를 넣기 전에 저장된 항목은 그대로 남아 있어서, 그 질문은 리빌드 전까지
    계속 같은 고장 답변을 1ms 만에 돌려준다. `precompute clear --broken` 으로 그것만 지운다.
    """
    from .answer import find_repeat_loop
    out: List[Dict[str, Any]] = []
    for r in store.conn.execute("SELECT key, query, result, build_version, hits FROM answer_cache"):
        try:
            res = json.loads(r["result"])
        except Exception:
            out.append({"key": r["key"], "query": r["query"], "reason": "JSON 파손"})
            continue
        info = find_repeat_loop(str(res.get("answer") or ""))
        if info:
            out.append({"key": r["key"], "query": r["query"], "build_version": r["build_version"],
                        "hits": r["hits"], "times": info["times"], "phrase": info["phrase"], "reason": "반복 루프"})
    return out


def clear_cache(store, stale_only: bool = False, broken_only: bool = False) -> int:
    if broken_only:
        n = 0
        for k in {b["key"] for b in find_broken(store)}:
            n += store.conn.execute("DELETE FROM answer_cache WHERE key=?", (k,)).rowcount
        store.conn.commit()
        return n
    if stale_only:
        cur = store.conn.execute("DELETE FROM answer_cache WHERE build_version != ?", (store.build_version(),))
    else:
        cur = store.conn.execute("DELETE FROM answer_cache")
    store.conn.commit()
    return cur.rowcount


def run(pipe, questions: Optional[List[str]] = None, from_log: int = 20, progress=None) -> Dict[str, Any]:
    """평가셋 질문 + 최근 질의 로그 상위 from_log 개를 미리 실행. 이미 캐시된 것은 건너뜀."""
    from .evalset import load_questions
    qs: List[str] = list(questions or [])
    if not questions:
        try:
            qs += [q["q"] for q in load_questions()]
        except Exception:
            pass
        seen: Dict[str, int] = {}
        for r in pipe.store.queries(500):
            k = _norm_q(r["query"])
            seen[k] = seen.get(k, 0) + 1
        top = sorted(seen.items(), key=lambda kv: -kv[1])[:from_log]
        norm_existing = {_norm_q(q) for q in qs}
        for k, _ in top:
            if k not in norm_existing:
                qs.append(k)
    saved = pipe.s.toggles.precompute
    pipe.s.toggles.precompute = True
    done = skipped = 0
    t0 = time.perf_counter()
    try:
        for i, q in enumerate(dict.fromkeys(qs)):
            key = cache_key(q, pipe.store.build_version(), pipe.answer_signature())
            if get_cached(pipe.store, key):
                skipped += 1
                continue
            res, _ = pipe.query(q, log=False)
            if not res.get("precomputed"):
                put_cached(pipe.store, key, q, res, source="precompute")
            done += 1
            if progress:
                progress("precompute %d/%d: %s" % (i + 1, len(qs), q[:50]))
    finally:
        pipe.s.toggles.precompute = saved
    return {"questions": len(qs), "computed": done, "skipped_cached": skipped, "ms": round((time.perf_counter() - t0) * 1000), "cache": cache_status(pipe.store)}


# ---------------------------------------------------------------- doc vectors
def doc_card(doc_id: str, title: str, meta: Dict[str, Any], chunks: List[Any]) -> str:
    heads: List[str] = []
    first = ""
    for c in chunks:
        if not first:
            first = (c["text"] or "")[:400]
        for h in _HEAD.findall(c["text"] or ""):
            if h not in heads:
                heads.append(h.strip())
        for part in (c["heading"] or "").split(" > "):
            if part and part not in heads:
                heads.append(part.strip())
    parts = ["# %s" % title]
    if meta:
        bits = [meta.get("doc_type") or "", meta.get("ext_id") or "", " ".join(meta.get("tags") or []), " ".join(meta.get("modules") or []),
                meta.get("summary") or "", meta.get("status") or ""]
        parts.append(" ".join(b for b in bits if b))
    parts.append("섹션: " + " / ".join(heads[:20]))
    parts.append(first)
    return "\n".join(parts)


def build_doc_vectors(pipe, doc_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    st = pipe.store
    emb = pipe.embedder
    docs = st.list_docs()
    if doc_ids is not None:
        want = set(doc_ids)
        docs = [d for d in docs if d["doc_id"] in want]
    metas = {r["doc_id"]: st._meta_row(r) for r in st.conn.execute("SELECT * FROM doc_meta")}
    cards: List[Tuple[str, str]] = []
    for d in docs:
        if d["doc_id"].startswith("wiki/"):
            continue
        chunks = st.all_chunks(d["doc_id"])
        if not chunks:
            continue
        cards.append((d["doc_id"], doc_card(d["doc_id"], d["title"], metas.get(d["doc_id"]) or {}, chunks)))
    n = 0
    for i in range(0, len(cards), max(1, int(pipe.s.embed_batch or 64))):
        part = cards[i:i + max(1, int(pipe.s.embed_batch or 64))]
        vecs = emb.embed([c for _, c in part])
        for j, (doc_id, card) in enumerate(part):
            v = np.asarray(vecs[j], dtype=np.float16 if pipe.s.embed_store_dtype == "float16" else np.float32)
            st.conn.execute("INSERT OR REPLACE INTO doc_vectors(doc_id,provider,dim,vec,card) VALUES(?,?,?,?,?)",
                            (doc_id, emb.name, int(v.shape[0]), v.tobytes(), card[:2000]))
            n += 1
    st.conn.commit()
    st._docvec_cache = None
    return {"doc_vectors": n, "provider": emb.name}


def doc_vector_matrix(store, provider: str):
    cache = getattr(store, "_docvec_cache", None)
    if cache and cache[0] == store.build_version() and cache[1] == provider:
        return cache[2], cache[3]
    rows = store.conn.execute("SELECT doc_id, dim, vec FROM doc_vectors WHERE provider=?", (provider,)).fetchall()
    if not rows:
        store._docvec_cache = (store.build_version(), provider, [], np.zeros((0, 1), dtype=np.float32))
        return [], store._docvec_cache[3]
    dim = rows[0]["dim"]
    ids = [r["doc_id"] for r in rows if r["dim"] == dim]
    mat = np.vstack([store._decode_vec(r["vec"], dim).astype(np.float32) for r in rows if r["dim"] == dim])
    store._docvec_cache = (store.build_version(), provider, ids, mat)
    return ids, mat


def doc_vector_search(store, embedder, query: str, k: int, prof) -> List[Tuple[str, float]]:
    """문서 카드 유사도 → 문서 대표 청크(질의 키워드 최다 청크, 없으면 첫 청크) 목록."""
    from .retrieval import matmul_sims
    with prof.stage("doc_vector_search", k=k, provider=embedder.name) as st:
        ids, mat = doc_vector_matrix(store, embedder.name)
        if not ids:
            st.note(hits=0, reason="no doc vectors (build with doc_vector toggle)")
            return []
        qv = embedder.embed([query])[0]
        if qv.shape[0] != mat.shape[1]:
            st.note(hits=0, reason="dim mismatch")
            return []
        sims = matmul_sims(mat, qv)
        top = np.argsort(-sims)[:k]
        kws = keywords(query)
        out: List[Tuple[str, float]] = []
        for i in top:
            if sims[i] <= 0:
                continue
            chunks = store.all_chunks(ids[i])
            if not chunks:
                continue
            best = chunks[0]
            best_sc = -1
            for c in chunks:
                low = (c["heading"] + " " + c["text"]).lower()
                sc = sum(1 for kw in kws if kw in low)
                if sc > best_sc:
                    best, best_sc = c, sc
            out.append((best["chunk_id"], float(sims[i])))
        st.note(hits=len(out), top=[(c, round(s, 3)) for c, s in out[:5]], n_docs=len(ids))
        return out
