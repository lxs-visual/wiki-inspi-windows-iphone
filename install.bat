@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  === Installation d'Inspi Local ===
echo.
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo [X] Python introuvable. Installe Python 3.11 ou plus depuis python.org
  echo     en cochant "Add python.exe to PATH", puis relance ce fichier.
  pause & exit /b 1
)
if not exist .venv (
  echo [1/6] Creation de l'environnement Python...
  %PY% -m venv .venv || (pause & exit /b 1)
)
echo [2/6] Installation des dependances...
.venv\Scripts\python -m pip install --upgrade pip -q
.venv\Scripts\python -m pip install -r requirements.txt -q || (pause & exit /b 1)

echo [3/6] Modele de vision Ollama...
where ollama >nul 2>nul && (
  ollama pull qwen2.5vl:7b
) || (
  echo [!] Ollama n'est pas installe : telecharge-le sur ollama.com puis lance
  echo     dans un terminal : ollama pull qwen2.5vl:7b
)

echo [4/6] Plugins Obsidian (Dataview, Excalidraw) dans le coffre...
.venv\Scripts\python run.py plugins

echo [5/6] Icones : bureau, menu Demarrer et lancement au demarrage de Windows...
.venv\Scripts\python run.py raccourcis

echo [6/6] Extension du navigateur...
set /p EXT="  Installer l'extension dans (C)hrome, (E)dge, ou (R)ien ? [C/E/R] "
if /i "%EXT%"=="C" .venv\Scripts\python run.py extension chrome
if /i "%EXT%"=="E" .venv\Scripts\python run.py extension edge

echo.
echo  Termine. Double-clique sur l'icone "Inspi" de ton bureau pour lancer l'appli.
echo  Windows va demander d'autoriser Python sur le reseau : coche "Reseaux prives" uniquement.
echo.
pause
