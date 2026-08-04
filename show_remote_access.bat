@echo off
setlocal
chcp 65001 >nul
title 查看手机访问地址
set "TAILSCALE_EXE=%ProgramFiles%\Tailscale\tailscale.exe"
if not exist "%TAILSCALE_EXE%" (
    echo [错误] 电脑尚未安装 Tailscale。
    pause
    exit /b 1
)
"%TAILSCALE_EXE%" funnel status
echo.
echo 上方 https:// 开头的地址就是手机访问地址。
pause
