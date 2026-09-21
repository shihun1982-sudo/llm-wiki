# -*- coding: utf-8 -*-
"""headless 전환 검증 (docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md §2.1, 단계 A1).

재현했던 증상: agents.json 의 opencode 항목이 `{prompt}` 를 인자로 두고 prompt_mode 가 arg 인 채 시스템 프롬프트 + 컨텍스트 39,719자가
argv 로 들어가 Windows CreateProcess 의 명령줄 32,767자 한계에 걸려 `OSError: [WinError 206]` 로 죽었다.
여기서는 목업 에이전트(`python -m llmwiki.headless --mock --prompt-arg {prompt}`)로
  (1) arg 모드 + 40,000자 프롬프트 → arg_max_chars 가드가 stdin(또는 {prompt_file} 이 있으면 file)으로 자동 전환하고 정상 응답 + 폴백 표시,
  (2) arg_max_chars=0 이면 전환하지 않음(짧은 프롬프트로 확인; Windows 에서는 긴 프롬프트가 실제로 WinError 206 으로 실패함을 확인),
  (3) config.json 만 바꿔 API(mock provider) ↔ headless(mock agent) 를 왕복
를 확인한다. 네트워크 없음. 서버를 띄우지 않는다.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki import headless as hl  # noqa: E402
from llmwiki.config import load_settings  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402
from llmwiki.providers import LLMError, MockLLM  # noqa: E402

LONG_USER = "질문: RX DMA underrun 원인 [C1] [C2]\n\n" + ("컨텍스트 " * 8000)     # 40,000자 남짓 (요청 당시 39,719자와 같은 규모; Windows 한계 32,767 초과)
assert len(LONG_USER) > 40000


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lwhl_")
        os.environ["LLMWIKI_AGENTS_PATH"] = os.path.join(self.tmp, "agents.json")
        for k in ("LLMWIKI_LLM_PROVIDER", "LLMWIKI_ANSWER_PROVIDER", "LLMWIKI_LLM_ROLES"):
            os.environ.pop(k, None)
        hl.save_agents(hl.DEFAULT_AGENTS)     # 기본값이 명시된 임시 agents.json (저장소 파일은 건드리지 않는다)

    def tearDown(self):
        os.environ.pop("LLMWIKI_AGENTS_PATH", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _agent(self, name, extra_args, **cfg):
        """mock 항목을 복제해 명령 인자·설정을 덧씌운 에이전트를 임시 agents.json 에 추가한다."""
        agents = hl.load_agents()
        a = json.loads(json.dumps(agents["mock"]))
        a["command"] = ["{python}", "-m", "llmwiki.headless", "--mock"] + extra_args
        a.update(cfg)
        agents[name] = a
        hl.save_agents(agents)


class ArgGuardTest(_Base):
    def test_defaults_are_explicit(self):
        """with_defaults/save_agents 가 arg_max_chars 와 prompt_mode=stdin 을 파일에 명시한다 (운영자가 기존 줄을 고쳐 바꿀 수 있게)."""
        with open(hl.agents_path(), encoding="utf-8") as f:
            data = json.load(f)
        for name in ("opencode", "claude", "codex", "mock"):
            self.assertEqual(data[name]["arg_max_chars"], 30000, name)
            self.assertEqual(data[name]["prompt_mode"], "stdin", name)
        self.assertIn("arg_max_chars", data["_comment"])
        self.assertEqual(hl.with_defaults({"command": ["x"]})["prompt_mode"], "stdin")
        self.assertEqual(hl.with_defaults({"command": ["x"]})["arg_max_chars"], 30000)
        # dict 로 직접 만든 항목(agents.json 을 거치지 않음)도 코드 기본값이 stdin 이다 — 예전에는 arg 라 WinError 206 의 원인이었다
        llm = hl.HeadlessAgentLLM("mock", "")
        llm.cfg = {"command": ["{python}", "-m", "llmwiki.headless", "--mock"]}
        self.assertEqual(llm._prompt_mode(), "stdin")

    def test_arg_mode_long_prompt_falls_back_to_stdin(self):
        """(1) arg + 40,000자 → argv 총 길이 > arg_max_chars(30000) → stdin 으로 자동 전환, 정상 응답, 폴백 표시."""
        self._agent("argmock", ["--prompt-arg", "{prompt}"], prompt_mode="arg")
        llm = hl.HeadlessAgentLLM("argmock", "")
        self.assertTrue(llm.available)
        r = llm.complete("TASK=answer\n규칙", LONG_USER, max_tokens=100)
        self.assertIn("(mock answer)", r["text"])
        self.assertIn("[C1]", r["text"])                     # 프롬프트 전체가 stdin 으로 전달되어 인용이 살아 있다
        self.assertEqual(r["prompt_mode"], "stdin")
        self.assertIn("arg→stdin", r["prompt_mode_fallback"])
        self.assertIn("arg_max_chars=30000", r["prompt_mode_fallback"])
        # trace 의 LLM 단계 meta 는 usage 만 옮기므로(st.note(usage=…)) 전환 표시가 usage 에도 실린다
        self.assertEqual(r["usage"]["prompt_mode_fallback"], r["prompt_mode_fallback"])
        self.assertEqual(r["exit_code"], 0)

    def test_arg_mode_long_prompt_falls_back_to_file_when_template_has_prompt_file(self):
        """템플릿에 {prompt_file} 이 있으면 stdin 이 아니라 file 로 전환한다 (stdin 을 못 받는 에이전트용)."""
        self._agent("filemock", ["--prompt-file", "{prompt_file}", "--prompt-arg", "{prompt}"], prompt_mode="arg")
        llm = hl.HeadlessAgentLLM("filemock", "")
        r = llm.complete("TASK=answer", LONG_USER, max_tokens=100)
        self.assertIn("[C1]", r["text"])
        self.assertEqual(r["prompt_mode"], "file")
        self.assertIn("arg→file", r["prompt_mode_fallback"])

    def test_arg_max_chars_zero_disables_guard(self):
        """(2) arg_max_chars=0 + 짧은 프롬프트 → 전환 없음, 프롬프트가 인자로 전달된다(stdin 은 DEVNULL)."""
        self._agent("argmock0", ["--prompt-arg", "{prompt}"], prompt_mode="arg", arg_max_chars=0)
        llm = hl.HeadlessAgentLLM("argmock0", "")
        r = llm.complete("TASK=answer", "짧은 질문 [C1]", max_tokens=50)
        self.assertIn("[C1]", r["text"])                     # 인자로 받은 프롬프트로 답했다
        self.assertEqual(r["prompt_mode"], "arg")
        self.assertNotIn("prompt_mode_fallback", r)
        self.assertNotIn("prompt_mode_fallback", r["usage"])
        # 기본 가드(30000)라도 짧은 프롬프트는 arg 그대로
        self._agent("argmock1", ["--prompt-arg", "{prompt}"], prompt_mode="arg")
        r2 = hl.HeadlessAgentLLM("argmock1", "").complete("TASK=answer", "짧은 질문 [C2]", max_tokens=50)
        self.assertEqual(r2["prompt_mode"], "arg")
        self.assertNotIn("prompt_mode_fallback", r2)

    @unittest.skipUnless(os.name == "nt", "Windows CreateProcess 명령줄 한계(32,767자)에서만 재현")
    def test_arg_max_chars_zero_long_prompt_reproduces_winerror_206(self):
        """가드를 끈 채 긴 프롬프트를 arg 로 넘기면 원래 증상(WinError 206 → LLMError kind=exec)이 그대로 난다 — 가드가 막는 것이 이것이다."""
        self._agent("argraw", ["--prompt-arg", "{prompt}"], prompt_mode="arg", arg_max_chars=0, retries=0)
        llm = hl.HeadlessAgentLLM("argraw", "")
        with self.assertRaises(LLMError) as cm:
            llm.complete("TASK=answer", LONG_USER, max_tokens=50)
        self.assertEqual(cm.exception.kind, "exec")
        self.assertIn("206", str(cm.exception))

    def test_mask_argv_hides_inlined_prompt(self):
        """로그용 argv: 프롬프트가 인자 그대로든 더 긴 인자 안에 인라인됐든 `<prompt:N chars>` 로 가린다."""
        prompt = "TASK=answer\n\n" + LONG_USER
        masked = hl.HeadlessAgentLLM._mask_argv(["agent", "--prompt-arg", prompt, "--json"], prompt)
        self.assertEqual(masked[2], "<prompt:%d chars>" % len(prompt))
        inl = "--prompt=" + prompt + "\n\n### FILE a.md\nevidence"
        masked2 = hl.HeadlessAgentLLM._mask_argv(["agent", inl], prompt)
        self.assertEqual(masked2[1], "<prompt:%d chars>" % len(inl))
        self.assertNotIn("컨텍스트", " ".join(masked + masked2))

    def test_stdin_mode_unchanged(self):
        """기존 stdin 모드: {prompt} 인자가 빠지고 프롬프트는 파이프로 간다 (전환 표시 없음)."""
        llm = hl.HeadlessAgentLLM("mock", "")
        r = llm.complete("TASK=answer", LONG_USER, max_tokens=50)
        self.assertIn("[C1]", r["text"])
        self.assertEqual(r["prompt_mode"], "stdin")
        self.assertNotIn("prompt_mode_fallback", r)
        args = llm._render(["{python}", "-m", "llmwiki.headless", "--mock", "{prompt}"], "P", "")
        self.assertNotIn("P", args)
        self.assertIn("P", llm._render(["{python}", "--mock", "{prompt}"], "P", "", mode="arg"))


class ConfigSwitchTest(_Base):
    """(3) config.json 만 바꿔 headless(mock agent) ↔ API(mock provider) 왕복 — 코드 변경 없이 llm_roles.answer.provider 로 전환된다."""

    def setUp(self):
        _Base.setUp(self)
        corpus = os.path.join(self.tmp, "corpus")
        os.makedirs(corpus)
        with open(os.path.join(corpus, "a.md"), "w", encoding="utf-8") as f:
            f.write("# ISSUE-1 RX DMA underrun\n\nRX DMA underrun 시 PHY 재시작 실패. CL-5 로 수정.\n\n## 원인\nFIFO 임계값 오류\n")
        self.cfg_path = os.path.join(self.tmp, "config.json")
        self.cfg = {"corpus_dirs": [corpus], "data_dir": os.path.join(self.tmp, "data"), "wiki_dir": os.path.join(self.tmp, "wiki"),
                    "llm_provider": "mock", "embed_provider": "hash", "embed_dim": 256,
                    "llm_roles": {"answer": {"provider": "headless:mock"}},
                    "toggles": {"query_cache": False, "health_check": False, "precompute": False}}
        self._write()
        self.p = Pipeline(load_settings(self.cfg_path))
        self.p.build(full=True)

    def tearDown(self):
        self.p.store.close()
        _Base.tearDown(self)

    def _write(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)

    def _switch(self, provider):
        """config.json 의 llm_roles.answer.provider 만 고쳐 쓰고 파일에서 다시 읽어 파이프라인에 적용한다 (서버의 config 재적재와 같은 경로)."""
        self.cfg["llm_roles"]["answer"]["provider"] = provider
        self._write()
        self.p.s = load_settings(self.cfg_path)
        self.p.reload()

    def test_switch_headless_and_api_by_config_only(self):
        llm = self.p.llm_for("answer")
        self.assertIsInstance(llm, hl.HeadlessAgentLLM)
        self.assertEqual(llm.name, "headless:mock")
        self.assertIn("(mock answer)", llm.complete("TASK=answer", "q [C1]")["text"])
        r, _ = self.p.query("RX DMA underrun", log=False)
        self.assertEqual(r["config"]["llm"], "headless:mock")
        # → API mock provider
        self._switch("mock")
        llm2 = self.p.llm_for("answer")
        self.assertIsInstance(llm2, MockLLM)
        self.assertEqual(llm2.name, "mock")
        r2, _ = self.p.query("RX DMA underrun", log=False)
        self.assertEqual(r2["config"]["llm"], "mock")
        # → 다시 headless (파일만 바꿨는데 새 인스턴스로 해석된다)
        self._switch("headless:mock")
        llm3 = self.p.llm_for("answer")
        self.assertIsInstance(llm3, hl.HeadlessAgentLLM)
        self.assertIsNot(llm3, llm)
        self.assertEqual(self.p.query("RX DMA underrun", log=False)[0]["config"]["llm"], "headless:mock")


if __name__ == "__main__":
    unittest.main()
