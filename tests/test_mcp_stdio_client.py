# -*- coding: utf-8 -*-
"""stdio MCP 클라이언트(`llmwiki/mcp_client.py: MCPClient`)의 **막히지 않음**을 고정한다.

다른 팀의 MCP 서버를 stdio 로 붙일 때 우리가 멈추면 안 된다. 2026-09-19 이전 구현은 두 경우에 교착됐다.

  1. **말이 없는 서버** — `request()` 가 락을 잡은 채 `stdout.readline()` 을 블로킹으로 돌아서
     `timeout_s` 가 영원히 검사되지 않았다(마감은 줄과 줄 사이에서만 봤다).
  2. **stderr 를 많이 쓰는 서버** — stderr 파이프를 세션 중 비우지 않아 버퍼가 차면 자식이 write 에서 멈추고,
     우리는 stdout 을 기다려 양쪽이 서로를 기다렸다. 로그를 많이 찍는 서버를 붙이면 그대로 걸린다.

지금은 stdout·stderr 리더 스레드가 계속 비우고 `request()` 는 큐에서 timeout 으로 꺼낸다.
이 테스트는 **실제 자식 프로세스**를 띄워 두 경우를 재현한다(외부 의존 없음 — 파이썬 한 줄짜리 가짜 서버).
"""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from llmwiki import mcp_client as mc          # noqa: E402

# 가짜 MCP 서버들 — stdin 에서 JSON 한 줄을 읽고 규칙대로 답한다.
# 공통: id 가 없는 메시지(알림 — 클라이언트가 보내는 notifications/initialized 등)는 답하지 않고 넘긴다.
SKIP_NOTIFY = "    if m.get('id') is None: continue\n"
SILENT = (
    "import sys,json\n"
    "for line in sys.stdin:\n"
    "    m=json.loads(line)\n"
    + SKIP_NOTIFY +
    "    if m.get('method')=='initialize':\n"
    "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'serverInfo':{'name':'silent'}}})+'\\n'); sys.stdout.flush()\n"
    "    # 그 밖의 요청에는 **아무 응답도 하지 않는다** (말 없는 서버)\n"
)
NOISY = (
    "import sys,json\n"
    "for line in sys.stdin:\n"
    "    m=json.loads(line)\n"
    "    sys.stderr.write('x'*200000+'\\n'); sys.stderr.flush()\n"   # 파이프 버퍼보다 훨씬 크게
    + SKIP_NOTIFY +
    "    r={'serverInfo':{'name':'noisy'}} if m.get('method')=='initialize' else ({'tools':[]} if m.get('method')=='tools/list' else {'ok':True})\n"
    "    sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r})+'\\n'); sys.stdout.flush()\n"
)
ASKS_BACK = (
    "import sys,json\n"
    "for line in sys.stdin:\n"
    "    m=json.loads(line)\n"
    + SKIP_NOTIFY +
    "    if m.get('method')=='initialize':\n"
    "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':99,'method':'sampling/createMessage','params':{}})+'\\n')\n"   # 서버 → 클라 요청
    "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'serverInfo':{'name':'asker'}}})+'\\n')\n"
    "    elif m.get('method')=='tools/list':\n"
    "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'tools':[]}})+'\\n')\n"
    "    else:\n"
    "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'ok':True}})+'\\n')\n"
    "    sys.stdout.flush()\n"
)
DIES = (
    "import sys,json\n"
    "line=sys.stdin.readline()\n"
    "m=json.loads(line)\n"
    "sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'serverInfo':{'name':'dies'}}})+'\\n'); sys.stdout.flush()\n"
    "sys.stderr.write('fatal: giving up\\n'); sys.stderr.flush()\n"
    "raise SystemExit(3)\n"
)


def cfg(src, timeout_s=6):
    return {"command": [sys.executable, "-u", "-c", src], "timeout_s": timeout_s}


class StdioClientTest(unittest.TestCase):
    def test_silent_server_times_out_and_does_not_hang(self):
        """응답하지 않는 서버에서 timeout_s 안에 오류로 끝나야 한다 (예전에는 영원히 멈췄다)."""
        c = mc.MCPClient("silent", cfg(SILENT, timeout_s=3))
        # initialize 는 답하지만 tools/list 는 답하지 않는다 → start() 가 그 자리에서 timeout
        t0 = time.time()
        with self.assertRaises(RuntimeError) as e:
            c.start()
        took = time.time() - t0
        self.assertIn("timeout", str(e.exception))
        self.assertLess(took, 20, "timeout_s=3 인데 %.1f초나 걸렸습니다 — 블로킹 읽기가 남아 있습니다" % took)
        c.close()

    def test_noisy_stderr_does_not_deadlock(self):
        """stderr 를 파이프 버퍼보다 많이 쓰는 서버와도 정상 왕복해야 한다."""
        c = mc.MCPClient("noisy", cfg(NOISY, timeout_s=15))
        try:
            c.start()                       # initialize + tools/list 왕복
            for _ in range(3):              # 매 호출마다 200KB 씩 더 쓴다
                r = c.request("ping", {})
                self.assertTrue((r.get("result") or {}).get("ok"))
            self.assertTrue(c.stderr_tail(), "stderr 를 읽어 두어야 실패 원인을 보고할 수 있다")
        finally:
            c.close()

    def test_server_request_gets_method_not_found(self):
        """서버가 우리에게 요청을 보내면 즉답해야 상대가 멈추지 않는다 (우리는 제공 기능이 없다)."""
        c = mc.MCPClient("asker", cfg(ASKS_BACK, timeout_s=8))
        try:
            c.start()                       # 서버 → 클라 요청이 섞여 와도 initialize 응답을 정확히 골라낸다
            self.assertEqual(c.tools, [])
            r = c.request("ping", {})
            self.assertTrue((r.get("result") or {}).get("ok"))
        finally:
            c.close()

    def test_dead_server_reports_stderr(self):
        """자식이 죽으면 기다리지 않고 stderr 와 함께 알린다."""
        c = mc.MCPClient("dies", cfg(DIES, timeout_s=8))
        t0 = time.time()
        with self.assertRaises(RuntimeError) as e:
            c.start()
        msg = str(e.exception)
        self.assertTrue("exited" in msg or "closed" in msg, msg)
        self.assertLess(time.time() - t0, 20)
        c.close()


if __name__ == "__main__":
    unittest.main()
