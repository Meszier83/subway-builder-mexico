@echo off
cd /d "%~dp0"
python tools\map_analyzer.py %*
if errorlevel 1 pause
