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
mkdir -p corpus
echo
python3 setup/check_env.py
echo
echo "다음 단계:  python3 -m llmwiki build --full --trace   그리고   python3 -m llmwiki serve"
