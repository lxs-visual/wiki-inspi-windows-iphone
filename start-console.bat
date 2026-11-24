@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Inspi Local en mode console (ferme cette fenetre pour arreter).
".venv\Scripts\python.exe" run.py --console
pause
