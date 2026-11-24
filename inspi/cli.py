"""Commande « inspi » (via inspi.bat).

    inspi search "glitch neon" [--color "#1e3a8a"|bleu] [--style x] [--mood x] [--technique x] [--usage x] [--limit 20] [--json]
    inspi board "requête" --name "Mon board" [--color …] [--limit 30]
    inspi open              ouvre la note d'accueil dans Obsidian
    inspi map               ouvre la galerie dans le navigateur
    inspi doctor            vérifie l'installation
    inspi plugins           installe / met à jour Dataview et Excalidraw dans le coffre
    inspi reclasser [--tout]  reclasse les anciennes notes (ou toutes)
    inspi clipper           écrit les modèles Obsidian Web Clipper dans le dossier clipper/
    inspi raccourcis        crée les icônes (bureau, menu Démarrer, démarrage de Windows)
    inspi extension [chrome|edge]   prépare l'installation de l'extension
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import webbrowser

from . import store
from .config import CFG, ROOT


def _filter(items, a):
    out = []
    for it in items:
        m = it["meta"]
        if a.style and a.style not in (m.get("style") or []) + [it.get("style")]:
            continue
        if a.mood and a.mood not in (m.get("mood") or []) + [it.get("mood")]:
            continue
        if a.technique and a.technique not in (m.get("technique") or []):
            continue
        if getattr(a, "usage", None) and a.usage not in (m.get("use_for") or []):
            continue
        out.append(it)
    return out


def cmd_search(a):
    items = _filter(store.search(a.query or "", color=a.color or "", status="done"), a)[: a.limit]
    if a.json:
        print(json.dumps([{k: it.get(k) for k in ("id", "title", "note", "image", "source_url", "tags", "main_color",
                                                   "color_distance")} for it in items], ensure_ascii=False, indent=1))
        return 0
    for it in items:
        d = f"  Δ{it['color_distance']}" if "color_distance" in it else ""
        print(f"{it['id']}  {(it.get('title') or '')[:48]:<48} {it.get('main_color') or '':8} {', '.join(it['tags'][:4])}{d}")
    print(f"\n{len(items)} résultat(s)")
    return 0


def cmd_board(a):
    from . import boards
    items = _filter(store.search(a.query or "", color=a.color or "", status="done", type_="image"), a)[: a.limit]
    if not items:
        print("Aucune image ne correspond.")
        return 1
    rel, added = boards.build(a.name, [it["id"] for it in items])
    print(f"{added} image(s) → {store.VAULT / rel}")
    webbrowser.open(f"obsidian://open?vault={urllib.parse.quote(store.VAULT.name)}&file={urllib.parse.quote(rel[:-3])}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="inspi", description="Inspi Local")
    sub = p.add_subparsers(dest="cmd")
    for name in ("search", "board"):
        s = sub.add_parser(name)
        s.add_argument("query", nargs="?", default="")
        s.add_argument("--color")
        s.add_argument("--style")
        s.add_argument("--mood")
        s.add_argument("--technique")
        s.add_argument("--usage")
        s.add_argument("--limit", type=int, default=20 if name == "search" else 30)
        if name == "search":
            s.add_argument("--json", action="store_true")
        else:
            s.add_argument("--name", required=True)
    for name in ("open", "map", "doctor", "plugins", "clipper"):
        sub.add_parser(name)
    rc = sub.add_parser("raccourcis")
    rc.add_argument("--sans-demarrage", action="store_true")
    ex = sub.add_parser("extension")
    ex.add_argument("browser", nargs="?", default="")
    r = sub.add_parser("reclasser")
    r.add_argument("--tout", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "search":
        return cmd_search(a)
    if a.cmd == "board":
        return cmd_board(a)
    if a.cmd == "open":
        webbrowser.open(f"obsidian://open?vault={urllib.parse.quote(store.VAULT.name)}&file=_Inspi")
        return 0
    if a.cmd == "map":
        webbrowser.open(f"http://127.0.0.1:{CFG['port']}/")
        return 0
    if a.cmd == "doctor":
        from . import doctor
        return doctor.main()
    if a.cmd == "plugins":
        from . import obsidian
        for line in obsidian.install_plugins(force=True):
            print("  " + line)
        print("Ouvre (ou recharge) le coffre dans Obsidian et active les modules de la communauté.")
        return 0
    if a.cmd == "clipper":
        from . import obsidian
        obsidian.clipper_templates(ROOT / "clipper", store.VAULT.name)
        print(f"Modèles écrits dans {ROOT / 'clipper'}")
        return 0
    if a.cmd == "raccourcis":
        from . import winsetup
        return winsetup.main(["raccourcis"] + (["--sans-demarrage"] if a.sans_demarrage else []))
    if a.cmd == "extension":
        from . import winsetup
        return winsetup.main(["extension", a.browser] if a.browser else ["extension"])
    if a.cmd == "reclasser":
        n = store.requeue_all(only_old=not a.tout)
        print(f"{n} note(s) remise(s) en file. Inspi les reclasse en arrière-plan (l'appli doit tourner).")
        return 0
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
