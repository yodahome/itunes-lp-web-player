@echo off
cd /d "%~dp0"

:: Detect Windows UI language (de-* -> German, otherwise English)
powershell -NoProfile -Command "if ((Get-UICulture).Name -like 'de*') { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
  set "IS_DE=1"
  title iTunes LP Sammlung ^(http://127.0.0.1:8000^)
) else (
  set "IS_DE=0"
  title iTunes LP Collection ^(http://127.0.0.1:8000^)
)

:: Open default browser after 1 second
start "" cmd /c "timeout /t 1 /nobreak >nul & start http://127.0.0.1:8000"

echo ========================================================
if "%IS_DE%"=="1" (
  echo   iTunes LP Server laeuft unter http://127.0.0.1:8000
  echo   Dieses Fenster geoeffnet lassen, solange du hoerst.
  echo   Zum Beenden einfach das Fenster schliessen ^(oder Strg+C^).
) else (
  echo   iTunes LP server is running at http://127.0.0.1:8000
  echo   Keep this window open while listening.
  echo   Close this window ^(or press Ctrl+C^) to stop the server.
)
echo ========================================================
echo.

python "%~dp0serve_itlp.py"
pause