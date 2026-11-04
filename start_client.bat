@echo off
title PromptVault Team - Calisan (Client)
cd /d "%~dp0"

echo ========================================================
echo   PromptVault Team - CALISAN (CLIENT) SURUMU
echo   Ekip Prompterine ^& Gems Kasasina Baglan
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

:START_APP
:: GitHub Otomatik Guncelleme Kontrolu (Git varsa)
where git >nul 2>&1
if %ERRORLEVEL% equ 0 (
    if exist .git (
        echo [KONTROL] GitHub guncellemeleri denetleniyor...
        git fetch origin main >nul 2>&1
        set LOCAL_HASH=
        set REMOTE_HASH=
        for /f "tokens=*" %%i in ('git rev-parse HEAD 2^>nul') do set LOCAL_HASH=%%i
        for /f "tokens=*" %%i in ('git rev-parse origin/main 2^>nul') do set REMOTE_HASH=%%i
        if not "%LOCAL_HASH%"=="" if not "%REMOTE_HASH%"=="" (
            if not "%LOCAL_HASH%"=="%REMOTE_HASH%" (
                echo.
                echo ========================================================
                echo   [GUNCELLEME] GitHub'da yeni bir surum tespit edildi!
                echo   Guncel dosyalar indiriliyor (git pull)...
                echo ========================================================
                git pull origin main
                if exist requirements.txt (
                    .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
                )
                .venv\Scripts\python.exe -m pip install -e . --no-deps --quiet
                echo   [BASARILI] Uygulama en son surume guncellendi.
                echo.
            )
        )
    )
)

:: 7862 portunu tutan eski sureci sonlandir
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 7862 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo Calisan Kasasi: http://127.0.0.1:7862
echo Tarayici aciliyor...
echo [BILGI] Tarayici kapatildiginda bu pencere otomatik olarak kapanacaktir.
echo.

.venv\Scripts\python.exe -m promptvault_team --role client --port 7862 --open
set APP_EXIT_CODE=%ERRORLEVEL%

:: Exit code 42: Canli guncelleme sonrasi otomatik yeniden baslatma
if "%APP_EXIT_CODE%"=="42" (
    echo.
    echo ========================================================
    echo   [YENIDEN BASLATMA] Guncelleme sonrasi baslatiliyor...
    echo ========================================================
    timeout /t 2 /nobreak >nul
    goto START_APP
)

if %APP_EXIT_CODE% neq 0 (
    echo.
    echo [HATA] Bir sorun olustu, hata detaylari yukaridadir.
    pause
)
