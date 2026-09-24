"""30명이 **Web·CLI·MCP 로 동시에** 쓸 때 서버가 버티는가 (2026-09-24).

왜 이 하네스가 따로 필요한가
  - `verify_tri_surface.py` 는 세 창구가 **같은 답**을 주는지 본다 — 순차 실행이라 부하가 아니다.
  - `verify_request_ledger.py` · `verify_build_load.py` 는 부하를 주지만 **HTTP 질의 경로 하나**만 민다.
  - 실제 사내 환경에서는 브라우저·터미널·외부 LLM 이 **같은 서버 프로세스 · 같은 요청 관리자 ·
    같은 SQLite 파일**을 동시에 쓴다. 경합·대기열 거절·종류별 한도는 그때서야 진짜 모습을 보인다.
    특히 CLI 는 **별도 프로세스**라 같은 DB 파일에 다른 연결로 붙는다 — 쓰기 잠금 문제가 드러나는 자리다.

무엇을 재는가
  창구별로 보낸 수 · 성공 · 거절(429/503) · 서버 오류(5xx) · p50/p95, 그리고
  **느린 질의가 도는 동안 빠른 검색이 갇히지 않는가**(종류별 한도의 목적).

합격 기준
  1. 5xx(서버 결함) 0건 — 거절(429/503)은 한도가 일한 것이므로 정상이다.
  2. 보낸 모든 요청이 요청 원장에 남는다 (창구별로 origin=web·cli·mcp 가 다 보여야 한다).
  3. 로그에 `database is locked` 0건.
  4. 검색 p95 가 질의 p95 보다 확실히 작다 (예약 슬롯이 실제로 일했다).

격리
  임시 폴더 + mock LLM + 자체 포트. 실제 색인·설정·원장·로그를 건드리지 않는다.

실행:
    python tools/verify/verify_three_surface_load.py                  # 30명
    python tools/verify/verify_three_surface_load.py --users 50       # 더 세게
    python tools/verify/verify_three_surface_load.py --rounds 5       # 1인당 반복 수
    python tools/verify/verify_three_surface_load.py --keep           # 임시 폴더 보존
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys

# 콘솔이 cp949 여도 한글·기호 출력에서 죽지 않게 (다른 verify_* 와 같은 처리, 2026-09-24)
try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
except Exception:
    pass
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from verify_request_ledger import Env  # noqa: E402  격리 환경을 그대로 쓴다 (임시 폴더·mock LLM·자체 포트)

QUESTIONS = [
    "RX DMA underrun %d 의 원인과 CL 은?",
    "underrun %d 이 PHY 재시작에 미치는 영향은?",
    "클럭 게이팅 타이밍 %d 관련 이슈를 정리해줘",
    "t_setup %d ns 가 문제가 되는 조건은?",
]


# ---------------------------------------------------------------- 클라이언트
class Client:
    """헤더 SSO 로 **사람마다 다른 사용자**가 되는 HTTP 클라이언트.

    인증이 꺼져 있으면 모든 요청이 admin 이 되고, admin 은 `rate_limit.exempt_roles` 때문에
    사용자당 한도를 **건너뛴다**. 그러면 30명을 흉내 내도 실은 한 사람이라 한도가 전혀 검사되지 않는다.
    그래서 리버스 프록시 SSO(type=header)를 켜고 요청마다 사용자 헤더를 붙인다 —
    사내 배포에서 실제로 쓰는 방식과 같다.
    """

    def __init__(self, base: str, user: str):
        self.base, self.user = base.rstrip("/"), user

    def call(self, method: str, path: str, body=None, timeout: float = 120, extra=None):
        h = {"Content-Type": "application/json", "X-Requested-With": "llmwiki",
             "X-Forwarded-User": self.user}
        h.update(extra or {})
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}"), time.perf_counter() - t0
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8") or "{}"), time.perf_counter() - t0
            except Exception:
                return e.code, {}, time.perf_counter() - t0
        except Exception as e:
            return 0, {"error": str(e)[:120]}, time.perf_counter() - t0


class McpClient(Client):
    """같은 서버의 `POST /mcp` (Streamable HTTP) — 외부 LLM 이 실제로 쓰는 경로."""

    def __init__(self, base: str, user: str):
        Client.__init__(self, base, user)
        self.url = "/mcp"
        self.session = None
        self._id = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "verify-load", "version": "1"}})

    def rpc(self, method: str, params=None, timeout: float = 300):
        self._id += 1
        extra = {"Accept": "application/json, text/event-stream"}
        if self.session:
            extra["Mcp-Session-Id"] = self.session
        h = {"Content-Type": "application/json", "X-Forwarded-User": self.user}
        h.update(extra)
        body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        req = urllib.request.Request(self.base + self.url, method="POST", headers=h,
                                     data=json.dumps(body, ensure_ascii=False).encode("utf-8"))
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw, hdrs, status = r.read(), dict(r.headers.items()), r.status
            if hdrs.get("Mcp-Session-Id"):
                self.session = hdrs["Mcp-Session-Id"]
            return status, json.loads(raw.decode("utf-8")), time.perf_counter() - t0
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8") or "{}"), time.perf_counter() - t0
            except Exception:
                return e.code, {}, time.perf_counter() - t0
        except Exception as e:
            return 0, {"error": str(e)[:120]}, time.perf_counter() - t0

    def tool(self, name: str, args: dict, timeout: float = 300):
        code, j, dt = self.rpc("tools/call", {"name": name, "arguments": args}, timeout=timeout)
        res = (j or {}).get("result") or {}
        # MCP 는 도구 오류를 JSON-RPC 200 + isError 로 돌려준다. 부하 시험에서는 그것도 실패로 센다.
        if res.get("isError"):
            return code, {"_error": "\n".join(c.get("text", "") for c in res.get("content") or [])[:160]}, dt
        return code, res, dt


# ---------------------------------------------------------------- 집계
class Stat:
    def __init__(self, name: str):
        self.name = name
        self.lock = threading.Lock()
        self.ok, self.rejected, self.server_error, self.failed = 0, 0, 0, 0
        self.ms: list = []
        self.codes: dict = {}
        self.errors: list = []          # 실패 사유 원문 — 숫자만 보면 무엇이 틀렸는지 알 수 없다

    def add(self, http: int, dt: float, note: str = ""):
        with self.lock:
            self.ms.append(dt * 1000.0)
            self.codes[http] = self.codes.get(http, 0) + 1
            if note and (http >= 500 or http == 0) and len(self.errors) < 5:
                self.errors.append(note[:200])
            if http == 200:
                self.ok += 1
            elif http in (429, 503):
                self.rejected += 1
            elif http >= 500:
                self.server_error += 1
            else:
                self.failed += 1

    def line(self) -> str:
        n = len(self.ms) or 1
        s = sorted(self.ms)
        p50 = s[int(n * 0.5) - 1] if s else 0.0
        p95 = s[min(n - 1, int(n * 0.95))] if s else 0.0
        return "  %-14s 보냄 %-4d 성공 %-4d 거절 %-4d 5xx %-3d 실패 %-3d  p50 %7.0fms  p95 %7.0fms  %s" % (
            self.name, len(self.ms), self.ok, self.rejected, self.server_error, self.failed,
            p50, p95, " ".join("%s:%d" % (k, v) for k, v in sorted(self.codes.items())))

    def p95(self) -> float:
        s = sorted(self.ms)
        return s[min(len(s) - 1, int(len(s) * 0.95))] if s else 0.0


# ---------------------------------------------------------------- 사람들
def web_user(env: Env, idx: int, rounds: int, st_q: Stat, st_s: Stat, st_poll: Stat,
             st_acc: Stat, stop: threading.Event):
    """브라우저 한 명 — 질의(비동기 잡)를 던지고, 그동안 화면 갱신 폴링을 계속한다.

    폴링을 함께 보내는 것이 핵심이다. 예전에는 동기 질의가 브라우저 연결 6개를 물어
    **폴링이 출발조차 못 해** 화면이 멈춘 것처럼 보였다. 그 회귀를 여기서 잡는다.

    시간은 **두 가지**를 따로 잰다. 비동기 잡이라 POST 는 즉시 돌아오므로,
    그것만 재면 "질의가 5ms" 라는 무의미한 숫자가 나온다.
      접수(accept) = POST 응답까지  — 화면이 멈추지 않는가의 지표
      질의(query)  = 답이 나올 때까지 — 실제 처리 시간, 검색과 비교할 대상
    """
    c = Client(env.base, "user%02d" % idx)
    for r in range(rounds):
        if stop.is_set():
            return
        q = QUESTIONS[(idx + r) % len(QUESTIONS)] % (8000 + (idx + r) % 6)
        t0 = time.perf_counter()
        code, j, dt = c.call("POST", "/api/query", {"q": q, "async": True, "log": True}, timeout=180)
        st_acc.add(code, dt, str((j or {}).get("error", ""))[:160])
        job = (j or {}).get("job")
        if not job:                       # 거절(429/503)이거나 동기 응답 — 접수 시점이 곧 끝이다
            st_q.add(code, dt, str((j or {}).get("error", ""))[:160])
            continue
        # 잡이 도는 동안 사람처럼 화면을 본다 (1.5초 간격 폴링)
        deadline = time.time() + 300
        last = 0
        while time.time() < deadline and not stop.is_set():
            pc, pj, pdt = c.call("GET", "/api/jobs/" + job, timeout=30)
            st_poll.add(pc, pdt)
            last = pc
            if pc != 200 or (pj or {}).get("status") != "running":
                break
            ac, _aj, adt = c.call("GET", "/api/activity", timeout=30)
            st_poll.add(ac, adt)
            time.sleep(1.5)
        st_q.add(last if last else 0, time.perf_counter() - t0)
        sc, _sj, sdt = c.call("POST", "/api/search", {"q": "underrun", "k": 5}, timeout=60)
        st_s.add(sc, sdt, str((_sj or {}).get("error", ""))[:160])


def mcp_user(env: Env, idx: int, rounds: int, st_q: Stat, st_s: Stat, stop: threading.Event):
    """외부 LLM 한 명 — `POST /mcp` 로 wiki_query · wiki_search 를 번갈아 부른다."""
    try:
        m = McpClient(env.base, "mcp%02d" % idx)
    except Exception:
        st_q.add(0, 0.0)
        return
    for r in range(rounds):
        if stop.is_set():
            return
        q = QUESTIONS[(idx + r) % len(QUESTIONS)] % (8000 + (idx + r) % 6)
        # 인자 이름은 MCP 스키마를 따른다 — wiki_query 는 `question`, wiki_search 는 `query` 다.
        code, res, dt = m.tool("wiki_query", {"question": q}, timeout=300)
        st_q.add(code if not res.get("_error") else 500, dt, res.get("_error", ""))
        code, res, dt = m.tool("wiki_search", {"query": "underrun", "k": 5}, timeout=120)
        st_s.add(code if not res.get("_error") else 500, dt, res.get("_error", ""))


def cli_user(env: Env, idx: int, rounds: int, st: Stat, stop: threading.Event):
    """터미널 한 명 — **별도 프로세스**로 `python -m llmwiki query`.

    서버와 같은 SQLite 파일에 다른 연결로 붙는다. 질의가 쓰기 잠금을 쥐던 문제(2026-09-23)가
    남아 있다면 여기서 `database is locked` 로 드러난다.
    """
    for r in range(rounds):
        if stop.is_set():
            return
        q = QUESTIONS[(idx + r) % len(QUESTIONS)] % (8000 + (idx + r) % 6)
        t0 = time.perf_counter()
        try:
            p = subprocess.run([sys.executable, "-m", "llmwiki", "query", q, "--json"],
                               cwd=ROOT, env=env.env, capture_output=True, timeout=600)
            st.add(200 if p.returncode == 0 else 500, time.perf_counter() - t0)
        except Exception:
            st.add(0, time.perf_counter() - t0)


# ---------------------------------------------------------------- 확인
def _security_json(path: str) -> None:
    """리버스 프록시 SSO 를 켜서 요청마다 **다른 사용자**가 되게 한다 (사내 배포와 같은 방식)."""
    cfg = {
        "mode": "on",
        "anonymous_role": "class3",          # 헤더가 없는 요청(CLI 게이트 등)도 읽기·질의는 된다
        "local": {"enabled": True, "min_password_len": 8},
        "sso": {"enabled": True, "type": "header", "default_role": "class3",
                "header": {"user": "X-Forwarded-User", "groups": "X-Forwarded-Groups",
                           "trusted_proxies": ["127.0.0.1"]}},
        "cli": {"default_role": "admin"},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def _ledger_by_origin(env: Env) -> dict:
    out = {}
    for r in env.ledger_rows(""):
        out[r.get("origin") or "-"] = out.get(r.get("origin") or "-", 0) + 1
    return out


def _ledger_matrix(env: Env):
    """종류 × 창구 행렬 — 합계만 보면 '어떤 요청이 어디에 들어갔는지' 를 확인할 수 없다.

    창구마다 **몇 줄이 나와야 맞는지**가 다르다:
      web 질의  = 접수(POST) + 잡(sub=job) 두 줄  · web 검색 = 한 줄
      mcp       = tools/call 마다 한 줄 + 세션 initialize 한 줄
      cli       = 프로세스마다 한 줄 (다른 프로세스에서 같은 폴더에 append)
    """
    rows = env.ledger_rows("")
    mat, kinds, origins = {}, set(), set()
    for r in rows:
        k, o = r.get("kind") or "-", r.get("origin") or "-"
        sub = r.get("sub") or ""
        key = (k + ("/" + sub if sub else ""), o)
        mat[key] = mat.get(key, 0) + 1
        kinds.add(k + ("/" + sub if sub else ""))
        origins.add(o)
    origins = sorted(origins)
    print("    원장 종류 × 창구 (총 %d줄)" % len(rows))
    print("      %-14s %s" % ("", " ".join("%8s" % o for o in origins)))
    for k in sorted(kinds):
        print("      %-14s %s" % (k, " ".join("%8d" % mat.get((k, o), 0) for o in origins)))
    return rows


def _corrupt_lines(env: Env) -> int:
    """읽을 수 없는 원장 줄 — 여러 프로세스가 같은 파일에 쓸 때 줄이 섞이면 생긴다.

    깨진 줄은 곧 **사라진 요청**이므로 부하 시험의 핵심 지표다.
    """
    d = os.path.join(env.data, "ledger")
    n = 0
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(d, fn), encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    json.loads(line)
                except Exception:
                    n += 1
    return n


def _locked_in_logs(env: Env) -> int:
    n = 0
    logs = os.path.join(env.tmp, "logs")
    for fn in os.listdir(logs) if os.path.isdir(logs) else []:
        if not fn.endswith(".log"):
            continue
        try:
            with open(os.path.join(logs, fn), encoding="utf-8", errors="replace") as f:
                n += sum(1 for line in f if "database is locked" in line)
        except OSError:
            pass
    return n


# ---------------------------------------------------------------- 본체
def run(users: int, rounds: int, port: int, keep: bool) -> int:
    web_n = max(1, int(users * 0.6))
    mcp_n = max(1, int(users * 0.2))
    cli_n = max(1, users - web_n - mcp_n)
    print("30명 동시 사용 시험 — Web %d명 · MCP %d명 · CLI %d명 (1인당 %d회) · 포트 %d"
          % (web_n, mcp_n, cli_n, rounds, port))
    print("격리 환경(임시 폴더 · mock LLM). 실제 색인·설정·원장·로그는 건드리지 않는다.")

    env = Env(port, keep=keep,
              concurrency={"max_parallel_reads": 8, "queue_max": 128, "queue_timeout_s": 300,
                           "max_parallel_per_user": 3, "max_parallel_per_ip": 99,
                           "classes": {"query": {"max_parallel": 6, "queue_max": 96, "queue_timeout_s": 300},
                                       "search": {"max_parallel": 4, "queue_max": 32, "queue_timeout_s": 30},
                                       "mcp": {"max_parallel": 4, "queue_max": 32, "queue_timeout_s": 300},
                                       "cli": {"max_parallel": 2, "queue_max": 8, "queue_timeout_s": 300}}},
              rate_limit={"enabled": True, "per_user_per_min": 120, "per_ip_per_min": 0,
                          "classes": {"query": {"per_user_per_min": 60}, "search": {"per_user_per_min": 120}}})
    bad = 0
    try:
        print("  임시 폴더: %s" % env.tmp)
        _security_json(os.path.join(env.tmp, "security.json"))
        print("  색인 빌드 중…")
        env.build()
        env.start()

        order = ("web:접수", "web:질의", "web:검색", "web:폴링", "mcp:질의", "mcp:검색", "cli:질의")
        st = {k: Stat(k) for k in order}
        stop = threading.Event()
        ths = []
        for i in range(web_n):
            ths.append(threading.Thread(target=web_user, args=(env, i, rounds, st["web:질의"], st["web:검색"],
                                                               st["web:폴링"], st["web:접수"], stop)))
        for i in range(mcp_n):
            ths.append(threading.Thread(target=mcp_user, args=(env, i, rounds, st["mcp:질의"], st["mcp:검색"], stop)))
        for i in range(cli_n):
            ths.append(threading.Thread(target=cli_user, args=(env, i, rounds, st["cli:질의"], stop)))

        t0 = time.perf_counter()
        for t in ths:
            t.start()
        for t in ths:
            t.join(1800)
        stop.set()
        dt = time.perf_counter() - t0
        time.sleep(2)

        sent = sum(len(s.ms) for s in st.values())
        print("\n  == 창구별 (총 %d회 · %.0f초 · %.1f회/초) ==" % (sent, dt, sent / max(0.01, dt)))
        for k in order:
            print(st[k].line())

        print("\n  == 판정 ==")
        errs = sum(s.server_error for s in st.values())
        if errs:
            print("    FAIL 서버 오류(5xx) %d건 — 거절(429/503)과 달리 이것은 결함이다" % errs)
            for k in order:
                for e in st[k].errors:
                    print("         %-10s %s" % (k, e))
            bad += 1
        else:
            print("    OK   서버 오류(5xx) 0건 (거절 %d건은 한도가 일한 것)"
                  % sum(s.rejected for s in st.values()))

        rows = _ledger_matrix(env)
        origins = _ledger_by_origin(env)
        missing = [o for o in ("web", "cli", "mcp") if not origins.get(o)]
        if missing:
            print("    FAIL 원장에 %s 창구의 기록이 없다 — 그 경로의 요청은 사라진다" % ", ".join(missing))
            bad += 1
        else:
            print("    OK   세 창구의 요청이 모두 원장에 남았다")

        # 창구마다 **보낸 수와 남은 수**를 직접 맞춰 본다. 합계만 보면 한 창구가 통째로 빠져도 모른다.
        cli_sent = len(st["cli:질의"].ms)
        cli_rows = len([r for r in rows if (r.get("origin") or "") == "cli" and (r.get("kind") or "") == "query"])
        web_sent = len(st["web:접수"].ms)
        web_rows = len([r for r in rows if (r.get("origin") or "") == "web" and (r.get("kind") or "") == "query"
                        and not (r.get("sub") or "")])
        print("    보낸 수 ↔ 원장: CLI %d↔%d · Web 질의 %d↔%d" % (cli_sent, cli_rows, web_sent, web_rows))
        for label, sent_n, got_n in (("CLI", cli_sent, cli_rows), ("Web 질의", web_sent, web_rows)):
            if got_n < sent_n:
                print("    FAIL %s 요청 %d건 중 %d건만 원장에 남았다 (%d건이 사라졌다)"
                      % (label, sent_n, got_n, sent_n - got_n))
                bad += 1

        corrupt = _corrupt_lines(env)
        if corrupt:
            print("    FAIL 읽을 수 없는 원장 줄 %d개 — 여러 프로세스의 쓰기가 한 줄 안에서 섞였다 "
                  "(그 줄의 요청은 사라진다)" % corrupt)
            bad += 1
        else:
            print("    OK   깨진 원장 줄 0개 (서버·CLI 여러 프로세스가 같은 파일에 동시에 써도)")

        locked = _locked_in_logs(env)
        if locked:
            print("    FAIL 'database is locked' %d건 — 질의가 쓰기 잠금을 쥐고 있다" % locked)
            bad += 1
        else:
            print("    OK   'database is locked' 0건 (CLI 가 별도 프로세스로 같은 DB 를 쓰는 동안에도)")

        qp95 = max(st["web:질의"].p95(), st["mcp:질의"].p95())
        sp95 = max(st["web:검색"].p95(), st["mcp:검색"].p95())
        pp95 = st["web:폴링"].p95()
        print("    질의 p95 %.0fms(끝까지) · 접수 p95 %.0fms · 검색 p95 %.0fms · 화면 폴링 p95 %.0fms"
              % (qp95, st["web:접수"].p95(), sp95, pp95))
        if sp95 >= qp95:
            print("    주의 검색이 질의만큼 느리다 — 종류별 예약 슬롯이 일하지 않았을 수 있다")
        else:
            print("    OK   느린 질의가 도는 동안에도 검색이 갇히지 않았다 (예약 슬롯이 일했다)")
        if pp95 > 5000:
            print("    FAIL 화면 갱신 폴링 p95 가 %.0fms — 사용자에게는 '화면이 멈춘' 것으로 보인다" % pp95)
            bad += 1
        else:
            print("    OK   화면 갱신 폴링이 계속 응답했다 (질의가 몰려도 화면이 멈추지 않는다)")
    finally:
        env.stop()
        if keep:
            print("\n임시 폴더를 남겼습니다: %s" % env.tmp)
    print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Web·CLI·MCP 동시 부하 시험")
    ap.add_argument("--users", type=int, default=30, help="동시 사용자 수 (기본 30 — Web 60%% · MCP 20%% · CLI 20%%)")
    ap.add_argument("--rounds", type=int, default=2, help="1인당 반복 수")
    ap.add_argument("--port", type=int, default=8831)
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 남긴다")
    ns = ap.parse_args(argv)
    return run(ns.users, ns.rounds, ns.port, ns.keep)


if __name__ == "__main__":
    raise SystemExit(main())
