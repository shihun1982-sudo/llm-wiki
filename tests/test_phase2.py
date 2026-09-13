# -*- coding: utf-8 -*-
"""Phase 2: 문서 계약(front matter·스키마·lint·메타 토큰) · 결정적 관계/provenance · 임베딩 캐시/재개/적응형 배치 ·
정합성 검증(verify/fix) · rename · 위키 stale 정리 · trigram 폴백 · 복합어 · MCP 소스(mock) · CLI."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402
from llmwiki import schema as sc  # noqa: E402
from llmwiki import textutil as tu  # noqa: E402
from llmwiki import query_rules as qr  # noqa: E402
from llmwiki import tuning as tn  # noqa: E402
from llmwiki.providers import BaseEmbedder  # noqa: E402
from llmwiki.cli import run_captured  # noqa: E402
from llmwiki.graph_rules import entity_id_for  # noqa: E402


def _gen_corpus(out: str) -> None:
    spec = importlib.util.spec_from_file_location("mk", os.path.join(ROOT, "setup", "make_sample_corpus_modem.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    mod.gen(out, 1)


class FakeEmbedder(BaseEmbedder):
    """결정적 벡터 + 실패 주입 (텍스트에 fail_marker 가 있으면 예외)."""
    name = "fake"

    def __init__(self, dim: int = 16):
        self.dim = dim
        self.model = "fake-v1"
        self.available = True
        self.calls = 0
        self.texts = 0
        self.fail_marker = None
        self.fail_batches_over = 0   # 배치 크기가 이보다 크면 실패 (적응형 축소 테스트)

    def _embed(self, texts):
        self.calls += 1
        self.texts += len(texts)
        if self.fail_batches_over and len(texts) > self.fail_batches_over:
            raise RuntimeError("HTTP 413 batch too large")
        if self.fail_marker and any(self.fail_marker in t for t in texts):
            raise RuntimeError("HTTP 500 embed failed")
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for j, ch in enumerate(t[:200]):
                out[i, (ord(ch) + j) % self.dim] += 1.0
            out[i] /= max(1e-9, np.linalg.norm(out[i]))
        return out


class Phase2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus = os.path.join(self.tmp, "corpus")
        _gen_corpus(self.corpus)
        for k in ("LOGS_DIR", "SCHEMAS_DIR", "QUERY_RULES", "MCP_SOURCES", "PROMPTS_DIR"):
            os.environ["LLMWIKI_%s_PATH" % k] = os.path.join(self.tmp, k.lower())
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        s = Settings(corpus_dirs=[self.corpus], data_dir=os.path.join(self.tmp, "data"), wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=256, embed_batch=8, embed_batch_max=32, embed_commit_every=2)
        s.toggles = Toggles(query_cache=False, health_check=False)
        self.s = s
        self.p = Pipeline(s)

    def tearDown(self):
        self.p.store.close()
        for k in ("LOGS_DIR", "SCHEMAS_DIR", "QUERY_RULES", "MCP_SOURCES", "PROMPTS_DIR"):
            os.environ.pop("LLMWIKI_%s_PATH" % k, None)
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        tu.set_compounds({})
        tn.load_tuning(os.path.join(self.tmp, "none.json"))
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 스키마 / front matter ----
    def test_front_matter_and_lint(self):
        text = "---\nschema_version: 1\ndoc_type: issue\nid: ISSUE-2041\ntitle: \"RX: DMA\"\ndate: 2026-08-21\nstatus: fixed\ntags: [rx, dma]\nhw:\n  chip: MDM9x\n  rev: B1\nrelated:\n  cls:\n    - CL-55321\n    - CL-55402\n  issues: []\n---\n# 본문\n\n## 현상\nx\n"
        meta, body, had = sc.parse_front_matter(text)
        self.assertTrue(had)
        self.assertEqual(meta["id"], "ISSUE-2041")
        self.assertEqual(meta["title"], "RX: DMA")
        self.assertEqual(meta["tags"], ["rx", "dma"])
        self.assertEqual(meta["hw"], {"chip": "MDM9x", "rev": "B1"})
        self.assertEqual(meta["related"]["cls"], ["CL-55321", "CL-55402"])
        self.assertEqual(meta["related"]["issues"], [])
        self.assertTrue(body.startswith("# 본문"))
        nm = sc.normalize_meta(meta, "issues/ISSUE-2041.md", "t", body, 0.0)
        self.assertEqual(nm["doc_type"], "issue")
        self.assertEqual(nm["date"], "2026-08-21")
        self.assertEqual(nm["related"]["cls"], ["CL-55321", "CL-55402"])
        self.assertFalse(nm["inferred"])
        lint = sc.lint_document(meta, nm, body, True)
        self.assertFalse(any(x["level"] == "error" for x in lint))
        self.assertIn("ISSUE-2041".lower(), sc.meta_tokens(nm))
        # enum 오류 + 필수 누락 + ID 형식
        bad = {"schema_version": 1, "doc_type": "cl", "id": "CHANGE-1", "title": "x", "date": "2026-01-01", "status": "bogus"}
        nm2 = sc.normalize_meta(bad, "cls/x.md", "x", "", 0.0)
        lint2 = sc.lint_document(bad, nm2, "", True)
        fields = {(x["field"], x["level"]) for x in lint2}
        self.assertIn(("status", "error"), fields)
        self.assertIn(("related.issues", "error"), fields)
        self.assertIn(("id", "error"), fields)
        # front matter 없음 → 추론 + warn
        nm3 = sc.normalize_meta({}, "issues/ISSUE-0001_x.md", "ISSUE-0001", "# ISSUE-0001\n본문", 1700000000.0)
        self.assertEqual(nm3["doc_type"], "issue")
        self.assertEqual(nm3["ext_id"], "ISSUE-0001")
        self.assertTrue(nm3["inferred"])
        self.assertEqual(nm3["date_source"], "mtime")
        lint3 = sc.lint_document({}, nm3, "본문", False)
        self.assertTrue(all(x["level"] != "error" for x in lint3))
        # 마이그레이션 0→1
        mig = sc.migrate_meta({"type": "issue", "created": "2026-01-02"})
        self.assertEqual(mig["doc_type"], "issue")
        self.assertEqual(mig["schema_version"], 1)
        self.assertIn("issue", sc.doc_types())
        self.assertIn("ISSUE-", sc.example_document("issue"))

    # ---- 빌드: doc_meta · 메타 토큰 · 결정적 관계 · provenance ----
    def test_build_contract_and_provenance(self):
        res, tr = self.p.build(full=True)
        st = {c["name"]: c for c in tr["children"]}
        self.assertEqual(res["docs"], 37)                          # 36 계약 문서 + README
        self.assertGreaterEqual(st["chunk_index"]["meta"]["doc_types"].get("issue", 0), 10)
        self.assertEqual(res["lint"]["errors"], 0)
        self.assertEqual(res["lint"]["inferred"], 2)             # misc/meeting + README 만 추론
        counts = self.p.store.doc_type_counts()
        self.assertEqual(counts["cl"], 10)
        m = self.p.store.get_doc_meta("corpus/cls/CL-55301.md")
        self.assertEqual(m["ext_id"], "CL-55301")
        self.assertEqual(m["related"]["issues"], ["ISSUE-2001"])
        self.assertEqual(m["date"], "2026-06-06")
        # 메타 토큰: ID 질의가 문서를 찾음 (FTS)
        r, t = self.p.query("ISSUE-2001 원인", log=False)
        self.assertTrue(any("ISSUE-2001" in h["doc_id"] for h in r["hits"][:3]))
        # explicit 관계: CL-55301 -fixes-> ISSUE-2001 · rule 관계: 본문 ID 언급
        rels = self.p.store.relations_of(entity_id_for("CL-55301"))
        kinds = {(x["rel"], x["provenance"]) for x in rels}
        self.assertIn(("fixes", "explicit"), kinds)
        pc = self.p.store.provenance_counts()
        self.assertGreater(pc.get("explicit", 0), 10)
        self.assertGreater(pc.get("rule", 0), 10)
        self.assertGreater(pc.get("cooccur", 0), 10)
        e = self.p.store.get_entity(entity_id_for("ISSUE-2001"))
        self.assertEqual(e["type"], "issue")
        refs = json.loads(e["doc_refs"])
        self.assertTrue(any("ISSUE-2001" in x["doc_id"] for x in refs))
        self.assertTrue(any("CL-55301" in x["doc_id"] for x in refs))      # CL 문서에도 언급 → 노드 ↔ 원본 문서 연결
        g = self.p.graph_export(limit=100, provenance="explicit")
        self.assertTrue(g["edges"] and all(ed["provenance"] == "explicit" for ed in g["edges"]))
        # 그래프 검색: CL 질의 → ID 시드 → doc_refs 문서 후보
        r2, t2 = self.p.query("CL-55301 은 어떤 이슈를 수정했나", log=False)
        gs = {c["name"]: c for c in t2["children"]}["graph_search"]["meta"]
        self.assertTrue(any("CL-55301" in s[0] for s in gs["seeds"]))
        self.assertIn("provenance", gs)
        out = run_captured(["graph", "--provenance", "explicit", "--limit", "20"], self.s, self.p)
        self.assertIn("fixes", out["output"])
        out2 = run_captured(["corpus", "lint"], self.s, self.p)
        self.assertIn("meeting", out2["output"])
        out3 = run_captured(["corpus", "types"], self.s, self.p)
        self.assertIn("weekly_report", out3["output"])
        out4 = run_captured(["corpus", "lint-file", os.path.join(self.corpus, "cls", "CL-55301.md")], self.s, self.p)
        self.assertEqual(out4["code"], 0)
        self.assertIn("doc_type=cl", out4["output"])
        # 평가셋 기준선 (규칙 채널만으로도 hit@5 가 높아야 함)
        ev, _ = self.p.evaluate(k=5, questions=json.load(open(os.path.join(self.corpus, "questions.json"), encoding="utf-8")))
        self.assertGreaterEqual(ev["summary"]["hit@k"], 0.8, ev["summary"])

    # ---- 임베딩 러너: 캐시 · 재개 · 적응형 배치 · 알림 · 리포트 ----
    def test_embed_runner_cache_resume_adaptive(self):
        fake = FakeEmbedder()
        self.p._embedder = fake
        self.p.s.embed_provider = "fake"
        fake.fail_batches_over = 4                      # 배치 8 → 실패 → 4 로 축소
        res, tr = self.p.build(full=True)
        st = {c["name"]: c for c in tr["children"]}
        em = st["embed"]["meta"]
        self.assertEqual(em["failed"], 0)
        self.assertGreater(em["resized"], 0)                    # 8 → 실패 → 4 → 성공 시 재확대 → 다시 축소 (적응)
        self.assertLessEqual(em["final_batch"], 8)
        self.assertEqual(em["cache_hits"], 0)
        n_chunks = self.p.store.stats()["chunks"]
        self.assertEqual(self.p.store.embed_coverage("fake")["coverage"], 1.0)
        self.assertGreater(self.p.store.cache_stats()["entries"], 0)
        # 전체 리빌드 → 캐시 적중, 임베더 호출 0
        fake.calls = 0
        fake.fail_batches_over = 0
        res2, tr2 = self.p.build(full=True)
        em2 = {c["name"]: c for c in tr2["children"]}["embed"]["meta"]
        self.assertEqual(em2["cache_hits"], n_chunks)
        self.assertEqual(fake.calls, 0)
        # 실패 주입: 특정 청크 실패 → coverage < 1, alerts, 다음 빌드(변경 없음)에서 재개
        self.p.store.cache_clear()
        fake.fail_marker = "PDCCH"
        res3, tr3 = self.p.build(full=True)
        em3 = {c["name"]: c for c in tr3["children"]}["embed"]["meta"]
        self.assertGreater(em3["failed"], 0)
        self.assertTrue(any(a["check"] == "embed" for a in res3["alerts"]))
        cov = self.p.store.embed_coverage("fake")
        self.assertLess(cov["coverage"], 1.0)
        vr = self.p.store.verify("fake")
        self.assertTrue(any(c["name"] == "embedding_coverage" and not c["ok"] for c in vr["checks"]))
        runs = self.p.store.embed_runs(1)
        self.assertEqual(runs[0]["status"], "done_with_failures")
        fake.fail_marker = None
        res4, tr4 = self.p.build(full=False)                # 파일 변경 없음이지만 missing 이 있어 embed 실행
        self.assertEqual(res4["changed"], 0)
        st4 = {c["name"]: c for c in tr4["children"]}
        self.assertTrue(st4["embed"]["enabled"])
        self.assertGreater(st4["embed"]["meta"]["resume_missing"], 0)
        self.assertEqual(self.p.store.embed_coverage("fake")["coverage"], 1.0)
        prog = self.p.store.kv_get("embed_progress")
        self.assertEqual(prog["status"], "done")
        out = run_captured(["embed", "report"], self.s, self.p)
        self.assertIn("coverage: ", out["output"])
        out2 = run_captured(["build", "status"], self.s, self.p)
        self.assertIn("running=False", out2["output"])

    # ---- 정합성 검증 · rename · 위키 stale ----
    def test_verify_fix_rename_wiki_stale(self):
        self.p.build(full=True)
        vr = self.p.store.verify("hash", wiki_dir=self.s.wiki_dir)
        self.assertTrue(vr["ok"], [c for c in vr["checks"] if not c["ok"]])
        # 손상 주입: 댕글링 멘션/FTS 고아/위키 stale 페이지
        c = self.p.store.conn
        c.execute("INSERT INTO mentions(entity_id,chunk_id,doc_id,count,source) VALUES('e:x','nope#0','nope',1,'t')")
        c.execute("INSERT INTO chunks_fts(chunk_id,doc_id,heading,body,tokens) VALUES('ghost#0','ghost','h','b','t')")
        with open(os.path.join(self.s.wiki_dir, "Ghost_Entity.md"), "w", encoding="utf-8") as f:
            f.write("# Ghost\n")
        c.commit()
        vr2 = self.p.store.verify("hash", wiki_dir=self.s.wiki_dir)
        bad = {x["name"] for x in vr2["checks"] if not x["ok"]}
        self.assertIn("mention_dangling", bad)
        self.assertIn("fts_orphans", bad)
        self.assertIn("wiki_stale_pages", bad)
        vr3 = self.p.store.verify("hash", fix=True, wiki_dir=self.s.wiki_dir)
        self.assertTrue(vr3["ok"], [x for x in vr3["checks"] if not x["ok"]])
        self.assertFalse(os.path.exists(os.path.join(self.s.wiki_dir, "Ghost_Entity.md")))
        out = run_captured(["build", "verify"], self.s, self.p)
        self.assertIn("verify: OK", out["output"])
        # rename: 파일 이름 변경 → renamed=1, 문서 수 동일, 임베딩 coverage 유지
        old = os.path.join(self.corpus, "issues", "ISSUE-2010.md")
        new = os.path.join(self.corpus, "issues", "ISSUE-2010-renamed.md")
        os.rename(old, new)
        res, tr = self.p.build(full=False)
        self.assertEqual(res["renamed"], 1)
        self.assertEqual(res["removed"], 1)
        self.assertEqual(self.p.store.stats()["docs"], 37)
        self.assertEqual(self.p.store.embed_coverage("hash")["coverage"], 1.0)
        self.assertTrue(self.p.build(full=False)[1]["children"])   # no-op
        # 문서 삭제 → 그 문서만의 엔티티가 고아가 되면 위키 페이지 정리 (증분)
        wiki_before = set(os.listdir(self.s.wiki_dir))
        os.remove(os.path.join(self.corpus, "misc", "meeting_2026-09-02.md"))
        res2, tr2 = self.p.build(full=False)
        self.assertEqual(res2["removed"], 1)
        pr = {c["name"]: c for c in tr2["children"]}["prune"]["meta"]
        self.assertGreaterEqual(pr.get("stale_wiki_pages", 0), 1)
        gone = wiki_before - set(os.listdir(self.s.wiki_dir))
        self.assertTrue(any("회의" in g or "2026" in g for g in gone), gone)
        vr4 = self.p.store.verify("hash", wiki_dir=self.s.wiki_dir)
        self.assertTrue(all(c["ok"] for c in vr4["checks"] if c["name"] not in ("community_unassigned",)), [c for c in vr4["checks"] if not c["ok"]])

    # ---- 한글: 복합어 · trigram 폴백 · 토크나이저 폴백 ----
    def test_korean_compound_trigram(self):
        qr.load_rules()
        self.assertIn("재전송", tu.keywords("재전송타이머 초기화 누락"))
        self.assertIn("타이머", tu.tokenize_for_fts("재전송타이머").split())
        self.assertEqual(tu.set_tokenizer("kiwi"), "heuristic" if tu._TOKENIZER["kiwi"] is None else "kiwi")
        tu.set_tokenizer("heuristic")
        self.p.s.toggles.fts_trigram = True
        self.p.build(full=True)
        self.assertTrue(self.p.store._has_trigram())
        rows = self.p.store.trigram_search("descripto", 5)
        self.assertTrue(rows)
        r, t = self.p.query("descripto 정렬", log=False)
        fm = {c["name"]: c for c in t["children"]}["fts_search"]["meta"]
        self.assertTrue(any("trigram" in x for x in fm["tiers"]) or fm["hits"] > 0)
        vr = self.p.store.verify("hash")
        self.assertTrue(any(c["name"] == "trigram_rows" and c["ok"] for c in vr["checks"]))
        self.p.s.toggles.fts_trigram = False
        self.p.build(full=True)
        self.assertFalse(self.p.store._has_trigram())
        r2 = qr.expand("PDCCH 디코딩 재시작 문제 시뮬레이터 제외")
        types = {f["type"] for f in r2["fired"]}
        self.assertIn("acronym", types)
        self.assertIn("synonym", types)
        self.assertIn("exclude", types)
        self.assertIn("NOT", r2["fts_query"])

    # ---- MCP 소스 (mock 서버) ----
    def test_mcp_source_mock(self):
        from llmwiki import mcp_client as mc
        srcs = mc.load_sources()
        self.assertIn("mango", srcs)
        srcs["mock"]["enabled"] = True
        mc.save_sources(srcs)
        ts = mc.test_sources(self.s, ["mock"])
        self.assertTrue(ts[0]["ok"], ts)
        self.assertIn("list_issues", ts[0]["tools"])
        # data/mcp_cache 는 프로젝트 루트 아래 → 테스트에서는 임시 경로로 덮어씀
        mc.cache_dir = lambda name: os.path.join(self.tmp, "mcp_cache", name)   # type: ignore
        try:
            ing = mc.ingest(self.s, ["mock"])
            self.assertEqual(ing["errors"], [])
            self.assertEqual(ing["written"], 3)
            p = os.path.join(self.tmp, "mcp_cache", "mock", "issue", "ISSUE-9001.md")
            self.assertTrue(os.path.exists(p))
            with open(p, encoding="utf-8") as f:
                txt = f.read()
            self.assertIn("doc_type: issue", txt)
            self.assertIn("CL-7001", txt)
            ing2 = mc.ingest(self.s, ["mock"])
            self.assertEqual(ing2["written"], 0)
            self.assertEqual(ing2["skipped"], 3)
            self.p.s.toggles.mcp_sources = True
            res, tr = self.p.build(full=True)
            st = {c["name"]: c for c in tr["children"]}
            self.assertTrue(st["mcp_ingest"]["enabled"])
            self.assertEqual(res["docs"], 37 + 3)
            r, t = self.p.query("TX 전력 제어 PA gain 테이블 인덱스 오류", log=False)
            self.assertTrue(any("ISSUE-9001" in h["doc_id"] for h in r["hits"][:5]))
            rels = self.p.store.relations_of(entity_id_for("CL-7001"))
            self.assertTrue(any(x["rel"] == "fixes" and x["provenance"] == "explicit" for x in rels))
            en = mc.enrich(self.s, "AGC 수렴")
            self.assertTrue(any(e.get("id") == "ISSUE-9002" for e in en))
            out = run_captured(["mcp-source", "list"], self.s, self.p)
            self.assertIn("mango", out["output"])
        finally:
            mc.cache_dir = lambda name: os.path.join(ROOT, "data", "mcp_cache", name)   # type: ignore


if __name__ == "__main__":
    unittest.main()
