"""문서와 코드가 **어긋나지 않는지** 기계적으로 확인한다.

왜 있나: 문서는 조용히 낡는다. 링크가 끊기고, 예시 명령이 사라진 옵션을 쓰고, 설정 키 이름이
바뀌어도 문서에는 옛 이름이 남는다. 사람이 수십 개 문서를 다시 읽는 방식으로는 놓칠 수밖에 없고,
이 프로젝트는 **다른 LLM 이 문서만 보고 사내에 올리는 것**이 목표라 그 오류가 그대로 사고가 된다.

**문서 배치**: `docs/` 바로 아래 = 현행(지금의 사실) · `docs/history/<날짜>/` = 기록(그날의 사실).
둘 다 검사하지만 잣대가 다르다 — 현행은 "낡으면 실패", 기록은 "링크만 성하면 된다"
(자세한 이유는 docs/DOC_MAP.md §1).

무엇을 보나:

  A. 문서가 주장하는 것이 **코드에 있는가**
    1. 문서 간 링크 · 저장소 파일 링크가 실제로 있는가 (링크는 **그 문서가 있는 폴더 기준**으로 푼다)
    2. 문서가 인용한 `python -m llmwiki <명령>` 이 CLI 에 실제로 있는가
    3. 문서가 인용한 `config.json` 설정 키 · 토글이 `config.py` 에 실제로 있는가
    4. 문서가 인용한 `tools/verify/*.py` · `setup/*` 파일이 있는가
    5. 문서가 인용한 API 경로(`/api/...`)가 서버에 있는가

  B. **거꾸로** — 코드에 있는 것이 문서에 있는가 (문서가 기능을 따라가지 못하는 것을 잡는다)
    6. 모든 CLI 명령 · MCP 도구 · 설정/토글 키가 **현행 문서 어딘가에 설명돼 있는가**
       (기록 문서에 있는 것은 "예전에 있었다" 일 뿐이라 세지 않는다)
    7. 모든 `tools/verify/verify_*.py` 가 `tools/verify/README.md` 목록에 있는가

  C. 배치와 도달성
    8. 현행 자리(`docs/`)에 **날짜가 붙은 파일 이름**이 있으면 실패 (그것은 기록이다)
    9. 현행 문서가 README 나 BRINGUP_GUIDE 에서 **도달 가능한가** (고아 문서)
   10. 기록 문서가 `docs/history/README.md` 회차 색인에 있는가
   11. 코드가 문서를 가리키는 참조(`docs/XXX.md`)가 실제 파일인가

  D. 숫자
   12. `<!--live:키-->` 로 **표시한** 규모 숫자가 코드와 맞는가 (표시가 없으면 검사하지 않는다)

실행:
    python tools/verify/verify_docs.py
    python tools/verify/verify_docs.py --verbose
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

DOCS = os.path.join(ROOT, "docs")
# 문서에 나오지만 파일로 존재하지 않아도 되는 것 (예시용 가짜 경로 · 사용자가 만들 파일)
ALLOW_MISSING = {
    "bundle.md", "guide.md", "out.md", "report.md", "answer.md", "x.md", "노트.md",
    # 문서가 "이런 파일을 새로 만들면 된다" 며 드는 예 (있으면 안 되는 것은 아니지만 없어도 맞다)
    "build_log.json",
    "config.json", ".env", "security.json", "server.json", "schedule.json", "models.json",
    "tuning.json", "presets.json", "query_rules.json", "pins.json", "agents.json", "mcp_sources.json",
    # 코드 리뷰 문서가 "이렇게 나누면 좋겠다" 며 드는 제안 경로 (아직 만들지 않았고, 만들지 않아도 맞다)
    "_env.py", "results",
}


def read(p):
    """BOM 을 떼고 읽는다.

    PowerShell 의 `Set-Content -Encoding utf8` 등이 붙이는 BOM(`\\ufeff`) 때문에 문서 77개 중 11개가
    `\\ufeff# 제목` 으로 시작한다. 이걸 그대로 두면 "첫 줄이 `# ` 로 시작하는가" 같은 검사가
    **조용히 아무것도 검사하지 않는다** — 실제로 제목 날짜 검사가 그렇게 통과하고 있었다 (2026-09-20).
    """
    with open(p, encoding="utf-8") as f:
        return f.read().lstrip("﻿")


def strip_code(md: str) -> str:
    """코드펜스 안은 예시라 링크·경로 검사에서 제외한다 (```…``` 과 ~~~…~~~)."""
    out, fence = [], None
    for ln in md.split("\n"):
        s = ln.strip()
        if fence is None and (s.startswith("```") or s.startswith("~~~")):
            fence = s[:3]
            continue
        if fence is not None:
            if s.startswith(fence):
                fence = None
            continue
        out.append(ln)
    return "\n".join(out)


def strip_inline_code(md: str) -> str:
    """인라인 코드 스팬(`…`)을 지운다.

    링크 검사에만 쓴다. 백틱 안의 `stages[{…}](depth1)` 같은 글자는 마크다운이 **링크로 렌더링하지
    않으므로** 끊긴 링크가 아니다. 이걸 빼지 않으면 "구조를 설명하는 문장" 이 매번 거짓 양성이 된다.
    """
    return re.sub(r"`[^`\n]*`", "`…`", md)


def cli_commands():
    """cli.py 의 서브커맨드 이름 전부."""
    src = read(os.path.join(ROOT, "llmwiki", "cli.py"))
    return set(re.findall(r'sub\.add_parser\(\s*"([a-zA-Z0-9_\-]+)"', src)) | \
           set(re.findall(r'\.add_parser\(\s*"([a-zA-Z0-9_\-]+)"', src))


def config_keys():
    from llmwiki.config import Settings, Toggles
    return set(Settings.__dataclass_fields__) | set(Toggles.__dataclass_fields__)


def tuning_keys():
    try:
        from llmwiki.tuning import _INDEX
        return set(_INDEX)
    except Exception:
        return set()


def api_paths():
    src = read(os.path.join(ROOT, "llmwiki", "web", "server.py"))
    paths = set(re.findall(r'["\'](/api/[a-zA-Z0-9_/\-]+)["\']', src))
    paths |= set(re.findall(r'u\.path\.startswith\(["\'](/api/[a-zA-Z0-9_/\-]+)', src))
    paths.add("/mcp")
    return paths


# ---- '지금'의 규모를 말하는 숫자 ----------------------------------------------
# 문서에는 "그때의 사실"(기록)과 "지금의 사실"(현황)이 섞여 있다. 앞은 고치면 안 되고 뒤는 낡으면 안 된다.
# 둘을 문장 모양으로 구분하려 했더니 산문까지 걸렸다 — "표 밖에 있던 CLI 명령 3개" 는 총계가 아니다.
# 그래서 **글쓴이가 직접 표시**하게 한다: 숫자 앞에 `<!--live:<키>-->` 를 둔다. HTML 주석이라 화면에는
# 보이지 않고, 표시된 숫자만 코드와 대조한다. 표시가 없으면 검사하지 않는다(기록은 그대로 둔다).
#
#     예) `CLI 명령 <!--live:cli-->48개` → 화면에는 "CLI 명령 48개"
#
# 키: cli(명령 수) · api(Web 경로 수) · mcp(도구 수) · tests(단위 테스트 수) · harness(verify_*.py 수) · docs(문서 수)
LIVE_MARK = re.compile(r"<!--\s*live:([a-z_]+)\s*-->\s*\**(\d+)")
LIVE_KEYS = ("cli", "api", "mcp", "tests", "harness", "docs", "hist")


HISTORY = os.path.join(DOCS, "history")
#: 현행 문서 이름에 있으면 안 되는 모양 — 날짜가 붙은 문서는 **기록**이고 `docs/history/<날짜>/` 에 산다.
#: (이 규칙이 없으면 "docs 바로 아래 = 지금" 이라는 약속이 말로만 남는다.)
DATED_NAME = re.compile(r"_(20\d{6}|\d{4})(?:_\d)?\.md$|^requirement-\d{4}\.md$|_V\d+\.md$")


def _history_docs():
    """[(표시이름, 절대경로, 그 문서가 있는 폴더)] — 링크는 **자기 폴더 기준**으로 푼다."""
    out = []
    if not os.path.isdir(HISTORY):
        return out
    for d in sorted(os.listdir(HISTORY)):
        dp = os.path.join(HISTORY, d)
        if not os.path.isdir(dp):
            continue
        for f in sorted(os.listdir(dp)):
            if f.endswith(".md"):
                out.append(("history/%s/%s" % (d, f), os.path.join(dp, f), dp))
    return out


def live_counts(n_docs=0, n_hist=0):
    """코드·파일에서 **지금의** 규모를 센다 (문서가 표시한 숫자의 기준)."""
    import glob as _glob
    cnt = {"cli": len(cli_commands()), "mcp": 0, "tests": 0, "docs": n_docs, "hist": n_hist,
           "harness": len(_glob.glob(os.path.join(ROOT, "tools", "verify", "verify_*.py")))}
    src = read(os.path.join(ROOT, "llmwiki", "web", "server.py"))
    routes = set(re.findall(r'u\.path\s*==\s*["\'](/api/[a-zA-Z0-9_/\-]+)["\']', src))
    routes |= set(re.findall(r'u\.path\.startswith\(\s*["\'](/api/[a-zA-Z0-9_/\-]+)', src))
    for m in re.finditer(r'u\.path\s+in\s+\(([^)]*)\)', src):
        routes |= set(re.findall(r'["\'](/api/[a-zA-Z0-9_/\-]+)["\']', m.group(1)))
    cnt["api"] = len(routes)
    try:
        from llmwiki.mcp import TOOLS
        cnt["mcp"] = len(TOOLS)
    except Exception:
        pass
    # 단위 테스트는 `def test_` 의 수로 센다 (unittest 를 실제로 돌리지 않고도 규모를 알 수 있게).
    n = 0
    for f in _glob.glob(os.path.join(ROOT, "tests", "test_*.py")):
        n += len(re.findall(r"^\s+def test_", read(f), re.M))
    cnt["tests"] = n
    return cnt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="문서와 코드가 어긋나지 않는지 확인")
    ap.add_argument("--verbose", action="store_true")
    ns = ap.parse_args(argv)

    problems = []          # (문서, 종류, 내용)
    checked = {"link": 0, "cli": 0, "cfg": 0, "file": 0, "api": 0, "count": 0, "cover": 0}

    # docs/ 루트 = 현행 문서, docs/history/<날짜>/ = 그날의 기록. 둘 다 검사하되 **링크 기준 폴더가 다르다**.
    docs = sorted(f for f in os.listdir(DOCS) if f.endswith(".md"))
    hist = sorted(_history_docs())          # [(표시이름, 절대경로, 그 문서가 있는 폴더)]
    cmds, cfg, tun, apis = cli_commands(), config_keys(), tuning_keys(), api_paths()
    all_md = {f.lower() for f in docs}

    for name, p, here in [(f, os.path.join(DOCS, f), DOCS) for f in docs] + hist:
        raw = read(p)
        md = strip_code(raw)

        # 1. 마크다운 링크 (인라인 코드 안의 대괄호/괄호는 링크가 아니다)
        for text, target in re.findall(r"\[([^\]]*)\]\(([^)\s]+)\)", strip_inline_code(md)):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            checked["link"] += 1
            t = target.split("#")[0]
            if not t:
                continue
            cand = os.path.normpath(os.path.join(here, t))
            cand2 = os.path.normpath(os.path.join(ROOT, t))
            if not (os.path.exists(cand) or os.path.exists(cand2)):
                problems.append((name, "링크", "%s → %s (파일 없음)" % (text[:30], target)))

        # 2. CLI 명령
        for m in re.finditer(r"python -m llmwiki\s+([a-zA-Z0-9_\-]+)", md):
            c = m.group(1)
            if c.startswith("-"):
                continue        # 전역 플래그(--version, --user, --log-level …)는 서브커맨드가 아니다
            checked["cli"] += 1
            if c not in cmds:
                problems.append((name, "CLI", "`python -m llmwiki %s` — cli.py 에 없는 명령" % c))

        # 3. 설정 키: `config.json` 맥락에서 인용한 백틱 키
        for m in re.finditer(r"`(toggles\.)?([a-z][a-z0-9_]{3,})`", md):
            key = m.group(2)
            if m.group(1):          # toggles.<name> 은 반드시 토글이어야 한다
                checked["cfg"] += 1
                if key not in cfg:
                    problems.append((name, "설정", "`toggles.%s` — Toggles/Settings 에 없음" % key))

        # 4. 저장소 파일 인용 (tools/ · setup/ · llmwiki/ · prompts/ · schemas/)
        #    주의: `tools/list`·`tools/call`·`prompts/list` 는 **MCP JSON-RPC 메서드 이름**이지 파일이 아니다.
        #    그래서 마지막 조각에 확장자가 있는 것만 파일로 본다 (폴더 인용은 끝의 / 로 구분).
        for m in re.finditer(r"`((?:tools|setup|llmwiki|prompts|schemas|eval)/[A-Za-z0-9_./\-]+)`", md):
            rel = m.group(1)
            last = rel.rstrip("/").split("/")[-1]
            if "." not in last and not rel.endswith("/"):
                continue        # MCP 메서드 이름이거나 산문 속 경로 조각
            checked["file"] += 1
            if "*" in rel or "<" in rel:
                continue
            if not os.path.exists(os.path.join(ROOT, rel.replace("/", os.sep))):
                if os.path.basename(rel.rstrip("/")) in ALLOW_MISSING:
                    continue
                problems.append((name, "파일", "`%s` 없음" % rel))

        # 5. API 경로
        for m in re.finditer(r"`?(?:GET|POST|DELETE)\s+(/api/[a-zA-Z0-9_/\-]+)", md):
            path = m.group(1)
            checked["api"] += 1
            if path.rstrip("/") not in apis and not any(path.startswith(a) for a in apis):
                problems.append((name, "API", "%s — server.py 에 없음" % path))

    # 5.5 README 의 "CLI 요약" 표가 실제 명령 목록과 맞는가 (양방향)
    #     한쪽만 보면 낡는다: 없는 명령을 적어 두는 것도, 새 명령을 안 적는 것도 문제다.
    readme = read(os.path.join(ROOT, "README.md"))
    m = re.search(r"## 4\. CLI 요약.*?(?=\n## )", readme, re.S)
    if m:
        block = m.group(0)
        named = set()
        for code in re.findall(r"`([^`]+)`", block):
            first = code.strip().split()[0] if code.strip() else ""
            first = first.split("|")[0].strip()
            if re.fullmatch(r"[a-z][a-z0-9_\-]{1,20}", first):
                named.add(first)
        # 표에 적혔지만 CLI 에 없는 것
        for c in sorted(named - cmds):
            if c in ("python", "run", "cmd", "llmwiki", "tools", "pip", "set", "cd", "echo", "curl", "npx", "node", "ollama", "robocopy", "copy"):
                continue
            problems.append(("README.md", "CLI표", "`%s` — README CLI 요약에 있으나 cli.py 에 없음" % c))
        # CLI 에 있는데 표에 없는 것 (새로 만든 명령을 안 적은 경우)
        skip_doc = {"serve", "mcp"}      # 인터페이스 행에서 다르게 표기
        for c in sorted(cmds - named - skip_doc):
            problems.append(("README.md", "CLI표", "`%s` — cli.py 에 있으나 README CLI 요약에 없음" % c))

    # 6. 고아 문서: README / BRINGUP_GUIDE 에서 도달 가능한가
    reach = set()
    for entry in ("README.md", os.path.join("docs", "BRINGUP_GUIDE.md")):
        src = read(os.path.join(ROOT, entry))
        for t in re.findall(r"\(([^)\s]*\.md)[^)]*\)", src):
            reach.add(os.path.basename(t).lower())
    # 한 단계 더: README 가 가리킨 문서가 가리키는 문서까지 (문서끼리 잇는 것도 도달로 본다)
    for nm in list(reach):
        p = os.path.join(DOCS, nm)
        if os.path.exists(p):
            for t in re.findall(r"\(([^)\s]*\.md)[^)]*\)", read(p)):
                reach.add(os.path.basename(t).lower())
    orphans = sorted(f for f in all_md if f.lower() not in reach)

    # 6.3 기록 문서는 **회차 색인**(docs/history/README.md)에서 찾을 수 있어야 한다.
    #     현행 문서처럼 README 가 일일이 가리키지는 않으므로, 색인 하나가 그 역할을 한다.
    hist_index_p = os.path.join(HISTORY, "README.md")
    hist_index = read(hist_index_p) if os.path.exists(hist_index_p) else ""
    for label, hp, _hd in hist:
        base = os.path.basename(hp)
        if base == "README.md":
            continue
        if base not in hist_index:
            orphans.append(label + "  (docs/history/README.md 회차 색인에 없음)")

    # 6.4 **레이아웃 규칙** — "docs 바로 아래 = 지금 · docs/history/<날짜>/ = 그날".
    #     현행 자리에 날짜가 붙은 문서가 있으면 그 약속이 이미 깨진 것이다 (2026-09-20 재배치).
    for name in docs:
        if DATED_NAME.search(name):
            problems.append((name, "레이아웃",
                             "현행 문서 자리(docs/)에 날짜가 붙은 이름이 있다 — 기록이면 docs/history/<날짜>/ 로, "
                             "현행이면 날짜를 뺀 이름으로 (docs/DOC_MAP.md §1)"))
        # 제목(H1)에 박힌 날짜도 같은 문제다 — 현행 문서인데 "그 시점 기준" 으로 읽히면 안 된다.
        # (본문 안에서 "2026-09-19 에 이렇게 고쳤다" 고 쓰는 것은 정상이라 첫 줄만 본다.)
        h1 = next((ln for ln in read(os.path.join(DOCS, name)).splitlines() if ln.startswith("# ")), "")
        if re.search(r"20\d\d-\d\d-\d\d", h1):
            problems.append((name, "레이아웃",
                             "현행 문서의 제목에 날짜가 있다 — '그 시점 기준' 으로 읽힌다: %s" % h1.strip()[:70]))
    for d in sorted(os.listdir(HISTORY) if os.path.isdir(HISTORY) else []):
        if os.path.isdir(os.path.join(HISTORY, d)) and not re.fullmatch(r"20\d\d-\d\d-\d\d", d):
            problems.append(("docs/history", "레이아웃", "`%s` 는 날짜 폴더 이름(YYYY-MM-DD)이 아니다" % d))

    # 6.5 **지금의 규모를 말하는 숫자** — `<!--live:키-->` 로 표시된 것만 코드와 대조한다 (위 설명 참조).
    live = live_counts(len(docs), len([1 for lb, _p, _d in hist if os.path.basename(_p) != "README.md"]))
    for name in ["README.md"] + [os.path.join("docs", d) for d in docs]:
        p = os.path.join(ROOT, name)
        md = read(p)            # 주석은 코드블록 밖에만 쓴다는 전제 — strip 하지 않아도 무해하다
        for key, said in LIVE_MARK.findall(md):
            checked["count"] += 1
            if key not in live:
                problems.append((os.path.basename(name), "수치", "<!--live:%s--> 는 모르는 키다 (가능: %s)"
                                 % (key, ", ".join(LIVE_KEYS))))
            elif int(said) != live[key]:
                problems.append((os.path.basename(name), "수치",
                                 "<!--live:%s--> 옆 숫자가 %s 인데 지금은 %d 다 (지금을 설명하는 자리라 고쳐야 한다)"
                                 % (key, said, live[key])))

    # 6.55 **기능이 현행 문서에 설명돼 있는가** (지금까지와 반대 방향).
    #      앞의 검사들은 "문서가 주장하는 것이 코드에 있나" 를 본다. 그것만으로는 **문서가 기능을 따라가지
    #      못하는 것**을 못 잡는다 — 새 명령·도구·설정을 만들고 어느 현행 문서에도 안 적으면,
    #      문서만 보고 올리는 사람에게는 그 기능이 **없는 것**이다.
    #      기록 문서(history/)는 세지 않는다: 거기 적혀 있다는 것은 "예전에 있었다" 일 뿐이다.
    cur_blob = "\n".join(read(os.path.join(DOCS, f)) for f in docs)
    for c in sorted(cmds):
        checked["cover"] += 1
        if c not in cur_blob:
            problems.append(("(문서)", "설명 없음", "CLI 명령 `%s` 가 현행 문서 어디에도 없다" % c))
    try:
        from llmwiki.mcp import TOOLS as _TOOLS
        for t in sorted(x["name"] for x in _TOOLS):
            checked["cover"] += 1
            if t not in cur_blob:
                problems.append(("(문서)", "설명 없음", "MCP 도구 `%s` 가 현행 문서 어디에도 없다" % t))
    except Exception:
        pass
    for k in sorted(cfg):
        checked["cover"] += 1
        if k not in cur_blob:
            problems.append(("(문서)", "설명 없음", "설정/토글 `%s` 가 현행 문서 어디에도 없다" % k))

    # 6.6 하네스가 **자기 README 에 적혀 있는가** — 하네스를 만들고 목록에 안 넣으면 아무도 돌리지 않는다.
    #     (2026-09-20 에 켜 보니 25개 중 14개가 빠져 있었다.)
    import glob as _glob
    vreadme_p = os.path.join(ROOT, "tools", "verify", "README.md")
    if os.path.exists(vreadme_p):
        vreadme = read(vreadme_p)
        for f in sorted(_glob.glob(os.path.join(ROOT, "tools", "verify", "verify_*.py"))):
            b = os.path.basename(f)
            checked["file"] += 1
            if b not in vreadme and b[:-3] not in vreadme:
                problems.append(("tools/verify/README.md", "하네스",
                                 "`%s` 가 하네스 목록에 없다 — 목록에 없으면 아무도 돌리지 않는다" % b))

    # 7. 코드 → 문서 참조
    code_refs = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "llmwiki")):
        for f in files:
            if not f.endswith((".py", ".js", ".html", ".css")):
                continue
            src = read(os.path.join(base, f))
            for t in set(re.findall(r"docs/([A-Za-z0-9_\-]+\.md)", src)):
                code_refs.append((os.path.relpath(os.path.join(base, f), ROOT), t))
    bad_code_refs = [(f, t) for f, t in code_refs if not os.path.exists(os.path.join(DOCS, t))]

    # ---- 보고 ----
    print("문서 %d개(현행) + %d개(기록) · 검사: 링크 %d · CLI %d · 설정 %d · 파일 %d · API %d · 규모 수치 %d · 기능 설명 %d · 코드→문서 참조 %d"
          % (len(docs), live["hist"], checked["link"], checked["cli"], checked["cfg"], checked["file"],
             checked["api"], checked["count"], checked["cover"], len(code_refs)))
    print("지금 규모: CLI %d · Web %d · MCP %d · 단위 테스트 %d · 하네스 %d"
          % (live["cli"], live["api"], live["mcp"], live["tests"], live["harness"]))
    by_kind = {}
    for _n, kind, _d in problems:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    if problems:
        print("\n어긋난 곳 %d건" % len(problems))
        cur = None
        for name, kind, detail in sorted(problems):
            if name != cur:
                print("\n  %s" % name)
                cur = name
            print("    [%s] %s" % (kind, detail))
    if bad_code_refs:
        print("\n코드가 가리키는 없는 문서 %d건" % len(bad_code_refs))
        for f, t in sorted(set(bad_code_refs)):
            print("    %s → docs/%s" % (f, t))
    if orphans:
        print("\nREADME·BRINGUP_GUIDE 에서 도달할 수 없는 문서 %d건 (문서만 보고 올리는 사람은 못 찾는다)" % len(orphans))
        for o in orphans:
            print("    docs/%s" % o)
    bad = len(problems) + len(bad_code_refs) + len(orphans)
    print("\n요약: 어긋남 %d · 없는 문서 참조 %d · 고아 문서 %d" % (len(problems), len(set(bad_code_refs)), len(orphans)))
    print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
