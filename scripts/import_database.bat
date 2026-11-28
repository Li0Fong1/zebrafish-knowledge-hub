@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8
call "%~dp0_mysql_path.bat"

set MYSQL_USER=root
set MYSQL_PASSWORD=%MYSQL_PASSWORD%
set SQL=release\zebrafish_db_full.sql

if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
        if "%%a"=="MYSQL_USER" set MYSQL_USER=%%b
        if "%%a"=="MYSQL_PASSWORD" set MYSQL_PASSWORD=%%b
    )
)

echo.
echo Import zebrafish_db from %SQL%
echo.

if not exist "%SQL%" (
    echo [ERROR] file not found: %SQL%
    pause
    exit /b 1
)

where mysql >nul 2>&1
if errorlevel 1 (
    echo [ERROR] mysql not found - install MySQL 8.0 first
    pause
    exit /b 1
)

echo Importing, may take 1-5 minutes...
mysql -u %MYSQL_USER% -p%MYSQL_PASSWORD% < "%SQL%"
if errorlevel 1 (
    echo [ERROR] import failed - check MySQL service and password in .env
    pause
    exit /b 1
)

echo [OK] database imported
echo.
pause
