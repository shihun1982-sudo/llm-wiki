@echo off
rem ===== LLM Wiki 설치 (Windows) =====
rem 사용법: setup\install.bat            (현재 python 사용)
rem         setup\install.bat venv       (.venv 가상환경 생성 후 설치 — Python 3.9+ 권장)
rem 이 파일은 UTF-8 로 저장되어 있으므로, 아래 한글 안내가 깨지지 않으려면 코드페이지가 65001 이어야 한다.
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8

if "%1"=="venv" (
  if not exist .venv ( python -m venv .venv )
  call .venv\Scripts\activate.bat
)

python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
rem 선택 패키지: 실패해도 계속 (없으면 raw HTTP 로 동작)
python -m pip install anthropic 2>nul

if not exist config.json ( copy setup\config.example.json config.json >nul && echo config.json 생성됨 - 기본 corpus_dirs 는 샘플 setup\sample_corpus_modem 입니다. 실제 문서는 corpus\ 에 넣고 corpus_dirs 를 수정하세요 )
if not exist .env ( copy setup\.env.example .env >nul && echo .env 생성됨 - API 키를 입력하세요 ^(선택^) )
if not exist security.json ( copy setup\security.example.json security.json >nul && echo security.json 생성됨 - 공개 전에 admin 계정 생성: python -m llmwiki users add ^<id^> --role admin )
if not exist agents.json ( copy setup\agents.example.json agents.json >nul && echo agents.json 생성됨 - headless 에이전트 명령·재시도 정책 )
if not exist server.json ( copy setup\server.example.json server.json >nul && echo server.json 생성됨 - 동시 사용자 동시성·대기열·속도 제한 ^(docs\CONCURRENCY.md^) )
if not exist schedule.json ( copy setup\schedule.example.json schedule.json >nul && echo schedule.json 생성됨 - 주기 작업 17가지 예시 ^(증분 빌드만 켜져 있음; schedule list 로 확인, docs\SCHEDULER.md^) )
if not exist models.json ( copy setup\models.example.json models.json >nul && echo models.json 생성됨 - 사용 가능한 LLM/임베딩 모델 목록 ^(models list^) )
if not exist corpus ( mkdir corpus )

echo.
python setup\check_env.py
echo.
echo 다음 단계:  run.bat build --full --trace   그리고   run.bat serve
endlocal
