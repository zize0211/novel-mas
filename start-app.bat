@echo off
rem novel-mas desktop launcher - double click me
rem All the real work is in start-app.ps1
chcp 65001 >nul
rem -WindowStyle Hidden：脚本要在后台等浏览器关掉，不弹黑框
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start-app.ps1"
