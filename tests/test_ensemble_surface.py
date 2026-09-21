# -*- coding: utf-8 -*-
"""앙상블(역할 단위 다중 LLM)이 **세 창구에서 같은 값**을 읽고 쓰는지 고정한다 (요청 6).

2026-09-19 에 발견: 엔진(`providers.EnsembleLLM`)·CLI(`models ensemble`)·config 스키마는 있었는데
**Web UI 에는 편집 화면이 아예 없었다**. 역할 표에 모델 드롭다운이 하나뿐이라 사용자가 "3개를 어디서 고르나" 를
물었다. 그래서 `GET /api/models` 가 앙상블 유효값·원본·기본값·최대 멤버 수를 내려주고,
`POST /api/models/set` 이 `llm_roles.<role>.ensemble` 을 파일에 저장하는지 여기서 검사한다.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles, apply_overrides, save_settings, load_settings  # noqa: E402


class EnsembleConfigTest(unittest.TestCase):
    """config 계층: 기본값 합치기 · 빈 멤버 제거 · 최대 3개 · provider 상속."""

    def _s(self, ens):
        s = Settings(llm_provider="mock", llm_model="m0")
        s.llm_roles = {"answer": {"model": "role-model", "ensemble": ens}}
        return s

    def test_disabled_by_default(self):
        s = Settings(llm_provider="mock")
        self.assertFalse(s.effective_ensemble("answer")["enabled"])

    def test_defaults_are_merged(self):
        e = self._s({"enabled": True, "members": [{"enabled": True, "model": "a"}]}).effective_ensemble("answer")
        self.assertTrue(e["enabled"])
        self.assertEqual(e["wait"], Settings.ENSEMBLE_DEFAULTS["wait"])
        self.assertEqual(e["timeout_s"], Settings.ENSEMBLE_DEFAULTS["timeout_s"])
        self.assertEqual(e["prompt"], Settings.ENSEMBLE_DEFAULTS["prompt"])

    def test_unused_members_are_dropped_and_capped(self):
        """✘ 로 끈 멤버와 모델이 빈 멤버는 빠지고, 최대 3개까지만 쓴다."""
        e = self._s({"enabled": True, "members": [
            {"enabled": True, "model": "a"},
            {"enabled": False, "model": "b"},      # 껐다
            {"enabled": True, "model": ""},        # 모델이 없다
            {"enabled": True, "model": "c"},
            {"enabled": True, "model": "d"},
            {"enabled": True, "model": "e"},       # 상한을 넘는다
        ]}).effective_ensemble("answer")
        self.assertEqual([m["model"] for m in e["members"]], ["a", "c", "d"])
        self.assertLessEqual(len(e["members"]), Settings.ENSEMBLE_MAX_MEMBERS)

    def test_enabled_needs_a_usable_member(self):
        e = self._s({"enabled": True, "members": [{"enabled": False, "model": "a"}]}).effective_ensemble("answer")
        self.assertFalse(e["enabled"], "쓸 멤버가 없으면 켜진 것으로 보지 않는다")

    def test_member_provider_inherits_role(self):
        s = self._s({"enabled": True, "members": [{"enabled": True, "model": "a"}]})
        s.llm_roles["answer"]["provider"] = "ollama"
        e = s.effective_ensemble("answer")
        self.assertEqual(e["members"][0]["provider"], "ollama")

    def test_weight_and_wait_are_normalized(self):
        e = self._s({"enabled": True, "wait": "TIMEOUT", "timeout_s": "30", "min_results": "2",
                     "members": [{"enabled": True, "model": "a", "weight": "1.5"}]}).effective_ensemble("answer")
        self.assertEqual(e["wait"], "timeout")
        self.assertEqual(e["timeout_s"], 30)
        self.assertEqual(e["min_results"], 2)
        self.assertEqual(e["members"][0]["weight"], 1.5)

    def test_saved_to_config_file_and_read_back(self):
        """Web UI 가 보내는 모양 그대로 저장 → 파일 → 다시 읽기 (UI ↔ 파일 ↔ 유효값)."""
        # CONFIG_PATH 는 import 시점에 정해지므로 환경변수 대신 **경로를 직접 넘긴다**
        tmp = tempfile.mkdtemp(prefix="lwens_")
        cfg = os.path.join(tmp, "config.json")
        old = os.environ.get("LLMWIKI_CONFIG")
        try:
            s = load_settings(cfg)
            apply_overrides(s, {"llm_roles": {"answer": {"model": "role-model", "ensemble": {
                "enabled": True, "wait": "timeout", "timeout_s": 45, "min_results": 2, "prompt": "ensemble_merge",
                "members": [{"enabled": True, "provider": "ollama", "model": "a", "weight": 1.5},
                            {"enabled": True, "provider": "", "model": "b", "weight": 1.0},
                            {"enabled": False, "provider": "", "model": "", "weight": 1.0}],
                "aggregator": {"provider": "ollama", "model": "agg"}}}}})
            save_settings(s, cfg)
            with open(cfg, encoding="utf-8") as f:
                raw = json.load(f)
            ens = raw["llm_roles"]["answer"]["ensemble"]
            self.assertTrue(ens["enabled"])
            self.assertEqual(len(ens["members"]), 3, "껐던 멤버도 모양을 지켜 저장한다 (다시 켤 때 값이 남도록)")
            s2 = load_settings(cfg)
            e = s2.effective_ensemble("answer")
            self.assertEqual([m["model"] for m in e["members"]], ["a", "b"])
            self.assertEqual(e["aggregator"]["model"], "agg")
            self.assertEqual(e["wait"], "timeout")
            self.assertEqual(e["timeout_s"], 45)
        finally:
            if old is None:
                os.environ.pop("LLMWIKI_CONFIG", None)
            else:
                os.environ["LLMWIKI_CONFIG"] = old
            shutil.rmtree(tmp, ignore_errors=True)


class EnsembleWebSurfaceTest(unittest.TestCase):
    """Web 화면이 앙상블을 그리는 데 필요한 것이 **응답과 JS 양쪽에** 있는가."""

    def test_api_models_includes_ensemble(self):
        """/api/models 가 역할별 유효값·원본·기본값·상한을 내려준다 (화면이 '비우면 무엇을 상속하는지' 를 보이려면 필요)."""
        import llmwiki.web.server as S
        src = open(S.__file__, encoding="utf-8").read()
        i = src.find('u.path == "/api/models"')
        self.assertGreater(i, 0)
        block = src[i:i + 1600]
        for key in ('"ensemble"', '"ensemble_defaults"', '"ensemble_max_members"', "effective_ensemble"):
            self.assertIn(key, block, "GET /api/models 응답에 %s 가 없습니다" % key)

    def test_settings_js_has_editor_and_saves_it(self):
        """역할 표에 앙상블 편집이 있고, 저장 경로(modelsSettings)가 그 값을 담는다."""
        p = os.path.join(ROOT, "llmwiki", "web", "static", "js", "settings.js")
        js = open(p, encoding="utf-8").read()
        for needle in ("function ensembleRow(", "function ensembleSettings(", "ensembleRow(role)",
                       "c.ensemble = en", 'data-ens-f="weight"', 'data-ens-f="agg_model"', 'data-ens-f="wait"'):
            self.assertIn(needle, js, "settings.js 에 %s 가 없습니다 — Web UI 에서 앙상블을 못 고칩니다" % needle)

    def test_cli_has_ensemble_command(self):
        cli = open(os.path.join(ROOT, "llmwiki", "cli.py"), encoding="utf-8").read()
        self.assertIn("models ensemble", cli.replace("\n", " "))


if __name__ == "__main__":
    unittest.main()
