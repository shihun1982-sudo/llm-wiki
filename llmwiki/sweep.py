"""파라미터 **스윕** — 저장된 질의를 `rerun` 재생 위에서 값만 바꿔 N회 돌리고 단계별로 비교한다.

왜 있나
------
"rrf_k 를 10, 30, 60 으로 두면 뭐가 달라지나" 를 보려고 질의를 세 번 처음부터 돌리면, LLM 확장·리랭크가
매번 미묘하게 달라져서 차이가 **내가 바꾼 값 때문인지 검색이 흔들려서인지** 알 수 없다. 그래서 값마다
`Pipeline.rerun(request_id, point)` 을 부른다 — 그 키가 영향을 주는 **첫 단계(재시작점) 앞은 저장값을 그대로
재생**하고 뒤만 다시 계산하므로, 값별 차이는 그 값의 효과로 분리된다. 엔진 로직은 하나도 복제하지 않는다
(`/api/query/rerun`·`rerun` CLI 와 같은 `request_scope(overrides) + pipe.rerun()` 경로).

무엇을 남기나
-------------
`<sweep_dir>/sw_<id>.json` 한 파일에 값별 실행 요약: ms · result_type · 답변 본문 · claim 수치 · 근거 판정 ·
고정 단계 목록(`STAGES`)의 {ms, replayed, skipped, meta 일부} · 순위 목록(융합/부스트/리랭크/최종/컨텍스트 id).
순위 목록은 각 재실행이 자기 체크포인트(`data/reruns/req_<new id>.json`)를 남기므로 거기서 읽는다 — trace 에는
순서가 남지 않기 때문이다. `compare()` 는 첫 값을 기준으로 값마다 단계별 diff 를 만든다 (difflib).

설정 (`config.json`)
--------------------
| 키 | 기본 | 뜻 |
|---|---|---|
| `sweep_dir` | `data/sweeps` | 결과 폴더 (`data/…` 는 data_dir 아래) |
| `sweep_keep` | `30` | 최근 몇 건을 남길지 |
| `sweep_max_values` | `20` | 한 스윕의 값 개수 상한 (LLM 호출 폭주 방지) |
| `sweep_max_parallel` | `1` | 값을 동시에 몇 개 돌릴지. 1 = 순차 (권장: 로컬/게이트웨이 LLM 한도) |
"""
from __future__ import annotations

import difflib
import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import rerun as _rr
from . import tuning as _tuning
from .config import Settings, Toggles, split_role_key, _to_bool

VERSION = 1
ORDER_TOP = 12          # 순위 목록에 남기는 상위 id 수 (기준 대비 diff 는 이 범위에서 본다)
ANSWER_DIFF_LINES = 40  # 답변 unified diff 최대 줄 수
MAX_REPEATS = 10

# 비교 격자의 열 — trace 단계 이름을 **가족**으로 묶는다 (재생되면 "rerank", 계산되면 "rerank_llm"/"rerank_local" 로 이름이 달라진다)
STAGES: List[str] = ["rrf_fuse", "boost", "channel_inject", "rerank", "doc_expand", "context", "evidence_check", "answer_llm", "claim_check"]
STAGE_FAMILY: Dict[str, Tuple[str, ...]] = {
    "rrf_fuse": ("rrf_fuse", "fusion_llm"),
    "boost": ("boost", "external_inject"),
    "channel_inject": ("channel_inject",),
    "rerank": ("rerank", "rerank_llm", "rerank_local", "rerank_cross_encoder", "rerank_api", "rerank_review_llm"),
    "doc_expand": ("doc_expand",),
    "context": ("context", "evidence_compress"),
    "evidence_check": ("evidence_check", "fallback"),
    "answer_llm": ("answer_llm", "answer", "answer_extractive", "answer_insufficient", "generate_answer"),
    "claim_check": ("claim_check", "answer_refine"),
}
# 단계 → 체크포인트에서 읽는 순위 목록 키 (없는 단계는 meta·본문·수치로만 비교)
STAGE_ORDER_KEY: Dict[str, str] = {"rrf_fuse": "fused", "boost": "boosted", "rerank": "reranked", "doc_expand": "final", "context": "context"}

# ---------------------------------------------------------------- 키 → 재시작점
# 튜닝 레지스트리의 stage → 재시작점. 빌드 단계(chunk_index/embed/graph_build)와 forensic 은 질의 경로에서 재현되지 않으므로 스윕 불가.
_TSTAGE_POINT: Dict[str, str] = {
    "time_scope": "plan", "query_rules": "plan", "router": "plan", "query_expand": "plan",
    "fts_search": "retrieve", "vector_search": "retrieve", "graph_search": "retrieve",
    "rrf_fuse": "rrf_fuse", "rerank": "rerank", "context": "context",
    # evidence/fallback 은 컨텍스트 뒤에서 판정한다. answer_llm 점에서는 ctx 가 재생되어 fallback 이 돌지 않으므로 context 부터.
    "evidence": "context", "answer": "answer_llm", "claim": "claim_check",
}
# 키 이름으로 더 정확히 좁힌다 (stage 표보다 우선). 앞 항목이 우선.
_KEY_PREFIX_POINT: List[Tuple[str, str]] = [
    ("doc_expand_", "doc_expand"),
    ("pin_boost", "boost"), ("doc_type_boost", "boost"), ("provenance_boost", "boost"), ("feedback_boost_w", "boost"),
    ("time_boost_w", "boost"), ("recency_half_life_days", "boost"),
    ("external_rag_", "retrieve"),
    ("fusion_llm_", "rerank"), ("post_rerank_llm_k", "rerank"),     # 두 LLM 단계는 '리랭크부터' 에서 다시 계산된다 (rerun.STAGE_POINT)
    ("refs_preview_chars", "answer_llm"),
]
# 토글 → 재시작점 (architecture.FLOWS 의 query 흐름에서 그 토글을 가진 첫 단계로 정하고, 표에 없는 것만 여기서)
_TOGGLE_POINT: Dict[str, Optional[str]] = {
    "llm_after_fusion": "rerank", "llm_after_rerank": "rerank", "external_rag": "retrieve", "mcp_sources": "retrieve",
    "query_cache": "plan", "precompute": "plan", "profile_expansion": "plan",
    "evidence_check": "context", "evidence_check_llm": "context", "fallback_loop": "context",
    "llm_failure_report": "answer_llm", "degrade_on_llm_failure": "answer_llm",
    # 재현할 의미가 없는 것 (None = 거부)
    "rerun_capture": None, "analysis_mode": None, "forensic_auto": None, "evolve_capture": None, "evolve_auto_apply": None,
    "log_stages": None, "collab": None, "auto_build": None, "health_check": None, "precompute_after_build": None,
    "mcp_federation": None, "memory_decay": None, "evolve_from_forensics": None,
}
# 역할 LLM 단축키(<role>_model 등) → 재시작점. 빌드/리뷰 역할은 질의 경로에 없다.
_ROLE_POINT: Dict[str, Optional[str]] = {"answer": "answer_llm", "verify": "answer_llm", "rerank": "rerank", "fusion": "rerank", "select": "rerank",
                                         "expand": "plan", "router": "plan", "extract": None, "summary": None, "review": None, "forensic": None}
# 튜닝 레지스트리에 없는 config.json 키 → 재시작점 (없으면 'plan' = 전체 재실행)
_CONFIG_POINT: Dict[str, str] = {"answer_mode": "answer_llm", "output_mode": "plan", "debug_level": "plan", "rerank_url": "rerank", "rerank_api_model": "rerank",
                                 "graph_hops": "retrieve"}
_ROLE_NUMERIC_ATTRS = ("timeout_s", "retries", "backoff_s", "backoff_max_s", "budget_s", "circuit_failures", "circuit_cooldown_s")


_FLOW_TOGGLE_POINTS: Dict[str, str] = {}


def _query_flow_toggle_points() -> Dict[str, str]:
    """architecture.FLOWS['query'] 를 훑어 토글 → 그 토글을 가진 **첫** 단계의 재시작점 (한 번만 계산)."""
    if _FLOW_TOGGLE_POINTS:
        return _FLOW_TOGGLE_POINTS
    out = _FLOW_TOGGLE_POINTS
    try:
        from .architecture import FLOWS
        stages = FLOWS.get("query", {}).get("stages") or []
    except Exception:
        return out
    for st in stages:
        pt = None
        for tr in (st.get("trace") or [st.get("key")]):
            pt = _rr.point_for_stage(str(tr))
            if pt:
                break
        if not pt:
            continue
        for tg in st.get("toggles") or []:
            out.setdefault(tg, pt)
    return out


def classify_key(key: str) -> Dict[str, Any]:
    """스윕 키 하나를 해석한다 → {key, input, kind(toggle|tuning|config|role), type, min, max, choices, default, stage, point, desc}.

    받는 표기: `rrf_k` · `tuning.rrf_k` · `toggles.rerank` · `rerank` · `answer_model` · `llm_roles.answer.effort`.
    모르는 키·스윕할 수 없는 키는 ValueError (이유 포함).
    """
    raw = str(key or "").strip()
    if not raw:
        raise ValueError("key 가 비었습니다")
    name = raw
    if name.startswith("toggles."):
        name = name[len("toggles."):]
    elif name.startswith("tuning."):
        name = name[len("tuning."):]
    elif name.startswith("llm_roles."):
        parts = name.split(".")
        if len(parts) != 3:
            raise ValueError("llm_roles 키는 llm_roles.<role>.<attr> 형태여야 합니다 (받은 값: %s)" % raw)
        name = "%s_%s" % (parts[1], parts[2])
    # 1) 토글
    if name in Toggles.__dataclass_fields__:
        pt = _TOGGLE_POINT.get(name, _query_flow_toggle_points().get(name))
        if name in _TOGGLE_POINT and _TOGGLE_POINT[name] is None:
            raise ValueError("토글 %s 는 질의 결과를 바꾸지 않아 스윕할 수 없습니다" % name)
        if not pt:
            raise ValueError("토글 %s 는 빌드/운영용이라 질의 재실행으로 스윕할 수 없습니다" % name)
        from .config import TOGGLE_HELP
        return {"key": name, "input": raw, "kind": "toggle", "type": "bool", "min": None, "max": None, "choices": [False, True],
                "default": getattr(Toggles(), name), "stage": "", "point": pt, "desc": str(TOGGLE_HELP.get(name, ""))[:200]}
    # 2) 튜닝 레지스트리 (source=tuning 은 tuning.json, source=config 는 config.json 값)
    if name in _tuning._INDEX:
        sp = _tuning._INDEX[name]
        pt = None
        for pre, p in _KEY_PREFIX_POINT:
            if name.startswith(pre):
                pt = p
                break
        pt = pt or _TSTAGE_POINT.get(sp["stage"])
        if not pt:
            raise ValueError("%s 는 %s 단계(빌드/포렌식) 값이라 질의 재실행으로 스윕할 수 없습니다 — build 뒤 eval/trial 로 비교하세요" % (name, sp["stage"]))
        if sp.get("rebuild"):
            raise ValueError("%s 는 전체 리빌드가 필요한 값이라 스윕할 수 없습니다" % name)
        return {"key": name, "input": raw, "kind": "tuning" if sp["source"] == "tuning" else "config", "type": sp["type"],
                "min": sp["min"], "max": sp["max"], "choices": sp["choices"], "default": sp["default"], "stage": sp["stage"], "point": pt,
                "desc": str(sp.get("desc") or "")[:200]}
    # 3) 역할 LLM 단축키
    rk = split_role_key(name)
    if rk:
        role, attr = rk
        pt = _ROLE_POINT.get(role)
        if pt is None:
            raise ValueError("역할 %s 는 질의 경로에 없어 스윕할 수 없습니다 (answer/rerank/verify/expand/fusion/select 만)" % role)
        typ = "float" if attr in _ROLE_NUMERIC_ATTRS else ("choice" if attr == "effort" else "str")
        return {"key": name, "input": raw, "kind": "role", "type": typ, "min": None, "max": None,
                "choices": ["low", "medium", "high"] if attr == "effort" else None, "default": None, "stage": role, "point": pt,
                "desc": "역할 %s 의 %s (llm_roles.%s.%s)" % (role, attr, role, attr)}
    # 4) 그 밖의 config.json 스칼라 키 (URL·경로도 해석은 되지만 Web 에서는 역할 화이트리스트가 막는다)
    if name in Settings.__dataclass_fields__ and name != "toggles":
        cur = getattr(Settings(), name)
        if isinstance(cur, bool):
            typ = "bool"
        elif isinstance(cur, int):
            typ = "int"
        elif isinstance(cur, float):
            typ = "float"
        elif isinstance(cur, str):
            typ = "str"
        else:
            raise ValueError("%s 는 목록/객체 설정이라 스윕할 수 없습니다" % name)
        from .config import SETTING_HELP
        return {"key": name, "input": raw, "kind": "config", "type": typ, "min": None, "max": None,
                "choices": [False, True] if typ == "bool" else None, "default": cur, "stage": "", "point": _CONFIG_POINT.get(name, "plan"),
                "desc": str(SETTING_HELP.get(name, ""))[:200]}
    raise ValueError("모르는 키: %s — 튜닝 키(rrf_k…)·토글(rerank, toggles.claim_check)·config 키(top_k_final)·역할 키(answer_model) 중 하나. 목록: sweep keys" % raw)


def point_for_key(key: str, from_point: Optional[str] = None) -> str:
    """이 키를 바꿨을 때 **다시 계산해야 하는 첫** 재시작점. `from_point` 를 주면 (검증 후) 그것을 쓴다."""
    if from_point:
        if from_point not in _rr.POINT_IDS:
            raise ValueError("재시작점은 %s 중 하나여야 합니다 (받은 값: %s)" % (", ".join(_rr.POINT_IDS), from_point))
        return from_point
    return classify_key(key)["point"]


def coerce_value(info: Dict[str, Any], v: Any) -> Any:
    """키 종류에 맞게 값을 바꾼다 (범위·choices 검사 포함)."""
    kind, typ, name = info["kind"], info["type"], info["key"]
    if kind == "toggle":
        return _to_bool(v)
    if name in _tuning._INDEX:
        return _tuning.coerce(name, v)
    if typ == "bool":
        return _to_bool(v)
    if typ == "int":
        return int(float(v))
    if typ == "float":
        return float(v)
    if typ == "choice":
        s = str(v)
        if info.get("choices") and s not in info["choices"]:
            raise ValueError("%s must be one of %s" % (name, info["choices"]))
        return s
    return str(v)


def _parse_range(text: str) -> List[Any]:
    parts = [x.strip() for x in str(text).replace(",", ":").split(":") if x.strip() != ""]
    if len(parts) not in (2, 3):
        raise ValueError("range 는 start:stop:step 형태여야 합니다 (받은 값: %s)" % text)
    is_int = all(("." not in x and "e" not in x.lower()) for x in parts)
    try:
        a, b = float(parts[0]), float(parts[1])
        step = float(parts[2]) if len(parts) == 3 else 1.0
    except ValueError:
        raise ValueError("range 의 값이 숫자가 아닙니다: %s" % text)
    if step == 0:
        raise ValueError("range 의 step 은 0 일 수 없습니다")
    if (b - a) * step < 0:
        raise ValueError("range 의 방향이 맞지 않습니다 (start=%s stop=%s step=%s)" % (parts[0], parts[1], step))
    n = int((b - a) / step + 1e-9) + 1
    if n > 10000:
        raise ValueError("range 가 너무 큽니다 (%d개)" % n)
    out = [a + i * step for i in range(n)]
    return [int(round(x)) for x in out] if is_int else [round(x, 10) for x in out]


def resolve_values(key: str, spec: Any = None, max_values: int = 20) -> List[Any]:
    """값 목록을 만든다. spec: {"range": "10:100:10"} | {"values": [...]} | 문자열(range 또는 "a,b,c") | 목록 | None.
    bool 키에 spec 이 없으면 [False, True], choice 키면 choices 전부. 종류에 맞게 coerce 하고 상한(`max_values`)을 넘으면 거부."""
    info = classify_key(key)
    vals: List[Any]
    if spec is None or spec == {} or spec == "":
        if info["type"] == "bool":
            vals = [False, True]
        elif info["type"] == "choice" and info.get("choices"):
            vals = list(info["choices"])
        else:
            raise ValueError("%s 의 값 목록(values) 또는 범위(range) 가 필요합니다" % info["key"])
    elif isinstance(spec, dict):
        if spec.get("range") not in (None, ""):
            vals = _parse_range(str(spec["range"]))
        elif spec.get("values") is not None:
            v = spec["values"]
            vals = [x.strip() for x in str(v).split(",") if x.strip() != ""] if isinstance(v, str) else list(v)
        else:
            return resolve_values(key, None, max_values)
    elif isinstance(spec, (list, tuple)):
        vals = list(spec)
    else:
        text = str(spec)
        vals = _parse_range(text) if ":" in text else [x.strip() for x in text.split(",") if x.strip() != ""]
    if not vals:
        raise ValueError("값이 하나도 없습니다")
    coerced: List[Any] = []
    for v in vals:
        try:
            cv = coerce_value(info, v)
        except (ValueError, TypeError) as e:
            raise ValueError("%s=%r: %s" % (info["key"], v, e))
        if cv not in coerced:
            coerced.append(cv)
    if max_values and len(coerced) > int(max_values):
        raise ValueError("값이 %d개 — sweep_max_values=%d 를 넘습니다 (config.json 에서 올리거나 범위를 줄이세요)" % (len(coerced), int(max_values)))
    return coerced


def sweepable_keys(s: Any = None) -> List[Dict[str, Any]]:
    """화면의 폼이 쓸 목록: 질의 경로의 튜닝/설정 키 + 토글 + 역할 LLM 키, 각각 type/min/max/choices/stage/point."""
    out: List[Dict[str, Any]] = []
    names: List[str] = [p["key"] for p in _tuning.TUNABLES] + list(Toggles.__dataclass_fields__)
    names += ["answer_mode"]
    for role in ("answer", "rerank", "verify", "expand", "fusion", "select"):
        names += ["%s_model" % role, "%s_provider" % role, "%s_effort" % role]
    for n in names:
        try:
            info = classify_key(n)
        except ValueError:
            continue
        if s is not None and info["kind"] in ("config",) and hasattr(s, info["key"]):
            info["value"] = getattr(s, info["key"])
        elif info["kind"] == "toggle" and s is not None:
            info["value"] = getattr(s.toggles, info["key"], None)
        elif info["kind"] == "tuning":
            info["value"] = _tuning.T.get(info["key"])
        out.append(info)
    order = {p: i for i, p in enumerate(_rr.POINT_IDS)}
    out.sort(key=lambda d: (order.get(d["point"], 99), d["kind"], d["key"]))
    return out


# ---------------------------------------------------------------- 저장 · 조회
def sweep_dir(s: Any) -> str:
    fn = getattr(s, "sweep_record_dir", None)
    if callable(fn):
        d = fn()
        if d:
            return d
    return os.path.join(getattr(s, "data_dir", "data"), "sweeps")


def _norm_id(sid: Any) -> str:
    x = str(sid or "").strip()
    if x.startswith("sw_"):
        x = x[3:]
    if x.endswith(".json"):
        x = x[:-5]
    return x


def path_for(s: Any, sid: Any) -> str:
    return os.path.join(sweep_dir(s), "sw_%s.json" % _norm_id(sid))


def save(s: Any, record: Dict[str, Any]) -> str:
    d = sweep_dir(s)
    os.makedirs(d, exist_ok=True)
    p = path_for(s, record["id"])
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, default=str)
    os.replace(tmp, p)
    prune(s)
    return p


def load(s: Any, sid: Any) -> Optional[Dict[str, Any]]:
    try:
        with open(path_for(s, sid), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("version") == VERSION else None


def prune(s: Any) -> int:
    keep = int(getattr(s, "sweep_keep", 30) or 0)
    if keep <= 0:
        return 0
    d = sweep_dir(s)
    try:
        files = [(os.path.getmtime(os.path.join(d, n)), os.path.join(d, n)) for n in os.listdir(d) if n.startswith("sw_") and n.endswith(".json")]
    except OSError:
        return 0
    files.sort(reverse=True)
    n = 0
    for _, p in files[keep:]:
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
    return n


def list_sweeps(s: Any, limit: int = 50) -> List[Dict[str, Any]]:
    d = sweep_dir(s)
    out: List[Dict[str, Any]] = []
    try:
        names = [n for n in os.listdir(d) if n.startswith("sw_") and n.endswith(".json")]
    except OSError:
        return out
    rows = []
    for n in names:
        p = os.path.join(d, n)
        try:
            rows.append((os.path.getmtime(p), p, n))
        except OSError:
            pass
    rows.sort(reverse=True)
    for mtime, p, n in rows[:limit]:
        row: Dict[str, Any] = {"id": n[3:-5], "mtime": mtime, "bytes": os.path.getsize(p)}
        try:
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
            row.update({k: rec.get(k) for k in ("key", "kind", "point", "request_id", "query", "repeats", "created", "ms")})
            row["values"] = rec.get("values")
            row["n_runs"] = len(rec.get("runs") or [])
            row["n_errors"] = sum(1 for r in (rec.get("runs") or []) if r.get("error"))
        except (OSError, ValueError):
            row["error"] = "읽을 수 없음"
        out.append(row)
    return out


# ---------------------------------------------------------------- 실행
def _flat(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def walk(n: Dict[str, Any]) -> None:
        out.append(n)
        for c in n.get("children") or []:
            walk(c)
    for c in (trace or {}).get("children") or []:
        walk(c)
    return out


def _meta_subset(meta: Any, limit: int = 12) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(meta, dict):
        return out
    for k, v in meta.items():
        if k in ("replay_source",):
            continue
        if isinstance(v, (bool, int, float)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:80]
        elif isinstance(v, (list, dict)):
            out[k] = len(v)
        if len(out) >= limit:
            break
    return out


def stage_summary(trace: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """trace → STAGES 열마다 {present, ms, replayed, skipped, names, meta}."""
    flat = _flat(trace)
    out: Dict[str, Dict[str, Any]] = {}
    for st in STAGES:
        fam = STAGE_FAMILY[st]
        nodes = [n for n in flat if str(n.get("name")) in fam]
        if not nodes:
            out[st] = {"present": False, "ms": 0.0, "replayed": False, "skipped": False, "names": [], "meta": {}}
            continue
        meta: Dict[str, Any] = {}
        for n in nodes:
            meta.update(_meta_subset(n.get("meta")))
        out[st] = {"present": True, "ms": round(max(float(n.get("ms") or 0.0) for n in nodes), 2),
                   "replayed": all(bool(n.get("replayed")) for n in nodes),
                   "skipped": all(not n.get("enabled", True) for n in nodes),
                   "names": [str(n.get("name")) for n in nodes], "meta": meta}
    return out


def _orderings(res: Dict[str, Any], ck: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    if ck:
        for k in ("fused", "boosted", "reranked", "final"):
            lst = ck.get(k)
            if isinstance(lst, list):
                out[k] = [str(x.get("chunk_id")) for x in lst[:ORDER_TOP] if isinstance(x, dict)]
        ctx = ck.get("ctx")
        if isinstance(ctx, dict):
            out["context"] = [str(c.get("chunk_id")) for c in (ctx.get("citations") or []) if isinstance(c, dict)]
    hb = res.get("hits_brief") or []
    if "final" not in out:
        out["final"] = [str(h.get("chunk_id")) for h in hb if (h.get("why") or [""])[0] not in ("neighbor", "doc_expand")][:ORDER_TOP]
    if "context" not in out:
        out["context"] = [str(h.get("chunk_id")) for h in hb if h.get("in_context")]
    return out


def summarize_run(pipe, res: Dict[str, Any], tr: Dict[str, Any], value: Any, i: int, repeat: int, wall_ms: float) -> Dict[str, Any]:
    rid = res.get("request_id")
    ck = _rr.load(pipe.s, rid) if rid is not None else None
    claims = res.get("claims") if isinstance(res.get("claims"), dict) else None
    ev = res.get("evidence") if isinstance(res.get("evidence"), dict) else {}
    cited = res.get("cited") or []
    return {"i": i, "value": value, "repeat": repeat, "request_id": rid, "ms": res.get("ms"), "wall_ms": round(wall_ms, 1),
            "result_type": res.get("result_type") or res.get("answer_mode"), "answer_mode": res.get("answer_mode"),
            "answer": str(res.get("answer") or ""), "n_chars": len(str(res.get("answer") or "")),
            "cited": list(cited), "n_citations": len(cited), "groundedness": res.get("groundedness"),
            "claims": {k: v for k, v in (claims or {}).items() if isinstance(v, (int, float, str, bool)) or v is None} if claims else None,
            "verdict": ev.get("verdict"), "evidence_score": ev.get("score"),
            "tokens": (res.get("tokens") or {}).get("total_tokens"),
            "replayed": [str(n.get("name")) for n in _flat(tr) if n.get("replayed")],
            "stages": stage_summary(tr), "orders": _orderings(res, ck),
            "rerun": res.get("rerun"), "error": None}


def _llm_to_overrides(llm: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """{"answer": {"model": "…", "effort": "high"}} → {"answer_model": "…", "answer_effort": "high"} (역할 하나만 바꾸는 단축키 형태)."""
    out: Dict[str, Any] = {}
    for role, cfg in (llm or {}).items():
        if not isinstance(cfg, dict):
            continue
        for attr, v in cfg.items():
            if v in (None, ""):
                continue
            k = "%s_%s" % (role, attr)
            if split_role_key(k):
                out[k] = v
    return out


def _resolve_request(pipe, request_id: Any, query: Optional[str], base_ov: Dict[str, Any], log: bool) -> Tuple[Any, Dict[str, Any], Optional[Dict[str, Any]]]:
    """기준 요청을 정한다 → (request_id, 체크포인트, 기준 질의 결과 또는 None). 요청이 없고 질의가 있으면 한 번 실행해 만든다."""
    s = pipe.s
    base_res = None
    if request_id in (None, "") and query:
        with pipe.request_scope(overrides=dict(base_ov, rerun_capture=True)):
            base_res, _ = pipe.query(query, log=log)
        request_id = base_res.get("request_id")
        if request_id is None or not _rr.have(s, request_id):
            raise ValueError("기준 질의의 중간 결과를 저장하지 못했습니다: %s" % ((base_res.get("rerun") or {}).get("reason") or "request_id 없음"))
    elif request_id in (None, "", "last"):
        rows = _rr.list_saved(s, 1)
        if not rows:
            raise ValueError("저장된 중간 결과가 없습니다 — 질의를 한 번 실행하거나 --query 로 기준을 만드세요 (toggles.rerun_capture 확인)")
        request_id = rows[0]["request_id"]
    if isinstance(request_id, str) and request_id.isdigit():
        request_id = int(request_id)
    data = _rr.load(s, request_id)
    if not data:
        raise ValueError("요청 #%s 의 중간 결과가 없습니다 (rerun --list 로 확인)" % request_id)
    return request_id, data, base_res


def run(pipe, request_id: Any, key: str, values: Optional[List[Any]] = None, *, repeats: int = 1, from_point: Optional[str] = None,
        llm: Optional[Dict[str, Any]] = None, query: Optional[str] = None, progress=None, overrides: Optional[Dict[str, Any]] = None,
        log: bool = False, save_record: bool = True) -> Dict[str, Any]:
    """스윕 실행. 값마다 `request_scope(overrides={키: 값}) + pipe.rerun(request_id, point)` — 재실행 API 와 같은 경로.

    request_id: 정수 | "last" | None(+query 로 기준 생성). values: 이미 resolve_values 를 거친 목록(아니면 여기서 coerce).
    llm: {"answer": {"model": …, "provider": …, "effort": …}} 역할 오버라이드 (모든 값에 공통). overrides: 그 밖의 공통 오버라이드.
    progress(msg): 값마다 한 줄. 반환: 저장한 record (sw_<id>.json 과 같은 내용).
    """
    s = pipe.s
    info = classify_key(key)
    point = point_for_key(key, from_point)
    vals = list(values) if values is not None else resolve_values(key, None, int(getattr(s, "sweep_max_values", 20) or 20))
    vals = [coerce_value(info, v) for v in vals]
    limit = int(getattr(s, "sweep_max_values", 20) or 20)
    if len(vals) > limit:
        raise ValueError("값이 %d개 — sweep_max_values=%d 를 넘습니다" % (len(vals), limit))
    repeats = max(1, min(int(repeats or 1), MAX_REPEATS))
    base_ov: Dict[str, Any] = dict(overrides or {})
    if not isinstance(base_ov.get("tuning"), dict):
        base_ov.pop("tuning", None)
    base_ov.update(_llm_to_overrides(llm))
    base_ov["rerun_capture"] = True       # 각 재실행이 자기 체크포인트를 남겨야 단계별 순위를 읽을 수 있다
    say = progress or (lambda m: None)

    rid, data, base_res = _resolve_request(pipe, request_id, query, base_ov, log)
    if point != "plan":
        ok, why = _rr.check_compatible(data, str(pipe.store.build_version()))
        if not ok:
            raise ValueError(why)
    q = str(data.get("query") or "")
    sid = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:4])
    say("스윕 %s: %s = %s (재시작점 %s, 기준 #%s%s)" % (sid, info["key"], ", ".join(str(v) for v in vals), point, rid, " ×%d" % repeats if repeats > 1 else ""))
    plan = [(vi, v, r) for vi, v in enumerate(vals) for r in range(repeats)]
    total = len(plan)
    done = {"n": 0}
    lock = threading.Lock()

    def _one(job: Tuple[int, Any, int]) -> Dict[str, Any]:
        vi, v, r = job
        ov = dict(base_ov)
        if info["kind"] == "tuning":
            ov["tuning"] = dict(ov.get("tuning") or {}, **{info["key"]: v})
        else:
            ov[info["key"]] = v
        t0 = time.perf_counter()
        try:
            with pipe.request_scope(overrides=ov):
                res, tr = pipe.rerun(rid, point, log=log)
            row = summarize_run(pipe, res, tr, v, vi, r, (time.perf_counter() - t0) * 1000.0)
        except ValueError as e:
            row = {"i": vi, "value": v, "repeat": r, "error": str(e)[:300], "ms": None, "stages": {}, "orders": {}, "answer": ""}
        with lock:
            done["n"] += 1
            n = done["n"]
        say("%s=%s%s → %s (%d/%d)" % (info["key"], v, " #%d" % (r + 1) if repeats > 1 else "",
                                      ("오류: " + row["error"]) if row.get("error") else "%.0fms %s g=%s" % (row.get("ms") or 0, row.get("result_type") or "", row.get("groundedness")), n, total))
        return row

    t_all = time.perf_counter()
    n_par = max(1, int(getattr(s, "sweep_max_parallel", 1) or 1))
    if n_par > 1 and total > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(n_par, total), thread_name_prefix="sweep") as ex:
            runs = list(ex.map(_one, plan))      # map 은 입력 순서를 지킨다
    else:
        runs = [_one(j) for j in plan]
    record: Dict[str, Any] = {
        "version": VERSION, "id": sid, "created": time.time(), "request_id": rid, "query": q, "build_version": str(data.get("build_version") or ""),
        "key": info["key"], "input_key": key, "kind": info["kind"], "type": info["type"], "point": point, "point_label": (_rr.point_info(point) or {}).get("label", point),
        "values": vals, "repeats": repeats, "llm": llm or None, "overrides": {k: v for k, v in base_ov.items() if k != "rerun_capture"} or None,
        "parallel": n_par, "ms": round((time.perf_counter() - t_all) * 1000.0, 1), "runs": runs,
        "n_ok": sum(1 for r in runs if not r.get("error")), "n_error": sum(1 for r in runs if r.get("error")),
        "base_request_id": (base_res or {}).get("request_id"), "stages": STAGES,
    }
    if save_record:
        try:
            record["path"] = save(s, record)
        except OSError as e:
            record["save_error"] = str(e)[:200]
    say("완료: %d/%d 성공, %.0fms" % (record["n_ok"], total, record["ms"]))
    return record


# ---------------------------------------------------------------- 비교
def _list_diff(a: List[str], b: List[str]) -> Dict[str, Any]:
    a, b = [str(x) for x in (a or [])], [str(x) for x in (b or [])]
    sa, sb = set(a), set(b)
    pa, pb = {x: i for i, x in enumerate(a)}, {x: i for i, x in enumerate(b)}
    moved = [{"id": x, "from": pa[x], "to": pb[x]} for x in b if x in pa and pa[x] != pb[x]]
    ratio = difflib.SequenceMatcher(None, a, b).ratio() if (a or b) else 1.0
    return {"same": a == b, "ratio": round(ratio, 3), "added": [x for x in b if x not in sa], "removed": [x for x in a if x not in sb],
            "moved": moved, "n_base": len(a), "n": len(b), "top1_same": bool(a and b and a[0] == b[0])}


def _set_diff(a: List[str], b: List[str]) -> Dict[str, Any]:
    sa, sb = set(str(x) for x in (a or [])), set(str(x) for x in (b or []))
    j = len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0
    return {"same": sa == sb, "jaccard": round(j, 3), "added": sorted(sb - sa), "removed": sorted(sa - sb), "n_base": len(sa), "n": len(sb)}


def _text_diff(a: str, b: str) -> Dict[str, Any]:
    a, b = str(a or ""), str(b or "")
    ratio = difflib.SequenceMatcher(None, a, b).ratio() if (a or b) else 1.0
    lines: List[str] = []
    if a != b:
        it = difflib.unified_diff(a.splitlines(), b.splitlines(), "기준", "값", lineterm="", n=1)
        for ln in it:
            lines.append(ln)
            if len(lines) >= ANSWER_DIFF_LINES:
                lines.append("… (생략)")
                break
    return {"same": a == b, "ratio": round(ratio, 3), "chars_delta": len(b) - len(a), "diff": lines}


def _delta(a: Any, b: Any) -> Optional[float]:
    try:
        if a is None or b is None:
            return None
        return round(float(b) - float(a), 4)
    except (TypeError, ValueError):
        return None


def _diff_run(base: Dict[str, Any], r: Dict[str, Any]) -> Dict[str, Any]:
    is_base = r is base
    out: Dict[str, Any] = {"i": r.get("i"), "value": r.get("value"), "repeat": r.get("repeat"), "request_id": r.get("request_id"), "is_base": is_base,
                           "ms": r.get("ms"), "ms_delta": _delta(base.get("ms"), r.get("ms")),
                           "groundedness": r.get("groundedness"), "groundedness_delta": _delta(base.get("groundedness"), r.get("groundedness")),
                           "n_citations": r.get("n_citations"), "n_citations_delta": _delta(base.get("n_citations"), r.get("n_citations")),
                           "tokens": r.get("tokens"), "tokens_delta": _delta(base.get("tokens"), r.get("tokens")),
                           "result_type": r.get("result_type"), "result_type_changed": r.get("result_type") != base.get("result_type"),
                           "verdict": r.get("verdict"), "verdict_changed": r.get("verdict") != base.get("verdict")}
    bo, ro = base.get("orders") or {}, r.get("orders") or {}
    out["hits"] = _list_diff(bo.get("final") or [], ro.get("final") or [])
    out["context"] = _set_diff(bo.get("context") or [], ro.get("context") or [])
    out["answer"] = _text_diff(base.get("answer"), r.get("answer"))
    stages: Dict[str, Any] = {}
    bs, rs = base.get("stages") or {}, r.get("stages") or {}
    for st in STAGES:
        b, c = bs.get(st) or {}, rs.get(st) or {}
        cell: Dict[str, Any] = {"present": c.get("present", False), "ms": c.get("ms"), "ms_delta": _delta(b.get("ms"), c.get("ms")),
                                "replayed": c.get("replayed", False), "skipped": c.get("skipped", False),
                                "state_changed": (bool(b.get("replayed")) != bool(c.get("replayed"))) or (bool(b.get("skipped")) != bool(c.get("skipped"))) or (bool(b.get("present")) != bool(c.get("present"))),
                                "meta_changed": (b.get("meta") or {}) != (c.get("meta") or {})}
        ok = STAGE_ORDER_KEY.get(st)
        if ok:
            cell["order"] = _set_diff(bo.get(ok) or [], ro.get(ok) or []) if ok == "context" else _list_diff(bo.get(ok) or [], ro.get(ok) or [])
        if st == "answer_llm":
            cell["answer"] = {k: out["answer"][k] for k in ("same", "ratio", "chars_delta")}
        if st == "claim_check":
            cell["groundedness_delta"] = out["groundedness_delta"]
        if st == "evidence_check":
            cell["verdict"] = r.get("verdict")
            cell["verdict_changed"] = out["verdict_changed"]
        changed = cell["state_changed"] or cell["meta_changed"]
        if "order" in cell:
            changed = changed or not cell["order"]["same"]
        if st == "answer_llm":
            changed = changed or not out["answer"]["same"]
        if st == "claim_check":
            changed = changed or bool(out["groundedness_delta"])
        if st == "evidence_check":
            changed = changed or out["verdict_changed"]
        cell["changed"] = bool(changed) and not is_base
        stages[st] = cell
    out["stages"] = stages
    out["n_changed_stages"] = sum(1 for c in stages.values() if c["changed"])
    return out


def compare(record: Dict[str, Any]) -> Dict[str, Any]:
    """기준(첫 값의 첫 실행) 대비 값마다 단계별 diff + best 힌트."""
    runs = record.get("runs") or []
    ok = [r for r in runs if not r.get("error")]
    if not ok:
        return {"key": record.get("key"), "point": record.get("point"), "error": "성공한 실행이 없습니다", "runs": [], "stages": STAGES, "best": {}}
    base = ok[0]
    rows: List[Dict[str, Any]] = []
    for r in runs:
        if r.get("error"):
            rows.append({"i": r.get("i"), "value": r.get("value"), "repeat": r.get("repeat"), "error": r.get("error"), "is_base": False, "stages": {}})
        else:
            rows.append(_diff_run(base, r))
    best: Dict[str, Any] = {}
    g = [r for r in ok if r.get("groundedness") is not None]
    if g:
        m = max(g, key=lambda r: float(r["groundedness"]))
        best["groundedness"] = {"value": m["value"], "groundedness": m["groundedness"], "request_id": m.get("request_id")}
    m = min(ok, key=lambda r: float(r.get("ms") or 0))
    best["ms"] = {"value": m["value"], "ms": m.get("ms"), "request_id": m.get("request_id")}
    m = max(ok, key=lambda r: int(r.get("n_citations") or 0))
    best["citations"] = {"value": m["value"], "n_citations": m.get("n_citations"), "request_id": m.get("request_id")}
    t = [r for r in ok if r.get("tokens") is not None]
    if t:
        m = min(t, key=lambda r: float(r["tokens"]))
        best["tokens"] = {"value": m["value"], "tokens": m["tokens"], "request_id": m.get("request_id")}
    return {"key": record.get("key"), "kind": record.get("kind"), "point": record.get("point"), "stages": STAGES,
            "baseline": {"value": base.get("value"), "request_id": base.get("request_id"), "i": base.get("i"), "repeat": base.get("repeat")},
            "runs": rows, "best": best, "n_runs": len(runs), "n_error": len(runs) - len(ok)}


# ---------------------------------------------------------------- 텍스트
def _cell(c: Dict[str, Any]) -> str:
    if not c or not c.get("present"):
        return "·"
    if c.get("skipped"):
        return "–"
    ms = c.get("ms") or 0.0
    txt = ("%.0f" % ms) if ms >= 10 else ("%.1f" % ms)
    if c.get("replayed"):
        txt = "⟲" + txt
    if c.get("changed"):
        txt += "★"
    return txt


def render_text(record: Dict[str, Any], cmp: Optional[Dict[str, Any]] = None) -> str:
    cmp = cmp or compare(record)
    lines = ["스윕 sw_%s — %s = %s  (%s · 재시작점 %s · 기준 요청 #%s%s)" % (
        record.get("id"), record.get("key"), ", ".join(str(v) for v in (record.get("values") or [])), record.get("kind"), record.get("point"),
        record.get("request_id"), " · %d회 반복" % record.get("repeats", 1) if int(record.get("repeats") or 1) > 1 else "")]
    lines.append("질의: %s" % str(record.get("query") or "")[:120])
    lines.append("표기: ms · ⟲=재생(저장값) · –=건너뜀 · ·=없음 · ★=기준과 다름")
    hdr = "%-14s " % "값" + " ".join("%-10s" % st[:10] for st in STAGES) + " | %7s %6s %4s %s" % ("총ms", "ground", "인용", "type")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for row in cmp.get("runs") or []:
        label = str(row.get("value"))
        if int(record.get("repeats") or 1) > 1:
            label += " #%d" % (int(row.get("repeat") or 0) + 1)
        if row.get("is_base"):
            label += " (기준)"
        if row.get("error"):
            lines.append("%-14s 오류: %s" % (label[:14], row["error"][:100]))
            continue
        cells = " ".join("%-10s" % _cell(row["stages"].get(st) or {}) for st in STAGES)
        g = row.get("groundedness")
        lines.append("%-14s %s | %7s %6s %4s %s" % (label[:14], cells, "%.0f" % (row.get("ms") or 0), "%.2f" % g if g is not None else "-",
                                                   row.get("n_citations") if row.get("n_citations") is not None else "-", row.get("result_type") or ""))
    for row in cmp.get("runs") or []:
        if row.get("is_base") or row.get("error"):
            continue
        bits = []
        h = row.get("hits") or {}
        if not h.get("same"):
            bits.append("최종 순위 유사도 %.2f (추가 %d · 제거 %d · 이동 %d)" % (h.get("ratio", 0), len(h.get("added") or []), len(h.get("removed") or []), len(h.get("moved") or [])))
        c = row.get("context") or {}
        if not c.get("same"):
            bits.append("컨텍스트 jaccard %.2f (+%d/-%d)" % (c.get("jaccard", 0), len(c.get("added") or []), len(c.get("removed") or [])))
        a = row.get("answer") or {}
        if not a.get("same"):
            bits.append("답변 유사도 %.2f (%+d자)" % (a.get("ratio", 0), a.get("chars_delta", 0)))
        if bits:
            lines.append("  %s: %s" % (row.get("value"), " · ".join(bits)))
    best = cmp.get("best") or {}
    if best:
        parts = []
        if "groundedness" in best:
            parts.append("groundedness 최고 = %s (%.2f)" % (best["groundedness"]["value"], float(best["groundedness"]["groundedness"])))
        if "ms" in best:
            parts.append("가장 빠름 = %s (%.0fms)" % (best["ms"]["value"], float(best["ms"]["ms"] or 0)))
        if "citations" in best:
            parts.append("인용 최다 = %s (%s)" % (best["citations"]["value"], best["citations"]["n_citations"]))
        lines.append("최적 힌트: " + " · ".join(parts))
    if record.get("path"):
        lines.append("파일: %s" % record["path"])
    return "\n".join(lines)


def brief(record: Dict[str, Any], answer_chars: int = 2000) -> Dict[str, Any]:
    """MCP structuredContent 용: 답변 본문을 잘라 크기를 줄인 사본."""
    out = dict(record)
    out["runs"] = [dict(r, answer=str(r.get("answer") or "")[:answer_chars]) for r in (record.get("runs") or [])]
    return out
