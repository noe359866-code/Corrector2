"""Normalización de texto, idéntica a sql norm_torrent_text().

minúsculas → sin acentos → solo [a-z0-9] separados por un espacio.
Si cambias esto, cambia también sql/001_helpers.sql (y al revés).
"""
from __future__ import annotations

import re
import unicodedata

_WS_RUN = re.compile(r"[^a-z0-9]+")


def normalize(text: object) -> str:
    """'Frieren: Beyond...' -> 'frieren beyond...' ('' si vacío)."""
    if text is None:
        return ""
    t = unicodedata.normalize("NFKD", str(text).lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return _WS_RUN.sub(" ", t).strip()


def title_key(text: object) -> str | None:
    """Clave de agrupación: normalizado, o None si no hay nada."""
    return normalize(text) or None


def token_ok(text: object, token: object) -> bool:
    """¿Aparece el token con límite de palabra? ('anal' no caza 'analytics')."""
    t, k = normalize(text), normalize(token)
    if not t or not k:
        return False
    return f" {k} " in f" {t} "
