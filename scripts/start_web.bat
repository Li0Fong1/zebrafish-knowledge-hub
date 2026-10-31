@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
echo.
echo ========================================
echo   斑马鱼基因问答 - Web 界面
echo   启动后浏览器访问 http://127.0.0.1:8080
echo ========================================
echo.
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -X utf8 -m api.api_server
) else (
    python -X utf8 -m api.api_server
)
echo.
pause
