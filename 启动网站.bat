@echo off
REM 双击本文件 = 在终端里执行 python web_app.py（与手动输入效果相同）
setlocal
cd /d "%~dp0"
chcp 65001 >nul 2>&1
title 英语听写 — python web_app.py

cls
echo.
echo  ============================================================
echo    你要找的启动命令就是下面这一行（和手动输入完全一样）：
echo.
echo        python web_app.py
echo.
echo    当前目录（必须是项目根目录）：
echo        %cd%
echo.
echo    主程序文件（必须存在）：
echo        %cd%\web_app.py
echo  ============================================================
echo.

if not exist "%cd%\web_app.py" (
  echo [错误] 当前文件夹里没有 web_app.py，请勿移动本 bat 文件。
  pause
  exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 找不到 python 命令。可尝试安装 Python 后勾选 Add to PATH，
  echo        或把下面一行的 python 改成 py
  pause
  exit /b 1
)

echo 正在执行: python "%cd%\web_app.py"
echo （窗口里接下来会出现 Flask 的 Running on ...）
echo.
python "%cd%\web_app.py"
set EXITCODE=%ERRORLEVEL%
echo.
echo ---------- 已退出，代码 %EXITCODE% ----------
pause
endlocal
