@echo off
REM 在项目目录打开一个「空闲」命令行，可随意输入命令（与 start_web.cmd 无关）
cd /d "%~dp0"
title 项目命令行 — %cd%
echo 当前目录: %cd%
echo 示例: netstat -ano ^| findstr :5000
echo 退出本窗口: exit
echo.
cmd /k
