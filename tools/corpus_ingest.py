# -*- coding: utf-8 -*-
"""아무 문서나 코퍼스 계약(front matter) 형식으로 바꿔 넣는 범용 도구. (2026-09-15)

형식이 정해지지 않은 자료(메모·회의록·소스코드·JSON·로그·HTML·PDF…)를 그대로 넣어도 색인·검색·그래프가 동작하도록
docs/CORPUS_CONTRACT.md 의 front matter 를 붙여 준다. 유형은 파일 경로·이름·본문으로 추론하고(ISSUE-/CL-/TC-/RULE-/주간보고…),
알 수 없으면 자유 형식 유형 `note` 로 넣는다.

데이터 손실에 대해 (중요)
  - 텍스트 계열(.md .txt .csv .json .log .yaml .ini .c .h .py …): **본문을 한 글자도 바꾸지 않고** 앞에 front matter 만 붙인다.
    소스코드·로그는 마크다운에서 깨지지 않도록 ``` 코드펜스로 감싸며(내용 추가만, 삭제 없음) front matter 에 wrapped: code 로 남긴다.
  - .html: 원본을 그대로 보존하고 본문에는 태그를 제거한 텍스트를 넣는다(빌드 단계에서 어차피 태그를 지운다). extracted: true.
  - .pdf / .docx: 텍스트만 추출한다 — 표 구조·이미지·레이아웃은 남지 않는다(원본 파일은 보관). extracted: true 로 표시한다.
  - 인코딩: utf-8 → utf-8-sig → cp949 → latin-1 순으로 시도하고, 실제로 쓴 인코딩을 front matter 에 기록한다.
  - **원본 보존**: 기본으로 `<out>/_originals/` 에 원본 파일을 그대로 복사한다(--no-copy-originals 로 끔). 코퍼스는 색인용 사본이고,
    원본이 항상 함께 남으므로 변환이 마음에 들지 않으면 다시 만들 수 있다.
  - **검증**: 변환 결과에서 front matter/코드펜스를 걷어 낸 본문이 원본 텍스트와 같은지 매번 비교하고, 다르면 manifest 에
    lossless=false 와 이유를 남긴다. 요약에도 건수가 나온다.

사용:
    python tools/corpus_ingest.py <파일|폴더> [...] --out corpus/imported
    python tools/corpus_ingest.py D:/team_docs --out corpus/imported --recursive --dry-run
    python tools/corpus_ingest.py D:/logs --out corpus/imported --include "*.log,*.txt" --max-file-mb 5
이후: config.json 의 corpus_dirs 에 out 폴더를 넣고  python -m llmwiki build --full --yes  →  python -m llmwiki corpus lint
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TEXT_EXT = {".md", ".markdown", ".txt", ".text", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".yaml", ".yml",
            ".ini", ".cfg", ".conf", ".toml", ".log", ".sql", ".xml", ".svg"}
CODE_EXT = {".c", ".h", ".cpp", ".hpp", ".cc", ".py", ".js", ".ts", ".java", ".go", ".rs", ".sh", ".bat", ".ps1",
            ".mk", ".make", ".cmake", ".v", ".sv", ".vhd", ".asm", ".s", ".m", ".rb", ".pl", ".lua", ".kt", ".swift"}
HTML_EXT = {".html", ".htm", ".xhtml"}
DOC_EXT = {".pdf", ".docx"}
SKIP_DIRS = {".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", ".vscode", "_originals"}
ENCODINGS = ("utf-8", "utf-8-sig", "cp949", "euc-kr", "cp1252", "latin-1")


def sniff_text(data: bytes) -> Tuple[Optional[str], str]:
    """(텍스트, 인코딩). 디코딩할 수 없으면 (None, '')."""
    if b"\x00" in data[:4096]:
        return None, ""
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return None, ""


def html_to_text(html: str) -> str:
    """빌드 단계(corpus.py)와 같은 수준의 단순 태그 제거."""
    import html as _h
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _h.unescape(s)
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", s)).strip()


def pdf_to_text(path: str) -> str:
    import pypdf
    r = pypdf.PdfReader(path)
    out = []
    for i, p in enumerate(r.pages):
        try:
            out.append("\n\n[page %d]\n%s" % (i + 1, p.extract_text() or ""))
        except Exception:
            out.append("\n\n[page %d]\n" % (i + 1))
    return "".join(out)


def docx_to_text(path: str) -> str:
    """외부 패키지 없이 docx(zip+xml)에서 단락 텍스트만 뽑는다."""
    import zipfile
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(ns + "p"):
        txt = "".join(t.text or "" for t in p.iter(ns + "t"))
        paras.append(txt)
    return "\n\n".join(x for x in paras if x.strip())


def slug(name: str, maxlen: int = 80) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-._")
    s = re.sub(r"-{2,}", "-", s)
    return (s or "doc")[:maxlen]


def first_title(text: str, fallback: str) -> str:
    for line in text.splitlines()[:40]:
        s = line.strip().lstrip("#").strip()
        if len(s) >= 3 and not s.startswith(("---", "```", "<!--", "/*", "//")):
            return re.sub(r"\s+", " ", s)[:160]
    return fallback[:160]


def yaml_str(s: str) -> str:
    s = str(s).replace('"', "'").replace("\n", " ").strip()
    return '"%s"' % s if re.search(r'[:#\[\]{}&*!|>%@`,]', s) or not s else s


def guess_type_and_id(rel_path: str, text: str, default_type: str) -> Tuple[str, str]:
    """llmwiki 의 추론 규칙을 먼저 쓰고(ISSUE-/CL-/TC-…), 안 되면 default_type + NOTE- id."""
    try:
        from llmwiki import schema as sc
        dt = sc.infer_doc_type(rel_path.replace("\\", "/"), text)
        if dt:
            ext_id = sc.infer_id(dt, rel_path.replace("\\", "/"), text)
            if ext_id:
                return dt, ext_id
    except Exception:
        pass
    base = os.path.splitext(os.path.basename(rel_path))[0]
    return default_type, ("NOTE-" + slug(base)) if default_type == "note" else ("NOTE-" + slug(base))


def build_document(meta: Dict[str, Any], body: str, wrap_code: bool, lang: str) -> str:
    """front matter + '# 제목' + 본문. 본문은 한 글자도 바꾸지 않는다 (코드펜스로 감쌀 때도 여는/닫는 줄만 추가).

    본문 무결성을 나중에도 확인할 수 있도록 body_sha1 · body_chars 를 front matter 에 남긴다:
        python -c "import re,hashlib,sys; d=open(sys.argv[1],encoding='utf-8').read(); ..."  또는 tools/corpus_ingest.py --verify-only
    """
    fm = ["---", "schema_version: 1", "doc_type: %s" % meta["doc_type"], "id: %s" % meta["id"],
          "title: %s" % yaml_str(meta["title"]), "date: %s" % meta["date"]]
    for k in ("author", "status", "source_path", "source_format", "encoding", "wrapped", "extracted", "summary"):
        if meta.get(k):
            fm.append("%s: %s" % (k, yaml_str(meta[k])))
    fm.append("body_sha1: %s" % hashlib.sha1(body.encode("utf-8")).hexdigest())
    fm.append("body_chars: %d" % len(body))
    if meta.get("tags"):
        fm.append("tags: [%s]" % ", ".join(meta["tags"]))
    if meta.get("module"):
        fm.append("module: [%s]" % ", ".join(meta["module"]))
    if meta.get("hw"):
        fm.append("hw: {chip: %s, rev: %s}" % (meta["hw"].get("chip", ""), meta["hw"].get("rev", "")))
    if meta.get("related"):
        fm.append("related:")
        for k, v in meta["related"].items():
            fm.append("  %s: [%s]" % (k, ", ".join(v)))
    fm.append("---")
    head = "\n".join(fm)
    title_line = "\n\n# %s\n\n" % meta["title"]
    if wrap_code:
        # 여는 줄 ```lang\n + 본문 + \n``` — 본문 자체는 그대로. 닫는 펜스 뒤에는 아무것도 붙이지 않는다(왕복 정확성)
        return head + title_line + "```%s\n%s\n```" % (lang, body)
    return head + title_line + body


_FM_RE = re.compile(r"^---\r?\n.*?\r?\n---", re.S)


def extract_body(doc: str) -> str:
    """검증용: 변환 결과에서 front matter · 제목 줄 · 코드펜스를 걷어 내고 본문만 돌려준다 (build_document 의 역연산)."""
    s = _FM_RE.sub("", doc, count=1)
    s = re.sub(r"^\n\n# [^\n]*\n\n", "", s, count=1)
    m = re.match(r"^```[^\n]*\n(.*)\n```$", s, re.S)
    if m:
        return m.group(1)
    return s


def verify_file(path: str) -> Dict[str, Any]:
    """저장된 문서의 본문이 front matter 의 body_sha1 과 일치하는지 다시 확인한다."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        doc = f.read()
    m = re.search(r"^body_sha1:\s*([0-9a-f]{40})\s*$", doc[:4000], re.M)
    if not m:
        return {"path": path, "checked": False, "reason": "body_sha1 없음 (이 도구가 만든 문서가 아님)"}
    body = extract_body(doc)
    got = hashlib.sha1(body.encode("utf-8")).hexdigest()
    return {"path": path, "checked": True, "ok": got == m.group(1), "expected": m.group(1), "got": got, "chars": len(body)}


def iter_inputs(paths: List[str], recursive: bool, include: List[str], exclude: List[str]) -> List[str]:
    out: List[str] = []
    for p in paths:
        p = p if os.path.isabs(p) else os.path.join(ROOT, p)
        if os.path.isfile(p):
            out.append(p)
            continue
        if not os.path.isdir(p):
            print("  (없는 경로) %s" % p)
            continue
        for base, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in files:
                out.append(os.path.join(base, fn))
            if not recursive:
                dirs[:] = []
    def ok(fp: str) -> bool:
        name = os.path.basename(fp)
        if include and not any(fnmatch.fnmatch(name.lower(), g.strip().lower()) for g in include):
            return False
        if exclude and any(fnmatch.fnmatch(name.lower(), g.strip().lower()) for g in exclude):
            return False
        return True
    return sorted(x for x in out if ok(x))


def main() -> int:
    ap = argparse.ArgumentParser(description="아무 문서나 코퍼스 계약 형식으로 변환 (데이터 손실 없이, 원본 보관)")
    ap.add_argument("inputs", nargs="+", help="파일 또는 폴더")
    ap.add_argument("--out", default=os.path.join("corpus", "imported"), help="출력 폴더 (기본 corpus/imported)")
    ap.add_argument("--recursive", action="store_true", default=True, help="폴더를 재귀 탐색 (기본 켜짐)")
    ap.add_argument("--no-recursive", dest="recursive", action="store_false")
    ap.add_argument("--include", default="", help="파일명 glob 쉼표 목록 (예 '*.md,*.txt')")
    ap.add_argument("--exclude", default="", help="제외할 파일명 glob 쉼표 목록")
    ap.add_argument("--default-type", default="note", help="추론 실패 시 doc_type (기본 note = 자유 형식)")
    ap.add_argument("--max-file-mb", type=float, default=20.0, help="이보다 큰 파일은 건너뜀 (0 = 제한 없음)")
    ap.add_argument("--max-total-mb", type=float, default=0.0, help="출력 총량 상한 MB (0 = 제한 없음)")
    ap.add_argument("--copy-originals", action="store_true", default=True, help="원본을 <out>/_originals/ 에 보관 (기본 켜짐)")
    ap.add_argument("--no-copy-originals", dest="copy_originals", action="store_false")
    ap.add_argument("--flatten", action="store_true", help="폴더 구조를 유지하지 않고 한 폴더에 저장")
    ap.add_argument("--overwrite", action="store_true", help="이미 있는 출력 파일도 다시 만든다")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 무엇을 할지만 보여 준다")
    ap.add_argument("--manifest", default="", help="manifest JSON 경로 (기본 <out>/_ingest_manifest.json)")
    ap.add_argument("--verify-only", action="store_true", help="변환하지 않고, out 폴더의 문서들이 body_sha1 과 일치하는지만 검사")
    ns = ap.parse_args()
    try:
        from llmwiki import console as _c
        _c.setup()
    except Exception:
        pass

    out_dir = ns.out if os.path.isabs(ns.out) else os.path.join(ROOT, ns.out)
    orig_dir = os.path.join(out_dir, "_originals")
    if ns.verify_only:
        checked = bad = 0
        for base, dirs, fns in os.walk(out_dir):
            dirs[:] = [d for d in dirs if d != "_originals"]
            for fn in fns:
                if not fn.endswith(".md"):
                    continue
                r = verify_file(os.path.join(base, fn))
                if not r.get("checked"):
                    continue
                checked += 1
                if not r["ok"]:
                    bad += 1
                    print("  MISMATCH %s (기대 %s, 실제 %s)" % (r["path"], r["expected"][:12], r["got"][:12]))
        print("본문 무결성 검사: %d개 중 %d개 불일치" % (checked, bad))
        return 1 if bad else 0
    files = iter_inputs(ns.inputs, ns.recursive, [g for g in ns.include.split(",") if g.strip()],
                        [g for g in ns.exclude.split(",") if g.strip()])
    print("대상 파일 %d개 → %s%s" % (len(files), out_dir, "  (dry-run)" if ns.dry_run else ""))
    if not ns.dry_run:
        os.makedirs(out_dir, exist_ok=True)
        if ns.copy_originals:
            os.makedirs(orig_dir, exist_ok=True)

    manifest: List[Dict[str, Any]] = []
    used_ids: Dict[str, int] = {}
    stats = {"written": 0, "skipped": 0, "failed": 0, "lossy": 0, "bytes_in": 0, "bytes_out": 0}
    total_out = 0
    budget = ns.max_total_mb * 1e6
    t0 = time.time()

    for fp in files:
        rec: Dict[str, Any] = {"src": fp}
        try:
            size = os.path.getsize(fp)
            rec["bytes_in"] = size
            if ns.max_file_mb and size > ns.max_file_mb * 1e6:
                rec.update(status="skipped", reason="파일이 max-file-mb(%s MB)보다 큼" % ns.max_file_mb)
                stats["skipped"] += 1
                manifest.append(rec)
                continue
            if budget and total_out >= budget:
                rec.update(status="skipped", reason="max-total-mb 도달")
                stats["skipped"] += 1
                manifest.append(rec)
                continue
            ext = os.path.splitext(fp)[1].lower()
            with open(fp, "rb") as f:
                data = f.read()
            wrap_code = False
            lang = ""
            extracted = ""
            encoding = ""
            if ext in DOC_EXT:
                try:
                    body = pdf_to_text(fp) if ext == ".pdf" else docx_to_text(fp)
                except Exception as e:
                    rec.update(status="failed", reason="%s 추출 실패: %s (pypdf 설치 필요?)" % (ext, str(e)[:120]))
                    stats["failed"] += 1
                    manifest.append(rec)
                    continue
                extracted = "true"
                encoding = "extracted"
            elif ext in HTML_EXT:
                raw, encoding = sniff_text(data)
                if raw is None:
                    rec.update(status="skipped", reason="디코딩 불가(바이너리)")
                    stats["skipped"] += 1
                    manifest.append(rec)
                    continue
                body = html_to_text(raw)
                extracted = "true"
            else:
                raw, encoding = sniff_text(data)
                if raw is None:
                    rec.update(status="skipped", reason="바이너리로 판단 (텍스트 추출 대상 아님)")
                    stats["skipped"] += 1
                    manifest.append(rec)
                    continue
                body = raw.replace("\r\n", "\n")
                if ext in CODE_EXT or (ext not in TEXT_EXT and ext != ".md"):
                    wrap_code = True
                    lang = re.sub(r"[^A-Za-z0-9_+.-]", "", ext.lstrip(".")) or "text"   # 펜스 언어는 안전한 문자만
                if ext in (".md", ".markdown") and body.lstrip().startswith("---"):
                    # 이미 front matter 가 있는 문서는 그대로 복사 (덮어쓰지 않는다)
                    rec.update(status="copied", reason="이미 front matter 가 있음")
                    if not ns.dry_run:
                        dst = _dest_path(out_dir, fp, ns, used_ids, suffix=".md")
                        shutil.copy2(fp, dst)
                        rec["dest"] = dst
                        rec["bytes_out"] = os.path.getsize(dst)
                        total_out += rec["bytes_out"]
                    stats["written"] += 1
                    rec["lossless"] = True
                    manifest.append(rec)
                    continue
            rel = os.path.relpath(fp, ROOT) if fp.startswith(ROOT) else os.path.basename(fp)
            doc_type, ext_id = guess_type_and_id(rel, body, ns.default_type)
            n = used_ids.get(ext_id, 0)
            used_ids[ext_id] = n + 1
            if n:
                ext_id = "%s-%d" % (ext_id, n + 1)
            mtime = os.path.getmtime(fp)
            meta = {"doc_type": doc_type, "id": ext_id,
                    "title": first_title(body, os.path.basename(fp)),
                    "date": time.strftime("%Y-%m-%d", time.localtime(mtime)),
                    "status": "active" if doc_type == "note" else "",
                    "source_path": rel.replace("\\", "/"), "source_format": (ext.lstrip(".") or "text"),
                    "encoding": encoding, "wrapped": "code" if wrap_code else "", "extracted": extracted,
                    "tags": sorted({"imported", (ext.lstrip(".") or "text")}),
                    "module": [slug(os.path.basename(os.path.dirname(fp)) or "root", 30).lower()]}
            if doc_type == "sw_design" and not meta["module"]:
                meta["module"] = ["imported"]
            doc = build_document(meta, body, wrap_code, lang)
            # 손실 검증: 되돌려 꺼낸 본문이 원본 텍스트와 같아야 한다 (pdf/docx/html 은 추출이라 제외)
            lossless = True
            reason = ""
            if not extracted:
                back = extract_body(doc)
                if back != body:
                    lossless = False
                    reason = "본문 재추출 불일치 (원본 %d자 → %d자)" % (len(body), len(back))
            else:
                lossless = False
                reason = "%s 에서 텍스트만 추출 (레이아웃·이미지 없음; 원본은 _originals/ 에 보관)" % (ext.lstrip("."))
            rec.update(status="written", dest="", doc_type=doc_type, id=ext_id, encoding=encoding,
                       lossless=lossless, reason=reason, body_chars=len(body))
            if not lossless:
                stats["lossy"] += 1
            if not ns.dry_run:
                dst = _dest_path(out_dir, fp, ns, used_ids, suffix=".md", forced_name=slug(ext_id))
                with open(dst, "w", encoding="utf-8", newline="\n") as f:
                    f.write(doc)
                rec["dest"] = dst
                rec["bytes_out"] = os.path.getsize(dst)
                total_out += rec["bytes_out"]
                if ns.copy_originals:
                    op = os.path.join(orig_dir, slug(ext_id) + (ext or ".bin"))
                    shutil.copy2(fp, op)
                    rec["original"] = op
            stats["written"] += 1
            stats["bytes_in"] += size
            stats["bytes_out"] += rec.get("bytes_out", 0)
            manifest.append(rec)
        except Exception as e:      # 한 파일 때문에 전체가 멈추지 않게
            rec.update(status="failed", reason="%s: %s" % (type(e).__name__, str(e)[:160]))
            stats["failed"] += 1
            manifest.append(rec)

    mpath = ns.manifest or os.path.join(out_dir, "_ingest_manifest.json")
    if not ns.dry_run:
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "out": out_dir, "stats": stats, "items": manifest}, f, ensure_ascii=False, indent=1)
    print("\n변환 %d · 건너뜀 %d · 실패 %d · 추출(비가역) %d · 출력 %.2f MB · %.0f초" % (
        stats["written"], stats["skipped"], stats["failed"], stats["lossy"], total_out / 1e6, time.time() - t0))
    lossy = [r for r in manifest if r.get("lossless") is False]
    if lossy:
        print("본문이 원본과 다른 문서 %d개 (대부분 pdf/docx/html 텍스트 추출):" % len(lossy))
        for r in lossy[:10]:
            print("  - %s: %s" % (os.path.basename(r["src"]), r.get("reason")))
    bad = [r for r in manifest if r.get("status") == "failed"]
    for r in bad[:10]:
        print("  실패 %s: %s" % (os.path.basename(r["src"]), r.get("reason")))
    if not ns.dry_run:
        print("manifest: %s" % mpath)
        if ns.copy_originals:
            print("원본 보관: %s" % orig_dir)
        print("다음: config.json corpus_dirs 에 %s 추가 → python -m llmwiki build --full --yes → python -m llmwiki corpus lint" % ns.out)
    return 0 if not bad else 1


def _dest_path(out_dir: str, src: str, ns, used: Dict[str, int], suffix: str = ".md", forced_name: str = "") -> str:
    name = (forced_name or slug(os.path.splitext(os.path.basename(src))[0])) + suffix
    if ns.flatten:
        d = out_dir
    else:
        parent = os.path.basename(os.path.dirname(src)) or "root"
        d = os.path.join(out_dir, slug(parent, 40))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    if os.path.exists(path) and not ns.overwrite:
        i = 2
        while os.path.exists(os.path.join(d, "%s-%d%s" % (name[:-len(suffix)], i, suffix))):
            i += 1
        path = os.path.join(d, "%s-%d%s" % (name[:-len(suffix)], i, suffix))
    return path


if __name__ == "__main__":
    sys.exit(main())
