@echo off
chcp 65001 >nul
REM one-click launcher for video-to-md: starts server and opens browser
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
