"""단계 재실행(⟲)과 진행 중 작업 상세를 **실제 브라우저에서** 눌러 확인한다.

두 기능 모두 "눌렀을 때 무엇이 보이는가" 가 핵심이라 렌더 검사나 API 검사로는 부족하다.

  A. 워터폴의 ⟲ → 재실행 창 → 실행 → 결과가 다시 그려지고 앞 단계는 '재생' 으로 표시되는가
  B. 진행 중 작업 탭의 **최근 완료** 항목 클릭 → 저장해 둔 답변이 그 자리에 보이는가,
     'Ask 화면에서 열기' 가 다시 실행하지 않고 그때 결과를 복원하는가

실행:
    python tools/verify/verify_rerun_ui.py
    python tools/verify/verify_rerun_ui.py --port 8845 --cdp 9365
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

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Runner:
    def __init__(self, page):
        self.page, self.rows = page, []

    def step(self, name, prep, click, expect, note=""):
        p = self.page
        p.eval("window.__lwReset(); 'ok'")
        if prep:
            r = p.eval(prep + "; 'ok'")
            if isinstance(r, dict) and r.get("__error__"):
                return self._rec("FAIL", name, "준비 JS 오류 " + str(r["__error__"])[:100])
        if click:
            c = p.eval(click)
            if isinstance(c, dict) and c.get("__error__"):
                return self._rec("FAIL", name, "클릭 JS 오류 " + str(c["__error__"])[:100])
            if c in ("no-el", "disabled"):
                return self._rec("FAIL", name, "대상 없음/비활성 (%s)" % c)
        # 재실행은 LLM(mock)까지 도므로 조건이 만족될 때까지 최대 30초 기다린다
        ok = None
        for _ in range(30):
            p.eval("new Promise(r=>setTimeout(()=>r(1), 1000))")
            ok = p.eval(expect) if expect else True
            if ok is True:
                break
        errs = p.eval("__lw.errs.slice(0,2)") or []
        if errs:
            return self._rec("FAIL", name, "JS 오류 " + str(errs[0])[:100])
        if ok is not True:
            why = "기대 불충족"
            if isinstance(ok, dict) and ok.get("__error__"):
                why = "기대식 오류 " + str(ok["__error__"])[:80]
            return self._rec("FAIL", name, why + " :: " + str(expect)[:80])
        return self._rec("OK  ", name, note)

    def _rec(self, verdict, name, why):
        self.rows.append((verdict, name, why))
        print("%s %-44s %s" % (verdict, name, why))
        return verdict == "OK  "


def main(argv=None) -> int:
    if not EDGE:
        print("RERUN-UI SKIP: Edge/Chrome 을 찾지 못함 (LLMWIKI_BROWSER 로 지정)")
        return 0
    ap = argparse.ArgumentParser(description="단계 재실행 ⟲ 와 진행 중 작업 상세를 브라우저에서 확인")
    ap.add_argument("--port", type=int, default=8845)
    ap.add_argument("--cdp", type=int, default=9365)
    ap.add_argument("--keep", action="store_true")
    ns = ap.parse_args(argv)

    base = "http://127.0.0.1:%d" % ns.port
    prof = os.path.join(tempfile.gettempdir(), "llmwiki_rerunui_profile")
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
            print("RERUN-UI SKIP: 브라우저 디버깅 포트에 붙지 못했습니다")
            return 0
        page = Page(WS(url))
        page.eval("new Promise(r=>setTimeout(()=>r(1), 5000))")
        page.eval(SHIM)
        run = Runner(page)

        # ---- 질의 한 건 (이게 있어야 중간 결과와 '최근 완료' 항목이 생긴다) ----
        page.eval("location.hash='#ask/query'; 'ok'")
        page.eval("new Promise(r=>setTimeout(()=>r(1), 1500))")
        run.step("질의 실행", "document.querySelector('#q').value='ISSUE-2001 의 원인과 수정 CL 은?'",
                 "(function(){var b=document.getElementById('btn-query'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "!!document.querySelector('#query-out') && !document.querySelector('#query-out').classList.contains('hidden')")

        # ---- A. 워터폴의 ⟲ ----
        run.step("워터폴 단계에 ⟲ 가 붙는다", "", "",
                 "document.querySelectorAll('#trace .tr-rerun').length > 3",
                 note="재시작점이 있는 단계에만 (sync_index 같은 줄에는 없어야 한다)")
        run.step("되돌릴 수 없는 단계에는 ⟲ 가 없다", "", "",
                 "(function(){var rows=[].slice.call(document.querySelectorAll('#trace .tr-row'));"
                 " var r=rows.find(function(x){return (x.querySelector('.tr-name')||{}).textContent &&"
                 "   x.querySelector('.tr-name').textContent.indexOf('sync_index')>=0;});"
                 " return !r || !r.querySelector('.tr-rerun');})()")
        # 그냥 클릭은 **바로 실행**이어야 한다 (창이 뜨면 값 하나 바꿔 볼 때마다 단계가 하나 늘어난다)
        run.step("⟲ 클릭 → 창 없이 바로 실행", "",
                 "(function(){var b=document.querySelector('#trace .tr-rerun[data-rerun=\"answer_llm\"]')"
                 "  || document.querySelector('#trace .tr-rerun'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "!document.querySelector('#rerun-modal') && "
                 "(document.querySelector('#q-rerun-note')||{}).textContent.indexOf('재실행') >= 0")
        run.step("Shift+클릭 → 설정 창이 뜬다", "",
                 "(function(){var b=document.querySelector('#trace .tr-rerun[data-rerun=\"answer_llm\"]')"
                 "  || document.querySelector('#trace .tr-rerun'); if(!b) return 'no-el';"
                 " b.dispatchEvent(new MouseEvent('click',{bubbles:true,shiftKey:true})); return 'clicked';})()",
                 "!!document.querySelector('#rerun-modal') && document.querySelectorAll('#rr-point option').length >= 9")
        # 창이 **제대로 그려지는가**: 예전에 `.modal` 이름이 게시 대화상자의 덮개와 충돌해
        # 카드에 position:fixed/display:flex 가 붙어 내용이 가로로 흩어져 겹쳐 보였다.
        run.step("창 레이아웃이 깨지지 않는다 (덮개/카드 역할 분리)", "", "",
                 "(function(){var c=document.querySelector('#rerun-modal .modal'); if(!c) return false;"
                 " var s=getComputedStyle(c), r=c.getBoundingClientRect();"
                 " return s.position!=='fixed' && s.display==='block' && r.width<=560 && r.width>=300 && r.height<window.innerHeight;})()")
        run.step("창 안의 입력칸이 카드 너비를 채운다", "", "",
                 "(function(){var t=document.querySelector('#rr-ov'), c=document.querySelector('#rerun-modal .modal');"
                 " if(!t||!c) return false; return t.getBoundingClientRect().width > c.getBoundingClientRect().width*0.7;})()")
        run.step("창에서 설정을 적고 실행",
                 "document.querySelector('#rr-point').value='answer_llm'; document.querySelector('#rr-ov').value='{\"claim_check\": false}'",
                 "(function(){var b=document.getElementById('rr-go'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "!document.querySelector('#rerun-modal')",
                 note="창이 닫히면 실행이 끝난 것")
        run.step("결과 화면에 재실행 표시", "", "",
                 "(document.querySelector('#q-rerun-note')||{}).textContent.indexOf('재실행') >= 0")
        run.step("앞 단계가 '재생' 으로 표시된다", "", "",
                 "document.querySelectorAll('#trace .tr-row.replayed').length >= 5")
        run.step("답변 단계는 재생이 아니라 다시 계산됐다", "", "",
                 "(function(){var rows=[].slice.call(document.querySelectorAll('#trace .tr-row'));"
                 " var r=rows.find(function(x){var n=x.querySelector('.tr-name');"
                 "   return n && n.textContent.indexOf('answer_llm')>=0;});"
                 " return !!r && !r.classList.contains('replayed');})()")
        run.step("바꾼 설정이 반영됐다 (claim_check 끔)", "", "",
                 "document.querySelector('#q-evidence').textContent.indexOf('claim 검증') < 0",
                 note="claim_check 를 껐으므로 검증 줄이 없어야 한다")

        # ---- B. 진행 중 작업 → 최근 완료 클릭 ----
        page.eval("location.hash='#observability/activity'; 'ok'")
        page.eval("new Promise(r=>setTimeout(()=>r(1), 2500))")
        run.step("최근 완료 목록이 그려진다", "",
                 "(function(){var b=document.getElementById('btn-act-refresh'); if(b) b.click(); return 'clicked';})()",
                 "document.querySelectorAll('#act-recent-board .act-item').length > 0")
        run.step("완료 항목을 누르면 상세가 열린다", "",
                 "(function(){var c=document.querySelector('#act-recent-board .act-item'); if(!c) return 'no-el';"
                 " c.click(); return 'clicked';})()",
                 "!!document.querySelector('#act-detail') && !document.querySelector('#act-detail').classList.contains('hidden')")
        run.step("저장해 둔 답변이 그 자리에 보인다", "", "",
                 "!!document.querySelector('#act-answer') && document.querySelector('#act-answer').textContent.length > 20",
                 note="목록에서 누른 이유는 대개 '그래서 뭐라고 답했지?' 다")
        # 끝난 작업은 더 볼 게 없으므로 폴링을 멈춰야 한다. 멈추지 않으면 1.5초마다 화면을 다시 그려
        # '전체 보기' 로 펼친 답변이 곧바로 도로 접힌다 (사용자가 겪은 증상).
        run.step("완료 항목은 폴링을 멈춘다", "window.__lwReset()",
                 "new Promise(r=>setTimeout(()=>r('waited'), 5000))",
                 "__lw.fetches.filter(function(u){return String(u).indexOf('/api/progress')>=0;}).length === 0",
                 note="5초 동안 진행 조회가 한 번도 없어야 한다")
        run.step("'전체 보기' 로 펼치면 펼친 채로 남는다", "",
                 "(function(){var b=document.getElementById('btn-act-more');"
                 " if(!b) return 'skip'; b.click(); return 'clicked';})()",
                 "(function(){var a=document.querySelector('#act-answer');"
                 " return !a || !a.classList.contains('clip');})()",
                 note="답변이 짧아 '전체 보기' 가 없으면 그대로 통과")
        run.step("'Ask 화면에서 열기' 로 그때 결과를 복원", "",
                 "(function(){var b=document.getElementById('btn-act-ask'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "location.hash.indexOf('ask/query') >= 0 && document.querySelector('#answer').textContent.length > 20")
        run.step("복원은 **다시 실행하지 않는다**", "", "",
                 "__lw.fetches.filter(function(u){return String(u).indexOf('/api/query')===0;}).length === 0",
                 note="저장된 결과를 그리는 것이지 질의를 다시 던지는 것이 아니다")

        # ---- B2. 왼쪽 사이드바: 품질/속도/토큰 배지와 축 필터 ----
        page.eval("location.hash='#ask/query'; 'ok'")
        page.eval("new Promise(r=>setTimeout(()=>r(1), 1200))")
        run.step("토글마다 효과 배지가 붙는다", "", "",
                 "document.querySelectorAll('#toggle-groups label[data-t] .fxs').length >= 60",
                 note="켜면 무엇이 좋아지고 무엇을 내주는지")
        run.step("묶음마다 단계 배지와 한 줄 설명", "", "",
                 "document.querySelectorAll('#toggle-groups .tg-stage').length >= 5 && "
                 "document.querySelectorAll('#toggle-groups .tg-hint').length >= 5")
        run.step("'기타' 로 밀린 토글이 없다", "", "",
                 "!document.querySelector('#toggle-groups .toggle-group[data-g=\"rest\"]')",
                 note="모든 토글이 제 묶음에 들어가 있어야 한다")
        run.step("품질↑ 필터가 실제로 걸러 낸다",
                 "", "(function(){var b=document.querySelector('#fx-filter [data-fx=\"q\"]'); if(!b) return 'no-el'; b.click(); return 'ok';})()",
                 "(function(){var all=document.querySelectorAll('#toggle-groups label[data-t]').length;"
                 " var vis=[].slice.call(document.querySelectorAll('#toggle-groups label[data-t]'))"
                 "   .filter(function(l){return !l.classList.contains('fx-hidden');}).length;"
                 " return vis > 0 && vis < all;})()")
        run.step("걸러진 것은 모두 품질이 좋아지는 토글", "", "",
                 "[].slice.call(document.querySelectorAll('#toggle-groups label[data-t]'))"
                 " .filter(function(l){return !l.classList.contains('fx-hidden');})"
                 " .every(function(l){return !!l.querySelector('.fx.up');})")
        run.step("이름 검색이 함께 걸린다",
                 "(function(){var s=document.getElementById('fx-search'); s.value='claim';"
                 " s.dispatchEvent(new Event('input',{bubbles:true})); return 'ok';})()", "",
                 "[].slice.call(document.querySelectorAll('#toggle-groups label[data-t]'))"
                 " .filter(function(l){return !l.classList.contains('fx-hidden');})"
                 " .every(function(l){return l.dataset.t.indexOf('claim') >= 0;})")
        run.step("'전체' 로 되돌리면 다 보인다",
                 "(function(){var s=document.getElementById('fx-search'); s.value='';"
                 " s.dispatchEvent(new Event('input',{bubbles:true})); return 'ok';})()",
                 "(function(){document.querySelector('#fx-filter [data-fx=\"\"]').click(); return 'ok';})()",
                 "[].slice.call(document.querySelectorAll('#toggle-groups label[data-t]'))"
                 " .every(function(l){return !l.classList.contains('fx-hidden');})")

        # ---- C. 게시 대화상자도 같은 이름 충돌에 걸려 있었다 → 함께 확인 ----
        run.step("게시 대화상자가 화면 가운데 덮개로 뜬다",
                 "location.hash='#ask/query'",
                 "(function(){var b=document.getElementById('btn-chat-board'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "(function(){var o=document.getElementById('post-modal'); if(!o||o.classList.contains('hidden')) return false;"
                 " var s=getComputedStyle(o); var box=o.querySelector('.modal-box');"
                 " return s.position==='fixed' && !!box && box.getBoundingClientRect().width>300;})()",
                 note="덮개는 .modal-overlay, 카드는 .modal-box — 역할이 섞이면 안 된다")

        bad = [r for r in run.rows if r[0] != "OK  "]
        print("\n검사 %d개 중 %d개 통과 · 실패 %d개" % (len(run.rows), len(run.rows) - len(bad), len(bad)))
        for r in bad:
            print("  FAIL %-44s %s" % (r[1], r[2]))
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
