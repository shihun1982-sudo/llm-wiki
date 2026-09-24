# GRAPH_RULES — 문서에서 그래프를 만드는 규칙 (`data/rules.json`)

> 대상: 코퍼스를 이 시스템에 올리는 사람. 여기를 고치면 **다음 `build graph` 부터** 그래프가 달라진다.
> 질의를 넓히는 [QUERY_RULES.md](QUERY_RULES.md)(`query_rules.json`)와 **다른 파일, 다른 시점**이다 —
> 저쪽은 질의할 때마다 쓰이고, 이쪽은 빌드할 때 쓰인다.
> 빌드가 끝난 *그래프* 의 진단은 [GRAPH_PROFILE.md](GRAPH_PROFILE.md).

## 0. 한 장 요약

```
문서 ──▶ 청크 ──▶ RuleExtractor.extract_chunk ──▶ 노드(엔티티) + 변(관계) ──▶ graph_build ──▶ DB
                        ▲                                      ▲
                 data/rules.json                          schema 절이 이름을 정리
```

| 무엇을 하고 싶나 | 고칠 절 | 확인 |
|---|---|---|
| 우리 조직/제품 이름을 노드로 잡고 싶다 | `entities` | `graph-rules add-entity` · `graph-rules test "<문장>"` |
| 짧은 약어(IR·BB·CTO)가 영단어 안(`first`·`abbreviation`)에서 잡혀 가짜 허브가 된다 | `matching` · `entities[*].match` (§1.1) | `graph-rules test "the first director"` 가 아무것도 안 잡아야 한다 → `build graph` → `graph profile` |
| 같은 것을 부르는 다른 표기를 한 노드로 모으고 싶다 | `entities[*].aliases` | `graph-rules add-alias` |
| `ISSUE-2041` 같은 사내 ID 를 노드로 만들고 싶다 | `id_patterns` | `graph-rules test "ISSUE-2041"` |
| "CL 문서가 이슈를 언급하면 *고쳤다*로 잇고 싶다" | `link_rules` | `graph-rules test "…" --doc-type cl` |
| 본문 필드(`**담당**: …`)를 관계로 만들고 싶다 | `relation_patterns` | 〃 |
| `4 ns` 같은 사양값·`rev B1` 같은 버전을 노드로 남기고 싶다 | `chunk_values` | 〃 |
| 관계 이름이 `uses`/`used` 로 갈라진다 | `schema.relations[*].aliases` | `graph-rules lint` |
| front matter `related.cls` 를 관계로 | `explicit_rels` · `related_key_type` | `build graph` 뒤 `graph profile` |

```bat
python -m llmwiki graph-rules types      :: 쓸 수 있는 유형·값 종류·관계 어휘
python -m llmwiki graph-rules lint       :: 빌드 전 정적 점검 (오류가 있으면 종료 코드 1)
python -m llmwiki graph-rules test "CL-55302 가 ISSUE-2001 을 고쳤다" --doc-type cl --ext-id CL-55302
```

## 1. 파일의 절

| 절 | 무엇을 정하나 |
|---|---|
| `entities` | 표준명 → `{type, aliases}`. 본문에서 이름/별칭을 찾아 노드로 만든다 (긴 별칭이 먼저 매칭된다) |
| `id_patterns` | `{type, regex, canonical}` — 사내 문서 ID 를 잡아 canonical 형태의 노드로 |
| `link_rules` | `{when_doc_type, target_type, rel, weight, confidence}` — 문서 유형 × 언급된 ID 유형 → 결정적 관계 |
| `relation_patterns` | 본문 필드 정규식 → 관계 (§2) |
| `chunk_values` | 청크마다 남길 스칼라 값 (§3) |
| `schema` | 타입·관계 **어휘**와 모르는 이름 정책 (§4) |
| `types_for_cooccur` | 공동출현(`co_occurs`)·`mentions` 관계를 만들 유형. 여기 없는 유형은 노드로만 남는다 |
| `explicit_rels` · `related_key_type` | front matter `related.*` → 관계 이름 / 대상 유형 |
| `analyst_pattern` · `decision_pattern` | 애널리스트 코멘트(`comments_on`) · 결정 블록(`decision` 노드 + `decides`) |
| `date_patterns` · `money_pattern` · `percent_pattern` · `measure_pattern` · `version_pattern` | 스칼라 값을 잡는 정규식 (§3 에서 쓴다) |

`graph-rules fill-defaults` 가 빠진 절과 `schema` 안의 빠진 표준 어휘를 채운다 — **이미 적어 둔 값은 건드리지 않는다.**

### 1.1 `matching` — 사전 별칭을 **어떻게** 찾나 (2026-09-24)

```jsonc
"matching": {
  "ascii_word_boundary": true,      // ASCII 별칭은 앞뒤가 영숫자가 아니어야 매칭 — "first" 의 IR, "vector" 의 CTO 를 잡지 않는다
  "case_sensitive_max_len": 3       // 길이 3 이하 ASCII 별칭(IR·BB·CTO)은 대소문자를 구분. 0 = 모두 무시(예전 동작)
},
"entities": {
  "IR본부": {"type": "org_unit", "aliases": ["IR"], "match": {"whole_word": true, "case_sensitive": true}}   // 엔티티별 덮어쓰기 (선택)
}
```

왜: 예전 매처는 모든 별칭을 **경계 없이·대소문자 무시**로 찾았다. 실데이터에서 허브 1~5위(IR본부 degree 7,748 · CTO 2,244 · CHRO · COO · 베이스밴드)가
전부 `corpus_d[ir]s`·`ve[cto]r`·`syn[chro]nous`·`[coo]rdinate`·`a[bb]reviation` 의 오탐이었다. 한글 별칭은 조사가 붙으므로(베이스밴드는) 경계 규칙을 적용하지 않는다.
질의 쪽(`retrieval._match_entities`)도 같은 경계 규칙을 쓴다 — "first step" 이 IR본부를 시드로 잡던 것도 같은 버그였다.

- 기본값은 켜져 있고 `graph-rules fill-defaults` 가 절을 채운다. **재빌드(`build graph`) 전까지 기존 그래프는 예전 결과**다 — `graph profile` 의 소견 `alias_false_positive` 가 그것을 알린다.
- 되돌리기: `ascii_word_boundary: false`, `case_sensitive_max_len: 0`.
- `lint` 가 `matching`·`match` 의 모르는 키를 알린다. 시험: `graph-rules test "The first director will coordinate"` → 아무것도 잡히지 않아야 한다.

## 2. `relation_patterns` — 본문 필드를 관계로

```jsonc
{
  "name": "affected_module",
  "regex": "\\*\\*(?:영향\\s*모듈|모듈)\\*\\*\\s*[:：]\\s*([^\\n]+)",
  "value": "text",            // 잡은 문자열을 **무엇으로 읽을지** (§2.1)
  "node_type": "module",      // value=text 일 때 만들 노드의 유형
  "split": ",",               // 여러 개면 나눈다
  "in_chunk":    {"rel": "affects_module", "weight": 0.8, "confidence": 0.8, "desc": "영향 모듈: "},
  "in_decision": {"rel": "owner", "weight": 1.0, "confidence": 0.9}
}
```

- `in_chunk` — 청크 전체에서 찾고, **출발 노드는 문서**.
- `in_decision` — `### D1.` 결정 블록 안에서 찾고, **출발 노드는 그 결정**.
- 둘 다 적으면 범위마다 다른 관계 이름을 줄 수 있다 (담당이 결정 안에서는 `owner`, 밖에서는 `responsible`).

### 2.1 값 종류 (`value`)

`llmwiki/graph_rules.py` 의 **레지스트리**(`VALUE_TYPES`)에서 온다. `graph-rules types` 가 현재 목록을 출력한다.

| `value` | 잡은 문자열을 | 만드는 노드 유형 |
|---|---|---|
| `entity` | `entities` 사전에서 찾는다 | 사전에 적힌 유형 |
| `id` | `id_patterns` 로 문서 ID 를 찾는다 | 그 패턴의 `type` |
| `date` | `date_patterns` | `date` |
| `money` | `money_pattern` | `amount` |
| `percent` | `percent_pattern` | `percent` |
| `measure` | `measure_pattern` — `4 ns` · `1.5 dB` · `100 MHz` (공백을 지워 표기를 통일) | `metric` |
| `version` | `version_pattern` — `rev B1` · `v1.2` | `version` |
| `text` | 잡은 문자열 자체 (`node_type` 으로 유형 지정, `split` 으로 나눔) | `node_type` (기본 `term`) |

`measure` 와 `version` 은 2026-09-19 에 추가했다. 이 코퍼스의 실제 질문이 *"HW rev B1 에서 t_setup 은 몇 ns 인가?"* 인데,
그전에는 숫자에 걸리는 값 종류가 금액·퍼센트뿐이라 **사양값이 그래프에 남지 않았다.**

**새 값 종류를 만들려면** (코드 한 곳만 늘린다):

```python
from llmwiki import graph_rules as gr
def _resolve(ex, val, pat, add):          # 잡은 문자열 → 대상 노드 id 목록
    return [add(val.strip().upper(), pat.get("node_type") or "part", "", 0.9)]
gr.register_value_type(gr.ValueType("partno", "부품번호", "P/N 을 part 노드로", _resolve, "part"))
```

등록하면 규칙 파일에서 `"value": "partno"` 로 바로 쓸 수 있고, `graph-rules types` · Web · MCP 설명에 자동으로 나온다.
`plugins/` 에서 등록해도 된다.

## 3. `chunk_values` — 청크마다 남기는 스칼라

```jsonc
"chunk_values": [
  {"name": "date",    "value": "date",    "rel": "mentions_date",    "per_chunk": 8, "weight": 0.3, "confidence": 0.6},
  {"name": "measure", "value": "measure", "rel": "mentions_measure", "per_chunk": 8, "weight": 0.4, "confidence": 0.7},
  {"name": "version", "value": "version", "rel": "mentions_version", "per_chunk": 4, "weight": 0.5, "confidence": 0.8}
]
```

- `per_chunk` 는 청크당 최대 개수. **`0` 이면 그 종류를 끈다** (파일 한 줄로 끄고 켠다).
- 이 절이 통째로 없는 **예전 파일**은 예전 동작(날짜·금액만, `dates_per_chunk`/`amounts_per_chunk` 튜닝값 적용)으로 돈다.
- 주의: 여기서 만든 노드를 `types_for_cooccur` 에 넣지 말 것. 날짜·금액·버전은 어느 문서에나 나오므로
  **허브**가 되어 그래프 검색이 모든 문서로 번진다 (`graph profile` 의 소견 `alias_false_positive`·`hub_type_policy_mismatch` 가 잡아 준다).

## 4. `schema` — 타입·관계 어휘 (2026-09-19)

### 왜 필요했나

규칙과 LLM 이 만들어 낸 `type`/`rel` 문자열이 **아무 검증 없이** 저장돼 왔다. 그 결과:

- 같은 뜻의 관계가 `uses` / `used` / `utilizes` 로 갈라져 그래프 확장에서 서로 다른 변이 됐다.
- 엔티티 유형에 `relation`(유형이 아님) · `CL`(유효값은 소문자 `cl`) 같은 값이 들어왔다. 이런 노드는
  등록은 되지만 `types_for_cooccur` 에 걸리지 않아 **관계가 하나도 생기지 않는다** — 조용히 무효다.

최근 지식그래프 구축의 공통 권고는 *스키마를 먼저 선언하고 추출을 거기에 맞추는 것*(schema-guided extraction)이다.
자유 문자열 추출은 같은 뜻의 노드/변이 여러 이름으로 흩어져 검색이 무너진다.

### 모양

```jsonc
"schema": {
  "on_unknown": "keep",
  "entity_types": { "metric": {"desc": "수치+단위 사양값"}, "version": {"desc": "리비전/버전"}, … },
  "relations": {
    "fixes":     {"inverse": "fixed_by", "src": ["cl"], "dst": ["issue"], "desc": "이 CL 이 이 이슈를 고쳤다"},
    "fixed_by":  {"inverse": "fixes"},
    "co_occurs": {"symmetric": true},
    "uses":      {"aliases": ["use", "used", "utilizes", "using"]}
  }
}
```

| 키 | 뜻 |
|---|---|
| `entity_types` | **선언된** 엔티티 유형. 선언이 있으면 이것이 유효 목록이 된다 (§4.1) |
| `relations[*].aliases` | 갈라진 이름을 표준 이름으로 모은다 — 규칙 경로와 LLM 경로 모두에 적용 |
| `relations[*].inverse` | 역관계. `fixes`/`fixed_by` 를 두 곳에 손으로 맞춰 적을 필요가 없다 |
| `relations[*].symmetric` | 방향이 없는 관계 (`co_occurs`) |
| `relations[*].src`/`dst` | 기대하는 끝점 유형. **버리는 데 쓰지 않고** `lint` 의 경고로만 쓴다 |
| `on_unknown` | 어휘 밖 관계를 만났을 때 — `keep`(기본: 그대로 두되 보고) · `map`(별칭이면 고치고 나머지는 보고) · `drop`(버림) |

`keep` 이 기본인 이유: 반입 문서가 바뀌는 환경에서 초기 스키마는 반드시 불완전하다. 강제로 버리면
"그래프가 갑자기 비었다" 가 된다. 어느 정책이든 **어휘 밖 이름은 빌드 보고서에 개수와 예시로 남는다**
(빌드 로그의 `schema: 어휘 밖 관계 N종(M건) …` 줄, 그리고 빌드 stats 의 `schema` 항목).

### 4.1 유효한 엔티티 유형은 어떻게 정해지나

`graph_rules.known_types()` 가 계산한다:

```
schema.entity_types ∪ types_for_cooccur ∪ id_patterns[*].type ∪ related_key_type.values ∪ link_rules[*].target_type
```

`schema.entity_types` 가 **없는 예전 파일**에서만 `entities[*].type` 도 유효한 것으로 본다.
선언이 있으면 사전에 쓰인 유형을 자동으로 인정하지 않는다 — 인정하면 오타로 들어간 유형이
"사전에 쓰였으니 유효하다"고 스스로를 정당화해서 검증이 아무것도 못 잡는다.

이 목록은 [EVOLVE.md §1.5](EVOLVE.md) 의 제안 설명도 쓴다 — `entity` 제안의 `type` 이 여기 없으면 `[X]` 로 막힌다.

## 5. `graph-rules lint` — 빌드 전 정적 점검

파일**만** 보고 "이 규칙은 애초에 돌 수 없다" 를 찾는다. 빌드된 그래프를 보는 `graph profile`(= "이 규칙이 아무것도
못 만들었다")와 역할이 다르다. 둘 다 돌리는 것이 맞다.

| 찾는 것 | 등급 | 왜 |
|---|---|---|
| 깨진 정규식 (`analyst_pattern`·`decision_pattern`·`*_pattern` 등) | error | 빌드 시작 때 컴파일되어 **빌드가 멈춘다** |
| 깨진 정규식 (`relation_patterns`·`id_patterns`) | error | 그 패턴만 조용히 무시된다 |
| 없는 값 종류 (`value`) | error | `text` 로 떨어져 엉뚱한 노드가 생긴다 |
| 없는 엔티티 유형 | error | 등록돼도 관계가 안 생긴다 (대소문자 차이면 고칠 값을 알려 준다) |
| 여러 엔티티가 같은 별칭을 가짐 | error | 어느 쪽으로 붙을지 정해지지 않는다 |
| 어휘에 없는 관계 이름 | warn | `on_unknown` 정책에 따라 버려질 수 있다 |
| 앞선 규칙에 **가려진** `link_rules` | warn | 먼저 맞는 규칙이 이기므로 영원히 안 걸린다 (`*` 규칙을 위에 두면 그 아래가 전부 죽는다) |
| `inverse` 짝 불일치 | warn | 한쪽만 고치면 조용히 어긋난다 |
| 별칭이 이름과 같음 / 이름 앞뒤 공백 | warn | 효과가 없다 |

Web 의 **원문 JSON 저장**은 저장 전에 이 점검을 돌려 **error 가 있으면 막는다**
(고치는 중간 상태를 저장하려면 API 에 `force: true`).

## 6. 세 창구

| | 어디에 |
|---|---|
| CLI | `graph-rules show \| types \| lint \| test "<문장>" \| add-entity <이름> <type> [별칭…] \| add-alias <엔티티> <별칭…> \| fill-defaults \| path` |
| Web | 지식 › **그래프 규칙** — 요약 숫자 · 점검 · 문장 시험 · 엔티티 사전 표와 추가 폼 · 원문 JSON(고급) |
| MCP | `wiki_graph_rules` (읽기 전용) — `action=types \| lint \| test`. 규칙 추가는 `wiki_propose` 의 `entity`/`alias` 제안으로 올리면 사람이 승인한다 |

권한: 보기·점검·시험은 **read**, 사전을 고치는 `add-entity`/`add-alias`/`fill-defaults` 와 Web 저장은 **edit** 등급이다
([SECURITY.md](SECURITY.md) §2.3).

## 7. 문제 해결

| 증상 | 원인 | 확인 |
|---|---|---|
| 규칙을 고쳤는데 검색이 그대로 | 그래프는 빌드 때 만들어진다 | `build graph` (청킹을 바꿨으면 `build --full`) |
| 새 `relation_patterns` 를 넣었는데 관계가 안 생긴다 | 정규식이 실제 표기와 다르거나, 끝점 노드가 그 청크에서 안 만들어졌다 | `graph-rules test "<그 문장>"` — 노드와 관계가 바로 보인다 |
| 노드는 생기는데 검색에 안 잡힌다 | 그 유형이 `types_for_cooccur` 에 없어 `mentions`/`co_occurs` 가 안 생긴다 | `graph-rules types` 로 유형 확인 → 필요하면 `types_for_cooccur` 에 추가 |
| 그래프 검색이 모든 문서로 번진다 | 날짜·금액 같은 유형이 허브가 됐다 | `graph profile` 의 소견 → `chunk_values[*].per_chunk` 를 줄이거나 `types_for_cooccur` 에서 제외 |
| 빌드 로그에 "어휘 밖 관계 …" 가 나온다 | LLM 또는 규칙이 `schema.relations` 에 없는 이름을 만들었다 | 그 이름을 어휘에 넣거나, 같은 뜻의 표준 관계 `aliases` 에 넣는다 |
| `link_rules` 를 넣었는데 안 걸린다 | 위에 `*`/`*` 규칙이 있어 가려졌다 | `graph-rules lint` 가 "가려져" 로 알려 준다 — 그 줄을 위로 옮긴다 |
| 별칭을 넣었는데 엉뚱한 노드에 붙는다 | 다른 엔티티도 같은 별칭을 가지고 있다 | `graph-rules lint` 의 "함께 가지고 있습니다" |

## 8. 검증

```bat
python -m unittest tests.test_graph_rules_schema   :: 값 종류 레지스트리 · chunk_values · 스키마 · lint (30건)
python -m llmwiki graph-rules lint                 :: 현재 파일 (오류가 있으면 종료 코드 1)
python tools/verify/verify_surface_align.py        :: CLI · Web · MCP 정렬
python tools/verify/verify_all.py                  :: 전체
```

규칙 파일을 고친 뒤에는 **`build graph` → `graph profile` → `eval --retrieval-only`** 순으로 본다 —
각각 "만들어졌나 · 쓸 만한 모양인가 · 검색이 나아졌나" 에 답한다.

## 9. 구현 파일

| 파일 | 역할 |
|---|---|
| `llmwiki/graph_rules.py` | `DEFAULT_RULES` · `VALUE_TYPES` 레지스트리 · `Schema` · `RuleExtractor` · `known_types` · `lint` · `fill_defaults` |
| `llmwiki/graph_build.py` | 청크 → DB. 규칙/LLM 양쪽 산출물에 같은 스키마 어휘를 적용하고 어휘 밖 이름을 보고 |
| `llmwiki/graph_profile.py` | 빌드된 그래프의 동적 진단 — [GRAPH_PROFILE.md](GRAPH_PROFILE.md) |
| `llmwiki/web/static/js/knowledge.js` | Web 지식 › 그래프 규칙 화면 |
| `tests/test_graph_rules_schema.py` | 회귀 테스트 |
