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
rem    安装版会装到 C 盘、还会往 C:\Users\你的用户名\.ollama 写密钥；
rem    便携版解压在项目里（E 盘），密钥和模型也都放 E 盘。
rem
rem  环境变量说明：
rem    OLLAMA_MODELS      模型权重存放位置（几个 GB，别放 C 盘）
rem    HOME/USERPROFILE   重定向，让它在 E 盘生成密钥而不是 C 盘
rem
rem  编码：本文件存成 GBK（cmd 在中文 Windows 的原生编码）。
rem        不要存 UTF-8，否则中文注释会被当成命令执行。
rem        所以这里也不加 chcp 65001。
rem ============================================================
setlocal

set "PET=%~dp0"
set "PET=%PET:~0,-1%"
set "OLLAMA_EXE=%PET%\ollama\ollama.exe"

if not exist "%OLLAMA_EXE%" (
    echo [skip] 没找到便携版 Ollama: %OLLAMA_EXE%
    echo        不想用本地模型的话，这一项可以忽略。
    exit /b 0
)

rem 已经在跑就不重复启动
tasklist /fi "imagename eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if not errorlevel 1 (
    echo [skip] Ollama 已经在运行了。
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

rem ---- 等一下确认真的起来了 ----
rem 以前直接 exit，用户看不到任何反馈：起没起来都不知道。
rem 走本地模型模式时最怕"以为它在跑，其实没起"。
rem 注意：不用 timeout（它在输入被重定向的环境下会报错），用 ping 当延时。
set /a _TRY=0
:WAIT_LOOP
set /a _TRY+=1
ping -n 2 127.0.0.1 >nul 2>&1
curl -s -m 2 http://127.0.0.1:11434/api/version 2>nul | find "version" >nul
if not errorlevel 1 goto OK
if %_TRY% GEQ 20 goto FAIL
goto WAIT_LOOP

:OK
echo [完成] 本地模型服务已就绪: http://127.0.0.1:11434
echo        模型目录: %OLLAMA_MODELS%
exit /b 0

:FAIL
echo [失败] 等了 20 秒服务还没起来。
echo        常见原因: 显卡驱动不对 / 端口被别的程序占用
echo        手动排查: 在本目录运行下面这条，看它报什么错
echo            ollama\ollama.exe serve
exit /b 1
