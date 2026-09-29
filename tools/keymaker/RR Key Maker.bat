@echo off
rem Opens the RR Key Maker window (for you only - never ship this folder to customers).
cd /d "%~dp0"
set PY=python
if exist "..\..\.venv\Scripts\python.exe" set PY=..\..\.venv\Scripts\python.exe
%PY% rr_keymaker.py
if errorlevel 1 pause
