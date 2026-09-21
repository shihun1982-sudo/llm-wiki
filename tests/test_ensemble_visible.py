# -*- coding: utf-8 -*-
"""앙상블로 돌았다는 것이 **보이는가** (2026-09-20 요청).

사용자가 `answer_llm` 단계 JSON 을 보고 물었다: *"이거 앙상블로 동작한건가?"*
그때 단서는 `"model": "llama3.1+llama3.1+llama3.1"` 의 **`+` 뿐**이었다. 멤버가 몇 개 성공했는지,
누가 느렸는지, 취합에 얼마를 더 썼는지는 결과(`r["ensemble"]`)에만 있고 trace 로 올라오지 않아
화면에도 CLI 에도 나오지 않았다.

여기서 지키는 것
  · `answer_llm` 단계 meta 에 `ensemble` 요약이 실린다 (멤버별 모델·ms·토큰·성공 여부 + 취합기)
  · 앙상블이 **아니면** 그 키가 없다 (단일 호출에 빈 표가 뜨면 오히려 헷갈린다)
  · 본문(text)은 싣지 않는다 — trace 는 요청마다 저장되므로 크기가 배로 늘어난다
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki.providers import summarize_ensemble   # noqa: E402
from llmwiki import profiler as _prof               # noqa: E402


class NoteCurrentTest(unittest.TestCase):
    """**지금 열려 있는 단계**에 프로바이더가 직접 적을 수 있어야 한다.

    앙상블은 역할 11개 어디에나 켤 수 있다. 호출 자리마다 같은 코드를 넣으면 한 자리만 빠뜨려도
    그 역할은 "앙상블인지 알 수 없는" 상태가 된다 — 그래서 프로바이더 한 곳에서 적는다.
    """

    def test_writes_into_the_open_stage(self):
        p = _prof.Profiler("t", log=False)
        with p.stage("some_llm"):
            self.assertTrue(_prof.note_current(ensemble={"n_members": 2}))
        d = p.finish()
        st = [c for c in d["children"] if c["name"] == "some_llm"][0]
        self.assertEqual(st["meta"]["ensemble"]["n_members"], 2)

    def test_is_silent_outside_a_stage(self):
        """단계 밖에서 불려도 터지지 않는다 — 관측이 본 기능을 막으면 안 된다."""
        self.assertFalse(_prof.note_current(ensemble={"x": 1}))

    def test_restores_the_previous_stage(self):
        """중첩 단계에서 빠져나오면 **바깥 단계**로 돌아가야 한다 (안 그러면 엉뚱한 줄에 적힌다)."""
        p = _prof.Profiler("t", log=False)
        with p.stage("outer"):
            with p.stage("inner"):
                _prof.note_current(mark="inner")
            _prof.note_current(mark="outer")
        d = p.finish()
        outer = [c for c in d["children"] if c["name"] == "outer"][0]
        self.assertEqual(outer["meta"]["mark"], "outer")
        self.assertEqual(outer["children"][0]["meta"]["mark"], "inner")


class SummarizeTest(unittest.TestCase):
    RAW = {
        "members": [
            {"model": "llama3.1", "provider": "openai", "weight": 1.0, "ok": True, "ms": 9000.4,
             "chars": 820, "usage": {"input_tokens": 1000, "output_tokens": 300}, "text": "본문" * 500},
            {"model": "qwen2.5", "provider": "ollama", "weight": 1.0, "ok": True, "ms": 12000.9,
             "chars": 640, "usage": {"input_tokens": 1010, "output_tokens": 280}, "text": "본문" * 400},
            {"model": "llama3.1", "provider": "openai", "weight": 0.5, "ok": False, "ms": 120000.0,
             "chars": 0, "usage": {}, "error": "ensemble wait=timeout: 120s 안에 응답 없음"},
        ],
        "aggregator": {"model": "llama3.1", "provider": "openai", "ms": 5300.2,
                       "usage": {"input_tokens": 4100, "output_tokens": 800}},
        "policy": {"wait": "all", "timeout_s": 120}, "aggregated": True,
    }

    def test_not_ensemble_returns_none(self):
        """단일 호출이면 키 자체가 없어야 한다 — 빈 표를 그리면 오히려 헷갈린다."""
        self.assertIsNone(summarize_ensemble(None))
        self.assertIsNone(summarize_ensemble({}))
        self.assertIsNone(summarize_ensemble({"members": []}))

    def test_counts_members_and_successes(self):
        s = summarize_ensemble(self.RAW)
        self.assertEqual(s["n_members"], 3)
        self.assertEqual(s["n_ok"], 2, "실패한 멤버를 성공으로 세면 '다 잘 됐다' 로 읽힌다")
        self.assertTrue(s["aggregated"])

    def test_keeps_per_member_detail(self):
        """'누가 느렸나' 에 답하려면 멤버마다 모델·ms·토큰이 있어야 한다."""
        m = summarize_ensemble(self.RAW)["members"]
        self.assertEqual([x["model"] for x in m], ["llama3.1", "qwen2.5", "llama3.1"])
        self.assertEqual([x["provider"] for x in m], ["openai", "ollama", "openai"])
        self.assertEqual(m[0]["ms"], 9000)
        self.assertEqual(m[1]["input_tokens"], 1010)
        self.assertFalse(m[2]["ok"])
        self.assertIn("timeout", m[2]["error"])

    def test_reports_aggregator_cost(self):
        """취합 LLM 도 **한 번 더 부르는 비용**이다 — 따로 보여야 한다."""
        a = summarize_ensemble(self.RAW)["aggregator"]
        self.assertEqual(a["ms"], 5300)
        self.assertEqual(a["input_tokens"], 4100)

    def test_slowest_member_is_the_bar_baseline(self):
        """멤버는 **병렬**이라 합이 단계 시간이 아니다 — 화면 막대는 가장 느린 멤버 기준이다."""
        self.assertEqual(summarize_ensemble(self.RAW)["member_ms_max"], 120000)

    def test_does_not_carry_answer_text(self):
        """trace 는 요청마다 저장된다 — 멤버 본문까지 실으면 크기가 배로 는다."""
        s = summarize_ensemble(self.RAW)
        self.assertNotIn("text", s["members"][0])
        import json
        self.assertNotIn("본문", json.dumps(s, ensure_ascii=False))


class EndToEndTest(unittest.TestCase):
    """실제로 앙상블을 켜고 질의해 **trace 에 올라오는지** 본다 (mock LLM).

    요약 함수만 테스트하면 "만들어는 놨는데 아무도 안 부르는" 상태를 못 잡는다 —
    이 저장소에서 여러 번 겪은 유형이다.
    """

    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        cls.tmp = tempfile.mkdtemp(prefix="llmwiki_ens_")
        cls._saved = os.environ.get("LLMWIKI_LOGS_DIR_PATH")
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(cls.tmp, "logs")
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        with open(os.path.join(corpus, "a.md"), "w", encoding="utf-8") as f:
            f.write("---\ndoc_type: issue\next_id: ISSUE-1\n---\n\n# RX DMA underrun\n\n"
                    "RX DMA 에서 underrun 이 발생하면 PHY 재시작이 실패한다. rev B1 에서 t_setup 은 4 ns 이다.\n" * 3)
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"),
                     wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", llm_model="mock-a", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False, query_cache=False, llm_answer=True)
        # answer 역할만 앙상블 — 멤버 2 + 취합 1
        s.llm_roles = dict(getattr(s, "llm_roles", {}) or {}, answer={
            "ensemble": {"enabled": True,
                         "members": [{"enabled": True, "provider": "mock", "model": "mock-a", "weight": 1.0},
                                     {"enabled": True, "provider": "mock", "model": "mock-b", "weight": 1.0}],
                         "aggregator": {"provider": "mock", "model": "mock-agg"}}})
        cls.p = Pipeline(s)
        cls.p.build(full=True)
        cls.res, cls.tr = cls.p.query("RX DMA underrun 원인", log=False)
        cls.shutil, cls.tempfile = shutil, tempfile

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        if cls._saved is None:
            os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
        else:
            os.environ["LLMWIKI_LOGS_DIR_PATH"] = cls._saved
        cls.shutil.rmtree(cls.tmp, ignore_errors=True)

    def _answer_stage(self):
        found = []

        def walk(n):
            if n.get("name") == "answer_llm":
                found.append(n)
            for c in (n.get("children") or []):
                walk(c)
        walk(self.tr)
        return found[0] if found else None

    def test_answer_stage_says_it_was_an_ensemble(self):
        st = self._answer_stage()
        self.assertIsNotNone(st, "answer_llm 단계가 trace 에 없다 — LLM 답변이 돌지 않았다")
        ens = (st.get("meta") or {}).get("ensemble")
        self.assertIsNotNone(ens, "앙상블로 돌았는데 trace 에 그 사실이 없다 "
                                  "— 화면·CLI 가 model 이름의 '+' 로 유추해야 한다")
        self.assertEqual(ens["n_members"], 2)
        self.assertEqual(len(ens["members"]), 2)
        self.assertTrue(ens["members"][0]["model"])

    def test_works_for_roles_other_than_answer(self):
        """**answer 만이 아니다.** 앙상블은 역할 11개 어디에나 켤 수 있고, 어디에 켜도 보여야 한다.

        answer 에만 붙였다면 rerank·summary 에 켠 사람은 여전히 알 수 없다 — 그 상태가 원래 문제였다.
        """
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        tmp = self.tempfile.mkdtemp(prefix="llmwiki_ens2_")
        saved = os.environ.get("LLMWIKI_LOGS_DIR_PATH")
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(tmp, "logs")
        try:
            corpus = os.path.join(tmp, "corpus")
            os.makedirs(corpus)
            with open(os.path.join(corpus, "a.md"), "w", encoding="utf-8") as f:
                f.write("---\ndoc_type: issue\next_id: ISSUE-1\n---\n\n# RX DMA\n\nRX DMA underrun 이 난다.\n" * 4)
            s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data"),
                         wiki_dir=os.path.join(tmp, "wiki"),
                         llm_provider="mock", embed_provider="hash", embed_dim=64)
            # **rerank** 역할에만 앙상블 (answer 아님)
            s.toggles = Toggles(llm_graph=False, community_summary=False, query_cache=False,
                                llm_answer=False, rerank=True, rerank_llm=True)
            s.llm_roles = dict(getattr(s, "llm_roles", {}) or {}, rerank={
                "ensemble": {"enabled": True,
                             "members": [{"enabled": True, "provider": "mock", "model": "mock-r1", "weight": 1.0},
                                         {"enabled": True, "provider": "mock", "model": "mock-r2", "weight": 1.0}],
                             "aggregator": {"provider": "mock", "model": "mock-ragg"}}})
            p = Pipeline(s)
            try:
                p.build(full=True)
                _res, tr = p.query("RX DMA underrun", log=False)
                found = []

                def walk(n):
                    if (n.get("meta") or {}).get("ensemble"):
                        found.append(n)
                    for c in (n.get("children") or []):
                        walk(c)
                walk(tr)
                self.assertTrue(found, "rerank 역할에 앙상블을 켰는데 trace 어디에도 표시가 없다 "
                                       "— answer 말고 다른 역할은 여전히 알 수 없는 상태다")
                ens = (found[0].get("meta") or {})["ensemble"]
                self.assertEqual(ens["n_members"], 2)
                self.assertEqual(ens.get("role"), "rerank", "어느 역할의 앙상블인지도 남아야 한다")
            finally:
                p.store.close()
        finally:
            if saved is None:
                os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
            else:
                os.environ["LLMWIKI_LOGS_DIR_PATH"] = saved
            self.shutil.rmtree(tmp, ignore_errors=True)

    def test_single_llm_run_has_no_ensemble_key(self):
        """앙상블을 끄면 그 키가 없어야 한다 (빈 표가 뜨면 오히려 헷갈린다)."""
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        tmp = self.tempfile.mkdtemp(prefix="llmwiki_noens_")
        saved = os.environ.get("LLMWIKI_LOGS_DIR_PATH")
        os.environ["LLMWIKI_LOGS_DIR_PATH"] = os.path.join(tmp, "logs")
        try:
            corpus = os.path.join(tmp, "corpus")
            os.makedirs(corpus)
            with open(os.path.join(corpus, "a.md"), "w", encoding="utf-8") as f:
                f.write("---\ndoc_type: issue\next_id: ISSUE-1\n---\n\n# RX DMA\n\nRX DMA underrun 이 난다.\n" * 3)
            s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data"),
                         wiki_dir=os.path.join(tmp, "wiki"),
                         llm_provider="mock", embed_provider="hash", embed_dim=64)
            s.toggles = Toggles(llm_graph=False, community_summary=False, query_cache=False, llm_answer=True)
            p = Pipeline(s)
            try:
                p.build(full=True)
                _res, tr = p.query("RX DMA underrun", log=False)
                found = []

                def walk(n):
                    if n.get("name") == "answer_llm":
                        found.append(n)
                    for c in (n.get("children") or []):
                        walk(c)
                walk(tr)
                if found:
                    self.assertIsNone((found[0].get("meta") or {}).get("ensemble"),
                                      "앙상블이 아닌데 ensemble 키가 붙었다")
            finally:
                p.store.close()
        finally:
            if saved is None:
                os.environ.pop("LLMWIKI_LOGS_DIR_PATH", None)
            else:
                os.environ["LLMWIKI_LOGS_DIR_PATH"] = saved
            self.shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
