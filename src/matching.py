"""Confianza de matches (rapidfuzz) + variantes de búsqueda.

Umbrales: acepta >= 0.82 (>= 0.90 en variantes laxas). Un match conseguido
con variante sintética vale x0.8 (PENALIZACION_VARIANTE): un ID dudoso ya
no puede borrar nada.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from .textnorm import normalize

ACEPTAR = 0.82
VARIANTE_LAXA = 0.90
PENALIZACION_VARIANTE = 0.8
CONFIABLE = 0.92  # igual que sql torrent_id_es_confiable()

_ARTICLES = ("the ", "a ", "an ", "el ", "la ", "los ", "las ", "un ", "una ")
_ROMAN = {"II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7",
          "VIII": "8", "IX": "9", "X": "10"}
_SEASON_CUT = re.compile(r"\s*(S\d{1,2}E\d{1,3}|\d{1,2}x\d{1,3}|season\s+\d+|temporada\s+\d+).*$", re.I)


def similarity(a: str, b: str) -> float:
    """Similitud 0..1 (token_set + partial sobre texto normalizado)."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return (fuzz.token_set_ratio(na, nb) + fuzz.partial_ratio(na, nb)) / 200.0


def confidence(query: str, candidate: str,
               year_q: int | None = None, year_c: int | None = None,
               variant: bool = False) -> float:
    """Confianza 0..1: similitud + bonus por año exacto (x0.8 si variante)."""
    s = similarity(query, candidate)
    if year_q and year_c:
        if int(year_q) == int(year_c):
            s += 0.04
        else:
            s -= 0.10
    if variant:
        s *= PENALIZACION_VARIANTE
    return round(min(1.0, max(0.0, s)), 4)


def accepted(conf: float, lax: bool = False) -> bool:
    """¿El match es bastante bueno? (lax = variante recortada, exige más)."""
    return conf >= (VARIANTE_LAXA if lax else ACEPTAR)


def variants(title: str) -> list[tuple[str, bool]]:
    """Variantes de búsqueda: (texto, es_laxa). La primera es el original."""
    t = re.sub(r"\s+", " ", (title or "").strip())
    if not t:
        return []
    out: list[tuple[str, bool]] = [(t, False)]
    seen = {normalize(t)}

    def add(v: str, lax: bool):
        v = re.sub(r"\s+", " ", v.strip(" -:"))
        if v and normalize(v) not in seen:
            seen.add(normalize(v))
            out.append((v, lax))

    # sin coletilla ': ...'
    if ":" in t:
        add(t.split(":", 1)[0], False)
    # sin artículo inicial
    low = t.lower()
    for art in _ARTICLES:
        if low.startswith(art):
            add(t[len(art):], False)
            break
    # recortes por palabras (laxos: exigen más confianza)
    words = t.split()
    for n in (4, 3, 2):
        if len(words) > n:
            add(" ".join(words[:n]), True)
    return out


def aggressive_variants(title: str) -> list[str]:
    """Variantes agresivas para el fallback (roman->arábigo, corta S01E02...)."""
    t = re.sub(r"\s+", " ", (title or "").strip())
    if not t:
        return []
    out: list[str] = []
    seen = {normalize(t)}

    def add(v: str):
        v = re.sub(r"\s+", " ", v.strip(" -:"))
        if v and normalize(v) not in seen:
            seen.add(normalize(v))
            out.append(v)

    add(_SEASON_CUT.sub("", t))
    roman = t
    for k, v in _ROMAN.items():
        roman = re.sub(r"\b%s\b" % k, v, roman)
    add(roman)
    add(re.sub(r"\b(19\d{2}|20\d{2})\b", "", t))
    words = t.split()
    for n in (5, 4, 3, 2):
        if len(words) > n:
            add(" ".join(words[:n]))
    return out
