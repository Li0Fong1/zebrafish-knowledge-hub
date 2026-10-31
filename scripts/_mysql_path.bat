@echo off
REM 将 MySQL bin 加入 PATH（本机未配置 PATH 时使用）
set "MYSQL_BIN="
if exist "C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqldump.exe" (
    set "MYSQL_BIN=C:\Program Files\MySQL\MySQL Server 8.4\bin"
)
if not defined MYSQL_BIN if exist "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe" (
    set "MYSQL_BIN=C:\Program Files\MySQL\MySQL Server 8.0\bin"
)
if not defined MYSQL_BIN if exist "C:\Program Files\MySQL\MySQL Server 5.7\bin\mysqldump.exe" (
    set "MYSQL_BIN=C:\Program Files\MySQL\MySQL Server 5.7\bin"
)
if defined MYSQL_BIN (
    set "PATH=%MYSQL_BIN%;%PATH%"
)
