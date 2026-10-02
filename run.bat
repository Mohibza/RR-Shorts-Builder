@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Rebels Revolt Shorts is not set up yet. Running setup first...
    call setup.bat
)
REM OpenCV 5 removed the face detector files - keep 4.x
dir /b /ad ".venv\Lib\site-packages\opencv_python_headless-5*" >nul 2>&1 && (
    echo Updating a component, one moment...
    ".venv\Scripts\python.exe" -m pip install -q "opencv-python-headless>=4.9,<5"
)
".venv\Scripts\python.exe" -c "import webview" >nul 2>&1 || ".venv\Scripts\python.exe" -m pip install -q "pywebview>=5.3" >nul 2>&1
REM screen recorder: PC sound capture (optional, the recorder works without it)
".venv\Scripts\python.exe" -c "import soundcard" >nul 2>&1 || ".venv\Scripts\python.exe" -m pip install -q "soundcard>=0.4.3" >nul 2>&1
start "" ".venv\Scripts\pythonw.exe" "%~dp0app.py" %*
