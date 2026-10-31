@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0.."
call .venv\Scripts\activate.bat 2>nul
python -X utf8 -m scripts.import_wos_ris --rebuild-rag
pause
