# -*- coding: utf-8 -*-
"""파이프라인 오케스트레이션: build / query / eval / graph inspect / auto-build / system info.

모든 공개 함수는 (result, trace) 를 돌려주며 trace 는 Profiler 트리(JSON) 이다.
모든 요청(build/query/eval/search)의 trace 는 requests 테이블에 저장되어 Web UI 'Requests' 탭에서 단계별로 확인할 수 있다.

역할별 LLM: llm_for("answer"|"rerank"|"extract"|"summary"|"review") — config.llm_roles 로 역할마다 다른 프로바이더/모델/effort.
확장(3,000+ 문서, 매일 수십 개 추가) 을 위한 장치:
  - stat_skip: mtime/size 가 같은 파일은 읽지 않음 (load_corpus 가 파일 수에 비례하는 stat 만 수행)
  - 증분 빌드에서 변경된 문서의 청크만 재색인/재임베딩/재추출, 변경 엔티티 페이지만 위키 재작성
  - hash IDF 재적합·커뮤니티 재탐지·FTS optimize 는 전체 빌드에서만 (토글로 증분에도 가능)
  - auto_build: 서버가 주기적으로 코퍼스를 stat 스캔해 변경이 있을 때만 증분 빌드
  - warm_cache: 빌드 직후 벡터 행렬/엔티티 인덱스 적재 → 첫 질의 지연 제거
  - query_cache / context_trim / dedupe_hits / rerank_llm 토글로 질의 토큰·지연 절감
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from .answer import build_context, generate_answer
from .config import Settings, apply_overrides
from .corpus import Document, chunk_document, iter_corpus, scan_changed
from .evalset import aggregate, load_questions, score_result
from .graph_build import build_graph_for_chunks, finalize_graph
from .graph_rules import RuleExtractor, load_rules
from .profiler import Profiler, jsonable
from .providers import make_embedder, make_llm, HashEmbedder, MODEL_CATALOG
from .retrieval import fts_search, vector_search, graph_search, rrf_fuse, rerank, route, Hit
from .store import Store
from .textutil import tokenize_for_fts, sha1, set_tokenizer
from .wiki import write_wiki, wiki_notes
from . import schema as _schema
from . import query_rules as _qrules
from . import tuning as _tuning
from . import prompts as _prompts
from . import logging_setup as _log
from . import progress as _pg
from .providers import parse_json, LLMError


def _role_overridden(cfg: Any) -> bool:
    """llm_roles.<role> 에 '실제로 지정한 값' 이 있는지. `config fill-defaults` 가 적어 둔 빈 뼈대("" · 꺼진 ensemble)는 지정이 아니다."""
    if not isinstance(cfg, dict):
        return False
    for k, v in cfg.items():
        if k == "ensemble":
            if isinstance(v, dict) and v.get("enabled"):
                return True
            continue
        if v not in (None, "", {}, []):
            return True
    return False
from . import providers as _providers
from .buildlock import BuildLock, BuildLockedError

BUILD_CHANNELS = ("fts", "vector", "graph")


class _LlmCache(dict):
    """역할별 LLM 인스턴스 캐시. 키는 (role, 역할설정, 전역서명) 튜플이라 요청마다 다른 모델을 써도 서로 덮어쓰지 않는다.
    하위 호환/테스트: `pipe._llms["answer"] = obj` 처럼 **문자열 키로 대입하면 그 역할을 그 인스턴스로 고정**한다(목업 주입)."""

    def __init__(self) -> None:
        dict.__init__(self)
        self.pins: Dict[str, Any] = {}

    def __setitem__(self, k: Any, v: Any) -> None:
        if isinstance(k, str):
            self.pins[k] = v
        else:
            dict.__setitem__(self, k, v)

    def __contains__(self, k: Any) -> bool:
        return (k in self.pins) if isinstance(k, str) else dict.__contains__(self, k)

    def get(self, k: Any, default: Any = None) -> Any:
        return self.pins.get(k, default) if isinstance(k, str) else dict.get(self, k, default)

    def pop(self, k: Any, default: Any = None) -> Any:
        return self.pins.pop(k, default) if isinstance(k, str) else dict.pop(self, k, default)


PROVIDER_SIG_KEYS = ("llm_provider", "llm_model", "llm_effort", "answer_effort", "llm_fallbacks", "ollama_url", "ollama_model",
                     "openai_base_url", "openai_api_key_header", "openai_extra_headers", "anthropic_base_url",
                     "llm_timeout", "llm_retries", "llm_retry_backoff_s", "llm_retry_backoff", "llm_retry_backoff_max_s", "llm_budget_s",
                     "llm_http_retries", "llm_circuit_failures", "llm_circuit_cooldown_s")
EMBED_SIG_KEYS = ("embed_provider", "embed_model", "embed_dim", "openai_embed_base_url", "openai_embed_model", "openai_base_url",
                  "openai_api_key_header", "ollama_url", "embed_store_dtype")


class Pipeline:
    """파이프라인 하나가 서버 전체(모든 스레드)에 공유된다.

    병렬 처리(2026-09-15):
    - `s` 는 스레드 로컬: request_scope() 안에서는 그 요청만의 Settings 사본을 돌려주고, 밖에서는 전역(base) 설정. 요청 단위 오버라이드·프리셋이
      다른 사용자의 질의에 섞이지 않는다. 설정을 영구 저장하는 경로(config/models set)는 reload() 가 사본을 전역으로 승격한다.
    - LLM/임베더 인스턴스는 (역할, provider, model, 정책) 서명으로 캐시되어, 요청마다 다른 모델을 써도 reload 없이 공존한다.
    - 튜닝(_tuning.T) 은 요청 오버레이, 저장소(Store) 는 스레드별 연결(request_scope 가 session 을 연다).
    """

    def __init__(self, settings: Settings):
        self._tls = threading.local()
        self._base_s = settings
        os.makedirs(settings.data_dir, exist_ok=True)
        try:
            _log.setup_from_settings(settings)
        except Exception:
            pass
        _tuning.load_tuning()
        self._init_text_plugins()
        self.store = Store(settings.db_path, busy_timeout_s=float(getattr(settings, "db_busy_timeout_s", 30.0) or 30.0),
                           pool_size=int(getattr(settings, "db_pool_size", 16) or 16),
                           max_live=int(getattr(settings, "db_max_live_connections", 64) or 0),
                           wait_timeout_s=float(getattr(settings, "db_pool_wait_timeout_s", 2.0) or 0.0))
        self._seen_version = self.store.build_version()
        self._llms = _LlmCache()
        self._embedders: Dict[str, Any] = {}
        self._embedder_pin: Any = None       # pipe._embedder = obj 로 고정한 임베더 (테스트/목업)
        self._prov_lock = threading.RLock()
        self._rules: Optional[RuleExtractor] = None
        self._qcache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._qcache_stats = {"hits": 0, "misses": 0}
        self._qlock = threading.Lock()
        self._lock = threading.RLock()
        self.watcher: Dict[str, Any] = {"enabled": False, "last_scan": None, "last_result": None, "last_build": None,
                                        "builds": 0, "errors": 0}

    # ---------- 요청 단위 설정 (스레드 로컬) ----------
    @property
    def s(self) -> Settings:
        ov = getattr(self._tls, "settings", None)
        return ov if ov is not None else self._base_s

    @s.setter
    def s(self, v: Settings) -> None:
        if getattr(self._tls, "settings", None) is not None:
            self._tls.settings = v
        else:
            self._base_s = v

    @property
    def base_settings(self) -> Settings:
        return self._base_s

    def in_request_scope(self) -> bool:
        return getattr(self._tls, "settings", None) is not None

    # ---------- 요청자 신분 (스레드 로컬) ----------
    # 2026-09-19: 검색이 "누가 묻는가" 를 알아야 문서 단위 접근 제어를 할 수 있다 (llmwiki/docacl.py).
    # 함수 서명을 줄줄이 바꾸는 대신, 요청 범위와 같은 스레드 로컬에 실어 보낸다 —
    # Web/MCP/CLI/스케줄러가 모두 request_scope() 로 감싸므로 한 자리에서 들어오고 한 자리에서 지워진다.
    @property
    def actor(self) -> Dict[str, str]:
        return getattr(self._tls, "actor", None) or {"user": "", "role": "admin", "origin": ""}

    @actor.setter
    def actor(self, v: Optional[Dict[str, str]]) -> None:
        self._tls.actor = dict(v or {})

    def acl_filter(self):
        """이 요청자의 문서 접근 판정기. 규칙이 없으면 아무도 막지 않는다."""
        from . import docacl as _acl
        if not bool(getattr(self.s.toggles, "doc_acl", True)):
            return _acl.Filter("admin", {}, {"enabled": False, "rules": [], "default_min_role": "viewer"})
        return _acl.Filter(self.actor.get("role") or "viewer", self.store.doc_meta_map())

    @property
    def tuning(self):
        return _tuning.T

    @tuning.setter
    def tuning(self, _v: Any) -> None:   # 구 코드 호환 (self.tuning = load_tuning())
        pass

    @contextlib.contextmanager
    def request_scope(self, overrides: Optional[Dict[str, Any]] = None, presets: Optional[List[str]] = None, mode: str = "",
                      actor: Optional[Dict[str, str]] = None):
        """요청 하나의 격리 범위: 설정 사본(+overrides) · 튜닝 오버레이(+presets/mode) · 스레드 전용 DB 연결 · **요청자 신분**.
        Web/MCP/CLI(콘솔)/스케줄러/워처가 모두 이걸로 감싼다. 중첩되면 바깥 사본을 바탕으로 다시 사본을 만든다.

        actor: {"user","role","origin"} — 문서 단위 접근 제어(docacl)가 이 역할로 근거를 거른다.
        주지 않으면 바깥 범위의 값을 잇고, 그것도 없으면 admin (CLI·내부 호출은 제한하지 않는다)."""
        prev_actor = getattr(self._tls, "actor", None)
        if actor is not None:
            self._tls.actor = dict(actor)
        prev_s = getattr(self._tls, "settings", None)
        base = prev_s if prev_s is not None else self._base_s
        s_local = base.copy()
        if overrides is not None and not isinstance(overrides, dict):
            raise ValueError("overrides 는 객체(JSON object)여야 합니다 (받은 값: %s)" % type(overrides).__name__)
        # overrides["tuning"] = {키: 값} 은 Settings 가 아니라 이 요청의 튜닝 오버레이로 간다 (Web/MCP/CLI --tuning 이 같은 길).
        # 모르는 키·범위 밖 값은 ValueError 로 바로 알린다 — 조용히 버리면 "설정이 안 먹는다" 로 보인다.
        tun_ov = overrides.get("tuning") if overrides else None
        if tun_ov is not None and not isinstance(tun_ov, dict):
            raise ValueError("overrides.tuning 은 객체(JSON object)여야 합니다 (받은 값: %s)" % type(tun_ov).__name__)
        if overrides:
            apply_overrides(s_local, {k: v for k, v in overrides.items() if k != "tuning"})
        self._tls.settings = s_local
        prev_ov = _tuning.T.push_overlay()
        if tun_ov:
            try:
                for k, v in tun_ov.items():
                    _tuning.T.set(str(k), v)
            except (KeyError, ValueError, TypeError) as e:
                _tuning.T.pop_overlay(prev_ov)
                self._tls.settings = prev_s
                raise ValueError("overrides.tuning: %s" % e)
        names = [x for x in (presets or []) if x]
        if mode == "deep":
            names.append("deep_research")
        elif mode == "fast":
            names.append("speed")
        info = None
        try:
            if names:
                from . import presets as _presets
                info = _presets.apply(s_local, names, save=False)   # 토글/설정은 사본에, 튜닝은 오버레이에
            with self.store.session():
                yield s_local
        finally:
            _tuning.T.pop_overlay(prev_ov)
            self._tls.settings = prev_s
            if actor is not None:
                self._tls.actor = prev_actor

    # ---------- lazy providers ----------
    @property
    def llm(self):
        """기본(전역) LLM — 역할 지정이 없는 호출용. 역할별은 llm_for(role)."""
        return self.llm_for("default")

    def _llm_key(self, role: str) -> Tuple[Any, ...]:
        s = self.s
        # role_llm() 은 ensemble(기본값 병합·활성 멤버·취합기) 과 provider_source 까지 돌려주므로 앙상블 설정 변경(요청 단위 오버라이드 포함)도 서명에 들어간다
        rc = s.role_llm(role) if role != "default" else {"provider": s.llm_provider, "model": s.llm_model}
        base = tuple((k, json.dumps(getattr(s, k, None), sort_keys=True, default=str)) for k in PROVIDER_SIG_KEYS)
        return (role, json.dumps(rc, sort_keys=True, default=str), base)

    def llm_for(self, role: str):
        pinned = self._llms.pins.get(role)
        if pinned is not None:
            if getattr(pinned, "role", "default") != role:
                pinned.role = role      # 고정 인스턴스도 역할을 달고 다녀야 실패 보고(llm_report)에 어느 역할인지 남는다
            return pinned
        key = self._llm_key(role)
        llm = dict.get(self._llms, key)
        if llm is None:
            with self._prov_lock:
                llm = dict.get(self._llms, key)
                if llm is None:
                    llm = make_llm(self.s, None if role == "default" else role)
                    if len(self._llms) >= 64:   # 요청 단위 오버라이드가 다양해도 무한히 늘지 않게
                        for k in list(self._llms.keys())[:16]:
                            dict.pop(self._llms, k, None)
                    dict.__setitem__(self._llms, key, llm)
        return llm

    def _embed_key(self) -> str:
        s = self.s
        return json.dumps({k: getattr(s, k, None) for k in EMBED_SIG_KEYS}, sort_keys=True, default=str)

    @property
    def embedder(self):
        if self._embedder_pin is not None:
            return self._embedder_pin
        key = self._embed_key()
        emb = self._embedders.get(key)
        if emb is None:
            with self._prov_lock:
                emb = self._embedders.get(key)
                if emb is None:
                    emb = make_embedder(self.s)
                    if isinstance(emb, HashEmbedder):
                        idf = self.store.kv_get("hash_idf")
                        if idf and len(idf) == emb.dim:
                            import numpy as np
                            emb._idf = np.asarray(idf, dtype="float32")
                    if len(self._embedders) >= 8:
                        self._embedders.clear()
                    self._embedders[key] = emb
        return emb

    @property
    def _embedder(self):
        """구 코드/테스트 호환: `pipe._embedder = obj` 는 임베더 고정, `= None` 은 고정 해제 + 캐시 비움."""
        return self._embedder_pin if self._embedder_pin is not None else self._embedders.get(self._embed_key())

    @_embedder.setter
    def _embedder(self, v: Any) -> None:
        if v is None:
            self._embedder_pin = None
            with self._prov_lock:
                self._embedders.clear()
        else:
            self._embedder_pin = v

    # ---------- 질의 캐시 (스레드 안전) ----------
    def qcache_get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._qlock:
            v = self._qcache.get(key)
            if v is not None:
                self._qcache.move_to_end(key)
                self._qcache_stats["hits"] += 1
            else:
                self._qcache_stats["misses"] += 1
            return v

    def qcache_put(self, key: str, value: Dict[str, Any]) -> None:
        with self._qlock:
            self._qcache[key] = value
            limit = max(1, int(self.s.query_cache_size or 1))
            while len(self._qcache) > limit:
                self._qcache.popitem(last=False)

    @property
    def rules(self) -> RuleExtractor:
        if self._rules is None:
            T = _tuning.T
            self._rules = RuleExtractor(load_rules(), T.get("cooccur_window"), T.get("cooccur_scale"), T.get("cooccur_min_w"),
                                        T.get("dates_per_chunk"), T.get("amounts_per_chunk"))
        return self._rules

    def _init_text_plugins(self) -> None:
        """토크나이저(heuristic/kiwi) + 복합어 사전(query_rules compound) — 색인과 질의가 같은 규칙을 쓴다."""
        try:
            self.tokenizer = set_tokenizer(_tuning.T.get("tokenizer"))
        except Exception:
            self.tokenizer = "heuristic"
        try:
            _qrules.load_rules()
        except Exception:
            pass

    def reload_tuning(self, from_file: bool = True) -> None:
        """튜닝 변경을 파이프라인에 반영(토크나이저·규칙·임베더·질의 캐시 재초기화).
        from_file=False 면 tuning.json 을 다시 읽지 않고 메모리의 전역 T 를 그대로 쓴다 — 프리셋(--preset)·fusion compare·MCP 처럼
        요청 단위로 T 를 바꾼 뒤 호출할 때 쓰던 호출인데, 이제 요청 오버레이(request_scope)가 그 역할을 하므로 오버레이 안에서는 아무것도 하지 않는다."""
        if not from_file and _tuning.T.has_overlay():
            return
        if from_file:
            _tuning.load_tuning()
        self._init_text_plugins()
        self._rules = None
        with self._prov_lock:
            self._embedders.clear()
        with self._qlock:
            self._qcache.clear()

    def _ensure_providers(self, prof: Profiler, roles: Tuple[str, ...]) -> None:
        """프로바이더 최초 생성 비용(ollama ping 0.4s×역할 등)을 숨기지 않고 'providers' 단계로 기록."""
        need = [r for r in roles if (r not in self._llms.pins and self._llm_key(r) not in self._llms)] + \
               (["embedder"] if (self._embedder_pin is None and self._embed_key() not in self._embedders) else [])
        if not need:
            return
        with prof.stage("providers", created=need) as st:
            info = {}
            for r in roles:
                llm = self.llm_for(r)      # 앙상블이면 make_llm 이 멤버·취합기까지 만든다
                members = getattr(llm, "members", None)
                info[r] = "%s/%s%s%s" % (llm.name, llm.model, (" (ensemble %d members)" % len(members)) if members else "",
                                         "" if llm.available else " (unavailable)")
            emb = self.embedder
            info["embedder"] = "%s d=%s" % (emb.name, emb.dim)
            st.note(**info)

    def reload(self) -> None:
        """설정/튜닝/프로바이더 재적재. 요청 범위 안에서 불리면(예: Web 콘솔 `config set`, /api/config) 그 요청이 저장한 설정 사본을 전역으로 승격한다."""
        local = getattr(self._tls, "settings", None)
        if local is not None:
            self._base_s = local.copy()
        _tuning.load_tuning()
        self._init_text_plugins()
        with self._prov_lock:
            pins = dict(self._llms.pins)     # 고정된 목업 인스턴스는 유지 (테스트/특수 주입)
            self._llms = _LlmCache()
            self._llms.pins.update(pins)
            self._embedders.clear()
        self._rules = None
        self.store.invalidate_caches()
        with self._qlock:
            self._qcache.clear()
        self._seen_version = self.store.build_version()
        try:
            _log.setup_from_settings(self._base_s)   # log_level 변경을 재시작 없이 반영
        except Exception:
            pass

    def sync_with_db(self) -> bool:
        """다른 프로세스(CLI 빌드, evolve apply)가 DB 를 바꿨으면 메모리 캐시(벡터 행렬·IDF·규칙·질의 캐시)를 버린다."""
        v = self.store.build_version()
        if v != getattr(self, "_seen_version", None):
            with self._prov_lock:
                self._embedders.clear()
            self._rules = None
            self.store.invalidate_caches()
            with self._qlock:
                self._qcache.clear()
            self._seen_version = v
            return True
        return False

    def reset_index(self, keep_logs: bool = True, keep_wiki_notes: bool = True, snapshot: bool = True, actor: str = "",
                    snapshot_keep: int = 3) -> Dict[str, Any]:
        """색인 완전 초기화. DB 파일을 지우는 대신 색인 테이블을 모두 비우고 VACUUM 한다.
        - 빌드 파일 락 안에서 실행 (다른 프로세스의 빌드와 겹치지 않게)
        - snapshot=True 면 지우기 전에 data/snapshots/ 에 자동 스냅샷 (`snapshot list|restore`) — 실수로 지워도 되돌릴 수 있다"""
        with self._lock:
            lock = BuildLock(os.path.join(self.s.data_dir, "build.lock"), timeout=self.s.build_lock_timeout,
                         stale_after_s=self.s.build_lock_stale_s, cmd="reset")
            lock.acquire()
            try:
                return self._reset_index(keep_logs, keep_wiki_notes, snapshot, actor, snapshot_keep)
            finally:
                lock.release()

    def _reset_index(self, keep_logs: bool, keep_wiki_notes: bool, snapshot: bool, actor: str, snapshot_keep: int) -> Dict[str, Any]:
        st = self.store
        snap = None
        if snapshot and st.stats().get("docs"):
            from . import snapshots as _snap
            try:
                snap = _snap.create(self, "auto:reset" + ("-purge" if not keep_logs else ""), actor=actor, reason="before reset_index")
                _snap.prune(self, keep=snapshot_keep)
            except Exception as e:
                _log.log("warning", "snapshot before reset failed: %s" % e, "build")
        _log.log("warning", "reset_index by %s (keep_logs=%s)" % (actor or "?", keep_logs), "build")
        index_tables = ["docs", "chunks", "chunks_fts", "embeddings", "entities", "entities_fts", "relations",
                        "mentions", "communities", "kv", "doc_meta", "doc_vectors", "answer_cache"]
        if st._has_trigram():
            index_tables.append("chunks_tri")
        log_tables = ["query_log", "proposals", "evolution_log", "synonyms", "requests", "forensics", "episodes", "trials", "embed_runs"]
        version = st.build_version()
        cleared = []
        for t in index_tables + ([] if keep_logs else log_tables):
            st.conn.execute("DELETE FROM %s" % t)
            cleared.append(t)
        st.conn.commit()
        try:
            st.conn.execute("VACUUM")
        except Exception:
            pass
        st.kv_set("build_version", version)
        removed_pages = 0
        if os.path.isdir(self.s.wiki_dir):
            from .wiki import _read_note
            for fn in os.listdir(self.s.wiki_dir):
                fp = os.path.join(self.s.wiki_dir, fn)
                if fn.endswith(".md") and not (keep_wiki_notes and _read_note(fp)):
                    os.remove(fp)
                    removed_pages += 1
        self.reload()
        return {"cleared_tables": cleared, "kept_logs": keep_logs, "removed_wiki_pages": removed_pages,
                "snapshot": (snap or {}).get("name"),
                "db_bytes": os.path.getsize(self.s.db_path) if os.path.exists(self.s.db_path) else 0}

    def provider_status(self) -> Dict[str, Any]:
        roles = {}
        for role in Settings.LLM_ROLES:
            cfg = self.s.role_llm(role)
            llm = self.llm_for(role)
            # describe() 는 앙상블이면 members/aggregator 를 포함한다. provider_source(role|global|catalog) 와 ensemble 설정은 configured 안에 있다.
            roles[role] = dict(llm.describe(), configured=cfg, overridden=_role_overridden((self.s.llm_roles or {}).get(role)),
                               provider_source=cfg.get("provider_source", ""), ensemble_enabled=bool((cfg.get("ensemble") or {}).get("enabled")))
        llm = self.llm
        emb = self.embedder
        try:
            from .headless import load_agents
            agents = {k: {"desc": v.get("desc", ""), "command": v.get("command", [None])[0], "model": v.get("model", "")} for k, v in load_agents().items()}
        except Exception:
            agents = {}
        return {"llm": {"name": llm.name, "available": llm.available, "model": getattr(llm, "model", None)},
                "roles": roles,
                "embedder": dict(emb.describe(), provider_setting=self.s.embed_provider, model_setting=self.s.embed_model,
                                 store_dtype=self.s.embed_store_dtype),
                "rerank": {"method": _tuning.T.get("rerank_method"), "url": self.s.rerank_url, "model": self.s.rerank_api_model,
                           "style": self.s.rerank_api_style},
                "openai": {"base_url": self.s.openai_base_url, "key": bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")),
                           "key_header": getattr(self.s, "openai_api_key_header", "authorization"), "embed_base_url": getattr(self.s, "openai_embed_base_url", "")},
                "anthropic": {"base_url": getattr(self.s, "anthropic_base_url", "") or "https://api.anthropic.com",
                              "key": bool(os.environ.get("ANTHROPIC_API_KEY")), "auth_token": bool(os.environ.get("ANTHROPIC_AUTH_TOKEN"))},
                "agents": agents,
                "catalog": MODEL_CATALOG,
                "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version}

    def test_providers(self, which: Optional[List[str]] = None, live: bool = False) -> Dict[str, Any]:
        """연결 테스트: 역할별 LLM ping + 임베더 1건 임베딩 (토큰 소비 없음/최소).
        live=True 면 역할별로 실제 완성 호출을 1회 한다(같은 provider/model 은 1회만) — PAT 권한·헤더·모델명·headless 실행까지 확인."""
        out: Dict[str, Any] = {}
        which = which or list(Settings.LLM_ROLES) + ["embedder"] + (["rerank_api"] if self.s.rerank_url else [])
        live_done: Dict[str, Dict[str, Any]] = {}
        for w in which:
            if w == "embedder":
                r = self.embedder.ping()
                out["embedder"] = dict(r, provider=self.embedder.name, model=getattr(self.embedder, "model", None))
            elif w == "rerank_api":
                from .rerankers import ping_rerank_api
                out["rerank_api"] = dict(ping_rerank_api(self.s), url=self.s.rerank_url, model=self.s.rerank_api_model)
            else:
                llm = self.llm_for(w)
                r = llm.ping()
                row = dict(r, provider=llm.name, model=llm.model, available=llm.available)
                if live:
                    key = "%s/%s" % (llm.name, llm.model)
                    if key not in live_done:
                        live_done[key] = llm.live_test()
                    lv = live_done[key]
                    # 2026-09-19: **실제 호출이 성공하면 그 프로바이더는 쓸 수 있다.** 예전에는 ping 의 ok 까지
                    # and 로 묶어서, 모델 목록 표기가 다르다는 이유만으로 "실제 호출 ✓" 인데도 ✗ 로 보였다.
                    # ping 결과는 ping_ok 로 따로 남겨 화면이 "연결은 됐지만 목록에 없음" 을 구분해 보여 준다.
                    row.update(live_ok=lv["ok"], live_ms=round(lv["ms"], 1), live_detail=lv["detail"],
                               ping_ok=bool(r.get("ok")), ok=bool(lv["ok"]))
                    if lv.get("members") is not None:     # 앙상블: 멤버·취합기별 live 결과도 그대로 싣는다
                        row["live_members"], row["live_aggregator"] = lv.get("members"), lv.get("aggregator")
                try:
                    cfg = self.s.role_llm(w)
                except Exception:
                    cfg = {}
                row["provider_source"] = cfg.get("provider_source", "")
                if cfg.get("ensemble", {}).get("enabled"):
                    row["ensemble"] = {k: cfg["ensemble"].get(k) for k in ("wait", "timeout_s", "min_results", "prompt", "aggregator")}
                    row["ensemble"]["members"] = [{"provider": m["provider"], "model": m["model"], "weight": m["weight"]} for m in cfg["ensemble"]["members"]]
                hint = self._catalog_hint(cfg.get("provider"), cfg.get("model"), cfg.get("provider_source", ""))
                if hint:
                    row["hint"] = hint
                out[w] = row
        return out

    @staticmethod
    def _catalog_hint(provider: Any, model: Any, source: str = "") -> str:
        """설정의 provider/model 짝이 카탈로그(models.json)와 다르면 한 줄 힌트 (계획 §0.1-c). 카탈로그가 자동으로 고른 경우도 알려 준다."""
        try:
            from . import models_catalog as _mc
            cp = _mc.provider_for(str(model or ""))
        except Exception:
            return ""
        p = str(provider or "")
        if not cp or p in ("auto", "mock", "none"):
            return ""
        if source == "catalog":
            return "provider 가 비어 있어 카탈로그의 provider %s 를 사용했습니다 (models.json: %s) — 다른 provider 를 쓰려면 llm_roles.<role>.provider 를 적으세요" % (cp, model)
        if cp != p:
            return "카탈로그에는 %s 이 provider %s 로 등록되어 있습니다 — provider 를 바꾸거나 카탈로그를 고치세요 (지금 provider=%s)" % (model, cp, p)
        return ""

    def test_catalog(self, live: bool = False, kinds: Optional[List[str]] = None) -> Dict[str, Any]:
        """카탈로그(models.json) 전체 연결 테스트 (계획 §0.1-d): enabled 항목마다 (provider, model) 로 ping, live=True 면 완성 호출 1회.
        kinds: ["llm","embed","rerank"] 중 검사할 절 (기본 llm+embed; rerank 는 rerank_url 이 있을 때만).
        반환 {"rows": [{id, provider, kind, label, ok, ms, detail, live_ok?, live_ms?, live_detail?}], "n", "ok_n", "live", "path"}."""
        from . import models_catalog as _mc
        from .providers import _make_llm, apply_policy, make_embedder
        cat = _mc.load_catalog(force=True)
        kinds = kinds or ["llm", "embed"] + (["rerank"] if self.s.rerank_url else [])
        rows: List[Dict[str, Any]] = []
        seen: Dict[str, Dict[str, Any]] = {}
        for m in cat.get("models", []):
            if "llm" not in kinds or not m.get("enabled", True):
                continue
            prov, mid = str(m.get("provider") or "auto"), str(m.get("id") or "")
            key = "llm:%s/%s" % (prov, mid)
            if key in seen:
                rows.append(dict(seen[key], id=mid, provider=prov, kind="llm", label=m.get("label") or "", detail="(위와 같은 provider/model)"))
                continue
            row: Dict[str, Any] = {"id": mid, "provider": prov, "kind": "llm", "label": m.get("label") or ""}
            t0 = time.perf_counter()
            try:
                llm = apply_policy(_make_llm(prov, mid, self.s), self.s, None)
                r = llm.ping()
                row.update(ok=bool(r.get("ok")), ms=round(float(r.get("ms") or (time.perf_counter() - t0) * 1000), 1), detail=str(r.get("detail") or ""),
                           available=bool(getattr(llm, "available", False)))
                # ping 이 실패해도 실제 호출은 해 본다: "모델 목록에 없다" 는 표기 차이일 뿐 호출은 되는 경우가 많다.
                # 실제 호출이 성공하면 그 모델은 **쓸 수 있는 것**이므로 최종 ok 는 live 결과를 따른다.
                if live:
                    lv = llm.live_test()
                    row.update(live_ok=bool(lv.get("ok")), live_ms=round(float(lv.get("ms") or 0), 1), live_detail=str(lv.get("detail") or ""),
                               ping_ok=row["ok"], ok=bool(lv.get("ok")))
            except Exception as e:
                row.update(ok=False, ms=round((time.perf_counter() - t0) * 1000, 1), detail="error: %s" % str(e)[:300])
            seen[key] = row
            rows.append(row)
        for m in cat.get("embed", []):
            if "embed" not in kinds or not m.get("enabled", True):
                continue
            prov, mid = str(m.get("provider") or "auto"), str(m.get("id") or "")
            row = {"id": mid or "(hash)", "provider": prov, "kind": "embed", "label": m.get("label") or ""}
            t0 = time.perf_counter()
            try:
                s2 = self.s.copy()
                s2.embed_provider, s2.embed_model = prov, mid
                if prov == "hash":
                    s2.embed_dim = min(int(s2.embed_dim or 256), 256)      # 검사용: 큰 hash 행렬을 만들 필요가 없다
                emb = make_embedder(s2)
                r = emb.ping()
                row.update(ok=bool(r.get("ok")), ms=round(float(r.get("ms") or (time.perf_counter() - t0) * 1000), 1),
                           detail=str(r.get("detail") or ""), dim=r.get("dim") or getattr(emb, "dim", None))
            except Exception as e:
                row.update(ok=False, ms=round((time.perf_counter() - t0) * 1000, 1), detail="error: %s" % str(e)[:300])
            rows.append(row)
        for m in cat.get("rerank", []):
            if "rerank" not in kinds or not m.get("enabled", True):
                continue
            prov, mid = str(m.get("provider") or "cohere"), str(m.get("id") or "")
            row = {"id": mid, "provider": prov, "kind": "rerank", "label": m.get("label") or ""}
            t0 = time.perf_counter()
            try:
                from .rerankers import ping_rerank_api
                s2 = self.s.copy()
                s2.rerank_api_style, s2.rerank_api_model = prov, mid
                r = ping_rerank_api(s2)
                row.update(ok=bool(r.get("ok")), ms=round(float(r.get("ms") or (time.perf_counter() - t0) * 1000), 1), detail=str(r.get("detail") or ""))
            except Exception as e:
                row.update(ok=False, ms=round((time.perf_counter() - t0) * 1000, 1), detail="error: %s" % str(e)[:300])
            rows.append(row)
        return {"rows": rows, "n": len(rows), "ok_n": sum(1 for r in rows if r.get("ok")), "live": bool(live), "path": _mc.catalog_path(),
                "note": "ping 만 확인" if not live else "ping + 실제 완성 호출 1회 (같은 provider/model 은 1회)"}

    def automap_models(self, live: bool = False, apply: bool = False,
                       test: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """**연결되는 모델만 골라 역할에 자동 배정한다** (2026-09-19).

        왜: 새 환경에 옮기면 카탈로그에는 모델이 20여 개 있는데 그중 무엇이 실제로 붙는지 모른 채
        역할 10개를 손으로 하나씩 맞춰야 했다. 카탈로그 전체 연결 테스트를 이미 돌리고 있으니,
        그 결과에서 **쓸 수 있는 모델만** 추려 역할에 한 번에 꽂아 준다.

        고르는 규칙 (먼저 맞는 것이 이긴다)
          1. 연결 테스트를 통과한 모델만 후보. live=True 면 실제 호출까지 성공한 것만.
          2. 카탈로그의 `roles` 가 그 역할을 (또는 `*` 로 전부를) 가리키는 모델.
          3. 그중 카탈로그에 적힌 순서가 앞선 것 — models.json 의 줄 순서가 곧 선호 순위다.
          4. 역할 전용 후보가 없으면 `*` 후보, 그것도 없으면 통과한 아무 LLM.
        임베딩과 API 리랭크도 같은 방식으로 하나씩 고른다.

        apply=False 면 제안만 돌려준다(기본). apply=True 면 config.json 의 `llm_roles`
        (+ 필요하면 embed_provider/embed_model·rerank_api_model)를 저장하고 프로바이더를 다시 만든다.
        """
        from . import models_catalog as _mc
        from .config import save_settings
        res = test or self.test_catalog(live=live)
        rows = res.get("rows") or []
        cat = _mc.load_catalog(force=True)
        order = {("%s/%s" % (m.get("provider") or "auto", m.get("id") or "")): i for i, m in enumerate(cat.get("models", []))}
        roles_of = {("%s/%s" % (m.get("provider") or "auto", m.get("id") or "")): list(m.get("roles") or ["*"])
                    for m in cat.get("models", [])}

        def passed(r):
            if not r.get("ok"):
                return False
            return bool(r.get("live_ok")) if (live and "live_ok" in r) else True

        # 순위: 폴백용(mock·hash)은 맨 뒤로 민다. 연결은 늘 되지만 품질이 목적이 아니라서,
        # 진짜 모델이 하나라도 붙으면 그쪽을 골라야 한다 (자동 매핑이 hash 임베딩을 고르면 검색 품질이 무너진다).
        FALLBACK = ("mock", "hash", "none")

        def rank(r):
            prov, mid = str(r.get("provider") or ""), str(r.get("id") or "")
            fb = 1 if (prov in FALLBACK or mid in ("", "(hash)")) else 0
            return (fb, order.get("%s/%s" % (prov, mid), 9999), mid)

        llm_ok = sorted([r for r in rows if r.get("kind") == "llm" and passed(r)], key=rank)
        embed_ok = sorted([r for r in rows if r.get("kind") == "embed" and passed(r)], key=rank)
        rerank_ok = sorted([r for r in rows if r.get("kind") == "rerank" and passed(r)], key=rank)

        proposal: Dict[str, Any] = {}
        for role in Settings.LLM_ROLES:
            cur = self.s.role_llm(role)
            pick, why = None, ""
            for r in llm_ok:
                rs = roles_of.get("%s/%s" % (r["provider"], r["id"]), ["*"])
                if role in rs:
                    pick, why = r, "카탈로그에서 %s 역할용으로 등록된 모델" % role
                    break
            if pick is None:
                for r in llm_ok:
                    if "*" in roles_of.get("%s/%s" % (r["provider"], r["id"]), ["*"]):
                        pick, why = r, "모든 역할에 쓸 수 있는 모델(roles=*)"
                        break
            if pick is None and llm_ok:
                pick, why = llm_ok[0], "이 역할용 모델이 없어 연결되는 첫 모델로"
            if pick is None:
                proposal[role] = {"kind": "llm", "provider": "", "model": "", "current": "%s/%s" % (cur["provider"], cur["model"]),
                                  "why": "연결되는 LLM 이 하나도 없습니다", "changed": False, "ok": False}
                continue
            newv = "%s/%s" % (pick["provider"], pick["id"])
            proposal[role] = {"kind": "llm", "provider": pick["provider"], "model": pick["id"],
                              "label": pick.get("label") or "", "ms": pick.get("ms"),
                              "current": "%s/%s" % (cur["provider"], cur["model"]),
                              "changed": newv != "%s/%s" % (cur["provider"], cur["model"]),
                              "why": why, "ok": True}
        if embed_ok:
            e = embed_ok[0]
            proposal["embed"] = {"kind": "embed", "provider": e["provider"], "model": e["id"] if e["id"] != "(hash)" else "",
                                 "label": e.get("label") or "", "dim": e.get("dim"),
                                 "current": "%s/%s" % (self.s.embed_provider, self.s.embed_model or "(hash)"),
                                 "changed": (e["provider"] != self.s.embed_provider or (e["id"] if e["id"] != "(hash)" else "") != self.s.embed_model),
                                 "why": "연결되는 임베딩 모델 중 카탈로그 첫 항목", "ok": True,
                                 "warn": "임베딩 모델을 바꾸면 벡터 채널을 다시 만들어야 합니다 (`build vector --full`)"}
        if rerank_ok:
            r0 = rerank_ok[0]
            proposal["rerank_api"] = {"kind": "rerank", "provider": r0["provider"], "model": r0["id"],
                                      "current": "%s/%s" % (self.s.rerank_api_style, self.s.rerank_api_model or "-"),
                                      "changed": r0["id"] != self.s.rerank_api_model,
                                      "why": "연결되는 리랭크 API 중 첫 항목", "ok": True}

        applied: List[str] = []
        if apply:
            roles_cfg = dict(self.s.llm_roles or {})
            for role in Settings.LLM_ROLES:
                p = proposal.get(role) or {}
                if not p.get("ok") or not p.get("changed"):
                    continue
                d = dict(roles_cfg.get(role) or {})
                d["provider"], d["model"] = p["provider"], p["model"]
                roles_cfg[role] = d
                applied.append("%s=%s/%s" % (role, p["provider"], p["model"]))
            self.s.llm_roles = roles_cfg
            pe = proposal.get("embed")
            if pe and pe.get("changed"):
                self.s.embed_provider, self.s.embed_model = pe["provider"], pe["model"]
                applied.append("embed=%s/%s" % (pe["provider"], pe["model"] or "(hash)"))
            pr = proposal.get("rerank_api")
            if pr and pr.get("changed"):
                self.s.rerank_api_style, self.s.rerank_api_model = pr["provider"], pr["model"]
                applied.append("rerank_api=%s" % pr["model"])
            if applied:
                save_settings(self.s)
                self.reload()          # 저장한 값을 전역 설정으로 올리고 프로바이더를 다시 만든다
        return {"proposal": proposal, "applied": applied, "live": bool(live),
                "candidates": {"llm": len(llm_ok), "embed": len(embed_ok), "rerank": len(rerank_ok)},
                "tested": res.get("n"), "ok_n": res.get("ok_n"), "path": _mc.catalog_path(),
                "note": "실제 호출까지 성공한 모델만" if live else "ping 이 통과한 모델만 (--live 로 실제 호출까지 확인 가능)"}

    # =====================================================================
    # BUILD
    # =====================================================================
    def build(self, full: bool = False, progress=None, debug: Optional[int] = None, force: bool = False,
              health: Optional[bool] = None, channels: Optional[List[str]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """force=True: health 실패/락 대기 없이 강행. health=None 이면 toggles.health_check 를 따름.
        channels: ['fts','vector','graph'] 중 이번 빌드에서 처리할 채널만 (없는 채널의 단계는 skipped). 문서 로드·청킹은 항상 수행."""
        with self._lock:
            lock = BuildLock(os.path.join(self.s.data_dir, "build.lock"), timeout=self.s.build_lock_timeout,
                         stale_after_s=self.s.build_lock_stale_s, cmd="build")
            try:
                lock.acquire()
            except BuildLockedError as e:
                _log.log("warning", "build refused: %s" % e, "build")
                raise
            saved = None
            if channels:
                chs = {c.strip().lower() for c in channels if c.strip()}
                bad = chs - set(BUILD_CHANNELS)
                if bad:
                    lock.release()
                    raise ValueError("unknown channel(s) %s (fts|vector|graph)" % sorted(bad))
                t = self.s.toggles
                saved = {k: getattr(t, k) for k in ("build_fts", "embed", "rule_graph", "llm_graph", "doc_vector", "wiki_pages", "communities")}
                t.build_fts = t.build_fts and "fts" in chs
                t.embed = t.embed and "vector" in chs
                t.doc_vector = t.doc_vector and "vector" in chs
                if "graph" not in chs:
                    t.rule_graph = t.llm_graph = t.wiki_pages = t.communities = False
            try:
                res, tr = self._build(full, progress, debug, force, health)
                if channels:
                    res["channels"] = sorted(chs)
                return res, tr
            finally:
                if saved:
                    for k, v in saved.items():
                        setattr(self.s.toggles, k, v)
                lock.release()

    def _build(self, full: bool, progress, debug: Optional[int], force: bool = False,
               health: Optional[bool] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        s, t = self.s, self.s.toggles
        prof = Profiler("build", debug=self.s.debug_level if debug is None else debug, log=t.log_stages)
        _providers.reset_incidents()
        incremental = (not full) and t.incremental
        result: Dict[str, Any] = {"mode": "incremental" if incremental else "full", "alerts": []}
        error: Optional[str] = None

        def report(msg: str) -> None:
            _log.log("info", msg, "build")
            _pg.note(msg)
            if progress:
                progress(msg)

        try:
            # ---- 0. health ----
            do_health = t.health_check if health is None else bool(health)
            if do_health:
                from .health import run_health
                with prof.stage("health", for_build=True) as st:
                    hr = run_health(self, quick=False, for_build=True)
                    st.note(ok=hr["ok"], fails=hr["fails"], warnings=hr["warnings"],
                            failed=[c["name"] for c in hr["checks"] if not c["ok"]])
                    st.debug(checks=hr["checks"])
                    result["health"] = {"ok": hr["ok"], "fails": hr["fails"], "warnings": hr["warnings"],
                                        "failed": [dict((k, c[k]) for k in ("name", "detail", "level") if k in c) for c in hr["checks"] if not c["ok"]]}
                    for c in hr["checks"]:
                        if not c["ok"]:
                            result["alerts"].append({"level": c["level"], "check": c["name"], "detail": c["detail"], "fix": c.get("fix", "")})
                    if not hr["ok"] and not force:
                        raise RuntimeError("health check failed: %s (build --force 로 강행, --no-health-check 로 생략)" % ", ".join(
                            c["name"] for c in hr["checks"] if not c["ok"] and c["level"] == "fail"))
                    report("health: %s (fail=%d warn=%d)" % ("ok" if hr["ok"] else "FAIL", hr["fails"], hr["warnings"]))
            else:
                prof.skipped("health", "disabled" if not t.health_check else "skipped")
            self._ensure_providers(prof, ("extract", "summary") if (t.llm_graph or t.community_summary) else ())
            self._init_text_plugins()
            # ---- 0.5 MCP 소스 ingest (옵션) ----
            extra_dirs: List[str] = []
            if t.mcp_sources:
                from . import mcp_client as _mcp
                with prof.stage("mcp_ingest") as st:
                    try:
                        ing = _mcp.ingest(s)
                        st.note(written=ing["written"], skipped=ing["skipped"], sources=list(ing["sources"].keys()), errors=len(ing["errors"]))
                        st.debug(detail=ing)
                        for e in ing["errors"]:
                            result["alerts"].append({"level": "warn", "check": "mcp_ingest", "detail": json.dumps(e, ensure_ascii=False)[:200]})
                    except Exception as e:
                        st.note(error=str(e)[:200])
                        result["alerts"].append({"level": "warn", "check": "mcp_ingest", "detail": str(e)[:200]})
                extra_dirs = _mcp.ingest_dirs(s)
            else:
                prof.skipped("mcp_ingest")
            # ---- 1. load_corpus ----
            scan_dirs = list(s.corpus_dirs) + [d for d in extra_dirs if d not in s.corpus_dirs]
            with prof.stage("load_corpus", dirs=scan_dirs, stat_skip=bool(incremental and t.stat_skip), tokenizer=self.tokenizer,
                            exclude=list(getattr(s, "corpus_exclude", []) or [])) as st:
                known = self.store.doc_stats() if (incremental and t.stat_skip) else None
                lstats: Dict[str, Any] = {}
                docs = iter_corpus(scan_dirs, known, lstats, exclude=getattr(s, "corpus_exclude", None))
                for n in wiki_notes(s.wiki_dir):   # 위키 편집 노트 overlay
                    text = "# %s (편집 노트)\n\n%s" % (n["name"], n["note"])
                    docs.append(Document(doc_id="wiki/" + n["name"] + ".md", path=n["path"], title=n["name"] + " (편집 노트)",
                                         text=text, kind="wiki", hash=sha1(text), meta={"root": s.wiki_dir, "folder": "wiki"}))
                kinds: Dict[str, int] = {}
                for d in docs:
                    kinds[d.kind] = kinds.get(d.kind, 0) + 1
                st.note(docs=len(docs), kinds=kinds, files_seen=lstats.get("files_seen"), read=lstats.get("read"),
                        skipped_by_stat=lstats.get("skipped_stat"), unsupported=lstats.get("unsupported"),
                        read_ms_by_kind=lstats.get("read_ms_by_kind"))
                st.debug(slowest_files=lstats.get("slowest"), missing_dirs=lstats.get("missing_dirs"), empty=lstats.get("empty"))
                report("loaded %d docs (%d read, %d skipped by stat)" % (len(docs), lstats.get("read", 0), lstats.get("skipped_stat", 0)))

            # ---- 2. diff ----
            with prof.stage("diff") as st:
                old = self.store.doc_hashes()
                new_ids = {d.doc_id for d in docs}
                removed = [d for d in old if d not in new_ids]
                if not incremental:
                    changed = [d for d in docs if not d.skipped]
                else:
                    changed = [d for d in docs if not d.skipped and old.get(d.doc_id) != d.hash]
                # rename/이동 감지: 삭제된 문서와 해시가 같은 신규 문서 (임베딩은 캐시로 재사용, 색인만 다시)
                removed_hash = {old[r]: r for r in removed}
                renamed = [(removed_hash[d.hash], d.doc_id) for d in changed if d.doc_id not in old and d.hash in removed_hash]
                st.note(changed=len(changed), unchanged=len(docs) - len(changed), removed=len(removed),
                        new=sum(1 for d in changed if d.doc_id not in old), renamed=len(renamed))
                st.debug(changed_ids=[d.doc_id for d in changed[:50]], removed_ids=removed[:50], renamed=renamed[:20])
                result.update(docs=len(docs), changed=len(changed), removed=len(removed), renamed=len(renamed))
            has_changes = bool(changed or removed)

            # ---- 3. chunk_index (+ 문서 계약: front matter → doc_meta, lint, 메타 토큰) ----
            doc_meta_changed: Dict[str, Dict[str, Any]] = {}
            with prof.stage("chunk_index", docs=len(changed), trigram=bool(t.fts_trigram), schema_lint=bool(t.schema_lint), fts=bool(t.build_fts)) as st:
                # 전체 리빌드는 모든 문서를 다시 쓰므로 FTS 를 통째로 한 번 비운다.
                # 문서·청크마다 지우면 chunk_id/doc_id 가 UNINDEXED 인 FTS5 를 매번 전체 스캔하게 되어
                # 빌드 시간이 청크 수의 제곱으로 늘어난다 (trigram 을 켜면 특히 심하다).
                fts_cleared = False
                if not incremental:
                    self.store.clear_fts(trigram=bool(t.fts_trigram and t.build_fts))
                    fts_cleared = bool(t.build_fts)
                    st.note(fts_cleared=True, trigram=bool(t.fts_trigram))
                removed_touched: List[str] = []
                for d in removed:
                    removed_touched.extend(self.store.entities_for_chunks(self.store.chunk_ids_of_docs([d])))
                    self.store.delete_doc(d, fts_cleared=fts_cleared)
                n_chunks = 0
                largest = (0, "")
                lint_err = lint_warn = 0
                lint_by_type: Dict[str, int] = {}
                inferred = 0
                for d in changed:
                    self.store.delete_doc(d.doc_id, fts_cleared=fts_cleared)
                    chunks = chunk_document(d, s.chunk_max_chars, s.chunk_overlap_chars, _tuning.T.get("chunk_min_chars"))
                    fm = (d.meta or {}).get("fm") or {}
                    if d.kind == "wiki":
                        nm = {"doc_id": d.doc_id, "doc_type": "wiki_note", "ext_id": "", "title": d.title, "date": time.strftime("%Y-%m-%d"), "ts": time.time(),
                              "date_source": "now", "tags": [], "modules": [], "related": {}, "inferred": False, "schema_version": 0, "extra": {}}
                        lint: List[Dict[str, str]] = []
                    else:
                        nm = _schema.normalize_meta(fm, d.doc_id, d.title, d.text, d.mtime)
                        lint = _schema.lint_document(fm, nm, d.text, bool(fm)) if t.schema_lint else []
                    extra = _schema.meta_tokens(nm)
                    self.store.upsert_doc(d, chunks, tokenize_for_fts, extra_tokens=extra, trigram=bool(t.fts_trigram),
                                          fts=bool(t.build_fts), fts_cleared=True)   # 바로 위 delete_doc 가 이미 지웠다
                    self.store.upsert_doc_meta(nm, lint)
                    doc_meta_changed[d.doc_id] = nm
                    ne = sum(1 for x in lint if x["level"] == "error")
                    nw = sum(1 for x in lint if x["level"] == "warn")
                    lint_err += ne
                    lint_warn += nw
                    if nm.get("inferred"):
                        inferred += 1
                    lint_by_type[nm.get("doc_type") or "(none)"] = lint_by_type.get(nm.get("doc_type") or "(none)", 0) + 1
                    n_chunks += len(chunks)
                    if len(chunks) > largest[0]:
                        largest = (len(chunks), d.doc_id)
                self.store.commit()
                if changed:
                    prev = self.store.kv_get("lint_summary") or {}
                    if incremental and prev:
                        # 증분: 전체 집계는 DB 에서 다시 계산 (정확)
                        agg = self.store.conn.execute("SELECT COUNT(*) n, COALESCE(SUM(lint_errors),0) e, COALESCE(SUM(lint_warnings),0) w, COALESCE(SUM(inferred),0) i FROM doc_meta").fetchone()
                        summary = {"docs": int(agg["n"]), "errors": int(agg["e"]), "warnings": int(agg["w"]), "inferred": int(agg["i"]), "ts": time.time()}
                    else:
                        summary = {"docs": len(changed), "errors": lint_err, "warnings": lint_warn, "inferred": inferred, "ts": time.time()}
                    self.store.kv_set("lint_summary", summary)
                st.note(chunks=n_chunks, avg_chunks_per_doc=round(n_chunks / max(1, len(changed)), 1),
                        largest_doc=largest[1], largest_doc_chunks=largest[0], doc_types=lint_by_type,
                        lint_errors=lint_err, lint_warnings=lint_warn, inferred_meta=inferred)
                result["chunks_indexed"] = n_chunks
                result["lint"] = {"errors": lint_err, "warnings": lint_warn, "inferred": inferred, "doc_types": lint_by_type}
                if lint_err:
                    result["alerts"].append({"level": "warn", "check": "schema_lint", "detail": "%d docs with schema errors (corpus lint 로 확인)" % lint_err})
                if not t.build_fts and changed:
                    result["alerts"].append({"level": "warn", "check": "build_fts", "detail": "build_fts off — %d docs 의 FTS 행을 쓰지 않음 (build fts 로 재색인)" % len(changed), "fix": "build fts"})
                report("indexed %d chunks%s%s" % (n_chunks, " (FTS)" if t.build_fts else " (FTS skipped: build_fts off)", " lint errors=%d" % lint_err if lint_err else ""))

            # ---- 4. embed (재개·캐시·적응형 배치·진행률) ----
            n_missing = len(self.store.missing_embeddings(self.embedder.name)) if t.embed else 0
            if t.embed and (has_changes or not incremental or n_missing):
                from .embed_run import EmbedRunner
                with prof.stage("embed", provider=self.embedder.name, model=getattr(self.embedder, "model", None), adaptive=bool(t.embed_adaptive),
                                dtype=s.embed_store_dtype, resume_missing=n_missing) as st:
                    emb = self.embedder
                    changed_ids = set(self.store.chunk_ids_of_docs([d.doc_id for d in changed]))
                    if isinstance(emb, HashEmbedder):
                        have_idf = emb._idf is not None or bool(self.store.kv_get("hash_idf"))
                        refit = (not incremental) or t.idf_refit_incremental or not have_idf
                        if refit:
                            all_chunks = self.store.all_chunks()
                            emb.fit_idf([c["heading"] + "\n" + c["text"] for c in all_chunks])
                            self.store.kv_set("hash_idf", emb._idf.tolist())
                            todo = all_chunks
                        else:
                            missing = set(self.store.missing_embeddings(emb.name))
                            todo = [c for c in self.store.all_chunks() if c["chunk_id"] in missing or c["chunk_id"] in changed_ids]
                        st.note(idf_refit=refit, idf_reason="full/first build" if (not incremental or not have_idf) else
                                ("toggle idf_refit_incremental" if refit else "reused stored IDF"))
                    else:
                        missing = set(self.store.missing_embeddings(emb.name))
                        todo = [c for c in self.store.all_chunks() if c["chunk_id"] in missing or c["chunk_id"] in changed_ids]
                    runner = EmbedRunner(self.store, emb, s, prof.run_id, report, adaptive=bool(t.embed_adaptive))
                    er = runner.run(todo, dtype=s.embed_store_dtype)
                    st.note(todo=len(todo), **{k: v for k, v in er.items() if k not in ("alerts", "failed_ids")})
                    st.debug(failed_ids=er.get("failed_ids"), alerts=er.get("alerts"))
                    result["embedded"] = er["embedded"]
                    result["embed"] = {k: v for k, v in er.items() if k != "alerts"}
                    for a in er.get("alerts", []):
                        result["alerts"].append({"level": a["level"], "check": "embed", "detail": a["msg"]})
                    report("embedded %d chunks (cache %d, failed %d)" % (er["embedded"], er["cache_hits"], er["failed"]))
            elif t.embed:
                prof.skipped("embed", "no changes")
            else:
                prof.skipped("embed")

            # ---- 5. graph_build ----
            touched: Optional[List[str]] = None
            if (t.rule_graph or t.llm_graph) and (has_changes or not incremental):
                with prof.stage("graph_build") as st:
                    if not incremental:
                        self.store.clear_graph()
                        chunks = self.store.all_chunks()
                        dmeta = {r["doc_id"]: self.store._meta_row(r) for r in self.store.conn.execute("SELECT * FROM doc_meta")}
                    else:
                        chunks = [c for d in changed for c in self.store.all_chunks(d.doc_id)]
                        dmeta = doc_meta_changed
                    titles = {d["doc_id"]: d["title"] for d in self.store.list_docs()}
                    gstats = build_graph_for_chunks(self.store, chunks, titles, prof, self.rules if t.rule_graph else None,
                                                    self.llm_for("extract") if t.llm_graph else None, t.llm_graph,
                                                    self.s.role_llm("extract")["effort"], s.llm_graph_budget, s.llm_graph_min_chars,
                                                    doc_meta=dmeta, explicit=bool(t.explicit_relations), report=report,
                                                    extract_tokens=s.role_max_tokens('extract', 4000), summary_tokens=s.role_max_tokens('summary', 800))
                    touched = None if not incremental else sorted(set(gstats.pop("touched", [])) | set(removed_touched))
                    gstats.pop("touched", None)
                    do_comm = t.communities and (not incremental or t.incremental_communities)
                    fin = finalize_graph(self.store, prof, do_comm, self.llm_for("summary"), t.community_summary,
                                         self.s.role_llm("summary")["effort"], touched,
                                         "" if do_comm or not t.communities else "incremental build (incremental_communities off)",
                                         summary_tokens=s.role_max_tokens("summary", 800))
                    st.note(**gstats, **fin, scope="full" if not incremental else "changed docs (%d chunks)" % len(chunks),
                            provenance=self.store.provenance_counts())
                    result["graph"] = dict(gstats, **fin)
                    report("graph: entities touched=%s explicit=%s id_links=%s" % (gstats.get("touched_entities"), gstats.get("explicit_relations"), gstats.get("id_relations")))
            elif t.rule_graph or t.llm_graph:
                prof.skipped("graph_build", "no changes")
            else:
                prof.skipped("graph_build")

            # ---- 5.5 doc_vectors (문서 카드 임베딩 채널) ----
            if t.doc_vector and t.embed and (has_changes or not incremental):
                from .precompute import build_doc_vectors
                with prof.stage("doc_vectors") as st:
                    dv = build_doc_vectors(self, None if not incremental else [d.doc_id for d in changed])
                    st.note(**dv)
                    result["doc_vectors"] = dv["doc_vectors"]
            elif t.doc_vector:
                prof.skipped("doc_vectors", "no changes" if t.embed else "embed off")
            else:
                prof.skipped("doc_vectors")

            # ---- 6. wiki_pages ----
            if t.wiki_pages and (has_changes or not incremental):
                only = None if (not incremental or t.wiki_full_rewrite) else (touched or [])
                result["wiki"] = write_wiki(self.store, s.wiki_dir, prof, min_degree=_tuning.T.get("wiki_min_degree"), prune=(only is None), only=only)
            elif t.wiki_pages:
                prof.skipped("wiki_pages", "no changes")
            else:
                prof.skipped("wiki_pages")

            # ---- 7. prune / maintenance / verify ----
            with prof.stage("prune") as st:
                n_orph, orphan_names = self.store.prune_orphan_entities(return_names=True)
                pr: Dict[str, Any] = {"orphan_entities": n_orph, "dangling": self.store.prune_dangling()}
                # 증분 빌드에서도 사라진 엔티티의 위키 페이지(편집 노트 없음)는 정리
                if orphan_names and os.path.isdir(s.wiki_dir):
                    from .wiki import slug, _read_note
                    n_rm = 0
                    for nm_ in orphan_names:
                        fp = os.path.join(s.wiki_dir, slug(nm_) + ".md")
                        if os.path.exists(fp) and not _read_note(fp):
                            try:
                                os.remove(fp)
                                n_rm += 1
                            except OSError:
                                pass
                    pr["stale_wiki_pages"] = n_rm
                if not incremental and t.embed:
                    pr["old_provider_embeddings"] = self.store.prune_embeddings(self.embedder.name)
                self.store.commit()
                if not incremental and t.fts_optimize:
                    t0 = time.perf_counter()
                    self.store.fts_optimize()
                    pr["fts_optimize_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                self.store.wal_checkpoint()
                st.note(**pr)
                result["pruned"] = pr
            if t.verify_after_build:
                with prof.stage("verify") as st:
                    vr = self.store.verify(self.embedder.name if t.embed else None, fix=False, wiki_dir=s.wiki_dir)
                    problems = [c for c in vr["checks"] if not c["ok"]]
                    st.note(ok=vr["ok"], problems=[(c["name"], c["count"]) for c in problems], counts=vr["counts"])
                    result["verify"] = {"ok": vr["ok"], "problems": [(c["name"], c["count"]) for c in problems]}
                    for c in problems:
                        if c["name"] in ("embedding_coverage", "community_unassigned"):
                            continue   # 예상 가능한 상태 (실패 청크 재개 / 증분 커뮤니티) 는 알림만 생략
                        result["alerts"].append({"level": "warn", "check": "verify:" + c["name"], "detail": "%s (%d)" % (c["detail"], c["count"]),
                                                 "fix": "build verify --fix" if c.get("fixable") else ""})
            else:
                prof.skipped("verify")

            self.store.kv_set("last_build", {"ts": time.time(), "mode": result["mode"], "docs": len(docs), "changed": len(changed),
                                             "removed": len(removed), "alerts": result.get("alerts", [])[:20], "lint": result.get("lint"),
                                             "embed": {k: v for k, v in (result.get("embed") or {}).items() if k in ("embedded", "failed", "cache_hits", "status")}})
            result["build_version"] = self.store.bump_build_version()
            self._seen_version = result["build_version"]
            with self._qlock:
                self._qcache.clear()

            # ---- 8. warm_cache ----
            if t.warm_cache:
                with prof.stage("warm_cache") as st:
                    info: Dict[str, Any] = {}
                    if t.embed:
                        ids, mat = self.store.vector_matrix(self.embedder.name)
                        info["vectors"] = len(ids)
                        info["matrix_mb"] = round(mat.nbytes / 1e6, 1)
                        info["load_ms"] = round(getattr(self.store, "last_vec_load_ms", 0.0), 1)
                    info["entities_indexed"] = len(self.store.entity_index())
                    st.note(**info)
            else:
                prof.skipped("warm_cache")
            # ---- 9. precompute (옵션) ----
            if t.precompute_after_build and has_changes:
                from . import precompute as _pc
                with prof.stage("precompute") as st:
                    try:
                        pcr = _pc.run(self, progress=report)
                        st.note(**{k: v for k, v in pcr.items() if k != "cache"})
                        result["precompute"] = pcr
                    except Exception as e:
                        st.note(error=str(e)[:200])
            else:
                prof.skipped("precompute")
            result["stats"] = self.store.stats()
        except _pg.Cancelled as e:
            error = "cancelled: %s" % e
            result["error"] = error
            result["cancelled"] = True
            try:
                self.store.commit()   # 지금까지의 진행(체크포인트·임베딩 배치)은 보존 → 다음 build 가 이어서 한다
            except Exception:
                pass
            _log.log("warning", "build cancelled: %s" % e, "build")
            raise
        except Exception as e:
            error = "%s: %s" % (type(e).__name__, e)
            result["error"] = error
            _log.log("error", "build failed: %s" % error, "build")
            raise
        finally:
            from .query_engine import llm_report_from_incidents
            rep = llm_report_from_incidents(_providers.drain_incidents())
            if rep:
                result["llm_report"] = rep
                if t.llm_failure_report:
                    result.setdefault("alerts", []).append({"level": "warn", "check": "llm_failures", "detail": "; ".join(rep["summary"])[:400],
                                                            "fix": "agents.json timeout_s/retries · config llm_timeout/llm_retries · models test --live"})
            trace = prof.finish()
            result["ms"] = trace["ms"]
            result["tokens"] = trace["summary"]["llm"]
            result["run_id"] = trace.get("run_id")
            try:
                result["request_id"] = self.store.log_request(
                    "build", "%s build: %s docs, %s changed, %s removed" % (result["mode"], result.get("docs", "?"),
                                                                        result.get("changed", "?"), result.get("removed", "?")),
                    trace, {k: v for k, v in result.items() if k != "stats"}, {"toggles": t.__dict__}, error, keep=s.keep_requests)
            except Exception:
                pass
        return result, trace

    # =====================================================================
    # CHANNEL BUILD (fts | vector | graph — 하나만 다시 만든다)
    # =====================================================================
    def build_channel(self, channel: str, full: bool = False, progress=None, debug: Optional[int] = None, force: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """chunks 는 그대로 두고 한 채널의 산출물만 다시 만든다.
          fts    : chunks_fts(+chunks_tri) 를 전부 다시 씀 (토크나이저·복합어·메타 토큰 변경 후). 임베딩·그래프 불변.
          vector : 임베딩 없는 청크만(기본) 또는 전부(full; hash 는 IDF 재적합) 임베딩. FTS·그래프 불변. doc_vector 토글이 켜져 있으면 문서 카드도 재생성.
          graph  : 그래프 테이블을 비우고 전체 청크에서 재추출 + 커뮤니티 + doc_refs + 위키 페이지. FTS·임베딩 불변.
        끝나면 verify(요약)·build_version 증가(캐시 무효화)·warm_cache. requests 테이블에 kind=build 로 기록."""
        channel = str(channel or "").strip().lower()
        if channel not in BUILD_CHANNELS:
            raise ValueError("unknown channel %r (fts|vector|graph)" % channel)
        with self._lock:
            lock = BuildLock(os.path.join(self.s.data_dir, "build.lock"), timeout=self.s.build_lock_timeout,
                         stale_after_s=self.s.build_lock_stale_s, cmd="build %s" % channel)
            lock.acquire()
            try:
                return self._build_channel(channel, full, progress, debug, force)
            finally:
                lock.release()

    def _build_channel(self, channel: str, full: bool, progress, debug: Optional[int], force: bool) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        s, t = self.s, self.s.toggles
        prof = Profiler("build", debug=self.s.debug_level if debug is None else debug, log=t.log_stages)
        _providers.reset_incidents()
        result: Dict[str, Any] = {"mode": "channel:%s" % channel, "channel": channel, "full": bool(full), "alerts": []}
        error: Optional[str] = None

        def report(msg: str) -> None:
            _log.log("info", msg, "build")
            _pg.note(msg)
            if progress:
                progress(msg)

        try:
            n_chunks = int(self.store.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            if n_chunks == 0:
                raise RuntimeError("chunks 가 없습니다 — 먼저 build (전체 빌드) 로 문서를 청킹하세요")
            before = {"fts": self.store.conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0],
                      "embeddings": self.store.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0],
                      "entities": self.store.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
                      "relations": self.store.conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]}
            with prof.stage("build_channel", channel=channel, full=bool(full), chunks=n_chunks) as st0:
                self._init_text_plugins()
                if channel == "fts":
                    with prof.stage("reindex_fts", trigram=bool(t.fts_trigram), tokenizer=self.tokenizer) as st:
                        r = self.store.reindex_fts(tokenize_for_fts, _schema.meta_tokens, trigram=bool(t.fts_trigram),
                                                   progress=lambda i, n, d: _pg.tick(i, n, d))
                        st.note(**r)
                        result["reindexed"] = r
                        report("fts reindexed: %d chunks in %d docs" % (r["chunks"], r["docs"]))
                    if t.fts_optimize:
                        t0 = time.perf_counter()
                        self.store.fts_optimize()
                        result["fts_optimize_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                elif channel == "vector":
                    if not t.embed and not force:
                        raise RuntimeError("toggles.embed 가 꺼져 있습니다 (--embed 또는 --force)")
                    from .embed_run import EmbedRunner
                    self._ensure_providers(prof, ())
                    emb = self.embedder
                    with prof.stage("embed", provider=emb.name, model=getattr(emb, "model", None), adaptive=bool(t.embed_adaptive), dtype=s.embed_store_dtype, full=bool(full)) as st:
                        all_chunks = self.store.all_chunks()
                        if isinstance(emb, HashEmbedder):
                            have_idf = emb._idf is not None or bool(self.store.kv_get("hash_idf"))
                            if full or not have_idf:
                                emb.fit_idf([c["heading"] + "\n" + c["text"] for c in all_chunks])
                                self.store.kv_set("hash_idf", emb._idf.tolist())
                                todo = all_chunks
                                st.note(idf_refit=True)
                            else:
                                missing = set(self.store.missing_embeddings(emb.name))
                                todo = [c for c in all_chunks if c["chunk_id"] in missing]
                        else:
                            if full:
                                todo = all_chunks
                            else:
                                missing = set(self.store.missing_embeddings(emb.name))
                                todo = [c for c in all_chunks if c["chunk_id"] in missing]
                        if full and emb.name != "hash":
                            # 캐시를 지우진 않지만 embeddings 행은 새로 만든다 (같은 내용은 캐시 적중 → 비용 0)
                            pass
                        runner = EmbedRunner(self.store, emb, s, prof.run_id, report, adaptive=bool(t.embed_adaptive))
                        er = runner.run(todo, dtype=s.embed_store_dtype)
                        st.note(todo=len(todo), **{k: v for k, v in er.items() if k not in ("alerts", "failed_ids")})
                        result["embedded"] = er["embedded"]
                        result["embed"] = {k: v for k, v in er.items() if k != "alerts"}
                        for a in er.get("alerts", []):
                            result["alerts"].append({"level": a["level"], "check": "embed", "detail": a["msg"]})
                        pruned = self.store.prune_embeddings(emb.name)
                        result["pruned_other_provider"] = pruned
                        report("embedded %d chunks (todo %d, cache %d, failed %d, pruned other provider %d)" % (er["embedded"], len(todo), er["cache_hits"], er["failed"], pruned))
                    if t.doc_vector:
                        from .precompute import build_doc_vectors
                        with prof.stage("doc_vectors") as st:
                            dv = build_doc_vectors(self, None)
                            st.note(**dv)
                            result["doc_vectors"] = dv.get("doc_vectors")
                elif channel == "graph":
                    if not (t.rule_graph or t.llm_graph) and not force:
                        raise RuntimeError("toggles.rule_graph / llm_graph 가 모두 꺼져 있습니다 (--rule-graph 또는 --force)")
                    self._ensure_providers(prof, ("extract", "summary") if (t.llm_graph or t.community_summary) else ())
                    with prof.stage("graph_build", full=True) as st:
                        self.store.clear_graph()
                        chunks = self.store.all_chunks()
                        dmeta = {r["doc_id"]: self.store._meta_row(r) for r in self.store.conn.execute("SELECT * FROM doc_meta")}
                        titles = {d["doc_id"]: d["title"] for d in self.store.list_docs()}
                        gstats = build_graph_for_chunks(self.store, chunks, titles, prof, self.rules if (t.rule_graph or force) else None,
                                                        self.llm_for("extract") if t.llm_graph else None, t.llm_graph,
                                                        self.s.role_llm("extract")["effort"], s.llm_graph_budget, s.llm_graph_min_chars,
                                                        doc_meta=dmeta, explicit=bool(t.explicit_relations), report=report,
                                                    extract_tokens=s.role_max_tokens('extract', 4000), summary_tokens=s.role_max_tokens('summary', 800))
                        gstats.pop("touched", None)
                        fin = finalize_graph(self.store, prof, bool(t.communities), self.llm_for("summary"), t.community_summary,
                                             self.s.role_llm("summary")["effort"], None, "" if t.communities else "disabled", summary_tokens=s.role_max_tokens("summary", 800))
                        st.note(**gstats, **fin, provenance=self.store.provenance_counts())
                        result["graph"] = dict(gstats, **fin)
                        report("graph rebuilt: entities touched=%s explicit=%s id_links=%s" % (gstats.get("touched_entities"), gstats.get("explicit_relations"), gstats.get("id_relations")))
                    if t.wiki_pages:
                        result["wiki"] = write_wiki(self.store, s.wiki_dir, prof, min_degree=_tuning.T.get("wiki_min_degree"), prune=True, only=None)
                    n_orph, _names = self.store.prune_orphan_entities(return_names=True)
                    result["pruned"] = {"orphan_entities": n_orph, "dangling": self.store.prune_dangling()}
                self.store.commit()
                self.store.wal_checkpoint()
                after = {"fts": self.store.conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0],
                         "embeddings": self.store.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0],
                         "entities": self.store.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
                         "relations": self.store.conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]}
                result["counts_before"], result["counts_after"] = before, after
                # 독립성 확인: 다른 채널의 행 수는 그대로여야 한다
                untouched = {"fts": ("embeddings", "entities", "relations"), "vector": ("fts", "entities", "relations"), "graph": ("fts", "embeddings")}[channel]
                changed_other = [k for k in untouched if before[k] != after[k]]
                if changed_other:
                    result["alerts"].append({"level": "warn", "check": "channel_isolation", "detail": "다른 채널의 행 수가 바뀜: %s" % changed_other})
                st0.note(before=before, after=after, other_channels_unchanged=not changed_other)
            if t.verify_after_build:
                with prof.stage("verify") as st:
                    vr = self.store.verify(self.embedder.name if t.embed else None, fix=False, wiki_dir=s.wiki_dir)
                    problems = [c for c in vr["checks"] if not c["ok"]]
                    st.note(ok=vr["ok"], problems=[(c["name"], c["count"]) for c in problems])
                    result["verify"] = {"ok": vr["ok"], "problems": [(c["name"], c["count"]) for c in problems]}
                    for c in problems:
                        if c["name"] in ("embedding_coverage", "community_unassigned") and channel != "vector":
                            continue
                        result["alerts"].append({"level": "warn", "check": "verify:" + c["name"], "detail": "%s (%d)" % (c["detail"], c["count"]),
                                                 "fix": "build verify --fix" if c.get("fixable") else ""})
            lb = self.store.kv_get("last_build") or {}
            lb = dict(lb, channel_build={"channel": channel, "ts": time.time(), "full": bool(full), "alerts": result["alerts"][:10]})
            self.store.kv_set("last_build", lb)
            result["build_version"] = self.store.bump_build_version()
            self._seen_version = result["build_version"]
            with self._qlock:
                self._qcache.clear()
            self.store.invalidate_caches()
            if t.warm_cache:
                with prof.stage("warm_cache") as st:
                    info: Dict[str, Any] = {}
                    if t.embed:
                        ids, mat = self.store.vector_matrix(self.embedder.name)
                        info["vectors"] = len(ids)
                    info["entities_indexed"] = len(self.store.entity_index())
                    st.note(**info)
            result["stats"] = self.store.stats()
        except _pg.Cancelled as e:
            error = "cancelled: %s" % e
            result["error"] = error
            result["cancelled"] = True
            try:
                self.store.commit()
            except Exception:
                pass
            _log.log("warning", "channel build cancelled: %s" % e, "build")
            raise
        except Exception as e:
            error = "%s: %s" % (type(e).__name__, e)
            result["error"] = error
            _log.log("error", "channel build failed: %s" % error, "build")
            raise
        finally:
            from .query_engine import llm_report_from_incidents
            rep = llm_report_from_incidents(_providers.drain_incidents())
            if rep:
                result["llm_report"] = rep
                if t.llm_failure_report:
                    result.setdefault("alerts", []).append({"level": "warn", "check": "llm_failures", "detail": "; ".join(rep["summary"])[:400]})
            trace = prof.finish()
            result["ms"] = trace["ms"]
            result["tokens"] = trace["summary"]["llm"]
            result["run_id"] = trace.get("run_id")
            try:
                result["request_id"] = self.store.log_request("build", "channel build: %s%s" % (channel, " (full)" if full else ""), trace,
                                                              {k: v for k, v in result.items() if k != "stats"}, {"toggles": t.__dict__}, error, keep=s.keep_requests)
            except Exception:
                pass
        return result, trace

    # =====================================================================
    # AUTO BUILD (watcher)
    # =====================================================================
    def check_changes(self) -> Dict[str, Any]:
        """파일을 읽지 않고 stat 만으로 변경 여부 판단 (수천 파일도 수십 ms)."""
        t0 = time.perf_counter()
        # 제외 목록을 **빌드와 똑같이** 넘긴다 — 다르면 워처가 헛빌드를 반복한다
        r = scan_changed(self.s.corpus_dirs, self.store.doc_stats(), exclude=getattr(self.s, "corpus_exclude", None))
        r["scan_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        r["ts"] = time.time()
        self.watcher["last_scan"] = r["ts"]
        self.watcher["last_result"] = {k: v for k, v in r.items() if k not in ("changed", "removed")}
        return r

    def auto_build_tick(self, progress=None) -> Dict[str, Any]:
        """변경이 있을 때만 증분 빌드. 서버 워처 스레드/`watch` CLI 가 주기적으로 호출."""
        r = self.check_changes()
        if r["n_changed"] or r["n_removed"]:
            try:
                res, _ = self.build(full=False, progress=progress)
                self.watcher["builds"] += 1
                self.watcher["last_build"] = {"ts": time.time(), "changed": res.get("changed"), "removed": res.get("removed"),
                                              "ms": res.get("ms"), "request_id": res.get("request_id")}
                r["built"] = True
                r["build"] = self.watcher["last_build"]
            except Exception as e:
                self.watcher["errors"] += 1
                r["built"] = False
                r["error"] = str(e)
        else:
            r["built"] = False
        return r

    # =====================================================================
    # QUERY
    # =====================================================================
    def visibility_key(self) -> str:
        """**무엇을 볼 수 있는가**를 나타내는 캐시 구획 이름. 답변 캐시·사전계산 캐시의 키에 들어간다.

        왜 필요한가: 캐시가 맞으면 `doc_acl` 단계는 실행조차 되지 않는다(조기 반환). 키에 신분이 없으면
        admin 이 한 번 물은 답이 그대로 viewer 에게 돌아간다 — 접근 제어가 캐시 하나로 무너진다.

        **사용자 이름이 아니라 '가시성 등급' 으로 나눈다.** 사용자별로 나누면 30명 환경에서 적중률이 1/30 이
        되어 캐시를 켜는 의미가 사라진다. 같은 역할은 같은 문서를 보므로 역할 하나가 곧 한 구획이다.
        규칙이 없으면(=아무도 안 막힘) 모두 같은 구획을 쓴다 — 예전과 같은 적중률.
        """
        try:
            from . import docacl as _acl
            if not bool(getattr(self.s.toggles, "doc_acl", True)):
                return "all"
            acl = _acl.load()
            if not (bool(acl.get("enabled", True)) and (acl.get("rules") or _acl._rank(acl.get("default_min_role")) > 0)):
                return "all"          # 규칙 없음 = 누구나 같은 것을 본다
            role = (self.actor or {}).get("role") or "viewer"
            return "admin" if role == "admin" else "role:%s" % role
        except Exception:
            return "unknown"          # 판정 실패 시 구획을 분리해 둔다 (섞이는 것보다 낫다)

    def _cache_key(self, q: str) -> str:
        t = self.s.toggles
        sig = {"q": q.strip(), "vis": self.visibility_key(),
               "toggles": {k: v for k, v in t.__dict__.items() if not k.startswith("evolve")},
               "k": [self.s.top_k_fts, self.s.top_k_vector, self.s.top_k_graph, self.s.top_k_final, self.s.graph_hops, self.s.rrf_k],
               "ctx": [self.s.context_max_chars, self.s.context_chunk_chars, self.s.rerank_candidates, self.s.answer_max_tokens],
               "llm": [self.llm_for("answer").describe().get("model"), self.llm_for("rerank").describe().get("model")],
               "emb": self.embedder.name, "v": self.store.build_version(), "syn": len(self.store.synonyms()),
               "tuning": _tuning.T.to_dict(), "day": time.strftime("%Y-%m-%d") if t.time_scope else ""}
        return hashlib.sha1(json.dumps(sig, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _content_llm_sig(self, role: str) -> Dict[str, Any]:
        """캐시 키에 넣을 **답 내용을 바꾸는** LLM 설정만 (2026-09-20).

        예전에는 `role_llm(role)["model"]` 하나만 넣었다. 그런데 앙상블을 켜면 그 역할의 모델은
        **불리지도 않고**(make_llm 이 EnsembleLLM 을 돌려준다) 값도 그대로라, 앙상블을 켜거나 멤버를
        바꿔도 키가 변하지 않았다 — precompute 캐시가 **앙상블 이전에 만든 답**을 계속 돌려준다.
        "설정은 바꿨는데 아무 일도 안 일어난다" 가 되는 자리다.

        타임아웃·재시도·회로 차단은 답 **내용**을 바꾸지 않으므로 넣지 않는다 (넣으면 무의미한 캐시 미스가 된다).
        앙상블의 wait/timeout_s/min_results 는 어떤 멤버가 취합에 들어가는지를 바꾸므로 넣는다.
        """
        rc = self.s.role_llm(role)
        return {"provider": rc.get("provider"), "model": rc.get("model"), "effort": rc.get("effort"),
                "max_tokens": rc.get("max_tokens"), "ensemble": rc.get("ensemble")}

    def answer_signature(self) -> Dict[str, Any]:
        """답변 결과에 영향을 주는 설정 요약 (precompute 캐시 키)."""
        s, t = self.s, self.s.toggles
        return {"vis": self.visibility_key(),     # 접근 제어 구획 — 캐시가 맞으면 doc_acl 은 실행되지 않는다
                "toggles": {k: v for k, v in t.__dict__.items() if not k.startswith(("evolve", "log_", "forensic", "health", "profile"))},
                "k": [s.top_k_fts, s.top_k_vector, s.top_k_graph, s.top_k_final, s.graph_hops, s.rrf_k],
                "ctx": [s.context_max_chars, s.context_chunk_chars, s.rerank_candidates, s.answer_max_tokens],
                "llm": [self._content_llm_sig("answer"), self._content_llm_sig("rerank")], "emb": self.s.embed_provider,
                "tuning": _tuning.T.to_dict()}

    def query(self, q: str, log: bool = True, overrides: Optional[Dict[str, Any]] = None,
              debug: Optional[int] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """질의 (v3 엔진).

        `overrides` 를 주면 이 호출 동안만 적용한다(안쪽 request_scope 로 감싸므로 바깥 범위는 그대로).
        2026-09-19 이전에는 이 인자를 **받아 놓고 무시**해서, 넘긴 쪽은 설정이 먹은 줄 알지만 아무 일도
        일어나지 않았다 — 값을 조용히 버리는 인자는 두지 않는다. Web 서버처럼 여러 요청을 다룰 때는
        여전히 `request_scope(overrides=…)` 로 감싸는 편이 낫다(프리셋·신분과 한 범위에서 처리된다)."""
        from .query_engine import QueryEngine
        if not overrides:
            return QueryEngine(self).run(q, log=log, debug=debug)
        with self.request_scope(overrides=overrides):
            return QueryEngine(self).run(q, log=log, debug=debug)

    def rerun(self, request_id: Any, point: str, log: bool = True, debug: Optional[int] = None,
              query: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """저장해 둔 중간 결과로 **특정 단계부터** 다시 돌린다 (docs/RERUN.md).

        설정 변경은 호출자가 `request_scope(overrides=…, presets=…)` 로 감싸서 준다 —
        Web 의 일반 질의와 같은 길을 쓰므로 "재실행에서만 되는 설정" 같은 것이 생기지 않는다.
        """
        from . import rerun as _rerun
        from .query_engine import QueryEngine
        if point not in _rerun.POINT_IDS:
            raise ValueError("재시작점은 %s 중 하나여야 합니다" % ", ".join(_rerun.POINT_IDS))
        data = _rerun.load(self.s, request_id)
        ok, why = _rerun.check_compatible(data or {}, str(self.store.build_version()))
        if point != "plan" and not ok:
            raise ValueError(why)
        q = query or str((data or {}).get("query") or "")
        if not q:
            raise ValueError("원 질의를 찾을 수 없습니다 (중간 결과가 없고 query 도 주지 않았습니다)")
        eng = QueryEngine(self)
        if point != "plan" and data:
            eng.resume = _rerun.Resume(point, data)
        res, tr = eng.run(q, log=log, debug=debug)
        res["rerun_of"] = request_id
        res["rerun_from"] = point
        return res, tr

    def _query_legacy(self, q: str, log: bool = True, overrides: Optional[Dict[str, Any]] = None,
                      debug: Optional[int] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        s = self.s
        t = s.toggles
        prof = Profiler("query", debug=s.debug_level if debug is None else debug, log=t.log_stages)
        _log.log("info", "query: %s" % q[:200], "query")
        with prof.stage("sync_index") as st:
            st.note(build_version=self.store.build_version(), reloaded_caches=self.sync_with_db())
        self._ensure_providers(prof, ("answer", "rerank"))

        # ---- query cache ----
        key = self._cache_key(q) if t.query_cache else None
        cached = self.qcache_get(key) if key else None
        if cached:
            with prof.stage("cache_hit", key=key[:12]) as st:
                st.note(saved_ms=cached["result"].get("ms"), saved_tokens=cached["trace"].get("summary", {}).get("llm", {}).get("total_tokens", 0))
            trace = prof.finish()
            res = dict(cached["result"], cached=True, ms=trace["ms"], query_id=None, proposals=[])
            res["request_id"] = self.store.log_request("query", "[cache] " + q, trace, {"cached_from": cached["result"].get("request_id")},
                                                       res.get("config"), None, keep=s.keep_requests)
            return res, trace

        lists: Dict[str, List[Tuple[str, float]]] = {}
        weights = {"fts": 1.0, "vector": 1.0, "graph": 1.0}
        route_info: Dict[str, Any] = {}
        graph_res: Dict[str, Any] = {}
        fts_snips: Dict[str, str] = {}

        if t.router:
            with prof.stage("router") as st:
                route_info = route(q, self.store)
                weights = route_info["weights"]
                st.note(**route_info)
        else:
            prof.skipped("router")

        # ---- LLM 질의 확장 (옵션): 대체 질의로 FTS/벡터를 추가 실행해 별도 리스트로 융합 (원 질의는 항상 유지) ----
        T = self.tuning
        alt_queries: List[str] = []
        if t.query_expand or t.query_decompose:
            rl = self.llm_for("expand")
            if rl.available:
                with prof.stage("query_expand", model=rl.model, n=T.get("query_expand_n"), decompose=t.query_decompose) as st:
                    try:
                        r = rl.complete(_prompts.get("expand"), "N=%d\n%s" % (T.get("query_expand_n"), q), max_tokens=400,
                                        effort=s.role_llm("expand")["effort"], json_mode=True)
                        data = parse_json(r["text"]) or {}
                        if t.query_expand:
                            alt_queries = [x for x in (data.get("queries") or []) if isinstance(x, str) and x.strip() and x.strip() != q][:T.get("query_expand_n")]
                        subs: List[str] = []
                        if t.query_decompose:
                            subs = [x for x in (data.get("sub_queries") or []) if isinstance(x, str) and x.strip() and x.strip() != q][:T.get("query_decompose_max")]
                            alt_queries += [x for x in subs if x not in alt_queries]
                        st.note(alt_queries=alt_queries, sub_queries=subs, keywords=(data.get("keywords") or [])[:10], usage=r.get("usage"))
                        st.sample(response=r["text"][:1000])
                    except (LLMError, ValueError) as e:
                        st.note(error=str(e)[:200])
            else:
                prof.skipped("query_expand", "LLM provider unavailable")
        else:
            prof.skipped("query_expand")

        if t.fts:
            rows = fts_search(self.store, q, s.top_k_fts, self.store.synonyms(), prof)
            lists["fts"] = [(cid, sc) for cid, sc, _ in rows]
            fts_snips = {cid: sn for cid, _, sn in rows}
            for i, aq in enumerate(alt_queries):
                arows = fts_search(self.store, aq, s.top_k_fts, self.store.synonyms(), prof)
                lists["fts_alt%d" % (i + 1)] = [(cid, sc) for cid, sc, _ in arows]
                weights["fts_alt%d" % (i + 1)] = weights.get("fts", 1.0) * T.get("query_expand_w")
        else:
            prof.skipped("fts_search")
        if t.vector:
            lists["vector"] = vector_search(self.store, self.embedder, q, s.top_k_vector, prof,
                                            bool(getattr(s.toggles, "embed_query_cache", True)))
            for i, aq in enumerate(alt_queries):
                lists["vector_alt%d" % (i + 1)] = vector_search(self.store, self.embedder, aq, s.top_k_vector, prof,
                                                                bool(getattr(s.toggles, "embed_query_cache", True)))
                weights["vector_alt%d" % (i + 1)] = weights.get("vector", 1.0) * T.get("query_expand_w")
        else:
            prof.skipped("vector_search")
        if t.graph:
            seeds = None
            if route_info.get("entities"):
                seeds = [(e, sc) for e, sc in route_info["entities"]]
            graph_res = graph_search(self.store, q, s.top_k_graph, s.graph_hops, prof, seeds)
            lists["graph"] = graph_res["chunks"]
        else:
            prof.skipped("graph_search")

        hits = rrf_fuse(lists, weights, s.rrf_k, prof)
        chunks = self.store.get_chunks([h.chunk_id for h in hits])
        if t.rerank:
            rl = self.llm_for("rerank")
            hits = rerank(hits, chunks, q, rl, s.top_k_final, prof, s.role_llm("rerank")["effort"],
                          use_llm=t.rerank_llm, n_cands=s.rerank_candidates, chunk_chars=s.rerank_chunk_chars, settings=s)
        else:
            prof.skipped("rerank")
        final = hits[: s.top_k_final]

        from . import models_catalog as _mc
        budget = _mc.context_budget(s, "answer")     # 설정값과 모델 창 중 작은 쪽 (query_engine 과 같은 규칙)
        with prof.stage("context", max_chars=budget["chars"], configured=budget["configured"],
                        model_window=budget["window_tokens"], budget_limited=budget["limited"],
                        trim=t.context_trim, dedupe=t.dedupe_hits) as st:
            if budget["limited"]:
                st.note(budget=budget["reason"])
            ctx = build_context(final, chunks, graph_res if t.graph else None, budget["chars"], query=q,
                                trim=t.context_trim, dedupe=t.dedupe_hits, chunk_chars=s.context_chunk_chars, stage=st, store=self.store,
                                guard=bool(getattr(t, "context_guard", True)))
            st.note(chars=ctx["chars"], citations=len(ctx["citations"]))
        al = self.llm_for("answer")
        ans = generate_answer(q, ctx, al, t.llm_answer, prof, s.role_llm("answer")["effort"], chunks, final,
                              max_tokens=s.answer_max_tokens, meta=self.store.doc_meta_map(), graph=graph_res if t.graph else None)

        cite_n = {c["chunk_id"]: c["n"] for c in ctx["citations"]}
        hit_dicts = []
        for h in final:
            c = chunks.get(h.chunk_id)
            in_ctx = h.chunk_id in cite_n
            hit_dicts.append(dict(h.to_dict(), n=cite_n.get(h.chunk_id), in_context=in_ctx, doc_id=c["doc_id"] if c else "",
                                  heading=c["heading"] if c else "", text=c["text"] if c else "", snippet=fts_snips.get(h.chunk_id, "")))
        for cit in ctx["citations"]:   # 인접 청크(context_neighbors) 도 근거 목록에 포함
            if cit.get("kind") == "neighbor" and cit["chunk_id"] in chunks:
                c = chunks[cit["chunk_id"]]
                hit_dicts.append({"chunk_id": cit["chunk_id"], "scores": {}, "ranks": {}, "fused": 0.0, "why": ["neighbor"], "rerank": None,
                                  "n": cit["n"], "in_context": True, "doc_id": c["doc_id"], "heading": c["heading"], "text": c["text"], "snippet": ""})
        hit_dicts.sort(key=lambda d: (d["n"] is None, d["n"] or 0))
        result = {"query": q, "answer": ans["answer"], "answer_mode": ans["mode"], "cited": ans["cited"], "model": ans.get("model"),
                  "hits": hit_dicts, "route": route_info, "graph": {k: v for k, v in graph_res.items() if k != "chunks"},
                  "config": {"toggles": t.__dict__, "weights": weights, "llm": al.name, "llm_model": al.model,
                             "rerank_llm": self.llm_for("rerank").describe().get("model"), "embedder": self.embedder.name,
                             "tuning": self.tuning.to_dict(), "alt_queries": alt_queries},
                  "cached": False}
        if ans.get("repeat_loop"):
            result["repeat_loop"] = ans["repeat_loop"]        # 답변 상단 배너 + 캐시 제외용
        if log and t.evolve_capture:
            from .evolve import capture_query
            with prof.stage("evolve_capture") as st:
                result["proposals"] = capture_query(self, q, result, final)
                st.note(proposals=result["proposals"])
        else:
            prof.skipped("evolve_capture", "log off" if not log else "disabled")
        trace = prof.finish()
        result["ms"] = trace["ms"]
        result["tokens"] = trace["summary"]["llm"]
        result["run_id"] = trace.get("run_id")
        if log and t.evolve_capture:
            result["query_id"] = self.store.log_query(q, result["config"], [h.chunk_id for h in final], ans["answer"],
                                                      {"top_fused": final[0].fused if final else 0, "n_hits": len(final)}, trace)
        result["request_id"] = self.store.log_request("query", q, trace, {k: v for k, v in result.items() if k not in ("hits",)},
                                                      result["config"], None, keep=s.keep_requests, archive_dir=s.requests_archive_dir())
        if result.get("query_id"):
            self.store.set_query_request_id(int(result["query_id"]), int(result["request_id"] or 0))
        # 반복 루프로 망가진 답변은 캐시하지 않는다 — 한 번 캐시되면 그 질문은 계속 같은 고장 답변을 즉시 돌려준다
        if key and not result.get("repeat_loop"):
            self.qcache_put(key, {"result": dict(result), "trace": trace})
        return result, trace

    def cache_info(self) -> Dict[str, Any]:
        return {"query_cache": {"size": len(self._qcache), "max": self.s.query_cache_size, **self._qcache_stats},
                "vector_matrix": self.store.vector_cache_info(),
                "entity_index": {"loaded": self.store._ent_cache is not None,
                                 "n": len(self.store._ent_cache[1]) if self.store._ent_cache else 0},
                "llm_instances": len(self._llms), "embedder_instances": len(self._embedders), "db_pool": self.store.pool_info()}

    def clear_caches(self) -> None:
        with self._qlock:
            self._qcache.clear()
            self._qcache_stats = {"hits": 0, "misses": 0}
        self.store.invalidate_caches()

    # =====================================================================
    # EVAL
    # =====================================================================
    #: 답변에 LLM 을 쓰는 토글 — `retrieval_only` 가 한 번에 끈다.
    #  검색 지표(hit@k·MRR·term_recall)는 LLM 없이도 전부 계산된다. 그런데 예전에는 평가가 항상 전체
    #  파이프라인을 돌려서 25문항에 5분·수천 토큰이 들었고, 그래서 **아무도 자주 돌리지 않았다** —
    #  품질 루프가 거기서 끊긴다. 검색을 튜닝할 때는 이 스위치 하나로 토큰 0에 끝낸다.
    EVAL_LLM_TOGGLES = ("llm_answer", "claim_check", "claim_check_llm", "evidence_check_llm", "answer_refine",
                        "query_expand", "query_decompose", "router_llm", "rerank_llm",
                        "llm_after_fusion", "llm_after_rerank", "evidence_compress", "external_rag")

    def evaluate(self, k: int = 5, questions: Optional[List[Dict[str, Any]]] = None, log: bool = False,
                 retrieval_only: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """회귀 평가. `retrieval_only` 면 LLM 을 쓰는 단계를 전부 끄고 **검색 지표만** 낸다(빠르고 토큰 0).
        답변 관련 지표(answer_term_recall)는 그 모드에서 뜻이 없으므로 None 으로 둔다 — 0 으로 두면
        '나빠졌다' 로 잘못 읽힌다."""
        prof = Profiler("eval", debug=self.s.debug_level, log=self.s.toggles.log_stages)
        qs = questions or load_questions()
        rows = []
        prev_tog = {t: getattr(self.s.toggles, t, None) for t in self.EVAL_LLM_TOGGLES} if retrieval_only else {}
        if retrieval_only:
            for t in self.EVAL_LLM_TOGGLES:
                if hasattr(self.s.toggles, t):
                    setattr(self.s.toggles, t, False)
        try:
            with prof.stage("run", questions=len(qs), retrieval_only=retrieval_only):
                for qd in qs:
                    res, tr = self.query(qd["q"], log=log)
                    chunks = {h["chunk_id"]: {"text": h["text"]} for h in res["hits"]}
                    sc = score_result(qd, res["hits"], chunks, res["answer"], k)
                    if retrieval_only:
                        sc["answer_term_recall"] = None
                    from .evalset import primary_hits
                    rows.append(dict(sc, q=qd["q"], ms=tr["ms"], top=[h["chunk_id"] for h in primary_hits(res["hits"])[:k]],
                                     tokens=res.get("tokens", {}).get("total_tokens", 0), cached=res.get("cached", False),
                                     request_id=res.get("request_id"), run_id=res.get("run_id"),
                                     # 기대값을 결과에 싣는다 — 이게 없어서 "놓친 문항을 왜 놓쳤나" 로
                                     # 바로 넘어갈 수 없었다. 포렌식은 이 두 값만 있으면 바로 돌아간다.
                                     expect_docs=list(qd.get("expect_docs") or []),
                                     expect_terms=list(qd.get("expect_terms") or [])))
        finally:
            for t, v in prev_tog.items():
                if v is not None:
                    setattr(self.s.toggles, t, v)
        agg = aggregate(rows)
        agg["total_tokens"] = sum(r["tokens"] for r in rows)
        agg["avg_ms"] = round(sum(r["ms"] for r in rows) / max(1, len(rows)), 1)
        agg["retrieval_only"] = bool(retrieval_only)
        trace = prof.finish()
        from .evalset import discriminating
        out = {"summary": agg, "rows": rows, "config": {"toggles": self.s.toggles.__dict__},
               # 어떤 지표가 지금 변별력이 있나 — 전 문항 같은 값이면 그 눈금으로는 튜닝 효과를 못 본다
               "discriminating": discriminating(rows)}
        out["request_id"] = self.store.log_request("eval", "eval k=%d n=%d hit@k=%.3f" % (k, len(rows), agg["hit@k"]), trace,
                                                   out, {"toggles": self.s.toggles.__dict__}, keep=self.s.keep_requests)
        return out, trace

    # =====================================================================
    # GRAPH INSPECT
    # =====================================================================
    def graph_export(self, limit: int = 400, community: Optional[int] = None, provenance: Optional[str] = None,
                     types: Optional[List[str]] = None) -> Dict[str, Any]:
        """provenance: 'explicit,rule' 처럼 쉼표 목록으로 관계 출처 필터. types: 노드 유형 필터."""
        self.sync_with_db()
        ents = self.store.entities(limit * 3)
        ents = [e for e in ents if e["type"] not in ("date", "amount")]
        if community is not None:
            ents = [e for e in ents if e["community"] == community]
        if types:
            ents = [e for e in ents if e["type"] in types]
        ents = ents[:limit]
        ids = {e["entity_id"] for e in ents}
        prov = set(x.strip() for x in (provenance or "").split(",") if x.strip())
        rels = [r for r in self.store.relations_all() if r["src"] in ids and r["dst"] in ids and r["rel"] not in ("mentions_date", "mentions_amount")
                and (not prov or (r.get("provenance") or "") in prov)]
        merged: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for r in rels:
            key = (r["src"], r["dst"], r["rel"])
            m = merged.setdefault(key, {"src": r["src"], "dst": r["dst"], "rel": r["rel"], "weight": 0.0, "n": 0, "source": r["source"],
                                        "description": r["description"], "provenance": r.get("provenance") or "", "confidence": float(r.get("confidence") or 0)})
            m["weight"] += float(r["weight"] or 0)
            m["n"] += 1
            m["confidence"] = max(m["confidence"], float(r.get("confidence") or 0))
        return {"nodes": [{"id": e["entity_id"], "name": e["name"], "type": e["type"], "degree": e["degree"], "community": e["community"],
                           "source": e["source"], "n_docs": e.get("n_docs") or 0, "n_mentions": e.get("n_mentions") or 0} for e in ents],
                "edges": list(merged.values()), "communities": self.store.communities_all(),
                "provenance_counts": self.store.provenance_counts()}

    def build_status(self) -> Dict[str, Any]:
        """진행 중/마지막 빌드 상태: 락 보유자, 임베딩 진행률, 마지막 빌드, 최근 임베딩 실행."""
        from .buildlock import BuildLock
        lock = BuildLock(os.path.join(self.s.data_dir, "build.lock"))
        holder = lock.holder()
        running = bool(holder and not lock._is_stale(holder))
        return {"running": running, "lock": holder if running else None, "embed_progress": self.store.kv_get("embed_progress"),
                "last_build": self.store.kv_get("last_build"), "build_version": self.store.build_version(),
                "last_embed_run": (self.store.embed_runs(1) or [None])[0], "lint_summary": self.store.kv_get("lint_summary"),
                "stats": self.store.stats()}

    def entity_detail(self, entity_id: str) -> Dict[str, Any]:
        e = self.store.get_entity(entity_id)
        if not e:
            return {}
        try:
            e["doc_refs"] = json.loads(e.get("doc_refs") or "[]")
        except Exception:
            e["doc_refs"] = []
        if not e["doc_refs"]:
            # 구버전 코드로 빌드된 DB(doc_refs 미계산) 폴백: 이 엔티티만 즉시 계산해 저장
            self.store.refresh_doc_refs([entity_id])
            self.store.commit()
            e2 = self.store.get_entity(entity_id) or {}
            try:
                e["doc_refs"] = json.loads(e2.get("doc_refs") or "[]")
                e["n_docs"], e["n_mentions"] = e2.get("n_docs"), e2.get("n_mentions")
            except Exception:
                pass
        rels = self.store.relations_of(entity_id)
        for r in rels:
            r["src_name"] = (self.store.get_entity(r["src"]) or {}).get("name", r["src"])
            r["dst_name"] = (self.store.get_entity(r["dst"]) or {}).get("name", r["dst"])
        mentions = self.store.mentions_of(entity_id)
        chunks = self.store.get_chunks([m["chunk_id"] for m in mentions[:10]])
        docs = {d["doc_id"]: d for d in self.store.list_docs()}
        for ref in e["doc_refs"]:
            d = docs.get(ref["doc_id"])
            if d:
                ref["title"] = d["title"]
                ref["path"] = d["path"]
        return {"entity": e, "relations": rels[:60],
                "mentions": [dict(m, heading=chunks[m["chunk_id"]]["heading"], text=chunks[m["chunk_id"]]["text"][:300])
                             for m in mentions[:10] if m["chunk_id"] in chunks]}

    # =====================================================================
    # SYSTEM / SCALE
    # =====================================================================
    def system_info(self, target_docs: int = 3000, daily_new: int = 20, horizon_days: int = 365) -> Dict[str, Any]:
        st = self.store.stats()
        docs, chunks, embs = max(1, st["docs"]), st["chunks"], st["embeddings"]
        dim = getattr(self.embedder, "dim", 0) or 0
        avg_chunks = chunks / docs
        db_per_doc = st["db_bytes"] / docs
        future_docs = target_docs + daily_new * horizon_days
        def proj(n_docs: int) -> Dict[str, Any]:
            n_chunks = int(n_docs * avg_chunks)
            return {"docs": n_docs, "chunks": n_chunks, "vector_matrix_mb": round(n_chunks * dim * 4 / 1e6, 1),
                    "db_mb": round(db_per_doc * n_docs / 1e6, 1)}
        builds = self.store.request_series("build", 30)
        queries = self.store.request_series("query", 100)
        qms = sorted(r["ms"] for r in queries) if queries else []
        wal = self.s.db_path + "-wal"
        return {
            "index": st,
            "avg_chunks_per_doc": round(avg_chunks, 2),
            "embedding": {"provider": self.embedder.name, "dim": dim, "stored": embs,
                          "matrix_mb_now": round(embs * dim * 4 / 1e6, 1)},
            "projection": {"current": proj(docs), "target": proj(target_docs), "after_horizon": proj(future_docs),
                           "assumptions": {"target_docs": target_docs, "daily_new": daily_new, "horizon_days": horizon_days,
                                           "note": "hash 임베딩은 dim×4 bytes/청크. 3만 청크×4096d ≈ 490MB → embed_dim 1024 권장 (≈120MB) 또는 외부 임베더(1024d)"}},
            "build_history": builds,
            "query_latency": {"n": len(qms), "avg_ms": round(sum(qms) / len(qms), 1) if qms else None,
                              "p50_ms": qms[len(qms) // 2] if qms else None, "p95_ms": qms[int(len(qms) * 0.95)] if qms else None},
            "caches": self.cache_info(),
            "watcher": dict(self.watcher, enabled=self.s.toggles.auto_build, interval=self.s.auto_build_interval),
            "files": {"db_mb": round(st["db_bytes"] / 1e6, 2), "wal_mb": round(os.path.getsize(wal) / 1e6, 2) if os.path.exists(wal) else 0},
            "corpus_dirs": self.s.corpus_dirs,
            "console": (lambda: __import__("llmwiki.console", fromlist=["describe"]).describe())(),
            "toggles": self.s.toggles.__dict__,
            "perf_settings": {k: getattr(self.s, k) for k in ("rerank_candidates", "rerank_chunk_chars", "context_max_chars",
                                                                "context_chunk_chars", "answer_max_tokens", "llm_graph_budget",
                                                                "llm_graph_min_chars", "query_cache_size", "auto_build_interval",
                                                                "embed_batch", "debug_level", "keep_requests")},
        }

    def maintenance(self, action: str) -> Dict[str, Any]:
        t0 = time.perf_counter()
        if action == "vacuum":
            self.store.conn.execute("VACUUM")
        elif action == "fts_optimize":
            self.store.fts_optimize()
        elif action == "wal_checkpoint":
            self.store.wal_checkpoint()
        elif action == "clear_cache":
            self.clear_caches()
        elif action == "warm_cache":
            self.store.vector_matrix(self.embedder.name)
            self.store.entity_index()
        elif action == "refresh_doc_refs":
            self.store.refresh_doc_refs(None)
            self.store.commit()
        elif action == "purge_requests":
            self.store.conn.execute("DELETE FROM requests")
            self.store.conn.commit()
        elif action == "prune_requests":
            # 보관 폴더에서 requests_keep_days 를 넘긴 결과 파일만 지운다 (DB 는 건드리지 않는다).
            pr = self.store.prune_request_archive(self.s.requests_archive_dir(), int(self.s.requests_keep_days or 0))
            return {"ok": True, "action": action, "ms": round((time.perf_counter() - t0) * 1000, 1), "archive": pr}
        else:
            return {"error": "unknown action %s" % action}
        return {"ok": True, "action": action, "ms": round((time.perf_counter() - t0) * 1000, 1), "stats": self.store.stats()}
