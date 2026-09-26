@echo off
title KastoBrain setup
cd /d "%~dp0"
echo === KastoBrain first-time setup ===
echo This creates KastoBrain's own folders inside %~dp0
echo It never touches anything outside this folder.
echo.
echo --- Step 1: DRY RUN (nothing is changed) ---
python app\setup_layout.py "%~dp0."
echo.
echo If the list above looks right, press any key to create the folders.
echo To cancel, just close this window.
pause >nul
python app\setup_layout.py "%~dp0." --apply
echo.
echo Done. Double-click start-kastobrain.bat to open the app.
pause
