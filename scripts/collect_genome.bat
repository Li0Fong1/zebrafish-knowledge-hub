@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
echo.

echo ========================================

echo   斑马鱼全基因组采集 [Ensembl 最新版]

echo   默认：全部基因 + 全量 cDNA

echo ========================================

echo.

if exist ".venv\Scripts\python.exe" (

    .venv\Scripts\python.exe -X utf8 -m scripts.collect_genome %*

) else (

    python -X utf8 -m scripts.collect_genome %*

)

echo.

pause

