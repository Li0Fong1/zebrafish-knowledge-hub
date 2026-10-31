@echo off
rem UTF-8 console init - must run before any non-ASCII output
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONLEGACYWINDOWSSTDIO=utf-8
if not defined EPMC_REQUEST_INTERVAL set EPMC_REQUEST_INTERVAL=1.5
