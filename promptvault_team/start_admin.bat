@echo off
title PromptVault Team - Yonetici (Admin)
cd /d "%~dp0.."

echo ========================================================
echo   PromptVault Team - YONETICI (ADMIN) SURUMU
echo   Gems, Custom GPTs, Skills ^& Kurumsal Drive Yonetimi
echo ========================================================
echo.

if not exist .venv\Scripts\python.exe (
    echo [BILGI] Ilk kurulum yapiliyor, sanal ortam olusturuluyor...
    python -m venv .venv
)

:: Paketlerin kurulu olup olmadigini kontrol et
.venv\Scripts\python.exe -c "import uvicorn, fastapi" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [BILGI] Gerekli kutuphaneler yukleniyor, lutfen bekleyin...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    .venv\Scripts\python.exe -m pip install -e .
)

:: 7861 portunu tutan eski sureci sonlandir
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 7861 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo Yonetici Paneli: http://127.0.0.1:7861
echo Tarayici aciliyor...
echo [BILGI] Tarayici kapatildiginda bu pencere otomatik olarak kapanacaktir.
echo.

.venv\Scripts\python.exe -m promptvault_team --role admin --port 7861 --open
if %ERRORLEVEL% neq 0 (
    echo.
    echo [HATA] Bir sorun olustu, hata detaylari yukaridadir.
    pause
)
