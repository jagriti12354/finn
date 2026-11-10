@echo off
rem Run by Windows Task Scheduler. Usage: cycle.cmd filings ^| close ^| digest
setlocal
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set PY=.venv\Scripts\python.exe
if not exist data\logs mkdir data\logs
set LOG=data\logs\cycle.log
echo ===== %date% %time% %1 >> %LOG%

if /i "%1"=="filings" (
    %PY% -m ingest.announcements --days 30 >> %LOG% 2>&1
    %PY% -m ingest.enrich >> %LOG% 2>&1
    %PY% -m ingest.push >> %LOG% 2>&1
    %PY% -m ingest.publish >> %LOG% 2>&1
)
if /i "%1"=="close" (
    %PY% -m ingest.prices >> %LOG% 2>&1
    %PY% -m ingest.push >> %LOG% 2>&1
    %PY% -m ingest.publish >> %LOG% 2>&1
)
if /i "%1"=="digest" (
    %PY% -m ingest.push --digest >> %LOG% 2>&1
)
endlocal
