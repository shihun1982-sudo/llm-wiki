@echo off
rem LLM Wiki launcher (Windows). Usage: run.bat build | serve | query "question" | eval | test
rem UTF-8 console: this file and all sources are UTF-8, so Korean output needs code page 65001.
rem (llmwiki also sets it from Python - see llmwiki/console.py - this makes it right from the first line.)
chcp 65001 >nul 2>&1
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
