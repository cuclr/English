@echo off
setlocal
chcp 65001 >nul
title 配置手机远程访问
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境，请先创建 .venv。
    goto :failed
)

set "TAILSCALE_EXE=%ProgramFiles%\Tailscale\tailscale.exe"
if not exist "%TAILSCALE_EXE%" (
    echo [错误] 电脑尚未安装 Tailscale。
    echo 请从 https://tailscale.com/download/windows 下载并登录免费 Personal 账号。
    goto :failed
)

".venv\Scripts\python.exe" "remote_access.py" ensure-gui
if errorlevel 1 goto :failed

echo.
echo 正在启用免费的 HTTPS 远程访问……
"%TAILSCALE_EXE%" funnel --bg http://127.0.0.1:5000
if errorlevel 1 (
    echo.
    echo [错误] 无法启用 Tailscale Funnel。
    echo 请确认 Tailscale 已登录，并尝试右键“以管理员身份运行”本脚本。
    goto :failed
)

echo.
echo 配置完成。手机无需安装 App，请使用下面显示的 HTTPS 地址访问：
"%TAILSCALE_EXE%" funnel status
echo.
echo 日常使用时，只需照常启动 English Vocabulary；电脑必须保持联网和开机。
pause
exit /b 0

:failed
echo.
echo 未完成远程访问配置。
pause
exit /b 1
