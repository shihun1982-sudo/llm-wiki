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

    # ---- 프로바이더/키: 역할별로 실제 어떤 provider 가 쓰이는지 보고, 그 provider 에 필요한 키·URL·실행 파일을 점검 ----
    providers_used = {s.role_llm(r)["provider"] for r in s.LLM_ROLES} | {s.llm_provider}
    key_a, tok_a = os.environ.get("ANTHROPIC_API_KEY"), os.environ.get("ANTHROPIC_AUTH_TOKEN")
    key_o = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    line(OK if (key_a or tok_a) else WARN, "ANTHROPIC_API_KEY/AUTH_TOKEN %s" % ("설정됨" if (key_a or tok_a) else "없음"),
         "" if (key_a or tok_a) else "Anthropic 을 쓰려면 .env 에 키(PAT 는 ANTHROPIC_AUTH_TOKEN) — 아니면 openai_base_url(PAT 게이트웨이)/Ollama/headless")
    if s.anthropic_base_url:
        line(OK, "anthropic_base_url=%s (게이트웨이)" % s.anthropic_base_url, "" if (key_a or tok_a) else "게이트웨이 PAT 를 ANTHROPIC_AUTH_TOKEN 또는 ANTHROPIC_API_KEY 에")
    if "openai" in providers_used or s.embed_provider == "openai":
        line(OK if key_o else WARN, "OPENAI_API_KEY/LLM_API_KEY %s (openai_base_url=%s, 헤더 %s)" % ("설정됨" if key_o else "없음", s.openai_base_url, s.openai_api_key_header),
             "" if key_o else "게이트웨이가 인증을 요구하면 .env 에 PAT (로컬 vLLM/Ollama 는 비워도 됨)")
    elif key_o:
        line(OK, "OPENAI_API_KEY 설정됨 (현재 openai provider 미사용)")
    line(OK if os.environ.get("VOYAGE_API_KEY") else WARN, "VOYAGE_API_KEY %s" % ("설정됨" if os.environ.get("VOYAGE_API_KEY") else "없음 → 로컬 hash 임베딩(또는 Ollama bge-m3) 사용"))
    if os.environ.get("RERANK_API_KEY"):
        line(OK, "RERANK_API_KEY 설정됨")
    print("llm_provider=%s llm_model=%s embed_provider=%s openai_base_url=%s anthropic_base_url=%s rerank_url=%s" % (
        s.llm_provider, s.llm_model, s.embed_provider, s.openai_base_url, s.anthropic_base_url or "-", s.rerank_url or "-"))
    if s.llm_provider == "auto" and not (key_a or tok_a):
        line(WARN, "llm_provider=auto 는 openai/headless 를 고르지 않음", "게이트웨이(PAT)나 opencode 를 쓰려면 llm_provider 또는 llm_roles.<role>.provider 에 명시")

    import urllib.request
    try:
        with urllib.request.urlopen(s.ollama_url + "/api/tags", timeout=0.5) as r:
            tags = json.loads(r.read().decode("utf-8")).get("models", [])
            line(OK, "Ollama 연결됨 (%d 모델)" % len(tags))
    except Exception:
        line(WARN if ("ollama" in providers_used or s.embed_provider == "ollama") else OK, "Ollama 없음 (선택 사항)")

    from llmwiki.providers import make_llm, make_embedder
    llm, emb = make_llm(s), make_embedder(s)
    line(OK if llm.available or s.llm_provider in ("auto", "none") else WARN,
         "선택된 LLM: %s (available=%s)" % (llm.name, llm.available))
    line(OK, "선택된 임베더: %s" % emb.name)
    # 역할별 ping: openai 게이트웨이 / headless 실행 파일 / anthropic 게이트웨이 연결 여부 (토큰 소비 없음)
    seen = set()
    for role in s.LLM_ROLES:
        cfg = s.role_llm(role)
        key = (cfg["provider"], cfg["model"])
        if key in seen or cfg["provider"] in ("none", "mock", "auto"):
            continue
        seen.add(key)
        try:
            r = make_llm(s, role).ping()
            line(OK if r.get("ok") else WARN, "ping %s/%s: %s" % (cfg["provider"], cfg["model"], (r.get("detail") or "")[:120]),
                 "" if r.get("ok") else "python -m llmwiki models test --live 로 실제 호출까지 확인")
        except Exception as e:
            line(WARN, "ping %s/%s 실패: %s" % (cfg["provider"], cfg["model"], str(e)[:120]))
    if s.embed_provider == "openai":
        try:
            r = emb.ping()
            line(OK if r.get("ok") else WARN, "임베딩 엔드포인트 %s: %s" % (s.openai_embed_base_url or s.openai_base_url, (r.get("detail") or "")[:120]))
        except Exception as e:
            line(WARN, "임베딩 엔드포인트 실패: %s" % str(e)[:120])
    print()
    if problems:
        print("문제 %d건 — 위 FAIL 항목을 해결한 뒤 다시 실행하세요." % problems)
    else:
        print("환경 준비 완료. 다음: python -m llmwiki health  →  python -m llmwiki build --full --trace  (상세: docs/BRINGUP_GUIDE.md)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
