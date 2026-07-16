@echo off
chcp 65001 >nul
REM one-click stop for video-to-md server
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
