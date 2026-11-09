@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 续推运行器 - 启动
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" (
  echo 还没有安装，请先双击“安装.bat”。
  pause
  exit /b 1
)
"%PY%" -m autocontinue launch %*
if errorlevel 1 (
  echo.
  echo 启动失败，请把上面的信息截图发给云知。
  pause
) else (
  timeout /t 3 >nul
)
