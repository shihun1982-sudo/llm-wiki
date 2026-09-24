# LLM Wiki v3 — FTS + Vector + GraphRAG · Self-Evolving (모뎀 HW 제어 임베디드 SW 조직용)

로컬 문서(Issue · Change List · SW/HW 설계 · 코딩 규칙 · 주간 보고 · TC 등)를 색인해 **FTS(BM25) + 벡터 + 그래프** 로 검색하고,
**근거가 있는 내용만** 구조화된 답변으로 돌려주는 위키형 RAG 시스템입니다. 근거가 부족하면 단계적으로 확장 검색(fallback)하고, 그래도 없으면
`insufficient data` 로 답하며 **포렌식**을 남깁니다. 누적된 포렌식·피드백은 자가진화 **제안**(사람 승인)이 됩니다.

외부 API 없이 완전히 동작합니다(hash 임베딩 + 규칙 그래프 + 추출식 답변). 키/서버를 붙이면 LLM 답변·확장·검증·리랭크가 켜집니다.

---

## 0. 문서 안내 — 무엇을 어떤 순서로 읽나

> **문서가 <!--live:docs-->44개다. 어느 것을 지금의 사실로 믿어야 하는지부터 알고 싶으면 [DOC_MAP.md](docs/DOC_MAP.md) 를 먼저 본다.**
> 이 저장소의 문서는 **현행 문서**(기능·절차·구조 — 낡으면 고친다)와 **기록 문서**(계획·검증·리뷰 — 그때의 사실이라 고치지 않는다)로 나뉜다.
> 기록 문서에 적힌 결함과 미완료는 대개 이후 회차에서 처리됐다 — 지금 상태는 현행 문서와 `python tools/verify/verify_all.py` 가 말한다.

### 목적별 읽는 순서

| 나는 … | 이 순서로 |
|---|---|
| **어떤 문서를 읽어야 할지부터 모르겠다** | [DOC_MAP.md](docs/DOC_MAP.md) — 문서 지도. §0 가장 급한 세 줄 · §1 현행 ↔ 기록 구분 · §4 증상별 도구 · §8 문서를 고칠 때의 규칙 |
| **처음 접했고 전체가 궁금하다** | 이 README §1~§5 → `docs/llmwiki_guide.html`(브라우저에서 클릭하며 구조 파악) → [ARCHITECTURE_V3.md](docs/history/2026-09-15/ARCHITECTURE_V3.md) §0 용어·§1 그림·§9 "한 질의의 여정" |
| **코드를 고치러 왔다 / 구조를 한 편으로 알고 싶다** | [SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md) — 설계 제약과 **채택하지 않은 것들**, 모듈 66개 지도, 질의 27단계 상세, 설정 4층, 동시성, 보안 네 겹, **확장 지점**(새 채널·프로바이더·도구를 어디에 넣나), **정합 불변식과 깨질 때 잡히는 곳** |
| **내 PC/서버에 설치해 써보고 싶다** | [setup/INSTALL.md](setup/INSTALL.md) → [BRINGUP_GUIDE.md](docs/BRINGUP_GUIDE.md) §0 체크리스트부터 순서대로 → 막히면 BRINGUP_GUIDE §10 문제 해결 |
| **다른 곳에서 이미 빌드해 둔 색인이 있다 — 다시 빌드하고 싶지 않다** | [BRINGUP_GUIDE.md §2.2](docs/BRINGUP_GUIDE.md) — 색인은 **파일 하나**(`data/llmwiki.sqlite3`)다. 그것과 `data/rules.json`·`corpus/` 를 **타임스탬프 보존해서** 복사하면 끝. 맞아야 하는 것(임베더·차원·청크 설정)과 받는 쪽 3분 확인 절차, 채널이 비어 온 경우의 부분 리빌드까지 |
| **코퍼스와 색인을 통째로 다른 서버로 옮긴다** | [BRINGUP_GUIDE.md §2.3](docs/BRINGUP_GUIDE.md) — 경로 설정이 전부 상대 경로라 **폴더째 복사하면 고칠 것이 없다**. 뺄 폴더(`data/snapshots` 가 색인보다 크다)·`robocopy /COPY:DAT`·`rsync -a` 명령과, 받는 쪽에서 **반드시 바꾸는 것**(세션 비밀키 삭제·`.env`·`embed_provider` 고정·`security.json`·`server.json`) 표 |
| **여러 사람·여러 외부 LLM 이 쓰는 서버로 열고 싶다** | [IMPLEMENTATION_PLAN_0914.md](docs/history/2026-09-14/IMPLEMENTATION_PLAN_0914.md) §0 → [SECURITY.md](docs/SECURITY.md)(역할·권한 표·API 키) → [MCP.md](docs/MCP.md)(원격/다수 LLM) → BRINGUP_GUIDE §4.4~4.5 |
| **Web UI 를 처음 쓴다 / 화면 기능이 궁금하다** | [WEB_UI.md](docs/WEB_UI.md) §0 한 장 요약 → 필요한 절만. 버튼이 안 먹는 것 같으면 §9 자가 점검 |
| **일반 사용자(viewer)에게 어떻게 보이는지 확인하고 싶다** | 헤더의 `👁 권한 보기` 에서 viewer 선택 — 서버가 실제로 그 권한으로 처리한다(권한은 낮추기만 한다). 실제 로그인 흐름까지 보려면 `serve --host 0.0.0.0` — [WEB_UI.md](docs/WEB_UI.md) §8 |
| **내 화면 설정을 계정에 저장하고 어디서나 쓰고 싶다** | [WEB_UI.md](docs/WEB_UI.md) §4 — 헤더의 `💾 내 설정 저장`. 서버 설정은 바뀌지 않는다 |
| **30명이 동시에 쓰는데 느리거나 거절당한다 / 관리자로 제어하고 싶다** | [CONCURRENCY.md](docs/CONCURRENCY.md) §0 한 장 요약 → §3 `server.json` 권장값 → Web 관리 › 서버 모니터 (또는 `python -m llmwiki server stats`) → §9 문제 해결표 |
| **동시 질의가 서로를 막는다 / 요청이 기록 없이 사라진다 / 이 변경을 다른 환경에 옮긴다** | [REQUEST_LEDGER.md](docs/REQUEST_LEDGER.md) — 이 문서 하나로 원인·설정·포팅·검증·롤백까지 (§2 슬롯 수 정하는 법 → §9 옮기는 절차 → §10 검증 → §11 문제 해결 → **§13 포팅을 LLM 에게 시키는 프롬프트**) |
| **정해진 시각·주기로 빌드·수집·evolve 를 돌리고 싶다** | [SCHEDULER.md](docs/SCHEDULER.md) §2 시점 지정 → §3 동작 19종 예시 → `setup/schedule.example.json` 복사 → Web 설정 › 스케줄 또는 `python -m llmwiki schedule add` |
| **검색이 틀렸을 때 사람이 고쳐 쌓고 싶다 (자가진화)** | [EVOLVE.md](docs/EVOLVE.md) §1 제안 종류 → **§1.5 제안 설명(승인 판단)** → §2 자동 적용 안전장치 → Web Evolve › 제안(HITL) 또는 `python -m llmwiki evolve status` · `evolve show <번호>` · `evolve auto-apply --dry-run` |
| **오래 걸리는 빌드·질의의 진행률을 보고 중간에 멈추고 싶다** | [CONCURRENCY.md](docs/CONCURRENCY.md) §4 시간 제한과 취소 (Web 진행 패널의 중지 버튼, CLI `Ctrl+C`, `server activity` / `server cancel <token>`) |
| **진행 중 작업 목록에서 한 줄을 눌러 "지금 어디쯤인지" 또는 "그래서 뭐라고 답했는지" 보고 싶다** | [ACTIVITY_DETAIL.md](docs/ACTIVITY_DETAIL.md) — 실행 중이면 단계·진행 기록 실시간, 끝났으면 **그때 저장한 답변**을 그 자리에서. ↩ Ask 화면 복원 · 📄 요청 프로파일 · ⟲ 다시 실행 |
| **답에 기대한 문서가 왜 없는지 알고 싶다** | [FORENSIC.md](docs/FORENSIC.md) → `forensic expect last --doc <ID> --term <용어>` (Web Ask 의 🎯, MCP `wiki_forensic`) |
| **품질·속도·토큰이 마음에 안 드는데 어느 설정을 만질지 모르겠다** | [ANALYSIS_MODE.md](docs/ANALYSIS_MODE.md) → `query "…" --analyze --focus quality|speed|tokens` → `logs/analysis/req_<id>.md` 를 LLM 에게 첨부 (Web 토글 `analysis_mode` + 📊, MCP `wiki_analysis`) |
| **설정을 바꿔 가며 확인하고 싶은데 매번 처음부터 도는 게 너무 느리다** | [RERUN.md](docs/RERUN.md) — 워터폴의 각 단계 **⟲** 로 그 단계부터만 다시 실행(앞 단계는 저장해 둔 결과를 재생). 답변 프롬프트 실험은 `--from answer_llm`, 검증 임계값은 `--from claim_check`. CLI `python -m llmwiki rerun <request_id> --from <단계>` |
| **어떤 손잡이가 어느 단계에 작용하는지 한 장으로 보고, 그 자료를 통째로 LLM 에게 주고 싶다** | [OPTIMIZATION_GUIDE.md](docs/OPTIMIZATION_GUIDE.md)(자동 생성: `arch doc`) → `python -m llmwiki optimize last --out bundle.md` 로 **가이드+지금 설정+질의 실측+지시문**을 한 파일로 → 그 파일을 LLM 에게 첨부 (Web Ask 의 📦 최적화 자료 묶음 다운로드) |
| **다른 RAG·검색 API·MCP 서버를 붙이고 싶다 / 외부 LLM 이 우리 MCP 하나로 여러 RAG 를 쓰게 하고 싶다** | [RAG_FEDERATION.md](docs/RAG_FEDERATION.md) §0 결정표 → §5 절차 → `setup/mcp_sources.example.json` → BRINGUP_GUIDE §4.6 |
| **옮겨 세운 뒤 전부 정상인지 확인하고 싶다** | **`python tools/verify/verify_all.py`** 한 줄이면 모든 하네스(단위·스트레스·문서정합·정렬·설정 양방향·CLI·Web·MCP·UI·버튼·보안화면·재실행·협업·타임아웃·몽키)를 돌리고 결과 표를 찍는다 → 실패 행만 [BRINGUP_GUIDE §10](docs/BRINGUP_GUIDE.md). 회차별 해설: [VERIFICATION_0918.md](docs/history/2026-09-19/VERIFICATION_0918.md) · [VERIFICATION_0917.md](docs/history/2026-09-17/VERIFICATION_0917.md) · [VERIFICATION_0916_2.md](docs/history/2026-09-16/VERIFICATION_0916_2.md) · [VERIFICATION_0916.md](docs/history/2026-09-16/VERIFICATION_0916.md) · [VERIFICATION_0915.md](docs/history/2026-09-15/VERIFICATION_0915.md) |
| **CLI·Web·MCP 가 정말 같은 기능·같은 답을 주는지 확인하고 싶다** | [SURFACE_ALIGNMENT.md](docs/SURFACE_ALIGNMENT.md) — 세 겹으로 본다: ①존재·②전수(`verify_surface_align.py`, `--inventory` 로 전수 목록) → ③동작(`verify_tri_surface.py` 가 `serve`·CLI 프로세스·`POST /mcp` 를 **진짜 띄워** 42건 비교) |
| **무엇을 고쳤을 때 어떤 테스트를 돌려야 하나** | [TESTING_GUIDE.md](docs/TESTING_GUIDE.md) — 변경 영역(모듈) → 단위 테스트 · 하네스 · 함께 볼 문서 표, 새 기능을 넣을 때 반드시 늘려야 하는 표(정렬표·MCP 도구 집합·설정 키 명시), 실패를 읽는 법 |
| **이번 버전에 무엇이 바뀌었고 기존 환경을 올릴 때 무엇을 해야 하나** | [RELEASE_NOTES.md](docs/RELEASE_NOTES.md) — 버전별 변경 표 + "운영자가 할 일". `python -m llmwiki --version` 이 맨 위 절과 같아야 한다 |
| **어떤 값을 어디서 바꾸나 / 이 키가 무슨 뜻인가 / 바꾼 뒤 무엇을 해야 하나** | [CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md) — 파일 18종 · config 키 103개 · 토글 66개 · 튜닝 141개를 한 문서에(자동 생성). §6 "값을 바꾼 뒤 무엇을 해야 하나", §7 "자주 하는 변경" |
| **설정을 바꿨는데 서버에 안 먹는다 / 파일을 고쳤는데 화면에 안 보인다** | [SETTINGS_SYNC.md](docs/SETTINGS_SYNC.md) — UI ↔ 파일 ↔ 유효값을 양방향으로 확인하는 절차(`config show --effective`·`config reload`), `config fill-defaults` 로 모든 키를 파일에 명시, 하네스 `verify_settings_sync.py` |
| **설정값 하나를 바꿔 가며 어느 단계에서 무엇이 달라지는지 보고 싶다** | [SWEEP.md](docs/SWEEP.md) — `sweep run last --key rrf_k --range 10:100:10` → 단계×값 비교 격자. 🧭 Pipeline 페이지([PIPELINE_PAGE.md](docs/PIPELINE_PAGE.md))의 스윕 폼 |
| **그래프의 무엇이 잘못됐고 무엇을 고쳐야 하나 (규칙 조각 · 코퍼스 수정) / 규칙을 바꿨는데 좋아졌는지 숫자로** | [GRAPH_PROFILE.md](docs/GRAPH_PROFILE.md) §2.5 소견 — `graph profile` (Knowledge › 그래프 진단, MCP `wiki_graph_profile`) · `--compare` |
| **그래프 화면의 "상위 N · 무리 · 관계 출처" 가 무슨 뜻인지 / 그래프를 여러 방식으로 보기** | [WEB_UI.md](docs/WEB_UI.md) §0.69 — Knowledge › 그래프 (보기 모드 4종 · 무리 상세 `graph community` · MCP `wiki_community`) |
| **동의어·약어 규칙이 어느 방향으로 퍼지는지 알고 싶다** | [QUERY_RULES.md](docs/QUERY_RULES.md) — acronym/synonym 양방향 · alias/related/exclude 일방, `rules explain <용어>` |
| **사람들이 쓰는 중에 코퍼스를 갱신해야 한다** | [BUILD_UNDER_LOAD.md](docs/BUILD_UNDER_LOAD.md) §1 30명 실측표 → §4 권장 운영(증분·채널은 아무 때나 · 전체 리빌드는 야간 또는 `reads_during_build=always`) |
| **빌드가 왜 느린지 · 질의가 얼마나 몰리는지 · 디스크가 어디서 커지는지 알고 싶다** | [OPS_STATS.md](docs/OPS_STATS.md) — `python -m llmwiki stats --full` (Web 옵저빌리티 › 시스템 의 **운영 통계**, MCP `wiki_status(full=true)`). 단계별 빌드 ms · 시간대 분포 · p50/p95 와 가장 느린 질의 · 토큰 · 근거 부족률 · 폴더별 용량과 정리 힌트 |
| **사내 게이트웨이(PAT)나 opencode 에 붙여야 한다** | [LLM_CONNECT.md](docs/LLM_CONNECT.md) §1 바꾸는 키만 추린 샘플 5개 → `models test --live` → 증명은 `tools/verify/verify_llm_switch.py` |
| **다른 환경(회사/다른 서버)에 올려야 한다 — 무엇을 들고 가나** | [PORTING.md](docs/PORTING.md) §1 연결 정보 지도 → §0 `config bundle --out conf` + `LLMWIKI_CONF_DIR` → §3 확인 명령 → 기동 절차는 [BRINGUP_GUIDE.md](docs/BRINGUP_GUIDE.md) |
| **우리 조직의 용어·ID·문서 관계를 그래프로 잡고 싶다** | [GRAPH_RULES.md](docs/GRAPH_RULES.md) §1 절 지도 → `graph-rules test "<문장>"` 로 확인 → `graph-rules lint` → `build graph`. Web 지식 › 그래프 규칙 · MCP `wiki_graph_rules` |
| **문서 근거가 없을 때도 답을 받고 싶다 / 리랭크 뒤 후보만 받아 내 LLM 으로 처리하고 싶다** | [ANSWER_MODES.md](docs/ANSWER_MODES.md) — `answer_mode=best_effort`(배경 지식 `[BK]` 표기) · `output_mode=fused|reranked|context` |
| **LLM 여러 개를 동시에 불러 취합하고 싶다** | [ENSEMBLE.md](docs/ENSEMBLE.md) — `llm_roles.<role>.ensemble`, `models ensemble set` |
| **opencode 같은 headless 에이전트가 긴 프롬프트에서 죽는다 (`WinError 206`)** | [HEADLESS.md](docs/HEADLESS.md) — `prompt_mode=stdin` 기본, `arg_max_chars` 가드, `models test --live` |
| **실패할 때 어떻게 버티는지 알고 싶다 (LLM 무응답·느림·외부 RAG 다운·폭주)** | [VERIFICATION_0917.md §3](docs/history/2026-09-17/VERIFICATION_0917.md) — `verify_timeouts.py` 24항목. mock 테스트 훅(`LLMWIKI_MOCK_FAIL`·`LLMWIKI_MOCK_DELAY_MS`)으로 실패를 **일부러 일으켜** 확인한다 |
| **이 작업을 이어받는다 / 지금 무엇이 남아 있는지 알고 싶다** | [HANDOVER_0916.md](docs/history/2026-09-16/HANDOVER_0916.md) — 2026-09-16 시점의 완료/남은 작업. 그 문서의 §2.1~§2.5 는 [VERIFICATION_0916.md](docs/history/2026-09-16/VERIFICATION_0916.md) 에서 끝났다 |
| **내가 지난번에 물어본 것과 그 답을 다시 보고 싶다 / 누가 무엇을 돌리는지 보고 싶다** | [REQUEST_HISTORY.md](docs/REQUEST_HISTORY.md) — Ask 탭의 "🕘 내 지난 요청", 결과 보관 폴더(`requests_dir`)와 보존 기간, 전체 조회 권한(`requests all`) |
| **팀원과 화면에서 바로 이야기하고, 남길 것만 남기고 싶다** | [COLLAB.md](docs/COLLAB.md) — 사이드바 휘발성 채팅 + `/게시` 로 올리는 게시판(요청과 연결), 접속자 캐릭터·말풍선 설정. 토글 `collab` 하나로 끌 수 있는 부수 기능 |
| **우리 팀 문서를 넣고 싶다** | [CORPUS_CONTRACT.md](docs/CORPUS_CONTRACT.md) → `corpus lint` → BRINGUP_GUIDE §5 코퍼스 계약 적용 |
| **명령 하나가 내부에서 무엇을 하는지 알고 싶다** | [CLI_FLOWS.md](docs/CLI_FLOWS.md) §3 해당 명령 (예제 → 내부 단계 → 실제 출력 → 오류) |
| **코드·문서 전체의 장단점과 고칠 순서를 알고 싶다 / 서버 공개 전 무엇을 막아야 하나** | [CODE_REVIEW_0917.md](docs/history/2026-09-17/CODE_REVIEW_0917.md) — 전 파일 정독 결과. §0 P0(보안 5건: 요청 overrides 로 PAT 유출·기본 admin 배포·`mode auto`·500 트레이스·MCP 3건)·P1(무결성 7건) 표, §2 영역별 결함(`파일:줄`), §4 개선 26항목, §6 우선순위·규모 |
| **답 품질이 기대만큼 안 나온다 — 무엇부터 손대야 하나** | [QUALITY_REVIEW_0917.md](docs/history/2026-09-17/QUALITY_REVIEW_0917.md) — 실제 색인에서 **측정한** 레버 순서. 1위는 튜닝이 아니라 **코퍼스 위생**(색인하면 안 되는 것이 섞이면 hit@k 0.64↔0.88). `health` 의 `corpus_self_index`·`channels_populated`·`embedder_quality` 경고부터 확인 |
| **품질을 올리고 싶다(튜닝)** | ARCHITECTURE_V3 §5 질의 단계 표 → [TUNING.md](docs/TUNING.md) → CLI_FLOWS 의 `trial` / `fusion compare` / `forensic` |
| **코드를 고치고 싶다** | ARCHITECTURE_V3 §2 모듈 지도·§3 데이터 모델 → 해당 모듈 → `tests/` |
| **왜 이렇게 설계됐는지 알고 싶다** | [ANALYSIS_REPORT_0913.md](docs/history/2026-09-13/ANALYSIS_REPORT_0913.md) → [IMPLEMENTATION_PLAN_0913.md](docs/history/2026-09-13/IMPLEMENTATION_PLAN_0913.md) → (이전 세대) ARCHITECTURE_V2 · legacy/ |

### 문서를 어떻게 관리하나 — **현행 / 기록** 두 갈래 (2026-09-20 정리)

```
docs/                       ← 현행 문서. 날짜 없음. 주제당 하나. 낡으면 고친다.
docs/history/<YYYY-MM-DD>/  ← 그날의 계획·검증·리뷰. 불변. 고치지 않는다.
```

**규칙 한 줄: `docs/` 바로 아래에 있으면 지금의 사실이다.** 지도와 읽는 순서는 [DOC_MAP.md](docs/DOC_MAP.md), 회차 색인은 [docs/history/README.md](docs/history/README.md).

| | 왜 이렇게 |
|---|---|
| 현행 문서를 **날짜/릴리스 폴더로 복제하지 않는다** | 두 벌이 되는 순간 한쪽만 고쳐지고, 읽는 사람은 최신이 어느 것인지 판단할 방법이 없다. 날짜 폴더는 *여러 제품 버전을 동시에 서비스할 때* 쓰는 장치인데 여기는 사내에 한 벌만 돈다 |
| 기계 대조가 **얼어붙은 사본에는 동작하지 않는다** | `verify_docs.py` 가 문서의 명령·설정 키·API 경로·링크·**규모 숫자**를 코드와 대조해 낡으면 실패시킨다. 과거 사본은 당연히 낡으므로 검사에서 빠지고, 빠진 문서는 결국 틀린 문서가 된다 |
| 자동 생성 문서가 있다 | [TUNING.md](docs/TUNING.md) · [OPTIMIZATION_GUIDE.md](docs/OPTIMIZATION_GUIDE.md) · [CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md) 는 코드 레지스트리에서 만든다. 과거 버전용으로 얼려 둘 수 없다 |
| **기록만 날짜로 묶는다** | 계획·검증·리뷰는 *그날의 사실*이라 고치면 "왜 이렇게 됐나" 를 되짚을 근거가 사라진다. 업계에서 ADR(결정 기록)을 날짜 찍어 불변으로 두는 것과 같은 이유 |

이 규칙은 하네스가 지킨다 — **현행 자리에 날짜 붙은 파일명이 있으면 실패**하고, 회차 색인에 없는 기록 문서는 고아로 잡힌다.
**버전 축**은 [RELEASE_NOTES.md](docs/RELEASE_NOTES.md) 하나가 담당한다(무엇이 바뀌었나 + 기존 환경을 올릴 때 할 일 + 상세 문서 링크).

### 현행 문서 — 각 문서가 담고 있는 것

**[docs/SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md) — 전체 구조 (구조를 설명하는 유일한 현행 문서)**
§0 한 장 요약과 **§0.1 핵심 용어**(청크·채널·provenance·groundedness·HITL·토글/튜닝/프리셋·request_id/run_id·창구), §1 설계 제약과 **채택하지 않은 것들**, §2 모듈 지도(어느 파일이 무슨 역할인지), §3 데이터 모델(SQLite 테이블 전부), §4 네 가지 흐름(build·query·evolve·watch), §5 질의 27단계 상세 — 단계 이름이 곧 `--trace`/Web 프로파일에 나오는 이름이며 각 단계의 토글·튜닝 키를 함께 적었고, **§5.7 한 질문이 14단계를 거치는 여정**을 실제 실행 결과로 보여 준다. 이어서 §6 LLM 호출 구조, §7 설정 4층, §8 세 창구, §9 동시성, §10 보안 네 겹, §11 관측 다섯 가지, §12 검색 품질 장치, **§13 확장 지점**(새 채널·프로바이더·도구를 어디에 넣나), **§14 정합 불변식과 깨질 때 잡히는 곳**, §15 검증 체계, §16 한계와 다음 단계, §17 숫자를 다시 세는 법.
예전에 따로 있던 `ARCHITECTURE_V2`·`ARCHITECTURE_V3`·`REBUILD_SPEC` 는 그때의 설계 기록이라 [docs/history/](docs/history/README.md) 로 옮겼다 — 구조를 알고 싶으면 이 문서 하나면 된다.

**[docs/BRINGUP_GUIDE.md](docs/BRINGUP_GUIDE.md) — 새 환경 포팅 절차서**
다른 PC·서버·조직으로 옮겨 세우는 엔지니어용. §0 체크리스트, §1 환경 요구, §2 설치와 폴더 구조, §3 설정 파일 하나하나 채우는 법(config.json 키·역할별 모델·토글·`LLMWIKI_*` 환경변수·경로 레지스트리), §4 프로바이더 연결(Anthropic / OpenAI-compatible / Ollama / rerank API / headless 에이전트 / MCP 소스 각각의 설정과 `models test`), §5 코퍼스 계약 적용, §6 첫 빌드와 검증(`health` → `build --full` → `build verify` → 확인 포인트), §7 평가 기준선과 튜닝, §8 OS 스케줄러 등록, §9 일상 운영, §10 증상별 문제 해결 표, §11 "포팅 시 코드 변경이 필요한 곳(없어야 정상)".

**[docs/CLI_FLOWS.md](docs/CLI_FLOWS.md) — 모든 CLI 명령의 단계별 동작 (운영 참고서, 가장 긴 문서)**
40여 개 하위 명령 전부를 다룬다. §1 개요(실행 방식, 공통 옵션, 설정 우선순위, PowerShell 주의점), §2 명령 카탈로그 표, §3 명령별 상세 — 각 명령마다 "예제 입력 → 내부에서 실행되는 단계와 모듈/함수 → 격리 샌드박스에서 실제로 실행해 얻은 출력 → 관련 토글·튜닝 → 흔한 오류와 종료 코드" 순서, §4 end-to-end 시나리오 3개(새 환경 bring-up / 매일 증분 운영 / 답변 품질 디버깅과 trial 회귀 확인), §5 종료 코드와 자동화 팁. 출력 예가 모두 실측이므로 자기 환경의 출력과 비교하며 읽을 수 있다.

**[docs/CORPUS_CONTRACT.md](docs/CORPUS_CONTRACT.md) — 문서 계약**
문서를 어떻게 써야 색인·검색이 잘 되는지에 대한 규칙. 파일 형식, 공통 front matter 필드(schema_version·doc_type·id·title·date·tags·module·hw·related), 7가지 문서 유형(issue·cl·sw_design·hw_design·coding_rule·weekly_report·tc_list)별 필수 필드와 권장 섹션, ID 규칙(ISSUE-nnnn, CL-nnnnn)과 그것이 만들어내는 결정적 관계, 완성 예시 문서, lint 규칙, 빌드에서 front matter 가 어떻게 쓰이는지, MCP 로 가져온 raw data 가 계약 문서로 변환되는 방식, **§8 형식이 없는 문서를 넣는 범용 변환기**(`tools/corpus_ingest.py` — 본문을 그대로 두고 `body_sha1` 로 무손실을 재검증, 텍스트 추출이 불가역인 형식은 원본을 `_originals/` 에 보관, 유형 불명은 `doc_type: note`).

**docs/llmwiki_guide.html — 인터랙티브 가이드 (브라우저로 열기)**
외부 의존 없는 단일 HTML. 구조 지도(모듈 카드를 클릭하면 역할·주요 함수 표시), Build/Query/Evolve 플로우 스테퍼(단계를 하나씩 넘기며 예제 명령·출력·확인 포인트·토글·튜닝 확인), CLI 카탈로그(검색·영역 필터), 설정 파일 지도, 데이터 모델, Web UI 7그룹과 MCP 도구, 운영·문제 해결, light/dark 테마. 발표나 온보딩 때 화면에 띄워 설명하기 좋다.

**[docs/TUNING.md](docs/TUNING.md) — 튜닝 파라미터 표 (자동 생성)**
`python -m llmwiki tuning doc` 이 코드의 튜닝 레지스트리에서 생성한다. 단계별로 키·타입·기본값·범위·영향(impact)·전체 리빌드 필요 여부·설명. 값을 바꾸는 세 가지 방법(tuning.json / `tuning set` / Web) 안내 포함. 코드가 바뀌면 다시 생성한다.

**[docs/history/2026-09-13/ANALYSIS_REPORT_0913.md](docs/history/2026-09-13/ANALYSIS_REPORT_0913.md) — 요구사항 분석·판정 리포트**
`docs/user-req.0913.,txt`(사용자 요구 원문 22항목)에 대해 항목별로 타당성·현재 코드 대비 갭·impact·진행 여부(✅/🟡/🔵/⛔)를 판정한 문서. §0 판정표, §1 당시 코드 상태 진단(규모 한계 포함), §2 고려사항(코퍼스 계약·use case 분리·5,000문서 규모), §3 요청별 상세 분석, §4 참고한 최신 동향, §5 사용자 확인이 필요했던 결정 사항 D1~D15 와 그 결론. "왜 이렇게 만들었나"의 근거.

**[docs/history/2026-09-13/IMPLEMENTATION_PLAN_0913.md](docs/history/2026-09-13/IMPLEMENTATION_PLAN_0913.md) — 구현 계획서**
분석 리포트를 바탕으로 무엇을 어떤 순서로 만들지 정한 문서(Phase 0~8, 신규/변경 파일 목록, 신규 토글·튜닝 이름, 리스크와 대응). 상단에 구현 완료 상태와 결정 사항 확정 내용을 적어 두었다. 구현 결과와 계획을 대조할 때 사용.

**[docs/history/2026-09-14/IMPLEMENTATION_PLAN_0914.md](docs/history/2026-09-14/IMPLEMENTATION_PLAN_0914.md) — 2026-09-14 구현 계획서 (요청 6항목의 판정·대안·설계·검증)**
다중 사용자 권한(6역할·7등급·admin 편집 권한 표·익명 viewer·CLI 게이트·API 키), MCP 원격/다수 LLM(Streamable HTTP·브리지), 채널별 빌드(fts/vector/graph 독립성 근거), 문서 단위 확장(doc_expand), headless/LLM 재시도와 실패 보고, 기대 결과 포렌식. 사용자 제안과 다르게 한 곳(§0.1)과 신규 설정 키 표(§8) 포함. **다른 LLM 이 bring-up 할 때 이 문서 → 아래 세 문서 순서로 읽는다.**

**[docs/SECURITY.md](docs/SECURITY.md) — 다중 사용자 서버의 로그인·역할·권한 표·API 키·파괴적 작업 보호**
왜 "관리 암호 하나"가 아니라 계정·역할·권한 표인지(검토한 대안), 역할 6단계(`viewer < class3 < class2 < class1 < builder < admin`)와 작업 등급 7단계(read/run/edit/index/rebuild/admin/destructive)의 기본 최소 역할·확인 방식, admin 이 편집하는 `permissions`(등급별 최소 역할 + 개별 작업 오버라이드), 익명(게스트) 접속, 로컬 ID/비밀번호·SSO(OIDC·프록시 헤더)·API 키 병행, CLI 권한 게이트(`--user`, `cli.default_role`), 확인 문구·비밀번호 재입력·자동 스냅샷, 감사 로그, 서버 공개 체크리스트(기본 admin `kh82.kim` 비밀번호 변경 포함).
2026-09-19 추가: **§6.2 문서 단위 접근 제어**(`docacl.json` 경로 규칙 + 문서 front matter 의 `acl:`, 높은 쪽이 적용 — 질의 근거·채널 검색·문서 열람·MCP 네 출구를 모두 막고, `security docacl check --role viewer` 로 저장 전 영향을 본다)와 **프롬프트 인젝션 방어**(`llmwiki/ctxguard.py`, 토글 `context_guard` — 문서 본문의 구획·역할·인용 흉내를 무력화해 펜스로 감싼다. 내용은 지우지 않는다).

**[docs/SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md) — 현재 구조 전체 지도 (2026-09-19) · 코드를 고치러 왔다면 여기부터**
이 시스템이 **무엇이 어디에 있고 왜 그렇게 되어 있는지** 한 편에 담은 지도. §1 설계 제약(표준 라이브러리만·파일 설정·세 창구 정합·관측 가능성)과 **채택하지 않은 것과 그 이유**(FastAPI·벡터DB·LangChain·pytest·스트리밍 — 다음 사람이 같은 제안을 다시 하지 않도록), §2 모듈 61개 지도, §3 데이터 모델(테이블 24개), §4 네 흐름(build 10 · query 27 · evolve 6 · watch 2), §5 **질의 경로 27단계 상세**(각 단계가 무엇을 하고 무엇으로 조절하나, 왜 이 순서인가), §6 LLM 역할 10개·컨텍스트 예산·앙상블·실패 처리, §7 설정 4층과 우선순위, §8 세 창구 배치 원칙, §9 동시성(요청 격리·용량 제어·락 순서·연결 풀), §10 보안 네 겹, §11 관측 다섯 가지, §13 **확장 지점**(새 채널·프로바이더·MCP 도구·문서 유형을 어디에 넣나), §14 **정합 불변식과 그것이 깨지면 어디서 잡히나**, §16 한계, §17 **이 문서의 숫자를 다시 세는 법**. 설치는 BRINGUP_GUIDE, 손잡이 사전은 OPTIMIZATION_GUIDE 로 이어진다.

**[docs/history/2026-09-19/QA_HARDENING_0919.md](docs/history/2026-09-19/QA_HARDENING_0919.md) — QA 강화: 사각지대 점검과 결함 수정 (2026-09-19)**
기존 테스트가 "기능이 동작하는가" 를 본다면, 이 점검은 네 가지 다른 질문을 던졌다 — ① 세 창구가 **같은 것을 돌려주는가**(존재하는가가 아니라) ② 모델이 **잘못 답할 때** 그것이 드러나는가 ③ 부하가 끝난 뒤 **제자리로 돌아오는가** ④ 권한 없는 문서가 **근거로 새지 않는가**. 찾은 결함 12건은 모두 "조용히 잘못되는" 것들이었다(예외도 로그도 없고 화면에는 그럴듯한 답이 보인다): 모델 입력 창을 아무도 보지 않아 프롬프트가 잘리던 것, 컨텍스트 상한에 걸리면 뒤 순위 근거가 통째로 빠지던 것, 마침표 뒤 인용을 잃어 제대로 답한 것이 깎이던 것, 없는 인용 `[C99]` 가 `citation_precision 1.0` 으로 통과하던 것, MCP 기본 경로에 구조화 결과가 없던 것, `Pipeline.query(overrides=)` 가 인자를 조용히 버리던 것, 빌린 DB 연결을 아무도 세지 않던 것, 접근 제어가 `sqlite3.Row` 예외 하나에 통째로 열리던 것. **§5 에 손으로 확인하는 TC 시나리오 표**(창구 정합 5 · RAG 품질 8 · 동시성 5 · 보안 8)가 있고, §6 이 그것을 자동 테스트 파일에 매핑한다. §8 에는 **지금 하지 않은 판단과 그 이유**를 적어 두었다.

**[docs/history/2026-09-19/CODEBASE_REVIEW_0919.md](docs/history/2026-09-19/CODEBASE_REVIEW_0919.md) — 외부 시각의 전체 코드베이스 분석 (2026-09-19)**
이 코드를 처음 보는 리뷰어가 `llmwiki/` 전체를 읽고 쓴 분석 리포트. §0 성격과 강점·약점 요약, §1 아키텍처 강점(파일·함수 지목), §2 약점 16건(심각도·**구체적 실패 시나리오**·파일:줄), §3 코드 품질(함수 길이·중복·일관성 없는 패턴 6축), §4 **테스트 사각지대**("고친 자리"가 아니라 "고친 함수"만 보는 테스트, 브라우저 없을 때 UI 하네스가 조용히 OK 로 집계되는 문제), §5 성능(1만 문서/30명에서 먼저 무너질 곳 — **부하가 질의 수가 아니라 접속자 수에 비례**하는 폴링 경로), §6 제안 P0~P2(작업량과 **하지 않았을 때의 대가**), §7 **하지 말아야 할 것 8건**(FastAPI 이전·벡터DB·SSE·캐시를 사용자별로 나누기 등 — 좋아 보이지만 이 프로젝트의 제약에서는 틀린 개선안), §8 확인 못 한 것 10건, §9 문서 드리프트. 여기서 나온 심각도 '높음' 5건은 전부 재확인 후 수정했다([QA_HARDENING_0919.md §9](docs/history/2026-09-19/QA_HARDENING_0919.md)).

**[docs/MCP.md](docs/MCP.md) — 외부 LLM(여러 개, 같은 PC/원격) 연결**
stdio(같은 PC) · Streamable HTTP(`serve` 의 `POST /mcp`, Bearer API 키, 다수 클라이언트) · 브리지(stdio 전용 클라이언트 → 원격) · 단독 HTTP 서버의 결정표와 설정 예(Windows/Linux, Claude Code/Desktop/Cursor/opencode JSON), **도구 12개**와 도구별 힌트(`annotations` 읽기/쓰기 구분), 인자 검증 메시지(붙는 LLM 이 실패 이유를 읽고 스스로 고친다), 프로토콜 버전 협상, **`mcp --doctor` 자가 점검**(bring-up 연결 확인 단계), 보안(키는 클라이언트마다 따로 — 동시성 제한이 키 단위), 429/503 거부의 뜻, **다른 RAG 를 붙이는 세 가지 방법**(검색 채널 융합 · 도구 중계 · 색인), 플러그인 도구 작성법, 종단 검증(`verify_mcp.py`), 문제 해결.

**[docs/OPTIMIZATION_GUIDE.md](docs/OPTIMIZATION_GUIDE.md) — 손잡이 지도: 어떤 토글·튜닝·설정이 어느 단계에 어떻게 작용하는가 (자동 생성)**
`python -m llmwiki arch doc` 이 코드의 구조 레지스트리(`llmwiki/architecture.py`)와 튜닝 레지스트리(`llmwiki/tuning.py`)에서 생성하므로 단계나 설정이 바뀌면 문서도 바뀐다. §0 세 가지 렌즈(품질·속도·토큰)와 렌즈별 손잡이 우선순위, §1 설정이 사는 곳과 적용 시점(config/tuning/presets/server.json), §2 읽는 법, §3 전체 구조(query·build·evolve·watch 흐름의 단계 나열), §4~§7 흐름별 단계 표(trace 이름 ↔ 토글 ↔ 튜닝 키 ↔ config 키 ↔ 품질/속도/토큰 영향), §9 LLM 에게 최적화를 묻는 법.
최적화를 물을 때는 이 문서만 주지 말고 **`python -m llmwiki optimize last --out bundle.md`** 가 만드는 묶음을 준다 — A 지금 설정 스냅샷 · B 질의 한 건의 단계별 실측(분석 리포트) · C 요청 지시문 · D 이 손잡이 지도가 한 파일에 담긴다. Web 에서는 Ask › 📊 상세 분석 리포트 › **📦 최적화 자료 묶음 다운로드**(또는 묶음 복사), API 로는 `GET /api/optimize/bundle?request_id=<id>&focus=quality|speed|tokens`, 지도만 보려면 `GET /api/optimize/guide`.

**[docs/FORENSIC.md](docs/FORENSIC.md) — 포렌식 디버깅 (자동 + 기대 결과)**
자동 포렌식 확인 방법과, 사용자가 "이 문서/용어가 답에 있어야 했다"고 알려주면 같은 설정으로 검색을 재실행해 fts/vector/graph → 융합 → 리랭크 → 컨텍스트 → 답변 중 어느 단계에서 탈락했는지와 수정안(규칙/pin/튜닝/코퍼스)을 내는 `forensic expect` 의 동작·출력 예·해석 가이드·LLM 실패 보고와의 구분. 자주 헷갈리는 **"원 판정 sufficient 인데 기대 문서는 미해결"** 조합의 뜻과 할 일, 목표가 수십 개일 때 화면이 접는 기준(`forensic_targets_shown`), 수정안을 confidence 순으로 보여 주고 약한 것은 접는 기준(`forensic_suggestion_min_confidence`).

**[docs/ANALYSIS_MODE.md](docs/ANALYSIS_MODE.md) — 상세 분석 모드 (2026-09-15)**
토글 `analysis_mode`(또는 `query --analyze`)로 질의 한 건의 모든 단계 결과·설정 스냅샷·채널/융합/리랭크/컨텍스트 상세·답변 판정·**품질/속도/토큰 세 렌즈의 소견과 조절점(토글·튜닝 키=현재값)**·자동 포렌식·프롬프트 샘플을 `logs/analysis/req_<id>.md` 한 장으로 남긴다. LLM 에게 그대로 첨부해 튜닝을 묻는 절차, 렌즈 규칙 표, `analyze` CLI · Web 📊 · MCP `wiki_analysis` · `GET /api/analysis`.

**[docs/RERUN.md](docs/RERUN.md) — 단계 재실행 (2026-09-17)**
질의 한 건의 시간은 `answer_llm` 61% · `claim_check` 25% 처럼 뒤쪽에 쏠린다. 프롬프트나 튜닝 하나를 바꿔 볼 때마다 검색부터 전부 다시 도는 낭비를 없애고, **무엇 때문에 답이 바뀌었는지**를 분리해서 보기 위해, 저장해 둔 중간 결과로 **고른 단계부터만** 다시 돈다(앞 단계는 재생). 워터폴 각 줄의 **⟲**, `POST /api/query/rerun`, `python -m llmwiki rerun <id> --from <단계>`. 재시작점 9종 표, 무엇을 저장하고 무엇을 저장하지 않는지(청크 본문은 색인에서 다시 읽는다 → 파일 80~90KB), 색인이 바뀌면 거부하는 이유, 설정 4개(`rerun_capture`·`rerun_dir`·`rerun_keep`·`rerun_max_mb`), 그리고 **재실행이 하지 않는 것**(캐시로 답하지 않음 · fallback 루프를 돌지 않음).

**[docs/RAG_FEDERATION.md](docs/RAG_FEDERATION.md) — 다른 RAG 연동과 MCP 확장 (2026-09-15)**
`mcp_sources.json` 한 파일로 다른 팀의 LLM Wiki(http)·MCP 를 제공하는 사내 RAG·REST 검색 API(rest)·stdio MCP 서버를 붙이는 방법. 외부 결과를 검색 채널 `ext_<source>` 로 융합하는 `external_rag`, 외부 도구를 우리 `/mcp` 에 `<source>__<tool>` 로 노출하는 `mcp_federation`(재귀 방지 포함), 코드 수정 없이 도구를 늘리는 플러그인 폴더 `plugins/mcp_tools/`. 결정표·설정 필드 표·동작 상세·절차·검증·문제 해결.

**[docs/ACTIVITY_DETAIL.md](docs/ACTIVITY_DETAIL.md) — 진행 중 작업 목록의 한 줄을 눌렀을 때 (2026-09-17)**
실행 중이면 지금 어느 단계인지·진행 기록을 실시간으로, 끝났으면 **그때 저장한 답변**을 그 자리에서 보여 준다. ↩ Ask 화면 복원(다시 실행하지 않는다) · 📄 요청 프로파일 · ⟲ 다시 실행. 설계에서 정한 것(제자리 요약 + 한 번 더 눌러 깊이, 복원은 '내 지난 요청' 과 같은 함수 재사용, 줄↔작업 연결은 `data-token`)과 한계(진행 기록은 메모리).

**[docs/history/2026-09-17/QUALITY_REVIEW_0917.md](docs/history/2026-09-17/QUALITY_REVIEW_0917.md) — 쿼리·셀프이볼브·리빌드 품질 재검토 (2026-09-17)**
RAG 위키의 본래 목적 세 축을 실제 색인(문서 352·청크 16,894)에서 **측정으로** 점검한 회차. 가장 큰 발견은 튜닝이 아니라 **코퍼스 위생**이었다 — 이 도구 자신의 소스가 색인돼 도메인 문서를 밀어내고 있었고, 제외하니 hit@k 0.64 → 0.88 · MRR 0.396 → 0.676. 그에 맞춰 `corpus_exclude` 설정과 `health` 경고를 넣었다. 자가진화가 날짜를 동의어로 제안하던 결함(`nvidia → 2026`)을 고쳤고, 융합 설정(`rrf_k`)이 레버라는 **가설은 측정으로 기각**했으며(리랭크가 최종 순위를 정한다), 증분 빌드가 전체 빌드와 어긋나지 않음을 확인했다.

**[docs/history/2026-09-17/VERIFICATION_0917.md](docs/history/2026-09-17/VERIFICATION_0917.md) — 2026-09-17 전면 재검토 보고서**
코드·문서 전수 재검토 회차. 문서↔코드 정합을 **기계로** 확인하는 `verify_docs.py`, 모든 하네스를 한 번에 돌려 숫자를 한 표로 모으는 `verify_all.py`, 실패 경로를 일부러 일으키는 `verify_timeouts.py` 를 새로 만들고, 그 과정에서 찾은 결함들(질의 캐시가 재실행을 가로채던 것 · `mcp_sources.json` 오타가 엉뚱한 오류로 나타나던 것 · **그래프 채널이 비어 있는 채로 몇 주간 방치되던 것**)을 고쳤다.

**[docs/history/2026-09-16/VERIFICATION_0916_2.md](docs/history/2026-09-16/VERIFICATION_0916_2.md) — 2026-09-16(2차) 검증 보고서 (요청 이력 · 모델 화면 · 답변 페르소나 · 규칙 확장 · headless 내성 · 협업)**
§0 요약표(단위 181 · 스트레스 9 · CLI 222 · Web 285 · MCP 98 · 버튼 90 · 몽키), §1 요청별로 **무엇을 왜 그렇게 고쳤나**(각 항목에 "예전에는 무엇이 잘못됐나"), §2 검증 상세와 **이 회차에 찾아 고친 결함 2건** — ① **협업 폴링이 읽기 슬롯을 잡아 질의를 밀어내던 것**(같은 시드 멍키 비교로 폭격 중 정상 질의 0/22 → 4/24, 30명 동시 질의 1.1s → 0.7s), ② `argv` 의 NUL 문자가 예외로 새던 것, §3 다른 환경에서 다시 돌리는 순서, §4 바뀐 파일.

**[docs/history/2026-09-16/IMPLEMENTATION_PLAN_0916.md](docs/history/2026-09-16/IMPLEMENTATION_PLAN_0916.md) — 2026-09-16(2차) 구현 계획**
요청 이력·모델 화면·답변 페르소나·규칙 확장성·headless 무응답·협업(채팅/게시판)을 **왜 그렇게 만들기로 했는가** — 검토한 대안과 버린 이유 포함. 부수 기능이 본체를 망가뜨리지 않게 지킨 네 가지 원칙.

**[docs/history/2026-09-16/VERIFICATION_0916.md](docs/history/2026-09-16/VERIFICATION_0916.md) — 2026-09-16 검증 보고서 (MCP 종단 검증 완주 · 포렌식 화면 정리)**
§0 요약표(단위 163 · CLI 222 · Web 259 · **MCP 종단 98** · UI 배선 · 브라우저 · 버튼 · 몽키), §1 이 회차에 찾아 고친 결함 — 제품 2건(**stdio `Content-Length` 가 한글 본문에서 다음 메시지를 삼키던 것**, `method` 없는 본문을 조용히 버려 클라이언트가 멈추던 것)과 하네스 7건(블로킹 readline, Windows 개행 변환, latin-1 헤더, `mode=auto` 루프백에서 인증이 꺼지는 것, 키 발급 순서, 키 하나로 동시성 흉내, doctor 기대의 모순), §2 구간별 확인 항목, §3 **기대 결과 포렌식 화면 개선**(“판정은 sufficient 인데 기대 문서는 미해결” 안내, 목표·수정안 정렬과 접기), §4 다른 환경에서 다시 돌리는 순서.

**[docs/history/2026-09-15/VERIFICATION_0915.md](docs/history/2026-09-15/VERIFICATION_0915.md) — 2026-09-15 전 기능 검증 보고서**
Web UI 와 CLI 가 제공하는 모든 기능을 하나씩 실행해 얻은 결과: 단위 테스트 87, CLI 184 명령(격리 임시 환경), Web 210 요청(게스트/viewer/class1/admin/API 키/MCP 9도구/CSRF), UI 배선 정적 검사, Edge headless 렌더. 검증 중 고친 결함 2건(잘못된 API 키가 게스트로 강등되던 문제, `mcp --client-config` 경로 이스케이프), 요청 6항목 ↔ 검증 매핑, 다른 환경에서 재실행하는 법(`tools/verify/`).

**[docs/REQUEST_HISTORY.md](docs/REQUEST_HISTORY.md) — 지난 요청 목록과 "그때 그 답" 다시 보기 (2026-09-16)**
Ask 탭의 **🕘 내 지난 요청**(대기·진행 중·완료를 한 목록에서, 한 줄을 누르면 질의를 다시 돌리지 않고 그때의 답변·근거·판정을 그대로 재현), 결과 원본을 DB 밖 파일로 남기는 이유와 위치(`requests_dir` = `data/requests/<yyyy-mm>/req_<id>.json`)·보존 기간(`requests_keep_days`)·정리 명령(`maintenance prune_requests`), `requests` 테이블의 `user` 열과 이관, 남의 요청까지 보는 권한(`requests all`), API 와 증상별 문제 해결.

**[docs/COLLAB.md](docs/COLLAB.md) — 휘발성 채팅과 게시판 (2026-09-16)**
왼쪽 사이드바 최상단에 고정되는 **휘발성 채팅**(서버 메모리, `retain_min` 뒤 사라짐)과 `/게시 제목` 으로만 남는 **게시판**(`data/collab/board.json`), 게시할 때 **내 최근 작업(요청)을 골라 연결**하는 흐름, 접속자 **캐릭터**(드래그로 이동, 머문 시간에 따라 말풍선 글씨가 커짐 — 시작 크기·증가 주기는 관리자 설정), `server.json` 의 `collab` 절 전체와 권한 표, **본체에 영향을 주지 않게 만든 방법**(토글 하나로 off, 저장 분리, 폴링 자동 중단, 클릭 가로채지 않음).

**[docs/WEB_UI.md](docs/WEB_UI.md) — Web UI 사용 설명서 (2026-09-16)**
화면을 실제로 쓰는 사람과 "이 화면이 원래 이렇게 동작하는 게 맞나"를 확인하는 엔지니어용. §1 헤더 활동 표시기(작업 하나 = 막대 하나, 내 요청은 초록), §2 탭 고정과 1·2·3·4열 분할 보기(패널별 넓게·접기·이동·새로고침), §3 진행 중 작업 보드와 용량 게이지·중지, §4 **계정별 Web UI 프로파일**(저장되는 항목·서버 설정과의 분리·API), §5 상세 분석 리포트 다운로드와 **LLM 소견 받기**, §6 답변 반복 루프 자동 차단과 캐시 정리, §7 테마(기본 Light), §8 **viewer 화면으로 보는 법**, §9 증상별 자가 점검과 클릭 검증 도구.

**[docs/CONCURRENCY.md](docs/CONCURRENCY.md) — 다중 사용자 동시성·요청 관리·취소 (2026-09-15)**
30명이 함께 쓰는 서버의 운영 문서. §0 한 장 요약표, 예전 전역 락 구조의 문제와 지금 구조(요청 격리 · 읽기/쓰기 락 · 동시 실행 슬롯 · 대기열), `server.json` 키 전부와 30명 기준 권장값, 거절 응답(429/503)의 의미와 대응, 진행률·경과 시간·ETA 표시와 취소(Web·CLI·다른 프로세스 작업까지), 누가 무엇을 보고 제어할 수 있는지, IP/사용자 차단·점검 모드, 역할별 LLM 타임아웃·재시도·백오프·회로 차단과 LLM 이 최종 실패해도 답을 내는 대체 경로, SQLite 동시성(WAL·연결 풀·`database is locked` 대처), 증상별 문제 해결표, 검증 명령.

**[docs/REQUEST_LEDGER.md](docs/REQUEST_LEDGER.md) — 서버로의 **모든 요청**을 한 곳에서: 동시 질의 · 한도 · 관측 · 포팅 (2026-09-24)**
**다른 환경으로 옮기는 사람(또는 LLM)이 이 문서 하나만 읽으면 되게** 쓴 문서. §1 무엇이 문제였나 — 질의가 커밋 없는 INSERT 하나로 **자기 수명 내내 SQLite 쓰기 잠금을 쥐고 있었다**는 것(실측 재현 포함)과 요청이 기록 없이 사라지는 경로 9가지, §2 **슬롯 수는 LLM 엔드포인트가 정한다**(질의 시간의 75~97%가 LLM 대기 · 용량 계산법 · 포화점 측정), §3~8 변경별 설정 키와 기본값(DB 잠금 수정 · 종류별 한도 · **요청 원장** · 통합 화면 · 비동기 질의 · 질의 로그 통합), §9 옮기는 절차와 설정을 바꾸는 세 경로, §10 검증 명령과 합격 기준, §11 증상별 문제 해결, §12 **변경별 되돌리기**, **§13 이 일을 다른 LLM 에게 시킬 때 그대로 붙여 넣는 프롬프트**(파악 → 적용 → 검증 3단계 + LLM 이 자주 틀리는 곳). 재현 결과: 동시 질의 24건 중 **12건(50%)이 예전 구조에서는 기록 없이 사라졌다** — 원장은 24건 전부를 잡았다. 설계 근거와 기각한 대안은 [IMPLEMENTATION_PLAN_0923.md](docs/history/2026-09-23/IMPLEMENTATION_PLAN_0923.md).

**[docs/history/2026-09-19/DEEP_REVIEW_0919.md](docs/history/2026-09-19/DEEP_REVIEW_0919.md) — 심층 리뷰: 품질·속도·토큰·프로파일·디버깅·다중 사용자 안정성 (2026-09-19)**
실측에 근거한 개선 보고서. 발견 10건과 이번에 고친 것·남은 것을 한 표로, 그리고 축마다 측정값과 출처. **검색 경로는 22 ms 인데 실 LLM 을 붙이면 25.8초 중 LLM 이 98%** 라는 것, `claim_check` 가 그중 46% 를 쓴다는 것, 토큰은 입력:출력이 1,526:36 이라 출력 상한보다 컨텍스트를 줄이는 쪽이 효과가 크다는 것. 안정성은 soak 4,863건 전부 200·5xx 0건이지만 **멍키가 거짓 OK 를 낸다**는 것과 문서의 청크 수가 세 값이라는 신뢰성 문제까지. 마지막에 우선순위 6가지.

**[docs/EVAL_TRIAL.md](docs/EVAL_TRIAL.md) — 품질 루프를 **믿을 수 있게** 돌리는 법 (평가 · trial 비교 · 포렌식, 2026-09-19)**
2026-09-20 추가: trial 의 **문항 원천**을 고를 수 있다 — 평가셋(고정 25문항, 정답 있음) 또는 **실제 질의 이력**(`trial run --source queries --days 7 --only negative`). 이력에는 정답이 없어 `hit@k`·`mrr`·`term_recall` 은 계산되지 않고, 화면·markdown 이 *"계산할 수 없음 — 0점이 아닙니다"* 라고 못 박는다(빈칸을 0점으로 읽으면 "이력으로 돌렸더니 품질이 폭락했다" 는 잘못된 결론이 난다). 그리고 비교가 **단계별 표**를 함께 낸다 — 질의 1건당 단계 ms·토큰·호출수를 나란히 놓고 **달라진 단계를 앞에** 둔다. 이 저장소 실측에서 `claim_check` 가 질의당 14.0초·41,869토큰으로 **답변 생성과 맞먹는 비용**임이 최종 지표가 아니라 이 표에서 드러났다.

점수보다 **신뢰도가 먼저**다. `eval --check` 가 평가셋이 코퍼스에 섞여 들어갔는지(질문 파일이 색인돼 정답 문서를 밀어내는 사고 — 실제로 이 저장소에서 `hit@k 0.6` 의 진짜 원인이었다)·기대 문서가 색인에 있는지·문항 수가 충분한지를 먼저 본다. `eval --retrieval-only` 는 LLM 단계를 전부 꺼 **토큰 0**으로 검색 지표만 내고(검색 튜닝은 이걸로 반복한다), 질의 임베딩 캐시(`embed_query_cache`)가 반복 실행에서 `vector_search` 를 **8.4초 → 0.13초**로 줄인다. `eval --forensic` 은 놓친 문항마다 평가셋의 기대값을 그대로 써서 **어느 단계에서 탈락했는지와 수정안**까지 붙인다. trial 비교는 값이 높은 쪽을 그냥 "최선" 이라 하지 않고 **부호 검정**으로 "한 문항 뒤집힌 것"과 진짜 차이를 가르며, 품질과 함께 **토큰·p95 의 대가**를 말한다. §3 변별력(전 문항 같은 값인 지표는 "안 움직이는 눈금"), §7 지표 읽는 법.

**[docs/EVOLVE.md](docs/EVOLVE.md) — 자가진화: 제안 → 사람 승인 → 적용 → 회귀 평가 → 롤백 (2026-09-19)**
제안이 어디서 생기고(질의 캡처 · 피드백 · LLM 리뷰) 어떤 종류가 있으며(`evolve.KINDS` 10종, 리빌드가 따르는 것 표시) 어떻게 적용되는지. **§1.5 제안 설명** — 제안을 payload JSON 원문 대신 "무엇이 · 어느 파일에서 · 어떻게 바뀌고(before/after diff) · 리빌드가 드나 · 되돌리는 법"으로 보여 주고, 값을 점검해 *승인해도 실패하거나 조용히 무효인* 제안을 `[X]` 로 가른다(운영 DB 대기 124건 중 43건이 그랬다). `error` 급 점검이 붙으면 회귀평가 전에 적용을 막고, 이름 꼬리 공백은 적용할 때 지운다. **자동 적용의 안전장치 세 겹**(신뢰도 하한 · 종류 제한 `evolve_auto_apply_kinds` · 건수 상한)과 회귀 평가·롤백, 적용마다 쌓이는 스냅샷을 `evolve_snapshot_keep` 로 관리하는 법, CLI·Web·MCP 세 창구 대응표(적용·거절을 MCP 에 두지 않는 이유), 스케줄러 연동, 설정 키, 증상별 문제 해결.

**[docs/SCHEDULER.md](docs/SCHEDULER.md) — 서버 스케줄러 (2026-09-15)**
`schedule.json` 하나로 정해진 시각·주기에 작업을 돌린다. 시점 지정 3가지(`every` · `at`+`days` · 5필드 `cron`), 동작 19종(증분/전체 빌드, URL 수집, 파이썬 스크립트, 임의 CLI 명령, 질의, LLM·headless 호출, MCP 수집, 유지보수, HTTP 호출, **evolve · memory · precompute · eval · trial · snapshot · wiki · forensic · embed_report**)의 JSON 예시, 동시성·취소와의 관계, Web(설정 › 스케줄)·CLI(`schedule list|add|run|enable`) 조작, 실행 이력과 상태 파일, 서버를 상시 띄우지 않는 환경에서 OS 스케줄러로 같은 동작을 부르는 법, 문제 해결, 검증.

**[docs/history/2026-09-15/IMPLEMENTATION_PLAN_0915.md](docs/history/2026-09-15/IMPLEMENTATION_PLAN_0915.md) — 2026-09-15 구현 계획서 (다중 사용자 서버화의 판정·대안·설계·검증)**
요청 8항목(병렬 처리 · 역할별 LLM 정책 · 30명 동시 사용과 관리자 제어 · 진행률과 취소 · 스케줄러 · 모델 목록 · 로그인 뒤로가기 버그 · DEBUG 검증)에 대한 판정표, 선택한 설계와 **택하지 않은 대안과 그 이유**(멀티프로세스 · PostgreSQL · asyncio 재작성), 호환성이 바뀐 지점(요청 단위 프리셋 미리보기), 멍키 테스트와 스트레스 테스트로 찾은 서버 결함 10건의 원인과 수정, 터미널 한글 깨짐의 원인과 해법, 실제 10MB 코퍼스 구성과 무손실 변환, Web UI 사용성 항목별 구현, 신규 파일·설정 키 목록, 남은 개선 여지.

**[docs/CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md) — 사용자가 고치는 모든 파일과 키 (자동 생성)**
설정 파일 18종의 지도(무엇을 정하나 · 어디서 바꾸나 · **언제 반영되나** · 원본 예시)와 `config.json` 키 103개, 역할별 LLM(`llm_roles`, 앙상블 포함), 토글 66개(묶음별 · 켰을 때의 품질/속도/토큰 영향), `tuning.json` 상수 141개(단계별 · 범위 · **리빌드 필요 여부**)를 한 문서에. 마지막 두 절이 실무에서 가장 자주 쓰인다 — "값을 바꾼 뒤 무엇을 해야 하나"(reload / `build --full` / `build graph`) 와 "자주 하는 변경 — 어디를 고치나". `python -m llmwiki config doc` 이 코드 레지스트리에서 생성하므로 코드가 바뀌면 문서도 따라온다.

**[docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md) — 버전별 변경 요약 (현재 3.1.0)**
맨 위 절이 현재 버전. 회차마다 "무엇이 바뀌었나(표) → 운영자가 할 일(새 키를 파일에 명시하는 `config fill-defaults --all` 등) → 상세 문서" 순서. `python -m llmwiki --version` · `/api/status.version` · MCP `serverInfo.version` 이 모두 이 값을 낸다. 버전을 올리는 절차 포함.

**[docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) — 무엇을 고쳤을 때 무엇을 돌리나**
변경 영역(모듈) → 단위 테스트 · 하네스 · 함께 볼 문서 표, 세 단계 규칙(저장마다 / 기능 완료 / 회차 마감), 새 기능을 넣을 때 반드시 늘려야 하는 것(정렬표 CAPS · MCP 도구 집합 테스트 · 설정 키의 파일 명시 · verify_settings_sync), 실패 메시지를 읽는 법.

**[docs/SWEEP.md](docs/SWEEP.md) — 파라미터 스윕 (저장된 질의 위에서 값 하나만 바꿔 N회 비교, 2026-09-18)**
지난 질의를 기준으로 키 하나(`rrf_k` · 토글 `rerank` · `top_k_final` · `answer_model` …)의 값을 `start:stop:step` 범위나 목록으로 바꿔 가며, 값마다 그 키가 영향을 주는 단계부터만 `rerun` 재생으로 다시 돌린다 — 앞 단계는 저장값이라 차이는 그 값의 효과로 분리된다. 첫 값이 기준이 되어 최종 순위·컨텍스트 id 집합·답변 텍스트·groundedness/ms/인용/토큰 Δ 를 단계×값 격자로 비교한다. 설정 `sweep_dir`/`sweep_keep`/`sweep_max_values`/`sweep_max_parallel`, CLI `sweep run|list|show|compare|keys`, `GET/POST /api/sweep`, MCP `wiki_sweep`, 🧭 Pipeline 페이지의 스윕 폼, 스윕 불가 키의 이유와 문제 해결 표.

**[docs/GRAPH_PROFILE.md](docs/GRAPH_PROFILE.md) — 지식 그래프 진단 프로파일 (2026-09-18 · 2026-09-24 소견)**
2026-09-24: 지표 위에 **소견 13종**(증거 → 원인 → 처방 → 확인)이 온다. 처방은 문장이 아니라 rules.json 에 붙여 넣을 **조각**, 고칠 **문서 목록**, 튜닝 키, 명령이다. 실데이터에서 허브 1~5위가 짧은 별칭의 단어 내부 오탐(`corpus_d[ir]s` 의 IR본부 degree 7,748)이었음을 소견이 문맥과 함께 냈고, 매처가 단어 경계·대소문자 규칙(`rules.json` `matching`)을 얻었다. 같은 회차에 그래프 탭이 용어를 설명하고(상위 N · 무리 · 관계 출처 · 연결/이웃) 보기 모드 4종과 무리 상세(세 창구)를 얻었다 — [WEB_UI.md](docs/WEB_UI.md) §0.69 · 설계 [GRAPH_KNOWLEDGE_PLAN_0924.md](docs/history/2026-09-24/GRAPH_KNOWLEDGE_PLAN_0924.md).
"규칙(`data/rules.json`)을 바꾸면 그래프가 어떻게 달라지는가" 를 숫자로 보는 도구. 규모·연결성(성분·고립·허브 경고)·문서 커버리지·품질 신호(중복 후보·끊긴 관계·cooccur 비중)·규칙 기여(죽은 규칙/사전)·질의 활용(시드 비율·엔티티 후보 키워드) 6절과, 각각 어느 파일·키를 고칠지 가리키는 규칙 기반 제안 12종, 실행 이력(`data/graph_profiles/`)과 `--compare` 핵심 지표 Δ, 옵션 `--eval`(graph 채널만 hit@k). 설정 `graph_profile_keep`/`graph_profile_requests`/`graph_profile_hubs`, CLI `graph profile`, `GET /api/graph/profile[/history]`, Knowledge › 그래프 진단 탭, MCP `wiki_graph_profile`.

**[docs/QUERY_RULES.md](docs/QUERY_RULES.md) — 규칙 기반 질의 확장 사전과 방향 (2026-09-18)**
`query_rules.json` 의 **9개 유형**이 왜 각각 그 방향인지, 확장이 FTS/벡터/보조 리스트/NOT 에 어떻게 들어가는지(`rules test` 출력 필드), 규칙 접기 `query_rules_max_rounds` 와 related 를 양방향으로 바꾸는 튜닝 `related_symmetric`(기본 false). 2026-09-19 추가: **§1.1 유형 레지스트리**(새 유형은 `register_type()` 한 번 — 예전에는 파일 15개를 고쳐야 했고 한 자리만 빠뜨리면 그 유형이 조용히 무시됐다)와 새 유형 셋 — **`context`**(같은 약어가 팀마다 다른 뜻일 때 문맥이 맞을 때만 넓히고, 안 맞으면 아예 발화하지 않는다), **`hypernym`**(분류 체계 — 내려갈 때와 올라갈 때 가중이 다르다. 상위어 문서는 대개 일반론이므로), **`unit`**(`4KB` ⇄ `4096 byte`. 키가 숫자와 함께 와야 뜻이 생겨 사전 용어로 못 적으므로 질의를 직접 훑는 첫 유형). "이 말은 어떻게 퍼지나" 를 보여 주는 `rules explain <용어>` / 유형 표 `rules types` / `GET /api/query_rules/explain?term=` / Settings › 질의 규칙 사전 / MCP `wiki_rules(action=types|explain|test)` 의 결과 필드 해석, `rules lint` 항목, 문제 해결.

**[docs/BUILD_UNDER_LOAD.md](docs/BUILD_UNDER_LOAD.md) — 사람들이 쓰는 중에 빌드해도 되나 (30명 실측, 2026-09-19)**
30명이 계속 질의하는 동안 빌드를 걸고 **빌드가 도는 구간에 시작된 질의만** 추려 쟀다. **증분 빌드와 채널 빌드(`fts`/`vector`/`graph`)는 그냥 돌려도 된다** — `graph` 채널 빌드는 7.7초나 걸리는데 그 사이 1,438건이 평소 속도(p50 73ms)로 처리됐다. **전체 리빌드만 다르다**: 5.7초 동안 질의 54건, p95 **5.5초** — *빌드 시간 = 모든 사용자의 대기 시간*이다(실패·거절은 0). 그리고 이 측정이 결함 하나를 드러냈다 — 문서가 권하던 `reads_during_build=always` 가 **정확히 그 상황에서 무효**였다(완화 분기가 `weight == "write"` 만 봤는데 리빌드는 `"exclusive"` 로 들어온다). 고친 뒤 같은 조건에서 빌드 중 질의가 **54건 → 893건**, p95 **5,548ms → 376ms**. 권장 운영(증분은 아무 때나 · 채널 빌드로 대체 · 전체 리빌드는 야간 · 불가피하면 `always`)과 건드리면 안 되는 값(`read_wait_timeout_s` 는 빌드가 아니라 *질의의 수명*)도 여기.

**[docs/LLM_CONNECT.md](docs/LLM_CONNECT.md) — LLM 에 닿는 두 방법과 `config.json` 샘플 (2026-09-19)**
**(A) LLM API**(OpenAI/Anthropic 호환 — 사내 게이트웨이 + PAT 포함)와 **(B) headless 에이전트**(`opencode` 를 비대화형 자식 프로세스로) 각각의 **바꾸는 키만** 추린 샘플 다섯 개 — 게이트웨이 + PAT, Anthropic 호환, 로컬 Ollama, opencode 전부, **역할별 혼합**(질의 역할만 headless·빌드 역할은 API — 권장). 전환은 `llm_provider`·`llm_model` **두 줄**이고 코드는 고치지 않는다. 그것을 말이 아니라 하네스로 지킨다 — `tools/verify/verify_llm_switch.py` 가 API 모드에서 **CLI·Web·MCP 세 창구**를 모두 돌리고, `config.json` 두 키만 바꿔 headless 로 전환한 뒤(다른 설정 파일과 `llmwiki/*.py` 의 수정 시각이 그대로인지 확인), 같은 세 창구가 그대로 동작하며 셋 다 headless 를 보고하는지, 역할 하나만 바꾸는 것도 되는지, 되돌려지는지까지 검사 18개로 확인한다(`verify_all` 에 포함). 세 창구 대응표와 주의점(`auto` 는 게이트웨이·headless 를 고르지 않는다 · headless 로 빌드하면 청크마다 프로세스가 뜬다 · 임베딩은 별개다)도 여기.

**[docs/PORTING.md](docs/PORTING.md) — 다른 환경에 올릴 때 건드려야 하는 모든 연결 정보 (2026-09-19)**
`config`·`tuning`·규칙 말고도 **환경에 묶인 것**이 어디에 있는지 한 장에 모았다 — LLM(역할별 10개, 게이트웨이 주소·인증 헤더 이름·추가 헤더·headless `agents.json`·카탈로그·앙상블), 임베딩·리랭크, MCP 양방향(이쪽이 서버일 때 / 남의 RAG 를 붙일 때 `mcp_sources.json`), 웹 바인드와 로그인·SSO·문서 접근 제어·동시성, 코퍼스·색인 경로, 지식 규칙·프롬프트, 그리고 `LLMWIKI_*` 덮어쓰기. 자격증명은 **`.env` 에만** 두고 `config.json` 에는 주소와 헤더 이름까지만 둔다는 규칙도 여기. **"설정을 한 폴더에서 관리하는 게 낫나"** 에 대한 답과 그 구현: 파일을 옮기면 기존 설치·문서·예시 경로가 전부 깨지므로 **옮기지 않고 가리킨다** — `config bundle --out conf` 로 설정 17종을 모으고 `LLMWIKI_CONF_DIR=conf` 로 그 폴더를 먼저 보게 한다(안 쓰면 동작 동일, 일부만 넣어도 되고, 개별 `LLMWIKI_<NAME>_PATH` 가 더 세다). 옮길 때는 그 폴더 하나와 `corpus/` 만 들고 가면 된다.

**[docs/RESET.md](docs/RESET.md) — 관리자 초기화 세 가지: 데이터·빌드 / 설정 / 로그·이력 (2026-09-19)**
다른 환경으로 옮기거나 코퍼스를 통째로 바꿀 때 앞 환경의 흔적을 턴다. 지우는 명령이 **먼저 보여 주고 나중에 지운다** — CLI 기본이 미리보기이고 `--apply` 가 있어야 실행되며, Web 버튼도 "지우는 목록 · 유지하는 목록" 표를 띄운 뒤 한 번 더 눌러야 확인 문구 + 비밀번호 모달로 넘어간다. 범위마다 **지우지 않는 것**을 명시한다 — `corpus/` 원본은 절대, `security.json`(계정)과 `.env`(키·PAT)는 옵션으로 켜야, `data` 초기화는 질의 로그를 남기고 `logs` 초기화는 색인을 남긴다. 어느 명령도 건드리지 않아 폴더를 복사할 때마다 따라오던 `data/{requests,reruns,sweeps,graph_profiles}`(이 저장소에서 실측 620개·66MB)와 `logs/`(107MB)가 비로소 정리된다. `data` 범위는 지우기 전에 자동 스냅샷을 만든다.

**[docs/GRAPH_RULES.md](docs/GRAPH_RULES.md) — 문서에서 그래프를 만드는 규칙 `data/rules.json` (2026-09-19)**
질의를 넓히는 `query_rules.json` 과 **다른 파일, 다른 시점**이다 — 이쪽은 **빌드할 때** 노드와 변을 만든다. 절마다 무엇을 정하는지(`entities` · `id_patterns` · `link_rules` · `relation_patterns` · `chunk_values` · `schema` · `types_for_cooccur` · `explicit_rels`), **값 종류 레지스트리**(`relation_patterns[*].value` — `entity`/`id`/`date`/`money`/`percent`/`text` 에 더해 `measure`(`4 ns`·`1.5 dB` → `metric` 노드) 와 `version`(`rev B1`)을 추가했고, 새 종류는 `register_value_type()` 한 번이면 CLI·Web·MCP 설명에 함께 나타난다), 청크마다 남길 스칼라를 파일로 켜고 끄는 `chunk_values`, 그리고 **`schema` 절**(타입·관계 어휘 — `uses`/`used`/`utilizes` 를 표준 이름으로 모으고, `inverse` 로 `fixes`↔`fixed_by` 를 한 번만 적으며, 어휘 밖 이름은 `on_unknown` 정책대로 처리하고 **빌드 보고서에 남긴다**. 예전에는 LLM 이 낸 `type`/`rel` 에 검증이 전혀 없었다). 빌드 전 정적 점검 `graph-rules lint`(깨진 정규식 · 없는 유형 · **가려진 `link_rules`** · 겹치는 별칭 · `inverse` 짝)와 문장 하나로 확인하는 `graph-rules test`, 증상별 문제 해결. 빌드된 *그래프* 의 진단은 [GRAPH_PROFILE.md](docs/GRAPH_PROFILE.md) 가 맡는다(정적 ↔ 동적).

**[docs/SETTINGS_SYNC.md](docs/SETTINGS_SYNC.md) — 설정 기본값 명시와 UI ↔ 파일 ↔ 유효값 정합 (2026-09-18)**
`config fill-defaults [--tuning --rules --all --examples --dry-run]` 이 config.json/tuning.json/query_rules.json/data/rules.json/setup 예시에 모든 키를 기본값으로 채우는 규칙(있는 값 유지, `_explicit_defaults` 표식), `.env` 가시성(`config env`, `GET /api/env`, 마스킹, `LLMWIKI_*` 오버라이드, `config reload --env`, Settings › config.json 탭의 .env 패널), 카탈로그 전체 연결 테스트(`models test --catalog [--live]`, `POST /api/models/test_catalog`, Settings › 카탈로그의 "전체 카탈로그 테스트"), 역할에 model 만 적었을 때의 카탈로그 provider 자동 해석과 `models test` 불일치 힌트, 그리고 표면마다 "UI 저장 → 파일 → `config show --effective`" 와 "파일 편집 → reload → UI" 를 확인하는 절차(하네스 `tools/verify/verify_settings_sync.py`).

**[docs/SURFACE_ALIGNMENT.md](docs/SURFACE_ALIGNMENT.md) — CLI · Web UI · MCP 전수 정렬 감사 (2026-09-20)**
"정렬돼 있다" 를 세 뜻으로 갈라서 본다 — **①존재**(기능마다 세 창구가 있는가) · **②전수**(코드에 있는 것이 **빠짐없이** 대조표에 있는가) · **③동작**(같은 입력에 **같은 답**을 주는가). ②가 이번에 새로 생겼고, 그것이 핵심이다: 그 전까지 하네스는 **사람이 적은 표**를 기준으로만 봤기 때문에 표에 줄이 없으면 검사 자체가 되지 않았고 — 실제로 CLI 명령 3개(`fusion`·`system`·`time`)와 Web 경로 33개가 표 밖에 있었다(전부 존재하는 기능이었고, 표가 따라가지 못한 것이다). 이제 **코드가 기준**이라 새 명령·경로·도구를 만들면 표에 줄을 더하기 전까지 하네스가 실패하고, 비워 둔 칸에 이유가 없어도 실패한다. ③은 새 하네스 `tools/verify/verify_tri_surface.py` 가 맡는다 — 단위 테스트처럼 엔진 함수를 직접 부르는 대신 **`serve` 를 띄우고 `python -m llmwiki …` 를 진짜 실행하고 같은 서버의 `POST /mcp` 를 호출해** 42건을 비교한다(인용 `[C#]`→chunk_id 매핑, 유형 필터가 실제로 거르는지, **실패도 같은지**). 감사가 찾아 고친 것: `rules explain ""` 이 Web 400·MCP 오류인데 **CLI 만 종료코드 0** 이었던 것과, 양쪽 다 `None` 이라 조용히 통과하던 비교 2개. MCP 에 일부러 두지 않은 53개의 이유 네 갈래와, 창구마다 응답 **모양**이 다른 것이 왜 정상인지도 여기.

**[docs/OPS_STATS.md](docs/OPS_STATS.md) — 운영 통계: 관리자가 실제로 묻는 것 (2026-09-20)**
옵저빌리티 › 시스템 화면은 **색인 규모**(문서·청크·엔티티 수)만 보여 줬다. 운영자가 정말 묻는 것 — 빌드가 어느 단계에서 느린가, 질의가 언제·어느 창구로 몰리나, 토큰을 어디에 쓰나, 근거를 못 찾은 질의가 얼마나 되나, 디스크가 어디서 커지나 — 에 필요한 데이터는 **이미 DB 에 다 있었다**(요청 trace·질의 로그·임베딩 실행 기록). 읽지 않고 있었을 뿐이다. 9개 절(`index` 임베딩 없는 청크 포함 · `build` 느린 단계 순위 · `queries` 시간대 분포와 창구별 · `latency` p50/p95 와 **가장 느린 질의 목록** · `tokens` 질의당·호출당 · `quality` 근거 부족률/👍👎/포렌식/대기 제안 · `users` · `storage` 테이블·폴더별 용량 + **정리 힌트** · `embed` 캐시 적중률)을 `stats --full [--days --section --top]` / `GET /api/opstats` / `wiki_status(full=true)` 세 창구가 **같은 한 곳**(`llmwiki/opstats.py`)에서 읽는다. 읽기 전용이고 기간·표본 상한이 있으며 표본 수를 함께 돌려준다(3건으로 낸 p95 를 숫자만 보고 믿지 않도록). 붙이자마자 이 저장소에서 `data/snapshots` **899MB > DB 496MB** 를 찾아냈다 — §4 에 그런 경고와 조치 표.

**[setup/INSTALL.md](setup/INSTALL.md) — 설치와 최소 설정**
setup/ 폴더의 각 파일 용도, 요구사항 표, Windows/macOS/Linux 설치 명령, 최소 설정 3단계, 실행 명령, 자기 코퍼스에 맞추는 4단계, 문제 해결 요약, 폴더 통째 이식 방법.

### 기록 문서 — [docs/history/](docs/history/README.md) (읽지 않아도 사용에는 지장 없음)

날짜 폴더 하나가 한 회차다. **그날의 사실이라 고치지 않는다** — 거기 적힌 결함·미완료·숫자는
대부분 이후 회차에서 처리됐으니, 지금 상태는 현행 문서와 [VERIFICATION.md](docs/VERIFICATION.md) 로 확인한다.

| 회차 | 무엇 |
|---|---|
| [2026-09-24 Knowledge](docs/history/2026-09-24/GRAPH_KNOWLEDGE_PLAN_0924.md) | **가장 최근** — 그래프 진단을 소견·처방으로, 그래프 탭 용어·보기 모드, 사전 매처 단어 경계 |
| [2026-09-24](docs/history/2026-09-24/CODE_REVIEW_0924.md) | 09-23 회차의 **사후 코드 리뷰와 전체 검증**. 결함 14건(정상 종료 때 원장 유실 · 테스트의 실사용 원장 오염 · 다중 프로세스 원장 줄 깨짐 · cp949 콘솔에서의 거짓 FAIL · 끊긴 클라이언트가 대기열을 30분 차지 · stderr 파이프가 막히자 서버 정지 · 위키 페이지 이름 미검증 · 새 요청 행의 IP 누락 등)과 재발 방지 규칙 11가지 |
| [2026-09-23](docs/history/2026-09-23/IMPLEMENTATION_PLAN_0923.md) | 요청 5건 — 동시 질의 DB 잠금(원인: 커밋 없는 `cache_put`) · 질의/검색 별도 한도 · **요청 원장** · 사라지는 요청 재현 · 세 창구 정합. 운영 절차는 [REQUEST_LEDGER.md](docs/REQUEST_LEDGER.md) |
| [2026-09-20](docs/history/2026-09-20/DOCS_REORG_0920.md) · [UX_FIXES_0920.md](docs/history/2026-09-20/UX_FIXES_0920.md) | 문서 재배치(현행/기록 분리) · 화면 사용성 수정(Trial 비교 · 포렌식 · 운영 통계 추세) |
| [2026-09-19](docs/history/2026-09-19/IMPLEMENTATION_PLAN_0919.md) | 요청 14항목의 조사·설계·택하지 않은 대안·검증. 심층 리뷰 3종도 같은 폴더 |
| [2026-09-18](docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918.md) · [(2차)](docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md) | 요청 23건(답변/출력 모드·앙상블·스윕·그래프 진단·Pipeline 페이지) |
| [2026-09-17](docs/history/2026-09-17/CODE_REVIEW_0917.md) | 전면 재검토 · [REBUILD_SPEC.md](docs/history/2026-09-17/REBUILD_SPEC.md)(v2 시점 재구현 사양서 — 바닥부터 다시 만들 때만) |
| [2026-09-16](docs/history/2026-09-16/IMPLEMENTATION_PLAN_0916.md) · [2026-09-15](docs/history/2026-09-15/IMPLEMENTATION_PLAN_0915.md) | 요청 이력·협업 / 다중 사용자 서버화 · [ARCHITECTURE_V3.md](docs/history/2026-09-15/ARCHITECTURE_V3.md)(v3 설계 — 지금 구조는 [SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md)) |
| [2026-09-13](docs/history/2026-09-13/ANALYSIS_REPORT_0913.md) · [2026-09-11](docs/history/2026-09-11/IMPLEMENTATION_BRIEF.md) | 최초 요구사항·분석·v2 구조. `docs/user-req.0913.,txt` 와 [requirement-0913.md](docs/history/2026-09-13/requirement-0913.md) 는 같은 원문 |
| [docs/legacy/](docs/legacy/) | 1차 구현(v1) 시점의 ARCHITECTURE · DESIGN_REVIEW · MULTI_AGENT 제안 |

---

## 1. 5분 안에 시작하기

```bat
:: Windows
cd llm-wiki-rag-selfevolving
setup\install.bat                      :: 패키지 설치 + config.json/.env 생성 + 환경 진단
python -m llmwiki health               :: 환경·프로바이더·DB·코퍼스 점검
python -m llmwiki build --full --trace :: 색인 (기본 코퍼스: setup/sample_corpus_modem — 합성 모뎀 문서 38개)
python -m llmwiki query "ISSUE-2001 의 원인과 수정 CL 은?" --trace
python -m llmwiki serve                :: Web UI → http://127.0.0.1:8765/
```
```bash
# macOS / Linux
bash setup/install.sh && python3 -m llmwiki build --full --trace && python3 -m llmwiki serve
```

자기 코퍼스로 바꾸려면 `config.json` 의 `corpus_dirs` 를 수정하고 `build --full`. 문서 형식은 [CORPUS_CONTRACT.md](docs/CORPUS_CONTRACT.md) 를 따르면 ID 노드·결정적 관계·시간 검색이 켜집니다.

### 요구사항
| 항목 | 최소 | 권장 |
|---|---|---|
| Python | 3.9 | 3.11+ (검증: 3.14) |
| 패키지 | numpy, pypdf | + anthropic, sentence-transformers, kiwipiepy, pyyaml (`setup/requirements-optional.txt`) |
| 외부 서비스 | 없음 | Anthropic · OpenAI-compatible(vLLM/LM Studio/Ollama/OpenRouter) · rerank API · headless 에이전트(opencode 등) · MCP 소스 |

---

## 2. 설정 파일 (모두 파일로 외부화, `config paths`)

| 파일 | 내용 |
|---|---|
| `config.json` | 코퍼스 경로, 프로바이더/**역할별 모델과 LLM 정책**(answer·rerank·extract·summary·review·expand·verify·forensic 마다 `timeout_s`·`retries`·`backoff`·`budget_s`·`circuit_failures` 를 따로), **59개 토글**(+`build_fts`·`doc_expand`·`llm_failure_report`), 운영 수치(배치·WAL·로그·timezone·`llm_timeout`·`llm_retries`·`llm_retry_backoff`·`db_busy_timeout_s`·`db_pool_size`), **터미널 인코딩**(`console_encoding`·`console_set_codepage`), **서버/MCP 기본값**(`web_host`·`web_port`·`mcp_transport`·`mcp_host`·`mcp_port`·`mcp_url` — `serve`/`mcp` 플래그 생략 시) — 원본 `setup/config.example.json` |
| `server.json` | **동시 사용자 제어**: 동시 읽기 수와 사용자/IP별 상한(`concurrency`)·대기열 크기와 대기 시간·빌드 중 읽기 허용 방식·질의/검색/MCP 시간 제한(`timeouts`)·분당 속도 제한(`rate_limit`)·세션 수와 유휴 시간(`sessions`)·IP 및 사용자 차단과 점검 모드(`access`)·모니터 공개 범위(`monitor`) — [docs/CONCURRENCY.md](docs/CONCURRENCY.md). 원본 `setup/server.example.json`. 환경변수 `LLMWIKI_SERVER_<섹션>_<키>` 로도 덮어쓴다 |
| `schedule.json` | **정해진 시각·주기에 돌릴 작업**: 증분/전체 빌드, URL 수집, 파이썬 스크립트, 임의 CLI 명령, 질의, LLM·headless 호출, MCP 수집, evolve·memory·precompute·eval·trial·snapshot·wiki·forensic·embed_report — [docs/SCHEDULER.md](docs/SCHEDULER.md). 원본 `setup/schedule.example.json`. 저장하면 서버가 자동으로 다시 읽는다 |
| `models.json` | **쓸 수 있는 LLM 목록**(id·provider·label·역할·태그·context·사용 여부). Web 설정의 모델 드롭다운과 `models list` 가 이 목록을 보여 준다. `models discover` 로 Ollama·OpenAI 호환 게이트웨이에서 실제 제공 모델을 가져와 추가. 원본 `setup/models.example.json` |
| `data/profiles.json` | **계정별 Web UI 설정**(테마·토글·프리셋·오버라이드·고정 탭·분할 보기). 서버 기본 설정과 분리되어 있어 한 사람이 바꿔도 남에게 영향이 없다 |
| `.env` | API 키/PAT (`OPENAI_API_KEY`·`LLM_API_KEY`, `ANTHROPIC_API_KEY`·`ANTHROPIC_AUTH_TOKEN` …) + 모든 설정의 env 오버라이드 (`LLMWIKI_<KEY>`, `LLMWIKI_TOGGLE_<NAME>`) |
| `security.json` | **로그인(로컬 ID/비밀번호 + SSO + API 키)·역할 6단계·권한 표(permissions)·익명 접속·CLI 게이트·파괴적 작업 정책** — [docs/SECURITY.md](docs/SECURITY.md). 저장소에는 admin `kh82.kim/1234qwer` 가 들어 있다(공개 전 변경). 원본 `setup/security.example.json`(users 비어 있음, 키별 설명 포함) |
| `docacl.json` | **문서 단위 접근 제어** — 어떤 역할이 어떤 문서를 **근거로** 볼 수 있나. 경로 규칙(`rules[].prefix`/`min_role`) + 문서 front matter 의 `acl:` 중 **높은 쪽**이 적용된다. 질의 근거·채널 검색·문서 열람·MCP 를 모두 막는다 — [docs/SECURITY.md §6.2](docs/SECURITY.md). 원본 `setup/docacl.example.json`. **파일이 없거나 규칙이 비면 아무도 막지 않는다**(기본) · `security docacl show|check --role <역할>` |
| `tuning.json` | 알고리즘 상수 130+ (FTS·라우터·그래프·융합·근거 판정·claim·메모리·`doc_expand_*`·`forensic_*`) — `tuning show`, Web › Settings › 튜닝, [docs/TUNING.md](docs/TUNING.md) |
| `presets.json` | **품질/속도/토큰/offline/deep_research** 묶음 — `--preset quality`, 사이드바 체크박스 |
| `query_rules.json` | **규칙 기반 질의 확장 사전**: 유형 **9개** — acronym / synonym / alias / related / exclude / compound + **context**(문맥마다 뜻이 갈리는 말) / **hypernym**(분류 체계) / **unit**(숫자+단위 동치). 유형마다 적용 방식이 다르다(`rules types` 로 표, `rules explain <용어>` 로 한 말의 경로). 유형 자체가 **레지스트리**라 새 유형은 등록 한 번으로 CLI·Web·MCP 에 모두 나타난다 — [QUERY_RULES.md](docs/QUERY_RULES.md). 원본 예시 `setup/query_rules.example.json` |
| `stopwords.json` | **불용어 목록**(질의 키워드 추출에서 제거). 없으면 코드 기본값으로 생성, 저장하면 재시작 없이 반영. 원본 `setup/stopwords.example.json` — [STOPWORDS.md](docs/STOPWORDS.md) |
| `data/rules.json` | 그래프 사전·정규식 + **ID 패턴·결정적 링크 규칙**(CL→Issue) |
| `schemas/` | 문서 유형별 스키마(schema_version), 추론 규칙, 마이그레이션 |
| `prompts/*.md` | 역할별 프롬프트 + **answer_guide.md**(Evidence-rich · Structured · Grounded 답변 가이드) |
| `pins.json` `agents.json` | 고정 근거 · headless 에이전트 명령 템플릿 + **재시도 정책**(`timeout_s` 300 · `retries` 3 · `retry_on`; 원본 `setup/agents.example.json`) |
| `mcp_sources.json` | **다른 RAG · MCP 서버 · REST 검색 API 연결** (전송 stdio/http/rest; `retrieve` 검색 채널 · `expose` 도구 페더레이션 · `ingest` 색인) — [docs/RAG_FEDERATION.md](docs/RAG_FEDERATION.md). 원본 `setup/mcp_sources.example.json` |
| `plugins/mcp_tools/*.py` | MCP 플러그인 도구 (`register(add_tool)`; 예시 `_example_echo.py`). 위치 `config.json mcp_plugins_dir` |
| `setup/mcp_clients.example.json` | (LLM Wiki 가 읽지 않음) 외부 LLM 클라이언트에 붙여 넣는 MCP 설정 블록 4종. 환경 값이 채워진 버전은 `python -m llmwiki mcp --client-config` |

2026-09-14 ~ 09-18 기능(권한·MCP·채널 빌드·doc_expand·재시도·포렌식·동시성·스케줄러·모델 목록·답변/출력 모드·앙상블·스윕·그래프 진단·로그 총량·불용어)의 **파일·키·기본값·예시·확인 명령 총람**은 [BRINGUP_GUIDE.md §3.2](docs/BRINGUP_GUIDE.md). **모든 키는 기본값이라도 파일에 명시**하는 것이 규칙이다 — `python -m llmwiki config fill-defaults --all` 이 빠진 키를 기본값으로 채워 쓴다(값을 바꿀 때 줄만 고치면 되게). 값이 Web UI ↔ 파일 ↔ 서버 사이에서 양방향으로 맞는지는 `tools/verify/verify_settings_sync.py` 가 검사한다([SETTINGS_SYNC.md](docs/SETTINGS_SYNC.md)). `setup/check_env.py` 가 security/agents/서버 포트/재시도/동시성/스케줄/모델 카탈로그/터미널 인코딩 설정을 한 줄씩 보고한다.

설정 파일은 모두 **원자적으로 저장**된다(`llmwiki/atomicio.py`). 여러 관리자가 같은 순간에 저장해도 파일이 반쪽으로 남거나 서로의 내용을 덮어쓰지 않고, 저장 중에 읽는 요청은 항상 이전 내용을 온전히 본다.

---

## 3. 주요 기능 (v3)

- **문서 계약**: front matter(schema_version, doc_type, id, date, tags, module, hw, related) → doc_meta · 메타 토큰 · lint(`corpus lint`).
- **결정적 관계 + provenance**: explicit(front matter) / rule(ID 패턴) / cooccur / llm / human 구분, confidence 관리, 그래프 탐색 가중(`provenance_w`), 노드 ↔ 원본 문서(doc_refs).
- **프로바이더**: Anthropic(직접 또는 **Anthropic-compatible 게이트웨이 + PAT**, `anthropic_base_url`), **OpenAI-compatible**(chat/embeddings, **사내 게이트웨이 + PAT** — 헤더 형식 `openai_api_key_header`), Ollama, **rerank 전용 엔드포인트**(Cohere/Jina/vLLM/Voyage), **Generic Headless Agent**(opencode/claude/codex CLI subprocess, `agents.json`, Windows `.cmd` 셸 지원), Voyage/ST/hash 임베더. 연결 확인은 `models test --live`(실제 호출 1회). 설정 예: `setup/config.example.pat-gateway.json`, `config.example.headless.json`.
- **진행 표시**: 빌드/질의처럼 오래 걸리는 작업은 CLI 에 `⏳ 단계 › 진도율 · LLM 응답 대기 Ns` 를, Web 에 진행 패널(단계 경로·%·LLM 대기·최근 로그)을 실시간으로 보여 준다. 멈춘 것처럼 보이던 "전체 리빌드 + llm_graph" 는 서버 락 뒤에 있던 폴링을 락 밖으로 옮겨 해결.
- **빌드 견고성**: `health` 사전 검사, 파일 락, 임베딩 **내용 해시 캐시**(rename/재빌드 0 비용), **재개/체크포인트**, **적응형 배치·WAL 관리**, 진행률·coverage 리포트(`embed report`, Web), 정합성 검증 `build verify --fix`, 삭제/rename 추적.
- **한글**: 조사 제거 + bigram + 스크립트 경계 분리 + **복합어 사전** + 선택적 **kiwi 형태소** + **trigram 폴백**.
- **질의**: **한국어 상대 시간 파싱**(지난주·3일전·Q3, timezone 설정) → **규칙 확장**(유형별) → 라우터(+LLM) → **LLM 확장/분해**(원 질의 유지) → pin → fts/vector/graph/doc_vector → **융합 5방식**(rrf·weighted·zscore·dbsf·rrf_boost) + **post-boost**(문서유형·시간·최신성·pin·provenance·피드백·exclude) → 리랭크 → 컨텍스트.
- **근거**: **evidence_check**(휴리스틱/LLM) → **fallback 루프**(rules→expand→graph→wide→mcp, attempt/token/latency 예산) → **insufficient_data 응답** → **claim_check**(인용 존재 + 실제 지지 검증, groundedness/citation_precision, mark/drop/refine).
- **포렌식**: 단계별 진단 규칙으로 "왜 답을 못 만들었나" 기록(`forensic last`), 누적 → `memory consolidate` → corpus_gap/query_rule/tuning 제안(HITL).
- **메모리**: episodic(에피소드·피드백 부스트) + semantic(승인 규칙·pin) + decay(반감기).
- **평가**: eval 경로 = 사용자 경로. **trial** 저장/비교(지표 Δ, 질문별 승/패, 설정 diff), `fusion compare`.
- **로그**: `logs/` JSON Lines(정상 동작 포함), `run_id` 로 요청 프로파일과 연결(`logs grep --request <id>`).
- **Web UI**: 워크플로 기준 8그룹(Ask / **🧭 Pipeline** / Corpus / Knowledge / Quality / Evolve / Settings / Observability), 프리셋 체크박스, 토글 사이드바 자동 생성, **테마**(light/dark/high-contrast/solarized, 확장 가능), 콘솔에서 CLI 전체 실행. 단계 구조를 읽는 자리와 고치는 자리는 **Pipeline 하나**다 — 예전 Observability › 구조·흐름 탭은 2026-09-19 에 흡수됐다([PIPELINE_PAGE.md](docs/PIPELINE_PAGE.md)).
- **다중 사용자 권한** ([SECURITY.md](docs/SECURITY.md)): 로컬 ID/비밀번호 + SSO(OIDC · 프록시 헤더) + API 키 병행, **역할 6단계 `viewer < class3 < class2 < class1 < builder < admin`**, 작업 등급 7단계(read/run/edit/index/rebuild/admin/destructive)에 **admin 이 편집하는 권한 표**(`security perms`, Web 보안 탭), **익명 접속 = viewer**(DB 무영향 기능 전부), CLI 도 같은 표로 게이트(`--user`), 리빌드/파괴적 작업은 확인 문구 + 비밀번호 + 자동 스냅샷, 감사 로그.
- **문서 단위 접근 제어** ([SECURITY.md §6.2](docs/SECURITY.md)): 위 권한 표가 "무엇을 **실행**할 수 있나" 라면 이쪽은 "무엇을 **읽을** 수 있나" 다. RAG 에서 검색은 곧 읽기이므로, 인사·보안 사고·미공개 로드맵이 섞인 위키에서는 `docacl.json` 의 경로 규칙과 문서 front matter 의 `acl:`(둘 중 **높은 등급**)로 근거를 역할별로 가린다. 막히는 곳은 **질의 답변의 근거(trace 의 `doc_acl` 단계) · 채널 검색 · 문서 열람 · MCP** 네 곳 모두이고, admin 은 항상 전부 본다. 규칙이 없으면 아무도 막지 않는다. `security docacl show|check --role viewer`, Web Settings › 보안 › 문서 접근 제어(역할별 "보임/가려짐" 영향 확인), 토글 `doc_acl`.
- **프롬프트 인젝션 방어** ([SECURITY.md §6.2](docs/SECURITY.md)): 사내 위키는 누구나 문서를 올리고 외부 글도 수집된다. 그 본문이 그대로 답변 프롬프트에 들어가므로, 컨텍스트에 넣기 전에 구획(`<<</C1>>>`)·역할(`system:`)·인용(`[C7]`) 흉내 조각을 무력화해 펜스로 감싼다(`llmwiki/ctxguard.py`, 토글 `context_guard`). **내용은 지우지 않고 표시만 바꾸므로 근거 품질은 그대로**이고, 시도한 흔적은 `injection_marks` 로 trace 에 남는다.
- **MCP** ([MCP.md](docs/MCP.md)): stdio(같은 PC) + **Streamable HTTP**(`serve` 의 `POST /mcp`, Bearer API 키, 여러 외부 LLM 동시 접속, Windows/Linux) + 브리지(stdio 전용 클라이언트 → 원격). 도구 **17개** `wiki_query(mode=deep, answer_mode, output_mode …)`, `wiki_search`, `wiki_related`, `wiki_doc`, `wiki_entity`, `wiki_propose`, `wiki_feedback`, `wiki_forensic`, `wiki_status`, `wiki_analysis`, `wiki_sources`, `wiki_external_search`, `wiki_requests`, `wiki_rerun`, `wiki_sweep`, `wiki_rules`, `wiki_graph_profile` — 모두 읽기/제안(색인 불변). `tools/list` 는 도구마다 **annotations**(읽기/쓰기·외부 접근 여부)를 함께 주고, `tools/call` 은 실행 전에 **인자(required·type·enum)를 검증**해 실패 이유와 스키마를 돌려준다 — 붙은 LLM 이 스스로 고쳐 다시 부른다. 프로토콜 버전은 `2025-06-18`/`2025-03-26`/`2024-11-05` 중 협상. 붙기 전 자가 점검 **`python -m llmwiki mcp --doctor`**. use case(구현/코드리뷰/이슈분석/리팩토링/unified search)별 skill·agent 는 이 도구 위에 별도로 만든다.
- **상세 분석 모드** ([ANALYSIS_MODE.md](docs/ANALYSIS_MODE.md)): 토글 `analysis_mode` / `query --analyze` → 질의가 debug_level 2 로 실행되고 설정 스냅샷·단계 타임라인·채널별 상위·융합/부스트/리랭크 전후·doc_expand·컨텍스트·fallback·최종 근거 표·답변 판정·claim·**품질/속도/토큰 렌즈 소견 + 조절점(현재값)**·포렌식·프롬프트 샘플이 `logs/analysis/req_<id>.md` 한 장으로. `analyze <id|last> [--focus] [--print]`, Web 📊(초점·다운로드·복사), MCP `wiki_analysis`, `GET /api/analysis`. LLM 에게 첨부하는 지시문 포함.
- **다른 RAG 연동·MCP 확장** ([RAG_FEDERATION.md](docs/RAG_FEDERATION.md)): `mcp_sources.json` 에 소스(전송 stdio/http/rest)를 적으면 (1) **`external_rag`** — 외부 검색 결과가 가상 청크 `ext:<source>:<id>` 로 fts/vector/graph 와 함께 RRF 융합·리랭크·인용(`[C#]`, `hits.external`), (2) **`mcp_federation`** — 외부 서버의 tool 이 우리 `/mcp` 에 `<source>__<tool>` 로 노출·중계(외부 LLM 은 우리 서버 하나만 등록; A↔B 상호 연결도 재귀 방지), (3) **플러그인** `plugins/mcp_tools/*.py` 의 `register(add_tool)` 로 코드 수정 없이 도구 추가. 리허설용 목업 `--mock-server`(stdio)·`--mock-rest`(REST) 포함.
- **채널별 빌드**: `build fts|vector|graph [--full]` 로 한 채널만 다시 만든다(세 채널은 `chunks` 만 읽어 서로 독립; 끝나면 verify 로 결손 보고). `build --channels fts,vector`, 토글 `build_fts`/`embed`/`rule_graph`, Web 빌드 탭 버튼. — BRINGUP_GUIDE §6.1
- **문서 단위 확장 (doc_expand)**: 리랭크 상위 청크가 속한 문서의 나머지 청크를 문서 순서로 컨텍스트에 추가(`[C#]` 인용, 상한 `doc_expand_*` 튜닝). 고르는 방식은 `doc_expand_mode` 로 keyword·vector·hybrid 중에 정하고, **`full` 로 두면 근거가 나온 문서를 통째로** 읽힌다(점수로 거르지 않고 `doc_expand_max_chunks`·`context_max_chars` 로만 제한 — 설계 문서·절차서처럼 문서 하나를 끝까지 봐야 하는 질문에). 토글 on/off, speed/token 프리셋은 off. — BRINGUP_GUIDE §7.1
- **LLM 재시도·실패 보고**: headless(`agents.json timeout_s=300, retries=3`)와 모든 프로바이더의 timeout/네트워크 오류를 `llm_retries` 회 재시도하고, 최종 실패는 결과 `llm_report` + 답변 상단 `⚠ LLM 실행 보고`(역할·시도 횟수·오류·대체 경로) + 빌드 alerts 로 남긴다. — BRINGUP_GUIDE §4.3
- **기대 결과 포렌식** ([FORENSIC.md](docs/FORENSIC.md)): `forensic expect <id|last> --doc … --term …` — 사용자가 기대한 문서/용어가 fts/vector/graph → 융합 → 리랭크 → 컨텍스트 → 답변 중 어디서 탈락했는지 단계별 표 + 수정안(규칙/pin/튜닝/코퍼스, `--propose` 로 HITL 등록). 모든 질의 결과에서 Web(🎯)·CLI·MCP 로 요청 가능.
- **동시 사용 (30명 기준)** ([CONCURRENCY.md](docs/CONCURRENCY.md)): 질의는 서로 막지 않고 병렬로 처리된다(요청마다 설정 사본·전용 DB 연결·독립 프로파일 카운터). 쓰기만 짧게 배타 구간을 잡고, 증분 빌드는 읽기와 함께 돌아간다. Web·CLI·MCP·스케줄러가 **하나의 요청 관리자**를 공유해 동시 실행 슬롯·대기열·속도 제한·차단·점검 모드가 모든 경로에 똑같이 적용된다. 용량을 넘으면 기다리게 두지 않고 `429`/`503` 과 재시도 시점을 돌려준다. 설정은 `server.json`.
- **역할별 LLM 정책**: answer·rerank·extract 처럼 역할마다 `timeout_s`·`retries`·`backoff`(지수/선형+지터)·`budget_s`(재시도 포함 총 시간)·회로 차단(`circuit_failures`·`circuit_cooldown_s`)·**`max_tokens`**(출력 토큰 상한)을 따로 준다. 기본 배포값은 보조 단계를 빨리 포기하고(expand 30초·rerank 60초·verify 90초, 각 1회 재시도) 사용자가 기다리는 answer 는 넉넉히(240초·2회), 빌드·배치는 길게(extract 120초·review 300초) 잡는다. 같은 모델이 연속 실패하면 잠시 호출을 끊어 모두가 타임아웃을 기다리는 상황을 막고, **LLM 이 끝내 실패해도 추출식 답변·로컬 리랭크·규칙 그래프로 최선의 결과를 낸다**(무엇을 무엇으로 대체했는지 답변 상단과 `llm_report` 에 표시).
- **진행률과 취소**: 빌드·질의의 단계·%·경과·남은 예상 시간·대기열 위치를 Web 진행 패널과 CLI 한 줄 표시로 보여 주고, 언제든 중지할 수 있다(단계·임베딩 배치·LLM 호출 직전에서 협조적으로 끊고 빌드는 진행분을 저장). CLI·스케줄러·MCP 작업도 `data/live/` 를 통해 같은 화면에 보이고 서버에서 취소된다.
- **스케줄러** ([SCHEDULER.md](docs/SCHEDULER.md)): `schedule.json` 에 시각·주기(`every` / `at`+요일 / 5필드 `cron`)와 동작 19종을 적으면 서버가 실행한다. 증분 빌드·URL 수집·파이썬 스크립트·임의 CLI 명령은 물론 evolve·memory·precompute·eval·trial·snapshot·wiki·forensic·embed_report 까지 포함한다. 파일을 저장하면 자동 재적재되고, 실행 이력과 다음 실행 시각을 Web 설정 › 스케줄과 `schedule list` 에서 본다.
- **모델 목록**: `models.json` 에 쓸 수 있는 모델을 넣고 빼면 Web 설정의 역할별 드롭다운과 `models list` 에 그대로 반영된다. `models discover` 로 Ollama·OpenAI 호환 게이트웨이의 실제 제공 모델을 조회해 추가할 수 있다.
- **관리자 모니터**: Web 관리 › 서버 모니터(또는 `server stats|activity|limits|block|kick`)에서 진행 중·대기 중 요청, 사용자·IP별 사용량, 거절 사유, 세션 목록, LLM 회로 상태를 보고 제한값을 바꾸거나 특정 IP·사용자를 차단·강제 로그아웃한다. 일반 사용자도 "지금 서버에서 무엇이 돌고 있는지"는 볼 수 있어(공개 범위는 `server.json monitor` 로 조절) 느린 이유를 스스로 확인한다.
- **계정별 Web UI 프로파일** ([WEB_UI.md](docs/WEB_UI.md) §4): 테마·토글·프리셋·요청 단위 오버라이드·질의 모드·고정 탭·화면 분할 상태(열 수·높이·넓게·접힘)·마지막 화면을 계정에 저장해 어느 PC에서 접속하든 같은 화면으로 시작한다. 로그인하면 자동으로 불러온다. 서버 공용 설정과 완전히 분리되어 있고, 게스트는 브라우저에만 남는다.
- **Web UI 화면 구성** ([WEB_UI.md](docs/WEB_UI.md)): 탭 고정 + 1·2·3·4열 분할 보기(패널별 넓게·접기·순서 이동·개별 새로고침, 머리글 고정·내용만 스크롤), 헤더의 항상 보이는 활동 표시기(작업 하나 = 막대 하나, 내 요청은 초록, 대기는 점선), 진행 중 작업 보드와 용량 게이지. 화면 갱신 조회는 **하나로 합쳐** 여러 패널이 나눠 쓴다.
- **답변 반복 루프 차단** ([WEB_UI.md](docs/WEB_UI.md) §6): 작은 모델이 같은 구절을 되풀이하는 고장을 탐지해 잘라내고 이유를 표시하며, 그런 답변은 캐시에 넣지 않는다. 이미 저장된 것은 `precompute check` · `precompute clear --broken` 으로 정리한다.
- **분석 리포트 LLM 소견** ([WEB_UI.md](docs/WEB_UI.md) §5): 상세 분석 리포트를 LLM 에게 읽히고 "어떤 설정을 어떤 값으로 바꾸면 좋아지는지"를 표로 받는다. 그대로 Evolve 제안(HITL)으로 등록할 수 있다.
- **터미널 한글**: CLI 가 시작할 때 콘솔 코드페이지와 표준 입출력 인코딩을 UTF-8 로 맞춰, 로캘이 다른 PC 나 출력을 파일로 넘길 때 한글이 깨지거나 명령이 `UnicodeEncodeError` 로 죽던 문제를 막는다(`console_encoding`).
- **답변 모드 · 출력 모드** ([ANSWER_MODES.md](docs/ANSWER_MODES.md), 2026-09-18): `answer_mode=grounded|best_effort`(근거가 부족해도 문서 사실 `[C#]` + 배경 지식 `[BK]` 로 답함, 결과에 `result_type`·`refs` 항상), `output_mode=answer|fused|reranked|context`(융합 뒤·리랭크 뒤·컨텍스트까지의 **중간 산출물을 그대로 응답** — 클라이언트 LLM 이 직접 처리할 때), 융합 뒤 LLM 두 단계 토글 `llm_after_fusion`/`llm_after_rerank`, 실패 시 대체 경로 토글 `degrade_on_llm_failure`. CLI `--answer-mode/--output` · Web Ask 드롭다운 · MCP `wiki_query(answer_mode, output_mode)` 세 창구 동일.
- **채널별 top-k 가중 · 리랭크 창 주입** ([FUSION_TOPK.md](docs/FUSION_TOPK.md)): 채널마다 "top-k 안" 과 "밖" 후보에 다른 배율(`fts_topk_n/_topk_w/_tail_w` …), 채널별 상위 n개를 리랭크 창에 보장 주입(`channel_inject`). 요청 단위 `overrides.tuning{…}` 으로 파일을 건드리지 않고 시험.
- **역할 단위 앙상블** ([ENSEMBLE.md](docs/ENSEMBLE.md)): `llm_roles.<role>.ensemble` 에 멤버 최대 3개(✓/provider/model/weight) + 대기 정책 + 취합 LLM(`prompts/ensemble_merge.md`). 모든 LLM 단계가 같은 래퍼를 지나므로 한 설정이 전 단계에 적용. `models ensemble show|set`, Web 역할 표 "앙상블 ▸".
- **파라미터 스윕** ([SWEEP.md](docs/SWEEP.md)): 지난 질의를 기준으로 키 하나의 값을 바꿔 가며 **그 키의 단계부터만** 재실행(rerun 재생) → 단계 × 값 비교 격자(기준과 다른 셀 강조, 셀 클릭 = diff). `sweep run|list|show|compare` · `/api/sweep` · MCP `wiki_sweep` · 🧭 Pipeline 페이지 스윕 폼. 값 개수·병렬 수는 `sweep_max_values`·`sweep_max_parallel`.
- **그래프 진단 프로파일** ([GRAPH_PROFILE.md](docs/GRAPH_PROFILE.md)): 규모·연결성(성분/고립/허브)·문서 커버리지·품질 신호(중복 후보·끊긴 관계·cooccur 비중)·**규칙 기여(죽은 규칙)**·질의 활용(시드 없던 질의 키워드)·개선 제안(어느 파일·키를 고칠지)·이력 비교(`--compare`). `graph profile` · Knowledge › 그래프 진단 · MCP `wiki_graph_profile`.
- **🧭 Pipeline 페이지** ([PIPELINE_PAGE.md](docs/PIPELINE_PAGE.md)): 질의/빌드/진화/워처 흐름을 세로 블록으로 놓고 블록마다 토글·config 값·튜닝 키를 **그 자리에서** 본다·바꾼다(이번 요청에만 / tuning.json 저장 두 갈래) + 스윕 폼. 사이드바 토글은 요약(`sidebar_toggles=compact`)으로 줄었고 `full` 로 예전 배치를 되살린다.
- **headless 일반화** ([HEADLESS.md](docs/HEADLESS.md)): 프롬프트 전달 기본 `stdin`(Windows 명령줄 32K 한계 `WinError 206` 방지 — `arg_max_chars` 가드가 넘치면 자동 전환), 자식 환경 `env_passthrough` 허용 목록, `config.json` 만으로 API ↔ headless 전환(카탈로그 provider 자동 해석), 목업 에이전트로 종단 테스트(`tests/test_headless_switch.py`).
- **설정 ↔ 서버 연동 보증** ([SETTINGS_SYNC.md](docs/SETTINGS_SYNC.md)): `config fill-defaults --all`(모든 키를 기본값으로 파일에 명시), `config env`/`GET /api/env`(`.env` 가시성·마스킹), 카탈로그에서 모델을 고르면 provider 자동 채움, `models test --catalog`(카탈로그 전체 연결 테스트), 하네스 `verify_settings_sync.py` 가 10개 설정 표면을 **양방향**(UI → 파일 → 유효값, 파일 → reload → UI) 으로 검사.
- **운영 한도**: 로그 총량 `log_total_max_mb`(500) 초과 시 `warn|prune|stop`([LOG_QUOTA.md](docs/LOG_QUOTA.md)), 불용어 파일 `stopwords.json`([STOPWORDS.md](docs/STOPWORDS.md)), 요청 단위 `overrides` 화이트리스트와 500 트레이스 마스킹([SECURITY.md §6.1](docs/SECURITY.md)) 을 전제로 `web_host` 기본 **0.0.0.0**.

---

## 4. CLI 요약 (`python -m llmwiki <cmd>` 또는 `run.bat <cmd>`, 상세: [CLI_FLOWS.md](docs/CLI_FLOWS.md))

| 영역 | 명령 |
|---|---|
| 빌드/운영 | `health` · `build [--full] [--channels fts,vector,graph] [status|verify --fix]` · **`build fts|vector|graph [--full]`**(채널 리빌드) · `embed report|status|clear-cache` · `corpus lint|types|example|lint-file|stats` · `mcp-source list|test|tools|retrieve|federated|ingest|enrich|fetch`(다른 RAG/검색 API 연동) · `watch --once` · `maintenance …` · `system` |
| 질의/디버그 | `query "…" [--preset q] [--trace] [--json] [--no-doc-expand] [--analyze --focus quality|speed|tokens]` **`[--answer-mode grounded|best_effort] [--output answer|fused|reranked|context]`**(답변 모드 · 중간 산출물을 그대로 응답 — [ANSWER_MODES.md](docs/ANSWER_MODES.md)) · **`analyze <id|last> [--focus] [--print] [--out]`**(상세 분석 리포트) · **`search fts,vector,graph "…" [--mode or|and|rrf] [--k N]`**(채널을 하나 또는 여러 개 조합해 돌리고 행마다 어느 채널이 몇 위로 찾았는지 표시 — Web Ask › 채널 검색 · MCP 도구와 같은 엔진) · **`inspect "…"`**(질의 해부 — LLM 없이 토큰화·규칙 확장·시간 표현·채널 라우팅·pin 을 한 번에. Web Ask › 디버그 · MCP 도구는 [MCP.md](docs/MCP.md)) · `rules show|add|test|lint|stats` · **`rules effect [--order fired|helped|rate|useless] [--reset]`**(규칙별 **실제 효과** — 걸린 횟수·후보를 가져온 횟수·최종 컨텍스트에 기여한 횟수·인용된 횟수. 걸리기만 하고 한 번도 기여하지 못한 규칙을 찾아 지우는 근거 — [QUERY_RULES.md](docs/QUERY_RULES.md)) · **`rules explain <용어>`**(이 말이 어느 유형·방향으로 퍼지나 — [QUERY_RULES.md](docs/QUERY_RULES.md)) · `time "…"` · `pin add|list|test|remove` · `precompute run|status|clear` · `forensic last|list|summary|<id>` · **`forensic expect <id|last> --doc … --term … [--propose]`** · **`rerun <request_id> --from <단계> [--points] [--list] [--trace]`**(저장해 둔 중간 결과로 **그 단계부터만** 다시 — [RERUN.md](docs/RERUN.md)) |
| 품질 | `eval [--matrix]` · `trial run|list|compare|report` · `fusion show|compare` · **`sweep run <id|last> --key K (--range a:b:s | --values …) [--repeats N] [--from 단계]` · `sweep list|show|compare <id>`**(값마다 그 단계부터만 재실행해 단계×값 비교 — [SWEEP.md](docs/SWEEP.md)) · **`graph profile [--json] [--eval] [--compare] [--out FILE]`**(그래프 진단: 규모·연결성·허브·고립·규칙 기여·제안·이력 비교 — [GRAPH_PROFILE.md](docs/GRAPH_PROFILE.md)) |
| 운영 통계 | **`stats [--full] [--days N] [--section index|build|queries|latency|tokens|quality|users|storage|embed] [--top N] [--json]`** — `--full` 이 색인 규모 너머를 연다: 단계별 빌드 ms, 질의 시간대·창구 분포, p50/p95 와 가장 느린 질의, 토큰, 근거 부족률, 폴더·테이블별 용량과 정리 힌트, 임베딩 캐시 적중률. 읽기 전용 — [OPS_STATS.md](docs/OPS_STATS.md) |
| 초기화 | **`reset data|settings|logs [--apply]`** — 다른 환경으로 옮길 때 앞 환경의 흔적을 턴다. 기본은 **미리보기**(지우는 목록·유지하는 목록·용량)이고 `--apply` 가 있어야 실행된다. `corpus/` 원본과 `security.json`·`.env` 는 기본으로 지키며, data 범위는 지우기 전에 자동 스냅샷 — [RESET.md](docs/RESET.md) |
| 지식 규칙 | **`graph-rules show|types|lint|test "<문장>"|add-entity <이름> <type> [별칭…]|add-alias <엔티티> <별칭…>|fill-defaults|path`** — 문서에서 그래프를 만드는 규칙(`data/rules.json`). **types** 는 쓸 수 있는 엔티티 유형·값 종류·관계 어휘, **lint** 는 빌드 전 정적 점검(깨진 정규식·없는 유형·가려진 링크 규칙·겹치는 별칭), **test** 는 문장 하나가 어떤 노드·관계가 되는지 — [GRAPH_RULES.md](docs/GRAPH_RULES.md) |
| 진화 | `evolve status|show|apply|reject|review|feedback` · `memory status|decay|consolidate|episodes` · `wiki` |
| 설정 | `config show [--effective]|set|reset|paths|reload` · **`config fill-defaults [--tuning] [--rules] [--all] [--examples] [--dry-run]`**(모든 설정·튜닝·규칙 키를 **기본값으로 파일에 명시** — 나중에 값을 바꿀 때 줄만 고치면 되게) · **`config env`**(`.env` 키 이름·설정 여부·마스킹 값·`LLMWIKI_*` 오버라이드 — [SETTINGS_SYNC.md](docs/SETTINGS_SYNC.md)) · `models show|test [--live]|set` · **`models test --catalog [--live]`**(카탈로그 enabled 모델 전부 연결 테스트) · **`models list [--role r] [--provider p]`·`models catalog add|remove`·`models discover`·`models policy`**(쓸 수 있는 모델 목록과 역할별 타임아웃/재시도 정책) · **`models ensemble show|set <role> …`**(역할 단위 다중 LLM 병렬 + 취합 — [ENSEMBLE.md](docs/ENSEMBLE.md)) · `tuning show|set|reset|doc` · `preset list|show|apply|diff` · `prompts list|show|reset` |
| 서버 운영 | **`server stats|activity|limits [set k=v]|block|unblock|kick|maintenance|circuits|cancel <token>`** — 진행 중·대기 중 요청 보기, 동시성·속도 제한 변경(`server.json` 저장), IP/사용자 차단, 세션 강제 종료, LLM 회로 해제, 작업 중지. `--server URL` 로 원격 서버에도 사용 ([CONCURRENCY.md](docs/CONCURRENCY.md)) |
| 스케줄 | **`schedule list|show|add|remove|enable|disable|run <name>|history|validate`** — 정해진 시각·주기 작업. 서버가 떠 있으면 서버가 돌리고, `schedule run` 은 OS 스케줄러에서 단발로 부를 때 쓴다 ([SCHEDULER.md](docs/SCHEDULER.md)) |
| 보안 | `users add|list|set-role|passwd|remove` (역할 viewer/class3/class2/class1/builder/admin) · `security show|init|audit|perms [set k=v|reset]` · `apikey add|list|remove` · `snapshot list|create|restore|prune` — 리빌드/파괴적 명령(`build --full`, `build fts|vector|graph`, `maintenance purge_requests`, `config reset`, `snapshot restore`)은 확인 문구 또는 `--yes`. 전역 `--user <id>`(+`LLMWIKI_PASSWORD`) 로 CLI 실행자 로그인 |
| 관측 | **`ledger list [--problems] [--status|--kind|--origin|--user|--q|--min-ms] · ledger show <token> · ledger stats [--days N] · ledger prune`**(요청 원장 — 서버로의 **모든 요청**을 거절·시간초과·취소·중단까지. `data/ledger` 파일을 직접 읽으므로 **서버가 꺼져 있어도** 된다. 실행 중 서버는 `server ledger` — [REQUEST_LEDGER.md](docs/REQUEST_LEDGER.md)) · `requests list|last|show` · **`requests queries [--user|--origin|--q] · requests users`**(질의 이력 — **누가** 무엇을 물었고 👍/👎 가 어디 몰리나. 2026-09-23부터 원천이 `requests` 로 합쳐졌다 — [CLI_FLOWS.md](docs/CLI_FLOWS.md)) · `logs tail|grep|files` · **`logs status [--json]`**(`logs/` 총량·상한·동작 — [LOG_QUOTA.md](docs/LOG_QUOTA.md)) · `arch [show|doc|limits] [--flow …]`(**`arch doc` = [OPTIMIZATION_GUIDE.md](docs/OPTIMIZATION_GUIDE.md) 재생성** · **`arch limits` = 단계별 시간 제한** — 그 단계를 끊을 수 있는 값과 어느 파일의 어느 키인지. Web 에서는 trace 의 실측 ms 밑 `≤ 제한` 과 🧭 Pipeline 단계 상세의 '시간 제한' 표가 같은 값을 쓴다 — [CONCURRENCY.md](docs/CONCURRENCY.md)) · **`optimize <id|last> [--focus quality|speed|tokens] [--out bundle.md]`**(가이드+설정+실측+지시문을 한 파일로 — LLM 에게 그대로 준다) · `graph [--provenance …]` · `entity` · `docs` · `stats` |
| 인터페이스 | **`--version`**(= [RELEASE_NOTES.md](docs/RELEASE_NOTES.md) 맨 위 절 · `/api/status.version` · MCP serverInfo) · `serve [--port] [--host] [--insecure]` (Web UI + `POST /mcp`; 기본값은 `config.json web_host/web_port` — 바인드 기본이 **0.0.0.0**) · `mcp [--transport stdio|http --host --port] [--connect URL --token …] [--client-config [--url]] [--doctor [--check-sources]]` (기본값 `mcp_transport/mcp_host/mcp_port/mcp_url`; **`--doctor` 는 도구·스키마·플러그인·외부 소스·페더레이션·인증을 한 번에 자가 점검**) |
| 검증 | **`python tools/verify/verify_all.py`** 한 줄로 전부 — 단위·스트레스(30명 동시)·문서정합·**CLI/Web/MCP 정렬**·**설정 UI↔파일↔서버 양방향**·CLI·Web·MCP 종단·UI 배선·브라우저·버튼·보안화면·단계 재실행·협업 다중접속·**타임아웃/실패 경로**·몽키. 결과는 `tools/verify/verify_all_result.json` 과 [VERIFICATION_0918.md §0](docs/history/2026-09-19/VERIFICATION_0918.md) 표에 **자동으로** 기록된다(손으로 옮기지 않으므로 낡지 않는다). **무엇을 고쳤을 때 무엇을 돌리나**는 [TESTING_GUIDE.md](docs/TESTING_GUIDE.md). 개별 실행: `verify_surface_align.py` **`verify_stage_align.py`**(파이프라인 **단계**가 코드·레지스트리·진행 라벨·토글/튜닝/config 네 곳에서 같은 이름인지 — [PIPELINE_PAGE.md §4.7](docs/PIPELINE_PAGE.md)) `verify_settings_sync.py` `verify_cli.py` `verify_web.py` `verify_mcp.py [--quick]` `verify_docs.py` `verify_responsive.py` `verify_timeouts.py` `verify_buttons.py` `verify_security_ui.py`([SECURITY §8.1](docs/SECURITY.md)) `verify_rerun_ui.py` `verify_monkey.py` |
| 코퍼스 도구 | `python tools/corpus_ingest.py <경로> --out corpus/imported`(형식 없는 문서를 계약 형식으로 **무손실** 변환, `--verify-only` 로 재검증) · `python tools/fetch_rfc_corpus.py --max-mb 8`(공개 RFC 로 실데이터 코퍼스 구성) |

---

## 5. 구조 한눈에

```
build : health → [mcp_ingest] → load_corpus(front matter) → diff(rename) → chunk_index(FTS+메타토큰+lint; 채널 fts) → embed(캐시·재개·적응형; 채널 vector) → graph(explicit/rule/cooccur/llm → doc_refs → communities; 채널 graph) → [doc_vectors] → wiki → prune → verify → warm → [precompute]
        채널만 다시: build fts | vector | graph  (chunks 불변, 다른 채널 불변, verify 로 결손 보고)
query : cache → time_scope → query_rules → router(+llm) → [query_expand] → pins → fts(+rule/alt/related) | vector(+alt) | graph | [doc_vector] | [external_rag: ext_<source> 가상 청크] → fuse+boost(채널 top-k 가중) → [channel/external inject] → [llm_after_fusion] → rerank → [llm_after_rerank] → doc_expand(같은 문서 관련 청크) → context → evidence_check → [fallback L1..L4] → answer(llm|extractive|insufficient|best_effort[BK]; LLM 실패 시 llm_report/degrade) → claim_check → forensic → evolve_capture → episode → log
        output_mode=fused|reranked|context 는 그 지점에서 멈춰 후보/컨텍스트를 그대로 응답 · sweep = rerun 재생 위에서 값만 바꿔 N회 → 단계×값 비교
mcp   : tools/list = built-in 17 + plugins/mcp_tools/*.py + [mcp_federation: <source>__<tool> 중계]  ←  외부 LLM (stdio | POST /mcp | 브리지)
        기대 결과 포렌식: forensic expect <id> --doc --term → 같은 설정으로 재실행 → 단계별 탈락 지점 + 수정안
evolve: capture | feedback | llm_review | forensics consolidate | forensic expect → proposals(strength, decay; pin/query_rule/tuning 적용 가능) → HITL apply → snapshot → rebuild → trial → 승격|롤백
access: 게스트(viewer) / 로컬 ID·PW / SSO / API 키 → 등급표(read<run<edit<index<rebuild<admin<destructive) × 권한 표(permissions) → Web · CLI(--user) · MCP(stdio | POST /mcp | 브리지)
```

## 6. 폴더

```
llmwiki/            패키지 (config, presets, tuning, prompts, logging_setup, profiler, buildlock, health, corpus, schema, store, providers, headless, rerankers,
                    embed_run, graph_rules, graph_build, graph_llm, wiki, precompute, query_engine, retrieval, fusion, query_rules, timeparse, pins,
                    answer, evidence, forensic, evolve, memory, trials, evalset, pipeline, cli, auth, snapshots, mcp(stdio+HTTP+브리지), mcp_client, architecture, web/)
security.json       로그인·역할·권한 표·API 키 (기본 admin kh82.kim — 공개 전 변경)
setup/              INSTALL.md, install.bat/.sh, check_env.py, config.example.json (+ .pat-gateway / .headless), .env.example,
                    security.example.json, agents.example.json, mcp_clients.example.json, requirements-optional.txt,
                    sample_corpus_modem/ (합성 모뎀 코퍼스 + questions.json — 기본 config 가 가리키는 곳), make_sample_corpus_modem.py, schedule_build.ps1/.sh
tools/verify/       전 기능 검증 하네스 (verify_cli / verify_web / verify_ui_wiring / verify_browser) — docs/history/2026-09-15/VERIFICATION_0915.md
plugins/mcp_tools/  MCP 플러그인 도구 폴더 (_example_echo.py 예시, README) — docs/RAG_FEDERATION.md §3
corpus/             실제 문서를 넣는 폴더 (git 제외). 넣은 뒤 config.json corpus_dirs 를 ["corpus"] 로
docs/               현행 문서(날짜 없음) · history/<날짜>/ (회차 기록) · legacy/ (v1 문서)
schemas/ prompts/   문서 스키마 · 프롬프트 (첫 실행 시 기본값 생성)
data/               llmwiki.sqlite3, rules.json, mcp_cache/(MCP ingest 문서), snapshots/, build.lock
logs/ wiki/ eval/   로그(JSONL) · 위키 페이지 · 평가셋
tests/              unittest (python -m unittest discover -s tests)
```

## 7. 현재 상태 (2026-09-20 · v3.2.0)

- **2026-09-19/20 요청 14항목 — 완료** ([RELEASE_NOTES.md](docs/RELEASE_NOTES.md) 3.2.0 · 설계·판정 [IMPLEMENTATION_PLAN_0919.md](docs/history/2026-09-19/IMPLEMENTATION_PLAN_0919.md)): 그래프 규칙 확장성(값 종류 레지스트리·**스키마 절**·lint), 관리자 **초기화 3종**, 포팅 연결 정보 문서와 `config bundle`+`LLMWIKI_CONF_DIR`, LLM API/headless 두 경우의 `config.json` 샘플과 전환 하네스, 빌드 과정 점검(전 채널·30명 동시 실측), Ask **채널 검색·디버그** 개선, 앙상블 설명 복구, **Trial 문항 원천**(실제 질의 이력)과 단계별 지표, **Evolve 제안 설명·impact**, **근거 → 원본 문서 연결**, **운영 통계**(`stats --full`), **세 창구 전수 정렬 감사**, 그리고 이 문서 정리. 항목마다 조사한 사실·택한 설계·**택하지 않은 대안과 이유**·검증 명령이 그 문서에 있다.
  이 회차에서 반복해 나온 결함 유형은 하나였다 — **설정은 되는데 아무 일도 하지 않는 기능**: 문서가 권하던 `reads_during_build=always` 가 전체 리빌드에서 무효였고(고친 뒤 빌드 중 질의 54→**893건**, p95 5,548→**376ms**), `ask.js` 의 한 줄 오류가 **모든 화면 핸들러 등록을 죽이고** 있었으며(버튼 검사와 API 검사는 그동안 통과하고 있었다), 앙상블 CSS 가 다른 탭에 갇혀 있었고, `LW.openEntity` 는 불리기만 하고 정의된 적이 없었다. 각각에 재발 방지 장치를 붙였다(콘솔 오류 수집 · 탭 범위 CSS 정적 검사 · 정책 회귀 테스트 · 제안 오류 게이트).
  검증 기준도 함께 고쳤다: 정렬 검사의 기준을 **사람이 적은 표에서 코드로** 옮겨 새 명령·경로·도구를 표에 안 적으면 실패하게 했고(그 전에는 36개가 표 밖에 있었다), 세 창구를 **실제로 띄워** 비교하는 [`verify_tri_surface.py`](tools/verify/verify_tri_surface.py) 를 만들었다 — [SURFACE_ALIGNMENT.md](docs/SURFACE_ALIGNMENT.md).
- **2026-09-18/19 요청 23건 (17 + 6)** ([RELEASE_NOTES.md](docs/RELEASE_NOTES.md) 3.1.0 · 설계 [IMPLEMENTATION_PLAN_0918.md](docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918.md) · [IMPLEMENTATION_PLAN_0918_2.md](docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md) · 검증 [VERIFICATION_0918.md](docs/history/2026-09-19/VERIFICATION_0918.md)): 질의 경로에 답변 모드(`best_effort` + `[BK]`)·출력 모드(`fused|reranked|context`)·융합 뒤 LLM 두 단계·채널별 top-k 가중을 넣었고, 역할 단위 **앙상블**, **파라미터 스윕**(rerun 재생 위), **그래프 진단 프로파일**, **🧭 Pipeline 페이지**(토글·튜닝·스윕을 흐름 위에서), `rules explain`(규칙 방향), headless `WinError 206` 근본 수정(stdin 기본 + `arg_max_chars` 가드), `config fill-defaults`(모든 키를 파일에 명시), `.env` 가시성, 카탈로그 전체 연결 테스트, 로그 총량 제한, 불용어 파일, 사이드바 접기·복사 버튼·반응형, 그리고 외부 바인드 기본(`0.0.0.0`)의 전제인 요청 단위 `overrides` 화이트리스트와 500 트레이스 마스킹. MCP 도구 17개.
  검증에서 찾아 고친 결함: **켜도 아무 일도 일어나지 않던 기능 3건**(best_effort · degrade_on_llm_failure · 융합/리랭크 뒤 LLM — 설정과 프롬프트만 있고 파이프라인이 호출하지 않았다), 권한 거부가 500 으로 나가던 것, Settings 의 버튼 2개에 핸들러가 없던 것, `schedule.json` 을 고쳐도 새로고침에 안 보이던 것, 1024px 에서 Pipeline 폼이 가로 스크롤을 만들던 것.
  새 하네스 둘: **`verify_settings_sync.py`**(설정 11종을 UI→파일→유효값, 파일→reload→UI **양방향**으로) · **`verify_responsive.py`**(폭 5종에서 가로 넘침 0). 회귀 절차는 [TESTING_GUIDE.md](docs/TESTING_GUIDE.md).

- **2026-09-16 실사용 점검과 Web UI 정리** ([WEB_UI.md](docs/WEB_UI.md) · [VERIFICATION_0915.md](docs/history/2026-09-15/VERIFICATION_0915.md) §4.1): 실제로 써 보면서 나온 문제를 모두 고쳤다. **전체 리빌드가 청크 수의 제곱으로 느려지던 것**(`fts_trigram` 을 켜면 16,882 청크 기준 색인 단계만 약 17분 → 몇 초, §4.2 실측표), **답변이 같은 구절을 수십 번 반복하던 것**(자동 차단 + 캐시 오염 방지 + 이미 저장된 것 정리 도구), **화면이 한참 멈췄다 한꺼번에 갱신되던 것**(서버를 HTTP/1.1 keep-alive 로, 화면 갱신 조회를 하나로 통합), 메모리 화면의 `[object Object]`, 포렌식 `진단 실행` 무반응, 프로파일이 잘못된 요청 한 번에 지워지던 것. 화면은 1·2·3·4열 분할 보기, 헤더 활동 표시기(작업 하나 = 막대 하나, 내 요청은 초록), 진행 중 작업 보드, 분석 리포트 **LLM 소견**, 기본 테마 Light 로 정리했다. 버튼을 **실제로 눌러** 확인하는 `verify_click.py` 와 리빌드 속도를 지키는 `bench_fts.py` 를 검증 도구에 추가했다.
- **2026-09-15 다중 사용자 서버화** ([IMPLEMENTATION_PLAN_0915.md](docs/history/2026-09-15/IMPLEMENTATION_PLAN_0915.md) · [CONCURRENCY.md](docs/CONCURRENCY.md) · [SCHEDULER.md](docs/SCHEDULER.md)): 전역 락 하나로 한 번에 한 요청만 처리하던 서버를 **30명 동시 사용**을 견디는 구조로 바꿨다. 요청마다 설정 사본·전용 DB 연결·독립 카운터를 갖는 요청 격리, Web/CLI/MCP/스케줄러가 공유하는 요청 관리자(읽기·쓰기 락, 동시 실행 슬롯, 대기열, 속도 제한, 차단, 점검 모드, 감시 스레드), 역할별 LLM 타임아웃·재시도·백오프·회로 차단과 실패 시 대체 경로, 진행률·ETA 표시와 취소, `schedule.json` 스케줄러(동작 19종), `models.json` 모델 목록, 계정별 Web UI 프로파일, 로그인 뒤로가기 버그 수정, 터미널 한글 깨짐 수정. 신규 설정 파일 `server.json`·`schedule.json`·`models.json`(원본은 `setup/*.example.json`).
- **2026-09-15 스트레스·멍키 검증**: 동시성·스트레스 테스트 **43개** 추가(30 동시 질의 평균 43ms·p95 62ms, 빌드 중 질의, 긴 작업 취소, 잘못된 입력 25종, 설정 파일 동시 저장) + 터미널 인코딩 테스트 8개. 무작위 입력 하네스 `tools/verify/verify_monkey.py` 로 Web·MCP·CLI 에 제어문자·서러게이트·거대 본문·잘못된 JSON 을 쏟아부어 **서버 결함 13건**을 찾아 고쳤다. 특히 한 사람의 잘못된 입력이 **모두에게 오래 남는** 세 가지였다: `query_rules` 에 사전이 아닌 값이 저장되면 이후 모든 질의가 깨지던 것, 짝 없는 서러게이트가 섞인 질의 한 건 뒤로 서버 모니터가 계속 400 을 내던 것, 느린 콘솔 CLI 두 개가 읽기 슬롯을 잡은 채 다른 64명을 대기열에 쌓던 것. 최종 재실행 결과 500 오류 0.
- **2026-09-15 실데이터 코퍼스**: 공개 RFC 163편(8.4MB)과 형식 없는 프로젝트 문서 187개(2.5MB)를 계약 형식으로 변환해 색인 — 문서 352 · 청크 16,882 · 엔티티 607 · 관계 11,266. 변환기 `tools/corpus_ingest.py` 는 본문을 그대로 보존하고 `body_sha1` 로 **무손실을 재검증**하며(151/151 일치), 텍스트 추출이 불가역인 형식(HTML·PDF·docx)은 원본을 `_originals/` 에 보관한다.
- **2026-09-15 전 기능 검증** ([VERIFICATION_0915.md](docs/history/2026-09-15/VERIFICATION_0915.md)): 단위 테스트 87/87 · CLI 184/184 명령(격리 환경) · Web 210/210 요청(게스트·viewer·class1·admin·API 키·MCP 11도구+페더레이션·CSRF) · UI 배선 OK · Edge headless JS 오류 0. 발견·수정 2건: 잘못된/폐기된 API 키가 게스트로 강등되던 것 → 401, `mcp --client-config` 경로 이중 이스케이프. 하네스는 `tools/verify/` 에 있어 다른 환경에서 그대로 재실행.
- **2026-09-15 상세 분석 모드** ([ANALYSIS_MODE.md](docs/ANALYSIS_MODE.md)): `analysis_mode` 토글·`query --analyze`·`analyze`·Web 📊·MCP `wiki_analysis`·`GET /api/analysis`, 리포트 §0~§9+부록, 렌즈 규칙 표. 테스트 6개 추가.
- **2026-09-15 다른 RAG 연동·MCP 확장** ([RAG_FEDERATION.md](docs/RAG_FEDERATION.md)): 외부 소스 전송 stdio/http/rest, 검색 채널 `external_rag`(가상 청크 융합·inject·인용), 페더레이션 `mcp_federation`(`<source>__<tool>`, 재귀 방지), 플러그인 `plugins/mcp_tools/`, 도구 `wiki_sources`/`wiki_external_search`. 테스트 7개 추가(87/87), 하네스 CLI 184/184 · Web 210/210.
- **2026-09-15 설정 외부화**: `serve`/`mcp` 기본값 `config.json web_host/web_port/mcp_transport/mcp_host/mcp_port/mcp_url`, 기대 결과 포렌식 임계 `tuning.json forensic_near_miss_mult/term_candidates/term_targets/pin_confidence`, 원본 예시 `setup/security.example.json`·`agents.example.json`·`mcp_clients.example.json`, `install.*` 가 security/agents 도 생성, `check_env.py` 가 보안/에이전트/서버/재시도 설정 보고, `mcp --client-config` — 총람 [BRINGUP_GUIDE §3.2](docs/BRINGUP_GUIDE.md).
- 검증 환경: Python 3.14.7, 합성 모뎀 코퍼스 38문서·190청크. 단위/통합 테스트 **87개** 통과(`python -m unittest discover -s tests`): 프로바이더 PAT 헤더·Anthropic 게이트웨이·headless `.cmd`·인증/SSO/감사 + **권한 표·익명·API 키·CLI 게이트(test_auth) · 채널 빌드 독립성·doc_expand·LLM 재시도/실패 보고·기대 결과 포렌식·MCP HTTP/브리지(test_features_0914)**.
- 실제 엔드포인트: 로컬 Ollama(llama3.1)로 `models test --live`, `build --full --llm-graph` 진행 표시까지 확인. 실서버 스모크(`serve --host 0.0.0.0`): 게스트 질의/기대 결과 포렌식 200, 게스트 빌드 401(로그인 안내), `kh82.kim` 로그인 → 권한 표 조회 → 채널 리빌드 428→승인→job 완료 → API 키 발급 → `POST /mcp` initialize/`wiki_forensic` → 감사 로그. 사내 PAT 게이트웨이·opencode·Mango MCP 는 fake 서버/mock 으로 배선을 검증했고 실제 연결은 포팅 환경에서 `models test --live`, `mcp-source test` 로 확인.
- 2026-09-14 변경 (2차, [IMPLEMENTATION_PLAN_0914.md](docs/history/2026-09-14/IMPLEMENTATION_PLAN_0914.md)): (1) 역할 6단계·등급 7단계·admin 편집 권한 표·익명 viewer·CLI 게이트·API 키(SECURITY.md), (2) MCP Streamable HTTP(`/mcp`)·브리지·도구 `wiki_feedback`/`wiki_forensic`(MCP.md), (3) 채널별 빌드 `build fts|vector|graph`·`--channels`·`build_fts` 토글, (4) 문서 단위 확장 `doc_expand`(+튜닝 5개), (5) headless/LLM 재시도(`agents.json timeout_s/retries`, `llm_retries`)·`llm_report`, (6) 기대 결과 포렌식 `forensic expect`(FORENSIC.md), 제안 kind `pin/query_rule/tuning` 적용.
- 2026-09-14 변경 (1차): 빌드/질의 실시간 진행 표시와 "전체 리빌드가 starting… 에서 멈춤" 수정 · `llm_timeout`, PAT 게이트웨이 헤더 설정·`models test --live`·opencode Windows 실행 수정·`rerank_model`/`rerank_api_model` 분리, 로그인·역할·파괴적 작업 보호·스냅샷·감사 로그.
- 규모(5,000+50/일) 설계 근거와 남은 개선 항목은 [ANALYSIS_REPORT_0913.md](docs/history/2026-09-13/ANALYSIS_REPORT_0913.md) §1.4 참조. `docs/llmwiki_guide.html`(인터랙티브 가이드)은 2차 변경 이전 구조를 보여 주며, 2차 기능은 위 문서들이 기준이다.
