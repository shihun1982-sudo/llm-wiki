# -*- coding: utf-8 -*-
"""Knowledge 회차 (2026-09-24): 그래프 진단 **소견** · 사전 매처(단어 경계·대소문자) · 그래프 탭 뒷단(export 필터·이웃·중심·무리 상세) · 세 창구 정합.

무엇을 확인하나
  1. 매처: 'first' 의 IR, 'director' 의 CTO 같은 단어 내부 매칭이 사라지고 실제 언급은 남는다. 엔티티별 match 덮어쓰기 · 끄면 예전 동작 ·
     질의 쪽 name_in_text 도 같은 규칙 · lint 가 모르는 키를 알린다.
  2. 소견: 모든 소견이 같은 모양(증거·원인·처방·확인)이고 id/area 가 목록 안이며, 심어 둔 결함(관계 키 미매핑·쓰레기 제목)을 잡는다.
  3. graph_export: 무리 필터가 자르기 **전**에 적용 · neighbors · structure 만 · center/hops · 화면 기준 집계.
  4. community_export 가 CLI `graph community` · MCP wiki_community 와 같은 dict 를 준다. 서버는 잘못된 값에 400/404.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402
from llmwiki import graph_profile as gp  # noqa: E402
from llmwiki import graph_findings as gf  # noqa: E402
from llmwiki import graph_rules as gr  # noqa: E402
from llmwiki import tuning as tn  # noqa: E402
from llmwiki import schema as sc  # noqa: E402
from llmwiki import query_rules as qr  # noqa: E402

try:
    from tests.test_graph_profile import _gen_corpus, ENV_KEYS  # noqa: E402
except ImportError:  # unittest discover -s tests
    from test_graph_profile import _gen_corpus, ENV_KEYS  # type: ignore  # noqa: E402


class MatcherTest(unittest.TestCase):
    SENT = "The first director will coordinate synchronous transfers; this factor is an abbreviation. IR 본부와 CTO 가 AMD, 삼성전자, 베이스밴드는 참여."

    def _rules(self, **matching):
        r = json.loads(json.dumps(gr.DEFAULT_RULES))
        r["entities"] = {"IR본부": {"type": "org_unit", "aliases": ["IR"]}, "CTO": {"type": "role", "aliases": []},
                         "COO": {"type": "role", "aliases": []}, "CHRO": {"type": "role", "aliases": []},
                         "베이스밴드": {"type": "module", "aliases": ["BB"]}, "AMD": {"type": "org", "aliases": []},
                         "Samsung": {"type": "org", "aliases": ["삼성전자"]}}
        r["matching"] = dict(gr.MATCHING_DEFAULTS, **matching)
        return r

    def test_default_matching_drops_embedded_and_keeps_real_mentions(self):
        found = [c for c, _ in gr.RuleExtractor(self._rules()).find_entities(self.SENT)]
        self.assertEqual(found, ["IR본부", "CTO", "AMD", "Samsung", "베이스밴드"])

    def test_legacy_matching_reproduces_the_false_positives(self):
        found = [c for c, _ in gr.RuleExtractor(self._rules(ascii_word_boundary=False, case_sensitive_max_len=0)).find_entities(self.SENT)]
        self.assertIn("COO", found)        # coordinate
        self.assertIn("CHRO", found)       # synchronous
        self.assertGreater(found.count("IR본부"), 1)

    def test_per_entity_override_wins(self):
        r = self._rules()
        r["entities"]["IR본부"]["match"] = {"whole_word": False, "case_sensitive": False}
        found = [c for c, _ in gr.RuleExtractor(r).find_entities("first")]
        self.assertEqual(found, ["IR본부"])
        r["entities"]["IR본부"]["match"] = {"whole_word": True, "case_sensitive": True}
        self.assertEqual(gr.RuleExtractor(r).find_entities("ir 본부 first"), [])
        self.assertEqual([c for c, _ in gr.RuleExtractor(r).find_entities("IR 본부")], ["IR본부"])

    def test_longer_alias_wins_when_overlapping(self):
        r = self._rules()
        r["entities"]["SK하이닉스"] = {"type": "org", "aliases": ["SK hynix", "SK"]}
        found = [c for c, _ in gr.RuleExtractor(r).find_entities("SK hynix and SK")]
        self.assertEqual(found, ["SK하이닉스", "SK하이닉스"])

    def test_query_side_uses_the_same_boundary_rule(self):
        self.assertFalse(gr.name_in_text("ir", "first step", True))
        self.assertTrue(gr.name_in_text("ir", "ir 본부 현황", True))
        self.assertTrue(gr.name_in_text("베이스밴드", "베이스밴드는 어디에", True))
        self.assertTrue(gr.name_in_text("ir", "first step", False))     # 끄면 예전 동작

    def test_lint_knows_the_new_keys(self):
        r = self._rules()
        r["matching"]["typo"] = 1
        r["entities"]["CTO"]["match"] = {"whole_wrod": True}
        issues = gr.lint(r)["issues"]
        self.assertTrue(any(i["where"] == "matching.typo" for i in issues))
        self.assertTrue(any(i["where"].startswith("entities[CTO].match.") for i in issues))
        self.assertFalse([i for i in gr.lint(self._rules())["issues"] if i["level"] == "error"])

    def test_fill_defaults_adds_matching_section(self):
        d = tempfile.mkdtemp(prefix="lwmatch_")
        try:
            path = os.path.join(d, "rules.json")
            r = json.loads(json.dumps(gr.DEFAULT_RULES))
            r.pop("matching", None)
            gr.save_rules(r, path)
            rep = gr.fill_defaults(path)
            self.assertIn("matching", rep["added"])
            self.assertEqual(gr.load_rules(path)["matching"], gr.MATCHING_DEFAULTS)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class _PipelineBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="lwgf_")
        cls.corpus = os.path.join(cls.tmp, "corpus")
        _gen_corpus(cls.corpus)
        for k in ENV_KEYS:
            os.environ["LLMWIKI_%s_PATH" % k] = os.path.join(cls.tmp, k.lower())
        os.environ["LLMWIKI_RULES_PATH"] = os.path.join(cls.tmp, "rules.json")
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        cls._tuning_path = tn.TUNING_PATH
        tn.TUNING_PATH = os.path.join(cls.tmp, "tuning.json")
        s = Settings(corpus_dirs=[cls.corpus], data_dir=os.path.join(cls.tmp, "data"), wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=256, graph_profile_keep=3, graph_profile_hubs=5, graph_profile_requests=50)
        s.toggles = Toggles(query_cache=False, health_check=False, community_summary=False, wiki_pages=False)
        cls.s = s
        cls.p = Pipeline(s)
        cls.p.build(full=True)
        cls.p.query("ISSUE-2001 의 근본 원인은?", log=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        for k in ENV_KEYS:
            os.environ.pop("LLMWIKI_%s_PATH" % k, None)
        os.environ.pop("LLMWIKI_RULES_PATH", None)
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        tn.load_tuning(os.path.join(cls.tmp, "none.json"))
        tn.TUNING_PATH = cls._tuning_path
        shutil.rmtree(cls.tmp, ignore_errors=True)


class FindingsTest(_PipelineBase):
    def test_shape_and_vocabulary(self):
        prof = gp.profile(self.p)
        fr = prof["findings"]
        self.assertEqual(fr["errors"], [], fr["errors"])
        for f in fr["findings"]:
            self.assertEqual(set(f), {"id", "severity", "area", "title", "why", "evidence", "fix", "verify"}, f["id"])
            self.assertIn(f["id"], gf.FINDING_IDS)
            self.assertIn(f["area"], gf.AREAS)
            self.assertIn(f["severity"], ("error", "warn", "info"))
            self.assertEqual(set(f["evidence"]), {"numbers", "samples"})
            self.assertIn(f["fix"]["kind"], ("rules_patch", "corpus_edit", "tuning_set", "build_cmd", "query_rules_patch"))
            self.assertTrue(f["fix"]["steps"] and f["fix"]["commands"], f["id"])
            self.assertTrue(f["verify"].get("metric") and f["verify"].get("command"), f["id"])
        self.assertIn("findings_error", gp.key_metrics(prof))
        txt, md = gp.render_text(prof), gp.render_markdown(prof)
        self.assertIn("소견", txt)
        self.assertIn("## 소견", md)

    def test_planted_defects_are_found_with_concrete_fixes(self):
        st = self.p.store
        docs = st.list_docs()
        self.assertGreaterEqual(len(docs), 3)
        d0, d1, d2 = docs[0]["doc_id"], docs[1]["doc_id"], docs[2]["doc_id"]
        saved = {r["doc_id"]: (r["related"], r["doc_type"]) for r in st.conn.execute("SELECT doc_id, related, doc_type FROM doc_meta WHERE doc_id IN (?,?,?)", (d0, d1, d2))}
        titles = {d["doc_id"]: d["title"] for d in docs[:3]}
        try:
            # (1) 규칙에 없는 related 키 (2) 쓰레기 제목 2건 (3) 무리 0개
            st.conn.execute("UPDATE doc_meta SET related=? WHERE doc_id=?", (json.dumps({"obsoletes": ["ISSUE-2001"]}), d0))
            st.conn.execute("UPDATE docs SET title=? WHERE doc_id=?", ("-*- coding: utf-8 -*-", d1))
            st.conn.execute("UPDATE docs SET title=? WHERE doc_id=?", ("#!/usr/bin/env python", d2))
            st.conn.execute("DELETE FROM communities")
            st.conn.commit()
            st._meta_cache = None
            prof = gp.profile(self.p)
            by_id = {f["id"]: f for f in prof["findings"]["findings"]}
            self.assertIn("related_key_unmapped", by_id)
            rk = by_id["related_key_unmapped"]
            self.assertIn("obsoletes", rk["fix"]["snippet"]["related_key_type"])
            self.assertTrue(any(u["key"] == "obsoletes" for u in rk["evidence"]["samples"]))
            self.assertIn("junk_titles", by_id)
            jt = by_id["junk_titles"]
            self.assertEqual(jt["fix"]["kind"], "corpus_edit")
            self.assertTrue({s["doc_id"] for s in jt["evidence"]["samples"]} >= {d1, d2})
            self.assertIn("no_communities", by_id)
            self.assertEqual(by_id["no_communities"]["area"], "build")
            self.assertIn("build graph", " ".join(by_id["no_communities"]["fix"]["commands"]))
        finally:
            for did, (rel, _dt) in saved.items():
                st.conn.execute("UPDATE doc_meta SET related=? WHERE doc_id=?", (rel, did))
            for did, t in titles.items():
                st.conn.execute("UPDATE docs SET title=? WHERE doc_id=?", (t, did))
            st.conn.commit()
            st._meta_cache = None
            self.p.build(full=True)      # 무리 복구

    def test_alias_false_positive_is_measured_from_real_contexts(self):
        """별칭 'BB' 가 'abbreviation' 안에서만 잡히는 상황을 만들면 소견이 그 문맥을 증거로 든다."""
        st = self.p.store
        rules = gr.load_rules()
        rules["entities"]["베이스밴드"] = {"type": "module", "aliases": ["BB"]}
        rules["matching"] = {"ascii_word_boundary": False, "case_sensitive_max_len": 0}     # 예전 매처로 빌드
        gr.save_rules(rules)
        self.p.reload()          # 파이프라인은 추출기를 캐시한다 — 규칙 파일을 바꾸면 다시 읽게 한다
        try:
            extra = os.path.join(self.corpus, "abbrev_note.md")
            with open(extra, "w", encoding="utf-8") as f:
                f.write("---\ntitle: abbreviation memo\ndoc_type: note\n---\n" + "\n".join("The abbreviation and the rabbit hobby are subbed here %d." % i for i in range(12)))
            self.p.build(full=True)
            prof = gp.profile(self.p)
            by_id = {f["id"]: f for f in prof["findings"]["findings"]}
            self.assertIn("alias_false_positive", by_id, [f["id"] for f in prof["findings"]["findings"]])
            f = by_id["alias_false_positive"]
            names = [s["name"] for s in f["evidence"]["samples"]]
            self.assertIn("베이스밴드", names)
            samp = next(s for s in f["evidence"]["samples"] if s["name"] == "베이스밴드")
            self.assertTrue(any("[bb]" in x.lower() for x in samp["samples"]), samp["samples"])
            self.assertIn("베이스밴드", f["fix"]["snippet"]["entities"])
            self.assertFalse(f["evidence"]["numbers"]["matching_enabled"])
        finally:
            os.remove(extra)
            rules["matching"] = dict(gr.MATCHING_DEFAULTS)
            rules["entities"].pop("베이스밴드", None)
            gr.save_rules(rules)
            self.p.reload()
            self.p.build(full=True)


class GraphExportTest(_PipelineBase):
    def test_community_filter_is_applied_before_the_cut(self):
        comms = self.p.store.communities_all()
        self.assertTrue(comms)
        small = min(comms, key=lambda c: c["size"])
        g = self.p.graph_export(limit=5, community=int(small["community"]))
        self.assertTrue(g["nodes"], "작은 무리를 고르면 노드가 0개였다 (자른 뒤에 거르던 버그)")
        self.assertTrue(all(n["community"] == int(small["community"]) for n in g["nodes"]))
        self.assertEqual([c["community"] for c in g["communities"]], [int(small["community"])])
        self.assertGreaterEqual(g["all_communities_n"], len(comms))

    def test_shape_neighbors_and_shown_counts(self):
        g = self.p.graph_export(limit=10)
        self.assertEqual(g["shown"], len(g["nodes"]))
        self.assertLessEqual(g["shown"], 10)
        self.assertGreater(g["total_entities"], g["shown"])
        for n in g["nodes"]:
            self.assertIn("neighbors", n)
            self.assertLessEqual(n["neighbors"], n["degree"])
        self.assertEqual(sum(g["edge_counts_shown"].values()), len(g["edges"]))
        shown_comms = {n["community"] for n in g["nodes"] if n["community"] is not None and n["community"] >= 0}
        self.assertEqual({c["community"] for c in g["communities"]}, shown_comms)

    def test_structure_only_and_provenance_filters(self):
        g = self.p.graph_export(limit=60, edge_kinds="structure")
        self.assertTrue(all(e["rel"] not in Pipeline.COOCCUR_RELS and e["provenance"] != "cooccur" for e in g["edges"]))
        self.assertEqual(g["edge_kinds"], "structure")
        g2 = self.p.graph_export(limit=60, provenance="explicit")
        self.assertTrue(all(e["provenance"] == "explicit" for e in g2["edges"]))

    def test_center_and_hops(self):
        g = self.p.graph_export(limit=50, center="ISSUE-2001", hops=1)
        self.assertEqual(g["nodes"][0]["name"], "ISSUE-2001")
        ids = {n["id"] for n in g["nodes"]}
        center = g["nodes"][0]["id"]
        for n in g["nodes"][1:]:
            self.assertTrue(any((e["src"] == center and e["dst"] == n["id"]) or (e["dst"] == center and e["src"] == n["id"]) for e in g["edges"]), n["name"])
        g2 = self.p.graph_export(limit=200, center="ISSUE-2001", hops=2)
        self.assertGreaterEqual(len(g2["nodes"]), len(ids))
        self.assertEqual(g2["hops"], 2)

    def test_types_filter(self):
        g = self.p.graph_export(limit=50, types=["issue"])
        self.assertTrue(g["nodes"])
        self.assertTrue(all(n["type"] == "issue" for n in g["nodes"]))


class CommunityThreeSurfacesTest(_PipelineBase):
    def test_export_and_label(self):
        comms = self.p.store.communities_all()
        cid = int(comms[0]["community"])
        d = self.p.community_export(cid)
        self.assertEqual(d["community"], cid)
        self.assertTrue(d["members"] and d["label"] and d["types"])
        self.assertEqual(d["summary_source"], "rule")
        self.assertIn(d["members"][0]["name"], d["label"])        # 규칙 기본문이면 대표 엔티티 3개가 이름
        self.assertLessEqual(d["structural_edges"], len(d["edges"]))
        self.assertIsNone(self.p.community_export(99999))

    def test_cli_and_mcp_agree_with_the_engine(self):
        from llmwiki.cli import run_captured
        from llmwiki import mcp
        cid = int(self.p.store.communities_all()[0]["community"])
        eng = self.p.community_export(cid)
        r = run_captured(["graph", "community", "--community", str(cid), "--json"], self.s, self.p)
        self.assertEqual(r["code"], 0, r)
        cli = json.loads(r["output"])
        self.assertEqual((cli["community"], cli["size"], cli["label"], [m["id"] for m in cli["members"]]),
                         (eng["community"], eng["size"], eng["label"], [m["id"] for m in eng["members"]]))
        res = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "wiki_community", "arguments": {"id": cid}}})
        sc_ = res["result"]["structuredContent"]
        self.assertEqual([m["id"] for m in sc_["members"]], [m["id"] for m in eng["members"]])
        bad = mcp.handle(self.p, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "wiki_community", "arguments": {"id": 99999}}})
        self.assertTrue(bad["result"].get("isError"))
        r2 = run_captured(["graph", "community"], self.s, self.p)
        self.assertEqual(r2["code"], 2)


class GraphHttpTest(_PipelineBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from llmwiki.web import server as ws
        cls.ws = ws
        cls._prev = (getattr(ws.Handler, "pipe", None), getattr(ws.Handler, "auth", None))
        ws.Handler.pipe = cls.p
        ws.Handler.auth = None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ws.Handler)
        cls.httpd.daemon_threads = True
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.ws.Handler.pipe, cls.ws.Handler.auth = cls._prev
        super().tearDownClass()

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=20) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")

    def test_filters_reach_the_engine_and_bad_values_are_400(self):
        code, g = self._get("/api/graph?limit=40&provenance=explicit")
        self.assertEqual(code, 200)
        self.assertTrue(all(e["provenance"] == "explicit" for e in g["edges"]))
        code, g = self._get("/api/graph?limit=40&types=issue")
        self.assertTrue(g["nodes"] and all(n["type"] == "issue" for n in g["nodes"]))
        code, _ = self._get("/api/graph?community=abc")
        self.assertEqual(code, 400)
        code, g = self._get("/api/graph?limit=20&edge_kinds=structure&center=ISSUE-2001&hops=1")
        self.assertEqual(code, 200)
        self.assertEqual(g["nodes"][0]["name"], "ISSUE-2001")

    def test_community_endpoint(self):
        cid = int(self.p.store.communities_all()[0]["community"])
        code, d = self._get("/api/community?id=%d" % cid)
        self.assertEqual(code, 200)
        self.assertEqual(d["community"], cid)
        self.assertEqual(self._get("/api/community?id=x")[0], 400)
        self.assertEqual(self._get("/api/community?id=99999")[0], 404)

    def test_profile_markdown_download(self):
        req = urllib.request.Request(self.base + "/api/graph/profile?format=md")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.assertIn("text/markdown", r.headers.get("Content-Type", ""))
            body = r.read().decode("utf-8")
        self.assertIn("# 그래프 진단 프로파일", body)
        self.assertIn("## 소견", body)


if __name__ == "__main__":
    unittest.main()
