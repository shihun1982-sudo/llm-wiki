# -*- coding: utf-8 -*-
"""로그 총량 제한(2026-09-18, 요청 11): log_total_max_mb / log_limit_action(warn|prune|stop) / audit 로테이션 / analysis_keep.

작은 상한(1 MB) 과 log_check_interval_s=0(매 레코드 점검) 으로 세 동작을 확인한다:
  (a) warn  → quota_status()["over"] 가 True, error.log 에 'log quota exceeded' 한 줄
  (b) prune → 오래된 *.log.N 부터 지워 80% 아래로 (현재 파일·analysis 리포트는 남음)
  (c) stop  → INFO 는 llmwiki.log 에 안 남고 WARNING 은 error.log 에 남음, 80% 아래로 내려가면 재개
  (d) audit.jsonl 이 audit_max_mb 를 넘으면 audit.jsonl.1 로 로테이션 (audit_backups 개까지)
  (e) analysis_keep — 오래된 리포트(md+json 한 쌍) 부터 지움
  (f) setup_logging 이 max_mb/backups 가 바뀌면 핸들러를 다시 만든다 · setup_from_settings 는 path_for("logs_dir") 를 쓴다
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki import logging_setup as ls  # noqa: E402
from llmwiki import auth as AU  # noqa: E402
from llmwiki import analysis as AN  # noqa: E402
from llmwiki.config import Settings, path_for  # noqa: E402

MB = 1024 * 1024


def _fill(path: str, nbytes: int, age_s: float = 0.0) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * nbytes)
    if age_s:
        t = time.time() - age_s
        os.utime(path, (t, t))


def _lines(path: str):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    out.append({"msg": ln})
    return out


class LogQuotaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lwq_")
        self.logs = os.path.join(self.tmp, "logs")
        ls.shutdown_logging()

    def tearDown(self):
        ls.shutdown_logging()
        ls.configure_quota(500, "warn", 60)
        AU.configure_audit(20, 5)
        os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup(self, action: str, limit_mb: int = 1):
        ls.setup_logging(self.logs, "INFO", max_mb=10, backups=10, total_max_mb=limit_mb, limit_action=action, check_interval_s=0)
        # 핸들러 생성 직후 상태 (파일들이 비어 있으니 over=False)
        self.assertFalse(ls.quota_status()["over"])

    # (a) warn
    def test_warn_sets_over_and_writes_error_log(self):
        self._setup("warn")
        _fill(os.path.join(self.logs, "llmwiki.log.1"), int(1.2 * MB))
        ls.log("info", "after-fill", "test")
        q = ls.quota_status()
        self.assertTrue(q["over"], q)
        self.assertFalse(q["stopped"])
        self.assertEqual(q["action"], "warn")
        self.assertGreater(q["total_mb"], 1.0)
        errs = _lines(os.path.join(self.logs, "error.log"))
        self.assertTrue(any(r.get("msg") == "log quota exceeded" for r in errs), errs)
        # warn 은 지우지 않는다 · INFO 도 계속 남는다
        self.assertTrue(os.path.exists(os.path.join(self.logs, "llmwiki.log.1")))
        self.assertTrue(any(r.get("msg") == "after-fill" for r in _lines(os.path.join(self.logs, "llmwiki.log"))))
        # 상한 0 = 제한 없음
        ls.configure_quota(0, "warn", 0)
        ls.log("info", "no-limit", "test")
        self.assertFalse(ls.quota_status()["over"])
        self.assertFalse(ls.quota_status()["enabled"])

    # (b) prune
    def test_prune_deletes_oldest_backups_until_low_water(self):
        self._setup("prune")
        _fill(os.path.join(self.logs, "llmwiki.log.1"), 500 * 1024, age_s=100)
        _fill(os.path.join(self.logs, "llmwiki.log.2"), 500 * 1024, age_s=200)
        _fill(os.path.join(self.logs, "llmwiki.log.3"), 500 * 1024, age_s=300)
        _fill(os.path.join(self.logs, "analysis", "req_1.md"), 100 * 1024, age_s=1000)   # 백업보다 오래됐지만 백업을 먼저 지운다
        ls.log("info", "trigger", "test")
        q = ls.quota_status()
        self.assertFalse(q["over"], q)
        self.assertLessEqual(q["total_mb"], 0.8)
        self.assertFalse(os.path.exists(os.path.join(self.logs, "llmwiki.log.3")))
        self.assertFalse(os.path.exists(os.path.join(self.logs, "llmwiki.log.2")))
        self.assertTrue(os.path.exists(os.path.join(self.logs, "llmwiki.log.1")))
        self.assertTrue(os.path.exists(os.path.join(self.logs, "analysis", "req_1.md")))
        self.assertTrue(os.path.exists(os.path.join(self.logs, "llmwiki.log")))
        self.assertEqual(q["pruned"], ["llmwiki.log.3", "llmwiki.log.2"])
        errs = _lines(os.path.join(self.logs, "error.log"))
        self.assertTrue(any(r.get("msg") == "log quota prune" for r in errs), errs)
        # 백업이 다 없어져도 부족하면 analysis → audit 백업 순
        _fill(os.path.join(self.logs, "audit.jsonl.1"), 600 * 1024, age_s=50)
        _fill(os.path.join(self.logs, "analysis", "req_2.md"), 300 * 1024, age_s=10)
        ls.log("info", "trigger2", "test")
        q = ls.quota_status()
        self.assertFalse(os.path.exists(os.path.join(self.logs, "llmwiki.log.1")))
        self.assertFalse(os.path.exists(os.path.join(self.logs, "analysis", "req_1.md")))
        self.assertLessEqual(q["total_mb"], 0.8)

    # (c) stop
    def test_stop_drops_info_but_keeps_error_log(self):
        self._setup("stop")
        _fill(os.path.join(self.logs, "llmwiki.log.1"), int(1.2 * MB))
        ls.log("info", "hello-info-1", "test")
        ls.log("warning", "hello-warn", "test")
        q = ls.quota_status()
        self.assertTrue(q["stopped"], q)
        self.assertTrue(q["over"])
        main = _lines(os.path.join(self.logs, "llmwiki.log"))
        errs = _lines(os.path.join(self.logs, "error.log"))
        self.assertFalse(any(r.get("msg") == "hello-info-1" for r in main), main)
        self.assertFalse(any(r.get("msg") == "hello-warn" for r in main), main)     # stop 중엔 llmwiki.log 에는 WARNING 도 안 남는다
        self.assertTrue(any(r.get("msg") == "hello-warn" for r in errs), errs)
        self.assertTrue(any(r.get("msg") == "log quota exceeded" and (r.get("data") or {}).get("stopped") for r in errs), errs)
        # 80% 아래로 내려가면 재개
        os.remove(os.path.join(self.logs, "llmwiki.log.1"))
        ls.log("info", "hello-info-2", "test")
        q = ls.quota_status()
        self.assertFalse(q["stopped"], q)
        self.assertFalse(q["over"])
        main = _lines(os.path.join(self.logs, "llmwiki.log"))
        self.assertTrue(any(r.get("msg") == "hello-info-2" for r in main), main)
        self.assertTrue(any(r.get("msg") == "log quota resumed" for r in _lines(os.path.join(self.logs, "error.log"))))

    # check_quota / format_quota / 핸들러 없는 프로세스
    def test_check_quota_without_handlers_only_measures(self):
        ls.shutdown_logging()
        ls.configure_quota(1, "prune", 0)
        _fill(os.path.join(self.logs, "llmwiki.log.1"), int(1.5 * MB), age_s=10)
        q = ls.check_quota(force=True, dir_hint=self.logs)
        self.assertTrue(q["over"])
        self.assertTrue(os.path.exists(os.path.join(self.logs, "llmwiki.log.1")))   # 핸들러가 없으면 재기만 하고 지우지 않는다
        self.assertEqual(q["dir"], self.logs)
        txt = ls.format_quota(q)
        self.assertIn("OVER", txt)
        self.assertIn("log_limit_action=prune", txt)

    # (d) audit 로테이션
    def test_audit_rotation(self):
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = self.logs
        self.assertEqual(path_for("logs_dir"), self.logs)
        AU.configure_audit(0.01, 2)   # 10 KB
        for i in range(300):
            AU.write_audit(None, "op%d" % i, "edit", True, ip="127.0.0.1", detail={"i": i, "pad": "p" * 100})
        p = os.path.join(self.logs, "audit.jsonl")
        self.assertTrue(os.path.exists(p))
        self.assertTrue(os.path.exists(p + ".1"))
        self.assertTrue(os.path.exists(p + ".2"))
        self.assertFalse(os.path.exists(p + ".3"))
        self.assertLessEqual(os.path.getsize(p), 0.01 * MB + 1024)
        for fn in (p, p + ".1", p + ".2"):
            for r in _lines(fn):
                self.assertIn("op", r)
        # 마지막 줄은 최신 파일에
        self.assertEqual(_lines(p)[-1]["op"], "op299")
        self.assertTrue(AU.Auth.audit_tail(5))
        # backups=0 → 비운다
        AU.configure_audit(0.001, 0)
        for i in range(30):
            AU.write_audit(None, "z%d" % i, "edit", True, detail={"pad": "p" * 100})
        self.assertLessEqual(os.path.getsize(p), 0.001 * MB + 512)
        self.assertFalse(os.path.exists(p + ".3"))

    # (e) analysis_keep
    def test_analysis_keep_prunes_oldest_reports(self):
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = self.logs
        d = AN.analysis_dir()
        for i in range(1, 6):
            _fill(os.path.join(d, "req_%d.md" % i), 100, age_s=600 - i * 60)
            _fill(os.path.join(d, "req_%d.json" % i), 100, age_s=600 - i * 60)
        removed = AN.prune_reports(2)
        self.assertEqual(sorted(removed), sorted(["req_1.md", "req_1.json", "req_2.md", "req_2.json", "req_3.md", "req_3.json"]))
        left = sorted(os.listdir(d))
        self.assertEqual(left, ["req_4.json", "req_4.md", "req_5.json", "req_5.md"])
        self.assertEqual(AN.prune_reports(0), [])      # 0 = 무제한
        self.assertEqual(AN.prune_reports(10), [])     # 이미 이하

    # (f) 재구성 조건 · setup_from_settings
    def test_setup_reconfigures_when_params_change(self):
        ls.setup_logging(self.logs, "INFO", max_mb=10, backups=10, check_interval_s=0)
        h0 = list(ls._STATE["handlers"])
        ls.setup_logging(self.logs, "INFO", max_mb=10, backups=10, check_interval_s=0)
        self.assertEqual(h0, ls._STATE["handlers"])                      # 같은 파라미터 → 그대로
        ls.setup_logging(self.logs, "INFO", max_mb=3, backups=2, check_interval_s=0)
        self.assertNotEqual(h0, ls._STATE["handlers"])                   # max_mb/backups 변경 → 재구성
        fh = [h for h in ls._STATE["handlers"] if hasattr(h, "maxBytes")]
        self.assertTrue(fh and all(h.maxBytes == 3 * MB and h.backupCount == 2 for h in fh))
        ls.setup_logging(self.logs, "INFO", max_mb=3, backups=2, console=True, check_interval_s=0)
        self.assertEqual(len(ls._STATE["handlers"]), 5)                  # console 추가
        # 총량 설정만 바뀌면 핸들러는 유지하고 설정만 반영
        h1 = list(ls._STATE["handlers"])
        ls.setup_logging(self.logs, "INFO", max_mb=3, backups=2, console=True, total_max_mb=7, limit_action="stop", check_interval_s=5)
        self.assertEqual(h1, ls._STATE["handlers"])
        q = ls.quota_status()
        self.assertEqual((q["limit_mb"], q["action"], q["interval_s"]), (7, "stop", 5.0))
        # setup_from_settings → path_for("logs_dir") (LLMWIKI_LOGS_DIR_PATH)
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(self.tmp, "logs2")
        s = Settings()
        s.log_total_max_mb, s.log_limit_action, s.audit_max_mb, s.audit_backups = 9, "prune", 3, 1
        d = ls.setup_from_settings(s)
        self.assertEqual(d, os.path.join(self.tmp, "logs2"))
        self.assertEqual(ls.log_dir(), d)
        self.assertEqual(ls.quota_status()["action"], "prune")
        self.assertEqual((AU._AUDIT_CFG["max_mb"], AU._AUDIT_CFG["backups"]), (3.0, 1))
        # 잘못된 action 은 warn 으로
        ls.configure_quota(5, "bogus", 1)
        self.assertEqual(ls.quota_status()["action"], "warn")


if __name__ == "__main__":
    unittest.main()
