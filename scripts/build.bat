@echo off
setlocal
pushd "%~dp0.." || exit /b 1
set "CONDA_COMMAND=conda"
if defined CONDA_EXE set "CONDA_COMMAND=%CONDA_EXE%"
call "%CONDA_COMMAND%" --version >nul 2>&1
if errorlevel 1 (
    echo Conda was not found. Open Anaconda Prompt or set CONDA_EXE.
    popd
    exit /b 1
)
call "%CONDA_COMMAND%" run --no-capture-output -n bili python --version
if errorlevel 1 (
    echo Run scripts\setup.bat first to create the bili environment.
    popd
    exit /b 1
)
call "%CONDA_COMMAND%" run --no-capture-output -n bili python -m pip install -r requirements-build.txt
if errorlevel 1 (
    popd
    exit /b 1
)
call "%CONDA_COMMAND%" run --no-capture-output -n bili python -m bili_player.packaging build %*
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
