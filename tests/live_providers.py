#!/usr/bin/env python3
"""Consultas REALES a proveedores públicos (informativo, no bloquea).

En CI corre con continue-on-error: si un proveedor está caído (Jikan suele
dar 504), avisa pero no tumba el workflow. Con --strict sí falla.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.http_client import Http  # noqa: E402
from src.providers import anilist, imdb, jikan, kitsu, tvmaze  # noqa: E402

STRICT = "--strict" in sys.argv
fails: list[str] = []


def check(name, cond, detail=""):
    print(("  ✔ " if cond else "  ✖ ") + name
          + (f" ({detail})" if detail and not cond else ""), flush=True)
    if not cond:
        fails.append(name)


http = Http()
print("live_providers (red real)...", flush=True)

for title, a_id, k_id, m_id in [
        ("Jujutsu Kaisen", 113415, 42765, 40748),
        ("Frieren", 154587, 46474, 52991)]:
    r = anilist.search(http, title)
    check(f"anilist {title}",
          bool(r) and r[0].ids.get("anilist_id") == a_id
          and r[0].ids.get("mal_id") == m_id
          and r[0].confidence >= 0.82,
          str(r[0].ids) if r else "sin respuesta")
    r = kitsu.search(http, title)
    check(f"kitsu {title}",
          bool(r) and r[0].ids.get("kitsu_id") == k_id
          and r[0].confidence >= 0.82,
          str(r[0].ids) if r else "sin respuesta")

r = jikan.search(http, "Frieren")
if not r:
    print("  ~ jikan caído o sin respuesta (normal: suele dar 504)",
          flush=True)
else:
    check("jikan Frieren", r[0].ids.get("mal_id") == 52991,
          str(r[0].ids))

for title, year, tt in [("The Matrix", 1999, "tt0133093"),
                        ("Breaking Bad", None, "tt0903747"),
                        ("Dune Part Two", 2024, "tt15239678")]:
    r = imdb.search(http, title, year)
    check(f"imdb {title}",
          bool(r) and r[0].ids.get("imdb_id") == tt
          and r[0].confidence >= 0.82,
          str(r[0].ids) if r else "sin respuesta")

r = tvmaze.search(http, "Breaking Bad")
check("tvmaze Breaking Bad",
      bool(r) and r[0].ids.get("imdb_id") == "tt0903747",
      str(r[0].ids) if r else "sin respuesta")

http.close()
print(f"live: {0 if fails else 'todo'} "
      f"({len(fails)} fallos, strict={STRICT})", flush=True)
sys.exit(1 if fails and STRICT else 0)
