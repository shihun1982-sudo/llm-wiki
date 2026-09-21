# -*- coding: utf-8 -*-
"""채널별 top-k 구간 가중 · 리랭크 창 보장 주입 (docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md §2.2).

지키려는 것:
  1) topk 맵이 없으면 fuse() 는 예전과 완전히 같다 (boosts 비어 있음, 점수 동일).
  2) 순위 r ≤ n 은 topk_w, 밖은 tail_w 가 채널 가중치에 곱해지고 Hit.boosts["topk_<채널>"] 에 남는다.
     보조 리스트(fts_alt1, ext_<src>)는 기본 채널 값을 물려받는다. tail_w=0 이면 그 리스트에서 빠진다.
  3) apply_boosts 는 boosts 를 덮지 않고 합친다.
  4) channel_inject 는 external_rag_inject 와 같은 방식으로 창 밖 후보를 창 안으로 올리고 why 에 inject:<채널> 을 남긴다.
  5) 파이프라인에서 tuning 값으로 켜면 trace(rrf_fuse.topk, channel_inject) 와 hits 의 boosts/why 에 보인다.
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from llmwiki import fusion as F                      # noqa: E402
from llmwiki import tuning as tn                     # noqa: E402
from llmwiki.retrieval import Hit                    # noqa: E402

LISTS = {
    "fts": [("a", 10.0), ("b", 8.0), ("c", 6.0), ("d", 4.0)],
    "fts_alt1": [("c", 5.0), ("e", 4.0)],
    "vector": [("b", 0.9), ("e", 0.8), ("f", 0.7)],
    "ext_other": [("x", 0.5), ("y", 0.4)],
}
W = {"fts": 1.0, "fts_alt1": 0.6, "vector": 1.0, "ext_other": 0.8}


def rrf(w, rank, k=60):
    return w / (k + rank)


class BaseChannelTest(unittest.TestCase):
    def test_base_channel(self):
        self.assertEqual(F.base_channel("fts"), "fts")
        self.assertEqual(F.base_channel("fts_alt1"), "fts")
        self.assertEqual(F.base_channel("fts_rule"), "fts")
        self.assertEqual(F.base_channel("fts_rel1"), "fts")
        self.assertEqual(F.base_channel("vector_alt2"), "vector")
        self.assertEqual(F.base_channel("graph"), "graph")
        self.assertEqual(F.base_channel("doc_vector"), "doc_vector")
        self.assertEqual(F.base_channel("ext_jira"), "external")

    def test_topk_map_from_tuning(self):
        t = tn.Tuning({})
        self.assertEqual(F.topk_map(t), {})
        t = tn.Tuning({"fts_topk_n": 2, "fts_topk_w": 1.5, "fts_tail_w": 0.5, "vector_topk_w": 2.0})   # vector 는 n=0 → 빠진다
        self.assertEqual(F.topk_map(t), {"fts": (2, 1.5, 0.5)})

    def test_parse_inject_map(self):
        self.assertEqual(F.parse_inject_map("fts:2,vector:2,graph:1"), {"fts": 2, "vector": 2, "graph": 1})
        self.assertEqual(F.parse_inject_map("fts:0,bogus:3,external:1"), {"external": 1})
        self.assertEqual(F.parse_inject_map(""), {})


class FuseTopkTest(unittest.TestCase):
    def test_no_topk_is_unchanged(self):
        hits, meta = F.fuse(LISTS, W, 60, "rrf", topk=None)
        by = {h.chunk_id: h for h in hits}
        self.assertAlmostEqual(by["a"].fused, rrf(1.0, 1))
        self.assertAlmostEqual(by["c"].fused, rrf(1.0, 3) + rrf(0.6, 1))
        self.assertTrue(all(not h.boosts for h in hits))
        self.assertNotIn("topk", meta)

    def test_topk_weights_and_boosts(self):
        hits, meta = F.fuse(LISTS, W, 60, "rrf", topk={"fts": (2, 1.5, 0.5)})
        by = {h.chunk_id: h for h in hits}
        # 순위 1·2 는 ×1.5, 3·4 는 ×0.5. fts_alt1(보조 리스트)도 fts 의 값을 물려받는다 (c 는 alt1 에서 1위 → ×1.5)
        self.assertAlmostEqual(by["a"].fused, rrf(1.5, 1))
        self.assertAlmostEqual(by["b"].fused, rrf(1.5, 2) + rrf(1.0, 1))     # vector 는 topk 없음 → ×1
        self.assertAlmostEqual(by["c"].fused, rrf(0.5, 3) + rrf(0.6 * 1.5, 1))
        self.assertAlmostEqual(by["d"].fused, rrf(0.5, 4))
        self.assertEqual(by["a"].boosts, {"topk_fts": 1.5})
        self.assertEqual(by["d"].boosts, {"topk_fts": 0.5})
        self.assertEqual(by["c"].boosts, {"topk_fts": 0.5})          # 채널당 한 번(먼저 적용된 값)만 남는다
        self.assertEqual(by["f"].boosts, {})
        self.assertEqual(by["e"].boosts, {"topk_fts": 1.5})          # alt1 2위 → n=2 안
        self.assertAlmostEqual(by["e"].fused, rrf(0.6 * 1.5, 2) + rrf(1.0, 2))
        self.assertEqual(meta["topk"], {"fts": {"n": 2, "topk_w": 1.5, "tail_w": 0.5}})
        self.assertEqual(meta["topk_in"], 4)         # fts a,b + alt1 c,e
        self.assertEqual(meta["topk_out"], 2)        # fts c,d
        self.assertEqual(meta["topk_dropped"], 0)

    def test_tail_zero_drops_entries(self):
        hits, meta = F.fuse(LISTS, W, 60, "rrf", topk={"fts": (1, 1.0, 0.0), "external": (1, 2.0, 0.0)})
        by = {h.chunk_id: h for h in hits}
        self.assertNotIn("d", by)                    # fts 4위 → 버림 (다른 채널에 없음)
        self.assertNotIn("y", by)                    # ext_other 2위 → 버림
        self.assertIn("c", by)                       # fts 3위는 버려졌지만 alt1 1위로 살아남는다
        self.assertEqual(by["c"].ranks, {"fts_alt1": 1})
        self.assertAlmostEqual(by["x"].fused, rrf(0.8 * 2.0, 1))
        self.assertEqual(by["x"].boosts, {"topk_external": 2.0})
        self.assertEqual(meta["topk_dropped"], 5)    # fts b,c,d + alt1 e + ext y
        self.assertEqual(by["b"].ranks, {"vector": 1})
        self.assertEqual(by["e"].ranks, {"vector": 2})

    def test_other_methods_apply_factor(self):
        for m in ("weighted", "zscore", "dbsf", "rrf_boost"):
            plain, _ = F.fuse(LISTS, W, 60, m)
            boosted, _ = F.fuse(LISTS, W, 60, m, topk={"vector": (1, 3.0, 1.0)})
            p = {h.chunk_id: h.fused for h in plain}
            b = {h.chunk_id: h.fused for h in boosted}
            self.assertGreater(b["b"], p["b"], m)      # vector 1위만 커진다
            self.assertAlmostEqual(b["f"], p["f"], msg=m)

    def test_apply_boosts_merges(self):
        hits, _ = F.fuse(LISTS, W, 60, "rrf", topk={"fts": (1, 2.0, 1.0)})
        chunks = {h.chunk_id: {"chunk_id": h.chunk_id, "doc_id": "doc/" + h.chunk_id, "heading": "", "text": ""} for h in hits}
        F.apply_boosts(hits, chunks, {}, pinned={"a": 1.0}, pin_w=10.0)
        by = {h.chunk_id: h for h in hits}
        self.assertEqual(by["a"].boosts, {"topk_fts": 2.0, "pin": 10.0})   # 덮지 않고 합친다
        self.assertEqual(hits[0].chunk_id, "a")


class InjectTest(unittest.TestCase):
    def _hits(self, n=10):
        out = []
        for i in range(n):
            h = Hit("h%d" % i)
            h.fused = 1.0 - i * 0.05
            out.append(h)
        return out

    def test_inject_lifts_into_window(self):
        hits = self._hits(10)
        lists = {"vector": [("h9", 0.9), ("h1", 0.8)], "fts": [("h8", 5.0)], "ext_a": [("h7", 0.5)], "ext_b": [("h6", 0.4)]}
        moved = F.inject_channels(hits, lists, {"vector": 2, "external": 1}, win=4)
        self.assertEqual(moved, {"vector": ["h9"], "external": ["h7", "h6"]})   # h1 은 이미 창 안, fts 는 주입 대상 아님
        order = [h.chunk_id for h in hits]
        self.assertTrue(all(order.index(x) < 4 + 3 for x in ("h9", "h7", "h6")))
        by = {h.chunk_id: h for h in hits}
        self.assertIn("inject:vector", by["h9"].why)
        self.assertIn("inject:external", by["h7"].why)
        self.assertNotIn("inject:vector", by["h1"].why)
        self.assertGreater(by["h9"].fused, by["h4"].fused)       # 창 끝 요소(h3) 바로 위 → h4 보다 위
        self.assertLess(by["h9"].fused, by["h2"].fused)

    def test_inject_noop(self):
        hits = self._hits(3)
        self.assertEqual(F.inject_channels(hits, {"fts": [("h2", 1.0)]}, {}, 2), {})
        self.assertEqual(F.inject_channels(hits, {"fts": [("h2", 1.0)]}, {"fts": 1}, 5), {})    # 창이 후보보다 크면 할 일 없음
        self.assertEqual(F.inject_channels(hits, {"fts": [("zz", 1.0)]}, {"fts": 1}, 2), {})   # 후보에 없는 id


DOC_A = "# ISSUE-3001 AGC 이득 오류\n\n수신 AGC 이득 계산이 틀려 SNR 이 떨어진다. 담당 박민수, 수정 CL-70001.\n\n## 조치\n이득 테이블을 다시 만든다.\n"
DOC_B = "# ISSUE-3002 RACH 충돌\n\n프리앰블 충돌로 접속 지연. 담당 이수현, 수정 CL-70002.\n"
DOC_C = "# 주간 보고 9월 2주\n\nAGC 이득 이슈(ISSUE-3001) 진행 중. RACH 는 완료.\n"


class PipelineTopkTest(unittest.TestCase):
    """실제 파이프라인(mock LLM, hash 임베딩)에서 tuning 값이 trace 와 hits 에 보이는지."""

    @classmethod
    def setUpClass(cls):
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        cls.tmp = tempfile.mkdtemp(prefix="lwtopk_")
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        for name, text in (("a.md", DOC_A), ("b.md", DOC_B), ("c.md", DOC_C)):
            open(os.path.join(corpus, name), "w", encoding="utf-8").write(text)
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"), wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=512, debug_level=1, rerank_candidates=2, top_k_final=2)
        s.toggles = Toggles(query_cache=False, precompute=False, evolve_capture=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        tn.load_tuning(os.path.join(cls.tmp, "none.json"))
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _stages(self, tr):
        return {c["name"]: c for c in tr["children"]}

    def test_topk_via_request_overrides(self):
        ov = {"tuning": {"fts_topk_n": 1, "fts_topk_w": 2.0, "fts_tail_w": 0.5, "channel_inject": "vector:2,fts:1"}}
        with self.p.request_scope(overrides=ov):
            self.assertEqual(tn.T.get("fts_topk_n"), 1)
            res, tr = self.p.query("AGC 이득 오류 담당", log=False)
        self.assertEqual(tn.T.get("fts_topk_n"), 0)          # 요청이 끝나면 오버레이가 사라진다
        st = self._stages(tr)
        self.assertEqual(st["rrf_fuse"]["meta"]["topk"], {"fts": {"n": 1, "topk_w": 2.0, "tail_w": 0.5}})
        self.assertGreaterEqual(st["rrf_fuse"]["meta"]["topk_in"], 1)
        self.assertIn("channel_inject", st)
        self.assertEqual(st["channel_inject"]["meta"]["inject"], {"vector": 2, "fts": 1})
        # 부스트 뒤에도 topk 배율이 남아 있다 (apply_boosts 가 합친다)
        top = res["hits"][0]
        self.assertIn("topk_fts", top["boosts"])
        self.assertIn(top["boosts"]["topk_fts"], (2.0, 0.5))
        self.assertEqual(res["output_mode"], "answer")

    def test_bad_tuning_override_is_rejected(self):
        with self.assertRaises(ValueError):
            with self.p.request_scope(overrides={"tuning": {"fts_topk_n": 999}}):
                pass
        with self.assertRaises(ValueError):
            with self.p.request_scope(overrides={"tuning": {"no_such_key": 1}}):
                pass
        self.assertFalse(tn.T.has_overlay())


if __name__ == "__main__":
    unittest.main()
