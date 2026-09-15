# -*- coding: utf-8 -*-
"""설정 프리셋 — 품질/속도/토큰 최적화처럼 토글·튜닝·config 값을 묶어 한 번에 적용한다.

presets.json:
  {"quality": {"desc": "...", "toggles": {...}, "tuning": {...}, "settings": {...}}, ...}
적용 순서: 이름 목록 순서대로 덮어쓰며(뒤가 우선) 충돌 키는 conflicts 로 보고한다.
CLI: preset list | show <name> | apply <a> [<b> …] [--save] | diff <name>     질의/평가/빌드: --preset quality,token
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .config import Settings, Toggles, apply_overrides, save_settings, path_for
from . import tuning as _tuning

DEFAULT_PRESETS: Dict[str, Dict[str, Any]] = {
    "quality": {
        "desc": "품질 최적화 — LLM 개입 단계(확장·근거 판정·claim 검증·fallback)를 모두 켜고 후보/컨텍스트를 넓힘. 지연·토큰 ↑.",
        "toggles": {"router": True, "rerank": True, "rerank_llm": True, "llm_answer": True, "context_trim": False, "dedupe_hits": True,
                    "query_rules": True, "time_scope": True, "query_expand": True, "query_decompose": True, "evidence_check": True,
                    "evidence_check_llm": True, "fallback_loop": True, "claim_check": True, "claim_check_llm": True, "answer_refine": True,
                    "pins": True, "forensic_auto": True, "doc_expand": True},
        "tuning": {"fts_mode": "tiered", "prf_enabled": True, "context_neighbors": 1, "query_expand_n": 3, "fallback_max_attempts": 3,
                   "answer_length_target": "long", "rerank_method": "auto", "doc_expand_max_chunks": 5, "doc_expand_min_score": 0.15},
        "settings": {"top_k_fts": 20, "top_k_vector": 20, "top_k_graph": 16, "top_k_final": 10, "rerank_candidates": 24,
                     "context_max_chars": 14000, "answer_max_tokens": 4000, "answer_effort": "high"},
    },
    "speed": {
        "desc": "속도 최적화 — LLM 호출을 답변 1회로 줄이고 후보 수·홉 수를 낮춤. 캐시·프리컴퓨트 사용.",
        "toggles": {"rerank_llm": False, "query_expand": False, "query_decompose": False, "evidence_check": True, "evidence_check_llm": False,
                    "fallback_loop": False, "claim_check": True, "claim_check_llm": False, "answer_refine": False, "query_cache": True,
                    "precompute": True, "context_trim": True, "dedupe_hits": True, "doc_expand": False},
        "tuning": {"fts_mode": "or", "prf_enabled": False, "context_neighbors": 0, "rerank_method": "local", "answer_length_target": "normal"},
        "settings": {"top_k_fts": 10, "top_k_vector": 10, "top_k_graph": 8, "top_k_final": 6, "graph_hops": 1, "rerank_candidates": 12,
                     "context_max_chars": 7000, "answer_effort": "low"},
    },
    "token": {
        "desc": "토큰 최적화 — 입력 컨텍스트 압축·중복 제거·LLM 판정 최소화. 답변 LLM 외 호출 0회 목표.",
        "toggles": {"rerank_llm": False, "query_expand": False, "query_decompose": False, "evidence_check_llm": False, "fallback_loop": False,
                    "claim_check": True, "claim_check_llm": False, "answer_refine": False, "evidence_compress": False, "context_trim": True,
                    "dedupe_hits": True, "query_cache": True, "doc_expand": False},
        "tuning": {"rerank_method": "local", "context_neighbors": 0, "context_graph_relations": 5, "answer_length_target": "short"},
        "settings": {"top_k_final": 5, "context_max_chars": 5000, "context_chunk_chars": 700, "answer_max_tokens": 1200, "answer_effort": "low"},
    },
    "offline": {
        "desc": "오프라인 — LLM/외부 API 없이 추출식 답변·로컬 리랭크·규칙 확장만 사용.",
        "toggles": {"llm_answer": False, "rerank_llm": False, "query_expand": False, "evidence_check_llm": False, "claim_check_llm": False,
                    "answer_refine": False, "llm_graph": False, "community_summary": False, "mcp_sources": False},
        "tuning": {"rerank_method": "local"},
        "settings": {"llm_provider": "none"},
    },
    "deep_research": {
        "desc": "심층 조사(unified search) — 확장·분해·fallback 을 최대로, 긴 구조화 답변. MCP/에이전트 활용 시 함께 사용.",
        "toggles": {"query_rules": True, "time_scope": True, "query_expand": True, "query_decompose": True, "evidence_check": True,
                    "evidence_check_llm": True, "fallback_loop": True, "claim_check": True, "claim_check_llm": True, "answer_refine": True,
                    "rerank_llm": True, "forensic_auto": True, "doc_expand": True},
        "tuning": {"query_expand_n": 4, "fallback_max_attempts": 4, "fallback_token_budget": 60000, "fallback_latency_ms": 120000,
                   "context_neighbors": 1, "answer_length_target": "long", "doc_expand_top_docs": 5, "doc_expand_max_chunks": 6, "doc_expand_min_score": 0.1},
        "settings": {"top_k_fts": 30, "top_k_vector": 30, "top_k_graph": 20, "top_k_final": 14, "graph_hops": 3, "rerank_candidates": 32,
                     "context_max_chars": 20000, "answer_max_tokens": 6000, "answer_effort": "high"},
    },
}


def presets_path() -> str:
    return path_for("presets")


def load_presets() -> Dict[str, Dict[str, Any]]:
    p = presets_path()
    if not os.path.exists(p):
        save_presets(DEFAULT_PRESETS)
        return json.loads(json.dumps(DEFAULT_PRESETS))
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def save_presets(data: Dict[str, Dict[str, Any]]) -> str:
    p = presets_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    out = {"_comment": "설정 프리셋. toggles/tuning/settings 를 묶어 `preset apply <name>` 또는 --preset 으로 적용. 새 프리셋은 항목 추가."}
    out.update(data)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return p


def _known_toggles(t: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in (t or {}).items() if k in Toggles.__dataclass_fields__}


def _known_tuning(t: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in (t or {}).items() if k in _tuning._INDEX and _tuning._INDEX[k]["source"] == "tuning"}


def plan(names: List[str], presets: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """여러 프리셋을 합쳐 최종 변경 집합과 충돌 목록을 만든다 (뒤가 우선)."""
    presets = presets or load_presets()
    merged: Dict[str, Dict[str, Any]] = {"toggles": {}, "tuning": {}, "settings": {}}
    origin: Dict[str, str] = {}
    conflicts: List[Dict[str, Any]] = []
    unknown: List[str] = []
    for n in names:
        pr = presets.get(n)
        if not pr:
            unknown.append(n)
            continue
        for sect, vals in (("toggles", _known_toggles(pr.get("toggles", {}))), ("tuning", _known_tuning(pr.get("tuning", {}))),
                           ("settings", {k: v for k, v in (pr.get("settings") or {}).items() if k in Settings.__dataclass_fields__ or
                                         (k.rsplit("_", 1)[0] in Settings.LLM_ROLES)})):
            for k, v in vals.items():
                key = sect + "." + k
                if k in merged[sect] and merged[sect][k] != v:
                    conflicts.append({"key": key, "kept": v, "from": n, "dropped": merged[sect][k], "dropped_from": origin[key]})
                merged[sect][k] = v
                origin[key] = n
    return {"names": names, "unknown": unknown, "toggles": merged["toggles"], "tuning": merged["tuning"], "settings": merged["settings"],
            "conflicts": conflicts}


def apply(settings: Settings, names: List[str], save: bool = False, tuning: Optional[_tuning.Tuning] = None) -> Dict[str, Any]:
    """settings(Settings 객체) 와 전역 tuning 에 프리셋을 적용. 반환에 이전 값(prev)이 있어 restore() 로 되돌릴 수 있다."""
    t = tuning or _tuning.T
    pl = plan(names)
    prev: Dict[str, Any] = {"toggles": {}, "tuning": {}, "settings": {}}
    for k, v in pl["toggles"].items():
        prev["toggles"][k] = getattr(settings.toggles, k)
    for k, v in pl["settings"].items():
        if k in Settings.__dataclass_fields__:
            prev["settings"][k] = getattr(settings, k)
        else:
            role, attr = k.rsplit("_", 1)
            prev["settings"][k] = (settings.llm_roles.get(role) or {}).get(attr, "")
    for k, v in pl["tuning"].items():
        prev["tuning"][k] = t.values.get(k, None)
    apply_overrides(settings, dict(pl["toggles"], **pl["settings"]))
    errors: Dict[str, str] = {}
    for k, v in pl["tuning"].items():
        try:
            t.set(k, v)
        except (KeyError, ValueError) as e:
            errors[k] = str(e)
    if save:
        save_settings(settings)
        _tuning.save_tuning(t)
    return dict(pl, prev=prev, errors=errors, saved=save)


def restore(settings: Settings, prev: Dict[str, Any], tuning: Optional[_tuning.Tuning] = None) -> None:
    t = tuning or _tuning.T
    apply_overrides(settings, dict(prev.get("toggles", {}), **{k: v for k, v in prev.get("settings", {}).items()}))
    for k, v in prev.get("tuning", {}).items():
        if v is None:
            t.reset(k)
        else:
            t.values[k] = v


def diff(settings: Settings, name: str, tuning: Optional[_tuning.Tuning] = None) -> List[Dict[str, Any]]:
    """프리셋을 적용하면 무엇이 바뀌는지 (현재값 → 프리셋값)."""
    t = tuning or _tuning.T
    pl = plan([name])
    rows: List[Dict[str, Any]] = []
    for k, v in pl["toggles"].items():
        cur = getattr(settings.toggles, k)
        rows.append({"key": "toggles." + k, "current": cur, "preset": v, "changes": cur != v})
    for k, v in pl["settings"].items():
        cur = getattr(settings, k, None) if k in Settings.__dataclass_fields__ else (settings.llm_roles.get(k.rsplit("_", 1)[0]) or {}).get(k.rsplit("_", 1)[1])
        rows.append({"key": k, "current": cur, "preset": v, "changes": cur != v})
    for k, v in pl["tuning"].items():
        cur = t.get(k)
        rows.append({"key": "tuning." + k, "current": cur, "preset": v, "changes": cur != v})
    return rows


def parse_names(arg: Optional[str]) -> List[str]:
    if not arg:
        return []
    return [x.strip() for x in str(arg).replace(";", ",").split(",") if x.strip()]
