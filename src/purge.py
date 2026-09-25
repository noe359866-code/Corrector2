"""Etapas de limpieza (llaman a las RPCs con el presupuesto compartido)."""
from __future__ import annotations

import logging

from .blacklist import ALLOW, BLOCKED_CORE, SOFT_ADULT

log = logging.getLogger("purge")


def run_purge(db, cfg, budget, dry_run: bool) -> dict:
    """junk -> blocked -> absolute -> dead. Devuelve un dict por etapa."""
    out: dict = {}

    if cfg.purge_junk:
        r = db.purge_junk(dry_run, budget.remaining, cfg.purge_empty_title)
        budget.consume(int(r.get("deleted", 0) or 0))
        out["junk"] = r
        log.info("limpieza basura: %s", r.get("nota"))
    else:
        out["junk"] = {"skipped": True, "nota": "desactivada (PURGE_JUNK=false)"}

    if cfg.purge_blocked:
        # OJO: p_tokens sustituye a la lista interna -> hay que fusionar.
        tokens = list(BLOCKED_CORE) + list(cfg.blocked_tokens_extra) \
            if cfg.blocked_tokens_extra else None
        soft = list(SOFT_ADULT) if cfg.purge_soft_adult else None
        allow = list(cfg.allow_tokens_extra) or None
        r = db.purge_blocked(dry_run, budget.remaining, tokens, soft, allow)
        budget.consume(int(r.get("deleted", 0) or 0))
        out["blocked"] = r
        log.info("limpieza xxx: %s", r.get("nota"))
    else:
        out["blocked"] = {"skipped": True, "nota": "desactivada (PURGE_BLOCKED=false)"}

    if cfg.purge_absolute_only:
        r = db.purge_absolute(cfg.absolute_only_action,
                              cfg.absolute_require_season_null,
                              dry_run, budget.remaining)
        budget.consume(int(r.get("deleted", 0) or 0))
        out["absolute"] = r
        log.info("absolute_only: %s", r.get("nota"))
    else:
        out["absolute"] = {"skipped": True,
                           "nota": "desactivada (PURGE_ABSOLUTE_ONLY=false)"}

    if cfg.purge_dead:
        r = db.purge_dead(cfg.dead_min_seeders, cfg.dead_older_days,
                          dry_run, budget.remaining)
        budget.consume(int(r.get("deleted", 0) or 0))
        out["dead"] = r
        log.info("muertos: %s", r.get("nota"))
    else:
        out["dead"] = {"skipped": True, "nota": "desactivada (PURGE_DEAD=false)"}

    # para que ALLOW no quede sin usar si alguien importa este módulo
    assert ALLOW and BLOCKED_CORE
    return out
