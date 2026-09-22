# -*- coding: utf-8 -*-
"""앙상블이 실패했을 때 **역할 모델로 되돌아가는지** (2026-09-20 요청).

왜 필요한가: 앙상블을 켜면 `make_llm()` 이 역할 모델 대신 `EnsembleLLM` 을 돌려주므로,
멤버가 전부 죽으면 그 역할은 답을 아예 못 낸다(answer 는 추출식으로 떨어진다). 역할 모델은
멀쩡히 설정돼 있는데도 쓰이지 않는 것이다. 그래서 `ensemble.fallback_role_model` 로 되돌아가되,
**그때까지 성공한 멤버 답을 살릴지(merge) 새 프롬프트로 다시 돌릴지(rerun)** 를 고를 수 있게 했다.

여기서 확인하는 것:
  1. 설정 계층 — 기본값·상속·정규화가 실제로 `effective_ensemble` 까지 온다
  2. 동작 — 멤버가 죽었을 때 폴백이 **실제로 불린다** (설정만 있고 아무 일도 안 하면 안 된다)
  3. fallback_mode 세 값이 **서로 다르게** 동작한다 (merge 는 멤버 답을 받고, rerun 은 원래 프롬프트를 받는다)
  4. 폴백이 결과 meta 에 남아 세 창구에 보인다 (조용히 역할 모델 답을 주면 안 된다)
"""
import unittest

from llmwiki.config import Settings, _norm_ensemble_raw, ensemble_template
from llmwiki.providers import BaseLLM, EnsembleLLM, LLMError, summarize_ensemble


class FakeLLM(BaseLLM):
    """받은 프롬프트를 그대로 기록하는 가짜 LLM. fail=True 면 항상 실패한다."""

    def __init__(self, name, model, text="ok", fail=False):
        BaseLLM.__init__(self)
        self.name, self.model, self._text, self._fail = name, model, text, fail
        self.available = True
        self.calls = []                      # [(system, user)]

    def complete(self, system, user, max_tokens=0, effort="", json_mode=False, files=None):
        self.calls.append((system, user))
        if self._fail:
            raise LLMError("%s/%s 강제 실패" % (self.name, self.model))
        return {"text": self._text, "usage": {"input_tokens": 10, "output_tokens": 5}, "model": self.model, "ms": 1.0}


def _ens(members, fallback=None, mode="auto", min_results=1):
    return EnsembleLLM([(m, 1.0) for m in members], aggregator=None, wait="all", timeout_s=5,
                       min_results=min_results, prompt="ensemble_merge", role="answer",
                       fallback=fallback, fallback_mode=mode)


class SettingsLayerTest(unittest.TestCase):
    def test_defaults_declare_both_keys(self):
        """코드 기본값에 두 키가 **명시**돼 있어야 파일에도 그대로 쓰인다."""
        self.assertTrue(Settings.ENSEMBLE_DEFAULTS.get("fallback_role_model"))
        self.assertEqual(Settings.ENSEMBLE_DEFAULTS.get("fallback_mode"), "auto")
        self.assertEqual(Settings.ENSEMBLE_FALLBACK_MODES, ("auto", "merge", "rerun"))

    def test_template_has_keys(self):
        """뼈대에 키가 있어야 운영자가 '있는 줄을 고치는' 방식으로 바꿀 수 있다."""
        t = ensemble_template()
        self.assertIn("fallback_role_model", t)
        self.assertIn("fallback_mode", t)
        self.assertEqual(t["fallback_mode"], "")      # "" = llm_ensemble_defaults 상속

    def test_norm_keeps_and_validates(self):
        self.assertEqual(_norm_ensemble_raw({"fallback_mode": "MERGE"})["fallback_mode"], "merge")
        self.assertEqual(_norm_ensemble_raw({"fallback_mode": "이상한값"})["fallback_mode"], "auto")
        self.assertNotIn("fallback_mode", _norm_ensemble_raw({"fallback_mode": ""}))   # 빈 값 = 상속
        self.assertIs(_norm_ensemble_raw({"fallback_role_model": "false"})["fallback_role_model"], False)

    def test_effective_reports_fallback_target(self):
        """화면·CLI 가 '무엇으로 되돌아가는지' 를 그대로 보여 줄 수 있어야 한다."""
        s = Settings(llm_provider="mock", llm_model="role-m", llm_roles={
            "answer": {"ensemble": {"enabled": True, "members": [{"enabled": True, "model": "m1"}]}}})
        eff = s.effective_ensemble("answer")
        self.assertTrue(eff["enabled"])
        self.assertTrue(eff["fallback_role_model"])
        self.assertEqual(eff["fallback_mode"], "auto")
        self.assertEqual(eff["fallback"]["model"], "role-m")

    def test_effective_off_means_no_target(self):
        s = Settings(llm_provider="mock", llm_model="role-m", llm_roles={
            "answer": {"ensemble": {"enabled": True, "fallback_role_model": False,
                                    "members": [{"enabled": True, "model": "m1"}]}}})
        eff = s.effective_ensemble("answer")
        self.assertFalse(eff["fallback_role_model"])
        self.assertIsNone(eff["fallback"])

    def test_role_override_beats_global_default(self):
        s = Settings(llm_provider="mock", llm_model="role-m",
                     llm_ensemble_defaults={"fallback_mode": "rerun"},
                     llm_roles={"answer": {"ensemble": {"enabled": True, "fallback_mode": "merge",
                                                        "members": [{"enabled": True, "model": "m1"}]}}})
        self.assertEqual(s.effective_ensemble("answer")["fallback_mode"], "merge")
        s2 = Settings(llm_provider="mock", llm_model="role-m",
                      llm_ensemble_defaults={"fallback_mode": "rerun"},
                      llm_roles={"answer": {"ensemble": {"enabled": True,
                                                         "members": [{"enabled": True, "model": "m1"}]}}})
        self.assertEqual(s2.effective_ensemble("answer")["fallback_mode"], "rerun")


class BehaviourTest(unittest.TestCase):
    def test_all_members_dead_without_fallback_raises(self):
        """폴백이 없으면 예전 그대로 실패한다 (이 테스트가 아래 것들을 의미 있게 만든다)."""
        e = _ens([FakeLLM("mock", "m1", fail=True), FakeLLM("mock", "m2", fail=True)])
        with self.assertRaises(LLMError):
            e.complete("SYS", "USR")

    def test_all_members_dead_falls_back_and_reruns(self):
        """멤버가 전부 죽으면 역할 모델이 **원래 프롬프트**를 받는다 (취합할 것이 없으므로)."""
        fb = FakeLLM("mock", "role-m", text="역할 모델 답")
        e = _ens([FakeLLM("mock", "m1", fail=True), FakeLLM("mock", "m2", fail=True)], fallback=fb)
        r = e.complete("SYS", "USR")
        self.assertEqual(r["text"], "역할 모델 답")
        self.assertEqual(len(fb.calls), 1, "폴백이 실제로 불려야 한다 — 설정만 있고 안 돌면 안 된다")
        self.assertEqual(fb.calls[0], ("SYS", "USR"), "취합할 멤버 답이 없으면 원래 프롬프트 그대로")
        self.assertEqual(r["ensemble"]["fallback"]["mode"], "rerun")
        self.assertEqual(r["ensemble"]["fallback"]["used_members"], 0)

    def test_partial_results_are_merged_by_role_model(self):
        """성공한 멤버가 있으면 **버리지 않고** 역할 모델이 취합한다 (min_results 미달)."""
        alive = FakeLLM("mock", "m1", text="살아남은 답")
        e = _ens([alive, FakeLLM("mock", "m2", fail=True)], fallback=FakeLLM("mock", "role-m", text="합친 답"),
                 min_results=2)
        r = e.complete("SYS", "USR")
        self.assertEqual(r["text"], "합친 답")
        fb_llm = e.fallback
        self.assertEqual(len(fb_llm.calls), 1)
        sent_user = fb_llm.calls[0][1]
        self.assertIn("살아남은 답", sent_user, "살아남은 멤버 답이 폴백 프롬프트에 들어가야 한다")
        self.assertIn("후보 답변 1개", sent_user)
        self.assertEqual(r["ensemble"]["fallback"]["mode"], "merge")
        self.assertEqual(r["ensemble"]["fallback"]["used_members"], 1)
        self.assertTrue(r["ensemble"]["aggregated"])

    def test_mode_rerun_discards_member_answers(self):
        """rerun 을 고르면 살아남은 답이 있어도 쓰지 않는다 — 두 모드가 실제로 달라야 한다."""
        alive = FakeLLM("mock", "m1", text="살아남은 답")
        fb = FakeLLM("mock", "role-m", text="새로 만든 답")
        e = _ens([alive, FakeLLM("mock", "m2", fail=True)], fallback=fb, mode="rerun", min_results=2)
        r = e.complete("SYS", "USR")
        self.assertEqual(fb.calls[0], ("SYS", "USR"))
        self.assertNotIn("살아남은 답", fb.calls[0][1])
        self.assertEqual(r["ensemble"]["fallback"]["mode"], "rerun")
        self.assertFalse(r["ensemble"]["aggregated"])

    def test_mode_merge_uses_member_answers(self):
        alive = FakeLLM("mock", "m1", text="살아남은 답")
        fb = FakeLLM("mock", "role-m", text="합친 답")
        e = _ens([alive, FakeLLM("mock", "m2", fail=True)], fallback=fb, mode="merge", min_results=2)
        e.complete("SYS", "USR")
        self.assertIn("살아남은 답", fb.calls[0][1])

    def test_fallback_failure_keeps_original_error(self):
        """폴백까지 실패하면 앙상블 실패로 끝난다 — 조용히 빈 답을 주지 않는다."""
        e = _ens([FakeLLM("mock", "m1", fail=True)], fallback=FakeLLM("mock", "role-m", fail=True))
        with self.assertRaises(LLMError) as cm:
            e.complete("SYS", "USR")
        self.assertIn("fallback", str(cm.exception))

    def test_healthy_ensemble_never_calls_fallback(self):
        """정상일 때 폴백이 불리면 호출이 한 번 늘어난다 — 그러면 안 된다."""
        fb = FakeLLM("mock", "role-m")
        e = _ens([FakeLLM("mock", "m1", text="A"), FakeLLM("mock", "m2", text="B")], fallback=fb)
        e.aggregator = FakeLLM("mock", "agg", text="합침")
        r = e.complete("SYS", "USR")
        self.assertEqual(r["text"], "합침")
        self.assertEqual(fb.calls, [], "멤버가 멀쩡한데 폴백을 부르면 안 된다")
        self.assertIsNone(r["ensemble"].get("fallback"))


class VisibilityTest(unittest.TestCase):
    def test_summary_exposes_fallback(self):
        """폴백이 요약에 없으면 '앙상블이 돈 줄 알았는데 역할 모델 답' 인 것을 아무도 모른다."""
        fb = FakeLLM("mock", "role-m", text="역할 모델 답")
        e = _ens([FakeLLM("mock", "m1", fail=True)], fallback=fb)
        r = e.complete("SYS", "USR")
        s = summarize_ensemble(r["ensemble"])
        self.assertIsNotNone(s)
        self.assertIsNotNone(s["fallback"], "요약에 폴백이 실려야 세 창구가 같은 답을 한다")
        self.assertEqual(s["fallback"]["model"], "role-m")
        self.assertEqual(s["fallback"]["mode"], "rerun")
        self.assertEqual(s["n_ok"], 0)

    def test_summary_has_no_fallback_when_healthy(self):
        e = _ens([FakeLLM("mock", "m1", text="A")])
        r = e.complete("SYS", "USR")
        self.assertIsNone(summarize_ensemble(r["ensemble"])["fallback"])


if __name__ == "__main__":
    unittest.main()
