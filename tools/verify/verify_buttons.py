"""Web UI 의 **모든 버튼을 실제로 눌러** 기대한 동작을 하는지 확인한다.

왜 있나: 렌더 검사(`verify_browser.py`)는 "그려지는가" 만 본다. 눌러도 아무 일도 일어나지 않는 버튼
(핸들러가 조용히 return 하거나 예외로 죽거나, 애초에 배선이 안 된 경우)은 잡지 못한다.
이 스크립트는 헤드리스 브라우저를 CDP 로 붙잡고 버튼을 하나씩 클릭한 뒤 이렇게 판정한다.

  FAIL  콘솔/프라미스 오류가 났다
  FAIL  아무 일도 일어나지 않았다 (서버 호출도, 화면 변화도, 알림도 없음)
  OK    위 둘 다 아니고, 기대 조건(있으면)도 만족한다

**격리 환경에서 돈다.** 설정·DB 를 임시 폴더로 복사하고 mock LLM 으로 서버를 띄우므로,
저장·초기화 같은 버튼을 눌러도 실제 데이터가 바뀌지 않는다.

**무엇을 안 보나 (의도적)** — 대상은 `index.html` 안의 `<button id=…>` 이다. 따라서:
  - 질의·분석 뒤에 JS 가 만들어 넣는 Ask 패널 버튼(`qa-*`: md 열기·다운로드·🧠 LLM 소견·📦 최적화 자료 묶음 …)과
    링크 모양 버튼(`<a class="button">`: 묶음 다운로드·손잡이 지도)은 여기서 수집되지 않는다.
    이들은 `verify_ui_wiring.py`(동적 id 와 핸들러 배선) + `verify_web.py`(그 버튼이 부르는 `/api/analysis`,
    `/api/optimize/bundle|guide` 응답) 로 나누어 확인한다.
  - 즉 "모든 버튼" 은 **정적 버튼 전수** 를 뜻한다. 동적 버튼을 여기서도 누르려면 먼저 질의를 실행해야 하는데
    (`btn-query` 는 HEAVY), 그러면 이 스크립트가 매번 질의 시간을 쓰게 된다.

실행:
    python tools/verify/verify_buttons.py              # 전체 (무거운 것 제외)
    python tools/verify/verify_buttons.py --only evolve,memory
    python tools/verify/verify_buttons.py --heavy      # 전체 빌드·평가 등 오래 걸리는 것도
    python tools/verify/verify_buttons.py --live       # 격리하지 않고 지금 서버에 (읽기 버튼만)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_click import WS, Page, EDGE            # noqa: E402  (최소 WebSocket/CDP 클라이언트 재사용)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable
HTML = os.path.join(ROOT, "llmwiki", "web", "static", "index.html")

# 오래 걸려서 기본으로는 누르지 않는 것 (--heavy 로 포함)
HEAVY = {"btn-build", "btn-build-full", "btn-eval", "btn-eval-matrix", "btn-tr-run", "btn-fusion-compare",
         "btn-tr-ab",     # A/B = trial 을 **두 번** 돌린다 (평가셋 전체를 LLM 으로) — btn-tr-run 과 같은 이유로 무겁다
         "btn-models-test-live", "btn-cat-discover", "btn-docvec", "btn-pc-run", "btn-w-rebuild",
         "btn-query", "btn-fx-llm", "btn-ev-review", "btn-mem-consolidate", "btn-fx-consolidate",
         "btn-src-ingest", "btn-src-retrieve", "btn-verify", "btn-verify-fix", "btn-health", "btn-scan",
         "btn-snap-create"}   # 스냅샷은 색인 DB 전체(수백 MB)를 복사한다
# 격리 환경이어도 화면을 되돌리기 어려운 것 (--live 에서는 자동으로 제외된다)
MUTATING = {"btn-config-save", "btn-config-reload", "btn-models-save", "btn-agents-save", "btn-tuning-save",
            "btn-tuning-reset", "btn-tuning-reload", "btn-preset-save", "btn-prompt-save", "btn-prompt-reset",
            "btn-rules-save", "btn-qr-save", "btn-w-save", "btn-srv-save", "btn-srv-reload", "btn-srv-maint",
            "btn-srv-block", "btn-srv-circuit-reset", "btn-srv-loglevel", "btn-sec-reload", "btn-snap-create",
            "btn-user-add", "btn-pw-change", "btn-sch-save", "btn-sch-add", "btn-sch-test", "btn-embed-cache-clear",
            "btn-pc-clear", "btn-watch-start", "btn-watch-stop", "btn-watch-scan", "btn-watch-tick",
            "btn-perf-save", "btn-src-save", "btn-mem-decay", "btn-console", "btn-cat-add", "btn-mp-add",
            "btn-qr-add", "btn-pin-add", "btn-sch-remove"}

# 입력이 필요한 버튼: 누르기 전에 채워 준다 (그러지 않으면 '입력하세요' 안내만 나오고 끝난다)
PREFILL = {
    "btn-q-expect-run": "document.querySelector('#qx-docs')&&(document.querySelector('#qx-docs').value='ISSUE-2001');",
    "btn-fx-expect": "document.querySelector('#fx-docs').value='ISSUE-2001';",
    "btn-search": "document.querySelector('#s-q')&&(document.querySelector('#s-q').value='PDCCH');",
    "btn-rules-test": "document.querySelector('#rules-test-q')&&(document.querySelector('#rules-test-q').value='PDCCH 디코더');",
    # 질의 해부 — 입력이 비어 있으면 핸들러가 바로 빠져나가 아무것도 렌더하지 않는다(헛도는 검사였다).
    # 2026-09-20: 프리필을 넣어도 동작하지 않던 진짜 원인은 `ask.js` 가 `loaders` 를 LW 에서 구조분해하지
    # 않아 **그 IIFE 가 로드 중에 죽어 있었던** 것이다 — 그 뒤의 onclick 이 아예 등록되지 않았다.
    # 버튼·API 검사는 모두 통과했고 `verify_browser` 의 콘솔 오류 수집이 잡았다.
    "btn-dbg": "document.querySelector('#dbg-q')&&(document.querySelector('#dbg-q').value='ISSUE-2001');",
    "btn-dbg-copy": "document.querySelector('#dbg-q')&&(document.querySelector('#dbg-q').value='ISSUE-2001');",
    "btn-time-test": "document.querySelector('#time-q')&&(document.querySelector('#time-q').value='지난주 리뷰한 CL');",
    "btn-qr-test": "document.querySelector('#qr-test-q')&&(document.querySelector('#qr-test-q').value='PDCCH');",
    "btn-graph": "document.querySelector('#g-entity')&&(document.querySelector('#g-entity').value='ISSUE-2001');",
    "btn-tr-compare": "",
    "btn-tr-md": "",
    # 과거 질의를 **고를 수 있어야** 비교가 내 관심사 위에서 돈다 (2026-09-20).
    # 목록이 체크박스와 함께 떠야 하고, 안 뜨면 예전처럼 기간·건수로 뭉뚱그리는 것밖에 못 한다.
    "btn-tr-pick": "!document.querySelector('#tr-pick').classList.contains('hidden')"
                   " && (document.querySelectorAll('#tr-pick [data-qid]').length>0"
                   "     || document.querySelector('#tr-pick').textContent.indexOf('과거 질의가 없습니다')>=0)",
}

# 버튼별 기대 조건(선택). 없으면 '오류 없음 + 무언가 일어남' 만 본다.
EXPECT = {
    "btn-ev-refresh": "document.querySelector('#ev-pending').innerHTML.length>20"
                      " && document.querySelector('#ev-summary').textContent.indexOf('pending')>=0",
    "btn-mem-refresh": "document.querySelector('#mem-status').textContent.indexOf('[object Object]')<0"
                       " && document.querySelector('#mem-episodes').innerHTML.length>20",
    # 포렌식 목록은 **판정 칩**(문제만/전부/판정별)과 함께 그려져야 한다. 칩이 없으면 화면이
    # 다시 '최근 60건 나열' 로 돌아간 것이고, 그러면 정상 건에 덮여 볼 것을 못 본다 (2026-09-20).
    "btn-fx-refresh": "document.querySelector('#fx-list').innerHTML.length>20"
                      " && document.querySelectorAll('#fx-summary .fx-chips .chip').length>=2"
                      " && !!document.querySelector('#fx-summary .fx-chips .chip.on')",
    "btn-fx-run": "document.querySelector('#fx-detail').textContent.indexOf('포렌식 #')>=0",
    "btn-req-refresh": "!!document.querySelector('#req-list table')",
    "btn-act-refresh": "!!document.querySelector('#act-gauge .ag-slots')",
    "btn-logs-load": "!!document.querySelector('#log-table table')",
    "btn-logs": "!!document.querySelector('#logs table')",
    "btn-srv-refresh": "document.querySelector('#srv-overview').innerHTML.length>20",
    "btn-sys-refresh": "document.querySelector('#tab-system').innerHTML.length>500",
    "btn-embed-refresh": "document.querySelector('#embed-cov').innerHTML.length>20",
    "btn-preset-refresh": "document.querySelector('#preset-cards').innerHTML.length>10",
    "btn-cat-refresh": "document.querySelector('#cat-table').innerHTML.length>10"
                       " || document.querySelector('#cat-discover').innerHTML.length>0",
    "btn-sch-refresh": "document.querySelector('#sch-table').innerHTML.length>10",
    "btn-sec-refresh": "document.querySelector('#sec-users').innerHTML.length>10",
    "btn-arch-refresh": "document.querySelector('#arch-flows').innerHTML.length>50",
    "btn-lint-refresh": "document.querySelector('#lint-table').innerHTML.length>10"
                        " || document.querySelector('#lint-summary').innerHTML.length>10",
    "btn-config-effective": "document.querySelector('#config-effective').textContent.length>50",
    # 초기화 버튼은 **미리보기만** 연다 — 표와 '유지합니다' 목록이 그려지는지 본다.
    # 실제로 지우는 '지금 초기화' 는 미리보기 뒤에 JS 가 만들며 id 가 없으므로 이 하네스가 누를 수 없다.
    # 해부가 실제로 그려졌는지 — 다섯 구간 + 임시 토글 줄. 조건식은 ASCII(선택자)만 쓴다.
    "btn-dbg": "document.querySelectorAll('#dbg-out .dbg-sec').length>=6"
               " && !!document.querySelector('#dbg-toggles [data-tg]')",
    # 운영 통계가 실제로 집계돼 그려졌는지 (섹션이 여러 개 + **추세 차트**가 실제 SVG 로 그려졌는지).
    # 차트를 숫자 표로만 두면 "나아지나 나빠지나" 가 한눈에 안 보여 만든 뜻이 없다 (2026-09-20).
    #
    # **눈금과 축까지 본다.** 첫 판은 viewBox 를 `preserveAspectRatio="none"` 으로 늘려 점이 타원이 되고
    # 축이 없어 값을 읽을 수 없었다. 늘리기로 돌아가거나 축이 사라지면 여기서 실패해야 한다.
    "btn-ops": "document.querySelectorAll('#ops-out .dbg-sec').length>=4"
               " && document.querySelectorAll('#ops-out .chart svg').length>=3"
               " && document.querySelectorAll('#ops-out .chart svg .ch-bar, #ops-out .chart svg .ch-line').length>0"
               " && document.querySelectorAll('#ops-out [data-bucket]').length===3"
               " && document.querySelectorAll('#ops-out .chart svg .ch-grid').length>=3"
               " && document.querySelectorAll('#ops-out .chart svg .ch-ylab').length>=3"
               " && document.querySelectorAll('#ops-out .chart svg .ch-xlab').length>=1"
               " && !document.querySelector('#ops-out .chart svg[preserveAspectRatio=\"none\"]')",
    "btn-reset-data": "document.querySelector('#sys-reset-out').innerHTML.indexOf('유지')>=0",
    "btn-reset-settings": "document.querySelector('#sys-reset-out').innerHTML.indexOf('유지')>=0",
    "btn-reset-logs": "document.querySelector('#sys-reset-out').innerHTML.indexOf('유지')>=0",
}

SHIM = """
window.__lw = {errs: [], fetches: [], toasts: []};
window.addEventListener('error', function(e){ __lw.errs.push('error: ' + (e.message||e)); });
window.addEventListener('unhandledrejection', function(e){
  var r = e.reason; __lw.errs.push('promise: ' + ((r && (r.message||r.stack)) || r)); });
(function(){ var of = window.fetch; window.fetch = function(){ try{ __lw.fetches.push(String(arguments[0])); }catch(x){}
  return of.apply(this, arguments); }; })();
(function(){ var t = document.getElementById('toast'); if (!t) return;
  new MutationObserver(function(){ if (t.textContent) __lw.toasts.push(t.textContent); })
    .observe(t, {childList:true, characterData:true, subtree:true}); })();
window.confirm = function(){ return false; };      // 확인 대화상자는 '취소' 로 (파괴적 진행 방지)
window.alert = function(){};                        // 모달이 메인 스레드를 막지 않게
window.prompt = function(){ return null; };
window.__lwReset = function(){ __lw.errs=[]; __lw.fetches=[]; __lw.toasts=[]; };
'ok'
"""


def tab_map(html: str):
    """버튼 id → (group, tab)"""
    groups = {}
    for g, body in re.findall(r'<nav class="tabs[^"]*" data-group="([^"]+)">(.*?)</nav>', html, re.S):
        for t in re.findall(r'data-tab="([^"]+)"', body):
            groups[t] = g
    out = {}
    for tab, body in re.findall(r'<section id="tab-([^"]+)"(.*?)</section>', html, re.S):
        for bid in re.findall(r'<button[^>]*id="([^"]+)"', body):
            out[bid] = (groups.get(tab, ""), tab)
    return out


def isolated_env(port: int, extra_cfg=None, extra_files=None, serve: bool = True):
    """설정·DB 를 임시 폴더로 복사하고 mock LLM 으로 서버를 띄운다. (tmp, env, proc) 반환.

    extra_cfg   : config.json 에 덮어쓸 키 (예: {"mcp_plugins_dir": "..."} )
    extra_files : 임시 폴더에 추가로 쓸 파일 {이름: 객체(JSON 으로 저장)}
    serve       : False 면 서버를 띄우지 않는다 (proc=None) — stdio MCP 처럼 env 만 필요할 때.
    """
    tmp = tempfile.mkdtemp(prefix="lwbtn_")
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    cfg["data_dir"] = os.path.join(tmp, "data")
    cfg["wiki_dir"] = os.path.join(tmp, "wiki")
    cfg["llm_provider"] = "mock"
    cfg["llm_roles"] = {}
    # 임베더도 **고정**한다. embed_provider=auto 는 이 PC 에 무엇이 깔려 있는지에 따라 달라져서
    # (예: `ollama pull bge-m3` 한 순간 1024차원 ollama 임베더로 바뀐다) 색인 차원이 어긋나
    # 벡터 검색이 빈 결과를 내고 하네스가 엉뚱하게 실패했다 (2026-09-19). 검증은 결정적이어야 한다.
    cfg["embed_provider"] = "hash"
    cfg["embed_model"] = ""
    cfg["embed_dim"] = int(cfg.get("embed_dim") or 256) if str(cfg.get("embed_provider") or "") == "hash" else 256
    cfg["toggles"] = dict(cfg.get("toggles") or {}, auto_build=False, precompute=False, query_cache=False)
    os.makedirs(cfg["data_dir"])
    src_db = os.path.join(ROOT, "data", "llmwiki.sqlite3")
    if os.path.exists(src_db):
        print("  색인 DB 복사 중 (%.0f MB)…" % (os.path.getsize(src_db) / 1e6))
        shutil.copy2(src_db, os.path.join(cfg["data_dir"], cfg.get("db_name", "llmwiki.sqlite3")))
    if os.path.isdir(os.path.join(ROOT, "wiki")):
        shutil.copytree(os.path.join(ROOT, "wiki"), cfg["wiki_dir"])
    cfg.update(extra_cfg or {})
    json.dump(cfg, open(os.path.join(tmp, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for f in ("tuning.json", "presets.json", "query_rules.json", "mcp_sources.json", "agents.json", "pins.json",
              "security.json", "server.json", "schedule.json", "models.json"):
        s = os.path.join(ROOT, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(tmp, f))
    for name, obj in (extra_files or {}).items():
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    shutil.copy2(os.path.join(ROOT, "data", "rules.json"), os.path.join(tmp, "rules.json"))
    shutil.copytree(os.path.join(ROOT, "schemas"), os.path.join(tmp, "schemas"))
    shutil.copytree(os.path.join(ROOT, "prompts"), os.path.join(tmp, "prompts"))
    shutil.copy2(os.path.join(ROOT, "eval", "questions.json"), os.path.join(tmp, "questions.json"))
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               LLMWIKI_CONFIG=os.path.join(tmp, "config.json"), LLMWIKI_TUNING_PATH=os.path.join(tmp, "tuning.json"),
               LLMWIKI_PRESETS_PATH=os.path.join(tmp, "presets.json"), LLMWIKI_QUERY_RULES_PATH=os.path.join(tmp, "query_rules.json"),
               LLMWIKI_MCP_SOURCES_PATH=os.path.join(tmp, "mcp_sources.json"), LLMWIKI_AGENTS_PATH=os.path.join(tmp, "agents.json"),
               LLMWIKI_PINS_PATH=os.path.join(tmp, "pins.json"), LLMWIKI_RULES_PATH=os.path.join(tmp, "rules.json"),
               LLMWIKI_SCHEMAS_DIR_PATH=os.path.join(tmp, "schemas"), LLMWIKI_PROMPTS_DIR_PATH=os.path.join(tmp, "prompts"),
               LLMWIKI_EVAL_PATH=os.path.join(tmp, "questions.json"), LLMWIKI_LOGS_DIR_PATH=os.path.join(tmp, "logs"),
               LLMWIKI_SECURITY_PATH=os.path.join(tmp, "security.json"), LLMWIKI_SERVER_PATH=os.path.join(tmp, "server.json"),
               LLMWIKI_SCHEDULE_PATH=os.path.join(tmp, "schedule.json"), LLMWIKI_MODELS_PATH=os.path.join(tmp, "models.json"))
    env.pop("LLMWIKI_API_KEY", None)
    proc = None
    if serve:
        proc = subprocess.Popen([PY, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(port)],
                                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return tmp, env, proc


# 질의를 한 번 실행한 뒤에만 존재하는 버튼들 (Ask 패널). id 또는 CSS 선택자.
#   check: 눌러서 '화면이 바뀌었는가' 를 판정할 표현식 (없으면 오류 없음 + 무언가 변함)
DYNAMIC = [
    ("모든 단계 펼치기", "#trace [data-tr='open']", "document.querySelectorAll('#trace .tr-row.open').length > 0"),
    ("모두 접기", "#trace [data-tr='close']", "document.querySelectorAll('#trace .tr-row.open').length === 0"),
    ("trace 복사", "#trace [data-tr='copy']", ""),
    ("근거 문단 펼치기", "#hits .hit .t", "document.querySelectorAll('#hits .hit.open').length > 0"),
    ("내 지난 요청 열기", "#myreq-box > summary", "document.querySelector('#myreq-list').innerHTML.length > 10"),
    ("내 지난 요청 새로고침", "#btn-myreq-refresh", "document.querySelector('#myreq-list').innerHTML.length > 10"),
    # '챗' 하나로 2줄째(입력칸)와 접속자 아이콘을 함께 켜고 끈다
    ("챗 끄기 (입력칸·아이콘 함께)", "#btn-chat-toggle",
     "document.querySelector('#chat-row2').classList.contains('hidden') && "
     "document.querySelectorAll('#people-layer .person').length === 0"),
    ("챗 켜기", "#btn-chat-toggle", "!document.querySelector('#chat-row2').classList.contains('hidden')"),
    # 보낸 말은 채팅창이 아니라 **내 아이콘 위 말풍선**으로 뜬다 (아이폰 메시지 모양)
    ("채팅 보내기 → 말풍선", "#btn-chat-send",
     "!!document.querySelector('#people-layer .person.me .bubble') && "
     "document.querySelector('#people-layer .person.me .bubble').textContent.length > 0"),
    ("게시 대화상자 열기", "#btn-chat-board", "!document.querySelector('#post-modal').classList.contains('hidden')"),
    ("게시 대화상자에 작업 목록", "#post-modal", "document.querySelectorAll('#cp-request option').length >= 1"),
    ("게시 대화상자 닫기", "#btn-cp-cancel", "document.querySelector('#post-modal').classList.contains('hidden')"),
    ("게시판 열기", "#btn-chat-board", "!document.querySelector('#post-modal').classList.contains('hidden')"),
    ("게시판으로 이동", "#btn-cp-board", "document.querySelector('#tab-board').classList.contains('active')"),
    ("게시판 새로고침", "#btn-board-refresh", "document.querySelector('#board-list').innerHTML.length > 5"),
]


def click_dynamic(page, wait: float):
    """질의를 한 번 돌린 뒤 Ask 패널의 동적 버튼을 눌러 본다. (verdict, tab, name, why, state) 목록 반환."""
    out = []
    print("\n동적 버튼 (질의 뒤에 생기는 것들)\n")
    page.eval("location.hash='#ask/query'; 'ok'")
    page.eval("new Promise(r=>setTimeout(()=>r(1), 1500))")
    page.eval("(function(){var q=document.getElementById('q'); if(q) q.value='ISSUE-2001 의 원인과 수정 CL 은?'; return 'ok';})()")
    page.eval("(function(){var b=document.getElementById('btn-query'); if(b) b.click(); return 'ok';})()")
    # 질의가 끝나 결과 영역이 보일 때까지 (mock LLM 이라 보통 1~3초)
    for _ in range(40):
        vis = page.eval("(function(){var e=document.getElementById('query-out'); return !!e && !e.classList.contains('hidden');})()")
        if vis is True:
            break
        page.eval("new Promise(r=>setTimeout(()=>r(1), 1000))")
    if page.eval("(function(){var e=document.getElementById('query-out'); return !!e && !e.classList.contains('hidden');})()") is not True:
        print("  (질의 결과가 나오지 않아 동적 버튼 검사를 건너뜁니다)")
        return out
    # 채팅 입력칸은 비어 있으면 아무 일도 안 하므로 미리 채운다
    page.eval("(function(){var t=document.getElementById('chat-text'); if(t) t.value='버튼 검증'; return 'ok';})()")
    for name, sel, check in DYNAMIC:
        page.eval("window.__lwReset(); 'ok'")
        clicked = page.eval("(function(){var b=document.querySelector(%r); if(!b) return 'no-el';"
                            " if(b.disabled) return 'disabled'; b.click(); return 'clicked';})()" % sel)
        page.eval("new Promise(r=>setTimeout(()=>r(1), %d))" % int(max(0.8, wait) * 1000))
        st = page.eval("(function(){return {errs: __lw.errs.slice(0,3), fetches: __lw.fetches.length,"
                       " toasts: __lw.toasts.slice(0,2)};})()") or {}
        errs = st.get("errs") or []
        ok = True if not check else page.eval(check)
        verdict, why = "OK  ", ""
        if clicked != "clicked":
            verdict, why = "FAIL", str(clicked)
        elif errs:
            verdict, why = "FAIL", "오류 " + str(errs[0])[:90]
        elif ok is not True:
            verdict, why = "FAIL", "기대 조건 불충족 (%s)" % check[:60]
        out.append((verdict, "query(동적)", name, why, st))
        print("%s %-10s %-22s fetch=%-2s %s%s" % (verdict, "query", name, st.get("fetches"),
                                                  (("알림: " + str((st.get("toasts") or [""])[0])[:36]) if st.get("toasts") else ""),
                                                  ("  " + why) if why else ""))
    return out


def main(argv=None) -> int:
    if not EDGE:
        print("BUTTONS SKIP: Edge/Chrome 을 찾지 못함 (LLMWIKI_BROWSER 로 지정)")
        return 0
    ap = argparse.ArgumentParser(description="Web UI 의 모든 버튼을 눌러 동작을 확인")
    ap.add_argument("--port", type=int, default=8830)
    ap.add_argument("--cdp", type=int, default=9340)
    ap.add_argument("--only", default="", help="탭 이름 쉼표 (예: evolve,memory)")
    ap.add_argument("--heavy", action="store_true", help="빌드·평가처럼 오래 걸리는 버튼도 누른다")
    ap.add_argument("--live", action="store_true", help="격리하지 않고 지금 서버에 (상태를 바꾸는 버튼은 제외)")
    ap.add_argument("--wait", type=float, default=2.5, help="클릭 후 대기(초)")
    ap.add_argument("--keep", action="store_true")
    ns = ap.parse_args(argv)

    html = open(HTML, encoding="utf-8").read()
    tabs = tab_map(html)
    skip = set() if ns.heavy else set(HEAVY)
    if ns.live:
        skip |= MUTATING
    only = {x.strip() for x in ns.only.split(",") if x.strip()}
    targets = [(b, g, t) for b, (g, t) in tabs.items() if b not in skip and (not only or t in only)]
    targets.sort(key=lambda x: (x[2], x[0]))

    base = "http://127.0.0.1:%d" % ns.port
    prof = os.path.join(tempfile.gettempdir(), "llmwiki_buttons_profile")
    shutil.rmtree(prof, ignore_errors=True)
    tmp = proc = br = None
    rows = []
    try:
        if ns.live:
            print("  대상: 지금 떠 있는 서버 %s (상태 변경 버튼 제외)" % base)
        else:
            tmp, _env, proc = isolated_env(ns.port)
            print("  격리 서버 시작: %s (data=%s)" % (base, tmp))
        for _ in range(150):
            try:
                urllib.request.urlopen(base + "/api/auth/me", timeout=2)
                break
            except Exception:
                time.sleep(1)
        br = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
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
        if not url:
            print("BUTTONS SKIP: 브라우저 디버깅 포트에 붙지 못했습니다")
            return 0
        def connect():
            for _ in range(40):
                try:
                    lst = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % ns.cdp, timeout=2).read().decode())
                    u = next((t["webSocketDebuggerUrl"] for t in lst if t.get("type") == "page"), None)
                    if u:
                        pg = Page(WS(u))
                        pg.eval("new Promise(r=>setTimeout(()=>r(1), 1500))")
                        pg.eval(SHIM)
                        return pg
                except Exception:
                    pass
                time.sleep(1)
            return None

        page = Page(WS(url))
        page.eval("new Promise(r=>setTimeout(()=>r(1), 5000))")
        page.eval(SHIM)

        def timed_out(v):
            return isinstance(v, dict) and v.get("__timeout__")

        print("\n버튼 %d개 클릭 (건너뜀 %d개)\n" % (len(targets), len(skip)))
        cur = None
        for bid, group, tab in targets:
            if (group, tab) != cur:
                page.eval("location.hash='#%s/%s'; 'ok'" % (group, tab))
                page.eval("new Promise(r=>setTimeout(()=>r(1), 2200))")
                cur = (group, tab)
            page.eval("window.__lwReset(); 'ok'")
            pre = PREFILL.get(bid)
            if pre:
                page.eval(pre + " 'ok'")
            before = page.eval("(function(){var s=document.querySelector('#tab-%s'); return s?s.innerHTML.length:0;})()" % tab)
            clicked = page.eval("(function(){var b=document.getElementById('%s');"
                                " if(!b) return 'no-el'; if(b.disabled) return 'disabled'; b.click(); return 'clicked';})()" % bid)
            if timed_out(clicked):
                # 페이지가 응답하지 않는다 → 이 버튼은 실패로 기록하고 붙어서 계속한다
                rows.append(("FAIL", tab, bid, "클릭 후 페이지가 응답하지 않음(시간 초과)", {}))
                print("FAIL %-10s %-22s 페이지 응답 없음 — 다시 연결" % (tab, bid))
                pg = connect()
                if pg is None:
                    print("  브라우저에 다시 붙지 못했습니다 — 중단")
                    break
                page, cur = pg, None
                continue
            page.eval("new Promise(r=>setTimeout(()=>r(1), %d))" % int(ns.wait * 1000))
            st = page.eval("(function(){var s=document.querySelector('#tab-%s');"
                           " return {errs: __lw.errs.slice(0,3), fetches: __lw.fetches.length, toasts: __lw.toasts.slice(0,2),"
                           " len: s?s.innerHTML.length:0};})()" % tab) or {}
            if timed_out(st):
                rows.append(("FAIL", tab, bid, "클릭 뒤 상태를 읽지 못함(시간 초과)", {}))
                print("FAIL %-10s %-22s 상태 확인 시간 초과 — 다시 연결" % (tab, bid))
                pg = connect()
                if pg is None:
                    break
                page, cur = pg, None
                continue
            errs = st.get("errs") or []
            moved = (st.get("len") != before) or st.get("fetches") or st.get("toasts")
            exp = EXPECT.get(bid)
            exp_ok = True if not exp else page.eval(exp)
            verdict = "OK  "
            why = ""
            if clicked != "clicked":
                verdict, why = "FAIL", clicked
            elif errs:
                verdict, why = "FAIL", "오류 " + str(errs[0])[:90]
            elif not moved:
                verdict, why = "FAIL", "아무 일도 일어나지 않음 (호출·화면변화·알림 없음)"
            elif exp_ok is not True:
                verdict, why = "FAIL", "기대 조건 불충족" + (": " + str(exp_ok.get("__error__"))[:60] if isinstance(exp_ok, dict) else "")
            rows.append((verdict, tab, bid, why, st))
            print("%s %-10s %-22s fetch=%-2s dom=%+-6d %s%s" % (
                verdict, tab, bid, st.get("fetches"), (st.get("len") or 0) - (before or 0),
                (("알림: " + str((st.get("toasts") or [""])[0])[:40]) if st.get("toasts") else ""), ("  " + why) if why else ""))
        # ---------------- 동적 버튼: 질의한 **뒤에** JS 가 그려 넣는 것들 ----------------
        # 정적 수집(index.html 의 <button id=…>)에 안 잡히므로 여기서 따로 누른다.
        # 복사·펼치기·접기처럼 화면만 바꾸는 버튼은 fetch 가 없어도 정상이다 → 판정 기준이 다르다.
        if not ns.live:
            rows += click_dynamic(page, ns.wait)

        bad = [r for r in rows if r[0] == "FAIL"]
        print("\n버튼 %d개 중 %d개 통과 · 실패 %d개" % (len(rows), len(rows) - len(bad), len(bad)))
        for r in bad:
            print("  FAIL %-10s %-22s %s" % (r[1], r[2], r[3]))
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_buttons_result.json")
        json.dump([{"verdict": r[0], "tab": r[1], "button": r[2], "why": r[3], "state": r[4]} for r in rows],
                  open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("결과: %s" % out)
        print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
        return 1 if bad else 0
    finally:
        for p in (br, proc):
            if p:
                p.terminate()
                try:
                    p.communicate(timeout=10)
                except Exception:
                    p.kill()
        if tmp and not ns.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
