@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8
echo.
echo ========================================
echo   导出斑马鱼基因数据（CSV + 目录）
echo   供外部人员清洗
echo ========================================
echo.
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -X utf8 -m scripts.export_gene_data_for_cleaning
) else (
    python -X utf8 -m scripts.export_gene_data_for_cleaning
)
echo.
pause
