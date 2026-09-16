@echo off
setlocal
title SF OData Field Finder

cd /d "%~dp0"

where py >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo.
        echo No se encontro Python 3.
        echo Instala Python desde https://www.python.org/downloads/ y vuelve a ejecutar este archivo.
        echo.
        pause
        exit /b 1
    )
    set "PYTHON=python"
)

%PYTHON% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    echo La instalacion de Python no incluye tkinter, necesario para la interfaz grafica.
    echo Instala o modifica Python con el componente 'tcl/tk and IDLE'.
    echo.
    pause
    exit /b 1
)

%PYTHON% -c "import requests" >nul 2>&1
if errorlevel 1 (
    echo Instalando la dependencia requests...
    %PYTHON% -m pip install --user requests
    if errorlevel 1 (
        echo.
        echo No fue posible instalar requests.
        echo Revisa la conexion a Internet y vuelve a ejecutar este archivo.
        echo.
        pause
        exit /b 1
    )
)

echo Iniciando SF OData Field Finder...
%PYTHON% "%~dp0sf_odata_field_finder_gui.py" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo La aplicacion termino con codigo %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%
