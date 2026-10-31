@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0.."
call .venv\Scripts\activate.bat

:collect_loop
.venv\Scripts\python.exe -X utf8 -m scripts.collect_literature %*
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE%==130 exit /b 0
if %EXIT_CODE%==2 exit /b 2
if %EXIT_CODE%==0 goto done
echo.
echo [自动续传] 采集意外中断，30 秒后从中断处继续（Ctrl+C 可彻底停止）...
timeout /t 30 /nobreak >nul
goto collect_loop

:done
pause
