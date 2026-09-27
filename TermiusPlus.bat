@echo off
cd /d "%~dp0"
where py >nul 2>&1 && py -3 "%~dp0launcher\windows_app.py" && exit /b 0
where python >nul 2>&1 && python "%~dp0launcher\windows_app.py" && exit /b 0
echo 未找到 Python。请安装 Python 3.9 或更高版本，或在项目目录运行 python app.py。
pause
exit /b 1
