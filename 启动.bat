@echo off
call "%~dp0scripts\_console_utf8.bat"
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -X utf8 -m scripts.launch_menu
) else (
    python -X utf8 -m scripts.launch_menu
)
if errorlevel 1 (
    echo.
    echo [错误] 启动菜单未能打开，请查看上方报错信息。
    pause
)
exit /b %ERRORLEVEL%
