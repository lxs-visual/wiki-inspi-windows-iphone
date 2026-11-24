"""Jeton d'appairage : stocké dans le Gestionnaire d'identifiants Windows (keyring),
avec repli sur un fichier si keyring n'est pas disponible."""
from __future__ import annotations

import secrets
import socket

from .config import ROOT

SERVICE = "inspi-local"
_FALLBACK = ROOT / ".token"
_token: str | None = None


def _kr():
    try:
        import keyring  # type: ignore
        keyring.get_password(SERVICE, "probe")
        return keyring
    except Exception:
        return None


def get_token() -> str:
    global _token
    if _token:
        return _token
    kr = _kr()
    tok = None
    if kr:
        tok = kr.get_password(SERVICE, "token")
    elif _FALLBACK.exists():
        tok = _FALLBACK.read_text().strip()
    if not tok:
        tok = reset_token()
    _token = tok
    return tok


def reset_token() -> str:
    """Génère un nouveau jeton (les anciens appareils devront être réappairés)."""
    global _token
    tok = secrets.token_urlsafe(24)
    kr = _kr()
    if kr:
        kr.set_password(SERVICE, "token", tok)
    else:
        _FALLBACK.write_text(tok)
    _token = tok
    return tok


def check(candidate: str | None) -> bool:
    return bool(candidate) and secrets.compare_digest(candidate, get_token())


def lan_ip() -> str:
    """Adresse du PC sur le réseau local (aucun paquet n'est réellement envoyé)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()
