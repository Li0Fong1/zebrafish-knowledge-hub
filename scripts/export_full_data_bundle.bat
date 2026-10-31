@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo ========================================
echo   完整数据包 (MySQL+文献+RAG+参考+清洗)
echo ========================================
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\export_full_data_bundle.py %*
) else (
  python scripts\export_full_data_bundle.py %*
)
pause
