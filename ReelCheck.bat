@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\ReelCheck\venv"

rem περιβάλλον από το παλιό όνομα (DCP Player): μεταφορά αντί για νέα εγκατάσταση
if not exist "%VENV%\Scripts\pythonw.exe" if exist "%LOCALAPPDATA%\DCPPlayer\venv\Scripts\pythonw.exe" (
  if not exist "%LOCALAPPDATA%\ReelCheck" mkdir "%LOCALAPPDATA%\ReelCheck"
  move "%LOCALAPPDATA%\DCPPlayer\venv" "%VENV%" >nul
)
if exist "%VENV%\Scripts\pythonw.exe" goto run

echo ============================================
echo  ReelCheck - πρώτη εκκίνηση
echo  Στήνω ό,τι χρειάζεται (θέλει internet).
echo ============================================

call :findpy
if not defined PY (
  echo.
  echo Δεν βρέθηκε Python. Την κατεβάζω και την εγκαθιστώ...
  where winget >nul 2>nul && (
    winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
  )
  call :findpy
)
if not defined PY (
  echo Κατέβασμα από python.org...
  set "INST=%TEMP%\python-3.13.1-amd64.exe"
  powershell -NoProfile -Command "Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.13.1/python-3.13.1-amd64.exe' -OutFile '!INST!'"
  if exist "!INST!" (
    "!INST!" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0
    del "!INST!" >nul 2>nul
  )
  call :findpy
)
if not defined PY (
  echo.
  echo Δεν μπόρεσα να εγκαταστήσω Python.
  echo Κατέβασέ την από https://www.python.org/downloads/ και ξανατρέξε αυτό το αρχείο.
  pause
  exit /b 1
)

echo Python: !PY!
"!PY!" -m venv "%VENV%" || (echo Αποτυχία δημιουργίας περιβάλλοντος & pause & exit /b 1)
echo Εγκατάσταση βιβλιοθηκών (av, numpy, pygame-ce, sounddevice)...
"%VENV%\Scripts\python.exe" -m pip install -q --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install -q -r requirements.txt || (
  echo Αποτυχία εγκατάστασης βιβλιοθηκών.
  rmdir /s /q "%VENV%"
  pause
  exit /b 1
)
echo Έτοιμο.

:run
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0reelcheck.py" %*
exit /b 0

:findpy
set "PY="
for %%P in ("%USERPROFILE%\anaconda3\python.exe" "%USERPROFILE%\miniconda3\python.exe" "%ProgramData%\anaconda3\python.exe" "%ProgramData%\miniconda3\python.exe" "%LOCALAPPDATA%\anaconda3\python.exe") do (
  if not defined PY if exist "%%~P" set "PY=%%~P"
)
if not defined PY (
  for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do (
    if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
  )
)
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul ^| findstr /v /i WindowsApps') do if not defined PY set "PY=%%P"
)
exit /b 0
