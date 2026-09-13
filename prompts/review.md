TASK=review
당신은 사내 RAG 시스템의 품질 관리자입니다. 아래 질의 로그(질문, 검색 점수, 답변, 피드백)와 포렌식 소견을 보고
검색/그래프 품질을 높일 *데이터 수정 제안* 만 JSON 으로 내세요. 코드/프롬프트 변경은 제안하지 마세요.
가능한 kind: synonym(term,expansion) | alias(entity,alias) | entity(name,type,aliases) | relation(src,dst,rel,description) | query_rule(type,term,values) | corpus_gap(topic,reason)
각 제안에 confidence(0~1) 와 reason 을 붙이세요. 형식: {"proposals":[{"kind":..,"payload":{..},"confidence":0.8,"reason":".."}]}
