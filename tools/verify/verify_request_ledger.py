"""사라지는 요청을 **재현하고**, 요청 원장이 그것을 잡는지 확인한다 (2026-09-23 요청 4).

사용자 보고: "동시에 다수 사용자가 query 를 요청했을 때, 일부 query 가 전혀 기록이 남아 있지 않고
사라지는 문제가 있다. 시간이 좀 흐르면 없어져서 원인도 찾을 수 없다. 이거 재현되는지 확인해줘."

이 하네스가 하는 일
  1. **보낸 요청을 기준선으로 남긴다** — 클라이언트가 실제로 보낸 것이 진실이다.
  2. 그것을 두 원천과 대조한다:
       requests 테이블 = 예전부터 있던 '요청 프로파일'
       data/ledger    = 이번에 만든 요청 원장
     예전 구조에서는 **거절·시간초과·취소·중단이 requests 에 아예 들어가지 않으므로** 차이가 그대로 드러난다.
  3. 원장 쪽은 **누락 0 · 중복 0 · 상태 미상 0** 이어야 한다.

왜 기존 하네스로는 부족했나
  `verify_soak.py`·`verify_monkey.py` 는 "서버가 버티는가"(5xx 없이 응답하는가)를 본다.
  응답을 200 으로 받고도 **기록이 남지 않는** 이 문제는 그 검사를 그대로 통과한다.

격리
  임시 폴더 + `LLMWIKI_*_PATH` + **mock LLM**(실제 LLM 을 부르지 않는다) + 자체 포트.
  실제 색인·설정·로그·원장을 건드리지 않으므로, 운영 중인 서버가 있어도 안전하다.

실행:
    python tools/verify/verify_request_ledger.py                 # 시나리오 A~C
    python tools/verify/verify_request_ledger.py --scenario A    # 하나만
    python tools/verify/verify_request_ledger.py --capacity      # 동시 실행 수별 처리량(포화점)
    python tools/verify/verify_request_ledger.py --keep          # 임시 폴더를 남긴다(사후 분석)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sqlite3
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
sys.path.insert(0, ROOT)

DOC = """---
doc_type: issue
ext_id: ISSUE-{n}
---

# RX DMA underrun {n}

RX DMA 에서 underrun 이 발생하면 PHY 재시작이 실패한다. 원인은 클럭 게이팅 타이밍이다.
rev B1 에서 t_setup 은 4 ns 이고 CL-{c} 에서 고쳤다. AGC 수렴 지연은 별개 문제다.
"""


# ---------------------------------------------------------------- 환경
class Env:
    """격리된 서버 하나 (임시 데이터·mock LLM·자체 포트)."""

    def __init__(self, port: int, keep: bool = False, mock_delay_ms: int = 900, **server_cfg):
        self.port = port
        self.keep = keep
        server_cfg["mock_delay_ms"] = mock_delay_ms
        self.tmp = tempfile.mkdtemp(prefix="llmwiki_led_")
        self.base = "http://127.0.0.1:%d" % port
        self.proc = None
        self.data = os.path.join(self.tmp, "data")
        corpus = os.path.join(self.tmp, "corpus")
        os.makedirs(corpus)
        for i in range(6):
            with open(os.path.join(corpus, "d%d.md" % i), "w", encoding="utf-8") as f:
                f.write(DOC.format(n=8000 + i, c=90000 + i) * 3)
        self.corpus = corpus
        cfg = {
            "corpus_dirs": [corpus], "data_dir": self.data, "wiki_dir": os.path.join(self.tmp, "wiki"),
            "llm_provider": "mock", "embed_provider": "hash", "embed_dim": 64,
            "web_host": "127.0.0.1", "web_port": port,
            "toggles": {"llm_graph": False, "community_summary": False, "query_cache": False, "auto_build": False},
        }
        self.config_path = os.path.join(self.tmp, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        # 서버 설정: 한도를 낮춰 **거절을 일부러 유발**한다 (기록이 남는지가 요점이다)
        sc = {"concurrency": {"max_parallel_reads": 4, "queue_max": 8, "queue_timeout_s": 5,
                              "max_parallel_per_user": 99, "max_parallel_per_ip": 99},
              "rate_limit": {"enabled": False},
              "ledger": {"enabled": True, "dir": os.path.join(self.data, "ledger"), "include_get": "heavy"},
              "monitor": {"live_dir": os.path.join(self.data, "live")}}
        for k, v in server_cfg.items():
            if isinstance(v, dict):
                sc.setdefault(k, {}).update(v)
        self.server_path = os.path.join(self.tmp, "server.json")
        with open(self.server_path, "w", encoding="utf-8") as f:
            json.dump(sc, f, ensure_ascii=False, indent=1)
        self.env = dict(os.environ, PYTHONIOENCODING="utf-8",
                        LLMWIKI_CONFIG_PATH=self.config_path, LLMWIKI_SERVER_PATH=self.server_path,
                        LLMWIKI_LOGS_DIR_PATH=os.path.join(self.tmp, "logs"),
                        LLMWIKI_DATA_DIR_PATH=self.data,
                        LLMWIKI_SECURITY_PATH=os.path.join(self.tmp, "security.json"))
        # mock LLM 은 즉시 답하므로 그대로 두면 **혼잡이 만들어지지 않는다** — 슬롯이 비어 거절도 대기도 없다.
        # 실제 환경의 느린 LLM(실측 질의 1건 250초)을 흉내 내려면 호출마다 지연을 준다.
        # 이 지연이 있어야 "동시 질의가 몰려 일부가 버려지는" 상황을 재현할 수 있다.
        self.env["LLMWIKI_MOCK_DELAY_MS"] = str(int(server_cfg.pop("mock_delay_ms", 900)))

    def build(self) -> None:
        subprocess.run([sys.executable, "-m", "llmwiki", "build", "--full", "--json"], cwd=ROOT, env=self.env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=ROOT, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(90):
            try:
                urllib.request.urlopen(self.base + "/api/auth/me", timeout=2).read()
                return
            except Exception:
                time.sleep(1)
        raise RuntimeError("서버가 뜨지 않았습니다 (포트 %d)" % self.port)

    def restart(self) -> None:
        """설정 파일을 직접 고친 뒤 되살릴 때 쓴다 (관리 API 가 막힌 상태에서 빠져나오는 유일한 길)."""
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except Exception:
                self.proc.kill()
            self.proc = None
        self.start()

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except Exception:
                self.proc.kill()
            self.proc = None
        if not self.keep:
            shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 클라이언트 ----
    def post(self, path: str, body: dict, timeout: float = 120):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "X-Requested-With": "llmwiki"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")
            except Exception:
                return e.code, {}
        except Exception as e:
            return 0, {"error": str(e)}

    def set_limits(self, values: dict, timeout: float = 30):
        """서버 모니터의 한도 변경 — 행동 이름은 `set_limits`, 값은 `values` 다 (server.py `_admin_server`)."""
        return self.post("/api/admin/server", {"action": "set_limits", "values": values}, timeout=timeout)

    def get(self, path: str, timeout: float = 60):
        try:
            with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except Exception:
            return {}

    # ---- 확인 ----
    def db_requests(self) -> int:
        p = os.path.join(self.data, "llmwiki.sqlite3")
        if not os.path.exists(p):
            return 0
        c = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
        try:
            return int(c.execute("SELECT COUNT(*) FROM requests WHERE kind='query'").fetchone()[0])
        finally:
            c.close()

    def ledger_rows(self, kind: str = "query"):
        d = os.path.join(self.data, "ledger")
        folded = {}
        for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not fn.endswith(".jsonl"):
                continue
            with open(os.path.join(d, fn), encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    tok = rec.get("token")
                    if not tok:
                        continue
                    cur = folded.setdefault(tok, {"token": tok})
                    cur.update({k: v for k, v in rec.items() if k not in ("ev",)})
                    if rec.get("ev") == "close":
                        cur["closed"] = True
        return [r for r in folded.values() if not kind or r.get("kind") == kind]


# ---------------------------------------------------------------- 시나리오
def scenario_a(env: Env, n: int = 40) -> int:
    """A. 빌드 완료 후, **서로 다른 질문을** 동시에 — 사용자가 보고한 바로 그 상황.

    질문을 전부 다르게 만드는 것이 핵심이다: 같은 질문이면 임베딩·답변 캐시에 걸려
    잠금 경합도, 기록 경로도 제대로 타지 않는다.
    """
    print("\n[A] 서로 다른 질의 %d건 동시 (슬롯 4 · 대기열 8 · 대기한도 5초 — 거절을 일부러 유발)" % n)
    sent, lock = [], threading.Lock()

    def one(i: int):
        q = "질의 %d 번: RX DMA underrun %d 원인과 CL 은?" % (i, 8000 + (i % 6))
        code, j = env.post("/api/query", {"q": q, "async": True, "log": True})
        with lock:
            sent.append({"i": i, "q": q, "http": code, "job": j.get("job"), "code": j.get("code")})

    ths = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    t0 = time.perf_counter()
    for t in ths:
        t.start()
    for t in ths:
        t.join(180)
    print("    보낸 요청 %d건 · 접수까지 %.1fs" % (len(sent), time.perf_counter() - t0))

    accepted = [s for s in sent if s["job"]]
    rejected = [s for s in sent if not s["job"]]
    print("    접수됨 %d · 즉시 거절 %d (%s)" % (
        len(accepted), len(rejected), ", ".join(sorted({str(s["code"]) for s in rejected})) or "-"))

    # 잡이 끝나기를 기다린다
    deadline = time.time() + 300
    while time.time() < deadline:
        left = [s for s in accepted if (env.get("/api/jobs/" + s["job"]) or {}).get("status") == "running"]
        if not left:
            break
        time.sleep(2)
    time.sleep(2)

    db = env.db_requests()
    led = env.ledger_rows("query")
    led_closed = [r for r in led if r.get("closed")]
    by_status = {}
    for r in led:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1

    print("\n    == 대조 ==")
    print("    보낸 질의                : %d" % len(sent))
    print("    requests 테이블(kind=query): %d   ← 예전 구조에서 남던 것" % db)
    print("    요청 원장(kind=query)     : %d   (종료 기록 %d)" % (len(led), len(led_closed)))
    print("    원장 상태별              : %s" % by_status)
    missing_old = len(sent) - db
    print("\n    예전 구조였다면 기록이 없었을 요청: %d건 (%.0f%%)"
          % (missing_old, 100.0 * missing_old / max(1, len(sent))))
    print("    원장 누락: %d건" % (len(sent) - len(led)))

    bad = 0
    if len(led) < len(sent):
        print("    FAIL 원장에 %d건이 빠졌다" % (len(sent) - len(led)))
        bad += 1
    if len(led_closed) != len(led):
        print("    FAIL 종료가 기록되지 않은 항목 %d건" % (len(led) - len(led_closed)))
        bad += 1
    if by_status.get("unknown"):
        print("    FAIL 상태 미상 %d건" % by_status["unknown"])
        bad += 1
    if missing_old <= 0:
        print("    주의 이번 실행에서는 예전 구조와 차이가 나지 않았다 (거절이 유발되지 않았을 수 있다)")
    if not bad:
        print("    OK   보낸 요청이 모두 원장에 정확히 한 번씩, 종료 상태까지 남았다")
    return bad


def _maintenance_off(env: Env) -> None:
    """점검 모드를 끈다 — **관리 API 가 막혀 있을 수 있다.**

    허용 역할에서 admin 을 빼고 점검 모드를 켜면 `/api/admin/server` 자신도 503 이 된다
    (`check_access` 는 관리 경로를 예외로 두지 않는다). 그러면 API 로는 되돌릴 수 없으므로
    설정 파일을 직접 고치고 서버를 다시 띄운다. 이것을 하지 않으면 점검 모드가 켜진 채로
    남아 **뒤에 오는 시나리오가 전부 거절로 오염된다** (실제로 그랬다).
    """
    if env.set_limits({"access.maintenance_mode": False, "access.maintenance_allow_roles": ["admin"]})[0] == 200:
        return
    with open(env.server_path, encoding="utf-8") as f:
        sc = json.load(f)
    sc.setdefault("access", {}).update({"maintenance_mode": False, "maintenance_allow_roles": ["admin"]})
    with open(env.server_path, "w", encoding="utf-8") as f:
        json.dump(sc, f, ensure_ascii=False, indent=1)
    print("    (관리 API 도 점검 모드에 막혀 설정 파일을 고치고 서버를 다시 띄운다)")
    # Windows 의 terminate() 는 강제 종료라 서버의 종료 처리가 돌지 않는다.
    # writer 가 버퍼(flush_ms=200ms)를 비울 틈을 주고 죽인다 — 안 그러면 방금 거절이 사라진다.
    time.sleep(0.6)
    env.restart()


def scenario_b(env: Env) -> int:
    """B. 거절 사유가 남는가 — 점검 모드로 막고, 그 거절에 **무엇이 막았는지**가 붙는지 본다.

    주의: 이 격리 환경에는 security.json 이 없어 인증이 `off` 다. 그러면 `Auth.identify()` 가
    모든 요청을 **admin 으로 취급**하고(auth.py: mode=="off"), 점검 모드는 기본 허용 역할이 admin 이라
    그냥 통과해 버린다. 그래서 허용 역할을 admin 이 **아닌** 것으로 바꿔 놓고 막히는지 본다 —
    검사하려는 것은 "누가 통과하느냐" 가 아니라 "거절이 사유와 함께 기록되느냐" 이기 때문이다.
    """
    print("\n[B] 점검 모드 거절 — 사유와 설정 키가 기록되는가")
    on = {"access.maintenance_mode": True, "access.maintenance_allow_roles": ["builder"]}
    off = {"access.maintenance_mode": False, "access.maintenance_allow_roles": ["admin"]}
    sc, sj = env.set_limits(on)
    if sc != 200:
        print("    FAIL 점검 모드를 켜지 못했다 — HTTP %s %s" % (sc, sj.get("error") or ""))
        _maintenance_off(env)
        return 1
    try:
        code, _j = env.post("/api/query", {"q": "점검 중 질의 RX DMA", "async": True})
    finally:
        _maintenance_off(env)
    time.sleep(1)

    # 시나리오 A 가 만든 대기열 거절과 섞이지 않게 **점검 모드 거절만** 골라 본다.
    rows = [r for r in env.ledger_rows("") if r.get("code") == "maintenance"]
    print("    HTTP %s · 원장의 점검 모드 거절 %d건" % (code, len(rows)))
    bad = 0
    if code != 503:
        print("    FAIL 점검 모드를 켰는데 요청이 통과했다 (HTTP %s)" % code)
        bad += 1
    if not rows:
        print("    FAIL 거절이 원장에 남지 않았다 — 이것이 바로 '기록 없이 사라지는' 경우다")
        return bad + 1
    need = ("what", "key", "hint")
    for r in rows[:3]:
        lim = json.loads(r["limit_json"]) if r.get("limit_json") else {}
        print("      %-12s %s" % (r.get("http") or "-", lim.get("what") or "(사유 정보 없음)"))
        print("      %-12s %s" % ("", "설정 키 %s · 해제: %s" % (lim.get("key") or "-", lim.get("hint") or "-")))
        miss = [k for k in need if not lim.get(k)]
        if miss:
            print("    FAIL 거절 맥락이 비어 있다: %s" % ", ".join(miss))
            bad += 1
    if not bad:
        print("    OK   거절이 '무엇이 막았나 · 설정 키 · 해제 방법' 과 함께 남았다")
    return bad


def scenario_c(env: Env) -> int:
    """C. 서버가 죽어도 '들어왔다' 는 남는가 — open 을 먼저 쓰는 설계의 핵심."""
    print("\n[C] 서버 강제 종료 — 진행 중이던 요청이 '중단' 으로 남는가")
    before = len(env.ledger_rows(""))
    for i in range(6):
        env.post("/api/query", {"q": "중단 확인용 질의 %d RX DMA underrun %d" % (i, 8000 + i), "async": True})
    # 비동기라 POST 는 즉시 돌아온다. 잡이 **도는 도중에** 죽여야 '끝이 없는 기록' 이 생긴다.
    time.sleep(0.3)
    env.proc.kill()                 # 정상 종료가 아니라 강제 종료
    env.proc.wait(timeout=20)
    env.proc = None
    time.sleep(1)
    rows = env.ledger_rows("")
    opened_only = [r for r in rows if not r.get("closed")]
    print("    원장 %d → %d건 · 종료가 없는 항목 %d건 (이것이 화면에서 '중단' 으로 보인다)"
          % (before, len(rows), len(opened_only)))
    if len(rows) <= before:
        print("    FAIL 강제 종료 직전의 요청이 하나도 남지 않았다")
        return 1
    if not opened_only:
        print("    주의 죽이기 전에 모두 끝나 '중단' 상태는 만들어지지 않았다 (접수 기록은 모두 남았다)")
    print("    OK   접수 기록이 남는다 — 죽은 뒤에도 '들어왔다' 는 사실이 사라지지 않는다")
    return 0


def capacity(env: Env, steps=(1, 2, 4, 8)) -> int:
    """동시 실행 수를 올려 가며 **처리량이 더 오르지 않는 지점**을 찾는다 (슬롯 수를 정하는 근거)."""
    print("\n[용량] 동시 실행 수별 처리량 — throughput 이 안 오르는 지점이 그 환경의 상한")
    print("    %-6s %-10s %-10s %-10s" % ("동시", "완료", "총 시간", "완료/분"))
    for n in steps:
        # 용량 측정에서는 **거절이 없어야** 한다 — 거절된 요청은 일을 하지 않으므로 처리량이 부풀려진다.
        sc, sj = env.set_limits({"concurrency.max_parallel_reads": n, "concurrency.classes.query.max_parallel": n,
                                 "concurrency.queue_max": 256, "concurrency.queue_timeout_s": 600})
        if sc != 200:
            print("    FAIL 슬롯 수를 바꾸지 못했다 — HTTP %s %s (측정값이 무의미해진다)" % (sc, sj.get("error") or ""))
            return 1
        jobs, lock = [], threading.Lock()

        def one(i, n=n):
            _c, j = env.post("/api/query", {"q": "용량 측정 %d-%d RX DMA underrun" % (n, i), "async": True})
            if j.get("job"):
                with lock:
                    jobs.append(j["job"])
        t0 = time.perf_counter()
        ths = [threading.Thread(target=one, args=(i,)) for i in range(n * 3)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(120)
        deadline = time.time() + 240
        while time.time() < deadline:
            if not [j for j in jobs if (env.get("/api/jobs/" + j) or {}).get("status") == "running"]:
                break
            time.sleep(1)
        dt = time.perf_counter() - t0
        print("    %-6d %-10d %-10.1f %-10.1f" % (n, len(jobs), dt, len(jobs) / max(0.01, dt / 60)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="사라지는 요청 재현 + 요청 원장 검증")
    ap.add_argument("--port", type=int, default=8821)
    ap.add_argument("--requests", type=int, default=40, help="시나리오 A 의 동시 질의 수")
    ap.add_argument("--scenario", default="", help="A | B | C (비우면 전부)")
    ap.add_argument("--capacity", action="store_true", help="동시 실행 수별 처리량만 측정")
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 남긴다")
    ns = ap.parse_args(argv)

    print("요청 원장 검증 — 격리 환경(임시 폴더 · mock LLM · 포트 %d). 실제 색인·설정·원장은 건드리지 않는다." % ns.port)
    env = Env(ns.port, keep=ns.keep)
    bad = 0
    try:
        print("  임시 폴더: %s" % env.tmp)
        print("  색인 빌드 중…")
        env.build()
        env.start()
        want = (ns.scenario or "ABC").upper()
        if ns.capacity:
            bad += capacity(env)
        else:
            if "A" in want:
                bad += scenario_a(env, ns.requests)
            if "B" in want:
                bad += scenario_b(env)
            if "C" in want:
                bad += scenario_c(env)      # 마지막 — 서버를 죽인다
    finally:
        env.stop()
        if ns.keep:
            print("\n임시 폴더를 남겼습니다: %s" % env.tmp)
    print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
