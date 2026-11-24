"""Récupère l'image d'un lien partagé (page web → og:image, Pinterest → original HD).

Sécurité : http(s) uniquement, adresses privées/locales refusées (y compris après redirection),
taille et durée plafonnées. Seul le serveur Inspi appelle ce module, jamais une page web.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0 Safari/537.36")
MAX_PAGE = 3_000_000
MAX_IMAGE = 25 * 1024 * 1024
TIMEOUT = 15
PIN_HOST = re.compile(r"(^|\.)pinterest\.[a-z.]+$|^pin\.it$", re.I)
PINIMG = re.compile(r"^https://i\.pinimg\.com/(\d+x|originals)/", re.I)


class FetchError(Exception):
    pass


def _check_host(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("lien non http(s)")
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except socket.gaierror:
        raise FetchError(f"site introuvable : {u.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise FetchError("adresse locale ou privée refusée")


def _get(url: str, cap: int, referer: str = "") -> tuple[bytes, str, str]:
    """GET avec redirections vérifiées une par une. Retourne (contenu, type, url finale)."""
    headers = {"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    for _ in range(6):
        _check_host(url)
        r = requests.get(url, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=False)
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers.get("location", ""))
            r.close()
            continue
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code}")
        buf = bytearray()
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > cap:
                raise FetchError("fichier trop gros")
        return bytes(buf), r.headers.get("content-type", ""), url
    raise FetchError("trop de redirections")


class _Meta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta: dict[str, str] = {}
        self.title = ""
        self.first_img = ""
        self.ld: list[str] = []
        self._t = self._ld = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "meta":
            k = (d.get("property") or d.get("name") or "").lower()
            if k and d.get("content"):
                self.meta.setdefault(k, d["content"])
        elif tag == "title":
            self._t = True
        elif tag == "img" and not self.first_img and d.get("src", "").startswith("http"):
            self.first_img = d["src"]
        elif tag == "script" and (d.get("type") or "").lower() == "application/ld+json":
            self._ld, self._buf = True, []

    def handle_endtag(self, tag):
        if tag == "title":
            self._t = False
        elif tag == "script" and self._ld:
            self._ld = False
            self.ld.append("".join(self._buf))

    def handle_data(self, data):
        if self._t:
            self.title += data
        if self._ld:
            self._buf.append(data)


def _ld_author(blobs: list[str]) -> str:
    def name(v):
        if isinstance(v, list):
            return name(v[0]) if v else ""
        if isinstance(v, dict):
            return v.get("name", "")
        return v if isinstance(v, str) else ""

    def walk(o):
        if isinstance(o, list):
            for x in o:
                r = walk(x)
                if r:
                    return r
            return ""
        if isinstance(o, dict):
            return name(o.get("creator")) or name(o.get("author")) or walk(o.get("@graph"))
        return ""

    for b in blobs:
        try:
            a = walk(json.loads(b))
            if a:
                return a
        except Exception:
            continue
    return ""


def pinterest_variants(img_url: str) -> list[str]:
    """Pour une image pinimg : d'abord l'original, puis les grandes tailles, puis l'URL donnée."""
    if not PINIMG.match(img_url):
        return [img_url]
    out = [PINIMG.sub(f"https://i.pinimg.com/{size}/", img_url) for size in ("originals", "1200x", "736x")]
    return list(dict.fromkeys(out + [img_url]))


def page_info(url: str) -> dict:
    raw, ctype, final = _get(url, MAX_PAGE)
    if ctype.startswith("image/"):
        return {"image_url": final, "direct": raw, "final_url": final}
    html = raw.decode("utf-8", errors="replace")
    p = _Meta()
    try:
        p.feed(html)
    except Exception:
        pass
    m = p.meta
    img = m.get("og:image:secure_url") or m.get("og:image") or m.get("twitter:image") or m.get("twitter:image:src") or p.first_img
    author = m.get("author") or m.get("article:author") or _ld_author(p.ld)
    if PIN_HOST.search(urlparse(final).hostname or "") and author.strip().lower() == "pinterest":
        author = ""
    return {
        "image_url": urljoin(final, img) if img else "",
        "title": (m.get("og:title") or p.title or "").strip()[:300],
        "site_name": (m.get("og:site_name") or "").strip()[:200],
        "author": author.strip()[:200],
        "alt": (m.get("og:image:alt") or m.get("og:description") or "").strip()[:500],
        "final_url": final,
    }


def fetch_image(img_url: str, referer: str = "") -> bytes:
    last = None
    for candidate in pinterest_variants(img_url):
        try:
            data, ctype, _ = _get(candidate, MAX_IMAGE, referer)
            if data[:4] in (b"\x89PNG", b"GIF8") or data[:3] == b"\xff\xd8\xff" or data[8:12] in (b"WEBP", b"avif", b"heic") or ctype.startswith("image/"):
                return data
            last = FetchError("ce n'est pas une image")
        except Exception as e:  # noqa: BLE001
            last = e
    raise last or FetchError("image introuvable")


def resolve(item: dict) -> tuple[bytes, dict]:
    """Pour un item 'fetch' : retourne (octets de l'image, infos de page)."""
    info: dict = {}
    img_url = item.get("image_url") or ""
    page = item.get("source_url") or ""
    if page and (not img_url or PIN_HOST.search(urlparse(page).hostname or "")):
        info = page_info(page)
        if info.get("direct"):
            return info.pop("direct"), info
        img_url = img_url or info.get("image_url", "")
        # Pinterest : l'image de la page est en 736px, on vise l'original
    if not img_url:
        raise FetchError("aucune image trouvée sur cette page")
    data = fetch_image(img_url, referer=page)
    info["image_url"] = img_url
    return data, info
