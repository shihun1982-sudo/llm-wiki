"""설정 ▸ **스케줄** 화면을 실제로 조작해 확인한다 (작업 추가 → 저장 → 실행 → 이력 → 삭제).

왜 따로 있나: `verify_buttons.py` 는 스케줄 버튼도 **빈 입력으로** 누른다. `btn-sch-save` 는
이름이 비면 "이름을 입력하세요" 로 끝나므로, 눌러도 실제 저장 경로는 한 번도 지나지 않는다
(보안 탭에서 겪은 것과 같은 함정). 여기서는 폼을 채우고 눌러 **서버의 schedule.json 과 실행 이력까지**
확인한다.

실행:
    python tools/verify/verify_schedule_ui.py --port 8951 --cdp 9451
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
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_click import WS, Page, EDGE            # noqa: E402
from verify_buttons import isolated_env, SHIM      # noqa: E402

ROWS = []
NAME = "verify-sched"


def rec(ok, name, detail=""):
    ROWS.append({"ok": bool(ok), "name": name, "detail": str(detail)[:150]})
    print("%s %-44s %s" % ("OK  " if ok else "FAIL", name, str(detail)[:100]), flush=True)
    return bool(ok)


def js(v):
    return json.dumps(v, ensure_ascii=False)


def wait_for(page, expr, seconds=25.0):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if page.eval(expr) is True:
            return True
        page.eval("new Promise(r=>setTimeout(()=>r(1), 700))")
    return False


def main(argv=None) -> int:
    if not EDGE:
        print("SKIP: Edge/Chrome 을 찾지 못함")
        return 0
    ap = argparse.ArgumentParser(description="스케줄 화면 조작 검증")
    ap.add_argument("--port", type=int, default=8951)
    ap.add_argument("--cdp", type=int, default=9451)
    ns = ap.parse_args(argv)

    base = "http://127.0.0.1:%d" % ns.port
    prof = os.path.join(tempfile.gettempdir(), "llmwiki_sched_profile")
    shutil.rmtree(prof, ignore_errors=True)
    tmp, _env, proc = isolated_env(ns.port)
    sched_path = os.path.join(tmp, "schedule.json")
    br = None
    try:
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
            print("SKIP: 브라우저에 붙지 못했습니다")
            return 0
        page = Page(WS(url))
        page.eval("new Promise(r=>setTimeout(()=>r(1), 5000))")
        page.eval(SHIM)

        page.eval("location.hash='#settings/schedule'; 'ok'")
        page.eval("new Promise(r=>setTimeout(()=>r(1), 2500))")

        rec(page.eval("!!document.querySelector('#sch-table')") is True and
            bool(page.eval("(document.querySelector('#sch-path')||{}).textContent")),
            "스케줄 화면이 그려진다", page.eval("(document.querySelector('#sch-path')||{}).textContent"))
        rec(page.eval("document.querySelectorAll('#sch-type option').length > 3") is True,
            "동작 종류 목록이 서버에서 채워진다", "%s종" % page.eval("document.querySelectorAll('#sch-type option').length"))

        # ---- 작업 추가 → 편집기 열림 ----
        page.eval("(function(){document.getElementById('btn-sch-add').click(); return 'ok';})()")
        page.eval("new Promise(r=>setTimeout(()=>r(1), 900))")
        rec(page.eval("!document.querySelector('#sch-editor').classList.contains('hidden')") is True,
            "'+ 작업 추가' 로 편집기가 열린다")

        # ---- 폼을 **채우고** 저장 (여기가 기존 버튼 검사가 못 보던 자리) ----
        page.eval("(function(){"
                  " document.querySelector('#sch-name').value=%s;"
                  " document.querySelector('#sch-when-kind').value='every';"
                  " document.querySelector('#sch-when-kind').dispatchEvent(new Event('change',{bubbles:true}));"
                  " document.querySelector('#sch-every').value='6h';"
                  " var ty=document.querySelector('#sch-type');"
                  " var opt=[].slice.call(ty.options).find(function(o){return /maintenance/.test(o.value);}) || ty.options[0];"
                  " ty.value=opt.value; ty.dispatchEvent(new Event('change',{bubbles:true}));"
                  " return ty.value;})()" % js(NAME))
        page.eval("new Promise(r=>setTimeout(()=>r(1), 600))")
        chosen = page.eval("document.querySelector('#sch-type').value")
        page.eval("(function(){var a=document.querySelector('#sch-action');"
                  " if(!a.value.trim()) a.value='{}'; return 'ok';})()")
        page.eval("window.__lwReset(); 'ok'")
        page.eval("(function(){document.getElementById('btn-sch-save').click(); return 'ok';})()")
        ok = wait_for(page, "document.querySelector('#sch-table').innerHTML.indexOf(%s) >= 0" % js(NAME))
        rec(ok, "폼을 채워 저장하면 표에 나타난다", "동작=%s · 알림=%s" % (chosen, (page.eval("__lw.toasts.slice(0,1)") or [""])[0]))

        # ---- 서버 파일에 실제로 들어갔는가 (화면만 그런 것이 아니라) ----
        saved = {}
        try:
            saved = json.load(open(sched_path, encoding="utf-8"))
        except Exception as e:
            saved = {"_err": str(e)}
        tasks = saved.get("tasks") or []
        mine = next((t for t in tasks if t.get("name") == NAME), None)
        rec(bool(mine), "schedule.json 에 저장됐다", json.dumps(mine or saved, ensure_ascii=False)[:110])
        if mine:
            rec(mine.get("every") == "6h", "입력한 실행 시점이 그대로 저장된다", "every=%s" % mine.get("every"))
            rec(bool(mine.get("action")), "동작 설정이 저장된다", json.dumps(mine.get("action"), ensure_ascii=False)[:70])

        # ---- API 로도 보이는가 ----
        try:
            j = json.loads(urllib.request.urlopen(base + "/api/schedule", timeout=20).read().decode())
        except Exception as e:
            j = {"error": str(e)}
        names = [t.get("name") for t in (j.get("tasks") or [])]
        rec(NAME in names, "GET /api/schedule 에 보인다", "tasks=%s running=%s" % (names, j.get("running")))

        # ---- 지금 실행 → 이력에 남는다 ----
        page.eval("window.__lwReset(); 'ok'")
        page.eval("(function(){var rows=[].slice.call(document.querySelectorAll('#sch-table tr'));"
                  " var tr=rows.find(function(x){return x.textContent.indexOf(%s)>=0;});"
                  " if(!tr) return 'no-row';"
                  " var b=tr.querySelector('[data-run],[data-sch-run]');"
                  " if(b){b.click(); return 'row-run';}"
                  " tr.click(); return 'opened';})()" % js(NAME))
        page.eval("new Promise(r=>setTimeout(()=>r(1), 800))")
        # 편집기의 '지금 실행' 으로도 시도 (행 버튼이 없을 수 있다)
        page.eval("(function(){var b=document.getElementById('btn-sch-test');"
                  " if(b && !document.querySelector('#sch-editor').classList.contains('hidden')) b.click(); return 'ok';})()")
        ran = wait_for(page, "document.querySelector('#sch-history').innerHTML.indexOf(%s) >= 0" % js(NAME), 40)
        if not ran:   # 화면 이력이 늦으면 API 로 확인
            try:
                j2 = json.loads(urllib.request.urlopen(base + "/api/schedule", timeout=20).read().decode())
                ran = any(h.get("name") == NAME for h in (j2.get("history") or []))
            except Exception:
                ran = False
        rec(ran, "'지금 실행' 이 돌고 이력에 남는다")

        # ---- 끄기 → 파일에 반영 (행의 '끄기' 버튼. 체크박스가 아니라 data-tog 버튼이다) ----
        clicked = page.eval("(function(){var b=document.querySelector('#sch-table [data-tog=\"%s\"]');"
                            " if(!b) return 'no-btn'; var t=b.textContent; b.click(); return t;})()" % NAME)
        page.eval("new Promise(r=>setTimeout(()=>r(1), 2000))")
        try:
            saved2 = json.load(open(sched_path, encoding="utf-8"))
            mine2 = next((t for t in (saved2.get("tasks") or []) if t.get("name") == NAME), {})
            rec(mine2.get("enabled") is False, "행의 '끄기' 가 파일에 반영된다",
                "버튼=%s → enabled=%s" % (clicked, mine2.get("enabled")))
        except Exception as e:
            rec(False, "행의 '끄기' 가 파일에 반영된다", str(e)[:80])
        # ---- 다시 켜기 → 되돌아온다 ----
        page.eval("(function(){var b=document.querySelector('#sch-table [data-tog=\"%s\"]');"
                  " if(b) b.click(); return 'ok';})()" % NAME)
        page.eval("new Promise(r=>setTimeout(()=>r(1), 2000))")
        try:
            saved3 = json.load(open(sched_path, encoding="utf-8"))
            mine3 = next((t for t in (saved3.get("tasks") or []) if t.get("name") == NAME), {})
            rec(mine3.get("enabled") is not False, "'켜기' 로 되돌아온다", "enabled=%s" % mine3.get("enabled"))
        except Exception as e:
            rec(False, "'켜기' 로 되돌아온다", str(e)[:80])
        # ---- 삭제 (뒷정리 겸) ----
        page.eval("window.confirm = function(){ return true; }; 'ok'")
        page.eval("(function(){var b=document.querySelector('#sch-table [data-del=\"%s\"]');"
                  " if(b) b.click(); return 'ok';})()" % NAME)
        gone = wait_for(page, "document.querySelector('#sch-table').innerHTML.indexOf(%s) < 0" % js(NAME), 20)
        rec(gone, "삭제하면 표에서 사라진다")

        # ---- 잘못된 입력은 막는다 ----
        page.eval("(function(){document.getElementById('btn-sch-add').click(); return 'ok';})()")
        page.eval("new Promise(r=>setTimeout(()=>r(1), 700))")
        page.eval("window.__lwReset(); 'ok'")
        page.eval("(function(){document.querySelector('#sch-name').value='';"
                  " document.getElementById('btn-sch-save').click(); return 'ok';})()")
        page.eval("new Promise(r=>setTimeout(()=>r(1), 1200))")
        rec((page.eval("__lw.toasts.join(' ').indexOf('이름') >= 0") is True),
            "이름 없이 저장하면 안내가 뜬다", (page.eval("__lw.toasts.slice(0,1)") or [""])[0])

        errs = page.eval("__lw.errs.slice(0,2)") or []
        rec(not errs, "JS 오류 없음", str(errs[:1]))

        bad = [r for r in ROWS if not r["ok"]]
        print("\n검사 %d개 중 %d개 통과 · 실패 %d개" % (len(ROWS), len(ROWS) - len(bad), len(bad)))
        for r in bad:
            print("  FAIL %-44s %s" % (r["name"], r["detail"]))
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
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
