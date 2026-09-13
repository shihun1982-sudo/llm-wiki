@echo off
rem LLM Wiki launcher (Windows). Usage: run.bat build | serve | query "question" | eval | test
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if "%~1"=="" (
  echo usage: run.bat build ^| serve ^| query "question" ^| eval ^| test ^| ^<any llmwiki command^>
  goto :eof
)
if "%~1"=="test" (
  python -m unittest discover -s tests -v
  goto :eof
)
python -m llmwiki %*
