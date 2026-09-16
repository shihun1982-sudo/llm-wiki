# -*- coding: utf-8 -*-
"""실시간 진행 상황 레지스트리 — 오래 걸리는 요청(빌드·질의·평가·trial)이 지금 어느 단계에서 무엇을 하고 있는지를
다른 스레드(Web 폴링)가 락 없이 읽을 수 있게 한다.

- bind(token, kind, label): 현재 스레드의 작업을 token 에 연결 (스레드 로컬). unbind() 로 해제.
- Profiler.stage() 가 stage_enter/stage_exit 를, BaseLLM.complete() 가 llm_start/llm_end 를, 임베딩 러너와 LLM 추출 루프가 tick() 을 호출한다.
- get(token) → {"stage": 현재 단계 경로, "detail", "done", "total", "llm": {...}, "elapsed_s", "stage_elapsed_s", "log": [...]}
- cli_monitor(token): CLI 용 — 별도 스레드가 단계/진도율 변화를 stderr 에 한 줄씩 출력하는 컨텍스트 매니저.

누가 bind 하는가: Web 서버는 job id / 요청의 progress_token 으로, CLI 는 build/query 명령이 직접 bind 한다.
"""
from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_LIVE: Dict[str, Dict[str, Any]] = {}
_TL = threading.local()
_KEEP_DONE_S = 300.0     # 끝난 항목은 5분간 유지(마지막 폴링이 완료 상태를 읽을 수 있도록)
_MAX_LOG = 400   # Web 진행 패널의 "전체 보기" 가 누적 로그를 보여 주므로 넉넉히 (한 줄 ≈ 60B → 24KB)
# 외부(다른 프로세스: CLI/MCP stdio) 발행자 — reqmgr.LiveRegistry 가 등록. cli_monitor 가 주기적으로 스냅샷을 파일로 쓰고 취소 파일을 읽는다.
_PUBLISHER: Dict[str, Any] = {"fn": None, "cancel_check": None}


class Cancelled(BaseException):
    """사용자/관리자가 진행 중인 요청을 취소했다. BaseException 이라 `except Exception` 으로 삼켜지지 않고 단계·잡·CLI 까지 올라간다."""

    def __init__(self, token: str = "", by: str = "", reason: str = ""):
        BaseException.__init__(self, "cancelled%s%s" % ((" by " + by) if by else "", (": " + reason) if reason else ""))
        self.token, self.by, self.reason = token, by, reason


def cancel(token: str, by: str = "", reason: str = "") -> bool:
    """token 의 작업에 취소를 요청한다 (협조적 취소: 다음 단계 경계·배치·LLM 호출 전에 Cancelled 가 발생). 실행 중이 아니면 False."""
    with _LOCK:
        e = _LIVE.get(token)
        if not e or e["status"] != "running":
            return False
        e["cancel"] = {"by": by or "?", "reason": reason or "", "ts": _now()}
        _log(e, "■ 취소 요청 (%s)%s" % (by or "?", (": " + reason) if reason else ""))
        return True


def cancel_requested(token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """현재 스레드(또는 token)의 취소 요청 정보. 없으면 None. 외부 발행자(파일 기반)의 취소도 확인한다."""
    tok = token or getattr(_TL, "token", None)
    if not tok:
        return None
    with _LOCK:
        e = _LIVE.get(tok)
        c = e.get("cancel") if e else None
    if c:
        return c
    chk = _PUBLISHER.get("cancel_check")
    if chk:
        try:
            ext = chk(tok)
        except Exception:
            ext = None
        if ext:
            with _LOCK:
                e = _LIVE.get(tok)
                if e and e["status"] == "running":
                    e["cancel"] = ext
                    _log(e, "■ 취소 요청 (%s, 외부)" % ext.get("by", "?"))
            return ext
    return None


def check_cancel() -> None:
    """취소 요청이 있으면 Cancelled 를 던진다. 단계 진입·tick·LLM 호출 전에 호출된다."""
    c = cancel_requested()
    if c:
        raise Cancelled(getattr(_TL, "token", "") or "", c.get("by", ""), c.get("reason", ""))


def sleep_cancellable(seconds: float, step: float = 0.5) -> None:
    """취소를 확인하며 잠든다 (LLM 재시도 backoff·대기열 대기용)."""
    end = time.time() + max(0.0, float(seconds))
    while True:
        check_cancel()
        left = end - time.time()
        if left <= 0:
            return
        time.sleep(min(step, left))


def set_publisher(fn, cancel_check=None) -> None:
    """다른 프로세스가 볼 수 있게 스냅샷을 내보내는 함수와 외부 취소 확인 함수를 등록 (reqmgr.LiveRegistry)."""
    _PUBLISHER["fn"] = fn
    _PUBLISHER["cancel_check"] = cancel_check

# 단계 이름 → 사람이 읽는 설명 (없으면 이름 그대로)
STAGE_LABELS: Dict[str, str] = {
    "health": "환경·프로바이더 사전 점검", "mcp_ingest": "외부 MCP 소스 수집", "load_corpus": "코퍼스 파일 읽기", "diff": "변경 문서 비교",
    "chunk_index": "청킹·FTS 색인·문서 계약 검사", "embed": "임베딩 계산", "graph_build": "그래프 구축", "rule_extract": "규칙 기반 엔티티/관계 추출",
    "llm_extract": "LLM 엔티티/관계 추출", "communities": "커뮤니티 탐지", "community_summary": "커뮤니티 요약(LLM)", "doc_vectors": "문서 카드 벡터",
    "wiki_pages": "위키 페이지 생성", "prune": "정리", "verify": "정합성 검증", "warm_cache": "캐시 예열", "precompute": "답변 사전 계산",
    "sync_index": "색인 동기화", "providers": "프로바이더 준비", "time_scope": "시간 표현 해석", "query_rules": "규칙 기반 질의 확장",
    "router": "채널 라우팅", "query_expand": "LLM 질의 확장", "pins": "고정 근거", "fts_search": "키워드 검색", "fts_search_rules": "확장식 검색",
    "fts_search_alt": "대체 질의 검색", "fts_search_related": "관련어 검색", "vector_search": "벡터 검색", "graph_search": "그래프 검색",
    "doc_vector_search": "문서 카드 검색", "external_rag": "외부 RAG 검색", "rrf_fuse": "융합", "boost": "부스트", "rerank_llm": "LLM 리랭크", "rerank_api": "API 리랭크",
    "rerank_local": "로컬 리랭크", "rerank_cross_encoder": "크로스인코더 리랭크", "doc_expand": "문서 단위 확장", "context": "컨텍스트 조립", "evidence_check": "근거 충분성 판정",
    "build_channel": "채널 빌드", "reindex_fts": "FTS 재색인", "forensic_expect": "기대 결과 포렌식",
    "fallback": "확장 재검색(fallback)", "evidence_compress": "근거 압축(LLM)", "answer_llm": "LLM 답변 생성", "answer_extractive": "추출식 답변",
    "answer_insufficient": "근거 부족 응답", "claim_check": "claim 검증", "answer_refine": "답변 재작성(LLM)", "forensic": "포렌식 진단",
    "evolve_capture": "자가진화 제안 수집", "episode": "에피소드 기록", "run": "평가 실행", "eval": "평가",
}


def _now() -> float:
    return time.time()


def bind(token: str, kind: str = "", label: str = "", client: Optional[Dict[str, Any]] = None) -> None:
    """현재 스레드에서 이후 발생하는 단계/LLM/tick 이벤트를 token 항목에 기록한다.
    client: {user, role, ip, origin(web|mcp|cli|schedule|watch), agent} — 서버 모니터/활동 목록에 표시."""
    if not token:
        return
    _TL.token = token
    # 사용자가 보낸 문자열의 짝 없는 서러게이트를 걸러 둔다. 그대로 두면 이 항목이 들어간 뒤부터
    # 진행 목록을 JSON 으로 내보내거나 data/live 에 쓸 때마다 실패한다.
    try:
        str(label).encode("utf-8")
    except UnicodeEncodeError:
        label = str(label).encode("utf-8", "replace").decode("utf-8", "replace")
    with _LOCK:
        _LIVE[token] = {"token": token, "kind": kind, "label": label, "started": _now(), "finished": None, "status": "running",
                        "path": [], "stage": None, "stage_started": None, "detail": "", "done": None, "total": None,
                        "llm": {"active": False, "provider": None, "model": None, "calls": 0, "started": None, "role": None, "ms_total": 0.0},
                        "log": [], "cancel": None, "client": dict(client or {}), "pid": os.getpid(), "queue": None}
        _gc()


def set_client(token: str, **client: Any) -> None:
    with _LOCK:
        e = _LIVE.get(token)
        if e:
            e["client"].update({k: v for k, v in client.items() if v is not None})


def set_queue(token: str, position: Optional[int], waiting: Optional[int] = None) -> None:
    """대기열 위치 표시 (None 이면 대기 종료)."""
    with _LOCK:
        e = _LIVE.get(token)
        if e:
            e["queue"] = None if position is None else {"position": int(position), "waiting": int(waiting or 0), "since": e.get("queue", {}).get("since") if e.get("queue") else _now()}


def unbind(status: str = "done", detail: str = "") -> None:
    token = getattr(_TL, "token", None)
    _TL.token = None
    if not token:
        return
    with _LOCK:
        e = _LIVE.get(token)
        if e:
            e["status"] = status
            e["finished"] = _now()
            e["stage"] = None
            e["path"] = []
            e["queue"] = None
            if detail:
                e["detail"] = detail
            e["llm"]["active"] = False
    fn = _PUBLISHER.get("fn")
    if fn:
        try:
            fn(token, get(token), final=True)
        except Exception:
            pass


def current_token() -> Optional[str]:
    return getattr(_TL, "token", None)


def _entry() -> Optional[Dict[str, Any]]:
    token = getattr(_TL, "token", None)
    return _LIVE.get(token) if token else None


def stage_enter(name: str, meta: Optional[Dict[str, Any]] = None) -> None:
    check_cancel()
    with _LOCK:
        e = _entry()
        if not e:
            return
        e["path"].append(name)
        e["stage"] = name
        e["stage_started"] = _now()
        e["done"] = None
        e["total"] = None
        e["detail"] = _brief(meta)
        _log(e, "▶ %s%s" % (STAGE_LABELS.get(name, name), (" · " + e["detail"]) if e["detail"] else ""))


def stage_exit(name: str, ms: float = 0.0, error: str = "") -> None:
    with _LOCK:
        e = _entry()
        if not e:
            return
        if e["path"] and e["path"][-1] == name:
            e["path"].pop()
        e["stage"] = e["path"][-1] if e["path"] else None
        e["stage_started"] = _now() if e["stage"] else None
        e["done"] = None
        e["total"] = None
        e["detail"] = ""
        if error:
            _log(e, "✖ %s 실패: %s" % (STAGE_LABELS.get(name, name), error[:120]))
        else:
            _log(e, "✔ %s %.0fms" % (STAGE_LABELS.get(name, name), ms))


def tick(done: Optional[int] = None, total: Optional[int] = None, detail: str = "") -> None:
    """진도율 갱신 (예: 임베딩 배치, LLM 추출 청크 i/n). 취소 확인 지점이기도 하다."""
    check_cancel()
    with _LOCK:
        e = _entry()
        if not e:
            return
        if done is not None:
            e["done"] = int(done)
        if total is not None:
            e["total"] = int(total)
        if detail:
            e["detail"] = detail[:200]


def note(msg: str) -> None:
    with _LOCK:
        e = _entry()
        if e:
            _log(e, msg)


def llm_start(provider: str, model: str, role: str = "") -> None:
    with _LOCK:
        e = _entry()
        if not e:
            return
        e["llm"].update({"active": True, "provider": provider, "model": model, "role": role, "started": _now()})


def llm_end(ms: float = 0.0, error: str = "") -> None:
    with _LOCK:
        e = _entry()
        if not e:
            return
        e["llm"]["active"] = False
        e["llm"]["calls"] += 1
        e["llm"]["ms_total"] += float(ms or 0)
        e["llm"]["started"] = None
        if error:
            _log(e, "✖ LLM 호출 실패: %s" % error[:120])


def get(token: str) -> Optional[Dict[str, Any]]:
    """폴링용 스냅샷 (락 최소 보유, 경과 시간 계산 포함)."""
    with _LOCK:
        e = _LIVE.get(token)
        if not e:
            return None
        now = _now()
        out = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in e.items()}
    out["elapsed_s"] = round(((out["finished"] or now) - out["started"]), 1)
    out["stage_elapsed_s"] = round(now - out["stage_started"], 1) if out.get("stage_started") else None
    out["stage_label"] = STAGE_LABELS.get(out["stage"] or "", out["stage"] or "")
    out["path_labels"] = [STAGE_LABELS.get(p, p) for p in out["path"]]
    llm = out["llm"]
    llm["elapsed_s"] = round(now - llm["started"], 1) if llm.get("active") and llm.get("started") else None
    out["pct"] = round(100.0 * out["done"] / out["total"], 1) if out.get("total") and out.get("done") is not None else None
    if out.get("pct") is not None and out.get("stage_elapsed_s") and out["pct"] > 0:
        out["eta_s"] = round(out["stage_elapsed_s"] * (100.0 - out["pct"]) / out["pct"], 0)   # 현재 단계 기준 남은 시간 추정
    else:
        out["eta_s"] = None
    out["cancel_requested"] = bool(out.get("cancel"))
    return out


def all_running() -> List[Dict[str, Any]]:
    with _LOCK:
        toks = [t for t, e in _LIVE.items() if e["status"] == "running"]
    return [x for x in (get(t) for t in toks) if x]


def all_recent(limit: int = 100) -> List[Dict[str, Any]]:
    """실행 중 + 최근 끝난 항목 (finished 역순)."""
    with _LOCK:
        toks = sorted(_LIVE.keys(), key=lambda t: (_LIVE[t]["finished"] or float("inf"), _LIVE[t]["started"]), reverse=True)[:limit]
    return [x for x in (get(t) for t in toks) if x]


def summary_line(snap: Dict[str, Any]) -> str:
    """스냅샷 한 줄 요약 (CLI/로그용). 예: '▶ 임베딩 계산 › 12/190 (6%) · 3.2s · LLM 대기 ollama/llama3.1 4s'"""
    if not snap:
        return ""
    parts: List[str] = []
    q = snap.get("queue")
    if q:
        parts.append("대기열 %s번째 (대기 %s)" % (q.get("position"), q.get("waiting")))
    path = snap.get("path_labels") or []
    if path:
        parts.append(" › ".join(path[-2:]))
    if snap.get("pct") is not None:
        parts.append("%s/%s (%.0f%%)%s" % (snap.get("done"), snap.get("total"), snap["pct"], (" ETA %ds" % snap["eta_s"]) if snap.get("eta_s") else ""))
    elif snap.get("detail"):
        parts.append(str(snap["detail"]))
    if snap.get("stage_elapsed_s") is not None:
        parts.append("단계 %.0fs" % snap["stage_elapsed_s"])
    if snap.get("elapsed_s") is not None:
        parts.append("전체 %.0fs" % snap["elapsed_s"])
    llm = snap.get("llm") or {}
    if llm.get("active"):
        parts.append("LLM 응답 대기 %s/%s %.0fs" % (llm.get("provider"), llm.get("model"), llm.get("elapsed_s") or 0))
    if snap.get("cancel_requested"):
        parts.append("취소 중…")
    return " · ".join(parts)


@contextlib.contextmanager
def cli_monitor(token: str, kind: str = "", label: str = "", enabled: bool = True, interval: float = 1.0, stream=None,
                client: Optional[Dict[str, Any]] = None):
    """CLI 에서 오래 걸리는 명령을 감싼다: bind 하고, 백그라운드 스레드가 단계/진도율이 바뀔 때마다 stderr 에 한 줄 출력.
    enabled=False 면 bind 만 하고 출력하지 않는다(--json 등). 예외가 나면 status=error 로, 취소(Ctrl+C·서버 취소)면 cancelled 로 unbind.
    발행자(reqmgr.LiveRegistry)가 등록되어 있으면 2초마다 스냅샷을 파일로 내보내 서버 모니터가 CLI 작업도 보고 취소할 수 있게 한다."""
    bind(token, kind, label, client=dict(client or {}, origin=(client or {}).get("origin") or "cli"))
    out = stream or sys.stderr
    stop = threading.Event()
    last: List[str] = [""]
    pub = _PUBLISHER.get("fn")

    def loop() -> None:
        t_last = 0.0
        t_pub = 0.0
        while not stop.wait(0.25):
            snap = get(token)
            if not snap or snap.get("status") != "running":
                continue
            now = time.time()
            if pub and now - t_pub >= 2.0:
                t_pub = now
                try:
                    pub(token, snap, final=False)
                except Exception:
                    pass
            if not enabled:
                continue
            line = summary_line(snap)
            # 단계가 바뀌면 즉시, 같은 단계는 interval 마다 (LLM 대기 시간·경과 초가 계속 변하므로 절제)
            stage_key = "|".join(snap.get("path") or [])
            if stage_key != last[0] or (line and now - t_last >= interval):
                last[0] = stage_key
                t_last = now
                if line:
                    try:
                        out.write("  ⏳ %s\n" % line)
                        out.flush()
                    except Exception:
                        pass

    th = threading.Thread(target=loop, daemon=True) if (enabled or pub) else None
    if th:
        th.start()
    try:
        if pub:
            try:
                pub(token, get(token), final=False)
            except Exception:
                pass
        yield token
    except Cancelled as e:
        unbind("cancelled", str(e)[:200])
        raise
    except KeyboardInterrupt:
        unbind("cancelled", "Ctrl+C")
        raise
    except BaseException as e:
        unbind("error", str(e)[:200])
        raise
    else:
        unbind("done")
    finally:
        stop.set()
        if th:
            th.join(timeout=1.0)


def _brief(meta: Optional[Dict[str, Any]]) -> str:
    if not meta:
        return ""
    parts = []
    for k, v in meta.items():
        if isinstance(v, (int, float, str, bool)) and k not in ("debug",):
            s = str(v)
            if len(s) > 40:
                s = s[:40] + "…"
            parts.append("%s=%s" % (k, s))
        if len(parts) >= 4:
            break
    return " ".join(parts)


def _log(e: Dict[str, Any], msg: str) -> None:
    e["log"].append("%s %s" % (time.strftime("%H:%M:%S"), msg))
    if len(e["log"]) > _MAX_LOG:
        del e["log"][: len(e["log"]) - _MAX_LOG]


def _gc() -> None:
    now = _now()
    for t in [t for t, e in _LIVE.items() if e["finished"] and now - e["finished"] > _KEEP_DONE_S]:
        _LIVE.pop(t, None)
