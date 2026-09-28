@echo off
setlocal EnableDelayedExpansion
title Rebels Revolt Shorts - upload the latest version to GitHub
cd /d "%~dp0"
echo.
echo  Uploading the latest Rebels Revolt Shorts to your private GitHub repo...
echo.
set "GIT=git"
where git >nul 2>&1 || (if exist "%ProgramFiles%\Git\cmd\git.exe" (set "GIT=%ProgramFiles%\Git\cmd\git.exe") else (
    echo  Git isn't installed. Run push_to_github.bat once first. & pause & exit /b 1))
if not exist ".git" (echo  This folder isn't linked to GitHub yet. Run push_to_github.bat once first. & pause & exit /b 1)
for /f "delims=" %%V in ('".venv\Scripts\python.exe" -c "import shortsforge;print(shortsforge.__version__)" 2^>nul') do set "VER=%%V"
if not defined VER set "VER=update"
"!GIT!" add -A
"!GIT!" commit -q -m "Rebels Revolt Shorts v!VER!" >nul 2>&1
"!GIT!" push origin main >nul 2>&1
if errorlevel 1 (
    REM GitHub has changes this PC doesn't have yet (e.g. uploaded from another folder or edited on github.com):
    REM bring them in, keeping this PC's version wherever the same lines differ, then upload again
    echo  GitHub has newer changes from somewhere else - combining them with this version...
    "!GIT!" fetch origin
    "!GIT!" merge --no-edit --allow-unrelated-histories -X ours origin/main -m "Combine GitHub changes with v!VER!"
    if errorlevel 1 (
        "!GIT!" merge --abort >nul 2>&1
        echo  Couldn't combine them automatically. Saving GitHub's copy as a backup branch and uploading this version...
        for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmm"') do set "STAMP=%%D"
        "!GIT!" push origin "origin/main:refs/heads/backup-!STAMP!"
        "!GIT!" push --force-with-lease origin main
    ) else (
        "!GIT!" push origin main
    )
)
if errorlevel 1 (
    echo.
    echo  Upload failed. If it asks you to sign in, run push_to_github.bat once - it signs you in to GitHub.
    pause & exit /b 1
)
for /f "delims=" %%U in ('"!GIT!" remote get-url origin') do set "URL=%%U"
echo.
echo  DONE - v!VER! is on !URL:.git=!
echo.
pause
