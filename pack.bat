@echo off
chcp 65001 >nul
title 哥伦比娅桌宠 · 打包
cd /d "%~dp0"

echo ============================================
echo   哥伦比娅桌宠 · 打包为 EXE
echo ============================================
echo.

rem 定位 Python：优先本机安装，其次桌面宠物自带的 D 盘运行时
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

echo [1/4] 检查 PyInstaller...
"%PY%" -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo    未安装，正在安装 PyInstaller...
    "%PY%" -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
)
echo    OK
echo.

echo [2/4] 清理旧文件...
if exist "dist"        rmdir /s /q "dist"
if exist "build"       rmdir /s /q "build"
if exist "main.spec"   del /q "main.spec"
echo    OK
echo.

echo [3/4] 打包中（首次约需 1~3 分钟）...
"%PY%" -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name "哥伦比娅桌宠" ^
    --hidden-import PIL._tkinter_finder ^
    --collect-submodules PIL ^
    --collect-all miniaudio ^
    main.py

if errorlevel 1 (
    echo.
    echo [失败] 打包过程出错，请检查上方日志。
    pause
    exit /b 1
)

echo.
echo [4/4] 复制资源文件夹...
if not exist "dist\images"      mkdir "dist\images"
xcopy /y /q "images\*" "dist\images\" >nul 2>&1
if exist "dialogues.txt" copy /y "dialogues.txt" "dist\" >nul 2>&1
if exist "music" (
    if not exist "dist\music"   mkdir "dist\music"
    xcopy /y /q "music\*" "dist\music\" >nul 2>&1
)

echo.
echo ============================================
echo   打包完成！
echo   EXE 位置：dist\哥伦比娅桌宠.exe
echo   （images 文件夹需与 EXE 放在同一目录）
echo ============================================
pause