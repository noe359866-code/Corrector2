"""IMDb suggestions (SIN clave): el mismo buscador de imdb.com.

Verificado: The.Matrix -> tt0133093, Breaking.Bad -> tt0903747,
Dune.Part.Two -> tt15239678, Frieren -> tt22248376.
"""
from __future__ import annotations

from urllib.parse import quote

from .. import matching
from .base import ProviderResult

SOURCE = "imdb:suggest"


def parse_suggestions(data: dict | None, query: str,
                      year: int | None = None) -> list[ProviderResult]:
    """Parseo puro (offline-testeable) de /suggestion/x/<q>.json."""
    items = (data or {}).get("d") or []
    out: list[ProviderResult] = []
    for it in items:
        imdb_id = it.get("id") or ""
        title = it.get("l") or ""
        if not imdb_id.startswith("tt") or not title:
            continue
        y = it.get("y")
        try:
            y = int(y) if y else None
        except (TypeError, ValueError):
            y = None
        out.append(ProviderResult(
            title=title, ids={"imdb_id": imdb_id},
            confidence=matching.confidence(query, title, year, y),
            source=SOURCE, year=y))
    out.sort(key=lambda r: r.confidence, reverse=True)
    return out


def search(http, title: str, year: int | None = None) -> list[ProviderResult]:
    """Busca en IMDb suggestions. [] si falla la red."""
    if not (title or "").strip():
        return []
    url = f"https://v3.sg.media-imdb.com/suggestion/x/{quote(title.strip())}.json"
    data = http.get_json(url, params={"includeVideos": "0"})
    if not isinstance(data, dict):
        return []
    return parse_suggestions(data, title, year)
