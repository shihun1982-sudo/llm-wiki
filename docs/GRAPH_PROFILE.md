# GRAPH PROFILE — 지식 그래프 **진단 프로파일** (소견과 처방 · 규모 · 연결성 · 커버리지 · 규칙 기여 · 이력 비교)

> CLI: `python -m llmwiki graph profile [--json] [--eval] [--compare] [--out FILE]`
> API: `GET /api/graph/profile[?compare=1&eval=1]` · `?format=md`(마크다운 보고서 내려받기) · `GET /api/graph/profile/history`
> MCP: `wiki_graph_profile(eval?, compare?)`
> 화면: Knowledge › **그래프 진단** 탭
> 설계 원문: [IMPLEMENTATION_PLAN_0918_2.md §2.5](history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md) · 규칙 파일은 [CORPUS_CONTRACT.md](CORPUS_CONTRACT.md)(front matter) · `data/rules.json`

## 0. 한 장 요약

| 항목 | 내용 |
|---|---|
| 무엇 | 지금 그래프를 보고 **무엇이 잘못됐고 무엇을 어떻게 고쳐야 하는지**를 **소견**(2026-09-24, §2.5)으로 낸다 — 증거(숫자·실제 예) → 원인 → 처방(rules.json 에 붙여 넣을 조각 · 고칠 문서 목록 · 튜닝 키 · 명령) → 확인 방법. 그 아래에 근거가 되는 지표(규모·연결성·커버리지·품질·규칙 기여·질의 활용)와 이력 비교 |
| 절 | **`findings` 소견**(§2.5) · `size` 규모 · `connectivity` 연결성 · `coverage` 문서 커버리지 · `quality` 품질 신호 · `rules` 규칙 기여 · `usage` 질의 활용 · `suggestions` 임계값 제안(예전 형식, 호환) · (옵션) `eval` · `compare` |
| 루프 | `graph profile` → 소견의 처방 적용(`rules.json` 조각 병합 / 문서 수정) → `build graph` → `graph profile --compare` (소견의 `verify.metric` 이 기대대로 움직였는지) |
| 저장 | 실행마다 `<data_dir>/graph_profiles/gp_<YYYYMMDD_HHMMSS>_<µs>.json`, `graph_profile_keep`(30) 개 보관 |
| 권한 | **read** (GET · CLI `graph` · MCP readOnlyHint). 색인·설정은 바꾸지 않지만 이력 파일은 남긴다 |
| 비용 | 큰 그래프에서 몇 초(`/api/graph` 접두 → `HEAVY_GET` 읽기 슬롯). `--eval` 은 질문셋 크기만큼 더 |

## 1. 왜 이렇게 만들었나

그래프 규칙을 고치면 지금까지는 `graph` 요약(노드 수·상위 노드)과 감으로 판단했다. "고립 엔티티가 늘었나, 날짜 노드가 허브가
됐나, 이 id_pattern 이 실제로 무엇을 만들었나, 질의가 그래프를 쓰고는 있나" 를 한 번에 보는 숫자가 없었다.

| 대안 | 왜 안 골랐나 |
|---|---|
| LLM 에게 그래프 품질을 평가시키기 | 비결정적이고 느리다. 규칙을 바꾼 **전후 비교**가 목적이라 같은 입력에 같은 숫자가 나와야 한다 → 전부 결정적 계산 |
| `graph` 내보내기(`graph_export`)에 필드 추가 | 내보내기는 시각화용 상위 N 노드다. 전체 성분/차수/커버리지는 전체 relations·mentions 를 훑어야 한다 → 별도 모듈 |
| 제안을 자유 문장으로 | 고칠 **파일·키**가 없는 제안은 실행할 수 없다 → 모든 제안이 `{kind, severity, detail, action(파일·키), target}` 형태, 임계값은 `THRESHOLDS` 로 결과에 실린다 |

## 2. 지표 읽는 법

계산 대상은 `store.entities()` 전부 · `relations_all()` · `mentions` 테이블 · `communities_all()` · `list_docs()` · `data/rules.json`.

| 절 | 필드 | 읽는 법 |
|---|---|---|
| `size` | `entities` `relations` `mentions` `communities` `docs` `chunks` · `entities_by_type` · `entities_by_source` · `relations_by_rel` · `relations_by_provenance`(explicit/rule/human/llm/cooccur) · `relations_by_source` | provenance 분포가 첫 판단 기준 — `rule`/`explicit` 이 많을수록 결정적 그래프 |
| `connectivity` | `components`(union-find 성분 수) · `largest_component_ratio` · `isolated`/`isolated_ratio`(degree 0) · `isolated_samples`(12) · `isolated_by_type` · `degree{median,p90,max,mean}` · `hubs[]`(상위 `graph_profile_hubs`) · `hub_warnings` | 성분이 많고 최대 성분 비율이 낮으면 섬이 많다. **허브 `warning`** = 유형이 `date/amount/percent/document/role` 이고 차수 ≥ 5 — 이런 노드가 허브면 그래프 검색이 모든 문서로 번진다 |
| `coverage` | `covered`/`uncovered`/`pct` · `by_doc_type{total,covered,pct,uncovered_samples}`(pct 오름차순) · `entities_per_doc` · `mentions_per_chunk` | "커버" = 문서 노드 자신과 약한 유형(`date/amount/percent`)을 **뺀** 엔티티가 하나라도 있는 문서. 유형별 표에서 낮은 유형이 사전 보강 대상 |
| `quality` | `duplicate_candidates`/`duplicates[]`(이름 정규화 동일 · 별칭 겹침) · `dangling_relations`(src/dst 없는 관계) · `self_loops` · `cooccur_share` · `weight{min,p25,median,p75,max,mean}` | 끊긴 관계 > 0 은 부분 빌드 찌꺼기 → 전체 그래프 재생성. cooccur 비중이 높으면 정밀도가 낮다 |
| `rules` | `rows[]{kind,name,detail,entities,relations}` — `id_pattern` · `link_rule` · `relation_pattern` · `analyst_pattern`/`decision_pattern` · `explicit_rel` · `dictionary{total,active,dead,dead_names,top}` · `dead_rules[]` | 엔티티 0 · 관계 0 = **죽은 규칙**(정규식이 코퍼스 표기와 다르거나 대상 문서 유형이 없다). 사전 항목은 멘션 0 이면 죽은 것 |
| `usage` | 최근 `graph_profile_requests` 건의 질의 요청: `seed_share`(그래프 시드 있던 비율) · `requests_with_graph_hit_share` · `graph_hit_share`(최종 근거 중 `graph*` why 비율) · `no_seed_keywords[]`(시드 없던 질의의 키워드 중 엔티티 이름이 아닌 것) · `no_seed_samples` | 시드 비율이 낮으면 질의 어휘가 엔티티 사전에 없다는 뜻 — `no_seed_keywords` 가 곧 엔티티 후보 |
| `eval` (옵션) | `hit@k` `mrr` `term_recall` `n` `channel="graph"` `k=5` | `toggles.fts/vector` 를 끄고 `graph` 만 켜 `pipe.evaluate(k=5)` — `eval --matrix` 의 graph 조합과 같다. 끝나면 토글 복원 |

## 2.5 소견(`findings`) — 무엇이 잘못됐고 무엇을 고칠지 (2026-09-24)

지표만으로는 "그래서 무엇을 고치나" 가 남는다. 소견은 그 답이다. 엔진은 `llmwiki/graph_findings.py`, 세 창구(CLI 텍스트·`--out` md, Web 그래프 진단 탭, MCP `structuredContent`)가 같은 목록을 받는다.
설계 근거와 실데이터 조사는 [GRAPH_KNOWLEDGE_PLAN_0924.md](history/2026-09-24/GRAPH_KNOWLEDGE_PLAN_0924.md).

소견 하나의 키(전부 같다): `id` · `severity`(error/warn/info) · `area`(rules/corpus/tuning/build/query_rules — **어디를 고치는가**) · `title` · `why` ·
`evidence{numbers, samples}` · `fix{kind, section?, snippet?, files?, steps[], commands[]}` · `verify{metric, expect, command}`.
`fix.kind`: `rules_patch`(`snippet` 을 rules.json 의 `section` 절에 병합) · `corpus_edit`(`files` 를 고친다) · `tuning_set` · `build_cmd` · `query_rules_patch`.

| id | 무엇을 보나 | 처방 |
|---|---|---|
| `alias_false_positive` | 허브·상위 사전 엔티티의 실제 mention 문맥에서 **단어 안에서 잡힌 비율**(예: `corpus_d[ir]s` 의 IR). 두 얼굴: **지금 규칙으로도** 단어 안에서 잡히면 area=rules, 지금 규칙은 안 잡는데 그래프에 예전 매처의 멘션이 남아 있으면(저장 멘션 ≥ 지금 매칭 ×2) area=**build**("예전 매처로 빌드됨") | rules: `matching` 절 + `entities.<name>.match` 조각 ([GRAPH_RULES.md](GRAPH_RULES.md) §1.1) / build: `build graph --yes` |
| `related_key_unmapped` | front matter `related.<key>` 중 `related_key_type`·`explicit_rels` 에 없는 키, 그로 생긴 **없는 타입**, 대상 타입 추정 | rules: `related_key_type`·`explicit_rels`·`schema.relations` 조각 |
| `doc_type_without_rules` | link_rules·explicit_rels·id_patterns 어디에도 없는 문서 유형 | rules: 뼈대 조각 |
| `dead_rule_no_docs` | 죽은 규칙 중 "그 문서 유형이 0건" · "front matter 에 그 related 키 없음" | corpus: 규약대로 문서/related 를 만든다 |
| `dead_rule_no_match` | 문서는 있는데 못 잡음 — 정규식 시험 결과와 **코퍼스에 실제로 있는 필드 이름** | rules: 정규식·대상 타입 수정 → `graph-rules test` |
| `junk_titles` | 코드·shebang·기호·3자 미만·중복 제목 (문서 노드 별칭 오염) | corpus: 파일 목록 + `title:` |
| `no_communities` | 토글은 켜져 있는데 무리 0 | build: `build graph` / `incremental_communities` |
| `structure_share_low` | mentions·co_occurs 를 뺀 **구조 관계** 비율 < 15% | corpus: `related.*`·ID 표기·relation_patterns |
| `uncovered_docs` | 커버리지 미달 유형의 문서를 **왜**(ID 없음 / 사전 엔티티 없음 / 본문 짧음)로 | corpus |
| `isolated_by_type` | 고립 30%↑ — 사전 엔티티면 `types_for_cooccur`, 아니면 link_rules | rules |
| `no_seed_queries` | 시드 없는 질의 50%↑ → 키워드 목록 | query_rules: alias 조각 / rules aliases |
| `hub_type_policy_mismatch` | `types_for_cooccur` 에 date/amount/percent/document | rules |
| `id_missing` | ext_id 없음/추론 30%↑ (note 제외) | corpus: `id:` |

임계값은 `graph_findings.THRESHOLDS` 이고 결과의 `findings.thresholds` 에 그대로 실린다. 소견 계산이 하나 실패해도 나머지는 살고 `findings.errors` 에 남는다.
화면(Knowledge › 그래프 진단)에서는 소견이 지표 **위**에 오고, 영역 필터 · 조각 복사 · **규칙 탭에 붙여 넣기**(규칙 탭 상단 상자 + 클립보드; 자동 병합은 하지 않는다) · **보고서(md) 내려받기** 가 있다.

## 3. 제안(`suggestions[]`) — 종류와 가리키는 곳 (예전 형식 — 소견이 같은 내용을 더 자세히 다룬다)

임계값(`THRESHOLDS`, 결과의 `thresholds` 에 그대로): `isolated_ratio` 0.30 · `coverage_pct` 50 · `coverage_min_docs` 3 · `cooccur_share` 0.70 · `no_seed_share` 0.50 · `keyword_min_count` 2 · `hub_warn_min_degree` 5.

| kind | severity | 조건 | action (고칠 파일·키) | target |
|---|---|---|---|---|
| `empty` | error | 엔티티 0 | `build graph`(토글 `rule_graph`) → `data/rules.json entities/id_patterns` | build |
| `dangling` | error | 끊긴 관계 > 0 | `build graph` 전체 재생성 | build |
| `isolated` | warn | 고립 비율 ≥ 30% | `data/rules.json id_patterns` · `link_rules` · `relation_patterns`; 사전 엔티티면 `types_for_cooccur` 에 유형 포함 | rules |
| `hub_date` / `hub_amount` | warn | 허브가 date/amount 유형 | `tuning.json dates_per_chunk` / `amounts_per_chunk` 하향(재빌드 필요) | tuning |
| `hub_percent` / `hub_document` / `hub_role` | warn | 허브가 그 유형 | `data/rules.json types_for_cooccur` 에서 제외 또는 `relation_patterns` 정리 | rules |
| `coverage` | warn | 문서 유형의 문서 ≥ 3 이고 커버 < 50% | `data/rules.json entities` 에 그 유형의 용어, 또는 그 유형 ID 를 잡는 `id_patterns` | rules |
| `cooccur` | info | cooccur 비중 ≥ 70% 이고 관계 ≥ 20 | `link_rules` · `relation_patterns` 추가, front matter `related.*` 채우기 | rules |
| `duplicate` | warn | 합치기 후보 ≥ 1 | `data/rules.json entities.<대표어>.aliases`; 질의 쪽은 `query_rules.json alias` | rules |
| `dead_dictionary` | info | 사전 엔티티 중 멘션 0 | `entities` 에서 지우거나 `aliases` 에 실제 표기 | rules |
| `dead_rule` | info | 엔티티·관계 0 인 규칙(최대 8) | `data/rules.json <절>` (`id_patterns`/`link_rules`/`relation_patterns`/`explicit_rels` …) | rules |
| `no_seed_queries` | warn | 질의 ≥ 5 이고 시드 없는 비율 ≥ 50% | `entities` 에 키워드 추가 → `build graph`; 표기 차이면 `query_rules.json alias/acronym` | rules |
| `graph_channel_weak` | info | 근거 ≥ 20 · graph 근거 < 5% · 시드 비율 > 50% | `tuning.json channel_w_graph` · `config.json graph_hops` · `top_k_graph` | tuning |

화면에서는 `target` 에 따라 "규칙 편집으로"(Knowledge › 그래프 규칙) · "튜닝으로"(Settings › 튜닝) · "빌드로"(Corpus › 빌드) 버튼이 붙는다.

## 4. 이력과 비교

- 세 창구 모두 실행하면 **먼저 저장**한다(`save()` → `gp_…json`, `atomicio`). 파일명은 초 + 마이크로초라 같은 초에 여러 번 저장해도 이름 정렬 = 시간 순.
- `graph_profile_keep` 를 넘으면 이름 정렬로 오래된 것부터 삭제(`0` = 무제한).
- `--compare` / `?compare=1` / `compare=true`: 이력 최신 우선 목록에서 **두 번째**(= 이번 실행 직전) 파일을 읽어 `KEY_METRICS` 21개의 before/after/Δ 를 만든다.
  이전이 없으면 `compare: null` 과 "이번 실행이 첫 기록입니다" 안내.
- `KEY_METRICS`: 엔티티·관계·멘션·커뮤니티·연결 성분·최대 성분 비율·고립 수/비율·허브 최대 차수·허브 경고·커버리지 %·문서당 엔티티·cooccur 비중·중복 후보·끊긴 관계·죽은 규칙·죽은 사전 항목·시드 비율·graph 근거 비율·(eval) hit@k·MRR.
- `GET /api/graph/profile/history` → `{history[](최신 우선, 핵심 지표 + path/file/generated_at/build_version), dir, keep}`.

## 5. 설정 (`config.json`)

| 키 | 기본 | 뜻 | 확인 |
|---|---|---|---|
| `graph_profile_keep` | `30` | `data/graph_profiles/gp_<ts>.json` 보관 개수. `--compare` 가 직전 파일과 비교. `0` = 무제한 | `GET /api/graph/profile/history` 의 `keep`, 화면 이력 머리말 |
| `graph_profile_requests` | `200` | '질의 활용' 절이 보는 최근 질의 요청 수(`requests` 테이블 `kind='query'`) | 결과 `usage.sample_limit` |
| `graph_profile_hubs` | `10` | 허브 표 행 수(차수 상위). 최소 1 | 결과 `connectivity.hubs` 길이 |

세 키 모두 `config.json` 과 `setup/config.example.json` 에 기본값으로 적혀 있다. `setup/config.example.headless.json` · `setup/config.example.pat-gateway.json` 에는 없다(→ §9).
임계값(`THRESHOLDS`)·허브 경고 유형(`HUB_WARN_TYPES`)·약한 유형(`WEAK_TYPES`)은 코드 상수다 — 결과의 `thresholds` 로 값이 보이지만 파일로 바꿀 수는 없다.

## 6. 세 창구

### CLI
```bat
python -m llmwiki graph profile                        :: 텍스트 리포트 + 저장 경로
python -m llmwiki graph profile --compare              :: 직전 저장본과 핵심 지표 Δ 표
python -m llmwiki graph profile --eval                 :: graph 채널만 켠 hit@5 / MRR 포함
python -m llmwiki graph profile --out C:\tmp\gp.md     :: 마크다운 리포트 저장 (+ 텍스트 출력)
python -m llmwiki graph profile --json                 :: 전체 dict (saved · compare · thresholds 포함)
python -m llmwiki graph                                :: 예전 그대로 내보내기 (action 기본 export)
```
종료 코드 0. `graph` 는 `_READ_CLI` 라 어느 역할이든 실행할 수 있다.

### Web
- `GET /api/graph/profile?eval=0|1&compare=0|1` → 프로파일 dict + `saved`(파일 경로) (+ `compare`).
- `GET /api/graph/profile/history` → 이력.
- Knowledge › **그래프 진단** 탭: `진단 실행` · `이전 실행과 비교` · `평가 포함 (graph 채널만 hit@k)` 체크 · `이력`. 지표 카드(고립 비율이 임계값 이상이면 경고색, 끊긴 관계 > 0 은 빨강) → 제안 목록(이동 버튼) → 허브(엔티티 클릭 = 그래프 탭 상세) · 고립 예시 · 규칙 기여(0/0 은 빨강) · 문서 커버리지(유형별, 50% 미만 경고색) · 합치기 후보 · 질의 활용 · 이력 표.
  탭을 처음 열면 이력만 읽고, 진단은 버튼을 눌러야 돈다(비용 때문).

### MCP
`wiki_graph_profile(eval=false, compare=false)` — `content` 는 텍스트 리포트, `structuredContent` 는 전체 dict. `readOnlyHint: true` 지만 이력 파일은 남긴다(설명에 명시).

## 7. 검증

```bat
python -m unittest tests.test_graph_profile -v
```
| 테스트 | 확인하는 것 |
|---|---|
| `test_sections_and_consistency` | 절 존재 · `size` 가 `store.stats()` 와 일치 · 유형/rel 합계 = 총계 · 허브 ≤ `graph_profile_hubs` · 커버+미커버 = 문서 수 · 끊긴 관계 0 · `id_pattern issue` 살아 있음 · 제안 필드 · 렌더/JSON 직렬화 |
| `test_save_history_compare_and_prune` | 5회 저장 → `graph_profile_keep=3` 개만 남음 · 이력 최신 우선 · 같은 그래프 Δ=0 · 이전 없을 때 before=None |
| `test_include_eval_restores_toggles` | 평가 중 (fts, vector, graph) = (False, False, True) · 뒤에 복원 · `eval.channel="graph"` |
| `test_suggestions_rule_based` | 임계값을 넘긴 프로파일 → `isolated`(rules.json) · `hub_date`(`dates_per_chunk`, tuning) · `dangling`(build) · `coverage` |
| `test_cli_and_mcp_surfaces` | `graph profile --json/--compare/--out` · 기존 `graph` 유지 · `wiki_graph_profile` structuredContent · readOnlyHint |

수동: `python -m llmwiki graph profile --json | python -c "import json,sys; d=json.load(sys.stdin); print(d['size']['entities'], d['connectivity']['isolated_ratio'], len(d['suggestions']), d['saved'])"` · `python tools\verify\verify_surface_align.py`(그래프 진단 행).

## 8. 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| `[error] empty 그래프에 엔티티가 없습니다` | 그래프 빌드 안 됨/규칙이 아무것도 못 잡음 → `build graph`, `toggles.rule_graph`, `data/rules.json entities/id_patterns` |
| `dangling` 끊긴 관계 | 부분 빌드·삭제 뒤 찌꺼기 → `python -m llmwiki build graph` |
| 허브가 날짜(`hub_date`) | `tuning.json dates_per_chunk` 를 낮추고 `build graph`(재빌드 필요 값) |
| `(비교할 이전 프로파일이 없습니다)` | 첫 실행. 규칙 고치고 빌드 뒤 다시 `--compare` |
| `--compare` 가 늘 Δ=0 | 규칙을 바꿨지만 `build graph` 를 안 했다. 프로파일은 색인을 읽을 뿐 다시 만들지 않는다 |
| `usage.requests` 가 0 | `requests` 테이블에 질의 요청이 없다(`keep_requests` 정리 포함). 질의를 몇 번 실행한 뒤 다시 |
| `eval.error` | `eval/questions.json` 없음 또는 평가 실패 — `python -m llmwiki eval` 로 먼저 확인 |
| 화면이 오래 "진단 중…" | 큰 그래프 + `--eval`. 읽기 슬롯(`HEAVY_GET`)을 기다리는 중일 수 있다 |

## 9. 파일에 없는 키

| 파일 | 키 | 기본값 |
|---|---|---|
| `setup/config.example.headless.json` | `graph_profile_keep` · `graph_profile_requests` · `graph_profile_hubs` | 30 · 200 · 10 |
| `setup/config.example.pat-gateway.json` | 같은 세 키 | 같음 |

`python -m llmwiki config fill-defaults --examples` 가 채운다([SETTINGS_SYNC.md](SETTINGS_SYNC.md)).

## 10. 계획과 다른 점

- 계획 §2.5 의 `--out md` 는 `--out FILE`(경로 지정) 로 구현됐고, 마크다운 저장 뒤 텍스트도 stdout 에 나온다.
- 제안에 계획에 없던 `graph_channel_weak`(시드는 있는데 graph 근거가 5% 미만)과 `empty`/`dangling` 이 추가됐다.
- 허브 경고 유형에 계획의 "날짜/역할" 외에 `amount/percent/document` 가 포함된다.

## 11. 구현 파일

- `llmwiki/graph_findings.py` — 소견(§2.5). 테스트 `tests/test_graph_findings_0924.py`.

| 파일 | 내용 |
|---|---|
| `llmwiki/graph_profile.py` | `profile()` 6절 계산 · `suggest()` · `save`/`prune`/`load`/`history` · `KEY_METRICS`/`key_metrics`/`compare` · `render_text`/`render_markdown` · 상수 `THRESHOLDS`/`HUB_WARN_TYPES`/`WEAK_TYPES` |
| `llmwiki/config.py` | `graph_profile_keep/requests/hubs` + `SETTING_HELP` |
| `llmwiki/cli.py` | `graph profile [--json] [--eval] [--compare] [--out]` |
| `llmwiki/web/server.py` | `GET /api/graph/profile` · `GET /api/graph/profile/history` |
| `llmwiki/web/static/js/knowledge.js` · `index.html#tab-graphprof` | 그래프 진단 탭 |
| `llmwiki/mcp.py` | `wiki_graph_profile` |
| `tests/test_graph_profile.py` | 5건 |
