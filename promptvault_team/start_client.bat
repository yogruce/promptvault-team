@echo off
title PromptVault Team - Calisan (Client)
cd /d "%~dp0.."

echo ========================================================
echo   PromptVault Team - CALISAN (CLIENT) SURUMU
echo   Ekip Prompt Kasasi, Gemini Gems ^& Claude Skills
echo ========================================================
echo.

if not exist .venv\Scripts\python.exe (
    echo [BILGI] Ilk kurulum yapiliyor, sanal ortam olusturuluyor...
    python -m venv .venv
    .venv\Scripts\pip install -e .[web]
)

:: 7862 portunu tutan eski sureci sonlandir
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 7862 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo Calisan Kasasi: http://127.0.0.1:7862
echo Tarayici aciliyor...
echo.

.venv\Scripts\python.exe -m promptvault_team --role client --port 7862 --open
pause
