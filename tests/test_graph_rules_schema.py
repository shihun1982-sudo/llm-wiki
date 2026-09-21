# -*- coding: utf-8 -*-
"""그래프 규칙 확장성 — 값 종류 레지스트리 · chunk_values · 스키마 어휘 · lint.

왜 이 테스트가 있나 (2026-09-19):
  "규칙을 data/rules.json 으로 외부화했다" 가 절반만 사실이었다.
  - `relation_patterns[*].value` 의 종류가 `_apply_rel_patterns()` 의 if/elif 사슬에 박혀 있어,
    `4 ns` 같은 단위 값이나 `rev B1` 같은 버전을 잡으려면 파이썬을 고쳐야 했다.
  - 청크마다 남길 스칼라(날짜·금액)와 그 관계명(`mentions_date` …)도 코드에 있었다.
  - graph_build 에 **관계 이름 화이트리스트**가 있어, 파일에 새로 적은 관계 패턴이 이름이 다르다는 이유로
    조용히 버려졌다.
  - 규칙과 LLM 이 낸 `type`/`rel` 에 아무 검증이 없어 `CL`(유효값 `cl`)·`used`/`uses` 처럼 갈라졌다.

이 테스트는 그 네 가지가 고쳐졌고, **옛 규칙 파일이 그대로 돈다**(호환)는 것을 본다.
"""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmwiki import graph_rules as gr   # noqa: E402


def _ex(rules=None):
    return gr.RuleExtractor(copy.deepcopy(rules or gr.DEFAULT_RULES))


def _types(ents):
    out = {}
    for e in ents.values():
        out.setdefault(e.type, []).append(e.name)
    return out


class ValueTypeRegistryTest(unittest.TestCase):
    def test_registry_holds_every_builtin(self):
        for name in ("entity", "date", "money", "percent", "id", "text", "measure", "version"):
            self.assertIn(name, gr.VALUE_TYPES, name)

    def test_describe_value_types_is_shared_by_the_three_surfaces(self):
        rows = gr.describe_value_types()
        self.assertEqual(len(rows), len(gr.VALUE_TYPES))
        for r in rows:
            self.assertTrue(r["label"] and r["desc"], r)

    def test_a_new_value_type_needs_no_code_change_in_the_extractor(self):
        """레지스트리에 등록만 하면 규칙 파일에서 바로 쓸 수 있어야 한다."""
        calls = []

        def _resolve(ex, val, pat, add):
            calls.append(val)
            return [add("TICKET-" + val.strip(), "issue", "", 0.9)]

        gr.register_value_type(gr.ValueType("ticketish", "테스트용", "테스트", _resolve, "issue"))
        try:
            rules = copy.deepcopy(gr.DEFAULT_RULES)
            rules["relation_patterns"] = [{"name": "t", "regex": r"티켓:\s*(\S+)", "value": "ticketish",
                                           "in_chunk": {"rel": "references", "weight": 0.9, "confidence": 0.9}}]
            ents, _c, rels = _ex(rules).extract_chunk("티켓: 4242 입니다", "", "d", "문서")
            self.assertEqual(calls, ["4242"])
            self.assertIn("TICKET-4242", [e.name for e in ents.values()])
            self.assertTrue([r for r in rels if r.rel == "references"])
        finally:
            gr.VALUE_TYPES.pop("ticketish", None)

    def test_unknown_value_type_falls_back_to_text_instead_of_crashing(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules["relation_patterns"] = [{"name": "t", "regex": r"X:\s*(\S+)", "value": "오타종류",
                                       "node_type": "term", "in_chunk": {"rel": "references"}}]
        ents, _c, _r = _ex(rules).extract_chunk("X: abc", "", "d", "문서")
        self.assertIn("abc", [e.name for e in ents.values()])
        # 대신 lint 가 알려 준다
        issues = gr.lint(rules)["issues"]
        self.assertTrue([i for i in issues if i["level"] == "error" and "값 종류" in i["detail"]])


class NewValueTypesTest(unittest.TestCase):
    """이 코퍼스가 실제로 묻는 것 — "rev B1 에서 t_setup 은 몇 ns 인가?" """

    def setUp(self):
        self.ents, self.counts, self.rels = _ex().extract_chunk(
            "rev B1 에서 t_setup 은 4 ns 이고 잡음 여유는 1.5 dB 이다. 클럭은 100 MHz.", "", "d", "PHY 사양")

    def test_measurements_become_metric_nodes(self):
        got = _types(self.ents).get("metric") or []
        self.assertIn("4ns", got)
        self.assertIn("1.5dB", got)
        self.assertIn("100MHz", got)

    def test_revision_becomes_a_version_node(self):
        self.assertEqual(_types(self.ents).get("version"), ["rev B1"])

    def test_they_are_linked_to_the_document(self):
        rels = {r.rel for r in self.rels}
        self.assertIn("mentions_measure", rels)
        self.assertIn("mentions_version", rels)


class ChunkValuesTest(unittest.TestCase):
    def test_per_chunk_zero_turns_a_kind_off_from_the_file(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        for cv in rules["chunk_values"]:
            if cv["name"] == "version":
                cv["per_chunk"] = 0
        _e, _c, rels = _ex(rules).extract_chunk("rev B1 에서 4 ns", "", "d", "문서")
        self.assertNotIn("mentions_version", {r.rel for r in rels})
        self.assertIn("mentions_measure", {r.rel for r in rels})

    def test_per_chunk_caps_the_count(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        for cv in rules["chunk_values"]:
            if cv["name"] == "measure":
                cv["per_chunk"] = 2
        _e, _c, rels = _ex(rules).extract_chunk("1 ns, 2 ns, 3 ns, 4 ns, 5 ns", "", "d", "문서")
        self.assertEqual(len([r for r in rels if r.rel == "mentions_measure"]), 2)

    def test_old_file_without_the_section_keeps_the_old_behaviour(self):
        """`chunk_values` 절이 없는 예전 rules.json — 날짜·금액만, 관계명도 그대로."""
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules.pop("chunk_values")
        _e, _c, rels = _ex(rules).extract_chunk("2026년 8월 3일에 5억원을 쓰고 4 ns 를 맞췄다", "", "d", "문서")
        rels_set = {r.rel for r in rels}
        self.assertIn("mentions_date", rels_set)
        self.assertIn("mentions_amount", rels_set)
        self.assertNotIn("mentions_measure", rels_set)     # 새 종류는 선언하지 않으면 생기지 않는다


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.ex = _ex()

    def test_split_relation_names_are_merged(self):
        for alias in ("used", "use", "utilizes", "using"):
            self.assertEqual(self.ex.schema.rel(alias), "uses", alias)

    def test_entity_type_case_is_fixed(self):
        self.assertEqual(self.ex.schema.etype("CL"), "cl")
        self.assertEqual(self.ex.schema.etype("Issue"), "issue")

    def test_unknown_names_are_reported_not_lost_by_default(self):
        s = gr.Schema(gr.DEFAULT_RULES["schema"], gr.known_types(gr.DEFAULT_RULES))
        self.assertEqual(s.on_unknown, "keep")
        self.assertEqual(s.rel("없는관계"), "없는관계")       # keep — 잃지 않는다
        self.assertEqual(s.report()["unknown_rel_kinds"], 1)
        self.assertEqual(s.report()["unknown_rels"], [["없는관계", 1]])

    def test_drop_policy_actually_drops(self):
        raw = copy.deepcopy(gr.DEFAULT_RULES["schema"])
        raw["on_unknown"] = "drop"
        s = gr.Schema(raw, gr.known_types(gr.DEFAULT_RULES))
        self.assertIsNone(s.rel("없는관계"))
        self.assertEqual(s.rel("used"), "uses")            # 별칭은 여전히 모은다

    def test_inverse_pairs(self):
        self.assertEqual(self.ex.schema.inverse("fixes"), "fixed_by")
        self.assertEqual(self.ex.schema.inverse("fixed_by"), "fixes")
        self.assertEqual(self.ex.schema.inverse("co_occurs"), "co_occurs")   # 대칭
        self.assertEqual(self.ex.schema.inverse("mentions"), "")

    def test_no_schema_section_changes_nothing(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules.pop("schema")
        ex = _ex(rules)
        self.assertEqual(ex.schema.rel("아무관계나"), "아무관계나")
        self.assertEqual(ex.schema.etype("MyType"), "MyType")

    def test_relations_are_normalized_on_extraction(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules["relation_patterns"] = [{"name": "u", "regex": r"\*\*사용\*\*\s*[:：]\s*([^\n]+)", "value": "text",
                                       "node_type": "tech", "in_chunk": {"rel": "used"}}]   # 갈라진 이름
        _e, _c, rels = _ex(rules).extract_chunk("**사용**: GAA", "", "d", "문서")
        self.assertIn("uses", {r.rel for r in rels})
        self.assertNotIn("used", {r.rel for r in rels})


class FieldRelNamesTest(unittest.TestCase):
    def test_names_come_from_the_file_not_from_code(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules["relation_patterns"].append({"name": "custom", "regex": r"\*\*맞춤\*\*\s*[:：]\s*([^\n]+)",
                                           "value": "text", "node_type": "term",
                                           "in_chunk": {"rel": "references"}})
        names = _ex(rules).field_rel_names()
        for expect in ("owner", "responsible", "deadline", "decides", "references"):
            self.assertIn(expect, names, expect)


class LintTest(unittest.TestCase):
    def test_default_rules_have_no_errors(self):
        self.assertEqual(gr.lint(gr.DEFAULT_RULES)["counts"]["errors"], 0)

    def test_shipped_rules_file_has_no_errors(self):
        """실제 data/rules.json 이 빌드할 수 있는 상태여야 한다 (회귀 방지)."""
        self.assertEqual(gr.lint()["counts"]["errors"], 0)

    def test_broken_regex_is_an_error(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules["relation_patterns"][0]["regex"] = "([unclosed"
        issues = gr.lint(rules)["issues"]
        self.assertTrue([i for i in issues if i["level"] == "error" and "정규식" in i["detail"]])

    def test_unknown_entity_type_is_an_error(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules["entities"]["헛것"] = {"type": "relation", "aliases": []}
        issues = gr.lint(rules)["issues"]
        self.assertTrue([i for i in issues if i["level"] == "error" and "없는 type" in i["detail"]])

    def test_case_mismatch_suggests_the_right_spelling(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules["entities"]["헛것"] = {"type": "CL", "aliases": []}
        fix = [i["fix"] for i in gr.lint(rules)["issues"] if "없는 type" in i["detail"]][0]
        self.assertIn("'cl'", fix)

    def test_duplicate_alias_across_entities_is_an_error(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules["entities"]["A사"] = {"type": "org", "aliases": ["겹침"]}
        rules["entities"]["B사"] = {"type": "org", "aliases": ["겹침"]}
        issues = gr.lint(rules)["issues"]
        self.assertTrue([i for i in issues if i["level"] == "error" and "함께 가지고" in i["detail"]])

    def test_shadowed_link_rule_is_reported(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules["link_rules"] = [{"when_doc_type": "*", "target_type": "*", "rel": "references"},
                               {"when_doc_type": "cl", "target_type": "issue", "rel": "fixes"}]
        issues = gr.lint(rules)["issues"]
        self.assertTrue([i for i in issues if "가려져" in i["detail"]])

    def test_inverse_without_a_partner_is_reported(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules["schema"]["relations"]["짝없음"] = {"inverse": "어디에도없음"}
        issues = gr.lint(rules)["issues"]
        self.assertTrue([i for i in issues if "역관계" in i["detail"]])

    def test_bad_policy_is_an_error(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules["schema"]["on_unknown"] = "아무거나"
        self.assertTrue([i for i in gr.lint(rules)["issues"] if i["level"] == "error" and "정책" in i["detail"]])


class KnownTypesTest(unittest.TestCase):
    def test_declared_types_count_even_if_unused(self):
        rules = copy.deepcopy(gr.DEFAULT_RULES)
        rules["schema"]["entity_types"]["새타입"] = {"desc": "아직 아무 데도 안 쓴다"}
        self.assertIn("새타입", gr.known_types(rules))

    def test_wildcard_is_excluded(self):
        self.assertNotIn("*", gr.known_types(gr.DEFAULT_RULES))


class FillDefaultsTest(unittest.TestCase):
    def test_missing_schema_subkeys_are_filled_without_touching_user_entries(self):
        import tempfile
        from llmwiki import atomicio
        d = tempfile.mkdtemp()
        path = os.path.join(d, "rules.json")
        old = copy.deepcopy(gr.DEFAULT_RULES)
        old["schema"] = {"on_unknown": "map", "relations": {"fixes": {"desc": "내가 적은 설명"}}}
        atomicio.write_json(path, old)
        rep = gr.fill_defaults(path)
        got = json.loads(open(path, encoding="utf-8").read())
        self.assertEqual(got["schema"]["on_unknown"], "map")                    # 사람이 정한 정책은 유지
        self.assertEqual(got["schema"]["relations"]["fixes"]["desc"], "내가 적은 설명")  # 사람이 적은 항목도 유지
        self.assertIn("uses", got["schema"]["relations"])                        # 빠진 표준 어휘는 채운다
        self.assertTrue([a for a in rep["added"] if a.startswith("schema.relations.")])


class BuildWiringTest(unittest.TestCase):
    """규칙만 고쳐 놓고 빌드가 그것을 **실제로 쓰는지** 본다.

    추출기 단위 테스트만으로는 부족하다. 추출기가 관계를 잘 만들어도 저장은 graph_build 가 하고,
    거기에는 저장 조건·통계 분류·스키마 적용이 따로 있다 (예전에는 관계 이름이 코드에 박혀 있어서
    파일에 패턴을 더하면 통계가 틀어졌고, 다섯 개 이름은 끝점이 없어도 저장돼 dangling 관계가 됐다).
    그래서 여기서는 **빌드까지** 돌려 DB 에 실제로 들어갔는지 본다.
    """
    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        cls.tmp = tempfile.mkdtemp(prefix="llmwiki_gr_")
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(corpus)
        with open(os.path.join(corpus, "spec.md"), "w", encoding="utf-8") as f:
            f.write("---\ndoc_type: hw_design\next_id: HWD-PHY-1\n---\n\n"
                    "# PHY 타이밍\n\nrev B1 에서 t_setup 은 4 ns 이다.\n\n"
                    "**영향 모듈**: RX DMA\n\nISSUE-2001 을 참고한다.\n")
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"),
                     wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False)
        cls.p = Pipeline(s)
        cls.p.build(full=True)
        cls.types = {}
        for e in cls.p.store.entities(5000):
            cls.types.setdefault(e["type"], []).append(e["name"])
        cls.rels = {r["rel"] for r in cls.p.store.conn.execute("SELECT DISTINCT rel FROM relations")}
        cls.shutil = shutil

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        cls.shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_measure_and_version_nodes_survive_the_build(self):
        self.assertIn("4ns", self.types.get("metric") or [])
        self.assertIn("rev B1", self.types.get("version") or [])

    def test_their_relations_are_stored_not_dropped(self):
        self.assertIn("mentions_measure", self.rels)
        self.assertIn("mentions_version", self.rels)

    def test_a_field_pattern_relation_is_stored(self):
        # `**영향 모듈**: RX DMA` → affects_module. 예전 화이트리스트에 없던 이름이다.
        self.assertIn("affects_module", self.rels)

    def test_link_rule_relation_is_stored(self):
        self.assertTrue({"references", "fixes"} & self.rels, self.rels)

    def test_no_dangling_relations(self):
        """끝점이 없는 관계가 하나도 없어야 한다 — 옛 관계 이름 화이트리스트가 만들던 것."""
        n = self.p.store.conn.execute(
            "SELECT COUNT(*) FROM relations r WHERE NOT EXISTS(SELECT 1 FROM entities e WHERE e.entity_id=r.src)"
            " OR NOT EXISTS(SELECT 1 FROM entities e WHERE e.entity_id=r.dst)").fetchone()[0]
        self.assertEqual(n, 0)


class FieldRelStatsTest(unittest.TestCase):
    """빌드 통계의 '필드 관계 ↔ ID 관계' 분류가 **파일**을 따라야 한다.

    예전에는 그 목록이 graph_build.py 의 문자열 튜플이었다. 그래서 규칙 파일에 관계 패턴을 하나 더하면
    그 관계가 'ID 언급에서 나온 결정적 관계(id_relations)' 로 잘못 세어졌다 — 코드를 고치지 않는 한.
    """

    def test_a_new_pattern_relation_is_not_counted_as_an_id_relation(self):
        import shutil
        import tempfile
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        from llmwiki import graph_rules as g2
        tmp = tempfile.mkdtemp(prefix="llmwiki_grs_")
        try:
            corpus = os.path.join(tmp, "corpus")
            os.makedirs(corpus)
            with open(os.path.join(corpus, "d.md"), "w", encoding="utf-8") as f:
                # 너무 짧은 문서는 청크가 0개가 되어 그래프도 비어 버린다 — 본문을 충분히 준다
                f.write("---\ndoc_type: spec\next_id: SPEC-1\n---\n\n# 검증 사양\n\n"
                        "**검증자**: GAA\n\n"
                        + ("이 사양은 게이트 올 어라운드 공정에서의 검증 절차를 설명한다. "
                           "검증자는 공정 담당이 지정하며 결과는 주간 보고에 남긴다.\n") * 6)
            rules = copy.deepcopy(g2.DEFAULT_RULES)
            rules["relation_patterns"].append(
                {"name": "verifier", "regex": r"\*\*검증자\*\*\s*[:：]\s*([^\n]+)", "value": "entity",
                 "in_chunk": {"rel": "verified_by", "weight": 0.9, "confidence": 0.9, "desc": "검증자: "}})
            rules_path = os.path.join(tmp, "rules.json")
            from llmwiki import atomicio
            atomicio.write_json(rules_path, rules)
            os.environ["LLMWIKI_RULES_PATH"] = rules_path
            s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(tmp, "data"),
                         wiki_dir=os.path.join(tmp, "wiki"),
                         llm_provider="mock", embed_provider="hash", embed_dim=64)
            s.toggles = Toggles(llm_graph=False, community_summary=False)
            p = Pipeline(s)
            ex = g2.RuleExtractor(rules)
            self.assertIn("verified_by", ex.field_rel_names(),
                          "파일에 적은 관계가 '필드 관계' 로 분류되어야 한다")
            try:
                p.build(full=True)
                rels = {r["rel"] for r in p.store.conn.execute("SELECT DISTINCT rel FROM relations")}
                self.assertIn("verified_by", rels, "파일에 적은 관계가 저장되어야 한다")
            finally:
                p.store.close()
        finally:
            os.environ.pop("LLMWIKI_RULES_PATH", None)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
