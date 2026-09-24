# -*- coding: utf-8 -*-
"""**세 창구가 같은 답을 주는가** — CLI 프로세스 · Web 서버 · MCP 클라이언트를 **진짜로 띄워** 비교한다.

## 왜 또 하나 필요한가

이미 있는 것들이 보는 것:

| 하네스 | 보는 것 | 못 보는 것 |
|---|---|---|
| `verify_surface_align.py` | 기능마다 CLI 명령·Web 경로·MCP 도구가 **있는가** (정적) | 셋이 **같은 답**을 주는가 |
| `verify_cli.py` · `verify_web.py` · `verify_mcp.py` | 창구마다 **따로** 동작하는가 | 창구 사이의 차이 |
| `tests/test_surface_consistency.py` | 같은 값을 주는가 | **같은 프로세스 안에서** 엔진 함수를 직접 부른다 — CLI 가 실제로 그 함수를 부르는지는 보지 않는다 |

마지막 줄이 이 파일의 이유다. 단위 테스트는 `p.query(...)` 를 직접 불러 "CLI 도 이렇게 부를 것이다" 를
전제한다. 그런데 어긋남은 대개 그 전제가 깨질 때 생긴다 — CLI 가 인자를 다르게 넘기거나, 기본값을 하나
더 얹거나, `--json` 출력에 안내 줄을 섞어 파싱을 깨뜨린다(2026-09-20 `trial run --source queries` 에서
실제로 있었던 일). 그래서 여기서는 **`python -m llmwiki …` 를 진짜 실행하고**, **`serve` 를 띄워 HTTP 로
부르고**, **같은 서버의 `POST /mcp` 로 MCP 도구를 부른다**. 세 경로가 모두 진짜다.

## 무엇을 같다고 보나

시각·요청 번호·ms·표현 형식은 창구마다 달라도 된다. **투영(projection)** 을 정해 그것만 비교한다 —
문서 수·청크 id 목록·정규화된 질의어·엔티티 id 처럼 "다르면 같은 시스템이 아닌" 값들.

실행:
    python tools/verify/verify_tri_surface.py [--port 8879] [--keep]
결과: tools/verify/verify_tri_surface_result.json (어긋나면 종료코드 1)
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
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
PY = sys.executable

RESULTS = []
FAILED = []

try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
except Exception:
    pass


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:400]})
    if not ok:
        FAILED.append(name)
    print("  %s %-52s %s" % ("OK  " if ok else "FAIL", name, str(detail)[:100]))
    return bool(ok)


def same(name, values, detail=""):
    """창구별 값 {창구: 값} 이 모두 같은가. 값이 하나도 없으면 **실패**로 본다 (빈 비교는 통과가 아니다)."""
    got = {k: v for k, v in values.items() if v is not None}
    if len(got) < 2:
        return check(name, False, "비교할 창구가 %d 개뿐 — 값: %s" % (len(got), json.dumps(values, ensure_ascii=False, default=str)[:200]))
    first = next(iter(got.values()))
    if not first and first != 0:
        return check(name, False, "비교 대상이 비어 있다 (빈 값끼리 같다고 하면 안 된다): %s" % json.dumps(got, ensure_ascii=False, default=str)[:200])
    ok = all(v == first for v in got.values())
    return check(name, ok, detail if ok else "창구별 값이 다르다: " + json.dumps(got, ensure_ascii=False, default=str)[:300])


# ---------------------------------------------------------------- 환경
def setup_env(tmp: str) -> dict:
    """설정 일습을 임시 폴더로 복사하고 **작은 샘플 코퍼스**를 가리키게 한다 (mock LLM · hash 임베더)."""
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    cfg.update({"data_dir": os.path.join(tmp, "data"), "wiki_dir": os.path.join(tmp, "wiki"),
                "corpus_dirs": [os.path.join(ROOT, "setup", "sample_corpus_modem")],
                "llm_provider": "mock", "llm_roles": {},
                "embed_provider": "hash", "embed_model": "", "embed_dim": 256})
    # 캐시가 켜져 있으면 두 번째 창구가 캐시를 맞아 '같다' 가 공짜로 참이 된다.
    cfg["toggles"] = dict(cfg.get("toggles") or {}, auto_build=False, precompute=False, query_cache=False)
    os.makedirs(cfg["data_dir"])
    json.dump(cfg, open(os.path.join(tmp, "config.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for f in ("tuning.json", "presets.json", "query_rules.json", "mcp_sources.json", "agents.json",
              "pins.json", "security.json", "server.json", "schedule.json", "models.json"):
        s = os.path.join(ROOT, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(tmp, f))
    shutil.copy2(os.path.join(ROOT, "data", "rules.json"), os.path.join(tmp, "rules.json"))
    shutil.copytree(os.path.join(ROOT, "schemas"), os.path.join(tmp, "schemas"))
    shutil.copytree(os.path.join(ROOT, "prompts"), os.path.join(tmp, "prompts"))
    shutil.copy2(os.path.join(ROOT, "eval", "questions.json"), os.path.join(tmp, "questions.json"))
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               LLMWIKI_CONFIG=os.path.join(tmp, "config.json"),
               LLMWIKI_TUNING_PATH=os.path.join(tmp, "tuning.json"),
               LLMWIKI_PRESETS_PATH=os.path.join(tmp, "presets.json"),
               LLMWIKI_QUERY_RULES_PATH=os.path.join(tmp, "query_rules.json"),
               LLMWIKI_MCP_SOURCES_PATH=os.path.join(tmp, "mcp_sources.json"),
               LLMWIKI_AGENTS_PATH=os.path.join(tmp, "agents.json"),
               LLMWIKI_PINS_PATH=os.path.join(tmp, "pins.json"),
               LLMWIKI_RULES_PATH=os.path.join(tmp, "rules.json"),
               LLMWIKI_SCHEMAS_DIR_PATH=os.path.join(tmp, "schemas"),
               LLMWIKI_PROMPTS_DIR_PATH=os.path.join(tmp, "prompts"),
               LLMWIKI_EVAL_PATH=os.path.join(tmp, "questions.json"),
               LLMWIKI_LOGS_DIR_PATH=os.path.join(tmp, "logs"),
               LLMWIKI_SECURITY_PATH=os.path.join(tmp, "security.json"),
               LLMWIKI_SERVER_PATH=os.path.join(tmp, "server.json"),
               LLMWIKI_SCHEDULE_PATH=os.path.join(tmp, "schedule.json"),
               LLMWIKI_MODELS_PATH=os.path.join(tmp, "models.json"))
    for k in ("LLMWIKI_API_KEY", "LLMWIKI_USER", "LLMWIKI_PASSWORD"):
        env.pop(k, None)
    return env


# ---------------------------------------------------------------- 세 창구
def cli_json(env, argv, timeout=600):
    """`python -m llmwiki … --json` 을 **진짜 실행**하고 stdout 에서 JSON 을 꺼낸다.

    `--json` 출력에 안내 줄이 섞이면 여기서 걸린다 — 붙어 있는 도구는 그 줄 때문에 파싱이 깨진다.
    """
    p = subprocess.run([PY, "-m", "llmwiki"] + argv, cwd=ROOT, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    out = (p.stdout or "").lstrip("﻿")
    if p.returncode != 0:
        return None, "exit=%d %s" % (p.returncode, (p.stderr or out)[-200:])
    i = min([x for x in (out.find("{"), out.find("[")) if x >= 0] or [-1])
    if i < 0:
        return None, "JSON 이 없다: %s" % out[:200]
    if out[:i].strip():
        return None, "--json 인데 앞에 다른 출력이 섞였다: %r" % out[:i][:120]
    try:
        return json.loads(out[i:]), ""
    except Exception as e:
        return None, "JSON 파싱 실패: %s" % e


class Web:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def get(self, path, timeout=300):
        with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def post(self, path, body, timeout=600):
        req = urllib.request.Request(self.base + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))


class Mcp:
    """같은 서버의 `POST /mcp` (Streamable HTTP) — 외부 LLM 이 실제로 쓰는 경로."""

    def __init__(self, base):
        self.url = base.rstrip("/") + "/mcp"
        self.session = None
        self._id = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "verify-tri", "version": "1"}})

    def rpc(self, method, params=None, timeout=600):
        self._id += 1
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, headers=h, method="POST",
                                     data=json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method,
                                                      "params": params or {}}, ensure_ascii=False).encode("utf-8"))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body, hdrs = r.read(), dict(r.headers.items())
        if hdrs.get("Mcp-Session-Id"):
            self.session = hdrs["Mcp-Session-Id"]
        return json.loads(body.decode("utf-8"))

    def call(self, tool, args=None):
        res = (self.rpc("tools/call", {"name": tool, "arguments": args or {}}) or {}).get("result") or {}
        if res.get("isError"):
            return {"_error": "\n".join(c.get("text", "") for c in res.get("content") or [])[:200]}
        sc = res.get("structuredContent")
        txt = "\n".join(c.get("text", "") for c in res.get("content") or [])
        # 도구마다 전문을 본문 JSON 으로 주기도 하고(structuredContent 는 요약), 구조화로 주기도 한다.
        # 붙은 LLM 은 둘 다 본다 — 비교도 둘을 합쳐서 한다.
        body = {}
        try:
            body = json.loads(txt) if txt.lstrip()[:1] in ("{", "[") else {}
        except Exception:
            body = {}
        return {"structured": sc or {}, "body": body if isinstance(body, dict) else {}, "text": txt}


def _pick(mcp_out, *keys):
    """MCP 응답(본문 JSON → structuredContent 순)에서 첫 번째로 찾은 키."""
    for src in (mcp_out.get("body") or {}, mcp_out.get("structured") or {}):
        for k in keys:
            if k in src:
                return src[k]
    return None


# ---------------------------------------------------------------- 비교 항목
def compare_all(env, web, mcp):
    # ── 1. 색인 상태 ────────────────────────────────────────────────
    print("\n[1] 색인 상태 (CLI `stats` · GET /api/status · wiki_status)")
    c, why = cli_json(env, ["stats", "--json"])
    check("CLI stats --json 이 파싱된다", c is not None, why)
    w = web.get("/api/status")
    m = mcp.call("wiki_status")
    for k in ("docs", "chunks"):
        same("상태 %s" % k, {
            "cli": ((c or {}).get("stats") or {}).get(k),
            "web": (w.get("stats") or {}).get(k),
            "mcp": ((_pick(m, "stats") or {}) or {}).get(k),
        })

    # ── 2. 운영 통계 ────────────────────────────────────────────────
    print("\n[2] 운영 통계 (CLI `stats --full` · GET /api/opstats · wiki_status(full))")
    c, why = cli_json(env, ["stats", "--full", "--json", "--days", "30", "--section", "index"])
    check("CLI stats --full --json 이 파싱된다", c is not None, why)
    w = web.get("/api/opstats?days=30&sections=index")
    m = mcp.call("wiki_status", {"full": True, "days": 30, "sections": ["index"]})
    # wiki_status(full) 는 운영 통계를 `ops` 아래에 싣는다 (기본 상태와 섞이지 않게).
    mi = ((_pick(m, "ops") or {}) or {}).get("index") or _pick(m, "index")
    same("운영 통계 index.docs", {
        "cli": ((c or {}).get("index") or {}).get("docs"),
        "web": (w.get("index") or {}).get("docs"),
        "mcp": (mi or {}).get("docs") if isinstance(mi, dict) else None,
    })

    # ── 2.5 운영 통계 추세 (일/주/월) ────────────────────────────────
    print("\n[2.5] 추세 (CLI `stats --full --section trend --bucket` · GET /api/opstats?bucket= · wiki_status)")
    for bk in ("day", "week", "month"):
        c, why = cli_json(env, ["stats", "--full", "--json", "--section", "trend", "--bucket", bk])
        w = web.get("/api/opstats?sections=trend&bucket=" + bk)
        m = mcp.call("wiki_status", {"full": True, "sections": ["trend"], "bucket": bk})
        mt = ((_pick(m, "ops") or {}) or {}).get("trend") or {}
        same("추세 %s — 묶음 이름" % bk, {
            "cli": ((c or {}).get("trend") or {}).get("bucket"),
            "web": (w.get("trend") or {}).get("bucket"), "mcp": mt.get("bucket"),
        })
        same("추세 %s — 구간 수" % bk, {
            "cli": ((c or {}).get("trend") or {}).get("n_points"),
            "web": (w.get("trend") or {}).get("n_points"), "mcp": mt.get("n_points"),
        })
    # 묶음이 굵을수록 기간이 넓어져야 한다 (월간을 7일치로 그리면 막대가 하나뿐이다)
    d1 = web.get("/api/opstats?sections=trend&bucket=day")["trend"]["days"]
    d3 = web.get("/api/opstats?sections=trend&bucket=month")["trend"]["days"]
    check("묶음이 굵으면 기간도 넓다 (일 %g일 < 월 %g일)" % (d1, d3), d3 > d1)

    # ── 2.6 포렌식 목록의 기본이 '문제 건만' 인가 ────────────────────
    # 갓 세운 색인에는 포렌식 기록이 없다. **빈 목록끼리 같다** 는 비교가 아니므로(§same),
    # 먼저 답이 나오는 질의와 근거가 없는 질의를 하나씩 던져 기록을 만든다.
    print("\n[2.6] 포렌식 목록 (CLI `forensic list --only` · GET /api/forensics?only=)")
    web.post("/api/query", {"q": "DMA 오버런 원인"})
    web.post("/api/query", {"q": "존재하지 않는 zzz 항목의 보라색 규격은 무엇인가"})
    subprocess.run([PY, "-m", "llmwiki", "forensic", "last"], cwd=ROOT, env=env,
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    n_fx = len(web.get("/api/forensics?only=all&limit=50") or [])
    if not check("비교할 포렌식 기록을 만들었다 (%d건)" % n_fx, n_fx > 0,
                 "기록이 없으면 아래 비교가 빈 채로 통과한다"):
        return
    c, why = cli_json(env, ["forensic", "list", "--only", "problems", "--limit", "50", "--json"])
    w_prob = web.get("/api/forensics?only=problems&limit=50")
    w_all = web.get("/api/forensics?only=all&limit=50")
    same("문제 건만 — 기록 id 목록", {
        "cli": [r["id"] for r in ((c or {}).get("rows") or [])] or None,
        "web": [r["id"] for r in (w_prob or [])] or None,
    })
    check("기본 보기에 정상 건(sufficient)이 섞이지 않는다",
          not [r for r in (w_prob or []) if r.get("verdict") == "sufficient"],
          "문제만 보여 주기로 한 목록에 정상 건이 있다")
    check("`only=all` 이면 전부 나온다 (문제 %d ≤ 전부 %d)" % (len(w_prob or []), len(w_all or [])),
          len(w_all or []) >= len(w_prob or []))
    s = web.get("/api/forensics/summary")
    check("요약의 판정 건수는 **전체**를 센다", (s or {}).get("n", 0) >= len(w_all or []),
          "요약 n=%s < 목록 %d — 최근 N건만 세고 있다" % ((s or {}).get("n"), len(w_all or [])))

    # ── 2.7 비교에 쓸 과거 질의 고르기 ───────────────────────────────
    print("\n[2.7] 과거 질의 후보 (CLI `trial candidates` · GET /api/eval/candidates)")
    c, why = cli_json(env, ["trial", "candidates", "--days", "365", "--limit", "20", "--json"])
    check("CLI trial candidates --json 이 파싱된다", c is not None, why)
    w = web.get("/api/eval/candidates?days=365&limit=20")
    qid = lambda d: [x.get("from_query_id") for x in ((d or {}).get("candidates") or [])] or None
    same("후보 질의 id 목록", {"cli": qid(c), "web": qid(w)})
    cands = (w or {}).get("candidates") or []
    check("후보에 고를 재료가 붙어 있다 (시각·평가·판정·근거)",
          bool(cands) and all(("ts" in x and "feedback" in x and "verdict" in x and "groundedness" in x) for x in cands),
          "무엇을 고를지 판단할 값이 없다 — 화면에 빈 칸만 뜬다")
    # 판정은 query_log 의 `scores.verdict` 에서 온다. 예전에는 **없는 키**(`answer_mode`)를 봐서
    # `--only insufficient` 가 항상 0건이었다(필터가 죽어 있었다) — 값이 실제로 채워지는지 본다.
    check("판정이 실제로 채워진다", any(x.get("verdict") for x in cands),
          "verdict 가 전부 비어 있다 — 거르기(근거 약함/못 찾음)가 아무것도 못 고른다")

    # ── 2.8 앙상블 폴백 (실패하면 역할 모델로 되돌리기) ──────────────
    # 왜: 앙상블을 켜면 역할 모델은 **불리지 않는다**. 멤버가 전부 죽으면 그 역할은 답을 못 낸다.
    # 그래서 `fallback_role_model` 로 되돌리되, 세 창구가 **같은 값**을 보고 같은 설명을 해야 한다.
    print("\n[2.8] 앙상블 폴백 (CLI `models ensemble show --json` · GET /api/models · wiki_query trace)")
    c, why = cli_json(env, ["models", "ensemble", "show", "answer", "--json"])
    check("CLI models ensemble show --json 이 파싱된다", c is not None, why)
    w = web.get("/api/models")
    c_ans = (c or {}).get("answer") or {}
    w_ans = ((w or {}).get("ensemble") or {}).get("answer") or {}
    for k in ("fallback_role_model", "fallback_mode"):
        same("폴백 설정 %s" % k, {"cli": (c_ans.get("effective") or {}).get(k),
                               "web": (w_ans.get("effective") or {}).get(k)})
    same("폴백이 돌아갈 역할 모델", {"cli": ((c_ans.get("effective") or {}).get("fallback") or {}).get("model"),
                          "web": ((w_ans.get("effective") or {}).get("fallback") or {}).get("model")})
    check("기본값이 '켜짐 · auto' 다 (앙상블 탓에 답이 아예 안 나오는 일이 없게)",
          (w_ans.get("effective") or {}).get("fallback_role_model") is True
          and (w_ans.get("effective") or {}).get("fallback_mode") == "auto",
          "유효값=%s" % json.dumps({k: (w_ans.get("effective") or {}).get(k) for k in ("fallback_role_model", "fallback_mode")},
                                 ensure_ascii=False))
    # 끄면 되돌릴 대상이 사라져야 한다 (설정이 실제로 먹는가 — 값만 바뀌고 동작이 그대로면 안 된다).
    # config.json 은 **mtime 자동 재적재가 아니다** — 떠 있는 서버는 reload 를 받아야 새 파일을 읽는다
    # (docs/SETTINGS_SYNC.md 의 how=reload). 그래서 운영자가 하는 그대로 reload 를 거쳐 확인한다.
    def _reload():
        try:
            web.post("/api/config", {"action": "reload", "_confirm": True})
        except Exception:
            pass

    cli_json(env, ["models", "ensemble", "set", "answer", "--fallback", "false"])
    _reload()
    e2 = (((web.get("/api/models") or {}).get("ensemble") or {}).get("answer") or {}).get("effective") or {}
    check("폴백을 끄면 되돌릴 대상이 없어진다 (CLI → 파일 → reload → Web)",
          e2.get("fallback_role_model") is False and e2.get("fallback") is None,
          "껐는데 fallback=%s" % json.dumps(e2.get("fallback"), ensure_ascii=False))
    cli_json(env, ["models", "ensemble", "set", "answer", "--fallback", "true", "--fallback-mode", "rerun"])
    _reload()
    e3 = (((web.get("/api/models") or {}).get("ensemble") or {}).get("answer") or {}).get("effective") or {}
    check("CLI 로 바꾼 모드가 Web 유효값에 그대로 온다 (CLI → 파일 → reload → Web)",
          e3.get("fallback_mode") == "rerun", "web 유효값=%s" % e3.get("fallback_mode"))
    cli_json(env, ["models", "ensemble", "set", "answer", "--fallback", "", "--fallback-mode", ""])   # 되돌리기
    _reload()

    # ── 3. 질의 해부 ────────────────────────────────────────────────
    print("\n[3] 질의 해부 (CLI `inspect` · POST /api/debug/query · wiki_inspect)")
    q = "지난주 DMA 오버런 원인"
    c, why = cli_json(env, ["inspect", q, "--json"])
    check("CLI inspect --json 이 파싱된다", c is not None, why)
    w = web.post("/api/debug/query", {"q": q})
    m = mcp.call("wiki_inspect", {"query": q})
    for k in ("normalized", "tokens", "keywords"):
        same("해부 %s" % k, {"cli": (c or {}).get(k), "web": w.get(k), "mcp": _pick(m, k)})
    same("해부의 시간 범위", {
        "cli": ((c or {}).get("time") or {}).get("scope", {}).get("from"),
        "web": ((w.get("time") or {}).get("scope") or {}).get("from"),
        "mcp": ((_pick(m, "time") or {}).get("scope") or {}).get("from"),
    }, "시간 표현이 세 창구에서 같은 범위로 읽힌다")

    # ── 4. 시간 표현 ────────────────────────────────────────────────
    print("\n[4] 시간 표현 (CLI `time` · GET /api/time)")
    c, why = cli_json(env, ["time", "지난주", "--json"])
    check("CLI time --json 이 파싱된다", c is not None, why)
    w = web.get("/api/time?q=" + urllib.parse.quote("지난주"))
    for k in ("from", "to", "kind"):
        same("시간 %s" % k, {"cli": (c or {}).get(k), "web": w.get(k)})

    # ── 5. 채널 검색 ────────────────────────────────────────────────
    print("\n[5] 채널 검색 (CLI `search` · POST /api/search · wiki_search)")
    sq = "DMA 오버런"
    c, why = cli_json(env, ["search", "fts,vector", sq, "--mode", "and", "--k", "5", "--json"])
    check("CLI search --json 이 파싱된다", c is not None, why)
    w = web.post("/api/search", {"q": sq, "channels": ["fts", "vector"], "mode": "and", "k": 5})
    m = mcp.call("wiki_search", {"query": sq, "channels": ["fts", "vector"], "mode": "and", "k": 5})
    ids = lambda d: [r.get("chunk_id") for r in ((d or {}).get("rows") or [])] or None
    same("검색 결과 chunk_id 목록", {
        "cli": ids(c), "web": ids(w) or ids((w or {}).get("result")), "mcp": ids({"rows": _pick(m, "rows")}),
    })
    same("검색 mode", {"cli": (c or {}).get("mode"), "web": w.get("mode"), "mcp": _pick(m, "mode")})

    # ── 6. 문서 유형 필터가 실제로 거르는가 ──────────────────────────
    # 코퍼스마다 유형 이름이 다르므로 **거르지 않은 결과에서 실제로 나온 유형**을 골라 쓴다.
    # 아무 유형이나 넣으면 세 창구가 나란히 빈 결과를 주고, 그 비교는 통과가 아니라 무의미다.
    print("\n[6] 문서 유형 필터 (세 창구가 같은 것을 거른다)")
    plain = web.post("/api/search", {"q": sq, "channels": ["fts"], "mode": "or", "k": 10})
    plain = plain.get("result") if isinstance(plain.get("result"), dict) else plain
    # 검색 행에는 doc_type 이 들어 있지 않다(문서 목록이 그것을 들고 있다) — 문서 목록으로 유형을 붙인다.
    dtmap = {d["doc_id"]: d.get("doc_type") for d in (web.get("/api/docs") or [])}
    dt = next((dtmap.get(r.get("doc_id")) for r in (plain.get("rows") or []) if dtmap.get(r.get("doc_id"))), None)
    if check("거를 문서 유형을 골랐다", bool(dt),
             dt or "검색 결과의 문서에 doc_type 이 없다 — rows=%s dtmap=%s" % (
                 [r.get("doc_id") for r in (plain.get("rows") or [])][:3], list(dtmap.items())[:3])):
        c, _ = cli_json(env, ["search", "fts", sq, "--k", "10", "--doc-types", dt, "--json"])
        w = web.post("/api/search", {"q": sq, "channels": ["fts"], "mode": "or", "k": 10, "doc_types": [dt]})
        w = w.get("result") if isinstance(w.get("result"), dict) else w
        m = mcp.call("wiki_search", {"query": sq, "channels": ["fts"], "mode": "or", "k": 10, "doc_types": [dt]})
        same("유형 `%s` 필터 후 chunk_id 목록" % dt, {
            "cli": ids(c), "web": ids(w), "mcp": ids({"rows": _pick(m, "rows")}),
        })
        n_all, n_f = len(plain.get("rows") or []), len((w or {}).get("rows") or [])
        check("필터가 실제로 걸렀다 (전체 %d → %d)" % (n_all, n_f), 0 < n_f <= n_all,
              "필터 결과가 비었거나 오히려 늘었다")
        check("남은 행이 모두 유형 `%s` 다" % dt,
              all(dtmap.get(r.get("doc_id")) == dt for r in ((w or {}).get("rows") or [])),
              "필터를 걸었는데 다른 유형이 섞여 있다")

    # ── 7. 엔티티 ──────────────────────────────────────────────────
    print("\n[7] 엔티티 (CLI `entity` · GET /api/entity · wiki_entity)")
    ename = None
    try:
        g = web.get("/api/graph?limit=1")
        nodes = g.get("nodes") or g.get("entities") or []
        ename = (nodes[0].get("name") if nodes else None)
    except Exception:
        pass
    if not ename:
        ename = "ISSUE-2001"
    c, why = cli_json(env, ["entity", ename, "--json"])
    check("CLI entity --json 이 파싱된다", c is not None, why)
    w = web.get("/api/entity?name=" + urllib.parse.quote(ename))
    m = mcp.call("wiki_entity", {"name": ename})
    eid = lambda d: ((d or {}).get("entity") or {}).get("entity_id") or ((d or {}).get("entity") or {}).get("id")
    same("엔티티 id (이름 `%s` 로 열었을 때)" % ename, {
        "cli": eid(c), "web": eid(w), "mcp": eid({"entity": _pick(m, "entity")}),
    })

    # ── 7b. 무리(커뮤니티) 상세 (2026-09-24) ────────────────────────
    print("\n[7b] 무리 상세 (CLI `graph community` · GET /api/community · wiki_community)")
    comms = (g.get("communities") if isinstance(g, dict) else None) or []
    cid = comms[0]["community"] if comms else 0
    c, why = cli_json(env, ["graph", "community", "--community", str(cid), "--json"])
    check("CLI graph community --json 이 파싱된다", c is not None, why)
    w = web.get("/api/community?id=%s" % cid)
    m = mcp.call("wiki_community", {"id": cid})
    mem = lambda d: [x.get("id") for x in ((d or {}).get("members") or [])][:10] or None
    same("무리 #%s 구성원 (상위 10)" % cid, {"cli": mem(c), "web": mem(w), "mcp": mem({"members": _pick(m, "members")})})
    same("무리 #%s 이름" % cid, {"cli": (c or {}).get("label"), "web": (w or {}).get("label"), "mcp": _pick(m, "label")})

    # ── 8. 문서 상세 ────────────────────────────────────────────────
    print("\n[8] 문서 상세 (GET /api/doc · wiki_doc)")
    docs = web.get("/api/docs")
    did = docs[0]["doc_id"] if docs else None
    if check("비교할 문서가 있다", bool(did), did or ""):
        w = web.get("/api/doc?id=" + urllib.parse.quote(did))
        m = mcp.call("wiki_doc", {"id": did})
        # wiki_doc 은 **사람(LLM)이 읽는 글**로 답한다 — 청크 배열이 아니라 본문이다.
        # 그래서 비교는 "같은 문서의 같은 개수"로 한다: 문서 머리줄과 `chunks: N`.
        n_web = len((w or {}).get("chunks") or []) or None
        mt = m.get("text") or ""
        mm = re.search(r"^chunks:\s*(\d+)", mt, re.M)
        same("문서 %s 의 청크 수" % did, {"web": n_web, "mcp": int(mm.group(1)) if mm else None})
        check("MCP wiki_doc 이 같은 문서를 연다", mt.splitlines()[:1] == ["# %s" % did] if mt else False,
              (mt.splitlines()[:1] or [""])[0][:80])

    # ── 9. 그래프 빌드 규칙 ─────────────────────────────────────────
    print("\n[9] 그래프 빌드 규칙 (CLI `graph-rules types` · GET /api/graph_rules · wiki_graph_rules)")
    c, why = cli_json(env, ["graph-rules", "types", "--json"])
    check("CLI graph-rules types --json 이 파싱된다", c is not None, why)
    w = web.get("/api/graph_rules")
    m = mcp.call("wiki_graph_rules", {"action": "types"})
    ty = lambda d: sorted((d or {}).get("entity_types") or (d or {}).get("types") or []) or None
    same("엔티티 유형 목록", {"cli": ty(c), "web": ty(w), "mcp": ty({"entity_types": _pick(m, "entity_types")})})

    # ── 10. 질의 규칙 설명 ──────────────────────────────────────────
    print("\n[10] 질의 규칙 설명 (CLI `rules explain` · GET /api/query_rules/explain · wiki_rules)")
    term = None
    try:
        qr = json.load(open(os.path.join(ROOT, "query_rules.json"), encoding="utf-8"))
        for _t, mm in qr.items():
            if isinstance(mm, dict) and mm:
                term = next(iter(mm))
                break
    except Exception:
        pass
    if check("설명할 용어가 있다 (query_rules.json)", bool(term), term or ""):
        c, why = cli_json(env, ["rules", "explain", term, "--json"])
        check("CLI rules explain --json 이 파싱된다", c is not None, why)
        w = web.get("/api/query_rules/explain?term=" + urllib.parse.quote(term))
        m = mcp.call("wiki_rules", {"action": "explain", "term": term})
        exp = lambda d: json.dumps((d or {}).get("expands_to") or (d or {}).get("entries") or [],
                                   ensure_ascii=False, sort_keys=True) if d else None
        same("용어 `%s` 가 무엇으로 퍼지나" % term, {
            "cli": exp(c), "web": exp(w),
            "mcp": exp((m.get("structured") or {}) or None),
        })
        same("용어 `%s` 의 정규화형" % term, {
            "cli": (c or {}).get("normalized"), "web": w.get("normalized"),
            "mcp": (m.get("structured") or {}).get("normalized"),
        })

    # ── 11. 질의 (근거와 인용) ─────────────────────────────────────
    print("\n[11] 질의 (CLI `query` · POST /api/query · wiki_query) — 근거·인용·판정")
    qq = "ISSUE-2001 의 원인과 수정 CL 은?"
    c, why = cli_json(env, ["query", qq, "--json"])
    check("CLI query --json 이 파싱된다", c is not None, why)
    w = web.post("/api/query", {"q": qq})
    m = mcp.call("wiki_query", {"question": qq})
    cres = (c or {}).get("result") or (c or {})
    wres = w.get("result") or w
    cite = lambda hits: {str(h["n"]): h["chunk_id"] for h in (hits or []) if h.get("n") is not None and h.get("in_context")} or None
    mcites = _pick(m, "citations")
    same("인용 [C#] → chunk_id", {
        "cli": cite(cres.get("hits")), "web": cite(wres.get("hits")),
        "mcp": {str(x["n"]): x["chunk_id"] for x in mcites} if mcites else None,
    })
    same("답변 본문", {"cli": cres.get("answer"), "web": wres.get("answer"), "mcp": _pick(m, "answer")})
    same("result_type", {"cli": cres.get("result_type"), "web": wres.get("result_type"), "mcp": _pick(m, "result_type")})

    # ── 12. 요청 단위 오버라이드가 세 창구에서 같은 뜻인가 ───────────
    print("\n[12] 요청 단위 오버라이드 (output_mode=fused)")
    c, why = cli_json(env, ["query", qq, "--output", "fused", "--json"])
    w = web.post("/api/query", {"q": qq, "overrides": {"output_mode": "fused"}})
    m = mcp.call("wiki_query", {"question": qq, "output_mode": "fused"})
    same("output_mode=fused 의 result_type", {
        "cli": ((c or {}).get("result") or c or {}).get("result_type"),
        "web": (w.get("result") or w).get("result_type"),
        "mcp": _pick(m, "result_type"),
    }, "셋 다 candidates_fused 여야 한다")

    # ── 13. 거절도 같은가 ──────────────────────────────────────────
    print("\n[13] 실패 정렬 — 빈 용어는 세 창구가 모두 거절해야 한다")
    code = None
    try:
        web.get("/api/query_rules/explain?term=")
    except urllib.error.HTTPError as e:
        code = e.code
    check("Web 은 빈 term 을 400 으로 거절한다", code == 400, "code=%s" % code)
    m = mcp.call("wiki_rules", {"action": "explain", "term": ""})
    check("MCP 도 빈 term 을 오류로 돌려준다", "_error" in m or "error" in json.dumps(m, ensure_ascii=False),
          json.dumps(m, ensure_ascii=False)[:120])
    p = subprocess.run([PY, "-m", "llmwiki", "rules", "explain", "", "--json"], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    check("CLI 도 빈 용어를 0 이 아닌 코드로 거절한다", p.returncode != 0, "exit=%d" % p.returncode)


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CLI·Web·MCP 세 창구 동작 동등성 (실제 프로세스)")
    ap.add_argument("--port", type=int, default=8879)
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 지우지 않는다")
    ns = ap.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="lwtri_")
    env = setup_env(tmp)
    proc = None
    t0 = time.time()
    try:
        print("빌드 (mock LLM · hash 임베더, 샘플 코퍼스)…")
        # `build --full` 은 파괴적 등급이라 확인을 받는다 — 비대화형이므로 --yes 로 통과시킨다
        # (임시 폴더의 빈 색인이라 지울 것도 없다).
        b = subprocess.run([PY, "-m", "llmwiki", "build", "--full", "--yes"], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        if not check("빌드", b.returncode == 0, (b.stderr or b.stdout)[-200:]):
            raise SystemExit(1)
        base = "http://127.0.0.1:%d" % ns.port
        proc = subprocess.Popen([PY, "-m", "llmwiki", "serve", "--host", "127.0.0.1", "--port", str(ns.port)],
                                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        web = Web(base)
        for _ in range(120):
            try:
                web.get("/api/status", timeout=5)
                break
            except Exception:
                time.sleep(0.5)
        else:
            check("서버 기동", False, "포트 %d 가 응답하지 않는다" % ns.port)
            raise SystemExit(1)
        check("서버 기동", True, base)
        compare_all(env, web, Mcp(base))
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        if not ns.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print("\n임시 폴더 유지: %s" % tmp)

    out = {"checks": RESULTS, "failed": FAILED, "n": len(RESULTS), "ok": len(RESULTS) - len(FAILED),
           "sec": round(time.time() - t0, 1)}
    with open(os.path.join(HERE, "verify_tri_surface_result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n검사 %d건 · 통과 %d · 실패 %d · %.1fs" % (out["n"], out["ok"], len(FAILED), out["sec"]))
    for name in FAILED:
        print("  FAIL %s" % name)
    print("\nRESULT %s" % ("PROBLEMS" if FAILED else "OK"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
