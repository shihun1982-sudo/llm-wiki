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
  python -m llmwiki maintenance vacuum|fts_optimize|wal_checkpoint|clear_cache|warm_cache|refresh_doc_refs|prune_requests
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
from typing import Any, Dict, List, Optional, Tuple

import time

from .config import Toggles, Settings, load_settings, save_settings, apply_overrides
from .profiler import jsonable
from . import progress as _pg


def _add_toggle_flags(p: argparse.ArgumentParser) -> None:
    p.set_defaults(_has_toggle_flags=True)   # 이 명령만 토글 플래그(--fts/--no-fts …)를 갖는다 (_overrides_from_ns 가 확인)
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
    from . import __version__ as _ver
    ap.add_argument("--version", action="version", version="llmwiki %s" % _ver, help="버전 (docs/RELEASE_NOTES.md 의 최신 절과 같다)")
    ap.add_argument("--user", dest="cli_user", default=None, help="CLI 실행자 로컬 계정 (security.json users). 비밀번호는 --password / LLMWIKI_PASSWORD / 프롬프트. 기본 역할은 security.json cli.default_role")
    ap.add_argument("--password", dest="cli_password", default=None, help="--user 의 비밀번호 (스크립트용; 가능하면 LLMWIKI_PASSWORD 환경변수 사용)")
    ap.add_argument("--log-level", dest="log_level", default=None, help="이번 실행의 logs/ 파일 로그 레벨 (DEBUG|INFO|WARNING|ERROR; = LLMWIKI_LOG_LEVEL)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("build", help="코퍼스 색인 (FTS/Vector/Graph/Wiki) · build status · build verify [--fix] · build fts|vector|graph [--full] (채널 리빌드)")
    p.add_argument("action", nargs="?", choices=["run", "status", "verify", "fts", "vector", "graph"], default="run",
                   help="run(기본) | status | verify | fts(FTS 색인만 다시) | vector(임베딩만: 없는 청크, --full 이면 전부) | graph(그래프만 전체 재추출)")
    p.add_argument("--channels", default=None, help="run: 이번 빌드에서 처리할 채널만 (fts,vector,graph 쉼표 목록; 나머지 단계는 skipped)")
    p.add_argument("--fix", action="store_true", help="verify: 안전한 정리(댕글링·고아·n_chunks·stale 위키) 수행")
    p.add_argument("--full", action="store_true", help="증분 무시, 전체 리빌드 (vector: 캐시 무시 전부 재임베딩)")
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
    p.add_argument("--role", default=None, help="viewer | class3 | class2 | class1 | builder | admin (operator=class1)")
    p.add_argument("--password", default=None, help="비밀번호 (생략하면 프롬프트; 환경변수 LLMWIKI_PASSWORD 도 인식)")
    p.add_argument("--display", default="", help="표시 이름")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("security", help="로그인/역할/권한/파괴적 작업 정책 (security.json): show | init | audit | perms [show|set <level|op>=<role> …|reset] | docacl [show|check|init]")
    p.add_argument("action", choices=["show", "init", "audit", "perms", "docacl"], nargs="?", default="show")
    p.add_argument("args", nargs="*", help="perms set read=viewer run=viewer '/api/eval=class2' 'cli:trial run=class2' | perms reset | docacl show | docacl check | docacl init")
    p.add_argument("--n", type=int, default=50, help="audit: 최근 N 건")
    p.add_argument("--role", default="viewer", help="docacl check: 이 역할로 봤을 때 몇 건이 가려지는지 (기본 viewer)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("apikey", help="API 키 (MCP HTTP / 스크립트용 Bearer 토큰, 역할 부여): add <name> --role viewer | list | remove <id|name>")
    p.add_argument("action", choices=["add", "list", "remove"], nargs="?", default="list")
    p.add_argument("name", nargs="?")
    p.add_argument("--role", default="viewer")
    p.add_argument("--note", default="")
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

    p = sub.add_parser("logs", help="logs/ 조회: tail | grep --request <id> | --run <run_id> | --text | files | status(총량 제한 상태)")
    p.add_argument("action", choices=["tail", "grep", "files", "dir", "status"], nargs="?", default="tail")
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

    p = sub.add_parser("mcp-source", help="외부 소스/다른 RAG(mcp_sources.json): list | test [name] | tools <name> | retrieve \"질의\" [--source n] | ingest [name] [--since] [--dry-run] | enrich \"질의\" | fetch <name> <tool> [json] | federated")
    p.add_argument("action", choices=["list", "test", "tools", "retrieve", "ingest", "enrich", "fetch", "federated"], nargs="?", default="list")
    p.add_argument("--source", default=None, help="retrieve: 이 소스만")
    p.add_argument("--k", type=int, default=5, help="retrieve: 소스당 결과 수")
    p.add_argument("args", nargs="*")
    p.add_argument("--since", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("rules", help="규칙 기반 질의 확장 사전(query_rules.json): types(유형 표) | show | add <type> <term> <values…> | remove <type> <term> [value] | test \"질의\" | explain <용어>(어느 유형·방향으로 무엇을 끌어오나) | lint(중복·순환·사슬 점검) | merge <파일> [--graph] [--replace]")
    p.add_argument("action", choices=["show", "add", "remove", "test", "explain", "stats", "path", "lint", "merge", "effect", "types"], nargs="?", default="show")
    p.add_argument("args", nargs="*")
    p.add_argument("--order", default="fired", choices=["fired", "helped", "rate", "useless"],
                   help="effect: 정렬 — fired(많이 걸린 순) | helped(기여 많은 순) | rate(기여율) | useless(걸리기만 하고 기여 0)")
    p.add_argument("--reset", action="store_true", help="effect: 누적치를 지운다 (args 에 용어를 주면 그 규칙만)")
    p.add_argument("--graph", action="store_true", help="merge: query_rules.json 이 아니라 그래프 규칙(data/rules.json) 에 합친다")
    p.add_argument("--replace", action="store_true", help="merge: 같은 용어의 값을 합치지 않고 통째로 바꾼다")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("reset", help="관리자 초기화: data(색인·빌드 산출물) | settings(설정 파일) | logs(로그·이력). "
                                     "기본은 **미리보기**이며, 실제로 지우려면 --apply 를 준다. 다른 환경으로 옮길 때 쓴다")
    p.add_argument("scope", choices=["data", "settings", "logs"], nargs="?", default=None)
    p.add_argument("--apply", action="store_true", help="실제로 지운다 (없으면 미리보기만)")
    p.add_argument("--yes", action="store_true", help="확인 문구 생략 (스크립트용)")
    p.add_argument("--no-snapshot", dest="no_snapshot", action="store_true", help="data: 지우기 전 자동 스냅샷을 만들지 않는다")
    p.add_argument("--purge-wiki-notes", dest="purge_wiki_notes", action="store_true", help="data: 사람이 쓴 위키 편집 노트까지 지운다")
    p.add_argument("--clear-embed-cache", dest="clear_embed_cache", action="store_true",
                   help="data: 임베딩 캐시까지 지운다 (임베더를 바꿀 때. 다음 빌드에서 전부 다시 임베딩한다)")
    p.add_argument("--include-security", dest="include_security", action="store_true",
                   help="settings: security.json·docacl.json 도 초기화 (계정·권한이 사라진다)")
    p.add_argument("--include-env", dest="include_env", action="store_true",
                   help="settings: .env 도 삭제 (API 키·PAT 가 사라진다 — 복구 불가)")
    p.add_argument("--include-proposals", dest="include_proposals", action="store_true",
                   help="logs: 자가진화 제안도 지운다")
    p.add_argument("--include-sessions", dest="include_sessions", action="store_true",
                   help="logs: 로그인 세션도 초기화 (모두 다시 로그인)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("graph-rules", help="그래프 빌드 규칙(data/rules.json): show | types(엔티티 type·값 종류·관계 어휘) | lint(빌드 전 정적 점검) | test \"<문장>\"(무엇이 잡히나) | add-entity <이름> <type> [별칭…] | add-alias <엔티티> <별칭…> | fill-defaults | path")
    p.add_argument("action", choices=["show", "types", "lint", "test", "add-entity", "add-alias", "fill-defaults", "path"],
                   nargs="?", default="show")
    p.add_argument("args", nargs="*")
    p.add_argument("--doc-type", dest="doc_type", default="", help="test: 문서 유형 (link_rules 가 걸리는지 보려면 — 예 cl)")
    p.add_argument("--ext-id", dest="ext_id", default="", help="test: 문서 ID (예 CL-55302)")
    p.add_argument("--dry-run", action="store_true", help="fill-defaults: 쓰지 않고 무엇이 채워질지만")
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

    p = sub.add_parser("precompute", help="답변 사전 계산 캐시: run [--from-log N] | status | check(고장난 답변 찾기) | clear [--stale|--broken] | doc-vectors(문서 카드 임베딩 재생성)")
    p.add_argument("action", choices=["run", "status", "check", "clear", "doc-vectors"], nargs="?", default="status")
    p.add_argument("--from-log", type=int, default=20)
    p.add_argument("--stale", action="store_true")
    p.add_argument("--broken", action="store_true", help="같은 구절을 되풀이하는 고장난 답변만 지운다 (precompute check 로 먼저 확인)")
    _add_toggle_flags(p)   # --json 포함

    p = sub.add_parser("forensic", help="포렌식: <request_id> | last | list | summary | run <request_id> [--llm] | expect <request_id|last> --doc … --term … (기대 결과 포렌식)")
    p.add_argument("target", nargs="?", default="last")
    p.add_argument("args", nargs="*", help="run <request_id> | expect <request_id|last>")
    p.add_argument("--llm", action="store_true", help="LLM(forensic 역할) 추가 소견")
    p.add_argument("--doc", dest="docs", action="append", default=[], help="expect: 기대 문서 (ext_id 예 ISSUE-2003, 또는 doc_id 부분 문자열). 여러 번")
    p.add_argument("--term", dest="terms", action="append", default=[], help="expect: 답변/근거에 있어야 했던 용어·수치. 여러 번")
    p.add_argument("--chunk", dest="chunks", action="append", default=[], help="expect: 기대 청크 id (doc_id#n). 여러 번")
    p.add_argument("--note", default="", help="expect: 자유 메모 (에피소드 피드백에 저장)")
    p.add_argument("--propose", action="store_true", help="expect: 수정안(pin/규칙)을 자가진화 제안 큐에 등록")
    p.add_argument("--only", default="problems",
                   help="list: 무엇을 보나 — problems(기본: insufficient·weak·expectation·error) | all | "
                        "판정 이름(sufficient·weak·insufficient·expectation, 콤마로 여러 개). "
                        "기록의 대부분은 정상 건이라 기본을 문제 건으로 좁혀 둔다")
    p.add_argument("--q", default="", help="list: 질의문에 이 말이 들어간 것만")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("fusion", help="융합 방식 비교: compare [--methods rrf,zscore,…] [--k 5] [--questions 파일]")
    p.add_argument("action", choices=["compare", "show"], nargs="?", default="show")
    p.add_argument("--methods", default=None)
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--questions", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("memory", help="자가진화 메모리(시스템이 스스로 배운 것): status | episodes [--only feedback|negative|positive] [--q 검색] | "
                                      "boosts(지금 검색이 받는 피드백 부스트) | decaying(사라지기 직전 제안) | decay | consolidate")
    p.add_argument("action", choices=["status", "decay", "consolidate", "episodes", "boosts", "decaying"], nargs="?", default="status")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--only", choices=["feedback", "negative", "positive"], default=None, help="episodes: 피드백이 있는 것만 / 👎 만 / 👍 만")
    p.add_argument("--q", default=None, help="episodes: 질문 본문에서 찾기")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("time", help="한국어 시간 표현 파싱 테스트: time \"지난주 리뷰한 CL\"")
    p.add_argument("text", nargs="+")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("trial", help="회귀 trial: run --name A [--preset q] [--set k=v …] | candidates(비교에 쓸 과거 질의 고르기) | list | compare A B [C D] | report A | show A")
    p.add_argument("action", choices=["run", "candidates", "list", "compare", "report", "show"], nargs="?", default="list")
    p.add_argument("refs", nargs="*", help="trial id 또는 이름")
    p.add_argument("--name", default=None)
    p.add_argument("--set", dest="sets", action="append", default=[], help="k=v (settings/toggles/tuning), 여러 번")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--questions", default=None)
    p.add_argument("--source", default="queries", choices=["evalset", "queries"],
                   help="run: 문항을 어디서 — **queries(기본, 실제 질의 이력)** | evalset(eval/questions.json). "
                        "기본이 실제 이력인 이유: 대개 알고 싶은 것은 '진짜로 물어본 질문에서 좋아졌나' 이고, "
                        "이 저장소에서는 평가셋 자체가 코퍼스에 색인돼 hit@k 가 오염돼 있다(`eval --check`). "
                        "queries 는 정답이 없어 hit@k·mrr·term_recall 을 계산하지 않고 지연·토큰·근거 부족률·단계별 비용을 본다. "
                        "쓸 만한 이력이 없으면 **평가셋으로 물러나며 그 사실을 알린다**. 정답 대비 검색 품질을 재려면 `--source evalset`")
    p.add_argument("--days", type=float, default=7.0, help="run --source queries: 최근 며칠 (기본 7)")
    p.add_argument("--limit", type=int, default=30, help="run --source queries: 문항 수 상한 (기본 30)")
    p.add_argument("--only", default="", choices=["", "negative", "feedback", "weak", "insufficient"],
                   help="run --source queries · candidates: negative(👎 만) | feedback(평가가 달린 것만) | "
                        "weak(근거가 약하거나 못 찾은 것) | insufficient(근거를 못 찾은 것만). "
                        "거르기를 걸면 더 넓게 훑는다 — 문제 질의는 드물어 최근 몇백 건 안에 없을 수 있다")
    p.add_argument("--pick", default="",
                   help="run: **직접 고른** 질의 이력으로 (query_log id 를 콤마로 — `trial candidates` 가 번호를 보여 준다). "
                        "기간·건수로 뭉뚱그리는 --source queries 와 달리 '이 질문들' 을 그대로 쓴다. Web Quality › Trial 비교의 '질의 고르기' 와 같다")
    p.add_argument("--note", default="")
    p.add_argument("--md", action="store_true", help="compare 결과를 markdown 으로")
    p.add_argument("--stages", action="store_true", help="compare: 단계별 표를 텍스트 출력에도 (기본은 달라진 단계 요약만)")
    _add_toggle_flags(p)

    p = sub.add_parser("query", help="질의")
    p.add_argument("question", nargs="+")
    p.add_argument("--k", type=int, default=None)
    p.add_argument("--no-log", action="store_true")
    p.add_argument("--analyze", action="store_true", help="상세 분석 모드로 실행(=--analysis-mode) 하고 리포트 경로·상위 소견을 출력. --print-analysis 로 리포트 전문 출력")
    p.add_argument("--print-analysis", action="store_true", help="--analyze 와 함께: 마크다운 리포트 전문을 stdout 에")
    p.add_argument("--focus", choices=["quality", "speed", "tokens", "all"], default="all", help="--analyze: 렌즈 초점")
    p.add_argument("--output", dest="output_mode", choices=["answer", "fused", "reranked", "context"], default=None,
                   help="출력 모드 (config.json output_mode): answer=끝까지(기본) · fused=융합·부스트 뒤 후보(리랭크 전) · reranked=리랭크 뒤 후보 · context=컨텍스트까지(답변 LLM 생략). --json 이면 candidates/lists/stages 또는 context/refs 포함")
    p.add_argument("--tuning", dest="tuning_kv", default=None,
                   help="이번 실행에만 적용할 튜닝 값 'fts_topk_n=5,fts_topk_w=1.5' (tuning.json 은 바꾸지 않는다; 키는 `tuning show`)")
    # Web 의 overrides · MCP 의 overrides 와 **같은 길**. 예전에는 CLI 에만 이 손잡이가 없어서
    # "timeout 을 7초로 두고 한 번만 돌려 보기" 를 CLI 에서는 config 를 고쳐야 했다 (다른 사용자에게도 영향).
    p.add_argument("--set", dest="set_kv", default=None, action="append",
                   help="이번 실행에만 적용할 설정 'llm_timeout=7,llm_retries=1' (config.json 은 바꾸지 않는다). "
                        "역할 단축키도 된다: 'answer_timeout_s=30,rerank_model=llama3.1'. 여러 번 줄 수 있다. "
                        "허용 키는 역할에 따라 다르다 (URL·경로·서버 운영 키는 admin) — Web/MCP 의 overrides 와 같은 화이트리스트")
    p.add_argument("--answer-mode", dest="answer_mode", choices=["grounded", "best_effort"], default=None,
                   help="답변 모드 (config.json answer_mode 의 요청 단위 오버라이드): grounded=근거만 · best_effort=근거 부족해도 LLM([C#]/[BK] 표시)")
    _add_toggle_flags(p)

    p = sub.add_parser("rerun", help="단계 재실행: 저장해 둔 중간 결과로 <request_id> 를 특정 단계부터 다시 (docs/RERUN.md)")
    p.add_argument("request_id", nargs="?", help="다시 돌릴 원 요청 id")
    p.add_argument("--from", dest="from", default="answer_llm", help="재시작점 (기본 answer_llm). 목록: --points")
    p.add_argument("--points", action="store_true", help="재시작점 목록만 출력")
    p.add_argument("--list", action="store_true", help="저장된 중간 결과 목록")
    p.add_argument("--n", type=int, default=20, help="--list 개수")
    p.add_argument("--no-log", action="store_true")
    _add_toggle_flags(p)

    p = sub.add_parser("sweep", help="파라미터 스윕: run <request_id|last> --key K (--range a:b:s | --values v1,v2) [--repeats N] [--from POINT] [--query \"…\"] | list | show <id> | compare <id> | keys (docs/SWEEP.md)")
    p.add_argument("action", choices=["run", "list", "show", "compare", "keys"], nargs="?", default="list")
    p.add_argument("target", nargs="?", help="run: 기준 request_id 또는 last (--query 가 있으면 생략 가능) · show/compare: 스윕 id")
    p.add_argument("--key", default=None, help="바꿀 키: 튜닝 키(rrf_k) · 토글(rerank, toggles.claim_check) · config 키(top_k_final) · 역할 키(answer_model). 목록: sweep keys")
    p.add_argument("--range", dest="range_", default=None, help="start:stop:step (예 10:100:10, 0.1:0.9:0.2)")
    p.add_argument("--values", default=None, help="쉼표 목록 (예 rrf, zscore 또는 false,true). 토글은 생략하면 false,true")
    p.add_argument("--repeats", type=int, default=1, help="값마다 반복 횟수 (LLM 흔들림을 보려면 2~3)")
    p.add_argument("--from", dest="from_point", default=None, help="재시작점 강제 (기본: 키가 속한 단계에서 자동). 목록: rerun --points")
    p.add_argument("--query", default=None, help="기준 요청이 없을 때 이 질의를 한 번 실행해 기준을 만든다")
    p.add_argument("--log", action="store_true", help="각 재실행을 query_log/자가진화 캡처에도 남긴다 (기본: 요청 기록만)")
    p.add_argument("--n", type=int, default=30, help="list 개수")
    _add_toggle_flags(p)

    p = sub.add_parser("analyze", help="상세 분석 리포트: <request_id>|last [--focus quality|speed|tokens] [--print] [--out 파일] — logs/analysis/req_<id>.md (docs/ANALYSIS_MODE.md)")
    p.add_argument("target", nargs="?", default="last", help="request_id 또는 last")
    p.add_argument("--focus", choices=["quality", "speed", "tokens", "all"], default="all")
    p.add_argument("--print", dest="print_md", action="store_true", help="마크다운 전문을 stdout 에 (기본은 경로·요약만)")
    p.add_argument("--out", default=None, help="마크다운을 이 파일에도 저장")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("eval", help="회귀 평가 (eval/questions.json). --check 로 '이 숫자를 믿어도 되나' 를 먼저 보고, "
                                    "--retrieval-only 로 LLM 없이 빠르게(토큰 0), --forensic 으로 놓친 문항의 원인까지")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--matrix", action="store_true", help="fts/vector/graph 조합별 비교")
    p.add_argument("--questions", default=None, help="질문셋 JSON 경로 (기본 eval/questions.json)")
    p.add_argument("--check", action="store_true",
                   help="점수를 내기 전에 **평가셋 신뢰도**만 점검한다: 평가셋이 코퍼스에 색인됐는지(오염) · 기대 문서가 색인에 있는지 · 문항 수가 충분한지")
    p.add_argument("--retrieval-only", dest="retrieval_only", action="store_true",
                   help="LLM 을 쓰는 단계를 전부 끄고 **검색 지표만**(hit@k·MRR·term_recall). 빠르고 토큰 0 — 검색을 튜닝할 때 이걸 쓴다")
    p.add_argument("--forensic", action="store_true",
                   help="놓친 문항마다 기대 문서/용어로 **원인 분석**까지 (어느 단계에서 탈락했나 + 수정안). 평가셋의 expect_docs/expect_terms 를 그대로 쓴다")
    p.add_argument("--forensic-max", type=int, default=5, help="--forensic: 분석할 실패 문항 수 상한")
    _add_toggle_flags(p)

    p = sub.add_parser("graph", help="그래프 요약/내보내기 | profile [--eval] [--compare] [--out FILE] (그래프 진단: 규모·연결성·커버리지·규칙 기여·제안 — docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md §2.5)")
    p.add_argument("action", nargs="?", choices=["export", "profile"], default="export", help="export(기본) | profile(진단 프로파일)")
    p.add_argument("--eval", action="store_true", help="profile: 그래프 채널만 켠 평가(hit@k/MRR)를 함께 (eval --matrix 의 graph 조합)")
    p.add_argument("--compare", action="store_true", help="profile: 직전 저장 프로파일(data/graph_profiles)과 핵심 지표 비교")
    p.add_argument("--out", default=None, help="profile: 마크다운 리포트를 이 파일에 저장")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--community", type=int, default=None)
    p.add_argument("--provenance", default=None, help="관계 출처 필터: explicit,rule,human,llm,cooccur")
    p.add_argument("--types", default=None, help="노드 유형 필터 (쉼표)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("entity", help="엔티티 상세")
    p.add_argument("name", nargs="+")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("inspect", help="질의 해부 (LLM 없이): 토큰화 · 규칙 확장 · 시간 표현 · 채널 라우팅 · 고정 근거")
    p.add_argument("question", nargs="+")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("search", help="채널 검색 디버그 — 한 채널 또는 여러 채널 조합 (fts,vector,graph · all)")
    p.add_argument("channel", help="fts | vector | graph | 콤마로 여러 개(fts,vector) | all")
    p.add_argument("question", nargs="+")
    p.add_argument("--k", type=int, default=8)
    p.add_argument("--mode", choices=["or", "and", "rrf"], default="or",
                   help="여러 채널일 때 조합 방식: or(합집합·커버리지) | and(교집합·채널 합의) | rrf(질의 경로와 같은 가중 융합)")
    p.add_argument("--require", default=None, help="이 채널들은 **반드시** 찾아야 한다 (AND). 예: --require graph")
    p.add_argument("--exclude", default=None, help="이 채널들이 찾은 것은 결과에서 **뺀다** (NOT). 예: --exclude vector")
    p.add_argument("--doc-types", dest="doc_types", default=None,
                   help="이 문서 유형만 본다 (콤마. 예: --doc-types issue,cl). 유형 목록은 `corpus types`. "
                        "질의의 doc_types 가 *가중치* 인 것과 달리 여기서는 **거르는** 조건이다")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("evolve", help="자가진화 제안 관리: status | show <id> | list [상태] | propose <kind> <payload JSON> | apply <id> | reject <id> [사유] | review | feedback <qid> ±1 [메모] | auto-apply")
    p.add_argument("action", choices=["status", "show", "apply", "reject", "review", "feedback", "list", "propose", "auto-apply", "kinds"])
    p.add_argument("args", nargs="*")
    p.add_argument("--reason", default="manual", help="propose: 제안 이유")
    p.add_argument("--confidence", type=float, default=0.9, help="propose: 신뢰도 (0~1)")
    p.add_argument("--min-confidence", dest="min_conf", type=float, default=None, help="auto-apply: 이 값 이상만 (기본 evolve_min_confidence)")
    p.add_argument("--kinds", default=None, help="auto-apply: 허용할 종류 (콤마. 기본 config evolve_auto_apply_kinds)")
    p.add_argument("--max-apply", dest="max_apply", type=int, default=5, help="auto-apply: 한 번에 적용할 최대 건수")
    p.add_argument("--dry-run", action="store_true", help="auto-apply: 적용하지 않고 대상만 보여 준다")
    p.add_argument("--no-eval", action="store_true", help="apply 시 회귀평가 생략")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("wiki", help="위키 페이지 재생성")
    p.add_argument("--min-degree", type=int, default=1)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("docs", help="색인된 문서 목록")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("stats", help="인덱스 통계/프로바이더 상태 · **`--full` 로 운영 통계**(빌드·질의·지연·토큰·품질·사용자·디스크·임베딩)")
    p.add_argument("--full", action="store_true",
                   help="운영 통계 — 빌드가 어느 단계에서 느린가 · 질의가 얼마나·언제 몰리나 · p50/p95 지연과 느린 질의 · "
                        "토큰을 어디에 쓰나 · 근거 부족·피드백 · 사용자별 · 디스크가 어디서 커지나 · 임베딩 캐시 적중")
    p.add_argument("--days", type=float, default=7.0, help="--full: 집계 기간 (기본 7일)")
    p.add_argument("--section", dest="sections", action="append", default=[],
                   help="--full: 이 섹션만 (여러 번). index|build|queries|latency|tokens|quality|users|storage|embed|trend")
    p.add_argument("--top", type=int, default=8, help="--full: 목록에 보여 줄 개수")
    p.add_argument("--bucket", choices=["day", "week", "month"], default="day",
                   help="--full --section trend: 추세를 일/주/월 중 무엇으로 묶을지 (기본 day). "
                        "기간은 묶음에 맞춰 자동으로 늘어난다 — 주간 12주 · 월간 1년 (--trend-days 로 덮어쓴다)")
    p.add_argument("--trend-days", dest="trend_days", type=float, default=None,
                   help="--full --section trend: 추세 기간을 직접 지정 (기본은 --bucket 에 맞춘 값)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("config", help="설정 보기/변경: show | set | reset | paths | fill-defaults (기본값을 파일에 명시) | reload [--env] | env (.env 가시성) | doc (docs/CONFIG_REFERENCE.md 생성)")
    p.add_argument("action", choices=["show", "set", "reset", "paths", "fill-defaults", "reload", "env", "doc", "bundle"])
    p.add_argument("kv", nargs="*", help="key=value")
    p.add_argument("--effective", action="store_true", help="show: 키별 현재값·기본값·출처(default/file/env)·env 이름")
    p.add_argument("--yes", action="store_true", help="reset: 확인 문구 생략")
    # fill-defaults: config.json(항상) + --tuning(tuning.json 모든 키) + --rules(query_rules.json 모든 type 절 · data/rules.json 모든 절) · --all = 둘 다
    p.add_argument("--tuning", action="store_true", help="fill-defaults: tuning.json 의 모든 튜닝 키를 기본값으로 채움 (_explicit_defaults 표식)")
    p.add_argument("--rules", action="store_true", help="fill-defaults: query_rules.json 의 모든 type 절 + data/rules.json 의 모든 절")
    p.add_argument("--all", action="store_true", help="fill-defaults: --tuning --rules")
    p.add_argument("--examples", action="store_true", help="fill-defaults: setup/*.example.* 파일에도 실행 (저장소 정비용)")
    p.add_argument("--dry-run", action="store_true", help="fill-defaults: 무엇이 추가될지만 보여 주고 쓰지 않음")
    p.add_argument("--env", action="store_true", help="reload: .env 도 다시 읽어 os.environ 을 파일 값으로 덮어씀 (config reload --env)")
    p.add_argument("--out", default=None, help="bundle: 설정을 모아 둘 폴더 (그 폴더를 LLMWIKI_CONF_DIR 로 쓰면 된다)")
    p.add_argument("--from", dest="from_dir", default=None, help="bundle: 이 폴더의 설정을 원래 자리로 되돌린다")
    p.add_argument("--include-secrets", dest="include_secrets", action="store_true",
                   help="bundle: .env 를 값째 복사 (기본은 키 이름만 — 자격증명이 딸려 나가지 않게)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("models", help="역할별 LLM/임베딩 모델 설정 보기·테스트·변경 · 카탈로그(models.json): list | catalog add|remove | discover")
    p.add_argument("action", choices=["show", "test", "set", "list", "catalog", "discover", "policy", "ensemble", "automap"], nargs="?", default="show",
                   help="show(역할별 설정+정책) | test [--live] [--catalog] | **automap [--live] [--apply]**(연결되는 모델만 골라 역할에 자동 배정) | set k=v | list [--role r] [--provider p] (카탈로그) | catalog add <id> --provider … | catalog remove <id> | discover (서버가 제공하는 모델 조회) | policy (역할별 timeout/retry 표) | ensemble show [role] | ensemble set <role> …")
    p.add_argument("kv", nargs="*", help="set: answer_model=claude-opus-5 rerank_provider=ollama answer_timeout_s=120 answer_retries=2 embed_provider=hash ... | catalog add <id> | catalog remove <id> | ensemble show [role] | ensemble set <role>")
    p.add_argument("--live", action="store_true", help="test: ping 외에 실제 완성 호출 1회 (PAT 권한·헤더·모델명·headless 실행 확인, 토큰 소량 소비)")
    p.add_argument("--catalog", action="store_true", help="test: 역할이 아니라 models.json 카탈로그의 enabled 모델 전부를 (provider, model) 로 ping (+--live 면 완성 1회)")
    p.add_argument("--apply", action="store_true", help="automap: 제안을 config.json 의 llm_roles(+임베딩·리랭크)에 실제로 저장한다 (기본은 제안만 출력)")
    # ensemble set <role> 의 플래그 (dest 는 토글 이름과 겹치지 않게 ens_ 접두)
    p.add_argument("--enabled", dest="ens_enabled", default=None, help="ensemble set: true|false")
    p.add_argument("--member", dest="ens_member", nargs="+", action="append", default=None, metavar="N k=v",
                   help="ensemble set: --member 1 provider=anthropic model=claude-sonnet-5 weight=1.5 enabled=true (N = 1..3, 여러 번 가능)")
    p.add_argument("--aggregator", dest="ens_aggregator", nargs="+", default=None, metavar="k=v", help="ensemble set: --aggregator provider=… model=… [effort=…]")
    p.add_argument("--wait", dest="ens_wait", choices=["all", "timeout", ""], default=None, help="ensemble set: all | timeout ('' = llm_ensemble_defaults 상속)")
    p.add_argument("--timeout", dest="ens_timeout", default=None, help="ensemble set: timeout_s (초, '' = 상속)")
    p.add_argument("--min", dest="ens_min", default=None, help="ensemble set: min_results ('' = 상속)")
    p.add_argument("--role", default=None, help="list: 역할 필터")
    p.add_argument("--provider", default=None, help="list/catalog add: provider")
    p.add_argument("--label", default=None, help="catalog add: 표시 이름")
    p.add_argument("--roles", default=None, help="catalog add: 쉼표 목록 (비우면 전 역할)")
    p.add_argument("--tags", default=None, help="catalog add: 쉼표 목록")
    p.add_argument("--notes", default=None, help="catalog add: 메모")
    # 주의: dest 는 토글 이름(embed 등)과 겹치면 안 된다 — _overrides_from_ns 가 토글로 오인해 설정을 덮어쓴다
    p.add_argument("--embedding", dest="catalog_embed", action="store_true", help="catalog add/remove: 임베딩 모델 목록에 넣기")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("server", help="실행 중인 서버(serve) 모니터/제어 (HTTP): status | requests | cancel <token> | limits [set k=v …] | block add|remove ip|user <값> | sessions [revoke <sid>] | maintenance on|off | kick <user> | circuits [reset]")
    p.add_argument("action", choices=["status", "requests", "cancel", "limits", "block", "sessions", "maintenance", "kick", "circuits", "log-level"], nargs="?", default="status")
    p.add_argument("args", nargs="*", help="cancel <token> | limits set concurrency.max_parallel_reads=16 … | block add ip 10.0.0.5 | sessions revoke <sid> | maintenance on [메시지] | kick <user> | circuits reset [key] | log-level DEBUG")
    p.add_argument("--url", default=None, help="서버 URL (기본 http://<web_host>:<web_port>)")
    p.add_argument("--token", default=None, help="admin API 키 (LLMWIKI_API_KEY); 없으면 --user/--password 로 로그인")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("schedule", help="스케줄 작업(schedule.json): list | show <name> | run <name> (서버 없이 지금 실행) | enable|disable <name> | remove <name> | add --task '<json>' | history [-n N] | validate | trigger <name> (실행 중인 서버에 요청)")
    p.add_argument("action", choices=["list", "show", "run", "enable", "disable", "remove", "add", "history", "validate", "trigger"], nargs="?", default="list")
    p.add_argument("name", nargs="?")
    p.add_argument("--task", default=None, help="add: 작업 JSON (예 '{\"name\":\"nightly\",\"cron\":\"0 3 * * *\",\"action\":{\"type\":\"build\"}}')")
    p.add_argument("-n", type=int, default=30, help="history: 최근 N 건")
    p.add_argument("--url", default=None, help="trigger: 서버 URL")
    p.add_argument("--token", default=None, help="trigger: admin API 키")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("requests", help="요청별 프로파일/디버그 trace 조회 · `queries`/`users` 로 질의 로그(누가 무엇을 물었나)")
    p.add_argument("action", choices=["list", "show", "last", "queries", "users"], nargs="?", default="list",
                   help="list|show|last(요청 기록) · queries(질의 로그 — 사용자·창구 포함) · users(사용자별 질의 집계)")
    p.add_argument("id", nargs="?", type=int)
    p.add_argument("--kind", default=None, help="query|build|eval|search")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--user", default=None, help="queries/users: 이 사용자가 낸 질의만")
    p.add_argument("--origin", default=None, help="queries: 창구로 거르기 (web|api|cli|mcp|schedule)")
    p.add_argument("--q", default=None, help="queries: 질문에 이 말이 들어간 것만")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("system", help="확장성/캐시/워처/최근 빌드·질의 지연 통계")
    p.add_argument("--target-docs", type=int, default=3000)
    p.add_argument("--daily-new", type=int, default=20)
    p.add_argument("--horizon-days", type=int, default=365)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("maintenance", help="DB 유지보수")
    p.add_argument("action", choices=["vacuum", "fts_optimize", "wal_checkpoint", "clear_cache", "warm_cache", "refresh_doc_refs",
                                      "purge_requests", "prune_requests"])
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

    p = sub.add_parser("arch", help="구조/흐름과 토글·CLI·튜닝 영향 (Web Architecture 탭과 동일 정의). `arch doc` = 최적화 가이드 문서 생성")
    p.add_argument("action", nargs="?", choices=["show", "doc", "limits"], default="show",
                   help="show(기본) | doc(docs/OPTIMIZATION_GUIDE.md 생성) | limits(단계별 시간 제한 — Web trace 의 '실측 (제한)' 과 같은 값)")
    p.add_argument("--flow", default=None, help="query|build|evolve|watch")
    p.add_argument("--out", default=None, help="doc 의 출력 파일 (기본 docs/OPTIMIZATION_GUIDE.md)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("optimize", help="LLM 에게 그대로 줄 최적화 자료 묶음 생성 (가이드 + 지금 설정 + 질의 실측 + 지시문)")
    p.add_argument("target", nargs="?", default="last", help="request id 또는 last")
    p.add_argument("--focus", choices=["all", "quality", "speed", "tokens"], default="all")
    p.add_argument("--out", default=None, help="출력 파일 (생략하면 화면)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("mcp", help="MCP 서버: stdio(기본) | --transport http (Streamable HTTP, 원격 LLM 다수) | --connect URL (stdio→HTTP 브리지)")
    p.add_argument("--transport", choices=["stdio", "http"], default=None, help="stdio(같은 PC 클라이언트가 자식 프로세스로 실행) | http(POST /mcp 서버; serve 도 /mcp 를 제공). 기본 config.json mcp_transport")
    p.add_argument("--host", default=None, help="http: 바인드 주소 (외부 공개는 0.0.0.0 + API 키). 기본 config.json mcp_host")
    p.add_argument("--port", type=int, default=None, help="http: 포트. 기본 config.json mcp_port")
    p.add_argument("--connect", default=None, help="브리지: 원격 MCP HTTP URL (예 http://host:8765/mcp). stdin/stdout 의 JSON-RPC 를 그 URL 로 중계 (LLMWIKI_MCP_URL, config.json mcp_url)")
    p.add_argument("--token", default=None, help="브리지/HTTP: API 키 (apikey add …; LLMWIKI_MCP_TOKEN)")
    p.add_argument("--insecure", action="store_true", help="http: 로그인 설정 없이 외부에 공개 (권장하지 않음)")
    p.add_argument("--client-config", action="store_true", help="실행하지 않고, 이 환경(python 경로·프로젝트 루트·web_host/web_port) 기준 MCP 클라이언트 설정 JSON(stdio/http/브리지) 을 출력")
    p.add_argument("--doctor", action="store_true", help="실행하지 않고, MCP 설정을 자가 점검: 도구 목록·스키마·플러그인·외부 소스 연결·페더레이션·인증 (bring-up 확인용)")
    p.add_argument("--check-sources", action="store_true", help="--doctor: 외부 소스에 실제로 연결해 본다 (느릴 수 있음)")
    p.add_argument("--json", action="store_true", help="--doctor/--client-config: JSON 으로 출력")
    p.add_argument("--url", default=None, help="--client-config: 클라이언트가 접근할 서버 URL (기본 http://<web_host>:<web_port>; 0.0.0.0 이면 이 PC 호스트명)")

    p = sub.add_parser("serve", help="Web UI 서버 (+ /mcp Streamable HTTP MCP)")
    p.add_argument("--port", type=int, default=None, help="기본 config.json web_port (8765)")
    p.add_argument("--host", default=None, help="0.0.0.0 등 외부 공개 시 security.json 의 로그인 설정이 필요 (없으면 거부). 기본 config.json web_host")
    p.add_argument("--insecure", action="store_true", help="로그인 설정 없이 외부에 공개 (권장하지 않음)")
    return ap


def _strip_global(argv: List[str]) -> List[str]:
    """--user/--password 전역 옵션을 뺀 argv (권한 분류용)."""
    out: List[str] = []
    skip = False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in ("--user", "--password"):
            skip = True
            continue
        if a.startswith("--user=") or a.startswith("--password="):
            continue
        out.append(a)
    return out


def _cli_client(ns: argparse.Namespace) -> Dict[str, str]:
    """CLI 실행자를 Web/MCP 와 **같은 모양**의 client dict 로. 질의 로그·활동 목록의 '사용자' 칸이 된다.

    `_cli_gate` 가 넣어 둔 `ns._actor`(이름·역할·로그인 방법)를 쓴다. 게이트를 지나지 않은 경로면 빈 값이다.
    """
    name, role, via = getattr(ns, "_actor", ("", "", "")) or ("", "", "")
    return {"user": str(name or ""), "role": str(role or ""), "via": str(via or ""),
            "origin": "cli", "ip": "", "agent": "cli"}


def _request_overrides(ns: argparse.Namespace) -> Dict[str, Any]:
    """`--set k=v,k=v` → 요청 단위 overrides. Web/MCP 와 **같은 화이트리스트**를 지난다.

    값은 문자열로 두어도 된다 — `apply_overrides` 가 필드 타입에 맞춰 변환한다(int/float/bool/list/dict).
    역할 단축키(`answer_timeout_s`, `rerank_model`)도 그대로 쓴다(`config.split_role_key`).
    권한은 CLI 게이트가 정한 실행자 역할을 따른다 — CLI 기본 역할이 admin 이면 예전처럼 전부 쓸 수 있고,
    공용 서버에서 `cli.default_role` 을 낮춰 두었다면 Web/MCP 와 똑같이 걸린다.
    """
    raw = getattr(ns, "set_kv", None)
    if not raw:
        return {}
    items = raw if isinstance(raw, list) else [raw]
    ov: Dict[str, Any] = {}
    for chunk in items:
        for part in str(chunk).split(","):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                raise ValueError("'key=value' 형태여야 합니다: %r" % part)
            k, _, v = part.partition("=")
            ov[k.strip()] = v.strip()
    if not ov:
        return {}
    from .auth import filter_overrides, load_security
    role = (getattr(ns, "_actor", ("", "", "")) or ("", "", ""))[1] or "admin"
    try:
        cfg = load_security() or {}
    except Exception:
        cfg = {}
    return filter_overrides(ov, role, cfg)


def _cli_gate(argv: List[str], ns: argparse.Namespace) -> Optional[int]:
    """CLI 권한 게이트: security.json 의 등급표/permissions 로 실행자 역할을 검사한다. 통과하면 None, 거부면 종료 코드.
    실행자 역할: --user/LLMWIKI_USER(로컬 계정) > LLMWIKI_API_KEY > cli.default_role(기본 admin). 거부는 감사 로그에 남는다."""
    from .auth import classify_cli, cli_actor, cli_min_role, AuthError, RANK, User, LEVEL_LABEL, write_audit
    level, op = classify_cli(_strip_global(argv))
    try:
        name, role, via = cli_actor(getattr(ns, "cli_user", None), getattr(ns, "cli_password", None))
    except AuthError as e:
        print("!! %s" % e.error)
        return 5
    need = cli_min_role(level, op)
    ns._actor = (name, role, via)
    if RANK[role] < RANK[need]:
        print("!! 권한 부족: '%s' 작업(%s)은 %s 이상만 실행할 수 있습니다 (현재 %s@%s). --user <id> 로 로그인하거나 security.json cli.default_role/permissions 를 확인하세요."
              % (op, LEVEL_LABEL[level], need, role, via))
        write_audit(User(name, role, via), op, level, False, "cli", error="cli gate: need %s" % need)
        return 5
    if level not in ("read",):
        write_audit(User(name, role, via), op, level, True, "cli")
    return None


def _overrides_from_ns(ns: argparse.Namespace) -> Dict[str, Any]:
    ov: Dict[str, Any] = {}
    # 토글은 _add_toggle_flags 를 붙인 명령에서만 읽는다 — 다른 명령의 같은 이름 플래그(예 models --embedding)를 토글로 오인하지 않도록
    keys = (list(Toggles.__dataclass_fields__) if getattr(ns, "_has_toggle_flags", False) else []) + ["llm_provider", "embed_provider", "llm_model", "debug_level"]
    keys += ["%s_%s" % (r, a) for r in Settings.LLM_ROLES for a in ("model", "provider")]
    for name in keys:
        v = getattr(ns, name, None)
        if v is not None:
            ov[name] = v
    if getattr(ns, "k", None) and getattr(ns, "cmd", "") == "query":   # eval/trial 의 --k 는 평가 k
        ov["top_k_final"] = ns.k
    if getattr(ns, "output_mode", None):           # query --output fused|reranked|context (config.json output_mode 의 요청 단위 오버라이드)
        ov["output_mode"] = ns.output_mode
    if getattr(ns, "answer_mode", None):           # query --answer-mode grounded|best_effort (Web 사이드바 #ov-answer-mode 와 같은 길)
        ov["answer_mode"] = ns.answer_mode
    if getattr(ns, "tuning_kv", None):             # query --tuning k=v,k=v → overrides["tuning"] (request_scope 가 오버레이에 적용)
        tv: Dict[str, Any] = {}
        for part in str(ns.tuning_kv).split(","):
            if "=" in part:
                k_, _, v_ = part.partition("=")
                tv[k_.strip()] = v_.strip()
        if tv:
            ov["tuning"] = tv
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


def _cmd_config_fill_defaults(ns: argparse.Namespace, as_json: bool) -> int:
    """config fill-defaults [--tuning] [--rules] [--all] [--examples] [--dry-run]:
    config.json 의 모든 Settings 키·toggles·llm_roles 뼈대, tuning.json 의 모든 튜닝 키, query_rules.json 의 모든 type 절,
    data/rules.json 의 모든 절을 **기본값으로 채워 쓴다** (있는 값은 유지). --examples 는 setup/*.example.* 에도."""
    import os as _os
    from .config import fill_defaults as _fill_cfg, ROOT as _ROOT, path_for as _pf
    from . import tuning as _tn, query_rules as _qr, graph_rules as _gr
    do_t = bool(ns.tuning or getattr(ns, "all", False))
    do_r = bool(ns.rules or getattr(ns, "all", False))
    dry = bool(getattr(ns, "dry_run", False))
    jobs: List[Tuple[str, str, Any]] = [("config", _pf("config"), _fill_cfg)]
    if do_t:
        jobs.append(("tuning", _pf("tuning"), _tn.fill_defaults))
    if do_r:
        jobs.append(("query_rules", _pf("query_rules"), _qr.fill_defaults))
        jobs.append(("rules", _pf("rules"), _gr.fill_defaults))
    if getattr(ns, "examples", False):
        setup = _os.path.join(_ROOT, "setup")
        for f in ("config.example.json", "config.example.headless.json", "config.example.pat-gateway.json"):
            if _os.path.exists(_os.path.join(setup, f)):
                jobs.append(("config(example)", _os.path.join(setup, f), _fill_cfg))
        if do_t:
            jobs.append(("tuning(example)", _os.path.join(setup, "tuning.example.json"), _tn.fill_defaults))   # 없으면 만든다
        if do_r:
            for f in sorted(_os.listdir(setup)):
                if f.startswith("query_rules.example") and f.endswith(".json"):
                    jobs.append(("query_rules(example)", _os.path.join(setup, f), _qr.fill_defaults))
                if f.startswith("rules.example") and f.endswith(".json"):
                    jobs.append(("rules(example)", _os.path.join(setup, f), _gr.fill_defaults))
    reports = []
    for kind, path, fn in jobs:
        try:
            rep = fn(path, dry_run=dry)
        except Exception as e:
            rep = {"path": path, "error": str(e)}
        reports.append(dict(rep, kind=kind))
    if as_json:
        _out(reports, True)
    else:
        for r in reports:
            if r.get("error"):
                print("[ERR ] %-22s %s — %s" % (r["kind"], r["path"], r["error"]))
                continue
            n_added = len(r.get("added") or []) + len(r.get("added_toggles") or []) + sum(len(v) for v in (r.get("added_roles") or {}).values())
            mark = "dry " if dry else ("write" if r.get("written") else "same ")
            print("[%s] %-22s %s — 추가 %d개%s" % (mark, r["kind"], r["path"], n_added, "  (변경 없음)" if not r.get("changed") else ""))
            if r.get("added"):
                print("        키: %s" % ", ".join(r["added"][:40]) + (" …" if len(r["added"]) > 40 else ""))
            if r.get("added_toggles"):
                print("        toggles: %s" % ", ".join(r["added_toggles"][:40]) + (" …" if len(r["added_toggles"]) > 40 else ""))
            if r.get("added_roles"):
                print("        llm_roles: " + " · ".join("%s(%s)" % (k, ",".join(v)[:60]) for k, v in r["added_roles"].items()))
            if r.get("unknown"):
                print("        모르는 키(유지): %s" % ", ".join(r["unknown"]))
        print("\n%s%s" % ("(dry-run: 쓰지 않았습니다) " if dry else "", "확인: config show --effective · tuning show · 되돌리기: 필요 없는 줄은 지우면 기본값"))
    return 1 if any(r.get("error") for r in reports) else 0


def _cmd_models_ensemble(ns: argparse.Namespace, p, as_json: bool) -> int:
    """models ensemble show [role] | set <role> [--enabled true|false] [--member N k=v …] [--aggregator k=v …] [--wait all|timeout] [--timeout N] [--min N]
    config.json llm_roles.<role>.ensemble 을 읽고 쓴다 (모양은 Settings.effective_ensemble / config._norm_ensemble_raw 와 같다)."""
    from .config import _norm_ensemble_raw, ensemble_template, save_settings as _save
    sub = ns.kv[0] if ns.kv else "show"
    roles = [ns.kv[1]] if len(ns.kv) > 1 else list(Settings.LLM_ROLES)
    for r in roles:
        if r not in Settings.LLM_ROLES:
            print("ERROR: 알 수 없는 역할 %s (%s)" % (r, ", ".join(Settings.LLM_ROLES)))
            return 1
    if sub == "show":
        out = {}
        for role in roles:
            raw = (p.s.llm_roles.get(role) or {}).get("ensemble")
            _rl = p.s.role_llm(role)
            out[role] = {"raw": raw, "effective": p.s.effective_ensemble(role),
                         "role": {"provider": _rl.get("provider", ""), "model": _rl.get("model", "")}}
        if as_json:
            _out(out, True)
            return 0
        for role, d in out.items():
            e = d["effective"]
            print("%-9s ensemble=%s wait=%s timeout_s=%s min_results=%s prompt=%s%s" % (
                role, "ON " if e["enabled"] else "off", e["wait"], e["timeout_s"], e["min_results"], e["prompt"],
                "" if d["raw"] else "  (설정 없음 — llm_ensemble_defaults 상속)"))
            print("    역할 모델: %s/%s  (멤버가 provider/model 을 비우면 이 값을 상속)" % (d["role"]["provider"] or "-", d["role"]["model"] or "-"))
            for i, m in enumerate(e["members"], 1):
                print("    member %d: %s/%s weight=%s effort=%s (provider: %s)" % (i, m["provider"], m["model"], m["weight"], m["effort"] or "-", m["provider_source"]))
            # 켜 놓았는데 쓸 멤버가 없는 상태 — 조용히 단일 LLM 으로 돌기 때문에 반드시 말해 준다.
            if str((raw or {}).get("enabled", "")).strip().lower() in ("1", "true", "yes", "on") and not e["members"]:
                n_raw = len([m for m in ((raw or {}).get("members") or []) if isinstance(m, dict)])
                print("    ! enabled=true 이지만 쓸 멤버가 0개입니다 — 앙상블이 돌지 않고 역할 모델 1회로 동작합니다.")
                print("      멤버는 model 이 비어 있으면 enabled 와 무관하게 빠집니다(멤버 칸 %d개 중 0개 유효)." % n_raw)
                print("      해결: models ensemble set %s --member 1 model=<모델> --member 2 model=<모델>" % role)
            if e["aggregator"].get("model"):
                print("    aggregator: %s/%s" % (e["aggregator"]["provider"], e["aggregator"]["model"]))
            elif e["enabled"]:
                print("    aggregator: (첫 멤버가 취합)")
        print("변경: models ensemble set <role> --enabled true --member 1 provider=anthropic model=claude-sonnet-5 weight=1.5 --member 2 … --aggregator model=… --wait all|timeout --timeout 120 --min 1")
        return 0
    if sub != "set" or len(ns.kv) < 2:
        print("usage: models ensemble show [role] | models ensemble set <role> [--enabled true|false] [--member N k=v …] [--aggregator k=v …] [--wait all|timeout] [--timeout N] [--min N]")
        return 1
    role = ns.kv[1]
    cur = (p.s.llm_roles.get(role) or {}).get("ensemble")
    ens = _norm_ensemble_raw(cur if isinstance(cur, dict) else {})
    tpl = ensemble_template()
    while len(ens["members"]) < Settings.ENSEMBLE_MAX_MEMBERS:
        ens["members"].append(dict(tpl["members"][0]))

    def kvs(items: List[str]) -> Dict[str, str]:
        d: Dict[str, str] = {}
        for it in items:
            k, eq, v = it.partition("=")
            if not eq:
                raise ValueError("k=v 형식이어야 합니다: %s" % it)
            d[k.strip()] = v.strip()
        return d
    try:
        if ns.ens_enabled is not None:
            ens["enabled"] = str(ns.ens_enabled).strip().lower() in ("1", "true", "yes", "on")
        for spec in (ns.ens_member or []):
            n = int(spec[0])
            if not 1 <= n <= Settings.ENSEMBLE_MAX_MEMBERS:
                raise ValueError("member N 은 1..%d" % Settings.ENSEMBLE_MAX_MEMBERS)
            m = ens["members"][n - 1]
            for k, v in kvs(spec[1:]).items():
                if k == "weight":
                    m["weight"] = float(v)
                elif k == "enabled":
                    m["enabled"] = v.lower() in ("1", "true", "yes", "on")
                elif k in ("provider", "model", "effort"):
                    m[k] = v
                else:
                    raise ValueError("member 키는 provider|model|weight|effort|enabled: %s" % k)
        if ns.ens_aggregator:
            for k, v in kvs(ns.ens_aggregator).items():
                if k not in ("provider", "model", "effort"):
                    raise ValueError("aggregator 키는 provider|model|effort: %s" % k)
                ens["aggregator"][k] = v
        if ns.ens_wait is not None:
            if ns.ens_wait:
                ens["wait"] = ns.ens_wait
            else:
                ens.pop("wait", None)
        for attr, val in (("timeout_s", ns.ens_timeout), ("min_results", ns.ens_min)):
            if val is None:
                continue
            if str(val).strip() == "":
                ens.pop(attr, None)
            else:
                ens[attr] = int(float(val))
    except ValueError as e:
        print("ERROR:", e)
        return 1
    p.s.llm_roles.setdefault(role, {})["ensemble"] = ens
    _save(p.s)
    p.reload()
    eff = p.s.effective_ensemble(role)
    _out({"role": role, "raw": ens, "effective": eff}, as_json,
         "saved llm_roles.%s.ensemble: enabled=%s members=%s aggregator=%s wait=%s timeout_s=%s min_results=%s" % (
             role, eff["enabled"], ["%s/%s×%s" % (m["provider"], m["model"], m["weight"]) for m in eff["members"]],
             (eff["aggregator"].get("model") or "(첫 멤버)"), eff["wait"], eff["timeout_s"], eff["min_results"]))
    return 0


def _rules_explain_text(r: Dict[str, Any]) -> str:
    """rules explain 을 사람이 읽는 표로 (유형 · 방향 · 대표어 · 값 · 적용 방식)."""
    lines = ["'%s' 은(는) 어떻게 퍼지나 — %s  (related_symmetric=%s)" % (r["term"], r["path"], "true" if r["related_symmetric"] else "false"), ""]
    if not r["entries"]:
        lines.append("  이 말로 발화하는 규칙 없음 (사전에 키/양방향 값으로 없다)")
    else:
        lines.append("  %-9s %-22s %-24s %-40s %s" % ("유형", "방향", "대표어", "값", "적용 방식"))
        for e in r["entries"]:
            lines.append("  %-9s %-22s %-24s %-40s %s" % (e["type"], e["direction"] + (" ←값" if e.get("reverse") else ""), str(e["canonical"])[:24],
                                                          ", ".join(e["values"])[:40], e["how"]))
    if r["expanded_from"]:
        lines += ["", "  이 말을 끌어오는 규칙 (값으로 적힌 곳):"]
        for x in r["expanded_from"]:
            lines.append("  %-9s %-22s %-24s %s" % (x["type"], x["direction"], str(x["key"])[:24], x["note"]))
    lines += ["", "  " + r["note"]]
    return "\n".join(lines)


def _mcp_doctor_text(rep: Dict[str, Any]) -> str:
    """mcp --doctor 를 사람이 읽는 표로."""
    mark = {"ok": "  OK ", "warn": "WARN ", "error": " !!  "}
    lines = ["MCP 자가 점검 — 프로토콜 %s (지원 %s)" % (rep["protocol"], ", ".join(rep["supported_protocols"])), ""]
    for c in rep["checks"]:
        lines.append("%s %-18s %s" % (mark[c["level"]], c["check"], c["detail"]))
        if c["level"] != "ok" and c.get("hint"):
            lines.append("%s%s→ %s" % (" " * 5, " " * 19, c["hint"]))
    lines += ["", "도구 %d개: %s" % (len(rep["tools"]), ", ".join(rep["tools"])), ""]
    lines.append("결과: %s (오류 %d · 경고 %d)" % ("정상" if rep["ok"] else "문제 있음", rep["errors"], rep["warnings"]))
    if rep["ok"]:
        lines.append("클라이언트 설정: python -m llmwiki mcp --client-config   · 문서 docs/MCP.md")
    return "\n".join(lines)


def _replayed_names(trace: Dict[str, Any]) -> List[str]:
    """trace 에서 **재생된**(계산하지 않고 저장값을 쓴) 단계 이름 — 단계 재실행 결과 요약용."""
    out: List[str] = []

    def walk(n: Dict[str, Any]) -> None:
        if n.get("replayed"):
            out.append(str(n.get("name")))
        for c in n.get("children") or []:
            walk(c)

    for c in trace.get("children") or []:
        walk(c)
    return out


def _print_trace(trace: Dict[str, Any], depth: int = 0, total: Optional[float] = None, verbose: bool = False) -> None:
    total = total or trace.get("ms") or 1.0
    flag = "" if trace.get("enabled", True) else " (skipped: %s)" % trace.get("meta", {}).get("reason", "")
    if trace.get("replayed"):
        flag = " (재생 — 저장된 결과를 그대로 씀)"
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
    # **앙상블은 JSON 한 줄에 묻히면 안 된다** — 화면과 같은 내용을 멤버별 줄로 편다 (2026-09-20).
    ens = meta.pop("ensemble", None)
    if ens and isinstance(ens, dict) and ens.get("members"):
        pad = "  " * depth
        print("%s  ⑂ 앙상블 — 멤버 %d/%d 성공%s%s"
              % (pad, ens.get("n_ok", 0), ens.get("n_members", 0),
                 " · 취합 1회" if ens.get("aggregated") else " · 취합 없음(성공 1개)",
                 (" · 대기 %s" % (ens.get("policy") or {}).get("wait")) if (ens.get("policy") or {}).get("wait") else ""))
        for i, m in enumerate(ens["members"], 1):
            print("%s     멤버%d %-22s %8.0f ms  tok %d/%d%s"
                  % (pad, i, "%s%s" % (m.get("model") or "-", ("/" + m["provider"]) if m.get("provider") else ""),
                     m.get("ms") or 0, m.get("input_tokens") or 0, m.get("output_tokens") or 0,
                     ("  !! " + str(m.get("error"))[:80]) if not m.get("ok") else ""))
        ag = ens.get("aggregator")
        if ag:
            print("%s     취합  %-22s %8.0f ms  tok %d/%d"
                  % (pad, "%s%s" % (ag.get("model") or "-", ("/" + ag["provider"]) if ag.get("provider") else ""),
                     ag.get("ms") or 0, ag.get("input_tokens") or 0, ag.get("output_tokens") or 0))
        print("%s     (멤버는 동시에 실행 — 단계 시간 ≈ 가장 느린 멤버 + 취합)" % pad)
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


def run(argv: Optional[List[str]] = None, settings: Optional[Settings] = None, pipe=None, gate: bool = True) -> int:
    """gate=False: Web 콘솔처럼 호출자가 이미 권한을 판정한 경우 (run_captured)."""
    ap = build_parser()
    ns = ap.parse_args(argv)
    if not ns.cmd:
        ap.print_help()
        return 0
    if gate:
        code = _cli_gate(list(argv if argv is not None else sys.argv[1:]), ns)
        if code is not None:
            return code
    if getattr(ns, "log_level", None):
        os.environ["LLMWIKI_LOG_LEVEL"] = str(ns.log_level).upper()   # load_settings 가 env 를 읽는다 (Pipeline 이 logs/ 핸들러 레벨을 맞춤)
    as_json = getattr(ns, "json", False)
    ov = _overrides_from_ns(ns)
    from .pipeline import Pipeline
    if pipe is not None:
        # Web 콘솔/스케줄러: 공유 파이프라인을 요청 범위(설정 사본 + 프리셋 오버레이 + 스레드 전용 DB 연결)로 감싼다 — 전역 설정을 건드리지 않음
        from . import presets as _presets
        names = _presets.parse_names(getattr(ns, "preset", None)) if getattr(ns, "preset", None) else []
        with pipe.request_scope(overrides=ov or None, presets=names):
            return _run_cmd(ns, pipe.s, pipe, as_json)
    s = settings or load_settings()
    s = apply_overrides(s.copy() if settings else s, {k: v for k, v in ov.items() if k != "tuning"})
    p = Pipeline(s)
    if ov.get("tuning"):
        # --tuning k=v: 단일 실행이므로 전역 T 에 바로 얹는다 (파일에는 저장하지 않는다). 모르는 키·범위 밖 값은 여기서 바로 알린다.
        from . import tuning as _tn
        try:
            for k_, v_ in ov["tuning"].items():
                _tn.T.set(k_, v_)
        except (KeyError, ValueError) as e:
            print("!! --tuning: %s" % e)
            return 2
    if ns.cmd in ("query", "build", "eval", "trial", "precompute", "forensic", "schedule", "fusion"):
        try:
            from . import reqmgr as _rq
            _rq.install_cli_publisher()   # data/live 에 진행 상황 발행 → 실행 중인 서버의 모니터에서 보이고 취소할 수 있다
        except Exception:
            pass
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
    except _pg.Cancelled as e:
        print("cancelled: %s" % e)
        return 130
    except KeyboardInterrupt:
        print("\ncancelled (Ctrl+C) — 지금까지의 진행(체크포인트)은 보존됩니다; 빌드는 다음 build 가 이어서 합니다")
        return 130
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

    if ns.cmd == "build" and ns.action in ("fts", "vector", "graph"):
        from .buildlock import BuildLockedError
        if not _confirm_destructive(ns, "채널 리빌드: %s%s" % (ns.action, " (--full: 전부 다시)" if ns.full else ""),
                                    {"fts": "chunks_fts 행을 전부 다시 씁니다 (임베딩·그래프 불변)", "vector": "임베딩이 없는 청크를 임베딩합니다 (--full 이면 전부; FTS·그래프 불변)",
                                     "graph": "엔티티/관계/멘션/커뮤니티/위키 페이지를 비우고 전체 청크에서 다시 만듭니다 (FTS·임베딩 불변)"}[ns.action]):
            _out({"error": "cancelled"}, as_json, "build %s cancelled" % ns.action)
            return 4
        try:
            with _pg.cli_monitor("cli-build-%s-%d" % (ns.action, int(time.time())), "build", "build %s" % ns.action, enabled=not as_json and not _CAPTURED):
                res, tr = p.build_channel(ns.action, full=ns.full, progress=lambda m: print("  ·", m) if not as_json else None, force=ns.force)
        except BuildLockedError as e:
            _out({"error": str(e), "holder": e.holder}, as_json, "build refused: %s" % e)
            return 2
        except (RuntimeError, ValueError) as e:
            _out({"error": str(e)}, as_json, "build %s aborted: %s" % (ns.action, e))
            return 3
        if ns.trace:
            _print_trace(tr, verbose=(ns.debug_level or 0) >= 2)
        _out({"result": res, "trace": tr if ns.trace else None}, as_json,
             "build %s done: before=%s after=%s verify=%s build_version=%s%s" % (ns.action, json.dumps(res.get("counts_before")), json.dumps(res.get("counts_after")),
                                                                              (res.get("verify") or {}).get("ok"), res.get("build_version"),
                                                                              ("\nALERTS: " + json.dumps(res["alerts"], ensure_ascii=False)) if res.get("alerts") else ""))
        return 0

    if ns.cmd == "build":
        from .buildlock import BuildLockedError
        channels = [x.strip() for x in (ns.channels or "").split(",") if x.strip()] or None
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
                res, tr = p.build(full=ns.full, progress=lambda m: print("  ·", m) if not as_json else None, force=ns.force, channels=channels)
        except BuildLockedError as e:
            _out({"error": str(e), "holder": e.holder}, as_json, "build refused: %s" % e)
            return 2
        except ValueError as e:
            _out({"error": str(e)}, as_json, "build aborted: %s" % e)
            return 1
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
            scoped = p.in_request_scope()      # Web 콘솔/스케줄러: 요청 범위라 --save 없이는 이 요청에만 적용되고 사라진다 (다른 사용자에게 영향 없음)
            if ns.save:
                p.reload()                     # apply(save=True) 가 config.json·tuning.json 을 이미 썼다 → 전역으로 승격
            _out({k: v for k, v in r.items() if k != "prev"} | {"scope": "saved" if ns.save else ("request" if scoped else "process")}, as_json,
                 "applied %s: toggles=%d tuning=%d settings=%d conflicts=%d%s%s" % (
                     ns.names, len(r["toggles"]), len(r["tuning"]), len(r["settings"]), len(r["conflicts"]),
                     " (config.json/tuning.json 에 저장됨)" if ns.save else (" (이번 요청에만 적용 — 서버 기본값을 바꾸려면 --save)" if scoped else " (이번 프로세스만; --save 로 저장)"),
                     ("\nunknown: %s" % r["unknown"]) if r["unknown"] else ""))
            return 0

    if ns.cmd == "logs":
        from . import logging_setup as _ls
        from .config import path_for
        d = _ls.log_dir() or path_for("logs_dir")
        if ns.action == "dir":
            print(d)
            return 0
        if ns.action == "status":   # 총량 제한: 지금 다시 재고(action 도 적용) 상태를 보여준다
            q = _ls.check_quota(force=True, dir_hint=d)
            _out(q, as_json, _ls.format_quota(q))
            return 0
        if ns.action == "files":
            _out(_ls.files(d), as_json, "\n".join("%-14s %10d B  %s" % (f["file"], f["bytes"], __import__("time").strftime("%m-%d %H:%M:%S", __import__("time").localtime(f["mtime"]))) for f in _ls.files(d)))
            return 0
        path = __import__("os").path.join(d, ns.file + ".log")
        run_id = ns.run
        if ns.request is not None:
            try:
                _rid = int(ns.request)
            except (TypeError, ValueError):
                print("ERROR: --request 는 요청 번호(정수)여야 합니다 (받은 값: %s)" % ns.request)
                return 1
            r = p.store.get_request(_rid)
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
            rows = [_mcp.source_summary(k, v) for k, v in srcs.items()]
            _out(rows, as_json, "\n".join("%-10s enabled=%-5s %-5s %s\n%11s target=%s ingest=%d enrich=%d retrieve=%s expose=%s" % (
                r["name"], r["enabled"], r["transport"], r["desc"], "", r["target"], r["ingest"], r["enrich"],
                ",".join("%s(%s,w=%s)" % (x["tool"], x["when"], x["weight"]) for x in r["retrieve"]) or "-", r["expose"]) for r in rows)
                 + "\n\n토글 mcp_sources=%s external_rag=%s mcp_federation=%s  파일: %s" % (p.s.toggles.mcp_sources, p.s.toggles.external_rag, p.s.toggles.mcp_federation, _mcp.sources_path()))
            return 0
        if ns.action == "test":
            _out(_mcp.test_sources(p.s, ns.args or None), True)
            return 0
        if ns.action == "tools":
            if not ns.args:
                print("usage: mcp-source tools <name>")
                return 1
            cfg = _mcp.load_sources().get(ns.args[0])
            if not cfg:
                print("unknown source", ns.args[0])
                return 1
            _out(_mcp.remote_tools(ns.args[0], cfg), True)
            return 0
        if ns.action == "retrieve":
            rows = _mcp.retrieve(p.s, " ".join(ns.args), ns.k, names=[ns.source] if ns.source else None, include_fallback=True)
            _out(rows, as_json, "\n".join(("- [%s] 오류: %s" % (r["source"], r["error"])) if r.get("error") else
                                           ("- [%s] %-12s %.3f %s | %s" % (r["source"], r["id"], r["score"], r["title"][:50], (r["text"] or "")[:100].replace("\n", " "))) for r in rows)
                 + ("\n(결과 없음 — retrieve 매핑이 있는 enabled 소스가 없거나 결과 0건)" if not rows else ""))
            return 0
        if ns.action == "federated":
            from . import mcp as _m
            tools = _m.federated_tools(p.s, refresh=True)
            _out({"mcp_federation": p.s.toggles.mcp_federation, "tools": [t["name"] for t in tools], "errors": {k: v.get("error") for k, v in _m._FED_CACHE.items() if v.get("error")},
                  "plugins": _m.load_plugins(p.s)}, True)
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
            with _mcp.open_source(ns.args[0], cfg) as c:
                _out(c.call_tool(ns.args[1], args), True)
            return 0

    if ns.cmd == "reset":
        # 관리자 초기화 — 다른 환경으로 옮길 때 앞 환경의 흔적을 지운다.
        # 기본은 **미리보기**다. 지우는 명령이 기본으로 지워 버리면 안 되기 때문이다 (--apply 가 있어야 실행).
        from . import reset as _rs
        if not ns.scope:
            _out({"scopes": _rs.SCOPES}, as_json,
                 "초기화 범위 3가지 — `reset <범위>` 로 미리보기, `--apply` 로 실행\n\n"
                 + "\n".join("  %-9s %s" % (k, v) for k, v in _rs.SCOPES.items())
                 + "\n\n자세히: docs/BRINGUP_GUIDE.md · Web 설정 › 관리 › 초기화 · 되돌리기는 `snapshot list|restore`")
            return 0
        opts = {"snapshot": not ns.no_snapshot, "keep_wiki_notes": not ns.purge_wiki_notes,
                "clear_embed_cache": bool(ns.clear_embed_cache),
                "include_security": bool(ns.include_security), "include_env": bool(ns.include_env),
                "include_proposals": bool(ns.include_proposals), "include_sessions": bool(ns.include_sessions)}
        plan = _rs.preview(p, ns.scope, **opts)
        if plan.get("error"):
            print("ERROR: " + plan["error"])
            return 1
        if not ns.apply:
            _out(plan, as_json, _rs.format_preview(plan)
                 + "\n\n(미리보기만 했습니다 — 실제로 지우려면 --apply 를 붙이세요)")
            return 0
        print(_rs.format_preview(plan))
        print()
        warn = [i["detail"] for i in plan["items"] if i["level"] == "warn"]
        if not _confirm_destructive(ns, "초기화 '%s' — %d행 · 파일 %d개" % (ns.scope, plan["total_rows"], plan["total_files"]),
                                    " / ".join(warn)[:200] or plan["description"][:200]):
            print("cancelled")
            return 4
        r = _rs.run(p, ns.scope, actor="cli", **opts)
        _out(r, as_json, "초기화 완료 (%s · %.0fms)\n\n다음에 할 일\n%s"
             % (ns.scope, r["ms"], "\n".join("  %d. %s" % (i + 1, x) for i, x in enumerate(r["next"]))))
        return 0

    if ns.cmd == "graph-rules":
        # 그래프 빌드 규칙 (data/rules.json). 질의 확장 규칙(query_rules.json)의 `rules` 와 짝이다 —
        # 예전에는 이쪽에 `rules merge --graph` 밖에 없어서, 보기·점검·시험을 Web 원문 textarea 로만 할 수 있었다.
        from . import graph_rules as _gr
        a = ns.action
        if a == "path":
            print(_gr.rules_path())
            return 0
        if a == "show":
            _out(_gr.load_rules(), True)
            return 0
        if a == "types":
            r = _gr.load_rules()
            types, vts = _gr.known_types(r), _gr.describe_value_types()
            schema = _gr.Schema(r.get("schema"), types)
            out = {"entity_types": types, "value_types": vts,
                   "relations": {k: v for k, v in schema.relations.items()}, "on_unknown": schema.on_unknown}
            _out(out, as_json,
                 "엔티티 type %d종 (types_for_cooccur · id_patterns · schema.entity_types 의 합집합)\n  %s\n\n"
                 "값 종류 %d개 — relation_patterns[*].value · chunk_values[*].value 에 쓴다\n%s\n\n"
                 "관계 어휘 %d개 (schema.relations · 모르는 관계 정책 on_unknown=%s)\n%s" % (
                     len(types), ", ".join(types), len(vts),
                     "\n".join("  %-9s %-10s %s" % (v["name"], v["label"], v["desc"][:74]) for v in vts),
                     len(schema.relations), schema.on_unknown,
                     "\n".join("  %-16s %s%s" % (
                         k, (v.get("desc") or "")[:56],
                         ("  ↔ %s" % v["inverse"]) if v.get("inverse") else ("  (대칭)" if v.get("symmetric") else ""))
                         for k, v in list(schema.relations.items())[:40]) or "  (스키마 절이 없습니다 — `graph-rules fill-defaults` 로 기본 어휘를 채우세요)"))
            return 0
        if a == "lint":
            r = _gr.lint()
            if as_json:
                _out(r, True)
                return 0
            c = r["counts"]
            print("그래프 규칙 점검 — 엔티티 %d · 별칭 %d · type %d · 관계패턴 %d · chunk_values %d · id패턴 %d · link규칙 %d · 관계어휘 %d"
                  % (c["entities"], c["aliases"], c["types"], c["relation_patterns"], c["chunk_values"],
                     c["id_patterns"], c["link_rules"], c["schema_relations"]))
            for i in r["issues"]:
                print("  [%s] %-28s %s" % ({"error": "X", "warn": "!", "info": "i"}.get(i["level"], "?"), i["where"], i["detail"]))
                if i["fix"]:
                    print("        → %s" % i["fix"])
            print("  오류 %d · 경고 %d%s" % (c["errors"], c["warns"], "  (모든 점검 통과)" if not r["issues"] else ""))
            print("  ※ 이것은 **파일만** 보는 정적 점검입니다. 빌드된 그래프의 진단은 `graph-prof` 입니다.")
            return 0 if c["errors"] == 0 else 1
        if a == "test":
            if not ns.args:
                print('사용법: graph-rules test "<문장>" [--doc-type cl] [--ext-id CL-55302]')
                return 1
            text = " ".join(ns.args)
            ex = _gr.RuleExtractor(_gr.load_rules())
            dm = {"doc_type": ns.doc_type, "ext_id": ns.ext_id} if (ns.doc_type or ns.ext_id) else None
            ents, counts, rels = ex.extract_chunk(text, "", "test", "테스트 문서", dm)
            out = {"entities": [{"name": e.name, "type": e.type, "mentions": counts.get(k, 0)} for k, e in ents.items()],
                   "relations": [{"src": ents[r.src].name if r.src in ents else r.src,
                                  "rel": r.rel, "dst": ents[r.dst].name if r.dst in ents else r.dst,
                                  "provenance": r.provenance, "weight": r.weight} for r in rels],
                   "unknown_rels": ex.schema.unknown_rels, "unknown_types": ex.schema.unknown_types}
            _out(out, as_json,
                 "엔티티 %d개\n%s\n\n관계 %d개\n%s%s" % (
                     len(out["entities"]),
                     "\n".join("  %-14s %-12s 멘션 %d" % (e["name"][:14], e["type"], e["mentions"]) for e in out["entities"]) or "  (없음)",
                     len(out["relations"]),
                     "\n".join("  %-14s -[%s]-> %-14s (%s w=%.2f)" % (x["src"][:14], x["rel"], x["dst"][:14], x["provenance"], x["weight"])
                               for x in out["relations"]) or "  (없음)",
                     ("\n\n어휘 밖: 관계 %s · 타입 %s" % (out["unknown_rels"], out["unknown_types"]))
                     if (out["unknown_rels"] or out["unknown_types"]) else ""))
            return 0
        if a == "add-entity":
            if len(ns.args) < 2:
                print("사용법: graph-rules add-entity <이름> <type> [별칭…]   (type 목록은 `graph-rules types`)")
                return 1
            name, etype, aliases = ns.args[0].strip(), ns.args[1].strip(), [x.strip() for x in ns.args[2:] if x.strip()]
            r = _gr.load_rules()
            types = _gr.known_types(r)
            if types and etype not in types:
                print("ERROR: 없는 type '%s' — 쓸 수 있는 값: %s" % (etype, ", ".join(types)))
                return 1
            ents = r.setdefault("entities", {})
            if name in ents:                                  # type: ignore
                print("이미 있습니다: %s — 별칭만 더하려면 `graph-rules add-alias %s <별칭…>`" % (name, name))
                return 1
            ents[name] = {"type": etype, "aliases": aliases}   # type: ignore
            _gr.save_rules(r)
            _out({"name": name, "type": etype, "aliases": aliases}, as_json,
                 "엔티티 추가: %s (%s)%s — 반영하려면 `build graph`" % (name, etype, (" 별칭 %d개" % len(aliases)) if aliases else ""))
            return 0
        if a == "add-alias":
            if len(ns.args) < 2:
                print("사용법: graph-rules add-alias <엔티티 이름> <별칭…>")
                return 1
            name, aliases = ns.args[0].strip(), [x.strip() for x in ns.args[1:] if x.strip()]
            r = _gr.load_rules()
            ents = r.get("entities") or {}                     # type: ignore
            key = next((k for k in ents if k.strip().lower() == name.lower()), None)
            if key is None:
                print("ERROR: 규칙 사전에 '%s' 가 없습니다 — `graph-rules add-entity %s <type>` 로 먼저 만드세요" % (name, name))
                return 1
            cur = list(ents[key].get("aliases") or [])
            added = [a2 for a2 in aliases if a2 not in cur and a2.lower() != key.lower()]
            ents[key]["aliases"] = cur + added
            _gr.save_rules(r)
            _out({"entity": key, "added": added, "aliases": ents[key]["aliases"]}, as_json,
                 "별칭 %d개 추가: %s → %s — 반영하려면 `build graph`" % (len(added), key, ", ".join(added) or "(없음 — 이미 있었습니다)"))
            return 0
        if a == "fill-defaults":
            r = _gr.fill_defaults(dry_run=ns.dry_run)
            _out(r, as_json, "%s: 채운 절 %s" % (r.get("path"), ", ".join(r.get("added") or []) or "(없음 — 이미 모두 있습니다)"))
            return 0
        return 1

    if ns.cmd == "rules":
        from . import query_rules as _qr
        if ns.action == "show":
            _out(_qr.load_rules(), True)
            return 0
        if ns.action == "types":
            # 규칙 유형 표 — 무엇을 어느 유형에 넣어야 하는지가 이 화면 하나로 정해진다.
            rows = _qr.describe_types()
            _out(rows, as_json, "규칙 유형 %d개 (query_rules.json 의 절 이름)\n\n%s\n\n%s" % (
                len(rows),
                "\n".join("  %-9s %-16s %-6s 항목 %-4d %s%s" % (
                    r["name"], r["label"], r["direction"], r["count"],
                    r["how"][:72], ("  [신규 %s]" % r["since"]) if r["since"] else "") for r in rows),
                "값 모양: list=문자열 목록 · str=문자열 하나 · cond=[{when,then}] 조건 목록 · map=객체\n"
                "추가: rules add <type> <term> <값…>   설명: rules explain <용어>   점검: rules lint"))
            return 0
        if ns.action == "stats":
            _out(_qr.stats(), True)
            return 0
        if ns.action == "effect":
            # 규칙별 누적 효과: 걸린 횟수 · 후보를 가져온 횟수 · 최종 컨텍스트에 기여한 횟수 · 인용된 횟수
            from . import ruleeffect as _re
            if getattr(ns, "reset", False):
                _out(_re.reset(p.store, ns.args[0] if ns.args else None), True)
                return 0
            r = _re.stats(p.store, 200, getattr(ns, "order", "fired"))
            if as_json:
                _out(r, True)
                return 0
            print("규칙 효과 — 규칙 %d개 기록 · 총 발화 %d회 · 정렬 %s" % (r["n"], r["total_fired"], r["order"]))
            print("  %s" % r["note"])
            if r["never_helped"]:
                print("  ⚠ 3회 이상 걸렸는데 한 번도 기여하지 못한 규칙 %d개 — `rules effect --order useless` 로 확인" % r["never_helped"])
            print("  %-26s %-9s %6s %6s %6s %6s %7s" % ("규칙", "유형", "발화", "후보", "기여", "인용", "기여율"))
            for x in r["rows"]:
                print("  %-26s %-9s %6d %6d %6d %6d %6.0f%%" % (x["term"][:26], x["type"] or "-", x["fired"], x["cand"], x["helped"], x["cited"], 100 * x["help_rate"]))
            if not r["rows"]:
                print("  (아직 기록이 없습니다 — 질의를 몇 번 돌리면 쌓입니다)")
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
        if ns.action == "explain":
            # 빈 문자열을 **준** 경우(`rules explain ""`)도 용어가 없는 것이다. 예전에는 `ns.args` 가
            # `[""]` 라 목록이 비어 있지 않다는 이유로 통과해 종료코드 0 과 "없는 용어" 결과를 냈다 —
            # Web 은 400, MCP 는 오류를 내므로 같은 입력에 창구마다 다르게 굴었다 (2026-09-20 정렬 감사).
            if not " ".join(ns.args or []).strip():
                print("usage: rules explain <용어>   (예: rules explain AGC)")
                return 1
            r = _qr.explain(" ".join(ns.args))
            _out(r, as_json, _rules_explain_text(r))
            return 0
        if ns.action == "merge":
            if not ns.args:
                print("usage: rules merge <파일.json> [--graph] [--replace]   (예시: setup/query_rules.example.modem.json)")
                return 1
            src = ns.args[0]
            if not os.path.exists(src):
                print("파일이 없습니다: %s" % src)
                return 1
            with open(src, encoding="utf-8") as f:
                incoming = json.load(f)
            if ns.graph:
                # 그래프 규칙(data/rules.json): 절 단위로 덮어쓴다 (entities 는 항목 단위로 합친다)
                from . import graph_rules as _gr
                cur = _gr.load_rules()
                changed = {}
                for k, v in incoming.items():
                    if k.startswith("_"):
                        continue
                    if k == "entities" and isinstance(v, dict) and isinstance(cur.get(k), dict) and not ns.replace:
                        n0 = len(cur[k])
                        cur[k].update({ek: ev for ek, ev in v.items() if not str(ek).startswith("_")})
                        changed[k] = "%d → %d" % (n0, len(cur[k]))
                    else:
                        changed[k] = "덮어씀 (%s개)" % (len(v) if isinstance(v, (list, dict)) else 1)
                        cur[k] = v
                _gr.save_rules(cur)
                _out({"merged": changed, "path": _gr.rules_path()}, as_json,
                     "그래프 규칙 합침 — %s\n" % _gr.rules_path() + "\n".join("  %-20s %s" % (k, v) for k, v in changed.items())
                     + "\n\n반영하려면: python -m llmwiki build graph")
                return 0
            r = _qr.merge_rules(incoming, replace=ns.replace)
            p.reload_tuning()
            lines = ["질의 확장 사전 합침 — %s%s" % (r["path"], " (덮어쓰기)" if r["replace"] else "")]
            for typ, st in r["merged"].items():
                lines.append("  %-9s 새 용어 %-3d · 값 늘어난 용어 %-3d · 총 %d" % (typ, st["added"], st["grown"], st["total"]))
            lines.append("\n점검: python -m llmwiki rules lint")
            _out(r, as_json, "\n".join(lines))
            return 0
        if ns.action == "lint":
            r = _qr.lint()
            lines = ["규칙 사전 점검 — %s" % r["path"],
                     "  항목: " + " · ".join("%s %d" % (k, v) for k, v in r["stats"].items()) + "   (query_rules_max_rounds=%d)" % r["max_rounds"], ""]
            for i in r["issues"]:
                lines.append("  %-5s %-11s %-24s %s" % ("오류" if i["level"] == "error" else "경고", i["kind"], i["term"][:24], i["detail"]))
            if not r["issues"]:
                lines.append("  문제 없음")
            lines += ["", "결과: %s (오류 %d · 경고 %d)" % ("정상" if r["ok"] else "고칠 것이 있습니다", r["errors"], r["warnings"])]
            _out(r, as_json, "\n".join(lines))
            return 0 if r["ok"] else 1

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
        if ns.action == "check":
            bad = _pc.find_broken(p.store)
            _out({"broken": bad, "n": len(bad)}, as_json,
                 ("고장난 답변 %d개 (같은 구절 반복) — `precompute clear --broken` 으로 지우세요:\n" % len(bad)
                  + "\n".join("  · %s  (%d번 반복) %s" % ((b.get("query") or "")[:50], b.get("times", 0), (b.get("phrase") or "")[:50]) for b in bad))
                 if bad else "고장난 답변 없음 (캐시 정상)")
            return 0
        if ns.action == "clear":
            _out({"removed": _pc.clear_cache(p.store, stale_only=ns.stale, broken_only=ns.broken)}, True)
            return 0
        if ns.action == "doc-vectors":
            _out(_pc.build_doc_vectors(p), True)
            return 0
        _out(_pc.cache_status(p.store), True)
        return 0

    if ns.cmd == "forensic":
        from . import forensic as _fx
        tgt = ns.target
        if tgt == "expect":
            ref = ns.args[0] if ns.args else "last"
            if ref == "last":
                reqs = p.store.requests("query", 1)
                if not reqs:
                    print("no query request")
                    return 1
                rid = int(reqs[0]["id"])
            else:
                try:
                    rid = int(ref)
                except ValueError:
                    print("usage: forensic expect <request_id|last> --doc ISSUE-2003 --term 0x40 [--chunk id] [--note …] [--propose]")
                    return 1
            if not (ns.docs or ns.terms or ns.chunks):
                print("기대 결과를 하나 이상 주세요: --doc <ext_id|doc_id 부분> · --term <용어> · --chunk <chunk_id>")
                return 1
            with _pg.cli_monitor("cli-fx-%d" % int(time.time()), "query", "forensic expect #%d" % rid, enabled=not as_json and not _CAPTURED):
                rep = _fx.trace_expectation(p, rid, ns.docs, ns.terms, ns.chunks, note=ns.note, propose=ns.propose)
            _out(rep, as_json, _fx.format_expectation(rep))
            return 0 if not rep.get("error") else 1
        if tgt == "summary":
            _out(_fx.summary(p.store), True)
            return 0
        if tgt == "list":
            # 기본은 **문제 건만** — Web 포렌식 화면과 같은 기준(`only=problems`).
            # 이 저장소 실측으로 기록의 90%가 정상이라, 전부 보여 주면 볼 이유가 있는 줄이 묻힌다.
            only = (getattr(ns, "only", "") or "problems")
            rows = _fx.list_forensics(p.store, ns.limit,
                                      verdict=(None if only in ("problems", "all", "") else only),
                                      q=(getattr(ns, "q", "") or ""),
                                      only_problems=(only == "problems"))
            cnt = _fx.verdict_counts(p.store)
            head = ("판정별 전체: %s   (지금 보기: %s)"
                    % (" · ".join("%s %d" % (k, v) for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])),
                       "문제 건만" if only == "problems" else ("전부" if only == "all" else only)))
            body = "\n".join("#%-4s req=%-5s %-12s g=%-5s %s" % (r["id"], r["request_id"], r["verdict"], r["groundedness"], r["query"][:60]) for r in rows)
            _out({"rows": rows, "counts": cnt, "only": only}, as_json,
                 head + "\n" + (body or "(해당 없음 — `--only all` 로 전부 보거나 `--only sufficient` 로 정상 건을 봅니다)"))
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
            rows = _mem.episodes(p.store, ns.limit, ns.only or "", ns.q or "")
            _out(rows, as_json, "\n".join(
                "#%-5s %s  %-9s %-10s %s  str=%.2f  %s" % (
                    e["id"], time.strftime("%m-%d %H:%M", time.localtime(e.get("ts") or 0)),
                    (e.get("kind") or "")[:9], (e.get("outcome") or "")[:10],
                    "👍" if (e.get("feedback") or 0) > 0 else ("👎" if (e.get("feedback") or 0) < 0 else "  "),
                    float(e.get("strength") or 0), (e.get("query") or "")[:60]) for e in rows)
                or "(에피소드 없음 — 질의에 👍/👎 를 누르거나 토글 evolve_capture 를 켜면 쌓입니다)")
            return 0
        if ns.action == "boosts":
            # 지금 검색이 실제로 받고 있는 피드백 부스트 (Web 메모리 화면의 같은 표)
            rows = _mem.boost_table(p.store, hl, top=ns.limit)
            _out(rows, as_json, "피드백 부스트 %d건 (반감기 %s일 · fusion 의 post-boost 로 들어갑니다)\n%s" % (
                len(rows), hl,
                "\n".join("  %+.3f  %-46s %s%s" % (
                    e["weight"], e["chunk_id"][:46], (e.get("heading") or "")[:34],
                    "" if e.get("exists") else "  [색인에 없음 — 재빌드로 사라진 청크]") for e in rows)
                or "  (없음 — 아직 👍/👎 가 없습니다)"))
            return 0
        if ns.action == "decaying":
            rows = _mem.decaying_proposals(p.store, hl, p.tuning.get("memory_archive_strength"), ns.limit)
            _out(rows, as_json, "감쇠 중인 미승인 제안 %d건 (임계 %s 아래로 내려가면 archived)\n%s" % (
                len(rows), p.tuning.get("memory_archive_strength"),
                "\n".join("  #%-5s %-12s str=%.3f  %s  %s" % (
                    e["id"], (e.get("kind") or "")[:12], e["strength_now"],
                    ("남은 %s일" % e["days_left"]) if e.get("days_left") is not None else "감쇠 없음",
                    "⚠ 곧 사라짐" if e.get("at_risk") else "") for e in rows)
                or "  (없음)"))
            return 0
        _out(_mem.status(p.store, hl), True)
        return 0

    if ns.cmd == "trial":
        from . import trials as _tr
        if ns.action == "candidates":
            # **비교에 쓸 과거 질의 고르기** — 번호를 보고 `trial run --pick 12,18,25` 로 넘긴다.
            # Web Quality › Trial 비교의 '질의 고르기' 목록과 같은 함수(GET /api/eval/candidates).
            from .evalset import from_query_log
            got = from_query_log(p.store, days=ns.days, limit=ns.limit, only=ns.only)
            rows = got["questions"]
            txt = "\n".join(
                "#%-5s %-16s %-4s %-5s %s" % (
                    c.get("from_query_id"),
                    __import__("time").strftime("%m-%d %H:%M", __import__("time").localtime(c.get("ts") or 0)),
                    ("👎" if (c.get("feedback") or 0) < 0 else ("👍" if (c.get("feedback") or 0) > 0 else "")),
                    ("근거X" if c.get("insufficient") else ""),
                    (c.get("q") or "")[:60]) for c in rows)
            _out({"candidates": rows, "source": got["source"]}, as_json,
                 (txt + "\n\n고른 번호로 돌리기:  trial run --pick %s --name 내비교"
                  % ",".join(str(c.get("from_query_id")) for c in rows[:3]))
                 if rows else "최근 %g일 안에 질의 이력이 없습니다 (only=%s)" % (ns.days, ns.only or "all"))
            return 0
        if ns.action == "run":
            from .evalset import load_questions, from_query_log, pick_questions
            src = None
            picked = [x.strip() for x in str(getattr(ns, "pick", "") or "").split(",") if x.strip()]
            if picked:
                # 사람이 고른 질의만 — 기간·건수로 뭉뚱그리는 것과 달리 "이 질문들" 을 그대로 쓴다
                got = pick_questions(p.store, picked)
                qs, src = got["questions"], got["source"]
                if not qs:
                    print("ERROR: 고른 번호에 해당하는 질의가 없습니다 — `trial candidates` 로 번호를 확인하세요")
                    return 1
                if src.get("missing"):
                    print("주의: 찾지 못한 번호 %s" % src["missing"], file=sys.stderr if as_json else sys.stdout)
                print("고른 질의 %d문항으로 돌립니다 — 정답이 없어 hit@k·mrr·term_recall 은 계산하지 않습니다" % len(qs),
                      file=sys.stderr if as_json else sys.stdout)
            elif getattr(ns, "source", "queries") == "queries" and not ns.questions:
                # 실제 질의 이력으로 (기본) — 평가셋이 실제 사용과 다르고, 이 저장소에서는 평가셋이 코퍼스에
                # 색인돼 hit@k 가 오염돼 있다(`eval --check`). 대신 정답이 없어 일부 지표는 계산되지 않는다.
                got = from_query_log(p.store, days=ns.days, limit=ns.limit, only=ns.only)
                qs, src = got["questions"], got["source"]
                # --json 일 때 이 안내가 stdout 에 섞이면 JSON 파싱이 깨진다 → stderr 로 (사람은 그대로 본다)
                note_to = sys.stderr if as_json else sys.stdout
                if not qs:
                    # **막지 않고 평가셋으로 물러난다** — 기본값이 실행을 실패시키면 안 된다.
                    # 다만 어느 원천으로 돌았는지는 반드시 알린다 (조용히 바뀌면 숫자를 잘못 읽는다).
                    print("쓸 만한 질의 이력이 없어(최근 %g일%s) **평가셋으로** 돌립니다 — "
                          "이력으로 돌리려면 질의를 먼저 쌓거나 --days 를 늘리세요"
                          % (ns.days, (" · %s" % ns.only) if ns.only else ""), file=note_to)
                    qs, src = (load_questions(ns.questions) if ns.questions else None), None
                else:
                    print("문항 %d개를 실제 질의 이력에서 가져왔습니다 (최근 %g일%s) — "
                          "정답이 없어 hit@k·mrr·term_recall 은 계산하지 않습니다"
                          % (len(qs), ns.days, (" · %s" % ns.only) if ns.only else ""), file=note_to)
            else:
                qs = load_questions(ns.questions) if ns.questions else None
            ov: Dict[str, Any] = {}
            for kv in ns.sets:
                k_, _, v_ = kv.partition("=")
                ov[k_.strip()] = v_.strip()
            name = ns.name or ("trial-%s" % __import__("time").strftime("%m%d-%H%M%S"))
            r = _tr.run_trial(p, name, qs, k=ns.k, preset=ns.preset, overrides=ov or None, note=ns.note, source=src)
            _out(r, as_json, "trial #%s %s (%s, 문항 %d): %s"
                 % (r["trial_id"], r["name"], (r.get("source") or {}).get("kind", "evalset"), r["n"],
                    json.dumps({k: v for k, v in r["summary"].items() if not k.startswith("_")}, ensure_ascii=False)))
            return 0
        if ns.action == "list":
            rows = _tr.list_trials(p.store, 50)
            # 정답이 없어 **계산하지 않은** 지표는 `—` 로 찍는다. 0 으로 보이면 "완전 실패" 로 읽힌다.
            def _v(x):
                return "—" if x is None else str(x)
            SRCL = {"evalset": "평가셋", "queries": "질의이력", "list": "직접"}
            txt = "\n".join("#%-4s %-22s %-6s v%-3s n=%-3s hit=%-5s mrr=%-5s g=%-5s insuf=%-5s ms=%-7s %s" % (
                r["trial_id"], r["name"][:22], SRCL.get((r.get("source") or {}).get("kind"), "?"),
                r["build_version"], r["summary"].get("n"), _v(r["summary"].get("hit@k")), _v(r["summary"].get("mrr")),
                _v(r["summary"].get("groundedness")), _v(r["summary"].get("insufficient_rate")), _v(r["summary"].get("avg_ms")),
                __import__("time").strftime("%m-%d %H:%M", __import__("time").localtime(r["ts"]))) for r in rows)
            if any(not r.get("graded") for r in rows):
                txt += "\n\n— 표시는 **계산하지 않은 것**입니다(0점이 아닙니다). 질의 이력에는 채점할 정답이 없어\n" \
                       "   hit@k·mrr·term_recall 을 낼 수 없습니다 — groundedness·insufficient·ms·토큰으로 비교하세요."
            _out(rows, as_json, txt or "(no trials)")
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

    if ns.cmd == "analyze":
        from . import analysis as _an
        try:
            rid = None if ns.target in ("last", "", None) else int(ns.target)
        except (TypeError, ValueError):
            print("ERROR: analyze 의 대상은 요청 번호(정수) 또는 last 입니다 (받은 값: %s)" % ns.target)
            return 1
        r = _an.analyze(p, rid, focus=None if ns.focus == "all" else ns.focus)
        if r.get("error"):
            print("ERROR:", r["error"])
            return 1
        if ns.out:
            with open(ns.out, "w", encoding="utf-8") as f:
                f.write(r["markdown"])
        if as_json:
            _out({"summary": r["summary"], "paths": r["paths"], "report": r["report"]}, True)
            return 0
        if ns.print_md:
            print(r["markdown"])
            return 0
        sm = r["summary"]
        print("분석 리포트: %s  (json: %s)%s" % (r["paths"].get("md"), r["paths"].get("json"), ("  (+ %s)" % ns.out) if ns.out else ""))
        print("request #%s · %.0f ms (LLM %.0f ms) · 토큰 %s · 판정 %s · groundedness %s · 상세도 %s" % (
            sm["request_id"], sm.get("total_ms") or 0, sm.get("llm_ms") or 0, (sm.get("tokens") or {}).get("total_tokens"), sm.get("verdict"), sm.get("groundedness"), sm.get("detail_level")))
        for lens in ("quality", "speed", "tokens"):
            if ns.focus not in ("all", lens):
                continue
            for f in sm["top"].get(lens) or []:
                print("  [%s] %-7s %s%s" % (f["severity"], lens, f["title"], ("  → " + ", ".join(f["knobs"])) if f.get("knobs") else ""))
        print("전문: analyze %s --print   |  LLM 에게 넘기기: 위 .md 파일을 그대로 첨부 (docs/ANALYSIS_MODE.md)" % sm["request_id"])
        return 0

    if ns.cmd == "query":
        q = " ".join(ns.question)
        if getattr(ns, "analyze", False):
            p.s.toggles.analysis_mode = True
        try:
            ov = _request_overrides(ns)
        except (ValueError, KeyError) as e:
            print("!! --set: %s" % e)
            return 2
        except Exception as e:          # AuthError — 모르는 키(400) 또는 역할이 못 쓰는 키(403)
            print("!! --set: %s" % getattr(e, "error", e))
            return 2 if getattr(e, "status", 0) == 400 else 5
        with _pg.cli_monitor("cli-query-%d" % int(time.time()), "query", q[:80], enabled=not as_json and not _CAPTURED,
                             client=_cli_client(ns)):
            res, tr = p.query(q, log=not ns.no_log, overrides=ov or None)
        if getattr(ns, "analyze", False) and res.get("analysis") and ns.focus != "all" and res["analysis"].get("md"):
            from . import analysis as _an
            r2 = _an.analyze(p, res.get("request_id"), focus=ns.focus)
            if not r2.get("error"):
                res["analysis"] = dict(r2["summary"], **r2["paths"])
        if as_json:
            _out({"result": res, "trace": tr}, True)
            return 0
        print("Q:", q)
        print("route:", json.dumps(res.get("route", {}).get("kind")), "weights:", res["config"]["weights"])
        if res.get("output_mode") and res["output_mode"] != "answer":
            print("output_mode:", res["output_mode"], "result_type:", res.get("result_type"))
        print("-" * 70)
        print(res["answer"])       # output_mode=fused|reranked 면 후보 표(마크다운), context 면 컨텍스트 본문이 들어 있다
        print("-" * 70)
        if res.get("output_mode") == "context" and res.get("refs"):
            for x in res["refs"]:
                print("[C%s] %s | %s | %s chars" % (x.get("n"), x.get("chunk_id"), (x.get("heading") or "")[:50], x.get("chars")))
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
        an = res.get("analysis")
        if an:
            print("=" * 70)
            if an.get("error"):
                print("분석 리포트 생성 실패:", an["error"])
            else:
                print("📊 분석 리포트: %s" % an.get("md"))
                for lens in ("quality", "speed", "tokens"):
                    if ns.focus not in ("all", lens):
                        continue
                    for f in (an.get("top") or {}).get(lens) or []:
                        print("  [%s] %-7s %s%s" % (f["severity"], lens, f["title"], ("  → " + ", ".join(f["knobs"])) if f.get("knobs") else ""))
                if getattr(ns, "print_analysis", False) and an.get("md"):
                    print("=" * 70)
                    with open(an["md"], "r", encoding="utf-8") as f:
                        print(f.read())
        return 0

    if ns.cmd == "sweep":
        from . import sweep as _sw
        if ns.action == "keys":
            rows = _sw.sweepable_keys(p.s)
            _out({"keys": rows, "points": __import__("llmwiki.rerun", fromlist=["POINTS"]).POINTS}, as_json,
                 "\n".join("%-26s %-7s %-7s %-11s %s" % (r["key"], r["kind"], r["type"], r["point"],
                                                          ("choices=%s" % r["choices"]) if r.get("choices") else ("%s..%s" % (r.get("min"), r.get("max")) if r.get("min") is not None else ""))
                           for r in rows))
            return 0
        if ns.action == "list":
            rows = _sw.list_sweeps(p.s, ns.n)
            _out({"sweeps": rows}, as_json,
                 "\n".join("sw_%-22s %-22s %-11s #%-6s %2d값 %s%s" % (r["id"], str(r.get("key"))[:22], str(r.get("point")), r.get("request_id"),
                                                                     len(r.get("values") or []), time.strftime("%m-%d %H:%M", time.localtime(r["mtime"])),
                                                                     (" 오류 %d" % r["n_errors"]) if r.get("n_errors") else "")
                           for r in rows) or "저장된 스윕이 없습니다 (sweep run 으로 만든다)")
            return 0
        if ns.action in ("show", "compare"):
            rec = _sw.load(p.s, ns.target or "")
            if not rec:
                print("스윕을 찾을 수 없습니다: %s (sweep list 로 확인)" % ns.target)
                return 1
            cmp_ = _sw.compare(rec)
            if as_json:
                _out({"record": rec, "compare": cmp_} if ns.action == "show" else cmp_, True)
                return 0
            print(_sw.render_text(rec, cmp_))
            if ns.action == "compare":
                for row in cmp_.get("runs") or []:
                    if row.get("is_base") or row.get("error") or (row.get("answer") or {}).get("same"):
                        continue
                    print("\n=== %s=%s 답변 diff (유사도 %.2f) ===" % (rec["key"], row["value"], row["answer"]["ratio"]))
                    print("\n".join(row["answer"]["diff"]))
            return 0
        # run
        if not ns.key:
            print("사용법: sweep run <request_id|last> --key K (--range a:b:s | --values v1,v2,…) [--repeats N] [--from POINT] [--query \"…\"]")
            return 2
        try:
            spec = {"range": ns.range_} if ns.range_ else ({"values": ns.values} if ns.values else None)
            vals = _sw.resolve_values(ns.key, spec, int(getattr(p.s, "sweep_max_values", 20) or 20))
            rec = _sw.run(p, ns.target if ns.target else None, ns.key, vals, repeats=ns.repeats, from_point=ns.from_point, query=ns.query,
                          progress=(None if as_json else (lambda m: print("  " + m))), log=bool(ns.log))
        except ValueError as e:
            print("스윕할 수 없습니다: %s" % e)
            return 2
        cmp_ = _sw.compare(rec)
        if as_json:
            _out({"record": rec, "compare": cmp_}, True)
            return 0
        print()
        print(_sw.render_text(rec, cmp_))
        return 0 if rec.get("n_ok") else 1

    if ns.cmd == "rerun":
        from . import rerun as _rr
        if ns.points:
            _out({"points": _rr.POINTS}, as_json,
                 "\n".join("%-12s %s\n             %s" % (x["id"], x["label"], x["note"]) for x in _rr.POINTS))
            return 0
        if ns.list:
            rows = _rr.list_saved(p.s, ns.n)
            _out({"saved": rows}, as_json,
                 "\n".join("#%-8s %6.1f KB  %s" % (r["request_id"], r["bytes"] / 1024, time.strftime("%m-%d %H:%M", time.localtime(r["mtime"])))
                           for r in rows) or "저장된 중간 결과가 없습니다 (toggles.rerun_capture 확인)")
            return 0
        if not ns.request_id:
            print("사용법: rerun <request_id> --from <단계> · 단계 목록은 --points · 저장 목록은 --list")
            return 2
        try:
            res, tr = p.rerun(ns.request_id, ns.__dict__["from"], log=not ns.no_log)
        except ValueError as e:
            print("재실행할 수 없습니다: %s" % e)
            return 2
        if as_json:
            _out({"result": res, "trace": tr}, True)
            return 0
        rep = _replayed_names(tr)
        print("재실행 #%s — '%s' 부터 (재생 %d단계: %s)" % (ns.request_id, ns.__dict__["from"], len(rep), ", ".join(rep[:8]) or "-"))
        print("-" * 70)
        print(res["answer"])
        print("-" * 70)
        print("total %.1f ms | tokens=%s | 새 request_id=%s" % (res["ms"], res.get("tokens", {}).get("total_tokens", 0), res.get("request_id")))
        if ns.trace:
            print("=" * 70)
            _print_trace(tr, verbose=(ns.debug_level or 0) >= 2)
        return 0

    if ns.cmd == "eval":
        from .evalset import load_questions
        qset = load_questions(ns.questions) if ns.questions else None
        if getattr(ns, "check", False):
            # 점수를 내기 전에 "이 숫자를 믿어도 되나" 부터. 오염된 색인에서 튜닝을 시작하면 며칠을 버린다.
            from .evalset import health as _health
            h = _health(p, qset)
            mark = {"ok": "✔", "warn": "△", "bad": "✘"}[h["level"]]
            _out(h, as_json, "%s 평가셋 신뢰도: %s (문항 %d · 색인 문서 %d)\n%s" % (
                mark, {"ok": "문제 없음", "warn": "주의", "bad": "이 상태의 점수는 믿을 수 없습니다"}[h["level"]],
                h["n"], h["checked"]["docs_indexed"],
                "\n".join("  [%s] %s\n%s" % (i["level"], i["detail"],
                                             "\n".join("       · " + x for x in i["questions"][:6]))
                          for i in h["issues"]) or "  (모든 점검 통과)"))
            return 0 if h["level"] != "bad" else 1
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
        ro = bool(getattr(ns, "retrieval_only", False))
        r, tr = p.evaluate(k=ns.k, questions=qset, log=not ro, retrieval_only=ro)
        # 놓친 문항의 원인까지 — 기대값은 평가셋에 이미 있으므로 공짜다 (예전에는 손으로 다시 적어야 했다)
        if getattr(ns, "forensic", False):
            from . import forensic as _fx
            miss = [x for x in r["rows"] if not x.get("hit") and x.get("request_id")][: max(1, ns.forensic_max)]
            r["forensics"] = []
            for x in miss:
                try:
                    rep = _fx.trace_expectation(p, int(x["request_id"]), list(x.get("expect_docs") or []),
                                                list(x.get("expect_terms") or []), note="eval --forensic")
                    r["forensics"].append({"q": x["q"], "request_id": x["request_id"],
                                           "summary": rep.get("summary"), "suggestions": rep.get("suggestions"),
                                           "lost_counts": rep.get("lost_counts"), "forensic_id": rep.get("forensic_id")})
                except Exception as e:
                    r["forensics"].append({"q": x["q"], "error": str(e)[:160]})
        if as_json:
            _out({"result": r, "trace": tr}, True)
            return 0
        for row in r["rows"]:
            tr_ = row.get("term_recall")
            print("%s rank=%-4s term=%s  %s" % ("✔" if row["hit"] else "✘", row["rank"],
                                                ("%.2f" % tr_) if tr_ is not None else "  - ", row["q"]))
        s = r["summary"]
        print("summary:", json.dumps(s, ensure_ascii=False))
        # 변별력이 없는 지표를 말해 준다 — 만점이라고 좋은 게 아니라 '안 움직이는 눈금' 일 수 있다
        dull = [m for m, d in (r.get("discriminating") or {}).items() if not d.get("useful")]
        if dull:
            print("△ 변별력 없음: %s — 모든 문항이 같은 값이라 이 지표로는 튜닝 효과를 볼 수 없습니다" % ", ".join(dull))
        if ro:
            print("(검색 전용: LLM 단계를 껐습니다 — 토큰 %s · 답변 지표는 계산하지 않음)" % s.get("total_tokens"))
        for f in (r.get("forensics") or []):
            print("\n✘ %s" % f["q"])
            if f.get("error"):
                print("   분석 실패: %s" % f["error"])
                continue
            for line in (f.get("summary") or [])[:3]:
                print("   %s" % line)
            for sg in (f.get("suggestions") or [])[:3]:
                print("   → (%s %.2f) %s" % (sg.get("kind"), sg.get("confidence") or 0, sg.get("detail", "")[:110]))
        return 0

    if ns.cmd == "graph" and ns.action == "profile":
        from . import graph_profile as _gp
        prof = _gp.profile(p, include_eval=ns.eval)
        prof["saved"] = _gp.save(prof)
        if ns.compare:
            hist = _gp.history(p.s)
            prev = _gp.load(hist[1]["path"]) if len(hist) > 1 else None
            prof["compare"] = _gp.compare(prev, prof) if prev else None
            if not prev:
                print("(비교할 이전 프로파일이 없습니다 — 이번 실행이 첫 기록입니다: %s)" % prof["saved"])
        if ns.out:
            with open(ns.out, "w", encoding="utf-8") as f:
                f.write(_gp.render_markdown(prof))
            print("마크다운 저장: %s" % ns.out)
        _out(prof, as_json, _gp.render_text(prof))
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

    if ns.cmd == "inspect":
        from . import querydebug as _qd
        d = _qd.inspect_query(p, " ".join(ns.question))
        _out(d, as_json, _qd.render_text(d))
        return 0

    if ns.cmd == "search":
        from .profiler import Profiler
        from .retrieval import channel_search, parse_channels
        q = " ".join(ns.question)
        prof = Profiler("search")
        r = channel_search(p.store, p.embedder, p.s, q, ns.channel, mode=ns.mode, k=ns.k, prof=prof,
                           require=getattr(ns, "require", None), exclude=getattr(ns, "exclude", None),
                           doc_types=getattr(ns, "doc_types", None), acl=p.acl_filter())
        r["trace"] = prof.finish()
        if as_json:
            _out(r, True)
            return 0
        chans = r["channels"]
        head = "채널 %s · 조건 %s · k=%d — 합집합 %d · 교집합 %d · 표시 %d" % (
            "+".join(chans), r.get("expr") or r["mode"], r["k"],
            r["counts"]["union"], r["counts"]["intersection"], r["counts"]["returned"])
        lines = [head, "  " + " · ".join("%s %d건 %.0fms" % (c, v["n"], v["ms"]) for c, v in r["per_channel"].items())]
        if r.get("weights"):
            lines.append("  RRF 가중치: " + ", ".join("%s=%.2f" % (c, w) for c, w in r["weights"].items()))
        lines.append("")
        lines.append("  %-4s %-34s %-22s %-7s %s" % ("#", "chunk_id", "채널(순위)", "점수", "문서 · 헤딩"))
        for i, x in enumerate(r["rows"]):
            chs = ",".join("%s#%d" % (c, v["rank"]) for c, v in sorted(x["channels"].items()))
            lines.append("  %-4d %-34s %-22s %-7.4f %s" % (i + 1, x["chunk_id"][:34], chs[:22], x["score"],
                                                           ("%s > %s" % (x.get("doc_id") or "", x.get("heading") or ""))[:60]))
        if not r["rows"]:
            lines.append("  (결과 없음)")
        if r.get("graph"):
            g = r["graph"]
            lines.append("")
            lines.append("  그래프 시드: %s" % json.dumps(g.get("seeds"), ensure_ascii=False)[:160])
            lines.append("  엔티티 %d · 관계 %d" % (len(g.get("entities") or []), len(g.get("relations") or [])))
        if len(chans) == 1:
            lines.append("")
            lines.append("  (여러 채널을 한 번에 보려면: search fts,vector,graph \"질의\" --mode and)")
        _out(r, as_json, "\n".join(lines))
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
            # payload JSON 원문 대신 한 줄 설명 + 점검 표시 (자세히는 `evolve show <id>`) — 2026-09-19
            for pr in st["pending"]:
                ex = pr.get("explain") or {}
                mark = "[X]" if [c for c in (ex.get("checks") or []) if c["level"] == "error"] else \
                       ("[!]" if [c for c in (ex.get("checks") or []) if c["level"] == "warn"] else "   ")
                print("  %s #%-4d %-11s conf=%.2f  %s" % (mark, pr["id"], pr["kind"], pr["confidence"] or 0,
                                                          ex.get("title") or json.dumps(pr["payload"], ensure_ascii=False)[:80]))
            if st["pending"]:
                print("  ([X] 이대로는 적용 실패/무효 · [!] 확인 필요 — 자세히: `evolve show <번호>`)")
            return 0
        if a == "show":
            # 제안 하나를 사람 말로 풀어 준다 — 무엇이 · 어느 파일에서 · 어떻게 바뀌고 · 무슨 영향이 있는지
            if not ns.args:
                print("사용법: evolve show <제안번호>  (번호는 `evolve status` 에서)")
                return 1
            try:
                pid = int(ns.args[0])
            except ValueError:
                print("ERROR: 제안 번호가 정수가 아닙니다 (받은 값: %r)" % ns.args[0])
                return 1
            d = ev.describe_proposal(p, pid)
            if d.get("error"):
                print("ERROR: %s (#%s)" % (d["error"], pid))
                return 1
            from . import proposal_explain as _pe
            _out(d, as_json, _pe.format_description(d))
            return 0
        if a == "list":
            _out(p.store.proposals(ns.args[0] if ns.args else None), True)
            return 0
        if a == "kinds":
            # 적용할 수 있는 제안 종류와 설명 (Web 드롭다운·MCP wiki_propose 와 같은 목록)
            allow = ev.auto_apply_kinds(p.s)
            _out({"kinds": ev.KINDS, "auto_apply_kinds": allow}, as_json,
                 "제안 종류 %d개 (자동 적용 허용: %s)\n" % (len(ev.KINDS), ", ".join(allow)) +
                 "\n".join("  %-14s %s%s" % (k, v, "   [자동 적용 가능]" if k in allow else "") for k, v in ev.KINDS.items()))
            return 0
        if a == "propose":
            # 수동 제안 등록 — 예전에는 Web/MCP 만 되고 CLI 에는 없었다 (세 창구 정렬)
            if len(ns.args) < 2:
                print("사용법: evolve propose <kind> '<payload JSON>' [--reason 이유] [--confidence 0.9]")
                print("  가능한 kind: %s  (설명은 `evolve kinds`)" % ", ".join(sorted(ev.KINDS)))
                return 1
            kind = ns.args[0]
            if kind not in ev.KINDS:
                print("ERROR: 알 수 없는 kind %r — 가능: %s" % (kind, ", ".join(sorted(ev.KINDS))))
                return 1
            try:
                payload = json.loads(" ".join(ns.args[1:]))
            except ValueError as e:
                print("ERROR: payload 가 JSON 이 아닙니다: %s" % e)
                return 1
            pid = p.store.add_proposal(kind, payload, ns.reason, float(ns.confidence), "manual")
            _out({"id": pid, "kind": kind, "status": "proposed"}, as_json,
                 "제안 #%d 등록 (%s) — 승인하려면 `evolve apply %d`" % (pid, kind, pid))
            return 0
        if a == "auto-apply":
            # 스케줄러의 auto_apply 와 같은 규칙을 손으로 한 번 돌린다 (신뢰도 하한 · 종류 제한 · 건수 상한)
            allow = [x.strip() for x in ns.kinds.split(",")] if ns.kinds else ev.auto_apply_kinds(p.s)
            min_conf = ns.min_conf if ns.min_conf is not None else float(p.s.evolve_min_confidence or 0.9)
            picked, applied, errors = [], [], []
            for pr in p.store.proposals("proposed"):
                if len(picked) >= ns.max_apply:
                    break
                if float(pr.get("confidence") or 0) < min_conf or pr.get("kind") not in allow:
                    continue
                picked.append({"id": pr["id"], "kind": pr["kind"], "confidence": pr["confidence"]})
            if not ns.dry_run:
                for pr in picked:
                    try:
                        r = ev.apply_proposal(p, int(pr["id"]), evaluate=not ns.no_eval)
                        applied.append({"id": pr["id"], "kind": pr["kind"], "status": r.get("status")})
                    except Exception as e:
                        errors.append({"id": pr["id"], "error": str(e)[:200]})
            out = {"picked": picked, "applied": applied, "errors": errors, "min_confidence": min_conf,
                   "kinds": allow, "dry_run": bool(ns.dry_run), "evaluate": not ns.no_eval}
            _out(out, as_json,
                 "대상 %d건 (신뢰도 ≥ %.2f · 종류 %s · 회귀평가 %s)\n" % (len(picked), min_conf, ",".join(allow), "포함" if not ns.no_eval else "생략") +
                 "\n".join("  #%s %-12s conf=%.2f%s" % (x["id"], x["kind"], x["confidence"] or 0,
                                                         "" if ns.dry_run else " → " + str(next((a2["status"] for a2 in applied if a2["id"] == x["id"]), "실패")))
                           for x in picked) +
                 ("\n(미리보기만 했습니다 — 실제로 적용하려면 --dry-run 을 빼세요)" if ns.dry_run else "") +
                 ("\n오류 %d건: %s" % (len(errors), json.dumps(errors, ensure_ascii=False)[:200]) if errors else ""))
            return 0
        _bad = []

        def _num(i: int, what: str):
            """잘못된 인자로 traceback 을 흘리지 않고 사용법을 알려 준다."""
            try:
                return int(ns.args[i])
            except (IndexError, TypeError, ValueError):
                _bad.append(what)
                return None
        def _usage(example: str) -> int:
            print("ERROR: %s 가 정수가 아닙니다. 사용법: evolve %s (받은 인자: %s)"
                  % ("·".join(_bad), example, list(ns.args) or "없음"))
            return 1
        if a == "apply":
            n = _num(0, "제안 번호")
            if n is None:
                return _usage("apply <제안번호>")
            _out(ev.apply_proposal(p, n, evaluate=not ns.no_eval), True)
            return 0
        if a == "reject":
            n = _num(0, "제안 번호")
            if n is None:
                return _usage("reject <제안번호> [사유]")
            _out(ev.reject_proposal(p, n, " ".join(ns.args[1:])), True)
            return 0
        if a == "review":
            _out(ev.llm_review(p), True)
            return 0
        if a == "feedback":
            qid, fb = _num(0, "질의 번호"), _num(1, "평가 점수")
            if qid is None or fb is None:
                return _usage("feedback <질의번호> <점수> [메모]")
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
        if getattr(ns, "full", False):
            # 운영 통계 — Web 옵저빌리티 › 시스템 · MCP wiki_status(full=true) 와 같은 함수 (llmwiki/opstats.py)
            from . import opstats as _ops
            d = _ops.collect(p, days=ns.days, sections=ns.sections or None, top=ns.top,
                             bucket=ns.bucket, trend_days=ns.trend_days)
            _out(d, as_json, _ops.format_text(d))
            return 0
        st = {"stats": p.store.stats(), "providers": p.provider_status(), "toggles": p.s.toggles.__dict__}
        _out(st, as_json, json.dumps(jsonable(st), ensure_ascii=False, indent=2))
        return 0

    if ns.cmd == "config":
        if ns.action == "paths":
            from .config import all_paths, conf_dir, CONF_DIR_FILES
            d = conf_dir()
            _out({"paths": all_paths(), "conf_dir": d, "conf_dir_files": list(CONF_DIR_FILES)}, as_json,
                 "\n".join("%-14s %s" % (k, v) for k, v in all_paths().items())
                 + ("\n\nLLMWIKI_CONF_DIR = %s  (이 폴더에 있는 설정이 먼저 쓰입니다)" % d if d else
                    "\n\n설정을 한 폴더에 모으려면: `config bundle --out conf` → 환경변수 LLMWIKI_CONF_DIR=conf (docs/PORTING.md)"))
            return 0
        if ns.action == "bundle":
            # 설정을 한 폴더로 모으거나(--out) 그 폴더에서 되돌린다(--from). 포팅용 — docs/PORTING.md §4
            from .config import bundle as _bundle
            if not ns.out and not ns.from_dir:
                print("사용법: config bundle --out <폴더>   (모으기)")
                print("        config bundle --from <폴더>  (되돌리기)")
                print("        --include-secrets 를 주면 .env 를 값째 복사합니다 (기본은 키 이름만)")
                return 1
            r = _bundle(out_dir=ns.out or "", restore_from=ns.from_dir or "",
                        include_secrets=bool(ns.include_secrets), dry_run=bool(ns.dry_run))
            if r.get("error"):
                print("ERROR: " + r["error"])
                return 1
            _out(r, as_json,
                 "%s %s (%d개%s)\n%s\n%s" % (
                     "모음" if r["action"] == "bundle" else "되돌림", r["dir"], len(r["copied"]),
                     " · 미리보기" if r.get("dry_run") else "",
                     "\n".join("  %-12s → %s%s" % (c["name"], c["to"], "  (%s)" % c["note"] if c.get("note") else "")
                               for c in r["copied"]),
                     ("\n건너뜀: " + ", ".join("%s(%s)" % (s["name"], s["why"]) for s in r["skipped"]) if r["skipped"] else "")
                     + "\n" + str(r.get("use") or r.get("note") or "")))
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
        if ns.action == "doc":
            # 설정·토글·튜닝 레지스트리에서 docs/CONFIG_REFERENCE.md 를 생성한다 (tuning doc · arch doc 과 같은 방식).
            from . import configdoc as _cd
            path = _cd.write_doc()
            print("written: %s" % path)
            return 0
        if ns.action == "fill-defaults":
            return _cmd_config_fill_defaults(ns, as_json)
        if ns.action == "reload":
            # 파일을 쓰지 않고 디스크의 config.json(+ --env 면 .env) 을 다시 읽어 이 프로세스(Web 콘솔이면 서버)에 반영한다
            from .config import load_settings as _load_settings, reload_env as _reload_env, path_for as _pf
            envrep = _reload_env() if ns.env else None
            p.s = _load_settings()
            p.reload()
            out = {"ok": True, "config": _pf("config"), "env": (envrep or {}).get("path") if ns.env else None,
                   "env_reloaded": (envrep or {}).get("reloaded") if ns.env else None,
                   "answer": p.s.role_llm("answer")["provider"] + "/" + str(p.s.role_llm("answer")["model"])}
            _out(out, as_json, "config reloaded: %s%s\n  answer=%s" % (out["config"], ("  .env=%s (%d keys)" % (out["env"], len(out["env_reloaded"] or []))) if ns.env else "", out["answer"]))
            return 0
        if ns.action == "env":
            from .config import env_report as _env_report
            rep = _env_report()
            if as_json:
                _out(rep, True)
                return 0
            print(".env: %s%s" % (rep["path"], "" if rep["exists"] else "  (파일 없음)"))
            print("  %-28s %-6s %-14s %s" % ("key", "set", "source", "value(masked)"))
            for k in rep["keys"]:
                print("  %-28s %-6s %-14s %s" % (k["name"], "yes" if k["set"] else "-", k["source"] or ("file(빈값)" if k.get("file_empty") else "-"), k["masked"]))
            if rep["overrides"]:
                print("활성 LLMWIKI_* 오버라이드 (config.json 보다 우선):")
                for o in rep["overrides"]:
                    print("  %-34s → %-28s %s" % (o["env"], o["key"], o["masked"]))
            print("다시 읽기: config reload --env · Web: Settings › config.json › .env")
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

    if ns.cmd == "server":
        return _cmd_server(ns, p, as_json)

    if ns.cmd == "schedule":
        return _cmd_schedule(ns, p, as_json)

    if ns.cmd == "models" and ns.action in ("list", "catalog", "discover", "policy"):
        from . import models_catalog as _mc
        if ns.action == "policy":
            tbl = p.s.role_policy_table()
            if as_json:
                _out(tbl, True)
                return 0
            print("역할별 LLM 정책 (llm_roles.<role>.<attr> > config 전역 > 기본값; headless 는 agents.json timeout_s/retries 가 전역보다 우선)")
            print("  %-9s %-28s %8s %7s %9s %-11s %7s %8s %8s" % ("role", "provider/model", "timeout", "retries", "backoff", "mode", "max", "budget", "circuit"))
            for r, c in tbl.items():
                print("  %-9s %-28s %7ss %7s %8ss %-11s %6ss %7ss %s/%ss" % (r, ("%s/%s" % (c["provider"], c["model"]))[:28], c["timeout_s"], c["retries"], c["backoff_s"],
                                                                            c["backoff"], c["backoff_max_s"], c["budget_s"] or "-", c["circuit_failures"], c["circuit_cooldown_s"]))
            print("변경: models set answer_timeout_s=120 answer_retries=2 rerank_backoff=linear … (config.json llm_roles) · 회로 상태: server circuits")
            return 0
        if ns.action == "discover":
            r = _mc.discover(p.s)
            if as_json:
                _out(r, True)
                return 0
            for prov in ("ollama", "openai"):
                rows = r.get(prov) or []
                print("%s (%s): %s" % (prov, getattr(p.s, "ollama_url" if prov == "ollama" else "openai_base_url", ""), r["errors"].get(prov, "%d models" % len(rows))))
                for m in rows:
                    print("  %s %s" % ("✔" if m.get("in_catalog") else "+", m.get("id")))
            print("+ 표시는 카탈로그에 없는 모델: models catalog add <id> --provider %s" % "ollama|openai")
            return 0
        if ns.action == "catalog":
            sub = ns.kv[0] if ns.kv else "list"
            if sub == "add":
                if len(ns.kv) < 2:
                    print("usage: models catalog add <id> --provider <p> [--label …] [--roles a,b] [--tags x,y] [--notes …] [--embed]")
                    return 1
                m: Dict[str, Any] = {"id": ns.kv[1], "provider": ns.provider or "auto", "label": ns.label or ns.kv[1],
                                     "roles": [x for x in (ns.roles or "").split(",") if x.strip()], "tags": [x for x in (ns.tags or "").split(",") if x.strip()],
                                     "notes": ns.notes or "", "enabled": True}
                if getattr(ns, "catalog_embed", False):
                    m["kind"] = "embed"
                _mc.add_model(m)
                print("added: %s (%s) → %s" % (m["id"], m["provider"], _mc.catalog_path()))
                return 0
            if sub == "remove":
                if len(ns.kv) < 2:
                    print("usage: models catalog remove <id> [--provider p]")
                    return 1
                try:
                    _mc.remove_model(ns.kv[1], ns.provider)
                except ValueError as e:
                    print("ERROR:", e)
                    return 1
                print("removed: %s" % ns.kv[1])
                return 0
            if sub == "path":
                print(_mc.catalog_path())
                return 0
        d = _mc.describe(p.s, role=ns.role)
        rows = [m for m in d["models"] if (not ns.provider or m["provider"] == ns.provider)]
        if as_json:
            _out({"models": rows, "embed": d["embed"], "in_use": d["in_use"], "unknown_in_use": d["unknown_in_use"], "path": d["path"]}, True)
            return 0
        print("모델 카탈로그 (%s)%s" % (d["path"], (" — role=%s" % ns.role) if ns.role else ""))
        for m in rows:
            print("  %s %-34s %-18s %-32s roles=%s %s" % ("✔" if m.get("enabled", True) else "✘", m["id"][:34], m["provider"], (m.get("label") or "")[:32],
                                                       ",".join(m.get("roles") or []) or "*", " ".join("#" + t for t in (m.get("tags") or []))))
        print("임베딩:")
        for m in d["embed"]:
            print("  %-26s %-8s %s" % (m.get("id") or "(hash)", m.get("provider"), m.get("label") or ""))
        print("현재 사용: " + ", ".join("%s=%s/%s" % (r, c["provider"], c["model"]) for r, c in d["in_use"].items()))
        if d["unknown_in_use"]:
            print("카탈로그에 없는 설정: " + ", ".join("%s=%s/%s" % (u["role"], u["provider"], u["model"]) for u in d["unknown_in_use"]) + "  (models catalog add … 로 등록)")
        print("추가/삭제: models catalog add <id> --provider ollama --label … --roles answer,rerank | models catalog remove <id> · 서버 조회: models discover")
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
        if ns.action == "ensemble":
            return _cmd_models_ensemble(ns, p, as_json)
        if ns.action == "automap":
            # 카탈로그 전체 연결 테스트 → 연결되는 모델만 골라 역할에 배정 (제안, --apply 면 저장)
            r = p.automap_models(live=bool(ns.live), apply=bool(getattr(ns, "apply", False)))
            if as_json:
                _out(r, True)
                return 0
            print("자동 매핑 (%s) — 카탈로그 %s개 중 연결 OK: LLM %d · 임베딩 %d · 리랭크 %d  [%s]" % (
                r["path"], r.get("tested"), r["candidates"]["llm"], r["candidates"]["embed"], r["candidates"]["rerank"], r["note"]))
            print("  %-10s %-34s %-34s %s" % ("역할", "지금", "제안", "이유"))
            for role, x in r["proposal"].items():
                new = ("%s/%s" % (x.get("provider"), x.get("model"))) if x.get("ok") else "(없음)"
                mark = "→" if x.get("changed") else "="
                print("  %-10s %-34s %s %-32s %s" % (role, (x.get("current") or "")[:34], mark, new[:32], x.get("why", "")))
                if x.get("warn"):
                    print("             ⚠ %s" % x["warn"])
            if r["applied"]:
                print("\nconfig.json 에 저장했습니다: %s" % ", ".join(r["applied"]))
            elif getattr(ns, "apply", False):
                print("\n바꿀 것이 없습니다 (이미 같은 모델).")
            else:
                print("\n(제안만 출력했습니다. 실제로 꽂으려면 `models automap --apply`, 실제 호출까지 확인하려면 --live 를 같이)")
            return 0
        if ns.action == "test" and getattr(ns, "catalog", False):
            # 카탈로그(models.json) 전체: enabled 항목마다 (provider, model) ping (+--live 완성 1회)
            r = p.test_catalog(live=bool(ns.live))
            if as_json:
                _out(r, True)
            else:
                print("카탈로그 연결 테스트 (%s) — %d 항목 중 %d OK%s" % (r["path"], r["n"], r["ok_n"], "  [live]" if r["live"] else ""))
                for x in r["rows"]:
                    extra = ("  live: %s %.0fms %s" % ("OK" if x.get("live_ok") else "FAIL", x.get("live_ms", 0), x.get("live_detail", ""))) if "live_ok" in x else ""
                    print("[%s] %-6s %-20s %-34s %6.0fms  %s%s" % ("OK " if x.get("ok") else "FAIL", x.get("kind"), (x.get("provider") or "")[:20], (x.get("id") or "")[:34],
                                                                x.get("ms") or 0, (x.get("detail") or "")[:100], extra))
                if not ns.live:
                    print("(ping 만 확인. 실제 완성 호출까지 확인하려면 models test --catalog --live · 역할별 확인은 models test)")
            return 0 if r["ok_n"] == r["n"] else 1
        if ns.action == "test":
            r = p.test_providers(live=bool(ns.live))
            if as_json:
                _out(r, True)
            else:
                for k, x in r.items():
                    mark = "OK " if x.get("ok") else "FAIL"
                    extra = ("  live: %s %.0fms %s" % ("OK" if x.get("live_ok") else "FAIL", x.get("live_ms", 0), x.get("live_detail", ""))) if "live_ok" in x else ""
                    print("[%s] %-10s %s/%s  %.0fms  %s%s" % (mark, k, x.get("provider") or x.get("url") or "", x.get("model"), x.get("ms") or 0, x.get("detail", ""), extra))
                    if x.get("hint"):
                        print("       ↳ %s" % x["hint"])     # 계획 §0.1-c: provider/model 짝이 카탈로그와 다르면 한 줄
                if not ns.live:
                    print("(ping 만 확인. PAT 권한·헤더·모델명·headless 실행까지 확인하려면 models test --live · 카탈로그 전체는 models test --catalog)")
            return 0 if all(x.get("ok") for x in r.values()) else 1
        st = p.provider_status()
        if as_json:
            _out(st, True)
            return 0
        print("embedder: %s model=%s dim=%s available=%s (embed_provider=%s embed_model=%r)" % (
            st["embedder"]["name"], st["embedder"].get("model"), st["embedder"].get("dim"), st["embedder"]["available"],
            st["embedder"]["provider_setting"], st["embedder"]["model_setting"]))
        print("global llm: provider=%s model=%s effort(extract/rerank)=%s effort(answer)=%s  timeout=%ss retries=%s backoff=%s %ss(max %ss) budget=%ss circuit=%s/%ss" % (
            p.s.llm_provider, p.s.llm_model, p.s.llm_effort, p.s.answer_effort, p.s.llm_timeout, p.s.llm_retries, p.s.llm_retry_backoff, p.s.llm_retry_backoff_s,
            p.s.llm_retry_backoff_max_s, p.s.llm_budget_s, p.s.llm_circuit_failures, p.s.llm_circuit_cooldown_s))
        for role, r in st["roles"].items():
            c = r["configured"]
            pol = r.get("policy") or {}
            circ = r.get("circuit") or {}
            print("  %-8s -> %s/%s effort=%s available=%s%s  timeout=%ss retries=%s backoff=%s%s%s  [%s]" % (
                role, c["provider"], c["model"], c["effort"], r["available"], " (role override)" if r["overridden"] else " (global)",
                pol.get("timeout_s"), pol.get("retries"), pol.get("backoff_s"), (" budget=%ss" % pol["budget_s"]) if pol.get("budget_s") else "",
                (" CIRCUIT OPEN" if circ.get("open_until", 0) > time.time() else ""), st["catalog"]["roles"].get(role, "")))
        print("변경: models set rerank_provider=ollama rerank_model=llama3.1 answer_model=claude-opus-5 answer_timeout_s=120 answer_retries=2 embed_provider=voyage · 정책 표: models policy · 카탈로그: models list")
        return 0

    if ns.cmd == "requests":
        from .profiler import flatten_trace
        if ns.action == "queries":
            # 질의 로그 = '누가 무엇을 물었나'. Web Observability › 질의·로그 와 같은 데이터.
            rows = p.store.queries(ns.limit, user=ns.user, q=ns.q, origin=ns.origin)
            if as_json:
                _out(rows, True)
                return 0
            import time as _t
            for r in rows:
                who = r.get("user") or "-"
                extra = "/".join(x for x in (r.get("role"), r.get("origin"), r.get("via")) if x)
                fb = "" if r.get("feedback") is None else (" +1" if r["feedback"] > 0 else " -1")
                print("#%-5d %s %-14s %-20s %s%s" % (
                    r["id"], _t.strftime("%m-%d %H:%M:%S", _t.localtime(r["ts"])), who[:14],
                    ("(" + extra + ")")[:20], (r.get("query") or "")[:60], fb))
            if not rows:
                print("질의 로그가 비어 있습니다 (토글 evolve_capture 가 꺼져 있으면 기록하지 않습니다)")
            return 0
        if ns.action == "users":
            rows = p.store.query_users(ns.limit)
            if as_json:
                _out(rows, True)
                return 0
            import time as _t
            print("%-24s %6s %5s %5s  %s" % ("사용자", "질의", "+1", "-1", "마지막"))
            for r in rows:
                print("%-24s %6d %5d %5d  %s" % (r["user"][:24], r["n"], r["up"] or 0, r["down"] or 0,
                                                 _t.strftime("%m-%d %H:%M:%S", _t.localtime(r["last_ts"] or 0))))
            return 0
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
                print("mode=%s (effective: %s) · local=%s · sso=%s · anonymous=%s · 역할: %s · 파일: %s" % (
                    a.cfg.get("mode"), a.mode, info["local"], info["sso"], a.anonymous_role or "(로그인 필수)", "<".join(ROLES),
                    __import__("llmwiki.auth", fromlist=["security_path"]).security_path()))
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
            from .auth import norm_role, ROLE_ALIASES
            if str(ns.role or "").lower() not in ROLES and str(ns.role or "").lower() not in ROLE_ALIASES:
                print("--role %s 필요" % "|".join(ROLES))
                return 1
            a.set_role(ns.name, ns.role)
            print("role updated: %s → %s" % (ns.name, norm_role(ns.role)))
            return 0

    if ns.cmd == "apikey":
        from .auth import Auth
        a = Auth(p.s)
        if ns.action == "add":
            if not ns.name:
                print("apikey add <name> --role viewer")
                return 1
            try:
                r = a.add_api_key(ns.name, ns.role, ns.note)
            except ValueError as e:
                print("error: %s" % e)
                return 1
            _out(r, as_json, "API key created (지금만 표시됩니다 — 안전한 곳에 보관):\n  id=%s name=%s role=%s\n  token=%s\n사용: Authorization: Bearer %s  (MCP HTTP / curl / LLMWIKI_API_KEY)"
                 % (r["id"], r["name"], r["role"], r["token"], r["token"]))
            return 0
        if ns.action == "remove":
            ok = a.remove_api_key(ns.name or "")
            print("removed" if ok else "no such key")
            return 0 if ok else 1
        rows = a.list_api_keys()
        _out(rows, as_json, "\n".join("%-10s %-24s %-8s created=%s last_used=%s %s" % (r["id"], r["name"], r["role"],
                                                                                       time.strftime("%m-%d %H:%M", time.localtime(r.get("created") or 0)),
                                                                                       time.strftime("%m-%d %H:%M", time.localtime(r["last_used"])) if r.get("last_used") else "-",
                                                                                       r.get("note") or "") for r in rows) or "(no api keys — apikey add <name> --role viewer)")
        return 0

    if ns.cmd == "security":
        from .auth import Auth, load_security, save_security, DEFAULT_SECURITY, LEVELS, LEVEL_LABEL, DEFAULT_LEVEL_ROLE, ROLE_LABEL, ROLES
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
        if ns.action == "docacl":
            # 문서 단위 접근 제어 (docacl.json) — 규칙을 넣기 전에 "누가 무엇을 못 보게 되는가" 를 먼저 본다.
            from . import docacl as _dacl
            sub = ns.args[0] if ns.args else "show"
            if sub == "init":
                if os.path.exists(_dacl.acl_path()):
                    print("already exists: %s" % _dacl.acl_path())
                    return 1
                _dacl.save(dict(_dacl.DEFAULTS))
                print("created %s — setup/docacl.example.json 의 rules 를 참고해 경로 규칙을 넣으세요 (docs/SECURITY.md)" % _dacl.acl_path())
                return 0
            if sub == "check":
                r = _dacl.check(p.store, ns.role)
                _out(r, as_json, "역할 %s: 문서 %d건 중 %d건 가려짐 (보임 %d) · 규칙 적용 %s\n%s" % (
                    r["role"], r["docs"], r["blocked"], r["visible"], "on" if r["enabled"] else "off (규칙 없음/토글 꺼짐)",
                    "\n".join("  %-50s 필요 %-8s (%s)" % (x["doc_id"][:50], x["min_role"], x["why"]) for x in r["examples"][:30])
                    or "  (가려지는 문서 없음)"))
                return 0
            if sub != "show":
                print("usage: security docacl [show | check --role <역할> | init]")
                return 1
            d = _dacl.describe()
            _out(d, as_json, "file: %s%s\nenabled: %s · default_min_role: %s · 토글 doc_acl: %s\n역할 (낮→높): %s\n규칙 %d개:\n%s\n%s" % (
                d["path"], "" if d["exists"] else "  (파일 없음 — 아무도 막지 않음. 'security docacl init')",
                d["enabled"], d["default_min_role"], "on" if getattr(p.s.toggles, "doc_acl", True) else "off",
                " < ".join(d["roles"]), len(d["rules"]),
                "\n".join("  %-40s → %-8s %s" % (r.get("prefix"), r.get("min_role"), r.get("note") or "") for r in d["rules"]) or "  (없음)",
                d["note"]))
            return 0
        if ns.action == "perms":
            a = Auth(p.s)
            sub = ns.args[0] if ns.args else "show"
            if sub == "set":
                for kv in ns.args[1:]:
                    k, _, v = kv.partition("=")
                    try:
                        a.set_permission(k.strip(), v.strip())
                    except ValueError as e:
                        print("ERROR:", e)
                        return 1
            elif sub == "reset":
                a.set_permissions({"levels": dict(DEFAULT_LEVEL_ROLE), "ops": {}})
            elif sub != "show":
                print("usage: security perms [show | set <level|op>=<role> … | reset]")
                return 1
            perms = a.permissions()
            if as_json:
                _out(perms, True)
                return 0
            print("역할 (낮→높): %s" % " < ".join(ROLES))
            for r in ROLES:
                print("  %-8s %s" % (r, ROLE_LABEL[r]))
            print("\n등급별 최소 역할 (permissions.levels):")
            for lv in LEVELS:
                print("  %-12s %-8s %s%s" % (lv, perms["levels"][lv], LEVEL_LABEL[lv], "" if perms["levels"][lv] == DEFAULT_LEVEL_ROLE[lv] else "  (기본 %s)" % DEFAULT_LEVEL_ROLE[lv]))
            print("\n개별 작업 오버라이드 (permissions.ops): %s" % ("(없음)" if not perms["ops"] else ""))
            for k, v in perms["ops"].items():
                print("  %-36s %s" % (k, v))
            c = a.cfg.get("cli") or {}
            print("\n익명 접속 역할: %s · CLI 기본 역할: %s (require_login=%s) · 파일: %s" % (a.anonymous_role or "(로그인 필수)", c.get("default_role"), c.get("require_login"), _auth.security_path()))
            print("변경: security perms set run=viewer  |  security perms set '/api/eval=class2'  |  security perms set 'cli:trial run=class2'")
            return 0
        a = Auth(p.s)
        cfg = json.loads(json.dumps(a.cfg))
        for v in (cfg.get("users") or {}).values():
            v.pop("pw", None)
        for v in (cfg.get("api_keys") or {}).values():
            v.pop("hash", None)
        _out({"path": _auth.security_path(), "effective_mode": a.mode, "security": cfg, "permissions": a.permissions()}, as_json,
             "file: %s\nmode: %s (effective %s) · anonymous_role: %s · cli.default_role: %s\nusers: %d · api_keys: %d · sso: %s (%s)\ndestructive: phrase=%r reauth=%s snapshot_before=%s keep=%s\npermissions.levels: %s\npermissions.ops: %d\n%s" % (
                 _auth.security_path(), cfg.get("mode"), a.mode, a.anonymous_role or "(로그인 필수)", (cfg.get("cli") or {}).get("default_role"),
                 len(cfg.get("users") or {}), len(cfg.get("api_keys") or {}), "on" if (cfg.get("sso") or {}).get("enabled") else "off",
                 (cfg.get("sso") or {}).get("type"), (cfg.get("destructive") or {}).get("confirm_phrase"), (cfg.get("destructive") or {}).get("require_reauth"),
                 (cfg.get("destructive") or {}).get("snapshot_before"), (cfg.get("destructive") or {}).get("snapshot_keep"),
                 json.dumps(a.permissions()["levels"]), len(a.permissions()["ops"]),
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

    if ns.cmd == "optimize":
        from . import optimize as _opt
        try:
            rid = None if str(ns.target).lower() in ("last", "", "none") else int(ns.target)
        except (TypeError, ValueError):
            print("ERROR: optimize 의 대상은 요청 번호(정수) 또는 last 입니다 (받은 값: %s)" % ns.target)
            return 1
        b = _opt.bundle_markdown(p, rid, None if ns.focus == "all" else ns.focus)
        if ns.out:
            from . import atomicio as _aio
            _aio.write_text(ns.out, b["markdown"])
            _out({k: v for k, v in b.items() if k != "markdown"}, as_json,
                 "최적화 자료 묶음: %s (%d자, request #%s, 초점 %s)\n  이 파일을 통째로 LLM 에게 주고 C 절의 요청에 답하게 하세요."
                 % (ns.out, b["chars"], b.get("request_id"), b["focus"]))
        else:
            print(b["markdown"])
        return 0

    if ns.cmd == "arch":
        from .architecture import registry, render_text
        if getattr(ns, "action", "show") == "doc":
            from . import optimize as _opt
            from . import atomicio as _aio
            from .config import ROOT as _R
            out = ns.out or os.path.join(_R, "docs", "OPTIMIZATION_GUIDE.md")
            _aio.write_text(out, _opt.guide_markdown(p.s))
            _out({"written": out}, as_json, "written: %s" % out)
            return 0
        if getattr(ns, "action", "show") == "limits":
            from .architecture import stage_limits, render_limits
            from . import reqmgr as _rqm
            from .architecture import FLOWS as _FLOWS
            lim = stage_limits(p.s, _rqm.load_config())
            if ns.flow:
                keep = {st["key"] for st in (_FLOWS.get(ns.flow) or {"stages": []})["stages"]}
                lim["flows"] = {k: v for k, v in lim["flows"].items() if k == ns.flow}
                lim["stages"] = {k: v for k, v in lim["stages"].items() if k in keep}
            _out(lim, as_json, render_limits(lim))
            return 0
        reg = registry()
        if ns.flow:
            reg["flows"] = {k: v for k, v in reg["flows"].items() if k == ns.flow}
        if as_json:
            _out(reg, True)
            return 0
        print(render_text(reg, p.s.toggles.__dict__))
        return 0

    if ns.cmd == "mcp":
        from .mcp import serve_stdio, bridge_stdio_to_http, client_config_snippets
        url = ns.connect or os.environ.get("LLMWIKI_MCP_URL") or getattr(p.s, "mcp_url", "") or ""
        token = ns.token or os.environ.get("LLMWIKI_MCP_TOKEN") or ""
        if getattr(ns, "client_config", False):
            import socket
            base = ns.url or url.rsplit("/mcp", 1)[0]
            if not base:
                host = p.s.web_host if p.s.web_host not in ("0.0.0.0", "::", "") else socket.gethostname()
                base = "http://%s:%d" % (host, int(p.s.web_port))
            _out(client_config_snippets(base, token), True)
            return 0
        if getattr(ns, "doctor", False):
            from .mcp import doctor as _doctor
            rep = _doctor(p, check_sources=bool(getattr(ns, "check_sources", False)))
            if as_json:
                _out(rep, True)
            else:
                print(_mcp_doctor_text(rep))
            return 0 if rep["ok"] else 1
        if url:
            return bridge_stdio_to_http(url, token, timeout=int(getattr(p.s, "llm_timeout", 600) or 600))
        transport = ns.transport or getattr(p.s, "mcp_transport", "stdio") or "stdio"
        if transport == "http":
            from .web.server import serve
            serve(p, ns.host or p.s.mcp_host, int(ns.port or p.s.mcp_port), insecure=bool(ns.insecure), mcp_only=True)
            return 0
        serve_stdio(p)
        return 0

    if ns.cmd == "serve":
        from .web.server import serve
        serve(p, ns.host or p.s.web_host, int(ns.port or p.s.web_port), insecure=bool(getattr(ns, "insecure", False)))
        return 0
    print("unknown command: %s" % ns.cmd)
    return 1


def _server_client(ns, p):
    """server/schedule trigger 명령용 HTTP 클라이언트: URL(--url > config web_host/web_port), 인증(--token > LLMWIKI_API_KEY > --user/--password 로그인)."""
    import socket
    import urllib.request
    import urllib.error
    url = ns.url
    if not url:
        host = p.s.web_host if p.s.web_host not in ("0.0.0.0", "::", "") else "127.0.0.1"
        url = "http://%s:%d" % (host, int(p.s.web_port))
    url = url.rstrip("/")
    token = ns.token or os.environ.get("LLMWIKI_API_KEY") or ""
    cookie = ""
    if not token and getattr(ns, "cli_user", None):
        pw = getattr(ns, "cli_password", None) or os.environ.get("LLMWIKI_PASSWORD") or ""
        req = urllib.request.Request(url + "/api/auth/login", data=json.dumps({"username": ns.cli_user, "password": pw}).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "X-Requested-With": "llmwiki-cli"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            cookie = (r.headers.get("Set-Cookie") or "").split(";")[0]

    def call(method: str, path: str, body=None):
        hdrs = {"Content-Type": "application/json", "X-Requested-With": "llmwiki-cli"}
        if token:
            hdrs["Authorization"] = "Bearer " + token
        if cookie:
            hdrs["Cookie"] = cookie
        req = urllib.request.Request(url + path, data=json.dumps(body).encode("utf-8") if body is not None else None, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")
            except Exception:
                return e.code, {"error": "HTTP %s" % e.code}
        except Exception as e:
            return 599, {"error": "서버에 연결할 수 없습니다 (%s): %s — serve 가 실행 중인지, --url 이 맞는지 확인" % (url, e)}
    return url, call


def _cmd_server(ns, p, as_json: bool) -> int:
    url, call = _server_client(ns, p)
    a = list(ns.args or [])
    act = ns.action
    if act == "status":
        code, j = call("GET", "/api/admin/server")
        if code != 200:
            code2, j2 = call("GET", "/api/activity")
            if code2 == 200:
                j = {"activity": j2, "note": "admin 이 아니라 활동 목록만 (server status 전체는 admin 키/계정 필요)"}
                code = 200
        if code != 200:
            _out(j, as_json, "ERROR %s: %s" % (code, j.get("error")))
            return 1
        if as_json:
            _out(j, True)
            return 0
        if "counters" in j:
            lim = j["limits"]["concurrency"]
            print("서버 %s (pid %s) uptime %.0fs · 실행 %d · 대기 %d · 락 %s · 처리량 %.1f/min (최근 %.0f분, 오류 %d)" % (
                url, j.get("pid"), j["uptime_s"], j["running"], j["queued"], json.dumps(j["lock"]), j["throughput_per_min"], j["window_min"], j["errors_in_window"]))
            print("제한: parallel_reads=%s per_user=%s per_ip=%s queue=%s/%ss reads_during_build=%s · rate/min user=%s ip=%s query=%s · maintenance=%s" % (
                lim["max_parallel_reads"], lim["max_parallel_per_user"], lim["max_parallel_per_ip"], lim["queue_max"], lim["queue_timeout_s"], lim["reads_during_build"],
                j["limits"]["rate_limit"]["per_user_per_min"], j["limits"]["rate_limit"]["per_ip_per_min"], j["limits"]["rate_limit"]["query_per_user_per_min"],
                j["limits"]["access"]["maintenance_mode"]))
            for k, v in (j.get("latency") or {}).items():
                print("  지연 %-8s n=%d avg=%.0fms p50=%.0f p95=%.0f max=%.0f" % (k, v["n"], v["avg_ms"], v["p50_ms"], v["p95_ms"], v["max_ms"]))
            print("카운터: " + json.dumps(j.get("counters"), ensure_ascii=False))
            open_c = {k: v for k, v in (j.get("circuits") or {}).items() if v.get("open")}
            if open_c:
                print("회로 차단 중: " + ", ".join(open_c))
            print("클라이언트 (최근 %d):" % len(j.get("clients") or []))
            for c in (j.get("clients") or [])[:15]:
                print("  %-26s active=%s total=%s rejected=%s errors=%s last=%s %s" % (c["key"], c["active"], c["total"], c["rejected"], c["errors"],
                                                                                   time.strftime("%H:%M:%S", time.localtime(c["last_seen"])), c.get("agent", "")[:30]))
            act_ = j.get("activity") or {}
        else:
            act_ = j.get("activity") or {}
        for sect in ("running", "queued", "external"):
            rows = act_.get(sect) or []
            if rows:
                print("%s (%d):" % (sect, len(rows)))
                for r in rows:
                    print("  %-18s %-8s %-40s %s %s %.0fs %s" % (r.get("token"), r.get("kind"), (r.get("label") or "")[:40], r.get("user") or "-", r.get("origin") or "",
                                                                 r.get("elapsed_s") or 0, ("· " + r["stage"]) if r.get("stage") else ""))
        return 0
    if act == "requests":
        code, j = call("GET", "/api/activity?history=%d" % 30)
        if code != 200:
            _out(j, as_json, "ERROR %s: %s" % (code, j.get("error")))
            return 1
        if as_json:
            _out(j, True)
            return 0
        for sect in ("running", "queued", "external", "recent"):
            rows = j.get(sect) or []
            print("%s (%d)" % (sect, len(rows)))
            for r in rows:
                print("  %-18s %-9s %-8s %-36s %-12s %6.1fs %s%s" % (r.get("token"), r.get("status"), r.get("kind"), (r.get("label") or "")[:36], (r.get("user") or "-")[:12],
                                                                     r.get("elapsed_s") or 0, r.get("stage") or "", (" ✖ " + r["error"][:60]) if r.get("error") else ""))
        print("취소: server cancel <token>")
        return 0
    if act == "cancel":
        if not a:
            print("usage: server cancel <token>")
            return 1
        code, j = call("DELETE", "/api/activity/%s" % a[0])
        _out(j, as_json, ("cancel requested: %s" % a[0]) if j.get("ok") else ("ERROR: %s" % j.get("error")))
        return 0 if j.get("ok") else 1
    if act == "limits":
        if a and a[0] == "set":
            vals = {}
            for kv in a[1:]:
                k, _, v = kv.partition("=")
                vals[k.strip()] = v.strip()
            code, j = call("POST", "/api/admin/server", {"action": "set_limits", "values": vals, "save": True})
        else:
            code, j = call("GET", "/api/admin/server")
            j = {"limits": j.get("limits"), "config_path": j.get("config_path")} if code == 200 else j
        if code != 200:
            _out(j, as_json, "ERROR %s: %s" % (code, j.get("error")))
            return 1
        _out(j, True)
        return 0
    if act == "block":
        if len(a) < 3 or a[0] not in ("add", "remove") or a[1] not in ("ip", "user", "allow_ip"):
            print("usage: server block add|remove ip|user|allow_ip <값>")
            return 1
        code, j = call("POST", "/api/admin/server", {"action": "block", "kind": a[1], "value": a[2], "add": a[0] == "add"})
        _out(j, as_json, "ERROR: %s" % j.get("error") if code != 200 else "%s list: %s" % (a[1], j.get("list")))
        return 0 if code == 200 else 1
    if act == "sessions":
        body = {"action": "sessions", "sub": "list"}
        if a and a[0] == "revoke" and len(a) > 1:
            body = {"action": "sessions", "sub": "revoke", "sid": a[1]}
        elif a and a[0] == "revoke-user" and len(a) > 1:
            body = {"action": "sessions", "sub": "revoke_user", "user": a[1]}
        code, j = call("POST", "/api/admin/server", body)
        if code != 200:
            _out(j, as_json, "ERROR %s: %s" % (code, j.get("error")))
            return 1
        if as_json:
            _out(j, True)
            return 0
        for s_ in j.get("sessions") or []:
            print("  %-18s %-14s %-8s %-15s created=%s last=%s %s" % (s_["sid"], s_["user"], s_["role"], s_.get("ip"), time.strftime("%m-%d %H:%M", time.localtime(s_["created"])),
                                                                     time.strftime("%m-%d %H:%M", time.localtime(s_["last_seen"])), (s_.get("agent") or "")[:30]))
        print("(server.json sessions.enforce=true 일 때 revoke 가 즉시 로그아웃시킨다)")
        return 0
    if act == "maintenance":
        on = bool(a) and a[0].lower() in ("on", "1", "true")
        code, j = call("POST", "/api/admin/server", {"action": "maintenance", "enabled": on, "message": " ".join(a[1:]) if len(a) > 1 else ""})
        _out(j, as_json, "maintenance_mode=%s" % on if code == 200 else "ERROR: %s" % j.get("error"))
        return 0 if code == 200 else 1
    if act == "kick":
        if not a:
            print("usage: server kick <user>")
            return 1
        code, j = call("POST", "/api/admin/server", {"action": "kick", "user": a[0]})
        _out(j, as_json, "cancelled %s requests of %s" % (j.get("cancelled"), a[0]) if code == 200 else "ERROR: %s" % j.get("error"))
        return 0 if code == 200 else 1
    if act == "circuits":
        if a and a[0] == "reset":
            code, j = call("POST", "/api/admin/server", {"action": "circuit_reset", "key": a[1] if len(a) > 1 else None})
        else:
            code, j = call("GET", "/api/admin/server")
            j = {"circuits": j.get("circuits")} if code == 200 else j
        _out(j, True)
        return 0 if code == 200 else 1
    if act == "log-level":
        if not a:
            print("usage: server log-level DEBUG|INFO|WARNING|ERROR")
            return 1
        code, j = call("POST", "/api/admin/server", {"action": "log_level", "level": a[0], "save": False})
        _out(j, as_json, "server log_level=%s (이번 실행만; 영구는 config set log_level=…)" % j.get("log_level") if code == 200 else "ERROR: %s" % j.get("error"))
        return 0 if code == 200 else 1
    return 1


def _cmd_schedule(ns, p, as_json: bool) -> int:
    from . import scheduler as _sc
    act = ns.action
    if act == "add":
        if not ns.task:
            print("usage: schedule add --task '<json>'")
            return 1
        try:
            t = json.loads(ns.task)
            _sc.upsert_task(t)
        except (ValueError, KeyError) as e:
            print("ERROR:", e)
            return 1
        print("saved: %s → %s" % (t.get("name"), _sc.schedule_path()))
        return 0
    if act == "remove":
        ok = _sc.remove_task(ns.name or "")
        print("removed" if ok else "no such task: %s" % ns.name)
        return 0 if ok else 1
    if act in ("enable", "disable"):
        ok = _sc.set_enabled(ns.name or "", act == "enable")
        print("%s: %s" % (act, ns.name) if ok else "no such task: %s" % ns.name)
        return 0 if ok else 1
    if act == "validate":
        d = _sc.load_schedule()
        bad = 0
        for t in d.get("tasks", []):
            try:
                _sc.validate_task(t)
                print("  OK   %s" % t.get("name"))
            except Exception as e:
                bad += 1
                print("  FAIL %s: %s" % (t.get("name"), e))
        if d.get("error"):
            print("  FAIL %s" % d["error"])
            bad += 1
        return 1 if bad else 0
    if act == "history":
        rows = _sc.read_history(ns.n)
        if as_json:
            _out(rows, True)
            return 0
        for r in rows:
            print("  %s %-20s %-9s %6.0fms %s" % (time.strftime("%m-%d %H:%M:%S", time.localtime(r.get("ts", 0))), r.get("name"), r.get("status"), r.get("ms") or 0,
                                                 (r.get("error") or "")[:80] or json.dumps(r.get("result"), ensure_ascii=False, default=str)[:80]))
        return 0
    if act == "run":
        t = next((x for x in _sc.load_schedule().get("tasks", []) if x.get("name") == ns.name), None)
        if not t:
            print("no such task: %s" % ns.name)
            return 1
        sch = _sc.Scheduler(p, None)
        tok = sch.run_now(ns.name, by="cli")
        with _pg.cli_monitor("cli-schedule-%s" % ns.name, "schedule", "schedule %s" % ns.name, enabled=False):
            pass
        while ns.name in sch.running:
            time.sleep(0.5)
        rec = sch.history(1)[0] if sch.history(1) else {}
        _out(rec, as_json, "%s: %s (%.0f ms) %s" % (ns.name, rec.get("status"), rec.get("ms") or 0, rec.get("error") or json.dumps(rec.get("result"), ensure_ascii=False, default=str)[:300]))
        return 0 if rec.get("status") == "done" else 1
    if act == "trigger":
        url, call = _server_client(ns, p)
        code, j = call("POST", "/api/schedule", {"action": "run", "name": ns.name})
        _out(j, as_json, "triggered: job %s" % j.get("job") if code == 200 else "ERROR: %s" % j.get("error"))
        return 0 if code == 200 else 1
    rows = _sc.list_tasks_static()
    if act == "show":
        rows = [r for r in rows if r.get("name") == ns.name]
        if not rows:
            print("no such task: %s" % ns.name)
            return 1
        _out(rows[0], True)
        return 0
    if as_json:
        _out(rows, True)
        return 0
    print("스케줄 (%s) — 서버(serve)가 실행 중일 때 동작; 서버 없이 한 번 실행: schedule run <name>" % _sc.schedule_path())
    for r in rows:
        when = r.get("cron") or ("at %s %s" % (r.get("at"), ",".join(r.get("days") or []) or "daily") if r.get("at") else "every %s" % r.get("every"))
        print("  %s %-20s %-22s %-12s next=%s last=%s %s%s" % ("✔" if r.get("enabled", True) else "✘", r.get("name"), when, (r.get("action") or {}).get("type"),
                                                            time.strftime("%m-%d %H:%M", time.localtime(r["next_run"])) if r.get("next_run") else "-",
                                                            (time.strftime("%m-%d %H:%M", time.localtime(r["last_run"])) + " " + str(r.get("last_status"))) if r.get("last_run") else "-",
                                                            ("INVALID: " + r["invalid"]) if r.get("invalid") else "", (" ✖ " + r["last_error"][:60]) if r.get("last_error") else ""))
    if not rows:
        print("  (작업 없음) 예시: setup/schedule.example.json → schedule.json, docs/SCHEDULER.md")
    return 0


_CAPTURED = False   # Web 콘솔(run_captured)에서 실행 중이면 True — 진행 모니터의 stderr 출력을 끈다


def run_captured(argv: List[str], settings: Settings, pipe, actor: str = "web") -> Dict[str, Any]:
    """Web 콘솔용: stdout 을 캡처해 문자열로 반환. 권한은 서버(/api/cli)가 이미 판정했으므로 CLI 게이트를 타지 않는다."""
    global _CAPTURED
    buf = io.StringIO()
    code = 0
    _CAPTURED = True
    try:
        with redirect_stdout(buf):
            code = run(argv, settings, pipe, gate=False)
    except SystemExit as e:  # argparse 오류/--help
        code = int(e.code or 0)
    except Exception as e:
        buf.write("ERROR: %s: %s" % (type(e).__name__, e))
        code = 1
    finally:
        _CAPTURED = False
    return {"code": code, "output": buf.getvalue()}


def main() -> None:
    # 터미널 인코딩부터 정리한다 (한글·기호 깨짐 / UnicodeEncodeError 방지 — llmwiki/console.py 설명 참조)
    from . import console as _console
    _console.setup()
    sys.exit(run())
