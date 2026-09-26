@echo off
title KastoBrain
cd /d "%~dp0"
echo === Starting KastoBrain ===
echo The app runs only on this PC. Close this window to stop it.
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8765"
python app\server.py "%~dp0."
pause
