@echo off
chcp 65001 >nul
title 续推运行器 - 安装
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup.ps1" %*
if errorlevel 1 pause
