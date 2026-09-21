# -*- coding: utf-8 -*-
"""빌드 중 읽기 정책 (`concurrency.reads_during_build`) — 켜면 **실제로** 동작하는가.

왜 이 테스트가 있나 (2026-09-19):
  문서(CONCURRENCY.md §문제 해결)가 "질의가 갑자기 다 느려짐 → `reads_during_build=always`" 를 권하고
  있었는데, **그 설정이 정확히 그 상황에서 무효**였다. 완화 분기가 `weight == "write"` 만 보고 있었고
  전체/채널 리빌드는 `weight == "exclusive"` 로 들어오기 때문이다. 30명 부하 실측에서 `always` 를 켜도
  질의가 빌드 시간만큼(5.5초) 그대로 기다리는 것으로 드러났다.

  이 프로젝트에서 가장 비싼 결함 유형이다 — **설정을 켰는데 아무 일도 일어나지 않는 것.**
  화면에도 문서에도 있으니 고쳐진 줄 알고 넘어간다. 그래서 락 모드를 직접 본다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import reqmgr as R      # noqa: E402


def lock_mode(policy, weight, build_mode):
    """`ticket()` 이 고르는 락 모드를 같은 규칙으로 재현한다.

    실제 티켓을 잡으면 스레드·슬롯·레지스트리가 얽히므로, **정책 결정 부분만** 떼어 본다.
    reqmgr.ticket() 의 해당 줄과 이 함수가 어긋나면 아래 test_matches_the_implementation 이 잡는다.
    """
    mode = "exclusive"
    if weight == "soft" and policy in ("incremental", "always"):
        mode = "soft"
    elif policy == "always" and build_mode and weight in ("exclusive", "write"):
        mode = "soft"
    return mode


class PolicyTest(unittest.TestCase):
    def test_default_incremental_keeps_full_rebuild_exclusive(self):
        """기본값에서 전체 리빌드는 배타다 — 질의가 부분 색인을 보면 안 된다."""
        self.assertEqual(lock_mode("incremental", "exclusive", "full"), "exclusive")

    def test_default_incremental_allows_reads_during_incremental_build(self):
        self.assertEqual(lock_mode("incremental", "soft", "incremental"), "soft")

    def test_default_incremental_allows_reads_during_channel_build(self):
        """채널 빌드는 server.py 가 soft 로 넣는다 (full 이 아닐 때)."""
        self.assertEqual(lock_mode("incremental", "soft", "channel"), "soft")

    def test_always_now_relaxes_the_full_rebuild(self):
        """이 한 줄이 고친 것이다 — 예전에는 exclusive 그대로였다."""
        self.assertEqual(lock_mode("always", "exclusive", "full"), "soft")

    def test_always_does_not_relax_non_builds(self):
        """설정 저장·스냅샷 복원은 build_mode 가 비어 있다 — 완화 대상이 아니다."""
        self.assertEqual(lock_mode("always", "exclusive", ""), "exclusive")

    def test_never_makes_even_incremental_exclusive(self):
        self.assertEqual(lock_mode("never", "soft", "incremental"), "exclusive")
        self.assertEqual(lock_mode("never", "exclusive", "full"), "exclusive")

    def test_matches_the_implementation(self):
        """위 재현이 `reqmgr.ticket()` 의 실제 코드와 같은지 — 소스를 읽어 확인한다.

        정책 분기가 바뀌면 이 테스트가 먼저 깨진다 (재현이 낡은 채로 통과하는 일을 막는다).
        """
        import inspect
        src = inspect.getsource(R.RequestManager.ticket)
        self.assertIn('if weight == "soft" and pol in ("incremental", "always")', src)
        self.assertIn('elif pol == "always" and build_mode and weight in ("exclusive", "write")', src)


class DefaultsTest(unittest.TestCase):
    def test_policy_default_is_incremental(self):
        self.assertEqual(R.DEFAULTS["concurrency"]["reads_during_build"], "incremental")

    def test_read_wait_is_bounded(self):
        """읽기가 배타 작업을 무한히 기다리면 스레드가 쌓여 서버가 마비된다."""
        self.assertGreater(R.DEFAULTS["concurrency"]["read_wait_timeout_s"], 0)

    def test_weight_mapping(self):
        self.assertEqual(R.weight_for_level("read"), "read")
        self.assertEqual(R.weight_for_level("run"), "read")
        self.assertEqual(R.weight_for_level("index"), "soft")
        self.assertEqual(R.weight_for_level("rebuild"), "exclusive")
        self.assertEqual(R.weight_for_level("destructive"), "exclusive")
        self.assertEqual(R.weight_for_level("read", "collab:post"), "none")


if __name__ == "__main__":
    unittest.main()
