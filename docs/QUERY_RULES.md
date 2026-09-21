# QUERY RULES — 규칙 기반 질의 확장 사전 (`query_rules.json`) 과 **방향**

> CLI: `python -m llmwiki rules types|show|add|remove|test|explain|stats|path|lint|merge|effect`
> API: `GET /api/query_rules`(규칙 + **유형 표**) · `GET /api/query_rules/test?q=` · `GET /api/query_rules/explain?term=` · `GET /api/query_rules/lint` · `GET /api/query_rules/effect` · `POST /api/query_rules {action: add|remove|save}`
> MCP: `wiki_rules(action=types|explain|test, term|q)` (읽기 전용)
> 화면: Settings › **질의 규칙 사전** 탭 — 유형 표 · "이 말은 어떻게 퍼지나" · 규칙 효과
> 설계 원문: [IMPLEMENTATION_PLAN_0918_2.md §2.4](history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md) · 튜닝 키 표는 [TUNING.md](TUNING.md) · 원본 예시 `setup/query_rules.example.json`

## 0. 한 장 요약

| 항목 | 내용 |
|---|---|
| 무엇 | LLM 없이 **결정적으로** 질의를 넓히거나 고치거나 좁히는 사전. 토큰 0, 수 ms, 그리고 **왜 그렇게 넓어졌는지 설명된다** |
| 파일 | `query_rules.json`(경로 `LLMWIKI_QUERY_RULES_PATH`). 원본 예시 `setup/query_rules.example.json` (절마다 `_how_*` 설명) |
| 유형 | **9개** — `acronym` `synonym` `alias` `related` `exclude` `compound` + **`context` `hypernym` `unit`**(2026-09-19). 표는 `rules types` |
| 유형 추가 | `RULE_TYPES` **레지스트리**에 `register_type()` 한 번 — 색인·확장·린트·설명·통계·CLI·Web·MCP 가 따라온다 (§1.1) |
| 방향 | **acronym · synonym · hypernym 양방향**, **alias · related · exclude 일방**, `context` 는 조건부, `unit` 은 패턴 |
| 접기 | 규칙은 `query_rules_max_rounds`(2) 번 접어 적용(A→B 뒤 B→C 도 걸린다). `related`·`hypernym`·`exclude` 는 연쇄하지 않는다 |
| 보기 | `rules explain <용어>` — 그 말이 어느 유형·어느 방향으로 무엇을 끌어오고, 무엇이 그 말을 끌어오는지 |
| 권한 | types/show/test/explain/stats/lint/path/effect = read · add/remove/merge/save = edit |

## 1. 유형과 방향 — 왜 다른가

색인(`query_rules._build_index`)이 방향을 정한다. 양방향 = 키가 질의에 있으면 값으로, 값이 있으면 키(와 다른 값)로 넓힌다. 일방 = 키 → 값만.

| 유형 | 쓰는 형태 | 방향 | 적용 방식(`HOW`) | 왜 이 방향인가 |
|---|---|---|---|---|
| `acronym` | `"AGC": ["Automatic Gain Control", "자동 이득 제어"]` | **양방향** | FTS 구문(phrase) OR(`acronym_phrase`) · 벡터 치환 질의 · 그래프 시드, 동일 가중 | 완전 동치 — 어느 쪽이 질의에 있어도 같은 것을 찾는다 |
| `synonym` | `"재시작": ["리셋", "restart", "reset"]` | **양방향** | FTS OR(`syn_w`) · 벡터 치환 질의 | 준동치 |
| `alias` | `"모뎀B": "MDM9x-B1"` | **일방** 키 → canonical | canonical 로 **치환**(+원 표기 OR) · 그래프 시드는 canonical | 표기 **정규화**가 목적. 양방향이면 "정규 표기" 가 없어진다 — 일부러 일방 |
| `related` | `"DMA underrun": ["FIFO overflow", "버퍼 언더런"]` | **일방** (기본) | 주 질의에 섞지 않고 **보조 리스트**(`related_w`)로 따로 융합 | 정밀도 보호 — 연관어가 서로를 끌어오면 주제가 번진다. 필요하면 `related_symmetric=true` |
| `exclude` | `"시뮬레이터": ["simulator", "simulation"]` | **일방** | FTS NOT + 후보 페널티(`exclude_penalty`) | 키가 있을 때 값을 빼는 것이라 반대 방향이 의미가 없다 |
| `compound` | `"재전송타이머": ["재전송", "타이머"]` | 분리 | 색인·질의 토크나이저에서 부분으로 분리(`textutil.set_compounds`) | 확장이 아니라 토큰화 규칙 |
| **`context`** | `"PA": [{"when": ["rf","전력"], "then": ["Power Amplifier"]}, {"when": ["일정","조직"], "then": ["Product Area"]}]` | **문맥** | `when` 중 하나가 질의에 있을 때만 `then` 으로 OR(`context_w` 0.9) | 같은 약어가 팀마다 다른 뜻인 경우가 **사내 위키에서 정밀도를 가장 많이 깎는다**. 조건이 안 맞으면 **아예 발화하지 않는다** — 억지로 넓히느니 가만히 있는 편이 낫다 |
| **`hypernym`** | `"메모리 오류": ["DMA 오버런", "FIFO 언더런"]` | **상하** | 보조 리스트. 내려갈 때 `hypernym_down_w`(0.35), 올라갈 때 `hypernym_up_w`(0.2) | 분류 체계를 동의어와 같은 가중으로 섞으면 곧 잡음이 된다. **상위어 문서는 대개 일반론**이라 구체적 질문의 답이 아니므로 올라갈 때를 더 약하게 |
| **`unit`** | `"KB": {"factor": 1024, "base": "byte", "aliases": ["킬로바이트"]}` | **수치** | 질의의 `숫자+단위` 를 찾아 환산값·다른 표기를 OR (`4KB` ⇄ `4 킬로바이트` ⇄ `4096` ⇄ `4096 byte`) | HW·모뎀 문서는 같은 값을 제각각 적는다. 키가 **숫자와 함께 와야 뜻이 생겨** 사전 용어로는 표현할 수 없다 → 이 유형만 질의를 직접 훑는다(`match="scan"`) |

**양방향이 필요하면** 동치인지 묻는다: 동치면 `acronym`/`synonym` 에 넣는다(정답). 연관어를 양쪽으로 쓰고 싶을 때만 `related_symmetric` 을 켠다. `alias` 는 바꾸지 않는다.

**무엇을 어느 유형에 넣나** (`rules types` 가 같은 표를 출력한다):

    완전히 같은 말      → acronym          비슷한 말        → synonym
    표기만 다른 말      → alias            곁가지 주제      → related
    끌어오면 안 되는 말  → exclude          붙여 쓰는 말     → compound
    문맥마다 뜻이 갈림  → context          분류 체계        → hypernym
    숫자 + 단위        → unit

### 1.1 유형 레지스트리 — 유형 하나를 늘리는 법

2026-09-19 이전에는 유형 이름이 **파일 15개**에 흩어져 있었다(`TYPES`·`DIRECTION`·`HOW`·`_build_index`·
`expand`·`lint`·`explain`·`add_rule`·CLI·Web·MCP·검증기…). 한 자리만 빠뜨리면 그 유형은 **조용히 무시된다**.
지금은 유형이 `llmwiki/query_rules.py` 의 `RULE_TYPES` 레지스트리 항목 하나다.

```python
register_type(RuleType(
    name="antonym", label="반의어", direction="일방",
    how="후보 페널티 (exclude 와 같은 길)",
    value="list",          # list | str | cond | map
    apply=lambda acc, txt, canon, vals, rnd, in_q: (acc.exclude.extend(vals), [])[1],
))
```

이 한 번으로 따라오는 것: 색인(`_build_index`) · 확장(`expand`) · 통계(`stats`) · 린트(`lint`) ·
설명(`explain`) · 빈 절 생성(`fill_defaults`) · 병합(`merge_rules`) · 추가(`add_rule`) ·
CLI `rules types` · Web 드롭다운과 유형 표 · MCP `wiki_rules(action=types)`.

`apply` 는 **누적기 `Acc`** 에 기여한다. 어느 채널에 넣느냐가 곧 설계다.

| 채널 | 뜻 | 위험 |
|---|---|---|
| `acc.or_groups` | FTS 주 질의에 `( A OR B )` 로 들어간다 | recall↑ **precision 위험** — 정말 같은 말에만 |
| `acc.alt` | 벡터/FTS 의 **별도 질의** `(text, weight, kind, canonical)` | 주 질의를 오염시키지 않는다 |
| `acc.related` | 보조 리스트 `(text, weight, canonical)` — 융합에만 참여 | 가장 안전. 넓히고 싶지만 확신이 없을 때 |
| `acc.exclude` | FTS NOT + 후보 페널티 | 너무 넓게 잡으면 답이 사라진다 |
| `acc.seeds` | 그래프 시드 엔티티 이름 | — |

`apply` 가 `None` 을 돌려주면 **발화하지 않은 것**으로 친다(`context` 의 조건 불일치).
사전 키로 표현할 수 없는 유형은 `match="scan"` 으로 두고 `scan(acc, section)` 에서 질의를 직접 훑는다(`unit`).

## 2. 확장이 실제로 어떻게 들어가나 (`expand`)

`rules test "질의"` / `GET /api/query_rules/test?q=` / `wiki_rules(action=test)` 의 결과:

| 필드 | 뜻 |
|---|---|
| `query_alias` | alias 치환이 끝난 질의 |
| `fts_query` | FTS 용 확장 질의 — 원 질의 + acronym/synonym OR 묶음, exclude 는 `NOT (...)`. 발화한 규칙이 없으면 빈 문자열 |
| `alt_queries` | `[(text, weight, kind)]` 벡터/FTS 추가 리스트(치환 질의). 가중치 순 상위 4 |
| `related` | `[(text, weight)]` 보조 리스트(별도 융합). 상위 4 |
| `exclude` | NOT/페널티 대상 용어(소문자) |
| `seeds` | 그래프 시드에 더할 canonical 이름 |
| `fired` | 발화한 규칙 `{type, canonical, round …}` — `round` 는 몇 번째 접기에서 나왔는지 |

접기: `query_rules_max_rounds`(기본 2) 번까지 새로 들여온 말에 규칙을 다시 적용한다. 1이면 `TAT → Turn Around Time`(acronym) 뒤의
`Turn Around Time → 응답시간`(synonym) 이 조용히 무시된다(2026-09-16 실제 사례). related 와 alias 는 다시 펼치지 않는다.
같은 (유형, 대표어)는 한 번만 적용된다 — 양방향 사전은 같은 OR 묶음을 두 번 만들기 때문.
용어 매칭: 영문/숫자 용어는 단어 경계(`ISSUE-2001` 안의 조각은 제외), 한글은 포함 매칭(조사 결합). 겹치면 **더 긴** 매치가 이기고, 같은 구간이면 유형이 달라도 함께 발화한다.

## 3. 설정 (`tuning.json`, stage `query_rules`)

| 키 | 기본 | 뜻 | 확인 |
|---|---|---|---|
| `related_symmetric` | `false` | related 를 값 → 키 방향으로도 색인. 켜면 recall↑ · precision↓. 파일이 그대로여도 색인을 다시 만든다(캐시 키에 플래그 포함) | `rules explain FIFO overflow` 의 `related_symmetric` · `tuning show --stage query_rules` |
| `query_rules_max_rounds` | `2` (1~5) | 규칙 접기 횟수. 3 = 약어→정식명→동의어 사슬이 깊은 사전 | `rules lint` 의 `max_rounds` · `deep_chain` 경고 |
| `syn_w` | `0.8` (0~2) | synonym OR 가중(원 질의 1.0 대비) | `rules test` 의 `alt_queries` weight |
| `related_w` | `0.4` (0~2) | related 보조 리스트 가중 | `rules test` 의 `related` weight |
| `exclude_penalty` | `0.5` (0~1) | exclude 용어 포함 후보의 fused 점수 배율(0 = 제거) | trace `boost`/근거 표 |
| `acronym_phrase` | `true` | acronym 확장어를 구문(phrase)으로 넣을지(false = 토큰 OR) | `rules test` 의 `fts_query` 에 `"…"` 구문 |

바꾸는 법: `python -m llmwiki tuning set related_symmetric=true` · Web Settings › 튜닝(query_rules 단계) · 요청 단위 `overrides.tuning`(파일에 남지 않음).
`query_rules.json` 자체는 `LLMWIKI_QUERY_RULES_PATH` 로 위치를 바꿀 수 있고, 조직 사전 예시는 `setup/query_rules.example.modem.json`(`rules merge <파일>` 로 합침).

## 4. `rules explain <용어>` 읽는 법

| 필드 | 뜻 |
|---|---|
| `entries[]` | 색인에서 **이 말로 발화하는** 규칙 `{type, direction, canonical, values, how, reverse}`. `reverse=true` = 값 쪽에서 거꾸로 걸린 항목(acronym/synonym 의 양방향, `related_symmetric` 의 related) |
| `expands_to[]` | 이 말이 질의에 있을 때 **들여오는 말** — `as`: `OR`(acronym/synonym) · `치환`(alias) · `보조 리스트`(related) · `NOT`(exclude) |
| `expanded_from[]` | 다른 말이 질의에 있을 때 **이 말을 들여오는 규칙**(이 말이 값으로 적힌 곳) `{type, direction, key, values, reverse_applies, note}`. alias/related(일방)는 **여기에만** 나오고 `entries` 에는 없다 → "왜 반대로는 안 넓혀지나" 의 답 |
| `found` | entries 나 expanded_from 이 하나라도 있으면 true |
| `related_symmetric` · `directions` · `how` · `note` · `path` | 지금 플래그 값 · 유형별 방향/적용 표 · 요약 문장 · 사전 파일 경로 |

예(기본 사전): `rules explain MDM9x-B1` → `entries` 비고 `expanded_from` 에 `alias 모뎀B → 반대는 하지 않는다 — 표기 정규화가 목적`.
`rules explain FIFO overflow` → `related_symmetric=false` 면 `expanded_from` 만(`reverse_applies=false`), `true` 면 `entries` 에 `related 양방향 (related_symmetric)` 가 생긴다.

## 4.5 규칙 효과 — 어떤 규칙이 **실제로 답에 기여했나** (2026-09-19)

규칙은 쌓이기만 하고 줄지 않는다. 한 번 넣은 동의어가 쓸모 있는지 아무도 모르니 지울 근거가 없고,
잘못 넣은 규칙은 엉뚱한 문단을 계속 끌어와 정밀도를 갉아먹는다. 그래서 질의마다 규칙별로 네 가지를 센다.

| 세는 것 | 뜻 |
|---|---|
| `fired` | 그 규칙이 질의에 걸렸다 |
| `cand` | 그 규칙이 만든 대체 질의·관련어 검색이 **후보를 하나라도** 가져왔다 |
| `helped` | 그 후보 중 하나가 **최종 컨텍스트에 들어갔다** (= 답변의 근거가 됐다) |
| `cited` | 그 근거를 답변이 **`[C#]` 로 인용**했다 |

**정확도**: 대체 질의 리스트마다 출처 규칙을 달아 둔다(`expand()` 가 돌려주는 `alt_queries` 의 4번째 원소,
`related` 의 3번째 원소). 그래서 여러 규칙이 동시에 걸려도 "누구 덕인지" 를 뭉개지 않는다.
한 청크를 두 규칙이 모두 찾았다면 둘 다 기여로 센다 — 실제로 둘 다 찾았기 때문이다.

읽는 법. `fired` 가 크고 `helped` 가 0 이면 그 규칙은 **노이즈만 만들고 있다** — 지울 후보다.
`cand` 는 큰데 `helped` 가 작으면 후보는 가져오지만 리랭크에서 계속 떨어지는 것이니, 값이 너무 넓지 않은지 본다.

| 창구 | 방법 |
|---|---|
| CLI | `python -m llmwiki rules effect [--order fired\|helped\|rate\|useless] [--json]` · 초기화 `rules effect --reset [용어]` |
| Web | Settings › 질의 규칙 사전 의 **규칙 효과** 버튼 (정렬 선택). 3회 이상 걸렸는데 기여 0 인 줄은 빨갛게 표시 |
| API | `GET /api/query_rules/effect?order=&limit=` |

저장은 `kv` 테이블의 `rule_effect` 한 줄(JSON)이라 스키마 변경이 없고, 질의당 쓰기 1회다.
기록이 실패해도 질의는 그대로 성공한다 (관측용이라 절대 질의를 실패시키지 않는다). 구현: `llmwiki/ruleeffect.py`.

### 4.6 2026-09-19 에 한 것 — 유형 세 개 추가와 레지스트리

위 §4.5 의 "더 넓힐 수 있는 방향" 중 세 가지를 구현했다. 그러면서 **유형을 늘리는 일 자체가 어려웠던 것**을
먼저 고쳤다(§1.1) — 유형 이름이 파일 15개에 흩어져 있어서, 하나를 늘리려면 아홉 자리를 손봐야 했고
한 자리만 빠뜨리면 그 유형이 조용히 무시됐다.

| 추가 | 무엇을 푸는가 | 왜 이 모양인가 |
|---|---|---|
| **`context`** (문맥 조건부) | 같은 약어가 팀마다 다른 뜻 (RF 의 `PA` ↔ 조직의 `PA`) | 조건이 안 맞으면 **아예 발화하지 않는다**. 같은 말을 `acronym` 에도 넣으면 그쪽이 항상 넓혀서 무의미해지므로 `rules lint` 가 `context_shadowed` 로 경고한다 |
| **`hypernym`** (분류 체계) | "메모리 오류" 로 물었을 때 "DMA 오버런" 문서를 찾게 | **방향에 따라 가중이 다르다** — 상위어 문서는 대개 일반론이라 올라갈 때를 더 약하게(0.2 vs 0.35). 보조 리스트로만 들어가 주 질의를 오염시키지 않는다 |
| **`unit`** (수치 동치) | `4KB` 로 물었을 때 `4096바이트` 로 적힌 문서를 찾게 | 키가 숫자와 함께 와야 뜻이 생겨 사전 용어로 못 적는다 → `match="scan"` 으로 질의를 직접 훑는 첫 유형. 덕분에 레지스트리가 **패턴형 유형**도 받을 수 있게 됐다 |

### 아직 하지 않은 것

| 아이디어 | 무엇이 좋아지나 | 왜 아직 안 했나 |
|---|---|---|
| **근접(NEAR) 구문 매칭** | 여러 낱말로 된 정규 명칭을 OR 토큰으로 흩지 않고 구문으로 본다 | FTS5 NEAR 도입 + 기존 `acronym_phrase` 와의 관계 정리가 필요 |
| **띄어쓰기·표기 변형 자동 생성** | "전력제어" ↔ "전력 제어" 를 사람이 일일이 넣지 않아도 된다 | 지금은 토큰 컬럼이 일부 흡수한다 — 효과 통계로 남은 누락을 먼저 재는 편이 낫다 |
| **문서 유형별 규칙** (`only_doc_types`) | 스펙 문서에서만 쓰는 약어를 이슈 검색에 섞지 않는다 | `context` 의 `when` 이 질의 문자열만 본다. 문서 유형은 질의가 아니라 **후보 쪽** 조건이라 융합 단계에 손을 대야 한다 |
| **오타·표기 흔들림 자동 교정** | "인터럽투" → "인터럽트" | 편집 거리 기반은 오검출이 잦다. 알려진 오타는 지금도 `alias` 로 적을 수 있다 |
| **질의 로그에서 규칙 후보 발굴** | 근거를 못 찾은 질의의 말과, 나중에 답이 된 문서의 말을 대조해 동의어 후보를 제안 | 자가진화(`evolve review`)가 비슷한 일을 한다 — 규칙 효과 데이터가 쌓인 뒤에 합치는 편이 낫다 |

## 5. 세 창구

### CLI
```bat
python -m llmwiki rules types                        :: **유형 표** — 무엇을 어느 유형에 넣나 (방향·값 모양·항목 수·적용 방식)
python -m llmwiki rules show                         :: 사전 전체
python -m llmwiki rules stats                        :: 유형별 개수
python -m llmwiki rules effect --order useless       :: 걸리기만 하고 한 번도 기여 못 한 규칙 (지울 후보)
python -m llmwiki rules explain AGC                  :: 유형 · 방향 · 대표어 · 값 · 적용 방식 표 + "이 말을 끌어오는 규칙"
python -m llmwiki rules test "AGC 수렴이 느린 이유"    :: expand 결과 JSON (fts_query · alt_queries · related · exclude · seeds · fired)
python -m llmwiki rules lint                         :: duplicate · cross_type · self_ref · alias_chain · deep_chain · empty
python -m llmwiki rules add synonym 지연 latency 딜레이  :: 편집 (edit 등급)
python -m llmwiki rules remove synonym 지연 딜레이
python -m llmwiki rules merge setup\query_rules.example.modem.json [--replace]
python -m llmwiki rules path
```
`--json` 은 모든 action 에 통한다. `explain` 에 용어가 없으면 usage 출력 + 종료 1.

### Web
- Settings › **질의 규칙 사전**: **유형 표**(접었다 펼 수 있는 "규칙 유형 표" — 드롭다운과 표가 모두 서버의 레지스트리에서 채워지므로 유형을 늘려도 화면이 따라온다) · 유형/용어/값 추가 폼(선택한 유형의 **값 모양에 따라 입력칸 안내가 바뀌고**, `cond`·`map` 유형은 아래 JSON 편집기로 안내한다) · 사전 JSON 편집·저장(`action=save`) · "테스트" 입력(`/api/query_rules/test`) · **"이 말은 어떻게 퍼지나 (용어 하나)"** 입력 + `설명` 버튼(Enter 도 됨) → 유형·방향·대표어·값·적용 방식 표, "이 말을 끌어오는 규칙 (값으로 적힌 곳)" 표, `related_symmetric` 값, 요약 문장.
- `GET /api/query_rules/explain?term=<용어>` → §4 의 dict. `GET /api/query_rules/test?q=` · `GET /api/query_rules/lint` · `GET /api/query_rules`. 모두 read.
- `POST /api/query_rules {"action":"add"|"remove"|"save", …}` → edit 등급(`security.json permissions.levels.edit`, 기본 class2).

### MCP
`wiki_rules(action="types")` → 유형 표(붙은 LLM 이 "이 말을 어느 유형에 넣자" 를 제안할 때의 근거. 제안 자체는 `wiki_propose`).
`wiki_rules(action="explain", term=…)` → `content` 는 CLI 와 같은 표(`_rules_explain_text`), `structuredContent` 는 dict.
`wiki_rules(action="test", q=…)` → `expand` 결과. `readOnlyHint: true`, 사전을 바꾸지 않는다. `term`/`q` 가 없거나 action 이 다르면 오류.

## 6. 검증

```bat
python -m unittest tests.test_query_rules_explain -v
```
| 테스트 | 확인하는 것 |
|---|---|
| `test_directions_per_type` | acronym 키/값 모두 양방향(`reverse`) · synonym 값 쪽 발화 · alias 키→canonical 일방, canonical 쪽은 `entries` 빔 + `expanded_from` 에 "반대" 안내 · related 기본 일방(`reverse_applies=false`) · exclude `NOT` · compound · 없는 말 `found=false` |
| `test_related_symmetric_toggle` | 기본: `FIFO overflow` 질의에 `related` 없음 → `related_symmetric=true` 로 바꾸면 파일 그대로여도 `DMA underrun` 이 보조 리스트에 → 끄면 되돌아감 · 키 방향은 그대로 |
| `test_cli_and_mcp` | `_rules_explain_text` 표 · `wiki_rules explain/test` · 인자 누락/잘못된 action 오류 · readOnlyHint |

수동: `python -m llmwiki rules explain AGC --json | python -c "import json,sys; d=json.load(sys.stdin); print(d['found'], [(e['type'], e['direction']) for e in d['entries']])"` · `python tools\verify\verify_surface_align.py`(규칙 사전 설명 행).

## 7. 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| 정식 명칭으로 물었는데 약어 문서가 안 잡힌다 | 그 쌍이 `alias` 나 `related` 에 있다(일방). 동치면 `acronym` 으로 옮긴다 — `rules explain <정식명>` 의 `expanded_from` 이 알려 준다 |
| `related` 값 쪽에서 키가 안 딸려 온다 | 의도된 기본(정밀도 보호). 필요하면 `tuning set related_symmetric=true` |
| 사슬 규칙의 끝이 안 펼쳐진다 | `rules lint` 의 `deep_chain` → `query_rules_max_rounds` 를 3 으로 |
| `alias_chain` 경고 | A→B, B→C 에서 치환은 한 번만 → A→C 로 직접 적는다 |
| 파일을 고쳤는데 결과가 그대로 | 파일 mtime 으로 캐시가 갈리므로 보통 즉시 반영. 서버가 다른 경로를 보는지 `rules path`/`LLMWIKI_QUERY_RULES_PATH` 확인 |
| 저장 뒤 모든 질의가 깨진다 | 사전(JSON object)이 아닌 값을 저장하려 하면 `save_rules` 가 거절한다. 파일이 손상되면 기본 규칙으로 동작하고 error 로그를 남긴다 |

## 8. 파일에 없는 키

| 파일 | 키 | 기본값 | 비고 |
|---|---|---|---|
| `tuning.json` | `related_symmetric` | `false` | 파일이 "기본값과 같은 값은 저장하지 않는" sparse 모드. `config fill-defaults --tuning` 이 모든 튜닝 키를 `_explicit_defaults` 표식과 함께 채운다 |
| `tuning.json` | `query_rules_max_rounds` · `syn_w` · `related_w` · `exclude_penalty` · `acronym_phrase` | `2` · `0.8` · `0.4` · `0.5` · `true` | 같음 |
| `setup/tuning.example.json` | (파일 자체가 없음) | — | `config fill-defaults --tuning --examples` 가 만든다 |

`query_rules.json`(6절 모두 있음: acronym 68 · synonym 35 · alias 10 · related 11 · exclude 3 · compound 14)과 `setup/query_rules.example.modem.json`(6절 + 절별 `_comment_*`)은 누락이 없다.

## 9. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/query_rules.py` | `TYPES` · `DIRECTION`/`HOW` 표 · `_build_index(rules, related_symmetric)` · `_related_symmetric()` · `load_rules`(캐시 키에 플래그) · `expand` · `explain` · `lint` · `stats` · `fill_defaults` |
| `llmwiki/tuning.py` | `related_symmetric` · `query_rules_max_rounds` · `syn_w` · `related_w` · `exclude_penalty` · `acronym_phrase` 레지스트리 |
| `llmwiki/cli.py` | `rules …` · `_rules_explain_text` |
| `llmwiki/web/server.py` | `GET /api/query_rules[/test|/lint|/explain]` · `POST /api/query_rules` |
| `llmwiki/web/static/js/settings.js` · `index.html#tab-qrules` | 질의 규칙 사전 탭 · `explainTerm()` |
| `llmwiki/mcp.py` | `wiki_rules` |
| `tests/test_query_rules_explain.py` | 3건 |
