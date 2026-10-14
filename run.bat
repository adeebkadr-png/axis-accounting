@echo off
chcp 65001 >nul
title المحاسب الذكي
cd /d "%~dp0"
python -c "import fastapi, uvicorn, anthropic, multipart" 2>nul || (
  echo جارِ تثبيت المتطلبات لأول مرة...
  python -m pip install -r requirements.txt
)
echo.
echo   المحاسب الذكي يعمل على: http://127.0.0.1:8000
echo   لإيقاف البرنامج أغلق هذه النافذة
echo.
start "" http://127.0.0.1:8000
cd backend
python -m uvicorn app:app --host 127.0.0.1 --port 8000
