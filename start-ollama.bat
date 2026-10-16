@echo off
rem ============================================================
rem  启动本地模型服务（Ollama 便携版）
rem  ------------------------------------------------------------
rem  为什么需要这个：
rem    桌宠的「本地 llm 模型」对话模式要连 127.0.0.1:11434。
rem    Ollama 不是系统服务，关机后就没了，得有人把它拉起来。
rem    把本文件的快捷方式放进「启动」文件夹即可开机自启。
rem
rem  为什么不用安装版：
rem    安装版会装到 C 盘、还会往 C:\Users\<你>\.ollama 写密钥；
rem    便携版解压在项目里（E 盘），密钥和模型也都放 E 盘。
rem
rem  环境变量说明：
rem    OLLAMA_MODELS  模型权重存放位置（几个 GB，别放 C 盘）
rem    HOME/USERPROFILE  重定向，让它在 E 盘生成密钥而不是 C 盘
rem ============================================================
chcp 65001 >nul
setlocal

set "PET=%~dp0"
set "PET=%PET:~0,-1%"
set "OLLAMA_EXE=%PET%\ollama\ollama.exe"

if not exist "%OLLAMA_EXE%" (
    echo [跳过] 没找到便携版 Ollama: %OLLAMA_EXE%
    echo        不想用本地模型的话，这一项可以忽略。
    exit /b 0
)

rem 已经在跑就不重复启动
tasklist /fi "imagename eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if not errorlevel 1 (
    echo [跳过] Ollama 已经在运行了。
    exit /b 0
)

set "OLLAMA_MODELS=%PET%\ollama-models"
set "OLLAMA_HOME=%PET%\ollama-home"
set "HOME=%PET%\ollama-home"
set "USERPROFILE=%PET%\ollama-home"

if not exist "%OLLAMA_MODELS%" mkdir "%OLLAMA_MODELS%"
if not exist "%OLLAMA_HOME%"   mkdir "%OLLAMA_HOME%"

echo 正在启动本地模型服务...
start "" /min "%OLLAMA_EXE%" serve
exit /b 0
