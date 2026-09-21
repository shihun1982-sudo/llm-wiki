# -*- coding: utf-8 -*-
"""2026-09-17 전면 재검토에서 찾은 것들에 대한 회귀 테스트.

여기 모인 것들의 공통점: **정상 경로에서는 드러나지 않는다**. 설정을 잘못 적었을 때,
LLM 이 돌아오지 않을 때처럼 실패할 때에만 보이는 것들이라 정상 동작만 확인해서는 놓친다.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from llmwiki.mcp_client import normalize_source          # noqa: E402
from llmwiki.providers import MockLLM, LLMError          # noqa: E402


class SourceConfigTest(unittest.TestCase):
    """`mcp_sources.json` 을 잘못 적었을 때 **무엇이 잘못됐는지** 말해 주는가.

    다른 RAG 를 붙이는 사람이 가장 먼저 만나는 파일이다. 예전에는 `retrieve` 를 (하나만 쓰니까)
    객체로 적으면, dict 를 순회해 문자열 키가 spec 자리에 들어가 질의 도중
    `'str' object has no attribute 'get'` 로 나타났다 — 어느 소스의 무엇이 문제인지 알 수 없었다.
    """

    def test_single_spec_object_is_accepted(self):
        c = normalize_source("peer", {"retrieve": {"tool": "search", "args": {"q": "{query}"}}})
        self.assertIsInstance(c["retrieve"], list)
        self.assertEqual(c["retrieve"][0]["tool"], "search")

    def test_ingest_and_enrich_are_normalized_too(self):
        c = normalize_source("peer", {"ingest": {"tool": "a"}, "enrich": {"tool": "b"}})
        self.assertEqual([c["ingest"][0]["tool"], c["enrich"][0]["tool"]], ["a", "b"])

    def test_bad_shapes_name_the_source_and_field(self):
        for cfg, must in (({"retrieve": "search"}, "목록이어야"),
                          ({"retrieve": ["search"]}, "객체여야"),
                          ({"retrieve": [{"args": {}}]}, "'tool' 이 없습니다")):
            with self.assertRaises(ValueError) as e:
                normalize_source("peer_wiki", cfg)
            msg = str(e.exception)
            self.assertIn("peer_wiki", msg, "어느 소스인지 말해야 한다: %s" % msg)
            self.assertIn("retrieve", msg, "어느 항목인지 말해야 한다: %s" % msg)
            self.assertIn(must, msg)

    def test_non_dict_source_is_refused(self):
        with self.assertRaises(ValueError):
            normalize_source("peer", "not-a-dict")

    def test_valid_config_passes_through(self):
        src = {"enabled": True, "transport": "http", "retrieve": [{"tool": "wiki_search", "args": {}}]}
        self.assertEqual(normalize_source("peer", src)["retrieve"], src["retrieve"])

    def test_shipped_example_is_valid(self):
        """setup/mcp_sources.example.json 이 스스로의 규칙을 지키는가 (문서가 틀리면 아무도 못 붙인다)."""
        import json
        p = os.path.join(ROOT, "setup", "mcp_sources.example.json")
        data = json.load(open(p, encoding="utf-8"))
        for name, cfg in data.items():
            if name.startswith("_"):
                continue
            normalize_source(name, cfg)      # 예외가 나면 실패


class SourceLoadResilienceTest(unittest.TestCase):
    """소스 하나가 잘못돼도 **나머지와 질의는 살아 있어야** 한다.

    여러 사람이 쓰는 서버에서 `mcp_sources.json` 의 오타 하나가 모두의 질의를 멈추게 하면 안 된다.
    그래서 읽기는 관대하게(건너뛰고 이유를 남긴다), 저장·점검은 엄격하게 본다.
    """

    def setUp(self):
        import json
        import shutil
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="lwsrc_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "mcp_sources.json")
        json.dump({
            "_comment": "주석은 건너뛴다",
            "good": {"enabled": True, "transport": "http", "url": "http://x/mcp",
                     "retrieve": [{"tool": "search", "args": {}}]},
            "broken": {"enabled": True, "transport": "http", "url": "http://y/mcp",
                       "retrieve": ["search"]},        # 목록 안에 문자열 — 흔한 실수
        }, open(self.path, "w", encoding="utf-8"), ensure_ascii=False)
        self._old = os.environ.get("LLMWIKI_MCP_SOURCES_PATH")
        os.environ["LLMWIKI_MCP_SOURCES_PATH"] = self.path
        self.addCleanup(lambda: (os.environ.__setitem__("LLMWIKI_MCP_SOURCES_PATH", self._old)
                                 if self._old else os.environ.pop("LLMWIKI_MCP_SOURCES_PATH", None)))

    def test_bad_source_is_skipped_not_fatal(self):
        from llmwiki import mcp_client as mc
        srcs = mc.load_sources()
        self.assertIn("good", srcs, "정상 소스는 그대로 쓸 수 있어야 한다")
        self.assertNotIn("broken", srcs, "잘못된 소스는 건너뛴다")
        errs = mc.source_errors()
        self.assertIn("broken", errs)
        self.assertIn("retrieve", errs["broken"])

    def test_strict_mode_raises_for_editing_paths(self):
        from llmwiki import mcp_client as mc
        with self.assertRaises(ValueError):
            mc.load_sources(strict=True)

    def test_comment_keys_are_ignored(self):
        from llmwiki import mcp_client as mc
        self.assertNotIn("_comment", mc.load_sources())


class EmptyChannelHealthTest(unittest.TestCase):
    """켜진 채널이 **비어 있는** 상태를 health 가 잡아내는가.

    실제로 겪은 일: 코퍼스를 넣은 뒤 증분 빌드만 돌아(changed=0) 그래프가 한 번도 만들어지지 않았고,
    엔티티 0개인 채로 몇 주가 지났다. 질의마다 graph_search 는 돌지만 늘 0건이라 오류도 로그도 없다.
    `build graph` 13초면 617 엔티티·13,411 관계가 생기는데, 아무도 그 사실을 몰랐던 것이 문제다.
    """

    def _pipe(self, **toggles):
        import shutil
        import tempfile
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        tmp = tempfile.mkdtemp(prefix="lwhealth_")
        self.addCleanup(shutil.rmtree, tmp, True)
        corpus = os.path.join(tmp, "corpus")
        os.makedirs(corpus)
        open(os.path.join(corpus, "d.md"), "w", encoding="utf-8").write(
            "# ISSUE-3001 링크 협상 실패\n\nRX DMA 의 FIFO 임계값 문제. 수정 CL 은 CL-70001.\n")
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data"), wiki_dir=os.path.join(tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=256)
        s.toggles = Toggles(**dict({"auto_build": False}, **toggles))
        p = Pipeline(s)
        self.addCleanup(p.store.close)
        return p

    def _check(self, pipe):
        from llmwiki.health import run_health
        r = run_health(pipe, quick=True)
        return next((c for c in r["checks"] if c["name"] == "channels_populated"), None)

    def test_graph_on_but_empty_is_flagged(self):
        p = self._pipe(graph=True, rule_graph=False, llm_graph=False, vector=False, doc_vector=False)
        p.build(full=True)
        p.store.conn.execute("DELETE FROM entities")
        p.store.conn.commit()
        c = self._check(p)
        self.assertIsNotNone(c, "channels_populated 검사가 없습니다")
        self.assertFalse(c["ok"], "그래프가 비었는데 통과로 보고했습니다")
        self.assertIn("build graph", c["detail"], "무엇을 하면 되는지 알려야 한다")

    def test_populated_graph_passes(self):
        p = self._pipe(graph=True, rule_graph=True, llm_graph=False, vector=False, doc_vector=False)
        p.build(full=True)
        n = p.store.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        self.assertGreater(n, 0, "규칙 그래프가 엔티티를 못 만들었습니다 (그러면 이 검사가 의미 없다)")
        self.assertTrue(self._check(p)["ok"])

    def test_graph_off_is_not_flagged(self):
        """끈 채널은 비어 있어도 정상이다 (경고를 남발하면 아무도 안 본다)."""
        p = self._pipe(graph=False, rule_graph=False, llm_graph=False, vector=False, doc_vector=False)
        p.build(full=True)
        self.assertTrue(self._check(p)["ok"])

    def _quality(self, pipe):
        from llmwiki.health import run_health
        r = run_health(pipe, quick=True)
        return next((c for c in r["checks"] if c["name"] == "embedder_quality"), None)

    def test_explicit_hash_is_not_warned(self):
        """오프라인으로 hash 를 **고른** 사람에게는 경고하지 않는다."""
        p = self._pipe(vector=True, graph=False, rule_graph=False, llm_graph=False, doc_vector=False)
        p.s.embed_provider = "hash"
        p.build(full=True)
        self.assertTrue(self._quality(p)["ok"])

    def test_auto_fallback_to_hash_is_warned_when_index_is_large(self):
        """auto 인데 모델을 못 찾아 hash 가 된 경우에만, 그리고 색인이 클 때만 경고한다.

        주의: `auto` 는 이 PC 에 실제로 무엇이 깔려 있는지에 따라 결과가 달라진다(VOYAGE 키 · Ollama 의
        임베딩 모델). 그래서 **찾지 못하는 상태를 명시적으로 만든다** — 그러지 않으면 누군가 `ollama pull bge-m3`
        를 한 순간 이 테스트가 깨진다(2026-09-19에 실제로 그랬다).
        """
        p = self._pipe(vector=True, graph=False, rule_graph=False, llm_graph=False, doc_vector=False)
        p.s.embed_provider = "auto"
        p.s.ollama_url = "http://127.0.0.1:1"      # 닿지 않는 주소 → auto 가 Ollama 임베딩 모델을 찾지 못한다
        self._no_voyage = os.environ.pop("VOYAGE_API_KEY", None)
        self.addCleanup(lambda: os.environ.__setitem__("VOYAGE_API_KEY", self._no_voyage) if self._no_voyage else None)
        p.reload()
        p.build(full=True)
        self.assertEqual(p.embedder.name, "hash", "auto 가 hash 로 떨어지지 않았습니다 (이 테스트의 전제)")
        c = self._quality(p)
        self.assertIsNotNone(c)
        # 작은 색인(문서 1개)에서는 경고하지 않는다
        self.assertTrue(c["ok"], "작은 색인에서까지 경고하면 소음이 된다")
        # 큰 색인처럼 보이게 하면 경고한다
        real_stats = p.store.stats
        p.store.stats = lambda: dict(real_stats(), chunks=5000)
        try:
            c2 = self._quality(p)
            self.assertFalse(c2["ok"], "큰 색인이 hash 로 도는데 알리지 않았습니다")
            self.assertIn("hash", c2["detail"])
        finally:
            p.store.stats = real_stats


class SidebarToggleMetaTest(unittest.TestCase):
    """왼쪽 사이드바가 쓰는 토글 메타데이터가 **하나도 빠지지 않았는가**.

    토글이 63개라 하나 추가하고 그룹·설명·효과 표에 넣는 것을 잊기 쉽다. 실제로 `rerun_capture` 를
    추가하면서 셋 다 빠뜨렸고, 화면에서는 설명 없는 '기타' 항목으로 밀려 있었다.
    """

    def _names(self):
        from llmwiki.config import Toggles
        return list(Toggles.__dataclass_fields__)

    def test_every_toggle_is_in_a_group(self):
        from llmwiki.config import TOGGLE_GROUPS
        grouped = [t for g in TOGGLE_GROUPS for t in g["toggles"]]
        missing = [t for t in self._names() if t not in grouped]
        self.assertFalse(missing, "그룹에 없는 토글 (화면에서 '기타' 로 밀린다): %s" % missing)

    def test_no_ghost_or_duplicate_in_groups(self):
        from llmwiki.config import TOGGLE_GROUPS
        names = set(self._names())
        grouped = [t for g in TOGGLE_GROUPS for t in g["toggles"]]
        self.assertFalse([t for t in grouped if t not in names], "Toggles 에 없는 이름이 그룹에 있습니다")
        dup = sorted({t for t in grouped if grouped.count(t) > 1})
        self.assertFalse(dup, "두 그룹에 중복된 토글: %s" % dup)

    def test_every_toggle_has_help(self):
        from llmwiki.config import TOGGLE_HELP
        missing = [t for t in self._names() if not str(TOGGLE_HELP.get(t) or "").strip()]
        self.assertFalse(missing, "설명이 없는 토글 (마우스를 올려도 아무 것도 안 뜬다): %s" % missing)

    def test_every_toggle_has_effect_entry(self):
        """품질/속도/토큰 배지의 근거. 항목 자체가 없으면 '영향 없음' 과 구분되지 않는다."""
        from llmwiki.config import TOGGLE_EFFECT
        missing = [t for t in self._names() if t not in TOGGLE_EFFECT]
        self.assertFalse(missing, "효과 표에 없는 토글: %s" % missing)

    def test_effect_values_are_well_formed(self):
        from llmwiki.config import TOGGLE_EFFECT, TOGGLE_AXES
        for t, e in TOGGLE_EFFECT.items():
            self.assertIsInstance(e, dict, t)
            for k, v in e.items():
                self.assertIn(k, TOGGLE_AXES, "%s: 모르는 축 %r" % (t, k))
                self.assertIn(v, (1, -1), "%s.%s 는 +1/-1 이어야 합니다 (받은 값 %r)" % (t, k, v))

    def test_groups_have_stage_and_hint(self):
        """묶음마다 단계 배지와 한 줄 설명이 있어야 한다 (제목만으로는 무엇을 하는 묶음인지 모른다)."""
        from llmwiki.config import TOGGLE_GROUPS
        for g in TOGGLE_GROUPS:
            self.assertTrue(str(g.get("stage") or "").strip(), "%s: stage 없음" % g["key"])
            self.assertTrue(str(g.get("hint") or "").strip(), "%s: hint 없음" % g["key"])

    def test_speed_preset_toggles_agree_with_effect_table(self):
        """`speed` 프리셋이 끄는 것들은 효과 표에서도 속도를 **나쁘게** 하는 것이어야 한다.

        표와 프리셋이 서로 다른 말을 하면 배지를 믿을 수 없다.
        """
        import json
        p = os.path.join(ROOT, "presets.json")
        if not os.path.exists(p):
            self.skipTest("presets.json 없음")
        from llmwiki.config import TOGGLE_EFFECT
        speed = (json.load(open(p, encoding="utf-8")).get("speed") or {}).get("toggles") or {}
        for name, on in speed.items():
            e = TOGGLE_EFFECT.get(name)
            if e is None or on:
                continue
            self.assertTrue(e.get("s", 0) < 0 or e.get("t", 0) < 0,
                            "speed 프리셋이 %s 를 끄는데, 효과 표에는 속도/토큰을 나쁘게 한다고 되어 있지 않다: %s" % (name, e))


class CorpusExcludeTest(unittest.TestCase):
    """코퍼스에 있어도 **색인하지 않을** 경로를 설정으로 정할 수 있는가.

    왜 생겼나: 제외 폴더가 코드에 박혀 있어서, 색인하면 안 되는 것이 코퍼스에 섞여도 파일을 옮기는
    것 말고는 방법이 없었다. 실제로 이 도구 자신의 소스가 코퍼스에 들어가 도메인 문서를 밀어냈고,
    제외했더니 hit@k 0.64 → 0.88 · MRR 0.396 → 0.676 이었다.
    """

    def _pipe(self, exclude=None):
        import shutil
        import tempfile
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        tmp = tempfile.mkdtemp(prefix="lwex_")
        self.addCleanup(shutil.rmtree, tmp, True)
        corpus = os.path.join(tmp, "corpus")
        for sub in ("issues", "vendor", "vendor/deep"):
            os.makedirs(os.path.join(corpus, sub), exist_ok=True)
        open(os.path.join(corpus, "issues", "ISSUE-9001.md"), "w", encoding="utf-8").write(
            "# ISSUE-9001 링크 실패\n\nFIFO 임계값 문제. 수정 CL 은 CL-90001.\n")
        open(os.path.join(corpus, "vendor", "NOTE-lib.md"), "w", encoding="utf-8").write("# vendor lib\n\n남의 소스.\n")
        open(os.path.join(corpus, "vendor", "deep", "NOTE-deep.md"), "w", encoding="utf-8").write("# deep\n\n더 깊은 것.\n")
        open(os.path.join(corpus, "top.md"), "w", encoding="utf-8").write("# top\n\n최상위 문서.\n")
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data"), wiki_dir=os.path.join(tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=128)
        s.toggles = Toggles(auto_build=False, llm_graph=False)
        if exclude is not None:
            s.corpus_exclude = exclude
        p = Pipeline(s)
        self.addCleanup(p.store.close)
        return p

    def _docs(self, pipe):
        return {r[0] for r in pipe.store.conn.execute("SELECT doc_id FROM docs").fetchall() if not r[0].startswith("wiki/")}

    def test_without_exclude_everything_is_indexed(self):
        p = self._pipe()
        p.build(full=True)
        self.assertEqual(len(self._docs(p)), 4)

    def test_folder_prefix_excludes_subtree(self):
        for pat in ("vendor/", "vendor/*", "vendor/**"):
            p = self._pipe([pat])
            p.build(full=True)
            got = self._docs(p)
            self.assertNotIn("corpus/vendor/NOTE-lib.md", got, "패턴 %r 이 폴더를 제외하지 못했습니다" % pat)
            self.assertNotIn("corpus/vendor/deep/NOTE-deep.md", got, "패턴 %r 이 하위 폴더를 제외하지 못했습니다" % pat)
            self.assertIn("corpus/issues/ISSUE-9001.md", got, "도메인 문서까지 지웠습니다")

    def test_glob_pattern_excludes_by_name(self):
        p = self._pipe(["**/NOTE-*.md"])
        p.build(full=True)
        got = self._docs(p)
        self.assertNotIn("corpus/vendor/NOTE-lib.md", got)
        self.assertEqual({"corpus/issues/ISSUE-9001.md", "corpus/top.md"}, got)

    def test_excluded_docs_are_removed_on_rebuild(self):
        """이미 색인된 뒤에 제외하면 **다음 빌드에서 사라져야** 한다 (설정만 바꾸고 방치되면 안 된다)."""
        p = self._pipe()
        p.build(full=True)
        self.assertIn("corpus/vendor/NOTE-lib.md", self._docs(p))
        p.s.corpus_exclude = ["vendor/"]
        res, _ = p.build(full=False)
        self.assertGreaterEqual(res.get("removed", 0), 2)
        self.assertNotIn("corpus/vendor/NOTE-lib.md", self._docs(p))

    def test_watcher_scan_uses_the_same_exclude(self):
        """워처가 빌드와 다른 목록을 쓰면 매 주기마다 헛빌드가 돈다."""
        from llmwiki.corpus import scan_changed
        p = self._pipe(["vendor/"])
        p.build(full=True)
        r = scan_changed(p.s.corpus_dirs, p.store.doc_stats(), exclude=p.s.corpus_exclude)
        self.assertEqual(r["n_changed"], 0, "제외한 파일을 '바뀐 문서' 로 세고 있습니다: %s" % r["changed"][:3])
        self.assertEqual(r["n_removed"], 0, "제외한 파일을 '삭제된 문서' 로 세고 있습니다: %s" % r["removed"][:3])

    def test_pattern_helpers(self):
        from llmwiki.corpus import is_excluded, compile_excludes
        pats = compile_excludes(["  imported/llmwiki/ ", "", "# 주석", "**/NOTE-*.md"])
        self.assertEqual(len(pats), 2)
        self.assertTrue(is_excluded("imported/llmwiki/a.md", pats))
        self.assertTrue(is_excluded("imported/llmwiki/deep/b.md", pats))
        self.assertTrue(is_excluded("x/y/NOTE-z.md", pats))
        self.assertFalse(is_excluded("imported/issues/ISSUE-1.md", pats))
        self.assertFalse(is_excluded("imported/llmwikiX/a.md", pats), "접두 일치가 너무 넓습니다")


class IncrementalRebuildConsistencyTest(unittest.TestCase):
    """증분 빌드를 반복한 색인이 **처음부터 전체 빌드한 것과 같은가**.

    증분은 바뀐 문서만 다시 넣는다. 어딘가에서 지우지 않거나 두 번 넣으면 색인이 조용히 어긋나고,
    질의는 계속 답을 주므로 아무도 모른다. 문서 추가·수정·삭제·이름변경을 한 번씩 거친 뒤
    같은 코퍼스로 전체 빌드한 것과 대조한다.
    """

    def _make(self, tmp, files):
        corpus = os.path.join(tmp, "corpus")
        os.makedirs(corpus, exist_ok=True)
        for name, text in files.items():
            p = os.path.join(corpus, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        return corpus

    def _pipe(self, corpus, tmp, tag):
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data-" + tag),
                     wiki_dir=os.path.join(tmp, "wiki-" + tag),
                     llm_provider="mock", embed_provider="hash", embed_dim=128)
        s.toggles = Toggles(auto_build=False, llm_graph=False, rule_graph=True)
        p = Pipeline(s)
        self.addCleanup(p.store.close)
        return p

    def _snapshot(self, pipe):
        c = pipe.store.conn
        docs = sorted(r[0] for r in c.execute("SELECT doc_id FROM docs").fetchall())
        chunks = sorted(r[0] for r in c.execute("SELECT chunk_id FROM chunks").fetchall())
        fts = sorted(r[0] for r in c.execute("SELECT chunk_id FROM chunks_fts").fetchall())
        emb = sorted(r[0] for r in c.execute("SELECT chunk_id FROM embeddings").fetchall())
        ents = sorted(r[0] for r in c.execute("SELECT entity_id FROM entities").fetchall())
        return {"docs": docs, "chunks": chunks, "fts": fts, "emb": emb, "entities": ents}

    def test_incremental_sequence_matches_full_rebuild(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix="lwinc_")
        self.addCleanup(shutil.rmtree, tmp, True)
        base = {
            "issues/ISSUE-1.md": "# ISSUE-1 링크 실패\n\nFIFO 임계값. 수정 CL 은 CL-101.\n",
            "issues/ISSUE-2.md": "# ISSUE-2 AGC 지연\n\noff-by-one 오류. 수정 CL 은 CL-102.\n",
            "cls/CL-101.md": "# CL-101\n\nISSUE-1 을 고친다.\n",
        }
        corpus = self._make(tmp, base)
        inc = self._pipe(corpus, tmp, "inc")
        inc.build(full=True)

        # ① 추가 ② 수정 ③ 삭제 ④ 이름 변경 — 실제로 일어나는 네 가지를 한 번씩
        self._make(tmp, {"cls/CL-102.md": "# CL-102\n\nISSUE-2 를 고친다.\n"})
        inc.build(full=False)
        self._make(tmp, {"issues/ISSUE-1.md": "# ISSUE-1 링크 실패 (수정됨)\n\nFIFO 임계값을 8 로. 수정 CL 은 CL-101.\n"})
        inc.build(full=False)
        os.remove(os.path.join(corpus, "issues", "ISSUE-2.md"))
        inc.build(full=False)
        os.rename(os.path.join(corpus, "cls", "CL-101.md"), os.path.join(corpus, "cls", "CL-101-renamed.md"))
        inc.build(full=False)

        full = self._pipe(corpus, tmp, "full")
        full.build(full=True)

        a, b = self._snapshot(inc), self._snapshot(full)
        for key in ("docs", "chunks", "fts", "emb"):
            self.assertEqual(a[key], b[key],
                             "증분 결과가 전체 빌드와 다릅니다 (%s)\n증분만: %s\n전체만: %s"
                             % (key, sorted(set(a[key]) - set(b[key]))[:5], sorted(set(b[key]) - set(a[key]))[:5]))

    def test_build_verify_reports_clean_after_incremental(self):
        """색인 자체 무결성 점검(build verify)이 증분 반복 뒤에도 깨끗해야 한다."""
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix="lwinc2_")
        self.addCleanup(shutil.rmtree, tmp, True)
        corpus = self._make(tmp, {"a.md": "# A\n\nISSUE-1 내용.\n", "b.md": "# B\n\nCL-101 내용.\n"})
        p = self._pipe(corpus, tmp, "v")
        p.build(full=True)
        self._make(tmp, {"c.md": "# C\n\n새 문서.\n"})
        p.build(full=False)
        os.remove(os.path.join(corpus, "a.md"))
        p.build(full=False)
        r = p.store.verify(p.embedder.name, fix=False, wiki_dir=p.s.wiki_dir)
        bad = {k: v for k, v in (r or {}).items() if isinstance(v, (int, float)) and v and "orphan" in k.lower()}
        self.assertFalse(bad, "증분 빌드 뒤 고아 행이 남았습니다: %s" % bad)


class EvolveProposalQualityTest(unittest.TestCase):
    """자가진화가 **쓸 수 없는 제안**을 쌓지 않는가.

    실제 색인에서 발견한 것: `nvidia → 2026`, `nvidia → 20`, `추가 → 2026` 같은 동의어 제안이
    쌓여 있었다. "2026년 5월 20일 … 임원회의" 헤딩에서 숫자를 뽑아 동의어 후보로 올린 것이다.
    사람이 승인하면 이후 모든 'nvidia' 질의가 '2026' 까지 확장되어 검색이 망가진다.
    엔티티 후보 쪽에는 원래 숫자 필터가 있었는데 동의어 쪽에만 없었다 (비대칭).
    """

    def test_digits_and_dates_are_not_synonym_candidates(self):
        from llmwiki.evolve import _usable_term
        for bad in ("2026", "20", "5", "2026년", "3분기", "12일", "a", ""):
            self.assertFalse(_usable_term(bad), "%r 은 동의어 후보가 되면 안 됩니다" % bad)

    def test_real_terms_are_kept(self):
        from llmwiki.evolve import _usable_term
        for good in ("nvidia", "PDCCH", "캐파", "RX_DMA", "t_setup", "bge-m3"):
            self.assertTrue(_usable_term(good), "%r 은 동의어 후보로 남아야 합니다" % good)

    def test_capture_does_not_propose_date_synonyms(self):
        """헤딩이 날짜뿐인 문단에서는 동의어 제안이 아예 나오지 않아야 한다."""
        import shutil
        import tempfile
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        tmp = tempfile.mkdtemp(prefix="lwev_")
        self.addCleanup(shutil.rmtree, tmp, True)
        corpus = os.path.join(tmp, "corpus")
        os.makedirs(corpus)
        open(os.path.join(corpus, "mtg.md"), "w", encoding="utf-8").write(
            "# 2026년 5월 20일 (수) · 캐파 확장 검토 임원회의\n\n"
            "## 2026년 5월 20일 (수)\n\n논의 내용은 별도 첨부. nvidia 언급 있음.\n")
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data"), wiki_dir=os.path.join(tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=128)
        s.toggles = Toggles(auto_build=False, llm_graph=False, evolve_capture=True, evolve_auto_apply=False)
        p = Pipeline(s)
        self.addCleanup(p.store.close)
        p.build(full=True)
        with p.request_scope():
            p.query("nvidia 추가 논의", log=True)
        cols = [r[1] for r in p.store.conn.execute("PRAGMA table_info(proposals)").fetchall()]
        rows = [dict(zip(cols, r)) for r in p.store.conn.execute("SELECT * FROM proposals WHERE kind='synonym'").fetchall()]
        import json as _j
        bad = []
        for r in rows:
            try:
                pay = _j.loads(r["payload"])
            except Exception:
                continue
            for side in ("term", "expansion"):
                v = str(pay.get(side) or "")
                if v.isdigit() or v in ("2026년", "20일", "5월"):
                    bad.append(pay)
        self.assertFalse(bad, "숫자·날짜가 동의어 제안에 들어갔습니다: %s" % bad[:4])


class RerankFusedWeightTest(unittest.TestCase):
    """리랭크가 **융합·부스트 점수를 얼마나 반영할지** 정하는 손잡이.

    왜 생겼나: 리랭크는 후보를 다시 줄 세우면서 앞 단계(채널 융합 rrf_k·채널 가중치·시간/pin/provenance
    부스트)가 매긴 fused 점수를 버렸다(동점일 때만 참고, cross_encoder 는 아예 무시). 그래서 rrf_k 를
    2~60 으로 바꿔도 최종 순위가 거의 그대로였다(실측 MRR 0.467~0.477). 특히 pin_boost=10 으로 고정한
    근거가 리랭크에 밀리는 일이 생긴다. 이제 `rerank_fused_w` 로 비중을 정한다 (기본 0 = 예전 동작).
    """

    def _hits(self):
        from llmwiki.retrieval import Hit
        a, b = Hit("A"), Hit("B")
        a.rerank, a.fused = 0.9, 0.01        # 리랭크가 높게 본 후보
        b.rerank, b.fused = 0.5, 0.99        # 융합·부스트가 높게 본 후보 (예: pin)
        return [a, b]

    def test_default_zero_keeps_previous_behaviour(self):
        from llmwiki.retrieval import final_order
        order = [h.chunk_id for h in final_order(self._hits(), 0.0)]
        self.assertEqual(order, ["A", "B"], "기본값에서는 리랭크 점수만으로 정렬해야 한다")

    def test_weight_lets_fused_win(self):
        from llmwiki.retrieval import final_order
        order = [h.chunk_id for h in final_order(self._hits(), 2.0)]
        self.assertEqual(order, ["B", "A"], "비중을 높이면 융합·부스트가 최종 순위를 뒤집을 수 있어야 한다")

    def test_weight_is_monotonic(self):
        """가중치를 올릴수록 fused 가 높은 후보가 앞으로 온다 (중간에 뒤집혔다 돌아오지 않는다)."""
        from llmwiki.retrieval import final_order
        seen = [[h.chunk_id for h in final_order(self._hits(), w)][0] for w in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0)]
        self.assertEqual(seen[0], "A", "가중치 0 에서는 리랭크가 이겨야 한다")
        self.assertEqual(seen[-1], "B", "가중치가 크면 융합이 이겨야 한다")
        # A 구간이 먼저, B 구간이 나중 — 전환이 한 번뿐이어야 한다 ('A' < 'B' 이므로 정렬된 상태와 같다)
        self.assertEqual(seen, sorted(seen), "가중치에 따라 순위가 왔다 갔다 합니다: %s" % seen)

    def test_equal_scores_do_not_crash(self):
        from llmwiki.retrieval import Hit, final_order
        hs = [Hit("A"), Hit("B")]
        for h in hs:
            h.rerank, h.fused = 0.5, 0.5     # 모두 같은 값 → 정규화 분모 0
        self.assertEqual(len(final_order(hs, 1.0)), 2)

    def test_tunable_is_declared_and_documented(self):
        from llmwiki.tuning import _INDEX
        t = _INDEX.get("rerank_fused_w")
        self.assertIsNotNone(t, "rerank_fused_w 가 튜닝 목록에 없습니다")
        self.assertEqual(t["default"], 0.0, "기본값이 바뀌면 기존 동작이 조용히 달라진다")
        self.assertEqual(t["stage"], "rerank")
        self.assertIn("pin", (t.get("impact") or ""), "무엇에 쓰는 손잡이인지 설명에 있어야 한다")


class MockTestHookTest(unittest.TestCase):
    """실패 경로를 결정적으로 재현하는 테스트 훅. **환경 변수가 없으면 아무 일도 하지 않아야** 한다."""

    def setUp(self):
        MockLLM._fail_counts.clear()
        for k in ("LLMWIKI_MOCK_FAIL", "LLMWIKI_MOCK_DELAY_MS"):
            os.environ.pop(k, None)

    tearDown = setUp

    def test_hook_is_inert_by_default(self):
        r = MockLLM().complete("TASK=answer", "[C1] x")
        self.assertIn("mock answer", r["text"])

    def test_transient_failure_then_success(self):
        os.environ["LLMWIKI_MOCK_FAIL"] = "timeout:2"
        m = MockLLM()
        m.retries, m.retry_backoff_s, m.circuit_failures = 3, 0, 0
        r = m.complete("TASK=answer", "[C1] x")
        self.assertIn("mock answer", r["text"])
        self.assertEqual(m.stats["retries"], 2, "두 번 실패하고 세 번째에 성공해야 한다")

    def test_persistent_failure_is_transient_llmerror(self):
        os.environ["LLMWIKI_MOCK_FAIL"] = "timeout"
        m = MockLLM()
        m.retries, m.retry_backoff_s, m.circuit_failures = 1, 0, 0
        with self.assertRaises(LLMError) as e:
            m.complete("TASK=answer", "x")
        self.assertTrue(e.exception.transient, "타임아웃은 재시도 대상이어야 한다")
        self.assertEqual(e.exception.kind, "timeout")

    def test_delay_hook_is_honored(self):
        import time
        os.environ["LLMWIKI_MOCK_DELAY_MS"] = "250"
        t0 = time.time()
        MockLLM().complete("TASK=answer", "x")
        self.assertGreaterEqual(time.time() - t0, 0.2)


if __name__ == "__main__":
    unittest.main()
