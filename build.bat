@echo off
REM Close SalesAutomation.exe before rebuilding (avoids "file in use" errors).
REM Produces dist\SalesAutomation.exe with the blue "S" icon from assets\app.ico (not the default Python icon).
cd /d "%~dp0"
py -3.12 scripts\build_windows_exe.py
if errorlevel 1 (
  echo Build failed. Try: py -0p  to list Pythons, or edit this script to use your python.exe path.
  exit /b 1
)
echo.
echo Done: dist\SalesAutomation.exe  (branded icon from assets\app.ico)
echo If an older exe nearby shows the yellow Python snake, delete it — it was built without this flow.
exit /b 0
