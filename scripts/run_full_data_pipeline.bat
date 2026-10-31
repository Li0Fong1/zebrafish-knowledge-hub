@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

if not exist "logs" mkdir logs

echo.
echo ========================================
echo   全面斑马鱼数据补全（过夜自动运行）
echo   日志：logs\full_pipeline.log
echo ========================================
echo.

if exist ".venv\Scripts\python.exe" (
    start "ZebrafishFullPipeline" /MIN cmd /c ".venv\Scripts\python.exe -X utf8 -m scripts.run_full_data_pipeline >> logs\pipeline_stdout.log 2>&1"
) else (
    start "ZebrafishFullPipeline" /MIN cmd /c "python -X utf8 -m scripts.run_full_data_pipeline >> logs\pipeline_stdout.log 2>&1"
)

echo [OK] 已在后台启动，可关闭本窗口。
echo 查看进度：logs\full_pipeline.log
echo.
timeout /t 5 >nul
