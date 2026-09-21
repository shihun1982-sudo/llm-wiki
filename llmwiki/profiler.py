# -*- coding: utf-8 -*-
"""단계별 프로파일러 (디버그 레벨 · 카운터 · 토큰/SQL 집계).

    prof = Profiler("query", debug=1)
    with prof.stage("fts_search", query=q) as st:
        ...
        st.note(hits=len(rows))            # 항상 기록되는 요약 메타
        st.debug(match=match)              # debug>=1 일 때만 기록 (상세 디버그)
        st.sample(prompt=prompt_text)      # debug>=2 일 때만 기록 (프롬프트/응답 원문 등 큰 페이로드)
        st.log("phase 2 start")            # 타임스탬프 로그 (debug>=1)

각 stage 는 시작 offset, 소요 ms, 그 사이 발생한 SQL 문 수 / LLM 호출 수 / 토큰 수를 자동으로 기록한다.
Profiler.finish() 는 트리 JSON 과 root.summary(총 ms, 단계별 비율, 토큰 합계, 느린 단계 Top) 를 돌려준다.
"""
from __future__ import annotations

import threading
import time
import traceback
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

# 카운터: Store(SQL) 와 providers(LLM) 가 증가시키고 Stage 가 진입/종료 시점의 차이를 기록한다.
# 요청(스레드) 단위로 분리한다 — 여러 질의가 동시에 실행될 때 서로의 SQL/LLM/토큰 수가 섞이지 않도록 (2026-09-15 병렬화).
# COUNTERS 는 프로세스 전체 누적(관측용), _TL.counters 는 현재 스레드 누적(Stage 가 읽는 값).
COUNTERS: Dict[str, float] = {"sql": 0, "llm_calls": 0, "llm_input_tokens": 0, "llm_output_tokens": 0,
                              "embed_calls": 0, "embed_texts": 0}
_CLOCK = threading.Lock()
_TL = threading.local()


def _tl_counters() -> Dict[str, float]:
    c = getattr(_TL, "counters", None)
    if c is None:
        c = _TL.counters = {}
    return c


def note_current(**kw: Any) -> bool:
    """**지금 열려 있는 단계**에 메모를 남긴다 (없으면 조용히 무시, 반환 False).

    왜 필요한가 (2026-09-20): 앙상블은 역할 11개 어디에나 켤 수 있는데, 그 사실을 trace 에 남기려면
    LLM 을 부르는 **모든 자리**(answer·rerank·extract·summary·review·expand·verify·forensic·fusion·select)
    에 같은 코드를 넣어야 했다. 한 자리만 빠뜨려도 그 역할은 "앙상블인지 알 수 없는" 상태가 된다 —
    이 저장소에서 반복해 겪은 유형이다. 그래서 **프로바이더 한 곳**에서 지금 단계에 직접 적게 한다.

    스레드 로컬이라 요청마다 격리된다(`Profiler.stage` 가 진입/이탈에서 세우고 되돌린다).
    """
    st = getattr(_TL, "stage", None)
    if st is None:
        return False
    try:
        st.note(**kw)
        return True
    except Exception:
        return False


def count(key: str, n: float = 1) -> None:
    c = _tl_counters()
    c[key] = c.get(key, 0) + n
    with _CLOCK:
        COUNTERS[key] = COUNTERS.get(key, 0) + n


def _snapshot() -> Dict[str, float]:
    return dict(_tl_counters())


def totals() -> Dict[str, float]:
    """프로세스 전체 누적 카운터 (서버 모니터용)."""
    with _CLOCK:
        return dict(COUNTERS)


class Stage:
    def __init__(self, name: str, meta: Optional[Dict[str, Any]] = None, debug: int = 0, t_origin: float = 0.0):
        self.name = name
        self.meta: Dict[str, Any] = dict(meta or {})
        self.dbg: Dict[str, Any] = {}
        self.samples: Dict[str, Any] = {}
        self.debug_level = debug
        self.t0 = time.perf_counter()
        self.t1: Optional[float] = None
        self.offset_ms = (self.t0 - t_origin) * 1000.0 if t_origin else 0.0
        self.children: List["Stage"] = []
        self.error: Optional[str] = None
        self.enabled = True
        self.replayed = False        # 계산하지 않고 저장해 둔 결과를 재생했다 (단계 재실행 — rerun.py)
        self.logs: List[str] = []
        self._c0 = _snapshot()
        self.counters: Dict[str, float] = {}

    # ---- 기록 API ----
    def note(self, **kw: Any) -> None:
        self.meta.update(kw)

    def debug(self, **kw: Any) -> None:
        if self.debug_level >= 1:
            self.dbg.update(kw)

    def sample(self, **kw: Any) -> None:
        if self.debug_level >= 2:
            self.samples.update(kw)

    def log(self, msg: str) -> None:
        if self.debug_level >= 1:
            self.logs.append("%.1fms %s" % ((time.perf_counter() - self.t0) * 1000, msg))

    def close(self) -> None:
        self.t1 = time.perf_counter()
        c1 = _snapshot()
        self.counters = {k: c1.get(k, 0) - self._c0.get(k, 0) for k in c1 if c1.get(k, 0) - self._c0.get(k, 0)}

    @property
    def ms(self) -> float:
        end = self.t1 if self.t1 is not None else time.perf_counter()
        return (end - self.t0) * 1000.0

    @property
    def self_ms(self) -> float:
        return max(0.0, self.ms - sum(c.ms for c in self.children))

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name, "ms": round(self.ms, 2), "self_ms": round(self.self_ms, 2),
                             "offset_ms": round(self.offset_ms, 2), "enabled": self.enabled}
        if self.replayed:
            d["replayed"] = True
        if self.meta:
            d["meta"] = jsonable(self.meta)
        if self.counters:
            d["counters"] = jsonable(self.counters)
        if self.dbg:
            d["debug"] = jsonable(self.dbg)
        if self.samples:
            d["samples"] = jsonable(self.samples)
        if self.error:
            d["error"] = self.error
        if self.logs:
            d["logs"] = self.logs
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


def jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [jsonable(v) for v in o]
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except Exception:
        pass
    if hasattr(o, "to_dict"):
        return jsonable(o.to_dict())
    if hasattr(o, "keys") and hasattr(o, "__getitem__"):  # sqlite3.Row
        try:
            return {k: jsonable(o[k]) for k in o.keys()}
        except Exception:
            pass
    return str(o)


def _new_run_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


class Profiler:
    """name = 요청 종류(query/build/eval/search/…). run_id 는 요청마다 발급되어 logs/ 의 모든 레코드와 requests 행에 함께 저장된다."""

    def __init__(self, name: str = "root", debug: int = 0, log: bool = True, run_id: Optional[str] = None):
        self.debug = int(debug or 0)
        self.root = Stage(name, debug=self.debug)
        self.root.offset_ms = 0.0
        self._stack: List[Stage] = [self.root]
        self._lock = threading.Lock()
        self.run_id = run_id or _new_run_id()
        self.log_enabled = bool(log)
        self._finished = False
        try:
            from . import logging_setup as _ls
            _ls.push_run(self.run_id, name)
            if self.log_enabled:
                _ls.log("info", "%s start" % name, "stage", run_id=self.run_id)
        except Exception:
            pass

    def _emit(self, st: Stage) -> None:
        if not self.log_enabled:
            return
        try:
            from . import logging_setup as _ls
            meta = {k: v for k, v in st.meta.items() if isinstance(v, (int, float, str, bool)) or v is None}
            s = str(jsonable(meta))
            data = {"stage": st.name, "ms": round(st.ms, 1), "enabled": st.enabled, "meta": s[:400]}
            if st.counters:
                data["counters"] = dict(st.counters)
            if st.error:
                _ls.log("error", "%s failed: %s" % (st.name, st.error), "stage", **data)
            elif not st.enabled:
                _ls.log("debug", "%s skipped: %s" % (st.name, st.meta.get("reason", "")), "stage", **data)
            else:
                _ls.log("info", "%s done %.1fms" % (st.name, st.ms), "stage", **data)
        except Exception:
            pass

    @contextmanager
    def stage(self, name: str, **meta: Any):
        st = Stage(name, meta, debug=self.debug, t_origin=self.root.t0)
        with self._lock:
            self._stack[-1].children.append(st)
            self._stack.append(st)
        prev_tl = getattr(_TL, "stage", None)
        _TL.stage = st                    # 깊은 곳(프로바이더)에서 지금 단계에 메모를 남길 수 있게 (note_current)
        _pg = None
        try:
            try:
                from . import progress as _pg   # 실시간 진행 표시 (Web 폴링용) — 바인딩된 스레드에서만 기록
                _pg.stage_enter(name, meta)     # 취소 요청이 있으면 여기서 progress.Cancelled (BaseException) 가 난다
            except Exception:
                _pg = None
            yield st
        except BaseException as e:
            st.error = "%s: %s" % (type(e).__name__, e)
            if isinstance(e, Exception):
                st.logs.append(traceback.format_exc()[-800:])
            raise
        finally:
            _TL.stage = prev_tl
            st.close()
            with self._lock:
                if self._stack and self._stack[-1] is st:
                    self._stack.pop()
            self._emit(st)
            if _pg is not None:
                try:
                    _pg.stage_exit(name, st.ms, st.error or "")
                except Exception:
                    pass

    def skipped(self, name: str, reason: str = "disabled") -> None:
        st = Stage(name, {"reason": reason}, debug=self.debug, t_origin=self.root.t0)
        st.enabled = False
        st.t1 = st.t0
        self._stack[-1].children.append(st)
        self._emit(st)

    def replayed(self, name: str, source: str = "", **meta: Any) -> None:
        """이 단계는 계산하지 않고 **저장해 둔 결과를 재생**했다 (단계 재실행).

        `skipped` 와 달리 `enabled` 는 True 로 둔다 — 그 단계의 결과는 실제로 쓰였기 때문이다.
        꺼져 있어서 건너뛴 것과 재생한 것을 화면에서 구분할 수 있어야 한다.
        """
        st = Stage(name, dict(meta, replay_source=source or "checkpoint"), debug=self.debug, t_origin=self.root.t0)
        st.replayed = True
        st.t1 = st.t0
        with self._lock:
            self._stack[-1].children.append(st)
        self._emit(st)

    @property
    def current(self) -> Stage:
        return self._stack[-1]

    def finish(self) -> Dict[str, Any]:
        self.root.close()
        d = self.root.to_dict()
        d["summary"] = self.summary()
        d["debug_level"] = self.debug
        d["run_id"] = self.run_id
        if not self._finished:
            self._finished = True
            try:
                from . import logging_setup as _ls
                if self.log_enabled:
                    sm = d["summary"]
                    _ls.log("info", "%s finish %.1fms" % (self.root.name, self.root.ms), "stage", run_id=self.run_id,
                            total_ms=sm["total_ms"], llm_calls=sm["llm"]["calls"], tokens=sm["llm"]["total_tokens"],
                            sql=sm["sql_statements"], errors=[e["name"] for e in sm["errors"]], skipped=sm["skipped"])
                _ls.pop_run(self.run_id)
            except Exception:
                pass
        return d

    # ---- 집계 ----
    def summary(self) -> Dict[str, Any]:
        total = self.root.ms or 1.0
        flat = self.flat()
        tokens_in = sum(f["counters"].get("llm_input_tokens", 0) for f in flat if f["depth"] == 1)
        tokens_out = sum(f["counters"].get("llm_output_tokens", 0) for f in flat if f["depth"] == 1)
        calls = sum(f["counters"].get("llm_calls", 0) for f in flat if f["depth"] == 1)
        sql = sum(f["counters"].get("sql", 0) for f in flat if f["depth"] == 1)
        top = sorted((f for f in flat if f["depth"] >= 1 and f["enabled"]), key=lambda f: -f["ms"])[:5]
        return {
            "total_ms": round(total, 2),
            "stages": [{"name": f["name"], "ms": f["ms"], "pct": round(100.0 * f["ms"] / total, 1), "enabled": f["enabled"]}
                       for f in flat if f["depth"] == 1],
            "slowest": [{"name": f["name"], "ms": f["ms"], "pct": round(100.0 * f["ms"] / total, 1)} for f in top],
            "skipped": [f["name"] for f in flat if not f["enabled"]],
            "errors": [{"name": f["name"], "error": f["error"]} for f in flat if f["error"]],
            "llm": {"calls": int(calls), "input_tokens": int(tokens_in), "output_tokens": int(tokens_out),
                    "total_tokens": int(tokens_in + tokens_out)},
            "sql_statements": int(sql),
        }

    def flat(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []

        def walk(s: Stage, depth: int) -> None:
            out.append({"name": s.name, "depth": depth, "ms": round(s.ms, 2), "self_ms": round(s.self_ms, 2),
                        "offset_ms": round(s.offset_ms, 2), "enabled": s.enabled, "error": s.error,
                        "meta": jsonable(s.meta), "counters": dict(s.counters)})
            for c in s.children:
                walk(c, depth + 1)
        walk(self.root, 0)
        return out


def flatten_trace(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    """저장된 trace JSON 을 평면 리스트로 (UI 표/CLI 출력용)."""
    out: List[Dict[str, Any]] = []

    def walk(n: Dict[str, Any], depth: int) -> None:
        out.append({"name": n.get("name"), "depth": depth, "ms": n.get("ms", 0), "self_ms": n.get("self_ms", n.get("ms", 0)),
                    "offset_ms": n.get("offset_ms", 0), "enabled": n.get("enabled", True), "error": n.get("error"),
                    "meta": n.get("meta") or {}, "counters": n.get("counters") or {}, "debug": n.get("debug") or {},
                    "samples": n.get("samples") or {}, "logs": n.get("logs") or []})
        for c in n.get("children", []):
            walk(c, depth + 1)
    walk(trace, 0)
    return out
