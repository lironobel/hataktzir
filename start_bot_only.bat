@echo off
chcp 65001 >nul
title Clips Bot only (no monitor)
rem Bot + upload queue only. No live detection, no analysis cost.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python scripts\tgbot2.py
pause
