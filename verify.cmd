@echo off
REM Wrapper que lanza verify.ps1 saltándose la ExecutionPolicy de PowerShell.
REM Doble click sobre este archivo y la verificación arranca sola.

cd /d "%~dp0"
where pwsh >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    pwsh -ExecutionPolicy Bypass -NoProfile -File "%~dp0verify.ps1"
) else (
    powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0verify.ps1"
)
echo.
echo ----------------------------------------
echo Pulsa cualquier tecla para cerrar esta ventana.
pause >nul
