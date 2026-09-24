# -*- coding: utf-8 -*-
"""요청 원장이 **자기 환경에만** 기록하는가 (2026-09-24).

왜 이 테스트가 있나
  요청 원장은 모듈 전역 상태(`reqledger._STATE`)로 폴더를 기억한다. 임시 설정으로 만든
  `RequestManager` 가 `data_dir` 을 모르면 **프로젝트의 `data/ledger`** 로 떨어지고,
  한 번 그렇게 설정되면 같은 프로세스의 뒤따르는 CLI 경로 기록까지 전부 그리로 간다.
  실제로 단위 테스트 한 번에 800줄 넘게 실사용 원장에 섞여 들어갔다 — 그러면 원장을 근거로
  장애를 분석할 수 없다. 원장은 "요청이 사라지지 않게" 하는 기능이므로 신뢰성이 전부다.

여기서 지키는 것
  1. `ledger.dir` 을 준 설정으로 만든 RequestManager 는 그 폴더에만 쓴다.
  2. `LLMWIKI_LEDGER_DIR_PATH` 가 설정을 이긴다 (하네스·테스트가 한 줄로 격리할 수 있어야 한다).
  3. `configure()` 를 부르지 않은 프로세스는 아무 데도 쓰지 않는다.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import reqledger as L, reqmgr  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_LEDGER = os.path.join(ROOT, "data", "ledger")


def _project_ledger_size() -> int:
    """프로젝트 원장의 총 바이트 — 테스트가 여기에 한 글자라도 쓰면 늘어난다."""
    if not os.path.isdir(PROJECT_LEDGER):
        return 0
    return sum(os.path.getsize(os.path.join(PROJECT_LEDGER, f))
               for f in os.listdir(PROJECT_LEDGER) if f.endswith(".jsonl"))


class LedgerIsolationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="llmwiki-led-iso-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._env = os.environ.get("LLMWIKI_LEDGER_DIR_PATH")
        self._cfg, self._dir = L._STATE.get("cfg"), L._STATE.get("dir")
        self.addCleanup(self._restore)
        # 묶음 전체는 tests/__init__.py 가 이미 격리해 둔다. 여기서는 **그 안전망을 걷어내고**
        # 설정만으로도 격리되는지를 본다 — 안전망에 기대면 진짜 회귀를 놓친다.
        os.environ.pop("LLMWIKI_LEDGER_DIR_PATH", None)
        self.before = _project_ledger_size()

    def _restore(self):
        if self._env is None:
            os.environ.pop("LLMWIKI_LEDGER_DIR_PATH", None)
        else:
            os.environ["LLMWIKI_LEDGER_DIR_PATH"] = self._env
        L._STATE["cfg"], L._STATE["dir"] = self._cfg, self._dir

    def _assert_project_untouched(self):
        self.assertEqual(self.before, _project_ledger_size(),
                         "테스트가 실제 운영 원장(data/ledger)에 기록했다 — "
                         "설정에 ledger.dir 을 주거나 LLMWIKI_LEDGER_DIR_PATH 로 격리해야 한다")

    def test_manager_writes_only_into_its_own_dir(self):
        """설정에 ledger.dir 을 주면 그 폴더에만 남는다."""
        cfg = reqmgr.load_config(os.path.join(self.tmp, "server.json"))
        cfg.setdefault("ledger", {})["dir"] = os.path.join(self.tmp, "ledger")
        mgr = reqmgr.RequestManager(cfg, path=os.path.join(self.tmp, "server.json"), install_publisher=False)
        self.addCleanup(mgr.stop)
        with mgr.ticket("query", "read", client={"user": "iso", "role": "viewer"}, label="격리 확인"):
            pass
        L.flush(3.0)
        mine = os.path.join(self.tmp, "ledger")
        self.assertTrue(os.path.isdir(mine) and os.listdir(mine), "자기 폴더에 기록되지 않았다")
        self._assert_project_untouched()

    def test_env_var_overrides_config(self):
        """LLMWIKI_LEDGER_DIR_PATH 가 설정보다 세다 — 하네스가 한 줄로 격리할 수 있어야 한다."""
        forced = os.path.join(self.tmp, "forced")
        os.environ["LLMWIKI_LEDGER_DIR_PATH"] = forced
        L.configure({"enabled": True, "dir": os.path.join(self.tmp, "ignored")}, data_dir=self.tmp)
        self.assertEqual(os.path.normpath(forced), os.path.normpath(L.ledger_dir()))
        L.open_(L.new_token(), kind="query", label="env 확인")
        L.flush(3.0)
        self.assertTrue(os.path.isdir(forced) and os.listdir(forced))
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "ignored")))
        self._assert_project_untouched()

    def test_unconfigured_process_writes_nothing(self):
        """configure() 를 부르지 않았으면 꺼져 있다 — Pipeline 만 만드는 도구가 오염시키지 못하게."""
        L._STATE["cfg"] = None
        self.assertFalse(L.enabled())
        L.open_(L.new_token(), kind="query", label="쓰이면 안 된다")
        L.flush(1.0)
        self._assert_project_untouched()


if __name__ == "__main__":
    unittest.main()
