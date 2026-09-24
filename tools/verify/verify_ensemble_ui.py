"""앙상블 편집기(🧭 Pipeline › 앙상블)를 **실제 브라우저로 눌러** 확인한다 (2026-09-20).

왜 이 검사가 따로 필요한가:
  사용자가 「사용」을 켰는데 멤버 칸이 전부 "(상속: 사용 안 함)" 인 채로 남아, 칸이 잠긴 줄 알고 막혔다.
  실제 규칙은 config.Settings.effective_ensemble 과 같다 — **모델이 비면 enabled 와 무관하게 그 멤버는 빠진다.**
  그런데 화면은 그 사실을 말하지 않았고, 「쓰기」를 켜도 줄이 흐린 채 "✘ 사용 안 함" 그대로여서 반응이 없어 보였다.
  버튼·API 검사는 전부 통과했다. 눌러 보고 화면이 무엇을 말하는지 읽는 검사가 없었기 때문이다.

재현하는 상태: llm_roles.expand.ensemble.enabled = true, 멤버 3칸 모두 model="" (= 사용자가 막혔던 그 상태).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

# 콘솔이 cp949 여도 한글·기호 출력에서 죽지 않게 (다른 verify_* 와 같은 처리, 2026-09-24)
try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
except Exception:
    pass
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_click import WS, Page, EDGE                      # noqa: E402
from verify_buttons import isolated_env, ROOT, PY            # noqa: E402

PORT = int(os.environ.get("LLMWIKI_VERIFY_PORT", "8796"))
CDP = int(os.environ.get("LLMWIKI_VERIFY_CDP", "9396"))
ROLE = "expand"
# 사용자가 막혔던 상태 그대로: 켜져 있지만 멤버는 모두 모델이 비어 있다.
BROKEN = {ROLE: {"ensemble": {"enabled": True, "prompt": "", "wait": "", "timeout_s": "", "min_results": "",
                              "aggregator": {"provider": "", "model": "", "effort": ""},
                              "members": [{"enabled": True, "provider": "", "model": "", "weight": 1.0, "effort": ""},
                                          {"enabled": True, "provider": "", "model": "", "weight": 1.0, "effort": ""},
                                          {"enabled": False, "provider": "", "model": "", "weight": 1.0, "effort": ""}]}}}

rows: list[tuple[str, str, str]] = []


def check(name: str, ok, why: str = "") -> None:
    rows.append(("OK  " if ok is True else "FAIL", name, "" if ok is True else (why or "기대와 다름: %r" % (ok,))))
    print("%s %-46s %s" % (rows[-1][0], name, rows[-1][2]))


def main() -> int:
    if not EDGE:
        print("ENSEMBLE-UI SKIP: Edge/Chrome 을 찾지 못함 (LLMWIKI_BROWSER 로 지정)")
        return 0
    tmp, env, proc = isolated_env(PORT, extra_cfg={"llm_provider": "mock", "llm_model": "mock", "llm_roles": BROKEN})
    br = None
    try:
        for _ in range(90):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/api/auth/me" % PORT, timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        base = "http://127.0.0.1:%d" % PORT
        # ---- 1. 서버가 역할 모델을 함께 준다 (화면이 "비우면 무엇이 되는지" 를 말하려면 필요) ----
        api = json.load(urllib.request.urlopen(base + "/api/models", timeout=20))
        ens = (api.get("ensemble") or {}).get(ROLE) or {}
        check("API: ensemble.%s 에 role(역할 모델) 포함" % ROLE, "role" in ens, "키=%s" % sorted(ens.keys()))
        role_model = (ens.get("role") or {}).get("model") or ""
        check("API: 역할 모델이 비어 있지 않음", bool(role_model), "role=%r" % (ens.get("role"),))
        check("API: enabled=true 인데 유효 멤버 0개 (재현 성공)",
              (ens.get("effective") or {}).get("enabled") is False and not (ens.get("effective") or {}).get("members"),
              "effective=%r" % (ens.get("effective"),))

        prof = os.path.join(tmp, "profile")
        br = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--user-data-dir=" + prof,
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
            print("ENSEMBLE-UI SKIP: CDP 에 붙지 못함")
            return 0
        page = Page(WS(ws_url))
        page.eval("window.__errs=[]; window.addEventListener('error',e=>__errs.push(String(e.message))); 'ok'")
        page.eval("location.href=%r; 'ok'" % (base + "/#pipeline/ensemble"))
        for _ in range(40):                       # 앙상블 표가 그려질 때까지
            time.sleep(0.5)
            if page.eval("!!document.querySelector('#ens-roles [data-ens-card=\"%s\"]')" % ROLE) is True:
                break
        check("화면: JS 오류 없이 앙상블 탭이 그려짐",
              page.eval("(window.__errs||[]).length === 0 && !!document.querySelector('#ens-roles [data-ens-card=\"%s\"]')" % ROLE),
              str(page.eval("(window.__errs||[]).slice(0,2)")))

        q = "#ens-roles [data-ens-card=\"%s\"] " % ROLE
        # ---- 2. 빈 칸 라벨이 거짓말을 하지 않는다 ----
        # 예전 라벨은 "(상속: 사용 안 함)" — 비우면 상속되는 것처럼 읽히지만 실제로는 그 멤버가 빠진다.
        lbl = page.eval("(function(){var s=document.querySelector('%s[data-ens-m=\"0\"][data-ens-f=\"model\"]');"
                        "return s? s.options[0].textContent : '';})()" % q)
        check("멤버 빈칸 라벨에 '상속' 이라고 쓰지 않는다", isinstance(lbl, str) and "상속" not in lbl, "라벨=%r" % lbl)
        check("멤버 빈칸 라벨이 '호출되지 않는다' 고 말한다",
              isinstance(lbl, str) and ("호출되지 않" in lbl or "안 씀" in lbl), "라벨=%r" % lbl)

        # ---- 2-1. 드롭다운에 **고를 수 있는 모델이 실제로 있다** ----
        # 이 탭으로 바로 오면 카탈로그를 없는 키(j.catalog)로 읽어 CATALOG 가 비었고, 멤버 모델 칸에
        # 고를 것이 하나도 없어 "칸이 잠겼다" 로 보였다. 라벨만 보는 검사로는 잡히지 않는다.
        opts = page.eval("(function(){var s=document.querySelector('%s[data-ens-m=\"0\"][data-ens-f=\"model\"]');"
                         "if(!s) return -1; var n=0;"
                         "for(var i=0;i<s.options.length;i++){var o=s.options[i];"
                         "if(o.value && o.value!=='__custom__' && !o.disabled) n++;} return n;})()" % q)
        check("멤버 모델 드롭다운에 고를 수 있는 모델이 있다", isinstance(opts, int) and opts > 0, "선택 가능 옵션=%r" % opts)
        check("역할 모델이 드롭다운에서 고를 수 있다",
              page.eval("(function(){var s=document.querySelector('%s[data-ens-m=\"0\"][data-ens-f=\"model\"]');"
                        "if(!s) return false;"
                        "for(var i=0;i<s.options.length;i++){if(s.options[i].value===%r && !s.options[i].disabled) return true;}"
                        "return false;})()" % (q, role_model)), "역할 모델=%r" % role_model)

        # ---- 3. 켰는데 멤버 0개면 경고가 보인다 (조용히 단일 LLM 으로 도는 것을 알려야 한다) ----
        check("경고: '멤버 0개라 앙상블이 돌지 않는다' 가 보인다",
              page.eval("(function(){var b=document.querySelector('%s[data-ens-empty]');"
                        "return !!b && b.offsetParent !== null && b.textContent.indexOf('0개')>=0;})()" % q))
        check("경고에 역할 모델이 무엇인지 적혀 있다",
              page.eval("(function(){var b=document.querySelector('%s[data-ens-empty]');"
                        "return !!b && b.textContent.indexOf(%r)>=0;})()" % (q, role_model)))
        check("요약 pill 이 'ON' 만 말하지 않고 멤버 0 을 알린다",
              page.eval("(function(){var p=document.querySelector('%ssummary .pill');"
                        "return !!p && p.textContent.indexOf('0')>=0;})()" % q),
              str(page.eval("(function(){var p=document.querySelector('%ssummary .pill'); return p?p.textContent:'';})()" % q)))

        # ---- 4. 「쓰기」를 켜면 역할 모델이 채워져 실제로 켜진다 (여기서 막혔다) ----
        before = page.eval("(function(){var s=document.querySelector('%s[data-ens-m=\"2\"][data-ens-f=\"model\"]'); return s?s.value:'?';})()" % q)
        check("멤버 #3 은 처음에 모델이 비어 있다", before == "", "값=%r" % before)
        page.eval("(function(){var c=document.querySelector('%s[data-ens-m=\"2\"][data-ens-f=\"enabled\"]');"
                  "c.checked=true; c.dispatchEvent(new Event('change',{bubbles:true})); return 'ok';})()" % q)
        time.sleep(0.6)
        after = page.eval("(function(){var s=document.querySelector('%s[data-ens-m=\"2\"][data-ens-f=\"model\"]'); return s?s.value:'?';})()" % q)
        check("「쓰기」를 켜면 멤버 #3 에 역할 모델이 자동으로 채워진다", after == role_model, "값=%r (역할 모델=%r)" % (after, role_model))
        check("멤버 #3 줄이 더 이상 꺼진(off) 상태가 아니다",
              page.eval("(function(){var r=document.querySelector('%s[data-ens-mrow=\"2\"]'); return !!r && !r.classList.contains('off');})()" % q))
        check("멤버가 생기면 '멤버 0개' 경고가 사라진다",
              page.eval("(function(){var b=document.querySelector('%s[data-ens-empty]');"
                        "return !b || b.style.display === 'none';})()" % q))

        # ---- 5. 「쓰기」만 켜고 모델이 없으면 그 줄이 왜 빠지는지 말한다 ----
        page.eval("(function(){var s=document.querySelector('%s[data-ens-m=\"2\"][data-ens-f=\"model\"]');"
                  "s.value=''; s.dispatchEvent(new Event('change',{bubbles:true})); return 'ok';})()" % q)
        time.sleep(0.5)
        check("모델을 다시 비우면 '모델을 골라야 켜집니다' 라고 말한다",
              page.eval("(function(){var r=document.querySelector('%s[data-ens-mrow=\"2\"]');"
                        "return !!r && r.classList.contains('need') && r.textContent.indexOf('모델을 골라야')>=0;})()" % q))

        # ---- 6. 「빈 멤버 채우기」 버튼이 실제로 채운다 ----
        page.eval("(function(){var b=document.querySelector('%s[data-ens-fill]'); if(b) b.click(); return 'ok';})()" % q)
        time.sleep(0.6)
        filled = page.eval("(function(){var n=0; for(var i=0;i<3;i++){"
                           "var s=document.querySelector('%s[data-ens-m=\"'+i+'\"][data-ens-f=\"model\"]');"
                           "if(s && s.value) n++;} return n;})()" % q)
        check("「빈 멤버 채우기」 로 「쓰기」가 켜진 3칸이 모두 채워진다", filled == 3, "채워진 칸=%r" % filled)

        # ---- 7. 저장하면 config.json 에 실제로 들어간다 (UI → 파일 → 유효값) ----
        page.eval("(function(){var b=document.querySelector('#btn-ens-save')||"
                  "document.querySelector('[data-act=\"ens-save\"]'); if(b) b.click(); return b?'ok':'no-btn';})()")
        time.sleep(2.5)
        api2 = json.load(urllib.request.urlopen(base + "/api/models", timeout=20))
        eff2 = ((api2.get("ensemble") or {}).get(ROLE) or {}).get("effective") or {}
        check("저장 뒤 서버 유효값에 멤버가 생긴다 (UI → 파일 → 유효값)",
              bool(eff2.get("enabled")) and len(eff2.get("members") or []) == 3,
              "enabled=%r members=%d" % (eff2.get("enabled"), len(eff2.get("members") or [])))

        # ---- 8. 실패 시 폴백 (2026-09-20) — 켜고 끄고, 모드를 고르고, 파일까지 왕복하는가 ----
        check("「실패 시」 줄에 폴백 스위치가 있다",
              page.eval("!!document.querySelector('%s[data-ens-f=\"fallback_role_model\"]')" % q))
        check("폴백 기본이 켜짐이다 (앙상블 탓에 답이 아예 안 나오는 일이 없게)",
              page.eval("(function(){var c=document.querySelector('%s[data-ens-f=\"fallback_role_model\"]'); return !!c && c.checked;})()" % q))
        check("되돌아갈 역할 모델을 화면이 말해 준다",
              page.eval("(function(){var l=document.querySelector('%s[data-ens-f=\"fallback_role_model\"]').closest('.ens-line');"
                        "return !!l && l.textContent.indexOf(%r)>=0;})()" % (q, role_model)))
        modes = page.eval("(function(){var s=document.querySelector('%s[data-ens-f=\"fallback_mode\"]'); if(!s) return [];"
                          "var o=[]; for(var i=0;i<s.options.length;i++) o.push(s.options[i].value); return o;})()" % q)
        check("모드 세 가지를 고를 수 있다 (auto·merge·rerun)",
              isinstance(modes, list) and all(m in modes for m in ("auto", "merge", "rerun")), "옵션=%r" % (modes,))
        # 화면에서 끄고 모드를 바꾼 뒤 저장 → 서버 유효값이 실제로 바뀌는가 (UI → 파일 → 유효값)
        page.eval("(function(){var c=document.querySelector('%s[data-ens-f=\"fallback_role_model\"]'); c.checked=false;"
                  "c.dispatchEvent(new Event('change',{bubbles:true}));"
                  "var s=document.querySelector('%s[data-ens-f=\"fallback_mode\"]'); s.value='rerun';"
                  "s.dispatchEvent(new Event('change',{bubbles:true})); return 'ok';})()" % (q, q))
        page.eval("(function(){var b=document.querySelector('#btn-ens-save'); if(b) b.click(); return 'ok';})()")
        time.sleep(2.5)
        api3 = json.load(urllib.request.urlopen(base + "/api/models", timeout=20))
        eff3 = ((api3.get("ensemble") or {}).get(ROLE) or {}).get("effective") or {}
        check("화면에서 폴백을 끄면 서버 유효값도 꺼진다 (UI → 파일 → 유효값)",
              eff3.get("fallback_role_model") is False and eff3.get("fallback") is None,
              "유효값 fallback_role_model=%r fallback=%r" % (eff3.get("fallback_role_model"), eff3.get("fallback")))
        check("화면에서 고른 모드가 서버 유효값에 온다", eff3.get("fallback_mode") == "rerun",
              "유효값 fallback_mode=%r" % eff3.get("fallback_mode"))

        # ---- 9. 최악 소요가 화면에 보이는가 (곱셈이 눈에 안 보여서 함정이 된다) ----
        check("API 가 최악 소요를 계산해 준다",
              isinstance((ens.get("budget") or {}).get("worst_s"), (int, float)) and (ens.get("budget") or {}).get("worst_s") > 0,
              "budget=%r" % (ens.get("budget"),))
        check("API 가 요청 상한(query_s)도 함께 준다",
              isinstance(api.get("query_timeout_s"), (int, float)), "query_timeout_s=%r" % api.get("query_timeout_s"))
        page.eval("location.reload(); 'ok'")
        for _ in range(40):
            time.sleep(0.5)
            if page.eval("!!document.querySelector('#ens-roles [data-ens-card=\"%s\"] .ens-budget')" % ROLE) is True:
                break
        check("화면에 「최악 소요」가 보인다",
              page.eval("(function(){var b=document.querySelector('%s.ens-budget');"
                        "return !!b && b.offsetParent !== null && b.textContent.indexOf('최악 소요')>=0;})()" % q))
        check("최악 소요에 '한 번 = N회 × M초' 근거가 적혀 있다",
              page.eval("(function(){var b=document.querySelector('%s.ens-budget');"
                        "return !!b && /\\d+회 × \\d+초/.test(b.textContent);})()" % q))

        bad = [r for r in rows if r[0] == "FAIL"]
        print("\n%d개 중 %d개 통과" % (len(rows), len(rows) - len(bad)))
        print("ENSEMBLE-UI", "OK" if not bad else "PROBLEMS")
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
