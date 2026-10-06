@echo off
REM SLURM ResumeProgram hook for ORB (Windows)
REM Powers up cloud nodes via ORB CLI provisioning.
REM Node names are validated before they reach any command, and failures of
REM scontrol and of the ORB CLI are reported with a non-zero exit code.
REM Delayed expansion is enabled only to write the node list to a file
REM without cmd re-parsing its contents.
setlocal enabledelayedexpansion

set "NODE_LIST=%~1"
if not defined NODE_LIST goto :nonodes

set "TEMPLATE_ID=%SLURM_ORB_TEMPLATE_ID%"
if "%TEMPLATE_ID%"=="" set "TEMPLATE_ID=default"

REM Validate node names: alphanumeric, hyphens, underscores, brackets, commas, spaces only.
set "CHECK_FILE=%TEMP%\orb_resume_%RANDOM%.tmp"
>"%CHECK_FILE%" echo(!NODE_LIST!
findstr /r /x /c:"[a-zA-Z0-9 ,_\[\]\-]*" "%CHECK_FILE%" >nul 2>nul
set "VALID_RC=%ERRORLEVEL%"
del "%CHECK_FILE%" >nul 2>nul
if not "%VALID_RC%"=="0" goto :badnodes

where scontrol >nul 2>nul
if errorlevel 1 goto :noscontrol

REM Expand the SLURM hostlist (handles bracket ranges like compute-[001-003]).
set "HOSTS_FILE=%TEMP%\orb_resume_hosts_%RANDOM%.tmp"
scontrol show hostnames "%NODE_LIST%" >"%HOSTS_FILE%"
set "SCONTROL_RC=%ERRORLEVEL%"
if not "%SCONTROL_RC%"=="0" goto :scontrolfailed

set "NUM_NODES=0"
for /f "usebackq" %%N in ("%HOSTS_FILE%") do set /a NUM_NODES+=1
del "%HOSTS_FILE%" >nul 2>nul
if "%NUM_NODES%"=="0" goto :nohosts

orb machines request "%TEMPLATE_ID%" %NUM_NODES% --nodes "%NODE_LIST%" --scheduler slurm
set "ORB_RC=%ERRORLEVEL%"
if not "%ORB_RC%"=="0" goto :orbfailed
exit /b 0

:nonodes
echo ERROR: No node names provided 1>&2
exit /b 1

:badnodes
echo ERROR: Invalid node name characters in node list 1>&2
exit /b 1

:noscontrol
echo ERROR: scontrol not found 1>&2
exit /b 1

:scontrolfailed
del "%HOSTS_FILE%" >nul 2>nul
echo ERROR: scontrol show hostnames failed (exit %SCONTROL_RC%) 1>&2
exit /b %SCONTROL_RC%

:nohosts
echo ERROR: Node list resolved to no hosts 1>&2
exit /b 1

:orbfailed
echo ERROR: Batch CLI request failed (exit %ORB_RC%) 1>&2
exit /b %ORB_RC%
