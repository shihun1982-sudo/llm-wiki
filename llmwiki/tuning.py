# -*- coding: utf-8 -*-
"""단계별 튜닝 파라미터 레지스트리 + tuning.json 로더.

- 검색/그래프/컨텍스트/답변 알고리즘의 상수를 모두 여기서 정의(기본값·범위·설명·impact·예시)하고,
  <프로젝트 루트>/tuning.json 에 적힌 값만 덮어쓴다 (없는 키는 기본값).
- source="config" 인 항목은 config.json(Settings) 에 값이 있는 기존 파라미터로, 문서/UI 에서 한 곳에 보여주기 위해 함께 등재한다.
- 사용: from .tuning import T ; T.get("fts_w_heading")   (Pipeline 이 load_tuning() 으로 갱신)
- 문서 생성: python -m llmwiki tuning doc  → docs/TUNING.md
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional

from .config import ROOT, path_for

TUNING_PATH = path_for("tuning")   # LLMWIKI_TUNING_PATH > LLMWIKI_TUNING(구) > <ROOT>/tuning.json

STAGES: Dict[str, str] = {
    "chunk_index": "청킹 · FTS 색인 (빌드)",
    "embed": "임베딩 (빌드)",
    "graph_build": "그래프 추출 · 커뮤니티 (빌드)",
    "time_scope": "시간 표현 해석 (질의)",
    "query_rules": "규칙 기반 질의 확장 (질의)",
    "router": "적응형 라우터 (질의)",
    "query_expand": "LLM 질의 확장 (질의, 옵션)",
    "fts_search": "FTS(BM25) 검색 + PRF (질의)",
    "vector_search": "벡터 검색 (질의)",
    "graph_search": "그래프 검색 (질의)",
    "rrf_fuse": "융합 · 부스트 (질의)",
    "rerank": "리랭크 (질의)",
    "context": "컨텍스트 구성 (질의)",
    "evidence": "근거 충분성 판정 · fallback 루프 (질의)",
    "answer": "답변 생성 (질의)",
    "claim": "답변 검증 (질의)",
    "forensic": "포렌식 · 자가진화 메모리",
}


def _p(key: str, stage: str, typ: str, default: Any, desc: str, impact: str, example: str, lo: Any = None, hi: Any = None,
       choices: Optional[List[Any]] = None, rebuild: bool = False, source: str = "tuning") -> Dict[str, Any]:
    return {"key": key, "stage": stage, "type": typ, "default": default, "min": lo, "max": hi, "choices": choices,
            "desc": desc, "impact": impact, "example": example, "rebuild": rebuild, "source": source}


TUNABLES: List[Dict[str, Any]] = [
    # ------------------------------------------------------------------ chunk_index
    _p("chunk_max_chars", "chunk_index", "int", 900, "청크 최대 글자 수 (헤딩 단위 섹션을 문단 경계로 분할).",
       "작을수록 검색 정밀도↑·컨텍스트 낭비↓ 이지만 문맥이 끊기고 청크 수·벡터 메모리↑. 클수록 recall↑, 정밀도↓.",
       "회의록처럼 결정사항이 짧으면 600, 논문/PDF 위주면 1200.", 200, 4000, rebuild=True, source="config"),
    _p("chunk_overlap_chars", "chunk_index", "int", 120, "인접 청크 간 겹치는 글자 수.",
       "문장이 경계에서 잘리는 손실을 줄이지만 중복 청크가 늘어 dedupe_hits 가 필요해짐.",
       "chunk_max_chars 의 10~15%. 0 이면 겹침 없음.", 0, 1000, rebuild=True, source="config"),
    _p("chunk_min_chars", "chunk_index", "int", 20, "이보다 짧은 조각은 청크로 만들지 않음.",
       "표 구분선·빈 헤딩 같은 잡음 청크를 제거. 너무 크면 짧은 결정사항이 사라짐.", "20 (기본). 표가 많은 CSV/HTML 이면 40.", 0, 200, rebuild=True),
    _p("tokenizer", "chunk_index", "choice", "heuristic", "한국어 토크나이저. heuristic = 조사 제거 + 문자 bigram + 복합어 사전(query_rules compound). kiwi = kiwipiepy 형태소 분석기(설치 시) 명사·외래어·숫자 추가. auto = kiwi 있으면 kiwi.",
       "kiwi 는 조사·어미 변형과 복합 명사에 강하지만 색인 시간↑. 색인과 질의가 같은 토크나이저를 써야 하므로 변경 시 전체 리빌드.", "heuristic (기본), kiwi (pip install kiwipiepy)",
       choices=["heuristic", "kiwi", "auto"], rebuild=True),
    _p("wiki_min_degree", "chunk_index", "int", 1, "위키 페이지를 만들 엔티티의 최소 연결 수. 대규모 코퍼스에서 페이지 수 억제.", "높이면 위키 파일 수↓.", "1 (소규모), 3 (수천 문서)", 0, 100),
    # ------------------------------------------------------------------ embed
    _p("embed_dim", "embed", "int", 4096, "hash 임베딩 차원 (외부 임베더는 무시).",
       "차원↑ 충돌↓ 정확도↑ 이지만 메모리 = 청크수×dim×4B. 3만 청크: 4096d≈500MB, 1024d≈125MB.", "소규모 4096, 3천 문서 이상 1024.",
       256, 16384, rebuild=True, source="config"),
    _p("embed_batch", "embed", "int", 64, "임베딩 배치 크기.", "API 임베더는 배치가 크면 요청 수↓ 지연↓, 너무 크면 타임아웃.", "hash 64~256, voyage 64, ollama 32.", 1, 512, source="config"),
    _p("hash_ngram_weight", "embed", "float", 0.5, "hash 임베딩에서 한글 문자 n-gram 특성 가중치 (단어 1.0 대비).",
       "높이면 표기 변형(띄어쓰기·조사)에 강해지고 낮추면 정확 단어 매칭에 가까워짐.", "한국어 비중 높으면 0.5~0.7, 영문 위주 0.3.", 0.0, 2.0, rebuild=True),
    # ------------------------------------------------------------------ graph_build
    _p("cooccur_window", "graph_build", "int", 8, "규칙 추출에서 공동출현 관계를 만들 때 한 엔티티가 보는 뒤쪽 엔티티 수.",
       "클수록 관계 수↑(그래프 밀도↑, 검색 시 확장 노드↑), 작을수록 희소.", "회의록 8, 긴 논문 5.", 1, 30, rebuild=True),
    _p("cooccur_scale", "graph_build", "float", 120.0, "공동출현 가중치 = min(1, cooccur_scale / 문자거리).",
       "클수록 멀리 떨어진 엔티티도 강하게 연결.", "120 (한 문장 정도). 문단 단위 연결을 원하면 300.", 10.0, 2000.0, rebuild=True),
    _p("cooccur_min_w", "graph_build", "float", 0.15, "이보다 작은 공동출현 가중치는 버림.",
       "높이면 약한 연결이 사라져 그래프 검색 잡음↓ recall↓.", "0.15 기본, 잡음이 많으면 0.3.", 0.0, 1.0, rebuild=True),
    _p("dates_per_chunk", "graph_build", "int", 8, "청크당 날짜 노드 최대 수.", "날짜 노드는 허브가 되기 쉬워 제한.", "8", 0, 50, rebuild=True),
    _p("amounts_per_chunk", "graph_build", "int", 6, "청크당 금액 노드 최대 수.", "위와 동일.", "6", 0, 50, rebuild=True),
    _p("community_iters", "graph_build", "int", 20, "label propagation 반복 횟수.",
       "많을수록 커뮤니티가 안정되지만 큰 그래프에서 시간↑. 보통 10회 안에 수렴.", "20", 1, 100, rebuild=True),
    _p("llm_known_entities", "graph_build", "int", 60, "LLM 추출 프롬프트에 넣는 '이미 알려진 엔티티' 수.",
       "많을수록 이름 정규화(같은 대상 같은 이름)↑, 입력 토큰↑.", "60. 토큰 절약 시 30.", 0, 300),
    _p("llm_graph_budget", "graph_build", "int", 0, "빌드당 LLM 추출 호출 상한 (0=무제한).", "일일 증분(20문서≈200청크)만 처리하도록 제한.", "250", 0, 100000, source="config"),
    _p("llm_graph_min_chars", "graph_build", "int", 80, "이보다 짧은 청크는 LLM 추출 생략.", "짧은 조각의 호출 낭비 방지.", "80", 0, 2000, source="config"),
    # ------------------------------------------------------------------ router
    _p("router_short_kw", "router", "int", 2, "키워드 수가 이 이하이면 'keyword' 질의로 분류 (FTS 가중↑).",
       "짧은 질의를 키워드 검색 위주로. 너무 크면 대부분이 keyword 로 분류돼 벡터/그래프가 약해짐.", "2", 0, 10),
    _p("router_long_kw", "router", "int", 5, "키워드 수가 이 이상이면 'semantic' 질의로 분류 (벡터 가중↑).", "긴 서술형 질문에 의미 검색 비중↑.", "5", 1, 30),
    _p("router_entity_min", "router", "float", 1.0, "엔티티 매칭 점수가 이 이상인 것만 '엔티티 있음' 으로 셈.", "낮추면 약한 FTS 매칭도 relational 판단에 포함.", "1.0", 0.0, 20.0),
    _p("router_strong_seed", "router", "float", 5.0, "정확 별칭 매칭(강한 시드) 판단 점수.", "강한 시드가 2개 이상이면 그래프 가중치를 FTS 와 동등하게.", "5.0", 0.0, 50.0),
    _p("router_base_graph", "router", "float", 0.7, "hybrid 질의의 기본 그래프 가중치 (FTS·벡터는 1.0).", "그래프 채널의 기본 영향력. 그래프가 잡음이면 낮춤.", "0.7", 0.0, 3.0),
    _p("router_kw_fts", "router", "float", 1.4, "keyword 질의의 FTS 가중치.", "", "1.4", 0.0, 3.0),
    _p("router_kw_vector", "router", "float", 0.8, "keyword 질의의 벡터 가중치.", "", "0.8", 0.0, 3.0),
    _p("router_kw_graph", "router", "float", 0.6, "keyword 질의의 그래프 가중치.", "", "0.6", 0.0, 3.0),
    _p("router_rel_graph", "router", "float", 0.85, "relational 질의(엔티티 2개↑ 또는 관계어)의 그래프 가중치.", "", "0.85", 0.0, 3.0),
    _p("router_rel_graph_strong", "router", "float", 1.0, "relational + 강한 시드 2개↑ 일 때 그래프 가중치.", "", "1.0", 0.0, 3.0),
    _p("router_rel_vector", "router", "float", 0.9, "relational 질의의 벡터 가중치.", "", "0.9", 0.0, 3.0),
    _p("router_num_fts_bonus", "router", "float", 0.3, "질의에 숫자가 있으면 FTS 가중치에 더함.", "날짜·금액 질문은 정확 매칭이 중요.", "0.3", 0.0, 2.0),
    _p("router_sem_vector_bonus", "router", "float", 0.3, "semantic 질의의 벡터 가중치 보너스.", "", "0.3", 0.0, 2.0),
    # ------------------------------------------------------------------ time_scope
    _p("time_mode", "time_scope", "choice", "boost", "시간 표현이 있을 때 날짜 범위를 boost(가산) 로 쓸지 filter(범위 밖 제외) 로 쓸지. filter 가 0건이면 boost 로 자동 완화.",
       "filter 는 정밀하지만 문서 날짜 메타가 없는 문서를 놓침. boost 는 안전.", "boost", choices=["boost", "filter"]),
    _p("time_boost_w", "time_scope", "float", 0.5, "날짜 범위 안 문서에 곱하는 부스트 (fused × (1+w)).", "", "0.5", 0.0, 3.0),
    _p("recency_half_life_days", "time_scope", "int", 0, "최신성 부스트 반감기(일). 0 이면 끔. 시간 표현이 없어도 최신 문서를 약간 우대.",
       "주간 보고·이슈처럼 최신이 중요한 코퍼스에서 유용. 설계 문서 위주면 0.", "0 (기본), 180.", 0, 3650),
    # ------------------------------------------------------------------ query_rules
    _p("syn_w", "query_rules", "float", 0.8, "synonym 확장 리스트 가중치 (원 질의 1.0 대비).", "", "0.8", 0.0, 2.0),
    _p("related_w", "query_rules", "float", 0.4, "related(관련어) 보조 리스트 가중치 — 주 질의에 섞지 않고 별도 리스트로 융합.", "높이면 관련 주제가 상위로 올라와 precision↓.", "0.4", 0.0, 2.0),
    _p("exclude_penalty", "query_rules", "float", 0.5, "exclude 용어를 포함한 후보의 fused 점수 배율 (0=완전 제거).", "", "0.5", 0.0, 1.0),
    # 2026-09-19 추가 유형 (llmwiki/query_rules.py 의 RULE_TYPES 레지스트리)
    _p("context_w", "query_rules", "float", 0.9, "context(문맥 의존) 확장의 치환 질의 가중치.",
       "문맥 조건이 맞을 때만 발화하므로 일반 synonym(syn_w 0.8)보다 **정확하다** → 조금 높게 둔다. 낮추면 문맥 확장이 순위에 덜 반영된다.", "0.9", 0.0, 2.0),
    _p("hypernym_down_w", "query_rules", "float", 0.35, "hypernym: 질의의 **상위어**로부터 하위어를 보조 리스트에 넣을 때의 가중치.",
       "'메모리 오류' 로 물었을 때 'DMA 오버런' 문서를 얼마나 끌어올릴지. 높이면 recall↑ precision↓ — related_w(0.4) 근처가 무난하다.", "0.35", 0.0, 2.0),
    _p("hypernym_up_w", "query_rules", "float", 0.2, "hypernym: 질의의 **하위어**로부터 상위어를 보조 리스트에 넣을 때의 가중치.",
       "상위어 문서는 대개 일반론이라 구체적 질문의 답이 아니다 → down 보다 낮게. 0 으로 두면 올라가기를 끈다.", "0.2", 0.0, 2.0),
    _p("acronym_phrase", "query_rules", "bool", True, "acronym 확장어를 구문(phrase) 검색으로 넣을지 (false 면 토큰 OR).", "", "true", choices=[True, False]),
    _p("query_rules_max_rounds", "query_rules", "int", 2,
       "규칙을 몇 번 접어 적용할지. 1 이면 한 번만 — 'TAT→Turn Around Time'(acronym) 뒤에 걸린 'Turn Around Time→응답시간'(synonym) 이 무시된다.",
       "크게 하면 사슬이 긴 사전에서 확장어가 폭증해 precision↓·지연↑. 연관어(related)와 별칭(alias)은 다시 펼치지 않는다.",
       "2 (기본), 3 (약어→정식명→동의어 사슬이 깊은 사전)", 1, 5),
    _p("related_symmetric", "query_rules", "bool", False,
       "related(연관어)를 양방향으로 쓸지. false(기본) 는 일방 — 키 A 가 질의에 있을 때만 값 B 를 보조 리스트에 넣는다. "
       "true 면 B 가 질의에 있을 때 A 도 보조 리스트에 넣는다 (acronym/synonym 은 원래 양방향, alias 는 늘 일방). `rules explain <용어>` 로 확인.",
       "켜면 recall↑ 이지만 연관어가 서로를 끌어와 precision↓. 동치라면 related 대신 acronym/synonym 에 넣는 것이 정답.",
       "false", choices=[True, False]),
    # ------------------------------------------------------------------ query_expand
    _p("query_expand_n", "query_expand", "int", 2, "생성할 대체 질의 수.", "많을수록 recall↑ 지연↑ (질의마다 FTS+벡터 실행).", "2", 1, 5),
    _p("query_expand_w", "query_expand", "float", 0.6, "대체 질의 결과 리스트의 융합 가중치 (원 질의 채널 가중치 대비 배율).", "1.0 이면 원 질의와 동등.", "0.6", 0.0, 2.0),
    _p("query_decompose_max", "query_expand", "int", 3, "분해 sub-query 최대 수.", "", "3", 1, 6),
    # ------------------------------------------------------------------ fts_search
    _p("top_k_fts", "fts_search", "int", 12, "FTS 후보 수.", "많을수록 recall↑, 융합/리랭크 비용↑.", "12 (소규모), 30 (수천 문서).", 1, 200, source="config"),
    _p("fts_mode", "fts_search", "choice", "tiered", "질의 토큰 결합 방식. tiered = 모든 키워드 AND 로 먼저 검색해 정밀 결과를 앞세우고 부족하면 OR 로 보충. or = OR 만.",
       "tiered 는 키워드가 모두 들어있는 문단을 최상위로 올려 MRR↑. 키워드가 많고 문서가 짧으면 AND 결과가 0 이라 OR 로 폴백.",
       "tiered (기본). 질문이 길고 서술형이면 or.", choices=["tiered", "or"]),
    _p("fts_and_min_hits", "fts_search", "int", 3, "tiered 모드에서 AND 결과가 이보다 적으면 OR 결과로 보충.", "", "3", 0, 50),
    _p("fts_w_heading", "fts_search", "float", 2.0, "BM25 헤딩 컬럼 가중치.", "헤딩(섹션 제목) 일치를 얼마나 중시할지. 회의록 '결정사항 > D1' 처럼 헤딩이 정보량이 크면 높게.", "2.0", 0.0, 10.0),
    _p("fts_w_body", "fts_search", "float", 1.0, "BM25 본문 컬럼 가중치.", "", "1.0", 0.0, 10.0),
    _p("fts_w_tokens", "fts_search", "float", 1.5, "BM25 정규화 토큰(조사 제거·bigram) 컬럼 가중치.", "한국어 조사 변형 매칭의 비중. 영문 위주면 낮춤.", "1.5", 0.0, 10.0),
    _p("fts_bigram_fallback", "fts_search", "bool", True, "결과가 없으면 조사 제거 + 문자 bigram 으로 재검색.", "오타·띄어쓰기 변형에 강해짐. 잡음 hit 가 생길 수 있음.", "true", choices=[True, False]),
    _p("fts_synonym_expand", "fts_search", "bool", True, "동의어 사전(자가진화 synonyms) 으로 질의 확장.", "", "true", choices=[True, False]),
    _p("fts_snippet_tokens", "fts_search", "int", 18, "스니펫 길이(토큰).", "UI 표시용.", "18", 5, 64),
    _p("prf_enabled", "fts_search", "bool", False, "PRF(pseudo-relevance feedback, RM3 식): 1차 FTS 상위 문단의 빈출 용어로 질의를 확장해 재검색.",
       "LLM 없이 recall↑ (어휘 불일치 완화). 1차 결과가 틀리면 잘못된 방향으로 확장(query drift) 위험 → 상위 문단 수를 작게.",
       "true, prf_docs 3, prf_terms 4. 도메인 용어가 많은 기술 문서에서 효과.", choices=[True, False]),
    _p("prf_docs", "fts_search", "int", 3, "PRF 가 참조할 상위 문단 수.", "", "3", 1, 10),
    _p("prf_terms", "fts_search", "int", 4, "PRF 로 추가할 용어 수.", "", "4", 1, 20),
    # ------------------------------------------------------------------ vector_search
    _p("top_k_vector", "vector_search", "int", 12, "벡터 후보 수.", "", "12", 1, 200, source="config"),
    _p("vector_min_sim", "vector_search", "float", 0.0, "이 코사인 유사도 이하의 후보는 버림.",
       "hash 임베딩은 0.1~0.6 범위. 낮은 유사도 잡음을 잘라 융합 품질↑. 너무 높으면 벡터 채널이 비어 버림.", "hash 0.05, voyage 0.3.", -1.0, 1.0),
    # ------------------------------------------------------------------ graph_search
    _p("top_k_graph", "graph_search", "int", 12, "그래프 후보 수.", "", "12", 1, 200, source="config"),
    _p("graph_hops", "graph_search", "int", 2, "시드 엔티티에서 확장할 홉 수.", "2 면 A-B-C 다중 홉 근거 수집. 3 이상은 허브 폭증·지연↑.", "2", 1, 4, source="config"),
    _p("graph_seed_min", "graph_search", "float", 0.5, "시드로 쓸 최소 엔티티 매칭 점수.", "낮추면 약한 매칭도 시드가 되어 recall↑ 잡음↑.", "0.5", 0.0, 20.0),
    _p("graph_max_seeds", "graph_search", "int", 6, "시드 엔티티 최대 수.", "", "6", 1, 30),
    _p("graph_decay", "graph_search", "float", 0.5, "홉마다 점수 감쇠 (decay^hop).", "작을수록 먼 노드 영향↓.", "0.5", 0.05, 1.0),
    _p("graph_hub_exp", "graph_search", "float", 0.5, "허브 페널티 지수: gain /= degree^exp.", "0 이면 페널티 없음(문서/역할 노드가 모든 것을 연결), 1 이면 강한 페널티.", "0.5", 0.0, 2.0),
    _p("graph_frontier", "graph_search", "int", 60, "홉당 확장 노드 상한.", "지연과 recall 의 트레이드오프.", "60", 5, 500),
    _p("graph_revisit_factor", "graph_search", "float", 0.3, "이미 방문한 노드에 더해지는 gain 배율.", "", "0.3", 0.0, 1.0),
    _p("graph_seed_chunk_w", "graph_search", "float", 2.0, "시드 엔티티가 직접 언급된 청크 점수 배율 (확장 노드 청크 = 1.0).", "높이면 시드 근거 문단 우선, 낮추면 다중 홉 문단도 부상.", "2.0", 0.0, 10.0),
    _p("graph_top_entities", "graph_search", "int", 30, "확장 후 청크 수집에 쓰는 상위 엔티티 수.", "", "30", 5, 200),
    _p("graph_rel_bonus_n", "graph_search", "int", 40, "관계 근거 청크에 순위 보너스를 주는 상위 관계 수.", "", "40", 0, 200),
    _p("graph_cover_w", "graph_search", "float", 2.0, "이중 검색 재가중: 질의 키워드 커버리지 가산 배율 (score*(base+cover) + cover*w).", "키워드가 실제로 들어있는 문단을 앞세움(LightRAG low-level).", "2.0", 0.0, 10.0),
    _p("graph_cover_base", "graph_search", "float", 0.3, "커버리지 0 인 문단이 유지하는 점수 비율.", "0 이면 키워드 없는 문단 완전 제거.", "0.3", 0.0, 1.0),
    _p("provenance_w", "graph_search", "str", "explicit:1.0,rule:0.9,human:0.9,llm:0.6,cooccur:0.35",
       "관계 출처(provenance)별 확장 gain 배율. explicit=front matter 명시, rule=ID/정규식 규칙, human=승인된 제안, llm=LLM 추출, cooccur=공동출현.",
       "결정적 관계(CL→Issue)를 공동출현보다 강하게 따라감. cooccur 를 0 으로 두면 사전 엔티티 공동출현 확장이 사라짐.", "explicit:1.0,rule:0.9,human:0.9,llm:0.6,cooccur:0.35"),
    _p("graph_doc_refs_n", "graph_search", "int", 3, "확장 상위 엔티티마다 doc_refs 로 추가할 문서 수 (문서의 첫 청크/최다 언급 청크를 후보로). 0 이면 끔.",
       "ID 노드(ISSUE-2041)의 원본 문서를 바로 후보에 올려 문서 단위 검색 품질↑.", "3", 0, 20),
    # ------------------------------------------------------------------ rrf_fuse
    _p("rrf_k", "rrf_fuse", "int", 60, "RRF 상수 (1/(k+rank)).", "작을수록 1위 편중, 클수록 순위 차이가 완만.", "60", 1, 1000, source="config"),
    _p("fusion_method", "rrf_fuse", "choice", "rrf", "융합 방식. rrf = 순위 기반(점수 척도 무관, 안정적). weighted/minmax = 채널 점수 min-max 정규화 가중합. zscore = 채널별 z-정규화 가중합. dbsf = 3σ 정규화(분포 기반). rrf_boost = rrf + 후보 점수 소량 반영.",
       "rrf 는 채널별 점수 척도가 달라도 안전(OpenSearch 벤치: 튜닝된 정규화 대비 NDCG -3~4%, 강건). 정규화 계열은 한 채널이 압도적으로 확신할 때 그 결과를 살리지만 outlier 에 민감. `fusion compare` 로 평가셋에서 비교.",
       "rrf (기본). 의미 임베더 사용 시 zscore/dbsf 실험.", choices=["rrf", "weighted", "minmax", "zscore", "dbsf", "rrf_boost"]),
    _p("fusion_multi_bonus", "rrf_fuse", "float", 0.0, "2개 이상 채널에 동시에 나온 후보에 더하는 보너스 (fused 점수 단위, rrf 는 ~0.01 스케일).",
       "채널 합의(consensus)를 직접 보상. 리랭크 local 의 consensus 와 중복될 수 있음.", "0.005", 0.0, 1.0),
    _p("channel_w_fts", "rrf_fuse", "float", 1.0, "사용자 채널 가중치 배율(FTS) — 라우터 가중치에 곱함.", "특정 채널을 전역적으로 강/약화.", "1.0", 0.0, 3.0),
    _p("channel_w_vector", "rrf_fuse", "float", 1.0, "사용자 채널 가중치 배율(벡터).", "", "1.0", 0.0, 3.0),
    _p("channel_w_graph", "rrf_fuse", "float", 1.0, "사용자 채널 가중치 배율(그래프).", "", "1.0", 0.0, 3.0),
    _p("channel_w_doc_vector", "rrf_fuse", "float", 0.7, "문서 카드 벡터 채널 가중치 배율.", "", "0.7", 0.0, 3.0),
    _p("channel_w_external", "rrf_fuse", "float", 1.0, "외부 RAG 채널(ext_<source>, external_rag 토글) 가중치 배율. 소스별 weight(mcp_sources.json retrieve.weight) 와 곱한다.",
       "외부 결과를 내부 채널보다 앞세우려면 >1, 참고용이면 0.5 이하.", "1.0", 0.0, 3.0),
    _p("external_rag_k", "rrf_fuse", "int", 5, "외부 RAG 소스마다 요청할 결과 수 (retrieve.args 의 {k}). fallback 라운드에서는 k_mult 배.", "많을수록 외부 지연·토큰↑.", "5", 1, 50),
    _p("external_rag_inject", "rrf_fuse", "int", 2, "외부 소스별 상위 n개 결과를 (RRF 순위와 무관하게) 리랭크 후보 창에 보장 주입. 외부 채널은 리스트가 하나뿐이라 내부 리스트 여러 개(fts/alt/vector…)와 RRF 로 경쟁하면 후보 밖으로 밀리기 쉬우므로, 최종 판단은 리랭커에 맡긴다.",
       "0 이면 순수 RRF 경쟁. 크면 외부 결과가 항상 리랭크를 받는다(리랭크 비용↑).", "2", 0, 20),
    # 채널별 top-k 구간 가중 (docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md §2.2): 채널 c 의 순위 r 에 w_c × (topk_w if r ≤ topk_n else tail_w).
    # 보조 리스트(fts_rule/fts_alt1/vector_alt2/fts_rel1/ext_<src>)는 이름 앞부분의 기본 채널 값을 물려받는다. 적용 배율은 Hit.boosts["topk_<채널>"].
    _p("fts_topk_n", "rrf_fuse", "int", 0, "FTS 채널에서 'top-k 안' 으로 볼 순위 (0 = 구간 가중 끔). 이 순위까지는 fts_topk_w, 밖은 fts_tail_w 를 채널 가중치에 곱한다.",
       "FTS 상위만 믿고 싶을 때(정확 매칭이 강한 코퍼스) n 을 작게·topk_w 를 크게. 보조 리스트(fts_rule/fts_alt/fts_rel)에도 같은 값이 적용된다.", "0 (기본), 5", 0, 200),
    _p("fts_topk_w", "rrf_fuse", "float", 1.0, "FTS 채널 top-k 안 후보의 채널 가중 배율.", "1.5 면 상위 n개가 RRF 합산에서 1.5배. 1.0 이면 변화 없음.", "1.0 (기본), 1.5", 0.0, 5.0),
    _p("fts_tail_w", "rrf_fuse", "float", 1.0, "FTS 채널 top-k 밖 후보의 배율 (0 = 밖은 후보에서 버림).",
       "0.5 면 하위 후보의 기여가 절반. 0 이면 그 채널의 하위 후보는 융합에 참여하지 않는다(다른 채널에서 나오면 살아남는다).", "1.0 (기본), 0.5", 0.0, 5.0),
    _p("vector_topk_n", "rrf_fuse", "int", 0, "벡터 채널에서 'top-k 안' 으로 볼 순위 (0 = 끔). vector_alt 리스트에도 적용.", "의미 임베더의 상위 결과가 신뢰도가 높을 때 상위를 우대.", "0 (기본), 5", 0, 200),
    _p("vector_topk_w", "rrf_fuse", "float", 1.0, "벡터 채널 top-k 안 후보의 배율.", "", "1.0", 0.0, 5.0),
    _p("vector_tail_w", "rrf_fuse", "float", 1.0, "벡터 채널 top-k 밖 후보의 배율 (0 = 버림).", "hash 임베딩처럼 하위 순위가 잡음이면 0.5 이하.", "1.0", 0.0, 5.0),
    _p("graph_topk_n", "rrf_fuse", "int", 0, "그래프 채널에서 'top-k 안' 으로 볼 순위 (0 = 끔).", "시드 직결 청크(상위)와 다중 홉 청크(하위)를 다르게 대접할 때.", "0 (기본), 3", 0, 200),
    _p("graph_topk_w", "rrf_fuse", "float", 1.0, "그래프 채널 top-k 안 후보의 배율.", "", "1.0", 0.0, 5.0),
    _p("graph_tail_w", "rrf_fuse", "float", 1.0, "그래프 채널 top-k 밖 후보의 배율 (0 = 버림).", "", "1.0", 0.0, 5.0),
    _p("doc_vector_topk_n", "rrf_fuse", "int", 0, "문서 카드 벡터 채널에서 'top-k 안' 으로 볼 순위 (0 = 끔).", "", "0 (기본), 3", 0, 200),
    _p("doc_vector_topk_w", "rrf_fuse", "float", 1.0, "문서 카드 벡터 채널 top-k 안 후보의 배율.", "", "1.0", 0.0, 5.0),
    _p("doc_vector_tail_w", "rrf_fuse", "float", 1.0, "문서 카드 벡터 채널 top-k 밖 후보의 배율 (0 = 버림).", "", "1.0", 0.0, 5.0),
    _p("external_topk_n", "rrf_fuse", "int", 0, "외부 RAG 채널(모든 ext_<source>)에서 'top-k 안' 으로 볼 순위 (0 = 끔).", "외부 소스의 상위 결과만 신뢰할 때. 소스마다 따로 세지 않고 각 ext_ 리스트에 같은 n 을 적용한다.", "0 (기본), 2", 0, 200),
    _p("external_topk_w", "rrf_fuse", "float", 1.0, "외부 RAG 채널 top-k 안 후보의 배율.", "", "1.0", 0.0, 5.0),
    _p("external_tail_w", "rrf_fuse", "float", 1.0, "외부 RAG 채널 top-k 밖 후보의 배율 (0 = 버림).", "", "1.0", 0.0, 5.0),
    _p("channel_inject", "rrf_fuse", "str", "", "채널별 리랭크 창 보장 주입 수 `fts:2,vector:2,graph:1` (채널: fts | vector | graph | doc_vector | external). "
       "그 채널 주 리스트의 상위 n개를 RRF 순위와 무관하게 리랭크 후보 창 안으로 올린다(창 끝 요소 바로 위의 fused, why=inject:<채널>). external_rag_inject 와 같은 방식.",
       "'벡터 1위인데 리랭크 후보에도 못 들었다' 를 막는다. 최종 순위는 리랭커가 정하므로 부작용은 리랭크 후보 수 증가뿐.", "\"\" (기본), fts:2,vector:2,graph:1"),
    _p("doc_type_boost", "rrf_fuse", "str", "", "문서 유형 부스트 맵 `issue:1.2,cl:1.1` (fused × 값). 라우터가 힌트를 잡으면 해당 유형 추가 ×1.2.",
       "질문 유형과 문서 유형이 맞을 때 상위로.", "issue:1.2,coding_rule:1.1"),
    _p("pin_boost", "rrf_fuse", "float", 10.0, "pin 된 청크의 fused 점수 배율 (사실상 최상위 고정).", "", "10.0", 1.0, 100.0),
    _p("provenance_boost", "rrf_fuse", "float", 0.2, "그래프 후보 중 explicit/rule 관계로 도달한 청크의 추가 배율(1+w).", "", "0.2", 0.0, 2.0),
    _p("feedback_boost_w", "rrf_fuse", "float", 0.15, "긍정 피드백 청크 부스트 최대 배율(1+w×strength).", "", "0.15", 0.0, 1.0),
    _p("fusion_llm_candidates", "rrf_fuse", "int", 0, "융합 뒤 LLM 검토(토글 llm_after_fusion, 역할 fusion, prompts/fusion_review.md)에 보낼 상위 후보 수. 0 = rerank_candidates 와 같게.",
       "많을수록 프롬프트(후보 × rerank_chunk_chars)와 토큰↑, 검토 범위↑. 리랭크 후보 수보다 크게 두면 리랭크 창 밖 후보까지 걸러 준다.", "0 (=rerank_candidates), 24", 0, 100),
    _p("fusion_llm_drop_penalty", "rrf_fuse", "float", 0.3, "융합 뒤 LLM 검토가 drop 으로 고른 후보의 fused 점수 배율. 0 이면 후보에서 제거한다(why=llm_drop).",
       "0.3 은 감점만 하므로 LLM 이 틀려도 리랭크가 되살릴 수 있다. 0 은 확실히 제거하지만 LLM 오판이 그대로 결과가 된다.", "0.3 (기본), 0 (제거)", 0.0, 1.0),
    # ------------------------------------------------------------------ rerank
    _p("rerank_candidates", "rerank", "int", 16, "리랭크 후보 수.", "", "16", 1, 100, source="config"),
    _p("rerank_chunk_chars", "rerank", "int", 600, "LLM/크로스인코더 입력 청크 글자 수.", "", "600", 100, 4000, source="config"),
    _p("rerank_method", "rerank", "choice", "auto", "리랭크 방식. auto = rerank_url 이 있으면 api, 아니면 LLM 가능 시 LLM(rerank_llm 토글), 아니면 local. api = 전용 rerank 엔드포인트(Cohere/Jina/vLLM/Voyage). cross_encoder = sentence-transformers CrossEncoder(로컬). llm = LLM 순위. local = 휴리스틱.",
       "api/cross_encoder 는 토큰 0·품질 높음(bge-reranker-v2-m3 등 다국어). 실패 시 local 폴백.",
       "api + rerank_url http://host:8000/v1/rerank", choices=["auto", "api", "llm", "cross_encoder", "local"]),
    _p("rerank_ce_model", "rerank", "str", "BAAI/bge-reranker-v2-m3", "크로스인코더 모델명 (sentence-transformers CrossEncoder).", "", "BAAI/bge-reranker-v2-m3 (다국어), cross-encoder/ms-marco-MiniLM-L-6-v2 (영문 경량)"),
    _p("rerank_w_cover", "rerank", "float", 0.6, "local 리랭크: 키워드 커버리지 가중치.", "", "0.6", 0.0, 1.0),
    _p("rerank_w_consensus", "rerank", "float", 0.3, "local 리랭크: 채널 합의(등장 채널 수/3) 가중치.", "", "0.3", 0.0, 1.0),
    _p("rerank_w_length", "rerank", "float", 0.1, "local 리랭크: 길이 보정(400자 미만 감점) 가중치.", "", "0.1", 0.0, 1.0),
    _p("rerank_fused_w", "rerank", "float", 0.0,
       "리랭크 최종 순위에 **융합·부스트 점수(fused)** 를 얼마나 섞을지. 0 = 리랭크 점수만으로 정렬(기본, 예전 동작).",
       "리랭크는 후보를 다시 줄 세우면서 그 앞 단계(채널 융합 rrf_k·채널 가중치·시간/문서유형/pin/provenance 부스트)가 "
       "매긴 점수를 **버린다**(동점일 때만 참고). 그래서 rrf_k·fusion_method 를 아무리 바꿔도 최종 순위가 거의 그대로다 "
       "(2026-09-17 실측: rrf_k 2~60 에서 MRR 0.467~0.477). 이 값을 올리면 융합·부스트가 최종 순위에 실제로 반영되어 "
       "그 손잡이들이 의미를 갖는다. 특히 **pin_boost 로 고정한 근거가 리랭크에 밀리는 것**을 막을 때 쓴다. "
       "리랭크 점수와 fused 는 척도가 달라 후보 집합 안에서 각각 0~1 로 정규화한 뒤 `rerank + w × fused` 로 합친다.",
       "0 = 리랭크만 믿는다(기본) · 0.3 = 융합을 참고 · 1.0 = 리랭크와 같은 비중 · pin 을 확실히 올리려면 1.0 이상",
       0.0, 5.0),
    _p("rerank_heading_bonus", "rerank", "float", 0.1, "local 리랭크: 헤딩에 질의 키워드가 있으면 더하는 보너스.",
       "섹션 제목이 곧 주제인 문서(회의록 결정사항)에서 MRR↑ (실습 코퍼스 all 채널 MRR 0.743→0.799). 단, 제목만 맞고 본문에 답이 없는 문단(일정표)이 올라올 수 있어 단일 채널 평가에서는 1문항 하락 — 0 으로 끄면 원복.",
       "0.1 (기본). 헤딩이 빈약한 PDF 위주 코퍼스면 0.", 0.0, 1.0),
    _p("post_rerank_llm_k", "rerank", "int", 0, "리랭크 뒤 LLM 선택(토글 llm_after_rerank, 역할 select, prompts/rerank_review.md)에 보낼 리랭크 상위 후보 수. 0 = top_k_final × 2.",
       "LLM 이 이 안에서 컨텍스트에 넣을 청크(select)와 통째로 읽을 문서(expand_docs)를 고른다. 선택된 청크 수는 top_k_final 에 매이지 않는다(컨텍스트 상한 context_max_chars 로만 제한).",
       "0 (=top_k_final×2), 12", 0, 100),
    # ------------------------------------------------------------------ context
    _p("top_k_final", "context", "int", 8, "최종 컨텍스트 후보 수.", "많을수록 근거↑ 토큰↑.", "8", 1, 50, source="config"),
    _p("context_max_chars", "context", "int", 9000, "컨텍스트 총 글자 상한.", "≈ 토큰/3.", "9000", 500, 200000, source="config"),
    _p("context_chunk_chars", "context", "int", 1200, "context_trim 시 청크당 글자 상한.", "", "1200", 100, 20000, source="config"),
    # 2026-09-19: context_max_chars 는 사람이 정한 값이라 모델의 입력 창과 무관했다. 창이 작은 모델에 긴 컨텍스트를
    # 넣으면 프롬프트가 조용히 잘려 뒤쪽 근거가 통째로 사라진다. 아래 두 값으로 models.json 의 context_k 를 실제 상한에 연결한다.
    _p("context_chars_per_token", "context", "float", 2.0, "모델 창(토큰)을 글자 수로 바꿀 때 쓰는 비율 (글자/토큰).",
       "낮출수록 토큰을 넉넉히 잡아 **덜 넣는다**(안전). 한글·혼합 문서는 2.0, 영문 위주 코퍼스는 3~4 로 올려 더 넣을 수 있다.", "2.0", 1.0, 6.0),
    _p("context_budget_reserve_tokens", "context", "int", 2000, "모델 창에서 컨텍스트 말고 다른 것(시스템 프롬프트·질문·형식 지시)에 남겨 두는 토큰.",
       "프롬프트를 길게 고쳤거나 질문이 긴 편이면 키운다. 컨텍스트 상한이 그만큼 줄어든다.", "2000", 0, 32000),
    _p("context_min_fit_chars", "context", "int", 400, "컨텍스트 상한에 걸린 근거를 **잘라서라도 넣을** 최소 남은 공간(글자).",
       "예전에는 큰 청크 하나가 상한을 넘으면 거기서 **멈춰** 뒤 순위 근거가 통째로 빠졌다. 이제 남은 공간이 이 값 이상이면 잘라서 넣고, 아니면 건너뛰고 다음 후보를 계속 본다. 0 = 자르지 않고 건너뛰기만.", "400", 0, 5000),
    _p("context_neighbors", "context", "int", 0, "상위 context_neighbor_top 개 청크의 앞/뒤 인접 청크를 n개씩 추가(같은 문서).",
       "표·목록이 청크 경계에서 잘린 경우 답변 완성도↑. 토큰↑.", "1 (상위 2개 청크의 앞뒤 1개씩).", 0, 3),
    _p("context_neighbor_top", "context", "int", 2, "인접 청크를 붙일 상위 청크 수.", "", "2", 1, 10),
    _p("dedupe_similarity", "context", "float", 0.85, "dedupe_hits 시 토큰 Jaccard 유사도가 이 이상인 문단(다른 문서 포함)을 중복으로 제거.",
       "일정표와 회의록에 같은 문장이 반복되는 코퍼스에서 토큰 절약. 너무 낮으면 관련 문단이 사라짐.", "0.85", 0.5, 1.0),
    _p("context_graph_relations", "context", "int", 15, "컨텍스트 끝에 붙이는 그래프 관계 수.", "관계 텍스트는 다중 홉 답변에 도움, 토큰↑.", "15", 0, 100),
    _p("doc_expand_top_docs", "context", "int", 3, "문서 단위 확장(doc_expand 토글): 리랭크 상위 청크가 속한 문서 중 앞에서 몇 개 문서를 확장할지.",
       "많을수록 여러 문서의 보조 청크가 들어와 근거 완전성↑ 토큰↑.", "3 (기본). 단일 문서 질문이 많으면 1~2.", 1, 20),
    _p("doc_expand_max_chunks", "context", "int", 3, "문서 단위 확장: 문서당 추가할 최대 청크 수.", "문서 전체를 넣으려면 크게 (컨텍스트 상한 context_max_chars 는 그대로 적용).", "3", 1, 50),
    _p("doc_expand_min_score", "context", "float", 0.2, "문서 단위 확장: 이 점수(0~1) 이상인 청크만 추가. 점수 = 키워드 커버리지·벡터 유사도(모드별).",
       "낮추면 관련 없는 청크까지 들어와 토큰 낭비, 높이면 확장이 거의 안 됨. 0 이면 상한까지 무조건 추가.", "0.2", 0.0, 1.0),
    _p("doc_expand_mode", "context", "choice", "hybrid", "문서 단위 확장 점수 방식: keyword(질의 키워드 커버리지) | vector(질의-청크 코사인, 부모 청크 대비 정규화) | hybrid(가중합) | full(근거가 나온 문서를 통째로 — 점수로 거르지 않고 문서 순서대로, doc_expand_max_chunks 와 context_max_chars 로만 제한).",
       "hash 임베더에서는 keyword 비중이 안전. 의미 임베더면 vector/hybrid. full 은 근거 문서 전체를 읽히므로 품질↑·토큰↑ (doc_expand_max_chunks 를 함께 키운다).",
       "hybrid", choices=["keyword", "vector", "hybrid", "full"]),
    _p("doc_expand_w", "context", "float", 0.5, "hybrid 모드에서 벡터 점수 가중 (키워드는 1-w).", "", "0.5", 0.0, 1.0),
    # ------------------------------------------------------------------ evidence / fallback
    _p("evidence_min_score", "evidence", "float", 0.015, "충분성 휴리스틱: 상위 fused 점수가 이 미만이면 weak.", "rrf 스케일(1/(60+r)): 단일 채널 1위 ≈0.0164, 채널 2개 합의 ≈0.03. 0.02 로 올리면 단일 채널 근거는 모두 weak.", "0.015", 0.0, 1.0),
    _p("evidence_min_channels", "evidence", "int", 1, "충분성 휴리스틱: 상위 후보가 등장한 채널 수가 이 미만이면 weak.", "2 로 올리면 채널 합의를 요구.", "1", 0, 4),
    _p("evidence_min_cover", "evidence", "float", 0.5, "충분성 휴리스틱: 컨텍스트가 질의 키워드를 이 비율 미만으로 커버하면 insufficient (1.0 미만이면 weak).", "", "0.5", 0.0, 1.0),
    _p("evidence_min_chars", "evidence", "int", 200, "컨텍스트 글자수가 이 미만이면 weak.", "", "200", 0, 5000),
    _p("fallback_max_attempts", "evidence", "int", 2, "fallback 루프 최대 재시도 횟수 (L1→L4 순으로 1회씩).", "over-retrieval 방지 (에이전틱 RAG 최대 실패 원인).", "2", 0, 6),
    _p("fallback_token_budget", "evidence", "int", 20000, "fallback 루프에서 쓸 수 있는 LLM 토큰 총량.", "", "20000", 0, 500000),
    _p("fallback_latency_ms", "evidence", "int", 30000, "fallback 루프 전체 지연 상한(ms).", "", "30000", 0, 600000),
    _p("fallback_widen_factor", "evidence", "float", 2.0, "fallback 라운드마다 top_k 를 곱하는 배율.", "", "2.0", 1.0, 5.0),
    _p("fallback_levels", "evidence", "str", "rules,expand,graph,wide", "fallback 단계 순서 (rules | expand | graph | wide | mcp).", "값싼 단계부터. mcp 는 mcp_sources 토글 필요.", "rules,expand,graph,wide"),
    # ------------------------------------------------------------------ answer
    _p("answer_length_target", "answer", "choice", "normal", "답변 상세도 목표: short(핵심만) | normal | long(근거 전체를 상세 설명). 프롬프트에 반영.",
       "long 은 evidence-rich 답변, 토큰↑.", "normal", choices=["short", "normal", "long"]),
    _p("answer_max_tokens", "answer", "int", 3000, "답변 LLM 출력 토큰 상한.", "", "3000", 100, 32000, source="config"),
    _p("answer_repeat_guard", "answer", "bool", True, "답변이 같은 구절을 무한 반복하는 LLM 고장(반복 루프)을 잡아 잘라내고 경고를 붙인다.",
       "끄면 반복된 답변이 그대로 나가고 캐시에도 저장된다.", "true"),
    _p("answer_repeat_min_chars", "answer", "int", 12, "반복으로 판정할 최소 구절 길이(글자).", "너무 작으면 정상적인 짧은 반복도 잡는다.", "12", 4, 400),
    _p("answer_repeat_times", "answer", "int", 4, "같은 구절이 연속으로 이 횟수 이상 나오면 반복 루프로 본다.", "표·목록에는 정상적인 반복이 있으므로 3 미만은 권하지 않는다.", "4", 3, 50),
    _p("answer_effort", "answer", "choice", "medium", "답변 LLM effort.", "high 는 추론↑ 지연·비용↑.", "medium", choices=["low", "medium", "high"], source="config"),
    _p("llm_effort", "answer", "choice", "low", "추출/리랭크/요약/리뷰 LLM effort (역할별 llm_roles 로 개별 지정 가능).", "", "low", choices=["low", "medium", "high"], source="config"),
    _p("extractive_sentences", "answer", "int", 6, "추출식 답변 문장 수.", "", "6", 1, 30),
    _p("extractive_min_len", "answer", "int", 15, "추출식 답변 후보 문장 최소 길이.", "", "15", 1, 200),
    _p("extractive_max_len", "answer", "int", 400, "추출식 답변 후보 문장 최대 길이.", "", "400", 20, 2000),
    _p("refs_preview_chars", "answer", "int", 200, "결과 refs(LLM 에 실제로 전달된 근거 목록)의 항목마다 붙이는 본문 미리보기 글자 수. CLI --json · Web REF 목록 · MCP structuredContent.refs 에 같은 값.",
       "화면·응답 크기에만 영향. 답변 품질과 무관.", "200 (기본), 0 (미리보기 없음)", 0, 2000),
    # 출력 모드 (config.json output_mode ≠ answer 일 때만 의미, docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md §2.3)
    _p("output_candidates_n", "answer", "int", 0, "output_mode=fused|reranked 에서 응답 candidates[] 에 담을 후보 수. 0 = rerank_candidates 와 같게.",
       "output_mode=answer 에는 영향 없음. 크게 두면 리랭크 창 밖 후보(리랭크 점수 없음)까지 보인다.", "0 (=rerank_candidates), 30", 0, 500),
    _p("output_list_n", "answer", "int", 20, "output_mode=fused|reranked 에서 응답 lists{채널: [(chunk_id, score)]} 에 담을 채널별 상위 개수.",
       "화면·응답 크기에만 영향.", "20", 0, 500),
    _p("output_chunk_chars", "answer", "int", 0, "output_mode=fused|reranked 에서 candidates[].text 를 이 글자 수로 자른다. 0 = 전문.",
       "응답 크기 제어. MCP 로 붙는 LLM 에 넘길 때 300~600.", "0 (전문), 400", 0, 20000),
    # ------------------------------------------------------------------ claim
    _p("claim_support_min", "claim", "float", 0.5, "휴리스틱 claim 검증: 문장의 핵심 토큰(키워드·수치·ID) 중 인용 근거에 있는 비율이 이 미만이면 unsupported.", "", "0.5", 0.0, 1.0),
    _p("claim_policy", "claim", "choice", "mark", "미지원 문장 처리: mark(문장 끝에 [미확인] 표기) | drop(제거) | refine(answer_refine 토글 시 LLM 재작성, 아니면 mark).",
       "drop 은 답변이 짧아질 수 있음.", "mark", choices=["mark", "drop", "refine"]),
    _p("claim_min_groundedness", "claim", "float", 0.6, "groundedness 가 이 미만이면 답변 상단에 경고 + 포렌식 자동 기록.", "", "0.6", 0.0, 1.0),
    # ------------------------------------------------------------------ forensic / memory
    _p("memory_half_life_days", "forensic", "int", 60, "제안·규칙·pin strength 반감기(일). 재사용/긍정 피드백 시 strength 강화.", "", "60", 1, 3650),
    _p("memory_archive_strength", "forensic", "float", 0.2, "미승인 제안의 strength 가 이 미만이면 자동 보관(archive).", "", "0.2", 0.0, 1.0),
    _p("forensic_min_events", "forensic", "int", 3, "같은 주제의 포렌식 소견이 이 횟수 이상 누적되면 corpus_gap 제안 생성.", "", "3", 1, 100),
    _p("forensic_near_miss_mult", "forensic", "int", 3, "기대 결과 포렌식(forensic expect): 기대 청크가 채널 top_k 밖이지만 top_k × 이 배수 안에 있으면 'top_k 상향' 튜닝 제안을 낸다.",
       "크면 먼 순위까지 상향 제안(잡음↑), 1 이면 제안 없음에 가깝다.", "3", 1, 20),
    _p("forensic_term_candidates", "forensic", "int", 6, "기대 결과 포렌식: FTS 탈락 청크에서 뽑는 대표 용어(동의어 후보) 수. 헤딩 용어가 먼저, 그다음 빈도순.", "", "6", 1, 30),
    _p("forensic_term_targets", "forensic", "int", 20, "기대 결과 포렌식: 문서 지정 없이 용어만 준 경우 코퍼스에서 그 용어를 담은 청크를 최대 몇 개까지 목표로 삼을지.", "많으면 재실행 판정이 느려진다.", "20", 1, 200),
    _p("forensic_pin_confidence", "forensic", "float", 0.6, "기대 결과 포렌식이 내는 pin 제안의 confidence (evolve 목록 정렬·자동 적용 임계와 비교되는 값).", "", "0.6", 0.0, 1.0),
    _p("forensic_suggestion_min_confidence", "forensic", "float", 0.5,
       "기대 결과 포렌식 화면에서 '수정안' 을 바로 펼쳐 보여 줄 최소 confidence. 이 미만은 접어 둔다(버리지는 않는다).",
       "낮추면 근거가 약한 제안까지 먼저 보이고, 높이면 확실한 것만 남는다.", "0.5", 0.0, 1.0),
    _p("forensic_targets_shown", "forensic", "int", 3,
       "기대 결과 포렌식 화면에서 목표 청크의 '탈락 단계' 표를 바로 보여 줄 개수 (가장 멀리 간 것 순). 나머지는 접어 둔다.",
       "용어만 주고 실행하면 목표가 수십 개가 되어 같은 표가 반복된다.", "3", 1, 200),
]

_INDEX: Dict[str, Dict[str, Any]] = {p["key"]: p for p in TUNABLES}


def spec(key: str) -> Dict[str, Any]:
    return _INDEX[key]


def coerce(key: str, value: Any) -> Any:
    p = _INDEX[key]
    t = p["type"]
    if t == "bool":
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if t == "int":
        v = int(float(value))
    elif t == "float":
        v = float(value)
    elif t == "choice":
        v = value
        if p["choices"] and v not in p["choices"]:
            raise ValueError("%s must be one of %s" % (key, p["choices"]))
        return v
    else:
        return str(value)
    if p["min"] is not None and v < p["min"]:
        raise ValueError("%s < min %s" % (key, p["min"]))
    if p["max"] is not None and v > p["max"]:
        raise ValueError("%s > max %s" % (key, p["max"]))
    return v


class Tuning:
    """tuning.json 의 오버라이드 + 기본값. source=config 키는 Settings 가 진실이므로 여기서는 값을 갖지 않는다.

    병렬 처리(2026-09-15): 요청 단위 오버레이. push_overlay() 를 부른 스레드는 그 뒤로 `values` 가 자기만의 사본(기본 = 현재 전역값 복사)을
    가리키므로, 프리셋(--preset / mode=deep)·fusion compare·trial 이 요청 안에서 T.values 를 바꿔도 다른 스레드의 질의에 영향을 주지 않는다.
    pop_overlay() 로 해제. 오버레이가 없는 스레드(CLI 단일 실행, 서버의 설정 저장)는 전역 값을 직접 다룬다.
    """
    _TL = threading.local()

    def __init__(self, values: Optional[Dict[str, Any]] = None):
        self._base: Dict[str, Any] = {}
        for k, v in (values or {}).items():
            if k in _INDEX and _INDEX[k]["source"] == "tuning":
                try:
                    cv = coerce(k, v)
                except (ValueError, TypeError):
                    continue
                # 기본값과 같은 값은 '오버라이드' 가 아니다 — `config fill-defaults --tuning` 이 모든 키를 파일에 적어 두어도
                # to_dict()/describe(overridden) 는 실제로 바꾼 것만 보이게 한다 (set() 과 같은 규칙)
                if cv != _INDEX[k]["default"]:
                    self._base[k] = cv

    # ---- 요청 단위 오버레이 ----
    @property
    def values(self) -> Dict[str, Any]:
        ov = getattr(Tuning._TL, "overlay", None)
        return ov if ov is not None else self._base

    @values.setter
    def values(self, d: Dict[str, Any]) -> None:
        if getattr(Tuning._TL, "overlay", None) is not None:
            Tuning._TL.overlay = dict(d or {})
        else:
            self._base = dict(d or {})

    def push_overlay(self) -> Dict[str, Any]:
        """현재 스레드에 전역값 사본 오버레이를 만든다 (중첩 허용: 이전 오버레이를 반환하므로 pop_overlay(prev) 로 복원)."""
        prev = getattr(Tuning._TL, "overlay", None)
        Tuning._TL.overlay = dict(prev if prev is not None else self._base)
        return prev

    def pop_overlay(self, prev: Optional[Dict[str, Any]] = None) -> None:
        Tuning._TL.overlay = prev

    def has_overlay(self) -> bool:
        return getattr(Tuning._TL, "overlay", None) is not None

    def get(self, key: str) -> Any:
        vals = self.values
        if key in vals:
            return vals[key]
        return _INDEX[key]["default"]

    def set(self, key: str, value: Any) -> Any:
        if key not in _INDEX:
            raise KeyError("unknown tunable: %s" % key)
        if _INDEX[key]["source"] != "tuning":
            raise KeyError("%s is a config.json setting (use config set)" % key)
        v = coerce(key, value)
        if v == _INDEX[key]["default"]:
            self.values.pop(key, None)
        else:
            self.values[key] = v
        return v

    def reset(self, key: Optional[str] = None) -> None:
        if key:
            self.values.pop(key, None)
        else:
            self.values.clear()

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.values)

    def describe(self, settings: Any = None) -> List[Dict[str, Any]]:
        out = []
        for p in TUNABLES:
            d = dict(p)
            if p["source"] == "config":
                d["value"] = getattr(settings, p["key"], p["default"]) if settings is not None else p["default"]
            else:
                d["value"] = self.get(p["key"])
            d["overridden"] = d["value"] != p["default"]
            out.append(d)
        return out


T = Tuning()   # 전역 현재값 (Pipeline.reload / load_tuning 이 갱신). 객체는 하나로 유지하고 내용만 바꾼다 (다른 모듈이 참조를 들고 있음).


def load_tuning(path: Optional[str] = None) -> Tuning:
    path = path or TUNING_PATH
    vals: Dict[str, Any] = {}
    if os.path.exists(path):
        try:
            from . import atomicio
            data = atomicio.read_json(path)
            vals = {k: v for k, v in (data or {}).items() if not k.startswith("_")}
        except Exception:
            vals = {}
    fresh = Tuning(vals)
    T._base = fresh._base       # 전역 값만 교체 (요청 오버레이는 각 스레드가 갖고 있으므로 건드리지 않음)
    return T


EXPLICIT_MARK = "_explicit_defaults"    # tuning.json 에 이 키가 true 면 모든 튜닝 키를 기본값까지 적어 둔다 (config fill-defaults --tuning)
_COMMENT_SPARSE = "단계별 튜닝 파라미터 오버라이드. 키/기본값/설명은 docs/TUNING.md 또는 `python -m llmwiki tuning show`. 기본값과 같은 값은 저장하지 않습니다."
_COMMENT_EXPLICIT = ("단계별 튜닝 파라미터 (모든 키를 기본값까지 명시 — `config fill-defaults --tuning`). 설명·범위는 docs/TUNING.md 또는 "
                     "`python -m llmwiki tuning show`. 값을 바꾸면 그 줄만 고치면 된다. _explicit_defaults 를 지우면 다음 저장부터 바꾼 값만 남긴다.")


def tunable_keys() -> List[str]:
    """tuning.json 이 가질 수 있는 키 (source=tuning) — 단계 순서."""
    return [p["key"] for p in TUNABLES if p["source"] == "tuning"]


def save_tuning(t: Tuning, path: Optional[str] = None, explicit: Optional[bool] = None) -> str:
    """tuning.json 저장. explicit=None 이면 파일의 _explicit_defaults 표식을 따른다:
    표식이 있으면 모든 튜닝 키를 현재값(기본값 포함)으로, 없으면 예전처럼 바꾼 값만."""
    path = path or TUNING_PATH
    from . import atomicio
    if explicit is None:
        prev = atomicio.read_json(path)
        explicit = bool(isinstance(prev, dict) and prev.get(EXPLICIT_MARK))
    if explicit:
        data: Dict[str, Any] = {"_comment": _COMMENT_EXPLICIT, EXPLICIT_MARK: True}
        data.update({k: t.get(k) for k in tunable_keys()})
    else:
        data = {"_comment": _COMMENT_SPARSE}
        data.update(t.to_dict())
    return atomicio.write_json(path, data)


def fill_defaults(path: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """tuning.json 에 빠진 튜닝 키를 전부 기본값으로 채워 쓴다 (있는 값 유지, _explicit_defaults=true 표식).
    표식이 있으면 이후 `tuning set` · Web 저장도 모든 키를 유지한다. 반환 {path, added, unknown, written}."""
    path = path or TUNING_PATH
    from . import atomicio
    prev = atomicio.read_json(path)
    prev = prev if isinstance(prev, dict) else {}
    out: Dict[str, Any] = {"_comment": _COMMENT_EXPLICIT, EXPLICIT_MARK: True}
    added: List[str] = []
    unknown: List[str] = []
    for k in tunable_keys():
        if k in prev:
            out[k] = prev[k]
        else:
            out[k] = _INDEX[k]["default"]
            added.append(k)
    for k, v in prev.items():
        if k.startswith("_"):
            continue
        if k in _INDEX and _INDEX[k]["source"] == "config":
            unknown.append("%s (config.json 항목 — 여기서는 무시됨)" % k)
        elif k not in _INDEX:
            unknown.append(k)
            out[k] = v                    # 모르는 키도 지우지 않는다 (다른 버전이 쓰는 값일 수 있음)
    rep = {"path": path, "added": added, "unknown": unknown, "dry_run": dry_run,
           "written": False, "changed": bool(added) or not prev.get(EXPLICIT_MARK)}
    if rep["changed"] and not dry_run:
        atomicio.write_json(path, out)
        rep["written"] = True
    return rep


def render_doc(settings: Any = None, t: Optional[Tuning] = None) -> str:
    """docs/TUNING.md 본문 생성."""
    t = t or T
    rows = t.describe(settings)
    lines = ["# 단계별 튜닝 파라미터 (자동 생성: `python -m llmwiki tuning doc`)", "",
             "값을 바꾸는 방법: (1) 프로젝트 루트 `tuning.json` 에 `{\"키\": 값}` 으로 적기 (source=tuning), config 항목은 `config.json`;",
             "(2) CLI `python -m llmwiki tuning set 키=값 …` / `config set 키=값`; (3) Web UI **Tuning** 탭. `rebuild` 표시는 값 변경 후 전체 리빌드가 필요한 항목입니다.", "",
             "| 단계 | 키 | 타입 | 기본 | 현재 | 범위/선택 | 설명 | 영향(impact) | 예시 | 파일 | rebuild |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        rng = ("%s~%s" % (r["min"], r["max"])) if r["min"] is not None or r["max"] is not None else (", ".join(str(c) for c in (r["choices"] or [])) or "")
        cur = r["value"]
        lines.append("| %s | `%s` | %s | %s | %s%s | %s | %s | %s | %s | %s | %s |" % (
            r["stage"], r["key"], r["type"], r["default"], cur, " **(변경)**" if r["overridden"] else "", rng,
            r["desc"].replace("|", "\\|"), r["impact"].replace("|", "\\|"), r["example"].replace("|", "\\|"),
            "config.json" if r["source"] == "config" else "tuning.json", "✔" if r["rebuild"] else ""))
    lines += ["", "## 단계 설명", ""]
    for k, v in STAGES.items():
        lines.append("- **%s** — %s" % (k, v))
    return "\n".join(lines) + "\n"
