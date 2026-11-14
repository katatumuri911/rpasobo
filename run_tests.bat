@echo off
REM Run the Python test suite. See tools/run_tests.py for options.
REM Set RPCHAT_SLOW_TESTS=1 before running to include the slow (real-data) tests.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python tools/run_tests.py %*
