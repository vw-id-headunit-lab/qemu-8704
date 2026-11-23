@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0.." || exit /b 1
rem Read the whole block before waiting on Python: edits during a session
rem must not make cmd.exe resume at a stale byte offset in this file.
(
    if exist ".venv\Scripts\python.exe" (
        ".venv\Scripts\python.exe" "run.py" run --config "config.local.json" --mode hmi --open-viewer --seconds 3600 --language zh_CN %*
    ) else (
        python "run.py" run --config "config.local.json" --mode hmi --open-viewer --seconds 3600 --language zh_CN %*
    )
    if errorlevel 1 (
        echo.
        echo HMI launcher reported an error. See the message above.
        pause
        exit /b 1
    ) else (
        echo.
        echo HMI session ended. Its previous viewer URL is no longer active.
        pause
        exit /b 0
    )
)
