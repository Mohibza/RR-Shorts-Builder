@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo RR Shorts Builder is not set up yet. Running setup first...
    call setup.bat
)
REM OpenCV 5 removed the face detector files - keep 4.x
dir /b /ad ".venv\Lib\site-packages\opencv_python_headless-5*" >nul 2>&1 && (
    echo Updating a component, one moment...
    ".venv\Scripts\python.exe" -m pip install -q "opencv-python-headless>=4.9,<5"
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0app.py"
