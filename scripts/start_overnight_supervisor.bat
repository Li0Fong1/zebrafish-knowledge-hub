@echo off
call "%~dp0_console_utf8.bat"
cd /d "%~dp0.."
title 斑马鱼 - 过夜监听器 (18+15+19 自动重试)
call .venv\Scripts\activate.bat 2>nul
echo.
echo 监听器：08别名 + 21 GRCz12tu + 18/15/19 + 22测试 + 23 RAG
echo 查看日志: scripts\tail_log.bat 18
echo 状态快照: python scripts\watch_overnight.py
echo 按 Ctrl+C 停止监听器（子任务可能继续）
echo.
python -X utf8 scripts\overnight_supervisor.py
pause
