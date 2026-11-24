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

FIELDS = {"style": 3.0, "movement": 2.5, "mood": 2.0, "technique": 2.0, "themes": 1.5, "tags": 1.5,
          "medium": 1.0, "use_for": 0.5}
K = 8
_cache: dict = {"v": -1}
_lock = threading.Lock()


def _features(it: dict) -> dict[str, float]:
    m = it["meta"]
    f: dict[str, float] = {}
    for field, w in FIELDS.items():
        vals = it["tags"] if field == "tags" else [it.get("medium")] if field == "medium" else m.get(field) or []
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


def _label(members, vecs) -> str:
    score: Counter = Counter()
    for i in members:
        for k, x in vecs[i].items():
            if not k.startswith(("use_for:", "medium:")):
                score[k] += x
    words: list[str] = []
    for k, _ in score.most_common(12):
        w = k.split(":", 1)[1].replace("-", " ")
        if w not in words:
            words.append(w)
        if len(words) == 3:
            break
    return " · ".join(words) or "divers"


def _build() -> dict:
    items = [it for it in store.all_items() if it["status"] == "done" and it["type"] == "image"]
    ids = [it["id"] for it in items]
    vecs = _tfidf([_features(it) for it in items])
    labs = [colors.lab(it["meta"].get("main_color") or "") for it in items]
    inv = defaultdict(list)
    for i, v in enumerate(vecs):
        for k, x in v.items():
            inv[k].append((i, x))
    nb: dict[str, list] = {}
    edges = []
    for i, v in enumerate(vecs):
        scores: dict[int, float] = defaultdict(float)
        for k, x in v.items():
            for j, y in inv[k]:
                if j != i:
                    scores[j] += x * y
        if labs[i]:
            for j in list(scores):
                if labs[j]:
                    scores[j] += 0.15 * max(0.0, 1 - math.dist(labs[i], labs[j]) / 60)
        ranked = sorted(scores.items(), key=lambda t: -t[1])
        nb[ids[i]] = [(ids[j], round(s, 3)) for j, s in ranked[:30] if s > 0.05]
        edges += [(i, j, s) for j, s in ranked[:K] if s > 0.08]
    families, fam_of = [], {}
    if len(items) >= 4 and edges:
        import networkx as nx
        g = nx.Graph()
        g.add_nodes_from(range(len(items)))
        for a, b, s in edges:
            w = max(s, g[a][b]["weight"]) if g.has_edge(a, b) else s
            g.add_edge(a, b, weight=w)
        comms = sorted(nx.community.louvain_communities(g, weight="weight", resolution=1.2, seed=7), key=len, reverse=True)
        for fi, c in enumerate(comms):
            if len(c) < 3:
                continue
            fam = {"id": fi, "label": _label(c, vecs), "ids": [ids[i] for i in c]}
            families.append(fam)
            for i in c:
                fam_of[ids[i]] = fi
    return {"v": store.version(), "nb": nb, "families": families, "fam_of": fam_of}


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
