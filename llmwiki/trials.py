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

#: 정답(기대 문서·용어)이 있어야만 계산되는 지표 — 실제 질의 이력으로 돌린 trial 에는 없다.
#: 화면이 빈칸을 "0점" 으로 오해하지 않게 **왜 없는지**를 함께 말하기 위해 따로 둔다 (2026-09-20).
NEEDS_GROUND_TRUTH = ("hit@k", "mrr", "term_recall", "answer_term_recall")

#: 단계별로 모으는 값. 지금까지 trial 은 **파이프라인 최종 결과** 12개만 저장해서,
#: "rerank 를 켜서 느려진 만큼 값어치를 했나" 같은 물음에 답할 수 없었다. 각 문항의 요청 trace 에
#: 단계별 ms·토큰·호출수가 이미 있으므로(profiler), 그것을 문항 전체에 걸쳐 합쳐 둔다 (2026-09-20).
STAGE_FIELDS = ("ms", "llm_calls", "llm_input_tokens", "llm_output_tokens", "sql", "embed_calls")
#: 단계 지표는 **낮을수록 좋다** (시간·비용). 품질은 위 METRICS 가 본다.
STAGE_LOWER_BETTER = True


#: 채점에 쓰이는 정답 키. **`evalset.score_result()` 가 실제로 읽는 이름**과 같아야 한다 —
#: 다른 이름을 적어 두면 평가셋 문항까지 '채점 불가' 로 가려져 hit@k 가 통째로 사라진다
#: (2026-09-20 에 `docs`/`terms` 로 잘못 적어 실제로 그렇게 됐다).
GROUND_TRUTH_KEYS = ("expect_docs", "expect_terms")


def _has_ground_truth(questions) -> bool:
    """문항에 채점할 **정답**(기대 문서 또는 기대 용어)이 하나라도 있는가.

    평가셋(`eval/questions.json`)의 문항은 `expect_docs`/`expect_terms` 를 갖는다. 실제 질의 이력에서
    뽑은 문항에는 없다 — 사람이 물어본 말만 있기 때문이다. 이 구분이 hit@k 를 **0 으로 볼지 공백으로
    볼지**를 가른다 (§run_trial 주석).
    """
    for q in (questions or []):
        if isinstance(q, dict) and any(q.get(k) for k in GROUND_TRUTH_KEYS):
            return True
    return False


def _walk_stages(node, out, depth=0):
    """trace 트리를 훑어 단계 이름별로 ms·counters 를 합친다. 같은 이름이 여러 번 돌면(fts_search_alt) 합산한다."""
    if not isinstance(node, dict):
        return
    name = str(node.get("name") or "")
    if depth > 0 and name:
        d = out.setdefault(name, {"n": 0, "ms": 0.0, "llm_calls": 0, "llm_input_tokens": 0,
                                  "llm_output_tokens": 0, "sql": 0, "embed_calls": 0})
        d["n"] += 1
        d["ms"] += float(node.get("ms") or 0)
        for k, v in (node.get("counters") or {}).items():
            if k in d:
                d[k] += int(v or 0)
    for ch in (node.get("children") or []):
        _walk_stages(ch, out, depth + 1)


def collect_stages(store, rows) -> Dict[str, Dict[str, Any]]:
    """문항들의 요청 trace → 단계별 합계/평균. 요청이 없는 문항은 건너뛴다."""
    agg: Dict[str, Dict[str, Any]] = {}
    n_req = 0
    for row in rows:
        rid = row.get("request_id")
        if not rid:
            continue
        try:
            req = store.get_request(int(rid))
        except Exception:
            continue
        tr = (req or {}).get("trace")
        if not isinstance(tr, dict):
            continue
        n_req += 1
        _walk_stages(tr, agg)
    for name, d in agg.items():
        d["ms"] = round(d["ms"], 1)
        d["avg_ms"] = round(d["ms"] / max(1, n_req), 1)      # 질의 한 건당
        d["runs_per_query"] = round(d["n"] / max(1, n_req), 2)
        d["tokens"] = d["llm_input_tokens"] + d["llm_output_tokens"]
    return {"n_requests": n_req, "stages": agg}


def _qhash(questions: List[Dict[str, Any]]) -> str:
    return hashlib.sha1(json.dumps([q.get("q") for q in questions], ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def snapshot_config(pipe, preset: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    s = pipe.s
    return {"settings": {k: v for k, v in s.to_dict().items() if k not in ("toggles", "corpus_dirs")}, "toggles": dict(s.toggles.__dict__),
            "tuning": _tuning.T.to_dict(), "preset": preset or "", "overrides": overrides or {},
            "providers": {"answer": s.role_llm("answer"), "rerank": s.role_llm("rerank"), "embed": s.embed_provider, "embed_model": s.embed_model}}


def run_trial(pipe, name: str, questions: Optional[List[Dict[str, Any]]] = None, k: int = 5, preset: Optional[str] = None,
              overrides: Optional[Dict[str, Any]] = None, note: str = "", progress=None,
              source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`source` 는 문항을 어디서 가져왔는지 (`{"kind": "evalset"|"queries"|"list", …}`) — 저장해 두고 비교 화면이 보여 준다.

    실제 질의 이력(`kind="queries"`)으로 돌리면 **정답(기대 문서·용어)이 없으므로** hit@k·term_recall 은
    계산되지 않는다. 그 대신 지연·토큰·근거 부족률·단계별 비용을 **실제로 사람들이 묻는 질문** 위에서 비교한다.
    """
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
        # 단계별 집계 (2026-09-20) — 각 문항의 요청 trace 에 이미 있는 값을 문항 전체로 합친다.
        # summary 안에 두면 저장 형식을 바꾸지 않고도 compare 가 쓸 수 있다 (예전 trial 에는 없으므로 compare 가 건너뛴다).
        try:
            summ["_stages"] = collect_stages(pipe.store, rows)
        except Exception as e:
            summ["_stages"] = {"error": str(e)[:120], "n_requests": 0, "stages": {}}
        summ["_source"] = dict(source or {"kind": "evalset"}, n=len(qs))
        # **정답이 없는 문항이면 정답이 필요한 지표를 0 이 아니라 None 으로 남긴다** (2026-09-20).
        #
        # 왜: `evaluate()` 는 기대 문서가 없으면 "못 맞혔다" 로 세어 hit@k 가 0.0 이 된다. 실제 질의
        # 이력으로 돌린 trial 이 목록에서 `hit@k 0.000` 으로 보이면 **완전 실패한 설정처럼 읽힌다** —
        # 실제로는 채점할 정답이 없었을 뿐이다. 비교 화면만 '계산 불가' 라고 적고 목록은 0 을 보여 주면
        # 두 화면이 서로 다른 말을 하게 된다. 그래서 **저장할 때** 한 번만 정리한다.
        if not _has_ground_truth(qs):
            for _m in NEEDS_GROUND_TRUTH:
                summ[_m] = None
            summ["_no_ground_truth"] = True
        cur = pipe.store.conn.execute("INSERT INTO trials(name,ts,build_version,config,questions_hash,summary,rows,note,request_id) VALUES(?,?,?,?,?,?,?,?,?)",
                                      (name, time.time(), pipe.store.build_version(), json.dumps(cfg_snap, ensure_ascii=False, default=str), _qhash(qs),
                                       json.dumps(summ, ensure_ascii=False), json.dumps(rows, ensure_ascii=False, default=str), note, r.get("request_id")))
        pipe.store.conn.commit()
        return {"trial_id": int(cur.lastrowid), "name": name, "summary": summ, "n": len(rows), "questions_hash": _qhash(qs),
                "request_id": r.get("request_id"), "source": summ["_source"]}
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


def _mask_ungraded(summ: Dict[str, Any]) -> Dict[str, Any]:
    """정답 없이 돌린 trial 의 '정답이 필요한 지표' 를 공백으로 만든다 (읽을 때 한 번 더).

    2026-09-20 이전에 저장된 trial 에는 0.0 이 박혀 있다. 그것을 목록에서 그대로 보여 주면
    **완전 실패한 설정처럼 읽히고**, 비교 화면은 '계산 불가' 라고 말하므로 두 화면이 어긋난다.
    저장 시점(run_trial)과 읽는 시점 양쪽에서 같은 규칙을 적용해 어느 화면에서 보든 같게 만든다.
    """
    src = (summ.get("_source") or {}).get("kind", "evalset")
    if summ.get("_no_ground_truth") or src == "queries":
        summ = dict(summ)
        for m in NEEDS_GROUND_TRUTH:
            summ[m] = None
        summ["_no_ground_truth"] = True
    return summ


def list_trials(store, limit: int = 50) -> List[Dict[str, Any]]:
    out = []
    for r in store.conn.execute("SELECT trial_id, name, ts, build_version, questions_hash, summary, note FROM trials ORDER BY trial_id DESC LIMIT ?", (limit,)):
        d = dict(r)
        try:
            d["summary"] = _mask_ungraded(json.loads(d["summary"] or "{}"))
        except Exception:
            d["summary"] = {}
        # 화면·CLI 가 "무엇으로 돌린 trial 인가" 를 한 칸으로 보여 줄 수 있게 꺼내 둔다.
        d["source"] = (d["summary"].get("_source") or {"kind": "evalset"})
        d["graded"] = not d["summary"].get("_no_ground_truth")
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
    if isinstance(d.get("summary"), dict):
        d["summary"] = _mask_ungraded(d["summary"])    # 목록·비교·상세가 같은 값을 본다
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
    # ---- 추천 (2026-09-19 개편) ----
    # 예전에는 지표 값이 제일 높은 것을 그냥 "최선" 이라고 했다. 문항이 25개면 hit@k 한 칸이 0.04 라서,
    # **한 문항이 뒤집힌 것**을 "더 좋다" 고 추천해 버린다. 그래서 두 가지를 더한다.
    #   (1) 유의성 — 질문별 승/패로 부호 검정을 해서 "우연과 구분되는가" 를 본다
    #   (2) 대가   — 품질이 올라도 토큰·지연이 얼마나 늘었는지 **같이** 말한다
    #              (품질만 보고 고르면 운영에서 감당 못 할 설정을 고르게 된다)
    n_q = len(per_q) or len(base["rows"]) or 1
    one_q = round(1.0 / n_q, 4)

    def val(i, m):
        row = next((r for r in metrics if r["metric"] == m), None)
        v = row["values"][i] if row else None
        return float(v) if isinstance(v, (int, float)) else None

    def cost_note(i):
        parts = []
        for m, unit, fmt_ in (("tokens_per_query", "토큰", "%+.0f%%"), ("p95_ms", "p95", "%+.0f%%"), ("avg_ms", "평균", "%+.0f%%")):
            b, x = val(0, m), val(i, m)
            if b and x is not None and b > 0:
                pct = (x - b) / b * 100.0
                if abs(pct) >= 5:
                    parts.append(("%s " + fmt_) % (unit, pct))
        return " · ".join(parts)

    rec = []
    for j in range(1, len(trials)):
        w = wins.get(trials[j]["name"], {})
        win, loss = int(w.get("win", 0)), int(w.get("loss", 0))
        sig = _sign_test(win, loss)
        dh = val(j, "hit@k") - val(0, "hit@k") if (val(j, "hit@k") is not None and val(0, "hit@k") is not None) else None
        cost = cost_note(j)
        if dh is None:
            continue
        if not same_q:
            verdict = "질문셋이 달라 비교할 수 없습니다 (같은 평가셋으로 다시 돌리세요)"
        elif win + loss == 0:
            verdict = "순위가 바뀐 문항이 없습니다 — 검색 결과가 동일합니다"
        elif not sig["significant"]:
            verdict = ("차이를 확인할 수 없습니다 (승 %d · 패 %d · 무 %d, p≈%.2f). "
                       "문항 %d개에서는 %s 이하의 차이가 우연과 구분되지 않습니다"
                       % (win, loss, int(w.get("tie", 0)), sig["p"], n_q, one_q))
        else:
            verdict = "%s (승 %d · 패 %d, p≈%.2f)" % ("더 좋습니다" if win > loss else "더 나쁩니다", win, loss, sig["p"])
        rec.append({"trial": trials[j]["name"], "hit@k_delta": round(dh, 4), "one_question": one_q,
                    "win": win, "loss": loss, "tie": int(w.get("tie", 0)), "p": sig["p"],
                    "significant": sig["significant"], "cost": cost,
                    "text": "%s: %s%s" % (trials[j]["name"], verdict, (" · 대가: " + cost) if cost else "")})
    # ---- 단계별 비교 (2026-09-20) ----
    # "품질이 올랐는데 어느 단계가 그 값을 치렀나" 를 보려면 최종 지표만으로는 부족하다.
    # 각 trial 이 저장해 둔 단계 집계를 나란히 놓고, 기준 대비 차이를 낸다.
    # 예전 trial 에는 `_stages` 가 없다 — 그럴 때는 빈 목록을 주고 화면이 이유를 말한다.
    stage_rows: List[Dict[str, Any]] = []
    stage_have = [bool(((t["summary"] or {}).get("_stages") or {}).get("stages")) for t in trials]
    if all(stage_have):
        names = []
        for t in trials:
            for nm in ((t["summary"]["_stages"]).get("stages") or {}):
                if nm not in names:
                    names.append(nm)
        for nm in names:
            cells = []
            for t in trials:
                st = ((t["summary"]["_stages"]).get("stages") or {}).get(nm)
                cells.append({"avg_ms": (st or {}).get("avg_ms"), "tokens": (st or {}).get("tokens"),
                              "llm_calls": (st or {}).get("llm_calls"), "runs_per_query": (st or {}).get("runs_per_query"),
                              "ran": st is not None})
            base_ms = cells[0]["avg_ms"] if cells[0]["ran"] else None
            d_ms = [None] + [None if (not c["ran"] or base_ms is None) else round(float(c["avg_ms"]) - float(base_ms), 1)
                             for c in cells[1:]]
            base_tk = cells[0]["tokens"] if cells[0]["ran"] else None
            d_tk = [None] + [None if (not c["ran"] or base_tk is None) else int(c["tokens"]) - int(base_tk)
                             for c in cells[1:]]
            # 이 단계가 이번 비교에서 의미 있게 달라졌는가 (질의당 5ms 또는 토큰 50 이상)
            moved = any(x is not None and abs(x) >= 5 for x in d_ms[1:]) or any(x is not None and abs(x) >= 50 for x in d_tk[1:]) \
                or any(c["ran"] != cells[0]["ran"] for c in cells[1:])
            stage_rows.append({"stage": nm, "cells": cells, "delta_ms": d_ms, "delta_tokens": d_tk, "changed": moved})
        stage_rows.sort(key=lambda r: (not r["changed"], -max([abs(x) for x in r["delta_ms"][1:] if x is not None] or [0])))
    stages = {"available": all(stage_have), "rows": stage_rows,
              "note": "" if all(stage_have) else
                      "이 비교에는 단계별 기록이 없는 trial 이 있습니다 (2026-09-20 이전 실행). 다시 돌리면 채워집니다.",
              "lower_is_better": STAGE_LOWER_BETTER}

    # 정답이 없는 원천(실제 질의 이력)으로 돌린 trial 이면, 빈 지표를 '0점' 으로 오해하지 않게 알려 준다.
    # 판정 근거 둘: ① 원천이 queries ② 저장할 때 남긴 `_no_ground_truth`(2026-09-20 이후 실행).
    sources = [((t["summary"] or {}).get("_source") or {"kind": "evalset"}) for t in trials]
    no_gt = [s for i, s in enumerate(sources)
             if s.get("kind") == "queries" or (trials[i]["summary"] or {}).get("_no_ground_truth")]
    unavailable = list(NEEDS_GROUND_TRUTH) if no_gt else []
    # **원천이 섞였는가** — 평가셋 trial 과 질의 이력 trial 을 나란히 놓으면 비교 가능한 지표가
    # 한쪽에만 있다. 숫자를 보여 주기 전에 그것부터 말해 준다.
    mixed = len({s.get("kind", "evalset") for s in sources}) > 1
    return {"trials": [{"trial_id": t["trial_id"], "name": t["name"], "ts": t["ts"], "build_version": t["build_version"],
                        "n": len(t["rows"]), "note": t.get("note"),
                        "source": ((t["summary"] or {}).get("_source") or {"kind": "evalset"})} for t in trials],
            "same_questions": same_q, "metrics": metrics, "per_question": per_q, "config_diff": diff, "wins": wins,
            "recommendation": rec, "n_questions": n_q, "one_question": one_q, "missing": missing,
            "stages": stages, "sources": sources, "unavailable_metrics": unavailable,
            "mixed_sources": mixed,
            "mixed_why": ("문항 원천이 서로 다른 trial 을 함께 놓았습니다 — 문항 자체가 다르므로 "
                          "품질 지표를 같은 잣대로 비교할 수 없습니다. 같은 원천끼리 비교하세요") if mixed else "",
            "comparable_metrics": [m for m in METRICS if m not in unavailable],
            "unavailable_why": ("실제 질의 이력으로 돌린 trial 이라 정답(기대 문서·용어)이 없습니다 — "
                                "빈칸은 '0점' 이 아니라 '계산할 수 없음' 입니다. "
                                "정답 없이도 읽을 수 있는 지표(groundedness · citation_precision · "
                                "insufficient_rate · 지연 · 토큰)로 판단하세요") if unavailable else ""}


def _sign_test(win: int, loss: int) -> Dict[str, Any]:
    """부호 검정 — 승/패가 **동전 던지기로 설명되는가**. 표준 라이브러리만으로 정확 이항 검정을 한다.

    무승부는 세지 않는다(부호 검정의 관례). p 는 양측이며, 0.05 미만일 때만 '차이가 있다' 고 말한다.
    문항이 적으면 어지간한 차이로는 절대 유의하지 않게 나오는데, 그게 맞다 — 25문항에서 1~2문항 차이는
    실제로 우연과 구분할 수 없다.
    """
    from math import comb
    n = win + loss
    if n == 0:
        return {"p": 1.0, "significant": False, "n": 0}
    k = min(win, loss)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / float(2 ** n)
    p = min(1.0, 2.0 * tail)
    return {"p": round(p, 4), "significant": p < 0.05, "n": n}


def report_md(cmp: Dict[str, Any]) -> str:
    if cmp.get("error"):
        return "error: %s" % cmp["error"]
    names = [t["name"] for t in cmp["trials"]]
    lines = ["# Trial 비교: " + " vs ".join(names), ""]
    # 문항을 어디서 가져왔는지 — 평가셋인지 실제 질의 이력인지에 따라 읽는 법이 달라진다
    srcs = cmp.get("sources") or []
    if srcs:
        lines += ["| trial | 문항 원천 | 문항 수 |", "|---|---|---|"]
        for t, s in zip(cmp["trials"], srcs):
            kind = {"evalset": "평가셋", "queries": "실제 질의 이력", "list": "직접 고른 문항"}.get(s.get("kind"), s.get("kind"))
            extra = (" · 최근 %s일 · %s" % (s.get("days"), s.get("only"))) if s.get("kind") == "queries" else ""
            lines.append("| %s | %s%s | %s |" % (t["name"], kind, extra, s.get("n", t.get("n"))))
        lines.append("")
    if cmp.get("unavailable_metrics"):
        lines += ["> ⚠ %s" % cmp.get("unavailable_why"),
                  "> 계산할 수 없는 지표: %s" % ", ".join(cmp["unavailable_metrics"]), ""]
    lines += ["| 지표 | " + " | ".join(names) + " | Δ(vs %s) |" % names[0], "|---|" + "---|" * (len(names) + 1)]
    for m in cmp["metrics"]:
        vals = ["%s" % ("-" if v is None else v) for v in m["values"]]
        deltas = ", ".join("%+.3f" % d if isinstance(d, (int, float)) else "-" for d in (m.get("delta") or [])[1:]) or "-"
        mark = " ★" if m.get("best") not in (None, 0) else ""
        na = " (계산 불가)" if m["metric"] in (cmp.get("unavailable_metrics") or []) else ""
        lines.append("| %s%s%s | %s | %s |" % (m["metric"], mark, na, " | ".join(vals), deltas))
    # 단계별 — "품질이 올랐는데 어느 단계가 그 값을 치렀나"
    stg = cmp.get("stages") or {}
    if stg.get("available") and stg.get("rows"):
        changed = [r for r in stg["rows"] if r["changed"]]
        lines += ["", "## 단계별 (질의 1건당 · 낮을수록 좋다)"]
        if changed:
            lines += ["| 단계 | " + " | ".join("%s ms" % n for n in names) + " | Δms | Δ토큰 |", "|---|" + "---|" * (len(names) + 2)]
            for r in changed[:20]:
                cells = ["-" if not c["ran"] else str(c["avg_ms"]) for c in r["cells"]]
                dms = ", ".join("%+.1f" % d if isinstance(d, (int, float)) else "-" for d in r["delta_ms"][1:]) or "-"
                dtk = ", ".join("%+d" % d if isinstance(d, (int, float)) else "-" for d in r["delta_tokens"][1:]) or "-"
                lines.append("| %s | %s | %s | %s |" % (r["stage"], " | ".join(cells), dms, dtk))
            if len(stg["rows"]) > len(changed):
                lines.append("")
                lines.append("*달라지지 않은 단계 %d개는 생략했습니다.*" % (len(stg["rows"]) - len(changed)))
        else:
            lines.append("의미 있게 달라진 단계가 없습니다 (질의당 ±5ms · ±50토큰 기준).")
    elif stg.get("note"):
        lines += ["", "## 단계별", "*%s*" % stg["note"]]
    if cmp["wins"]:
        lines += ["", "## 질문별 승/패 (vs %s)" % names[0]] + ["- %s: win %d / loss %d / tie %d" % (n, w["win"], w["loss"], w["tie"]) for n, w in cmp["wins"].items()]
    if cmp["config_diff"]:
        lines += ["", "## 설정 차이"] + ["- %s: %s" % (d["key"], " → ".join(json.dumps(v, ensure_ascii=False) for v in d["values"])) for d in cmp["config_diff"][:40]]
    if cmp["recommendation"]:
        # 추천은 이제 dict 다 (유의성·대가 포함). 예전 문자열 목록도 그대로 받아 준다.
        lines += ["", "## 판정 (문항 %d개 · 한 문항 = %s)" % (cmp.get("n_questions", 0), cmp.get("one_question", "-"))]
        lines += ["- " + (r["text"] if isinstance(r, dict) else str(r)) for r in cmp["recommendation"]]
    return "\n".join(lines)
