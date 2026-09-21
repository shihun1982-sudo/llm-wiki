# FUSION_TOPK — 채널별 top-k 구간 가중과 리랭크 창 보장 주입 (`channel_inject`)

> 설정: `tuning.json` (단계 `rrf_fuse`) — `{fts,vector,graph,doc_vector,external}_topk_n / _topk_w / _tail_w` · `channel_inject`
> 코드: `llmwiki/fusion.py` (`topk_map`, `fuse(topk=)`, `parse_inject_map`, `inject_channels`) · `llmwiki/query_engine.py` `_retrieve()`
> 설계 근거: [IMPLEMENTATION_PLAN_0918_2.md §2.2](history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md) · 검증: `tests/test_fusion_topk.py`

## 0. 한 장 요약

```
RRF:  fused(c) = Σ_list  w_list × factor(rank) / (rrf_k + rank)          factor = topk_w  (rank ≤ topk_n)
                                                                            = tail_w  (rank >  topk_n;  0 이면 그 리스트에서 제외)
                                                                            = 1.0     (topk_n = 0 → 구간 가중 끔, 예전과 동일)
→ 부스트 → channel_inject "fts:2,vector:2": 채널 주 리스트 상위 n개를 리랭크 후보 창(win) 안으로 올린다 (why=inject:<채널>) → 리랭크
```

| 항목 | 값 |
|---|---|
| 채널 | `fts` `vector` `graph` `doc_vector` `external` (`BASE_CHANNELS`). 보조 리스트는 이름으로 기본 채널을 물려받는다: `fts_rule`·`fts_alt1`·`fts_rel1` → fts, `vector_alt2` → vector, `ext_<src>` → external |
| 흔적 | 배율이 1.0 이 아니면 `Hit.boosts["topk_<채널>"]`(채널당 한 번, 첫 리스트 값) → 근거 표·Web 후보 표의 boosts 열. trace `rrf_fuse.meta.topk / topk_in / topk_out / topk_dropped` |
| 주입 | trace 단계 `channel_inject{inject, window, moved}` · `Hit.why` 에 `inject:fts` · 결과 `stages.inject`(output_mode=reranked) · Web 후보 표 "주입(channel_inject)" 주석 |
| 요청 단위 | CLI `query --tuning "fts_topk_n=5,fts_topk_w=1.5"` · Web Pipeline 페이지 rrf_fuse 블록 "이번 요청에만" / 사이드바 오버라이드 · MCP `wiki_query(overrides={"tuning": {…}})`. 모르는 키·범위 밖 값은 `ValueError` → MCP `isError`, Web 400 |
| 영구 | `python -m llmwiki tuning set fts_topk_n=5 fts_topk_w=1.5` · Web Settings › 튜닝 표 · `tuning.json` 직접 편집(`config reload`) |

## 1. 왜 이렇게 만들었나 (설계 근거)

`fusion.fuse()` 는 채널 가중치 `w_c` 하나를 모든 순위에 같게 곱했다(`w_c/(k+rank)`). 요구는 "**그 채널의 top-k 안에 든 후보와 밖의 후보를 다르게 대접하라**" 였다.

| 대안 | 왜 버렸나 / 골랐나 |
|---|---|
| 리랭크 입력에서 채널별로 top-k 만 남기기 | RRF 의 "여러 채널이 합의한 후보" 신호를 버리게 되고 `fusion_method` 와 겹친다 → 기각 |
| 채널별 `rrf_k` | 순위 감쇠 곡선을 바꾸는 것이라 "안/밖" 을 뚜렷이 가르지 못하고 직관적으로 조절하기 어렵다 → 기각 |
| **구간 가중 `topk_w`/`tail_w` + 보장 주입 `channel_inject`** ✔ | 배율은 되돌리기 쉽고(1.0 = 끔), `tail_w=0` 으로 "밖은 버림" 도 표현된다. 주입은 이미 있는 `external_rag_inject` 와 같은 방식이라 리랭커가 최종 순위를 정하는 원칙이 유지된다 |

두 장치의 역할 분담: 구간 가중은 **점수**를 바꾸고(융합 순위 자체가 달라짐), 주입은 점수와 무관하게 **리랭크 후보 창에 들어감**만 보장한다("벡터 1위인데 리랭크 후보에도 못 들었다" 방지). 주입된 후보의 최종 순위는 리랭커가 정하므로 부작용은 리랭크 후보 수 증가뿐이다.

## 2. 설정 키 (`tuning.json`, 단계 `rrf_fuse`)

| 키 | 형 · 범위 | 기본 | 뜻 |
|---|---|---|---|
| `fts_topk_n` · `vector_topk_n` · `graph_topk_n` · `doc_vector_topk_n` · `external_topk_n` | int 0..200 | `0` | 그 채널에서 "top-k 안" 으로 볼 순위. **0 = 구간 가중 끔**(그 채널은 예전과 같다) |
| `fts_topk_w` · `vector_topk_w` · `graph_topk_w` · `doc_vector_topk_w` · `external_topk_w` | float 0..5 | `1.0` | top-k 안 후보의 채널 가중 배율 |
| `fts_tail_w` · `vector_tail_w` · `graph_tail_w` · `doc_vector_tail_w` · `external_tail_w` | float 0..5 | `1.0` | top-k 밖 후보의 배율. **0 = 밖은 그 리스트에서 버림**(다른 채널에서 나오면 살아남는다) |
| `channel_inject` | str | `""` | `fts:2,vector:2,graph:1` — 채널별 리랭크 창 보장 주입 수. 모르는 채널·0 이하는 무시 |

`external_topk_n` 은 소스마다 따로 세지 않고 각 `ext_<src>` 리스트에 같은 n 을 적용한다. `tuning show --stage rrf_fuse` 가 설명·범위·현재값을 보여 주고 `docs/TUNING.md`(`tuning doc`)에 같은 표가 있다.
저장소의 `tuning.json` 은 기본값과 같은 값을 쓰지 않는 희소 파일이라 이 키들이 보이지 않는다 — 모든 키를 명시하려면 `python -m llmwiki config fill-defaults --tuning`(`_explicit_defaults: true` 표식). `setup/tuning.example.json` 은 `--examples` 를 붙였을 때 생성된다.

## 3. 동작

### 3.1 구간 가중 (`fusion.fuse(lists, weights, k, method, multi_bonus, topk)`)
1. `topk = fusion.topk_map(T)` — `<채널>_topk_n > 0` 인 채널만 `{채널: (n, topk_w, tail_w)}`. 모두 0 이면 빈 dict → 인자 없음과 동일(회귀 없음, `test_no_topk_is_unchanged`).
2. 리스트마다 `base_channel(name)` 으로 채널을 정하고, 순위 r(1부터)에 `factor = topk_w (r ≤ n) | tail_w (r > n)`. `tail_w == 0` 이면 그 항목은 이 리스트에서 **Hit 를 만들지 않는다**(`topk_dropped` 카운트; 다른 리스트에 있으면 거기서 만들어진다).
3. 배율은 모든 `fusion_method`(rrf · weighted/minmax · zscore · dbsf · rrf_boost)에서 `w_list × factor` 로 들어간다.
4. `factor != 1.0` 이면 `Hit.boosts.setdefault("topk_<채널>", factor)` — 같은 채널의 보조 리스트가 여럿이어도 첫 값만 남긴다.
5. `rrf_fuse` 단계 meta: `topk={채널: {n, topk_w, tail_w}}, topk_in, topk_out, topk_dropped` (+ 기존 candidates/multi_source/overlap/method). `topk` 가 비면 이 키들은 없다.
6. `apply_boosts()` 는 이제 `h.boosts.update(...)` 로 **합친다**(예전에는 덮어써 topk 흔적이 사라졌다) → 근거 표에 `topk_fts×1.5 · time×1.5` 처럼 함께 보인다.

### 3.2 리랭크 창 보장 주입 (`fusion.inject_channels(hits, lists, inject, win)`)
1. `win` = 리랭크 후보 창 = `rerank_candidates × cfg.k_mult`(0 이면 `max(top_k_final×2, 10)`). `external_rag_inject` 와 같은 창.
2. `parse_inject_map(T["channel_inject"])` → `{채널: n}`. 채널 주 리스트: fts/vector/graph/doc_vector 는 같은 이름, external 은 모든 `ext_*`.
3. 각 채널 주 리스트 상위 n개 중 현재 위치가 `win` 밖인 후보를 `hits[win-1].fused + 1e-6` 로 올리고 `why` 에 `inject:<채널>` 추가 → 다시 정렬. 반환 `{채널: [올린 id]}`.
4. trace 단계 `channel_inject{inject, window, moved}` + `n`. 옮긴 것이 없어도 단계는 남는다(설정이 있을 때).
5. 적용하지 않는 경우: `channel_inject` 가 비었을 때 · 단계 재실행에서 리랭크 결과를 재생할 때(`rp["reranked"]` 있음) · `output_mode=fused`(리랭크 전 순위를 보려는 것이라 주입도 하지 않음).

## 4. 세 창구 — 요청 단위 오버라이드 경로

세 창구는 모두 `Pipeline.request_scope(overrides={"tuning": {…}})` 한 길을 탄다: `_tuning.T.push_overlay()` → `T.set(k, v)`(키·형·범위 검증, 실패 시 오버레이 롤백 + `ValueError`) → 요청 끝에 `pop_overlay`. 파일에는 남지 않는다.

| 창구 | 요청 단위 | 영구 |
|---|---|---|
| CLI | `python -m llmwiki query "…" --tuning "fts_topk_n=5,fts_topk_w=1.5,channel_inject=vector:2"` | `python -m llmwiki tuning set fts_topk_n=5 fts_topk_w=1.5 channel_inject=fts:2,vector:2` · `tuning reset fts_topk_n` |
| Web | Pipeline 페이지(🧭) rrf_fuse 블록의 "이번 요청에만" 값 → 사이드바 "이번 요청 오버라이드: 토글 n · 튜닝 m" → `POST /api/query {"overrides": {"tuning": {…}}}` | Settings › 튜닝 표(`/api/tuning`) 또는 Pipeline 블록 저장 → `tuning.json` |
| MCP | `wiki_query(question, overrides={"tuning": {"fts_topk_n": 5, "fts_topk_w": 1.5, "channel_inject": "vector:2"}})` — 모르는 키는 `isError: invalid overrides: …` | (파일 편집은 MCP 밖) |

결과 확인: `trace.children[rrf_fuse].meta.topk`, `trace.children[channel_inject].meta.moved`, `result.hits[i].boosts.topk_<채널>`, `result.hits[i].why` 의 `inject:<채널>`. `output_mode=reranked` 로 부르면 `stages.rerank_before`(주입 뒤 리랭크 입력 순서)와 `stages.inject` 를 한 응답에서 본다([ANSWER_MODES.md](ANSWER_MODES.md) §3).

## 5. 검증 명령

```powershell
python -m unittest tests.test_fusion_topk -v
#   BaseChannelTest(3): base_channel · topk_map_from_tuning · parse_inject_map
#   FuseTopkTest(5): no_topk_is_unchanged · topk_weights_and_boosts · tail_zero_drops_entries · other_methods_apply_factor · apply_boosts_merges
#   InjectTest(2): inject_lifts_into_window · inject_noop
#   PipelineTopkTest(2): topk_via_request_overrides(trace meta·boosts·오버레이 해제) · bad_tuning_override_is_rejected      (총 12건 + 13번째 없음, 12건)
python -m llmwiki tuning show --stage rrf_fuse | Select-String "topk|tail|channel_inject"
python -m llmwiki query "AGC 이득 오류 담당" --tuning "fts_topk_n=1,fts_topk_w=2.0,channel_inject=vector:2" --json --no-log | Select-String "topk_fts|inject"
python -m llmwiki query "AGC 이득 오류 담당" --output reranked --tuning "channel_inject=vector:2" --no-log        # 후보 표의 why 열에 inject:vector
```

## 6. 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| `topk_w` 를 바꿨는데 순위가 그대로 | `<채널>_topk_n` 이 0(끔). n 을 먼저 정한다. `rrf_fuse.meta.topk` 에 채널이 보여야 한다 |
| 어떤 후보가 아예 사라졌다 | `tail_w=0` 으로 그 채널 밖 후보를 버렸고 다른 채널에도 없었다(`meta.topk_dropped`). `tail_w` 를 0.3~0.5 로 |
| `channel_inject` 를 줬는데 `moved` 가 비어 있다 | 그 채널 상위 n개가 이미 창 안에 있다(정상) · 채널 이름 오타(모르는 채널은 무시) · 리랭크 재생 중 · `output_mode=fused` |
| MCP `invalid overrides: fts_topk_n …` | 범위(0..200)·형 오류. `tuning show --stage rrf_fuse` 로 범위 확인 |
| `tuning.json` 에 키가 없다 | 희소 파일이 정상. `tuning show` 가 기본값을 보여 주고, 전부 명시하려면 `config fill-defaults --tuning` |
| 근거 표의 boosts 에 `topk_*` 가 없다 | 배율이 1.0(기록 안 함) 또는 그 후보가 top-k 판정 대상 리스트에 없었다 |
| 주입 후보가 최종 근거에 없다 | 정상일 수 있다 — 주입은 리랭크 **입력**을 보장할 뿐 최종 순위는 리랭커가 정한다. `output_mode=reranked` 의 `stages.rerank_before` vs `final_order` 로 확인 |

## 7. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/fusion.py` | `BASE_CHANNELS` · `base_channel()` · `topk_map()` · `fuse(topk=)`(구간 가중, `topk_in/out/dropped`, `boosts["topk_<채널>"]`) · `apply_boosts()`(boosts 병합) · `parse_inject_map()` · `inject_channels()` |
| `llmwiki/query_engine.py` | `_retrieve()`: `topk_map(T)` → `fuse(..., topk=)`(stage meta `topk`) · `parse_inject_map` → `inject_channels`(stage `channel_inject`, 재생/fused 제외) · `R["inject"]` → `result.stages.inject` |
| `llmwiki/tuning.py` | 16개 키(`_p(..., "rrf_fuse", ...)`) + 설명·범위 → `tuning show`/`docs/TUNING.md`/Web 튜닝 표 |
| `llmwiki/pipeline.py` | `request_scope(overrides["tuning"])` — 오버레이 push/set/pop, `ValueError` |
| `llmwiki/cli.py` | `query --tuning k=v,k=v` → `overrides["tuning"]` |
| `llmwiki/mcp.py` | `wiki_query.overrides`(설명에 tuning 예시) → `pipe.request_scope(overrides=…)` · `ValueError` → `isError` |
| `llmwiki/web/server.py` | `OVERRIDE_SAFE_KEYS` 의 `tuning` |
| `llmwiki/web/static/js/ask.js` | 후보 표의 `주입(channel_inject)` 주석 |
| `tests/test_fusion_topk.py` | 12건 (§5) |
