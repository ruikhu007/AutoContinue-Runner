@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 续推运行器 - AI 接口（MCP）
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" (
  echo 还没有安装，请先双击“安装.bat”。
  pause
  exit /b 1
)
rem 本机 HTTP 方式的 MCP 接口：http://127.0.0.1:8766/mcp ，需要 Bearer 令牌（程序目录里的 mcp.token，首次自动生成）。
rem 运行器本体需要先在运行（双击“启动续推运行器.bat”或桌面快捷方式）。关掉这个窗口即关闭 AI 接口。
"%PY%" -m autocontinue mcp --transport streamable-http %*
pause
