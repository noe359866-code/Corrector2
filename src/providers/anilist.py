"""AniList (SIN clave, GraphQL): anime -> anilist_id + mal_id (idMal)."""
from __future__ import annotations

from .. import matching
from .base import ProviderResult

SOURCE = "anilist"

_QUERY = """query ($search: String) {
  Page(perPage: 8) { media(search: $search, type: ANIME) {
    id idMal seasonYear title { romaji english native } } } }"""


def parse_response(data: dict | None, query: str,
                   year: int | None = None) -> list[ProviderResult]:
    """Parseo puro de la respuesta GraphQL."""
    try:
        media = (data or {})["data"]["Page"]["media"] or []
    except (KeyError, TypeError):
        return []
    out: list[ProviderResult] = []
    for m in media:
        titles = (m.get("title") or {})
        names = [t for t in (titles.get("romaji"), titles.get("english"))
                 if t]
        if not names or not m.get("id"):
            continue
        y = m.get("seasonYear")
        best = max(names, key=lambda n: matching.confidence(query, n, year, y))
        ids = {"anilist_id": m["id"]}
        if m.get("idMal"):
            ids["mal_id"] = m["idMal"]
        out.append(ProviderResult(
            title=best, ids=ids,
            confidence=matching.confidence(query, best, year, y),
            source=SOURCE, year=y))
    out.sort(key=lambda r: r.confidence, reverse=True)
    return out


def search(http, title: str, year: int | None = None) -> list[ProviderResult]:
    if not (title or "").strip():
        return []
    data = http.post_json("https://graphql.anilist.co",
                          {"query": _QUERY,
                           "variables": {"search": title.strip()}})
    if not isinstance(data, dict):
        return []
    return parse_response(data, title, year)
