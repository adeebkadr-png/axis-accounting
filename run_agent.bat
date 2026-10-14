@echo off
chcp 65001 >nul
title وكيل الطباعة — axis للمحاسبة
cd /d "%~dp0"
python -c "import PIL, arabic_reshaper, bidi" 2>nul || python -m pip install pillow arabic-reshaper python-bidi pyserial
echo.
echo   وكيل الطباعة يعمل. اتركه مفتوحاً أثناء العمل على نقطة البيع.
echo   اضبط الطابعة في ملف agent.json ثم اختر «وكيل الطباعة المحلي» من إعدادات البرنامج.
echo.
python print_agent.py
pause
