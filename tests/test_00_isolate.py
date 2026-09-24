# -*- coding: utf-8 -*-
"""묶음 전체의 격리 — **discover 모드에서도** 실사용 원장·로그를 건드리지 않게 (2026-09-24, CODE_REVIEW_0924 §2.13).

왜 이 파일이 있나
  `tests/__init__.py` 가 같은 환경변수를 걸지만, `python -m unittest discover -s tests` 는 시작 폴더가 곧 최상위라
  **패키지 `__init__` 을 임포트하지 않는다** (실측: discover 뒤 'tests' not in sys.modules). 그래서 62개 모듈 중
  로그 폴더를 스스로 격리하지 않은 44개가 mock 질의 로그를 실사용 `logs/` 에 썼다 (한 번에 약 6,800줄).
  discover 는 모듈을 이름순으로 임포트하므로 `test_00_…` 이 가장 먼저 실행돼 나머지 모듈이 임포트되기 전에 환경을 건다.
  `python -m unittest tests.test_x` 처럼 패키지로 부르면 `__init__` 이 같은 일을 한다 — 두 경로 모두 덮는다.

규칙: 이미 지정된 값(CI·개별 테스트)은 존중한다(setdefault).
"""
import os
import tempfile
import unittest

_TMP = tempfile.gettempdir()
os.environ.setdefault("LLMWIKI_LEDGER_DIR_PATH", os.path.join(_TMP, "llmwiki_test_ledger"))
os.environ.setdefault("LLMWIKI_LOGS_DIR_PATH", os.path.join(_TMP, "llmwiki_test_logs"))
# 환경변수만으로는 부족했다: 17개 모듈이 tearDown 에서 `os.environ.pop("LLMWIKI_LOGS_DIR_PATH")` 로 지우면 위 기본값도 함께
# 사라져 그 뒤의 테스트가 실사용 logs/ 에 썼다(실측: 전체 한 번에 6,796줄). 그래서 pop 에 지워지지 않는 대체 기본값도 건다.
from llmwiki.config import set_path_fallback  # noqa: E402
set_path_fallback("logs_dir", os.path.join(_TMP, "llmwiki_test_logs"))


class IsolationTest(unittest.TestCase):
    def test_ledger_and_logs_point_outside_the_project(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for key in ("LLMWIKI_LEDGER_DIR_PATH", "LLMWIKI_LOGS_DIR_PATH"):
            v = os.environ.get(key, "")
            self.assertTrue(v, key + " 가 비어 있다 — 테스트가 실사용 폴더에 쓴다")
            self.assertFalse(os.path.abspath(v).startswith(os.path.join(root, "data")) or os.path.abspath(v).startswith(os.path.join(root, "logs")),
                             "%s=%s 가 프로젝트 안을 가리킨다" % (key, v))

    def test_logs_dir_resolution_honours_the_env(self):
        from llmwiki.config import path_for
        self.assertEqual(os.path.normcase(os.path.abspath(path_for("logs_dir"))), os.path.normcase(os.path.abspath(os.environ["LLMWIKI_LOGS_DIR_PATH"])))

    def test_fallback_survives_a_fixture_popping_the_env(self):
        """어느 테스트가 tearDown 에서 환경변수를 지워도 실사용 logs/ 로 떨어지지 않는다."""
        from llmwiki.config import path_for
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        saved = os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
        try:
            self.assertFalse(os.path.normcase(os.path.abspath(path_for("logs_dir"))).startswith(os.path.normcase(os.path.join(root, "logs"))),
                             "환경변수가 지워지자 실사용 logs/ 로 떨어졌다")
        finally:
            if saved is not None:
                os.environ["LLMWIKI_LOGS_DIR_PATH"] = saved


if __name__ == "__main__":
    unittest.main()
