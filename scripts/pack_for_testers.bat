@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."

set "DEST=release\ZebrafishGeneQA-TestPack"
set "FAISS_SRC=%LOCALAPPDATA%\ZebrafishGeneQA\faiss_index"

echo.
echo ========================================
echo   Pack test release - bundled Python
echo   Output folder: %DEST%
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found
    pause
    exit /b 1
)

if not exist "release" mkdir release
if exist "%DEST%" (
    echo Cleaning old pack...
    rmdir /s /q "%DEST%"
)
mkdir "%DEST%"

echo [1/6] Copy app and .venv ...
robocopy . "%DEST%" /E /XD release __pycache__ .idea .git agent-tools /XF .env *.pyc /NFL /NDL /NJH /NJS /nc /ns /np
if errorlevel 8 (
    echo [ERROR] robocopy failed
    pause
    exit /b 1
)

echo [2/6] Copy FAISS index ...
if exist "%FAISS_SRC%" (
    if not exist "%DEST%\data\faiss_index" mkdir "%DEST%\data\faiss_index"
    robocopy "%FAISS_SRC%" "%DEST%\data\faiss_index" /E /NFL /NDL /NJH /NJS /nc /ns /np
) else (
    echo [WARN] FAISS index missing - RAG may be slow on first run
)

echo [3/6] Write .env ...
copy /Y ".env.example" "%DEST%\.env" >nul

echo [4/6] Export MySQL ...
call scripts\export_database.bat nopause
if exist "release\zebrafish_db_full.sql" (
    if not exist "%DEST%\release" mkdir "%DEST%\release"
    copy /Y "release\zebrafish_db_full.sql" "%DEST%\release\zebrafish_db_full.sql" >nul
    echo [OK] SQL copied into pack
) else (
    echo [WARN] SQL export failed - run scripts\export_database.bat manually
)

echo [5/6] Copy readme ...
copy /Y "给测试人员.txt" "%DEST%\给测试人员.txt" >nul

echo [6/6] Done
echo.
echo Folder: %DEST%
echo Zip it and send to testers. Use English path e.g. D:\ZebrafishGeneQA
echo Full pack about 8-10 GB (models\ holds embedding + reranker + 0.5B).
echo.
pause
