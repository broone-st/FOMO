@echo off
rem ============================================================
rem  FOMO (Fear of Missing Out) - launcher
rem  1) prefer the bundled runtime in .\python  (portable build)
rem  2) otherwise fall back to a REAL system Python, skipping the
rem     Microsoft Store app-execution alias: WindowsApps\python.exe
rem     only opens the Store and never actually runs the script.
rem
rem  Two lessons are baked into this file:
rem  * ONLY pythonw.exe is ever used. python.exe / py.exe are console
rem    programs: they attach a black console window that stays on
rem    screen for as long as FOMO runs.
rem  * fomo.py is compiled once before starting. pythonw.exe has no
rem    stderr, so a syntax error would make it vanish without trace.
rem  Keep this file ASCII-only: cmd parses .bat in the OEM codepage.
rem ============================================================
setlocal
cd /d %~dp0

set "PYW="
if exist "python\pythonw.exe" set "PYW=python\pythonw.exe"

if not defined PYW (
  for /f "delims=" %%P in ('where pythonw 2^>nul') do (
    if not defined PYW (
      echo %%P| findstr /i "WindowsApps" >nul
      if errorlevel 1 set "PYW=%%P"
    )
  )
)

if not defined PYW (
  for /f "delims=" %%P in ('where pyw 2^>nul') do (
    if not defined PYW (
      echo %%P| findstr /i "WindowsApps" >nul
      if errorlevel 1 set "PYW=%%P"
    )
  )
)

if not defined PYW goto :nopython

rem --- preflight: compile fomo.py once, without opening a console ---
set "PRE=%TEMP%\fomo_preflight_%RANDOM%.txt"
"%PYW%" -c "import py_compile;py_compile.compile(r'fomo.py',doraise=True)" >nul 2>"%PRE%"
if errorlevel 1 (
  echo.
  echo   [FOMO] fomo.py could not be compiled, so it was NOT started.
  echo   The interpreter said:
  echo.
  type "%PRE%"
  echo.
  echo   Fix the problem shown above, then run this launcher again.
  echo.
  pause
  del "%PRE%" >nul 2>nul
  goto :eof
)
del "%PRE%" >nul 2>nul

start "" "%PYW%" fomo.py
goto :eof

:nopython
echo.
echo   [FOMO] No usable Python runtime found.
echo.
echo   This program normally ships with its own runtime in the
echo   "python" sub-folder next to this file. Please make sure the
echo   whole FOMO folder was extracted as-is.
echo.
echo   Or install Python 3.8+ from https://www.python.org/downloads/
echo   and tick "Add python.exe to PATH" during setup.
echo.
echo   Do NOT use the Microsoft Store version for this: its
echo   "python" only opens the Store and never runs the program.
echo.
pause
