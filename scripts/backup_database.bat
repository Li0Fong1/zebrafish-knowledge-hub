@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8

set MYSQL_USER=root
set MYSQL_PASSWORD=%MYSQL_PASSWORD%
set MYSQL_DATABASE=zebrafish_db
call "%~dp0_mysql_path.bat"

set STAMP=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set STAMP=%STAMP: =0%
set OUT=backup\zebrafish_db_%STAMP%.sql

if not exist "backup" mkdir backup

echo.
echo 备份 MySQL 到 %OUT%
echo.

where mysqldump >nul 2>&1
if errorlevel 1 (
    echo [ERROR] mysqldump not found
    pause
    exit /b 1
)

mysqldump -u %MYSQL_USER% -p%MYSQL_PASSWORD% --default-character-set=utf8mb4 --single-transaction %MYSQL_DATABASE% > "%OUT%"
if errorlevel 1 (
    echo [ERROR] backup failed
    pause
    exit /b 1
)
echo [OK] backup done
pause
