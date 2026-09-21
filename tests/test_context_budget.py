# -*- coding: utf-8 -*-
"""컨텍스트 예산 — "근거가 모델에게 실제로 도착하는가".

## 무엇이 문제였나

컨텍스트 상한(`context_max_chars`)은 **사람이 정한 값**이고 모델의 입력 창과는 아무 관계가 없었다.
`models.json` 에 `context_k`(모델 창)가 이미 있었지만 어디에서도 쓰이지 않았다. 그래서 두 가지가 조용히 일어났다.

  A. **창 초과** — 창이 작은 모델(예 `context_k: 8`)에 긴 컨텍스트를 넣으면 프롬프트가 잘린 채 호출된다.
     뒤쪽 근거가 통째로 사라지지만 모델은 그 사실을 말해 주지 않아서, 인용 번호만 맞고 내용은 빈 답이 나온다.
  B. **순위가 아니라 길이 때문에 빠지는 근거** — 조립 루프는 상한을 처음 넘기는 청크에서 `break` 했다.
     2위에 큰 청크 하나가 있으면 3위 이하는 자리가 남아 있어도 통째로 빠졌다.

## 이 테스트가 고정하는 것

  1. 창 정보를 모르면(카탈로그 밖 모델) **설정값을 그대로** 쓴다 — 모른다고 근거를 줄이지 않는다
  2. 창이 좁으면 설정값보다 **작은 상한**을 쓰고, 그 이유가 관측 가능하다
  3. 같은 id 가 여러 provider 에 있으면 **가장 작은 창**을 쓴다 (잘리는 쪽이 더 나쁘다)
  4. 큰 청크 하나가 상한에 걸려도 **뒤 순위 근거가 계속 들어간다** (잘라 넣거나 건너뛰고 계속)
  5. 잘라 넣은 것과 아예 빠진 것을 **구분해서** 보고한다

참고: `llmwiki/models_catalog.py: context_budget/window_tokens` · `llmwiki/answer.py: build_context` ·
튜닝 `context_chars_per_token` · `context_budget_reserve_tokens` · `context_min_fit_chars`
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import models_catalog as mc      # noqa: E402
from llmwiki import tuning as _tuning         # noqa: E402
from llmwiki.answer import build_context      # noqa: E402
from llmwiki.config import Settings           # noqa: E402


class _Hit:
    def __init__(self, cid):
        self.chunk_id = cid


def _chunks(*texts):
    out = {}
    for i, t in enumerate(texts, 1):
        cid = "corpus/doc%d.md#1" % i
        out[cid] = {"chunk_id": cid, "doc_id": "corpus/doc%d.md" % i, "heading": "제목%d" % i,
                    "text": t, "n": 1, "start": 0, "end": len(t)}
    return out


class _CatalogFile:
    """임시 models.json 을 물려 카탈로그를 바꿔 끼운다 (LLMWIKI_MODELS_PATH)."""

    def __init__(self, models):
        self.models = models

    def __enter__(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"models": self.models, "embed": []}, self.tmp, ensure_ascii=False)
        self.tmp.close()
        self.prev = os.environ.get("LLMWIKI_MODELS_PATH")
        os.environ["LLMWIKI_MODELS_PATH"] = self.tmp.name
        mc.load_catalog(force=True)
        return self

    def __exit__(self, *a):
        if self.prev is None:
            os.environ.pop("LLMWIKI_MODELS_PATH", None)
        else:
            os.environ["LLMWIKI_MODELS_PATH"] = self.prev
        mc.load_catalog(force=True)
        os.unlink(self.tmp.name)


def _settings(model, max_chars=9000, out_tokens=2000):
    s = Settings(context_max_chars=max_chars, answer_max_tokens=out_tokens)
    s.llm_roles = {"answer": {"model": model}}
    return s


class WindowTest(unittest.TestCase):
    def test_unknown_model_means_no_limit(self):
        with _CatalogFile([{"id": "big", "provider": "openai", "context_k": 200, "enabled": True}]):
            self.assertEqual(mc.window_tokens("없는-모델"), 0)
            b = mc.context_budget(_settings("없는-모델"))
            self.assertEqual(b["chars"], 9000)
            self.assertFalse(b["limited"])
            self.assertIn("창 정보 없음", b["reason"])

    def test_context_k_zero_means_unknown(self):
        with _CatalogFile([{"id": "m", "provider": "openai", "context_k": 0, "enabled": True}]):
            self.assertEqual(mc.window_tokens("m"), 0)
            self.assertFalse(mc.context_budget(_settings("m"))["limited"])

    def test_smallest_window_wins_across_providers(self):
        """같은 id 가 두 provider 에 있으면 좁은 쪽 — 창을 넘겨 잘리는 쪽이 더 나쁘다."""
        with _CatalogFile([{"id": "qwen2.5:7b", "provider": "ollama", "context_k": 32, "enabled": True},
                           {"id": "qwen2.5:7b", "provider": "openai", "context_k": 128, "enabled": True}]):
            self.assertEqual(mc.window_tokens("qwen2.5:7b"), 32000)

    def test_disabled_entry_ignored(self):
        with _CatalogFile([{"id": "m", "provider": "ollama", "context_k": 8, "enabled": False},
                           {"id": "m", "provider": "openai", "context_k": 128, "enabled": True}]):
            self.assertEqual(mc.window_tokens("m"), 128000)

    def test_narrow_window_shrinks_the_cap(self):
        # 창 8k − 출력 2000 − 예비 2000 = 4000 토큰 × 2.0 글자/토큰 = 8000자 < 설정 20000자
        with _CatalogFile([{"id": "small", "provider": "ollama", "context_k": 8, "enabled": True}]):
            b = mc.context_budget(_settings("small", max_chars=20000))
            self.assertTrue(b["limited"])
            self.assertEqual(b["chars"], 8000)
            self.assertEqual(b["configured"], 20000)
            self.assertIn("8000자", b["reason"])       # 왜 줄었는지 사람이 읽을 수 있어야 한다

    def test_wide_window_leaves_setting_alone(self):
        with _CatalogFile([{"id": "big", "provider": "anthropic", "context_k": 200, "enabled": True}]):
            b = mc.context_budget(_settings("big", max_chars=20000))
            self.assertFalse(b["limited"])
            self.assertEqual(b["chars"], 20000)

    def test_never_collapses_to_zero(self):
        """출력 상한이 창보다 커도 최소한의 근거는 넣는다 — 근거 0 으로 답하게 두지 않는다."""
        with _CatalogFile([{"id": "tiny", "provider": "ollama", "context_k": 2, "enabled": True}]):
            b = mc.context_budget(_settings("tiny", max_chars=9000, out_tokens=8000))
            self.assertTrue(b["limited"])
            self.assertGreaterEqual(b["chars"], 500)

    def test_chars_per_token_is_tunable(self):
        with _CatalogFile([{"id": "small", "provider": "ollama", "context_k": 8, "enabled": True}]):
            prev = _tuning.T.get("context_chars_per_token")
            try:
                _tuning.T.set("context_chars_per_token", 4.0)
                self.assertEqual(mc.context_budget(_settings("small", max_chars=20000))["chars"], 16000)
            finally:
                _tuning.T.set("context_chars_per_token", prev)

    def test_budget_survives_broken_settings(self):
        """예산 계산이 실패해도 질의는 계속된다 (설정값 사용)."""
        class Bad:
            context_max_chars = 9000
            answer_max_tokens = 2000

            @property
            def llm_roles(self):
                raise RuntimeError("boom")
        b = mc.context_budget(Bad())
        self.assertEqual(b["chars"], 9000)
        self.assertFalse(b["limited"])


class AssemblyOverflowTest(unittest.TestCase):
    """상한에 걸렸을 때 뒤 순위 근거가 어떻게 되나."""

    def _ctx(self, texts, max_chars, min_fit=None):
        ch = _chunks(*texts)
        hits = [_Hit(c) for c in ch]
        prev = _tuning.T.get("context_min_fit_chars")
        try:
            if min_fit is not None:
                _tuning.T.set("context_min_fit_chars", min_fit)
            return build_context(hits, ch, None, max_chars, guard=False)
        finally:
            _tuning.T.set("context_min_fit_chars", prev)

    def test_small_hits_after_a_huge_one_still_get_in(self):
        """예전에는 여기서 break 해서 C2·C3 가 통째로 빠졌다 — 길이 때문이지 순위 때문이 아니었는데도."""
        ctx = self._ctx(["가" * 5000, "나" * 100, "다" * 100], max_chars=1000, min_fit=0)
        ids = [c["chunk_id"] for c in ctx["citations"]]
        self.assertEqual(len(ids), 2, "큰 청크 뒤의 작은 근거가 들어가지 않았다: %s" % ids)
        self.assertIn("corpus/doc2.md#1", ids)
        self.assertIn("corpus/doc3.md#1", ids)
        self.assertTrue(any("doc1" in d for d in ctx["dropped"]))

    def test_truncate_when_there_is_usable_room(self):
        ctx = self._ctx(["가" * 5000, "나" * 100], max_chars=1000, min_fit=400)
        ids = [c["chunk_id"] for c in ctx["citations"]]
        self.assertIn("corpus/doc1.md#1", ids)          # 잘려서라도 들어간다
        self.assertEqual(ctx["truncated"], ["corpus/doc1.md#1"])
        self.assertLessEqual(ctx["chars"], 1000)
        self.assertIn("…", ctx["text"])

    def test_truncated_and_dropped_are_reported_separately(self):
        """'잘려서 들어간 것' 과 '아예 빠진 것' 은 진단이 다르다 — 섞어 보고하면 원인을 못 찾는다."""
        ctx = self._ctx(["가" * 5000, "나" * 5000], max_chars=1200, min_fit=400)
        self.assertEqual(len(ctx["truncated"]), 1)
        self.assertTrue(ctx["dropped"])
        self.assertNotIn(ctx["truncated"][0], [d.split(" ")[0] for d in ctx["dropped"]])

    def test_never_exceeds_the_cap(self):
        for mf in (0, 400, 2000):
            ctx = self._ctx(["가" * 3000, "나" * 3000, "다" * 3000], max_chars=2000, min_fit=mf)
            self.assertLessEqual(ctx["chars"], 2000, "min_fit=%d 에서 상한을 넘었다" % mf)

    def test_citation_numbers_stay_dense(self):
        """건너뛴 근거 때문에 [C2] 가 비면 안 된다 — 모델이 없는 번호를 인용하게 된다."""
        ctx = self._ctx(["가" * 5000, "나" * 100, "다" * 100], max_chars=1000, min_fit=0)
        self.assertEqual([c["n"] for c in ctx["citations"]], list(range(1, len(ctx["citations"]) + 1)))
        for c in ctx["citations"]:
            self.assertIn("[C%d]" % c["n"], ctx["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
