"""설정 ▸ **보안 · 사용자** 탭의 모든 조작을 실제 브라우저에서 눌러 본다.

왜 따로 있나: `verify_buttons.py` 는 정적 버튼을 **빈 입력으로** 누른다. 그래서
`사용자 추가`(id 가 비면 핸들러가 조용히 `return`)나 `API 키 발급`(이름이 비어도 발급은 되지만
결과 표시를 확인하지 않음)처럼 **입력이 있어야 의미가 생기는 버튼**은 "눌러도 아무 일 없음" 과
"원래 아무 일도 안 하는 게 맞음" 을 구분하지 못한다. 여기서는 폼을 채우고 누른 뒤
**서버에 실제로 반영되었는지**(security.json / API 응답)까지 확인한다.

두 가지 모드에서 모두 돌린다 — 실제 배포에서 둘 다 쓰이기 때문이다.

  auto   security.json `mode:"auto"` + 127.0.0.1 접속 → 인증이 꺼진 상태(로컬 관리자).
         사내 배포 전 개발자 PC 에서 보는 화면이 이것이다.
  on     `mode:"on"` + 로컬 admin 계정으로 로그인. 이때만 `warn.confirm` 단계 확인(428 →
         확인 모달)이 걸리므로, **모달을 거쳐서도** 버튼이 끝까지 동작하는지 본다.

실행:
    python tools/verify/verify_security_ui.py                 # 두 모드 모두
    python tools/verify/verify_security_ui.py --mode on       # 한 모드만
    python tools/verify/verify_security_ui.py --snapshot      # 스냅샷 만들기(느림)도 포함
"""
from __future__ import annotations

import argparse
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
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_click import WS, Page, EDGE            # noqa: E402
from verify_buttons import isolated_env            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

ADMIN_USER = "verify.admin"
ADMIN_PW = "verifyadmin123"
NEW_USER = "verify.user"
NEW_PW = "verifyuser123"
NEW_PW2 = "verifyuser456"
KEY_NAME = "verify-key"

# 확인 모달(428 step-up)이 뜨면 문구·비밀번호를 채우고 '진행' 을 누른다.
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
window.confirm = function(){ return true; };        // 삭제·되돌리기까지 끝까지 확인한다
window.alert = function(){};
window.__promptValue = null;
window.prompt = function(){ return window.__promptValue; };
window.__lwReset = function(){ __lw.errs=[]; __lw.fetches=[]; __lw.toasts=[]; };
// 단계 확인 모달이 뜨면 자동으로 통과시킨다 (사람이 누르는 것과 같은 경로)
window.__stepups = 0;
setInterval(function(){
  var ov = document.getElementById('stepup'); if (!ov) return;
  var ph = document.getElementById('su-phrase');
  if (ph) { var b = ov.querySelector('.modal-op code'); ph.value = (ov.textContent.match(/DELETE INDEX/) ? 'DELETE INDEX' : ph.placeholder || ''); }
  var pw = document.getElementById('su-pw'); if (pw) pw.value = %s;
  var ok = document.getElementById('su-ok'); if (ok) { __lw.toasts.push('[단계확인 모달 통과]'); window.__stepups++; ok.click(); }
}, 250);
'ok'
"""


def js(v):
    return json.dumps(v, ensure_ascii=False)


class Runner:
    def __init__(self, page, base):
        self.page, self.base, self.rows = page, base, []

    def step(self, name, prep, click, expect, note=""):
        p = self.page
        p.eval("window.__lwReset(); 'ok'")
        if prep:
            r = p.eval(prep + "; 'ok'")
            if isinstance(r, dict) and r.get("__error__"):
                return self._rec("FAIL", name, "준비 JS 오류 " + str(r["__error__"])[:90], {})
        clicked = p.eval(click)
        if isinstance(clicked, dict) and clicked.get("__error__"):
            return self._rec("FAIL", name, "클릭 JS 오류 " + str(clicked["__error__"])[:90], {})
        if clicked in ("no-el", "disabled"):
            return self._rec("FAIL", name, "대상 없음/비활성 (%s)" % clicked, {})
        p.eval("new Promise(r=>setTimeout(()=>r(1), 2200))")
        st = p.eval("(function(){return {errs:__lw.errs.slice(0,3), fetches:__lw.fetches.length,"
                    " toasts:__lw.toasts.slice(0,3), stepups: window.__stepups};})()") or {}
        errs = st.get("errs") or []
        ok = True if not expect else p.eval(expect)
        if errs:
            return self._rec("FAIL", name, "JS 오류 " + str(errs[0])[:90], st)
        if ok is not True:
            why = "기대 불충족"
            if isinstance(ok, dict) and ok.get("__error__"):
                why = "기대식 오류 " + str(ok["__error__"])[:70]
            return self._rec("FAIL", name, why + " :: " + expect[:70], st)
        return self._rec("OK  ", name, note, st)

    def _rec(self, verdict, name, why, st):
        self.rows.append((verdict, name, why, st))
        print("%s %-34s fetch=%-2s %s%s" % (
            verdict, name, st.get("fetches"),
            (("알림: " + str((st.get("toasts") or [""])[0])[:42]) if st.get("toasts") else ""),
            ("  " + why) if why else ""))
        return verdict == "OK  "


def api_get(page, path):
    """**브라우저 안에서** GET 한다.

    밖에서 urllib 로 부르면 세션 쿠키가 없어 `mode:"on"` 에서는 익명(viewer)으로 취급되어
    사용자·키 목록이 빈 배열로 돌아온다 — 화면은 멀쩡한데 검사만 실패하는 가짜 결함이 된다.
    """
    r = page.eval("fetch(%s, {headers:{'X-Requested-With':'llmwiki'}}).then(r=>r.text())" % js(path))
    if isinstance(r, dict) and r.get("__error__"):
        return {"error": str(r["__error__"])}
    try:
        return json.loads(r or "{}")
    except Exception as e:
        return {"error": "%s :: %s" % (e, str(r)[:120])}


PLAIN_USER = "verify.plain"
PLAIN_PW = "verifyplain123"


def run_mode(mode: str, port: int, cdp: int, do_snapshot: bool):
    """한 모드(auto|on|nonadmin)에서 보안 탭 전체를 눌러 본다. (rows, tmp) 반환."""
    print("\n" + "=" * 78)
    print("보안 · 사용자 탭 — %s" % ("mode=on · admin 이 **아닌** 계정" if mode == "nonadmin" else "security.json mode=%s" % mode))
    print("=" * 78)
    from llmwiki.auth import hash_password
    sec = json.load(open(os.path.join(ROOT, "security.json"), encoding="utf-8"))
    sec["mode"] = "on" if mode == "nonadmin" else mode
    sec["users"] = {ADMIN_USER: {"role": "admin", "pw": hash_password(ADMIN_PW), "created": time.time()},
                    PLAIN_USER: {"role": "class2", "pw": hash_password(PLAIN_PW), "created": time.time()}}
    sec["api_keys"] = {}
    sec["permissions"] = dict(sec.get("permissions") or {}, ops={})   # 예전 테스트가 남긴 op 오버라이드 제거
    tmp, env, proc = isolated_env(port, extra_files={"security.json": sec})
    base = "http://127.0.0.1:%d" % port
    prof = os.path.join(tempfile.gettempdir(), "llmwiki_secui_profile_%s" % mode)
    shutil.rmtree(prof, ignore_errors=True)
    br = None
    rows = []
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(base + "/api/auth/me", timeout=2)
                break
            except Exception:
                time.sleep(1)
        br = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
                               "--user-data-dir=" + prof, "--remote-debugging-port=%d" % cdp, base + "/"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = None
        for _ in range(60):
            try:
                lst = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % cdp, timeout=2).read().decode())
                url = next((t["webSocketDebuggerUrl"] for t in lst if t.get("type") == "page"), None)
                if url:
                    break
            except Exception:
                pass
            time.sleep(1)
        if not url:
            print("SKIP: 브라우저 디버깅 포트에 붙지 못했습니다")
            return [], tmp
        page = Page(WS(url))
        page.eval("new Promise(r=>setTimeout(()=>r(1), 4000))")
        page.eval(SHIM % js(ADMIN_PW))

        if mode in ("on", "nonadmin"):
            # 로그인하지 않으면 /login 으로 튕긴다 → 브라우저 컨텍스트에서 로그인해 쿠키를 심는다
            who, pw = (PLAIN_USER, PLAIN_PW) if mode == "nonadmin" else (ADMIN_USER, ADMIN_PW)
            r = page.eval("fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json',"
                          "'X-Requested-With':'llmwiki'}, body: JSON.stringify({username:%s, password:%s})})"
                          ".then(r=>r.status)" % (js(who), js(pw)))
            print("  로그인(%s): HTTP %s" % (who, r))
            page.eval("location.href='/'; 'ok'")
            page.eval("new Promise(r=>setTimeout(()=>r(1), 4000))")
            page.eval(SHIM % js(pw))

        run = Runner(page, base)
        page.eval("location.hash='#settings/security'; 'ok'")
        page.eval("new Promise(r=>setTimeout(()=>r(1), 3000))")

        if mode == "nonadmin":
            # admin 이 아닌 사람이 보는 화면. 버튼을 남겨 두고 401/403 을 뱉는 것이 아니라,
            # **왜 못 쓰는지**가 화면에 적혀 있어야 한다 (이것이 '버튼이 죽었다' 의 흔한 정체).
            run.step("사용자 추가 폼이 감춰짐", "", "'checked'",
                     "document.querySelector('#sec-add-form').classList.contains('hidden')")
            run.step("왜 못 쓰는지 화면에 표시", "", "'checked'",
                     "document.querySelector('#sec-users').textContent.indexOf('admin') >= 0"
                     " && document.querySelector('#sec-users').textContent.indexOf('class2') >= 0")
            run.step("API 키 영역은 비어 있음 (발급 버튼 없음)", "", "'checked'",
                     "!document.querySelector('#btn-ak-add')")
            run.step("내 비밀번호 변경은 그대로 쓸 수 있다",
                     "document.querySelector('#pw-old').value=%s; document.querySelector('#pw-new').value=%s"
                     % (js(PLAIN_PW), js(PLAIN_PW + "x")),
                     "(function(){var b=document.getElementById('btn-pw-change'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                     "__lw.toasts.join(' ').indexOf('비밀번호 변경됨') >= 0")
            return run.rows, tmp

        run.step("탭 열림 · 목록 렌더", "",
                 "'shown'",
                 "document.querySelector('#sec-users').innerHTML.length > 10"
                 " && document.querySelector('#sec-summary').innerHTML.length > 20")
        run.step("새로고침 (btn-sec-refresh)", "",
                 "(function(){var b=document.getElementById('btn-sec-refresh'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "document.querySelector('#sec-users').innerHTML.length > 10")
        run.step("security.json 다시 읽기", "",
                 "(function(){var b=document.getElementById('btn-sec-reload'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "__lw.toasts.join(' ').indexOf('security.json') >= 0")

        # ---- 입력이 비었을 때: 조용히 끝나지 않고 **안내가 떠야** 한다 ----
        run.step("빈 id 로 추가 → 안내",
                 "document.querySelector('#su-name').value=''; document.querySelector('#su-pass').value=''",
                 "(function(){var b=document.getElementById('btn-user-add'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "__lw.toasts.join(' ').indexOf('id 를 입력') >= 0",
                 note="예전에는 아무 반응이 없어 '버튼이 죽었다' 로 보였다")
        run.step("짧은 비밀번호 → 보내기 전에 안내",
                 "document.querySelector('#su-name').value='verify.short'; document.querySelector('#su-pass').value='123'",
                 "(function(){var b=document.getElementById('btn-user-add'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "__lw.toasts.join(' ').indexOf('자 이상') >= 0")

        # ---- 사용자 추가: 폼을 **채우고** 누른다 (여기가 verify_buttons 가 못 보던 자리) ----
        run.step("사용자 추가 (id·비밀번호 입력)",
                 "document.querySelector('#su-name').value=%s;"
                 " document.querySelector('#su-pass').value=%s;"
                 " document.querySelector('#su-role').value='class2'" % (js(NEW_USER), js(NEW_PW)),
                 "(function(){var b=document.getElementById('btn-user-add'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "document.querySelector('#sec-users').innerHTML.indexOf(%s) >= 0" % js(NEW_USER))
        us = api_get(page, "/api/auth/users")
        names = [x.get("name") for x in (us.get("users") or [])]
        run._rec("OK  " if NEW_USER in names else "FAIL", "추가한 사용자가 서버에 남음",
                 "" if NEW_USER in names else "서버 목록에 없음: %s" % names, {})
        run.step("추가 폼이 비워짐 (재입력 방지)", "", "'checked'",
                 "document.querySelector('#su-name').value === '' && document.querySelector('#su-pass').value === ''")

        run.step("역할 바꾸기 (표의 드롭다운)", "",
                 "(function(){var s=document.querySelector(\"#sec-users [data-role-of='%s']\");"
                 " if(!s) return 'no-el'; s.value='class1'; s.onchange(); return 'clicked';})()" % NEW_USER,
                 "__lw.toasts.join(' ').indexOf('역할 변경') >= 0")
        run.step("사용자 비밀번호 바꾸기 (관리자)",
                 "window.__promptValue=%s" % js(NEW_PW2),
                 "(function(){var b=document.querySelector(\"#sec-users [data-pw-of='%s']\");"
                 " if(!b) return 'no-el'; b.click(); return 'clicked';})()" % NEW_USER,
                 "__lw.toasts.join(' ').indexOf('비밀번호 변경됨') >= 0")

        # ---- API 키 발급 ----
        run.step("빈 이름으로 발급 → 안내",
                 "document.querySelector('#ak-name').value=''",
                 "(function(){var b=document.getElementById('btn-ak-add'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "__lw.toasts.join(' ').indexOf('키 이름을 입력') >= 0")
        run.step("API 키 발급 (이름·역할 입력)",
                 "document.querySelector('#ak-name').value=%s; document.querySelector('#ak-role').value='class3'" % js(KEY_NAME),
                 "(function(){var b=document.getElementById('btn-ak-add'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "!document.querySelector('#ak-out').classList.contains('hidden')"
                 " && document.querySelector('#ak-out').textContent.indexOf('token') >= 0")
        run.step("발급 즉시 표에도 나타남 (새로고침 없이)", "", "'checked'",
                 "document.querySelector('#sec-keys').innerHTML.indexOf(%s) >= 0"
                 " && !document.querySelector('#ak-out').classList.contains('hidden')" % js(KEY_NAME),
                 note="토큰 표시는 그대로 남아야 한다 (한 번만 볼 수 있으므로)")
        ak = api_get(page, "/api/apikeys")
        knames = [x.get("name") for x in (ak.get("keys") or [])]
        run._rec("OK  " if KEY_NAME in knames else "FAIL", "발급한 키가 서버에 남음",
                 "" if KEY_NAME in knames else "서버 키 목록: %s" % knames, {})
        run.step("발급한 키가 표에 보임 (새로고침)", "",
                 "(function(){document.getElementById('btn-sec-refresh').click(); return 'clicked';})()",
                 "document.querySelector('#sec-keys').innerHTML.indexOf(%s) >= 0" % js(KEY_NAME))
        run.step("API 키 삭제", "",
                 "(function(){var b=document.querySelector('#sec-keys [data-key-del]');"
                 " if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "document.querySelector('#sec-keys').innerHTML.indexOf(%s) < 0" % js(KEY_NAME))

        # ---- 권한 표 ----
        run.step("권한 저장 (btn-perms-save)",
                 "document.querySelector(\"#sec-perms [data-lv='run']\").value='class2'",
                 "(function(){var b=document.getElementById('btn-perms-save'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "__lw.toasts.join(' ').indexOf('권한 저장됨') >= 0")
        sv = api_get(page, "/api/security")
        lv = ((sv.get("permissions") or {}).get("levels") or {}).get("run")
        run._rec("OK  " if lv == "class2" else "FAIL", "권한 변경이 파일에 반영됨",
                 "" if lv == "class2" else "run=%s (class2 기대)" % lv, {})
        run.step("기본값으로 (btn-perms-reset)", "",
                 "(function(){var b=document.getElementById('btn-perms-reset'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "document.querySelector(\"#sec-perms [data-lv='run']\").value !== 'class2'")

        # ---- 문서 접근 제어 (docacl.json) ----
        # 화면 → 파일 → 서버 판정까지 한 줄로 확인한다. 규칙을 넣었는데 실제로 가려지지 않으면
        # "보안 설정이 있는 줄 알았는데 없는" 가장 나쁜 상태가 되므로, 저장 후 check 결과까지 본다.
        run.step("문서 접근 제어 패널이 그려짐", "", "'checked'",
                 "!!document.querySelector('#acl-enabled') && !!document.getElementById('btn-acl-save')")
        run.step("규칙 추가 → 저장",
                 "(function(){document.getElementById('btn-acl-add').click();"
                 " var rs=document.querySelectorAll('#acl-rules tr[data-i] [data-acl-prefix]');"
                 " var last=rs[rs.length-1]; last.value='corpus/__verify_acl__/';"
                 " var sel=last.closest('tr').querySelector('[data-acl-role]'); sel.value='class1'; return 'set';})()",
                 "(function(){var b=document.getElementById('btn-acl-save'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "__lw.toasts.join(' ').indexOf('문서 접근 제어 저장됨') >= 0")
        acl = api_get(page, "/api/docacl")
        prefixes = [r.get("prefix") for r in (acl.get("rules") or [])]
        ok_saved = "corpus/__verify_acl__/" in prefixes
        run._rec("OK  " if ok_saved else "FAIL", "규칙이 docacl.json 에 저장됨",
                 "" if ok_saved else "서버 규칙: %s" % prefixes, {})
        run.step("영향 확인 버튼이 역할별 표를 그림", "",
                 "(function(){var b=document.getElementById('btn-acl-check'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                 "document.querySelector('#acl-check').innerHTML.indexOf('가려짐') >= 0",
                 note="저장 전에 '몇 건이 가려지나' 를 볼 수 있어야 규칙을 안심하고 넣는다")
        run.step("규칙 삭제 → 저장 (뒷정리)",
                 "(function(){var rows=document.querySelectorAll('#acl-rules tr[data-i]');"
                 " for(var i=0;i<rows.length;i++){ var p=rows[i].querySelector('[data-acl-prefix]');"
                 "  if(p && p.value.indexOf('__verify_acl__')>=0) p.value=''; } return 'cleared';})()",
                 "(function(){document.getElementById('btn-acl-save').click(); return 'clicked';})()",
                 "__lw.toasts.join(' ').indexOf('문서 접근 제어 저장됨') >= 0")
        acl2 = api_get(page, "/api/docacl")
        gone = "corpus/__verify_acl__/" not in [r.get("prefix") for r in (acl2.get("rules") or [])]
        run._rec("OK  " if gone else "FAIL", "빈 접두사 규칙은 삭제로 처리됨", "" if gone else "아직 남아 있다", {})

        # ---- 내 비밀번호 변경 ----
        if mode == "on":
            run.step("내 비밀번호 변경 (로컬 로그인)",
                     "document.querySelector('#pw-old').value=%s; document.querySelector('#pw-new').value=%s"
                     % (js(ADMIN_PW), js(ADMIN_PW + "x")),
                     "(function(){var b=document.getElementById('btn-pw-change'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                     "__lw.toasts.join(' ').indexOf('비밀번호 변경됨') >= 0")
        else:
            run.step("내 비밀번호 변경 → 안내 (인증 꺼짐)",
                     "document.querySelector('#pw-old').value='x'; document.querySelector('#pw-new').value='yyyyyyyy'",
                     "(function(){var b=document.getElementById('btn-pw-change'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                     "__lw.toasts.join(' ').indexOf('로컬 계정') >= 0",
                     note="인증이 꺼진 상태에서는 바꿀 계정이 없다 — 안내가 뜨면 정상")

        # ---- 사용자 삭제 (뒷정리 겸) ----
        run.step("사용자 삭제", "",
                 "(function(){var b=document.querySelector(\"#sec-users [data-del-of='%s']\");"
                 " if(!b) return 'no-el'; b.click(); return 'clicked';})()" % NEW_USER,
                 "document.querySelector('#sec-users').innerHTML.indexOf(%s) < 0" % js(NEW_USER))

        if do_snapshot:
            run.step("스냅샷 만들기 (btn-snap-create)",
                     "window.__promptValue='verify'",
                     "(function(){var b=document.getElementById('btn-snap-create'); if(!b) return 'no-el'; b.click(); return 'clicked';})()",
                     "document.querySelector('#sec-snapshots').innerHTML.indexOf('verify') >= 0")

        n = page.eval("window.__stepups") or 0
        print("  단계 확인 모달 통과 %s회 (mode=on 이면 변경 버튼마다 1회가 정상)" % n)
        rows = run.rows
        return rows, tmp
    finally:
        for p in (br, proc):
            if p:
                p.terminate()
                try:
                    p.communicate(timeout=10)
                except Exception:
                    p.kill()
        shutil.rmtree(prof, ignore_errors=True)


def main(argv=None) -> int:
    if not EDGE:
        print("SECURITY-UI SKIP: Edge/Chrome 을 찾지 못함 (LLMWIKI_BROWSER 로 지정)")
        return 0
    ap = argparse.ArgumentParser(description="보안 · 사용자 탭의 모든 조작을 실제로 눌러 확인")
    ap.add_argument("--mode", default="auto,on,nonadmin",
                    help="auto(인증 꺼짐) / on(admin 로그인) / nonadmin(admin 아닌 계정) — 쉼표로 여러 개")
    ap.add_argument("--port", type=int, default=8841)
    ap.add_argument("--cdp", type=int, default=9361)
    ap.add_argument("--snapshot", action="store_true", help="스냅샷 만들기도 누른다 (DB 복사 — 느림)")
    ap.add_argument("--keep", action="store_true")
    ns = ap.parse_args(argv)

    allrows = []
    tmps = []
    for i, mode in enumerate([m.strip() for m in ns.mode.split(",") if m.strip()]):
        rows, tmp = run_mode(mode, ns.port + i, ns.cdp + i, ns.snapshot)
        tmps.append(tmp)
        allrows += [(mode,) + r for r in rows]
    if not ns.keep:
        for t in tmps:
            if t:
                shutil.rmtree(t, ignore_errors=True)

    bad = [r for r in allrows if r[1] != "OK  "]
    print("\n검사 %d개 중 %d개 통과 · 실패 %d개" % (len(allrows), len(allrows) - len(bad), len(bad)))
    for r in bad:
        print("  FAIL [%s] %-34s %s" % (r[0], r[2], r[3]))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_security_ui_result.json")
    json.dump([{"mode": r[0], "verdict": r[1], "check": r[2], "why": r[3], "state": r[4]} for r in allrows],
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("결과: %s" % out)
    print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
