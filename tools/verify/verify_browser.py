"""브라우저(Edge headless) 로 Web UI 를 실제 로드해 JS 오류·초기 렌더를 확인한다. 실제 DB(프로젝트 루트) 로 127.0.0.1 전용 서버를 띄운다(mode auto → 로컬 admin)."""
import os, re, subprocess, sys, time, urllib.request, json
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # <프로젝트 루트>/tools/verify/ 기준
import shutil
EDGE = next((p for p in (os.environ.get("LLMWIKI_BROWSER", ""), shutil.which("msedge"), shutil.which("chrome"), shutil.which("google-chrome"), shutil.which("chromium"),
                         r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe") if p and os.path.exists(p)), None)
if not EDGE:
    print("BROWSER SKIP: Edge/Chrome 을 찾지 못함 (LLMWIKI_BROWSER=<실행 파일> 로 지정)"); sys.exit(0)
PORT = 8793
env = dict(os.environ, PYTHONIOENCODING="utf-8")
proc = subprocess.Popen([sys.executable, "-m", "llmwiki", "serve", "--port", str(PORT)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/auth/me" % PORT, timeout=2); break
        except Exception:
            time.sleep(0.5)
    out = {}
    for name, url in (("index", "http://127.0.0.1:%d/" % PORT), ("login", "http://127.0.0.1:%d/login" % PORT)):
        import tempfile
        prof = os.path.join(tempfile.gettempdir(), "llmwiki_verify_browser_profile")
        r = subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--user-data-dir=" + prof, "--enable-logging=stderr", "--v=0",
                            "--virtual-time-budget=8000", "--dump-dom", url], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        dom, log = r.stdout, r.stderr
        errs = [l.strip() for l in log.splitlines() if "CONSOLE" in l and ("Uncaught" in l or "error" in l.lower()) and "favicon" not in l]
        consoles = [l.strip()[:200] for l in log.splitlines() if "CONSOLE" in l]
        out[name] = {"dom_len": len(dom), "console_lines": len(consoles), "errors": errs[:20], "console": consoles[:20]}
        if name == "index":
            # JS 가 실제로 실행되어 채운 흔적: 상태 배지·탭 목록·사용자 배지
            out[name]["user_badge"] = bool(re.search(r'id="user-badge"[^>]*>[^<]+<', dom)) or ("guest" in dom) or ("admin" in dom)
            out[name]["status_filled"] = bool(re.search(r'id="status-line"[^>]*>[^<]{3,}', dom)) or ("docs" in dom.lower())
            out[name]["tabs"] = len(re.findall(r'data-tab="', dom))
            m = re.search(r'<title>([^<]*)</title>', dom); out[name]["title"] = m.group(1) if m else ""
            # 기능 요소 존재
            for eid in ("btn-q-expect", "q-llm-report", "build-channels", "sec-perms", "sec-keys", "fx-docs", "bc-fts"):
                out[name]["has_" + eid] = ('id="%s"' % eid) in dom
    print(json.dumps(out, ensure_ascii=False, indent=1))
    ok = all(not v["errors"] for v in out.values()) and out["index"]["dom_len"] > 10000
    print("BROWSER", "OK" if ok else "PROBLEMS")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
