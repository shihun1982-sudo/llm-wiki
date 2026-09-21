"""워터폴(trace) 그림을 **실제 브라우저에서 픽셀로 재서** 읽을 수 있는지 확인한다 (2026-09-20).

왜 이 검사가 필요한가:
  "이전 단계의 끝에서 다음 단계의 시작이 이어지지 않는다" 는 신고가 있었다. 그런데 `offset_ms` 데이터는
  완벽했고(단계마다 0.1~1.5ms 간격으로 연속), 막대 기하도 정확했다. 문제는 **배지**였다.
    - `.tr-bar .cnt` 마다 `margin-left:auto` 가 걸려 있었는데, flex 는 auto 여백이 여러 개면
      남는 공간을 그 **사이에 나눈다**. 그래서 배지가 2개인 줄만 첫 배지가 타임라인과 무관한 가운데로 밀렸다.
    - 게다가 앙상블 배지가 막대와 **같은 파랑으로 꽉 채워져** 있어 막대 조각처럼 보였다.
  둘 다 DOM 검사·API 검사·버튼 검사를 모두 통과한다. **레이아웃을 재 봐야만** 잡힌다.

판정:
  1. 같은 깊이의 단계 막대가 앞 단계 끝에서 시작하는가 (겹침·틈 모두 허용오차 안)
  2. 막대 왼쪽 끝이 offset_ms 비율과 맞는가 (데이터 → 그림)
  3. 배지가 오른쪽에 **한 덩어리로** 붙어 있는가 (막대 자리로 끼어들지 않는가)
  4. 배지가 막대와 같은 색으로 채워져 있지 않은가 (막대로 오해되지 않는가)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_click import WS, Page, EDGE                      # noqa: E402
from verify_buttons import isolated_env                      # noqa: E402

PORT = int(os.environ.get("LLMWIKI_VERIFY_PORT", "8797"))
CDP = int(os.environ.get("LLMWIKI_VERIFY_CDP", "9397"))
TOL = 3.0          # px. 최소 폭 보정(0.4%) 때문에 아주 짧은 단계는 살짝 겹친다

# 실제 질의 한 번의 모양을 그대로 옮긴 합성 trace — 데이터에 기대지 않아 언제 돌려도 같은 결과가 나온다.
# 앙상블 배지 2개 + llm/sql 배지가 섞인 줄을 반드시 포함한다 (문제가 났던 조합).
TRACE = {
    "name": "query", "ms": 20000.0, "offset_ms": 0.0, "debug_level": 1, "run_id": "wf-test",
    "counters": {"llm_calls": 10, "llm_input_tokens": 15000, "llm_output_tokens": 1900, "sql": 31550},
    "children": [
        {"name": "router", "ms": 100.0, "offset_ms": 0.0, "counters": {"sql": 207}},
        {"name": "query_expand", "ms": 6000.0, "offset_ms": 100.0,
         "counters": {"llm_calls": 4, "llm_input_tokens": 2000, "llm_output_tokens": 200, "sql": 120},
         "meta": {"ensemble": {"role": "expand", "n_members": 3, "n_ok": 3, "aggregated": True,
                               "member_ms_max": 5800, "policy": "all",
                               "members": [{"model": "m1", "provider": "p", "ms": 5800, "ok": True},
                                           {"model": "m2", "provider": "p", "ms": 5100, "ok": True},
                                           {"model": "m3", "provider": "p", "ms": 4900, "ok": True}],
                               "aggregator": {"model": "agg", "provider": "p", "ms": 200}}}},
        {"name": "fts_search", "ms": 50.0, "offset_ms": 6100.0, "counters": {"sql": 2210}},
        {"name": "rerank_llm", "ms": 2500.0, "offset_ms": 6150.0,
         "counters": {"llm_calls": 1, "llm_input_tokens": 2000, "llm_output_tokens": 100}},
        {"name": "doc_expand", "ms": 2000.0, "offset_ms": 8650.0, "counters": {"sql": 5}},
        {"name": "answer_llm", "ms": 9000.0, "offset_ms": 10650.0,
         "counters": {"llm_calls": 4, "llm_input_tokens": 8000, "llm_output_tokens": 900},
         "meta": {"ensemble": {"role": "answer", "n_members": 3, "n_ok": 3, "aggregated": True,
                               "member_ms_max": 8100, "policy": "all",
                               "members": [{"model": "m1", "provider": "p", "ms": 8100, "ok": True},
                                           {"model": "m2", "provider": "p", "ms": 7400, "ok": True},
                                           {"model": "m3", "provider": "p", "ms": 6900, "ok": True}],
                               "aggregator": {"model": "agg", "provider": "p", "ms": 800}}}},
        {"name": "claim_check", "ms": 300.0, "offset_ms": 19650.0,
         "counters": {"llm_calls": 1, "llm_input_tokens": 3500, "llm_output_tokens": 200}},
        {"name": "evolve_capture", "ms": 50.0, "offset_ms": 19950.0, "counters": {"sql": 1}},
    ],
}

rows: list[tuple[str, str, str]] = []


def check(name: str, ok, why: str = "") -> None:
    rows.append(("OK  " if ok is True else "FAIL", name, "" if ok is True else (why or "기대와 다름: %r" % (ok,))))
    print("%s %-52s %s" % (rows[-1][0], name, rows[-1][2]))


def main() -> int:
    if not EDGE:
        print("WATERFALL SKIP: Edge/Chrome 을 찾지 못함 (LLMWIKI_BROWSER 로 지정)")
        return 0
    tmp, env, proc = isolated_env(PORT)
    br = None
    try:
        for _ in range(90):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/api/auth/me" % PORT, timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        prof = os.path.join(tmp, "profile")
        br = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
                               "--user-data-dir=" + prof, "--window-size=1280,900",
                               "--remote-debugging-port=%d" % CDP, "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws_url = ""
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % CDP, timeout=2))
                ws_url = next((t["webSocketDebuggerUrl"] for t in tabs if t.get("type") == "page"), "")
                if ws_url:
                    break
            except Exception:
                time.sleep(0.5)
        if not ws_url:
            print("WATERFALL SKIP: CDP 에 붙지 못함")
            return 0
        page = Page(WS(ws_url))
        page.eval("location.href=%r; 'ok'" % ("http://127.0.0.1:%d/" % PORT))
        for _ in range(40):
            time.sleep(0.5)
            if page.eval("!!(window.LW && LW.renderTrace)") is True:
                break
        check("LW.renderTrace 를 쓸 수 있다", page.eval("!!(window.LW && LW.renderTrace)"))
        page.eval("window.__tr = %s; 'ok'" % json.dumps(TRACE, ensure_ascii=False))
        n = page.eval("(function(){var h=document.createElement('div'); h.id='wf-test';"
                      "h.style.width='900px'; h.style.position='absolute'; h.style.left='0'; h.style.top='0';"
                      "document.body.appendChild(h); LW.renderTrace(h, window.__tr, {controls:false});"
                      "return document.querySelectorAll('#wf-test .tr-row').length;})()")
        check("워터폴이 %d줄로 그려진다" % (len(TRACE["children"]) + 1), n == len(TRACE["children"]) + 1, "줄=%r" % n)
        if n != len(TRACE["children"]) + 1:
            print("WATERFALL PROBLEMS")
            return 1

        geo = page.eval("""(function(){
          var out=[], rs=document.querySelectorAll('#wf-test .tr-row');
          for(var i=0;i<rs.length;i++){
            var box=rs[i].querySelector('.tr-bar'); if(!box) continue;
            var bb=box.getBoundingClientRect(), b=box.querySelector('i');
            var ib=b? b.getBoundingClientRect(): null;
            var cs=box.querySelectorAll('.cnt, .ens-txt'), cn=[];
            for(var k=0;k<cs.length;k++){var cb=cs[k].getBoundingClientRect(), st=getComputedStyle(cs[k]);
              cn.push({l:+(cb.left-bb.left).toFixed(2), r:+(cb.right-bb.left).toFixed(2),
                       bg:st.backgroundColor, bd:st.borderTopWidth, cls:cs[k].className});}
            out.push({name:(rs[i].querySelector('.tr-name')||{}).textContent||'',
                      boxW:+bb.width.toFixed(2),
                      l: ib? +(ib.left-bb.left).toFixed(2):null, w: ib? +ib.width.toFixed(2):null,
                      barBg: b? getComputedStyle(b).backgroundColor : null, cnts:cn});
          }
          return out;})()""")
        kids = geo[1:]                                   # 0번은 루트(query) 줄
        boxW = geo[0]["boxW"]
        total = TRACE["ms"]

        # ---- 1. 앞 단계 끝에서 다음 단계가 시작하는가 ----
        worst, worst_name = 0.0, ""
        prev_end = None
        for g, c in zip(kids, TRACE["children"]):
            if g["l"] is None:
                continue
            if prev_end is not None:
                d = abs(g["l"] - prev_end)
                if d > worst:
                    worst, worst_name = d, c["name"]
            prev_end = g["l"] + g["w"]
        check("단계 막대가 앞 단계 끝에서 시작한다 (허용 %.0fpx)" % TOL, worst <= TOL,
              "가장 큰 어긋남 %.2fpx (%s)" % (worst, worst_name))

        # ---- 2. 막대 위치가 offset_ms 와 맞는가 (데이터 → 그림) ----
        worst2, worst2_name = 0.0, ""
        for g, c in zip(kids, TRACE["children"]):
            if g["l"] is None:
                continue
            want = c["offset_ms"] / total * boxW
            d = abs(g["l"] - want)
            if d > worst2:
                worst2, worst2_name = d, c["name"]
        check("막대 왼쪽 끝이 offset_ms 비율과 맞는다", worst2 <= TOL,
              "가장 큰 차이 %.2fpx (%s)" % (worst2, worst2_name))

        # ---- 3. 배지가 오른쪽에 한 덩어리로 붙는가 ----
        # margin-left:auto 가 배지마다 걸리면 남는 공간이 배지 **사이**에 나뉘어 첫 배지가 가운데로 밀린다.
        gaps = []
        for g, c in zip(kids, TRACE["children"]):
            cn = g["cnts"]
            if len(cn) < 2:
                continue
            for a, b in zip(cn, cn[1:]):
                gaps.append((c["name"], round(b["l"] - a["r"], 2)))
        check("배지가 2개 이상인 줄이 있다 (검사가 헛돌지 않는다)", len(gaps) > 0, "배지쌍=%d" % len(gaps))
        bad_gap = [x for x in gaps if x[1] > 12]
        check("배지 사이가 벌어지지 않는다 (덩어리로 우측 정렬)", not bad_gap, "벌어진 곳=%r" % (bad_gap[:3],))
        far = [(c["name"], cn[-1]["r"], g["boxW"]) for g, c in zip(kids, TRACE["children"])
               for cn in [g["cnts"]] if cn and (g["boxW"] - cn[-1]["r"]) > 12]
        check("마지막 배지가 오른쪽 끝에 닿는다", not far, "떨어진 곳=%r" % (far[:3],))

        # ---- 4. 배지가 막대와 같은 색으로 채워져 있지 않은가 ----
        bar_bg = next((g["barBg"] for g in kids if g["barBg"]), "")
        same = [(c["name"], cn["cls"], cn["bg"]) for g, c in zip(kids, TRACE["children"])
                for cn in g["cnts"] if cn["bg"] == bar_bg]
        check("배지가 막대와 같은 색으로 채워져 있지 않다 (막대로 오해 방지)", not same,
              "막대색=%s · 같은 색 배지=%r" % (bar_bg, same[:3]))
        # ---- 5. 앙상블 표시는 **글씨만** (배경·테두리 없음) ----
        # 알약 모양이면 막대와 같은 줄에서 타임라인 위의 조각처럼 읽힌다.
        ens = [cn for g in kids for cn in g["cnts"] if "ens-txt" in cn["cls"]]
        check("앙상블 표시가 실제로 그려진다 (검사가 헛돌지 않는다)", len(ens) == 2, "앙상블 표시=%d" % len(ens))
        opaque = [e for e in ens if e["bg"] not in ("rgba(0, 0, 0, 0)", "transparent")]
        check("앙상블 표시의 배경이 투명하다", not opaque, "배경=%r" % ([e["bg"] for e in opaque][:2],))
        bordered = [e for e in ens if e["bd"] not in ("0px", "0")]
        check("앙상블 표시에 테두리가 없다", not bordered, "테두리=%r" % ([e["bd"] for e in bordered][:2],))

        bad = [r for r in rows if r[0] == "FAIL"]
        print("\n%d개 중 %d개 통과" % (len(rows), len(rows) - len(bad)))
        print("WATERFALL", "OK" if not bad else "PROBLEMS")
        return 0 if not bad else 1
    finally:
        for p in (br, proc):
            if p:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except Exception:
                    p.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
