# docs/history — 그날의 기록 (고치지 않는다)

> 여기 있는 문서는 **그때의 사실**이다. 지금의 사실은 [docs/ 바로 아래](../DOC_MAP.md)에 있다.
>
> **읽기 전에**: 이 폴더의 문서에 적힌 결함·미완료·숫자는 **그날의 것**이다. 대부분 이후 회차에서
> 처리됐다. 지금 상태를 알고 싶으면 [DOC_MAP.md](../DOC_MAP.md) → 현행 문서, 또는
> `python tools/verify/verify_all.py` 를 본다.

## 왜 고치지 않나

낡았다고 고치면 **"왜 이렇게 됐나" 를 되짚을 근거가 사라진다.** 이 저장소에서 반복해 나온 결함
유형("설정은 되는데 아무 일도 하지 않는 기능")은 매번 "예전에 이렇게 했다가 이렇게 고쳤다" 는
기록 덕분에 원인을 빨리 찾았다. 그래서 기록은 **불변**으로 둔다 — 업계에서 ADR(Architecture
Decision Record)을 날짜·번호로 박아 두고 수정하지 않는 것과 같은 이유다.

바뀐 내용은 기록을 고치는 대신 **새 회차 폴더**와 **현행 문서**에 쓴다.

## 회차 색인

| 날짜 | 무엇을 했나 | 문서 |
|---|---|---|
| **2026-09-11** | 최초 요구사항과 입력 자료 | [IMPLEMENTATION_BRIEF.md](2026-09-11/IMPLEMENTATION_BRIEF.md)(무엇을 왜 — 구현은 재량) · [TRENDS_REFERENCE.md](2026-09-11/TRENDS_REFERENCE.md)(참고 트렌드 보고서 요약) |
| **2026-09-13** | v1~v2 설계와 요구사항 분석 | [requirement-0913.md](2026-09-13/requirement-0913.md)(사용자 요청 원문) · [ANALYSIS_REPORT_0913.md](2026-09-13/ANALYSIS_REPORT_0913.md)(요구사항 분석·규모 근거) · [IMPLEMENTATION_PLAN_0913.md](2026-09-13/IMPLEMENTATION_PLAN_0913.md) · [ARCHITECTURE_V2.md](2026-09-13/ARCHITECTURE_V2.md) · [REQUESTS_AND_TRENDS.md](2026-09-13/REQUESTS_AND_TRENDS.md) |
| **2026-09-14** | 권한 표·API 키·MCP HTTP/브리지·채널별 빌드·doc_expand·LLM 재시도·기대 결과 포렌식 | [IMPLEMENTATION_PLAN_0914.md](2026-09-14/IMPLEMENTATION_PLAN_0914.md) |
| **2026-09-15** | 다중 사용자 서버화(요청 격리·요청 관리자·역할별 LLM 정책·진행률/취소·스케줄러) · 상세 분석 모드 · 다른 RAG 연동 | [IMPLEMENTATION_PLAN_0915.md](2026-09-15/IMPLEMENTATION_PLAN_0915.md) · [VERIFICATION_0915.md](2026-09-15/VERIFICATION_0915.md) · [ARCHITECTURE_V3.md](2026-09-15/ARCHITECTURE_V3.md)(v3 구조 — 지금 구조는 [SYSTEM_ARCHITECTURE.md](../SYSTEM_ARCHITECTURE.md)) |
| **2026-09-16** | 요청 이력 · 모델 화면 · 답변 페르소나 · 규칙 확장 라운드 · headless 내성 · 협업(채팅/게시판) | [IMPLEMENTATION_PLAN_0916.md](2026-09-16/IMPLEMENTATION_PLAN_0916.md) · [VERIFICATION_0916.md](2026-09-16/VERIFICATION_0916.md) · [VERIFICATION_0916_2.md](2026-09-16/VERIFICATION_0916_2.md) · [HANDOVER_0916.md](2026-09-16/HANDOVER_0916.md) |
| **2026-09-17** | 전면 재검토(코드·문서·전 기능·동시성·실패 경로) · 단계 재실행 | [QUALITY_REVIEW_0917.md](2026-09-17/QUALITY_REVIEW_0917.md) · [CODE_REVIEW_0917.md](2026-09-17/CODE_REVIEW_0917.md) · [VERIFICATION_0917.md](2026-09-17/VERIFICATION_0917.md) · [REBUILD_SPEC.md](2026-09-17/REBUILD_SPEC.md)(v2 시점 재구현 사양서) |
| **2026-09-18** | 요청 23건(17+6): 답변/출력 모드 · 앙상블 · 스윕 · 그래프 진단 · Pipeline 페이지 · 운영 한도 | [IMPLEMENTATION_PLAN_0918.md](2026-09-18/IMPLEMENTATION_PLAN_0918.md) · [IMPLEMENTATION_PLAN_0918_2.md](2026-09-18/IMPLEMENTATION_PLAN_0918_2.md) |
| **2026-09-19** | 심층 리뷰·사각지대 점검 + **요청 14항목**(그래프 규칙 스키마 · 초기화 · 포팅 · Ask 화면 · Trial 원천 · 근거 링크 · 운영 통계 · 세 창구 정렬 감사) | [IMPLEMENTATION_PLAN_0919.md](2026-09-19/IMPLEMENTATION_PLAN_0919.md)(항목마다 조사한 사실·택하지 않은 대안·검증) · [DEEP_REVIEW_0919.md](2026-09-19/DEEP_REVIEW_0919.md) · [CODEBASE_REVIEW_0919.md](2026-09-19/CODEBASE_REVIEW_0919.md) · [QA_HARDENING_0919.md](2026-09-19/QA_HARDENING_0919.md) · [VERIFICATION_0918.md](2026-09-19/VERIFICATION_0918.md)(09-18/19 회차 검증 보고) |
| **2026-09-20** | **문서 재배치**(현행/기록 분리, 구조 문서 통합, 배치 규칙을 하네스로 고정) + **화면 사용성 수정**(Trial 비교 · 포렌식 · 운영 통계 추세 차트) | [DOCS_REORG_0920.md](2026-09-20/DOCS_REORG_0920.md)(날짜 폴더를 어디까지 쓸지 검토한 안 3개와 택한 이유) · [UX_FIXES_0920.md](2026-09-20/UX_FIXES_0920.md)(화면이 아니라 실제 데이터를 먼저 본 진단, 숫자를 만드는 쪽에서 고친 이유) |
| **2026-09-23** | **요청 5건**: 동시 질의 DB 잠금 · 질의/채널검색 별도 한도 · **요청 원장**(모든 요청 통합 조회) · 사라지는 요청 재현 · 세 창구 정합과 포팅 가이드 | [IMPLEMENTATION_PLAN_0923.md](2026-09-23/IMPLEMENTATION_PLAN_0923.md)(잠금의 진짜 원인을 실험으로 특정: 커밋 없는 `cache_put` 이 질의 내내 쓰기 잠금을 쥔다. 대화 중 결정 사항 7건을 §0.2 에 취합) |
| **2026-09-24** | 앞 회차의 **사후 코드 리뷰와 전체 검증** — 의도대로 구현됐는지, 부수 피해는 없는지 | [CODE_REVIEW_0924.md](2026-09-24/CODE_REVIEW_0924.md)(**가장 최근** — 결함 6건: 정상 종료 때 원장이 잘림 · 테스트가 실사용 원장에 회당 700~800줄 · CLI 질의에 상세 링크 없음 · 재현 하네스가 거짓 OK · 지표의 분모 오류 · 문서-코드 불일치. §5 에 재발 방지 규칙 5가지) |

## 다음 회차를 쓸 때

1. `docs/history/<오늘 날짜>/` 를 만들고 그 회차의 계획·검증·리뷰 문서를 넣는다.
2. 위 색인 표에 줄을 더한다 — **색인에 없는 기록 문서는 `verify_docs.py` 가 고아로 잡는다.**
3. 바뀐 동작은 **현행 문서**(`docs/` 바로 아래)에 반영하고, 기록 문서는 그대로 둔다.
4. 릴리스 단위로 묶이는 변경이면 [RELEASE_NOTES.md](../RELEASE_NOTES.md) 맨 위에 절을 추가한다.
