@echo off
rem Allure -- generateur de polaires de vitesse a partir de trames NMEA0183
rem Copyright (C) 2026 ETDEL -- GPL-3.0-or-later (voir LICENSE)
rem
rem Installe les bibliotheques dont Allure a besoin, pour la commande
rem "python" (celle qu'utilise le README pour lancer Allure) :
rem   - matplotlib          indispensable (trace des polaires et des cadrans)
rem   - pystray + pillow    facultatifs (rangement dans la zone de notification)
rem
rem tkinter ne s'installe PAS par pip : il fait partie de l'installateur de
rem Python (case "tcl/tk and IDLE"). Ce script verifie sa presence et dit
rem quoi faire s'il manque.
rem
rem Les succes et echecs se lisent avec && et || plutot qu'avec
rem "if errorlevel 1" : ce dernier teste "code >= 1" et prendrait pour un
rem succes un Python qui plante (codes Windows negatifs).
rem
rem Usage : double-clic, ou "installer_dependances.bat" en invite de commande.

setlocal EnableExtensions
title Allure - installation des dependances
rem pushd plutot que cd : il sait aussi entrer dans un dossier reseau
rem (\\serveur\partage), en lui pretant une lettre de lecteur.
pushd "%~dp0" || goto :no_folder

echo ==============================================================
echo   Allure - installation des dependances
echo ==============================================================
echo.

rem ---------------------------------------------------------------------
rem 1. Trouver Python (3.8 ou plus recent)
rem
rem "python" d'abord : c'est la commande que le README fait utiliser pour
rem lancer Allure, il faut donc installer pour CET interprete-la. Le
rem lanceur "py" ne sert que de repli -- il peut designer un autre Python
rem que celui du PATH. L'alias du Microsoft Store (python.exe qui ouvre le
rem Store) echoue au test de version et n'est donc jamais retenu.
rem ---------------------------------------------------------------------
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && set "PY=py -3"
)
if not defined PY goto :no_python

echo Python utilise :
%PY% -c "import sys; print('  ' + sys.executable); print('  version ' + sys.version.split()[0] + ' (' + str(64 if sys.maxsize > 2**32 else 32) + ' bits)')"
echo.

rem ---------------------------------------------------------------------
rem 2. tkinter (fourni par l'installateur de Python, pas par pip)
rem ---------------------------------------------------------------------
set "TK_OK=1"
%PY% -c "import tkinter" >nul 2>&1 || set "TK_OK=0"
if "%TK_OK%"=="1" (
    echo [OK]  tkinter est present.
) else (
    echo [!!]  tkinter est ABSENT : Python a ete installe sans "tcl/tk and IDLE".
    echo       Relancez l'installateur de Python, choisissez "Modify",
    echo       cochez "tcl/tk and IDLE", puis relancez ce script.
)
echo.

rem ---------------------------------------------------------------------
rem 3. pip (normalement livre avec Python ; ensurepip le reinstalle sinon)
rem ---------------------------------------------------------------------
%PY% -m pip --version >nul 2>&1 && goto :have_pip
echo pip est absent, tentative de reinstallation...
%PY% -m ensurepip --upgrade
%PY% -m pip --version >nul 2>&1 || goto :no_pip
:have_pip

rem ---------------------------------------------------------------------
rem 4. matplotlib -- indispensable
rem ---------------------------------------------------------------------
echo Installation de matplotlib (indispensable)...
echo --------------------------------------------------------------
%PY% -m pip install --disable-pip-version-check matplotlib || goto :mpl_failed
echo.

rem ---------------------------------------------------------------------
rem 5. pystray + pillow -- facultatifs : un echec n'empeche pas Allure de
rem    fonctionner, la croix de la fenetre fermera simplement le programme
rem    au lieu de le ranger dans la zone de notification.
rem ---------------------------------------------------------------------
echo Installation de pystray et pillow (facultatifs : zone de notification)...
echo --------------------------------------------------------------
set "TRAY_OK=1"
%PY% -m pip install --disable-pip-version-check pystray pillow || set "TRAY_OK=0"
echo.

rem ---------------------------------------------------------------------
rem 6. Verification finale
rem ---------------------------------------------------------------------
echo ==============================================================
echo   Bilan
echo ==============================================================
%PY% -c "import matplotlib; print('[OK]  matplotlib ' + matplotlib.__version__)" || goto :mpl_failed
if "%TRAY_OK%"=="1" (
    echo [OK]  pystray + pillow : la croix rangera Allure pres de l'horloge.
) else (
    echo [--]  pystray + pillow non installes : facultatif, Allure fonctionne
    echo       sans -- la croix fermera simplement le programme.
)
if "%TK_OK%"=="0" (
    echo [!!]  tkinter manque toujours : voir plus haut, Allure ne pourra pas
    echo       s'ouvrir tant qu'il n'est pas installe.
    goto :end_error
)
echo.
echo Tout est en place. Lancez Allure avec :
echo     %PY% allure.py
goto :end_ok

rem ---------------------------------------------------------------------
:no_folder
echo [!!]  Impossible d'entrer dans le dossier de ce script.
goto :end_error_nopop

:no_python
echo [!!]  Aucun Python 3.8 ou plus recent n'a ete trouve.
echo.
echo       Installez-le depuis https://www.python.org/downloads/ en cochant :
echo         - "Add Python to PATH"       (premier ecran)
echo         - "tcl/tk and IDLE"          (ecran "Optional Features")
echo       puis relancez ce script.
goto :end_error

:no_pip
echo [!!]  pip est introuvable et n'a pas pu etre reinstalle.
echo       Relancez l'installateur de Python, choisissez "Modify" et cochez
echo       "pip", puis relancez ce script.
goto :end_error

:mpl_failed
echo.
echo [!!]  L'installation de matplotlib a echoue (voir le message ci-dessus).
echo.
echo       Causes les plus frequentes :
echo         - pas de connexion internet, proxy, ou pare-feu / antivirus qui
echo           interdit le reseau a python.exe (message "WinError 10013") ;
echo         - Python trop recent ou en 32 bits : aucun paquet tout fait
echo           n'existe encore pour lui -- preferer un Python 64 bits recent
echo           mais deja etabli.
echo       Installation manuelle : voir la section "Installation hors ligne"
echo       du README.
goto :end_error

:end_ok
echo.
pause
popd
endlocal
exit /b 0

:end_error
popd
:end_error_nopop
echo.
pause
endlocal
exit /b 1
