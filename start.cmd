@echo off
rem Starts Nightshift (dashboard + guardian; the guardian starts LIVE applying). Works from any folder.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_all.ps1"
