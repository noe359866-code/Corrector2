"""Kitsu (SIN clave): anime -> kitsu_id (+ mal/anilist vía mappings)."""
from __future__ import annotations

from .. import matching
from .base import ProviderResult

SOURCE = "kitsu"
_HEADERS = {"Accept": "application/vnd.api+json"}


def _year_of(start: str | None) -> int | None:
    try:
        return int((start or "")[:4]) if start else None
    except (TypeError, ValueError):
        return None


def parse_search(data: dict | None, query: str,
                 year: int | None = None) -> list[ProviderResult]:
    """Parseo puro de /anime?filter[text]=..."""
    out: list[ProviderResult] = []
    for item in (data or {}).get("data") or []:
        attr = item.get("attributes") or {}
        titles = attr.get("titles") or {}
        names = [t for t in (attr.get("canonicalTitle"), titles.get("en"),
                             titles.get("en_us")) if t]
        if not names or not item.get("id"):
            continue
        y = _year_of(attr.get("startDate"))
        best = max(names, key=lambda n: matching.confidence(query, n, year, y))
        try:
            kid = int(item["id"])
        except (TypeError, ValueError):
            continue
        out.append(ProviderResult(
            title=best, ids={"kitsu_id": kid},
            confidence=matching.confidence(query, best, year, y),
            source=SOURCE, year=y))
    out.sort(key=lambda r: r.confidence, reverse=True)
    return out


def parse_mappings(data: dict | None) -> dict:
    """Parseo puro de /anime/{id}/mappings -> {mal_id, anilist_id}."""
    ids: dict = {}
    for item in (data or {}).get("data") or []:
        attr = item.get("attributes") or {}
        site = (attr.get("externalSite") or "").lower().replace(" ", "")
        try:
            ext = int(attr.get("externalId"))
        except (TypeError, ValueError):
            continue
        if "myanimelist" in site and "mal_id" not in ids:
            ids["mal_id"] = ext
        elif "anilist" in site and "anilist_id" not in ids:
            ids["anilist_id"] = ext
    return ids


def search(http, title: str, year: int | None = None,
           with_mappings: bool = True) -> list[ProviderResult]:
    if not (title or "").strip():
        return []
    data = http.get_json(
        "https://kitsu.io/api/edge/anime",
        params={"filter[text]": title.strip(), "page[limit]": 6,
                "page[offset]": 0},
        headers=_HEADERS)
    if not isinstance(data, dict):
        return []
    results = parse_search(data, title, year)
    if with_mappings and results and results[0].confidence >= 0.5:
        kid = results[0].ids.get("kitsu_id")
        maps = http.get_json(
            f"https://kitsu.io/api/edge/anime/{kid}/mappings",
            params={"page[limit]": 20}, headers=_HEADERS)
        if isinstance(maps, dict):
            results[0].ids.update(parse_mappings(maps))
    return results
