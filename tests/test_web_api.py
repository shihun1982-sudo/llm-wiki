# -*- coding: utf-8 -*-
"""Phase 7: Web API 회귀 — 서버를 스레드로 띄우고 v3 엔드포인트를 왕복한다 (정적 파일·상태·질의(preset/mode)·빌드 job·임베딩 리포트·
lint·규칙·pin·프리셋·프롬프트·로그·포렌식·trial·메모리·precompute·agents·테마·시간·MCP 소스·유효 설정·fusion 비교)."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles  # noqa: E402
from llmwiki.pipeline import Pipeline  # noqa: E402
from llmwiki import schema as sc  # noqa: E402
from llmwiki import query_rules as qr  # noqa: E402
from llmwiki import tuning as tn  # noqa: E402
from llmwiki.web import server as ws  # noqa: E402


def _gen_corpus(out: str) -> None:
    spec = importlib.util.spec_from_file_location("mk", os.path.join(ROOT, "setup", "make_sample_corpus_modem.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    mod.gen(out, 1)


class WebApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.corpus = os.path.join(cls.tmp, "corpus")
        _gen_corpus(cls.corpus)
        for k in ("LOGS_DIR", "SCHEMAS_DIR", "QUERY_RULES", "MCP_SOURCES", "PROMPTS_DIR", "PINS", "PRESETS", "AGENTS", "RULES"):
            os.environ["LLMWIKI_%s_PATH" % k] = os.path.join(cls.tmp, k.lower() + (".json" if k == "RULES" else ""))
        # 설정 저장 API 가 프로젝트의 config.json / tuning.json 을 건드리지 않도록 격리.
        # **환경변수로** 한다 — 모듈 상수(`config.CONFIG_PATH`)를 바꾸는 것은 지원되는 격리 수단이 아니라서,
        # save/load 가 `path_for("config")` 를 쓰도록 바뀐 2026-09-19 에 격리가 조용히 풀렸다.
        from llmwiki import config as _cfg
        cls._cfg_path, cls._tun_path = _cfg.CONFIG_PATH, tn.TUNING_PATH
        os.environ["LLMWIKI_CONFIG_PATH"] = os.path.join(cls.tmp, "config.json")
        os.environ["LLMWIKI_TUNING_PATH"] = os.path.join(cls.tmp, "tuning.json")
        _cfg.CONFIG_PATH = os.path.join(cls.tmp, "config.json")
        tn.TUNING_PATH = os.path.join(cls.tmp, "tuning.json")
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        s = Settings(corpus_dirs=[cls.corpus], data_dir=os.path.join(cls.tmp, "data"), wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=256)
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
        from llmwiki import config as _cfg
        _cfg.CONFIG_PATH, tn.TUNING_PATH = cls._cfg_path, cls._tun_path
        for k in ("LOGS_DIR", "SCHEMAS_DIR", "QUERY_RULES", "MCP_SOURCES", "PROMPTS_DIR", "PINS", "PRESETS", "AGENTS",
                  "RULES", "CONFIG", "TUNING"):
            os.environ.pop("LLMWIKI_%s_PATH" % k, None)
        sc._CACHE["mtime"] = None
        qr._CACHE["mtime"] = None
        tn.load_tuning(os.path.join(cls.tmp, "none.json"))
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _get(self, path, raw=False):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=60) as r:
            data = r.read()
            return data if raw else json.loads(data.decode("utf-8"))

    def _post(self, path, body):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))

    def _job(self, jid, timeout=120):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self._get("/api/jobs/" + jid)
            if j["status"] != "running":
                return j
            time.sleep(0.2)
        raise AssertionError("job timeout")

    def test_static_and_status(self):
        html = self._get("/", raw=True).decode("utf-8")
        self.assertIn('data-group="ask"', html)
        for f in ("js/core.js", "js/ask.js", "js/corpus.js", "js/knowledge.js", "js/quality.js", "js/evolve.js", "js/settings.js", "js/observability.js", "style.css", "themes/dark.css"):
            self.assertTrue(len(self._get("/static/" + f, raw=True)) > 100, f)
        st = self._get("/api/status")
        self.assertIn("toggle_groups", st)
        self.assertIn("presets", st)
        self.assertIn("quality", st["presets"])
        self.assertIn("doc_types", st)
        names = {t for g in st["toggle_groups"] for t in g["toggles"]}
        self.assertTrue(names <= set(st["toggle_names"]))
        self.assertIn("fallback_loop", names)
        th = self._get("/api/themes")
        self.assertTrue(any(t["key"] == "dark" for t in th["themes"]))
        eff = self._get("/api/config/effective")
        self.assertTrue(any(r["key"] == "toggles.claim_check" for r in eff))
        h = self._get("/api/health?quick=1")
        self.assertIn("checks", h)

    def test_query_with_preset_and_mode(self):
        j = self._post("/api/query", {"q": "ISSUE-2001 원인과 수정 CL", "overrides": {}, "preset": "token", "mode": "fast"})
        r = j["result"]
        self.assertIn("plan", r)
        self.assertIn("evidence", r)
        self.assertEqual(r["config"]["toggles"]["rerank_llm"], False)     # token/speed 프리셋 적용
        self.assertIn("--preset", r["cli"])
        self.assertTrue(self.p.s.toggles.rerank_llm)                      # 요청 후 복원
        self.assertTrue(any(h.get("doc_type") for h in r["hits"]))
        rid = r["request_id"]
        f = self._get("/api/forensic?request_id=%d&rerun=1" % rid)
        self.assertIn("findings", f)
        # 포렌식 목록: **기본은 문제 건만** (2026-09-20). 방금 질의는 근거가 충분했으므로 기본 보기에는
        # 나오지 않고 `only=all` 에서만 보인다. 예전 줄은 `… or True` 라 사실상 아무것도 검사하지 않았다.
        all_fx = self._get("/api/forensics?only=all&limit=50")
        self.assertTrue([x for x in all_fx if x["request_id"] == rid], "방금 진단한 기록이 목록에 없다")
        self.assertFalse([x for x in self._get("/api/forensics") if x.get("verdict") == "sufficient"],
                         "기본 보기(문제 건만)에 정상 건이 섞였다 — 화면이 정상 건으로 덮인다")
        logs = self._get("/api/logs?request=%d" % rid)
        self.assertTrue(any((x.get("data") or {}).get("stage") == "fts_search" for x in logs["rows"]))
        self.assertIn("files", self._get("/api/logs/files"))
        # 채널 검색 (2026-09-19): 여러 채널을 조합할 수 있게 되면서 응답이
        # {channels, mode, per_channel, rows[…채널별 순위…], graph?, counts} 로 바뀌었다.
        # 그래프 채널만의 정보(시드·엔티티·관계·provenance)는 result.graph 아래에 있다.
        s2 = self._post("/api/search", {"q": "AGC 수렴", "channel": "graph", "k": 5, "overrides": {}})
        self.assertEqual(s2["result"]["channels"], ["graph"])
        self.assertIn("provenance", s2["result"]["graph"])
        self.assertTrue(s2["result"]["rows"], "그래프 채널 결과가 비어 있다")
        self.assertIn("graph", s2["result"]["rows"][0]["channels"], "행에 어느 채널이 찾았는지가 없다")
        # 조합: 두 채널을 AND 로 — 교집합만 나와야 한다
        s3 = self._post("/api/search", {"q": "AGC 수렴", "channels": ["fts", "graph"], "mode": "and", "k": 5, "overrides": {}})
        self.assertEqual(s3["result"]["mode"], "and")
        self.assertTrue(all(r["n_channels"] == 2 for r in s3["result"]["rows"]), s3["result"]["rows"])
        # 복합 조건: (fts) 그리고 (graph 필수)
        s4 = self._post("/api/search", {"q": "AGC 수렴", "channels": ["fts"], "require": ["graph"], "k": 5, "overrides": {}})
        self.assertEqual(s4["result"]["mode"], "composite")
        self.assertTrue(all("graph" in r["channels"] for r in s4["result"]["rows"]), s4["result"]["rows"])
        # 질의 해부 (LLM 없음)
        d = self._post("/api/debug/query", {"q": "지난주 AGC 수렴", "overrides": {}})
        self.assertIn("tokens", d)
        self.assertIn("weights", d["router"])
        t = self._get("/api/time?q=" + urllib.request.quote("지난주 CL"))
        self.assertEqual(t["expr"], "지난주")

    def test_build_job_and_corpus_endpoints(self):
        j = self._post("/api/build", {"full": False, "overrides": {}})
        job = self._job(j["job"])
        self.assertEqual(job["status"], "done")
        self.assertIn("alerts", job["result"]["result"])
        bs = self._get("/api/build/status")
        self.assertFalse(bs["running"])
        self.assertIn("lint_summary", bs)
        er = self._get("/api/embed/report")
        self.assertEqual(er["coverage"]["coverage"], 1.0)
        v = self._get("/api/build/verify")
        self.assertTrue(v["ok"])
        v2 = self._post("/api/build/verify", {"fix": True})
        self.assertIn("fixed", v2)
        lint = self._get("/api/corpus/lint")
        self.assertIn("summary", lint)
        self.assertTrue(any(r["doc_id"].endswith("meeting_2026-09-02.md") for r in lint["rows"]))
        types = self._get("/api/corpus/types")
        self.assertIn("issue", types["schemas"])
        self.assertIn("ISSUE-", types["examples"]["issue"])
        docs = self._get("/api/docs")
        self.assertTrue(any(d.get("ext_id") == "CL-55301" for d in docs))
        src = self._get("/api/mcp_sources")
        self.assertIn("mango", src["sources"])
        self.assertEqual(self._post("/api/mcp_sources", {"action": "test", "names": ["mock"]})[0]["ok"], True)

    def test_settings_endpoints(self):
        pr = self._get("/api/presets")
        self.assertIn("speed", pr["presets"])
        d = self._get("/api/presets/diff?name=speed")
        self.assertTrue(any(x["changes"] for x in d))
        ap = self._post("/api/presets", {"action": "apply", "names": "token", "save": False})
        self.assertIn("settings", ap)
        # save=false = 미리보기: 응답에는 프리셋이 적용된 값이 오지만 서버 전역 설정은 그대로 (다중 사용자 격리, 2026-09-15)
        self.assertTrue(ap.get("preview"))
        self.assertFalse(ap["settings"]["toggles"]["rerank_llm"])
        self.assertTrue(self.p.s.toggles.rerank_llm)
        self._post("/api/config", {"settings": {"rerank_llm": True, "top_k_final": 8, "context_max_chars": 9000, "answer_max_tokens": 3000, "answer_effort": "medium", "context_chunk_chars": 1200}})
        tn.T.reset()
        self.assertTrue(self.p.s.toggles.rerank_llm)
        pl = self._get("/api/prompts")
        self.assertTrue(any(x["name"] == "answer_guide" for x in pl))
        one = self._get("/api/prompts?name=answer_guide")
        self.assertIn("content", one)
        self._post("/api/prompts", {"name": "answer_guide", "content": "# 테스트 가이드 XYZ"})
        self.assertIn("XYZ", self._get("/api/prompts?name=answer_guide")["content"])
        self._post("/api/prompts", {"action": "reset", "name": "answer_guide"})
        self.assertNotIn("XYZ", self._get("/api/prompts?name=answer_guide")["content"])
        qrl = self._get("/api/query_rules")
        self.assertIn("acronym", qrl["rules"])
        self._post("/api/query_rules", {"action": "add", "type": "synonym", "term": "언더런", "values": ["underrun"]})
        self.assertTrue(any(f["type"] == "synonym" for f in self._get("/api/query_rules/test?q=" + urllib.request.quote("언더런 원인"))["fired"]))
        self._post("/api/query_rules", {"action": "remove", "type": "synonym", "term": "언더런"})
        pin = self._post("/api/pins", {"action": "add", "doc": "RULE-ISR-001", "keywords": ["리뷰"], "note": "t"})
        self.assertTrue(any(p["id"] == pin["id"] for p in self._get("/api/pins")))
        self.assertTrue(self._post("/api/pins", {"action": "test", "q": "코드 리뷰 규칙"})["matched"])
        self._post("/api/pins", {"action": "remove", "id": pin["id"]})
        ag = self._get("/api/agents")
        self.assertIn("opencode", ag["agents"])
        self.assertTrue(self._get("/api/models")["providers"]["agents"])
        mem = self._get("/api/memory")
        self.assertIn("episodes", mem)
        self.assertIn("entries", self._get("/api/precompute"))

    def test_trials_and_fusion_jobs(self):
        qp = os.path.join(self.corpus, "questions.json")
        j1 = self._job(self._post("/api/trials", {"action": "run", "name": "web-a", "k": 5, "questions": qp, "overrides": {}})["job"])
        self.assertEqual(j1["status"], "done", j1.get("error"))
        j2 = self._job(self._post("/api/trials", {"action": "run", "name": "web-b", "k": 5, "questions": qp, "sets": {"graph": "false"}, "overrides": {}})["job"])
        self.assertEqual(j2["status"], "done", j2.get("error"))
        self.assertTrue(self.p.s.toggles.graph)
        lst = self._get("/api/trials")
        self.assertGreaterEqual(len(lst), 2)
        cmp_ = self._get("/api/trials/compare?ids=web-a,web-b")
        self.assertIn("markdown", cmp_)
        self.assertTrue(any(d["key"] == "toggles.graph" for d in cmp_["config_diff"]))
        tr = self._get("/api/trial?id=web-a")
        self.assertEqual(tr["name"], "web-a")
        fj = self._job(self._post("/api/fusion/compare", {"methods": ["rrf", "zscore"], "k": 5, "questions": qp})["job"])
        self.assertEqual(fj["status"], "done", fj.get("error"))
        self.assertEqual([r["method"] for r in fj["result"]["rows"]], ["rrf", "zscore"])
        fs = self._get("/api/forensics/summary")
        self.assertIn("n", fs)
        mm = self._post("/api/memory", {"action": "decay"})
        self.assertIn("proposals_decayed", mm)


if __name__ == "__main__":
    unittest.main()
