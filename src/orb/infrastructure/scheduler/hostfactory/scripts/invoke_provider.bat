@echo off
set SCRIPT_DIR=%~dp0
set PARENT_DIR=%SCRIPT_DIR%..
python "%PARENT_DIR%\src\app.py" %*