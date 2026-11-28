@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8
call "%~dp0_mysql_path.bat"

set MYSQL_USER=root
set MYSQL_PASSWORD=%MYSQL_PASSWORD%
set MYSQL_DATABASE=zebrafish_db
set SKIP_PAUSE=0
if /i "%~1"=="nopause" set SKIP_PAUSE=1

if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
        if "%%a"=="MYSQL_USER" set MYSQL_USER=%%b
        if "%%a"=="MYSQL_PASSWORD" set MYSQL_PASSWORD=%%b
        if "%%a"=="MYSQL_DATABASE" set MYSQL_DATABASE=%%b
    )
)

set OUT=release\zebrafish_db_full.sql
if not exist "release" mkdir release

echo.
echo Export MySQL to %OUT%
echo user=%MYSQL_USER%  db=%MYSQL_DATABASE%
echo.

where mysqldump >nul 2>&1
if errorlevel 1 (
    echo [ERROR] mysqldump not found
    echo Install MySQL 8.0 or add its bin folder to PATH
    echo Example: C:\Program Files\MySQL\MySQL Server 8.0\bin
    if "%SKIP_PAUSE%"=="0" pause
    exit /b 1
)

mysqldump -u %MYSQL_USER% -p%MYSQL_PASSWORD% --default-character-set=utf8mb4 --single-transaction --routines --triggers %MYSQL_DATABASE% > "%OUT%"
if errorlevel 1 (
    echo [ERROR] export failed - check MySQL running and password in .env
    if "%SKIP_PAUSE%"=="0" pause
    exit /b 1
)

for %%A in ("%OUT%") do echo [OK] exported, size=%%~zA bytes
echo.
if "%SKIP_PAUSE%"=="0" pause
