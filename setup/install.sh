#!/usr/bin/env bash
# ===== LLM Wiki 설치 (macOS / Linux) =====
# 사용법: bash setup/install.sh          (현재 python3 사용)
#         bash setup/install.sh venv     (.venv 생성 후 설치)
set -e
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
if [ "$1" = "venv" ]; then
  [ -d .venv ] || python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install -r requirements.txt
python3 -m pip install anthropic 2>/dev/null || echo "(anthropic SDK 설치 생략 — raw HTTP 로 동작)"
[ -f config.json ] || { cp setup/config.example.json config.json; echo "config.json 생성됨 - 기본 corpus_dirs 는 샘플 setup/sample_corpus_modem 입니다. 실제 문서는 corpus/ 에 넣고 corpus_dirs 를 수정하세요"; }
[ -f .env ] || { cp setup/.env.example .env; echo ".env 생성됨 - API 키를 입력하세요 (선택)"; }
[ -f security.json ] || { cp setup/security.example.json security.json; echo "security.json 생성됨 - 공개(serve --host 0.0.0.0) 전에 admin 계정을 만드세요: python3 -m llmwiki users add <id> --role admin"; }
[ -f agents.json ] || { cp setup/agents.example.json agents.json; echo "agents.json 생성됨 - headless 에이전트(opencode 등) 명령·재시도 정책"; }
[ -f server.json ] || { cp setup/server.example.json server.json; echo "server.json 생성됨 - 동시 사용자 동시성·대기열·속도 제한 (docs/CONCURRENCY.md)"; }
[ -f schedule.json ] || { cp setup/schedule.example.json schedule.json; echo "schedule.json 생성됨 - 주기 작업 17가지 예시 (증분 빌드만 켜져 있음; schedule list 로 확인, docs/SCHEDULER.md)"; }
[ -f models.json ] || { cp setup/models.example.json models.json; echo "models.json 생성됨 - 사용 가능한 LLM/임베딩 모델 목록 (python3 -m llmwiki models list)"; }
mkdir -p corpus
echo
python3 setup/check_env.py
echo
echo "다음 단계:  python3 -m llmwiki build --full --trace   그리고   python3 -m llmwiki serve"
