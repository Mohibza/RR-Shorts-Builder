@echo off
setlocal EnableDelayedExpansion
title Rebels Revolt Shorts - upload to a private GitHub repo
cd /d "%~dp0"
echo.
echo  ===========================================================
echo    Upload Rebels Revolt Shorts to a PRIVATE GitHub repository
echo  ===========================================================
echo.
set "REPO=RR-Shorts-Builder"

REM ---------- Git ----------
set "GIT=git"
where git >nul 2>&1 || (
    if exist "%ProgramFiles%\Git\cmd\git.exe" (set "GIT=%ProgramFiles%\Git\cmd\git.exe") else (
        echo [1/5] Installing Git...
        winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements -h
        set "GIT=%ProgramFiles%\Git\cmd\git.exe"
    )
)
REM ---------- GitHub CLI ----------
set "GH=gh"
where gh >nul 2>&1 || (
    if exist "%ProgramFiles%\GitHub CLI\gh.exe" (set "GH=%ProgramFiles%\GitHub CLI\gh.exe") else (
        echo [2/5] Installing GitHub CLI...
        winget install -e --id GitHub.cli --accept-package-agreements --accept-source-agreements -h
        set "GH=%ProgramFiles%\GitHub CLI\gh.exe"
    )
)
"!GIT!" --version >nul 2>&1 || (echo  Git could not be installed. Install it from git-scm.com and run this again. & pause & exit /b 1)
"!GH!" --version >nul 2>&1 || (echo  GitHub CLI could not be installed. Install it from cli.github.com and run this again. & pause & exit /b 1)

REM ---------- Sign in (opens your browser once) ----------
"!GH!" auth status >nul 2>&1 || (
    echo [3/5] Sign in to GitHub: your browser opens. Copy the one-time code shown here into the page.
    "!GH!" auth login --hostname github.com --git-protocol https --web
    "!GH!" auth status >nul 2>&1 || (echo  GitHub sign-in did not finish. Run this again. & pause & exit /b 1)
)
"!GH!" auth setup-git >nul 2>&1
for /f "delims=" %%U in ('""%GH%" api user --jq .login"') do set "ME=%%U"
for /f "delims=" %%I in ('""%GH%" api user --jq .id"') do set "MYID=%%I"
if not defined ME (echo  Couldn't read your GitHub username. Run this again. & pause & exit /b 1)
echo      Signed in as !ME!

REM ---------- Commit ----------
echo [4/5] Preparing the files...
if not exist ".git" "!GIT!" init -b main >nul
"!GIT!" config user.name >nul 2>&1 || "!GIT!" config user.name "!ME!"
"!GIT!" config user.email >nul 2>&1 || "!GIT!" config user.email "!MYID!+!ME!@users.noreply.github.com"
"!GIT!" add -A
"!GIT!" commit -q -m "Rebels Revolt Shorts" >nul 2>&1
"!GIT!" log -1 --oneline >nul 2>&1 || (echo  Nothing to upload? & pause & exit /b 1)

REM ---------- Create the private repo and push ----------
echo [5/5] Uploading to github.com/!ME!/%REPO% (private)...
"!GH!" repo view "!ME!/%REPO%" >nul 2>&1
if errorlevel 1 (
    "!GH!" repo create "%REPO%" --private --source . --remote origin --push --description "Rebels Revolt Shorts - AI Shorts generator for Windows" || (echo  Upload failed - see the message above. & pause & exit /b 1)
) else (
    "!GIT!" remote get-url origin >nul 2>&1 || "!GIT!" remote add origin "https://github.com/!ME!/%REPO%.git"
    "!GIT!" push -u origin main || (echo  Upload failed - see the message above. & pause & exit /b 1)
)
echo.
echo  ===========================================================
echo    DONE:  https://github.com/!ME!/%REPO%   (private)
echo  ===========================================================
echo.
echo  The repo is private: your friend needs an invite to open the link.
set "FRIEND="
set /p "FRIEND=Your friend's GitHub username (Enter to skip): "
if defined FRIEND (
    "!GH!" api -X PUT "repos/!ME!/%REPO%/collaborators/!FRIEND!" -f permission=pull >nul && (
        echo  Invite sent to !FRIEND!. They accept it from their email or github.com/notifications,
        echo  then open: https://github.com/!ME!/%REPO%
    ) || echo  Could not invite "!FRIEND!" - check the username.
)
"!GH!" repo view "!ME!/%REPO%" --web >nul 2>&1
echo.
echo  To upload later changes, just run this file again.
pause
