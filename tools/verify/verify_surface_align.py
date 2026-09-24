"""**CLI · Web UI · MCP** 세 창구가 같은 기능을 제공하는지 대조한다.

왜 있나: 기능은 보통 한 창구에서 먼저 만들어지고 나머지에 옮겨진다. 옮기는 것을 잊으면
"CLI 에서는 되는데 화면에는 없다" 가 쌓인다. 사용자는 셋 중 하나만 쓰므로 그 사람에게는
**그 기능이 없는 것**이다. 사람이 기억으로 맞추는 대신 표로 만들어 비교한다.

무엇을 보나:
  1. 기능(capability)마다 CLI 명령 · Web API · MCP 도구가 있는지 (아래 CAPS 표)
  2. 그 이름들이 **실제로 존재**하는지 (cli.py 서브커맨드 · server.py 경로 · mcp.py TOOLS)
  3. **거꾸로**: 코드에 있는 CLI 명령·Web 경로·MCP 도구가 **빠짐없이 표에 들어 있는지**
  4. 비워 둔 칸마다 **이유(note)가 적혀 있는지**
  5. MCP 도구가 모두 문서(MCP.md)에 적혀 있는지

3번이 2026-09-20 에 추가됐다. 그 전까지 이 하네스는 **표를 기준**으로만 봤다 — 표에 적지 않은
기능은 애초에 검사 대상이 아니었다. 그래서 "표에 없으니 통과" 가 성립했고, 실제로 CLI 명령 3개
(`fusion`·`system`·`time`)와 Web 경로 33개가 표 밖에 있었다. 이제는 **코드가 기준**이다:
새 명령이나 새 경로를 만들면 표에 줄을 더하기 전까지 이 하네스가 실패한다.

표기:
  · `cli`/`web`/`mcp` 칸에는 이름을 **공백으로 여러 개** 적을 수 있다 (첫 번째가 대표).
    같은 기능이 여러 경로로 갈라진 자리(`/api/doc` `/api/docs` `/api/doc_chunks`)를 한 줄로 묶기 위한 것이다.
  · `""` = 그 창구에는 **없어야 정상** — `note` 에 이유를 반드시 적는다 (이유가 없으면 실패).
  · `None` = 아직 없다 (정렬이 깨진 자리 → 실패로 보고).

실행:
    python tools/verify/verify_surface_align.py
    python tools/verify/verify_surface_align.py --md      # 표를 마크다운으로 (문서에 붙일 때)
    python tools/verify/verify_surface_align.py --inventory  # 코드에서 뽑은 전수 목록 (명령·액션·경로·도구)
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# 콘솔이 cp949 여도 한글·기호 출력에서 죽지 않게 (다른 verify_* 와 같은 처리, 2026-09-24)
try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# (기능, CLI 명령, Web API 경로, MCP 도구, 비고)
#   ""  = 없어야 정상 (비고에 이유)
#   None = 아직 없다 (정렬이 깨진 자리 → 실패로 보고)
CAPS = [
    ("질의 (RAG 답변)", "query", "/api/query", "wiki_query", ""),
    ("평가셋 신뢰도 점검 (오염·누락·표본)", "eval", "/api/eval/check", "",
     "CLI `eval --check` · Web Quality › 평가의 '신뢰도 점검' — 점수보다 먼저 본다 (docs/EVAL_TRIAL.md §1). "
     "MCP 에 두지 않는 것은 평가·trial 과 같은 이유: 붙은 LLM 이 자기 설정을 평가해 스스로 바꾸면 HITL 이 아니다"),
    ("요청 단위 설정 오버라이드 (timeout·retry·모델…)", "query", "/api/query", "wiki_query",
     "세 창구가 **같은 화이트리스트**(auth.filter_overrides)를 지난다 — CLI `query --set llm_timeout=7,llm_retries=1` · Web `overrides` · MCP `wiki_query(overrides=…)`. "
     "역할 단축키(`answer_timeout_s`)도 같은 이름. 모르는 키는 조용히 버리지 않고 400, 역할이 못 쓰는 키는 403 (llmwiki/auth.py)"),
    ("검색 (채널 조합 or/and/rrf · 필수/제외 · 문서 유형 필터)", "search", "/api/search", "wiki_search",
     "세 창구가 같은 엔진(retrieval.channel_search) — CLI `search fts,vector --mode and --doc-types issue,cl` · "
     "Web Ask › 채널 검색(채널 조건표 · 유형 칩 · 최근 검색어 · 발췌 강조) · MCP `channels`/`mode`/`require`/`exclude`/`doc_types`. "
     "문서 유형은 **거르는** 조건이다 (질의의 doc_types 는 가중치) — 동등성은 tests/test_search_filters.py"),
    ("질의 해부 (LLM 없이)", "inspect", "/api/debug/query", "wiki_inspect", "토큰화·규칙 확장·시간 표현·라우팅·pin — CLI `inspect` · Web Ask › 디버그"),
    ("시간 표현 해석 (지난주 · 3분기 · 2026-09 …)", "time", "/api/time", "",
     "CLI `time \"지난주\"` ↔ Web Ask › 디버그의 시간 범위 줄이 쓰는 `GET /api/time` — 둘 다 llmwiki/timeparse.py. "
     "MCP 에 낱개 도구로 두지 않는다: `wiki_inspect` 의 해부 결과에 같은 값(`time`)이 들어 있어 도구를 하나 더 늘릴 이유가 없다"),
    ("단계 재실행", "rerun", "/api/query/rerun /api/rerun", "wiki_rerun",
     "`POST /api/query/rerun` 이 실행이고 `GET /api/rerun` 은 화면이 trace 각 줄에 ⟲ 를 달 수 있게 재시작점 표와 저장된 중간 결과 유무를 준다"),
    ("지난 요청 목록·상세", "requests", "/api/requests /api/request", "wiki_requests",
     "`/api/requests` 는 목록, `/api/request?id=` 는 한 건(`brief=1` 이면 trace 를 빼고 답변 요약만). 남의 요청은 '전체 조회' 권한이 있어야 보인다"),
    ("요청 원장 — 서버로의 **모든** 요청(거절·시간초과·취소·중단 포함)", "ledger", "/api/ledger", "wiki_requests",
     "CLI `ledger list|show <token>|stats|prune` 은 **서버가 꺼져 있어도** data/ledger 파일을 직접 읽는다(사후 분석·포팅용), 실행 중 서버는 `server ledger`. "
     "Web 은 Observability › 📋 요청(전체) — 진행 중 작업·요청 프로파일·질의 로그를 한 목록으로 합치고 한 건을 누르면 진행·답변·단계·로그·**같은 시각의 요청**을 한 패널에서 본다. "
     "MCP 는 도구를 늘리지 않고 `wiki_requests(source=\"ledger\", status=…, token=…)` 으로 같은 것을 준다 — 붙은 LLM 의 질문('전에 물어봤나'/'왜 실패했나')이 한 도구에서 답이 되게. "
     "권한은 새로 만들지 않고 기존 작업 `requests all` 을 그대로 쓴다. 설명: docs/REQUEST_LEDGER.md"),
    ("종류(질의·검색·MCP·CLI)별 동시/대기/속도 한도", "server", "/api/admin/server", "",
     "CLI `server limits set concurrency.classes.search.max_parallel=6 rate_limit.classes.search.per_user_per_min=90` ↔ Web Observability › 서버 모니터. "
     "질의 상한을 전체 슬롯보다 작게 잡으면 그 차이가 **빠른 검색의 예약 슬롯**이 된다(별도 예약 설정을 만들지 않는 이유). "
     "MCP 에는 두지 않는다 — 설정 변경은 MCP 밖이라는 기존 원칙(붙은 LLM 이 자기 한도를 바꾸게 할 수 없다)"),
    ("파라미터 스윕 (값별 단계 비교)", "sweep", "/api/sweep", "wiki_sweep", ""),
    ("규칙 사전 설명 (이 말은 어떻게 퍼지나)", "rules", "/api/query_rules/explain", "wiki_rules", "읽기 전용 explain/test 만 — 편집은 사람이 화면/CLI 에서"),
    ("그래프 진단 프로파일", "graph", "/api/graph/profile", "wiki_graph_profile", ""),
    ("상세 분석 리포트", "analyze", "/api/analysis", "wiki_analysis", ""),
    ("포렌식 (왜 못 찾았나) · 문제 건만 거르기", "forensic", "/api/forensic /api/forensics /api/forensics/summary", "wiki_forensic",
     "`/api/forensic` 은 한 건 분석, `/api/forensics` 는 목록, `/api/forensics/summary` 는 누적 소견 — CLI `forensic last|list|summary|<id>` 와 같은 갈래. "
     "**목록의 기본은 '문제 건만'** — CLI `forensic list --only problems|all|<판정>` ↔ Web `?only=problems` ↔ 화면의 판정 칩. "
     "기록의 90%가 정상 건이라(실측 1,122 중 1,025) 전부 보여 주면 볼 이유가 있는 줄이 묻힌다 (2026-09-20)"),
    ("엔티티·관계 조회", "entity", "/api/entity", "wiki_entity", ""),
    ("그래프 이웃", "graph", "/api/graph", "", "엔티티 상세(wiki_entity)와 유사 문서(wiki_related)가 덮는다"),
    ("무리(커뮤니티) 상세", "graph", "/api/community", "wiki_community", "2026-09-24 — Web 그래프 탭 '무리 하나' 보기 · CLI graph community --community N"),
    ("유사 문서 + 연결", "", "/api/search", "wiki_related", "MCP 전용 — 이슈 분석 결과로 관련 문서를 찾는 용도"),
    ("문서 목록 · 전문 + 메타", "docs", "/api/doc_chunks /api/doc /api/docs", "wiki_doc",
     "`/api/docs` 는 목록(유형·ext_id·날짜), `/api/doc?id=` 는 한 건의 전체 모습(메타·청크·관계·엔티티 — MCP `wiki_doc` 과 **같은 함수** querydebug.doc_detail), "
     "`/api/doc_chunks` 는 청크만. 셋 다 문서 접근 제어(docacl)를 지난다"),
    ("문서 원문(청크)", "", "/api/chunk", "", "wiki_doc 이 문서 단위로 덮는다"),
    ("위키 페이지", "wiki", "/api/wiki/list /api/wiki/page", "", "위키는 색인에서 생성된 뷰 — MCP 는 wiki_doc 으로 원문을 본다. `list` 는 목록, `page` 는 본문"),
    ("색인 상태·통계", "stats", "/api/status", "wiki_status", ""),
    ("규모 추정 (목표 문서 수 → 디스크·시간·토큰)", "system", "/api/system", "",
     "CLI `system [--target-docs --daily-new --horizon-days]` ↔ Web 옵저빌리티 › 시스템 의 규모 추정 — 둘 다 Pipeline.system_info(). "
     "지금 상태가 아니라 **가정을 넣어 계산하는 것**이라 MCP 에 두지 않는다 (붙은 LLM 이 쓸 자리가 없다). 지금 상태는 wiki_status"),
    ("건강 점검", "health", "/api/health", "", "운영 점검 — 외부 LLM 이 아니라 사람이 본다"),
    ("외부 소스 직접 검색", "mcp-source", "/api/mcp_sources", "wiki_external_search", ""),
    ("빌드", "build", "/api/build", "", "MCP 로는 색인을 바꾸지 않는다 (외부 LLM 에 쓰기 권한을 주지 않음)"),
    ("평가", "eval", "/api/eval", "", "오래 걸리는 배치 — CLI/Web 잡으로만"),
    ("비교에 쓸 **과거 질의 고르기**", "trial", "/api/eval/candidates", "",
     "CLI `trial candidates [--days --limit --only]` 로 번호를 보고 `trial run --pick 773,772` ↔ "
     "Web Quality › Trial 비교의 `질의 고르기…` 목록(체크박스). 기간·건수로 뭉뚱그리는 `--source queries` 와 달리 "
     "**'이 질문들' 을 그대로** 쓴다 — 비교하고 싶은 질의는 대개 몇 개로 정해져 있다 (2026-09-20). "
     "MCP 에는 두지 않는다: trial 자체가 MCP 밖이라 고르는 화면만 있어도 쓸 데가 없다"),
    ("Trial 비교", "trial", "/api/trials /api/trial", "", "〃 (`/api/trials` 목록·실행, `/api/trial?id=` 한 건)"),
    ("융합 방식 비교 (RRF ↔ weighted ↔ …)", "fusion", "/api/fusion/compare", "",
     "CLI `fusion show|compare` ↔ Web 🧭 Pipeline 의 융합 비교 — 저장된 질의 위에서 융합 방식만 바꿔 순위가 어떻게 달라지는지 본다. "
     "설정 실험이라 MCP 에 두지 않는다 (같은 이유로 sweep 만 예외적으로 열려 있다)"),
    ("규칙 사전 (질의 확장)", "rules", "/api/query_rules", "", "지식 편집은 사람이 화면/CLI 에서"),
    ("규칙 효과 측정", "rules", "/api/query_rules/effect", "", "CLI `rules effect` · Web Settings › 질의 규칙 사전 — 관측이라 MCP 는 두지 않는다"),
    ("그래프 규칙", "rules", "/api/rules", "", "〃"),
    ("Pin (고정 근거)", "pin", "/api/pins", "", "〃"),
    ("프리셋", "preset", "/api/presets", "", "〃"),
    ("튜닝 값", "tuning", "/api/tuning", "", "〃"),
    ("프롬프트", "prompts", "/api/prompts", "", "〃"),
    ("설정(config.json) · .env 가시성", "config", "/api/config /api/env", "",
     "설정 변경은 admin 화면/CLI 에서만. `GET /api/env` ↔ CLI `config env` — 값은 마스킹해서 **어느 키가 어디서 왔는지**만 보여 준다 (docs/SETTINGS_SYNC.md)"),
    ("모델·프로바이더", "models", "/api/models", "", "〃"),
    ("headless 에이전트 정의 (agents.json)", "", "/api/agents", "",
     "Web Settings › 에이전트에서 편집한다(`command`·`prompt_mode`·`timeout_s`·`retries`). "
     "CLI 전용 명령은 두지 않았다 — 위치는 `config paths` 가 알려 주고 편집은 파일을 직접 여는 편이 낫다(JSON 한 덩어리라 한 줄씩 넣는 CLI 가 오히려 불편하다). "
     "MCP 에는 두지 않는다: LLM 실행 방법 자체를 붙은 LLM 이 바꾸게 할 수 없다 — docs/HEADLESS.md"),
    ("앙상블 (역할 단위 다중 LLM)", "models", "/api/models", "",
     "설정 변경은 admin 화면/CLI 에서만 — CLI `models ensemble show|set`, Web 은 **🧭 Pipeline › 앙상블 한 곳**에서만 편집한다 "
     "(Settings › 모델 의 역할 표에는 상태 줄과 '앙상블 설정으로' 버튼만 둔다 — 같은 값을 두 화면에서 받으면 어느 쪽이 적용됐는지 알 수 없다). "
     "둘 다 역할 모델(멤버가 비운 칸이 상속하는 값)을 함께 보여 준다: CLI 는 '역할 모델:' 줄, Web 은 `/api/models` 의 `ensemble.<role>.role`. "
     "MCP 에는 두지 않는다: 붙은 LLM 이 자기를 부르는 LLM 구성을 바꾸게 할 수 없다"),
    ("앙상블 실패 시 역할 모델로 되돌리기", "models", "/api/models", "",
     "앙상블을 켜면 역할 모델은 **불리지 않으므로**, 멤버가 min_results 를 못 채우면 그 역할은 답을 못 낸다. "
     "`ensemble.fallback_role_model`(켜짐 기본)로 역할 모델이 한 번 더 돌고, `fallback_mode` 로 **무엇을 받을지** 고른다 — "
     "auto(살아남은 답이 있으면 취합) · merge(되도록 취합) · rerun(항상 원래 프롬프트). "
     "CLI `models ensemble show|set --fallback --fallback-mode`, Web 🧭 Pipeline › 앙상블 의 「실패 시」 줄. "
     "MCP 는 설정 도구를 두지 않는(위 행과 같은 이유) 대신 `overrides.llm_roles.<role>.ensemble` 로 요청 단위 지정과 "
     "결과 `meta.ensemble.fallback` 관측이 된다 (2026-09-20)"),
    ("앙상블로 돌았는지 **보이기** (멤버·취합 내역)", "query", "/api/query", "wiki_query",
     "세 창구가 같은 trace 를 본다 — `answer_llm` 단계 meta 의 `ensemble`(멤버별 모델·프로바이더·ms·토큰·성공 여부 + 취합기). "
     "CLI `query --trace` 는 멤버별 줄로, Web 은 그 줄 오른쪽에 `앙상블 n/m+취합` **글씨**(배경·테두리 없음 — 알약이면 워터폴에서 "
     "막대 조각으로 읽힌다)와 펼쳤을 때 표로, MCP 는 응답 trace 에 같은 값으로. "
     "**폴백으로 역할 모델이 대신 답했으면 그것도 같이 보인다** — Web 은 빨간 `폴백 · 역할 모델이 답함` 배지 + 표의 '폴백' 줄, "
     "CLI 는 '폴백 … << 이 답은 역할 모델이 만들었습니다' 줄, MCP 는 `meta.ensemble.fallback`. "
     "예전에는 `model` 이 `llama3.1+llama3.1+llama3.1` 처럼 `+` 로 이어 붙은 것을 보고 **사람이 유추**해야 했다 (2026-09-20)"),
    ("스케줄", "schedule", "/api/schedule", "", "서버 운영 — 화면/CLI"),
    ("사용자·권한", "users", "/api/auth/users", "", "보안 — 화면/CLI"),
    ("로그인·세션 (로컬 계정 · SSO · 비밀번호 변경 · 역할 미리보기)", "users",
     "/api/auth/login /api/auth/logout /api/auth/me /api/auth/password /api/auth/preview", "",
     "Web 로그인 화면이 쓰는 경로들. CLI 는 `--user`/`--password`(또는 `LLMWIKI_PASSWORD`)로 매 실행마다 인증하므로 세션이 없고, "
     "계정 관리는 `users add|passwd|set-role` 이다. `/api/auth/preview` 는 admin 이 **다른 역할의 눈으로** 화면을 보는 것 — 권한 설정을 바꾸기 전에 확인하는 용도. "
     "MCP 는 세션 대신 API 키로 인증한다 (docs/SECURITY.md · docs/MCP.md)"),
    ("보안 정책 · 감사 로그", "security", "/api/security /api/audit", "",
     "CLI `security show|init|perms|docacl|audit` ↔ Web Settings › 보안. `/api/audit` 는 거부·파괴적 작업 기록이고 admin 전용이다 — "
     "감사 기록을 붙은 LLM 이 읽게 하지 않는다 (누가 무엇을 거부당했는지가 그대로 드러난다)"),
    ("API 키", "apikey", "/api/apikeys", "", "〃"),
    ("문서 접근 제어 (역할별 근거 차단)", "security", "/api/docacl", "", "CLI `security docacl show|check --role <역할>|init` · Web Settings › 보안 › 문서 접근 제어 — 규칙 편집은 admin 전용이라 MCP 에 두지 않는다. 단, MCP 도구(wiki_query·wiki_search·wiki_doc·wiki_related)는 호출자의 역할로 **적용**받는다 (llmwiki/docacl.py)"),
    ("스냅샷", "snapshot", "/api/snapshot", "", "〃"),
    ("서버 모니터·제한", "server", "/api/admin/server", "", "〃"),
    ("진행 중 작업·취소·진행률", "server", "/api/activity /api/progress /api/jobs/", "",
     "CLI `server requests|cancel` ↔ Web 진행 패널. `/api/progress[/<token>]` 과 `/api/jobs/<token>` 은 화면이 **긴 작업의 진행 단계를 따라가는** 경로다 "
     "(빌드·평가·trial — 사람이 보는 동안만 의미가 있어 CLI 에는 없다. CLI 는 그 자리에서 `--trace` 로 같은 단계를 찍는다)"),
    ("로그", "logs", "/api/logs", "", "〃"),
    ("외부 RAG 소스", "mcp-source", "/api/mcp_sources", "wiki_sources", ""),
    ("설정 묶기·되돌리기 (포팅: 한 폴더로 모으고 LLMWIKI_CONF_DIR 로 가리킨다)", "config", "", "",
     "CLI `config bundle --out <폴더> | --from <폴더>` · `config paths`(파일별 실제 출처). "
     "파일 시스템 폴더를 만드는 동작이라 Web/MCP 에 두지 않는다 — 서버가 임의 경로에 쓰게 하면 안 된다. "
     "Web 에서는 Settings 의 각 파일 편집으로, 옮기는 일은 터미널에서 — docs/PORTING.md"),
    ("운영 통계 (빌드·질의량·지연·토큰·품질·사용자·디스크·임베딩)", "stats", "/api/opstats", "wiki_status",
     "CLI `stats --full [--days 7] [--section build|queries|latency|tokens|quality|users|storage|embed]` ↔ "
     "Web 옵저빌리티 › 시스템 의 **운영 통계** ↔ MCP `wiki_status(full=true, days, sections)`. "
     "셋 다 llmwiki/opstats.py 한 곳을 쓴다 (읽기 전용 · 기간과 표본 상한이 있다). "
     "`users` 절만 admin 전용이라 그 밖에는 `redacted` 로 가려 내보낸다"),
    ("운영 통계 추세 (일·주·월)", "stats", "/api/opstats", "wiki_status",
     "CLI `stats --full --section trend [--bucket day|week|month] [--trend-days N]` ↔ "
     "Web 옵저빌리티 › 시스템 › 추세(일간·주간·월간 버튼 + 막대·꺾은선 차트) ↔ MCP `wiki_status(full=true, sections=[\"trend\"], bucket=…)`. "
     "한 시점의 p95 로는 '나아지나 나빠지나' 에 답할 수 없어 구간별 질의량·지연·토큰·빌드·근거 부족률을 낸다. "
     "기간은 묶음에 맞춰 자동으로 늘어난다(주간 12주·월간 1년) — 월간을 7일치로 그리면 막대가 하나뿐이라 뜻이 없다. "
     "터미널에는 같은 값을 스파크라인으로 찍는다 (2026-09-20)"),
    ("Trial 문항 원천 (평가셋 / 실제 질의 이력) · 단계별 비교 · 채점 불가 표시", "trial", "/api/trials", "",
     "CLI `trial run --source evalset|queries [--days 7 --limit 30 --only negative|feedback|insufficient]` ↔ "
     "Web Quality › Trial 비교의 '문항 원천' 줄. 비교는 단계별 표(질의 1건당 ms·토큰·호출수, 달라진 단계 우선)를 함께 낸다. "
     "**정답이 없는 문항(질의 이력)이면 hit@k·MRR·term_recall 을 0 이 아니라 공백(`—`)으로 낸다** — 0 으로 보이면 "
     "완전히 실패한 설정으로 읽힌다. 저장할 때(run_trial)와 읽을 때(list/get) 양쪽에 같은 규칙을 걸어 "
     "CLI `trial list` · Web 목록 · 비교 화면이 같은 값을 보인다. 원천이 섞이면 비교 전에 경고한다 (2026-09-20). "
     "MCP 에는 두지 않는다 — trial 은 평가셋 전체를 LLM 으로 돌리는 **비싼 실행**이라 사람이 시작해야 한다. "
     "Web 에만 있는 **A/B 비교(기준↔변경 한 번에)** 는 CLI 의 `trial run` 두 번 + `trial compare` 와 같은 일을 한 번에 하는 것이라 새 기능이 아니다"),
    ("관리자 초기화 (데이터·빌드 / 설정 / 로그·이력)", "reset", "/api/reset", "",
     "CLI `reset data|settings|logs [--apply]` ↔ Web 설정 › 시스템 › 초기화. **미리보기가 기본**이고 `--apply`/버튼으로만 실행된다. "
     "MCP 에는 두지 않는다 — 붙어 있는 LLM 이 색인이나 설정을 지울 수 있으면 안 된다 (파괴적 작업은 사람의 확인 문구 + 비밀번호 뒤에만)"),
    ("그래프 빌드 규칙 (엔티티 사전 · 값 종류 · 관계 어휘 · 점검 · 문장 시험)", "graph-rules", "/api/graph_rules", "wiki_graph_rules",
     "CLI `graph-rules show|types|lint|test|add-entity|add-alias|fill-defaults` ↔ Web 지식 › 그래프 규칙 ↔ MCP `wiki_graph_rules`(읽기 전용). "
     "질의 확장 규칙(`rules`/query_rules.json)과 짝이다 — 이쪽은 **빌드 때** 그래프를 만드는 규칙"),
    ("자가진화 제안 · 적용 · 거절 · 검토", "evolve",
     "/api/evolve/proposals /api/evolve/propose /api/evolve/apply /api/evolve/reject /api/evolve/review", "wiki_propose",
     "CLI `evolve list|propose|apply|reject|review` ↔ Web Evolve 탭의 카드 버튼. MCP 는 **제안까지만**(`wiki_propose`) — "
     "적용·거절은 사람이 누른다(HITL). 적용은 파괴적 등급이라 확인 문구 + 비밀번호를 다시 받는다 (docs/EVOLVE.md)"),
    ("자가진화 제안 보기", "evolve", "/api/evolve/status", "wiki_evolve", "CLI `evolve status|list|kinds` · Web Evolve 탭 · MCP 는 읽기 전용(적용·거절은 사람이)"),
    ("자가진화 제안 설명 (무엇이·어디서·어떻게 바뀌나·영향·점검)", "evolve", "/api/evolve/describe", "wiki_evolve",
     "CLI `evolve show <id>` ↔ Web Evolve 탭 제안 카드(목록에 자동으로 붙음) ↔ MCP wiki_evolve(id=<번호> 또는 explain=true). "
     "세 창구가 llmwiki/proposal_explain.py 한 곳을 쓴다 — payload JSON 원문만 보여 주던 문제(2026-09-19)"),
    ("자가진화 자동 적용 (손으로 1회)", "evolve", "/api/evolve/auto_apply", "", "CLI `evolve auto-apply [--dry-run]` · Web Evolve 탭의 '자동 적용 실행' · 스케줄러 evolve op=auto_apply — 적용은 사람 승인 경로라 MCP 에 두지 않는다"),
    ("메모리 (스스로 배운 것: 부스트·감쇠·에피소드)", "memory", "/api/memory", "",
     "운영 — 화면/CLI. CLI `memory status|boosts|decaying|episodes [--only --q] |decay|consolidate` ↔ Web Evolve › 메모리의 같은 네 표. "
     "MCP 에는 두지 않는다 — 붙은 LLM 이 자기 피드백의 효과를 보고 스스로 강화하면 HITL 이 아니다 (docs/EVOLVE.md §2.5)"),
    ("피드백", "evolve", "/api/feedback", "wiki_feedback", ""),
    ("최적화 자료 묶음 · 가이드", "optimize", "/api/optimize/bundle /api/optimize/guide", "",
     "파일로 내려받아 LLM 에 첨부. `bundle` 은 가이드+지금 설정+질의 실측을 한 파일로, `guide` 는 자동 생성 가이드(`arch doc`)만 — CLI 는 `optimize` 와 `arch doc`"),
    ("아키텍처 지도", "arch", "/api/architecture", "", "〃"),
    ("단계별 시간 제한", "arch", "/api/limits", "", "CLI `arch limits` · Web trace 의 실측 옆 '≤ 제한' 과 🧭 Pipeline 단계 상세 — 읽기 전용 관측"),
    ("질의 로그 (누가 무엇을 물었나)", "requests", "/api/queries /api/query_trace /api/query_users", "",
     "CLI `requests queries [--user|--origin|--q]` · `requests users` · Web Observability › 질의·로그 — 관측이라 MCP 는 두지 않는다. "
     "`/api/query_trace?id=` 는 그때의 근거와 컨텍스트(남의 것이면 403), `/api/query_users` 는 사용자별 집계로 **admin 전용**이다"),
    ("모델 자동 매핑 (연결되는 모델 → 역할)", "models", "/api/models/automap", "", "CLI `models automap [--live] [--apply]` · Web Settings › 모델·프로바이더 — 설정 변경은 admin 화면/CLI 에서만"),
    ("코퍼스 계약 점검 · 문서 유형", "corpus", "/api/corpus/lint /api/corpus/types", "",
     "빌드 전 점검 — 화면/CLI. `/api/corpus/types` ↔ CLI `corpus types` 는 이 코퍼스에 실제로 있는 `doc_type` 목록으로, 검색 필터 칩과 질의 가중치가 같은 목록을 쓴다"),
    ("임베딩 현황", "embed", "/api/embed/report", "", "〃"),
    ("사전 계산", "precompute", "/api/precompute", "", "〃"),
    ("워처", "watch", "/api/watch", "", "〃"),
    ("유지보수", "maintenance", "/api/maintenance", "", "〃"),
    ("협업 채팅·게시판", "", "/api/collab", "", "Web 전용 부수 기능 (토글 collab)"),
    ("계정별 화면 프로파일 (테마·토글·고정 탭·분할)", "", "/api/profile", "",
     "Web 전용 — 저장 대상이 **그 사람의 화면 상태**라 CLI·MCP 에 대응물이 없다(`data/profiles.json`). "
     "게스트는 서버에 저장하지 않고 브라우저에만 남는다. 서버 공용 설정은 바뀌지 않는다 (docs/WEB_UI.md §4)"),
    ("테마 목록", "", "/api/themes", "",
     "Web 전용 — `static/themes/themes.json` 을 읽어 헤더 셀렉터를 채운다. 화면 표현이라 다른 창구에 뜻이 없다"),
    ("화면에서 CLI 실행", "", "/api/cli", "",
     "Web 관리 › 콘솔. **터미널을 못 여는 사람**(원격·공용 PC)을 위한 창구라 CLI 쪽 대응물이 있을 수 없고, "
     "MCP 에는 두지 않는다 — 붙은 LLM 에게 임의 명령 실행을 열어 주는 것이기 때문이다. "
     "권한은 우회되지 않는다: 같은 등급표를 지나고 rebuild/destructive 등급이면 확인 문구 + 비밀번호를 다시 받는다 (docs/SECURITY.md)"),
    ("서버 기동", "serve", "", "", "명령 자체가 서버를 띄운다"),
    ("MCP 서버 기동·점검", "mcp", "", "", "〃"),
]

# 코드에는 있지만 어느 기능 줄에도 적지 않기로 한 것 — 반드시 이유를 남긴다.
# (표를 억지로 채우는 대신 "왜 표에 없는가" 를 여기에 적는다.)
EXEMPT = {}


def cli_commands():
    src = open(os.path.join(ROOT, "llmwiki", "cli.py"), encoding="utf-8").read()
    return set(re.findall(r'\.add_parser\(\s*"([a-zA-Z0-9_\-]+)"', src))


def cli_actions():
    """명령마다 `action` 위치 인자의 choices — `rules show|add|test…` 같은 2단계 이름.

    명령 이름만 맞춰서는 "CLI 에도 있다" 가 거짓이 될 수 있다(명령은 있는데 그 동작이 없는 경우).
    정렬표는 명령 단위지만, 목록화(`--inventory`)와 문서 대조에는 이 층이 필요하다.
    """
    src = open(os.path.join(ROOT, "llmwiki", "cli.py"), encoding="utf-8").read()
    ms = list(re.finditer(r'sub\.add_parser\(\s*"([a-zA-Z0-9_\-]+)"', src))
    out = {}
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(src)
        blk = src[m.end():end]
        a = re.search(r'add_argument\(\s*"action"[^)]*?choices=\[([^\]]*)\]', blk, re.S)
        out[m.group(1)] = re.findall(r'"([^"]+)"', a.group(1)) if a else []
    return out


def api_paths():
    """**라우팅 조건에서** 경로를 뽑는다 — 주석이나 설명 문자열이 아니라 실제로 처리되는 경로만.

    `u.path == "/api/x"` · `u.path.startswith("/api/x")` · `u.path in ("/api/x", …)` 세 모양을 읽는다.
    """
    src = open(os.path.join(ROOT, "llmwiki", "web", "server.py"), encoding="utf-8").read()
    p = set(re.findall(r'u\.path\s*==\s*["\'](/api/[a-zA-Z0-9_/\-]+)["\']', src))
    p |= set(re.findall(r'u\.path\.startswith\(\s*["\'](/api/[a-zA-Z0-9_/\-]+)', src))
    for m in re.finditer(r'u\.path\s+in\s+\(([^)]*)\)', src):
        p |= set(re.findall(r'["\'](/api/[a-zA-Z0-9_/\-]+)["\']', m.group(1)))
    return p


def mcp_tools():
    from llmwiki.mcp import TOOLS
    return {t["name"] for t in TOOLS}


def _names(field):
    """표의 한 칸 → 이름 목록. 공백으로 여러 개 적을 수 있다 (첫 번째가 대표)."""
    if field is None:
        return None
    return [x for x in str(field).split() if x]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CLI · Web · MCP 정렬 대조")
    ap.add_argument("--md", action="store_true", help="마크다운 표로 출력")
    ap.add_argument("--inventory", action="store_true", help="코드에서 뽑은 전수 목록만 출력하고 끝낸다")
    ns = ap.parse_args(argv)

    cmds, paths, tools = cli_commands(), api_paths(), mcp_tools()
    acts = cli_actions()

    if ns.inventory:
        print("== CLI 명령 %d (괄호 안은 action choices) ==" % len(cmds))
        for c in sorted(cmds):
            a = acts.get(c) or []
            print("  %-14s %s" % (c, "|".join(a) if a else ""))
        print("\n== Web /api 경로 %d ==" % len(paths))
        for p_ in sorted(paths):
            print("  %s" % p_)
        print("\n== MCP 도구 %d ==" % len(tools))
        for t in sorted(tools):
            print("  %s" % t)
        return 0

    problems = []
    rows = []
    for cap, cli, web, mcp, note in CAPS:
        def mark(field, pool, kind):
            names = _names(field)
            if names is None:
                problems.append((cap, "%s 창구가 없다 (정렬 깨짐)" % kind))
                return "**없음**"
            if not names:
                # 비워 둔 칸은 **이유가 있어야** 한다. 이유 없이 비면 그 자리가 곧 정렬이 깨진 곳이다.
                if not note:
                    problems.append((cap, "%s 칸을 비웠는데 이유(note)가 없다" % kind))
                    return "– ⚠"
                return "–"
            out = []
            for name in names:
                ok = name in pool
                if not ok and kind == "Web":
                    ok = any(p_ == name or p_.startswith(name.rstrip("/") + "/") for p_ in pool)
                if not ok:
                    problems.append((cap, "%s `%s` 가 실제로 없다" % (kind, name)))
                    out.append("`%s` ⚠" % name)
                else:
                    out.append("`%s`" % name)
            return " ".join(out)
        rows.append((cap, mark(cli, cmds, "CLI"), mark(web, paths, "Web"), mark(mcp, tools, "MCP"), note))

    # ── 거꾸로: 코드에 있는 것이 표에 빠짐없이 들어 있는가 ──────────────────────
    # 표를 기준으로만 보면 "표에 안 적은 기능" 은 영영 검사되지 않는다. 그래서 코드를 기준으로 한 번 더 본다.
    listed_cli, listed_web, listed_mcp = set(), set(), set()
    for _c, cl, w, m, _n in CAPS:
        listed_cli |= set(_names(cl) or [])
        listed_web |= set(_names(w) or [])
        listed_mcp |= set(_names(m) or [])

    for c in sorted(cmds - listed_cli - set(EXEMPT)):
        problems.append(("(표 밖)", "CLI 명령 `%s` 가 정렬표에 없다 — 줄을 더하거나 EXEMPT 에 이유를 적어라" % c))
    for p_ in sorted(paths):
        if p_ in EXEMPT:
            continue
        if any(p_ == w or p_.startswith(w.rstrip("/") + "/") for w in listed_web):
            continue
        problems.append(("(표 밖)", "Web 경로 `%s` 가 정렬표에 없다 — 줄을 더하거나 EXEMPT 에 이유를 적어라" % p_))
    for t in sorted(tools - listed_mcp - set(EXEMPT)):
        problems.append(("(표 밖)", "MCP 도구 `%s` 가 정렬표에 없다" % t))

    # MCP 도구가 문서에 적혀 있는가
    mcpdoc = open(os.path.join(ROOT, "docs", "MCP.md"), encoding="utf-8").read()
    undoc = sorted(t for t in tools if t not in mcpdoc)
    for t in undoc:
        problems.append(("(문서)", "MCP 도구 `%s` 가 docs/MCP.md 에 없다" % t))

    if ns.md:
        print("| 기능 | CLI | Web API | MCP 도구 | 비고 |")
        print("|---|---|---|---|---|")
        for r in rows:
            print("| %s | %s | %s | %s | %s |" % r)
    else:
        print("%-24s %-14s %-26s %-16s %s" % ("기능", "CLI", "Web API", "MCP", "비고"))
        print("-" * 110)
        for cap, cli, web, mcp, note in rows:
            print("%-24s %-14s %-26s %-16s %s" % (cap[:24], cli[:14], web[:26], mcp[:16], note[:34]))

    n_web_listed = sum(len(_names(w) or []) for _c, _cl, w, _m, _n in CAPS)
    print("\n기능 %d개 · CLI 명령 %d · Web 경로 %d(표에 기재 %d) · MCP 도구 %d"
          % (len(CAPS), len(cmds), len(paths), n_web_listed, len(tools)))
    tri = sum(1 for _c, cl, w, m, _n in CAPS if cl and w and m)
    print("세 창구 모두 제공: %d개 · MCP 는 읽기 전용이라 의도적으로 뺀 것: %d개"
          % (tri, sum(1 for _c, _cl, _w, m, n in CAPS if not m and n)))
    print("전수 대조: CLI %d/%d · Web %d/%d · MCP %d/%d 가 표에 들어 있다"
          % (len(cmds & listed_cli), len(cmds),
             sum(1 for p_ in paths if any(p_ == w or p_.startswith(w.rstrip("/") + "/") for w in listed_web)), len(paths),
             len(tools & listed_mcp), len(tools)))
    if problems:
        print("\n어긋난 곳 %d건" % len(problems))
        for cap, why in problems:
            print("  %-24s %s" % (cap[:24], why))
    print("\nRESULT %s" % ("PROBLEMS" if problems else "OK"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
