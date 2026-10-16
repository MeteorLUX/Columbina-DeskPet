@echo off
chcp 65001 >nul
title 哥伦比娅桌宠
cd /d "%~dp0"

rem 优先使用本机安装的 Python，找不到就用打包自带的运行时
set "PY="
if exist "%~dp0python\python.exe"                            set "PY=%~dp0python\python.exe"
if not defined PY if exist "%~dp0python\python.exe"       set "PY=%~dp0python\python.exe"
if not defined PY if exist "D:\python312\python.exe" set "PY=D:\python312\python.exe"
if not defined PY where python >nul 2>&1 && set "PY=python"

if not defined PY (
    echo [错误] 未检测到 Python，请先运行 install.bat。
    pause
    exit /b 1
)

echo 正在唤醒哥伦比娅... （"%PY%"）
"%PY%" main.py

if errorlevel 1 (
    echo.
    echo 程序异常退出，请截图上方信息反馈。
    pause
)