"""Serveur local : reçoit les captures (iPhone, extension, PC) et sert la galerie."""
from __future__ import annotations

import base64
import io
import logging
import urllib.parse
from pathlib import Path

import qrcode
import qrcode.image.svg
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from . import auth, boards, enrich, similar, store
from .config import CFG, save as save_config

WEB = Path(__file__).resolve().parent.parent / "web"
PORT = int(CFG["port"])
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
COOKIE = "inspi_token"
log = logging.getLogger("inspi.server")

app = FastAPI(title="Inspi Local", docs_url=None, redoc_url=None)


# ---------- sécurité ----------
def _host_ok(request: Request) -> bool:
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    return host in LOOPBACK or host == auth.lan_ip()  # protège du « DNS rebinding »


def _is_local(request: Request) -> bool:
    return (request.client.host if request.client else "") in LOOPBACK


def _token_from(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return request.headers.get("x-inspi-token") or request.cookies.get(COOKIE)


def authed(request: Request) -> bool:
    if not _host_ok(request):
        return False
    if auth.check(_token_from(request)):
        return True
    if _is_local(request):
        # Depuis le PC lui-même : OK sans jeton, sauf requête d'écriture venant d'un autre site
        if request.method in ("GET", "HEAD"):
            return True
        origin = request.headers.get("origin")
        return origin is None or origin in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")
    return False


def need(request: Request):
    if not authed(request):
        log.warning("refusé : %s %s depuis %s (host=%s, origin=%s)", request.method, request.url.path,
                    request.client.host if request.client else "?", request.headers.get("host"),
                    request.headers.get("origin"))
        raise HTTPException(401, "Appareil non appairé. Ouvre la page d'appairage sur le PC.")


def need_local(request: Request):
    if not (_is_local(request) and _host_ok(request)):
        raise HTTPException(403, "Cette page n'est accessible que depuis le PC.")


# ---------- pages ----------
def _page(request: Request, name: str):
    if not authed(request):
        return HTMLResponse((WEB / "not-paired.html").read_text(encoding="utf-8"), status_code=401)
    return FileResponse(WEB / name)


@app.get("/", response_class=HTMLResponse)
def gallery(request: Request):
    return _page(request, "gallery.html")


@app.get("/m", response_class=HTMLResponse)
def phone(request: Request):
    return _page(request, "phone.html")


@app.get("/p")
def pair_link(t: str, request: Request):
    """Lien contenu dans le QR code : enregistre le jeton dans un cookie puis ouvre la page iPhone."""
    if not _host_ok(request) or not auth.check(t):
        raise HTTPException(401, "QR code expiré : réaffiche la page d'appairage sur le PC.")
    r = RedirectResponse("/m", status_code=303)
    r.set_cookie(COOKIE, t, max_age=60 * 60 * 24 * 400, httponly=True, samesite="strict")
    return r


@app.get("/pair", response_class=HTMLResponse)
def pair_page(request: Request):
    need_local(request)
    ip, tok = auth.lan_ip(), auth.get_token()
    base = f"http://{ip}:{PORT}"
    link = f"{base}/p?t={urllib.parse.quote(tok)}"
    img = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, box_size=12)
    buf = io.BytesIO()
    img.save(buf)
    html = (WEB / "pair.html").read_text(encoding="utf-8")
    return html.replace("{{QR}}", buf.getvalue().decode()).replace("{{BASE}}", base).replace("{{TOKEN}}", tok)


@app.post("/api/extension/token")
def extension_token(request: Request):
    """Appairage automatique de l'extension : uniquement depuis ce PC et depuis une extension de navigateur
    (une page web ne peut pas imiter l'en-tête Origin d'une extension)."""
    origin = request.headers.get("origin") or ""
    if not (_is_local(request) and _host_ok(request) and origin.startswith(("chrome-extension://", "extension://"))):
        raise HTTPException(403, "Réservé à l'extension Inspi sur ce PC.")
    log.info("extension appairée : %s", origin)
    return JSONResponse({"token": auth.get_token()}, headers={"Access-Control-Allow-Origin": origin})


@app.post("/api/extension/install")
async def extension_install(request: Request):
    need_local(request)
    need(request)
    body = await request.json()
    from . import winsetup
    try:
        return {"ok": True, **winsetup.install_extension(str(body.get("browser", "")))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


@app.get("/aide-extension", response_class=HTMLResponse)
def extension_help(request: Request):
    need_local(request)
    from . import winsetup
    return (WEB / "extension.html").read_text(encoding="utf-8").replace("{{FOLDER}}", str(winsetup.EXT_DEST))


@app.post("/api/token/reset")
def token_reset(request: Request):
    need_local(request)
    need(request)
    auth.reset_token()
    return {"ok": True}


for _name, _mime in (("manifest.webmanifest", "application/manifest+json"), ("icon.svg", "image/svg+xml"),
                     ("app.css", "text/css"), ("icon.png", "image/png"), ("icon-512.png", "image/png")):
    def _static(n=_name, m=_mime):
        return FileResponse(WEB / n, media_type=m)
    app.get(f"/{_name}")(_static)


# ---------- capture ----------
def _is_url(t: str) -> bool:
    return t.startswith(("http://", "https://")) and " " not in t and "\n" not in t


def _msg(created: list[dict]) -> str:
    already = sum(1 for c in created if c.get("already"))
    links = sum(1 for c in created if c.get("status") == "fetch")
    new = len(created) - already - links
    parts = []
    if new:
        parts.append(f"{new} inspi reçue(s), classement en cours")
    if links:
        parts.append(f"{links} lien(s) : image en cours de récupération")
    if already:
        parts.append(f"{already} déjà enregistrée(s), source ajoutée")
    return " · ".join(parts)


@app.post("/api/capture")
async def capture(
    request: Request,
    file: list[UploadFile] | None = File(None),
    text: str = Form(""),
    url: str = Form(""),
    title: str = Form(""),
    note: str = Form(""),
    author: str = Form(""),
    site_name: str = Form(""),
    alt: str = Form(""),
    image_url: str = Form(""),
    source: str = Form("iphone"),
):
    """Formulaire : images (champ file, plusieurs possibles), texte, lien. Utilisé par l'iPhone et la galerie."""
    need(request)
    created = []
    page = dict(source=source, page_title=title, author=author, site_name=site_name, alt=alt)
    for f in file or []:
        data = await f.read()
        if not data:
            continue
        try:
            created.append(store.add_image(data, f.filename or "", source_url=url, user_note=note,
                                           image_url=image_url, **page))
        except Exception as e:  # noqa: BLE001
            # Le Raccourci iOS envoie le texte / les liens partagés comme un « fichier » texte
            try:
                txt = data.decode("utf-8").strip()
            except UnicodeDecodeError:
                raise HTTPException(400, f"Fichier illisible ({f.filename}) : {e}")
            if len(data) < 200_000 and txt:
                text = (text + "\n" + txt).strip()
            else:
                raise HTTPException(400, f"Fichier illisible ({f.filename})")
    text = text.strip()
    if not created:
        link = url or (text if _is_url(text) else "")
        if link or image_url:
            # Lien partagé (Pinterest, Behance, site…) : le PC va chercher l'image de la page
            created.append(store.add_url(link, image_url=image_url, user_note=note, **page))
        elif text:
            created.append(store.add_text(text, source=source, source_url=url, page_title=title, user_note=note))
    elif text:  # image + texte : le texte devient la note
        for it in created:
            if not it.get("already"):
                store.update(it["id"], user_note=(note + "\n" + text).strip())
                store.write_note(it["id"])
    if not created:
        raise HTTPException(400, "Rien à enregistrer : envoie une image, un lien ou du texte.")
    enrich.WORKER.wake.set()
    return {"ok": True, "count": len(created), "ids": [c["id"] for c in created], "message": _msg(created)}


@app.post("/api/capture-json")
async def capture_json(request: Request):
    """Extension : {image_b64?, image_url?, filename?, text?, url?, title?, note?, author?, site_name?, alt?}.
    Sans octets mais avec image_url : le PC récupère l'image lui-même (sinon le lien est gardé)."""
    need(request)
    body = await request.json()
    page = dict(source=str(body.get("source", "extension"))[:20], page_title=str(body.get("title", ""))[:300],
                author=str(body.get("author", ""))[:200], site_name=str(body.get("site_name", ""))[:200],
                alt=str(body.get("alt", ""))[:500])
    url, note, img_url = str(body.get("url", "")), str(body.get("note", "")), str(body.get("image_url", ""))
    text = str(body.get("text", "")).strip()
    if body.get("image_b64"):
        try:
            it = store.add_image(base64.b64decode(body["image_b64"]), body.get("filename", ""), source_url=url,
                                 user_note=note, image_url=img_url if img_url.startswith("http") else "", **page)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Image illisible : {e}")
    elif img_url.startswith("http") or _is_url(text):
        it = store.add_url(url if img_url else text, image_url=img_url, user_note=note, **page)
    elif text:
        it = store.add_text(text, source=page["source"], source_url=url, page_title=page["page_title"], user_note=note)
    else:
        raise HTTPException(400, "image_b64, image_url ou text requis")
    enrich.WORKER.wake.set()
    return {"ok": True, "id": it["id"], "already": bool(it.get("already")), "message": _msg([it])}


# ---------- consultation ----------
FACETS = ("type", "technique", "sujet", "couleur", "température", "usage")


def _facet_params(request: Request) -> dict:
    return {k: request.query_params.get(f"f_{k}") for k in FACETS if request.query_params.get(f"f_{k}")}


@app.get("/api/items")
def items(request: Request, q: str = "", tag: str = "", type: str = "", status: str = "", color: str = "",
          sort: str = "new", limit: int = 500, offset: int = 0):
    need(request)
    res = store.search(q, tag, type, status, color, _facet_params(request), sort, min(limit, 5000), offset)
    if sort == "family":
        fam = similar.data()["fam_of"]
        res.sort(key=lambda it: fam.get(it["id"], 9999))
    elif sort == "board":
        rank = {}
        for bi, b in enumerate(boards.list_boards()):
            for iid in b["ids"]:
                rank.setdefault(iid, bi)
        res.sort(key=lambda it: rank.get(it["id"], 9999))
    return res


@app.get("/api/facets")
def facets(request: Request, q: str = "", color: str = "", type: str = ""):
    need(request)
    return store.facet_counts(store.search(q, "", type, "", color, _facet_params(request), limit=1_000_000))


@app.get("/api/tags")
def tags(request: Request):
    need(request)
    return [{"tag": t, "n": n} for t, n in store.tag_counts()]


@app.get("/api/similar")
def sim(request: Request, ids: str, limit: int = 40):
    need(request)
    out = []
    for iid, score in similar.similar_to([i for i in ids.split(",") if i], limit):
        it = store.get(iid)
        if it and not it.get("trashed"):
            out.append({**it, "score": score})
    return out


@app.get("/api/families")
def families(request: Request):
    need(request)
    return [{"id": f["id"], "label": f["label"], "size": len(f["ids"]), "ids": f["ids"]}
            for f in similar.data()["families"]]


@app.get("/api/duplicates")
def duplicates(request: Request):
    need(request)
    return [{**g, "items": [store.get(i) for i in g["ids"]]} for g in similar.duplicate_groups()]


@app.get("/api/status")
def status(request: Request):
    need(request)
    ok, msg = enrich.backend_ok()
    return {"counts": store.stats(), "worker": enrich.WORKER.status, "ollama": ok, "ollama_msg": msg,
            "model": enrich.backend_label(), "ai_label": enrich.backend_label(), "ai_backend": CFG["ai_backend"],
            "incomplete": store.count_incomplete(), "vault": str(store.VAULT), "vault_name": store.VAULT.name,
            "is_pc": _is_local(request), "use_for": enrich.USE_FOR}


@app.patch("/api/item/{iid}")
async def edit(iid: str, request: Request):
    need(request)
    body = await request.json()
    it = store.get(iid)
    if not it:
        raise HTTPException(404)
    allowed = {k: body[k] for k in ("title", "user_note", "tags", "description", "author") if k in body}
    if "tags" in allowed:
        allowed["tags"] = [enrich.norm_tag(t) for t in allowed["tags"] if str(t).strip()]
    if "use_for" in body:
        allowed["meta"] = {**it["meta"], "use_for": [u for u in body["use_for"] if u in enrich.USE_FOR]}
    store.update(iid, **allowed)
    store.write_note(iid)
    return store.get(iid)


@app.post("/api/item/{iid}/retry")
def retry(iid: str, request: Request):
    need(request)
    it = store.get(iid)
    if not it:
        raise HTTPException(404)
    store.update(iid, status="fetch" if (it["type"] == "image" and not it.get("image")) else "pending",
                 attempts=0, error=None)
    store.write_note(iid)
    enrich.WORKER.wake.set()
    return {"ok": True}


@app.post("/api/item/{iid}/edit")
async def edit_image(iid: str, request: Request):
    """Rogner / redimensionner : {crop?: {x,y,w,h}, width?, height?, mode: replace|copy, reclassify?}."""
    need(request)
    b = await request.json()
    try:
        it = store.edit_image(iid, b.get("crop"), b.get("width"), b.get("height"),
                              "copy" if b.get("mode") == "copy" else "replace", bool(b.get("reclassify")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if it["status"] == "pending":
        enrich.WORKER.wake.set()
    return it


@app.post("/api/item/{iid}/restore-original")
def restore_original(iid: str, request: Request):
    need(request)
    try:
        return store.restore_original(iid)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/item/{iid}")
def remove(iid: str, request: Request):
    need(request)
    res = store.trash([iid])
    if not res["count"]:
        raise HTTPException(404)
    return {"ok": True, **res}


@app.post("/api/trash")
async def trash(request: Request):
    need(request)
    body = await request.json()
    return {"ok": True, **store.trash([str(i) for i in body.get("ids", [])])}


@app.post("/api/restore")
async def restore(request: Request):
    need(request)
    body = await request.json()
    n = store.restore(str(body.get("batch", "")))
    return {"ok": True, "restored": n}


@app.post("/api/reclassify-all")
async def reclassify_all(request: Request):
    need(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    n = store.requeue_all(only_old=not body.get("all"))
    enrich.WORKER.wake.set()
    return {"ok": True, "count": n}


# ---------- réglages de l'IA (depuis le PC uniquement) ----------
@app.get("/api/ai")
def ai_get(request: Request):
    need_local(request)
    key = enrich.claude_key()
    return {"backend": CFG["ai_backend"], "ollama_model": CFG["ollama_model"], "ollama_url": CFG["ollama_url"],
            "ollama_models": enrich.ollama_models(), "ollama_ok": enrich.ollama_ok(),
            "claude_model": CFG["claude_model"], "claude_models": enrich.CLAUDE_MODELS,
            "has_key": bool(key), "key_hint": (key[:7] + "…" + key[-4:]) if len(key) > 12 else "",
            "fallback": bool(CFG.get("ai_fallback"))}


@app.post("/api/ai")
async def ai_set(request: Request):
    need_local(request)
    need(request)
    body = await request.json()
    upd = {}
    if body.get("backend") in ("ollama", "claude"):
        upd["ai_backend"] = body["backend"]
    if body.get("ollama_model"):
        upd["ollama_model"] = str(body["ollama_model"])[:100]
    if body.get("claude_model") in dict(enrich.CLAUDE_MODELS):
        upd["claude_model"] = body["claude_model"]
    if "fallback" in body:
        upd["ai_fallback"] = bool(body["fallback"])
    if upd:
        save_config(**upd)
    if body.get("api_key"):
        k = str(body["api_key"]).strip()
        if not k.startswith("sk-"):
            raise HTTPException(400, "Clé invalide : elle commence par « sk-ant- ».")
        enrich.set_claude_key(k)
    if body.get("clear_key"):
        enrich.set_claude_key("")
    enrich.WORKER.wake.set()
    return ai_get(request)


@app.post("/api/ai/test")
async def ai_test(request: Request):
    """Classe une image (la plus récente, ou celle demandée) SANS l'enregistrer, pour vérifier la fiche."""
    need_local(request)
    need(request)
    import time
    body = await request.json()
    it = store.get(body["id"]) if body.get("id") else next(
        (i for i in store.all_items() if i["type"] == "image" and i.get("image")), None)
    if not it:
        raise HTTPException(400, "Ajoute d'abord une image pour tester.")
    backend = body.get("backend") or CFG["ai_backend"]
    t0 = time.time()
    try:
        from starlette.concurrency import run_in_threadpool
        res = await run_in_threadpool(enrich.classify, it, backend)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"{enrich.backend_label(backend)} : {e}")
    return {"ok": True, "id": it["id"], "seconds": round(time.time() - t0, 1), "label": enrich.backend_label(backend),
            "used": res["meta"]["enriched_by"], "missing": res["meta"].get("fiche_incomplete") or [], "fiche": res}


@app.get("/api/doctor")
def doctor_api(request: Request):
    need(request)
    from . import doctor
    return doctor.run(server_running=True)


@app.get("/media/{iid}/{kind}")
def media(iid: str, kind: str, request: Request):
    need(request)
    it = store.get(iid)
    if not it or not it.get("image"):
        raise HTTPException(404)
    p = store.THUMBS / f"{iid}.webp" if kind == "thumb" else store.VAULT / it["image"]
    if not p.exists():
        p = store.VAULT / it["image"]
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-cache"})  # l'image peut être recadrée


@app.get("/api/boards")
def list_boards(request: Request):
    need(request)
    vault = urllib.parse.quote(store.VAULT.name)
    return [{**b, "obsidian": f"obsidian://open?vault={vault}&file={urllib.parse.quote(b['path'][:-3])}"}
            for b in boards.list_boards()]


@app.post("/api/board")
async def make_board(request: Request):
    need(request)
    body = await request.json()
    ids = [str(i) for i in body.get("ids", [])]
    try:
        rel, added = boards.build(str(body.get("name", "Moodboard")), ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    vault = urllib.parse.quote(store.VAULT.name)
    return {"ok": True, "path": rel, "added": added, "similar": [i for i, _ in similar.similar_to(ids, 12)],
            "obsidian": f"obsidian://open?vault={vault}&file={urllib.parse.quote(rel[:-3])}"}


@app.exception_handler(HTTPException)
async def http_err(_, exc: HTTPException):
    return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
