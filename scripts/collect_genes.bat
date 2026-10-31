@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
echo.
echo ========================================
echo   斑马鱼基因数据一键采集 [Ensembl]
echo   默认：classic 经典基因集 [约35个]
echo ========================================
echo.
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -X utf8 -m scripts.collect_genes --preset classic %*
) else (
    python -X utf8 -m scripts.collect_genes --preset classic %*
)
echo.
pause
