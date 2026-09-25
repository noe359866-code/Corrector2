"""Base de proveedores: resultado común + clave de caché."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..textnorm import normalize


@dataclass
class ProviderResult:
    title: str
    ids: dict = field(default_factory=dict)  # tmdb_id, imdb_id, anilist_id...
    confidence: float = 0.0
    source: str = ""
    year: int | None = None


def cache_key(title: str, year: int | None, media_type: str | None) -> str:
    """Clave estable título+año+tipo ('frieren|2023|anime')."""
    return f"{normalize(title)}|{year or ''}|{(media_type or '').lower()}"
