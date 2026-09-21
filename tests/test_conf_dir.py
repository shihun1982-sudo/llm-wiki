# -*- coding: utf-8 -*-
"""설정을 한 폴더로 모으기 — `LLMWIKI_CONF_DIR` 과 `config bundle`.

왜 이 기능이 있나 (2026-09-19):
  "설정을 한 폴더에서 관리하는 게 낫지 않나" 라는 물음에 대한 답이다. 파일을 실제로 옮기면 기존 설치·문서·
  스크립트·예시의 경로가 전부 깨지고, `.env` 는 도구들이 프로젝트 루트에서 찾는 관례가 있다. 그래서
  **옮기지 않고**, 폴더 하나를 가리키면 그 폴더를 먼저 보게 했다. 쓰지 않으면 예전과 똑같이 동작한다.

이 테스트가 지키는 것
  - 폴더를 안 쓰면 **아무것도 달라지지 않는다** (가장 중요 — 기존 설치가 깨지면 안 된다)
  - 폴더 안의 값이 실제로 **유효값에 반영된다** (파일만 바뀌고 동작은 그대로인 일이 없게)
  - 폴더에 **일부만** 넣어도 된다 (없는 것은 기본 위치)
  - 개별 지정(`LLMWIKI_<NAME>_PATH`)이 폴더보다 세다
  - 묶을 때 `.env` 의 **값이 딸려 나가지 않는다** (기본)
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import config as C      # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="llmwiki_conf_")
        self.saved = {k: os.environ.get(k) for k in
                      ("LLMWIKI_CONF_DIR", "LLMWIKI_CONFIG_PATH", "LLMWIKI_CONFIG", "LLMWIKI_TUNING_PATH")}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bundle_to(self, **kw):
        return C.bundle(out_dir=self.tmp, **kw)


class DefaultBehaviourTest(_Base):
    def test_without_the_folder_nothing_changes(self):
        os.environ.pop("LLMWIKI_CONF_DIR", None)
        self.assertEqual(C.conf_dir(), "")
        self.assertTrue(C.path_for("config").endswith("config.json"))
        self.assertTrue(C.path_for("rules").endswith(os.path.join("data", "rules.json")))

    def test_empty_env_is_treated_as_unset(self):
        os.environ["LLMWIKI_CONF_DIR"] = "   "
        self.assertEqual(C.conf_dir(), "")


class ConfDirTest(_Base):
    def setUp(self):
        super().setUp()
        self.bundle_to()
        os.environ["LLMWIKI_CONF_DIR"] = self.tmp

    def test_folder_files_win_over_defaults(self):
        self.assertEqual(C.path_for("config"), os.path.join(self.tmp, "config.json"))
        self.assertEqual(C.path_for("rules"), os.path.join(self.tmp, "rules.json"))

    def test_rules_loses_its_data_prefix_in_the_folder(self):
        """`data/rules.json` 은 설정인데 데이터 폴더에 산다 — 모으면 그냥 `rules.json` 이 된다."""
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "rules.json")))

    def test_the_value_actually_takes_effect(self):
        """파일 자리만 바뀌고 동작은 그대로인 일이 없어야 한다."""
        p = os.path.join(self.tmp, "config.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d["top_k_final"] = 4242
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        self.assertEqual(C.load_settings().top_k_final, 4242)
        os.environ.pop("LLMWIKI_CONF_DIR", None)
        self.assertNotEqual(C.load_settings().top_k_final, 4242)

    def test_partial_folder_falls_back_per_file(self):
        os.remove(os.path.join(self.tmp, "tuning.json"))
        self.assertFalse(C.path_for("tuning").startswith(self.tmp))
        self.assertTrue(C.path_for("config").startswith(self.tmp))

    def test_explicit_path_beats_the_folder(self):
        other = os.path.join(self.tmp, "somewhere-else.json")
        with open(other, "w", encoding="utf-8") as f:
            json.dump({}, f)
        os.environ["LLMWIKI_TUNING_PATH"] = other
        self.assertEqual(C.path_for("tuning"), other)

    def test_logs_and_themes_are_not_folder_managed(self):
        """운영 산출물(logs)과 앱 자원(themes)은 설정이 아니다 — 모으는 대상에서 뺀다."""
        for name in ("logs_dir", "themes", "eval"):
            self.assertNotIn(name, C.CONF_DIR_FILES, name)
            self.assertFalse(C.path_for(name).startswith(self.tmp), name)


class StrayWriteGuardTest(_Base):
    """직접 만든 Settings 가 **프로젝트 config.json** 을 덮어쓰는 것을 막는다.

    이 저장소에서 두 번 일어난 사고다. `load_settings()` 로 읽은 Settings 는 읽어 온 자리를 기억하지만,
    `Settings(data_dir=<임시폴더>, …)` 처럼 직접 만든 것은 그 자리가 없어서 `save_settings(s)` 가
    기본 경로로 떨어진다. 그러면 프로젝트 설정이 임시 경로를 가리키게 되어 **색인이 통째로 안 보인다.**
    조용히 성공하기 때문에 원인을 찾는 데 두 번 다 오래 걸렸다 — 그래서 예외로 바꿨다.
    """

    def test_stray_write_to_the_project_config_raises(self):
        s = C.Settings(data_dir=os.path.join(self.tmp, "data"))
        with self.assertRaises(C.StraySettingsWrite):
            C.save_settings(s, os.path.join(C.ROOT, "config.json"))

    def test_writing_to_an_isolated_path_is_allowed(self):
        target = os.path.join(self.tmp, "config.json")
        C.save_settings(C.Settings(data_dir=os.path.join(self.tmp, "data")), target)
        self.assertTrue(os.path.exists(target))

    def test_default_settings_may_be_written_to_the_project_config(self):
        """`config reset` 은 기본값 Settings 를 프로젝트 파일에 쓴다 — 막으면 안 된다."""
        C._guard_stray_write(C.Settings(), os.path.join(C.ROOT, "config.json"))

    def test_settings_loaded_from_a_file_may_be_written_back(self):
        target = os.path.join(self.tmp, "config.json")
        C.save_settings(C.Settings(data_dir=os.path.join(self.tmp, "data")), target)
        loaded = C.load_settings(target)
        self.assertEqual(getattr(loaded, "_config_path", None), target)
        C._guard_stray_write(loaded, os.path.join(C.ROOT, "config.json"))   # _config_path 가 있으면 통과


class BundleTest(_Base):
    def test_bundle_copies_every_managed_file_that_exists(self):
        r = self.bundle_to()
        names = {c["name"] for c in r["copied"]}
        for must in ("config", "tuning", "rules", "security", "prompts_dir"):
            self.assertIn(must, names, must)
        self.assertIn("LLMWIKI_CONF_DIR", r["use"])

    def test_env_values_are_stripped_by_default(self):
        """자격증명이 실수로 묶음에 섞여 나가면 안 된다."""
        r = self.bundle_to()
        envs = [c for c in r["copied"] if c["name"] == "env"]
        if not envs:
            self.skipTest(".env 가 없는 환경")
        with open(os.path.join(self.tmp, ".env"), encoding="utf-8") as f:
            body = f.read()
        for line in body.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            self.assertTrue(line.endswith("="), "값이 남아 있습니다: %s" % line)

    def test_dry_run_writes_nothing(self):
        d = os.path.join(self.tmp, "preview-only")
        r = C.bundle(out_dir=d, dry_run=True)
        self.assertTrue(r["copied"])
        self.assertFalse(os.path.exists(d))

    def test_both_directions_is_an_error(self):
        self.assertIn("error", C.bundle(out_dir="a", restore_from="b"))
        self.assertIn("error", C.bundle())

    def test_restore_from_a_missing_folder_is_an_error(self):
        self.assertIn("error", C.bundle(restore_from=os.path.join(self.tmp, "없는폴더")))

    def test_restore_puts_files_back(self):
        self.bundle_to()
        target = os.path.join(self.tmp, "restored")
        os.makedirs(target)
        os.environ["LLMWIKI_CONFIG_PATH"] = os.path.join(target, "config.json")
        r = C.bundle(restore_from=self.tmp)
        self.assertTrue([c for c in r["copied"] if c["name"] == "config"])
        self.assertTrue(os.path.exists(os.path.join(target, "config.json")))

    def test_restore_warns_that_a_running_server_needs_a_restart(self):
        self.bundle_to()
        r = C.bundle(restore_from=self.tmp)
        self.assertIn("재시작", r["note"])


if __name__ == "__main__":
    unittest.main()
