# -*- coding: utf-8 -*-
"""Trial — 튜닝/변경 전후 회귀 비교 시스템.

trial run --name A [--preset quality] [--set k=v …] : 현재(또는 임시 적용한) 설정으로 평가셋을 실행해 trials 테이블에 저장
  저장: 설정 스냅샷(settings + tuning + preset), build_version, 지표 요약, 질문별 행(request_id 포함), 질문셋 해시
trial compare A B [C D] : 지표 Δ, 질문별 승/패/동률, 설정 diff, 추천 요약
지표: hit@k, mrr, term_recall, answer_term_recall, groundedness, citation_precision, insufficient_rate, fallback_rate,
      avg_ms, p95_ms, tokens_per_query, embed_coverage
evolve 의 apply 전/후 평가도 trial 로 기록해 하나의 시스템으로 본다.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional

from . import tuning as _tuning

METRICS = ("hit@k", "mrr", "term_recall", "answer_term_recall", "groundedness", "citation_precision", "insufficient_rate", "fallback_rate",
           "avg_ms", "p95_ms", "tokens_per_query", "embed_coverage")
HIGHER_BETTER = {"hit@k": True, "mrr": True, "term_recall": True, "answer_term_recall": True, "groundedness": True, "citation_precision": True,
                 "insufficient_rate": False, "fallback_rate": False, "avg_ms": False, "p95_ms": False, "tokens_per_query": False, "embed_coverage": True}


def _qhash(questions: List[Dict[str, Any]]) -> str:
    return hashlib.sha1(json.dumps([q.get("q") for q in questions], ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def snapshot_config(pipe, preset: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    s = pipe.s
    return {"settings": {k: v for k, v in s.to_dict().items() if k not in ("toggles", "corpus_dirs")}, "toggles": dict(s.toggles.__dict__),
            "tuning": _tuning.T.to_dict(), "preset": preset or "", "overrides": overrides or {},
            "providers": {"answer": s.role_llm("answer"), "rerank": s.role_llm("rerank"), "embed": s.embed_provider, "embed_model": s.embed_model}}


def run_trial(pipe, name: str, questions: Optional[List[Dict[str, Any]]] = None, k: int = 5, preset: Optional[str] = None,
              overrides: Optional[Dict[str, Any]] = None, note: str = "", progress=None) -> Dict[str, Any]:
    from .evalset import load_questions
    from .config import apply_overrides, Settings
    qs = questions or load_questions()
    prev_settings = pipe.s.to_dict()
    prev_tuning = dict(_tuning.T.values)
    preset_prev = None
    try:
        # 프리셋/오버라이드는 메모리(T)에만 적용하고 끝나면 복원한다. reload_tuning 은 파일을 다시 읽으므로 먼저 호출.
        pipe.reload_tuning()
        _tuning.T.values = dict(prev_tuning)
        if preset:
            from . import presets as _presets
            pa = _presets.apply(pipe.s, _presets.parse_names(preset), save=False)
            preset_prev = pa["prev"]
        if overrides:
            tun = {k: v for k, v in overrides.items() if k in _tuning._INDEX and _tuning._INDEX[k]["source"] == "tuning"}
            cfg = {k: v for k, v in overrides.items() if k not in tun}
            for kk, vv in tun.items():
                _tuning.T.set(kk, vv)
            if cfg:
                apply_overrides(pipe.s, cfg)
        pipe._init_text_plugins()
        pipe._qcache.clear()
        cfg_snap = snapshot_config(pipe, preset, overrides)
        r, tr = pipe.evaluate(k=k, questions=qs, log=False)
        rows = r["rows"]
        summ = dict(r["summary"])
        # 확장 지표 (요청 결과에서)
        g = []
        cp = []
        insuf = fb = 0
        ms = []
        for row in rows:
            req = pipe.store.get_request(int(row["request_id"])) if row.get("request_id") else None
            res = (req or {}).get("result") or {}
            if res.get("groundedness") is not None:
                g.append(float(res["groundedness"]))
            if (res.get("claims") or {}).get("citation_precision") is not None:
                cp.append(float(res["claims"]["citation_precision"]))
            if res.get("answer_mode") == "insufficient":
                insuf += 1
            if res.get("fallback"):
                fb += 1
            ms.append(float(row["ms"]))
            row["answer_mode"] = res.get("answer_mode")
            row["groundedness"] = res.get("groundedness")
            row["verdict"] = ((res.get("evidence") or {}).get("verdict")) if res.get("evidence") else None
        n = max(1, len(rows))
        ms_sorted = sorted(ms)
        summ.update({"groundedness": round(sum(g) / len(g), 3) if g else None, "citation_precision": round(sum(cp) / len(cp), 3) if cp else None,
                     "insufficient_rate": round(insuf / n, 3), "fallback_rate": round(fb / n, 3),
                     "p95_ms": ms_sorted[int(len(ms_sorted) * 0.95)] if ms_sorted else None, "tokens_per_query": round(summ.get("total_tokens", 0) / n, 1),
                     "embed_coverage": pipe.store.embed_coverage(pipe.embedder.name)["coverage"]})
        cur = pipe.store.conn.execute("INSERT INTO trials(name,ts,build_version,config,questions_hash,summary,rows,note,request_id) VALUES(?,?,?,?,?,?,?,?,?)",
                                      (name, time.time(), pipe.store.build_version(), json.dumps(cfg_snap, ensure_ascii=False, default=str), _qhash(qs),
                                       json.dumps(summ, ensure_ascii=False), json.dumps(rows, ensure_ascii=False, default=str), note, r.get("request_id")))
        pipe.store.conn.commit()
        return {"trial_id": int(cur.lastrowid), "name": name, "summary": summ, "n": len(rows), "questions_hash": _qhash(qs), "request_id": r.get("request_id")}
    finally:
        if preset_prev is not None:
            from . import presets as _presets
            _presets.restore(pipe.s, preset_prev)
        ns = Settings.from_dict(prev_settings)
        for kk, vv in ns.to_dict().items():
            if kk != "toggles":
                setattr(pipe.s, kk, vv)
        pipe.s.toggles = ns.toggles
        pipe.reload_tuning()
        _tuning.T.values = dict(prev_tuning)
        pipe._init_text_plugins()


def list_trials(store, limit: int = 50) -> List[Dict[str, Any]]:
    out = []
    for r in store.conn.execute("SELECT trial_id, name, ts, build_version, questions_hash, summary, note FROM trials ORDER BY trial_id DESC LIMIT ?", (limit,)):
        d = dict(r)
        try:
            d["summary"] = json.loads(d["summary"] or "{}")
        except Exception:
            d["summary"] = {}
        out.append(d)
    return out


def get_trial(store, ref: Any) -> Optional[Dict[str, Any]]:
    """ref: trial_id 또는 name(최신)."""
    if isinstance(ref, int) or str(ref).isdigit():
        r = store.conn.execute("SELECT * FROM trials WHERE trial_id=?", (int(ref),)).fetchone()
    else:
        r = store.conn.execute("SELECT * FROM trials WHERE name=? ORDER BY trial_id DESC LIMIT 1", (str(ref),)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("config", "summary", "rows"):
        try:
            d[k] = json.loads(d[k] or "{}")
        except Exception:
            pass
    return d


def _flat_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (cfg.get("settings") or {}).items():
        out["settings." + k] = v
    for k, v in (cfg.get("toggles") or {}).items():
        out["toggles." + k] = v
    for k, v in (cfg.get("tuning") or {}).items():
        out["tuning." + k] = v
    out["preset"] = cfg.get("preset")
    out["providers"] = json.dumps(cfg.get("providers"), sort_keys=True, ensure_ascii=False)
    return out


def compare(store, refs: List[Any]) -> Dict[str, Any]:
    trials = [get_trial(store, r) for r in refs]
    missing = [r for r, t in zip(refs, trials) if not t]
    trials = [t for t in trials if t]
    if len(trials) < 2:
        return {"error": "need ≥2 trials", "missing": missing}
    base = trials[0]
    metrics = []
    for m in METRICS:
        row = {"metric": m, "higher_better": HIGHER_BETTER.get(m, True), "values": [t["summary"].get(m) for t in trials]}
        vals = [v for v in row["values"] if isinstance(v, (int, float))]
        if len(vals) >= 2 and isinstance(row["values"][0], (int, float)):
            row["delta"] = [None] + [round(float(v) - float(row["values"][0]), 4) if isinstance(v, (int, float)) else None for v in row["values"][1:]]
            best_i = max(range(len(row["values"])), key=lambda i: (row["values"][i] if isinstance(row["values"][i], (int, float)) else -1e9) * (1 if row["higher_better"] else -1))
            row["best"] = best_i
        metrics.append(row)
    # 질문별 승/패
    per_q: List[Dict[str, Any]] = []
    same_q = all(t["questions_hash"] == base["questions_hash"] for t in trials)
    if same_q:
        for i, brow in enumerate(base["rows"]):
            q = brow["q"]
            cells = []
            for t in trials:
                rr = t["rows"][i] if i < len(t["rows"]) else {}
                cells.append({"hit": rr.get("hit"), "rank": rr.get("rank"), "term_recall": rr.get("term_recall"), "groundedness": rr.get("groundedness"),
                              "answer_mode": rr.get("answer_mode"), "ms": rr.get("ms"), "request_id": rr.get("request_id")})
            outcome = []
            for c in cells[1:]:
                b, x = cells[0], c
                if (x["hit"] and not b["hit"]) or (x["hit"] and b["hit"] and (x["rank"] or 99) < (b["rank"] or 99)):
                    outcome.append("win")
                elif (b["hit"] and not x["hit"]) or (x["hit"] and b["hit"] and (x["rank"] or 99) > (b["rank"] or 99)):
                    outcome.append("loss")
                else:
                    outcome.append("tie")
            per_q.append({"q": q, "cells": cells, "outcome": outcome})
    # 설정 diff
    flats = [_flat_cfg(t["config"]) for t in trials]
    keys = sorted(set().union(*[set(f.keys()) for f in flats]))
    diff = [{"key": k, "values": [f.get(k) for f in flats]} for k in keys if len({json.dumps(f.get(k), sort_keys=True, ensure_ascii=False) for f in flats}) > 1]
    wins = {}
    for j in range(1, len(trials)):
        wins[trials[j]["name"]] = {"win": sum(1 for p in per_q if p["outcome"][j - 1] == "win"), "loss": sum(1 for p in per_q if p["outcome"][j - 1] == "loss"),
                                   "tie": sum(1 for p in per_q if p["outcome"][j - 1] == "tie")}
    # 추천
    rec = []
    for row in metrics:
        if "best" in row and row["best"] != 0 and row["metric"] in ("hit@k", "mrr", "groundedness"):
            rec.append("%s: %s 가 최선 (%s)" % (row["metric"], trials[row["best"]]["name"], row["values"][row["best"]]))
    return {"trials": [{"trial_id": t["trial_id"], "name": t["name"], "ts": t["ts"], "build_version": t["build_version"], "n": len(t["rows"]), "note": t.get("note")} for t in trials],
            "same_questions": same_q, "metrics": metrics, "per_question": per_q, "config_diff": diff, "wins": wins, "recommendation": rec, "missing": missing}


def report_md(cmp: Dict[str, Any]) -> str:
    if cmp.get("error"):
        return "error: %s" % cmp["error"]
    names = [t["name"] for t in cmp["trials"]]
    lines = ["# Trial 비교: " + " vs ".join(names), "", "| 지표 | " + " | ".join(names) + " | Δ(vs %s) |" % names[0], "|---|" + "---|" * (len(names) + 1)]
    for m in cmp["metrics"]:
        vals = ["%s" % ("-" if v is None else v) for v in m["values"]]
        deltas = ", ".join("%+.3f" % d if isinstance(d, (int, float)) else "-" for d in (m.get("delta") or [])[1:]) or "-"
        mark = " ★" if m.get("best") not in (None, 0) else ""
        lines.append("| %s%s | %s | %s |" % (m["metric"], mark, " | ".join(vals), deltas))
    if cmp["wins"]:
        lines += ["", "## 질문별 승/패 (vs %s)" % names[0]] + ["- %s: win %d / loss %d / tie %d" % (n, w["win"], w["loss"], w["tie"]) for n, w in cmp["wins"].items()]
    if cmp["config_diff"]:
        lines += ["", "## 설정 차이"] + ["- %s: %s" % (d["key"], " → ".join(json.dumps(v, ensure_ascii=False) for v in d["values"])) for d in cmp["config_diff"][:40]]
    if cmp["recommendation"]:
        lines += ["", "## 추천"] + ["- " + r for r in cmp["recommendation"]]
    return "\n".join(lines)
