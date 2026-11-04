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

if not exist .venv\Scripts\python.exe (
    echo [BILGI] Ilk kurulum yapiliyor, sanal ortam olusturuluyor...
    python -m venv .venv
)

:: Paketlerin kurulu olup olmadigini kontrol et
.venv\Scripts\python.exe -c "import uvicorn, fastapi" >nul 2>&1
if errorlevel 1 (
    echo [BILGI] Gerekli kutuphaneler yukleniyor, lutfen bekleyin...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    .venv\Scripts\python.exe -m pip install -e .
)

:START_APP
:: GitHub Otomatik Guncelleme Kontrolu
where git >nul 2>&1
if errorlevel 1 goto :AFTER_UPDATE_CHECK
if not exist .git goto :AFTER_UPDATE_CHECK

echo [KONTROL] GitHub guncellemeleri denetleniyor...
git fetch origin main >nul 2>&1
git rev-parse HEAD > "%TEMP%\pvt_local.txt" 2>nul
git rev-parse origin/main > "%TEMP%\pvt_remote.txt" 2>nul

fc "%TEMP%\pvt_local.txt" "%TEMP%\pvt_remote.txt" >nul 2>&1
if errorlevel 1 (
    echo.
    echo ========================================================
    echo   [GUNCELLEME] GitHub'da yeni bir surum tespit edildi!
    echo   Guncel dosyalar indiriliyor...
    echo ========================================================
    git pull origin main
    if exist requirements.txt (
        .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
    )
    .venv\Scripts\python.exe -m pip install -e . --no-deps --quiet
    echo   [BASARILI] Uygulama en son surume guncellendi.
    echo.
)
del "%TEMP%\pvt_local.txt" "%TEMP%\pvt_remote.txt" >nul 2>&1

:AFTER_UPDATE_CHECK
:: 7861 portunu tutan eski sureci sonlandir
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 7861 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo Yonetici Paneli: http://127.0.0.1:7861
echo Tarayici aciliyor...
echo [BILGI] Tarayici kapatildiginda bu pencere otomatik olarak kapanacaktir.
echo.

.venv\Scripts\python.exe -m promptvault_team --role admin --port 7861 --open
set APP_EXIT_CODE=%ERRORLEVEL%

:: Exit code 42: Canli guncelleme sonrasi otomatik yeniden baslatma
if "%APP_EXIT_CODE%"=="42" (
    echo.
    echo ========================================================
    echo   [YENIDEN BASLATMA] Guncelleme sonrasi baslatiliyor...
    echo ========================================================
    timeout /t 2 /nobreak >nul
    goto :START_APP
)

if %APP_EXIT_CODE% neq 0 (
    echo.
    echo [HATA] Bir sorun olustu, hata detaylari yukaridadir.
    pause
)
