# -*- coding: utf-8 -*-
"""2026-09-15 다중 사용자 기능 검증.

1) 요청 격리: 동시 질의가 서로의 설정(프리셋·오버라이드·역할 모델)·튜닝·SQLite 연결을 오염시키지 않는다.
2) 요청 관리자: 읽기 병렬 / 쓰기 배타(soft=증분 빌드 중 질의 허용) · 동시 수 상한 · 대기열 · 속도 제한 · 차단 · 점검 모드.
3) 취소: 진행 중인 질의/빌드를 협조적으로 중지하고, 지금까지의 결과·체크포인트를 유지한다. 시간 제한(watchdog) 자동 취소.
4) 역할별 LLM 정책: timeout/retries/backoff/budget/회로 차단이 역할마다 따로 적용되고, 최종 실패해도 대체 경로로 답이 나온다.
5) 스케줄러: every/at/cron 파싱과 다음 실행 시각, 동작(python/query/build/http/fetch_url) 실행과 이력.
6) 모델 카탈로그(models.json): 목록·추가·삭제·역할 필터.
7) 스트레스: 스레드 30개 동시 질의 · 긴 빌드 중 질의 · 다수 요청 중 취소 (DEBUG 로그 레벨).
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402
from llmwiki import tuning as tn  # noqa: E402
from llmwiki import schema as sc  # noqa: E402
from llmwiki import query_rules as qr  # noqa: E402
from llmwiki import progress as pg  # noqa: E402
from llmwiki import providers as pv  # noqa: E402
from llmwiki import reqmgr as rq  # noqa: E402
from llmwiki import scheduler as sch  # noqa: E402
from llmwiki import models_catalog as mc  # noqa: E402
from llmwiki import auth as A  # noqa: E402
from llmwiki.web import server as ws  # noqa: E402


def _gen_corpus(out):
    spec = importlib.util.spec_from_file_location("mk", os.path.join(ROOT, "setup", "make_sample_corpus_modem.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    mod.gen(out, 1)


class _Base(unittest.TestCase):
    KEYS = ("LOGS_DIR", "SCHEMAS_DIR", "QUERY_RULES", "MCP_SOURCES", "PROMPTS_DIR", "PINS", "PRESETS", "AGENTS", "SECURITY", "RULES", "SERVER", "SCHEDULE", "MODELS")
    FILE_KEYS = ("RULES", "SECURITY", "AGENTS", "PINS", "QUERY_RULES", "PRESETS", "MCP_SOURCES", "SERVER", "SCHEDULE", "MODELS")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus = os.path.join(self.tmp, "corpus")
        _gen_corpus(self.corpus)
        for k in self.KEYS:
            os.environ["LLMWIKI_%s_PATH" % k] = os.path.join(self.tmp, k.lower() + (".json" if k in self.FILE_KEYS else ""))
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        mc._CACHE["data"] = None
        A.reset_sessions()
        pv.circuit_reset()
        self._tuning_path = tn.TUNING_PATH
        tn.TUNING_PATH = os.path.join(self.tmp, "tuning.json")
        tn.load_tuning(tn.TUNING_PATH)
        s = Settings(corpus_dirs=[self.corpus], data_dir=os.path.join(self.tmp, "data"), wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=256, log_level="DEBUG")
        s.toggles = Toggles(query_cache=False, health_check=False, precompute=False)
        self.s = s
        self.p = Pipeline(s)
        self.p.build(full=True)

    def tearDown(self):
        try:
            self.p.store.close()
        except Exception:
            pass
        rq._MANAGER = None
        pg.set_publisher(None, None)
        for k in self.KEYS:
            os.environ.pop("LLMWIKI_%s_PATH" % k, None)
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        mc._CACHE["data"] = None
        A.reset_sessions()
        pv.circuit_reset()
        tn.load_tuning(os.path.join(self.tmp, "none.json"))
        tn.TUNING_PATH = self._tuning_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mgr(self, **over):
        cfg = rq.load_config(os.path.join(self.tmp, "server.json"))
        for k, v in over.items():
            a, b = k.split(".", 1)
            cfg[a][b] = v
        m = rq.RequestManager(cfg, path=os.path.join(self.tmp, "server.json"), install_publisher=False)
        self.addCleanup(m.stop)
        return m


# ======================================================================== 1. 요청 격리
class IsolationTest(_Base):
    def test_parallel_queries_do_not_share_settings(self):
        """동시에 서로 다른 top_k / 토글 / 튜닝으로 질의해도 각자의 값으로 실행되고, 전역 설정은 그대로."""
        base_k = self.p.base_settings.top_k_final
        results = {}
        errors = []

        def run(i):
            try:
                k = 3 + (i % 5)
                with self.p.request_scope(overrides={"top_k_final": k, "rerank": (i % 2 == 0)}):
                    tn.T.values["rrf_k"] = 10 + i          # 요청 오버레이에만 기록
                    self.assertEqual(self.p.s.top_k_final, k)
                    self.assertEqual(self.p.s.toggles.rerank, i % 2 == 0)
                    res, tr = self.p.query("ISSUE-2001 원인과 수정 CL", log=False)
                    results[i] = (self.p.s.top_k_final, tn.T.get("rrf_k"), res["config"]["toggles"]["rerank"], len(res["hits"]))
            except BaseException as e:   # noqa: BLE001
                errors.append("%s: %s" % (type(e).__name__, e))

        ths = [threading.Thread(target=run, args=(i,)) for i in range(12)]
        [t.start() for t in ths]
        [t.join(120) for t in ths]
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 12)
        for i, (k, rrf, rr, nhits) in results.items():
            self.assertEqual(k, 3 + (i % 5), "요청 %d 의 top_k 가 다른 요청에 오염됨" % i)
            self.assertEqual(rrf, 10 + i, "요청 %d 의 튜닝 오버레이가 오염됨" % i)
            self.assertEqual(rr, i % 2 == 0)
            self.assertGreater(nhits, 0)
        # 전역은 불변
        self.assertEqual(self.p.base_settings.top_k_final, base_k)
        self.assertNotIn("rrf_k", tn.T.values)
        self.assertEqual(self.p.s.top_k_final, base_k)

    def test_role_models_isolated_and_cached_per_signature(self):
        """요청마다 다른 역할 모델을 써도 서로 덮어쓰지 않고, 같은 조합은 인스턴스를 재사용한다."""
        with self.p.request_scope(overrides={"answer_model": "m-a"}):
            self.assertEqual(self.p.s.role_llm("answer")["model"], "m-a")
            a1 = self.p.llm_for("answer")
        with self.p.request_scope(overrides={"answer_model": "m-b", "answer_timeout_s": 42}):
            self.assertEqual(self.p.s.role_llm("answer")["model"], "m-b")
            b1 = self.p.llm_for("answer")
            self.assertEqual(b1.timeout, 42)          # 역할별 정책이 인스턴스에 반영
        self.assertIsNot(a1, b1)                      # 서명이 다르면 다른 인스턴스 (서로 덮어쓰지 않음)
        with self.p.request_scope(overrides={"answer_model": "m-a"}):
            self.assertIs(self.p.llm_for("answer"), a1)   # 같은 서명 → 캐시 재사용 (요청마다 새로 만들지 않음)
        # 요청 밖에서는 전역 설정의 모델
        self.assertEqual(self.p.s.role_llm("answer")["model"], self.p.base_settings.llm_model)
        self.assertNotEqual(self.p.llm_for("answer").timeout, 42)

    def test_store_session_per_thread(self):
        """스레드마다 다른 SQLite 연결을 쓴다 (한 스레드의 commit 이 다른 스레드의 트랜잭션을 확정하지 않도록)."""
        seen = {}

        def run(i):
            with self.p.store.session():
                seen[i] = id(self.p.store.conn)
                time.sleep(0.05)
                self.p.store.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
        ths = [threading.Thread(target=run, args=(i,)) for i in range(6)]
        [t.start() for t in ths]
        [t.join(30) for t in ths]
        self.assertEqual(len(set(seen.values())), 6)
        self.assertGreaterEqual(self.p.store.pool_info()["idle"], 1)

    def test_profiler_counters_are_per_request(self):
        """동시 질의의 SQL/LLM 카운터가 섞이지 않는다."""
        traces = {}

        def run(i):
            with self.p.request_scope():
                _res, tr = self.p.query("ISSUE-200%d 원인" % (i % 3), log=False)
                traces[i] = tr["summary"]["sql_statements"]
        ths = [threading.Thread(target=run, args=(i,)) for i in range(6)]
        [t.start() for t in ths]
        [t.join(120) for t in ths]
        self.assertEqual(len(traces), 6)
        for i, n in traces.items():
            self.assertGreater(n, 0)
            self.assertLess(n, 5000, "요청 %d 의 SQL 카운터에 다른 요청이 섞였다 (%d)" % (i, n))


# ======================================================================== 2. 요청 관리자
class RequestManagerTest(_Base):
    def test_rwlock_reads_parallel_writes_exclusive(self):
        lock = rq.RWLock()
        self.assertTrue(lock.acquire_read(timeout=1))
        self.assertTrue(lock.acquire_read(timeout=1))          # 읽기는 동시에
        self.assertEqual(lock.state()["readers"], 2)
        self.assertFalse(lock.acquire_write("exclusive", timeout=0.3))   # 읽는 중이면 배타 쓰기 대기 → 타임아웃
        self.assertTrue(lock.acquire_write("soft", timeout=0.3))         # soft 는 읽기와 공존 (증분 빌드)
        self.assertFalse(lock.acquire_write("soft", timeout=0.2))        # 쓰기끼리는 배타
        lock.release_write()
        lock.release_read()
        lock.release_read()
        self.assertTrue(lock.acquire_write("exclusive", timeout=1))
        self.assertFalse(lock.acquire_read(timeout=0.3))                 # 배타 쓰기 중에는 읽기 대기
        lock.release_write()
        self.assertTrue(lock.acquire_read(timeout=1))
        lock.release_read()

    def test_parallel_read_slots_and_queue(self):
        m = self._mgr(**{"concurrency.max_parallel_reads": 2, "concurrency.queue_timeout_s": 5, "rate_limit.enabled": False})
        started, peak, cur = [], [0], [0]
        lk = threading.Lock()

        def run(i):
            with m.ticket("query", "read", client={"user": "u%d" % i, "role": "viewer", "ip": "1.1.1.%d" % i}, label="q%d" % i):
                with lk:
                    cur[0] += 1
                    peak[0] = max(peak[0], cur[0])
                started.append(i)
                time.sleep(0.4)
                with lk:
                    cur[0] -= 1
        ths = [threading.Thread(target=run, args=(i,)) for i in range(6)]
        [t.start() for t in ths]
        [t.join(30) for t in ths]
        self.assertEqual(len(started), 6)
        self.assertLessEqual(peak[0], 2, "동시 실행 상한을 넘었다 (%d)" % peak[0])

    def test_rate_limit_and_per_user_limit(self):
        m = self._mgr(**{"rate_limit.per_user_per_min": 3, "rate_limit.query_per_user_per_min": 2, "concurrency.max_parallel_per_user": 1})
        cl = {"user": "bob", "role": "viewer", "via": "local", "ip": "10.0.0.9"}
        for _ in range(2):
            with m.ticket("query", "read", client=cl, label="q"):
                pass
        with self.assertRaises(rq.Rejected) as cm:      # 질의 분당 상한
            with m.ticket("query", "read", client=cl, label="q"):
                pass
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(cm.exception.code, "rate_limited")
        self.assertIsNotNone(cm.exception.retry_after)
        # admin 은 면제
        with m.ticket("query", "read", client={"user": "root", "role": "admin", "via": "local", "ip": "10.0.0.1"}, label="q"):
            pass
        # 동시 수 상한
        m2 = self._mgr(**{"concurrency.max_parallel_per_user": 1, "rate_limit.enabled": False})
        hold = threading.Event()
        done = threading.Event()

        def hold_one():
            with m2.ticket("query", "read", client=cl, label="long"):
                hold.set()
                done.wait(5)
        t = threading.Thread(target=hold_one)
        t.start()
        hold.wait(5)
        with self.assertRaises(rq.Rejected) as cm2:
            with m2.ticket("query", "read", client=cl, label="second"):
                pass
        self.assertEqual(cm2.exception.status, 429)
        self.assertEqual(cm2.exception.code, "per_user_limit")
        done.set()
        t.join(10)

    def test_queue_full_and_access_control(self):
        m = self._mgr(**{"concurrency.max_parallel_reads": 1, "concurrency.queue_max": 1, "concurrency.queue_timeout_s": 3, "rate_limit.enabled": False,
                         "concurrency.max_parallel_per_user": 99, "concurrency.max_parallel_per_ip": 99})
        rel = threading.Event()
        started = threading.Event()

        def hold():
            with m.ticket("query", "read", client={"user": "a", "ip": "1.1.1.1"}, label="hold"):
                started.set()
                rel.wait(10)
        t = threading.Thread(target=hold)
        t.start()
        started.wait(5)
        waiter_err = []

        def waiter():
            try:
                with m.ticket("query", "read", client={"user": "b", "ip": "1.1.1.2"}, label="wait"):
                    pass
            except rq.Rejected as e:
                waiter_err.append(e)
        w = threading.Thread(target=waiter)
        w.start()
        time.sleep(0.4)
        with self.assertRaises(rq.Rejected) as cm:       # 대기열이 가득 참
            with m.ticket("query", "read", client={"user": "c", "ip": "1.1.1.3"}, label="overflow"):
                pass
        self.assertEqual(cm.exception.status, 503)
        self.assertEqual(cm.exception.code, "queue_full")
        rel.set()
        t.join(10)
        w.join(10)
        # 차단·점검 모드
        m.block("ip", "9.9.9.", add=True, save=False)
        with self.assertRaises(rq.Rejected) as cm2:
            m.check_access("9.9.9.7", "x", "viewer")
        self.assertEqual(cm2.exception.status, 403)
        m.block("user", "mallory", add=True, save=False)
        with self.assertRaises(rq.Rejected):
            m.check_access("2.2.2.2", "mallory", "viewer")
        m.set_limits({"access.maintenance_mode": True}, save=False)
        with self.assertRaises(rq.Rejected) as cm3:
            m.check_access("2.2.2.2", "bob", "viewer")
        self.assertEqual(cm3.exception.status, 503)
        m.check_access("2.2.2.2", "root", "admin")      # admin 은 통과
        m.set_limits({"access.maintenance_mode": False}, save=False)

    def test_activity_and_stats(self):
        m = self._mgr(**{"rate_limit.enabled": False})
        started = threading.Event()
        rel = threading.Event()

        def hold():
            with m.ticket("query", "read", client={"user": "u1", "role": "viewer", "ip": "1.2.3.4", "origin": "web"}, label="보고서 질의"):
                started.set()
                rel.wait(10)
        t = threading.Thread(target=hold)
        t.start()
        started.wait(5)
        act = m.activity(viewer=True)
        self.assertEqual(len(act["running"]), 1)
        self.assertEqual(act["running"][0]["label"], "보고서 질의")
        self.assertIsNone(act["running"][0]["ip"])            # viewer 에게는 IP 를 감춘다
        self.assertEqual(m.activity(viewer=False)["running"][0]["ip"], "1.2.3.4")
        rel.set()
        t.join(10)
        st = m.stats()
        self.assertGreaterEqual(st["counters"]["done_done"], 1)
        self.assertIn("query", st["latency"])
        self.assertTrue(any(c["key"] == "user:u1" for c in st["clients"]))

    def test_limits_persist_to_file(self):
        m = self._mgr()
        m.set_limits({"concurrency.max_parallel_reads": 16, "rate_limit.per_user_per_min": 99}, save=True)
        with open(os.path.join(self.tmp, "server.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["concurrency"]["max_parallel_reads"], 16)
        self.assertEqual(saved["rate_limit"]["per_user_per_min"], 99)
        with self.assertRaises(KeyError):
            m.set_limits({"concurrency.nope": 1}, save=False)
        os.environ["LLMWIKI_SERVER_CONCURRENCY_MAX_PARALLEL_READS"] = "24"
        try:
            self.assertEqual(rq.load_config(os.path.join(self.tmp, "server.json"))["concurrency"]["max_parallel_reads"], 24)
        finally:
            os.environ.pop("LLMWIKI_SERVER_CONCURRENCY_MAX_PARALLEL_READS", None)


# ======================================================================== 3. 취소 · 시간 제한
class CancelTest(_Base):
    def test_cancel_query_midway(self):
        """실행 중인 질의를 취소하면 progress.Cancelled 로 빠져나오고 상태가 cancelled 로 남는다."""
        tok = "t-cancel-1"
        out = {}

        def run():
            try:
                pg.bind(tok, "query", "long", client={"user": "u"})
                for _ in range(200):
                    pg.tick(1, 200, "step")
                    time.sleep(0.02)
                out["status"] = "done"
                pg.unbind("done")
            except pg.Cancelled as e:
                out["status"] = "cancelled"
                out["by"] = e.by
                pg.unbind("cancelled")
        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.2)
        self.assertTrue(pg.cancel(tok, "admin", "테스트"))
        t.join(10)
        self.assertEqual(out["status"], "cancelled")
        self.assertEqual(out["by"], "admin")
        self.assertEqual((pg.get(tok) or {})["status"], "cancelled")

    def test_cancel_build_keeps_progress(self):
        """빌드를 중간에 취소해도 예외가 전파되고, 다음 빌드가 이어서 완료한다."""
        tok = "t-build-cancel"
        err = {}

        def run():
            try:
                with pg.cli_monitor(tok, "build", "build --full", enabled=False):
                    with self.p.request_scope():
                        self.p.build(full=False, force=True)
            except pg.Cancelled as e:
                err["cancelled"] = str(e)
            except Exception as e:  # noqa: BLE001
                err["other"] = "%s: %s" % (type(e).__name__, e)
        # 새 문서를 넣어 증분 빌드가 실제로 일을 하게 한다
        with open(os.path.join(self.corpus, "issues", "ISSUE-9100.md"), "w", encoding="utf-8") as f:
            f.write("---\nschema_version: 1\ndoc_type: issue\nid: ISSUE-9100\ntitle: 취소 테스트\ndate: 2026-09-15\n---\n\n# ISSUE-9100\n\n" + ("본문 문장. " * 200))
        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.05)
        pg.cancel(tok, "admin", "중지")
        t.join(60)
        self.assertNotIn("other", err, err.get("other"))
        # 취소되지 않았더라도(너무 빨라서) 다음 빌드는 정상 완료해야 한다
        with self.p.request_scope():
            res, _ = self.p.build(full=False, force=True)
        self.assertIsNone(res.get("error"))
        self.assertTrue(any(d["doc_id"].endswith("ISSUE-9100.md") for d in self.p.store.list_docs()))

    def test_watchdog_timeout_cancels(self):
        m = self._mgr(**{"timeouts.query_s": 1, "rate_limit.enabled": False})
        out = {}

        def run():
            try:
                with m.ticket("query", "read", client={"user": "u"}, label="느린 질의"):
                    for _ in range(100):
                        pg.tick(1, 100, "…")
                        time.sleep(0.1)
                out["status"] = "done"
            except pg.Cancelled:
                out["status"] = "cancelled"
        t = threading.Thread(target=run)
        t.start()
        t.join(20)
        self.assertEqual(out.get("status"), "cancelled")
        self.assertGreaterEqual(m.counters["timeouts"], 1)

    def test_sleep_cancellable(self):
        tok = "t-sleep"
        out = {}

        def run():
            pg.bind(tok, "x", "sleep")
            t0 = time.time()
            try:
                pg.sleep_cancellable(10)
                out["slept"] = time.time() - t0
            except pg.Cancelled:
                out["cancelled_after"] = time.time() - t0
            pg.unbind("done")
        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.3)
        pg.cancel(tok, "admin")
        t.join(10)
        self.assertIn("cancelled_after", out)
        self.assertLess(out["cancelled_after"], 3)


# ======================================================================== 4. 역할별 LLM 정책
class _SlowLLM(pv.BaseLLM):
    name = "slow"
    available = True
    model = "slow"

    def __init__(self, fail_times=99, sleep=0.0, kind="timeout"):
        pv.BaseLLM.__init__(self)
        self.fail_times = fail_times
        self.sleep = sleep
        self.kind = kind
        self.calls = 0

    def _complete(self, system, user, max_tokens, effort, json_mode):
        self.calls += 1
        if self.sleep:
            time.sleep(self.sleep)
        if self.calls <= self.fail_times:
            raise pv.LLMError("simulated %s" % self.kind, transient=True, kind=self.kind)
        return {"text": "OK", "usage": {"input_tokens": 1, "output_tokens": 1}, "ms": 1.0, "model": self.model}


class RolePolicyTest(_Base):
    def test_role_policy_resolution(self):
        s = Settings(llm_timeout=600, llm_retries=3, llm_retry_backoff_s=2.0, llm_budget_s=0,
                     llm_roles={"answer": {"model": "big", "timeout_s": 120, "retries": 1, "backoff": "linear", "budget_s": 200},
                                "rerank": {"model": "small"}})
        a = s.role_llm("answer")
        self.assertEqual((a["model"], a["timeout_s"], a["retries"], a["backoff"], a["budget_s"]), ("big", 120, 1, "linear", 200))
        r = s.role_llm("rerank")
        self.assertEqual((r["model"], r["timeout_s"], r["retries"], r["backoff"]), ("small", 600, 3, "exponential"))
        tbl = s.role_policy_table()
        self.assertEqual(set(tbl), set(Settings.LLM_ROLES))
        # 단축 키와 환경변수
        from llmwiki.config import apply_overrides, split_role_key
        apply_overrides(s, {"verify_timeout_s": 30, "verify_retries": 0})
        self.assertEqual(s.role_llm("verify")["timeout_s"], 30)
        self.assertEqual(s.role_llm("verify")["retries"], 0)
        self.assertEqual(split_role_key("answer_backoff_max_s"), ("answer", "backoff_max_s"))
        self.assertIsNone(split_role_key("web_port"))

    def test_make_llm_applies_per_role_policy(self):
        s = Settings(llm_provider="mock", llm_timeout=600, llm_retries=3,
                     llm_roles={"rerank": {"timeout_s": 15, "retries": 0, "circuit_failures": 0}})
        rr = pv.make_llm(s, "rerank")
        an = pv.make_llm(s, "answer")
        self.assertEqual((rr.timeout, rr.retries, rr.circuit_failures), (15, 0, 0))
        self.assertEqual((an.timeout, an.retries), (600, 3))
        self.assertEqual(rr.policy()["timeout_s"], 15)

    def test_retry_backoff_and_budget(self):
        llm = _SlowLLM(fail_times=99)
        llm.retries, llm.retry_backoff_s, llm.retry_backoff, llm.circuit_failures = 3, 0.05, "exponential", 0
        t0 = time.time()
        with self.assertRaises(pv.LLMError):
            llm.complete("s", "u")
        self.assertEqual(llm.calls, 4)                     # 1 + retries
        self.assertGreater(time.time() - t0, 0.05)
        # 예산 초과 → 재시도 중단
        llm2 = _SlowLLM(fail_times=99, sleep=0.12)
        llm2.retries, llm2.retry_backoff_s, llm2.budget_s, llm2.circuit_failures = 10, 0.01, 1, 0
        with self.assertRaises(pv.LLMError):
            llm2.complete("s", "u")
        self.assertLess(llm2.calls, 11)
        inc = pv.drain_incidents()
        self.assertTrue(any(i.get("budget_exhausted") for i in inc))

    def test_circuit_breaker(self):
        pv.circuit_reset()
        llm = _SlowLLM(fail_times=99)
        llm.retries, llm.retry_backoff_s, llm.circuit_failures, llm.circuit_cooldown_s = 0, 0, 2, 30
        for _ in range(2):
            with self.assertRaises(pv.LLMError):
                llm.complete("s", "u")
        calls_before = llm.calls
        with self.assertRaises(pv.LLMError) as cm:          # 회로가 열려 즉시 실패 (프로바이더 호출 없음)
            llm.complete("s", "u")
        self.assertEqual(cm.exception.kind, "circuit_open")
        self.assertEqual(llm.calls, calls_before)
        self.assertTrue(pv.circuit_all()["slow/slow"]["open"])
        pv.circuit_reset("slow/slow")
        self.assertFalse(pv.circuit_all().get("slow/slow", {}).get("open"))

    def test_failed_llm_still_answers(self):
        """answer 역할이 끝까지 실패해도 추출식 답변과 llm_report 로 결과를 낸다 (LLM 없이도 최선의 결과)."""
        bad = _SlowLLM(fail_times=99)
        bad.retries, bad.retry_backoff_s, bad.circuit_failures = 1, 0, 0
        self.p._llms["answer"] = bad
        with self.p.request_scope():
            res, _tr = self.p.query("ISSUE-2001 원인과 수정 CL", log=False)
        self.assertIn(res["answer_mode"], ("extractive", "insufficient"))
        self.assertTrue(res["answer"].strip())
        self.assertIsNotNone(res.get("llm_report"))
        self.assertTrue(any(f["role"] == "answer" for f in res["llm_report"]["failures"]))
        self.assertIn("추출식", "".join(x["fallback"] for x in res["llm_report"]["fallbacks"]))
        self.assertIn("⚠ LLM 실행 보고", res["answer"])


# ======================================================================== 5. 스케줄러
class SchedulerTest(_Base):
    def test_cron_and_every_parsing(self):
        import datetime as dt
        c = sch.CronSpec("30 3 * * *")
        self.assertTrue(c.matches(dt.datetime(2026, 9, 15, 3, 30)))
        self.assertFalse(c.matches(dt.datetime(2026, 9, 15, 3, 31)))
        nxt = c.next_after(dt.datetime(2026, 9, 15, 3, 30))
        self.assertEqual((nxt.day, nxt.hour, nxt.minute), (16, 3, 30))
        c2 = sch.CronSpec("*/15 * * * mon")
        self.assertTrue(c2.matches(dt.datetime(2026, 9, 14, 10, 45)))     # 월요일
        self.assertFalse(c2.matches(dt.datetime(2026, 9, 15, 10, 45)))    # 화요일
        self.assertEqual(sch.parse_every("30s"), 30)
        self.assertEqual(sch.parse_every("2h"), 7200)
        with self.assertRaises(ValueError):
            sch.parse_every("1s")
        with self.assertRaises(ValueError):
            sch.CronSpec("bad")
        t = {"name": "x", "every": "10m", "action": {"type": "build"}}
        sch.validate_task(t)
        self.assertAlmostEqual(sch.next_run(t, 1000.0, 1000.0), 1600.0, delta=1)
        with self.assertRaises(ValueError):
            sch.validate_task({"name": "y", "action": {"type": "build"}})
        with self.assertRaises(ValueError):
            sch.validate_task({"name": "y", "every": "1h", "action": {"type": "nope"}})

    def test_actions_python_query_http_build(self):
        script = os.path.join(self.tmp, "job.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write("import sys\nprint('hello', sys.argv[1:])\n")
        r = sch.run_action(self.p, {"name": "s", "action": {"type": "python", "script": script, "args": ["--x"], "timeout_s": 60}})
        self.assertEqual(r["exit"], 0)
        self.assertIn("hello", r["stdout"])
        with self.assertRaises(FileNotFoundError):
            sch.run_action(self.p, {"name": "s", "action": {"type": "python", "script": "nope.py"}})
        out = os.path.join(self.tmp, "q.md")
        with self.p.request_scope():
            r2 = sch.run_action(self.p, {"name": "q", "action": {"type": "query", "q": "ISSUE-2001 원인", "out": out, "log": False}})
        self.assertTrue(os.path.exists(out))
        self.assertIn("ISSUE-2001", open(out, encoding="utf-8").read())
        with self.p.request_scope():
            r3 = sch.run_action(self.p, {"name": "b", "action": {"type": "build", "full": False}})
        self.assertIn("mode", r3)
        r4 = sch.run_action(self.p, {"name": "m", "action": {"type": "maintenance", "action": "wal_checkpoint"}})
        self.assertTrue(r4["ok"])
        self.assertEqual(sch.action_weight({"action": {"type": "build", "full": True}}), ("exclusive", "full"))
        self.assertEqual(sch.action_weight({"action": {"type": "build"}}), ("soft", "incremental"))
        self.assertEqual(sch.action_weight({"action": {"type": "query"}}), ("read", ""))

    def test_wiki_feature_actions(self):
        """스케줄에서 위키의 주요 기능(evolve · memory · precompute · eval · trial · snapshot · wiki · forensic · embed_report)을 실행할 수 있다."""
        with self.p.request_scope():
            r = sch.run_action(self.p, {"name": "ev", "action": {"type": "evolve", "op": "status"}})
            self.assertIn("pending", r)
            r = sch.run_action(self.p, {"name": "mem", "action": {"type": "memory", "op": "status"}})
            self.assertIn("episodes", r)
            r = sch.run_action(self.p, {"name": "mem2", "action": {"type": "memory", "op": "decay"}})
            self.assertIsInstance(r, dict)
            r = sch.run_action(self.p, {"name": "fx", "action": {"type": "forensic", "op": "summary", "out": os.path.join(self.tmp, "fx.md")}})
            self.assertTrue(os.path.exists(os.path.join(self.tmp, "fx.md")))
            r = sch.run_action(self.p, {"name": "er", "action": {"type": "embed_report", "out": os.path.join(self.tmp, "emb.md")}})
            self.assertTrue(os.path.exists(os.path.join(self.tmp, "emb.md")))
            r = sch.run_action(self.p, {"name": "wk", "action": {"type": "wiki", "min_degree": 1}})
            self.assertIsInstance(r, dict)
            r = sch.run_action(self.p, {"name": "sn", "action": {"type": "snapshot", "op": "create", "tag": "auto:test", "keep": 2}})
            self.assertTrue(r.get("name"))
            r = sch.run_action(self.p, {"name": "sn2", "action": {"type": "snapshot", "op": "prune", "keep": 0}})
            self.assertIn("removed", r)
            r = sch.run_action(self.p, {"name": "ev2", "action": {"type": "evolve", "op": "auto_apply", "min_confidence": 0.99, "max_apply": 1, "kinds": ["pin"]}})
            self.assertIn("applied", r)
            r = sch.run_action(self.p, {"name": "pc", "action": {"type": "precompute", "op": "clear", "stale": False}})
            self.assertIn("removed", r)
            r = sch.run_action(self.p, {"name": "cli", "action": {"type": "cli", "argv": ["build", "verify"]}})
            self.assertEqual(r["exit"], 0)
        with self.assertRaises(ValueError):
            sch.run_action(self.p, {"name": "x", "action": {"type": "evolve", "op": "nope"}})
        # 가중치: 자동 적용은 배타, 조회는 읽기
        self.assertEqual(sch.action_weight({"action": {"type": "evolve", "op": "auto_apply"}})[0], "exclusive")
        self.assertEqual(sch.action_weight({"action": {"type": "evolve", "op": "review"}})[0], "read")
        self.assertEqual(sch.action_weight({"action": {"type": "snapshot"}})[0], "exclusive")
        for t in sch.ACTION_TYPES:
            self.assertIn(t, sch.HELP, "%s 의 설명이 HELP 에 없다 (Web UI 도움말)" % t)
            sch.validate_task({"name": "t", "every": "1h", "action": {"type": t}})

    def test_fetch_url_saves_and_skips_same_content(self):
        from http.server import BaseHTTPRequestHandler
        body = "<html><body>공지사항 본문</body></html>".encode("utf-8")

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        url = "http://127.0.0.1:%d/notice.html" % httpd.server_address[1]
        dest = os.path.join(self.corpus, "fetched")
        act = {"type": "fetch_url", "urls": [url], "dest": dest, "build_after": False}
        r = sch.run_action(self.p, {"name": "f", "action": act})
        self.assertEqual(len(r["written"]), 1)
        r2 = sch.run_action(self.p, {"name": "f", "action": act})
        self.assertEqual(len(r2["written"]), 0)
        self.assertEqual(len(r2["skipped"]), 1)

    def test_scheduler_runs_task_and_records_history(self):
        sch.save_tasks([{"name": "tick", "enabled": True, "every": "5s", "run_on_start": True,
                         "action": {"type": "maintenance", "action": "wal_checkpoint"}}])
        s = sch.Scheduler(self.p, None)
        self.addCleanup(s.stop)
        tok = s.run_now("tick", by="test")
        self.assertTrue(tok)
        for _ in range(100):
            if not s.running:
                break
            time.sleep(0.1)
        h = s.history(5)
        self.assertTrue(h)
        self.assertEqual(h[0]["name"], "tick")
        self.assertEqual(h[0]["status"], "done")
        rows = s.list_tasks()
        self.assertEqual(rows[0]["last_status"], "done")
        self.assertTrue(rows[0]["next_run"])
        with self.assertRaises(KeyError):
            s.run_now("nope")
        # 파일 편집 → 자동 재적재
        sch.save_tasks([{"name": "tick", "enabled": False, "every": "5s", "action": {"type": "maintenance", "action": "wal_checkpoint"}}])
        s._maybe_reload()
        self.assertFalse(s.list_tasks()[0]["enabled"])
        self.assertTrue(sch.remove_task("tick"))
        self.assertEqual(sch.list_tasks_static(), [])

    def test_invalid_task_is_reported_not_crashing(self):
        with open(sch.schedule_path(), "w", encoding="utf-8") as f:
            json.dump({"tasks": [{"name": "bad", "cron": "nonsense", "action": {"type": "build"}}]}, f)
        s = sch.Scheduler(self.p, None)
        self.addCleanup(s.stop)
        rows = s.list_tasks()
        self.assertTrue(any(r.get("invalid") for r in rows))


# ======================================================================== 6. 모델 카탈로그
class ModelCatalogTest(_Base):
    def test_list_add_remove(self):
        d = mc.describe(self.p.s)
        self.assertTrue(d["models"])
        self.assertTrue(os.path.exists(mc.catalog_path()))
        n = len(d["models"])
        mc.add_model({"id": "my-model", "provider": "ollama", "label": "테스트", "roles": ["answer"]})
        self.assertEqual(len(mc.list_models()), n + 1)
        self.assertTrue(any(m["id"] == "my-model" for m in mc.list_models(role="answer")))
        self.assertFalse(any(m["id"] == "my-model" for m in mc.list_models(role="rerank")))
        mc.add_model({"id": "my-model", "provider": "ollama", "label": "수정됨", "roles": []})
        self.assertEqual(len(mc.list_models()), n + 1)          # 같은 id+provider 는 갱신
        self.assertTrue(any(m["id"] == "my-model" for m in mc.list_models(role="rerank")))
        mc.remove_model("my-model")
        self.assertEqual(len(mc.list_models()), n)
        with self.assertRaises(ValueError):
            mc.remove_model("nope")
        with self.assertRaises(ValueError):
            mc.add_model({"provider": "ollama"})

    def test_unknown_model_in_use_is_reported(self):
        s = self.p.base_settings.copy()
        s.llm_provider = "ollama"
        s.llm_roles = {"answer": {"model": "totally-unknown-model"}}
        d = mc.describe(s)
        self.assertTrue(any(u["model"] == "totally-unknown-model" for u in d["unknown_in_use"]))


# ======================================================================== 7. 스트레스 (Web)
class StressTest(unittest.TestCase):
    """실제 HTTP 서버에 동시 요청을 보내 대기열·속도 제한·취소·빌드 중 질의를 확인한다."""
    KEYS = _Base.KEYS
    FILE_KEYS = _Base.FILE_KEYS

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.corpus = os.path.join(cls.tmp, "corpus")
        _gen_corpus(cls.corpus)
        for k in cls.KEYS:
            os.environ["LLMWIKI_%s_PATH" % k] = os.path.join(cls.tmp, k.lower() + (".json" if k in cls.FILE_KEYS else ""))
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        mc._CACHE["data"] = None
        A.reset_sessions()
        cls._tuning_path = tn.TUNING_PATH
        tn.TUNING_PATH = os.path.join(cls.tmp, "tuning.json")
        tn.load_tuning(tn.TUNING_PATH)
        # 로그를 DEBUG 로: 동시 요청에서 run_id 별 단계 로그가 섞이지 않는지 확인
        s = Settings(corpus_dirs=[cls.corpus], data_dir=os.path.join(cls.tmp, "data"), wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=256, log_level="DEBUG", keep_requests=5000)
        s.toggles = Toggles(query_cache=False, health_check=False, precompute=False)
        cls.s = s
        cls.p = Pipeline(s)
        cls.p.build(full=True)
        with open(os.path.join(cls.tmp, "server.json"), "w", encoding="utf-8") as f:
            json.dump({"concurrency": {"max_parallel_reads": 4, "max_parallel_per_user": 30, "max_parallel_per_ip": 60, "queue_max": 200, "queue_timeout_s": 120},
                       "rate_limit": {"enabled": False}, "timeouts": {"query_s": 0, "job_s": 0},
                       "monitor": {"live_dir": os.path.join(cls.tmp, "live")}}, f)
        rq._MANAGER = None
        cls.mgr = rq.get_manager()
        ws.Handler.pipe = cls.p
        ws.Handler.auth = A.Auth(s, "127.0.0.1")
        ws._SCHED["scheduler"] = None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ws.Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.mgr.stop()
        rq._MANAGER = None
        pg.set_publisher(None, None)
        cls.p.store.close()
        for k in cls.KEYS:
            os.environ.pop("LLMWIKI_%s_PATH" % k, None)
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        mc._CACHE["data"] = None
        A.reset_sessions()
        tn.load_tuning(os.path.join(cls.tmp, "none.json"))
        tn.TUNING_PATH = cls._tuning_path
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _req(self, method, path, body=None, timeout=180):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data,
                                     headers={"Content-Type": "application/json", "X-Requested-With": "llmwiki"}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")
            except Exception:
                return e.code, {}

    def test_30_concurrent_queries(self):
        """30명 동시 질의: 모두 200, 각자의 질문에 대한 답, 대기열을 거쳐도 유실 없음."""
        n = 30
        out = {}
        errs = []

        def run(i):
            try:
                code, j = self._req("POST", "/api/query", {"q": "ISSUE-200%d 의 원인은?" % (i % 3 + 1), "log": False,
                                                           "progress_token": "stress-%d" % i, "overrides": {"top_k_final": 3 + (i % 4)}})
                out[i] = (code, j)
            except Exception as e:  # noqa: BLE001
                errs.append("%s: %s" % (type(e).__name__, e))
        t0 = time.time()
        ths = [threading.Thread(target=run, args=(i,)) for i in range(n)]
        [t.start() for t in ths]
        [t.join(300) for t in ths]
        el = time.time() - t0
        self.assertEqual(errs, [])
        self.assertEqual(len(out), n)
        for i, (code, j) in out.items():
            self.assertEqual(code, 200, j)
            self.assertIn("result", j)
            self.assertIn("ISSUE-200%d" % (i % 3 + 1), j["result"]["query"])
            self.assertEqual(j["result"]["config"]["toggles"]["rerank"], self.s.toggles.rerank)
        # 동시 실행 상한이 지켜졌고(슬롯 4), 전부 처리됐다
        st = self.mgr.stats()
        self.assertGreaterEqual(st["counters"]["done_done"], n)
        self.assertEqual(st["running"], 0)
        self.assertEqual(st["queued"], 0)
        print("\n  [stress] %d concurrent queries in %.1fs (avg %.0f ms, p95 %.0f ms)" % (
            n, el, (st["latency"].get("query") or {}).get("avg_ms", 0), (st["latency"].get("query") or {}).get("p95_ms", 0)))

    def test_collab_traffic_does_not_disturb_queries(self):
        """협업(채팅·게시)은 **부수 기능**이다 — 질의와 함께 쏟아져도 질의를 막거나 느리게 하면 안 된다.

        채팅 폴링은 4초마다 모든 접속자가 보내므로, 이것이 읽기 슬롯을 잡으면 30명 환경에서 질의가 밀린다.
        그래서 collab 은 `read` 등급이지만 파이프라인 락을 쓰지 않는다 — 여기서 그것을 확인한다.
        """
        n_q, n_c = 8, 40
        res = {"q": [], "c": [], "err": []}

        def query(i):
            try:
                code, j = self._req("POST", "/api/query", {"q": "ISSUE-200%d 원인" % (i % 3 + 1), "log": False})
                res["q"].append((code, (j.get("result") or {}).get("ms")))
            except Exception as e:  # noqa: BLE001
                res["err"].append("query %s: %s" % (type(e).__name__, e))

        def chat(i):
            try:
                c1, _ = self._req("POST", "/api/collab", {"action": "say", "text": "부하 %d" % i})
                c2, _ = self._req("GET", "/api/collab")
                res["c"].append((c1, c2))
            except Exception as e:  # noqa: BLE001
                res["err"].append("collab %s: %s" % (type(e).__name__, e))

        ths = [threading.Thread(target=query, args=(i,)) for i in range(n_q)]
        ths += [threading.Thread(target=chat, args=(i,)) for i in range(n_c)]
        [t.start() for t in ths]
        [t.join(300) for t in ths]
        self.assertEqual(res["err"], [])
        self.assertEqual(len(res["q"]), n_q)
        self.assertTrue(all(code == 200 for code, _ in res["q"]), res["q"])     # 질의가 전부 성공
        self.assertTrue(all(a == 200 and b == 200 for a, b in res["c"]), res["c"][:3])
        # 채팅이 읽기 슬롯을 잡지 않았다 → 대기열에 쌓이지 않고 끝났다
        st = self.mgr.stats()
        self.assertEqual((st["running"], st["queued"]), (0, 0))
        # 가중치 자체를 못 박는다: collab 은 락 밖(none)이어야 한다.
        # read 로 두면 30명이 몇 초마다 폴링하는 것만으로 읽기 슬롯이 차서 질의가 밀린다
        # (2026-09-16 멍키 테스트에서 폭격 중 정상 질의가 0/22 로 떨어져 발견).
        from llmwiki import reqmgr as _rq
        self.assertEqual(_rq.weight_for_level("read", "collab say"), "none")
        self.assertEqual(_rq.weight_for_level("admin", "collab admin"), "none")
        self.assertEqual(_rq.weight_for_level("read", "/api/query"), "read")   # 질의는 그대로 읽기 슬롯

    def test_queries_during_build_and_activity_visible(self):
        """증분 빌드(soft 쓰기) 중에도 질의가 처리되고, 활동 목록에 빌드가 보인다."""
        with open(os.path.join(self.corpus, "issues", "ISSUE-9200.md"), "w", encoding="utf-8") as f:
            f.write("---\nschema_version: 1\ndoc_type: issue\nid: ISSUE-9200\ntitle: 동시 빌드\ndate: 2026-09-15\n---\n\n# ISSUE-9200\n\n" + ("문장. " * 300))
        code, j = self._req("POST", "/api/build", {"full": False})
        self.assertEqual(code, 200, j)
        job = j["job"]
        oks = []
        for _ in range(6):
            c2, j2 = self._req("POST", "/api/query", {"q": "ISSUE-2001 원인", "log": False})
            oks.append(c2)
            ca, ja = self._req("GET", "/api/activity")
            self.assertEqual(ca, 200)
            time.sleep(0.05)
        self.assertTrue(all(c == 200 for c in oks), oks)
        for _ in range(600):
            c3, j3 = self._req("GET", "/api/jobs/" + job)
            if j3.get("status") != "running":
                break
            time.sleep(0.1)
        self.assertEqual(j3["status"], "done", j3.get("error"))

    def test_cancel_running_job(self):
        """긴 작업을 DELETE /api/jobs/<id> 로 중지 → status=cancelled."""
        code, j = self._req("POST", "/api/build", {"full": True, "_confirm": True, "_phrase": "DELETE INDEX"})
        self.assertEqual(code, 200, j)
        job = j["job"]
        time.sleep(0.15)
        c2, j2 = self._req("DELETE", "/api/jobs/" + job)
        self.assertEqual(c2, 200, j2)
        for _ in range(900):
            c3, j3 = self._req("GET", "/api/jobs/" + job)
            if j3.get("status") != "running":
                break
            time.sleep(0.1)
        self.assertIn(j3["status"], ("cancelled", "done"))    # 아주 빠르면 이미 끝났을 수 있다
        # 색인은 여전히 쓸 수 있어야 한다 (취소가 DB 를 깨뜨리지 않음)
        c4, j4 = self._req("POST", "/api/build", {"full": False})
        self.assertEqual(c4, 200)
        for _ in range(900):
            c5, j5 = self._req("GET", "/api/jobs/" + j4["job"])
            if j5.get("status") != "running":
                break
            time.sleep(0.1)
        self.assertEqual(j5["status"], "done", j5.get("error"))
        c6, j6 = self._req("POST", "/api/query", {"q": "ISSUE-2001 원인", "log": False})
        self.assertEqual(c6, 200)
        self.assertTrue(j6["result"]["hits"])

    def test_rate_limit_and_maintenance_over_http(self):
        self.mgr.set_limits({"rate_limit.enabled": True, "rate_limit.query_per_user_per_min": 2, "rate_limit.exempt_roles": []}, save=False)
        try:
            codes = [self._req("POST", "/api/query", {"q": "ISSUE-2001", "log": False})[0] for _ in range(4)]
            self.assertIn(429, codes, codes)
        finally:
            self.mgr.set_limits({"rate_limit.enabled": False, "rate_limit.exempt_roles": ["admin"]}, save=False)
        self.mgr.set_limits({"access.maintenance_mode": True}, save=False)
        try:
            code, _j = self._req("POST", "/api/query", {"q": "ISSUE-2001", "log": False})
            self.assertEqual(code, 200, "점검 모드라도 maintenance_allow_roles 의 역할(admin)은 통과해야 한다")
            self.mgr.set_limits({"access.maintenance_allow_roles": ["nobody"]}, save=False)
            code, j = self._req("POST", "/api/query", {"q": "x", "log": False})
            self.assertEqual(code, 503)
            self.assertEqual(j.get("code"), "maintenance")
            code, j = self._req("GET", "/api/status")
            self.assertEqual(code, 503)          # 조회도 막힌다 (정적 파일·로그인은 제외)
        finally:
            self.mgr.set_limits({"access.maintenance_mode": False, "access.maintenance_allow_roles": ["admin"]}, save=False)
        code, j = self._req("POST", "/api/query", {"q": "ISSUE-2001 원인", "log": False})
        self.assertEqual(code, 200)

    def test_slow_query_shows_progress_and_can_be_cancelled(self):
        """LLM 이 느린(20초) 질의: 진행 표시에 'LLM 응답 대기' 가 뜨고, 중지하면 즉시 멈추며 서버는 계속 정상 동작한다."""
        slow = _SlowLLM(fail_times=0, sleep=20.0)
        slow.retries, slow.circuit_failures = 0, 0
        self.p._llms["answer"] = slow
        try:
            tok = "slow-q-1"
            res = {}

            def run():
                res["r"] = self._req("POST", "/api/query", {"q": "ISSUE-2001 원인", "log": False, "progress_token": tok}, timeout=120)
            t = threading.Thread(target=run)
            t.start()
            live = {}
            for _ in range(200):
                time.sleep(0.1)
                _c, live = self._req("GET", "/api/progress/" + tok)
                if (live.get("llm") or {}).get("active"):
                    break
            self.assertTrue((live.get("llm") or {}).get("active"), "LLM 대기 상태가 진행 표시에 나타나지 않았다: %s" % live)
            self.assertEqual(live.get("status"), "running")
            self.assertIsNotNone(live.get("elapsed_s"))
            # 활동 목록에도 보인다
            _c, act = self._req("GET", "/api/activity")
            self.assertTrue(any(r["token"] == tok for r in act["running"]))
            # 중지
            c2, j2 = self._req("DELETE", "/api/activity/" + tok)
            self.assertEqual(c2, 200, j2)
            t.join(60)
            code, j = res["r"]
            self.assertIn(code, (200, 499), j)      # 취소는 499, LLM 이 먼저 끝났으면 200
            if code == 499:
                self.assertTrue(j.get("cancelled"))
        finally:
            self.p._llms.pins.pop("answer", None)
        # 서버는 계속 정상
        code, j = self._req("POST", "/api/query", {"q": "ISSUE-2001 원인", "log": False})
        self.assertEqual(code, 200)
        self.assertTrue(j["result"]["hits"])

    def test_malformed_input_returns_400_not_500(self):
        """잘못된 모양의 입력은 400 으로 거절해야 한다 (500 은 서버 결함). 멍키 테스트가 찾은 회귀를 고정한다."""
        cases = [
            ("/api/query", {"q": "x", "overrides": "문자열"}),
            ("/api/query", {"q": "x", "overrides": 123}),
            ("/api/query", {"q": "x", "overrides": ["a"]}),
            ("/api/query", {"q": "x", "preset": {"a": 1}}),
            ("/api/query", {"q": "x", "mode": ["deep"]}),
            ("/api/search", {"q": "x", "k": "많이"}),
            ("/api/feedback", {"confidence": "0"}),
            ("/api/feedback", {"query_id": "abc", "feedback": "up"}),
            ("/api/evolve/propose", None),
            ("/api/evolve/propose", {"kind": "x", "payload": "문자열", "confidence": "높음"}),
            ("/api/evolve/apply", {"id": "abc"}),
            ("/api/evolve/reject", {}),
            ("/api/prompts", {"content": "x"}),
            ("/api/query_rules", {"rules": 3.14}),
            ("/api/pins", {"action": "add", "weight": "무거움"}),
            ("/api/tuning", {"values": "문자열"}),
            ("/api/models/catalog", {"action": "add", "model": "문자열"}),
            ("/api/schedule", {"action": "add", "task": "문자열"}),
            ("/api/activity", {"action": "cancel", "token": {"a": 1}}),
            ("/api/wiki/page", {"name": ["x"], "content": 1}),
            ("/api/trials", {"action": "run", "k": "다섯"}),
            ("/api/build/verify", {"fix": "네"}),
            ("/api/watch", {"action": "start", "interval": "빠르게"}),
            ("/api/mcp_sources", {"action": "retrieve", "q": {"x": 1}, "k": "셋"}),
            ("/api/cli", {"argv": {"a": 1}}),
        ]
        bad = []
        for path, body in cases:
            code, j = self._req("POST", path, body if body is not None else {})
            if code == 500:
                bad.append("%s %s → 500 %s" % (path, json.dumps(body, ensure_ascii=False)[:80], str(j.get("error"))[:120]))
            elif code not in (200, 400, 401, 403, 404, 428, 429, 503):
                bad.append("%s → 예상 밖 %s" % (path, code))
        self.assertEqual(bad, [], "잘못된 입력이 500 을 냈다:\n  " + "\n  ".join(bad))
        # 서버는 계속 정상
        code, j = self._req("POST", "/api/query", {"q": "ISSUE-2001 원인", "log": False})
        self.assertEqual(code, 200, "정상 질의가 %s: %s" % (code, json.dumps(j, ensure_ascii=False)[:300]))

    def test_user_profile_roundtrip(self):
        """계정별 Web UI 설정 프로파일: 저장·불러오기·허용 키만·서버 전역 설정 불변."""
        code, j = self._req("GET", "/api/profile")
        self.assertEqual(code, 200, j)
        self.assertIn("profile", j)
        prof = {"theme": "dark", "toggles": {"rerank": False}, "presets": ["speed"], "pins": ["server", "logs"],
                "split": True, "overrides": {"ov-k": "5"}, "bogus": "저장되면 안 됨"}
        code, j = self._req("POST", "/api/profile", {"action": "save", "profile": prof})
        self.assertEqual(code, 200, j)
        self.assertNotIn("bogus", j["profile"], "허용하지 않은 키가 저장됐다")
        self.assertEqual(j["profile"]["theme"], "dark")
        code, j = self._req("GET", "/api/profile")
        self.assertEqual(j["profile"]["pins"], ["server", "logs"])
        self.assertTrue(j["profile"]["split"])
        # 서버 전역 설정은 그대로 (프로파일은 그 사람의 화면 상태일 뿐)
        self.assertTrue(self.p.base_settings.toggles.rerank)
        code, j = self._req("POST", "/api/profile", {"action": "save", "profile": "문자열"})
        self.assertEqual(code, 400, j)
        code, j = self._req("POST", "/api/profile", {"action": "save", "profile": {"note": "가" * 40000}})
        self.assertEqual(code, 400, j)      # 크기 상한
        code, j = self._req("POST", "/api/profile", {"action": "reset"})
        self.assertEqual(code, 200, j)
        code, j = self._req("GET", "/api/profile")
        self.assertEqual(j["profile"], {})

    def test_debug_logs_keep_requests_separate(self):
        """DEBUG 레벨에서 동시 요청의 로그가 run_id 로 분리된다."""
        from llmwiki import logging_setup as ls
        ids = []

        def run(i):
            code, j = self._req("POST", "/api/query", {"q": "ISSUE-200%d 원인" % (i % 3 + 1), "log": False})
            if code == 200:
                ids.append(j["result"]["run_id"])
        ths = [threading.Thread(target=run, args=(i,)) for i in range(8)]
        [t.start() for t in ths]
        [t.join(120) for t in ths]
        self.assertEqual(len(ids), 8)
        self.assertEqual(len(set(ids)), 8, "run_id 가 겹쳤다 (요청 분리 실패)")
        path = os.path.join(ls.log_dir() or os.path.join(self.tmp, "logs_dir"), "query.log")
        rows = ls.grep(path, run_id=ids[0], limit=500)
        self.assertTrue(rows, "run_id 로 로그를 찾지 못했다")
        self.assertTrue(all(r.get("run_id") == ids[0] for r in rows))
        self.assertTrue(any(r.get("level") == "DEBUG" for r in ls.tail(path, 400)), "DEBUG 레벨 레코드가 없다")


class ConsoleCliLimitTest(unittest.TestCase):
    """Web 콘솔 CLI 가 읽기 슬롯을 무한정 잡지 못하게 한다.

    2026-09-15 회귀: `timeouts.cli_s` 가 0(무제한)이던 시절, 느린 CLI 두 개가 읽기 슬롯을 6분 넘게
    잡는 바람에 다른 사용자 64명이 전부 대기열에 쌓였다.
    """

    def test_query_string_ints_are_clamped(self):
        """자리수 제한 없는 정수가 그대로 SQLite 로 넘어가 500 이 나지 않는지.

        2026-09-16 회귀: `/api/trials?limit=999…999` 가
        `OverflowError: Python int too large to convert to C…` 로 500 이 났다.
        """
        from llmwiki.web.server import _qint
        big = "9" * 80
        self.assertEqual(_qint({"limit": big}, "limit", 50), 1_000_000, "상한으로 잘리지 않았다")
        self.assertEqual(_qint({"limit": "-5"}, "limit", 50), 0, "하한으로 잘리지 않았다")
        self.assertEqual(_qint({"limit": "abc"}, "limit", 50), 50, "숫자가 아니면 기본값이어야 한다")
        self.assertEqual(_qint({}, "limit", 50), 50)
        self.assertEqual(_qint({"limit": "7"}, "limit", 50), 7)
        # SQLite 가 실제로 받아들이는 범위인지
        con = __import__("sqlite3").connect(":memory:")
        con.execute("create table t(x)")
        con.execute("select * from t limit ?", (_qint({"limit": big}, "limit", 50),)).fetchall()
        con.close()

    def test_batch_jobs_do_not_hog_read_slots(self):
        """평가·trial 같은 배치 작업이 동시에 여러 개 돌아 대화형 질의를 밀어내지 않는지.

        2026-09-16 회귀: eval 4개가 9분째 읽기 슬롯을 모두 차지해 Web UI 가 응답하지 않았다.
        """
        import threading as _th
        from llmwiki import reqmgr
        tmp = tempfile.mkdtemp(prefix="llmwiki-batch-")
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = reqmgr.load_config(os.path.join(tmp, "server.json"))
        cfg["concurrency"]["max_parallel_reads"] = 8
        cfg["concurrency"]["max_parallel_batch"] = 1
        cfg["concurrency"]["queue_timeout_s"] = 3
        cfg["rate_limit"]["enabled"] = False
        mgr = reqmgr.RequestManager(cfg, path=os.path.join(tmp, "server.json"), install_publisher=False)
        self.addCleanup(mgr.stop)

        started, release, second = _th.Event(), _th.Event(), {"in": False}

        def batch_one():
            with mgr.ticket("eval", "read", client={"user": "a", "role": "admin"}, label="eval 1"):
                started.set()
                release.wait(10)

        def batch_two():
            try:
                with mgr.ticket("eval", "read", client={"user": "b", "role": "admin"}, label="eval 2"):
                    second["in"] = True
            except reqmgr.Rejected:
                second["rejected"] = True

        t1 = _th.Thread(target=batch_one, daemon=True); t1.start()
        self.assertTrue(started.wait(10), "첫 배치가 시작되지 않았다")
        t2 = _th.Thread(target=batch_two, daemon=True); t2.start()
        time.sleep(1.0)
        self.assertFalse(second["in"], "배치 두 번째가 동시에 실행됐다 (max_parallel_batch 무시)")
        # 그 동안에도 일반 질의는 바로 들어가야 한다
        ok = {"ran": False}
        with mgr.ticket("query", "read", client={"user": "c", "role": "viewer"}, label="q"):
            ok["ran"] = True
        self.assertTrue(ok["ran"], "배치가 도는 동안 대화형 질의가 막혔다")
        release.set()
        t1.join(10); t2.join(10)

    def test_body_size_is_capped(self):
        """거대한 본문을 다 받아 파싱하는 동안 슬롯·메모리를 잡지 않도록 미리 거절한다."""
        from llmwiki import reqmgr
        self.assertGreater(float(reqmgr.DEFAULTS["concurrency"]["max_body_mb"]), 0,
                           "max_body_mb 가 0 이면 3MB 본문이 그대로 처리되어 요청이 30초 넘게 걸린다")

    def test_default_cli_limit_is_bounded(self):
        from llmwiki import reqmgr
        self.assertGreater(float(reqmgr.DEFAULTS["timeouts"]["cli_s"]), 0,
                           "timeouts.cli_s 기본값이 0이면 느린 콘솔 명령이 읽기 슬롯을 계속 잡는다")
        self.assertEqual(float(reqmgr.DEFAULTS["timeouts"]["job_s"]), 0,
                         "빌드 같은 백그라운드 작업은 기본 무제한이어야 한다")

    def test_ticket_applies_the_limit(self):
        from llmwiki import reqmgr
        tmp = tempfile.mkdtemp(prefix="llmwiki-cli-limit-")
        self.addCleanup(shutil.rmtree, tmp, True)
        mgr = reqmgr.RequestManager(reqmgr.load_config(os.path.join(tmp, "server.json")),
                                    path=os.path.join(tmp, "server.json"), install_publisher=False)
        self.addCleanup(mgr.stop)
        with mgr.ticket("cli", "read", client={"user": "u1", "role": "viewer"}, label="cli:query") as t:
            self.assertEqual(float(t["limit_s"]), float(mgr.cfg["timeouts"]["cli_s"]))
        # 빌드처럼 오래 걸리는 CLI 는 서버가 job_s 를 명시적으로 넘긴다
        with mgr.ticket("cli", "none", client={"user": "u1", "role": "viewer"}, label="cli:build",
                        timeout_s=float(mgr.cfg["timeouts"]["job_s"])) as t:
            self.assertEqual(float(t["limit_s"]), 0.0)


class RolePreviewTest(unittest.TestCase):
    """권한 미리보기(admin 이 viewer 화면을 확인)는 **낮추기만** 해야 한다.

    쿠키에 서명을 하지 않는 이유가 바로 이것이다 — 위조해도 자기 권한을 줄일 뿐 올릴 수 없다.
    """

    def _u(self, role):
        from llmwiki.auth import User
        return User("someone", role, "local", 0.0)

    def test_lowers_only(self):
        from llmwiki.auth import apply_preview, PREVIEW_COOKIE
        admin = self._u("admin")
        got = apply_preview(admin, "%s=viewer" % PREVIEW_COOKIE)
        self.assertEqual(got.role, "viewer")
        self.assertEqual(got.preview_of, "admin")
        self.assertEqual(got.name, admin.name)

    def test_cannot_raise(self):
        from llmwiki.auth import apply_preview, PREVIEW_COOKIE
        for actual in ("viewer", "class3", "class2"):
            for want in ("admin", "builder", "class1"):
                with self.subTest(actual=actual, want=want):
                    got = apply_preview(self._u(actual), "%s=%s" % (PREVIEW_COOKIE, want))
                    self.assertEqual(got.role, actual, "권한이 올라갔다")
                    self.assertFalse(got.preview_of)

    def test_ignores_garbage_and_absent(self):
        from llmwiki.auth import apply_preview, PREVIEW_COOKIE
        admin = self._u("admin")
        for header in ("", "other=1", "%s=" % PREVIEW_COOKIE, "%s=nope" % PREVIEW_COOKIE,
                       "%s=../../etc" % PREVIEW_COOKIE):
            with self.subTest(header=header):
                self.assertEqual(apply_preview(admin, header).role, "admin")
        self.assertIsNone(apply_preview(None, "%s=viewer" % PREVIEW_COOKIE))

    def test_cookie_round_trip(self):
        from llmwiki.auth import preview_cookie, preview_role, PREVIEW_COOKIE
        c = preview_cookie("viewer")
        self.assertIn("%s=viewer" % PREVIEW_COOKIE, c)
        self.assertEqual(preview_role(c.split(";")[0]), "viewer")
        self.assertIn("Max-Age=0", preview_cookie(""))          # 해제
        self.assertIn("Secure", preview_cookie("viewer", https=True))


class SurrogateSafetyTest(unittest.TestCase):
    """짝 없는 서러게이트가 섞인 질의 하나가 서버 모니터를 영구히 망가뜨리지 않는지.

    2026-09-15 회귀: 퍼징이 보낸 '\\ud83d' 가 요청 라벨로 저장되자, 그 뒤 `/api/activity` 가
    JSON 으로 직렬화될 때마다 UnicodeEncodeError 로 400 을 냈다. 이력에서 빠질 때까지 admin 이
    서버 모니터를 볼 수 없었다.
    """

    BAD = "질의 \ud83d 그리고 \udfff 끝"

    def test_label_is_sanitized(self):
        from llmwiki import reqmgr
        tmp = tempfile.mkdtemp(prefix="llmwiki-surrogate-")
        self.addCleanup(shutil.rmtree, tmp, True)
        mgr = reqmgr.RequestManager(reqmgr.load_config(os.path.join(tmp, "server.json")),
                                    path=os.path.join(tmp, "server.json"), install_publisher=False)
        self.addCleanup(mgr.stop)
        with mgr.ticket("query", "read", client={"user": self.BAD, "role": "viewer", "ip": "1.2.3.4"},
                        label=self.BAD):
            act = mgr.activity()
            json.dumps(act, ensure_ascii=False).encode("utf-8")   # 여기서 터지면 회귀
        json.dumps(mgr.activity(), ensure_ascii=False).encode("utf-8")
        json.dumps(mgr.stats(), ensure_ascii=False).encode("utf-8")

    def test_request_body_is_scrubbed(self):
        """SQLite 는 짝 없는 서러게이트를 저장하지 못하므로 경계에서 걸러야 한다."""
        import sqlite3
        from llmwiki.web.server import _scrub
        from llmwiki.mcp import _scrub_surrogates
        raw = {"q": self.BAD, "overrides": {"k": [self.BAD, 3]}, self.BAD: "v"}
        for fn in (_scrub, _scrub_surrogates):
            out = fn(raw)
            json.dumps(out, ensure_ascii=False).encode("utf-8")      # 응답 직렬화
            con = sqlite3.connect(":memory:")
            con.execute("create table t(x text)")
            con.execute("insert into t values(?)", (out["q"],))      # DB 기록
            con.close()

    def test_progress_bind_is_sanitized(self):
        from llmwiki import progress as pg
        pg.bind("tok-surrogate", "query", self.BAD)
        try:
            snap = pg.get("tok-surrogate") or {}
            json.dumps(snap, ensure_ascii=False, default=str).encode("utf-8")
        finally:
            pg.unbind("done", "")


class AtomicWriteTest(unittest.TestCase):
    """설정 파일을 여러 요청이 동시에 저장해도 깨지지 않는지.

    2026-09-15 회귀: 고정된 `<파일>.tmp` 이름을 쓰던 시절, 두 관리자가 동시에 권한을 바꾸면
    Windows 에서 os.replace 가 `[WinError 5] 액세스가 거부되었습니다` 로 실패해 500 이 났다.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="llmwiki-atomic-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_concurrent_writers_never_corrupt(self):
        from llmwiki import atomicio
        p = os.path.join(self.tmp, "sec.json")
        errors, readers_bad = [], []
        stop = threading.Event()

        def writer(i):
            try:
                for n in range(25):
                    atomicio.write_json(p, {"who": i, "n": n, "payload": ["값%d" % n] * 40})
            except Exception as e:                       # noqa: BLE001 - 무엇이든 실패면 회귀
                errors.append("%s: %s" % (type(e).__name__, e))

        def reader():
            # 설정 파일 읽기는 모두 atomicio 를 거친다 — Windows 에서 열려 있는 파일은
            # os.replace 를 막으므로, 읽기도 같은 경로 락을 잡아야 저장이 굶지 않는다.
            while not stop.is_set():
                try:
                    d = atomicio.read_json(p)
                    if d is not None and (not isinstance(d, dict) or "who" not in d):
                        readers_bad.append(d)
                except Exception as e:                   # 반쪽 파일을 읽었다는 뜻
                    readers_bad.append(str(e))

        rs = [threading.Thread(target=reader, daemon=True) for _ in range(3)]
        [t.start() for t in rs]
        ws = [threading.Thread(target=writer, args=(i,)) for i in range(12)]
        [t.start() for t in ws]
        [t.join(60) for t in ws]
        stop.set()
        [t.join(5) for t in rs]
        self.assertEqual(errors, [], "동시 저장이 실패했다")
        self.assertEqual(readers_bad, [], "저장 중간 상태가 읽혔다 (원자성 깨짐)")
        with open(p, "r", encoding="utf-8") as f:
            self.assertIn("who", json.load(f))
        leftovers = [n for n in os.listdir(self.tmp) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [], "임시 파일이 남았다: %s" % leftovers)

    def test_security_json_concurrent_permission_changes(self):
        from llmwiki import auth
        os.environ["LLMWIKI_SECURITY"] = os.path.join(self.tmp, "security.json")
        self.addCleanup(os.environ.pop, "LLMWIKI_SECURITY", None)
        cfg = auth.load_security()
        errors = []

        def flip(i):
            try:
                for n in range(8):
                    c = auth.load_security()
                    c.setdefault("permissions", {}).setdefault("ops", {})["op%d" % i] = "admin" if n % 2 else "builder"
                    auth.save_security(c)
            except Exception as e:                       # noqa: BLE001
                errors.append("%s: %s" % (type(e).__name__, e))

        ths = [threading.Thread(target=flip, args=(i,)) for i in range(8)]
        [t.start() for t in ths]
        [t.join(60) for t in ths]
        self.assertEqual(errors, [], "security.json 동시 저장이 실패했다")
        self.assertIsInstance(auth.load_security().get("permissions"), dict)
        self.assertTrue(cfg is not None)


if __name__ == "__main__":
    unittest.main()
