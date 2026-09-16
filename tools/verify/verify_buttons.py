"""Web UI 의 **모든 버튼을 실제로 눌러** 기대한 동작을 하는지 확인한다.

왜 있나: 렌더 검사(`verify_browser.py`)는 "그려지는가" 만 본다. 눌러도 아무 일도 일어나지 않는 버튼
(핸들러가 조용히 return 하거나 예외로 죽거나, 애초에 배선이 안 된 경우)은 잡지 못한다.
이 스크립트는 헤드리스 브라우저를 CDP 로 붙잡고 버튼을 하나씩 클릭한 뒤 이렇게 판정한다.

  FAIL  콘솔/프라미스 오류가 났다
  FAIL  아무 일도 일어나지 않았다 (서버 호출도, 화면 변화도, 알림도 없음)
  OK    위 둘 다 아니고, 기대 조건(있으면)도 만족한다

**격리 환경에서 돈다.** 설정·DB 를 임시 폴더로 복사하고 mock LLM 으로 서버를 띄우므로,
저장·초기화 같은 버튼을 눌러도 실제 데이터가 바뀌지 않는다.

실행:
    python tools/verify/verify_buttons.py              # 전체 (무거운 것 제외)
    python tools/verify/verify_buttons.py --only evolve,memory
    python tools/verify/verify_buttons.py --heavy      # 전체 빌드·평가 등 오래 걸리는 것도
    python tools/verify/verify_buttons.py --live       # 격리하지 않고 지금 서버에 (읽기 버튼만)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_click import WS, Page, EDGE            # noqa: E402  (최소 WebSocket/CDP 클라이언트 재사용)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
HTML = os.path.join(ROOT, "llmwiki", "web", "static", "index.html")

# 오래 걸려서 기본으로는 누르지 않는 것 (--heavy 로 포함)
HEAVY = {"btn-build", "btn-build-full", "btn-eval", "btn-eval-matrix", "btn-tr-run", "btn-fusion-compare",
         "btn-models-test-live", "btn-cat-discover", "btn-docvec", "btn-pc-run", "btn-w-rebuild",
         "btn-query", "btn-fx-llm", "btn-ev-review", "btn-mem-consolidate", "btn-fx-consolidate",
         "btn-src-ingest", "btn-src-retrieve", "btn-verify", "btn-verify-fix", "btn-health", "btn-scan",
         "btn-snap-create"}   # 스냅샷은 색인 DB 전체(수백 MB)를 복사한다
# 격리 환경이어도 화면을 되돌리기 어려운 것 (--live 에서는 자동으로 제외된다)
MUTATING = {"btn-config-save", "btn-config-reload", "btn-models-save", "btn-agents-save", "btn-tuning-save",
            "btn-tuning-reset", "btn-tuning-reload", "btn-preset-save", "btn-prompt-save", "btn-prompt-reset",
            "btn-rules-save", "btn-qr-save", "btn-w-save", "btn-srv-save", "btn-srv-reload", "btn-srv-maint",
            "btn-srv-block", "btn-srv-circuit-reset", "btn-srv-loglevel", "btn-sec-reload", "btn-snap-create",
            "btn-user-add", "btn-pw-change", "btn-sch-save", "btn-sch-add", "btn-sch-test", "btn-embed-cache-clear",
            "btn-pc-clear", "btn-watch-start", "btn-watch-stop", "btn-watch-scan", "btn-watch-tick",
            "btn-perf-save", "btn-src-save", "btn-mem-decay", "btn-console", "btn-cat-add", "btn-mp-add",
            "btn-qr-add", "btn-pin-add", "btn-sch-remove"}

# 입력이 필요한 버튼: 누르기 전에 채워 준다 (그러지 않으면 '입력하세요' 안내만 나오고 끝난다)
PREFILL = {
    "btn-q-expect-run": "document.querySelector('#qx-docs')&&(document.querySelector('#qx-docs').value='ISSUE-2001');",
    "btn-fx-expect": "document.querySelector('#fx-docs').value='ISSUE-2001';",
    "btn-search": "document.querySelector('#s-q')&&(document.querySelector('#s-q').value='PDCCH');",
    "btn-rules-test": "document.querySelector('#rules-test-q')&&(document.querySelector('#rules-test-q').value='PDCCH 디코더');",
    "btn-time-test": "document.querySelector('#time-q')&&(document.querySelector('#time-q').value='지난주 리뷰한 CL');",
    "btn-qr-test": "document.querySelector('#qr-test-q')&&(document.querySelector('#qr-test-q').value='PDCCH');",
    "btn-graph": "document.querySelector('#g-entity')&&(document.querySelector('#g-entity').value='ISSUE-2001');",
    "btn-tr-compare": "",
    "btn-tr-md": "",
}

# 버튼별 기대 조건(선택). 없으면 '오류 없음 + 무언가 일어남' 만 본다.
EXPECT = {
    "btn-ev-refresh": "document.querySelector('#ev-pending').innerHTML.length>20"
                      " && document.querySelector('#ev-summary').textContent.indexOf('pending')>=0",
    "btn-mem-refresh": "document.querySelector('#mem-status').textContent.indexOf('[object Object]')<0"
                       " && document.querySelector('#mem-episodes').innerHTML.length>20",
    "btn-fx-refresh": "document.querySelector('#fx-list').innerHTML.length>20",
    "btn-fx-run": "document.querySelector('#fx-detail').textContent.indexOf('포렌식 #')>=0",
    "btn-req-refresh": "!!document.querySelector('#req-list table')",
    "btn-act-refresh": "!!document.querySelector('#act-gauge .ag-slots')",
    "btn-logs-load": "!!document.querySelector('#log-table table')",
    "btn-logs": "!!document.querySelector('#logs table')",
    "btn-srv-refresh": "document.querySelector('#srv-overview').innerHTML.length>20",
    "btn-sys-refresh": "document.querySelector('#tab-system').innerHTML.length>500",
    "btn-embed-refresh": "document.querySelector('#embed-cov').innerHTML.length>20",
    "btn-preset-refresh": "document.querySelector('#preset-cards').innerHTML.length>10",
    "btn-cat-refresh": "document.querySelector('#cat-table').innerHTML.length>10"
                       " || document.querySelector('#cat-discover').innerHTML.length>0",
    "btn-sch-refresh": "document.querySelector('#sch-table').innerHTML.length>10",
    "btn-sec-refresh": "document.querySelector('#sec-users').innerHTML.length>10",
    "btn-arch-refresh": "document.querySelector('#arch-flows').innerHTML.length>50",
    "btn-lint-refresh": "document.querySelector('#lint-table').innerHTML.length>10"
                        " || document.querySelector('#lint-summary').innerHTML.length>10",
    "btn-config-effective": "document.querySelector('#config-effective').textContent.length>50",
}

SHIM = """
window.__lw = {errs: [], fetches: [], toasts: []};
window.addEventListener('error', function(e){ __lw.errs.push('error: ' + (e.message||e)); });
window.addEventListener('unhandledrejection', function(e){
  var r = e.reason; __lw.errs.push('promise: ' + ((r && (r.message||r.stack)) || r)); });
(function(){ var of = window.fetch; window.fetch = function(){ try{ __lw.fetches.push(String(arguments[0])); }catch(x){}
  return of.apply(this, arguments); }; })();
(function(){ var t = document.getElementById('toast'); if (!t) return;
  new MutationObserver(function(){ if (t.textContent) __lw.toasts.push(t.textContent); })
    .observe(t, {childList:true, characterData:true, subtree:true}); })();
window.confirm = function(){ return false; };      // 확인 대화상자는 '취소' 로 (파괴적 진행 방지)
window.alert = function(){};                        // 모달이 메인 스레드를 막지 않게
window.prompt = function(){ return null; };
window.__lwReset = function(){ __lw.errs=[]; __lw.fetches=[]; __lw.toasts=[]; };
'ok'
"""


def tab_map(html: str):
    """버튼 id → (group, tab)"""
    groups = {}
    for g, body in re.findall(r'<nav class="tabs[^"]*" data-group="([^"]+)">(.*?)</nav>', html, re.S):
        for t in re.findall(r'data-tab="([^"]+)"', body):
            groups[t] = g
    out = {}
    for tab, body in re.findall(r'<section id="tab-([^"]+)"(.*?)</section>', html, re.S):
        for bid in re.findall(r'<button[^>]*id="([^"]+)"', body):
            out[bid] = (groups.get(tab, ""), tab)
    return out


def isolated_env(port: int, extra_cfg=None, extra_files=None, serve: bool = True):
    """설정·DB 를 임시 폴더로 복사하고 mock LLM 으로 서버를 띄운다. (tmp, env, proc) 반환.

    extra_cfg   : config.json 에 덮어쓸 키 (예: {"mcp_plugins_dir": "..."} )
    extra_files : 임시 폴더에 추가로 쓸 파일 {이름: 객체(JSON 으로 저장)}
    serve       : False 면 서버를 띄우지 않는다 (proc=None) — stdio MCP 처럼 env 만 필요할 때.
    """
    tmp = tempfile.mkdtemp(prefix="lwbtn_")
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    cfg["data_dir"] = os.path.join(tmp, "data")
    cfg["wiki_dir"] = os.path.join(tmp, "wiki")
    cfg["llm_provider"] = "mock"
    cfg["llm_roles"] = {}
    cfg["toggles"] = dict(cfg.get("toggles") or {}, auto_build=False, precompute=False, query_cache=False)
    os.makedirs(cfg["data_dir"])
    src_db = os.path.join(ROOT, "data", "llmwiki.sqlite3")
    if os.path.exists(src_db):
        print("  색인 DB 복사 중 (%.0f MB)…" % (os.path.getsize(src_db) / 1e6))
        shutil.copy2(src_db, os.path.join(cfg["data_dir"], cfg.get("db_name", "llmwiki.sqlite3")))
    if os.path.isdir(os.path.join(ROOT, "wiki")):
        shutil.copytree(os.path.join(ROOT, "wiki"), cfg["wiki_dir"])
    cfg.update(extra_cfg or {})
    json.dump(cfg, open(os.path.join(tmp, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for f in ("tuning.json", "presets.json", "query_rules.json", "mcp_sources.json", "agents.json", "pins.json",
              "security.json", "server.json", "schedule.json", "models.json"):
        s = os.path.join(ROOT, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(tmp, f))
    for name, obj in (extra_files or {}).items():
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    shutil.copy2(os.path.join(ROOT, "data", "rules.json"), os.path.join(tmp, "rules.json"))
    shutil.copytree(os.path.join(ROOT, "schemas"), os.path.join(tmp, "schemas"))
    shutil.copytree(os.path.join(ROOT, "prompts"), os.path.join(tmp, "prompts"))
    shutil.copy2(os.path.join(ROOT, "eval", "questions.json"), os.path.join(tmp, "questions.json"))
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               LLMWIKI_CONFIG=os.path.join(tmp, "config.json"), LLMWIKI_TUNING_PATH=os.path.join(tmp, "tuning.json"),
               LLMWIKI_PRESETS_PATH=os.path.join(tmp, "presets.json"), LLMWIKI_QUERY_RULES_PATH=os.path.join(tmp, "query_rules.json"),
               LLMWIKI_MCP_SOURCES_PATH=os.path.join(tmp, "mcp_sources.json"), LLMWIKI_AGENTS_PATH=os.path.join(tmp, "agents.json"),
               LLMWIKI_PINS_PATH=os.path.join(tmp, "pins.json"), LLMWIKI_RULES_PATH=os.path.join(tmp, "rules.json"),
               LLMWIKI_SCHEMAS_DIR_PATH=os.path.join(tmp, "schemas"), LLMWIKI_PROMPTS_DIR_PATH=os.path.join(tmp, "prompts"),
               LLMWIKI_EVAL_PATH=os.path.join(tmp, "questions.json"), LLMWIKI_LOGS_DIR_PATH=os.path.join(tmp, "logs"),
               LLMWIKI_SECURITY_PATH=os.path.join(tmp, "security.json"), LLMWIKI_SERVER_PATH=os.path.join(tmp, "server.json"),
               LLMWIKI_SCHEDULE_PATH=os.path.join(tmp, "schedule.json"), LLMWIKI_MODELS_PATH=os.path.join(tmp, "models.json"))
    env.pop("LLMWIKI_API_KEY", None)
    proc = None
    if serve:
        proc = subprocess.Popen([PY, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(port)],
                                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return tmp, env, proc


def main(argv=None) -> int:
    if not EDGE:
        print("BUTTONS SKIP: Edge/Chrome 을 찾지 못함 (LLMWIKI_BROWSER 로 지정)")
        return 0
    ap = argparse.ArgumentParser(description="Web UI 의 모든 버튼을 눌러 동작을 확인")
    ap.add_argument("--port", type=int, default=8830)
    ap.add_argument("--cdp", type=int, default=9340)
    ap.add_argument("--only", default="", help="탭 이름 쉼표 (예: evolve,memory)")
    ap.add_argument("--heavy", action="store_true", help="빌드·평가처럼 오래 걸리는 버튼도 누른다")
    ap.add_argument("--live", action="store_true", help="격리하지 않고 지금 서버에 (상태를 바꾸는 버튼은 제외)")
    ap.add_argument("--wait", type=float, default=2.5, help="클릭 후 대기(초)")
    ap.add_argument("--keep", action="store_true")
    ns = ap.parse_args(argv)

    html = open(HTML, encoding="utf-8").read()
    tabs = tab_map(html)
    skip = set() if ns.heavy else set(HEAVY)
    if ns.live:
        skip |= MUTATING
    only = {x.strip() for x in ns.only.split(",") if x.strip()}
    targets = [(b, g, t) for b, (g, t) in tabs.items() if b not in skip and (not only or t in only)]
    targets.sort(key=lambda x: (x[2], x[0]))

    base = "http://127.0.0.1:%d" % ns.port
    prof = os.path.join(tempfile.gettempdir(), "llmwiki_buttons_profile")
    shutil.rmtree(prof, ignore_errors=True)
    tmp = proc = br = None
    rows = []
    try:
        if ns.live:
            print("  대상: 지금 떠 있는 서버 %s (상태 변경 버튼 제외)" % base)
        else:
            tmp, _env, proc = isolated_env(ns.port)
            print("  격리 서버 시작: %s (data=%s)" % (base, tmp))
        for _ in range(150):
            try:
                urllib.request.urlopen(base + "/api/auth/me", timeout=2)
                break
            except Exception:
                time.sleep(1)
        br = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
                               "--user-data-dir=" + prof, "--remote-debugging-port=%d" % ns.cdp, base + "/"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = None
        for _ in range(60):
            try:
                lst = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % ns.cdp, timeout=2).read().decode())
                url = next((t["webSocketDebuggerUrl"] for t in lst if t.get("type") == "page"), None)
                if url:
                    break
            except Exception:
                pass
            time.sleep(1)
        if not url:
            print("BUTTONS SKIP: 브라우저 디버깅 포트에 붙지 못했습니다")
            return 0
        def connect():
            for _ in range(40):
                try:
                    lst = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % ns.cdp, timeout=2).read().decode())
                    u = next((t["webSocketDebuggerUrl"] for t in lst if t.get("type") == "page"), None)
                    if u:
                        pg = Page(WS(u))
                        pg.eval("new Promise(r=>setTimeout(()=>r(1), 1500))")
                        pg.eval(SHIM)
                        return pg
                except Exception:
                    pass
                time.sleep(1)
            return None

        page = Page(WS(url))
        page.eval("new Promise(r=>setTimeout(()=>r(1), 5000))")
        page.eval(SHIM)

        def timed_out(v):
            return isinstance(v, dict) and v.get("__timeout__")

        print("\n버튼 %d개 클릭 (건너뜀 %d개)\n" % (len(targets), len(skip)))
        cur = None
        for bid, group, tab in targets:
            if (group, tab) != cur:
                page.eval("location.hash='#%s/%s'; 'ok'" % (group, tab))
                page.eval("new Promise(r=>setTimeout(()=>r(1), 2200))")
                cur = (group, tab)
            page.eval("window.__lwReset(); 'ok'")
            pre = PREFILL.get(bid)
            if pre:
                page.eval(pre + " 'ok'")
            before = page.eval("(function(){var s=document.querySelector('#tab-%s'); return s?s.innerHTML.length:0;})()" % tab)
            clicked = page.eval("(function(){var b=document.getElementById('%s');"
                                " if(!b) return 'no-el'; if(b.disabled) return 'disabled'; b.click(); return 'clicked';})()" % bid)
            if timed_out(clicked):
                # 페이지가 응답하지 않는다 → 이 버튼은 실패로 기록하고 붙어서 계속한다
                rows.append(("FAIL", tab, bid, "클릭 후 페이지가 응답하지 않음(시간 초과)", {}))
                print("FAIL %-10s %-22s 페이지 응답 없음 — 다시 연결" % (tab, bid))
                pg = connect()
                if pg is None:
                    print("  브라우저에 다시 붙지 못했습니다 — 중단")
                    break
                page, cur = pg, None
                continue
            page.eval("new Promise(r=>setTimeout(()=>r(1), %d))" % int(ns.wait * 1000))
            st = page.eval("(function(){var s=document.querySelector('#tab-%s');"
                           " return {errs: __lw.errs.slice(0,3), fetches: __lw.fetches.length, toasts: __lw.toasts.slice(0,2),"
                           " len: s?s.innerHTML.length:0};})()" % tab) or {}
            if timed_out(st):
                rows.append(("FAIL", tab, bid, "클릭 뒤 상태를 읽지 못함(시간 초과)", {}))
                print("FAIL %-10s %-22s 상태 확인 시간 초과 — 다시 연결" % (tab, bid))
                pg = connect()
                if pg is None:
                    break
                page, cur = pg, None
                continue
            errs = st.get("errs") or []
            moved = (st.get("len") != before) or st.get("fetches") or st.get("toasts")
            exp = EXPECT.get(bid)
            exp_ok = True if not exp else page.eval(exp)
            verdict = "OK  "
            why = ""
            if clicked != "clicked":
                verdict, why = "FAIL", clicked
            elif errs:
                verdict, why = "FAIL", "오류 " + str(errs[0])[:90]
            elif not moved:
                verdict, why = "FAIL", "아무 일도 일어나지 않음 (호출·화면변화·알림 없음)"
            elif exp_ok is not True:
                verdict, why = "FAIL", "기대 조건 불충족" + (": " + str(exp_ok.get("__error__"))[:60] if isinstance(exp_ok, dict) else "")
            rows.append((verdict, tab, bid, why, st))
            print("%s %-10s %-22s fetch=%-2s dom=%+-6d %s%s" % (
                verdict, tab, bid, st.get("fetches"), (st.get("len") or 0) - (before or 0),
                (("알림: " + str((st.get("toasts") or [""])[0])[:40]) if st.get("toasts") else ""), ("  " + why) if why else ""))
        bad = [r for r in rows if r[0] == "FAIL"]
        print("\n버튼 %d개 중 %d개 통과 · 실패 %d개" % (len(rows), len(rows) - len(bad), len(bad)))
        for r in bad:
            print("  FAIL %-10s %-22s %s" % (r[1], r[2], r[3]))
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_buttons_result.json")
        json.dump([{"verdict": r[0], "tab": r[1], "button": r[2], "why": r[3], "state": r[4]} for r in rows],
                  open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("결과: %s" % out)
        print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
        return 1 if bad else 0
    finally:
        for p in (br, proc):
            if p:
                p.terminate()
                try:
                    p.communicate(timeout=10)
                except Exception:
                    p.kill()
        if tmp and not ns.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
