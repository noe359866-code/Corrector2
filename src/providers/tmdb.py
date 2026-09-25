"""TMDB (necesita clave gratis): movie/series -> tmdb_id (+ imdb_id).

Caminos exactos: imdb->tmdb (/find), tmdb->imdb (/external_ids).
"""
from __future__ import annotations

from .. import matching
from .base import ProviderResult

SOURCE = "tmdb"
_BASE = "https://api.themoviedb.org/3"


def _year_of(date: str | None) -> int | None:
    try:
        return int((date or "")[:4]) if date else None
    except (TypeError, ValueError):
        return None


def parse_search(data: dict | None, query: str, year: int | None,
                 kind: str) -> list[ProviderResult]:
    """Parseo puro de /search/movie|tv."""
    out: list[ProviderResult] = []
    for item in (data or {}).get("results") or []:
        name = item.get("title" if kind == "movie" else "name") or ""
        if not name or not item.get("id"):
            continue
        y = _year_of(item.get("release_date" if kind == "movie" else "first_air_date"))
        out.append(ProviderResult(
            title=name, ids={"tmdb_id": item["id"]},
            confidence=matching.confidence(query, name, year, y),
            source=SOURCE, year=y))
    out.sort(key=lambda r: r.confidence, reverse=True)
    return out


def search(http, api_key: str, title: str, year: int | None = None,
           kind: str = "movie") -> list[ProviderResult]:
    kind = "tv" if kind in ("tv", "series", "anime") else "movie"
    if not api_key or not (title or "").strip():
        return []
    params = {"api_key": api_key, "query": title.strip(),
              "include_adult": "false", "language": "es-ES"}
    if year:
        params["year" if kind == "movie" else "first_air_date_year"] = year
    data = http.get_json(f"{_BASE}/search/{kind}", params=params)
    if not isinstance(data, dict):
        return []
    return parse_search(data, title, year, kind)


def external_ids(http, api_key: str, tmdb_id: int,
                 kind: str = "movie") -> dict:
    """tmdb -> imdb (/external_ids). {} si falla."""
    kind = "tv" if kind in ("tv", "series", "anime") else "movie"
    if not api_key or not tmdb_id:
        return {}
    data = http.get_json(f"{_BASE}/{kind}/{tmdb_id}/external_ids",
                         params={"api_key": api_key})
    if isinstance(data, dict) and data.get("imdb_id"):
        return {"imdb_id": data["imdb_id"]}
    return {}


def find_by_imdb(http, api_key: str, imdb_id: str) -> dict:
    """imdb -> tmdb (/find). {} si falla."""
    if not api_key or not imdb_id:
        return {}
    data = http.get_json(f"{_BASE}/find/{imdb_id}",
                         params={"api_key": api_key,
                                 "external_source": "imdb_id"})
    if not isinstance(data, dict):
        return {}
    for key in ("movie_results", "tv_results"):
        results = data.get(key) or []
        if results and results[0].get("id"):
            return {"tmdb_id": results[0]["id"]}
    return {}
