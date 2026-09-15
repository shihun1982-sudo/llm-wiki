# -*- coding: utf-8 -*-
"""2026-09-14 기능: 채널 빌드(fts/vector/graph 독립) · 문서 단위 확장(doc_expand) · LLM 재시도/실패 보고(headless mock, HTTP timeout) ·
기대 결과 포렌식(forensic expect) · MCP Streamable HTTP + API 키 + 브리지 · 제안 kind(pin/query_rule/tuning) 적용."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402
from llmwiki import tuning as tn  # noqa: E402
from llmwiki import schema as sc  # noqa: E402
from llmwiki import query_rules as qr  # noqa: E402
from llmwiki import headless as hl  # noqa: E402
from llmwiki import auth as A  # noqa: E402
from llmwiki.cli import run_captured  # noqa: E402


def _gen_corpus(out: str) -> None:
    spec = importlib.util.spec_from_file_location("mk", os.path.join(ROOT, "setup", "make_sample_corpus_modem.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    mod.gen(out, 1)


def _st(trace):
    return {c["name"]: c for c in trace["children"]}


class _Base(unittest.TestCase):
    KEYS = ("LOGS_DIR", "SCHEMAS_DIR", "QUERY_RULES", "MCP_SOURCES", "PROMPTS_DIR", "PINS", "PRESETS", "AGENTS", "SECURITY", "RULES")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus = os.path.join(self.tmp, "corpus")
        _gen_corpus(self.corpus)
        for k in self.KEYS:
            os.environ["LLMWIKI_%s_PATH" % k] = os.path.join(self.tmp, k.lower() + (".json" if k in ("RULES", "SECURITY", "AGENTS", "PINS", "QUERY_RULES", "PRESETS", "MCP_SOURCES") else ""))
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        self._tuning_path = tn.TUNING_PATH
        tn.TUNING_PATH = os.path.join(self.tmp, "tuning.json")
        tn.load_tuning(tn.TUNING_PATH)
        s = Settings(corpus_dirs=[self.corpus], data_dir=os.path.join(self.tmp, "data"), wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=256)
        s.toggles = Toggles(query_cache=False, health_check=False, precompute=False)
        self.s = s
        self.p = Pipeline(s)
        self.p.build(full=True)

    def tearDown(self):
        self.p.store.close()
        for k in self.KEYS:
            os.environ.pop("LLMWIKI_%s_PATH" % k, None)
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        tn.load_tuning(os.path.join(self.tmp, "none.json"))
        tn.TUNING_PATH = self._tuning_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _counts(self):
        c = self.p.store.conn
        return {t: c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ("chunks", "chunks_fts", "embeddings", "entities", "relations", "mentions")}


class ChannelBuildTest(_Base):
    def test_channel_builds_are_independent(self):
        base = self._counts()
        self.assertTrue(base["chunks"] > 0 and base["chunks_fts"] == base["chunks"] and base["embeddings"] == base["chunks"])
        v0 = self.p.store.build_version()
        # fts 만 다시: 다른 채널 행 수 불변, verify OK, build_version 증가
        res, tr = self.p.build_channel("fts")
        self.assertEqual(res["channel"], "fts")
        self.assertEqual(res["counts_after"]["embeddings"], base["embeddings"])
        self.assertEqual(res["counts_after"]["entities"], base["entities"])
        self.assertTrue(res["verify"]["ok"], res["verify"])
        self.assertGreater(res["build_version"], v0)
        self.assertIn("reindex_fts", {c["name"] for c in _st(tr)["build_channel"]["children"]})
        self.assertFalse(any(a["check"] == "channel_isolation" for a in res["alerts"]))
        # vector: 임베딩을 일부 지운 뒤 채널 빌드 → 복구, FTS/그래프 불변
        self.p.store.conn.execute("DELETE FROM embeddings WHERE rowid IN (SELECT rowid FROM embeddings LIMIT 5)")
        self.p.store.conn.commit()
        self.p.store.invalidate_caches()
        res2, _ = self.p.build_channel("vector")
        self.assertEqual(res2["embedded"], 5)
        self.assertEqual(res2["counts_after"]["embeddings"], base["embeddings"])
        self.assertEqual(res2["counts_after"]["fts"], base["chunks_fts"])
        self.assertEqual(res2["counts_after"]["entities"], base["entities"])
        # vector --full: 전부 다시 (hash 는 IDF 재적합)
        res3, _ = self.p.build_channel("vector", full=True)
        self.assertEqual(res3["embedded"], base["chunks"])
        # graph: 그래프를 비운 뒤 채널 빌드 → 복구
        self.p.store.clear_graph()
        self.p.store.conn.commit()
        self.assertEqual(self._counts()["entities"], 0)
        res4, _ = self.p.build_channel("graph")
        after = self._counts()
        self.assertEqual(after["entities"], base["entities"])
        self.assertEqual(after["relations"], base["relations"])
        self.assertEqual(after["chunks_fts"], base["chunks_fts"])
        self.assertEqual(after["embeddings"], base["embeddings"])
        self.assertTrue(res4["verify"]["ok"], res4["verify"])
        # 질의는 여전히 정상 (캐시 무효화 후; 상위 hit 은 doc_expand 확장 청크가 섞이므로 넉넉히 본다)
        r, t = self.p.query("ISSUE-2001 원인과 수정 CL", log=False)
        self.assertTrue(any("ISSUE-2001" in h["doc_id"] for h in r["hits"][:8]), [h["doc_id"] for h in r["hits"][:8]])
        with self.assertRaises(ValueError):
            self.p.build_channel("bogus")

    def test_channels_option_and_build_fts_toggle(self):
        # --channels fts: 임베딩/그래프 단계 skipped, 토글은 원복
        with open(os.path.join(self.corpus, "issues", "ISSUE-2001.md"), "a", encoding="utf-8") as f:
            f.write("\n\n## 추가\n채널 빌드 테스트 문장 CHANNELTEST.\n")
        res, tr = self.p.build(channels=["fts"])
        self.assertEqual(res["channels"], ["fts"])
        st = _st(tr)
        self.assertFalse(st["embed"]["enabled"])
        self.assertFalse(st["graph_build"]["enabled"])
        self.assertTrue(self.p.s.toggles.embed and self.p.s.toggles.rule_graph)
        self.assertTrue(self.p.store.fts_search('"channeltest"', 3))
        cov = self.p.store.embed_coverage("hash")
        self.assertLess(cov["coverage"], 1.0)                       # 바뀐 문서의 새 청크는 아직 임베딩 없음
        res2, _ = self.p.build_channel("vector")                    # → vector 채널만 보충
        self.assertGreater(res2["embedded"], 0)
        self.assertEqual(self.p.store.embed_coverage("hash")["coverage"], 1.0)
        with self.assertRaises(ValueError):
            self.p.build(channels=["nope"])
        # build_fts off: FTS 행을 쓰지 않음 → verify 가 fts_missing 보고, build fts 로 복구
        with open(os.path.join(self.corpus, "issues", "ISSUE-2002.md"), "a", encoding="utf-8") as f:
            f.write("\n\n## 추가2\nFTS 가 꺼진 상태에서 추가된 문장 FTSOFFTEST 입니다 (chunk_min_chars 보다 길게).\n")
        self.p.s.toggles.build_fts = False
        res3, tr3 = self.p.build()
        self.p.s.toggles.build_fts = True
        self.assertTrue(any(a["check"] == "build_fts" for a in res3["alerts"]))
        self.assertFalse(self.p.store.fts_search('"ftsofftest"', 3))
        vr = self.p.store.verify("hash")
        self.assertTrue(any(c["name"] == "fts_missing" and not c["ok"] for c in vr["checks"]))
        self.p.build_channel("fts")
        self.assertTrue(self.p.store.fts_search('"ftsofftest"', 3))
        self.assertTrue(self.p.store.verify("hash")["ok"])
        # CLI
        out = run_captured(["build", "fts", "--yes", "--json"], self.s, self.p)
        self.assertEqual(out["code"], 0, out["output"])
        self.assertIn('"channel": "fts"', out["output"])
        out2 = run_captured(["build", "--channels", "vector,graph", "--json"], self.s, self.p)
        self.assertEqual(out2["code"], 0, out2["output"])
        self.assertEqual(run_captured(["build", "vector"], self.s, self.p)["code"], 4)     # rebuild 등급 → --yes 없이 비대화형 거부


class DocExpandTest(_Base):
    def test_doc_expand_adds_same_document_chunks(self):
        self.p.s.toggles.doc_expand = True
        r, t = self.p.query("ISSUE-2001 원인과 수정 CL", log=False)
        st = _st(t)
        self.assertTrue(st["doc_expand"]["enabled"])
        self.assertGreater(st["doc_expand"]["meta"]["added"], 0)
        ex = [h for h in r["hits"] if "doc_expand" in (h.get("why") or [])]
        self.assertTrue(ex)
        self.assertTrue(all(h["in_context"] and h["n"] for h in ex))
        self.assertIn("doc_expand", r)
        self.assertEqual(r["doc_expand"]["added"], st["doc_expand"]["meta"]["added"])
        # 확장 청크는 부모와 같은 문서
        parents = {h["chunk_id"]: h["doc_id"] for h in r["hits"] if not any(w in ("doc_expand", "neighbor") for w in (h.get("why") or []))}
        for h in ex:
            self.assertEqual(parents.get(h.get("parent")), h["doc_id"], h)
        # 상한: 문서당 max_chunks, 상위 top_docs 문서
        per_doc = {}
        for h in ex:
            per_doc[h["doc_id"]] = per_doc.get(h["doc_id"], 0) + 1
        self.assertTrue(all(v <= tn.T.get("doc_expand_max_chunks") for v in per_doc.values()))
        self.assertLessEqual(len(per_doc), tn.T.get("doc_expand_top_docs"))
        # off → 단계 skipped, 확장 없음
        self.p.s.toggles.doc_expand = False
        r2, t2 = self.p.query("ISSUE-2001 원인과 수정 CL", log=False)
        self.assertFalse(_st(t2)["doc_expand"]["enabled"])
        self.assertFalse([h for h in r2["hits"] if "doc_expand" in (h.get("why") or [])])
        # keyword 모드 · min_score 1.0 → 아무것도 추가되지 않음
        self.p.s.toggles.doc_expand = True
        tn.T.set("doc_expand_mode", "keyword")
        tn.T.set("doc_expand_min_score", 1.0)
        r3, t3 = self.p.query("ISSUE-2001 원인과 수정 CL", log=False)
        self.assertEqual(_st(t3)["doc_expand"]["meta"]["added"], 0)
        tn.T.reset("doc_expand_mode")
        tn.T.reset("doc_expand_min_score")
        # 프리셋 speed 는 doc_expand off
        out = run_captured(["query", "ISSUE-2001 원인", "--preset", "speed", "--json", "--no-log"], self.s, self.p)
        j = json.loads(out["output"])
        self.assertFalse(j["result"]["config"]["toggles"]["doc_expand"])


class LlmRetryTest(_Base):
    def _agent(self, name, extra_args, **cfg):
        agents = hl.load_agents()
        a = json.loads(json.dumps(agents["mock"]))
        a["command"] = ["{python}", "-m", "llmwiki.headless", "--mock"] + extra_args
        a.update(cfg)
        agents[name] = a
        hl.save_agents(agents)

    def test_headless_retry_then_success_and_final_failure(self):
        from llmwiki.providers import LLMError, drain_incidents
        state = os.path.join(self.tmp, "fail_state")
        # 처음 2번은 종료 코드 3 → 3번째 성공 (retries=3)
        self._agent("flaky", ["--fail-times", "2", "--state", state], retries=3, retry_backoff_s=0, timeout_s=30)
        llm = hl.HeadlessAgentLLM("flaky", "")
        self.assertEqual(llm.retries, 3)
        r = llm.complete("TASK=answer", "q [C1]")
        self.assertEqual(r["attempts"], 3)
        self.assertEqual(len(r["retry_errors"]), 2)
        self.assertIn("(mock answer)", r["text"])
        self.assertEqual(llm.stats["retries"], 2)
        self.assertFalse(drain_incidents())
        # 항상 실패 (retries=2 → 3회 시도) → LLMError(transient) + incident
        self._agent("dead", ["--fail-times", "99", "--state", state + "2"], retries=2, retry_backoff_s=0, timeout_s=30)
        llm2 = hl.HeadlessAgentLLM("dead", "")
        with self.assertRaises(LLMError) as cm:
            llm2.complete("TASK=answer", "q")
        self.assertTrue(cm.exception.transient)
        self.assertIn("3회 시도", str(cm.exception))
        inc = drain_incidents()
        self.assertEqual(len(inc), 1)
        self.assertEqual((inc[0]["attempts"], inc[0]["role"], inc[0]["provider"]), (3, "default", "headless:dead"))
        # 타임아웃: --sleep 5, timeout_s=1, retries=1 → 2회 시도 후 timeout 실패 (각 1초)
        self._agent("slow", ["--sleep", "5"], retries=1, retry_backoff_s=0, timeout_s=1)
        llm3 = hl.HeadlessAgentLLM("slow", "")
        t0 = time.time()
        with self.assertRaises(LLMError) as cm3:
            llm3.complete("TASK=answer", "q")
        self.assertLess(time.time() - t0, 12)
        self.assertEqual(cm3.exception.kind, "timeout")
        self.assertEqual(drain_incidents()[0]["attempts"], 2)
        # retry_on 에서 exit 를 빼면 재시도하지 않음
        self._agent("noretry", ["--fail-times", "99", "--state", state + "3"], retries=3, retry_backoff_s=0, timeout_s=30, retry_on=["timeout"])
        llm4 = hl.HeadlessAgentLLM("noretry", "")
        with self.assertRaises(LLMError) as cm4:
            llm4.complete("TASK=answer", "q")
        self.assertFalse(cm4.exception.transient)
        self.assertEqual(drain_incidents()[0]["attempts"], 1)

    def test_query_reports_llm_failure_and_falls_back(self):
        state = os.path.join(self.tmp, "fail_state_q")
        self._agent("dead", ["--fail-times", "99", "--state", state], retries=1, retry_backoff_s=0, timeout_s=30)
        self.p.s.llm_roles = {"answer": {"provider": "headless:dead"}}
        self.p.reload()
        r, t = self.p.query("ISSUE-2001 원인과 수정 CL", log=False)
        self.assertEqual(r["answer_mode"], "extractive")
        self.assertTrue(r.get("llm_report"))
        self.assertEqual(r["llm_report"]["failures"][0]["role"], "answer")
        self.assertEqual(r["llm_report"]["failures"][0]["attempts"], 2)
        self.assertIn("LLM 실행 보고", r["answer"])
        self.assertIn("추출식", r["answer"])
        self.assertIn("error", _st(t)["answer_llm"]["meta"])
        # 토글 off → 배너 없음 (보고서는 남음)
        self.p.s.toggles.llm_failure_report = False
        r2, _ = self.p.query("ISSUE-2001 원인과 수정 CL", log=False)
        self.assertNotIn("LLM 실행 보고", r2["answer"])
        self.assertTrue(r2.get("llm_report"))
        self.p.s.toggles.llm_failure_report = True
        self.p.s.llm_roles = {}
        self.p.reload()
        # 빌드(llm_graph) 실패도 alerts/llm_report 로
        self.p.s.llm_roles = {"extract": {"provider": "headless:dead"}}
        self.p.s.toggles.llm_graph = True
        self.p.s.llm_graph_budget = 2
        self.p.reload()
        try:
            res, _ = self.p.build(full=True)
            self.assertTrue(res.get("llm_report"))
            self.assertTrue(any(a["check"] == "llm_failures" for a in res["alerts"]))
        finally:
            self.p.s.toggles.llm_graph = False
            self.p.s.llm_roles = {}
            self.p.reload()

    def test_http_timeout_is_transient_and_retried(self):
        from llmwiki.providers import OpenAICompatLLM, LLMError, drain_incidents
        calls = {"n": 0}

        class Slow(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                calls["n"] += 1
                time.sleep(3)
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                except (ConnectionError, OSError):
                    pass    # 클라이언트가 이미 타임아웃으로 끊음 (정상)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Slow)      # 스레드 서버: 각 시도가 즉시 접수되어 calls 가 정확히 센다
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            llm = OpenAICompatLLM("http://127.0.0.1:%d/v1" % httpd.server_address[1], "m", api_key="k", timeout=1)
            llm.retries, llm.retry_backoff_s = 2, 0.0
            with self.assertRaises(LLMError) as cm:
                llm.complete("s", "u")
            self.assertEqual(cm.exception.kind, "timeout")
            time.sleep(0.5)
            self.assertEqual(calls["n"], 3)
            self.assertEqual(drain_incidents()[0]["attempts"], 3)
        finally:
            httpd.shutdown()

    def test_settings_retry_defaults(self):
        from llmwiki.providers import make_llm
        s = Settings(llm_provider="mock", llm_retries=5, llm_retry_backoff_s=0.5)
        llm = make_llm(s, "answer")
        self.assertEqual((llm.retries, llm.retry_backoff_s), (5, 0.5))
        self._agent("fixed", [], retries=1, retry_backoff_s=0, timeout_s=7)
        s2 = Settings(llm_provider="headless:fixed", llm_retries=5, llm_timeout=600)
        llm2 = make_llm(s2, "answer")
        self.assertEqual((llm2.retries, llm2.timeout), (1, 7))        # agents.json 이 우선


class ForensicExpectTest(_Base):
    def test_expect_cited_and_absent(self):
        from llmwiki import forensic as _fx
        self.p.s.toggles.llm_answer = False          # 추출식 답변 = 원문 문장 → 기대 용어 '8ns' 가 답변에 실제로 들어간다 (mock LLM 은 아님)
        r, _ = self.p.query("HW rev B1 에서 t_setup 은 몇 ns 인가?", log=True)
        rid = r["request_id"]
        self.assertIn("8ns", r["answer"])
        # 기대 문서가 실제로 인용된 경우
        rep = _fx.trace_expectation(self.p, rid, ["HWD-PHY-TIMING-B1"], ["8ns"])
        self.assertTrue(rep["rerun"])
        self.assertTrue(rep["original_hits_known"])
        self.assertTrue(rep["targets"])
        best = next(t for t in rep["targets"] if t["chunk_id"] == rep["best_target"])
        self.assertEqual(best["lost_at"], "none")
        self.assertEqual(best["original"], "cited")
        self.assertTrue(any(j["stage"] == "context" and j["status"] == "hit" for j in best["journey"]))
        self.assertTrue(rep["forensic_id"])
        self.assertEqual(_fx.list_forensics(self.p.store, 1)[0]["origin"], "expectation")
        self.assertIsNone(self.p.store.get_request(rid + 1))          # 재실행은 requests 에 남지 않음
        txt = _fx.format_expectation(rep)
        self.assertIn("정상 인용됨", txt)
        # 무관한 문서를 기대 → 검색 단계 탈락 + 제안(pin) + 재실행 기록 없음
        rep2 = _fx.trace_expectation(self.p, rid, ["ISSUE-2003"], ["1.5dB"], note="TX 문서를 기대", propose=True)
        best2 = next(t for t in rep2["targets"] if t["chunk_id"] == rep2["best_target"])
        self.assertNotEqual(best2["lost_at"], "none")
        self.assertTrue(any(j["stage"] == "fts" and j["status"] == "miss" for j in best2["journey"]))
        self.assertTrue(any(s["kind"] == "pin" for s in rep2["suggestions"]))
        self.assertTrue(rep2["proposals"])
        pid = next(p for p in rep2["proposals"] if self.p.store.get_proposal(p)["kind"] == "pin")
        # pin 제안 적용 → pins.json 에 등록 → 같은 질의에서 문서가 상단에
        from llmwiki import evolve as ev, pins as _pins
        res = ev.apply_proposal(self.p, pid, evaluate=False)
        self.assertEqual(res["status"], "applied", res)
        self.assertTrue(any((p.get("doc") or "") == "ISSUE-2003" for p in _pins.load_pins()))
        r3, _ = self.p.query("HW rev B1 에서 t_setup 은 몇 ns 인가?", log=False)
        self.assertTrue(any("ISSUE-2003" in h["doc_id"] for h in r3["hits"][:4]), [h["doc_id"] for h in r3["hits"][:5]])
        # 코퍼스에 없는 용어 → corpus_gap
        rep3 = _fx.trace_expectation(self.p, rid, [], ["블루투스코덱XYZ"])
        self.assertTrue(any(s["kind"] == "corpus_gap" for s in rep3["suggestions"]))
        # 에피소드에 부정 피드백 기록
        ep = self.p.store.conn.execute("SELECT kind, feedback FROM episodes WHERE kind='expectation' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(ep["kind"], "expectation")
        # CLI
        out = run_captured(["forensic", "expect", str(rid), "--doc", "HWD-PHY-TIMING-B1", "--term", "8ns"], self.s, self.p)
        self.assertEqual(out["code"], 0, out["output"])
        self.assertIn("forensic expect", out["output"])
        out2 = run_captured(["forensic", "expect", "last"], self.s, self.p)
        self.assertEqual(out2["code"], 1)
        # mock LLM 답변("(mock answer) … [C1]")에는 용어가 없으므로 answer 단계 탈락으로 판정되어야 한다
        self.p.s.toggles.llm_answer = True
        r6, _ = self.p.query("HW rev B1 에서 t_setup 은 몇 ns 인가?", log=True)
        rep6 = _fx.trace_expectation(self.p, r6["request_id"], ["HWD-PHY-TIMING-B1"], ["8ns"])
        b6 = next(t for t in rep6["targets"] if t["chunk_id"] == rep6["best_target"])
        self.assertEqual(b6["lost_at"], "answer")
        self.assertTrue(any(s["payload"].get("key") == "answer_length_target" for s in rep6["suggestions"]))
        # 캐시 적중 요청 → 원 요청으로 따라감
        self.p.s.toggles.query_cache = True
        r4, _ = self.p.query("HW rev B1 에서 t_setup 은 몇 ns 인가?", log=True)
        r5, _ = self.p.query("HW rev B1 에서 t_setup 은 몇 ns 인가?", log=True)
        self.assertTrue(r5["cached"])
        rep5 = _fx.trace_expectation(self.p, r5["request_id"], ["HWD-PHY-TIMING-B1"], [])
        self.assertEqual(rep5["request_id"], r4["request_id"])
        self.assertEqual(rep5["followed_from"], [r5["request_id"]])
        self.p.s.toggles.query_cache = False

    def test_proposal_kinds_query_rule_and_tuning(self):
        from llmwiki import evolve as ev
        pid = self.p.store.add_proposal("query_rule", {"type": "synonym", "term": "언더런", "values": ["underrun"]}, "t", 0.6, "expectation")
        self.assertEqual(ev.apply_proposal(self.p, pid, evaluate=False)["status"], "applied")
        self.assertTrue(any(f["type"] == "synonym" for f in qr.expand("언더런 원인", 0.8, 0.4, True)["fired"]))
        pid2 = self.p.store.add_proposal("tuning", {"key": "doc_expand_max_chunks", "value": 5}, "t", 0.6, "expectation")
        self.assertEqual(ev.apply_proposal(self.p, pid2, evaluate=False)["status"], "applied")
        self.assertEqual(tn.T.get("doc_expand_max_chunks"), 5)
        tn.T.reset("doc_expand_max_chunks")
        pid3 = self.p.store.add_proposal("corpus_gap", {"topic": "x"}, "t", 0.6, "expectation")
        self.assertEqual(ev.apply_proposal(self.p, pid3, evaluate=False)["status"], "failed")


class McpHttpTest(_Base):
    def _serve(self, mode_on=True, anonymous="viewer", mcp_only=False):
        from llmwiki.web import server as ws
        cfg = json.loads(json.dumps(A.DEFAULT_SECURITY))
        cfg["mode"] = "on" if mode_on else "off"
        cfg["anonymous_role"] = anonymous
        A.save_security(cfg)
        auth = A.Auth(self.s, host="0.0.0.0")
        auth.add_user("admin1", "admin-pass-1", "admin")
        self.key = auth.add_api_key("test-client", "viewer")
        ws.Handler.pipe = self.p
        ws.Handler.auth = auth
        ws.Handler.host = "0.0.0.0"
        ws.Handler.mcp_only = mcp_only
        self.ws = ws
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ws.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d/mcp" % self.port

    def tearDown(self):
        try:
            self.httpd.shutdown()
            self.ws.Handler.auth = None
            self.ws.Handler.host = "127.0.0.1"
            self.ws.Handler.mcp_only = False
        except Exception:
            pass
        _Base.tearDown(self)

    def _post(self, url, msg, token=None, session=None):
        from llmwiki.mcp import http_post_mcp
        return http_post_mcp(url, msg, token or "", session, 60)

    def test_streamable_http_with_api_key_and_anonymous(self):
        url = self._serve(mode_on=True, anonymous="")     # 로그인 필수 → 토큰 없으면 401
        st, h, body = self._post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}})
        self.assertEqual(st, 401, body)
        self.assertIn("Bearer", h.get("WWW-Authenticate", ""))
        st, h, body = self._post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, token="lwk_bad_x")
        self.assertEqual(st, 401)
        tok = self.key["token"]
        st, h, body = self._post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, token=tok)
        self.assertEqual(st, 200, body)
        j = json.loads(body.decode("utf-8"))
        self.assertEqual(j["result"]["serverInfo"]["name"], "llmwiki")
        sid = h.get("Mcp-Session-Id")
        self.assertTrue(sid)
        st, _, body = self._post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, token=tok, session=sid)
        self.assertEqual(st, 202)
        st, _, body = self._post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token=tok, session=sid)
        names = [t["name"] for t in json.loads(body)["result"]["tools"]]
        self.assertIn("wiki_forensic", names)
        self.assertIn("wiki_feedback", names)
        st, _, body = self._post(url, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "wiki_query", "arguments": {"question": "ISSUE-2001 원인", "mode": "fast"}}}, token=tok, session=sid)
        txt = json.loads(body)["result"]["content"][0]["text"]
        self.assertIn("request_id", txt)
        rid = int(txt.rsplit("request_id: ", 1)[1].split(" ")[0])
        st, _, body = self._post(url, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "wiki_forensic", "arguments": {"request_id": rid, "expected_docs": ["ISSUE-2001"], "expected_terms": ["FIFO"]}}}, token=tok, session=sid)
        res = json.loads(body)["result"]
        self.assertIn("forensic expect", res["content"][0]["text"])
        self.assertIn("summary", res["structuredContent"])
        # 배열(batch) 요청
        st, _, body = self._post(url, [{"jsonrpc": "2.0", "id": 5, "method": "ping"}, {"jsonrpc": "2.0", "id": 6, "method": "tools/list"}], token=tok, session=sid)
        arr = json.loads(body)
        self.assertEqual([x["id"] for x in arr], [5, 6])
        # 세션 쿠키로도 가능 (브라우저/스크립트)
        req = urllib.request.Request("http://127.0.0.1:%d/api/auth/login" % self.port, data=json.dumps({"username": "admin1", "password": "admin-pass-1"}).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "X-Requested-With": "llmwiki"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            cookie = r.headers.get("Set-Cookie").split(";")[0]
        req2 = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}).encode("utf-8"),
                                      headers={"Content-Type": "application/json", "Cookie": cookie}, method="POST")
        with urllib.request.urlopen(req2, timeout=30) as r:
            self.assertEqual(json.loads(r.read())["id"], 7)
        # GET → 405, DELETE → 200
        req3 = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok}, method="GET")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req3, timeout=10)
        self.assertEqual(cm.exception.code, 405)
        req4 = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok}, method="DELETE")
        with urllib.request.urlopen(req4, timeout=10) as r:
            self.assertEqual(r.status, 200)
        # 감사 로그에 거부 기록
        rows = A.Auth.audit_tail(50)
        self.assertTrue(any(r["op"] == "mcp" and r["ok"] is False for r in rows))
        # 브리지: stdin JSON-RPC → HTTP → stdout
        from llmwiki.mcp import bridge_stdio_to_http
        import io
        inp = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n" + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
                          + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "wiki_status", "arguments": {}}}) + "\n")
        out = io.StringIO()
        code = bridge_stdio_to_http(url, tok, timeout=60, inp=inp, out=out)
        self.assertEqual(code, 0)
        lines = [json.loads(x) for x in out.getvalue().strip().splitlines()]
        self.assertEqual([x["id"] for x in lines], [1, 2])
        self.assertIn("stats", lines[1]["result"]["content"][0]["text"])
        # 잘못된 토큰으로 브리지 → JSON-RPC 오류
        inp2 = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n")
        out2 = io.StringIO()
        bridge_stdio_to_http(url, "lwk_bad_x", timeout=30, inp=inp2, out=out2)
        self.assertIn("error", json.loads(out2.getvalue().strip()))

    def test_anonymous_viewer_can_use_mcp_without_token(self):
        url = self._serve(mode_on=True, anonymous="viewer", mcp_only=True)
        st, h, body = self._post(url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(st, 200, body)
        self.assertTrue(json.loads(body)["result"]["tools"])
        # mcp_only 서버는 /api/status 를 주지 않는다
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen("http://127.0.0.1:%d/api/status" % self.port, timeout=10)
        self.assertEqual(cm.exception.code, 404)
        # 게스트 역할을 올린 API 키는 그 역할로 동작 (class3 → run 등급 도구도 서버 API 로 가능)
        st, h, body = self._post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "wiki_status", "arguments": {}}}, token=self.key["token"])
        self.assertEqual(st, 200)
        snippets = __import__("llmwiki.mcp", fromlist=["client_config_snippets"]).client_config_snippets("http://127.0.0.1:%d" % self.port, "tok")
        self.assertIn("/mcp", snippets["http_json"]["mcpServers"]["llmwiki"]["url"])


if __name__ == "__main__":
    unittest.main()
