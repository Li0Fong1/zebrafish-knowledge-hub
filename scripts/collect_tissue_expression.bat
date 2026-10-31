@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
python -m scripts.collect_tissue_expression %*
pause
