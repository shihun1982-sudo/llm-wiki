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
from typing import Dict, List, Optional, Any

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
}
_PATH_ENV_ALIASES = {"config": "LLMWIKI_CONFIG", "env": "LLMWIKI_ENV_FILE", "tuning": "LLMWIKI_TUNING"}


def path_for(name: str) -> str:
    """이름으로 파일/폴더 경로 해석: LLMWIKI_<NAME>_PATH > (구 별칭 env) > 기본값(프로젝트 루트 기준)."""
    env = os.environ.get("LLMWIKI_%s_PATH" % name.upper()) or os.environ.get(_PATH_ENV_ALIASES.get(name, ""), "")
    p = env or _PATH_DEFAULTS[name]
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(ROOT, p))


def all_paths() -> Dict[str, str]:
    return {k: path_for(k) for k in _PATH_DEFAULTS}


def load_dotenv(path: Optional[str] = None) -> Dict[str, str]:
    """.env 의 KEY=VALUE 를 os.environ 에 넣는다 (이미 설정된 값은 덮어쓰지 않음)."""
    path = path or ENV_FILE
    loaded: Dict[str, str] = {}
    if not os.path.exists(path):
        return loaded
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
                loaded[k] = v
    return loaded


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
    query_cache: bool = True       # 동일 질의+설정+빌드버전 결과 캐시 (LLM 토큰 절약)
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
    precompute: bool = False       # 사전 계산 답변 캐시(answer_cache) 사용
    doc_vector: bool = False       # 문서 카드 임베딩 채널 (문서 단위 검색)
    feedback_boost: bool = False   # 긍정 피드백 청크 boost (decay)
    forensic_auto: bool = True     # 근거 부족/미지원 답변 시 포렌식 자동 기록
    mcp_sources: bool = False      # 외부 MCP 소스(mcp_sources.json) 사용 (빌드 ingest / 질의 enrich)
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


@dataclass
class Settings:
    corpus_dirs: List[str] = field(default_factory=lambda: list(DEFAULT_CORPUS_DIRS))
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
    llm_fallbacks: bool = True     # anthropic server-side refusal fallback
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    embed_provider: str = "auto"   # auto | hash | voyage | ollama | st
    embed_model: str = ""          # voyage: voyage-3.5 / ollama: nomic-embed-text / st: 모델명
    embed_dim: int = 4096          # hash 임베딩 차원
    # 역할별 LLM (비우면 llm_provider/llm_model/llm_effort 를 사용). 역할: answer | rerank | extract | summary | review
    #   예) {"rerank": {"provider": "ollama", "model": "llama3.1", "effort": "low"}, "answer": {"model": "claude-opus-5"}}
    llm_roles: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # 토큰/속도 관련 상세 설정
    rerank_candidates: int = 16    # 리랭크 후보 수 (LLM 리랭크 입력 크기)
    rerank_chunk_chars: int = 600  # 리랭크 프롬프트에 넣는 청크당 최대 글자수
    context_max_chars: int = 9000  # 답변 컨텍스트 최대 글자수
    context_chunk_chars: int = 1200  # context_trim 시 청크당 최대 글자수
    answer_max_tokens: int = 3000
    llm_graph_budget: int = 0      # 빌드당 LLM 추출 호출 상한 (0 = 무제한)
    llm_graph_min_chars: int = 80  # 이보다 짧은 청크는 LLM 추출 생략
    query_cache_size: int = 200
    # 확장/운영
    auto_build_interval: int = 300  # auto_build 스캔 주기(초)
    embed_batch: int = 64
    debug_level: int = 1           # 프로파일 상세도: 0 요약만 · 1 디버그 메타/로그 · 2 프롬프트/응답 원문 샘플까지
    keep_requests: int = 2000      # requests 테이블 보존 개수 (초과분 자동 삭제)
    # evolve
    evolve_min_confidence: float = 0.8
    evolve_low_score_threshold: float = 0.05
    # ---- 지역/시간 ----
    timezone: str = "Asia/Seoul"   # 상대 시간 표현("지난주") 해석 기준 시간대 (IANA 이름)
    week_start: str = "mon"        # 주의 시작 요일 (mon | sun)
    # ---- 로그 (logs/ 폴더) ----
    log_level: str = "INFO"        # DEBUG | INFO | WARNING | ERROR
    log_max_mb: int = 10           # 파일당 최대 MB (로테이션)
    log_backups: int = 10          # 보관 파일 수
    log_console: bool = False      # 콘솔에도 WARNING 이상 출력
    # ---- OpenAI-compatible / rerank 엔드포인트 (키는 .env: OPENAI_API_KEY, RERANK_API_KEY) ----
    openai_base_url: str = "http://localhost:11434/v1"   # vLLM · LM Studio · Ollama(OpenAI 호환) · OpenRouter · 사내 게이트웨이
    openai_embed_model: str = ""   # embed_provider=openai 일 때 모델명 (비우면 embed_model)
    rerank_url: str = ""           # rerank_method=api 일 때 엔드포인트 (예 http://localhost:8000/v1/rerank)
    rerank_model: str = ""         # rerank 모델명 (예 BAAI/bge-reranker-v2-m3, rerank-2)
    rerank_api_style: str = "cohere"   # cohere(=jina/vLLM) | voyage
    # ---- 임베딩 실행 제어 (품질과 무관, 실행 안정성) ----
    embed_batch_max: int = 256     # 적응형 배치 상한
    embed_batch_target_ms: int = 8000   # 배치 지연이 이보다 크면 배치 축소
    embed_commit_every: int = 10   # N 배치마다 commit + 진행률 저장 (중단 후 재개 단위)
    wal_checkpoint_mb: int = 64    # 빌드 중 WAL 이 이보다 크면 체크포인트
    embed_store_dtype: str = "float32"   # float32 | float16 (저장·행렬 메모리 절반, 유사도 오차 미미)
    build_lock_timeout: int = 0    # 다른 빌드가 락을 잡고 있을 때 기다릴 초 (0 = 즉시 실패)
    toggles: Toggles = field(default_factory=Toggles)

    LLM_ROLES = ("answer", "rerank", "extract", "summary", "review", "expand", "verify", "forensic")

    def role_llm(self, role: str) -> Dict[str, str]:
        """역할별 (provider, model, effort) 해석: llm_roles[role] 의 값이 비어 있으면 전역값."""
        r = dict((self.llm_roles or {}).get(role) or {})
        default_effort = self.answer_effort if role == "answer" else self.llm_effort
        return {"provider": r.get("provider") or self.llm_provider,
                "model": r.get("model") or self.llm_model,
                "effort": r.get("effort") or default_effort}

    def __post_init__(self) -> None:
        self.corpus_dirs = [resolve_path(p) for p in (self.corpus_dirs or [])]
        self.data_dir = resolve_path(self.data_dir)
        self.wiki_dir = resolve_path(self.wiki_dir)

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, self.db_name)

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
        for attr in ("provider", "model", "effort"):
            v = os.environ.get("LLMWIKI_%s_%s" % (role.upper(), attr.upper()))
            if v:
                ov["%s_%s" % (role, attr)] = v
    return ov


def load_settings(path: Optional[str] = None) -> Settings:
    load_dotenv()
    path = path or CONFIG_PATH
    if not os.path.exists(path) and os.path.exists(CONFIG_EXAMPLE):
        shutil.copy2(CONFIG_EXAMPLE, path)
    SETTING_SOURCES.clear()
    file_keys: set = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
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


def save_settings(s: Settings, path: Optional[str] = None) -> None:
    path = path or CONFIG_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s.to_portable_dict(), f, ensure_ascii=False, indent=2)


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
            s.llm_roles = {role: {kk: vv for kk, vv in (cfg or {}).items() if vv} for role, cfg in (v or {}).items()}
        elif "_" in k and k.rsplit("_", 1)[0] in Settings.LLM_ROLES and k.rsplit("_", 1)[1] in ("provider", "model", "effort"):
            role, attr = k.rsplit("_", 1)   # rerank_model=..., answer_provider=... 형태의 단축 키
            s.llm_roles.setdefault(role, {})
            if v == "":
                s.llm_roles[role].pop(attr, None)
            else:
                s.llm_roles[role][attr] = str(v)
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
            elif k in ("data_dir", "wiki_dir"):
                v = resolve_path(v)
            setattr(s, k, v)
    return s


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
    "query_cache": "[토큰/지연] 동일 질의+설정+빌드버전 결과를 메모리 캐시 (LLM 호출 생략).",
    "context_trim": "[토큰] 긴 청크를 질의 관련 문장 위주로 압축해 답변 프롬프트 입력 토큰 절감.",
    "dedupe_hits": "[토큰] 같은 문서의 겹치는(오버랩) 청크를 컨텍스트에서 제거.",
    "auto_build": "[운영] 서버가 auto_build_interval 초마다 코퍼스를 stat 스캔, 변경 시 증분 빌드.",
    "health_check": "[운영] 빌드 전에 프로바이더·DB·디스크·코퍼스 health 검사. 실패 항목이 있으면 빌드를 시작하지 않음 (build --force 로 강행).",
    "log_stages": "[디버그] 프로파일 단계 종료(이름·ms·요약)를 logs/ 파일에 기록. 정상 동작도 추적 가능.",
    "profile_expansion": "[디버그] 확장 전 질의로도 검색을 실행해 규칙/LLM 확장이 추가한 hit 를 프로파일에 기록 (지연 2배).",
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
    "precompute": "[속도/토큰] 사전 계산된 답변 캐시(answer_cache, build_version 키)를 우선 사용.",
    "doc_vector": "[품질] 문서 카드(제목·메타·헤딩 개요) 임베딩 채널 — 긴 설계 문서의 문서 단위 검색.",
    "feedback_boost": "[품질] 긍정 피드백을 받은 청크에 감쇠하는 소량 boost.",
    "forensic_auto": "[운영] 근거 부족·미지원 답변이 나오면 포렌식 진단을 자동 실행해 forensics 테이블에 누적.",
    "mcp_sources": "[데이터] mcp_sources.json 의 외부 MCP(예: Mango) 에서 raw data 를 가져와 색인(ingest)/질의 보강(enrich).",
    "embed_adaptive": "[운영] 임베딩 배치 크기를 실패/지연에 따라 자동 조절하고 WAL 크기를 관리. 품질과 무관.",
    "fts_trigram": "[품질/크기] 한글 trigram 폴백 색인 (0-hit 시에만 사용). 색인 크기 2~3배.",
    "schema_lint": "[데이터] 빌드 시 front matter 스키마 검사 결과를 리포트 (필수 필드·enum·ID 형식).",
    "explicit_relations": "[품질] front matter related.* 와 ID 패턴(ISSUE-/CL-) 규칙으로 결정적(explicit/rule) 관계 생성.",
    "precompute_after_build": "[속도] 빌드 완료 후 평가셋·빈번 질의 답변을 사전 계산.",
    "verify_after_build": "[운영] 빌드 후 색인 정합성(FTS↔청크·임베딩 coverage·댕글링) 요약 검증.",
    "memory_decay": "[진화] 제안·규칙·pin 의 strength 를 시간 감쇠/재사용 강화 (memory decay 잡).",
    "evolve_from_forensics": "[진화] 누적 포렌식 소견을 집계해 corpus_gap/query_rule/tuning 제안 생성.",
}

# 토글 그룹 (Web UI 사이드바 자동 생성용). 이름은 Toggles 필드와 1:1.
TOGGLE_GROUPS: List[Dict[str, Any]] = [
    {"key": "build", "title": "Build", "toggles": ["rule_graph", "llm_graph", "embed", "communities", "community_summary", "wiki_pages", "incremental",
                                                   "explicit_relations", "schema_lint", "doc_vector", "fts_trigram", "mcp_sources"]},
    {"key": "build_perf", "title": "Build · 속도/안정성", "perf": True, "toggles": ["stat_skip", "idf_refit_incremental", "incremental_communities", "wiki_full_rewrite",
                                                                              "warm_cache", "fts_optimize", "embed_adaptive", "health_check", "verify_after_build", "precompute_after_build"]},
    {"key": "query", "title": "Query · 검색", "toggles": ["fts", "vector", "graph", "router", "router_llm", "time_scope", "query_rules", "query_expand", "query_decompose", "pins", "rerank"]},
    {"key": "answer", "title": "Query · 근거/답변", "toggles": ["evidence_check", "evidence_check_llm", "fallback_loop", "llm_answer", "evidence_compress", "claim_check",
                                                          "claim_check_llm", "answer_refine", "forensic_auto"]},
    {"key": "query_perf", "title": "Query · 토큰/지연", "perf": True, "toggles": ["rerank_llm", "query_cache", "precompute", "context_trim", "dedupe_hits", "feedback_boost"]},
    {"key": "evolve", "title": "Evolve · System", "toggles": ["evolve_capture", "evolve_auto_apply", "evolve_from_forensics", "memory_decay", "auto_build", "log_stages", "profile_expansion"]},
]

SETTING_HELP: Dict[str, str] = {
    "rerank_candidates": "리랭크 후보 수 (LLM 리랭크 프롬프트 크기 ∝ 후보 수 × rerank_chunk_chars).",
    "rerank_chunk_chars": "리랭크 프롬프트에 넣는 청크당 글자 수.",
    "context_max_chars": "답변 컨텍스트 총 글자 상한 (≈ 토큰 ÷ 3).",
    "context_chunk_chars": "context_trim 시 청크당 글자 상한.",
    "answer_max_tokens": "답변 LLM 출력 토큰 상한.",
    "llm_graph_budget": "빌드당 LLM 추출 호출 상한 (0=무제한). 신규 문서 20개/일 × 10청크 ≈ 200회.",
    "llm_graph_min_chars": "이보다 짧은 청크는 LLM 추출 생략.",
    "query_cache_size": "질의 캐시 항목 수.",
    "auto_build_interval": "auto_build 스캔 주기(초).",
    "embed_batch": "임베딩 배치 크기 (API 임베더는 64 이하 권장).",
    "debug_level": "프로파일 상세도: 0 요약 · 1 디버그 메타/로그 · 2 프롬프트/응답 원문 샘플.",
    "keep_requests": "requests 테이블 보존 개수.",
    "embed_dim": "hash 임베딩 차원 (메모리 = 청크수×dim×4B; float16 저장 시 절반). 모든 차원 지원, 변경 시 build --full.",
    "timezone": "상대 시간 표현(지난주·어제·3일전) 해석 기준 시간대. 기본 Asia/Seoul.",
    "week_start": "주 시작 요일 (mon|sun) — '지난주' 범위 계산.",
    "log_level": "logs/ 파일 로그 레벨 (DEBUG 면 단계별 상세까지).",
    "log_max_mb": "로그 파일당 최대 크기(MB). 초과 시 로테이션.",
    "log_backups": "로테이션 보관 파일 수.",
    "log_console": "콘솔(stderr)에도 WARNING 이상 출력.",
    "openai_base_url": "OpenAI-compatible 서버 base URL (…/v1). vLLM·LM Studio·Ollama·OpenRouter·사내 게이트웨이.",
    "openai_embed_model": "embed_provider=openai 일 때 임베딩 모델명.",
    "rerank_url": "rerank_method=api 엔드포인트 URL (Cohere/Jina/vLLM /v1/rerank, Voyage /v1/rerank).",
    "rerank_model": "rerank API 모델명.",
    "rerank_api_style": "rerank 응답 포맷: cohere(=jina/vLLM results[].relevance_score) | voyage(data[].relevance_score).",
    "embed_batch_max": "적응형 임베딩 배치 상한 (성공이 이어지면 embed_batch 에서 이 값까지 증가).",
    "embed_batch_target_ms": "배치 1회 목표 지연(ms). 초과하면 배치 축소.",
    "embed_commit_every": "N 배치마다 commit·진행률 저장 (중단 후 재개 단위).",
    "wal_checkpoint_mb": "빌드 중 WAL 파일이 이 크기(MB)를 넘으면 체크포인트.",
    "embed_store_dtype": "벡터 저장/행렬 dtype: float32 | float16 (메모리 절반).",
    "build_lock_timeout": "다른 프로세스가 빌드 중일 때 락을 기다릴 초 (0=즉시 실패).",
}
