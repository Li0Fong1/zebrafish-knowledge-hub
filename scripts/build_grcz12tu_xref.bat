@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0.."
call .venv\Scripts\activate.bat 2>nul
python -X utf8 -m scripts.build_grcz12tu_xref
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% NEQ 0 pause
exit /b %EXIT_CODE%
