# -*- coding: utf-8 -*-
"""터미널 출력 인코딩 (llmwiki/console.py) — 다른 환경에서 한글·기호가 깨지거나 명령이 죽지 않는지.

재현했던 증상: Windows 의 로캘 코드페이지가 cp949/cp1252 인 상태에서 출력을 파일·파이프로 리디렉션하면
`UnicodeEncodeError: 'cp949' codec can't encode character '\\u2714'` 로 CLI 가 중간에 죽고, 한글도 깨져 나왔다.
여기서는 좁은 인코딩(PYTHONIOENCODING=ascii/cp949)을 강제한 자식 프로세스로 실제 CLI 를 돌려 회귀를 막는다.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llmwiki import console as C  # noqa: E402

SYMBOLS = "한글 ⏳ ✔ ✘ · → ⚠ ■ ▶"
# 자식 프로세스는 부모의 `config.set_path_fallback` 을 물려받지 못한다 — 로그·원장은 환경변수로 **반드시** 격리한다 (2026-09-24).
# 예전에는 `query` 가 프로젝트의 실제 config.json·색인으로 돌아 실사용 requests 표에 행을 남기고(실행마다 1건) 로그를 섞었다.
_TMP = tempfile.mkdtemp(prefix="lwconsole_")
_ISO_ENV = {"LLMWIKI_LOGS_DIR_PATH": os.path.join(_TMP, "logs"), "LLMWIKI_LEDGER_DIR_PATH": os.path.join(_TMP, "ledger")}


def _run(argv, env_extra=None, timeout=180):
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env.update(_ISO_ENV)
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, "-m", "llmwiki"] + argv, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def _isolated_config():
    """질의를 실제로 돌리는 테스트용: 임시 폴더에 문서 1개 · mock LLM · hash 임베더 · 임시 data/wiki 를 가리키는 config."""
    d = os.path.join(_TMP, "iso")
    if os.path.isdir(d):
        return os.path.join(d, "config.json")
    os.makedirs(os.path.join(d, "corpus"))
    with open(os.path.join(d, "corpus", "ISSUE-2001.md"), "w", encoding="utf-8") as f:
        f.write("---\nid: ISSUE-2001\ndoc_type: issue\ntitle: 인터럽트 지연\n---\n# ISSUE-2001\n## 현상\n인터럽트 지연이 3ms 를 넘는다.\n## 원인\nDMA 큐 오버런.\n")
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.update({"data_dir": os.path.join(d, "data"), "wiki_dir": os.path.join(d, "wiki"), "corpus_dirs": [os.path.join(d, "corpus")],
                "llm_provider": "mock", "llm_roles": {}, "embed_provider": "hash", "embed_model": "", "embed_dim": 256})
    cfg["toggles"] = dict(cfg.get("toggles") or {}, auto_build=False, precompute=False, query_cache=False, health_check=False, wiki_pages=False)
    path = os.path.join(d, "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    return path


class ConsoleEncodingTest(unittest.TestCase):
    def test_setup_reports_state(self):
        d = C.describe()
        for k in ("mode", "console", "stdout_encoding", "locale_encoding", "safe"):
            self.assertIn(k, d)

    def test_safe_downgrades_unrepresentable_symbols(self):
        class _S:
            encoding = "ascii"
            errors = "strict"
        out = C.safe(SYMBOLS, _S())
        self.assertNotIn("⏳", out)
        self.assertIn("OK", out)          # ✔ → OK
        out.encode("ascii")               # ascii 로 인코딩 가능해야 한다

    def test_cli_survives_narrow_encoding(self):
        """PYTHONIOENCODING=ascii 로 강제해도 CLI 가 죽지 않고 UTF-8 한글을 출력한다 (예전에는 UnicodeEncodeError 로 죽었다)."""
        for enc in ("ascii", "cp949" if os.name == "nt" else "latin-1"):
            code, out, err = _run(["corpus", "types"], {"PYTHONIOENCODING": enc})
            self.assertEqual(code, 0, "PYTHONIOENCODING=%s 에서 종료 코드 %s: %s" % (enc, code, err[-400:]))
            self.assertNotIn(b"UnicodeEncodeError", out + err)
            text = out.decode("utf-8")     # UTF-8 로 디코딩되어야 한다 (깨진 바이트가 아님)
            self.assertTrue(any("가" <= ch <= "힣" for ch in text), "한글이 출력되지 않았다: %r" % text[:200])

    def test_cli_progress_symbols_on_stderr(self):
        """진행 표시(⏳)는 stderr 로 나간다 — 좁은 인코딩에서도 죽지 않아야 한다."""
        cfg = _isolated_config()
        code_b, out_b, err_b = _run(["build"], {"LLMWIKI_CONFIG_PATH": cfg, "PYTHONIOENCODING": "ascii"})
        self.assertEqual(code_b, 0, err_b[-400:])
        code, out, err = _run(["query", "ISSUE-2001 원인", "--no-log"], {"PYTHONIOENCODING": "ascii", "LLMWIKI_LLM_PROVIDER": "mock", "LLMWIKI_CONFIG_PATH": cfg})
        self.assertEqual(code, 0, err[-400:])
        self.assertNotIn(b"UnicodeEncodeError", out + err)
        out.decode("utf-8")
        err.decode("utf-8")

    def test_console_encoding_off_and_native(self):
        """설정으로 동작을 바꿀 수 있다 (native = 코드페이지를 건드리지 않고 표현 불가 문자만 ?)."""
        code, out, err = _run(["corpus", "types"], {"LLMWIKI_CONSOLE_ENCODING": "native", "PYTHONIOENCODING": "ascii"})
        self.assertEqual(code, 0, err[-300:])
        self.assertNotIn(b"UnicodeEncodeError", out + err)
        code2, out2, err2 = _run(["config", "paths"], {"LLMWIKI_CONSOLE_ENCODING": "off"})
        self.assertEqual(code2, 0, err2[-300:])

    def test_health_reports_console(self):
        """좁은 콘솔 인코딩에서 health 가 **완주**하고 console_encoding 행을 낸다.
        exit 0 을 요구하지 않는다: 이 PC 의 config.json 이 headless 에이전트(opencode 등)를 가리키는데 그 실행 파일이 PATH 에 없으면
        health 는 정당하게 FAIL(exit 1) 이다 — 테스트 목적은 인코딩 회귀이므로 '요약 줄 존재 + Traceback 없음' 으로 판정한다."""
        code, out, err = _run(["health", "--quick"], {"PYTHONIOENCODING": "ascii"})
        text = out.decode("utf-8")
        self.assertIn(code, (0, 1), err[-300:])
        self.assertRegex(text, r"(?m)^health: ")
        self.assertNotIn(b"Traceback", out + err)
        self.assertIn("console_encoding", text)

    def test_project_text_files_are_utf8(self):
        """포팅 환경에서 깨지지 않도록 저장소의 텍스트 파일은 UTF-8 이어야 한다 (.ps1 은 Windows PowerShell 5.1 을 위해 BOM 필요)."""
        bad = []
        for rel in ("run.bat", os.path.join("setup", "install.bat"), os.path.join("setup", "install.sh"),
                    "config.json", "README.md", os.path.join("setup", "config.example.json"),
                    os.path.join("setup", "server.example.json"), os.path.join("setup", "schedule.example.json")):
            p = os.path.join(ROOT, rel)
            if not os.path.exists(p):
                continue
            with open(p, "rb") as f:
                data = f.read()
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as e:
                bad.append("%s: %s" % (rel, e))
        self.assertEqual(bad, [])
        ps1 = os.path.join(ROOT, "setup", "schedule_build.ps1")
        if os.path.exists(ps1):
            with open(ps1, "rb") as f:
                head = f.read(3)
            with open(ps1, "rb") as f:
                body = f.read()
            if any(b > 127 for b in body):
                self.assertEqual(head, b"\xef\xbb\xbf", "Windows PowerShell 5.1 은 BOM 없는 UTF-8 .ps1 의 한글을 깨뜨린다")

    def test_batch_files_set_utf8_codepage(self):
        """한글이 들어간 .bat 은 chcp 65001 을 해야 cmd 가 UTF-8 로 읽고 출력한다."""
        for rel in ("run.bat", os.path.join("setup", "install.bat")):
            p = os.path.join(ROOT, rel)
            with open(p, "r", encoding="utf-8") as f:
                src = f.read()
            if any(ord(ch) > 127 for ch in src):
                self.assertIn("chcp 65001", src, "%s 에 한글이 있으면 chcp 65001 이 필요하다" % rel)


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
