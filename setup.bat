@echo off
echo Setting up Telemetry Viewer dependencies...

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed or not in your PATH.
    pause
    exit /b
)

:: Install dependencies
echo Installing requirements...
pip install -r requirements.txt

echo.
echo Setup complete! You can now run your scripts.
pause