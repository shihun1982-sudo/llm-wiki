# -*- coding: utf-8 -*-
"""LLM 연결 방식 전환이 **config.json 만으로** 되는지 — CLI · Web · MCP 세 창구에서 종단 증명.

왜 이 하네스가 있나 (2026-09-19):
  이 시스템은 두 가지로 LLM 에 닿는다 — (A) 일반 LLM API(사내 게이트웨이 URL + PAT 포함) 와
  (B) headless CLI 에이전트(opencode 등)를 자식 프로세스로 실행. 둘 다 실제로 많이 쓰이므로,
  **"config.json 만 고치면 코드 수정 없이 바뀐다"** 가 사실이어야 한다. 문서에 그렇게 적혀 있는 것과
  실제로 그런 것은 다르다 — 이 하네스가 매번 확인한다.

무엇을 증명하나
  1. API 모드로 CLI/Web/MCP 질의가 되고, 셋 다 같은 프로바이더를 보고한다
  2. **config.json 의 llm 키만** 바꿔 headless 로 전환한다 (다른 파일·코드는 그대로)
  3. 같은 세 창구가 그대로 동작하고, 셋 다 headless 프로바이더를 보고한다
  4. 역할 하나만 headless 로 바꾸는 것도 된다 (전역과 역할별 두 축)
  5. 되돌리면 원래대로 (전환이 단방향이 아니다)

네트워크 없이 돈다 — API 쪽은 `mock` 프로바이더, headless 쪽은 `agents.json` 의 `mock` 에이전트
(`python -m llmwiki.headless --mock`)를 쓴다. 실제 게이트웨이·opencode 로 확인하려면
`python -m llmwiki models test --live` 를 쓴다 (docs/HEADLESS.md §검증).

사용법: python tools/verify/verify_llm_switch.py [--port 8973]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
sys.path.insert(0, ROOT)

rows = []


def check(name, ok, detail=""):
    rows.append({"name": name, "ok": bool(ok), "detail": str(detail)[:150]})
    print("%-4s %-52s %s" % ("OK" if ok else "FAIL", name, str(detail)[:110]))
    return bool(ok)


def _gen_corpus(d):
    os.makedirs(d, exist_ok=True)
    body = ("RX DMA 에서 underrun 이 발생하면 PHY 재시작이 실패한다. 원인은 클럭 게이팅 타이밍이며 "
            "CL-90001 에서 고쳤다. rev B1 에서 t_setup 은 4 ns 이다.\n")
    for i in range(3):
        with open(os.path.join(d, "d%d.md" % i), "w", encoding="utf-8") as f:
            f.write("---\ndoc_type: issue\next_id: ISSUE-900%d\n---\n\n# RX DMA underrun %d\n\n%s" % (i, i, body * 4))


def write_config(path, patch):
    """config.json 의 **일부 키만** 바꾼다 — 이 하네스가 다른 파일을 건드리지 않는다는 것을 스스로 지킨다."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    d.update(patch)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def run_cli(env, *argv):
    r = subprocess.run([PY, "-m", "llmwiki"] + list(argv), cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _json(text):
    """CLI 의 --json 출력은 콘솔 인코딩 때문에 BOM 이 붙을 수 있다."""
    try:
        return json.loads(str(text).lstrip("﻿"))
    except Exception:
        return {}


def role_provider(env, role="answer"):
    """역할이 실제로 쓰는 프로바이더 이름 (`models show --json` 의 roles.<role>.name)."""
    code, out = run_cli(env, "models", "show", "--json")
    return str(((_json(out).get("roles") or {}).get(role) or {}).get("name") or ""), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8973)
    ns = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="lwswitch_")
    corpus = os.path.join(tmp, "corpus")
    _gen_corpus(corpus)
    cfg = os.path.join(tmp, "config.json")
    shutil.copyfile(os.path.join(ROOT, "setup", "config.example.json"), cfg)

    env = dict(os.environ)
    env.update({
        "LLMWIKI_CONFIG_PATH": cfg,
        "LLMWIKI_TUNING_PATH": os.path.join(tmp, "tuning.json"),
        "LLMWIKI_LOGS_DIR_PATH": os.path.join(tmp, "logs"),
        "LLMWIKI_PRESETS_PATH": os.path.join(tmp, "presets.json"),
        "LLMWIKI_QUERY_RULES_PATH": os.path.join(tmp, "query_rules.json"),
        "LLMWIKI_RULES_PATH": os.path.join(tmp, "rules.json"),
        "LLMWIKI_PINS_PATH": os.path.join(tmp, "pins.json"),
        "LLMWIKI_SECURITY_PATH": os.path.join(tmp, "security.json"),
        "LLMWIKI_SERVER_PATH": os.path.join(tmp, "server.json"),
        "LLMWIKI_SCHEDULE_PATH": os.path.join(tmp, "schedule.json"),
        "LLMWIKI_PROMPTS_DIR_PATH": os.path.join(tmp, "prompts"),
        "PYTHONIOENCODING": "utf-8",
    })
    # 코퍼스·데이터 경로와 '가벼운 빌드' 설정은 처음 한 번만 (이후 전환에서는 llm_* 만 만진다)
    write_config(cfg, {
        "corpus_dirs": [corpus], "data_dir": os.path.join(tmp, "data"), "wiki_dir": os.path.join(tmp, "wiki"),
        "embed_provider": "hash", "embed_model": "", "embed_dim": 64,
        "toggles": dict(json.load(open(cfg, encoding="utf-8")).get("toggles") or {},
                        llm_graph=False, community_summary=False, health_check=False, query_cache=False),
        "llm_provider": "mock", "llm_model": "mock-model",
    })

    code, out = run_cli(env, "build", "--full", "--yes")   # --full 은 색인 초기화라 확인이 필요하다
    if not check("초기 빌드", code == 0, out.strip().splitlines()[-1] if out.strip() else ""):
        print("\nRESULT PROBLEMS"); return 1

    # ---------------- 전환 전: config.json 외의 파일 지문 ----------------
    def fingerprint():
        fp = {}
        for name in ("agents.json", "tuning.json", "query_rules.json", "rules.json", "security.json", "server.json"):
            p = os.path.join(tmp, name) if name != "agents.json" else os.path.join(ROOT, "agents.json")
            fp[name] = os.path.getmtime(p) if os.path.exists(p) else None
        fp["llmwiki_src"] = max((os.path.getmtime(os.path.join(dp, f))
                                 for dp, _dn, fs in os.walk(os.path.join(ROOT, "llmwiki")) for f in fs
                                 if f.endswith(".py")), default=0)
        return fp

    before_fp = fingerprint()

    # ---------------- 세 창구에서 같은 질의 ----------------
    def three_surfaces(label, expect_provider):
        ok_all = True
        # (1) CLI
        code, out = run_cli(env, "query", "RX DMA underrun 원인", "--json")
        ok = code == 0 and '"answer"' in out
        prov, raw = role_provider(env)
        ok_all &= check("%s · CLI query" % label, ok, "answer provider=%s" % (prov or raw[:40]))
        ok_all &= check("%s · CLI provider 일치" % label, expect_provider in str(prov), "%s ⊂ %r" % (expect_provider, prov))

        # (2) Web
        srv = subprocess.Popen([PY, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(ns.port)],
                               cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace")
        base = "http://127.0.0.1:%d" % ns.port
        try:
            for _ in range(80):
                try:
                    urllib.request.urlopen(base + "/api/status", timeout=2).read()
                    break
                except Exception:
                    time.sleep(0.25)
            req = urllib.request.Request(base + "/api/query", method="POST",
                                         data=json.dumps({"q": "RX DMA underrun 원인"}).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            body = json.loads(urllib.request.urlopen(req, timeout=180).read().decode("utf-8"))
            st = json.loads(urllib.request.urlopen(base + "/api/status", timeout=10).read().decode("utf-8"))
            wprov = (((st.get("providers") or {}).get("roles") or {}).get("answer") or {}).get("name") or ""
            if not wprov:      # 화면이 보는 것과 같은 자리 — 없으면 전역 llm 로
                wprov = ((st.get("providers") or {}).get("llm") or {}).get("name") or ""
            ok_all &= check("%s · Web /api/query" % label, bool((body.get("result") or {}).get("answer") is not None),
                            "provider=%s" % wprov)
            ok_all &= check("%s · Web provider 일치" % label, expect_provider in str(wprov), "%s ⊂ %s" % (expect_provider, wprov))

            # (3) MCP — 같은 서버의 /mcp
            mreq = urllib.request.Request(base + "/mcp", method="POST", headers={"Content-Type": "application/json"},
                                          data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                           "params": {"name": "wiki_query",
                                                                      "arguments": {"question": "RX DMA underrun 원인", "k": 3}}}).encode("utf-8"))
            mres = json.loads(urllib.request.urlopen(mreq, timeout=180).read().decode("utf-8"))
            text = ((mres.get("result") or {}).get("content") or [{}])[0].get("text", "")
            ok_all &= check("%s · MCP wiki_query" % label, bool(text), text[:60].replace("\n", " "))
            sreq = urllib.request.Request(base + "/mcp", method="POST", headers={"Content-Type": "application/json"},
                                          data=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                                           "params": {"name": "wiki_status", "arguments": {}}}).encode("utf-8"))
            sres = json.loads(urllib.request.urlopen(sreq, timeout=60).read().decode("utf-8"))
            stext = ((sres.get("result") or {}).get("content") or [{}])[0].get("text", "")
            # "mock" 은 "headless:mock" 안에도 들어 있으므로, API 모드에서는 headless 가 **없다**는 것까지 본다.
            hit = expect_provider in stext
            no_headless = ("headless" not in stext) if expect_provider == "mock" else True
            ok_all &= check("%s · MCP provider 일치" % label, hit and no_headless,
                            ("headless 없음 · " if expect_provider == "mock" else "")
                            + (", ".join(l.strip()[:50] for l in stext.splitlines()
                                         if expect_provider in l)[:80] or "(상태 텍스트에서 확인)"))
        finally:
            srv.terminate()
            try:
                srv.wait(timeout=20)
            except Exception:
                srv.kill()
        return ok_all

    three_surfaces("A) LLM API", "mock")

    # ---------------- config.json 의 llm 키만 바꿔 headless 로 ----------------
    write_config(cfg, {"llm_provider": "headless:mock", "llm_model": "mock/any"})
    check("전환: config.json 만 수정했다", True, "llm_provider · llm_model 두 키")
    after_fp = fingerprint()
    check("다른 설정 파일이 바뀌지 않았다",
          all(before_fp[k] == after_fp[k] for k in before_fp if k != "llmwiki_src"),
          ", ".join(k for k in before_fp if before_fp[k] != after_fp[k] and k != "llmwiki_src") or "없음")
    check("코드(llmwiki/*.py)가 바뀌지 않았다", before_fp["llmwiki_src"] == after_fp["llmwiki_src"])

    three_surfaces("B) headless", "headless")

    # ---------------- 역할 하나만 headless ----------------
    write_config(cfg, {"llm_provider": "mock", "llm_model": "mock-model",
                       "llm_roles": {"answer": {"provider": "headless:mock", "model": "mock/any"}}})
    a, _ = role_provider(env, "answer")
    r, _ = role_provider(env, "rerank")
    check("C) 역할별 전환: answer 만 headless", "headless" in a and "headless" not in r,
          "answer=%r rerank=%r" % (a, r))

    # ---------------- 되돌리기 ----------------
    write_config(cfg, {"llm_provider": "mock", "llm_model": "mock-model", "llm_roles": {}})
    a, _ = role_provider(env, "answer")
    check("D) 되돌리기: 다시 API 모드", a and "headless" not in a, "answer=%r" % a)

    shutil.rmtree(tmp, ignore_errors=True)
    n_ok = sum(1 for r in rows if r["ok"])
    print("\n검사 %d개 중 %d개 통과 · 실패 %d개" % (len(rows), n_ok, len(rows) - n_ok))
    for r in rows:
        if not r["ok"]:
            print("  FAIL %-50s %s" % (r["name"], r["detail"]))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_llm_switch_result.json"),
              "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "ok": n_ok, "n": len(rows)}, f, ensure_ascii=False, indent=1)
    print("\nRESULT " + ("OK" if n_ok == len(rows) else "PROBLEMS"))
    return 0 if n_ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
