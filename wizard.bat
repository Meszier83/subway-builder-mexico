@echo off
chcp 65001 > nul
title Subway Builder Mexico - Wizard Studio v7.2
echo ================================================================
echo   SUBWAY BUILDER MEXICO v7.2 - WIZARD STUDIO
echo   Identidad Grafica: Metro CDMX / Lance Wyman Standard
echo ================================================================
echo.
echo Iniciando servidor local en http://127.0.0.1:8080 ...
echo.

python tools\wizard.py %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Ocurrio un error al ejecutar el servidor del Wizard.
    echo [TIP] Si el puerto 8080 ya esta ocupado o hay otra instancia abierta,
    echo       ejecuta 'stop_wizard.bat' para cerrarla y liberar los puertos.
    pause
)
