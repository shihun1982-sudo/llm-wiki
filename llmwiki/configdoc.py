# -*- coding: utf-8 -*-
"""`docs/CONFIG_REFERENCE.md` 생성기 — 사용자가 손으로 고치는 **모든 파일과 키**의 단일 레퍼런스.

왜 생성하나: 설정 키가 104개(config) + 66개(토글) + 160개(튜닝) + 규칙/보안/서버/스케줄 파일까지 있다.
손으로 표를 쓰면 다음 회차에 반드시 낡는다. 그래서 코드의 레지스트리(`config.Settings`/`Toggles`/`SETTING_HELP`/
`TOGGLE_HELP`/`TOGGLE_GROUPS`/`TOGGLE_EFFECT`, `tuning.TUNABLES`, `config._PATH_DEFAULTS`)에서 직접 만든다.
코드가 바뀌면 `python -m llmwiki config doc` 한 번으로 문서가 따라온다.

담는 것: 파일마다 ① 무엇을 정하나 ② 어디서 바꾸나(파일·CLI·Web) ③ 언제 반영되나(즉시/재시작/리빌드)
④ 키 표(기본값·설명·영향). 값을 바꿀 때 무엇을 다시 해야 하는지가 이 문서의 핵심이다.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .config import (ROOT, Settings, Toggles, SETTING_HELP, TOGGLE_HELP, TOGGLE_GROUPS, TOGGLE_EFFECT,
                     _PATH_DEFAULTS, all_paths)
from .tuning import TUNABLES, STAGES

DOC_PATH = os.path.join(ROOT, "docs", "CONFIG_REFERENCE.md")

# 파일별 안내 — 키 표는 레지스트리에서 만들고, "무엇을 정하나/언제 반영되나" 는 여기서 준다.
FILES: List[Dict[str, str]] = [
    {"name": "config.json", "path": "config", "what": "코퍼스 위치 · 역할별 LLM · 임베딩 · 토글 66개 · 운영 수치(로그·DB·서버 바인드 등) — **가장 많이 고치는 파일**",
     "where": "Web Settings › config.json / 모델·프로바이더 · CLI `config show|set`, `models set` · 파일 직접",
     "when": "즉시 (서버는 `config reload` 또는 Settings 의 `↻ config.json 다시 읽기`). `web_*`·`mcp_*` 는 다음 기동부터",
     "example": "setup/config.example.json · setup/config.example.pat-gateway.json · setup/config.example.headless.json"},
    {"name": ".env", "path": "env", "what": "API 키·PAT 과 모든 설정의 환경변수 오버라이드(`LLMWIKI_<KEY>`, `LLMWIKI_TOGGLE_<NAME>`)",
     "where": "파일 직접 (값은 화면에 **마스킹**되어 보인다: Settings › config.json 탭 하단 `.env` 패널 · CLI `config env`)",
     "when": "`config reload --env` 또는 Web 의 `.env 다시 읽기`. 프로세스 환경변수가 파일보다 우선",
     "example": "setup/.env.example"},
    {"name": "tuning.json", "path": "tuning", "what": "알고리즘 상수 %d개 (청킹·임베딩·그래프·라우터·검색·융합·리랭크·컨텍스트·근거·claim·포렌식·메모리)" % len([t for t in TUNABLES if t["source"] == "tuning"]),
     "where": "Web 🧭 Pipeline(단계별) · Settings › 튜닝 · CLI `tuning show|set|reset` · 파일 직접",
     "when": "즉시 (`tuning reload` 또는 Web). `rebuild` 표시가 붙은 키는 **전체 리빌드** 필요",
     "example": "setup/tuning.example.json (`config fill-defaults --tuning --examples` 로 생성)"},
    {"name": "query_rules.json", "path": "query_rules", "what": "질의 확장 사전 — acronym · synonym(양방향) / alias · related · exclude(일방) / compound",
     "where": "Web Settings › 질의 규칙 사전 · CLI `rules show|add|remove|test|explain|lint` · 파일 직접",
     "when": "즉시 (파일 mtime 을 보고 다시 읽는다)", "example": "setup/query_rules.example.modem.json"},
    {"name": "data/rules.json", "path": "rules", "what": "지식 그래프 규칙 — 엔티티 사전 · 관계 정규식 · ID 패턴(`id_patterns`) · 결정적 링크(`link_rules`)",
     "where": "Web Knowledge › 그래프 규칙 · CLI `rules`(그래프) · 파일 직접",
     "when": "**그래프 재빌드** (`build graph`) 뒤 반영. 진단은 `graph profile --compare`", "example": "setup/rules.example.modem.json"},
    {"name": "stopwords.json", "path": "stopwords", "what": "질의 키워드 추출에서 제거할 불용어",
     "where": "파일 직접", "when": "즉시 (mtime). 없으면 코드 기본값으로 생성", "example": "setup/stopwords.example.json"},
    {"name": "presets.json", "path": "presets", "what": "토글+튜닝+config 묶음 (quality · speed · token · offline · deep_research)",
     "where": "Web Settings › 프리셋 · 사이드바 체크(요청 단위) · CLI `preset list|show|apply|diff`",
     "when": "즉시. 사이드바 체크는 **그 요청에만**, `preset apply --save` 는 파일에 기록", "example": "(저장소 기본값)"},
    {"name": "models.json", "path": "models", "what": "쓸 수 있는 모델 카탈로그 (LLM · 임베딩 · 리랭크). 화면 드롭다운의 원천이며 provider 자동 해석에도 쓰인다",
     "where": "Web Settings › 카탈로그(추가·삭제·전체 연결 테스트) · CLI `models list|catalog add|remove|discover`",
     "when": "즉시 (mtime)", "example": "setup/models.example.json"},
    {"name": "agents.json", "path": "agents", "what": "headless 에이전트 실행 명령과 재시도 정책 (opencode · claude · codex · mock)",
     "where": "Web Settings › 모델 하단 `agents.json` · 파일 직접", "when": "즉시", "example": "setup/agents.example.json"},
    {"name": "security.json", "path": "security", "what": "로그인(로컬·SSO·API 키) · 역할 6단계 · 권한 표 · 익명 접속 · CLI 게이트 · 파괴적 작업 정책 · 요청 overrides 허용 목록",
     "where": "Web Settings › 보안·사용자 · CLI `users`, `security`, `apikey`", "when": "즉시 (`security reload` 또는 화면의 '다시 읽기')",
     "example": "setup/security.example.json"},
    {"name": "server.json", "path": "server", "what": "동시성·대기열·시간 제한·속도 제한·세션·차단·점검 모드·모니터 공개 범위, `mcp` 절(페더레이션 캐시·타임아웃·상한), `debug.expose_trace`",
     "where": "Web 관리 › 서버 모니터 · CLI `server limits|block|maintenance`", "when": "`server` 명령·화면은 즉시. 파일을 직접 고쳤으면 **reload** 필요",
     "example": "setup/server.example.json"},
    {"name": "schedule.json", "path": "schedule", "what": "정해진 시각·주기에 돌릴 작업 (동작 19종)",
     "where": "Web Settings › 스케줄 · CLI `schedule add|enable|run|validate`", "when": "즉시 (서버가 mtime 을 보고 다시 읽는다)",
     "example": "setup/schedule.example.json"},
    {"name": "mcp_sources.json", "path": "mcp_sources", "what": "다른 RAG·MCP 서버·REST 검색 API 연결 (retrieve 채널 · expose 도구 중계 · ingest)",
     "where": "Web Corpus › MCP 소스 · CLI `mcp-source list|test|retrieve|federated`", "when": "즉시", "example": "setup/mcp_sources.example.json"},
    {"name": "pins.json", "path": "pins", "what": "고정 근거 — 특정 질의·문서를 항상 후보에 넣는다",
     "where": "Web Settings › Pin · CLI `pin add|list|test|remove`", "when": "즉시", "example": "(저장소 기본값)"},
    {"name": "prompts/*.md", "path": "prompts_dir", "what": "역할별 LLM 프롬프트 (답변·리랭크·추출·검증·융합 검토·리랭크 선택·앙상블 취합·best_effort 등)",
     "where": "Web Settings › 프롬프트 · CLI `prompts list|show|reset` · 파일 직접", "when": "즉시 (mtime). 없으면 코드 기본값으로 생성", "example": "(첫 실행 시 생성)"},
    {"name": "schemas/*.json", "path": "schemas_dir", "what": "문서 유형별 front matter 스키마 · 추론 규칙 · 마이그레이션",
     "where": "파일 직접", "when": "lint·빌드 때", "example": "(저장소 기본값)"},
    {"name": "eval/questions.json", "path": "eval", "what": "회귀 평가셋 — 자기 코퍼스 질문으로 바꾼다",
     "where": "Web Quality › 평가 · 파일 직접", "when": "즉시", "example": "(저장소 기본값)"},
    {"name": "data/profiles.json", "path": None, "what": "계정별 Web UI 설정 (테마·토글·사이드바 배치·고정 탭 등). 서버 공용 설정과 분리",
     "where": "Web 헤더 `💾 내 설정 저장`", "when": "즉시 (로그인 시 자동 적용)", "example": "(자동 생성)"},
]


def _fmt(v: Any) -> str:
    if isinstance(v, str):
        return '`"%s"`' % v if v else "`\"\"`"
    if isinstance(v, bool):
        return "`%s`" % ("true" if v else "false")
    if isinstance(v, (list, dict)):
        s = json.dumps(v, ensure_ascii=False)
        return "`%s`" % (s if len(s) <= 60 else s[:57] + "…")
    return "`%s`" % v


def _defaults() -> Dict[str, Any]:
    s = Settings()
    d = {k: getattr(s, k) for k in Settings.__dataclass_fields__ if k != "toggles"}
    return d


def _axes(name: str) -> str:
    e = TOGGLE_EFFECT.get(name) or {}
    lab = {"q": "품질", "s": "속도", "t": "토큰"}
    out = []
    for k in ("q", "s", "t"):
        if e.get(k):
            out.append("%s%s" % (lab[k], "↑" if e[k] > 0 else "↓"))
    return " · ".join(out) or "—"


def config_markdown() -> str:
    L: List[str] = []
    a = L.append
    defs = _defaults()
    paths = all_paths()
    a("# CONFIG REFERENCE — 사용자가 고치는 모든 파일과 키 (자동 생성)")
    a("")
    a("> **이 문서는 `python -m llmwiki config doc` 이 코드의 레지스트리에서 생성한다.** 손으로 고치지 말고 코드를 고친 뒤 다시 생성한다.")
    a("> 설정을 *어떻게* 바꾸는지(절차·권한·검증)는 [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) 와 [SETTINGS_SYNC.md](SETTINGS_SYNC.md),")
    a("> 단계별로 어떤 값이 어디에 작용하는지는 [OPTIMIZATION_GUIDE.md](OPTIMIZATION_GUIDE.md) 와 [PIPELINE_PAGE.md](PIPELINE_PAGE.md) 에 있다.")
    a("")
    a("## 0. 원칙 네 가지")
    a("")
    a("1. **모든 설정은 파일에 있다.** 코드를 고쳐야 바뀌는 값은 없다 — 새 환경으로 폴더를 복사하고 파일만 채우면 동작한다.")
    a("2. **기본값도 파일에 적는다.** `python -m llmwiki config doc` 로 이 표를 보고, `config fill-defaults --all` 로 빠진 키를 기본값째 채운다. 나중에 값을 바꿀 때 *줄을 고치기만* 하면 된다.")
    a("3. **우선순위는 항상 같다**: CLI 플래그 > 환경변수(`LLMWIKI_<KEY>`) > 파일 > 코드 기본값. 지금 유효한 값과 출처는 `config show --effective`.")
    a("4. **요청 단위 변경은 파일을 바꾸지 않는다.** 사이드바 토글·🧭 Pipeline 의 '이번 요청에만'·`overrides` 는 그 요청에만 적용된다.")
    a("")
    a("## 1. 파일 지도")
    a("")
    a("| 파일 | 무엇을 정하나 | 어디서 바꾸나 | 언제 반영되나 | 원본 예시 |")
    a("|---|---|---|---|---|")
    for f in FILES:
        a("| **%s** | %s | %s | %s | %s |" % (f["name"], f["what"], f["where"], f["when"], f["example"]))
    a("")
    a("모든 파일은 위치를 옮길 수 있다 — `LLMWIKI_<이름>_PATH` 환경변수. 지금 쓰는 경로는 `python -m llmwiki config paths`:")
    a("")
    a("| 이름 | 기본 위치 | 환경변수 |")
    a("|---|---|---|")
    for k in sorted(_PATH_DEFAULTS):
        a("| `%s` | `%s` | `LLMWIKI_%s_PATH` |" % (k, _PATH_DEFAULTS[k], k.upper()))
    a("")
    a("> 저장은 모두 **원자적**이다(`llmwiki/atomicio.py`) — 여러 관리자가 같은 순간에 저장해도 파일이 반쪽으로 남지 않는다.")
    a("")

    # ---- config.json 키 ----
    a("## 2. `config.json` — 설정 키 %d개" % len(defs))
    a("")
    a("비워 두면 코드 기본값을 쓴다. 역할별 LLM(`llm_roles.<role>`)은 §3, 토글은 §4 에 따로 있다.")
    a("")
    a("| 키 | 기본값 | 설명 |")
    a("|---|---|---|")
    for k in sorted(defs):
        a("| `%s` | %s | %s |" % (k, _fmt(defs[k]), (SETTING_HELP.get(k) or "").replace("|", "\\|") or "—"))
    a("")

    # ---- 역할별 LLM ----
    a("## 3. 역할별 LLM — `config.json` 의 `llm_roles.<role>`")
    a("")
    a("역할 %d개: %s" % (len(Settings.LLM_ROLES), ", ".join("`%s`" % r for r in Settings.LLM_ROLES)))
    a("")
    a("각 역할에 줄 수 있는 값: %s. **비우면 전역값을 상속**한다." % ", ".join("`%s`" % x for x in Settings.LLM_ROLE_ATTRS))
    a("")
    a("| 속성 | 뜻 | 비우면 |")
    a("|---|---|---|")
    a("| `provider` | `auto` \\| `anthropic` \\| `openai` \\| `ollama` \\| `headless:<agent>` \\| `mock` \\| `none` | 모델이 `models.json` 에 한 provider 로만 있으면 **그 provider**, 아니면 전역 `llm_provider` |")
    a("| `model` | 모델 id (카탈로그에 없어도 동작) | 전역 `llm_model` |")
    a("| `effort` | `low` \\| `medium` \\| `high` | `llm_effort` (answer 역할은 `answer_effort`) |")
    a("| `timeout_s` · `retries` · `backoff` · `backoff_s` · `backoff_max_s` · `budget_s` | 호출 1회의 시간 제한과 재시도 정책 | `llm_timeout` · `llm_retries` · `llm_retry_backoff*` · `llm_budget_s` |")
    a("| `circuit_failures` · `circuit_cooldown_s` | 연속 실패 시 잠시 호출을 끊는다 | `llm_circuit_failures` · `llm_circuit_cooldown_s` |")
    a("| `max_tokens` | 출력 토큰 상한 | 단계 기본값: %s |" % ", ".join("%s %d" % (k, v) for k, v in sorted(Settings.ROLE_DEFAULT_MAX_TOKENS.items())))
    a("| `ensemble` | **한 역할에 LLM 최대 %d개 병렬 + 취합** — [ENSEMBLE.md](ENSEMBLE.md) | 꺼짐(단일 LLM) |" % Settings.ENSEMBLE_MAX_MEMBERS)
    a("")
    a("`ensemble` 의 모양 (기본값: %s):" % ", ".join("`%s`=%s" % (k, v) for k, v in Settings.ENSEMBLE_DEFAULTS.items()))
    a("")
    a("```jsonc")
    a('"llm_roles": {')
    a('  "answer": {')
    a('    "model": "claude-sonnet-5",')
    a('    "ensemble": {')
    a('      "enabled": true,')
    a('      "members": [                       // 최대 %d개 · enabled:false 이거나 model 이 비면 쓰지 않는다' % Settings.ENSEMBLE_MAX_MEMBERS)
    a('        {"enabled": true,  "provider": "anthropic", "model": "claude-sonnet-5", "weight": 1.5},')
    a('        {"enabled": true,  "provider": "openai",    "model": "gpt-4o-mini",     "weight": 1.0},')
    a('        {"enabled": false, "provider": "",          "model": "",                "weight": 1.0}')
    a('      ],')
    a('      "wait": "all",                     // all = 전부 기다림 · timeout = timeout_s 까지 온 것만')
    a('      "timeout_s": 120,')
    a('      "min_results": 1,                  // 이보다 적게 오면 앙상블 실패 → 단일 결과')
    a('      "aggregator": {"provider": "anthropic", "model": "claude-sonnet-5"},   // 비우면 역할 모델')
    a('      "prompt": "ensemble_merge"         // prompts/ensemble_merge.md — 가중치 해석 규칙을 여기에 쓴다')
    a('    }')
    a('  }')
    a('}')
    a("```")
    a("")

    # ---- 토글 ----
    a("## 4. 토글 %d개 — `config.json` 의 `toggles`" % len(Toggles.__dataclass_fields__))
    a("")
    a("요청 단위로도 바꾼다: 사이드바 체크 · 🧭 Pipeline · CLI 플래그(`--rerank` / `--no-rerank`) · `overrides`.")
    a("`품질↑` 는 켰을 때 그 축이 좋아진다는 뜻, `속도↓` 는 느려진다는 뜻이다.")
    a("")
    tg_defaults = {k: getattr(Toggles(), k) for k in Toggles.__dataclass_fields__}
    seen: set = set()
    for g in TOGGLE_GROUPS:
        names = [t for t in g["toggles"] if t in tg_defaults]
        if not names:
            continue
        a("### %s" % g["title"])
        if g.get("hint"):
            a("")
            a("%s" % g["hint"])
        a("")
        a("| 토글 | 기본 | 켜면 | 설명 |")
        a("|---|---|---|---|")
        for t in names:
            seen.add(t)
            a("| `%s` | %s | %s | %s |" % (t, "on" if tg_defaults[t] else "off", _axes(t),
                                            (TOGGLE_HELP.get(t) or "").replace("|", "\\|") or "—"))
        a("")
    rest = [t for t in tg_defaults if t not in seen]
    if rest:
        a("### (그룹 미지정)")
        a("")
        a("| 토글 | 기본 | 켜면 | 설명 |")
        a("|---|---|---|---|")
        for t in rest:
            a("| `%s` | %s | %s | %s |" % (t, "on" if tg_defaults[t] else "off", _axes(t), (TOGGLE_HELP.get(t) or "—").replace("|", "\\|")))
        a("")

    # ---- 튜닝 ----
    tun = [t for t in TUNABLES if t["source"] == "tuning"]
    cfg_tun = [t for t in TUNABLES if t["source"] == "config"]
    a("## 5. `tuning.json` — 단계별 알고리즘 상수 %d개" % len(tun))
    a("")
    a("단계 순서대로. `리빌드` 열이 ✔ 이면 값을 바꾼 뒤 **전체 리빌드**(`build --full`)가 필요하다 — 색인에 그 값이 박혀 있기 때문이다.")
    a("값을 바꿔 가며 효과를 보려면 🧭 Pipeline 의 **범위 스윕**([SWEEP.md](SWEEP.md)).")
    a("")
    by_stage: Dict[str, List[Dict[str, Any]]] = {}
    for t in tun:
        by_stage.setdefault(t["stage"], []).append(t)
    for st, title in STAGES.items():
        rows = by_stage.get(st) or []
        if not rows:
            continue
        a("### `%s` — %s" % (st, title))
        a("")
        a("| 키 | 기본 | 범위 | 리빌드 | 설명 · 영향 |")
        a("|---|---|---|---|---|")
        for t in rows:
            rng = ""
            if t.get("choices"):
                rng = " \\| ".join("`%s`" % c for c in t["choices"])
            elif t.get("min") is not None or t.get("max") is not None:
                rng = "%s~%s" % ("" if t.get("min") is None else t["min"], "" if t.get("max") is None else t["max"])
            desc = (t["desc"] or "").replace("|", "\\|")
            imp = (t.get("impact") or "").replace("|", "\\|")
            a("| `%s` | %s | %s | %s | %s%s |" % (t["key"], _fmt(t["default"]), rng or "—", "✔" if t.get("rebuild") else "",
                                                  desc, (" — " + imp) if imp else ""))
        a("")
    if cfg_tun:
        a("### 튜닝 표에 함께 보이지만 `config.json` 에 저장되는 키")
        a("")
        a("화면·문서에서 한자리에 보여 주려고 함께 등재한 것이다. **`tuning.json` 에 적으면 반영되지 않는다** — `config.json` 에 적는다.")
        a("")
        a("| 키 | 단계 | 기본 | 설명 |")
        a("|---|---|---|---|")
        for t in cfg_tun:
            a("| `%s` | `%s` | %s | %s |" % (t["key"], t["stage"], _fmt(t["default"]), (t["desc"] or "").replace("|", "\\|")))
        a("")

    # ---- 바꾼 뒤 할 일 ----
    a("## 6. 값을 바꾼 뒤 무엇을 해야 하나")
    a("")
    a("| 바꾼 것 | 해야 할 일 | 확인 |")
    a("|---|---|---|")
    a("| `config.json` 의 대부분 | 서버면 `config reload`(또는 Web 의 `↻ config.json 다시 읽기`) | `config show --effective` |")
    a("| `web_host`·`web_port`·`mcp_*` | **서버 재시작** | 기동 로그의 주소 |")
    a("| `.env` | `config reload --env` (또는 Web 의 `.env 다시 읽기`) | `config env` |")
    a("| `tuning.json` 의 `리빌드 ✔` 키 · `embed_provider`·`embed_model`·`embed_dim`·청킹 키 | **`build --full`** | `build verify` · `health` |")
    a("| `data/rules.json` | **`build graph`** | `graph profile --compare` |")
    a("| `query_rules.json` · `stopwords.json` · `prompts/*.md` · `models.json` · `agents.json` · `schedule.json` | 없음 (즉시) | `rules explain`, `prompts show`, `models list`, `schedule list` |")
    a("| `security.json` | `security reload` 또는 화면의 '다시 읽기' | `security show`, `security perms` |")
    a("| `server.json` | `server` 명령/화면은 즉시, 파일 직접 편집은 **reload** | `server limits` |")
    a("")
    a("바꾼 값이 **실제로 서버에 먹었는지** 양방향으로 확인하는 하네스가 있다: `python tools/verify/verify_settings_sync.py` ([SETTINGS_SYNC.md](SETTINGS_SYNC.md)).")
    a("")
    a("## 7. 자주 하는 변경 — 어디를 고치나")
    a("")
    a("| 하고 싶은 것 | 고칠 곳 |")
    a("|---|---|")
    a("| 내 문서를 색인한다 | `config.json` 의 `corpus_dirs` → `build --full` |")
    a("| 사내 게이트웨이(PAT)로 LLM 을 쓴다 | `config.json` 의 `llm_provider=openai` + `openai_base_url`, `.env` 의 `OPENAI_API_KEY` — [BRINGUP_GUIDE.md §4.1](BRINGUP_GUIDE.md) |")
    a("| Anthropic 을 쓴다 | `llm_roles.<role>.provider=anthropic` + `.env` 의 `ANTHROPIC_API_KEY` |")
    a("| opencode 같은 CLI 를 LLM 으로 쓴다 | `llm_roles.<role>.provider=headless:opencode` — [HEADLESS.md](HEADLESS.md) |")
    a("| 한 단계에 모델 여러 개를 물어본다 | `llm_roles.<role>.ensemble` — [ENSEMBLE.md](ENSEMBLE.md) |")
    a("| 답이 느리다 | 사이드바 `speed` 프리셋 → `llm_after_*`·`claim_check_llm`·`rerank_llm` 끄기 · `top_k_*` 줄이기 |")
    a("| 근거가 부족해도 답을 받고 싶다 | `answer_mode=best_effort` — [ANSWER_MODES.md](ANSWER_MODES.md) |")
    a("| 중간 산출물(융합·리랭크 결과)만 받고 싶다 | `output_mode=fused\\|reranked\\|context` |")
    a("| 채널별 가중치를 바꾼다 | `tuning.json` 의 `channel_w_*`, `*_topk_n/_topk_w/_tail_w` — [FUSION_TOPK.md](FUSION_TOPK.md) |")
    a("| 로그가 너무 쌓인다 | `log_total_max_mb`·`log_limit_action` — [LOG_QUOTA.md](LOG_QUOTA.md) |")
    a("| 외부에 서버를 연다 | `web_host`(기본 `0.0.0.0`) + `security.json` — [SECURITY.md](SECURITY.md) |")
    a("")
    a("---")
    a("")
    a("생성: `python -m llmwiki config doc` · 기준 경로: `%s`" % os.path.basename(paths.get("config", "config.json")))
    return "\n".join(L) + "\n"


def write_doc(path: str = "") -> str:
    path = path or DOC_PATH
    from . import atomicio
    atomicio.write_text(path, config_markdown())
    return path
