@echo off
rem ===== LLM Wiki 설치 (Windows) =====
rem 사용법: setup\install.bat            (현재 python 사용)
rem         setup\install.bat venv       (.venv 가상환경 생성 후 설치 — Python 3.9+ 권장)
setlocal
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
if not exist corpus ( mkdir corpus )

echo.
python setup\check_env.py
echo.
echo 다음 단계:  run.bat build --full --trace   그리고   run.bat serve
endlocal
