"""문서와 코드가 **어긋나지 않는지** 기계적으로 확인한다.

왜 있나: 문서는 조용히 낡는다. 링크가 끊기고, 예시 명령이 사라진 옵션을 쓰고, 설정 키 이름이
바뀌어도 문서에는 옛 이름이 남는다. 사람이 33개 문서를 다시 읽는 방식으로는 놓칠 수밖에 없고,
이 프로젝트는 **다른 LLM 이 문서만 보고 사내에 올리는 것**이 목표라 그 오류가 그대로 사고가 된다.

무엇을 보나 (모두 '문서가 주장하는 것' 을 코드/파일에서 확인하는 방향):

  1. 문서 간 링크 · 저장소 파일 링크가 실제로 있는가
  2. 문서가 인용한 `python -m llmwiki <명령>` 이 CLI 에 실제로 있는가
  3. 문서가 인용한 `config.json` 설정 키 · 토글이 `config.py` 에 실제로 있는가
  4. 문서가 인용한 `tools/verify/*.py` · `setup/*` 파일이 있는가
  5. 문서가 인용한 API 경로(`/api/...`)가 서버에 있는가
  6. `docs/` 의 모든 문서가 README 나 BRINGUP_GUIDE 에서 **도달 가능한가** (고아 문서)
  7. 코드가 문서를 가리키는 참조(`docs/XXX.md`)가 실제 파일인가

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
}


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="문서와 코드가 어긋나지 않는지 확인")
    ap.add_argument("--verbose", action="store_true")
    ns = ap.parse_args(argv)

    problems = []          # (문서, 종류, 내용)
    checked = {"link": 0, "cli": 0, "cfg": 0, "file": 0, "api": 0}

    docs = sorted(f for f in os.listdir(DOCS) if f.endswith(".md"))
    cmds, cfg, tun, apis = cli_commands(), config_keys(), tuning_keys(), api_paths()
    all_md = {f.lower() for f in docs}

    for name in docs:
        p = os.path.join(DOCS, name)
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
            cand = os.path.normpath(os.path.join(DOCS, t))
            cand2 = os.path.normpath(os.path.join(ROOT, t))
            if not (os.path.exists(cand) or os.path.exists(cand2)):
                problems.append((name, "링크", "%s → %s (파일 없음)" % (text[:30], target)))

        # 2. CLI 명령
        for m in re.finditer(r"python -m llmwiki\s+([a-zA-Z0-9_\-]+)", md):
            checked["cli"] += 1
            c = m.group(1)
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
                if os.path.basename(rel) in ALLOW_MISSING:
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
    print("문서 %d개 · 검사: 링크 %d · CLI %d · 설정 %d · 파일 %d · API %d · 코드→문서 참조 %d"
          % (len(docs), checked["link"], checked["cli"], checked["cfg"], checked["file"], checked["api"], len(code_refs)))
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
