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
