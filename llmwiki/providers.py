# -*- coding: utf-8 -*-
"""LLM / 임베딩 프로바이더.

Python 3.7 환경(공식 anthropic SDK 설치 불가: jiter>=0.4 는 3.8+)을 고려해 표준 라이브러리 urllib 로
Anthropic Messages API 를 직접 호출한다. SDK 가 설치 가능한 환경(3.9+)이라면 AnthropicSDKLLM 이 자동 선택된다.

LLM:   none | mock | anthropic | ollama
Embed: hash (로컬, 문자 n-gram 해싱; 오프라인) | voyage | ollama | st(sentence-transformers)
"""
from __future__ import annotations

import json
import os
import random
import re
import socket
import threading
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

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

    def _complete(self, system: str, user: str, max_tokens: int, effort: str, json_mode: bool) -> Dict[str, Any]:
        t0 = time.perf_counter()
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
        elif "TASK=rerank" in system:
            ids = re.findall(r"\[(\d+)\]", user)
            text = json.dumps({"ranking": [int(i) for i in dict.fromkeys(ids)]})
        elif "TASK=summarize" in system:
            text = "(mock summary) " + user[:200].replace("\n", " ")
        elif "TASK=rewrite" in system:
            q = user.split("\n")[-1].strip()
            text = json.dumps({"queries": [q + " 관련 내용", q.replace("?", "") + " 상세"], "keywords": []}, ensure_ascii=False)
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
            ok = (self.model in ids) or not ids
            return {"ok": ok, "ms": (time.perf_counter() - t0) * 1000, "models": ids[:40],
                    "detail": "connected %s" % self.base_url + ("" if ok else "; model %s not in list" % self.model)}
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
            ok = any(n == self.model or n.split(":")[0] == self.model for n in names)
            return {"ok": ok, "ms": (time.perf_counter() - t0) * 1000, "models": names,
                    "detail": "connected" if ok else "model %s not pulled (ollama pull %s)" % (self.model, self.model)}
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
            ok = (self.model in ids) or not ids
            return {"ok": ok, "ms": (time.perf_counter() - t0) * 1000, "models": ids[:40],
                    "detail": "connected %s" % self.base_url + ("" if ok else "; model %s not in list" % self.model)}
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
    """settings.llm_provider/llm_model 또는 (role 이 주어지면) settings.llm_roles[role] 로 LLM 생성."""
    if role:
        cfg = settings.role_llm(role)
        provider, model = cfg["provider"], cfg["model"]
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
