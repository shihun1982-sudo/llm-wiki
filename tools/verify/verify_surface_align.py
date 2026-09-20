"""**CLI · Web UI · MCP** 세 창구가 같은 기능을 제공하는지 대조한다.

왜 있나: 기능은 보통 한 창구에서 먼저 만들어지고 나머지에 옮겨진다. 옮기는 것을 잊으면
"CLI 에서는 되는데 화면에는 없다" 가 쌓인다. 사용자는 셋 중 하나만 쓰므로 그 사람에게는
**그 기능이 없는 것**이다. 사람이 기억으로 맞추는 대신 표로 만들어 비교한다.

무엇을 보나:
  1. 기능(capability)마다 CLI 명령 · Web API · MCP 도구가 있는지 (아래 CAPS 표)
  2. 그 이름들이 **실제로 존재**하는지 (cli.py 서브커맨드 · server.py 경로 · mcp.py TOOLS)
  3. MCP 도구가 모두 문서(MCP.md)에 적혀 있는지

`web` 이 `-` 인 기능은 "화면에 없어도 되는 것"(예: 서버 기동)이고, 의도적으로 비운 것은
`note` 에 이유를 적는다. 이유 없이 비면 그것이 곧 정렬이 깨진 자리다.

실행:
    python tools/verify/verify_surface_align.py
    python tools/verify/verify_surface_align.py --md    # 표를 마크다운으로 (문서에 붙일 때)
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# (기능, CLI 명령, Web API 경로, MCP 도구, 비고)
#   ""  = 없어야 정상 (비고에 이유)
#   None = 아직 없다 (정렬이 깨진 자리 → 실패로 보고)
CAPS = [
    ("질의 (RAG 답변)", "query", "/api/query", "wiki_query", ""),
    ("검색 (채널별)", "search", "/api/search", "wiki_search", ""),
    ("단계 재실행", "rerun", "/api/query/rerun", "wiki_rerun", ""),
    ("지난 요청 목록·상세", "requests", "/api/requests", "wiki_requests", ""),
    ("상세 분석 리포트", "analyze", "/api/analysis", "wiki_analysis", ""),
    ("포렌식 (왜 못 찾았나)", "forensic", "/api/forensic", "wiki_forensic", ""),
    ("엔티티·관계 조회", "entity", "/api/entity", "wiki_entity", ""),
    ("그래프 이웃", "graph", "/api/graph", "", "엔티티 상세(wiki_entity)와 유사 문서(wiki_related)가 덮는다"),
    ("유사 문서 + 연결", "", "/api/search", "wiki_related", "MCP 전용 — 이슈 분석 결과로 관련 문서를 찾는 용도"),
    ("문서 전문 + 메타", "docs", "/api/doc_chunks", "wiki_doc", ""),
    ("문서 원문(청크)", "", "/api/chunk", "", "wiki_doc 이 문서 단위로 덮는다"),
    ("위키 페이지", "wiki", "/api/wiki/list", "", "위키는 색인에서 생성된 뷰 — MCP 는 wiki_doc 으로 원문을 본다"),
    ("색인 상태·통계", "stats", "/api/status", "wiki_status", ""),
    ("건강 점검", "health", "/api/health", "", "운영 점검 — 외부 LLM 이 아니라 사람이 본다"),
    ("외부 소스 직접 검색", "mcp-source", "/api/mcp_sources", "wiki_external_search", ""),
    ("빌드", "build", "/api/build", "", "MCP 로는 색인을 바꾸지 않는다 (외부 LLM 에 쓰기 권한을 주지 않음)"),
    ("평가", "eval", "/api/eval", "", "오래 걸리는 배치 — CLI/Web 잡으로만"),
    ("Trial 비교", "trial", "/api/trials", "", "〃"),
    ("규칙 사전 (질의 확장)", "rules", "/api/query_rules", "", "지식 편집은 사람이 화면/CLI 에서"),
    ("그래프 규칙", "rules", "/api/rules", "", "〃"),
    ("Pin (고정 근거)", "pin", "/api/pins", "", "〃"),
    ("프리셋", "preset", "/api/presets", "", "〃"),
    ("튜닝 값", "tuning", "/api/tuning", "", "〃"),
    ("프롬프트", "prompts", "/api/prompts", "", "〃"),
    ("설정(config.json)", "config", "/api/config", "", "설정 변경은 admin 화면/CLI 에서만"),
    ("모델·프로바이더", "models", "/api/models", "", "〃"),
    ("스케줄", "schedule", "/api/schedule", "", "서버 운영 — 화면/CLI"),
    ("사용자·권한", "users", "/api/auth/users", "", "보안 — 화면/CLI"),
    ("API 키", "apikey", "/api/apikeys", "", "〃"),
    ("스냅샷", "snapshot", "/api/snapshot", "", "〃"),
    ("서버 모니터·제한", "server", "/api/admin/server", "", "〃"),
    ("진행 중 작업·취소", "server", "/api/activity", "", "〃"),
    ("로그", "logs", "/api/logs", "", "〃"),
    ("외부 RAG 소스", "mcp-source", "/api/mcp_sources", "wiki_sources", ""),
    ("자가진화 제안", "evolve", "/api/evolve/proposals", "wiki_propose", ""),
    ("메모리", "memory", "/api/memory", "", "운영 — 화면/CLI"),
    ("피드백", "evolve", "/api/feedback", "wiki_feedback", ""),
    ("최적화 자료 묶음", "optimize", "/api/optimize/bundle", "", "파일로 내려받아 LLM 에 첨부"),
    ("아키텍처 지도", "arch", "/api/architecture", "", "〃"),
    ("코퍼스 계약 점검", "corpus", "/api/corpus/lint", "", "빌드 전 점검 — 화면/CLI"),
    ("임베딩 현황", "embed", "/api/embed/report", "", "〃"),
    ("사전 계산", "precompute", "/api/precompute", "", "〃"),
    ("워처", "watch", "/api/watch", "", "〃"),
    ("유지보수", "maintenance", "/api/maintenance", "", "〃"),
    ("협업 채팅·게시판", "", "/api/collab", "", "Web 전용 부수 기능 (토글 collab)"),
    ("서버 기동", "serve", "", "", "명령 자체가 서버를 띄운다"),
    ("MCP 서버 기동·점검", "mcp", "", "", "〃"),
]


def cli_commands():
    src = open(os.path.join(ROOT, "llmwiki", "cli.py"), encoding="utf-8").read()
    return set(re.findall(r'\.add_parser\(\s*"([a-zA-Z0-9_\-]+)"', src))


def api_paths():
    src = open(os.path.join(ROOT, "llmwiki", "web", "server.py"), encoding="utf-8").read()
    p = set(re.findall(r'["\'](/api/[a-zA-Z0-9_/\-]+)["\']', src))
    p |= set(re.findall(r'u\.path\.startswith\(["\'](/api/[a-zA-Z0-9_/\-]+)', src))
    return p


def mcp_tools():
    from llmwiki.mcp import TOOLS
    return {t["name"] for t in TOOLS}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CLI · Web · MCP 정렬 대조")
    ap.add_argument("--md", action="store_true", help="마크다운 표로 출력")
    ns = ap.parse_args(argv)

    cmds, paths, tools = cli_commands(), api_paths(), mcp_tools()
    problems = []
    rows = []
    for cap, cli, web, mcp, note in CAPS:
        def mark(name, pool, kind):
            if name is None:
                problems.append((cap, "%s 창구가 없다 (정렬 깨짐)" % kind))
                return "**없음**"
            if name == "":
                return "–"
            ok = name in pool if kind != "Web" else (name in pool or any(p.startswith(name) for p in pool))
            if not ok:
                problems.append((cap, "%s `%s` 가 실제로 없다" % (kind, name)))
                return "`%s` ⚠" % name
            return "`%s`" % name
        rows.append((cap, mark(cli, cmds, "CLI"), mark(web, paths, "Web"), mark(mcp, tools, "MCP"), note))

    # MCP 도구 중 표에 없는 것 (새로 만들고 표에 안 넣은 것)
    listed = {m for _c, _cl, _w, m, _n in CAPS if m}
    orphan_tools = sorted(t for t in tools if t not in listed)
    for t in orphan_tools:
        problems.append(("(표 밖)", "MCP 도구 `%s` 가 정렬표에 없다" % t))

    # MCP 도구가 문서에 적혀 있는가
    mcpdoc = open(os.path.join(ROOT, "docs", "MCP.md"), encoding="utf-8").read()
    undoc = sorted(t for t in tools if t not in mcpdoc)
    for t in undoc:
        problems.append(("(문서)", "MCP 도구 `%s` 가 docs/MCP.md 에 없다" % t))

    if ns.md:
        print("| 기능 | CLI | Web API | MCP 도구 | 비고 |")
        print("|---|---|---|---|---|")
        for r in rows:
            print("| %s | %s | %s | %s | %s |" % r)
    else:
        print("%-24s %-14s %-26s %-16s %s" % ("기능", "CLI", "Web API", "MCP", "비고"))
        print("-" * 110)
        for cap, cli, web, mcp, note in rows:
            print("%-24s %-14s %-26s %-16s %s" % (cap[:24], cli[:14], web[:26], mcp[:16], note[:34]))

    print("\n기능 %d개 · CLI 명령 %d · Web 경로 %d · MCP 도구 %d" % (len(CAPS), len(cmds), len(paths), len(tools)))
    tri = sum(1 for _c, cl, w, m, _n in CAPS if cl and w and m)
    print("세 창구 모두 제공: %d개 · MCP 는 읽기 전용이라 의도적으로 뺀 것: %d개"
          % (tri, sum(1 for _c, _cl, _w, m, n in CAPS if not m and n)))
    if problems:
        print("\n어긋난 곳 %d건" % len(problems))
        for cap, why in problems:
            print("  %-24s %s" % (cap[:24], why))
    print("\nRESULT %s" % ("PROBLEMS" if problems else "OK"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
