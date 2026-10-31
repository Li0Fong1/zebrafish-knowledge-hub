@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
echo.
echo ========================================
echo   斑马鱼多模型智能问答系统 [CLI]
echo ========================================
echo.
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -X utf8 -m cli.main
) else (
    python -X utf8 -m cli.main
)
echo.
pause
