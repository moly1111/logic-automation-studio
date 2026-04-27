@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -c "from automation_studio import AutomationStudio; app = AutomationStudio(); app.run()"
if errorlevel 1 (
    echo.
    echo 启动失败，请确认已安装 Python 且依赖可用。
    pause
)
