@echo off
rem Sepsis Six Bundle - Windows installer (requests Administrator rights)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1" %*
