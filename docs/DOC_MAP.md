# DOC_MAP — 문서 지도: 무엇을 언제 읽나, 무엇을 믿나

> **규칙 한 줄: `docs/` 바로 아래에 있으면 지금의 사실, `docs/history/<날짜>/` 에 있으면 그날의 사실이다.**
>
> 현행 문서 <!--live:docs-->44개 · 기록 <!--live:hist-->30개. 전부 읽을 필요는 없다.
> 기계적 정합(링크 · 명령 · 설정 키 · API 경로 · 규모 숫자 · 레이아웃)은 `python tools/verify/verify_docs.py` 가 지킨다.

## 0. 가장 급한 사람을 위한 세 줄

| 지금 하려는 일 | 읽을 것 |
|---|---|
| **다른 환경(회사 서버)에 올린다** | [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) → [PORTING.md](PORTING.md) → [LLM_CONNECT.md](LLM_CONNECT.md) → `python tools/verify/verify_all.py` |
| **코드를 고치러 왔다** | [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md)(지도) → 고칠 영역의 기능 문서(§3) → [TESTING_GUIDE.md](TESTING_GUIDE.md)(무엇을 돌리나) |
| **답이 이상해서 원인을 찾는다** | [FORENSIC.md](FORENSIC.md) → [ANALYSIS_MODE.md](ANALYSIS_MODE.md) → [RERUN.md](RERUN.md) → [SWEEP.md](SWEEP.md) |

## 1. 폴더 구조 — 왜 이렇게 나눴나

```
docs/
  DOC_MAP.md            ← 여기 (입구)
  SYSTEM_ARCHITECTURE.md  SECURITY.md  BRINGUP_GUIDE.md  …   ← 현행 문서. 날짜 없음. 주제당 하나.
  history/
    README.md           ← 회차 색인 (무엇을 언제 했나)
    2026-09-11/ … 2026-09-20/   ← 그날의 계획·검증·리뷰. 불변.
```

| | **현행 문서** (`docs/` 바로 아래) | **기록 문서** (`docs/history/<날짜>/`) |
|---|---|---|
| 무엇 | 기능 설명 · 운영 절차 · 구조 지도 · 참조 사전 | 구현 계획 · 검증 보고 · 리뷰 · 인수인계 |
| 이름 | **날짜 없음** (`SECURITY.md`) | 그대로 둔다 (`VERIFICATION_0917.md`) |
| 낡으면 | **고친다** | **고치지 않는다** |
| 숫자 | `<!--live:키-->` 가 붙은 것은 하네스가 코드와 대조 | 그때 잰 값 그대로 |
| 읽는 때 | 늘 | "왜 이렇게 만들었나" 가 궁금할 때만 |

**왜 현행 문서를 날짜 폴더로 나누지 않았나.** 날짜 폴더는 *여러 제품 버전을 동시에 서비스할 때*
쓰는 장치다(Docusaurus·mkdocs 의 버전 폴더). 여기는 사내에 한 벌만 도는데 그렇게 나누면
"SECURITY 의 최신은 어느 폴더?" 를 매번 찾아야 하고 폴더마다 낡은 사본이 쌓인다.
게다가 코드·설정 예시가 `docs/SECURITY.md` 같은 경로를 100곳 넘게 가리키고 있어 현행을 옮기면 전부 깨진다.
**결정 기록만 날짜로 묶는 것**은 반대로 업계 관행이다 — ADR(Architecture Decision Record)이 정확히
"결정은 날짜 찍어 불변으로, 현행 문서는 날짜 없이 살아 있게" 다.

그래서 규칙은 하나다: **날짜가 붙은 이름은 `history/` 에 산다.** 현행 자리에 날짜가 붙은 문서가
있으면 `verify_docs.py` 가 실패시킨다 — 약속이 말로만 남지 않게.

> 기록 문서에 적힌 결함·미완료는 **대부분 이후 회차에서 처리됐다.** 지금 상태는 현행 문서와
> [VERIFICATION.md](VERIFICATION.md) 가 말한다.

## 2. 올리기 · 포팅 (읽는 순서대로)

| 문서 | 무엇 | 언제 |
|---|---|---|
| [BRINGUP_GUIDE.md](BRINGUP_GUIDE.md) | **가장 큰 문서이자 출발점** — 환경·설치·설정 파일 18종·프로바이더 연결·코퍼스 계약·첫 빌드·평가 기준선·스케줄·운영·문제 해결 | 새 환경에 올릴 때 처음부터 끝까지 |
| [PORTING.md](PORTING.md) | 환경에 묶인 **모든 연결 정보**가 어디 있는지 한 장에. `config bundle --out conf` + `LLMWIKI_CONF_DIR` | 무엇을 들고 갈지 정할 때 |
| [LLM_CONNECT.md](LLM_CONNECT.md) | LLM 에 닿는 두 방법(API 게이트웨이+PAT / headless `opencode`)의 `config.json` 샘플 5개 | 사내 LLM 에 붙일 때 |
| [HEADLESS.md](HEADLESS.md) | headless 에이전트 전달 방식·격리·`WinError 206` | opencode·claude·codex 를 쓸 때 |
| [CORPUS_CONTRACT.md](CORPUS_CONTRACT.md) | 문서 front matter 계약(`doc_type`·`ext_id`·관계) | 자기 문서를 넣기 전에 |
| [RESET.md](RESET.md) | 앞 환경의 흔적 지우기 3종 — 미리보기가 기본 | 폴더를 복사해 옮긴 직후 |
| [SECURITY.md](SECURITY.md) | 로그인(로컬+SSO)·역할 6단계·작업 등급 7단계·API 키·문서 단위 접근 제어 | 사람들에게 열기 전에 **반드시** |
| [CONCURRENCY.md](CONCURRENCY.md) | 동시 사용자 슬롯·대기열·속도 제한·서버 모니터 | 여러 명이 쓸 때 |
| [SCHEDULER.md](SCHEDULER.md) | 정해진 시각마다 증분 빌드·정리 | 운영에 들어갈 때 |
| [setup/INSTALL.md](../setup/INSTALL.md) | 설치와 최소 설정 | 맨 처음 |

## 3. 기능별 사용 설명 (필요할 때 펼친다)

| 영역 | 문서 |
|---|---|
| **화면** | [WEB_UI.md](WEB_UI.md)(탭·분할·프로파일·Ask 검색/디버그·운영 통계) · [PIPELINE_PAGE.md](PIPELINE_PAGE.md) · [ACTIVITY_DETAIL.md](ACTIVITY_DETAIL.md) · [REQUEST_HISTORY.md](REQUEST_HISTORY.md) · [COLLAB.md](COLLAB.md) |
| **질의 동작** | [ANSWER_MODES.md](ANSWER_MODES.md)(`answer_mode`·`output_mode`) · [FUSION_TOPK.md](FUSION_TOPK.md)(채널 가중·주입) · [ENSEMBLE.md](ENSEMBLE.md)(역할당 최대 3 LLM) |
| **지식 규칙** | [QUERY_RULES.md](QUERY_RULES.md)(질의를 넓히는 사전) · [GRAPH_RULES.md](GRAPH_RULES.md)(빌드 때 그래프를 만드는 규칙) · [STOPWORDS.md](STOPWORDS.md) |
| **자가진화** | [EVOLVE.md](EVOLVE.md)(제안·승인·롤백, 제안 설명 §1.5) |
| **외부 연동** | [MCP.md](MCP.md)(우리를 도구로 내주기) · [RAG_FEDERATION.md](RAG_FEDERATION.md)(남의 RAG 를 붙이기) |
| **설정** | [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md)(자동 생성·전 키) · [TUNING.md](TUNING.md)(자동 생성·단계별 손잡이) · [SETTINGS_SYNC.md](SETTINGS_SYNC.md)(UI ↔ 파일 ↔ 유효값) · [OPTIMIZATION_GUIDE.md](OPTIMIZATION_GUIDE.md)(자동 생성) |
| **운영 관측** | [OPS_STATS.md](OPS_STATS.md)(빌드·질의·지연·토큰·디스크) · [LOG_QUOTA.md](LOG_QUOTA.md) · [BUILD_UNDER_LOAD.md](BUILD_UNDER_LOAD.md)(쓰는 중에 빌드해도 되나) |

## 4. 품질을 고치는 도구 (증상 → 문서)

| 증상 | 문서 | 첫 명령 |
|---|---|---|
| 답이 부실하다 / 근거가 약하다 | [FORENSIC.md](FORENSIC.md) | `forensic last` |
| **기대한 문서**가 안 나왔다 | [FORENSIC.md](FORENSIC.md) §기대 결과 | `forensic expect last --doc <ID> --term <용어>` |
| 어느 설정을 만질지 모르겠다 | [ANALYSIS_MODE.md](ANALYSIS_MODE.md) | `query "…" --analyze` |
| 값을 바꿔 가며 비교하고 싶다 | [SWEEP.md](SWEEP.md) | `sweep run last --key rrf_k --range 10:100:10` |
| 매번 처음부터 도는 게 느리다 | [RERUN.md](RERUN.md) | `rerun <id> --from answer_llm` |
| 품질이 좋아졌는지 숫자로 | [EVAL_TRIAL.md](EVAL_TRIAL.md) | `eval --check` → `trial run --source queries` |
| 그래프가 좋아졌는지 | [GRAPH_PROFILE.md](GRAPH_PROFILE.md) | `graph profile --compare` |
| 규칙이 실제로 쓸모 있나 | [QUERY_RULES.md](QUERY_RULES.md) | `rules effect --order useless` |

## 5. 구조를 이해하기

| 문서 | 무엇 | 깊이 |
|---|---|---|
| [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) | **현재 구조 전체 지도** — 용어 · 설계 제약과 그 이유 · 모듈 · 데이터 모델 · 네 흐름 · 질의 27단계 · 한 질의의 여정 · 확장 지점 · 정합 불변식 | 처음 오는 사람이 읽을 것 |
| [CLI_FLOWS.md](CLI_FLOWS.md) | 명령별 운영 흐름 — 가장 긴 참조 문서 | 특정 명령을 쓸 때 |
| [history/2026-09-17/REBUILD_SPEC.md](history/2026-09-17/REBUILD_SPEC.md) | v2 시점의 재구현 사양서 | 바닥부터 다시 만들 때만 (기록) |

## 6. 고치고 나서 — 검증

| 문서 | 무엇 |
|---|---|
| [TESTING_GUIDE.md](TESTING_GUIDE.md) | **무엇을 고쳤을 때 무엇을 돌리나** — 영역별 표, 새 기능을 넣을 때 반드시 늘려야 하는 것, **테스트를 새로 쓸 때의 규칙**, 실패 메시지 읽는 법 |
| [VERIFICATION.md](VERIFICATION.md) | 검증 체계(층별로 무엇을 잡나)와 **최신 전체 결과**(`verify_all.py` 가 자동 갱신) |
| [SURFACE_ALIGNMENT.md](SURFACE_ALIGNMENT.md) | CLI·Web·MCP 정렬을 세 겹(존재·전수·동작)으로 보는 법과 지금 상태 |

한 줄로: **`python tools/verify/verify_all.py`**.

## 7. 기록 — [history/README.md](history/README.md)

회차별 계획·검증·리뷰. 날짜 폴더 하나가 한 회차다. **고치지 않는다.**
가장 최근 회차는 [2026-09-19](history/2026-09-19/IMPLEMENTATION_PLAN_0919.md) — 요청 14항목의
판단 근거(조사한 사실 · 택하지 않은 대안과 이유 · 검증)가 항목마다 적혀 있다.

## 8. 문서를 고치는 사람에게 (사람이든 LLM 이든)

1. **어느 쪽인지 먼저 본다.** `docs/` 바로 아래 = 고친다. `docs/history/` = 고치지 않는다.
2. **기능을 바꿨으면 그 기능의 현행 문서도 같이 바꾼다.** 코드만 고치고 문서를 두면 다음 사람이
   문서를 믿고 틀린 절차를 밟는다. 이 저장소에서 문서는 부속물이 아니라 인도물이다.
3. **새 현행 문서**는 README §0 색인이나 BRINGUP_GUIDE 에서 링크한다. **새 기록 문서**는
   [history/README.md](history/README.md) 회차 표에 줄을 더한다. 둘 다 빠지면 `verify_docs.py` 가 고아로 잡는다.
4. **날짜를 파일 이름에 넣지 않는다** — 넣는 순간 그것은 기록이고 `history/<날짜>/` 로 가야 한다.
5. **지금의 규모를 숫자로 쓸 거면 표시를 붙인다** — `CLI 명령 <!--live:cli-->49개` 처럼.
   화면에는 주석이 안 보이고, 코드와 어긋나면 하네스가 잡는다.
   키: `cli` · `api` · `mcp` · `tests` · `harness` · `docs`(현행 수) · `hist`(기록 수).
6. **고친 뒤 `python tools/verify/verify_docs.py`**.
