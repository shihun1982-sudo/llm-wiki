# -*- coding: utf-8 -*-
"""Phase 0: 설정(env 오버라이드·경로 레지스트리) · 로깅(run_id) · 프롬프트 외부화 · 빌드 락 · health · 프리셋 · CLI."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki.config import Settings, Toggles, env_overrides, apply_overrides, path_for, effective_settings  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402
from llmwiki import logging_setup as ls  # noqa: E402
from llmwiki import prompts as pr  # noqa: E402
from llmwiki import presets as ps  # noqa: E402
from llmwiki import tuning as tn  # noqa: E402
from llmwiki.buildlock import BuildLock, BuildLockedError  # noqa: E402
from llmwiki.cli import run_captured  # noqa: E402

DOC = "# ISSUE-2041 RX DMA underrun\n\n## 현상\nRX 경로 DMA underrun 발생 시 PHY 재시작 실패. CL-55321 로 수정.\n\n## 원인\nFIFO 임계값 설정 오류.\n"


class Phase0Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus = os.path.join(self.tmp, "corpus")
        os.makedirs(self.corpus)
        with open(os.path.join(self.corpus, "issue.md"), "w", encoding="utf-8") as f:
            f.write(DOC)
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(self.tmp, "logs")
        os.environ["LLMWIKI_PROMPTS_DIR_PATH"] = os.path.join(self.tmp, "prompts")
        os.environ["LLMWIKI_PRESETS_PATH"] = os.path.join(self.tmp, "presets.json")
        os.environ["LLMWIKI_RULES_PATH"] = os.path.join(self.tmp, "rules.json")
        from llmwiki import config as _cfg
        self._cfg_path, self._tun_path = _cfg.CONFIG_PATH, tn.TUNING_PATH
        _cfg.CONFIG_PATH = os.path.join(self.tmp, "config.json")
        tn.TUNING_PATH = os.path.join(self.tmp, "tuning.json")
        ls._STATE["dir"] = None
        s = Settings(corpus_dirs=[self.corpus], data_dir=os.path.join(self.tmp, "data"), wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=256)
        s.toggles = Toggles(query_cache=False)
        self.s = s
        self.p = Pipeline(s)

    def tearDown(self):
        self.p.store.close()
        from llmwiki import config as _cfg
        _cfg.CONFIG_PATH, tn.TUNING_PATH = self._cfg_path, self._tun_path
        for k in ("LLMWIKI_LOGS_DIR_PATH", "LLMWIKI_PROMPTS_DIR_PATH", "LLMWIKI_PRESETS_PATH", "LLMWIKI_RULES_PATH"):
            os.environ.pop(k, None)
        ls._STATE["dir"] = None
        tn.load_tuning(os.path.join(self.tmp, "none.json"))
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 설정 ----
    def test_env_overrides_and_effective(self):
        os.environ["LLMWIKI_TOP_K_FINAL"] = "3"
        os.environ["LLMWIKI_TOGGLE_FTS"] = "false"
        os.environ["LLMWIKI_ANSWER_MODEL"] = "claude-sonnet-5"
        os.environ["LLMWIKI_TIMEZONE"] = "UTC"
        try:
            ov = env_overrides()
            self.assertEqual(ov["top_k_final"], "3")
            self.assertEqual(ov["fts"], "false")
            self.assertEqual(ov["answer_model"], "claude-sonnet-5")
            s = apply_overrides(Settings(), ov)
            self.assertEqual(s.top_k_final, 3)
            self.assertFalse(s.toggles.fts)
            self.assertEqual(s.role_llm("answer")["model"], "claude-sonnet-5")
            self.assertEqual(s.timezone, "UTC")
        finally:
            for k in ("LLMWIKI_TOP_K_FINAL", "LLMWIKI_TOGGLE_FTS", "LLMWIKI_ANSWER_MODEL", "LLMWIKI_TIMEZONE"):
                os.environ.pop(k, None)
        rows = effective_settings(self.s)
        keys = {r["key"] for r in rows}
        self.assertIn("toggles.health_check", keys)
        self.assertIn("embed_store_dtype", keys)
        self.assertTrue(all("env" in r for r in rows))
        self.assertTrue(path_for("presets").endswith("presets.json"))
        self.assertEqual(path_for("logs_dir"), os.environ["LLMWIKI_LOGS_DIR_PATH"])

    # ---- 로깅 + run_id ----
    def test_logging_run_id_links_requests(self):
        self.p.build(full=True)
        r, t = self.p.query("RX DMA underrun 원인", log=False)
        self.assertTrue(r["run_id"])
        self.assertEqual(t["run_id"], r["run_id"])
        req = self.p.store.get_request(r["request_id"])
        self.assertEqual(req["run_id"], r["run_id"])
        self.assertEqual(self.p.store.request_by_run(r["run_id"])["id"], r["request_id"])
        d = ls.log_dir()
        self.assertTrue(d and os.path.isdir(d))
        rows = ls.grep(os.path.join(d, "llmwiki.log"), run_id=r["run_id"])
        names = [x.get("data", {}).get("stage") for x in rows if x.get("data")]
        self.assertIn("fts_search", names)
        self.assertTrue(any(x["msg"].startswith("query finish") for x in rows))
        qrows = ls.grep(os.path.join(d, "query.log"), run_id=r["run_id"])
        self.assertTrue(qrows)
        brows = ls.tail(os.path.join(d, "build.log"), 200)
        self.assertTrue(any(x.get("kind") == "build" for x in brows))
        self.assertFalse(any(x.get("kind") == "query" for x in brows))
        # log_stages off → 단계 로그 없음
        self.p.s.toggles.log_stages = False
        r2, _ = self.p.query("FIFO 임계값", log=False)
        rows2 = ls.grep(os.path.join(d, "llmwiki.log"), run_id=r2["run_id"])
        self.assertFalse(any((x.get("data") or {}).get("stage") == "fts_search" for x in rows2))
        # CLI logs grep --request
        out = run_captured(["logs", "grep", "--request", str(r["request_id"])], self.s, self.p)
        self.assertEqual(out["code"], 0)
        self.assertIn("fts_search", out["output"])

    # ---- 프롬프트 외부화 ----
    def test_prompts_files_and_reload(self):
        txt = pr.get("answer_system")
        self.assertTrue(txt.startswith("TASK=answer"))
        self.assertTrue(os.path.exists(pr.path("answer_system")))
        # 시스템 프롬프트 뒤에 답변 가이드가 붙는다 — 가이드의 출력 뼈대 제목은 추출식 답변과 같은 것을 쓴다
        sysp = pr.answer_system()
        for h in ("## 핵심", "## 상세", "## 근거", "## 미확인"):
            self.assertIn(h, sysp)
        self.assertIn("PHY", sysp)                     # 모뎀 PHY 임베디드 개발자 페르소나
        pr.set_text("answer_guide", "# 내 가이드\n짧게.")
        self.assertIn("내 가이드", pr.answer_system())
        pr.reset("answer_guide")
        self.assertIn("출력 뼈대", pr.answer_system())
        self.assertTrue(all(r["exists"] for r in pr.list_prompts()))
        # 답변 단계가 파일 프롬프트를 사용 (샘플에 시스템 프롬프트 기록)
        self.p.build(full=True)
        pr.set_text("answer_guide", "# 특별지시 XYZ123")
        r, t = self.p.query("RX DMA underrun 원인", log=False, debug=2)
        st = {c["name"]: c for c in t["children"]}
        self.assertIn("XYZ123", st["answer_llm"]["samples"]["system"])
        self.assertEqual(st["answer_llm"]["meta"]["length_target"], "normal")
        out = run_captured(["prompts", "list"], self.s, self.p)
        self.assertIn("answer_guide", out["output"])

    # ---- 빌드 락 ----
    def test_build_lock(self):
        path = os.path.join(self.tmp, "data", "build.lock")
        with BuildLock(path):
            with self.assertRaises(BuildLockedError):
                self.p.build(full=False)
            out = run_captured(["build"], self.s, self.p)
            self.assertEqual(out["code"], 2)
            self.assertIn("refused", out["output"])
        # stale 락(죽은 pid) 은 회수
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pid": 999999, "host": __import__("socket").gethostname(), "ts": 0}, f)
        res, _ = self.p.build(full=True)
        self.assertEqual(res["docs"], 1)
        self.assertFalse(os.path.exists(path))

    # ---- health ----
    def test_health_and_build_gate(self):
        from llmwiki.health import run_health, format_health
        r = run_health(self.p, quick=True)
        names = {c["name"] for c in r["checks"]}
        for n in ("python", "sqlite_fts5", "db_integrity", "disk_free", "corpus_dirs", "vector_memory"):
            self.assertIn(n, names)
        self.assertTrue(r["ok"])
        self.assertIn("health:", format_health(r))
        res, t = self.p.build(full=True)
        st = {c["name"]: c for c in t["children"]}
        self.assertIn("health", st)
        self.assertTrue(st["health"]["meta"]["ok"])
        self.assertEqual(res["health"]["fails"], 0)
        # 코퍼스 폴더 없음 → fail → 빌드 거부, --force 로 강행
        self.p.s.corpus_dirs = [os.path.join(self.tmp, "nope")]
        with self.assertRaises(RuntimeError):
            self.p.build(full=False)
        res2, _ = self.p.build(full=False, force=True)
        self.assertTrue(res2["alerts"])
        self.assertEqual(res2["alerts"][0]["check"], "corpus_dirs")
        self.p.s.toggles.health_check = False
        _, t3 = self.p.build(full=False)
        self.assertFalse({c["name"]: c for c in t3["children"]}["health"]["enabled"])
        out = run_captured(["health", "--quick"], self.s, self.p)
        self.assertIn("corpus_dirs", out["output"])

    # ---- 프리셋 ----
    def test_presets_apply_restore_diff(self):
        names = list(ps.load_presets().keys())
        self.assertIn("quality", names)
        self.assertTrue(os.path.exists(ps.presets_path()))
        s = self.s
        base_k = s.top_k_final
        d = ps.diff(s, "speed")
        self.assertTrue(any(r["key"] == "toggles.rerank_llm" and r["changes"] for r in d))
        r = ps.apply(s, ["quality", "token"])
        self.assertFalse(s.toggles.rerank_llm)             # token 이 뒤 → 우선
        self.assertTrue(any(c["key"] == "toggles.rerank_llm" for c in r["conflicts"]))
        self.assertEqual(s.top_k_final, 5)
        self.assertEqual(tn.T.get("answer_length_target"), "short")
        self.assertEqual(r["unknown"], [])
        ps.restore(s, r["prev"])
        self.assertEqual(s.top_k_final, base_k)
        self.assertTrue(s.toggles.rerank_llm)
        self.assertEqual(tn.T.get("answer_length_target"), "normal")
        # CLI --preset 은 요청 후 복원
        self.p.build(full=True)
        out = run_captured(["query", "RX DMA underrun", "--preset", "speed", "--json"], self.s, self.p)
        self.assertEqual(out["code"], 0)
        data = json.loads(out["output"])
        self.assertEqual(data["result"]["config"]["toggles"]["rerank_llm"], False)
        self.assertTrue(self.p.s.toggles.rerank_llm)        # run_captured 는 settings 사본에 적용하고 요청 후 복원
        self.assertEqual(tn.T.get("rerank_method"), "auto")
        out2 = run_captured(["preset", "apply", "offline"], self.s, self.p)
        self.assertIn("applied", out2["output"])
        # Web 콘솔은 요청 범위에서 실행된다: --save 없이는 서버 전역 설정이 바뀌지 않는다 (다중 사용자 격리, 2026-09-15)
        self.assertIn("이번 요청에만", out2["output"])
        self.assertTrue(self.p.s.toggles.llm_answer)
        out2b = run_captured(["preset", "apply", "offline", "--save"], self.s, self.p)
        self.assertIn("저장됨", out2b["output"])
        self.assertFalse(self.p.s.toggles.llm_answer)
        run_captured(["preset", "apply", "quality", "--save"], self.s, self.p)
        self.assertTrue(self.p.s.toggles.llm_answer)
        out3 = run_captured(["preset", "list"], self.s, self.p)
        self.assertIn("deep_research", out3["output"])
        out4 = run_captured(["config", "show", "--effective"], self.s, self.p)
        self.assertIn("LLMWIKI_TOP_K_FINAL", out4["output"])
        out5 = run_captured(["config", "paths"], self.s, self.p)
        self.assertIn("presets", out5["output"])


if __name__ == "__main__":
    unittest.main()
