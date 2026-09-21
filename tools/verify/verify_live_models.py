"""**실제 모델**로 종단 확인 — mock 이 아니라 지금 이 PC 에서 진짜 붙는 LLM·임베더·리랭커로 질의를 끝까지 돌린다.

왜 있나: 다른 하네스는 전부 `llm_provider=mock`(결정적·무료·빠름)으로 배선을 검증한다. 그래서 "배선은 맞는데
실제 모델에서는 안 되는" 것 — 모델 이름 표기(`llama3.1` vs `llama3.1:latest`), 임베딩 차원 불일치,
프롬프트가 길어 끊기는 것, 한국어 응답 품질 — 은 잡히지 않는다. 이 하네스가 그 구멍을 메운다.

무엇을 하나 (모두 **격리 임시 환경** — 실제 색인·설정은 건드리지 않는다):
  1. 이 PC 에서 실제로 붙는 프로바이더를 **자동 탐지**한다 (Ollama 태그 목록 · API 키 · headless 실행 파일).
  2. 고른 모델로 config 를 만들고 샘플 코퍼스를 **진짜 임베딩으로 빌드**한다.
  3. `models test --live`(역할마다 완성 호출 1회) · 카탈로그 전체 테스트.
  4. 질의를 **끝까지** 돌려 답변·인용·groundedness·단계 시간을 검사한다 (grounded / best_effort 두 모드).
  5. LLM 리랭크·융합 뒤 LLM 검토처럼 **LLM 이 관여하는 단계**를 켜고 실제로 돌았는지 trace 로 확인한다.

실행:
    python tools/verify/verify_live_models.py                 # 자동 탐지
    python tools/verify/verify_live_models.py --list          # 무엇이 붙는지만 보고 끝
    python tools/verify/verify_live_models.py --chat ollama:llama3.1:latest --embed ollama:bge-m3
    python tools/verify/verify_live_models.py --skip-build    # 이미 만든 임시 환경 재사용(--keep 과 함께)
결과: `검사 N개 중 M개 통과` + 표, `verify_live_models_result.json`, 붙는 모델이 하나도 없으면 SKIP(exit 0).
문서: docs/BRINGUP_GUIDE.md §4 · docs/VERIFICATION.md
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_live_models_result.json")

rows = []


def check(name, ok, detail="", group="-", severity="fail"):
    """severity='fail' = 배선 검사(실패하면 exit 1) · 'warn' = **모델 품질** 관찰(실패해도 통과로 친다).

    작은 로컬 모델은 같은 질문에도 인용을 빠뜨리거나 답 길이가 들쭉날쭉하다. 그것을 실패로 세면
    "코드가 깨졌다" 와 "이 모델이 지시를 잘 안 따른다" 가 섞여 신호가 죽는다. 그래서 둘을 나눈다.
    """
    sev = "fail" if severity == "fail" else "warn"
    rows.append({"name": name, "group": group, "ok": bool(ok), "severity": sev, "detail": str(detail)[:400]})
    mark = "OK  " if ok else ("FAIL" if sev == "fail" else "WARN")
    print("  %s %-10s %-44s %s" % (mark, group, name, str(detail)[:90]), flush=True)
    return bool(ok)


# ---------------------------------------------------------------- 1. 무엇이 붙는가
def ollama_models(url="http://localhost:11434"):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=4) as r:
            return [m.get("name", "") for m in (json.loads(r.read().decode("utf-8")) or {}).get("models", [])]
    except Exception:
        return []


def which(name):
    return shutil.which(name) or shutil.which(name + ".cmd") or shutil.which(name + ".exe")


# 임베딩 전용 모델로 알려진 이름 (채팅 후보에서 뺀다)
EMBED_HINTS = ("embed", "bge", "e5", "gte", "minilm", "nomic")


def discover():
    """이 PC 에서 실제로 쓸 수 있는 것: {chat:[...], embed:[...], rerank:[...], notes:[...]}"""
    out = {"chat": [], "embed": [], "rerank": [], "notes": []}
    tags = ollama_models()
    for t in tags:
        (out["embed"] if any(h in t.lower() for h in EMBED_HINTS) else out["chat"]).append("ollama:" + t)
    if not tags:
        out["notes"].append("Ollama 응답 없음 (http://localhost:11434) — `ollama serve` 와 `ollama pull` 확인")
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        out["chat"].append("anthropic:claude-haiku-4-5-20251001")
    else:
        out["notes"].append(".env 에 ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN 이 없어 Anthropic 은 건너뜀")
    if os.environ.get("OPENAI_API_KEY"):
        out["chat"].append("openai:gpt-4o-mini")
        out["embed"].append("openai:text-embedding-3-small")
    if os.environ.get("VOYAGE_API_KEY"):
        out["embed"].append("voyage:voyage-3.5")
    if os.environ.get("RERANK_API_KEY") or os.environ.get("COHERE_API_KEY") or os.environ.get("JINA_API_KEY"):
        out["rerank"].append("api:rerank_url")
    for agent in ("opencode", "claude", "codex"):
        p = which(agent)
        if p:
            out["chat"].append("headless:%s" % agent)
            out["notes"].append("headless %s 발견: %s" % (agent, p))
    try:
        import sentence_transformers  # noqa: F401
        out["rerank"].append("cross_encoder:BAAI/bge-reranker-v2-m3")
    except Exception:
        out["notes"].append("sentence-transformers 없음 — 크로스인코더 리랭크는 건너뜀")
    out["embed"].append("hash:hash")          # 항상 가능한 오프라인 폴백 (마지막 후보)
    return out


def split_spec(spec):
    """'ollama:llama3.1:latest' → ('ollama', 'llama3.1:latest')"""
    prov, _, model = spec.partition(":")
    return prov, model


# ---------------------------------------------------------------- 2. 격리 환경
def build_env(tmp, chat, embed, dim):
    def cp(src, dst):
        shutil.copytree(src, dst) if os.path.isdir(src) else shutil.copy2(src, dst)

    cprov, cmodel = split_spec(chat)
    eprov, emodel = split_spec(embed)
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    cfg.update({"data_dir": os.path.join(tmp, "data"), "wiki_dir": os.path.join(tmp, "wiki"),
                "corpus_dirs": [os.path.join(ROOT, "setup", "sample_corpus_modem")],
                "llm_provider": cprov, "llm_model": cmodel, "llm_roles": {},
                "embed_provider": eprov, "embed_model": emodel, "embed_dim": dim,
                # 실모델은 느리다 — 역할마다 넉넉히 두되 무한정 기다리지는 않는다
                "llm_timeout": 300, "llm_retries": 1, "answer_max_tokens": 1200})
    cfg["toggles"] = dict(cfg.get("toggles") or {}, llm_graph=False, community_summary=False, precompute=False,
                          query_cache=False, rerun_capture=True, analysis_mode=False)
    json.dump(cfg, open(os.path.join(tmp, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    shutil.copy2(os.path.join(ROOT, ".env"), os.path.join(tmp, ".env")) if os.path.exists(os.path.join(ROOT, ".env")) \
        else open(os.path.join(tmp, ".env"), "w", encoding="utf-8").write("PYTHONIOENCODING=utf-8\n")
    for f in ("tuning.json", "presets.json", "query_rules.json", "mcp_sources.json", "agents.json", "pins.json",
              "security.json", "models.json", "server.json", "schedule.json"):
        src = os.path.join(ROOT, f)
        if os.path.exists(src):
            cp(src, os.path.join(tmp, f))
    os.makedirs(os.path.join(tmp, "data"), exist_ok=True)
    cp(os.path.join(ROOT, "data", "rules.json"), os.path.join(tmp, "rules.json"))
    for d in ("schemas", "prompts"):
        cp(os.path.join(ROOT, d), os.path.join(tmp, d))
    cp(os.path.join(ROOT, "eval", "questions.json"), os.path.join(tmp, "questions.json"))
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               LLMWIKI_CONFIG=os.path.join(tmp, "config.json"), LLMWIKI_ENV_FILE=os.path.join(tmp, ".env"),
               LLMWIKI_TUNING_PATH=os.path.join(tmp, "tuning.json"), LLMWIKI_PRESETS_PATH=os.path.join(tmp, "presets.json"),
               LLMWIKI_QUERY_RULES_PATH=os.path.join(tmp, "query_rules.json"), LLMWIKI_MCP_SOURCES_PATH=os.path.join(tmp, "mcp_sources.json"),
               LLMWIKI_AGENTS_PATH=os.path.join(tmp, "agents.json"), LLMWIKI_PINS_PATH=os.path.join(tmp, "pins.json"),
               LLMWIKI_RULES_PATH=os.path.join(tmp, "rules.json"), LLMWIKI_SCHEMAS_DIR_PATH=os.path.join(tmp, "schemas"),
               LLMWIKI_PROMPTS_DIR_PATH=os.path.join(tmp, "prompts"), LLMWIKI_EVAL_PATH=os.path.join(tmp, "questions.json"),
               LLMWIKI_LOGS_DIR_PATH=os.path.join(tmp, "logs"), LLMWIKI_SECURITY_PATH=os.path.join(tmp, "security.json"),
               LLMWIKI_SERVER_PATH=os.path.join(tmp, "server.json"), LLMWIKI_SCHEDULE_PATH=os.path.join(tmp, "schedule.json"),
               LLMWIKI_MODELS_PATH=os.path.join(tmp, "models.json"))
    for k in ("LLMWIKI_USER", "LLMWIKI_PASSWORD", "LLMWIKI_API_KEY"):
        env.pop(k, None)
    return env


def run(env, args, timeout=1800):
    r = subprocess.run([PY, "-m", "llmwiki"] + args, cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def main(argv=None):
    ap = argparse.ArgumentParser(description="실제 모델로 종단 검증")
    ap.add_argument("--list", action="store_true", help="붙는 것만 조사해 출력하고 끝")
    ap.add_argument("--chat", default="", help="prov:model (예 ollama:llama3.1:latest)")
    ap.add_argument("--embed", default="", help="prov:model (예 ollama:bge-m3)")
    ap.add_argument("--dim", type=int, default=0, help="임베딩 차원 (0 = 자동 감지)")
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 지우지 않는다")
    ap.add_argument("--timeout", type=int, default=1800, help="명령 하나의 제한(초)")
    ns = ap.parse_args(argv)

    disc = discover()
    print("이 PC 에서 붙는 것:")
    for k in ("chat", "embed", "rerank"):
        print("  %-7s %s" % (k, ", ".join(disc[k]) or "(없음)"))
    for n in disc["notes"]:
        print("  · %s" % n)
    if ns.list:
        return 0
    chat = ns.chat or (disc["chat"][0] if disc["chat"] else "")
    embed = ns.embed or next((e for e in disc["embed"] if not e.startswith("hash")), disc["embed"][0])
    if not chat:
        print("\nLIVE SKIP: 실제로 붙는 채팅 모델이 없습니다 (Ollama 모델을 받거나 .env 에 키를 넣으세요)")
        return 0
    dim = ns.dim or (1024 if "bge-m3" in embed else 768 if "nomic" in embed else 1536 if "text-embedding-3-small" in embed
                     else 1024 if "voyage" in embed else 4096)
    print("\n선택: chat=%s · embed=%s (dim %d)\n" % (chat, embed, dim))

    tmp = tempfile.mkdtemp(prefix="lwlive_")
    t0 = time.time()
    try:
        env = build_env(tmp, chat, embed, dim)
        # ---- 연결 ----
        code, out, err = run(env, ["models", "test", "--live"], ns.timeout)
        ok_roles = out.count("[OK ]")
        check("models test --live (역할별 실제 호출)", code == 0 and ok_roles >= 2, "OK %d개 · %s" % (ok_roles, (out.strip().splitlines() or [""])[0][:80]), "연결")
        code, out, err = run(env, ["models", "test", "--catalog"], ns.timeout)
        check("models test --catalog (카탈로그 전체 ping)", code in (0, 1), (out.strip().splitlines() or [""])[-1][:90], "연결")

        # ---- 빌드 (진짜 임베딩) ----
        tb = time.time()
        code, out, err = run(env, ["build", "--full", "--yes", "--no-snapshot"], ns.timeout)
        build_s = time.time() - tb
        check("build --full (실제 임베딩으로 색인)", code == 0, "%.0fs · %s" % (build_s, (out.strip().splitlines() or [""])[-1][:70]), "빌드")
        code, out, err = run(env, ["build", "verify", "--json"], ns.timeout)
        try:
            v = json.loads(out)
            check("build verify (채널 결손 0)", not v.get("alerts"), "alerts=%s" % (v.get("alerts") or [])[:2], "빌드")
        except Exception:
            check("build verify (채널 결손 0)", code == 0, (out or err)[:90], "빌드")

        # ---- 질의: grounded ----
        q = "PDCCH 디코딩 실패의 원인과 조치는?"
        code, out, err = run(env, ["query", q, "--json", "--no-log"], ns.timeout)
        res = {}
        try:
            res = (json.loads(out) or {}).get("result") or json.loads(out)
        except Exception:
            pass
        ans = str(res.get("answer") or "")
        check("query (grounded) 완주", code == 0 and bool(ans), "result_type=%s · %d자" % (res.get("result_type"), len(ans)), "질의")
        check("근거 목록(refs) 이 비어 있지 않다", bool(res.get("refs")), "refs=%d" % len(res.get("refs") or []), "질의")
        g = res.get("groundedness")
        check("groundedness 판정이 있다", g is not None, "groundedness=%s · verdict=%s" % (g, (res.get("evidence") or {}).get("verdict")), "질의")
        # 인용은 **모델 품질** — 컨텍스트에 [C#] 블록이 들어갔는지(배선)는 refs/citations 로 이미 확인했다.
        # 작은 로컬 모델은 같은 질문에도 인용을 빠뜨릴 때가 있어 warn 으로 남긴다.
        cl = res.get("claims") or {}
        check("답변에 인용 [C#] 이 있다 (모델 품질)", "[C" in ans,
              "인용 %d개 · citation_precision=%s · refined=%s · 이 모델이 지시를 안 따랐을 수 있다" % (ans.count("[C"), cl.get("citation_precision"), cl.get("refined")),
              "질의", severity="warn")

        # ---- 질의: best_effort (근거가 없어도 배경지식으로) ----
        code, out, err = run(env, ["query", "5G NR 의 HARQ 재전송 최대 횟수는?", "--answer-mode", "best_effort", "--json", "--no-log"], ns.timeout)
        try:
            r2 = (json.loads(out) or {}).get("result") or {}
        except Exception:
            r2 = {}
        check("query --answer-mode best_effort", code == 0 and str(r2.get("result_type") or "") in ("best_effort", "grounded", "extractive"),
              "result_type=%s · [BK] %d개" % (r2.get("result_type"), str(r2.get("answer") or "").count("[BK]")), "질의")

        # ---- LLM 이 관여하는 단계가 실제로 도는가 (trace) ----
        code, out, err = run(env, ["query", q, "--rerank-llm", "--llm-after-fusion", "--json", "--no-log"], ns.timeout)
        try:
            j3 = json.loads(out); tr = j3.get("trace") or {}; r3 = j3.get("result") or {}
        except Exception:
            tr, r3 = {}, {}

        def flat(n, acc=None):
            acc = acc if acc is not None else []
            acc.append(n)
            for c in n.get("children") or []:
                flat(c, acc)
            return acc
        names = {n.get("name"): n for n in flat(tr)} if tr else {}
        check("LLM 리랭크 단계가 실제로 돌았다", bool(names.get("rerank_llm")) and names["rerank_llm"].get("enabled") is not False,
              "ms=%s" % (names.get("rerank_llm") or {}).get("ms"), "LLM단계")
        fl = names.get("fusion_llm")
        check("융합 뒤 LLM 검토 단계가 실제로 돌았다", bool(fl) and fl.get("enabled") is not False,
              "meta=%s" % json.dumps((fl or {}).get("meta") or {}, ensure_ascii=False)[:80], "LLM단계")

        # ---- 평가 (실제 모델로 hit@k) ----
        code, out, err = run(env, ["eval", "--k", "5", "--json"], ns.timeout)
        try:
            ev = json.loads(out)
            # `eval --json` 은 {"result": {"rows": [...], "summary": {...}}, "trace": {...}} 를 준다
            ev = ev.get("result") if isinstance(ev.get("result"), dict) else ev
            summ = ev.get("summary") if isinstance(ev.get("summary"), dict) else ev
            check("eval hit@5 (실제 임베딩)", code == 0 and summ.get("hit@k") is not None,
                  "hit@k=%s · mrr=%s · term_recall=%s · n=%s" % (summ.get("hit@k"), summ.get("mrr"), summ.get("term_recall"), summ.get("n")), "평가")
        except Exception:
            check("eval hit@5 (실제 임베딩)", code == 0, (out or err)[:90], "평가")
    finally:
        if not ns.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print("임시 폴더 유지: %s" % tmp)

    bad = [r for r in rows if not r["ok"] and r["severity"] == "fail"]
    warn = [r for r in rows if not r["ok"] and r["severity"] == "warn"]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"ts": time.strftime("%Y-%m-%d %H:%M"), "chat": chat, "embed": embed, "dim": dim,
                   "discovered": disc, "seconds": round(time.time() - t0, 1),
                   "total": len(rows), "failed": len(bad), "warned": len(warn), "rows": rows}, f, ensure_ascii=False, indent=1)
    print("\n" + "=" * 92)
    print("| 구간 | 검사 | 결과 |")
    print("|---|---|---|")
    for r in rows:
        mark = "OK — " + r["detail"][:60] if r["ok"] else (("**실패** " if r["severity"] == "fail" else "△ 품질 ") + r["detail"][:60])
        print("| %s | %s | %s |" % (r["group"], r["name"], mark))
    print("=" * 92)
    print("모델: chat=%s · embed=%s(dim %d) · %.0fs" % (chat, embed, dim, time.time() - t0))
    print("결과: %s" % OUT)
    print("\n검사 %d개 중 %d개 통과%s" % (len(rows), len(rows) - len(bad) - len(warn),
                                     (" · 품질 경고 %d건" % len(warn)) if warn else ""))
    for r in warn:
        print("  WARN [%s] %s — %s" % (r["group"], r["name"], r["detail"][:160]))
    for r in bad:
        print("  FAIL [%s] %s — %s" % (r["group"], r["name"], r["detail"][:160]))
    print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
