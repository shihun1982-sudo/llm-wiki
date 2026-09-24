# -*- coding: utf-8 -*-
"""멍키/퍼즈 테스트 — 무작위 입력이 Web · MCP · CLI 로 쏟아질 때 서버가 버티는지. (2026-09-15)

무엇을 보나
  1. **살아남는가**: 무작위 요청 폭격 뒤에도 서버 프로세스가 죽지 않고, 정상 질의가 다시 200 으로 답하는가.
  2. **500 이 나지 않는가**: 잘못된 입력은 400/401/403/404/413/422/429/503 처럼 *의도된* 코드로 거절되어야 한다.
     500(Internal Server Error)은 처리하지 못한 예외이므로 전부 결함으로 모아 보고한다.
  3. **막히지 않는가**: 동시 폭격 중에도 정상 질의의 지연이 한계 안에 있고, 대기열/속도 제한이 429/503 으로 정직하게 거절하는가.
  4. **CLI·MCP 도 같이**: 무작위 argv 로 CLI 를 돌렸을 때 파이썬 traceback 이 새어 나오지 않는가, 무작위 JSON-RPC 에 MCP 가 isError 로 답하는가.

기본은 **격리 실행**: 현재 config.json 을 복사하고 data_dir 만 임시 폴더로 바꾼 뒤(색인 DB 는 복사해 재사용) 서버를 띄운다.
실제 운영 DB 는 건드리지 않는다. 이미 떠 있는 서버를 때리려면 --url 을 준다(그 경우 파괴적 작업은 보내지 않는다).

사용:
    python tools/verify/verify_monkey.py                       # 격리 서버 + 기본 강도
    python tools/verify/verify_monkey.py --requests 3000 --threads 24
    python tools/verify/verify_monkey.py --url http://127.0.0.1:8765 --token <API_KEY>
    python tools/verify/verify_monkey.py --seed 42             # 재현
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import string
import subprocess
import sys

# 콘솔이 cp949 여도 한글·기호 출력에서 죽지 않게 (다른 verify_* 와 같은 처리, 2026-09-24)
try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
except Exception:
    pass
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
sys.path.insert(0, ROOT)

# ---------------------------------------------------------------- 무작위 입력 생성기
WEIRD = [
    "", " ", "\n", "\t\r\n", "\x00", "\x00\x01\x02", "\\", "\\\\", "'", '"', "`", "${jndi:ldap://x}",
    "'; DROP TABLE chunks; --", "<script>alert(1)</script>", "../../etc/passwd", "..\\..\\windows\\win.ini",
    "%s%s%s%n", "{{7*7}}", "null", "undefined", "NaN", "-1", "0", "1e308", "9" * 400,
    "한글 질의 테스트", "🙂🚀🔥", "日本語テスト", "العربية", "‮evil", "\ud83d", "a" * 10000,
    "SELECT * FROM", "*", "?", "()", "[]", "{}", "|", "&&", ";", "…",
    "ISSUE-2001", "CL-55301", "SWD-RFC-1661", "PPP LCP", "RFC 1661 옵션 협상",
]
KEYS = ["q", "query", "question", "k", "limit", "id", "name", "action", "overrides", "preset", "mode", "channel",
        "full", "reset", "settings", "values", "token", "task", "model", "argv", "docs", "terms", "feedback",
        "query_id", "request_id", "kind", "payload", "confidence", "rules", "content", "progress_token"]


def rnd_scalar(rng):
    r = rng.random()
    if r < 0.35:
        return rng.choice(WEIRD)
    if r < 0.5:
        return rng.randint(-2 ** 40, 2 ** 40)
    if r < 0.6:
        return rng.choice([True, False, None])
    if r < 0.7:
        return rng.random() * 1e9
    if r < 0.8:
        return "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 200)))
    return rng.choice(WEIRD)


def rnd_value(rng, depth=0):
    if depth > 3 or rng.random() < 0.55:
        return rnd_scalar(rng)
    if rng.random() < 0.5:
        return [rnd_value(rng, depth + 1) for _ in range(rng.randint(0, 5))]
    return {str(rng.choice(KEYS + WEIRD[:8])): rnd_value(rng, depth + 1) for _ in range(rng.randint(0, 5))}


def rnd_body(rng):
    if rng.random() < 0.1:
        return None
    return {rng.choice(KEYS): rnd_value(rng) for _ in range(rng.randint(1, 5))}


GET_PATHS = ["/api/status", "/api/docs", "/api/graph", "/api/entity", "/api/chunk", "/api/doc_chunks", "/api/queries",
             "/api/requests", "/api/request", "/api/query_trace", "/api/models", "/api/models/catalog", "/api/system",
             "/api/watch", "/api/tuning", "/api/architecture", "/api/limits", "/api/evolve/status", "/api/evolve/proposals",
             "/api/wiki/list", "/api/wiki/page", "/api/eval/questions", "/api/rules", "/api/health", "/api/config/effective",
             "/api/presets", "/api/presets/diff", "/api/prompts", "/api/logs/files", "/api/logs", "/api/forensics",
             "/api/forensics/summary", "/api/forensic", "/api/trials", "/api/trial", "/api/trials/compare", "/api/pins",
             "/api/query_rules", "/api/query_rules/test", "/api/embed/report", "/api/build/status", "/api/build/verify",
             "/api/corpus/lint", "/api/corpus/types", "/api/mcp_sources", "/api/memory", "/api/precompute", "/api/agents",
             "/api/themes", "/api/time", "/api/analysis", "/api/activity", "/api/progress", "/api/schedule", "/api/auth/me",
             "/api/query_rules/lint", "/api/collab", "/api/collab/board",
             "/api/jobs/zzz", "/api/nope", "/", "/login", "/static/js/core.js", "/static/../config.json", "/mcp"]
POST_PATHS = ["/api/query", "/api/search", "/api/feedback", "/api/forensic/expect", "/api/forensic/llm", "/api/evolve/propose",
              "/api/pins", "/api/presets", "/api/query_rules", "/api/tuning", "/api/prompts", "/api/rules", "/api/memory",
              "/api/precompute", "/api/trials", "/api/fusion/compare", "/api/eval", "/api/watch", "/api/mcp_sources",
              "/api/build/verify", "/api/wiki/page", "/api/evolve/apply", "/api/evolve/reject", "/api/evolve/review",
              "/api/models/test", "/api/activity", "/api/schedule", "/api/models/catalog", "/api/cli", "/api/collab",
              "/mcp", "/api/nope"]
# 색인을 지우거나 서버 설정을 영구히 바꾸는 경로는 기본으로 보내지 않는다
DESTRUCTIVE = {"/api/build", "/api/config", "/api/models/set", "/api/snapshot", "/api/maintenance", "/api/auth/users",
               "/api/security", "/api/apikeys", "/api/agents", "/api/admin/server"}
MCP_TOOLS = ["wiki_query", "wiki_search", "wiki_related", "wiki_doc", "wiki_entity", "wiki_propose", "wiki_feedback",
             "wiki_forensic", "wiki_status", "wiki_sources", "wiki_external_search", "wiki_analysis", "nope_tool", ""]
CLI_ARGS = [["--help"], ["stats"], ["health", "--quick"], ["build", "status"], ["corpus", "types"], ["models", "list"],
            ["schedule", "list"], ["logs", "tail", "-n", "3"], ["requests", "list"], ["nonexistent"], ["query"],
            ["query", "--k", "-1"], ["tuning", "set", "zzz=1"], ["config", "show"], ["--bogus-flag"], ["forensic", "last"],
            ["preset", "show"], ["entity"], ["search", "fts"], ["time"], ["analyze", "notanumber"], ["server", "status"]]


class Monkey:
    def __init__(self, base, token="", rng=None, destructive=False, timeout=60):
        self.base, self.token, self.rng = base, token, rng or random.Random()
        self.destructive, self.timeout = destructive, timeout
        self.lock = threading.Lock()
        self.codes = Counter()
        self.findings = []           # 500 / 연결 실패 / traceback
        self.slow = []
        self.n = 0
        self.last_reject = ""     # 마지막 거절 응답 본문 (사후 확인이 실패했을 때 이유를 보고서에 남긴다)

    @staticmethod
    def _quote_value(v):
        """질의 문자열 값 하나를 안전하게 인코딩.

        무작위 입력에는 짝 없는 서러게이트(예: '\\ud83d')가 섞이는데, 그대로 quote 하면
        **클라이언트 쪽**이 UnicodeEncodeError 로 죽어 버려 서버를 시험하지 못한다.
        여기서 대체 문자로 낮춰 두면 서버에는 깨진 바이트가 그대로 전달된다.
        """
        s = str(v)[:80].encode("utf-8", "replace").decode("utf-8", "replace")
        return urllib.parse.quote(s, safe="")

    def record(self, kind, detail):
        with self.lock:
            if len(self.findings) < 200:
                self.findings.append({"kind": kind, **detail})

    def req(self, method, path, body=None, headers=None, timeout=None):
        h = {"X-Requested-With": "llmwiki-monkey"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        h.update(headers or {})
        data = None
        if body is not None:
            try:
                # ensure_ascii=True: 짝 없는 서러게이트(\ud83d)도 \uXXXX 로 이스케이프되어 클라이언트가 죽지 않는다
                data = json.dumps(body, ensure_ascii=True, default=str).encode("ascii", "replace")
            except Exception:
                data = b'{"q": "unserializable"}'
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                # 본문을 **돌려준다** (2026-09-24). 예전에는 200 이면 "" 를 돌려줘서 drain()·capacity_note() 가
                # 언제나 빈 활동 목록을 보고 "대기열 비움" 으로 판정했다 — 실제로는 버려진 요청 128건이
                # 30분짜리 대기열에 남아 있었다. 응답을 버리는 검증은 검증이 아니다 (CODE_REVIEW_0924 §5 규칙 2).
                return r.status, time.time() - t0, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            payload = ""
            try:
                payload = e.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            return e.code, time.time() - t0, payload
        except Exception as e:
            return -1, time.time() - t0, "%s: %s" % (type(e).__name__, str(e)[:200])

    def one(self):
        rng = self.rng
        roll = rng.random()
        if roll < 0.40:
            method, path = "GET", rng.choice(GET_PATHS)
            if rng.random() < 0.6:
                path += "?" + "&".join("%s=%s" % (rng.choice(KEYS), self._quote_value(rnd_scalar(rng))) for _ in range(rng.randint(1, 3)))
            body = None
        elif roll < 0.80:
            method, path, body = "POST", rng.choice(POST_PATHS), rnd_body(rng)
            if path == "/mcp":
                body = {"jsonrpc": "2.0", "id": rng.randint(0, 99), "method": rng.choice(["initialize", "tools/list", "tools/call", "ping", "nope"]),
                        "params": {"name": rng.choice(MCP_TOOLS), "arguments": rnd_body(rng) or {}}}
            if path == "/api/cli":
                body = {"argv": rng.choice(CLI_ARGS) if rng.random() < 0.6 else str(rnd_scalar(rng))[:60]}
        elif roll < 0.86:
            method, path, body = rng.choice(["PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]), rng.choice(GET_PATHS + POST_PATHS), None
        elif roll < 0.92:
            method, path, body = "POST", "/api/query", {"q": rng.choice(WEIRD), "overrides": rnd_value(self.rng), "preset": rnd_scalar(rng)}
        else:
            method, path, body = "GET", "/" + "".join(rng.choice("abcdefghijklmnopq/._-") for _ in range(rng.randint(1, 30))), None
        if not self.destructive and path in DESTRUCTIVE:
            path = "/api/status"
            method, body = "GET", None
        code, el, payload = self.req(method, path, body)
        with self.lock:
            self.n += 1
            self.codes[code] += 1
            if el > 30:
                self.slow.append((round(el, 1), method, path))
        if code == 500:
            self.record("http_500", {"method": method, "path": path, "body": json.dumps(body, ensure_ascii=False, default=str)[:300] if body else None,
                                     "response": payload[:300]})
        elif code == -1 and "timed out" not in payload:
            self.record("connection", {"method": method, "path": path, "error": payload})
        return code

    def malformed_bodies(self):
        """JSON 이 아닌 본문 · 잘못된 Content-Length · 거대한 본문."""
        import http.client
        host = self.base.split("//", 1)[1]
        hostname, _, port = host.partition(":")
        for name, raw, ctype in (("not-json", b"{{{ not json", "application/json"),
                                 ("binary", bytes(range(256)) * 8, "application/json"),
                                 ("huge", b'{"q": "' + b"A" * 3_000_000 + b'"}', "application/json"),
                                 ("empty-ct", b'{"q":"x"}', ""),
                                 ("form", b"q=x&k=1", "application/x-www-form-urlencoded")):
            try:
                c = http.client.HTTPConnection(hostname, int(port or 80), timeout=self.timeout)
                h = {"X-Requested-With": "llmwiki-monkey"}
                if ctype:
                    h["Content-Type"] = ctype
                c.request("POST", "/api/query", body=raw, headers=h)
                r = c.getresponse()
                body = r.read()[:200]
                with self.lock:
                    self.codes[r.status] += 1
                    self.n += 1
                if r.status == 500:
                    self.record("http_500", {"method": "POST", "path": "/api/query (%s)" % name, "response": body.decode("utf-8", "replace")[:200]})
                c.close()
            except Exception as e:
                self.record("malformed", {"case": name, "error": "%s: %s" % (type(e).__name__, str(e)[:150])})

    def drain(self, limit_s: float = 360.0):
        """폭격으로 밀어 넣은 작업이 끝날 때까지 기다린다.

        폭격 직후의 429/503 은 **정상 동작**(용량 초과를 정직하게 거절)이지 결함이 아니다.
        그러나 "폭격 뒤에도 서버가 멀쩡한가"를 보려면 큐가 빈 상태에서 물어야 한다.
        그래서 먼저 진행 중·대기 중 요청이 0이 될 때까지 기다리고, 그 다음에 정상 질의를 던진다.
        반환값은 (빠져나갔는가, 마지막으로 본 진행/대기 건수).
        """
        t0 = time.time()
        last = None
        while time.time() - t0 < limit_s:
            code, _el, payload = self.req("GET", "/api/activity", timeout=20)
            if code != 200:
                # 활동 목록을 볼 수 없으면 권한 없음 등 — 판단 근거가 없으니 짧게만 기다린다
                time.sleep(2.0)
                return None, None
            try:
                j = json.loads(payload) if payload else {}
            except ValueError:
                j = {}
            running = len(j.get("running") or []) + len(j.get("external") or [])
            queued = len(j.get("queued") or [])
            lock = j.get("lock") or {}
            # 대기열이 비어도 배타 작업이 있고 쓰기가 줄 서 있으면 읽기는 계속 막힌다(writer preference).
            blocked = bool(lock.get("writer")) or int(lock.get("writers_waiting") or 0) > 0
            last = (running, queued, "writer:%s/%s" % (lock.get("writer_label") or "-", lock.get("writers_waiting") or 0))
            if running == 0 and queued == 0 and not blocked:
                return True, last
            time.sleep(2.0)
        return False, last

    def healthy(self, tries: int = 1, wait: float = 3.0, drain_first: bool = False):
        """정상 질의가 제대로 답하는가.

        429/503 은 서버가 살아 있다는 증거이지 결함이 아니므로 재시도한다.
        서버가 `Retry-After` 를 주면 그 값을 존중하고, 아니면 대기 시간을 점점 늘린다.
        """
        if drain_first:
            self.drain()
        code = el = 0
        for i in range(max(1, tries)):
            code, el, payload = self.req("POST", "/api/query", {"q": "PPP LCP 협상 절차는?", "log": False}, timeout=180)
            self.last_reject = payload[:300] if code != 200 else ""
            if code == 200:
                return True, code, round(el, 1)
            if code not in (429, 503) or i == tries - 1:
                break
            after = wait * (1.5 ** i)
            try:                                  # 서버가 알려 준 재시도 시점을 따른다
                j = json.loads(payload) if payload else {}
                after = max(after, min(30.0, float(j.get("retry_after") or j.get("retry_after_s") or 0)))
            except Exception:
                pass
            time.sleep(min(after, 30.0))
        return False, code, round(el, 1)

    def capacity_note(self):
        """건강 확인이 실패했을 때 '무엇 때문에 거절됐는지'를 보고서에 남긴다."""
        code, _el, payload = self.req("GET", "/api/activity", timeout=20)
        if code != 200:
            return {"activity_code": code}
        try:
            j = json.loads(payload) if payload else {}
        except ValueError:
            return {"activity_code": code}
        return {"running": [(a.get("kind"), a.get("label"), a.get("elapsed_s")) for a in (j.get("running") or [])][:5],
                "queued": [(a.get("kind"), a.get("weight"), a.get("elapsed_s")) for a in (j.get("queued") or [])][:5],
                "queued_n": len(j.get("queued") or []), "external": len(j.get("external") or []),
                "lock": j.get("lock"), "limits": j.get("limits")}


def run_cli_monkey(rng, n, env, findings, lock):
    """무작위 argv 로 CLI 실행 — traceback 이 새어 나오면 결함."""
    for _ in range(n):
        argv = list(rng.choice(CLI_ARGS))
        if rng.random() < 0.4:
            argv.append(str(rnd_scalar(rng))[:60])
        # NUL 은 **실제 명령줄에 넣을 수 없다** — OS 가 금지한다. 여기서 빼두지 않으면 CLI 가 시작도 못 하고
        # subprocess 가 ValueError 로 터져, 제품 결함이 아닌 하네스 오류가 결함으로 기록된다 (2026-09-16).
        # Web 경로(/api/cli)는 JSON 으로 NUL 이 들어올 수 있어 서버가 400 으로 거절한다 (_as_argv).
        argv = [a.replace("\x00", "") for a in argv]
        try:
            p = subprocess.run([PY, "-m", "llmwiki"] + argv, cwd=ROOT, env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
            out = (p.stdout or "") + (p.stderr or "")
            if "Traceback (most recent call last)" in out:
                with lock:
                    findings.append({"kind": "cli_traceback", "argv": " ".join(argv), "out": out[-400:]})
            if "UnicodeEncodeError" in out:
                with lock:
                    findings.append({"kind": "cli_encoding", "argv": " ".join(argv), "out": out[-300:]})
        except subprocess.TimeoutExpired:
            with lock:
                findings.append({"kind": "cli_timeout", "argv": " ".join(argv)})
        except Exception as e:
            with lock:
                findings.append({"kind": "cli_error", "argv": " ".join(argv), "error": str(e)[:200]})


def main() -> int:
    ap = argparse.ArgumentParser(description="무작위 입력 멍키 테스트 (Web · MCP · CLI)")
    ap.add_argument("--url", default="", help="이미 떠 있는 서버 (없으면 격리 서버를 직접 띄운다)")
    ap.add_argument("--token", default="", help="API 키 (선택)")
    ap.add_argument("--requests", type=int, default=1200, help="총 HTTP 요청 수")
    ap.add_argument("--threads", type=int, default=16, help="동시 스레드 수")
    ap.add_argument("--cli", type=int, default=15, help="무작위 CLI 실행 횟수 (0 = 생략)")
    ap.add_argument("--seed", type=int, default=None, help="난수 시드 (재현용)")
    ap.add_argument("--port", type=int, default=8794)
    ap.add_argument("--destructive", action="store_true", help="빌드·설정 변경 같은 파괴적 경로도 포함 (격리 실행에서만)")
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 지우지 않는다")
    ap.add_argument("--real-llm", action="store_true", help="격리 서버에서도 config.json 의 실제 LLM 을 쓴다 (느림; 기본은 mock)")
    ns = ap.parse_args()
    try:
        from llmwiki import console as _c
        _c.setup()
    except Exception:
        pass
    seed = ns.seed if ns.seed is not None else random.randrange(1 << 30)
    print("멍키 테스트 seed=%d requests=%d threads=%d cli=%d%s" % (seed, ns.requests, ns.threads, ns.cli, " destructive" if ns.destructive else ""))

    tmp = None
    proc = None
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    base = ns.url.rstrip("/")
    if not base:
        # 이미 떠 있는 포트에는 새로 붙지 않는다 — 이전 실행에서 남은 서버(옛 코드)를 때리면 결과가 거짓이 된다.
        # 다만 **중단하지는 않는다**: 고아 프로세스 하나 때문에 전체 검증이 멈추는 일이 실제로 있었다
        # (verify_all 의 첫 단계에서만 0.1초 만에 실패). 비어 있는 다음 포트를 찾아 계속한다.
        import socket as _s

        def _busy(port):
            probe = _s.socket()
            probe.settimeout(1.0)
            try:
                return probe.connect_ex(("127.0.0.1", port)) == 0
            finally:
                probe.close()

        if _busy(ns.port):
            alt = next((p for p in range(ns.port + 1, ns.port + 40) if not _busy(p)), None)
            if alt is None:
                print("포트 %d~%d 가 모두 사용 중입니다 — 이전 실행의 서버가 남아 있는지 확인하세요." % (ns.port, ns.port + 39))
                return 2
            print("포트 %d 가 사용 중이라 %d 로 옮깁니다 (이전 실행의 서버가 남아 있을 수 있습니다)." % (ns.port, alt))
            ns.port = alt
        tmp = tempfile.mkdtemp(prefix="lwmonkey_")
        cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
        cfg["data_dir"] = os.path.join(tmp, "data")
        cfg["wiki_dir"] = os.path.join(tmp, "wiki")
        cfg["log_level"] = "DEBUG"
        # 목적은 '서버가 쓰레기 입력을 견디는가' 이지 LLM 속도가 아니다 — mock LLM 으로 빠르게 많이 때린다.
        # (실제 LLM 으로 부하를 보려면 --url 로 운영 서버를 지정하고 --requests 를 줄인다.)
        if not ns.real_llm:
            cfg["llm_provider"] = "mock"
            cfg["llm_roles"] = {}
            cfg["toggles"] = dict(cfg.get("toggles") or {}, auto_build=False, precompute=False, query_cache=False)
        os.makedirs(cfg["data_dir"])
        src_db = os.path.join(ROOT, "data", "llmwiki.sqlite3")
        if os.path.exists(src_db):
            print("  색인 DB 복사 중 (%.0f MB)…" % (os.path.getsize(src_db) / 1e6))
            shutil.copy2(src_db, os.path.join(cfg["data_dir"], cfg.get("db_name", "llmwiki.sqlite3")))
        if os.path.isdir(os.path.join(ROOT, "wiki")):
            shutil.copytree(os.path.join(ROOT, "wiki"), cfg["wiki_dir"])
        json.dump(cfg, open(os.path.join(tmp, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for f in ("tuning.json", "presets.json", "query_rules.json", "mcp_sources.json", "agents.json", "pins.json",
                  "security.json", "server.json", "schedule.json", "models.json"):
            s = os.path.join(ROOT, f)
            if os.path.exists(s):
                shutil.copy2(s, os.path.join(tmp, f))
        shutil.copy2(os.path.join(ROOT, "data", "rules.json"), os.path.join(tmp, "rules.json"))
        shutil.copytree(os.path.join(ROOT, "schemas"), os.path.join(tmp, "schemas"))
        shutil.copytree(os.path.join(ROOT, "prompts"), os.path.join(tmp, "prompts"))
        shutil.copy2(os.path.join(ROOT, "eval", "questions.json"), os.path.join(tmp, "questions.json"))
        env.update(LLMWIKI_CONFIG=os.path.join(tmp, "config.json"), LLMWIKI_TUNING_PATH=os.path.join(tmp, "tuning.json"),
                   LLMWIKI_PRESETS_PATH=os.path.join(tmp, "presets.json"), LLMWIKI_QUERY_RULES_PATH=os.path.join(tmp, "query_rules.json"),
                   LLMWIKI_MCP_SOURCES_PATH=os.path.join(tmp, "mcp_sources.json"), LLMWIKI_AGENTS_PATH=os.path.join(tmp, "agents.json"),
                   LLMWIKI_PINS_PATH=os.path.join(tmp, "pins.json"), LLMWIKI_RULES_PATH=os.path.join(tmp, "rules.json"),
                   LLMWIKI_SCHEMAS_DIR_PATH=os.path.join(tmp, "schemas"), LLMWIKI_PROMPTS_DIR_PATH=os.path.join(tmp, "prompts"),
                   LLMWIKI_EVAL_PATH=os.path.join(tmp, "questions.json"), LLMWIKI_LOGS_DIR_PATH=os.path.join(tmp, "logs"),
                   LLMWIKI_SECURITY_PATH=os.path.join(tmp, "security.json"), LLMWIKI_SERVER_PATH=os.path.join(tmp, "server.json"),
                   LLMWIKI_SCHEDULE_PATH=os.path.join(tmp, "schedule.json"), LLMWIKI_MODELS_PATH=os.path.join(tmp, "models.json"))
        env.pop("LLMWIKI_API_KEY", None)
        proc = subprocess.Popen([PY, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(ns.port)], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        # 서버 출력 파이프를 **반드시 비운다** (2026-09-24, CODE_REVIEW_0924 §2.10): 읽지 않으면 버퍼가 차는 순간
        # 서버의 stderr 쓰기가 영원히 막히고, 배타 잠금을 쥔 스레드가 막히면 서버 전체가 멎는다 (멍키 테스트 실측 28분).
        threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()
        base = "http://127.0.0.1:%d" % ns.port
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/api/auth/me", timeout=2)
                break
            except Exception:
                if proc.poll() is not None:
                    print("서버가 즉시 종료되었습니다 (exit %s)" % proc.returncode)
                    return 2
                time.sleep(0.5)
        if proc.poll() is not None:
            print("서버가 시작되지 못했습니다 (exit %s)" % proc.returncode)
            return 2
        print("  격리 서버 시작: %s (data=%s, pid=%s)" % (base, tmp, proc.pid))
    else:
        print("  대상 서버: %s (격리 아님 — 파괴적 경로는 보내지 않습니다)" % base)
        ns.destructive = False

    m = Monkey(base, ns.token, random.Random(seed), ns.destructive)
    ok0, code0, el0 = m.healthy()
    print("  사전 확인: 정상 질의 %s (%.1fs)" % ("OK" if ok0 else "FAIL code=%s" % code0, el0))

    findings_lock = threading.Lock()
    cli_findings = []
    cli_thread = None
    if ns.cli:
        cli_thread = threading.Thread(target=run_cli_monkey, args=(random.Random(seed + 1), ns.cli, env, cli_findings, findings_lock), daemon=True)
        cli_thread.start()

    stop = threading.Event()
    per = max(1, ns.requests // ns.threads)

    def worker(i):
        rng = random.Random(seed * 131 + i)
        mm = Monkey(base, ns.token, rng, ns.destructive)
        mm.lock, mm.codes, mm.findings, mm.slow = m.lock, m.codes, m.findings, m.slow
        for _ in range(per):
            if stop.is_set():
                return
            try:
                mm.one()
            except Exception as e:
                m.record("client_error", {"error": "%s: %s" % (type(e).__name__, str(e)[:200]), "trace": traceback.format_exc()[-300:]})
            with m.lock:
                m.n = mm.n = m.n
    t0 = time.time()
    ths = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(ns.threads)]
    [t.start() for t in ths]
    # 폭격 중에도 정상 질의가 되는지 주기적으로 확인
    mid = []
    while any(t.is_alive() for t in ths):
        time.sleep(3)
        if proc is not None and proc.poll() is not None:
            m.record("server_died", {"exit": proc.returncode})
            stop.set()
            break
        ok, code, el = m.healthy()
        mid.append((ok, code, el))
    [t.join(60) for t in ths]
    elapsed = time.time() - t0
    m.malformed_bodies()
    if cli_thread:
        cli_thread.join(600)
    m.findings.extend(cli_findings)

    # 폭격으로 밀어 넣은 작업이 끝나기 전에 물으면 503 이 나오는데, 그건 결함이 아니다
    # 용량 초과를 정직하게 거절한 것이다. 큐가 빈 뒤에 물어야 "살아남았는가"를 볼 수 있다.
    # 폭격 때 넣긴 요청들의 서버 스레드는 클라이언트가 떠난 뒤에도 살아 대기열을 채운다.
    # 그것들이 queue_timeout_s 로 풀리기 전까지 기다린 뒤에 물어야 '살아남았는가' 를 볼 수 있다.
    drained, backlog = m.drain()
    print("사후 정리: 대기열 %s (진행/대기 %s)" % ("비움" if drained else ("확인 불가" if drained is None else "남음"), backlog))
    # 폭격에 튜닝·프리셋을 무작위 값으로 바꿔 놓았을 수 있다. 그 조합은 '설정이 비정상인 것' 이지
    # '서버가 망가진 것' 이 아니므로, 마지막 생존 확인 전에 기본값으로 되돌린다.
    if tmp:
        rc, _e, _b = m.req("POST", "/api/tuning", {"action": "reset"}, timeout=60)
        print("사후 정리: 튜닝 기본값 복구 %s" % ("OK" if rc == 200 else "실패 %s" % rc))
    ok1, code1, el1 = m.healthy(tries=12, wait=6.0, drain_first=True)
    note = {} if ok1 else dict(m.capacity_note(), response=m.last_reject)
    if note:
        print("  거절 사유 단서: %s" % json.dumps(note, ensure_ascii=False)[:500])
    alive = proc is None or proc.poll() is None
    print("\n요청 %d건 / %.0f초 (%.0f req/s)" % (sum(m.codes.values()), elapsed, sum(m.codes.values()) / max(0.1, elapsed)))
    print("상태 코드: %s" % ", ".join("%s×%d" % (k if k != -1 else "conn-err", v) for k, v in sorted(m.codes.items(), key=lambda x: -x[1])))
    good = sum(v for k, v in m.codes.items() if 200 <= k < 500 or k in (429, 503))
    print("정상 처리·정상 거절: %d / 500 오류: %d / 연결 실패: %d" % (good, m.codes.get(500, 0), m.codes.get(-1, 0)))
    if mid:
        okn = sum(1 for o, _, _ in mid if o)
        print("폭격 중 정상 질의: %d/%d 성공, 지연 %.1f~%.1fs" % (okn, len(mid), min(e for _, _, e in mid), max(e for _, _, e in mid)))
    if m.slow:
        print("30초 초과 요청 %d건 (예: %s)" % (len(m.slow), m.slow[:3]))
    print("사후 확인: 서버 %s · 정상 질의 %s (%.1fs)" % ("살아 있음" if alive else "죽음", "OK" if ok1 else "FAIL code=%s" % code1, el1))
    by_kind = Counter(f["kind"] for f in m.findings)
    print("결함 후보 %d건: %s" % (len(m.findings), dict(by_kind) or "-"))
    for f in m.findings[:25]:
        print("  [%s] %s" % (f["kind"], json.dumps({k: v for k, v in f.items() if k != "kind"}, ensure_ascii=False)[:240]))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_monkey_result.json")
    json.dump({"seed": seed, "requests": sum(m.codes.values()), "elapsed_s": round(elapsed, 1),
               "codes": {str(k): v for k, v in m.codes.items()}, "findings": m.findings,
               "alive": alive, "healthy_before": ok0, "healthy_after": ok1, "mid": mid,
               "drained": drained, "backlog_after": backlog, "capacity_note": note},
              open(out, "w", encoding="utf-8"), ensure_ascii=True, indent=1, default=str)
    print("결과: %s" % out)
    if proc is not None:
        proc.terminate()
        try:
            proc.communicate(timeout=10)
        except Exception:
            proc.kill()
    if tmp and not ns.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    elif tmp:
        print("임시 폴더 유지: %s" % tmp)
    # 결함으로 보는 것: 서버 예외(500) · 서버 사망 · 연결 실패(시간 초과 제외) · CLI traceback 노출.
    # 폭격 직후의 429/503 은 **설계된 정상 동작**이다 — 1200건을 쏟아붓고 수십 건을 중간에 끊으면
    # 버려진 요청들이 queue_timeout_s 로 풀리기 전까지 대기열에 남아 있고, 그 동안 서버는 정직하게 거절한다.
    # 그래서 '폭격 뒤 정상 질의' 는 보고만 하고 판정에는 넣지 않는다(서버가 살아 있는지는 따로 본다).
    fatal = (not alive) or m.codes.get(500, 0) or any(f["kind"] in ("server_died", "cli_traceback", "connection") for f in m.findings)
    if not ok1:
        print("  참고: 폭격 직후 정상 질의가 %s 로 거절됐습니다 — 대기열이 빠지는 중이면 정상입니다." % code1)
    print("\nRESULT %s" % ("PROBLEMS" if fatal else "OK"))
    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main())
