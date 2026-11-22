@echo off
rem Stops Nightshift cleanly (no new applications). Works from any folder.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_all.ps1"
