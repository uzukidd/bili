@echo off
setlocal
pushd "%~dp0.." || exit /b 1
set "CONDA_COMMAND=conda"
if defined CONDA_EXE set "CONDA_COMMAND=%CONDA_EXE%"
call "%CONDA_COMMAND%" --version >nul 2>&1
if errorlevel 1 (
    echo Conda was not found. Open Anaconda Prompt or add Conda to PATH.
    popd
    exit /b 1
)
call "%CONDA_COMMAND%" run --no-capture-output -n bili python scripts\check_stream.py %*
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
