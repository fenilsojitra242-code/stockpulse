@echo off
title Stop StockPulse
echo Stopping StockPulse servers...

:: Find and terminate processes listening on port 8000
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo Stopping process PID: %%a on port 8000...
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo StockPulse has been stopped.
timeout /t 2 >nul
