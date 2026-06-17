@echo off
REM SLURM ResumeProgram hook for ORB (Windows)
REM Powers up cloud nodes via ORB CLI provisioning.
set "NODE_LIST=%*"
if "%NODE_LIST%"=="" (
    echo ERROR: No node names provided
    exit /b 1
)
orb machines request --nodes "%NODE_LIST%" --scheduler slurm
