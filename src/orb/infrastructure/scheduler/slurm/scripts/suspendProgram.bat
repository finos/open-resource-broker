@echo off
REM SLURM SuspendProgram hook for ORB (Windows)
REM Powers down cloud nodes via ORB CLI deprovisioning.
set "NODE_LIST=%*"
if "%NODE_LIST%"=="" (
    echo ERROR: No node names provided
    exit /b 1
)
orb machines return --nodes "%NODE_LIST%" --scheduler slurm
