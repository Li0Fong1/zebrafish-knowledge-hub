@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
set "CONSOLE_TITLE=斑马鱼基因问答体 - 公网隧道"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -X utf8 -m scripts.expose_internet
) else (
    python -X utf8 -m scripts.expose_internet
)
pause
