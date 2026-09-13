# -*- coding: utf-8 -*-
"""LLM Wiki 페이지 생성: 엔티티별 마크다운 페이지 (요약, 관계, 근거 문단, 출처).

wiki/ 폴더의 페이지는 사람이 편집할 수 있으며, 편집 내용(`## 편집 노트` 섹션)은 다음 빌드에서
overlay 코퍼스로 재색인되어 검색 결과에 반영된다 (self-evolving 의 human-in-the-loop 경로).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

from .profiler import Profiler
from .store import Store

NOTE_MARK = "## 편집 노트"


def slug(name: str) -> str:
    s = re.sub(r"[^\w가-힣\-]+", "_", name).strip("_")
    return s[:80] or "page"


def write_wiki(store: Store, wiki_dir: str, prof: Profiler, min_degree: int = 1, prune: bool = False,
               only: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """엔티티 위키 페이지 생성. only(엔티티 id 집합) 가 주어지면 그 페이지들과 INDEX 만 다시 쓴다 (증분 빌드)."""
    os.makedirs(wiki_dir, exist_ok=True)
    written_files = set()
    ents = [e for e in store.entities() if e["type"] not in ("date", "amount") and (e["degree"] or 0) >= min_degree]
    only_set = set(only) if only is not None else None
    targets = ents if only_set is None else [e for e in ents if e["entity_id"] in only_set]
    with prof.stage("wiki_pages", entities=len(ents), mode="incremental(%d)" % len(targets) if only_set is not None else "full") as st:
        written = 0
        index_lines = ["# LLM Wiki 인덱스", "", "| 엔티티 | 유형 | 연결수 | 문서수 | 커뮤니티 |", "|---|---|---|---|---|"]
        for e in ents:
            index_lines.append("| [[%s]] | %s | %s | %s | %s |" % (slug(e["name"]), e["type"], e["degree"], e.get("n_docs") or 0, e["community"]))
        for e in targets:
            path = os.path.join(wiki_dir, slug(e["name"]) + ".md")
            note = _read_note(path)
            rels = store.relations_of(e["entity_id"])
            mentions = store.mentions_of(e["entity_id"])[:8]
            chunks = store.get_chunks([m["chunk_id"] for m in mentions])
            try:
                doc_refs = json.loads(e.get("doc_refs") or "[]")
            except Exception:
                doc_refs = []
            lines = ["# %s" % e["name"], "",
                     "- 유형: `%s`  · 출처: `%s`  · 신뢰도: %.2f  · 연결수: %s  · 커뮤니티: %s  · 문서수: %s" % (
                         e["type"], e["source"], e["confidence"] or 0, e["degree"], e["community"], e.get("n_docs") or len(doc_refs)),
                     "- 별칭: %s" % ", ".join(json.loads(e["aliases"] or "[]")) if e["aliases"] else "- 별칭: (없음)", ""]
            if e["description"]:
                lines += ["## 설명", "", e["description"], ""]
            if doc_refs:
                lines += ["## 문서 참조", ""]
                for d in doc_refs[:20]:
                    lines.append("- `%s` (언급 %s회, 청크 %s개, 첫 청크 `%s`)" % (d["doc_id"], d["mentions"], d["chunks"], d.get("first_chunk") or ""))
                lines.append("")
            lines += ["## 관계", ""]
            for r in rels[:40]:
                other = r["dst"] if r["src"] == e["entity_id"] else r["src"]
                oe = store.get_entity(other)
                oname = oe["name"] if oe else other
                arrow = "→" if r["src"] == e["entity_id"] else "←"
                lines.append("- %s **%s** [[%s]] (w=%.2f, %s)%s" % (arrow, r["rel"], slug(oname), r["weight"] or 0, r["source"],
                                                                (" — " + r["description"][:100]) if r["description"] else ""))
            lines += ["", "## 근거 문단", ""]
            for m in mentions:
                c = chunks.get(m["chunk_id"])
                if c:
                    lines.append("- `%s` (%s): %s" % (c["chunk_id"], c["heading"][:60], c["text"][:160].replace("\n", " ") + "…"))
            lines += ["", NOTE_MARK, "", note or "(이 섹션은 빌드 시 보존됩니다. 사람이 수정/보충한 내용을 적으면 다음 빌드에서 색인됩니다.)", ""]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            written_files.add(os.path.basename(path))
            written += 1
        if only_set is not None:  # 증분: 이번에 안 쓴 페이지도 '살아있는' 것으로 간주
            written_files.update(slug(e["name"]) + ".md" for e in ents)
        comms = store.communities_all()
        if comms:
            index_lines += ["", "## 커뮤니티", ""]
            for c in comms[:30]:
                index_lines.append("- **C%s** (n=%s): %s — %s" % (c["community"], c["size"], ", ".join(json.loads(c["top_entities"])[:6]),
                                                                (c["summary"] or "")[:160].replace("\n", " ")))
        with open(os.path.join(wiki_dir, "INDEX.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(index_lines))
        removed = 0
        if prune:  # 이번 빌드에서 생성되지 않은 옛 페이지 중 사람 편집 노트가 없는 것만 삭제
            for fn in os.listdir(wiki_dir):
                if fn.endswith(".md") and fn != "INDEX.md" and fn not in written_files and not _read_note(os.path.join(wiki_dir, fn)):
                    os.remove(os.path.join(wiki_dir, fn))
                    removed += 1
        st.note(written=written, removed_stale=removed, total_entities=len(ents))
    return {"pages": written, "removed_stale": removed, "total_entities": len(ents)}


def _read_note(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        txt = f.read()
    i = txt.find(NOTE_MARK)
    if i < 0:
        return ""
    note = txt[i + len(NOTE_MARK):].strip()
    if note.startswith("(이 섹션은"):
        return ""
    return note


def wiki_notes(wiki_dir: str) -> List[Dict[str, str]]:
    """사람이 작성한 편집 노트를 overlay 문서로 반환."""
    out: List[Dict[str, str]] = []
    if not os.path.isdir(wiki_dir):
        return out
    for fn in sorted(os.listdir(wiki_dir)):
        if not fn.endswith(".md") or fn == "INDEX.md":
            continue
        p = os.path.join(wiki_dir, fn)
        note = _read_note(p)
        if note:
            out.append({"name": fn[:-3], "path": p, "note": note})
    return out
