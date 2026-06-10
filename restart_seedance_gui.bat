@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: 自动检测系统代理并设置环境变量
for /f "tokens=3" %%a in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyServer 2^>nul') do set "PROXY_ADDR=%%a"
for /f "tokens=3" %%a in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable 2^>nul') do set "PROXY_ENABLE=%%a"
if "%PROXY_ENABLE%"=="1" if not "%PROXY_ADDR%"=="" (
    set "HTTP_PROXY=http://%PROXY_ADDR%"
    set "HTTPS_PROXY=http://%PROXY_ADDR%"
)

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
