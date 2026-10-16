@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title 哥伦比娅桌宠 · 依赖安装
cd /d "%~dp0"

echo ============================================
echo   哥伦比娅桌宠 · 依赖安装
echo ============================================
echo.

rem 定位 Python：优先本机安装，其次桌面宠物自带的 D 盘运行时
set "PY="
if exist "%~dp0python\python.exe"                            set "PY=%~dp0python\python.exe"
if not defined PY if exist "%~dp0python\python.exe"       set "PY=%~dp0python\python.exe"
if not defined PY if exist "D:\python312\python.exe" set "PY=D:\python312\python.exe"
if not defined PY where python >nul 2>&1 && set "PY=python"

if not defined PY (
    echo [错误] 未检测到 Python。
    echo 请先安装 Python 3.8 或更高版本：
    echo   https://www.python.org/downloads/
    echo 安装时记得勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

echo [1/4] Python 版本：
"%PY%" --version
echo.

echo [2/4] 升级 pip...
"%PY%" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.

echo [3/4] 安装依赖库（Pillow / requests / 离线语音识别 / 唱歌播放）...
"%PY%" -m pip install Pillow requests sherpa-onnx soundcard numpy miniaudio -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.

echo [4/4] 离线语音模型（SenseVoice，约 1GB 流量）...
if exist "voice-model\model.int8.onnx" (
    echo 模型已存在，跳过下载。
) else (
    set /p DL=现在下载离线语音模型？不下载则语音对话不可用。(Y/N)：
    if /i "!DL!"=="Y" (
        echo 下载中，请耐心等待...
        curl.exe -L -o voice-model.tar.bz2 "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"
        echo 解压中...
        tar -xjf voice-model.tar.bz2
        ren sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17 voice-model
        del voice-model.tar.bz2
        del voice-model\model.onnx >nul 2>&1
        echo 模型下载完成。
    ) else (
        echo 已跳过，语音对话暂不可用。
    )
)
echo.

echo ============================================
echo   安装完成！运行 run.bat 启动桌宠。
echo ============================================
pause