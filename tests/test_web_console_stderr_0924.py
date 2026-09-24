# -*- coding: utf-8 -*-
"""Web 콘솔(run_captured)이 argparse 의 사용법 오류를 **응답 본문**에 담는가 (2026-09-24, CODE_REVIEW_0924 §2.10).

무엇이 문제였나
  `run_captured` 는 stdout 만 잡았다. 존재하지 않는 명령을 콘솔에서 치면 argparse 가 오류를 **서버 프로세스의 stderr** 로 써서
  (1) 콘솔에는 code=2 에 빈 출력만 보였고, (2) 서버를 띄운 쪽이 stderr 파이프를 읽지 않으면 버퍼가 찬 순간 그 쓰기가 영원히 막혔다.
  막힌 스레드가 배타 잠금을 쥐고 있어 서버 전체가 28분 넘게 멎었다(멍키 테스트 실측).

무엇을 확인하나
  1. 알 수 없는 명령·잘못된 플래그의 오류 문구가 `output` 에 들어 있고 code 가 2 다.
  2. 실행 중 프로세스의 sys.stderr 에는 아무것도 쓰이지 않는다.
  3. `--help` 는 여전히 code 0 에 사용법을 돌려준다.
"""
import io
import sys
import unittest
from contextlib import redirect_stderr

from llmwiki.cli import run_captured


class WebConsoleStderrTest(unittest.TestCase):
    def _run(self, argv):
        leaked = io.StringIO()
        with redirect_stderr(leaked):          # run_captured 가 잡지 못한 stderr 출력이 여기로 온다
            r = run_captured(argv, None, None)
        return r, leaked.getvalue()

    def test_unknown_command_error_goes_to_output_not_stderr(self):
        r, leaked = self._run(["nonexistent"])
        self.assertEqual(r["code"], 2)
        self.assertIn("invalid choice", r["output"])
        self.assertIn("nonexistent", r["output"])
        self.assertEqual(leaked, "", "argparse 오류가 서버 stderr 로 새어 나갔다")

    def test_bogus_flag_error_goes_to_output(self):
        r, leaked = self._run(["--bogus-flag"])
        self.assertEqual(r["code"], 2)
        self.assertIn("bogus-flag", r["output"])
        self.assertEqual(leaked, "")

    def test_help_still_works(self):
        r, leaked = self._run(["--help"])
        self.assertEqual(r["code"], 0)
        self.assertIn("usage", r["output"].lower())
        self.assertEqual(leaked, "")
        self.assertIs(sys.stderr.__class__, sys.__stderr__.__class__)   # 원래 stderr 가 복구됐다


if __name__ == "__main__":
    unittest.main()
