@echo off
setlocal
title Initialize Your Fabric Demo
cd /d "%~dp0"

REM Thin Windows shim -- all the real logic lives in the cross-platform launch.py.
where python >nul 2>&1
if errorlevel 1 (
  echo(
  echo  [X] Python is not installed.
  echo      Install it from https://www.python.org/downloads/
  echo      During setup, TICK "Add python.exe to PATH", then run this again.
  echo(
  pause
  exit /b 1
)

python "%~dp0launch.py"
pause
