@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 续推运行器 - 卸载
echo 将删除桌面和开始菜单里的“续推运行器”快捷方式，以及程序目录里的 runtime（Python 运行环境）。
echo 会话记录 output、专用浏览器的登录信息 chrome-profile 会保留；不需要的话，之后整个删除本文件夹即可。
echo 请先在控制页点“关闭运行器”。
echo.
set /p ok=确认卸载？输入 Y 回车：
if /i not "%ok%"=="Y" exit /b 0
powershell -NoProfile -Command "$d=[Environment]::GetFolderPath('Desktop'); $p=[Environment]::GetFolderPath('Programs'); Remove-Item (Join-Path $d '续推运行器.lnk'),(Join-Path $p '续推运行器.lnk') -ErrorAction SilentlyContinue"
if exist "%~dp0runtime" rmdir /s /q "%~dp0runtime"
echo 已卸载。
pause
