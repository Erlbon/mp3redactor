@echo off
REM Builds a standalone Windows .exe using PyInstaller.
REM Run this from a Windows machine with Python 3.11+ installed.
REM
REM Checks the result of every step and stops with a clear message on
REM failure, instead of continuing to a false "Done" message.
REM
REM Does NOT bump the version number -- run bump_version.py yourself
REM first if this build should carry a new version. Keeping that a
REM separate, deliberate step means a test/debug build doesn't inflate
REM the official version history every time you run this script.
REM
REM mp3val.exe and keyfinder-cli.exe are external binaries this app
REM shells out to, not data assets -- so they are NOT passed through
REM the spec file's datas. They're copied into dist\tools as a separate
REM step after the build instead, since core\tool_locator.py looks for
REM tools\ next to the exe itself, not PyInstaller's onefile temp
REM extraction dir. See README.md for where to get those two binaries.

cd /d "%~dp0"
echo Working directory: %cd%
echo.

echo Checking for Python...
python --version
if errorlevel 1 (
    echo.
    echo ERROR: "python" was not found on your PATH.
    echo Install Python 3.11+ from python.org and make sure to check
    echo "Add python.exe to PATH" during installation, then try again.
    pause
    exit /b 1
)
echo.

echo Installing dependencies ^(this can take a minute the first time^)...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed. Common causes: no internet connection,
    echo or a proxy/firewall blocking pip. See the error above for details.
    pause
    exit /b 1
)
echo.

echo Building mp3redactor.exe ...
REM "python -m PyInstaller" instead of the bare "pyinstaller" command --
REM pip installs the pyinstaller console script into a "Scripts" folder
REM that often isn't on PATH, especially for a per-user (non-admin)
REM Python install. "python -m" always finds it as long as it's
REM installed in this same Python environment.
REM
REM The spec's upx=True is a silent no-op unless upx.exe is actually
REM findable -- prepend the shared copy so PyInstaller's own DLL
REM compression pass (CFG-protected DLLs are auto-skipped by
REM PyInstaller itself) actually runs instead of doing nothing. This
REM is what actually shrinks the numpy/aubio-driven OpenBLAS DLL that
REM otherwise makes this the biggest exe in the family.
set "PATH=%~dp0..\_shared-tools\upx;%PATH%"
python -m PyInstaller mp3redactor.spec --noconfirm
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed. See the error output above for details.
    echo Common causes: missing Python dependencies or a PyQt6 install
    echo problem.
    pause
    exit /b 1
)
echo.

if not exist "dist\mp3redactor.exe" (
    echo.
    echo ERROR: PyInstaller reported success but dist\mp3redactor.exe
    echo was not found. Please copy the full output above and report it.
    pause
    exit /b 1
)

echo Copying external tool binaries ^(mp3val.exe, keyfinder-cli.exe^) alongside the exe...
if exist "tools\" (
    xcopy /E /I /Y tools dist\tools >nul
) else (
    echo   NOTE: no tools\ folder found -- skipping. File-integrity
    echo   checking/fixing and key detection will report TOOL MISSING
    echo   until mp3val.exe / keyfinder-cli.exe are placed in tools\
    echo   and this build is re-run. See README.md.
)
echo.

echo.
echo ================================================================
echo  SUCCESS. Your app is at: %cd%\dist\mp3redactor.exe
echo  That one file ^(plus the dist\tools folder alongside it, if
echo  present^) can be copied anywhere and run with no Python install
echo  needed.
echo ================================================================
pause
