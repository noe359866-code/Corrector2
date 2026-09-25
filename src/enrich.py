"""Motor de resolución de IDs por nombre.

Camino feliz:
    movie/series -> TMDB -> imdb (/external_ids) -> OMDb (respaldo)
    anime        -> AniList -> idMal -> Kitsu (+mappings) -> Jikan/MAL
    siempre      -> IMDb suggestions (sin clave) -> imdb_id -> TMDB /find
Fallback agresivo: otro nombre, variantes, romanos, TVMaze, tipo cruzado...
"""
from __future__ import annotations

import datetime
import logging
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from . import matching
from .providers import anilist, imdb, jikan, kitsu, omdb, tmdb, tvmaze
from .providers.base import ProviderResult, cache_key
from .titleparse import parse_release, pick_release_name

log = logging.getLogger("enrich")

ID_FIELDS = ("tmdb_id", "imdb_id", "anilist_id", "kitsu_id", "mal_id")
MISS_MAX_AGE_DAYS = 20

# qué IDs se esperan por tipo (para resuelta/parcial/sin_match)
EXPECTED = {
    "movie": ("tmdb_id", "imdb_id"),
    "series": ("tmdb_id", "imdb_id"),
    "anime": ("anilist_id", "kitsu_id", "mal_id", "imdb_id"),
}


# ---------------------------------------------------------------------------
# Saneo de IDs (espejo de sql apply_torrent_ids: nunca tumba una fila)
# ---------------------------------------------------------------------------
def sanitize_ids(ids: dict) -> tuple[dict, int, list[str]]:
    """(limpios, nº_inválidos, campos_inválidos)."""
    clean: dict = {}
    invalid = 0
    fields: list[str] = []
    for f in ("tmdb_id", "anilist_id", "kitsu_id", "mal_id"):
        v = (ids or {}).get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        if isinstance(v, bool):
            invalid += 1
            fields.append(f)
        elif isinstance(v, int) and 1 <= v <= 2147483647:
            clean[f] = v
        elif isinstance(v, str) and v.strip().isdigit() \
                and 1 <= int(v.strip()) <= 2147483647:
            clean[f] = int(v.strip())
        else:
            invalid += 1
            fields.append(f)
    v = (ids or {}).get("imdb_id")
    if v is None or (isinstance(v, str) and not v.strip()):
        pass
    elif isinstance(v, str) and v.strip().lower().startswith("tt") \
            and v.strip()[2:].isdigit() and len(v.strip()) <= 12:
        clean["imdb_id"] = v.strip().lower()
    else:
        invalid += 1
        fields.append("imdb_id")
    return clean, invalid, fields


def new_stats() -> dict:
    return {p: {"calls": 0, "hits": 0, "misses": 0, "errors": 0,
                "cache_hits": 0}
            for p in ("anilist", "kitsu", "jikan", "imdb", "tvmaze",
                      "tmdb", "omdb")}


class Ctx(SimpleNamespace):
    """cfg, http, local, persistent, stats."""


# ---------------------------------------------------------------------------
# Resolución de UNA fila
# ---------------------------------------------------------------------------
def resolve_row(row: dict, ctx: Ctx) -> dict:
    cfg, http = ctx.cfg, ctx.http
    primary, backup = pick_release_name(row.get("title"), row.get("title_alt"))
    parsed = parse_release(primary or "")
    mtype = (row.get("type") or "").lower() or None
    if mtype not in ("movie", "series", "anime"):
        mtype = None
    year = parsed["year"]
    clean_title = parsed["title"] or primary or ""

    keys = []
    for name in (clean_title, primary, backup):
        if name:
            k = cache_key(name, year, mtype)
            if k not in keys:
                keys.append(k)

    ids: dict = {}
    sources: list[str] = []
    confs: list[float] = []
    called: set[tuple[str, str]] = set()
    used_fallback = False

    def _consider(res: ProviderResult | None, provider: str,
                  variant: bool = False, lax: bool = False) -> bool:
        if res is None:
            ctx.stats[provider]["misses"] += 1
            return False
        conf = res.confidence * (matching.PENALIZACION_VARIANTE
                                 if variant else 1.0)
        if not matching.accepted(conf, lax):
            ctx.stats[provider]["misses"] += 1
            return False
        clean, _, _ = sanitize_ids(res.ids)
        new = {k: v for k, v in clean.items() if k not in ids}
        if new:
            ids.update(new)
            ctx.stats[provider]["hits"] += 1
            if res.source not in sources:
                sources.append(res.source)
            confs.append(round(conf, 4))
            return True
        ctx.stats[provider]["cache_hits"] += 1
        return False

    def _call(provider: str, query: str, fn, *a, **kw):
        if not query or (provider, query) in called:
            return None
        called.add((provider, query))
        ctx.stats[provider]["calls"] += 1
        try:
            return fn(*a, **kw)
        except Exception as e:  # un proveedor no tumba la fila
            ctx.stats[provider]["errors"] += 1
            log.debug("%s(%s): %s", provider, query, e)
            return None

    def _best(results: list | None) -> ProviderResult | None:
        return results[0] if results else None

    # --- 1) cachés (local + persistente precargada) ---
    for k in keys:
        hit = ctx.local.get(k)
        if hit and hit.get("ids"):
            clean, _, _ = sanitize_ids(hit["ids"])
            if clean:
                ids.update({kk: vv for kk, vv in clean.items()
                            if kk not in ids})
                if hit.get("source") and hit["source"] not in sources:
                    sources.append(hit["source"])
                confs.append(float(hit.get("confidence") or 0.5))
        elif ctx.local.is_miss(k):
            pass
        else:
            prow = (ctx.persistent or {}).get(k)
            if prow:
                if not prow.get("found", True):
                    if _miss_fresh(prow.get("updated_at")):
                        continue  # miss reciente: ni se intenta
                else:
                    clean, _, _ = sanitize_ids(prow)
                    if clean:
                        ids.update({kk: vv for kk, vv in clean.items()
                                    if kk not in ids})
                        if prow.get("source") and \
                                prow["source"] not in sources:
                            sources.append(prow["source"])
                        confs.append(float(prow.get("confidence") or 0.5))
    if ids and _complete(ids, mtype, cfg.fill_all_ids):
        return _result(row, ids, sources, confs, keys, False, ctx)

    want_anime = mtype in ("anime", None)
    want_tmdb = mtype in ("movie", "series", None)

    def _round(query: str, variant: bool = False, lax: bool = False):
        if want_anime:
            _consider(_best(_call("anilist", query, anilist.search,
                                  http, query, year)), "anilist", variant, lax)
            _consider(_best(_call("kitsu", query, kitsu.search,
                                  http, query, year)), "kitsu", variant, lax)
            _consider(_best(_call("jikan", query, jikan.search,
                                  http, query, year)), "jikan", variant, lax)
        _consider(_best(_call("imdb", query, imdb.search, http, query, year)),
                  "imdb", variant, lax)
        if mtype in ("series", None):
            _consider(_best(_call("tvmaze", query, tvmaze.search,
                                  http, query, year)), "tvmaze", variant, lax)
        if want_tmdb and cfg.tmdb_api_key:
            kind = "tv" if mtype == "series" else "movie"
            r = _best(_call("tmdb", query, tmdb.search, http,
                            cfg.tmdb_api_key, query, year, kind))
            if _consider(r, "tmdb", variant, lax) and r:
                extra = _call("tmdb", query + "|ext", tmdb.external_ids,
                              http, cfg.tmdb_api_key,
                              r.ids.get("tmdb_id"), kind) or {}
                clean, _, _ = sanitize_ids(extra)
                for k, v in clean.items():
                    if k not in ids:
                        ids[k] = v
        if cfg.omdb_api_key:
            _consider(_call("omdb", query, omdb.search, http,
                            cfg.omdb_api_key, query, year,
                            "series" if mtype == "series" else "movie"),
                      "omdb", variant, lax)

    # --- 2) camino feliz ---
    if clean_title:
        _round(clean_title)
    if ids and _complete(ids, mtype, cfg.fill_all_ids):
        return _result(row, ids, sources, confs, keys, False, ctx)

    # --- 3) variantes del título ---
    for variant, lax in matching.variants(clean_title)[1:]:
        if _complete(ids, mtype, cfg.fill_all_ids):
            break
        used_fallback = True
        _round(variant, variant=True, lax=lax)
    if ids and _complete(ids, mtype, cfg.fill_all_ids):
        return _result(row, ids, sources, confs, keys, used_fallback, ctx)

    # --- 4) fallback agresivo ---
    if cfg.aggressive_fallback:
        used_fallback = True
        if backup and backup != clean_title:
            _round(backup)
        for variant in matching.aggressive_variants(clean_title):
            if _complete(ids, mtype, cfg.fill_all_ids):
                break
            _consider(_best(_call("imdb", variant, imdb.search,
                                  http, variant, year)),
                      "imdb", variant=True, lax=True)
            if want_anime:
                _consider(_best(_call("anilist", variant, anilist.search,
                                      http, variant, year)),
                          "anilist", variant=True, lax=True)
                _consider(_best(_call("kitsu", variant, kitsu.search,
                                      http, variant, year)),
                          "kitsu", variant=True, lax=True)
        # imdb -> tmdb exacto + tipo cruzado
        if cfg.tmdb_api_key and ids.get("imdb_id") and "tmdb_id" not in ids:
            extra = _call("tmdb", ids["imdb_id"], tmdb.find_by_imdb,
                          http, cfg.tmdb_api_key, ids["imdb_id"]) or {}
            clean, _, _ = sanitize_ids(extra)
            if clean:
                ids.update(clean)
                sources.append("tmdb:find")
                confs.append(0.95)
        if cfg.tmdb_api_key and "tmdb_id" not in ids and mtype is None:
            for kind in ("movie", "tv"):
                r = _best(_call("tmdb", f"{clean_title}|{kind}",
                                tmdb.search, http, cfg.tmdb_api_key,
                                clean_title, year, kind))
                if _consider(r, "tmdb", variant=True, lax=True):
                    break

    return _result(row, ids, sources, confs, keys, used_fallback, ctx)


def _complete(ids: dict, mtype: str | None, fill_all: bool) -> bool:
    if not ids:
        return False
    if not fill_all:
        return True
    expected = EXPECTED.get(mtype or "", ())
    if not expected:
        return len(ids) >= 2
    return all(f in ids for f in expected)


def _miss_fresh(updated_at) -> bool:
    if not updated_at:
        return False
    try:
        dt = datetime.datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        age = datetime.datetime.now(datetime.timezone.utc) - dt
        return age.days < MISS_MAX_AGE_DAYS
    except (ValueError, TypeError):
        return False


def _result(row: dict, ids: dict, sources: list[str], confs: list[float],
            keys: list[str], fallback: bool, ctx: Ctx) -> dict:
    key = keys[0] if keys else ""
    if ids:
        source = "+".join(sources) if sources else "proveedor"
        conf = round(min(confs) if confs else 0.5, 4)
        if key:
            ctx.local.set(key, ids, source, conf)
    else:
        source, conf = "none", 0.0
        if key:
            ctx.local.set_miss(key)
    out = {"id": row.get("id"), "cache_key": key,
           "source": source, "confidence": conf,
           "_fallback": fallback, "_has": bool(ids)}
    for f in ID_FIELDS:
        out[f] = ids.get(f)
    return out


# ---------------------------------------------------------------------------
# Lote: precarga caché persistente + hilos + apply
# ---------------------------------------------------------------------------
def process_batch(db, ctx: Ctx, rows: list, dry_run: bool) -> tuple[list, dict]:
    """Resuelve un lote y lo aplica. Devuelve (resultados, fila apply)."""
    keys: list[str] = []
    for r in rows:
        primary, backup = pick_release_name(r.get("title"), r.get("title_alt"))
        parsed = parse_release(primary or "")
        mtype = (r.get("type") or "").lower() or None
        for name in (parsed["title"] or primary, primary, backup):
            if name:
                k = cache_key(name, parsed["year"], mtype)
                if k not in keys:
                    keys.append(k)
    try:
        cached = db.get_cache(keys[:1000]) if keys else []
    except Exception as e:
        log.debug("caché persistente no disponible: %s", e)
        cached = []
    ctx.persistent = {c.get("cache_key"): c for c in cached if c.get("cache_key")}

    workers = max(1, min(ctx.cfg.workers, len(rows) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda r: resolve_row(r, ctx), rows))

    payload = [{k: v for k, v in r.items() if not k.startswith("_")}
               for r in results]
    apply_row: dict = {}
    if payload:
        try:
            apply_row = db.apply_ids(payload, dry_run)
        except Exception as e:
            log.error("apply_torrent_ids: %s", e)
            apply_row = {"updated": 0, "cached": 0, "invalid": 0,
                         "nota": f"error: {e}"}
    # caché persistente: 1 título reutilizado -> lo contamos
    reused = sum(1 for r in results
                 if r.get("_has") and not r.get("_fallback")
                 and r.get("source") not in ("none",))
    if reused:
        log.info("caché persistente: %d títulos reutilizados", reused)
    return results, apply_row
