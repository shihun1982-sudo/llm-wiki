"""브라우저(Edge/Chrome headless) 로 Web UI 를 실제 로드해 JS 오류·초기 렌더를 확인한다.

- 실제 DB(프로젝트 루트) 로 127.0.0.1 전용 서버를 띄운다 (mode auto → 로컬 admin).
- **탭마다 한 번씩** `#<group>/<tab>` 해시로 열어 그 탭의 loader(=API 호출·렌더)가 실제로 돌게 하고, 콘솔 오류와
  렌더 결과(그 탭 섹션 안에 내용이 채워졌는지)를 확인한다. "메뉴가 눌리기만 하고 아무 동작도 안 하는" 상태를 잡기 위함.
- 결과: 탭별 표 + BROWSER OK / PROBLEMS.
"""
import json
import os
import re
import shutil
import subprocess
import sys

# 콘솔이 cp949 여도 한글·기호 출력에서 죽지 않게 (다른 verify_* 와 같은 처리, 2026-09-24)
try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
except Exception:
    pass
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # <프로젝트 루트>/tools/verify/ 기준
EDGE = next((p for p in (os.environ.get("LLMWIKI_BROWSER", ""), shutil.which("msedge"), shutil.which("chrome"), shutil.which("google-chrome"), shutil.which("chromium"),
                         r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe") if p and os.path.exists(p)), None)
if not EDGE:
    print("BROWSER SKIP: Edge/Chrome 을 찾지 못함 (LLMWIKI_BROWSER=<실행 파일> 로 지정)")
    sys.exit(0)
PORT = int(os.environ.get("LLMWIKI_VERIFY_PORT", "8793"))
BUDGET = os.environ.get("LLMWIKI_VERIFY_BUDGET", "9000")   # 탭당 가상 시간(ms)
PROF = os.path.join(tempfile.gettempdir(), "llmwiki_verify_browser_profile")

html = open(os.path.join(ROOT, "llmwiki", "web", "static", "index.html"), encoding="utf-8").read()
# 그룹별 탭 목록 (nav 순서대로)
GROUPS = []
for m in re.finditer(r'<nav class="tabs[^"]*" data-group="([^"]+)">(.*?)</nav>', html, re.S):
    GROUPS.append((m.group(1), re.findall(r'data-tab="([^"]+)"', m.group(2))))
def section_body(doc, tab):
    m = re.search(r'<section id="tab-%s"[^>]*>(.*?)</section>' % re.escape(tab), doc, re.S)
    return m.group(1) if m else ""


# 판정: 브라우저에서 연 탭의 본문이 정적 HTML 보다 길어졌다면 그 탭의 loader(API 호출·렌더)가 실제로 동작한 것.


def shot(url, budget=BUDGET):
    r = subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--user-data-dir=" + PROF,
                        "--enable-logging=stderr", "--v=0", "--virtual-time-budget=" + str(budget), "--dump-dom", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    dom, log = r.stdout, r.stderr
    errs = [l.strip()[:300] for l in log.splitlines()
            if "CONSOLE" in l and ("Uncaught" in l or "ERROR" in l or re.search(r"\berror\b", l, re.I)) and "favicon" not in l
            and "Password field is not contained in a form" not in l]
    return dom, errs, [l.strip()[:200] for l in log.splitlines() if "CONSOLE" in l]


env = dict(os.environ, PYTHONIOENCODING="utf-8")
proc = subprocess.Popen([sys.executable, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(PORT)], cwd=ROOT, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
rows = []
try:
    for _ in range(90):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/auth/me" % PORT, timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    base = "http://127.0.0.1:%d" % PORT
    out = {}
    for name, url in (("index", base + "/"), ("login", base + "/login")):
        dom, errs, consoles = shot(url)
        out[name] = {"dom_len": len(dom), "console_lines": len(consoles), "errors": errs[:10]}
        if name == "index":
            out[name]["tabs"] = len(re.findall(r'data-tab="', dom))
            out[name]["user_badge"] = bool(re.search(r'id="user-badge"[^>]*>[^<]+<', dom)) or ("게스트" in dom) or ("admin" in dom)
            m = re.search(r"<title>([^<]*)</title>", dom)
            out[name]["title"] = m.group(1) if m else ""
            for eid in ("btn-q-expect", "q-llm-report", "build-channels", "sec-perms", "sec-keys", "fx-docs", "bc-fts",
                        "btn-pin-tab", "q-queue", "pinned-pane", "server-badge", "sch-table", "srv-limits", "act-running",
                        "cat-table", "roles-show-policy", "build-external"):
                out[name]["has_" + eid] = ('id="%s"' % eid) in dom
    # ---- 탭별 실제 동작 확인 (해시 라우팅으로 loader 실행) ----
    print("탭별 렌더 확인 (%d개)…" % sum(len(t) for _, t in GROUPS))
    for group, tabs in GROUPS:
        for tab in tabs:
            dom, errs, _ = shot("%s/#%s/%s" % (base, group, tab))
            sec = re.search(r'<section id="tab-%s"[^>]*class="([^"]*)"' % re.escape(tab), dom)
            active = bool(sec and "active" in sec.group(1))
            body = section_body(dom, tab)
            static = len(section_body(html, tab))
            grew = len(body) > static + 20      # loader 가 뭔가를 그렸다 (빈 목록이면 그대로일 수 있으므로 실패 조건은 아님)
            ok = active and not errs
            rows.append({"group": group, "tab": tab, "active": active, "rendered": grew, "errors": errs[:3],
                         "body_len": len(body), "static_len": static, "ok": ok})
            print("  %s %-14s %-12s body=%-6d %s%s" % ("OK " if ok else "FAIL", group, tab, len(body),
                                                       "렌더됨" if grew else "정적(빈 목록일 수 있음)",
                                                       ("  · " + errs[0][:110]) if errs else ""))
    out["tabs_checked"] = len(rows)
    out["tabs_failed"] = [r for r in rows if not r["ok"]]
    print(json.dumps({k: v for k, v in out.items() if k not in ("tabs_failed", "tabs_static")}, ensure_ascii=False, indent=1))
    out["tabs_static"] = [("%s/%s" % (r["group"], r["tab"])) for r in rows if not r["rendered"]]
    if out["tabs_static"]:
        print("정적 그대로인 탭(빈 목록이거나 loader 가 그릴 내용이 없음): %s" % ", ".join(out["tabs_static"]))
    if out["tabs_failed"]:
        print("문제 탭 %d개:" % len(out["tabs_failed"]))
        for r in out["tabs_failed"]:
            print("  %s/%s active=%s errors=%s" % (r["group"], r["tab"], r["active"], r["errors"]))
    ok = all(not v.get("errors") for v in (out["index"], out["login"])) and out["index"]["dom_len"] > 10000 and not out["tabs_failed"]
    json.dump({"pages": {k: v for k, v in out.items() if k in ("index", "login")}, "tabs": rows},
              open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_browser_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("BROWSER", "OK" if ok else "PROBLEMS")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
