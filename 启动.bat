@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 没有检测到 Python，请先安装 Python 3.9+ 并勾选 "Add to PATH"
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo 正在启动 网盘空间分析器 ...
python run.py
if errorlevel 1 pause
