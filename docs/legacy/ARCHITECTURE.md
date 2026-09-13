# 아키텍처 및 장·단점

> 1차 구현 시점의 문서입니다. v2(역할별 LLM, stat_skip 증분·워처, 노드 doc_refs, 성능 토글, 단계별 프로파일/요청 기록, 튜닝 레지스트리, Architecture·Tuning 탭, MCP) 는 [docs/ARCHITECTURE_V2.md](docs/ARCHITECTURE_V2.md) 를 보세요.

## 1. 전체 구조

```
                ┌──────────────── BUILD (python -m llmwiki build) ────────────────┐
 corpus dirs ─► load_corpus ─► diff(sha1) ─► chunk_index(FTS5) ─► embed ─► graph_build ─► wiki_pages
 wiki/편집노트 ┘   (md/pdf/html)   증분/전체      헤딩 인지 청킹     hash|voyage   ┌ rule_extract (사전+정규식)
                                                                                ├ llm_extract  (JSON, 옵션)
                                                                                ├ merge/alias resolve
                                                                                ├ degrees → label-propagation 커뮤니티
                                                                                └ community summary (LLM, 옵션)

                ┌──────────────── QUERY (python -m llmwiki query) ────────────────┐
 question ─► router ─┬► fts_search   (BM25 + 동의어 + bigram 폴백) ─┐
   (유형별 가중치)     ├► vector_search(cosine)                       ├► rrf_fuse ─► rerank(LLM|local) ─► context ─► answer(LLM|extractive)
                      └► graph_search (엔티티 매칭→n-hop→멘션 청크+관계) ┘                                    │
                                                                                                        ▼
                ┌──────────────── EVOLVE ─────────────────────────────────────────────────────────────────┐
 query_log ─► capture(갭 탐지) ─┐                                                                          │
 feedback(👍/👎+정정) ──────────┼─► proposals(kind, payload, confidence) ─► [HITL 승인 | auto ≥ θ] ─► apply: │
 llm_review(로그 검토) ─────────┘         synonym/alias/entity/relation/wiki_note/chunk_params              snapshot → 적용 → (리빌드) → 회귀평가 → 승격 or 롤백 → evolution_log
```

**저장소**: SQLite 단일 파일(FTS5 가상 테이블 + 임베딩 BLOB + 그래프 테이블 + 로그). **프로파일러**: 모든 단계가 `Profiler.stage()` 로 감싸져
trace 트리(ms, 입력/출력 요약, skipped 사유)를 만들고 CLI `--trace` / Web 워터폴로 표시. **토글**: `Toggles` dataclass 하나가 CLI 플래그·Web 체크박스·config.json 을 통일.

## 2. 구성 요소별 설계와 장·단점

### 2.1 FTS (SQLite FTS5 + 자체 한국어 전처리)
- 설계: unicode61 토크나이저의 한국어 한계를 보완하기 위해 `tokens` 컬럼에 (원형 + 조사 제거형 + 문자 bigram) 을 사전 토큰화해 저장. BM25 가중치(heading 2.0, body 1.0, tokens 1.5). 동의어 테이블로 질의 확장.
- 장점: 의존성 0, 수치·고유명사·날짜 정확 매칭에 강함(평가셋 hit@5 1.0), 밀리초 응답, 스니펫 하이라이트.
- 단점: 형태소 분석기가 아니라 조사 규칙이 근사적(복합어·활용형 누락 가능). 의미 유사 표현("생산능력" vs "캐파")은 동의어 등록 전엔 놓침 → 자가 진화의 synonym 제안이 이 갭을 메움.

### 2.2 Vector
- 설계: `BaseEmbedder` 플러그인. 기본 `hash`(문자 n-gram 해싱 + IDF + L2, 4096차원, 오프라인). `voyage`/`ollama`/`st` 는 설정만으로 교체. 행렬을 메모리에 캐시해 numpy dot 으로 cosine.
- 장점: 오프라인·결정적·빠름. 프로바이더 교체 시 코드 변경 없음. 청크 ≤ 수만 개면 ANN 인덱스 불필요.
- 단점: hash 임베딩은 **의미 임베딩이 아니다**(표기 유사성 기반). 실서비스에선 Voyage/다국어 ST 모델로 교체 필요. 대규모(≫10만 청크)에서는 FAISS/sqlite-vec 같은 ANN 필요.

### 2.3 GraphRAG (규칙 + LLM, LightRAG 식 이중 검색)
- 설계:
  - **규칙 추출기**: 외부화된 사전(`data/rules.json`: 정규명·유형·별칭) + 도메인 정규식(담당/마감/금액/참석/출처/애널리스트 코멘트/결정 D#/날짜/금액) + 거리 가중 공동출현. 문서 자체를 노드로 두어 문서↔엔티티 연결.
  - **LLM 추출기**: 고정 스키마 JSON, "이미 알려진 엔티티" 를 넘겨 정규화 유도, 별칭·FTS 로 기존 노드에 해소. provenance(rule/llm/evolve) 와 confidence 보존.
  - **검색**: 질의→엔티티 매칭(FTS+별칭 정확일치 보너스)→n-hop 확장(감쇠×관계 가중)→시드 다중 멘션 청크 우선 + 관계 근거 청크 보너스. 커뮤니티 요약은 옵션(기본은 요약 없이 엔티티/청크 이중 검색 → 트렌드 보고서의 LightRAG/LazyGraphRAG 방향).
- 장점: 규칙 경로는 비용 0·재현 가능·설명 가능(어떤 규칙이 만든 관계인지 추적). 관계형 질의("누가 담당", "영향")에서 결정·담당·마감 같은 구조 정보를 직접 근거로 끌어옴. LLM 경로는 변경 문서에만 실행되어 비용 통제.
- 단점: 규칙 사전은 도메인 종속(새 코퍼스면 사전 보강 필요 → 자가 진화 alias/entity 제안으로 점진 보강). 공동출현 간선이 많아 커뮤니티가 과밀(현재 3~4개). 허브 노드(역할·문서)가 확장 점수를 지배하기 쉬워 홉별 스냅샷·차수 정규화·상한이 필수였음(수정 전 graph 단독 hit@5 0.33 → 수정 후 1.0). Label propagation 은 Leiden 대비 품질 낮음.

### 2.4 융합·라우터·리랭크
- 설계: RRF(k=60) 에 채널 가중치. 라우터는 휴리스틱(키워드 수, 엔티티 수, 관계어, 숫자 포함)으로 keyword/relational/semantic/hybrid 분류. 리랭크는 LLM 가능 시 JSON 순위, 아니면 커버리지+다채널 합의+길이.
- 장점: 채널을 독립적으로 on/off 해도 안전(빈 리스트 허용), 각 히트에 `why`(fts#1, vector#4 …) 가 남아 설명 가능. 라우터 결정이 trace 에 기록됨.
- 단점: 라우터가 학습 기반이 아님. 로컬 리랭커는 cross-encoder 대비 약함(트렌드 보고서의 BGE-M3 류 도입은 Python 3.8+ 필요).

### 2.5 답변 생성
- 설계: 시스템 프롬프트에 `[C#]` 인용 강제, 상충 병기, 미확인 명시. LLM 불가 시 추출식(키워드 커버리지 문장 + 인용).
- 장점: 인용 없는 문장을 UI 에서 식별 가능, LLM 없이도 항상 근거 있는 출력.
- 단점: 추출식은 문장 나열 수준. LLM 답변 품질은 미검증(키 없음).

### 2.6 Self-Evolving
- 설계: (1) **capture** — 그래프 시드 없음/융합 점수 낮음/인용 없음을 갭으로 기록해 alias·entity·synonym·wiki_note 제안 (2) **feedback** — 부정+정정 텍스트는 confidence 0.9 wiki_note (사람 입력=고신뢰) (3) **llm_review** — 로그 기반 데이터 수정 제안 (4) **apply** — 스냅샷(DB/rules/wiki/config) → 적용 → 필요 시 리빌드 → 평가셋 회귀 → 악화 시 자동 롤백, 모두 evolution_log 에 체크섬과 함께 기록. 기본 HITL, 자동 승인은 임계값(0.8) 이상만.
- 장점: 제안은 **데이터만** 수정(코드/프롬프트 불변) → 트렌드 보고서의 "자가 진화 루프 내 악성 논리 주입" 리스크 차단. 평가셋이 안전망. 위키 편집 노트가 사람의 지식을 색인에 흘려 넣는 자연스러운 채널.
- 단점: 갭 탐지 휴리스틱의 정밀도가 낮아 저신뢰 제안이 쌓일 수 있음(HITL 이 필요한 이유). 회귀 평가셋이 작으면(12문항) 미세 악화를 못 잡음. 스냅샷은 파일 복사라 대규모 DB 에선 비용.

### 2.7 Web UI / CLI
- 설계: argparse 단일 진입점을 Web 콘솔이 in-process 로 실행(`run_captured`). 사이드바 토글은 요청 단위 오버라이드(`_with_overrides`)로 적용 후 복원, 동등 CLI 명령을 항상 표시. 빌드/평가는 백그라운드 job + 폴링. 그래프는 캔버스 force-layout(외부 라이브러리 없음).
- 장점: 의존성 0, CLI 와 Web 기능 집합 동일 보장, 모든 응답에 trace 동봉.
- 단점: 단일 프로세스·전역 락(동시 요청 직렬화), 인증 없음(로컬 전용), 스트리밍 없음.

## 3. 트렌드 보고서 대비 매핑
| 트렌드 | 본 구현 | 미구현/차이 |
|---|---|---|
| 하이브리드 + Reranker 표준 | FTS+Vector+Graph RRF, LLM/로컬 리랭크 | cross-encoder 리랭커(BGE-M3) 는 3.8+ 환경에서 플러그인 예정 |
| GraphRAG 경량화(LightRAG/LazyGraphRAG) | 엔티티+청크 이중 검색, 커뮤니티 요약 옵션 | 지연(lazy) 커뮤니티 요약 검색 채널 없음 |
| 실시간/증분 리빌드 | 문서 해시 diff, 변경 청크만 재추출·재임베딩 | 스트리밍(파일 감시/Kafka) 없음 — 폴링 빌드 |
| DTG(데이터 변환 그래프) | 관계 provenance/weight 스키마로 확장 가능 | 코드 코퍼스 미대상 |
| 자가 진화 + 샌드박스 + HITL | 제안 큐·스냅샷·회귀평가·롤백·데이터 전용 수정 | 서명/격리(프로세스 샌드박스) 없음 — 체크섬 로그 수준 |
| MCP 통합 | 없음 | JSON API 를 MCP 서버로 감싸면 됨(후속) |

## 4. 알려진 한계 (정직한 상태)
- LLM(anthropic) 경로는 키가 없어 **실제 호출 미검증** — mock 프로바이더로 배선만 검증. 3.7 에서는 raw HTTP, 3.9+ 에서는 SDK.
- hash 임베딩은 의미 검색이 아니므로 "vector 1.0" 결과는 이 코퍼스의 어휘 중복 덕. 실 의미 임베딩으로 교체 후 재평가 필요.
- 커뮤니티 수가 적고(3~4) 커뮤니티 요약 검색 채널이 없음 → 2차 개선 대상. 그래프 검색은 12문항 평가셋 기준이라 확장 평가셋에서 재검증 필요.
- PDF 는 pypdf 텍스트 추출 품질에 의존(표·그림 누락). 논문 3편은 발췌본.
