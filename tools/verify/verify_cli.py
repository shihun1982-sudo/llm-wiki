"""CLI 전수 검증: 격리된 임시 환경(설정/데이터/로그 전부 temp)에서 모든 하위 명령을 실제 subprocess 로 실행하고 종료 코드·출력 첫 줄을 기록한다."""
import json, os, re, shutil, subprocess, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # <프로젝트 루트>/tools/verify/ 기준
PY = sys.executable
tmp = tempfile.mkdtemp(prefix="lwverify_")
def cp(src, dst):
    if os.path.isdir(src): shutil.copytree(src, dst)
    else: shutil.copy2(src, dst)
# ---- 격리 파일 준비 ----
cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
cfg.update({"data_dir": os.path.join(tmp, "data"), "wiki_dir": os.path.join(tmp, "wiki"), "corpus_dirs": [os.path.join(ROOT, "setup", "sample_corpus_modem")],
            "llm_provider": "mock", "llm_roles": {}, "embed_provider": "hash", "embed_dim": 256})
cfg["toggles"]["health_check"] = True
json.dump(cfg, open(os.path.join(tmp, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
open(os.path.join(tmp, ".env"), "w").write("PYTHONIOENCODING=utf-8\n")
for f in ("tuning.json", "presets.json", "query_rules.json", "mcp_sources.json", "agents.json", "pins.json", "security.json"):
    cp(os.path.join(ROOT, f), os.path.join(tmp, f))
os.makedirs(os.path.join(tmp, "data"))
cp(os.path.join(ROOT, "data", "rules.json"), os.path.join(tmp, "rules.json"))
cp(os.path.join(ROOT, "schemas"), os.path.join(tmp, "schemas"))
cp(os.path.join(ROOT, "prompts"), os.path.join(tmp, "prompts"))
cp(os.path.join(ROOT, "eval", "questions.json"), os.path.join(tmp, "questions.json"))
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

rows = []
state = {}
def run(name, argv, expect=0, stdin=None, env_extra=None, grab=None, timeout=600):
    env = dict(ENV, **(env_extra or {}))
    t0 = time.time()
    try:
        p = subprocess.run([PY, "-m", "llmwiki"] + argv, cwd=ROOT, env=env, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        code, out = p.returncode, (p.stdout or "") + (("\n[stderr] " + p.stderr[-300:]) if p.returncode not in (expect if isinstance(expect, tuple) else (expect,)) and p.stderr else "")
    except subprocess.TimeoutExpired:
        code, out = -1, "TIMEOUT"
    ok = code in (expect if isinstance(expect, tuple) else (expect,))
    first = next((ln for ln in out.splitlines() if ln.strip() and not ln.startswith("  ⏳")), "")[:110]
    rows.append({"name": name, "argv": " ".join(argv), "code": code, "expect": expect, "ok": ok, "ms": round((time.time() - t0) * 1000), "out": first})
    if grab:
        try:
            state[grab[0]] = grab[1](out)
        except Exception as e:
            state[grab[0]] = None
    return out

J = lambda o: json.loads(o[o.index("{") if "{" in o else 0:]) if "{" in o or "[" in o else {}
# ---- 환경/설정 ----
run("health", ["health"])
run("health --quick", ["health", "--quick"])
run("config show", ["config", "show"])
run("config show --effective", ["config", "show", "--effective"])
run("config paths", ["config", "paths"])
run("config set", ["config", "set", "llm_retries=3"])
run("models show", ["models", "show"])
run("models test", ["models", "test"])
run("models test --live", ["models", "test", "--live"])
run("models set", ["models", "set", "rerank_provider=mock"])
run("tuning show", ["tuning", "show"])
run("tuning show --stage", ["tuning", "show", "--stage", "context"])
run("tuning set", ["tuning", "set", "doc_expand_max_chunks=4"])
run("tuning reset key", ["tuning", "reset", "doc_expand_max_chunks"])
run("tuning set bad", ["tuning", "set", "no_such_key=1"], expect=1)
run("preset list", ["preset", "list"])
run("preset show", ["preset", "show", "quality"])
run("preset diff", ["preset", "diff", "speed"])
run("preset apply (memory)", ["preset", "apply", "token"])
run("prompts list", ["prompts", "list"])
run("prompts show", ["prompts", "show", "answer_guide"])
run("prompts path", ["prompts", "path", "answer_guide"])
run("prompts reset", ["prompts", "reset", "rerank"])
run("corpus types", ["corpus", "types"])
run("corpus schema", ["corpus", "schema", "issue"])
run("corpus example", ["corpus", "example", "issue"])
run("corpus lint-file", ["corpus", "lint-file", os.path.join(ROOT, "setup", "sample_corpus_modem", "issues", "ISSUE-2001.md")], expect=(0, 1))
run("rules show", ["rules", "show"])
run("rules stats", ["rules", "stats"])
run("rules path", ["rules", "path"])
run("rules add", ["rules", "add", "synonym", "언더런", "underrun"])
run("rules test", ["rules", "test", "언더런 원인"])
run("rules remove", ["rules", "remove", "synonym", "언더런"])
run("time", ["time", "지난주 CL"])
# ---- 빌드 ----
run("build --full (no --yes → refused)", ["build", "--full"], expect=4)
run("build --full --yes", ["build", "--full", "--yes", "--no-snapshot", "--trace"])
run("build (incremental)", ["build"])
run("build status", ["build", "status"])
run("build verify", ["build", "verify"])
run("build verify --fix", ["build", "verify", "--fix"])
run("build fts (no --yes)", ["build", "fts"], expect=4)
run("build fts --yes", ["build", "fts", "--yes"])
run("build vector --yes", ["build", "vector", "--yes"])
run("build vector --full --yes", ["build", "vector", "--full", "--yes"])
run("build graph --yes", ["build", "graph", "--yes", "--trace"])
run("build --channels fts", ["build", "--channels", "fts", "--json"])
run("build --channels bad", ["build", "--channels", "nope"], expect=1)
run("corpus lint", ["corpus", "lint"])
run("corpus stats", ["corpus", "stats"])
run("embed report", ["embed", "report"])
run("embed status", ["embed", "status"])
run("embed runs", ["embed", "runs"])
run("embed clear-cache", ["embed", "clear-cache"])
# ---- 질의 ----
run("query --trace", ["query", "ISSUE-2001 의 원인과 수정 CL 은?", "--trace"])
out = run("query --json", ["query", "HW rev B1 에서 t_setup 은 몇 ns 인가?", "--json"], grab=("rid", lambda o: J(o)["result"]["request_id"]))
state["qid"] = (J(out)["result"] or {}).get("query_id")
run("query --preset speed", ["query", "ISSUE-2001 원인", "--preset", "speed", "--json"])
run("query --no-doc-expand", ["query", "ISSUE-2001 원인", "--no-doc-expand", "--no-log"])
run("query --k 3 --debug 2", ["query", "AGC 수렴 지연 원인", "--k", "3", "--debug", "2", "--trace"])
run("query insufficient", ["query", "블루투스 오디오 코덱 aptX 지연", "--no-log"])
run("search fts", ["search", "fts", "RX DMA underrun"])
run("search vector", ["search", "vector", "RX DMA underrun", "--k", "3"])
run("search graph", ["search", "graph", "ISSUE-2001", "--json"])
run("pin list", ["pin", "list"])
out = run("pin add", ["pin", "add", "--doc", "RULE-ISR-001", "--keywords", "리뷰", "--note", "t"], grab=("pin", lambda o: J(o)["id"]))
run("pin test", ["pin", "test", "코드 리뷰 규칙"])
run("pin remove", ["pin", "remove", state.get("pin") or "p1"])
# ---- 평가/품질 ----
run("eval", ["eval", "--k", "5"])
run("eval --matrix", ["eval", "--matrix", "--k", "5"])
run("trial run a", ["trial", "run", "--name", "a", "--k", "5"])
run("trial run b", ["trial", "run", "--name", "b", "--set", "doc_expand=false", "--k", "5"])
run("trial list", ["trial", "list"])
run("trial compare", ["trial", "compare", "a", "b", "--md"])
run("trial report", ["trial", "report", "a"])
run("trial show", ["trial", "show", "a", "--json"])
run("fusion show", ["fusion", "show"])
run("fusion compare", ["fusion", "compare", "--methods", "rrf,zscore", "--k", "5"])
run("forensic last", ["forensic", "last"])
run("forensic list", ["forensic", "list"])
run("forensic summary", ["forensic", "summary"])
run("forensic <id>", ["forensic", str(state.get("rid") or 1)])
run("forensic <id> --llm", ["forensic", str(state.get("rid") or 1), "--llm"])
run("forensic expect (cited)", ["forensic", "expect", str(state.get("rid") or 1), "--doc", "HWD-PHY-TIMING-B1", "--term", "8ns"])
out = run("forensic expect (absent, --propose)", ["forensic", "expect", "last", "--doc", "ISSUE-2003", "--term", "1.5dB", "--propose", "--json"],
          grab=("pids", lambda o: J(o).get("proposals") or []))
run("forensic expect (no args)", ["forensic", "expect", "last"], expect=1)
run("memory status", ["memory", "status"])
run("memory decay", ["memory", "decay"])
run("memory consolidate", ["memory", "consolidate"])
run("memory episodes", ["memory", "episodes"])
# ---- 진화 ----
run("evolve status", ["evolve", "status"])
run("evolve list", ["evolve", "list"])
run("evolve review", ["evolve", "review"])
if state.get("qid"):
    run("evolve feedback", ["evolve", "feedback", str(state["qid"]), "-1", "정정 메모"])
pids = state.get("pids") or []
if pids:
    run("evolve apply (pin proposal, no-eval)", ["evolve", "apply", str(pids[-1]), "--no-eval"])
    if len(pids) > 1:
        run("evolve reject", ["evolve", "reject", str(pids[0]), "not now"])
run("wiki", ["wiki", "--min-degree", "1"])
run("docs", ["docs"])
run("stats", ["stats"])
run("system", ["system"])
run("graph", ["graph", "--limit", "10"])
run("graph --provenance", ["graph", "--provenance", "explicit", "--limit", "5"])
run("entity", ["entity", "ISSUE-2001"])
run("entity (missing)", ["entity", "nope-entity-xyz"], expect=1)
# ---- 관측 ----
run("requests list", ["requests", "list", "--limit", "5"])
run("requests last", ["requests", "last"])
run("requests show", ["requests", "show", str(state.get("rid") or 1)])
run("logs tail", ["logs", "tail", "-n", "5"])
run("logs files", ["logs", "files"])
run("logs grep --text", ["logs", "grep", "--text", "query", "-n", "5"])
run("logs grep --request", ["logs", "grep", "--request", str(state.get("rid") or 1), "-n", "5"])
run("logs dir", ["logs", "dir"])
run("arch", ["arch"])
run("arch --flow query", ["arch", "--flow", "query", "--json"])
# ---- 유지보수/캐시 ----
for a in ("vacuum", "fts_optimize", "wal_checkpoint", "clear_cache", "warm_cache", "refresh_doc_refs"):
    run("maintenance " + a, ["maintenance", a])
run("maintenance purge_requests (no --yes)", ["maintenance", "purge_requests"], expect=4)
run("maintenance purge_requests --yes", ["maintenance", "purge_requests", "--yes"])
run("precompute status", ["precompute", "status"])
run("precompute run", ["precompute", "run", "--from-log", "3"])
run("precompute doc-vectors", ["precompute", "doc-vectors"])
run("precompute clear", ["precompute", "clear"])
run("mcp-source list", ["mcp-source", "list"])
run("mcp-source test mock", ["mcp-source", "test", "mock"])
run("mcp-source ingest mock --dry-run", ["mcp-source", "ingest", "mock", "--dry-run"])
run("mcp-source enrich", ["mcp-source", "enrich", "RX AGC"])
run("mcp-source fetch", ["mcp-source", "fetch", "mock", "list_issues"])
run("mcp-source fetch (bad args)", ["mcp-source", "fetch", "mock"], expect=1)
run("mcp-source tools mock", ["mcp-source", "tools", "mock"])
run("mcp-source tools (unknown)", ["mcp-source", "tools", "nope"], expect=1)
# ---- 다른 RAG 연동 (2026-09-15): mock 소스를 enabled 로 켜고 retrieve 채널 · 페더레이션 확인 ----
_sp = os.path.join(tmp, "mcp_sources.json")
_srcs = json.load(open(_sp, encoding="utf-8"))
_srcs["mock"]["enabled"] = True
json.dump(_srcs, open(_sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
out = run("mcp-source retrieve --source mock", ["mcp-source", "retrieve", "TX 전력 제어 PA gain", "--source", "mock", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and "ext:mock:ISSUE-9001" in out
rows[-1]["out"] = "ext hits=%d" % out.count("ext:mock:")
out = run("mcp-source federated", ["mcp-source", "federated", "--json"]) if False else run("mcp-source federated", ["mcp-source", "federated"])
rows[-1]["ok"] = rows[-1]["ok"] and '"mcp_federation"' in out
out = run("query --external-rag (ext:mock 채널 융합)", ["query", "TX 전력 제어 PA gain 테이블 인덱스 오류", "--external-rag", "--no-rerank-llm", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and "ext:mock:ISSUE-9001" in out and '"ext_inject"' in out
rows[-1]["out"] = "ext in hits=%s" % ("ext:mock:ISSUE-9001" in out)
out = run("query --no-external-rag", ["query", "TX 전력 제어 PA gain 테이블 인덱스 오류", "--no-external-rag", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and "ext:mock:" not in out
mcp_in2 = "\n".join(json.dumps(m) for m in [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                                           {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "mock__search", "arguments": {"q": "AGC", "limit": 2}}},
                                           {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "wiki_sources", "arguments": {}}}]) + "\n"
out = run("mcp stdio (federation: mock__search · wiki_sources)", ["mcp"], stdin=mcp_in2, env_extra={"LLMWIKI_TOGGLE_MCP_FEDERATION": "1"})
lines2 = [json.loads(x) for x in out.splitlines() if x.startswith("{")]
names2 = {t["name"] for t in (lines2[1]["result"]["tools"] if len(lines2) > 1 else [])}
rows[-1]["ok"] = rows[-1]["ok"] and "mock__search" in names2 and "wiki_sources" in names2 and len(lines2) == 4 and not lines2[2]["result"].get("isError")
rows[-1]["out"] = "tools=%d federated=%s" % (len(names2), sorted(n for n in names2 if "__" in n))
# ---- 스냅샷 ----
out = run("snapshot create", ["snapshot", "create", "--tag", "t1", "--json"], grab=("snap", lambda o: J(o)["name"]))
run("snapshot list", ["snapshot", "list"])
run("snapshot restore (no --yes)", ["snapshot", "restore", state.get("snap") or "x"], expect=4)
run("snapshot restore --yes", ["snapshot", "restore", state.get("snap") or "x", "--yes"])
run("snapshot restore missing", ["snapshot", "restore", "nope", "--yes"], expect=(1, 2, 3))
run("snapshot prune", ["snapshot", "prune", "--keep", "1"])
# ---- 보안 ----
run("users list", ["users", "list"])
run("users add", ["users", "add", "bob", "--role", "class1", "--password", "bobpass123"])
run("users add bad role", ["users", "add", "eve", "--role", "king", "--password", "evepass123"], expect=1)
run("users set-role", ["users", "set-role", "bob", "--role", "builder"])
run("users passwd", ["users", "passwd", "bob", "--password", "newpass1234"])
run("security show", ["security", "show"])
run("security init (exists)", ["security", "init"], expect=1)
run("security audit", ["security", "audit", "--n", "5"])
run("security perms", ["security", "perms"])
run("security perms set", ["security", "perms", "set", "run=viewer", "/api/eval=class2"])
run("security perms set bad", ["security", "perms", "set", "run=king"], expect=(0, 1))
run("security perms reset", ["security", "perms", "reset"])
run("apikey add", ["apikey", "add", "k1", "--role", "viewer"], grab=("tok", lambda o: re.search(r"lwk_[A-Za-z0-9_\-]+", o).group(0)))
run("apikey list", ["apikey", "list"])
run("--user login ok", ["--user", "bob", "--password", "newpass1234", "stats"])
run("--user wrong password", ["--user", "bob", "--password", "bad", "stats"], expect=5)
run("LLMWIKI_API_KEY gate", ["stats"], env_extra={"LLMWIKI_API_KEY": state.get("tok") or "lwk_x_y"})
run("LLMWIKI_API_KEY bad", ["stats"], env_extra={"LLMWIKI_API_KEY": "lwk_bad_key"}, expect=5)
# cli.default_role=viewer → 빌드 거부, --user builder 승격
sec = json.load(open(os.path.join(tmp, "security.json"), encoding="utf-8")); sec["cli"] = {"default_role": "viewer", "require_login": False}
json.dump(sec, open(os.path.join(tmp, "security.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
run("cli gate: viewer build → deny", ["build"], expect=5)
run("cli gate: viewer query ok", ["query", "ISSUE-2001", "--no-log"])
run("cli gate: --user builder build fts", ["--user", "bob", "build", "fts", "--yes"], env_extra={"LLMWIKI_PASSWORD": "newpass1234"})
sec["cli"] = {"default_role": "admin", "require_login": False}
json.dump(sec, open(os.path.join(tmp, "security.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
run("apikey remove", ["apikey", "remove", "k1"])
run("users remove", ["users", "remove", "bob", "--yes"])
# ---- watch / mcp stdio / config reset ----
run("watch --once", ["watch", "--once"])
mcp_in = "\n".join(json.dumps(m) for m in [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                          {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "wiki_status", "arguments": {}}}]) + "\n"
out = run("mcp (stdio roundtrip)", ["mcp"], stdin=mcp_in)
lines = [json.loads(x) for x in out.splitlines() if x.startswith("{")]
rows[-1]["out"] = "responses=%d tools=%d" % (len(lines), len(lines[1]["result"]["tools"]) if len(lines) > 1 else 0)
rows[-1]["ok"] = rows[-1]["ok"] and len(lines) == 3
run("mcp --connect (bad url → bridge error json)", ["mcp", "--connect", "http://127.0.0.1:1/mcp"], stdin=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n")
out = run("mcp --client-config", ["mcp", "--client-config", "--url", "http://wiki-host:8765", "--token", "lwk_x_y"])
cc = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and cc.get("http_json", {}).get("mcpServers", {}).get("llmwiki", {}).get("url") == "http://wiki-host:8765/mcp" \
    and "\\\\\\\\" not in json.dumps(cc.get("stdio_json")) and os.path.exists(cc["stdio_json"]["mcpServers"]["llmwiki"]["command"])
rows[-1]["out"] = "keys=%s" % sorted(k for k in cc if not k.startswith("_"))
run("config set web_port (server defaults)", ["config", "set", "web_port=8899", "mcp_port=8898"])
out = run("config show --effective (web/mcp keys)", ["config", "show", "--effective"])
rows[-1]["ok"] = rows[-1]["ok"] and "web_port" in out and "8899" in out and "mcp_transport" in out
# ---- 상세 분석 모드 (2026-09-15) ----
out = run("query --analyze --focus tokens", ["query", "CL-55302 는 어떤 이슈를 수정했나?", "--analyze", "--focus", "tokens"])
rows[-1]["ok"] = rows[-1]["ok"] and "분석 리포트" in out and "req_" in out
out = run("analyze last", ["analyze", "last"])
rows[-1]["ok"] = rows[-1]["ok"] and "req_" in out and "quality" in out
out = run("analyze last --print --focus speed", ["analyze", "last", "--print", "--focus", "speed"])
rows[-1]["ok"] = rows[-1]["ok"] and "## 6. 속도 렌즈" in out and "## 5. 품질 렌즈" not in out
rows[-1]["out"] = "md chars=%d" % len(out)
run("analyze (unknown id)", ["analyze", "999999"], expect=1)
out = run("analyze --json", ["analyze", "last", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and '"timeline"' in out and '"lenses"' in out
run("tuning set forensic_*", ["tuning", "set", "forensic_near_miss_mult=4", "forensic_term_targets=10"])
run("tuning reset forensic_near_miss_mult", ["tuning", "reset", "forensic_near_miss_mult"])
run("tuning doc", ["tuning", "doc"])
# ---- 다중 사용자·스케줄·모델 카탈로그 (2026-09-15) ----
out = run("models list (카탈로그)", ["models", "list"])
rows[-1]["ok"] = rows[-1]["ok"] and "모델 카탈로그" in out
out = run("models list --role rerank", ["models", "list", "--role", "rerank"])
out = run("models policy", ["models", "policy"])
rows[-1]["ok"] = rows[-1]["ok"] and "timeout" in out and "answer" in out
run("models catalog add", ["models", "catalog", "add", "verify-model", "--provider", "ollama", "--label", "검증용", "--roles", "answer"])
run("models catalog add (임베딩)", ["models", "catalog", "add", "verify-embed", "--provider", "ollama", "--embedding"])
run("models catalog remove (임베딩)", ["models", "catalog", "remove", "verify-embed"])
# 토글 이름과 겹치는 플래그가 설정을 덮어쓰지 않는지 (2026-09-15 회귀 방지): models set 뒤에도 embed 토글이 유지돼야 한다
out = run("models set 후 embed 토글 유지", ["config", "show", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and '"embed": true' in out.replace("True", "true")
out = run("models list (추가 확인)", ["models", "list", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and "verify-model" in out
run("models catalog remove", ["models", "catalog", "remove", "verify-model"])
run("models catalog remove (없음)", ["models", "catalog", "remove", "nope-model"], expect=1)
run("models discover", ["models", "discover"])
run("models set 역할 정책", ["models", "set", "answer_timeout_s=120", "answer_retries=2", "rerank_backoff=linear"])
out = run("models policy (역할 반영)", ["models", "policy"])
rows[-1]["ok"] = rows[-1]["ok"] and "120" in out
run("models set 되돌리기", ["models", "set", "answer_timeout_s=", "answer_retries=", "rerank_backoff="])

run("schedule list (비어 있음)", ["schedule", "list"])
run("schedule add", ["schedule", "add", "--task", json.dumps({"name": "verify-maint", "every": "1h", "action": {"type": "maintenance", "action": "wal_checkpoint"}})])
out = run("schedule list", ["schedule", "list"])
rows[-1]["ok"] = rows[-1]["ok"] and "verify-maint" in out
run("schedule show", ["schedule", "show", "verify-maint"])
run("schedule validate", ["schedule", "validate"])
out = run("schedule run (즉시 실행)", ["schedule", "run", "verify-maint"], timeout=300)
rows[-1]["ok"] = rows[-1]["ok"] and "done" in out
run("schedule history", ["schedule", "history", "-n", "5"])
run("schedule disable", ["schedule", "disable", "verify-maint"])
run("schedule enable", ["schedule", "enable", "verify-maint"])
run("schedule add (query 동작)", ["schedule", "add", "--task", json.dumps({"name": "verify-digest", "at": "03:00", "days": ["mon"],
                                                                          "action": {"type": "query", "q": "ISSUE-2001 원인", "out": os.path.join(tmp, "digest.md"), "log": False}})])
out = run("schedule run (query)", ["schedule", "run", "verify-digest"], timeout=300)
rows[-1]["ok"] = rows[-1]["ok"] and "done" in out and os.path.exists(os.path.join(tmp, "digest.md"))
run("schedule add (잘못된 cron)", ["schedule", "add", "--task", json.dumps({"name": "bad", "cron": "nope", "action": {"type": "build"}})], expect=1)
run("schedule add (없는 동작)", ["schedule", "add", "--task", json.dumps({"name": "bad2", "every": "1h", "action": {"type": "nope"}})], expect=1)
run("schedule run (없는 작업)", ["schedule", "run", "nope"], expect=1)
run("schedule remove", ["schedule", "remove", "verify-maint"])
run("schedule remove (없음)", ["schedule", "remove", "nope"], expect=1)
run("schedule remove digest", ["schedule", "remove", "verify-digest"])

run("server status (서버 없음)", ["server", "status"], expect=1)
run("server cancel (인자 없음)", ["server", "cancel"], expect=1)
run("server limits (서버 없음)", ["server", "limits"], expect=1)
run("--log-level DEBUG", ["--log-level", "DEBUG", "query", "ISSUE-2001 원인", "--no-log"])
out = run("logs grep DEBUG", ["logs", "grep", "--level", "DEBUG", "-n", "5"])
rows[-1]["ok"] = rows[-1]["ok"] and ("DEBUG" in out or "[]" in out)

run("config reset (no --yes)", ["config", "reset"], expect=4)
run("config reset --yes", ["config", "reset", "--yes"])
run("help", ["--help"])
run("query --help", ["query", "--help"])
run("server --help", ["server", "--help"])
run("schedule --help", ["schedule", "--help"])

# ---- 리포트 ----
bad = [r for r in rows if not r["ok"]]
print("CLI 검증: %d 명령 중 %d 통과, %d 실패" % (len(rows), len(rows) - len(bad), len(bad)))
for r in rows:
    print("%s %-42s code=%s%s %5dms  %s" % ("OK " if r["ok"] else "FAIL", r["name"], r["code"], "" if r["ok"] else " (expect %s)" % (r["expect"],), r["ms"], r["out"]))
json.dump(rows, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_cli_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
shutil.rmtree(tmp, ignore_errors=True)
