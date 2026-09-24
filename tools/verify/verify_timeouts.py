"""**타임아웃과 실패 경로**를 실제로 일으켜서, 서버가 어떻게 버티는지 확인한다.

왜 따로 있나: 정상 경로는 기존 하네스가 다 본다. 정작 사람을 괴롭히는 것은 "LLM 이 안 돌아올 때",
"요청이 너무 오래 걸릴 때", "외부 RAG 가 죽어 있을 때" 같은 **실패 경로**인데, 이건 실패를
만들어 내야 확인할 수 있다. mock 프로바이더의 테스트 훅(`LLMWIKI_MOCK_DELAY_MS`,
`LLMWIKI_MOCK_FAIL`)으로 결정적으로 재현한다 — 진짜 LLM 으로는 재현도 반복도 안 된다.

여기서 지키려는 약속은 하나다: **한 요청의 실패가 다른 사용자에게 번지지 않는다.**
느린 LLM 하나가 서버를 멈추지 않고, 죽은 외부 소스가 질의를 실패시키지 않고,
폭주 뒤에도 다음 질의는 정상이어야 한다.

실행:
    python tools/verify/verify_timeouts.py
    python tools/verify/verify_timeouts.py --port 8907 --keep
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_buttons import isolated_env          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable

ROWS = []


def rec(ok, name, detail=""):
    ROWS.append({"ok": bool(ok), "name": name, "detail": str(detail)[:160]})
    print("%s %-50s %s" % ("OK  " if ok else "FAIL", name, str(detail)[:110]), flush=True)
    return bool(ok)


class Srv:
    """환경 변수를 바꿔 가며 서버를 다시 띄우기 위한 최소 래퍼."""

    def __init__(self, port, env, tmp):
        self.port, self.env, self.tmp, self.proc = port, env, tmp, None
        self.base = "http://127.0.0.1:%d" % port

    def start(self, **extra_env):
        env = dict(self.env)
        env.update({k: str(v) for k, v in extra_env.items()})
        for k in ("LLMWIKI_MOCK_DELAY_MS", "LLMWIKI_MOCK_FAIL"):
            if k not in extra_env:
                env.pop(k, None)
        self.proc = subprocess.Popen([PY, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(self.port)],
                                     cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(150):
            try:
                urllib.request.urlopen(self.base + "/api/auth/me", timeout=2)
                return True
            except Exception:
                time.sleep(0.5)
        return False

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.communicate(timeout=10)
            except Exception:
                self.proc.kill()
            self.proc = None

    def post(self, path, body, timeout=300):
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8"),
                                   headers={"Content-Type": "application/json", "X-Requested-With": "llmwiki"}, method="POST")
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw.decode("utf-8"))
            except Exception:
                return e.code, {"raw": raw[:200].decode("utf-8", "ignore")}
        except Exception as e:
            return 0, {"err": str(e)[:200]}

    def get(self, path, timeout=60):
        try:
            with urllib.request.urlopen(self.base + path, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8"))
            except Exception:
                return e.code, {}
        except Exception as e:
            return 0, {"err": str(e)[:200]}


def stages(trace):
    out = []

    def walk(n):
        out.append(n)
        for c in n.get("children") or []:
            walk(c)
    walk(trace or {})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="타임아웃·실패 경로 내성 검증")
    ap.add_argument("--port", type=int, default=8907)
    ap.add_argument("--keep", action="store_true")
    ns = ap.parse_args(argv)

    tmp, env, proc0 = isolated_env(ns.port, serve=False)
    srv = Srv(ns.port, env, tmp)
    Q = "ISSUE-2001 의 원인과 수정 CL 은?"
    try:
        # ================= 1. LLM 이 몇 번 실패했다가 성공 → 재시도로 살아난다 =================
        print("\n[1] LLM 일시 실패 → 재시도로 성공")
        if not srv.start(LLMWIKI_MOCK_FAIL="timeout:2"):
            return rec(False, "서버 기동") or 1
        st, j = srv.post("/api/query", {"q": Q, "log": True})
        res = (j or {}).get("result") or {}
        rec(st == 200 and bool(res.get("answer")), "일시 실패 뒤 답변이 나온다", "HTTP %s · 답변 %d자" % (st, len(res.get("answer") or "")))
        rec(res.get("answer_mode") == "llm", "재시도가 성공해 LLM 답변으로 끝났다", "mode=%s" % res.get("answer_mode"))
        srv.stop()

        # ================= 1.5 timeout·retry 손잡이가 세 창구에서 **같은 뜻인가** (2026-09-19) =================
        # 왜: 기존 검사는 "서버가 재시도하고 버틴다" 는 **동작**만 봤다. 운영자가 실제로 묻는 것은
        # "그 값을 Web·CLI·MCP 어디서 보고 바꿀 수 있나, 그리고 셋이 같은 뜻인가" 다.
        # LLM 을 **항상 실패**시키면 incident 에 `max_attempts` 가 남는다 = 1 + retries.
        # 같은 overrides 를 Web 과 MCP 로 보내 그 숫자가 같은지 본다 — 숫자가 다르면 같은 손잡이가 아니다.
        print("\n[1.5] timeout·retry 손잡이의 세 창구 정합")
        # 회로 차단은 (프로바이더, 모델) 단위로 **요청을 넘어** 공유된다 — 첫 질의의 실패로 회로가 열리면
        # 다음 질의는 시도조차 하지 않아(max_attempts=0) 재시도 횟수를 잴 수 없다. 여기서는 재시도만 보고 싶으므로
        # 회로를 사실상 끈다(§2 가 회로 차단 자체를 따로 확인한다).
        if not srv.start(LLMWIKI_MOCK_FAIL="timeout", LLMWIKI_LLM_CIRCUIT_FAILURES="100000",
                         LLMWIKI_LLM_RETRY_BACKOFF_S="0.01", LLMWIKI_LLM_RETRY_BACKOFF_MAX_S="0.05"):
            return rec(False, "서버 기동") or 1

        def attempts_web(retries):
            _st, _j = srv.post("/api/query", {"q": Q, "log": False, "overrides": {"llm_retries": retries}})
            fs = (((_j or {}).get("result") or {}).get("llm_report") or {}).get("failures") or []
            ans = [f.get("max_attempts") for f in fs if f.get("role") == "answer"]
            return ans[0] if ans else None

        def attempts_mcp(retries):
            _st, _j = srv.post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                        "params": {"name": "wiki_query",
                                                   "arguments": {"question": Q, "overrides": {"llm_retries": retries}}}})
            sc = ((_j or {}).get("result") or {}).get("structuredContent") or {}
            fs = (sc.get("llm_report") or {}).get("failures") or []
            ans = [f.get("max_attempts") for f in fs if f.get("role") == "answer"]
            return ans[0] if ans else None

        w0, w2 = attempts_web(0), attempts_web(2)
        rec(w0 == 1 and w2 == 3, "Web: overrides.llm_retries 가 실제 시도 횟수를 바꾼다", "retries=0→%s · retries=2→%s (기대 1·3)" % (w0, w2))
        m0, m2 = attempts_mcp(0), attempts_mcp(2)
        rec(m0 == 1 and m2 == 3, "MCP: 같은 overrides 가 같은 시도 횟수를 만든다", "retries=0→%s · retries=2→%s" % (m0, m2))
        rec(w0 == m0 and w2 == m2, "Web 과 MCP 가 **같은 값**을 돌려준다", "web=(%s,%s) mcp=(%s,%s)" % (w0, w2, m0, m2))
        # MCP 응답에 실패 보고가 실려야 붙은 LLM 이 원인을 읽는다 (예전에는 기본 모드에 structuredContent 가 없었다)
        _st, _j = srv.post("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                    "params": {"name": "wiki_query", "arguments": {"question": Q}}})
        msc = ((_j or {}).get("result") or {}).get("structuredContent") or {}
        rec(bool((msc.get("llm_report") or {}).get("summary")), "MCP 구조화 결과에 llm_report 가 실린다",
            "; ".join(((msc.get("llm_report") or {}).get("summary") or []))[:80])
        # 역할 단위 정책도 같은 이름이어야 한다 (llm_roles.<role>.timeout_s / retries)
        _st, _j = srv.post("/api/query", {"q": Q, "log": False, "overrides": {"llm_roles": {"answer": {"retries": 1}}}})
        fs = (((_j or {}).get("result") or {}).get("llm_report") or {}).get("failures") or []
        rr = [f.get("max_attempts") for f in fs if f.get("role") == "answer"]
        rec(rr and rr[0] == 2, "역할 단위 llm_roles.answer.retries 도 걸린다", "max_attempts=%s (기대 2)" % (rr[0] if rr else None))
        # timeout 은 incident 에 그대로 남는다 — 세 창구가 같은 초 단위를 쓴다는 증거
        _st, _j = srv.post("/api/query", {"q": Q, "log": False, "overrides": {"llm_timeout": 7}})
        fs = (((_j or {}).get("result") or {}).get("llm_report") or {}).get("failures") or []
        ts = [f.get("timeout_s") for f in fs if f.get("role") == "answer"]
        rec(ts and abs(float(ts[0] or 0) - 7) < 0.01, "overrides.llm_timeout 이 incident 의 timeout_s 로 보인다", "timeout_s=%s" % (ts[0] if ts else None))
        # 같은 손잡이가 **철자에 따라 권한이 다르면** 안 된다 (전역 llm_circuit_failures 는 admin, 역할 철자도 admin)
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from llmwiki import auth as _auth
        pairs = [("llm_circuit_failures", "circuit_failures"), ("llm_circuit_cooldown_s", "circuit_cooldown_s"),
                 ("llm_timeout", "timeout_s"), ("llm_retries", "retries"), ("llm_budget_s", "budget_s")]
        bad = [(g, r) for g, r in pairs
               if (g in _auth.OVERRIDE_SAFE_KEYS) != (r in _auth.OVERRIDE_ROLE_ATTRS)]
        rec(not bad, "전역 철자와 역할 철자의 권한 등급이 같다", "어긋남: %s" % (bad or "없음"))

        # --- CLI 축: `query --set` 이 Web/MCP 의 overrides 와 같은 길인가 ---
        cenv = dict(env, LLMWIKI_MOCK_FAIL="timeout", LLMWIKI_LLM_CIRCUIT_FAILURES="100000",
                    LLMWIKI_LLM_RETRY_BACKOFF_S="0.01", LLMWIKI_LLM_RETRY_BACKOFF_MAX_S="0.05",
                    PYTHONIOENCODING="utf-8")

        def attempts_cli(retries):
            r = subprocess.run([PY, "-m", "llmwiki", "query", Q, "--no-log", "--json", "--set", "llm_retries=%d" % retries],
                               cwd=ROOT, env=cenv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            try:
                d = json.loads(r.stdout[r.stdout.index("{"):])
            except Exception:
                return None
            fs = ((d.get("result") or {}).get("llm_report") or {}).get("failures") or []
            a = [f.get("max_attempts") for f in fs if f.get("role") == "answer"]
            return a[0] if a else None
        c0, c2 = attempts_cli(0), attempts_cli(2)
        rec(c0 == 1 and c2 == 3, "CLI: `query --set llm_retries=` 가 같은 시도 횟수를 만든다", "retries=0→%s · retries=2→%s" % (c0, c2))
        rec(c0 == w0 and c2 == w2 and c0 == m0 and c2 == m2,
            "**세 창구가 같은 값** (Web=CLI=MCP)", "web=(%s,%s) cli=(%s,%s) mcp=(%s,%s)" % (w0, w2, c0, c2, m0, m2))
        # 권한이 낮은 실행자는 CLI 에서도 금지 키를 못 쓴다 (Web/MCP 와 같은 화이트리스트)
        # security.json 을 잠깐 바꿔 본다 — **원본을 그대로 되돌린다**. 되돌리지 않으면 뒤 구간이
        # 401 을 받아 엉뚱한 실패로 보인다 (2026-09-19 이 검사를 넣다가 실제로 겪었다).
        sec_p = os.path.join(tmp, "security.json")
        _orig_sec = None
        if os.path.exists(sec_p):
            with open(sec_p, "rb") as _f:
                _orig_sec = _f.read()
        try:
            _sec = json.loads(_orig_sec.decode("utf-8")) if _orig_sec else {}
        except Exception:
            _sec = {}
        _sec.setdefault("cli", {})["default_role"] = "viewer"
        with open(sec_p, "w", encoding="utf-8") as _f:
            json.dump(_sec, _f, ensure_ascii=False)
        try:
            r = subprocess.run([PY, "-m", "llmwiki", "query", "x", "--no-log", "--set", "openai_base_url=http://attacker/v1"],
                               cwd=ROOT, env=cenv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            out = (r.stdout or "") + (r.stderr or "")
            rec(r.returncode == 5 and "허용되지 않는 키" in out, "CLI: 낮은 역할은 금지 키를 못 쓴다 (Web/MCP 와 같은 이유 메시지)",
                "code=%s %s" % (r.returncode, out.strip().splitlines()[0][:90] if out.strip() else ""))
        finally:
            if _orig_sec is None:
                if os.path.exists(sec_p):
                    os.remove(sec_p)
            else:
                with open(sec_p, "wb") as _f:
                    _f.write(_orig_sec)
        srv.stop()

        # ================= 2. LLM 이 계속 실패 → 500 이 아니라 추출식 답변 + 실패 보고 =================
        print("\n[2] LLM 계속 실패 → 서비스는 계속된다 (추출식 대체 + 보고)")
        if not srv.start(LLMWIKI_MOCK_FAIL="timeout"):
            return rec(False, "서버 기동") or 1
        st, j = srv.post("/api/query", {"q": Q, "log": True})
        res = (j or {}).get("result") or {}
        rec(st == 200, "500 이 아니라 200 으로 응답한다", "HTTP %s" % st)
        rec(bool(res.get("answer")), "근거 기반 답변이 여전히 나온다", "mode=%s · %d자" % (res.get("answer_mode"), len(res.get("answer") or "")))
        rec(res.get("answer_mode") != "llm", "LLM 답변이 아니라 대체 경로로 표시된다", "mode=%s" % res.get("answer_mode"))
        lr = res.get("llm_report") or {}
        rec(bool(lr.get("summary")), "무엇이 왜 실패했는지 보고가 붙는다", "; ".join(lr.get("summary") or [])[:90])
        # 회로 차단: 연속 실패가 쌓이면 이후 호출은 기다리지 않고 바로 건너뛴다
        t0 = time.time()
        st2, j2 = srv.post("/api/query", {"q": Q + " (2)", "log": True})
        dt = time.time() - t0
        res2 = (j2 or {}).get("result") or {}
        rec(st2 == 200, "연속 실패 뒤에도 질의가 처리된다", "HTTP %s · %.1fs" % (st2, dt))
        inc = json.dumps((res2.get("llm_report") or {}), ensure_ascii=False)
        rec("circuit" in inc or dt < 30, "회로 차단 또는 빠른 실패로 시간을 낭비하지 않는다", "%.1fs" % dt)
        # 건강 상태는 여전히 조회 가능해야 한다 (관리자가 상황을 볼 수 있어야 한다)
        sh, hj = srv.get("/api/health?quick=1")
        rec(sh == 200, "LLM 이 죽어 있어도 health 는 응답한다", "HTTP %s ok=%s" % (sh, (hj or {}).get("ok")))
        srv.stop()

        # ================= 3. 느린 LLM: 동시 사용자 · 취소 · 회복 =================
        print("\n[3] 느린 LLM (호출마다 2초) — 동시 사용자 · 취소 · 회복")
        if not srv.start(LLMWIKI_MOCK_DELAY_MS="2000"):
            return rec(False, "서버 기동") or 1
        # (a) 느린 요청이 도는 동안에도 가벼운 조회는 즉시 응답해야 한다 (읽기 락에 갇히지 않는다)
        slow = {}

        def run_slow():
            slow["st"], slow["j"] = srv.post("/api/query", {"q": Q + " (느림)", "log": True, "progress_token": "tmo-slow-1"})
        th = threading.Thread(target=run_slow, daemon=True)
        th.start()
        time.sleep(2.0)
        t0 = time.time()
        sa, _ = srv.get("/api/activity")
        sp, _ = srv.get("/api/progress?token=tmo-slow-1")
        gap = time.time() - t0
        rec(sa == 200 and sp == 200 and gap < 5, "느린 질의 중에도 활동·진행 조회가 막히지 않는다", "%.2fs" % gap)
        th.join(timeout=180)
        rec(slow.get("st") == 200, "느린 질의도 결국 정상 응답", "HTTP %s" % slow.get("st"))

        # (b) 진행 중 취소 → 499, 그리고 서버는 멀쩡
        cancelled = {}

        def run_cancel():
            cancelled["st"], cancelled["j"] = srv.post("/api/query", {"q": Q + " (취소)", "log": True, "progress_token": "tmo-cancel-1"})
        th2 = threading.Thread(target=run_cancel, daemon=True)
        th2.start()
        time.sleep(2.5)
        cs, cj = srv.post("/api/activity", {"action": "cancel", "token": "tmo-cancel-1", "reason": "timeout-test"})
        th2.join(timeout=180)
        rec(cs == 200 and (cj or {}).get("ok"), "진행 중 요청에 취소가 접수된다", json.dumps(cj, ensure_ascii=False)[:80])
        rec(cancelled.get("st") in (499, 200), "취소된 요청이 깔끔히 끝난다 (499 또는 완료)", "HTTP %s" % cancelled.get("st"))
        st, j = srv.post("/api/query", {"q": Q + " (취소 뒤)", "log": True})
        rec(st == 200 and ((j or {}).get("result") or {}).get("answer"), "취소 직후 다음 질의가 정상", "HTTP %s" % st)
        srv.stop()

        # ================= 4. 대기열·동시 상한: 넘치면 거절하되 서버는 산다 =================
        print("\n[4] 동시 요청 폭주 — 거절은 하되 죽지 않는다")
        srvjson = os.path.join(tmp, "server.json")
        cfg = {}
        if os.path.exists(srvjson):
            try:
                cfg = json.load(open(srvjson, encoding="utf-8"))
            except Exception:
                cfg = {}
        cfg.setdefault("concurrency", {})
        cfg["concurrency"].update({"max_parallel_reads": 2, "queue_max": 3, "queue_timeout_s": 5})
        cfg.setdefault("rate_limit", {}).update({"per_user_per_min": 10000, "per_ip_per_min": 10000})
        json.dump(cfg, open(srvjson, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if not srv.start(LLMWIKI_MOCK_DELAY_MS="1500"):
            return rec(False, "서버 기동") or 1
        results = []
        lock = threading.Lock()

        def hit(i):
            s, _ = srv.post("/api/query", {"q": "%s (동시 %d)" % (Q, i), "log": False}, timeout=120)
            with lock:
                results.append(s)
        ths = [threading.Thread(target=hit, args=(i,), daemon=True) for i in range(12)]
        t0 = time.time()
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=180)
        took = time.time() - t0
        ok200 = sum(1 for s in results if s == 200)
        rej = sum(1 for s in results if s in (429, 503))
        err = sum(1 for s in results if s not in (200, 429, 503))
        rec(err == 0, "5xx(예상 밖 오류) 없이 처리되거나 거절된다", "200=%d 거절=%d 그밖=%d (%.0fs)" % (ok200, rej, err, took))
        rec(ok200 >= 2, "상한 안에서는 실제로 처리된다", "200 %d건" % ok200)
        sh, hj = srv.get("/api/health?quick=1")
        rec(sh == 200, "폭주 뒤에도 서버가 살아 있다", "health HTTP %s" % sh)
        st, j = srv.post("/api/query", {"q": Q + " (폭주 뒤)", "log": True}, timeout=180)
        rec(st == 200 and ((j or {}).get("result") or {}).get("answer"), "폭주 뒤 정상 질의가 다시 된다", "HTTP %s" % st)
        srv.stop()

        # ================= 5. 죽은 외부 RAG 소스: 질의는 계속된다 =================
        print("\n[5] 외부 RAG 소스가 죽어 있을 때")
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        dead_port = dead.getsockname()[1]
        dead.close()                       # 아무도 듣지 않는 포트
        srcs_path = os.path.join(tmp, "mcp_sources.json")
        srcs = {}
        if os.path.exists(srcs_path):
            try:
                srcs = json.load(open(srcs_path, encoding="utf-8"))
            except Exception:
                srcs = {}
        srcs["deadbox"] = {"enabled": True, "transport": "http", "url": "http://127.0.0.1:%d/mcp" % dead_port,
                           "timeout_s": 3, "retrieve": {"tool": "search", "args": {"q": "{query}"}},
                           "weight": 1.0, "note": "타임아웃 검증용 — 아무도 듣지 않는 포트"}
        json.dump(srcs, open(srcs_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        cfgp = os.path.join(tmp, "config.json")
        c = json.load(open(cfgp, encoding="utf-8"))
        c["toggles"] = dict(c.get("toggles") or {}, external_rag=True)
        json.dump(c, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if not srv.start():
            return rec(False, "서버 기동") or 1
        t0 = time.time()
        st, j = srv.post("/api/query", {"q": Q, "log": True}, timeout=180)
        took = time.time() - t0
        res = (j or {}).get("result") or {}
        rec(st == 200 and bool(res.get("answer")), "죽은 외부 소스가 있어도 답이 나온다", "HTTP %s · %.1fs" % (st, took))
        ext = [s for s in stages((j or {}).get("trace")) if s.get("name") == "external_rag"]
        noted = any("error" in json.dumps(s.get("meta") or {}, ensure_ascii=False) for s in ext)
        rec(noted or not ext, "실패한 소스가 trace 에 이유와 함께 남는다", json.dumps((ext[0].get("meta") if ext else {}), ensure_ascii=False)[:100])
        rec(took < 90, "죽은 소스가 질의를 무한정 붙들지 않는다", "%.1fs" % took)
        sm, mj = srv.post("/api/mcp_sources", {"action": "test", "source": "deadbox"}, timeout=60)
        rec(sm in (200, 400, 403), "소스 점검이 예외로 죽지 않고 결과를 돌려준다", "HTTP %s" % sm)
        srv.stop()

        # ================= 6. MCP 경로도 같은 성질인가 =================
        print("\n[6] MCP 경로에서의 실패 내성")
        if not srv.start(LLMWIKI_MOCK_FAIL="timeout"):
            return rec(False, "서버 기동") or 1
        st, j = srv.post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "wiki_query", "arguments": {"q": Q}}}, timeout=180)
        rec(st == 200 and "result" in (j or {}), "LLM 이 죽어도 MCP 도구는 결과를 돌려준다", "HTTP %s keys=%s" % (st, sorted((j or {}).keys())))
        st2, j2 = srv.post("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, timeout=60)
        rec(st2 == 200 and (j2 or {}).get("result"), "도구 목록은 영향을 받지 않는다", "HTTP %s" % st2)
        srv.stop()

        # ================= 7. 빌드 관련 제한은 48시간, 그리고 화면이 그 값을 읽어 온다 =================
        # 빌드가 시간 제한에 끊기는 사고는 몇 시간 뒤에야 드러나므로 재현이 어렵다. 값 자체를 계약으로 고정한다.
        print("\n[7] 빌드 시간 제한(48시간) 과 /api/limits")
        H48 = 172800
        if not srv.start():
            return rec(False, "서버 기동") or 1
        sl, lj = srv.get("/api/limits")
        rec(sl == 200 and isinstance(lj, dict) and lj.get("stages"), "GET /api/limits 가 단계별 제한을 준다",
            "HTTP %s · 단계 %d개" % (sl, len((lj or {}).get("stages") or {})))
        tr = (lj or {}).get("trace") or {}
        rec(bool(tr.get("answer_llm")) and bool(tr.get("fts_search")),
            "trace 노드 이름으로 제한을 찾을 수 있다 (실측 ms 옆 '≤ 제한')",
            "answer_llm=%s" % json.dumps((tr.get("answer_llm") or [{}])[0].get("label"), ensure_ascii=False))
        bl = {x["key"]: x["s"] for x in ((lj or {}).get("flows") or {}).get("build") or []}
        for key in ("timeouts.job_s", "concurrency.write_wait_timeout_s", "build_lock_timeout", "build_lock_stale_s"):
            v = bl.get(key)
            rec(v is not None and (v == 0 or v >= H48), "빌드 제한 %s 가 48시간 이상(또는 무제한)" % key, "%s초" % v)
        # 배포되는 파일에도 같은 값이 들어 있어야 한다 (코드 기본값만 고쳐 두면 복사해 간 폴더에서 어긋난다)
        for rel, path, want in (("server.json", ("timeouts", "job_s"), H48),
                                ("server.json", ("concurrency", "write_wait_timeout_s"), H48),
                                ("setup/server.example.json", ("timeouts", "job_s"), H48),
                                ("config.json", ("build_lock_timeout",), H48),
                                ("config.json", ("build_lock_stale_s",), H48),
                                ("setup/config.example.json", ("build_lock_stale_s",), H48)):
            try:
                d = json.load(open(os.path.join(ROOT, rel.replace("/", os.sep)), encoding="utf-8"))
                for p in path:
                    d = d[p]
            except Exception as e:
                d = "읽기 실패: %s" % e
            rec(d == want or d == 0, "%s %s = %d" % (rel, ".".join(path), want), "%s" % d)
        # read_wait_timeout_s 는 **질의**의 수명이므로 같이 올리면 안 된다 (전체 재빌드 동안 스레드가 쌓인다)
        rw = ((json.load(open(os.path.join(ROOT, "server.json"), encoding="utf-8")).get("concurrency") or {})
              .get("read_wait_timeout_s"))
        rec(rw is not None and 0 < float(rw) <= 3600,
            "read_wait_timeout_s 는 짧게 유지된다 (빌드가 아니라 질의의 수명)", "%s초" % rw)
        srv.stop()

        bad = [r for r in ROWS if not r["ok"]]
        print("\n검사 %d개 중 %d개 통과 · 실패 %d개" % (len(ROWS), len(ROWS) - len(bad), len(bad)))
        for r in bad:
            print("  FAIL %-50s %s" % (r["name"], r["detail"]))
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_timeouts_result.json")
        json.dump(ROWS, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("결과: %s" % out)
        print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
        return 1 if bad else 0
    finally:
        srv.stop()
        if tmp and not ns.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
