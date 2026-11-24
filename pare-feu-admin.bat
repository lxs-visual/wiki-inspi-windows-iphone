@echo off
:: Clic droit > Executer en tant qu'administrateur, seulement si l'iPhone n'arrive pas a joindre le PC.
netsh advfirewall firewall delete rule name="Inspi Local" >nul 2>nul
netsh advfirewall firewall add rule name="Inspi Local" dir=in action=allow protocol=TCP localport=18765 profile=private
echo Regle ajoutee (reseaux prives uniquement, port 18765).
pause
