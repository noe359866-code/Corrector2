"""Toda la configuración por variables de entorno (ver config.example.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on", "si", "sí")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    # --- credenciales ---
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_db_url: str = ""
    tmdb_api_key: str = ""
    omdb_api_key: str = ""

    # --- columna del título ---
    title_column: str = "auto"  # auto | title | title_text

    # --- modo / ritmo ---
    dry_run: bool = False
    batch_size: int = 400
    enrich_rounds: int = 25
    workers: int = 8
    recheck_days: int = 45
    fill_all_ids: bool = True
    only_types: str = ""

    # --- tope por corrida ---
    max_deletes_per_run: int = 0  # 0 = sin tope

    # --- reordenador de id ---
    renumber_ids: bool = False
    renumber_ids_force: bool = False
    renumber_max_rows: int = 200000

    # --- limpieza ---
    purge_junk: bool = True
    purge_blocked: bool = True
    purge_absolute_only: bool = True
    purge_dead: bool = False
    purge_soft_adult: bool = False
    purge_empty_title: bool = False
    absolute_only_action: str = "delete"  # delete | nullify
    absolute_require_season_null: bool = True
    dead_min_seeders: int = 1
    dead_older_days: int = 60
    # tandas de ids por etapa: la API de Supabase mata los statements de más
    # de ~8 s (error 57014). 0 = una sola llamada a tabla completa.
    purge_chunk_rows: int = 20000
    purge_chunk_min: int = 1000       # suelo al encoger la tanda por timeout

    # --- mejores (regla simple) ---
    keep_best: bool = False
    keep_best_limit: int = 3
    keep_best_min_seeders: int = 0
    keep_best_only_types: str = ""

    # --- regla de español ---
    keep_best_es: bool = True
    keep_best_es_limit: int = 3
    keep_best_es_min_spanish: int = 2
    keep_best_es_allow_subs_fallback: bool = True
    keep_best_es_dedupe: bool = True
    keep_best_es_strict: bool = False
    keep_best_es_use_title_hint: bool = True
    spanish_tokens_extra: str = ""
    spanish_report_limit: int = 15

    # --- reintentos ---
    retry_missing: bool = True
    retry_max_attempts: int = 4
    retry_min_age_minutes: int = 60
    retry_rounds: int = 10
    aggressive_fallback: bool = True

    # --- reporte ---
    summary_file: str = "summary.md"
    summary_json: str = "summary.json"
    report_to_step_summary: bool = True

    # --- listas propias ---
    blocked_tokens_extra: list[str] = field(default_factory=list)
    allow_tokens_extra: list[str] = field(default_factory=list)

    # --- caché ---
    cache_sqlite: str = "cache.sqlite"
    log_level: str = "INFO"


def load() -> Settings:
    """Lee la configuración actual del entorno."""
    return Settings(
        supabase_url=_str("SUPABASE_URL"),
        supabase_service_key=_str("SUPABASE_SERVICE_KEY"),
        supabase_db_url=_str("SUPABASE_DB_URL"),
        tmdb_api_key=_str("TMDB_API_KEY"),
        omdb_api_key=_str("OMDB_API_KEY"),
        title_column=_str("TITLE_COLUMN", "auto").lower() or "auto",
        dry_run=_bool("DRY_RUN", False),
        batch_size=_int("BATCH_SIZE", 400),
        enrich_rounds=_int("ENRICH_ROUNDS", 25),
        workers=max(1, _int("WORKERS", 8)),
        recheck_days=_int("RECHECK_DAYS", 45),
        fill_all_ids=_bool("FILL_ALL_IDS", True),
        only_types=_str("ONLY_TYPES", ""),
        max_deletes_per_run=_int("MAX_DELETES_PER_RUN", 0),
        renumber_ids=_bool("RENUMBER_IDS", False),
        renumber_ids_force=_bool("RENUMBER_IDS_FORCE", False),
        renumber_max_rows=_int("RENUMBER_MAX_ROWS", 200000),
        purge_junk=_bool("PURGE_JUNK", True),
        purge_blocked=_bool("PURGE_BLOCKED", True),
        purge_absolute_only=_bool("PURGE_ABSOLUTE_ONLY", True),
        purge_dead=_bool("PURGE_DEAD", False),
        purge_soft_adult=_bool("PURGE_SOFT_ADULT", False),
        purge_empty_title=_bool("PURGE_EMPTY_TITLE", False),
        absolute_only_action=_str("ABSOLUTE_ONLY_ACTION", "delete").lower() or "delete",
        absolute_require_season_null=_bool("ABSOLUTE_REQUIRE_SEASON_NULL", True),
        dead_min_seeders=_int("DEAD_MIN_SEEDERS", 1),
        dead_older_days=_int("DEAD_OLDER_DAYS", 60),
        purge_chunk_rows=_int("PURGE_CHUNK_ROWS", 20000),
        purge_chunk_min=_int("PURGE_CHUNK_MIN", 1000),
        keep_best=_bool("KEEP_BEST", False),
        keep_best_limit=_int("KEEP_BEST_LIMIT", 3),
        keep_best_min_seeders=_int("KEEP_BEST_MIN_SEEDERS", 0),
        keep_best_only_types=_str("KEEP_BEST_ONLY_TYPES", ""),
        keep_best_es=_bool("KEEP_BEST_ES", True),
        keep_best_es_limit=_int("KEEP_BEST_ES_LIMIT", 3),
        keep_best_es_min_spanish=_int("KEEP_BEST_ES_MIN_SPANISH", 2),
        keep_best_es_allow_subs_fallback=_bool("KEEP_BEST_ES_ALLOW_SUBS_FALLBACK", True),
        keep_best_es_dedupe=_bool("KEEP_BEST_ES_DEDUPE", True),
        keep_best_es_strict=_bool("KEEP_BEST_ES_STRICT", False),
        keep_best_es_use_title_hint=_bool("KEEP_BEST_ES_USE_TITLE_HINT", True),
        spanish_tokens_extra=_str("SPANISH_TOKENS_EXTRA", ""),
        spanish_report_limit=_int("SPANISH_REPORT_LIMIT", 15),
        retry_missing=_bool("RETRY_MISSING", True),
        retry_max_attempts=_int("RETRY_MAX_ATTEMPTS", 4),
        retry_min_age_minutes=_int("RETRY_MIN_AGE_MINUTES", 60),
        retry_rounds=_int("RETRY_ROUNDS", 10),
        aggressive_fallback=_bool("AGGRESSIVE_FALLBACK", True),
        summary_file=_str("SUMMARY_FILE", "summary.md"),
        summary_json=_str("SUMMARY_JSON", "summary.json"),
        report_to_step_summary=_bool("REPORT_TO_STEP_SUMMARY", True),
        blocked_tokens_extra=_list("BLOCKED_TOKENS_EXTRA"),
        allow_tokens_extra=_list("ALLOW_TOKENS_EXTRA"),
        cache_sqlite=_str("CACHE_SQLITE", "cache.sqlite"),
        log_level=_str("LOG_LEVEL", "INFO").upper() or "INFO",
    )
