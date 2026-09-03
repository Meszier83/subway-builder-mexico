@echo off
chcp 65001 > nul
title Subway Builder Mexico - Wizard Studio v6.3
echo ================================================================
echo   SUBWAY BUILDER MEXICO v6.3 - WIZARD STUDIO
echo   Identidad Grafica: Metro CDMX / Lance Wyman Standard
echo ================================================================
echo.
echo Iniciando servidor local en http://127.0.0.1:8080 ...
echo.

python tools\wizard.py %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Ocurrio un error al ejecutar el servidor del Wizard.
    pause
)
