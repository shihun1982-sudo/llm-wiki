# 구현 계획 (PLAN)

> 1차 구현 계획입니다. 2차(v2) 변경 내역과 요청별 대응은 [docs/ARCHITECTURE_V2.md](docs/ARCHITECTURE_V2.md), 튜닝 파라미터는 [docs/TUNING.md](docs/TUNING.md) 참조.

## 0. 목표
- 코퍼스(Chapter2/data 마크다운 16개 + practice3 PDF 3편·브리핑 md·대시보드 html)를 색인하고
  **FTS + 벡터 + GraphRAG** 하이브리드로 검색·답변한다.
- 그래프는 **파이썬 규칙 기반**(무료·결정적) + **LLM 기반**(선택) 두 경로로 구축·병합한다.
- 데이터/인덱스 결함을 **self-evolving** 으로 보정한다 (제안 큐, 회귀 평가, 롤백, HITL).
- **Web UI** 에서 CLI 전체를 실행·확인하고, 기능별 on/off 와 단계별 프로파일을 볼 수 있다.

## 1. 환경 제약 & 결정
| 항목 | 상태 | 결정 |
|---|---|---|
| Python | 3.7.4 만 존재 (3.12 설치는 보류) | 3.7 호환 코드, 표준 라이브러리 위주 |
| anthropic SDK | 3.7 설치 불가 (jiter ≥3.8) | urllib 로 Messages API 직접 호출, 3.9+ 면 SDK 자동 사용 |
| API 키 | 없음 | 모든 LLM 단계는 옵션 + 폴백(추출식 답변, 로컬 리랭크), `--llm mock` 으로 배선 검증 |
| 임베딩 | sentence-transformers 미설치 | 로컬 해시 n-gram 임베딩 기본, Voyage/Ollama/ST 플러그인 |
| 웹 | fastapi 미설치 | http.server(ThreadingHTTPServer) + 바닐라 JS |

## 2. 단계별 계획 (1차 구현 — 완료)
1. **스캐폴딩**: config(토글), profiler(trace 트리), textutil(한국어 조사·bigram 토크나이저)
2. **코퍼스/청킹**: md/txt/html/pdf 로더, 헤딩 인지 청커, sha1 해시(증분)
3. **저장소**: SQLite 단일 파일 — chunks_fts(FTS5), embeddings(BLOB), entities/relations/mentions/communities, query_log, proposals, evolution_log, synonyms
4. **그래프 빌드**: 규칙 추출기(사전+정규식: 담당/마감/금액/참석/출처/애널리스트/결정 D#/공동출현) → LLM 추출기(JSON) → 병합(별칭 해소) → degree → label propagation 커뮤니티 → (LLM) 요약
5. **검색**: 라우터(질의 유형별 가중치) → FTS(BM25, 동의어 확장, bigram 폴백) / 벡터(cosine) / 그래프(엔티티 매칭→n-hop 확장→멘션 청크+관계 근거) → RRF 융합 → 리랭크(LLM/로컬)
6. **답변**: LLM(인용 [C#] 강제, 상충 시 병기) / 추출식 폴백
7. **위키**: 엔티티 페이지 + INDEX, 편집 노트 보존·overlay 색인
8. **자가 진화**: capture(갭 탐지) / feedback / llm_review → proposals → apply(스냅샷→적용→리빌드→회귀평가→롤백/승격) → evolution_log
9. **CLI + Web UI**: argparse 단일 진입점, Web 콘솔이 CLI 를 in-process 실행, 토글·프로파일 워터폴·그래프 캔버스
10. **평가/테스트**: eval/questions.json (12문항) hit@k·MRR·term recall, matrix 비교, unittest

## 3. 검증 결과 (2026-09-11, Python 3.7, API 없음)
- build --full: 21 docs / 224 chunks / 134 entities / 2,248 relations / 3 communities, 7.5 s
- eval (all 채널): hit@5 = 1.0, MRR 0.74, term recall = 1.0 · 채널별 hit@5/MRR: fts 1.0/0.74 · vector 1.0/0.70 · graph 1.0/0.82 · fts+graph 1.0/0.78
  (그래프 확장의 허브 점수 폭증 버그를 수정하기 전에는 graph 단독 0.33 이었음 — 홉별 스냅샷·차수 정규화·상한으로 해결)
- evolve: 부정 피드백 → wiki_note 제안 → apply(회귀평가 동등) → overlay 문서 재색인 확인
- Web API: query/build job/eval matrix/cli/graph/wiki 정상

## 4. 2차 (품질 개선) 계획 — MULTI_AGENT.md 참조
- 그래프 검색 강건성: 평가셋 확장 후 관계 유형 가중치, 엔티티 매칭 재현율, 시드 확장 감쇠 튜닝 (현재 12문항에서는 hit@5 1.0)
- 커뮤니티 과밀(co_occurs) 해소: 가중치 임계값/Leiden 대체, 커뮤니티 요약 검색 채널 추가(LazyGraphRAG 식 지연 요약)
- 실제 LLM 연결 후: 추출 스키마 정합성, 인용 정확도, 리랭크 효과, 비용/지연 프로파일
- 자가 진화: LLM 리뷰 제안 품질 평가셋, 자동 승인 임계값 캘리브레이션, 스냅샷 보존 정책
- 인프라: Python 3.11+ 로 이전 시 anthropic SDK·sentence-transformers·FastAPI 전환(프로바이더 인터페이스 그대로)
