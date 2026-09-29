@echo off
setlocal EnableDelayedExpansion
title Rebels Revolt Shorts - GitHub
cd /d "%~dp0"
set "GIT=git"
where git >nul 2>&1 || (if exist "%ProgramFiles%\Git\cmd\git.exe" (set "GIT=%ProgramFiles%\Git\cmd\git.exe") else (set "GIT="))

:menu
cls
echo.
echo  ==============================================
echo    Rebels Revolt Shorts  -  GitHub
echo  ==============================================
echo.
echo    1  Pull   - download the latest version from GitHub
echo    2  Push   - upload this PC's version to GitHub
echo    3  Status - see what changed on this PC
echo    4  Exit
echo.
choice /c 1234 /n /m "  Press 1, 2, 3 or 4: "
set "PICK=%errorlevel%"
if "%PICK%"=="4" exit /b 0
if not defined GIT (
    echo.
    echo  Git isn't installed yet. Setting it up and linking this folder to GitHub...
    call "%~dp0push_to_github.bat"
    goto menu
)
if not exist ".git" (
    echo.
    echo  This folder isn't linked to GitHub yet. Linking it now ^(one time^)...
    call "%~dp0push_to_github.bat"
    goto menu
)
for /f "delims=" %%B in ('"!GIT!" rev-parse --abbrev-ref HEAD 2^>nul') do set "BR=%%B"
if not defined BR set "BR=main"
for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmm"') do set "STAMP=%%D"
if "%PICK%"=="1" goto pull
if "%PICK%"=="2" goto push
if "%PICK%"=="3" goto status
goto menu

REM ------------------------------------------------------------------ PULL
:pull
echo.
echo  Downloading the latest version from GitHub...
"!GIT!" fetch origin || (echo  Couldn't reach GitHub. Check your internet / sign-in and try again. & pause & goto menu)
REM keep anything changed on this PC safe first (a commit + a backup branch you can always go back to)
"!GIT!" add -A
"!GIT!" diff --cached --quiet || (
    "!GIT!" commit -q -m "Changes on this PC before pull !STAMP!"
    echo  Your local changes were saved first ^(backup branch: pc-backup-!STAMP!^).
)
"!GIT!" branch -q "pc-backup-!STAMP!" >nul 2>&1
"!GIT!" merge --no-edit -X theirs "origin/!BR!" -m "Pull from GitHub !STAMP!"
if errorlevel 1 (
    "!GIT!" merge --abort >nul 2>&1
    echo.
    echo  Couldn't combine automatically. Taking GitHub's version exactly
    echo  ^(this PC's version is kept in branch pc-backup-!STAMP!^).
    "!GIT!" reset -q --hard "origin/!BR!"
)
echo.
echo  DONE - this folder now has the latest version from GitHub.
echo  If the app is open, close it and start it again.
echo.
pause
goto menu

REM ------------------------------------------------------------------ PUSH
:push
echo.
echo  Uploading this PC's version to GitHub...
for /f "delims=" %%V in ('".venv\Scripts\python.exe" -c "import shortsforge;print(shortsforge.__version__)" 2^>nul') do set "VER=%%V"
if not defined VER set "VER=update"
"!GIT!" add -A
"!GIT!" commit -q -m "Rebels Revolt Shorts v!VER! (!STAMP!)" >nul 2>&1
"!GIT!" push origin "!BR!" >nul 2>&1
if errorlevel 1 (
    echo  GitHub has newer changes from somewhere else - combining them with this version...
    "!GIT!" fetch origin
    "!GIT!" merge --no-edit --allow-unrelated-histories -X ours "origin/!BR!" -m "Combine GitHub changes with v!VER!"
    if errorlevel 1 (
        "!GIT!" merge --abort >nul 2>&1
        echo  Couldn't combine them automatically. Saving GitHub's copy as a backup branch and uploading this version...
        "!GIT!" push origin "origin/!BR!:refs/heads/github-backup-!STAMP!"
        "!GIT!" push --force-with-lease origin "!BR!"
    ) else (
        "!GIT!" push origin "!BR!"
    )
)
if errorlevel 1 (
    echo.
    echo  Upload failed. If GitHub asks you to sign in, choose 2 again after signing in,
    echo  or run push_to_github.bat once to sign in.
    pause
    goto menu
)
for /f "delims=" %%U in ('"!GIT!" remote get-url origin') do set "URL=%%U"
echo.
echo  DONE - v!VER! is on !URL:.git=!
echo.
pause
goto menu

REM ------------------------------------------------------------------ STATUS
:status
echo.
"!GIT!" fetch -q origin >nul 2>&1
echo  Branch: !BR!
for /f %%A in ('"!GIT!" rev-list --count "origin/!BR!..HEAD" 2^>nul') do echo  Commits on this PC not on GitHub yet: %%A
for /f %%A in ('"!GIT!" rev-list --count "HEAD..origin/!BR!" 2^>nul') do echo  Commits on GitHub not on this PC yet: %%A
echo.
echo  Changed files on this PC ^(not uploaded^):
"!GIT!" status --short
echo.
pause
goto menu
