@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在执行启动前体检...
python 05_Video\scripts\preflight_check.py
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
echo 释放 8765 端口（关闭所有旧 GUI 进程，可能有多个）...
for /l %%i in (1,1,5) do (
  for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
  )
  timeout /t 1 /nobreak >nul
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr LISTENING') do (
  echo 警告：8765 仍被 PID %%a 占用，请手动结束该进程后重试。
  pause
  exit /b 1
)
echo 启动新版 GUI（api_version 11，支持一键拼接、提示词优化与图片生成）...
start "" pythonw 05_Video\scripts\seedance_gui.py
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8765/
echo 已在后台启动 GUI 并打开浏览器；运行日志在任务面板视频预览下方。
pause
