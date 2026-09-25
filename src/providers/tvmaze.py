"""TVmaze (SIN clave): series -> imdb_id (+ thetvdb de regalo)."""
from __future__ import annotations

from .. import matching
from .base import ProviderResult

SOURCE = "tvmaze"


def _year_of(premiered: str | None) -> int | None:
    try:
        return int((premiered or "")[:4]) if premiered else None
    except (TypeError, ValueError):
        return None


def parse_search(data: list | None, query: str,
                 year: int | None = None) -> list[ProviderResult]:
    """Parseo puro de /search/shows."""
    out: list[ProviderResult] = []
    for item in data or []:
        show = (item or {}).get("show") or {}
        name = show.get("name") or ""
        if not name:
            continue
        y = _year_of(show.get("premiered"))
        ext = show.get("externals") or {}
        ids: dict = {}
        if ext.get("imdb"):
            ids["imdb_id"] = ext["imdb"]
        if ext.get("tvdb"):
            ids["thetvdb"] = ext["tvdb"]
        out.append(ProviderResult(
            title=name, ids=ids,
            confidence=matching.confidence(query, name, year, y),
            source=SOURCE, year=y))
    out.sort(key=lambda r: r.confidence, reverse=True)
    return out


def search(http, title: str, year: int | None = None) -> list[ProviderResult]:
    if not (title or "").strip():
        return []
    data = http.get_json("https://api.tvmaze.com/search/shows",
                         params={"q": title.strip()})
    if not isinstance(data, list):
        return []
    return parse_search(data, title, year)
