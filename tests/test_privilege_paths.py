# -*- coding: utf-8 -*-
"""권한이 새는 **경로** — "한 문 앞에만 자물쇠를 달지 않았는가".

## 이 파일이 따로 있는 이유

이 저장소가 반복해서 겪은 실패는 늘 같은 모양이다.

  · 보호를 **한 창구에만** 넣는다 (Web 은 막았는데 MCP 는 안 막음)
  · 신분을 **일부 경로에만** 꿴다 (질의는 되는데 재실행·잡·콘솔은 admin)
  · 분류표에 **빠진 항목**이 안전한 쪽이 아니라 느슨한 쪽으로 떨어진다

기능 테스트로는 이런 것이 안 잡힌다. 고친 **함수**는 잘 동작하기 때문이다 — 문제는 그 함수를
**부르지 않는 길**이 남아 있다는 것이다. 그래서 여기서는 함수가 아니라 **경로**를 센다:
"질의를 실행하는 모든 길이 신분을 받는가", "overrides 를 받는 모든 길이 같은 필터를 지나는가".

참고: `docs/history/2026-09-19/QA_HARDENING_0919.md` · `docs/history/2026-09-19/CODEBASE_REVIEW_0919.md` · `llmwiki/auth.py` · `llmwiki/docacl.py`
"""
from __future__ import annotations

import io
import os
import re
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki import auth as A                    # noqa: E402
from llmwiki.auth import AuthError, classify_cli  # noqa: E402


def _src(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 1. overrides 화이트리스트
class OverrideWhitelistTest(unittest.TestCase):
    """`.env` 의 PAT 를 공격자 URL 로 보내는 길이 **한 창구라도** 남아 있으면 안 된다."""

    DANGEROUS = ("openai_base_url", "anthropic_base_url", "corpus_dirs", "data_dir", "db_path",
                 "logs_dir", "wiki_dir", "web_host", "web_port", "mcp_url")

    def test_dangerous_keys_are_denied_for_non_admin(self):
        for k in self.DANGEROUS:
            for role in ("viewer", "class3", "class2", "class1", "builder"):
                with self.assertRaises(AuthError, msg="%s 가 %s 에게 허용됐다" % (k, role)):
                    A.filter_overrides({k: "x"}, role, {})

    def test_admin_may_use_them(self):
        self.assertEqual(A.filter_overrides({"openai_base_url": "http://x"}, "admin", {}),
                         {"openai_base_url": "http://x"})

    def test_deny_list_beats_admin(self):
        cfg = {"overrides": {"deny": ["openai_base_url"]}}
        with self.assertRaises(AuthError):
            A.filter_overrides({"openai_base_url": "http://x"}, "admin", cfg)

    def test_allow_extra_opens_a_key_without_code_change(self):
        cfg = {"overrides": {"allow_extra": ["web_port"]}}
        self.assertEqual(A.filter_overrides({"web_port": 1}, "viewer", cfg), {"web_port": 1})

    def test_role_attr_keys(self):
        self.assertTrue(A.filter_overrides({"llm_roles": {"answer": {"model": "m"}}}, "viewer", {}))
        with self.assertRaises(AuthError):
            A.filter_overrides({"llm_roles": {"answer": {"base_url": "http://x"}}}, "viewer", {})

    def test_every_entry_point_uses_the_shared_filter(self):
        """Web 과 MCP **둘 다** auth 의 필터를 지나야 한다 — 예전에는 MCP 만 건너뛰었다."""
        web, mcp = _src("llmwiki/web/server.py"), _src("llmwiki/mcp.py")
        self.assertIn("_authmod.filter_overrides", web, "Web 이 공용 필터를 쓰지 않는다")
        self.assertIn("filter_overrides", mcp, "MCP 가 공용 필터를 쓰지 않는다")
        # MCP 에서 overrides 를 request_scope 로 넘기는 자리는 전부 safe_overrides 를 거쳐야 한다
        for m in re.finditer(r"request_scope\(overrides=([A-Za-z_][\w\.]*)", mcp):
            var = m.group(1)
            self.assertIn(var, ("ov",), "MCP 가 거르지 않은 변수(%s)를 request_scope 에 넘긴다" % var)
        self.assertIn("safe_overrides(pipe, args.get(\"overrides\")", mcp)
        self.assertIn("dict(safe_overrides(pipe, overrides))", mcp)


# ---------------------------------------------------------------- 2. CLI 등급 분류
class CliClassificationTest(unittest.TestCase):
    """분류표에 빠진 명령이 **느슨한 쪽**으로 떨어지면 콘솔이 admin 게이트의 뒷문이 된다."""

    def test_unknown_command_is_admin_not_edit(self):
        self.assertEqual(classify_cli(["nosuchcmd"])[0], "admin")
        self.assertEqual(classify_cli(["nosuchcmd", "sub"])[0], "admin")

    def test_schedule_write_paths_are_admin(self):
        # schedule 의 action 에는 python·cli 타입이 있어 임의 실행에 이른다 (scheduler.ACTION_TYPES)
        for argv in (["schedule", "add", "--task", "{}"], ["schedule", "run", "x"],
                     ["schedule", "trigger", "x"], ["schedule", "remove", "x"],
                     ["schedule", "enable", "x"], ["schedule", "disable", "x"]):
            self.assertEqual(classify_cli(argv)[0], "admin", "%s 가 admin 이 아니다" % " ".join(argv))

    def test_schedule_read_paths_stay_read(self):
        for argv in (["schedule", "list"], ["schedule", "show", "x"], ["schedule", "history"]):
            self.assertEqual(classify_cli(argv)[0], "read")

    def test_server_control_is_admin(self):
        for argv in (["server", "cancel", "t"], ["server", "block", "add", "ip", "1.2.3.4"],
                     ["server", "kick", "bob"], ["server", "maintenance", "on"], ["server", "limits", "set", "a=1"]):
            self.assertEqual(classify_cli(argv)[0], "admin", "%s 가 admin 이 아니다" % " ".join(argv))
        self.assertEqual(classify_cli(["server", "status"])[0], "read")

    def test_optimize_writes_a_file_only_as_admin(self):
        self.assertEqual(classify_cli(["optimize", "last"])[0], "read")
        self.assertEqual(classify_cli(["optimize", "last", "--out", "/tmp/x.md"])[0], "admin")

    def test_read_only_debug_commands_stay_read(self):
        for argv in (["inspect", "질의"], ["rerun", "12"], ["query", "x"], ["search", "x"]):
            self.assertEqual(classify_cli(argv)[0], "read")

    def test_every_cli_subcommand_is_classified(self):
        """`cli.py` 에 명령을 추가하고 등급표에 적지 않으면 여기서 잡힌다 (admin 으로 떨어지므로 안전하지만,
        의도한 등급인지 사람이 한 번 보게 만든다)."""
        cmds = sorted(set(re.findall(r'sub\.add_parser\("([a-z0-9_\-]+)"', _src("llmwiki/cli.py"))))
        self.assertGreater(len(cmds), 30)
        fell_through = [c for c in cmds if classify_cli([c])[0] == "admin" and c not in (
            "users", "security", "config", "models", "apikey", "serve", "mcp", "schedule", "server")]
        self.assertEqual(fell_through, [], "등급표에 없는 명령(=admin 으로 떨어짐): %s — auth.classify_cli 에 등급을 적으세요" % fell_through)


# ---------------------------------------------------------------- 3. 신분(actor) 배선
class ActorWiringTest(unittest.TestCase):
    """질의를 실행하는 **모든 길**이 신분을 받아야 한다. 하나만 빠져도 그 길로 전 문서가 열린다."""

    def test_pipeline_actor_defaults_to_admin(self):
        """기본값이 admin 이기 때문에 **빠뜨리면 조용히 열린다** — 이 사실을 테스트로 박아 둔다."""
        from llmwiki.pipeline import Pipeline
        self.assertEqual(Pipeline.actor.fget(_FakePipe()), {"user": "", "role": "admin", "origin": ""})

    def test_query_running_paths_pass_actor(self):
        web = _src("llmwiki/web/server.py")
        # 질의·해부·채널검색·재실행·잡·콘솔·MCP — 일곱 자리 전부
        for needle, why in (
            ("self._do_query(body, q, ov, actor=", "/api/query"),
            ("_qd.inspect_query(p, _as_str(body.get(\"q\")", "/api/debug/query"),
            ("actor=_actor_from_client(client)):\n                        res, tr = p.rerun", "/api/query/rerun"),
            ("with pipe.request_scope(actor=_actor_from_client(client)):", "잡(eval·sweep·precompute)"),
            ("with p.request_scope(actor=_actor_from_client(client)):", "/api/cli 콘솔"),
            ("dict(_actor_of(user), origin=\"mcp\")", "/mcp"),
        ):
            self.assertIn(needle.split("\n")[0], web, "%s 경로에 신분이 꿰여 있지 않다" % why)

    def test_no_bare_request_scope_in_query_paths(self):
        """`request_scope()` 를 신분 없이 여는 자리는 **내부 호출만** 남아야 한다 (워처·GET 읽기)."""
        web = _src("llmwiki/web/server.py")
        bare = [m.start() for m in re.finditer(r"request_scope\(\)", web)]
        # 워처 루프 · GET 핸들러 · POST 전처리 세 자리만 허용 (숫자가 늘면 새 경로가 신분 없이 열린 것)
        self.assertLessEqual(len(bare), 3,
                             "신분 없이 여는 request_scope() 가 %d 곳 — 새로 생긴 자리가 질의를 실행하는지 확인하세요" % len(bare))

    def test_replay_path_has_doc_acl(self):
        """재실행(재생)은 저장된 컨텍스트 본문을 쓴다 — 여기에 접근 제어가 없으면 통째로 새어 나간다."""
        qe = _src("llmwiki/query_engine.py")
        i = qe.index("def _replay_retrieval")
        body = qe[i:i + 4000]
        self.assertIn("acl_filter()", body, "재생 경로에 doc_acl 이 없다")
        self.assertIn("prof.stage(\"doc_acl\"", body)


class _FakePipe:
    """`Pipeline.actor` 의 기본값만 확인하기 위한 최소 대역 (스레드 로컬이 비어 있는 상태)."""

    class _TLS:
        pass

    def __init__(self):
        self._tls = _FakePipe._TLS()


# ---------------------------------------------------------------- 4. 캐시 구획
class CacheVisibilityTest(unittest.TestCase):
    """캐시가 맞으면 doc_acl 은 실행되지 않는다 — 키에 가시성이 없으면 접근 제어가 캐시 하나로 무너진다."""

    @classmethod
    def setUpClass(cls):
        import json as _json
        from llmwiki.config import Settings, Toggles
        from llmwiki.pipeline import Pipeline
        cls.tmp = tempfile.mkdtemp()
        corpus = os.path.join(cls.tmp, "corpus")
        os.makedirs(os.path.join(corpus, "hr"))
        with open(os.path.join(corpus, "hr", "pay.md"), "w", encoding="utf-8") as f:
            f.write("# 급여\n\n성과급 340%.\n")
        cls.acl = os.path.join(cls.tmp, "docacl.json")
        with open(cls.acl, "w", encoding="utf-8") as f:
            _json.dump({"enabled": True, "default_min_role": "viewer",
                        "rules": [{"prefix": "corpus/hr/", "min_role": "class1"}]}, f)
        s = Settings(corpus_dirs=[corpus], data_dir=os.path.join(cls.tmp, "data"),
                     wiki_dir=os.path.join(cls.tmp, "wiki"),
                     llm_provider="mock", embed_provider="hash", embed_dim=64)
        s.toggles = Toggles(llm_graph=False, community_summary=False)
        cls.p = Pipeline(s)

    @classmethod
    def tearDownClass(cls):
        cls.p.store.close()
        os.environ.pop("LLMWIKI_DOCACL_PATH", None)
        from llmwiki import docacl as _d
        _d.load(force=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _keys(self):
        out = {}
        for role in ("viewer", "class2", "class1", "admin"):
            with self.p.request_scope(actor={"user": "u", "role": role, "origin": "t"}):
                out[role] = (self.p.visibility_key(), self.p._cache_key("성과급은?"))
        return out

    def test_no_rules_means_one_partition(self):
        """규칙이 없으면 예전과 같은 적중률을 유지한다 — 보안 때문에 캐시를 못 쓰게 만들지 않는다."""
        os.environ.pop("LLMWIKI_DOCACL_PATH", None)
        from llmwiki import docacl as _d
        _d.load(force=True)
        k = self._keys()
        self.assertEqual({v[0] for v in k.values()}, {"all"})
        self.assertEqual(len({v[1] for v in k.values()}), 1, "규칙이 없는데 캐시가 역할별로 쪼개졌다")

    def test_rules_split_the_cache_by_role(self):
        os.environ["LLMWIKI_DOCACL_PATH"] = self.acl
        from llmwiki import docacl as _d
        _d.load(force=True)
        k = self._keys()
        self.assertNotEqual(k["viewer"][1], k["admin"][1], "admin 의 답이 viewer 에게 그대로 돌아간다")
        self.assertNotEqual(k["viewer"][1], k["class1"][1])
        # 사용자별이 아니라 **역할별** 이어야 한다 (30명 환경에서 적중률을 지키기 위해)
        with self.p.request_scope(actor={"user": "다른사람", "role": "viewer", "origin": "t"}):
            self.assertEqual(self.p._cache_key("성과급은?"), k["viewer"][1],
                             "같은 역할인데 사용자마다 캐시가 갈린다 — 적중률이 무너진다")

    def test_answer_signature_carries_visibility(self):
        os.environ["LLMWIKI_DOCACL_PATH"] = self.acl
        from llmwiki import docacl as _d
        _d.load(force=True)
        with self.p.request_scope(actor={"user": "u", "role": "viewer", "origin": "t"}):
            v = self.p.answer_signature()
        self.assertEqual(v.get("vis"), "role:viewer", "사전계산 캐시 키에 가시성이 없다")


if __name__ == "__main__":
    unittest.main(verbosity=2)
