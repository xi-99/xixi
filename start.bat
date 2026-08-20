@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 WORLD3 数字生命实验控制台...
echo 浏览器将自动打开 http://localhost:8501
python -m streamlit run app.py
pause
