"""Images similaires, familles d'images et doublons.

Similarité = TF-IDF pondéré sur le classement (style, mouvement, ambiance, technique, tags, thèmes)
+ proximité de la couleur principale (Lab). Familles = communautés de Louvain dans le graphe des
plus proches voisins. Méthode adaptée de wiki-inspi (MIT, © 2026 Cynthia Ortu).
"""
from __future__ import annotations

import hashlib
import math
import threading
from collections import Counter, defaultdict

from . import colors, store

FIELDS = {"style": 3.0, "medium": 2.5, "movement": 2.5, "technique": 2.0, "themes": 2.0, "mood": 1.2,
          "tags": 1.0, "use_for": 0.3, "kind": 2.0}
K = 6            # voisins gardés pour construire les familles
MIN_SIM = 0.22   # en dessous, deux images ne sont pas reliées
COLOR_W = 0.25   # part de la couleur dans la ressemblance
_cache: dict = {"v": -1}
_lock = threading.Lock()


def _features(it: dict) -> dict[str, float]:
    m = it["meta"]
    f: dict[str, float] = {}
    for field, w in FIELDS.items():
        if field == "tags":
            vals = it["tags"]
        elif field == "medium":
            vals = [it.get("medium")]
        elif field == "kind":
            vals = [it.get("kind")] if it.get("kind") in ("video", "screenshot") else []
        else:
            vals = m.get(field) or []
        for t in vals:
            if t:
                k = f"{field}:{str(t).lower()}"
                f[k] = max(f.get(k, 0), w)
    return f


def _tfidf(feats: list[dict]) -> list[dict]:
    n = len(feats)
    df = Counter(k for f in feats for k in f)
    out = []
    for f in feats:
        v = {k: w * math.log((1 + n) / (1 + df[k])) for k, w in f.items() if df[k] > 1}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({k: x / norm for k, x in v.items()})
    return out


LABEL_FIELDS = ("style", "medium", "themes", "movement", "technique", "kind", "mood", "tags")


def _label(members, feats, df, n) -> str:
    """Nom de famille : les traits les plus caractéristiques (fréquents dans la famille, rares ailleurs)."""
    cnt: Counter = Counter()
    for i in members:
        for k in feats[i]:
            cnt[k] += 1
    size = len(members)
    scored = []
    for k, c in cnt.items():
        field = k.split(":", 1)[0]
        if field not in LABEL_FIELDS or c < max(2, size * 0.4):
            continue
        lift = (c / size) / ((df[k] / n) or 1)
        scored.append((lift * (c / size) * (1.3 if field in ("style", "themes", "medium") else 1), k))
    words: list[str] = []
    for _, k in sorted(scored, reverse=True):
        w = k.split(":", 1)[1].replace("-", " ")
        w = {"video": "vidéos", "screenshot": "captures d'écran"}.get(w, w)
        if w not in words and not any(w in x or x in w for x in words):
            words.append(w)
        if len(words) == 3:
            break
    return " · ".join(words) or "divers"


def _build() -> dict:
    items = [it for it in store.all_items() if it["status"] == "done" and it["type"] == "image"]
    ids = [it["id"] for it in items]
    feats = [_features(it) for it in items]
    vecs = _tfidf(feats)
    df = Counter(k for f in feats for k in f)
    pals = [it["meta"].get("palette") or [] for it in items]
    inv = defaultdict(list)
    for i, v in enumerate(vecs):
        for k, x in v.items():
            inv[k].append((i, x))
    nb: dict[str, list] = {}
    top: dict[int, list] = {}
    for i, v in enumerate(vecs):
        scores: dict[int, float] = defaultdict(float)
        for k, x in v.items():
            for j, y in inv[k]:
                if j != i:
                    scores[j] += x * y
        for j in list(scores):  # couleur : écart entre palettes complètes (pas seulement la couleur principale)
            cs = max(0.0, 1 - colors.palette_distance(pals[i], pals[j]) / 45) if pals[i] and pals[j] else 0.0
            scores[j] = (1 - COLOR_W) * scores[j] + COLOR_W * cs
        ranked = sorted(scores.items(), key=lambda t: -t[1])
        nb[ids[i]] = [(ids[j], round(s, 3)) for j, s in ranked[:30] if s > 0.08]
        top[i] = [(j, s) for j, s in ranked[:K] if s >= MIN_SIM]
    return {"v": store.version(), "nb": nb, "ids": ids, "top": top, "feats": feats, "df": df}


def _families(d: dict, exclude: set[str]) -> tuple[list, dict]:
    """Familles automatiques (Louvain sur les voisins MUTUELS), hors images rangées à la main."""
    ids, top = d["ids"], d["top"]
    keep = {i for i, iid in enumerate(ids) if iid not in exclude}
    neigh = {i: {j for j, _ in top.get(i, []) if j in keep} for i in keep}
    families, fam_of = [], {}
    if len(keep) < 4:
        return families, fam_of
    import networkx as nx
    g = nx.Graph()
    g.add_nodes_from(keep)
    for i in keep:
        for j, s in top.get(i, []):
            if j in keep and i in neigh.get(j, set()):  # voisins dans les deux sens : liens solides seulement
                g.add_edge(i, j, weight=s)
    comms = sorted(nx.community.louvain_communities(g, weight="weight", resolution=1.0, seed=7), key=len, reverse=True)
    n = len(ids)
    for c in comms:
        if len(c) < 3:
            continue
        fid = "a" + hashlib.sha1(",".join(sorted(ids[i] for i in c)).encode()).hexdigest()[:8]
        families.append({"id": fid, "label": _label(c, d["feats"], d["df"], n), "ids": [ids[i] for i in c], "auto": True})
        for i in c:
            fam_of[ids[i]] = fid
    return families, fam_of


# ---------------- familles faites à la main ----------------
FAM_FILE = store.META / "familles.json"


def manual() -> list[dict]:
    import json
    try:
        data = json.loads(FAM_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    live = {it["id"] for it in store.all_items()}
    for f in data:
        f["ids"] = [i for i in f.get("ids", []) if i in live]
    return data


def save_manual(fams: list[dict]) -> None:
    import json
    FAM_FILE.write_text(json.dumps(fams, ensure_ascii=False, indent=1), encoding="utf-8")
    store.bump()


def edit_families(action: str, fam_id: str = "", name: str = "", ids: list[str] | None = None) -> dict:
    import uuid
    fams = manual()
    ids = [i for i in (ids or []) if store.get(i)]
    by = {f["id"]: f for f in fams}
    if action == "create":
        f = {"id": "m" + uuid.uuid4().hex[:8], "name": (name or "Nouvelle famille").strip()[:60], "ids": list(dict.fromkeys(ids))}
        fams.insert(0, f)
    elif action in ("rename", "add", "remove", "delete"):
        f = by.get(fam_id)
        if not f:
            raise ValueError("Famille introuvable.")
        if action == "rename":
            f["name"] = (name or f["name"]).strip()[:60]
        elif action == "add":
            f["ids"] = list(dict.fromkeys(f["ids"] + ids))
        elif action == "remove":
            f["ids"] = [i for i in f["ids"] if i not in ids]
        else:
            fams = [x for x in fams if x["id"] != fam_id]
    else:
        raise ValueError("Action inconnue.")
    save_manual(fams)
    return {"ok": True, "families": fams}


def all_families() -> dict:
    d = data()
    mine = manual()
    placed = {i for f in mine for i in f["ids"]}
    auto, fam_of = _families(d, placed)
    for f in mine:
        for i in f["ids"]:
            fam_of.setdefault(i, f["id"])
    return {"manual": mine, "auto": auto, "fam_of": fam_of}


def data() -> dict:
    with _lock:
        if _cache["v"] != store.version():
            _cache.clear()
            _cache.update(_build())
        return _cache


def similar_to(ids: list[str], limit: int = 40) -> list[tuple[str, float]]:
    d = data()
    score: dict[str, float] = defaultdict(float)
    for i in ids:
        for j, s in d["nb"].get(i, []):
            if j not in ids:
                score[j] += s
    return sorted(score.items(), key=lambda t: -t[1])[:limit]


def duplicate_groups() -> list[dict]:
    """Même image : octets identiques (sha1) ou quasi identique (dHash ≤ 3 bits).
    La copie à garder en premier : classée, plus grande, plus ancienne."""
    items = [it for it in store.all_items() if it.get("image")]
    parent = {it["id"]: it["id"] for it in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_sha: dict[str, str] = {}
    exact = set()
    for it in items:
        sha = it.get("sha1")
        if not sha:
            p = store.VAULT / it["image"]
            sha = hashlib.sha1(p.read_bytes()).hexdigest() if p.exists() else None
        if sha and sha in by_sha:
            parent[find(it["id"])] = find(by_sha[sha])
            exact |= {it["id"], by_sha[sha]}
        elif sha:
            by_sha[sha] = it["id"]
    hashed = [(it["dhash"], it["id"]) for it in items if it.get("dhash")]
    for a in range(len(hashed)):
        for b in range(a + 1, len(hashed)):
            if store.hamming(hashed[a][0], hashed[b][0]) <= 3:
                parent[find(hashed[a][1])] = find(hashed[b][1])
    groups = defaultdict(list)
    for it in items:
        groups[find(it["id"])].append(it)
    out = []
    for g in groups.values():
        if len(g) < 2:
            continue
        g.sort(key=lambda it: (it["status"] != "done", -((it.get("width") or 0) * (it.get("height") or 0)), it["created"]))
        out.append({"ids": [it["id"] for it in g], "keep": g[0]["id"], "exact": all(it["id"] in exact for it in g)})
    return out
