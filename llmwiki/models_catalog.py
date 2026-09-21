# -*- coding: utf-8 -*-
"""사용 가능한 LLM/임베딩 모델 카탈로그 — <루트>/models.json (원본 setup/models.example.json).

- 사람이 추가/삭제하는 목록. Web 설정의 모델 드롭다운, `models list`, `/api/models/catalog` 가 이 파일을 읽는다.
- 항목: {id, provider, label, roles(비우면 전 역할), tags, notes, enabled, context_k}. 임베딩은 "embed" 배열 (id, provider, dim).
- `models discover` 는 Ollama(/api/tags) · OpenAI-compatible(/v1/models) 서버가 실제로 제공하는 모델을 조회해 추가 후보를 보여 준다.
- 카탈로그는 '안내용' 이다: 설정에 카탈로그에 없는 모델을 적어도 동작하며(unknown 표시만), 검증은 `models test --live`.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from . import atomicio
from .config import path_for, ROOT

_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"mtime": None, "data": None, "path": None}

DEFAULT_CATALOG: Dict[str, Any] = {
    "_comment": "사용 가능한 LLM/임베딩 모델 목록 (Web 설정 드롭다운 · `models list` · `/api/models/catalog`). 항목 추가/삭제: `models catalog add <id> --provider ollama --label ... --roles answer,rerank` / `models catalog remove <id>` 또는 Web › Settings › 모델 › 카탈로그. roles 를 비우면 모든 역할에 표시. enabled=false 면 목록에서 숨김. 서버가 실제 제공하는 모델은 `models discover` 로 조회. 설명: docs/BRINGUP_GUIDE.md §3.4",
    "models": [
        {"id": "claude-fable-5-1", "provider": "anthropic", "label": "Claude Fable 5.1 (최상위)", "roles": ["answer", "review", "forensic"], "tags": ["quality"], "context_k": 200, "enabled": True, "notes": "Anthropic 직접 또는 anthropic_base_url 게이트웨이"},
        {"id": "claude-opus-5", "provider": "anthropic", "label": "Claude Opus 5", "roles": ["answer", "review", "forensic", "extract"], "tags": ["quality"], "context_k": 200, "enabled": True, "notes": ""},
        {"id": "claude-sonnet-5", "provider": "anthropic", "label": "Claude Sonnet 5", "roles": [], "tags": ["balanced"], "context_k": 200, "enabled": True, "notes": "전 역할 기본 추천"},
        {"id": "claude-haiku-4-5-20251001", "provider": "anthropic", "label": "Claude Haiku 4.5", "roles": ["rerank", "expand", "verify", "summary"], "tags": ["fast", "cheap"], "context_k": 200, "enabled": True, "notes": "보조 역할용"},
        {"id": "llama3.1", "provider": "ollama", "label": "Llama 3.1 8B (local)", "roles": [], "tags": ["local"], "context_k": 128, "enabled": True, "notes": "ollama pull llama3.1"},
        {"id": "qwen2.5:7b", "provider": "ollama", "label": "Qwen2.5 7B (local)", "roles": [], "tags": ["local", "korean"], "context_k": 32, "enabled": True, "notes": "ollama pull qwen2.5:7b"},
        {"id": "exaone3.5:7.8b", "provider": "ollama", "label": "EXAONE 3.5 7.8B (local, 한국어)", "roles": [], "tags": ["local", "korean"], "context_k": 32, "enabled": True, "notes": ""},
        {"id": "gpt-4o-mini", "provider": "openai", "label": "gpt-4o-mini (OpenAI-compatible 게이트웨이)", "roles": [], "tags": ["gateway"], "context_k": 128, "enabled": True, "notes": "openai_base_url + PAT"},
        {"id": "Qwen/Qwen2.5-7B-Instruct", "provider": "openai", "label": "Qwen2.5-7B-Instruct (vLLM)", "roles": [], "tags": ["gateway", "local"], "context_k": 32, "enabled": True, "notes": "vLLM/LM Studio 의 모델 id"},
        {"id": "anthropic/claude-sonnet-4-5", "provider": "headless:opencode", "label": "opencode → claude-sonnet-4-5", "roles": [], "tags": ["headless"], "context_k": 200, "enabled": True, "notes": "agents.json opencode; model 은 provider/model 형식"},
        {"id": "mock", "provider": "mock", "label": "mock (테스트용, 네트워크 없음)", "roles": [], "tags": ["test"], "context_k": 0, "enabled": True, "notes": "배선 검증·스트레스 테스트"},
    ],
    "embed": [
        {"id": "", "provider": "hash", "label": "hash (local n-gram, dim=embed_dim)", "dim": 0, "enabled": True, "notes": "오프라인 기본"},
        {"id": "bge-m3", "provider": "ollama", "label": "bge-m3 (ollama, 1024d)", "dim": 1024, "enabled": True, "notes": "ollama pull bge-m3"},
        {"id": "nomic-embed-text", "provider": "ollama", "label": "nomic-embed-text (ollama, 768d)", "dim": 768, "enabled": True, "notes": ""},
        {"id": "text-embedding-3-small", "provider": "openai", "label": "text-embedding-3-small (1536d)", "dim": 1536, "enabled": True, "notes": "openai_embed_base_url"},
        {"id": "voyage-3.5", "provider": "voyage", "label": "voyage-3.5 (1024d)", "dim": 1024, "enabled": True, "notes": "VOYAGE_API_KEY"},
        {"id": "BAAI/bge-m3", "provider": "st", "label": "BAAI/bge-m3 (sentence-transformers)", "dim": 1024, "enabled": True, "notes": "pip install sentence-transformers"},
    ],
    # rerank API 모델 (rerank_url + rerank_api_model). LLM 리랭크(역할 rerank)와는 다른 경로다 —
    # 역할 rerank 는 위 models 목록에서 고르고, 여기 항목은 전용 rerank 엔드포인트의 모델 이름이다.
    "rerank": [
        {"id": "BAAI/bge-reranker-v2-m3", "provider": "cohere", "label": "bge-reranker-v2-m3 (vLLM/Jina 호환 /v1/rerank)", "enabled": True, "notes": "rerank_api_style=cohere"},
        {"id": "rerank-multilingual-v3.0", "provider": "cohere", "label": "Cohere rerank-multilingual-v3.0", "enabled": True, "notes": "COHERE_API_KEY"},
        {"id": "jina-reranker-v2-base-multilingual", "provider": "cohere", "label": "Jina reranker v2 (multilingual)", "enabled": True, "notes": "JINA_API_KEY"},
        {"id": "rerank-2", "provider": "voyage", "label": "Voyage rerank-2", "enabled": True, "notes": "rerank_api_style=voyage"},
    ],
}

PROVIDERS = ["auto", "anthropic", "openai", "ollama", "headless:opencode", "headless:claude", "headless:codex", "headless:mock", "mock", "none"]
EMBED_PROVIDERS = ["auto", "hash", "voyage", "openai", "ollama", "st"]
RERANK_PROVIDERS = ["cohere", "voyage"]        # rerank_api_style 과 같은 값 (응답 포맷)
SECTIONS = ("models", "embed", "rerank")       # kind → 카탈로그 절. kind 를 안 주면 llm(=models)


def catalog_path() -> str:
    return path_for("models")


def load_catalog(force: bool = False) -> Dict[str, Any]:
    p = catalog_path()
    with _LOCK:
        try:
            mt = os.path.getmtime(p)
        except OSError:
            mt = None
        if not force and _CACHE["data"] is not None and _CACHE["mtime"] == mt and _CACHE["path"] == p:
            return _CACHE["data"]
        if mt is None:
            data = json.loads(json.dumps(DEFAULT_CATALOG))
            try:
                _write(data, p)
                mt = os.path.getmtime(p)
            except Exception:
                pass
        else:
            try:
                data = atomicio.read_json(p)
                if not isinstance(data, dict):
                    raise ValueError("models.json 형식 오류")
            except Exception:
                data = json.loads(json.dumps(DEFAULT_CATALOG))
        for sect in SECTIONS:
            data.setdefault(sect, [])
            if sect == "rerank" and not data[sect]:
                # 예전 models.json 에는 rerank 절이 없다 — 파일을 건드리지 않고 기본 목록만 채워 준다
                data[sect] = json.loads(json.dumps(DEFAULT_CATALOG["rerank"]))
        for m in data["models"]:
            _norm(m)
        for sect in ("embed", "rerank"):
            for m in data[sect]:
                _norm(m)
        _CACHE.update(mtime=mt, data=data, path=p)
        return data


def _norm(m: Dict[str, Any]) -> Dict[str, Any]:
    m["id"] = str(m.get("id") or "")
    m["provider"] = str(m.get("provider") or "auto")
    m.setdefault("label", m["id"])
    m["roles"] = [str(r) for r in (m.get("roles") or [])]
    m["tags"] = [str(t) for t in (m.get("tags") or [])]
    m.setdefault("notes", "")
    m["enabled"] = bool(m.get("enabled", True))
    return m


def _write(data: Dict[str, Any], p: Optional[str] = None) -> str:
    p = p or catalog_path()
    out = {"_comment": DEFAULT_CATALOG["_comment"]}
    out.update({k: v for k, v in data.items() if not k.startswith("_")})
    return atomicio.write_json(p, out)


def save_catalog(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("models", []), list):
        raise ValueError("catalog 는 {models:[...], embed:[...]} 형태여야 합니다")
    for m in data.get("models") or []:
        if not isinstance(m, dict) or not m.get("id"):
            raise ValueError("models 항목마다 id 가 필요합니다")
        _norm(m)
    _write(data)
    return load_catalog(force=True)


def add_model(m: Dict[str, Any]) -> Dict[str, Any]:
    if not m.get("id"):
        raise ValueError("id 필요")
    data = json.loads(json.dumps(load_catalog(force=True)))
    m = _norm(dict(m))
    kind = str(m.pop("kind", "llm") or "llm")
    sect = kind if kind in ("embed", "rerank") else "models"
    lst = data.setdefault(sect, [])
    for i, x in enumerate(lst):
        if x.get("id") == m["id"] and (x.get("provider") == m["provider"] or not m.get("provider")):
            lst[i] = dict(x, **m)
            break
    else:
        lst.append(m)
    _write(data)
    return load_catalog(force=True)


def remove_model(mid: str, provider: Optional[str] = None) -> Dict[str, Any]:
    data = json.loads(json.dumps(load_catalog(force=True)))
    n0 = sum(len(data.get(s, [])) for s in SECTIONS)
    for sect in SECTIONS:
        data[sect] = [x for x in data.get(sect, []) if not (x.get("id") == mid and (provider is None or x.get("provider") == provider))]
    if sum(len(data[s]) for s in SECTIONS) == n0:
        raise ValueError("카탈로그에 없는 모델: %s" % mid)
    _write(data)
    return load_catalog(force=True)


def provider_for(model_id: str) -> Optional[str]:
    """model id 가 카탈로그의 enabled LLM 항목에 **정확히 하나의 provider** 로만 있으면 그 provider, 아니면 None.

    계획 §0.1-(b): 역할 provider 를 비우고 model 만 적어도(예 "anthropic/claude-sonnet-4-5") 카탈로그가 가리키는 provider
    (headless:opencode) 가 선택되게 한다. 같은 id 가 두 provider 에 있으면(예 qwen2.5:7b 가 ollama 와 openai) 판단하지 않는다.
    provider 가 auto/빈 값인 항목은 정보가 없는 것으로 본다. 파일이 없거나 깨져도 None (설정 해석이 카탈로그 때문에 죽지 않게)."""
    mid = str(model_id or "").strip()
    if not mid:
        return None
    try:
        provs = {str(m.get("provider") or "") for m in load_catalog().get("models", [])
                 if m.get("enabled", True) and str(m.get("id") or "") == mid}
    except Exception:
        return None
    provs.discard("")
    provs.discard("auto")
    return provs.pop() if len(provs) == 1 else None


def window_tokens(model_id: str) -> int:
    """이 모델이 받아 주는 입력 창(토큰). 카탈로그의 `context_k`(천 토큰 단위) 기준, 모르면 0.

    같은 id 가 여러 provider 에 있으면 **가장 작은 값**을 쓴다 — 창을 넘겨 잘리는 쪽이
    조금 덜 넣는 쪽보다 나쁘기 때문이다(잘리면 뒤쪽 근거가 통째로 사라지고, 모델은 그 사실을 말해 주지 않는다).
    """
    mid = str(model_id or "").strip()
    if not mid:
        return 0
    try:
        ks = [int(m.get("context_k") or 0) for m in load_catalog().get("models", [])
              if m.get("enabled", True) and str(m.get("id") or "") == mid]
    except Exception:
        return 0
    ks = [k for k in ks if k > 0]
    return min(ks) * 1000 if ks else 0


def context_budget(settings: Any, role: str = "answer", configured: Optional[int] = None) -> Dict[str, Any]:
    """답변 컨텍스트에 실제로 넣을 **글자 수 상한**을 정한다.

    ## 왜 필요한가

    `context_max_chars` 는 사람이 정한 값이고, 모델의 입력 창과는 아무 관계가 없었다. 그래서 창이 작은 모델
    (예 `context_k: 32`, 로컬 7B)에 `deep_research` 프리셋(20000자)을 걸면, 프롬프트가 조용히 잘린 채로
    호출된다 — **뒤쪽 근거가 통째로 사라지고** 모델은 그 사실을 말해 주지 않아서, 인용 번호만 맞고 내용은
    비는 답이 나온다. 카탈로그에 `context_k` 가 이미 있는데도 쓰이지 않던 값이라 여기서 연결한다.

    ## 계산

        여유 토큰 = 창 − 출력 상한(answer_max_tokens) − 예비(context_budget_reserve_tokens: 시스템 프롬프트·질문·형식)
        상한(글자) = min(설정값, 여유 토큰 × context_chars_per_token)

    `context_chars_per_token` 기본 2.0 은 한글·혼합 문서에서 **토큰을 넉넉히 잡는**(= 안전한) 값이다.
    영문 위주 코퍼스라면 3~4 로 올려 더 많이 넣을 수 있다. 창을 모르면(0) 설정값을 그대로 쓴다 —
    카탈로그에 없는 모델 때문에 근거가 줄어들지는 않게 한다.

    반환: {chars, configured, window_tokens, model, limited(bool), reason}
    """
    from . import tuning as _tuning
    cfg = int(configured if configured is not None else getattr(settings, "context_max_chars", 9000) or 9000)
    out: Dict[str, Any] = {"chars": cfg, "configured": cfg, "window_tokens": 0, "model": "", "limited": False, "reason": ""}
    try:
        roles = getattr(settings, "llm_roles", None) or {}
        r = roles.get(role) if isinstance(roles, dict) else None
        model = str((r or {}).get("model") or "") or str(getattr(settings, "llm_model", "") or "")
        out["model"] = model
        win = window_tokens(model)
        out["window_tokens"] = win
        if win <= 0:
            out["reason"] = "카탈로그에 창 정보 없음 (context_k) — 설정값 사용"
            return out
        T = _tuning.T
        reserve = int(T.get("context_budget_reserve_tokens"))
        cpt = float(T.get("context_chars_per_token"))
        free = win - int(getattr(settings, "answer_max_tokens", 2000) or 2000) - reserve
        allowed = int(max(0, free) * cpt)
        if allowed < cfg:
            out["chars"] = max(500, allowed)     # 0 으로 만들지는 않는다 — 근거 없는 답보다 조금이라도 넣는 편이 낫다
            out["limited"] = True
            out["reason"] = "모델 창 %d토큰 − 출력 %d − 예비 %d → %d자 (설정 %d자)" % (
                win, int(getattr(settings, "answer_max_tokens", 2000) or 2000), reserve, out["chars"], cfg)
        else:
            out["reason"] = "모델 창 %d토큰 안에 설정값 %d자가 들어감" % (win, cfg)
    except Exception as e:
        out["reason"] = "예산 계산 실패(설정값 사용): %s" % str(e)[:80]
    return out


def list_models(role: Optional[str] = None, provider: Optional[str] = None, enabled_only: bool = True) -> List[Dict[str, Any]]:
    out = []
    for m in load_catalog().get("models", []):
        if enabled_only and not m.get("enabled", True):
            continue
        if provider and m.get("provider") != provider and not (provider == "auto"):
            continue
        if role and m.get("roles") and role not in m["roles"]:
            continue
        out.append(m)
    return out


def describe(settings: Any = None, role: Optional[str] = None) -> Dict[str, Any]:
    """Web/CLI 용: 목록 + provider 별 그룹 + 현재 설정이 쓰는 모델과 카탈로그 밖 모델."""
    cat = load_catalog()
    models = list_models(role=role, enabled_only=False)
    by_prov: Dict[str, List[str]] = {}
    for m in models:
        if m.get("enabled", True):
            by_prov.setdefault(m["provider"], []).append(m["id"])
    in_use: Dict[str, Dict[str, str]] = {}
    unknown: List[Dict[str, str]] = []
    if settings is not None:
        try:
            for r in settings.LLM_ROLES:
                c = settings.role_llm(r)
                in_use[r] = {"provider": c["provider"], "model": c["model"], "kind": "llm",
                             "source": "역할 설정" if (getattr(settings, "llm_roles", None) or {}).get(r, {}).get("model") else "전역 상속"}
                if c["provider"] not in ("none", "mock") and not any(m["id"] == c["model"] and (m["provider"] == c["provider"] or c["provider"] == "auto") for m in cat.get("models", [])):
                    unknown.append({"role": r, "provider": c["provider"], "model": c["model"]})
        except Exception:
            pass
        # 임베딩·리랭크(API)도 "지금 쓰는 모델" 이다 — 예전에는 역할 LLM 만 보여 줘서
        # 화면에서 임베딩/리랭크 모델이 아예 안 보였다 (2026-09-16).
        try:
            ep = str(getattr(settings, "embed_provider", "") or "auto")
            em = str(getattr(settings, "embed_model", "") or "")
            in_use["embed"] = {"provider": ep, "model": em or "(hash)", "kind": "embed",
                               "source": "config embed_provider/embed_model" + (" · auto 판정" if ep == "auto" else "")}
            if em and not any(m.get("id") == em for m in cat.get("embed", [])):
                unknown.append({"role": "embed", "provider": ep, "model": em})
        except Exception:
            pass
        try:
            ru = str(getattr(settings, "rerank_url", "") or "")
            rm = str(getattr(settings, "rerank_api_model", "") or "")
            in_use["rerank_api"] = {"provider": str(getattr(settings, "rerank_api_style", "cohere") or "cohere"),
                                    "model": rm or "(rerank_url 없음 — API 리랭크 꺼짐)", "kind": "rerank",
                                    "source": "config rerank_url/rerank_api_model"}
            if ru and rm and not any(m.get("id") == rm for m in cat.get("rerank", [])):
                unknown.append({"role": "rerank_api", "provider": str(getattr(settings, "rerank_api_style", "") or ""), "model": rm})
        except Exception:
            pass
    return {"path": catalog_path(), "models": models, "embed": cat.get("embed", []), "rerank": cat.get("rerank", []),
            "by_provider": by_prov, "providers": PROVIDERS, "embed_providers": EMBED_PROVIDERS, "rerank_providers": RERANK_PROVIDERS,
            "in_use": in_use, "unknown_in_use": unknown, "roles_note": "roles 가 빈 항목은 모든 역할에 표시"}


def discover(settings: Any, timeout: float = 3.0) -> Dict[str, Any]:
    """실제 서버가 제공하는 모델 조회 (추가 후보). Ollama /api/tags · OpenAI-compatible /v1/models."""
    import urllib.request
    out: Dict[str, Any] = {"ollama": [], "openai": [], "errors": {}}
    cat = load_catalog()
    known = {(m["provider"], m["id"]) for m in cat.get("models", [])}
    try:
        with urllib.request.urlopen(str(getattr(settings, "ollama_url", "")).rstrip("/") + "/api/tags", timeout=timeout) as r:
            for m in (json.loads(r.read().decode("utf-8")).get("models") or []):
                out["ollama"].append({"id": m.get("name"), "in_catalog": ("ollama", m.get("name")) in known, "size": m.get("size")})
    except Exception as e:
        out["errors"]["ollama"] = str(e)[:160]
    base = str(getattr(settings, "openai_base_url", "") or "").rstrip("/")
    if base:
        try:
            key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY") or ""
            hdr = str(getattr(settings, "openai_api_key_header", "authorization") or "authorization").lower()
            headers: Dict[str, str] = dict(getattr(settings, "openai_extra_headers", None) or {})
            if key:
                headers[hdr if hdr != "authorization" else "Authorization"] = ("Bearer " + key) if hdr == "authorization" else key
            req = urllib.request.Request(base + "/models", headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                for m in (json.loads(r.read().decode("utf-8")).get("data") or []):
                    out["openai"].append({"id": m.get("id"), "in_catalog": ("openai", m.get("id")) in known})
        except Exception as e:
            out["errors"]["openai"] = str(e)[:160]
    out["ts"] = time.time()
    return out
