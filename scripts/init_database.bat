@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
echo.
echo ========================================
echo   斑马鱼基因数据库初始化
echo   将创建 zebrafish_db 并导入示例数据
echo ========================================
echo.
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -X utf8 -m database.init_database
) else (
    python -X utf8 -m database.init_database
)
echo.
pause
