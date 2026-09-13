# -*- coding: utf-8 -*-
"""Phase 3~5: 시간 파싱 · 규칙 확장/exclude · 문서유형/시간 부스트 · pin · fusion 방식 · precompute/doc_vector ·
근거 판정/fallback/insufficient · claim 검증/정책 · 포렌식 · 메모리(에피소드·피드백·decay·consolidate) · CLI."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402
from llmwiki import timeparse as tp  # noqa: E402
from llmwiki import tuning as tn  # noqa: E402
from llmwiki import schema as sc  # noqa: E402
from llmwiki import query_rules as qr  # noqa: E402
from llmwiki import textutil as tu  # noqa: E402
from llmwiki.providers import BaseLLM  # noqa: E402
from llmwiki.answer import check_claims, split_claims, apply_claim_policy  # noqa: E402
from llmwiki.cli import run_captured  # noqa: E402


def _gen_corpus(out: str) -> None:
    spec = importlib.util.spec_from_file_location("mk", os.path.join(ROOT, "setup", "make_sample_corpus_modem.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    mod.gen(out, 1)


class FixedLLM(BaseLLM):
    name = "fixed"
    available = True
    model = "fixed"

    def __init__(self, text: str):
        BaseLLM.__init__(self)
        self.text = text

    def ping(self):
        return {"ok": True, "ms": 0, "detail": "fixed"}

    def _complete(self, system, user, max_tokens, effort, json_mode):
        if "TASK=rerank" in system:
            import re
            ids = re.findall(r"\[(\d+)\]", user)
            return {"text": json.dumps({"ranking": [int(i) for i in dict.fromkeys(ids)]}), "usage": {"input_tokens": 10, "output_tokens": 5}, "ms": 1, "model": "fixed"}
        return {"text": self.text, "usage": {"input_tokens": len(user) // 3, "output_tokens": len(self.text) // 3}, "ms": 1, "model": "fixed"}


def _st(trace):
    return {c["name"]: c for c in trace["children"]}


class Phase35Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.corpus = os.path.join(self.tmp, "corpus")
        _gen_corpus(self.corpus)
        for k in ("LOGS_DIR", "SCHEMAS_DIR", "QUERY_RULES", "MCP_SOURCES", "PROMPTS_DIR", "PINS", "PRESETS"):
            os.environ["LLMWIKI_%s_PATH" % k] = os.path.join(self.tmp, k.lower())
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        s = Settings(corpus_dirs=[self.corpus], data_dir=os.path.join(self.tmp, "data"), wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=256)
        s.toggles = Toggles(query_cache=False, health_check=False)
        self.s = s
        self.p = Pipeline(s)
        self.p.build(full=True)

    def tearDown(self):
        self.p.store.close()
        for k in ("LOGS_DIR", "SCHEMAS_DIR", "QUERY_RULES", "MCP_SOURCES", "PROMPTS_DIR", "PINS", "PRESETS"):
            os.environ.pop("LLMWIKI_%s_PATH" % k, None)
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        tu.set_compounds({})
        tn.load_tuning(os.path.join(self.tmp, "none.json"))
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 시간 파싱 ----
    def test_timeparse(self):
        now = datetime(2026, 9, 13, 10, 0)   # 일요일
        cases = {
            "어제 발생한 이슈": ("2026-09-12", "2026-09-12"), "오늘 리뷰": ("2026-09-13", "2026-09-13"), "그저께": ("2026-09-11", "2026-09-11"),
            "지난주 리뷰한 CL": ("2026-08-31", "2026-09-06"), "이번주 이슈": ("2026-09-07", "2026-09-13"), "다음주 계획": ("2026-09-14", "2026-09-20"),
            "지난달 보고": ("2026-08-01", "2026-08-31"), "이번달": ("2026-09-01", "2026-09-30"), "작년 이슈": ("2025-01-01", "2025-12-31"),
            "3일전 빌드": ("2026-09-10", "2026-09-10"), "2주 전": ("2026-08-24", "2026-08-30"), "최근 7일 이슈": ("2026-09-06", "2026-09-13"),
            "최근 한 달": ("2026-08-14", "2026-09-13"), "2026년 8월 21일 회의": ("2026-08-21", "2026-08-21"), "2026-08 주간 보고": ("2026-08-01", "2026-08-31"),
            "2026년 3분기 실적": ("2026-07-01", "2026-09-30"), "Q2 계획": ("2026-04-01", "2026-06-30"), "상반기 이슈": ("2026-01-01", "2026-06-30"),
            "8월 21일 회의": ("2026-08-21", "2026-08-21"), "2026.07.03 릴리스": ("2026-07-03", "2026-07-03"),
        }
        for q, (f, t) in cases.items():
            r = tp.parse(q, "Asia/Seoul", "mon", now)
            self.assertIsNotNone(r, q)
            self.assertEqual((r["from"], r["to"]), (f, t), q)
            self.assertTrue(r["query"] == q == r["expr"] or r["expr"] not in r["query"], q)
        self.assertIsNone(tp.parse("RX DMA underrun 원인", now=now))
        r = tp.parse("지난주 리뷰한 CL", "Asia/Seoul", "sun", now)     # 일요일 시작: 9/13(일) 은 이번주 첫날 → 지난주 = 9/6~9/12
        self.assertEqual((r["from"], r["to"]), ("2026-09-06", "2026-09-12"))
        out = run_captured(["time", "지난주 CL"], self.s, self.p)
        self.assertIn("\"expr\": \"지난주\"", out["output"])

    # ---- 규칙 확장 · exclude · 유형/시간 부스트 · pin ----
    def test_rules_boosts_pins(self):
        r, t = self.p.query("Physical Downlink Control Channel 디코더 문제", log=False)
        st = _st(t)
        self.assertGreater(st["query_rules"]["meta"]["fired"], 0)
        self.assertIn("acronym", st["query_rules"]["meta"]["types"])
        self.assertTrue(any("ISSUE-2005" in h["doc_id"] for h in r["hits"][:3]), [h["doc_id"] for h in r["hits"][:5]])
        self.assertTrue(st["rrf_fuse"]["meta"]["sources"].get("fts_alt1"))
        self.assertIn("fts_rule", st["rrf_fuse"]["meta"]["sources"])
        # exclude: '시뮬레이터' 언급 청크에 페널티
        r2, t2 = self.p.query("RX DMA underrun 검증 시뮬레이터 제외", log=False)
        st2 = _st(t2)
        self.assertIn("시뮬레이터", st2["query_rules"]["meta"]["exclude"])
        self.assertIn("시뮬레이터", st2["boost"]["meta"]["exclude"])
        self.assertTrue(all("시뮬레이터" not in h["text"] for h in r2["hits"] if not h["boosts"].get("exclude")))
        # 문서유형 힌트 + 시간 범위 부스트
        r3, t3 = self.p.query("2026년 8월 주간 보고 이슈 요약", log=False)
        st3 = _st(t3)
        self.assertEqual(st3["time_scope"]["meta"]["range"], ["2026-08-01", "2026-08-31"])
        self.assertIn("weekly_report", r3["plan"]["doc_types"])
        self.assertTrue(any(h["boosts"].get("time") for h in r3["hits"]))
        self.assertTrue(any(h["boosts"].get("router_type") for h in r3["hits"]))
        self.assertTrue(all(h["doc_type"] for h in r3["hits"] if h["doc_type"] is not None))
        # filter 모드: 범위 밖 제거
        tn.T.set("time_mode", "filter")
        r4, t4 = self.p.query("2026년 8월 주간 보고", log=False)
        self.assertTrue(all((h["date"] or "").startswith("2026-08") for h in r4["hits"] if h["in_context"]), [h["date"] for h in r4["hits"]])
        tn.T.reset("time_mode")
        # pin
        from llmwiki import pins as _pins
        pin = _pins.add_pin(doc="RULE-ISR-001", keywords_=["코드 리뷰"], note="리뷰 시 항상")
        r5, t5 = self.p.query("코드 리뷰 시 주의할 인터럽트 처리", log=False)
        st5 = _st(t5)
        self.assertEqual(st5["pins"]["meta"]["matched"], 1)
        self.assertTrue(any("RULE-ISR-001" in h["doc_id"] and h["boosts"].get("pin") for h in r5["hits"][:2]), [(h["doc_id"], h["boosts"]) for h in r5["hits"][:3]])
        self.assertEqual(r5["plan"]["pins"][0]["id"], pin["id"])
        self.assertTrue(_pins.load_pins()[0]["hits"] >= 1)
        out = run_captured(["pin", "list"], self.s, self.p)
        self.assertIn("RULE-ISR-001", out["output"])
        self.assertTrue(_pins.remove_pin(pin["id"]))
        # profile_expansion
        self.p.s.toggles.profile_expansion = True
        r6, t6 = self.p.query("PDCCH 디코딩 재시작", log=False)
        self.assertIn("expansion_profile", _st(t6))
        self.p.s.toggles.profile_expansion = False
        out2 = run_captured(["rules", "test", "AGC 재시작 오류"], self.s, self.p)
        self.assertIn("acronym", out2["output"])

    # ---- fusion 방식 · precompute · doc_vector ----
    def test_fusion_precompute_docvector(self):
        from llmwiki import fusion as _fusion
        for m in ("rrf", "weighted", "zscore", "dbsf", "rrf_boost"):
            tn.T.set("fusion_method", m)
            r, t = self.p.query("ISSUE-2003 TX 전력 낮음 원인", log=False)
            self.assertEqual(_st(t)["rrf_fuse"]["meta"]["method"], m)
            self.assertTrue(any("ISSUE-2003" in h["doc_id"] for h in r["hits"][:3]), m)
        tn.T.reset("fusion_method")
        qs = json.load(open(os.path.join(self.corpus, "questions.json"), encoding="utf-8"))[:6]
        rows = _fusion.compare_methods(self.p, qs, ["rrf", "zscore"], k=5)
        self.assertEqual([r["method"] for r in rows], ["rrf", "zscore"])
        self.assertTrue(all("hit@k" in r for r in rows))
        out = run_captured(["fusion", "compare", "--methods", "rrf,dbsf", "--questions", os.path.join(self.corpus, "questions.json"), "--k", "5"], self.s, self.p)
        self.assertIn("dbsf", out["output"])
        # precompute
        from llmwiki import precompute as _pc
        self.p.s.toggles.precompute = True
        r1, t1 = self.p.query("HW rev B1 t_setup", log=False)
        self.assertFalse(r1.get("precomputed"))
        r2, t2 = self.p.query("HW rev B1 t_setup", log=False)
        self.assertTrue(r2["precomputed"])
        self.assertIn("precompute_hit", _st(t2))
        self.assertEqual(t2["summary"]["llm"]["calls"], 0)
        pr = _pc.run(self.p, questions=["ISR blocking 대기 규칙", "HW rev B1 t_setup"])
        self.assertEqual(pr["computed"], 1)
        self.assertEqual(pr["skipped_cached"], 1)
        self.assertGreaterEqual(_pc.cache_status(self.p.store)["entries"], 2)
        self.p.build(full=True)                      # 재빌드 → 무효화
        r3, t3 = self.p.query("HW rev B1 t_setup", log=False)
        self.assertFalse(r3.get("precomputed"))
        self.assertEqual(_pc.clear_cache(self.p.store, stale_only=True) >= 1, True)
        self.p.s.toggles.precompute = False
        out = run_captured(["precompute", "status"], self.s, self.p)
        self.assertIn("entries", out["output"])
        # doc_vector 채널
        self.p.s.toggles.doc_vector = True
        res, tr = self.p.build(full=True)
        self.assertGreater(res["doc_vectors"], 30)
        r4, t4 = self.p.query("PHY 컨트롤러 드라이버 설계 구조", log=False)
        st4 = _st(t4)
        self.assertGreater(st4["doc_vector_search"]["meta"]["hits"], 0)
        self.assertIn("doc_vector", st4["rrf_fuse"]["meta"]["sources"])
        self.p.s.toggles.doc_vector = False

    # ---- 근거 판정 · fallback · insufficient · 포렌식 ----
    def test_evidence_fallback_forensic(self):
        from llmwiki import forensic as _fx
        r, t = self.p.query("ISSUE-2001 원인과 수정 CL", log=False)
        st = _st(t)
        self.assertIn(st["evidence_check"]["meta"]["verdict"], ("sufficient", "weak"))
        self.assertEqual(r["answer_mode"], "llm")
        # 코퍼스에 없는 주제 → insufficient → LLM 답변 생략 · 포렌식 자동 기록
        r2, t2 = self.p.query("블루투스 오디오 코덱 aptX 지연 튜닝 방법", log=True)
        st2 = _st(t2)
        self.assertEqual(r2["evidence"]["verdict"], "insufficient")
        self.assertEqual(r2["answer_mode"], "insufficient")
        self.assertIn("근거 부족", r2["answer"])
        self.assertFalse(st2["answer_llm"]["enabled"])
        self.assertIn("forensic", r2)
        fx = _fx.get_forensic(self.p.store, r2["request_id"])
        self.assertTrue(fx and fx["findings"])
        self.assertTrue(any(s["kind"] == "corpus_gap" for s in fx["suggestions"]))
        # fallback 루프: 예산 내에서 단계 시도, 라운드 기록
        self.p.s.toggles.fallback_loop = True
        tn.T.set("fallback_max_attempts", 2)
        r3, t3 = self.p.query("블루투스 오디오 코덱 aptX 지연", log=False)
        fb = [c for c in t3["children"] if c["name"] == "fallback"]
        self.assertEqual(len(fb), 2)
        self.assertEqual([c["meta"]["level"] for c in fb], ["rules", "expand"])   # mock LLM 이 있으므로 expand 단계 포함
        self.assertEqual(len(r3["fallback"]), 2)
        self.assertTrue(any(cc["name"] == "query_expand" and cc["meta"].get("reason") == "fallback" for c in fb for cc in c.get("children", [])))
        self.assertEqual(r3["answer_mode"], "insufficient")
        tn.T.reset("fallback_max_attempts")
        self.p.s.toggles.fallback_loop = False
        self.p.s.toggles.evidence_check = False
        r4, t4 = self.p.query("블루투스 오디오 코덱", log=False)
        self.assertEqual(r4["answer_mode"], "llm")
        self.assertFalse(_st(t4)["evidence_check"]["enabled"])
        self.p.s.toggles.evidence_check = True
        out = run_captured(["forensic", "last"], self.s, self.p)
        self.assertIn("findings", out["output"])
        out2 = run_captured(["forensic", "summary"], self.s, self.p)
        self.assertIn("by_verdict", out2["output"])
        out3 = run_captured(["forensic", str(r["request_id"])], self.s, self.p)
        self.assertEqual(out3["code"], 0)

    # ---- claim 검증 ----
    def test_claim_check_policy_and_units(self):
        chunks = {"a#0": {"heading": "ISSUE-2001", "text": "RX DMA underrun 발생 시 PHY 재시작 실패. 원인은 FIFO 임계값 설정 오류 (0x20 → 0x40 필요). CL-55301 로 수정."}}
        cites = [{"n": 1, "chunk_id": "a#0"}]
        ans = "ISSUE-2001 의 원인은 FIFO 임계값 설정 오류이다 [C1]. 수정 CL 은 CL-99999 이며 2027년 3월에 반영되었다 [C1]. 담당 엔지니어는 5명이다.\n\n## 근거 표\n| 근거 | 문서 |\n|---|---|\n| C1 | a |"
        ci = check_claims(ans, cites, chunks, 0.5)
        v = {c["i"]: c["verdict"] for c in ci["claims"]}
        self.assertEqual(v[0], "supported")
        self.assertEqual(v[1], "unsupported")
        self.assertIn(v[2], ("unsupported", "uncited"))
        self.assertLess(ci["groundedness"], 0.7)
        self.assertEqual(ci["n_factual"], 3)
        marked, n = apply_claim_policy(ans, ci["claims"], "mark")
        self.assertIn("[미확인", marked)
        dropped, n2 = apply_claim_policy(ans, ci["claims"], "drop")
        self.assertNotIn("CL-99999", dropped)
        self.assertEqual(len(split_claims("## 근거 표\n| a | b |\n")), 0)
        # 파이프라인: 고정 답변 LLM → claim_check 단계, 정책 mark, groundedness 경고, 포렌식
        bad = "ISSUE-2001 의 원인은 FIFO 임계값 설정 오류이다 [C1]. 수정 CL 은 CL-99999 이며 2027년 3월에 반영되었다 [C1]."
        self.p._llms["answer"] = FixedLLM(bad)
        self.p._llms["rerank"] = FixedLLM("")
        r, t = self.p.query("ISSUE-2001 원인과 수정 CL", log=False)
        st = _st(t)
        self.assertTrue(st["claim_check"]["enabled"])
        self.assertGreaterEqual(st["claim_check"]["meta"]["unsupported"], 1)
        self.assertIn("[미확인", r["answer"])
        self.assertIsNotNone(r["groundedness"])
        self.assertIn("citation_precision", r["claims"])
        tn.T.set("claim_policy", "drop")
        r2, _ = self.p.query("ISSUE-2001 원인과 수정 CL", log=False)
        self.assertNotIn("CL-99999", r2["answer"])
        tn.T.reset("claim_policy")
        self.p.s.toggles.claim_check = False
        _, t3 = self.p.query("ISSUE-2001 원인과 수정 CL", log=False)
        self.assertFalse(_st(t3)["claim_check"]["enabled"])
        self.p.s.toggles.claim_check = True
        self.p._llms.pop("answer", None)
        self.p._llms.pop("rerank", None)

    # ---- 메모리: 에피소드 · 피드백 부스트 · decay · consolidate ----
    def test_memory(self):
        from llmwiki import memory as _mem
        from llmwiki import evolve as ev
        from llmwiki import forensic as _fx
        r, t = self.p.query("AGC 수렴 지연 원인", log=True)
        eps = _mem.episodes(self.p.store, 5)
        self.assertTrue(eps and eps[0]["query"] == "AGC 수렴 지연 원인")
        ev.record_feedback(self.p, r["query_id"], +1)
        fw = _mem.feedback_weights(self.p.store, 60)
        self.assertTrue(fw)
        top_chunk = r["hits"][0]["chunk_id"]
        self.assertGreater(fw.get(top_chunk, 0), 0)
        self.p.s.toggles.feedback_boost = True
        r2, t2 = self.p.query("AGC 수렴 지연 원인", log=False)
        self.assertTrue(any(h["boosts"].get("feedback") for h in r2["hits"]))
        self.p.s.toggles.feedback_boost = False
        # decay: 오래된 제안 archive
        pid = self.p.store.add_proposal("synonym", {"term": "x", "expansion": "y"}, "old", 0.3, "capture")
        self.p.store.conn.execute("UPDATE proposals SET last_reinforced=?, ts=? WHERE id=?", (1.0, 1.0, pid))
        self.p.store.conn.commit()
        d = _mem.decay(self.p.store, 60, 0.2)
        self.assertGreaterEqual(d["proposals_archived"], 1)
        self.assertEqual(self.p.store.get_proposal(pid)["status"], "archived")
        # consolidate: 같은 주제 포렌식 3건 → corpus_gap 제안
        diag = {"findings": [], "suggestions": [{"kind": "corpus_gap", "detail": "블루투스 문서 없음", "confidence": 0.6, "payload": {"topic": "블루투스 코덱"}}], "topics": ["블루투스"], "severity": "warn"}
        for i in range(3):
            _fx.record(self.p.store, None, "", "블루투스 코덱 %d" % i, "insufficient", None, diag, "auto")
        c = _mem.consolidate(self.p.store, 3)
        self.assertTrue(c["proposals"])
        prop = self.p.store.get_proposal(c["proposals"][0])
        self.assertEqual(prop["kind"], "corpus_gap")
        self.assertEqual(prop["origin"], "forensics")
        st = _mem.status(self.p.store, 60)
        self.assertGreaterEqual(st["episodes"], 1)
        out = run_captured(["memory", "status"], self.s, self.p)
        self.assertIn("episodes", out["output"])


if __name__ == "__main__":
    unittest.main()
