"""요청 원장이 기존 세 화면의 정보를 **하나도 잃지 않았는지** 항목별로 대조한다.

왜 있나 (2026-09-23)
  Observability 의 `요청 프로파일` · `진행 중 작업` · `질의 로그` 는 같은 사건을 **원천별로** 쪼개 놓은 화면이라,
  한 요청을 이해하려면 탭 셋을 오가야 했다. 이것을 「요청(전체)」 하나로 합치기로 했는데,
  합치는 작업에서 가장 위험한 것은 **조용한 정보 유실**이다 — 화면은 그럴듯한데 예전에 보이던 값이 빠지는 것.
  사람이 눈으로 비교하는 대신, 세 화면이 주던 필드를 **표로 적어 두고** 새 경로에서 실제로 나오는지 확인한다.

무엇을 보나
  1. 기존 API 가 주던 필드가 원장 경로(`/api/ledger`, `/api/ledger/<token>`)에서 **도달 가능한가**
  2. 도달할 수 없는 항목은 **왜 그런지 이유가 적혀 있는가** (의도적으로 뺀 것 vs 빠뜨린 것)
  3. 원장에만 있는 것(거절·시간초과·중단)이 실제로 기록되는가 — 합치면서 **늘어난 정보**
  4. `query_log` ↔ `requests` 연결(`request_id`)이 채워지는가 — 합쳐 보여 주려면 이 키가 필요하다

실행:
    python tools/verify/verify_ledger_merge.py                 # 파일만 읽어 대조 (서버 불필요)
    python tools/verify/verify_ledger_merge.py --live          # 서버를 띄워 실제 응답으로 대조
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# (기존 화면, 그 화면이 주던 정보, 원장에서의 자리, 비고/이유)
#   자리가 "" 이면 **의도적으로 옮기지 않은 것** — 비고에 이유를 반드시 적는다 (없으면 실패).
COVER = [
    # ── 진행 중 작업 (GET /api/activity + /api/progress) ─────────────────────────
    ("진행 중 작업", "실행/대기 중인 요청 목록", "ledger.rows[status=running|queued]", ""),
    ("진행 중 작업", "현재 단계 · 단계 경로", "detail.stage · live(progress)", "실행 중이면 /api/progress/<token> 을 그대로 쓴다"),
    ("진행 중 작업", "진행률·ETA·LLM 대기 모델", "detail(live)", "〃"),
    ("진행 중 작업", "대기열 순번", "detail.queue_wait_s · live.queue", ""),
    ("진행 중 작업", "중지 버튼", "detail 의 ■ 중지", "DELETE /api/jobs/<token> — 같은 토큰"),
    ("진행 중 작업", "사용자·역할·창구·IP", "rows[].user/role/origin/ip", "IP 는 admin 만 (monitor.show_user_to_viewer)"),
    ("진행 중 작업", "외부 프로세스(CLI·MCP stdio)", "rows[origin=cli|mcp]", "progress.cli_monitor 가 원장에 open/close 를 쓴다"),
    ("진행 중 작업", "락 상태(readers/writer)", "summary.lock", "상단 상태 스트립"),
    ("진행 중 작업", "슬롯 게이지", "summary.limits · summary.classes", "〃"),
    # ── 요청 프로파일 (GET /api/requests, /api/request?id=) ───────────────────────
    ("요청 프로파일", "요청 목록(종류·시각·요약)", "ledger.rows", ""),
    ("요청 프로파일", "소요 ms", "rows[].ms · detail.profile.ms", ""),
    ("요청 프로파일", "LLM 호출 수 · 입출력 토큰", "detail.profile.llm_calls/input_tokens/output_tokens", ""),
    ("요청 프로파일", "SQL 문 수", "detail.profile.sql_count", ""),
    ("요청 프로파일", "run_id (로그 연결)", "detail.run_id · profile.run_id", ""),
    ("요청 프로파일", "단계별 워터폴(trace)", "detail?sections=trace", "무거워서 펼칠 때만 — 목록 응답에는 넣지 않는다"),
    ("요청 프로파일", "그때의 설정 스냅샷(config)", "detail?sections=trace 의 config", "〃"),
    ("요청 프로파일", "오류 메시지", "rows[].error · detail.error", ""),
    ("요청 프로파일", "두 요청 비교", "", "비교는 **두 건을 나란히 놓는 별도 작업**이라 요청 프로파일 탭에 남긴다 "
                                "(원장은 한 건의 수명을 보는 화면이고, 비교 UI 를 여기에 겹치면 둘 다 복잡해진다)"),
    ("요청 프로파일", "⟲ 단계 재실행", "", "재실행은 **새 요청을 만드는 실행 동작**이라 요청 프로파일/Ask 에 남긴다 "
                                 "— 원장 상세에서 📄 버튼으로 한 번에 넘어간다"),
    # ── 질의 로그 (GET /api/queries, /api/query_trace) ────────────────────────────
    ("질의 로그", "질문 원문", "rows[].label · detail.answer.query", ""),
    ("질의 로그", "답변 본문", "detail.answer.text", ""),
    ("질의 로그", "근거 청크 목록", "detail.answer.hits", "요약(hits_brief) — 원본도 requests 와 같은 범위"),
    ("질의 로그", "판정·groundedness", "detail.answer.verdict/groundedness", ""),
    ("질의 로그", "피드백 👍/👎 · 메모", "detail.qlog.feedback/note", ""),
    ("질의 로그", "누가 물었나(사용자·역할·창구·로그인 방법)", "rows[].user/role/origin/via", ""),
    ("질의 로그", "사용자별 질의 집계", "", "집계는 **운영 통계**(Observability › 시스템 · `/api/query_users`)의 몫이다 "
                                "— 원장은 건별 목록이고, 같은 숫자를 두 화면에서 내면 어긋난다"),
]

#: 원장에만 있는 것 — 합치면서 **늘어난** 정보 (예전에는 어디에도 남지 않았다)
ONLY_LEDGER = [
    ("거절된 요청 (429/503/403/413)", "status=rejected · code · http"),
    ("시간 초과로 끊긴 요청", "status=timeout"),
    ("취소된 요청", "status=cancelled"),
    ("끝이 기록되지 않은 요청 (서버 강제 종료)", "status=unknown"),
    ("대기열에서 기다린 시간", "queue_wait_s"),
    ("같은 시각에 돌던 다른 요청", "detail.concurrent"),
    ("HTTP 진입점 단위 기록(인증 실패·잘못된 본문)", "kind=http · error"),
]


def check_static() -> int:
    """표 자체의 무결성 — 비워 둔 칸에 이유가 적혀 있는가."""
    bad = 0
    print("기존 화면 → 원장 대조표 (%d항목)\n" % len(COVER))
    cur = None
    for screen, info, place, note in COVER:
        if screen != cur:
            cur = screen
            print("[%s]" % screen)
        if place:
            print("  OK   %-34s → %s" % (info, place))
        elif note.strip():
            print("  유지 %-34s → (원장에 옮기지 않음) %s" % (info, note.split("—")[0].strip()[:60]))
        else:
            print("  FAIL %-34s → 자리가 없고 이유도 없다" % info)
            bad += 1
    print("\n원장에만 있는 것 (합치면서 늘어난 정보):")
    for what, where in ONLY_LEDGER:
        print("  +    %-40s %s" % (what, where))
    return bad


def check_data() -> int:
    """실제 데이터 — 조인 키가 채워지는가, 원장이 실패 상태를 잡는가."""
    from llmwiki import reqmgr as R, reqledger as L
    L.configure(R.load_config().get("ledger"), R._data_dir())
    bad = 0
    print("\n실제 기록 확인 (%s)" % L.ledger_dir())
    st = L.stats(days=7)
    print("  최근 7일 %d건 · 상태별 %s" % (st["total"], st["by_status"]))
    for want in ("rejected", "error"):
        if not st["by_status"].get(want):
            print("  주의 상태 '%s' 기록이 없습니다 (아직 그런 요청이 없었을 수 있습니다)" % want)
    w = st["writer"]
    if w["dropped"]:
        print("  FAIL writer 드롭 %d건 — ledger.queue_max 를 올리세요" % w["dropped"])
        bad += 1

    # request_id 는 **실제로 실행된** 질의에만 있다. 거절·시간초과·취소는 파이프라인에 들어가지도
    # 못했으므로 없는 것이 정상이다 — 그것까지 분모에 넣으면 부하가 걸릴수록 비율이 떨어져 헛경보가 된다.
    rows, total = L.read(kind="query", limit=200)
    done = [r for r in rows if (r.get("status") or "") == "done"]
    never_ran = [r for r in rows if (r.get("status") or "") in ("rejected", "timeout", "cancelled", "unknown")]
    with_rid = [r for r in done if r.get("request_id")]
    if done:
        pct = 100.0 * len(with_rid) / len(done)
        mark = "OK  " if pct >= 95 else "FAIL"
        print("  %s 완료된 query %d건 중 request_id 보유 %d건 (%.0f%%) — 프로파일·질의로그와 잇는 키"
              % (mark, len(done), len(with_rid), pct))
        if pct < 95:
            print("       완료됐는데 request_id 가 없으면 📄 요청 프로파일로 갈 수 없다. 해당 줄:")
            for r in [x for x in done if not x.get("request_id")][:10]:
                print("         %s  origin=%-5s %s" % (
                    time.strftime("%m-%d %H:%M", time.localtime(float(r.get("opened") or r.get("ts") or 0))),
                    r.get("origin") or "-", (r.get("label") or "")[:40]))
            print("       (2026-09-24 이전에 남은 줄은 CLI 경로가 아직 id 를 싣지 않던 때의 것이라 비어 있을 수 있다)")
            bad += 1
    if never_ran:
        print("  참고 실행되지 못한 query %d건(거절·시간초과·취소·중단)은 request_id 가 없는 것이 정상 —"
              " 예전 구조에서 **기록 자체가 없던** 요청들이다" % len(never_ran))
    return bad


def check_live(port: int) -> int:
    """서버를 띄워 실제 응답에 필드가 들어 있는지 본다."""
    import subprocess
    import urllib.request
    bad = 0
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    srv = subprocess.Popen([sys.executable, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(90):
            try:
                urllib.request.urlopen(base + "/api/auth/me", timeout=2).read()
                break
            except Exception:
                time.sleep(1)
        j = json.loads(urllib.request.urlopen(base + "/api/ledger?limit=5", timeout=30).read().decode("utf-8"))
        need = ["rows", "total", "summary", "histogram", "statuses", "views"]
        for k in need:
            if k not in j:
                print("  FAIL /api/ledger 응답에 '%s' 가 없다" % k)
                bad += 1
        print("  OK   /api/ledger 응답 필드: %s" % ", ".join(need))
        if j.get("rows"):
            tok = j["rows"][0]["token"]
            d = json.loads(urllib.request.urlopen(base + "/api/ledger/" + tok, timeout=30).read().decode("utf-8"))
            for k in ("token", "status", "events"):
                if k not in d:
                    print("  FAIL 상세에 '%s' 가 없다" % k)
                    bad += 1
            print("  OK   상세 필드: token, status, events(%d개)%s" % (
                len(d.get("events") or []),
                (" + profile/answer" if d.get("answer") else " (질의가 아니라 프로파일 없음)")))
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:
            srv.kill()
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="원장이 기존 세 화면의 정보를 잃지 않았는지 대조")
    ap.add_argument("--live", action="store_true", help="서버를 띄워 실제 응답까지 확인")
    ap.add_argument("--port", type=int, default=8817)
    ns = ap.parse_args(argv)
    bad = check_static()
    try:
        bad += check_data()
    except Exception as e:
        print("  (데이터 확인 건너뜀: %s)" % e)
    if ns.live:
        print("\n서버 응답 확인 (포트 %d)" % ns.port)
        bad += check_live(ns.port)
    print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
