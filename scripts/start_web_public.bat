@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
set WEB_HOST=0.0.0.0
echo.
echo ========================================
echo   Web 局域网/服务器模式
echo   本机: http://127.0.0.1:8080
echo   局域网: http://你的内网IP:8080
echo   公网需路由器端口转发 8080 或选菜单 16
echo ========================================
echo.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -X utf8 -m api.api_server
) else (
    python -X utf8 -m api.api_server
)
pause
