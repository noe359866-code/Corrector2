"""OMDb (necesita clave gratis, 1000/día): respaldo para tmdb/imdb."""
from __future__ import annotations

from .. import matching
from .base import ProviderResult

SOURCE = "omdb"


def parse_item(data: dict | None, query: str,
               year: int | None = None) -> ProviderResult | None:
    """Parseo puro de ?t=<titulo> (None si no hay match)."""
    if not isinstance(data, dict) or data.get("Response") != "True":
        return None
    name = data.get("Title") or ""
    if not name:
        return None
    try:
        y = int((data.get("Year") or "")[:4])
    except (TypeError, ValueError):
        y = None
    ids: dict = {}
    if (data.get("imdbID") or "").startswith("tt"):
        ids["imdb_id"] = data["imdbID"]
    return ProviderResult(
        title=name, ids=ids,
        confidence=matching.confidence(query, name, year, y),
        source=SOURCE, year=y)


def search(http, api_key: str, title: str, year: int | None = None,
           media_type: str | None = None) -> ProviderResult | None:
    if not api_key or not (title or "").strip():
        return None
    params = {"apikey": api_key, "t": title.strip()}
    if year:
        params["y"] = year
    if media_type in ("movie", "series"):
        params["type"] = media_type
    data = http.get_json("https://www.omdbapi.com/", params=params)
    return parse_item(data if isinstance(data, dict) else None, title, year)
