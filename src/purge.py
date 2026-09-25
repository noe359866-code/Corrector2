"""Etapas de limpieza (llaman a las RPCs con el presupuesto compartido).

La purga se hace POR TANDAS de ids (p_min_id/p_max_id). Antes cada etapa
recorría la tabla entera en un solo statement: en Supabase la API REST mata
cualquier statement que pase de ~8 s (error 57014 "canceling statement due
to statement timeout") y la corrida se moría con exit 2 sin llegar a las
etapas siguientes.

Ahora:
  · cada tanda es un rango corto de la primary key -> Postgres usa el índice
    y el statement cabe de sobra en el timeout;
  · si una tanda revienta por timeout, se ENCOGE y se reintenta (hasta
    PURGE_CHUNK_MIN filas);
  · si aun así no cabe, se apunta el rango en `pendientes` y se SIGUE con la
    siguiente tanda: una tanda mala ya no tumba la corrida entera;
  · la etapa nunca lanza: devuelve lo que pudo y lo cuenta en `errores`.

PURGE_CHUNK_ROWS=0 apaga las tandas (una sola llamada a tabla completa).
"""
from __future__ import annotations

import logging
import time

from .blacklist import ALLOW, BLOCKED_CORE, SOFT_ADULT
from .db import DbError

log = logging.getLogger("purge")

# ---------------------------------------------------------------------------
# Cómo se ve un timeout de Postgres en la respuesta de PostgREST
# ---------------------------------------------------------------------------
_TIMEOUT_HINTS = ("57014", "statement timeout", "canceling statement",
                  "query_canceled", "timeout")


def _es_timeout(e: Exception) -> bool:
    s = str(e).lower()
    return any(h in s for h in _TIMEOUT_HINTS)


def _nuevo_acumulador() -> dict:
    return {"matched": 0, "deleted": 0, "skipped": 0, "skipped_limit": 0,
            "updated": 0, "sample": [], "tandas": 0, "errores": 0,
            "pendientes": []}


def _acumular(total: dict, r: dict) -> None:
    for k in ("matched", "deleted", "skipped", "skipped_limit", "updated"):
        if k in (r or {}):
            total[k] = total.get(k, 0) + int(r.get(k) or 0)
    if not total.get("sample") and (r or {}).get("sample"):
        total["sample"] = r["sample"]


def _nota(etiqueta: str, total: dict, singular: str, borrado: str,
          con_limpiados: bool = False) -> str:
    """Nota con el mismo formato de siempre + lo que faltó, si faltó algo."""
    extra = ""
    if con_limpiados and total.get("updated"):
        extra = f", {total['updated']} limpiados"
    nota = (f"{etiqueta}: {total['matched']} {singular}, "
            f"{total['deleted']} {borrado}{extra}")
    if total.get("parado_por_presupuesto"):
        nota += " (recorrido parcial: se agotó el presupuesto de borrado)"
    if total["errores"]:
        nota += (f" · ¡OJO! {total['errores']} tanda(s) sin revisar "
                 f"(timeout); repetir la corrida")
    return nota


def _por_tandas(db, llamada, budget, dry_run: bool, chunk_rows: int,
                chunk_min: int, etiqueta: str, singular: str,
                borrado: str = "borrados",
                con_limpiados: bool = False) -> dict:
    """Recorre la tabla por rangos de id y acumula el resultado de cada tanda.

    `llamada(min_id, max_id, limite)` debe devolver el dict de la RPC.
    """
    total = _nuevo_acumulador()
    try:
        lo, hi = db.id_bounds()
    except DbError as e:
        total["errores"] = 1
        total["pendientes"].append([None, None, str(e)[:160]])
        total["nota"] = f"{etiqueta}: no se pudo leer la tabla ({e})"
        log.error("%s: no se pudo leer el rango de ids: %s", etiqueta, e)
        return total

    if hi < lo:                       # tabla vacía
        total["nota"] = f"{etiqueta}: 0 {singular}, 0 {borrado} (tabla vacía)"
        return total

    if int(chunk_rows or 0) <= 0:     # tandas apagadas: una sola pasada
        try:
            r = llamada(None, None, budget.remaining)
        except DbError as e:
            total["errores"] = 1
            total["pendientes"].append([None, None, str(e)[:160]])
            total["nota"] = f"{etiqueta}: falló ({e})"
            log.error("%s: %s", etiqueta, e)
            return total
        _acumular(total, r)
        total["tandas"] = 1
        budget.consume(int(r.get("deleted", 0) or 0))
        total["nota"] = _nota(etiqueta, total, singular, borrado,
                               con_limpiados)
        return total

    size = max(1, int(chunk_rows))
    chunk_min = max(1, int(chunk_min or 1))
    cursor = lo
    while cursor <= hi:
        if budget.exhausted:
            total["parado_por_presupuesto"] = True
            break
        top = min(cursor + size - 1, hi)
        try:
            r = llamada(cursor, top, budget.remaining)
        except DbError as e:
            if _es_timeout(e) and size > chunk_min:
                size = max(chunk_min, size // 2)
                log.warning("%s: tanda %s-%s se pasó del timeout; "
                            "reintentando en tandas de %s filas",
                            etiqueta, cursor, top, size)
                continue                     # mismo cursor, tanda más chica
            if not _es_timeout(e):
                # error de red/API: un reintento no hace daño
                time.sleep(2)
                try:
                    r = llamada(cursor, top, budget.remaining)
                except DbError as e2:
                    e = e2
                else:
                    _acumular(total, r)
                    total["tandas"] += 1
                    budget.consume(int(r.get("deleted", 0) or 0))
                    cursor = top + 1
                    continue
            total["errores"] += 1
            total["pendientes"].append([cursor, top, str(e)[:160]])
            log.error("%s: tanda %s-%s falló (%s); se sigue con la siguiente",
                      etiqueta, cursor, top, str(e)[:120])
            cursor = top + 1
            continue

        _acumular(total, r)
        total["tandas"] += 1
        borradas = int(r.get("deleted", 0) or 0)
        budget.consume(borradas)
        if int(r.get("matched", 0) or 0):
            log.info("%s: tanda %s-%s → %s candidatas, %s borradas",
                     etiqueta, cursor, top, r.get("matched"), borradas)
        cursor = top + 1

    total["nota"] = _nota(etiqueta, total, singular, borrado,
                           con_limpiados)
    if total["tandas"] > 1:
        log.info("%s: %s tandas, %s candidatas, %s borradas%s",
                 etiqueta, total["tandas"], total["matched"], total["deleted"],
                 f", {total['errores']} con error" if total["errores"] else "")
    return total


def _etapa(out: dict, clave: str, activada: bool, nota_off: str,
           trabajo) -> None:
    """Una etapa aislada: si la base falla, las demás siguen."""
    if not activada:
        out[clave] = {"skipped": True, "nota": nota_off}
        return
    try:
        out[clave] = trabajo()
    except DbError as e:               # no debería pasar, pero por si acaso
        log.error("%s: la etapa falló: %s", clave, e)
        out[clave] = {"matched": 0, "deleted": 0, "skipped": 0,
                      "skipped_limit": 0, "sample": [], "errores": 1,
                      "pendientes": [[None, None, str(e)[:160]]],
                      "nota": f"{clave}: falló ({e})"}


def run_purge(db, cfg, budget, dry_run: bool) -> dict:
    """junk -> blocked -> absolute -> dead. Devuelve un dict por etapa."""
    out: dict = {}
    ch, ch_min = cfg.purge_chunk_rows, cfg.purge_chunk_min

    _etapa(out, "junk", cfg.purge_junk,
           "desactivada (PURGE_JUNK=false)",
           lambda: _por_tandas(
               db,
               lambda a, b, lim: db.purge_junk(dry_run, lim,
                                               cfg.purge_empty_title, a, b),
               budget, dry_run, ch, ch_min, "basura", "candidatas",
               "borradas"))

    if cfg.purge_blocked:
        # OJO: p_tokens sustituye a la lista interna -> hay que fusionar.
        tokens = list(BLOCKED_CORE) + list(cfg.blocked_tokens_extra) \
            if cfg.blocked_tokens_extra else None
        soft = list(SOFT_ADULT) if cfg.purge_soft_adult else None
        allow = list(cfg.allow_tokens_extra) or None
    else:
        tokens = soft = allow = None

    def _trabajo_blocked():
        return _por_tandas(
            db,
            lambda a, b, lim: db.purge_blocked(dry_run, lim, tokens, soft,
                                               allow, a, b),
            budget, dry_run, ch, ch_min,
            "bloqueados", "candidatos", "borrados")

    _etapa(out, "blocked", cfg.purge_blocked,
           "desactivada (PURGE_BLOCKED=false)", _trabajo_blocked)

    _etapa(out, "absolute", cfg.purge_absolute_only,
           "desactivada (PURGE_ABSOLUTE_ONLY=false)",
           lambda: _por_tandas(
               db,
               lambda a, b, lim: db.purge_absolute(
                   cfg.absolute_only_action,
                   cfg.absolute_require_season_null, dry_run, lim, a, b),
               budget, dry_run, ch, ch_min,
               f"absolute_only ({cfg.absolute_only_action})",
               "candidatos", "borrados", con_limpiados=True))

    _etapa(out, "dead", cfg.purge_dead,
           "desactivada (PURGE_DEAD=false)",
           lambda: _por_tandas(
               db,
               lambda a, b, lim: db.purge_dead(
                   cfg.dead_min_seeders, cfg.dead_older_days, dry_run, lim,
                   a, b),
               budget, dry_run, ch, ch_min, "muertos", "candidatos",
               "borrados"))

    # para que ALLOW no quede sin usar si alguien importa este módulo
    assert ALLOW and BLOCKED_CORE
    return out


def etapas_incompletas(out: dict) -> list[str]:
    """Nombres de etapa que no pudieron revisar toda la tabla."""
    return [k for k, v in (out or {}).items()
            if isinstance(v, dict) and int(v.get("errores", 0) or 0) > 0]
