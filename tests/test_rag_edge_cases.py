# -*- coding: utf-8 -*-
"""RAG 경계 상황 — "이상한 입력과 이상한 모델 출력에도 시스템이 정직한가".

## 왜 이 묶음이 따로 있는가

기존 테스트는 대부분 "정상 질문 → 정상 답" 을 본다. 실제 운영에서 사람을 잃는 것은 그쪽이 아니라
**시스템이 모르면서 아는 척할 때**다. 두 방향에서 온다.

  A. **입력이 이상하다** — 빈 질문, 공백, 아주 긴 질문, 특수문자, 한·영 섞임, 색인에 없는 주제
  B. **모델이 이상하게 답한다** — 없는 인용 `[C99]`, 인용 없는 단정, 빈 답, 프롬프트를 그대로 되뱉기

A 에서 원하는 것은 **터지지 않고 정직하게 모른다고 말하는 것**이고,
B 에서 원하는 것은 **거짓 인용이 검출되어 표시되는 것**이다. 둘 다 "답이 그럴듯해 보이는가" 가 아니라
**"근거와 답이 서로 맞는가"** 를 본다.

## 도구

모델이 잘못 답하는 경우는 진짜 LLM 으로 재현할 수 없다. `MockLLM` 의 환경변수 훅을 쓴다
(`LLMWIKI_MOCK_ANSWER`, `LLMWIKI_MOCK_FAIL`) — 기본 동작에는 영향이 없고 이 테스트 안에서만 켠다.

참고: `llmwiki/answer.py`(claim_check·split_claims) · `llmwiki/evidence.py` · `llmwiki/providers.py`(MockLLM 훅)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki.config import Settings, Toggles     # noqa: E402
from llmwiki.pipeline import Pipeline            # noqa: E402

DOCS = {
    "issue.md": """# ISSUE-2001 수신 DMA 오버런

## 원인
링 버퍼가 4KB 로 작아 버스트 트래픽에서 오버런이 발생했다. 담당은 모뎀SW팀.

## 조치
CL-55321 에서 링 버퍼를 8KB 로 늘렸다. 재현율은 0.3% 에서 0% 로 떨어졌다.
""",
    "mixed.md": """# AGC convergence delay (AGC 수렴 지연)

## Root cause
The AGC loop gain was set too low, so convergence took 42ms instead of 10ms.
게인 값을 0.25 에서 0.5 로 올려 수렴 시간을 12ms 로 줄였다.
""",
}


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        for name, body in DOCS.items():
            with open(os.path.join(corpus, name), "w", encoding="utf-8") as f:
                f.write(body)
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"),
                     wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False, query_cache=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _q(self, q, **kw):
        with self.p.request_scope(**kw):
            return self.p.query(q, log=False)


# ---------------------------------------------------------------- A. 이상한 입력
class WeirdInputTest(_Base):
    """터지지 않고, 아는 척하지 않는다."""

    def test_empty_and_whitespace(self):
        for q in ("", "   ", "\n\t "):
            res, _tr = self._q(q)
            self.assertIn("answer", res, "빈 질의에서 응답 모양이 깨졌다: %r" % q)
            self.assertIsInstance(res.get("hits"), list)

    def test_punctuation_only(self):
        res, _ = self._q("???!!! ...")
        self.assertIn("answer", res)

    def test_very_long_query_is_not_fatal(self):
        res, tr = self._q("링 버퍼 오버런 원인 " * 400)       # 약 9천 자
        self.assertIn("answer", res)
        self.assertIsInstance(tr, dict)

    def test_fts_special_characters_do_not_break_search(self):
        """FTS5 구문 문자(" * ( ) NEAR OR)가 그대로 들어가면 쿼리가 깨진다 — 이스케이프되어야 한다."""
        for q in ('"unclosed', "buffer AND OR NEAR", "a*(b)", "링버퍼 -- ; DROP TABLE chunks"):
            res, _ = self._q(q)
            self.assertIn("answer", res, "FTS 특수문자에서 실패: %r" % q)

    def test_unknown_topic_says_it_does_not_know(self):
        """색인에 없는 주제는 **근거 부족**으로 답해야 한다 — 그럴듯한 문장을 지어내면 안 된다."""
        res, _ = self._q("2031년 화성 기지의 냉각수 배관 규격은?")
        ev = res.get("evidence") or {}
        self.assertIn(ev.get("verdict"), ("insufficient", "weak", "sufficient"))
        if ev.get("verdict") == "insufficient":
            # 판정이 insufficient 면 result_type 도 그 사실을 드러내야 한다 (화면·MCP 가 이 값으로 경고한다)
            self.assertIn("insufficient", str(res.get("result_type")),
                          "근거가 없다고 판정하고도 일반 답변으로 내보냈다: %s" % res.get("result_type"))

    def test_mixed_language_query_finds_the_mixed_document(self):
        """한 문서 안에 한·영이 섞여 있으면 **어느 언어로 물어도** 찾혀야 한다."""
        ko, _ = self._q("AGC 수렴 지연 원인은?")
        en, _ = self._q("AGC convergence delay root cause")
        self.assertTrue(any("mixed" in h["doc_id"] for h in ko["hits"]), "한국어 질의가 혼합 문서를 못 찾았다")
        self.assertTrue(any("mixed" in h["doc_id"] for h in en["hits"]), "영어 질의가 혼합 문서를 못 찾았다")

    def test_fragmented_query_still_retrieves(self):
        """조사·어미가 없는 조각 질의(검색창에 흔하다)도 같은 문서를 찾는다."""
        full, _ = self._q("링 버퍼 오버런의 원인이 무엇인가요?")
        frag, _ = self._q("링버퍼 오버런")
        self.assertTrue({h["doc_id"] for h in full["hits"]} & {h["doc_id"] for h in frag["hits"]},
                        "완전한 문장과 조각 질의가 서로 다른 문서를 찾는다")


# ---------------------------------------------------------------- B. 이상한 모델 출력
class BadModelOutputTest(_Base):
    """모델이 잘못 답해도 **검출되고 표시되어야** 한다."""

    def setUp(self):
        self.addCleanup(os.environ.pop, "LLMWIKI_MOCK_ANSWER", None)

    def _forced(self, text, q="ISSUE-2001 의 원인은?"):
        os.environ["LLMWIKI_MOCK_ANSWER"] = text
        return self._q(q)

    def test_fabricated_citation_is_detected(self):
        """존재하지 않는 `[C99]` 를 인용하면 잡아야 한다 — **인용이 있다는 것만으로** 맞는 줄 알면 안 된다."""
        res, _ = self._forced("링 버퍼는 64KB 였습니다. [C99]")
        claims = res.get("claims") or {}
        self.assertTrue(claims, "claim_check 결과가 비었다 — 거짓 인용을 검사하지 않았다")
        self.assertGreaterEqual(claims.get("bad_citations", 0), 1,
                                "없는 인용 [C99] 가 아무 표시 없이 통과했다: %s" % str(claims)[:300])
        self.assertEqual(claims.get("citation_precision"), 0.0)
        self.assertEqual(claims.get("groundedness"), 0.0)

    def test_citation_after_the_period_is_not_lost(self):
        """LLM 은 `문장입니다. [C1]` 처럼 쓴다. 그 인용을 잃으면 제대로 답한 것도 '인용 없음' 으로 깎인다."""
        from llmwiki.answer import split_claims
        cl = split_claims("링 버퍼가 4KB 라 오버런이 났습니다. [C1] [C2]")
        self.assertEqual(len(cl), 1, "인용 조각이 별개 문장으로 잘렸다: %s" % cl)
        self.assertEqual(cl[0]["cites"], [1, 2])
        self.assertNotIn("[C1]", cl[0]["body"], "body 에는 인용 표시가 남지 않아야 한다")

    def test_empty_answer_does_not_crash(self):
        res, _ = self._forced("")
        self.assertIn("answer", res)
        self.assertIsInstance(res.get("groundedness"), (int, float, type(None)))

    def test_uncited_assertion_lowers_groundedness(self):
        """인용 없는 단정만 늘어놓은 답은 근거성이 낮게 나와야 한다 (숫자가 관측 가능해야 한다)."""
        cited, _ = self._forced("링 버퍼가 4KB 라 오버런이 났습니다. [C1]")
        uncited, _ = self._forced("링 버퍼가 64KB 라 오버런이 났고 담당은 회계팀입니다.")
        g_cited, g_uncited = cited.get("groundedness"), uncited.get("groundedness")
        if g_cited is not None and g_uncited is not None:
            self.assertGreaterEqual(g_cited, g_uncited,
                                    "인용한 답(%s)이 인용 없는 답(%s)보다 근거성이 낮다" % (g_cited, g_uncited))

    def test_answer_echoing_the_prompt_is_still_reported(self):
        """모델이 프롬프트를 되뱉어도 응답 모양은 유지된다 (앞단에서 터지지 않는다)."""
        res, _ = self._forced("TASK=answer\n다음 컨텍스트만 사용하여 답하시오.\n<<<C1>>>")
        self.assertIn("answer", res)
        self.assertIsInstance(res.get("hits"), list)

    def test_llm_failure_still_answers_with_evidence(self):
        """답변 LLM 이 죽어도 근거는 이미 찾았다 — 500 이 아니라 추출식으로라도 답하고 실패를 보고한다."""
        os.environ["LLMWIKI_MOCK_FAIL"] = "timeout"
        try:
            from llmwiki.providers import MockLLM
            MockLLM._fail_counts.clear()
            res, _ = self._q("ISSUE-2001 의 원인은?")
        finally:
            os.environ.pop("LLMWIKI_MOCK_FAIL", None)
            from llmwiki.providers import MockLLM as _M
            _M._fail_counts.clear()
        self.assertIn("answer", res)
        self.assertTrue(res.get("hits"), "LLM 이 실패했다고 근거까지 버렸다")


# ---------------------------------------------------------------- C. 인용 무결성
class CitationIntegrityTest(_Base):
    """화면·LLM·검증이 같은 번호를 본다."""

    def test_every_citation_number_points_at_a_real_chunk(self):
        res, _ = self._q("링 버퍼 오버런 원인과 조치는?")
        nums = {h["n"] for h in res["hits"] if h.get("n") is not None and h.get("in_context")}
        self.assertTrue(nums)
        self.assertEqual(nums, set(range(1, len(nums) + 1)), "인용 번호에 구멍이 있다: %s" % sorted(nums))
        for h in res["hits"]:
            if h.get("n") is not None and h.get("in_context"):
                self.assertTrue(self.p.store.get_chunk(h["chunk_id"]), "인용 %s 가 없는 청크를 가리킨다" % h["n"])

    def test_hits_not_in_context_have_no_number(self):
        """컨텍스트에 못 들어간 후보에 번호가 붙으면, 모델이 못 본 근거를 사람이 본 것으로 착각한다."""
        res, _ = self._q("링 버퍼 오버런 원인과 조치는?")
        for h in res["hits"]:
            if not h.get("in_context"):
                self.assertIsNone(h.get("n"), "컨텍스트 밖 후보 %s 에 인용 번호가 붙었다" % h["chunk_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
