#!/bin/bash
# StockPulse Automated Launcher for macOS and Linux

cd "$(dirname "$0")"

echo "======================================================================"
echo "                    STOCKPULSE DASHBOARD LAUNCHER"
echo "======================================================================"
echo ""

# 1. Detect Python 3
PYTHON_BIN=""
if command -v python3 &>/dev/null; then
    PYTHON_BIN="python3"
elif command -v python &>/dev/null; then
    PYTHON_BIN="python"
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "[ERROR] Python 3 is not installed or not in PATH."
    echo "Please install Python 3 from https://www.python.org/downloads/"
    read -p "Press Enter to exit..."
    exit 1
fi

echo "[*] Using Python: $($PYTHON_BIN --version)"
echo ""

# 2. Virtual Environment (.venv)
if [ ! -d ".venv" ]; then
    echo "[*] Setting up isolated Python environment (.venv)..."
    $PYTHON_BIN -m venv .venv
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment."
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo "[*] Virtual environment created."
    echo ""
fi

# 3. Install requirements
if [ ! -f ".venv/.install_complete" ]; then
    echo "======================================================================"
    echo "[*] FIRST-TIME SETUP: Installing required dependencies..."
    echo "[*] This takes 1-2 minutes. Please wait..."
    echo "======================================================================"
    .venv/bin/pip install --upgrade pip --quiet
    .venv/bin/pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to install dependencies."
        read -p "Press Enter to exit..."
        exit 1
    fi
    touch .venv/.install_complete
    echo "[*] Dependencies installed successfully!"
    echo ""
fi

# 4. Copy .env if missing
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
    else
        echo "# StockPulse Configuration" > .env
    fi
fi

# 5. Launch application & browser
echo "======================================================================"
echo "               STOCKPULSE APPLICATION IS RUNNING"
echo "======================================================================"
echo ""
echo "[*] Web Dashboard: http://127.0.0.1:8000"
echo "[*] Opening browser automatically..."
echo ""
echo "[*] To STOP: Press Ctrl + C or close this terminal window."
echo "======================================================================"
echo ""

# Open browser based on OS
(
    sleep 3
    if [[ "$OSTYPE" == "darwin"* ]]; then
        open "http://127.0.0.1:8000"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        xdg-open "http://127.0.0.1:8000" &>/dev/null || sensible-browser "http://127.0.0.1:8000" &>/dev/null
    fi
) &

# Run server
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
