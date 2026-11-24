"""Couleurs (distance perceptuelle Lab) et filtres de la galerie.

Les règles de filtres et le calcul Lab sont adaptés de wiki-inspi (MIT, © 2026 Cynthia Ortu).
"""
from __future__ import annotations

import colorsys
import math
import re

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

NAMED = {
    "noir": "#000000", "black": "#000000", "blanc": "#ffffff", "white": "#ffffff",
    "gris": "#808080", "grey": "#808080", "gray": "#808080", "rouge": "#d62828", "red": "#d62828",
    "orange": "#f77f00", "jaune": "#fcbf49", "yellow": "#fcbf49", "vert": "#2a9d8f", "green": "#2a9d8f",
    "bleu": "#1d4ed8", "blue": "#1d4ed8", "marine": "#1e3a8a", "navy": "#1e3a8a", "cyan": "#22d3ee",
    "turquoise": "#14b8a6", "violet": "#7c3aed", "purple": "#7c3aed", "rose": "#ec4899", "pink": "#ec4899",
    "magenta": "#d946ef", "brun": "#7f5539", "marron": "#7f5539", "brown": "#7f5539", "beige": "#e9d8a6",
    "creme": "#f5f0e1", "crème": "#f5f0e1", "or": "#d4af37", "gold": "#d4af37", "kaki": "#6b705c",
}


def norm_hex(v) -> str | None:
    m = HEX_RE.match(str(v or "").strip())
    return f"#{m.group(1).upper()}" if m else None


def parse_color(v: str) -> str | None:
    v = (v or "").strip().lower()
    return norm_hex(v) or NAMED.get(v)


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lab(hexc: str) -> tuple[float, float, float] | None:
    h = norm_hex(hexc)
    if not h:
        return None
    r, g, b = (_lin(int(h[i:i + 2], 16) / 255) for i in (1, 3, 5))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def distance(a: str, b: str) -> float:
    la, lb = lab(a), lab(b)
    return math.dist(la, lb) if la and lb else 999.0


def best_distance(palette: list, main: str | None, target: str) -> float | None:
    """Plus petite distance entre la couleur cherchée et la palette (pénalise les couleurs marginales)."""
    best = None
    for p in palette or []:
        hx = p.get("hex") if isinstance(p, dict) else p
        share = p.get("share", 0.2) if isinstance(p, dict) else 0.2
        d = distance(hx, target) + (1 - min(max(float(share or 0), 0), 1)) * 8
        best = d if best is None else min(best, d)
    if main:
        d = distance(main, target)
        best = d if best is None else min(best, d)
    return best


def color_name(hexc) -> str | None:
    h = norm_hex(hexc)
    if not h:
        return None
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hue, l, s = colorsys.rgb_to_hls(r, g, b)
    hue *= 360
    if s < 0.18 or l < 0.08 or l > 0.94:
        return "Noir" if l < 0.22 else "Blanc & crème" if l > 0.82 else "Gris"
    if (hue < 40 or hue >= 345) and l < 0.32 and s < 0.6:
        return "Brun"
    if hue < 15 or hue >= 345:
        return "Rouge"
    if hue < 40:
        return "Brun" if l < 0.35 else "Beige" if s < 0.45 and l > 0.7 else "Orange"
    if hue < 65:
        return "Jaune"
    if hue < 165:
        return "Vert"
    if hue < 255:
        return "Bleu"
    if hue < 290:
        return "Violet"
    return "Rose"


# ---------------- filtres (règles par mots-clés sur ce que le modèle a écrit) ----------------
TYPE = {
    "Photographie": r"photo|35mm|argentique|film-grain|grain|long-exposure|macro|objectif|depth-of-field",
    "Cinématique": r"cinema|film-still|movie|anamorph|widescreen|plan-de-film",
    "Illustration": r"illustrat|dessin|drawing|comic|bd|sketch|croquis|line-art|trait",
    "Peinture": r"peint|paint|aquarelle|watercolo|gouache|huile|oil-|acryli|brush",
    "Graphisme & typo": r"affiche|poster|typo|swiss|suisse|editorial|magazine|layout|mise-en-page|grille|grid|logo|brand|lettering|signaletique",
    "Collage & mix média": r"collage|mix|photomontage|assemblage|decoupage|cut-out",
    "3D": r"(^|[^a-z0-9])3d|render|rendu|cgi|blender|octane|cinema4d|c4d|low-poly",
    "Motion & VJ": r"motion|kinetic|animation|vj|glitch|feedback|generatif|generative|touchdesigner|particule",
    "Interface": r"(^|[^a-z])(ui|ux)([^a-z]|$)|interface|dashboard|site|website|app-|ecran|screen|bento",
    "Sculpture & installation": r"sculpt|installation|scenograph|ceramique|textile|broderie",
}
TECHNIQUE = {
    "Encre": r"encre|ink",
    "Aquarelle": r"aquarelle|watercolo",
    "Gouache": r"gouache",
    "Crayon & dessin": r"crayon|graphite|fusain|charcoal|pencil",
    "Collage": r"collage|photomontage|decoupage",
    "Risographie": r"riso",
    "Sérigraphie": r"serigraph|screen-print|silkscreen",
    "Gravure": r"gravure|etching|woodcut|linocut|lithograph",
    "Trame & halftone": r"halftone|trame|ben-day",
    "Argentique": r"argentique|35mm|analog|film-grain|polaroid|moyen-format",
    "Vectoriel": r"vector|flat",
    "Rendu 3D": r"(^|[^a-z0-9])3d|render|rendu|cgi",
    "Dégradé": r"degrade|gradient",
    "Glitch & feedback": r"glitch|feedback|datamosh|displacement",
    "Pixel art": r"pixel",
    "Généré par IA": r"ia-|ai-generated|midjourney|diffusion",
}
SUBJECT = {
    "Abstrait": r"abstra|non-figuratif",
    "Géométrique": r"geometr|grille|grid|cercle|carre|triangle|forme",
    "Formes organiques": r"organi|fluide|fluid|blob|courbe",
    "Personne": r"(personne|portrait|visage|femme|homme|corps|figure|foule|danseu|enfant|person|people|face)",
    "Mode": r"mode|fashion|vetement|tenue|maquillage|beaute",
    "Paysage & nature": r"(paysage|nature|mer|ocean|foret|montagne|ciel|eau|arbre|plage|desert|lac|riviere|fleur)",
    "Architecture & intérieur": r"architect|batiment|interieur|piece|facade|maison|escalier|rue|ville|urbain",
    "Objet & nature morte": r"nature-morte|objet|produit|bouteille|vase|fruit|packaging",
    "Typographie": r"typo|lettre|lettrage|font|mot",
    "Animal": r"(animal|oiseau|chien|chat|cheval|poisson|insecte|papillon)",
    "Voiture & véhicule": r"voiture|vehicule|moto|car-",
    "Noir et blanc": r"noir-et-blanc|monochrome|niveaux-de-gris",
}
_RX = {k: {n: re.compile(p) for n, p in d.items()} for k, d in (("type", TYPE), ("technique", TECHNIQUE), ("sujet", SUBJECT))}
TEMP = {"chaud": "Chaud", "froid": "Froid", "neutre": "Neutre"}


def _tokens(it: dict) -> str:
    m = it.get("meta") or {}
    parts: list[str] = []
    for k in ("tags",):
        parts += it.get(k) or []
    for k in ("style", "movement", "technique", "mood", "themes"):
        v = m.get(k)
        parts += v if isinstance(v, list) else [v] if v else []
    for k in ("medium", "style", "mood"):
        if it.get(k):
            parts.append(it[k])
    for k in ("subject", "medium_detail", "typography"):
        if m.get(k):
            parts.append(re.sub(r"\s+", "-", str(m[k]).lower()))
    import unicodedata
    s = " ".join(str(p).lower() for p in parts)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def facets(it: dict) -> dict[str, list[str]]:
    text = _tokens(it)
    out = {k: [n for n, rx in rules.items() if rx.search(text)] for k, rules in _RX.items()}
    m = it.get("meta") or {}
    c = color_name(m.get("main_color") or (it.get("palette") or [None])[0])
    out["couleur"] = [c] if c else []
    t = TEMP.get(str(m.get("color_temperature") or "").lower())
    out["température"] = [t] if t else []
    out["usage"] = list(m.get("use_for") or [])
    return out
