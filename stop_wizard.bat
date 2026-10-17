@echo off
chcp 65001 > nul
title Subway Builder Mexico - Terminador de Procesos
echo ================================================================
echo   SUBWAY BUILDER MEXICO - TERMINADOR DE PROCESOS
echo   Identidad Grafica: Metro CDMX / Lance Wyman Standard
echo ================================================================
echo.
echo Escaneando y deteniendo procesos del Wizard y puertos ocupados...
echo.

python tools\stop_wizard.py %*

if %ERRORLEVEL% EQU 0 goto done

echo.
echo [AVISO] Ejecutando terminacion de emergencia via PowerShell nativo...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ports = @(8080, 5050, 5000); foreach ($p in $ports) { Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | ForEach-Object { if ($_.OwningProcess -gt 4) { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host ('  [x] Puerto ' + $p + ' liberado (PID ' + $_.OwningProcess + ')') -ForegroundColor Yellow } } }; Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'wizard\.py|poi_studio\.py|build\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('  [x] Proceso terminado: ' + $_.Name + ' (PID ' + $_.ProcessId + ')') -ForegroundColor Yellow }; Write-Host '  [OK] Limpieza de emergencia finalizada.' -ForegroundColor Green"

:done
echo.
pause
