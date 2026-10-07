@echo off
chcp 65001 >nul
title Discord Bot & Web Dashboard Unified Launcher
cd /d "%~dp0"

echo ========================================================
echo   ⚡ DISCORD BOT & AUTOQUEST ALL-IN-ONE LAUNCHER
echo ========================================================
python run_all.py

pause
