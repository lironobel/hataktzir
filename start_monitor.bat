@echo off
chcp 65001 >nul
title Clips Monitor
cd /d "%~dp0"

rem ASCII only in this file. Hebrew in a rem line gets executed as commands.
rem PYTHONUTF8 stops python decoding ffmpeg output as cp1255.
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

:loop
echo [%date% %time%] starting monitor10...
python scripts\monitor10.py
echo [%date% %time%] monitor stopped. restarting in 60s...
timeout /t 60 /nobreak >nul
goto loop
