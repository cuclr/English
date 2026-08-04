@echo off
setlocal
chcp 65001 >nul
title 关闭手机远程访问
set "TAILSCALE_EXE=%ProgramFiles%\Tailscale\tailscale.exe"
if not exist "%TAILSCALE_EXE%" (
    echo [错误] 电脑尚未安装 Tailscale。
    pause
    exit /b 1
)
"%TAILSCALE_EXE%" funnel --https=443 off
if errorlevel 1 (
    echo [错误] 关闭失败，请尝试右键“以管理员身份运行”。
) else (
    echo 手机远程访问已关闭，本地应用和学习数据不受影响。
)
pause
