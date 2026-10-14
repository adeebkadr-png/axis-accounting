@echo off
chcp 65001 >nul
title axis للمحاسبة — خادم مشفّر HTTPS
cd /d "%~dp0"
python -c "import cryptography" 2>nul || python -m pip install cryptography
cd backend
python tls.py > "%TEMP%\axis_tls.txt"
if errorlevel 1 ( echo تعذر إنشاء الشهادة & pause & exit /b 1 )
echo.
echo   البرنامج يعمل باتصال مشفّر. افتح من أي جهاز على الشبكة أحد العناوين:
findstr "https://" "%TEMP%\axis_tls.txt"
echo.
echo   عند أول زيارة سيحذّر المتصفح من الشهادة (لأنها خاصة بشبكتك): اختر «خيارات متقدمة» ثم «متابعة».
echo.
python -m uvicorn app:app --host 0.0.0.0 --port 8443 --ssl-keyfile ..\data\tls\key.pem --ssl-certfile ..\data\tls\cert.pem
pause
