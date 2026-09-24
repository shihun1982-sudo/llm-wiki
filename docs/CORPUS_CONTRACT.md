# Corpus Contract — 문서 계약 (schema_version 1)

문서를 사람이 쓰든 LLM 이 생성하든 **어떤 필드를 넣어야 FTS / Vector / Graph 빌드와 검색이 잘 동작하는지**를 정한 계약이다.
계약을 지키면 (1) ID 노드(ISSUE-2041, CL-55321)와 결정적 관계(CL→Issue) 가 생기고, (2) 시간 질의("지난주") 가 문서 날짜로 동작하며,
(3) 문서 유형 부스트·pin·MCP 도구(`wiki_doc`, `wiki_related`) 가 정확해진다. 계약이 없어도 색인은 되지만 이 기능들은 추론값에 의존한다.

## 1. 파일 형식

- 마크다운(`.md`) + **YAML front matter**(파일 맨 앞 `---` 블록). `.txt` 도 같은 규칙.
- 인코딩 UTF-8. 파일명은 ID 를 포함하면 좋다(`ISSUE-2041.md`) — front matter 가 없을 때 추론에 쓰인다.
- 스키마 정의는 `schemas/<doc_type>.json`(공통 `common.json` 상속), 추론 규칙은 `schemas/infer.json`, 버전 마이그레이션은 `schemas/migrations.json`.
- 검사: `python -m llmwiki corpus lint` (빌드 시 자동, Web › Corpus › 문서 계약) · 단일 파일 `corpus lint-file <path>` · 예시 `corpus example issue`.

## 2. 공통 필드 (모든 유형)

| 필드 | 필수 | 형식 | 검색/그래프에서의 역할 |
|---|---|---|---|
| `schema_version` | ✔ | int (현재 1) | 마이그레이션 기준. 구버전은 `migrations.json` 규칙으로 자동 정규화 (파일은 수정하지 않음) |
| `doc_type` | ✔ | issue · cl · sw_design · hw_design · coding_rule · weekly_report · tc_list · (확장) | 그래프 노드 유형, 라우터 문서유형 힌트/부스트(`doc_type_boost`), coverage 집계, MCP 필터 |
| `id` | ✔ | 유형별 패턴 (아래) | **그래프 노드 ID = 문서 노드**. 모든 청크의 FTS 토큰에 포함되어 `ISSUE-2041 원인?` 같은 질의가 문서 전체를 찾음 |
| `title` | ✔ | 문자열 | 위키/근거 표시, 노드 별칭 |
| `date` | ✔ | `YYYY-MM-DD` | 시간 질의 boost/filter, 최신성 부스트, trial/포렌식 집계. 없으면 파일명 → mtime 순으로 추론 |
| `author` | | 문자열 | 표시 |
| `status` | 유형별 | enum | 필터/표시 (예: issue → open·analyzing·fixed·verified·closed·wontfix) |
| `tags` | | 리스트 | FTS 토큰(모든 청크), 문서 카드 임베딩 |
| `module` | | 리스트 | SW 모듈/컴포넌트 — FTS 토큰, code map 연결 |
| `hw` | | `{chip, rev}` | FTS 토큰(`rev_b1`), HW 리비전 필터 |
| `related` | | `{issues:[], cls:[], docs:[], tcs:[], rules:[]}` | **explicit 관계**(provenance=explicit, confidence 0.98). 키별 관계 이름은 `data/rules.json → explicit_rels` |
| `summary` | | 문자열 | 문서 카드(doc_vector 채널) |

### 2.1 그래프 진단이 코퍼스에 요구하는 것 (2026-09-24)

`graph profile` 의 소견(area = corpus)은 다음을 코퍼스 쪽 처방으로 낸다 — [GRAPH_PROFILE.md](GRAPH_PROFILE.md) §2.5:

| 소견 | 문서에서 할 일 |
|---|---|
| `junk_titles` | 파일 첫 줄이 제목이 되므로 코드·shebang 으로 시작하는 파일은 front matter `title:` 을 넣는다 (가져온 NOTE 문서가 흔히 이렇다) |
| `id_missing` | `id:` 를 유형 규약(§3)대로 — ID 가 없으면 제목이 노드가 되어 다른 문서의 `related.*` 가 닿지 못한다 |
| `related_key_unmapped` (rules 쪽) | `related.<key>` 를 새로 쓰면 `data/rules.json` 의 `related_key_type`·`explicit_rels` 에도 키를 적는다. 없으면 관계는 `references` 로 뭉개지고 대상 타입이 지어진다 |
| `structure_share_low` · `dead_rule_no_docs` | 문서끼리의 관계는 `related.*` 와 본문의 ID 표기에서만 온다 — 둘을 채워야 구조 관계가 생긴다 |
| `uncovered_docs` | 본문 용어가 사전(`entities`)에 없거나 본문이 너무 짧으면 그래프가 그 문서를 못 찾는다 |

## 3. 유형별 규칙

| doc_type | ID 패턴 | 추가 필수 | 권장 섹션(`## …`) | 결정적 관계(link_rules) |
|---|---|---|---|---|
| `issue` | `ISSUE-\d{3,7}` | `status` | 현상(필수) · 원인 · 분석 · 수정 · 검증 | 본문의 CL-… → `fixed_by`, ISSUE-… → `related_issue`, TC-… → `verified_by` |
| `cl` | `CL-\d{3,8}` | `related.issues` | 변경 내용 · 영향 범위 · 테스트 | ISSUE-… → `fixes` (front matter 는 explicit, 본문 언급은 rule) |
| `sw_design` | `SWD-[A-Z0-9_-]+` | `module` | 개요 · 구조 · 인터페이스 · 동작 흐름 · 제약 | 언급 ID → `references` |
| `hw_design` | `HWD-[A-Z0-9_-]+` | `hw` | 개요 · 레지스터 · 타이밍 · SW 제어 시퀀스 · **Revision History(필수)** | 언급 ID → `references` |
| `coding_rule` | `RULE-[A-Z0-9_-]+` | – | 규칙(필수) · 근거 · 예시 · 예외 | ISSUE-… → `motivated_by` |
| `weekly_report` | `WR-YYYY-Www` | `period {from,to}` | 업무 요약 · 이슈 요약 · CL 리뷰 요약 · 다음 주 계획 | ISSUE-… → `reports`, CL-… → `reviews` |
| `tc_list` | `TC-[A-Z0-9_-]+` | – | 목적 · 사전 조건 · 절차 · 기대 결과 | ISSUE-… → `verifies` |

새 유형(예: `build_log`)을 추가하려면 `schemas/build_log.json` 을 만들고(`extends: common`, `id_pattern`, `fields`, `sections`), 필요하면
`data/rules.json → id_patterns / link_rules` 에 ID 패턴과 관계 규칙을 추가한다. 코드 변경은 필요 없다.

## 4. 예시

```markdown
---
schema_version: 1
doc_type: issue
id: ISSUE-2041
title: RX 경로 DMA underrun 시 PHY 재시작 실패
date: 2026-08-21
author: hong.gd
status: fixed
severity: major
tags: [rx, dma, phy, modem-b1]
module: [rx_dma, phy_ctrl]
hw: {chip: MDM9x, rev: B1}
related: {issues: [ISSUE-1980], cls: [CL-55321, CL-55402], tcs: [TC-RX-DMA-007]}
summary: FIFO 임계값 오류로 underrun 후 PHY 재시작이 실패. CL-55321 로 수정.
---

# ISSUE-2041 RX 경로 DMA underrun 시 PHY 재시작 실패

## 현상
…

## 원인
FIFO 임계값 설정 오류 (0x20 → 0x40 필요). HW rev B1 에서 t_setup 마진 축소.

## 분석
…

## 수정
CL-55321 에서 RX_DMA_CTRL 기록 순서 수정.

## 검증
TC-RX-DMA-007 통과.
```

CL 문서는 `related.issues` 가 **필수**다 (lint error). 이 한 줄이 `CL-55321 -[fixes]-> ISSUE-2041` explicit 관계를 만들고, 이슈 노드의 doc_refs 에 CL 문서가 연결된다.

## 5. lint 규칙 요약

| 수준 | 조건 | 결과 |
|---|---|---|
| error | front matter 가 있는데 필수 필드 누락 · enum 위반 · ID 패턴 불일치 · CL 의 related.issues 없음 · date 형식 오류 | 색인은 되지만 `build` 결과 alerts 와 health(`schema_lint`) 에 경고. `corpus lint` 로 목록 |
| warn | front matter 없음(추론) · schema_version 구버전 · 필수 섹션 누락 · 추론된 유형/ID | 리포트만 |
| info | 권장 섹션 절반 이상 누락 | 리포트만 |

## 6. 빌드에서 일어나는 일

1. `load_corpus`: front matter 파싱(pyyaml 있으면 사용, 없으면 내장 파서) → 본문만 청킹, 해시는 원문 전체.
2. `chunk_index`: `normalize_meta()` → `doc_meta` 테이블(doc_type, ext_id, date/ts, tags, modules, hw, related, lint) + 모든 청크의 FTS `tokens` 컬럼에 메타 토큰(id, doctype_x, tags, modules, rev, related ids, date) 추가.
3. `graph_build`: 문서 노드 = `id`(type=doc_type) → 본문 ID 언급은 `id_patterns` 로 노드화하고 `link_rules` 로 관계(provenance=rule), front matter `related.*` 는 provenance=explicit. 공동출현은 cooccur, LLM 추출은 llm.
4. 질의: `time_scope` 가 `doc_meta.ts` 로 boost/filter, 라우터 힌트가 doc_type 부스트, 그래프 검색은 provenance 가중(`provenance_w`) 으로 explicit/rule 관계를 우선 탐색하고 노드 `doc_refs` 로 원본 문서를 후보에 올린다.

## 7. MCP 로 가져온 raw data

`mcp_sources.json` 의 ingest 매핑이 raw record → 위 계약을 따르는 md(front matter 포함) 로 `data/mcp_cache/<source>/<doc_type>/<id>.md` 에 저장하므로,
외부 데이터도 같은 lint/그래프/시간 규칙을 탄다. `doc_type` 은 매핑에서 지정(issue/cl/build …). 새 유형이면 스키마 파일을 추가한다.

## 8. 형식이 없는 문서를 넣기 (범용 변환기)

팀에 쌓인 자료는 대개 계약 형식이 아니다. 메모·소스코드·JSON·로그·HTML·PDF·docx 를 그대로 넣으려면 변환기를 쓴다.

```bat
python tools\corpus_ingest.py D:\team-docs --out corpus\imported
python tools\corpus_ingest.py --verify-only --out corpus\imported    :: 나중에 무손실을 다시 확인
```

**본문은 건드리지 않는다.** 변환기는 front matter 와 제목 줄만 앞에 붙이고 원문을 그대로 이어 붙인다(코드·로그는 펜스로 감싸되 내용은 그대로).
그리고 원문의 SHA-1 을 `body_sha1`, 길이를 `body_chars` 로 기록해 두므로, `--verify-only` 가 변환된 문서에서 본문을 다시 떼어 내 해시를 대조한다.
한 글자라도 달라지면 그 파일이 보고된다.

| 입력 | 무손실인가 | 비고 |
|---|---|---|
| `.md` `.txt` `.rst` `.csv` `.json` `.yaml` `.log` `.py` `.c` `.h` 등 텍스트 | **예** | 본문 그대로. 해시로 재검증 가능 |
| `.html` `.pdf` `.docx` | 아니오 (텍스트 추출) | 표·그림·서식이 사라진다. **원본을 `_originals/` 에 보관**하고 `_ingest_manifest.json` 에 `lossless: false` 와 이유를 남긴다 |

`_originals/` 는 색인에서 제외된다(`_archive`, `__pycache__`, `node_modules` 도 마찬가지). 원본을 남기되 같은 내용이 두 번 색인되지 않게 하기 위한 것이다.

유형을 추론할 수 없는 문서는 `doc_type: note` 가 된다(`schemas/note.json`). id 는 `NOTE-<파일명>` 이고,
`source_path`·`source_format`·`extracted` 필드로 어디서 온 무엇인지 추적한다. 나중에 제대로 된 유형으로 옮기려면 front matter 의 `doc_type` 과 `id` 만 고치면 된다.

변환 결과는 `_ingest_manifest.json` 한 파일에 정리된다 — 파일별 상태(ok/skip/fail), 무손실 여부와 그 이유, 원본 경로, 바이트 수. 변환 후 `corpus lint` 로 계약 위반을 확인하고 `build` 를 돌린다.
