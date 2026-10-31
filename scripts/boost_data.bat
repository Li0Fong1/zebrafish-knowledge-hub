@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
echo.

echo ========================================

echo   数据增强：表达量 + 文献 RAG（并行）

echo   可与深度注释 enrich-only 同时运行

echo ========================================

echo.

if exist ".venv\Scripts\python.exe" (

    .venv\Scripts\python.exe -X utf8 -m scripts.boost_data %*

) else (

    python -X utf8 -m scripts.boost_data %*

)

echo.

pause

