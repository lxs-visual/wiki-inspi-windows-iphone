"""Mise en place du coffre Obsidian : note d'accueil, vues Bases, plugins, import Web Clipper."""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

import requests
import yaml

from . import store

log = logging.getLogger("inspi.obsidian")

HUB = """---
type: hub
tags: [hub]
---

# Inspi

Ta bibliothèque d'inspirations, classée par un modèle de vision local. La galerie complète (recherche par couleur, familles, doublons, moodboards) : [ouvrir la galerie](http://127.0.0.1:{port}/).

## Dernières captures

![[_Inspi.base#Récentes]]

## Explorer

Toutes les vues (par couleur, style, ambiance, technique, usage, auteur) : [[_Inspi.base]]

## Moodboards

```dataview
LIST FROM "Moodboards"
SORT file.mtime DESC
```

## Thèmes

```dataview
LIST FROM "Thèmes"
SORT file.name ASC
```

## En attente de classement

```dataview
TABLE captured AS "Capturé", source AS "Source"
FROM "Inbox"
SORT captured DESC
LIMIT 20
```
"""


def _card_view(name: str, group: str | None = None, limit: int | None = None) -> dict:
    v = {"type": "cards", "name": name,
         "order": ["file.name", "style", "mood", "color_name", "author", "technique"],
         "image": "formula.cover", "imageFit": "contain", "imageAspectRatio": 0.8, "cardSize": 240,
         "sort": [{"property": "captured", "direction": "DESC"}]}
    if group:
        v["groupBy"] = {"property": group, "direction": "ASC"}
    if limit:
        v["limit"] = limit
    return v


BASE = {
    "filters": {"and": ['file.inFolder("Library")']},
    "formulas": {"cover": "link(thumbnail)"},
    "properties": {
        "color_name": {"displayName": "Couleur"}, "main_color": {"displayName": "Hex"},
        "color_temperature": {"displayName": "Température"}, "style": {"displayName": "Style"},
        "mood": {"displayName": "Ambiance"}, "author": {"displayName": "Auteur"},
        "technique": {"displayName": "Technique"}, "use_for": {"displayName": "Usage"},
        "medium": {"displayName": "Medium"}, "captured": {"displayName": "Capturé"},
    },
    "views": [
        _card_view("Récentes", limit=60), _card_view("Par couleur", "color_name"), _card_view("Par style", "style"),
        _card_view("Par ambiance", "mood"), _card_view("Par technique", "technique"),
        _card_view("Par usage", "use_for"), _card_view("Par medium", "medium"), _card_view("Par auteur", "author"),
        {"type": "table", "name": "Tableau", "order": ["file.name", "color_name", "main_color", "color_temperature",
                                                        "style", "mood", "technique", "author", "use_for", "captured"],
         "sort": [{"property": "captured", "direction": "DESC"}]},
    ],
}

PLUGINS = {  # id → dépôt GitHub (releases officielles)
    "dataview": "blacksmithgu/obsidian-dataview",
    "obsidian-excalidraw-plugin": "zsviczian/obsidian-excalidraw-plugin",
}


def ensure_vault(port: int) -> None:
    v = store.VAULT
    hub = v / "_Inspi.md"
    if not hub.exists():
        hub.write_text(HUB.replace("{port}", str(port)), encoding="utf-8")
    base = v / "_Inspi.base"
    if not base.exists():
        base.write_text(yaml.safe_dump(BASE, allow_unicode=True, sort_keys=False), encoding="utf-8")
    obs = v / ".obsidian"
    obs.mkdir(exist_ok=True)
    app = obs / "app.json"
    if not app.exists():  # images collées dans Obsidian → assets/
        app.write_text(json.dumps({"attachmentFolderPath": "assets", "newFileLocation": "folder",
                                   "newFileFolderPath": "Inbox"}, indent=2), encoding="utf-8")


def install_plugins(force: bool = False) -> list[str]:
    """Télécharge Dataview et Excalidraw depuis leurs releases GitHub officielles et les active."""
    obs = store.VAULT / ".obsidian"
    done = []
    for pid, repo in PLUGINS.items():
        dest = obs / "plugins" / pid
        if (dest / "main.js").exists() and not force:
            done.append(f"{pid} (déjà installé)")
            continue
        dest.mkdir(parents=True, exist_ok=True)
        base = f"https://github.com/{repo}/releases/latest/download/"
        for name in ("manifest.json", "main.js", "styles.css"):
            r = requests.get(base + name, timeout=180)
            if r.status_code == 404 and name == "styles.css":
                continue
            r.raise_for_status()
            (dest / name).write_bytes(r.content)
        version = json.loads((dest / "manifest.json").read_text(encoding="utf-8")).get("version", "")
        done.append(f"{pid} {version}")
    cp = obs / "community-plugins.json"
    enabled = json.loads(cp.read_text()) if cp.exists() else []
    for pid in PLUGINS:
        if pid not in enabled:
            enabled.append(pid)
    cp.write_text(json.dumps(enabled, indent=2), encoding="utf-8")
    return done


# ---------------- import des notes Obsidian Web Clipper ----------------
FM_RE = re.compile(r"^---\n(.*?)\n---", re.S)
MD_IMG = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)")


def import_clipper_notes() -> int:
    """Notes déposées dans Inbox/ par Obsidian Web Clipper (ou à la main) → inspi à récupérer."""
    known = store.known_note_paths()
    n = 0
    done_dir = store.META / "clipper-importées"
    for p in store.INBOX.glob("*.md"):
        rel = f"Inbox/{p.name}"
        if rel in known:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        m = FM_RE.match(text)
        fm = {}
        if m:
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except Exception:
                fm = {}
        if fm.get("id") and store.get(str(fm["id"])):
            continue  # une de nos notes
        img = str(fm.get("image_url") or "")
        if not img.startswith("http"):
            mi = MD_IMG.search(text)
            img = mi.group(1) if mi else ""
        url = str(fm.get("source_url") or fm.get("source") or "")
        if not (img or url.startswith("http")):
            continue  # simple note perso : on n'y touche pas
        store.add_url(url, source=str(fm.get("ingest_source") or "webclipper"), image_url=img,
                      page_title=str(fm.get("page_title") or fm.get("title") or "")[:300],
                      author=str(fm.get("author") or "")[:200], site_name=str(fm.get("site") or "")[:200])
        done_dir.mkdir(exist_ok=True)
        shutil.move(str(p), str(done_dir / p.name))
        n += 1
    return n


def clipper_templates(out: Path, vault_name: str) -> None:
    """Modèles Obsidian Web Clipper (générique + Pinterest) qui écrivent dans Inbox/."""
    out.mkdir(parents=True, exist_ok=True)

    def tpl(name, image, author, triggers):
        props = [("type", "image", "text"), ("source_url", "{{url}}", "text"), ("image_url", image, "text"),
                 ("page_title", "{{title}}", "text"), ("author", author, "text"), ("site", "{{site}}", "text"),
                 ("ingest_source", "webclipper", "text")]
        return {"schemaVersion": "0.1.0", "name": name, "behavior": "create",
                "noteContentFormat": "![](" + image + ")\n\n{{description}}\n\n[Source]({{url}})",
                "properties": [{"name": a, "value": b, "type": c} for a, b, c in props],
                "triggers": triggers, "noteNameFormat": "{{title|safe_name}}", "path": "Inbox", "vault": vault_name}

    files = {
        "inspi-generique.json": tpl("Inspi", "{{image}}", "{{author}}", []),
        "inspi-pinterest.json": tpl("Inspi Pinterest", "{{selector:img[elementtiming^=closeup-image-main]?src}}",
                                    "{{selector:[data-test-id=creator-profile-name]}}",
                                    ["/^https?:\\/\\/([a-z]+\\.)?pinterest\\.[a-z.]+\\/pin\\//"]),
        "inspi-behance.json": tpl("Inspi Behance", "{{image}}", "{{author}}",
                                  ["/^https?:\\/\\/(www\\.)?behance\\.net\\/gallery\\//"]),
    }
    for name, data in files.items():
        (out / name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
