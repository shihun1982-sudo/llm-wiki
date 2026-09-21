# 2026-09-19 구현 계획서 — 요청 11항목

이 문서는 2026-09-19 에 받은 **최종 요청 11항목**에 대한 판정·설계·검증 계획이다.
각 항목마다 (a) 현재 코드가 실제로 어떤 상태인지 조사한 사실, (b) 무엇이 문제인지, (c) 어떻게 고치기로 했고
**택하지 않은 대안과 그 이유**, (d) 검증 방법 순으로 적는다. 운영 절차는 각 기능의 문서(§끝의 표)로 간다.

| # | 요청 | 상태 |
|---|---|---|
| 1 | 그래프 규칙 확장성 · 최신 트렌드 반영 | **완료 (2026-09-19)** — [GRAPH_RULES.md](../../GRAPH_RULES.md) |
| 2 | 관리자용 초기화 버튼 3종 (데이터/빌드 · 설정 · 로그/이력) | **완료 (2026-09-19)** — [RESET.md](../../RESET.md) |
| 3 | 포팅 시 필요한 모든 연결 정보 문서 + 한 폴더 통합 여부 판단 | **완료 (2026-09-19)** — [PORTING.md](../../PORTING.md) |
| 4 | LLM API / headless opencode 두 경우의 `config.json` 샘플 + 3창구 정렬 확인 | **완료 (2026-09-19)** — [LLM_CONNECT.md](../../LLM_CONNECT.md) |
| 5 | 빌드 과정 점검 (전 채널 · 30명 동시) | **완료 (2026-09-19)** — [BUILD_UNDER_LOAD.md](../../BUILD_UNDER_LOAD.md) |
| 6 | Ask 채널 검색 UI 개선 · 기능 확장 | **완료 (2026-09-20)** — [WEB_UI.md §0.67](../../WEB_UI.md) |
| 7 | Ask 디버그가 실제 시나리오와 같은지 확인 + 라우팅/낱개 도구 설명 | **완료 (2026-09-20)** — [WEB_UI.md §0.67](../../WEB_UI.md) |
| 8 | 파이프라인 › 앙상블 설명 깨짐 | **완료 (2026-09-20)** |
| 9 | Trial 비교 — 문항 수 · 실제 질의 이력 · 단계별 전 지표 | **완료 (2026-09-20)** — [EVAL_TRIAL.md §5.1–5.2](../../EVAL_TRIAL.md) |
| 10 | Evolve 제안 설명 · impact | **완료 (2026-09-19)** — [EVOLVE.md §1.5](../../EVOLVE.md) |
| B | **질의 결과의 근거를 원본으로 연결** (2026-09-20 추가) — 근거 문단(`[C1]` …)과 그래프 관계 표의 항목을 누르면 원본 문서로 | **완료 (2026-09-20)** — [WEB_UI.md §0.675](../../WEB_UI.md) |
| C | **옵저빌리티 › 시스템·규모 개선** (2026-09-20 추가) — 빌드·질의 통계를 포함해 관리자에게 쓸모 있는 지표 | **완료 (2026-09-20)** — [OPS_STATS.md](../../OPS_STATS.md) |
| A | **CLI · Web UI · MCP 전수 정렬 감사** (2026-09-20 추가) — 현재 코드 기준으로 세 창구의 기능·동작이 빠짐없이 맞는지 | **완료 (2026-09-20)** — [SURFACE_AUDIT_0920.md](../../SURFACE_ALIGNMENT.md) |
| 11 | docs 전면 정리 + 수정 시 돌려야 할 테스트 안내 | **완료 (2026-09-20)** — [DOC_MAP.md](../../DOC_MAP.md) · [TESTING_GUIDE.md](../../TESTING_GUIDE.md) · [RELEASE_NOTES.md](../../RELEASE_NOTES.md) 3.2.0 |

---

## 1. 그래프 규칙 — 확장성 점검과 개선

### 1.1 조사한 사실 (현재 상태)

규칙 기반 그래프 추출기는 `llmwiki/graph_rules.py` 의 `RuleExtractor` 이고, 규칙은 `data/rules.json`
(12개 최상위 절, 엔티티 67개)에서 읽는다. 절별로 **데이터만 고쳐서 늘릴 수 있는지**를 따져 보면 이렇다.

| 절 | 무엇 | 데이터만으로 확장? |
|---|---|---|
| `entities` | 표준명 → `{type, aliases}` | ✔ |
| `id_patterns` | 문서 ID 정규식 → canonical ID + type | ✔ |
| `link_rules` | 문서 type × 대상 type → 관계 | ✔ (단, 먼저 맞는 규칙이 이긴다 — §1.2 ③) |
| `explicit_rels` · `related_key_type` | front matter `related.*` → 관계/타입 | ✔ |
| `types_for_cooccur` | 공동출현 관계를 만들 type | ✔ |
| `relation_patterns` | 정규식 → 관계. `value`(잡은 문자열을 무엇으로 볼지)와 `in_chunk`/`in_decision`(어느 범위에서) | **△ value 종류를 늘리려면 코드 수정** |
| `analyst_pattern` | 애널리스트 코멘트 → `comments_on` | ✘ 키 이름·관계명·그룹 의미가 코드에 박혀 있음 |
| `decision_pattern` | 결정 블록 → `decision` 노드 + `decides` | ✘ 〃 |
| `date_patterns` · `money_pattern` · `percent_pattern` | 날짜/금액/퍼센트 | ✘ 〃 (`mentions_date`·`mentions_amount` 관계명도 코드에) |

즉 **"규칙을 데이터로 외부화했다"는 절반만 사실**이다. 새 값 종류(예: `4 ns` 같은 단위 측정값, `rev B1` 같은
버전)를 잡고 싶으면 `_apply_rel_patterns()` 의 `if value == …` 사슬에 분기를 더해야 한다. 이 코퍼스의 실제 질문이
"HW rev B1 에서 t_setup 은 몇 ns 인가?" 인 것을 생각하면, **가장 필요한 값 종류가 바로 늘릴 수 없는 쪽**에 있다.

그리고 관계·타입 어휘에 **제약이 전혀 없다**. `graph_build.py` 가 LLM 추출 결과를 넣는 자리를 보면

```python
store.upsert_entity(eid, …, e.get("type") or "concept", …, "llm", 0.75)     # type 검증 없음
store.add_relation(sid, did, r.get("rel") or "related_to", …, "llm", 0.7)    # rel 검증 없음
```

LLM 이 낸 문자열이 그대로 들어간다. 그래서 실제로
- 자가진화 제안에 `type="relation"`(엔티티 type 이 아님)·`type="CL"`(유효값은 소문자 `cl`) 이 올라왔고,
- 관계명이 `used` / `uses` / `used_by` 처럼 갈라진다 (그래프 확장에서 서로 다른 변으로 취급된다).

§1.5 에서 만든 제안 설명이 이것을 **제안 단계**에서 잡지만, 빌드 단계에는 아직 아무 장치가 없다.

또 하나: 그래프 규칙은 **세 창구 정렬에서 빠져 있다.**

| 창구 | 지금 |
|---|---|
| CLI | `rules merge <파일> --graph` 뿐 — 보기·추가·점검·시험이 없다 |
| Web | 지식 › 그래프 규칙 = `data/rules.json` **원문 textarea** 하나 (`/api/rules` GET/POST). 검증 없이 통째로 덮어쓴다 |
| MCP | 없음 (`wiki_rules` 는 질의 규칙 `query_rules.json` 전용이다) |

질의 규칙(`query_rules.json`)은 2026-09-19 에 `RULE_TYPES` 레지스트리 + `types`/`explain`/`lint`/`test` CLI +
Web 구조 편집기 + MCP `wiki_rules` 로 정리했는데, **그래프 규칙은 그 정리를 받지 못했다**.

### 1.2 고칠 것

① **값 종류 레지스트리 (`VALUE_TYPES`)** — `_apply_rel_patterns` 의 `if/elif` 사슬을 질의 규칙의
`RULE_TYPES` 와 같은 등록형 레지스트리로 바꾼다. 내장 6종(`entity` `date` `money` `percent` `id` `text`)에
더해 이 코퍼스에 필요한 2종을 넣는다.

| 값 종류 | 잡는 것 | 만드는 노드 | 왜 |
|---|---|---|---|
| `measure` | `4 ns` · `1.5 dB` · `100 MHz` — 수치 + 단위 | `metric` (정규화된 `값 단위`) | 평가셋의 "t_setup 은 몇 ns" 류 질문이 숫자에 걸리지 않고 있었다 |
| `version` | `rev B1` · `v1.2` · `A2` | `version` | 같은 사실이 리비전마다 다르다 — 버전 노드가 있어야 "rev B1 에서" 를 좁힐 수 있다 |

② **스칼라 패턴 절 일반화 (`value_patterns`)** — `analyst_pattern` / `decision_pattern` / `date_patterns` /
`money_pattern` / `percent_pattern` 이 각자 코드 분기를 갖는 대신, 이름 하나에 `{regex, value, node_type, rel,
weight, confidence, per_chunk}` 를 적는 **한 절**로 모은다. 기존 키는 로드 시 이 형태로 **자동 번역**해서
옛 `data/rules.json` 도 그대로 돈다(호환). 이러면 "퍼센트도 날짜처럼 청크마다 노드로 남기고 싶다" 가
파일 한 줄로 끝난다 (지금은 `percent` 가 `relation_patterns` 안에서만 쓰이고 청크 단위로는 남지 않는다).

③ **스키마 절 (`schema`) — 타입·관계 어휘와 제약** (이번 개선의 핵심, 최신 트렌드)
최근 KG 구축의 공통 권고는 *스키마를 먼저 선언하고 추출을 거기에 맞춘다*(schema-guided extraction)이다.
자유 문자열 추출은 같은 뜻의 노드/변이 여러 이름으로 흩어져 그래프 검색이 무너진다.

```jsonc
"schema": {
  "entity_types": { "issue": {"desc": "결함 티켓"}, "cl": {"desc": "변경 목록"}, … },
  "relations": {
    "fixes":    {"inverse": "fixed_by", "src": ["cl"], "dst": ["issue"], "desc": "이 CL 이 이 이슈를 고쳤다"},
    "co_occurs":{"symmetric": true},
    "uses":     {"aliases": ["used", "use", "utilizes"]}     // 갈라진 이름을 하나로 모은다
  },
  "on_unknown": "keep"    // keep(기록만) | map(별칭이면 고치고 아니면 기록) | drop(버림)
}
```

- 빌드할 때 규칙·LLM 이 낸 `type`/`rel` 을 이 어휘에 맞춘다. `aliases` 에 있으면 표준 이름으로 **고치고**,
  없으면 `on_unknown` 정책에 따라 남기거나 버리며, **어느 쪽이든 빌드 보고서에 개수와 예시를 남긴다**
  (지금은 이상한 관계가 들어와도 아무도 모른다).
- `inverse` 를 선언하면 `fixes`/`fixed_by` 를 `link_rules` 에 두 번 적을 필요가 없고, 한쪽만 선언해도
  역방향 질의가 된다. 지금은 손으로 두 줄을 맞춰 적고 있어 한쪽만 고치면 조용히 어긋난다.
- `src`/`dst` 타입 제약은 **버리는 데 쓰지 않고 경고로만 쓴다** — 코퍼스가 바뀌는 환경에서 제약이 세면
  멀쩡한 관계를 잃는다. 기본 `on_unknown: "keep"` 도 같은 이유다.

④ **`graph_rules.lint()`** — 질의 규칙에 있는 점검을 그래프 규칙에도. 찾는 것:
깨진 정규식 / 없는 type / 어휘에 없는 관계명 / **가려진 `link_rules`**(앞선 `*` 규칙에 먹혀 영원히 안 걸리는 줄) /
여러 엔티티가 같은 별칭을 가짐(어느 쪽으로 붙을지 모름) / 별칭이 다른 엔티티 이름의 부분문자열 / `inverse` 짝 불일치.

⑤ **세 창구 정렬** — `graph-rules` 계열을 CLI·Web·MCP 에 맞춘다.

| | 명령/자리 |
|---|---|
| CLI | `graph-rules show|types|lint|test "<문장>"|add-entity|add-alias|fill-defaults` |
| Web | 지식 › 그래프 규칙 — 원문 textarea 는 남기되(고급) 위에 **엔티티/별칭 표 · 점검 결과 · 문장 시험** 추가 |
| MCP | `wiki_graph_rules` (읽기 전용: 스키마·엔티티·문장 시험) |

### 1.3 택하지 않은 대안

| 대안 | 왜 안 했나 |
|---|---|
| 규칙을 파이썬 플러그인 파일로 (`rules/*.py` 를 import) | 코퍼스만 바꾸면 되는 환경에 **코드 실행 경로**가 생긴다. 규칙 파일은 자가진화가 자동으로 고치는 대상이라(§EVOLVE) 데이터여야 안전하다 |
| 스키마를 강제(위반 시 관계 버림)로 | 반입 문서가 바뀌는 환경에서 초기 스키마는 반드시 불완전하다. 강제하면 "그래프가 갑자기 비었다" 가 된다. 기본은 기록, 정책으로 선택 |
| RDF/OWL 같은 표준 온톨로지 도입 | 표준 라이브러리만 쓰는 제약(의존성 추가 불가)과 맞지 않고, 이 규모에서 얻는 것보다 복잡도가 크다 |
| 별도 규칙 파일로 분리 (`data/schema.json`) | 절 하나 늘리는 편이 낫다 — 포팅할 때 옮길 파일이 늘어나는 것을 피한다(요청 3번과 충돌) |

### 1.4 구현하면서 실제로 찾은 것 (계획에 없던 것)

① **`graph_build` 의 관계 이름 화이트리스트는 '새 패턴을 버리는' 장치가 아니었다.**
조건이 `(양 끝점이 ents 에 있다) or (r.rel 이 다섯 이름 중 하나)` 였으므로, 끝점이 있는 관계는 이름과 무관하게
저장돼 왔다. 그 다섯 이름(`owner`·`attendee`·`source`·`responsible`·`comments_on`)이 실제로 한 일은 **끝점이 없는
관계를 통과시킨 것**이고, 그것이 `graph_profile` 이 보고하던 dangling 관계의 출처였다. 그 다섯은 모두
`value: "entity"` 패턴이 만드는 관계인데, `_v_entity` 가 노드를 등록하지 않아 생긴 구멍을 이름으로 막아 둔 것이었다.
→ `_v_entity` 가 노드를 등록하게 하고(멘션은 세지 않는다 — 청크 전체 스캔과 이중 계수가 되므로) 이름 목록을 지웠다.
**정직한 기록**: 이 등록은 정상 흐름에서는 관측되지 않는다(잡은 문자열은 청크의 부분이므로 같은 엔티티를 청크
전체 스캔이 이미 등록한다). 뮤테이션 테스트에서도 이 한 가지만 잡히지 않았다. 방어적 보장이며, 이름 목록을
안전하게 지우기 위한 전제다.

② **통계 분류는 진짜로 코드에 박혀 있었다.** `id_relations`(ID 언급에서 나온 결정적 관계) 카운터가 관계 이름
튜플을 하드코딩해 비교하고 있어서, 규칙 파일에 관계 패턴을 하나 더하면 그 관계가 ID 관계로 잘못 세어졌다.
→ `RuleExtractor.field_rel_names()` 가 파일에서 목록을 만든다.

③ **`known_types()` 가 자기 자신을 정당화하고 있었다.** 엔티티 사전에 쓰인 `type` 을 유효 목록에 넣고 있어서,
오타로 들어간 유형이 "사전에 쓰였으니 유효" 가 됐다. 테스트를 쓰다가 드러났다.
→ `schema.entity_types` 선언이 있으면 선언이 이기고, 선언이 없는 예전 파일에서만 추론한다.

④ **이 저장소의 `data/rules.json` 자체가 어휘에서 드리프트해 있었다.** `graph-rules lint` 가 `on_chip` ·
`conforms_to` · `measured_value` · `violates_spec` · `implements` 다섯 관계가 어휘 밖임을 찾아냈다 — 기본 어휘에 넣고
`fill-defaults` 로 파일에 채웠다. 별칭이 이름과 같은 항목 3건(`NVIDIA`/`Nvidia`, `RX DMA`/`rx dma`, `TX DMA`/`tx dma`)도
경고로 남아 있다(무해해서 데이터는 건드리지 않았다).

### 1.5 검증 (실행 결과)

```bat
python -m unittest tests.test_graph_rules_schema     :: 36건 OK (값 종류 · chunk_values · 스키마 · lint · 빌드 배선)
python -m llmwiki graph-rules lint                   :: 오류 0 · 경고 3
python -m llmwiki graph-rules test "rev B1 에서 t_setup 은 4 ns 이다. CL-55302 가 ISSUE-2001 을 고쳤다." --doc-type cl --ext-id CL-55302
python -m unittest discover -s tests -q              :: 572건 OK
python tools/verify/verify_surface_align.py          :: OK (기능 63 · CLI 47 · Web 110 · MCP 20)
python tools/verify/verify_web.py                    :: 실패 0
python tools/verify/verify_buttons.py                :: 버튼 130/130
python tools/verify/verify_docs.py                   :: 어긋남 0 · 고아 문서 0
```

`graph-rules test` 실행 결과 — `4ns`(metric) · `rev B1`(version) 노드가 새로 생기고
`CL-55302 -[fixes]-> ISSUE-2001` 결정적 관계가 함께 잡힌다. 예전에는 사양값과 리비전이 그래프에 남지 않았다.

**뮤테이션 검증** (기능을 되돌려 테스트가 실제로 실패하는지): 값 종류 레지스트리 사용 · 관계 이름 정규화 ·
`chunk_values` 적용 · "선언이 추론을 이김" 네 가지 모두 되돌리면 테스트가 실패한다. `_v_entity` 노드 등록만
잡히지 않는데, 그 이유는 ①에 적은 대로다.

---

## 2. 관리자 초기화 세 가지 (완료)

설계·절차는 [RESET.md](../../RESET.md). 여기에는 **판단의 근거**만 적는다.

### 2.1 왜 세 범위인가

요청이 "데이터/빌드 · 설정 · 로그/이력" 세 개였고, 실제로 **서로 다른 시점에 필요하다.**
코퍼스가 바뀌면 데이터만, 새 환경에 맞추면 설정만, 시험 흔적을 치우면 로그만 지운다. 하나로 합친
"전체 초기화" 를 만들지 않은 이유도 같다 — 셋 중 하나만 필요한 경우가 압도적으로 많은데, 합쳐 두면
필요 없는 것까지 지우게 된다.

### 2.2 조사한 사실

지워야 할 것이 흩어져 있었다: 색인 `build --reset`, 설정 `config reset`(그런데 `config.json` 하나뿐),
요청 이력 `maintenance purge_requests`, 로그 파일은 손으로. 그리고 `data/` 밑 부산물 폴더는
**어느 명령도 건드리지 않았다.** 이 저장소에서 실측:

```
data/requests  562개 ·  60.6 MB      logs/  9개 · 106.8 MB
data/reruns     50개 ·   4.8 MB      data/sweeps · graph_profiles  5개 · 0.3 MB
```

폴더를 복사할 때마다 **170 MB 넘는 앞 환경의 흔적**이 따라갔다. 게다가 `requests`/`reruns`/`sweeps` 는
전부 *지금 색인의 청크 id* 를 가리키므로, 색인만 갈아 끼우면 열 수 없는 껍데기가 된다.

### 2.3 설계에서 고른 것과 그 이유

| 결정 | 이유 |
|---|---|
| **미리보기가 기본**, `--apply` 가 있어야 실행 | 지우는 명령이 기본으로 지워 버리면 안 된다. Web 버튼도 표를 먼저 띄운다 |
| 범위마다 **지우지 않는 것**을 같이 보여 준다 | 사람이 가장 먼저 확인하고 싶은 정보다. "코퍼스는 남나?" 에 화면이 답해야 한다 |
| `security.json`·`.env` 는 **옵션으로만** | 원격에서 설정을 초기화하다 계정이 사라지면 다시 들어갈 길이 없다. 키는 복구 불가 |
| 로그 파일은 **지우지 않고 비운다** | Windows 에서 돌고 있는 서버가 열어 둔 파일을 지우면 그 뒤 모든 로그가 조용히 사라진다 |
| `data` 는 자동 스냅샷 | 이미 `reset_index` 에 있던 안전장치를 그대로 쓴다 |
| MCP 에 두지 않음 | 붙어 있는 LLM 이 색인·설정을 지울 수 있으면 안 된다 |
| 제안(proposals)은 `logs` 기본에서 제외 | 사람이 검토할 후보이지 '흔적' 이 아니다 |
| 임베딩 캐시는 `data` 기본에서 제외 | 내용 주소 캐시라 임베더가 같으면 재사용된다 — 지우면 리빌드가 훨씬 느려진다 |

### 2.4 구현 중 일으킨 사고와 그 대응 (기록)

`tests/test_reset.py` 의 첫 판이 `logs` 범위를 **격리 없이** 실행했다. 로그 폴더는 `Settings` 가 아니라
프로세스 전역(`config.path_for("logs_dir")`)이라, 임시 `data_dir` 을 쓰더라도 로그만은 프로젝트의 진짜
`logs/` 를 가리킨다. 그래서 테스트가 **이 저장소의 로그와 `logs/audit.jsonl`(보안 감사 이력)을 비웠다.**
내용은 복구하지 못했다.

대응: (a) 테스트가 `LLMWIKI_LOGS_DIR_PATH` 로 로그 폴더를 격리하도록 고치고, (b) 그 사실을 확인하는
테스트(`test_only_the_configured_log_dir_is_touched`)를 넣었으며, (c) [RESET.md](../../RESET.md) §7 에
"이 범위를 테스트하려면 반드시 로그 폴더를 격리하라" 를 경고로 남겼다.

### 2.5 검증

```bat
python -m unittest tests.test_reset      :: 29건 — '지우지 않아야 할 것' 위주 (코퍼스·설정·security·색인)
python -m unittest discover -s tests -q  :: 601건 OK
python -m llmwiki reset data             :: 미리보기가 실제 환경에서 무엇을 보고하는지
python tools/verify/verify_surface_align.py
```

## 3. 포팅 연결 정보 + 한 폴더 통합 여부 (완료)

지도와 절차는 [PORTING.md](../../PORTING.md). 여기에는 **"한 폴더에 모으는 게 나은가" 에 대한 판단**만 적는다.

### 3.1 무엇을 조사했나

설정 파일이 20종(`config.paths` 기준), 그중 환경에 묶인 연결 정보는 여섯 갈래였다 —
LLM(역할 10개 × 최대 14키 + 게이트웨이 주소·인증 헤더 이름·추가 헤더 + `agents.json` headless 템플릿 +
`models.json` 카탈로그 + 앙상블), 임베딩·리랭크, MCP 양방향(`mcp_transport/host/port` · `mcp_url` ·
`mcp_sources.json`), 웹·보안(`web_host/port` · `security.json` 의 local/SSO · `docacl.json` · `server.json`),
경로(`corpus_dirs` · `data_dir` · `wiki_dir` · 부산물 3종), 지식 규칙·프롬프트.
평탄한 연결 키만 세어도 **36개**이고 전부 `LLMWIKI_*` 로 덮어쓸 수 있다.

### 3.2 판단: 옮기지 말고 **가리키게** 한다

| 선택지 | 판정 |
|---|---|
| 지금처럼 루트에 흩어 둔다 | ✘ "무엇을 들고 가야 하나" 가 안 보인다. `data/rules.json` 은 설정인데 데이터 폴더에 살아서 색인 초기화와 헷갈린다 |
| 실제로 `conf/` 로 이사시킨다 | ✘ 기존 설치·문서 63편·`setup/` 예시·스크립트의 경로가 전부 깨진다. `.env` 는 편집기·CI 가 루트에서 찾는 관례가 있다 |
| **환경변수 하나로 폴더를 가리킨다** | ✔ 채택 — 깨는 것 없이 "한 폴더" 를 얻는다 |

구현: `LLMWIKI_CONF_DIR` 이 가리키는 폴더에 그 설정 파일이 **있으면** 그것을 쓴다(`config.path_for`).
- 안 쓰면 동작이 **완전히 동일**하다 (기존 설치 보호).
- **일부만** 넣어도 된다 — 없는 파일은 원래 자리.
- 개별 `LLMWIKI_<NAME>_PATH` 가 폴더보다 세다 (한 파일만 딴 데 두는 경우).
- 모으면 `data/rules.json` 이 `rules.json` 이 되어 위 이상함이 사라진다.
- `config bundle --out <폴더>` 가 17종을 모으고, `--from` 이 되돌린다. **`.env` 는 키 이름만 복사**한다 —
  자격증명이 묶음에 딸려 나가면 안 된다(`--include-secrets` 로만 값째).

대상에서 뺀 것: `logs_dir`(운영 산출물) · `themes`(앱 자원) · `eval/questions.json`(코퍼스에 가깝다).

### 3.3 구현 중 찾은 결함

`load_settings()` 가 모듈 상수 `CONFIG_PATH` 를 쓰고 있었다. 그 값은 **import 시점에 한 번만** 계산되므로
나중에 바뀐 환경변수(그리고 새로 만든 `LLMWIKI_CONF_DIR`)를 반영하지 못했다. 실제로 폴더를 가리켜도
`top_k_final` 이 옛 값 그대로였다 — 직접 확인해 보지 않았으면 "파일 자리만 바뀌고 동작은 그대로" 인
기능이 될 뻔했다. `path_for("config")` 로 바꿨고(`save_settings`·`fill_defaults` 도 같이),
테스트 `test_the_value_actually_takes_effect` 가 값이 실제로 유효값에 반영되는지를 지킨다.

### 3.4 이 변경이 일으킨 사고와 그 대응 (기록)

`load_settings()`/`save_settings()` 를 `path_for("config")` 로 바꾼 것에는 **숨은 부작용**이 있었다.
`tests/test_phase0.py` 와 `tests/test_web_api.py` 는 설정 격리를 **모듈 상수 `config.CONFIG_PATH` 를
몽키패치**해서 하고 있었다. 함수가 `path_for()` 를 보게 되자 그 패치가 조용히 무력해졌고,
`preset apply --save` 와 `POST /api/config` 가 **프로젝트의 `config.json`** 에 임시 폴더 경로를 써 버렸다
(`corpus_dirs`·`data_dir`·`wiki_dir` 가 `C:\…\Temp\tmp…` 로, `llm_provider` 가 `mock` 으로).
`verify_all` 의 "부하 뒤 health" 가 `fails=2` 로 이것을 잡아냈다.

복구는 **이번 작업으로 만든 `config bundle` 이 해 줬다** — 몇 시간 전에 만들어 둔 묶음에 정상 설정이
그대로 있었다. 색인(352문서·16,898청크)은 무사했다.

대응 셋:
1. **테스트를 지원되는 격리 수단으로** — `LLMWIKI_CONFIG_PATH`·`LLMWIKI_TUNING_PATH` 환경변수.
   모듈 상수를 바꾸는 것은 계약이 아니다.
2. **조용한 손상을 시끄러운 실패로** — `config._guard_stray_write()`. 읽어 온 자리를 모르는 Settings 가
   `data_dir` 이 기본과 다른 채로 **프로젝트 `config.json`** 에 쓰려 하면 `StraySettingsWrite` 예외.
   조건을 좁게 잡아 `config reset`(기본값 저장)과 격리 경로 저장은 통과한다.
3. **회귀 테스트** — `tests/test_conf_dir.py::StrayWriteGuardTest` 4건.

교훈으로 남길 것: *"경로 해석을 한 곳으로 모으는 변경"은 그 경로를 몽키패치로 가로채던 모든 자리를
같이 찾아야 한다.* 이 저장소에서는 테스트 격리가 그 자리였다.

### 3.5 검증

```bat
python -m unittest tests.test_conf_dir   :: 15건 — 안 쓰면 안 바뀐다 · 값이 실제로 먹는다 · 부분 모으기 · 비밀 제외
python -m unittest discover -s tests -q  :: 616건 OK
python -m llmwiki config bundle --out conf --dry-run
python -m llmwiki config paths           :: 파일별 실제 출처
```

## 4. LLM API ↔ headless opencode (완료)

샘플과 절차는 [LLM_CONNECT.md](../../LLM_CONNECT.md). 여기에는 판단과 증명 방식만.

### 4.1 조사한 사실

전환에 필요한 것은 이미 **전부 데이터**였다 — `llm_provider` 가 `"headless:<에이전트>"` 접두를 해석하고
(`providers.py`), 실행법은 `agents.json` 의 명령 템플릿이며, 역할별로도 `llm_roles.<역할>.provider` 로
따로 정할 수 있다. `setup/config.example.headless.json` 의 `_note_switch` 에 "전환은 두 줄" 이라고
적혀 있기까지 했다. **없던 것은 그것이 사실이라는 증거와, 읽을 수 있는 샘플이었다.**

- 기존 예시 두 파일(`config.example.pat-gateway.json` · `config.example.headless.json`)은
  `config fill-defaults --examples` 때문에 **모든 키가 든 100줄+ 덤프**다. "무엇을 바꿔야 하나" 가
  그 안에 묻혀 있다. 그래서 **바꾸는 키만** 추린 표를 문서에 따로 뒀다 (샘플 5종).
- 전환이 정말 config 만으로 되는지, 그리고 **세 창구가 같이 따라오는지**를 확인하는 자동 검사가 없었다.

### 4.2 만든 것 — 증명 하네스

`tools/verify/verify_llm_switch.py` (검사 18개, 네트워크 없음):

| # | 무엇을 본다 |
|---|---|
| 1 | API 모드에서 **CLI `query` · Web `POST /api/query` · MCP `wiki_query`** 가 모두 동작하고 셋 다 같은 프로바이더를 보고 |
| 2 | `config.json` 의 `llm_provider`·`llm_model` **두 키만** 바꿔 headless 로 전환 |
| 3 | 전환 전후로 **다른 설정 파일과 `llmwiki/*.py` 의 수정 시각이 같다** (= 코드를 안 고쳤다) |
| 4 | 같은 세 창구가 그대로 동작하고 셋 다 headless 를 보고 |
| 5 | **역할 하나만** headless (전역·역할별 두 축) |
| 6 | 되돌리면 원래대로 (단방향이 아니다) |

네트워크 없이 도는 이유: API 쪽은 `mock` 프로바이더, headless 쪽은 `agents.json` 의 `mock` 에이전트
(`python -m llmwiki.headless --mock`)를 쓴다. 실제 게이트웨이·실제 opencode 확인은 `models test --live` 가 맡는다
(둘은 역할이 다르다 — 이쪽은 "구조가 제너럴한가", 저쪽은 "이 환경에 실제로 붙는가").

`verify_all.py` 에 넣어 전체 검증 때마다 돈다.

### 4.3 검사를 만들면서 고친 것

처음 판은 프로바이더 이름을 `llm_report.provider` 와 `roles.<역할>.provider` 에서 찾았는데 둘 다 없었다.
실제 자리는 `models show --json` 의 `roles.<역할>.**name**` 이고, `--json` 출력에는 콘솔 인코딩 때문에
**BOM 이 붙을 수 있다**(`json.loads` 가 바로 실패한다). 둘 다 하네스에서 처리했다.
또 `"mock"` 은 `"headless:mock"` 의 부분 문자열이라 API 모드 단언이 헛돌 수 있어, API 모드에서는
"headless 가 **없다**" 까지 확인하도록 조였다.

### 4.4 검증

```bat
python tools/verify/verify_llm_switch.py   :: 18/18
python -m llmwiki models test --live       :: 실제 환경 (게이트웨이·opencode)
python tools/verify/verify_surface_align.py
```

## 5. 빌드 과정 점검 — 전 채널 · 30명 동시 (완료)

실측표와 권장 운영은 [BUILD_UNDER_LOAD.md](../../BUILD_UNDER_LOAD.md). 여기에는 **접근 방식과 찾은 결함**만.

### 5.1 접근 — 읽지 말고 재라

"30명이 쓰는 중에 빌드해도 되나" 는 코드를 읽어서 답이 안 나온다. 락 정책이 빌드 종류마다 다르고,
읽기는 *쓰기가 기다리고만 있어도* 잠깐 막히며(writer preference), 대기가 길어지면 503 이 된다.
그래서 하네스(`tools/verify/verify_build_load.py`)를 만들어 **30명을 실제로 붙여 놓고** 빌드를 걸었다.

핵심 설계 하나: **빌드가 도는 구간에 시작된 질의만** 따로 집계한다. 시나리오 전체 평균은 빌드 전후의
한가한 시간에 희석되어 "빌드 중에 어땠나" 를 감춘다. 실제로 첫 판은 전체 평균만 봐서 전체 리빌드의
p95 가 610ms 로 보였는데, 구간만 추리니 **5,548ms** 였다.

### 5.2 결과

| 빌드 | 시간 | 빌드 중 질의 | p50 | p95 | 판정 |
|---|---|---|---|---|---|
| 증분 | 1.2s | 265건 | 69ms | 154ms | 영향 없음 |
| 채널 `fts`/`vector`/`graph` | 0.6~7.7s | 51~1,438건 | 44~73ms | 142~503ms | 영향 없음 |
| **전체 리빌드** | 5.7s | **54건** | 53ms | **5,548ms** | **빌드 시간 = 대기 시간** |

실패·거절은 모든 경우 0이었고, 빌드 뒤 색인 무결성도 통과했다. 즉 **깨지지는 않는다.**
문제는 전체 리빌드 동안 서비스가 멈춘 것처럼 보인다는 것이다.

### 5.3 찾은 결함 — 설정을 켜도 아무 일이 일어나지 않았다

문서(CONCURRENCY.md 문제 해결표)가 이 상황에 `reads_during_build=always` 를 권하고 있었다.
켜고 다시 쟀더니 **바뀐 게 없었다**(질의 32건·p50 5,515ms).

원인: `reqmgr.ticket()` 의 완화 분기가 `weight == "write"` 만 보는데, 전체·채널 리빌드는
`weight == "exclusive"` 로 들어온다(`server.py:_start_job`). `weight_for_level()` 은 `"write"` 를
**한 번도 돌려주지 않는다.** 그래서 그 분기는 죽은 코드였고, 문서가 권하던 바로 그 상황에서 설정이 무효였다.

고친 뒤 같은 조건: 빌드 중 질의 **54건 → 893건**, p50 **5,515ms → 54ms**, p95 **5,548ms → 376ms**.
기본값(`incremental`)의 동작은 그대로임을 별도 실행으로 확인했다.

이 프로젝트에서 가장 비싼 결함 유형이다 — 화면에도 문서에도 있으니 고쳐진 줄 알고 넘어간다.
2026-09-18 검증에서도 같은 유형을 3건 찾았었다(README §7). 그래서 회귀 테스트를 락 모드 결정 자체에 걸고
(`tests/test_build_concurrency.py`), 재현 로직이 낡지 않도록 **구현 소스와 대조하는 테스트**까지 넣었다.

### 5.4 부수적으로 확인된 안전장치

- 채널·전체 리빌드는 admin 전용이고 Web 에서는 확인 문구 + 비밀번호 재입력을 거친다. 확인 없이 부르면
  `428 Precondition Required` — 하네스가 이것도 검사한다 (30명이 쓰는 중에 실수로 리빌드가 돌지 않는다).
- 전체 리빌드는 색인을 비우기 전에 자동 스냅샷을 만든다.
- 빌드 전 `health` 가 코퍼스 경로를 확인해, 경로가 비어 있으면 기존 색인을 지우지 않는다.

### 5.5 검증

```bat
python tools/verify/verify_build_load.py --users 30                 :: 26/26
python tools/verify/verify_build_load.py --users 30 --policy always
python -m unittest tests.test_build_concurrency                     :: 10/10
python -m unittest discover -s tests -q                             :: 630 OK
```

## 6. Ask 채널 검색 — 사용자 친화 + 기능 확장 (완료)

화면 설명은 [WEB_UI.md §0.67](../../WEB_UI.md). 여기에는 판단만.

### 6.1 무엇이 불친절했나

이 화면은 원래 "채널 디버그" 였다가 사용자용 검색으로 성격이 바뀌었는데, **말투와 기본값이 디버그 그대로**였다.

| 문제 | 근거 |
|---|---|
| 안내문이 "채널 검색 디버그 — 이 질의로 고른 채널을 돌립니다" | 찾으러 온 사람에게 할 말이 아니다 |
| 빠른 설정이 `전부 OR` / `전부 AND` / `RRF 융합` | 무엇을 고르면 무엇이 달라지는지 알 수 없다 |
| 기본이 FTS 만 켜짐 | "뜻이 가까운 문단" 을 못 찾는데 왜 그런지 화면에 없다 |
| trace 가 항상 펼쳐짐 | 사용자에게 필요 없는 정보가 결과를 밀어낸다 |
| 결과가 없으면 한 줄로 끝 | 다음에 무엇을 할지 모른다 |
| 찾고 나서 이어갈 곳이 없음 | 답변으로도, 고정 근거로도 이어지지 않는다 |

### 6.2 고친 것

**사용성**: 라벨을 사람 말로(`넓게 찾기`/`모두 일치`/`실제 순위`, 각각 언제 쓰는지 툴팁), 발췌에서 **찾은 말 강조**,
**최근 검색어**(브라우저 저장, 8개), trace 접기, 결과 없음일 때 **다음 행동 다섯 가지** 안내,
거른 건수를 요약 줄에 표시(`유형 필터로 N건 제외` · `권한으로 N건 가려짐` — 사라진 이유를 숨기지 않는다).

**이어가기**: `이 말로 답변 받기 →`(검색어를 질의 탭으로) · 행마다 `📌`(이 문서를 고정 근거로 —
"이게 정답인데 왜 안 올라오지" 를 본 자리에서 바로 고친다).

**기능 확장 — 문서 유형 필터**: 코퍼스가 섞여 있으면(이슈·CL·TC·주간보고) 보고 싶은 유형 밖의 문서가
자리를 다 차지한다. 질의 경로의 `doc_types` 는 *가중치를 올리는* 것이라 다른 유형이 여전히 올라온다 —
검색 화면에서 원하는 것은 **거르는** 쪽이라 별도로 넣었다.

설계에서 고른 것:
- 필터를 **채널별 원본 목록부터** 적용한다(문서 접근 제어와 같은 자리). 최종 행에서만 걸러도 표는 맞아
  보이지만 `per_channel` 의 건수·발췌가 실제와 달라진다.
- **없는 유형은 0건**을 돌려준다. 오타가 '전체' 로 조용히 풀리면 필터를 믿을 수 없다.
- 유형 목록은 서버가 준다(`/api/status` 의 `doc_types`, 문서 수 포함) — 코퍼스마다 다르므로 화면에 박지 않는다.

**세 창구 정렬**: 같은 엔진(`retrieval.channel_search`)에 인자 하나를 더하고
CLI `--doc-types issue,cl` · Web `doc_types` · MCP `wiki_search.doc_types` 를 같이 넣었다.
동등성은 `tests/test_search_filters.py::SurfaceParityTest` 가 **MCP 결과와 엔진 결과를 행 단위로 비교**해 지킨다.

### 6.3 검증

```bat
python -m unittest tests.test_search_filters   :: 14건 (필터 동작 + 3창구 동등성)
python tools/verify/verify_cli.py              :: 361/361 (유형 필터 3건 추가)
python tools/verify/verify_buttons.py          :: 134/134 (새 버튼 포함)
python tools/verify/verify_surface_align.py
python -m unittest discover -s tests -q        :: 644 OK
```

## 7. Ask 디버그 — 실제와 같은가 · 설명 (완료)

화면 설명은 [WEB_UI.md §0.67](../../WEB_UI.md). 여기에는 **물음에 대한 답**과 판단만.

### 7.1 "실제 사용자 시나리오와 동일하게 동작하는 거지?" → **아니었다. 세 군데가 달랐다**

| 단계 | 실제 경로(`query_engine.run`) | 해부(`querydebug.inspect_query`) |
|---|---|---|
| 규칙 확장 | `expand(q_search, …)` | `expand(q, …)` — **원문** |
| 채널 라우팅 | `route(q_search, store)` | `route(q, store)` — **원문** |
| 고정 근거 | `match_pins(store, q, route_info["doc_types"])` | `match_pins(store, q)` — 유형 없음 |

`q_search` 는 **시간 표현을 떼어 낸 뒤**의 질의다. 그래서 `지난주 ISSUE-2001 의 원인` 을 넣으면
실제는 `ISSUE-2001 의 원인` 으로 확장·라우팅하는데 화면은 `지난주` 를 포함해 계산하고 있었다.
디버그 화면이 거짓말을 하면 그것으로 내린 판단이 전부 틀어지므로 **가장 먼저 고쳐야 할 종류의 결함**이다.

고친 뒤: 실제와 같은 순서(시간 → 규칙 → 라우팅 → pin)로 계산하고, 시간 표현을 뗐으면 그 사실과
"무엇으로 계산했는지" 를 화면·CLI 에 적는다. 2번 칸에 `입력` 을 따로 보여 준다.
**어긋남 재발 방지**: `tests/test_debug_parity.py` 가 엔진 호출부의 **소스를 읽어** 같은 인자를 쓰는지까지 본다 —
엔진이 인자를 바꾸면 이 테스트가 먼저 깨진다.

### 7.2 "'채널 라우팅 유형 relational' 이 뭔지 모르겠다"

유형 이름만 있고 뜻도 근거도 없었다. 판정에 쓰인 신호는 이미 `route()` 안에 있었으므로 **설명을 그 자리에서 만든다**.

- 유형 4종의 뜻을 `retrieval.ROUTER_KINDS` **한 곳**에 두고 Web·CLI·MCP 가 같은 글을 쓴다.
- `why` 로 **왜 그 유형이 됐는지**를 신호 그대로 돌려준다 (`관계를 묻는 말이 있음: 원인` ·
  `아는 엔티티가 6개 잡힘` · `숫자가 있어 FTS 가중 +0.3`).
- 화면·CLI 에 *"이 유형이 하는 일은 가중치를 정하는 것 하나뿐 — 채널을 끄거나 켜지 않습니다"* 를 못 박았다.
  유형이 채널을 고르는 것으로 오해하기 쉬운 자리다.

### 7.3 "맨 아래 토글 줄이 뭔지 모르겠다 · 임시로 테스트해 볼 수 있나?"

그 줄은 **지금 켜져 있는 단계**였고, 읽기 전용이었다. 무엇을 뜻하는지도 안 적혀 있었다.
→ 무슨 뜻인지 적고(꺼진 단계는 위 결과에 반영되지 않는다는 것까지), **칩을 눌러 임시로 뒤집어 볼 수 있게** 했다.
요청 단위 오버라이드라 `config.json` 은 바뀌지 않는다(되돌리기 링크 포함). 영구 변경은 설정 › 파이프라인.

"낱개 도구" 도 이름만으로는 용도를 알 수 없었다 → **"위 해부의 한 칸만 따로 시험"** 이라고 적고,
도구마다 해부의 몇 번 칸에 해당하는지와 언제 쓰는지를 붙였다.

### 7.4 검증 도구에서 찾은 헛도는 검사 — 그리고 그것이 드러낸 진짜 결함

버튼 전수 검사의 `btn-dbg` 는 **입력이 빈 채로 눌려** 아무것도 렌더하지 않고 통과하고 있었다(`dom=+0`).
프리필을 넣었는데도 동작하지 않아 한동안 원인을 못 찾았다.

진짜 원인은 8번 작업 중 `verify_browser` 의 콘솔 오류 수집이 잡았다 —
**`ask.js` 가 `loaders` 를 LW 에서 구조분해하지 않아 그 IIFE 가 로드 중에 죽어 있었다**
(6번에서 `loaders.search = …` 를 넣으면서 들어간 실수다). 그래서 그 뒤에 있는 `$('#btn-dbg').onclick`
할당이 **아예 실행되지 않았고**, 버튼은 눌려도 아무 일이 없었다.

배운 것: **버튼 전수 검사와 API 검사는 이 결함을 잡지 못한다.** 핸들러가 없으면 클릭이 조용히 무시되고,
서버는 멀쩡하다. 잡은 것은 *페이지의 콘솔 오류를 읽는* 검사였다. 세 검사가 서로를 대신하지 못한다.

고친 뒤 `btn-dbg` 는 `dom=+3756` 로 실제 렌더를 확인하며, EXPECT(다섯 구간 + 임시 토글 줄)를 걸어 두었다.

### 7.5 검증

```bat
python -m unittest tests.test_debug_parity   :: 14건 (실제 경로 정합 · 라우터 설명 · 토글 반영)
python -m llmwiki inspect "지난주 ISSUE-2001 의 원인은 무엇인가"
python tools/verify/verify_buttons.py        :: 134/134
python -m unittest discover -s tests -q      :: 658 OK
```

## 8. 파이프라인 › 앙상블 설명이 깨져 보이던 것 (완료)

### 8.1 증상과 원인

도식이 칸 없이 한 줄 글자로 뭉개져 보였다 (`→앙상블 OFF … 또는앙상블 ON`).

원인은 **CSS 스코프**다. 앙상블 편집기는 원래 Settings › 모델(`#tab-models`) 안에 있었고, 2026-09-18 에
Pipeline › 앙상블(`#tab-ensemble`)로 **옮겨졌는데 CSS 는 `#tab-models` 스코프에 남았다.**
`display:flex`, 칸 테두리, 격자 레이아웃이 전부 먹지 않아 span 들이 그냥 이어 붙은 글자가 됐다.

그리고 이것은 도식만의 문제가 아니었다 — `.ens-body` `.ens-grid` `.ens-h` `.ens-m` `.ens-line` `.ens-actual`
`.ens-members` `.ens-eff` `tr.ens-row` `details.ens` 까지 **편집기 전체가 무스타일**이었다.
사용자가 보고한 것은 그중 눈에 가장 띄는 한 조각이었을 뿐이다.

### 8.2 고친 방식

스코프를 **탭이 아니라 컴포넌트에** 건다 (`#tab-models .ens-* → .ens-*`). 도식의 기본 모양도 마찬가지다.
`#tab-models` 아래에 남아 있던 중복 정의는 지웠다 — 두 곳에 두면 또 한쪽만 옮겨졌을 때 같은 일이 난다.

### 8.3 재발 방지 — 정적 검사

`tools/verify/verify_ui_wiring.py` 에 규칙 하나를 더했다:

> 어떤 CSS 클래스가 **`#tab-X` 아래에만** 정의돼 있는데 그 클래스를 **JS 가 만들어 내면** 경고한다.
> JS 는 어느 탭에든 렌더할 수 있으므로, 탭에 매는 것은 그 탭 전용 마크업일 때만 안전하다.

고치기 전 상태로 16개가 걸렸고(전부 앙상블 편집기), 고친 뒤 0개다. 모델 탭 전용 4개(`cat` `ell` `ell2` `in-use`)는
허용 목록에 두었다. 뮤테이션 확인: `.ens-body` 를 다시 `#tab-models` 스코프로 되돌리면 검사가 즉시 실패한다.

이 검사가 필요한 이유는 8.4 가 말해 준다.

### 8.4 이 작업이 드러낸 더 큰 결함

브라우저 렌더 검사를 돌리자 **전혀 다른 오류**가 잡혔다 —
`ask.js:567 Uncaught ReferenceError: loaders is not defined`.
6번 작업에서 `loaders.search = …` 를 넣으면서 `loaders` 를 LW 에서 구조분해하지 않았고,
그 결과 **그 IIFE 전체가 로드 중에 죽어** 뒤쪽의 이벤트 핸들러들이 등록되지 않았다(§7.4).

버튼 전수 검사(134개)도 API 검사(377개)도 이것을 잡지 못했다 — 핸들러가 없으면 클릭은 조용히 무시되고
서버는 멀쩡하기 때문이다. **페이지의 콘솔 오류를 읽는 검사만이** 잡았다.

### 8.5 검증

```bat
python tools/verify/verify_ui_wiring.py   :: 탭에 매인 CSS 검사 포함
python tools/verify/verify_browser.py     :: 콘솔 오류 0 (BROWSER OK)
python tools/verify/verify_buttons.py     :: 134/134 · btn-dbg 가 실제로 렌더(dom=+3756)
python tools/verify/verify_responsive.py  :: 폭 5종
```

## 9. Trial 비교 — 문항 원천과 단계별 지표 (완료)

절차는 [EVAL_TRIAL.md §5.1–5.2](../../EVAL_TRIAL.md). 여기에는 세 물음에 대한 답만.

### 9.1 "테스트 몇 개를 비교하는 거야?" → **고정 25문항, 고를 방법이 없었다**

`run_trial` 이 `evalset.load_questions()` 를 그대로 썼다. Web·CLI 어디에도 문항을 고르는 입구가 없었다.
→ **문항 원천**을 골라 넣는다: `evalset`(기본) · `queries`(실제 질의 이력) · 직접 준 목록.

### 9.2 "실제 질의 history 를 보든지 해야 하는 거 아니야?" → 맞다. 안 보고 있었다

`query_log` 에 실제 질의가 쌓이는데 trial 은 **한 번도 보지 않았다.** 두 가지 문제가 있었다:
(a) 평가셋은 사람이 미리 적은 목록이라 실제 질문과 다르고, (b) 이 저장소에서는 **평가셋 자체가 코퍼스에
색인돼 있어** `hit@k` 가 "오염을 얼마나 피했나" 를 재고 있다(`eval --check` 가 찾아낸 사실).

→ `evalset.from_query_log(store, days, limit, only)`: 기간·건수로 고르고 같은 질문은 한 번만,
`only` 로 👎·평가 있는 것·근거 못 찾은 것만 고를 수 있다.

**설계에서 중요한 선택**: 이력에는 정답이 없으므로 `hit@k`·`mrr`·`term_recall`·`answer_term_recall` 을
계산하지 않고, **"계산할 수 없음 — 0점이 아닙니다"** 를 비교 화면과 markdown 에 못 박았다.
그냥 0으로 두면 "이력으로 돌렸더니 품질이 폭락했다" 는 잘못된 결론이 난다 — 이 기능이 낼 수 있는
가장 큰 사고라서 문구를 데이터에 실어 보낸다(`unavailable_metrics` · `unavailable_why`).

### 9.3 "지표가 제한적이다. 단계·역할별로 볼 수 있어야 하지 않나" → 데이터는 있었고 버리고 있었다

저장하던 것은 **파이프라인 최종 결과 12개**뿐. 그런데 각 문항의 요청 trace 에는 단계별 ms·토큰·호출수·SQL 수가
이미 들어 있었다(profiler). trial 이 그것을 읽지 않고 버렸을 뿐이다.

→ `trials.collect_stages()` 가 문항들의 trace 를 훑어 단계별로 합치고(같은 이름이 여러 번 돌면 합산),
질의 1건당 평균으로 저장한다. `compare()` 가 나란히 놓고 Δms·Δ토큰을 내며 **달라진 단계를 앞으로** 정렬한다
(질의당 ±5ms 또는 ±50토큰 기준 — 26개를 다 보여 주면 아무것도 안 보인다).

이 저장소 실제 질의 5건 실측:

```
answer_llm    avg 15173.0ms · 토큰 53442 · LLM 20회
claim_check   avg 13987.8ms · 토큰 41869 · LLM 17회   ← 답변 생성과 맞먹는 비용
rerank_llm    avg  3234.8ms · 토큰 11536 · LLM  5회
query_expand  avg  2552.7ms · 토큰  1325 · LLM  5회
```

최종 지표(`avg_ms`, `tokens_per_query`)만으로는 절대 보이지 않던 사실이다.

### 9.4 호환

`_stages`·`_source` 를 `summary` 안에 넣어 **저장 형식을 바꾸지 않았다.** 2026-09-20 이전 trial 에는 없으므로
`compare` 가 단계 절을 건너뛰고 *"다시 돌리면 채워집니다"* 라고 적는다 — 회귀 테스트로 지킨다.

### 9.5 검증

```bat
python -m unittest tests.test_trial_sources   :: 20건 (이력 원천 · 단계 집계 · 계산 불가 표시 · 구버전 호환)
python -m llmwiki trial run --name hist --source queries --days 7 --limit 5
python -m llmwiki trial compare hist <다른trial>
python -m unittest discover -s tests -q       :: 678 OK
```

## 10. Evolve 제안 설명 · impact (완료)

구현 내용과 근거는 [EVOLVE.md §1.5](../../EVOLVE.md) 에 적었다. 요지만 옮기면:

- `llmwiki/proposal_explain.py` 가 제안 하나를 **LLM 없이** 설명으로 바꾼다 — 한 줄 요약 · 평문 설명 ·
  바뀌는 파일 경로 · before/after diff · 영향(리빌드 등급 · 영향 범위 · 위험도 · 되돌리는 법) · 값 점검.
- 운영 DB 대기 124건 중 **43건이 승인해도 실패하거나 조용히 무효**인 제안이었다(대상 엔티티 없는 alias 24,
  없는 type 11, 별칭=이름 6, 공백/헤딩 2). 화면에 `[X]` 로 드러난다.
- `apply_proposal` 이 회귀평가 **전에** 막고, 이름 꼬리 공백은 적용할 때 지운다.
- 나쁜 제안이 *만들어지던* 자리도 고쳤다 — `forensic.py` 가 붙일 대상 없는 `alias` 를 올리던 것을 `entity` 로,
  `memory.consolidate()` 의 묶음 키를 `alias|엔티티|별칭` 으로.
- CLI `evolve show <id>` · Web Evolve 제안 카드 · MCP `wiki_evolve(id=…)` 가 같은 설명을 쓴다.
- 테스트 `tests/test_proposal_explain.py` 29건. 네 가지 뮤테이션(필수 키 검증 제거 · type 오류를 info 로 강등 ·
  적용 전 차단 해제 · 공백 제거 해제)을 넣으면 모두 실패하는 것을 확인했다 — 테스트가 헛돌지 않는다는 증거.

---

## B. 질의 결과의 근거를 원본으로 연결 (완료)

화면 설명은 [WEB_UI.md §0.675](../../WEB_UI.md). 판단만 적는다.

### B.1 무엇이 없었나

답변의 근거를 보고도 원문으로 갈 길이 없었다. 채널 검색 결과는 이미 문서 화면으로 가는데
**답변의 근거 문단**과 **그래프 관계 표**가 안 갔다 — 가장 중요한 자리가 빠져 있었던 셈이다.

### B.2 설계에서 고른 것

| 결정 | 이유 |
|---|---|
| chunk 는 **그 문서 화면에서** 펼친다 (따로 띄우지 않음) | 문단만 보여 주면 앞뒤 맥락을 잃는다. 문서를 열고 그 문단으로 스크롤한다 |
| 그래프의 src/dst 는 **엔티티 화면**으로 | 엔티티 화면에 그 엔티티가 나온 **문서 참조 목록**이 있다 — 거기가 원본으로 가는 갈림길이다 |
| 이름→id 해석을 **서버에서** | 그래프 표는 이름(`CL-55303`)만 들고 있다. 화면이 id 규칙(`e:cl-55303`)을 만들면 규칙이 두 곳에 생기고 표기가 조금만 달라도 빗나간다. `/api/entity?name=` 이 `entity_id_for()` → 엔티티 FTS 순으로 찾는다 |
| 문서 id 가 없는 근거는 **누를 수 없게** + 이유 툴팁 | 눌러도 아무 일이 없는 것이 가장 나쁘다 |
| 링크 바인딩을 한 함수로 (`bindEvidenceLinks`) | 근거 카드·그래프 표·채널 검색이 같은 동작을 해야 한다. 한쪽만 되면 더 헷갈린다 |

### B.3 고치면서 발견한 것

**`LW.openEntity` 는 불리기만 하고 정의된 적이 없었다.** 지식 › 문서 화면의 엔티티 링크가 예전부터
조용히 아무 일도 하지 않고 있었다(`knowledge.js` 가 `LW.openEntity &&` 로 감싸 두어 오류도 안 났다).
`showEntity` 를 내보내 정의하니 그 링크도 함께 살아났다.

이번 작업에서 이런 "죽은 채로 통과하던 것" 이 세 번째다 — `btn-dbg`(핸들러 미등록), 앙상블 CSS(탭 스코프),
그리고 이것. 셋 다 **눌러 봐야만** 드러난다.

### B.4 검증

```bat
python -m unittest tests.test_evidence_links   :: 8건 (이름으로 엔티티 · 근거가 doc_id/chunk_id 를 들고 오는가)
python tools/verify/verify_browser.py          :: 콘솔 오류 0
python tools/verify/verify_buttons.py          :: 134/134
python -m unittest discover -s tests -q        :: 686 OK
```

## C. 옵저빌리티 › 시스템·규모 개선 (완료)

2026-09-20 요청: *"옵저빌리티 - 시스템, 규모 메뉴를 개선하고 싶어. 빌드, 질의에 대한 각종 통계 포함해서
관리자에게 도움이 될만한 통계 추가해줘."* B 를 끝낸 뒤, A(정렬 감사) 앞에 했다.
운영 절차와 읽는 법은 [OPS_STATS.md](../../OPS_STATS.md). 여기에는 **판단의 근거**만 적는다.

지금 그 화면에 있는 것: 색인 규모(문서·청크·임베딩·엔티티·관계), 캐시, 워처, 유지보수 버튼, 초기화(2번에서 추가),
성능/토큰 설정. **없는 것**이 관리자가 정작 궁금해하는 쪽이다 —

| 관리자가 묻는 것 | 지금 답할 수 있나 | 데이터는 어디 있나 |
|---|---|---|
| 빌드가 얼마나 걸렸나, 어느 단계가 느린가 | ✘ (마지막 빌드 요약만) | `kv.last_build` · `requests`(빌드 trace) |
| 임베딩을 몇 건 새로 했고 캐시는 얼마나 맞았나 | △ (`embed report` 는 CLI 만) | `embed_runs` |
| 질의가 하루에 몇 건, 누가, 언제 몰리나 | ✘ | `query_log` · `requests` |
| 질의 지연 분포(p50/p95)와 느린 질의 | ✘ | `requests` |
| 토큰을 얼마나 쓰나 (역할별·모델별) | ✘ | `requests` 의 tokens |
| 근거를 못 찾은 비율·부정 피드백 | ✘ | `query_log.feedback` · `forensics` |
| 어느 문서가 실제로 답에 쓰이나 (죽은 문서) | ✘ | `requests` 의 인용 |
| 디스크가 어디서 커지나 | △ (로그 총량만) | `data/` · `logs/` · DB 테이블별 |

### C.1 택한 방식

**집계 모듈 하나**(`llmwiki/opstats.py`)를 두고 세 창구가 그것만 부른다 — CLI `stats --full`,
`GET /api/opstats`, MCP `wiki_status(full=true)`. 창구마다 SQL 을 쓰면 같은 화면의 숫자가 갈리는데,
이 저장소는 이미 그런 사고를 한 번 겪었다(6번 항목의 Ask 디버그 ↔ 실제 질의 경로 불일치).

**택하지 않은 대안**

| 대안 | 왜 안 했나 |
|---|---|
| 집계 결과를 테이블에 적재(요약 테이블·야간 집계) | 스키마 마이그레이션과 적재 작업이 생기고, "통계가 최신이 아니다" 라는 새 고장 모드가 생긴다. 지금 규모(질의 수천 건)에서는 즉석 집계가 1초 안이다 |
| 화면 자동 갱신에 얹기 | 옵저빌리티 화면은 주기 갱신을 한다. 여기에 DB 스캔을 얹으면 사람이 화면을 켜 두는 것만으로 부하가 된다. **버튼을 눌렀을 때만** 집계한다 |
| 기간 제한 없이 전부 | 요청 trace 는 계속 쌓인다. `days`(기본 7)와 `max_rows`(기본 5000) 상한을 두고, 상한에 걸리면 `capped` 를 돌려줘 사람이 알게 한다 |
| 새 지표를 위해 수집 코드를 추가 | **필요한 데이터는 이미 다 있었다** — `requests`(단계 trace·ms·토큰), `query_log`(피드백·창구·사용자), `embed_runs`, `forensics`. 새로 수집할 것이 없어서 이 항목은 순수하게 *읽기* 작업이다 |

### C.2 설계에서 조심한 것

- **읽기 전용을 테스트로 고정한다.** `test_is_read_only` 가 집계 전후의 `query_log`·`requests` 행 수와 색인
  통계를 비교한다. 통계를 내다가 무엇을 쓰기 시작하면 바로 터진다.
- **표본 수를 항상 함께 돌려준다.** p95 는 표본이 3건이면 사실상 최대값이다. 숫자만 크게 보여 주면
  운영자가 그것을 근거로 설정을 바꾼다. `latency.n` 을 화면에 같이 띄우는 이유다.
- **빈 환경에서 죽지 않는다.** 새로 세운 서버에서 옵저빌리티 화면을 열면 빌드도 질의도 0건이다.
  `last_build` 가 없으면 "빌드 기록이 없습니다" 로 내려가고 p95 는 `None` 이다(`EmptyEnvironmentTest`).
- **섹션 레지스트리.** `SECTIONS` + `SECTION_HELP` 가 짝을 이루고, 짝이 빠지면
  `test_every_section_has_help_text` 가 잡는다 — 이 저장소의 다른 레지스트리들과 같은 규약이다.

### C.3 붙이자마자 나온 발견

실데이터로 처음 돌린 결과가 이 항목의 값을 스스로 증명했다.

| 값 | 뜻 |
|---|---|
| `data/snapshots` **899.4 MB**(464개) > DB **496.3 MB** | 스냅샷이 색인보다 커졌다. `snapshot prune --keep 3` 힌트가 붙는다 |
| `graph_build` **122.2초** / 전체 빌드 138초 | 빌드 시간의 88%가 한 단계다. 어디를 손봐야 하는지가 한 줄로 나온다 |
| 지연 p50 **22.4초** · p95 **99.4초** | 질의당 7,010 토큰과 함께 보면 LLM 호출이 지배적이라는 것이 보인다 |
| 임베딩 캐시 적중 **12.5%** | 캐시가 거의 못 맞고 있다 |

### C.4 검증

`tests/test_opstats.py` 16건(읽기 전용·빈 환경·섹션 필터·표본 수·정렬 순서·텍스트 렌더),
`verify_buttons.py` 에 `btn-ops` EXPECT(`dom=+8806`, 시스템 탭 25/25),
`verify_surface_align.py` CAPS 행 추가(3창구 대조).

## A. CLI · Web UI · MCP 전수 정렬 감사 (완료)

2026-09-20 에 추가로 요청받았다: *"현재 수정 및 code base 기준으로 web ui, mcp, cli 기능과 동작이 모두
align 되어 있는지 반드시 빠짐없이 모두 확인해 달라."* 6~10 을 끝낸 뒤, 11(문서 정리) **앞에** 했다.
결과와 절차는 [SURFACE_AUDIT_0920.md](../../SURFACE_ALIGNMENT.md). 여기에는 **판단의 근거**만 적는다.

지금 있는 것과 이번에 더 해야 할 것:

| 지금 있는 검사 | 무엇을 본다 | 빈 곳 |
|---|---|---|
| `verify_surface_align.py` | **사람이 적은 대조표**(기능 64개 × CLI/Web/MCP) 와 실제 목록의 대조 | 표에 적지 않은 기능은 애초에 검사되지 않는다 |
| `verify_cli.py` (358) · `verify_web.py` (377) · `verify_mcp.py` (129) | 창구마다 **따로** 동작 | 같은 기능이 창구마다 **같은 답을 주는지**는 안 본다 |
| `verify_stage_align.py` (16) | 단계 레지스트리 ↔ 라벨 ↔ 손잡이 | 질의 파이프라인 한정 |
| `tests/test_surface_consistency.py` (8) | 일부 기능의 3창구 동등성 | 표본이 8개뿐 |

그래서 이번 감사는 세 가지를 한다.

1. **전수 목록화** — CLI 서브커맨드·액션, Web 경로, MCP 도구를 **코드에서 직접** 뽑아
   대조표(`CAPS`)와 맞춰, 표에 빠진 것이 하나도 없는지 본다. (지금은 표가 기준이라 표의 누락을 못 잡는다.)
2. **동작 동등성** — 같은 입력에 세 창구가 **같은 결과**를 주는지 실제로 호출해 비교한다
   (질의·검색·해부·문서·엔티티·제안 설명·그래프 규칙·상태 등).
3. **의도적 제외의 근거** — MCP 에 없는 것마다 "왜 없는지" 가 표에 적혀 있는지 확인한다
   (파괴적·설정 변경은 일부러 뺀 것이라, 근거 없는 누락과 구분되어야 한다).

### A.1 택한 방식 — 기준을 표에서 **코드로** 옮겼다

셋 중 1번이 이 항목의 전부다. 기존 하네스는 `CAPS` 표를 순회하며 "이 이름이 실제로 있나" 를 봤다.
방향이 한쪽뿐이라 **표에 안 적은 기능은 검사 대상이 아니었다** — "표에 없으니 통과" 가 성립한다.
그래서 역방향을 추가했다: `cli.py` 의 `add_parser`, `server.py` 의 라우팅 조건, `mcp.TOOLS` 에서
전수를 뽑아 **표가 그것을 모두 덮는지** 본다. 덮지 않으면 실패다.

Web 경로는 문자열 리터럴을 긁는 대신 **라우팅 조건**(`u.path == …` · `startswith` · `in (…)`)에서 뽑는다.
주석이나 설명 문자열까지 경로로 세면 표를 억지로 채우게 되고, 그러면 표가 다시 거짓말을 시작한다.

**택하지 않은 대안**

| 대안 | 왜 안 했나 |
|---|---|
| 표를 없애고 코드에서 자동 생성 | 표의 값은 이름이 아니라 **`note` 의 판단**이다("MCP 에 두지 않는 이유"). 자동 생성은 그 판단을 버린다 |
| 경로마다 줄을 하나씩 | 112줄이 되고 기능 단위의 뜻이 사라진다. 대신 **한 칸에 여러 이름**을 넣을 수 있게 했다 |
| 표 밖 항목을 경고로만 | 경고는 쌓이면 배경이 된다. 실패여야 다음 사람이 줄을 더한다 |
| 동등성을 단위 테스트로만 | 단위 테스트는 엔진 함수를 직접 부른다 — **CLI 가 그 함수를 그렇게 부르는지**는 보지 않는다. 그래서 프로세스를 띄우는 하네스를 따로 만들었다 |

### A.2 찾은 것

표 밖에 있던 36개(CLI 3 · Web 33)는 전부 **존재하는 기능**이었고 표가 따라가지 못한 것이었다 — 갈래와
판단은 [SURFACE_AUDIT_0920.md §2](../../SURFACE_ALIGNMENT.md). 동작 쪽에서 실제 결함 두 가지가 나왔다:

1. **`rules explain ""` 의 종료코드** — Web 400 · MCP 오류인데 CLI 만 0 이었다. `ns.args` 가 `[""]` 라
   "인자가 있다" 로 통과했다. 스크립트에서 변수가 비어 넘어가는 흔한 상황인데, 그때 CLI 만 성공을 보고한다.
   **실패도 정렬돼야 한다**는 것이 이 항목에서 얻은 규칙이고, 하네스 §13 이 그것을 지킨다.
2. **빈 채로 통과하던 비교 2개** — 시간 비교가 없는 키(`start`)를 봐서 `None == None` 이었고,
   제안 설명 비교는 `skipTest` 로 조용히 넘어갔다. 새 하네스의 `same()` 은 **비교 대상이 하나뿐이거나
   비어 있으면 실패**로 본다. 이 저장소에서 반복해 나온 함정("설정은 됐는데 아무 일도 안 하는 기능")의
   테스트 판이다.

### A.3 검증

정적 쪽은 변이로 확인했다 — 줄을 지우면 `표 밖`, `note` 를 지우면 `이유가 없다` 가 뜬다.
동작 쪽은 `stats --json` 의 문서 수만 999 로 바꿔 `창구별 값이 다르다: {"cli": 999, "web": 38, "mcp": 38}`
를 확인하고 되돌렸다. `verify_all.py` 에 `tri_surface` 줄을 넣었다.

## 11. docs 전면 정리 + 수정 시 돌릴 테스트 안내 (완료)

2026-09-19 요청의 마지막 항목: *"docs 아래에 문서들 최신 코드, 동작 기준으로 전면 정리 필요해. 상세하고
완벽하게 문서화 되어야 해. 그리고 사람이나 LLM 이 수정 했을 때, 어떤 테스트 케이스를 어떤 방법으로
돌려봐야 하는지도 문서화 되어야 해."*

### 11.1 조사한 사실 — 무엇이 실제로 낡아 있었나

문서가 68개다. "전부 다시 쓴다" 는 현실적이지도, 옳지도 않다(§11.2). 그래서 먼저 **무엇이 실제로
어긋나 있는지**를 기계적으로 셌다. `verify_docs.py` 는 이미 링크·명령·설정 키·API 경로·고아 문서를
보고 있었고 어긋남 0 이었다. 그것이 못 보던 세 가지가 남아 있었다.

| 못 보던 것 | 실제로 발견된 것 |
|---|---|
| **지금의 규모를 말하는 숫자** | `SYSTEM_ARCHITECTURE.md` 가 CLI 46 · Web 107 · MCP 19 · 단위 테스트 428 · 하네스 22 라고 적고 있었다(실제 48 · 112 · 20 · 711 · 25). `ARCHITECTURE_V3.md` 는 MCP 도구 7종 |
| **하네스가 자기 목록에 있는가** | `tools/verify/README.md` 에 하네스 25개 중 **14개가 빠져 있었다** — 목록에 없으면 아무도 돌리지 않는다 |
| **새 기능이 참조 문서에 반영됐는가** | `MCP.md` 의 `wiki_status` 행에 `full`/`days`/`sections` 가 없고, `CLI_FLOWS.md` 카탈로그에 `stats --full`·`reset`·`graph-rules` 가 없고, `WEB_UI.md` 에 운영 통계 패널이 없었다 |

### 11.2 택한 방식과 택하지 않은 대안

**택하지 않은 것: 전 문서 재작성.** 이 저장소의 문서는 성격이 둘로 갈린다 — **현행 문서**(기능·절차·구조)와
**기록 문서**(구현 계획·검증 보고·리뷰). 기록 문서의 숫자와 결함 목록은 **그날의 사실**이고, 그것을 지금
값으로 고치면 "왜 이렇게 됐나" 를 되짚을 근거가 사라진다. 실제로 이 회차에서만 여섯 건의 결함이
"예전에 이렇게 했다가 이렇게 고쳤다" 는 기록 덕분에 원인을 빨리 찾았다.

그래서 **정리의 대상은 현행 문서로 한정**하고, 대신 **둘을 구분하는 방법**을 문서로 만들었다 —
[DOC_MAP.md](../../DOC_MAP.md). 새로 오는 사람(또는 LLM)이 가장 먼저 겪는 문제가
"68개 중 무엇을 믿어야 하나" 이기 때문이다.

**택하지 않은 것: 사람이 주기적으로 훑기.** 이미 그 방식으로 놓쳐서 위 표의 것들이 남았다.
대신 **낡으면 실패하게** 만들었다.

| 새 검사 | 어떻게 |
|---|---|
| 규모 숫자 | 글쓴이가 `<!--live:cli-->48개` 처럼 **표시**한 숫자만 코드와 대조한다. HTML 주석이라 화면에는 안 보인다 |
| 하네스 목록 | `tools/verify/verify_*.py` 가 전부 `tools/verify/README.md` 에 있는가 |

표시 방식을 고른 이유: 처음에는 `"CLI 명령 (\d+)개"` 같은 문장 패턴으로 잡으려 했는데, 산문까지 걸렸다 —
"표 밖에 있던 CLI 명령 3개" 는 총계가 아니다. **총계인지 아닌지는 글쓴이만 안다.** 그래서 선언하게 했다.
표시가 없으면 검사하지 않으므로 기록 문서는 자동으로 제외된다.

### 11.3 테스트 안내 (요청의 뒷부분)

[TESTING_GUIDE.md](../../TESTING_GUIDE.md) 가 이미 "변경 영역 → 단위 테스트 · 하네스 · 함께 볼 문서" 표를
갖고 있었다. 이번에 더한 것:

1. **영역 7개 추가** — 운영 통계 · 관리자 초기화 · 제안 설명 · Trial 원천 · 질의 해부 · 검색 필터/근거 링크 · 문서.
2. **§2.5 테스트를 새로 쓸 때의 규칙** — 이 회차의 사고 두 가지가 그대로 규칙이 됐다.
   (a) **테스트가 진짜 폴더를 건드리면 안 된다**: `reset` 테스트가 이 저장소의 `logs/` 를 비워
   감사 기록을 잃었다(§2.4). 폴더를 만들거나 지우는 테스트는 경로 환경변수를 먼저 덮어쓴다.
   (b) **변이 시험**: 통과하는 테스트가 아무것도 지키지 않는 경우가 반복해서 나왔다(양쪽 다 `None`,
   `skipTest`, 절대 참이 안 되는 조건). 새 테스트를 넣었으면 **고친 것을 일부러 되돌려 실패를 확인**한다.
   (c) 세 창구에 다 있는 기능은 단위 테스트(엔진)와 하네스(실제 프로세스) **양쪽**에 비교를 더한다.
3. 실패 메시지 해석 표에 새 하네스의 메시지 4종을 추가했다.

### 11.4 검증

`verify_docs.py` 를 두 번 변이시켜 확인했다 — 표시한 숫자를 19 로 바꾸면
`<!--live:mcp--> 옆 숫자가 19 인데 지금은 20 다`, 하네스 README 에서 한 줄을 지우면
`verify_stage_align.py 가 하네스 목록에 없다` 가 뜬다. 되돌린 뒤 `RESULT OK`.
버전은 `3.2.0` 으로 올리고 [RELEASE_NOTES.md](../../RELEASE_NOTES.md) 맨 위 절을 썼다
(`python -m llmwiki --version` · `/api/status.version` · MCP `serverInfo.version` 이 같은 값을 낸다).

## 문서 지도

| 항목 | 상세 문서 |
|---|---|
| 그래프 규칙 (1) | [GRAPH_RULES.md](../../GRAPH_RULES.md) — 이번에 새로 만든다 |
| 초기화 (2) | [BRINGUP_GUIDE.md](../../BRINGUP_GUIDE.md) · [REBUILD_SPEC.md](../2026-09-17/REBUILD_SPEC.md) |
| 포팅 (3·4) | [BRINGUP_GUIDE.md](../../BRINGUP_GUIDE.md) · [CONFIG_REFERENCE.md](../../CONFIG_REFERENCE.md) · [HEADLESS.md](../../HEADLESS.md) |
| 빌드·동시성 (5) | [REBUILD_SPEC.md](../2026-09-17/REBUILD_SPEC.md) · [CONCURRENCY.md](../../CONCURRENCY.md) |
| Ask 화면 (6·7·8) | [WEB_UI.md](../../WEB_UI.md) · [ENSEMBLE.md](../../ENSEMBLE.md) |
| 평가·Trial (9) | [EVAL_TRIAL.md](../../EVAL_TRIAL.md) |
| 자가진화 (10) | [EVOLVE.md](../../EVOLVE.md) §1.5 |
| 근거 → 원본 연결 (B) | [WEB_UI.md](../../WEB_UI.md) §0.675 |
| 운영 통계 (C) | [OPS_STATS.md](../../OPS_STATS.md) — 이번에 새로 만들었다 |
| 세 창구 정렬 감사 (A) | [SURFACE_AUDIT_0920.md](../../SURFACE_ALIGNMENT.md) — 〃 |
| 문서 정리 (11) | [DOC_MAP.md](../../DOC_MAP.md) — 〃 (현행 ↔ 기록 구분과 읽는 순서) · [TESTING_GUIDE.md](../../TESTING_GUIDE.md) §2.5 |
| 테스트 안내 (11) | [TESTING_GUIDE.md](../../TESTING_GUIDE.md) |
