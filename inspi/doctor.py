"""inspi doctor : vérifie chaque maillon et donne la correction de ce qui manque."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

import requests

from . import auth, store
from .config import CFG, CONFIG_PATH


def _check(name: str, ok: bool, detail: str, fix: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail, "fix": "" if ok else fix}


def run(server_running: bool | None = None) -> list[dict]:
    out = []
    out.append(_check("Python", sys.version_info >= (3, 10), sys.version.split()[0],
                      "Installe Python 3.11+ depuis python.org"))
    missing = []
    for mod in ("fastapi", "uvicorn", "PIL", "pillow_heif", "yaml", "requests", "qrcode", "keyring", "networkx"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    out.append(_check("Dépendances", not missing, "toutes présentes" if not missing else "manquent : " + ", ".join(missing),
                      "Relance install.bat"))
    out.append(_check("Configuration", CONFIG_PATH.exists(), str(CONFIG_PATH), "Lance Inspi une fois pour la créer"))
    v = store.VAULT
    try:
        (store.META / ".w").write_text("ok")
        (store.META / ".w").unlink()
        out.append(_check("Coffre Obsidian", True, str(v)))
    except Exception as e:  # noqa: BLE001
        out.append(_check("Coffre Obsidian", False, f"{v} : {e}", "Vérifie le chemin « vault » dans config.yml"))
    base = CFG["ollama_url"].rstrip("/")
    try:
        names = [m["name"] for m in requests.get(f"{base}/api/tags", timeout=3).json().get("models", [])]
        out.append(_check("Ollama", True, base))
        want = CFG["ollama_model"]
        has = any(n in (want, f"{want}:latest") for n in names)
        out.append(_check("Modèle de vision", has, want if has else f"{want} absent (installés : {', '.join(names) or 'aucun'})",
                          f"ollama pull {want}"))
    except Exception:
        out.append(_check("Ollama", False, f"{base} ne répond pas", "Lance l'application Ollama (ollama.com)"))
    if CFG.get("ai_backend") == "claude":
        from . import enrich
        has = bool(enrich.claude_key())
        out.append(_check("IA Claude", has, f"{CFG['claude_model']} · clé {'enregistrée' if has else 'absente'}",
                          "Galerie → ⋯ → Réglages IA → colle ta clé API"))
    if server_running is None:
        try:
            requests.get(f"http://127.0.0.1:{CFG['port']}/icon.svg", timeout=2)
            server_running = True
        except Exception:
            server_running = False
    out.append(_check("Serveur Inspi", server_running, f"http://127.0.0.1:{CFG['port']}", "Lance start.bat"))
    out.append(_check("Accès iPhone", CFG["host"] == "0.0.0.0",
                      f"http://{auth.lan_ip()}:{CFG['port']} (même Wi‑Fi)" if CFG["host"] == "0.0.0.0" else "désactivé (host = 127.0.0.1)",
                      "Mets host: 0.0.0.0 dans config.yml"))
    if sys.platform == "win32":
        try:
            r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", "name=all", "dir=in"],
                               capture_output=True, text=True, timeout=15, encoding="utf-8", errors="ignore")
            ok = "Inspi Local" in r.stdout or "python" in r.stdout.lower()
            out.append(_check("Pare-feu Windows", ok, "règle trouvée" if ok else "aucune règle pour Python / Inspi",
                              "Lance pare-feu-admin.bat en tant qu'administrateur"))
        except Exception:
            pass
    plug = v / ".obsidian" / "plugins"
    for pid, label in (("dataview", "Plugin Dataview"), ("obsidian-excalidraw-plugin", "Plugin Excalidraw")):
        ok = (plug / pid / "main.js").exists()
        out.append(_check(label, ok, "installé" if ok else "absent", "Lance : inspi plugins (ou relance install.bat)"))
    cp = v / ".obsidian" / "community-plugins.json"
    enabled = json.loads(cp.read_text()) if cp.exists() else []
    out.append(_check("Plugins activés", "obsidian-excalidraw-plugin" in enabled, ", ".join(enabled) or "aucun",
                      "Dans Obsidian : Réglages → Modules complémentaires → activer les modules de la communauté"))
    obs = shutil.which("obsidian") or any((p / "Obsidian.exe").exists() for p in _obsidian_dirs())
    out.append(_check("Obsidian", bool(obs), "trouvé" if obs else "introuvable", "Installe Obsidian (obsidian.md)"))
    free = shutil.disk_usage(v).free / 1e9
    out.append(_check("Espace disque", free > 2, f"{free:.0f} Go libres", "Libère de la place sur le disque du coffre"))
    return out


def _obsidian_dirs():
    import os
    from pathlib import Path
    for env in ("LOCALAPPDATA", "PROGRAMFILES"):
        if os.environ.get(env):
            yield Path(os.environ[env]) / "Programs" / "Obsidian"
            yield Path(os.environ[env]) / "Obsidian"


def main() -> int:
    bad = 0
    for c in run():
        mark = "OK " if c["ok"] else "XX "
        print(f"  {mark} {c['name']:<20} {c['detail']}")
        if not c["ok"]:
            bad += 1
            print(f"       → {c['fix']}")
    print("\nTout est en ordre." if not bad else f"\n{bad} point(s) à corriger.")
    return 1 if bad else 0
