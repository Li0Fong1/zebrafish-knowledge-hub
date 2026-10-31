@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0.."
call .venv\Scripts\activate.bat

echo %* | findstr /i "\-\-loop" >nul
if %ERRORLEVEL%==0 goto link_loop

.venv\Scripts\python.exe -X utf8 -m scripts.link_literature_genes %*
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% NEQ 0 pause
exit /b %EXIT_CODE%

:link_loop
:loop_restart
.venv\Scripts\python.exe -X utf8 -m scripts.link_literature_genes %*
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE%==130 exit /b 0
if %EXIT_CODE%==2 exit /b 2
if %EXIT_CODE%==0 exit /b 0
echo.
echo [自动续跑] 关联任务意外中断，30 秒后重试（Ctrl+C 可彻底停止）...
timeout /t 30 /nobreak >nul
goto loop_restart
