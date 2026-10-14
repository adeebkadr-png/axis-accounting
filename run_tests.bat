@echo off
chcp 65001 >nul
title اختبارات المحاسب الذكي
cd /d "%~dp0"
python -m pip install pytest >nul 2>&1
cd backend
python -m pytest tests -v
pause
