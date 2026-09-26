@echo off
setlocal EnableDelayedExpansion
title RR Shorts Builder setup
cd /d "%~dp0"
echo.
echo  ===============================================
echo    RR Shorts Builder - one-time setup
echo  ===============================================
echo.

REM ---------- 1. Python ----------
set "PY="
for %%V in (3.12 3.11 3.13 3.10) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [1/5] Python not found - installing Python 3.12 with winget...
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    set "PY=py -3.12"
    py -3.12 -c "import sys" >nul 2>&1 || (
        echo.
        echo  Python was installed. Please CLOSE this window and run setup.bat again.
        pause
        exit /b 1
    )
)
echo [1/5] Using Python: %PY%

REM ---------- 2. Virtual environment + packages ----------
if not exist ".venv\Scripts\python.exe" (
    echo [2/5] Creating virtual environment...
    %PY% -m venv .venv || (echo Failed to create venv & pause & exit /b 1)
)
echo [2/5] Installing Python packages (first time takes a few minutes)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Package install failed & pause & exit /b 1)

REM ---------- 3. FFmpeg ----------
where ffmpeg >nul 2>&1
if errorlevel 1 (
    if exist "bin\ffmpeg.exe" (
        echo [3/5] FFmpeg found in bin folder.
    ) else (
        echo [3/5] Installing FFmpeg with winget...
        winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    )
) else (
    echo [3/5] FFmpeg already installed.
)

REM ---------- 4. Deno (lets yt-dlp read all YouTube formats) ----------
where deno >nul 2>&1
if errorlevel 1 (
    echo [4/5] Installing Deno JavaScript runtime for YouTube downloads...
    winget install -e --id DenoLand.Deno --accept-package-agreements --accept-source-agreements >nul 2>&1
) else (
    echo [4/5] Deno already installed.
)

REM ---------- 5. Optional NVIDIA GPU acceleration ----------
echo.
nvidia-smi >nul 2>&1
if not errorlevel 1 (
    set /p GPU="[5/5] NVIDIA GPU detected. Install GPU acceleration for transcription (~1 GB)? (y/n): "
    if /i "!GPU!"=="y" (
        ".venv\Scripts\python.exe" -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
        echo      Turn on "Use NVIDIA GPU" in RR Shorts Builder Settings.
    )
) else (
    echo [5/5] No NVIDIA GPU detected - transcription will run on CPU.
)

REM ---------- Desktop shortcut ----------
if exist "%USERPROFILE%\Desktop\ShortsForge.lnk" del "%USERPROFILE%\Desktop\ShortsForge.lnk" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\RR Shorts Builder.lnk');" ^
  "$s.TargetPath='%~dp0.venv\Scripts\pythonw.exe';$s.Arguments='\"%~dp0app.py\"';" ^
  "$s.WorkingDirectory='%~dp0';$s.IconLocation='%~dp0assets\icon.ico';$s.Save()" >nul 2>&1

echo.
echo  Setup complete! Start RR Shorts Builder from the desktop shortcut or run.bat
echo.
pause
