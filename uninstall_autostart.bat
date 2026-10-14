@echo off
chcp 65001 >nul
schtasks /Delete /F /TN "AxisAccounting"
echo أُلغي التشغيل التلقائي.
pause
