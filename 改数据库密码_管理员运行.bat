@echo off
call "%~dp0scripts\_console_utf8.bat"
cd /d "%~dp0"
title 斑马鱼问答体 - 数据库密码迁移

net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo [错误] 需要管理员权限。
    echo        请关闭此窗口，右键本文件选择「以管理员身份运行」。
    echo.
    pause
    exit /b 1
)

echo [1/3] 启动 MySQL80 服务...
net start MySQL80 >nul 2>&1
if errorlevel 1 (
    echo       服务可能已在运行，继续尝试连接。
) else (
    echo       已启动。
)

echo.
echo [2/3] 迁移 root 密码（旧 -> MYSQL_PASSWORD_NEW）...
echo.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -X utf8 -m scripts.set_mysql_password %*
) else (
    python -X utf8 -m scripts.set_mysql_password %*
)
set RC=%ERRORLEVEL%

echo.
if "%RC%"=="0" (
    echo [3/3] 成功。请关闭本窗口，然后双击「启动.bat」重启应用。
) else (
    echo [3/3] 失败，错误码 %RC%。请查看上方提示。
)
echo.
pause
exit /b %RC%
