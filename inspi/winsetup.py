"""Intégration Windows : raccourcis (bureau, menu Démarrer, démarrage) et installation de l'extension."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import ROOT

APPDIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "InspiLocal"
EXT_DEST = APPDIR / "extension"


def _ps(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "-"],
                          input=script, capture_output=True, text=True, encoding="utf-8", errors="ignore")


def _q(s) -> str:  # chaîne PowerShell entre apostrophes
    return "'" + str(s).replace("'", "''") + "'"


def shortcuts(desktop: bool = True, startmenu: bool = True, startup: bool = True) -> list[str]:
    pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not pyw.exists():
        pyw = Path(sys.executable).with_name("pythonw.exe")
    icon = ROOT / "web" / "icon.ico"
    run = ROOT / "run.py"
    targets = []
    if desktop:
        targets.append(("Desktop", "Inspi.lnk", f'"{run}" --open'))
    if startmenu:
        targets.append(("Programs", "Inspi.lnk", f'"{run}" --open'))
    if startup:
        targets.append(("Startup", "Inspi Local.lnk", f'"{run}"'))
    lines = ["$w = New-Object -ComObject WScript.Shell"]
    for folder, name, args in targets:
        lines += [
            f"$p = Join-Path ([Environment]::GetFolderPath({_q(folder)})) {_q(name)}",
            "$s = $w.CreateShortcut($p)",
            f"$s.TargetPath = {_q(pyw)}", f"$s.Arguments = {_q(args)}", f"$s.WorkingDirectory = {_q(ROOT)}",
            f"$s.IconLocation = {_q(icon)}", "$s.Description = 'Inspi Local'",
            "$s.Save()", "Write-Output $p",
        ]
    r = _ps("\n".join(lines))
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "PowerShell a échoué")
    return [line for line in r.stdout.splitlines() if line.strip()]


def remove_startup() -> None:
    _ps("Remove-Item (Join-Path ([Environment]::GetFolderPath('Startup')) 'Inspi Local.lnk') -ErrorAction SilentlyContinue")


BROWSERS = {
    "chrome": ("Google Chrome", "chrome://extensions", [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"]),
    "edge": ("Microsoft Edge", "edge://extensions", [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"]),
}


def find_browsers() -> dict[str, str]:
    out = {}
    for key, (_, _, paths) in BROWSERS.items():
        for p in paths:
            exe = os.path.expandvars(p)
            if os.path.exists(exe):
                out[key] = exe
                break
    return out


def install_extension(browser: str = "") -> dict:
    """Copie l'extension dans un dossier stable, ouvre la page Extensions du navigateur et l'Explorateur."""
    found = find_browsers()
    if not found:
        raise RuntimeError("Ni Chrome ni Edge trouvé sur ce PC.")
    browser = browser if browser in found else ("chrome" if "chrome" in found else "edge")
    if EXT_DEST.exists():
        shutil.rmtree(EXT_DEST)
    shutil.copytree(ROOT / "extension", EXT_DEST)
    label, page, _ = BROWSERS[browser]
    subprocess.Popen([found[browser], page])
    subprocess.Popen(["explorer", str(EXT_DEST)])
    try:
        subprocess.run("clip", input=str(EXT_DEST), text=True, shell=True)
    except Exception:  # noqa: BLE001
        pass
    return {"browser": label, "folder": str(EXT_DEST), "page": page, "available": list(found)}


def main(args: list[str]) -> int:
    if not args or args[0] == "raccourcis":
        for p in shortcuts(startup="--sans-demarrage" not in args):
            print("  raccourci : " + p)
        return 0
    if args[0] == "extension":
        r = install_extension(args[1] if len(args) > 1 else "")
        print(f"""
  {r['browser']} s'ouvre sur {r['page']}.
  1. Active « Mode développeur » (en haut à droite sur Chrome, à gauche sur Edge).
  2. Clique « Charger l'extension non empaquetée » et choisis :
       {r['folder']}
     (le chemin est déjà copié : colle‑le avec Ctrl+V dans la fenêtre de choix du dossier)
  3. C'est tout : l'extension se connecte toute seule à Inspi (l'appli doit tourner).
  Autre navigateur : inspi extension chrome | inspi extension edge""")
        return 0
    print("usage : inspi windows [raccourcis|extension [chrome|edge]]")
    return 1
