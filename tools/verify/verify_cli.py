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
           LLMWIKI_MODELS_PATH=os.path.join(tmp, "models.json"), LLMWIKI_DOCACL_PATH=os.path.join(tmp, "docacl.json"))
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
# 2026-09-19: 메모리 화면이 보여 주는 네 표는 CLI 에도 같은 이름으로 있어야 한다 (docs/EVOLVE.md §2.5)
# 2026-09-19 품질 루프: 신뢰도 점검 → 검색 전용(토큰 0) → 놓친 문항 원인 (docs/EVAL_TRIAL.md)
run("eval --check (평가셋 신뢰도)", ["eval", "--check", "--json"], expect=(0, 1))
run("eval --retrieval-only (토큰 0)", ["eval", "--retrieval-only", "--json"])
run("eval --retrieval-only --forensic", ["eval", "--retrieval-only", "--forensic", "--forensic-max", "2", "--json"])
run("memory boosts (지금 받는 피드백 부스트)", ["memory", "boosts", "--limit", "10"])
run("memory decaying (사라지기 직전 제안)", ["memory", "decaying", "--limit", "10"])
run("memory episodes --only negative", ["memory", "episodes", "--only", "negative", "--limit", "5"])
run("memory episodes --q 검색", ["memory", "episodes", "--q", "ISSUE", "--limit", "5"])
run("memory boosts --json", ["memory", "boosts", "--json", "--limit", "5"])
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
# ---- 문서 접근 제어 (docacl.json) — 규칙이 없으면 아무도 막지 않고, 넣으면 그 역할에서만 가려진다 ----
# ---- 요청 단위 설정 오버라이드 (`--set`) — Web/MCP 의 overrides 와 같은 화이트리스트 ----
run("query --set (전역 키)", ["query", "ISSUE-2001 원인", "--no-log", "--json", "--set", "llm_timeout=9,llm_retries=1"])
run("query --set (역할 단축키)", ["query", "ISSUE-2001 원인", "--no-log", "--json", "--set", "answer_timeout_s=30"])
run("query --set (여러 번)", ["query", "ISSUE-2001 원인", "--no-log", "--json", "--set", "llm_retries=1", "--set", "llm_timeout=9"])
run("query --set 형식 오류 → 2", ["query", "x", "--no-log", "--set", "llm_timeout"], expect=2)
run("query --set 모르는 키 → 2 (조용히 버리지 않는다)", ["query", "x", "--no-log", "--set", "nope_zzz=1"], expect=2)
run("security docacl show (파일 없음)", ["security", "docacl", "show"])
run("security docacl init", ["security", "docacl", "init"])
run("security docacl init (exists)", ["security", "docacl", "init"], expect=1)
run("security docacl check (규칙 0 → 0건 가려짐)", ["security", "docacl", "check", "--role", "viewer", "--json"])
_acl_p = os.path.join(tmp, "docacl.json")
json.dump({"enabled": True, "default_min_role": "viewer",
           "rules": [{"prefix": "sample_corpus_modem/issues/", "min_role": "class1", "note": "verify"}]},
          open(_acl_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
run("security docacl show (규칙 1개)", ["security", "docacl", "show"])
_acl_out = run("security docacl check viewer (가려짐 > 0)", ["security", "docacl", "check", "--role", "viewer", "--json"])
run("security docacl check admin (0건)", ["security", "docacl", "check", "--role", "admin", "--json"])
run("security docacl 잘못된 하위 명령", ["security", "docacl", "nope"], expect=1)
json.dump({"enabled": True, "default_min_role": "viewer", "rules": []}, open(_acl_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
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

# ---- 2026-09-18 (docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md): 버전 · 출력 모드 · 답변 모드 · 채널 top-k 튜닝 · 스윕 · 그래프 진단 · 규칙 설명 ·
#      기본값 채우기 · .env 가시성 · 카탈로그 전체 테스트 · 로그 총량 · 앙상블 ----
out = run("--version", ["--version"])
rows[-1]["ok"] = rows[-1]["ok"] and re.match(r"llmwiki \d+\.\d+\.\d+", out.strip()) is not None
rows[-1]["out"] = out.strip()[:40]
out = run("query --output fused --json", ["query", "ISSUE-2001 의 원인과 수정 CL", "--output", "fused", "--json", "--no-log"])
_r = (J(out).get("result") or {})
rows[-1]["ok"] = rows[-1]["ok"] and _r.get("result_type") == "candidates_fused" and bool(_r.get("candidates")) and "lists" in _r and "stages" in _r \
    and all(k in _r["candidates"][0] for k in ("chunk_id", "scores", "ranks", "fused", "boosts", "why")) and _r["candidates"][0].get("rerank") is None
rows[-1]["out"] = "result_type=%s candidates=%d lists=%s" % (_r.get("result_type"), len(_r.get("candidates") or []), sorted(_r.get("lists") or {})[:4])
out = run("query --output reranked", ["query", "ISSUE-2001 의 원인과 수정 CL", "--output", "reranked", "--no-log"])
rows[-1]["ok"] = rows[-1]["ok"] and "result_type: candidates_reranked" in out and "| # | chunk_id |" in out
out = run("query --output reranked --json", ["query", "ISSUE-2001 의 원인과 수정 CL", "--output", "reranked", "--json", "--no-log"])
_r = (J(out).get("result") or {})
rows[-1]["ok"] = rows[-1]["ok"] and _r.get("result_type") == "candidates_reranked" and bool(_r.get("candidates")) and "rerank_before" in (_r.get("stages") or {}) \
    and all(c.get("rerank") is not None for c in _r["candidates"])
out = run("query --output context --json", ["query", "ISSUE-2001 의 원인과 수정 CL", "--output", "context", "--json", "--no-log"])
_r = (J(out).get("result") or {})
rows[-1]["ok"] = rows[-1]["ok"] and _r.get("result_type") == "context" and bool((_r.get("context") or {}).get("text")) and "[C1]" in (_r.get("context") or {}).get("text", "") \
    and isinstance(_r.get("refs"), list) and "candidates" not in _r
rows[-1]["out"] = "context %d자 refs=%d" % (len((_r.get("context") or {}).get("text") or ""), len(_r.get("refs") or []))
out = run("query --output context (text)", ["query", "ISSUE-2001 의 원인", "--output", "context", "--no-log"])
rows[-1]["ok"] = rows[-1]["ok"] and "[C1]" in out
def _find_stage(tr, name):
    for c in (tr or {}).get("children") or []:
        if c.get("name") == name:
            return c
        got = _find_stage(c, name)
        if got:
            return got
    return None
# answer_mode=best_effort (1차 §F · docs/ANSWER_MODES.md): 근거가 있으면 답변 LLM 이 best_effort 프롬프트로 돌아 result_type=best_effort,
# 근거가 부족(insufficient)해도 LLM 을 불러 [BK] 표시로 답한다 (grounded 만 insufficient_data 응답). 두 행이 FAIL 이면 answer_mode 가 엔진에 전달되지 않는 것.
out = run("query --answer-mode best_effort --json (근거 있음 → result_type=best_effort)", ["query", "ISSUE-2001 의 원인과 수정 CL", "--answer-mode", "best_effort", "--json", "--no-log"])
_j = J(out); _r = (_j.get("result") or {})
_al = _find_stage(_j.get("trace") or {}, "answer_llm") or {}
rows[-1]["ok"] = rows[-1]["ok"] and _r.get("result_type") == "best_effort" and (_al.get("meta") or {}).get("answer_mode") == "best_effort" and bool(_r.get("answer"))
rows[-1]["out"] = "result_type=%s answer_llm.meta.answer_mode=%s" % (_r.get("result_type"), (_al.get("meta") or {}).get("answer_mode"))
out = run("query --answer-mode best_effort --json (근거 부족 → LLM 호출·[BK])", ["query", "블루투스 오디오 코덱 aptX 지연", "--answer-mode", "best_effort", "--json", "--no-log"])
_j = J(out); _r = (_j.get("result") or {})
_al = _find_stage(_j.get("trace") or {}, "answer_llm") or {}
rows[-1]["ok"] = rows[-1]["ok"] and _r.get("result_type") == "best_effort" and _al.get("enabled", True) and bool(_r.get("answer"))
rows[-1]["out"] = "result_type=%s verdict=%s answer_llm.enabled=%s" % (_r.get("result_type"), (_r.get("evidence") or {}).get("verdict"), _al.get("enabled", True))
# LLM 없는 경로(--no-llm-answer)에서도 best_effort 가 죽지 않고 추출식으로 내려와야 한다
out = run("query --answer-mode best_effort --no-llm-answer", ["query", "ISSUE-2001 원인", "--answer-mode", "best_effort", "--no-llm-answer", "--json", "--no-log"])
_r = (J(out).get("result") or {})
rows[-1]["ok"] = rows[-1]["ok"] and bool(_r.get("answer")) and "Traceback" not in out
rows[-1]["out"] = "answer_mode=%s result_type=%s" % (_r.get("answer_mode"), _r.get("result_type"))
out = run("query --answer-mode grounded", ["query", "ISSUE-2001 원인", "--answer-mode", "grounded", "--no-log"])
run("query --answer-mode bad → argparse 2", ["query", "x", "--answer-mode", "nope"], expect=2)
run("query --output bad → argparse 2", ["query", "x", "--output", "nope"], expect=2)
# 채널 top-k 구간 가중 (§2.2): 요청 단위 --tuning 은 파일을 바꾸지 않고 rrf_fuse meta 에 남는다
out = run("query --tuning fts_topk_n=1,fts_topk_w=1.5 (요청 단위)", ["query", "ISSUE-2001 의 원인과 수정 CL", "--tuning", "fts_topk_n=1,fts_topk_w=1.5", "--output", "fused", "--json", "--no-log"])
_j = J(out)
_rf = _find_stage(_j.get("trace") or {}, "rrf_fuse") or {}
rows[-1]["ok"] = rows[-1]["ok"] and (((_rf.get("meta") or {}).get("topk") or {}).get("fts") or {}).get("n") == 1
rows[-1]["out"] = "rrf_fuse.meta.topk=%s" % json.dumps(((_rf.get("meta") or {}).get("topk") or {}), ensure_ascii=False)[:70]
_tv = json.load(open(os.path.join(tmp, "tuning.json"), encoding="utf-8"))
rows[-1]["ok"] = rows[-1]["ok"] and _tv.get("fts_topk_n") in (None, 0)          # 파일은 그대로
run("query --tuning (모르는 키) → 2", ["query", "x", "--tuning", "nope_key=1", "--no-log"], expect=2)
run("tuning set fts_topk_n=5 fts_topk_w=1.5", ["tuning", "set", "fts_topk_n=5", "fts_topk_w=1.5"])
out = run("tuning show --stage rrf_fuse", ["tuning", "show", "--stage", "rrf_fuse"])
rows[-1]["ok"] = rows[-1]["ok"] and "[rrf_fuse]" in out and re.search(r"fts_topk_n\s+=\s+5\b", out) is not None and re.search(r"fts_topk_w\s+=\s+1\.5\b", out) is not None \
    and "[context]" not in out
out = run("tuning show --stage rrf_fuse --json", ["tuning", "show", "--stage", "rrf_fuse", "--json"])
_rows = json.loads(out) if out.strip().startswith("[") else []
rows[-1]["ok"] = rows[-1]["ok"] and any(r["key"] == "fts_topk_n" and r["value"] == 5 and r["overridden"] for r in _rows) \
    and any(r["key"] == "channel_inject" for r in _rows) and all(r["stage"] == "rrf_fuse" for r in _rows)
rows[-1]["out"] = "rrf_fuse 키 %d개" % len(_rows)
_tv = json.load(open(os.path.join(tmp, "tuning.json"), encoding="utf-8"))
rows.append({"name": "tuning.json 에 fts_topk_n=5 저장됨", "argv": "(파일 확인)", "code": 0, "expect": 0, "ok": _tv.get("fts_topk_n") == 5 and _tv.get("fts_topk_w") == 1.5, "ms": 0,
             "out": "fts_topk_n=%s fts_topk_w=%s" % (_tv.get("fts_topk_n"), _tv.get("fts_topk_w"))})
run("tuning set channel_inject=fts:2,vector:1", ["tuning", "set", "channel_inject=fts:2,vector:1"])
out = run("query (channel_inject 적용)", ["query", "ISSUE-2001 의 원인과 수정 CL", "--output", "reranked", "--json", "--no-log"])
_j = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and _find_stage(_j.get("trace") or {}, "channel_inject") is not None
run("tuning reset fts_topk_n", ["tuning", "reset", "fts_topk_n"])
run("tuning reset fts_topk_w", ["tuning", "reset", "fts_topk_w"])
run("tuning reset channel_inject", ["tuning", "reset", "channel_inject"])
# 스윕 (§2.6 · docs/SWEEP.md): 기준 질의 → 값마다 재실행 재생 → 기록 · 목록 · 상세 · 비교
out = run("query (스윕 기준, rerun_capture)", ["query", "ISSUE-2001 의 원인과 수정 CL 은?", "--json"])
_r = (J(out).get("result") or {})
rows[-1]["ok"] = rows[-1]["ok"] and bool((_r.get("rerun") or {}).get("saved"))
rows[-1]["out"] = "request_id=%s rerun.saved=%s" % (_r.get("request_id"), (_r.get("rerun") or {}).get("saved"))
out = run("sweep keys", ["sweep", "keys"])
rows[-1]["ok"] = rows[-1]["ok"] and "rrf_k" in out and "rrf_fuse" in out and "answer_model" in out
out = run("sweep keys --json", ["sweep", "keys", "--json"])
_k = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and any(x["key"] == "rrf_k" for x in _k.get("keys") or []) and bool(_k.get("points"))
rows[-1]["out"] = "keys=%d points=%d" % (len(_k.get("keys") or []), len(_k.get("points") or []))
out = run("sweep run last --key rrf_k --range 10:40:10", ["sweep", "run", "last", "--key", "rrf_k", "--range", "10:40:10"],
          grab=("sweep_id", lambda o: re.search(r"스윕 sw_(\S+)", o).group(1)), timeout=900)
rows[-1]["ok"] = rows[-1]["ok"] and "rrf_k = 10, 20, 30, 40" in out and "(기준)" in out and "재시작점 rrf_fuse" in out and bool(state.get("sweep_id"))
rows[-1]["out"] = "sweep_id=%s" % state.get("sweep_id")
out = run("sweep run last --key claim_check (토글, 값 생략)", ["sweep", "run", "last", "--key", "claim_check", "--json"], timeout=900)
_j = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and (_j.get("record") or {}).get("values") == [False, True] and (_j.get("record") or {}).get("n_ok") == 2 \
    and (_j.get("compare") or {}).get("baseline", {}).get("value") is False
out = run("sweep run last --key answer_mode --values grounded,best_effort", ["sweep", "run", "last", "--key", "answer_mode", "--values", "grounded,best_effort", "--json"], timeout=900)
_j = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and (_j.get("record") or {}).get("point") == "answer_llm" and (_j.get("record") or {}).get("n_ok") == 2
out = run("sweep list", ["sweep", "list"])
rows[-1]["ok"] = rows[-1]["ok"] and (state.get("sweep_id") or "∅") in out and "rrf_k" in out
out = run("sweep show <id>", ["sweep", "show", state.get("sweep_id") or "x"])
rows[-1]["ok"] = rows[-1]["ok"] and ("sw_" + (state.get("sweep_id") or "∅")) in out and "(기준)" in out and "rrf_k = 10, 20, 30, 40" in out
out = run("sweep show <id> --json", ["sweep", "show", "sw_" + (state.get("sweep_id") or "x"), "--json"])
_j = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and (_j.get("record") or {}).get("id") == state.get("sweep_id") and (_j.get("compare") or {}).get("baseline", {}).get("value") == 10 \
    and len((_j.get("compare") or {}).get("runs") or []) == 4 and all("stages" in r for r in _j["compare"]["runs"])
rows[-1]["out"] = "runs=%d changed=%s" % (len(_j.get("compare", {}).get("runs") or []), [r.get("n_changed_stages") for r in _j.get("compare", {}).get("runs") or []])
out = run("sweep compare <id>", ["sweep", "compare", state.get("sweep_id") or "x"])
rows[-1]["ok"] = rows[-1]["ok"] and "sw_" + (state.get("sweep_id") or "∅") in out
out = run("sweep compare <id> --json", ["sweep", "compare", state.get("sweep_id") or "x", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and (J(out).get("baseline") or {}).get("value") == 10
run("sweep show (없는 id) → 1", ["sweep", "show", "nope-sweep"], expect=1)
run("sweep run (--key 없음) → 2", ["sweep", "run", "last"], expect=2)
run("sweep run (값 없음) → 2", ["sweep", "run", "last", "--key", "rrf_k"], expect=2)
run("sweep run (스윕 불가 토글) → 2", ["sweep", "run", "last", "--key", "rerun_capture"], expect=2)
run("sweep run (없는 요청) → 2", ["sweep", "run", "999999", "--key", "rrf_k", "--values", "10,60"], expect=2)
_sd = os.path.join(tmp, "data", "sweeps")
rows.append({"name": "sweep_dir 에 sw_*.json 저장", "argv": "(파일 확인)", "code": 0, "expect": 0, "ok": os.path.isdir(_sd) and len([n for n in os.listdir(_sd) if n.startswith("sw_")]) >= 3, "ms": 0,
             "out": "files=%d" % (len([n for n in os.listdir(_sd) if n.startswith("sw_")]) if os.path.isdir(_sd) else 0)})
# 그래프 진단 (§2.5)
out = run("graph profile", ["graph", "profile"])
rows[-1]["ok"] = rows[-1]["ok"] and "Traceback" not in out and len(out) > 200
out = run("graph profile --json", ["graph", "profile", "--json"])
_gp = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and all(k in _gp for k in ("size", "connectivity", "coverage", "quality", "rules", "usage", "suggestions", "saved")) and os.path.isfile(str(_gp.get("saved") or ""))
rows[-1]["out"] = "entities=%s suggestions=%d saved=%s" % ((_gp.get("size") or {}).get("entities"), len(_gp.get("suggestions") or []), os.path.basename(str(_gp.get("saved") or "")))
out = run("graph profile --compare --json", ["graph", "profile", "--compare", "--json"])
_gp = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and isinstance(_gp.get("compare"), dict) and "before" in _gp["compare"]     # 앞의 두 실행이 이력에 있으므로 비교가 있어야 한다
rows[-1]["out"] = "compare keys=%s" % sorted(_gp.get("compare") or {})[:5]
out = run("graph profile --compare (text)", ["graph", "profile", "--compare"])
rows[-1]["ok"] = rows[-1]["ok"] and "비교할 이전 프로파일이 없습니다" not in out
out = run("graph profile --out", ["graph", "profile", "--out", os.path.join(tmp, "gp.md")])
rows[-1]["ok"] = rows[-1]["ok"] and os.path.isfile(os.path.join(tmp, "gp.md")) and os.path.getsize(os.path.join(tmp, "gp.md")) > 200
_gpd = os.path.join(tmp, "data", "graph_profiles")
rows.append({"name": "graph_profiles 이력 파일 (graph_profile_keep)", "argv": "(파일 확인)", "code": 0, "expect": 0,
             "ok": os.path.isdir(_gpd) and len([n for n in os.listdir(_gpd) if n.startswith("gp_")]) >= 4, "ms": 0,
             "out": "files=%d" % (len([n for n in os.listdir(_gpd) if n.startswith("gp_")]) if os.path.isdir(_gpd) else 0)})
# 규칙 방향 설명 (§2.4) — PDCCH 는 하네스 fixture(query_rules.json) 의 acronym 키
out = run("rules explain PDCCH", ["rules", "explain", "PDCCH"])
rows[-1]["ok"] = rows[-1]["ok"] and "PDCCH" in out and "acronym" in out and "양방향" in out
out = run("rules explain PDCCH --json", ["rules", "explain", "PDCCH", "--json"])
_ex = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and _ex.get("term") == "PDCCH" and any(e.get("type") == "acronym" for e in _ex.get("entries") or []) and "related_symmetric" in _ex
rows[-1]["out"] = "entries=%d expanded_from=%d" % (len(_ex.get("entries") or []), len(_ex.get("expanded_from") or []))
out = run("rules explain (사전에 없는 말)", ["rules", "explain", "zzz-없는-용어"])
rows[-1]["ok"] = rows[-1]["ok"] and "규칙 없음" in out
run("rules explain (인자 없음) → 1", ["rules", "explain"], expect=1)
out = run("rules test PDCCH", ["rules", "test", "PDCCH 디코딩 실패 원인"])
rows[-1]["ok"] = rows[-1]["ok"] and "PDCCH" in out and ("acronym" in out or "expanded" in out or "fts" in out)
run("rules lint", ["rules", "lint"])

# ---- 그래프 빌드 규칙 (data/rules.json) — 질의 확장 규칙과 짝 (2026-09-19, docs/GRAPH_RULES.md) ----
out = run("graph-rules types", ["graph-rules", "types"])
rows[-1]["ok"] = rows[-1]["ok"] and "값 종류" in out and "measure" in out and "관계 어휘" in out
out = run("graph-rules lint", ["graph-rules", "lint"])
rows[-1]["ok"] = rows[-1]["ok"] and "오류 0" in out
out = run("graph-rules test", ["graph-rules", "test", "rev B1 에서 t_setup 은 4 ns 이다"])
rows[-1]["ok"] = rows[-1]["ok"] and "metric" in out and "version" in out
out = run("graph-rules test (doc-type)", ["graph-rules", "test", "ISSUE-2001 을 참고", "--doc-type", "cl", "--ext-id", "CL-1"])
rows[-1]["ok"] = rows[-1]["ok"] and "ISSUE-2001" in out
run("graph-rules test (인자 없음) → 1", ["graph-rules", "test"], expect=1)
out = run("graph-rules add-entity (없는 type) → 1", ["graph-rules", "add-entity", "테스트엔티티", "없는타입"], expect=1)
rows[-1]["ok"] = rows[-1]["ok"] and "없는 type" in out
out = run("graph-rules add-entity", ["graph-rules", "add-entity", "테스트엔티티", "module", "별칭1"])
rows[-1]["ok"] = rows[-1]["ok"] and "엔티티 추가" in out
out = run("graph-rules add-alias", ["graph-rules", "add-alias", "테스트엔티티", "별칭2"])
rows[-1]["ok"] = rows[-1]["ok"] and "별칭" in out
run("graph-rules fill-defaults", ["graph-rules", "fill-defaults"])

# ---- 관리자 초기화 — 미리보기는 아무것도 지우지 않아야 한다 (docs/RESET.md) ----
out = run("reset (범위 목록)", ["reset"])
rows[-1]["ok"] = rows[-1]["ok"] and "data" in out and "settings" in out and "logs" in out
_docs_before = run("stats (초기화 전)", ["stats", "--json"])
for _scope in ("data", "settings", "logs"):
    out = run("reset %s (미리보기)" % _scope, ["reset", _scope])
    rows[-1]["ok"] = rows[-1]["ok"] and "유지합니다" in out and "미리보기만" in out
out = run("stats (미리보기 뒤 — 변화 없어야)", ["stats", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and json.loads(out).get("docs") == json.loads(_docs_before).get("docs")
out = run("reset logs --apply --yes", ["reset", "logs", "--apply", "--yes"])
rows[-1]["ok"] = rows[-1]["ok"] and "초기화 완료" in out
out = run("stats (로그 초기화 뒤 색인 유지)", ["stats", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and json.loads(out).get("docs") == json.loads(_docs_before).get("docs")

# ---- 설정 묶기 (포팅) — docs/PORTING.md ----
_bundle_dir = os.path.join(tmp, "conf-bundle")
out = run("config bundle --out --dry-run", ["config", "bundle", "--out", _bundle_dir, "--dry-run"])
rows[-1]["ok"] = rows[-1]["ok"] and not os.path.exists(_bundle_dir) and "LLMWIKI_CONF_DIR" in out
out = run("config bundle --out", ["config", "bundle", "--out", _bundle_dir])
rows[-1]["ok"] = rows[-1]["ok"] and os.path.exists(os.path.join(_bundle_dir, "config.json")) \
    and os.path.exists(os.path.join(_bundle_dir, "rules.json"))
if os.path.exists(os.path.join(_bundle_dir, ".env")):
    _env_body = open(os.path.join(_bundle_dir, ".env"), encoding="utf-8").read()
    rows.append({"name": "config bundle: .env 값이 빠져 있다", "ok": all(
        l.startswith("#") or not l.strip() or l.rstrip().endswith("=") for l in _env_body.splitlines()),
        "code": 0, "ms": 0, "out": ".env 키만 복사"})
run("config bundle (인자 없음) → 1", ["config", "bundle"], expect=1)
out = run("config paths (묶음 안내)", ["config", "paths"])
rows[-1]["ok"] = rows[-1]["ok"] and "conf" in out
# 기본값 채우기 (1차 §2.10) — dry-run 은 파일을 쓰지 않는다
_before = open(os.path.join(tmp, "config.json"), encoding="utf-8").read()
out = run("config fill-defaults --dry-run", ["config", "fill-defaults", "--dry-run"])
rows[-1]["ok"] = rows[-1]["ok"] and open(os.path.join(tmp, "config.json"), encoding="utf-8").read() == _before and "Traceback" not in out
out = run("config fill-defaults --all --dry-run", ["config", "fill-defaults", "--all", "--dry-run"])
rows[-1]["ok"] = rows[-1]["ok"] and open(os.path.join(tmp, "config.json"), encoding="utf-8").read() == _before and "tuning" in out and "rules" in out
out = run("config fill-defaults --all --dry-run --json", ["config", "fill-defaults", "--all", "--dry-run", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and ("{" in out or "[" in out) and "Traceback" not in out
out = run("config fill-defaults --all (실제 쓰기)", ["config", "fill-defaults", "--all"])
_cfg2 = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
rows[-1]["ok"] = rows[-1]["ok"] and "output_mode" in _cfg2 and "sweep_max_values" in _cfg2 and "graph_profile_keep" in _cfg2 and isinstance((_cfg2.get("llm_roles") or {}).get("answer"), dict) \
    and _cfg2.get("llm_provider") == "mock"                     # 있는 값은 유지
rows[-1]["out"] = "config keys=%d llm_roles=%s" % (len(_cfg2), sorted(_cfg2.get("llm_roles") or {})[:3])
_tv = json.load(open(os.path.join(tmp, "tuning.json"), encoding="utf-8"))
rows.append({"name": "fill-defaults 뒤 tuning.json 에 모든 키 명시", "argv": "(파일 확인)", "code": 0, "expect": 0,
             "ok": "fts_topk_n" in _tv and "output_list_n" in _tv and "related_symmetric" in _tv, "ms": 0, "out": "tuning keys=%d" % len(_tv)})
out = run("health (fill-defaults 뒤에도 정상)", ["health", "--quick"])
out = run("query (fill-defaults 뒤에도 정상)", ["query", "ISSUE-2001 원인", "--no-log", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and bool((J(out).get("result") or {}).get("answer"))
# .env 가시성 — 값은 반드시 마스킹. 비밀값을 하나 심어 원문이 출력에 없는지 본다 (RERANK_API_KEY 는 rerank_url 이 없으면 쓰이지 않는다)
_secret = "rk-verifysecretvalue1234567890"
open(os.path.join(tmp, ".env"), "a", encoding="utf-8").write("RERANK_API_KEY=%s\n" % _secret)
out = run("config env", ["config", "env"])
rows[-1]["ok"] = rows[-1]["ok"] and ".env:" in out and "RERANK_API_KEY" in out and _secret not in out and "rk-…7890" in out and "LLMWIKI_CONFIG" in out
rows[-1]["out"] = "masked=%s overrides 표시=%s" % (_secret not in out, "LLMWIKI_CONFIG" in out)
out = run("config env --json", ["config", "env", "--json"])
_er = J(out)
_k = next((k for k in _er.get("keys") or [] if k["name"] == "RERANK_API_KEY"), {})
rows[-1]["ok"] = rows[-1]["ok"] and _k.get("set") is True and _k.get("masked") == "rk-…7890" and _k.get("secret") is True and _secret not in out \
    and any(o["env"] == "LLMWIKI_CONFIG" for o in _er.get("overrides") or []) and _er.get("exists") is True
rows[-1]["out"] = "keys=%d overrides=%d" % (len(_er.get("keys") or []), len(_er.get("overrides") or []))
out = run("config reload", ["config", "reload"])
rows[-1]["ok"] = rows[-1]["ok"] and "config reloaded" in out and "answer=mock" in out
out = run("config reload --env", ["config", "reload", "--env"])
rows[-1]["ok"] = rows[-1]["ok"] and "config reloaded" in out and ".env=" in out
out = run("config reload --json", ["config", "reload", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and J(out).get("ok") is True
# 카탈로그 전체 연결 테스트 — 격리 환경에는 실제 프로바이더가 없으므로 실패 항목이 있어 exit 1 이 정상. 표가 나오고 Traceback 이 없으면 통과.
out = run("models test --catalog", ["models", "test", "--catalog"], expect=(0, 1), timeout=900)
rows[-1]["ok"] = rows[-1]["ok"] and "카탈로그 연결 테스트" in out and "Traceback" not in out and re.search(r"\d+ 항목 중 \d+ OK", out) is not None
rows[-1]["out"] = (re.search(r"카탈로그 연결 테스트.*", out) or re.match(r".*", out)).group(0)[:100]
out = run("models test --catalog --json", ["models", "test", "--catalog", "--json"], expect=(0, 1), timeout=900)
_ct = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and isinstance(_ct.get("rows"), list) and _ct.get("n") == len(_ct["rows"]) and all(k in _ct["rows"][0] for k in ("id", "provider", "kind", "ok")) \
    and any(r["kind"] == "embed" for r in _ct["rows"])
rows[-1]["out"] = "n=%s ok_n=%s kinds=%s" % (_ct.get("n"), _ct.get("ok_n"), sorted({r["kind"] for r in _ct.get("rows") or []}))
# 연결되는 모델 → 역할 자동 배정 (2026-09-19). 격리 환경이라 mock 만 붙으므로 제안이 나오는지와 구조만 본다.
out = run("models automap", ["models", "automap"], expect=(0, 1), timeout=900)
rows[-1]["ok"] = rows[-1]["ok"] and "자동 매핑" in out and "Traceback" not in out
out = run("models automap --json", ["models", "automap", "--json"], expect=(0, 1), timeout=900)
_am = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and isinstance(_am.get("proposal"), dict) and "answer" in (_am.get("proposal") or {}) \
    and all(k in (_am["proposal"]["answer"] or {}) for k in ("kind", "current", "why", "ok")) and _am.get("applied") == []
rows[-1]["out"] = "후보 %s · 제안 %d개" % (_am.get("candidates"), len(_am.get("proposal") or {}))
# 채널 조합 검색 (2026-09-19) — 여러 채널 + or/and/rrf. 각 행에 채널별 순위가 붙는다.
out = run("search 다중채널 or", ["search", "fts,vector", "RX DMA underrun", "--mode", "or", "--k", "3", "--json"])
_sc = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and _sc.get("channels") == ["fts", "vector"] and _sc.get("mode") == "or" \
    and isinstance(_sc.get("rows"), list) and "per_channel" in _sc and "counts" in _sc
rows[-1]["out"] = "합집합 %s · 교집합 %s" % ((_sc.get("counts") or {}).get("union"), (_sc.get("counts") or {}).get("intersection"))
out = run("search 다중채널 and", ["search", "all", "RX DMA underrun", "--mode", "and", "--k", "3", "--json"])
_sa = J(out)
_need = len(_sa.get("channels") or [])
rows[-1]["ok"] = rows[-1]["ok"] and _sa.get("mode") == "and" and all(r.get("n_channels") == _need for r in (_sa.get("rows") or []))
rows[-1]["out"] = "채널 %s · 교집합만 %d행" % (_sa.get("channels"), len(_sa.get("rows") or []))
out = run("search 다중채널 rrf", ["search", "fts,vector", "RX DMA underrun", "--mode", "rrf", "--k", "3", "--json"])
_sr = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and _sr.get("mode") == "rrf" and isinstance(_sr.get("weights"), dict) and bool(_sr["weights"])
rows[-1]["out"] = "가중치 %s" % _sr.get("weights")
# 문서 유형 필터 (2026-09-20) — **거르는** 조건이다 (질의의 doc_types 는 가중치). 필터 없이 돈 결과와 비교한다.
out = run("search --doc-types (필터 없음 기준)", ["search", "fts", "RX DMA underrun", "--k", "8", "--json"])
_sall = J(out)
out = run("search --doc-types issue", ["search", "fts", "RX DMA underrun", "--k", "8", "--doc-types", "issue", "--json"])
_sdt = J(out)
_docs = [str(r.get("doc_id") or "") for r in (_sdt.get("rows") or [])]
rows[-1]["ok"] = rows[-1]["ok"] and _sdt.get("doc_types") == ["issue"] \
    and len(_sdt.get("rows") or []) <= len(_sall.get("rows") or []) \
    and all("/issues/" in d or "ISSUE" in d.upper() for d in _docs) \
    and "문서유형" in str(_sdt.get("expr"))
rows[-1]["out"] = "유형 필터 %d행 (전체 %d행) · 걸러냄 %s" % (
    len(_sdt.get("rows") or []), len(_sall.get("rows") or []), (_sdt.get("counts") or {}).get("doc_type_filtered"))
out = run("search --doc-types (없는 유형 → 0행)", ["search", "fts", "RX DMA underrun", "--doc-types", "없는유형", "--json"])
rows[-1]["ok"] = rows[-1]["ok"] and (J(out).get("rows") or []) == []
rows[-1]["out"] = "오타가 '전체' 로 풀리지 않는다"
# 질의 해부 (LLM 없음) — CLI `inspect` · Web /api/debug/query · MCP wiki_inspect 가 같은 함수
out = run("inspect", ["inspect", "지난주 ISSUE-2001 의 원인"])
rows[-1]["ok"] = rows[-1]["ok"] and "[1] 토큰화" in out and "[4] 채널 라우팅" in out
out = run("inspect --json", ["inspect", "지난주 ISSUE-2001 의 원인", "--json"])
_iq = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and all(k in _iq for k in ("tokens", "keywords", "fts", "time", "router", "pins", "stats")) \
    and isinstance((_iq.get("router") or {}).get("weights"), dict)
rows[-1]["out"] = "토큰 %d · 규칙 %s · 라우팅 %s" % (len(_iq.get("tokens") or []), (_iq.get("stats") or {}).get("rules_fired"), (_iq.get("router") or {}).get("kind"))
# 단계별 시간 제한 (2026-09-19)
out = run("arch limits", ["arch", "limits"])
rows[-1]["ok"] = rows[-1]["ok"] and "역할별 LLM 1회 제한" in out and "단계별" in out
out = run("arch limits --json", ["arch", "limits", "--json"])
_al = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and all(k in _al for k in ("flows", "stages", "trace", "roles")) and "answer_llm" in (_al.get("trace") or {})
rows[-1]["out"] = "단계 %d · trace %d" % (len(_al.get("stages") or {}), len(_al.get("trace") or {}))
# 질의 로그에 '누가' (2026-09-19)
out = run("requests queries", ["requests", "queries", "--limit", "5"])
rows[-1]["ok"] = rows[-1]["ok"] and "Traceback" not in out
out = run("requests queries --json", ["requests", "queries", "--limit", "5", "--json"])
# J() 는 첫 '{' 부터 자르므로 **배열** 출력에는 쓸 수 없다 (첫 원소만 읽고 Extra data 로 죽는다)
_rq = json.loads(out[out.index("["):]) if "[" in out else []
rows[-1]["ok"] = rows[-1]["ok"] and isinstance(_rq, list) and (not _rq or all(k in _rq[0] for k in ("id", "query", "user", "origin", "request_id")))
rows[-1]["out"] = "%d행" % len(_rq if isinstance(_rq, list) else [])
out = run("requests users", ["requests", "users"])
rows[-1]["ok"] = rows[-1]["ok"] and "사용자" in out and "Traceback" not in out
# 로그 총량 제한 (1차 §2.11)
out = run("logs status", ["logs", "status"])
rows[-1]["ok"] = rows[-1]["ok"] and "log quota" in out and "MB" in out
rows[-1]["out"] = out.splitlines()[0][:100] if out.strip() else ""
out = run("logs status --json", ["logs", "status", "--json"])
_lq = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and all(k in _lq for k in ("total_mb", "limit_mb", "over", "enabled", "action", "files"))
rows[-1]["out"] = "total=%s MB limit=%s MB action=%s" % (_lq.get("total_mb"), _lq.get("limit_mb"), _lq.get("action"))
# 앙상블 (1차 §E) — show / set / show 왕복 (config.json llm_roles.<role>.ensemble)
out = run("models ensemble show", ["models", "ensemble", "show"])
rows[-1]["ok"] = rows[-1]["ok"] and "answer" in out and "ensemble=" in out
out = run("models ensemble show answer", ["models", "ensemble", "show", "answer"])
rows[-1]["ok"] = rows[-1]["ok"] and out.strip().startswith("answer") and "ensemble=off" in out
out = run("models ensemble show answer --json", ["models", "ensemble", "show", "answer", "--json"])
_en = J(out)
rows[-1]["ok"] = rows[-1]["ok"] and "answer" in _en and "effective" in _en["answer"] and _en["answer"]["effective"].get("enabled") is False
run("models ensemble show (없는 역할) → 1", ["models", "ensemble", "show", "king"], expect=1)
run("models ensemble set (인자 없음) → 1", ["models", "ensemble", "set"], expect=1)
out = run("models ensemble set answer (mock 2멤버)", ["models", "ensemble", "set", "answer", "--enabled", "true",
                                                    "--member", "1", "provider=mock", "model=mock-a", "weight=1.5", "--member", "2", "provider=mock", "model=mock-b",
                                                    "--wait", "all", "--timeout", "60", "--min", "1"])
rows[-1]["ok"] = rows[-1]["ok"] and "saved llm_roles.answer.ensemble" in out and "enabled=True" in out
_cfg3 = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
_ens = ((_cfg3.get("llm_roles") or {}).get("answer") or {}).get("ensemble") or {}
rows.append({"name": "config.json llm_roles.answer.ensemble 저장", "argv": "(파일 확인)", "code": 0, "expect": 0,
             "ok": _ens.get("enabled") is True and len([m for m in _ens.get("members") or [] if m.get("model")]) == 2 and _ens.get("wait") == "all", "ms": 0,
             "out": "members=%s wait=%s" % ([m.get("model") for m in _ens.get("members") or [] if m.get("model")], _ens.get("wait"))})
out = run("models ensemble show answer (반영)", ["models", "ensemble", "show", "answer"])
rows[-1]["ok"] = rows[-1]["ok"] and "ensemble=ON" in out and "mock/mock-a" in out and "mock/mock-b" in out
out = run("query (앙상블 켠 상태)", ["query", "ISSUE-2001 원인", "--json", "--no-log"])
rows[-1]["ok"] = rows[-1]["ok"] and bool((J(out).get("result") or {}).get("answer"))
run("models ensemble set answer --enabled false", ["models", "ensemble", "set", "answer", "--enabled", "false"])
out = run("models ensemble show answer (끔)", ["models", "ensemble", "show", "answer"])
rows[-1]["ok"] = rows[-1]["ok"] and "ensemble=off" in out
run("rerun --points", ["rerun", "--points"])
run("rerun --list", ["rerun", "--list"])

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
