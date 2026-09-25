"""PostgREST + RPCs con la service_role (nunca exponer esta clave)."""
from __future__ import annotations

import httpx


class DbError(Exception):
    """La base no responde o devolvió un error."""


class RpcMissing(DbError):
    """La función RPC no existe: falta aplicar sql/*.sql."""


class DB:
    def __init__(self, url: str, key: str, timeout: float = 60.0) -> None:
        self.url = (url or "").rstrip("/")
        self.c = httpx.Client(
            base_url=self.url, timeout=timeout, follow_redirects=True,
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Accept": "application/json",
                     "Content-Type": "application/json",
                     "User-Agent": "torrents-enricher/1.6"})

    def close(self) -> None:
        self.c.close()

    # -- bajo nivel -------------------------------------------------------
    def ping(self) -> bool:
        """¿La base responde? Lanza DbError con mensaje claro si no."""
        try:
            r = self.c.get("/rest/v1/torrents",
                           params={"select": "id", "limit": 1})
        except httpx.HTTPError as e:
            raise DbError(f"Supabase no responde ({e}). "
                          "Revisa SUPABASE_URL / SUPABASE_SERVICE_KEY.")
        if r.status_code in (401, 403):
            raise DbError("Supabase devolvió 401/403: la SERVICE_KEY no vale. "
                          "Revisa SUPABASE_SERVICE_KEY.")
        if r.status_code >= 400:
            raise DbError(f"Supabase devolvió HTTP {r.status_code}: "
                          f"{r.text[:200]}")
        return True

    def rpc(self, name: str, params: dict | None = None):
        """Llama a public.<name> vía POST /rpc. Devuelve el JSON tal cual."""
        try:
            r = self.c.post(f"/rest/v1/rpc/{name}", json=params or {})
        except httpx.HTTPError as e:
            raise DbError(f"RPC '{name}': sin conexión ({e}).")
        body = (r.text or "")[:300]
        if r.status_code == 404 or "Could not find the function" in body:
            raise RpcMissing(
                f"RPC '{name}' no existe en la base. Aplica sql/*.sql "
                f"(SUPABASE_DB_URL + scripts/apply_sql.sh, o SQL Editor).")
        if r.status_code >= 400:
            raise DbError(f"RPC '{name}' HTTP {r.status_code}: {body}")
        try:
            return r.json()
        except ValueError:
            raise DbError(f"RPC '{name}': respuesta no JSON: {body[:200]}")

    def rpc_row(self, name: str, params: dict | None = None) -> dict:
        data = self.rpc(name, params)
        if isinstance(data, list):
            return data[0] if data else {}
        return data if isinstance(data, dict) else {}

    def table(self, table: str, select: str = "*",
              limit: int = 5, **filters) -> list:
        params = {"select": select, "limit": limit, **filters}
        try:
            r = self.c.get(f"/rest/v1/{table}", params=params)
        except httpx.HTTPError as e:
            raise DbError(f"GET {table}: sin conexión ({e}).")
        if r.status_code >= 400:
            raise DbError(f"GET {table} HTTP {r.status_code}: "
                          f"{(r.text or '')[:200]}")
        try:
            data = r.json()
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    # -- enriquecimiento --------------------------------------------------
    def get_to_enrich(self, batch: int, recheck_days: int,
                      only_types: str, expr: str = "auto") -> list:
        data = self.rpc("get_torrents_to_enrich_expr",
                        {"p_expr": expr, "p_batch": batch,
                         "p_recheck_days": recheck_days,
                         "p_only_types": only_types or None})
        return data if isinstance(data, list) else []

    def get_missing(self, batch: int, min_age_min: int,
                    max_attempts: int) -> list:
        data = self.rpc("get_torrents_missing_ids",
                        {"p_batch": batch,
                         "p_min_age_minutes": min_age_min,
                         "p_max_attempts": max_attempts})
        return data if isinstance(data, list) else []

    def reset_retry(self, max_attempts: int, min_age_min: int) -> dict:
        return self.rpc_row("reset_missing_ids_for_retry",
                            {"p_max_attempts": max_attempts,
                             "p_min_age_minutes": min_age_min})

    def apply_ids(self, rows: list, dry_run: bool = False) -> dict:
        return self.rpc_row("apply_torrent_ids",
                            {"p_ids": rows, "p_dry_run": bool(dry_run)})

    def get_cache(self, keys: list) -> list:
        if not keys:
            return []
        data = self.rpc("get_title_cache", {"p_keys": keys})
        return data if isinstance(data, list) else []

    # -- limpieza ---------------------------------------------------------
    def id_bounds(self) -> tuple[int, int]:
        """(id mínimo, id máximo) de public.torrents.

        La purga trabaja por tandas de ids; necesita saber por dónde empezar
        y terminar. Devuelve (0, -1) si la tabla está vacía (una tanda vacía
        no rompe nada, pero así nos ahorramos la llamada).
        """
        lo = self._edge_id("id")
        if lo is None:
            return (0, -1)
        hi = self._edge_id("id.desc")
        return (lo, hi if hi is not None else lo)

    def _edge_id(self, order: str) -> int | None:
        try:
            r = self.c.get("/rest/v1/torrents",
                           params={"select": "id", "order": order, "limit": 1})
        except httpx.HTTPError as e:
            raise DbError(f"GET torrents (order={order}): sin conexión ({e}).")
        if r.status_code >= 400:
            raise DbError(f"GET torrents HTTP {r.status_code}: "
                          f"{(r.text or '')[:200]}")
        try:
            data = r.json()
        except ValueError:
            return None
        if isinstance(data, list) and data and data[0].get("id") is not None:
            return int(data[0]["id"])
        return None

    def purge_junk(self, dry_run: bool, limit: int,
                   purge_empty: bool,
                   min_id: int | None = None,
                   max_id: int | None = None) -> dict:
        return self.rpc_row("purge_junk_torrents",
                            {"p_dry_run": dry_run, "p_limit": limit,
                             "p_purge_empty": purge_empty,
                             "p_min_id": min_id, "p_max_id": max_id})

    def purge_blocked(self, dry_run: bool, limit: int,
                      tokens: list | None = None,
                      soft_tokens: list | None = None,
                      allow: list | None = None,
                      min_id: int | None = None,
                      max_id: int | None = None) -> dict:
        return self.rpc_row("purge_blocked_torrents",
                            {"p_tokens": tokens, "p_soft_tokens": soft_tokens,
                             "p_allow": allow, "p_dry_run": dry_run,
                             "p_limit": limit,
                             "p_min_id": min_id, "p_max_id": max_id})

    def purge_absolute(self, action: str, require_season_null: bool,
                       dry_run: bool, limit: int,
                       min_id: int | None = None,
                       max_id: int | None = None) -> dict:
        return self.rpc_row("purge_absolute_only_torrents",
                            {"p_action": action,
                             "p_require_season_null": require_season_null,
                             "p_dry_run": dry_run, "p_limit": limit,
                             "p_min_id": min_id, "p_max_id": max_id})

    def purge_dead(self, min_seeders: int, older_days: int,
                   dry_run: bool, limit: int,
                   min_id: int | None = None,
                   max_id: int | None = None) -> dict:
        return self.rpc_row("purge_dead_torrents",
                            {"p_min_seeders": min_seeders,
                             "p_older_days": older_days,
                             "p_dry_run": dry_run, "p_limit": limit,
                             "p_min_id": min_id, "p_max_id": max_id})

    def keep_best(self, limit: int, min_seeders: int, only_types: str,
                  dry_run: bool, max_deletes: int) -> dict:
        return self.rpc_row("keep_best_torrents",
                            {"p_limit": limit, "p_min_seeders": min_seeders,
                             "p_only_types": only_types or None,
                             "p_dry_run": dry_run,
                             "p_max_deletes": max_deletes})

    def keep_best_es(self, cfg, dry_run: bool, max_deletes: int) -> dict:
        return self.rpc_row("keep_best_torrents_es",
                            {"p_limit": cfg.keep_best_es_limit,
                             "p_min_spanish": cfg.keep_best_es_min_spanish,
                             "p_min_seeders": cfg.keep_best_min_seeders,
                             "p_only_types": cfg.keep_best_only_types or None,
                             "p_dedupe": cfg.keep_best_es_dedupe,
                             "p_strict_spanish": cfg.keep_best_es_strict,
                             "p_use_title_hint":
                                 cfg.keep_best_es_use_title_hint,
                             "p_allow_subs_fallback":
                                 cfg.keep_best_es_allow_subs_fallback,
                             "p_spanish_tokens":
                                 cfg.spanish_tokens_extra or None,
                             "p_dry_run": dry_run,
                             "p_max_deletes": max_deletes})

    # -- informes ---------------------------------------------------------
    def renumber(self, dry_run: bool, max_rows: int,
                 force: bool, start: int = 1) -> dict:
        return self.rpc_row("renumber_torrent_ids",
                            {"p_dry_run": dry_run, "p_max_rows": max_rows,
                             "p_force": force, "p_start": start})

    def id_report(self) -> dict:
        return self.rpc_row("torrents_id_report")

    def ids_stats(self) -> dict:
        return self.rpc_row("torrents_ids_stats")

    def quality_stats(self) -> dict:
        return self.rpc_row("torrents_quality_stats")

    def spanish_stats(self) -> dict:
        return self.rpc_row("torrents_spanish_stats")

    def spanish_gaps(self, limit: int, tokens: str) -> list:
        data = self.rpc("report_spanish_gaps",
                        {"p_limit": limit,
                         "p_spanish_tokens": tokens or None})
        return data if isinstance(data, list) else []

    def security_audit(self) -> list:
        data = self.rpc("torrents_security_audit")
        return data if isinstance(data, list) else []

    # -- autodetección de la columna del título ---------------------------
    def detect_title_column(self) -> str:
        """'title_text' si title está vacío y title_text trae releases."""
        from .titleparse import looks_like_release_name
        try:
            rows = self.table("torrents", "title,title_text", 5)
        except DbError:
            return "title"
        if not rows:
            return "title"
        titles = [(r.get("title") or "").strip() for r in rows]
        texts = [(r.get("title_text") or "").strip() for r in rows]
        if all(not t for t in titles) and any(
                looks_like_release_name(x) for x in texts):
            return "title_text"
        return "title"
