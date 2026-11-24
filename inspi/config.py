"""Configuration : lue depuis config.yml à la racine du projet (créé au premier lancement)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yml"
HOME = Path(os.path.expanduser("~"))

DEFAULTS = {
    # Dossier du coffre Obsidian (créé s'il n'existe pas)
    "vault": str(HOME / "Documents" / "Inspi"),
    # Dossier où glisser des fichiers à importer
    "drop_folder": str(HOME / "Inspi Drop"),
    # 0.0.0.0 = accessible depuis le Wi-Fi local (iPhone). 127.0.0.1 = PC uniquement.
    "host": "0.0.0.0",
    "port": 18765,
    "ollama_url": "http://127.0.0.1:11434",
    # Modèle de vision. Plus léger si TouchDesigner tourne en même temps : "gemma3:4b"
    "ollama_model": "qwen2.5vl:7b",
    # Combien de temps Ollama garde le modèle en VRAM après un classement
    "ollama_keep_alive": "2m",
    "max_image_side": 1280,
    # IA de classement : "ollama" (local, rien ne quitte le PC) ou "claude" (API Anthropic, clé requise)
    "ai_backend": "ollama",
    "claude_model": "claude-sonnet-5-5",
    # Si Claude échoue (pas de crédit, pas d'Internet), utiliser l'IA locale
    "ai_fallback": True,
    "language": "fr",
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(yaml.safe_load(f) or {})
    else:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("# Configuration d'Inspi Local. Redémarre l'appli après modification.\n")
            yaml.safe_dump(DEFAULTS, f, allow_unicode=True, sort_keys=False)
    if os.environ.get("INSPI_VAULT"):
        cfg["vault"] = os.environ["INSPI_VAULT"]
    if os.environ.get("INSPI_DROP"):
        cfg["drop_folder"] = os.environ["INSPI_DROP"]
    if os.environ.get("INSPI_OLLAMA_URL"):
        cfg["ollama_url"] = os.environ["INSPI_OLLAMA_URL"]
    cfg["vault"] = Path(cfg["vault"]).expanduser()
    cfg["drop_folder"] = Path(cfg["drop_folder"]).expanduser()
    return cfg


CFG = load()


def save(**updates) -> None:
    """Modifie config.yml (et la configuration en mémoire) sans toucher aux autres clés."""
    data = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    data.update(updates)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("# Configuration d'Inspi Local.\n")
        yaml.safe_dump({k: str(v) if isinstance(v, Path) else v for k, v in data.items()}, f,
                       allow_unicode=True, sort_keys=False)
    CFG.update(updates)
