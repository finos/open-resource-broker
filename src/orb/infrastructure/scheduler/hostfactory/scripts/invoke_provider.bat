@echo off
rem Delayed expansion is intentionally not enabled: %* is forwarded to the
rem CLI and any "!" in an argument must reach it unchanged. Messages that
rem print paths sit outside parenthesised blocks so a ")" in a path such as
rem "C:\Program Files (x86)\..." cannot end a block early.
setlocal

set "SCRIPT_DIR=%~dp0"

if "%USE_LOCAL_DEV%"=="" set "USE_LOCAL_DEV=false"
set "PACKAGE_COMMAND=%ORB_COMMAND%"
if "%PACKAGE_COMMAND%"=="" set "PACKAGE_COMMAND=orb"
set "PACKAGE_NAME=%ORB_PACKAGE_NAME%"
if "%PACKAGE_NAME%"=="" set "PACKAGE_NAME=open-resource-broker"

rem Activate the virtualenv if ORB_VENV_PATH is set.
if "%ORB_VENV_PATH%"=="" goto :findroot_init
if not exist "%ORB_VENV_PATH%\Scripts\activate.bat" goto :novenv
call "%ORB_VENV_PATH%\Scripts\activate.bat"

:findroot_init
rem Walk up from SCRIPT_DIR until we find src\orb\run.py (works whether scripts
rem are at scripts\ after orb init or under src\orb\infrastructure in a checkout).
set "PROJECT_ROOT=%SCRIPT_DIR%"
set "LEVELS=0"
:findroot
if exist "%PROJECT_ROOT%src\orb\run.py" goto :foundroot
set /a LEVELS+=1
if %LEVELS% GEQ 10 goto :foundroot
pushd "%PROJECT_ROOT%.."
set "PROJECT_ROOT=%CD%\"
popd
goto :findroot
:foundroot

if /i "%USE_LOCAL_DEV%"=="true" goto :localdev
if "%USE_LOCAL_DEV%"=="1" goto :localdev
goto :packagemode

:localdev
rem Local development mode - run src\orb\run.py directly.
if not exist "%PROJECT_ROOT%src\orb\run.py" goto :norunpy

set "PYTHONPATH=%PROJECT_ROOT%src;%PROJECT_ROOT%;%PYTHONPATH%"

if exist "%PROJECT_ROOT%.venv\Scripts\python.exe" goto :venvpython
rem Prefer "python": "python3" on Windows can resolve to the Microsoft Store stub.
where python >nul 2>nul
if not errorlevel 1 goto :systempython
where python3 >nul 2>nul
if not errorlevel 1 goto :systempython3
goto :nopython

:venvpython
set "PYTHON_CMD=%PROJECT_ROOT%.venv\Scripts\python.exe"
goto :runpython
:systempython
set "PYTHON_CMD=python"
goto :runpython
:systempython3
set "PYTHON_CMD=python3"
goto :runpython

:runpython
"%PYTHON_CMD%" "%PROJECT_ROOT%src\orb\run.py" %*
exit /b %ERRORLEVEL%

:packagemode
rem Package mode - use the installed console script.
where %PACKAGE_COMMAND% >nul 2>nul
if errorlevel 1 goto :nocommand

%PACKAGE_COMMAND% %*
exit /b %ERRORLEVEL%

:novenv
echo Error: ORB_VENV_PATH set but %ORB_VENV_PATH%\Scripts\activate.bat not found 1>&2
exit /b 1

:norunpy
echo Error: src\orb\run.py not found at %PROJECT_ROOT%src\orb\run.py 1>&2
echo Make sure you're running from the correct directory or install the package. 1>&2
exit /b 1

:nopython
echo Error: Python not found 1>&2
exit /b 1

:nocommand
echo Error: %PACKAGE_COMMAND% command not found 1>&2
echo. 1>&2
echo Options: 1>&2
echo   1. Install package: pip install %PACKAGE_NAME% 1>&2
echo   2. Use local development: set USE_LOCAL_DEV=true ^&^& %~nx0 ^<arguments^> 1>&2
exit /b 1
