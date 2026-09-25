"""Jikan/MAL (SIN clave): tercer respaldo para el mal_id.

Jikan suele dar 504 (fallo suyo): por eso el mal_id sale primero de
AniList.idMal y de los mappings de Kitsu. Auto-limitado a ~2.4 req/s.
"""
from __future__ import annotations

from .. import matching
from .base import ProviderResult

SOURCE = "jikan"


def parse_search(data: dict | None, query: str,
                 year: int | None = None) -> list[ProviderResult]:
    """Parseo puro de /v4/anime."""
    out: list[ProviderResult] = []
    for item in (data or {}).get("data") or []:
        names = [t for t in (item.get("title_english"), item.get("title"))
                 if t]
        if not names or not item.get("mal_id"):
            continue
        y = item.get("year")
        best = max(names, key=lambda n: matching.confidence(query, n, year, y))
        try:
            mid = int(item["mal_id"])
        except (TypeError, ValueError):
            continue
        out.append(ProviderResult(
            title=best, ids={"mal_id": mid},
            confidence=matching.confidence(query, best, year, y),
            source=SOURCE, year=y if isinstance(y, int) else None))
    out.sort(key=lambda r: r.confidence, reverse=True)
    return out


def search(http, title: str, year: int | None = None) -> list[ProviderResult]:
    if not (title or "").strip():
        return []
    data = http.get_json("https://api.jikan.moe/v4/anime",
                         params={"q": title.strip(), "limit": 5,
                                 "order_by": "members", "sort": "desc"})
    if not isinstance(data, dict):
        return []
    return parse_search(data, title, year)
