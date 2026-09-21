# -*- coding: utf-8 -*-
"""문서 단위 접근 제어 — "권한 없는 문서가 근거로 새지 않는가" (Data Access Isolation).

## 위협 모델

사내 위키에는 등급이 다른 문서가 섞인다 (인사·보안 사고·미공개 로드맵·고객사). RAG 는 **검색이 곧 읽기**라서,
색인에 들어간 순간 누구의 질문에도 인용될 수 있다. 막아야 할 길은 하나가 아니다 — 하나만 막으면 나머지로 샌다.

  A. **답변 근거**   질의 → 융합 결과에 비공개 문서의 청크가 들어가고 답변에 인용된다
  B. **채널 검색**   `/api/search`(디버그 화면)·MCP `wiki_search` 의 snippet 으로 본문이 그대로 보인다
  C. **직접 열람**   검색을 막아도 `/api/doc?id=…`·MCP `wiki_doc` 로 doc_id 를 알면 전문이 열린다
  D. **목록 누설**   후보 목록(alternatives)·유사 문서 목록에 제목·ID 가 남는다
  E. **판정 실패**   메타를 못 읽는 등 예외가 나면 열어 줘 버린다 (fail-open)

## 이 테스트가 고정하는 것

  1. 규칙이 **없으면 예전과 똑같이 동작**한다 (켜는 순간 아무도 못 쓰게 되면 안 된다)
  2. 경로 규칙과 문서 front matter 의 `acl:` 중 **더 높은 등급**이 적용된다
  3. admin 은 항상 전부 본다 (운영·감사)
  4. A~D 의 모든 출구가 **같은 판정기**로 막힌다
  5. 막힌 건수가 **관측 가능**하다 (trace·응답 메타) — "왜 근거가 줄었지" 를 설명할 수 있어야 한다

참고: `llmwiki/docacl.py` · `setup/docacl.example.json` · `docs/SECURITY.md`
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import docacl as da                     # noqa: E402
from llmwiki.config import Settings, Toggles          # noqa: E402
from llmwiki.pipeline import Pipeline                 # noqa: E402

PUBLIC = """# HBM4 캐파 확장 공개 브리핑

## 결정사항
1차 투자 1,500억원 승인. 담당은 COO실.
"""

SECRET = """---
title: 2026 상반기 인사 평가 기준
---

# 인사 평가 기준

S등급 비율은 7%, 성과급 지급률은 기본급의 340% 로 한다. 담당은 인사기획팀.
"""

SELF_MARKED = """---
acl: class2
title: 미공개 로드맵
---

# 2027 로드맵

HBM5 양산은 2027년 3분기로 앞당긴다. 캐파 확장 2차 투자 2,800억원.
"""


# ---------------------------------------------------------------- 1. 규칙 해석 (순수 함수)
class RuleResolutionTest(unittest.TestCase):
    """min_role_for / can_see — 두 출처 중 좁은 쪽이 이기는가."""

    ACL = {"enabled": True, "default_min_role": "viewer",
           "rules": [{"prefix": "corpus/hr/", "min_role": "class1"},
                     {"prefix": "corpus/hr/public/", "min_role": "viewer"}]}

    def test_no_rules_blocks_nobody(self):
        acl = {"enabled": True, "default_min_role": "viewer", "rules": []}
        self.assertTrue(da.can_see("viewer", "corpus/hr/pay.md", None, acl))
        self.assertFalse(da.Filter("viewer", {}, acl).enabled)   # 판정기 자체가 꺼진다 = 비용 0

    def test_path_rule(self):
        need, why = da.min_role_for("corpus/hr/pay.md", None, self.ACL)
        self.assertEqual(need, "class1")
        self.assertEqual(why, "rule:corpus/hr/")
        self.assertFalse(da.can_see("viewer", "corpus/hr/pay.md", None, self.ACL))
        self.assertTrue(da.can_see("class1", "corpus/hr/pay.md", None, self.ACL))
        self.assertTrue(da.can_see("builder", "corpus/hr/pay.md", None, self.ACL))

    def test_narrower_wins_not_last_match(self):
        """겹치는 규칙은 '마지막에 적힌 것' 이 아니라 **더 높은 등급**이 이긴다 (안전한 쪽)."""
        need, _ = da.min_role_for("corpus/hr/public/faq.md", None, self.ACL)
        self.assertEqual(need, "class1")     # 뒤에 viewer 규칙이 있어도 낮춰 주지 않는다

    def test_front_matter_raises_but_never_lowers(self):
        need, why = da.min_role_for("corpus/misc/roadmap.md", {"acl": "class2"}, self.ACL)
        self.assertEqual((need, why), ("class2", "doc"))
        # 경로 규칙이 더 높으면 문서가 스스로 낮출 수 없다
        need2, _ = da.min_role_for("corpus/hr/pay.md", {"acl": "viewer"}, self.ACL)
        self.assertEqual(need2, "class1")

    def test_acl_list_takes_lowest(self):
        """`acl: [class1, admin]` 은 'class1 이상' 이라는 뜻 — 목록의 가장 낮은 역할이 하한."""
        need, _ = da.min_role_for("corpus/x.md", {"acl": ["admin", "class1"]}, self.ACL)
        self.assertEqual(need, "class1")

    def test_unknown_role_in_doc_is_ignored(self):
        need, _ = da.min_role_for("corpus/x.md", {"acl": "슈퍼관리자"}, self.ACL)
        self.assertEqual(need, "viewer")     # 오타로 문서가 잠겨 버리지 않는다

    def test_admin_always_sees(self):
        self.assertTrue(da.can_see("admin", "corpus/hr/pay.md", None, self.ACL))
        self.assertFalse(da.Filter("admin", {}, self.ACL).enabled)

    def test_default_min_role_whitelist_mode(self):
        """default_min_role 을 올리면 '규칙에 없는 문서는 전부 비공개' 가 된다."""
        acl = {"enabled": True, "default_min_role": "class2", "rules": []}
        self.assertTrue(da.Filter("viewer", {}, acl).enabled)
        self.assertFalse(da.can_see("viewer", "corpus/anything.md", None, acl))
        self.assertTrue(da.can_see("class2", "corpus/anything.md", None, acl))

    def test_backslash_paths_normalize(self):
        """Windows 에서 색인된 doc_id 가 역슬래시여도 같은 규칙에 걸린다."""
        self.assertFalse(da.can_see("viewer", "corpus\\hr\\pay.md", None, self.ACL))

    def test_disabled_flag(self):
        acl = dict(self.ACL, enabled=False)
        self.assertTrue(da.can_see("viewer", "corpus/hr/pay.md", None, acl))


# ---------------------------------------------------------------- 2. Filter (요청 단위 판정기)
class FilterTest(unittest.TestCase):
    ACL = {"enabled": True, "default_min_role": "viewer",
           "rules": [{"prefix": "corpus/hr/", "min_role": "class1"}]}

    def test_chunk_id_falls_back_to_doc_id(self):
        f = da.Filter("viewer", {}, self.ACL)
        self.assertFalse(f.chunk_ok("corpus/hr/pay.md#3"))
        self.assertTrue(f.chunk_ok("corpus/pub/a.md#1"))

    def test_filter_hits_and_report(self):
        class H:
            def __init__(self, cid):
                self.chunk_id = cid
        hits = [H("corpus/hr/pay.md#1"), H("corpus/pub/a.md#1"), H("corpus/hr/eval.md#2")]
        chunks = {h.chunk_id: {"doc_id": h.chunk_id.rsplit("#", 1)[0]} for h in hits}
        f = da.Filter("viewer", {}, self.ACL)
        kept, removed = f.filter_hits(hits, chunks)
        self.assertEqual([h.chunk_id for h in kept], ["corpus/pub/a.md#1"])
        self.assertEqual(removed, 2)
        # 관측 가능해야 한다 — 몇 건이, 어떤 등급 때문에 빠졌는가
        s = f.summary()
        self.assertEqual(s["blocked_docs"], 2)
        self.assertEqual(s["needs"], ["class1"])
        self.assertEqual(s["role"], "viewer")

    def test_verdict_is_cached(self):
        """청크 수백 개를 훑어도 문서당 한 번만 판정한다."""
        f = da.Filter("viewer", {}, self.ACL)
        for i in range(50):
            f.chunk_ok("corpus/hr/pay.md#%d" % i)
        self.assertEqual(len(f._cache), 1)


# ---------------------------------------------------------------- 3. 모든 출구 (통합)
class ExitsTest(unittest.TestCase):
    """실제로 색인해 놓고, 근거가 새어 나갈 수 있는 길을 하나씩 막혔는지 본다."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        corpus = os.path.join(cls.tmp, "corpus")
        for sub, name, body in (("pub", "capex.md", PUBLIC), ("hr", "eval.md", SECRET), ("misc", "roadmap.md", SELF_MARKED)):
            os.makedirs(os.path.join(corpus, sub), exist_ok=True)
            with open(os.path.join(corpus, sub, name), "w", encoding="utf-8") as f:
                f.write(body)
        cls.acl_file = os.path.join(cls.tmp, "docacl.json")
        with open(cls.acl_file, "w", encoding="utf-8") as f:
            json.dump({"enabled": True, "default_min_role": "viewer",
                       # doc_id 는 corpus 루트 기준 상대 경로다 (corpus/hr/eval.md) — 규칙도 같은 표기로 적는다
                       "rules": [{"prefix": "corpus/hr/", "min_role": "class1", "note": "인사"}],
                       "deny_message": "권한이 없는 문서입니다"}, f, ensure_ascii=False)
        os.environ["LLMWIKI_DOCACL_PATH"] = cls.acl_file
        da.load(force=True)
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"),
                     wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)
        cls.hr_doc = next(d["doc_id"] for d in cls.p.store.list_docs() if "eval" in d["doc_id"])
        cls.pub_doc = next(d["doc_id"] for d in cls.p.store.list_docs() if "capex" in d["doc_id"])

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        os.environ.pop("LLMWIKI_DOCACL_PATH", None)
        da.load(force=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _as(self, role):
        return self.p.request_scope(actor={"user": "t", "role": role, "origin": "test"})

    # --- A. 답변 근거
    def test_query_evidence_excludes_blocked_doc(self):
        with self._as("viewer"):
            res, tr = self.p.query("성과급 지급률과 S등급 비율은?", log=False)
        docs = {h["doc_id"] for h in res["hits"]}
        self.assertNotIn(self.hr_doc, docs, "viewer 의 답변 근거에 인사 문서가 들어갔다")
        self.assertNotIn("340%", json.dumps(res, ensure_ascii=False, default=str))

    def test_same_query_as_admin_does_find_it(self):
        """막힌 것이 '원래 못 찾는 것' 이 아니라 **권한 때문** 임을 대조로 증명한다."""
        with self._as("admin"):
            res, _ = self.p.query("성과급 지급률과 S등급 비율은?", log=False)
        self.assertIn(self.hr_doc, {h["doc_id"] for h in res["hits"]})

    def test_block_is_observable_in_trace(self):
        with self._as("viewer"):
            _res, tr = self.p.query("성과급 지급률은?", log=False)
        names = json.dumps(tr, ensure_ascii=False, default=str)
        self.assertIn("doc_acl", names, "trace 에 doc_acl 단계가 없다 — 왜 근거가 줄었는지 설명할 수 없다")

    def test_self_marked_doc_needs_class2(self):
        # 역할 순서는 viewer < class3 < class2 < class1 < builder < admin (숫자가 작을수록 높은 등급).
        # 따라서 `acl: class2` 는 class3·viewer 를 막고 class2 이상을 통과시킨다.
        with self._as("class3"):
            res, _ = self.p.query("HBM5 양산 시점과 2차 투자 금액은?", log=False)
        self.assertFalse([h for h in res["hits"] if "roadmap" in h["doc_id"]],
                         "front matter 의 acl: class2 가 class3 에게 열렸다")
        with self._as("class2"):
            res2, _ = self.p.query("HBM5 양산 시점과 2차 투자 금액은?", log=False)
        self.assertTrue(any("roadmap" in h["doc_id"] for h in res2["hits"]))

    # --- B. 채널 검색
    def test_channel_search_filters_rows_and_per_channel(self):
        from llmwiki.retrieval import channel_search
        from llmwiki.profiler import Profiler
        with self._as("viewer"):
            out = channel_search(self.p.store, self.p.embedder, self.p.s, "성과급 지급률 S등급",
                                 ["fts", "vector"], mode="or", k=8, prof=Profiler("t", log=False),
                                 acl=self.p.acl_filter())
        blob = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("340%", blob, "per_channel snippet 으로 본문이 샜다")
        self.assertNotIn(self.hr_doc, {r.get("doc_id") for r in out["rows"]})
        self.assertGreater(out["counts"]["acl_blocked"], 0)      # 관측 가능
        self.assertTrue(out["acl"]["enabled"])

    def test_channel_search_as_admin_unfiltered(self):
        from llmwiki.retrieval import channel_search
        from llmwiki.profiler import Profiler
        with self._as("admin"):
            out = channel_search(self.p.store, self.p.embedder, self.p.s, "성과급 지급률 S등급",
                                 ["fts"], mode="or", k=8, prof=Profiler("t", log=False),
                                 acl=self.p.acl_filter())
        self.assertFalse(out["acl"]["enabled"])
        self.assertEqual(out["counts"]["acl_blocked"], 0)

    # --- C. 직접 열람
    def test_doc_detail_denies(self):
        from llmwiki import querydebug as qd
        d = qd.doc_detail(self.p, self.hr_doc, role="viewer")
        self.assertTrue(d.get("denied"), "doc_detail 이 비공개 문서를 열었다")
        self.assertEqual(d.get("min_role"), "class1")
        self.assertNotIn("340%", json.dumps(d, ensure_ascii=False))
        ok = qd.doc_detail(self.p, self.pub_doc, role="viewer")
        self.assertFalse(ok.get("denied"))
        self.assertTrue(qd.doc_detail(self.p, self.hr_doc, role="class1").get("chunks"))

    # --- D. 목록 누설
    def test_alternatives_are_filtered(self):
        """부분 일치로 여러 문서가 잡힐 때, 볼 수 없는 문서는 후보 목록에도 남지 않는다."""
        from llmwiki import querydebug as qd
        d = qd.doc_detail(self.p, ".md", role="viewer")
        names = [d.get("doc_id")] + list(d.get("alternatives") or [])
        self.assertNotIn(self.hr_doc, names)

    # --- MCP: 세 창구가 같은 판정을 받는가
    def test_mcp_tools_honour_actor(self):
        """API 키 하나로 전 문서가 열리면 안 된다 — wiki_search·wiki_doc·wiki_related 모두 역할을 받는다."""
        from llmwiki import mcp as _mcp

        def call(name, args):
            with self._as("viewer"):
                return json.dumps(_mcp.call_tool(self.p, name, args, federate=False), ensure_ascii=False, default=str)
        self.assertNotIn("340%", call("wiki_search", {"query": "성과급 지급률 S등급", "channels": ["fts", "vector"], "k": 8}))
        doc = call("wiki_doc", {"id": self.hr_doc})
        self.assertIn("권한이 없는 문서입니다", doc)
        self.assertNotIn("340%", doc)
        self.assertNotIn(self.hr_doc, call("wiki_related", {"text": "성과급 지급률 S등급 평가", "doc_types": []}))
        # 대조: admin 으로는 같은 도구가 찾는다
        with self.p.request_scope(actor={"user": "a", "role": "admin", "origin": "test"}):
            self.assertIn("340%", json.dumps(_mcp.call_tool(self.p, "wiki_doc", {"id": self.hr_doc}, federate=False), ensure_ascii=False))

    # --- E. 영향 미리보기 (운영자가 규칙을 넣기 전에 본다)
    def test_check_reports_impact(self):
        r = da.check(self.p.store, "viewer")
        self.assertTrue(r["enabled"])
        self.assertEqual(r["blocked"], 2)        # 경로 규칙(hr) 1건 + front matter(roadmap) 1건
        self.assertEqual(r["visible"], r["docs"] - 2)
        self.assertEqual({x["why"] for x in r["examples"]}, {"rule:corpus/hr/", "doc"})
        self.assertEqual(da.check(self.p.store, "admin")["blocked"], 0)
        self.assertEqual(da.check(self.p.store, "class1")["blocked"], 0)   # 가장 높은 등급 중 하나

    def test_toggle_off_restores_old_behaviour(self):
        """긴급 해제: config 토글 하나로 전부 되돌아온다."""
        self.p.s.toggles.doc_acl = False
        try:
            with self._as("viewer"):
                self.assertFalse(self.p.acl_filter().enabled)
                res, _ = self.p.query("성과급 지급률은?", log=False)
            self.assertIn(self.hr_doc, {h["doc_id"] for h in res["hits"]})
        finally:
            self.p.s.toggles.doc_acl = True


if __name__ == "__main__":
    unittest.main(verbosity=2)
