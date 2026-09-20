# -*- coding: utf-8 -*-
"""코퍼스 로더 + 제목 인지형(heading-aware) 청커.

지원 포맷: .md .txt .csv .html(.htm) .pdf (pypdf). 문서마다 sha1 해시를 계산해 증분 빌드에 사용.

대규모 코퍼스(수천 파일, 매일 수십 개 추가) 를 위해 iter_corpus(dirs, known=...) 는 파일의 mtime/size 가
이전 빌드와 같으면 파일을 열지 않고(stat 만으로) '변경 없음' 문서 스텁을 돌려준다 (Toggles.stat_skip).
"""
from __future__ import annotations

import fnmatch
import os
import re
import html
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Iterable, Tuple

from .textutil import sha1

SUPPORTED = (".md", ".txt", ".csv", ".html", ".htm", ".pdf")
# 코퍼스 폴더 안에 있어도 색인하지 않는 폴더. _originals 는 tools/corpus_ingest.py 가 변환 전 원본을 보관하는 곳이라
# 색인하면 같은 내용이 두 번 들어간다 (원본은 추적·재변환용으로만 둔다).
EXCLUDE_DIRS = {"_originals", "_archive", "__pycache__", "node_modules"}


def compile_excludes(patterns: Optional[Iterable[str]]) -> List[str]:
    """`corpus_exclude` 패턴 목록을 정리한다 (빈 값·주석 제거).

    왜 필요한가: 예전에는 제외 폴더가 코드에 박혀 있어서, 코퍼스 안에 **색인하면 안 되는 것**이
    섞여 있어도 파일을 옮기는 것 말고는 방법이 없었다. 실제로 이 도구 자신의 소스가 코퍼스에
    들어가 도메인 문서를 밀어낸 적이 있다 (제외하니 hit@k 0.64 → 0.88).
    """
    return [str(p).strip() for p in (patterns or []) if str(p).strip() and not str(p).strip().startswith("#")]


def is_excluded(doc_id: str, patterns: Iterable[str]) -> bool:
    """doc_id(코퍼스 루트 기준 상대 경로, '/' 구분)가 제외 패턴에 걸리는가.

    받아들이는 형태 — 셋 다 같은 뜻으로 쓸 수 있게 한다 (사람마다 다르게 적는다):
      `imported/llmwiki/`  ·  `imported/llmwiki/*`  ·  `imported/llmwiki/**`   → 그 아래 전부
      `**/NOTE-*.md`  ·  `*.csv`                                              → fnmatch 패턴

    doc_id 는 **코퍼스 폴더 이름으로 시작**한다 (`corpus/imported/cls/CL-1.md`). `docs` 목록에 보이는
    그대로 적어도 되고, 앞의 코퍼스 폴더 이름을 빼고 적어도 되게 **둘 다** 맞춰 본다 —
    목록에서 복사해 붙이는 사람과 문서를 보고 적는 사람이 서로 다르게 쓰기 때문이다.
    """
    did = str(doc_id or "").replace("\\", "/")
    cands = [did]
    if "/" in did:
        cands.append(did.split("/", 1)[1])      # 맨 앞 코퍼스 폴더 이름을 뗀 형태
    for pat in patterns:
        p = pat.replace("\\", "/").lstrip("/")
        base = p.rstrip("*").rstrip("/")
        for c in cands:
            if fnmatch.fnmatch(c, p):
                return True
            if base and (c == base or c.startswith(base + "/")):
                return True
    return False


@dataclass
class Document:
    doc_id: str           # 코퍼스 루트 기준 상대 경로 (구분자 '/')
    path: str
    title: str
    text: str             # 본문 (front matter 제거)
    kind: str             # md | txt | csv | html | pdf
    hash: str             # 원문 전체(front matter 포함) sha1
    meta: Dict[str, Any] = field(default_factory=dict)   # root, folder, fm(front matter dict)
    mtime: float = 0.0
    size: int = 0
    skipped: bool = False  # stat_skip 으로 읽지 않은 '변경 없음' 스텁 (text 는 비어 있음)


@dataclass
class Chunk:
    chunk_id: str         # doc_id#n
    doc_id: str
    ordinal: int
    heading: str
    text: str
    start: int
    end: int


def _read_pdf(path: str) -> str:
    try:
        import pypdf
    except ImportError:
        return ""
    r = pypdf.PdfReader(path)
    pages = []
    for i, p in enumerate(r.pages):
        try:
            t = p.extract_text() or ""
        except Exception:
            t = ""
        pages.append("\n\n[page %d]\n%s" % (i + 1, t))
    return "\n".join(pages)


def _read_html(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read()
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h[1-6]>|</li>|</tr>", "\n", raw)
    raw = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n\s*\n+", "\n\n", raw)
    return raw.strip()


def doc_id_for(path: str, root: str) -> str:
    rel = os.path.relpath(path, root).replace("\\", "/")
    root_tag = os.path.basename(root.rstrip("\\/"))
    return root_tag + "/" + rel


def _read_csv(path: str) -> str:
    """CSV 를 마크다운 표 형태의 텍스트로 (헤더는 굵게, 행마다 한 줄) — FTS/임베딩이 컬럼명과 값을 함께 보도록."""
    import csv
    with open(path, "r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return ""
    header = [h.strip() for h in rows[0]]
    lines = ["# " + os.path.splitext(os.path.basename(path))[0], "", "컬럼: " + ", ".join(header), ""]
    for r in rows[1:]:
        cells = ["%s=%s" % (header[i] if i < len(header) else "col%d" % i, (c or "").strip()) for i, c in enumerate(r) if (c or "").strip()]
        if cells:
            lines.append("- " + "; ".join(cells))
    return "\n".join(lines)


def load_document(path: str, root: str, known: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[Document]:
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED:
        return None
    try:
        stt = os.stat(path)
        mtime, size = float(stt.st_mtime), int(stt.st_size)
    except OSError:
        return None
    doc_id = doc_id_for(path, root)
    if known is not None:
        k = known.get(doc_id)
        if k and k.get("mtime") and abs(float(k["mtime"]) - mtime) < 1e-6 and int(k.get("size") or -1) == size:
            return Document(doc_id=doc_id, path=path, title=k.get("title") or os.path.basename(path), text="",
                            kind=k.get("kind") or ext[1:], hash=k["hash"], meta={"root": root},
                            mtime=mtime, size=size, skipped=True)
    if ext == ".pdf":
        text = _read_pdf(path)
        kind = "pdf"
    elif ext in (".html", ".htm"):
        text = _read_html(path)
        kind = "html"
    elif ext == ".csv":
        text = _read_csv(path)
        kind = "csv"
    else:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        kind = ext[1:]
    if not text.strip():
        return None
    rel = os.path.relpath(path, root).replace("\\", "/")
    raw = text
    fm: Dict[str, Any] = {}
    had_fm = False
    if kind in ("md", "txt"):
        from .schema import parse_front_matter
        fm, body, had_fm = parse_front_matter(text)
        if had_fm:
            text = body if body.strip() else text
    title = (str(fm.get("title")) if fm.get("title") else "") or _guess_title(text) or os.path.basename(path)
    meta: Dict[str, Any] = {"root": root, "folder": os.path.dirname(rel).replace("\\", "/")}
    if had_fm:
        meta["fm"] = fm
    return Document(doc_id=doc_id, path=path, title=title, text=text, kind=kind, hash=sha1(raw), meta=meta, mtime=mtime, size=size)


def _guess_title(text: str) -> str:
    for line in text.splitlines()[:30]:
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:120]
    for line in text.splitlines():
        s = line.strip()
        if len(s) > 12 and not re.match(r"^\[page \d+\]$", s):
            return s[:120]
    return ""


def iter_corpus(dirs: Iterable[str], known: Optional[Dict[str, Dict[str, Any]]] = None,
                stats: Optional[Dict[str, Any]] = None, exclude: Optional[Iterable[str]] = None) -> List[Document]:
    """코퍼스 폴더를 재귀 스캔. known(doc_id -> {hash,mtime,size,...}) 이 주어지면 stat 이 같은 파일은 읽지 않는다.
    exclude 는 `corpus_exclude` 패턴 (doc_id 기준). stats 에 파일 수/스킵 수/제외 수/읽기 시간을 채운다."""
    docs: List[Document] = []
    st = stats if stats is not None else {}
    st.update({"files_seen": 0, "unsupported": 0, "skipped_stat": 0, "read": 0, "empty": 0, "read_ms_by_kind": {},
               "slowest": [], "missing_dirs": [], "excluded": 0})
    ex = compile_excludes(exclude)
    slow: List[Tuple[float, str]] = []
    for d in dirs:
        if not os.path.isdir(d):
            st["missing_dirs"].append(d)
            continue
        for base, _dirs, files in os.walk(d):
            _dirs[:] = [x for x in _dirs if (not x.startswith(".") or x == ".claude") and x not in EXCLUDE_DIRS]  # 숨김 폴더 제외(.claude 는 허용)
            for fn in sorted(files):
                p = os.path.join(base, fn)
                st["files_seen"] += 1
                if os.path.splitext(fn)[1].lower() not in SUPPORTED:
                    st["unsupported"] += 1
                    continue
                if ex and is_excluded(doc_id_for(p, d), ex):
                    st["excluded"] += 1
                    continue
                t0 = time.perf_counter()
                doc = load_document(p, d, known)
                dt = (time.perf_counter() - t0) * 1000
                if doc is None:
                    st["empty"] += 1
                    continue
                if doc.skipped:
                    st["skipped_stat"] += 1
                else:
                    st["read"] += 1
                    st["read_ms_by_kind"][doc.kind] = round(st["read_ms_by_kind"].get(doc.kind, 0.0) + dt, 1)
                    slow.append((dt, doc.doc_id))
                docs.append(doc)
    slow.sort(reverse=True)
    st["slowest"] = [{"doc_id": d, "ms": round(ms, 1)} for ms, d in slow[:5]]
    return docs


def scan_changed(dirs: Iterable[str], known: Dict[str, Dict[str, Any]],
                 exclude: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """빌드 없이 변경 여부만 빠르게 판단 (auto_build 워처용). 파일을 읽지 않고 stat 만 비교.

    exclude 는 iter_corpus 와 **같은 목록**을 받아야 한다. 다르면 워처가 "바뀐 문서가 있다" 고 보고
    빌드를 돌리는데 빌드는 그 문서를 제외해서, 매 주기마다 헛빌드가 돈다.
    """
    seen = set()
    changed: List[str] = []
    ex = compile_excludes(exclude)
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for base, _dirs, files in os.walk(d):
            _dirs[:] = [x for x in _dirs if (not x.startswith(".") or x == ".claude") and x not in EXCLUDE_DIRS]
            for fn in files:
                if os.path.splitext(fn)[1].lower() not in SUPPORTED:
                    continue
                p = os.path.join(base, fn)
                did = doc_id_for(p, d)
                if ex and is_excluded(did, ex):
                    continue
                seen.add(did)
                k = known.get(did)
                try:
                    stt = os.stat(p)
                except OSError:
                    continue
                if not k or abs(float(k.get("mtime") or 0) - stt.st_mtime) > 1e-6 or int(k.get("size") or -1) != stt.st_size:
                    changed.append(did)
    removed = [d for d in known if d not in seen and not d.startswith("wiki/")]
    return {"changed": changed, "removed": removed, "n_changed": len(changed), "n_removed": len(removed)}


_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")


def chunk_document(doc: Document, max_chars: int = 900, overlap: int = 120, min_chars: int = 20) -> List[Chunk]:
    """마크다운 헤딩 단위로 섹션을 나눈 뒤, 긴 섹션은 문단 경계로 분할(오버랩 포함)."""
    lines = doc.text.splitlines(True)
    sections: List[tuple] = []  # (heading_path, text, start)
    cur_heads: List[str] = [doc.title]
    buf: List[str] = []
    pos = 0
    start = 0
    for ln in lines:
        m = _HEAD.match(ln.strip())
        if m:
            if "".join(buf).strip():
                sections.append((" > ".join(cur_heads), "".join(buf), start))
            level = len(m.group(1))
            cur_heads = cur_heads[: max(1, level - 1)] + [m.group(2).strip()]
            buf = [ln]
            start = pos
        else:
            buf.append(ln)
        pos += len(ln)
    if "".join(buf).strip():
        sections.append((" > ".join(cur_heads), "".join(buf), start))

    chunks: List[Chunk] = []
    n = 0
    for heading, text, s0 in sections:
        for piece, off in _split_long(text, max_chars, overlap):
            body = piece.strip()
            if len(body) < max(1, min_chars):
                continue
            chunks.append(Chunk(chunk_id="%s#%d" % (doc.doc_id, n), doc_id=doc.doc_id, ordinal=n,
                                heading=heading, text=body, start=s0 + off, end=s0 + off + len(piece)))
            n += 1
    return chunks


def _split_long(text: str, max_chars: int, overlap: int):
    if len(text) <= max_chars:
        yield text, 0
        return
    paras = re.split(r"(\n\s*\n)", text)
    cur = ""
    cur_off = 0
    off = 0
    for p in paras:
        if len(cur) + len(p) > max_chars and cur.strip():
            yield cur, cur_off
            tail = cur[-overlap:] if overlap > 0 else ""
            cur_off = off - len(tail)
            cur = tail
        cur += p
        off += len(p)
        while len(cur) > max_chars * 1.6:  # 문단 자체가 매우 긴 경우 강제 분할
            yield cur[:max_chars], cur_off
            cur_off += max_chars - overlap
            cur = cur[max_chars - overlap:]
    if cur.strip():
        yield cur, cur_off
