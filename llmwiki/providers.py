# -*- coding: utf-8 -*-
"""LLM / 임베딩 프로바이더.

Python 3.7 환경(공식 anthropic SDK 설치 불가: jiter>=0.4 는 3.8+)을 고려해 표준 라이브러리 urllib 로
Anthropic Messages API 를 직접 호출한다. SDK 가 설치 가능한 환경(3.9+)이라면 AnthropicSDKLLM 이 자동 선택된다.

LLM:   none | mock | anthropic | ollama
Embed: hash (로컬, 문자 n-gram 해싱; 오프라인) | voyage | ollama | st(sentence-transformers)
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import random
import re
import socket
import threading
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .textutil import words, normalize_token, char_ngrams, stable_hash
from .profiler import count as _count
from . import progress as _pg

DEFAULT_LLM_TIMEOUT = 600   # make_llm 이 settings.llm_timeout 으로 인스턴스별 timeout 을 덮어쓴다
DEFAULT_LLM_RETRIES = 3     # timeout/네트워크/실행 실패(transient) 재시도 횟수 (config.json llm_retries)
DEFAULT_RETRY_BACKOFF_S = 2.0


# =============================== LLM ===============================
class LLMError(RuntimeError):
    """transient=True 면 재시도 대상(타임아웃·네트워크·프로세스 실행 실패·빈 출력). HTTP 4xx(인증·모델명 오류)처럼 다시 해도 같은 결과면 False."""

    def __init__(self, msg: str = "", transient: bool = False, kind: str = ""):
        RuntimeError.__init__(self, msg)
        self.transient = bool(transient)
        self.kind = kind or ("transient" if transient else "error")


# ---- LLM 실패 사건(incident) 기록: 요청(스레드) 단위로 모아 결과의 llm_report 에 싣는다 ----
_INCIDENTS = threading.local()


def reset_incidents() -> None:
    _INCIDENTS.items = []


def record_incident(item: Dict[str, Any]) -> None:
    items = getattr(_INCIDENTS, "items", None)
    if items is None:
        _INCIDENTS.items = items = []
    items.append(item)


def drain_incidents() -> List[Dict[str, Any]]:
    items = list(getattr(_INCIDENTS, "items", None) or [])
    _INCIDENTS.items = []
    return items


# ---- 회로 차단기(circuit breaker): provider/model 별 연속 최종 실패 수 — 프로세스 전체(모든 스레드) 공유 ----
# 죽은 게이트웨이에 30명이 각각 (1+retries)×timeout 을 기다리는 대신, 연속 N회 실패하면 cooldown 동안 즉시 실패시켜 대체 경로로 보낸다.
_CIRCUIT_LOCK = threading.Lock()
_CIRCUIT: Dict[str, Dict[str, Any]] = {}


def circuit_state(key: str) -> Dict[str, Any]:
    with _CIRCUIT_LOCK:
        return dict(_CIRCUIT.get(key) or {"failures": 0, "open_until": 0.0, "opened": 0, "last_error": ""})


def circuit_all() -> Dict[str, Dict[str, Any]]:
    with _CIRCUIT_LOCK:
        now = time.time()
        return {k: dict(v, open=bool(v.get("open_until", 0) > now)) for k, v in _CIRCUIT.items()}


def circuit_reset(key: Optional[str] = None) -> None:
    with _CIRCUIT_LOCK:
        if key:
            _CIRCUIT.pop(key, None)
        else:
            _CIRCUIT.clear()


def _circuit_check(key: str) -> Optional[float]:
    """열려 있으면 남은 초, 아니면 None."""
    with _CIRCUIT_LOCK:
        st = _CIRCUIT.get(key)
        if st and st.get("open_until", 0) > time.time():
            return st["open_until"] - time.time()
    return None


def _circuit_record(key: str, ok: bool, threshold: int, cooldown_s: float, error: str = "") -> bool:
    """호출 결과를 기록. 회로가 새로 열리면 True."""
    if threshold <= 0:
        return False
    with _CIRCUIT_LOCK:
        st = _CIRCUIT.setdefault(key, {"failures": 0, "open_until": 0.0, "opened": 0, "last_error": ""})
        if ok:
            st["failures"] = 0
            st["open_until"] = 0.0
            return False
        st["failures"] += 1
        st["last_error"] = error[:200]
        if st["failures"] >= threshold:
            st["open_until"] = time.time() + max(1.0, float(cooldown_s))
            st["opened"] += 1
            st["failures"] = 0
            return True
    return False


def _is_transient_exc(e: BaseException) -> bool:
    if isinstance(e, LLMError):
        return e.transient
    if isinstance(e, (TimeoutError, socket.timeout, ConnectionError)):
        return True
    if isinstance(e, urllib.error.URLError) and not isinstance(e, urllib.error.HTTPError):
        return True
    return False


class BaseLLM:
    """모든 LLM 프로바이더의 공통 래퍼.

    complete() 는 호출 수/토큰/지연을 self.stats 와 profiler.COUNTERS 에 누적한 뒤 _complete() 결과를 돌려준다.
    transient 오류(타임아웃·네트워크·headless 실행 실패)는 retries 회 재시도(backoff)하고, 최종 실패는 incident 로 남긴다.
    역할(role: answer/rerank/extract/summary/review)별로 서로 다른 인스턴스를 만들 수 있다 (make_llm(settings, role)).
    """
    name = "none"
    available = False
    model: Optional[str] = None
    role: str = "default"
    timeout: int = DEFAULT_LLM_TIMEOUT
    retries: int = DEFAULT_LLM_RETRIES
    retry_backoff_s: float = DEFAULT_RETRY_BACKOFF_S
    retry_backoff: str = "exponential"      # linear | exponential
    retry_backoff_max_s: float = 60.0
    budget_s: int = 0                       # 재시도 포함 총 시간 예산 (0 = 없음)
    http_retries: int = 2                   # 프로바이더 내부 429/5xx 재시도
    circuit_failures: int = 3               # 0 = 회로 차단 끔
    circuit_cooldown_s: int = 60

    def __init__(self) -> None:
        self.stats: Dict[str, float] = {"calls": 0, "errors": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0, "ms": 0.0, "circuit_rejects": 0}

    def circuit_key(self) -> str:
        return "%s/%s" % (self.name, self.model or "")

    def policy(self) -> Dict[str, Any]:
        """유효 재시도 정책 (describe / models show 용)."""
        return {"timeout_s": getattr(self, "timeout", None), "retries": getattr(self, "retries", 0), "backoff_s": getattr(self, "retry_backoff_s", 0),
                "backoff": getattr(self, "retry_backoff", "exponential"), "backoff_max_s": getattr(self, "retry_backoff_max_s", 0),
                "budget_s": getattr(self, "budget_s", 0), "http_retries": getattr(self, "http_retries", 0),
                "circuit_failures": getattr(self, "circuit_failures", 0), "circuit_cooldown_s": getattr(self, "circuit_cooldown_s", 0)}

    def _backoff_wait(self, attempt: int) -> float:
        base = float(getattr(self, "retry_backoff_s", DEFAULT_RETRY_BACKOFF_S) or 0)
        if base <= 0:
            return 0.0
        if str(getattr(self, "retry_backoff", "exponential")).lower().startswith("lin"):
            wait = base * attempt
        else:
            wait = base * (2 ** (attempt - 1))
        cap = float(getattr(self, "retry_backoff_max_s", 60.0) or 0)
        if cap > 0:
            wait = min(wait, cap)
        return wait * (1.0 + random.random() * 0.2)   # 지터: 동시에 실패한 여러 요청이 같은 순간에 재시도하지 않도록

    def complete(self, system: str, user: str, max_tokens: int = 2048, effort: str = "low",
                 json_mode: bool = False, files: Optional[List[str]] = None) -> Dict[str, Any]:
        """returns {'text': str, 'usage': {...}, 'ms': float, 'model': str, 'attempts': int}
        files: (headless agent 전용) 프롬프트에 첨부할 파일 경로 — 다른 프로바이더는 무시.
        재시도 정책(역할별): retries · backoff(linear|exponential, 상한·지터) · budget_s(총 시간 예산) · 회로 차단(circuit_failures/cooldown).
        취소: 진행 레지스트리에 취소 요청이 있으면 다음 시도 전에 progress.Cancelled 를 던진다."""
        if not hasattr(self, "stats"):
            BaseLLM.__init__(self)
        _count("llm_calls", 1)
        self.stats["calls"] += 1
        self._files = list(files or [])
        t_start = time.perf_counter()
        ckey = self.circuit_key()
        cf = int(getattr(self, "circuit_failures", 0) or 0)
        remaining = _circuit_check(ckey) if cf > 0 else None
        if remaining is not None:
            self.stats["circuit_rejects"] = self.stats.get("circuit_rejects", 0) + 1
            msg = "circuit open for %s (%.0fs 남음): 최근 연속 실패로 호출을 건너뜀" % (ckey, remaining)
            record_incident({"role": self.role, "provider": self.name, "model": str(self.model or ""), "attempts": 0, "max_attempts": 0,
                             "elapsed_ms": 0.0, "errors": [msg], "transient": True, "timeout_s": getattr(self, "timeout", None),
                             "prompt_chars": len(system) + len(user), "ts": time.time(), "circuit": True})
            _pg.note("LLM 회로 차단 중 (%s) — 대체 경로 사용" % ckey)
            raise LLMError(msg, transient=True, kind="circuit_open")
        _pg.check_cancel()
        _pg.llm_start(self.name, str(self.model or ""), self.role)
        max_attempts = 1 + max(0, int(getattr(self, "retries", 0) or 0))
        budget = float(getattr(self, "budget_s", 0) or 0)
        errors: List[str] = []
        r: Dict[str, Any] = {}
        for attempt in range(1, max_attempts + 1):
            try:
                r = self._complete(system, user, max_tokens, effort, json_mode)
                break
            except _pg.Cancelled:
                _pg.llm_end((time.perf_counter() - t_start) * 1000, "cancelled")
                raise
            except Exception as e:
                msg = "%s: %s" % (type(e).__name__, str(e)[:300]) if not isinstance(e, LLMError) else str(e)[:300]
                errors.append(msg)
                transient = _is_transient_exc(e)
                elapsed_s = time.perf_counter() - t_start
                over_budget = budget > 0 and elapsed_s >= budget
                try:
                    from . import logging_setup as _ls
                    _ls.log("warning", "llm call failed (attempt %d/%d%s): %s" % (attempt, max_attempts, ", retrying" if (transient and attempt < max_attempts and not over_budget) else
                                                                                   (", budget exhausted" if over_budget else "")),
                            "llm", provider=self.name, model=self.model, role=self.role, ms=round(elapsed_s * 1000, 1),
                            prompt_chars=len(system) + len(user), transient=transient)
                except Exception:
                    pass
                if transient and attempt < max_attempts and not over_budget and _pg.cancel_requested() is None:
                    self.stats["retries"] += 1
                    wait = self._backoff_wait(attempt)
                    if budget > 0:
                        wait = min(wait, max(0.0, budget - elapsed_s))
                    _pg.note("LLM 재시도 %d/%d (%s/%s, %s) %.1fs 후: %s" % (attempt + 1, max_attempts, self.name, self.model, self.role, wait, msg[:100]))
                    if wait > 0:
                        _pg.sleep_cancellable(wait)
                    continue
                self.stats["errors"] += 1
                elapsed = (time.perf_counter() - t_start) * 1000
                _pg.llm_end(elapsed, msg[:200])
                opened = _circuit_record(ckey, False, cf, float(getattr(self, "circuit_cooldown_s", 60) or 60), msg)
                if opened:
                    try:
                        from . import logging_setup as _ls
                        _ls.log("error", "llm circuit opened: %s (cooldown %ss)" % (ckey, getattr(self, "circuit_cooldown_s", 60)), "llm", provider=self.name, model=self.model)
                    except Exception:
                        pass
                record_incident({"role": self.role, "provider": self.name, "model": str(self.model or ""), "attempts": attempt, "max_attempts": max_attempts,
                                 "elapsed_ms": round(elapsed, 1), "errors": errors, "transient": transient, "timeout_s": getattr(self, "timeout", None),
                                 "prompt_chars": len(system) + len(user), "ts": time.time(), "budget_exhausted": over_budget, "circuit_opened": opened})
                if isinstance(e, LLMError):
                    if attempt > 1:
                        raise LLMError("%s (%d회 시도 모두 실패%s)" % (str(e), attempt, ", 시간 예산 초과" if over_budget else ""), transient=e.transient, kind=e.kind)
                    raise
                raise LLMError(msg, transient=transient)
        _circuit_record(ckey, True, cf, 0)
        r["attempts"] = len(errors) + 1
        if errors:
            r["retry_errors"] = errors
            try:
                from . import logging_setup as _ls
                _ls.log("info", "llm call recovered after %d retries" % len(errors), "llm", provider=self.name, model=self.model, role=self.role)
            except Exception:
                pass
        _pg.llm_end(float(r.get("ms", 0) or 0))
        try:
            from . import logging_setup as _ls
            _u = r.get("usage") or {}
            _ls.log("info", "llm call", "llm", provider=self.name, model=r.get("model", self.model), role=self.role,
                    ms=round(float(r.get("ms", 0) or 0), 1), input_tokens=_u.get("input_tokens"), output_tokens=_u.get("output_tokens"),
                    prompt_chars=len(system) + len(user), json_mode=json_mode)
        except Exception:
            pass
        u = r.get("usage") or {}
        ti, to = int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0)
        _count("llm_input_tokens", ti)
        _count("llm_output_tokens", to)
        self.stats["input_tokens"] += ti
        self.stats["output_tokens"] += to
        self.stats["ms"] += float(r.get("ms", 0) or 0)
        r.setdefault("provider", self.name)
        r["prompt_chars"] = len(system) + len(user)
        return r

    def _complete(self, system: str, user: str, max_tokens: int, effort: str, json_mode: bool) -> Dict[str, Any]:
        raise LLMError("no LLM provider configured")

    def ping(self) -> Dict[str, Any]:
        """연결/모델 확인 (토큰을 쓰지 않는 경량 호출). {'ok': bool, 'ms': float, 'detail': str}"""
        return {"ok": False, "ms": 0.0, "detail": getattr(self, "reason", "") or "no LLM provider configured"}

    def live_test(self, timeout_s: int = 60) -> Dict[str, Any]:
        """실제 완성 호출 1회 (토큰 소량 소비). ping 이 통과해도 PAT 권한/모델명/헤더가 틀리면 여기서 드러난다.
        headless 에이전트는 프로세스를 실제로 실행하므로 인증·출력 포맷까지 확인된다. (재시도 없이 1회만)"""
        if not self.available:
            return {"ok": False, "ms": 0.0, "detail": getattr(self, "reason", "") or "unavailable"}
        old = getattr(self, "timeout", None)
        old_retries = getattr(self, "retries", 0)
        try:
            self.timeout = min(int(old or timeout_s), timeout_s)
            self.retries = 0
            t0 = time.perf_counter()
            r = self.complete("You are a connectivity probe. Reply with exactly: OK", "ping", max_tokens=16, effort="low")
            ms = (time.perf_counter() - t0) * 1000
            text = (r.get("text") or "").strip()
            return {"ok": bool(text), "ms": ms, "detail": "reply=%r model=%s tokens=%s" % (text[:40], r.get("model"), (r.get("usage") or {}).get("output_tokens"))}
        except Exception as e:
            return {"ok": False, "ms": 0.0, "detail": "live call failed: %s" % str(e)[:300]}
        finally:
            if old is not None:
                self.timeout = old
            self.retries = old_retries

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "model": self.model, "available": self.available, "role": self.role,
                "reason": "" if self.available else (getattr(self, "reason", "") or ""), "stats": dict(getattr(self, "stats", {})),
                "policy": self.policy(), "circuit": circuit_state(self.circuit_key())}


class NoneLLM(BaseLLM):
    def __init__(self, reason: str = "") -> None:
        BaseLLM.__init__(self)
        self.reason = reason or "llm_provider=none (LLM 사용 안 함)"


class MockLLM(BaseLLM):
    """API 없이 파이프라인 배선을 검증하기 위한 결정적(deterministic) 목업.
    - 추출 요청: 텍스트에서 대문자/한글 명사구를 뽑아 entity JSON 생성
    - 리랭크 요청: 입력 순서 유지
    - 답변 요청: 컨텍스트 첫 문장들을 인용과 함께 합성
    """
    name = "mock"
    available = True
    model = "mock"

    def __init__(self) -> None:
        BaseLLM.__init__(self)

    def ping(self) -> Dict[str, Any]:
        return {"ok": True, "ms": 0.0, "detail": "mock (deterministic, no network)"}

    # ---- 테스트 훅 (환경 변수로만 켜진다 — 기본 동작에는 영향이 없다) ----
    # 타임아웃·재시도·회로 차단 같은 **실패 경로**는 실제로 실패를 만들어야 확인할 수 있는데,
    # 진짜 LLM 으로는 재현이 불가능하고 느리다. mock 에 아래 두 손잡이를 둔다:
    #   LLMWIKI_MOCK_DELAY_MS=1500        호출마다 이만큼 지연 (느린 요청·취소·대기열 시험용)
    #   LLMWIKI_MOCK_FAIL=timeout         매번 타임아웃으로 실패 (transient → 재시도·회로 차단 경로)
    #   LLMWIKI_MOCK_FAIL=timeout:2       처음 2회만 실패하고 3회째 성공 (재시도 성공 경로)
    #   LLMWIKI_MOCK_ANSWER=<본문>        답변 역할의 출력만 이 문자열로 바꾼다 (없는 인용 [C99]·빈 답·무인용 같은
    #                                     **모델이 잘못 답하는 경우**를 재현한다 — 진짜 LLM 으로는 재현이 불가능하다).
    #                                     "" 로 두면 빈 답. 다른 역할(추출·리랭크)에는 영향이 없다.
    # 문서: docs/CONCURRENCY.md §타임아웃 검증, tools/verify/verify_timeouts.py, tests/test_rag_edge_cases.py
    _fail_counts: Dict[str, int] = {}

    def _test_hooks(self) -> None:
        delay = float(os.environ.get("LLMWIKI_MOCK_DELAY_MS") or 0) / 1000.0
        if delay > 0:
            _pg.sleep_cancellable(delay)       # 취소 요청이 오면 여기서 깨어난다 (실제 LLM 대기와 같은 성질)
        spec = str(os.environ.get("LLMWIKI_MOCK_FAIL") or "").strip()
        if not spec:
            return
        kind, _, n = spec.partition(":")
        kind = kind or "timeout"
        limit = int(n) if n.isdigit() else -1        # -1 = 계속 실패
        key = "%s:%s" % (kind, self.role)
        seen = MockLLM._fail_counts.get(key, 0)
        if limit >= 0 and seen >= limit:
            return
        MockLLM._fail_counts[key] = seen + 1
        raise LLMError("mock %s (테스트 훅 LLMWIKI_MOCK_FAIL, %d회째)" % (kind, seen + 1),
                       transient=True, kind=kind)

    def _complete(self, system: str, user: str, max_tokens: int, effort: str, json_mode: bool) -> Dict[str, Any]:
        t0 = time.perf_counter()
        self._test_hooks()
        text = ""
        if "TASK=extract" in system:
            ents = []
            seen = set()
            for w in re.findall(r"[A-Z][A-Za-z0-9\-]{2,}|[가-힣]{2,}(?:본부|팀|실|회의|위원회|이사)", user):
                if w not in seen and len(ents) < 12:
                    seen.add(w)
                    ents.append({"name": w, "type": "org" if w.endswith(("본부", "팀", "실")) else "concept",
                                 "description": "mock-extracted"})
            rels = []
            for i in range(len(ents) - 1):
                rels.append({"src": ents[i]["name"], "dst": ents[i + 1]["name"], "rel": "related_to",
                             "description": "co-mentioned (mock)", "weight": 0.5})
            text = json.dumps({"entities": ents, "relations": rels}, ensure_ascii=False)
        elif "TASK=fusion_review" in system:
            # 융합 뒤 검토 (llm_after_fusion): 결정적으로 **마지막 후보 하나만** drop 한다 (후보가 3개 이상일 때).
            # 배선(감점·제거·재정렬·trace)을 시험할 수 있으면서 근거를 통째로 날리지 않는다.
            ids = [int(i) for i in dict.fromkeys(re.findall(r"\[(\d+)\]", user))]
            drop = ids[-1:] if len(ids) >= 3 else []
            text = json.dumps({"keep": [i for i in ids if i not in drop], "drop": drop, "reason": "mock: 마지막 후보만 무관으로 가정"}, ensure_ascii=False)
        elif "TASK=rerank_review" in system:
            # 리랭크 뒤 선택 (llm_after_rerank): 순서를 **뒤집어** 돌려준다 — 선택이 실제로 최종 순서를 바꾸는지 시험하기 위해.
            # expand_docs 는 첫 후보의 문서 하나 (doc_expand 우선·전체 확장 경로 시험).
            ids = [int(i) for i in dict.fromkeys(re.findall(r"\[(\d+)\]", user))]
            docs = re.findall(r"문서=(\S+)", user)
            text = json.dumps({"select": list(reversed(ids)), "expand_docs": docs[:1], "note": "mock: 역순 선택"}, ensure_ascii=False)
        elif "TASK=rerank" in system:
            ids = re.findall(r"\[(\d+)\]", user)
            text = json.dumps({"ranking": [int(i) for i in dict.fromkeys(ids)]})
        elif "TASK=summarize" in system:
            text = "(mock summary) " + user[:200].replace("\n", " ")
        elif "TASK=rewrite" in system:
            q = user.split("\n")[-1].strip()
            text = json.dumps({"queries": [q + " 관련 내용", q.replace("?", "") + " 상세"], "keywords": []}, ensure_ascii=False)
        else:
            forced = os.environ.get("LLMWIKI_MOCK_ANSWER")
            if forced is not None:
                text = forced
            else:
                cites = re.findall(r"\[(?:C)?(\d+)\]", user)
                first = cites[:3] if cites else []
                text = "(mock answer) 컨텍스트 기반 요약입니다. " + " ".join("[C%s]" % c for c in first)
        return {"text": text, "usage": {"input_tokens": len(user) // 3, "output_tokens": len(text) // 3},
                "ms": (time.perf_counter() - t0) * 1000, "model": "mock"}


ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com"


def _anthropic_auth() -> Dict[str, str]:
    """ANTHROPIC_API_KEY → x-api-key, 없으면 ANTHROPIC_AUTH_TOKEN(PAT) → Authorization: Bearer. 둘 다 없으면 {}."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return {"x-api-key": key}
    tok = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    if tok:
        return {"authorization": "Bearer " + tok}
    return {}


class AnthropicHTTPLLM(BaseLLM):
    """Anthropic Messages API 를 urllib 로 직접 호출 (SDK 미설치 환경용).
    base_url(anthropic_base_url) 을 주면 Anthropic 호환 사내 게이트웨이(PAT)로 보낸다."""
    name = "anthropic"

    def __init__(self, model: str, fallbacks: bool = True, api_key: Optional[str] = None, base_url: str = "",
                 extra_headers: Optional[Dict[str, str]] = None):
        BaseLLM.__init__(self)
        self.model = model
        self.fallbacks = fallbacks
        self.base_url = (base_url or ANTHROPIC_DEFAULT_BASE).rstrip("/")
        self.auth = {"x-api-key": api_key} if api_key else _anthropic_auth()
        self.extra_headers = dict(extra_headers or {})
        self.api_key = self.auth.get("x-api-key") or self.auth.get("authorization", "").replace("Bearer ", "")
        self.available = bool(self.auth)
        self.reason = "" if self.available else "ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 이 비어 있음 (.env 에 키 입력 후 서버 재시작 또는 '저장 & 프로바이더 재로드')"

    @property
    def API(self) -> str:
        return self.base_url + "/v1/messages"

    def _base_headers(self) -> Dict[str, str]:
        h = {"anthropic-version": "2023-06-01"}
        h.update(self.auth)
        h.update(self.extra_headers)
        return h

    def ping(self) -> Dict[str, Any]:
        if not self.available:
            return {"ok": False, "ms": 0.0, "detail": self.reason}
        t0 = time.perf_counter()
        req = urllib.request.Request(self.base_url + "/v1/models?limit=100", headers=self._base_headers())
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            ids = [m.get("id") for m in data.get("data", [])]
            mm = model_in_list(self.model, ids)
            ok = mm["known"] is not False
            return {"ok": ok, "ms": (time.perf_counter() - t0) * 1000, "models": ids[:40],
                    "model_known": mm["known"], "model_match": mm["match"],
                    "detail": "connected %s" % self.base_url + ("" if ok else "; %s" % mm["hint"])}
        except urllib.error.HTTPError as e:
            # 게이트웨이가 /v1/models 를 막아둔 경우(404/405)는 연결 자체는 된 것 — models test --live 로 실제 호출 확인
            body = e.read().decode("utf-8", "ignore")[:200]
            return {"ok": e.code in (404, 405), "ms": (time.perf_counter() - t0) * 1000,
                    "detail": "HTTP %s %s%s" % (e.code, body, " (models 목록 미제공 게이트웨이 — 'models test --live' 로 실제 호출 확인)" if e.code in (404, 405) else "")}
        except Exception as e:
            return {"ok": False, "ms": (time.perf_counter() - t0) * 1000, "detail": "network: %s" % e}

    def _complete(self, system: str, user: str, max_tokens: int, effort: str, json_mode: bool) -> Dict[str, Any]:
        if not self.available:
            raise LLMError("ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 이 설정되지 않았습니다")
        body: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": effort},
        }
        headers = dict(self._base_headers(), **{"content-type": "application/json"})
        if self.fallbacks and self.model.startswith(("claude-opus-5", "claude-fable")):
            # 안전 분류기 refusal 시 서버측 폴백 (스킬 가이드 기본값)
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
            body["fallbacks"] = "default"
        t0 = time.perf_counter()
        data = self._post(body, headers)
        if data.get("stop_reason") == "refusal":
            raise LLMError("model refused: %s" % json.dumps(data.get("stop_details"), ensure_ascii=False))
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return {"text": text, "usage": data.get("usage", {}), "ms": (time.perf_counter() - t0) * 1000,
                "model": data.get("model", self.model)}

    def _post(self, body: Dict[str, Any], headers: Dict[str, str], retries: Optional[int] = None) -> Dict[str, Any]:
        """HTTP 429/5xx 는 여기서 짧게 재시도(backoff, 횟수 = llm_http_retries). 타임아웃·네트워크 오류는 transient LLMError 로 올려 BaseLLM.complete 가 역할별 retries 만큼 재시도한다."""
        raw = json.dumps(body).encode("utf-8")
        last: Optional[Exception] = None
        retries = (1 + int(getattr(self, "http_retries", 2) or 0)) if retries is None else retries
        for attempt in range(retries):
            req = urllib.request.Request(self.API, data=raw, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "ignore")[:500]
                last = LLMError("HTTP %s: %s" % (e.code, msg), transient=(e.code in (408, 409, 429) or e.code >= 500))
                if e.code in (408, 409, 429) or e.code >= 500:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise last
            except (TimeoutError, socket.timeout) as e:
                raise LLMError("timeout after %ss: %s" % (self.timeout, e), transient=True, kind="timeout")
            except urllib.error.URLError as e:
                if isinstance(getattr(e, "reason", None), (TimeoutError, socket.timeout)):
                    raise LLMError("timeout after %ss: %s" % (self.timeout, e.reason), transient=True, kind="timeout")
                raise LLMError("network: %s" % e, transient=True, kind="network")
        raise last or LLMError("unknown")


class AnthropicSDKLLM(BaseLLM):
    """anthropic SDK 가 설치된 환경(Python 3.9+)에서 사용. base_url/extra_headers 는 config 의 anthropic_base_url 을 명시적으로 넘긴다
    (SDK 의 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN 환경변수도 그대로 동작)."""
    name = "anthropic"

    def __init__(self, model: str, fallbacks: bool = True, base_url: str = "", extra_headers: Optional[Dict[str, str]] = None):
        import anthropic  # noqa
        BaseLLM.__init__(self)
        self.model = model
        self.fallbacks = fallbacks
        kw: Dict[str, Any] = {}
        if base_url:
            kw["base_url"] = base_url.rstrip("/")
        if extra_headers:
            kw["default_headers"] = dict(extra_headers)
        self.base_url = kw.get("base_url") or os.environ.get("ANTHROPIC_BASE_URL") or ANTHROPIC_DEFAULT_BASE
        self.client = anthropic.Anthropic(**kw)
        self.available = True

    def ping(self) -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            page = self.client.models.list(limit=100)
            ids = [m.id for m in getattr(page, "data", [])]
            ok = self.model in ids or not ids
            return {"ok": ok, "ms": (time.perf_counter() - t0) * 1000, "models": ids[:40], "detail": "connected (sdk)"}
        except Exception as e:
            return {"ok": False, "ms": (time.perf_counter() - t0) * 1000, "detail": str(e)[:300]}

    def _complete(self, system: str, user: str, max_tokens: int, effort: str, json_mode: bool) -> Dict[str, Any]:
        t0 = time.perf_counter()
        kw: Dict[str, Any] = dict(model=self.model, max_tokens=max_tokens, system=system,
                                  messages=[{"role": "user", "content": user}],
                                  output_config={"effort": effort})
        if self.fallbacks and self.model.startswith(("claude-opus-5", "claude-fable")):
            kw["betas"] = ["server-side-fallback-2026-07-01"]
            kw["fallbacks"] = "default"
            resp = self.client.beta.messages.create(**kw)
        else:
            resp = self.client.messages.create(**kw)
        if getattr(resp, "stop_reason", "") == "refusal":
            raise LLMError("model refused")
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        usage = getattr(resp, "usage", None)
        return {"text": text, "usage": {"input_tokens": getattr(usage, "input_tokens", 0),
                                        "output_tokens": getattr(usage, "output_tokens", 0)},
                "ms": (time.perf_counter() - t0) * 1000, "model": resp.model}


_OLLAMA_PROBE: Dict[str, Any] = {}


class OllamaLLM(BaseLLM):
    name = "ollama"

    def __init__(self, url: str, model: str):
        BaseLLM.__init__(self)
        self.url = url.rstrip("/")
        self.model = model
        # 서버가 응답하고 모델이 실제로 받아져 있어야 available (모델 없는 서버를 auto 가 고르면 404 로 실패하므로)
        up = self._ping()
        self.available = up and self._has_model()
        self.reason = "" if self.available else ("Ollama 서버 응답 없음 (%s)" % self.url if not up else "Ollama 에 모델 '%s' 없음 — `ollama pull %s`" % (model, model))

    def _ping(self) -> bool:
        # 같은 URL 에 대한 가용성 probe 는 30초 동안 공유 (역할별 인스턴스마다 0.4s 타임아웃을 반복하지 않도록)
        now = time.time()
        cached = _OLLAMA_PROBE.get(self.url)
        if cached and now - cached[0] < 30:
            return cached[1]
        ok = False
        names: Optional[List[str]] = None      # None = 목록을 못 읽음(알 수 없음), [] = 서버에 모델 없음
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=0.4) as r:
                ok = r.status == 200
                try:
                    names = [m.get("name") or "" for m in json.loads(r.read().decode("utf-8")).get("models", [])]
                except Exception:
                    names = None
        except Exception:
            ok = False
        _OLLAMA_PROBE[self.url] = (now, ok, names)
        return ok

    def _has_model(self) -> bool:
        cached = _OLLAMA_PROBE.get(self.url)
        names = cached[2] if cached and len(cached) > 2 else None
        m = str(self.model or "")
        if names is None:
            return bool(m)
        return any(n == m or n.split(":")[0] == m or n == m + ":latest" for n in names)

    def ping(self) -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=3) as r:
                data = json.loads(r.read().decode("utf-8"))
            names = [m.get("name") for m in data.get("models", [])]
            mm = model_in_list(self.model, names)
            ok = mm["known"] is not False
            return {"ok": ok, "ms": (time.perf_counter() - t0) * 1000, "models": names,
                    "model_known": mm["known"], "model_match": mm["match"],
                    "detail": ("connected" + (" (%s)" % mm["match"] if mm["match"] and mm["match"] != self.model else ""))
                              if ok else "model %s not pulled — `ollama pull %s` · %s" % (self.model, self.model, mm["hint"])}
        except Exception as e:
            return {"ok": False, "ms": (time.perf_counter() - t0) * 1000, "detail": "ollama unreachable at %s: %s" % (self.url, e)}

    def _complete(self, system: str, user: str, max_tokens: int, effort: str, json_mode: bool) -> Dict[str, Any]:
        opts: Dict[str, Any] = {"num_predict": max_tokens}
        if float(getattr(self, "repeat_penalty", 0) or 0) > 0:
            opts["repeat_penalty"] = float(self.repeat_penalty)     # 같은 구절 반복 억제
        body = {"model": self.model, "system": system, "prompt": user, "stream": False, "options": opts}
        if json_mode:
            body["format"] = "json"
        t0 = time.perf_counter()
        req = urllib.request.Request(self.url + "/api/generate", data=json.dumps(body).encode("utf-8"),
                                     headers={"content-type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise LLMError("ollama HTTP %s: %s (ollama pull %s ?)" % (e.code, e.read().decode("utf-8", "ignore")[:200], self.model), transient=e.code >= 500)
        except (TimeoutError, socket.timeout) as e:
            raise LLMError("ollama timeout after %ss: %s" % (self.timeout, e), transient=True, kind="timeout")
        except Exception as e:
            raise LLMError("ollama network: %s" % e, transient=True, kind="network")
        return {"text": data.get("response", ""), "usage": {"input_tokens": data.get("prompt_eval_count", 0),
                                                            "output_tokens": data.get("eval_count", 0)},
                "ms": (time.perf_counter() - t0) * 1000, "model": self.model}


def openai_api_key(embed: bool = False) -> str:
    """.env 의 키: 임베딩은 OPENAI_EMBED_API_KEY 우선, LLM/공통은 OPENAI_API_KEY 또는 LLM_API_KEY(사내 PAT 이름이 OpenAI 와 무관할 때)."""
    if embed and os.environ.get("OPENAI_EMBED_API_KEY"):
        return os.environ["OPENAI_EMBED_API_KEY"]
    return os.environ.get("OPENAI_API_KEY", "") or os.environ.get("LLM_API_KEY", "")


def model_in_list(model: str, ids: List[str]) -> Dict[str, Any]:
    """설정한 모델 이름이 서버가 알려 준 목록에 있는가 — **이름 표기 차이를 흡수해서** 판단한다.

    2026-09-19: `ollama list` 는 `llama3.1:latest` 로 내놓는데 config 에는 보통 `llama3.1` 만 적는다.
    예전에는 이 둘을 다른 이름으로 보고 "model llama3.1 not in list" 로 연결 실패를 냈다 —
    실제 호출은 멀쩡히 되는데도 화면이 빨간 ✗ 를 보여 주던 원인이다.

    같은 모델로 보는 표기
      - 대소문자 차이
      - Ollama 의 암묵적 `:latest` (`llama3.1` ↔ `llama3.1:latest`)
      - 게이트웨이의 네임스페이스 접두어 (`openai/gpt-4o` · `models/gemini-1.5` ↔ `gpt-4o` · `gemini-1.5`)

    반환 {"known": True|False|None, "match": 실제 목록에 있던 이름, "hint": 사람에게 줄 안내}
    목록을 받지 못했으면(빈 목록) 판단하지 않고 None — 게이트웨이가 목록을 막아 둔 경우다.
    """
    ids = [str(x) for x in (ids or []) if x]
    if not ids:
        return {"known": None, "match": "", "hint": ""}
    m = str(model or "").strip()
    if not m:
        return {"known": None, "match": "", "hint": ""}

    def forms(x: str) -> set:
        x = x.strip()
        out = {x, x.lower()}
        low = x.lower()
        out.add(low.split(":")[0])            # llama3.1:latest → llama3.1
        out.add(low.rsplit("/", 1)[-1])       # openai/gpt-4o  → gpt-4o
        out.add(low.rsplit("/", 1)[-1].split(":")[0])
        return {y for y in out if y}

    want = forms(m)
    for i in ids:
        if want & forms(i):
            return {"known": True, "match": i, "hint": ""}
    near = [i for i in ids if m.lower()[:6] and m.lower()[:6] in i.lower()]
    hint = ("모델 이름이 목록에 없습니다. 비슷한 이름: %s" % ", ".join(near[:5])) if near else \
           ("모델 이름이 목록에 없습니다. 쓸 수 있는 이름: %s" % ", ".join(ids[:8]))
    return {"known": False, "match": "", "hint": hint}


def auth_headers(api_key: str, header: str = "authorization", extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """PAT/키 헤더 조립. header=authorization 이면 'Bearer <key>', 그 외(api-key, x-api-key, 임의)는 키 값 그대로."""
    h = {"content-type": "application/json"}
    h.update({str(k): str(v) for k, v in (extra or {}).items()})
    if api_key:
        name = (header or "authorization").strip().lower()
        h[name] = ("Bearer " + api_key) if name == "authorization" and not api_key.lower().startswith(("bearer ", "basic ")) else api_key
    return h


class OpenAICompatLLM(BaseLLM):
    """OpenAI-compatible chat/completions (vLLM · LM Studio · Ollama(OpenAI 호환) · OpenRouter · 사내 게이트웨이).
    base_url 은 …/v1 까지. 키는 OPENAI_API_KEY 또는 LLM_API_KEY (.env) — 로컬 서버는 비워도 됨.
    key_header: PAT 를 싣는 헤더 (authorization → Bearer, api-key/x-api-key 등은 값 그대로), extra_headers: 고정 헤더."""
    name = "openai"

    def __init__(self, base_url: str, model: str, api_key: Optional[str] = None, timeout: int = 600,
                 key_header: str = "authorization", extra_headers: Optional[Dict[str, str]] = None):
        BaseLLM.__init__(self)
        self.base_url = (base_url or "http://localhost:11434/v1").rstrip("/")
        self.model = model
        self.api_key = api_key if api_key is not None else openai_api_key()
        self.key_header = key_header or "authorization"
        self.extra_headers = dict(extra_headers or {})
        self.timeout = timeout
        self.available = bool(self.model)
        self._json_mode_ok: Optional[bool] = None

    def _headers(self) -> Dict[str, str]:
        return auth_headers(self.api_key, self.key_header, self.extra_headers)

    def ping(self) -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(self.base_url + "/models", headers=self._headers())
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            ids = [m.get("id") for m in (data.get("data") or [])]
            mm = model_in_list(self.model, ids)
            ok = mm["known"] is not False
            return {"ok": ok, "ms": (time.perf_counter() - t0) * 1000, "models": ids[:40],
                    "model_known": mm["known"], "model_match": mm["match"],
                    "detail": "connected %s" % self.base_url + ("" if ok else "; %s" % mm["hint"])}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:200]
            if e.code in (404, 405):   # 게이트웨이가 /models 를 제공하지 않음 — 인증/연결은 통과한 것
                return {"ok": True, "ms": (time.perf_counter() - t0) * 1000,
                        "detail": "connected %s (models 목록 미제공: HTTP %s — 'models test --live' 로 실제 호출 확인)" % (self.base_url, e.code)}
            hint = " ← 키/PAT 또는 openai_api_key_header 확인" if e.code in (401, 403) else ""
            return {"ok": False, "ms": (time.perf_counter() - t0) * 1000, "detail": "HTTP %s %s%s" % (e.code, body, hint)}
        except Exception as e:
            return {"ok": False, "ms": (time.perf_counter() - t0) * 1000, "detail": "unreachable %s: %s" % (self.base_url, e)}

    def _complete(self, system: str, user: str, max_tokens: int, effort: str, json_mode: bool) -> Dict[str, Any]:
        body: Dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "temperature": 0.0 if json_mode else 0.2,
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        # 반복 억제 (작은 모델이 같은 구절을 되풀이하는 고장을 줄인다). 0 이면 보내지 않는다 —
        # 일부 게이트웨이는 모르는 필드에 400 을 내므로 기본값일 때는 건드리지 않기 위함.
        if getattr(self, "frequency_penalty", 0):
            body["frequency_penalty"] = float(self.frequency_penalty)
        if getattr(self, "presence_penalty", 0):
            body["presence_penalty"] = float(self.presence_penalty)
        if json_mode and self._json_mode_ok is not False:
            body["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        try:
            data = self._post("/chat/completions", body)
        except LLMError as e:
            if json_mode and "response_format" in body and ("400" in str(e) or "response_format" in str(e)):
                self._json_mode_ok = False          # 서버가 json_object 를 지원하지 않음 → 다시 시도
                body.pop("response_format", None)
                data = self._post("/chat/completions", body)
            else:
                raise
        if json_mode and "response_format" in body:
            self._json_mode_ok = True
        choices = data.get("choices") or []
        text = ""
        if choices:
            msg = choices[0].get("message") or {}
            text = msg.get("content") or ""
            if isinstance(text, list):   # 일부 서버는 content parts
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        u = data.get("usage") or {}
        return {"text": text, "usage": {"input_tokens": int(u.get("prompt_tokens", 0) or 0), "output_tokens": int(u.get("completion_tokens", 0) or 0)},
                "ms": (time.perf_counter() - t0) * 1000, "model": data.get("model", self.model)}

    def _post(self, path: str, body: Dict[str, Any], retries: Optional[int] = None) -> Dict[str, Any]:
        raw = json.dumps(body).encode("utf-8")
        last: Optional[Exception] = None
        retries = (1 + int(getattr(self, "http_retries", 2) or 0)) if retries is None else retries
        for attempt in range(retries):
            req = urllib.request.Request(self.base_url + path, data=raw, headers=self._headers(), method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "ignore")[:500]
                last = LLMError("HTTP %s: %s" % (e.code, msg), transient=(e.code in (408, 409, 429) or e.code >= 500))
                if e.code in (408, 409, 429) or e.code >= 500:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise last
            except (TimeoutError, socket.timeout) as e:
                raise LLMError("timeout after %ss: %s" % (self.timeout, e), transient=True, kind="timeout")
            except urllib.error.URLError as e:
                if isinstance(getattr(e, "reason", None), (TimeoutError, socket.timeout)):
                    raise LLMError("timeout after %ss: %s" % (self.timeout, e.reason), transient=True, kind="timeout")
                raise LLMError("network: %s" % e, transient=True, kind="network")
        raise last or LLMError("unknown")


# =============================== 앙상블 (역할 단위 최대 3 LLM 병렬 + 취합) ===============================
ENSEMBLE_MAX_MEMBERS = 3


class EnsembleLLM(BaseLLM):
    """역할 하나에 여러 LLM 을 병렬로 부르고 취합 LLM 이 최종 본문을 만드는 래퍼 (docs/ENSEMBLE.md, 계획 §2.6).

    - members: [(BaseLLM, weight)] 최대 3개. 각 멤버는 자기 재시도·회로 차단 정책을 그대로 쓴다(멤버 인스턴스의 complete()).
    - wait: all = 모든 멤버가 끝날 때까지(멤버별 timeout 이 상한) · timeout = timeout_s 지나면 그때까지 온 결과만 사용.
    - min_results: 성공 결과가 이보다 적으면 LLMError (호출부는 단일 LLM 실패와 같은 대체 경로를 탄다).
    - 성공 1개면 취합 없이 그 본문(ensemble.aggregated=false), 2개 이상이면 prompts/<prompt>.md 규칙으로 취합 LLM 을 1회 부른다.
    - 결과 dict 에 r["ensemble"] = {members:[{model,provider,weight,ok,ms,chars,usage,error}], aggregator:{model,provider,ms,usage}, policy, aggregated}.
    - 멤버 실패는 record_incident 로 남겨 llm_report 에 보인다(ensemble_member=true). 래퍼 자체는 재시도·회로 차단을 하지 않는다.
    """
    name = "ensemble"

    def __init__(self, members: List[Tuple[BaseLLM, float]], aggregator: Optional[BaseLLM] = None, wait: str = "all",
                 timeout_s: float = 120, min_results: int = 1, prompt: str = "ensemble_merge", role: str = "default") -> None:
        BaseLLM.__init__(self)
        self.members: List[Tuple[BaseLLM, float]] = [(m, float(w if w not in (None, "") else 1.0)) for m, w in list(members)[:ENSEMBLE_MAX_MEMBERS]]
        self.aggregator: Optional[BaseLLM] = aggregator if aggregator is not None else (self.members[0][0] if self.members else None)
        self.wait = "timeout" if str(wait or "all").lower().startswith("time") else "all"
        self.timeout_s = float(timeout_s or 0)
        self.min_results = max(1, int(min_results or 1))
        self.prompt_name = str(prompt or "ensemble_merge")
        self.role = role
        self.model = "+".join(str(m.model or m.name) for m, _ in self.members) or "(no members)"
        self.retries = 0                 # 재시도는 멤버가 각자 한다
        self.circuit_failures = 0        # 회로 차단도 멤버 단위
        self.timeout = int(max((int(getattr(m, "timeout", 0) or 0) for m, _ in self.members), default=DEFAULT_LLM_TIMEOUT))

    # ---- 상태 ----
    @property
    def available(self) -> bool:  # type: ignore[override]
        return any(getattr(m, "available", False) for m, _ in self.members)

    @available.setter
    def available(self, _v: bool) -> None:
        pass    # 멤버 상태에서 파생되는 값 — 외부 대입은 무시

    @property
    def reason(self) -> str:
        if self.available:
            return ""
        if not self.members:
            return "ensemble: 활성 멤버가 없음 (llm_roles.<role>.ensemble.members)"
        return "ensemble: 멤버 전부 사용 불가 — " + " / ".join("%s/%s: %s" % (m.name, m.model, getattr(m, "reason", "") or "unavailable") for m, _ in self.members)

    def policy(self) -> Dict[str, Any]:
        return {"ensemble": True, "wait": self.wait, "timeout_s": self.timeout_s, "min_results": self.min_results, "prompt": self.prompt_name,
                "members": len(self.members), "aggregator": "%s/%s" % (self.aggregator.name, self.aggregator.model) if self.aggregator else "",
                "retries": 0, "backoff_s": 0, "budget_s": 0, "circuit_failures": 0}

    def describe(self) -> Dict[str, Any]:
        d = BaseLLM.describe(self)
        d["members"] = [dict(m.describe(), weight=w) for m, w in self.members]
        d["aggregator"] = self.aggregator.describe() if self.aggregator is not None else None
        d["ensemble"] = True
        return d

    def ping(self) -> Dict[str, Any]:
        t0 = time.perf_counter()
        rows = []
        for m, w in self.members:
            r = m.ping()
            rows.append(dict(r, provider=m.name, model=m.model, weight=w, available=m.available))
        agg = None
        if self.aggregator is not None and all(self.aggregator is not m for m, _ in self.members):
            agg = dict(self.aggregator.ping(), provider=self.aggregator.name, model=self.aggregator.model)
        ok_n = sum(1 for r in rows if r.get("ok"))
        ok = ok_n >= self.min_results and (agg is None or bool(agg.get("ok")))
        detail = "ensemble %d/%d members ok" % (ok_n, len(rows)) + "; " + "; ".join("%s/%s: %s" % (r["provider"], r["model"], "ok" if r.get("ok") else r.get("detail", "")[:80]) for r in rows)
        if agg is not None:
            detail += "; aggregator %s/%s: %s" % (agg["provider"], agg["model"], "ok" if agg.get("ok") else agg.get("detail", "")[:80])
        return {"ok": ok, "ms": (time.perf_counter() - t0) * 1000, "detail": detail, "members": rows, "aggregator": agg}

    def live_test(self, timeout_s: int = 60) -> Dict[str, Any]:
        t0 = time.perf_counter()
        rows = []
        for m, w in self.members:
            rows.append(dict(m.live_test(timeout_s), provider=m.name, model=m.model, weight=w))
        agg = None
        if self.aggregator is not None and all(self.aggregator is not m for m, _ in self.members):
            agg = dict(self.aggregator.live_test(timeout_s), provider=self.aggregator.name, model=self.aggregator.model)
        ok_n = sum(1 for r in rows if r.get("ok"))
        ok = ok_n >= self.min_results and (agg is None or bool(agg.get("ok")))
        detail = "ensemble %d/%d members ok" % (ok_n, len(rows)) + "; " + "; ".join("%s/%s: %s" % (r["provider"], r["model"], r.get("detail", "")[:80]) for r in rows)
        if agg is not None:
            detail += "; aggregator %s/%s: %s" % (agg["provider"], agg["model"], agg.get("detail", "")[:80])
        return {"ok": ok, "ms": (time.perf_counter() - t0) * 1000, "detail": detail, "members": rows, "aggregator": agg}

    # ---- 호출 ----
    def _run_member(self, idx: int, llm: BaseLLM, weight: float, system: str, user: str, max_tokens: int, effort: str,
                    json_mode: bool, token: Optional[str], files: List[str]) -> Dict[str, Any]:
        """작업 스레드: 멤버 1개 호출. 진행 토큰을 물려받아 취소가 전파되고, 이 스레드의 incident 를 모아 되돌려 준다."""
        if token:
            _pg._TL.token = token          # progress.bind 와 같은 스레드 로컬 — 취소 확인·LLM 활동 표시가 요청 항목에 붙는다
        reset_incidents()
        t0 = time.perf_counter()
        out: Dict[str, Any] = {"i": idx, "model": str(llm.model or ""), "provider": llm.name, "weight": weight, "ok": False,
                               "ms": 0.0, "chars": 0, "usage": {}, "error": "", "text": ""}
        try:
            eff = str(getattr(llm, "_ens_effort", "") or effort)
            r = llm.complete(system, user, max_tokens=max_tokens, effort=eff, json_mode=json_mode, files=files)
            text = r.get("text") or ""
            out.update(text=text, chars=len(text), usage=dict(r.get("usage") or {}), model=str(r.get("model") or llm.model or ""),
                       attempts=r.get("attempts", 1), ok=bool(text.strip()))
            if not out["ok"]:
                out["error"] = "empty output"
        except _pg.Cancelled:
            out["error"] = "cancelled"
        except Exception as e:
            out["error"] = str(e)[:300]
            out["transient"] = _is_transient_exc(e)
        out["ms"] = (time.perf_counter() - t0) * 1000
        out["incidents"] = drain_incidents()
        return out

    def _complete(self, system: str, user: str, max_tokens: int, effort: str, json_mode: bool) -> Dict[str, Any]:
        if not self.members:
            raise LLMError(self.reason)
        t_start = time.perf_counter()
        token = _pg.current_token()
        files = list(getattr(self, "_files", []) or [])
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(ENSEMBLE_MAX_MEMBERS, len(self.members))), thread_name_prefix="ensemble-%s" % self.role)
        futs = {}
        results: Dict[int, Dict[str, Any]] = {}
        try:
            for i, (m, w) in enumerate(self.members):
                futs[ex.submit(self._run_member, i, m, w, system, user, max_tokens, effort, json_mode, token, files)] = i
            pending = set(futs)
            deadline = (time.time() + self.timeout_s) if (self.wait == "timeout" and self.timeout_s > 0) else None
            while pending:
                _pg.check_cancel()                                   # 취소는 폴링 사이에 확인 (멤버 스레드도 같은 토큰으로 스스로 멈춘다)
                done, pending = concurrent.futures.wait(pending, timeout=0.25)
                for f in done:
                    results[futs[f]] = f.result()
                if deadline is not None and pending and time.time() >= deadline:
                    for f in pending:
                        i = futs[f]
                        m, w = self.members[i]
                        results[i] = {"i": i, "model": str(m.model or ""), "provider": m.name, "weight": w, "ok": False, "ms": (time.perf_counter() - t_start) * 1000,
                                      "chars": 0, "usage": {}, "error": "ensemble wait=timeout: %.0fs 안에 응답 없음 (결과는 버려짐)" % self.timeout_s, "text": "", "incidents": [], "timed_out": True}
                        f.cancel()
                    _pg.note("앙상블(%s) 대기 %.0fs 초과 — 도착한 %d개로 진행" % (self.role, self.timeout_s, sum(1 for r in results.values() if r.get("ok"))))
                    break
        finally:
            ex.shutdown(wait=False)
        rows = [results[i] for i in sorted(results)]
        # 멤버 스레드의 incident 를 요청 스레드로 옮긴다 (llm_report 에 멤버별 실패가 보이도록)
        for r in rows:
            for inc in r.pop("incidents", None) or []:
                record_incident(dict(inc, ensemble_member=True, ensemble_role=self.role))
            if not r.get("ok") and r.get("error") and not r.get("timed_out"):
                pass   # 멤버의 최종 실패는 이미 멤버 complete() 가 incident 로 남겼다
            elif r.get("timed_out"):
                record_incident({"role": self.role, "provider": r["provider"], "model": r["model"], "attempts": 1, "max_attempts": 1,
                                 "elapsed_ms": round(r["ms"], 1), "errors": [r["error"]], "transient": True, "timeout_s": self.timeout_s,
                                 "prompt_chars": len(system) + len(user), "ts": time.time(), "ensemble_member": True, "ensemble_role": self.role})
        _count("llm_calls", max(0, len(rows) - 1))    # 래퍼 자신이 1회로 세어지므로 멤버 수 - 1 을 더해 실제 호출 수를 맞춘다
        ok_rows = [r for r in rows if r.get("ok")]
        usage_sum = {"input_tokens": sum(int((r.get("usage") or {}).get("input_tokens", 0) or 0) for r in rows),
                     "output_tokens": sum(int((r.get("usage") or {}).get("output_tokens", 0) or 0) for r in rows)}
        meta_members = [{k: v for k, v in r.items() if k != "text"} for r in rows]
        policy = {"wait": self.wait, "timeout_s": self.timeout_s, "min_results": self.min_results, "prompt": self.prompt_name}
        if len(ok_rows) < self.min_results:
            errs = "; ".join("%s/%s: %s" % (r["provider"], r["model"], r.get("error", "")[:120]) for r in rows if not r.get("ok"))
            raise LLMError("ensemble(%s): %d/%d 멤버만 성공 (min_results=%d) — %s" % (self.role, len(ok_rows), len(rows), self.min_results, errs),
                           transient=bool(rows) and all(r.get("transient") or r.get("timed_out") for r in rows if not r.get("ok")), kind="ensemble")
        if len(ok_rows) == 1:
            r0 = ok_rows[0]
            _pg.note("앙상블(%s): 결과 1개 — 취합 없이 %s/%s 사용" % (self.role, r0["provider"], r0["model"]))
            out1 = {"text": r0["text"], "usage": usage_sum, "ms": (time.perf_counter() - t_start) * 1000, "model": r0["model"],
                    "ensemble": {"members": meta_members, "aggregator": None, "policy": policy, "aggregated": False}}
            _note_ensemble(out1["ensemble"], self.role)
            return out1
        # 2개 이상 → 취합
        from . import prompts as _prompts
        merge_rules = _prompts.get(self.prompt_name) or _prompts.DEFAULTS.get("ensemble_merge", "")
        parts = ["[원래 작업의 시스템 프롬프트]", system.strip(), "", "[원래 작업의 사용자 프롬프트]", user.strip(), "",
                 "[후보 답변 %d개]" % len(ok_rows)]
        for n, r in enumerate(ok_rows, 1):
            parts.append("")
            parts.append("후보 %d — 모델 %s (%s), 가중치 %.2f:" % (n, r["model"], r["provider"], float(r["weight"])))
            parts.append(r["text"].strip())
        parts.append("")
        parts.append("위 규칙에 따라 원래 작업의 형식 그대로 최종 답변 하나만 출력하세요." + (" 출력은 JSON 만." if json_mode else ""))
        agg = self.aggregator if self.aggregator is not None else self.members[0][0]
        ar = agg.complete(merge_rules, "\n".join(parts), max_tokens=max_tokens, effort=str(getattr(agg, "_ens_effort", "") or effort), json_mode=json_mode)
        agg_meta = {"model": str(ar.get("model") or agg.model or ""), "provider": agg.name, "ms": float(ar.get("ms", 0) or 0),
                    "usage": dict(ar.get("usage") or {}), "attempts": ar.get("attempts", 1), "prompt_chars": len(merge_rules) + sum(len(x) for x in parts)}
        out = {"text": ar.get("text") or "", "usage": usage_sum, "ms": (time.perf_counter() - t_start) * 1000, "model": self.model,
               "ensemble": {"members": meta_members, "aggregator": agg_meta, "policy": policy, "aggregated": True}}
        _note_ensemble(out["ensemble"], self.role)
        return out


def summarize_ensemble(ens: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """앙상블 결과 → **단계 meta 에 실을 요약**. 앙상블이 아니면 None (화면이 배지를 안 그린다).

    무엇을 남기나: 멤버 수·성공 수·취합 여부·대기 정책과, **멤버마다** 모델·프로바이더·ms·글자수·
    토큰·실패 사유. 이것이 있어야 "앙상블로 돌았나", "누가 느렸나", "몇이 실패했나", "취합에 얼마나
    더 썼나" 에 Web·CLI·MCP 가 같은 답을 할 수 있다.

    본문(text)은 싣지 않는다 — trace 는 요청마다 저장되므로 크기가 배로 늘고 읽을 일도 드물다.
    """
    if not isinstance(ens, dict) or not ens.get("members"):
        return None

    def _u(d, k):
        return int(((d or {}).get("usage") or {}).get(k) or 0)

    members = []
    for m in (ens.get("members") or []):
        members.append({"model": m.get("model"), "provider": m.get("provider"),
                        "ok": bool(m.get("ok")), "ms": round(float(m.get("ms") or 0)),
                        "chars": int(m.get("chars") or 0), "weight": m.get("weight"),
                        "input_tokens": _u(m, "input_tokens"), "output_tokens": _u(m, "output_tokens"),
                        "error": (str(m.get("error"))[:160] if m.get("error") else None)})
    agg = ens.get("aggregator") or None
    return {"members": members, "n_members": len(members), "n_ok": sum(1 for m in members if m["ok"]),
            "aggregated": bool(ens.get("aggregated")), "policy": ens.get("policy"),
            "member_ms_max": max([m["ms"] for m in members] or [0]),
            "aggregator": ({"model": agg.get("model"), "provider": agg.get("provider"),
                            "ms": round(float(agg.get("ms") or 0)),
                            "input_tokens": _u(agg, "input_tokens"),
                            "output_tokens": _u(agg, "output_tokens")} if agg else None)}


def _note_ensemble(ens: Dict[str, Any], role: str) -> None:
    """앙상블이 돌았다는 사실을 **지금 열려 있는 단계**에 남긴다.

    여기 한 곳에 두는 이유: 앙상블은 역할 11개(answer·rerank·extract·summary·review·expand·verify·
    forensic·fusion·select…) 어디에나 켤 수 있다. 호출 자리마다 같은 코드를 넣으면 한 자리만 빠뜨려도
    그 역할은 "앙상블인지 알 수 없는" 상태가 된다. 프로바이더가 직접 적으면 **켜는 곳마다 저절로** 보인다.
    """
    try:
        from . import profiler as _prof
        s = summarize_ensemble(ens)
        if s:
            _prof.note_current(ensemble=dict(s, role=role))
    except Exception:
        pass       # 관측은 본 기능을 막지 않는다


def _make_ensemble(settings, role: str, cfg: Dict[str, Any], ens: Dict[str, Any]) -> EnsembleLLM:
    from . import prompts as _prompts
    """role_llm(role)['ensemble'](이미 기본값이 합쳐지고 빈 멤버가 걸러진 dict) → EnsembleLLM. 멤버·취합기는 _make_llm + apply_policy(역할 정책 상속)."""
    members: List[Tuple[BaseLLM, float]] = []
    for m in (ens.get("members") or [])[:ENSEMBLE_MAX_MEMBERS]:
        llm = _make_llm(m.get("provider") or cfg["provider"], m.get("model") or cfg["model"], settings)
        llm.role = role
        apply_policy(llm, settings, role)
        llm._ens_effort = str(m.get("effort") or "")       # 비우면 호출 시점의 역할 effort
        members.append((llm, float(m.get("weight", 1.0) or 1.0)))
    agg_cfg = ens.get("aggregator") or {}
    aggregator: Optional[BaseLLM] = None
    if agg_cfg.get("model"):
        for llm, _ in members:      # 멤버와 같은 provider/model 이면 인스턴스를 공유 (stats·회로 상태가 하나로 모인다)
            if llm.name == (agg_cfg.get("provider") or cfg["provider"]) and str(llm.model) == str(agg_cfg["model"]) and not agg_cfg.get("effort"):
                aggregator = llm
                break
        if aggregator is None:
            aggregator = _make_llm(agg_cfg.get("provider") or cfg["provider"], agg_cfg["model"], settings)
            aggregator.role = role
            apply_policy(aggregator, settings, role)
            aggregator._ens_effort = str(agg_cfg.get("effort") or "")
    # 취합 프롬프트: config 에 적은 이름 > 역할 전용 파일(ensemble_merge_<role>.md) > 공용 ensemble_merge.md
    prompt_name = _prompts.ensemble_prompt_name(role, ens.get("prompt", ""))
    ens_llm = EnsembleLLM(members, aggregator, wait=ens.get("wait", "all"), timeout_s=ens.get("timeout_s", 120),
                          min_results=ens.get("min_results", 1), prompt=prompt_name, role=role)
    return ens_llm


# 모델 카탈로그 (UI 드롭다운/문서용; 자유 입력도 허용)
MODEL_CATALOG: Dict[str, Any] = {
    "llm": {
        "anthropic": ["claude-fable-5-1", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
        "openai": ["(OpenAI-compatible: vLLM/LM Studio/Ollama/OpenRouter — 서버의 모델 id)", "gpt-4o-mini", "qwen2.5:7b", "Qwen/Qwen2.5-7B-Instruct"],
        "ollama": ["llama3.1", "qwen2.5:7b", "gemma3:12b", "exaone3.5:7.8b"],
        "headless": ["(agents.json 의 에이전트 이름: headless:opencode 처럼 provider 에 지정, model 은 에이전트 모델)"],
        "mock": ["mock"], "none": [],
    },
    "embed": {
        "hash": ["(local n-gram hashing, dim=embed_dim)"],
        "voyage": ["voyage-3.5", "voyage-3.5-lite", "voyage-code-3", "voyage-multilingual-2"],
        "openai": ["text-embedding-3-small", "text-embedding-3-large", "bge-m3", "nomic-embed-text"],
        "ollama": ["bge-m3", "nomic-embed-text", "mxbai-embed-large"],
        "st": ["paraphrase-multilingual-MiniLM-L12-v2", "BAAI/bge-m3", "intfloat/multilingual-e5-base"],
    },
    "rerank": {"api": ["BAAI/bge-reranker-v2-m3 (vLLM/TEI)", "rerank-2 (voyage)", "rerank-v3.5 (cohere)", "jina-reranker-v2-base-multilingual"],
               "cross_encoder": ["BAAI/bge-reranker-v2-m3"], "llm": ["(rerank 역할 LLM)"], "local": ["(휴리스틱)"]},
    "effort": ["low", "medium", "high"],
    "roles": {"answer": "최종 답변 생성 (인용 강제)", "rerank": "후보 문단 재정렬", "extract": "그래프 엔티티/관계 추출 (빌드)",
              "summary": "커뮤니티 요약 (빌드)", "review": "자가진화 LLM 리뷰", "expand": "질의 확장/분해",
              "verify": "근거 충분성·claim 검증", "forensic": "포렌식 진단"},
}


def make_llm(settings, role: Optional[str] = None) -> BaseLLM:
    """settings.llm_provider/llm_model 또는 (role 이 주어지면) settings.llm_roles[role] 로 LLM 생성.
    역할의 ensemble 이 켜져 있고 활성 멤버가 1개 이상이면 EnsembleLLM(멤버 병렬 + 취합) 을 돌려준다.
    provider 가 비어 있고 model 이 카탈로그(models.json)에 한 provider 로만 있으면 role_llm() 이 그 provider 를 고른다(provider_source=catalog)."""
    if role:
        cfg = settings.role_llm(role)
        provider, model = cfg["provider"], cfg["model"]
        ens = cfg.get("ensemble") if isinstance(cfg.get("ensemble"), dict) else None
        if ens and ens.get("enabled") and ens.get("members"):
            return _make_ensemble(settings, role, cfg, ens)
    else:
        provider, model = settings.llm_provider, settings.llm_model
    llm = _make_llm(provider, model, settings)
    llm.role = role or "default"
    apply_policy(llm, settings, role)
    return llm


def apply_policy(llm: BaseLLM, settings, role: Optional[str] = None) -> BaseLLM:
    """역할별 재시도 정책을 인스턴스에 적용. 우선순위: llm_roles.<role>.* > config 전역(llm_timeout/llm_retries/…) > 코드 기본값.
    headless 는 agents.json 의 timeout_s/retries/retry_backoff_s 가 있으면 그 값이 우선(_*_fixed 표식) — 단, 역할 설정에 명시된 값은 그것도 덮어쓴다."""
    try:
        pol = settings.role_llm(role) if (role and role != "default" and hasattr(settings, "role_llm")) else None
    except Exception:
        pol = None
    explicit = dict(((getattr(settings, "llm_roles", None) or {}).get(role) or {})) if role else {}

    def pick(attr: str, global_key: str, default: Any, typ, fixed_flag: str = "") -> Any:
        if pol is not None and attr in explicit and explicit.get(attr) not in (None, ""):
            try:
                return typ(explicit[attr])          # 역할에 명시 → 최우선 (headless 고정값보다도)
            except (TypeError, ValueError):
                pass
        if fixed_flag and getattr(llm, fixed_flag, None):
            return None                              # headless agents.json 값 유지
        try:
            # 역할 정책(pol)에 그 키가 없으면 전역 설정으로 떨어진다 — 정책 dict 에 없는 새 키도 안전하게 동작
            v = pol.get(attr) if pol is not None else None
            if v in (None, ""):
                v = getattr(settings, global_key, default)
            return typ(v if v not in (None, "") else default)
        except (TypeError, ValueError):
            return typ(default)

    v = pick("timeout_s", "llm_timeout", DEFAULT_LLM_TIMEOUT, int, "_timeout_fixed")
    if v is not None:
        llm.timeout = int(v or DEFAULT_LLM_TIMEOUT)
    elif getattr(llm, "_timeout_fixed", None):
        llm.timeout = int(llm._timeout_fixed)
    v = pick("retries", "llm_retries", DEFAULT_LLM_RETRIES, int, "_retries_fixed")
    if v is not None:
        llm.retries = max(0, int(v))
    v = pick("backoff_s", "llm_retry_backoff_s", DEFAULT_RETRY_BACKOFF_S, float, "_backoff_fixed")
    if v is not None:
        llm.retry_backoff_s = max(0.0, float(v))
    llm.retry_backoff = str(pick("backoff", "llm_retry_backoff", "exponential", str) or "exponential")
    llm.retry_backoff_max_s = float(pick("backoff_max_s", "llm_retry_backoff_max_s", 60.0, float) or 0)
    llm.budget_s = int(pick("budget_s", "llm_budget_s", 0, int) or 0)
    llm.circuit_failures = int(pick("circuit_failures", "llm_circuit_failures", 3, int) or 0)
    llm.circuit_cooldown_s = int(pick("circuit_cooldown_s", "llm_circuit_cooldown_s", 60, int) or 60)
    try:
        llm.http_retries = max(0, int(getattr(settings, "llm_http_retries", 2)))
    except (TypeError, ValueError):
        llm.http_retries = 2
    # 반복 억제 (역할별로도 지정 가능: llm_roles.<role>.frequency_penalty 등)
    llm.frequency_penalty = float(pick("frequency_penalty", "llm_frequency_penalty", 0.0, float) or 0.0)
    llm.presence_penalty = float(pick("presence_penalty", "llm_presence_penalty", 0.0, float) or 0.0)
    llm.repeat_penalty = float(pick("repeat_penalty", "llm_repeat_penalty", 0.0, float) or 0.0)
    return llm


def _make_llm(p: str, model: str, settings) -> BaseLLM:
    p = (p or "auto").strip()
    if p == "none":
        return NoneLLM()
    if p == "mock":
        return MockLLM()
    if p == "headless" or p.startswith("headless:"):
        from .headless import HeadlessAgentLLM
        agent = p.split(":", 1)[1] if ":" in p else "opencode"
        return HeadlessAgentLLM(agent, model, settings)
    if p == "openai":
        return OpenAICompatLLM(settings.openai_base_url, model, key_header=getattr(settings, "openai_api_key_header", "authorization"),
                               extra_headers=getattr(settings, "openai_extra_headers", None))
    why: List[str] = []
    if p in ("anthropic", "auto"):
        llm = _anthropic(model, settings)
        if llm.available or p == "anthropic":
            return llm
        why.append("anthropic: " + (getattr(llm, "reason", "") or "unavailable"))
    if p in ("ollama", "auto"):
        # provider=ollama 로 명시되면 역할 모델명을 그대로, auto 폴백이면 ollama_model 을 사용
        o = OllamaLLM(settings.ollama_url, model if p == "ollama" else settings.ollama_model)
        if o.available or p == "ollama":
            return o
        why.append("ollama: " + (getattr(o, "reason", "") or "unavailable"))
    if p == "auto":
        return NoneLLM("auto 로 고를 수 있는 프로바이더가 없음 — " + " / ".join(why) + " (openai/headless 는 provider 에 명시해야 선택됨)")
    return NoneLLM("알 수 없는 provider '%s' (auto|anthropic|openai|ollama|headless:<agent>|mock|none)" % p if p not in ("none",) else "")


def _anthropic(model: str, settings) -> BaseLLM:
    """SDK 가 있고 키가 있으면 SDK, 아니면 urllib 구현. 두 경로 모두 anthropic_base_url(게이트웨이) 과 ANTHROPIC_AUTH_TOKEN(PAT) 을 지원."""
    base = (getattr(settings, "anthropic_base_url", "") or "").strip()
    try:
        import anthropic  # noqa
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return AnthropicSDKLLM(model, settings.llm_fallbacks, base_url=base)
    except Exception:
        pass
    return AnthropicHTTPLLM(model, settings.llm_fallbacks, base_url=base)


def parse_json(text: str) -> Any:
    """LLM 출력에서 JSON 객체를 최대한 복구."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        try:
            return json.loads(text[s:e + 1])
        except Exception:
            pass
    return None


# =============================== Embeddings ===============================
class BaseEmbedder:
    name = "none"
    dim = 0
    available = False
    model: Optional[str] = None

    def embed(self, texts: List[str]) -> np.ndarray:
        _count("embed_calls", 1)
        _count("embed_texts", len(texts))
        return self._embed(texts)

    def _embed(self, texts: List[str]) -> np.ndarray:
        raise NotImplementedError

    def ping(self) -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            v = self.embed(["연결 확인용 문장입니다. HBM4 capacity expansion."])
            return {"ok": True, "ms": (time.perf_counter() - t0) * 1000, "dim": int(v.shape[1]), "detail": "embedded 1 text"}
        except Exception as e:
            return {"ok": False, "ms": (time.perf_counter() - t0) * 1000, "detail": str(e)[:300]}

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "model": getattr(self, "model", None), "available": self.available, "dim": self.dim}


class HashEmbedder(BaseEmbedder):
    """오프라인 문자 n-gram 해싱 임베딩 (단어 + 한글 bigram/trigram, sublinear tf, L2 정규화).
    의미적 임베딩은 아니지만 한국어 표기 변형(조사, 띄어쓰기)에 강하고 의존성이 없다.
    """
    name = "hash"

    def __init__(self, dim: int = 4096, ngram_weight: float = 0.5):
        self.dim = dim
        self.model = "hash-ngram-%d" % dim
        self.available = True
        self.ngram_weight = float(ngram_weight)
        self._idf: Optional[np.ndarray] = None

    def _features(self, text: str) -> Dict[int, float]:
        f: Dict[int, float] = {}
        for w in words(text):
            n = normalize_token(w)
            if not n:
                continue
            f[stable_hash("w:" + n, self.dim)] = f.get(stable_hash("w:" + n, self.dim), 0.0) + 1.0
            if re.match(r"^[가-힣]+$", n):
                for g in char_ngrams(n, 2) + char_ngrams(n, 3):
                    h = stable_hash("g:" + g, self.dim)
                    f[h] = f.get(h, 0.0) + self.ngram_weight
            elif len(n) > 4:
                for g in char_ngrams(n, 3):
                    h = stable_hash("g:" + g, self.dim)
                    f[h] = f.get(h, 0.0) + 0.3
        return f

    def fit_idf(self, texts: List[str]) -> None:
        df = np.zeros(self.dim, dtype=np.float32)
        for t in texts:
            for h in self._features(t).keys():
                df[h] += 1
        n = max(1, len(texts))
        self._idf = np.log((n + 1) / (df + 1)) + 1.0

    def _embed(self, texts: List[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for h, c in self._features(t).items():
                out[i, h] += 1.0 + np.log(c)
            if self._idf is not None:
                out[i] *= self._idf
            nrm = np.linalg.norm(out[i])
            if nrm > 0:
                out[i] /= nrm
        return out


class VoyageEmbedder(BaseEmbedder):
    name = "voyage"
    API = "https://api.voyageai.com/v1/embeddings"

    def __init__(self, model: str = "voyage-3.5"):
        self.model = model or "voyage-3.5"
        self.api_key = os.environ.get("VOYAGE_API_KEY", "")
        self.available = bool(self.api_key)
        self.dim = 1024

    def _embed(self, texts: List[str]) -> np.ndarray:
        vecs: List[List[float]] = []
        for i in range(0, len(texts), 64):
            body = {"input": texts[i:i + 64], "model": self.model}
            req = urllib.request.Request(self.API, data=json.dumps(body).encode("utf-8"),
                                         headers={"content-type": "application/json",
                                                  "authorization": "Bearer " + self.api_key}, method="POST")
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
            vecs.extend(d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"]))
        m = np.asarray(vecs, dtype=np.float32)
        self.dim = m.shape[1]
        m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
        return m


class OpenAICompatEmbedder(BaseEmbedder):
    """OpenAI-compatible /v1/embeddings (vLLM · LM Studio · Ollama · OpenRouter · text-embedding-3 · 사내 게이트웨이)."""
    name = "openai"

    def __init__(self, base_url: str, model: str, api_key: Optional[str] = None, batch: int = 64,
                 key_header: str = "authorization", extra_headers: Optional[Dict[str, str]] = None, timeout: int = 300):
        self.base_url = (base_url or "http://localhost:11434/v1").rstrip("/")
        self.model = model or "text-embedding-3-small"
        self.api_key = api_key if api_key is not None else openai_api_key(embed=True)
        self.key_header = key_header or "authorization"
        self.extra_headers = dict(extra_headers or {})
        self.timeout = timeout
        self.batch = max(1, int(batch))
        self.available = True
        self.dim = 0

    def _embed(self, texts: List[str]) -> np.ndarray:
        vecs: List[List[float]] = []
        h = auth_headers(self.api_key, self.key_header, self.extra_headers)
        for i in range(0, len(texts), self.batch):
            body = {"input": texts[i:i + self.batch], "model": self.model}
            req = urllib.request.Request(self.base_url + "/embeddings", data=json.dumps(body).encode("utf-8"), headers=h, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                raise LLMError("embeddings HTTP %s: %s%s" % (e.code, e.read().decode("utf-8", "ignore")[:300],
                                                            " ← 키/PAT 또는 openai_api_key_header 확인" if e.code in (401, 403) else ""))
            vecs.extend(d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"]))
        m = np.asarray(vecs, dtype=np.float32)
        self.dim = m.shape[1]
        m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
        return m


OLLAMA_EMBED_PREFERRED = ("bge-m3", "nomic-embed-text", "mxbai-embed-large", "snowflake-arctic-embed", "all-minilm")


def ollama_models(url: str, timeout: float = 1.0) -> List[str]:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("name") or "" for m in data.get("models", [])]
    except Exception:
        return []


class OllamaEmbedder(BaseEmbedder):
    name = "ollama"

    def __init__(self, url: str, model: str = "nomic-embed-text"):
        self.url = url.rstrip("/")
        self.model = model or "nomic-embed-text"
        self.available = OllamaLLM._ping(self) and OllamaLLM._has_model(self)  # 공유 probe 캐시 사용

    def _embed(self, texts: List[str]) -> np.ndarray:
        vecs = []
        for i in range(0, len(texts), 32):
            body = {"model": self.model, "input": texts[i:i + 32]}
            req = urllib.request.Request(self.url + "/api/embed", data=json.dumps(body).encode("utf-8"),
                                         headers={"content-type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    data = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                raise LLMError("ollama embed HTTP %s: %s (ollama pull %s ?)" % (e.code, e.read().decode("utf-8", "ignore")[:200], self.model))
            vecs.extend(data["embeddings"])
        m = np.asarray(vecs, dtype=np.float32)
        self.dim = m.shape[1]
        m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
        return m


class STEmbedder(BaseEmbedder):
    name = "st"

    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer  # noqa
        self.model_name = model or "paraphrase-multilingual-MiniLM-L12-v2"
        self.model = SentenceTransformer(self.model_name)
        self.available = True

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "model": self.model_name, "available": self.available, "dim": self.dim}

    def _embed(self, texts: List[str]) -> np.ndarray:
        m = np.asarray(self.model.encode(texts, normalize_embeddings=True), dtype=np.float32)
        self.dim = m.shape[1]
        return m


def make_embedder(settings) -> BaseEmbedder:
    """embed_provider: auto | hash | voyage | openai | ollama | st.
    auto = VOYAGE_API_KEY 있으면 voyage → Ollama 에 임베딩 모델(bge-m3 등)이 받아져 있으면 ollama → hash."""
    p = (settings.embed_provider or "auto").strip()
    if p in ("voyage", "auto"):
        v = VoyageEmbedder(settings.embed_model)
        if v.available or p == "voyage":
            return v
    if p == "st":
        return STEmbedder(settings.embed_model)
    if p == "openai":
        return OpenAICompatEmbedder(getattr(settings, "openai_embed_base_url", "") or settings.openai_base_url,
                                    settings.openai_embed_model or settings.embed_model, batch=settings.embed_batch,
                                    key_header=getattr(settings, "openai_api_key_header", "authorization"),
                                    extra_headers=getattr(settings, "openai_extra_headers", None),
                                    timeout=int(getattr(settings, "llm_timeout", 600) or 600))
    if p == "ollama":
        return OllamaEmbedder(settings.ollama_url, settings.embed_model)
    if p == "auto":
        probe = type("x", (), {"url": settings.ollama_url.rstrip("/")})()
        names = ((_OLLAMA_PROBE.get(settings.ollama_url.rstrip("/")) or (0, False, None))[2] or []) if OllamaLLM._ping(probe) else []
        for pref in OLLAMA_EMBED_PREFERRED:
            hit = next((n for n in names if n == pref or n.split(":")[0] == pref), None)
            if hit:
                return OllamaEmbedder(settings.ollama_url, hit)
    from . import tuning as _tuning
    return HashEmbedder(settings.embed_dim, _tuning.T.get("hash_ngram_weight"))
