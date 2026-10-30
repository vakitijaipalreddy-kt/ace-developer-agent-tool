@echo off
REM ACE Developer Agent - double-click this to install.
REM Bypasses the PowerShell execution policy for this one script only,
REM which is what lets a downloaded zip install without policy changes.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1" %*
