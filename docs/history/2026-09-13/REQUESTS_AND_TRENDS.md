# 참고 자료 원문 — 사용자 요청 프롬프트, 트렌드 보고서, 시스템 내부 LLM 프롬프트

이 문서는 `llm-wiki-rag-selfevolving` 을 만들 때 **입력으로 삼은 요청과 자료**를 그대로 보존하고,
각 항목이 어디에 반영됐는지 연결한 기록입니다. 반영 판정은 [legacy/DESIGN_REVIEW.md](../../legacy/DESIGN_REVIEW.md) 를 참조하세요.

- A. 사용자 요청 프롬프트 (원문, 2026-09-11)
- B. 트렌드 보고서 원문 (사용자 제공, 구조 복원)
- C. 요청/트렌드 → 구현 매핑 요약
- D. 시스템 내부 LLM 프롬프트 (코드에 내장된 4종)

---

## A. 사용자 요청 프롬프트 (원문)

### A.1 최초 요청

> llm wiki 를 구성하고 싶어. 임베딩할 코퍼스가 있어야 하고, 해당 코퍼스를 build 해서 indexing을 하는데, 사용자 요청에 fts + vecterization + graph rag 검색해서 답변을 내는 형태여야해. 코퍼스 기반으로 graph rag build 시에는 파이선 rule 기반과 llm 기반으로 작성하는 방식으로 하려고 해. 여기에 수정이 필요한 data 나 index에 대해서는 self evolving 할 수 있는 형태면 좋겠어.
>
> 전체적인 구조를 만들어 주고, 장/단점을 알려줘. 그리고 실제 동작하는 코드도 작성해줘.
> web ui 로 확인할 수 있으면 더 좋겠어. web ui 는 전체 CLI 를 모두 확인할 수 있는 형태가 되면 좋겠고. 위에 나열한 각 기능들을 하나하나 on/off 해 가면서 그 결과를 확인할 수 있고, 각 단계별로 어떠케 처리되는지 확인 할 수 있도록 해줘. 그럴려면 각 단계별로 profile 할 수 있는 장치가 있어야 할 것 같아.
>
> 코퍼스 :
> `C:\Users\user\Desktop\삼성전자_DS_보직장_CLAUDE_실습자료\삼성전자_DS_보직장_CLAUDE_실습자료\삼성전자_DS_보직장_CLAUDE_실습자료\Chapter2\data`
> `C:\Users\user\Desktop\삼성전자_DS_보직장_CLAUDE_실습자료\삼성전자_DS_보직장_CLAUDE_실습자료\삼성전자_DS_보직장_CLAUDE_실습자료\Chapter0\practice3`
>
> 작업 폴더 : `C:\Users\user\Desktop\llm-wiki-rag-selfevolving`
>
> 위 작업을 하기 위해서 plan 작성해주고, 1차 구현 후 품질 개선을 하기 위해 어떤식으로 prompt를 작성하여 multi-agent 를 구성하면 좋을지도 제안해줘.

### A.2 후속 지시

1. (Python 3.12 설치 시도 거부 후) 트렌드 보고서(아래 B) 전문을 붙여 넣으며:
   > 위와 같은 최신 트렌드가 있어, 구조 설계 및 구현에 참고해서 작업해줘
2. > 위 최신 트렌드도 반영한거지?
3. > http://127.0.0.1:8765/ 이거 동작 안하는데?
4. > 여기에 내가 요청한 모든 개념들과 최신 trend 가 모두 반영된거야? 상세히 전체 구조와 적용된 technique을 모두 도식에 표시해주고, 장/단 점과 개선점을 모두 나열해줘. md 로 만들어줘
5. > 이거 만들때 참고한 프롬프트 요청과 트렌드도 정리해서 별로 md 로 만들어줘
6. (2026-09-11 오후) Chapter3 `.claude/agents/CEO/data` 폴더를 코퍼스 경로에 추가 요청 → `config.json` 반영, CSV 지원 추가
7. 후속 6개 요청 (응답: [ARCHITECTURE_V2.md](ARCHITECTURE_V2.md)):
   > 1. 기본적으로 embedding, rerank, chat llm model 을 설정할 수 있어야 하는 것으로 알고 있어. 이 설정이 어떤식으로 되어 있는지 상세히 설명해줘.
   > 2. 그리고 graph rag 구성시에, 각 node 마다 문서 ref 에 대한 정보도 연결되어 있어? 이렇게 하면 나중에 본문을 찾을 때 좋다고 하더라고.
   > 3. 현재 3000 개의 md 파일이 있고, 매일 20개 정도의 새로운 md 파일이 추가된다고 했을때, 이 정도 시스템을 유지하기 위해서 이정도 확장에 충분한 system을 만들어줘.
   > 4. 성능/속도/토큰양 측면에서 개선점을 찾아서 on/off 할 수 있게 만들어줘.
   > 5. 각 단계별 디버깅을 할 수 있게 디버깅 정보와 프로파일 정보를 단계별로 넣어줘.
   > 6. 사용자의 각 요청에 대해서 디버깅 프로파일 정보를 상세히 확인할 수 있도록 해줘.
   >
   > 위 기능을 상세히 확인할 수 있게 web ui 작성해줘.
   > 다 완료되면, 어떤 request 에 대한 응답인지 포함해서 최종 구조를 도식화 해서 doc 폴더에 md 파일 작성해줘야해
8. (2026-09-11 저녁) 응답: ARCHITECTURE_V2.md §11~§14
   > 검색 품질을 높히기 위해서 적용가능할만한 개선 포인트가 더 있을까? 그리고 지금 서버 살아 있어? 추가로 REQUESTS_AND_TRENDS.md 여기에 있는 최신 트렌드들도 대부분 반영이 된거지? 또한 각 단계별로 튜닝할 수 있는 요소들이 있다면 상세 설명, impact, 예시와 함께 별도 파일에서 제어할 수도 있도록 하는건 어때? 이런식으로 상세히 검토해보고 필요하면 구현해줘
   > 추가로 CLI 및 web ui에서 각 LLM 모델을 설정할 수 있게 해주면 좋겠어. 또한 … 전체 구조와 flow에 대한 그림이 있고, 사용자 요청 / data build / evolving 그 필요한 flow에 대해서 구조에서 나타내주고, 각 CLI 와 feature on/off 가 어떠케 영향을 주는지 표시해주고 impact 을 표시해주면 … web ui 그런식으로 추가 확장해

### A.3 요청에서 도출한 요구사항 목록

| ID | 요구사항 | 키워드 |
|---|---|---|
| R1 | 임베딩할 코퍼스 지정 | corpus |
| R2 | 코퍼스 build → indexing | build, index |
| R3 | FTS 검색 | fts |
| R4 | Vectorization 검색 | vector |
| R5 | Graph RAG 검색 | graph rag |
| R6 | 그래프 빌드 — 파이썬 규칙 기반 | rule-based |
| R7 | 그래프 빌드 — LLM 기반 | llm-based |
| R8 | 수정 필요한 data/index 의 self-evolving | self-evolving |
| R9 | 전체 구조 + 장/단점 | architecture |
| R10 | 실제 동작하는 코드 | working code |
| R11 | Web UI | web ui |
| R12 | Web UI 에서 전체 CLI 확인 | cli parity |
| R13 | 기능 하나하나 on/off + 결과 확인 | toggles |
| R14 | 각 단계별 처리 확인 | stage visibility |
| R15 | 단계별 profile 장치 | profiler |
| R16 | Plan 작성 | plan |
| R17 | 품질 개선용 multi-agent 프롬프트 제안 | multi-agent |

---

## B. 트렌드 보고서 원문 (사용자 제공, 구조 복원)

> 원문은 한 덩어리 텍스트로 제공되었으며, 아래는 문단·표 구조를 복원한 것입니다. 내용은 수정하지 않았습니다.

### 글로벌 LLM Wiki RAG 아키텍처 설계 전략 및 반도체 탑재 SW 적용 기획 보고서

본 보고서는 글로벌 기업 환경에서의 최신 대규모 언어 모델(LLM) 기반 검색 증강 생성(RAG) 파이프라인 설계 전략을 분석합니다. 특히 최근 6개월간 급격한 기술적 진보를 이룬 전체 텍스트 검색(FTS), 벡터화(Vectorization), 지식 그래프 RAG(GraphRAG), 데이터 리빌드(Data Rebuild), 그리고 자가 진화(Self-Evolving) 메커니즘을 중심으로 핵심 변화를 추적하였습니다. 본 조사는 대시보드 제작을 위한 최소 정보 수집을 목적으로, 공식 IR 및 주요 기술 백서 등 최우선 신뢰 출처에 기반하여 작성되었습니다.

#### ① 핵심 변화 5가지 (최근 6개월 중심)

LLM RAG 아키텍처는 최근 6개월을 기점으로 단순 벡터 검색을 넘어 하이브리드 검색과 지식 그래프를 결합한 복합 추론 단계로 진입했습니다. 이러한 기술적 전환은 검색의 정확도와 맥락 이해도를 극대화하는 방향으로 전개되고 있습니다.

**첫째, 하이브리드 검색(FTS + Vectorization)이 프로덕션 환경의 기본 표준으로 완전히 정착했습니다 [사실].** 단순한 밀집 벡터(Dense Vector) 검색은 특정 키워드의 정확한 매칭에 취약하다는 한계를 보였으며, 이를 극복하기 위해 희소 검색(Sparse Retrieval, BM25/FTS)과 밀집 검색을 병행한 뒤 Cross-encoder 기반의 재정렬(Reranking)을 수행하는 구조가 2026년 RAG 파이프라인의 필수 요건이 되었습니다.

**둘째, 막대한 컴퓨팅 자원이 소모되던 GraphRAG 아키텍처가 구조적으로 경량화되고 있습니다 [사실].** 마이크로소프트의 초기 GraphRAG 모델은 청크 단위의 엔티티 추출과 계층적 커뮤니티 요약 과정에서 심각한 비용 병목을 유발했습니다. 이에 대한 대안으로 요약 단계를 생략하고 엔티티와 텍스트 청크 단위의 이중 검색을 수행하는 LightRAG 기법이나, 동적 커뮤니티 선택을 통해 연산 효율을 높인 LazyGraphRAG 및 DRIFT Search 아키텍처가 새로운 표준으로 부상하고 있습니다.

**셋째, 데이터 리빌딩 방식이 배치(Batch) 처리에서 이벤트 스트리밍 기반의 실시간 위상(Topology) 업데이트로 진화하고 있습니다 [사실].** 전통적인 시스템이 일정 주기마다 그래프를 재구축하여 런타임 위상 불일치(Topology Drift)를 겪었던 반면, 최근에는 Kafka와 같은 스트리밍 백본을 통해 텔레메트리 및 이벤트 로그를 실시간으로 인제스트(Ingest)하는 ES-GraphRAG(Event-Sourced Streaming GraphRAG) 패턴이 도입되어 인덱싱 지연 시간을 획기적으로 단축하고 있습니다.

**넷째, 소프트웨어 코드 유지보수 및 디버깅 영역에서 파일 중심의 제어 흐름 그래프(AST/CPG)가 데이터 중심의 변환 그래프로 대체되고 있습니다 [추정].** 복잡한 엔터프라이즈 환경 및 반도체 탑재 소프트웨어에서 결함의 원인은 다수의 파일과 추상화 계층에 분산되어 있습니다. 이에 대응하여 데이터 상태를 노드로, 함수 변환을 간선(Edge)으로 모델링하는 DTG(Data Transformation Graph)가 제안되었으며, 이는 AI 에이전트가 코드 내 데이터의 인과적 흐름을 자율적으로 추적하도록 돕습니다.

**다섯째, AI 에이전트의 자가 진화(Self-Evolving) 생태계 구축과 이를 지원하는 범용 통합 접근 프로토콜이 상용화 단계에 진입했습니다 [사실].** Anthropic 등이 주도하는 MCP(Model Context Protocol)는 다수의 AI 클라이언트가 표준화된 단일 인터페이스를 통해 지식 그래프 및 외부 도구에 접근할 수 있도록 통합 뷰를 제공합니다. 동시에 에이전트가 외부 지식 저장소에서 스킬을 검색하고 스스로 새로운 도구를 작성하여 시스템 구조를 개선하는 Stage III(다중 에이전트 팀) 및 Stage IV(자가 진화 생태계)로의 전환이 가속화되고 있습니다.

#### ② 경쟁사별 전략 비교표

글로벌 클라우드 및 AI 벤더들은 각사의 인프라 강점을 활용하여 RAG 아키텍처의 주도권을 확보하기 위해 차별화된 전략을 전개하고 있습니다.

| 기업명 | FTS + Vectorization (하이브리드 검색) | Graph RAG 전략 | Data Rebuild & Self Evolving 파이프라인 |
|---|---|---|---|
| Microsoft | Azure AI Search 기반 벡터+키워드 융합 및 Reranking 네이티브 지원 | 커뮤니티 탐색 최적화를 위한 LazyGraphRAG 및 DRIFT Search 도입 | GitHub 중심의 자율 이슈 해결(AIR) 에이전트 및 데이터 중심 코드 변환(DTG) 프레임워크 연구 주도 |
| AWS | Amazon OpenSearch Serverless를 통한 관리형 하이브리드 RAG 파이프라인 | Bedrock Knowledge Bases 내 GraphRAG GA 출시(Neptune Analytics 통합)로 벡터 검색과 그래프 순회 동시 처리 | 다중 에이전트 RAG 패턴을 통해 AI가 도구 및 데이터 소스를 자율적으로 결정하는 Agentic RAG 워크플로우 지원 |
| Google | Vertex AI Search를 활용한 BigQuery 통합 및 Google Search Grounding | Knowledge Graph와 대규모 모델의 결합 및 MCP(Model Context Protocol) 지원을 통한 그래프 범용 접근성 확보 | Universal Commerce Protocol(UCP) 기반 에이전트 자율 협상 및 복합 구성(Composite AI) 의사결정 실험 |
| NVIDIA | NeMo Retriever를 통한 하이브리드 기반 의미론적 라우팅 및 Cross-encoder 재정렬 | 다중 모달(Multimodal) 정보와 분산 로그를 결합한 RAG 워크플로우 고도화 | 에이전트 자가 수정(Self-corrective) 루프 및 NeMo Guardrails 기반의 멀티 에이전트 로그 분석 생태계 상용화 |

#### ③ '반도체 탑재 SW 품질 및 유지 관리' 측면에서의 기회와 리스크

반도체 탑재 소프트웨어(펌웨어, RTOS, 드라이버)는 일반 IT 소프트웨어 대비 하드웨어 의존성이 높고 실시간성 요구가 엄격합니다. 따라서 최신 RAG 아키텍처 도입 시 독특한 기회와 위험 요소가 공존합니다.

| 구분 | 분석 항목 | 설명 및 근거 |
|---|---|---|
| 기회 | 1. Zero-Touch 결함 추적 및 논리 수정 [추정] | 기존 텍스트 기반 RAG 대신 데이터 변환 그래프(DTG)를 반도체 펌웨어 레포지토리에 적용할 경우, 에이전트가 변수 데이터의 생애주기와 인과 관계를 추적하여 복잡한 메모리 참조 오류나 제어 흐름 결함을 인간의 개입 없이 자율적으로 패치할 수 있습니다. |
| 기회 | 2. 멀티 에이전트 기반 대규모 로그 분석 자동화 [사실] | 반도체 장비 및 테스트 환경에서 생성되는 이질적이고 방대한 텔레메트리 로그를 하이브리드 검색(BM25+Vector)과 멀티 에이전트 협업으로 분석하여, 병목 현상 탐지 및 근본 원인 분석(Root Cause Analysis)의 속도를 획기적으로 개선할 수 있습니다. |
| 기회 | 3. 자가 진화 모델을 통한 테스트 커버리지 확장 [추정] | 새로운 칩셋 아키텍처나 하드웨어 사양 변경 시, 자가 진화(Self-Evolving) 에이전트가 기존 검증(QA) 스킬 라이브러리를 학습 및 변형하여 새로운 통합 테스트 도구와 검증 시나리오를 자율적으로 생성할 수 있습니다. |
| 리스크 | 1. 자가 진화 루프 내 악성 논리 주입 취약성 [사실] | 에이전트가 자체 도구를 자동 생성할 때 외부에서 검색된 악의적이거나 결함이 있는 스킬이 템플릿에 주입될 경우, 페이로드가 지속 보존되어 반도체 빌드 파이프라인 전체를 오염시킬 위험이 높습니다. |
| 리스크 | 2. 하드웨어 제약 조건 상실에 따른 치명적 환각 [추정] | LLM이 지식 그래프의 논리적 제약(비즈니스 제약, 레지스터 맵 규칙 등)을 무시하고 확률론적인 답변을 생성할 경우, 실시간 운영체제의 안정성을 심각하게 저해하는 환각(Hallucination) 기반 코드가 하드웨어에 탑재될 수 있습니다. |
| 리스크 | 3. 그래프 인덱싱의 실시간성 한계 및 비용 병목 [사실] | 임베디드 CI/CD 환경에서는 코드가 빈번하게 변경됩니다. 그러나 순수 GraphRAG 방식은 단일 노드 변경 시에도 전체 커뮤니티 요약을 재구성해야 하므로, 기존 벡터 RAG 대비 최대 300배의 인덱싱 비용이 발생하고 배포 파이프라인의 극심한 지연을 초래합니다. |

#### ④ 향후 1년간 낙관·기준·위기 시나리오

AI RAG 기술의 발전 속도와 기업의 수용성에 따라 향후 1년간의 반도체 SW 환경 변화 시나리오는 다음과 같이 추정됩니다 [추정].

먼저 **낙관 시나리오(Optimistic Scenario)** 하에서는 LightRAG 및 ES-GraphRAG와 같은 경량화 및 스트리밍 그래프 아키텍처가 전면 상용화되어 인덱싱 비용과 지연 시간이 90% 이상 절감됩니다. 반도체 펌웨어 개발 환경 내에 다중 에이전트 팀(설계, 구현, 검증 에이전트)이 완벽하게 정착하며, 개발자의 개입이 거의 없는 제로 터치(Zero-Touch) 디버깅 파이프라인이 코드 수정 작업의 80%를 성공적으로 대체합니다.

**기준 시나리오(Baseline Scenario)** 에서는 FTS와 Dense 벡터, 그리고 Reranker를 결합한 하이브리드 RAG 시스템이 사내 표준 검색 인프라로 자리 잡습니다. GraphRAG는 전체 파이프라인에 적용되기보다, 칩셋 아키텍처 명세서나 복합 시스템 제약 조건 분석과 같이 다중 홉(Multi-hop) 추론이 필수적인 특정 도메인에 국한되어 도입됩니다. 에이전트 시스템은 '추론 및 추천' 역할을 담당하며, 최종 코드 반영 및 하드웨어 배포는 여전히 인간 엔지니어의 승인(Human-in-the-loop)을 거치게 됩니다.

반면 **위기 시나리오(Pessimistic Scenario)** 에서는 통제되지 않은 자가 진화 에이전트가 오염된 스킬을 펌웨어 메인 브랜치에 자동 병합하면서 대규모 하드웨어 오작동 이슈가 발생합니다. 또한, 실시간으로 변화하는 소프트웨어 형상을 GraphRAG 인덱스 업데이트 속도가 따라가지 못해 심각한 위상 불일치(Topology Drift) 현상이 누적되고, 막대한 인프라 유지보수 비용으로 인해 경영진이 AI 자율화 프로젝트 투자를 전면 중단하게 됩니다.

#### ⑤ 시나리오별 대응 과제

| 시나리오 | 최우선 대응 과제 | 기대 효과 |
|---|---|---|
| 낙관 시나리오 | MCP 표준 인터페이스 구축 및 다중 에이전트 협업(Agentic Team) 워크플로우를 사내 CI/CD 시스템과 공식적으로 통합합니다. | 복잡한 반도체 시스템 설계부터 검증까지 이어지는 End-to-End 자동화를 달성하여 개발 생산성을 비약적으로 향상시킵니다. |
| 기준 시나리오 | 라우터(Router) 에이전트를 도입하여 단순 질의는 벡터 인덱스로, 복합 질의는 지식 그래프로 분기하는 '적응형 검색(Adaptive Retrieval)' 파이프라인을 설계합니다. | 검색 품질과 연산 비용 간의 최적의 균형(Sweet Spot)을 확보하여 효율적인 AI 검색 인프라를 유지합니다. |
| 위기 시나리오 | NeMo Guardrails 등 AI 보안 레이어를 필수화하고, 모든 에이전트 코드 생성 과정에 DTG 검증 및 엄격한 Human-in-the-loop 정책을 강제하는 '제한적 샌드박스'를 구성합니다. | 할루시네이션 및 악성 페이로드에 의한 하드웨어 손상을 원천 차단하고 기업 자산 보호를 극대화합니다. |

#### ⑥ 즉시 실행할 우선순위 과제 3개

이러한 전략적 환경 분석을 바탕으로, 대시보드 인프라 구축 및 시스템 고도화를 위해 다음 세 가지 과제를 즉시 실행해야 합니다.

1. 가장 시급한 과제는 기존 순수 벡터 검색(Naive RAG) 인프라를 전면 폐기하고 **하이브리드 RAG(Dense + Sparse + Reranker) 파이프라인**을 구축하는 것입니다. 의미론적 유사도와 키워드 매칭(FTS)의 장점을 융합하고, BGE-M3 등 다국어 교차 인코더(Cross-encoder)를 통해 검색 결과의 상위 순위를 재정렬하여 프로덕션 레벨의 정밀도를 즉시 확보해야 합니다.
2. 두 번째는 반도체 환경의 특수성을 고려한 **소스 코드 기반 데이터 변환 그래프(DTG) 파일럿 프로젝트**의 신속한 도입입니다. 기존의 문서 중심 임베딩 방식을 탈피하여 펌웨어 및 드라이버 코드를 데이터 위주의 그래프로 재구축(Data Rebuild)함으로써, AI 에이전트가 코드의 제어 흐름 노이즈를 배제하고 결함의 인과적 흐름을 추적할 수 있는 기반을 마련해야 합니다.
3. 마지막으로, 향후 확장될 다중 에이전트 생태계를 대비하여 **MCP(Model Context Protocol) 호환 인터페이스 및 자가 진화 통제 샌드박스**를 설정해야 합니다. 사내 지식 그래프로 접근하는 모든 AI 클라이언트의 권한을 단일 프로토콜로 통합 관리하고, 에이전트가 새로운 스킬을 생성할 때 악성 논리가 전파되지 않도록 엄격한 서명 및 격리 정책을 적용해야 합니다.

#### [참고] 자료 부족 및 상충 내용 명시

- **[데이터 상충] GraphRAG 인덱싱 비용의 경제성**: 마이크로소프트의 공식 문서는 LazyGraphRAG 도입 등을 통해 비용과 품질 측면에서 새로운 표준을 제시했다고 주장합니다. 반면, 엔터프라이즈 실무 및 서드파티 연구진의 분석에 따르면 여전히 순수 GraphRAG의 커뮤니티 요약 리빌드 비용은 기존 Vector RAG 대비 최대 300배 비싸며, LightRAG와 같은 경량화 우회 방식이 없다면 프로덕션 배포가 불가능하다는 상충된 평가가 존재합니다.
- **[자료 부족] 자가 진화(Self-Evolving) 에이전트 생태계의 도달 시점**: 다수의 산업 보고서가 완전한 자가 진화 생태계(Stage IV)의 상용화 시점을 2028년 이후로 예측하고 있으나, 2026년 현재 이미 자체 스킬을 모방하고 복제하는 루프에서의 보안 취약성이 발견되는 등 실질적인 기술 성숙도 판단 및 도입 시나리오를 확정하기에는 구체적인 실증 데이터가 현저히 부족합니다.

#### ⑦ 출처명·발행일·링크

- Microsoft Research · 2024년 11월 25일 · https://www.microsoft.com/en-us/research/project/graphrag/
- Amazon Web Services (AWS) · 2025년 3월 7일 · https://aws.amazon.com/ko/about-aws/whats-new/2025/03/amazon-bedrock-knowledge-bases-graphrag-generally-available/
- Datawalk · 2025년 - 2026년 경 · https://datawalk.com/does-mcp-replace-graphrag/

---

## C. 요청/트렌드 → 구현 매핑 요약

| 입력 | 설계 결정 | 코드 |
|---|---|---|
| R3+R4+R5 (3채널 검색) / 트렌드 ① 하이브리드+리랭크 | RRF 가중 융합 + 리랭크 단계 분리, 채널별 on/off | `retrieval.py` rrf_fuse, rerank |
| R6 규칙 기반 그래프 | 사전·정규식을 `data/rules.json` 으로 외부화 → 자가 진화가 수정 가능 | `graph_rules.py` |
| R7 LLM 기반 그래프 / 트렌드 ② 경량화 | 변경 문서에만 LLM 추출, 커뮤니티 요약은 옵션, 이중 검색 기본 | `graph_llm.py`, `graph_build.py`, `retrieval.graph_search` |
| R8 self-evolving / 트렌드 ⑤·리스크 1·⑤ 위기 대응 | 데이터 전용 제안 큐, 스냅샷·회귀평가·롤백, HITL 기본, 체크섬 로그 | `evolve.py` |
| 트렌드 ③ 스트리밍 리빌드 | 해시 diff 증분 빌드로 부분 반영 | `pipeline.build`, `store.delete_doc` |
| 트렌드 ⑤ 기준 시나리오 "적응형 검색 라우터" | 질의 유형 분류 → 채널 가중치 | `retrieval.route` |
| 리스크 2 환각 | 인용 강제·상충 병기·미확인 명시 답변 프롬프트, 무인용 답변은 갭 기록 | `answer.py`, `evolve.capture_query` |
| 리스크 3 인덱싱 비용 | 규칙 경로 무료, LLM 경로 선택·증분 | `graph_build.py` |
| R11~R15 UI·CLI·토글·프로파일 | Toggles 단일 정의, Profiler 트리, Web 콘솔이 CLI 실행 | `config.py`, `profiler.py`, `cli.py`, `web/` |
| R16, R17 | 계획·멀티에이전트 프롬프트 문서 | `PLAN.md`, `MULTI_AGENT.md` |
| 트렌드 ① cross-encoder 리랭크 | (v2) `rerank_method=cross_encoder` 로 sentence-transformers CrossEncoder 사용, 미설치 시 로컬 폴백 | `retrieval.py` rerank, `tuning.py` |
| 트렌드 ⑤ MCP | (v2) stdio JSON-RPC MCP 서버, 읽기 전용 도구(wiki_query/search/entity/status) | `mcp.py`, `cli.py mcp` |
| 트렌드 ③ 실시간 리빌드 | (v2) stat_skip 증분 + auto_build 워처 | `corpus.scan_changed`, `pipeline.auto_build_tick` |
| 후속 요청 7·8 (모델 설정·노드 문서참조·확장·토글·프로파일·요청별 확인·UI·튜닝·구조 페이지) | (v2) 상세는 ARCHITECTURE_V2.md | `tuning.py`, `architecture.py`, `web/` |
| 트렌드 ④ DTG, Guardrails 엔진, LazyGraphRAG 동적 커뮤니티 선택 | 미반영 — 확장 지점만 확보 | `ARCHITECTURE_V2.md` §14 |

---

## D. 시스템 내부 LLM 프롬프트 (코드 내장)

> Anthropic 호출 시 `system` 에 들어가는 문자열입니다. `TASK=` 접두어는 mock 프로바이더가 작업 유형을 구분하는 데도 쓰입니다. 원문은 각 파일을 참조하세요.

### D.1 엔티티/관계 추출 — `graph_llm.py` `EXTRACT_SYSTEM`

```
TASK=extract
당신은 기업 문서(회의록, 일정, 시장 리포트, 논문)에서 지식 그래프를 구축하는 추출기입니다.
주어진 텍스트에서 엔티티와 관계를 추출해 JSON 으로만 답하세요. 설명 문장은 쓰지 마세요.

엔티티 type: org | person | role | org_unit | product | tech | topic | event | meeting | decision | date | amount | metric | material | concept
관계 rel: owner | deadline | attendee | decides | supplies_to | competes_with | comments_on | affects | requires | part_of | scheduled_on | related_to

규칙
- 엔티티 name 은 문서에 나온 표기를 정규화한 짧은 명사구 (예: "NVIDIA", "CFO실", "HBM4 캐파 확장 투자").
- 이미 알려진 엔티티 목록이 주어지면 같은 대상은 반드시 같은 name 을 사용하세요.
- description 은 한 문장, 근거가 텍스트에 있을 때만.
- weight 는 0~1 (관계의 확실성/중요도).
출력 형식: {"entities":[{"name":..,"type":..,"description":..}], "relations":[{"src":..,"dst":..,"rel":..,"description":..,"weight":0.8}]}
```
user 메시지: `## 섹션: <heading>` + `## 이미 알려진 엔티티` (최대 60개) + `## 텍스트` (청크)

### D.2 커뮤니티 요약 — `graph_llm.py` `SUMMARY_SYSTEM`

```
TASK=summarize
아래는 지식 그래프의 한 커뮤니티(서로 밀접한 엔티티와 관계 목록)입니다.
이 커뮤니티가 다루는 주제를 한국어 3~5문장으로 요약하세요. 근거 없는 추정은 하지 마세요.
```

### D.3 리랭크 — `retrieval.py` `RERANK_SYSTEM`

```
TASK=rerank
당신은 검색 결과 리랭커입니다. 질문에 답하는 데 가장 유용한 순서로 후보 문단 번호를 정렬하세요.
직접 근거가 되는 문단을 앞에, 무관한 문단은 제외해도 됩니다. JSON 으로만 답하세요: {"ranking":[번호,...]}
```
user 메시지: `질문: …` + `[i] (heading) 본문 600자` × 최대 16개

### D.4 답변 생성 — `answer.py` `ANSWER_SYSTEM`

```
TASK=answer
당신은 사내 지식 위키의 답변 엔진입니다. 아래 컨텍스트(문단 [C1], [C2], … 와 그래프 관계)만 근거로 한국어로 답하세요.
규칙
- 모든 사실 문장 끝에 근거 문단 번호를 [C번호] 형식으로 인용하세요. 여러 근거는 [C1][C3] 처럼 나열합니다.
- 컨텍스트에 없는 내용은 "제공된 문서에서 확인되지 않음"이라고 명시하고 추측하지 마세요.
- 날짜·금액·수치는 원문 그대로 인용하세요. 서로 다른 문서가 상충하면 둘 다 제시하고 출처를 구분하세요.
- 먼저 2~4문장 핵심 답변, 그다음 필요하면 근거 bullet 을 붙이세요. 간결하게.
```
user 메시지: `## 질문` + `## 컨텍스트` ([C#] 블록 ≤ 9,000자 + 그래프 관계 ≤ 15개)

### D.5 자가 진화 리뷰 — `evolve.py` `REVIEW_SYSTEM`

```
TASK=review
당신은 사내 RAG 시스템의 품질 관리자입니다. 아래 질의 로그(질문, 검색 점수, 답변, 피드백)를 보고
검색/그래프 품질을 높일 *데이터 수정 제안* 만 JSON 으로 내세요. 코드/프롬프트 변경은 제안하지 마세요.
가능한 kind: synonym(term,expansion) | alias(entity,alias) | entity(name,type,aliases) | relation(src,dst,rel,description)
각 제안에 confidence(0~1) 와 reason 을 붙이세요. 형식: {"proposals":[{"kind":..,"payload":{..},"confidence":0.8,"reason":".."}]}
```
user 메시지: `## 기존 엔티티(일부)` (80개) + `## 질의 로그` (최근 30건: Q, top_fused, feedback, note, A)

### D.6 호출 파라미터 (공통)

- 모델: `claude-opus-5` (config `llm_model`), effort: 추출/리랭크/리뷰 `low`, 답변 `medium` (`output_config.effort`)
- refusal 폴백: `anthropic-beta: server-side-fallback-2026-07-01` + `fallbacks: "default"` (opus-5/fable 계열)
- `stop_reason == "refusal"` 이면 예외 → 로컬 폴백(리랭크 휴리스틱 / 추출식 답변)
- 재시도: 408/409/429/5xx 및 네트워크 오류 3회 백오프
- Python 3.7: urllib 직접 호출 / 3.9+ 및 SDK 설치 시 `anthropic.Anthropic()` 자동 선택
- 아직 실제 API 로 검증되지 않음 (키 없음) — mock 프로바이더로 배선만 확인
