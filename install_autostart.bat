@echo off
chcp 65001 >nul
title تشغيل axis للمحاسبة تلقائياً مع ويندوز
cd /d "%~dp0"
echo سيُضاف البرنامج إلى مهام ويندوز ليعمل في الخلفية عند كل تشغيل للجهاز (خادم الشبكة على المنفذ 8000).
echo.
schtasks /Create /F /TN "AxisAccounting" /SC ONLOGON /RL LIMITED /TR "wscript.exe \"%~dp0start_hidden.vbs\""
if errorlevel 1 ( echo تعذرت الإضافة — جرّب «تشغيل كمسؤول». & pause & exit /b 1 )
echo.
echo تم. سيعمل البرنامج تلقائياً عند تسجيل الدخول إلى ويندوز. للتشغيل الآن دون إعادة الإقلاع:
schtasks /Run /TN "AxisAccounting"
echo للإلغاء لاحقاً شغّل uninstall_autostart.bat
pause
