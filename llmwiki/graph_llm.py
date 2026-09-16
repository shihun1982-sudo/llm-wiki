# -*- coding: utf-8 -*-
"""LLM 기반 엔티티/관계 추출 및 커뮤니티 요약 프롬프트."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .providers import BaseLLM, parse_json, LLMError

from . import prompts as _prompts


def EXTRACT_SYSTEM() -> str:   # prompts/extract.md
    return _prompts.get("extract")


def SUMMARY_SYSTEM() -> str:   # prompts/summarize.md
    return _prompts.get("summarize")


def llm_extract(llm: BaseLLM, text: str, heading: str, known: List[str], effort: str = "low", n_known: int = 60,
                max_tokens: int = 4000) -> Optional[Dict[str, Any]]:
    user = "## 섹션: %s\n\n## 이미 알려진 엔티티\n%s\n\n## 텍스트\n%s" % (heading, ", ".join(known[:n_known]) or "(없음)", text)
    try:
        r = llm.complete(EXTRACT_SYSTEM(), user, max_tokens=max_tokens, effort=effort, json_mode=True)
    except LLMError:
        return None
    data = parse_json(r["text"])
    if not isinstance(data, dict):
        return None
    data.setdefault("entities", [])
    data.setdefault("relations", [])
    data["_usage"] = r.get("usage", {})
    data["_ms"] = r.get("ms", 0)
    return data


def llm_summarize_community(llm: BaseLLM, entities: List[Dict[str, Any]], relations: List[Dict[str, Any]],
                            effort: str = "low", max_tokens: int = 800) -> str:
    lines = ["엔티티:"]
    for e in entities[:40]:
        lines.append("- %s (%s) %s" % (e["name"], e["type"], (e.get("description") or "")[:80]))
    lines.append("관계:")
    for r in relations[:60]:
        lines.append("- %s -[%s]-> %s : %s" % (r["src"], r["rel"], r["dst"], (r.get("description") or "")[:80]))
    try:
        return llm.complete(SUMMARY_SYSTEM(), "\n".join(lines), max_tokens=max_tokens, effort=effort)["text"].strip()
    except LLMError:
        return ""
