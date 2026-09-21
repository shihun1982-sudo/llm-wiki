모뎀 HW를 제어하는 임베디드 SW 를 개발하고, 각정 모뎀 이슈 분석에 활용할 self evolving llm rag wiki를 만들려고 해. 아래는 내가 생각한 현재 workspace 코드 기준으로 추가로 필요한 내용이야. 그대로 바로 진행하지 말고, 너도 충분히 고민해서 타당한지 확인해줘. 그리고 구현 계획서를 제안해주고, confirm 받고 구현 진행해


아래 요청한 내용에 대해서 상세히 분석해서, impact을 정리하고 각 구현에 대해서 진행할지 여부에 대해서 상세 report 문서로 남겨줘.
그리고 각 구현 방향에 대해서 최종 confirm 받고나서 구현 시작해

최종적으로 다른 환경으로 porting 할 여지가 있기 때문에, 구현이 완료되면, 다른 환경으로 porting 할 것을 생각하고, bring-up guide 문서를 상세히 작성해줘.
또한 전체 구조를 도식화하고, 전체적으로 각 기능이 (예를들어 CLI 전체) 어떤식으로 동작하는지 flow를 상세한 예를 들어서 매 단계별로 설명해줘. 그외에 필요하다고 생각하는 정보를 모두 포함해서 작성해줘.

*제공하는 모든 기능은 CLI 로 구현될테니, 전체 CLI의 동작 flow를 단계별로 예를 들어 알려주면 좋겠어. MD 파일과 HTML 형식 모두 작성해줘. HTML 파일로 interactive 하게 표한하면 구조 및 동작에 대해서 사람들이 더 보기 좋을 것 같아.

************************* 추가로 고려해야 할 점 *************************

1. Corpus 는 아래와 같은 형태의 data를 build 할텐데, 이때 각 data 별로 어떤 형태 (corpus contract / document schema)가 좋을지 문서 포맷을 제안해줘.
 * 추후에, 문서를 사람이나 LLM 을 통해서 생성하더라도, "어떤 field 를 넣어야 FTS/Vector/Graph 를 포함한 build 및 검색 logic 이 잘 동작하는지"라는 계약(contract)가 이썽야 data 품질이 안정될 것이라고 생각해.
*문서 schema 는 향후 확장/변경 가능성을 고려하여 schema_version을 관리할 수 있는 형태면 좋겠어.
*그리고 아래 1) 2) 이외에 build 정보를 포함한 raw data를 가지고 올 수 있는 "Mango MCP"가 있어. 필요한 경우 (data build, query 처리, evolving 등..)에 "Mango MCP"에 접근해서 raw data 를 가지고 올 수 있다고 생각해. 이 feature 는 on/off toggle 할 수 있어야 하고, CLI 에서도 제어할 수 있으면 좋겠어.
*이런 접근 가능한 MCP 정보들도 별도 config 파일로 빼줘서 관리하는게 놓을 것 같아.

 1) Issue 문서 : 어떤 문제점이 있고, 원인 / 분석 / 수정 (Change List) 관련 정보들이 정리되어 있어. 즉 이슈, 현상, 해결책이 설명되어 있어.
 2) Change List (CL) 문서 : 실제로 반영한 수정으로 간단한 설명과 Issue 번호가 명시되어 있어.
 3) SW 설계문서 : architecture 문서 및 code map, code review rule 등이 포함될수 있어.
 4) HW 설계 문서 : HW 동작 timiing 및 HW 제어하는 embedded sw 개발에 필요한 정보들이 제공되고, HW revision History 도 포함되어 있어.
 5) Coding Rule Guide : 이건 3)을 기본으로 따를텐데, 새로운 규칙이 필요하면 추가 coding rule이 꾸준히 추가 될 수있어.
 6) Weeklky report : 업무 요약, 이슈 요약, CL 리뷰 요약.. 등 매주 이루어지는 다양한 업무에 대한 summary 가 포함.

위를 기본 data corpus로 가지고 있어. 그 외에 새로운 category (예를 들어 검증 TC List)가 data로 추가될 수 있기 때문에 확장 가능한 형태면 좋겠어.

2. 아래 use를 생각하고 있어. 아래 use case에 해당하는 별도 skill 이나 agent를 만들꺼고, 지금 만들 llm wiki에 접근하여 (아마도 mcp 형태로 지금 llm wiki에 접근할 것으로 예상함) 사용할 예정이고, 각 use case 별로 skill 이나 agent를 개발 하고 고도화 할 예정이야.
 * 내 생각엔, 아래 use case를 llm wiki 에 내장하는 것은 나쁜 생각인것 같고, 이부분은 각 use case 별로 별도로 고도화 하는게 맞다고 생각해. 내 생각 고려해서 고민해줘.

 1) 모뎀 제어 임베디드 SW feature implementation 을 위한 정보를 llm wiki에서 획득하여 구현하고 simulation (virtual platform) 으로 build 또는 TC 검증
 2) code review 진행할 때 code map 을 활용하여 심층적인 코드 리뷰를 하고, SW / HW rule에 맞춰서 코드 리뷰 진행
 3) issue analysis : 이슈 분석 후 분석 결과를 llm wiki에 제공해서 과거 유사 이슈나, 수정 CL을 확인하여 취합하고 이슈 분석에 도움이 되는 정보를 llm wiki 로부터 제공 받을 수 있음.
 4) refactoring : 새로운 refactoring 방향이 정해지면, sw/hw 구현에 맞는 정보를 획득할 수 있게 제공
 5) unified search : web uil 나 별로 cli로 사용자가 자연어로 입력한 내용에 대해서 관련 된 정보를 최대한 상세히 전체적으로 조사해서 return. 해주는 형태. 

3. 코퍼스는 현재 5000 개 정도의 md 파일이 있고, 매일 50개 정도가 신규 생성되어 늘어날 예정이야. 이런 환경에 적절한지 검토 해주고, 최신 트렌드를 조사해서 이 환경에 맞는 더 좋은 구조, 확장, 기능이 있다면 제안해줘.

 1) 매일 증가하는 코퍼스에 대해서는 전체 rebuild 는 필요 없고, 증분 빌드만 하돌고 하면 되겠지? 특정 시간에 진행할 수 있도록 별도 agent가 관리하는게 맞겠지? llw wiki가 제공하는 CLI를 이용해서, 아니면 llm wiki가 알아서 증분 빌드를 특정 시간에 하는게 맞을까? 니가 고민해서 제안해줘.

  * 증분 build 시 신규/수정 문서 뿐 아니라 삭제/rename된 문서르 포함하여 변경을 모두 추척하고, FTS/Vectgor/Graph 에 stale data가 남지 않도록 consistency를 관리할 수 있는 구조로 만들어줘.

************************* 구현 요청 *************************

0. 설정 파일 config.jaon, .env, tunning 관련 별로 parameter 파일, 각종 기능 및 debug feature on/off 파일 등 편의성과 확장성을 고려해서 파일로 제공해줘.
 * 모든 환경 변수 와 tunning param 등을 제어할 수 ㅣㅇㅆ도록 빼줘 (예를 들어, 코퍼스 경로, 각 단계별 llm model,, tunning para, toggle 로 on/off 하는 feature 등 모두)

1. OpenAI-compatible provider 구현
2. rerank 전용 엔드포인트 지원
3. 임베딩 시 재개 / 체크 포인트 확인 할 수 있도록 지원
 * build 전 API health check 하는 부분을 추가 구현해주면 좋겠어. 그러면 build 전에 문제를 확인 할 수 있도록
4. 임베딩 진행률 및 report를 볼 수 있는 CLI 및 web ui 제공
5. 그 외에 임베딩 진행 시에, 배치 사이즈나 WAL 크기 등 고정 값으로 진행 시 문제 될만한 것들을, 상황을 보면서 동적으로 진행 하도록 해줘. 사용자 개입이 필요하면 알려주고.
 * 단 이 내용은 전체 검색 품질, 사용자 요청에 대한 속도와는 직접적인 관련은 없는거지?
 * 다만 embedding이 일부 실패하여 coverage 가 낮아지느 ㄴ경우에는 retrieval 품질에 간접적으로 영향을 줄 수 있으므로 embedding coverage도 확인 할 있으면 좋겠어.
6. 현재 LLM 이 관여하는 부분 이외에, "사용자 query expansion"이나 "rebrieval/rerank/evidence selection 이후에 evidence가 insufficient 한 경우의 fallback loop"에 llm이 관여해서 다시 수행하게 하는 부분을 추가하고 싶은데, 어떠케 생각해?
 * 즉 사용자 query expansion에 관여하여 query를 다양하게 하는게 필요하고
 * fallback loop에 관여해서 검색 품질을 높ㅇ리 수 있다고 생각해.
 * LLM query expanstion은 원래 사용자 query 를 replace 하지 말고 additional retrival query를 생성하는 방향으로 해줘. origial query는 항상 retrival에 유지하는게 맞다고 생각해.
 * fallback loop 는 무한 반복되지 않도록 최대 attempt / budget/ latency 등을 제어할 수 있게 해줘.
 * 이런 부분에 대해서도 모두 toggle on/off 할 수 있어야 하고, 모두 CLI 나 web UI로 제공되어야 해.
 * LLM이 관여해서 성능이 좋아질만한 부분이 추가로 있는지 제안해주고, 있다면 구현해서 toggle on/off 제어하게 추가해줘
7. LLM 과 별개로, query expansion 시에 사용자가 정의한 동의어, 유사어, 관련어, 제외어 이런식으로 작성할 수 있는 파일이 있으면 좋겠어. LLM 이 관여하지 않아도 이 파일ㅇ르 참고하면 rule based 로 쿼리르 확장 할 수 있도록 하는 형태야.
 * acronym, synonym, allias, related term, exclude term 등을 구분할 수 있으면 좋겠어.
 * 각각을 단순히 query 에 동일하게 추가하지 말고, 각 rule type의 의미 차이를 고려하여 retrival에 적용할 수 있게 하면 어때? 이부분은 니가 고민해서 제안해줘. 적절한지 아닌지에 대해서
 * Rule-Based Expansion 전/후 query 와 실제 retrival에 어떤 영향을 주었는지 profile 할 수 있도록 해줘.
8. 한글 코퍼스가 대부분이기 때문에 한글 코퍼스를 build 할때 좋은 기능을 추가해줘.
 * 예를 들어 한글 bigram 열을 추가한다던가, 이거 이외에 품질을 개선하기 위한 다양한 기법 (pre-tokenization, bigram, n-gram 등이 방식)에 대해서 적절한지 판단해서 구현해줘.
9. answer 제공시에는 반드시 근거가 있는 내용에 대해서만 답변하도록 해줘. 즉 externally verifiable factual clain은 traceable evidence에 의해 support 되어야 해. 이 부분도 니 의견 반영해서 고민해줘.
 * factual claim 에 대해서 근거와 REF를 제공해야 한다고 생각해. 이를 통해 hallucination을 최대한 억제하고 싶어.
 * 단순히 citation이 존재하는지만 확인하지 말고, 해당 citation의 evidence가 실제 clain을 support 하는지도 검증할 수 있는 구조면 좋겠어.
 * 만약 원하는 답변이 없다면, insufficient data로 확인하고 확장 검색하는 fallback loop이 있으ㅏ면 좋겠어. 이 경우에 tollge 로 on/off 할 수 있게 해주고, 이때 전체 코퍼스를 무조건 다시 검색하기 전에, 단계별로 fallback 하는게 좋을 것 같아. 
 * 이런 경우에 "포렌식 검사"를 진행할 수 있는 기능이 있으면 좋겠어. 이를 통해서 사용자 요청에 대해서 왜 응답을 만들지 못했는지, 상세히 분석할 수 있는 profile 기능도 있어야 하고. 이런 정보들이가 누적되어 자동으로 저장되면 이를 통해 사용자에게 proposal 하는 구조를 만들어서 코퍼스에 필요한 data를 추가하는 self evolving 이나 품질을 높히기 위한 tunning에 도움이 될 것이라고 생각해. 물론 최종 승인은 사람이 할 수 있어야 하고, 이 승인 또한 on/off 로 제어할 수 있어야 해.
10. pin, precompute 기능도 구현해주고 toggle로 on/off 할 수 있게 해줘.
11. web ui 제공 시에, 전체적인 테마를 고를 수 있게 만들어줘. 이것도 확장 가능한 형태로 만들어주고, 일반적인 확장 형태가 있다면 그것을 사용해도돼.
12. 디버깅 목적으로, 어떤 동작 중에 문제가 된다면 log를 남길 수 있도록 해줘. 생각해보니까 문제 될때만 남기지 말고, 정상동작일 때도 남겨야 디버깅이 될 것 같은데, logs 폴더나 이런것을 만들어서 디버깅 용이하게 관리해줘.
 * 가능하면 query / build 단위 ID를 를 통해서 관련 log 와 profile 을 연결해서 추적할 수 있게 해줘.
13. 최근에 보니까 selve evolving 에 episodic memory + semantic memory + decay 개념ㅇ르 활용하는데, 이런것을 활용할 여지가 있을지 고민해서, 필요하면 구현해줘.
14. 사용자 quiry 에 대해서 Korean relative Time parsing을 지원해 주면 좋겠어.
 * 예를 들어, "지난주", "어제", "3일전" 등을 나라짜 범위로 변환해줘. 그러면 성능이 올라 갈것 같아. 이 시간 정보에 대한 지역을 설정할 수 있다면 설정할 수 있게 해줘. default 는 Korea야.
15. graph  build 시에, node 가 포함된 ref 문서 정보를 연결하는게 필요해 보여. 그래야 연결된 node의 원본문서를 바로 찾아서 검색 품질을 높일 수 있을 것 같아. 너도 고민해ㅈ봐
16. graph build 시에, llm 이 관여하기도 하지만 사용자가 지정한 rule 에 의해서 연결하는 부분 (deteministic relattion)도 필요하다고 생각해.
 * 예를들어 data corpus에 있는 CL 문서는 이슈번호가 반드시 명시되어 있어, 그래서 "CL 번호 - 이슈 번호" 이런식의 연결이 가능하기 때문에 이런 연결이 되면 좋겠어.
 * 가능하면 graph relation 을 explicit / rule-derived / llm-inferred 등으로 구분하고, provenance와 confidenc를 구분해서 관리할 수 있게 해줘. 이부분은 너도 추가로 고민해서 구현해줘. 
17. 사용자 요청에 대해서 각 단계별로 weight를 주거나 할 수있닌 tunning poiunt가 있으면 좋곘어. FTS, vector, graph 이런 결과값들에 대해서도 weight를 줘서 응답에 영향을 줄 수 있는 point가 있으면 좋겠어.
 * 다만 FTS/Vector/Graph의 score scale이 서로 다를 수 있기 때문에 단순 weighted sum을 전제로 하지 말고, 전체 fusion/ranking acritecture에 접합한 RRF, nomalization, callibration, boost 등의 방식을 비교해서 적절한 tunning poing를 제공해줘
18. evaluatino 검증 경로와 실제 사용자 사용 경로 web ui 수행 경로가 모두 일치 되면 좋겠어. 그래야 나중에 디버깅이 편하다고 생각해. 
19. 어떤 변경에 대해서 regression을 진행할 때 관리할 수 있는 지표와 시스템을 제공해줘.
 * tunning 시에 trial 마다 before / after 또는 3개 4개 정도 로 확장해서, 비교 trial 마다 각 종 지표와 impact 와 report 를 제공하고 비교할수 있으면 좋겠어. 이는 web UI로 제공하여 확인할 수 있으면 좋겠어. 관련하여 도움이 될만 한 정보르 상세히 기록해서 보여주는거지.
20. llm 선택에 확장성을 두고 싶어. 그래서 opencode CLI를 non-interactive / headless 방식으로 subprocess 실행하고, task/question/evidence/context 또는 관련 파일 경로를 전달한 뒤 structured result를 회수하여 query expansion, answer sysnthesis, forrensic, self evolving proposal 등의 선택적 Agent/LLM 단계에서 사용할 수 있도록 Generic Headless Agent Provider를 설계해줘.
21. Web UI 구성을 관련된 기능들끼지 같이 배치해서, 사용자가 사용하기 편한 구조로 만들어줘. 단순히 현재 구현된 기능을 메뉴에 나열하는 방식보다는, 사용자가 실제로 수행ㅇ하는 workflow와 서로 연관된 기능을 기준으로 Web UI의 information Archtecture와 화면 구성을 설계해줘.
 * 품질 최적화 / 속도 최적화 / 토큰 최적화 이런 checkbox 가 있고, 체크하면 관련된 설정들이 일괄적으로 다 설정되는 방법도 있으면 좋겠어. 이런게 web ui에 도 추가되고. CLI로도 있고.
22. "검색된 Evidence를 충분히 활용해서 사용자가 응답 내용을 충분히 이해할 수 있도록 구조화되고 상세하기 설명하도록 답변"하도록 사용자 query에 대응하도록 guide 넣어줘. 이와 관련되서 synthesize 시에 LLM에게 guide를 제공하는 md 파일이 별도로 있다면 도움이 될 것 같아. 만약 답변이 너무 길거나 짧은 경우에 단계별로 포렌식하여 디버깅 진행하려고 해. evidence 에 없는 내용을 일반 LLM 지식으로 임의 생성해서 답변 하면 안돼. Evidence-rich + Structured + Explantory+ Crounded Answer 방향으로 만들어야 한다고 생각해