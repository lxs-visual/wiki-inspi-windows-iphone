"""Stockage : notes Markdown dans le coffre Obsidian + index SQLite pour la recherche."""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from PIL import Image, ImageOps

try:  # photos iPhone (HEIC)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass

from . import colors, media
from .config import CFG

SCHEMA_VERSION = 3
COLORS_VERSION = 1
KINDS = ("image", "video", "screenshot", "text", "link")
VAULT: Path = CFG["vault"]
INBOX = VAULT / "Inbox"
LIBRARY = VAULT / "Library"
ASSETS = VAULT / "assets"
BOARDS = VAULT / "Moodboards"
THEMES = VAULT / "Thèmes"
META = VAULT / ".inspi"  # dossier caché : Obsidian l'ignore
THUMBS = META / "thumbs"
TRASH = META / "corbeille"
DB_PATH = META / "index.db"

for d in (INBOX, LIBRARY, ASSETS, BOARDS, THEMES, THUMBS, TRASH):
    d.mkdir(parents=True, exist_ok=True)

_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript(
    """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,              -- image | text
  status TEXT NOT NULL,            -- fetch | pending | done | error
  created TEXT NOT NULL,
  source TEXT,                     -- iphone | extension | drop | web | webclipper
  source_url TEXT, page_title TEXT, author TEXT,
  user_note TEXT, text TEXT,
  image TEXT,                      -- chemin relatif au coffre
  width INTEGER, height INTEGER,
  title TEXT, description TEXT,
  tags TEXT DEFAULT '[]', palette TEXT DEFAULT '[]',
  style TEXT, medium TEXT, mood TEXT,
  dhash TEXT, duplicate_of TEXT,
  note TEXT,                       -- chemin relatif de la note .md
  error TEXT, attempts INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_created ON items(created);
"""
)
# migration v1 → v2 : nouvelles colonnes
_have = {r["name"] for r in _conn.execute("PRAGMA table_info(items)")}
for col, decl in (("sha1", "TEXT"), ("site_name", "TEXT"), ("alt", "TEXT"), ("image_url", "TEXT"),
                  ("meta", "TEXT DEFAULT '{}'"), ("main_color", "TEXT"), ("schema_version", "INTEGER DEFAULT 0"),
                  ("trashed", "TEXT"), ("kind", "TEXT"), ("video", "TEXT"), ("duration", "REAL"),
                  ("author_url", "TEXT"), ("colors_v", "INTEGER DEFAULT 0")):
    if col not in _have:
        _conn.execute(f"ALTER TABLE items ADD COLUMN {col} {decl}")
_conn.commit()

ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif", ".avif", ".tif", ".tiff"}
_version = 0  # incrémenté à chaque changement : sert au cache des similarités


def bump():
    global _version
    _version += 1


def version() -> int:
    return _version


# ---------- utilitaires ----------
def new_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def slug(s: str, n: int = 50) -> str:
    s = re.sub(r"[^\w\s-]", "", s or "", flags=re.U).strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    return s[:n].strip("-") or "inspi"


def dhash(img: Image.Image, size: int = 8) -> str:
    g = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            l, r = px[row * (size + 1) + col], px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if l > r else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k, empty in (("tags", "[]"), ("palette", "[]"), ("meta", "{}")):
        try:
            d[k] = json.loads(d.get(k) or empty)
        except Exception:
            d[k] = json.loads(empty)
    return d


def get(item_id: str) -> dict | None:
    with _lock:
        r = _conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    return row_to_dict(r) if r else None


def update(item_id: str, **fields) -> None:
    for k in ("tags", "palette", "meta"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        _conn.execute(f"UPDATE items SET {cols} WHERE id=?", (*fields.values(), item_id))
        _conn.commit()
    bump()


def _insert(item: dict) -> None:
    cols = ", ".join(item)
    with _lock:
        _conn.execute(f"INSERT INTO items ({cols}) VALUES ({', '.join('?' * len(item))})", tuple(item.values()))
        _conn.commit()
    bump()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- images ----------
def _by_sha(sha: str, exclude: str = "") -> dict | None:
    with _lock:
        r = _conn.execute("SELECT * FROM items WHERE sha1=? AND id!=? AND trashed IS NULL", (sha, exclude)).fetchone()
    return row_to_dict(r) if r else None


def _near_duplicate(h: str, exclude: str = "") -> str | None:
    with _lock:
        rows = _conn.execute("SELECT id, dhash FROM items WHERE dhash IS NOT NULL AND id!=? AND trashed IS NULL",
                             (exclude,)).fetchall()
    for r in rows:
        if hamming(h, r["dhash"]) <= 3:
            return r["id"]
    return None


def add_seen(item_id: str, url: str, source: str) -> dict:
    """Image déjà enregistrée : on ajoute juste la nouvelle source à la note existante."""
    it = get(item_id)
    meta = it["meta"]
    seen = meta.setdefault("also_seen", [])
    if url and url != it.get("source_url") and url not in [s["url"] for s in seen]:
        seen.append({"url": url, "date": _now()[:10], "source": source})
        update(item_id, meta=meta)
        write_note(item_id)
    return get(item_id)


# Tailles d'écran courantes (iPhone, Android, PC) : une image PNG à ces dimensions est une capture d'écran
SCREEN_SIZES = {(1170, 2532), (1179, 2556), (1290, 2796), (1284, 2778), (1125, 2436), (828, 1792), (750, 1334),
                (1242, 2688), (1206, 2622), (1320, 2868), (1080, 2340), (1080, 2400), (1440, 3200), (1080, 1920),
                (1920, 1080), (2560, 1440), (3840, 2160), (1366, 768), (1536, 864), (1440, 900), (2880, 1800),
                (2560, 1600), (3024, 1964), (3456, 2234), (1600, 900), (2560, 1080), (3440, 1440), (1280, 720)}
SCREEN_NAME = re.compile(r"(screenshot|screen shot|capture[ _-]?d[' ]?[ée]cran|^capture|scr_|snip)", re.I)


def looks_like_screenshot(img: Image.Image, fmt: str, filename: str = "", source: str = "") -> bool:
    if source == "capture" or SCREEN_NAME.search(filename or ""):
        return True
    try:
        exif = img.getexif()
        camera = bool(exif.get(271) or exif.get(272))  # marque / modèle d'appareil photo
    except Exception:
        camera = False
    return fmt == "PNG" and not camera and (img.width, img.height) in SCREEN_SIZES


def color_fields(img: Image.Image) -> dict:
    """Champs couleur calculés à partir des pixels (plus fiables que l'IA)."""
    c = colors.extract_palette(img)
    return {"palette": [p["hex"] for p in c["palette"]], "main_color": c["main_color"], "colors_v": COLORS_VERSION,
            "_meta": {"palette": c["palette"], "main_color": c["main_color"], "accent_color": c["accent_color"],
                      "color_temperature": c["color_temperature"]}}


def _save_image(iid: str, data: bytes, filename: str = "", source: str = "") -> dict:
    img = Image.open(io.BytesIO(data))
    fmt = (img.format or "").upper()  # à lire avant exif_transpose, qui l'efface
    img = ImageOps.exif_transpose(img) or img
    ext = Path(filename).suffix.lower()
    month = datetime.now().strftime("%Y-%m")
    (ASSETS / month).mkdir(parents=True, exist_ok=True)
    # On garde l'original sauf formats peu lisibles par Obsidian (HEIC, TIFF…) → JPEG
    if fmt in ("JPEG", "PNG", "WEBP", "GIF") and ext not in (".heic", ".heif"):
        out_ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}[fmt]
        rel = f"assets/{month}/{iid}{out_ext}"
        (VAULT / rel).write_bytes(data)
    else:
        rel = f"assets/{month}/{iid}.jpg"
        img.convert("RGB").save(VAULT / rel, "JPEG", quality=92)
    thumb = img.convert("RGB")
    thumb.thumbnail((520, 520))
    thumb.save(THUMBS / f"{iid}.webp", "WEBP", quality=80)
    h = dhash(img)
    return {"image": rel, "width": img.width, "height": img.height, "dhash": h,
            "duplicate_of": _near_duplicate(h, iid),
            "kind": "screenshot" if looks_like_screenshot(img, fmt, filename, source) else "image", **color_fields(img)}


def _with_meta(fields: dict, meta: dict | None = None) -> dict:
    """Sépare les champs couleur destinés à « meta » des colonnes."""
    extra = fields.pop("_meta", {})
    for k in ("palette", "tags"):
        if isinstance(fields.get(k), list):
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    return {**fields, "meta": json.dumps({**(meta or {}), **extra}, ensure_ascii=False)}


def add_image(data: bytes, filename: str = "", source: str = "web", source_url: str = "",
              page_title: str = "", author: str = "", user_note: str = "", site_name: str = "",
              alt: str = "", image_url: str = "", author_url: str = "") -> dict:
    Image.open(io.BytesIO(data)).verify()  # refuse ce qui n'est pas une image
    sha = hashlib.sha1(data).hexdigest()
    same = _by_sha(sha)
    if same:
        return {**add_seen(same["id"], source_url, source), "already": True}
    iid = new_id()
    saved = _with_meta(_save_image(iid, data, filename, source))
    item = dict(id=iid, type="image", status="pending", created=_now(), source=source, source_url=source_url,
                page_title=page_title, author=author, author_url=author_url or None, site_name=site_name, alt=alt,
                image_url=image_url, user_note=user_note, sha1=sha, **saved)
    _insert(item)
    write_note(iid)
    return get(iid)


def add_video(data: bytes, filename: str = "video.mp4", source: str = "web", source_url: str = "",
              page_title: str = "", author: str = "", author_url: str = "", user_note: str = "",
              site_name: str = "") -> dict:
    """Vidéo : fichier gardé (converti en MP4 si le navigateur ne sait pas le lire), image d'aperçu,
    palette calculée sur plusieurs images. La fiche IA est faite à partir de 4 images clés."""
    import tempfile
    sha = hashlib.sha1(data).hexdigest()
    same = _by_sha(sha)
    if same:
        return {**add_seen(same["id"], source_url, source), "already": True}
    iid = new_id()
    month = datetime.now().strftime("%Y-%m")
    (ASSETS / month).mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lower()
    if ext not in media.VIDEO_EXT:
        ext = ".webm" if data[:4] == b"\x1aE\xdf\xa3" else ".mp4"
    dest = ASSETS / month / f"{iid}{ext}"
    dest.write_bytes(data)
    try:
        info = media.probe(dest)
        if not info["duration"] and ext == ".webm":  # les enregistrements du navigateur n'ont pas de durée
            fixed = dest.with_name(f"{iid}.fixed.webm")
            media._run(["-y", "-i", str(dest), "-c", "copy", str(fixed)], 600)
            if fixed.exists() and fixed.stat().st_size:
                fixed.replace(dest)
                info = media.probe(dest)
        if media.needs_transcode(info, dest):
            mp4 = dest.with_suffix(".mp4") if dest.suffix != ".mp4" else dest.with_name(f"{iid}.h264.mp4")
            media.transcode(dest, mp4)
            dest.unlink()
            dest = mp4.replace(ASSETS / month / f"{iid}.mp4")
            info = media.probe(dest)
        poster_rel = f"assets/{month}/{iid}-apercu.jpg"
        media.frame(dest, min((info["duration"] or 0) * 0.15, 3), VAULT / poster_rel, 1600)
        with tempfile.TemporaryDirectory() as td:
            frames = media.key_frames(dest, info["duration"], Path(td), 4, 400)
            strip = Image.new("RGB", (400 * len(frames), 400))
            for i, f in enumerate(frames):
                im = Image.open(f).convert("RGB")
                im.thumbnail((400, 400))
                strip.paste(im, (400 * i, 0))
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    poster = Image.open(VAULT / poster_rel)
    thumb = poster.convert("RGB")
    thumb.thumbnail((520, 520))
    thumb.save(THUMBS / f"{iid}.webp", "WEBP", quality=80)
    h = dhash(poster)
    cf = color_fields(strip)
    item = dict(id=iid, type="image", kind="video", status="pending", created=_now(), source=source,
                source_url=source_url, page_title=page_title, author=author, author_url=author_url or None,
                site_name=site_name, user_note=user_note, sha1=sha, image=poster_rel,
                video=dest.relative_to(VAULT).as_posix(), duration=info["duration"],
                width=info["width"], height=info["height"], dhash=h, **_with_meta(cf, {"has_audio": info["audio"]}))
    _insert(item)
    write_note(iid)
    return get(iid)


def add_url(url: str, source: str = "web", image_url: str = "", page_title: str = "", author: str = "",
            site_name: str = "", alt: str = "", user_note: str = "") -> dict:
    """Lien seul (partage iPhone, Web Clipper, image non lisible par l'extension) :
    l'image sera récupérée en arrière-plan (status 'fetch')."""
    iid = new_id()
    item = dict(id=iid, type="image", kind="image", status="fetch", created=_now(), source=source, source_url=url,
                image_url=image_url, page_title=page_title, author=author, site_name=site_name, alt=alt,
                user_note=user_note)
    _insert(item)
    write_note(iid)
    return get(iid)


def attach_image(item_id: str, data: bytes, filename: str = "", **page) -> dict:
    """Image récupérée pour un item 'fetch'. Si elle existe déjà, l'item fusionne avec l'existant."""
    Image.open(io.BytesIO(data)).verify()
    it = get(item_id)
    sha = hashlib.sha1(data).hexdigest()
    same = _by_sha(sha, item_id)
    if same:
        add_seen(same["id"], it.get("source_url") or "", it.get("source") or "")
        purge(item_id)
        return {**get(same["id"]), "already": True}
    saved = _save_image(item_id, data, filename, it.get("source") or "")
    extra = saved.pop("_meta", {})
    fields = {k: v for k, v in page.items() if v and not it.get(k)}
    update(item_id, status="pending", sha1=sha, error=None, attempts=0, meta={**it["meta"], **extra}, **saved, **fields)
    write_note(item_id)
    return get(item_id)


# ---------- rogner / redimensionner ----------
ORIGINALS = META / "originaux"


def edit_image(item_id: str, crop: dict | None, width: int | None, height: int | None,
               mode: str = "replace", reclassify: bool = False) -> dict:
    """Rogne (x, y, w, h en pixels de l'image d'origine) puis redimensionne.
    mode « replace » : remplace l'image (l'original est gardé dans .inspi/originaux, restaurable).
    mode « copy » : crée une nouvelle inspi avec la même source et la même note."""
    it = get(item_id)
    if not it or not it.get("image"):
        raise ValueError("Pas d'image à modifier.")
    src = VAULT / it["image"]
    img = ImageOps.exif_transpose(Image.open(src))
    fmt = (Image.open(src).format or "JPEG").upper()
    if crop:
        x, y = max(0, int(crop["x"])), max(0, int(crop["y"]))
        w, h = int(crop["w"]), int(crop["h"])
        x2, y2 = min(img.width, x + w), min(img.height, y + h)
        if x2 - x < 8 or y2 - y < 8:
            raise ValueError("Zone de rognage trop petite.")
        img = img.crop((x, y, x2, y2))
    if width or height:
        w = int(width or round(img.width * height / img.height))
        h = int(height or round(img.height * width / img.width))
        if not (8 <= w <= 12000 and 8 <= h <= 12000):
            raise ValueError("Taille invalide (8 à 12000 px).")
        img = img.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt == "PNG":
        img.save(buf, "PNG", optimize=True)
    elif fmt == "WEBP":
        img.save(buf, "WEBP", quality=95)
    else:
        img.convert("RGB").save(buf, "JPEG", quality=95)
    data = buf.getvalue()
    if mode == "copy":
        new = add_image(data, Path(it["image"]).name, source=it.get("source") or "web",
                        source_url=it.get("source_url") or "", page_title=it.get("page_title") or "",
                        author=it.get("author") or "", site_name=it.get("site_name") or "",
                        user_note=((it.get("user_note") or "") + f"\n(recadrage de {item_id})").strip())
        if not new.get("already") and not reclassify and it["status"] == "done":
            # même fiche que l'original (la palette peut différer légèrement) : pas besoin de reclasser
            fields = {k: it[k] for k in ("title", "description", "tags", "style", "medium", "mood")}
            keep = {k: v for k, v in it["meta"].items() if k not in ("also_seen", "trash_files", "original", "palette",
                                                                   "main_color", "accent_color", "color_temperature")}
            update(new["id"], status="done", schema_version=it.get("schema_version"), meta={**new["meta"], **keep},
                   **fields)
            write_note(new["id"])
        return get(new["id"])
    ORIGINALS.mkdir(exist_ok=True)
    meta = dict(it["meta"])
    if not meta.get("original"):  # on garde le tout premier original
        dest = ORIGINALS / Path(it["image"]).name
        shutil.copy2(src, dest)
        meta["original"] = {"file": dest.name, "width": it.get("width"), "height": it.get("height")}
    target = src.with_suffix({"PNG": ".png", "WEBP": ".webp"}.get(fmt, ".jpg"))
    target.write_bytes(data)
    if target != src:
        src.unlink(missing_ok=True)
    rel = target.relative_to(VAULT).as_posix()
    thumb = img.convert("RGB")
    thumb.thumbnail((520, 520))
    thumb.save(THUMBS / f"{item_id}.webp", "WEBP", quality=80)
    meta["edited_at"] = _now()
    cf = color_fields(img)
    meta.update(cf.pop("_meta"))
    fields = dict(image=rel, width=img.width, height=img.height, sha1=hashlib.sha1(data).hexdigest(), **cf,
                  dhash=dhash(img), meta=meta)
    if reclassify:
        fields.update(status="pending", attempts=0, error=None)
    update(item_id, **fields)
    write_note(item_id)
    return get(item_id)


def restore_original(item_id: str) -> dict:
    it = get(item_id)
    o = (it or {}).get("meta", {}).get("original")
    if not o or not (ORIGINALS / o["file"]).exists():
        raise ValueError("Pas d'original sauvegardé pour cette image.")
    data = (ORIGINALS / o["file"]).read_bytes()
    cur = VAULT / it["image"]
    target = cur.with_name(o["file"])
    if target != cur:
        cur.unlink(missing_ok=True)
    target.write_bytes(data)
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    thumb = img.convert("RGB")
    thumb.thumbnail((520, 520))
    thumb.save(THUMBS / f"{item_id}.webp", "WEBP", quality=80)
    meta = {k: v for k, v in it["meta"].items() if k not in ("original", "edited_at")}
    cf = color_fields(img)
    meta.update(cf.pop("_meta"))
    update(item_id, image=target.relative_to(VAULT).as_posix(), width=img.width, height=img.height, **cf,
           sha1=hashlib.sha1(data).hexdigest(), dhash=dhash(img), meta=meta)
    (ORIGINALS / o["file"]).unlink()
    write_note(item_id)
    return get(item_id)


def add_text(text: str, source: str = "web", source_url: str = "", page_title: str = "", user_note: str = "") -> dict:
    iid = new_id()
    t = text.strip()
    is_link = bool(re.fullmatch(r"https?://\S+", t))
    item = dict(id=iid, type="text", kind="link" if is_link else "text", status="pending", created=_now(),
                source=source, source_url=source_url or (t if is_link else ""), page_title=page_title, text=t,
                user_note=user_note)
    _insert(item)
    write_note(iid)
    return get(iid)


# ---------- corbeille ----------
def _used_by_board(rel: str, sha: str | None = None) -> bool:
    """Vrai si un moodboard utilise l'image (lien complet, lien court réécrit par Obsidian, ou empreinte)."""
    from .boards import board_refs, link_name
    name = link_name(rel)
    for p in BOARDS.glob("*.excalidraw.md"):
        fids, names = board_refs(p.read_text(encoding="utf-8", errors="ignore"))
        if name in names or (sha and sha in fids):
            return True
    return False


def trash(ids: list[str]) -> dict:
    """Met des inspi à la corbeille (annulable). L'image reste si un moodboard l'utilise."""
    batch = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    dest = TRASH / batch
    moved, kept = [], []
    for iid in ids:
        it = get(iid)
        if not it or it.get("trashed"):
            continue
        dest.mkdir(parents=True, exist_ok=True)
        files = {}
        for key in ("note", "image", "video"):
            rel = it.get(key)
            if not rel or not (VAULT / rel).exists():
                continue
            if key == "image" and _used_by_board(rel, it.get("sha1")):
                kept.append(rel)
                continue
            target = dest / rel.replace("/", "__")
            shutil.move(str(VAULT / rel), str(target))
            files[rel] = target.name
        update(iid, trashed=batch, meta={**it["meta"], "trash_files": files})
        moved.append(iid)
    with open(META / "suppressions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"batch": batch, "ids": moved, "date": _now()}) + "\n")
    return {"batch": batch, "count": len(moved), "kept_images": kept}


def restore(batch: str) -> int:
    with _lock:
        rows = _conn.execute("SELECT id FROM items WHERE trashed=?", (batch,)).fetchall()
    n = 0
    for r in rows:
        it = get(r["id"])
        for rel, name in (it["meta"].get("trash_files") or {}).items():
            src = TRASH / batch / name
            if src.exists() and not (VAULT / rel).exists():
                (VAULT / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(VAULT / rel))
        meta = {k: v for k, v in it["meta"].items() if k != "trash_files"}
        update(it["id"], trashed=None, meta=meta)
        n += 1
    return n


def purge(item_id: str) -> None:
    """Suppression interne d'un item vide (fusion de doublons)."""
    it = get(item_id)
    if it and it.get("note") and (VAULT / it["note"]).exists():
        (VAULT / it["note"]).unlink()
    with _lock:
        _conn.execute("DELETE FROM items WHERE id=?", (item_id,))
        _conn.commit()
    bump()


# ---------- note Markdown ----------
def _yaml(v):
    return v


def write_note(item_id: str) -> None:
    it = get(item_id)
    if not it or it.get("trashed"):
        return
    m = it["meta"]
    folder = LIBRARY if it["status"] == "done" else INBOX
    name = f"{it['id']} {slug(it.get('title') or it.get('page_title') or it['type'])}.md"
    rel = f"{folder.name}/{name}"
    fm: dict = {"id": it["id"], "type": it["type"], "kind": it.get("kind") or it["type"], "status": it["status"],
                "captured": it["created"], "source": it["source"]}
    if it.get("video"):
        fm["video"] = it["video"]
        fm["duration"] = it.get("duration")
    for k in ("source_url", "image_url", "page_title", "site_name", "author", "author_url"):
        if it.get(k):
            fm[k] = it[k]
    if it.get("author"):
        fm["author_source"] = "page"
    if it.get("image"):
        fm["thumbnail"] = it["image"]
    if it["status"] == "done":
        fm["schema_version"] = it.get("schema_version") or 1
        fm["medium"] = it.get("medium") or None
        for k in ("style", "movement", "mood", "technique"):
            fm[k] = m.get(k) or ([it[k]] if it.get(k) else [])
        fm["tags"] = [t.replace(" ", "-") for t in it["tags"]]
        fm["main_color"] = m.get("main_color") or (it["palette"][0] if it["palette"] else None)
        fm["color_name"] = colors.color_name(fm["main_color"])
        fm["color_temperature"] = m.get("color_temperature")
        fm["accent_color"] = m.get("accent_color")
        fm["palette"] = [p["hex"] for p in m.get("palette", [])] or it["palette"]
        for k in ("era", "subject", "composition", "lighting", "typography", "texture"):
            fm[k] = m.get(k)
        fm["references"] = m.get("references") or []
        fm["use_for"] = m.get("use_for") or []
        fm["themes"] = [f"[[{t}]]" for t in m.get("themes") or []]
        if m.get("author_guess"):
            fm["author_guess"] = m["author_guess"]
            fm["author_confidence"] = m.get("author_confidence")
        if m.get("fiche_incomplete"):
            fm["fiche_incomplete"] = m["fiche_incomplete"]
        fm["enriched_by"] = m.get("enriched_by")
        fm["enriched_at"] = m.get("enriched_at")
        fm = {k: v for k, v in fm.items() if v not in (None, "", [])} | {"tags": fm["tags"]}
    if it.get("duplicate_of"):
        fm["duplicate_of"] = it["duplicate_of"]
    body = ["---", yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, width=1000).strip(), "---", ""]
    body += [f"# {it.get('title') or it.get('page_title') or ('Lien à récupérer' if it['status'] == 'fetch' else 'Inspi')}", ""]
    if it.get("video"):
        body += [f"![[{it['video']}]]", ""]
    elif it.get("image"):
        body += [f"![[{it['image']}]]", ""]
    elif it.get("image_url"):
        body += [f"![]({it['image_url']})", ""]
    if it.get("text"):
        body += ["> " + it["text"].replace("\n", "\n> "), ""]
    if it.get("description"):
        body += ["## Pourquoi", it["description"], ""]
    pal = m.get("palette") or []
    if pal:
        body += ["## Palette", " · ".join(f"`{p['hex']}` {p.get('name', '')}".strip() for p in pal), ""]
    details = [(lbl, m.get(k)) for lbl, k in (("Sujet", "subject"), ("Composition", "composition"),
               ("Lumière", "lighting"), ("Typographie", "typography"), ("Texture", "texture"), ("Époque", "era"))]
    details = [(lbl, v) for lbl, v in details if v]
    if details:
        body += ["## Analyse"] + [f"- **{lbl}** : {v}" for lbl, v in details] + [""]
    if m.get("references"):
        body += ["Proche de : " + ", ".join(m["references"]), ""]
    if it.get("user_note"):
        body += ["## Ma note", it["user_note"], ""]
    body += ["## Source"]
    body.append(f"- [{it.get('page_title') or it['source_url']}]({it['source_url']})" if it.get("source_url") else "- (capture locale)")
    if it.get("author"):
        body.append(f"- Auteur : [{it['author']}]({it['author_url']})" if it.get("author_url") else f"- Auteur : {it['author']}")
    for s in m.get("also_seen") or []:
        body.append(f"- Aussi capturé le {s['date']} : {s['url']}")
    body.append("")
    if it.get("error"):
        body += [f"> [!warning] {it['error']}", ""]
    old = it.get("note")
    if old and old != rel and (VAULT / old).exists():
        (VAULT / old).unlink()
    (VAULT / rel).write_text("\n".join(body), encoding="utf-8")
    if old != rel:
        update(item_id, note=rel)
    for t in m.get("themes") or []:
        ensure_theme(t)


def ensure_theme(name: str) -> None:
    safe = re.sub(r'[\\/:*?"<>|#^\[\]]', "", name).strip()
    if not safe:
        return
    p = THEMES / f"{safe}.md"
    if p.exists():
        return
    p.write_text(
        f"---\ntype: theme\ntags: [theme]\n---\n\n# {safe}\n\nThème créé automatiquement par Inspi.\n\n"
        "```dataview\nTABLE WITHOUT ID embed(link(thumbnail)) AS \"\", file.link AS Inspi, style, mood\n"
        "FROM [[]] AND \"Library\"\nSORT captured DESC\n```\n", encoding="utf-8")


# ---------- recherche ----------
def all_items(include_trashed: bool = False) -> list[dict]:
    sql = "SELECT * FROM items" + ("" if include_trashed else " WHERE trashed IS NULL") + " ORDER BY created DESC"
    with _lock:
        return [row_to_dict(r) for r in _conn.execute(sql).fetchall()]


def _haystack(it: dict) -> str:
    m = it["meta"]
    parts = [it.get(k) or "" for k in ("title", "description", "style", "medium", "mood", "page_title", "text",
                                       "user_note", "author", "site_name")]
    parts += it["tags"]
    for k in ("style", "movement", "mood", "technique", "references", "use_for", "themes"):
        parts += [str(x) for x in m.get(k) or []]
    for k in ("subject", "composition", "lighting", "typography", "texture", "era", "author_guess"):
        parts.append(str(m.get(k) or ""))
    parts += [p.get("name", "") for p in m.get("palette") or []]
    import unicodedata
    s = " ".join(parts).lower()
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def search(q: str = "", tag: str = "", type_: str = "", status: str = "", color: str = "",
           facet: dict | None = None, sort: str = "new", limit: int = 500, offset: int = 0) -> list[dict]:
    import unicodedata
    items = all_items()
    words = unicodedata.normalize("NFKD", (q or "").lower()).encode("ascii", "ignore").decode().split()
    target = colors.parse_color(color) if color else None
    out = []
    for it in items:
        if type_ and (it.get("kind") or it["type"]) != type_ and not (type_ == "image" and it["type"] == "image" and not it.get("kind")):
            continue
        if status == "incomplete":
            if not (it["status"] == "done" and it["meta"].get("fiche_incomplete")):
                continue
        elif status and it["status"] != status:
            continue
        if tag and tag not in it["tags"]:
            continue
        if words:
            hay = _haystack(it)
            if not all(w in hay for w in words):
                continue
        it["facets"] = colors.facets(it)
        if facet and any(v and v not in it["facets"].get(k, []) for k, v in facet.items()):
            continue
        if target:
            d = colors.best_distance(it["meta"].get("palette") or it["palette"], it["meta"].get("main_color"), target)
            if d is None or d > 38:
                continue
            it["color_distance"] = round(d, 1)
        out.append(it)
    if target:
        out.sort(key=lambda x: x["color_distance"])
    elif sort == "old":
        out.reverse()
    elif sort == "title":
        out.sort(key=lambda x: (x.get("title") or x.get("page_title") or "~").lower())
    elif sort == "color":
        def hue(x):
            lb = colors.lab(x["meta"].get("main_color") or (x["palette"] or ["#808080"])[0])
            import math
            return (0, 0) if not lb else (round(math.atan2(lb[2], lb[1]), 1), -lb[0])
        out.sort(key=hue)
    return out[offset:offset + limit]


def facet_counts(items: list[dict]) -> dict:
    counts: dict[str, dict[str, int]] = {}
    for it in items:
        for k, vals in (it.get("facets") or colors.facets(it)).items():
            for v in vals:
                counts.setdefault(k, {})
                counts[k][v] = counts[k].get(v, 0) + 1
    return {k: sorted(v.items(), key=lambda x: -x[1]) for k, v in counts.items()}


def tag_counts() -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for it in all_items():
        for t in it["tags"]:
            counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda x: -x[1])


def queue(status: str, max_attempts: int = 3) -> list[dict]:
    with _lock:
        rows = _conn.execute("SELECT * FROM items WHERE status=? AND attempts<? AND trashed IS NULL ORDER BY created",
                             (status, max_attempts)).fetchall()
    return [row_to_dict(r) for r in rows]


def pending(max_attempts: int = 3) -> list[dict]:
    return queue("pending", max_attempts)


def requeue_all(only_old: bool = True) -> int:
    """Reclasse la bibliothèque (toutes les notes, ou seulement celles d'un schéma plus ancien)."""
    sql = "UPDATE items SET status='pending', attempts=0, error=NULL WHERE trashed IS NULL AND status IN ('done','error')"
    if only_old:
        sql += f" AND coalesce(schema_version,0) < {SCHEMA_VERSION}"
    with _lock:
        n = _conn.execute(sql).rowcount
        _conn.commit()
    bump()
    return n


def count_incomplete() -> int:
    return sum(1 for it in all_items() if it["status"] == "done" and it["meta"].get("fiche_incomplete"))


def stats() -> dict:
    with _lock:
        rows = _conn.execute("SELECT status, count(*) n FROM items WHERE trashed IS NULL GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def known_note_paths() -> set[str]:
    with _lock:
        return {r["note"] for r in _conn.execute("SELECT note FROM items WHERE note IS NOT NULL")}


def upgrade_library(on_progress=None) -> int:
    """Met à niveau les anciennes inspi : palette calculée à partir des pixels et type (image, capture, lien…)."""
    n = 0
    for it in all_items():
        fields: dict = {}
        if it.get("image") and (it.get("colors_v") or 0) < COLORS_VERSION and (VAULT / it["image"]).exists():
            try:
                img = Image.open(VAULT / it["image"])
                cf = color_fields(img)
                meta = {**it["meta"], **cf.pop("_meta")}
                fields.update(cf, meta=meta)
                if not it.get("kind"):
                    fmt = (img.format or "").upper()
                    fields["kind"] = "screenshot" if looks_like_screenshot(img, fmt, it["image"], it.get("source") or "") else "image"
            except Exception:  # noqa: BLE001
                fields["colors_v"] = COLORS_VERSION
        if not it.get("kind") and "kind" not in fields:
            t = (it.get("text") or "").strip()
            fields["kind"] = "link" if it["type"] == "text" and re.fullmatch(r"https?://\S+", t) else it["type"]
        if fields:
            update(it["id"], **fields)
            write_note(it["id"])
            n += 1
            if on_progress:
                on_progress(n)
    return n
