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
    try:
        from llmwiki import console as _console
        _console.setup()
    except Exception:
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    problems = 0
    # ---- 터미널 인코딩 (다른 환경에서 한글 깨짐의 가장 흔한 원인) ----
    try:
        from llmwiki import console as _c
        cd = _c.describe()
        line(OK if cd.get("safe") else WARN,
             "터미널 출력: stdout=%s · 로캘=%s%s · %s · mode=%s" % (
                 cd.get("stdout_encoding"), cd.get("locale_encoding"),
                 (" · 콘솔 코드페이지=%s" % cd["codepage_now"]) if cd.get("codepage_now") else "",
                 "콘솔" if cd.get("console") else "리디렉션(파일/파이프)", cd.get("mode")),
             "" if cd.get("safe") else "한글/기호를 출력할 수 없습니다 → config.json console_encoding=utf-8, Windows 는 chcp 65001, Linux 는 LANG=C.UTF-8")
    except Exception as e:
        line(WARN, "터미널 인코딩 점검 실패: %s" % str(e)[:120])
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
    # ---- 다중 사용자·MCP·재시도 설정 파일 (2026-09-15 기능) ----
    try:
        from llmwiki.config import path_for
        sec_path = path_for("security")
        if os.path.exists(sec_path):
            with open(sec_path, "r", encoding="utf-8") as f:
                sec = json.load(f)
            admins = [u for u, r in (sec.get("users") or {}).items() if (r or {}).get("role") == "admin"]
            line(OK if admins else WARN, "security.json mode=%s anonymous_role=%s admin 계정 %d개 API 키 %d개" % (
                sec.get("mode"), sec.get("anonymous_role"), len(admins), len(sec.get("api_keys") or {})),
                 "" if admins else "공개 전 admin 생성: python -m llmwiki users add <id> --role admin")
            if "kh82.kim" in admins:
                line(WARN, "기본 admin kh82.kim 이 남아 있음", "공개 전 비밀번호 변경: users passwd kh82.kim (또는 계정 삭제 후 새 admin)")
        else:
            line(WARN, "security.json 없음 (127.0.0.1 전용이면 무방)", "copy setup\\security.example.json security.json")
        ag_path = path_for("agents")
        if os.path.exists(ag_path):
            with open(ag_path, "r", encoding="utf-8") as f:
                ag = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
            line(OK, "agents.json 에이전트 %s (timeout_s/retries: %s)" % (list(ag), ", ".join("%s=%s/%s" % (k, v.get("timeout_s"), v.get("retries")) for k, v in ag.items())))
        line(OK, "serve 기본 %s:%s · mcp 기본 %s (http %s:%s)%s" % (s.web_host, s.web_port, s.mcp_transport, s.mcp_host, s.mcp_port,
             (" · 브리지 대상 " + (os.environ.get("LLMWIKI_MCP_URL") or s.mcp_url)) if (os.environ.get("LLMWIKI_MCP_URL") or s.mcp_url) else ""),
             "config.json web_host/web_port/mcp_* 또는 serve --host/--port 로 변경")
        line(OK, "LLM 재시도(전역): timeout=%ss retries=%s backoff=%s %ss(max %ss) budget=%ss 회로차단 %s회/%ss (headless 는 agents.json, 역할별은 llm_roles 가 우선)" % (
            s.llm_timeout, s.llm_retries, s.llm_retry_backoff, s.llm_retry_backoff_s, s.llm_retry_backoff_max_s, s.llm_budget_s or 0,
            s.llm_circuit_failures, s.llm_circuit_cooldown_s))
        # ---- 다중 사용자 동시성·스케줄·모델 카탈로그 (2026-09-15 기능) ----
        from llmwiki import reqmgr as _rq
        sp = _rq.server_path()
        rcfg = _rq.load_config(sp)
        c, rl = rcfg["concurrency"], rcfg["rate_limit"]
        line(OK if os.path.exists(sp) else WARN, "server.json %s — 동시 읽기 %s · 사용자당 %s · 대기열 %s(%ss) · 빌드 중 질의 %s · 분당 %s/%s(질의 %s) · 점검모드 %s" % (
            "있음" if os.path.exists(sp) else "없음(기본값 사용)", c["max_parallel_reads"], c["max_parallel_per_user"], c["queue_max"], c["queue_timeout_s"],
            c["reads_during_build"], rl["per_user_per_min"], rl["per_ip_per_min"], rl["query_per_user_per_min"], (rcfg["access"] or {}).get("maintenance_mode")),
            "" if os.path.exists(sp) else "copy setup\\server.example.json server.json (docs/CONCURRENCY.md)")
        line(OK, "SQLite 동시성: db_pool_size=%s busy_timeout=%ss WAL" % (s.db_pool_size, s.db_busy_timeout_s))
        # ---- 불용어 파일 (2026-09-18 요청 2) ----
        from llmwiki import textutil as _tu
        sw_path = path_for("stopwords")
        sw_exists = os.path.exists(sw_path)
        sw = _tu.load_stopwords()           # 없으면 기본 목록으로 생성
        line(OK, "stopwords.json %s — 불용어 %d개%s" % (
            "있음" if sw_exists else "없음 → 기본 목록으로 생성", len(sw),
            " (코드 기본값)" if sw == _tu.DEFAULT_STOPWORDS else " (편집됨)"),
             "질의 키워드에서 제거할 단어를 stopwords.json 에 추가 (재시작 불필요)")
        from llmwiki import scheduler as _sc
        tasks = _sc.list_tasks_static()
        bad = [t for t in tasks if t.get("invalid")]
        en = [t for t in tasks if t.get("enabled", True) and not t.get("invalid")]
        line(FAIL if bad else OK, "schedule.json 작업 %d개 (활성 %d)%s" % (len(tasks) - len(bad), len(en), (" · 설정 오류 %d: %s" % (len(bad), bad[0].get("invalid"))) if bad else ""),
             "python -m llmwiki schedule validate" if bad else "")
        if bad:
            problems += 1
        from llmwiki import models_catalog as _mc
        d = _mc.describe(s)
        line(WARN if d["unknown_in_use"] else OK, "models.json 카탈로그 %d개 (임베딩 %d)%s" % (len(d["models"]), len(d["embed"]),
             (" · 카탈로그에 없는 설정: " + ", ".join("%s=%s" % (u["role"], u["model"]) for u in d["unknown_in_use"])) if d["unknown_in_use"] else ""),
             "models catalog add <id> --provider <p>" if d["unknown_in_use"] else "")
    except Exception as e:
        line(WARN, "security/agents/server/schedule 점검 실패: %s" % str(e)[:200])
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
