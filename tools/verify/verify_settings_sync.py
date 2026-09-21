"""설정 **양방향** 정합 검증 — "UI 에서 바꾼 값이 서버에 먹는가" 와 "파일을 고친 값이 화면에 보이는가".

왜 있나 (2026-09-18 사용자 보고):
  "Web UI 에서 편집한 값(config, .env, 모델 카탈로그, query_rules, data/rules.json, 튜닝)이 **항상 실제 서버에 반영되지는
   않았고**, 반대로 파일을 직접 고친 것이 화면에 안 보이는 경우도 있었다."
설정 화면은 두 방향 모두 맞아야 쓸 수 있다. 한 방향만 보는 검사는 "저장은 되는데 안 먹는" 상태를 통과시킨다.

그래서 표면마다 두 번 확인한다:
  A. **UI → 파일 → 유효값**  POST(화면이 쓰는 그 API) → 디스크 파일에 값이 있나 → GET/`config show --effective` 가 새 값인가
  B. **파일 → 재적재 → UI**  파일을 직접 고침 → (필요하면) reload API → GET 이 새 값인가

B 에서 "재적재" 가 무엇인지는 표면마다 다르다. 그 차이 자체가 운영자에게 중요한 정보라 결과 표의 `how` 열에 적는다:
  auto(mtime 캐시라 저장만 하면 됨) · reload(전용 reload 액션 필요) · restart(서버 재시작 필요 — 검사는 건너뛰고 문서화)

실행:  python tools/verify/verify_settings_sync.py [--port 8934] [--keep]
결과:  `검사 N개 중 M개 통과` + 표, `verify_settings_sync_result.json`, 실패 시 exit 1
문서:  docs/SETTINGS_SYNC.md
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
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_settings_sync_result.json")

rows = []
state = {}


def check(name, surface, how, ok, detail=""):
    rows.append({"name": name, "surface": surface, "how": how, "ok": bool(ok), "detail": str(detail)[:300]})
    print("  %s %-12s %-46s %s" % ("OK  " if ok else "FAIL", surface, name, str(detail)[:80]), flush=True)
    return bool(ok)


# ---------------------------------------------------------------- 격리 환경 (verify_web.py 와 같은 방식)
def isolated(port):
    tmp = tempfile.mkdtemp(prefix="lwsync_")

    def cp(src, dst):
        shutil.copytree(src, dst) if os.path.isdir(src) else shutil.copy2(src, dst)

    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    cfg.update({"data_dir": os.path.join(tmp, "data"), "wiki_dir": os.path.join(tmp, "wiki"),
                "corpus_dirs": [os.path.join(ROOT, "setup", "sample_corpus_modem")],
                "llm_provider": "mock", "llm_roles": {}, "embed_provider": "hash", "embed_dim": 256})
    json.dump(cfg, open(os.path.join(tmp, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # .env: 마스킹·오버라이드 표시를 확인할 수 있게 가짜 키와 LLMWIKI_* 오버라이드를 하나씩 둔다 (실제 키는 쓰지 않는다)
    open(os.path.join(tmp, ".env"), "w", encoding="utf-8").write(
        "PYTHONIOENCODING=utf-8\nOPENAI_API_KEY=sk-verify-0123456789abcdef\n")
    for f in ("tuning.json", "presets.json", "query_rules.json", "mcp_sources.json", "agents.json", "pins.json",
              "security.json", "models.json", "server.json", "schedule.json"):
        src = os.path.join(ROOT, f)
        if os.path.exists(src):
            cp(src, os.path.join(tmp, f))
    os.makedirs(os.path.join(tmp, "data"), exist_ok=True)
    cp(os.path.join(ROOT, "data", "rules.json"), os.path.join(tmp, "rules.json"))
    cp(os.path.join(ROOT, "schemas"), os.path.join(tmp, "schemas"))
    cp(os.path.join(ROOT, "prompts"), os.path.join(tmp, "prompts"))
    cp(os.path.join(ROOT, "eval", "questions.json"), os.path.join(tmp, "questions.json"))
    sec = json.load(open(os.path.join(tmp, "security.json"), encoding="utf-8"))
    sec.update({"mode": "on", "anonymous_role": "viewer"})
    sec["cli"] = dict(sec.get("cli") or {}, default_role="admin", require_login=False)
    json.dump(sec, open(os.path.join(tmp, "security.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
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
    # LLMWIKI_* 오버라이드가 /api/env 에 보이는지 확인하기 위한 표본 (설정 키 하나)
    env["LLMWIKI_LOG_LEVEL"] = "INFO"
    subprocess.run([PY, "-m", "llmwiki", "build", "--full", "--yes", "--no-snapshot"], cwd=ROOT, env=env,
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    subprocess.run([PY, "-m", "llmwiki", "users", "add", "adm", "--role", "admin", "--password", "adm-pass-123"],
                   cwd=ROOT, env=env, capture_output=True)
    proc = subprocess.Popen([PY, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    return tmp, env, proc


def make_req(base):
    def req(method, path, body=None, cookie=""):
        h = {"Content-Type": "application/json", "X-Requested-With": "llmwiki"}
        if cookie:
            h["Cookie"] = cookie
        r = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8") if body is not None else None,
                                   headers=h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                data = resp.read()
                return resp.status, (json.loads(data.decode("utf-8")) if data.strip() else {}), resp.headers
        except urllib.error.HTTPError as e:
            data = e.read()
            try:
                return e.code, json.loads(data.decode("utf-8")), e.headers
            except Exception:
                return e.code, {"raw": data.decode("utf-8", "ignore")[:200]}, e.headers
    return req


def read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(p, d):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    # mtime 캐시가 1초 해상도인 파일 시스템에서도 변경을 알아채도록 mtime 을 앞으로 민다
    os.utime(p, (time.time() + 2, time.time() + 2))


def effective(env, key):
    """`config show --effective` 의 그 키 줄 (서버가 아니라 **새 프로세스**가 파일에서 읽은 값 — 파일에 실제로 갔는지의 증거)."""
    r = subprocess.run([PY, "-m", "llmwiki", "config", "show", "--effective"], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    for line in (r.stdout or "").splitlines():
        if line.strip().startswith(key):
            return line.strip()
    return ""


# ---------------------------------------------------------------- 표면별 검사
def run(port, keep):
    tmp, env, proc = isolated(port)
    base = "http://127.0.0.1:%d" % port
    req = make_req(base)
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/api/auth/me", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        st, j, h = req("POST", "/api/auth/login", {"username": "adm", "password": "adm-pass-123"})
        adm = (h.get("Set-Cookie") or "").split(";")[0] if h else ""
        if not check("admin 로그인", "auth", "-", st == 200 and adm, "status=%s" % st):
            return
        C = {"_confirm": True, "_password": "adm-pass-123"}

        # ---------------- 1. config.json (top_k_final) ----------------
        p_cfg = os.path.join(tmp, "config.json")
        st, j, _ = req("POST", "/api/config", dict(C, settings={"top_k_final": 11}), cookie=adm)
        check("UI 저장 → 응답 200", "config", "A", st == 200, j.get("error") or "")
        check("UI 저장 → 파일", "config", "A", read_json(p_cfg).get("top_k_final") == 11, read_json(p_cfg).get("top_k_final"))
        eff = effective(env, "top_k_final")
        check("UI 저장 → config show --effective", "config", "A", "11" in eff, eff)
        st, j, _ = req("GET", "/api/config/effective", cookie=adm)
        check("UI 저장 → GET /api/config/effective", "config", "A", "11" in json.dumps(j, ensure_ascii=False), st)
        d = read_json(p_cfg)
        d["top_k_final"] = 13
        write_json(p_cfg, d)
        st, j, _ = req("POST", "/api/config", dict(C, action="reload"), cookie=adm)
        check("파일 편집 → reload API", "config", "reload", st == 200 and j.get("reloaded") is True, j.get("error") or "")
        check("파일 편집 → GET 반영", "config", "reload", (j.get("settings") or {}).get("top_k_final") == 13,
              (j.get("settings") or {}).get("top_k_final"))
        st, j, _ = req("GET", "/api/status")
        check("파일 편집 → /api/status.settings 반영", "config", "reload", (j.get("settings") or {}).get("top_k_final") == 13,
              (j.get("settings") or {}).get("top_k_final"))

        # ---------------- 2. 역할별 LLM (llm_roles) + 카탈로그 provider 자동 해석 ----------------
        st, j, _ = req("POST", "/api/models/set", dict(C, settings={"llm_roles": {"answer": {"timeout_s": 123}}}), cookie=adm)
        check("UI 저장 → 응답 200", "llm_roles", "A", st == 200, j.get("error") or "")
        check("UI 저장 → 파일", "llm_roles", "A",
              ((read_json(p_cfg).get("llm_roles") or {}).get("answer") or {}).get("timeout_s") == 123,
              read_json(p_cfg).get("llm_roles"))
        st, j, _ = req("GET", "/api/models", cookie=adm)
        pol = (j.get("policy") or j.get("roles") or {})
        check("UI 저장 → GET /api/models 반영", "llm_roles", "A", "123" in json.dumps(j, ensure_ascii=False), str(pol)[:80])
        d = read_json(p_cfg)
        d["llm_roles"] = {"answer": {"timeout_s": 321}}
        write_json(p_cfg, d)
        req("POST", "/api/config", dict(C, action="reload"), cookie=adm)
        st, j, _ = req("GET", "/api/models", cookie=adm)
        check("파일 편집 → reload → GET 반영", "llm_roles", "reload", "321" in json.dumps(j, ensure_ascii=False), st)
        req("POST", "/api/models/set", dict(C, settings={"llm_roles": {}}), cookie=adm)

        # ---------------- 3. tuning.json ----------------
        # 주의: `rrf_k` 는 tuning 표에 함께 보이지만 source=config (config.json 항목) 라 tuning.json 에 저장되지 않는다.
        # 여기서는 진짜 tuning.json 키를 쓴다.
        p_tun = os.path.join(tmp, "tuning.json")
        TK = "doc_expand_max_chunks"
        st, j, _ = req("POST", "/api/tuning", dict(C, action="set", values={TK: 5}), cookie=adm)
        check("UI 저장 → 응답 200", "tuning", "A", st == 200 and not (j.get("errors") or {}), j.get("errors") or j.get("error") or "")
        check("UI 저장 → 파일", "tuning", "A", read_json(p_tun).get(TK) == 5, read_json(p_tun).get(TK))
        st, j, _ = req("GET", "/api/tuning")
        check("UI 저장 → GET 반영", "tuning", "A", (j.get("overrides") or {}).get(TK) == 5, (j.get("overrides") or {}).get(TK))
        d = read_json(p_tun)
        d[TK] = 6
        write_json(p_tun, d)
        st, j, _ = req("POST", "/api/tuning", dict(C, action="reload"), cookie=adm)
        check("파일 편집 → reload API", "tuning", "reload", st == 200 and j.get("reloaded") is True, j.get("error") or "")
        st, j, _ = req("GET", "/api/tuning")
        check("파일 편집 → GET 반영", "tuning", "reload", (j.get("overrides") or {}).get(TK) == 6, (j.get("overrides") or {}).get(TK))
        # 유효값이 실제 질의에도 쓰이는지 (설정 화면과 질의 경로가 같은 값을 보는가)
        st, j, _ = req("POST", "/api/query", {"q": "PDCCH", "log": False, "overrides": {}})
        used = (((j.get("result") or {}).get("config") or {}).get("tuning") or {}).get(TK)
        check("파일 편집 → 질의가 쓰는 값", "tuning", "reload", used == 6, used)
        req("POST", "/api/tuning", dict(C, action="reset", key=TK), cookie=adm)

        # ---------------- 4. query_rules.json (mtime 자동 재적재) ----------------
        p_qr = os.path.join(tmp, "query_rules.json")
        st, j, _ = req("POST", "/api/query_rules", dict(C, action="add", type="synonym", term="싱크검증", values=["syncverify"]), cookie=adm)
        check("UI 저장 → 응답 200", "query_rules", "A", st == 200, j.get("error") or "")
        check("UI 저장 → 파일", "query_rules", "A", "싱크검증" in json.dumps(read_json(p_qr), ensure_ascii=False), "")
        st, j, _ = req("GET", "/api/query_rules/explain?term=%s" % urllib.parse.quote("싱크검증"))
        check("UI 저장 → explain 반영", "query_rules", "A", bool((j or {}).get("entries")), json.dumps(j, ensure_ascii=False)[:80])
        d = read_json(p_qr)
        d.setdefault("synonym", {})["파일직접"] = ["fileedit"]
        write_json(p_qr, d)
        st, j, _ = req("GET", "/api/query_rules/explain?term=%s" % urllib.parse.quote("파일직접"))
        check("파일 편집 → GET 반영 (mtime 자동)", "query_rules", "auto", bool((j or {}).get("entries")), json.dumps(j, ensure_ascii=False)[:80])
        st, j, _ = req("POST", "/api/query_rules", dict(C, action="remove", type="synonym", term="싱크검증"), cookie=adm)

        # ---------------- 5. data/rules.json (그래프 사전) ----------------
        p_rules = os.path.join(tmp, "rules.json")
        rl = read_json(p_rules)
        ents = rl.get("entities")
        if isinstance(ents, dict):
            rl["entities"] = dict(ents, **{"싱크검증엔티티": {"type": "concept"}})
        elif isinstance(ents, list):
            rl["entities"] = list(ents) + [{"name": "싱크검증엔티티", "type": "concept"}]
        st, j, _ = req("POST", "/api/rules", dict(C, rules=rl), cookie=adm)
        check("UI 저장 → 응답 200", "rules.json", "A", st == 200, j.get("error") or "")
        check("UI 저장 → 파일", "rules.json", "A", "싱크검증엔티티" in json.dumps(read_json(p_rules), ensure_ascii=False), "")
        st, j, _ = req("GET", "/api/rules", cookie=adm)
        check("UI 저장 → GET 반영", "rules.json", "A", "싱크검증엔티티" in json.dumps(j, ensure_ascii=False), st)
        d = read_json(p_rules)
        if isinstance(d.get("entities"), dict):
            d["entities"]["파일직접엔티티"] = {"type": "concept"}
        else:
            d["entities"] = list(d.get("entities") or []) + [{"name": "파일직접엔티티", "type": "concept"}]
        write_json(p_rules, d)
        st, j, _ = req("GET", "/api/rules", cookie=adm)
        check("파일 편집 → GET 반영 (mtime 자동)", "rules.json", "auto", "파일직접엔티티" in json.dumps(j, ensure_ascii=False), st)

        # ---------------- 6. models.json 카탈로그 ----------------
        p_mod = os.path.join(tmp, "models.json")
        st, j, _ = req("POST", "/api/models/catalog", dict(C, action="add", model={"id": "sync-verify-a", "provider": "ollama", "label": "동기화검증", "roles": ["answer"]}), cookie=adm)
        check("UI 저장 → 응답 200", "models.json", "A", st == 200, j.get("error") or "")
        check("UI 저장 → 파일", "models.json", "A", "sync-verify-a" in json.dumps(read_json(p_mod), ensure_ascii=False), "")
        st, j, _ = req("GET", "/api/models/catalog")
        check("UI 저장 → GET 반영", "models.json", "A", "sync-verify-a" in json.dumps(j, ensure_ascii=False), st)
        d = read_json(p_mod)
        d["models"] = list(d.get("models") or []) + [{"id": "sync-verify-b", "provider": "ollama", "label": "파일직접", "roles": ["answer"], "enabled": True}]
        write_json(p_mod, d)
        st, j, _ = req("GET", "/api/models/catalog")
        check("파일 편집 → GET 반영 (mtime 자동)", "models.json", "auto", "sync-verify-b" in json.dumps(j, ensure_ascii=False), st)
        req("POST", "/api/models/catalog", dict(C, action="remove", id="sync-verify-a"), cookie=adm)

        # ---------------- 7. prompts/*.md ----------------
        p_pr = os.path.join(tmp, "prompts", "rerank.md")
        orig = open(p_pr, encoding="utf-8").read()
        st, j, _ = req("POST", "/api/prompts", dict(C, name="rerank", content="TASK=rerank\nUI에서 저장한 문구"), cookie=adm)
        check("UI 저장 → 응답 200", "prompts", "A", st == 200, j.get("error") or "")
        check("UI 저장 → 파일", "prompts", "A", "UI에서 저장한 문구" in open(p_pr, encoding="utf-8").read(), "")
        st, j, _ = req("GET", "/api/prompts?name=rerank")
        check("UI 저장 → GET 반영", "prompts", "A", "UI에서 저장한 문구" in json.dumps(j, ensure_ascii=False), st)
        with open(p_pr, "w", encoding="utf-8") as f:
            f.write("TASK=rerank\n파일에서 고친 문구")
        os.utime(p_pr, (time.time() + 2, time.time() + 2))
        st, j, _ = req("GET", "/api/prompts?name=rerank")
        check("파일 편집 → GET 반영 (mtime 자동)", "prompts", "auto", "파일에서 고친 문구" in json.dumps(j, ensure_ascii=False), st)
        with open(p_pr, "w", encoding="utf-8") as f:
            f.write(orig)

        # ---------------- 8. agents.json ----------------
        p_ag = os.path.join(tmp, "agents.json")
        ag = read_json(p_ag)
        name = next(iter(k for k in ag if not k.startswith("_")), "")
        if name:
            ag[name] = dict(ag[name], retries=7)
            st, j, _ = req("POST", "/api/agents", dict(C, agents=ag), cookie=adm)
            check("UI 저장 → 응답 200", "agents.json", "A", st == 200, j.get("error") or "")
            check("UI 저장 → 파일", "agents.json", "A", read_json(p_ag)[name].get("retries") == 7, read_json(p_ag)[name].get("retries"))
            st, j, _ = req("GET", "/api/agents", cookie=adm)
            check("UI 저장 → GET 반영", "agents.json", "A", ((j.get("agents") or {}).get(name) or {}).get("retries") == 7, st)
            d = read_json(p_ag)
            d[name] = dict(d[name], retries=9)
            write_json(p_ag, d)
            st, j, _ = req("GET", "/api/agents", cookie=adm)
            check("파일 편집 → GET 반영 (저장 시 재적재)", "agents.json", "auto",
                  ((j.get("agents") or {}).get(name) or {}).get("retries") == 9, ((j.get("agents") or {}).get(name) or {}).get("retries"))

        # ---------------- 9. schedule.json ----------------
        p_sc = os.path.join(tmp, "schedule.json")
        st, j, _ = req("POST", "/api/schedule", dict(C, action="add", task={"name": "sync-verify", "every": "1h", "action": {"type": "build"}, "enabled": False}), cookie=adm)
        check("UI 저장 → 응답 200", "schedule", "A", st == 200, j.get("error") or "")
        check("UI 저장 → 파일", "schedule", "A", "sync-verify" in json.dumps(read_json(p_sc), ensure_ascii=False), "")
        st, j, _ = req("GET", "/api/schedule", cookie=adm)
        check("UI 저장 → GET 반영", "schedule", "A", "sync-verify" in json.dumps(j, ensure_ascii=False), st)
        d = read_json(p_sc)
        for t in d.get("tasks") or []:
            if t.get("name") == "sync-verify":
                t["enabled"] = True
        write_json(p_sc, d)
        st, j, _ = req("GET", "/api/schedule", cookie=adm)
        got = [t for t in (j.get("tasks") or []) if t.get("name") == "sync-verify"]
        check("파일 편집 → GET 반영 (자동 재적재)", "schedule", "auto", bool(got) and got[0].get("enabled") is True,
              got[0].get("enabled") if got else "없음")
        req("POST", "/api/schedule", dict(C, action="remove", name="sync-verify"), cookie=adm)

        # ---------------- 10. server.json (동시성) ----------------
        p_sv = os.path.join(tmp, "server.json")
        st, j, _ = req("POST", "/api/admin/server", dict(C, action="set_limits", values={"concurrency.max_parallel_reads": 7}), cookie=adm)
        saved = st == 200
        check("UI 저장 → 응답 200", "server.json", "A", saved, j.get("error") or "")
        if saved:
            check("UI 저장 → 파일", "server.json", "A",
                  ((read_json(p_sv).get("concurrency") or {}).get("max_parallel_reads")) == 7,
                  (read_json(p_sv).get("concurrency") or {}).get("max_parallel_reads"))
            check("UI 저장 → 응답의 유효 한도", "server.json", "A", "7" in json.dumps(j.get("limits") or {}, ensure_ascii=False),
                  json.dumps(j.get("limits") or {}, ensure_ascii=False)[:80])
        d = read_json(p_sv)
        d.setdefault("concurrency", {})["max_parallel_reads"] = 9
        write_json(p_sv, d)
        st, j, _ = req("POST", "/api/admin/server", dict(C, action="reload"), cookie=adm)
        lim = (j.get("limits") or {}).get("concurrency") or {}
        check("파일 편집 → reload API", "server.json", "reload", st == 200 and lim.get("max_parallel_reads") == 9,
              lim.get("max_parallel_reads"))
        st, j, _ = req("GET", "/api/admin/server", cookie=adm)
        blob = json.dumps(j, ensure_ascii=False)
        check("파일 편집 → reload → GET 반영", "server.json", "reload", "9" in blob and "max_parallel_reads" in blob, st)

        # ---------------- 11. .env 가시성 (읽기 전용 + reload) ----------------
        st, j, _ = req("GET", "/api/env", cookie=adm)
        keys = {k.get("name"): k for k in (j.get("keys") or [])}
        check("GET /api/env (admin)", ".env", "-", st == 200 and bool(keys), st)
        ok_mask = bool(keys.get("OPENAI_API_KEY")) and "0123456789abcdef" not in json.dumps(j, ensure_ascii=False)
        check("값이 마스킹된다 (원문 노출 없음)", ".env", "-", ok_mask, (keys.get("OPENAI_API_KEY") or {}).get("masked"))
        check("LLMWIKI_* 오버라이드 목록", ".env", "-", any(o.get("env") == "LLMWIKI_LOG_LEVEL" for o in (j.get("overrides") or [])),
              [o.get("env") for o in (j.get("overrides") or [])][:5])
        st, j, _ = req("GET", "/api/env")
        check("비-admin 은 거부 (401/403)", ".env", "-", st in (401, 403), st)
        p_env = os.path.join(tmp, ".env")
        open(p_env, "a", encoding="utf-8").write("VOYAGE_API_KEY=vy-verify-9876543210abcdef\n")
        st, j, _ = req("POST", "/api/env", dict(C, action="reload"), cookie=adm)
        check("파일 편집 → reload API", ".env", "reload", st == 200 and j.get("ok") is True, j.get("error") or "")
        st, j, _ = req("GET", "/api/env", cookie=adm)
        k2 = {k.get("name"): k for k in (j.get("keys") or [])}
        check("파일 편집 → GET 반영", ".env", "reload", bool((k2.get("VOYAGE_API_KEY") or {}).get("set")),
              (k2.get("VOYAGE_API_KEY") or {}).get("source"))

        # ---------------- 12. fill-defaults 가 실제 파일을 채우는가 ----------------
        r = subprocess.run([PY, "-m", "llmwiki", "config", "fill-defaults", "--all"], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        check("config fill-defaults --all 실행", "fill-defaults", "-", r.returncode == 0, (r.stdout or r.stderr or "").strip().splitlines()[-1:] or "")
        cfg2 = read_json(p_cfg)
        check("config.json 에 answer_mode 가 명시된다", "fill-defaults", "-", "answer_mode" in cfg2, list(cfg2)[:4])
        check("toggles 에 새 토글이 명시된다", "fill-defaults", "-",
              all(k in (cfg2.get("toggles") or {}) for k in ("llm_after_fusion", "llm_after_rerank", "degrade_on_llm_failure")),
              sorted(set(("llm_after_fusion", "llm_after_rerank", "degrade_on_llm_failure")) - set(cfg2.get("toggles") or {})))
        tun2 = read_json(p_tun)
        check("tuning.json 이 기본값까지 명시된다", "fill-defaults", "-", tun2.get("_explicit_defaults") is True and "related_symmetric" in tun2,
              tun2.get("_explicit_defaults"))
        r2 = subprocess.run([PY, "-m", "llmwiki", "config", "fill-defaults", "--all", "--dry-run"], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        again = [ln for ln in (r2.stdout or "").splitlines() if "추가" in ln and "추가 0" not in ln]
        check("한 번 채운 뒤에는 추가할 키가 없다", "fill-defaults", "-", not again, again[:3])
        st, j, _ = req("POST", "/api/config", dict(C, action="reload"), cookie=adm)
        check("fill-defaults 뒤 서버가 파일을 읽는다", "fill-defaults", "reload", st == 200 and j.get("reloaded") is True, j.get("error") or "")

        # ---- Settings 와 🧭 Pipeline 은 같은 서버 값을 본다 (2026-09-19) ----
        # 두 화면이 같은 값을 다르게 보여 주던 문제. Settings 가 쓰는 창구(/api/tuning · /api/config · /api/models/set)로
        # 바꾼 뒤, Pipeline 이 읽는 창구(/api/architecture · /api/limits)에서 **같은 값**이 나오는지 본다.
        # tuning.json 을 원천으로 하는 키(source != config)는 /api/architecture 의 tuning 에,
        # config.json 을 원천으로 하는 키는 settings 에 실린다 — Pipeline 화면의 tuneFile() 과 같은 규칙이다.
        st, j, _ = req("POST", "/api/tuning", dict(C, action="set", values={"router_long_kw": 7}), cookie=adm)
        st2, arch, _ = req("GET", "/api/architecture", cookie=adm)
        check("튜닝 저장이 Pipeline 데이터(/api/architecture)에 바로 보인다", "tuning", "Settings → Pipeline",
              st == 200 and st2 == 200 and int((arch.get("tuning") or {}).get("router_long_kw") or 0) == 7,
              "HTTP %s %s · arch=%s" % (st, (j or {}).get("error") or (j or {}).get("errors") or "", (arch.get("tuning") or {}).get("router_long_kw")))
        st, j, _ = req("POST", "/api/tuning", dict(C, action="set", values={"rerank_candidates": 33}), cookie=adm)
        st2, arch, _ = req("GET", "/api/architecture", cookie=adm)
        check("config 원천 튜닝 키도 Pipeline 데이터에 보인다", "tuning", "Settings → Pipeline",
              st == 200 and st2 == 200 and int((arch.get("settings") or {}).get("rerank_candidates") or 0) == 33,
              "HTTP %s %s · arch=%s" % (st, (j or {}).get("error") or (j or {}).get("errors") or "", (arch.get("settings") or {}).get("rerank_candidates")))
        st, j, _ = req("POST", "/api/config", dict(C, settings={"top_k_final": 9}), cookie=adm)
        st2, arch, _ = req("GET", "/api/architecture", cookie=adm)
        check("config 저장이 Pipeline 데이터에 바로 보인다", "config", "Settings → Pipeline",
              st == 200 and st2 == 200 and int((arch.get("settings") or {}).get("top_k_final") or 0) == 9,
              (arch.get("settings") or {}).get("top_k_final"))
        # 역할별 LLM 타임아웃을 바꾸면 Pipeline 의 '시간 제한' 표와 trace 의 '≤ 제한' 이 같이 바뀐다
        st, j, _ = req("POST", "/api/models/set", dict(C, settings={"llm_roles": {"answer": {"timeout_s": 123}}}), cookie=adm)
        st2, lim, _ = req("GET", "/api/limits", cookie=adm)
        got = next((x["s"] for x in ((lim.get("trace") or {}).get("answer_llm") or []) if x.get("kind") == "llm"), None)
        check("역할 타임아웃 변경이 단계 시간 제한에 반영된다", "models", "Settings → 시간 제한",
              st == 200 and st2 == 200 and float(got or 0) == 123.0, "answer_llm ≤ %ss" % got)
        check("같은 값이 Pipeline 단계 표에도 있다", "models", "Settings → Pipeline",
              float(next((x["s"] for x in ((lim.get("stages") or {}).get("answer") or []) if x.get("kind") == "llm"), 0)) == 123.0,
              "stages.answer")

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=20)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print("임시 폴더 유지: %s" % tmp)


def main(argv=None):
    ap = argparse.ArgumentParser(description="설정 UI ↔ 파일 ↔ 서버 양방향 정합 검증")
    ap.add_argument("--port", type=int, default=8934)
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 지우지 않는다")
    ns = ap.parse_args(argv)
    print("설정 양방향 검증 시작 (격리 환경 준비 — 빌드 포함 1~2분)")
    try:
        run(ns.port, ns.keep)
    except Exception as e:
        import traceback
        traceback.print_exc()
        rows.append({"name": "하네스 예외", "surface": "-", "how": "-", "ok": False, "detail": str(e)[:300]})
    bad = [r for r in rows if not r["ok"]]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"ts": time.strftime("%Y-%m-%d %H:%M"), "total": len(rows), "failed": len(bad), "rows": rows}, f,
                  ensure_ascii=False, indent=1)
    print("\n" + "=" * 96)
    print("| 표면 | 방향 | 검사 | 결과 |")
    print("|---|---|---|---|")
    for r in rows:
        print("| %s | %s | %s | %s |" % (r["surface"], r["how"], r["name"], "OK" if r["ok"] else "**실패** " + r["detail"][:60]))
    print("=" * 96)
    print("결과: %s" % OUT)
    print("\n검사 %d개 중 %d개 통과" % (len(rows), len(rows) - len(bad)))
    for r in bad:
        print("  FAIL [%s/%s] %s — %s" % (r["surface"], r["how"], r["name"], r["detail"][:160]))
    print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
