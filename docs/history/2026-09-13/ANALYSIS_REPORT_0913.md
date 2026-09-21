# 요구사항 분석 리포트 (2026-09-13)

> 대상: `docs/user-req.0913.,txt` (모뎀 HW 제어 임베디드 SW 조직용 self-evolving LLM RAG wiki 추가 요구사항)
> 목적: 각 요구사항의 타당성·현재 코드 대비 갭·impact 를 정리하고 **진행 여부 판정**을 내린다. 구현 순서와 상세 작업은 [IMPLEMENTATION_PLAN_0913.md](IMPLEMENTATION_PLAN_0913.md) 참조.
> 판정 기호: ✅ 진행 · 🟡 조건부/축소 진행 · 🔵 이미 충족(보강만) · ⛔ 반대/보류
> **상태 (2026-09-13): 구현 완료.** §5 의 결정 사항 중 D3(Mango tool 스펙 없음 → 범용 어댑터+mock), D4(§3.10 해석), D6(전면 재배치), D14(4096d 기본 유지·전 차원 지원) 은 사용자 확인, 나머지는 권고안대로 진행. 결과: [IMPLEMENTATION_PLAN_0913.md](IMPLEMENTATION_PLAN_0913.md) · [BRINGUP_GUIDE.md](../../BRINGUP_GUIDE.md) · [CLI_FLOWS.md](../../CLI_FLOWS.md) · [ARCHITECTURE_V3.md](../2026-09-15/ARCHITECTURE_V3.md).

---

## 0. 한눈에 보는 판정표

| # | 요구 | 판정 | 품질 | 속도 | 토큰 | 구현 규모 | 핵심 결정 |
|---|---|---|---|---|---|---|---|
| A1 | Corpus contract / document schema + schema_version | ✅ | ↑↑ | – | – | 중 | YAML front matter + `schemas/*.json` + `corpus lint` |
| A1' | Mango MCP 접근 (toggle, CLI, 별도 config) | 🟡 | ↑ | ↓(호출 시) | – | 중 | 범용 MCP 클라이언트 + `mcp_sources.json`, 실제 tool 스펙은 확인 필요 |
| A2 | Use case 5종은 외부 skill/agent 로 | ✅ 동의 | – | – | – | 소 | wiki 는 MCP 도구 표면만 확장 (읽기 + 제안 쓰기) |
| A3 | 5,000 문서 + 50/일 적합성 | ✅ 적합 (조건) | – | – | – | 중 | 외부 스케줄러 + CLI 증분 빌드, 빌드 락, 정합성 검증 명령 |
| 0 | 설정 파일 분리·전체 env 제어 | ✅ | – | – | – | 소 | 모든 설정키 `LLMWIKI_*` 오버라이드, 파일 6종으로 정리 |
| 1 | OpenAI-compatible provider | ✅ | – | – | – | 소 | chat + embeddings, base_url/api_key/model |
| 2 | rerank 전용 엔드포인트 | ✅ | ↑ | ↑(LLM 리랭크 대비) | ↓ | 소 | Cohere/Jina/vLLM/Voyage `/rerank` 포맷 |
| 3 | 임베딩 재개/체크포인트 + 빌드 전 health check | ✅ | – | ↑ | – | 중 | 내용 해시 임베딩 캐시 + 배치 커밋 + `health` 명령 |
| 4 | 임베딩 진행률/리포트 CLI·Web | ✅ | – | – | – | 소 | kv `embed_progress`, `build status`, coverage 리포트 |
| 5 | 배치/WAL 동적 조절 + 개입 알림 | ✅ | (간접) | ↑ | – | 소 | 적응형 배치, WAL 임계 체크포인트, alerts |
| 6 | LLM 질의 확장 + 불충분 시 fallback loop | ✅ | ↑↑ | ↓ | ↓↓ | 대 | 원 질의 유지, 단계적 fallback, attempt/token/latency 예산 |
| 7 | 규칙 기반 어휘 파일 (acronym/synonym/alias/related/exclude) | ✅ | ↑↑ | – | – | 중 | 유형별 다른 적용 방식 + 전/후 프로파일 |
| 8 | 한글 코퍼스 품질 | ✅ | ↑ | ↓(색인 크기) | – | 중 | 스크립트 경계 분리·복합어·선택적 형태소 분석기(kiwi)·trigram 폴백 |
| 9 | 근거 기반 답변 + claim 검증 + 포렌식 + 제안 | ✅ | ↑↑ | ↓ | ↓ | 대 | claim_check 단계, forensics 테이블, request_id 연결 |
| 10 | pin / precompute | 🟡 | ↑ | ↑ | ↑ | 중 | 의미 정의 확인 필요 (§3.10) |
| 11 | Web UI 테마 | ✅ | – | – | – | 소 | CSS 변수 + `data-theme` + themes 레지스트리 |
| 12 | logs 폴더, 정상/이상 로그, request_id 연결 | ✅ | – | – | – | 소 | JSONL 로테이션, `logs` 명령 |
| 13 | episodic/semantic memory + decay | 🟡 | ↑ | – | – | 중 | 자가진화 신뢰도 decay + 사용 기반 boost 로 한정 |
| 14 | 한국어 상대 시간 파싱 (지역 설정) | ✅ | ↑ | – | – | 소 | 날짜 범위 → boost(기본)/filter |
| 15 | 그래프 노드 ↔ 원본 문서 참조 | 🔵 | ↑ | – | – | 소 | 이미 `doc_refs` 있음 → 검색·MCP 에 활용 |
| 16 | 결정적 관계 + provenance/confidence | ✅ | ↑↑ | – | – | 중 | explicit/rule/cooccur/llm/human 구분, CL↔Issue 규칙 |
| 17 | 단계별 weight / fusion 방식 비교 | ✅ | ↑ | – | – | 중 | rrf·wrrf·minmax·zscore·dbsf + post-fusion boost + `fusion compare` |
| 18 | eval 경로 = 사용자 경로 | 🔵 | – | – | – | 소 | 이미 동일(`evaluate→query`), preset/request_id 연결만 보강 |
| 19 | regression 지표/trial 비교 시스템 | ✅ | – | – | – | 중 | `trials` 테이블, `trial run/compare`, Web 비교 뷰 |
| 20 | Generic Headless Agent Provider (opencode) | ✅ | – | ↓ | – | 중 | subprocess + ndjson 파서, 명령 템플릿으로 CLI 무관 |
| 21 | Web UI IA 재설계 + 품질/속도/토큰 preset | ✅ | – | – | – | 대 | 워크플로 7그룹, `presets.json`, CLI `preset apply` |
| 22 | 답변 가이드 md + Evidence-rich 구조화 답변 | ✅ | ↑↑ | – | ↓ | 소 | `prompts/*.md` 외부화, 길이 포렌식 |

---

## 1. 현재 상태 진단 (코드 기준)

### 1.1 환경
- Python **3.14.7** (PLAN.md 작성 시점의 3.7 제약은 해소됨). numpy 2.5.3, pypdf, **anthropic SDK 설치됨**. sentence-transformers·pyyaml·kiwipiepy 미설치. SQLite 3.50.4 (FTS5 trigram 사용 가능). Ollama 설치됨. `opencode` CLI 는 PATH 에 없음.
- 기존 단위 테스트 21개 통과 (`python -m unittest discover -s tests`).
- `config.json` 의 `corpus_dirs` 는 실습용 반도체 문서 경로를 가리킴. 모뎀 코퍼스는 아직 연결되지 않음 → 계획에서는 **합성 모뎀 샘플 코퍼스**(Issue/CL/SW/HW/CodingRule/Weekly) 를 만들어 테스트·평가·데모에 사용한다.

### 1.2 구조 요약
```
build : providers → load_corpus(stat_skip) → diff(sha1) → chunk_index(FTS5) → embed → graph_build(rule|llm → degrees → doc_refs → communities) → wiki_pages → prune → warm_cache
query : sync_index → cache → router → [query_expand] → fts | vector | graph → rrf_fuse → rerank(llm|ce|local) → context → answer(llm|extractive) → evolve_capture → log
evolve: capture | feedback | llm_review → proposals → HITL → snapshot → apply → rebuild → eval → 승격|롤백
```
단일 SQLite 파일(FTS5 + float32 BLOB 벡터 + 그래프 테이블 + 로그), 프로파일러 trace 가 `requests` 테이블에 request_id 로 저장. 26개 토글, 82개 튜닝 파라미터, MCP stdio 서버(읽기 4종), Web UI(15탭, 바닐라 JS).

### 1.3 이번 요구와 관련해 이미 있는 것 (재사용)
| 요구 | 이미 있는 기반 |
|---|---|
| 6 (LLM 확장) | `query_expand` 튜닝: 대체 질의를 **별도 리스트**로 융합(원 질의 유지) — 방향 일치, 단 rerank 역할 LLM 재사용·fallback 없음 |
| 7 (동의어) | `synonyms` 테이블(evolve 산출) 을 FTS 에 단순 OR 확장 — 유형 구분 없음 |
| 8 (한글) | 조사 제거 + 한글 bigram 을 `tokens` 컬럼에 사전 토큰화, bigram 폴백 |
| 9 (근거) | 답변 프롬프트 인용 강제, `cited` 추출, 인용 없으면 `_gaps` 제안 — 검증은 "존재 여부"만 |
| 12 (로그) | request_id 기반 trace(JSON) 저장. 파일 로그 없음(`data/server.log` 0바이트) |
| 15 (doc_refs) | `entities.doc_refs/n_docs/n_mentions` 계산·위키·UI 노출 — 검색 단계에서는 미사용 |
| 16 (provenance) | `relations.source`(rule/llm/evolve) + `confidence` 컬럼 — 탐색 시 가중 미적용, explicit 유형 없음 |
| 17 (fusion) | `fusion_method = rrf | weighted(min-max)`, 라우터 가중치, `fusion_multi_bonus` |
| 18 (동일 경로) | `evaluate()` 가 `query()` 를 호출, Web/CLI/MCP 모두 `Pipeline.query` — 이미 동일 |
| 19 (회귀) | `eval`, `--matrix`, proposal 별 `eval_before/after` — trial 단위 저장·비교 없음 |
| 20 (headless) | `BaseLLM` 추상화 + 역할별 provider — 새 provider 추가만으로 가능 |

### 1.4 5,000 문서 + 50/일 규모 적합성 (A3)
가정: 문서당 평균 8~12 청크(현재 코퍼스 평균 10.6) → 초기 5만~6만 청크, 1년 뒤 +1.8만 문서 → 약 25만 청크.

| 구성요소 | 현재 방식 | 5만 청크 | 25만 청크 | 판단 |
|---|---|---|---|---|
| 벡터 저장 | float32 BLOB in SQLite | 1024d: 200MB / 4096d(hash 기본): 820MB | 1GB / 4GB | **hash 4096d 기본값은 부적합** → 의미 임베더(1024d 이하) 또는 `embed_dim=1024`. float16 저장 옵션 추가 |
| 벡터 검색 | numpy 전수 matmul | 1024d 기준 ~50M FLOP, 수십 ms | ~250M FLOP, 100~300ms | 25만까지 브루트포스 허용. 그 이상은 ANN(sqlite-vec/hnswlib) 플러그인 포인트만 마련 |
| FTS5 | unicode61 + 사전토큰 컬럼 | 문제 없음 | 문제 없음 (`optimize` 주기 실행) | 적합 |
| 규칙 그래프 | 청크당 co_occurs 전조합 | 27문서→2.2k 관계이므로 5천 문서→약 40만 관계 | 200만 | co_occurs 가중치 임계·상한 튜닝 필요, label propagation 은 파이썬 O(E·iters) → 전체 빌드에서만(현행 유지) |
| 증분 빌드 | stat 스캔 + 해시 diff | 5천 파일 stat 수십 ms | 수백 ms | 적합. 하루 50개 문서 증분은 수 초(+임베딩 API 시간) |
| LLM 그래프 추출 | 청크당 1회 | 초기 5만 호출(비용 큼) | 일 500회 | 규칙 추출 기본, LLM 은 `llm_graph_budget` 으로 상한 — 현행 유지 |
| 위키 페이지 | 엔티티당 md 파일 | 수천 파일, 증분은 touched 만 | 수만 파일 | `wiki_min_degree` 상향 옵션 필요 |
| requests/trace | 요청당 JSON | keep_requests 로 절단 | 동일 | 적합 |

결론: **SQLite 단일 파일 + 증분 빌드 구조는 이 규모에 적합**하다. 단 (1) 기본 임베더/차원 조정, (2) co_occurs 폭증 억제, (3) 삭제/rename 정합성 검증, (4) 빌드 동시 실행 방지(락) 가 필요하다.

**증분 빌드 스케줄 방식 (질문 3-1 답)**: llm wiki 가 "알아서" 특정 시각에 빌드하는 내장 스케줄러보다 **OS 스케줄러(Windows 작업 스케줄러 / cron)가 `python -m llmwiki build` 를 호출**하는 방식을 권장한다. 이유: (a) 서버가 꺼져 있어도 동작, (b) 실패 시 OS 로그·재시도 정책을 그대로 사용, (c) 빌드마다 request_id·로그 파일이 남아 추적 가능, (d) 별도 "빌드 관리 agent" 를 두더라도 결국 이 CLI 를 호출하면 된다. 서버 내장 watcher(`auto_build`)는 "저장 즉시 반영" 이 필요한 개발 환경용 보조 경로로 유지한다. 두 경로가 충돌하지 않도록 **파일 기반 빌드 락**을 추가한다.

**삭제/rename 정합성**: 현재 `delete_doc` 이 청크·FTS·임베딩·멘션·관계를 지우고 `prune_orphan_entities/prune_dangling` 이 안전망 역할을 하지만, (a) 증분 빌드에서는 위키 페이지가 정리되지 않고, (b) rename 은 "삭제 + 신규" 로 처리되어 전량 재임베딩되며, (c) 정합성을 사후 검증하는 수단이 없다. → `build verify`(정합성 검사: FTS 행수=청크수, 임베딩 coverage, 댕글링 멘션/관계, 고아 엔티티, 위키 페이지 ↔ 엔티티, 커뮤니티 미배정) 와 **내용 해시 임베딩 캐시**(rename/이동 시 재임베딩 0) 를 추가한다.

---

## 2. 고려사항 분석

### A1. Corpus contract / Document schema — ✅ 진행

**왜 필요한가**: FTS/벡터/그래프 세 채널이 모두 "문서 안에서 무엇을 찾을 수 있는가" 에 의존한다. 특히 그래프의 결정적 관계(CL→Issue), 시간 필터(문서 날짜), 문서 유형별 라우팅/부스트는 **정형 필드가 있어야만** 가능하다. 사람/LLM 이 문서를 생성할 때 계약이 있으면 데이터 품질이 안정된다는 요청자의 판단에 동의한다.

**제안 포맷**: 마크다운 본문 + **YAML front matter** (`---` 블록). 근거: (1) 기존 md 코퍼스와 호환, (2) Obsidian/Hugo 등 범용 도구가 그대로 읽음, (3) LLM 이 생성하기 쉬움, (4) sidecar JSON 보다 파일 이동 시 분리 위험이 없음. pyyaml 의존 없이 **단순 YAML 부분집합 파서**(스칼라·리스트·1단계 맵) 를 내장하고, pyyaml 이 설치되어 있으면 사용한다.

공통 필드(모든 유형):
```yaml
---
schema_version: 1          # 스키마 버전 (필수). 마이그레이션 규칙은 schemas/ 에 버전별 보관
doc_type: issue            # issue | cl | sw_design | hw_design | coding_rule | weekly_report | tc_list | (확장)
id: ISSUE-2041             # 유형별 ID 규칙 (필수, 그래프 노드 ID 가 됨)
title: "RX 경로 DMA underrun 시 PHY 재시작 실패"
date: 2026-08-21           # 문서 기준일 (필수) — 시간 질의/최신성 부스트에 사용
author: hong.gd
status: closed             # 유형별 enum
tags: [rx, dma, phy, modem-b1]
module: [rx_dma, phy_ctrl]   # SW 모듈/컴포넌트 (code map 과 연결)
hw: {chip: MDM9x, rev: B1}   # HW 대상/리비전
related: {issues: [ISSUE-1980], cls: [CL-55321, CL-55402], docs: [SWD-RX-DMA-03]}   # 명시적 링크 → explicit 관계
summary: "한 줄 요약 (선택, 없으면 첫 문단 사용)"
---
```
유형별 필수/권장 필드와 본문 섹션 계약(예: Issue 는 `## 현상`, `## 원인`, `## 분석`, `## 수정` 섹션 권장; CL 은 `related.issues` 필수; HW 설계는 `hw.rev` 필수 + `## Revision History` 표; Weekly report 는 `period: {from,to}` 와 `## 이슈 요약`, `## CL 리뷰 요약`) 은 `schemas/<doc_type>.json` 에 선언한다. 새 카테고리(TC List 등)는 JSON 하나 추가로 확장된다.

**빌드 반영**: front matter → `docs.meta` 저장 + 별도 `doc_meta` 테이블(doc_type, date, id, tags…) 색인 → (a) FTS 에 `tags/id/module` 토큰 추가, (b) 그래프에 `id` 노드(type=doc_type) + `related.*` 로 **explicit 관계**, (c) 라우터가 doc_type 힌트("CL", "이슈") 를 인식해 부스트, (d) 시간 질의는 `date` 로 부스트/필터, (e) `corpus lint` 가 필수 필드·enum·ID 형식·섹션 계약 위반을 리포트(빌드 시 경고, `--strict` 면 제외). front matter 가 없는 기존 문서는 **경로/파일명 규칙으로 유형 추론**(`schemas/infer.json` 의 glob 규칙) 하고 lint 가 "front matter 추가 권장" 으로 표시한다.

**Impact**: 품질 ↑↑ (explicit 관계·시간·유형 부스트가 모두 이 위에 올라감), 빌드 비용 미미. 리스크: 기존 문서 5,000개에 front matter 가 없으면 효과가 제한적 → 추론 규칙 + lint 리포트로 점진 적용.

### A1'. Mango MCP 원시 데이터 접근 — 🟡 조건부 진행

요청자는 Mango MCP 를 통해 build 정보 등 raw data 를 가져올 수 있다고 했으나 **도구 이름/입력·출력 스키마가 확인되지 않았다**. 따라서:
- 구현: 범용 **MCP 클라이언트**(stdio JSON-RPC, 기존 서버 코드의 대칭) + `mcp_sources.json` (서버 명령/URL, 인증 env 키, 사용할 tool 과 인자 매핑, 결과에서 문서 텍스트/메타를 뽑는 경로, 사용 시점: `build_ingest | query_enrich | evolve`) + 토글 `mcp_sources` + CLI `mcp-source list|test|fetch|ingest`. 빌드 시 `ingest` 는 결과를 `corpus_dirs` 밖의 **가상 코퍼스 폴더**(`data/mcp_cache/<source>/*.md`, front matter 포함) 로 저장해 일반 문서와 동일하게 색인한다(캐시·재현성·lint 가능). 질의 시 `query_enrich` 는 fallback loop 의 마지막 단계에서만 호출(비용 통제).
- 테스트는 mock MCP 서버로 수행. **실제 Mango MCP 연결은 tool 스펙을 받은 뒤 매핑 파일만 작성**하면 된다.
- 판정 이유: 설계는 지금 확정할 수 있고 가치가 크지만, 스펙 없이 "실동작" 을 약속할 수 없다.

### A2. Use case 5종을 wiki 에 내장하지 않는다 — ✅ 동의

요청자의 판단에 동의한다. 이유: (1) 각 use case 는 코드 저장소·시뮬레이터·리뷰 도구 등 **wiki 밖의 컨텍스트**가 필요하고 고도화 주기가 다르다. (2) wiki 에 내장하면 wiki 릴리스가 use case 에 묶인다. (3) 반대로 wiki 가 제공해야 하는 것은 **좋은 검색 원시 기능(primitive)** 이다.

wiki 가 MCP 로 제공할 표면(설계 제안):
| 도구 | 용도 | 대응 use case |
|---|---|---|
| `wiki_query(question, mode=fast\|deep, doc_types?, time?, k?)` | 근거 포함 구조화 답변 (deep = fallback loop + 상세 답변) | 1,3,4,5 |
| `wiki_search(channel, query, k, doc_types?)` | 채널별 원시 검색 | 모두 |
| `wiki_related(text, doc_types=[issue,cl], k)` | 입력 텍스트(이슈 분석 결과)와 유사한 문서 + 그래프로 연결된 CL/Issue 를 함께 반환 | 3 |
| `wiki_doc(doc_id \| id)` | 문서 전문 + 메타 + 연결 노드 | 1,2,4 |
| `wiki_entity(name)` | 노드 상세·관계·doc_refs | 2,4 |
| `wiki_propose(kind, payload, reason)` | 분석 결과·정정·새 관계를 **제안**으로 기록(HITL) — 직접 색인 변경 없음 | 3 |
| `wiki_status()` | 상태 | 운영 |
Unified search(5) 는 "질의 파이프라인 그 자체 + deep 모드" 이므로 wiki 내장이 맞다.

### A3. 규모/증분/스케줄 — ✅ (§1.4 참조)

---

## 3. 구현 요청별 분석

### 3.0 설정 파일 체계 — ✅
현재: `config.json`(경로·모델·토글·수치), `.env`(키 + 7개 오버라이드), `tuning.json`, `data/rules.json`. 문제: env 오버라이드가 일부 키만, 기능별 파일이 뒤섞임.
제안 파일 구성 (모두 프로젝트 루트 기준 상대 경로, `LLMWIKI_*_PATH` 로 위치 변경 가능):
| 파일 | 내용 | 변경 시 반영 |
|---|---|---|
| `config.json` | 경로, 프로바이더/역할별 모델, 토글, 운영 수치, 로그 설정, 지역(timezone) | 즉시(서버 reload) |
| `.env` | API 키, 엔드포인트 URL, **모든 config 키의 `LLMWIKI_<KEY>` 오버라이드**(토글은 `LLMWIKI_TOGGLE_<NAME>`) | 프로세스 시작 |
| `tuning.json` | 알고리즘 상수 (기존 82 + 신규) | 즉시 |
| `presets.json` | 품질/속도/토큰 최적화 등 **설정 묶음** (토글+튜닝+config 일괄) | 적용 시 |
| `query_rules.json` | 규칙 기반 어휘(acronym/synonym/alias/related/exclude) + 시간 파싱 지역 | 즉시 |
| `mcp_sources.json` | 외부 MCP 소스 정의 | 즉시 |
| `agents.json` | headless agent provider 명령 템플릿 | 즉시 |
| `data/rules.json` | 그래프 사전·관계 규칙(+ ID 패턴, explicit 관계 규칙) | 재빌드 |
| `schemas/*.json` | 문서 스키마(유형별, 버전별) + 추론 규칙 | lint/빌드 |
| `prompts/*.md` | 역할별 LLM 가이드(answer_guide.md 등) | 즉시 |
| `eval/questions.json` | 평가셋 | – |
디버그 토글(`debug_level`, `profile_expansion`, `keep_prompts`, `log_sql`) 은 config `debug` 섹션으로 모은다.

### 3.1 OpenAI-compatible provider — ✅
`OpenAICompatLLM(base_url, api_key, model)`: `/v1/chat/completions` (system+user, `max_tokens`, `response_format={"type":"json_object"}` 는 옵션·실패 시 무시), usage 파싱, 429/5xx 재시도. `OpenAICompatEmbedder`: `/v1/embeddings` 배치. 대상: vLLM, LM Studio, Ollama OpenAI 호환 포트, OpenRouter, 사내 게이트웨이. 설정: `llm_provider=openai`, `openai_base_url`, `OPENAI_API_KEY`(.env), 역할별 `{"provider":"openai","model":"..."}`. `ping` 은 `/v1/models`. 규모: 소.

### 3.2 rerank 전용 엔드포인트 — ✅
`rerank_method=api` 추가. 요청 `{model, query, documents[], top_n}` / 응답 `results[{index, relevance_score}]` — Cohere·Jina·vLLM `/v1/rerank`·Voyage `/v1/rerank`(응답 키 `data[].relevance_score`) 를 하나의 어댑터로 처리(`rerank_api_style=cohere|voyage`). 설정: `rerank_url`, `rerank_model`, `RERANK_API_KEY`. LLM 리랭크 대비 토큰 0·지연 ↓·품질 ↑(bge-reranker-v2-m3 등 다국어 모델). 규모: 소.

### 3.3 임베딩 재개/체크포인트 + 빌드 전 health check — ✅
- 현재 문제: `embed` 단계가 끝날 때 한 번 commit → 중간 실패 시 전부 유실. 실패 청크 추적 없음.
- 제안: (1) `embedding_cache(provider, model, text_sha1) → vec` 테이블: 같은 내용은 재임베딩하지 않음(rename/이동/재빌드/롤백 모두 이득), (2) N 배치마다 commit + kv `embed_progress {run_id, total, done, failed[], rate, eta, started}` 갱신 → 중단 후 `build` 재실행 시 남은 것만 진행(= 재개), (3) `build --resume` 은 별도 플래그 없이 기본 동작이며 `embed status` 로 확인, (4) **`health` 명령**: 역할별 LLM ping, 임베더 1건 임베딩 + 차원 확인(저장된 차원과 불일치 시 경고), rerank 엔드포인트, MCP 소스, DB `PRAGMA quick_check`, WAL 크기, 디스크 여유, 코퍼스 폴더 접근, 스키마 lint 요약. `build` 는 시작 전에 자동 실행(`--no-health` 로 생략), 실패 항목이 있으면 중단(`--force` 로 강행).

### 3.4 임베딩 진행률/리포트 — ✅
CLI `build status`(진행 중 빌드의 단계·임베딩 진행률·ETA·실패), `embed report`(coverage % 전체/문서유형별, 실패 청크 목록, provider/model/dim, 캐시 적중률, 최근 실행 이력). Web: Corpus 화면에 진행 바 + 리포트. 데이터는 kv + `embed_runs` 테이블.

### 3.5 배치/WAL 동적 조절 — ✅
- 적응형 배치: 시작값 `embed_batch`, 실패(타임아웃·429·413·메모리)는 절반으로, 연속 성공 시 1.5배(상한 `embed_batch_max`), 프로바이더별 하드 상한(voyage 128 등). 배치 지연이 `embed_batch_target_ms` 를 넘으면 축소.
- WAL: 배치 커밋 후 `-wal` 크기가 `wal_checkpoint_mb` 를 넘으면 `PASSIVE` 체크포인트, 실패가 반복되면(다른 연결이 읽는 중) alert.
- 사용자 개입 알림: 실패율 > 임계, 재시도 소진, 차원 불일치, 디스크 부족, 429 지속 → `result["alerts"]` + 로그 WARN + Web 배너 + (옵션) 빌드 중단.
- **요청자의 질문에 대한 답**: 맞다. 배치 크기·WAL 은 검색 품질·질의 지연과 **직접 관계가 없다**(질의는 완성된 색인만 읽는다). 유일한 간접 영향은 임베딩 실패로 인한 coverage 하락 → 벡터 채널 recall 저하. 그래서 coverage 를 health/eval/trial 지표에 포함하고, coverage < 임계 이면 질의 trace 에 경고를 남긴다.

### 3.6 LLM 질의 확장 + 불충분 시 fallback loop — ✅ (핵심)
동의한다. 단, "무조건 LLM 개입" 이 아니라 **필요할 때만 단계적으로** 개입해야 토큰/지연이 통제된다.

설계:
1. **query_expand** (토글, 역할 `expand`): 원 질의는 항상 유지, LLM 은 (a) 대체 표현 N개, (b) 다중 홉 질문이면 sub-query 2~4개 분해(`query_decompose` 토글), (c) 한/영/약어 변형 키워드를 JSON 으로 생성. 각 대체 질의는 별도 리스트로 융합되며 가중치 `query_expand_w`(현행 유지).
2. **evidence_check** (토글): rerank·context 이후 근거 충분성 판정. 1차는 **무료 휴리스틱**(상위 융합 점수, 다중 채널 합의 수, 키워드 커버리지, 컨텍스트 글자수, 시간 필터 적중) 으로 `sufficient | weak | insufficient` 를 내고, 2차(옵션 `evidence_check_llm`)는 LLM 이 "이 근거로 질문에 답할 수 있는가, 부족한 부분은 무엇인가" 를 JSON 으로 판정.
3. **fallback loop** (토글 `fallback_loop`): `insufficient/weak` 이면 단계적으로 확장. 전체 코퍼스 재검색 전에 값싼 단계부터:
   - L1 규칙 확장 강화: `query_rules` 의 related 까지 포함, FTS `or` 모드, k×2, bigram/trigram 폴백
   - L2 LLM 확장/분해(아직 안 했으면), 시간 필터를 boost 로 완화
   - L3 그래프 hops+1, `context_neighbors` 확대, doc_refs 기반 문서 단위 후보
   - L4 doc_type 필터 해제 + 전체 코퍼스 광역(k×4) + (옵션) MCP 소스 enrich
   - 예산: `fallback_max_attempts`(기본 2), `fallback_token_budget`, `fallback_latency_ms`; 초과 시 중단하고 `insufficient_data` 로 답변(무엇이 부족한지 명시). 각 라운드는 trace 에 `fallback[n]` 단계로 기록.
4. **추가 LLM 개입 제안 (모두 토글)**: (a) `evidence_compress` — 긴 청크에서 질문 관련 문장만 LLM 이 선택(현행 `context_trim` 의 LLM 판)… 토큰 절약과 품질 동시 개선, (b) `claim_check`(§3.9), (c) `router_llm` — 질의 의도/doc_type 분류(휴리스틱 라우터 보완, 저비용 모델), (d) `answer_refine` — 검증 실패 문장 재작성 1회. (e) 그래프 빌드 시 `llm_graph` 는 이미 있음.
Impact: 품질 ↑↑, 지연 +1~3 LLM 호출(불충분 시에만), 토큰 ↑(예산 상한). 규모: 대.

### 3.7 규칙 기반 어휘 파일 — ✅
"유형마다 다르게 적용하자" 는 제안이 적절하며 구체 설계는 다음과 같다 (`query_rules.json`):
| 유형 | 예 | 의미 | FTS 적용 | 벡터 적용 | 그래프 적용 | 가중 |
|---|---|---|---|---|---|---|
| acronym | PDCCH ⇄ Physical Downlink Control Channel | 완전 동치, 양방향 | 원 용어 OR 확장어(구문 검색) — 동일 가중 | 대체 질의 1개(확장어로 치환) | 별칭으로 엔티티 시드 매칭 | 1.0 |
| synonym | 재시작 ≈ 리셋 ≈ restart | 준동치 | OR 확장, 가중 `syn_w`(0.8) | 대체 질의(치환) | – | 0.8 |
| alias | "모뎀B" → canonical "MDM9x-B1" | 표기 정규화 | canonical 로 **치환**(+원 표기 OR) | canonical 로 치환 | canonical 엔티티 시드 | 1.0 |
| related | DMA underrun ~ FIFO overflow | 연관(동치 아님) | 주 질의에 넣지 않고 **별도 보조 리스트**(`fts_related`)로 융합, 가중 `related_w`(0.4) | 보조 대체 질의, 가중 0.4 | 그래프 확장 힌트(시드 아님) | 0.4 |
| exclude | "시뮬레이터" 제외 | 잡음 배제 | FTS `NOT` 절 + 후보 후처리 페널티 | 결과 후처리 페널티 | – | – |
근거: 동치어를 OR 로 넣으면 recall 은 오르지만 관련어까지 같은 가중으로 넣으면 precision 이 급락한다. related 는 RRF 에서 별도 리스트로 두면 "여러 채널 합의" 효과만 받고 단독으로 상위를 차지하지 못한다. exclude 는 BM25 에서 NOT 만으로는 벡터 채널을 막지 못하므로 후처리 페널티가 필요하다.
프로파일: `query_rules` 단계에 원 질의/확장 후 질의/발화 규칙/채널별 추가 hit 수(확장 전 검색을 한 번 더 실행하는 `profile_expansion` 디버그 토글일 때) 를 기록하고, 답변 근거 중 "확장 덕분에 들어온 청크" 를 표시한다. evolve 의 `synonyms` 테이블은 이 파일의 `synonym` 유형으로 통합(제안 승인 시 파일에 기록).

### 3.8 한글 코퍼스 품질 — ✅
현행 조사 제거 + bigram 은 합리적이며 유지한다. 추가 판단:
| 기법 | 판단 | 근거 |
|---|---|---|
| 스크립트 경계 분리 (`PDCCH디코딩` → `pdcch` `디코딩`, `3ms` → `3` `ms`) | ✅ 채택 | 임베디드 문서는 영문 약어+한글이 붙어 쓰이는 경우가 많음. 비용 0 |
| 복합 명사 사전 분리 (`재전송타이머` → `재전송` `타이머`) | ✅ 채택 (query_rules 의 `compound` 항목 + 사전) | 형태소 분석기 없이도 도메인 용어 recall 개선 |
| 형태소 분석기 (kiwipiepy) | 🟡 선택적 플러그인 | 설치돼 있으면 `tokenizer=kiwi`, 없으면 휴리스틱. 색인·질의 양쪽 동일 토크나이저 강제. 재빌드 필요 |
| 한글 bigram 컬럼 | 🔵 이미 있음 (`tokens`) | 헤딩 bigram 가중치 튜닝 추가 |
| trigram FTS 컬럼 | 🟡 폴백 전용 | 인덱스 2~3배. 별도 `chunks_tri` 테이블을 **토글**로 만들고 0-hit 폴백에서만 사용 |
| 자모 분해 | ⛔ | 오타 대응용인데 코퍼스가 생성 문서라 이득 적고 잡음 큼 |
| 임베더 | 권장: `bge-m3`(Ollama/ST) 또는 voyage-multilingual | hash 임베딩은 의미 검색이 아님 |
질의 측도 동일 파이프라인을 타도록 `textutil.analyze(text)` 하나로 통합한다.

### 3.9 근거 기반 답변·claim 검증·포렌식·제안 — ✅ (핵심)
동의한다. 설계:
- **답변 계약**: `prompts/answer_guide.md`(§3.22) 로 "모든 사실 문장에 [C#]", "근거 없는 내용은 '확인되지 않음'", 구조(핵심 답변 → 상세 설명 → 근거 표 → 미확인/추가 조사 항목).
- **claim_check 단계** (토글): 답변을 문장 단위로 분해 → 사실 문장(숫자·날짜·ID·인과 표현 포함) 판별 → 각 문장의 인용 [C#] 존재 확인 → **지원 검증**: 1차 휴리스틱(문장의 키워드·숫자·ID 가 인용 청크에 존재하는 비율), 2차(옵션 `claim_check_llm`) LLM/NLI 판정 `supported | partial | unsupported`. 결과 `groundedness = supported/총 사실문장`, 미지원 문장은 (설정에 따라) 제거·"[미확인]" 표기·`answer_refine` 1회.
- **insufficient_data 처리**: fallback loop(§3.6) 종료 후에도 부족하면 답변 대신 "무엇을 찾았고 무엇이 없는지" 를 구조화해 반환 + `forensics` 기록.
- **포렌식**: `forensic <request_id>` 명령/Web 패널. trace 를 읽어 단계별 진단 규칙을 적용: FTS 0건(질의 용어가 코퍼스에 없음? 규칙 확장 미발화?), 벡터 최고 유사도 < 임계(임베더 부적합/coverage), 그래프 시드 없음(사전 누락), 근거가 컨텍스트 상한에서 잘림, 리랭크가 정답 후보를 뒤로 보냄, claim 미지원, 답변 길이 이상(§3.22). 결과는 `forensics` 테이블(request_id, findings[], suggestions[]) 에 자동 누적(토글 `forensic_auto`: insufficient/미지원 발생 시 자동 실행).
- **제안 연결**: 누적된 forensics 를 집계해 proposal 생성 — `corpus_gap`(주제 X 문서 부족), `query_rule`(동의어/약어 추가), `tuning`(파라미터 변경 제안, 회귀 평가 첨부), `schema`(필드 누락). 승인은 기존 HITL(`evolve_auto_apply` 토글) 를 따르며, **tuning 제안은 자동 적용 대상에서 제외**(회귀 평가를 붙여 사람이 승인).

### 3.10 pin / precompute — 🟡 의미 확인 필요
요청 문구가 짧아 아래 해석으로 진행하되 확인을 요청한다.
- **pin**: (a) 특정 문서/청크를 특정 조건(질의 키워드·doc_type·항상) 에서 **반드시 근거에 포함**시키는 기능 — 예: 코드 리뷰 관련 질의에는 coding_rule 문서를 항상 포함, (b) 특정 질의에 대한 **정답 근거 고정**(사용자가 "이 질문의 답은 이 문서" 라고 pin → 이후 동일/유사 질의에서 상위 고정 + 평가셋으로 승격). `pins.json` + CLI `pin add|remove|list` + Web 근거 카드의 📌. 토글 `pins`.
- **precompute**: (a) 자주 묻는/평가셋 질의의 **답변 사전 계산** 을 영속 캐시(`answer_cache` 테이블, build_version 키) 에 저장해 첫 응답 지연·토큰 제거 — `precompute run`(평가셋 + 최근 질의 로그 상위 N) 을 빌드 후 자동 실행(토글 `precompute`), (b) 문서 단위 "카드"(제목+front matter+헤딩 개요) 임베딩을 사전 계산해 **문서 수준 검색 채널**(`doc_vector`) 을 제공 — 긴 문서·설계문서 검색에 유리.
다른 의미였다면 계획서 확정 시 알려 달라.

### 3.11 Web UI 테마 — ✅
CSS 변수는 이미 `:root` 에 정리돼 있음. `data-theme="<name>"` 속성 + `web/static/themes/<name>.css`(변수 재정의만) + `themes.json` 레지스트리 + 헤더 셀렉터 + `prefers-color-scheme` 자동 + localStorage 저장. 기본 제공: light, dark, high-contrast, solarized. 새 테마는 CSS 파일 하나 추가.

### 3.12 로깅 — ✅
`logs/` 폴더: `llmwiki.log`(모든 레벨, 로테이션 10MB×10), `error.log`, `build.log`, `query.log`, JSON Lines 형식(`ts, level, request_id, kind, stage, msg, data`). 프로파일러의 `note/log` 가 로거로도 흘러가고, 모든 LLM/임베딩/HTTP 호출은 `request_id` 를 컨텍스트로 갖는다(threading.local). CLI `logs tail|grep --request <id>|--since`, Web Observability 화면에서 request 선택 시 관련 로그 + 프로파일 + 포렌식을 한 화면에. 설정: `log_level`, `log_dir`, `log_json`, `log_sql`(디버그).

### 3.13 Episodic / Semantic memory + decay — 🟡 경량 적용
RAG "지식" 자체는 코퍼스가 정답이므로 코퍼스 사실을 decay 시키면 안 된다. 적용 가치가 있는 곳은 **자가진화 층**이다:
- Episodic memory = `episodes` 테이블: 질의 1건 = (질의, 사용 근거, 판정, 피드백, 포렌식, 결과 품질) — 현행 query_log/forensics/requests 의 정규화된 뷰.
- Semantic memory = 반복 에피소드에서 **통합(consolidation)** 된 지식: query_rules 항목, 별칭, 관계, pin, 위키 노트 — 기존 proposals/applied 가 이 역할. 통합 잡(`memory consolidate`) 이 유사 에피소드를 묶어 제안 신뢰도를 올린다.
- Decay: 제안·규칙·pin 에 `last_reinforced, strength` 를 두고 시간에 따라 감쇠(반감기 `memory_half_life_days`), 재사용/긍정 피드백 시 강화. strength 가 임계 이하인 미승인 제안은 자동 보관(archive), 적용된 규칙은 **비활성화 제안**만 생성(자동 삭제 금지).
- 검색 boost: 긍정 피드백을 받은 청크에 decay 되는 소량 boost(`feedback_boost`, 토글) — 사용 기반 랭킹 학습의 안전한 최소 형태.
과도한 메모리 시스템(A-MEM 식 노트 그래프 진화 등)은 코퍼스 wiki 목적에 맞지 않아 제외.

### 3.14 한국어 상대 시간 파싱 — ✅
`timeparse.py`: 어제/오늘/그저께/지난주/이번주/다음주/지난달/이번달/N일전/N주전/N개월전/작년/올해/상반기/하반기/Q1~Q4/YYYY년 M월/M월 D일/최근 N일/`2026-08` 등 → `[from, to]`. 지역: `timezone`(기본 `Asia/Seoul`), 주 시작 요일(기본 월), 기준 시각 주입(테스트용). 적용: `time_scope` 단계가 질의에서 시간 표현을 제거한 검색 질의 + 날짜 범위를 만들고, `doc_meta.date`(없으면 파일명 날짜 → mtime) 로 **boost**(기본, `time_mode=boost`) 또는 **filter**(`time_mode=filter`, 0건이면 boost 로 자동 완화). 시간 표현은 그래프 date 노드 매칭에도 사용. 규모: 소.

### 3.15 그래프 노드 ↔ 원본 문서 — 🔵 보강
`doc_refs` 가 이미 있으므로 (a) `graph_search` 가 확장 엔티티의 doc_refs 상위 문서를 **문서 단위 후보**로 추가(청크는 `first_chunk` + 해당 문서 내 키워드 최다 청크), (b) 컨텍스트에 "관련 문서" 목록을 근거로 표기, (c) MCP `wiki_entity/wiki_doc` 에 노출. 요청자의 판단("원본 문서를 바로 찾아야 품질이 오른다")에 동의하며, 특히 ID 노드(ISSUE-xxxx) 는 문서 자체가 정답인 경우가 많다.

### 3.16 결정적 관계 + provenance/confidence — ✅
- `relations.provenance ∈ {explicit, rule, cooccur, llm, human}` 컬럼 추가(기존 `source` 는 세부 출처 유지): explicit = front matter `related.*`, rule = `rules.json` 의 ID 패턴/정규식(예: 본문 "ISSUE-2041 수정", "CL 55321 반영"), cooccur = 공동 출현, llm = LLM 추출, human = 승인된 제안/위키 노트.
- 결정적 규칙 정의(`rules.json → id_patterns`, `link_rules`): `{"type":"issue","regex":"ISSUE-\\d{3,6}"}`, `{"type":"cl","regex":"CL[- ]?\\d{4,7}"}`, `{"when":{"doc_type":"cl"},"match":"issue","rel":"fixes"}`, `{"match":"cl","rel":"references"}` 등. 문서 유형·본문 위치(헤딩 `## 수정`) 조건도 지원.
- 탐색 가중: `provenance_w = {explicit:1.0, rule:0.9, human:0.9, llm:0.6, cooccur:0.3}` 튜닝 → graph_search gain 에 곱. 그래프 UI/위키/MCP 에 provenance·confidence 표시, `graph --provenance explicit` 필터.
- 같은 (src,rel,dst) 가 여러 provenance 로 관측되면 confidence 를 결합(최대값 + 관측 수 보너스) 한다.

### 3.17 단계별 가중치·fusion 방식 비교 — ✅
현재 `rrf | weighted(min-max/50)` 두 가지. 제안:
- `fusion_method ∈ {rrf, wrrf(가중 RRF, 현행), minmax, zscore, dbsf(3σ 정규화), rrf_boost}` 를 하나의 `fusion.py` 로 통합. 모든 방식에서 채널 가중치(라우터 산출 × 사용자 `channel_w`) 적용.
- **post-fusion boost**(방식 무관, 곱셈): doc_type boost(`doc_type_w` 맵 + 라우터 힌트), recency(`time_scope` 결과·`recency_half_life_days`), pin, provenance(그래프 후보의 관계 출처), feedback(§3.13), 다중 채널 합의(현행 `fusion_multi_bonus`).
- 권장 기본값: **RRF + boost** (스케일 무관·안정, OpenSearch 벤치마크 기준 정규화 대비 NDCG -3~4% 수준이지만 튜닝 없이 강건). 정규화 계열은 `fusion compare`(평가셋을 방식별로 실행해 hit@k/MRR/groundedness/지연을 표로) 로 비교 후 선택. 캘리브레이션(채널 점수→확률) 은 학습 데이터가 없어 보류하고 zscore 로 대체.

### 3.18 eval 경로 = 사용자 경로 — 🔵 보강
이미 `evaluate()` → `query()` 이며 Web/CLI/MCP 도 같은 함수를 쓴다. 보강: eval 행마다 `request_id` 저장(포렌식 연결), `eval --preset`, deep 모드 평가, claim/groundedness 지표를 eval 에 포함, eval 중 캐시 우회 옵션.

### 3.19 regression / trial 비교 시스템 — ✅
- `trials` 테이블: `trial_id, name, ts, build_version, config_snapshot(설정+튜닝+preset+프로바이더), questions_hash, summary(지표), rows(질문별), request_ids, note`.
- 지표: hit@k, MRR, term_recall, answer_term_recall, **groundedness**(claim 지원율), **citation_precision**(인용된 근거 중 실제 지원 비율), **insufficient_rate**, avg/p95 latency, tokens/query, embed coverage, fallback 발동률.
- CLI `trial run --name A [--preset quality] [--set k=v ...]`, `trial list`, `trial compare A B [C D]`(지표 Δ, 질문별 승/패/동률, 설정 diff, 추천 요약), `trial report A --md`. Web Quality 화면: trial 선택 2~4개 → 나란히 표 + 질문별 히트맵 + 설정 diff + 각 질문 request 프로파일 링크.
- evolve 의 `eval_before/after` 도 trial 로 기록해 하나의 시스템으로 통합.

### 3.20 Generic Headless Agent Provider — ✅
설계(`HeadlessAgentLLM(BaseLLM)`, `agents.json`):
```json
{"opencode": {
  "command": ["opencode", "run", "--format", "json", "-m", "{model}", "{prompt}"],
  "prompt_mode": "arg",          "// or": "stdin | file (프롬프트를 임시 파일로 쓰고 경로 전달)",
  "files_flag": "-f",            "// 첨부 파일 경로마다 -f <path>",
  "output": "ndjson",            "// ndjson | json | text",
  "text_paths": ["part.text", "text", "content"],  "// 이벤트에서 텍스트를 뽑는 키 후보",
  "final_marker": null, "timeout_s": 300, "cwd": "{project_root}", "env": {"OPENCODE_QUIET":"1"},
  "max_output_chars": 200000 }}
```
동작: system+user 를 하나의 프롬프트(JSON 출력 지시 포함) 로 합쳐 subprocess 실행 → stdout 의 ndjson 이벤트에서 텍스트를 순서대로 결합 → `parse_json` 으로 구조화 결과 회수 → `{"text","usage"(있으면),"ms","model"}`. 실패/타임아웃은 `LLMError` 로 기존 폴백 경로를 탄다. `llm_roles.<role>.provider = "headless:opencode"` 로 역할마다 선택. evidence/context 는 프롬프트에 인라인하거나 크면 임시 파일로 첨부(`-f`). 같은 템플릿으로 `claude -p --output-format json`, `codex exec --json`, `gemini -p` 도 등록 가능(제너릭). 현재 PC 에 `opencode` 가 없으므로 **mock 스크립트로 테스트**하고, 실제 이벤트 스키마는 첫 실행 로그로 확인해 `text_paths` 를 보정한다.

### 3.21 Web UI IA 재설계 + preset — ✅
현재 15개 탭은 기능 나열이다. 워크플로 기준 7그룹으로 재구성(기존 패널 코드는 재사용):
| 그룹 | 화면 | 포함 |
|---|---|---|
| Ask | 질의·답변·근거·포렌식·피드백·pin·deep 모드·preset 체크박스 | Query + Search Debug + Forensic |
| Corpus | 빌드/증분/스케줄·임베딩 진행률·coverage·문서 목록·lint·MCP 소스·watcher | Build + System(일부) + 신규 |
| Knowledge | 그래프(provenance 필터)·엔티티·위키·관계 규칙 | Graph + Wiki + Rules |
| Quality | 평가·trial 비교·포렌식 집계·fusion 비교 | Eval + 신규 Trials |
| Evolve | 제안 승인·메모리(에피소드/강도)·evolution_log | Evolve |
| Settings | 프로바이더/모델·토글·튜닝·preset·query_rules·테마·config | Models + Tuning + Config |
| Observability | 요청 프로파일·로그·아키텍처 플로우·시스템 규모 | Requests + Architecture + System + 신규 Logs |
Preset: `presets.json` 에 `quality`, `speed`, `token`, `offline`, `deep_research` 등 — 각 preset 은 토글/튜닝/config 변경 집합. Ask 화면 상단 체크박스(복수 선택 시 우선순위 규칙: 뒤에 선택한 것이 충돌 키를 덮음, 충돌 표시) + `preset apply quality [--save]` / `preset show` / `preset diff`. 콘솔 탭은 Observability 에 유지(CLI 전체 실행).

### 3.22 답변 가이드 md + 구조화 답변 — ✅
`prompts/answer_guide.md`(답변 구조·문체·인용 규칙·길이 원칙·"근거 밖 지식 금지"), `prompts/expand.md`, `prompts/evidence_check.md`, `prompts/claim_check.md`, `prompts/forensic.md`, `prompts/review.md`, `prompts/extract.md`. 로더가 시작 시 읽고 mtime 변경 시 재로드. 답변 형식: ① 핵심 답변(2~4문장) ② 상세 설명(근거별 소제목, 단계/원인/조치 등 질문 유형에 맞는 구조) ③ 근거 표(C#, 문서 id, 유형, 날짜, 무엇을 지지하는지) ④ 미확인/추가 조사 필요 항목 ⑤ (있으면) 상충 정보. `answer_length_target`(short/normal/long, deep 모드는 long) 튜닝. 길이 포렌식: 컨텍스트 글자수 대비 답변 글자수, max_tokens 도달 여부, 인용 수, 미지원 문장 수를 단계별로 기록해 "너무 짧음(근거 부족/컨텍스트 잘림)" "너무 김(근거 밖 서술/반복)" 을 진단.

---

## 4. 참고한 최신 동향 (설계 근거)
- Agentic RAG(2026): 계획→검색 오케스트레이션→다중 홉→자기 반성 4요소가 표준화됐고, 실제 장애 1순위는 **over-retrieval 루프**(8~12회 재검색) → 본 계획의 attempt/token/latency 예산 강제의 근거. ([FutureAGI](https://futureagi.com/blog/agentic-rag-systems-2025/), [Adaptive RAG 비교 연구](https://arxiv.org/html/2606.05658v1))
- 인용 검증: 답변 전체 groundedness 가 아니라 **claim 단위로 인용 근거가 실제 지지하는지**(NLI/LLM 판정) 평가하는 것이 2026 표준 → claim_check 설계. ([citation/attribution 평가](https://futureagi.com/blog/evaluating-llm-citation-attribution-2026/), [MedRAGChecker](https://arxiv.org/pdf/2601.06519))
- 융합: RRF 는 스케일 무관·강건하나 튜닝된 정규화(convex combination) 대비 NDCG 3~4% 낮을 수 있음 → 기본 RRF + 비교 도구 제공. ([OpenSearch RRF](https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/), [ACM TOIS fusion 분석](https://dl.acm.org/doi/full/10.1145/3596512), [정규화 문제 해설](https://avchauzov.github.io/blog/2025/hybrid-retrieval-rrf-rank-fusion/))
- 리랭크 API: Cohere/Jina 포맷이 사실상 표준이며 vLLM 이 `/v1/rerank` 로 동일 포맷 제공. ([vLLM rerank PR](https://github.com/vllm-project/vllm/pull/12376), [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/v0.9.2/serving/openai_compatible_server.html))
- 에이전트 메모리: Ebbinghaus 감쇠(MemoryBank/YourMemory), A-MEM 의 노트 진화, Mem0 벤치마크 — 코퍼스 wiki 에는 **자가진화 층의 신뢰도 감쇠·강화**만 차용. ([Mem0 State of Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026), [YourMemory decay](https://letsdatascience.com/news/yourmemory-adds-ebbinghaus-decay-to-agent-memory-4848cdb5), [Agent memory survey](https://github.com/Shichun-Liu/Agent-Memory-Paper-List))
- 한글 FTS: SQLite 기본 토크나이저는 CJK 에 약하고 trigram 은 색인 팽창, bigram/형태소 전처리가 권장 → 현행 사전 토큰화 유지 + 선택적 kiwi + trigram 폴백. ([FTS5 문서](https://www.sqlite.org/fts5.html), [trigram trick](https://github.com/tkys/sqlite-fts5-trigram-trick), [CJK bigram 확장](https://github.com/yodhcn/sqlite3-groonga-bigram))
- opencode headless: `opencode run --format json`(ndjson 이벤트), `-m provider/model`, `-f 파일`, `--attach` 로 서버 재사용. ([OpenCode CLI 문서](https://opencode.ai/docs/cli/), [명령 정리](https://www.mager.co/blog/2026-08-09-opencode-cli-commands/))

---

## 5. 확인이 필요한 결정 사항 (Decision Points)

| ID | 질문 | 권장안 |
|---|---|---|
| D1 | Python 최소 버전: 3.7 호환 유지 vs 3.9+ | **3.9+ 최소**(현재 3.14 사용). 표준 라이브러리 위주 원칙은 유지 (porting 용이) |
| D2 | 문서 스키마 형식: YAML front matter vs JSON sidecar | **front matter** (내장 미니 YAML 파서, pyyaml 있으면 사용) |
| D3 | Mango MCP: tool 이름/입출력 스키마 제공 가능한가? | 제공 전까지 범용 클라이언트 + mock 으로 구현, 매핑 파일만 나중에 작성 |
| D4 | pin / precompute 의미가 §3.10 해석과 같은가? | §3.10 (a)+(b) 모두 구현 |
| D5 | 증분 빌드 스케줄: OS 스케줄러 + CLI 권장, 서버 watcher 는 보조 | 권장안 + 빌드 락 + `setup/schedule_build.ps1|sh` 제공 |
| D6 | Web UI 재구성 범위: 워크플로 7그룹 전면 재배치(기존 패널 재사용, 프레임워크 없음) | 전면 재배치 |
| D7 | 한글 형태소 분석기(kiwipiepy) 선택적 플러그인 도입 | 도입(미설치 시 자동 폴백) |
| D8 | claim 검증 기본값: 휴리스틱(무료) 기본 + LLM 판정 토글 | 권장안 |
| D9 | opencode 가 이 PC 에 없음 → mock 으로 테스트, 실제 스키마는 첫 실행 시 보정 | 동의 여부 |
| D10 | 메모리/decay 는 §3.13 경량 범위로 한정 | 권장안 |
| D11 | 기본 fusion: RRF + post-boost, 비교 도구로 선택 | 권장안 |
| D12 | 내용 해시 임베딩 캐시(rename 시 재임베딩 0) 채택 | 채택 |
| D13 | 합성 모뎀 샘플 코퍼스(6유형, 30~40문서) 와 평가셋을 만들어 테스트/데모에 사용 | 채택 (실제 코퍼스 경로는 `config.json` 에서 교체) |
| D14 | 기본 임베더: 현재 hash 4096d → `embed_dim=1024` 로 낮추고, Ollama `bge-m3` 가 있으면 자동 선택 | 권장안 |
| D15 | 구현 순서: 계획서 Phase 0→8 순 (기반 → 프로바이더 → 빌드 → 검색 → 답변/루프 → 진화 → 평가 → UI → 문서) | 권장안 |
