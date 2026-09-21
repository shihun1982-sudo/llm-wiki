# -*- coding: utf-8 -*-
"""제안 설명(proposal_explain) — 사람이 읽을 수 있는 설명·영향·점검.

왜 이 테스트가 있나: Evolve 화면·CLI·MCP 가 제안을 payload JSON 원문으로만 보여 주던 때에는,
`{"aliases":["HWD-PHY-TIMING-B1 · PHY 타이밍 및 레지스터 사양 rev B1"],"name":"HWD-PHY-TIMING-B1 ","type":"relation"}`
같은 제안을 사람이 승인할지 말지 판단할 수 없었다. 실제 DB 에는 이런 제안이
 - 대상 엔티티가 없는 alias 24건 (승인하면 무조건 KeyError 로 failed)
 - 존재하지 않는 엔티티 type 11건 (등록은 되지만 관계가 하나도 안 생김)
 - 이름 앞뒤 공백 · 별칭이 헤딩 한 줄
쌓여 있었다. 이 테스트는 그 세 가지가 **설명에 드러나고**, error 급 점검이 붙은 제안은 **적용이 막히는지** 본다.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import proposal_explain as pe   # noqa: E402
from llmwiki import evolve as ev             # noqa: E402
from llmwiki import graph_rules as gr        # noqa: E402


RULES = {
    "entities": {"RX DMA": {"type": "hw_block", "aliases": ["rxdma"]},
                 "PHY": {"type": "hw_block", "aliases": []}},
    "types_for_cooccur": ["hw_block", "module", "concept", "cl"],
    "id_patterns": [{"type": "cl", "pattern": "CL-\\d+"}],
    "related_key_type": {"cls": "cl"},
    "link_rules": [{"when_doc_type": "spec", "target_type": "*", "rel": "about"}],
}


def _row(kind, payload, **kw):
    r = {"id": kw.get("id", 1), "kind": kind, "payload": payload, "reason": kw.get("reason", "테스트"),
         "confidence": kw.get("confidence", 0.8), "origin": kw.get("origin", "llm_review"),
         "status": "proposed", "strength": kw.get("strength", 1.0), "ts": 0}
    return r


class _FakeCtx(pe.Ctx):
    """규칙 사전을 파일에서 읽지 않고 위 RULES 로 고정한다 (프로젝트 data/rules.json 에 의존하지 않기 위해)."""

    def __init__(self):
        self.pipe = None
        self.rules = json.loads(json.dumps(RULES))
        self.types = gr.known_types(self.rules)
        self.entities = dict(self.rules["entities"])
        self.ent_lower = dict((k.strip().lower(), k) for k in self.entities)
        self._syn, self._qr = {}, {}


class KnownTypesTest(unittest.TestCase):
    def test_union_of_every_place_a_type_is_used(self):
        t = gr.known_types(RULES)
        for expect in ("hw_block", "module", "concept", "cl"):
            self.assertIn(expect, t)

    def test_wildcard_is_not_a_usable_entity_type(self):
        # link_rules 의 target_type "*" 는 '아무 type 이나' 라는 뜻이지 엔티티 type 이 아니다
        self.assertNotIn("*", gr.known_types(RULES))


class EntityProposalTest(unittest.TestCase):
    """#124 재현 — 이름 꼬리 공백 · 없는 type · 헤딩 한 줄 별칭."""

    def setUp(self):
        self.d = pe.describe(_row("entity", {"name": "HWD-PHY-TIMING-B1 ", "type": "relation",
                                             "aliases": ["HWD-PHY-TIMING-B1 · PHY 타이밍 및 레지스터 사양 rev B1"]},
                                  id=124), None, _FakeCtx())

    def _levels(self, needle):
        return [c["level"] for c in self.d["checks"] if needle in c["text"]]

    def test_title_says_what_it_is_not_raw_json(self):
        self.assertIn("엔티티 추가", self.d["title"])
        self.assertIn("HWD-PHY-TIMING-B1", self.d["title"])
        self.assertNotIn("{", self.d["title"])

    def test_names_the_file_it_changes(self):
        self.assertIn("rules.json", self.d["target"])

    def test_diff_shows_before_and_after(self):
        joined = "\n".join(self.d["diff"])
        self.assertIn("(사전에 없음)", joined)
        self.assertIn("HWD-PHY-TIMING-B1", joined)

    def test_impact_states_rebuild_cost(self):
        self.assertEqual(self.d["impact"]["rebuild"], "incremental")
        self.assertTrue(self.d["impact"]["rebuild_text"])
        self.assertTrue(self.d["impact"]["revert"])

    def test_unknown_type_is_an_error_with_the_valid_list(self):
        self.assertEqual(self._levels("type 'relation'"), ["error"])
        fix = [c["fix"] for c in self.d["checks"] if "type 'relation'" in c["text"]][0]
        self.assertIn("hw_block", fix)

    def test_trailing_space_is_flagged(self):
        self.assertEqual(self._levels("앞뒤에 공백"), ["warn"])

    def test_heading_shaped_alias_is_flagged(self):
        self.assertTrue([c for c in self.d["checks"] if "문장" in c["text"] or "이름 + 설명" in c["text"]])

    def test_verdict_is_not_applicable(self):
        self.assertFalse(self.d["applicable"])


class AliasProposalTest(unittest.TestCase):
    def test_missing_target_entity_is_an_error(self):
        # 포렌식이 만들던 {"alias": k} 형태 — 승인하면 KeyError 로 failed 가 되던 제안
        d = pe.describe(_row("alias", {"alias": "physical", "events": 7,
                                       "queries": ["Physical Downlink Control Channel 디코더 문제"]}), None, _FakeCtx())
        self.assertFalse(d["applicable"])
        self.assertTrue([c for c in d["checks"] if c["level"] == "error" and "entity" in c["text"]])
        self.assertIn("붙일 대상이 비어 있습니다", d["what"])

    def test_unknown_entity_name_is_an_error(self):
        d = pe.describe(_row("alias", {"entity": "없는엔티티", "alias": "rxd"}), None, _FakeCtx())
        self.assertTrue([c for c in d["checks"] if c["level"] == "error" and "없습니다" in c["text"]])

    def test_good_alias_is_applicable_and_shows_the_aliases_diff(self):
        d = pe.describe(_row("alias", {"entity": "RX DMA", "alias": "rx-dma"}), None, _FakeCtx())
        self.assertTrue(d["applicable"])
        self.assertIn("rx-dma", "\n".join(d["diff"]))
        self.assertIn("rxdma", "\n".join(d["diff"]))   # 기존 별칭도 보여 준다

    def test_alias_equal_to_name_is_useless(self):
        d = pe.describe(_row("alias", {"entity": "PHY", "alias": "phy"}), None, _FakeCtx())
        self.assertFalse(d["applicable"])

    def test_duplicate_alias_is_info_not_error(self):
        d = pe.describe(_row("alias", {"entity": "RX DMA", "alias": "rxdma"}), None, _FakeCtx())
        self.assertTrue([c for c in d["checks"] if c["level"] == "info"])
        self.assertTrue(d["applicable"])


class SynonymProposalTest(unittest.TestCase):
    def test_date_expansion_is_rejected_with_a_reason(self):
        # 실제로 쌓여 있던 'nvidia → 2026' — 승인하면 nvidia 질의 전체가 망가진다
        d = pe.describe(_row("synonym", {"term": "nvidia", "expansion": "2026"}, confidence=0.35, origin="capture"),
                        None, _FakeCtx())
        self.assertFalse(d["applicable"])
        self.assertTrue([c for c in d["checks"] if "날짜/숫자" in c["text"]])

    def test_scope_warns_that_every_query_is_affected(self):
        d = pe.describe(_row("synonym", {"term": "dma", "expansion": "직접메모리접근"}), None, _FakeCtx())
        self.assertTrue(d["applicable"])
        self.assertIn("모든", d["impact"]["scope"])
        self.assertEqual(d["impact"]["rebuild"], "none")


class OtherKindsTest(unittest.TestCase):
    def test_every_kind_produces_a_title_and_an_impact(self):
        samples = {
            "synonym": {"term": "a", "expansion": "b"},
            "alias": {"entity": "RX DMA", "alias": "rx-dma"},
            "entity": {"name": "새모듈", "type": "module", "aliases": []},
            "relation": {"src": "RX DMA", "dst": "PHY", "rel": "feeds"},
            "wiki_note": {"page": "_gaps", "note": "보강이 필요한 주제입니다"},
            "query_rule": {"type": "synonym", "term": "dma", "values": ["직접메모리접근"]},
            "pin": {"doc": "SPEC.md", "keywords": ["dma", "phy"]},
            "tuning": {"key": "top_k_final", "value": 8},
            "chunk_params": {"chunk_max_chars": 1200},
            "corpus_gap": {"topic": "phy timing"},
        }
        self.assertEqual(sorted(samples), sorted(ev.KINDS), "KINDS 가 늘면 설명도 함께 늘어야 한다")
        for kind, pl in samples.items():
            d = pe.describe(_row(kind, pl), None, _FakeCtx())
            self.assertTrue(d["title"], kind)
            self.assertNotIn("{", d["title"], "%s: 제목에 JSON 이 새어 나오면 안 된다" % kind)
            self.assertTrue(d["what"], kind)
            self.assertTrue(d["target"], kind)
            self.assertIn(d["impact"]["rebuild"], ("none", "none_na", "incremental", "full"), kind)
            self.assertTrue(d["impact"]["rebuild_text"], kind)
            self.assertTrue(pe.format_description(d), kind)

    def test_corpus_gap_says_it_cannot_be_applied(self):
        d = pe.describe(_row("corpus_gap", {"topic": "phy timing"}), None, _FakeCtx())
        self.assertFalse(d["applicable"])
        self.assertIn("문서", d["what"])
        self.assertEqual(d["impact"]["rebuild"], "none_na")

    def test_chunk_params_is_marked_as_the_expensive_one(self):
        d = pe.describe(_row("chunk_params", {"chunk_max_chars": 1200}), None, _FakeCtx())
        self.assertEqual(d["impact"]["rebuild"], "full")
        self.assertEqual(d["impact"]["risk"], "high")

    def test_missing_required_key_is_an_error_before_any_apply(self):
        d = pe.describe(_row("tuning", {"key": "top_k_final"}), None, _FakeCtx())   # value 없음
        self.assertFalse(d["applicable"])
        self.assertTrue([c for c in d["checks"] if c["level"] == "error" and "value" in c["text"]])

    def test_unknown_kind_is_reported_not_crashed(self):
        d = pe.describe(_row("무슨종류", {"x": 1}), None, _FakeCtx())
        self.assertFalse(d["applicable"])


class OriginTest(unittest.TestCase):
    def test_origin_is_translated_so_the_reader_can_weigh_it(self):
        for origin in ("capture", "forensics", "llm_review", "expectation", "feedback"):
            d = pe.describe(_row("synonym", {"term": "a", "expansion": "b"}, origin=origin), None, _FakeCtx())
            self.assertNotEqual(d["origin_text"], origin, origin)
            self.assertTrue(d["origin_text"])


class FormatTest(unittest.TestCase):
    def test_cli_rendering_contains_every_section(self):
        d = pe.describe(_row("entity", {"name": "새모듈", "type": "module", "aliases": ["newmod"]}), None, _FakeCtx())
        txt = pe.format_description(d)
        for section in ("무엇이 바뀌나", "바뀌는 곳", "영향", "리빌드", "판정", "명령"):
            self.assertIn(section, txt)

    def test_payload_cleaning_strips_the_whitespace_that_broke_matching(self):
        cleaned = ev._clean_payload("entity", {"name": "HWD-PHY-TIMING-B1 ", "type": " module ",
                                               "aliases": [" hwd ", "x"]})
        self.assertEqual(cleaned["name"], "HWD-PHY-TIMING-B1")
        self.assertEqual(cleaned["type"], "module")
        self.assertEqual(cleaned["aliases"], ["hwd", "x"])


class ApplyGateTest(unittest.TestCase):
    """error 급 점검이 붙은 제안은 **적용 전에** 막혀야 한다 (예전에는 수십 초짜리 회귀평가를 돌린 뒤 실패했다)."""

    def setUp(self):
        from llmwiki.config import Settings
        from llmwiki.pipeline import Pipeline
        self.tmp = tempfile.mkdtemp(prefix="llmwiki_pex_")
        corpus = os.path.join(self.tmp, "corpus")
        os.makedirs(corpus)
        with open(os.path.join(corpus, "d.md"), "w", encoding="utf-8") as f:
            f.write("# RX DMA\n\nRX DMA 는 PHY 에서 데이터를 받는다.\n")
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(self.tmp, "data"),
                     wiki_dir=os.path.join(self.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        self.p = Pipeline(s)

    def tearDown(self):
        try:
            self.p.store.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_broken_alias_fails_fast_with_an_explanation(self):
        pid = self.p.store.add_proposal("alias", {"alias": "physical"}, "테스트", 0.9, "forensics")
        r = ev.apply_proposal(self.p, pid, evaluate=True)   # evaluate=True 인데도 평가를 돌리지 않아야 한다
        self.assertEqual(r["status"], "failed")
        self.assertIn("entity", r["error"])
        self.assertEqual(self.p.store.get_proposal(pid)["status"], "failed")
        # 게이트를 통과해 실제 적용까지 갔다면(옛 동작) 에러는 원시 KeyError 이고 checks 가 없다.
        # 이 두 가지가 '평가 전에 막혔다' 는 유일한 증거다 — 없으면 이 테스트는 옛 동작도 통과해 버린다.
        self.assertIn("적용할 수 없는 제안입니다", r["error"])
        self.assertTrue([c for c in (r.get("checks") or []) if c["level"] == "error"])

    def test_describe_proposal_by_id_round_trips(self):
        pid = self.p.store.add_proposal("corpus_gap", {"topic": "phy"}, "테스트", 0.9, "forensics")
        d = ev.describe_proposal(self.p, pid)
        self.assertEqual(d["id"], pid)
        self.assertEqual(d["kind"], "corpus_gap")

    def test_describe_unknown_id_returns_error_not_crash(self):
        self.assertIn("error", ev.describe_proposal(self.p, 999999))

    def test_status_attaches_explanations_to_pending(self):
        self.p.store.add_proposal("corpus_gap", {"topic": "phy"}, "테스트", 0.9, "forensics")
        st = ev.status(self.p)
        self.assertTrue(st["pending"])
        self.assertIn("explain", st["pending"][0])
        self.assertTrue(st["pending"][0]["explain"]["title"])


if __name__ == "__main__":
    unittest.main()
