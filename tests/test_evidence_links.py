# -*- coding: utf-8 -*-
"""질의 결과의 근거를 원본으로 연결 — 이름으로 엔티티 찾기 (서버 쪽 계약).

왜 (2026-09-20 요청):
  "query 결과로 나오는 근거 문단과 그래프에 나온 근거를 누르면 원본 문서로 갈 수 있도록 연결시켜 달라."

서버 쪽에서 새로 필요한 것은 하나다 — **이름으로 엔티티 열기**.
질의 결과의 그래프 관계 표는 `CL-55303` 처럼 **이름**만 들고 있고, `/api/entity` 는 `e:cl-55303` 같은
**id** 만 받았다. 화면이 id 규칙을 스스로 만들어 내게 하면 규칙이 두 곳에 생긴다(그리고 표기가 조금만
달라도 빗나간다). 그래서 서버가 해석한다: `entity_id_for(name)` 로 만들어 보고, 없으면 엔티티 FTS 로 찾는다.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki.graph_rules import entity_id_for      # noqa: E402

DOC = """---
doc_type: cl
ext_id: CL-90003
related:
  issues: [ISSUE-9003]
---

# RX DMA underrun 수정

RX DMA underrun 을 고친 변경이다. 클럭 게이팅 타이밍을 조정했다. PHY 재시작이 정상화됐다.
"""
ISSUE = """---
doc_type: issue
ext_id: ISSUE-9003
---

# RX DMA underrun

RX DMA 에서 underrun 이 발생하면 PHY 재시작이 실패한다. 클럭 게이팅 타이밍이 원인이다.
"""


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        cls.tmp = tempfile.mkdtemp(prefix="llmwiki_ev_")
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        with open(os.path.join(corpus, "cl.md"), "w", encoding="utf-8") as f:
            f.write(DOC * 3)
        with open(os.path.join(corpus, "issue.md"), "w", encoding="utf-8") as f:
            f.write(ISSUE * 3)
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

    def resolve(self, name):
        """서버가 `/api/entity?name=` 에서 하는 것과 같은 해석."""
        eid = entity_id_for(name)
        if not self.p.store.get_entity(eid):
            hits = self.p.store.entity_fts('"%s"' % name.replace('"', ""), 1)
            eid = hits[0][0] if hits else eid
        return self.p.entity_detail(eid)


class EntityByNameTest(_Base):
    def test_exact_name_resolves(self):
        d = self.resolve("CL-90003")
        self.assertTrue(d.get("entity"), "그래프 관계 표의 이름으로 열려야 한다")
        self.assertEqual(d["entity"]["name"], "CL-90003")

    def test_entity_has_the_document_references(self):
        """엔티티 화면이 '원본으로 가는 길' 인 이유 — 여기에 문서 참조가 있다."""
        d = self.resolve("ISSUE-9003")
        refs = d["entity"].get("doc_refs") or []
        self.assertTrue(refs)
        self.assertTrue(any("issue" in str(r.get("doc_id", "")) for r in refs), refs)

    def test_relations_are_returned(self):
        d = self.resolve("CL-90003")
        self.assertTrue(d.get("relations"))

    def test_missing_name_is_empty_not_a_crash(self):
        self.assertFalse((self.resolve("없는엔티티XYZ") or {}).get("entity"))

    def test_id_rule_is_one_place(self):
        """화면이 id 규칙을 스스로 만들지 않게 — `entity_id_for` 한 곳만 쓴다."""
        self.assertEqual(entity_id_for("CL-90003"), "e:cl-90003")
        self.assertEqual(entity_id_for("RX DMA"), "e:rx_dma")


class QueryEvidenceTest(_Base):
    """근거 문단이 **원본으로 갈 수 있는 정보**를 들고 오는가 (doc_id·chunk_id)."""

    def test_hits_carry_doc_and_chunk_ids(self):
        r = self.p.query("RX DMA underrun 원인", log=False)
        res = r[0] if isinstance(r, tuple) else r
        hits = res.get("hits") or []
        self.assertTrue(hits)
        for h in hits:
            self.assertTrue(h.get("chunk_id"), h)
            self.assertTrue(h.get("doc_id"), "문서 id 가 없으면 원본으로 갈 수 없다: %s" % h)

    def test_chunk_id_belongs_to_the_doc(self):
        """화면이 chunk 를 그 문서 화면에서 여는 전제."""
        r = self.p.query("RX DMA underrun 원인", log=False)
        res = r[0] if isinstance(r, tuple) else r
        for h in (res.get("hits") or []):
            self.assertTrue(str(h["chunk_id"]).startswith(str(h["doc_id"])),
                            "%s ⊄ %s" % (h["chunk_id"], h["doc_id"]))

    def test_graph_relations_carry_names(self):
        r = self.p.query("CL-90003 이 고친 이슈", log=False)
        res = r[0] if isinstance(r, tuple) else r
        rels = ((res.get("graph") or {}).get("relations")) or []
        for x in rels:
            self.assertTrue(x.get("src") and x.get("dst"), x)


if __name__ == "__main__":
    unittest.main()
