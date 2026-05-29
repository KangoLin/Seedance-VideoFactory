@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [restart] 释放 8765 端口...
for /l %%i in (1,1,5) do (
  for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
  )
  timeout /t 1 /nobreak >nul
)
echo [restart] 启动 seedance_gui.py ...
start "" pythonw 05_Video\scripts\seedance_gui.py
timeout /t 2 /nobreak >nul
echo [restart] 完成。浏览器打开: http://127.0.0.1:8765/
exit /b 0
