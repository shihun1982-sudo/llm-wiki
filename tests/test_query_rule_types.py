# -*- coding: utf-8 -*-
"""규칙 유형 레지스트리 — "유형을 하나 늘리면 전부 따라오는가".

## 이 파일이 있는 이유

2026-09-19 이전에는 규칙 유형 6개가 코드 곳곳에 하드코딩돼 있었다. 유형 하나를 늘리려면
`TYPES`·`DIRECTION`·`HOW`·`_build_index`·`expand`·`lint`·`explain`·`add_rule` 을 고쳐야 했고,
바깥(CLI·Web·MCP)까지 합치면 파일 15개였다. **한 자리만 빠뜨리면 그 유형은 조용히 무시된다** —
설정은 있는데 배선이 없는, 이 저장소가 반복해서 겪은 실패 모양이다.

지금은 유형이 `RULE_TYPES` 레지스트리 항목 하나다. 이 테스트는 그 약속을 고정한다:

  1. **기존 6개의 동작이 바뀌지 않았다** (레지스트리로 옮기면서 깨지지 않았는가)
  2. 새 유형 3개(context·hypernym·unit)가 의도대로 넓힌다
  3. **가짜 유형을 하나 등록하면** 색인·확장·통계·설명·린트가 전부 따라온다 (= 진짜로 확장 가능한가)

3번이 이 파일의 핵심이다. 1·2번만 있으면 "이번에 넣은 세 개가 동작한다" 는 것만 알 수 있고,
다음 사람이 네 번째를 넣을 때 무엇이 필요한지는 여전히 모른다.

참고: `llmwiki/query_rules.py` · `docs/QUERY_RULES.md`
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki import query_rules as qr      # noqa: E402

RULES = {
    "acronym": {"DMA": ["Direct Memory Access"]},
    "synonym": {"재시작": ["리셋", "restart"]},
    "alias": {"모뎀B": "MDM9x-B1"},
    "related": {"AGC": ["RSSI"]},
    "exclude": {"시뮬레이터": ["simulator"]},
    "compound": {"재전송타이머": ["재전송", "타이머"]},
    "context": {
        "PA": [{"when": ["rf", "전력", "송신"], "then": ["Power Amplifier", "전력 증폭기"], "note": "RF"},
               {"when": ["일정", "조직"], "then": ["Product Area"], "note": "조직"}],
        "항상": [{"then": ["언제나"]}],          # when 이 없으면 항상 발화 (lint 가 경고)
    },
    "hypernym": {"메모리 오류": ["DMA 오버런", "FIFO 언더런", "버퍼 오버플로"]},
    "unit": {"KB": {"factor": 1024, "base": "byte", "aliases": ["킬로바이트"]},
             "ms": {"factor": 0.001, "base": "초", "aliases": ["밀리초"]}},
}


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path = os.path.join(cls.tmp, "query_rules.json")
        with open(cls.path, "w", encoding="utf-8") as f:
            json.dump(RULES, f, ensure_ascii=False)
        cls._prev = os.environ.get("LLMWIKI_QUERY_RULES_PATH")
        os.environ["LLMWIKI_QUERY_RULES_PATH"] = cls.path
        qr._CACHE["mtime"] = None

    @classmethod
    def tearDownClass(cls):
        if cls._prev is None:
            os.environ.pop("LLMWIKI_QUERY_RULES_PATH", None)
        else:
            os.environ["LLMWIKI_QUERY_RULES_PATH"] = cls._prev
        qr._CACHE["mtime"] = None
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ex(self, q):
        return qr.expand(q, 0.8, 0.4, True)

    def fired(self, q, typ=None):
        return [f for f in self.ex(q)["fired"] if typ is None or f["type"] == typ]


# ---------------------------------------------------------------- 1. 기존 유형이 그대로인가
class ExistingTypesUnchangedTest(_Base):
    def test_acronym_is_bidirectional_and_seeds_graph(self):
        r = self.ex("DMA 오류")
        self.assertTrue([f for f in r["fired"] if f["type"] == "acronym"])
        self.assertIn("DMA", r["seeds"])
        back = self.ex("Direct Memory Access 오류")          # 값 쪽에서 거꾸로도 걸린다
        self.assertTrue([f for f in back["fired"] if f["type"] == "acronym"])

    def test_alias_replaces_and_does_not_reverse(self):
        r = self.ex("모뎀B 재시작")
        self.assertIn("MDM9x-B1", r["query_alias"])
        self.assertNotIn("모뎀B", self.ex("MDM9x-B1 재시작")["query_alias"].replace("MDM9x-B1", ""))

    def test_related_goes_to_the_side_list_not_the_main_query(self):
        r = self.ex("AGC 수렴")
        self.assertTrue([x for x in r["related"] if x[0] == "RSSI"])
        self.assertNotIn("rssi", r["fts_query"].lower())

    def test_exclude_makes_a_not_clause(self):
        r = self.ex("시뮬레이터 결과")
        self.assertIn("NOT", r["fts_query"])
        self.assertIn("시뮬레이터", r["exclude"])

    def test_compound_is_not_a_query_rule(self):
        """복합어는 토크나이저에 주입될 뿐, 질의 확장에서 발화하지 않는다."""
        self.assertEqual(self.fired("재전송타이머 값", "compound"), [])


# ---------------------------------------------------------------- 2. 새 유형
class ContextTypeTest(_Base):
    """같은 약어가 팀마다 다른 뜻 — 문맥이 맞을 때만 넓힌다."""

    def test_same_term_expands_differently_by_context(self):
        rf = self.ex("PA 전력 증폭 문제")
        org = self.ex("PA 담당자 일정 확인")
        self.assertIn("amplifier", rf["fts_query"].lower())
        self.assertNotIn("product", rf["fts_query"].lower())
        self.assertIn("product", org["fts_query"].lower())
        self.assertNotIn("amplifier", org["fts_query"].lower())

    def test_no_matching_context_does_not_fire_at_all(self):
        """조건이 하나도 안 맞으면 **발화하지 않는다** — 억지로 넓히느니 가만히 있는 편이 낫다."""
        self.assertEqual(self.fired("PA 값", "context"), [])
        self.assertEqual(self.ex("PA 값")["fts_query"], "")

    def test_fired_record_shows_what_actually_fired(self):
        """기록의 values 는 조건 객체가 아니라 **실제로 들여온 말**이어야 한다 (화면·로그가 읽혀야 한다)."""
        f = self.fired("PA 전력 문제", "context")[0]
        self.assertEqual(f["values"], ["Power Amplifier", "전력 증폭기"])

    def test_when_omitted_means_always(self):
        self.assertTrue(self.fired("항상 그렇다", "context"))


class HypernymTypeTest(_Base):
    """분류 체계는 방향에 따라 가중이 다르다 — 같게 두면 곧 잡음이 된다."""

    def test_down_puts_hyponyms_in_the_side_list(self):
        r = self.ex("메모리 오류 원인")
        got = {x[0]: x[1] for x in r["related"]}
        self.assertIn("DMA 오버런", got)
        self.assertAlmostEqual(got["DMA 오버런"], 0.35, places=3)
        self.assertNotIn("오버런", r["fts_query"])      # 주 질의는 오염시키지 않는다

    def test_up_is_weaker_than_down(self):
        down = {x[0]: x[1] for x in self.ex("메모리 오류 원인")["related"]}
        up = {x[0]: x[1] for x in self.ex("FIFO 언더런 원인")["related"]}
        self.assertIn("메모리 오류", up)
        self.assertLess(up["메모리 오류"], down["DMA 오버런"],
                        "상위어로 올라갈 때가 내려갈 때보다 약해야 한다 (상위어 문서는 대개 일반론이다)")

    def test_does_not_chain(self):
        """분류 체계를 연쇄로 펼치면 금방 전 우주가 된다 — 한 단계만."""
        r = self.ex("메모리 오류 원인")
        self.assertTrue(all(f["round"] == 1 for f in r["fired"] if f["type"] == "hypernym"))


class UnitTypeTest(_Base):
    """숫자+단위는 사전 키로 적을 수 없다 — 질의를 직접 훑는 유형(match=scan)."""

    def test_converts_and_ors(self):
        r = self.ex("링 버퍼 4KB 오버런")
        self.assertIn("4096", r["fts_query"])
        self.assertIn("킬로바이트", r["fts_query"])
        f = self.fired("링 버퍼 4KB 오버런", "unit")[0]
        self.assertEqual(f["matched"], "4KB")

    def test_decimal_conversion(self):
        self.assertIn("0.042", self.ex("수렴 42ms")["fts_query"])

    def test_unit_without_a_number_does_not_fire(self):
        self.assertEqual(self.fired("KB 단위로", "unit"), [])

    def test_unknown_unit_is_ignored(self):
        self.assertEqual(self.fired("길이 5m", "unit"), [])


# ---------------------------------------------------------------- 3. 진짜로 확장 가능한가
class RegistryExtensibilityTest(_Base):
    """**가짜 유형을 하나 등록**하고, 아무 데도 손대지 않아도 전부 따라오는지 본다.

    이것이 이 파일의 핵심이다. 새 유형을 넣는 사람이 고쳐야 할 자리가 정말 하나뿐인가?
    """

    def setUp(self):
        self._saved = dict(qr.RULE_TYPES)

        def apply(acc, txt, canon, vals, rnd, in_q):
            acc.or_groups.append([txt] + list(vals))
            return []
        qr.register_type(qr.RuleType("zztest", "시험용", "일방", "시험용 OR 확장",
                                     chains=False, apply=apply, since="test"))
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(dict(RULES, zztest={"가나다": ["라마바"]}), f, ensure_ascii=False)
        qr._CACHE["mtime"] = None

    def tearDown(self):
        qr.RULE_TYPES.clear()
        qr.RULE_TYPES.update(self._saved)
        qr.register_type(qr.RULE_TYPES["acronym"])        # TYPES/DIRECTION/HOW 재계산
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(RULES, f, ensure_ascii=False)
        qr._CACHE["mtime"] = None

    def test_registration_updates_the_public_tables(self):
        self.assertIn("zztest", qr.TYPES)
        self.assertEqual(qr.DIRECTION["zztest"], "일방")
        self.assertIn("시험용", qr.HOW["zztest"])

    def test_it_is_indexed_and_expands(self):
        r = self.ex("가나다 질문")
        self.assertTrue([f for f in r["fired"] if f["type"] == "zztest"])
        self.assertIn("라마바", r["fts_query"])

    def test_stats_lint_explain_and_describe_follow(self):
        self.assertIn("zztest", qr.stats())
        self.assertIn("zztest", qr.lint()["stats"])
        self.assertIn("zztest", [t["name"] for t in qr.describe_types()])
        e = qr.explain("가나다")
        self.assertIn("zztest", [x["type"] for x in e["entries"]])

    def test_fill_defaults_creates_the_missing_section(self):
        p2 = os.path.join(self.tmp, "fresh.json")
        with open(p2, "w", encoding="utf-8") as f:
            json.dump({"acronym": {}}, f)
        rep = qr.fill_defaults(p2)
        self.assertIn("zztest", rep["added"])

    def test_add_rule_accepts_the_new_type(self):
        r = qr.add_rule("zztest", "새말", ["펼친말"])
        self.assertEqual(r["type"], "zztest")
        self.assertIn("펼친말", qr.load_rules()["zztest"]["새말"])


# ---------------------------------------------------------------- 4. 린트
class LintTest(_Base):
    def test_context_without_when_is_warned(self):
        kinds = {i["kind"] for i in qr.lint()["issues"]}
        self.assertIn("always_on", kinds)

    def test_context_shadowed_by_acronym_is_warned(self):
        """같은 말을 acronym 에도 넣으면 문맥 가르기가 무의미해진다 — 알려 줘야 한다."""
        p2 = os.path.join(self.tmp, "shadow.json")
        with open(p2, "w", encoding="utf-8") as f:
            json.dump({"acronym": {"PA": ["Power Amplifier"]},
                       "context": {"PA": [{"when": ["rf"], "then": ["Power Amplifier"]}]}}, f, ensure_ascii=False)
        prev = os.environ["LLMWIKI_QUERY_RULES_PATH"]
        os.environ["LLMWIKI_QUERY_RULES_PATH"] = p2
        qr._CACHE["mtime"] = None
        try:
            kinds = {i["kind"] for i in qr.lint()["issues"]}
        finally:
            os.environ["LLMWIKI_QUERY_RULES_PATH"] = prev
            qr._CACHE["mtime"] = None
        self.assertIn("context_shadowed", kinds)

    def test_bad_shapes_are_errors(self):
        p2 = os.path.join(self.tmp, "bad.json")
        with open(p2, "w", encoding="utf-8") as f:
            json.dump({"context": {"X": [{"when": ["a"]}]}, "unit": {"KB": ["틀린모양"]}}, f, ensure_ascii=False)
        prev = os.environ["LLMWIKI_QUERY_RULES_PATH"]
        os.environ["LLMWIKI_QUERY_RULES_PATH"] = p2
        qr._CACHE["mtime"] = None
        try:
            rep = qr.lint()
        finally:
            os.environ["LLMWIKI_QUERY_RULES_PATH"] = prev
            qr._CACHE["mtime"] = None
        self.assertFalse(rep["ok"])
        self.assertIn("shape", {i["kind"] for i in rep["issues"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
