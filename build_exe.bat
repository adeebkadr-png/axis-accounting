@echo off
chcp 65001 >nul
title بناء نسخة سطح المكتب — المحاسب الذكي
cd /d "%~dp0"
echo تثبيت أدوات البناء...
python -m pip install -r requirements.txt pyinstaller pywebview >nul
echo جارٍ البناء... (قد يستغرق عدة دقائق)
python -m PyInstaller smart-accounting.spec --noconfirm --clean
echo.
echo تم! البرنامج في:  dist\SmartAccounting\SmartAccounting.exe
pause
