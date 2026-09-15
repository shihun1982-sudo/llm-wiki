# -*- coding: utf-8 -*-
"""구조/흐름 레지스트리 — Web UI 'Architecture' 탭과 CLI `arch` 가 공유하는 단일 정의.

flows: build / query / evolve / watch 각각의 단계(stage) 순서와, 단계마다
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
       trace: List[str] = (), tuning_stage: str = "", module: str = "", io: str = "") -> Dict[str, Any]:
    return {"key": key, "title": title, "desc": desc, "impact": impact, "toggles": list(toggles), "settings": list(settings),
            "cli": list(cli), "trace": list(trace) or [key], "tuning_stage": tuning_stage, "module": module, "io": io}


FLOWS: Dict[str, Dict[str, Any]] = {
    "build": {
        "title": "Data Build (색인)", "entry": "python -m llmwiki build [--full] · Web Build 탭 · watch/auto_build",
        "desc": "코퍼스 폴더 → 문서 → 청크(FTS5) → 벡터 → 지식 그래프 → 위키 페이지. 증분 빌드는 변경 문서만 다시 처리.",
        "stages": [
            _s("health", "Health 검사", "빌드 전 Python/SQLite FTS5·DB 무결성·WAL·디스크·코퍼스 폴더·벡터 메모리 전망·임베딩 차원·(필요 역할) LLM/임베더/rerank/MCP ping.",
               "실패(fail) 항목이 있으면 빌드를 시작하지 않아 중간 실패를 예방. warn 은 alerts 로 보고.",
               ["health_check"], ["build_lock_timeout"], ["health", "health --quick", "build --force", "build --no-health-check"], module="health.py"),
            _s("providers", "프로바이더 준비", "역할별 LLM(extract/summary)·임베더 인스턴스 생성. auto 면 API 키/Ollama 탐지.",
               "최초 1회 지연(Ollama 탐지 ~0.8s). 프로바이더가 없으면 LLM 단계는 모두 skipped.",
               ["llm_graph", "community_summary"], ["llm_provider", "llm_model", "llm_roles", "embed_provider", "embed_model"],
               ["models set …", "--llm", "--embed-provider", "--extract-model"], module="providers.py"),
            _s("load_corpus", "코퍼스 로드", "corpus_dirs 를 재귀 스캔해 .md .txt .csv .html .pdf 를 읽고 sha1 해시. stat_skip 이면 mtime/size 같은 파일은 읽지 않음.",
               "파일 수·PDF 파싱에 비례. stat_skip 으로 변경 없는 빌드는 수십 ms.", ["stat_skip", "incremental"], ["corpus_dirs"],
               ["build", "build --no-stat-skip", "watch"], module="corpus.py", io="files → Document[]"),
            _s("diff", "변경 감지", "DB 의 문서 해시와 비교해 changed / removed / new 분류. --full 이면 전부 changed.",
               "증분 범위를 결정. incremental 끄면 매번 전체 재처리.", ["incremental"], [], ["build --full", "build --no-incremental"], module="pipeline.py"),
            _s("chunk_index", "청킹 · FTS 색인 (채널 fts)", "헤딩 단위 섹션 → 문단 경계 분할(오버랩) → chunks + chunks_fts(BM25, 조사 제거 토큰·bigram·메타 토큰). build_fts 를 끄면 FTS 행을 쓰지 않음. `build fts` 는 chunks 는 그대로 두고 FTS 행만 다시 쓴다(임베딩·그래프 불변).",
               "청크 크기가 검색 정밀도/컨텍스트 토큰/벡터 메모리를 좌우. 값 변경 시 전체 리빌드(세 채널 모두).",
               ["build_fts", "fts_trigram"], ["chunk_max_chars", "chunk_overlap_chars"], ["build --full", "build fts", "build --channels fts"], tuning_stage="chunk_index", module="corpus.py · store.py", io="Document → Chunk[] → FTS5"),
            _s("embed", "벡터 임베딩 (채널 vector)", "청크 텍스트를 임베딩해 embeddings 테이블에 저장. hash 임베더는 IDF 를 전체 빌드에서 적합. `build vector` 는 임베딩 없는 청크만(--full 이면 전부) 다시 임베딩한다(FTS·그래프 불변).",
               "벡터 채널 recall 의 원천. hash 는 오프라인·비의미적, voyage/st 는 의미 검색. 메모리 = 청크×dim×4B.",
               ["embed", "idf_refit_incremental", "embed_adaptive"], ["embed_provider", "embed_model", "embed_dim", "embed_batch"],
               ["build --no-embed", "build vector [--full]", "build --channels vector", "models set embed_provider=voyage"], tuning_stage="embed", module="providers.py · embed_run.py", io="Chunk[] → float32[dim]"),
            _s("graph_build", "그래프 추출 (채널 graph)", "규칙(사전·정규식) 및/또는 LLM 으로 엔티티·관계·멘션 추출 → degree → 노드 doc_refs → (전체 빌드) 커뮤니티 → (옵션) 요약. `build graph` 는 그래프 테이블을 비우고 전체 청크에서 재추출한다(FTS·임베딩 불변).",
               "그래프 채널·위키·엔티티 상세의 원천. 규칙은 무료·결정적, LLM 은 청크당 1회 호출(토큰↑, budget 으로 제한; 실패는 llm_report).",
               ["rule_graph", "llm_graph", "communities", "community_summary", "incremental_communities", "explicit_relations"],
               ["llm_graph_budget", "llm_graph_min_chars", "llm_roles.extract", "llm_roles.summary"],
               ["build --llm-graph", "build --no-rule-graph", "build graph", "build --channels graph", "entity <name>", "graph"],
               trace=["graph_build", "rule_extract", "llm_extract", "degrees", "doc_refs", "communities"], tuning_stage="graph_build",
               module="graph_rules.py · graph_llm.py · graph_build.py", io="Chunk[] → entities/relations/mentions"),
            _s("wiki_pages", "위키 페이지", "엔티티별 마크다운(설명·문서 참조·관계·근거 문단·편집 노트) + INDEX. 증분은 변경 엔티티만.",
               "사람이 편집한 `## 편집 노트` 가 다음 빌드에 overlay 문서로 재색인됨(HITL 진화 경로).",
               ["wiki_pages", "wiki_full_rewrite"], ["wiki_dir"], ["wiki", "build --no-wiki-pages"], module="wiki.py", io="entities → wiki/*.md"),
            _s("prune", "정리 · 유지보수", "고아 엔티티·끊긴 참조 삭제, 다른 임베더의 벡터 삭제, (전체 빌드) FTS optimize, WAL checkpoint.",
               "검색 속도 유지·DB 비대 방지.", ["fts_optimize"], [], ["maintenance fts_optimize|vacuum|wal_checkpoint"], module="store.py"),
            _s("warm_cache", "캐시 예열", "벡터 행렬·엔티티 인덱스를 메모리에 적재.", "빌드 직후 첫 질의 지연 제거(3만 청크면 수 초).",
               ["warm_cache"], [], ["maintenance warm_cache"], module="store.py"),
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
               trace=["cache_hit", "precompute_hit"], module="pipeline.py · precompute.py"),
            _s("time_scope", "시간 표현 해석", "'지난주·어제·3일전·2026년 8월·Q3' 등 한국어 상대/절대 시간 표현을 timezone 기준 날짜 범위로 변환하고 검색 질의에서 제거. 문서 날짜(front matter date → 파일명 → mtime) 로 boost 또는 filter.",
               "시간 조건이 있는 질문의 정밀도↑. filter 가 0건이면 boost 로 자동 완화.", ["time_scope"], ["timezone", "week_start"], ["--no-time-scope", "tuning set time_mode=filter"],
               tuning_stage="time_scope", module="timeparse.py"),
            _s("query_rules", "규칙 기반 질의 확장", "query_rules.json 의 acronym(동치·구문) / synonym(OR, 가중) / alias(정규화 치환) / related(보조 리스트) / exclude(NOT+페널티) 를 유형별로 다르게 적용.",
               "LLM 없이 결정적으로 recall↑. related 를 별도 리스트로 두어 precision 보호. profile_expansion 으로 전/후 효과 기록.",
               ["query_rules", "profile_expansion"], [], ["rules test \"질의\"", "--no-query-rules"], tuning_stage="query_rules", module="query_rules.py",
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
               ["external_rag"], [], ["mcp-source retrieve \"…\"", "tuning set channel_w_external=0.7", "--no-external-rag"], tuning_stage="rrf_fuse", module="mcp_client.py · query_engine.py", io="query → [(ext chunk, score)]"),
            _s("rrf_fuse", "융합 · 부스트", "채널별 순위를 RRF / 가중 RRF / min-max / z-score / dbsf 로 합친 뒤 post-fusion boost(문서유형·시간·최신성·pin·provenance·피드백·exclude 페널티) 적용.",
               "다중 채널 합의 후보가 상위로. 방식은 `fusion compare` 로 평가셋 비교. 모든 배율은 hit.boosts 에 기록.",
               ["pins", "feedback_boost"], ["rrf_k"], ["tuning set fusion_method=zscore", "fusion compare", "pin add …"], trace=["rrf_fuse", "boost"],
               tuning_stage="rrf_fuse", module="fusion.py", io="lists → Hit[] (fused, boosted)"),
            _s("rerank", "리랭크", "후보 상위 n개를 LLM(JSON 순위) / 크로스인코더 / 로컬 휴리스틱(커버리지·합의·길이·헤딩)으로 재정렬.",
               "최종 MRR 에 가장 직접적. LLM 은 토큰 1회, 크로스인코더는 로컬 CPU, 로컬은 수 ms.",
               ["rerank", "rerank_llm"], ["rerank_candidates", "rerank_chunk_chars", "llm_roles.rerank"], ["--no-rerank", "--no-rerank-llm", "tuning set rerank_method=cross_encoder"],
               trace=["rerank_llm", "rerank_cross_encoder", "rerank_local"], tuning_stage="rerank", module="retrieval.py"),
            _s("doc_expand", "문서 단위 확장", "리랭크 상위 청크가 속한 문서(doc_expand_top_docs)의 나머지 청크를 질의 키워드 커버리지·벡터 유사도(hybrid)로 점수화해 임계 이상만 문서 순서로 컨텍스트에 추가 (부모 청크 뒤, [C#] 인용 가능, why=doc_expand).",
               "한 문서의 표·목록·후속 문단이 잘려 나가는 문제 완화(근거 완전성↑). 토큰↑ → 상한(max_chunks·min_score)으로 제어. speed/token 프리셋은 off.",
               ["doc_expand"], [], ["--no-doc-expand", "tuning set doc_expand_max_chunks=5"], tuning_stage="context", module="query_engine.py", io="final Hit[] → extra chunks"),
            _s("context", "컨텍스트 구성", "상위 top_k_final 청크(+doc_expand 추가 청크)를 [C#] 블록으로 조립. 중복 제거(오버랩·유사 문단)·문장 압축·인접 청크·그래프 관계 첨부.",
               "답변 LLM 입력 토큰과 근거 완전성을 결정.", ["context_trim", "dedupe_hits"], ["top_k_final", "context_max_chars", "context_chunk_chars"],
               ["--no-context-trim", "--no-dedupe-hits", "--k 8"], tuning_stage="context", module="answer.py", io="Hit[] → context text + citations"),
            _s("evidence", "근거 충분성 · fallback 루프", "컨텍스트가 질문에 답하기에 충분한지 판정(휴리스틱: 점수·채널 합의·키워드 커버리지·글자수, 옵션 LLM). 부족하면 L1 규칙 확장 → L2 LLM 확장/분해 → L3 그래프 hops+1·인접 청크 → L4 광역(k×n, 유형 필터 해제, MCP) 순으로 재검색. attempt/token/latency 예산.",
               "근거 부족 답변을 줄임. 불충분할 때만 비용 발생. 무한 루프 방지 예산 필수.",
               ["evidence_check", "evidence_check_llm", "fallback_loop", "mcp_sources"], ["llm_roles.verify"], ["--fallback-loop", "tuning set fallback_max_attempts=3"],
               trace=["evidence_check", "fallback"], tuning_stage="evidence", module="evidence.py"),
            _s("answer", "답변 생성", "LLM(prompts/answer_system.md + answer_guide.md, 인용 강제, 구조화·상세) 또는 추출식(키워드 문장 선택) 답변. 근거 부족이면 insufficient_data 응답.",
               "품질의 최종 출력. LLM 없으면 자동 추출식. 가이드 md 를 편집해 구조/문체 변경.",
               ["llm_answer", "evidence_compress"], ["answer_max_tokens", "answer_effort", "llm_roles.answer"], ["--no-llm-answer", "--answer-model …", "prompts show answer_guide"],
               trace=["answer_llm", "answer_extractive", "evidence_compress"], tuning_stage="answer", module="answer.py"),
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
               ["query … --no-log", "evolve status"], module="evolve.py"),
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
               "품질 저하 자동 차단.", [], [], ["eval", "eval --matrix"], trace=["eval"], module="evalset.py"),
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
    """UI/CLI 용 전체 레지스트리: flows + 단계별 tunables(자동 수집) + 토글/설정 설명."""
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
            stages.append(d)
        flows[fk] = dict(f, stages=stages)
    return {"flows": flows, "toggle_help": TOGGLE_HELP, "setting_help": SETTING_HELP, "tuning_stages": STAGES}


def render_text(reg: Dict[str, Any], toggles: Dict[str, bool]) -> str:
    lines = []
    for fk, f in reg["flows"].items():
        lines.append("== %s — %s" % (fk, f["title"]))
        lines.append("   entry: %s" % f["entry"])
        for st in f["stages"]:
            tg = ", ".join("%s%s" % (t, "" if toggles.get(t, True) else "(OFF)") for t in st["toggles"]) or "-"
            lines.append("   ▸ %-18s %s" % (st["key"], st["title"]))
            lines.append("       %s" % st["desc"])
            lines.append("       impact: %s" % st["impact"])
            lines.append("       toggles: %s | settings: %s | cli: %s | tunables: %d" % (tg, ", ".join(st["settings"]) or "-", ", ".join(st["cli"]) or "-", len(st["tunables"])))
    return "\n".join(lines)
