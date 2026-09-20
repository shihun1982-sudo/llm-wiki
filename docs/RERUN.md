# RERUN — 질의를 **특정 단계부터** 다시 실행하기 (디버깅)

> 화면: `Ask ▸ 질의 ▸ 단계별 프로파일` 과 `관측 ▸ 요청 프로파일 ▸ 워터폴` 의 각 줄 왼쪽 **⟲**
> CLI: `python -m llmwiki rerun <request_id> --from <단계>`
> API: `POST /api/query/rerun` · `GET /api/rerun?request_id=<id>`

## 0. 무엇을 푸는 기능인가

한 질의의 시간은 대체로 이렇게 쏠린다 (실제 측정 예, 총 53.5초):

| 단계 | 비중 | 시간 |
|---|---|---|
| `answer_llm` | 61% | 32.8s |
| `claim_check` | 25% | 13.3s |
| `query_expand` | 9% | 4.7s |
| `rerank_llm` | 5% | 2.5s |
| 나머지 전부 (검색·융합·부스트·컨텍스트) | < 1% | 0.1s 안팎 |

프롬프트 한 줄, `claim_support_min` 하나, answer 모델의 effort 하나를 바꿔 보려고 **매번 53초**를
쓰는 것은 낭비다. 게다가 매번 처음부터 돌면 LLM 확장·리랭크가 조금씩 달라져서, 답이 바뀐 이유가
**내가 바꾼 값 때문인지 검색이 달라져서인지 알 수 없다**. 디버깅에서 이게 제일 나쁘다.

그래서 앞 단계 결과를 저장해 두었다가 **그대로 재생**하고, 고른 지점부터만 지금 설정으로 다시 돈다.
위 예에서 '답변부터' 재실행은 32.8초, '검증부터' 는 13.3초면 끝난다 — 나머지는 재생이다.

## 1. 설계: '이어서'가 아니라 '재생'

두 가지 방법이 있었다.

| 방법 | 어떻게 | 왜 안 골랐나 / 골랐나 |
|---|---|---|
| **resume** (이어서) | 중간 상태를 통째로 복원해 파이프라인 중간부터 이어 붙인다 | `QueryEngine.run()` 을 단계별 함수로 쪼개는 큰 수술이 필요하다. 제품에서 가장 뜨거운 경로라 회귀 위험이 크고, trace 도 반쪽만 남는다 → **기각** |
| **replay** (재생) ✔ | 파이프라인은 처음부터 돌되, 재시작점 **앞** 단계는 계산 대신 저장값을 돌려준다 | 각 단계 앞에 "재생할 값이 있으면 그것을 쓴다" 분기만 넣으면 된다. trace 가 온전히 남아 **어디까지 재생했는지 화면에 그대로 보인다** → 채택 |

재생한 단계는 trace 에 `replayed: true` 로 표시되고, 화면에서는 막대가 빗금으로, 이름 옆에 `재생`
배지로 나온다. 꺼져서 건너뛴 단계(`enabled: false`)와 **구분된다** — 둘을 같게 보이면
"이 단계가 왜 안 돌았지?" 를 판단할 수 없다.

## 2. 재시작점

| id | 라벨 | 재생하는 것 | 언제 쓰나 |
|---|---|---|---|
| `plan` | 계획부터 | (없음 — 사실상 전체 재실행) | 질의 확장·라우팅까지 다시 보고 싶을 때 |
| `retrieve` | 검색부터 | 계획(확장 질의·라우팅·pin) | `top_k_*`, FTS 모드, 채널 on/off |
| `rrf_fuse` | 융합부터 | + 채널별 후보 목록 | `rrf_k`, `fusion_method`, 채널 가중 |
| `boost` | 부스트부터 | + 융합 결과 | 시간·문서종류·pin·피드백 가중 |
| `rerank` | 리랭크부터 | + 부스트 순위 | 리랭크 모델·후보 수·`rerank_chunk_chars` |
| `doc_expand` | 문서 단위 확장부터 | + 리랭크 결과 | `doc_expand_*` |
| `context` | 컨텍스트 구성부터 | + 최종 근거 목록 | `context_max_chars`, `context_neighbors`, trim/dedupe |
| `answer_llm` | **답변 생성부터** | + 컨텍스트 전체 | **프롬프트·answer 모델·effort·max_tokens** ← 가장 쓸모 있다 |
| `claim_check` | 검증부터 | + 답변 | `claim_support_min`, `claim_policy`, `claim_check_llm` |

뒤로 갈수록 재생 범위가 **누적**된다. 예를 들어 '리랭크부터' 는 융합도 재생한다 — 어차피 부스트
결과로 덮어써질 값을 다시 계산할 이유가 없다.

trace 의 단계 이름 → 재시작점 대응표는 서버가 준다(`GET /api/rerun` 의 `stage_point`). 화면에
박아 두지 않는 이유는, 파이프라인에 단계가 늘 때 화면과 어긋나기 때문이다. 대응이 없는 단계
(`sync_index` 처럼 되돌릴 의미가 없는 것)에는 ⟲ 가 붙지 않는다.

## 3. 무엇을 저장하나 (그리고 무엇을 저장하지 **않나**)

파일: `<rerun_dir>/req_<request_id>.json` (기본 `data/reruns/`). 보통 **80~90 KB**.

저장한다: 계획(확장 질의·라우팅·pin·시간범위), 채널별 후보 목록(청크 id + 점수), 융합·부스트·리랭크
**순서와 점수**, 만들어진 컨텍스트 텍스트와 인용, 근거 판정, 답변, 그때의 설정·튜닝 스냅샷.

저장하지 **않는다**: 청크 본문. 색인에 있으므로 id 로 다시 읽으면 된다. 이것 하나로 파일이 수 MB 에서
수십 KB 로 줄어든다. 예외는 **외부 RAG 의 가상 청크**(`ext:…`) — 색인에 없으므로 본문째 저장한다.

### 색인이 바뀌면 거부한다

청크 id 는 빌드마다 달라질 수 있다. 그래서 저장할 때 `build_version` 을 함께 적고, 재실행 시 다르면
**재생을 거부한다**(`plan` 은 중간 결과를 쓰지 않으므로 허용). 엉뚱한 근거로 답을 만들어 내는 것보다
거부하고 이유를 말하는 편이 낫다.

## 4. 설정 (`config.json`)

| 키 | 기본 | 뜻 |
|---|---|---|
| `toggles.rerun_capture` | `true` | 질의마다 중간 결과를 남긴다. 끄면 ⟲ 가 "저장된 중간 결과 없음" 으로 막힌다 |
| `rerun_dir` | `"data/reruns"` | 저장 폴더. `data/…` 는 `data_dir` 아래로 본다 (격리 환경에서도 따라간다) |
| `rerun_keep` | `50` | 최근 몇 건을 남길지. 넘으면 오래된 것부터 지운다 |
| `rerun_max_mb` | `4.0` | 한 건의 상한. 넘으면 저장하지 않고 trace 에 이유를 남긴다 |

> `rerun_keep` 은 실제로 영향을 준다: 질의를 50번 더 하면 예전 요청의 ⟲ 는 막힌다.
> 오래 붙들고 실험할 요청이 있으면 값을 키우거나 파일을 따로 복사해 둔다.

## 5. 쓰는 법

### Web UI
1. 질의를 한 번 실행한다 (또는 `관측 ▸ 요청 프로파일` 에서 지난 요청을 연다).
2. 왼쪽 사이드바에서 바꾸고 싶은 토글·설정을 바꾼다 (보통 질의에 쓰는 그 설정 그대로다).
3. 단계별 프로파일에서 다시 돌리고 싶은 단계 줄의 **⟲** 를 누른다 → **바로 실행된다**.
   결과 화면이 그 자리에서 다시 그려지고, 재생된 단계는 빗금 막대 + `재생` 배지로 표시된다.
4. 사이드바에 없는 값을 이번 한 번만 바꿔 보려면 **Shift(또는 Alt) + 클릭** → 창이 뜬다.
   재시작점을 고르고 평면 JSON 으로 적는다: `{"top_k_final": 12, "claim_check": false}`.
   프리셋 이름(`speed`, `deep_research`)도 줄 수 있다.

> 클릭 한 번에 바로 도는 이유: 값 하나 바꿔 보는 일을 반복하는 도구인데 매번 창이 뜨면 단계가 하나씩
> 늘어난다. 사이드바 설정이 그대로 쓰이므로 "값 바꾸고 ⟲" 가 가장 짧은 길이다.

결과는 **새 요청으로 기록되고 그 자신의 중간 결과도 저장**되므로, 거기서 또 이어서 실험할 수 있다.
원본 요청의 중간 결과는 덮어쓰지 않는다.

### CLI
```bat
python -m llmwiki rerun --points                     :: 재시작점 목록
python -m llmwiki rerun --list                       :: 저장된 중간 결과 목록
python -m llmwiki rerun 3255 --from answer_llm --trace
python -m llmwiki rerun 3255 --from claim_check --claim-support-min 0.6
```

### API
```
GET  /api/rerun?request_id=<id>     → {points, stage_point, available, compatible, reason, capture_on, saved[]}
POST /api/query/rerun               → {request_id, from, overrides?, preset?, mode?, debug?}
                                      응답은 /api/query 와 같은 {result, trace} (+ rerun)
```
권한 등급은 질의와 같다(`read`). 색인을 바꾸지 않고 읽기만 하기 때문이다.

## 6. 경계 — 재실행이 **하지 않는** 것

- **캐시로 답하지 않는다.** `query_cache`/`precompute` 가 켜져 있어도 재실행은 캐시를 읽지 않는다.
  (초기 구현에는 이 구멍이 있었다: 캐시가 먼저 맞아 `cache_hit` 하나만 찍고 예전 답이 그대로 돌아와,
  설정을 바꿔도 화면이 그대로였다. `verify_web.py` 의 재실행 검사가 이걸 잡았다.)
- **fallback 루프를 돌지 않는다.** '답변부터'·'검증부터' 처럼 컨텍스트를 재생하는 재실행에서 근거가
  부족해도 검색을 다시 하지 않는다. fallback 은 *검색을 다시 하는* 것이라 "앞은 그대로 두고 뒤만
  바꿔 본다" 는 목적과 어긋난다. trace 에 건너뛴 이유가 남는다.
- **fallback 라운드는 재생하지 않는다.** 재생은 첫 검색 라운드에만 적용된다.
- **평가 도구가 아니다.** 여러 설정을 자동으로 비교하려면 `Trial 비교`(`trial run`)나
  `eval --matrix` 를 쓴다. 재실행은 사람이 한 번에 하나씩 보는 디버깅 도구다.

## 7. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/rerun.py` | 재시작점 표(`POINTS`/`STAGE_POINT`) · 캡처(`Capture`) · 저장/조회/정리 · 재생 계획(`Resume`) · 호환성 검사 |
| `llmwiki/query_engine.py` | 각 단계 앞의 재생 분기, 캡처 지점, `_replay_retrieval()`(검색~컨텍스트 통째 재생), 캐시 차단 |
| `llmwiki/profiler.py` | `Profiler.replayed()` 와 `Stage.replayed` → trace 에 `replayed: true` |
| `llmwiki/pipeline.py` | `Pipeline.rerun(request_id, point, …)` — 호환성 검사 후 `Resume` 을 붙여 엔진 실행 |
| `llmwiki/web/server.py` | `GET /api/rerun` · `POST /api/query/rerun` (일반 질의와 같은 티켓·같은 `request_scope`) |
| `llmwiki/web/static/js/core.js` | 워터폴의 ⟲ 버튼, 재실행 창(`openRerun`), `재생` 배지 |
| `llmwiki/cli.py` | `rerun` 명령 (`--points` · `--list` · `--from` · `--trace`) |
| `tests/test_rerun_0917.py` | 16건 — 저장 범위, 재시작점별 재생 범위, 설정 반영, 색인 불일치 거부, 캐시 차단, 원본 보존 |
| `tools/verify/verify_web.py` | 재실행 API 왕복 7건 |
