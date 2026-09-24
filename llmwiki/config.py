# -*- coding: utf-8 -*-
"""설정 및 기능 토글.

사용자 설정은 <프로젝트 루트>/config.json (없으면 setup/config.example.json 을 복사해 생성) 에 있고,
API 키는 <프로젝트 루트>/.env (setup/.env.example 참조) 에 둔다. 상대 경로는 프로젝트 루트 기준으로 해석되어
다른 PC 로 복사해도 그대로 동작한다.

우선순위: 환경변수(LLMWIKI_*) > config.json > 코드 기본값
모든 기능은 Toggles 로 on/off 가능하며, CLI 플래그 / Web UI / config.json 세 곳에서 동일한 이름을 사용한다.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.environ.get("LLMWIKI_CONFIG") or os.path.join(ROOT, "config.json")
CONFIG_EXAMPLE = os.path.join(ROOT, "setup", "config.example.json")
ENV_FILE = os.environ.get("LLMWIKI_ENV_FILE") or os.path.join(ROOT, ".env")

# 기본 코퍼스: 프로젝트의 corpus/ 폴더 (상대 경로는 프로젝트 루트 기준)
DEFAULT_CORPUS_DIRS = ["corpus"]

# ---------------------------------------------------------------- 파일 경로 레지스트리
# 모든 설정/데이터 파일의 위치. 환경변수 LLMWIKI_<NAME>_PATH 로 개별 변경 가능 (예: LLMWIKI_PRESETS_PATH=D:/x/presets.json).
_PATH_DEFAULTS: Dict[str, str] = {
    "config": "config.json", "env": ".env", "tuning": "tuning.json", "presets": "presets.json",
    "query_rules": "query_rules.json", "mcp_sources": "mcp_sources.json", "agents": "agents.json",
    "pins": "pins.json", "rules": "data/rules.json", "schemas_dir": "schemas", "prompts_dir": "prompts",
    "eval": "eval/questions.json", "logs_dir": "logs", "themes": "llmwiki/web/static/themes/themes.json",
    "security": "security.json", "server": "server.json", "schedule": "schedule.json", "models": "models.json",
    "stopwords": "stopwords.json",
    # 문서 단위 접근 제어 — 어떤 역할이 어떤 문서를 근거로 볼 수 있는가 (llmwiki/docacl.py)
    "docacl": "docacl.json",
}
_PATH_ENV_ALIASES = {"config": "LLMWIKI_CONFIG", "env": "LLMWIKI_ENV_FILE", "tuning": "LLMWIKI_TUNING"}

#: `LLMWIKI_CONF_DIR` 이 가리키는 폴더 하나에 **모아 둘 수 있는** 설정들 (2026-09-19).
#:
#: 왜 이렇게 했나: "설정을 한 폴더에서 관리하는 게 낫지 않나" 라는 물음에 대한 답이다. 파일을 실제로 옮기면
#: 기존 설치·문서·스크립트·예시의 경로가 전부 깨지고, `.env` 는 도구들이 프로젝트 루트에서 찾는 관례가 있다.
#: 그래서 **옮기지 않고**, 한 폴더를 가리키면 그 폴더를 먼저 보게 한다. 폴더를 쓰지 않으면 지금과 똑같이 동작한다.
#: 포팅할 때는 그 폴더 하나만 복사하면 된다 (`config bundle` 이 모아 준다).
#:
#: 여기 없는 것: `logs_dir`·`themes`(운영 산출물·앱 자원이라 설정이 아니다), `eval`(평가셋은 코퍼스에 가깝다).
CONF_DIR_FILES = ("config", "env", "tuning", "presets", "query_rules", "mcp_sources", "agents", "pins",
                  "rules", "security", "server", "schedule", "models", "stopwords", "docacl",
                  "schemas_dir", "prompts_dir")

#: conf 폴더 안에서 쓰는 이름 (기본 경로의 basename — `data/rules.json` 은 `rules.json` 이 된다)
CONF_DIR_NAMES = {name: os.path.basename(_PATH_DEFAULTS[name]) for name in CONF_DIR_FILES}


def conf_dir() -> str:
    """설정을 모아 둔 폴더 (`LLMWIKI_CONF_DIR`). 비어 있으면 '쓰지 않음'."""
    d = os.environ.get("LLMWIKI_CONF_DIR", "").strip()
    if not d:
        return ""
    return d if os.path.isabs(d) else os.path.normpath(os.path.join(ROOT, d))


#: 테스트 묶음이 거는 **경로 대체 기본값** — 환경변수보다 약하고 `_PATH_DEFAULTS` 보다 세다 (2026-09-24, CODE_REVIEW_0924 §2.13).
#: 개별 테스트가 자기 임시 폴더를 환경변수로 걸었다가 tearDown 에서 `os.environ.pop` 으로 지우면(17개 모듈이 그렇게 한다),
#: 그 뒤의 테스트는 실사용 logs/ 로 떨어졌다 — 묶음 전체의 환경변수 기본값도 같이 지워지기 때문이다. 이 표는 pop 에 지워지지 않는다.
#: 운영 코드는 이 표를 채우지 않는다 (tests/test_00_isolate.py · tests/__init__.py 만).
_PATH_FALLBACKS: Dict[str, str] = {}


def set_path_fallback(name: str, path: str) -> None:
    """`path_for(name)` 이 환경변수가 없을 때 쓸 경로를 건다 (테스트 격리용). 빈 값이면 해제."""
    if path:
        _PATH_FALLBACKS[name] = path
    else:
        _PATH_FALLBACKS.pop(name, None)


def path_for(name: str) -> str:
    """이름으로 파일/폴더 경로 해석.

    우선순위: `LLMWIKI_<NAME>_PATH` > (구 별칭 env) > **`LLMWIKI_CONF_DIR` 안에 그 파일이 있으면 그것** >
    기본값(프로젝트 루트 기준). 개별 지정이 폴더보다 세다 — 한 파일만 다른 곳에 두는 경우가 있기 때문이다.
    conf 폴더에 **없는** 파일은 기본 위치를 그대로 쓰므로, 일부만 모아 두는 것도 된다.
    """
    env = os.environ.get("LLMWIKI_%s_PATH" % name.upper()) or os.environ.get(_PATH_ENV_ALIASES.get(name, ""), "")
    if not env and name in _PATH_FALLBACKS:
        fb = _PATH_FALLBACKS[name]
        return fb if os.path.isabs(fb) else os.path.normpath(os.path.join(ROOT, fb))
    if not env and name in CONF_DIR_NAMES:
        d = conf_dir()
        if d:
            cand = os.path.join(d, CONF_DIR_NAMES[name])
            if os.path.exists(cand):
                return cand
    p = env or _PATH_DEFAULTS[name]
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(ROOT, p))


def all_paths() -> Dict[str, str]:
    return {k: path_for(k) for k in _PATH_DEFAULTS}


def bundle(out_dir: str = "", restore_from: str = "", include_secrets: bool = False,
           dry_run: bool = False) -> Dict[str, object]:
    """설정을 **한 폴더로 모으거나**(out_dir) 그 폴더에서 되돌린다(restore_from) — 포팅용.

    모은 폴더는 `LLMWIKI_CONF_DIR` 로 바로 쓸 수 있다. 즉 옮길 때 그 폴더 하나만 들고 가면 된다.

    `include_secrets=False`(기본) 면 `.env` 는 **키 이름만 남기고 값을 비워** 복사한다 — 자격증명이
    실수로 폴더·저장소에 섞여 나가지 않게. 값은 새 환경에서 사람이 채운다.
    """
    import shutil
    if bool(out_dir) == bool(restore_from):
        return {"error": "out_dir 또는 restore_from 중 하나만 주세요"}
    src_dir = restore_from
    dst_dir = out_dir
    copied: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []
    if out_dir:
        dst = os.path.abspath(out_dir if os.path.isabs(out_dir) else os.path.join(ROOT, out_dir))
        if not dry_run:
            os.makedirs(dst, exist_ok=True)
        for name in CONF_DIR_FILES:
            src = path_for(name)
            target = os.path.join(dst, CONF_DIR_NAMES[name])
            if not os.path.exists(src):
                skipped.append({"name": name, "why": "파일이 없습니다 (기본값으로 동작 중)"})
                continue
            if name == "env" and not include_secrets:
                keys = sorted(parse_dotenv(src))
                if not dry_run:
                    with open(target, "w", encoding="utf-8") as f:
                        f.write("# 값은 비워 두었습니다 — 새 환경에서 채우세요 (config bundle --include-secrets 로 값째 복사)\n")
                        for k in keys:
                            f.write("%s=\n" % k)
                copied.append({"name": name, "to": target, "note": "키 %d개, 값은 비움" % len(keys)})
                continue
            if not dry_run:
                if os.path.isdir(src):
                    shutil.rmtree(target, ignore_errors=True)
                    shutil.copytree(src, target)
                else:
                    shutil.copyfile(src, target)
            copied.append({"name": name, "to": target})
        return {"action": "bundle", "dir": dst, "copied": copied, "skipped": skipped, "dry_run": dry_run,
                "use": "이 폴더를 쓰려면 환경변수 LLMWIKI_CONF_DIR=%s" % dst,
                "secrets": "포함" if include_secrets else "제외 (.env 는 키 이름만)"}
    # restore
    src = os.path.abspath(src_dir if os.path.isabs(src_dir) else os.path.join(ROOT, src_dir))
    if not os.path.isdir(src):
        return {"error": "폴더가 없습니다: %s" % src}
    for name in CONF_DIR_FILES:
        s = os.path.join(src, CONF_DIR_NAMES[name])
        if not os.path.exists(s):
            skipped.append({"name": name, "why": "묶음에 없습니다"})
            continue
        target = path_for(name)
        if not dry_run:
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            if os.path.isdir(s):
                shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(s, target)
            else:
                shutil.copyfile(s, target)
        copied.append({"name": name, "to": target})
    return {"action": "restore", "dir": src, "copied": copied, "skipped": skipped, "dry_run": dry_run,
            "note": "설정 파일은 되돌렸지만 **실행 중인 서버는 다시 읽지 않습니다** — 재시작하세요"}


def env_file_path() -> str:
    """.env 위치 (LLMWIKI_ENV_FILE > LLMWIKI_ENV_PATH > <ROOT>/.env). 격리 환경(tools/verify)이 파일을 바꿔 끼울 수 있게 매번 다시 본다."""
    return os.environ.get("LLMWIKI_ENV_FILE") or os.environ.get("LLMWIKI_ENV_PATH") or ENV_FILE


def parse_dotenv(path: Optional[str] = None) -> Dict[str, str]:
    """.env 파일의 KEY=VALUE 를 그대로 읽는다 (os.environ 은 건드리지 않음). 값이 빈 줄도 키로 남긴다 — 화면에 '있지만 비어 있음' 으로 보이게."""
    path = path or env_file_path()
    out: Dict[str, str] = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k:
                out[k] = v
    return out


# .env 에서 읽어 os.environ 에 넣은 키 (다시 읽기 때 '파일에서 사라진 키' 를 지우기 위해 기억한다)
_DOTENV_LOADED: Dict[str, str] = {}


def load_dotenv(path: Optional[str] = None, override: bool = False) -> Dict[str, str]:
    """.env 의 KEY=VALUE 를 os.environ 에 넣는다. 기본은 이미 설정된 값을 덮어쓰지 않는다.
    override=True(`config reload --env` · POST /api/env reload) 면 파일 값으로 덮어쓰고, 파일에서 사라진 키(예전에 .env 가 넣은 것)는 지운다.
    OS 환경변수로 미리 잡혀 있던 키는 override 여도 덮어쓰지 않는다(.env 가 넣은 적 없는 키)."""
    path = path or env_file_path()
    loaded: Dict[str, str] = {}
    vals = parse_dotenv(path)
    if override:
        # 파일에서 사라졌거나 비워진 키 가운데 예전에 .env 가 넣은 것은 지운다 (OS 가 준 값은 그대로)
        for k in list(_DOTENV_LOADED):
            if not vals.get(k) and os.environ.get(k) == _DOTENV_LOADED[k]:
                os.environ.pop(k, None)
                _DOTENV_LOADED.pop(k, None)
    for k, v in vals.items():
        if not v:
            continue                      # 빈 값(KEY=)은 '설정 안 함'
        if k not in os.environ or os.environ.get(k, "") == "" or (override and k in _DOTENV_LOADED):
            os.environ[k] = v
            loaded[k] = v
            _DOTENV_LOADED[k] = v
    return loaded


def reload_env(path: Optional[str] = None) -> Dict[str, Any]:
    """.env 를 다시 읽어 os.environ 을 파일 값으로 맞춘다 (config reload --env / /api/env reload). 반환: env_report()."""
    p = path or env_file_path()
    loaded = load_dotenv(p, override=True)
    rep = env_report(p)
    rep["reloaded"] = sorted(loaded)
    return rep


def mask_secret(v: Any) -> str:
    """비밀 값 마스킹: 'sk-…ab12' 처럼 앞 3자 + … + 뒤 4자. 짧으면 '***'. 빈 값은 ''."""
    s = str(v or "")
    if not s:
        return ""
    if len(s) <= 8:
        return "***"
    return "%s…%s" % (s[:3], s[-4:])


# .env 에 없어도 화면/CLI 에 항상 보여 줄 키 (프로바이더가 실제로 읽는 이름) — setup/.env.example 과 같은 목록
KNOWN_ENV_KEYS = ("OPENAI_API_KEY", "LLM_API_KEY", "OPENAI_EMBED_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                  "VOYAGE_API_KEY", "RERANK_API_KEY", "COHERE_API_KEY", "JINA_API_KEY", "LLMWIKI_MCP_TOKEN", "LLMWIKI_API_KEY", "PYTHONIOENCODING")


def env_report(path: Optional[str] = None) -> Dict[str, Any]:
    """.env 가시성 (admin 전용 — 값은 마스킹): 키 이름 · 설정 여부 · 마스킹 값 · 출처(file/env/both) · 활성 LLMWIKI_* 오버라이드.
    반환 {path, exists, keys:[{name, set, masked, in_file, in_env, source}], overrides:[{env, key, masked}], mtime}."""
    p = path or env_file_path()
    file_vals = parse_dotenv(p)
    names = list(file_vals)
    for k in KNOWN_ENV_KEYS:
        if k not in names:
            names.append(k)
    keys: List[Dict[str, Any]] = []
    for k in names:
        in_file = bool(file_vals.get(k))
        env_v = os.environ.get(k, "")
        in_env = bool(env_v)
        src = "both" if (in_file and in_env) else ("file" if in_file else ("env" if in_env else ""))
        if in_file and in_env and _DOTENV_LOADED.get(k) != env_v and file_vals.get(k) != env_v:
            src = "env(os 가 우선)"       # OS 환경변수가 .env 값을 이기고 있다 — 사용자가 헷갈리는 지점
        keys.append({"name": k, "set": in_env, "masked": mask_secret(env_v), "in_file": k in file_vals, "file_empty": (k in file_vals and not in_file),
                     "in_env": in_env, "source": src, "secret": _looks_secret(k)})
    overrides: List[Dict[str, Any]] = []
    for env, v in sorted(os.environ.items()):
        if not env.startswith("LLMWIKI_") or not v:
            continue
        key = env[len("LLMWIKI_"):].lower()
        if key.startswith("toggle_"):
            key = "toggles." + key[len("toggle_"):]
        elif env.endswith("_PATH") or env in ("LLMWIKI_CONFIG", "LLMWIKI_ENV_FILE", "LLMWIKI_TUNING"):
            key = "(경로) " + key
        elif key not in Settings.__dataclass_fields__ and key not in Toggles.__dataclass_fields__ and not is_role_key(key) and key not in ("corpus_dirs", "llm_roles", "log_level"):
            key = "(설정 키 아님) " + key
        overrides.append({"env": env, "key": key, "masked": mask_secret(v) if _looks_secret(env) else v[:80]})
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        mtime = None
    return {"path": p, "exists": os.path.exists(p), "keys": keys, "overrides": overrides, "mtime": mtime,
            "note": "값은 마스킹되어 있습니다. 바꾸려면 파일을 편집한 뒤 '다시 읽기'(config reload --env). OS 환경변수로 잡힌 키는 .env 로 덮어쓰지 않습니다."}


def _looks_secret(name: str) -> bool:
    n = str(name).upper()
    return any(x in n for x in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS"))


def resolve_path(p: str) -> str:
    """상대 경로 → 프로젝트 루트 기준 절대 경로. ~ 와 %VAR% 확장."""
    p = os.path.expandvars(os.path.expanduser(str(p)))
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(ROOT, p))


def portable_path(p: str) -> str:
    """ROOT 하위 경로는 상대 경로로 저장해 이식성을 확보한다."""
    try:
        rel = os.path.relpath(p, ROOT)
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except ValueError:
        pass
    return p


@dataclass
class Toggles:
    """파이프라인 단계별 on/off 스위치. 이름은 CLI 옵션(--no-fts 등)과 1:1 대응."""
    # build
    rule_graph: bool = True        # 파이썬 규칙 기반 엔티티/관계 추출
    llm_graph: bool = False        # LLM 기반 엔티티/관계 추출 (API 필요)
    embed: bool = True             # 벡터 임베딩 생성
    communities: bool = True       # 그래프 커뮤니티 탐지 (label propagation)
    community_summary: bool = False  # 커뮤니티 요약 (LLM, 옵션)
    wiki_pages: bool = True        # 엔티티 위키 페이지 생성
    incremental: bool = True       # 문서 해시 기반 증분 빌드
    # query
    fts: bool = True
    vector: bool = True
    graph: bool = True
    router: bool = True            # 적응형 검색 라우터 (질의 유형별 가중치)
    rerank: bool = True            # 리랭킹 (LLM 가능 시 LLM, 아니면 로컬 휴리스틱)
    llm_answer: bool = True        # LLM 답변 생성 (불가 시 추출식 답변 폴백)
    # evolve
    evolve_capture: bool = True    # 질의/피드백/갭 자동 기록
    evolve_auto_apply: bool = False  # 제안 자동 승인 (기본 HITL)
    # ---- 성능/속도/토큰 개선 토글 (build) ----
    stat_skip: bool = True         # 파일 mtime/size 가 같으면 읽지도 해시하지도 않음 (대규모 코퍼스 증분 빌드 핵심)
    idf_refit_incremental: bool = False  # 증분 빌드에서도 hash IDF 재적합 + 전체 재임베딩 (기본: 전체 빌드만)
    incremental_communities: bool = False  # 증분 빌드에서도 커뮤니티 재탐지 (기본: 전체 빌드만; 그래프가 크면 느림)
    wiki_full_rewrite: bool = False  # 증분 빌드에서도 모든 위키 페이지 재작성 (기본: 변경 엔티티만)
    warm_cache: bool = True        # 빌드 직후 벡터 행렬/엔티티 인덱스를 미리 적재 (첫 질의 지연 제거)
    fts_optimize: bool = True      # 전체 빌드 후 FTS5 세그먼트 병합(optimize) + PRAGMA optimize
    # ---- 성능/속도/토큰 개선 토글 (query) ----
    rerank_llm: bool = True        # rerank 시 LLM 사용 (끄면 로컬 휴리스틱만 → 토큰 0)
    # 기본 off (2026-09-16): 캐시가 켜져 있으면 설정을 바꿔 가며 확인할 때 **바뀐 설정이 반영되지 않은 예전 답**이
    # 그대로 돌아와 "고쳤는데 그대로다" 로 보인다. 토큰을 아끼려면 운영에 올린 뒤 켠다.
    query_cache: bool = False      # 동일 질의+설정+빌드버전 결과 캐시 (LLM 토큰 절약)
    # 2026-09-19: 질의를 벡터로 바꾼 결과를 내용(sha1)으로 캐시. 원격 임베더에서 질의 하나가 2초 넘게 걸리고
    # 규칙 대체 질의까지 4번 임베딩하므로, 같은 질문을 반복하는 회귀 평가·trial·스윕이 특히 느렸다.
    embed_query_cache: bool = True
    context_trim: bool = True      # 컨텍스트 청크를 질의 관련 문장 위주로 압축 (입력 토큰 절감)
    dedupe_hits: bool = True       # 같은 문서의 겹치는 청크(오버랩) 중복 제거
    # ---- 질의 확장/검증/루프 (v3) ----
    query_rules: bool = True       # 규칙 기반 어휘 확장 (query_rules.json: acronym/synonym/alias/related/exclude)
    time_scope: bool = True        # 한국어 상대 시간 표현("지난주") → 날짜 범위 boost/filter
    query_expand: bool = False     # LLM 질의 확장 (원 질의 유지 + 추가 질의 생성)
    query_decompose: bool = False  # LLM 다중 홉 질의 분해 (sub-query 를 추가 검색)
    evidence_check: bool = True    # 근거 충분성 판정 (휴리스틱)
    evidence_check_llm: bool = False  # 근거 충분성 판정에 LLM 사용
    fallback_loop: bool = False    # 근거 부족 시 단계적 확장 검색 (L1 규칙 → L2 LLM 확장 → L3 그래프 → L4 광역)
    claim_check: bool = True       # 답변 문장별 인용·지원 검증 (휴리스틱)
    claim_check_llm: bool = False  # claim 지원 검증에 LLM 사용
    answer_refine: bool = False    # 미지원 문장을 LLM 이 1회 재작성
    evidence_compress: bool = False  # LLM 이 근거 문단에서 질문 관련 문장만 선택 (토큰↓)
    router_llm: bool = False       # LLM 라우터 (의도/문서유형 분류) — 휴리스틱 라우터 보완
    pins: bool = True              # pins.json 의 고정 근거 주입
    precompute: bool = False       # 사전 계산 답변 캐시(answer_cache) 사용 — 기본 off (query_cache 와 같은 이유)
    doc_vector: bool = False       # 문서 카드 임베딩 채널 (문서 단위 검색)
    feedback_boost: bool = False   # 긍정 피드백 청크 boost (decay)
    forensic_auto: bool = True     # 근거 부족/미지원 답변 시 포렌식 자동 기록
    collab: bool = True            # Web UI 협업(휘발성 채팅 + 게시판). 끄면 화면에서 사라지고 API 는 404 — 본체와 무관
    # 질의마다 단계별 중간 결과(순서·점수·컨텍스트·답변)를 파일로 남겨 **특정 단계부터 다시 돌릴 수 있게** 한다.
    # 청크 본문은 저장하지 않으므로 보통 수십 KB 다. 끄면 재실행 버튼이 "저장된 중간 결과 없음" 으로 막힌다.
    rerun_capture: bool = True     # 단계 재실행용 중간 결과 저장 (docs/RERUN.md)
    mcp_sources: bool = False      # 외부 MCP 소스(mcp_sources.json) 사용 (빌드 ingest / 질의 enrich)
    external_rag: bool = False     # 외부 RAG(mcp_sources.json retrieve 매핑) 결과를 검색 채널 ext_<source> 로 융합
    mcp_federation: bool = False   # mcp_sources.json 에서 expose 한 외부 서버 tool 을 우리 MCP tools/list 에 <source>__<tool> 로 노출·중계
    # ---- 빌드 (v3) ----
    embed_adaptive: bool = True    # 임베딩 배치 크기 자동 조절 + WAL 체크포인트
    fts_trigram: bool = False      # 한글 trigram 폴백 테이블 생성/사용 (색인 크기↑, 0-hit 폴백에만 사용)
    schema_lint: bool = True       # 빌드 시 문서 스키마(front matter) 검사 리포트
    explicit_relations: bool = True  # front matter related.* / ID 패턴 규칙으로 결정적 관계 생성
    precompute_after_build: bool = False  # 빌드 후 답변 사전 계산 실행
    verify_after_build: bool = True  # 빌드 후 색인 정합성 검증(요약)
    # ---- evolve (v3) ----
    memory_decay: bool = True      # 제안/규칙 강도의 시간 감쇠·강화 (memory decay 잡)
    evolve_from_forensics: bool = True  # 누적 포렌식에서 제안 생성
    # ---- 시스템 ----
    auto_build: bool = False       # 서버 실행 중 코퍼스 폴더를 주기적으로 스캔해 증분 빌드 (auto_build_interval 초)
    health_check: bool = True      # 빌드 시작 전 프로바이더/DB/디스크/코퍼스 health 검사 (실패 항목이 있으면 중단, --force 로 강행)
    log_stages: bool = True        # 프로파일 단계 종료를 logs/ 에 기록 (정상 동작 로그)
    profile_expansion: bool = False  # [디버그] 규칙/LLM 확장 전·후 검색을 모두 실행해 확장 효과를 프로파일에 기록 (지연↑)
    # [보안] 검색해 온 문서 본문을 구획(<<<C1>>>)으로 감싸고, 구조를 흉내 내는 조각(## 질문 · system: · [C3])의 표시를 바꾼다.
    # 위키 문서에 "이전 지시를 무시하고…" 같은 문장이 있어도 모델이 그것을 지시로 읽지 않게 한다 (llmwiki/ctxguard.py).
    context_guard: bool = True
    # [보안] 문서 단위 접근 제어 — docacl.json 의 규칙과 문서 front matter 의 acl 로 근거를 거른다.
    # 규칙이 비어 있으면 아무도 막지 않는다 (기본 동작은 예전과 같다). admin 은 항상 전부 본다.
    doc_acl: bool = True
    # ---- 2026-09-14: 채널 빌드 · 문서 단위 확장 · LLM 실패 보고 ----
    build_fts: bool = True         # 빌드에서 FTS 색인(chunks_fts) 을 쓴다. 끄면 FTS 채널이 stale (build fts 로 따로 만들 때)
    doc_expand: bool = True        # 리랭크 후 상위 문서의 나머지 청크 중 질의 관련 청크를 컨텍스트에 추가 (문서 단위 확장)
    llm_failure_report: bool = True  # LLM 호출이 재시도 후에도 실패하면 결과(llm_report)와 답변 상단에 상황·대체 경로를 보고
    analysis_mode: bool = False    # 상세 분석 모드: 질의를 debug_level 2 로 실행하고 모든 단계 결과를 logs/analysis/req_<id>.md 리포트로 남김 (품질/속도/토큰 렌즈)
    # ---- 2026-09-18: RRF 뒤 LLM 두 단계 · LLM 실패 시 대체 경로 (docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918.md §2.5 · §0.2) ----
    llm_after_fusion: bool = False   # 융합·부스트 직후(리랭크 전) LLM(역할 fusion, prompts/fusion_review.md) 이 후보를 검토해 무관한 것을 감점/제거 (trace fusion_llm)
    llm_after_rerank: bool = False   # 리랭크 직후(문서 확장·컨텍스트 전) LLM(역할 select, prompts/rerank_review.md) 이 컨텍스트에 넣을 청크와 통째로 읽을 문서를 고른다 (trace rerank_review_llm)
    degrade_on_llm_failure: bool = True  # 답변 LLM 이 재시도 뒤에도 실패하면 추출식 답변으로 계속(true). false 면 result_type=error 로 끝내고 llm_report 로만 보고


@dataclass
class Settings:
    corpus_dirs: List[str] = field(default_factory=lambda: list(DEFAULT_CORPUS_DIRS))
    # 코퍼스 안에 있어도 **색인하지 않을** 경로 패턴 (doc_id = 코퍼스 루트 기준 상대 경로).
    # 예: ["imported/llmwiki/", "imported/js/", "**/NOTE-*.md"]. 빈 목록이면 전부 색인한다.
    # 왜 필요한가: 색인하면 안 되는 것이 코퍼스에 섞이면 도메인 문서를 밀어낸다. 실측으로
    # 이 도구 자신의 소스 150개를 제외했더니 hit@k 0.64 → 0.88, MRR 0.396 → 0.676 이었다.
    corpus_exclude: List[str] = field(default_factory=list)
    data_dir: str = "data"
    wiki_dir: str = "wiki"
    db_name: str = "llmwiki.sqlite3"
    # chunking
    chunk_max_chars: int = 900
    chunk_overlap_chars: int = 120
    # retrieval
    top_k_fts: int = 12
    top_k_vector: int = 12
    top_k_graph: int = 12
    top_k_final: int = 8
    graph_hops: int = 2
    rrf_k: int = 60
    # providers
    llm_provider: str = "auto"     # auto | anthropic | ollama | mock | none
    llm_model: str = "claude-opus-5"
    llm_effort: str = "low"        # 추출/리랭크 effort
    answer_effort: str = "medium"  # 답변 생성 effort
    # 답변 모드 (요청 단위 오버라이드 · CLI --answer-mode · MCP wiki_query(answer_mode=) · Ask 드롭다운) — docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918.md §2.9
    #   grounded    = 문서 근거만으로 답한다. 근거 판정이 insufficient 면 LLM 을 부르지 않고 '근거 부족' 응답 (예전 동작)
    #   best_effort = 근거가 부족해도 LLM 을 부른다(prompts/answer_best_effort.md). 문서 사실은 [C#], 배경 지식 문장은 [BK] 로 표시
    answer_mode: str = "grounded"  # grounded | best_effort
    # 출력 모드 (요청 단위 오버라이드 · CLI --output · MCP wiki_query(output_mode=) · Ask 출력 드롭다운) — docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md §2.3
    #   answer = 끝까지(기본) · fused = 융합·부스트 뒤 후보(리랭크 전) · reranked = 리랭크 뒤 후보 · context = 컨텍스트까지(답변 LLM 생략)
    output_mode: str = "answer"    # answer | fused | reranked | context
    llm_fallbacks: bool = True     # anthropic server-side refusal fallback
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    embed_provider: str = "auto"   # auto | hash | voyage | ollama | st
    embed_model: str = ""          # voyage: voyage-3.5 / ollama: nomic-embed-text / st: 모델명
    embed_dim: int = 4096          # hash 임베딩 차원
    # 역할별 LLM (비우면 llm_provider/llm_model/llm_effort 를 사용). 역할: answer | rerank | extract | summary | review | expand | verify | forensic
    #   예) {"rerank": {"provider": "ollama", "model": "llama3.1", "effort": "low"}, "answer": {"model": "claude-opus-5"}}
    #   provider 를 비우고 model 만 적으면: models.json 카탈로그에 그 model 이 한 provider 로만 등록돼 있을 때 그 provider 를 쓴다
    #   (role_llm()['provider_source']=catalog). 전역 llm_provider 가 mock/none 이면 카탈로그 해석을 하지 않는다(테스트/오프라인 보호).
    #   앙상블(요청 6, docs/ENSEMBLE.md): llm_roles.<role>.ensemble = {
    #     "enabled": false,                                   // true 면 이 역할의 모든 LLM 호출이 멤버 병렬 + 취합으로 바뀐다
    #     "members": [ {"enabled": true, "provider": "anthropic", "model": "claude-sonnet-5", "weight": 1.5, "effort": ""}, … ],   // 최대 3개
    #     "wait": "all" | "timeout", "timeout_s": 120, "min_results": 1,   // 생략하면 llm_ensemble_defaults
    #     "aggregator": {"provider": "", "model": "", "effort": ""},        // 취합 LLM. model 을 비우면 첫 멤버가 취합
    #     "prompt": "ensemble_merge" }                                      // prompts/<prompt>.md — 취합 규칙·가중치 해석을 여기에 적는다
    #   멤버 provider 를 비우면 (카탈로그 → 역할 provider) 순으로 정한다. weight 는 취합 프롬프트에 그대로 전달되는 참고값이다.
    llm_roles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # 앙상블 전역 기본값 — 역할의 ensemble 에서 생략한 키가 상속한다.
    #   wait: all(모든 멤버가 끝날 때까지, 멤버별 timeout_s 가 상한) | timeout(timeout_s 지나면 도착한 결과만으로 취합)
    #   timeout_s: wait=timeout 일 때 대기 상한(초) · min_results: 이보다 적게 성공하면 앙상블 실패(단일 LLM 실패와 같은 대체 경로)
    #   prompt: 취합 LLM 프롬프트 파일 이름 (prompts/ensemble_merge.md)
    llm_ensemble_defaults: Dict[str, Any] = field(default_factory=lambda: {"wait": "all", "timeout_s": 120, "min_results": 1, "prompt": "ensemble_merge"})
    # 토큰/속도 관련 상세 설정
    rerank_candidates: int = 16    # 리랭크 후보 수 (LLM 리랭크 입력 크기)
    rerank_chunk_chars: int = 600  # 리랭크 프롬프트에 넣는 청크당 최대 글자수
    context_max_chars: int = 9000  # 답변 컨텍스트 최대 글자수
    context_chunk_chars: int = 1200  # context_trim 시 청크당 최대 글자수
    answer_max_tokens: int = 3000
    llm_graph_budget: int = 0      # 빌드당 LLM 추출 호출 상한 (0 = 무제한)
    llm_graph_min_chars: int = 80  # 이보다 짧은 청크는 LLM 추출 생략
    # 그래프 진단 프로파일 (`graph profile` · /api/graph/profile · wiki_graph_profile — docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md §2.5)
    graph_profile_keep: int = 30       # data/graph_profiles/gp_<ts>.json 보관 개수 (--compare 가 직전 것과 비교)
    graph_profile_requests: int = 200  # '질의 활용' 절이 보는 최근 질의 요청 수
    graph_profile_hubs: int = 10       # 허브 상위 N
    query_cache_size: int = 200
    # ---- 요청 이력 보관 (2026-09-16) ----
    # requests 테이블은 keep_requests 행으로 잘린다. 사용자가 "그때 그 답" 을 다시 보려면 결과가 남아 있어야 하므로
    # 결과/trace 를 DB 밖 파일로도 남긴다. 비우면 파일 보관을 하지 않는다(예전 동작).
    requests_dir: str = "data/requests"    # <data_dir> 기준 상대 경로 또는 절대 경로. 월별 폴더 + req_<id>.json
    requests_keep_days: int = 90           # 보관 파일 보존 기간(일). 0 = 지우지 않음. 정리: `maintenance prune_requests`
    # 단계 재실행용 중간 결과 (toggles.rerun_capture) — docs/RERUN.md
    rerun_dir: str = "data/reruns"         # <data_dir> 기준 상대 경로 또는 절대 경로. req_<request_id>.json
    rerun_keep: int = 50                   # 최근 몇 건을 남길지. 0 = 무제한(권장하지 않음)
    rerun_max_mb: float = 4.0              # 한 건의 상한(MB). 넘으면 저장하지 않고 trace 에 이유를 남긴다
    # 파라미터 스윕 (rerun 재생 위에서 값만 바꿔 N회) — llmwiki/sweep.py · docs/SWEEP.md
    sweep_dir: str = "data/sweeps"         # <data_dir> 기준 상대 경로 또는 절대 경로. sw_<id>.json
    sweep_keep: int = 30                   # 최근 몇 건을 남길지. 0 = 무제한
    sweep_max_values: int = 20             # 한 스윕의 값 개수 상한 (LLM 호출 폭주 방지)
    sweep_max_parallel: int = 1            # 값을 동시에 몇 개 돌릴지. 1 = 순차 (LLM 한도가 넉넉할 때만 올린다)
    # 확장/운영
    auto_build_interval: int = 300  # auto_build 스캔 주기(초)
    embed_batch: int = 64
    debug_level: int = 1           # 프로파일 상세도: 0 요약만 · 1 디버그 메타/로그 · 2 프롬프트/응답 원문 샘플까지
    keep_requests: int = 2000      # requests 테이블 보존 개수 (초과분 자동 삭제)
    # evolve
    evolve_min_confidence: float = 0.8
    evolve_low_score_threshold: float = 0.05
    # 자동 적용을 허용할 제안 종류 (비우면 evolve.AUTO_APPLY_KINDS_DEFAULT — 되돌리기 쉬운 것만)
    evolve_auto_apply_kinds: List[str] = field(default_factory=list)
    evolve_snapshot_keep: int = 20   # 적용마다 만드는 자동 스냅샷 보관 개수 (0 = 무제한)
    # ---- 지역/시간 ----
    timezone: str = "Asia/Seoul"   # 상대 시간 표현("지난주") 해석 기준 시간대 (IANA 이름)
    week_start: str = "mon"        # 주의 시작 요일 (mon | sun)
    # ---- 터미널 출력 (다른 환경에서 한글/기호 깨짐 방지 — llmwiki/console.py) ----
    console_encoding: str = "auto"      # auto | utf-8 | native | off. auto = Windows 콘솔이면 코드페이지를 UTF-8 로 바꾸고, 리디렉션이면 UTF-8 로 출력
    console_set_codepage: bool = True   # Windows 콘솔의 출력 코드페이지를 UTF-8(65001)로 바꿀지 (종료 시 원래대로 복구)
    # ---- 로그 (logs/ 폴더) ----
    log_level: str = "INFO"        # DEBUG | INFO | WARNING | ERROR
    log_max_mb: int = 10           # 파일당 최대 MB (로테이션)
    log_backups: int = 10          # 보관 파일 수
    log_console: bool = False      # 콘솔에도 WARNING 이상 출력
    log_total_max_mb: int = 500    # logs/ 폴더 총량 상한(MB, 로그+audit+analysis 합계). 0 = 제한 없음
    log_limit_action: str = "warn" # 총량 초과 시: warn(error.log 경고+health) | prune(오래된 백업·리포트 삭제→80%) | stop(error.log 만 남기고 기록 중단)
    log_check_interval_s: int = 60 # 총량 점검 주기(초). 0 = 매 레코드마다(테스트용)
    audit_max_mb: int = 20         # logs/audit.jsonl 파일당 최대 MB (초과 시 audit.jsonl.1 … 로 로테이션)
    audit_backups: int = 5         # audit.jsonl 로테이션 보관 개수
    analysis_keep: int = 200       # logs/analysis 리포트(md+json 한 쌍 = 1건) 보관 개수. 0 = 무제한
    # ---- OpenAI-compatible / rerank 엔드포인트 (키는 .env: OPENAI_API_KEY(또는 LLM_API_KEY), RERANK_API_KEY) ----
    openai_base_url: str = "http://localhost:11434/v1"   # vLLM · LM Studio · Ollama(OpenAI 호환) · OpenRouter · 사내 게이트웨이(PAT)
    openai_api_key_header: str = "authorization"   # PAT 를 싣는 헤더. authorization(→ "Bearer <key>") | api-key | x-api-key | 임의 헤더명(값은 키 그대로)
    openai_extra_headers: Dict[str, str] = field(default_factory=dict)   # 게이트웨이가 요구하는 고정 헤더 (예 {"X-Tenant": "modem"})
    openai_embed_base_url: str = ""   # 임베딩 전용 base URL (비우면 openai_base_url 과 같음)
    openai_embed_model: str = ""   # embed_provider=openai 일 때 모델명 (비우면 embed_model)
    anthropic_base_url: str = ""   # Anthropic 호환 게이트웨이(PAT). 비우면 https://api.anthropic.com. 키는 ANTHROPIC_API_KEY(x-api-key) 또는 ANTHROPIC_AUTH_TOKEN(Bearer)
    rerank_url: str = ""           # rerank_method=api 일 때 엔드포인트 (예 http://localhost:8000/v1/rerank)
    rerank_api_model: str = ""     # rerank API 모델명 (예 BAAI/bge-reranker-v2-m3, rerank-2). ※ rerank_model 은 '역할 rerank 의 LLM 모델' 단축키
    rerank_api_style: str = "cohere"   # cohere(=jina/vLLM) | voyage
    # ---- 임베딩 실행 제어 (품질과 무관, 실행 안정성) ----
    embed_batch_max: int = 256     # 적응형 배치 상한
    embed_batch_target_ms: int = 8000   # 배치 지연이 이보다 크면 배치 축소
    embed_commit_every: int = 10   # N 배치마다 commit + 진행률 저장 (중단 후 재개 단위)
    wal_checkpoint_mb: int = 64    # 빌드 중 WAL 이 이보다 크면 체크포인트
    embed_store_dtype: str = "float32"   # float32 | float16 (저장·행렬 메모리 절반, 유사도 오차 미미)
    build_lock_timeout: int = 172800   # 다른 빌드가 락을 잡고 있을 때 기다릴 초 (0 = 즉시 실패). 기본 48시간 = 앞 빌드가 끝날 때까지 기다린다
    build_lock_stale_s: int = 172800   # 빌드 락을 '주인이 죽었다'고 보고 회수하기까지의 시간(초). 빌드 자체의 최대 수명이므로 가장 긴 빌드보다 길어야 한다
    db_busy_timeout_s: float = 60.0   # SQLite 쓰기 잠금 대기(초). 다른 프로세스(CLI 빌드·서버 워처)가 쓰는 동안 기다리는 시간. 초과하면 관측용 기록(요청 로그)은 건너뛰고 질의는 정상 응답한다
    # 2026-09-23: 커밋 내구성. WAL 에서 NORMAL 은 **DB 손상이 없고**(체크포인트에서만 fsync) 정전 시 최근 몇 건의
    # 커밋만 날아간다 — 그 내용은 색인·관측 기록이라 재빌드로 복구된다. FULL 은 커밋마다 fsync 라,
    # 질의 1건이 커밋을 여러 번 하는 이 파이프라인에서는 동시 질의가 몰릴 때 그대로 지연이 된다.
    db_synchronous: str = "NORMAL"    # OFF | NORMAL | FULL | EXTRA
    db_pool_size: int = 16         # 서버가 스레드별로 재사용하는 SQLite 읽기 연결 풀 크기 (동시 질의 수 이상이면 충분)
    # 2026-09-19: pool_size 는 '놀고 있는' 연결만 제한한다. 아래 둘은 **빌려 나가 있는** 연결의 부드러운 상한 —
    # 세션을 닫지 않는 코드가 생겼을 때 연결이 무한히 늘지 않게 한다. 넘어도 질의를 실패시키지는 않는다.
    db_max_live_connections: int = 64   # 0 = 상한 없음(예전 동작). 정상 운영에서는 걸리지 않는 값 — 걸리면 누수 신호다
    db_pool_wait_timeout_s: float = 2.0  # 상한을 넘었을 때 반납을 기다리는 시간(초). 지나면 만들어서라도 진행하고 overflow 로 센다
    llm_timeout: int = 600         # LLM 호출 1회의 HTTP 타임아웃(초). 응답이 없으면 이 시간 뒤 실패로 처리(재시도 포함). headless 는 agents.json timeout_s 우선
    llm_retries: int = 3           # LLM 호출이 timeout/네트워크/실행 실패(transient)면 재시도할 횟수 (최대 1+llm_retries 회). headless 는 agents.json retries 우선
    llm_retry_backoff_s: float = 2.0   # 재시도 사이 기본 대기(초). llm_retry_backoff=linear 면 × 시도 번호, exponential 이면 × 2^(시도-1)
    # ---- 역할별 LLM 정책 기본값 (llm_roles.<role>.{timeout_s,retries,backoff_s,backoff,backoff_max_s,budget_s,circuit_failures,circuit_cooldown_s} 가 우선) ----
    llm_retry_backoff: str = "exponential"   # 재시도 대기 증가 방식: linear | exponential (+ 최대 20% 지터)
    llm_retry_backoff_max_s: float = 60.0    # 재시도 대기 상한(초)
    llm_budget_s: int = 0          # 한 호출의 재시도까지 포함한 총 시간 예산(초). 넘으면 더 재시도하지 않고 실패 → 대체 경로. 0 = 제한 없음
    # 반복 억제: 작은 모델이 같은 구절을 수십 번 되풀이하는 고장을 줄인다 (0 = 보내지 않음).
    # OpenAI 호환 게이트웨이는 frequency/presence_penalty, Ollama 네이티브는 repeat_penalty 로 전달된다.
    llm_frequency_penalty: float = 0.3
    llm_presence_penalty: float = 0.0
    llm_repeat_penalty: float = 1.1   # Ollama 네이티브(/api/generate) 전용. 1.0 = 억제 없음
    llm_http_retries: int = 2      # HTTP 429/5xx 에 대한 프로바이더 내부 짧은 재시도 횟수 (llm_retries 와 별도, 총 시도 ≤ (1+llm_retries)×(1+llm_http_retries))
    llm_circuit_failures: int = 3  # 같은 provider/model 이 연속으로 이 횟수 실패하면 회로를 열어(circuit open) 그 뒤 호출은 즉시 실패시킨다 (0 = 끔)
    llm_circuit_cooldown_s: int = 60   # 회로가 열린 뒤 다시 시도해 보기까지의 대기(초). 다수 사용자가 죽은 엔드포인트에 각각 timeout×재시도만큼 기다리는 것을 막는다
    # ---- 서버·MCP 기본값 (serve / mcp 명령의 플래그를 생략하면 여기 값; 플래그가 우선) ----
    # serve 바인드 주소. 기본 0.0.0.0 = 같은 네트워크의 동료가 바로 접속 (2026-09-18). 이때 security.json mode=auto 는
    # 로그인을 **켜므로** 첫 admin(`users add <id> --role admin`) 이 있어야 한다. 혼자 쓰는 PC 면 127.0.0.1.
    web_host: str = "0.0.0.0"
    web_port: int = 8765           # serve 포트 (Web UI + POST /mcp)
    mcp_transport: str = "stdio"   # mcp 명령 기본 전송: stdio(같은 PC 클라이언트가 자식 프로세스로) | http(단독 MCP HTTP 서버)
    mcp_host: str = "127.0.0.1"    # mcp --transport http 바인드 주소
    mcp_port: int = 8766           # mcp --transport http 포트
    mcp_url: str = ""              # 브리지 대상 URL (예 http://wiki-host:8765/mcp). 비어 있지 않으면 `mcp` 는 stdio→원격 HTTP 브리지로 동작. 토큰은 .env LLMWIKI_MCP_TOKEN
    mcp_plugins_dir: str = "plugins/mcp_tools"   # MCP 플러그인 도구 폴더 (*.py 의 register(add_tool)). 상대 경로는 프로젝트 루트 기준
    toggles: Toggles = field(default_factory=Toggles)

    # fusion = 융합 뒤 후보 검토(toggles.llm_after_fusion) · select = 리랭크 뒤 컨텍스트 선택(toggles.llm_after_rerank) — 2026-09-18
    LLM_ROLES = ("answer", "rerank", "extract", "summary", "review", "expand", "verify", "forensic", "fusion", "select")
    # 역할별로 지정할 수 있는 속성 (llm_roles.<role>.<attr>, 단축키 <role>_<attr>, 환경변수 LLMWIKI_<ROLE>_<ATTR>)
    LLM_ROLE_ATTRS = ("provider", "model", "effort", "timeout_s", "retries", "backoff_s", "backoff", "backoff_max_s", "budget_s",
                      "circuit_failures", "circuit_cooldown_s", "ensemble")   # ensemble 은 dict (단축키·환경변수로는 JSON 문자열)
    # 앙상블 기본값 (llm_ensemble_defaults 가 비어 있거나 키가 빠졌을 때)
    # fallback_role_model: 멤버가 min_results 를 못 채워 앙상블이 실패했을 때 **역할 모델로 한 번 더** 시도할지.
    #   켜 두면 "앙상블을 켠 탓에 답이 아예 안 나오는" 경우가 없어진다 — 앙상블 이전 동작으로 조용히 되돌아간다.
    #   끄면 예전처럼 LLMError 로 끝나고 호출부의 대체 경로(answer 는 추출식)로 간다.
    # fallback_mode: 그때 역할 모델이 **무엇을 받을지**.
    #   auto(기본) = 성공한 멤버 답이 있으면 그것들을 취합(merge), 하나도 없으면 원래 프롬프트로 다시(rerun)
    #   merge      = 되도록 살아남은 답을 취합한다 (하나도 없으면 취합할 것이 없으므로 rerun 으로 떨어진다)
    #   rerun      = 멤버 답을 쓰지 않고 항상 원래 프롬프트로 처음부터 다시 돈다
    # 왜 고르게 하나: merge 는 이미 쓴 토큰을 살리고 빠르지만 실패한 답에 끌려갈 수 있고,
    #   rerun 은 깨끗한 답을 얻지만 컨텍스트를 다시 넣어 비용·시간이 더 든다. 환경마다 답이 다르다.
    ENSEMBLE_FALLBACK_MODES = ("auto", "merge", "rerun")
    ENSEMBLE_DEFAULTS = {"wait": "all", "timeout_s": 120, "min_results": 1, "prompt": "ensemble_merge",
                         "fallback_role_model": True, "fallback_mode": "auto"}
    ENSEMBLE_MAX_MEMBERS = 3
    LLM_ROLE_POLICY_ATTRS = ("timeout_s", "retries", "backoff_s", "backoff", "backoff_max_s", "budget_s",
                             "circuit_failures", "circuit_cooldown_s", "max_tokens")

    def _catalog_provider(self, model: str) -> Optional[str]:
        """models.json 에서 model 이 한 provider 로만 등록돼 있으면 그 provider (계획 §0.1-b). 카탈로그 오류는 None."""
        try:
            from . import models_catalog as _mc      # 지연 import: models_catalog 가 config 를 import 한다
            return _mc.provider_for(model)
        except Exception:
            return None

    def _resolve_provider(self, explicit: Any, model: Any, fallback: str, fallback_source: str) -> Tuple[str, str]:
        """(provider, provider_source). explicit 가 있으면 그것(source=fallback_source 의 '명시' 형태), 없으면 카탈로그 → fallback.
        전역 provider 가 mock/none 이면 카탈로그 해석을 하지 않는다 — 테스트·오프라인 설정이 카탈로그 때문에 네트워크 provider 로 바뀌지 않게."""
        p = str(explicit or "").strip()
        if p:
            return p, fallback_source
        model = str(model or "").strip()
        if model and str(self.llm_provider or "") not in ("mock", "none"):
            cp = self._catalog_provider(model)
            if cp and cp != fallback:
                return cp, "catalog"
        return fallback, "global"

    def effective_ensemble(self, role: str) -> Dict[str, Any]:
        """llm_roles.<role>.ensemble 에 llm_ensemble_defaults 를 합치고 값을 정규화한 dict.
        members 는 enabled=false 이거나 model 이 빈 항목을 뺀 뒤 최대 ENSEMBLE_MAX_MEMBERS 개. 멤버 provider 가 비면 카탈로그 → 역할 provider.
        enabled 는 원래 값 AND 활성 멤버 ≥ 1. 반환 예:
        {"enabled": bool, "members": [{"enabled": true, "provider", "model", "weight": float, "effort": "", "provider_source"}],
         "wait": "all|timeout", "timeout_s": int, "min_results": int, "aggregator": {"provider", "model", "effort"}, "prompt": str}"""
        r = dict((self.llm_roles or {}).get(role) or {})
        raw = r.get("ensemble")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else {}
            except ValueError:
                raw = {}
        raw = raw if isinstance(raw, dict) else {}
        d = dict(self.ENSEMBLE_DEFAULTS)
        d.update({k: v for k, v in (self.llm_ensemble_defaults or {}).items() if v not in (None, "")})
        d.update({k: v for k, v in raw.items()
                  if k in ("wait", "timeout_s", "min_results", "prompt", "fallback_role_model", "fallback_mode")
                  and v not in (None, "")})
        role_provider, _ = self._resolve_provider(r.get("provider"), r.get("model"), str(self.llm_provider), "role")
        members: List[Dict[str, Any]] = []
        for m in (raw.get("members") or []):
            if not isinstance(m, dict):
                continue
            model = str(m.get("model") or "").strip()
            if not model or not _to_bool(m.get("enabled", True)):
                continue
            prov, src = self._resolve_provider(m.get("provider"), model, role_provider, "member")
            if src == "global":
                src = "role"
            try:
                w = float(m.get("weight", 1.0) if m.get("weight", 1.0) not in (None, "") else 1.0)
            except (TypeError, ValueError):
                w = 1.0
            members.append({"enabled": True, "provider": prov, "model": model, "weight": w, "effort": str(m.get("effort") or ""), "provider_source": src})
            if len(members) >= self.ENSEMBLE_MAX_MEMBERS:
                break
        agg_raw = raw.get("aggregator") if isinstance(raw.get("aggregator"), dict) else {}
        agg_model = str(agg_raw.get("model") or "").strip()
        agg_prov, agg_src = self._resolve_provider(agg_raw.get("provider"), agg_model, role_provider, "aggregator") if agg_model else ("", "")
        try:
            timeout_s = int(float(d.get("timeout_s") or 0))
        except (TypeError, ValueError):
            timeout_s = int(self.ENSEMBLE_DEFAULTS["timeout_s"])
        try:
            min_results = max(1, int(float(d.get("min_results") or 1)))
        except (TypeError, ValueError):
            min_results = 1
        wait = "timeout" if str(d.get("wait") or "all").lower().startswith("time") else "all"
        # 앙상블이 실패했을 때 돌아갈 역할 모델. 화면·CLI 가 "무엇으로 되돌아가는지" 를 그대로 보여 줄 수 있게 함께 싣는다.
        fb_on = bool(_to_bool(d.get("fallback_role_model", True)))
        fb_mode = str(d.get("fallback_mode") or "auto").strip().lower()
        if fb_mode not in self.ENSEMBLE_FALLBACK_MODES:
            fb_mode = "auto"
        role_model = str(r.get("model") or self.llm_model or "")
        return {"enabled": bool(_to_bool(raw.get("enabled", False))) and bool(members), "members": members,
                "wait": wait, "timeout_s": timeout_s, "min_results": min_results,
                "aggregator": {"provider": agg_prov, "model": agg_model, "effort": str(agg_raw.get("effort") or ""), "provider_source": agg_src if agg_src != "global" else "role"},
                "fallback_role_model": fb_on, "fallback_mode": fb_mode,
                "fallback": ({"provider": role_provider, "model": role_model, "mode": fb_mode} if (fb_on and role_model) else None),
                "prompt": str(d.get("prompt") or "ensemble_merge")}

    def ensemble_time_budget(self, role: str) -> Dict[str, Any]:
        """이 역할의 앙상블이 **최악의 경우 몇 초** 걸리는지 (2026-09-20).

        왜 계산해서 보여 주나: 곱셈이 눈에 안 보인다. 멤버 하나가 `timeout_s` 만큼 걸리는 것이 아니라
        **(1+retries)회 × timeout_s + 백오프** 다. 거기에 실패하면 폴백이 **같은 정책으로 한 번 더** 돈다.
        기본값(llm_timeout=600 · llm_retries=3)이면 한 단계가 최악 80분이 되는데, 화면에는
        `ensemble.timeout_s=120` 만 보여서 2분이 상한인 줄 알기 쉽다 — 그 값은 `wait=timeout` 일 때만 쓰인다.

        `role_llm()` 을 부르지 않는다 — 그쪽이 `effective_ensemble()` 을 부르므로 재귀가 된다.
        headless 멤버는 `agents.json` 의 timeout/retries 가 우선일 수 있어(역할에 명시가 없을 때) 실제 값은 더 짧을 수 있다.
        """
        r = dict((self.llm_roles or {}).get(role) or {})

        def num(key: str, default: Any, typ=float) -> Any:
            v = r.get(key)
            if v in (None, ""):
                return default
            try:
                return typ(v)
            except (TypeError, ValueError):
                return default

        timeout_s = num("timeout_s", int(self.llm_timeout or 600), int)
        retries = max(0, num("retries", int(self.llm_retries), int))
        backoff_s = num("backoff_s", float(self.llm_retry_backoff_s), float)
        backoff_max = num("backoff_max_s", float(self.llm_retry_backoff_max_s), float)
        expo = str(r.get("backoff") or self.llm_retry_backoff or "exponential").lower().startswith("exp")
        attempts = 1 + retries
        waits = 0.0
        for i in range(1, attempts):
            w = backoff_s * (2 ** (i - 1)) if expo else backoff_s * i
            waits += min(w, backoff_max) if backoff_max > 0 else w
        one_call = attempts * timeout_s + waits          # complete() 한 번의 최악 (재시도 포함)
        eff = self.effective_ensemble(role)
        if not eff.get("enabled"):
            return {"enabled": False, "one_call_s": one_call, "attempts": attempts, "timeout_s": timeout_s,
                    "members_s": 0.0, "aggregate_s": 0.0, "fallback_s": 0.0, "worst_s": one_call, "notes": []}
        # 멤버는 병렬이므로 가장 느린 하나가 상한. wait=timeout 이면 거기서 끊는다.
        members_s = one_call if eff.get("wait") != "timeout" else min(float(eff.get("timeout_s") or 0) or one_call, one_call)
        aggregate_s = one_call if len(eff.get("members") or []) > 1 else 0.0
        fallback_s = one_call if eff.get("fallback_role_model") else 0.0
        notes: List[str] = []
        if eff.get("wait") != "timeout":
            notes.append("wait=all 이라 ensemble.timeout_s(%s초)는 쓰이지 않는다 — 상한은 가장 느린 멤버다"
                         % eff.get("timeout_s"))
        if fallback_s:
            notes.append("실패하면 폴백이 같은 정책으로 한 번 더 돈다 (+%.0f초)" % fallback_s)
        return {"enabled": True, "one_call_s": one_call, "attempts": attempts, "timeout_s": timeout_s,
                "members_s": members_s, "aggregate_s": aggregate_s, "fallback_s": fallback_s,
                # 정상 경로(멤버 → 취합)와 실패 경로(멤버 → 폴백) 중 긴 쪽
                "worst_s": members_s + max(aggregate_s, fallback_s), "notes": notes}

    def role_llm(self, role: str) -> Dict[str, Any]:
        """역할별 (provider, model, effort + 재시도 정책 + ensemble) 해석: llm_roles[role] 의 값이 비어 있으면 전역값.
        정책 키: timeout_s(llm_timeout) · retries(llm_retries) · backoff_s(llm_retry_backoff_s) · backoff(llm_retry_backoff) ·
        backoff_max_s(llm_retry_backoff_max_s) · budget_s(llm_budget_s) · circuit_failures(llm_circuit_failures) · circuit_cooldown_s(llm_circuit_cooldown_s).
        provider 가 비어 있고 model 이 카탈로그에 한 provider 로만 있으면 그 provider (provider_source=catalog; 아니면 role|global).
        ensemble: effective_ensemble(role) — 기본값이 합쳐지고 빈 멤버가 걸러진 dict (enabled 가 false 면 단일 LLM)."""
        r = dict((self.llm_roles or {}).get(role) or {})
        default_effort = self.answer_effort if role == "answer" else self.llm_effort

        def num(key: str, default: Any, typ=float) -> Any:
            v = r.get(key)
            if v in (None, ""):
                return default
            try:
                return typ(v)
            except (TypeError, ValueError):
                return default
        provider, provider_source = self._resolve_provider(r.get("provider"), r.get("model"), str(self.llm_provider), "role")
        return {"provider": provider,
                "provider_source": provider_source,
                "model": r.get("model") or self.llm_model,
                "effort": r.get("effort") or default_effort,
                "ensemble": self.effective_ensemble(role),
                "timeout_s": num("timeout_s", int(self.llm_timeout or 600), int),
                "retries": num("retries", int(self.llm_retries), int),
                "backoff_s": num("backoff_s", float(self.llm_retry_backoff_s), float),
                "backoff": str(r.get("backoff") or self.llm_retry_backoff or "exponential"),
                "backoff_max_s": num("backoff_max_s", float(self.llm_retry_backoff_max_s), float),
                "budget_s": num("budget_s", int(self.llm_budget_s or 0), int),
                "circuit_failures": num("circuit_failures", int(self.llm_circuit_failures), int),
                "circuit_cooldown_s": num("circuit_cooldown_s", int(self.llm_circuit_cooldown_s), int),
                # 0 = 지정 안 함 → 호출부의 단계별 기본값을 쓴다 (role_max_tokens 참고)
                "max_tokens": num("max_tokens", int(self.answer_max_tokens) if role == "answer" else 0, int)}

    def role_max_tokens(self, role: str, default: int) -> int:
        """역할별 출력 토큰 상한. `llm_roles.<role>.max_tokens` 가 있으면 그 값, 없으면 단계별 기본값.

        단계마다 필요한 길이가 다르다(라우터 200 · 리랭크 400 · 답변 3000 …). 품질을 올리려고
        답변만 늘리거나, 비용을 줄이려고 추출만 줄이는 식으로 단계별로 조절할 수 있게 한다.
        """
        try:
            v = int(self.role_llm(role).get("max_tokens") or 0)
        except (TypeError, ValueError):
            v = 0
        return v if v > 0 else int(default)

    # 역할별 출력 토큰의 단계 기본값 (llm_roles.<role>.max_tokens 를 비워 두면 이 값이 쓰인다).
    # 그 단계가 실제로 뱉는 길이에 맞춘 값이다 — 화면·문서에서 '상속값' 으로 보여 준다.
    ROLE_DEFAULT_MAX_TOKENS = {"answer": 3000, "rerank": 400, "expand": 400, "verify": 1500, "fusion": 400, "select": 400,
                               "extract": 4000, "summary": 800, "review": 3000, "forensic": 1200}

    def role_policy_table(self) -> Dict[str, Dict[str, Any]]:
        """역할별 유효 정책 표 (models show / Web 설정 / check_env 용).

        max_tokens 는 '실제로 쓰일 값' 으로 채운다 — 지정이 없으면 0 이 아니라 단계 기본값.
        """
        out = {}
        for role in self.LLM_ROLES:
            pol = self.role_llm(role)
            dflt = int(self.answer_max_tokens) if role == "answer" else self.ROLE_DEFAULT_MAX_TOKENS.get(role, 1000)
            pol["max_tokens"] = self.role_max_tokens(role, dflt)
            out[role] = pol
        return out

    def __post_init__(self) -> None:
        self.corpus_dirs = [resolve_path(p) for p in (self.corpus_dirs or [])]
        self.data_dir = resolve_path(self.data_dir)
        self.wiki_dir = resolve_path(self.wiki_dir)

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, self.db_name)

    def requests_archive_dir(self) -> str:
        """요청 결과 보관 폴더의 절대 경로. 비어 있으면 "" (보관 안 함).
        `data/requests` 처럼 data 로 시작하는 상대 경로는 data_dir 아래로 본다 — 격리 환경에서도 따라간다."""
        d = str(self.requests_dir or "").strip()
        if not d:
            return ""
        if os.path.isabs(d):
            return d
        parts = d.replace("\\", "/").split("/")
        if parts and parts[0] == "data":
            parts = parts[1:]
        return os.path.join(self.data_dir, *parts) if parts else self.data_dir

    def rerun_capture_dir(self) -> str:
        """단계 재실행용 중간 결과 폴더의 절대 경로. `requests_archive_dir()` 과 같은 규칙
        (`data/…` 는 data_dir 아래로) — 격리 환경에서도 따라간다."""
        d = str(self.rerun_dir or "").strip()
        if not d:
            return ""
        if os.path.isabs(d):
            return d
        parts = d.replace("\\", "/").split("/")
        if parts and parts[0] == "data":
            parts = parts[1:]
        return os.path.join(self.data_dir, *parts) if parts else self.data_dir

    def sweep_record_dir(self) -> str:
        """파라미터 스윕 결과 폴더의 절대 경로 (`rerun_capture_dir()` 과 같은 규칙)."""
        d = str(self.sweep_dir or "").strip()
        if not d:
            return ""
        if os.path.isabs(d):
            return d
        parts = d.replace("\\", "/").split("/")
        if parts and parts[0] == "data":
            parts = parts[1:]
        return os.path.join(self.data_dir, *parts) if parts else self.data_dir

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_portable_dict(self) -> Dict[str, Any]:
        d = self.to_dict()
        d["corpus_dirs"] = [portable_path(p) for p in d["corpus_dirs"]]
        d["data_dir"] = portable_path(d["data_dir"])
        d["wiki_dir"] = portable_path(d["wiki_dir"])
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Settings":
        d = dict(d or {})
        tg = d.pop("toggles", {}) or {}
        # 구 키 호환: rerank_model(파일) → rerank_api_model. (rerank_model 은 이제 역할 단축키로만 쓰인다)
        if "rerank_api_model" not in d and d.get("rerank_model"):
            d["rerank_api_model"] = d["rerank_model"]
        d.pop("rerank_model", None)
        s = Settings(**{k: v for k, v in d.items() if k in Settings.__dataclass_fields__ and k != "LLM_ROLES"})
        s.toggles = Toggles(**{k: v for k, v in tg.items() if k in Toggles.__dataclass_fields__})
        if not isinstance(s.llm_roles, dict):
            s.llm_roles = {}
        return s

    def copy(self) -> "Settings":
        return Settings.from_dict(self.to_dict())


ENV_OVERRIDES = (("LLMWIKI_LLM_MODEL", "llm_model"), ("LLMWIKI_LLM_PROVIDER", "llm_provider"),
                 ("LLMWIKI_EMBED_PROVIDER", "embed_provider"), ("LLMWIKI_EMBED_MODEL", "embed_model"),
                 ("LLMWIKI_OLLAMA_URL", "ollama_url"), ("LLMWIKI_OLLAMA_MODEL", "ollama_model"),
                 ("LLMWIKI_DEBUG_LEVEL", "debug_level"))

# 설정 값의 출처 추적 (config show --effective): key -> default | file | env
SETTING_SOURCES: Dict[str, str] = {}


def env_overrides() -> Dict[str, Any]:
    """환경변수에서 설정 오버라이드를 수집한다.
    규칙: 모든 Settings 키는 LLMWIKI_<KEY 대문자>, 토글은 LLMWIKI_TOGGLE_<NAME 대문자> (또는 LLMWIKI_<NAME>),
    역할별 모델은 LLMWIKI_ANSWER_MODEL 처럼 <role>_<provider|model|effort>, llm_roles 전체는 LLMWIKI_LLM_ROLES(JSON).
    코퍼스 폴더는 LLMWIKI_CORPUS_DIRS (세미콜론 구분). 구 별칭(ENV_OVERRIDES)도 계속 인식."""
    ov: Dict[str, Any] = {}
    for env, key in ENV_OVERRIDES:
        if os.environ.get(env):
            ov[key] = os.environ[env]
    for key in Settings.__dataclass_fields__:
        if key == "toggles":
            continue
        v = os.environ.get("LLMWIKI_" + key.upper())
        if v is not None and v != "":
            ov[key] = v
    for name in Toggles.__dataclass_fields__:
        v = os.environ.get("LLMWIKI_TOGGLE_" + name.upper())
        if v is None:
            v = os.environ.get("LLMWIKI_" + name.upper())
        if v is not None and v != "":
            ov[name] = v
    for role in Settings.LLM_ROLES:
        for attr in Settings.LLM_ROLE_ATTRS:
            v = os.environ.get("LLMWIKI_%s_%s" % (role.upper(), attr.upper()))
            if v:
                ov["%s_%s" % (role, attr)] = v
    return ov


def is_role_key(k: str) -> bool:
    """'answer_model', 'rerank_timeout_s' 처럼 <role>_<attr> 형태의 단축 키인지."""
    for role in Settings.LLM_ROLES:
        if k.startswith(role + "_") and k[len(role) + 1:] in Settings.LLM_ROLE_ATTRS:
            return True
    return False


def split_role_key(k: str) -> Optional[tuple]:
    for role in Settings.LLM_ROLES:
        if k.startswith(role + "_") and k[len(role) + 1:] in Settings.LLM_ROLE_ATTRS:
            return role, k[len(role) + 1:]
    return None


def load_settings(path: Optional[str] = None) -> Settings:
    load_dotenv()
    # `path_for("config")` 를 쓴다 — 모듈 상수 CONFIG_PATH 는 import 시점에 한 번만 계산되어
    # `LLMWIKI_CONF_DIR`(설정을 한 폴더에 모은 경우)과 나중에 바뀐 환경변수를 반영하지 못한다 (2026-09-19).
    path = path or path_for("config")
    if not os.path.exists(path) and os.path.exists(CONFIG_EXAMPLE):
        shutil.copy2(CONFIG_EXAMPLE, path)
    SETTING_SOURCES.clear()
    file_keys: set = set()
    from . import atomicio
    raw_text = atomicio.read_text(path)       # 다른 요청이 저장 중이면 기다렸다 읽는다
    if raw_text is not None:
        raw = json.loads(raw_text)
        s = Settings.from_dict(raw)
        file_keys = set(k for k in raw if k != "toggles") | set("toggles." + k for k in (raw.get("toggles") or {}))
    else:
        s = Settings()
        save_settings(s, path)
    for k in Settings.__dataclass_fields__:
        SETTING_SOURCES[k] = "file" if k in file_keys else "default"
    for k in Toggles.__dataclass_fields__:
        SETTING_SOURCES["toggles." + k] = "file" if ("toggles." + k) in file_keys else "default"
    ov = env_overrides()
    if ov:
        apply_overrides(s, ov)
        for k in ov:
            SETTING_SOURCES["toggles." + k if k in Toggles.__dataclass_fields__ else k] = "env"
    if os.environ.get("LLMWIKI_CORPUS_DIRS"):
        s.corpus_dirs = [resolve_path(p) for p in os.environ["LLMWIKI_CORPUS_DIRS"].split(";") if p.strip()]
        SETTING_SOURCES["corpus_dirs"] = "env"
    # 읽어 온 자리를 기억한다 — `save_settings` 가 **같은 파일**에 되쓰기 위해서 (save_settings 주석 참고)
    try:
        s._config_path = path
    except Exception:
        pass
    return s


def effective_settings(s: Settings) -> List[Dict[str, Any]]:
    """config show --effective 용: 키·현재값·기본값·출처."""
    base = Settings()
    rows: List[Dict[str, Any]] = []
    for k in Settings.__dataclass_fields__:
        if k == "toggles":
            continue
        rows.append({"key": k, "value": getattr(s, k), "default": getattr(base, k), "source": SETTING_SOURCES.get(k, "default"),
                     "env": "LLMWIKI_" + k.upper(), "help": SETTING_HELP.get(k, "")})
    for k in Toggles.__dataclass_fields__:
        rows.append({"key": "toggles." + k, "value": getattr(s.toggles, k), "default": getattr(base.toggles, k),
                     "source": SETTING_SOURCES.get("toggles." + k, "default"), "env": "LLMWIKI_TOGGLE_" + k.upper(),
                     "help": TOGGLE_HELP.get(k, "")})
    return rows


class StraySettingsWrite(RuntimeError):
    """격리된 Settings 를 **기본 설정 파일**에 쓰려 했다 (config.py `_guard_stray_write`)."""


def _guard_stray_write(s: "Settings", path: str) -> None:
    """직접 만든 Settings 가 기본 `config.json` 을 덮어쓰는 것을 막는다 (2026-09-19).

    왜 필요한가: `load_settings()` 로 읽은 Settings 는 읽어 온 자리(`_config_path`)를 기억하지만,
    `Settings(data_dir=<임시폴더>, …)` 처럼 **직접 만든** 것은 그 자리가 없다. 그런 Settings 를
    `save_settings(s)`(경로 없이) 로 저장하면 기본값으로 떨어져 **프로젝트의 config.json** 에
    임시 폴더 경로가 쓰인다. 그러면 색인이 통째로 안 보이게 된다 — 이 저장소에서 두 번 일어났고
    두 번 다 원인을 찾는 데 오래 걸렸다 (조용히 성공하기 때문이다).

    막는 조건을 좁게 잡는다 — 셋이 **모두** 맞을 때만:
      (1) 읽어 온 자리(`_config_path`)가 없다 = 직접 만든 Settings
      (2) 쓰려는 곳이 **프로젝트 루트의 `config.json`** 이다 (격리된 임시 경로는 막지 않는다)
      (3) `data_dir` 이 기본값과 다르다 = 다른 환경을 가리키고 있다
    `config reset`(기본값 Settings 저장)도, 격리 환경에 저장하는 것도 걸리지 않는다.
    """
    if getattr(s, "_config_path", None):
        return
    project_cfg = os.path.normpath(os.path.join(ROOT, "config.json"))
    if os.path.normpath(os.path.abspath(str(path))) != project_cfg:
        return
    default_data = Settings.__dataclass_fields__["data_dir"].default
    cur = str(getattr(s, "data_dir", "") or "")
    if not cur or cur == default_data:
        return
    if os.path.normpath(os.path.abspath(cur)) == os.path.normpath(os.path.join(ROOT, str(default_data))):
        return
    raise StraySettingsWrite(
        "직접 만든 Settings(data_dir=%r)를 프로젝트 설정 파일(%s)에 쓰려 했습니다 — 이대로 저장하면 "
        "색인이 통째로 안 보이게 됩니다. 격리된 설정을 저장하려면 경로를 명시하세요: "
        "save_settings(s, '<그 환경의 config.json>'). 테스트라면 LLMWIKI_CONFIG_PATH 로 격리하세요." % (cur, path))


def save_settings(s: Settings, path: Optional[str] = None) -> None:
    """설정을 파일로. **읽어 온 그 파일**에 되쓴다 (2026-09-19).

    예전에는 모듈 전역 `CONFIG_PATH` 로만 썼다. 그래서 격리된 설정으로 만든 Pipeline 이 설정을 저장하는
    경로(`evolve` 의 `tuning`·`chunk_params` 제안 적용, Web 의 설정 저장)를 타면 **프로젝트의 config.json**
    에 임시 폴더 경로가 쓰였다 — 실제로 샌드박스 점검 한 번에 `data_dir`·`corpus_dirs`·`wiki_dir` 가
    임시 경로로 바뀌어 색인이 통째로 안 보이게 됐다. 읽어 온 자리를 기억해 두고 거기에 쓴다.
    """
    from . import atomicio      # 순환 import 방지: config 는 다른 모듈보다 먼저 적재된다
    path = path or getattr(s, "_config_path", None) or path_for("config")
    _guard_stray_write(s, path)
    out = s.to_portable_dict()
    # 파일이 `config fill-defaults` 로 채워져 있었다면(역할에 ensemble 뼈대가 있음) 저장 뒤에도 그 뼈대를 유지한다 —
    # apply_overrides 는 빈 값("")을 버리므로 Web 저장 한 번에 '명시된 기본값 줄' 이 사라지는 것을 막는다. 채워진 적 없는 파일은 예전 모양 그대로.
    try:
        prev = atomicio.read_json(path)
        prev_roles = (prev or {}).get("llm_roles") if isinstance(prev, dict) else None
        if isinstance(prev_roles, dict) and any(isinstance(v, dict) and "ensemble" in v for v in prev_roles.values()):
            out["llm_roles"] = _with_role_skeleton(out.get("llm_roles") or {})
        if isinstance(prev, dict) and isinstance(prev.get("_comment"), str) and "_comment" not in out:
            out = {"_comment": prev["_comment"], **out}
    except Exception:
        pass
    atomicio.write_json(path, out)


def _with_role_skeleton(roles: Dict[str, Any]) -> Dict[str, Any]:
    """모든 역할에 ROLE_TEMPLATE_KEYS("" = 상속) + ensemble 뼈대를 채운 사본 (있는 값 유지)."""
    out: Dict[str, Any] = {}
    for role in Settings.LLM_ROLES:
        r = dict(roles.get(role) or {})
        for k in ROLE_TEMPLATE_KEYS:
            r.setdefault(k, "")
        if not isinstance(r.get("ensemble"), dict):
            r["ensemble"] = ensemble_template()
        else:
            tpl = ensemble_template()
            for k, v in tpl.items():
                r["ensemble"].setdefault(k, v)
        out[role] = r
    for role, r in roles.items():          # 알 수 없는 역할 이름도 버리지 않는다
        out.setdefault(role, r)
    return out


def apply_overrides(s: Settings, overrides: Dict[str, Any]) -> Settings:
    """평면 dict(예: fts=False, llm_provider=mock)를 Settings/Toggles 에 반영."""
    for k, v in (overrides or {}).items():
        if v is None:
            continue
        if k in Toggles.__dataclass_fields__:
            setattr(s.toggles, k, _to_bool(v))
        elif k == "llm_roles":
            if isinstance(v, str):
                v = json.loads(v) if v.strip() else {}
            # 값이 dict 인 키(ensemble)는 그대로 보존한다 — 빈 문자열/None 만 걸러낸다 (`vv not in (None, "")` 는 dict 에도 안전)
            s.llm_roles = {role: {kk: (_norm_ensemble_raw(vv) if kk == "ensemble" else vv) for kk, vv in (cfg or {}).items() if vv not in (None, "")}
                           for role, cfg in (v or {}).items()}
        elif k == "llm_ensemble_defaults":
            if isinstance(v, str):
                v = json.loads(v) if v.strip() else {}
            cur = dict(getattr(s, k, None) or {})
            for a, b in (v or {}).items():
                if b in (None, ""):
                    cur.pop(str(a), None)
                elif a in ("timeout_s", "min_results"):
                    cur[str(a)] = int(float(b))
                else:
                    cur[str(a)] = str(b)
            s.llm_ensemble_defaults = cur
        elif split_role_key(k):
            role, attr = split_role_key(k)   # rerank_model=..., answer_provider=..., answer_timeout_s=..., answer_ensemble=<json> 형태의 단축 키
            s.llm_roles.setdefault(role, {})
            if v == "":
                s.llm_roles[role].pop(attr, None)
            elif attr == "ensemble":
                s.llm_roles[role][attr] = _norm_ensemble_raw(json.loads(v) if isinstance(v, str) else v)
            else:
                s.llm_roles[role][attr] = str(v) if attr in ("provider", "model", "effort", "backoff") else v
        elif k in Settings.__dataclass_fields__ and k != "toggles":
            cur = getattr(s, k)
            if isinstance(cur, bool):
                v = _to_bool(v)
            elif isinstance(cur, int):
                v = int(v)
            elif isinstance(cur, float):
                v = float(v)
            elif isinstance(cur, list):
                if isinstance(v, str):
                    v = [x for x in v.split(";") if x.strip()]
                v = [resolve_path(x) for x in v]
            elif isinstance(cur, dict):
                if isinstance(v, str):
                    v = json.loads(v) if v.strip() else {}
                v = {str(a): str(b) for a, b in (v or {}).items()}
            elif k in ("data_dir", "wiki_dir"):
                v = resolve_path(v)
            setattr(s, k, v)
    return s


def _norm_ensemble_raw(v: Any) -> Dict[str, Any]:
    """llm_roles.<role>.ensemble 의 저장용 정규화 (파일에 쓰이는 형태). 해석(기본값 병합·빈 멤버 제거)은 Settings.effective_ensemble."""
    if isinstance(v, str):
        v = json.loads(v) if v.strip() else {}
    v = dict(v) if isinstance(v, dict) else {}
    out: Dict[str, Any] = {"enabled": _to_bool(v.get("enabled", False))}
    members = []
    for m in (v.get("members") or [])[:Settings.ENSEMBLE_MAX_MEMBERS]:
        if not isinstance(m, dict):
            continue
        try:
            w = float(m.get("weight", 1.0) if m.get("weight", 1.0) not in (None, "") else 1.0)
        except (TypeError, ValueError):
            w = 1.0
        members.append({"enabled": _to_bool(m.get("enabled", True)), "provider": str(m.get("provider") or ""), "model": str(m.get("model") or ""),
                        "weight": w, "effort": str(m.get("effort") or "")})
    out["members"] = members
    for key in ("wait", "prompt"):
        if v.get(key) not in (None, ""):
            out[key] = str(v[key])
    if v.get("fallback_role_model") not in (None, ""):      # "" = llm_ensemble_defaults 상속
        out["fallback_role_model"] = _to_bool(v["fallback_role_model"])
    if v.get("fallback_mode") not in (None, ""):
        m = str(v["fallback_mode"]).strip().lower()
        out["fallback_mode"] = m if m in Settings.ENSEMBLE_FALLBACK_MODES else "auto"
    for key in ("timeout_s", "min_results"):
        if v.get(key) not in (None, ""):
            try:
                out[key] = int(float(v[key]))
            except (TypeError, ValueError):
                pass
    agg = v.get("aggregator") if isinstance(v.get("aggregator"), dict) else {}
    out["aggregator"] = {"provider": str(agg.get("provider") or ""), "model": str(agg.get("model") or ""), "effort": str(agg.get("effort") or "")}
    return out


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)

# 토글 설명 (Web UI 툴팁 / 문서). 이름은 Toggles 필드와 1:1.
TOGGLE_HELP: Dict[str, str] = {
    "rule_graph": "규칙(사전+정규식) 기반 엔티티/관계 추출. 비용 0, 결정적.",
    "llm_graph": "LLM 기반 엔티티/관계 추출 (빌드 시 청크마다 1회 호출 → 토큰 소비 큼; llm_graph_budget 으로 상한).",
    "embed": "청크 벡터 임베딩 생성 (벡터 검색 채널의 전제).",
    "communities": "그래프 커뮤니티 탐지 (label propagation). 전체 빌드에서 실행.",
    "community_summary": "커뮤니티별 LLM 요약 (토큰 소비).",
    "wiki_pages": "엔티티별 위키 마크다운 페이지 생성.",
    "incremental": "문서 해시 기반 증분 빌드 (끄면 매번 전체 재색인).",
    "fts": "FTS5(BM25) 키워드 검색 채널.",
    "vector": "벡터 유사도 검색 채널.",
    "graph": "그래프(엔티티 시드 → n-hop 확장) 검색 채널.",
    "router": "질의 유형에 따라 채널 가중치를 조정하는 휴리스틱 라우터.",
    "rerank": "융합 후보 재정렬 (LLM 가능 시 LLM, 아니면 로컬 휴리스틱).",
    "llm_answer": "LLM 답변 생성 (불가 시 추출식 답변 폴백).",
    "evolve_capture": "질의 갭/피드백을 자가진화 제안으로 기록.",
    "evolve_auto_apply": "고신뢰 제안 자동 적용 (기본 HITL 은 꺼짐).",
    "stat_skip": "[속도] 파일 mtime/size 가 이전 빌드와 같으면 읽지도 해시하지도 않음. 3,000 파일도 수십 ms 에 스캔.",
    "idf_refit_incremental": "[속도↓/정확도↑] 증분 빌드에서도 hash IDF 를 재적합하고 전체 청크를 재임베딩. 기본은 전체 빌드에서만.",
    "incremental_communities": "[속도↓] 증분 빌드에서도 커뮤니티 재탐지. 그래프가 크면 수 초~수십 초.",
    "wiki_full_rewrite": "[속도↓] 증분 빌드에서도 모든 위키 페이지 재작성. 기본은 변경된 엔티티 페이지만.",
    "warm_cache": "[지연] 빌드 직후 벡터 행렬·엔티티 인덱스를 메모리에 적재해 첫 질의 지연 제거.",
    "fts_optimize": "[속도] 전체 빌드 후 FTS5 세그먼트 병합(optimize) + PRAGMA optimize.",
    "rerank_llm": "[토큰] 리랭크에 LLM 사용. 끄면 로컬 휴리스틱만 (토큰 0, 수 ms).",
    "embed_query_cache": "[속도] 질의를 벡터로 바꾼 결과를 내용(sha1)으로 캐시 — 청크 임베딩과 같은 표(embedding_cache)를 쓴다. 원격 임베더(ollama·voyage)에서는 질의 하나가 2초를 넘고 규칙 대체 질의까지 4번 임베딩하므로, 같은 질문을 반복하는 **회귀 평가·trial·스윕**이 특히 빨라진다(실측 vector_search 8.4초 → 0.13초). 같은 글은 같은 벡터라 결과는 바뀌지 않는다. hash 임베더는 IDF 에 의존해 캐시하지 않는다. 비우기: `embed clear-cache`.",
    "query_cache": "[토큰/지연] 동일 질의+설정+빌드버전 결과를 메모리 캐시 (LLM 호출 생략). **기본 off** — 켜 두면 설정을 바꿔 가며 확인할 때 예전 답이 그대로 돌아와 '고쳤는데 그대로다' 로 보인다. 운영에 올린 뒤 토큰을 아끼려면 켠다. ※ 캐시는 둘이다 — 이것을 꺼도 precompute(영속 answer_cache)가 계속 답을 돌려주므로, 매번 새로 계산하려면 precompute 도 함께 끈다.",
    "context_trim": "[토큰] 긴 청크를 질의 관련 문장 위주로 압축해 답변 프롬프트 입력 토큰 절감.",
    "dedupe_hits": "[토큰] 같은 문서의 겹치는(오버랩) 청크를 컨텍스트에서 제거.",
    "auto_build": "[운영] 서버가 auto_build_interval 초마다 코퍼스를 stat 스캔, 변경 시 증분 빌드.",
    "health_check": "[운영] 빌드 전에 프로바이더·DB·디스크·코퍼스 health 검사. 실패 항목이 있으면 빌드를 시작하지 않음 (build --force 로 강행).",
    "log_stages": "[디버그] 프로파일 단계 종료(이름·ms·요약)를 logs/ 파일에 기록. 정상 동작도 추적 가능.",
    "profile_expansion": "[디버그] 확장 전 질의로도 검색을 실행해 규칙/LLM 확장이 추가한 hit 를 프로파일에 기록 (지연 2배).",
    "doc_acl": "[보안] 문서 단위 접근 제어. docacl.json 의 경로 규칙과 문서 front matter 의 `acl:` 로, 역할이 낮은 사용자에게는 그 문서를 근거로 주지 않는다. 규칙이 비어 있으면 아무도 막지 않고, admin 은 항상 전부 본다 — docs/SECURITY.md.",
    "context_guard": "[보안] 검색된 문서 본문을 구획(<<<C1>>>)으로 감싸고 구조 흉내 조각(## 질문 · system: · [C3] · <|im_start|>)의 표시를 바꿔 프롬프트 인젝션을 막는다. 본문을 지우지는 않는다. 끄면 예전처럼 본문이 그대로 들어간다.",
    "query_rules": "[품질] query_rules.json 의 acronym/synonym/alias/related/exclude 규칙으로 질의 확장 (LLM 불필요, 결정적).",
    "time_scope": "[품질] '지난주·어제·3일전·2026년 8월' 등 시간 표현을 날짜 범위로 바꿔 문서 날짜로 boost/filter.",
    "query_expand": "[품질/토큰] LLM 이 추가 검색 질의를 생성 (원 질의는 항상 유지). LLM 1회.",
    "query_decompose": "[품질/토큰] 다중 홉 질문을 sub-query 로 분해해 각각 검색 (query_expand 와 같은 호출에서 수행).",
    "evidence_check": "[품질] 리랭크 후 근거 충분성 판정(휴리스틱: 점수·채널 합의·키워드 커버리지). 부족하면 fallback_loop 트리거.",
    "evidence_check_llm": "[품질/토큰] 충분성 판정을 LLM 으로 (JSON: sufficient|weak|insufficient + 부족 항목 + 후속 질의).",
    "fallback_loop": "[품질/지연/토큰] 근거 부족 시 L1 규칙 확장 → L2 LLM 확장 → L3 그래프 확장 → L4 광역 검색 순으로 재시도. attempt/token/latency 예산으로 제한.",
    "claim_check": "[품질] 답변의 사실 문장마다 인용 존재 + 인용 근거가 실제로 지지하는지(키워드·수치·ID 대조) 검증 → groundedness.",
    "claim_check_llm": "[품질/토큰] claim 지원 검증을 LLM/NLI 판정으로 (supported|partial|unsupported).",
    "answer_refine": "[품질/토큰] 미지원 문장이 있으면 LLM 이 답변을 1회 재작성 (근거 밖 내용 제거).",
    "evidence_compress": "[토큰] LLM 이 근거 문단에서 질문 관련 문장만 남김 (context_trim 의 LLM 판).",
    "router_llm": "[품질/토큰] LLM 이 질의 의도·문서 유형을 분류해 라우터/부스트에 반영.",
    "pins": "[품질] pins.json 의 고정 근거(조건부 문서/청크, 질의별 정답)를 검색 결과 상단에 주입.",
    "precompute": "[속도/토큰] 사전 계산된 답변 캐시(answer_cache, build_version 키)를 우선 사용. **기본 off** (query_cache 와 같은 이유 — 바꾼 설정이 반영되지 않은 답이 돌아온다). query_cache 와 별개의 영속 캐시라, 매번 새로 계산하려면 둘 다 꺼야 한다. 내용 확인·정리: `precompute status|check|clear`.",
    "doc_vector": "[품질] 문서 카드(제목·메타·헤딩 개요) 임베딩 채널 — 긴 설계 문서의 문서 단위 검색.",
    "feedback_boost": "[품질] 긍정 피드백을 받은 청크에 감쇠하는 소량 boost.",
    "forensic_auto": "[운영] 근거 부족·미지원 답변이 나오면 포렌식 진단을 자동 실행해 forensics 테이블에 누적.",
    "collab": "[협업] Web UI 의 휘발성 채팅 + 게시판(/게시). 부수 기능이라 끄면 화면에서 사라지고 API 는 404 가 되며 질의·빌드·MCP 에는 영향이 없다. 세부 설정은 server.json 의 collab 절. 설명: docs/COLLAB.md",
    "rerun_capture": "[디버깅] 질의마다 단계별 중간 결과(순서·점수·컨텍스트·답변)를 data/reruns 에 남겨, 워터폴의 ⟲ 로 **그 단계부터만** 다시 돌릴 수 있게 한다. 청크 본문은 저장하지 않아 한 건 수십 KB. 끄면 ⟲ 가 '저장된 중간 결과 없음' 으로 막힌다. 보관 개수·상한: rerun_keep · rerun_max_mb. 설명: docs/RERUN.md",
    "mcp_sources": "[데이터] mcp_sources.json 의 외부 MCP(예: Mango) 에서 raw data 를 가져와 색인(ingest)/질의 보강(enrich).",
    "external_rag": "[품질/지연] mcp_sources.json 의 retrieve 매핑(다른 RAG·검색 API)을 질의마다 호출해 결과를 검색 채널 ext_<source> 로 융합(rrf). 가중치 channel_w_external × 소스 weight, 건수 external_rag_k. 외부 응답 지연이 질의 지연에 더해진다.",
    "mcp_federation": "[MCP] mcp_sources.json 에서 expose 한 외부 서버의 tool 을 우리 MCP tools/list 에 <source>__<tool> 로 노출하고 호출을 중계 — 외부 LLM 은 /mcp 하나로 여러 RAG 를 쓴다.",
    "embed_adaptive": "[운영] 임베딩 배치 크기를 실패/지연에 따라 자동 조절하고 WAL 크기를 관리. 품질과 무관.",
    "fts_trigram": "[품질/크기] 한글 trigram 폴백 색인 (0-hit 시에만 사용). 색인 크기 2~3배.",
    "schema_lint": "[데이터] 빌드 시 front matter 스키마 검사 결과를 리포트 (필수 필드·enum·ID 형식).",
    "explicit_relations": "[품질] front matter related.* 와 ID 패턴(ISSUE-/CL-) 규칙으로 결정적(explicit/rule) 관계 생성.",
    "precompute_after_build": "[속도] 빌드 완료 후 평가셋·빈번 질의 답변을 사전 계산.",
    "verify_after_build": "[운영] 빌드 후 색인 정합성(FTS↔청크·임베딩 coverage·댕글링) 요약 검증.",
    "memory_decay": "[진화] 제안·규칙·pin 의 strength 를 시간 감쇠/재사용 강화 (memory decay 잡).",
    "evolve_from_forensics": "[진화] 누적 포렌식 소견을 집계해 corpus_gap/query_rule/tuning 제안 생성.",
    "build_fts": "[빌드] FTS 색인(chunks_fts) 쓰기. 끄면 이번 빌드에서 FTS 채널이 갱신되지 않음 (build verify 의 fts_missing 으로 확인, `build fts` 로 따로 재색인).",
    "doc_expand": "[품질/토큰] 리랭크 후 상위 문서(doc_expand_top_docs)의 나머지 청크 중 질의 관련(키워드+벡터 hybrid ≥ doc_expand_min_score) 청크를 문서 순서로 컨텍스트에 추가. 인용 [C#] 가능, why=doc_expand.",
    "llm_failure_report": "[운영] LLM 호출이 재시도(llm_retries / agents.json retries) 후에도 실패하면 결과 llm_report 와 답변 상단에 역할·시도 횟수·오류·대체 경로(추출식 등)를 보고.",
    "analysis_mode": "[디버그] 상세 분석 모드. 질의를 debug_level 2(단계별 debug·프롬프트 샘플)로 실행하고 모든 단계 결과·설정 스냅샷·품질/속도/토큰 렌즈 소견과 조절점을 한 장의 마크다운(logs/analysis/req_<id>.md)으로 남긴다. LLM 에게 그대로 주어 튜닝을 물을 수 있다. 질의가 느려지고 requests 행이 커지므로 디버깅할 때만 켠다.",
    "llm_after_fusion": "[품질/토큰] 융합·부스트 직후(리랭크 전) LLM(역할 fusion, prompts/fusion_review.md)이 상위 fusion_llm_candidates 후보의 제목·발췌를 보고 명백히 무관한 것을 keep/drop 으로 골라낸다. drop 은 fused × fusion_llm_drop_penalty(0 이면 제거, why=llm_drop). 실패/파싱 오류면 순위 그대로. LLM 1회. trace: fusion_llm.",
    "llm_after_rerank": "[품질/토큰] 리랭크 직후(문서 확장·컨텍스트 전) LLM(역할 select, prompts/rerank_review.md)이 상위 post_rerank_llm_k 후보 중 컨텍스트에 넣을 청크(select, 그 순서가 최종 순서)와 통째로 읽힐 문서(expand_docs → doc_expand 가 그 문서를 우선·전체 확장)를 고른다. 실패면 리랭크 순위 그대로. LLM 1회. trace: rerank_review_llm.",
    "degrade_on_llm_failure": "[운영] 답변 LLM 이 재시도(llm_retries)·llm_fallbacks 뒤에도 실패했을 때 추출식 답변으로 **계속할지**. 켜짐(기본) = 지금까지의 근거로 추출식 답변 + llm_report. 끄면 그 질의는 result_type=error 로 끝나고(답변 본문은 오류 한 줄) llm_report 만 남는다 — '틀린 답보다 실패가 낫다' 는 운영에서. 리랭크의 로컬 폴백·규칙 그래프 폴백은 값싸므로 이 토글과 무관하게 계속 동작한다. 재시도(llm_retries)·llm_fallbacks·fallback_loop 와는 별개의 장치(docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918.md §0.2).",
}

# 토글 그룹 (Web UI 사이드바 자동 생성용). 이름은 Toggles 필드와 1:1.
#: 사이드바 토글 묶음. **파이프라인 순서**(질의를 받아 답이 나가기까지)대로 두고, 그 안에서
#: "무엇을 하는가" 로 묶는다. 화면 왼쪽은 위에서 아래로 읽히므로 순서 자체가 설명이 된다.
#: `stage` 는 이 묶음이 어느 단계에 붙는지 (화면의 단계 배지), `hint` 는 묶음 한 줄 설명.
TOGGLE_GROUPS: List[Dict[str, Any]] = [
    {"key": "query", "title": "① 검색 — 어디서 찾나", "stage": "질의",
     "hint": "채널을 켜고 끄고, 질의를 넓히는 단계. 채널을 끄면 그 신호는 아예 없다.",
     "toggles": ["fts", "vector", "graph", "external_rag", "router", "router_llm", "time_scope",
                 "query_rules", "query_expand", "query_decompose", "pins", "llm_after_fusion", "rerank", "llm_after_rerank"]},
    {"key": "answer", "title": "② 근거와 답변 — 어떻게 답하나", "stage": "질의",
     "hint": "찾은 것을 검증하고 답을 만드는 단계. LLM 호출이 몰려 있어 시간·토큰의 대부분을 쓴다.",
     "toggles": ["doc_expand", "evidence_check", "evidence_check_llm", "fallback_loop", "llm_answer",
                 "evidence_compress", "claim_check", "claim_check_llm", "answer_refine"]},
    {"key": "query_perf", "title": "③ 질의 속도 · 토큰", "perf": True, "stage": "질의",
     "hint": "켜면 빨라지고 토큰을 아끼는 것들 (일부는 품질을 조금 내준다).",
     "toggles": ["rerank_llm", "query_cache", "embed_query_cache", "precompute", "context_trim", "dedupe_hits", "feedback_boost"]},
    {"key": "debug", "title": "④ 디버깅 · 기록", "stage": "관측",
     "hint": "답이 왜 그렇게 나왔는지 되짚기 위한 기록. 품질은 그대로이고 약간의 시간·디스크를 쓴다.",
     "toggles": ["analysis_mode", "forensic_auto", "llm_failure_report", "degrade_on_llm_failure", "rerun_capture",
                 "log_stages", "profile_expansion"]},
    {"key": "build", "title": "⑤ 빌드 — 무엇을 색인하나", "stage": "빌드",
     "hint": "색인에 무엇을 넣을지. 여기서 끈 것은 질의에서 켜도 쓸 것이 없다.",
     "toggles": ["build_fts", "embed", "rule_graph", "llm_graph", "communities", "community_summary",
                 "wiki_pages", "incremental", "explicit_relations", "schema_lint", "doc_vector", "fts_trigram"]},
    {"key": "build_perf", "title": "⑥ 빌드 속도 · 안정성", "perf": True, "stage": "빌드",
     "hint": "빌드 시간을 줄이거나, 빌드 전후에 스스로 점검하게 한다.",
     "toggles": ["stat_skip", "idf_refit_incremental", "incremental_communities", "wiki_full_rewrite",
                 "warm_cache", "fts_optimize", "embed_adaptive", "health_check", "verify_after_build",
                 "precompute_after_build"]},
    {"key": "integrate", "title": "⑦ 연동 · 자동화", "stage": "운영",
     "hint": "다른 RAG·MCP 연결, 자동 빌드, 자가진화, 화면 부가 기능.",
     "toggles": ["mcp_sources", "mcp_federation", "auto_build", "evolve_capture", "evolve_auto_apply",
                 "evolve_from_forensics", "memory_decay", "collab"]},
    # 2026-09-19: 보안은 '검색 품질' 과 다른 축이라 따로 묶는다. 둘 다 끄면 예전과 똑같이 동작하므로
    # 무엇을 끄면 무엇이 열리는지 한 자리에서 보이게 하는 편이 안전하다 (docs/SECURITY.md).
    {"key": "security", "title": "⑧ 보안 — 무엇을 막나", "stage": "질의",
     "hint": "문서 본문이 모델에게 직접 지시하지 못하게 하고, 역할이 낮은 사용자에게는 그 문서를 근거로 주지 않는다. 끄면 예전 동작(무방비)으로 돌아간다.",
     "toggles": ["context_guard", "doc_acl"]},
]

#: 토글을 **켰을 때** 세 축이 어떻게 되는지. `+1` 좋아짐 · `-1` 나빠짐 · 없으면 영향 없음.
#: q=품질(답의 정확도·근거 충실도) · s=속도(응답 시간; 빌드 토글은 빌드 시간) · t=토큰(LLM 입출력 비용).
#: 화면 왼쪽에서 토글마다 배지로 보여 준다 — "이걸 켜면 뭐가 좋아지고 뭘 내주나" 를 누르기 전에 알게 하려는 것.
#: 근거: docs/OPTIMIZATION_GUIDE.md 의 단계별 손잡이 표와 실측(docs/history/2026-09-17/VERIFICATION_0917.md §3).
TOGGLE_EFFECT: Dict[str, Dict[str, int]] = {
    # ---- ① 검색 ----
    "fts": {"q": 1}, "vector": {"q": 1, "s": -1}, "graph": {"q": 1}, "external_rag": {"q": 1, "s": -1},
    "router": {"q": 1}, "router_llm": {"q": 1, "s": -1, "t": -1},
    "time_scope": {"q": 1}, "query_rules": {"q": 1},
    "query_expand": {"q": 1, "s": -1, "t": -1}, "query_decompose": {"q": 1, "s": -1, "t": -1},
    "pins": {"q": 1}, "rerank": {"q": 1, "s": -1},
    "llm_after_fusion": {"q": 1, "s": -1, "t": -1}, "llm_after_rerank": {"q": 1, "s": -1, "t": -1},
    # ---- ② 근거와 답변 ----
    "doc_expand": {"q": 1, "t": -1},
    "evidence_check": {"q": 1}, "evidence_check_llm": {"q": 1, "s": -1, "t": -1},
    "fallback_loop": {"q": 1, "s": -1, "t": -1},
    "llm_answer": {"q": 1, "s": -1, "t": -1},
    "evidence_compress": {"s": -1, "t": 1},        # 압축 호출을 한 번 더 하지만 답변 입력 토큰이 줄어든다
    "claim_check": {"q": 1, "s": -1}, "claim_check_llm": {"q": 1, "s": -1, "t": -1},
    "answer_refine": {"q": 1, "s": -1, "t": -1},
    # ---- ③ 질의 속도·토큰 ----
    "rerank_llm": {"q": 1, "s": -1, "t": -1},
    "query_cache": {"s": 1, "t": 1}, "precompute": {"s": 1, "t": 1},
    # 같은 글은 같은 벡터라 품질은 그대로, 속도만 오른다 (원격 임베더일수록 크게)
    "embed_query_cache": {"s": 1},
    "context_trim": {"s": 1, "t": 1}, "dedupe_hits": {"s": 1, "t": 1},
    "feedback_boost": {"q": 1},
    # ---- ④ 디버깅·기록 ----
    "analysis_mode": {"s": -1, "t": -1}, "forensic_auto": {"s": -1},
    "llm_failure_report": {}, "degrade_on_llm_failure": {}, "rerun_capture": {"s": -1},
    "log_stages": {}, "profile_expansion": {},
    # ---- ⑤ 빌드 (s = 빌드 시간) ----
    "build_fts": {"q": 1, "s": -1}, "embed": {"q": 1, "s": -1},
    "rule_graph": {"q": 1, "s": -1}, "llm_graph": {"q": 1, "s": -1, "t": -1},
    "communities": {"q": 1, "s": -1}, "community_summary": {"q": 1, "s": -1, "t": -1},
    "wiki_pages": {"s": -1}, "incremental": {"s": 1},
    "explicit_relations": {"q": 1}, "schema_lint": {"q": 1},
    "doc_vector": {"q": 1, "s": -1}, "fts_trigram": {"q": 1, "s": -1},
    # ---- ⑥ 빌드 속도·안정성 ----
    "stat_skip": {"s": 1}, "idf_refit_incremental": {"q": 1, "s": -1},
    "incremental_communities": {"s": 1}, "wiki_full_rewrite": {"s": -1},
    "warm_cache": {"s": 1}, "fts_optimize": {"s": 1}, "embed_adaptive": {"s": 1},
    "health_check": {"s": -1}, "verify_after_build": {"s": -1}, "precompute_after_build": {"s": -1},
    # ---- ⑦ 연동·자동화 ----
    "mcp_sources": {"q": 1}, "mcp_federation": {}, "auto_build": {},
    "evolve_capture": {"q": 1}, "evolve_auto_apply": {}, "evolve_from_forensics": {"q": 1},
    "memory_decay": {}, "collab": {},
    # ---- ⑧ 보안 ----
    # 둘 다 품질·속도·토큰을 거의 건드리지 않는다. context_guard 는 문자열 치환뿐(LLM 호출 없음)이고,
    # doc_acl 은 문서당 한 번 판정해 캐시한다. 대신 doc_acl 은 볼 수 없는 근거를 빼므로 그 사용자에게는
    # 답이 짧아질 수 있다 — 품질을 +1 로 적지 않는 이유다.
    "context_guard": {}, "doc_acl": {},
}
TOGGLE_AXES: Dict[str, str] = {"q": "품질", "s": "속도", "t": "토큰"}

SETTING_HELP: Dict[str, str] = {
    "rerank_candidates": "리랭크 후보 수 (LLM 리랭크 프롬프트 크기 ∝ 후보 수 × rerank_chunk_chars).",
    "rerank_chunk_chars": "리랭크 프롬프트에 넣는 청크당 글자 수.",
    "context_max_chars": "답변 컨텍스트 총 글자 상한 (≈ 토큰 ÷ 3).",
    "context_chunk_chars": "context_trim 시 청크당 글자 상한.",
    "answer_max_tokens": "답변 LLM 출력 토큰 상한.",
    "answer_mode": "답변 모드. grounded(기본) = 문서 근거만으로 답하고 근거 판정이 insufficient 면 LLM 을 부르지 않는다. best_effort = 근거가 부족해도 LLM(prompts/answer_best_effort.md)을 불러 문서 사실 [C#] + 배경 지식 [BK] 로 답한다. 결과의 result_type 으로 어느 쪽이었는지 알 수 있다. 요청 단위: CLI --answer-mode · MCP wiki_query(answer_mode=) · Web overrides.answer_mode.",
    "output_mode": "출력 모드 — 어디까지 만들고 무엇을 돌려줄지 (answer_mode 와 독립). answer(기본) = 끝까지. fused = 융합·부스트 뒤(리랭크 전) 후보 candidates[]·채널별 lists·stages 순서를 그대로 (result_type=candidates_fused). reranked = 리랭크 뒤 후보 + 리랭크 입력 전후 순서 (candidates_reranked). context = 컨텍스트([C#] 블록)·refs·근거 판정까지만, 답변 LLM 은 부르지 않는다 (context). answer 가 아니면 답변 LLM·claim 검증·자가진화 기록·캐시 저장을 하지 않는다. 튜닝 output_candidates_n / output_list_n / output_chunk_chars. 요청 단위: CLI --output · MCP wiki_query(output_mode=) · Web Ask 출력 드롭다운(overrides.output_mode).",
    "llm_graph_budget": "빌드당 LLM 추출 호출 상한 (0=무제한). 신규 문서 20개/일 × 10청크 ≈ 200회.",
    "llm_graph_min_chars": "이보다 짧은 청크는 LLM 추출 생략.",
    "graph_profile_keep": "그래프 진단 프로파일(data/graph_profiles/gp_<ts>.json) 보관 개수. `graph profile --compare` 가 직전 파일과 비교한다. 0 = 무제한.",
    "graph_profile_requests": "그래프 진단의 '질의 활용' 절이 보는 최근 질의 요청 수 (그래프 시드 비율·graph# 근거 비율·시드 없는 질의 키워드).",
    "graph_profile_hubs": "그래프 진단의 허브(차수 상위) 표 행 수. 날짜/금액/문서 유형 허브는 경고로 표시된다.",
    "query_cache_size": "질의 캐시 항목 수.",
    "requests_dir": "요청 결과/trace 를 따로 보관할 폴더 (월별 하위 폴더 + req_<id>.json). data_dir 기준 상대 경로 가능. 비우면 파일로 남기지 않는다 — 그러면 keep_requests 로 DB 행이 잘릴 때 '그때 그 답' 을 다시 볼 수 없다.",
    "requests_keep_days": "요청 보관 파일의 보존 기간(일). 0 = 지우지 않음. 정리: `python -m llmwiki maintenance prune_requests` 또는 스케줄 작업.",
    "corpus_exclude": "코퍼스 안에 있어도 색인하지 않을 경로 패턴 목록 (doc_id = 코퍼스 루트 기준 상대 경로). 예: [\"imported/llmwiki/\", \"**/NOTE-*.md\"]. 폴더는 `경로/` 또는 `경로/**`, 파일은 fnmatch 패턴. 색인하면 안 되는 것이 섞이면 도메인 문서를 밀어낸다 — 실측: 도구 자신의 소스 150개를 제외하니 hit@k 0.64→0.88. 바꾸면 다음 빌드에서 그 문서들이 색인에서 빠진다.",
    "rerun_dir": "단계 재실행용 중간 결과 폴더 (req_<request_id>.json). data_dir 기준 상대 경로 가능. 화면의 단계별 ⟲ 버튼이 이 파일을 읽는다 — docs/RERUN.md.",
    "rerun_keep": "재실행용 중간 결과를 최근 몇 건까지 남길지. 오래된 것부터 지운다. 0 = 무제한.",
    "rerun_max_mb": "재실행용 중간 결과 한 건의 상한(MB). 넘으면 저장하지 않고 trace 에 이유를 남긴다.",
    "sweep_dir": "파라미터 스윕 결과 폴더 (sw_<id>.json — 값별 단계 요약·순위·답변). data_dir 기준 상대 경로 가능. `sweep run|list|show|compare` · /api/sweep · wiki_sweep 이 읽고 쓴다 — docs/SWEEP.md.",
    "sweep_keep": "스윕 결과를 최근 몇 건까지 남길지. 오래된 것부터 지운다. 0 = 무제한.",
    "sweep_max_values": "한 스윕에서 돌릴 값의 최대 개수 (range·values 가 이보다 많으면 거부). 값마다 재실행 = LLM 호출이므로 폭주 방지.",
    "sweep_max_parallel": "스윕에서 값을 동시에 몇 개 돌릴지. 1(기본) = 순차. 게이트웨이 한도가 넉넉하면 2~4. 결과 순서는 값 순서로 고정된다.",
    "auto_build_interval": "auto_build 스캔 주기(초).",
    "embed_batch": "임베딩 배치 크기 (API 임베더는 64 이하 권장).",
    # 2026-09-19: Pipeline 단계 상세와 Settings 표에서 설명이 비어 있던 키들.
    # 단계가 가리키는 config 키는 모두 여기에 설명이 있어야 한다 (tools/verify/verify_stage_align.py 가 검사).
    "corpus_dirs": "색인할 코퍼스 폴더 목록. 재귀 스캔하며 .md .txt .csv .html .pdf 를 읽는다. 바꾸면 `build --full`.",
    "data_dir": "DB·캐시·스냅샷·진행 파일이 사는 폴더 (기본 data). 폴더째 옮기면 색인도 함께 간다.",
    "wiki_dir": "생성된 위키 페이지(`wiki/*.md`)가 사는 폴더. 사람이 고친 `## 편집 노트` 는 다음 빌드에 다시 색인된다.",
    "db_name": "data_dir 안의 SQLite 파일 이름 (기본 llmwiki.sqlite3).",
    "chunk_max_chars": "청크 하나의 최대 글자 수. 크면 문맥이 풍부하지만 검색 정밀도와 토큰이 나빠진다. 바꾸면 `build --full`.",
    "chunk_overlap_chars": "이웃 청크가 겹치는 글자 수. 경계에서 잘린 문장을 살린다. 바꾸면 `build --full`.",
    "top_k_fts": "FTS(키워드) 채널이 융합에 넘기는 후보 수.",
    "top_k_vector": "벡터 채널이 융합에 넘기는 후보 수.",
    "top_k_graph": "그래프 채널이 융합에 넘기는 후보 수.",
    "top_k_final": "리랭크 뒤 컨텍스트에 넣는 최종 청크 수. 답변 품질과 입력 토큰을 가장 직접 좌우한다.",
    "graph_hops": "그래프 검색에서 시드 엔티티로부터 확장할 홉 수. 늘리면 recall↑·노이즈↑.",
    "rrf_k": "RRF 융합 상수. 작을수록 각 채널 1위를 강하게 믿고, 클수록 순위 차이를 완만하게 본다.",
    "llm_provider": "기본 LLM 프로바이더 (auto | openai | anthropic | ollama | headless:<agent> | mock). 역할별로 llm_roles 에서 덮어쓴다.",
    "llm_model": "기본 LLM 모델 이름. 역할별로 llm_roles.<role>.model 이 우선한다.",
    "llm_effort": "기본 추론 강도 (low | medium | high). 지원하는 모델에서만 의미가 있다.",
    "answer_effort": "answer 역할의 추론 강도 기본값 (llm_effort 대신 쓰인다).",
    "llm_fallbacks": "기본 모델이 실패할 때 차례로 시도할 대체 모델 목록.",
    "ollama_url": "Ollama 서버 주소 (기본 http://localhost:11434). OpenAI 호환 경로 /v1 는 자동으로 붙는다.",
    "ollama_model": "Ollama 를 쓸 때의 기본 모델 이름 (`ollama list` 의 이름과 정확히 같아야 한다).",
    "embed_provider": "임베딩 프로바이더 (auto | hash | ollama | voyage | openai | st). auto 는 키·서버를 탐지한다. 바꾸면 `build vector --full`.",
    "embed_model": "임베딩 모델 이름. 비우면 프로바이더 기본값. 바꾸면 차원이 달라질 수 있어 `build vector --full`.",
    "llm_roles": "역할별 LLM 설정 묶음 (answer·rerank·extract·summary·review·expand·verify·forensic·fusion·select). 역할마다 provider/model/effort 와 timeout_s·retries·budget_s·max_tokens·ensemble 을 둔다.",
    "evolve_min_confidence": "자가진화 제안을 자동 적용 후보로 볼 최소 신뢰도 (evolve_auto_apply 가 켜져 있을 때).",
    "evolve_auto_apply_kinds": "자동 적용을 허용할 제안 종류 목록. 비우면 되돌리기 쉬운 것만(synonym·query_rule·pin·wiki_note). chunk_params(전체 리빌드)·alias/entity(그래프 재빌드)는 일부러 빼 두었다 — 넣으려면 여기에 적는다.",
    "evolve_snapshot_keep": "적용(apply)마다 만드는 자동 스냅샷을 몇 개까지 남길지. 0 = 무제한(예전 동작). 자동 적용을 켜 두면 스냅샷이 계속 쌓여 data 폴더가 커진다.",
    "evolve_low_score_threshold": "이 점수 아래로 떨어진 질의를 '개선 거리' 로 보고 제안을 만든다.",
    "toggles": "기능 on/off 묶음. 개별 설명은 Settings 토글 화면 또는 `python -m llmwiki config show --effective`.",
    "debug_level": "프로파일 상세도: 0 요약 · 1 디버그 메타/로그 · 2 프롬프트/응답 원문 샘플.",
    "keep_requests": "requests 테이블 보존 개수.",
    "embed_dim": "hash 임베딩 차원 (메모리 = 청크수×dim×4B; float16 저장 시 절반). 모든 차원 지원, 변경 시 build --full.",
    "timezone": "상대 시간 표현(지난주·어제·3일전) 해석 기준 시간대. 기본 Asia/Seoul.",
    "week_start": "주 시작 요일 (mon|sun) — '지난주' 범위 계산.",
    "console_encoding": "터미널 출력 인코딩: auto(기본) | utf-8 | native | off. 한글·기호(⏳ ✔ ·)가 깨지거나 UnicodeEncodeError 로 죽는 환경에서 조정. native = 코드페이지를 바꾸지 않고 표현 불가 문자만 ? 로.",
    "console_set_codepage": "Windows 콘솔 출력 코드페이지를 UTF-8(65001)로 바꿀지 (chcp 65001 과 동일, 종료 시 복구). 콘솔을 바꾸면 안 되는 환경이면 false + console_encoding=native.",
    "log_level": "logs/ 파일 로그 레벨 (DEBUG 면 단계별 상세까지).",
    "log_max_mb": "로그 파일당 최대 크기(MB). 초과 시 로테이션.",
    "log_backups": "로테이션 보관 파일 수.",
    "log_console": "콘솔(stderr)에도 WARNING 이상 출력.",
    "log_total_max_mb": "logs/ 폴더 총량 상한(MB). llmwiki/error/build/query 로그 + 백업 + audit.jsonl + analysis/ 합계. 0 = 제한 없음. 확인: `logs status`, health log_quota.",
    "log_limit_action": "총량 초과 시 동작: warn(error.log 에 경고 + health/Web 배너) | prune(오래된 *.log.N → analysis 리포트 → audit 백업 순으로 지워 80% 아래로) | stop(error.log 만 남기고 다른 로그 기록을 멈춤, 80% 아래로 내려가면 재개).",
    "log_check_interval_s": "총량을 다시 재는 최소 간격(초). 로그가 남을 때마다 이 간격이 지났는지만 확인하므로 부하가 거의 없다. 0 = 매 레코드(테스트용).",
    "audit_max_mb": "logs/audit.jsonl 파일당 최대 크기(MB). 초과 시 audit.jsonl.1, .2 … 로 로테이션.",
    "audit_backups": "audit.jsonl 로테이션 보관 개수. 0 = 초과 시 비운다.",
    "analysis_keep": "logs/analysis 의 리포트(req_<id>.md + .json = 1건) 보관 개수. 새 리포트를 저장할 때 오래된 것부터 지운다. 0 = 무제한.",
    "openai_base_url": "OpenAI-compatible 서버 base URL (…/v1). vLLM·LM Studio·Ollama·OpenRouter·사내 게이트웨이(PAT). 키는 .env OPENAI_API_KEY 또는 LLM_API_KEY.",
    "openai_api_key_header": "PAT/키를 싣는 헤더: authorization(Bearer <key>) | api-key | x-api-key | 임의 헤더명(값은 키 그대로).",
    "openai_extra_headers": "게이트웨이가 요구하는 고정 헤더 JSON (예 {\"X-Tenant\": \"modem\"}).",
    "openai_embed_base_url": "임베딩 전용 base URL (비우면 openai_base_url). 키는 OPENAI_EMBED_API_KEY 가 있으면 그것, 없으면 LLM 키.",
    "openai_embed_model": "embed_provider=openai 일 때 임베딩 모델명.",
    "anthropic_base_url": "Anthropic 호환 게이트웨이 URL (비우면 api.anthropic.com). 키: ANTHROPIC_API_KEY(x-api-key) 또는 ANTHROPIC_AUTH_TOKEN(Bearer PAT).",
    "rerank_url": "rerank_method=api 엔드포인트 URL (Cohere/Jina/vLLM /v1/rerank, Voyage /v1/rerank).",
    "rerank_api_model": "rerank API 모델명 (rerank_url 용). rerank_model 은 역할 rerank 의 LLM 모델 단축키이므로 다른 값.",
    "rerank_api_style": "rerank 응답 포맷: cohere(=jina/vLLM results[].relevance_score) | voyage(data[].relevance_score).",
    "embed_batch_max": "적응형 임베딩 배치 상한 (성공이 이어지면 embed_batch 에서 이 값까지 증가).",
    "embed_batch_target_ms": "배치 1회 목표 지연(ms). 초과하면 배치 축소.",
    "embed_commit_every": "N 배치마다 commit·진행률 저장 (중단 후 재개 단위).",
    "wal_checkpoint_mb": "빌드 중 WAL 파일이 이 크기(MB)를 넘으면 체크포인트.",
    "embed_store_dtype": "벡터 저장/행렬 dtype: float32 | float16 (메모리 절반).",
    "build_lock_timeout": "다른 프로세스가 빌드 중일 때 락을 기다릴 초 (0=즉시 실패). 기본 172800(48시간) — 앞 빌드가 끝나면 이어서 시작한다.",
    "build_lock_stale_s": "빌드 락의 주인이 살아 있어도 이 시간(초)이 지나면 죽은 락으로 보고 회수한다. 빌드 1회의 최대 수명이므로 가장 오래 걸리는 빌드보다 길게 (기본 172800=48시간).",
    "db_busy_timeout_s": "SQLite 쓰기 잠금 대기(초). CLI 빌드가 쓰는 동안 서버 질의의 로그 기록이 기다리는 시간 (WAL 이라 읽기는 기다리지 않음).",
    "db_synchronous": "SQLite 커밋 내구성 PRAGMA (OFF|NORMAL|FULL|EXTRA). 기본 NORMAL — WAL 에서는 **DB 가 깨지지 않고** 체크포인트에서만 fsync 하므로, 질의 1건이 커밋을 여러 번 하는 이 파이프라인에서 동시 질의 지연이 크게 준다. 정전 시 최근 몇 건의 커밋(색인·관측 기록)이 날아갈 수 있고 재빌드로 복구된다. 규정상 더 엄격해야 하면 FULL.",
    "db_pool_size": "서버 스레드별 SQLite 연결 풀 크기 (server.json concurrency.max_parallel_reads 이상 권장). **놀고 있는** 연결만 제한한다 — 빌려 나간 연결 수는 db_max_live_connections 가 본다.",
    "db_max_live_connections": "동시에 **빌려 나가 있는** SQLite 연결의 부드러운 상한 (0 = 무제한, 예전 동작). 넘으면 db_pool_wait_timeout_s 만큼 반납을 기다렸다가, 그래도 없으면 만들어서라도 진행하고 overflow 로 센다 — 질의를 실패시키지 않는다. 정상 운영에서는 걸리지 않는 값이므로 `/api/admin/server` 의 db_pool.overflow 가 늘면 세션 누수나 과부하 신호다.",
    "db_pool_wait_timeout_s": "db_max_live_connections 를 넘었을 때 다른 스레드가 연결을 반납하기를 기다리는 시간(초). 길게 잡으면 버스트가 평평해지지만 응답이 그만큼 늦어진다.",
    "llm_timeout": "LLM 호출 1회의 HTTP 타임아웃(초). 로컬 모델(Ollama)이 느리면 늘리고, 멈춘 서버를 빨리 감지하려면 줄인다 (예: 120). headless 는 agents.json timeout_s(기본 300) 가 우선.",
    "llm_retries": "LLM 호출이 timeout/네트워크/headless 실행 실패(transient)면 재시도할 횟수 (최대 1+n 회). HTTP 4xx(인증·모델명) 는 재시도하지 않음. headless 는 agents.json retries 우선.",
    "llm_retry_backoff_s": "재시도 사이 기본 대기 초. linear 면 ×시도번호, exponential 이면 ×2^(시도-1) (+지터). 역할별: llm_roles.<role>.backoff_s",
    "llm_retry_backoff": "재시도 대기 증가 방식 linear | exponential. 역할별: llm_roles.<role>.backoff",
    "llm_retry_backoff_max_s": "재시도 대기 상한(초). 역할별: llm_roles.<role>.backoff_max_s",
    "llm_budget_s": "한 LLM 호출의 재시도 포함 총 시간 예산(초). 넘으면 재시도를 멈추고 대체 경로(추출식 답변 등)로. 0=무제한. 역할별: llm_roles.<role>.budget_s",
    "llm_frequency_penalty": "같은 토큰을 다시 쓸 때의 벌점(OpenAI 호환). 작은 모델이 한 구절을 수십 번 되풀이하는 고장을 줄인다. 0=끔, 0.2~0.6 권장.",
    "llm_presence_penalty": "이미 나온 토큰에 주는 벌점(OpenAI 호환). 보통 0 으로 둔다.",
    "llm_repeat_penalty": "Ollama 네이티브(/api/generate) 의 repeat_penalty. 1.0=억제 없음, 1.1 권장.",
    "llm_http_retries": "HTTP 429/5xx 에 대한 프로바이더 내부 짧은 재시도 횟수 (llm_retries 와 곱해짐).",
    "llm_circuit_failures": "같은 provider/model 이 연속 n회 최종 실패하면 회로 차단(circuit open): cooldown 동안 호출을 즉시 실패시켜 30명이 각각 timeout 을 기다리지 않게. 0=끔. 역할별: llm_roles.<role>.circuit_failures",
    "llm_circuit_cooldown_s": "회로 차단 후 재시도까지 대기(초). 역할별: llm_roles.<role>.circuit_cooldown_s",
    "llm_ensemble_defaults": "역할별 앙상블(llm_roles.<role>.ensemble)에서 생략한 키의 기본값 JSON: wait(all|timeout) · timeout_s · min_results · prompt(prompts/<name>.md). 앙상블 자체는 역할마다 enabled=true 로 켠다 — docs/ENSEMBLE.md, CLI `models ensemble show|set`.",
    "web_host": "serve 기본 바인드 주소 (플래그 --host 가 우선). 사내 공개는 0.0.0.0 — security.json 의 로그인 설정이 있어야 허용.",
    "web_port": "serve 기본 포트 (--port 가 우선). Web UI 와 MCP Streamable HTTP(POST /mcp) 가 같은 포트.",
    "mcp_transport": "mcp 명령 기본 전송: stdio | http (--transport 가 우선).",
    "mcp_host": "mcp --transport http 기본 바인드 주소.",
    "mcp_port": "mcp --transport http 기본 포트 (serve 와 다른 포트로 MCP 만 열 때).",
    "mcp_url": "비어 있지 않으면 `mcp` 명령이 stdio→이 URL 로 중계하는 브리지로 동작 (예 http://wiki-host:8765/mcp). 우선순위: --connect > LLMWIKI_MCP_URL > 이 값. 토큰은 --token > LLMWIKI_MCP_TOKEN.",
    "mcp_plugins_dir": "MCP 플러그인 도구 폴더. 이 폴더의 *.py(밑줄로 시작하지 않는 파일) 가 register(add_tool) 로 도구를 등록하면 tools/list 에 나타난다. 예시: plugins/mcp_tools/_example_echo.py (밑줄을 지우면 활성).",
}

# ---------------------------------------------------------------- 기본값을 파일에 명시 (config fill-defaults)
# 운영자가 "이 키가 있는지" 를 코드에서 찾지 않고 파일의 한 줄을 고치면 되도록, 코드 기본값과 같은 값이라도 파일에 적어 둔다
# (2026-09-18 요청). 있는 값은 절대 바꾸지 않는다. 역할별 llm_roles.<role> 은 provider/model/effort/timeout_s/retries/max_tokens/ensemble 을
# 갖되 정책 키는 "" (= 전역값 상속) 로 둔다 — 숫자를 박아 넣으면 그 뒤 전역 llm_timeout 을 바꿔도 그 역할은 따라오지 않기 때문.
ROLE_TEMPLATE_KEYS = ("provider", "model", "effort", "timeout_s", "retries", "max_tokens")


def ensemble_template() -> Dict[str, Any]:
    """llm_roles.<role>.ensemble 의 '꺼진' 뼈대 (멤버 3행 + 취합기). 키 모양은 _norm_ensemble_raw 와 같다.
    wait/timeout_s/min_results/prompt 는 "" = llm_ensemble_defaults 상속."""
    return {"enabled": False,
            "members": [{"enabled": True, "provider": "", "model": "", "weight": 1.0, "effort": ""} for _ in range(Settings.ENSEMBLE_MAX_MEMBERS)],
            "wait": "", "timeout_s": "", "min_results": "", "prompt": "", "fallback_role_model": "", "fallback_mode": "",
            "aggregator": {"provider": "", "model": "", "effort": ""}}


def fill_defaults(path: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """config.json 에 빠진 Settings 키 · toggles 전부 · llm_roles.<role> 뼈대(모든 역할) 를 기본값으로 채워 쓴다.
    있는 값은 유지하고 키 순서는 '기존 키 → 빠진 키(dataclass 순서)'. 반환: {path, added, added_toggles, added_roles, written, dry_run}."""
    from . import atomicio
    path = path or path_for("config")
    raw = atomicio.read_json(path)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("%s 는 JSON 객체여야 합니다" % path)
    base = Settings().to_portable_dict()
    out: Dict[str, Any] = {}
    added: List[str] = []
    for k, v in raw.items():                     # 기존 키(주석 _comment 포함)를 원래 순서대로
        out[k] = v
    for k in Settings.__dataclass_fields__:
        if k in out:
            continue
        out[k] = json.loads(json.dumps(base[k])) if k != "toggles" else {}
        added.append(k)
    # toggles
    tg = out.get("toggles")
    if not isinstance(tg, dict):
        tg = {}
        if "toggles" not in added:
            added.append("toggles")
    added_toggles: List[str] = []
    for k, v in base["toggles"].items():
        if k not in tg:
            tg[k] = v
            added_toggles.append(k)
    out["toggles"] = tg
    # llm_roles: 모든 역할 + 역할마다 뼈대 키 + ensemble
    roles = out.get("llm_roles")
    if not isinstance(roles, dict):
        roles = {}
    added_roles: Dict[str, List[str]] = {}
    for role in Settings.LLM_ROLES:
        r = roles.get(role)
        if not isinstance(r, dict):
            r = {}
        miss: List[str] = []
        for k in ROLE_TEMPLATE_KEYS:
            if k not in r:
                r[k] = ""
                miss.append(k)
        if not isinstance(r.get("ensemble"), dict):
            r["ensemble"] = ensemble_template()
            miss.append("ensemble")
        else:
            ens, tpl = r["ensemble"], ensemble_template()
            for k, v in tpl.items():
                if k not in ens:
                    ens[k] = v
                    miss.append("ensemble." + k)
            mem = ens.get("members") if isinstance(ens.get("members"), list) else []
            while len(mem) < Settings.ENSEMBLE_MAX_MEMBERS:
                mem.append(tpl["members"][0].copy())
                miss.append("ensemble.members[%d]" % (len(mem) - 1))
            ens["members"] = mem
        roles[role] = r
        if miss:
            added_roles[role] = miss
    out["llm_roles"] = roles
    rep = {"path": path, "added": added, "added_toggles": added_toggles, "added_roles": added_roles, "dry_run": dry_run,
           "written": False, "changed": bool(added or added_toggles or added_roles)}
    if rep["changed"] and not dry_run:
        atomicio.write_json(path, out)
        rep["written"] = True
    return rep
