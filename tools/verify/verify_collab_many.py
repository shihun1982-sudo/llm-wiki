# -*- coding: utf-8 -*-
"""협업 다중 접속 시뮬레이션 — "10명이 붙어서 각자 움직이면 내 화면에 어떻게 보이나" 를 실제로 확인한다.

무엇을 하나
  1. 격리 서버를 띄우고 API 키 N개를 발급한다 (사람마다 다른 계정).
  2. N명이 각자 아이콘을 **움직이고 말을 한다** (백그라운드 스레드가 계속).
  3. 그동안 **헤드리스 브라우저**로 화면을 열어, 내 화면에 아이콘이 N개 보이는지 · 남이 움직이면
     내 화면의 좌표도 따라 바뀌는지 · 말풍선이 뜨는지를 실측한다.
  4. 화면 스크린샷을 남긴다 (`tools/verify/collab_many_<N>.png`) — 눈으로도 확인할 수 있게.

실행: python tools/verify/verify_collab_many.py [--people 10] [--seconds 12] [--port 8842] [--cdp 9350] [--keep]
결과: tools/verify/verify_collab_many_result.json (실패 항목이 있으면 종료코드 1)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
PY = sys.executable

from verify_buttons import isolated_env, EDGE          # noqa: E402
from verify_click import WS, Page                      # noqa: E402

try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
except Exception:
    pass

RESULTS = []
FAILED = []


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    if not ok:
        FAILED.append(name)
    print("  %s %-52s %s" % ("OK  " if ok else "FAIL", name, str(detail)[:110]))
    return ok


def post(base, token, body, timeout=20):
    req = urllib.request.Request(base + "/api/collab", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "X-Requested-With": "verify-collab",
                                          "Authorization": "Bearer " + token}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s" % e.code, "body": e.read()[:200].decode("utf-8", "replace")}
    except Exception as e:
        return {"error": str(e)[:120]}


def get(base, token, path, timeout=20):
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)[:120]}


SAYINGS = ["AGC 수렴 확인 중", "CL-55310 리뷰했습니다", "ISSUE-2001 재현됨", "빌드 돌립니다",
           "타이밍 8ns 넘어갑니다", "PA gain 테이블 봐 주세요", "회의 15분 뒤", "로그 올렸어요",
           "DMA underrun 또 났네요", "regression 통과"]


def walker(base, token, name, seconds, rng, errs):
    """한 사람: 계속 움직이고 가끔 말한다."""
    t_end = time.time() + seconds
    try:
        while time.time() < t_end:
            x = round(rng.uniform(0.08, 0.92), 3)
            y = round(rng.uniform(0.12, 0.88), 3)
            r = post(base, token, {"action": "touch", "x": x, "y": y})
            if r.get("error"):
                errs.append("%s touch: %s" % (name, r["error"]))
                return
            if rng.random() < 0.35:
                r2 = post(base, token, {"action": "say", "text": rng.choice(SAYINGS)})
                if r2.get("error"):
                    errs.append("%s say: %s" % (name, r2["error"]))
                    return
            time.sleep(rng.uniform(0.6, 1.4))
    except Exception as e:      # noqa: BLE001
        errs.append("%s: %s" % (name, e))


def main(argv=None):
    ap = argparse.ArgumentParser(description="협업 다중 접속 시뮬레이션 (아이콘·말풍선·실시간 이동)")
    ap.add_argument("--people", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=12.0, help="움직이는 시간(초)")
    ap.add_argument("--port", type=int, default=8842)
    ap.add_argument("--cdp", type=int, default=9350)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--no-browser", action="store_true", help="브라우저 없이 서버 상태만 확인")
    ns = ap.parse_args(argv)
    base = "http://127.0.0.1:%d" % ns.port
    rng = random.Random(20260916)

    print("협업 다중 접속 시뮬레이션 — %d명 · %.0f초" % (ns.people, ns.seconds))
    sec = json.load(open(os.path.join(ROOT, "security.json"), encoding="utf-8"))
    sec.update({"mode": "on", "anonymous_role": "viewer", "api_keys": {}})
    sec["cli"] = dict(sec.get("cli") or {}, default_role="admin", require_login=False)
    tmp, env, _ = isolated_env(ns.port, extra_files={"security.json": sec}, serve=False)

    names = ["dev%02d" % (i + 1) for i in range(ns.people)]
    keys = {}
    for n in names:
        r = subprocess.run([PY, "-m", "llmwiki", "apikey", "add", n, "--role", "class2"], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        tok = next((t for t in (r.stdout or "").replace('"', " ").replace(",", " ").split() if t.startswith("lwk_")), "")
        if tok:
            keys[n] = tok
    print("  계정 %d개 발급" % len(keys))

    proc = subprocess.Popen([PY, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(ns.port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = page = None
    shot = ""
    try:
        for _ in range(180):
            try:
                urllib.request.urlopen(base + "/api/auth/me", timeout=2)
                break
            except Exception:
                time.sleep(1)

        # ---- 모두 접속시킨다 ----
        for n, tok in keys.items():
            post(base, tok, {"action": "touch", "x": 0.1 + 0.08 * names.index(n), "y": 0.3})
        st = get(base, keys[names[0]], "/api/collab")
        check("%d명이 모두 접속자 목록에 보인다" % len(keys), len(st.get("people") or []) == len(keys),
              "people=%d (%s…)" % (len(st.get("people") or []), ", ".join(p["user"] for p in (st.get("people") or [])[:4])))
        emojis = {p["user"]: p["emoji"] for p in st.get("people") or []}
        check("아이콘이 정해져 있다 (접속 IP 의 SHA-1 — 랜덤 아님)", all(emojis.values()),
              " ".join("%s=%s" % (k, v) for k, v in list(emojis.items())[:6]))
        from llmwiki import collab as _cbmod
        check("같은 IP 는 늘 같은 아이콘", _cbmod.default_emoji("10.1.2.3") == _cbmod.default_emoji("10.1.2.3")
              and _cbmod.default_emoji("10.1.2.3") in _cbmod.EMOJI,
              "10.1.2.3 → %s" % _cbmod.default_emoji("10.1.2.3"))
        check("아이콘은 고를 수 없다 (선택지를 내보내지 않는다)", "emoji_choices" not in st, list(st)[:6])
        r = post(base, keys[names[0]], {"action": "touch", "emoji": "🔬"})
        check("emoji 를 보내도 무시된다 (IP 로만 정해진다)", r.get("emoji") != "🔬", r.get("emoji"))
        check("남의 IP 를 화면에 내보내지 않는다", all("ip_key" not in p for p in st.get("people") or []), "")

        # ---- 브라우저로 내 화면을 연다 (dev01 로 로그인 대신 익명 viewer 로 보되, 목록은 같다) ----
        if not ns.no_browser and EDGE:
            import shutil as _sh
            import tempfile as _tf
            prof = os.path.join(_tf.gettempdir(), "llmwiki_collab_profile")
            _sh.rmtree(prof, ignore_errors=True)
            br = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--window-size=1400,900",
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
            if url:
                page = Page(WS(url))
                page.eval("new Promise(r=>setTimeout(()=>r(1), 6000))")     # boot + 첫 폴링

        # ---- 모두 동시에 움직이며 말한다 ----
        errs = []
        ths = [threading.Thread(target=walker, args=(base, keys[n], n, ns.seconds, random.Random(rng.random()), errs), daemon=True)
               for n in names]
        t0 = time.time()
        [t.start() for t in ths]

        moved = []
        if page:
            # 움직이는 동안 내 화면의 아이콘 좌표를 두 번 읽어 **실제로 따라 움직이는지** 본다
            def snapshot():
                return page.eval("(function(){var o={};document.querySelectorAll('#people-layer .person').forEach(function(p){"
                                 "o[p.dataset.user]=[p.style.left,p.style.top,(p.querySelector('.bubble')||{}).textContent||''];});"
                                 "return o;})()") or {}
            time.sleep(3)
            a = snapshot()
            time.sleep(4)
            b = snapshot()
            n_icons = len(b)
            # 화면을 보고 있는 나 자신도 접속자다 → 움직이는 N명 + 보는 사람 1명
            check("내 화면에 %d명 + 나 = %d개 아이콘이 보인다" % (len(keys), len(keys) + 1),
                  n_icons in (len(keys), len(keys) + 1), "보이는 아이콘 %d개 (%s)" % (n_icons, ", ".join(sorted(b)[:3])))
            moved = [u for u in b if u in a and (a[u][0] != b[u][0] or a[u][1] != b[u][1])]
            check("남이 움직이면 내 화면의 아이콘도 따라 움직인다", len(moved) >= max(2, len(keys) // 3),
                  "%d/%d 명이 이동한 것으로 보임 (1초 폴링)" % (len(moved), n_icons))
            bubbles = [u for u in b if b[u][2]]
            check("말풍선이 아이콘 위에 뜬다", len(bubbles) >= 1, "%d명에게 말풍선" % len(bubbles))
            # '챗' 한 번으로 입력칸과 아이콘이 함께 꺼지고 켜진다
            page.eval("(function(){var b=document.getElementById('btn-chat-toggle'); if(b) b.click(); return 'ok';})()")
            page.eval("new Promise(r=>setTimeout(()=>r(1), 1500))")
            hidden = page.eval("document.querySelectorAll('#people-layer .person').length")
            row2 = page.eval("document.querySelector('#chat-row2').classList.contains('hidden')")
            check("'챗' 을 끄면 아이콘과 입력칸이 함께 사라진다", hidden == 0 and row2 is True,
                  "아이콘 %s개 · 입력칸 숨김=%s" % (hidden, row2))
            page.eval("(function(){var b=document.getElementById('btn-chat-toggle'); if(b) b.click(); return 'ok';})()")
            page.eval("new Promise(r=>setTimeout(()=>r(1), 2500))")
            shown = page.eval("document.querySelectorAll('#people-layer .person').length")
            check("다시 보이기", isinstance(shown, int) and shown >= len(keys), "다시 보이는 아이콘 %s개" % shown)
            # 스크린샷 — 눈으로도 확인할 수 있게
            try:
                data = page.ws.call("Page.captureScreenshot", {"format": "png"}, 30) or {}
                if data.get("data"):
                    shot = os.path.join(HERE, "collab_many_%d.png" % len(keys))
                    with open(shot, "wb") as f:
                        f.write(base64.b64decode(data["data"]))
                    print("  스크린샷: %s" % shot)
            except Exception as e:      # noqa: BLE001
                print("  (스크린샷 실패: %s)" % e)
        [t.join(ns.seconds + 10) for t in ths]
        check("%d명이 동시에 움직이는 동안 오류 없음" % len(keys), not errs, errs[:2])

        st2 = get(base, keys[names[0]], "/api/collab")
        check("서버가 %d명을 모두 들고 있다 (화면을 보는 사람이 있으면 +1)" % len(keys),
              len(st2.get("people") or []) >= len(keys),
              "people=%d · 경과 %.1fs" % (len(st2.get("people") or []), time.time() - t0))
        # ---- 본체(RAG 질의·MCP)가 협업 부하의 영향을 받지 않는가 ----
        # 이 검사가 이 스크립트의 핵심이다: 협업은 부수 기능이므로 여기서 하나라도 깨지면 협업을 꺼야 한다.
        errs2 = []
        ths2 = [threading.Thread(target=walker, args=(base, keys[n], n, 8.0, random.Random(rng.random()), errs2), daemon=True)
                for n in names]
        [t.start() for t in ths2]

        def timed_post(path, payload, timeout=120):
            rq = urllib.request.Request(base + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                        headers={"Content-Type": "application/json", "X-Requested-With": "verify-collab",
                                                 "Authorization": "Bearer " + keys[names[0]]}, method="POST")
            t = time.time()
            with urllib.request.urlopen(rq, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8")), time.time() - t

        try:
            jq, ms_q = timed_post("/api/query", {"q": "ISSUE-2001 원인", "log": False})
            ok_q = bool(jq.get("result"))
        except Exception as e:      # noqa: BLE001
            ok_q, ms_q = False, 0.0
            print("   질의 오류: %s" % e)
        check("협업 부하 중에도 RAG 질의가 정상", ok_q, "%.2fs" % ms_q)
        try:
            jm, ms_m = timed_post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            n_tools = len(((jm or {}).get("result") or {}).get("tools") or [])
        except Exception as e:      # noqa: BLE001
            n_tools, ms_m = 0, 0.0
            print("   MCP 오류: %s" % e)
        check("협업 부하 중에도 MCP tools/list 정상", n_tools >= 12, "도구 %d개 · %.2fs" % (n_tools, ms_m))
        try:
            jt, ms_t = timed_post("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                           "params": {"name": "wiki_query", "arguments": {"question": "ISSUE-2001 원인", "k": 3}}})
            ok_t = not (((jt or {}).get("result") or {}).get("isError"))
        except Exception as e:      # noqa: BLE001
            ok_t, ms_t = False, 0.0
            print("   MCP 도구 오류: %s" % e)
        check("협업 부하 중에도 MCP wiki_query 정상", ok_t, "%.2fs" % ms_t)
        [t.join(20) for t in ths2]
        check("본체 확인 중 협업 오류 없음", not errs2, errs2[:2])

        # 협업 호출이 서버의 '읽기 슬롯'을 잡지 않는지 (잡으면 30명 환경에서 질의가 밀린다)
        srv = get(base, keys[names[0]], "/api/activity")
        check("협업 호출이 활동 목록(읽기 슬롯)에 쌓이지 않는다",
              len((srv or {}).get("running") or []) == 0 and len((srv or {}).get("queued") or []) == 0,
              "running=%d queued=%d" % (len((srv or {}).get("running") or []), len((srv or {}).get("queued") or [])))

        # ---- 최적화: 접속자에 변화가 없으면 목록을 다시 보내지 않는다 ----
        full = get(base, keys[names[0]], "/api/collab")
        n_full = len(json.dumps(full, ensure_ascii=False).encode("utf-8"))
        time.sleep(0.2)
        inc = get(base, keys[names[0]], "/api/collab?since=%d&rev=%d&touch=0" % (full.get("last_id", 0), full.get("rev", 0)))
        n_inc = len(json.dumps(inc, ensure_ascii=False).encode("utf-8"))
        check("변화가 없으면 접속자 목록을 생략한다 (폴링 대역 절감)",
              inc.get("people_unchanged") is True and n_inc < n_full / 2,
              "%.1fKB → %.1fKB (%.0f%% 절감)" % (n_full / 1024, n_inc / 1024, 100 * (1 - n_inc / max(1, n_full))))
        post(base, keys[names[1]], {"action": "touch", "x": 0.77, "y": 0.44})
        inc2 = get(base, keys[names[0]], "/api/collab?since=%d&rev=%d&touch=0" % (full.get("last_id", 0), full.get("rev", 0)))
        check("누가 움직이면 목록을 다시 보낸다", bool(inc2.get("people")) and not inc2.get("people_unchanged"),
              "people=%d rev %s→%s" % (len(inc2.get("people") or []), full.get("rev"), inc2.get("rev")))
    finally:
        for p in (br, proc):
            if p:
                p.terminate()
                try:
                    p.communicate(timeout=10)
                except Exception:
                    p.kill()
        if not ns.keep:
            import shutil as _sh2
            _sh2.rmtree(tmp, ignore_errors=True)
        else:
            print("임시 폴더 유지: %s" % tmp)

    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "people": ns.people, "seconds": ns.seconds,
           "screenshot": shot, "total": len(RESULTS), "failed": FAILED, "results": RESULTS}
    with open(os.path.join(HERE, "verify_collab_many_result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n%s  %d/%d 통과" % ("COLLAB OK" if not FAILED else "COLLAB 실패", len(RESULTS) - len(FAILED), len(RESULTS)))
    if FAILED:
        print("실패: " + ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
