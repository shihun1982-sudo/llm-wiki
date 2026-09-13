# -*- coding: utf-8 -*-
"""리랭크 전용 엔드포인트 (rerank_method=api).

요청 { "model", "query", "documents": [...], "top_n" } — Cohere / Jina / vLLM(/v1/rerank, /v2/rerank) / TEI 공통.
응답 cohere 스타일: {"results":[{"index":i,"relevance_score":s}]}   voyage 스타일: {"data":[{"index":i,"relevance_score":s}]}
설정: settings.rerank_url, rerank_model, rerank_api_style(cohere|voyage), .env RERANK_API_KEY
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple

from .providers import LLMError


def _headers() -> Dict[str, str]:
    h = {"content-type": "application/json", "accept": "application/json"}
    key = os.environ.get("RERANK_API_KEY", "") or os.environ.get("COHERE_API_KEY", "") or os.environ.get("JINA_API_KEY", "")
    if key:
        h["authorization"] = "Bearer " + key
    return h


def rerank_api(settings, query: str, docs: List[str], top_n: int = 0, timeout: int = 120) -> Tuple[List[Tuple[int, float]], Dict[str, Any]]:
    """returns ([(index, score)] 내림차순, meta)"""
    url = (settings.rerank_url or "").strip()
    if not url:
        raise LLMError("rerank_url 이 설정되지 않았습니다")
    body: Dict[str, Any] = {"query": query, "documents": docs}
    if settings.rerank_model:
        body["model"] = settings.rerank_model
    if top_n:
        body["top_n"] = int(top_n)
    if settings.rerank_api_style == "voyage":
        body["top_k"] = int(top_n) if top_n else len(docs)
    t0 = time.perf_counter()
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError("rerank HTTP %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:300]))
    except Exception as e:
        raise LLMError("rerank network: %s" % e)
    ms = (time.perf_counter() - t0) * 1000
    rows = data.get("results")
    if rows is None:
        rows = data.get("data")
    if rows is None:
        raise LLMError("rerank: unexpected response keys %s" % list(data.keys())[:10])
    out: List[Tuple[int, float]] = []
    for r in rows:
        try:
            idx = int(r.get("index"))
            sc = float(r.get("relevance_score", r.get("score", 0.0)))
        except Exception:
            continue
        if 0 <= idx < len(docs):
            out.append((idx, sc))
    out.sort(key=lambda x: -x[1])
    usage = data.get("usage") or data.get("meta", {}).get("billed_units") or {}
    return out, {"ms": ms, "model": data.get("model", settings.rerank_model), "usage": usage, "n": len(out)}


def ping_rerank_api(settings) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        out, meta = rerank_api(settings, "modem rx dma underrun", ["RX DMA underrun 발생 시 PHY 재시작 실패", "주간 보고 요약"], top_n=2, timeout=20)
        ok = bool(out)
        return {"ok": ok, "ms": (time.perf_counter() - t0) * 1000, "detail": "%s model=%s top=%s" % (settings.rerank_url, meta.get("model"), out[:1])}
    except Exception as e:
        return {"ok": False, "ms": (time.perf_counter() - t0) * 1000, "detail": str(e)[:300]}
