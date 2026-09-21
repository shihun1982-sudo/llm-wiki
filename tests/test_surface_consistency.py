# -*- coding: utf-8 -*-
"""세 창구 일치 — 같은 질문을 Web·CLI·MCP 로 물으면 **같은 답과 같은 근거**가 나오는가.

## 왜 이 테스트가 따로 필요한가

`tools/verify/verify_surface_align.py` 는 "기능마다 CLI 명령·Web 경로·MCP 도구가 **존재하는가**" 를 본다.
존재는 정렬의 절반이다. 나머지 절반은 **같은 것을 돌려주는가** 이고, 그쪽이 조용히 어긋난다:

  · Web 만 새 필드를 얻고 MCP 응답은 옛 모양으로 남는다 (붙은 LLM 이 필드를 못 찾아 헛돈다)
  · 인용 번호 `[C1]` 이 창구마다 다른 청크를 가리킨다 (사람이 Web 에서 본 근거와 LLM 이 받은 근거가 다르다)
  · 한쪽만 기본 프리셋·토글이 달라서 근거 수가 다르다

이 테스트는 **결정적인 조건**(mock LLM · hash 임베더 · 캐시 off)에서 같은 질의를 세 창구로 보내고,
"달라도 되는 것"(request_id·시간·표현 형식)과 "달라서는 안 되는 것"(근거 청크·인용 매핑·판정)을 갈라서 본다.

## 달라도 되는 것 / 안 되는 것

| 항목 | 같아야 하나 | 이유 |
|---|---|---|
| 답변 본문 | ✔ | 같은 컨텍스트 → 같은 mock 답변 |
| 컨텍스트에 들어간 청크 목록과 순서 | ✔ | 근거가 다르면 같은 시스템이 아니다 |
| `[C#] → chunk_id` 매핑 | ✔ | 화면에서 본 번호와 LLM 이 받은 번호가 같아야 한다 |
| 근거 판정(verdict)·result_type | ✔ | 판정이 창구마다 다르면 품질 지표를 믿을 수 없다 |
| request_id · query_id · 시각 · ms | ✘ | 실행마다 다르다 |
| 표현(텍스트 표 vs JSON) | ✘ | 창구마다 읽는 대상이 다르다 |

참고: `tools/verify/verify_surface_align.py`(존재) · 이 파일(일치) · `docs/CLI_FLOWS.md`
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki import mcp as mcpmod                 # noqa: E402
from llmwiki.config import Settings, Toggles      # noqa: E402
from llmwiki.pipeline import Pipeline             # noqa: E402
from llmwiki.web import server as ws              # noqa: E402

QUESTION = "ISSUE-2001 의 원인과 수정 CL 은?"


def _q(s):
    return urllib.parse.quote(str(s), safe="")


def _gen_corpus(out: str) -> None:
    spec = importlib.util.spec_from_file_location("mk", os.path.join(ROOT, "setup", "make_sample_corpus_modem.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    mod.gen(out, 1)


class SurfaceConsistencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        corpus = os.path.join(cls.tmp, "corpus")
        _gen_corpus(corpus)
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"),
                     wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=256)
        # query_cache 를 끄지 않으면 두 번째 창구가 캐시를 맞아 '같다' 가 공짜로 참이 된다 — 비교의 뜻이 없어진다.
        s.toggles = Toggles(query_cache=False, health_check=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)
        ws.Handler.pipe = cls.p
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ws.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.p.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------------- 창구별 호출 ----------------
    def _web(self, body):
        req = urllib.request.Request("http://127.0.0.1:%d/api/query" % self.port,
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))

    def _get(self, path):
        """Web GET — 창구 비교에서 '화면이 실제로 부르는 것' 쪽."""
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))

    def _cli(self, **kw):
        """CLI `query --json` 이 쓰는 경로 그대로 (cli.py 는 p.query 를 부르고 {result,trace} 로 감싼다)."""
        with self.p.request_scope():
            res, tr = self.p.query(QUESTION, log=False, **kw)
        return {"result": res, "trace": tr}

    def _mcp(self, args=None):
        out = mcpmod.call_tool(self.p, "wiki_query", dict({"question": QUESTION}, **(args or {})), federate=False)
        self.assertFalse(out.get("isError"), out)
        return out

    # ---------------- 비교 ----------------
    @staticmethod
    def _cite_map(hits):
        """[C#] → chunk_id. 컨텍스트에 들어간 근거만 번호를 받는다."""
        return {h["n"]: h["chunk_id"] for h in hits if h.get("n") is not None and h.get("in_context")}

    def test_same_evidence_across_web_cli_mcp(self):
        web = self._web({"q": QUESTION})["result"]
        cli = self._cli()["result"]
        mcp = self._mcp()["structuredContent"] if self._mcp().get("structuredContent") else None

        web_ids, cli_ids = self._cite_map(web["hits"]), self._cite_map(cli["hits"])
        self.assertTrue(web_ids, "Web 응답에 컨텍스트 근거가 없다")
        self.assertEqual(web_ids, cli_ids, "Web 과 CLI 의 [C#] → chunk_id 매핑이 다르다")
        self.assertEqual([h["chunk_id"] for h in web["hits"]], [h["chunk_id"] for h in cli["hits"]],
                         "후보 순서가 창구마다 다르다")
        if mcp is not None and mcp.get("citations"):
            self.assertEqual({int(c["n"]): c["chunk_id"] for c in mcp["citations"]}, web_ids,
                             "MCP 의 인용 매핑이 Web 과 다르다")

    def test_same_answer_and_verdict(self):
        web = self._web({"q": QUESTION})["result"]
        cli = self._cli()["result"]
        self.assertEqual(web["answer"], cli["answer"], "같은 질의인데 답변 본문이 다르다")
        self.assertEqual(web.get("result_type"), cli.get("result_type"))
        self.assertEqual((web.get("evidence") or {}).get("verdict"), (cli.get("evidence") or {}).get("verdict"))

    def test_mcp_text_mentions_the_same_citations(self):
        """MCP 는 텍스트로도 답한다 — 그 텍스트의 근거 목록이 Web 과 같은 문서를 가리켜야 한다."""
        web = self._web({"q": QUESTION})["result"]
        text = self._mcp()["content"][0]["text"]
        for h in web["hits"]:
            if h.get("in_context"):
                self.assertIn(h["doc_id"], text, "MCP 텍스트에 Web 의 근거 문서 %s 가 없다" % h["doc_id"])

    def test_structured_content_keeps_the_contract(self):
        """붙은 LLM 이 파싱하는 필드는 조용히 사라지면 안 된다."""
        sc = self._mcp().get("structuredContent") or {}
        for k in ("answer", "citations", "evidence"):
            self.assertIn(k, sc, "MCP structuredContent 에 %s 가 없다 (붙은 LLM 의 계약)" % k)

    def test_output_mode_is_the_same_everywhere(self):
        """요청 단위 손잡이(output_mode)도 세 창구가 같은 뜻으로 받는다."""
        web = self._web({"q": QUESTION, "overrides": {"output_mode": "fused"}})["result"]
        cli = self._cli(overrides={"output_mode": "fused"})["result"] if self._supports_overrides() else None
        mcp = mcpmod.call_tool(self.p, "wiki_query", {"question": QUESTION, "output_mode": "fused"}, federate=False)
        self.assertEqual(web.get("result_type"), "candidates_fused")
        msc = mcp.get("structuredContent") or {}
        self.assertEqual(msc.get("result_type"), "candidates_fused", "MCP 가 output_mode 를 다르게 해석한다")
        if cli is not None:
            self.assertEqual(cli.get("result_type"), "candidates_fused")

    def _supports_overrides(self):
        import inspect
        return "overrides" in inspect.signature(self.p.query).parameters

    def test_query_overrides_argument_is_not_silently_ignored(self):
        """`Pipeline.query(overrides=…)` 는 **받아 놓고 무시**했었다.

        인자를 조용히 버리면 부르는 쪽은 설정이 먹은 줄 알고, 왜 안 바뀌는지 찾을 단서가 없다.
        받았으면 적용하거나, 적용 못 하면 알려야 한다 — 여기서는 적용한다(안쪽 request_scope).
        """
        with self.p.request_scope():
            res, _ = self.p.query(QUESTION, log=False, overrides={"output_mode": "fused"})
        self.assertEqual(res.get("result_type"), "candidates_fused")
        # 바깥 범위를 더럽히지 않는다 (다음 질의는 원래대로)
        with self.p.request_scope():
            res2, _ = self.p.query(QUESTION, log=False)
        self.assertNotEqual(res2.get("result_type"), "candidates_fused")

    def test_channel_search_is_the_same_engine(self):
        """채널 검색은 Web `/api/search` · MCP `wiki_search` · CLI `search` 가 한 엔진(retrieval.channel_search)이다."""
        from llmwiki.profiler import Profiler
        from llmwiki.retrieval import channel_search, parse_channels
        body = {"q": "DMA 오버런", "channels": ["fts", "vector"], "mode": "and", "k": 5}
        req = urllib.request.Request("http://127.0.0.1:%d/api/search" % self.port,
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            web = json.loads(r.read().decode("utf-8"))
        mcp = mcpmod.call_tool(self.p, "wiki_search", dict(body, query=body["q"]), federate=False)
        msc = mcp.get("structuredContent") or {}
        with self.p.request_scope():
            direct = channel_search(self.p.store, self.p.embedder, self.p.s, body["q"],
                                    parse_channels(body["channels"]), mode=body["mode"], k=body["k"],
                                    prof=Profiler("t", log=False), acl=self.p.acl_filter())
        ids = [r_["chunk_id"] for r_ in direct["rows"]]
        self.assertEqual([r_["chunk_id"] for r_ in (web.get("rows") or web.get("result", {}).get("rows") or [])], ids,
                         "Web 채널 검색 결과가 엔진과 다르다")
        self.assertEqual([r_["chunk_id"] for r_ in (msc.get("rows") or [])], ids,
                         "MCP 채널 검색 결과가 엔진과 다르다")
        self.assertEqual(msc.get("mode"), direct["mode"])

    def test_doc_detail_is_the_same_everywhere(self):
        """문서 한 건: Web `/api/doc?id=` · MCP `wiki_doc` · CLI `docs` 가 한 함수(querydebug.doc_detail)."""
        from llmwiki import querydebug as qd
        doc_id = self.p.store.list_docs()[0]["doc_id"]
        web = self._get("/api/doc?id=%s" % doc_id)
        mcp = (mcpmod.call_tool(self.p, "wiki_doc", {"id": doc_id}, federate=False).get("structuredContent") or {})
        direct = qd.doc_detail(self.p, doc_id, 20000, role="admin")
        self.assertTrue(direct.get("chunks"), "비교할 청크가 없다 — 이 테스트가 빈 채로 통과하면 안 된다")
        self.assertEqual(web.get("doc_id"), direct.get("doc_id"))
        self.assertEqual([c.get("chunk_id") for c in (web.get("chunks") or [])],
                         [c.get("chunk_id") for c in (direct.get("chunks") or [])],
                         "Web 문서 상세의 청크가 엔진과 다르다")
        if mcp:
            self.assertEqual(mcp.get("doc_id"), direct.get("doc_id"), "MCP wiki_doc 이 다른 문서를 준다")

    def test_entity_lookup_is_the_same_everywhere(self):
        """엔티티: Web `/api/entity` 는 **이름으로도** 열 수 있어야 하고(2026-09-20), MCP `wiki_entity` 와 같은 것을 가리켜야 한다."""
        rows = self.p.store.conn.execute("SELECT entity_id, name FROM entities LIMIT 1").fetchall()
        if not rows:
            self.skipTest("이 코퍼스에서 엔티티가 만들어지지 않았다")
        ent_id, name = rows[0]["entity_id"], rows[0]["name"]
        by_id = self._get("/api/entity?id=%s" % _q(ent_id))
        by_name = self._get("/api/entity?name=%s" % _q(name))
        self.assertTrue(by_id.get("entity"), "id 로 연 엔티티가 비어 있다 — 아래 비교가 무의미해진다")
        self.assertTrue(by_name.get("entity"), "이름으로 열기(`?name=`)가 동작하지 않는다 — 질의 결과의 그래프 관계 표가 죽는다")
        self.assertEqual((by_id.get("entity") or {}).get("id"), (by_name.get("entity") or {}).get("id"),
                         "같은 엔티티인데 id 로 열 때와 이름으로 열 때가 다르다")
        mcp = (mcpmod.call_tool(self.p, "wiki_entity", {"name": name}, federate=False).get("structuredContent") or {})
        if mcp.get("entity"):
            self.assertEqual(mcp["entity"].get("id"), (by_id.get("entity") or {}).get("id"),
                             "MCP wiki_entity 가 Web 과 다른 엔티티를 준다")

    def test_ops_stats_is_the_same_everywhere(self):
        """운영 통계: CLI `stats --full` · Web `/api/opstats` · MCP `wiki_status(full=true)` 가 한 모듈(opstats)."""
        from llmwiki import opstats as O
        web = self._get("/api/opstats?days=30&sections=index,storage")
        mcp = (mcpmod.call_tool(self.p, "wiki_status", {"full": True, "days": 30, "sections": ["index", "storage"]},
                                federate=False).get("structuredContent") or {})
        direct = O.collect(self.p, days=30, sections=["index", "storage"])
        self.assertGreater(direct["index"]["docs"], 0, "색인이 비어 비교가 무의미하다")
        self.assertEqual(web.get("index", {}).get("docs"), direct["index"]["docs"], "Web 운영 통계가 엔진과 다르다")
        self.assertEqual(set(web.get("sections") or []), set(direct["sections"]))
        got = mcp.get("ops") or mcp.get("index") and mcp or {}
        if got:
            idx = (got.get("ops") or got).get("index") or {}
            self.assertEqual(idx.get("docs"), direct["index"]["docs"], "MCP wiki_status(full) 가 엔진과 다르다")

    def test_graph_rules_view_is_the_same_everywhere(self):
        """그래프 빌드 규칙: Web `/api/graph_rules` · MCP `wiki_graph_rules(action=types)` · CLI `graph-rules types`."""
        from llmwiki import graph_rules as gr
        web = self._get("/api/graph_rules")
        out = mcpmod.call_tool(self.p, "wiki_graph_rules", {"action": "types"}, federate=False)
        rules = gr.load_rules()
        direct = sorted(gr.known_types(rules))
        self.assertEqual(sorted(web.get("entity_types") or web.get("types") or []), direct,
                         "Web 그래프 규칙의 엔티티 유형이 엔진과 다르다")
        # MCP 는 `structuredContent` 에 **요약**(개수)을, `content[0].text` 에 전문을 싣는다 —
        # 붙은 LLM 이 읽는 것은 텍스트 쪽이므로 비교도 그쪽으로 한다.
        mcp = json.loads(out["content"][0]["text"])
        self.assertEqual(sorted(mcp.get("entity_types") or []), direct,
                         "MCP wiki_graph_rules 의 엔티티 유형이 엔진과 다르다")
        self.assertEqual((out.get("structuredContent") or {}).get("n_types"), len(direct),
                         "MCP 요약(n_types)이 본문과 어긋난다")

    def test_rules_explain_is_the_same_everywhere(self):
        """질의 규칙 설명: Web `/api/query_rules/explain?term=` · MCP `wiki_rules(action=explain)` · CLI `rules explain`."""
        from llmwiki import query_rules as qr
        rules = qr.load_rules()
        # query_rules.json 은 {유형: {용어: [값…]}} 모양이다 — 아무 유형에서나 첫 용어를 고른다.
        term = None
        for _t, m in rules.items():
            if isinstance(m, dict) and m:
                term = next(iter(m))
                break
        self.assertTrue(term, "query_rules.json 에 규칙이 하나도 없다 — 이 비교가 빈 채로 통과하면 안 된다")
        web = self._get("/api/query_rules/explain?term=%s" % _q(term))
        mcp = (mcpmod.call_tool(self.p, "wiki_rules", {"action": "explain", "term": term},
                                federate=False).get("structuredContent") or {})
        self.assertEqual(web.get("term"), term)
        if mcp:
            self.assertEqual(mcp.get("term"), web.get("term"), "MCP 규칙 설명이 Web 과 다른 용어를 본다")
            self.assertEqual(len(mcp.get("edges") or mcp.get("expansions") or []),
                             len(web.get("edges") or web.get("expansions") or []),
                             "같은 용어인데 창구마다 확장 수가 다르다")

    def test_empty_term_is_rejected_the_same_way(self):
        """실패도 정렬의 일부다 — 용어 없이 부르면 세 창구가 모두 **거절**해야 한다.

        한쪽만 200 + 빈 껍데기를 주면 화면은 '규칙이 없다' 로 잘못 읽는다.
        """
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/query_rules/explain?term=")
        self.assertEqual(cm.exception.code, 400)
        out = mcpmod.call_tool(self.p, "wiki_rules", {"action": "explain", "term": ""}, federate=False)
        self.assertTrue(out.get("isError") or "error" in json.dumps(out.get("structuredContent") or {}),
                        "MCP 는 빈 용어를 받아 넘긴다 — Web 은 400 인데 창구마다 다르다")

    def test_time_parse_is_the_same_everywhere(self):
        """시간 표현: Web `/api/time?q=` · CLI `time` · 해부(`wiki_inspect`) 안의 time 이 한 파서(timeparse)."""
        from llmwiki import timeparse as tp
        expr = "지난주"
        web = self._get("/api/time?q=%s" % _q(expr))
        direct = tp.parse(expr, self.p.s.timezone, self.p.s.week_start) or {"expr": None}
        self.assertTrue(direct.get("from"), "'지난주' 를 파서가 못 읽는다 — 비교 대상이 없다")
        self.assertEqual(web.get("from"), direct.get("from"))
        self.assertEqual(web.get("to"), direct.get("to"))
        self.assertEqual(web.get("kind"), direct.get("kind"))
        mcp = (mcpmod.call_tool(self.p, "wiki_inspect", {"query": expr + " DMA 오버런"},
                                federate=False).get("structuredContent") or {})
        mt = (mcp.get("time") or {}).get("scope") or {}
        self.assertTrue(mt.get("from"), "해부 결과에 시간 범위가 없다 — MCP 만 시간 표현을 흘려보낸다")
        self.assertEqual((mt.get("from"), mt.get("to"), mt.get("kind")),
                         (direct.get("from"), direct.get("to"), direct.get("kind")),
                         "해부 안의 시간 범위가 /api/time 과 다르다 — 파서가 두 곳에 있다는 뜻")

    def test_status_is_the_same_everywhere(self):
        """색인 상태: Web `/api/status` · MCP `wiki_status` · CLI `stats`."""
        web = self._get("/api/status")
        out = mcpmod.call_tool(self.p, "wiki_status", {}, federate=False)
        mcp = json.loads(out["content"][0]["text"]) if out.get("content") else {}
        direct = self.p.store.stats()
        self.assertGreater(direct.get("docs") or 0, 0, "색인이 비어 비교가 무의미하다")
        for k in ("docs", "chunks"):
            self.assertEqual((web.get("stats") or {}).get(k), direct.get(k),
                             "Web 상태의 %s 가 저장소와 다르다" % k)
            got = mcp.get(k)
            if got is None:
                got = ((mcp.get("stats") or mcp.get("index") or {}) or {}).get(k)
            if got is not None:
                self.assertEqual(got, direct.get(k), "MCP 상태의 %s 가 저장소와 다르다" % k)

    def test_proposal_description_is_the_same_everywhere(self):
        """제안 설명: CLI `evolve show` · Web `/api/evolve/describe` · MCP `wiki_evolve(id=)` 가 한 모듈."""
        from llmwiki import evolve as ev
        out = mcpmod.call_tool(self.p, "wiki_propose",
                               {"kind": "query_rule",
                                "payload": {"type": "synonym", "key": "DMA", "values": ["직접 메모리 접근"]},
                                "reason": "세 창구 일치 확인용"}, federate=False)
        self.assertFalse(out.get("isError"), out)
        # wiki_propose 는 번호를 본문 JSON 으로 돌려준다 (structuredContent 없음).
        pid = json.loads(out["content"][0]["text"]).get("proposal_id")
        self.assertTrue(pid, "wiki_propose 가 제안 번호를 돌려주지 않았다 — 붙은 LLM 이 자기 제안을 다시 가리킬 수 없다")
        web = self._get("/api/evolve/describe?id=%d" % int(pid))
        direct = ev.describe_proposal(self.p, int(pid))
        mcp = (mcpmod.call_tool(self.p, "wiki_evolve", {"id": int(pid)}, federate=False).get("structuredContent") or {})
        self.assertTrue(direct.get("title"), "제안 설명에 제목이 없다 — 비교가 무의미하다")
        self.assertEqual(web.get("title"), direct.get("title"), "Web 제안 설명이 엔진과 다르다")
        self.assertEqual([c.get("text") for c in (web.get("checks") or [])],
                         [c.get("text") for c in (direct.get("checks") or [])],
                         "Web 의 점검 항목이 엔진과 다르다")
        if mcp:
            d = mcp.get("proposal") or mcp.get("describe") or mcp
            self.assertEqual(d.get("title"), direct.get("title"), "MCP 제안 설명이 Web 과 다르다")

    def test_inspect_is_the_same_everywhere(self):
        """질의 해부(LLM 없이)도 Web `/api/debug/query` · MCP `wiki_inspect` · CLI `inspect` 가 한 함수다."""
        from llmwiki import querydebug as qd
        req = urllib.request.Request("http://127.0.0.1:%d/api/debug/query" % self.port,
                                     data=json.dumps({"q": QUESTION}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            web = json.loads(r.read().decode("utf-8"))
        mcp = (mcpmod.call_tool(self.p, "wiki_inspect", {"query": QUESTION}, federate=False).get("structuredContent") or {})
        with self.p.request_scope():
            direct = qd.inspect_query(self.p, QUESTION)
        for k in ("normalized", "tokens", "keywords"):
            self.assertEqual(web.get(k), direct.get(k), "Web 해부의 %s 가 엔진과 다르다" % k)
            self.assertEqual(mcp.get(k), direct.get(k), "MCP 해부의 %s 가 엔진과 다르다" % k)


if __name__ == "__main__":
    unittest.main(verbosity=2)
