@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo   信鸽 SJTU
echo   -----------------------------
echo   信鸽   http://127.0.0.1:8722/static/index.html
echo   AstrBot http://127.0.0.1:6185
echo.
python main.py
echo.
echo 已退出，按任意键关闭窗口
pause >nul