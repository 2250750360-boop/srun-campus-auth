@echo off
cd /d "%~dp0"
python srun_auth.py %*
pause
