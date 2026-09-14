# -*- coding: utf-8 -*-
"""CLI. Web UI 의 콘솔 탭은 이 argparse 를 그대로 in-process 로 실행하므로 CLI = Web 기능 집합.

  python -m llmwiki build [--full [--no-reset]] [--purge-logs] [--no-embed] [--llm-graph] ...   (--full 은 기본으로 DB 삭제 후 완전 초기화)
  python -m llmwiki query "질문" [--no-fts] [--no-vector] [--no-graph] [--no-rerank] [--no-llm-answer] [--trace]
  python -m llmwiki eval [--k 5]
  python -m llmwiki graph [--limit 50] | entity <id|name>
  python -m llmwiki evolve status|apply <id>|reject <id>|review|feedback <qid> <+1|-1> [note]
  python -m llmwiki wiki [--min-degree 1]
  python -m llmwiki config show|set key=value ...
  python -m llmwiki models show|test|set <role>_<provider|model|effort>=...   (역할별 LLM / 임베딩 설정)
  python -m llmwiki requests [list [--kind query] | show <id> | last]        (요청별 프로파일/디버그 trace)
  python -m llmwiki system [--target-docs 3000 --daily-new 20]               (확장성/캐시/워처 상태)
  python -m llmwiki maintenance vacuum|fts_optimize|wal_checkpoint|clear_cache|warm_cache|refresh_doc_refs
  python -m llmwiki watch [--interval 300] [--once]                           (코퍼스 변경 감시 → 증분 빌드)
  python -m llmwiki tuning show|set k=v …|reset [k]|doc                         (단계별 튜닝 파라미터, tuning.json / docs/TUNING.md)
  python -m llmwiki arch [--flow query|build|evolve|watch]                    (구조·흐름·토글/CLI 영향 텍스트 도식)
  python -m llmwiki mcp                                                       (MCP stdio 서버: wiki_query/search/entity/status)
  python -m llmwiki serve [--port 8765]
  python -m llmwiki health [--quick] | preset list|show|apply|diff | logs tail|grep|files | prompts list|show|reset
  공통: --debug 0|1|2 (프로파일 상세도), --preset quality,token, --trace, --json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from contextlib import redirect_stdout
from typing import Any, Dict, List, Optional

import time

from .config import Toggles, Settings, load_settings, save_settings, apply_overrides
from .profiler import jsonable
from . import progress as _pg


def _add_toggle_flags(p: argparse.ArgumentParser) -> None:
    for name in Toggles.__dataclass_fields__:
        dash = name.replace("_", "-")
        p.add_argument("--%s" % dash, dest=name, action="store_true", default=None, help="enable %s" % name)
        p.add_argument("--no-%s" % dash, dest=name, action="store_false", default=None, help="disable %s" % name)
    p.add_argument("--llm", dest="llm_provider", default=None, help="auto|anthropic|ollama|mock|none")
    p.add_argument("--embed-provider", dest="embed_provider", default=None, help="auto|hash|voyage|ollama|st")
    p.add_argument("--model", dest="llm_model", default=None)
    for role in Settings.LLM_ROLES:
        p.add_argument("--%s-model" % role, dest="%s_model" % role, default=None, help="역할 %s 의 모델" % role)
        p.add_argument("--%s-provider" % role, dest="%s_provider" % role, default=None)
    p.add_argument("--debug", dest="debug_level", type=int, default=None, help="프로파일 상세도 0|1|2")
    p.add_argument("--preset", dest="preset", default=None, help="프리셋 적용 (presets.json; 쉼표로 여러 개, 뒤가 우선) 예: quality,token")
    p.add_argument("--trace", action="store_true", help="프로파일 trace 출력")
    p.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="llmwiki", description="LLM Wiki: FTS + Vector + GraphRAG (self-evolving)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("build", help="코퍼스 색인 (FTS/Vector/Graph/Wiki) · build status · build verify [--fix]")
    p.add_argument("action", nargs="?", choices=["run", "status", "verify"], default="run", help="run(기본) | status(진행/락/임베딩 진행률) | verify(정합성 검사)")
    p.add_argument("--fix", action="store_true", help="verify: 안전한 정리(댕글링·고아·n_chunks·stale 위키) 수행")
    p.add_argument("--full", action="store_true", help="증분 무시, 전체 리빌드")
    p.add_argument("--reset", dest="reset", action="store_true", default=None, help="색인 DB 파일 삭제 후 완전 초기화 빌드 (--full 의 기본 동작; 질의 로그/제안/동의어/위키 편집노트 보존)")
    p.add_argument("--no-reset", dest="reset", action="store_false", default=None, help="--full 시 DB 파일을 지우지 않고 테이블만 재생성")
    p.add_argument("--purge-logs", action="store_true", help="완전 초기화 시 질의 로그/제안/동의어도 삭제")
    p.add_argument("--force", action="store_true", help="health 검사 실패여도 빌드 강행")
    p.add_argument("--yes", action="store_true", help="파괴적 작업(--full/--reset/--purge-logs)의 확인 문구 입력 생략 (스케줄러 등 비대화형)")
    p.add_argument("--no-snapshot", action="store_true", help="초기화 전 자동 스냅샷 생략")
    _add_toggle_flags(p)

    p = sub.add_parser("users", help="Web 로그인 사용자 관리 (security.json): add | list | remove | set-role | passwd")
    p.add_argument("action", choices=["add", "list", "remove", "set-role", "passwd"])
    p.add_argument("name", nargs="?", help="사용자 id")
    p.add_argument("--role", default=None, help="viewer | operator | admin")
    p.add_argument("--password", default=None, help="비밀번호 (생략하면 프롬프트; 환경변수 LLMWIKI_PASSWORD 도 인식)")
    p.add_argument("--display", default="", help="표시 이름")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("security", help="로그인/역할/파괴적 작업 정책 보기·초기화 (security.json)")
    p.add_argument("action", choices=["show", "init", "audit"], nargs="?", default="show")
    p.add_argument("--n", type=int, default=50, help="audit: 최근 N 건")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("snapshot", help="색인 스냅샷: list | create [--tag t] | restore <name> | prune [--keep N]")
    p.add_argument("action", choices=["list", "create", "restore", "prune"], nargs="?", default="list")
    p.add_argument("name", nargs="?")
    p.add_argument("--tag", default="manual")
    p.add_argument("--keep", type=int, default=3)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("health", help="환경·프로바이더·DB·디스크·코퍼스 점검 (빌드 전 자동 실행되는 것과 동일)")
    p.add_argument("--quick", action="store_true", help="네트워크 ping(LLM/임베더/rerank/MCP) 생략")
    p.add_argument("--for-build", action="store_true", help="빌드에 필요한 역할만 ping")
    _add_toggle_flags(p)

    p = sub.add_parser("preset", help="설정 프리셋 (presets.json): 품질/속도/토큰 최적화 등 묶음 적용")
    p.add_argument("action", choices=["list", "show", "apply", "diff"], nargs="?", default="list")
    p.add_argument("names", nargs="*", help="프리셋 이름 (apply 는 여러 개, 뒤가 우선)")
    p.add_argument("--save", action="store_true", help="apply 결과를 config.json / tuning.json 에 저장")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("logs", help="logs/ 조회: tail | grep --request <id> | --run <run_id> | --text | files")
    p.add_argument("action", choices=["tail", "grep", "files", "dir"], nargs="?", default="tail")
    p.add_argument("-n", type=int, default=50)
    p.add_argument("--file", default="llmwiki", help="llmwiki | error | build | query")
    p.add_argument("--request", type=int, default=None, help="requests id → run_id 로 연결된 로그")
    p.add_argument("--run", default=None, help="run_id")
    p.add_argument("--text", default=None, help="포함 문자열")
    p.add_argument("--level", default=None, help="최소 레벨 DEBUG|INFO|WARNING|ERROR")
    p.add_argument("--since", default=None, help="예 30m, 2h, 1d")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("prompts", help="LLM 프롬프트/가이드 파일 (prompts/*.md) 목록·보기·초기화")
    p.add_argument("action", choices=["list", "show", "reset", "path"], nargs="?", default="list")
    p.add_argument("name", nargs="?")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("corpus", help="문서 계약: lint(스키마 검사) · types · schema <type> · example <type> · lint-file <path>")
    p.add_argument("action", choices=["lint", "types", "schema", "example", "lint-file", "stats"], nargs="?", default="lint")
    p.add_argument("arg", nargs="?", help="doc_type 또는 파일 경로")
    p.add_argument("--all", action="store_true", help="lint: 문제 없는 문서도 표시")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("embed", help="임베딩 진행률/coverage 리포트: status | report | clear-cache | runs")
    p.add_argument("action", choices=["status", "report", "clear-cache", "runs"], nargs="?", default="report")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("mcp-source", help="외부 MCP 소스(mcp_sources.json): list | test [name] | ingest [name] [--since] [--dry-run] | enrich \"질의\" | fetch <name> <tool> [json]")
    p.add_argument("action", choices=["list", "test", "ingest", "enrich", "fetch"], nargs="?", default="list")
    p.add_argument("args", nargs="*")
    p.add_argument("--since", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("rules", help="규칙 기반 질의 확장 사전(query_rules.json): show | add <type> <term> <values…> | remove <type> <term> [value] | test \"질의\"")
    p.add_argument("action", choices=["show", "add", "remove", "test", "stats", "path"], nargs="?", default="show")
    p.add_argument("args", nargs="*")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("pin", help="고정 근거(pins.json): list | add --doc <id부분> | --chunk <chunk_id> [--query \"…\" | --keywords a,b | --always | --doc-types issue,cl] | remove <pin_id>")
    p.add_argument("action", choices=["list", "add", "remove", "test"], nargs="?", default="list")
    p.add_argument("args", nargs="*")
    p.add_argument("--doc", default=None)
    p.add_argument("--chunk", default=None)
    p.add_argument("--query", default=None)
    p.add_argument("--keywords", default=None)
    p.add_argument("--always", action="store_true")
    p.add_argument("--doc-types", default=None)
    p.add_argument("--weight", type=float, default=1.0)
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("precompute", help="답변 사전 계산 캐시: run [--from-log N] | status | clear [--stale] | doc-vectors(문서 카드 임베딩 재생성)")
    p.add_argument("action", choices=["run", "status", "clear", "doc-vectors"], nargs="?", default="status")
    p.add_argument("--from-log", type=int, default=20)
    p.add_argument("--stale", action="store_true")
    _add_toggle_flags(p)   # --json 포함

    p = sub.add_parser("forensic", help="포렌식: <request_id> | last | list | summary | run <request_id> [--llm]")
    p.add_argument("target", nargs="?", default="last")
    p.add_argument("args", nargs="*", help="run <request_id>")
    p.add_argument("--llm", action="store_true", help="LLM(forensic 역할) 추가 소견")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("fusion", help="융합 방식 비교: compare [--methods rrf,zscore,…] [--k 5] [--questions 파일]")
    p.add_argument("action", choices=["compare", "show"], nargs="?", default="show")
    p.add_argument("--methods", default=None)
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--questions", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("memory", help="자가진화 메모리: status | decay | consolidate | episodes")
    p.add_argument("action", choices=["status", "decay", "consolidate", "episodes"], nargs="?", default="status")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("time", help="한국어 시간 표현 파싱 테스트: time \"지난주 리뷰한 CL\"")
    p.add_argument("text", nargs="+")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("trial", help="회귀 trial: run --name A [--preset q] [--set k=v …] | list | compare A B [C D] | report A | show A")
    p.add_argument("action", choices=["run", "list", "compare", "report", "show"], nargs="?", default="list")
    p.add_argument("refs", nargs="*", help="trial id 또는 이름")
    p.add_argument("--name", default=None)
    p.add_argument("--set", dest="sets", action="append", default=[], help="k=v (settings/toggles/tuning), 여러 번")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--questions", default=None)
    p.add_argument("--note", default="")
    p.add_argument("--md", action="store_true", help="compare 결과를 markdown 으로")
    _add_toggle_flags(p)

    p = sub.add_parser("query", help="질의")
    p.add_argument("question", nargs="+")
    p.add_argument("--k", type=int, default=None)
    p.add_argument("--no-log", action="store_true")
    _add_toggle_flags(p)

    p = sub.add_parser("eval", help="회귀 평가 (eval/questions.json)")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--matrix", action="store_true", help="fts/vector/graph 조합별 비교")
    p.add_argument("--questions", default=None, help="질문셋 JSON 경로 (기본 eval/questions.json)")
    _add_toggle_flags(p)

    p = sub.add_parser("graph", help="그래프 요약/내보내기")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--community", type=int, default=None)
    p.add_argument("--provenance", default=None, help="관계 출처 필터: explicit,rule,human,llm,cooccur")
    p.add_argument("--types", default=None, help="노드 유형 필터 (쉼표)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("entity", help="엔티티 상세")
    p.add_argument("name", nargs="+")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("search", help="단일 검색 채널 디버그 (fts|vector|graph)")
    p.add_argument("channel", choices=["fts", "vector", "graph"])
    p.add_argument("question", nargs="+")
    p.add_argument("--k", type=int, default=8)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("evolve", help="자가진화 제안 관리")
    p.add_argument("action", choices=["status", "apply", "reject", "review", "feedback", "list"])
    p.add_argument("args", nargs="*")
    p.add_argument("--no-eval", action="store_true", help="apply 시 회귀평가 생략")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("wiki", help="위키 페이지 재생성")
    p.add_argument("--min-degree", type=int, default=1)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("docs", help="색인된 문서 목록")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("stats", help="인덱스 통계/프로바이더 상태")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("config", help="설정 보기/변경")
    p.add_argument("action", choices=["show", "set", "reset", "paths"])
    p.add_argument("kv", nargs="*", help="key=value")
    p.add_argument("--effective", action="store_true", help="show: 키별 현재값·기본값·출처(default/file/env)·env 이름")
    p.add_argument("--yes", action="store_true", help="reset: 확인 문구 생략")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("models", help="역할별 LLM/임베딩 모델 설정 보기·테스트·변경")
    p.add_argument("action", choices=["show", "test", "set"], nargs="?", default="show")
    p.add_argument("kv", nargs="*", help="set: answer_model=claude-opus-5 rerank_provider=ollama rerank_model=llama3.1 embed_provider=hash ...")
    p.add_argument("--live", action="store_true", help="test: ping 외에 실제 완성 호출 1회 (PAT 권한·헤더·모델명·headless 실행 확인, 토큰 소량 소비)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("requests", help="요청별 프로파일/디버그 trace 조회")
    p.add_argument("action", choices=["list", "show", "last"], nargs="?", default="list")
    p.add_argument("id", nargs="?", type=int)
    p.add_argument("--kind", default=None, help="query|build|eval|search")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("system", help="확장성/캐시/워처/최근 빌드·질의 지연 통계")
    p.add_argument("--target-docs", type=int, default=3000)
    p.add_argument("--daily-new", type=int, default=20)
    p.add_argument("--horizon-days", type=int, default=365)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("maintenance", help="DB 유지보수")
    p.add_argument("action", choices=["vacuum", "fts_optimize", "wal_checkpoint", "clear_cache", "warm_cache", "refresh_doc_refs", "purge_requests"])
    p.add_argument("--yes", action="store_true", help="purge_requests: 확인 문구 생략")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("watch", help="코퍼스 변경 감시 → 변경 시 증분 빌드")
    p.add_argument("--interval", type=int, default=None, help="스캔 주기(초), 기본 config.auto_build_interval")
    p.add_argument("--once", action="store_true", help="한 번만 스캔/빌드")
    _add_toggle_flags(p)

    p = sub.add_parser("tuning", help="단계별 튜닝 파라미터 보기/변경/초기화/문서 생성")
    p.add_argument("action", choices=["show", "set", "reset", "doc"], nargs="?", default="show")
    p.add_argument("kv", nargs="*", help="set: fts_mode=tiered rerank_heading_bonus=0.1 … | reset: [키]")
    p.add_argument("--stage", default=None, help="show 시 단계 필터")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("arch", help="구조/흐름과 토글·CLI·튜닝 영향 (Web Architecture 탭과 동일 정의)")
    p.add_argument("--flow", default=None, help="query|build|evolve|watch")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("mcp", help="MCP 서버 (stdio JSON-RPC) — Claude Desktop/Code 등에서 도구로 사용")

    p = sub.add_parser("serve", help="Web UI 서버")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1", help="0.0.0.0 등 외부 공개 시 security.json 의 로그인 설정이 필요 (없으면 거부)")
    p.add_argument("--insecure", action="store_true", help="로그인 설정 없이 외부에 공개 (권장하지 않음)")
    return ap


def _overrides_from_ns(ns: argparse.Namespace) -> Dict[str, Any]:
    ov: Dict[str, Any] = {}
    keys = list(Toggles.__dataclass_fields__) + ["llm_provider", "embed_provider", "llm_model", "debug_level"]
    keys += ["%s_%s" % (r, a) for r in Settings.LLM_ROLES for a in ("model", "provider")]
    for name in keys:
        v = getattr(ns, name, None)
        if v is not None:
            ov[name] = v
    if getattr(ns, "k", None) and getattr(ns, "cmd", "") == "query":   # eval/trial 의 --k 는 평가 k
        ov["top_k_final"] = ns.k
    return ov


def _confirm_destructive(ns: argparse.Namespace, what: str, detail: str = "") -> bool:
    """파괴적 작업 확인: --yes 면 통과, 대화형이면 확인 문구(security.json destructive.confirm_phrase)를 입력받고,
    비대화형(파이프/스케줄러/Web 콘솔)인데 --yes 가 없으면 거부한다."""
    if getattr(ns, "yes", False):
        return True
    from .auth import load_security
    phrase = (load_security().get("destructive") or {}).get("confirm_phrase") or "DELETE INDEX"
    print("!! 파괴적 작업: %s" % what)
    if detail:
        print("   %s" % detail)
    if _CAPTURED or not sys.stdin or not sys.stdin.isatty():
        print("   비대화형 실행입니다. 정말 실행하려면 --yes 를 붙이세요 (Web 콘솔은 관리자 확인 후 자동으로 붙습니다).")
        return False
    try:
        typed = input("   계속하려면 확인 문구 '%s' 를 입력: " % phrase).strip()
    except (EOFError, KeyboardInterrupt):
        typed = ""
    if typed != phrase:
        print("   취소됨 (문구 불일치)")
        return False
    return True


def _out(obj: Any, as_json: bool, text: Optional[str] = None) -> None:
    if as_json or text is None:
        print(json.dumps(jsonable(obj), ensure_ascii=False, indent=2))
    else:
        print(text)


def _print_trace(trace: Dict[str, Any], depth: int = 0, total: Optional[float] = None, verbose: bool = False) -> None:
    total = total or trace.get("ms") or 1.0
    flag = "" if trace.get("enabled", True) else " (skipped: %s)" % trace.get("meta", {}).get("reason", "")
    err = (" !! " + trace["error"]) if trace.get("error") else ""
    cnt = trace.get("counters") or {}
    extra = []
    if cnt.get("llm_calls"):
        extra.append("llm=%d tok=%d/%d" % (cnt.get("llm_calls", 0), cnt.get("llm_input_tokens", 0), cnt.get("llm_output_tokens", 0)))
    if cnt.get("sql"):
        extra.append("sql=%d" % cnt["sql"])
    pct = " %4.0f%%" % (100.0 * trace["ms"] / total) if depth == 1 and trace.get("enabled", True) else "      "
    print("%s%-22s %8.1f ms%s %s%s%s" % ("  " * depth, trace["name"], trace["ms"], pct, " ".join(extra), flag, err))
    meta = {k: v for k, v in (trace.get("meta") or {}).items() if k != "reason"}
    lim = 2000 if verbose else 220
    if meta and depth > 0:
        s = json.dumps(meta, ensure_ascii=False)
        print("%s  ↳ %s" % ("  " * depth, s[:lim] + ("…" if len(s) > lim else "")))
    if verbose and trace.get("debug"):
        s = json.dumps(trace["debug"], ensure_ascii=False)
        print("%s  ⚙ %s" % ("  " * depth, s[:lim] + ("…" if len(s) > lim else "")))
    if verbose and trace.get("samples"):
        for k, v in trace["samples"].items():
            print("%s  ▶ %s: %s" % ("  " * depth, k, str(v)[:600].replace("\n", " ⏎ ")))
    if verbose and trace.get("logs"):
        for l in trace["logs"]:
            print("%s  · %s" % ("  " * depth, l))
    for c in trace.get("children", []):
        _print_trace(c, depth + 1, total, verbose)
    if depth == 0 and trace.get("summary"):
        sm = trace["summary"]
        print("summary: total=%.1f ms, llm calls=%s tokens=%s (in %s / out %s), sql=%s, slowest=%s" % (
            sm["total_ms"], sm["llm"]["calls"], sm["llm"]["total_tokens"], sm["llm"]["input_tokens"], sm["llm"]["output_tokens"],
            sm["sql_statements"], ", ".join("%s %.0f%%" % (x["name"], x["pct"]) for x in sm["slowest"][:3])))


def run(argv: Optional[List[str]] = None, settings: Optional[Settings] = None, pipe=None) -> int:
    ap = build_parser()
    ns = ap.parse_args(argv)
    if not ns.cmd:
        ap.print_help()
        return 0
    s = settings or load_settings()
    s = apply_overrides(s.copy() if settings else s, _overrides_from_ns(ns))
    from .pipeline import Pipeline
    p = pipe or Pipeline(s)
    if pipe is not None:
        p.s = s
        p.reload()
    as_json = getattr(ns, "json", False)
    preset_prev = None
    if getattr(ns, "preset", None):
        from . import presets as _presets
        pa = _presets.apply(s, _presets.parse_names(ns.preset), save=False)
        preset_prev = pa["prev"]
        if pa["unknown"] and not as_json:
            print("WARNING: unknown preset(s): %s" % pa["unknown"])
        p.reload_tuning(from_file=False)   # 프리셋 튜닝값은 메모리에만 있음 — 파일 재로드 금지
    try:
        return _run_cmd(ns, s, p, as_json)
    finally:
        if preset_prev is not None:
            from . import presets as _presets
            _presets.restore(s, preset_prev)
            p.reload_tuning(from_file=False)


def _parse_since(v: Optional[str]) -> Optional[float]:
    if not v:
        return None
    unit = v[-1].lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit)
    return float(v[:-1]) * mult if mult else float(v)


def _run_cmd(ns: argparse.Namespace, s: Settings, p, as_json: bool) -> int:
    if ns.cmd == "build" and ns.action == "status":
        stt = p.build_status()
        if as_json:
            _out(stt, True)
            return 0
        print("running=%s%s" % (stt["running"], (" (pid %s since %s)" % (stt["lock"].get("pid"), __import__("time").strftime("%H:%M:%S", __import__("time").localtime(stt["lock"].get("ts", 0))))) if stt["lock"] else ""))
        lb = stt.get("last_build") or {}
        print("last_build: %s mode=%s docs=%s changed=%s  build_version=%s" % (
            __import__("time").strftime("%Y-%m-%d %H:%M:%S", __import__("time").localtime(lb.get("ts", 0))) if lb else "-", lb.get("mode"), lb.get("docs"), lb.get("changed"), stt["build_version"]))
        ep = stt.get("embed_progress") or {}
        if ep:
            print("embed: %s %s/%s failed=%s cache=%s batch=%s rate=%s/s eta=%ss" % (ep.get("status"), ep.get("done"), ep.get("total"), ep.get("failed"), ep.get("cache_hits"),
                                                                                     ep.get("batch"), ep.get("rate_per_s"), ep.get("eta_s")))
        print("lint:", json.dumps(stt.get("lint_summary"), ensure_ascii=False))
        print("index:", json.dumps({k: v for k, v in stt["stats"].items() if k in ("docs", "chunks", "embeddings", "entities", "relations")}))
        return 0

    if ns.cmd == "build" and ns.action == "verify":
        vr = p.store.verify(p.embedder.name if p.s.toggles.embed else None, fix=ns.fix, wiki_dir=p.s.wiki_dir)
        if as_json:
            _out(vr, True)
            return 0 if vr["ok"] else 1
        print("verify: %s (problems=%d)%s" % ("OK" if vr["ok"] else "PROBLEMS", vr["problems"], " fixed=%s" % json.dumps(vr["fixed"]) if ns.fix else ""))
        for c in vr["checks"]:
            print("  %s %-24s %-6s %s" % ("✔" if c["ok"] else "✘", c["name"], c["count"], c["detail"] + ("  [--fix 가능]" if (not c["ok"] and c["fixable"]) else "")))
        print("counts:", json.dumps(vr["counts"]))
        return 0 if vr["ok"] else 1

    if ns.cmd == "build":
        from .buildlock import BuildLockedError
        do_reset = ns.reset if ns.reset is not None else bool(ns.full)   # --full 의 기본값 = 완전 초기화
        if do_reset or ns.purge_logs:
            stt = p.store.stats()
            if not _confirm_destructive(ns, "색인 완전 초기화%s" % (" + 질의 로그/제안/이력 삭제(--purge-logs)" if ns.purge_logs else ""),
                                        "현재 docs=%s chunks=%s entities=%s requests=%s → 모두 지우고 다시 만듭니다%s. (--full --no-reset 은 테이블을 비우지 않는 전체 리빌드)" % (
                                            stt.get("docs"), stt.get("chunks"), stt.get("entities"), stt.get("requests"),
                                            "" if ns.no_snapshot else "; 직전 상태는 data/snapshots/ 에 자동 저장(snapshot restore 로 복원)")):
                _out({"error": "cancelled"}, as_json, "build cancelled")
                return 4
        if do_reset and p.s.toggles.health_check and not ns.force:
            # 초기화(색인 삭제) 전에 health 를 먼저 확인 — 코퍼스 경로가 없으면 기존 색인을 지우지 않는다
            from .health import run_health, format_health
            hr = run_health(p, quick=True, for_build=True)
            if not hr["ok"]:
                _out({"error": "health check failed before reset", "health": hr}, as_json,
                     format_health(hr) + "\nbuild aborted before reset (기존 색인 유지). --force 로 강행, --no-health-check 로 생략")
                return 3
        if do_reset:
            from .auth import load_security
            dsec = load_security().get("destructive") or {}
            r = p.reset_index(keep_logs=not ns.purge_logs, snapshot=not ns.no_snapshot and bool(dsec.get("snapshot_before", True)),
                              actor="cli:%s" % (os.environ.get("USERNAME") or os.environ.get("USER") or "?"), snapshot_keep=int(dsec.get("snapshot_keep", 3) or 3))
            if not as_json:
                print("  · reset: cleared %d tables, kept_logs=%s, removed_wiki_pages=%d%s" % (
                    len(r["cleared_tables"]), r["kept_logs"], r["removed_wiki_pages"], (" · snapshot %s" % r["snapshot"]) if r.get("snapshot") else ""))
            ns.full = True
        try:
            # 진행 표시: 단계/진도율/LLM 대기 시간을 stderr 에 1초 간격으로 출력 (--json 이나 Web 콘솔에서는 조용히 bind 만)
            with _pg.cli_monitor("cli-build-%d" % int(time.time()), "build", "build --full" if ns.full else "build", enabled=not as_json and not _CAPTURED):
                res, tr = p.build(full=ns.full, progress=lambda m: print("  ·", m) if not as_json else None, force=ns.force)
        except BuildLockedError as e:
            _out({"error": str(e), "holder": e.holder}, as_json, "build refused: %s" % e)
            return 2
        except RuntimeError as e:
            if "health check failed" in str(e):
                _out({"error": str(e)}, as_json, "build aborted: %s" % e)
                return 3
            raise
        if ns.trace:
            _print_trace(tr, verbose=(ns.debug_level or 0) >= 2)
        _out({"result": res, "trace": tr if ns.trace else None}, as_json,
             "build done (%s): %s%s" % (res["mode"], json.dumps(res.get("stats"), ensure_ascii=False),
                                       ("\nALERTS: " + json.dumps(res["alerts"], ensure_ascii=False)) if res.get("alerts") else ""))
        return 0

    if ns.cmd == "health":
        from .health import run_health, format_health
        r = run_health(p, quick=ns.quick, for_build=ns.for_build)
        _out(r, as_json, format_health(r))
        return 0 if r["ok"] else 1

    if ns.cmd == "preset":
        from . import presets as _presets
        pr = _presets.load_presets()
        if ns.action == "list":
            _out(pr, as_json, "\n".join("%-14s %s" % (k, v.get("desc", "")) for k, v in pr.items()) + "\n\n파일: %s" % _presets.presets_path())
            return 0
        if ns.action == "show":
            name = ns.names[0] if ns.names else ""
            _out(pr.get(name, {"error": "unknown preset %s" % name}), True)
            return 0
        if ns.action == "diff":
            name = ns.names[0] if ns.names else ""
            rows = _presets.diff(s, name)
            _out(rows, as_json, "\n".join("%s %-32s %s → %s" % ("*" if r["changes"] else " ", r["key"], r["current"], r["preset"]) for r in rows))
            return 0
        if ns.action == "apply":
            r = _presets.apply(s, ns.names, save=ns.save)
            p.reload_tuning(from_file=False)
            if ns.save:
                p.reload()
            _out({k: v for k, v in r.items() if k != "prev"}, as_json,
                 "applied %s: toggles=%d tuning=%d settings=%d conflicts=%d%s%s" % (ns.names, len(r["toggles"]), len(r["tuning"]), len(r["settings"]),
                                                                                  len(r["conflicts"]), " (saved)" if ns.save else " (이번 프로세스만; --save 로 저장)",
                                                                                  ("\nunknown: %s" % r["unknown"]) if r["unknown"] else ""))
            return 0

    if ns.cmd == "logs":
        from . import logging_setup as _ls
        from .config import path_for
        d = _ls.log_dir() or path_for("logs_dir")
        if ns.action == "dir":
            print(d)
            return 0
        if ns.action == "files":
            _out(_ls.files(d), as_json, "\n".join("%-14s %10d B  %s" % (f["file"], f["bytes"], __import__("time").strftime("%m-%d %H:%M:%S", __import__("time").localtime(f["mtime"]))) for f in _ls.files(d)))
            return 0
        path = __import__("os").path.join(d, ns.file + ".log")
        run_id = ns.run
        if ns.request is not None:
            r = p.store.get_request(int(ns.request))
            run_id = (r or {}).get("run_id") or ""
            if not run_id:
                print("request %s has no run_id (구버전 기록)" % ns.request)
                return 1
        if ns.action == "tail" and not (run_id or ns.text or ns.level or ns.since):
            rows = _ls.tail(path, ns.n)
        else:
            rows = _ls.grep(path, run_id=run_id, text=ns.text, level=ns.level, since_s=_parse_since(ns.since), limit=ns.n if ns.action == "tail" else 2000)
        if as_json:
            _out(rows, True)
            return 0
        for r in rows:
            data = r.get("data")
            print("%s %-7s %-6s %-12s %s%s" % (r.get("t", ""), r.get("level", ""), r.get("kind", ""), r.get("run_id", ""), r.get("msg", ""),
                                             (" " + json.dumps(data, ensure_ascii=False)[:300]) if data else ""))
        return 0

    if ns.cmd == "corpus":
        from . import schema as _schema
        if ns.action == "types":
            rows = [{"doc_type": k, "title": v.get("title"), "id_pattern": v.get("id_pattern"), "required": [f for f, sp in v.get("fields", {}).items() if sp.get("required")]}
                    for k, v in _schema.load_schemas().items() if k != "common"]
            _out(rows, as_json, "\n".join("%-14s %-40s id=%s\n%15s required: %s" % (r["doc_type"], r["title"], r["id_pattern"], "", ", ".join(r["required"])) for r in rows)
                 + "\n\nschemas: %s" % _schema.schemas_dir())
            return 0
        if ns.action == "schema":
            _out(_schema.load_schemas().get(ns.arg or "", {"error": "unknown doc_type"}), True)
            return 0
        if ns.action == "example":
            print(_schema.example_document(ns.arg or "issue") or "unknown doc_type %s (corpus types)" % ns.arg)
            return 0
        if ns.action == "lint-file":
            import os as _os
            from .corpus import load_document
            path = _os.path.abspath(ns.arg or "")
            d = load_document(path, _os.path.dirname(path))
            if not d:
                print("cannot read", path)
                return 1
            fm = (d.meta or {}).get("fm") or {}
            nm = _schema.normalize_meta(fm, d.doc_id, d.title, d.text, d.mtime)
            lint = _schema.lint_document(fm, nm, d.text, bool(fm))
            _out({"meta": nm, "lint": lint}, as_json, "doc_type=%s id=%s date=%s(%s) inferred=%s\n%s" % (
                nm["doc_type"], nm["ext_id"], nm["date"], nm["date_source"], nm["inferred"], "\n".join("  [%s] %s: %s" % (x["level"], x["field"], x["msg"]) for x in lint) or "  (no issues)"))
            return 0 if not any(x["level"] == "error" for x in lint) else 1
        if ns.action == "stats":
            _out({"doc_types": p.store.doc_type_counts(), "lint": p.store.kv_get("lint_summary"), "provenance": p.store.provenance_counts()}, True)
            return 0
        rows = p.store.lint_rows(only_problems=not ns.all, limit=ns.limit)
        summ = p.store.kv_get("lint_summary") or {}
        if as_json:
            _out({"summary": summ, "rows": rows}, True)
            return 0
        print("lint summary: %s" % json.dumps(summ, ensure_ascii=False))
        for r in rows:
            print("%s %-50s type=%-13s id=%-14s err=%d warn=%d%s" % ("✘" if r["lint_errors"] else ("△" if r["lint_warnings"] else "✔"), r["doc_id"][:50], r["doc_type"] or "-", r["ext_id"] or "-",
                                                                 r["lint_errors"], r["lint_warnings"], " (inferred)" if r["inferred"] else ""))
            for x in r["lint"]:
                if x["level"] != "info":
                    print("      [%s] %s: %s" % (x["level"], x["field"], x["msg"]))
        if not rows:
            print("(no problems)" if not ns.all else "(no docs)")
        return 0

    if ns.cmd == "embed":
        from .embed_run import embed_report
        if ns.action == "clear-cache":
            _out({"removed": p.store.cache_clear()}, True)
            return 0
        if ns.action == "runs":
            _out(p.store.embed_runs(20), True)
            return 0
        rep = embed_report(p)
        if ns.action == "status":
            _out(rep.get("progress") or {"status": "idle"}, True)
            return 0
        if as_json:
            _out(rep, True)
            return 0
        cov = rep["coverage"]
        print("embedder: %s/%s dim=%s dtype=%s" % (rep["embedder"]["provider"], rep["embedder"]["model"], rep["embedder"]["dim"], rep["embedder"]["store_dtype"]))
        print("coverage: %d/%d = %.1f%%" % (cov["embedded"], cov["chunks"], 100 * cov["coverage"]))
        for r in cov["by_doc_type"]:
            print("  %-14s %5d/%-5d %.1f%%" % (r["doc_type"], r["embedded"], r["chunks"], 100 * r["coverage"]))
        if cov["missing_sample"]:
            print("missing sample:", cov["missing_sample"][:10])
        print("cache:", json.dumps(rep["cache"], ensure_ascii=False))
        pr = rep.get("progress") or {}
        if pr:
            print("progress: %s %s/%s failed=%s batch=%s" % (pr.get("status"), pr.get("done"), pr.get("total"), pr.get("failed"), pr.get("batch")))
        for r in rep["runs"][:5]:
            print("run %s %s total=%s done=%s failed=%s cache=%s batches=%s avg=%sms final_batch=%s alerts=%d" % (
                r["run_id"], r["status"], r["total"], r["done"], r["failed"], r["cache_hits"], r["batches"], r["avg_batch_ms"], r["final_batch"], len(r["alerts"])))
        return 0

    if ns.cmd == "mcp-source":
        from . import mcp_client as _mcp
        if ns.action == "list":
            srcs = _mcp.load_sources()
            _out(srcs, as_json, "\n".join("%-8s enabled=%-5s %s\n%9s command=%s ingest=%d enrich=%d" % (k, v.get("enabled"), v.get("desc", ""), "", v.get("command"), len(v.get("ingest") or []), len(v.get("enrich") or [])) for k, v in srcs.items())
                 + "\n\n토글 mcp_sources=%s  파일: %s" % (p.s.toggles.mcp_sources, _mcp.sources_path()))
            return 0
        if ns.action == "test":
            _out(_mcp.test_sources(p.s, ns.args or None), True)
            return 0
        if ns.action == "ingest":
            _out(_mcp.ingest(p.s, ns.args or None, since=ns.since, dry_run=ns.dry_run), True)
            return 0
        if ns.action == "enrich":
            _out(_mcp.enrich(p.s, " ".join(ns.args)), True)
            return 0
        if ns.action == "fetch":
            if len(ns.args) < 2:
                print("usage: mcp-source fetch <name> <tool> [json args]")
                return 1
            cfg = _mcp.load_sources().get(ns.args[0])
            if not cfg:
                print("unknown source", ns.args[0])
                return 1
            args = json.loads(ns.args[2]) if len(ns.args) > 2 else {}
            with _mcp.MCPClient(ns.args[0], cfg) as c:
                _out(c.call_tool(ns.args[1], args), True)
            return 0

    if ns.cmd == "rules":
        from . import query_rules as _qr
        if ns.action == "show":
            _out(_qr.load_rules(), True)
            return 0
        if ns.action == "stats":
            _out(_qr.stats(), True)
            return 0
        if ns.action == "path":
            print(_qr.rules_path())
            return 0
        if ns.action == "add":
            if len(ns.args) < 3:
                print("usage: rules add <acronym|synonym|alias|related|exclude|compound> <term> <value…>")
                return 1
            _out(_qr.add_rule(ns.args[0], ns.args[1], ns.args[2:]), True)
            p.reload_tuning()
            return 0
        if ns.action == "remove":
            ok = _qr.remove_rule(ns.args[0], ns.args[1], ns.args[2] if len(ns.args) > 2 else None)
            print("removed" if ok else "not found")
            return 0 if ok else 1
        if ns.action == "test":
            from . import tuning as _tn
            r = _qr.expand(" ".join(ns.args), _tn.T.get("syn_w"), _tn.T.get("related_w"), _tn.T.get("acronym_phrase"))
            _out(r, as_json, json.dumps(r, ensure_ascii=False, indent=1))
            return 0

    if ns.cmd == "pin":
        from . import pins as _pins
        if ns.action == "list":
            rows = _pins.load_pins()
            _out(rows, as_json, "\n".join("%-5s doc=%s chunk=%s when=%s w=%.2f strength=%.2f hits=%s %s" % (r["id"], r.get("doc"), r.get("chunk"), json.dumps(r.get("when"), ensure_ascii=False),
                                                                                                              r.get("weight", 1), r.get("strength", 1), r.get("hits", 0), r.get("note", "")) for r in rows) or "(no pins)")
            return 0
        if ns.action == "add":
            if not ns.doc and not ns.chunk:
                print("--doc 또는 --chunk 필요")
                return 1
            pin = _pins.add_pin(doc=ns.doc, chunk=ns.chunk, query=ns.query, keywords_=[x.strip() for x in ns.keywords.split(",")] if ns.keywords else None,
                                always=ns.always, doc_types=[x.strip() for x in ns.doc_types.split(",")] if ns.doc_types else None, weight=ns.weight, note=ns.note)
            _out(pin, True)
            return 0
        if ns.action == "remove":
            ok = _pins.remove_pin(ns.args[0] if ns.args else "")
            print("removed" if ok else "not found")
            return 0 if ok else 1
        if ns.action == "test":
            _out(_pins.match_pins(p.store, " ".join(ns.args)), True)
            return 0

    if ns.cmd == "precompute":
        from . import precompute as _pc
        if ns.action == "run":
            r = _pc.run(p, from_log=ns.from_log, progress=lambda m: print("  ·", m) if not as_json else None)
            _out(r, as_json, "precompute: questions=%s computed=%s skipped=%s ms=%s cache=%s" % (r["questions"], r["computed"], r["skipped_cached"], r["ms"], json.dumps(r["cache"])))
            return 0
        if ns.action == "clear":
            _out({"removed": _pc.clear_cache(p.store, stale_only=ns.stale)}, True)
            return 0
        if ns.action == "doc-vectors":
            _out(_pc.build_doc_vectors(p), True)
            return 0
        _out(_pc.cache_status(p.store), True)
        return 0

    if ns.cmd == "forensic":
        from . import forensic as _fx
        tgt = ns.target
        if tgt == "summary":
            _out(_fx.summary(p.store), True)
            return 0
        if tgt == "list":
            rows = _fx.list_forensics(p.store, ns.limit)
            _out(rows, as_json, "\n".join("#%-4s req=%-5s %-12s g=%-5s %s" % (r["id"], r["request_id"], r["verdict"], r["groundedness"], r["query"][:60]) for r in rows) or "(none)")
            return 0
        if tgt == "last":
            rows = _fx.list_forensics(p.store, 1)
            if not rows:
                # 마지막 질의 요청에 대해 즉시 진단
                reqs = p.store.requests("query", 1)
                if not reqs:
                    print("no forensics / no query")
                    return 1
                tgt = str(reqs[0]["id"])
            else:
                _out(rows[0], as_json, _fx.format_forensic(rows[0]))
                return 0
        if tgt == "run":
            tgt = ns.args[0] if getattr(ns, "args", None) else ""
        try:
            rid = int(tgt)
        except ValueError:
            print("usage: forensic <request_id> | last | list | summary | run <request_id> [--llm]")
            return 1
        existing = _fx.get_forensic(p.store, rid)
        req = p.store.get_request(rid)
        if not req or not req.get("trace"):
            print("request %s not found" % rid)
            return 1
        diag = _fx.diagnose(req["trace"], req.get("result") or {"query": req["summary"]}, p.s)
        if ns.llm:
            llm = p.llm_for("forensic")
            extra = _fx.llm_forensic(llm, (req.get("result") or {}).get("query", req["summary"]), req["trace"], diag, p.s.role_llm("forensic")["effort"]) if llm.available else None
            if extra:
                diag["findings"] += [dict(f, source="llm") for f in extra["findings"] if isinstance(f, dict)]
                diag["suggestions"] += [dict(s_, source="llm") for s_ in extra["suggestions"] if isinstance(s_, dict)]
        fid = existing["id"] if existing else _fx.record(p.store, rid, req.get("run_id") or "", (req.get("result") or {}).get("query", req["summary"]),
                                                          ((req.get("result") or {}).get("evidence") or {}).get("verdict", "?"), (req.get("result") or {}).get("groundedness"), diag, "manual")
        row = dict(id=fid, request_id=rid, run_id=req.get("run_id"), query=(req.get("result") or {}).get("query", req["summary"]),
                   verdict=((req.get("result") or {}).get("evidence") or {}).get("verdict"), groundedness=(req.get("result") or {}).get("groundedness"), **diag)
        _out(row, as_json, _fx.format_forensic(row))
        return 0

    if ns.cmd == "fusion":
        from . import fusion as _fusion
        if ns.action == "show":
            _out({"method": p.tuning.get("fusion_method"), "choices": ["rrf", "weighted", "minmax", "zscore", "dbsf", "rrf_boost"],
                  "boosts": {k: p.tuning.get(k) for k in ("doc_type_boost", "pin_boost", "provenance_boost", "feedback_boost_w", "time_boost_w", "recency_half_life_days", "exclude_penalty")}}, True)
            return 0
        from .evalset import load_questions
        qs = load_questions(ns.questions) if ns.questions else load_questions()
        rows = _fusion.compare_methods(p, qs, [x.strip() for x in ns.methods.split(",")] if ns.methods else None, ns.k)
        _out(rows, as_json, "\n".join("%-10s hit@%d=%.3f mrr=%.3f term_recall=%.3f avg_ms=%s" % (r["method"], ns.k, r["hit@k"], r["mrr"], r["term_recall"], r.get("avg_ms")) for r in rows))
        return 0

    if ns.cmd == "memory":
        from . import memory as _mem
        hl = p.tuning.get("memory_half_life_days")
        if ns.action == "decay":
            _out(_mem.decay(p.store, hl, p.tuning.get("memory_archive_strength")), True)
            return 0
        if ns.action == "consolidate":
            _out(_mem.consolidate(p.store, p.tuning.get("forensic_min_events")), True)
            return 0
        if ns.action == "episodes":
            _out(_mem.episodes(p.store, ns.limit), True)
            return 0
        _out(_mem.status(p.store, hl), True)
        return 0

    if ns.cmd == "trial":
        from . import trials as _tr
        if ns.action == "run":
            from .evalset import load_questions
            qs = load_questions(ns.questions) if ns.questions else None
            ov: Dict[str, Any] = {}
            for kv in ns.sets:
                k_, _, v_ = kv.partition("=")
                ov[k_.strip()] = v_.strip()
            name = ns.name or ("trial-%s" % __import__("time").strftime("%m%d-%H%M%S"))
            r = _tr.run_trial(p, name, qs, k=ns.k, preset=ns.preset, overrides=ov or None, note=ns.note)
            _out(r, as_json, "trial #%s %s: %s" % (r["trial_id"], r["name"], json.dumps(r["summary"], ensure_ascii=False)))
            return 0
        if ns.action == "list":
            rows = _tr.list_trials(p.store, 50)
            _out(rows, as_json, "\n".join("#%-4s %-24s v%-3s n=%-3s hit=%-5s mrr=%-5s g=%-5s insuf=%-5s ms=%-7s %s" % (
                r["trial_id"], r["name"][:24], r["build_version"], r["summary"].get("n"), r["summary"].get("hit@k"), r["summary"].get("mrr"), r["summary"].get("groundedness"),
                r["summary"].get("insufficient_rate"), r["summary"].get("avg_ms"), __import__("time").strftime("%m-%d %H:%M", __import__("time").localtime(r["ts"]))) for r in rows) or "(no trials)")
            return 0
        if ns.action in ("show", "report"):
            tr_ = _tr.get_trial(p.store, ns.refs[0] if ns.refs else "")
            if not tr_:
                print("trial not found")
                return 1
            if ns.action == "show" or as_json:
                _out(tr_, True)
                return 0
            print("# trial %s (#%s)\n\n%s\n\n질문별:" % (tr_["name"], tr_["trial_id"], json.dumps(tr_["summary"], ensure_ascii=False)))
            for row in tr_["rows"]:
                print("%s rank=%s term=%.2f g=%s mode=%s  %s" % ("✔" if row["hit"] else "✘", row["rank"], row["term_recall"], row.get("groundedness"), row.get("answer_mode"), row["q"]))
            return 0
        if ns.action == "compare":
            cmp_ = _tr.compare(p.store, ns.refs)
            if as_json:
                _out(cmp_, True)
                return 0
            print(_tr.report_md(cmp_))
            return 0 if not cmp_.get("error") else 1

    if ns.cmd == "time":
        from . import timeparse as _tp
        r = _tp.parse(" ".join(ns.text), p.s.timezone, p.s.week_start)
        _out(r or {"expr": None}, True)
        return 0

    if ns.cmd == "prompts":
        from . import prompts as _prompts
        if ns.action == "list":
            rows = _prompts.list_prompts()
            _out(rows, as_json, "\n".join("%-16s %6d chars %s %s" % (r["name"], r["chars"], "(default)" if r["is_default"] else "(edited) ", r["path"]) for r in rows))
            return 0
        if not ns.name:
            print("name 필요")
            return 1
        if ns.action == "show":
            print(_prompts.get(ns.name))
        elif ns.action == "reset":
            print("reset:", _prompts.reset(ns.name))
        elif ns.action == "path":
            print(_prompts.path(ns.name))
        return 0

    if ns.cmd == "query":
        q = " ".join(ns.question)
        with _pg.cli_monitor("cli-query-%d" % int(time.time()), "query", q[:80], enabled=not as_json and not _CAPTURED):
            res, tr = p.query(q, log=not ns.no_log)
        if as_json:
            _out({"result": res, "trace": tr}, True)
            return 0
        print("Q:", q)
        print("route:", json.dumps(res.get("route", {}).get("kind")), "weights:", res["config"]["weights"])
        print("-" * 70)
        print(res["answer"])
        print("-" * 70)
        for h in res["hits"]:
            tag = ("[C%d]" % h["n"]) if h.get("n") is not None else "[--]"   # n=None: 컨텍스트에 미포함(dedupe/trim)
            print("%s %s | %s | fused=%.4f rerank=%s via %s" % (tag, h["chunk_id"], (h.get("heading") or "")[:50], h.get("fused") or 0.0, h.get("rerank"), ",".join(h.get("why") or [])))
        if res.get("graph", {}).get("seeds"):
            print("graph seeds:", res["graph"]["seeds"])
        if res.get("proposals"):
            print("evolve proposals created:", res["proposals"])
        print("total %.1f ms | llm=%s embed=%s | tokens=%s | query_id=%s request_id=%s%s" % (
            res["ms"], res["config"]["llm"], res["config"]["embedder"], res.get("tokens", {}).get("total_tokens", 0),
            res.get("query_id"), res.get("request_id"), " [cached]" if res.get("cached") else ""))
        if ns.trace:
            print("=" * 70)
            _print_trace(tr, verbose=(ns.debug_level or 0) >= 2)
        return 0

    if ns.cmd == "eval":
        from .evalset import load_questions
        qset = load_questions(ns.questions) if ns.questions else None
        if ns.matrix:
            combos = [("fts", True, False, False), ("vector", False, True, False), ("graph", False, False, True),
                      ("fts+vector", True, True, False), ("fts+graph", True, False, True), ("all", True, True, True)]
            rows = []
            for name, f, v, g in combos:
                p.s.toggles.fts, p.s.toggles.vector, p.s.toggles.graph = f, v, g
                r, _ = p.evaluate(k=ns.k, questions=qset, log=False)
                rows.append(dict(r["summary"], combo=name))
            _out(rows, as_json, "\n".join("%-12s hit@%d=%.3f mrr=%.3f term_recall=%.3f" % (r["combo"], ns.k, r["hit@k"], r["mrr"], r["term_recall"]) for r in rows))
            return 0
        r, tr = p.evaluate(k=ns.k, questions=qset, log=False)
        if as_json:
            _out({"result": r, "trace": tr}, True)
            return 0
        for row in r["rows"]:
            print("%s rank=%s term=%.2f  %s" % ("✔" if row["hit"] else "✘", row["rank"], row["term_recall"], row["q"]))
        print("summary:", json.dumps(r["summary"], ensure_ascii=False))
        return 0

    if ns.cmd == "graph":
        g = p.graph_export(limit=ns.limit, community=ns.community, provenance=ns.provenance,
                           types=[x.strip() for x in ns.types.split(",")] if ns.types else None)
        if as_json:
            _out(g, True)
            return 0
        print("nodes=%d edges=%d communities=%d provenance=%s" % (len(g["nodes"]), len(g["edges"]), len(g["communities"]), json.dumps(g["provenance_counts"])))
        for n in g["nodes"][: ns.limit]:
            print("  %-40s %-10s deg=%-3s C%s [%s]" % (n["name"][:40], n["type"], n["degree"], n["community"], n["source"]))
        if ns.provenance:
            for e in g["edges"][:40]:
                print("  %s -[%s]-> %s  (%s w=%.2f conf=%.2f)" % (e["src"], e["rel"], e["dst"], e["provenance"], e["weight"], e["confidence"]))
        for c in g["communities"][:10]:
            print("C%s (n=%s): %s" % (c["community"], c["size"], (c["summary"] or "")[:120]))
        return 0

    if ns.cmd == "entity":
        name = " ".join(ns.name)
        from .graph_rules import entity_id_for
        eid = name if name.startswith("e:") else entity_id_for(name)
        d = p.entity_detail(eid)
        if not d:
            hits = p.store.entity_fts('"%s"' % name, 5)
            print("not found. candidates:", [p.store.get_entity(e)["name"] for e, _ in hits])
            return 1
        if as_json:
            _out(d, True)
            return 0
        e = d["entity"]
        print("%s (%s) src=%s deg=%s C%s\n%s" % (e["name"], e["type"], e["source"], e["degree"], e["community"], e["description"]))
        for r in d["relations"][:30]:
            print("  %s -[%s]-> %s  w=%.2f %s" % (r["src_name"], r["rel"], r["dst_name"], r["weight"] or 0, (r["description"] or "")[:60]))
        for m in d["mentions"][:5]:
            print("  · %s: %s" % (m["chunk_id"], m["text"][:100].replace("\n", " ")))
        return 0

    if ns.cmd == "search":
        from .profiler import Profiler
        from .retrieval import fts_search, vector_search, graph_search
        q = " ".join(ns.question)
        prof = Profiler("search")
        if ns.channel == "fts":
            rows = fts_search(p.store, q, ns.k, p.store.synonyms(), prof)
            out = [{"chunk_id": c, "score": s_, "snippet": sn} for c, s_, sn in rows]
        elif ns.channel == "vector":
            out = [{"chunk_id": c, "score": s_} for c, s_ in vector_search(p.store, p.embedder, q, ns.k, prof)]
        else:
            g = graph_search(p.store, q, ns.k, p.s.graph_hops, prof)
            out = {"chunks": g["chunks"], "seeds": g.get("seeds"), "entities": g["entities"][:10], "relations": g["relations"][:10]}
        _out({"result": out, "trace": prof.finish()}, as_json, json.dumps(jsonable(out), ensure_ascii=False, indent=1))
        return 0

    if ns.cmd == "evolve":
        from . import evolve as ev
        a = ns.action
        if a == "status":
            st = ev.status(p)
            if as_json:
                _out(st, True)
                return 0
            print("auto_apply=%s min_conf=%.2f pending=%d applied=%d synonyms=%d" % (st["auto_apply"], st["min_confidence"], len(st["pending"]), len(st["applied"]), len(st["synonyms"])))
            for pr in st["pending"]:
                print("  #%d %-10s conf=%.2f %s  <- %s" % (pr["id"], pr["kind"], pr["confidence"] or 0, json.dumps(pr["payload"], ensure_ascii=False)[:80], (pr["reason"] or "")[:60]))
            return 0
        if a == "list":
            _out(p.store.proposals(ns.args[0] if ns.args else None), True)
            return 0
        if a == "apply":
            res = ev.apply_proposal(p, int(ns.args[0]), evaluate=not ns.no_eval)
            _out(res, True)
            return 0
        if a == "reject":
            _out(ev.reject_proposal(p, int(ns.args[0]), " ".join(ns.args[1:])), True)
            return 0
        if a == "review":
            _out(ev.llm_review(p), True)
            return 0
        if a == "feedback":
            qid, fb = int(ns.args[0]), int(ns.args[1])
            _out(ev.record_feedback(p, qid, fb, " ".join(ns.args[2:])), True)
            return 0

    if ns.cmd == "wiki":
        from .profiler import Profiler
        from .wiki import write_wiki
        prof = Profiler("wiki")
        r = write_wiki(p.store, p.s.wiki_dir, prof, ns.min_degree)
        _out({"result": r, "trace": prof.finish()}, as_json, "wiki pages written: %s -> %s" % (r["pages"], p.s.wiki_dir))
        return 0

    if ns.cmd == "docs":
        docs = p.store.list_docs()
        _out(docs, as_json, "\n".join("%-70s %-5s chunks=%s" % (d["doc_id"][:70], d["kind"], d["n_chunks"]) for d in docs))
        return 0

    if ns.cmd == "stats":
        st = {"stats": p.store.stats(), "providers": p.provider_status(), "toggles": p.s.toggles.__dict__}
        _out(st, as_json, json.dumps(jsonable(st), ensure_ascii=False, indent=2))
        return 0

    if ns.cmd == "config":
        if ns.action == "paths":
            from .config import all_paths
            _out(all_paths(), as_json, "\n".join("%-14s %s" % (k, v) for k, v in all_paths().items()))
            return 0
        if ns.action == "show":
            if ns.effective:
                from .config import effective_settings
                rows = effective_settings(p.s)
                _out(rows, as_json, "\n".join("%-34s = %-30s [%s]%s  %s" % (r["key"], json.dumps(r["value"], ensure_ascii=False)[:30], r["source"],
                                                                         "" if r["value"] == r["default"] else " (기본 %s)" % json.dumps(r["default"], ensure_ascii=False)[:20], r["env"]) for r in rows))
                return 0
            _out(p.s.to_dict(), True)
            return 0
        if ns.action == "reset":
            if not _confirm_destructive(ns, "config.json 을 기본값으로 덮어쓰기", "코퍼스 경로·프로바이더·토글 설정이 모두 초기화됩니다 (색인 DB 는 유지)"):
                print("cancelled")
                return 4
            save_settings(Settings())
            print("config reset")
            return 0
        ov = {}
        for kv in ns.kv:
            k, _, v = kv.partition("=")
            ov[k] = v
        apply_overrides(p.s, ov)
        save_settings(p.s)
        _out(p.s.to_dict(), True)
        return 0

    if ns.cmd == "models":
        if ns.action == "set":
            ov = {}
            for kv in ns.kv:
                k, _, v = kv.partition("=")
                ov[k] = v
            apply_overrides(p.s, ov)
            save_settings(p.s)
            p.reload()
        if ns.action == "test":
            r = p.test_providers(live=bool(ns.live))
            if as_json:
                _out(r, True)
            else:
                for k, x in r.items():
                    mark = "OK " if x.get("ok") else "FAIL"
                    extra = ("  live: %s %.0fms %s" % ("OK" if x.get("live_ok") else "FAIL", x.get("live_ms", 0), x.get("live_detail", ""))) if "live_ok" in x else ""
                    print("[%s] %-10s %s/%s  %.0fms  %s%s" % (mark, k, x.get("provider") or x.get("url") or "", x.get("model"), x.get("ms") or 0, x.get("detail", ""), extra))
                if not ns.live:
                    print("(ping 만 확인. PAT 권한·헤더·모델명·headless 실행까지 확인하려면 models test --live)")
            return 0 if all(x.get("ok") for x in r.values()) else 1
        st = p.provider_status()
        if as_json:
            _out(st, True)
            return 0
        print("embedder: %s model=%s dim=%s available=%s (embed_provider=%s embed_model=%r)" % (
            st["embedder"]["name"], st["embedder"].get("model"), st["embedder"].get("dim"), st["embedder"]["available"],
            st["embedder"]["provider_setting"], st["embedder"]["model_setting"]))
        print("global llm: provider=%s model=%s effort(extract/rerank)=%s effort(answer)=%s" % (p.s.llm_provider, p.s.llm_model, p.s.llm_effort, p.s.answer_effort))
        for role, r in st["roles"].items():
            c = r["configured"]
            print("  %-8s -> %s/%s effort=%s available=%s%s  [%s]" % (role, c["provider"], c["model"], c["effort"], r["available"],
                                                                     " (role override)" if r["overridden"] else " (global)", st["catalog"]["roles"].get(role, "")))
        print("변경: python -m llmwiki models set rerank_provider=ollama rerank_model=llama3.1 answer_model=claude-opus-5 embed_provider=voyage embed_model=voyage-3.5")
        return 0

    if ns.cmd == "requests":
        from .profiler import flatten_trace
        if ns.action == "list":
            rows = p.store.requests(ns.kind, ns.limit)
            if as_json:
                _out(rows, True)
                return 0
            for r in rows:
                print("#%-5d %s %-6s %8.1f ms llm=%s tok=%s/%s sql=%-5s %s%s" % (
                    r["id"], __import__("time").strftime("%m-%d %H:%M:%S", __import__("time").localtime(r["ts"])), r["kind"], r["ms"] or 0,
                    r["llm_calls"], r["input_tokens"], r["output_tokens"], r["sql_count"], (r["summary"] or "")[:60], " !! " + r["error"][:40] if r.get("error") else ""))
            return 0
        rid = ns.id
        if ns.action == "last" or rid is None:
            rows = p.store.requests(ns.kind, 1)
            if not rows:
                print("no requests")
                return 1
            rid = rows[0]["id"]
        r = p.store.get_request(rid)
        if not r:
            print("request %s not found" % rid)
            return 1
        if as_json:
            _out(r, True)
            return 0
        print("#%d %s  %s  (%.1f ms, debug_level=%s)" % (r["id"], r["kind"], r["summary"], r["ms"] or 0, r["debug_level"]))
        if r.get("trace"):
            _print_trace(r["trace"], verbose=True)
        return 0

    if ns.cmd == "system":
        info = p.system_info(ns.target_docs, ns.daily_new, ns.horizon_days)
        if as_json:
            _out(info, True)
            return 0
        ix = info["index"]
        print("index: docs=%s chunks=%s embeddings=%s entities=%s relations=%s db=%.1fMB wal=%.1fMB" % (
            ix["docs"], ix["chunks"], ix["embeddings"], ix["entities"], ix["relations"], info["files"]["db_mb"], info["files"]["wal_mb"]))
        pr = info["projection"]
        for k in ("current", "target", "after_horizon"):
            v = pr[k]
            print("  %-14s docs=%-6d chunks=%-7d vector_matrix=%6.1fMB db≈%6.1fMB" % (k, v["docs"], v["chunks"], v["vector_matrix_mb"], v["db_mb"]))
        print("  note:", pr["assumptions"]["note"])
        ql = info["query_latency"]
        print("query latency (last %s): avg=%s p50=%s p95=%s ms" % (ql["n"], ql["avg_ms"], ql["p50_ms"], ql["p95_ms"]))
        print("builds:", ", ".join("%.0fms" % b["ms"] for b in info["build_history"][-10:]) or "-")
        print("caches:", json.dumps(info["caches"], ensure_ascii=False))
        print("watcher:", json.dumps(info["watcher"], ensure_ascii=False))
        return 0

    if ns.cmd == "maintenance":
        if ns.action == "purge_requests" and not _confirm_destructive(ns, "requests 테이블(요청 프로파일 이력 %s건) 삭제" % p.store.stats().get("requests")):
            _out({"error": "cancelled"}, True)
            return 4
        _out(p.maintenance(ns.action), True)
        return 0

    if ns.cmd == "users":
        from .auth import Auth, ROLES
        a = Auth(p.s)
        if ns.action == "list":
            rows = a.list_users()
            _out(rows, as_json, "\n".join("%-16s %-9s %s%s" % (r["name"], r["role"], r.get("display") or "", "" if r["has_password"] else "  (비밀번호 없음 — SSO 전용)") for r in rows)
                 or "(사용자 없음 — users add <id> --role admin)")
            info = a.public_info()
            if not as_json:
                print("mode=%s (effective: %s) · local=%s · sso=%s · 파일: %s" % (a.cfg.get("mode"), a.mode, info["local"], info["sso"], __import__("llmwiki.auth", fromlist=["security_path"]).security_path()))
            return 0
        if not ns.name:
            print("사용자 id 필요")
            return 1
        if ns.action in ("add", "passwd"):
            pw = ns.password or os.environ.get("LLMWIKI_PASSWORD")
            if pw is None and ns.action == "add" and ns.yes:
                pw = None   # SSO 전용 계정(역할만 지정)
            elif pw is None:
                import getpass
                try:
                    pw = getpass.getpass("비밀번호 (%s): " % ns.name)
                    if pw != getpass.getpass("다시 입력: "):
                        print("불일치")
                        return 1
                except (EOFError, KeyboardInterrupt):
                    print("취소")
                    return 1
            try:
                if ns.action == "add":
                    a.add_user(ns.name, pw, ns.role or "viewer", ns.display)
                    print("added %s role=%s%s" % (ns.name, ns.role or "viewer", "" if pw else " (SSO 전용, 비밀번호 없음)"))
                else:
                    a.set_password(ns.name, pw)
                    print("password updated: %s" % ns.name)
            except ValueError as e:
                print("error: %s" % e)
                return 1
            return 0
        if ns.action == "remove":
            print("removed" if a.remove_user(ns.name) else "no such user")
            return 0
        if ns.action == "set-role":
            if ns.role not in ROLES:
                print("--role viewer|operator|admin 필요")
                return 1
            a.set_role(ns.name, ns.role)
            print("role updated: %s → %s" % (ns.name, ns.role))
            return 0

    if ns.cmd == "security":
        from .auth import Auth, load_security, save_security, DEFAULT_SECURITY
        from . import auth as _auth
        if ns.action == "init":
            if os.path.exists(_auth.security_path()):
                print("already exists: %s" % _auth.security_path())
                return 1
            save_security(json.loads(json.dumps(DEFAULT_SECURITY)))
            print("created %s — 다음: users add <id> --role admin, 필요하면 sso 항목 설정 (docs/SECURITY.md)" % _auth.security_path())
            return 0
        if ns.action == "audit":
            rows = Auth.audit_tail(ns.n)
            _out(rows, as_json, "\n".join("%s %-12s %-9s %-5s %-12s %s%s" % (r.get("time"), r.get("user"), r.get("role"), "ok" if r.get("ok") else "DENY",
                                                                            r.get("level"), r.get("op"), (" · " + r["error"]) if r.get("error") else "") for r in rows) or "(no audit rows)")
            return 0
        a = Auth(p.s)
        cfg = json.loads(json.dumps(a.cfg))
        for v in (cfg.get("users") or {}).values():
            v.pop("pw", None)
        _out({"path": _auth.security_path(), "effective_mode": a.mode, "security": cfg}, as_json,
             "file: %s\nmode: %s (effective %s)\nusers: %d · sso: %s (%s)\ndestructive: phrase=%r reauth=%s snapshot_before=%s keep=%s\n%s" % (
                 _auth.security_path(), cfg.get("mode"), a.mode, len(cfg.get("users") or {}), "on" if (cfg.get("sso") or {}).get("enabled") else "off",
                 (cfg.get("sso") or {}).get("type"), (cfg.get("destructive") or {}).get("confirm_phrase"), (cfg.get("destructive") or {}).get("require_reauth"),
                 (cfg.get("destructive") or {}).get("snapshot_before"), (cfg.get("destructive") or {}).get("snapshot_keep"),
                 "" if os.path.exists(_auth.security_path()) else "(파일 없음 — 기본값. 'security init' 으로 생성)"))
        return 0

    if ns.cmd == "snapshot":
        from . import snapshots as _snap
        if ns.action == "list":
            rows = _snap.list_(p)
            _out(rows, as_json, "\n".join("%-32s %-20s %6.1fMB  %s %s" % (r["name"], r.get("tag"), (r.get("bytes") or 0) / 1e6,
                                                                       json.dumps(r.get("counts") or {}, ensure_ascii=False), r.get("reason") or "") for r in rows) or "(no snapshots)")
            return 0
        if ns.action == "create":
            r = _snap.create(p, ns.tag, actor="cli", reason="manual")
            _out(r, as_json, "snapshot %s (%.1fMB)" % (r["name"], r["bytes"] / 1e6))
            return 0
        if ns.action == "prune":
            _out({"removed": _snap.prune(p, ns.keep)}, True)
            return 0
        if ns.action == "restore":
            if not ns.name:
                print("snapshot 이름 필요 (snapshot list)")
                return 1
            if not _confirm_destructive(ns, "스냅샷 %s 로 복원" % ns.name, "현재 DB/wiki/rules/config 가 스냅샷 시점으로 교체됩니다 (복원 직전 상태도 자동 스냅샷)"):
                print("cancelled")
                return 4
            _snap.create(p, "auto:before-restore", actor="cli", reason="before restore %s" % ns.name)
            r = _snap.restore(p, ns.name)
            _out(r, as_json, "restored %s: %s" % (ns.name, json.dumps(r["stats"], ensure_ascii=False)[:200]))
            return 0

    if ns.cmd == "watch":
        interval = ns.interval or p.s.auto_build_interval
        print("watching %s every %ss (Ctrl+C to stop)" % (p.s.corpus_dirs, interval))
        import time as _t
        while True:
            r = p.auto_build_tick(progress=lambda m: print("  ·", m))
            print("%s scan %.0fms changed=%d removed=%d built=%s%s" % (_t.strftime("%H:%M:%S"), r["scan_ms"], r["n_changed"], r["n_removed"],
                                                                     r.get("built"), (" (%.0f ms)" % r["build"]["ms"]) if r.get("build") else ""))
            if ns.once:
                return 0
            try:
                _t.sleep(max(5, interval))
            except KeyboardInterrupt:
                return 0

    if ns.cmd == "tuning":
        from . import tuning as tn
        if ns.action == "set":
            for kv in ns.kv:
                k, _, v = kv.partition("=")
                try:
                    tn.T.set(k.strip(), v.strip())
                except (KeyError, ValueError) as e:
                    print("ERROR:", e)
                    return 1
            tn.save_tuning(tn.T)
            p.reload_tuning()
        elif ns.action == "reset":
            tn.T.reset(ns.kv[0] if ns.kv else None)
            tn.save_tuning(tn.T)
            p.reload_tuning()
        elif ns.action == "doc":
            import os as _os
            from .config import ROOT
            path = _os.path.join(ROOT, "docs", "TUNING.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(tn.render_doc(p.s, tn.T))
            print("written:", path)
            return 0
        rows = tn.T.describe(p.s)
        if ns.stage:
            rows = [r for r in rows if r["stage"] == ns.stage]
        if as_json:
            _out(rows, True)
            return 0
        cur = None
        for r in rows:
            if r["stage"] != cur:
                cur = r["stage"]
                print("\n[%s] %s" % (cur, tn.STAGES.get(cur, "")))
            print("  %-24s = %-28s %s(기본 %s, %s%s)" % (r["key"], r["value"], "* " if r["overridden"] else "", r["default"],
                                                     "config.json" if r["source"] == "config" else "tuning.json", ", rebuild" if r["rebuild"] else ""))
            print("      %s" % r["desc"])
            if r["impact"]:
                print("      impact: %s" % r["impact"])
            if r["example"]:
                print("      예: %s" % r["example"])
        print("\n파일: %s   (변경: tuning set 키=값 · config 항목은 config set)" % tn.TUNING_PATH)
        return 0

    if ns.cmd == "arch":
        from .architecture import registry, render_text
        reg = registry()
        if ns.flow:
            reg["flows"] = {k: v for k, v in reg["flows"].items() if k == ns.flow}
        if as_json:
            _out(reg, True)
            return 0
        print(render_text(reg, p.s.toggles.__dict__))
        return 0

    if ns.cmd == "mcp":
        from .mcp import serve_stdio
        serve_stdio(p)
        return 0

    if ns.cmd == "serve":
        from .web.server import serve
        serve(p, ns.host, ns.port, insecure=bool(getattr(ns, "insecure", False)))
        return 0
    print("unknown command: %s" % ns.cmd)
    return 1


_CAPTURED = False   # Web 콘솔(run_captured)에서 실행 중이면 True — 진행 모니터의 stderr 출력을 끈다


def run_captured(argv: List[str], settings: Settings, pipe) -> Dict[str, Any]:
    """Web 콘솔용: stdout 을 캡처해 문자열로 반환."""
    global _CAPTURED
    buf = io.StringIO()
    code = 0
    _CAPTURED = True
    try:
        with redirect_stdout(buf):
            code = run(argv, settings, pipe)
    except SystemExit as e:  # argparse 오류/--help
        code = int(e.code or 0)
    except Exception as e:
        buf.write("ERROR: %s: %s" % (type(e).__name__, e))
        code = 1
    finally:
        _CAPTURED = False
    return {"code": code, "output": buf.getvalue()}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(run())
