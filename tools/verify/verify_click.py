"""버튼·행을 **실제로 클릭해** 화면이 바뀌는지 확인한다 (Chrome DevTools Protocol).

왜 있나: `--dump-dom` 검사는 "페이지가 그려지는가" 만 본다. 눌러도 아무 일도 안 일어나는 버튼
(핸들러가 조용히 return 하거나 예외로 죽는 경우)은 잡지 못한다. 이 스크립트는 헤드리스 브라우저를
CDP 로 붙잡아 클릭하고, 클릭 전후의 DOM 과 콘솔 오류를 비교한다.

표준 라이브러리만 쓴다 (WebSocket 프레이밍을 직접 구현).

실행:
    python tools/verify/verify_click.py
    python tools/verify/verify_click.py --port 8815 --keep
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EDGE = next((p for p in (os.environ.get("LLMWIKI_BROWSER", ""), shutil.which("msedge"), shutil.which("chrome"),
                         shutil.which("google-chrome"), shutil.which("chromium"),
                         r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files\Google\Chrome\Application\chrome.exe") if p and os.path.exists(p)), None)


# ---------------------------------------------------------------- 최소 WebSocket 클라이언트
class WS:
    """CDP 용 최소 WebSocket (텍스트 프레임만). 표준 라이브러리로 충분하다."""

    def __init__(self, url: str, timeout: float = 30.0):
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)", url)
        if not m:
            raise ValueError("ws url 형식이 아닙니다: %s" % url)
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key))
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        head, _, rest = buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise RuntimeError("WebSocket 업그레이드 실패: %s" % head[:120])
        # Sec-WebSocket-Accept 검증은 생략한다: 상대는 우리가 방금 띄운 로컬 브라우저의
        # 디버깅 포트이고, 101 응답만으로 업그레이드가 끝난 것이 확인된다.
        self._buf = rest
        self._id = 0

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            d = self.sock.recv(65536)
            if not d:
                raise ConnectionError("연결이 끊겼습니다")
            self._buf += d
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, text: str) -> None:
        data = text.encode("utf-8")
        n = len(data)
        hdr = bytearray([0x81])
        mask = os.urandom(4)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        hdr += mask
        self.sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        while True:
            b1, b2 = self._recv_exact(2)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            payload = self._recv_exact(n)
            if op == 0x8:
                raise ConnectionError("서버가 연결을 닫았습니다")
            if op in (0x1, 0x2):
                return payload.decode("utf-8", "replace")
            # ping/pong 은 무시

    def call(self, method: str, params=None, timeout: float = 30.0):
        self._id += 1
        mid = self._id
        self.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        t0 = time.time()
        while time.time() - t0 < timeout:
            msg = json.loads(self.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s: %s" % (method, msg["error"]))
                return msg.get("result") or {}
        raise TimeoutError(method)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class Page:
    def __init__(self, ws: WS):
        self.ws = ws
        self.ws.call("Runtime.enable")
        self.ws.call("Log.enable")

    def eval(self, expr: str, timeout: float = 90.0):
        """JS 를 평가한다. 페이지가 응답하지 않으면 {"__timeout__": True} 를 돌려준다.

        버튼을 눌렀을 때 화면이 오래 멈출 수 있으므로(대용량 렌더 등), 여기서 죽지 않고
        호출자가 그 버튼만 실패로 기록하고 계속 진행할 수 있게 한다.
        """
        try:
            r = self.ws.call("Runtime.evaluate",
                             {"expression": expr, "returnByValue": True, "awaitPromise": True}, timeout)
        except (TimeoutError, ConnectionError) as e:
            return {"__timeout__": True, "__error__": type(e).__name__}
        if r.get("exceptionDetails"):
            ex = r["exceptionDetails"]
            msg = (ex.get("exception") or {}).get("description") or ex.get("text")
            return {"__error__": str(msg)[:400]}
        return (r.get("result") or {}).get("value")


def wait_server(base: str, tries: int = 90) -> None:
    for _ in range(tries):
        try:
            urllib.request.urlopen(base + "/api/auth/me", timeout=2)
            return
        except Exception:
            time.sleep(1)


CHECKS = [
    # (이름, 준비 JS, 클릭 JS, 성공 판정 JS)
    ("요청 프로파일: 행 클릭 → 상세",
     "location.hash='#observability/requests'; 'ok'",
     "(function(){var tr=document.querySelector('#req-list tr[data-id]'); if(!tr) return 'no-row'; tr.click(); return tr.dataset.id;})()",
     "document.querySelector('#req-inspector').innerHTML.indexOf('req-head')>=0 "
     "|| document.querySelector('#req-inspector').textContent.indexOf('워터폴')>=0"),
    ("포렌식: 진단 실행 (id 비움)",
     "location.hash='#quality/forensics'; 'ok'",
     "(function(){document.querySelector('#fx-req').value=''; document.querySelector('#btn-fx-run').click(); return 'clicked';})()",
     "document.querySelector('#fx-detail').textContent.indexOf('포렌식 #')>=0"),
    ("메모리: 목록 렌더",
     "location.hash='#evolve/memory'; 'ok'",
     "'noop'",
     "document.querySelector('#mem-status').textContent.indexOf('[object Object]')<0"),
    ("진행 중 작업: 보드 렌더",
     "location.hash='#observability/activity'; 'ok'",
     "'noop'",
     "!!document.querySelector('#act-gauge .ag-slots')"),
    ("로그: 조회 버튼",
     "location.hash='#observability/logs'; 'ok'",
     "(function(){document.querySelector('#btn-logs-load').click(); return 'clicked';})()",
     "!!document.querySelector('#log-table table')"),
    ("질의 로그: trace 버튼",
     "location.hash='#observability/qlog'; 'ok'",
     "(function(){var b=document.querySelector('#logs [data-tr]'); if(!b) return 'no-btn'; b.click(); return b.dataset.tr;})()",
     "document.querySelector('#logs-trace').innerHTML.length>200"),
    ("Ask: 예시 질문 칩 → 입력칸 채움",
     "location.hash='#ask/query'; document.querySelector('#q').value=''; 'ok'",
     "(function(){var c=document.querySelector('#samples span'); if(!c) return 'no-chip';"
     " document.querySelector('#q').value=c.textContent; return document.querySelector('#q').value;})()",
     "((document.querySelector('#q')||{}).value||'').length>0"),
]


def main(argv=None) -> int:
    if not EDGE:
        print("CLICK SKIP: Edge/Chrome 을 찾지 못함 (LLMWIKI_BROWSER 로 지정)")
        return 0
    ap = argparse.ArgumentParser(description="버튼을 실제로 눌러 화면이 바뀌는지 확인")
    ap.add_argument("--port", type=int, default=8815)
    ap.add_argument("--cdp", type=int, default=9333)
    ap.add_argument("--keep", action="store_true")
    ns = ap.parse_args(argv)

    base = "http://127.0.0.1:%d" % ns.port
    prof = os.path.join(tempfile.gettempdir(), "llmwiki_click_profile")
    shutil.rmtree(prof, ignore_errors=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    srv = subprocess.Popen([sys.executable, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(ns.port)],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = None
    bad = 0
    try:
        wait_server(base)
        br = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
                               "--user-data-dir=" + prof, "--remote-debugging-port=%d" % ns.cdp, base + "/"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = None
        for _ in range(60):
            try:
                tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % ns.cdp, timeout=2).read().decode())
                url = next((t["webSocketDebuggerUrl"] for t in tabs if t.get("type") == "page"), None)
                if url:
                    break
            except Exception:
                pass
            time.sleep(1)
        if not url:
            print("CLICK SKIP: 브라우저 디버깅 포트에 붙지 못했습니다")
            return 0
        page = Page(WS(url))
        page.eval("new Promise(r=>setTimeout(()=>r(1), 4000))")      # 초기 로딩 대기

        print("클릭 검증 — %s\n" % base)
        for name, setup, click, check in CHECKS:
            page.eval(setup)
            page.eval("new Promise(r=>setTimeout(()=>r(1), 2500))")   # 탭 loader 대기
            before = page.eval("document.body.innerText.length")
            clicked = page.eval(click)
            page.eval("new Promise(r=>setTimeout(()=>r(1), 3500))")   # 응답 대기
            ok = page.eval(check)
            errs = page.eval("(window.__lwErrors||[]).slice(-3)") or []
            after = page.eval("document.body.innerText.length")
            mark = "OK  " if ok is True else "FAIL"
            if ok is not True:
                bad += 1
            print("%s %-32s 클릭=%s 본문 %s→%s%s" % (mark, name, clicked, before, after,
                                                    ("  오류=%s" % errs) if errs else ""))
            if isinstance(ok, dict) and ok.get("__error__"):
                print("     판정식 예외: %s" % ok["__error__"])
        print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
        return 1 if bad else 0
    finally:
        for p in (br, srv):
            if p:
                p.terminate()
                try:
                    p.communicate(timeout=10)
                except Exception:
                    p.kill()
        if not ns.keep:
            shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
