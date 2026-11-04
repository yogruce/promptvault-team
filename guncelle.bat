@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title PromptVault Team - Guncelleme Araci
cd /d "%~dp0"

echo ========================================================
echo   PromptVault Team - Tek Tikla En Son Surume Guncelleme
echo ========================================================
echo.

:: 1. Eski surecleri kapat
echo [1/4] Calisan eski sunucular sonlandiriliyor...
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":7861" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":7862" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1

:: 2. Eger Git deposu ise git reset/pull kullan
if exist .git (
    echo [2/4] Git uzerinden guncelleniyor...
    git fetch origin main >nul 2>&1
    git reset --hard origin/main
    goto :INSTALL_DEPS
)

:: 3. Git yoksa GitHub'dan en guncel ZIP dosyasini indir ve ac
echo [2/4] GitHub'dan en guncel surum paketi (ZIP) indiriliyor...
set "ZIP_FILE=%TEMP%\promptvault_team_latest.zip"
set "EXTRACT_DIR=%TEMP%\promptvault_team_extract"

powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://github.com/yogruce/promptvault-team/archive/refs/heads/main.zip' -OutFile '%ZIP_FILE%'"
if errorlevel 1 (
    echo [HATA] GitHub'dan guncelleme paketi indirilemedi. Lutfen internet baglantinizi kontrol edin.
    pause
    exit /b 1
)

echo [3/4] Dosyalar guncelleniyor...
powershell -NoProfile -Command "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%EXTRACT_DIR%' -Force; $src = Get-ChildItem -Path '%EXTRACT_DIR%' -Directory | Select-Object -First 1; Copy-Item -Path ($src.FullName + '\*') -Destination '%~dp0' -Recurse -Force; Remove-Item -Path '%EXTRACT_DIR%' -Recurse -Force; Remove-Item -Path '%ZIP_FILE%' -Force"

:INSTALL_DEPS
echo [4/4] Bagimliliklar kontrol ediliyor...
if exist .venv\Scripts\python.exe (
    if exist requirements.txt (
        .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
    )
    .venv\Scripts\python.exe -m pip install -e . --no-deps --quiet
)

echo.
echo ========================================================
echo   [BASARILI] PromptVault Team en guncel surume yukseltildi!
echo ========================================================
echo.
timeout /t 3 /nobreak >nul
exit /b 0
