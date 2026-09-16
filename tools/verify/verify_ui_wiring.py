"""UI 배선 정적 검사: JS 가 참조하는 #id 선택자와 data-tab 이 index.html/login.html 에 존재하는지, JS 문법(괄호 균형·정규식 없이 파서 대용) 검사."""
import os, re, json
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # <프로젝트 루트>/tools/verify/ 기준
S = os.path.join(ROOT, "llmwiki", "web", "static")
html = open(os.path.join(S, "index.html"), encoding="utf-8").read()
login = open(os.path.join(S, "login.html"), encoding="utf-8").read()
ids = set(re.findall(r'\bid="([^"]+)"', html)) | set(re.findall(r'\bid="([^"]+)"', login))
tabs = set(re.findall(r'data-tab="([^"]+)"', html))
sections = set(re.findall(r'<section id="tab-([^"]+)"', html))
groups = set(re.findall(r'data-group="([^"]+)"', html))
print("html ids:", len(ids), "tabs:", len(tabs), "sections:", len(sections))
print("tabs without section:", sorted(tabs - sections), "| sections without tab:", sorted(sections - tabs))
missing = {}
dyn_ok = set()
for fn in sorted(os.listdir(os.path.join(S, "js"))):
    src = open(os.path.join(S, "js", fn), encoding="utf-8").read()
    # 동적으로 innerHTML 로 만드는 id 는 같은 JS 파일 안에서 id="…" 로 생성된다
    created = set(re.findall(r'id="([a-zA-Z0-9_\-]+)"', src)) | set(re.findall(r"id='([a-zA-Z0-9_\-]+)'", src)) \
        | set(re.findall(r"sel\('([a-zA-Z0-9_\-]+)'", src)) | set(re.findall(r"\.id = '([a-zA-Z0-9_\-]+)'", src)) \
        | set(re.findall(r"[a-zA-Z]+(?:Select|Input)\('([a-zA-Z0-9_\-]+)'", src))   # modelSelect('m-llm-model', …) 같은 헬퍼가 만드는 id
    refs = set(re.findall(r"\$\('#([a-zA-Z0-9_\-]+)'\)", src)) | set(re.findall(r"\$\(\"#([a-zA-Z0-9_\-]+)\"\)", src)) | set(re.findall(r"\$\('#([a-zA-Z0-9_\-]+) ", src))
    miss = sorted(r for r in refs if r not in ids and r not in created)
    missing[fn] = miss
    # 괄호 균형 (문자열/템플릿/주석을 대략 제거 후)
    stripped = re.sub(r"//[^\n]*", "", src)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
    stripped = re.sub(r"`(?:\\.|[^`\\])*`", "``", stripped, flags=re.S)
    stripped = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", stripped)
    stripped = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', stripped)
    stripped = re.sub(r"/(?:\\.|[^/\\\n])+/[gimsuy]*", "//", stripped)  # 정규식 리터럴 제거
    bal = {c: stripped.count(c) for c in "(){}[]"}
    balanced = bal["("] == bal[")"] and bal["{"] == bal["}"] and bal["["] == bal["]"]
    if not balanced:
        print("   imbalance:", bal)
    print("%-18s refs=%3d created=%3d missing=%s balanced=%s" % (fn, len(refs), len(created), miss or "-", balanced))
# 서버 API 경로 ↔ JS 호출 경로 대조
srv = open(os.path.join(ROOT, "llmwiki", "web", "server.py"), encoding="utf-8").read()
paths_srv = set(re.findall(r'u\.path (?:==|in \() ?"(/api/[^"]+)"', srv)) | set(re.findall(r'"(/api/[a-z_/]+)"', srv))
paths_js = set()
for fn in os.listdir(os.path.join(S, "js")):
    src = open(os.path.join(S, "js", fn), encoding="utf-8").read()
    paths_js |= set(re.findall(r"api\('(/api/[a-z_/]+)", src)) | set(re.findall(r"fetch\('(/api/[a-z_/]+)", src))
paths_js |= set(re.findall(r"fetch\('(/api/[a-z_/]+)", login))
unknown = sorted(p for p in paths_js if p not in paths_srv and not any(p.startswith(x) for x in ("/api/jobs/", "/api/progress")))
print("JS 가 부르는 API 경로:", len(paths_js), "| 서버에 없는 경로:", unknown or "-")
print("RESULT", "OK" if not any(missing.values()) and not unknown and not (tabs - sections) else "PROBLEMS")
