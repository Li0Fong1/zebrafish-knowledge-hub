@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8

echo.
echo ========================================
echo   导入清洗后的基因 CSV 到 MySQL
echo ========================================
echo.
echo 请先把清洗后的 CSV 放到一个文件夹，例如：
echo   项目阶段\05_数据与库\归档\data_export\cleaned_in\
echo 文件名需与导出一致：gene_basic.csv 等
echo.
set /p DIR=请输入 CSV 目录（相对项目根或绝对路径）: 
if "%DIR%"=="" (
    echo [取消]
    pause
    exit /b 1
)

echo.
echo 导入前是否先备份数据库？建议选 Y
set /p DOBK=先备份？[Y/n]: 
if /i "%DOBK%"=="" set DOBK=Y
if /i "%DOBK%"=="Y" call scripts\backup_database.bat

echo.
set /p MODE=模式 upsert(更新)/replace(整表重导) [upsert]: 
if "%MODE%"=="" set MODE=upsert

if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -X utf8 -m scripts.import_cleaned_gene_data --dir "%DIR%" --mode %MODE%
) else (
    python -X utf8 -m scripts.import_cleaned_gene_data --dir "%DIR%" --mode %MODE%
)
echo.
pause
