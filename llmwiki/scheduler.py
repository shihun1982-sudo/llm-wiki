# -*- coding: utf-8 -*-
"""스케줄러 — <루트>/schedule.json 에 적은 작업을 서버(serve) 안에서 정해진 시각/간격마다 실행한다. (2026-09-15)

schedule.json
  {"tick_s": 5, "tasks": [
     {"name": "nightly", "enabled": true, "cron": "0 3 * * *", "action": {"type": "build", "full": false}, "timeout_s": 3600, "overlap": "skip"},
     {"name": "fetch-notice", "every": "1h", "action": {"type": "fetch_url", "urls": ["https://intranet/notice.html"], "dest": "corpus/fetched", "build_after": true}},
     {"name": "digest", "at": "08:30", "days": ["mon","tue","wed","thu","fri"], "action": {"type": "query", "q": "지난주 리뷰한 CL 요약", "out": "logs/schedule/digest.md"}},
     {"name": "my-script", "every": "10m", "action": {"type": "python", "script": "tools/my_job.py", "args": ["--x"], "timeout_s": 300}},
     {"name": "opencode-skill", "cron": "0 22 * * fri", "action": {"type": "headless", "agent": "opencode", "prompt_file": "prompts/weekly_review.md", "out": "logs/schedule/review.md"}}
  ]}
언제: every("30s"|"10m"|"2h"|"1d") · at("HH:MM" + days[]) · cron("분 시 일 월 요일", * */n a-b a,b 지원) 중 하나. 시각은 config.json timezone 기준.
무엇: action.type = build | fetch_url | python | cli | query | llm | headless | mcp_ingest | maintenance | http  (ACTION_TYPES / HELP 참조)
실행: 요청 관리자 티켓(build 는 soft/exclusive, 나머지는 read)으로 실행되어 활동 목록에 보이고 취소할 수 있다. 결과는 logs/schedule.jsonl 과
      data/schedule_state.json(마지막 실행·다음 실행). 서버 없이 한 번 돌리기: python -m llmwiki schedule run <name> (OS 스케줄러용).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import atomicio
from . import progress as _pg
from .config import ROOT, path_for

ACTION_TYPES = ["build", "fetch_url", "python", "cli", "query", "llm", "headless", "mcp_ingest", "maintenance", "http",
                "evolve", "memory", "precompute", "eval", "trial", "snapshot", "wiki", "forensic", "embed_report"]
HELP: Dict[str, str] = {
    "build": "색인 빌드. {full: false|true, channels: [fts,vector,graph] | channel: 'fts'|'vector'|'graph'}. full 은 배타 실행(질의 대기), 증분은 정책상 질의 허용",
    "fetch_url": "URL 을 내려받아 코퍼스 폴더에 저장. {urls: [...] | url, dest: 'corpus/fetched', filename: '(선택) 고정 파일명', headers: {}, timeout_s: 60, build_after: true}. 내용이 같으면 건너뜀(sha1)",
    "python": "파이썬 스크립트 실행 (별도 프로세스, 현재 인터프리터). {script: 'tools/x.py', args: [], cwd: '', env: {}, timeout_s: 600}. 종료 코드 0 이 아니면 실패",
    "cli": "llmwiki CLI 명령을 이 프로세스에서 실행 — Web 콘솔과 같은 것이라 **모든 CLI 기능**을 쓸 수 있다. {argv: ['build','--full','--yes']}. 파괴적 명령은 --yes 필요",
    "query": "질의를 실행해 답변을 파일로 저장. {q: '질문', preset: 'quality', out: 'logs/schedule/<name>.md', k: 8}",
    "llm": "역할 LLM 에 프롬프트를 보내 결과를 저장. {role: 'answer', prompt: '...' | prompt_file: 'prompts/x.md', system: '', max_tokens: 2000, out: '...'}",
    "headless": "agents.json 의 headless 에이전트(opencode/claude/codex) 를 실행. {agent: 'opencode', model: '', prompt|prompt_file, files: [], out: '...'}",
    "mcp_ingest": "mcp_sources.json 소스에서 문서를 가져와(ingest) 필요하면 빌드. {sources: [...] | null, build_after: true}",
    "maintenance": "DB 유지보수. {action: vacuum|fts_optimize|wal_checkpoint|clear_cache|warm_cache|refresh_doc_refs|purge_requests}",
    "http": "웹훅/REST 호출. {url, method: 'POST', body: {}, headers: {}, timeout_s: 30}. 2xx 가 아니면 실패",
    "evolve": "자가진화. {op: 'review'(LLM 리뷰로 제안 생성) | 'consolidate'(포렌식→제안) | 'auto_apply'(신뢰도 이상 제안 자동 적용) | 'status', min_confidence: 0.9, max_apply: 5, kinds: ['pin','query_rule','tuning'], evaluate: false}. auto_apply 는 사람이 확인하지 않고 적용하므로 kinds 를 좁게 두는 것을 권한다",
    "memory": "메모리 관리. {op: 'decay'(강도 감쇠) | 'consolidate'(포렌식 누적→제안) | 'status'}",
    "precompute": "자주 묻는 질의 답변 사전 계산. {op: 'run'|'clear'|'doc_vectors', from_log: 20, stale: true}",
    "eval": "평가셋 회귀 실행. {k: 5, questions: '경로(선택)', out: 'logs/schedule/eval.md'}",
    "trial": "설정 실험 기록. {name: 'nightly', preset: 'quality', k: 5, sets: {키:값}, note: ''}",
    "snapshot": "색인 스냅샷. {op: 'create'|'prune', tag: 'nightly', keep: 3}",
    "wiki": "엔티티 위키 페이지 재생성. {min_degree: 1}",
    "forensic": "포렌식 요약/누적. {op: 'summary'|'consolidate', out: 'logs/schedule/forensic.md'}",
    "embed_report": "임베딩 coverage 리포트를 파일로. {out: 'logs/schedule/embed.md'}",
}
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
STATE_NAME = "schedule_state.json"
HISTORY_NAME = "schedule.jsonl"


def schedule_path() -> str:
    return path_for("schedule")


def state_path(data_dir: Optional[str] = None) -> str:
    if not data_dir:
        try:
            from .config import load_settings
            data_dir = load_settings().data_dir
        except Exception:
            data_dir = os.path.join(ROOT, "data")
    return os.path.join(data_dir, STATE_NAME)


def history_path() -> str:
    return os.path.join(path_for("logs_dir"), HISTORY_NAME)


# ---------------------------------------------------------------- 파일
def load_schedule() -> Dict[str, Any]:
    p = schedule_path()
    if not os.path.exists(p):
        return {"tick_s": 5, "tasks": []}
    d = atomicio.read_json(p)
    if not isinstance(d, dict):
        return {"tick_s": 5, "tasks": [], "error": "schedule.json 을 읽지 못했습니다 (JSON 형식 확인): %s" % p}
    d.setdefault("tasks", [])
    d.setdefault("tick_s", 5)
    return d


def save_tasks(tasks: List[Dict[str, Any]], extra: Optional[Dict[str, Any]] = None) -> str:
    d = load_schedule()
    d.pop("error", None)
    for t in tasks:
        validate_task(t)
    d["tasks"] = tasks
    if extra:
        d.update(extra)
    p = schedule_path()
    out = {"_comment": "서버 스케줄 작업 (docs/SCHEDULER.md). when: every | at(+days) | cron 중 하나. action.type: " + " | ".join(ACTION_TYPES) + ". 변경은 서버가 자동으로 다시 읽는다(mtime)."}
    out.update({k: v for k, v in d.items() if not k.startswith("_")})
    return atomicio.write_json(p, out)


def upsert_task(task: Dict[str, Any]) -> None:
    validate_task(task)
    d = load_schedule()
    tasks = [t for t in d["tasks"] if t.get("name") != task["name"]]
    tasks.append(task)
    save_tasks(tasks)


def remove_task(name: str) -> bool:
    d = load_schedule()
    tasks = [t for t in d["tasks"] if t.get("name") != name]
    if len(tasks) == len(d["tasks"]):
        return False
    save_tasks(tasks)
    return True


def set_enabled(name: str, enabled: bool) -> bool:
    d = load_schedule()
    hit = False
    for t in d["tasks"]:
        if t.get("name") == name:
            t["enabled"] = bool(enabled)
            hit = True
    if hit:
        save_tasks(d["tasks"])
    return hit


def validate_task(t: Dict[str, Any]) -> None:
    if not isinstance(t, dict) or not str(t.get("name") or "").strip():
        raise ValueError("task 에 name 이 필요합니다")
    if not any(k in t for k in ("every", "at", "cron")):
        raise ValueError("%s: every | at | cron 중 하나가 필요합니다" % t["name"])
    if "every" in t:
        parse_every(str(t["every"]))
    if "cron" in t:
        CronSpec(str(t["cron"]))
    if "at" in t:
        h, m = str(t["at"]).split(":")
        int(h), int(m)
        for d in t.get("days") or []:
            if str(d).lower()[:3] not in DAYS:
                raise ValueError("%s: days 는 mon..sun" % t["name"])
    act = t.get("action") or {}
    if not isinstance(act, dict) or act.get("type") not in ACTION_TYPES:
        raise ValueError("%s: action.type 은 %s 중 하나" % (t["name"], "|".join(ACTION_TYPES)))


def parse_every(s: str) -> float:
    s = str(s).strip().lower()
    unit = s[-1]
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit)
    if mult is None:
        return float(s)
    v = float(s[:-1]) * mult
    if v < 5:
        raise ValueError("every 는 최소 5초")
    return v


class CronSpec:
    """5 필드 cron: 분 시 일 월 요일 (요일 0=일요일 또는 mon..sun). * · */n · a-b · a,b · a-b/n."""

    def __init__(self, expr: str):
        parts = str(expr).split()
        if len(parts) != 5:
            raise ValueError("cron 은 5필드: 분 시 일 월 요일 (예 '0 3 * * *')")
        self.expr = expr
        self.minute = self._field(parts[0], 0, 59)
        self.hour = self._field(parts[1], 0, 23)
        self.dom = self._field(parts[2], 1, 31)
        self.month = self._field(parts[3], 1, 12)
        self.dow = self._field(parts[4], 0, 7, names=True)
        if 7 in self.dow:
            self.dow.add(0)

    @staticmethod
    def _field(f: str, lo: int, hi: int, names: bool = False) -> set:
        out: set = set()
        for part in f.split(","):
            part = part.strip().lower()
            step = 1
            if "/" in part:
                part, st = part.split("/", 1)
                step = int(st)
            if names:
                for k, v in DAYS.items():
                    part = part.replace(k, str((v + 1) % 7))   # mon=1 … sun=0
            if part in ("*", ""):
                rng = range(lo, hi + 1)
            elif "-" in part:
                a, b = part.split("-", 1)
                rng = range(int(a), int(b) + 1)
            else:
                rng = range(int(part), int(part) + 1)
            for v in rng:
                if lo <= v <= hi and (v - rng.start) % step == 0:
                    out.add(v)
        if not out:
            raise ValueError("cron 필드가 비었습니다: %s" % f)
        return out

    def matches(self, t: _dt.datetime) -> bool:
        dow = (t.weekday() + 1) % 7   # python mon=0 → cron sun=0
        return t.minute in self.minute and t.hour in self.hour and t.day in self.dom and t.month in self.month and dow in self.dow

    def next_after(self, t: _dt.datetime, max_days: int = 400) -> Optional[_dt.datetime]:
        t = t.replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)
        end = t + _dt.timedelta(days=max_days)
        while t < end:
            if t.month not in self.month:
                t = (t.replace(day=1) + _dt.timedelta(days=32)).replace(day=1, hour=0, minute=0)
                continue
            if t.day not in self.dom or ((t.weekday() + 1) % 7) not in self.dow:
                t = (t + _dt.timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if t.hour not in self.hour:
                t = (t + _dt.timedelta(hours=1)).replace(minute=0)
                continue
            if t.minute not in self.minute:
                t = t + _dt.timedelta(minutes=1)
                continue
            return t
        return None


def _tz(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name) if name else None
    except Exception:
        return None


def task_spec(t: Dict[str, Any]) -> Tuple[str, Any]:
    if "cron" in t:
        return "cron", CronSpec(str(t["cron"]))
    if "at" in t:
        h, m = str(t["at"]).split(":")
        days = [str(d).lower()[:3] for d in (t.get("days") or [])]
        dow = ",".join(days) if days else "*"
        return "cron", CronSpec("%d %d * * %s" % (int(m), int(h), dow))
    return "every", parse_every(str(t.get("every") or "1h"))


def next_run(t: Dict[str, Any], last_run: Optional[float], now: Optional[float] = None, tz: Any = None) -> Optional[float]:
    now = now or time.time()
    kind, spec = task_spec(t)
    if kind == "every":
        if last_run is None:
            return now + (0 if t.get("run_on_start") else spec)
        return last_run + spec
    base = _dt.datetime.fromtimestamp(max(now, last_run or 0) if last_run else now, tz=tz)
    nxt = spec.next_after(base)
    return nxt.timestamp() if nxt else None


def list_tasks_static() -> List[Dict[str, Any]]:
    d = load_schedule()
    st = _load_state()
    tzname = ""
    try:
        from .config import load_settings
        tzname = load_settings().timezone
    except Exception:
        pass
    tz = _tz(tzname)
    out = []
    for t in d.get("tasks", []):
        s = st.get(t.get("name"), {})
        try:
            validate_task(t)
            nr = next_run(t, s.get("last_run"), tz=tz) if t.get("enabled", True) else None
            err = ""
        except Exception as e:
            nr, err = None, str(e)
        out.append(dict(t, last_run=s.get("last_run"), last_status=s.get("last_status"), last_ms=s.get("last_ms"), last_error=s.get("last_error"),
                        last_skipped=s.get("last_skipped"), next_run=nr, running=False, invalid=err))
    if d.get("error"):
        out.append({"name": "(schedule.json)", "invalid": d["error"], "enabled": False})
    return out


def _load_state() -> Dict[str, Any]:
    try:
        d = atomicio.read_json(state_path())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_state(st: Dict[str, Any]) -> None:
    p = state_path()
    try:
        atomicio.write_json(p, st, indent=1, default=str)
    except Exception:
        pass


def read_history(n: int = 50) -> List[Dict[str, Any]]:
    p = history_path()
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()[-n:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out[::-1]


def _append_history(rec: Dict[str, Any]) -> None:
    try:
        p = history_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 실행
def _resolve(p: str) -> str:
    p = str(p or "")
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(ROOT, p))


def _read_prompt(act: Dict[str, Any]) -> str:
    if act.get("prompt_file"):
        with open(_resolve(act["prompt_file"]), "r", encoding="utf-8") as f:
            return f.read()
    return str(act.get("prompt") or "")


def _write_out(act: Dict[str, Any], name: str, text: str, default_ext: str = ".md") -> str:
    out = act.get("out") or os.path.join("logs", "schedule", "%s%s" % (name, default_ext))
    p = _resolve(out)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def run_action(pipe, task: Dict[str, Any], log=None) -> Dict[str, Any]:
    """작업 하나 실행 (스케줄러 스레드 또는 CLI `schedule run`). 반환 dict 는 이력에 기록된다."""
    act = dict(task.get("action") or {})
    typ = act.get("type")
    name = task.get("name", "?")
    note = log or (lambda m: None)
    t0 = time.time()
    if typ == "build":
        if act.get("channel"):
            res, _ = pipe.build_channel(str(act["channel"]), full=bool(act.get("full")), progress=note)
        else:
            channels = act.get("channels") or None
            res, _ = pipe.build(full=bool(act.get("full")), progress=note, channels=channels)
        return {"mode": res.get("mode"), "docs": res.get("docs"), "changed": res.get("changed"), "ms": res.get("ms"), "alerts": len(res.get("alerts") or []),
                "request_id": res.get("request_id"), "error": res.get("error")}
    if typ == "fetch_url":
        import urllib.request
        urls = list(act.get("urls") or ([act["url"]] if act.get("url") else []))
        dest = _resolve(act.get("dest") or os.path.join("corpus", "fetched"))
        os.makedirs(dest, exist_ok=True)
        written, skipped, errors = [], [], []
        for u in urls:
            try:
                req = urllib.request.Request(u, headers=dict(act.get("headers") or {}, **{"User-Agent": "llmwiki-scheduler"}))
                with urllib.request.urlopen(req, timeout=float(act.get("timeout_s") or 60)) as r:
                    data = r.read()
                    ctype = (r.headers.get("Content-Type") or "").lower()
                ext = ".pdf" if "pdf" in ctype else ".html" if "html" in ctype else ".md" if u.lower().endswith(".md") else ".txt"
                if act.get("filename") and len(urls) == 1:
                    fn = str(act["filename"])
                else:
                    base = u.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "index"
                    if "." not in base:
                        base += ext
                    fn = "%s_%s" % (hashlib.sha1(u.encode("utf-8")).hexdigest()[:8], "".join(c if (c.isalnum() or c in "-_.") else "_" for c in base)[:80])
                p = os.path.join(dest, fn)
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        if hashlib.sha1(f.read()).hexdigest() == hashlib.sha1(data).hexdigest():
                            skipped.append(fn)
                            continue
                with open(p, "wb") as f:
                    f.write(data)
                written.append(fn)
                note("fetched %s → %s (%d bytes)" % (u, fn, len(data)))
            except Exception as e:
                errors.append({"url": u, "error": str(e)[:200]})
        out: Dict[str, Any] = {"written": written, "skipped": skipped, "errors": errors, "dest": dest}
        if written and act.get("build_after", True):
            dirs = list(pipe.s.corpus_dirs)
            if not any(os.path.normcase(dest).startswith(os.path.normcase(d)) for d in dirs):
                out["warning"] = "dest 가 config corpus_dirs 밖에 있어 색인되지 않습니다 (corpus_dirs 에 추가)"
            else:
                res, _ = pipe.build(full=False, progress=note)
                out["build"] = {"changed": res.get("changed"), "ms": res.get("ms"), "request_id": res.get("request_id")}
        if errors and not written:
            raise RuntimeError("fetch failed: %s" % errors[0]["error"])
        return out
    if typ == "python":
        script = _resolve(act.get("script") or "")
        if not os.path.exists(script):
            raise FileNotFoundError("script not found: %s" % script)
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (act.get("env") or {}).items()})
        env.setdefault("PYTHONIOENCODING", "utf-8")
        cwd = _resolve(act.get("cwd") or ROOT)
        proc = subprocess.run([sys.executable, script] + [str(a) for a in (act.get("args") or [])], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=float(act.get("timeout_s") or 600), cwd=cwd, env=env)
        note("python exit %s" % proc.returncode)
        out = {"exit": proc.returncode, "stdout": (proc.stdout or "")[-2000:], "stderr": (proc.stderr or "")[-1000:]}
        if proc.returncode != 0:
            raise RuntimeError("script exit %s: %s" % (proc.returncode, (proc.stderr or proc.stdout or "")[-300:]))
        return out
    if typ == "cli":
        from .cli import run_captured
        argv = list(act.get("argv") or [])
        if not argv:
            raise ValueError("cli: argv 필요")
        r = run_captured(argv, pipe.s, pipe, actor="schedule:%s" % name)
        note("cli exit %s" % r["code"])
        if r["code"] != 0:
            raise RuntimeError("cli exit %s: %s" % (r["code"], (r["output"] or "")[-300:]))
        return {"exit": r["code"], "output": (r["output"] or "")[-2000:]}
    if typ == "query":
        q = str(act.get("q") or "")
        if not q:
            raise ValueError("query: q 필요")
        names = [x for x in str(act.get("preset") or "").replace(";", ",").split(",") if x.strip()]
        ov = {"top_k_final": int(act["k"])} if act.get("k") else None
        with pipe.request_scope(overrides=ov, presets=names):
            res, _ = pipe.query(q, log=bool(act.get("log", True)))
        text = "# %s\n\n_%s · request #%s · %s_\n\n%s\n" % (q, time.strftime("%Y-%m-%d %H:%M"), res.get("request_id"), res.get("answer_mode"), res.get("answer", ""))
        p = _write_out(act, name, text)
        return {"request_id": res.get("request_id"), "answer_mode": res.get("answer_mode"), "out": p, "ms": res.get("ms")}
    if typ in ("llm", "headless"):
        prompt = _read_prompt(act)
        if not prompt:
            raise ValueError("%s: prompt 또는 prompt_file 필요" % typ)
        if typ == "llm":
            llm = pipe.llm_for(str(act.get("role") or "answer"))
        else:
            from .headless import HeadlessAgentLLM
            from .providers import apply_policy
            llm = HeadlessAgentLLM(str(act.get("agent") or "opencode"), str(act.get("model") or ""), pipe.s)
            llm.role = "schedule"
            apply_policy(llm, pipe.s, None)
        if not llm.available:
            raise RuntimeError("LLM unavailable: %s (%s)" % (llm.name, getattr(llm, "reason", "")))
        r = llm.complete(str(act.get("system") or "You are a helpful assistant."), prompt, max_tokens=int(act.get("max_tokens") or 2000),
                         effort=str(act.get("effort") or "medium"), files=[_resolve(f) for f in (act.get("files") or [])] or None)
        p = _write_out(act, name, r.get("text") or "")
        return {"model": r.get("model"), "ms": r.get("ms"), "usage": r.get("usage"), "out": p, "chars": len(r.get("text") or "")}
    if typ == "mcp_ingest":
        from . import mcp_client as _mc
        ing = _mc.ingest(pipe.s, act.get("sources") or None)
        out = {"written": ing["written"], "skipped": ing["skipped"], "errors": ing["errors"][:5]}
        if ing["written"] and act.get("build_after", True):
            res, _ = pipe.build(full=False, progress=note)
            out["build"] = {"changed": res.get("changed"), "ms": res.get("ms")}
        return out
    if typ == "maintenance":
        return pipe.maintenance(str(act.get("action") or "wal_checkpoint"))
    if typ == "evolve":
        from . import evolve as _ev
        op = str(act.get("op") or "review")
        if op == "status":
            st = _ev.status(pipe)
            return {"pending": len(st.get("pending") or []), "applied": len(st.get("applied") or []), "auto_apply": st.get("auto_apply")}
        if op == "review":
            r = _ev.llm_review(pipe)
            note("evolve review: 제안 %s" % len(r.get("proposals") or []))
            return {"proposals": r.get("proposals"), "error": r.get("error")}
        if op == "consolidate":
            from . import memory as _mem
            from . import tuning as _tn
            r = _mem.consolidate(pipe.store, _tn.T.get("forensic_min_events"))
            note("forensic 누적 → 제안 %s" % r.get("created"))
            return r
        if op == "auto_apply":
            # 사람이 확인하지 않고 적용하므로: 신뢰도 하한 · 종류 제한 · 개수 상한을 모두 지킨다
            min_conf = float(act.get("min_confidence") or pipe.s.evolve_min_confidence or 0.9)
            # 종류 제한은 evolve 쪽 한 곳에서 정한다 (config.json evolve_auto_apply_kinds, 비우면 안전한 기본값).
            kinds = [str(k) for k in (act.get("kinds") or _ev.auto_apply_kinds(pipe.s))]
            limit = int(act.get("max_apply") or 5)
            # 2026-09-19: 회귀 평가 기본값을 **켬**으로 바꿨다. 사람이 안 보는 경로인데 평가를 끄면
            # 악화되어도 롤백이 돌지 않아, 검색 품질이 조용히 나빠진 채로 쌓인다. 끄려면 태스크에 evaluate:false 를 적는다.
            evaluate = bool(act.get("evaluate", True))
            applied, skipped, errors = [], 0, []
            for p in pipe.store.proposals("proposed"):
                if len(applied) >= limit:
                    break
                if float(p.get("confidence") or 0) < min_conf or p.get("kind") not in kinds:
                    skipped += 1
                    continue
                try:
                    r = _ev.apply_proposal(pipe, int(p["id"]), evaluate=evaluate)
                    applied.append({"id": p["id"], "kind": p["kind"], "ok": not r.get("error"), "detail": str(r)[:120]})
                    note("제안 #%s(%s) 적용" % (p["id"], p["kind"]))
                except Exception as e:      # 한 건 실패가 전체를 막지 않게
                    errors.append({"id": p.get("id"), "error": str(e)[:200]})
            return {"applied": applied, "skipped": skipped, "errors": errors, "min_confidence": min_conf,
                    "kinds": kinds, "evaluate": evaluate}
        raise ValueError("evolve op 은 review|consolidate|auto_apply|status")
    if typ == "memory":
        from . import memory as _mem
        from . import tuning as _tn
        op = str(act.get("op") or "decay")
        if op == "decay":
            return _mem.decay(pipe.store, _tn.T.get("memory_half_life_days"), _tn.T.get("memory_archive_strength"))
        if op == "consolidate":
            return _mem.consolidate(pipe.store, _tn.T.get("forensic_min_events"))
        if op == "status":
            return _mem.status(pipe.store, _tn.T.get("memory_half_life_days"))
        raise ValueError("memory op 은 decay|consolidate|status")
    if typ == "precompute":
        from . import precompute as _pc
        op = str(act.get("op") or "run")
        if op == "run":
            return _pc.run(pipe, questions=act.get("questions"), from_log=int(act.get("from_log") or 20), progress=note)
        if op == "clear":
            return {"removed": _pc.clear_cache(pipe.store, stale_only=bool(act.get("stale", True)))}
        if op == "doc_vectors":
            return _pc.build_doc_vectors(pipe)
        raise ValueError("precompute op 은 run|clear|doc_vectors")
    if typ == "eval":
        from .evalset import load_questions
        qs = load_questions(act["questions"]) if act.get("questions") else None
        r, _tr = pipe.evaluate(k=int(act.get("k") or 5), questions=qs, log=False)
        sm = r["summary"]
        if act.get("out"):
            lines = ["# 평가 %s" % time.strftime("%Y-%m-%d %H:%M"), "", "요약: %s" % json.dumps(sm, ensure_ascii=False), ""]
            lines += ["- %s rank=%s term=%.2f  %s" % ("HIT" if x["hit"] else "MISS", x["rank"], x["term_recall"], x["q"]) for x in r["rows"]]
            _write_out(act, name, "\n".join(lines))
        note("eval hit@%s=%.3f mrr=%.3f" % (act.get("k") or 5, sm.get("hit@k", 0), sm.get("mrr", 0)))
        return {"summary": sm, "out": act.get("out")}
    if typ == "trial":
        from . import trials as _tr
        tname = str(act.get("name") or ("sched-%s" % time.strftime("%m%d-%H%M")))
        r = _tr.run_trial(pipe, tname, None, k=int(act.get("k") or 5), preset=act.get("preset"),
                          overrides=act.get("sets") or None, note=str(act.get("note") or "scheduled"))
        return {"trial": tname, "summary": r.get("summary")}
    if typ == "snapshot":
        from . import snapshots as _snap
        op = str(act.get("op") or "create")
        if op == "create":
            r = _snap.create(pipe, str(act.get("tag") or "auto:schedule"), actor="scheduler", reason="scheduled snapshot")
            _snap.prune(pipe, keep=int(act.get("keep") or 3))
            return {"name": r.get("name"), "bytes": r.get("bytes")}
        if op == "prune":
            return {"removed": _snap.prune(pipe, keep=int(act.get("keep") or 3))}
        raise ValueError("snapshot op 은 create|prune")
    if typ == "wiki":
        from .wiki import write_wiki
        from .profiler import Profiler
        from . import tuning as _tn
        prof = Profiler("schedule", debug=0, log=False)
        r = write_wiki(pipe.store, pipe.s.wiki_dir, prof, min_degree=int(act.get("min_degree") or _tn.T.get("wiki_min_degree")), prune=True, only=None)
        return r
    if typ == "forensic":
        from . import forensic as _fx
        op = str(act.get("op") or "summary")
        if op == "summary":
            s = _fx.summary(pipe.store)
            if act.get("out"):
                _write_out(act, name, "# 포렌식 요약 %s\n\n```json\n%s\n```\n" % (time.strftime("%Y-%m-%d %H:%M"), json.dumps(s, ensure_ascii=False, indent=1)))
            return s
        if op == "consolidate":
            from . import memory as _mem
            from . import tuning as _tn
            return _mem.consolidate(pipe.store, _tn.T.get("forensic_min_events"))
        raise ValueError("forensic op 은 summary|consolidate")
    if typ == "embed_report":
        from .embed_run import embed_report
        r = embed_report(pipe)
        if act.get("out"):
            _write_out(act, name, "# 임베딩 리포트 %s\n\n```json\n%s\n```\n" % (time.strftime("%Y-%m-%d %H:%M"), json.dumps(r, ensure_ascii=False, indent=1, default=str)))
        return {"coverage": (r.get("coverage") or {}), "out": act.get("out")}
    if typ == "http":
        import urllib.request
        body = act.get("body")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        headers.update(act.get("headers") or {})
        req = urllib.request.Request(str(act.get("url")), data=data, headers=headers, method=str(act.get("method") or ("POST" if data else "GET")))
        with urllib.request.urlopen(req, timeout=float(act.get("timeout_s") or 30)) as r:
            return {"status": r.status, "body": r.read().decode("utf-8", "ignore")[:500]}
    raise ValueError("unknown action type: %s" % typ)


def action_weight(task: Dict[str, Any]) -> Tuple[str, str]:
    """(요청 관리자 티켓 가중치, build_mode). read=질의와 나란히 · soft=다른 쓰기만 배제 · exclusive=질의까지 대기."""
    act = task.get("action") or {}
    typ = act.get("type")
    if typ == "build":
        return ("exclusive", "full") if act.get("full") else ("soft", "channel" if act.get("channel") else "incremental")
    if typ in ("fetch_url", "mcp_ingest"):
        return ("soft", "incremental") if act.get("build_after", True) else ("read", "")
    if typ == "cli":
        argv = list(act.get("argv") or [])
        if argv and argv[0] == "build":
            return ("exclusive", "full") if any(a in ("--full", "--reset", "fts", "vector", "graph") for a in argv) else ("soft", "incremental")
        if argv and argv[0] in ("config", "models", "tuning", "snapshot", "evolve", "security", "users", "apikey"):
            return "exclusive", ""
        return "read", ""
    if typ in ("maintenance", "snapshot"):
        return "exclusive", ""
    if typ == "evolve":
        # 제안 적용은 색인·설정을 바꾼다(스냅샷·리빌드 유발 가능) → 배타. 조회/리뷰는 읽기.
        return ("exclusive", "") if str(act.get("op") or "review") == "auto_apply" else ("read", "")
    if typ in ("memory", "wiki", "precompute"):
        return ("soft", "") if str(act.get("op") or "") != "status" else ("read", "")
    return "read", ""


class Scheduler:
    def __init__(self, pipe, mgr=None):
        self.pipe = pipe
        self.mgr = mgr
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.tasks: List[Dict[str, Any]] = []
        self.state: Dict[str, Any] = _load_state()
        self.running: Dict[str, Dict[str, Any]] = {}     # name → {token, started}
        self._mtime: Optional[float] = None
        self.tick_s = 5.0
        self.errors: List[str] = []
        self._hist: List[Dict[str, Any]] = read_history(100)
        self.reload()

    # ---- 설정 ----
    def reload(self) -> None:
        d = load_schedule()
        with self._lock:
            self.tick_s = float(d.get("tick_s") or 5)
            self.tasks = []
            self.errors = [d["error"]] if d.get("error") else []
            for t in d.get("tasks", []):
                try:
                    validate_task(t)
                    self.tasks.append(t)
                except Exception as e:
                    self.errors.append("%s: %s" % (t.get("name"), e))
            try:
                self._mtime = os.path.getmtime(schedule_path())
            except OSError:
                self._mtime = None
            tz = _tz(getattr(self.pipe.s, "timezone", "") or "")
            now = time.time()
            for t in self.tasks:
                st = self.state.setdefault(t["name"], {})
                if t.get("enabled", True):
                    st["next_run"] = next_run(t, st.get("last_run"), now, tz)
                else:
                    st["next_run"] = None
            _save_state(self.state)

    def _maybe_reload(self) -> None:
        try:
            mt = os.path.getmtime(schedule_path())
        except OSError:
            mt = None
        if mt != self._mtime:
            self.reload()

    # ---- 스레드 ----
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.tick_s):
            try:
                self._maybe_reload()
                now = time.time()
                for t in list(self.tasks):
                    if not t.get("enabled", True):
                        continue
                    st = self.state.get(t["name"], {})
                    nr = st.get("next_run")
                    if nr and now >= nr:
                        self._launch(t, by="schedule")
            except Exception as e:
                self.errors.append("loop: %s" % str(e)[:200])

    def _launch(self, t: Dict[str, Any], by: str = "schedule") -> Optional[str]:
        name = t["name"]
        with self._lock:
            if name in self.running:
                if str(t.get("overlap") or "skip") == "skip":
                    self.state.setdefault(name, {})["last_skipped"] = time.time()
                    tz = _tz(getattr(self.pipe.s, "timezone", "") or "")
                    self.state[name]["next_run"] = next_run(t, time.time(), time.time(), tz)
                    _append_history({"ts": time.time(), "name": name, "status": "skipped", "reason": "previous run still running", "by": by})
                    return None
            token = "sch-%s-%s" % ("".join(c if c.isalnum() else "_" for c in name)[:30], uuid.uuid4().hex[:6])
            self.running[name] = {"token": token, "started": time.time(), "by": by}
        th = threading.Thread(target=self._run, args=(t, token, by), daemon=True, name="schedule-" + name)
        th.start()
        return token

    def _run(self, t: Dict[str, Any], token: str, by: str) -> None:
        name = t["name"]
        weight, build_mode = action_weight(t)
        client = {"user": "scheduler", "role": "builder", "via": "schedule", "ip": "", "origin": "schedule", "agent": by}
        t0 = time.time()
        rec: Dict[str, Any] = {"ts": t0, "name": name, "by": by, "token": token, "type": (t.get("action") or {}).get("type")}
        log_lines: List[str] = []

        def note(m: str) -> None:
            log_lines.append("%s %s" % (time.strftime("%H:%M:%S"), str(m)[:200]))
            _pg.note(str(m)[:200])
        status, err, result = "done", "", None
        try:
            if self.mgr is not None:
                cm = self.mgr.ticket("schedule", weight, client=client, label="schedule:%s" % name, token=token,
                                     timeout_s=float(t.get("timeout_s") or 0) or None, build_mode=build_mode)
            else:
                cm = _NullTicket(token, name)
            with cm:
                with self.pipe.request_scope():
                    result = run_action(self.pipe, t, note)
        except _pg.Cancelled as e:
            status, err = "cancelled", str(e)[:300]
        except Exception as e:
            status, err = "error", ("%s: %s" % (type(e).__name__, e))[:300]
        ms = round((time.time() - t0) * 1000, 1)
        rec.update(status=status, ms=ms, error=err, result=result, log=log_lines[-20:])
        _append_history(rec)
        with self._lock:
            self.running.pop(name, None)
            st = self.state.setdefault(name, {})
            st.update(last_run=t0, last_status=status, last_ms=ms, last_error=err, last_result=(json.dumps(result, ensure_ascii=False, default=str)[:500] if result is not None else None))
            tz = _tz(getattr(self.pipe.s, "timezone", "") or "")
            st["next_run"] = next_run(t, t0, time.time(), tz) if t.get("enabled", True) else None
            _save_state(self.state)
            self._hist.insert(0, rec)
            del self._hist[200:]
        try:
            from . import logging_setup as _ls
            _ls.log("info" if status == "done" else "warning", "schedule %s %s (%.0f ms) %s" % (name, status, ms, err), "watch", task=name, status=status)
        except Exception:
            pass

    # ---- 조회/제어 ----
    def run_now(self, name: str, by: str = "manual") -> str:
        t = next((x for x in self.tasks if x["name"] == name), None)
        if not t:
            raise KeyError("no such task: %s" % name)
        tok = self._launch(t, by=by)
        if not tok:
            raise ValueError("이미 실행 중입니다: %s" % name)
        return tok

    def list_tasks(self) -> List[Dict[str, Any]]:
        with self._lock:
            out = []
            for t in self.tasks:
                st = self.state.get(t["name"], {})
                r = self.running.get(t["name"])
                out.append(dict(t, last_run=st.get("last_run"), last_status=st.get("last_status"), last_ms=st.get("last_ms"), last_error=st.get("last_error"),
                                # last_skipped: 앞 실행이 안 끝나 건너뛴 시각 (overlap=skip). "왜 안 돌았나" 의 답이라 목록에 싣는다.
                                last_skipped=st.get("last_skipped"),
                                last_result=st.get("last_result"), next_run=st.get("next_run"), running=bool(r), token=(r or {}).get("token"),
                                weight=action_weight(t)[0], invalid=""))
            for e in self.errors:
                out.append({"name": "(invalid)", "invalid": e, "enabled": False})
            return out

    def history(self, n: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._hist[:n])

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            nxt = [(self.state.get(t["name"], {}).get("next_run"), t["name"]) for t in self.tasks if t.get("enabled", True)]
            nxt = sorted([x for x in nxt if x[0]])
            return {"tasks": len(self.tasks), "enabled": sum(1 for t in self.tasks if t.get("enabled", True)), "running": list(self.running.keys()),
                    "next": {"name": nxt[0][1], "at": nxt[0][0]} if nxt else None, "errors": list(self.errors)}


class _NullTicket:
    """서버 없이(CLI schedule run) 실행할 때: progress 에만 bind."""

    def __init__(self, token: str, name: str):
        self.token, self.name = token, name

    def __enter__(self):
        _pg.bind(self.token, "schedule", "schedule:%s" % self.name, client={"origin": "cli", "user": "scheduler"})
        return self

    def __exit__(self, et, ev, tb):
        _pg.unbind("done" if et is None else ("cancelled" if et is _pg.Cancelled else "error"), str(ev or "")[:200])
        return False
