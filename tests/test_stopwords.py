# -*- coding: utf-8 -*-
"""불용어 파일 (stopwords.json, docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918.md §2.2).

지키려는 것 세 가지.
  1) 파일이 없으면 코드 기본 목록으로 **생성**된다 (prompts 와 같은 방식).
  2) 파일을 고치면 재시작 없이 keywords() 에 반영된다 (mtime 캐시).
  3) 깨진 파일은 기본 목록으로 폴백하고 예외를 내지 않는다.
경로는 LLMWIKI_STOPWORDS_PATH 로 임시 폴더에 두어 저장소의 stopwords.json 을 건드리지 않는다.
"""
from __future__ import annotations

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

from llmwiki import textutil as tu                       # noqa: E402
from llmwiki.config import path_for                       # noqa: E402


def _touch_newer(path: str) -> None:
    """mtime 이 확실히 바뀌도록 (같은 초에 두 번 쓰는 경우 대비)."""
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 1.0))


class StopwordsFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="llmwiki_sw_")
        self.path = os.path.join(self.tmp, "stopwords.json")
        self._old_env = os.environ.get("LLMWIKI_STOPWORDS_PATH")
        os.environ["LLMWIKI_STOPWORDS_PATH"] = self.path
        tu._STOP_CACHE.clear()

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("LLMWIKI_STOPWORDS_PATH", None)
        else:
            os.environ["LLMWIKI_STOPWORDS_PATH"] = self._old_env
        tu._STOP_CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_path_registry(self) -> None:
        self.assertEqual(os.path.normpath(path_for("stopwords")), os.path.normpath(self.path))
        self.assertEqual(os.path.normpath(tu.stopwords_path()), os.path.normpath(self.path))

    def test_default_file_created_when_missing(self) -> None:
        self.assertFalse(os.path.exists(self.path))
        sw = tu.load_stopwords()
        self.assertTrue(os.path.exists(self.path), "없는 파일은 기본값으로 생성되어야 한다")
        self.assertEqual(sw, tu.DEFAULT_STOPWORDS)
        with open(self.path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        self.assertIn("_comment", obj)
        self.assertEqual(obj["stopwords"], sorted(tu.DEFAULT_STOPWORDS))
        # 기본 목록이 실제로 keywords() 에서 빠진다
        self.assertNotIn("무엇", tu.keywords("PDCCH 디코딩 실패는 무엇 때문인가"))
        self.assertIn("pdcch", tu.keywords("PDCCH 디코딩 실패는 무엇 때문인가"))

    def test_edit_reloads_without_restart(self) -> None:
        tu.load_stopwords()   # 생성
        self.assertIn("디코딩", tu.keywords("PDCCH 디코딩 실패"))
        time.sleep(0.05)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"stopwords": sorted(tu.DEFAULT_STOPWORDS) + ["디코딩"]}, f, ensure_ascii=False)
        _touch_newer(self.path)
        kws = tu.keywords("PDCCH 디코딩 실패")
        self.assertNotIn("디코딩", kws, "파일 수정이 재시작 없이 반영되어야 한다")
        self.assertIn("pdcch", kws)
        # 호환용 STOPWORDS 이름도 현재 파일을 본다
        self.assertIn("디코딩", tu.STOPWORDS)
        self.assertEqual(len(tu.STOPWORDS), len(tu.DEFAULT_STOPWORDS) + 1)
        # 다시 줄이면 되살아난다
        time.sleep(0.05)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"stopwords": ["pdcch"]}, f, ensure_ascii=False)
        _touch_newer(self.path)
        _touch_newer(self.path)
        kws = tu.keywords("PDCCH 디코딩 실패는 무엇")
        self.assertIn("디코딩", kws)
        self.assertIn("무엇", kws, "파일에서 뺀 단어는 더 이상 불용어가 아니다")
        self.assertNotIn("pdcch", kws)

    def test_broken_json_falls_back_to_defaults(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"stopwords": ["a", ')     # 잘린 JSON
        sw = tu.load_stopwords()
        self.assertEqual(sw, tu.DEFAULT_STOPWORDS)
        self.assertNotIn("무엇", tu.keywords("무엇 PDCCH"))
        # 형식 오류(문자열이 아닌 항목)도 기본값
        time.sleep(0.05)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"stopwords": ["a", 1, None]}, f)
        _touch_newer(self.path)
        _touch_newer(self.path)
        self.assertEqual(tu.load_stopwords(), tu.DEFAULT_STOPWORDS)
        # 깨진 파일은 덮어쓰지 않는다 (운영자가 고칠 수 있게)
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertIn("1", f.read())


if __name__ == "__main__":
    unittest.main()
