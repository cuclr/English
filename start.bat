@echo off
setlocal
cd /d "%~dp0"

set "VOCAB_PYTHON=%CD%\.venv\Scripts\python.exe"

if not exist "%VOCAB_PYTHON%" (
    echo Preparing the application for first use...
    python -m venv .venv
    if errorlevel 1 goto :setup_failed
)

"%VOCAB_PYTHON%" -c "import flask, fitz" >nul 2>nul
if errorlevel 1 (
    echo Installing required packages...
    "%VOCAB_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 goto :setup_failed
)

echo Starting the vocabulary app...
echo The browser will open automatically at http://127.0.0.1:5000
echo Keep this window open while using the app. Press Ctrl+C to stop it.
start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:5000'"
"%VOCAB_PYTHON%" app.py
goto :end

:setup_failed
echo.
echo Startup preparation failed. Check that Python is installed, then try again.
pause

:end
endlocal
