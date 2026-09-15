@echo off
cd /d "%~dp0"
where py >nul 2>&1 && py -3 rns-imager.py & goto :eof
where python >nul 2>&1 && python rns-imager.py & goto :eof
echo Nie widze Pythona w PATH.
pause
