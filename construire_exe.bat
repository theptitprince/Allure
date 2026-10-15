@echo off
rem Allure -- generateur de polaires de vitesse a partir de trames NMEA0183
rem Copyright (C) 2026 ETDEL -- GPL-3.0-or-later (voir LICENSE)
rem
rem Allure, par ETDEL -- https://github.com/theptitprince/Allure
rem Licence GPL-3.0-or-later, avec des conditions additionnelles (article 7)
rem decrites dans le fichier NOTICE : cette mention doit rester visible.
rem
rem Construit Allure.exe : un executable Windows AUTONOME (Python, tkinter et
rem matplotlib embarques), qui se lance sans rien installer sur le poste qui
rem le recoit.
rem
rem Etapes :
rem   1. trouver le Python du poste de construction ;
rem   2. verifier que matplotlib y est (sinon : installer_dependances.bat) ;
rem   3. installer PyInstaller s'il manque ;
rem   4. dessiner l'icone (disque bleu, voile blanche) -- allure_config.write_ico,
rem      aucun fichier image a distribuer ;
rem   5. empaqueter, puis poser Allure.exe A COTE de allure.py.
rem
rem Pourquoi a cote de allure.py : l'exe range ses donnees (donnees\, reglages\,
rem tampon\, journaux\) a cote de LUI-MEME. Pose ici, il retrouve donc
rem l'entrepot et les reglages deja accumules en lancant allure.py -- et
rem inversement. Deplace ailleurs, il repartirait d'un entrepot vide.
rem
rem Les succes et echecs se lisent avec && et || plutot qu'avec
rem "if errorlevel 1" : ce dernier teste "code >= 1" et prendrait pour un
rem succes un Python qui plante (codes Windows negatifs).
rem
rem Usage : double-clic, ou "construire_exe.bat" en invite de commande.

setlocal EnableExtensions
title Allure - construction de Allure.exe
rem pushd plutot que cd : il sait aussi entrer dans un dossier reseau
rem (\\serveur\partage), en lui pretant une lettre de lecteur. Sans lui, un
rem cd rate laisserait le script travailler dans un AUTRE dossier.
pushd "%~dp0" || goto :no_folder

echo ==============================================================
echo   Allure - construction de Allure.exe
echo ==============================================================
echo.

if not exist "allure.py" goto :no_sources

rem ---------------------------------------------------------------------
rem 1. Python -- meme recherche que installer_dependances.bat
rem ---------------------------------------------------------------------
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && set "PY=py -3"
)
if not defined PY goto :no_python

echo Python utilise :
%PY% -c "import sys; print('  ' + sys.executable); print('  version ' + sys.version.split()[0])"
echo.

rem ---------------------------------------------------------------------
rem 2. Ce que l'exe doit embarquer : tkinter et matplotlib. pystray et
rem    pillow sont facultatifs -- embarques s'ils sont la, sinon l'exe se
rem    ferme simplement a la croix au lieu de se ranger pres de l'horloge.
rem ---------------------------------------------------------------------
%PY% -c "import tkinter" >nul 2>&1 || goto :no_tk
%PY% -c "import matplotlib" >nul 2>&1 || goto :no_mpl
echo [OK]  tkinter et matplotlib sont presents.
set "TRAY_OPT="
%PY% -c "import pystray, PIL" >nul 2>&1 && set "TRAY_OPT=--hidden-import pystray._win32"
if defined TRAY_OPT (
    echo [OK]  pystray et pillow seront embarques.
) else (
    echo [--]  pystray/pillow absents : l'exe n'aura pas la zone de notification.
)
echo.

rem ---------------------------------------------------------------------
rem 3. PyInstaller
rem ---------------------------------------------------------------------
%PY% -m PyInstaller --version >nul 2>&1 && goto :have_pyinstaller
echo Installation de PyInstaller...
echo --------------------------------------------------------------
%PY% -m pip install --disable-pip-version-check pyinstaller
%PY% -m PyInstaller --version >nul 2>&1 || goto :no_pyinstaller
echo.
:have_pyinstaller
for /f "delims=" %%v in ('%PY% -m PyInstaller --version') do echo [OK]  PyInstaller %%v
echo.

rem ---------------------------------------------------------------------
rem 4. L'icone, dessinee par le programme lui-meme
rem ---------------------------------------------------------------------
if not exist "build" mkdir "build"
%PY% -c "import allure_config as c; c.write_ico(r'build\allure.ico')" || goto :no_icon
echo [OK]  Icone generee (disque bleu, voile blanche).
echo.

rem ---------------------------------------------------------------------
rem 5. Empaquetage
rem
rem   --onefile   un seul fichier a copier
rem   --windowed  pas de fenetre noire : les erreurs vont dans
rem               journaux\erreurs.log et dans une boite de dialogue
rem   --icon      chemin ABSOLU : PyInstaller resout les chemins relatifs
rem               depuis le dossier du .spec, pas depuis celui-ci
rem
rem L'Allure.exe existant n'est PAS supprime avant : si la construction
rem echoue, on garde celui qui marchait. On verifie seulement qu'il n'est
rem pas en cours d'utilisation (ouverture en ajout, sans rien ecrire), sans
rem quoi le remplacement final echouerait apres plusieurs minutes de travail.
rem ---------------------------------------------------------------------
if exist "Allure.exe" (
    2>nul (>>"Allure.exe" call;) || goto :exe_locked
)
echo Empaquetage (une a plusieurs minutes)...
echo --------------------------------------------------------------
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name Allure ^
    --icon "%~dp0build\allure.ico" ^
    --distpath "%~dp0build\dist" ^
    --workpath "%~dp0build\work" ^
    --specpath "%~dp0build" ^
    %TRAY_OPT% ^
    allure.py || goto :build_failed
if not exist "build\dist\Allure.exe" goto :build_failed
move /y "build\dist\Allure.exe" "Allure.exe" >nul || goto :build_failed
rmdir /s /q "build" >nul 2>&1

echo.
echo ==============================================================
echo   Allure.exe est pret, a cote de allure.py :
echo     "%~dp0Allure.exe"
echo ==============================================================
echo   Il partage les donnees de ce dossier (donnees\, reglages\...).
echo   Pour un autre poste : copiez Allure.exe seul -- il y creera ses
echo   propres dossiers -- puis reimportez votre sauvegarde (roue dentee
echo   ^> Sauvegardes).
echo.
echo   Premier lancement : autorisez Allure.exe dans le pare-feu Windows
echo   (reseau PRIVE), sinon les voies UDP ne recevront rien -- le
echo   pare-feu autorise un executable, pas un programme.
goto :end_ok

rem ---------------------------------------------------------------------
:no_folder
echo [!!]  Impossible d'entrer dans le dossier de ce script.
goto :end_error_nopop

:no_sources
echo [!!]  allure.py est introuvable : placez ce script dans le dossier
echo       d'Allure, a cote de allure.py, puis relancez-le.
goto :end_error

:no_python
echo [!!]  Aucun Python 3.8 ou plus recent n'a ete trouve.
echo       Installez-le depuis https://www.python.org/downloads/ (cochez
echo       "Add Python to PATH" et "tcl/tk and IDLE"), puis relancez.
goto :end_error

:no_tk
echo [!!]  tkinter est absent de ce Python : relancez l'installateur de
echo       Python, choisissez "Modify" et cochez "tcl/tk and IDLE".
goto :end_error

:no_mpl
echo [!!]  matplotlib est absent : lancez d'abord installer_dependances.bat
echo       (ou voir la section "Installation hors ligne" du README).
goto :end_error

:no_pyinstaller
echo.
echo [!!]  PyInstaller n'a pas pu etre installe (voir le message ci-dessus).
echo       Pas d'internet sur ce poste ? Voir la section
echo       "Installation hors ligne" du README : la meme methode vaut pour
echo       PyInstaller.
goto :end_error

:no_icon
echo [!!]  L'icone n'a pas pu etre generee (allure_config.py manquant ?).
goto :end_error

:exe_locked
echo [!!]  Allure.exe est en cours d'utilisation : fermez Allure (pensez a
echo       l'icone pres de l'horloge -- clic droit, Quitter), puis relancez.
goto :end_error

:build_failed
echo.
echo [!!]  L'empaquetage a echoue (voir les messages ci-dessus).
echo       Le dossier build\ est conserve pour examen ; l'ancien Allure.exe,
echo       s'il y en avait un, est intact.
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
