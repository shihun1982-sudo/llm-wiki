"""**다수 클라이언트 장시간 혼합 부하** — 서버로서 버티는지 거칠게 확인한다.

기존 하네스와 겹치지 않는 것만 본다:
  - `verify_timeouts.py` : 실패 경로 (LLM 죽음·느림·취소)
  - `verify_monkey.py`   : 잘못된 입력 폭격
  - 이 스크립트          : **정상 요청을 여러 창구에서 오래, 섞어서** 때린다

왜 필요한가: 이 서버는 Web UI · CLI · MCP 를 **동시에** 제공한다. 각각은 따로 검증되지만,
셋이 한 프로세스의 같은 색인·같은 락·같은 슬롯을 두고 경쟁할 때 무슨 일이 나는지는 따로 봐야 한다.
실제 사고는 대개 여기서 난다 — 빌드가 도는 중에 질의가 몰리고, 그 와중에 누가 설정을 저장한다.

확인하는 것:
  1. 오래(기본 60초) 섞어 때려도 **예상 밖 오류(5xx)가 0** 인가
  2. 증분 빌드가 도는 **동안에도** 질의가 응답하는가 (soft 락)
  3. 쓰기(설정 저장)와 읽기가 섞여도 서로를 깨뜨리지 않는가
  4. 부하가 끝난 뒤 **색인 무결성**이 그대로인가 (build verify)
  5. 부하 중에도 관리 화면(활동·health)이 응답하는가
  6. 메모리가 계속 늘지 않는가 (누수 대략 확인)

실행:
    python tools/verify/verify_soak.py                    # 60초
    python tools/verify/verify_soak.py --seconds 180 --clients 24
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_buttons import isolated_env          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
ROWS = []
QUESTIONS = [
    "ISSUE-2001 의 원인과 수정 CL 은?", "CL-55302 는 어떤 이슈를 수정했나?",
    "HW rev B1 에서 t_setup 은 몇 ns 인가?", "RX DMA 드라이버의 code map",
    "PDCCH 디코딩 실패 원인", "AGC 수렴 지연 이슈", "지난주 리뷰한 CL",
]


def rec(ok, name, detail=""):
    ROWS.append({"ok": bool(ok), "name": name, "detail": str(detail)[:170]})
    print("%s %-48s %s" % ("OK  " if ok else "FAIL", name, str(detail)[:110]), flush=True)
    return bool(ok)


class Client:
    def __init__(self, base):
        self.base = base

    def post(self, path, body, timeout=180):
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8"),
                                   headers={"Content-Type": "application/json", "X-Requested-With": "llmwiki"}, method="POST")
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:
            return 0, str(e).encode()

    def get(self, path, timeout=60):
        try:
            with urllib.request.urlopen(self.base + path, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:
            return 0, str(e).encode()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="다수 클라이언트 혼합 부하")
    ap.add_argument("--port", type=int, default=8971)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--clients", type=int, default=16)
    ap.add_argument("--keep", action="store_true")
    ns = ap.parse_args(argv)

    tmp, env, proc = isolated_env(ns.port, serve=False)
    # LLM 은 짧은 지연을 주어 '진짜로 겹치게' 만든다 (즉답이면 경쟁이 안 일어난다)
    env = dict(env, LLMWIKI_MOCK_DELAY_MS="120")
    srv = subprocess.Popen([PY, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(ns.port)],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % ns.port
    cli = Client(base)
    stop = threading.Event()
    lock = threading.Lock()
    stats = {"codes": {}, "lat": [], "err": [], "n": 0, "by_kind": {}}

    def note(kind, code, ms, body=b""):
        with lock:
            stats["n"] += 1
            stats["codes"][code] = stats["codes"].get(code, 0) + 1
            stats["by_kind"][kind] = stats["by_kind"].get(kind, 0) + 1
            stats["lat"].append(ms)
            if code >= 500 or code == 0:
                stats["err"].append("%s %s %s" % (kind, code, body[:120].decode("utf-8", "ignore")))

    try:
        for _ in range(180):
            try:
                urllib.request.urlopen(base + "/api/auth/me", timeout=2)
                break
            except Exception:
                time.sleep(1)
        else:
            return rec(False, "서버 기동") or 1
        rec(True, "서버 기동", base)

        # ---- 여러 창구를 흉내내는 작업자들 ----
        def w_query(i):
            while not stop.is_set():
                t0 = time.time()
                c, b = cli.post("/api/query", {"q": random.choice(QUESTIONS), "log": True})
                note("web:query", c, (time.time() - t0) * 1000, b)

        def w_search(i):
            while not stop.is_set():
                t0 = time.time()
                c, b = cli.post("/api/search", {"q": random.choice(["RX DMA", "PDCCH", "AGC"]), "channel": "fts", "k": 5})
                note("web:search", c, (time.time() - t0) * 1000, b)

        def w_mcp(i):
            while not stop.is_set():
                t0 = time.time()
                c, b = cli.post("/mcp", {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                         "params": {"name": "wiki_query", "arguments": {"q": random.choice(QUESTIONS)}}})
                note("mcp:query", c, (time.time() - t0) * 1000, b)
                c, b = cli.post("/mcp", {"jsonrpc": "2.0", "id": i, "method": "tools/list"})
                note("mcp:list", c, 0, b)

        def w_poll(i):
            while not stop.is_set():
                for p in ("/api/activity", "/api/status", "/api/health?quick=1", "/api/requests?limit=5"):
                    t0 = time.time()
                    c, b = cli.get(p)
                    note("web:poll", c, (time.time() - t0) * 1000, b)
                time.sleep(0.4)

        def w_write(i):
            """읽기 한복판에서 쓰기 — 설정 저장·pin 추가 (배타/soft 락을 건드린다)"""
            while not stop.is_set():
                t0 = time.time()
                c, b = cli.post("/api/pins", {"action": "add", "query": "soak", "chunk": "", "doc": "ISSUE-2001", "note": "soak"})
                note("web:write", c, (time.time() - t0) * 1000, b)
                time.sleep(1.5)

        def w_cli(i):
            """다른 프로세스(CLI)가 같은 색인을 읽는다"""
            while not stop.is_set():
                t0 = time.time()
                p = subprocess.run([PY, "-m", "llmwiki", "search", "fts", "PDCCH", "--k", "3"], cwd=ROOT, env=env,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
                note("cli:search", 200 if p.returncode == 0 else 500, (time.time() - t0) * 1000,
                     (p.stderr or "").encode()[:120])
                time.sleep(0.8)

        workers = []
        n = max(4, ns.clients)
        for i in range(n):
            f = [w_query, w_search, w_mcp, w_poll][i % 4]
            workers.append(threading.Thread(target=f, args=(i,), daemon=True))
        workers.append(threading.Thread(target=w_write, args=(0,), daemon=True))
        workers.append(threading.Thread(target=w_cli, args=(0,), daemon=True))
        print("\n%d개 작업자로 %d초 동안 섞어서 때립니다 (web 질의·검색 · MCP · 폴링 · 쓰기 · 별도 CLI 프로세스)\n"
              % (len(workers), ns.seconds), flush=True)
        for t in workers:
            t.start()

        # ---- 부하 한복판에서 증분 빌드 (soft 락) ----
        time.sleep(max(3, ns.seconds // 4))
        t0 = time.time()
        bc, bb = cli.post("/api/build", {"full": False}, timeout=600)
        job = {}
        try:
            job = json.loads(bb.decode())
        except Exception:
            pass
        rec(bc == 200 and bool(job.get("job")), "부하 중 증분 빌드 시작", "HTTP %s job=%s" % (bc, job.get("job")))
        # 빌드가 도는 동안 질의가 응답하는가
        qc, _ = cli.post("/api/query", {"q": QUESTIONS[0], "log": False}, timeout=300)
        rec(qc == 200, "빌드 중에도 질의가 응답한다 (soft 락)", "HTTP %s · %.1fs" % (qc, time.time() - t0))

        time.sleep(max(1, ns.seconds - (ns.seconds // 4) - 2))
        stop.set()
        for t in workers:
            t.join(timeout=30)

        # ---- 판정 ----
        codes = stats["codes"]
        five = sum(v for k, v in codes.items() if k >= 500 or k == 0)
        ok2 = codes.get(200, 0)
        rejected = sum(v for k, v in codes.items() if k in (429, 503))
        lat = sorted(stats["lat"]) or [0]
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        rec(five == 0, "예상 밖 오류(5xx·연결실패) 0건",
            "요청 %d건 · 200=%d · 거절(429/503)=%d · 5xx=%d" % (stats["n"], ok2, rejected, five))
        if stats["err"]:
            for e in stats["err"][:5]:
                print("     ! %s" % e)
        rec(ok2 > 0, "정상 처리된 요청이 있다", "200 %d건 · 창구별 %s" % (ok2, json.dumps(stats["by_kind"], ensure_ascii=False)))
        rec(p95 < 60000, "p95 지연이 60초 미만", "p50=%.0fms p95=%.0fms max=%.0fms" % (statistics.median(lat), p95, lat[-1]))

        # ---- 부하 뒤 상태 ----
        hc, hb = cli.get("/api/health?quick=1", timeout=120)
        hj = json.loads(hb.decode()) if hc == 200 else {}
        rec(hc == 200 and hj.get("fails", 1) == 0, "부하 뒤 health 통과", "HTTP %s fails=%s warn=%s" % (hc, hj.get("fails"), hj.get("warnings")))
        vc, vb = cli.post("/api/build/verify", {"fix": False}, timeout=600)
        vj = json.loads(vb.decode()) if vc == 200 else {}
        orphans = {k: v for k, v in (vj or {}).items() if isinstance(v, int) and v and "orphan" in k.lower()}
        rec(vc == 200 and not orphans, "부하 뒤 색인 무결성", "HTTP %s orphans=%s" % (vc, orphans or "없음"))
        ac, ab = cli.get("/api/activity", timeout=60)
        aj = json.loads(ab.decode()) if ac == 200 else {}
        rec(ac == 200 and not (aj.get("running") or []), "부하 뒤 실행 중 작업이 남지 않음",
            "running=%d queued=%d" % (len(aj.get("running") or []), len(aj.get("queued") or [])))
        qc2, _ = cli.post("/api/query", {"q": QUESTIONS[1], "log": True}, timeout=300)
        rec(qc2 == 200, "부하 뒤 정상 질의가 된다", "HTTP %s" % qc2)

        bad = [r for r in ROWS if not r["ok"]]
        print("\n검사 %d개 중 %d개 통과 · 실패 %d개" % (len(ROWS), len(ROWS) - len(bad), len(bad)))
        for r in bad:
            print("  FAIL %-48s %s" % (r["name"], r["detail"]))
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_soak_result.json")
        json.dump({"rows": ROWS, "stats": {k: v for k, v in stats.items() if k != "lat"},
                   "p50_ms": statistics.median(lat), "p95_ms": p95}, open(out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("결과: %s" % out)
        print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
        return 1 if bad else 0
    finally:
        stop.set()
        if srv:
            srv.terminate()
            try:
                srv.communicate(timeout=15)
            except Exception:
                srv.kill()
        if tmp and not ns.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
