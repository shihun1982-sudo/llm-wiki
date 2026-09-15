"""Web 전수 검증: 격리 환경에서 serve --host 0.0.0.0 (mode on, anonymous viewer) 를 띄우고 모든 GET/POST 엔드포인트를 게스트/viewer/class1/admin 으로 왕복한다."""
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # <프로젝트 루트>/tools/verify/ 기준
PY = sys.executable
PORT = 8792
tmp = tempfile.mkdtemp(prefix="lwweb_")
def cp(src, dst):
    shutil.copytree(src, dst) if os.path.isdir(src) else shutil.copy2(src, dst)
cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
cfg.update({"data_dir": os.path.join(tmp, "data"), "wiki_dir": os.path.join(tmp, "wiki"), "corpus_dirs": [os.path.join(ROOT, "setup", "sample_corpus_modem")],
            "llm_provider": "mock", "llm_roles": {}, "embed_provider": "hash", "embed_dim": 256})
cfg["toggles"]["mcp_federation"] = True      # 다른 RAG 연동 검증: mock 소스의 search 가 /mcp 에 mock__search 로 노출되어야 한다
json.dump(cfg, open(os.path.join(tmp, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
open(os.path.join(tmp, ".env"), "w").write("PYTHONIOENCODING=utf-8\n")
for f in ("tuning.json", "presets.json", "query_rules.json", "mcp_sources.json", "agents.json", "pins.json", "security.json"):
    cp(os.path.join(ROOT, f), os.path.join(tmp, f))
os.makedirs(os.path.join(tmp, "data"))
cp(os.path.join(ROOT, "data", "rules.json"), os.path.join(tmp, "rules.json"))
cp(os.path.join(ROOT, "schemas"), os.path.join(tmp, "schemas"))
cp(os.path.join(ROOT, "prompts"), os.path.join(tmp, "prompts"))
cp(os.path.join(ROOT, "eval", "questions.json"), os.path.join(tmp, "questions.json"))
_sp = os.path.join(tmp, "mcp_sources.json"); _srcs = json.load(open(_sp, encoding="utf-8")); _srcs["mock"]["enabled"] = True
json.dump(_srcs, open(_sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)     # 다른 RAG 연동 검증용 mock 소스
sec = json.load(open(os.path.join(tmp, "security.json"), encoding="utf-8")); sec["mode"] = "on"; sec["anonymous_role"] = "viewer"
json.dump(sec, open(os.path.join(tmp, "security.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", LLMWIKI_CONFIG=os.path.join(tmp, "config.json"), LLMWIKI_ENV_FILE=os.path.join(tmp, ".env"),
           LLMWIKI_TUNING_PATH=os.path.join(tmp, "tuning.json"), LLMWIKI_PRESETS_PATH=os.path.join(tmp, "presets.json"),
           LLMWIKI_QUERY_RULES_PATH=os.path.join(tmp, "query_rules.json"), LLMWIKI_MCP_SOURCES_PATH=os.path.join(tmp, "mcp_sources.json"),
           LLMWIKI_AGENTS_PATH=os.path.join(tmp, "agents.json"), LLMWIKI_PINS_PATH=os.path.join(tmp, "pins.json"), LLMWIKI_RULES_PATH=os.path.join(tmp, "rules.json"),
           LLMWIKI_SCHEMAS_DIR_PATH=os.path.join(tmp, "schemas"), LLMWIKI_PROMPTS_DIR_PATH=os.path.join(tmp, "prompts"), LLMWIKI_EVAL_PATH=os.path.join(tmp, "questions.json"),
           LLMWIKI_LOGS_DIR_PATH=os.path.join(tmp, "logs"), LLMWIKI_SECURITY_PATH=os.path.join(tmp, "security.json"))
for k in ("LLMWIKI_USER", "LLMWIKI_PASSWORD", "LLMWIKI_API_KEY"):
    ENV.pop(k, None)
subprocess.run([PY, "-m", "llmwiki", "build", "--full", "--yes", "--no-snapshot"], cwd=ROOT, env=ENV, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
subprocess.run([PY, "-m", "llmwiki", "users", "add", "c1", "--role", "class1", "--password", "c1-pass-123"], cwd=ROOT, env=ENV, capture_output=True)
subprocess.run([PY, "-m", "llmwiki", "users", "add", "v1", "--role", "viewer", "--password", "v1-pass-123"], cwd=ROOT, env=ENV, capture_output=True)
proc = subprocess.Popen([PY, "-m", "llmwiki", "serve", "--host", "0.0.0.0", "--port", str(PORT)], cwd=ROOT, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
base = "http://127.0.0.1:%d" % PORT
rows = []
def req(method, path, body=None, cookie="", token="", raw=False, headers=None):
    h = {"Content-Type": "application/json", "X-Requested-With": "llmwiki"}
    h.update(headers or {})
    if cookie: h["Cookie"] = cookie
    if token: h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8") if body is not None else None, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            data = resp.read()
            return resp.status, (data if raw else (json.loads(data.decode("utf-8")) if data.strip() else {})), resp.headers
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, (data if raw else json.loads(data.decode("utf-8"))), e.headers
        except Exception:
            return e.code, data, e.headers
def check(name, method, path, expect, body=None, cookie="", token="", raw=False, headers=None, keep=None):
    st, j, h = req(method, path, body, cookie, token, raw, headers)
    ok = st in (expect if isinstance(expect, tuple) else (expect,))
    if ok and isinstance(j, dict) and j.get("error") and st == 200 and not j.get("result") and not j.get("job") and "trace" not in j:
        ok = False
    if j is None:
        summ = ""
    elif isinstance(j, (bytes, str)):
        summ = j[:60]
    elif isinstance(j, dict):
        summ = json.dumps({k: (v if isinstance(v, (int, str, bool, float)) else "…") for k, v in list(j.items())[:4]}, ensure_ascii=False)[:90]
    else:
        summ = "list[%d]" % len(j)
    rows.append({"name": name, "path": "%s %s" % (method, path), "status": st, "expect": expect, "ok": ok, "out": summ if not isinstance(summ, bytes) else summ.decode("utf-8", "ignore")})
    if keep:
        state[keep] = j
    return st, j, h
state = {}
def wait_job(jid, cookie):
    for _ in range(600):
        st, j, _ = req("GET", "/api/jobs/" + jid, cookie=cookie)
        if j.get("status") != "running":
            return j
        time.sleep(0.3)
    return {"status": "timeout"}
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(base + "/api/auth/me", timeout=2); break
        except Exception:
            time.sleep(0.5)
    # ---- 정적 · 게스트 ----
    check("GET /", "GET", "/", 200, raw=True)
    check("GET /login", "GET", "/login", 200, raw=True)
    for f in ("js/core.js", "js/ask.js", "js/corpus.js", "js/knowledge.js", "js/quality.js", "js/evolve.js", "js/settings.js", "js/observability.js", "style.css", "themes/dark.css", "themes/themes.json"):
        check("static " + f, "GET", "/static/" + f, 200, raw=True)
    check("static traversal", "GET", "/static/../config.json", 404, raw=True)
    check("guest me", "GET", "/api/auth/me", 200, keep="me")
    assert state["me"]["user"]["via"] == "anon", state["me"]
    check("guest status", "GET", "/api/status", 200, keep="status")
    check("guest query", "POST", "/api/query", 200, {"q": "ISSUE-2001 의 원인과 수정 CL 은?", "overrides": {}, "log": True}, keep="q")
    rid = state["q"]["result"]["request_id"]; qid = state["q"]["result"].get("query_id")
    check("guest search", "POST", "/api/search", 200, {"q": "RX DMA", "channel": "fts", "k": 5, "overrides": {}})
    check("guest feedback", "POST", "/api/feedback", 200, {"query_id": qid, "feedback": 1, "note": ""})
    check("guest forensic expect", "POST", "/api/forensic/expect", 200, {"request_id": rid, "docs": "ISSUE-2001", "terms": "FIFO"})
    check("guest forensic llm", "POST", "/api/forensic/llm", 200, {"request_id": rid})
    check("guest propose", "POST", "/api/evolve/propose", 200, {"kind": "corpus_gap", "payload": {"topic": "x"}, "reason": "t"})
    check("guest pins test", "POST", "/api/pins", 200, {"action": "test", "q": "코드 리뷰"})
    check("guest presets apply (memory)", "POST", "/api/presets", 200, {"action": "apply", "names": "token"})
    check("guest query_rules test", "POST", "/api/query_rules", 200, {"action": "test", "q": "PDCCH"}, ) if False else None
    check("guest build → 401", "POST", "/api/build", 401, {"full": False})
    check("guest eval → 401", "POST", "/api/eval", 401, {"k": 5})
    check("guest pins add → 401", "POST", "/api/pins", 401, {"action": "add", "doc": "x"})
    check("guest config → 401", "POST", "/api/config", 401, {"settings": {}})
    check("guest users list → 401", "GET", "/api/auth/users", 401)
    check("guest cli → 401", "POST", "/api/cli", 401, {"argv": "stats"})
    for p in ("/api/graph?limit=20", "/api/entity?id=e:issue-2001", "/api/docs", "/api/queries", "/api/requests?limit=5", "/api/request?id=%d" % rid, "/api/models", "/api/system",
              "/api/watch", "/api/tuning", "/api/architecture", "/api/evolve/status", "/api/evolve/proposals", "/api/wiki/list", "/api/wiki/page?name=INDEX", "/api/eval/questions", "/api/rules",
              "/api/health?quick=1", "/api/config/effective", "/api/presets", "/api/presets/diff?name=speed", "/api/prompts", "/api/prompts?name=answer_guide", "/api/logs/files", "/api/logs?n=5",
              "/api/forensics", "/api/forensics/summary", "/api/forensic?request_id=%d&rerun=1" % rid, "/api/trials", "/api/pins", "/api/query_rules", "/api/query_rules/test?q=PDCCH", "/api/embed/report",
              "/api/build/status", "/api/build/verify", "/api/corpus/lint", "/api/corpus/types", "/api/mcp_sources", "/api/memory", "/api/precompute", "/api/agents", "/api/themes", "/api/time?q=%EC%A7%80%EB%82%9C%EC%A3%BC",
              "/api/progress", "/api/snapshot"):
        check("guest GET " + p.split("?")[0], "GET", p, 200)
    check("guest GET chunk", "GET", "/api/chunk?id=" + urllib.request.quote(state["q"]["result"]["hits"][0]["chunk_id"]), 200)
    check("guest GET doc_chunks", "GET", "/api/doc_chunks?id=" + urllib.request.quote(state["q"]["result"]["hits"][0]["doc_id"]), 200)
    if qid:
        check("guest GET query_trace", "GET", "/api/query_trace?id=%d" % qid, 200)
    check("guest GET audit → 401", "GET", "/api/audit", 401)
    check("guest GET security → 401", "GET", "/api/security", 401)
    check("guest GET apikeys → 401", "GET", "/api/apikeys", 401)
    check("guest 404", "GET", "/api/no/such", 404, raw=True)
    # ---- viewer 로그인 (게스트와 같지만 403) ----
    st, j, h = check("login v1", "POST", "/api/auth/login", 200, {"username": "v1", "password": "v1-pass-123"})
    view = h.get("Set-Cookie").split(";")[0]
    check("login bad", "POST", "/api/auth/login", 401, {"username": "v1", "password": "nope"})
    check("viewer build → 403", "POST", "/api/build", 403, {"full": False}, cookie=view)
    check("viewer password change", "POST", "/api/auth/password", 200, {"old": "v1-pass-123", "new": "v1-pass-456"}, cookie=view)
    check("viewer logout", "POST", "/api/auth/logout", 200, {}, cookie=view)
    # ---- class1 ----
    st, j, h = check("login c1", "POST", "/api/auth/login", 200, {"username": "c1", "password": "c1-pass-123"})
    c1 = h.get("Set-Cookie").split(";")[0]
    check("c1 eval (run) job", "POST", "/api/eval", 200, {"k": 5, "overrides": {}}, cookie=c1, keep="evjob")
    print("  eval job:", wait_job(state["evjob"]["job"], c1)["status"])
    check("c1 pins add (edit) → 428", "POST", "/api/pins", 428, {"action": "add", "doc": "RULE-ISR-001", "keywords": ["리뷰"]}, cookie=c1)
    check("c1 pins add confirmed", "POST", "/api/pins", 200, {"action": "add", "doc": "RULE-ISR-001", "keywords": ["리뷰"], "_confirm": True}, cookie=c1, keep="pin")
    check("c1 pins remove", "POST", "/api/pins", 200, {"action": "remove", "id": state["pin"]["id"], "_confirm": True}, cookie=c1)
    check("c1 build incremental (index) confirmed", "POST", "/api/build", 200, {"full": False, "_confirm": True, "channels": ["fts"]}, cookie=c1, keep="bjob")
    print("  build job:", wait_job(state["bjob"]["job"], c1)["status"])
    check("c1 build full → 403", "POST", "/api/build", 403, {"full": True, "_confirm": True}, cookie=c1)
    check("c1 channel build → 403", "POST", "/api/build", 403, {"channel": "fts", "_confirm": True}, cookie=c1)
    check("c1 config → 403", "POST", "/api/config", 403, {"settings": {}, "_confirm": True}, cookie=c1)
    check("c1 cli stats (run)", "POST", "/api/cli", 200, {"argv": "stats"}, cookie=c1)
    check("c1 cli build --full → 403", "POST", "/api/cli", 403, {"argv": "build --full", "_confirm": True}, cookie=c1)
    # ---- admin ----
    st, j, h = check("login admin", "POST", "/api/auth/login", 200, {"username": "kh82.kim", "password": "1234qwer"})
    adm = h.get("Set-Cookie").split(";")[0]
    C = {"_confirm": True}; D = {"_confirm": True, "_phrase": "DELETE INDEX", "_password": "1234qwer"}
    check("admin GET users", "GET", "/api/auth/users", 200, cookie=adm)
    check("admin GET audit", "GET", "/api/audit?n=10", 200, cookie=adm)
    check("admin GET security", "GET", "/api/security", 200, cookie=adm)
    check("admin GET apikeys", "GET", "/api/apikeys", 200, cookie=adm)
    check("admin models/test", "POST", "/api/models/test", 200, {"which": ["answer"], "overrides": {}}, cookie=adm)
    check("admin models/set", "POST", "/api/models/set", 200, dict(C, settings={"llm_retries": 3}), cookie=adm)
    check("admin config", "POST", "/api/config", 200, dict(C, settings={"top_k_final": 10}), cookie=adm)
    check("admin agents save", "POST", "/api/agents", 200, dict(C, agents=json.load(open(os.path.join(tmp, "agents.json"), encoding="utf-8"))), cookie=adm)
    check("admin mcp_sources test", "POST", "/api/mcp_sources", 200, {"action": "test", "names": ["mock"]}, cookie=adm)
    check("admin mcp_sources ingest dry", "POST", "/api/mcp_sources", 200, dict(C, action="ingest", names=["mock"], dry_run=True), cookie=adm)
    check("admin mcp_sources enrich", "POST", "/api/mcp_sources", 200, {"action": "enrich", "q": "RX AGC"}, cookie=adm)
    st, j, _ = check("admin mcp_sources retrieve (외부 RAG)", "POST", "/api/mcp_sources", 200, {"action": "retrieve", "q": "TX 전력 제어 PA gain", "k": 3}, cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and any(r.get("chunk_id") == "ext:mock:ISSUE-9001" for r in (j or {}).get("results", []))
    check("admin mcp_sources tools", "POST", "/api/mcp_sources", 200, {"action": "tools", "name": "mock"}, cookie=adm)
    check("admin mcp_sources tools (unknown) → 400", "POST", "/api/mcp_sources", 400, {"action": "tools", "name": "nope"}, cookie=adm)
    check("admin mcp_sources federated", "POST", "/api/mcp_sources", 200, {"action": "federated"}, cookie=adm)
    check("guest mcp_sources retrieve → 401 (run 등급)", "POST", "/api/mcp_sources", 401, {"action": "retrieve", "q": "x"})
    # ---- 상세 분석 모드 (2026-09-15) ----
    st, j, _ = check("guest query analysis_mode override", "POST", "/api/query", 200, {"q": "ISSUE-2001 의 원인", "overrides": {"analysis_mode": True}, "log": True})
    _an = ((j or {}).get("result") or {}).get("analysis") or {}
    rows[-1]["ok"] = rows[-1]["ok"] and bool(_an.get("md")) and _an.get("detail_level") == 2
    rows[-1]["out"] = "analysis md=%s detail=%s" % (bool(_an.get("md")), _an.get("detail_level"))
    _rid = ((j or {}).get("result") or {}).get("request_id") or rid
    st, j, _ = check("guest GET /api/analysis (json)", "GET", "/api/analysis?request_id=%d&focus=quality" % _rid, 200)
    rows[-1]["ok"] = rows[-1]["ok"] and "## 5. 품질 렌즈" in ((j or {}).get("markdown") or "") and (j or {}).get("summary", {}).get("request_id") == _rid
    st, j, _ = check("guest GET /api/analysis (md download)", "GET", "/api/analysis?request_id=%d&format=md&download=1" % _rid, 200, raw=True)
    rows[-1]["ok"] = rows[-1]["ok"] and b"# \xec\xa7\x88\xec\x9d\x98 \xec\x83\x81\xec\x84\xb8 \xeb\xb6\x84\xec\x84\x9d" in (j if isinstance(j, bytes) else b"")
    check("guest GET /api/analysis (unknown) → 404", "GET", "/api/analysis?request_id=999999", 404)
    st, j, _ = check("mcp wiki_analysis", "POST", "/mcp", 200, {"jsonrpc": "2.0", "id": 31, "method": "tools/call", "params": {"name": "wiki_analysis", "arguments": {"request_id": _rid, "focus": "speed"}}})
    rows[-1]["ok"] = rows[-1]["ok"] and not ((j or {}).get("result") or {}).get("isError") and "속도 렌즈" in json.dumps(j, ensure_ascii=False)
    st, j, _ = check("admin query external_rag override", "POST", "/api/query", 200, {"q": "TX 전력 제어 PA gain 테이블 인덱스 오류", "overrides": {"external_rag": True, "rerank_llm": False}, "log": False}, cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and any(h.get("doc_id", "").startswith("ext:mock:") for h in ((j or {}).get("result") or {}).get("hits", []))
    srcs = json.load(open(os.path.join(tmp, "mcp_sources.json"), encoding="utf-8"))
    check("admin mcp_sources save", "POST", "/api/mcp_sources", 200, dict(C, action="save", sources={k: v for k, v in srcs.items() if not k.startswith("_")}), cookie=adm)
    check("admin tuning set", "POST", "/api/tuning", 200, dict(C, action="set", values={"doc_expand_max_chunks": 4}), cookie=adm)
    check("admin tuning reset", "POST", "/api/tuning", 200, dict(C, action="reset", key="doc_expand_max_chunks"), cookie=adm)
    check("admin presets save", "POST", "/api/presets", 200, dict(C, action="save", presets={k: v for k, v in json.load(open(os.path.join(tmp, "presets.json"), encoding="utf-8")).items() if not k.startswith("_")}), cookie=adm)
    check("admin presets apply save", "POST", "/api/presets", 200, dict(C, action="apply", names="token", save=True), cookie=adm)
    check("admin prompts set", "POST", "/api/prompts", 200, dict(C, name="rerank", content="TASK=rerank\ntest"), cookie=adm)
    check("admin prompts reset", "POST", "/api/prompts", 200, dict(C, action="reset", name="rerank"), cookie=adm)
    check("admin query_rules add", "POST", "/api/query_rules", 200, dict(C, action="add", type="synonym", term="언더런", values=["underrun"]), cookie=adm)
    check("admin query_rules remove", "POST", "/api/query_rules", 200, dict(C, action="remove", type="synonym", term="언더런"), cookie=adm)
    qr = json.load(open(os.path.join(tmp, "query_rules.json"), encoding="utf-8"))
    check("admin query_rules save", "POST", "/api/query_rules", 200, dict(C, action="save", rules={k: v for k, v in qr.items() if not k.startswith("_")}), cookie=adm)
    check("admin rules save", "POST", "/api/rules", 200, dict(C, rules=json.load(open(os.path.join(tmp, "rules.json"), encoding="utf-8"))), cookie=adm)
    check("admin wiki page save", "POST", "/api/wiki/page", 200, dict(C, name="_gaps", content="# _gaps\n\n## 편집 노트\n\ntest"), cookie=adm)
    check("admin wiki page bad name", "POST", "/api/wiki/page", 400, dict(C, name="../x", content="x"), cookie=adm)
    check("admin evolve review", "POST", "/api/evolve/review", 200, {}, cookie=adm)
    st, j, _ = check("admin propose pin", "POST", "/api/evolve/propose", 200, {"kind": "pin", "payload": {"doc": "RULE-ISR-001", "keywords": ["리뷰"]}, "reason": "t"}, cookie=adm)
    check("admin evolve apply", "POST", "/api/evolve/apply", 200, dict(C, id=j["id"], evaluate=False), cookie=adm)
    st, j, _ = check("admin propose synonym", "POST", "/api/evolve/propose", 200, {"kind": "synonym", "payload": {"term": "a", "expansion": "b"}, "reason": "t"}, cookie=adm)
    check("admin evolve reject", "POST", "/api/evolve/reject", 200, dict(C, id=j["id"], note="no"), cookie=adm)
    check("admin memory decay", "POST", "/api/memory", 200, dict(C, action="decay"), cookie=adm)
    check("admin memory consolidate", "POST", "/api/memory", 200, dict(C, action="consolidate"), cookie=adm)
    check("admin build verify fix", "POST", "/api/build/verify", 200, dict(C, fix=True), cookie=adm)
    check("admin precompute run job", "POST", "/api/precompute", 200, dict(C, action="run", from_log=2), cookie=adm, keep="pcjob")
    print("  precompute job:", wait_job(state["pcjob"]["job"], adm)["status"])
    check("admin precompute doc_vectors", "POST", "/api/precompute", 200, dict(C, action="doc_vectors"), cookie=adm)
    check("admin precompute clear", "POST", "/api/precompute", 200, dict(C, action="clear"), cookie=adm)
    for a in ("vacuum", "fts_optimize", "wal_checkpoint", "clear_cache", "warm_cache", "refresh_doc_refs"):
        check("admin maintenance " + a, "POST", "/api/maintenance", 200, dict(C, action=a), cookie=adm)
    check("admin maintenance purge → 428", "POST", "/api/maintenance", 428, dict(C, action="purge_requests"), cookie=adm)
    check("admin maintenance purge confirmed", "POST", "/api/maintenance", 200, dict(D, action="purge_requests"), cookie=adm)
    check("admin trials run job", "POST", "/api/trials", 200, {"action": "run", "name": "w1", "k": 5, "overrides": {}}, cookie=adm, keep="trjob")
    tj = wait_job(state["trjob"]["job"], adm); print("  trial job:", tj["status"])
    check("admin trials compare", "GET", "/api/trials/compare?ids=w1,w1", 200, cookie=adm)
    check("admin trial get", "GET", "/api/trial?id=w1", 200, cookie=adm)
    check("admin trials delete", "POST", "/api/trials", 200, dict(C, action="delete", id=tj["result"]["trial_id"]), cookie=adm)
    check("admin fusion compare job", "POST", "/api/fusion/compare", 200, {"methods": ["rrf"], "k": 5}, cookie=adm, keep="fujob")
    print("  fusion job:", wait_job(state["fujob"]["job"], adm)["status"])
    check("admin watch scan", "POST", "/api/watch", 200, {"action": "scan"}, cookie=adm)
    check("admin watch start", "POST", "/api/watch", 200, dict(C, action="start", interval=300), cookie=adm)
    check("admin watch tick", "POST", "/api/watch", 200, dict(C, action="tick"), cookie=adm)
    check("admin watch stop", "POST", "/api/watch", 200, dict(C, action="stop"), cookie=adm)
    check("admin snapshot create", "POST", "/api/snapshot", 200, dict(C, action="create", tag="w"), cookie=adm, keep="snap")
    check("admin snapshot restore → 428", "POST", "/api/snapshot", 428, dict(C, action="restore", name=state["snap"]["name"]), cookie=adm)
    check("admin snapshot restore confirmed", "POST", "/api/snapshot", 200, dict(D, action="restore", name=state["snap"]["name"]), cookie=adm)
    check("admin snapshot prune", "POST", "/api/snapshot", 200, dict(C, action="prune", keep=1), cookie=adm)
    check("admin channel build → 428 phrase", "POST", "/api/build", 428, dict(C, channel="vector"), cookie=adm)
    check("admin channel build confirmed", "POST", "/api/build", 200, dict(D, channel="vector"), cookie=adm, keep="cbjob")
    print("  channel job:", wait_job(state["cbjob"]["job"], adm)["status"])
    check("admin build full confirmed", "POST", "/api/build", 200, dict(D, full=True, reset=True), cookie=adm, keep="fbjob")
    print("  full build job:", wait_job(state["fbjob"]["job"], adm)["status"])
    check("admin build purge_logs wrong pw → 428", "POST", "/api/build", 428, dict(C, full=True, purge_logs=True, _phrase="DELETE INDEX", _password="x"), cookie=adm)
    check("admin users add", "POST", "/api/auth/users", 200, dict(C, action="add", name="w2", password="w2-pass-123", role="class2"), cookie=adm)
    check("admin users set_role", "POST", "/api/auth/users", 200, dict(C, action="set_role", name="w2", role="class3"), cookie=adm)
    check("admin users set_password", "POST", "/api/auth/users", 200, dict(C, action="set_password", name="w2", password="w2-pass-456"), cookie=adm)
    check("admin users remove self → 400", "POST", "/api/auth/users", 400, dict(C, action="remove", name="kh82.kim"), cookie=adm)
    check("admin users remove", "POST", "/api/auth/users", 200, dict(C, action="remove", name="w2"), cookie=adm)
    check("admin security set_permission", "POST", "/api/security", 200, dict(C, action="set_permission", key="run", role="viewer"), cookie=adm)
    check("admin security set_permissions", "POST", "/api/security", 200, dict(C, action="set_permissions", permissions={"levels": {"run": "class3"}, "ops": {"/api/eval": "class2"}}), cookie=adm)
    check("admin security set_anonymous", "POST", "/api/security", 200, dict(C, action="set_anonymous", role="viewer"), cookie=adm)
    check("admin security set_cli", "POST", "/api/security", 200, dict(C, action="set_cli", default_role="admin", require_login=False), cookie=adm)
    check("admin security reload", "POST", "/api/security", 200, dict(C, action="reload"), cookie=adm)
    check("admin security bad action", "POST", "/api/security", 400, dict(C, action="zzz"), cookie=adm)
    st, j, _ = check("admin apikeys add", "POST", "/api/apikeys", 200, dict(C, action="add", name="w-key", role="viewer"), cookie=adm)
    tok = j["token"]
    check("apikey query", "POST", "/api/query", 200, {"q": "RX DMA underrun", "overrides": {"llm_answer": False}}, token=tok)
    check("mcp initialize (key)", "POST", "/mcp", 200, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, token=tok)
    st, j, _ = check("mcp tools/list (guest)", "POST", "/mcp", 200, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    _names = {t["name"] for t in ((j or {}).get("result") or {}).get("tools", [])}
    rows[-1]["ok"] = rows[-1]["ok"] and {"wiki_sources", "wiki_external_search", "mock__search"} <= _names
    rows[-1]["out"] = "tools=%d federated=%s" % (len(_names), sorted(n for n in _names if "__" in n))
    st, j, _ = check("mcp federated call mock__search", "POST", "/mcp", 200, {"jsonrpc": "2.0", "id": 21, "method": "tools/call", "params": {"name": "mock__search", "arguments": {"q": "AGC", "limit": 2}}}, token=tok)
    rows[-1]["ok"] = rows[-1]["ok"] and not ((j or {}).get("result") or {}).get("isError") and "ISSUE-9002" in json.dumps(j)
    st, j, _ = check("mcp wiki_external_search", "POST", "/mcp", 200, {"jsonrpc": "2.0", "id": 22, "method": "tools/call", "params": {"name": "wiki_external_search", "arguments": {"query": "PA gain", "k": 2}}}, token=tok)
    rows[-1]["ok"] = rows[-1]["ok"] and "ext:mock:ISSUE-9001" in json.dumps(j)
    st, j, _ = check("mcp federated call with depth header → guarded", "POST", "/mcp", 200, {"jsonrpc": "2.0", "id": 23, "method": "tools/list"}, token=tok, headers={"X-LLMWiki-Federation-Depth": "1"})
    rows[-1]["ok"] = rows[-1]["ok"] and not any("__" in t["name"] for t in ((j or {}).get("result") or {}).get("tools", []))
    check("mcp bad token → 401", "POST", "/mcp", 401, {"jsonrpc": "2.0", "id": 3, "method": "ping"}, token="lwk_x_y")
    for tool, args in (("wiki_query", {"question": "ISSUE-2001 원인", "mode": "fast"}), ("wiki_search", {"channel": "graph", "query": "ISSUE-2001"}), ("wiki_related", {"text": "RX DMA underrun PHY 재시작"}),
                       ("wiki_doc", {"id": "CL-55301"}), ("wiki_entity", {"name": "ISSUE-2001"}), ("wiki_propose", {"kind": "corpus_gap", "payload": {"topic": "x"}}),
                       ("wiki_feedback", {"query_id": qid or 1, "feedback": 1}), ("wiki_forensic", {"expected_docs": ["ISSUE-2001"], "expected_terms": ["FIFO"]}), ("wiki_status", {})):
        st, j, _ = check("mcp " + tool, "POST", "/mcp", 200, {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": tool, "arguments": args}}, token=tok)
        if isinstance(j, dict) and (j.get("result") or {}).get("isError"):
            rows[-1]["ok"] = False; rows[-1]["out"] = "isError: " + j["result"]["content"][0]["text"][:80]
    check("mcp GET → 405", "GET", "/mcp", 405, token=tok, raw=True)
    check("mcp DELETE", "DELETE", "/mcp", 200, token=tok)
    check("admin apikeys remove", "POST", "/api/apikeys", 200, dict(C, action="remove", id=j.get("id") if False else tok.split("_")[1]), cookie=adm)
    check("admin cli console", "POST", "/api/cli", 200, {"argv": "forensic summary"}, cookie=adm)
    check("admin cli serve → 428 (admin 등급 확인)", "POST", "/api/cli", 428, {"argv": "serve"}, cookie=adm)
    check("admin cli serve blocked", "POST", "/api/cli", 200, dict(C, argv="serve"), cookie=adm)
    check("bad apikey on /api/query → 401", "POST", "/api/query", 401, {"q": "x"}, token="lwk_x_y")
    check("non-lwk bearer → guest", "GET", "/api/auth/me", 200, token="opaque-proxy-token")
    check("admin cli purge → 428", "POST", "/api/cli", 428, dict(C, argv="maintenance purge_requests"), cookie=adm)
    check("admin cli purge confirmed", "POST", "/api/cli", 200, dict(D, argv="maintenance purge_requests"), cookie=adm)
    check("csrf origin → 403", "POST", "/api/query", 403, {"q": "x"}, cookie=adm, headers={"Origin": "http://evil.example"})
    check("admin logout", "POST", "/api/auth/logout", 200, {}, cookie=adm)
finally:
    proc.terminate()
    try:
        proc.communicate(timeout=5)
    except Exception:
        proc.kill()
bad = [r for r in rows if not r["ok"]]
print("WEB 검증: %d 요청 중 %d 통과, %d 실패" % (len(rows), len(rows) - len(bad), len(bad)))
for r in rows:
    print("%s %-40s %-34s %s%s  %s" % ("OK " if r["ok"] else "FAIL", r["name"], r["path"][:34], r["status"], "" if r["ok"] else " (expect %s)" % (r["expect"],), r["out"]))
json.dump(rows, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_web_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
shutil.rmtree(tmp, ignore_errors=True)
