@echo off
rem novel-mas desktop launcher - double click me
rem The real work is in start-app.ps1
chcp 65001 >nul

rem Use `start` so this cmd window can exit immediately.
rem If powershell runs in the foreground, cmd stays alive until the ps1
rem finishes (and the ps1 waits until the app window is closed) - that is
rem the black window you would otherwise see hanging around.
rem -WindowStyle Hidden keeps the helper powershell itself invisible too.
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start-app.ps1"
exit
