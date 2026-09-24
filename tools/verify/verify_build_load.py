# -*- coding: utf-8 -*-
"""빌드 중에도 서비스가 되는가 — 30명 동시 접속 + 채널별 빌드 실측.

왜 이 하네스가 있나 (2026-09-19):
  "30명이 쓰는 중에 빌드를 돌려도 괜찮은가" 는 코드를 읽어서는 답이 안 나온다. 락 정책이
  빌드 종류마다 다르고(증분=soft, 전체·채널=exclusive), 읽기는 **쓰기가 기다리고만 있어도** 잠깐 막히며
  (writer preference), 대기가 길어지면 503 이 된다. 그래서 **실제로 30명을 붙여 놓고 빌드를 돌려** 잰다.

무엇을 재나 (빌드 종류마다)
  - 질의 성공률 · p50/p95/최대 지연 · 거절(503/429) 수
  - 빌드가 끝난 뒤 색인 무결성과 질의 정상 동작
  - 빌드 중 질의가 **오답이 아니라 대기**로 처리되는가 (부분 색인이 새어 나가지 않는가)

판정 기준 (이 저장소 기본 설정 기준)
  증분 빌드   : 질의 성공률 100% — 읽기를 막지 않는 것이 설계다
  채널/전체   : 질의가 **실패하지 않아야** 한다 (대기는 허용). 503 이 나오면 대기 한도가 짧은 것이다

사용법: python tools/verify/verify_build_load.py [--users 30] [--port 8975] [--seconds 12]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
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
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
sys.path.insert(0, ROOT)

rows = []


def check(name, ok, detail=""):
    rows.append({"name": name, "ok": bool(ok), "detail": str(detail)[:160]})
    print("%-4s %-46s %s" % ("OK" if ok else "FAIL", name, str(detail)[:120]))
    return bool(ok)


def _gen_corpus(d, n=40):
    os.makedirs(d, exist_ok=True)
    body = ("RX DMA 에서 underrun 이 발생하면 PHY 재시작이 실패한다. 원인은 클럭 게이팅 타이밍이며 "
            "CL-9%04d 에서 고쳤다. rev B1 에서 t_setup 은 4 ns 이고 잡음 여유는 1.5 dB 다. "
            "AGC 수렴 지연은 별도 이슈다. 레지스터 접근 순서 규칙을 지켜야 한다.\n")
    for i in range(n):
        with open(os.path.join(d, "d%03d.md" % i), "w", encoding="utf-8") as f:
            f.write("---\ndoc_type: issue\next_id: ISSUE-9%03d\n---\n\n# RX DMA underrun %d\n\n%s"
                    % (i, i, (body % i) * 8))


class Load:
    """N명이 계속 질의한다. 결과: 성공·거절·실패 수와 지연 분포."""

    def __init__(self, base, users):
        self.base, self.users = base, users
        self.stop = threading.Event()
        self.lat, self.ok, self.rejected, self.failed = [], 0, 0, 0
        self.samples = []          # (시작시각, 지연, 종류) — 빌드 구간만 따로 보려고
        self.errors = {}
        self._lk = threading.Lock()
        self.threads = []

    def _rec(self, t0, dt, kind, code=None):
        with self._lk:
            # 시작 시각을 함께 남긴다 — **빌드가 도는 구간만** 따로 보기 위해서다.
            # 전체 시나리오 평균은 빌드 전후의 한가한 시간에 희석되어 "빌드 중에 어땠나" 를 감춘다.
            self.samples.append((t0, dt, kind))
            self.lat.append(dt)
            if kind == "ok":
                self.ok += 1
            elif kind == "rejected":
                self.rejected += 1
            else:
                self.failed += 1
            if code is not None:
                self.errors[code] = self.errors.get(code, 0) + 1

    def _one(self, i):
        qs = ["RX DMA underrun 원인", "rev B1 t_setup", "AGC 수렴 지연", "레지스터 접근 순서"]
        k = 0
        while not self.stop.is_set():
            q = qs[(i + k) % len(qs)]
            k += 1
            t0 = time.time()
            try:
                req = urllib.request.Request(self.base + "/api/search", method="POST",
                                             data=json.dumps({"q": q, "channels": ["fts"], "k": 5}).encode("utf-8"),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=180).read()
                self._rec(t0, time.time() - t0, "ok")
            except urllib.error.HTTPError as e:
                self._rec(t0, time.time() - t0, "rejected" if e.code in (429, 503) else "failed", e.code)
            except Exception as e:
                self._rec(t0, time.time() - t0, "failed", type(e).__name__)
            time.sleep(0.05)

    def window(self, t_from, t_to):
        """빌드가 도는 동안 **시작된** 질의만 추려 통계를 낸다."""
        with self._lk:
            sel = [(dt, kind) for (ts, dt, kind) in self.samples if t_from <= ts <= t_to]
        lat = sorted(dt for dt, _k in sel)
        n_ok = sum(1 for _dt, k in sel if k == "ok")
        n_rej = sum(1 for _dt, k in sel if k == "rejected")
        n_fail = sum(1 for _dt, k in sel if k == "failed")
        return {"n": len(sel), "ok": n_ok, "rejected": n_rej, "failed": n_fail,
                "p50_ms": round(1000 * statistics.median(lat), 1) if lat else 0,
                "p95_ms": round(1000 * lat[int(len(lat) * 0.95)], 1) if lat else 0,
                "max_ms": round(1000 * lat[-1], 1) if lat else 0}

    def start(self):
        for i in range(self.users):
            th = threading.Thread(target=self._one, args=(i,), daemon=True)
            th.start()
            self.threads.append(th)

    def finish(self):
        self.stop.set()
        for th in self.threads:
            th.join(timeout=10)
        lat = sorted(self.lat)
        return {"ok": self.ok, "rejected": self.rejected, "failed": self.failed, "errors": dict(self.errors),
                "n": len(lat),
                "p50_ms": round(1000 * statistics.median(lat), 1) if lat else 0,
                "p95_ms": round(1000 * lat[int(len(lat) * 0.95)], 1) if lat else 0,
                "max_ms": round(1000 * lat[-1], 1) if lat else 0}


def post(base, path, payload, timeout=1800, cookie=""):
    h = {"Content-Type": "application/json"}
    if cookie:
        h["Cookie"] = cookie
    req = urllib.request.Request(base + path, method="POST",
                                 data=json.dumps(payload).encode("utf-8"), headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def _wait_job(base, jid, cookie, tries=2000):
    """비동기 작업(빌드)이 끝날 때까지. 반환: 오류 문자열 (없으면 "")."""
    for _ in range(tries):
        try:
            req = urllib.request.Request(base + "/api/jobs/" + str(jid), headers={"Cookie": cookie} if cookie else {})
            j = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
        except Exception as e:
            return "job 조회 실패: %s" % str(e)[:60]
        if j.get("status") != "running":
            return "" if j.get("status") in ("done", "ok", "finished") else "job status=%s %s" % (
                j.get("status"), str(j.get("error"))[:70])
        time.sleep(0.3)
    return "job timeout"


def post_raw(base, path, payload, timeout=1800, cookie=""):
    """상태코드와 헤더까지 — 로그인·확인 게이트를 다루려면 필요하다."""
    h = {"Content-Type": "application/json"}
    if cookie:
        h["Cookie"] = cookie
    req = urllib.request.Request(base + path, method="POST",
                                 data=json.dumps(payload).encode("utf-8"), headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read().decode("utf-8")), r.headers
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {}
        return e.code, body, e.headers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=30)
    ap.add_argument("--port", type=int, default=8975)
    ap.add_argument("--docs", type=int, default=40)
    ap.add_argument("--warm", type=float, default=2.0, help="빌드 전 부하만 거는 시간(초)")
    ap.add_argument("--policy", default="", choices=["", "incremental", "always", "never"],
                    help="server.json 의 concurrency.reads_during_build 를 바꿔서 잰다 "
                         "(기본 incremental = 증분만 읽기 허용 · always = 전체 리빌드 중에도 읽기 허용)")
    ns = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="lwbload_")
    corpus = os.path.join(tmp, "corpus")
    _gen_corpus(corpus, ns.docs)
    cfg = os.path.join(tmp, "config.json")
    shutil.copyfile(os.path.join(ROOT, "setup", "config.example.json"), cfg)
    with open(cfg, encoding="utf-8") as f:
        d = json.load(f)
    d.update({"corpus_dirs": [corpus], "data_dir": os.path.join(tmp, "data"), "wiki_dir": os.path.join(tmp, "wiki"),
              "embed_provider": "hash", "embed_model": "", "embed_dim": 64,
              "llm_provider": "mock", "llm_model": "mock",
              "toggles": dict(d.get("toggles") or {}, llm_graph=False, community_summary=False,
                              health_check=False, query_cache=False)})
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)

    # 관리자 계정을 만든다 — 채널/전체 리빌드는 **admin + 확인 문구 + 비밀번호 재입력** 뒤에만 돈다.
    # (이 게이트가 있다는 것 자체가 "30명이 쓰는 중에 실수로 리빌드가 돌지 않는다" 의 근거다.)
    sec_path = os.path.join(tmp, "security.json")
    shutil.copyfile(os.path.join(ROOT, "setup", "security.example.json"), sec_path)
    ADMIN_USER, ADMIN_PW, PHRASE = "loadadmin", "load-test-pw-1234", "DELETE INDEX"

    if ns.policy:
        srv_path = os.path.join(tmp, "server.json")
        shutil.copyfile(os.path.join(ROOT, "setup", "server.example.json"), srv_path)
        with open(srv_path, encoding="utf-8") as f:
            sd = json.load(f)
        sd.setdefault("concurrency", {})["reads_during_build"] = ns.policy
        with open(srv_path, "w", encoding="utf-8") as f:
            json.dump(sd, f, ensure_ascii=False, indent=1)
        print("정책: concurrency.reads_during_build = %s" % ns.policy)

    env = dict(os.environ)
    for k, v in (("CONFIG", cfg), ("TUNING", os.path.join(tmp, "tuning.json")),
                 ("LOGS_DIR", os.path.join(tmp, "logs")), ("PRESETS", os.path.join(tmp, "presets.json")),
                 ("QUERY_RULES", os.path.join(tmp, "query_rules.json")), ("RULES", os.path.join(tmp, "rules.json")),
                 ("PINS", os.path.join(tmp, "pins.json")), ("SECURITY", os.path.join(tmp, "security.json")),
                 ("SERVER", os.path.join(tmp, "server.json")), ("SCHEDULE", os.path.join(tmp, "schedule.json")),
                 ("PROMPTS_DIR", os.path.join(tmp, "prompts"))):
        env["LLMWIKI_%s_PATH" % k] = v
    env["PYTHONIOENCODING"] = "utf-8"

    r = subprocess.run([PY, "-m", "llmwiki", "users", "add", ADMIN_USER, "--role", "admin", "--password", ADMIN_PW],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    check("관리자 계정 준비", r.returncode == 0, (r.stdout or r.stderr or "").strip().splitlines()[-1:])

    r = subprocess.run([PY, "-m", "llmwiki", "build", "--full", "--yes"], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if not check("초기 빌드", r.returncode == 0, (r.stdout or r.stderr or "").strip().splitlines()[-1:]):
        print("\nRESULT PROBLEMS"); return 1

    srv = subprocess.Popen([PY, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(ns.port)],
                           cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace")
    # 서버 출력 파이프를 **반드시 비운다** (2026-09-24, CODE_REVIEW_0924 §2.10): 읽지 않으면 버퍼가 차는 순간
    # 서버의 stderr 쓰기가 영원히 막히고, 배타 잠금을 쥔 스레드가 막히면 서버 전체가 멎는다 (멍키 테스트 실측 28분).
    threading.Thread(target=lambda: [None for _ in srv.stdout], daemon=True).start()
    base = "http://127.0.0.1:%d" % ns.port
    summary = {}
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/api/status", timeout=2).read()
                break
            except Exception:
                time.sleep(0.25)
        else:
            check("서버 기동", False, "떠오르지 않음"); print("\nRESULT PROBLEMS"); return 1
        check("서버 기동", True, base)

        st_, body_, hdr_ = post_raw(base, "/api/auth/login", {"username": ADMIN_USER, "password": ADMIN_PW}, timeout=60)
        adm = (hdr_.get("Set-Cookie") or "").split(";")[0]
        if not check("관리자 로그인", st_ == 200 and bool(adm), body_.get("error") or body_.get("role") or st_):
            print("\nRESULT PROBLEMS"); return 1

        # 확인 게이트가 실제로 막는지 먼저 본다 — 이게 있어야 "실수로 리빌드가 돌지 않는다" 가 사실이다
        st_, body_, _ = post_raw(base, "/api/build", {"channel": "fts"}, timeout=60, cookie=adm)
        check("확인 없이 채널 리빌드 → 막힘", st_ in (403, 428), "HTTP %s %s" % (st_, str(body_.get("error"))[:60]))
        CONFIRM = {"_confirm": True, "_phrase": PHRASE, "_password": ADMIN_PW}

        def scenario(label, payload, expect_no_fail=True):
            load = Load(base, ns.users)
            load.start()
            time.sleep(ns.warm)
            t0 = time.time()
            err = ""
            try:
                code, res, _ = post_raw(base, "/api/build", dict(CONFIRM, **payload), cookie=adm)
                if code != 200:
                    err = "HTTP %s %s" % (code, str((res or {}).get("error"))[:70])
                elif (res or {}).get("error"):
                    err = str(res["error"])[:100]
                elif (res or {}).get("job"):          # 비동기 작업이면 끝날 때까지 기다린다
                    err = _wait_job(base, res["job"], adm)
            except Exception as e:
                err = "%s: %s" % (type(e).__name__, str(e)[:80])
            t1 = time.time()
            build_s = round(t1 - t0, 1)
            win = load.window(t0, t1)          # **빌드가 도는 동안** 시작된 질의만
            time.sleep(0.5)
            st = load.finish()
            summary[label] = dict(st, build_s=build_s, build_error=err, during=win)
            total = st["ok"] + st["rejected"] + st["failed"]
            check("%s · 빌드 성공" % label, not err, err or "%.1fs" % build_s)
            check("%s · 질의 실패 0건" % label, st["failed"] == 0, "성공 %d · 거절 %d · 실패 %d %s"
                  % (st["ok"], st["rejected"], st["failed"], st["errors"] or ""))
            if expect_no_fail:
                check("%s · 질의 거절 0건" % label, st["rejected"] == 0,
                      "거절 %d/%d (%.1f%%)" % (st["rejected"], total, 100.0 * st["rejected"] / max(1, total)))
            check("%s · 빌드 뒤 질의 정상" % label, _query_ok(base), "")
            # 이 줄이 핵심이다 — 빌드 구간에서 질의가 **얼마나 기다렸나**
            print("      [빌드 중] 질의 %d건 p50=%sms p95=%sms max=%sms  /  [전체] p95=%sms · 빌드 %.1fs"
                  % (win["n"], win["p50_ms"], win["p95_ms"], win["max_ms"], st["p95_ms"], build_s))

        def _query_ok(b):
            try:
                res = post(b, "/api/search", {"q": "RX DMA underrun 원인", "channels": ["fts"], "k": 5}, timeout=120)
                return bool((res.get("result") or {}).get("hits") or (res.get("result") or {}).get("rows") or res.get("result"))
            except Exception:
                return False

        # 1) 증분 빌드 — 읽기를 막지 않는 것이 설계 (soft)
        scenario("증분 빌드", {"full": False})
        # 2) 채널 빌드 3종 — 각각 배타 (exclusive)
        for ch in ("fts", "vector", "graph"):
            scenario("채널 빌드 %s" % ch, {"channel": ch})
        # 3) 전체 리빌드 — 배타 + 색인 초기화
        scenario("전체 리빌드", {"full": True, "reset": True})

        # 무결성
        try:
            v = post(base, "/api/build/verify", {})
            probs = ((v.get("result") or v).get("problems") or [])
            check("빌드 뒤 색인 무결성", not probs, probs[:2] or "문제 없음")
        except Exception as e:
            check("빌드 뒤 색인 무결성", False, str(e)[:100])
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=20)
        except Exception:
            srv.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n── 요약 (%d명 동시 · 빌드가 도는 구간 기준) ──" % ns.users)
    print("  %-16s %7s %6s %6s %6s %9s %9s %9s"
          % ("시나리오", "빌드(s)", "질의", "거절", "실패", "p50(ms)", "p95(ms)", "max(ms)"))
    for k, v in summary.items():
        w = v.get("during") or {}
        print("  %-16s %7s %6d %6d %6d %9s %9s %9s"
              % (k, v["build_s"], w.get("n", 0), w.get("rejected", 0), w.get("failed", 0),
                 w.get("p50_ms", 0), w.get("p95_ms", 0), w.get("max_ms", 0)))
    print("  ※ max 가 빌드 시간에 가까우면 그 빌드가 **읽기를 막고 있다**는 뜻이다 (배타 락).")

    n_ok = sum(1 for r_ in rows if r_["ok"])
    print("\n검사 %d개 중 %d개 통과 · 실패 %d개" % (len(rows), n_ok, len(rows) - n_ok))
    for r_ in rows:
        if not r_["ok"]:
            print("  FAIL %-44s %s" % (r_["name"], r_["detail"]))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_build_load_result.json"),
              "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "summary": summary, "users": ns.users, "ok": n_ok, "n": len(rows)},
                  f, ensure_ascii=False, indent=1)
    print("\nRESULT " + ("OK" if n_ok == len(rows) else "PROBLEMS"))
    return 0 if n_ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
