@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title PromptVault Team - Yonetici (Admin)
cd /d "%~dp0"

echo ========================================================
echo   PromptVault Team - YONETICI (ADMIN) SURUMU
echo   Gems, Custom GPTs, Skills ve Kurumsal Drive Yonetimi
echo ========================================================
echo.

:: 1. Python Sanal Ortami Kontrolu
if not exist .venv\Scripts\python.exe (
    echo [BILGI] Ilk kurulum yapiliyor, sanal ortam olusturuluyor...
    where python >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [HATA] Sisteminizde Python bulunamadi!
        echo Lutfen https://www.python.org adresinden Python indirip kurun.
        echo Kurulum sirasinda "Add python.exe to PATH" secenegini isaretleyin.
        echo.
        pause
        exit /b 1
    )
    python -m venv .venv
    if errorlevel 1 (
        echo [HATA] Sanal ortam olusturulamadi.
        pause
        exit /b 1
    )
)

:: 2. Gerekli paketler kurulu mu?
.venv\Scripts\python.exe -c "import uvicorn, fastapi" >nul 2>&1
if errorlevel 1 (
    echo [BILGI] Gerekli kutuphaneler yukleniyor, lutfen bekleyin...
    .venv\Scripts\python.exe -m pip install --upgrade pip --quiet
    .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
    .venv\Scripts\python.exe -m pip install -e . --no-deps --quiet
)

:: 3. Git guncellemesi (varsa)
if exist .git (
    where git >nul 2>&1
    if not errorlevel 1 (
        git fetch origin main >nul 2>&1
        git rev-parse HEAD > "%TEMP%\pvt_local.txt" 2>nul
        git rev-parse origin/main > "%TEMP%\pvt_remote.txt" 2>nul
        fc "%TEMP%\pvt_local.txt" "%TEMP%\pvt_remote.txt" >nul 2>&1
        if errorlevel 1 (
            echo [GUNCELLEME] Yeni surum indiriliyor...
            git pull origin main || git reset --hard origin/main
        )
        del "%TEMP%\pvt_local.txt" "%TEMP%\pvt_remote.txt" >nul 2>&1
    )
)

:RUN_SERVER
:: 4. Port 7861'i tutan eski sureci kapat
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 7861 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":7861" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1

echo.
echo Yonetici Paneli: http://127.0.0.1:7861
echo Tarayici aciliyor...
echo [BILGI] Tarayici kapatildiginda bu pencere otomatik olarak kapanacaktir.
echo.

.venv\Scripts\python.exe -m promptvault_team --role admin --port 7861 --open
set APP_EXIT_CODE=%ERRORLEVEL%

:: Exit code 42: Canli guncelleme sonrasi otomatik yeniden baslatma
if "%APP_EXIT_CODE%"=="42" (
    echo.
    echo [YENIDEN BASLATMA] Guncelleme sonrasi baslatiliyor...
    timeout /t 2 /nobreak >nul
    goto :RUN_SERVER
)

if %APP_EXIT_CODE% neq 0 (
    echo.
    echo ========================================================
    echo [HATA] Sunucu bir hata ile kapandi (Hata Kodu: %APP_EXIT_CODE%)
    echo ========================================================
    pause
    exit /b %APP_EXIT_CODE%
)

exit /b 0
