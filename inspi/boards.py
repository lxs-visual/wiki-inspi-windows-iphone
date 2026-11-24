"""Moodboards au format du plugin Obsidian Excalidraw (.excalidraw.md).

Nouveau board : titre, légendes (titre · auteur, site) et bande de palette.
Board existant (json ou compressed-json) : rien n'est déplacé, les images s'ajoutent en dessous,
avec une sauvegarde avant chaque écriture. Adapté de wiki-inspi (MIT, © 2026 Cynthia Ortu).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import uuid
from datetime import datetime
from urllib.parse import urlparse

from . import colors, store

CELL, GAP = 340, 40
DRAW_RE = re.compile(r"```(compressed-json|json)\n(.*?)\n```", re.S)
FILE_RE = re.compile(r"^([0-9a-f]{40}): \[\[([^\]]+)\]\]", re.M)
_KEY = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="


def lz_decompress_b64(s: str) -> str:
    """LZString.decompressFromBase64 (format « compressed-json » d'Excalidraw)."""
    s = re.sub(r"\s+", "", s)
    if not s:
        return ""
    vals = [_KEY.index(c) for c in s]
    pos, val, idx = 32, vals[0], 1

    def bits(n):
        nonlocal pos, val, idx
        res, p = 0, 1
        for _ in range(n):
            resb = val & pos
            pos >>= 1
            if pos == 0:
                pos = 32
                val = vals[idx] if idx < len(vals) else 0
                idx += 1
            if resb:
                res |= p
            p <<= 1
        return res

    d = {0: 0, 1: 1, 2: 2}
    enlarge, dsize, nbits = 4, 4, 3
    c = bits(2)
    if c == 2:
        return ""
    w = chr(bits(8 if c == 0 else 16))
    d[3] = w
    out = [w]
    while True:
        if idx > len(vals) + 1:
            return "".join(out)
        c = bits(nbits)
        if c in (0, 1):
            d[dsize] = chr(bits(8 if c == 0 else 16))
            dsize += 1
            c = dsize - 1
            enlarge -= 1
        elif c == 2:
            return "".join(out)
        if enlarge == 0:
            enlarge = 2 ** nbits
            nbits += 1
        if c in d:
            entry = d[c]
        elif c == dsize:
            entry = w + w[0]
        else:
            raise ValueError("dessin compressé illisible")
        out.append(entry)
        d[dsize] = w + entry[0]
        dsize += 1
        enlarge -= 1
        w = entry
        if enlarge == 0:
            enlarge = 2 ** nbits
            nbits += 1


def _el(kind: str, x, y, w, h, **extra) -> dict:
    eid = uuid.uuid4().hex[:20]
    seed = int(eid[:8], 16)
    base = {"id": eid, "type": kind, "x": round(x), "y": round(y), "width": round(w), "height": round(h),
            "angle": 0, "strokeColor": "#181A26", "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 1, "strokeStyle": "solid", "roughness": 0, "opacity": 100, "groupIds": [],
            "frameId": None, "roundness": None, "seed": seed, "version": 1, "versionNonce": seed ^ 0x5F5F5F,
            "isDeleted": False, "boundElements": None, "updated": 1, "link": None, "locked": False}
    base.update(extra)
    return base


def _text(x, y, w, text, size=13, color="#181A26") -> dict:
    text = " ".join(str(text).split())
    lines = max(1, math.ceil(len(text) * size * 0.55 / w))
    return _el("text", x, y, w, round(lines * size * 1.25), strokeColor=color, fontSize=size, fontFamily=2,
               text=text, textAlign="left", verticalAlign="top", baseline=round(size * 0.85), containerId=None,
               originalText=text, lineHeight=1.25, autoResize=False)


def _host(url) -> str:
    try:
        return urlparse(str(url)).netloc.removeprefix("www.")
    except Exception:
        return ""


def _layout(items: list[dict], x0: float, y0: float, cols: int) -> tuple[list[dict], dict, float]:
    """Images en colonnes (type Pinterest) + légendes. Retourne (éléments, fichiers, bas)."""
    heights = [y0] * cols
    els, files = [], {}
    for it in items:
        c = min(range(cols), key=lambda j: heights[j])
        w0, h0 = it.get("width") or 1, it.get("height") or 1
        h = round(CELL * h0 / w0)
        x, y = x0 + c * (CELL + GAP), heights[c]
        fid = it.get("sha1") or hashlib.sha1((store.VAULT / it["image"]).read_bytes()).hexdigest()
        files[fid] = it["image"]
        link = it["source_url"] if str(it.get("source_url") or "").startswith("http") else None
        els.append(_el("image", x, y, CELL, h, strokeColor="transparent", fileId=fid, status="saved",
                       scale=[1, 1], crop=None, link=link))
        cap = (it.get("title") or it.get("page_title") or "") + (f" · {it['author']}" if it.get("author") else "")
        used = 0
        if cap.strip():
            t = _text(x, y + h + 6, CELL, cap, 13)
            els.append(t)
            used += t["height"]
        host = _host(it.get("source_url"))
        if host:
            t = _text(x, y + h + 6 + used, CELL, host, 11, "#6b7280")
            els.append(t)
            used += t["height"]
        heights[c] = y + h + 6 + used + GAP
    return els, files, max(heights)


def merge_palette(items: list[dict], k: int = 8) -> list[str]:
    buckets: list[list] = []
    for it in items:
        for p in it["meta"].get("palette") or [{"hex": h, "share": 0.2} for h in it["palette"]]:
            for b in buckets:
                if colors.distance(b[0], p["hex"]) < 12:
                    b[1] += p.get("share") or 0.2
                    break
            else:
                buckets.append([p["hex"], p.get("share") or 0.2])
    buckets.sort(key=lambda b: -b[1])
    return [b[0] for b in buckets[:k]]


def _palette_strip(items, x0, y) -> list[dict]:
    els = [_text(x0, y, 600, "Palette", 20)]
    for j, hx in enumerate(merge_palette(items)):
        sx = x0 + j * 130
        els.append(_el("rectangle", sx, y + 40, 110, 80, backgroundColor=hx, strokeColor="transparent"))
        els.append(_text(sx, y + 126, 110, hx, 12))
    return els


HEAD = ("---\nexcalidraw-plugin: parsed\ntags: [excalidraw, moodboard, inspi]\n---\n"
        "==⚠  Switch to EXCALIDRAW VIEW in the MORE OPTIONS menu of this document. ⚠==\n\n"
        "# Excalidraw Data\n\n## Text Elements\n## Embedded Files\n")


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|#^\[\]]', "", name).strip()[:80] or "Moodboard"


def build(name: str, ids: list[str]) -> tuple[str, int]:
    """Crée ou complète Moodboards/<name>.excalidraw.md. Retourne (chemin relatif, nb ajoutées)."""
    safe = safe_name(name)
    path = store.BOARDS / f"{safe}.excalidraw.md"
    items = [it for it in (store.get(i) for i in ids) if it and it.get("image") and not it.get("trashed")]
    if path.exists():
        return f"Moodboards/{path.name}", _append(path, items)
    if not items:
        raise ValueError("Aucune image à placer.")
    cols = max(2, min(5, round(math.sqrt(len(items) * 1.3))))
    els = [_text(120, 100, 900, safe, 34),
           _text(120, 160, 900, f"{len(items)} images · Inspi · {datetime.now():%d/%m/%Y}", 14, "#6b7280")]
    img_els, files, bottom = _layout(items, 120, 230, cols)
    els += img_els + _palette_strip(items, 120, bottom + 20)
    scene = {"type": "excalidraw", "version": 2, "source": "https://github.com/zsviczian/obsidian-excalidraw-plugin",
             "elements": els, "appState": {"gridSize": None, "viewBackgroundColor": "#FFFFFF"}, "files": {}}
    emb = "".join(f"{fid}: [[{rel}]]\n\n" for fid, rel in files.items())
    path.write_text(HEAD + emb + "%%\n## Drawing\n```json\n" + json.dumps(scene, indent=1, ensure_ascii=False)
                    + "\n```\n%%\n", encoding="utf-8")
    return f"Moodboards/{path.name}", len(items)


def _append(path, items: list[dict]) -> int:
    text = path.read_text(encoding="utf-8")
    m = DRAW_RE.search(text)
    if not m:
        raise ValueError("Ce moodboard n'a pas de dessin lisible.")
    data = json.loads(lz_decompress_b64(m.group(2)) if m.group(1) == "compressed-json" else m.group(2))
    have = {fid for fid, _ in FILE_RE.findall(text)} | {e.get("fileId") for e in data.get("elements", []) if e.get("fileId")}
    have_paths = {rel for _, rel in FILE_RE.findall(text)}
    fresh = [it for it in items if (it.get("sha1") not in have) and it["image"] not in have_paths]
    if not fresh:
        return 0
    live = [e for e in data.get("elements", []) if not e.get("isDeleted")]
    bottom = max((e.get("y", 0) + e.get("height", 0) for e in live), default=0)
    left = min((e.get("x", 0) for e in live), default=120)
    cols = max(2, min(5, round(math.sqrt(len(fresh) * 1.3))))
    els, files, _ = _layout(fresh, left, bottom + 120, cols)
    data["elements"] = data.get("elements", []) + els
    emb = "".join(f"{fid}: [[{rel}]]\n\n" for fid, rel in files.items())
    head = text[: m.start()]
    marker = "## Embedded Files\n"
    if marker in head:
        i = head.index(marker) + len(marker)
        head = head[:i] + emb + head[i:]
    else:
        head = head.replace("%%\n## Drawing", marker + emb + "%%\n## Drawing", 1)
    backup = store.META / "board-backups"
    backup.mkdir(exist_ok=True)
    shutil.copy2(path, backup / f"{path.name[:-14]}.{datetime.now():%Y%m%d-%H%M%S}.excalidraw.md")
    body = "```json\n" + json.dumps(data, indent=1, ensure_ascii=False) + "\n```"
    path.write_text(head + body + text[m.end():], encoding="utf-8")
    return len(fresh)


def list_boards() -> list[dict]:
    """Moodboards du coffre, du plus récent au plus ancien, avec les inspi qu'ils contiennent."""
    by_path = {it["image"]: it["id"] for it in store.all_items() if it.get("image")}
    out = []
    for p in sorted(store.BOARDS.glob("*.excalidraw.md"), key=lambda p: -p.stat().st_mtime):
        rels = [rel for _, rel in FILE_RE.findall(p.read_text(encoding="utf-8", errors="ignore"))]
        ids = [by_path[r] for r in rels if r in by_path]
        out.append({"name": p.name[: -len(".excalidraw.md")], "images": len(rels), "ids": ids,
                    "updated": datetime.fromtimestamp(p.stat().st_mtime).strftime("%d/%m/%Y %H:%M"),
                    "path": f"Moodboards/{p.name}"})
    return out
