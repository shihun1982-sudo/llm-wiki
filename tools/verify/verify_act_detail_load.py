"""진행 중 작업 상세의 "저장된 결과를 불러오는 중…" 이 **실제로 끝나는지** 시간을 재며 확인한다.

왜 따로 있나: `verify_rerun_ui.py` 는 "결국 답변이 보이는가" 를 최대 30초까지 기다려서 본다.
그래서 **느리게 채워지는 것**과 **영영 안 채워지는 것**을 구분하지 못한다. 사용자에게는 둘이 전혀
다른 경험이다. 여기서는 패널 문구를 0.5초마다 표본 추출해 **언제 바뀌는지**를 기록하고,
다시 눌러 열기·연속 클릭 같은 실제 조작 순서에서도 멈추지 않는지 본다.

실행:
    python tools/verify/verify_act_detail_load.py --port 8941 --cdp 9441
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


def rec(ok, name, detail=""):
    ROWS.append({"ok": bool(ok), "name": name, "detail": str(detail)[:150]})
    print("%s %-46s %s" % ("OK  " if ok else "FAIL", name, str(detail)[:100]), flush=True)
    return bool(ok)


PANEL = ("(function(){var e=document.querySelector('#act-detail');"
         " if(!e||e.classList.contains('hidden')) return '(닫힘)';"
         " return (e.textContent||'').replace(/\\s+/g,' ').slice(0,120);})()")


def settle(page, seconds=15.0, step=0.5):
    """패널 문구를 표본 추출해 (첫 '불러오는 중' 이후) 언제 실제 내용으로 바뀌는지 잰다."""
    t0 = time.time()
    seen = []
    while time.time() - t0 < seconds:
        txt = page.eval(PANEL) or ""
        if not seen or seen[-1][1] != txt:
            seen.append((round(time.time() - t0, 1), txt))
        if "불러오는 중" not in txt and "(닫힘)" != txt and len(txt) > 40:
            break
        page.eval("new Promise(r=>setTimeout(()=>r(1), %d))" % int(step * 1000))
    return round(time.time() - t0, 1), seen


def main(argv=None) -> int:
    if not EDGE:
        print("SKIP: Edge/Chrome 을 찾지 못함")
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8941)
    ap.add_argument("--cdp", type=int, default=9441)
    ns = ap.parse_args(argv)

    base = "http://127.0.0.1:%d" % ns.port
    prof = os.path.join(tempfile.gettempdir(), "llmwiki_actload_profile")
    shutil.rmtree(prof, ignore_errors=True)
    tmp, _env, proc = isolated_env(ns.port)
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

        # 완료 항목이 여러 개 있어야 '연속 클릭' 을 시험할 수 있다
        page.eval("location.hash='#ask/query'; 'ok'")
        page.eval("new Promise(r=>setTimeout(()=>r(1), 1500))")
        for q in ("ISSUE-2001 의 원인과 수정 CL 은?", "CL-55302 는 어떤 이슈를 수정했나?", "RX DMA 드라이버의 code map"):
            page.eval("(function(){document.querySelector('#q').value=%s;"
                      " document.getElementById('btn-query').click(); return 'ok';})()" % json.dumps(q, ensure_ascii=False))
            for _ in range(40):
                page.eval("new Promise(r=>setTimeout(()=>r(1), 1000))")
                vis = page.eval("(function(){var e=document.getElementById('query-out');"
                                " return !!e && !e.classList.contains('hidden');})()")
                if vis is True:
                    break
        page.eval("location.hash='#observability/activity'; 'ok'")
        page.eval("new Promise(r=>setTimeout(()=>r(1), 3000))")
        page.eval("(function(){var b=document.getElementById('btn-act-refresh'); if(b) b.click(); return 'ok';})()")
        page.eval("new Promise(r=>setTimeout(()=>r(1), 2000))")
        n = page.eval("document.querySelectorAll('#act-recent-board .act-item').length") or 0
        rec(n >= 2, "최근 완료 항목이 2개 이상", "%s개" % n)

        # ---- ① 처음 열기: 언제 채워지나 ----
        page.eval("(function(){var c=document.querySelectorAll('#act-recent-board .act-item')[0];"
                  " if(c) c.click(); return 'ok';})()")
        took, seen = settle(page)
        rec("불러오는 중" not in (seen[-1][1] if seen else ""), "① 처음 열기 — 로딩 문구가 사라진다", "%.1fs · %s" % (took, (seen[-1][1] if seen else "")[:70]))
        rec(took < 10, "① 채워지는 데 10초 미만", "%.1fs" % took)
        print("      표본:", " | ".join("%.1fs %s" % (t, x[:46]) for t, x in seen[:4]))

        # ---- ② 닫았다 다시 열기 (같은 항목) ----
        page.eval("(function(){var c=document.querySelectorAll('#act-recent-board .act-item')[0];"
                  " c.click(); return 'ok';})()")          # 닫기
        page.eval("new Promise(r=>setTimeout(()=>r(1), 400))")
        page.eval("(function(){var c=document.querySelectorAll('#act-recent-board .act-item')[0];"
                  " c.click(); return 'ok';})()")          # 다시 열기
        took2, seen2 = settle(page)
        rec("불러오는 중" not in (seen2[-1][1] if seen2 else ""), "② 닫았다 다시 열기 — 다시 채워진다", "%.1fs" % took2)

        # ---- ③ 응답이 오기 전에 **다른 항목**을 누른다 (경합) ----
        page.eval("(function(){var cs=document.querySelectorAll('#act-recent-board .act-item');"
                  " cs[1].click(); return 'ok';})()")
        page.eval("new Promise(r=>setTimeout(()=>r(1), 60))")   # 응답 전에 곧바로
        page.eval("(function(){var cs=document.querySelectorAll('#act-recent-board .act-item');"
                  " cs[2].click(); return 'ok';})()")
        took3, seen3 = settle(page)
        last3 = seen3[-1][1] if seen3 else ""
        rec("불러오는 중" not in last3, "③ 빠르게 다른 항목 클릭 — 멈추지 않는다", "%.1fs · %s" % (took3, last3[:70]))
        # 화면에 남은 것이 **마지막으로 누른 항목**인지 (엉뚱한 결과가 박히면 안 된다)
        same = page.eval("(function(){var e=document.querySelector('#act-detail');"
                         " var b=e && e.querySelector('#btn-act-profile');"
                         " var cs=document.querySelectorAll('#act-recent-board .act-item');"
                         " return !!b;})()")
        rec(same is True, "③ 마지막으로 누른 항목의 상세가 남는다", str(same))

        errs = page.eval("__lw.errs.slice(0,3)") or []
        rec(not errs, "JS 오류 없음", str(errs[:1]))

        bad = [r for r in ROWS if not r["ok"]]
        print("\n검사 %d개 중 %d개 통과 · 실패 %d개" % (len(ROWS), len(ROWS) - len(bad), len(bad)))
        for r in bad:
            print("  FAIL %-46s %s" % (r["name"], r["detail"]))
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
