# -*- coding: utf-8 -*-
"""**단계 정렬 검증** — 같은 파이프라인 단계를 네 곳이 같은 이름으로 알고 있는지 본다 (2026-09-19).

왜 필요한가: 한 단계는 네 곳에 나뉘어 산다.

  1. **코드**   — `prof.stage("이름")` 이 만드는 trace 노드
  2. **레지스트리** — `llmwiki/architecture.py` 의 `FLOWS[*].stages[*].trace` (🧭 Pipeline · `arch` · 시간 제한)
  3. **라벨표** — `llmwiki/progress.py` 의 `STAGE_LABELS` (진행 패널의 한국어 이름)
  4. **손잡이** — 그 단계가 가리키는 토글(`TOGGLE_HELP`) · config 키(`SETTING_HELP`) · 튜닝 키(`tuning.TUNABLES`)

하나만 빠져도 화면에서 조용히 어긋난다. 실제로 2026-09-19 에 `fts_search_alt` · `fts_search_rules` 는
trace 에는 나오는데 레지스트리에 없어서 Pipeline 에서 토글·튜닝·시간 제한이 붙지 않았다.
이 하네스는 그런 어긋남을 **이름 단위로** 잡는다.

실행:
    python tools/verify/verify_stage_align.py
    python tools/verify/verify_stage_align.py --json
"""
from __future__ import annotations

import argparse
import dataclasses
import io
import json
import os
import re
import sys

# 콘솔이 cp949 여도 한글·기호 출력에서 죽지 않게 (다른 verify_* 와 같은 처리, 2026-09-24)
try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from llmwiki import architecture as A            # noqa: E402
from llmwiki import presets as _presets          # noqa: E402
from llmwiki import progress as _pg              # noqa: E402
from llmwiki import tuning as _tuning            # noqa: E402
from llmwiki.config import Settings, SETTING_HELP, TOGGLE_HELP  # noqa: E402

ROWS = []

# `prof.stage("x")` / `prof.skipped("x")` 처럼 **이름을 그 자리에 적는** 호출만 찾는다.
# 변수로 넘기는 경우(`stage_name=`)는 여기서 안 잡히므로 STAGE_LABELS 쪽 검사로 받는다.
CALL = re.compile(r'\.(?:stage|skipped|note_stage)\(\s*["\']([a-z0-9_]+)["\']')


def rec(ok, name, detail=""):
    ROWS.append({"ok": bool(ok), "name": name, "detail": str(detail)[:200]})
    print("%s %-56s %s" % ("OK  " if ok else "FAIL", name, str(detail)[:110]), flush=True)
    return bool(ok)


def emitted_stage_names():
    out = {}
    base = os.path.join(ROOT, "llmwiki")
    for root, _dirs, files in os.walk(base):
        if "__pycache__" in root:
            continue
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            for i, line in enumerate(io.open(p, encoding="utf-8").read().splitlines()):
                for m in CALL.finditer(line):
                    out.setdefault(m.group(1), []).append("%s:%d" % (f, i + 1))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="단계 정렬 검증 (코드 ↔ architecture ↔ progress ↔ 손잡이)")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)

    reg = A.registry()
    known, stage_of = set(), {}
    for fk, fl in reg["flows"].items():
        known.add(fk)
        for st in fl["stages"]:
            for tn in (st["trace"] or [st["key"]]):
                known.add(tn)
                stage_of.setdefault(tn, (fk, st["key"]))
            known.add(st["key"])

    print("== 1. 코드가 만드는 단계 이름이 레지스트리에 있는가")
    emitted = emitted_stage_names()
    missing = sorted(n for n in emitted if n not in known)
    rec(not missing, "모든 trace 단계가 architecture 레지스트리에 있다",
        "빠진 이름: %s" % (", ".join("%s(%s)" % (n, emitted[n][0]) for n in missing) if missing else "없음 (%d개 확인)" % len(emitted)))

    print("\n== 2. 진행 패널 라벨(progress.STAGE_LABELS)")
    lab = set(_pg.STAGE_LABELS)
    only_lab = sorted(lab - known)
    rec(not only_lab, "라벨표의 단계가 모두 레지스트리에 있다", "레지스트리에 없는 라벨: %s" % (", ".join(only_lab) or "없음"))
    no_lab = sorted(n for n in known if n not in lab and n not in reg["flows"])
    rec(not no_lab, "레지스트리의 단계가 모두 한국어 라벨을 가진다",
        "라벨 없는 단계: %s" % (", ".join(no_lab) or "없음 (%d개)" % len(lab)))

    print("\n== 3. 페이즈 · 튜닝 단계 배치")
    noph = [st["key"] for fl in reg["flows"].values() for st in fl["stages"] if not st.get("phase")]
    rec(not noph, "모든 단계가 페이즈(상위 단계)에 배치돼 있다", "미배치: %s" % (", ".join(noph) or "없음"))
    badts = [(st["key"], st["tuning_stage"]) for fl in reg["flows"].values() for st in fl["stages"]
             if st["tuning_stage"] and st["tuning_stage"] not in _tuning.STAGES]
    rec(not badts, "tuning_stage 가 tuning.STAGES 안에 있다", "어긋남: %s" % (badts or "없음"))

    print("\n== 4. 단계가 가리키는 손잡이가 실재하는가")
    fields = {f.name for f in dataclasses.fields(Settings)}
    bad_cfg, no_help, bad_tg = [], [], []
    for fk, fl in reg["flows"].items():
        for st in fl["stages"]:
            for k in st["settings"]:
                base = k.split(".")[0]
                if base not in fields:
                    bad_cfg.append("%s/%s→%s" % (fk, st["key"], k))
                elif base not in SETTING_HELP:
                    no_help.append(base)
            for t in st["toggles"]:
                if t not in TOGGLE_HELP:
                    bad_tg.append("%s/%s→%s" % (fk, st["key"], t))
    rec(not bad_cfg, "단계가 가리키는 config 키가 실재한다", "없는 키: %s" % (", ".join(bad_cfg) or "없음"))
    rec(not no_help, "그 config 키에 설명(SETTING_HELP)이 있다", "설명 없음: %s" % (", ".join(sorted(set(no_help))) or "없음"))
    rec(not bad_tg, "단계가 가리키는 토글이 실재한다", "없는 토글: %s" % (", ".join(bad_tg) or "없음"))

    print("\n== 5. 모든 config 키·토글에 설명이 있는가 (Settings 화면이 비지 않게)")
    miss_help = sorted(fields - set(SETTING_HELP))
    rec(not miss_help, "config 키 %d개 모두 설명이 있다" % len(fields), "설명 없음: %s" % (", ".join(miss_help) or "없음"))
    tg_fields = set(dataclasses.asdict(Settings().toggles)) if hasattr(Settings(), "toggles") else set()
    miss_tg = sorted(tg_fields - set(TOGGLE_HELP))
    rec(not miss_tg, "토글 %d개 모두 설명이 있다" % len(tg_fields), "설명 없음: %s" % (", ".join(miss_tg) or "없음"))

    print("\n== 6. 튜닝 키가 단계에 연결돼 있는가")
    used = {st["tuning_stage"] for fl in reg["flows"].values() for st in fl["stages"] if st["tuning_stage"]}
    orphan = sorted({p["key"] for p in _tuning.TUNABLES if p["stage"] not in used})
    rec(not orphan, "튜닝 키 %d개가 모두 어떤 단계에 속한다" % len(_tuning.TUNABLES),
        "고아 키: %s" % (", ".join(orphan[:20]) or "없음"))

    print("\n== 7. LLM 역할 ↔ 단계 (시간 제한·모델 선택이 붙는 자리)")
    mapped = {r for rs in A.STAGE_ROLES.values() for r in rs}
    rec(not (set(Settings.LLM_ROLES) - mapped), "역할 %d개가 모두 어떤 단계에 연결돼 있다" % len(Settings.LLM_ROLES),
        "안 붙은 역할: %s" % (", ".join(sorted(set(Settings.LLM_ROLES) - mapped)) or "없음"))
    rec(not (mapped - set(Settings.LLM_ROLES)), "STAGE_ROLES 가 없는 역할을 가리키지 않는다",
        "잘못된 역할: %s" % (", ".join(sorted(mapped - set(Settings.LLM_ROLES))) or "없음"))
    bad_stage = sorted(k for k in A.STAGE_ROLES if k not in {st["key"] for fl in reg["flows"].values() for st in fl["stages"]})
    rec(not bad_stage, "STAGE_ROLES 의 단계 키가 실재한다", "없는 단계: %s" % (", ".join(bad_stage) or "없음"))

    print("\n== 8. 시간 제한 표가 모든 trace 이름을 덮는가")
    s = Settings()
    try:
        from llmwiki import reqmgr as _rq
        lim = A.stage_limits(s, _rq.load_config())
    except Exception:
        lim = A.stage_limits(s, {})
    uncovered = sorted(n for n in known if n not in lim["trace"] and n not in reg["flows"])
    rec(not uncovered, "trace 이름 %d개 모두 시간 제한을 안다" % len(lim["trace"]),
        "제한을 모르는 이름: %s" % (", ".join(uncovered) or "없음"))

    print("\n== 9. 프리셋이 실재하는 키만 건드리는가")
    tk = {p["key"] for p in _tuning.TUNABLES}
    bad_pre = []
    for name, body in (_presets.load_presets() or {}).items():
        for k in (body.get("toggles") or {}):
            if k not in TOGGLE_HELP:
                bad_pre.append("%s toggle:%s" % (name, k))
        for k in (body.get("tuning") or {}):
            if k not in tk:
                bad_pre.append("%s tuning:%s" % (name, k))
        for k in (body.get("settings") or {}):
            if k.split(".")[0] not in fields:
                bad_pre.append("%s config:%s" % (name, k))
    rec(not bad_pre, "프리셋이 실재하는 키만 건드린다", "알 수 없는 키: %s" % (", ".join(bad_pre) or "없음"))

    bad = [r for r in ROWS if not r["ok"]]
    print("\n검사 %d개 중 %d개 통과 · 실패 %d개" % (len(ROWS), len(ROWS) - len(bad), len(bad)))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_stage_align_result.json")
    io.open(out, "w", encoding="utf-8").write(json.dumps(ROWS, ensure_ascii=False, indent=1))
    if ns.json:
        print(json.dumps(ROWS, ensure_ascii=False, indent=1))
    print("결과: %s" % out)
    print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
