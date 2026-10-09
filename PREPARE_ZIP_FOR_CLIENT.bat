@echo off
setlocal
title Prepare Clean Client ZIP - StockPulse

echo ======================================================================
echo             PACKAGING CLEAN CLIENT ZIP FOR FORWARDING
echo ======================================================================
echo.

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0package_client.ps1"

echo.
echo ======================================================================
echo  The ZIP file is located in your parent folder:
echo  Downloads\StockPulse_Client_Delivery.zip
echo ======================================================================
echo.
pause
