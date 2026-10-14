@echo off
chcp 65001 >nul
title المحاسب الذكي — خادم الشبكة
cd /d "%~dp0"
echo.
echo   شغّل هذا الملف على جهاز واحد فقط (الخادم)، وافتح على بقية الأجهزة العنوان التالي:
echo.
for /f "tokens=14" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo        http://%%a:8000
echo.
echo   تأكد من السماح للمنفذ 8000 في جدار حماية ويندوز عند أول تشغيل.
echo.
cd backend
python -m uvicorn app:app --host 0.0.0.0 --port 8000
