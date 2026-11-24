"""Point d'entrée d'Inspi Local : serveur + classement + dossier de dépôt + icône dans la barre des tâches.

    python run.py            → avec icône dans la barre des tâches (si pystray est installé)
    python run.py --console  → sans icône, logs dans la console
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from inspi import enrich, obsidian, store, watcher  # noqa: E402
from inspi.config import CFG  # noqa: E402

LOG = store.META / "inspi.log"
handlers = [logging.FileHandler(LOG, encoding="utf-8")]
if sys.stdout:
    handlers.append(logging.StreamHandler())
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", handlers=handlers)

PORT = int(CFG["port"])
LOCAL = f"http://127.0.0.1:{PORT}"


def open_path(p):
    if sys.platform == "win32":
        os.startfile(p)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", str(p)])


def start_server():
    import uvicorn
    from inspi.server import app

    config = uvicorn.Config(app, host=CFG["host"], port=PORT, log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    server.run()


def _ext(browser):
    from inspi import winsetup
    try:
        winsetup.install_extension(browser)
        webbrowser.open(LOCAL + "/aide-extension")
    except Exception as e:  # noqa: BLE001
        logging.warning("extension : %s", e)


def tray():
    import pystray
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 60, 60), 14, fill=(255, 92, 53))
    d.rectangle((18, 18, 30, 30), fill="white")
    d.rectangle((34, 18, 46, 46), fill="white")
    d.rectangle((18, 34, 30, 46), fill="white")

    def quit_(icon, _):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Ouvrir la galerie", lambda: webbrowser.open(LOCAL), default=True),
        pystray.MenuItem("Appairer l'iPhone", lambda: webbrowser.open(LOCAL + "/pair")),
        pystray.MenuItem("Ouvrir le coffre", lambda: open_path(store.VAULT)),
        pystray.MenuItem("Ouvrir le dossier de dépôt", lambda: open_path(CFG["drop_folder"])),
        pystray.MenuItem("Installer l'extension", pystray.Menu(
            pystray.MenuItem("dans Chrome", lambda: _ext("chrome")),
            pystray.MenuItem("dans Edge", lambda: _ext("edge")))),
        pystray.MenuItem("Voir le journal", lambda: open_path(LOG)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quitter", quit_),
    )
    pystray.Icon("inspi", img, "Inspi Local", menu).run()


def already_running() -> bool:
    try:
        import requests
        return requests.get(LOCAL + "/icon.svg", timeout=1.5).ok
    except Exception:
        return False


def main():
    if len(sys.argv) > 1 and sys.argv[1] not in ("--console", "--open"):
        from inspi import cli
        sys.exit(cli.main(sys.argv[1:]))
    if already_running():  # une seule instance : l'icône du bureau ouvre juste la galerie
        if "--open" in sys.argv:
            webbrowser.open(LOCAL)
        return
    try:
        obsidian.ensure_vault(PORT)
        obsidian.clipper_templates(Path(__file__).parent / "clipper", store.VAULT.name)
    except Exception as e:  # noqa: BLE001
        logging.warning("préparation du coffre : %s", e)
    def upgrade():
        try:
            n = store.upgrade_library()
            if n:
                logging.info("bibliothèque mise à niveau : %s inspi (couleurs, types)", n)
        except Exception as e:  # noqa: BLE001
            logging.warning("mise à niveau : %s", e)
    threading.Thread(target=upgrade, daemon=True, name="upgrade").start()
    enrich.WORKER.start()
    watcher.DropWatcher(on_new=enrich.WORKER.wake.set).start()
    logging.info("Inspi Local sur %s — coffre : %s — modèle : %s", LOCAL, store.VAULT, CFG["ollama_model"])
    if "--console" in sys.argv:
        start_server()
        return
    threading.Thread(target=start_server, daemon=True).start()
    if "--open" in sys.argv:
        threading.Timer(2.5, lambda: webbrowser.open(LOCAL)).start()
    try:
        tray()
    except Exception as e:  # noqa: BLE001
        logging.warning("Icône indisponible (%s), mode console.", e)
        threading.Event().wait()


if __name__ == "__main__":
    main()
