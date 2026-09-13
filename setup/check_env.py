# -*- coding: utf-8 -*-
"""새 환경 진단: python setup/check_env.py

Python 버전, 필수/선택 패키지, SQLite FTS5, 설정 파일, API 키, Ollama, 코퍼스 폴더를 점검하고 권장 조치를 출력한다.
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"


def line(status: str, msg: str, hint: str = "") -> None:
    print("[%s] %s%s" % (status, msg, ("  → " + hint) if hint else ""))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    problems = 0
    v = sys.version_info
    if v >= (3, 11):
        line(OK, "Python %d.%d.%d" % v[:3])
    elif v >= (3, 9):
        line(OK, "Python %d.%d.%d (권장 3.11+)" % v[:3])
    else:
        line(FAIL, "Python %d.%d — 3.9 이상 필요 (zoneinfo·dataclass·타입 문법)" % v[:2])
        problems += 1

    for mod, req, hint in (("numpy", True, "pip install numpy"), ("pypdf", False, "pip install pypdf (PDF 코퍼스용)"),
                           ("anthropic", False, "pip install anthropic (없어도 raw HTTP 로 동작)"),
                           ("sentence_transformers", False, "pip install sentence-transformers (로컬 의미 임베딩/크로스인코더)"),
                           ("kiwipiepy", False, "pip install kiwipiepy (한국어 형태소 분석, tuning tokenizer=kiwi)"),
                           ("yaml", False, "pip install pyyaml (front matter 파서; 없으면 내장 파서)")):
        try:
            m = importlib.import_module(mod)
            line(OK, "%s %s" % (mod, getattr(m, "__version__", "")))
        except Exception:
            line(FAIL if req else WARN, "%s 없음" % mod, hint)
            problems += 1 if req else 0

    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        line(OK, "SQLite %s FTS5 사용 가능" % sqlite3.sqlite_version)
    except Exception as e:
        line(FAIL, "SQLite FTS5 사용 불가: %s" % e, "FTS5 가 포함된 Python 배포판 필요")
        problems += 1

    from llmwiki.config import load_settings, CONFIG_PATH, ENV_FILE
    s = load_settings()
    line(OK if os.path.exists(CONFIG_PATH) else WARN, "설정 파일 %s" % CONFIG_PATH)
    line(OK if os.path.exists(ENV_FILE) else WARN, ".env %s" % ("있음" if os.path.exists(ENV_FILE) else "없음"),
         "" if os.path.exists(ENV_FILE) else "copy setup\\.env.example .env 후 키 입력 (선택)")
    for d in s.corpus_dirs:
        if os.path.isdir(d):
            n = sum(len([f for f in fs if f.lower().endswith((".md", ".txt", ".html", ".htm", ".pdf"))]) for _, _, fs in os.walk(d))
            line(OK if n else WARN, "코퍼스 %s (%d 파일)" % (d, n), "" if n else "md/txt/html/pdf 파일을 넣거나 config.json 의 corpus_dirs 수정")
        else:
            line(FAIL, "코퍼스 폴더 없음: %s" % d, "config.json 의 corpus_dirs 를 실제 경로로 수정")
            problems += 1

    key = os.environ.get("ANTHROPIC_API_KEY")
    line(OK if key else WARN, "ANTHROPIC_API_KEY %s" % ("설정됨" if key else "없음 → LLM 단계는 폴백(추출식 답변/로컬 리랭크)"),
         "" if key else ".env 에 키를 넣거나 openai_base_url(OpenAI-compatible)/Ollama/headless 에이전트 설정")
    line(OK if os.environ.get("VOYAGE_API_KEY") else WARN, "VOYAGE_API_KEY %s" % ("설정됨" if os.environ.get("VOYAGE_API_KEY") else "없음 → 로컬 hash 임베딩(또는 Ollama bge-m3) 사용"))
    for k in ("OPENAI_API_KEY", "RERANK_API_KEY"):
        if os.environ.get(k):
            line(OK, "%s 설정됨" % k)
    print("llm_provider=%s llm_model=%s embed_provider=%s openai_base_url=%s rerank_url=%s" % (s.llm_provider, s.llm_model, s.embed_provider, s.openai_base_url, s.rerank_url or "-"))

    try:
        import urllib.request
        with urllib.request.urlopen(s.ollama_url + "/api/tags", timeout=0.5) as r:
            tags = json.loads(r.read().decode("utf-8")).get("models", [])
            line(OK, "Ollama 연결됨 (%d 모델)" % len(tags))
    except Exception:
        line(WARN, "Ollama 없음 (선택 사항)")

    from llmwiki.providers import make_llm, make_embedder
    llm, emb = make_llm(s), make_embedder(s)
    line(OK if llm.available or s.llm_provider in ("auto", "none") else WARN,
         "선택된 LLM: %s (available=%s)" % (llm.name, llm.available))
    line(OK, "선택된 임베더: %s" % emb.name)
    print()
    if problems:
        print("문제 %d건 — 위 FAIL 항목을 해결한 뒤 다시 실행하세요." % problems)
    else:
        print("환경 준비 완료. 다음: python -m llmwiki health  →  python -m llmwiki build --full --trace  (상세: docs/BRINGUP_GUIDE.md)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
