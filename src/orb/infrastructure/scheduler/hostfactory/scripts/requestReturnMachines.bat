@echo off
set "HF_CALLER_SCRIPT=%~nx0"
call "%~dp0invoke_provider.bat" machines return %*
