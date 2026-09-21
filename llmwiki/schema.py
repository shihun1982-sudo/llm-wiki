# -*- coding: utf-8 -*-
"""Corpus contract / document schema.

- 문서는 마크다운 + YAML front matter (`---` 블록). pyyaml 이 있으면 사용, 없으면 내장 미니 YAML 파서(스칼라·리스트·맵·중첩).
- 스키마는 schemas/<doc_type>.json (없으면 기본값 생성). common.json 을 상속(extends). infer.json 은 front matter 가 없는
  문서의 유형/ID 를 경로·파일명·본문 패턴으로 추론한다. schema_version 은 migrations.json 규칙으로 최신 버전으로 올린다.
- lint: 필수 필드·enum·ID 형식·날짜·섹션 계약 검사 → {level: error|warn, field, msg}.
- normalize_meta(): 빌드가 doc_meta 테이블에 넣는 정규화 메타 (doc_type, ext_id, date, tags, modules, hw, related…).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import path_for

CURRENT_SCHEMA_VERSION = 1

DEFAULT_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "common": {
        "title": "모든 문서 공통 필드",
        "fields": {
            "schema_version": {"type": "int", "required": True, "desc": "스키마 버전 (현재 1)"},
            "doc_type": {"type": "string", "required": True, "desc": "issue | cl | sw_design | hw_design | coding_rule | weekly_report | tc_list | (확장)"},
            "id": {"type": "string", "required": True, "desc": "문서 ID (유형별 규칙). 그래프 노드 ID 가 됨"},
            "title": {"type": "string", "required": True, "desc": "제목"},
            "date": {"type": "date", "required": True, "desc": "기준일 YYYY-MM-DD (시간 질의·최신성)"},
            "author": {"type": "string", "required": False},
            "status": {"type": "string", "required": False, "desc": "유형별 enum"},
            "tags": {"type": "list", "required": False, "desc": "검색 태그 (FTS 토큰에 포함)"},
            "module": {"type": "list", "required": False, "desc": "SW 모듈/컴포넌트 (code map 연결)"},
            "hw": {"type": "map", "required": False, "desc": "{chip, rev}"},
            "related": {"type": "map", "required": False, "desc": "{issues:[], cls:[], docs:[], tcs:[]} → explicit 관계"},
            "summary": {"type": "string", "required": False, "desc": "한 줄 요약 (없으면 첫 문단)"},
        },
        "sections": {"recommended": [], "required": []},
    },
    "issue": {
        "title": "이슈 문서 (현상 · 원인 · 분석 · 수정)", "extends": "common",
        "id_pattern": "^ISSUE-\\d{3,7}$",
        "fields": {"status": {"type": "enum", "values": ["open", "analyzing", "fixed", "verified", "closed", "wontfix"], "required": True},
                   "severity": {"type": "enum", "values": ["critical", "major", "minor", "trivial"], "required": False},
                   "related": {"type": "map", "required": False, "desc": "cls 에 수정 CL 나열"}},
        "sections": {"recommended": ["현상", "원인", "분석", "수정", "검증"], "required": ["현상"]},
    },
    "cl": {
        "title": "Change List (수정 반영)", "extends": "common",
        "id_pattern": "^CL-\\d{3,8}$",
        "fields": {"related": {"type": "map", "required": True, "desc": "issues 필수 (CL 은 반드시 이슈 번호를 명시)"},
                   "status": {"type": "enum", "values": ["review", "merged", "reverted"], "required": False},
                   "files": {"type": "list", "required": False, "desc": "변경 파일 목록"}},
        "required_related": ["issues"],
        "sections": {"recommended": ["변경 내용", "영향 범위", "테스트"], "required": []},
    },
    "sw_design": {
        "title": "SW 설계 문서 (아키텍처 · code map · 리뷰 규칙)", "extends": "common",
        "id_pattern": "^SWD-[A-Z0-9_-]+$",
        "fields": {"status": {"type": "enum", "values": ["draft", "approved", "deprecated"], "required": False},
                   "module": {"type": "list", "required": True}},
        "sections": {"recommended": ["개요", "구조", "인터페이스", "동작 흐름", "제약"], "required": []},
    },
    "hw_design": {
        "title": "HW 설계 문서 (타이밍 · 레지스터 · revision history)", "extends": "common",
        "id_pattern": "^HWD-[A-Z0-9_-]+$",
        "fields": {"hw": {"type": "map", "required": True, "desc": "{chip, rev} 필수"},
                   "status": {"type": "enum", "values": ["draft", "released", "obsolete"], "required": False}},
        "sections": {"recommended": ["개요", "레지스터", "타이밍", "SW 제어 시퀀스", "Revision History"], "required": ["Revision History"]},
    },
    "coding_rule": {
        "title": "코딩 규칙 가이드", "extends": "common",
        "id_pattern": "^RULE-[A-Z0-9_-]+$",
        "fields": {"status": {"type": "enum", "values": ["active", "deprecated"], "required": False},
                   "scope": {"type": "list", "required": False, "desc": "적용 범위 (모듈/언어)"}},
        "sections": {"recommended": ["규칙", "근거", "예시", "예외"], "required": ["규칙"]},
    },
    "weekly_report": {
        "title": "주간 업무 보고", "extends": "common",
        "id_pattern": "^WR-\\d{4}-W\\d{2}(-[A-Za-z0-9_]+)?$",
        "fields": {"period": {"type": "map", "required": True, "desc": "{from, to}"}},
        "sections": {"recommended": ["업무 요약", "이슈 요약", "CL 리뷰 요약", "다음 주 계획"], "required": []},
    },
    "tc_list": {
        "title": "검증 TC 목록", "extends": "common",
        "id_pattern": "^TC-[A-Z0-9_-]+$",
        "fields": {"status": {"type": "enum", "values": ["draft", "active", "retired"], "required": False}},
        "sections": {"recommended": ["목적", "사전 조건", "절차", "기대 결과"], "required": []},
    },
}

DEFAULT_INFER: Dict[str, Any] = {
    "_comment": "front matter 가 없는 문서의 유형/ID 추론 규칙. 위에서부터 첫 매치. glob 은 doc_id(코퍼스 루트 기준 경로) 대상.",
    "rules": [
        {"glob": "*issue*/*", "doc_type": "issue"}, {"glob": "*/issues/*", "doc_type": "issue"}, {"filename_regex": "^ISSUE[-_]", "doc_type": "issue"},
        {"glob": "*cl*/*", "doc_type": "cl"}, {"filename_regex": "^CL[-_]?\\d", "doc_type": "cl"},
        {"glob": "*sw*design*/*", "doc_type": "sw_design"}, {"filename_regex": "^SWD[-_]", "doc_type": "sw_design"},
        {"glob": "*hw*design*/*", "doc_type": "hw_design"}, {"filename_regex": "^HWD[-_]", "doc_type": "hw_design"},
        {"glob": "*coding*rule*/*", "doc_type": "coding_rule"}, {"filename_regex": "^RULE[-_]", "doc_type": "coding_rule"},
        {"glob": "*weekly*/*", "doc_type": "weekly_report"}, {"filename_regex": "^WR[-_]", "doc_type": "weekly_report"},
        {"glob": "*tc*/*", "doc_type": "tc_list"}, {"filename_regex": "^TC[-_]", "doc_type": "tc_list"},
    ],
    "id_from_text": {"issue": "\\bISSUE-\\d{3,7}\\b", "cl": "\\bCL-\\d{3,8}\\b", "sw_design": "\\bSWD-[A-Z0-9_-]+\\b",
                     "hw_design": "\\bHWD-[A-Z0-9_-]+\\b", "coding_rule": "\\bRULE-[A-Z0-9_-]+\\b", "weekly_report": "\\bWR-\\d{4}-W\\d{2}\\b",
                     "tc_list": "\\bTC-[A-Z0-9_-]+\\b"},
    "date_from_filename": ["(20\\d{2})[-_.]?(\\d{2})[-_.]?(\\d{2})"],
}

DEFAULT_MIGRATIONS: Dict[str, Any] = {
    "_comment": "schema_version 마이그레이션. 'from->to': {rename: {old: new}, default: {field: value}}. 빌드 시 메타를 최신 버전으로 정규화(파일은 수정하지 않음).",
    "0->1": {"rename": {"type": "doc_type", "created": "date", "issue_id": "id"}, "default": {"schema_version": 1}},
}

_CACHE: Dict[str, Any] = {"mtime": None, "schemas": None, "infer": None, "migrations": None}


# ---------------------------------------------------------------- YAML (mini)
_SCALAR_TRUE = ("true", "yes", "on")
_SCALAR_FALSE = ("false", "no", "off")


def _scalar(v: str) -> Any:
    s = v.strip()
    if s == "" or s in ("~", "null", "None"):
        return None
    if (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'"):
        return s[1:-1]
    if s.lower() in _SCALAR_TRUE:
        return True
    if s.lower() in _SCALAR_FALSE:
        return False
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_scalar(x) for x in _split_flow(inner)] if inner else []
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        out: Dict[str, Any] = {}
        for item in _split_flow(inner):
            k, _, vv = item.partition(":")
            out[k.strip().strip('"').strip("'")] = _scalar(vv)
        return out
    if re.match(r"^-?\d+$", s):
        return int(s)
    if re.match(r"^-?\d+\.\d+$", s):
        return float(s)
    return s


def _split_flow(s: str) -> List[str]:
    out, depth, cur, quote = [], 0, "", None
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            cur += ch
        elif ch in "[{":
            depth += 1
            cur += ch
        elif ch in "]}":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [x.strip() for x in out]


def _parse_block(lines: List[str], i: int, indent: int) -> Tuple[Any, int]:
    """indent 수준의 블록을 파싱. 반환 (값, 다음 줄 index)."""
    result: Any = None
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.strip().startswith("#"):
            i += 1
            continue
        cur_indent = len(raw) - len(raw.lstrip(" "))
        if cur_indent < indent:
            break
        if cur_indent > indent and result is None:
            indent = cur_indent
        line = raw.strip()
        if line.startswith("- "):
            if result is None:
                result = []
            if not isinstance(result, list):
                break
            item = line[2:].strip()
            if ":" in item and not item.startswith(("[", "{", '"', "'")) and not re.match(r"^https?:", item):
                # "- key: value" 로 시작하는 맵 항목
                sub_lines = [(" " * (cur_indent + 2)) + item] + lines[i + 1:]
                val, consumed = _parse_block(sub_lines, 0, cur_indent + 2)
                result.append(val)
                i += consumed   # sub_lines 의 첫 줄은 현재 줄
                continue
            result.append(_scalar(item))
            i += 1
            continue
        if result is None:
            result = {}
        if not isinstance(result, dict):
            break
        key, _, val = line.partition(":")
        key = key.strip().strip('"').strip("'")
        val = val.strip()
        if val == "" or val == "|" or val == ">":
            # 중첩 블록 또는 리터럴
            if val in ("|", ">"):
                j = i + 1
                buf = []
                while j < len(lines) and (not lines[j].strip() or (len(lines[j]) - len(lines[j].lstrip(" "))) > cur_indent):
                    buf.append(lines[j].strip())
                    j += 1
                result[key] = ("\n" if val == "|" else " ").join(buf).strip()
                i = j
                continue
            sub, j = _parse_block(lines, i + 1, cur_indent + 1)
            result[key] = sub if sub is not None else None
            i = j
            continue
        result[key] = _scalar(val)
        i += 1
    return result, i


def parse_yaml(text: str) -> Any:
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        pass
    except Exception:
        pass
    val, _ = _parse_block(text.splitlines(), 0, 0)
    return val


_FM_RE = re.compile(r"^﻿?\s*---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.S)


def parse_front_matter(text: str) -> Tuple[Dict[str, Any], str, bool]:
    """(meta, body, had_front_matter)"""
    m = _FM_RE.match(text or "")
    if not m:
        return {}, text, False
    try:
        meta = parse_yaml(m.group(1))
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, text[m.end():], True


def dump_front_matter(meta: Dict[str, Any]) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, (list, dict)):
            lines.append("%s: %s" % (k, json.dumps(v, ensure_ascii=False)))
        elif isinstance(v, str) and (":" in v or v.strip() != v or v == ""):
            lines.append("%s: %s" % (k, json.dumps(v, ensure_ascii=False)))
        else:
            lines.append("%s: %s" % (k, v))
    lines.append("---")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 스키마 로드
def schemas_dir() -> str:
    return path_for("schemas_dir")


def ensure_defaults() -> List[str]:
    d = schemas_dir()
    os.makedirs(d, exist_ok=True)
    made = []
    for name, sc in DEFAULT_SCHEMAS.items():
        p = os.path.join(d, name + ".json")
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                json.dump(sc, f, ensure_ascii=False, indent=2)
            made.append(name)
    for name, data in (("infer", DEFAULT_INFER), ("migrations", DEFAULT_MIGRATIONS)):
        p = os.path.join(d, name + ".json")
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            made.append(name)
    return made


def _load_all() -> None:
    d = schemas_dir()
    ensure_defaults()
    try:
        mt = max(os.path.getmtime(os.path.join(d, f)) for f in os.listdir(d) if f.endswith(".json"))
    except (ValueError, OSError):
        mt = 0
    if _CACHE["mtime"] == mt and _CACHE["schemas"] is not None:
        return
    schemas: Dict[str, Dict[str, Any]] = {}
    infer: Dict[str, Any] = dict(DEFAULT_INFER)
    migrations: Dict[str, Any] = dict(DEFAULT_MIGRATIONS)
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, f), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        name = f[:-5]
        if name == "infer":
            infer = data
        elif name == "migrations":
            migrations = data
        else:
            schemas[name] = data
    # extends 해석
    resolved: Dict[str, Dict[str, Any]] = {}
    for name, sc in schemas.items():
        fields: Dict[str, Any] = {}
        base = sc.get("extends")
        if base and base in schemas:
            fields.update(json.loads(json.dumps(schemas[base].get("fields", {}))))
        for k, v in (sc.get("fields") or {}).items():
            fields[k] = dict(fields.get(k, {}), **v)
        resolved[name] = dict(sc, fields=fields)
    _CACHE.update(mtime=mt, schemas=resolved, infer=infer, migrations=migrations)


def load_schemas() -> Dict[str, Dict[str, Any]]:
    _load_all()
    return _CACHE["schemas"]


def doc_types() -> List[str]:
    return [k for k in load_schemas() if k != "common"]


def infer_rules() -> Dict[str, Any]:
    _load_all()
    return _CACHE["infer"]


# ---------------------------------------------------------------- 추론 · 정규화
def _title_line(text: str) -> str:
    """본문의 첫 제목 줄(# …) 또는 첫 비어있지 않은 줄 — ID 추론은 여기서만 (본문 중간의 ID 언급은 참조일 뿐)."""
    for line in (text or "").splitlines()[:20]:
        s = line.strip()
        if s:
            return s.lstrip("#").strip()
    return ""


def infer_doc_type(doc_id: str, text: str) -> Optional[str]:
    rules = infer_rules()
    fname = os.path.basename(doc_id)
    for r in rules.get("rules", []):
        if "glob" in r and fnmatch.fnmatch(doc_id.lower(), r["glob"].lower()):
            return r["doc_type"]
        if "filename_regex" in r and re.search(r["filename_regex"], fname, re.I):
            return r["doc_type"]
    title = _title_line(text)
    for dt, pat in (rules.get("id_from_text") or {}).items():
        if re.match(r"^\W*(?:%s)" % pat.replace("\\b", ""), title):
            return dt
    return None


def infer_id(doc_type: Optional[str], doc_id: str, text: str) -> Optional[str]:
    rules = infer_rules()
    pat = (rules.get("id_from_text") or {}).get(doc_type or "")
    fname = os.path.basename(doc_id)
    if pat:
        m = re.search(pat, fname) or re.match(r"^\W*(%s)" % pat.replace("\\b", ""), _title_line(text))
        if m:
            return m.group(1) if m.lastindex else m.group(0)
    return None


def _norm_date(v: Any) -> Optional[str]:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    s = str(v).strip()
    m = re.match(r"^(20\d{2})[-./년]\s?(\d{1,2})[-./월]\s?(\d{1,2})일?", s)
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(20\d{2})(\d{2})(\d{2})$", s)
    if m:
        return "%s-%s-%s" % m.groups()
    return None


def date_from_filename(doc_id: str) -> Optional[str]:
    for pat in infer_rules().get("date_from_filename", []):
        m = re.search(pat, os.path.basename(doc_id))
        if m:
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 1 <= mo <= 12 and 1 <= d <= 31:
                    return "%04d-%02d-%02d" % (y, mo, d)
            except (ValueError, IndexError):
                pass
    return None


def migrate_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    _load_all()
    meta = dict(meta)
    ver = meta.get("schema_version")
    try:
        ver = int(ver) if ver is not None else 0
    except (TypeError, ValueError):
        ver = 0
    mig = _CACHE["migrations"] or {}
    while ver < CURRENT_SCHEMA_VERSION:
        rule = mig.get("%d->%d" % (ver, ver + 1)) or {}
        for old, new in (rule.get("rename") or {}).items():
            if old in meta and new not in meta:
                meta[new] = meta.pop(old)
        for k, v in (rule.get("default") or {}).items():
            meta.setdefault(k, v)
        ver += 1
        meta["schema_version"] = ver
    return meta


def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in re.split(r"[,;]", v) if x.strip()]
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v)]


def normalize_meta(fm: Dict[str, Any], doc_id: str, title: str, text: str, mtime: float = 0.0) -> Dict[str, Any]:
    """빌드가 doc_meta 에 저장하는 정규화 메타. front matter 가 없으면 추론(inferred=True)."""
    had = bool(fm)
    meta = migrate_meta(fm) if fm else {}
    doc_type = str(meta.get("doc_type") or "").strip().lower() or (infer_doc_type(doc_id, text) or "")
    ext_id = str(meta.get("id") or "").strip() or (infer_id(doc_type or None, doc_id, text) or "")
    date = _norm_date(meta.get("date")) if meta.get("date") is not None else None
    date_source = "front_matter" if date else ""
    if not date:
        date = date_from_filename(doc_id)
        date_source = "filename" if date else ""
    if not date and mtime:
        date = time.strftime("%Y-%m-%d", time.localtime(mtime))
        date_source = "mtime"
    related_raw = meta.get("related") or {}
    related: Dict[str, List[str]] = {}
    if isinstance(related_raw, dict):
        for k, v in related_raw.items():
            related[str(k)] = _as_list(v)
    elif related_raw:
        related["docs"] = _as_list(related_raw)
    hw = meta.get("hw") if isinstance(meta.get("hw"), dict) else {}
    period = meta.get("period") if isinstance(meta.get("period"), dict) else {}
    ts = 0.0
    if date:
        try:
            ts = time.mktime(time.strptime(date, "%Y-%m-%d"))
        except (ValueError, OverflowError):
            ts = 0.0
    return {"doc_id": doc_id, "doc_type": doc_type, "ext_id": ext_id, "title": str(meta.get("title") or title or ""),
            "date": date or "", "ts": ts, "date_source": date_source, "author": str(meta.get("author") or ""),
            "status": str(meta.get("status") or ""), "tags": _as_list(meta.get("tags")), "modules": _as_list(meta.get("module") or meta.get("modules")),
            "hw_chip": str(hw.get("chip") or ""), "hw_rev": str(hw.get("rev") or ""), "related": related,
            "period_from": _norm_date(period.get("from")) or "", "period_to": _norm_date(period.get("to")) or "",
            "summary": str(meta.get("summary") or ""), "schema_version": int(meta.get("schema_version") or 0) if had else 0,
            # `acl:` 는 문서가 스스로 매기는 접근 등급 (`acl: class1` 또는 `acl: [class1, admin]`).
            # extra 가 아니라 정식 필드로 뽑아야 doc_meta 의 전용 컬럼에 들어가고, 질의마다 값싸게 읽힌다 (llmwiki/docacl.py).
            "acl": _as_list(meta.get("acl")) if isinstance(meta.get("acl"), (list, tuple)) else str(meta.get("acl") or "").strip(),
            "inferred": not had, "extra": {k: v for k, v in meta.items() if k not in (
                "schema_version", "doc_type", "id", "title", "date", "author", "status", "tags", "module", "modules", "hw", "related", "period", "summary", "acl")}}


def meta_tokens(nm: Dict[str, Any]) -> str:
    """FTS 토큰 컬럼에 추가할 메타 토큰 (ID·유형·태그·모듈·HW·관련 ID) — 모든 청크에 붙어 'ISSUE-2041 원인?' 같은 질의가 문서 전체를 찾게 함."""
    toks: List[str] = []
    if nm.get("ext_id"):
        toks += [nm["ext_id"].lower(), nm["ext_id"].lower().replace("-", "")]
    if nm.get("doc_type"):
        toks.append("doctype_" + nm["doc_type"])
    toks += [t.lower() for t in nm.get("tags", [])]
    toks += [t.lower() for t in nm.get("modules", [])]
    if nm.get("hw_chip"):
        toks.append(nm["hw_chip"].lower())
    if nm.get("hw_rev"):
        toks.append("rev_" + nm["hw_rev"].lower())
    for lst in (nm.get("related") or {}).values():
        toks += [x.lower() for x in lst]
    if nm.get("date"):
        toks.append(nm["date"].replace("-", ""))
    return " ".join(dict.fromkeys(toks))


# ---------------------------------------------------------------- lint
_HEAD_RE = re.compile(r"^#{1,6}\s+(.*)$", re.M)


def lint_document(fm: Dict[str, Any], nm: Dict[str, Any], body: str, had_fm: bool) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    schemas = load_schemas()
    dt = nm.get("doc_type") or ""
    if not had_fm:
        issues.append({"level": "warn", "field": "front_matter", "msg": "front matter 없음 (유형/ID/날짜는 추론됨: %s / %s / %s)" % (dt or "?", nm.get("ext_id") or "?", nm.get("date_source") or "-")})
    if not dt:
        issues.append({"level": "error" if had_fm else "warn", "field": "doc_type", "msg": "doc_type 을 알 수 없음"})
        return issues
    sc = schemas.get(dt)
    if not sc:
        issues.append({"level": "warn", "field": "doc_type", "msg": "schemas/%s.json 없음 (알 수 없는 유형 — 확장하려면 스키마 파일 추가)" % dt})
        return issues
    meta = migrate_meta(fm) if fm else {}
    if had_fm and int(fm.get("schema_version") or 0) != CURRENT_SCHEMA_VERSION:
        issues.append({"level": "warn", "field": "schema_version", "msg": "schema_version=%s (현재 %d, 빌드 시 자동 마이그레이션)" % (fm.get("schema_version"), CURRENT_SCHEMA_VERSION)})
    for fname, spec in (sc.get("fields") or {}).items():
        val = meta.get(fname)
        present = val not in (None, "", [], {})
        if fname == "id":
            present = bool(nm.get("ext_id"))
            val = nm.get("ext_id")
        if fname == "date":
            present = bool(nm.get("date")) and nm.get("date_source") == "front_matter"
        if fname == "title":
            present = bool(nm.get("title"))
        if spec.get("required") and not present:
            lvl = "error" if had_fm else "warn"
            if fname in ("date",) and nm.get("date"):
                lvl = "warn"
            issues.append({"level": lvl, "field": fname, "msg": "필수 필드 누락%s" % ((" (추론값: %s)" % nm.get("date")) if fname == "date" and nm.get("date") else "")})
            continue
        if not present:
            continue
        t = spec.get("type")
        if t == "enum" and str(val) not in [str(x) for x in spec.get("values", [])]:
            issues.append({"level": "error", "field": fname, "msg": "허용값 아님: %s (허용 %s)" % (val, spec.get("values"))})
        elif t == "list" and not isinstance(val, (list, tuple, str)):
            issues.append({"level": "error", "field": fname, "msg": "리스트여야 함"})
        elif t == "map" and not isinstance(val, dict):
            issues.append({"level": "error", "field": fname, "msg": "맵({})이어야 함"})
        elif t == "date" and fname == "date" and not _norm_date(val):
            issues.append({"level": "error", "field": fname, "msg": "날짜 형식 YYYY-MM-DD 필요: %s" % val})
        elif t == "int" and not isinstance(val, int):
            issues.append({"level": "error", "field": fname, "msg": "정수여야 함"})
    pat = sc.get("id_pattern")
    if pat and nm.get("ext_id") and not re.match(pat, nm["ext_id"]):
        issues.append({"level": "error" if had_fm else "warn", "field": "id", "msg": "ID 형식 불일치: %s (규칙 %s)" % (nm["ext_id"], pat)})
    for key in sc.get("required_related", []):
        if not (nm.get("related") or {}).get(key):
            issues.append({"level": "error" if had_fm else "warn", "field": "related.%s" % key, "msg": "related.%s 필수 (예: CL → 이슈 번호)" % key})
    # `acl:` 은 유형 스키마에 없는 공통 필드다. 오타(acl: class-1)는 조용히 무시돼 문서가 공개된 채로 남으므로
    # 빌드 린트에서 알려 준다 (llmwiki/docacl.py · docs/SECURITY.md).
    if nm.get("acl"):
        from .docacl import RANK as _ROLE_RANK
        bad = [x for x in ([nm["acl"]] if isinstance(nm["acl"], str) else list(nm["acl"])) if str(x) not in _ROLE_RANK]
        if bad:
            issues.append({"level": "error", "field": "acl",
                           "msg": "알 수 없는 역할: %s (허용 %s) — 이 값은 무시되어 문서가 공개로 남습니다" % (", ".join(map(str, bad)), ", ".join(_ROLE_RANK))})
    heads = [h.strip().lower() for h in _HEAD_RE.findall(body or "")]
    secs = sc.get("sections") or {}
    for req in secs.get("required", []):
        if not any(req.lower() in h for h in heads):
            issues.append({"level": "warn", "field": "sections", "msg": "필수 섹션 '## %s' 없음" % req})
    missing_rec = [r for r in secs.get("recommended", []) if not any(r.lower() in h for h in heads)]
    if missing_rec and len(missing_rec) >= max(2, len(secs.get("recommended", [])) // 2):
        issues.append({"level": "info", "field": "sections", "msg": "권장 섹션 누락: %s" % ", ".join(missing_rec)})
    return issues


def lint_summary(pipe) -> Dict[str, Any]:
    """마지막 빌드에서 저장한 lint 집계 (kv lint_summary)."""
    return pipe.store.kv_get("lint_summary") or {"docs": 0, "errors": 0, "warnings": 0}


def example_document(doc_type: str) -> str:
    """유형별 예시 문서 (계약 문서/CLI corpus example)."""
    sc = load_schemas().get(doc_type)
    if not sc:
        return ""
    today = time.strftime("%Y-%m-%d")
    ex_id = {"issue": "ISSUE-2041", "cl": "CL-55321", "sw_design": "SWD-RX-DMA-03", "hw_design": "HWD-PHY-TIMING-B1", "coding_rule": "RULE-ISR-001",
             "weekly_report": "WR-2026-W36", "tc_list": "TC-RX-DMA-007"}.get(doc_type, doc_type.upper() + "-001")
    meta: Dict[str, Any] = {"schema_version": CURRENT_SCHEMA_VERSION, "doc_type": doc_type, "id": ex_id, "title": "(제목)", "date": today,
                            "author": "user.id", "tags": ["rx", "dma"], "module": ["rx_dma"], "hw": {"chip": "MDM9x", "rev": "B1"}}
    if doc_type == "issue":
        meta.update(status="fixed", severity="major", related={"cls": ["CL-55321"], "issues": []})
    elif doc_type == "cl":
        meta.update(status="merged", related={"issues": ["ISSUE-2041"]}, files=["drivers/rx_dma.c"])
    elif doc_type == "hw_design":
        meta.update(status="released")
    elif doc_type == "weekly_report":
        meta.update(period={"from": today, "to": today})
    body = "\n".join("## %s\n\n(내용)\n" % s for s in (sc.get("sections") or {}).get("recommended", ["개요"]))
    return dump_front_matter(meta) + "\n# %s %s\n\n%s" % (ex_id, "(제목)", body)
