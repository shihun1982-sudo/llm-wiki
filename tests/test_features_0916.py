# -*- coding: utf-8 -*-
"""2026-09-16 (2차) 기능: 요청 이력·결과 보관 · 협업(휘발성 채팅 + 게시판) · 규칙 확장(다단·중복) · 모델 카탈로그(embed/rerank).

각 테스트는 "예전에는 무엇이 잘못됐나" 를 주석으로 남긴다 — 회귀가 나면 왜 이 검사가 있는지 바로 알 수 있게.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from llmwiki.config import Settings, Toggles          # noqa: E402
from llmwiki.store import Store                        # noqa: E402


class RequestHistoryTest(unittest.TestCase):
    """요청 이력: 사용자 열 · 결과 보관 파일 · DB 가 잘려도 다시 보기."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lwreq_")
        self.store = Store(os.path.join(self.tmp, "t.sqlite3"))
        self.archive = os.path.join(self.tmp, "requests")

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_user_column_and_filter(self):
        a = self.store.log_request("query", "A 의 질문", {"ms": 1}, {"answer": "a"}, None, user="alice")
        b = self.store.log_request("query", "B 의 질문", {"ms": 2}, {"answer": "b"}, None, user="bob")
        self.assertTrue(a and b)
        # 내 것만 — 예전에는 origin 에 "web alice" 처럼 한 문자열이라 걸러낼 수 없었다
        mine = self.store.requests(user="alice")
        self.assertEqual([r["id"] for r in mine], [a])
        self.assertEqual(mine[0]["user"], "alice")
        self.assertEqual(len(self.store.requests()), 2)
        # 요약 검색
        self.assertEqual([r["id"] for r in self.store.requests(q="B 의")], [b])

    def test_result_is_archived_and_survives_db_prune(self):
        rid = self.store.log_request("query", "보관 확인", {"ms": 3}, {"answer": "답", "query": "보관 확인"},
                                     None, user="alice", archive_dir=self.archive)
        row = self.store.get_request(rid)
        self.assertTrue(row["file"] and os.path.exists(row["file"]))
        # DB 행이 keep_requests 로 잘려도 "그때 그 답" 은 남아야 한다 (사용자에게 가장 나쁜 실패를 막는다)
        self.store.conn.execute("DELETE FROM requests WHERE id=?", (rid,))
        self.store.conn.commit()
        self.assertIsNone(self.store.get_request(rid))                     # 보관 폴더를 안 주면 없다
        back = self.store.get_request(rid, archive_dir=self.archive)       # 주면 파일에서 복원
        self.assertTrue(back and back.get("from_archive"))
        self.assertEqual(back["result"]["answer"], "답")

    def test_prune_archive_respects_keep_days(self):
        rid = self.store.log_request("query", "오래된 것", {"ms": 1}, {"answer": "x"}, None, archive_dir=self.archive)
        path = self.store.get_request(rid)["file"]
        os.utime(path, (time.time() - 100 * 86400, time.time() - 100 * 86400))
        self.assertEqual(self.store.prune_request_archive(self.archive, 0)["skipped"], True)   # 0 = 지우지 않음
        self.assertTrue(os.path.exists(path))
        r = self.store.prune_request_archive(self.archive, 90)
        self.assertEqual(r["removed"], 1)
        self.assertFalse(os.path.exists(path))

    def test_requests_dir_resolves_under_data_dir(self):
        """`data/requests` 같은 상대 경로는 data_dir 아래로 본다 — 격리 환경에서도 따라가야 한다."""
        s = Settings(data_dir=os.path.join(self.tmp, "d"), requests_dir="data/requests")
        self.assertEqual(s.requests_archive_dir(), os.path.join(s.data_dir, "requests"))
        s2 = Settings(data_dir=os.path.join(self.tmp, "d"), requests_dir="")
        self.assertEqual(s2.requests_archive_dir(), "")                   # 비우면 보관 안 함
        s3 = Settings(data_dir=os.path.join(self.tmp, "d"), requests_dir=os.path.join(self.tmp, "abs"))
        self.assertEqual(s3.requests_archive_dir(), os.path.join(self.tmp, "abs"))


class CollabTest(unittest.TestCase):
    """협업: 채팅은 휘발성, /게시 한 것만 남는다. 본체에 영향을 주지 않아야 한다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lwcol_")
        self.s = Settings(data_dir=self.tmp)
        from llmwiki import collab as cb
        cb.clear()
        cb._PRESENCE.clear()
        self.cb = cb

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_chat_is_volatile_and_command_is_parsed(self):
        r = self.cb.say("alice", "안녕하세요")
        self.assertTrue(r["ok"])
        self.assertIsNone(r["command"])                       # 평범한 말은 명령이 아니다
        st = self.cb.state("alice")
        self.assertEqual([m["text"] for m in st["messages"]], ["안녕하세요"])
        # /게시 만 명령
        self.assertEqual(self.cb.parse_command("/게시 AGC 질의 이상")["title"], "AGC 질의 이상")
        self.assertEqual(self.cb.parse_command("/post hello")["name"], "post")
        self.assertIsNone(self.cb.parse_command("게시 합니다"))
        # 보관 시간이 지나면 사라진다 (휘발성)
        self.cb._MSGS[0]["ts"] = time.time() - 10 * 3600
        self.assertEqual(self.cb.state("alice", 0)["messages"], [])

    def test_board_persists_and_links_request(self):
        r = self.cb.post(self.s, "alice", "피드백", "AGC 답이 이상합니다", request_id=42, kind="feedback")
        self.assertTrue(r["ok"])
        pid = r["post"]["id"]
        self.assertEqual(r["post"]["request_id"], 42)          # 어떤 작업에 대한 피드백인지 연결된다
        self.assertTrue(os.path.exists(self.cb.board_path(self.s)))
        self.assertEqual(len(self.cb.board(self.s)["posts"]), 1)
        self.cb.reply(self.s, pid, "bob", "확인했습니다")
        self.assertEqual(len(self.cb.board(self.s)["posts"][0]["replies"]), 1)
        self.cb.resolve(self.s, pid, "bob", True)
        self.assertTrue(self.cb.board(self.s)["posts"][0]["resolved"])
        self.assertEqual(self.cb.board(self.s, unresolved_only=True)["posts"], [])
        self.assertTrue(self.cb.remove(self.s, pid)["ok"])
        self.assertEqual(self.cb.board(self.s)["posts"], [])
        self.assertIn("error", self.cb.remove(self.s, pid))     # 없는 글

    def test_bubble_font_grows_with_time_and_is_capped(self):
        """머문 시간에 따라 말풍선이 커지되 상한을 넘지 않는다. 값은 전부 설정에서 온다."""
        cfg = {"bubble_font_start_px": 10, "bubble_font_step_px": 2, "bubble_font_step_min": 30, "bubble_font_max_px": 20}
        now = time.time()
        self.assertEqual(self.cb.font_px(now, cfg), 10)                    # 방금
        self.assertEqual(self.cb.font_px(now - 30 * 60, cfg), 12)          # 30분 → +2
        self.assertEqual(self.cb.font_px(now - 90 * 60, cfg), 16)          # 90분 → +6
        self.assertEqual(self.cb.font_px(now - 100 * 3600, cfg), 20)       # 상한

    def test_presence_position_is_relative_and_clamped(self):
        p = self.cb.touch("alice", 0.3, 0.7)
        self.assertEqual((p["x"], p["y"]), (0.3, 0.7))
        self.assertTrue(p["emoji"])
        p2 = self.cb.touch("alice", 5.0, -1.0)                 # 화면 밖으로 끌어도 0~1 로 잡아 둔다
        self.assertEqual((p2["x"], p2["y"]), (1.0, 0.0))
        self.assertEqual(p2["since"], p["since"])              # 머문 시간은 유지 (말풍선 크기의 근거)

    def test_icon_comes_from_ip_and_cannot_be_chosen(self):
        """아이콘은 **접속 IP 의 SHA-1** 로 정해진다 — 랜덤도 아니고 고를 수도 없다.
        같은 자리(같은 PC)에서 오면 늘 같은 아이콘이라 '저건 누구 자리' 가 눈에 익는다."""
        a = self.cb.default_emoji("10.1.2.3")
        self.assertEqual(a, self.cb.default_emoji("10.1.2.3"))          # 결정적 (몇 번을 물어도 같다)
        self.assertIn(a, self.cb.EMOJI)
        # 서로 다른 IP 는 목록 전체에 고르게 퍼진다. 32종이라 두 IP 가 겹치는 일(1/32)은 정상이므로
        # 특정 두 개를 비교하지 않고 분포를 본다.
        got = {self.cb.default_emoji("10.1.2.%d" % i) for i in range(1, 121)}
        self.assertGreaterEqual(len(got), len(self.cb.EMOJI) // 2, "아이콘이 한쪽으로 쏠린다: %s" % sorted(got))
        p = self.cb.touch("bob", ip="10.1.2.3")
        self.assertEqual(p["emoji"], a)
        # 자리를 옮기면(IP 변경) 아이콘도 그 자리의 것으로
        other = next(ip for ip in ("10.9.9.9", "172.16.4.4", "192.168.5.5") if self.cb.default_emoji(ip) != a)
        self.assertEqual(self.cb.touch("bob", ip=other)["emoji"], self.cb.default_emoji(other))
        # 목록에 동물·캐릭터가 없다 (엔지니어 도구만)
        self.assertFalse(set(self.cb.EMOJI) & {"🐨", "🐱", "🐶", "🦊", "🐻", "🐼", "🐞"})
        # 남의 IP 는 화면으로 나가지 않는다
        st = self.cb.state("bob")
        self.assertTrue(all("ip_key" not in q for q in st["people"]))
        self.assertNotIn("emoji_choices", st)                            # 고르기 선택지를 주지 않는다

    def test_disabled_toggle_turns_it_off(self):
        """부수 기능이므로 토글 하나로 완전히 꺼져야 한다."""
        s = Settings(data_dir=self.tmp, toggles=Toggles(collab=False))
        self.assertFalse(self.cb.enabled(s))
        self.assertTrue(self.cb.enabled(Settings(data_dir=self.tmp)))

    def test_collab_is_not_imported_by_query_path(self):
        """본체(질의·빌드·MCP)가 이 모듈을 import 하지 않는다 — 고장이 번지지 않게."""
        import re
        for mod in ("pipeline.py", "query_engine.py", "mcp.py", "store.py", "answer.py", "retrieval.py"):
            src = open(os.path.join(ROOT, "llmwiki", mod), encoding="utf-8").read()
            self.assertIsNone(re.search(r"import\s+collab|from\s+\.\s*collab", src), "%s 가 collab 을 import 합니다" % mod)


class QueryRulesExpansionTest(unittest.TestCase):
    """규칙 확장: 사슬(다단)과 한 낱말의 여러 유형이 모두 동작해야 한다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lwqr_")
        self.path = os.path.join(self.tmp, "query_rules.json")
        os.environ["LLMWIKI_QUERY_RULES_PATH"] = self.path
        from llmwiki import query_rules as qr
        qr._CACHE.update(mtime=None, rules=None, index=None)
        self.qr = qr
        qr.save_rules({
            "acronym": {"TAT": ["Turn Around Time"], "AGC": ["Automatic Gain Control"]},
            "synonym": {"Turn Around Time": ["응답시간"], "AGC": ["gain control loop"]},
            "alias": {}, "related": {}, "exclude": {}, "compound": {},
        })

    def tearDown(self):
        os.environ.pop("LLMWIKI_QUERY_RULES_PATH", None)
        self.qr._CACHE.update(mtime=None, rules=None, index=None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_chain_expands_across_rounds(self):
        """TAT → Turn Around Time(acronym) → 응답시간(synonym).
        1회만 적용하던 예전에는 두 번째 규칙이 조용히 무시됐다 (사용자가 겪은 문제)."""
        r = self.qr.expand("TAT 가 왜 늘었나", max_rounds=2)
        kinds = {(f["type"], f["matched"]) for f in r["fired"]}
        self.assertIn(("acronym", "TAT"), kinds)
        self.assertIn(("synonym", "Turn Around Time"), kinds)
        self.assertIn("응답시간", r["fts_query"])
        # 라운드를 1 로 두면 예전 동작 (사슬이 끊긴다)
        r1 = self.qr.expand("TAT 가 왜 늘었나", max_rounds=1)
        self.assertNotIn("응답시간", r1["fts_query"])

    def test_same_word_fires_every_type(self):
        """AGC 는 acronym 이면서 synonym 이다. 예전에는 먼저 걸린 하나만 살아남았다."""
        r = self.qr.expand("AGC 수렴", max_rounds=1)
        fired = [f for f in r["fired"] if f["matched"].upper() == "AGC"]
        self.assertEqual({f["type"] for f in fired}, {"acronym", "synonym"})
        self.assertIn(["gain control loop"], [f["values"] for f in fired if f["type"] == "synonym"])
        # acronym 확장어는 구문(phrase)으로, synonym 확장어는 토큰으로 FTS 에 들어간다 (설계)
        self.assertIn('"Automatic Gain Control"', r["fts_query"])
        for tok in ('"gain"', '"control"', '"loop"'):
            self.assertIn(tok, r["fts_query"])

    def test_lint_finds_alias_chain_and_duplicates(self):
        self.qr.save_rules({"acronym": {"A": ["Alpha"]}, "synonym": {}, "related": {}, "exclude": {}, "compound": {},
                            "alias": {"가": "나", "나": "다"}})
        self.qr._CACHE.update(mtime=None, rules=None, index=None)
        r = self.qr.lint()
        self.assertFalse(r["ok"])
        self.assertTrue(any(i["kind"] == "alias_chain" for i in r["issues"]))

    def test_merge_adds_without_losing_existing(self):
        before = self.qr.load_rules()["acronym"]["TAT"]
        r = self.qr.merge_rules({"acronym": {"TAT": ["TAT(응답시간)"], "PA": ["Power Amplifier"]},
                                 "_comment_x": "설명 키는 무시된다"})
        self.assertEqual(r["merged"]["acronym"]["added"], 1)       # PA 가 새로
        after = self.qr.load_rules()["acronym"]
        self.assertEqual(after["TAT"][: len(before)], before)      # 기존 값은 남는다
        self.assertIn("PA", after)
        self.assertNotIn("_comment_x", after)


class ModelCatalogTest(unittest.TestCase):
    """모델 카탈로그: 임베딩·리랭크도 목록에서 고를 수 있어야 한다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lwcat_")
        os.environ["LLMWIKI_MODELS_PATH"] = os.path.join(self.tmp, "models.json")
        from llmwiki import models_catalog as mc
        mc.load_catalog(force=True)
        self.mc = mc

    def tearDown(self):
        os.environ.pop("LLMWIKI_MODELS_PATH", None)
        self.mc.load_catalog(force=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_catalog_has_three_kinds_and_add_remove(self):
        cat = self.mc.load_catalog(force=True)
        for sect in ("models", "embed", "rerank"):
            self.assertTrue(cat.get(sect), "%s 절이 비어 있습니다" % sect)
        self.mc.add_model({"id": "my-reranker", "provider": "cohere", "kind": "rerank"})
        self.assertTrue(any(m["id"] == "my-reranker" for m in self.mc.load_catalog(force=True)["rerank"]))
        self.mc.remove_model("my-reranker")
        self.assertFalse(any(m["id"] == "my-reranker" for m in self.mc.load_catalog(force=True)["rerank"]))

    def test_describe_reports_embed_and_rerank_in_use(self):
        """예전에는 역할 LLM 만 '지금 쓰는 모델' 에 나와서 임베딩·리랭크 모델이 화면에서 아예 안 보였다."""
        s = Settings(embed_provider="ollama", embed_model="bge-m3", rerank_url="http://x/v1/rerank",
                     rerank_api_model="BAAI/bge-reranker-v2-m3")
        d = self.mc.describe(s)
        self.assertIn("embed", d["in_use"])
        self.assertIn("rerank_api", d["in_use"])
        self.assertEqual(d["in_use"]["embed"]["model"], "bge-m3")
        self.assertEqual(d["in_use"]["rerank_api"]["model"], "BAAI/bge-reranker-v2-m3")
        self.assertTrue(all(v.get("source") for v in d["in_use"].values()))   # 값이 어디서 왔는지 표시
        self.assertTrue(d["rerank_providers"])

    def test_old_catalog_without_rerank_section_still_loads(self):
        with open(os.environ["LLMWIKI_MODELS_PATH"], "w", encoding="utf-8") as f:
            json.dump({"models": [{"id": "x", "provider": "ollama"}], "embed": []}, f)
        cat = self.mc.load_catalog(force=True)
        self.assertTrue(cat["rerank"])           # 파일을 고치지 않고 기본 목록으로 채워 준다


if __name__ == "__main__":
    unittest.main()
