@echo off
setlocal
pushd "%~dp0.." || exit /b 1
set "CONDA_COMMAND=conda"
if defined CONDA_EXE set "CONDA_COMMAND=%CONDA_EXE%"
call "%CONDA_COMMAND%" run --no-capture-output -n bili python -B scripts/check_list_gui.py %*
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
