@echo off
chcp 65001 >nul
cd /d "%~dp0external_tools\deepseek-free-api"

echo 准备启动 DeepSeek 网页端反代服务...
if not exist ".venv\Scripts\python.exe" (
  echo 创建 Python 虚拟环境...
  python -m venv .venv
)

echo 安装/检查依赖...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo 依赖安装失败，请检查 Python / 网络环境。
  pause
  exit /b 1
)

echo 释放 8000 端口...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr LISTENING') do (
  taskkill /F /PID %%a >nul 2>&1
)

echo 启动 DeepSeek Proxy: http://127.0.0.1:8000/admin
start "" /min ".venv\Scripts\python.exe" -u proxy.py
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8000/admin

echo.
echo 请在打开的管理面板中登录 DeepSeek 网页账号或导入 cURL。
pause
