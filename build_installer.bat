@echo off
setlocal EnableDelayedExpansion
title Rebels Revolt Shorts - build installer
cd /d "%~dp0"
echo.
echo  ===========================================================
echo    Building Rebels-Revolt-Shorts-Setup.exe (share it with anyone -
echo    they don't need Python, FFmpeg or anything else)
echo  ===========================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo  Run setup.bat first.
    pause & exit /b 1
)
set "PY=.venv\Scripts\python.exe"

echo [1/6] Updating build tools...
"%PY%" -m pip install -q --upgrade pip >nul
"%PY%" -m pip install -q -r requirements.txt || (echo Package install failed & pause & exit /b 1)
if not exist "web\dist\index.html" (echo  The interface files web\dist are missing. & pause & exit /b 1)
"%PY%" -m pip install -q --upgrade pyinstaller || (echo PyInstaller install failed & pause & exit /b 1)

echo [2/6] Packaging the app (takes a few minutes)...
if exist "dist\RRShortsBuilder" rmdir /s /q "dist\RRShortsBuilder"
"%PY%" -m PyInstaller RRShortsBuilder.spec --noconfirm --clean || (echo Packaging failed & pause & exit /b 1)

echo [3/6] Bundling FFmpeg and Deno...
if not exist "dist\RRShortsBuilder\bin" mkdir "dist\RRShortsBuilder\bin"
set "FF="
if exist "bin\ffmpeg.exe" set "FF=%CD%\bin\ffmpeg.exe"
if not defined FF for /f "delims=" %%F in ('where ffmpeg 2^>nul') do if not defined FF set "FF=%%F"
if not defined FF for /f "delims=" %%F in ('dir /b /s "%LOCALAPPDATA%\Microsoft\WinGet\Packages\ffmpeg.exe" 2^>nul') do if not defined FF set "FF=%%F"
if not defined FF (
    echo  FFmpeg not found - installing it with winget...
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    for /f "delims=" %%F in ('dir /b /s "%LOCALAPPDATA%\Microsoft\WinGet\Packages\ffmpeg.exe" 2^>nul') do if not defined FF set "FF=%%F"
)
if not defined FF (echo  Could not find ffmpeg.exe & pause & exit /b 1)
REM a winget "Links" entry is only a shim: copy the real exe that sits next to ffprobe
for %%D in ("!FF!") do set "FFDIR=%%~dpD"
if not exist "!FFDIR!ffprobe.exe" (
    for /f "delims=" %%F in ('dir /b /s "%LOCALAPPDATA%\Microsoft\WinGet\Packages\ffmpeg.exe" 2^>nul') do set "FF=%%F"
    for %%D in ("!FF!") do set "FFDIR=%%~dpD"
)
copy /y "!FFDIR!ffmpeg.exe" "dist\RRShortsBuilder\bin\" >nul
copy /y "!FFDIR!ffprobe.exe" "dist\RRShortsBuilder\bin\" >nul 2>&1
echo      ffmpeg: !FFDIR!ffmpeg.exe

set "DN="
for /f "delims=" %%F in ('where deno 2^>nul') do if not defined DN set "DN=%%F"
if not defined DN for /f "delims=" %%F in ('dir /b /s "%LOCALAPPDATA%\Microsoft\WinGet\Packages\deno.exe" 2^>nul') do if not defined DN set "DN=%%F"
if not defined DN if exist "%USERPROFILE%\.deno\bin\deno.exe" set "DN=%USERPROFILE%\.deno\bin\deno.exe"
if defined DN (
    copy /y "!DN!" "dist\RRShortsBuilder\bin\" >nul
    echo      deno:   !DN!
) else (
    echo      deno not found - YouTube downloads will still work for most videos
)

echo [4/6] Self-testing the packaged app...
"dist\RRShortsBuilder\RRShortsBuilder.exe" --selftest
set "ST=%ERRORLEVEL%"
type "%APPDATA%\RRShortsBuilder\selftest.txt"
if not "%ST%"=="0" (
    echo.
    echo  The self-test found a problem - see the report above. Send that text to get it fixed.
    pause & exit /b 1
)

echo [5/6] Getting Inno Setup (makes the single Setup.exe)...
set "ISCC="
for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do (
    if exist %%P if not defined ISCC set "ISCC=%%~P"
)
if not defined ISCC (
    winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements
    for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe") do (
        if exist %%P if not defined ISCC set "ISCC=%%~P"
    )
)
if not defined ISCC (echo  Inno Setup could not be installed. Install it from jrsoftware.org and run again. & pause & exit /b 1)

echo [6/6] Building the installer...
"!ISCC!" /Q installer.iss || (echo Installer build failed & pause & exit /b 1)

echo.
echo  ===========================================================
echo    DONE:  installer_output\Rebels-Revolt-Shorts-Setup-2.1.1.exe
echo    Send that one file to your friend.
echo  ===========================================================
explorer "installer_output"
pause
