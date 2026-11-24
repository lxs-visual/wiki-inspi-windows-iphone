"""Dossier de dépôt : tout fichier image ou .txt/.md glissé dedans est importé."""
from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from . import obsidian, store
from .config import CFG

log = logging.getLogger("inspi.watch")
DROP: Path = CFG["drop_folder"]


class DropWatcher(threading.Thread):
    def __init__(self, on_new=None):
        super().__init__(daemon=True, name="drop-watcher")
        self.on_new = on_new
        self.sizes: dict[Path, int] = {}
        DROP.mkdir(parents=True, exist_ok=True)
        (DROP / "importés").mkdir(exist_ok=True)

    def run(self):
        n = 0
        while True:
            try:
                self.scan()
            except Exception as e:  # noqa: BLE001
                log.warning("dossier de dépôt : %s", e)
            if n % 4 == 0:  # toutes les ~12 s : notes Web Clipper arrivées dans Inbox/
                try:
                    if obsidian.import_clipper_notes() and self.on_new:
                        self.on_new()
                except Exception as e:  # noqa: BLE001
                    log.warning("import Web Clipper : %s", e)
            n += 1
            time.sleep(3)

    def scan(self):
        for p in DROP.iterdir():
            if not p.is_file() or p.name.startswith("."):
                continue
            size = p.stat().st_size
            if self.sizes.get(p) != size:  # attend que la copie soit finie
                self.sizes[p] = size
                continue
            self.sizes.pop(p, None)
            ext = p.suffix.lower()
            try:
                if ext in store.ALLOWED:
                    store.add_image(p.read_bytes(), p.name, source="drop")
                elif ext in (".txt", ".md"):
                    store.add_text(p.read_text(encoding="utf-8", errors="ignore"), source="drop")
                else:
                    continue
                shutil.move(str(p), str(DROP / "importés" / p.name))
                log.info("importé %s", p.name)
                if self.on_new:
                    self.on_new()
            except Exception as e:  # noqa: BLE001
                log.warning("impossible d'importer %s : %s", p.name, e)
