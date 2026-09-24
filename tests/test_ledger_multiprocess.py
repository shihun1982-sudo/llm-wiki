# -*- coding: utf-8 -*-
"""요청 원장에 **여러 프로세스가 동시에 써도** 줄이 깨지지 않는가 (2026-09-24).

왜 이 테스트가 있나
  원장은 서버 프로세스만 쓰는 것이 아니다. CLI 질의·빌드는 호출마다 **새 프로세스**이고,
  MCP stdio 도 별도 프로세스이며, 이들이 같은 `data/ledger/req-<날짜>.jsonl` 에 append 한다.

  처음 구현은 `open(path, "a")` + `f.write(...)` 였는데 두 가지가 겹쳐 줄이 깨졌다:
    ① 텍스트 모드의 8KB 버퍼 때문에 큰 배치가 여러 번의 OS 쓰기로 쪼개진다.
    ② **Windows 의 O_APPEND 는 프로세스 간 원자적이지 않다** — CRT 가 "끝으로 seek 후 write" 를 하므로
       두 프로세스가 같은 끝 위치를 잡으면 한쪽이 다른 쪽의 줄을 덮어쓴다.
  깨진 줄은 읽을 때 조용히 버려지므로 결과는 **"요청이 기록 없이 사라진다"** — 이 기능이 막으려는 바로 그것이다.
  30명 동시 부하 시험(`tools/verify/verify_three_surface_load.py`)에서 CLI 질의 18건 중 3~7건이 이렇게 사라졌고,
  파일에는 `ing"}` 같은 꼬리 조각만 남아 있었다.

여기서 지키는 것
  1. 여러 프로세스가 동시에 써도 **깨진 줄 0개**.
  2. 모든 프로세스가 쓴 줄이 **하나도 빠지지 않는다**(프로세스가 짧게 살다 가도 — atexit 으로 비운다).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 자식 프로세스: 원장을 켜고, **다른 자식들과 같은 시각에** 줄을 쏟아낸 뒤 flush 없이 그냥 끝낸다.
#   - 공통 시작 시각(argv[3])까지 기다리는 것이 핵심이다. 순서대로 시작하면 쓰기가 겹치지 않아
#     프로세스 간 경쟁이 재현되지 않는다(실제로 그래서 처음 만든 판이 결함을 놓쳤다).
#   - 중간에 쉬면서 여러 배치로 나눠 쓴다 — 한 번에 몰아 쓰면 배치가 한 덩어리라 겹칠 틈이 좁다.
#   - 끝낼 때 flush 를 부르지 않는다. 종료 시 비워 주는 처리가 없으면 여기서 줄이 사라진다.
CHILD = r"""
import os, sys, time
sys.path.insert(0, %(root)r)
from llmwiki import reqledger as L
L.configure({"enabled": True, "dir": %(dir)r, "flush_ms": 10})
tag, n, start = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
while time.time() < start:
    time.sleep(0.002)
for i in range(n):
    t = L.open_(L.new_token(), kind="query", origin="cli", user=tag,
                label="%%s-%%04d 아주 긴 라벨 " %% (tag, i) + "x" * 400)
    L.close(t, "done", ms=1.0, request_id=i, note="y" * 400)
    if i %% 5 == 4:
        time.sleep(0.004)
"""


class LedgerMultiProcessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="llmwiki-led-mp-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dir = os.path.join(self.tmp, "ledger")

    def _read(self):
        """(정상 레코드, 깨진 줄 수)"""
        recs, bad = [], 0
        for fn in sorted(os.listdir(self.dir)) if os.path.isdir(self.dir) else []:
            if not fn.endswith(".jsonl"):
                continue
            with open(os.path.join(self.dir, fn), encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        recs.append(json.loads(line))
                    except Exception:
                        bad += 1
        return recs, bad

    def test_concurrent_processes_do_not_corrupt_lines(self):
        src = os.path.join(self.tmp, "child.py")
        with open(src, "w", encoding="utf-8") as f:
            f.write(CHILD % {"root": ROOT, "dir": self.dir})
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        env.pop("LLMWIKI_LEDGER_DIR_PATH", None)      # 이 테스트는 자기 폴더를 직접 지정한다
        per, tags = 150, ["p%d" % i for i in range(6)]
        # 모두 띄운 뒤 **같은 시각**에 쓰기 시작한다 (프로세스 시작 시간이 들쭉날쭉해도 겹치게).
        start = __import__("time").time() + 2.0
        procs = [subprocess.Popen([sys.executable, src, t, str(per), "%.3f" % start], cwd=ROOT, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for t in tags]
        for p in procs:
            out, err = p.communicate(timeout=180)
            self.assertEqual(0, p.returncode, "자식 프로세스 실패: %s" % (err or b"")[-300:])

        recs, bad = self._read()
        self.assertEqual(0, bad,
                         "깨진 줄 %d개 — 여러 프로세스의 쓰기가 한 줄 안에서 섞였다. "
                         "깨진 줄의 요청은 읽을 때 버려지므로 곧 '사라진 요청' 이다" % bad)

        opens = [r for r in recs if r.get("ev") == "open"]
        closes = {r.get("token") for r in recs if r.get("ev") == "close"}
        self.assertEqual(len(tags) * per, len(opens),
                         "접수 기록 %d개가 나와야 하는데 %d개다 — 프로세스가 끝날 때 버퍼가 버려졌을 수 있다"
                         % (len(tags) * per, len(opens)))
        missing = [r["token"] for r in opens if r.get("token") not in closes]
        self.assertFalse(missing, "종료 기록이 없는 항목 %d개" % len(missing))
        by_tag = {}
        for r in opens:
            by_tag[r.get("user")] = by_tag.get(r.get("user"), 0) + 1
        self.assertEqual({t: per for t in tags}, by_tag, "프로세스별로 쓴 줄 수가 맞지 않는다")


if __name__ == "__main__":
    unittest.main()
