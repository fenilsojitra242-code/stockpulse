@echo off
setlocal
title StockPulse - Automated Launcher

echo ======================================================================
echo                     STOCKPULSE DASHBOARD LAUNCHER
echo ======================================================================
echo.

cd /d "%~dp0"

:: -------------------------------------------------------------------------
:: 1. Detect Python Installation
:: -------------------------------------------------------------------------
set "PYTHON_CMD="

:: Check standard 'python'
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_CMD=python"
    goto :PYTHON_READY
)

:: Check Python Launcher 'py'
py -3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3"
    goto :PYTHON_READY
)

:: Check common AppData / ProgramFiles installation paths
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python313\python.exe"
    goto :PYTHON_READY
)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
    goto :PYTHON_READY
)
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
    goto :PYTHON_READY
)
if exist "%LocalAppData%\Programs\Python\Python310\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python310\python.exe"
    goto :PYTHON_READY
)
if exist "%ProgramFiles%\Python313\python.exe" (
    set "PYTHON_CMD=%ProgramFiles%\Python313\python.exe"
    goto :PYTHON_READY
)
if exist "%ProgramFiles%\Python312\python.exe" (
    set "PYTHON_CMD=%ProgramFiles%\Python312\python.exe"
    goto :PYTHON_READY
)
if exist "%ProgramFiles%\Python311\python.exe" (
    set "PYTHON_CMD=%ProgramFiles%\Python311\python.exe"
    goto :PYTHON_READY
)
if exist "%ProgramFiles%\Python310\python.exe" (
    set "PYTHON_CMD=%ProgramFiles%\Python310\python.exe"
    goto :PYTHON_READY
)

:: If Python is not found, attempt automatic download and installation
echo [!] Python was not detected on your system.
echo [*] StockPulse requires Python 3.10 or newer.
echo.
echo [*] Attempting to automatically download and install Python for you...
echo     Please click "Yes" if Windows prompts for permission.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $installer = Join-Path $env:TEMP 'python_setup.exe'; Write-Host '[*] Downloading official Python installer...'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile $installer; Write-Host '[*] Running installation...'; Start-Process $installer -ArgumentList '/passive', 'InstallAllUsers=0', 'PrependPath=1', 'Include_pip=1', 'Include_launcher=1' -Wait; Remove-Item -Force $installer -ErrorAction SilentlyContinue;"

:: Re-check after installer
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_CMD=python"
    goto :PYTHON_READY
)
py -3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3"
    goto :PYTHON_READY
)
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
    goto :PYTHON_READY
)

echo.
echo [ERROR] Python could not be installed automatically.
echo Please install Python manually from: https://www.python.org/downloads/
echo Note: When installing, make sure to check "Add Python to PATH".
echo.
pause
exit /b 1

:PYTHON_READY
echo [*] Python detected:
%PYTHON_CMD% --version
echo.

:: -------------------------------------------------------------------------
:: 2. Setup Virtual Environment (.venv)
:: -------------------------------------------------------------------------
set "VENV_DIR=%~dp0.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "INSTALL_FLAG=%VENV_DIR%\.install_complete"

if exist "%VENV_PYTHON%" goto :VENV_READY

echo [*] Creating isolated virtual environment...
%PYTHON_CMD% -m venv "%VENV_DIR%"
if %errorlevel% neq 0 (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
)
echo [*] Virtual environment created successfully.
echo.

:VENV_READY

:: -------------------------------------------------------------------------
:: 3. Automatically Install Requirements (First Run Only)
:: -------------------------------------------------------------------------
if exist "%INSTALL_FLAG%" goto :DEPS_READY

echo ======================================================================
echo [*] FIRST-TIME SETUP: Installing all project requirements...
echo [*] This will take 1 to 2 minutes. Please wait...
echo ======================================================================
echo.

"%VENV_PYTHON%" -m pip install --upgrade pip --quiet
"%VENV_PYTHON%" -m pip install -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Package installation failed. Please check your internet connection.
    pause
    exit /b 1
)

echo ok > "%INSTALL_FLAG%"
echo.
echo [*] All requirements installed successfully!
echo.

:DEPS_READY

:: -------------------------------------------------------------------------
:: 4. Ensure .env File Exists
:: -------------------------------------------------------------------------
if not exist "%~dp0.env" (
    if exist "%~dp0.env.example" (
        copy "%~dp0.env.example" "%~dp0.env" >nul
    ) else (
        echo # StockPulse Environment Configuration > "%~dp0.env"
    )
)

:: -------------------------------------------------------------------------
:: 5. Launch Server and Open Web Browser
:: -------------------------------------------------------------------------
echo ======================================================================
echo                  STOCKPULSE APPLICATION IS RUNNING
echo ======================================================================
echo.
echo [*] Dashboard URL: http://127.0.0.1:8000
echo [*] Opening your web browser automatically in 3 seconds...
echo.
echo [*] To STOP the server: Close this window or press Ctrl + C.
echo ======================================================================
echo.

:: Open default browser after 3 seconds in background
start "" /B cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8000"

:: Start Uvicorn server in foreground
"%VENV_PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

if %errorlevel% neq 0 (
    echo.
    echo Server has stopped.
    pause
)
