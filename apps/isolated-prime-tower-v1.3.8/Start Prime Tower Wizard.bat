@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
 py -3 tower_wizard.py
) else (
 python tower_wizard.py
)
pause
