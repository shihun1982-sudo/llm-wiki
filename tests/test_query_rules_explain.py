# -*- coding: utf-8 -*-
"""규칙 방향 (요청 4): explain() 의 유형별 방향 표 · related_symmetric 튜닝 · CLI/MCP 창구."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki import query_rules as qr  # noqa: E402
from llmwiki import tuning as tn  # noqa: E402


class QueryRulesExplainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lwqx_")
        os.environ["LLMWIKI_QUERY_RULES_PATH"] = os.path.join(self.tmp, "query_rules.json")
        self._tuning_path = tn.TUNING_PATH
        tn.TUNING_PATH = os.path.join(self.tmp, "tuning.json")
        tn.load_tuning(tn.TUNING_PATH)
        qr._CACHE.update(mtime=None, rules=None, index=None, sym=None)
        qr.save_rules({
            "acronym": {"AGC": ["Automatic Gain Control"]},
            "synonym": {"재시작": ["리셋", "restart"]},
            "alias": {"모뎀B": "MDM9x-B1"},
            "related": {"DMA underrun": ["FIFO overflow", "버퍼 언더런"]},
            "exclude": {"시뮬레이터": ["simulator"]},
            "compound": {"재전송타이머": ["재전송", "타이머"]},
        })

    def tearDown(self):
        tn.T.values.pop("related_symmetric", None)
        os.environ.pop("LLMWIKI_QUERY_RULES_PATH", None)
        qr._CACHE.update(mtime=None, rules=None, index=None, sym=None)
        tn.load_tuning(os.path.join(self.tmp, "none.json"))
        tn.TUNING_PATH = self._tuning_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_directions_per_type(self):
        # acronym: 키도 값도 양방향
        r = qr.explain("AGC")
        self.assertTrue(r["found"])
        self.assertEqual([(e["type"], e["direction"]) for e in r["entries"]], [("acronym", "양방향")])
        r2 = qr.explain("Automatic Gain Control")
        self.assertEqual(r2["entries"][0]["type"], "acronym")
        self.assertTrue(r2["entries"][0]["reverse"])
        self.assertIn("AGC", r2["entries"][0]["values"])
        # synonym 값 쪽에서도 발화
        r3 = qr.explain("restart")
        self.assertEqual(r3["entries"][0]["direction"], "양방향")
        self.assertIn("재시작", r3["entries"][0]["values"])
        # alias: 키 → canonical 일방. canonical 쪽에서는 entries 가 비고 expanded_from 에만 나온다
        ra = qr.explain("모뎀B")
        self.assertEqual(ra["entries"][0]["type"], "alias")
        self.assertEqual(ra["entries"][0]["direction"], "일방")
        self.assertEqual(ra["expands_to"][0]["as"], "치환")
        rc = qr.explain("MDM9x-B1")
        self.assertEqual(rc["entries"], [])
        self.assertEqual(rc["expanded_from"][0]["type"], "alias")
        self.assertTrue(rc["found"])
        self.assertIn("반대", rc["expanded_from"][0]["note"])
        # related: 기본 일방
        rr = qr.explain("DMA underrun")
        self.assertEqual(rr["entries"][0]["direction"], "일방")
        self.assertEqual(rr["expands_to"][0]["as"], "보조 리스트")
        rv = qr.explain("FIFO overflow")
        self.assertEqual(rv["entries"], [])
        self.assertEqual(rv["expanded_from"][0]["type"], "related")
        self.assertFalse(rv["expanded_from"][0]["reverse_applies"])
        self.assertFalse(rv["related_symmetric"])
        # exclude · compound
        self.assertEqual(qr.explain("시뮬레이터")["expands_to"][0]["as"], "NOT")
        self.assertEqual(qr.explain("재전송타이머")["entries"][0]["type"], "compound")
        # 없는 말
        self.assertFalse(qr.explain("없는말")["found"])
        self.assertIn("note", qr.explain("없는말"))

    def test_related_symmetric_toggle(self):
        # 기본: 값(FIFO overflow)이 질의에 있어도 키(DMA underrun)는 보조 리스트에 안 들어간다
        r = qr.expand("FIFO overflow 원인")
        self.assertEqual(r["related"], [])
        # 켜면 값→키 방향도 색인 (파일은 그대로 — 캐시 키에 플래그가 들어가 재색인)
        tn.T.values["related_symmetric"] = True
        r2 = qr.expand("FIFO overflow 원인")
        self.assertEqual([x[0] for x in r2["related"]], ["DMA underrun"])
        ex = qr.explain("FIFO overflow")
        self.assertTrue(ex["related_symmetric"])
        self.assertEqual(ex["entries"][0]["type"], "related")
        self.assertIn("related_symmetric", ex["entries"][0]["direction"])
        self.assertTrue(ex["expanded_from"][0]["reverse_applies"])
        # 키 방향은 그대로
        r3 = qr.expand("DMA underrun 이 왜")
        self.assertEqual([x[0] for x in r3["related"]][:2], ["FIFO overflow", "버퍼 언더런"])
        # 끄면 되돌아간다
        tn.T.values["related_symmetric"] = False
        self.assertEqual(qr.expand("FIFO overflow 원인")["related"], [])
        self.assertFalse(qr.explain("FIFO overflow")["related_symmetric"])

    def test_cli_and_mcp(self):
        from llmwiki.cli import _rules_explain_text
        from llmwiki import mcp
        txt = _rules_explain_text(qr.explain("AGC"))
        self.assertIn("acronym", txt)
        self.assertIn("양방향", txt)
        r = mcp.call_tool(None, "wiki_rules", {"action": "explain", "term": "모뎀B"})
        self.assertFalse(r.get("isError"), r)
        self.assertEqual(r["structuredContent"]["entries"][0]["type"], "alias")
        r2 = mcp.call_tool(None, "wiki_rules", {"action": "test", "q": "AGC 수렴"})
        self.assertFalse(r2.get("isError"), r2)
        self.assertTrue(any(f["type"] == "acronym" for f in r2["structuredContent"]["fired"]))
        self.assertTrue(mcp.call_tool(None, "wiki_rules", {"action": "explain"}).get("isError"))
        self.assertTrue(mcp.call_tool(None, "wiki_rules", {"action": "nope", "term": "x"}).get("isError"))
        self.assertTrue(mcp.ANNOTATIONS["wiki_rules"]["readOnlyHint"])


if __name__ == "__main__":
    unittest.main()
