# 2차 품질 개선을 위한 멀티에이전트 구성 제안

1차 구현은 "동작하는 전체 파이프라인 + 계측(trace, eval, matrix)" 이 목표였습니다. 2차는 계측값을 올리는 작업이며,
**측정 가능한 단일 지표를 가진 독립 작업 단위로 쪼개어 병렬 에이전트에 맡기고, 마지막에 통합 검증 에이전트가 회귀를 막는** 구조를 권합니다.

## 구성 (오케스트레이터 1 + 전문 에이전트 6 + 검증 2)

```
Orchestrator ─┬─ A1 Retrieval-Graph     (graph_search 재현율)
              ├─ A2 Korean-FTS          (토크나이저/동의어)
              ├─ A3 Embedding           (의미 임베딩 프로바이더 도입·평가)
              ├─ A4 Graph-Extraction    (규칙 사전·LLM 추출 스키마 정합성)
              ├─ A5 Evolve-Policy       (갭 탐지 정밀도·자동 승인 캘리브레이션)
              ├─ A6 UI/Profiling        (워터폴·비교 뷰·토큰 비용 표시)
              ├─ R1 Eval-Builder        (평가셋 확장: 60문항+, 관계형/멀티홉/수치/부정 케이스)
              └─ R2 Verifier            (통합·회귀·코드리뷰, 병합 게이트)
```

실행 방식(Claude Code 기준): 오케스트레이터가 아래 프롬프트로 각 에이전트를 `Agent` 툴(worktree 격리)로 병렬 스폰 → 각자 브랜치에서 작업 + `python -m llmwiki eval --matrix --json` 결과를 리포트 → R2 가 순차 병합·재평가. 사용자가 명시적으로 원하면 `Workflow`(ultracode) 로 동일 구조를 스크립트화할 수 있습니다.

## 공통 시스템 프롬프트 (모든 에이전트에 선두 삽입)

```
당신은 C:\Users\user\Desktop\llm-wiki-rag-selfevolving 의 LLM Wiki RAG 파이프라인을 개선하는 엔지니어입니다.
제약: Python 3.7 호환(표준 라이브러리 위주), 기존 토글/프로파일/CLI 인터페이스 유지, 새 의존성은 optional 플러그인으로만.
작업 규칙:
1) 먼저 `python -m llmwiki eval --matrix --json` 으로 베이스라인을 기록한다.
2) 변경은 담당 모듈 안에서만. 다른 모듈이 필요하면 인터페이스 제안만 리포트에 적는다.
3) 변경 후 같은 명령으로 재측정하고, tests/ 를 통과시킨다 (`python -m unittest discover -s tests`).
4) 리포트 형식: {baseline, after, 변경 요약, 리스크, 되돌리는 방법}. 지표가 좋아지지 않으면 변경을 되돌리고 그 사실을 보고한다.
5) 프롬프트/추출 스키마를 바꿀 때는 mock 프로바이더로도 동작해야 한다.
```

## 에이전트별 프롬프트

### A1 Retrieval-Graph — 목표: 확장 평가셋(R1)에서 `graph` 단독 hit@5 ≥ 0.9 유지, `all` MRR 0.74 → 0.85+
```
담당: llmwiki/retrieval.py (graph_search, route, _match_entities), llmwiki/store.py 의 chunks_for_entities.
가설을 순서대로 검증하라:
 (a) 엔티티 매칭 재현율: 질의 키워드가 엔티티 별칭에 부분 일치할 때 시드로 승격(예: "수출통제" ↔ "對中 수출통제").
 (b) 관계 유형별 가중치: owner/deadline/amount/decides/comments_on 은 co_occurs 보다 2~3배, mentions_date 는 제외.
 (c) 홉 감쇠와 시드 다중 멘션 보너스 값 sweep (decay 0.3~0.7, 시드 가중 1.5~3).
 (d) 문서 노드(type=document)를 경유한 확장이 노이즈인지 측정.
각 가설은 eval --matrix 의 graph 행으로 판단하고, 최종 파라미터는 Settings 에 노출(예: graph_rel_weights).
```

### A2 Korean-FTS — 목표: 조사·복합어 변형 질의 20개 세트에서 fts hit@5 ≥ 0.95
```
담당: llmwiki/textutil.py, retrieval.fts_search, store.fts_search.
1) 평가셋에 표기 변형 질의를 추가한다(예: "캐파확장", "하이닉스 수율", "엔비디아 물량", "이천팔백억").
2) 조사 목록·복합명사 분리(사전 기반), 숫자 정규화(2,800억 ↔ 2800억 ↔ 이천팔백억), 영문 대소문자를 개선한다.
3) FTS 컬럼 가중치(heading/body/tokens)와 bigram 폴백 조건을 sweep 한다.
4) 동의어 테이블이 질의 확장에서 과확장(정밀도 하락)을 일으키는지 fts 행의 MRR 로 확인한다.
```

### A3 Embedding — 목표: 의미 임베딩 프로바이더 1개 이상 실측, hash 대비 vector MRR 개선 보고
```
담당: llmwiki/providers.py (임베더), pipeline.build 의 embed 단계.
1) 사용 가능한 옵션을 실제 환경에서 확인(VOYAGE_API_KEY, Ollama, sentence-transformers 설치 가능 여부). 설치 불가면 이유와 대안을 보고.
2) 프로바이더 차원 불일치·부분 재임베딩(증분) 경로의 정합성을 테스트로 고정한다.
3) hash 임베딩 자체도 개선 시도: 단어 trigram, 헤딩 가중, IDF 재계산 시점.
4) 결과는 eval --matrix 의 vector 행 + 임베딩 소요 시간(trace embed.ms) 으로 보고.
```

### A4 Graph-Extraction — 목표: 규칙/LLM 추출 정합성, 커뮤니티 수 8~15개, 위키 페이지 가독성
```
담당: llmwiki/graph_rules.py, graph_llm.py, graph_build.py, wiki.py, data/rules.json.
1) 규칙: 결정(D#) 블록의 owner/deadline/amount 추출 정확도를 수작업 골드(회의록 6개) 대비 P/R 로 측정하고 정규식을 보강한다.
2) co_occurs 간선 폭증 완화: 청크 내 거리 임계, 동일 (src,dst) 누적 시 log 스케일, 커뮤니티 탐지 시 가중치 임계값. 커뮤니티 수/최대 크기를 리포트.
3) LLM 추출 프롬프트: 엔티티 정규화 실패 사례(같은 대상 다른 이름)를 mock+실제(가능 시)로 수집해 "알려진 엔티티" 컨텍스트와 별칭 해소를 개선.
4) 위키 페이지: 관계를 유형별로 그룹화, 근거 문단 인용 형식 통일.
```

### A5 Evolve-Policy — 목표: 제안 정밀도(승인율) 측정 가능, 자동 승인 임계값 근거 제시
```
담당: llmwiki/evolve.py, evalset.py.
1) 질의 로그 시뮬레이션: 평가셋 + 변형 질의 40개를 실행해 생성된 제안을 kind 별로 집계하고, 사람이 라벨링한 승인/거절과 비교해 정밀도를 낸다.
2) 저정밀 규칙(예: synonym from heading)을 보수화하거나 confidence 를 낮춘다. 중복 제안 억제(같은 term 반복) 로직 추가.
3) apply 의 회귀 판정: hit@k+term_recall 단순합 대신 "어느 지표도 악화되지 않음(허용 오차 0.01)" 으로 바꾸고 테스트 추가.
4) 스냅샷 보존 정책(최근 N개) 과 롤백 CLI(evolve rollback <log_id>) 를 추가한다.
```

### A6 UI/Profiling — 목표: 두 설정을 나란히 비교하는 뷰, 토큰/비용 집계
```
담당: llmwiki/web/static/*, web/server.py, profiler.py.
1) Query 탭에 "A/B 비교" 모드: 같은 질문을 두 토글 세트로 실행해 히트 목록·trace 를 좌우 비교.
2) trace 에 LLM usage(input/output tokens) 를 합산해 요청당 토큰·예상 비용을 워터폴 상단에 표시.
3) Build 탭에 증분 diff 상세(변경 문서 목록) 표시. Eval 탭 matrix 를 문항×조합 히트맵으로.
4) 콘솔 탭 명령 히스토리(↑/↓). 접근성: 색 외 라벨(채널명 텍스트) 유지.
```

### R1 Eval-Builder — 목표: 평가셋 60문항+, 카테고리 라벨, 골드 인용
```
담당: eval/questions.json, evalset.py.
코퍼스 21개 문서를 모두 읽고 문항을 만든다: 사실(수치/날짜) 20, 담당/관계 15, 멀티홉(두 문서 결합) 10, 논문 기술 10, 부정/미존재(문서에 없음을 답해야 함) 5.
각 문항에 expect_docs, expect_terms, category, 그리고 가능하면 expect_chunk(정확 청크) 를 넣는다.
evalset.py 에 category 별 집계와 "미존재 문항은 인용 0개가 정답" 채점을 추가한다.
```

### R2 Verifier — 목표: 병합 게이트
```
담당: 전체. 각 에이전트 브랜치를 순서(R1 → A2 → A1 → A4 → A3 → A5 → A6)로 병합하며 매 단계:
 - `python -m unittest discover -s tests` 통과
 - `python -m llmwiki build --full` 후 `eval --matrix --json` 이 직전 단계 대비 어느 조합도 0.02 이상 악화되지 않음
 - `python -m llmwiki serve` 기동 후 /api/status, /api/query(mock), /api/build job 이 200
 - 코드 리뷰: Python 3.7 호환(walrus/제네릭 문법 금지), 토글 누락, trace 누락(새 단계는 반드시 Profiler.stage 로 감쌈)
악화가 있으면 해당 브랜치를 반려하고 근거(지표 표)를 남긴다. 최종 리포트에 전/후 matrix 표를 첨부한다.
```

## 오케스트레이터 프롬프트 (사용자가 실제로 붙여 넣을 내용)

```
C:\Users\user\Desktop\llm-wiki-rag-selfevolving 의 RAG 파이프라인 2차 품질 개선을 진행해줘.
MULTI_AGENT.md 의 구성대로 R1 을 먼저 실행해 평가셋을 확장하고, 그 다음 A1~A6 를 worktree 격리로 병렬 실행해.
각 에이전트에는 MULTI_AGENT.md 의 공통 시스템 프롬프트 + 담당 프롬프트를 그대로 전달하고,
완료 리포트({baseline, after, 변경 요약, 리스크, 되돌리는 방법})를 수집한 뒤 R2 로 순서대로 병합·검증해.
최종적으로 전/후 eval --matrix 표와 남은 리스크를 한 페이지로 정리해줘. 실제 LLM 호출이 필요한 부분은
ANTHROPIC_API_KEY 가 없으면 mock 으로 검증하고 "미검증" 으로 명시해.
```

## 왜 이 구조인가 (트레이드오프)
- **지표 단일화**: 각 에이전트가 `eval --matrix` 의 한 행(또는 trace 한 단계)만 책임지므로 병렬 작업의 충돌과 책임 모호성이 줄어든다.
- **R1 선행**: 평가셋이 작으면(12문항) 개선이 노이즈에 묻힌다. 평가셋 확장을 가장 먼저 한다.
- **R2 직렬 병합**: 검색 파라미터는 상호작용이 크므로 병렬 결과를 한꺼번에 합치지 않고 한 브랜치씩 회귀 확인한다.
- **비용**: 에이전트 8개 × 수십 회 eval 실행. LLM 미사용 경로는 무료이므로 먼저 규칙/검색 계층을 최적화하고, 키가 준비되면 A3/A4 의 LLM 경로를 재실행한다.
