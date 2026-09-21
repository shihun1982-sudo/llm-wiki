"""창 크기 대응(반응형) 검증 — 좁은 화면에서 화면이 잘리거나 가로로 새어 나가지 않는가 (2026-09-18, 요청 4).

왜 있나: 사무실 모니터에서만 보고 만들면 노트북·분할 화면·태블릿에서 표가 잘리거나 사이드바가 본문을 밀어내
가로 스크롤이 생긴다. 눈으로는 매번 확인하기 어렵고, 한 번 고쳐도 다음 회차에 되돌아간다.

무엇을 보나 — 폭 5종(360 · 768 · 1024 · 1366 · 1920)에서 탭마다:
  1. **가로 넘침 없음** — `documentElement.scrollWidth <= innerWidth + 1`
  2. **화면 밖으로 나간 요소 없음** — 보이는 요소의 `right > innerWidth + 1` 인 것의 수(툴팁·숨김 요소 제외)
  3. **콘솔 오류 0**
  4. 960px 미만에서 사이드바가 본문 위로 접혀 본문이 화면 폭을 차지하는가(레이아웃이 바뀌는가)

Edge/Chrome 을 headless 로 띄우고 `--window-size` 로 폭을 바꾸며 `--dump-dom` 한다. DOM 을 받기 전에
페이지가 자기 검사 결과를 `<html data-rsp="…">` 에 적도록 해시(`#rsp`)로 진입해 JS 를 돌린다 —
CDP 없이 표준 라이브러리만으로 측정값을 가져오기 위한 방법이다.

실행:  python tools/verify/verify_responsive.py [--port 8795] [--widths 360,768,1024,1366,1920]
결과:  `검사 N개 중 M개 통과` + 폭×탭 표, `verify_responsive_result.json`, 실패 시 exit 1
문서:  docs/WEB_UI.md
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

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_responsive_result.json")
EDGE = next((p for p in (os.environ.get("LLMWIKI_BROWSER", ""), shutil.which("msedge"), shutil.which("chrome"),
                         shutil.which("google-chrome"), shutil.which("chromium"),
                         r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files\Google\Chrome\Application\chrome.exe") if p and os.path.exists(p)), None)
PROF = os.path.join(tempfile.gettempdir(), "llmwiki_verify_responsive_profile")

# 페이지에 주입할 자기 검사 스크립트. 결과를 <html data-rsp="{json}"> 에 적는다 (dump-dom 으로 회수).
PROBE = """
(function(){
  function run(){
    try{
      var de = document.documentElement, W = window.innerWidth;
      var over = [], n = 0;
      // 가로 스크롤이 **허용된** 조상(.tbl-wrap{overflow:auto} 같은) 안의 넓은 표는 정상 패턴이다 —
      // 페이지가 새는 것이 아니라 그 칸 안에서만 밀리므로 넘침으로 세지 않는다.
      function inScrollable(e){
        for (var p = e.parentElement; p && p !== document.body; p = p.parentElement){
          var ox = getComputedStyle(p).overflowX;
          if (ox === 'auto' || ox === 'scroll') return true;
        }
        return false;
      }
      var els = document.querySelectorAll('body *');
      for (var i=0;i<els.length;i++){
        var e = els[i];
        var cs = getComputedStyle(e);
        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.position === 'fixed') continue;
        if (!e.offsetParent && cs.position !== 'static') continue;
        var r = e.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        if (r.right > W + 1 && !inScrollable(e)){
          n++;
          if (over.length < 6) over.push((e.tagName.toLowerCase()) + (e.id ? '#'+e.id : '') +
            (e.className && typeof e.className === 'string' ? '.'+e.className.trim().split(/\\s+/).slice(0,2).join('.') : '') +
            ' right=' + Math.round(r.right));
        }
      }
      // 실제로 **가로로 스크롤되는 칸**의 수. 페이지 넘침(0)과는 다른 문제다 — 칸마다 스크롤바가 생기면
      // 사용자는 "횡 스크롤이 너무 많다" 고 느낀다 (2026-09-19 보고). 표 하나가 넓어 스크롤하는 것은 정상이지만
      // 한 화면에 여러 개면 레이아웃이 잘못된 것이다.
      var scr = [], nscr = 0;
      for (var j=0;j<els.length;j++){
        var e2 = els[j], cs2 = getComputedStyle(e2);
        if (cs2.display === 'none' || cs2.visibility === 'hidden') continue;
        if (!(cs2.overflowX === 'auto' || cs2.overflowX === 'scroll')) continue;
        if (e2.scrollWidth > e2.clientWidth + 1 && e2.clientWidth > 0){
          nscr++;
          if (scr.length < 8) scr.push((e2.tagName.toLowerCase()) + (e2.id ? '#'+e2.id : '') +
            (e2.className && typeof e2.className === 'string' ? '.'+e2.className.trim().split(/\\s+/).slice(0,2).join('.') : '') +
            ' ' + e2.scrollWidth + '>' + e2.clientWidth);
        }
      }
      // 표 머리와 본문이 **같은 표**로 그려지는가 (2026-09-19).
      // 전역 규칙 `table>tbody{display:table}` 은 넓은 표가 스스로 스크롤하게 하려는 것인데,
      // .tbl-wrap 안의 표에서 <thead> 와 만나면 tbody 가 **중첩 표**가 되어 머리는 전체 폭에 퍼지고
      // 본문은 왼쪽에 뭉쳐 글자가 세로로 한 자씩 쌓였다 ('자동 매핑 제안' 표에서 발견).
      // 첫 본문 행의 칸이 같은 열 머리와 어긋나면 그 증상이다.
      // 문제의 표들(자동 매핑 제안·규칙 효과·카탈로그 테스트)은 **버튼을 눌러야** 그려지므로
      // 기본 탭 스캔만으로는 이 검사가 헛돈다. 그래서 같은 모양의 **대조 표본**을 잠깐 심어
      // CSS 규약 자체를 매번 확인한다 (측정이 끝나면 지운다).
      var probe = document.createElement('div');
      probe.id = '__rsp_tbl_probe';
      probe.innerHTML = '<div class="tbl-wrap"><table class="tbl"><thead><tr><th style="width:24px"></th>' +
        '<th>역할</th><th>지금</th><th>제안</th><th>이유</th></tr></thead><tbody><tr>' +
        '<td><input type="checkbox"></td><td><b>answer</b></td>' +
        '<td class="mono small muted">anthropic/claude-sonnet-5</td>' +
        '<td class="mono small ok">ollama/llama3.1</td>' +
        '<td>모든 역할에 쓸 수 있는 모델 (roles=*)</td></tr></tbody></table></div>';
      probe.style.cssText = 'position:absolute;left:0;top:0;width:100%;visibility:hidden';
      (document.querySelector('main') || document.body).appendChild(probe);

      var tsplit = [], ntsplit = 0;
      var wraps = document.querySelectorAll('.tbl-wrap > table');
      for (var w=0; w<wraps.length; w++){
        var tb = wraps[w];
        if (getComputedStyle(tb).display === 'none') continue;
        var ths = tb.querySelectorAll(':scope > thead > tr > th');
        var tds = tb.querySelectorAll(':scope > tbody > tr:first-child > td');
        if (!ths.length || ths.length !== tds.length) continue;
        for (var c=0; c<ths.length; c++){
          var a = ths[c].getBoundingClientRect(), b = tds[c].getBoundingClientRect();
          if (Math.abs(a.left - b.left) > 4 || Math.abs(a.width - b.width) > 4){
            ntsplit++;
            if (tsplit.length < 6) tsplit.push((tb.className || 'table') + ' 열' + c +
              ' th[' + Math.round(a.left) + ',' + Math.round(a.width) + '] td[' +
              Math.round(b.left) + ',' + Math.round(b.width) + ']');
            break;
          }
        }
      }
      probe.remove();     // 대조 표본은 측정에만 쓰고 지운다 (넘침·스크롤 집계에 섞이지 않게 위에서 이미 끝났다)
      var main = document.querySelector('main') || document.querySelector('#main') || document.body;
      var mr = main.getBoundingClientRect();
      var sb = document.querySelector('#sidebar');
      var sbr = sb ? sb.getBoundingClientRect() : null;
      de.setAttribute('data-rsp', JSON.stringify({
        w: W, scrollWidth: de.scrollWidth, docOverflow: de.scrollWidth > W + 1,
        overflowCount: n, overflow: over, scrollerCount: nscr, scrollers: scr,
        tableSplitCount: ntsplit, tableSplit: tsplit,
        mainWidth: Math.round(mr.width), mainRatio: +(mr.width / W).toFixed(2),
        sidebarTop: sbr ? Math.round(sbr.top) : null, sidebarWidth: sbr ? Math.round(sbr.width) : null,
        stacked: sbr ? (mr.top >= sbr.bottom - 2 || sbr.width > W * 0.9) : null
      }));
    }catch(e){ document.documentElement.setAttribute('data-rsp', JSON.stringify({error: String(e)})); }
  }
  if (document.readyState === 'complete') setTimeout(run, 400); else window.addEventListener('load', function(){ setTimeout(run, 400); });
})();
"""


def shot(url, width, height=900, budget=8000):
    """headless 로 폭 width 에서 열고 (DOM, 콘솔오류) 를 돌려준다. PROBE 는 서버가 주입한다(아래 --probe)."""
    r = subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--user-data-dir=" + PROF,
                        "--enable-logging=stderr", "--v=0", "--hide-scrollbars",
                        "--window-size=%d,%d" % (width, height),
                        "--virtual-time-budget=%d" % budget, "--dump-dom", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    dom, log = r.stdout, r.stderr
    errs = [l.strip()[:220] for l in log.splitlines()
            if "CONSOLE" in l and ("Uncaught" in l or "ERROR" in l or re.search(r"\berror\b", l, re.I))
            and "favicon" not in l and "Password field is not contained in a form" not in l]
    return dom, errs


def probe_of(dom):
    m = re.search(r'data-rsp="([^"]*)"', dom)
    if not m:
        return None
    raw = m.group(1).replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    try:
        return json.loads(raw)
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Web UI 창 크기 대응 검증")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--widths", default="360,768,1024,1366,1920")
    ap.add_argument("--tabs", default="", help="쉼표로 구분한 group/tab (기본: 대표 탭 6종)")
    ap.add_argument("--max-scrollers", type=int, default=2,
                    help="한 화면에 허용할 **가로 스크롤 칸** 수 (넓은 표 하나가 스크롤하는 것은 정상, 여러 개면 레이아웃 문제)")
    ns = ap.parse_args(argv)
    if not EDGE:
        print("RESPONSIVE SKIP: Edge/Chrome 을 찾지 못함 (LLMWIKI_BROWSER=<실행 파일> 로 지정)")
        return 0
    widths = [int(x) for x in ns.widths.split(",") if x.strip()]
    # 대표 탭: 사이드바가 있는 Ask, 넓은 표(모델·보안·서버), 3열 그리드(Pipeline), 긴 목록(요청)
    tabs = [t for t in (ns.tabs.split(",") if ns.tabs else
                        ["ask/query", "pipeline/pipeline", "settings/models", "settings/security",
                         "observability/requests", "observability/server"]) if t.strip()]

    # 정적 파일에 PROBE 를 잠시 주입한다 (검사 뒤 원상 복구) — 서버 코드를 건드리지 않고 측정값을 얻는 가장 작은 방법.
    idx = os.path.join(ROOT, "llmwiki", "web", "static", "index.html")
    orig = open(idx, encoding="utf-8").read()
    if "data-rsp" in orig:
        print("index.html 에 이미 probe 가 있습니다 — 이전 실행이 비정상 종료했을 수 있습니다. 확인 후 다시 실행하세요.")
        return 1
    patched = orig.replace("</body>", "<script>%s</script>\n</body>" % PROBE)
    rows = []
    proc = None
    try:
        with open(idx, "w", encoding="utf-8") as f:
            f.write(patched)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        proc = subprocess.Popen([sys.executable, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(ns.port)],
                                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = "http://127.0.0.1:%d" % ns.port
        for _ in range(90):
            try:
                urllib.request.urlopen(base + "/api/auth/me", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        print("폭 %s × 탭 %d종 확인 중…" % (widths, len(tabs)))
        for w in widths:
            for t in tabs:
                dom, errs = shot("%s/#%s" % (base, t), w)
                p = probe_of(dom)
                if p is None:
                    rows.append({"width": w, "tab": t, "ok": False, "why": "probe 결과 없음 (렌더 실패?)", "errors": errs[:2]})
                elif p.get("error"):
                    rows.append({"width": w, "tab": t, "ok": False, "why": "probe 오류: %s" % p["error"], "errors": errs[:2]})
                else:
                    why = []
                    if p.get("docOverflow"):
                        why.append("가로 넘침 scrollWidth=%s > %s" % (p.get("scrollWidth"), p.get("w")))
                    if p.get("overflowCount"):
                        why.append("화면 밖 요소 %d개: %s" % (p["overflowCount"], "; ".join(p.get("overflow") or [])[:160]))
                    if errs:
                        why.append("콘솔 오류: " + errs[0][:100])
                    if w < 960 and p.get("stacked") is False:
                        why.append("좁은 폭인데 사이드바가 옆에 남아 본문이 %d%%" % int(100 * (p.get("mainRatio") or 0)))
                    if (p.get("scrollerCount") or 0) > ns.max_scrollers:
                        why.append("가로 스크롤 칸 %d개(상한 %d): %s" % (p["scrollerCount"], ns.max_scrollers,
                                                                  "; ".join(p.get("scrollers") or [])[:160]))
                    if p.get("tableSplitCount"):
                        # 표 머리와 본문이 다른 표로 그려짐 — 글자가 세로로 쌓여 읽을 수 없게 된다
                        why.append("표 머리/본문 어긋남 %d개: %s" % (p["tableSplitCount"],
                                                              "; ".join(p.get("tableSplit") or [])[:200]))
                    rows.append(dict(p, width=w, tab=t, ok=not why, why=" · ".join(why), errors=errs[:2]))
                r = rows[-1]
                print("  %s %5dpx %-24s %s" % ("OK  " if r["ok"] else "FAIL", w, t, r.get("why") or ""))
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except Exception:
                proc.kill()
        with open(idx, "w", encoding="utf-8") as f:     # probe 원상 복구 (실패해도 반드시)
            f.write(orig)

    bad = [r for r in rows if not r["ok"]]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"ts": time.strftime("%Y-%m-%d %H:%M"), "widths": widths, "tabs": tabs,
                   "total": len(rows), "failed": len(bad), "rows": rows}, f, ensure_ascii=False, indent=1)
    print("\n" + "=" * 90)
    print("| 폭 | 탭 | scrollWidth | 화면 밖 | 가로스크롤 칸 | 본문 비율 | 결과 |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        print("| %d | %s | %s | %s | %s | %s | %s |" % (r["width"], r["tab"], r.get("scrollWidth", "-"),
                                                        r.get("overflowCount", "-"), r.get("scrollerCount", "-"),
                                                        r.get("mainRatio", "-"),
                                                        "OK" if r["ok"] else "**실패** " + (r.get("why") or "")[:60]))
    print("=" * 90)
    print("결과: %s" % OUT)
    print("\n검사 %d개 중 %d개 통과" % (len(rows), len(rows) - len(bad)))
    for r in bad:
        print("  FAIL %dpx %s — %s" % (r["width"], r["tab"], (r.get("why") or "")[:180]))
    print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
