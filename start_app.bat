@echo off
setlocal
chcp 65001 >nul
title English Vocabulary
cd /d "%~dp0"

set "VOCAB_URL=http://127.0.0.1:5000"
set "VOCAB_PYTHON=%~dp0.venv\Scripts\python.exe"
set "VOCAB_APP=%~dp0app.py"
set "VOCAB_BROWSER_HELPER=%~dp0browser_launcher.py"

if not exist "%VOCAB_APP%" goto :missing_app
if not exist "%VOCAB_PYTHON%" goto :missing_venv
if not exist "%VOCAB_BROWSER_HELPER%" goto :missing_helper

"%VOCAB_PYTHON%" -c "import flask, fitz" >nul 2>nul
if errorlevel 1 goto :broken_python

"%VOCAB_PYTHON%" "%VOCAB_BROWSER_HELPER%" --check "%VOCAB_URL%" >nul 2>nul
if not errorlevel 1 (
    echo 背单词应用已经运行，正在打开浏览器...
    start "" "%VOCAB_URL%"
    exit /b 0
)

echo 正在启动背单词应用...
echo 使用期间请保留此窗口，关闭窗口即可停止应用。
start "" /b "%VOCAB_PYTHON%" "%VOCAB_BROWSER_HELPER%" "%VOCAB_URL%"
"%VOCAB_PYTHON%" "%VOCAB_APP%"
set "VOCAB_EXIT_CODE=%ERRORLEVEL%"

if not "%VOCAB_EXIT_CODE%"=="0" (
    echo.
    echo 应用启动失败。请查看上方错误信息，确认端口 5000 没有被其他程序占用。
    pause
)
exit /b %VOCAB_EXIT_CODE%

:missing_app
echo 未找到 app.py，请确认启动脚本位于项目根目录。
pause
exit /b 1

:missing_venv
echo 未找到虚拟环境，请先在项目目录创建 .venv。
pause
exit /b 1

:missing_helper
echo 未找到浏览器启动辅助文件 browser_launcher.py，请检查项目文件是否完整。
pause
exit /b 1

:broken_python
echo Python 环境异常或依赖不完整，请重新安装 requirements.txt 中的依赖。
pause
exit /b 1
