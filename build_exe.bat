@echo off
rem Builds dist\ReelCheck.exe (needs the venv created by "ReelCheck.bat")
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\ReelCheck\venv"
for /f "delims=" %%B in ('call "%VENV%\Scripts\python.exe" -c "import sys;print(sys.base_prefix)"') do set "BASE=%%B"
call "%VENV%\Scripts\python.exe" -m pip install -q pyinstaller
set "EXTRA="
rem Anaconda Python keeps its DLLs in Library\bin, PyInstaller misses them
if exist "%BASE%\Library\bin\tcl86t.dll" set EXTRA=--add-binary "%BASE%\Library\bin\libexpat.dll;." --add-binary "%BASE%\Library\bin\expat.dll;." --add-binary "%BASE%\Library\bin\tcl86t.dll;." --add-binary "%BASE%\Library\bin\tk86t.dll;." --add-binary "%BASE%\Library\bin\ffi.dll;." --add-binary "%BASE%\Library\bin\zlib.dll;." --add-data "%BASE%\Library\lib\tcl8.6;_tcl_data" --add-data "%BASE%\Library\lib\tk8.6;_tk_data"
call "%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed --name "ReelCheck" --collect-all av --hidden-import pyexpat --hidden-import _tkinter --collect-submodules app --icon "%~dp0assets\icon.ico" --add-data "%~dp0assets\icon.ico;assets" --add-data "%~dp0assets\icon.png;assets" %EXTRA% --workpath "%TEMP%\reelcheckbuild" --specpath "%TEMP%\reelcheckbuild" reelcheck.py
