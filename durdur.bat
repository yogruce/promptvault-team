@echo off
chcp 65001 >nul
echo ========================================================
echo   PromptVault Team - Port Temizleme ve Sunucu Kapatma
echo ========================================================
echo.

echo [1/3] 7860, 7861 ve 7862 portlarini tutan surecler sonlandiriliyor...
powershell -NoProfile -Command "7860, 7861, 7862 | ForEach-Object { Get-NetTCPConnection -LocalPort $_ -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }" >nul 2>&1

for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":7860" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":7861" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":7862" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1

echo [2/3] Acik kalan PromptVault Python surecleri sonlandiriliyor...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%%promptvault%%'\" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo [3/3] Tum portlar basariyla temizlendi ve sunucular kapatildi!
echo.
timeout /t 2 /nobreak >nul
exit /b 0
