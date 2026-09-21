# -*- coding: utf-8 -*-
"""관리자 초기화 — 세 범위(데이터/빌드 · 설정 · 로그/이력).

왜 이 모듈이 있나 (2026-09-19):
  이 폴더를 다른 환경으로 옮겨 bring-up 할 때, 앞 환경의 흔적을 지워야 한다. 그런데 "무엇을 지워야
  깨끗한가" 가 여기저기 흩어져 있었다 — 색인은 `build --reset`, 설정은 `config reset`(config.json 하나뿐),
  요청 이력은 `maintenance purge_requests`, 로그 파일은 손으로. `data/` 밑의 부산물 폴더
  (`reruns` 50개 · `sweeps` · `graph_profiles` · `requests` · `live` · `collab`)는 어느 명령도 건드리지 않아
  옮긴 환경에 그대로 따라갔다.

원칙
  1. **미리보기가 먼저다.** 모든 범위는 `preview()` 로 "무엇을 얼마나 지우는지" 를 먼저 돌려준다.
     Web 버튼도 CLI 도 이 목록을 보여 준 뒤에 실행한다.
  2. **되돌릴 수 있게.** 데이터 초기화는 지우기 전에 자동 스냅샷을 만든다(`snapshot list|restore`).
  3. **스스로 잠기지 않게.** `security.json`(계정·권한)과 `.env`(자격증명)는 **기본으로 건드리지 않는다**.
     지우려면 명시적으로 켜야 한다 — 원격에서 설정을 초기화하다 로그인이 막히면 복구할 길이 없다.
  4. **코퍼스 원본은 절대 지우지 않는다.** `corpus/` 는 사람이 넣은 자료다. 색인만 지운다.

범위
  data     색인·빌드 산출물 (문서/청크/임베딩/그래프/위키 페이지/부산물 폴더). 코퍼스 원본은 유지
  settings 설정 파일을 `setup/*.example.*` 또는 코드 기본값으로 되돌린다 (보안·자격증명 제외)
  logs     로그 파일과 이력 테이블 (질의 로그·요청·포렌식·에피소드·trial·평가 실행 기록)
"""
from __future__ import annotations

import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional

SCOPES: Dict[str, str] = {
    "data": "색인·빌드 산출물 — 문서·청크·임베딩·그래프·위키 페이지와 data/ 부산물 폴더. "
            "**코퍼스 원본(corpus/)과 설정은 그대로.** 지우기 전에 스냅샷을 만든다",
    "settings": "설정 파일을 기본값으로 — config.json · tuning.json · presets.json · query_rules.json · "
                "data/rules.json · agents.json · models.json · pins.json · mcp_sources.json · schedule.json · "
                "server.json · stopwords.json · prompts/. **security.json 과 .env 는 기본 제외**",
    "logs": "로그 파일과 이력 — logs/*.log · audit.jsonl · 질의 로그 · 요청 프로파일 · 포렌식 · 에피소드 · "
            "trial · 임베딩 실행 기록 · data/{requests,reruns,sweeps,graph_profiles,live}",
}

#: data 범위가 비우는 DB 테이블 (pipeline.reset_index 와 같은 목록 — 한 곳에서만 정의한다)
#: 여기 없는 테이블(proposals·query_log …)은 '이력' 이므로 logs 범위가 맡는다.
#: `embedding_cache` 는 내용 주소 캐시라 임베더가 그대로면 다시 쓸 수 있다 — 기본 유지, 옵션으로 삭제.
DATA_AUX_DIRS = ("requests", "reruns", "sweeps", "graph_profiles", "live")
LOG_TABLES = ("query_log", "requests", "forensics", "episodes", "trials", "embed_runs", "evolution_log")
LOG_AUX_DIRS = ("requests", "reruns", "sweeps", "graph_profiles", "live")

#: settings 범위: 설정 이름 → 되돌리는 방법.
#:   example  setup/<파일>이 있으면 그것으로 덮어쓴다 (사람이 읽을 수 있는 주석과 예시가 들어 있다)
#:   code     코드 기본값을 파일로 쓴다
#:   delete   파일을 지우면 코드 기본값이 적용된다
SETTINGS_PLAN: List[Dict[str, str]] = [
    {"name": "config", "how": "example", "example": "config.example.json"},
    {"name": "tuning", "how": "example", "example": "tuning.example.json"},
    {"name": "query_rules", "how": "example", "example": "query_rules.example.json"},
    {"name": "agents", "how": "example", "example": "agents.example.json"},
    {"name": "models", "how": "example", "example": "models.example.json"},
    {"name": "mcp_sources", "how": "example", "example": "mcp_sources.example.json"},
    {"name": "schedule", "how": "example", "example": "schedule.example.json"},
    {"name": "server", "how": "example", "example": "server.example.json"},
    {"name": "stopwords", "how": "example", "example": "stopwords.example.json"},
    {"name": "rules", "how": "code"},            # data/rules.json — graph_rules.DEFAULT_RULES
    {"name": "presets", "how": "code"},          # presets.DEFAULT_PRESETS
    {"name": "pins", "how": "code"},             # 빈 목록
]
#: 명시적으로 켜야만 초기화되는 것 (스스로 잠기거나 자격증명을 잃지 않게)
SETTINGS_GUARDED: List[Dict[str, str]] = [
    {"name": "security", "how": "example", "example": "security.example.json",
     "warn": "계정·권한·로그인 설정이 초기화됩니다 — 원격에서 실행하면 다시 로그인하지 못할 수 있습니다"},
    {"name": "docacl", "how": "example", "example": "docacl.example.json",
     "warn": "문서 접근 제어 규칙이 초기화됩니다 — 등급 문서가 모두에게 보일 수 있습니다"},
    {"name": "env", "how": "delete",
     "warn": ".env 의 API 키·PAT 가 사라집니다 (복구 불가 — 먼저 따로 보관하세요)"},
]


def _root() -> str:
    from .config import ROOT
    return ROOT


def _setup_path(fn: str) -> str:
    return os.path.join(_root(), "setup", fn)


def _size(path: str) -> int:
    try:
        if os.path.isdir(path):
            return sum(os.path.getsize(os.path.join(dp, f))
                       for dp, _dn, fs in os.walk(path) for f in fs)
        return os.path.getsize(path)
    except OSError:
        return 0


def _count_files(path: str) -> int:
    try:
        return sum(len(fs) for _dp, _dn, fs in os.walk(path)) if os.path.isdir(path) else (1 if os.path.exists(path) else 0)
    except OSError:
        return 0


def _rows(store, table: str) -> int:
    try:
        return int(store.conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])
    except Exception:
        return 0


def _item(kind: str, target: str, detail: str, count: int = 0, bytes_: int = 0, level: str = "info") -> Dict[str, Any]:
    return {"kind": kind, "target": target, "detail": detail, "count": count, "bytes": bytes_, "level": level}


# ------------------------------------------------------------------ preview
def preview(pipe, scope: str, **opts: Any) -> Dict[str, Any]:
    """무엇을 얼마나 지우는지. **아무것도 바꾸지 않는다.**"""
    scope = str(scope)
    if scope not in SCOPES:
        return {"error": "scope 는 %s 중 하나입니다" % " | ".join(SCOPES)}
    fn = {"data": _preview_data, "settings": _preview_settings, "logs": _preview_logs}[scope]
    items = fn(pipe, opts)
    return {"scope": scope, "description": SCOPES[scope], "items": items,
            "total_rows": sum(i["count"] for i in items if i["kind"] == "table"),
            "total_files": sum(i["count"] for i in items if i["kind"] in ("file", "dir")),
            "total_bytes": sum(i["bytes"] for i in items),
            "kept": _kept(scope, opts), "options": _options(scope, opts)}


def _kept(scope: str, opts: Dict[str, Any]) -> List[str]:
    """지우지 **않는** 것 — 사람이 가장 먼저 확인하고 싶어 하는 정보다."""
    if scope == "data":
        out = ["코퍼스 원본 (corpus/)", "설정 파일 전부", "질의 로그·요청 이력 (로그 범위에서 지웁니다)"]
        if not opts.get("clear_embed_cache"):
            out.append("임베딩 캐시 (embedding_cache — 같은 임베더면 재사용되어 리빌드가 빨라집니다)")
        if opts.get("keep_wiki_notes", True):
            out.append("사람이 쓴 위키 편집 노트")
        return out
    if scope == "settings":
        out = ["색인 DB 와 코퍼스", "로그·이력"]
        if not opts.get("include_security"):
            out.append("security.json (계정·권한) · docacl.json — 옵션으로 켜야 초기화됩니다")
        if not opts.get("include_env"):
            out.append(".env (API 키·PAT) — 옵션으로 켜야 삭제됩니다")
        return out
    out = ["색인 DB (문서·청크·임베딩·그래프)", "설정 파일 전부"]
    if not opts.get("include_proposals"):
        out.append("자가진화 제안 (proposals) — 사람이 검토할 후보라 기본 유지")
    if not opts.get("include_sessions"):
        out.append("로그인 세션 (sessions.json)")
    return out


def _options(scope: str, opts: Dict[str, Any]) -> Dict[str, Any]:
    if scope == "data":
        return {"snapshot": bool(opts.get("snapshot", True)), "keep_wiki_notes": bool(opts.get("keep_wiki_notes", True)),
                "clear_embed_cache": bool(opts.get("clear_embed_cache"))}
    if scope == "settings":
        return {"include_security": bool(opts.get("include_security")), "include_env": bool(opts.get("include_env"))}
    return {"include_proposals": bool(opts.get("include_proposals")), "include_sessions": bool(opts.get("include_sessions"))}


def _preview_data(pipe, opts: Dict[str, Any]) -> List[Dict[str, Any]]:
    st, s = pipe.store, pipe.s
    items: List[Dict[str, Any]] = []
    for t, label in (("docs", "문서"), ("chunks", "청크"), ("embeddings", "임베딩 벡터"),
                     ("entities", "그래프 엔티티"), ("relations", "그래프 관계"), ("mentions", "멘션"),
                     ("communities", "커뮤니티"), ("doc_meta", "문서 메타"), ("doc_vectors", "문서 벡터"),
                     ("answer_cache", "답변 캐시")):
        n = _rows(st, t)
        if n:
            items.append(_item("table", t, label, n))
    if opts.get("clear_embed_cache"):
        items.append(_item("table", "embedding_cache", "임베딩 캐시 (지우면 다음 빌드에서 전부 다시 임베딩합니다)",
                           _rows(st, "embedding_cache"), level="warn"))
    wiki = s.wiki_dir
    if os.path.isdir(wiki):
        n = len([f for f in os.listdir(wiki) if f.endswith(".md")])
        if n:
            items.append(_item("dir", wiki, "위키 페이지%s" % (" (사람이 쓴 노트는 유지)" if opts.get("keep_wiki_notes", True) else " — 노트까지 전부"),
                               n, _size(wiki)))
    for d in DATA_AUX_DIRS:
        p = os.path.join(s.data_dir, d)
        n = _count_files(p)
        if n:
            # 이 폴더들은 '로그' 범위와 겹친다. 여기서도 지우는 이유: 전부 **이 색인의 청크 id 를 가리키는**
            # 기록이라, 색인을 비우고 남겨 두면 열 수 없는 껍데기가 된다 (재실행·스윕·프로파일 모두).
            items.append(_item("dir", p, {"requests": "요청 프로파일 파일 (지운 색인을 가리킴 — 로그 범위와 겹침)",
                                          "reruns": "단계 재실행 기록 (〃)",
                                          "sweeps": "파라미터 스윕 기록 (〃)",
                                          "graph_profiles": "그래프 진단 이력 (〃)",
                                          "live": "진행 중 작업 임시 파일"}.get(d, d), n, _size(p)))
    return items


def _preview_settings(pipe, opts: Dict[str, Any]) -> List[Dict[str, Any]]:
    from .config import path_for
    items: List[Dict[str, Any]] = []
    plan = list(SETTINGS_PLAN)
    if opts.get("include_security"):
        plan += [x for x in SETTINGS_GUARDED if x["name"] in ("security", "docacl")]
    if opts.get("include_env"):
        plan += [x for x in SETTINGS_GUARDED if x["name"] == "env"]
    for spec in plan:
        try:
            target = path_for(spec["name"])
        except KeyError:
            continue
        how = spec["how"]
        src = ""
        if how == "example":
            src = _setup_path(spec["example"])
            if not os.path.exists(src):
                how, src = "code", ""
        detail = {"example": "setup/%s 로 되돌림" % spec.get("example", ""),
                  "code": "코드 기본값으로 되돌림", "delete": "파일 삭제"}[how]
        items.append(_item("file", target, detail + (" — " + spec["warn"] if spec.get("warn") else ""),
                           1 if os.path.exists(target) else 0, _size(target),
                           "warn" if spec.get("warn") else "info"))
    pd = path_for("prompts_dir")
    if os.path.isdir(pd):
        items.append(_item("dir", pd, "프롬프트 파일을 코드 기본값으로 되돌림 (사람이 고친 문구가 사라집니다)",
                           len([f for f in os.listdir(pd) if f.endswith(".md")]), _size(pd), "warn"))
    return items


def _preview_logs(pipe, opts: Dict[str, Any]) -> List[Dict[str, Any]]:
    from .config import path_for
    st, s = pipe.store, pipe.s
    items: List[Dict[str, Any]] = []
    labels = {"query_log": "질의 로그", "requests": "요청 프로파일", "forensics": "포렌식 기록",
              "episodes": "메모리 에피소드", "trials": "Trial 실행 기록", "embed_runs": "임베딩 실행 기록",
              "evolution_log": "자가진화 적용 이력"}
    for t in LOG_TABLES:
        n = _rows(st, t)
        if n:
            items.append(_item("table", t, labels.get(t, t), n))
    if opts.get("include_proposals"):
        items.append(_item("table", "proposals", "자가진화 제안 (사람이 검토할 후보입니다)", _rows(st, "proposals"), level="warn"))
    ld = path_for("logs_dir")
    if os.path.isdir(ld):
        files = [f for f in os.listdir(ld) if os.path.isfile(os.path.join(ld, f))]
        if files:
            items.append(_item("dir", ld, "로그 파일 %s" % ", ".join(sorted(files)[:6]) + (" …" if len(files) > 6 else ""),
                               len(files), _size(ld)))
    for d in LOG_AUX_DIRS:
        p = os.path.join(s.data_dir, d)
        n = _count_files(p)
        if n:
            items.append(_item("dir", p, "부산물 폴더", n, _size(p)))
    if opts.get("include_sessions"):
        sp = os.path.join(s.data_dir, "sessions.json")
        if os.path.exists(sp):
            items.append(_item("file", sp, "로그인 세션 (모든 사용자가 다시 로그인해야 합니다)", 1, _size(sp), "warn"))
    return items


# ------------------------------------------------------------------ run
def run(pipe, scope: str, actor: str = "", **opts: Any) -> Dict[str, Any]:
    """실제로 지운다. 반환에는 실행 전 `preview` 가 그대로 들어간다 (무엇을 지웠는지 기록으로 남게)."""
    from . import logging_setup as _log
    scope = str(scope)
    if scope not in SCOPES:
        return {"error": "scope 는 %s 중 하나입니다" % " | ".join(SCOPES)}
    plan = preview(pipe, scope, **opts)
    t0 = time.time()
    _log.log("warning", "reset %s by %s (%s)" % (scope, actor or "?", json.dumps(plan["options"], ensure_ascii=False)), "build")
    fn = {"data": _run_data, "settings": _run_settings, "logs": _run_logs}[scope]
    done = fn(pipe, opts, actor)
    out = {"scope": scope, "plan": plan, "done": done, "ms": round((time.time() - t0) * 1000, 1),
           "actor": actor, "ts": time.time()}
    out["next"] = _next_steps(scope, opts)
    return out


def _next_steps(scope: str, opts: Dict[str, Any]) -> List[str]:
    """초기화 다음에 사람이 해야 할 일 — bring-up 절차에서 여기가 가장 자주 막힌다."""
    if scope == "data":
        return ["`corpus/` 에 이 환경의 문서를 넣는다",
                "`python -m llmwiki corpus lint` 로 문서 계약을 확인한다",
                "`python -m llmwiki build --full --trace` 로 다시 색인한다",
                "`python -m llmwiki build verify` 로 alerts 0 을 확인한다"]
    if scope == "settings":
        return ["`config.json` 의 `corpus_dirs` 와 모델/프로바이더를 이 환경에 맞게 고친다",
                "`.env` 에 API 키 또는 PAT 를 넣는다 (초기화하지 않았다면 그대로 남아 있다)",
                "`python -m llmwiki models test --live` 로 LLM 연결을 확인한다",
                "`python -m llmwiki config show --effective` 로 유효값을 확인한다",
                "설정이 바뀌었으므로 **서버를 다시 시작**한다"]
    return ["`python -m llmwiki logs tail` 로 새 로그가 쌓이는지 확인한다",
            "평가 기준선이 필요하면 `trial run --name baseline` 을 다시 찍는다"]


def _run_data(pipe, opts: Dict[str, Any], actor: str) -> Dict[str, Any]:
    r = pipe.reset_index(keep_logs=True, keep_wiki_notes=bool(opts.get("keep_wiki_notes", True)),
                         snapshot=bool(opts.get("snapshot", True)), actor=actor,
                         snapshot_keep=int(getattr(pipe.s, "evolve_snapshot_keep", 3) or 3))
    removed_dirs = []
    for d in DATA_AUX_DIRS:
        p = os.path.join(pipe.s.data_dir, d)
        if os.path.isdir(p):
            n = _count_files(p)
            shutil.rmtree(p, ignore_errors=True)
            os.makedirs(p, exist_ok=True)
            if n:
                removed_dirs.append({"dir": p, "files": n})
    cache_cleared = 0
    if opts.get("clear_embed_cache"):
        try:
            cache_cleared = _rows(pipe.store, "embedding_cache")
            pipe.store.conn.execute("DELETE FROM embedding_cache")
            pipe.store.conn.commit()
        except Exception:
            cache_cleared = 0
    return dict(r, aux_dirs=removed_dirs, embed_cache_cleared=cache_cleared)


def _run_settings(pipe, opts: Dict[str, Any], actor: str) -> Dict[str, Any]:
    from .config import path_for
    from . import atomicio
    restored: List[Dict[str, str]] = []
    plan = list(SETTINGS_PLAN)
    if opts.get("include_security"):
        plan += [x for x in SETTINGS_GUARDED if x["name"] in ("security", "docacl")]
    if opts.get("include_env"):
        plan += [x for x in SETTINGS_GUARDED if x["name"] == "env"]
    for spec in plan:
        name = spec["name"]
        try:
            target = path_for(name)
        except KeyError:
            continue
        how, src = spec["how"], ""
        if how == "example":
            src = _setup_path(spec["example"])
            if not os.path.exists(src):
                how = "code"
        try:
            if how == "example":
                os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                shutil.copyfile(src, target)
                restored.append({"file": target, "from": "setup/" + spec["example"]})
            elif how == "delete":
                if os.path.exists(target):
                    os.remove(target)
                restored.append({"file": target, "from": "(삭제)"})
            else:
                _write_code_default(name, target, atomicio)
                restored.append({"file": target, "from": "코드 기본값"})
        except OSError as e:
            restored.append({"file": target, "from": "실패: %s" % e})
    # 프롬프트는 이름마다 기본값이 있다
    from . import prompts as _prompts
    prompts_reset = []
    for nm in list(getattr(_prompts, "DEFAULTS", {})):
        try:
            _prompts.reset(nm)
            prompts_reset.append(nm)
        except Exception:
            pass
    # 이 프로세스에도 바로 반영 (파일만 바꾸고 옛 값으로 계속 도는 일을 막는다)
    try:
        from .config import load_settings
        pipe.s = load_settings()
        pipe.reload()
        pipe.reload_tuning()
    except Exception:
        pass
    return {"restored": restored, "prompts_reset": prompts_reset,
            "note": "설정 파일은 되돌렸지만 **실행 중인 서버는 모듈을 다시 읽지 않습니다** — 서버를 재시작하세요"}


def _write_code_default(name: str, target: str, atomicio) -> None:
    if name == "rules":
        from .graph_rules import DEFAULT_RULES
        atomicio.write_json(target, json.loads(json.dumps(DEFAULT_RULES)))
    elif name == "presets":
        from .presets import DEFAULT_PRESETS
        atomicio.write_json(target, json.loads(json.dumps(DEFAULT_PRESETS)))
    elif name == "pins":
        atomicio.write_json(target, [])
    elif name == "config":
        from .config import Settings, save_settings
        save_settings(Settings(), target)
    elif name == "tuning":
        from . import tuning as _tn
        _tn.save_tuning(_tn.Tuning(), target)
    else:                                   # 기본값이 따로 없으면 파일을 지워 코드 기본값이 서게 한다
        if os.path.exists(target):
            os.remove(target)


def _run_logs(pipe, opts: Dict[str, Any], actor: str) -> Dict[str, Any]:
    from .config import path_for
    st = pipe.store
    cleared: Dict[str, int] = {}
    tables = list(LOG_TABLES) + (["proposals"] if opts.get("include_proposals") else [])
    for t in tables:
        n = _rows(st, t)
        try:
            st.conn.execute("DELETE FROM %s" % t)
            cleared[t] = n
        except Exception:
            pass
    st.conn.commit()
    try:
        st.conn.execute("VACUUM")
    except Exception:
        pass
    files = 0
    ld = path_for("logs_dir")
    if os.path.isdir(ld):
        for fn in os.listdir(ld):
            fp = os.path.join(ld, fn)
            if not os.path.isfile(fp):
                continue
            try:
                # 로그 파일은 **지우지 않고 비운다** — 돌고 있는 서버가 열어 둔 핸들을 잃지 않게.
                # Windows 에서 열린 파일을 지우면 그 뒤 모든 로그가 조용히 사라진다.
                with open(fp, "w", encoding="utf-8"):
                    pass
                files += 1
            except OSError:
                pass
    dirs = []
    for d in LOG_AUX_DIRS:
        p = os.path.join(pipe.s.data_dir, d)
        if os.path.isdir(p):
            n = _count_files(p)
            shutil.rmtree(p, ignore_errors=True)
            os.makedirs(p, exist_ok=True)
            if n:
                dirs.append({"dir": p, "files": n})
    sessions = 0
    if opts.get("include_sessions"):
        try:
            from . import auth as _auth
            _auth.reset_sessions()
            sessions = 1
        except Exception:
            pass
    return {"tables": cleared, "log_files_truncated": files, "aux_dirs": dirs, "sessions_reset": sessions}


# ------------------------------------------------------------------ 표시
def rel(path: str) -> str:
    """프로젝트 루트 기준 상대 경로 (화면이 좁아도 어느 파일인지 알아보게)."""
    try:
        return os.path.relpath(str(path), _root())
    except (ValueError, OSError):
        return str(path)


def format_preview(plan: Dict[str, Any]) -> str:
    if plan.get("error"):
        return "ERROR: " + plan["error"]
    L = ["초기화 범위: %s" % plan["scope"], "  " + plan["description"], ""]
    L.append("지웁니다 (%d행 · 파일 %d개 · %.1f MB)" % (plan["total_rows"], plan["total_files"], plan["total_bytes"] / 1048576.0))
    for i in plan["items"]:
        mark = "[!]" if i["level"] == "warn" else "   "
        amount = ("%d행" % i["count"]) if i["kind"] == "table" else ("%d개 · %.1f MB" % (i["count"], i["bytes"] / 1048576.0))
        name = i["target"] if i["kind"] == "table" else rel(i["target"])
        L.append("  %s %-30s %-14s %s" % (mark, name[:30], amount, i["detail"]))
    L.append("")
    L.append("유지합니다")
    for k in plan["kept"]:
        L.append("  · " + k)
    L.append("")
    L.append("옵션: " + json.dumps(plan["options"], ensure_ascii=False))
    return "\n".join(L)
