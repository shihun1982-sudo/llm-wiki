"""**전 검증 한 번에** — 모든 하네스를 순서대로 돌리고 결과를 한 표로 모은다.

왜 있나: 검증 스크립트가 12개로 늘었다. 하나씩 돌리면 (a) 빠뜨리기 쉽고 (b) 문서에 적는 숫자가
손으로 옮겨지다 낡는다. 실제로 README 와 검증 보고서의 "단위 181 · Web 285 · 버튼 90" 은
코드가 늘어난 뒤에도 그대로 남아 있었다.

그래서 이 스크립트가 **유일한 출처**가 된다:
  - 모든 하네스를 돌려 통과/실패 수를 긁어모으고
  - `tools/verify/verify_all_result.json` 에 기계가 읽을 형태로 남기고
  - 문서에 그대로 붙일 수 있는 마크다운 표를 출력한다
  - `verify_docs.py` 가 이 JSON 과 문서의 숫자를 대조한다 (숫자가 낡으면 검증이 실패한다)

실행:
    python tools/verify/verify_all.py                 # 기본 묶음 (약 15~25분)
    python tools/verify/verify_all.py --quick         # 브라우저·멍키 제외 (약 8분)
    python tools/verify/verify_all.py --full          # MCP 전체 + 30명 협업 시뮬레이션까지
    python tools/verify/verify_all.py --only unit,web
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_all_result.json")

# (키, 이름, argv, 결과를 긁는 정규식(pass, total) 또는 "ok" 판정용, 기본 묶음 포함 여부)
SUITES = [
    ("unit", "단위 테스트", [PY, "-m", "unittest", "discover", "-s", "tests"],
     r"Ran (?P<total>\d+) tests", "quick"),
    ("stress", "스트레스 (30명 동시)", [PY, "-m", "unittest", "tests.test_concurrency_0915.StressTest"],
     r"Ran (?P<total>\d+) tests", "quick"),
    ("docs", "문서 ↔ 코드 정합", [PY, "tools/verify/verify_docs.py"], None, "quick"),
    ("align", "CLI · Web · MCP 정렬", [PY, "tools/verify/verify_surface_align.py"], None, "quick"),
    ("tri_surface", "세 창구 동작 동등성 (CLI 프로세스 · Web · MCP)", [PY, "tools/verify/verify_tri_surface.py", "--port", "8879"],
     r"검사 (?P<total>\d+)건 · 통과 (?P<passed>\d+)", "quick"),
    ("stage_align", "단계 정렬 (코드 ↔ 레지스트리 ↔ 라벨 ↔ 손잡이)", [PY, "tools/verify/verify_stage_align.py"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "quick"),
    ("settings_sync", "설정 UI ↔ 파일 ↔ 서버 양방향", [PY, "tools/verify/verify_settings_sync.py", "--port", "8934"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "quick"),
    ("llm_switch", "LLM 연결 전환 (API ↔ headless, 세 창구)", [PY, "tools/verify/verify_llm_switch.py", "--port", "8973"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "quick"),
    ("build_load", "빌드 중 서비스 (30명 동시 · 채널별)", [PY, "tools/verify/verify_build_load.py", "--port", "8975", "--users", "30"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "quick"),
    ("cli", "CLI 전수", [PY, "tools/verify/verify_cli.py"],
     r"CLI 검증: (?P<total>\d+) 명령 중 (?P<passed>\d+) 통과", "quick"),
    ("web", "Web API 전수", [PY, "tools/verify/verify_web.py"],
     r"WEB 검증: (?P<total>\d+) 요청 중 (?P<passed>\d+) 통과", "quick"),
    ("mcp", "MCP 종단", [PY, "tools/verify/verify_mcp.py", "--quick"],
     r"MCP (?:OK|실패)\s+(?P<passed>\d+)/(?P<total>\d+) 통과", "quick"),
    ("wiring", "UI 배선", [PY, "tools/verify/verify_ui_wiring.py"], None, "quick"),
    ("browser", "브라우저 렌더", [PY, "tools/verify/verify_browser.py", "--port", "8901", "--cdp", "9401"], None, "base"),
    ("responsive", "창 크기 대응 (폭 5종)", [PY, "tools/verify/verify_responsive.py", "--port", "8904"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "base"),
    ("buttons", "버튼 전수", [PY, "tools/verify/verify_buttons.py", "--port", "8902", "--cdp", "9402"],
     r"버튼 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "base"),
    # 앙상블 편집기는 "켰는데 멤버 0개" 라는 **조용히 아무 일도 안 하는** 상태를 만들 수 있어 따로 눌러 본다.
    ("ensemble-ui", "앙상블 편집기 (실제 클릭)", [PY, "tools/verify/verify_ensemble_ui.py"],
     r"(?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "base"),
    # 워터폴은 DOM·API 검사를 다 통과하면서도 **레이아웃만 틀릴 수 있다** — 픽셀을 재서 본다.
    ("waterfall", "워터폴 기하 (픽셀 측정)", [PY, "tools/verify/verify_trace_waterfall.py"],
     r"(?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "base"),
    ("security_ui", "보안 · 사용자 화면", [PY, "tools/verify/verify_security_ui.py", "--port", "8903", "--cdp", "9403"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "base"),
    ("rerun_ui", "단계 재실행 · 작업 상세", [PY, "tools/verify/verify_rerun_ui.py", "--port", "8905", "--cdp", "9405"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "base"),
    ("collab", "협업 다중 접속 (10명)", [PY, "tools/verify/verify_collab_many.py", "--people", "10",
                                  "--port", "8906", "--cdp", "9406"],
     r"COLLAB (?:OK|실패)\s+(?P<passed>\d+)/(?P<total>\d+) 통과", "base"),
    ("act_load", "작업 상세 로딩 시간", [PY, "tools/verify/verify_act_detail_load.py", "--port", "8909", "--cdp", "9409"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "base"),
    ("schedule_ui", "스케줄 화면", [PY, "tools/verify/verify_schedule_ui.py", "--port", "8910", "--cdp", "9410"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "base"),
    ("timeout", "타임아웃 · 취소 내성", [PY, "tools/verify/verify_timeouts.py", "--port", "8907"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "base"),
    ("soak", "다중 클라이언트 혼합 부하", [PY, "tools/verify/verify_soak.py", "--port", "8971", "--seconds", "60", "--clients", "16"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "base"),
    ("monkey", "무작위 입력 내성", [PY, "tools/verify/verify_monkey.py"], None, "base"),
    # --full 에서만
    # 실제 모델은 느리다(20분+). 붙는 모델이 없으면 스스로 SKIP(exit 0) 하므로 CI 에서도 안전하다.
    ("live_models", "실제 모델 종단 (mock 아님)", [PY, "tools/verify/verify_live_models.py"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "full"),
    ("mcp_full", "MCP 종단 (전체)", [PY, "tools/verify/verify_mcp.py"],
     r"MCP (?:OK|실패)\s+(?P<passed>\d+)/(?P<total>\d+) 통과", "full"),
    ("collab30", "협업 다중 접속 (30명)", [PY, "tools/verify/verify_collab_many.py", "--people", "30",
                                    "--port", "8908", "--cdp", "9408"],
     r"COLLAB (?:OK|실패)\s+(?P<passed>\d+)/(?P<total>\d+) 통과", "full"),
    ("soak_long", "다중 클라이언트 혼합 부하 (장시간 · 32클라이언트)",
     [PY, "tools/verify/verify_soak.py", "--port", "8972", "--seconds", "240", "--clients", "32"],
     r"검사 (?P<total>\d+)개 중 (?P<passed>\d+)개 통과", "full"),
]


def run_one(key, name, argv, pat, timeout_s):
    t0 = time.time()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout_s)
        out = (p.stdout or "") + "\n" + (p.stderr or "")
        code = p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        code = -9
    secs = time.time() - t0
    passed = total = None
    if pat:
        m = None
        for m in re.finditer(pat, out):
            pass                      # 마지막 것을 쓴다 (요약 줄은 맨 끝에 나온다)
        if m:
            g = m.groupdict()
            total = int(g["total"]) if g.get("total") else None
            passed = int(g["passed"]) if g.get("passed") else None
            if passed is None and total is not None:
                # unittest: "Ran N tests" + OK 면 전부 통과
                passed = total if re.search(r"^OK\b", out, re.M) else None
                if passed is None:
                    fails = len(re.findall(r"^(FAIL|ERROR):", out, re.M))
                    passed = max(0, total - fails)
    ok = (code == 0)
    return {"key": key, "name": name, "argv": " ".join(argv[1:]), "ok": ok, "code": code,
            "passed": passed, "total": total, "seconds": round(secs, 1),
            "tail": "\n".join(out.strip().split("\n")[-12:])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="모든 검증 하네스를 돌려 한 표로")
    ap.add_argument("--quick", action="store_true", help="브라우저·멍키 등 오래 걸리는 것 제외")
    ap.add_argument("--full", action="store_true", help="MCP 전체·30명 시뮬레이션까지")
    ap.add_argument("--only", default="", help="키 쉼표 (예: unit,web,mcp)")
    ap.add_argument("--timeout", type=int, default=3600, help="개별 하네스 제한(초)")
    ns = ap.parse_args(argv)

    only = {x.strip() for x in ns.only.split(",") if x.strip()}
    rows = []
    for key, name, cmd, pat, tier in SUITES:
        if only:
            if key not in only:
                continue
        elif tier == "full" and not ns.full:
            continue
        elif tier == "base" and ns.quick:
            continue
        print("\n▶ %s  (%s)" % (name, " ".join(cmd[1:])), flush=True)
        r = run_one(key, name, cmd, pat, ns.timeout)
        rows.append(r)
        mark = "OK  " if r["ok"] else "FAIL"
        cnt = ("%s/%s" % (r["passed"], r["total"])) if r["total"] is not None else ("OK" if r["ok"] else "-")
        print("  %s %-26s %-12s %6.1fs" % (mark, name, cnt, r["seconds"]), flush=True)
        if not r["ok"]:
            print("  ── 마지막 출력 ──\n%s" % "\n".join("    " + x for x in r["tail"].split("\n")), flush=True)

    bad = [r for r in rows if not r["ok"]]
    res = {"ts": time.time(), "when": time.strftime("%Y-%m-%d %H:%M"), "rows": rows,
           "ok": not bad, "python": sys.version.split()[0]}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    table = ["| 무엇 | 명령 | 결과 |", "|---|---|---|"]
    for r in rows:
        cnt = ("**%s/%s 통과**" % (r["passed"], r["total"])) if r["total"] is not None else ("**OK**" if r["ok"] else "**실패**")
        table.append("| %s | `python %s` | %s (%.0fs) |" % (r["name"], r["argv"], cnt, r["seconds"]))
    print("\n" + "=" * 78)
    print("\n".join(table))
    print("=" * 78)
    print("결과: %s" % OUT)
    # 문서의 표를 **여기서 직접 갱신**한다. 손으로 옮기면 반드시 낡는다 (실제로 README 와 검증 보고서의
    # "단위 181 · Web 285" 가 코드가 늘어난 뒤에도 그대로 남아 있었다).
    #
    # 단 `--quick` 은 8종만 도는 **부분 집합**이다. 그것으로 문서를 덮으면 21종 전체 표가 8줄로 줄어들어
    # "검증 범위가 좁아진 것처럼" 보인다 (실제로 한 번 그렇게 됐다). 부분 실행은 문서를 건드리지 않는다.
    if not only and not ns.quick:
        # 현행 문서에 쓴다. 날짜가 박힌 회차 보고서(docs/history/…)는 **그날의 사실**이라 덮으면 안 된다
        # (2026-09-20 재배치: 살아 있는 결과 표는 docs/VERIFICATION.md 하나다).
        doc = os.path.join(ROOT, "docs", "VERIFICATION.md")
        mark = "<!-- VERIFY_ALL_TABLE -->"
        try:
            with open(doc, encoding="utf-8") as f:
                src = f.read()
            if mark in src:
                head, _, rest = src.partition(mark)
                tail = rest.split("<!-- /VERIFY_ALL_TABLE -->", 1)[-1] if "<!-- /VERIFY_ALL_TABLE -->" in rest else rest
                body = "\n\n> %s 기준 · Python %s · 모두 격리 임시 환경에서 실행 (실제 색인·설정은 건드리지 않는다)\n\n%s\n" % (
                    res["when"], res["python"], "\n".join(table))
                with open(doc, "w", encoding="utf-8") as f:
                    f.write(head + mark + body + "<!-- /VERIFY_ALL_TABLE -->" + tail)
                print("문서 표 갱신: docs/VERIFICATION.md")
        except OSError as e:
            print("문서 표 갱신 실패(무시): %s" % e)
    print("\n%d개 중 %d개 통과" % (len(rows), len(rows) - len(bad)))
    for r in bad:
        print("  FAIL %s (exit %s)" % (r["name"], r["code"]))
    print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
