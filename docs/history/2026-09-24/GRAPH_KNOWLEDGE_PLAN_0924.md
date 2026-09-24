# Knowledge 회차 계획 — 그래프 진단을 "소견과 처방" 으로, 그래프 탭을 "읽을 수 있게" (2026-09-24)

> **이 문서의 지위**: 사용자 요청 2건(그래프 진단 개선 · 그래프 탭 용어/보기 개선)의 조사·설계·택하지 않은 대안·검증 기록.
> 운영 절차는 현행 문서에 있다 — 진단은 [GRAPH_PROFILE.md](../../GRAPH_PROFILE.md), 규칙 매칭은 [GRAPH_RULES.md](../../GRAPH_RULES.md) §1.1,
> 화면은 [WEB_UI.md](../../WEB_UI.md) §0.69, 코퍼스 규약은 [CORPUS_CONTRACT.md](../../CORPUS_CONTRACT.md).

## 0. 요청과 해석

| 요청(원문 요지) | 해석 |
|---|---|
| ① "그래프 진단에서 지금 그래프를 보고 **문제점을 파악해서 어떤 점을 개선해야 하는지** 상세히·명확히 — graph rule 을 어떻게 추가할지, 코퍼스를 어떻게 구성할지, 검색 품질을 높이는 정보" | 지표(숫자)와 한 줄 제안으로는 부족하다. **소견(finding)** 단위로: 증거(숫자·실제 예) → 원인 → 처방(붙여 넣을 rules.json 조각 / 고칠 문서 목록 / 튜닝 키 / 명령) → 확인 방법 |
| ② "그래프의 **노드 수**는 뭘 의미하나, **커뮤니티와 출처 c0 c1 c2** 가 뭔지 모르겠다. 사용자 입장에서 알 수 있게, 더 자세히·다양하게" | 화면이 용어를 직접 설명하고(상위 N · 무리 · 관계 출처 · 연결/이웃), 보기 모드(개요·이웃·무리 하나·유형별)와 무리 상세를 더한다 |
| ③ (도중) "노드 수 선택은 어떻게 되나? **전체가 몇 개고 엣지가 몇 개인지** 있어야 몇 개를 볼지 정하지 않나" | 고르기 **전에** 전체 규모(엔티티·관계·구조/공동출현·유형별·무리)를 첫 줄에 보여 주고, 50/120/300/전체 빠른 선택과 "= 전체의 N%" 를 붙인다 |

## 1. 조사한 사실 (2026-09-24, 실DB 읽기 전용)

### 1.1 지금 진단이 놓치던 것

| # | 사실 | 지금 진단의 반응 | 왜 놓쳤나 |
|---|---|---|---|
| 1 | 허브 1위 **IR본부**(org_unit) degree 7,748 · CTO 2,244 · CHRO 846 · COO 580 · 베이스밴드 504. 짧은 별칭(IR·CTO·COO·CHRO·BB)이 **단어 경계 없이·대소문자 무시**로 매칭돼 `corpus_d[ir]s`·`LOGS_D[IR]_PATH`·`ve[cto]r`·`syn[chro]nous`·`a[bb]reviation` 에 걸린 것. 실측: IR본부 mention 125건 중 124건(99%), CTO 129건 전부(100%)가 단어 내부 | role 허브 3개만 경고. 처방 "types_for_cooccur 에서 role 제외" 는 **틀린 처방**. IR본부는 org_unit 이라 경고 없음 | 매칭 정규식(`graph_rules._compile`)에 경계가 없고, 진단은 매칭의 **문맥**을 보지 않는다 |
| 2 | RFC 코퍼스 front matter `related.obsoletes`(42 문서)·`related.updates`(11) 가 `related_key_type`·`explicit_rels` 에 없어 관계가 `references` 로 뭉개지고, 대상 엔티티에 **없는 타입** `obsolete`(50개)·`update`(20개)가 생김(`key.rstrip("s")`) | 경고 없음 | front matter 키와 규칙 매핑을 대조하지 않는다 |
| 3 | 관계 14,320개 중 구조 관계(explicit 142 + rule 433)는 **4%** | cooccur 비중 info 한 줄 | 문서→엔티티 `mentions` 가 비율을 부풀린다 |
| 4 | 문서 352개 중 **105개**의 제목이 `-*- coding: utf-8 -*-`·shebang 같은 코드 줄 → 문서 노드 별칭이 오염돼 NOTE-ASK/NOTE-CORPUS-2 … 가 중복 후보로 묶임 | 중복 후보 수만 | 코퍼스 구조 문제를 코퍼스 처방으로 잇지 않는다 |
| 5 | 죽은 규칙 15건 | 전부 같은 문장 | "그 doc_type 문서 0건" 과 "정규식이 안 맞음" 을 구분하지 않는다 |
| 6 | 무리(커뮤니티) 36개 중 **30개가 1인 무리**, C0 이 562개 | — | 화면이 무리를 번호로만 부른다 |
| 7 | 서버 `/api/graph` 가 화면의 **출처·유형 필터를 넘기지 않는다**; 무리 필터는 상위 `limit×3` 을 자른 **뒤** 적용돼 작은 무리를 고르면 노드 0개 | — | 09-13 회차의 미완 |

### 1.2 그래프 탭이 헷갈리던 이유

| 화면의 말 | 실제 뜻 |
|---|---|
| 노드 수 120 | `entities` 를 degree(관계 행 수) 내림차순으로 자른 **상위 N개**. 전체가 아니다. 기본값도 HTML 120 · 서버 150 · CLI 40 · 엔진 400 으로 제각각이었다 |
| 커뮤니티 C0, C1 | 라벨 전파가 붙인 **순번**. C0 = degree 최상위 노드의 무리. 이름이 없고 요약은 "핵심 엔티티: …" 나열 |
| 목록의 [rule]/[llm] | 요약문을 **누가 썼는가**. 관계 출처(provenance)와 같은 말이 두 뜻으로 |
| 출처 드롭다운 | 관계 provenance — 그런데 서버가 무시했다 |
| 노드 크기 = degree | 관계 **행** 수(청크마다 셈). 이웃 수가 아니다. IR본부 degree 7,748 → 반지름 110px |

## 2. 설계

### 2.1 소견(finding) — 세 창구가 같은 dict

`llmwiki/graph_findings.py` 가 `graph_profile.profile()` 결과에 `findings` 절을 더한다. 소견 하나의 모양은 모두 같다:

```jsonc
{"id": "alias_false_positive", "severity": "error", "area": "rules",
 "title": "짧은 별칭이 단어 안에서 매칭돼 가짜 허브를 만든다",
 "why": "…원인…",
 "evidence": {"numbers": {"entities": 7, "worst_embedded_share": 1.0, "matching_enabled": true, "detail": "IR본부(org_unit, degree 7748, 단어 내부 99%), …"},
              "samples": [{"name": "IR본부", "occurrences": 125, "embedded": 124, "samples": ["corpus_d[ir]s` 에", "I_LOGS_D[IR]_PATH`"]}]},
 "fix": {"kind": "rules_patch", "section": "matching · entities.<name>.match",
         "snippet": {"matching": {"ascii_word_boundary": true, "case_sensitive_max_len": 3}, "entities": {"IR본부": {"match": {"whole_word": true, "case_sensitive": true}}}},
         "steps": ["…"], "commands": ["python -m llmwiki build graph", "python -m llmwiki graph profile --compare"]},
 "verify": {"metric": "connectivity.hubs[IR본부].degree", "expect": "재빌드 뒤 크게 감소", "command": "graph profile --compare"}}
```

소견 13종과 처방 영역(`area`):

| id | 본다 | area / fix.kind |
|---|---|---|
| `alias_false_positive` | 허브·상위 사전 엔티티의 실제 mention 문맥에서 **단어 내부 매칭 비율** | rules / rules_patch (`matching`, `entities.<name>.match`) |
| `related_key_unmapped` | doc_meta `related.*` 키 ∖ 규칙 매핑, 그로 생긴 가짜 타입, 대상 타입 추정 | rules / rules_patch (`related_key_type`·`explicit_rels`·`schema.relations`) |
| `doc_type_without_rules` | link_rules·explicit_rels·id_patterns 어디에도 없는 문서 유형 | rules / rules_patch 뼈대 |
| `dead_rule_no_docs` | 죽은 규칙 중 "그 문서 유형이 0건" · "front matter 에 그 related 키 없음" | corpus / corpus_edit |
| `dead_rule_no_match` | 문서는 있는데 못 잡음 — 정규식 시험 결과 + **코퍼스에 실제로 있는 필드 이름** | rules / rules_patch |
| `junk_titles` | 코드·shebang·기호·3자 미만·중복 제목 → 파일 목록 | corpus / corpus_edit |
| `no_communities` | 토글 on 인데 0 | build / build_cmd |
| `structure_share_low` | mentions·co_occurs 를 뺀 구조 관계 비율 < 15% | corpus / corpus_edit |
| `uncovered_docs` | 커버리지 미달 유형의 문서를 **왜**(ID 없음 / 사전 엔티티 없음 / 본문 짧음)로 | corpus / corpus_edit |
| `isolated_by_type` | 고립 30%↑ — 사전 엔티티면 types_for_cooccur, 아니면 link_rules | rules / rules_patch |
| `no_seed_queries` | 시드 없는 질의 50%↑ → 키워드 목록 | query_rules / query_rules_patch |
| `hub_type_policy_mismatch` | types_for_cooccur 에 date/amount/percent/document | rules / rules_patch |
| `id_missing` | ext_id 없음/추론 30%↑ (note 제외) | corpus / corpus_edit |

기존 `suggestions` 는 호환을 위해 남기고 화면에서는 접어 둔다. 임계값은 `graph_findings.THRESHOLDS` 로 결과에 실린다.

### 2.2 매처 — 처방이 실행 가능하려면 규칙 파일이 그것을 표현해야 한다

`rules.json` 에 `matching` 절과 엔티티별 `match` 를 더했다 ([GRAPH_RULES.md](../../GRAPH_RULES.md) §1.1):

- `matching.ascii_word_boundary`(true): ASCII 별칭은 앞뒤가 영숫자가 아니어야 매칭. 한글은 조사가 붙으므로 적용하지 않는다.
- `matching.case_sensitive_max_len`(3): 이 길이 이하 ASCII 별칭은 대소문자 구분.
- `entities.<name>.match.whole_word / case_sensitive`: 엔티티별 덮어쓰기.

구현: 대소문자 구분/무시 정규식을 **둘**로 컴파일하고 매치를 합쳐 겹치면 먼저 시작하고 더 긴 것만 남긴다. 질의 쪽 `retrieval._match_entities` 도 같은 경계 규칙(`graph_rules.name_in_text`)을 쓴다 — "first step" 에서 IR본부가 시드로 잡히던 것도 같은 버그였다. `lint` 가 모르는 키를 알리고 `fill-defaults` 가 절을 채운다. 되돌리기: `ascii_word_boundary=false`, `case_sensitive_max_len=0`.

**기본값을 켠 채로 배포하는 이유**: 실데이터 허브 1~5위가 전부 이 오탐이다. 기존 그래프는 재빌드(`build graph`) 전까지 예전 매처 결과로 남고, 소견이 그것을 알린다(`matching_enabled: true` → "다시 빌드하면 사라진다").

### 2.3 그래프 탭

- **용어를 화면이 설명**: 상단 안내문 + 각 컨트롤 title. "노드 수" → **"상위 N개"**(연결 많은 순, 전체 아님), "커뮤니티 C0" → **"무리 #0 · 대표 엔티티 3개"**(번호는 순번), "[rule]/[llm]" → "요약: 규칙 기본문 / LLM 작성", "출처" → **"관계 출처"** + 값마다 설명.
- **전체 규모를 먼저**(요청 ③): 첫 줄에 전체 엔티티·그릴 수 있는 수·관계(구조/공동출현)·무리·유형별 수. 상위 N 옆에 50/120/300/전체 버튼과 "= 전체의 N%". 서버 `/api/graph` 가 `total_entities`·`candidates`·`total_relations`·`total_structural`·`total_cooccur`·`entities_by_type`·`edge_counts_shown` 를 준다.
- **보기 모드** `#g-view`: 개요(상위 N) · 이웃(중심 엔티티 + 1~3홉 — 그래프 검색의 시야) · 무리 하나 · 유형별 배치(열 = 유형).
- **공동출현 숨김** 기본 on(`edge_kinds=structure`): 실DB 는 간선의 96% 가 cooccur 라 구조가 묻힌다.
- hover 툴팁 · 선택 노드의 이웃만 강조 · 커서 기준 확대 · 맞춤/초기화 · 반지름 상한 28px · 라벨은 연결 상위 40개만 · 레이아웃이 식으면 rAF 정지 · HiDPI 이동 속도 보정.
- 상세 패널: 한 줄 요약(연결·문서·무리·출처의 뜻) · 별칭 칩 · 관계를 **출처별 탭**으로 · 문서 링크는 문서 페이지로(예전엔 콘솔로) · "이 노드 중심으로 보기" · 무리 링크.
- **무리 상세**(`/api/community?id=`, CLI `graph community --community N`, MCP `wiki_community`): 구성원·유형·안쪽 관계(출처별)·관련 문서·요약(누가 썼는지). 이름 = LLM 요약 첫 문장 또는 대표 3개.
- 서버 수정: 출처·유형 필터를 넘김 · 무리 필터를 자르기 전에 적용 · `community` 비숫자 400 · 기본 limit 를 세 창구 모두 120 으로.

### 2.4 그래프 진단 탭

소견 목록(영역 필터: 전체/규칙/코퍼스/튜닝/빌드/질의규칙)이 지표 카드 **위**에 온다. 소견마다 증거 표·원인·처방 단계·JSON 조각(복사 / **규칙 탭에 붙여 넣기** — 규칙 탭 상단의 읽기 전용 상자에 넣고 클립보드에 복사한다. 자동 병합은 하지 않는다: 규칙 파일은 사람이 저장하고 저장 전 lint 가 돈다)·명령·확인. **보고서(md) 내려받기**(`/api/graph/profile?format=md`) = CLI `--out` 과 같은 마크다운, LLM 에게 그대로 주는 용도. 이력 표에 소견 error/warn 수.

### 2.5 택하지 않은 것

| 안 | 이유 |
|---|---|
| LLM 에게 그래프를 주고 소견을 쓰게 | 결정적이지 않고 LLM 없는 환경에서 안 된다. LLM 은 보고서를 **읽는 쪽**(optimize 번들·md 내려받기) |
| 처방을 규칙 파일에 자동 적용 | `POST /api/rules` 의 lint 게이트와 "규칙 파일은 사람이 저장한다" 원칙과 충돌 |
| `HUB_WARN_TYPES` 에 org_unit 추가로 IR본부 잡기 | 증상 치료. 원인은 매처 |
| Leiden/Louvain 으로 무리 품질 개선 | 표준 라이브러리 제약, 그리고 지금 문제는 알고리즘보다 cooccur 간선 과잉(매처 오탐). 매처를 고치고 다시 본다 |
| D3 등 외부 시각화 | 오프라인·표준 라이브러리 원칙. 캔버스 코드를 손보는 편이 싸다 |
| 무리 번호를 1부터 | CLI·MCP·DB 가 0부터 쓴다. 창구마다 번호가 다르면 더 헷갈린다 — 번호 대신 **이름**을 앞세운다 |

## 3. 검증

(§3.1 은 회차 종료 시점의 실측. 재현 명령은 각 행에.)

### 3.1 결과

| 검증 | 결과 |
|---|---|
| `python -m unittest tests.test_graph_findings_0924` (매처 7 · 소견 3 · export 5 · 세 창구 2 · HTTP 3) | 20 OK |
| 실데이터 복사본에서 `graph profile --json` | 소견 7건(error 2 · warn 4 · info 1) · 계산 203ms — alias_false_positive 가 IR본부 124/125 · CTO 129/129 를 문맥과 함께 냄, related_key_unmapped 가 obsoletes/updates 와 가짜 타입 70개, junk_titles 105/352 |
| 매처 실측 | "The first director will coordinate synchronous transfers … abbreviation" → 예전 매처 12건 매칭(IR본부×3·CTO×2·COO·CHRO…) → 새 매처 0건, 실제 언급(IR 본부·CTO·AMD·삼성전자·베이스밴드)은 그대로 |
| 전체 단위 테스트 · verify_ui_wiring · verify_web · verify_browser · verify_buttons · verify_tri_surface · verify_mcp · verify_surface_align · verify_docs | §3.2 에 기록 |

### 3.2 회차 종료 실측

**실데이터 복사본에서 새 매처로 재빌드했을 때** (`graph-rules fill-defaults` → `build graph --yes` 18초, 서버는 건드리지 않았다):

| 지표 | 재빌드 전 (예전 매처) | 재빌드 후 (matching 절 켬) |
|---|---|---|
| 허브 상위 5 | IR본부 7,748 · CTO 2,244 · CHRO 846 · COO 580 · SWD-RFC-1812 554 | NOTE-CLI_FLOWS 250 · ISSUE-2001 246 · RX DMA 204 · NOTE-MAKE_SAMPLE_CORPUS_MODEM 134 · RULE-ISR-001 123 |
| 관계 | 14,320 | **3,713** (오탐 엔티티가 만든 공동출현 관계가 빠졌다) |
| cooccur 비중 | 0.96 | 0.846 |
| 무리 | 36 (30개가 1인 무리, C0 이 562개) | 173 (가짜 허브가 모든 것을 한 무리로 붙이던 것이 풀렸다) |
| 고립 비율 | 2.7% | 9.9% (허브가 사라지자 실제로 관계가 없던 노드가 드러났다 — 다음 처방 대상) |
| 소견 `alias_false_positive` | area=**build** "예전 매처로 빌드됨": IR본부 그래프 멘션 125 · 지금 매처 1, CTO 129 · 0, CHRO 128 · 1 … 7개 | 사라짐 |
| 프로파일 계산(소견 포함) | 956ms (소견이 지금 매처로 표본 청크를 다시 매칭하므로 첫 판 205ms 보다 늘었다) | 520ms |

소견 로직의 첫 판은 "별칭 문자열이 단어 안에 **등장하는** 횟수" 를 세서 재빌드 뒤에도 경고했다(스케줄러의 `sched` 가 `schedule_build` 안에 등장 — 새 매처는 안 잡는데). 그래서 **지금 매처가 실제로 잡는 자리**를 따로 세고, 그래프에 저장된 멘션이 지금 매칭의 2배 이상이면 "예전 매처로 빌드됨(build)" 으로, 지금 매처로도 단어 안에서 잡히면 "규칙을 고쳐라(rules)" 로 나눴다. 띄어쓴 이름(RX DMA)·한글 이름은 경계 규칙 대상이 아니므로 세지 않는다.

**하네스 전수**: `python -m unittest discover -s tests` 823 OK(신규 20) · `verify_ui_wiring` OK · `verify_web` 379/379 · `verify_browser` OK · `verify_buttons` OK(새 버튼 fit/reset/graph, gp-run/compare/history 클릭 확인) · `verify_tri_surface` OK([7b] 무리 상세 세 창구 일치) · `verify_mcp` 156/156 · `verify_surface_align` OK(CAPS 에 무리 상세 행) · `verify_stage_align` OK · `verify_docs` OK.
