#!/usr/bin/env bash
# 증분 빌드 스케줄 (cron 예시). 프로젝트 루트에서 실행되며 파일 락으로 중복 실행을 막는다.
#   crontab -e  →  30 2 * * *  /path/to/llm-wiki-rag-selfevolving/setup/schedule_build.sh >> /path/to/logs/cron.log 2>&1
#   전체 리빌드:  FULL=1 ./setup/schedule_build.sh
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONIOENCODING=utf-8
PY="${PYTHON:-python3}"
"$PY" -m llmwiki health --for-build --quick || echo "health warnings (continuing)"
if [ "${FULL:-0}" = "1" ]; then
  "$PY" -m llmwiki build --full --json
else
  "$PY" -m llmwiki build --json
fi
code=$?
if [ $code -ne 0 ]; then echo "build failed (exit $code) — python -m llmwiki logs tail --file build"; fi
exit $code
