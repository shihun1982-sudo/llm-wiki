# -*- coding: utf-8 -*-
"""구조/흐름 레지스트리 — Web UI 'Architecture' 탭과 CLI `arch` 가 공유하는 단일 정의.

계층은 **흐름 → 페이즈 → 단계 → trace 노드** 네 단이다 (2026-09-19). 페이즈(phase)는 단계 위의 묶음으로,
"지금 어느 국면인가" 를 먼저 읽고 필요한 단계로 내려가기 위한 것이다 — 질의 흐름만 26단계라 단계만 나열하면
어디가 검색이고 어디가 답변인지 한눈에 들어오지 않는다. Web 🧭 Pipeline 페이지와 CLI `arch` 가 같은 묶음을 쓴다.

flows: build / query / evolve / watch 각각의 단계(stage) 순서와, 단계마다
  - phase   : 상위 묶음 키 (PHASES[flow][phase] 에 제목·설명·색)
  - toggles : 이 단계를 켜고 끄거나 동작을 바꾸는 Toggles 이름
  - tunables: tuning.py 레지스트리에서 이 단계에 속한 파라미터 키 (자동 수집)
  - settings: config.json 의 관련 키
  - cli     : 관련 CLI 명령/플래그
  - impact  : 이 단계가 품질/속도/토큰에 미치는 영향 요약
  - trace   : 프로파일 trace 에서 이 단계에 해당하는 stage 이름들 (마지막 실행 시간을 겹쳐 보일 때 사용)
"""
from __future__ import annotations

from typing import Any, Dict, List

from .config import TOGGLE_HELP, SETTING_HELP
from .tuning import TUNABLES, STAGES


def _s(key: str, title: str, desc: str, impact: str, toggles: List[str] = (), settings: List[str] = (), cli: List[str] = (),
       trace: List[str] = (), tuning_stage: str = "", module: str = "", io: str = "", phase: str = "") -> Dict[str, Any]:
    return {"key": key, "title": title, "desc": desc, "impact": impact, "toggles": list(toggles), "settings": list(settings),
            "cli": list(cli), "trace": list(trace) or [key], "tuning_stage": tuning_stage, "module": module, "io": io,
            "phase": phase}


# 페이즈(상위 단계) — 흐름마다 순서대로. 단계의 phase 가 비어 있으면 registry() 가 STAGE_PHASE 로 채운다.
PHASES: Dict[str, List[Dict[str, str]]] = {
    "build": [
        {"key": "prepare", "title": "준비 · 점검", "desc": "빌드를 시작해도 되는지 확인하고 프로바이더를 만든다. 여기서 막으면 중간 실패가 없다."},
        {"key": "ingest", "title": "수집 · 변경 감지", "desc": "코퍼스 폴더를 읽어 문서로 만들고, 지난 빌드와 비교해 바뀐 것만 고른다."},
        {"key": "index", "title": "채널 색인", "desc": "청크 → FTS · 벡터 · 그래프. 세 채널은 서로 독립이라 `build fts|vector|graph` 로 따로 다시 만들 수 있다."},
        {"key": "derive", "title": "파생물 생성", "desc": "색인에서 사람이 읽을 위키 페이지를 만든다."},
        {"key": "finish", "title": "정리 · 예열", "desc": "고아 데이터를 지우고 캐시를 데워 첫 질의를 빠르게 한다."},
    ],
    "query": [
        {"key": "prepare", "title": "준비 · 캐시", "desc": "색인 최신화 · 역할 LLM 준비 · 같은 질의면 여기서 즉시 답한다(뒤 단계 전부 생략)."},
        {"key": "understand", "title": "질의 이해 · 확장", "desc": "무엇을 묻는지 정리해 **검색어를 만든다**. 여기가 틀리면 뒤 단계가 아무리 좋아도 못 찾는다."},
        {"key": "retrieve", "title": "채널 검색", "desc": "FTS · 벡터 · 그래프 · 문서카드 · 외부 RAG 를 **각각** 돌려 후보를 모은다. 채널이 꺼져 있으면 그 신호는 아예 없다."},
        {"key": "select", "title": "융합 · 선별", "desc": "채널별 순위를 합치고(RRF) 부스트·주입·리랭크로 **최종 순위**를 정한다. 최종 품질에 가장 직접적인 구간."},
        {"key": "compose", "title": "컨텍스트 · 답변", "desc": "고른 근거로 컨텍스트를 짜고 충분한지 판정한 뒤 답을 만들고 인용을 검증한다. **시간·토큰의 대부분**이 여기 있다."},
        {"key": "after", "title": "사후 기록 · 학습", "desc": "왜 그렇게 답했는지 남기고, 개선 제안을 모은다. 답 자체는 바뀌지 않는다."},
    ],
    "evolve": [
        {"key": "capture", "title": "제안 수집", "desc": "질의 갭·피드백·로그 검토에서 고칠 거리를 모은다."},
        {"key": "review", "title": "검토 · 적용", "desc": "사람이 승인(HITL)하고, 되돌릴 수 있게 스냅샷을 남긴 뒤 반영한다."},
        {"key": "verify", "title": "검증 · 이력", "desc": "회귀 평가로 좋아졌는지 확인하고 나빠지면 되돌린다."},
    ],
    "watch": [
        {"key": "scan", "title": "변경 감지", "desc": "파일을 읽지 않고 stat 만 비교 — 변경이 없으면 비용이 거의 0."},
        {"key": "apply", "title": "증분 반영", "desc": "바뀐 문서만 build 흐름으로 다시 넣는다."},
    ],
}

# 단계 → 페이즈 (단계 정의에 phase 를 적지 않았을 때 쓰는 기본 배치). 새 단계를 넣으면 여기에도 한 줄 추가한다 —
# 빠뜨리면 registry() 가 "(기타)" 페이즈로 모으고 tests/test_tuning_arch.py 가 알려 준다.
STAGE_PHASE: Dict[str, str] = {
    # build
    "health": "prepare", "providers": "prepare",
    "load_corpus": "ingest", "diff": "ingest",
    "chunk_index": "index", "embed": "index", "graph_build": "index",
    "wiki_pages": "derive",
    "prune": "finish", "warm_cache": "finish",
    # query
    "sync_index": "prepare", "cache_hit": "prepare",
    "time_scope": "understand", "query_rules": "understand", "router": "understand", "query_expand": "understand", "pins": "understand",
    "fts_search": "retrieve", "vector_search": "retrieve", "graph_search": "retrieve", "doc_vector_search": "retrieve", "external_rag": "retrieve",
    "rrf_fuse": "select", "doc_acl": "select", "fusion_llm": "select", "rerank": "select", "rerank_review_llm": "select",
    "doc_expand": "compose", "context": "compose", "evidence": "compose", "answer": "compose", "claim": "compose",
    "forensic": "after", "evolve_capture": "after", "log": "after", "analysis": "after",
    # evolve
    "capture": "capture", "hitl": "review", "snapshot": "review", "apply": "review",
    "regress": "verify", "evolution_log": "verify",
    # watch
    "scan": "scan", "build_incremental": "apply",
}


FLOWS: Dict[str, Dict[str, Any]] = {
    "build": {
        "title": "Data Build (색인)", "entry": "python -m llmwiki build [--full] · Web Build 탭 · watch/auto_build",
        "desc": "코퍼스 폴더 → 문서 → 청크(FTS5) → 벡터 → 지식 그래프 → 위키 페이지. 증분 빌드는 변경 문서만 다시 처리.",
        "stages": [
            _s("health", "Health 검사", "빌드 전 Python/SQLite FTS5·DB 무결성·WAL·디스크·코퍼스 폴더·벡터 메모리 전망·임베딩 차원·(필요 역할) LLM/임베더/rerank/MCP ping.",
               "실패(fail) 항목이 있으면 빌드를 시작하지 않아 중간 실패를 예방. warn 은 alerts 로 보고.",
               ["health_check"], ["build_lock_timeout", "build_lock_stale_s"], ["health", "health --quick", "build --force", "build --no-health-check"], module="health.py"),
            _s("providers", "프로바이더 준비", "역할별 LLM(extract/summary)·임베더 인스턴스 생성. auto 면 API 키/Ollama 탐지.",
               "최초 1회 지연(Ollama 탐지 ~0.8s). 프로바이더가 없으면 LLM 단계는 모두 skipped.",
               ["llm_graph", "community_summary"], ["llm_provider", "llm_model", "llm_roles", "embed_provider", "embed_model"],
               ["models set …", "--llm", "--embed-provider", "--extract-model"], module="providers.py"),
            _s("load_corpus", "코퍼스 로드", "corpus_dirs 를 재귀 스캔해 .md .txt .csv .html .pdf 를 읽고 sha1 해시. stat_skip 이면 mtime/size 같은 파일은 읽지 않음.",
               "파일 수·PDF 파싱에 비례. stat_skip 으로 변경 없는 빌드는 수십 ms.", ["stat_skip", "incremental"], ["corpus_dirs"],
               ["build", "build --no-stat-skip", "watch"], trace=["load_corpus", "mcp_ingest"], module="corpus.py", io="files → Document[]"),
            _s("diff", "변경 감지", "DB 의 문서 해시와 비교해 changed / removed / new 분류. --full 이면 전부 changed.",
               "증분 범위를 결정. incremental 끄면 매번 전체 재처리.", ["incremental"], [], ["build --full", "build --no-incremental"], module="pipeline.py"),
            _s("chunk_index", "청킹 · FTS 색인 (채널 fts)", "헤딩 단위 섹션 → 문단 경계 분할(오버랩) → chunks + chunks_fts(BM25, 조사 제거 토큰·bigram·메타 토큰). build_fts 를 끄면 FTS 행을 쓰지 않음. `build fts` 는 chunks 는 그대로 두고 FTS 행만 다시 쓴다(임베딩·그래프 불변).",
               "청크 크기가 검색 정밀도/컨텍스트 토큰/벡터 메모리를 좌우. 값 변경 시 전체 리빌드(세 채널 모두).",
               ["build_fts", "fts_trigram"], ["chunk_max_chars", "chunk_overlap_chars"], ["build --full", "build fts", "build --channels fts"], trace=["chunk_index", "build_channel", "reindex_fts"],
               tuning_stage="chunk_index", module="corpus.py · store.py", io="Document → Chunk[] → FTS5"),
            _s("embed", "벡터 임베딩 (채널 vector)", "청크 텍스트를 임베딩해 embeddings 테이블에 저장. hash 임베더는 IDF 를 전체 빌드에서 적합. `build vector` 는 임베딩 없는 청크만(--full 이면 전부) 다시 임베딩한다(FTS·그래프 불변).",
               "벡터 채널 recall 의 원천. hash 는 오프라인·비의미적, voyage/st 는 의미 검색. 메모리 = 청크×dim×4B.",
               ["embed", "idf_refit_incremental", "embed_adaptive"], ["embed_provider", "embed_model", "embed_dim", "embed_batch"],
               ["build --no-embed", "build vector [--full]", "build --channels vector", "models set embed_provider=voyage"],
               trace=["embed", "doc_vectors"], tuning_stage="embed", module="providers.py · embed_run.py", io="Chunk[] → float32[dim]"),
            _s("graph_build", "그래프 추출 (채널 graph)", "규칙(사전·정규식) 및/또는 LLM 으로 엔티티·관계·멘션 추출 → degree → 노드 doc_refs → (전체 빌드) 커뮤니티 → (옵션) 요약. `build graph` 는 그래프 테이블을 비우고 전체 청크에서 재추출한다(FTS·임베딩 불변).",
               "그래프 채널·위키·엔티티 상세의 원천. 규칙은 무료·결정적, LLM 은 청크당 1회 호출(토큰↑, budget 으로 제한; 실패는 llm_report).",
               ["rule_graph", "llm_graph", "communities", "community_summary", "incremental_communities", "explicit_relations"],
               ["llm_graph_budget", "llm_graph_min_chars", "llm_roles.extract", "llm_roles.summary"],
               ["build --llm-graph", "build --no-rule-graph", "build graph", "build --channels graph", "entity <name>", "graph"],
               trace=["graph_build", "rule_extract", "llm_extract", "degrees", "doc_refs", "communities", "community_summary"], tuning_stage="graph_build",
               module="graph_rules.py · graph_llm.py · graph_build.py", io="Chunk[] → entities/relations/mentions"),
            _s("wiki_pages", "위키 페이지", "엔티티별 마크다운(설명·문서 참조·관계·근거 문단·편집 노트) + INDEX. 증분은 변경 엔티티만.",
               "사람이 편집한 `## 편집 노트` 가 다음 빌드에 overlay 문서로 재색인됨(HITL 진화 경로).",
               ["wiki_pages", "wiki_full_rewrite"], ["wiki_dir"], ["wiki", "build --no-wiki-pages"], module="wiki.py", io="entities → wiki/*.md"),
            _s("prune", "정리 · 유지보수", "고아 엔티티·끊긴 참조 삭제, 다른 임베더의 벡터 삭제, (전체 빌드) FTS optimize, WAL checkpoint.",
               "검색 속도 유지·DB 비대 방지.", ["fts_optimize"], [], ["maintenance fts_optimize|vacuum|wal_checkpoint"],
               trace=["prune", "verify"], module="store.py"),
            _s("warm_cache", "캐시 예열", "벡터 행렬·엔티티 인덱스를 메모리에 적재.", "빌드 직후 첫 질의 지연 제거(3만 청크면 수 초).",
               ["warm_cache"], [], ["maintenance warm_cache"], trace=["warm_cache", "precompute"], module="store.py"),
        ],
    },
    "query": {
        "title": "User Request (질의 → 답변)", "entry": "python -m llmwiki query \"…\" · Web Query 탭 · MCP wiki_query",
        "desc": "질의 → (캐시) → 라우터 → 3채널 검색 → 융합 → 리랭크 → 컨텍스트 → 답변 → 자가진화 캡처 → 로그. 모든 단계가 trace 로 기록.",
        "stages": [
            _s("sync_index", "인덱스 동기화", "다른 프로세스가 빌드했으면(build_version 변경) 메모리 캐시를 버림.", "재시작 없이 최신 색인 반영.", [], [], [], module="pipeline.py"),
            _s("providers", "프로바이더 준비", "answer/rerank 역할 LLM·임베더 인스턴스 (프로세스당 1회).", "최초 질의 지연. 이후 0ms.",
               ["llm_answer", "rerank_llm"], ["llm_roles.answer", "llm_roles.rerank"], ["--answer-model", "--rerank-model", "--llm mock"], module="providers.py"),
            _s("cache_hit", "질의 캐시 · 프리컴퓨트", "같은 질의+토글+튜닝+빌드버전이면 메모리 캐시 결과를, precompute 토글이면 영속 answer_cache(사전 계산) 결과를 즉시 반환.",
               "LLM 토큰 0, 수 ms. 로그/자가진화는 건너뜀. 재빌드 시 자동 무효화.",
               ["query_cache", "precompute", "precompute_after_build"], ["query_cache_size"], ["query … --no-query-cache", "precompute run", "maintenance clear_cache"],
               trace=["cache_hit", "precompute_hit", "precompute_miss"], module="pipeline.py · precompute.py"),
            _s("time_scope", "시간 표현 해석", "'지난주·어제·3일전·2026년 8월·Q3' 등 한국어 상대/절대 시간 표현을 timezone 기준 날짜 범위로 변환하고 검색 질의에서 제거. 문서 날짜(front matter date → 파일명 → mtime) 로 boost 또는 filter.",
               "시간 조건이 있는 질문의 정밀도↑. filter 가 0건이면 boost 로 자동 완화.", ["time_scope"], ["timezone", "week_start"], ["--no-time-scope", "tuning set time_mode=filter"],
               tuning_stage="time_scope", module="timeparse.py"),
            _s("query_rules", "규칙 기반 질의 확장", "query_rules.json 의 acronym(동치·구문) / synonym(OR, 가중) / alias(정규화 치환) / related(보조 리스트) / exclude(NOT+페널티) 를 유형별로 다르게 적용.",
               "LLM 없이 결정적으로 recall↑. related 를 별도 리스트로 두어 precision 보호. profile_expansion 으로 전/후 효과 기록.",
               ["query_rules", "profile_expansion"], [], ["rules test \"질의\"", "--no-query-rules"],
               trace=["query_rules", "expansion_profile"], tuning_stage="query_rules", module="query_rules.py",
               io="query → {fts_query, alt_queries[], exclude[], seeds[]}"),
            _s("router", "적응형 라우터", "키워드 수·엔티티 매칭·관계어·숫자 유무로 keyword/relational/semantic/hybrid 분류 → 채널 가중치. router_llm 이면 LLM 의도/문서유형 분류를 보탬.",
               "채널 가중치가 융합 결과를 직접 좌우. 끄면 1:1:1.", ["router", "router_llm"], [], ["--no-router"], tuning_stage="router", module="retrieval.py", io="query → weights{fts,vector,graph}"),
            _s("query_expand", "LLM 질의 확장 (옵션)", "LLM(expand 역할) 이 추가 검색 질의 n개 + (query_decompose) 다중 홉 sub-query 를 생성 → 원 질의는 유지하고 FTS/벡터를 추가 실행해 별도 리스트로 융합.",
               "어휘 불일치 완화(recall↑). LLM 1회(토큰·지연↑).", ["query_expand", "query_decompose"], ["llm_roles.expand"], ["--query-expand", "--query-decompose"],
               tuning_stage="query_expand", module="query_engine.py"),
            _s("pins", "고정 근거(pin)", "pins.json 의 조건(always/keywords/query/doc_types)에 맞는 문서·청크를 후보에 주입하고 fusion 에서 pin_boost 배율.",
               "코딩 규칙처럼 특정 질의 유형에 항상 포함할 문서, 사용자가 확인한 정답 근거 고정.", ["pins"], [], ["pin add --doc … --keywords …", "pin list"], module="pins.py"),
            _s("fts_search", "FTS(BM25)", "동의어 확장 → (tiered) AND 검색 → OR 보충 → bigram 폴백 → (옵션) PRF 재검색.",
               "정확 키워드·숫자·날짜에 강함. 한국어 조사 변형은 토큰 컬럼으로 흡수.", ["fts"], ["top_k_fts"], ["--no-fts", "search fts \"…\""],
               trace=["fts_search", "fts_search_rules", "fts_search_alt", "fts_search_related"],
               tuning_stage="fts_search", module="retrieval.py · store.py", io="query → [(chunk, bm25)]"),
            _s("vector_search", "벡터 검색", "질의 임베딩 × 청크 행렬 내적 → 상위 k.", "의미 유사 문단 recall. hash 임베더는 표기 변형에 강하나 의미 이해는 못함.",
               ["vector"], ["top_k_vector"], ["--no-vector", "search vector \"…\""], tuning_stage="vector_search", module="retrieval.py", io="query → [(chunk, cos)]"),
            _s("graph_search", "그래프 검색", "라우터 시드 엔티티 → n-hop 확장(감쇠·허브 페널티) → 멘션 청크 수집 → 키워드 커버리지 재가중 (LightRAG 이중 검색).",
               "다중 홉·관계형 질문에 강함. 시드가 없으면 빈 결과.", ["graph"], ["top_k_graph", "graph_hops"], ["--no-graph", "search graph \"…\"", "entity <name>"],
               tuning_stage="graph_search", module="retrieval.py", io="seeds → entities → [(chunk, score)]"),
            _s("doc_vector_search", "문서 카드 벡터 (옵션)", "문서 단위 카드(제목·메타·헤딩 개요·첫 문단) 임베딩 유사도 → 문서 대표 청크를 후보로.",
               "긴 설계 문서의 문서 단위 recall↑. 빌드 시 doc_vectors 생성 필요.", ["doc_vector"], [], ["--doc-vector", "precompute doc-vectors"], module="precompute.py"),
            _s("external_rag", "외부 RAG 채널 (옵션)", "mcp_sources.json 의 retrieve 매핑(다른 RAG 의 MCP tool 또는 REST 검색 API)을 호출해 결과를 가상 청크 ext:<source>:<id> 로 만들고 채널 ext_<source> 로 융합에 넣는다. 인용 [C#] 가능, hits.external 에 source/url.",
               "다른 팀 RAG·사내 검색을 코드 수정 없이 채널로 추가. 외부 지연이 더해지므로 timeout_s 와 weight 로 제어. 소스 오류는 채널 하나만 비고 질의는 계속.",
               ["external_rag"], [], ["mcp-source retrieve \"…\"", "tuning set channel_w_external=0.7", "--no-external-rag"],
               trace=["external_rag", "mcp_enrich", "external_inject"], tuning_stage="rrf_fuse", module="mcp_client.py · query_engine.py", io="query → [(ext chunk, score)]"),
            _s("rrf_fuse", "융합 · 부스트", "채널별 순위를 RRF / 가중 RRF / min-max / z-score / dbsf 로 합친 뒤 post-fusion boost(문서유형·시간·최신성·pin·provenance·피드백·exclude 페널티) 적용.",
               "다중 채널 합의 후보가 상위로. 방식은 `fusion compare` 로 평가셋 비교. 모든 배율은 hit.boosts 에 기록.",
               ["pins", "feedback_boost"], ["rrf_k"], ["tuning set fusion_method=zscore", "fusion compare", "pin add …"], trace=["rrf_fuse", "boost", "channel_inject"],
               tuning_stage="rrf_fuse", module="fusion.py", io="lists → Hit[] (fused, boosted)"),
            _s("doc_acl", "문서 접근 제어", "요청자의 역할로 근거를 거른다. `docacl.json` 의 경로 규칙과 문서 front matter 의 `acl:` 중 **더 높은 등급**이 적용되고, 가려진 문서의 청크는 리랭크·컨텍스트·답변·인용 어디에도 닿지 않는다.",
               "검색은 읽기다 — 등급이 다른 문서가 섞인 위키에서 RAG 가 유출 경로가 되지 않게 한다. 규칙이 비어 있으면 아무도 막지 않고, admin 은 항상 전부 본다. 가려진 건수는 trace 와 응답 메타에 남는다.",
               ["doc_acl"], [], ["security docacl show", "security docacl check --role viewer"],
               trace=["doc_acl"], module="docacl.py", io="Hit[] → Hit[] (역할로 필터)"),
            _s("fusion_llm", "융합 뒤 LLM 검토 (옵션)", "융합·부스트 직후(리랭크 전) LLM(역할 fusion, prompts/fusion_review.md)이 상위 fusion_llm_candidates 후보의 제목·발췌를 보고 명백히 무관한 것을 keep/drop 으로 고른다. drop 은 fused × fusion_llm_drop_penalty (0 이면 목록에서 제거, why=llm_drop).",
               "리랭크 창에 들어갈 후보를 미리 걸러 정밀도↑·리랭크 토큰↓. LLM 1회 추가. 실패·파싱 오류면 순위를 그대로 둔다(품질을 깎지 않는 방향). 전부 drop 하라는 응답은 무시한다.",
               ["llm_after_fusion"], ["llm_roles.fusion"], ["tuning set fusion_llm_candidates=30 fusion_llm_drop_penalty=0"],
               trace=["fusion_llm"], tuning_stage="rrf_fuse", module="query_engine.py", io="Hit[] (fused) → Hit[] (일부 감점·제거)"),
            _s("rerank", "리랭크", "후보 상위 n개를 LLM(JSON 순위) / 크로스인코더 / 로컬 휴리스틱(커버리지·합의·길이·헤딩)으로 재정렬.",
               "최종 MRR 에 가장 직접적. LLM 은 토큰 1회, 크로스인코더는 로컬 CPU, 로컬은 수 ms.",
               ["rerank", "rerank_llm"], ["rerank_candidates", "rerank_chunk_chars", "llm_roles.rerank"], ["--no-rerank", "--no-rerank-llm", "tuning set rerank_method=cross_encoder"],
               trace=["rerank_llm", "rerank_cross_encoder", "rerank_local", "rerank_api"], tuning_stage="rerank", module="retrieval.py"),
            _s("rerank_review_llm", "리랭크 뒤 LLM 선택 (옵션)", "리랭크 직후(문서 확장·컨텍스트 전) LLM(역할 select, prompts/rerank_review.md)이 상위 post_rerank_llm_k 후보 중 컨텍스트에 넣을 청크를 중요한 순서대로(select) 고르고, 한 절로는 부족해 통째로 읽어야 할 문서(expand_docs)를 지목한다.",
               "컨텍스트 구성을 LLM 이 정한다(정밀도↑). expand_docs 는 doc_expand 가 그 문서를 우선·전체 확장한다(토큰↑). LLM 1회 추가. 실패·빈 select 면 리랭크 순위 그대로.",
               ["llm_after_rerank"], ["llm_roles.select"], ["tuning set post_rerank_llm_k=16"],
               trace=["rerank_review_llm"], tuning_stage="rerank", module="query_engine.py", io="Hit[] (reranked) → Hit[] (재정렬) + expand_docs"),
            _s("doc_expand", "문서 단위 확장", "리랭크 상위 청크가 속한 문서(doc_expand_top_docs)의 나머지 청크를 질의 키워드 커버리지·벡터 유사도(hybrid)로 점수화해 임계 이상만 문서 순서로 컨텍스트에 추가 (부모 청크 뒤, [C#] 인용 가능, why=doc_expand).",
               "한 문서의 표·목록·후속 문단이 잘려 나가는 문제 완화(근거 완전성↑). 토큰↑ → 상한(max_chunks·min_score)으로 제어. speed/token 프리셋은 off.",
               ["doc_expand"], [], ["--no-doc-expand", "tuning set doc_expand_max_chunks=5"], tuning_stage="context", module="query_engine.py", io="final Hit[] → extra chunks"),
            _s("context", "컨텍스트 구성", "상위 top_k_final 청크(+doc_expand 추가 청크)를 [C#] 블록으로 조립. 중복 제거(오버랩·유사 문단)·문장 압축·인접 청크·그래프 관계 첨부. "
               "상한은 `context_max_chars` 와 **모델 창**(models.json `context_k`) 중 작은 쪽 — 창을 넘기면 프롬프트가 조용히 잘려 뒤쪽 근거가 사라지므로 미리 줄이고 그 이유를 단계 note 에 남긴다. "
               "상한에 걸린 근거는 남은 자리가 `context_min_fit_chars` 이상이면 **잘라서** 넣고(truncated), 아니면 건너뛰고 다음 순위를 계속 본다. "
               "문서 본문은 데이터로 취급해 구획으로 감싸고 구조 흉내 조각을 무력화한다(토글 `context_guard`).",
               "답변 LLM 입력 토큰과 근거 완전성을 결정.", ["context_trim", "dedupe_hits", "context_guard"],
               ["top_k_final", "context_max_chars", "context_chunk_chars"],   # 튜닝 키(context_chars_per_token 등)는 tuning_stage="context" 로 자동 수집된다
               ["--no-context-trim", "--no-dedupe-hits", "--k 8"], tuning_stage="context", module="answer.py", io="Hit[] → context text + citations"),
            _s("evidence", "근거 충분성 · fallback 루프", "컨텍스트가 질문에 답하기에 충분한지 판정(휴리스틱: 점수·채널 합의·키워드 커버리지·글자수, 옵션 LLM). 부족하면 L1 규칙 확장 → L2 LLM 확장/분해 → L3 그래프 hops+1·인접 청크 → L4 광역(k×n, 유형 필터 해제, MCP) 순으로 재검색. attempt/token/latency 예산.",
               "근거 부족 답변을 줄임. 불충분할 때만 비용 발생. 무한 루프 방지 예산 필수.",
               ["evidence_check", "evidence_check_llm", "fallback_loop", "mcp_sources"], ["llm_roles.verify"], ["--fallback-loop", "tuning set fallback_max_attempts=3"],
               trace=["evidence_check", "fallback"], tuning_stage="evidence", module="evidence.py"),
            _s("answer", "답변 생성", "LLM(prompts/answer_system.md + answer_guide.md, 인용 강제, 구조화·상세) 또는 추출식(키워드 문장 선택) 답변. 근거 부족이면 insufficient_data 응답.",
               "품질의 최종 출력. LLM 없으면 자동 추출식. 가이드 md 를 편집해 구조/문체 변경.",
               ["llm_answer", "evidence_compress"], ["answer_max_tokens", "answer_effort", "llm_roles.answer"], ["--no-llm-answer", "--answer-model …", "prompts show answer_guide"],
               trace=["answer_llm", "answer_extractive", "answer_insufficient", "evidence_compress"], tuning_stage="answer", module="answer.py"),
            _s("claim", "답변 검증 (claim check)", "답변을 문장으로 나눠 사실 문장마다 [C#] 인용 존재 + 인용 근거가 실제로 지지하는지(키워드·수치·ID 대조, 옵션 LLM/NLI) 검증 → groundedness. 미지원 문장은 표기/제거/재작성.",
               "hallucination 억제. 휴리스틱은 무료, LLM 판정은 토큰↑.", ["claim_check", "claim_check_llm", "answer_refine"], ["llm_roles.verify"],
               ["--claim-check-llm", "tuning set claim_policy=drop"], trace=["claim_check", "answer_refine"], tuning_stage="claim", module="answer.py"),
            _s("forensic", "포렌식 (자동 · 기대 결과)", "근거 부족·낮은 groundedness 시 trace 를 진단 규칙으로 분석해 findings/suggestions 를 forensics 테이블에 기록. 누적 → 제안. "
               "사용자가 기대 문서/용어를 주면(forensic expect) 같은 설정으로 검색을 재실행해 기대 청크가 fts/vector/graph → 융합 → 리랭크 → 컨텍스트 → 답변 중 어느 단계에서 탈락했는지와 수정안을 낸다.",
               "실패 원인 추적과 자가진화 데이터 확보. LLM 호출 실패는 llm_report 로 함께 보고.", ["forensic_auto", "llm_failure_report"], ["llm_retries", "llm_retry_backoff_s"],
               ["forensic last", "forensic <request_id>", "forensic summary", "forensic expect last --doc ISSUE-2003 --term 0x40"],
               trace=["forensic", "forensic_expect"], tuning_stage="forensic", module="forensic.py"),
            _s("evolve_capture", "자가진화 캡처", "시드 없음·낮은 점수·무인용 답변을 감지해 alias/entity/synonym/wiki_note 제안 생성.",
               "제안은 Evolve 탭에서 HITL 승인. 자동 적용은 evolve_auto_apply.", ["evolve_capture", "evolve_auto_apply"], ["evolve_min_confidence", "evolve_low_score_threshold"],
               ["query … --no-log", "evolve status"], trace=["evolve_capture", "episode"], module="evolve.py"),
            _s("log", "로그 · 요청 기록", "query_log(피드백용) + requests(전체 trace).", "Requests 탭 / `requests` CLI 의 원천.", [], ["keep_requests"], ["requests list", "requests last"], trace=["log"], module="store.py"),
            _s("analysis", "상세 분석 리포트 (analysis_mode)", "토글이 켜지면 질의를 debug_level 2 로 실행하고 설정 스냅샷·단계 타임라인·채널/융합/리랭크/컨텍스트 상세·답변 판정·품질/속도/토큰 렌즈 소견(조절점=토글/튜닝 키와 현재값)·자동 포렌식·프롬프트 샘플을 logs/analysis/req_<id>.md 로 남긴다. 저장된 요청도 `analyze <id>` 로 생성(요약 수준).",
               "품질/지연/토큰 디버깅의 출발점. LLM 에게 그대로 넘겨 튜닝을 물을 수 있다. 질의당 수십 ms·requests 행 크기 증가 → 디버깅할 때만 켠다.",
               ["analysis_mode"], ["debug_level", "keep_requests"], ["query \"…\" --analyze --focus speed", "analyze last --print", "analyze <id> --out report.md"], trace=[], module="analysis.py"),
        ],
    },
    "evolve": {
        "title": "Self-Evolving (제안 → 적용 → 검증)", "entry": "Web Evolve 탭 · evolve status|apply|reject|review|feedback",
        "desc": "질의 캡처·👍/👎 피드백·LLM 리뷰가 데이터 수정 제안을 만들고, 사람이 승인하면 스냅샷 → 적용 → 재색인 → 회귀 평가 → 승격/롤백.",
        "stages": [
            _s("capture", "제안 생성", "evolve_capture(질의 갭) · record_feedback(👎 + 정정문 → wiki_note) · llm_review(질의 로그 검토).",
               "데이터 전용 제안(코드/프롬프트 변경 없음).", ["evolve_capture"], ["llm_roles.review"], ["evolve review", "evolve feedback <qid> -1 \"정정\""], trace=["evolve_capture"], module="evolve.py"),
            _s("hitl", "검토 (HITL)", "proposals 큐를 사람이 승인/거절. evolve_auto_apply 면 고신뢰 제안 자동 적용.",
               "안전장치: 기본은 수동 승인.", ["evolve_auto_apply"], ["evolve_min_confidence"], ["evolve status", "evolve apply <id>", "evolve reject <id>"], trace=[], module="evolve.py"),
            _s("snapshot", "스냅샷", "rules.json·synonyms·위키 노트를 data/snapshots 에 저장.", "롤백 가능성 확보.", [], [], [], trace=[], module="evolve.py"),
            _s("apply", "적용 · 재색인", "synonym → 즉시, alias/entity/relation → rules.json + 증분/그래프 재빌드, wiki_note → 위키 편집 노트 → 재색인.",
               "빌드 흐름을 재사용 (변경 문서만).", [], [], ["evolve apply <id> --no-eval"], trace=["build"], module="evolve.py · pipeline.build"),
            _s("regress", "회귀 평가 · 승격/롤백", "eval/questions.json 으로 hit@k·term recall 비교. 악화되면 스냅샷 복원.",
               "품질 저하 자동 차단.", [], [], ["eval", "eval --matrix"], trace=["eval", "run"], module="evalset.py"),
            _s("evolution_log", "이력", "적용/롤백을 체크섬과 함께 기록.", "감사 추적.", [], [], ["evolve list"], trace=[], module="store.py"),
        ],
    },
    "watch": {
        "title": "Auto Build (워처)", "entry": "serve (toggles.auto_build) · watch [--interval]",
        "desc": "주기적으로 코퍼스를 stat 스캔해 변경이 있을 때만 증분 빌드. 매일 추가되는 문서를 자동 반영.",
        "stages": [
            _s("scan", "stat 스캔", "파일을 읽지 않고 mtime/size 만 비교 (3,000 파일 ≈ 수십 ms).", "변경 없으면 비용 거의 0.", ["auto_build"], ["auto_build_interval"], ["watch --once", "System 탭 '지금 스캔'"], trace=[], module="corpus.scan_changed"),
            _s("build_incremental", "증분 빌드", "변경/삭제 문서만 build 흐름으로.", "빌드 흐름 참조.", ["incremental", "stat_skip"], [], ["build"], trace=["build"], module="pipeline.build"),
        ],
    },
}


def registry() -> Dict[str, Any]:
    """UI/CLI 용 전체 레지스트리: flows + 단계별 tunables(자동 수집) + **페이즈(상위 단계)** + 토글/설정 설명.

    흐름마다 `phases` 를 함께 준다 — 각 항목은 {key, title, desc, stages:[단계 key…]} 이며 **단계 순서를 그대로 지킨다**
    (페이즈는 연속된 단계 묶음이라 순서를 바꾸지 않는다). 어느 페이즈에도 배치되지 않은 단계는 마지막 `(기타)` 로 모은다.
    """
    by_stage: Dict[str, List[Dict[str, Any]]] = {}
    for p in TUNABLES:
        by_stage.setdefault(p["stage"], []).append({"key": p["key"], "default": p["default"], "desc": p["desc"], "impact": p["impact"],
                                                    "source": p["source"], "rebuild": p["rebuild"]})
    flows = {}
    for fk, f in FLOWS.items():
        stages = []
        for st in f["stages"]:
            d = dict(st)
            d["tunables"] = by_stage.get(st["tuning_stage"], []) if st["tuning_stage"] else []
            d["phase"] = d.get("phase") or STAGE_PHASE.get(st["key"], "")
            stages.append(d)
        defined = {p["key"]: p for p in PHASES.get(fk, [])}
        order = [p["key"] for p in PHASES.get(fk, [])]
        groups: Dict[str, List[str]] = {}
        for st in stages:
            groups.setdefault(st["phase"] or "_other", []).append(st["key"])
        phases = [dict(defined[k], stages=groups[k]) for k in order if k in groups]
        if "_other" in groups:
            phases.append({"key": "_other", "title": "(기타)", "desc": "페이즈가 지정되지 않은 단계 — architecture.STAGE_PHASE 에 추가하세요.",
                           "stages": groups["_other"]})
        flows[fk] = dict(f, stages=stages, phases=phases)
    return {"flows": flows, "toggle_help": TOGGLE_HELP, "setting_help": SETTING_HELP, "tuning_stages": STAGES}


# ---------------------------------------------------------------------------
# 단계별 시간 제한 (2026-09-19)
# ---------------------------------------------------------------------------
# 화면에서 "이 단계가 7511 ms 걸렸다" 만 보면 그게 여유 있는 값인지 제한에 거의 닿은 값인지 알 수 없다.
# 그래서 실측 옆에 **그 단계를 끊을 수 있는 제한**을 같이 보여 준다. 여기는 그 제한의 단일 정의로,
# Web trace(core.js) · 🧭 Pipeline(pipeline.js) · CLI `arch limits` 가 모두 이 표를 쓴다.

# 단계 → 그 단계가 부르는 LLM 역할(들). 첫 번째가 그 단계의 대표 역할이다.
STAGE_ROLES: Dict[str, List[str]] = {
    # build
    "providers": ["extract", "summary"],
    "graph_build": ["extract", "summary"],
    "wiki_pages": ["summary"],
    # query
    "router": ["expand"],              # router_llm 은 expand 역할의 LLM 인스턴스를 쓴다
    "query_expand": ["expand"],
    "fusion_llm": ["fusion"],
    "rerank": ["rerank"],
    "rerank_review_llm": ["select"],
    "evidence": ["verify", "expand"],  # 충분성 판정(verify) + fallback 의 LLM 확장(expand)
    "answer": ["answer"],
    "claim": ["verify", "answer"],     # 판정(verify) + answer_refine(answer)
    "forensic": ["forensic"],
    "analysis": ["forensic"],
    # evolve
    "capture": ["review"],
}

# 단계 → LLM 이 아닌 제한. ("설정 키", 설명) 형태이며 config.json 에서 읽는다.
STAGE_OTHER_LIMITS: Dict[str, List[str]] = {
    "sync_index": ["db_busy_timeout_s"],
    "log": ["db_busy_timeout_s"],
    "chunk_index": ["db_busy_timeout_s"],
    "embed": ["db_busy_timeout_s"],
}

# 흐름 → 그 흐름 전체에 걸리는 제한. (server.json 의 키, config.json 의 키)
FLOW_LIMITS: Dict[str, List[str]] = {
    "query": ["timeouts.query_s"],
    "build": ["timeouts.job_s", "concurrency.write_wait_timeout_s", "build_lock_timeout", "build_lock_stale_s"],
    "evolve": ["timeouts.job_s"],
    "watch": ["timeouts.job_s", "build_lock_timeout", "build_lock_stale_s"],
}

_LIMIT_DESC = {
    "timeouts.query_s": ("질의 1건 전체", "server.json", "watchdog 이 넘기면 취소를 요청하고 지금까지의 결과로 답한다"),
    "timeouts.job_s": ("백그라운드 작업 1건 전체", "server.json", "빌드·평가·스냅샷. 0 = 제한 없음"),
    "timeouts.search_s": ("검색 API 1건", "server.json", ""),
    "timeouts.mcp_s": ("MCP 도구 호출 1건", "server.json", ""),
    "concurrency.write_wait_timeout_s": ("빌드가 시작을 기다리는 시간", "server.json", "진행 중 질의가 끝나기를 기다린다. 넘으면 503"),
    "build_lock_timeout": ("앞 빌드의 락을 기다리는 시간", "config.json", "0 = 즉시 실패"),
    "build_lock_stale_s": ("빌드 1회의 최대 수명", "config.json", "이 시간이 지난 락은 죽은 것으로 보고 회수한다"),
    "db_busy_timeout_s": ("SQLite 쓰기 잠금 대기", "config.json", "다른 프로세스가 쓰는 동안 기다리는 시간"),
}


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _dig(cfg: Dict[str, Any], dotted: str) -> Any:
    cur: Any = cfg or {}
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def stage_limits(s: Any, server_cfg: Dict[str, Any] = None) -> Dict[str, Any]:
    """단계마다 '이 단계를 끊을 수 있는 시간 제한' 을 근거(파일·키)와 함께 모은다.

    반환
      {"flows": {흐름: [제한…]},
       "stages": {단계 key: [제한…]},
       "trace":  {trace 노드 이름: [제한…]},      ← Web trace 가 실측 ms 옆에 바로 쓰는 표
       "roles":  {역할: {"timeout_s":…, "budget_s":…, "retries":…, "provider":…, "model":…}}}

    제한 하나 = {"s": 초, "label": 사람이 읽는 이름, "key": 설정 키, "file": 파일, "kind": 종류, "note": 설명}
    kind: llm(LLM 호출 1회) · llm_budget(재시도까지 합친 상한) · request(요청 전체) · job · lock · db
    s 가 0 이면 '제한 없음' 이다.
    """
    server_cfg = server_cfg or {}

    def cfg_limit(key: str, kind: str) -> Dict[str, Any]:
        label, fname, note = _LIMIT_DESC.get(key, (key, "config.json", ""))
        val = _dig(server_cfg, key) if "." in key else getattr(s, key, None)
        return {"s": _num(val), "label": label, "key": key, "file": fname, "kind": kind, "note": note}

    roles: Dict[str, Any] = {}
    for role in getattr(s, "LLM_ROLES", ()):
        pol = s.role_llm(role)
        ens = pol.get("ensemble") or {}
        roles[role] = {"timeout_s": _num(pol.get("timeout_s")), "budget_s": _num(pol.get("budget_s")),
                       "retries": int(_num(pol.get("retries"))), "provider": pol.get("provider"),
                       "model": pol.get("model"), "headless": str(pol.get("provider") or "") in ("headless", "agent", "opencode"),
                       # 앙상블 상태도 같이 준다 — 🧭 Pipeline 단계 상세가 Settings 와 같은 값을 보여 준다
                       "ensemble": {"enabled": bool(ens.get("enabled")),
                                    "members": [{"provider": m.get("provider"), "model": m.get("model"), "weight": m.get("weight")}
                                                for m in (ens.get("members") or [])],
                                    "aggregator": ens.get("aggregator") or {}, "prompt": ens.get("prompt"),
                                    "wait": ens.get("wait"), "timeout_s": ens.get("timeout_s"),
                                    "min_results": ens.get("min_results")}}

    def role_limits(role: str) -> List[Dict[str, Any]]:
        r = roles.get(role) or {}
        out = [{"s": r.get("timeout_s", 0), "label": "%s 역할 LLM 1회" % role, "key": "llm_roles.%s.timeout_s" % role,
                "file": "agents.json (headless)" if r.get("headless") else "config.json", "kind": "llm",
                "note": "비우면 llm_timeout(%ds). 재시도 %d회까지." % (int(_num(getattr(s, "llm_timeout", 0))), int(r.get("retries") or 0))}]
        if r.get("budget_s"):
            out.append({"s": r["budget_s"], "label": "%s 역할 재시도 포함 상한" % role, "key": "llm_roles.%s.budget_s" % role,
                        "file": "config.json", "kind": "llm_budget", "note": "재시도를 다 합쳐 이 시간을 넘기면 포기한다"})
        return out

    flows: Dict[str, List[Dict[str, Any]]] = {}
    for fk in FLOWS:
        kinds = {"timeouts.query_s": "request", "timeouts.job_s": "job"}
        flows[fk] = [cfg_limit(k, kinds.get(k, "lock")) for k in FLOW_LIMITS.get(fk, [])]

    stages: Dict[str, List[Dict[str, Any]]] = {}
    trace: Dict[str, List[Dict[str, Any]]] = {}
    for fk, f in FLOWS.items():
        for st in f["stages"]:
            lim: List[Dict[str, Any]] = []
            for role in STAGE_ROLES.get(st["key"], []):
                lim += role_limits(role)
            # 대표(첫 번째)는 '이 단계를 실제로 끊는' 값이어야 한다 → LLM 1회 → 흐름 전체 → 부수적인 잠금 대기 순.
            lim += flows.get(fk, [])
            for key in STAGE_OTHER_LIMITS.get(st["key"], []):
                lim.append(cfg_limit(key, "db"))
            stages[st["key"]] = lim
            # trace 노드 이름과 **단계 키 자체**를 모두 등록한다. 단계 키가 trace 목록에 없는 경우가 있는데
            # (예: 단계 answer 의 trace 는 answer_llm/answer_extractive), 화면이 단계 키로 물어볼 수 있다.
            for tn in list(st.get("trace") or []) + [st["key"]]:
                trace.setdefault(tn, lim)
    # trace 의 뿌리 노드(query/build/…) 는 흐름 전체 제한을 쓴다
    for fk in FLOWS:
        trace.setdefault(fk, flows.get(fk, []))
    return {"flows": flows, "stages": stages, "trace": trace, "roles": roles,
            # 단계 → 그 단계가 부르는 LLM 역할 (화면이 "이 단계의 모델·앙상블" 을 바로 그릴 수 있게)
            "stage_roles": {k: list(v) for k, v in STAGE_ROLES.items()}}


def render_limits(lim: Dict[str, Any]) -> str:
    """`arch limits` 의 텍스트 표 — Web 과 같은 값을 터미널에서 확인한다."""
    def fmt(v: float) -> str:
        v = float(v or 0)
        if v <= 0:
            return "제한 없음"
        if v >= 3600:
            return "%gh" % round(v / 3600.0, 2)
        if v >= 60:
            return "%gm" % round(v / 60.0, 2)
        return "%gs" % v
    out = ["== 역할별 LLM 1회 제한 (config.json llm_roles.<role>.timeout_s)"]
    out.append("   %-10s %-10s %-10s %-6s %s" % ("역할", "timeout", "budget", "retries", "provider/model"))
    for role, r in sorted(lim["roles"].items()):
        out.append("   %-10s %-10s %-10s %-6d %s/%s" % (role, fmt(r["timeout_s"]), fmt(r["budget_s"]),
                                                        r["retries"], r["provider"], r["model"]))
    for fk, rows in lim["flows"].items():
        out.append("")
        out.append("== 흐름 %s 전체" % fk)
        for x in rows:
            out.append("   %-36s %-12s %s · %s" % (x["key"], fmt(x["s"]), x["file"], x["note"] or "-"))
    out.append("")
    out.append("== 단계별 (대표 제한 1개)")
    for key, rows in lim["stages"].items():
        if rows:
            out.append("   %-20s %-12s %s" % (key, fmt(rows[0]["s"]), rows[0]["label"]))
    return "\n".join(out)


def render_text(reg: Dict[str, Any], toggles: Dict[str, bool]) -> str:
    """`arch show` 의 텍스트 지도 — 흐름 › **페이즈** › 단계 순서로 들여쓴다 (Web 🧭 Pipeline 과 같은 계층)."""
    lines = []
    for fk, f in reg["flows"].items():
        lines.append("== %s — %s" % (fk, f["title"]))
        lines.append("   entry: %s" % f["entry"])
        by_key = {s["key"]: s for s in f["stages"]}
        for ph in f.get("phases") or [{"key": "", "title": "", "desc": "", "stages": [s["key"] for s in f["stages"]]}]:
            if ph.get("title"):
                lines.append("   ┌ [%s] %s — %s" % (ph["key"], ph["title"], ph["desc"]))
            for sk in ph["stages"]:
                st = by_key.get(sk)
                if not st:
                    continue
                tg = ", ".join("%s%s" % (t, "" if toggles.get(t, True) else "(OFF)") for t in st["toggles"]) or "-"
                lines.append("   │ ▸ %-18s %s" % (st["key"], st["title"]))
                lines.append("   │     %s" % st["desc"])
                lines.append("   │     impact: %s" % st["impact"])
                lines.append("   │     toggles: %s | settings: %s | cli: %s | tunables: %d" % (tg, ", ".join(st["settings"]) or "-", ", ".join(st["cli"]) or "-", len(st["tunables"])))
    return "\n".join(lines)
