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
           LLMWIKI_LOGS_DIR_PATH=os.path.join(tmp, "logs"), LLMWIKI_SECURITY_PATH=os.path.join(tmp, "security.json"),
           LLMWIKI_SERVER_PATH=os.path.join(tmp, "server.json"), LLMWIKI_SCHEDULE_PATH=os.path.join(tmp, "schedule.json"),
           LLMWIKI_MODELS_PATH=os.path.join(tmp, "models.json"))
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
    # ---- 최적화 자료 묶음 (2026-09-16) — Ask 의 📦 버튼 3종이 부르는 경로 ----
    st, j, _ = check("guest GET /api/optimize/guide", "GET", "/api/optimize/guide", 200, raw=True)
    _g = (j if isinstance(j, bytes) else b"").decode("utf-8", "replace")
    rows[-1]["ok"] = rows[-1]["ok"] and "손잡이" in _g and len(_g) > 3000
    rows[-1]["out"] = "guide %d자" % len(_g)
    st, j, _ = check("guest GET /api/optimize/bundle (md)", "GET", "/api/optimize/bundle?request_id=%d&focus=quality" % _rid, 200, raw=True)
    _b = (j if isinstance(j, bytes) else b"").decode("utf-8", "replace")
    # 묶음은 A 설정 · B 실측 · C 지시문 · D 손잡이 지도를 한 파일로 — 최상위 제목이 하나여야 목차가 충돌하지 않는다.
    # analysis_mode 질의의 리포트에는 프롬프트 샘플이 코드블록으로 들어 있고 그 안에도 '# ' 줄이 있다 → 펜스 밖만 센다.
    def _h1_outside_fences(text):
        """코드펜스 밖의 '# ' 제목 수. 펜스는 **여는 길이 이상**의 같은 문자로만 닫힌다 —
        프롬프트 샘플처럼 안에 ``` 가 든 블록은 ```` 로 감싸므로 단순 토글로는 어긋난다."""
        n, open_len, open_ch = 0, 0, ""
        for ln in text.splitlines():
            s = ln.strip()
            m = re.match(r"^(`{3,}|~{3,})", s)
            if m:
                ch, ln_len = m.group(1)[0], len(m.group(1))
                if not open_len:
                    open_len, open_ch = ln_len, ch
                    continue
                if ch == open_ch and ln_len >= open_len:
                    open_len, open_ch = 0, ""
                continue
            if not open_len and ln.startswith("# "):
                n += 1
        return n
    _h1 = _h1_outside_fences(_b)
    rows[-1]["ok"] = rows[-1]["ok"] and len(_b) > len(_g) and _h1 == 1
    rows[-1]["out"] = "bundle %d자 · 코드펜스 밖 h1 %d개" % (len(_b), _h1)
    st, j, _ = check("guest GET /api/optimize/bundle (json)", "GET", "/api/optimize/bundle?request_id=%d&focus=all&format=json" % _rid, 200)
    rows[-1]["ok"] = rows[-1]["ok"] and bool((j or {}).get("markdown"))
    check("guest GET /api/optimize/bundle (unknown) → 404", "GET", "/api/optimize/bundle?request_id=999999", 404)
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
    # ---- 다중 사용자 동시성 · 모니터 · 스케줄 · 모델 카탈로그 (2026-09-15) ----
    check("guest activity (viewer 도 조회)", "GET", "/api/activity", 200)
    st, j, _ = check("guest activity 필드", "GET", "/api/activity?history=5", 200)
    rows[-1]["ok"] = rows[-1]["ok"] and all(k in (j or {}) for k in ("running", "queued", "external", "recent", "lock", "limits"))
    rows[-1]["out"] = "running=%d queued=%d admin=%s" % (len(j.get("running", [])), len(j.get("queued", [])), j.get("admin"))
    check("guest admin/server → 401 (로그인 안내)", "GET", "/api/admin/server", 401)
    check("guest cancel unknown → 404", "POST", "/api/activity", 404, {"action": "cancel", "token": "nope"})
    st, j, _ = check("admin server status", "GET", "/api/admin/server", 200, cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and all(k in (j or {}) for k in ("counters", "limits", "clients", "latency", "activity", "sessions", "circuits"))
    rows[-1]["out"] = "uptime=%ss clients=%d" % (j.get("uptime_s"), len(j.get("clients", [])))
    check("admin set_limits", "POST", "/api/admin/server", 200, dict(C, action="set_limits", values={"concurrency.max_parallel_reads": 6, "rate_limit.per_user_per_min": 500}, save=True), cookie=adm)
    check("admin set_limits (없는 키) → 400", "POST", "/api/admin/server", 400, dict(C, action="set_limits", values={"concurrency.nope": 1}), cookie=adm)
    check("admin block ip", "POST", "/api/admin/server", 200, dict(C, action="block", kind="ip", value="203.0.113.7", add=True), cookie=adm)
    check("admin unblock ip", "POST", "/api/admin/server", 200, dict(C, action="block", kind="ip", value="203.0.113.7", add=False), cookie=adm)
    check("admin sessions list", "POST", "/api/admin/server", 200, dict(C, action="sessions", sub="list"), cookie=adm)
    check("admin circuit reset", "POST", "/api/admin/server", 200, dict(C, action="circuit_reset"), cookie=adm)
    check("admin log_level DEBUG", "POST", "/api/admin/server", 200, dict(C, action="log_level", level="DEBUG", save=False), cookie=adm)
    check("admin query (DEBUG 로그)", "POST", "/api/query", 200, {"q": "ISSUE-2001 원인", "log": False}, cookie=adm)
    check("admin log_level INFO", "POST", "/api/admin/server", 200, dict(C, action="log_level", level="INFO", save=False), cookie=adm)
    check("admin maintenance on", "POST", "/api/admin/server", 200, dict(C, action="maintenance", enabled=True), cookie=adm)
    check("점검 모드: 게스트 질의 → 503", "POST", "/api/query", 503, {"q": "x"})
    check("점검 모드: admin 은 통과", "POST", "/api/query", 200, {"q": "ISSUE-2001 원인", "log": False}, cookie=adm)
    check("admin maintenance off", "POST", "/api/admin/server", 200, dict(C, action="maintenance", enabled=False), cookie=adm)
    check("게스트 질의 복구", "POST", "/api/query", 200, {"q": "ISSUE-2001 원인", "log": False})
    st, j, _ = check("models catalog 조회 (llm·embed·rerank)", "GET", "/api/models/catalog", 200)
    # 임베딩·리랭크 모델도 카탈로그에서 고를 수 있어야 한다 (예전에는 화면에 목록이 없었다)
    rows[-1]["ok"] = rows[-1]["ok"] and (j or {}).get("models") and (j or {}).get("embed") and (j or {}).get("rerank")
    rows[-1]["out"] = "models=%d embed=%d rerank=%d" % (len(j.get("models", [])), len(j.get("embed", [])), len(j.get("rerank", [])))
    # "지금 쓰는 모델" 에 역할 LLM 뿐 아니라 임베딩·리랭크(API)도 들어 있어야 한다
    st, j2, _ = check("models catalog: 지금 쓰는 모델에 embed·rerank 포함", "GET", "/api/models/catalog", 200)
    _iu = (j2 or {}).get("in_use") or {}
    rows[-1]["ok"] = rows[-1]["ok"] and "embed" in _iu and "rerank_api" in _iu and any(v.get("kind") == "llm" for v in _iu.values())
    rows[-1]["out"] = "in_use=%s" % ",".join(sorted(_iu))
    check("admin catalog add (kind=rerank)", "POST", "/api/models/catalog", 200,
          dict(C, action="add", model={"id": "web-verify-rerank", "provider": "cohere", "label": "웹검증 리랭크", "kind": "rerank"}), cookie=adm)
    st, j3, _ = check("catalog add(kind=rerank) 반영", "GET", "/api/models/catalog", 200)
    rows[-1]["ok"] = rows[-1]["ok"] and any(m["id"] == "web-verify-rerank" for m in (j3 or {}).get("rerank", []))
    check("admin catalog remove (rerank)", "POST", "/api/models/catalog", 200, dict(C, action="remove", id="web-verify-rerank"), cookie=adm)
    # ---- 요청 이력 (2026-09-16): 내 요청 / 전체 / 보관 파일에서 다시 보기 ----
    st, jr, _ = check("guest GET /api/requests (내 요청 목록)", "GET", "/api/requests?limit=5", 200)
    rows[-1]["ok"] = rows[-1]["ok"] and isinstance(jr, dict) and "rows" in jr and "live" in jr and "can_all" in jr
    rows[-1]["out"] = "rows=%d live=%d scope=%s can_all=%s" % (len((jr or {}).get("rows") or []), len((jr or {}).get("live") or []),
                                                               (jr or {}).get("scope"), (jr or {}).get("can_all"))
    st, jr2, _ = check("admin GET /api/requests?scope=all", "GET", "/api/requests?scope=all&limit=5", 200, cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and (jr2 or {}).get("scope") == "all" and (jr2 or {}).get("can_all") is True
    st, jr3, _ = check("요청 상세에 보관 파일 경로", "GET", "/api/request?id=%d" % _rid, 200)
    rows[-1]["ok"] = rows[-1]["ok"] and (jr3 or {}).get("id") == _rid
    rows[-1]["out"] = "file=%s" % os.path.basename(str((jr3 or {}).get("file") or "(없음)"))
    check("없는 요청 → 404", "GET", "/api/request?id=99999999", 404)
    # brief=1: 진행 중 작업 목록의 상세 패널용 — trace 를 빼고 답변 요약만 (느린 질의가 떠 있을 때 먼저 도착해야 한다)
    _full_raw, _brief_raw = req("GET", "/api/request?id=%d" % _rid, raw=True)[1], req("GET", "/api/request?id=%d&brief=1" % _rid, raw=True)[1]
    st, jb, _ = check("요청 상세 brief=1 (trace 제외)", "GET", "/api/request?id=%d&brief=1" % _rid, 200)
    rows[-1]["ok"] = rows[-1]["ok"] and (jb or {}).get("brief") is True and (jb or {}).get("trace") is None
    rows[-1]["ok"] = rows[-1]["ok"] and (jb or {}).get("id") == _rid and isinstance((jb or {}).get("result"), dict)
    _fk, _bk = len(_full_raw) / 1024.0, len(_brief_raw) / 1024.0
    rows[-1]["out"] = "%.0fKB → %.0fKB (%.0f%% 감소)" % (_fk, _bk, 100 * (1 - _bk / max(_fk, 1)))
    st, jb2, _ = check("brief 에도 화면이 쓰는 항목은 남는다", "GET", "/api/request?id=%d&brief=1" % _rid, 200)
    _r = (jb2 or {}).get("result") or {}
    rows[-1]["ok"] = rows[-1]["ok"] and bool(_r.get("answer")) and ("hits" not in _r)
    rows[-1]["out"] = "answer %d자 · hits_brief %d건 · hits 제외=%s" % (
        len(_r.get("answer") or ""), len(_r.get("hits_brief") or []), "hits" not in _r)
    st, jm, _ = check("admin maintenance prune_requests (보관 파일 정리)", "POST", "/api/maintenance", 200, dict(C, action="prune_requests"), cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and "archive" in (jm or {})
    rows[-1]["out"] = json.dumps((jm or {}).get("archive") or {}, ensure_ascii=False)[:80]
    # ---- 단계 재실행 (2026-09-17): 저장해 둔 중간 결과로 특정 단계부터 (docs/RERUN.md) ----
    # 중간 결과는 rerun_keep 개만 남으므로, 앞에서 쓰던 요청은 이미 정리되었을 수 있다 → **바로 앞에서** 한 번 더 질의한다.
    st, _jq, _ = check("재실행용 질의 (중간 결과 저장)", "POST", "/api/query", 200, {"q": "ISSUE-2001 의 원인과 수정 CL", "log": True})
    _rrid = ((_jq or {}).get("result") or {}).get("request_id") or _rid
    rows[-1]["out"] = "request_id=%s rerun=%s" % (_rrid, json.dumps(((_jq or {}).get("result") or {}).get("rerun") or {}, ensure_ascii=False)[:60])
    st, jrr, _ = check("재실행 정보 (재시작점 표 + 저장 여부)", "GET", "/api/rerun?request_id=%s" % _rrid, 200)
    _pts = [p["id"] for p in ((jrr or {}).get("points") or [])]
    rows[-1]["ok"] = rows[-1]["ok"] and "answer_llm" in _pts and "claim_check" in _pts and isinstance((jrr or {}).get("stage_point"), dict)
    rows[-1]["out"] = "points=%d available=%s compatible=%s capture=%s" % (len(_pts), (jrr or {}).get("available"),
                                                                          (jrr or {}).get("compatible"), (jrr or {}).get("capture_on"))
    if (jrr or {}).get("available"):
        # 답변부터: 검색·컨텍스트는 재생되고 answer_llm 만 다시 계산되어야 한다
        st, jr4, _ = check("재실행: 답변부터", "POST", "/api/query/rerun", 200, {"request_id": _rrid, "from": "answer_llm"})
        _tr = (jr4 or {}).get("trace") or {}
        _rep = []
        def _walk(n):
            if n.get("replayed"):
                _rep.append(n.get("name"))
            for c in n.get("children") or []:
                _walk(c)
        _walk(_tr)
        rows[-1]["ok"] = rows[-1]["ok"] and "context" in _rep and "answer_llm" not in _rep and bool((jr4 or {}).get("result", {}).get("answer"))
        rows[-1]["out"] = "재생 %d단계: %s | 단계 %s" % (len(_rep), ",".join(_rep[:6]),
                                                    ",".join(str(c.get("name")) for c in (_tr.get("children") or [])[:8]))
        # 설정을 바꿔서 재실행 (이게 이 기능의 목적이다)
        st, jr5, _ = check("재실행: 설정 바꿔서", "POST", "/api/query/rerun", 200,
                           {"request_id": _rrid, "from": "claim_check", "overrides": {"claim_check": False}})
        rows[-1]["ok"] = rows[-1]["ok"] and (jr5 or {}).get("result", {}).get("claims") in (None, {})
        rows[-1]["out"] = "claims=%s (껐으므로 없어야 함)" % ((jr5 or {}).get("result", {}).get("claims") is not None)
    check("재실행: 모르는 단계 → 400", "POST", "/api/query/rerun", 400, {"request_id": _rrid, "from": "없는단계"})
    check("재실행: request_id 없음 → 400", "POST", "/api/query/rerun", 400, {"from": "answer_llm"})
    check("재실행: 없는 요청 → 400", "POST", "/api/query/rerun", 400, {"request_id": 99999999, "from": "answer_llm"})
    # ---- 그동안 하네스가 건드리지 않던 기능 3종 (2026-09-17 커버리지 점검에서 발견) ----
    # 권한 미리보기: admin 이 "viewer 에게는 어떻게 보이나" 를 확인하는 기능. 권한을 **낮추기만** 한다.
    st, jp, hp = check("권한 미리보기 켜기 (viewer 로)", "POST", "/api/auth/preview", 200, dict(C, role="viewer"), cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and (jp or {}).get("preview") == "viewer"
    # 미리보기는 **별도 쿠키**로 전달된다. 그 쿠키를 같이 보내지 않으면 검사가 아무것도 확인하지 못한다
    # (처음 작성했을 때 실제로 그랬고, admin 권한 그대로 200 이 나왔다).
    _pv = "; ".join(v.split(";")[0] for v in (hp.get_all("Set-Cookie") or [])) if hasattr(hp, "get_all") else ""
    adm_pv = (adm + "; " + _pv) if _pv else adm
    rows[-1]["out"] = "preview 쿠키 %s" % ("받음" if _pv else "없음!")
    st, _jme, _ = check("미리보기 중에는 admin 전용 조회가 막힌다", "GET", "/api/auth/users", (401, 403), cookie=adm_pv)
    rows[-1]["out"] = "HTTP %s (viewer 로 낮춰 보는 중)" % st
    st, jq, _ = check("미리보기 중에도 일반 질의는 된다", "POST", "/api/query", 200, {"q": "ISSUE-2001", "log": False}, cookie=adm_pv)
    rows[-1]["ok"] = rows[-1]["ok"] and bool(((jq or {}).get("result") or {}).get("answer"))
    st, jp2, hp2 = check("권한 미리보기 끄기", "POST", "/api/auth/preview", 200, dict(C, role=""), cookie=adm_pv)
    _pv2 = "; ".join(v.split(";")[0] for v in (hp2.get_all("Set-Cookie") or [])) if hasattr(hp2, "get_all") else ""
    st, jme2, _ = check("끄면 admin 으로 돌아온다", "GET", "/api/auth/users", 200,
                        cookie=(adm + "; " + _pv2) if _pv2 else adm)
    rows[-1]["ok"] = rows[-1]["ok"] and isinstance((jme2 or {}).get("users"), list)
    check("미리보기로 권한을 **올릴 수는 없다**", "POST", "/api/auth/preview", 400, dict(C, role="없는역할"), cookie=adm)

    # 내 화면 설정 저장 (계정별) — 게스트는 서버에 저장하지 않는다
    st, jpf, _ = check("내 설정 조회 (admin)", "GET", "/api/profile", 200, cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and "profile" in (jpf or {})
    st, jpf2, _ = check("내 설정 저장", "POST", "/api/profile", 200, dict(C, profile={"theme": "dark", "verify": True}), cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and (jpf2 or {}).get("ok") is not False
    st, jpf3, _ = check("저장한 설정이 다시 읽힌다", "GET", "/api/profile", 200, cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and ((jpf3 or {}).get("profile") or {}).get("theme") == "dark"
    check("게스트는 서버에 저장하지 않는다 → 403", "POST", "/api/profile", 403, dict(C, profile={"x": 1}))
    check("내 설정 초기화", "POST", "/api/profile", 200, dict(C, action="reset"), cookie=adm)

    # 분석 렌즈 소견 (Ask 패널의 🧠) — 질의 하나에 대해 LLM 소견을 만든다
    # mock LLM 은 소견 JSON 을 만들지 못하므로 `parsed:false` 가 정상이다. 여기서 보는 것은
    # "엔드포인트가 살아 있고, LLM 실패를 500 이 아니라 **구조화된 결과**로 돌려주는가" 다.
    st, jai, _ = check("분석 LLM 소견 (mock 은 parsed:false 가 정상)", "POST", "/api/analysis/insight", 200, {"request_id": _rrid})
    rows[-1]["ok"] = st == 200 and isinstance(jai, dict) and "available" in jai
    rows[-1]["out"] = "available=%s parsed=%s" % ((jai or {}).get("available"), (jai or {}).get("parsed"))
    check("분석 소견: 없는 요청 → 404", "POST", "/api/analysis/insight", 404, {"request_id": 99999999})

    # ---- 협업: 휘발성 채팅 + 게시판 (2026-09-16). 부수 기능이라 토글로 끄면 404 여야 한다 ----
    st, jc, _ = check("collab 상태 조회", "GET", "/api/collab", 200)
    rows[-1]["ok"] = rows[-1]["ok"] and isinstance(jc, dict) and "messages" in jc and "people" in jc and "config" in jc
    st, jc2, _ = check("collab 채팅 (휘발성)", "POST", "/api/collab", 200, {"action": "say", "text": "검증용 메시지"})
    rows[-1]["ok"] = rows[-1]["ok"] and (jc2 or {}).get("ok") and (jc2 or {}).get("command") is None
    st, jc3, _ = check("collab /게시 는 명령으로 인식", "POST", "/api/collab", 200, {"action": "say", "text": "/게시 검증 제목"})
    rows[-1]["ok"] = rows[-1]["ok"] and ((jc3 or {}).get("command") or {}).get("name") == "post"
    rows[-1]["out"] = json.dumps((jc3 or {}).get("command") or {}, ensure_ascii=False)
    st, jc4, _ = check("collab 게시 (요청 연결)", "POST", "/api/collab", 200,
                       {"action": "post", "title": "검증 글", "body": "내용", "request_id": _rid, "kind": "feedback"})
    _pid = ((jc4 or {}).get("post") or {}).get("id")
    rows[-1]["ok"] = rows[-1]["ok"] and bool(_pid) and ((jc4 or {}).get("post") or {}).get("request_id") == _rid
    st, jc5, _ = check("collab 게시판 조회", "GET", "/api/collab/board?limit=10", 200)
    rows[-1]["ok"] = rows[-1]["ok"] and any(x.get("id") == _pid for x in (jc5 or {}).get("posts") or [])
    check("collab 댓글", "POST", "/api/collab", 200, {"action": "reply", "id": _pid, "text": "댓글입니다"})
    check("collab 해결 표시", "POST", "/api/collab", 200, {"action": "resolve", "id": _pid, "value": True})
    st, jc6, _ = check("collab 캐릭터 위치 저장 · 아이콘은 IP 로 (고를 수 없음)", "POST", "/api/collab", 200,
                       {"action": "touch", "x": 0.2, "y": 0.6, "emoji": "🐨"})
    rows[-1]["ok"] = (rows[-1]["ok"] and abs(float((jc6 or {}).get("x") or 0) - 0.2) < 1e-6
                      and (jc6 or {}).get("emoji") and (jc6 or {}).get("emoji") != "🐨")
    rows[-1]["out"] = "emoji=%s (보낸 값 무시)" % (jc6 or {}).get("emoji")
    check("guest 는 채팅 비우기 불가 → 401 (admin 등급)", "POST", "/api/collab", 401, {"action": "clear"})
    # 남의 글(=admin 이 쓴 글)을 게스트가 remove_mine 으로 지우려 하면 거절되어야 한다
    st, jca, _ = check("admin 이 글 작성", "POST", "/api/collab", 200, {"action": "post", "title": "admin 글", "body": "x"}, cookie=adm)
    _apid = ((jca or {}).get("post") or {}).get("id")
    check("남의 글은 remove_mine 으로 못 지움 → 403", "POST", "/api/collab", 403, {"action": "remove_mine", "id": _apid})
    check("내 글은 remove_mine 으로 지워진다", "POST", "/api/collab", 200, {"action": "remove_mine", "id": _pid})
    check("admin 글 삭제 (확인 필요 → 428)", "POST", "/api/collab", 428, {"action": "remove", "id": _apid}, cookie=adm)
    check("admin 글 삭제 (확인 후)", "POST", "/api/collab", 200, dict(C, action="remove", id=_apid), cookie=adm)
    st, jc7, _ = check("collab 말풍선 글자 크기 설정이 내려옴", "GET", "/api/collab", 200)
    rows[-1]["ok"] = rows[-1]["ok"] and "bubble_font_step_min" in ((jc7 or {}).get("config") or {})
    rows[-1]["out"] = json.dumps((jc7 or {}).get("config") or {}, ensure_ascii=False)[:90]
    check("guest config reload → 401", "POST", "/api/config", 401, {"action": "reload"})
    st, j4, _ = check("admin config reload (파일 다시 읽기)", "POST", "/api/config", 200, dict(C, action="reload"), cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and (j4 or {}).get("reloaded") is True and (j4 or {}).get("settings")
    rows[-1]["out"] = "path=%s" % os.path.basename(str((j4 or {}).get("path") or ""))
    check("guest catalog 수정 → 401", "POST", "/api/models/catalog", 401, {"action": "add", "model": {"id": "x", "provider": "ollama"}})
    check("admin catalog add", "POST", "/api/models/catalog", 200, dict(C, action="add", model={"id": "web-verify-model", "provider": "ollama", "label": "웹검증", "roles": ["answer"]}), cookie=adm)
    st, j, _ = check("catalog add 반영", "GET", "/api/models/catalog?role=answer", 200)
    rows[-1]["ok"] = rows[-1]["ok"] and any(m["id"] == "web-verify-model" for m in (j or {}).get("models", []))
    check("admin catalog remove", "POST", "/api/models/catalog", 200, dict(C, action="remove", id="web-verify-model"), cookie=adm)
    st, j, _ = check("models 역할 정책 포함", "GET", "/api/models", 200)
    rows[-1]["ok"] = rows[-1]["ok"] and "policy" in (j or {}) and "answer" in (j.get("policy") or {}) and "timeout_s" in j["policy"]["answer"]
    check("admin 역할별 정책 저장", "POST", "/api/models/set", 200, dict(C, settings={"llm_roles": {"answer": {"timeout_s": 120, "retries": 2}}}), cookie=adm)
    st, j, _ = check("역할 정책 반영 확인", "GET", "/api/models", 200, cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and (j.get("policy") or {}).get("answer", {}).get("timeout_s") == 120
    check("역할별 정책 되돌리기", "POST", "/api/models/set", 200, dict(C, settings={"llm_roles": {}}), cookie=adm)
    st, j, _ = check("schedule 조회", "GET", "/api/schedule", 200, cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and "tasks" in (j or {}) and "action_types" in (j or {})
    check("guest schedule 수정 → 401", "POST", "/api/schedule", 401, {"action": "add", "task": {"name": "x", "every": "1h", "action": {"type": "build"}}})
    check("admin schedule add", "POST", "/api/schedule", 200, dict(C, action="add", task={"name": "web-verify", "every": "1h",
                                                                                          "action": {"type": "maintenance", "action": "wal_checkpoint"}}), cookie=adm)
    st, j, _ = check("schedule 목록에 반영", "GET", "/api/schedule", 200, cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and any(t["name"] == "web-verify" for t in (j or {}).get("tasks", []))
    check("admin schedule 즉시 실행", "POST", "/api/schedule", 200, dict(C, action="run", name="web-verify"), cookie=adm)
    time.sleep(1.0)
    st, j, _ = check("schedule 이력", "GET", "/api/schedule?n=5", 200, cookie=adm)
    rows[-1]["ok"] = rows[-1]["ok"] and any(h.get("name") == "web-verify" for h in (j or {}).get("history", []))
    check("admin schedule disable", "POST", "/api/schedule", 200, dict(C, action="disable", name="web-verify"), cookie=adm)
    check("admin schedule remove", "POST", "/api/schedule", 200, dict(C, action="remove", name="web-verify"), cookie=adm)
    check("admin schedule 잘못된 작업 → 400", "POST", "/api/schedule", 400, dict(C, action="add", task={"name": "bad", "cron": "nope", "action": {"type": "build"}}), cookie=adm)
    # 동시 질의 20건 (대기열·슬롯) — 모두 200 이어야 한다. 버스트 동안에는 속도 제한을 넉넉히.
    check("버스트용 제한 완화", "POST", "/api/admin/server", 200, dict(C, action="set_limits", save=False, values={
        "concurrency.max_parallel_reads": 6, "concurrency.max_parallel_per_user": 40, "concurrency.max_parallel_per_ip": 40,
        "rate_limit.per_user_per_min": 0, "rate_limit.per_ip_per_min": 0, "rate_limit.query_per_user_per_min": 0}), cookie=adm)
    import threading as _th
    _codes = []

    def _one(i):
        s2, _j2, _h2 = req("POST", "/api/query", {"q": "ISSUE-200%d 원인" % (i % 3 + 1), "log": False})
        _codes.append(s2)
    _ths = [_th.Thread(target=_one, args=(i,)) for i in range(20)]
    [t.start() for t in _ths]
    [t.join(300) for t in _ths]
    rows.append({"name": "동시 질의 20건 (게스트)", "path": "POST /api/query ×20", "status": ",".join(str(c) for c in sorted(set(_codes))),
                 "expect": 200, "ok": all(c == 200 for c in _codes), "out": "%d/%d OK" % (sum(1 for c in _codes if c == 200), len(_codes))})
    # 긴 작업 취소: 전체 리빌드를 시작하자마자 중지 (티켓이 생기기 전에 들어온 취소 = 예약 취소 경로)
    st, j, _ = check("admin 전체 빌드 시작(취소용)", "POST", "/api/build", 200, dict(D, full=True, reset=True), cookie=adm, keep="cxjob")
    if st == 200 and j.get("job"):
        check("admin 작업 취소 (DELETE /api/jobs)", "DELETE", "/api/jobs/" + j["job"], 200, cookie=adm)
        _fin = wait_job(j["job"], adm)
        rows.append({"name": "취소된 작업 상태", "path": "GET /api/jobs/<id>", "status": _fin.get("status"), "expect": "cancelled|done",
                     "ok": _fin.get("status") in ("cancelled", "done"), "out": str(_fin.get("error"))[:80]})
        check("취소 후 전체 빌드 정상", "POST", "/api/build", 200, dict(D, full=True, reset=True), cookie=adm, keep="rbjob")
        _rb = wait_job(state["rbjob"]["job"], adm)
        print("  recovery build job:", _rb.get("status"), (_rb.get("error") or "")[:200])
        rows.append({"name": "취소 후 빌드 복구", "path": "GET /api/jobs/<id>", "status": _rb.get("status"), "expect": "done",
                     "ok": _rb.get("status") == "done", "out": (_rb.get("error") or "")[:80]})
    check("취소 후 질의 정상", "POST", "/api/query", 200, {"q": "ISSUE-2001 원인", "log": False})
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
