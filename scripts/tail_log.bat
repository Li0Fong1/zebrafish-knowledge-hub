@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -X utf8 scripts\tail_log.py %*
) else (
    python -X utf8 scripts\tail_log.py %*
)
