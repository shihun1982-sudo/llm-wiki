# -*- coding: utf-8 -*-
"""공통 요청 관리자 (Web · MCP · CLI 콘솔 · 스케줄러 · 워처) — 2026-09-15 병렬화.

역할
  1. 읽기/쓰기 락: 질의·검색(read) 은 여러 개가 동시에, 빌드·설정 저장·스냅샷 복원(write) 은 하나만. 쓰기는 진행 중인 읽기가 끝나기를
     기다리고(writer preference: 쓰기가 기다리는 동안 새 읽기는 뒤로 줄을 선다), 정책에 따라 증분 빌드 중에는 읽기를 허용한다(soft write).
  2. 용량 제어: 동시 읽기 슬롯(max_parallel_reads) · 사용자/IP 별 동시 수 · 대기열 상한/대기 시간(초과 → 503) · 분당 요청 수(→ 429).
  3. 접근 제어: IP/사용자 차단 목록, 허용 IP 목록, 점검 모드(admin 만).
  4. 관측: 실행 중/대기 중/최근 완료 요청, 클라이언트(IP·사용자·출처)별 통계, 지연 통계, 거부 통계. viewer 도 활동 목록은 볼 수 있다.
  5. 취소: 진행 레지스트리(progress) 의 협조적 취소 + 대기열에서의 취소 + 시간 제한 감시(watchdog).
  6. 다른 프로세스(CLI `query`/`build`, MCP stdio) 의 작업도 data/live/ 파일로 보고 취소할 수 있다(LiveRegistry).

설정: <루트>/server.json (원본 setup/server.example.json). 없으면 코드 기본값. admin 이 Web/CLI 로 바꾸면 파일에 저장된다.
RAG 성능에 주는 영향: 티켓 발급은 딕셔너리 갱신 + 락 몇 개(µs 단위)이고, 검색·LLM 호출 경로에는 손대지 않는다.
"""
from __future__ import annotations

import collections
import contextlib
import copy
import json
import os
import socket
import threading
import time
import uuid
from typing import Any, Deque, Dict, List, Optional

from . import atomicio
from . import progress as _pg
from . import reqledger as _led
from .config import ROOT, path_for

DEFAULTS: Dict[str, Any] = {
    "_comment": "서버 요청 관리자 설정. 설명: docs/CONCURRENCY.md. admin 은 Web › Observability › 서버 모니터 또는 `python -m llmwiki server limits set k=v` 로 바꿀 수 있다.",
    "concurrency": {
        "max_parallel_reads": 8,          # 동시에 실행할 질의/검색/MCP 도구 호출 수 (LLM 응답 대기가 대부분이라 CPU 코어 수보다 크게 잡아도 된다)
        "max_parallel_per_user": 3,       # 같은 사용자(또는 게스트 IP)가 동시에 실행할 수 있는 요청 수
        "max_parallel_per_ip": 6,         # 같은 IP 의 동시 요청 수 (프록시 뒤라면 크게)
        # 평가·trial·사전계산처럼 **안에서 질의를 여러 번 도는 배치 작업**의 동시 실행 수.
        # 이런 작업은 한 번에 수 분~수십 분 읽기 슬롯을 물고 있어서, 여러 개가 동시에 돌면
        # 대화형 질의가 전부 대기열로 밀린다(2026-09-16: eval 4개가 9분째 슬롯을 다 차지했다). 0 = 무제한.
        "max_parallel_batch": 1,
        "max_body_mb": 8,                 # 요청 본문 상한(MB). 초과 → 413. 0 = 무제한
        # HTTP keep-alive 유휴 시간(초). 브라우저는 한 사이트에 연결을 6개까지만 열기 때문에,
        # 연결을 재사용하지 않으면 오래 걸리는 질의가 갱신 요청을 막는다. 0 = keep-alive 끔(HTTP/1.0).
        "keep_alive_s": 30,
        # 슬롯을 기다리는 요청 상한 (초과 → 503 busy). 2026-09-23: 64 → 128.
        # 질의 1건이 100초 가까이 걸리는 환경(로컬 LLM)에서 64는 30명이 두 번씩만 물어도 가득 찬다.
        # 대기열이 길어도 queue_timeout_s 가 수명을 끊으므로, 즉시 503(queue_full)보다 FIFO 대기가 낫다.
        "queue_max": 128,
        "queue_timeout_s": 120,           # 슬롯을 기다리는 최대 시간 (초과 → 503)
        "reads_during_build": "incremental",   # never | incremental | always — 빌드 중 질의 허용 범위 (incremental: 증분 빌드 동안만)
        # 빌드(쓰기)가 진행 중인 읽기가 끝나길 기다리는 최대 시간. 빌드를 취소시키는 값이므로 48시간으로 둔다.
        "write_wait_timeout_s": 172800,
        # 읽기가 배타 작업(전체 빌드 등)이 끝나길 기다리는 최대 시간. 이 값은 **빌드가 아니라 질의**의 수명이다.
        # 크게 잡으면 전체 재빌드 동안 질의 스레드가 계속 쌓여 서버가 마비되므로 짧게 유지하고(초과 → 503 재시도 안내),
        # 빌드 중에도 질의를 받으려면 reads_during_build 로 조절한다.
        "read_wait_timeout_s": 900,
    },
    "timeouts": {                          # 협조적 시간 제한: 넘으면 취소 요청 (다음 단계·배치·LLM 호출 전에 멈춤). 0 = 없음
        # cli_s: Web 콘솔이 부르는 CLI 는 읽기 슬롯을 잡은 채 실행된다. 0(무제한)이면 느린 한 명령이
        # 슬롯을 계속 차지해 다른 사용자가 전부 대기열에 쌓인다(2026-09-15 멍키 테스트로 확인).
        # 빌드처럼 오래 걸리는 CLI 는 weight 가 soft/exclusive 이므로 job_s 를 따른다.
        # job_s: 빌드·평가·스냅샷 같은 배치 작업의 상한. 48시간(172800). 0 으로 두면 제한이 없다.
        "query_s": 900, "search_s": 120, "job_s": 172800, "mcp_s": 900, "cli_s": 600,
    },
    "rate_limit": {
        "enabled": True,
        "per_user_per_min": 60,           # 사용자별 분당 요청(질의·검색·도구 호출·쓰기) 수
        "per_ip_per_min": 120,            # IP 별 분당 요청 수
        "query_per_user_per_min": 20,     # 사용자별 분당 질의(kind=query) 수 — LLM 비용 보호
        "exempt_roles": ["admin"],
    },
    "sessions": {
        "enforce": False,                 # True 면 로그인 세션을 서버가 기억하고(max_per_user 초과 시 가장 오래된 세션 만료) admin 이 강제 로그아웃할 수 있다
        "max_per_user": 5,
        "idle_timeout_min": 720,
    },
    "access": {
        "block_ips": [],                  # 차단 IP (정확히 일치 또는 '10.1.2.' 처럼 접두어)
        "block_users": [],                # 차단 사용자 id
        "allow_ips": [],                  # 비어 있지 않으면 이 IP(접두어)만 허용
        "maintenance_mode": False,        # True 면 아래 역할 외의 요청은 503 (점검 안내)
        "maintenance_message": "서버 점검 중입니다. 잠시 후 다시 시도하세요.",
        "maintenance_allow_roles": ["admin"],
    },
    "monitor": {
        "history_size": 500,              # 최근 완료 요청 보관 수 (메모리)
        "viewer_can_see_activity": True,  # viewer 도 GET /api/activity 로 실행 중 작업 목록을 본다
        "show_user_to_viewer": True,      # 활동 목록에 사용자 id 표시 (False 면 역할만)
        "slow_request_ms": 30000,         # 이보다 오래 걸린 요청은 slow 로 표시/집계
        "stats_window_min": 15,           # 처리량/지연 통계 창
        "live_dir": "data/live",          # 다른 프로세스(CLI/MCP stdio) 작업 파일 위치
        "live_stale_s": 90,               # heartbeat 가 이보다 오래되면 죽은 것으로 간주
    },
    # 요청 원장(2026-09-23, 요청 3·4) — 서버로 들어온 **모든 요청**을 data/ledger/*.jsonl 에 남긴다.
    # SQLite 가 아닌 이유: 원장이 기록해야 할 대표 사건이 "DB 가 잠겨 기록하지 못했다" 이고,
    # 스냅샷 복원(DB 파일 교체)에도 되감기지 않아야 하기 때문이다. 상세: docs/REQUEST_LEDGER.md
    "ledger": {
        "enabled": True,
        "dir": "data/ledger",          # data/… 상대 경로는 data_dir 아래로 본다. logs/ 가 아닌 이유: 로그 총량 정리에 지워지면 안 된다
        "keep_days": 30,               # 보존 기간(일). 0 = 지우지 않음
        "max_mb": 512,                 # 폴더 총량 상한. 넘으면 오래된 파일부터 삭제
        "include_get": "heavy",        # none | heavy | all — 폴링(/api/progress·/api/activity)이 원장을 덮지 않게
        "queue_max": 10000,            # writer 큐 상한. 넘으면 드롭하고 **드롭 수를 센다**(조용한 유실 금지)
        "flush_ms": 200,               # writer 가 모아서 쓰는 간격(ms)
        "live_rows": 500,              # 목록을 빠르게 그리기 위한 메모리 색인 크기
        "running_grace_s": 900,        # close 없는 항목을 '중단(unknown)' 으로 볼 때까지의 시간(초)
        # 화면(Observability › 요청) 갱신 주기(ms). 목록·상태 스트립·시간 막대가 **한 번의 요청으로** 함께 갱신된다.
        # 짧게 잡으면 사람이 많을수록 폴링이 늘어난다(브라우저 1개당 1초에 한 번씩 × 접속자 수).
        # 0 = 자동 갱신 끔(새로고침 버튼만). 화면에서 사용자가 더 길게 바꿀 수도 있다.
        "refresh_ms": 5000,
        "refresh_choices_ms": [2000, 5000, 10000, 30000, 0],   # 화면 드롭다운에 보일 값 (0 = 끔)
        "histogram_buckets": 60,       # 시간 막대를 몇 칸으로 나눌지 (기간 ÷ 이 수 = 한 칸의 폭)
    },
    "debug": {
        # 500 응답에 스택트레이스를 포함할지. 기본 false — 트레이스는 error.log 에 참조 id 와 함께 남고
        # 클라이언트는 {"code":"internal","ref":…} 만 받는다 (admin 은 항상 트레이스를 본다). 개발 PC 에서만 true.
        "expose_trace": False,
    },
    # Web UI 협업(휘발성 채팅 + 게시판). **부수 기능** — 토글 collab 으로 끄면 화면에서 사라지고
    # 질의·빌드·MCP 에는 영향이 없다. 상세: docs/COLLAB.md · llmwiki/collab.py
    "collab": {
        "enabled": True,
        "retain_min": 120,                # 채팅을 메모리에 두는 시간(분) — 지나면 사라진다(휘발성)
        "max_messages": 500,              # 메모리에 두는 최대 메시지 수
        "max_chars": 2000,                # 메시지 한 건의 최대 길이
        "board_max": 2000,                # 게시판 글 최대 수 (data/collab/board.json)
        "board_keep_days": 365,           # 게시글 보존 기간(일). 0 = 지우지 않음
        "bubble_font_start_px": 12,       # 말풍선 시작 글자 크기 (admin 이 조정)
        "bubble_font_step_px": 1,         # 얼마씩 키울지
        "bubble_font_step_min": 30,       # 몇 분마다 키울지 (기본 30분에 1px)
        "bubble_font_max_px": 28,         # 상한
        "idle_hide_min": 240,             # 이만큼 조용하면 캐릭터를 숨긴다(분). 0 = 계속 표시
    },
    # MCP 서버(llmwiki/mcp.py · mcp_client.py) 운영 수치 — 2026-09-18 요청 3 (다른 MCP 서버와 공존·확장). 상세: docs/MCP.md §9.
    # 읽는 곳: mcp.mcp_config() (5초 캐시). 환경변수 LLMWIKI_SERVER_MCP_<KEY> 로도 덮어쓸 수 있다.
    "mcp": {
        "fed_cache_ttl_s": 300,           # 페더레이션 소스의 tools/list 캐시(초). 연결에 실패한 소스도 이 시간 동안은 다시 시도하지 않는다(백오프)
        "fed_list_timeout_s": 5,          # tools/list 가 원격 소스의 tools/list 를 기다리는 최대 시간(초). 소스가 느려도 우리 도구 목록은 이 안에 나온다
        "source_timeout_s_default": 10,   # mcp_sources.json 소스에 timeout_s 가 없을 때의 호출 타임아웃(초; retrieve/expose 중계/test)
        "ingest_timeout_s_default": 60,   # ingest(문서 내려받기)는 오래 걸리므로 timeout_s 가 없을 때 이 값을 쓴다
        "bridge_timeout_s": 120,          # `mcp --connect` 브리지가 원격 /mcp 응답을 기다리는 최대 시간(초) — 예전에는 llm_timeout 을 빌려 썼다
        "max_k": 50,                      # 도구 인자 k(근거/결과 수)의 상한 — 붙는 LLM 이 k=10000 을 보내도 이 값으로 잘린다
        "max_doc_chars": 20000,           # wiki_doc max_chars 의 상한(문자)
        "plugin_rescan_s": 5,             # 플러그인 폴더(mcp_plugins_dir)의 mtime 을 다시 검사하는 최소 간격(초). 0 = 요청마다
        # initialize 응답의 instructions — 붙는 LLM 이 처음 읽는 사용 안내. 사내 용어에 맞게 바꿔도 된다
        "instructions": "사내 LLM Wiki. wiki_query 로 질문(인용 [C#] 포함 답변) → 결과가 부족하면 wiki_forensic(request_id, expected_docs/terms) 로 원인 분석, wiki_feedback 으로 피드백, wiki_propose 로 제안.",
    },
}

SERVER_JSON_NAME = "server"


def _data_dir() -> str:
    """원장 폴더의 기준이 되는 data_dir (격리 환경도 따라가게). 설정을 못 읽으면 ROOT.

    `LLMWIKI_DATA_DIR_PATH` 가 있으면 그것을 **먼저** 본다 (2026-09-23).
    이유: 테스트와 검증 하네스는 `Settings(data_dir=임시폴더)` 로 격리하는데, RequestManager 는
    그 Settings 를 받지 않고 프로젝트 설정을 읽는다. 그래서 **테스트가 실제 data/ledger 에 기록**했고,
    실사용 기록 사이에 `bob`·`u1`·`key:test-client` 같은 픽스처가 섞였다(실측 2,616건 중 다수).
    `LLMWIKI_LOGS_DIR_PATH` 가 로그에 대해 하는 것과 같은 장치다.
    """
    env = os.environ.get("LLMWIKI_DATA_DIR_PATH", "").strip()
    if env:
        return env if os.path.isabs(env) else os.path.normpath(os.path.join(ROOT, env))
    try:
        from .config import load_settings
        return str(load_settings().data_dir)
    except Exception:
        return ROOT


def server_path() -> str:
    try:
        return path_for(SERVER_JSON_NAME)
    except KeyError:
        p = os.environ.get("LLMWIKI_SERVER_PATH") or os.path.join(ROOT, "server.json")
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(ROOT, p))


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or server_path()
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(path):
        try:
            cfg = _merge(cfg, atomicio.read_json(path, {}) or {})
        except Exception:
            pass
    # 환경변수: LLMWIKI_SERVER_<SECTION>_<KEY> (예 LLMWIKI_SERVER_CONCURRENCY_MAX_PARALLEL_READS=16)
    for sect, vals in list(cfg.items()):
        if not isinstance(vals, dict):
            continue
        for k in [x for x in vals if str(x).startswith("_")]:
            vals.pop(k, None)          # 파일에 적힌 설명용 _comment 는 설정 값이 아니다
        for k, v in vals.items():
            env = os.environ.get("LLMWIKI_SERVER_%s_%s" % (sect.upper(), k.upper()))
            if env is None or env == "":
                continue
            vals[k] = coerce_like(v, env)
    return cfg


def coerce_like(cur: Any, v: Any) -> Any:
    if isinstance(cur, bool):
        return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(cur, int) and not isinstance(cur, bool):
        return int(float(v))
    if isinstance(cur, float):
        return float(v)
    if isinstance(cur, list):
        if isinstance(v, list):
            return v
        return [x.strip() for x in str(v).replace(";", ",").split(",") if x.strip()]
    return v


def save_config(cfg: Dict[str, Any], path: Optional[str] = None) -> str:
    path = path or server_path()
    data = {"_comment": DEFAULTS["_comment"]}
    data.update({k: v for k, v in cfg.items() if not k.startswith("_")})
    return atomicio.write_json(path, data)


# 안에서 질의를 여러 번 도는 배치 작업 — 동시 실행 수를 따로 제한한다 (max_parallel_batch)
BATCH_KINDS = ("eval", "trial", "precompute", "fusion")


def safe_text(s: Any, limit: int = 160) -> str:
    """요청 목록·이력에 남길 문자열을 안전하게 만든다.

    사용자가 보낸 질의·라벨에는 짝 없는 서러게이트(예: '\\ud83d')가 섞여 들어올 수 있다.
    그대로 두면 JSON 으로 내보내거나 파일에 쓸 때 UnicodeEncodeError 가 나고,
    그런 값이 이력에 한 번 들어가면 **이후 모든 모니터 조회가 실패한다**.
    """
    t = str(s or "")[:limit]
    try:
        t.encode("utf-8")
        return t
    except UnicodeEncodeError:
        return t.encode("utf-8", "replace").decode("utf-8", "replace")


class Rejected(Exception):
    """요청을 받지 않음. status: 429(속도 제한) · 503(대기열/점검/차단) · 403(차단).

    `limit` 에는 **무엇이 막았는지**를 담는다 (2026-09-23): 어느 설정 키가 이 한도를 정하고,
    그때 값이 얼마였으며, 지금 얼마나 차 있었는지. 거절은 로그 파일에 남지 않으므로
    이 정보가 없으면 나중에 "왜 거절됐는지" 를 알 방법이 아예 없다 — 요청 원장이 이것을 그대로 보여 준다.
    """

    def __init__(self, status: int, message: str, retry_after: Optional[float] = None, code: str = "",
                 limit: Optional[Dict[str, Any]] = None):
        Exception.__init__(self, message)
        self.status, self.message, self.retry_after, self.code = status, message, retry_after, code
        self.limit = dict(limit or {})

    def body(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"error": self.message, "code": self.code or ("rate_limited" if self.status == 429 else "busy")}
        if self.retry_after is not None:
            d["retry_after_s"] = round(float(self.retry_after), 1)
        if self.limit:
            d["limit"] = self.limit
        return d


# ---------------------------------------------------------------- RW lock (writer preference, soft write)
class RWLock:
    """읽기 여러 개 / 쓰기 하나. 쓰기 모드: exclusive(읽기도 막음) · soft(다른 쓰기만 막고 읽기는 허용 — 증분 빌드용)."""

    def __init__(self) -> None:
        self._cv = threading.Condition(threading.Lock())
        self.readers = 0
        self.writer: Optional[str] = None          # None | "exclusive" | "soft"
        self.writer_label = ""
        self.writers_waiting = 0

    def acquire_read(self, timeout: Optional[float] = None, on_wait=None) -> bool:
        deadline = None if timeout is None else time.time() + timeout
        with self._cv:
            waited = False
            while self.writer == "exclusive" or self.writers_waiting > 0:
                if not waited and on_wait:
                    on_wait(self.writer_label or "write")
                waited = True
                if deadline is not None:
                    left = deadline - time.time()
                    if left <= 0:
                        return False
                    self._cv.wait(min(left, 1.0))
                else:
                    self._cv.wait(1.0)
                _pg.check_cancel()
            self.readers += 1
            return True

    def release_read(self) -> None:
        with self._cv:
            self.readers = max(0, self.readers - 1)
            self._cv.notify_all()

    def acquire_write(self, mode: str = "exclusive", timeout: Optional[float] = None, on_wait=None, label: str = "") -> bool:
        deadline = None if timeout is None else time.time() + timeout
        with self._cv:
            self.writers_waiting += 1
            try:
                waited = False

                def blocked() -> bool:
                    if self.writer is not None:
                        return True
                    return mode == "exclusive" and self.readers > 0
                while blocked():
                    if not waited and on_wait:
                        on_wait("%d readers" % self.readers if self.writer is None else (self.writer_label or self.writer))
                    waited = True
                    if deadline is not None:
                        left = deadline - time.time()
                        if left <= 0:
                            return False
                        self._cv.wait(min(left, 1.0))
                    else:
                        self._cv.wait(1.0)
                    _pg.check_cancel()
                self.writer = mode
                self.writer_label = label
                return True
            finally:
                self.writers_waiting -= 1
                self._cv.notify_all()

    def release_write(self) -> None:
        with self._cv:
            self.writer = None
            self.writer_label = ""
            self._cv.notify_all()

    def state(self) -> Dict[str, Any]:
        with self._cv:
            return {"readers": self.readers, "writer": self.writer, "writer_label": self.writer_label, "writers_waiting": self.writers_waiting}


# ---------------------------------------------------------------- 다른 프로세스의 작업 (파일 기반)
class LiveRegistry:
    """data/live/<token>.json 에 진행 스냅샷을 쓰고(CLI/MCP stdio 프로세스), 서버가 읽어 활동 목록에 합친다. 취소는 <token>.cancel 파일."""

    def __init__(self, live_dir: str, stale_s: float = 90.0):
        self.dir = live_dir if os.path.isabs(live_dir) else os.path.normpath(os.path.join(ROOT, live_dir))
        self.stale_s = float(stale_s or 90)
        self.pid = os.getpid()

    def _path(self, token: str, ext: str = ".json") -> str:
        safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in token)[:80]
        return os.path.join(self.dir, safe + ext)

    def publish(self, token: str, snap: Optional[Dict[str, Any]], final: bool = False) -> None:
        if not snap:
            return
        try:
            os.makedirs(self.dir, exist_ok=True)
            p = self._path(token)
            if final and snap.get("status") != "running":
                # 끝난 작업은 파일을 지운다 (서버 메모리 이력에는 남지 않음 — CLI 는 requests 테이블로 추적)
                for ext in (".json", ".cancel"):
                    try:
                        os.remove(self._path(token, ext))
                    except OSError:
                        pass
                return
            slim = {k: v for k, v in snap.items() if k != "log"}
            slim["log"] = (snap.get("log") or [])[-5:]
            slim["heartbeat"] = time.time()
            slim["host"] = socket.gethostname()
            slim["pid"] = self.pid
            atomicio.write_json(p, slim, indent=None, default=str)
        except Exception:
            pass

    def cancel_check(self, token: str) -> Optional[Dict[str, Any]]:
        p = self._path(token, ".cancel")
        if os.path.exists(p):
            try:
                d = atomicio.read_json(p, {}) or {}
                return {"by": d.get("by", "?"), "reason": d.get("reason", ""), "ts": d.get("ts", time.time())}
            except Exception:
                return {"by": "?", "reason": "", "ts": time.time()}
        return None

    def request_cancel(self, token: str, by: str, reason: str = "") -> bool:
        if not os.path.exists(self._path(token)):
            return False
        try:
            with open(self._path(token, ".cancel"), "w", encoding="utf-8") as f:
                json.dump({"by": by, "reason": reason, "ts": time.time()}, f)
            return True
        except Exception:
            return False

    def list_external(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not os.path.isdir(self.dir):
            return out
        now = time.time()
        for fn in os.listdir(self.dir):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(self.dir, fn)
            d = atomicio.read_json(p)
            if not isinstance(d, dict):
                continue
            if int(d.get("pid") or 0) == self.pid:
                continue
            stale = (now - float(d.get("heartbeat") or 0)) > self.stale_s
            if stale:
                try:
                    os.remove(p)
                    os.remove(self._path(d.get("token") or fn[:-5], ".cancel"))
                except OSError:
                    pass
                continue
            d["external"] = True
            d["elapsed_s"] = round(now - float(d.get("started") or now), 1)
            out.append(d)
        return out


# ---------------------------------------------------------------- 요청 관리자
class RequestManager:
    READ_WEIGHTS = ("read",)
    WRITE_WEIGHTS = ("write", "exclusive", "soft")

    def __init__(self, cfg: Optional[Dict[str, Any]] = None, path: Optional[str] = None, install_publisher: bool = True,
                 data_dir: str = ""):
        # data_dir: 이 서버가 쓰는 데이터 폴더. 주면 원장이 **그 폴더**에 쓴다.
        # 주지 않으면 프로젝트 설정을 읽는데, 그러면 임시 Settings 로 띄운 서버(테스트·검증 하네스)도
        # 프로젝트의 data/ledger 에 기록해 실사용 기록과 섞인다 (2026-09-23 실측으로 확인).
        self.data_dir = data_dir or ""
        self.path = path or server_path()
        self.cfg: Dict[str, Any] = cfg if cfg is not None else load_config(self.path)
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self.rw = RWLock()
        self.active: Dict[str, Dict[str, Any]] = {}        # token → ticket (queued/running)
        self.history: Deque[Dict[str, Any]] = collections.deque(maxlen=int(self.cfg["monitor"]["history_size"]))
        self.clients: Dict[str, Dict[str, Any]] = {}       # "user:<id>" / "ip:<addr>" → 통계
        self._rate: Dict[str, Deque[float]] = collections.defaultdict(collections.deque)
        self._pending_cancel: Dict[str, Dict[str, Any]] = {}   # 아직 시작하지 않은 토큰(잡 스레드가 뜨기 전)에 대한 취소 예약
        self.counters: Dict[str, int] = collections.Counter()
        self.started = time.time()
        self._durations: Deque[Any] = collections.deque(maxlen=2000)   # (ts, kind, ms, ok)
        self.live = LiveRegistry(self.cfg["monitor"].get("live_dir") or "data/live", float(self.cfg["monitor"].get("live_stale_s") or 90))
        _led.configure(self.cfg.get("ledger"), self.data_dir or _data_dir())
        if install_publisher:
            _pg.set_publisher(self.live.publish, self.live.cancel_check)
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_stop = threading.Event()
        self._watchdog.start()

    # ---- 설정 ----
    def reload(self) -> Dict[str, Any]:
        with self._lock:
            self.cfg = load_config(self.path)
            self.history = collections.deque(self.history, maxlen=int(self.cfg["monitor"]["history_size"]))
            self.live = LiveRegistry(self.cfg["monitor"].get("live_dir") or "data/live", float(self.cfg["monitor"].get("live_stale_s") or 90))
            _pg.set_publisher(self.live.publish, self.live.cancel_check)
        _led.configure(self.cfg.get("ledger"), self.data_dir or _data_dir())
        return self.cfg

    def set_limits(self, updates: Dict[str, Any], save: bool = True) -> Dict[str, Any]:
        """{'concurrency.max_parallel_reads': 16, 'access.block_ips': [...]} 또는 {'concurrency': {...}} 형태."""
        with self._lock:
            for k, v in (updates or {}).items():
                if isinstance(v, dict) and k in self.cfg and isinstance(self.cfg[k], dict):
                    for kk, vv in v.items():
                        if kk in self.cfg[k]:
                            self.cfg[k][kk] = coerce_like(self.cfg[k][kk], vv)
                        else:
                            raise KeyError("unknown key %s.%s" % (k, kk))
                    continue
                if "." not in k:
                    raise KeyError("key must be <section>.<name>: %s" % k)
                sect, name = k.split(".", 1)
                if sect not in self.cfg or not isinstance(self.cfg[sect], dict):
                    raise KeyError("unknown key %s" % k)
                # 종류별 한도는 3단계다: concurrency.classes.<kind>.<key> · rate_limit.classes.<kind>.<key>.
                # 아직 없는 <kind> 는 **만들어 준다** — 종류가 늘 때마다 파일을 먼저 고치게 하지 않으려고.
                # 다만 <key> 는 아는 이름만 받는다 (오타를 조용히 삼키지 않는다).
                if name.startswith("classes."):
                    parts = name.split(".")
                    if len(parts) != 3:
                        raise KeyError("key must be %s.classes.<kind>.<name>: %s" % (sect, k))
                    _c, kind_, leaf = parts
                    known = ({"max_parallel", "max_parallel_per_user", "queue_max", "queue_timeout_s"}
                             if sect == "concurrency" else {"per_user_per_min"})
                    if leaf not in known:
                        raise KeyError("unknown key %s (가능: %s)" % (k, ", ".join(sorted(known))))
                    classes = self.cfg[sect].setdefault("classes", {})
                    cur = classes.setdefault(kind_, {})
                    cur[leaf] = coerce_like(cur.get(leaf, 0), v)
                    continue
                if name not in self.cfg[sect]:
                    raise KeyError("unknown key %s" % k)
                self.cfg[sect][name] = coerce_like(self.cfg[sect][name], v)
            self.history = collections.deque(self.history, maxlen=int(self.cfg["monitor"]["history_size"]))
            if save:
                save_config(self.cfg, self.path)
            self._cv.notify_all()
        return self.public_config()

    def public_config(self) -> Dict[str, Any]:
        return {k: copy.deepcopy(v) for k, v in self.cfg.items() if not k.startswith("_")}

    def block(self, kind: str, value: str, add: bool = True, save: bool = True) -> List[str]:
        key = {"ip": "block_ips", "user": "block_users", "allow_ip": "allow_ips"}[kind]
        with self._lock:
            lst = [x for x in (self.cfg["access"].get(key) or []) if x]
            if add and value and value not in lst:
                lst.append(value)
            if not add:
                lst = [x for x in lst if x != value]
            self.cfg["access"][key] = lst
            if save:
                save_config(self.cfg, self.path)
        return lst

    # ---- 접근 검사 ----
    @staticmethod
    def _ip_match(ip: str, patterns: List[str]) -> bool:
        for p in patterns or []:
            p = str(p).strip()
            if not p:
                continue
            if p == ip or (p.endswith((".", ":")) and ip.startswith(p)) or (p.endswith("*") and ip.startswith(p[:-1])):
                return True
        return False

    def check_access(self, ip: str, user: str, role: str) -> None:
        """차단/허용/점검 모드 — 모든 요청(GET 포함)에 적용. 거부면 Rejected."""
        a = self.cfg.get("access") or {}
        # 접근 제어 거절도 **무엇이 막았는지**를 함께 남긴다 (2026-09-23). 한도 거절과 같은 모양이라
        # 원장 상세의 '왜 거절됐나' 가 같은 자리에서 답한다 — 이게 없으면 문구 한 줄뿐이라
        # "어느 설정을 어떻게 되돌려야 하나" 를 알 수 없다.
        if a.get("allow_ips") and ip and not self._ip_match(ip, a["allow_ips"]):
            self.counters["rejected_blocked"] += 1
            raise Rejected(403, "허용되지 않은 IP 입니다 (server.json access.allow_ips)", code="ip_not_allowed",
                           limit={"what": "허용 IP 목록 밖", "who": ip, "key": "access.allow_ips",
                                  "value": ", ".join(a.get("allow_ips") or [])[:120],
                                  "hint": "이 목록이 비어 있지 않으면 **여기 적힌 IP 만** 접속할 수 있습니다. "
                                          "`server block add allow_ip <주소>` 로 추가합니다"})
        if ip and self._ip_match(ip, a.get("block_ips") or []):
            self.counters["rejected_blocked"] += 1
            raise Rejected(403, "차단된 IP 입니다", code="ip_blocked",
                           limit={"what": "IP 차단 목록", "who": ip, "key": "access.block_ips",
                                  "value": ", ".join(a.get("block_ips") or [])[:120],
                                  "hint": "`server block remove ip %s` 로 해제합니다" % ip})
        if user and user in (a.get("block_users") or []):
            self.counters["rejected_blocked"] += 1
            raise Rejected(403, "차단된 사용자입니다: %s" % user, code="user_blocked",
                           limit={"what": "사용자 차단 목록", "who": user, "key": "access.block_users",
                                  "value": ", ".join(a.get("block_users") or [])[:120],
                                  "hint": "`server block remove user %s` 로 해제합니다" % user})
        if a.get("maintenance_mode") and role not in (a.get("maintenance_allow_roles") or ["admin"]):
            self.counters["rejected_maintenance"] += 1
            raise Rejected(503, str(a.get("maintenance_message") or "maintenance"), retry_after=60, code="maintenance",
                           limit={"what": "점검 모드 — 허용 역할 외의 모든 요청을 막습니다",
                                  "who": "%s (역할 %s)" % (user or "게스트", role or "-"),
                                  "key": "access.maintenance_mode",
                                  "value": "켜짐 · 허용 역할: %s" % ", ".join(a.get("maintenance_allow_roles") or ["admin"]),
                                  "hint": "`server maintenance off` 또는 Web › 서버 모니터에서 끕니다. "
                                          "특정 역할을 통과시키려면 access.maintenance_allow_roles 에 추가합니다"})

    def _rate_check(self, key: str, limit: int, now: float) -> Optional[float]:
        """분당 limit 초과면 재시도까지 남은 초, 아니면 None."""
        if limit <= 0:
            return None
        dq = self._rate[key]
        while dq and now - dq[0] > 60.0:
            dq.popleft()
        if len(dq) >= limit:
            return max(0.1, 60.0 - (now - dq[0]))
        return None

    def _rate_hit(self, key: str, now: float) -> None:
        self._rate[key].append(now)

    # ---- 티켓 ----
    def _client_key(self, client: Dict[str, Any]) -> str:
        """제한·통계의 기준: 로그인 사용자는 사용자 단위, 게스트/익명은 IP 단위."""
        u = client.get("user") or ""
        if u and u != "guest" and client.get("via") != "anon":
            return "user:" + u
        return "ip:" + (client.get("ip") or "?")

    def _bump_client(self, client: Dict[str, Any], field: str, n: int = 1) -> None:
        for key in {self._client_key(client), "ip:" + (client.get("ip") or "?")}:
            c = self.clients.setdefault(key, {"key": key, "user": client.get("user"), "role": client.get("role"), "ip": client.get("ip"),
                                              "active": 0, "total": 0, "rejected": 0, "errors": 0, "last_seen": 0.0, "first_seen": time.time(),
                                              "origins": {}, "agent": ""})
            c[field] = c.get(field, 0) + n
            c["last_seen"] = time.time()
            if client.get("agent"):
                c["agent"] = str(client["agent"])[:120]
            if client.get("origin"):
                c["origins"][client["origin"]] = c["origins"].get(client["origin"], 0) + (1 if field == "total" else 0)

    @contextlib.contextmanager
    def ticket(self, kind: str, weight: str, client: Optional[Dict[str, Any]] = None, label: str = "", token: Optional[str] = None,
               timeout_s: Optional[float] = None, build_mode: str = ""):
        """요청 하나를 등록하고 실행 권한(락·슬롯)을 얻는다. weight: none(락 없음, 등록만) | read | soft(증분 빌드) | exclusive(전체 빌드·설정 저장).
        거부(Rejected) 는 호출자가 HTTP 상태로 변환. 안에서 progress 에 bind 되므로 취소·진행률이 함께 동작한다."""
        client = dict(client or {})
        tok = token or ("r-" + uuid.uuid4().hex[:10])
        now = time.time()
        conc, rl = self.cfg["concurrency"], self.cfg["rate_limit"]
        exempt = client.get("role") in (rl.get("exempt_roles") or [])
        client = {k: (safe_text(v, 200) if isinstance(v, str) else v) for k, v in client.items()}
        t = {"token": tok, "kind": kind, "weight": weight, "label": safe_text(label), "client": client, "submitted": now, "started": None,
             "finished": None, "status": "queued", "queue_wait_s": 0.0, "error": "", "pid": os.getpid()}
        # 원장: 이 티켓에 딸린 원장 항목. HTTP 진입점이 이미 연 span 이 있으면 그 토큰에 덧붙이고,
        # 없으면(CLI·MCP stdio·잡·스케줄러) 여기서 연다 — 어느 길로 들어오든 한 건은 남는다.
        led_tok = str(client.get("ledger_token") or "")
        led_own = False
        if not led_tok:
            led_tok = _led.open_(_led.new_token(), kind=kind, origin=client.get("origin") or "",
                                 user=client.get("user") or "", role=client.get("role") or "",
                                 via=client.get("via") or "", ip=client.get("ip") or "",
                                 label=safe_text(label), weight=weight, client_token=tok)
            led_own = True
        else:
            _led.update(led_tok, kind=kind, weight=weight, client_token=tok, label=safe_text(label))
        t["ledger_token"] = led_tok
        try:
          with self._lock:
            self.check_access(client.get("ip") or "", client.get("user") or "", client.get("role") or "")
            if weight != "none":
                if rl.get("enabled") and not exempt:
                    ck = self._client_key(client)
                    ra = self._rate_check(ck, int(rl.get("per_user_per_min") or 0), now)
                    if ra is None and client.get("ip"):
                        ra = self._rate_check("ip:" + client["ip"], int(rl.get("per_ip_per_min") or 0), now)
                    # 종류별 분당 한도 (rate_limit.classes.<kind>.per_user_per_min).
                    # kind=query 는 예전 키 query_per_user_per_min 을 기본값으로 이어받는다 (하위 호환).
                    per_min = self._class_limit("rate_limit", kind, "per_user_per_min",
                                                int(rl.get("query_per_user_per_min") or 0) if kind == "query" else 0)
                    if ra is None and int(per_min or 0) > 0:
                        ra = self._rate_check("%s:%s" % (ck, kind), int(per_min), now)
                    if ra is not None:
                        self.counters["rejected_rate"] += 1
                        self._bump_client(client, "rejected")
                        raise Rejected(429, "요청이 너무 잦습니다 (%s). %.0f초 후 다시 시도하세요" % (ck, ra), retry_after=ra, code="rate_limited",
                                       limit={"what": "분당 요청 수", "who": ck, "kind": kind,
                                              "key": "rate_limit.classes.%s.per_user_per_min / rate_limit.per_user_per_min" % kind,
                                              "value": int(per_min or rl.get("per_user_per_min") or 0)})
                # 대기열: 전체 상한과 **종류별 상한**을 둘 다 본다 (질의가 대기열을 다 먹어 검색이 못 들어오는 것을 막는다)
                queued = sum(1 for x in self.active.values() if x["status"] == "queued")
                if queued >= int(conc.get("queue_max") or 64):
                    self.counters["rejected_queue"] += 1
                    self._bump_client(client, "rejected")
                    raise Rejected(503, "서버가 바쁩니다: 대기열이 가득 찼습니다 (%d)" % queued, retry_after=10, code="queue_full",
                                   limit={"what": "전체 대기열 길이", "key": "concurrency.queue_max",
                                          "value": int(conc.get("queue_max") or 0), "current": queued})
                kq_max = int(self._class_limit("concurrency", kind, "queue_max", 0) or 0)
                if kq_max > 0:
                    kq = sum(1 for x in self.active.values() if x["status"] == "queued" and x["kind"] == kind)
                    if kq >= kq_max:
                        self.counters["rejected_queue"] += 1
                        self._bump_client(client, "rejected")
                        raise Rejected(503, "%s 대기열이 가득 찼습니다 (%d/%d)" % (kind, kq, kq_max), retry_after=10, code="queue_full_kind",
                                       limit={"what": "이 **종류**의 대기열 길이", "kind": kind,
                                              "key": "concurrency.classes.%s.queue_max" % kind,
                                              "value": kq_max, "current": kq})
                if not exempt:
                    ck = self._client_key(client)
                    mine = sum(1 for x in self.active.values() if self._client_key(x["client"]) == ck and x["weight"] != "none")
                    if mine >= int(conc.get("max_parallel_per_user") or 999):
                        self.counters["rejected_per_user"] += 1
                        self._bump_client(client, "rejected")
                        raise Rejected(429, "동시 요청이 너무 많습니다 (%s: %d개 실행/대기 중). 끝난 뒤 다시 시도하세요" % (ck, mine), retry_after=5, code="per_user_limit",
                                       limit={"what": "사용자(또는 IP)당 동시 요청 수 — 종류 무관", "who": ck,
                                              "key": "concurrency.max_parallel_per_user",
                                              "value": int(conc.get("max_parallel_per_user") or 0), "current": mine})
                    # 종류별 사용자당 동시 수 — 한 사람이 질의로 슬롯을 다 먹어도 그 사람의 검색은 따로 센다
                    kpu = int(self._class_limit("concurrency", kind, "max_parallel_per_user", 0) or 0)
                    if kpu > 0:
                        mine_k = sum(1 for x in self.active.values()
                                     if x["kind"] == kind and self._client_key(x["client"]) == ck and x["weight"] != "none")
                        if mine_k >= kpu:
                            self.counters["rejected_per_user"] += 1
                            self._bump_client(client, "rejected")
                            raise Rejected(429, "%s 동시 요청이 너무 많습니다 (%s: %d/%d)" % (kind, ck, mine_k, kpu),
                                           retry_after=5, code="per_user_limit_kind",
                                           limit={"what": "이 **종류**의 사용자당 동시 요청 수", "who": ck, "kind": kind,
                                                  "key": "concurrency.classes.%s.max_parallel_per_user" % kind,
                                                  "value": kpu, "current": mine_k,
                                                  "hint": "게스트는 IP 로 묶입니다. 늘리려면 `server limits set "
                                                          "concurrency.classes.%s.max_parallel_per_user=3`" % kind})
                    ip = client.get("ip") or ""
                    if ip:
                        mine_ip = sum(1 for x in self.active.values() if (x["client"].get("ip") or "") == ip and x["weight"] != "none")
                        if mine_ip >= int(conc.get("max_parallel_per_ip") or 999):
                            self.counters["rejected_per_ip"] += 1
                            self._bump_client(client, "rejected")
                            raise Rejected(429, "이 IP 의 동시 요청이 너무 많습니다 (%d개)" % mine_ip, retry_after=5, code="per_ip_limit")
                if rl.get("enabled") and not exempt:
                    self._rate_hit(self._client_key(client), now)
                    if client.get("ip"):
                        self._rate_hit("ip:" + client["ip"], now)
                    self._rate_hit("%s:%s" % (self._client_key(client), kind), now)
            self.active[tok] = t
            self._bump_client(client, "active")
            self._bump_client(client, "total")
            self.counters["submitted"] += 1
        except Rejected as e:
            # 거절은 예전에 **메모리 카운터만** 올리고 어디에도 남지 않았다 (요청 4 원인 C).
            # 이제 원장에 사유·코드·HTTP 와 함께 남는다. span 이 이미 열려 있으면 그쪽이 닫는다.
            # `limit` 에 **무엇이 막았고 그때 서버가 얼마나 차 있었는지** 를 함께 남긴다.
            # 거절은 로그 파일에 남지 않으므로 이게 없으면 나중에 원인을 알 방법이 아예 없다.
            lim = json.dumps(e.limit, ensure_ascii=False) if e.limit else None
            if led_own:
                _led.close(led_tok, "rejected", http=e.status, code=e.code, error=e.message,
                           ms=round((time.time() - now) * 1000, 1), kind=kind, limit_json=lim,
                           user=client.get("user") or "", origin=client.get("origin") or "")
            else:
                _led.update(led_tok, status="rejected", code=e.code, error=e.message, http=e.status, limit_json=lim)
            raise
        _pg.bind(tok, kind, label or kind, client=client)
        _led.update(led_tok, status="queued")
        with self._lock:
            pend = self._pending_cancel.pop(tok, None)
        if pend:
            _pg.cancel(tok, pend.get("by", "?"), pend.get("reason", ""))   # 시작 전에 들어온 취소 요청을 즉시 반영
        held_read = held_write = False
        limit_s = timeout_s if timeout_s is not None else float((self.cfg.get("timeouts") or {}).get("%s_s" % kind, 0) or 0)
        t["limit_s"] = limit_s
        status = "error"
        try:
            if weight == "read":
                self._wait_read_slot(t)
                held_read = True
            elif weight in ("soft", "exclusive", "write"):
                # soft = 증분 빌드처럼 "다른 쓰기만 막으면 되는" 작업. 정책 reads_during_build 가 never 면 그것도 배타로 올린다.
                pol = str(conc.get("reads_during_build") or "incremental")
                mode = "exclusive"
                if weight == "soft" and pol in ("incremental", "always"):
                    mode = "soft"
                elif pol == "always" and build_mode and weight in ("exclusive", "write"):
                    # `always` = "빌드 중에도 질의를 받는다". 예전 조건은 `weight == "write"` 만 봤는데,
                    # 전체/채널 리빌드는 weight 가 **"exclusive"** 로 들어오므로 이 분기가 한 번도 타지 않았다.
                    # 즉 문서가 권하던 `reads_during_build=always` 가 **정확히 그 상황(전체 리빌드)에서 무효**였다.
                    # 30명 부하 실측에서 always 를 켜도 질의가 빌드 시간만큼(5.5초) 그대로 기다렸다 (2026-09-19).
                    # `build_mode` 는 빌드에서만 설정되므로(설정 저장·스냅샷 복원은 빈 문자열) 완화 범위는 빌드로 한정된다.
                    mode = "soft"

                def on_wait(what: str) -> None:
                    _pg.note("다른 작업이 끝나기를 기다리는 중… (%s)" % what)
                ok = self.rw.acquire_write(mode, timeout=float(conc.get("write_wait_timeout_s") or DEFAULTS["concurrency"]["write_wait_timeout_s"]),
                                           on_wait=on_wait, label=label or kind)
                if not ok:
                    raise Rejected(503, "다른 작업(빌드/질의)이 끝나지 않아 %s 를 시작하지 못했습니다" % (label or kind), retry_after=30, code="write_wait_timeout")
                held_write = True
                t["lock_mode"] = mode
            with self._lock:
                t["status"] = "running"
                t["started"] = time.time()
                t["queue_wait_s"] = round(t["started"] - t["submitted"], 3)
            _pg.set_queue(tok, None)
            _led.update(led_tok, status="running", queue_wait_s=t["queue_wait_s"], lock_mode=t.get("lock_mode"),
                        limit_s=limit_s)
            yield t
            status = "done"
        except _pg.Cancelled as e:
            # watchdog 이 시간 제한으로 끊은 것과 사람이 멈춘 것을 원장에서 구분한다
            status = "timeout" if "watchdog" in str(e) or "시간 제한" in str(e) else "cancelled"
            t["error"] = str(e)[:200]
            raise
        except Rejected as e:
            status = "rejected"
            t["error"] = e.message
            raise
        except BaseException as e:
            status = "error"
            t["error"] = ("%s: %s" % (type(e).__name__, e))[:300]
            raise
        finally:
            if held_read:
                self._release_read_slot()
            if held_write:
                self.rw.release_write()
            end = time.time()
            with self._lock:
                self.active.pop(tok, None)
                t["status"] = status
                t["finished"] = end
                t["ms"] = round((end - (t["started"] or t["submitted"])) * 1000, 1)
                self.history.appendleft({k: v for k, v in t.items()})
                self._bump_client(client, "active", -1)
                if status == "error":
                    self._bump_client(client, "errors")
                self.counters["done_" + status] += 1
                if t["started"]:
                    self._durations.append((end, kind, t["ms"], status == "done"))
                    if t["ms"] >= float(self.cfg["monitor"].get("slow_request_ms") or 30000):
                        self.counters["slow"] += 1
            _pg.unbind(status, t.get("error") or "")
            # 원장 종료. span 이 있으면(HTTP) 거기서 닫으므로 여기서는 값만 덧붙인다 — 한 요청이 두 줄로 닫히지 않게.
            # run_id 를 함께 남긴다 (2026-09-23): 이게 없으면 원장에서 **로그로 가는 길이 끊긴다**
            # (📜 링크도, 요청 상세의 로그 절도 run_id 로 거른다). 실측에서 한 건도 기록되지 않고 있었다.
            fin = {"ms": t["ms"], "queue_wait_s": t.get("queue_wait_s"), "request_id": t.get("request_id"),
                   "run_id": t.get("run_id"), "kind": kind,
                   "note": t.get("note"), "error": t.get("error") or "", "stage": (_pg.get(tok) or {}).get("stage_label")}
            if led_own:
                _led.close(led_tok, status, **fin)
            else:
                _led.update(led_tok, status=status, **fin)

    # ---- 종류(kind)별 한도 ----
    def _class_limit(self, section: str, kind: str, key: str, default: Any = 0) -> Any:
        """`<section>.classes.<kind>.<key>` — 없으면 default (보통은 공용 값).

        질의와 채널 검색에 **별도 한도**를 두기 위한 것이다(2026-09-23 요청 2).
        `concurrency.classes.query.max_parallel` 을 전체 슬롯보다 작게 잡으면 그 차이가
        빠른 요청(검색)의 **예약 슬롯**이 된다 — 별도 예약 설정을 만들지 않고 상한 하나로 같은 효과를 낸다.
        """
        try:
            cls = ((self.cfg.get(section) or {}).get("classes") or {}).get(kind)
        except Exception:
            return default
        # 설정 파일의 설명용 `_comment` 는 문자열이라 종류 설정이 아니다 (파일에 주석을 적을 수 있게 허용한다)
        if not isinstance(cls, dict):
            return default
        v = cls.get(key)
        return default if v is None or v == "" else v

    def class_view(self) -> List[Dict[str, Any]]:
        """화면·CLI 가 보여 줄 종류별 한도 표 (유효값 + 어디서 왔는지)."""
        conc, rl = self.cfg["concurrency"], self.cfg["rate_limit"]
        total = int(conc.get("max_parallel_reads") or 8)
        # `_comment` 로 시작하는 키는 파일에 적은 설명이지 종류 이름이 아니다
        named = [k for src in (conc.get("classes") or {}, rl.get("classes") or {}) for k in src if not str(k).startswith("_")]
        kinds = sorted(set(named + ["query", "search", "mcp", "cli"]))
        out = []
        for k in kinds:
            mp = int(self._class_limit("concurrency", k, "max_parallel", 0) or 0)
            out.append({"kind": k,
                        "max_parallel": mp or total, "max_parallel_set": bool(mp),
                        "max_parallel_per_user": int(self._class_limit("concurrency", k, "max_parallel_per_user", 0) or 0)
                        or int(conc.get("max_parallel_per_user") or 0),
                        "queue_max": int(self._class_limit("concurrency", k, "queue_max", 0) or 0) or int(conc.get("queue_max") or 0),
                        "queue_timeout_s": float(self._class_limit("concurrency", k, "queue_timeout_s", 0) or 0)
                        or float(conc.get("queue_timeout_s") or 0),
                        "per_user_per_min": int(self._class_limit("rate_limit", k, "per_user_per_min",
                                                                  int(rl.get("query_per_user_per_min") or 0) if k == "query" else 0) or 0),
                        "timeout_s": float((self.cfg.get("timeouts") or {}).get("%s_s" % k, 0) or 0),
                        "reserved_for_others": max(0, total - mp) if mp else 0})
        return out

    def _wait_read_slot(self, t: Dict[str, Any]) -> None:
        conc = self.cfg["concurrency"]
        kind = t["kind"]
        # 대기 시간도 종류별로 — 검색은 15초 안에 못 들어가면 기다릴 이유가 없다(거절이 낫다)
        deadline = time.time() + float(self._class_limit("concurrency", kind, "queue_timeout_s",
                                                         conc.get("queue_timeout_s") or 120) or 120)
        tok = t["token"]
        # 1) 읽기 락 (배타 작업 중이면 대기)
        def on_wait(what: str) -> None:
            _pg.note("다른 작업(%s)이 끝나기를 기다리는 중…" % what)
        ok = self.rw.acquire_read(timeout=float(conc.get("read_wait_timeout_s") or 900), on_wait=on_wait)
        if not ok:
            raise Rejected(503, "배타 작업(전체 빌드 등)이 끝나지 않아 요청을 처리하지 못했습니다", retry_after=30, code="read_wait_timeout")
        # 2) 동시 실행 슬롯
        batch_max = int(conc.get("max_parallel_batch") or 0)
        is_batch = t["kind"] in BATCH_KINDS
        total_max = int(conc.get("max_parallel_reads") or 8)
        # 종류별 상한. 전체보다 작게 잡으면 그 차이가 **다른 종류의 예약 슬롯**이 된다
        # (질의 6 < 전체 8 → 슬롯 2개는 언제나 비어 있어 빠른 채널 검색이 즉시 실행된다).
        kind_max = int(self._class_limit("concurrency", kind, "max_parallel", 0) or 0)
        if kind_max > total_max:
            kind_max = total_max        # 전체보다 크게 잡아도 의미가 없다 — 조용히 절단하고 화면이 알린다
        with self._cv:
            while True:
                running = sum(1 for x in self.active.values() if x["status"] == "running" and x["weight"] == "read")
                # 배치 작업(평가·trial·사전계산)은 따로 센다 — 여러 개가 동시에 돌면 대화형 질의가 다 밀린다
                batch_running = (sum(1 for x in self.active.values()
                                     if x["status"] == "running" and x["kind"] in BATCH_KINDS) if is_batch else 0)
                kind_running = (sum(1 for x in self.active.values()
                                    if x["status"] == "running" and x["weight"] == "read" and x["kind"] == kind) if kind_max else 0)
                if (running < total_max
                        and not (is_batch and batch_max and batch_running >= batch_max)
                        and not (kind_max and kind_running >= kind_max)):
                    return
                ahead = sum(1 for x in self.active.values() if x["status"] == "queued" and x["weight"] == "read" and x["submitted"] < t["submitted"])
                _pg.set_queue(tok, ahead + 1, sum(1 for x in self.active.values() if x["status"] == "queued"))
                left = deadline - time.time()
                if left <= 0:
                    self.rw.release_read()
                    self.counters["rejected_queue_timeout"] += 1
                    waited = float(self._class_limit("concurrency", kind, "queue_timeout_s", conc.get("queue_timeout_s") or 120) or 120)
                    raise Rejected(503, "대기 시간 초과 (%.0f초): 서버가 바쁩니다 (%s)" % (waited, kind),
                                   retry_after=15, code="queue_timeout",
                                   limit={"what": "대기열에서 기다릴 수 있는 시간", "kind": kind,
                                          "key": "concurrency.classes.%s.queue_timeout_s / concurrency.queue_timeout_s" % kind,
                                          "value": waited, "current": running, "slots": total_max,
                                          "hint": "슬롯이 %d개인데 %d개가 실행 중이었습니다. 슬롯을 늘리기 전에 "
                                                  "LLM 엔드포인트가 그만큼 받아 주는지 먼저 확인하세요 (docs/REQUEST_LEDGER.md §2)"
                                                  % (total_max, running)})
                self._cv.wait(min(1.0, left))
                try:
                    _pg.check_cancel()
                except _pg.Cancelled:
                    self.rw.release_read()
                    raise

    def _release_read_slot(self) -> None:
        self.rw.release_read()
        with self._cv:
            self._cv.notify_all()

    # ---- 취소 ----
    def cancel(self, token: str, by: str = "admin", reason: str = "", allow_owner: Optional[str] = None,
               pending_ok: bool = False) -> Dict[str, Any]:
        """실행 중이면 즉시 취소를 요청하고, 아직 시작 전(잡 스레드가 뜨기 전)이면 예약해 두었다가 시작하자마자 취소한다."""
        with self._lock:
            t = self.active.get(token)
        if t:
            owner = (t["client"] or {}).get("user")
            if allow_owner is not None and owner != allow_owner:
                return {"ok": False, "error": "본인 요청만 취소할 수 있습니다"}
            ok = _pg.cancel(token, by, reason)
            with self._cv:
                self._cv.notify_all()
            self.counters["cancel_requests"] += 1
            return {"ok": ok, "token": token, "status": t["status"]}
        # 서버 밖(다른 프로세스) 작업?
        if allow_owner is None and self.live.request_cancel(token, by, reason):
            self.counters["cancel_requests"] += 1
            return {"ok": True, "token": token, "external": True}
        # 아직 티켓이 생기지 않은 토큰(막 시작한 잡): 예약해 두면 ticket() 이 시작하자마자 취소한다
        if pending_ok:
            with self._lock:
                now = time.time()
                for k in [k for k, v in self._pending_cancel.items() if now - v["ts"] > 300]:
                    self._pending_cancel.pop(k, None)
                self._pending_cancel[token] = {"by": by, "reason": reason, "ts": now}
            self.counters["cancel_requests"] += 1
            return {"ok": True, "token": token, "pending": True}
        # progress 에만 있는 항목 (ticket 없이 bind 된 경우)
        if _pg.cancel(token, by, reason):
            return {"ok": True, "token": token}
        return {"ok": False, "error": "실행 중인 요청이 아닙니다: %s" % token}

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop.wait(1.0):
            try:
                now = time.time()
                with self._lock:
                    items = [(t["token"], t) for t in self.active.values() if t["status"] == "running" and (t.get("limit_s") or 0) > 0]
                for tok, t in items:
                    if now - (t["started"] or now) > float(t["limit_s"]):
                        if _pg.cancel(tok, "watchdog", "시간 제한 %.0fs 초과 (server.json timeouts)" % float(t["limit_s"])):
                            self.counters["timeouts"] += 1
            except Exception:
                pass

    def stop(self) -> None:
        self._watchdog_stop.set()

    # ---- 관측 ----
    def _snap_ticket(self, t: Dict[str, Any], viewer: bool = False) -> Dict[str, Any]:
        live = _pg.get(t["token"]) or {}
        cl = t.get("client") or {}
        show_user = (not viewer) or bool(self.cfg["monitor"].get("show_user_to_viewer", True))
        now = time.time()
        return {"token": t["token"], "kind": t["kind"], "weight": t["weight"], "label": t["label"], "status": t["status"],
                "user": (cl.get("user") if show_user else None), "role": cl.get("role"), "origin": cl.get("origin"),
                "ip": (cl.get("ip") if not viewer else None), "agent": (cl.get("agent") if not viewer else None),
                "submitted": t["submitted"], "started": t["started"], "queue_wait_s": t.get("queue_wait_s"),
                # 끝난 요청은 소수 3자리까지 — 답변 캐시 적중은 1ms 안팎이라 0.1초로 반올림하면
                # '0.0s' 가 되어 "실행이 안 된 것 아니냐"는 오해를 준다.
                "elapsed_s": round((now - (t["started"] or t["submitted"])), 1) if t["status"] in ("running", "queued") else round((t.get("ms") or 0) / 1000, 3),
                "stage": live.get("stage_label"), "path": live.get("path_labels"), "pct": live.get("pct"), "eta_s": live.get("eta_s"),
                "llm": {k: live.get("llm", {}).get(k) for k in ("active", "provider", "model", "elapsed_s", "calls")} if live.get("llm") else None,
                "queue": live.get("queue"), "cancel_requested": bool(live.get("cancel")), "lock_mode": t.get("lock_mode"), "limit_s": t.get("limit_s"),
                "error": t.get("error") if not viewer else None, "detail": live.get("detail"),
                # note: 핸들러가 남기는 한 줄 설명(예: '캐시'). 1ms 만에 끝난 요청이 왜 그런지 목록에서 바로 보이게.
                "note": t.get("note"),
                # request_id: 끝난 작업을 눌렀을 때 **저장해 둔 그때 그 결과**(요청 프로파일)로 바로 갈 수 있게.
                # 핸들러가 결과를 만든 뒤 티켓에 적어 준다 (없으면 진행 기록만 보여 준다).
                "request_id": t.get("request_id")}

    def activity(self, viewer: bool = False, history: int = 20) -> Dict[str, Any]:
        with self._lock:
            act = [self._snap_ticket(t, viewer) for t in sorted(self.active.values(), key=lambda x: x["submitted"])]
            hist = [self._snap_ticket(t, viewer) for t in list(self.history)[:history]]
        ext = []
        for d in self.live.list_external():
            cl = d.get("client") or {}
            ext.append({"token": d.get("token"), "kind": d.get("kind"), "weight": "external", "label": d.get("label"), "status": d.get("status"),
                        "user": cl.get("user") if (not viewer or self.cfg["monitor"].get("show_user_to_viewer", True)) else None, "role": cl.get("role"),
                        "origin": cl.get("origin") or "cli", "ip": None, "submitted": d.get("started"), "started": d.get("started"),
                        "elapsed_s": d.get("elapsed_s"), "stage": d.get("stage_label"), "path": d.get("path_labels"), "pct": d.get("pct"), "eta_s": d.get("eta_s"),
                        "llm": d.get("llm"), "cancel_requested": bool(d.get("cancel")), "external": True, "pid": d.get("pid"), "host": d.get("host")})
        # ticket 없이 progress 에만 bind 된 항목(잡 러너)도 포함
        seen = {a["token"] for a in act}
        for snap in _pg.all_running():
            if snap["token"] in seen:
                continue
            cl = snap.get("client") or {}
            act.append({"token": snap["token"], "kind": snap.get("kind"), "weight": "job", "label": snap.get("label"), "status": "running",
                        "user": cl.get("user") if (not viewer or self.cfg["monitor"].get("show_user_to_viewer", True)) else None, "role": cl.get("role"),
                        "origin": cl.get("origin"), "ip": cl.get("ip") if not viewer else None, "submitted": snap.get("started"), "started": snap.get("started"),
                        "elapsed_s": snap.get("elapsed_s"), "stage": snap.get("stage_label"), "path": snap.get("path_labels"), "pct": snap.get("pct"),
                        "eta_s": snap.get("eta_s"), "llm": snap.get("llm"), "cancel_requested": bool(snap.get("cancel")), "queue": snap.get("queue")})
        return {"running": [a for a in act if a["status"] == "running"], "queued": [a for a in act if a["status"] == "queued"],
                "external": ext, "recent": hist, "lock": self.rw.state(), "ts": time.time(),
                "limits": {"max_parallel_reads": self.cfg["concurrency"]["max_parallel_reads"], "queue_max": self.cfg["concurrency"]["queue_max"]}}

    def stats(self) -> Dict[str, Any]:
        now = time.time()
        win = float(self.cfg["monitor"].get("stats_window_min") or 15) * 60
        with self._lock:
            recent = [d for d in self._durations if now - d[0] <= win]
            by_kind: Dict[str, List[float]] = collections.defaultdict(list)
            errs = 0
            for _, k, ms, ok in recent:
                by_kind[k].append(ms)
                if not ok:
                    errs += 1
            lat = {}
            for k, v in by_kind.items():
                v = sorted(v)
                lat[k] = {"n": len(v), "avg_ms": round(sum(v) / len(v), 1), "p50_ms": v[len(v) // 2], "p95_ms": v[min(len(v) - 1, int(len(v) * 0.95))], "max_ms": v[-1]}
            running = sum(1 for t in self.active.values() if t["status"] == "running")
            queued = sum(1 for t in self.active.values() if t["status"] == "queued")
            clients = sorted(self.clients.values(), key=lambda c: c["last_seen"], reverse=True)[:200]
            counters = dict(self.counters)
        try:
            from . import providers as _prov
            circuits = _prov.circuit_all()
        except Exception:
            circuits = {}
        try:
            ledger = _led.stats(days=0.25)
        except Exception:
            ledger = {}
        return {"uptime_s": round(now - self.started, 0), "running": running, "queued": queued, "window_min": win / 60,
                "throughput_per_min": round(len(recent) / max(1.0, win / 60), 2), "errors_in_window": errs, "latency": lat,
                "counters": counters, "clients": clients, "lock": self.rw.state(), "circuits": circuits,
                "limits": self.public_config(), "classes": self.class_view(), "ledger": ledger,
                "pid": os.getpid(), "host": socket.gethostname()}

    def kick_user(self, user: str) -> int:
        """사용자의 실행 중/대기 중 요청을 모두 취소."""
        n = 0
        with self._lock:
            toks = [t["token"] for t in self.active.values() if (t["client"] or {}).get("user") == user]
        for tok in toks:
            if self.cancel(tok, "admin", "kicked").get("ok"):
                n += 1
        return n


# 전역 (서버 프로세스당 하나). CLI 단독 실행에서는 만들지 않는다 — LiveRegistry 만 progress 에 등록해 서버가 볼 수 있게 한다.
_MANAGER: Optional[RequestManager] = None


def get_manager(create: bool = True, data_dir: str = "") -> Optional[RequestManager]:
    """서버 프로세스당 하나. `data_dir` 은 **처음 만들 때만** 쓰인다 (원장을 그 폴더에 쓰게 한다)."""
    global _MANAGER
    if _MANAGER is None and create:
        _MANAGER = RequestManager(data_dir=data_dir)
    return _MANAGER


def install_cli_publisher(data_dir: str = "") -> None:
    """CLI/MCP stdio 프로세스: 서버 없이도 진행 상황을 data/live 에 발행하고 서버의 취소 요청을 받는다.

    2026-09-23: 원장도 함께 켠다. `data/live` 는 **살아 있는 동안만** 보이는 휘발성 파일이라
    CLI 빌드·질의가 끝나면 흔적이 사라졌다 — '진행 중 작업' 에는 보이지만 어디에도 남지 않는 것이다.
    원장은 파일 append 라 다른 프로세스에서 써도 안전하므로, 여기서 같은 폴더에 함께 남긴다.

    2026-09-24: `data_dir` 을 **받는다**. 받지 않으면 프로젝트 설정의 data_dir 로 떨어지는데,
    임시 Settings 로 도는 단위 테스트가 CLI 경로를 지날 때 **실제 data/ledger 에 기록**했다
    (실측: 한 번 돌릴 때마다 질의 픽스처 수십 건). 이 실행이 실제로 쓰는 data_dir 에 남기는 것이 맞다.
    """
    cfg = load_config()
    live_dir = cfg["monitor"].get("live_dir") or "data/live"
    if data_dir and not os.path.isabs(live_dir):
        # 'data/…' 는 그 실행의 data_dir 아래로 (reqledger.configure 와 같은 규칙)
        live_dir = os.path.join(data_dir, live_dir[len("data/"):] if live_dir.startswith("data/") else live_dir)
    live = LiveRegistry(live_dir, float(cfg["monitor"].get("live_stale_s") or 90))
    _pg.set_publisher(live.publish, live.cancel_check)
    _led.configure(cfg.get("ledger"), data_dir or _data_dir())


def weight_for_level(level: str, op: str = "", body: Optional[Dict[str, Any]] = None) -> str:
    """권한 등급(auth.classify_*) → 락 가중치. read/run → read · index(증분 빌드 등) → soft(정책에 따라) · 나머지 쓰기 → exclusive."""
    # 협업(채팅·게시)은 색인이나 파이프라인을 건드리지 않는다. 접속자마다 몇 초에 한 번씩 폴링하므로
    # 읽기 슬롯을 잡게 두면 **30명 환경에서 질의가 밀린다** — 부수 기능이 본체를 막는 셈이라 슬롯 밖에 둔다.
    # (권한 등급은 그대로 read/admin 이다 — 여기서는 락 가중치만 정한다.)
    if str(op or "").startswith("collab"):
        return "none"
    if level in ("read", "run"):
        return "read"
    if level == "index":
        return "soft"
    return "exclusive"
