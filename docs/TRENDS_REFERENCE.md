# 참고: 글로벌 LLM Wiki RAG 아키텍처 트렌드 (사용자 제공 보고서 요약)

본 프로젝트 설계에 반영한 핵심 트렌드 5가지:

1. **하이브리드 검색(FTS + Vector) + Reranking이 프로덕션 표준** → `retrieval.py`의 RRF 융합 + 리랭크 단계.
2. **GraphRAG 경량화 (LightRAG / LazyGraphRAG / DRIFT)** → 커뮤니티 요약 대신 *엔티티 + 청크 이중 검색(dual-level)* 을 기본으로, 커뮤니티 요약은 옵션.
3. **배치 리빌드 → 증분/이벤트 기반 리빌드** → 문서 해시 기반 델타 빌드(변경 문서만 재청킹/재임베딩/재추출), Topology Drift 최소화.
4. **데이터 중심 그래프(DTG)** → 본 프로젝트는 문서 코퍼스 대상이라 직접 적용 대상은 아니나, 그래프 스키마를 (노드=상태/개체, 간선=변환/관계, provenance) 형태로 확장 가능하게 설계.
5. **자가 진화(Self-Evolving) + 통제 샌드박스 + HITL** → `evolve.py`: 제안(proposal) 큐 → 스테이징 적용 → 회귀 평가(eval set) 통과 시 승격, 자동 승격은 임계값+정책으로 제한.

리스크 대응 반영:
- 자가 진화 루프 내 악성/결함 주입 → 제안은 *데이터(별칭·동의어·관계·청크 경계)* 만 수정 가능, 코드는 수정 불가. 모든 제안은 evolution_log에 서명(해시)과 함께 기록, 롤백 가능.
- 환각 → 답변은 항상 청크 인용([doc#chunk]) 강제, 근거 없는 문장은 UI에서 구분.
- 그래프 인덱싱 비용 → 규칙 기반 추출은 항상 실행(무료), LLM 추출은 변경 문서에만 선택 실행.

출처(보고서 인용): Microsoft Research GraphRAG(2024-11-25), AWS Bedrock KB GraphRAG GA(2025-03-07), Datawalk "Does MCP replace GraphRAG".
