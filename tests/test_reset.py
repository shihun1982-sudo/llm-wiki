# -*- coding: utf-8 -*-
"""관리자 초기화 (llmwiki/reset.py) — 세 범위가 **지워야 할 것만** 지우는가.

왜 이 테스트가 있나 (2026-09-19):
  초기화는 되돌릴 수 없는 동작이라, 틀렸을 때 비용이 가장 큰 기능이다. 특히 위험한 실수 셋:
  - **코퍼스 원본을 지운다** — 사람이 넣은 자료다. 색인만 지워야 한다.
  - **security.json 을 지운다** — 원격에서 설정을 초기화하다 계정이 사라지면 다시 들어갈 길이 없다.
  - **미리보기가 실제로 지운다** — 확인하려고 눌렀는데 지워지면 안 된다.
  그래서 "무엇을 지우는가" 만큼 "무엇을 **안** 지우는가" 를 같은 무게로 검사한다.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import reset as R            # noqa: E402


DOC = """---
doc_type: issue
ext_id: ISSUE-9001
---

# RX DMA underrun

RX DMA 에서 underrun 이 발생하면 PHY 재시작이 실패한다. rev B1 에서 t_setup 은 4 ns 이다.
원인은 클럭 게이팅 타이밍이며 CL-90001 에서 고쳤다. 잡음 여유는 1.5 dB 로 측정됐다.
"""


class _Base(unittest.TestCase):
    def setUp(self):
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        self.tmp = tempfile.mkdtemp(prefix="llmwiki_reset_")
        # **로그 폴더를 반드시 격리한다.** logs_dir 은 Settings 가 아니라 프로세스 전역(path_for)이라,
        # 격리하지 않으면 `logs` 범위 테스트가 **프로젝트의 진짜 로그와 audit.jsonl 을 비운다**.
        # (2026-09-19 에 실제로 그렇게 됐다 — 감사 로그 이력이 사라졌다.)
        self._saved_logs_env = os.environ.get("LLMWIKI_LOGS_DIR_PATH")
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(self.tmp, "logs")
        os.makedirs(os.environ["LLMWIKI_LOGS_DIR_PATH"], exist_ok=True)
        with open(os.path.join(os.environ["LLMWIKI_LOGS_DIR_PATH"], "llmwiki.log"), "w", encoding="utf-8") as f:
            f.write("테스트 로그 한 줄\n")
        self.corpus = os.path.join(self.tmp, "corpus")
        os.makedirs(self.corpus)
        for i in range(2):
            with open(os.path.join(self.corpus, "d%d.md" % i), "w", encoding="utf-8") as f:
                f.write(DOC.replace("9001", "900%d" % i))
        s = Settings(corpus_dirs=[self.corpus], data_dir=os.path.join(self.tmp, "data"),
                     wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False)
        self.p = Pipeline(s)
        self.p.build(full=True)

    def tearDown(self):
        try:
            self.p.store.close()
        except Exception:
            pass
        if self._saved_logs_env is None:
            os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
        else:
            os.environ["LLMWIKI_LOGS_DIR_PATH"] = self._saved_logs_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, table):
        try:
            return int(self.p.store.conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])
        except Exception:
            return -1

    def query_history(self):
        """'질의 이력' 건수. 2026-09-23부터 원천이 query_log → requests(kind='query') 다.
        옛 행도 함께 세어, 전환기에 이 검사가 원천 하나만 보고 착각하지 않게 한다."""
        return self.rows("query_log") + int(self.p.store.conn.execute(
            "SELECT COUNT(*) FROM requests WHERE kind='query'").fetchone()[0])


class PreviewIsReadOnlyTest(_Base):
    """미리보기는 **아무것도 바꾸지 않아야 한다** — 이 하나가 틀리면 나머지가 다 위험해진다."""

    def test_preview_changes_nothing(self):
        before = {t: self.rows(t) for t in ("docs", "chunks", "entities", "relations", "query_log")}
        files_before = sorted(os.listdir(self.corpus))
        for scope in R.SCOPES:
            plan = R.preview(self.p, scope)
            self.assertFalse(plan.get("error"), scope)
            self.assertTrue(plan["items"] or scope == "logs", scope)
        after = {t: self.rows(t) for t in before}
        self.assertEqual(before, after)
        self.assertEqual(files_before, sorted(os.listdir(self.corpus)))

    def test_preview_reports_what_is_kept(self):
        for scope in R.SCOPES:
            self.assertTrue(R.preview(self.p, scope)["kept"], scope)

    def test_unknown_scope_is_an_error_not_a_crash(self):
        self.assertIn("error", R.preview(self.p, "everything"))
        self.assertIn("error", R.run(self.p, "everything"))

    def test_format_is_readable(self):
        txt = R.format_preview(R.preview(self.p, "data"))
        for part in ("초기화 범위", "지웁니다", "유지합니다", "옵션"):
            self.assertIn(part, txt)


class DataScopeTest(_Base):
    def setUp(self):
        super().setUp()
        self.p.query("RX DMA underrun 원인", log=True)     # 질의 로그·요청 이력을 만든다
        self.before_queries = self.query_history()
        self.r = R.run(self.p, "data", actor="test")

    def test_index_is_cleared(self):
        for t in ("docs", "chunks", "embeddings", "entities", "relations", "mentions"):
            self.assertEqual(self.rows(t), 0, t)

    def test_corpus_files_survive(self):
        """가장 중요한 불변식 — 사람이 넣은 원본은 절대 지우지 않는다."""
        self.assertEqual(len(os.listdir(self.corpus)), 2)

    def test_settings_files_survive(self):
        from llmwiki.config import path_for
        self.assertTrue(os.path.exists(path_for("config")))

    def test_query_history_survives(self):
        """질의 로그는 '로그' 범위의 것이다 — 데이터 초기화가 가져가면 안 된다."""
        self.assertEqual(self.query_history(), self.before_queries)
        self.assertGreater(self.before_queries, 0)

    def test_embedding_cache_survives_by_default(self):
        """비싼 캐시라 기본은 유지한다. (hash 임베더는 캐시를 채우지 않으므로 직접 한 줄 넣어 확인한다.)"""
        import numpy as np
        self.p.store.cache_put("test", "m", [("sha-keep", np.zeros(4, dtype="float32"))])
        R.run(self.p, "data", actor="test")
        self.assertGreater(self.rows("embedding_cache"), 0)

    def test_snapshot_is_made_before_deleting(self):
        self.assertTrue(self.r["done"].get("snapshot"), "되돌릴 수 있어야 한다")

    def test_next_steps_are_given(self):
        self.assertTrue(self.r["next"])
        self.assertTrue([x for x in self.r["next"] if "build" in x])

    def test_rebuilding_restores_the_index(self):
        self.p.build(full=True)
        self.assertGreater(self.rows("docs"), 0)
        self.assertGreater(self.rows("chunks"), 0)


class DataScopeOptionTest(_Base):
    def test_clear_embed_cache_option(self):
        import numpy as np
        self.p.store.cache_put("test", "m", [("sha-drop", np.zeros(4, dtype="float32"))])
        self.assertGreater(self.rows("embedding_cache"), 0)
        R.run(self.p, "data", actor="test", clear_embed_cache=True)
        self.assertEqual(self.rows("embedding_cache"), 0)


class LogScopeTest(_Base):
    def setUp(self):
        super().setUp()
        self.p.query("RX DMA underrun 원인", log=True)
        self.pid = self.p.store.add_proposal("synonym", {"term": "dma", "expansion": "직접메모리접근"},
                                             "테스트", 0.9, "manual")
        self.assertGreater(self.query_history(), 0)
        self.r = R.run(self.p, "logs", actor="test")

    def test_history_tables_are_cleared(self):
        for t in ("query_log", "requests", "forensics", "episodes", "trials"):
            self.assertEqual(self.rows(t), 0, t)

    def test_index_survives(self):
        """로그 초기화가 색인을 건드리면 안 된다 — 다시 빌드해야 하는 비용이 크다."""
        self.assertGreater(self.rows("docs"), 0)
        self.assertGreater(self.rows("chunks"), 0)
        self.assertGreater(self.rows("entities"), 0)

    def test_proposals_survive_by_default(self):
        self.assertEqual(self.rows("proposals"), 1)
        self.assertTrue(self.p.store.get_proposal(self.pid))

    def test_proposals_can_be_cleared_on_request(self):
        R.run(self.p, "logs", actor="test", include_proposals=True)
        self.assertEqual(self.rows("proposals"), 0)

    def test_queries_work_after_clearing_logs(self):
        r = self.p.query("RX DMA underrun 원인", log=True)
        self.assertTrue(r)

    def test_only_the_configured_log_dir_is_touched(self):
        """로그 폴더는 Settings 가 아니라 프로세스 전역(path_for)이다 — 격리된 폴더만 비워야 한다."""
        from llmwiki.config import path_for
        self.assertTrue(path_for("logs_dir").startswith(self.tmp), "테스트가 프로젝트 로그를 건드리면 안 된다")
        self.assertEqual(os.path.getsize(os.path.join(path_for("logs_dir"), "llmwiki.log")), 0)

    def test_log_files_are_truncated_not_deleted(self):
        """Windows 에서 열린 파일을 지우면 그 뒤 모든 로그가 조용히 사라진다 — 비우기만 해야 한다."""
        from llmwiki.config import path_for
        self.assertTrue(os.path.exists(os.path.join(path_for("logs_dir"), "llmwiki.log")))


class SettingsScopeTest(unittest.TestCase):
    """설정 초기화는 **프로젝트 파일**을 건드리므로 격리된 복사본에서만 돌린다."""

    def setUp(self):
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        self.tmp = tempfile.mkdtemp(prefix="llmwiki_rset_")
        self.corpus = os.path.join(self.tmp, "corpus")
        os.makedirs(self.corpus)
        with open(os.path.join(self.corpus, "d.md"), "w", encoding="utf-8") as f:
            f.write(DOC)
        # 설정 파일들을 임시 폴더로 옮긴다 (LLMWIKI_*_PATH)
        self.saved_env = {}
        for name, fn in (("config", "config.json"), ("tuning", "tuning.json"), ("presets", "presets.json"),
                         ("query_rules", "query_rules.json"), ("rules", "rules.json"), ("pins", "pins.json"),
                         ("security", "security.json"), ("agents", "agents.json"), ("models", "models.json"),
                         ("mcp_sources", "mcp_sources.json"), ("schedule", "schedule.json"),
                         ("server", "server.json"), ("stopwords", "stopwords.json"), ("docacl", "docacl.json")):
            key = "LLMWIKI_%s_PATH" % name.upper()
            self.saved_env[key] = os.environ.get(key)
            os.environ[key] = os.path.join(self.tmp, fn)
        self.prompts_dir = os.path.join(self.tmp, "prompts")
        self.saved_env["LLMWIKI_PROMPTS_DIR_PATH"] = os.environ.get("LLMWIKI_PROMPTS_DIR_PATH")
        os.environ["LLMWIKI_PROMPTS_DIR_PATH"] = self.prompts_dir
        self.saved_env["LLMWIKI_LOGS_DIR_PATH"] = os.environ.get("LLMWIKI_LOGS_DIR_PATH")
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(self.tmp, "logs")
        os.makedirs(os.environ["LLMWIKI_LOGS_DIR_PATH"], exist_ok=True)
        # 사람이 고친 것처럼 값을 써 둔다
        with open(os.environ["LLMWIKI_CONFIG_PATH"], "w", encoding="utf-8") as f:
            json.dump({"top_k_final": 999, "corpus_dirs": [self.corpus]}, f)
        with open(os.environ["LLMWIKI_SECURITY_PATH"], "w", encoding="utf-8") as f:
            json.dump({"users": {"내계정": {"role": "admin"}}, "_mine": True}, f, ensure_ascii=False)
        s = Settings(corpus_dirs=[self.corpus], data_dir=os.path.join(self.tmp, "data"),
                     wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False)
        self.p = Pipeline(s)
        self.p.build(full=True)

    def tearDown(self):
        try:
            self.p.store.close()
        except Exception:
            pass
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _security(self):
        with open(os.environ["LLMWIKI_SECURITY_PATH"], encoding="utf-8") as f:
            return json.load(f)

    def test_security_is_not_touched_by_default(self):
        """원격에서 설정을 초기화하다 계정이 사라지면 다시 들어갈 길이 없다."""
        R.run(self.p, "settings", actor="test")
        self.assertTrue(self._security().get("_mine"), "security.json 이 기본으로 초기화되면 안 된다")

    def test_security_can_be_reset_on_request(self):
        R.run(self.p, "settings", actor="test", include_security=True)
        self.assertFalse(self._security().get("_mine"))

    def test_config_is_restored(self):
        with open(os.environ["LLMWIKI_CONFIG_PATH"], encoding="utf-8") as f:
            self.assertEqual(json.load(f).get("top_k_final"), 999)
        R.run(self.p, "settings", actor="test")
        with open(os.environ["LLMWIKI_CONFIG_PATH"], encoding="utf-8") as f:
            self.assertNotEqual(json.load(f).get("top_k_final"), 999)

    def test_index_and_corpus_survive(self):
        R.run(self.p, "settings", actor="test")
        self.assertEqual(len(os.listdir(self.corpus)), 1)
        n = self.p.store.conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        self.assertGreater(n, 0, "설정 초기화가 색인을 지우면 안 된다")

    def test_preview_marks_the_dangerous_ones(self):
        plan = R.preview(self.p, "settings", include_security=True, include_env=True)
        warns = [i for i in plan["items"] if i["level"] == "warn"]
        self.assertTrue(warns)
        self.assertTrue([i for i in warns if "security" in i["target"] or ".env" in i["target"]])

    def test_restored_files_are_valid_json(self):
        R.run(self.p, "settings", actor="test")
        for key in ("LLMWIKI_CONFIG_PATH", "LLMWIKI_TUNING_PATH", "LLMWIKI_RULES_PATH", "LLMWIKI_PRESETS_PATH"):
            path = os.environ[key]
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    json.load(f)          # 깨진 파일을 남기면 다음 기동이 막힌다

    def test_next_steps_mention_restarting_the_server(self):
        r = R.run(self.p, "settings", actor="test")
        self.assertTrue([x for x in r["next"] if "서버" in x])


class AuthTest(unittest.TestCase):
    """등급 — 미리보기는 read, 실행은 destructive."""

    def test_cli_preview_is_read_and_apply_is_destructive(self):
        from llmwiki.auth import classify_cli
        self.assertEqual(classify_cli(["reset", "data"])[0], "read")
        self.assertEqual(classify_cli(["reset", "data", "--apply"])[0], "destructive")
        self.assertEqual(classify_cli(["reset"])[0], "read")

    def test_api_post_is_destructive(self):
        from llmwiki.auth import classify_api
        self.assertEqual(classify_api("POST", "/api/reset", {"scope": "data"})[0], "destructive")
        self.assertEqual(classify_api("GET", "/api/reset", {})[0], "read")


if __name__ == "__main__":
    unittest.main()
