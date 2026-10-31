@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\run_product_autostart.py
) else (
  python scripts\run_product_autostart.py
)
pause
