@echo off
chcp 936 >nul
title Ollama智能下载器v4.8

cd /d "%~dp0"

echo ==========================================
echo     Ollama智能下载器 v4.8
echo     GitHub开源版
echo ==========================================
echo.

:: 首先读取config中的安装标记
set "is_installed=0"
for /f "tokens=2 delims==" %%a in ('findstr "is_ollama_installed=" config.txt') do set "is_installed=%%a"

:: 如果标记为1（已安装），直接跳过检测
if "%is_installed%"=="1" (
    echo [信息] 检测到已配置Ollama环境，跳过检测步骤
    goto CheckPython
)

:: 如果标记为0或未找到，执行检测
echo [步骤1/2] 检测Ollama环境...

ollama ps >nul 2>&1
if %errorlevel% == 0 (
    echo [OK] Ollama已安装！
    call :UpdateConfig 1
    goto CheckPython
) else (
    echo [警告] 未检测到Ollama主程序
    echo.
    goto InstallMenu
)

:InstallMenu
echo ==========================================
echo     Ollama未安装，请选择安装方式：
echo ==========================================
echo.
echo  [1] 可自定义安装路径版（下载安装包）
echo      打开官网下载页面，可自定义安装位置
echo.
echo  [2] 快速安装版（PowerShell一键安装）
echo      自动安装到默认路径，需要管理员权限
echo.
echo  [0] 取消（退出程序）
echo.
echo ==========================================
echo.

set /p choice="请输入选项 (0/1/2): "

if "%choice%"=="0" (
    echo [信息] 用户取消安装
    pause
    exit /b 1
)

if "%choice%"=="1" goto InstallMethod1
if "%choice%"=="2" goto InstallMethod2

echo [错误] 无效选项，请重新运行
pause
exit /b 1

:InstallMethod1
echo.
echo [信息] 您选择了：自定义安装路径版
echo [倒计时] 5秒后将打开下载页面...
echo [提示] 下载完成后请运行安装程序，安装完成后重新运行本软件
echo.

for /l %%i in (5,-1,1) do (
    echo [倒计时] %%i秒后打开下载页面...
    timeout /t 1 /nobreak >nul
)

echo [操作] 正在打开下载页面...
start https://ollama.com/download/OllamaSetup.exe

echo.
echo ==========================================
echo     请完成以下步骤：
echo ==========================================
echo  1. 在浏览器中下载OllamaSetup.exe
echo  2. 运行安装程序并按提示安装
echo  3. 安装完成后，重新运行"开始下载.bat"
echo.
echo  [按任意键退出...]
echo ==========================================
pause >nul
exit /b 1

:InstallMethod2
echo.
echo [信息] 您选择了：快速安装版（PowerShell）
echo [倒计时] 5秒后开始安装...
echo [提示] 此操作需要管理员权限，请允许PowerShell运行
echo.

for /l %%i in (5,-1,1) do (
    echo [倒计时] %%i秒后开始安装...
    timeout /t 1 /nobreak >nul
)

echo [操作] 正在启动PowerShell安装...
echo.

powershell -Command "Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -Command \"irm https://ollama.com/install.ps1 | iex\"' -Verb RunAs -Wait"

echo.
echo [检查] 验证安装结果...
timeout /t 3 >nul

ollama ps >nul 2>&1
if %errorlevel% == 0 (
    echo [OK] Ollama安装成功！
    call :UpdateConfig 1
    echo [信息] 安装完成！按任意键继续启动下载器...
    pause >nul
    goto CheckPython
) else (
    echo [错误] 安装可能失败，请手动安装后重试
    pause
    exit /b 1
)

:UpdateConfig
set "configFile=%~dp0config.txt"
if not exist "%configFile%" (
    echo [警告] 配置文件不存在，跳过更新
    exit /b 0
)

powershell -Command "(Get-Content '%configFile%') -replace 'is_ollama_installed=.*', 'is_ollama_installed=%~1' | Set-Content '%configFile%' -Encoding UTF8"
echo [配置] 已更新安装状态（is_ollama_installed=%~1）
exit /b 0

:CheckPython
echo.
echo [步骤2/2] 检查Python环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到Python！
    echo [提示] 请先安装Python 3.x：https://www.python.org/downloads/
    echo        安装时请勾选"Add Python to PATH"
    pause
    exit /b 1
)

echo [OK] Python环境检测通过！
echo.

for /f "tokens=2 delims==" %%a in ('findstr "default_max_speed=" config.txt') do set "maxSpeed=%%a"
for /f "tokens=2 delims==" %%a in ('findstr "threshold_percent=" config.txt') do set "threshold=%%a"

echo [提示] 模型示例：qwen3:0.6b llama3:8b qwen2.5:7b
set /p modelName="请输入模型名称："
if "%modelName%"=="" set modelName=qwen3:0.6b
echo [确认] 已选择模型：%modelName%
echo.

echo ==========================================
echo [配置信息]（可在config.txt中修改）：
echo   预估速度：%maxSpeed% MB/s
echo   阈值比例：%threshold%%%
echo ==========================================
echo.

python ollama_downloader.py "%modelName%"

echo.
echo ==========================================
echo           下载程序已结束
echo ==========================================
pause
