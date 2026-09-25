"""CLI: python -m src.main MODO [flags]

Modos: all | purge | enrich | retry | best | renumber | stats | doctor | selftest
Flags: --dry-run --limit N --rounds N --max-deletes N --renumber -v -q
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback

from . import blacklist, matching
from .cache import LocalCache
from .config import Settings, load
from .db import DB, DbError, RpcMissing
from .enrich import Ctx, EXPECTED, new_stats, process_batch, sanitize_ids
from .http_client import Http
from .providers import anilist, imdb
from .purge import run_purge
from .report import Report
from .textnorm import normalize
from .titleparse import looks_like_release_name, parse_release

log = logging.getLogger("main")


# ---------------------------------------------------------------------------
class Budget:
    """Presupuesto de borrado compartido (0 = sin tope)."""

    def __init__(self, cap: int) -> None:
        self.cap = max(0, int(cap or 0))
        self.used = 0

    @property
    def remaining(self) -> int:
        return 0 if self.cap <= 0 else max(0, self.cap - self.used)

    @property
    def exhausted(self) -> bool:
        return self.cap > 0 and self.used >= self.cap

    def consume(self, n: int) -> None:
        self.used += max(0, int(n or 0))


def setup_logging(level: str) -> None:
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    datefmt = "%H:%M:%S"
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(fmt, datefmt))
        root.addHandler(console)
        try:
            disk = logging.FileHandler("run.log", encoding="utf-8")
            disk.setFormatter(logging.Formatter(fmt, datefmt))
            root.addHandler(disk)
        except OSError:
            pass


def must_db(cfg: Settings, rep: Report) -> DB:
    """DB lista o salida 2 con mensaje claro (y reporte escrito)."""
    if not cfg.supabase_url or not cfg.supabase_service_key:
        msg = ("❌ Faltan SUPABASE_URL / SUPABASE_SERVICE_KEY. "
               "Ponlos como Secrets del repo (o en tu .env local).")
        print(msg, flush=True)
        rep.text(msg)
        rep.set("error", msg)
        rep.write(cfg.summary_file, cfg.summary_json,
                  cfg.report_to_step_summary)
        raise SystemExit(2)
    db = DB(cfg.supabase_url, cfg.supabase_service_key)
    try:
        db.ping()
    except DbError as e:
        msg = f"❌ {e}"
        print(msg, flush=True)
        rep.text(msg)
        rep.set("error", msg)
        rep.write(cfg.summary_file, cfg.summary_json,
                  cfg.report_to_step_summary)
        raise SystemExit(2)
    return db


def _rpc_error_note(rep: Report, cfg: Settings, e: Exception) -> None:
    msg = f"❌ {e}"
    print(msg, flush=True)
    rep.text(msg)
    rep.set("error", str(e))
    rep.write(cfg.summary_file, cfg.summary_json,
              cfg.report_to_step_summary)


# ---------------------------------------------------------------------------
# SELFTEST (offline: sin red ni base)
# ---------------------------------------------------------------------------
def cmd_selftest(cfg: Settings) -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))
        print(("  ✔ " if ok else "  ✖ ") + name
              + (f" ({detail})" if detail and not ok else ""), flush=True)

    print("selftest (offline)...", flush=True)
    p = parse_release("The.Matrix.1999.1080p.BluRay.x264-AMIABLE")
    check("parser película", p["title"] == "The Matrix" and p["year"] == 1999,
          str(p))
    p = parse_release("[SubsPlease] Jujutsu Kaisen - 24 (1080p)")
    check("parser anime", "Jujutsu Kaisen" in p["title"]
          and p["absolute"] == 24, str(p))
    p = parse_release("Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.H.264-GROUP")
    check("parser 'Part Two' no es episodio",
          p["title"] == "Dune Part Two" and p["year"] == 2024
          and p["episode"] is None and p["absolute"] is None, str(p))
    check("release_name sí",
          looks_like_release_name(
              "Frieren Beyond Journeys End S01E01 1080p WEB-DL x264"))
    check("release_name no",
          not looks_like_release_name("Nombre interno 12345"))
    check("blacklist onlyfans", blacklist.is_blocked("OnlyFans pack 2024")[0])
    check("blacklist permite XXXTentacion",
          not blacklist.is_blocked("XXXTentacion - Look At Me")[0])
    check("blacklist permite xXx (2002)",
          not blacklist.is_blocked("xXx 2002 1080p BluRay")[0])
    check("blacklist grupo pegado",
          blacklist.is_blocked("Show S01", group="xXxTorrents")[0])
    check("matching idéntico",
          matching.similarity("Dune Part Two", "Dune Part Two") == 1.0)
    base = matching.confidence("Frieren", "Frieren Beyond Journey End")
    vari = matching.confidence("Frieren", "Frieren Beyond Journey End",
                               variant=True)
    check("matching penaliza variante", 0 < vari < base, f"{base} vs {vari}")
    check("matching umbrales",
          matching.accepted(0.95) and not matching.accepted(0.5)
          and not matching.accepted(0.85, lax=True))
    check("textnorm acentos", normalize("ÁÉÍÓÚ Ñ Ç") == "aeiou n c")
    clean, inv, _ = sanitize_ids({"tmdb_id": "603.5", "mal_id": " 52991 ",
                                  "imdb_id": "tt0133093"})
    check("saneo rechaza '603.5', acepta ' 52991 '",
          inv == 1 and clean.get("mal_id") == 52991
          and clean.get("imdb_id") == "tt0133093", str((clean, inv)))
    clean, inv, _ = sanitize_ids({"imdb_id": "tt-abc"})
    check("saneo rechaza imdb inválido", inv == 1 and not clean)

    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sql_ok = all((root / "sql" / f"00{i}_{n}.sql").exists() for i, n in
                 [(0, "base"), (1, "helpers"), (2, "enrich"), (3, "purge"),
                  (4, "retry_spanish"), (5, "renumber_limit"),
                  (6, "signature_guard"), (7, "permisos")])
    check("sql/000..007 existen", sql_ok)
    try:
        s007 = (root / "sql" / "007_permisos.sql").read_text(encoding="utf-8")
        danger = ("purge_junk_torrents", "purge_blocked_torrents",
                  "purge_absolute_only_torrents", "purge_dead_torrents",
                  "keep_best_torrents", "keep_best_torrents_es",
                  "renumber_torrent_ids", "apply_torrent_ids")
        check("007 cubre las 8 destructivas",
              all(fn in s007 for fn in danger))
        s006 = (root / "sql" / "006_signature_guard.sql").read_text(
            encoding="utf-8")
        check("006 cubre keep_best_torrents_es",
              "keep_best_torrents_es" in s006)
    except OSError as e:
        check("sql legible", False, str(e))

    check("config por defecto", load().batch_size == 400)

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".sqlite") as tmp:
        cache = LocalCache(tmp.name)
        cache.set("k", {"tmdb_id": 603}, "tmdb", 0.95)
        hit = cache.get("k")
        check("caché local", hit and hit["ids"].get("tmdb_id") == 603)
        cache.close()

    r = imdb.parse_suggestions(
        {"d": [{"id": "tt0133093", "l": "The Matrix", "y": 1999}]},
        "The Matrix", 1999)
    check("imdb parse", r and r[0].ids.get("imdb_id") == "tt0133093"
          and r[0].confidence >= 0.9, str(r[0].confidence if r else None))
    r = anilist.parse_response(
        {"data": {"Page": {"media": [
            {"id": 154587, "idMal": 52991, "seasonYear": 2023,
             "title": {"romaji": "Frieren", "english": "Frieren: Beyond Journey's End"}}]}}},
        "Frieren", 2023)
    check("anilist parse", r and r[0].ids.get("mal_id") == 52991)

    try:
        import yaml  # noqa
        wf = (root / ".github" / "workflows" / "main.yml").read_text(
            encoding="utf-8")
        data = yaml.safe_load(wf)
        check("workflow válido",
              "jobs" in data and len(data["jobs"]) >= 3)
    except ImportError:
        check("workflow válido", True, "pyyaml no instalado (skip)")
    except Exception as e:  # noqa: BLE001
        check("workflow válido", False, str(e))

    fails = [c for c in checks if not c[1]]
    if fails:
        print(f"✖ SELFTEST: {len(fails)} fallos", flush=True)
        return 1
    print(f"✔ SELFTEST OK ({len(checks)} comprobaciones)", flush=True)
    return 0


# ---------------------------------------------------------------------------
# DOCTOR
# ---------------------------------------------------------------------------
def cmd_doctor(cfg: Settings) -> int:
    rep = Report()
    rep.title("🩺 Doctor")
    if not cfg.supabase_url or not cfg.supabase_service_key:
        msg = "❌ Faltan SUPABASE_URL / SUPABASE_SERVICE_KEY"
        print(msg, flush=True)
        rep.text(msg)
        rep.write(cfg.summary_file, cfg.summary_json,
                  cfg.report_to_step_summary)
        return 1
    db = DB(cfg.supabase_url, cfg.supabase_service_key)
    try:
        try:
            db.ping()
            print("✔ supabase: responde", flush=True)
            rep.text("✔ supabase: responde")
        except DbError as e:
            print(f"❌ supabase: {e}", flush=True)
            rep.text(f"❌ supabase: {e}")
            rep.write(cfg.summary_file, cfg.summary_json,
                      cfg.report_to_step_summary)
            return 1
        try:
            col = db.detect_title_column()
            print(f"✔ título: auto -> {col}", flush=True)
            rep.text(f"✔ título: auto -> `{col}`")
        except DbError as e:
            print(f"⚠️ título: {e}", flush=True)
            rep.text(f"⚠️ título: {e}")
        try:
            stats = db.ids_stats()
            print(f"✔ RPCs: ok (total {stats.get('total')})", flush=True)
            rep.text(f"✔ RPCs: ok (total `{stats.get('total')}`)")
        except RpcMissing as e:
            print(f"⚠️ RPCs: {e}", flush=True)
            rep.text(f"⚠️ RPCs: {e}")
        code = 0
        try:
            findings = db.security_audit()
            for f in findings:
                nivel = (f.get("nivel") or "").upper()
                hall = f.get("hallazgo") or ""
                if nivel == "ALTO":
                    print(f"🚨 SEGURIDAD (ALTO): {hall}", flush=True)
                    rep.text(f"🚨 SEGURIDAD (ALTO): {hall}")
                    code = 1
                elif nivel == "MEDIO":
                    print(f"⚠️ seguridad (medio): {hall}", flush=True)
                    rep.text(f"⚠️ seguridad (medio): {hall}")
                else:
                    print(f"🛡️ seguridad: {hall}", flush=True)
                    rep.text(f"🛡️ seguridad: {hall}")
            rep.set("seguridad", findings)
        except (DbError, RpcMissing) as e:
            print(f"⚠️ seguridad: {e}", flush=True)
            rep.text(f"⚠️ seguridad: {e}")
        print(f"TMDB: {'✔' if cfg.tmdb_api_key else '—'}  "
              f"OMDb: {'✔' if cfg.omdb_api_key else '—'}  "
              f"dry_run: {cfg.dry_run}", flush=True)
        rep.write(cfg.summary_file, cfg.summary_json,
                  cfg.report_to_step_summary)
        return code
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Etapas (reutilizadas por los modos sueltos y por all)
# ---------------------------------------------------------------------------
def do_purge(db: DB, cfg: Settings, rep: Report, budget: Budget,
             dry_run: bool) -> dict:
    before = db.quality_stats()
    out = run_purge(db, cfg, budget, dry_run)
    after = db.quality_stats()
    fuera = sum(int((v or {}).get("skipped_limit", 0) or 0)
                for v in out.values() if isinstance(v, dict))
    rep.title("🧹 Limpieza" + (" (dry-run)" if dry_run else ""))
    rep.table("Antes / después",
              ["Métrica", "Antes", "Después", "Fuera por tope"],
              [["Total", before.get("total"), after.get("total"), fuera],
               ["Sin seeders", before.get("sin_seeders"),
                after.get("sin_seeders"), ""],
               ["Absolute-only", before.get("absolute_only"),
                after.get("absolute_only"), ""],
               ["Sin temporada", before.get("sin_temporada"),
                after.get("sin_temporada"), ""]])
    rows = []
    for stage, label in (("junk", "Basura"), ("blocked", "XXX/adultos"),
                         ("absolute", "Absolute-only"), ("dead", "Muertos")):
        r = out.get(stage, {}) or {}
        if r.get("skipped"):
            rows.append([label, "⏭️", "", "", r.get("nota", "")])
        else:
            rows.append([label, r.get("matched"), r.get("deleted"),
                         r.get("skipped_limit"), r.get("nota", "")])
    rep.table("Etapas", ["Etapa", "Candidatas", "Borradas",
                         "Fuera por tope", "Nota"], rows)
    data = {"stats_antes": before, "stats_despues": after,
            "purge": out, "fuera_por_tope": fuera}
    rep.update(data)
    return data


def _enrich_loop(db: DB, cfg: Settings, dry_run: bool, rounds: int,
                 mode: str, limit_total: int = 0) -> tuple[dict, dict]:
    http = Http()
    cache = LocalCache(cfg.cache_sqlite)
    ctx = Ctx(cfg=cfg, http=http, local=cache, persistent={},
              stats=new_stats())
    totals = {"filas_procesadas": 0, "resueltas": 0, "parciales": 0,
              "sin_match": 0, "errores": 0, "fallbacks": 0,
              "fallback_hits": 0, "ids_descartados": 0,
              "por_proveedor": {}, "ejemplos": []}
    try:
        try:
            expr = cfg.title_column if cfg.title_column != "auto" \
                else db.detect_title_column()
        except DbError:
            expr = "auto"
        remaining = limit_total or 0
        for _ in range(max(1, rounds)):
            if mode == "retry":
                rows = db.get_missing(cfg.batch_size,
                                      cfg.retry_min_age_minutes,
                                      cfg.retry_max_attempts)
            else:
                rows = db.get_to_enrich(cfg.batch_size, cfg.recheck_days,
                                        cfg.only_types, expr)
            if not rows:
                break
            if remaining:
                rows = rows[:remaining]
                remaining -= len(rows)
            results, apply_row = process_batch(db, ctx, rows, dry_run)
            totals["filas_procesadas"] += len(results)
            totals["ids_descartados"] += int(
                (apply_row or {}).get("invalid", 0) or 0)
            for r in results:
                expected = EXPECTED.get(
                    (next((x.get("type") or ""
                           for x in rows if x.get("id") == r.get("id")),
                          "") or "").lower(), ())
                n_has = sum(1 for f in
                            (expected or ("tmdb_id", "imdb_id", "anilist_id",
                                         "kitsu_id", "mal_id"))
                            if r.get(f))
                if not r.get("_has"):
                    totals["sin_match"] += 1
                elif expected and n_has >= len(expected):
                    totals["resueltas"] += 1
                elif n_has:
                    totals["parciales"] += 1
                else:
                    totals["sin_match"] += 1
                if r.get("_fallback"):
                    totals["fallbacks"] += 1
                    if r.get("_has"):
                        totals["fallback_hits"] += 1
                if len(totals["ejemplos"]) < 7 and r.get("_has"):
                    totals["ejemplos"].append(
                        f"{r.get('source')} (conf {r.get('confidence')})")
            if remaining is not None and limit_total and remaining <= 0:
                break
        for prov, st in ctx.stats.items():
            if st["calls"] or st["hits"]:
                totals["por_proveedor"][prov] = dict(st)
        totals["cache_local"] = cache.stats()
        return totals, dict(ctx.stats)
    finally:
        http.close()
        cache.close()


def do_enrich(db: DB, cfg: Settings, rep: Report, dry_run: bool,
              rounds: int, limit_total: int = 0) -> dict:
    totals, providers = _enrich_loop(db, cfg, dry_run, rounds, "enrich",
                                     limit_total)
    rep.title("🆔 IDs (TMDB/IMDb/AniList/Kitsu/MAL)"
              + (" (dry-run)" if dry_run else ""))
    rep.table("Resultado",
              ["Métrica", "Valor"],
              [["Filas procesadas", totals["filas_procesadas"]],
               ["Resueltas", totals["resueltas"]],
               ["Parciales", totals["parciales"]],
               ["Sin match", totals["sin_match"]],
               ["Fallbacks", f"{totals['fallbacks']} "
                              f"({totals['fallback_hits']} con hit)"],
               ["IDs descartados por formato inválido",
                totals["ids_descartados"]]])
    data = {"enrich": totals, "proveedores": providers}
    rep.update(data)
    return data


def do_retry(db: DB, cfg: Settings, rep: Report, dry_run: bool,
             rounds: int) -> dict:
    reset = db.reset_retry(cfg.retry_max_attempts,
                           cfg.retry_min_age_minutes)
    totals, providers = _enrich_loop(db, cfg, dry_run, rounds, "retry")
    rep.title("🔁 Reintentos de NULL" + (" (dry-run)" if dry_run else ""))
    rep.text(f"Cola: {reset.get('nota', '')}")
    rep.table("Resultado",
              ["Métrica", "Valor"],
              [["A la cola", reset.get("queued")],
               ["Agotados", reset.get("exhausted")],
               ["Reintentadas", totals["filas_procesadas"]],
               ["Resueltas", totals["resueltas"]],
               ["Parciales", totals["parciales"]],
               ["Sin match", totals["sin_match"]]])
    data = {"retry_cola": reset, "retry": totals,
            "proveedores_retry": providers}
    rep.update(data)
    return data


def do_best(db: DB, cfg: Settings, rep: Report, budget: Budget,
            dry_run: bool) -> dict:
    before = db.spanish_stats()
    out: dict = {}
    if cfg.keep_best:
        r = db.keep_best(cfg.keep_best_limit, cfg.keep_best_min_seeders,
                         cfg.keep_best_only_types, dry_run,
                         budget.remaining)
        budget.consume(int(r.get("deleted", 0) or 0))
        out["keep_best"] = r
    if cfg.keep_best_es:
        r = db.keep_best_es(cfg, dry_run, budget.remaining)
        budget.consume(int(r.get("deleted", 0) or 0))
        out["keep_best_es"] = r
    gaps = db.spanish_gaps(cfg.spanish_report_limit,
                           cfg.spanish_tokens_extra)
    after = db.spanish_stats()
    es = out.get("keep_best_es", {}) or {}
    rep.title("🇪🇸 Español + deduplicado" + (" (dry-run)" if dry_run else ""))
    rep.table("Cuota de español",
              ["Métrica", "Valor"],
              [["Grupos", es.get("groups")],
               ["Grupos con 2+ en AUDIO", es.get("groups_ok_audio")],
               ["Grupos con audio o subtítulos",
                es.get("groups_ok_spanish")],
               ["Grupos flojos", es.get("groups_low_spanish")],
               ["Conservados POR SUBTITULADO",
                es.get("kept_with_subs")],
               ["Duplicados", es.get("duplicates")],
               ["Borradas", es.get("deleted")],
               ["Fuera por tope", es.get("skipped_limit")]])
    if gaps:
        rep.details("Dónde falta español",
                    "\n".join(f"- **{g.get('obra')}** "
                              f"(T{g.get('season')}E{g.get('episode')}): "
                              f"{g.get('con_audio')} audio, "
                              f"{g.get('con_subs')} subs, "
                              f"{g.get('total')} total" for g in gaps))
    rep.table("Audio/subtítulos en tabla",
              ["Métrica", "Valor"],
              [["🎧 con audio ES", after.get("con_audio_es")],
               ["💬 con subtítulos ES", after.get("con_subs_es")],
               ["Solo subtitulado", after.get("solo_subs")],
               ["Sin español", after.get("sin_espanol")]])
    data = {"spanish_antes": before, "spanish_despues": after,
            "best": out, "spanish_gaps": gaps}
    rep.update(data)
    return data


def do_renumber(db: DB, cfg: Settings, rep: Report, dry_run: bool,
                force_run: bool = False) -> dict:
    if not cfg.renumber_ids and not force_run:
        rep.title("🔢 Reordenar id")
        rep.text("⏭️ omitido (RENUMBER_IDS=false)")
        data = {"renumber": {"omitido": True}}
        rep.update(data)
        return data
    before = db.id_report()
    r = db.renumber(dry_run, cfg.renumber_max_rows,
                    cfg.renumber_ids_force)
    after = db.id_report()
    rep.title("🔢 Reordenar id" + (" (dry-run)" if dry_run else ""))
    rep.text(f"{r.get('nota', '')}")
    rep.table("Columna id",
              ["Métrica", "Antes", "Después"],
              [["Total", before.get("total"), after.get("total")],
               ["Rango", f"{before.get('min_id')}..{before.get('max_id')}",
                f"{after.get('min_id')}..{after.get('max_id')}"],
               ["Huecos", before.get("huecos"), after.get("huecos")],
               ["Listo", before.get("listo"), after.get("listo")]])
    data = {"renumber": {"informe_antes": before, "renumber": r,
                         "informe_despues": after}}
    rep.update(data)
    return data


def do_stats(db: DB, rep: Report) -> dict:
    ids = db.ids_stats()
    quality = db.quality_stats()
    spanish = db.spanish_stats()
    idrep = db.id_report()
    rep.title("📊 Estado de la tabla")
    rep.table("IDs",
              ["Métrica", "Valor"],
              [["Total", ids.get("total")],
               ["Sin ID", ids.get("sin_id")],
               ["TMDB", ids.get("con_tmdb")],
               ["IMDb", ids.get("con_imdb")],
               ["AniList", ids.get("con_anilist")],
               ["Kitsu", ids.get("con_kitsu")],
               ["MAL", ids.get("con_mal")]])
    print(f"total={ids.get('total')} sin_id={ids.get('sin_id')} "
          f"huecos_id={idrep.get('huecos')}", flush=True)
    data = {"stats": {"ids": ids, "calidad": quality,
                      "espanol": spanish, "id": idrep}}
    rep.update(data)
    return data


# ---------------------------------------------------------------------------
# Modos sueltos
# ---------------------------------------------------------------------------
def cmd_purge(cfg: Settings, args) -> int:
    rep = Report()
    db = must_db(cfg, rep)
    try:
        budget = Budget(args.max_deletes if args.max_deletes is not None
                        else cfg.max_deletes_per_run)
        do_purge(db, cfg, rep, budget, args.dry_run or cfg.dry_run)
        rep.set("presupuesto", {"tope": budget.cap, "usado": budget.used,
                                "restante": budget.remaining})
        rep.write(cfg.summary_file, cfg.summary_json,
                  cfg.report_to_step_summary)
        return 0
    except (DbError, RpcMissing) as e:
        _rpc_error_note(rep, cfg, e)
        return 2
    finally:
        db.close()


def cmd_enrich(cfg: Settings, args) -> int:
    rep = Report()
    db = must_db(cfg, rep)
    try:
        do_enrich(db, cfg, rep, args.dry_run or cfg.dry_run,
                  args.rounds or cfg.enrich_rounds, args.limit or 0)
        rep.write(cfg.summary_file, cfg.summary_json,
                  cfg.report_to_step_summary)
        return 0
    except (DbError, RpcMissing) as e:
        _rpc_error_note(rep, cfg, e)
        return 2
    finally:
        db.close()


def cmd_retry(cfg: Settings, args) -> int:
    rep = Report()
    db = must_db(cfg, rep)
    try:
        do_retry(db, cfg, rep, args.dry_run or cfg.dry_run,
                 args.rounds or cfg.retry_rounds)
        rep.write(cfg.summary_file, cfg.summary_json,
                  cfg.report_to_step_summary)
        return 0
    except (DbError, RpcMissing) as e:
        _rpc_error_note(rep, cfg, e)
        return 2
    finally:
        db.close()


def cmd_best(cfg: Settings, args) -> int:
    rep = Report()
    db = must_db(cfg, rep)
    try:
        budget = Budget(args.max_deletes if args.max_deletes is not None
                        else cfg.max_deletes_per_run)
        do_best(db, cfg, rep, budget, args.dry_run or cfg.dry_run)
        rep.set("presupuesto", {"tope": budget.cap, "usado": budget.used,
                                "restante": budget.remaining})
        rep.write(cfg.summary_file, cfg.summary_json,
                  cfg.report_to_step_summary)
        return 0
    except (DbError, RpcMissing) as e:
        _rpc_error_note(rep, cfg, e)
        return 2
    finally:
        db.close()


def cmd_renumber(cfg: Settings, args) -> int:
    rep = Report()
    db = must_db(cfg, rep)
    try:
        do_renumber(db, cfg, rep, args.dry_run or cfg.dry_run,
                    force_run=args.renumber)
        rep.write(cfg.summary_file, cfg.summary_json,
                  cfg.report_to_step_summary)
        return 0
    except (DbError, RpcMissing) as e:
        _rpc_error_note(rep, cfg, e)
        return 2
    finally:
        db.close()


def cmd_stats(cfg: Settings, args) -> int:
    rep = Report()
    db = must_db(cfg, rep)
    try:
        do_stats(db, rep)
        try:
            findings = db.security_audit()
            rep.set("seguridad", findings)
            for f in findings:
                if (f.get("nivel") or "").upper() == "ALTO":
                    rep.text(f"🚨 SEGURIDAD (ALTO): {f.get('hallazgo')}")
        except (DbError, RpcMissing):
            pass
        rep.write(cfg.summary_file, cfg.summary_json,
                  cfg.report_to_step_summary)
        return 0
    except (DbError, RpcMissing) as e:
        _rpc_error_note(rep, cfg, e)
        return 2
    finally:
        db.close()


def cmd_all(cfg: Settings, args) -> int:
    rep = Report()
    t0 = time.time()
    rep.title("🎬 Editor de torrents")
    if args.dry_run or cfg.dry_run:
        rep.text("**DRY-RUN**: no se borra ni se escribe nada.")
    db = must_db(cfg, rep)
    try:
        dry = args.dry_run or cfg.dry_run
        budget = Budget(args.max_deletes if args.max_deletes is not None
                        else cfg.max_deletes_per_run)
        do_purge(db, cfg, rep, budget, dry)
        if budget.exhausted:
            rep.text("⏭️ presupuesto agotado: se omiten enrich/retry/best.")
        else:
            do_enrich(db, cfg, rep, dry, args.rounds or cfg.enrich_rounds,
                      args.limit or 0)
            if cfg.retry_missing:
                do_retry(db, cfg, rep, dry,
                         args.rounds or cfg.retry_rounds)
            do_best(db, cfg, rep, budget, dry)
        do_renumber(db, cfg, rep, dry, force_run=args.renumber)
        do_stats(db, rep)
        try:
            findings = db.security_audit()
            rep.set("seguridad", findings)
            for f in findings:
                if (f.get("nivel") or "").upper() == "ALTO":
                    rep.text(f"🚨 SEGURIDAD (ALTO): {f.get('hallazgo')}")
                elif (f.get("nivel") or "").upper() == "MEDIO":
                    rep.text(f"⚠️ seguridad (medio): {f.get('hallazgo')}")
        except (DbError, RpcMissing) as e:
            rep.text(f"⚠️ seguridad: {e}")
        rep.set("duracion_seg", round(time.time() - t0, 1))
        rep.set("presupuesto", {"tope": budget.cap, "usado": budget.used,
                                "restante": budget.remaining})
        rep.write(cfg.summary_file, cfg.summary_json,
                  cfg.report_to_step_summary)
        print(f"✔ ALL OK en {time.time() - t0:.1f}s "
              f"(borradas: {budget.used})", flush=True)
        return 0
    except (DbError, RpcMissing) as e:
        _rpc_error_note(rep, cfg, e)
        return 2
    finally:
        db.close()


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="src.main",
        description="Editor de torrents: IDs + limpieza + español")
    p.add_argument("mode", choices=["all", "purge", "enrich", "retry",
                                    "best", "renumber", "stats", "doctor",
                                    "selftest"])
    p.add_argument("--dry-run", action="store_true",
                   help="no borra ni escribe nada")
    p.add_argument("--limit", type=int, default=0,
                   help="máximo de filas a enriquecer (0 = todas)")
    p.add_argument("--rounds", type=int, default=0,
                   help="rondas (0 = las de la config)")
    p.add_argument("--max-deletes", type=int, default=None,
                   help="tope de borrado de esta corrida (0 = sin tope)")
    p.add_argument("--renumber", action="store_true",
                   help="fuerza el reordenador de id")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load()
    if args.quiet:
        setup_logging("WARNING")
    elif args.verbose:
        setup_logging("DEBUG")
    else:
        setup_logging(cfg.log_level)
    try:
        if args.mode == "selftest":
            return cmd_selftest(cfg)
        if args.mode == "doctor":
            return cmd_doctor(cfg)
        if args.mode == "stats":
            return cmd_stats(cfg, args)
        if args.mode == "purge":
            return cmd_purge(cfg, args)
        if args.mode == "enrich":
            return cmd_enrich(cfg, args)
        if args.mode == "retry":
            return cmd_retry(cfg, args)
        if args.mode == "best":
            return cmd_best(cfg, args)
        if args.mode == "renumber":
            return cmd_renumber(cfg, args)
        if args.mode == "all":
            return cmd_all(cfg, args)
    except SystemExit as e:
        return int(e.code or 0)
    except KeyboardInterrupt:
        print("interrumpido", flush=True)
        return 130
    except Exception:  # noqa: BLE001 - que no muera sin decir por qué
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
