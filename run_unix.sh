#!/bin/bash

echo "=========================================="
echo "   SerPilas Virtual Money Server"
echo "=========================================="
echo ""

# Check for Python
if ! command -v python3 &> /dev/null
then
    echo "[ERROR] python3 could not be found."
    echo "Please install Python 3."
    exit
fi

# Create Virtual Environment
if [ ! -d "venv" ]; then
    echo "[+] Creating virtual environment..."
    python3 -m venv venv
fi

# Activate Virtual Environment
echo "[+] Activating environment..."
source venv/bin/activate

# Install/Update Dependencies
echo "[+] Checking requirements..."
pip install -r requirements.txt

# Run Application
echo "[+] Starting Server..."
python3 main.py

echo ""
echo "[!] Server has stopped."
