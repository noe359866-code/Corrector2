"""Batería offline: parser, blacklist, matching, pipeline, fallback, SQL files."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import blacklist, matching
from src.cache import LocalCache
from src.config import Settings, load
from src.enrich import sanitize_ids
from src.db import DbError, RpcMissing
from src.main import Budget
from src.providers import anilist, imdb, kitsu, tvmaze, tmdb
from src.purge import _por_tandas, etapas_incompletas, run_purge
from src.report import Report
from src.textnorm import normalize, title_key, token_ok
from src.titleparse import (looks_like_release_name, parse_release,
                            pick_release_name, release_signals)

ROOT = Path(__file__).resolve().parent.parent
SQL = ROOT / "sql"


# ---------------------------------------------------------------- parser ---
class TestTitleParse:
    def test_movie(self):
        p = parse_release("The.Matrix.1999.1080p.BluRay.x264-AMIABLE")
        assert p["title"] == "The Matrix"
        assert p["year"] == 1999
        assert p["quality"] == "1080p"
        assert p["group"] == "AMIABLE"

    def test_anime_subsplease(self):
        p = parse_release("[SubsPlease] Jujutsu Kaisen - 24 (1080p)")
        assert "Jujutsu Kaisen" in p["title"]
        assert p["absolute"] == 24
        assert p["group"] == "SubsPlease"

    def test_part_two_no_es_episodio(self):
        p = parse_release("Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.H.264")
        assert p["title"] == "Dune Part Two"
        assert p["year"] == 2024
        assert p["episode"] is None and p["absolute"] is None

    def test_s01e02(self):
        p = parse_release("Breaking.Bad.S05E14.1080p.BluRay.x264")
        assert p["season"] == 5 and p["episode"] == 14
        assert "Breaking Bad" in p["title"]

    def test_1x02(self):
        p = parse_release("Show.Name.1x02.720p.HDTV")
        assert p["season"] == 1 and p["episode"] == 2

    def test_vacio_no_rompe(self):
        p = parse_release("")
        assert p["title"] == ""


# ---------------------------------------------------------- release name ---
class TestReleaseName:
    def test_si(self):
        assert looks_like_release_name(
            "Frieren Beyond Journeys End S01E01 1080p WEB-DL x264")

    def test_no(self):
        assert not looks_like_release_name("Nombre interno 12345")

    def test_signals(self):
        assert release_signals("The Matrix") == 0
        assert release_signals(
            "Show.S01E01.1080p.WEB-DL.x264-GRP") >= 3

    def test_pick(self):
        main, alt = pick_release_name(
            "Nombre interno 12345",
            "Frieren.Beyond.Journeys.End.S01E12.1080p.WEB-DL.x264")
        assert "Frieren" in main
        assert alt == "Nombre interno 12345"


# -------------------------------------------------------------- matching ---
class TestMatching:
    def test_identico(self):
        assert matching.similarity("Dune", "Dune") == 1.0

    def test_bonus_ano(self):
        a = matching.confidence("Dune", "Dune", 2021, 2021)
        b = matching.confidence("Dune", "Dune", 2021, 1984)
        assert a > b

    def test_penaliza_variante(self):
        base = matching.confidence("Frieren", "Frieren")
        vari = matching.confidence("Frieren", "Frieren", variant=True)
        assert vari == pytest.approx(base * 0.8)

    def test_umbrales(self):
        assert matching.accepted(0.95)
        assert not matching.accepted(0.5)
        assert not matching.accepted(0.85, lax=True)
        assert matching.accepted(0.95, lax=True)

    def test_variants(self):
        vs = matching.variants("Frieren: Beyond Journey's End")
        assert vs[0][0] == "Frieren: Beyond Journey's End"
        assert any("Frieren" in v and lax for v, lax in vs[1:])

    def test_aggressive_roman(self):
        assert "Rocky 2" in matching.aggressive_variants("Rocky II")


# ------------------------------------------------------------- blacklist ---
class TestBlacklist:
    def test_onlyfans(self):
        blocked, _ = blacklist.is_blocked("OnlyFans pack 2024 xxx")
        assert blocked

    def test_xxxtentacion_vive(self):
        assert not blacklist.is_blocked("XXXTentacion - Look At Me")[0]

    def test_analytics_vive(self):
        assert not blacklist.is_blocked("The Analytics of Love S01")[0]

    def test_allow_xxx_2002(self):
        assert not blacklist.is_blocked("xXx 2002 1080p BluRay")[0]

    def test_allow_adult_swim(self):
        assert not blacklist.is_blocked("Adult Swim Show S02E01")[0]

    def test_allow_sex_education(self):
        assert not blacklist.is_blocked("Sex Education S03 1080p")[0]

    def test_grupo_pegado(self):
        blocked, why = blacklist.is_blocked("Show S01", group="xXxTorrents")
        assert blocked and "grupo" in why

    def test_soft_apagado(self):
        assert not blacklist.is_blocked("Nude Beach Documentary")[0]

    def test_soft_encendido(self):
        assert blacklist.is_blocked("Nude Beach Rips", soft=True)[0]

    def test_18_mas(self):
        assert blacklist.is_blocked("Show 18+ UNCENSORED")[0]


# -------------------------------------------------------------- textnorm ---
class TestTextnorm:
    def test_acentos(self):
        assert normalize("ÁÉÍÓÚ Ñ Ç Ü") == "aeiou n c u"

    def test_token_ok(self):
        assert token_ok("The Analytics of Love", "anal") is False
        assert token_ok("Anal Tales 2024", "anal") is True

    def test_title_key_vacio(self):
        assert title_key("") is None
        assert title_key("  ") is None


# --------------------------------------------------------------- saneo ---
class TestSanitize:
    def test_acepta(self):
        clean, inv, _ = sanitize_ids({"tmdb_id": 603,
                                      "imdb_id": "tt0133093",
                                      "mal_id": " 52991 "})
        assert inv == 0 and clean == {"tmdb_id": 603,
                                      "imdb_id": "tt0133093",
                                      "mal_id": 52991}

    def test_rechaza(self):
        clean, inv, fields = sanitize_ids({"tmdb_id": "603.5",
                                           "imdb_id": "tt-abc",
                                           "mal_id": -3,
                                           "kitsu_id": True})
        assert inv == 4 and clean == {} and set(fields) == {
            "tmdb_id", "imdb_id", "mal_id", "kitsu_id"}

    def test_vacios_no_cuentan(self):
        clean, inv, _ = sanitize_ids({"tmdb_id": None, "imdb_id": "  ",
                                      "mal_id": ""})
        assert inv == 0 and clean == {}


# ----------------------------------------------------------------- caché ---
class TestCache:
    def test_roundtrip(self, tmp_path):
        cache = LocalCache(str(tmp_path / "c.sqlite"))
        cache.set("k", {"tmdb_id": 1}, "tmdb", 0.9)
        hit = cache.get("k")
        assert hit["ids"] == {"tmdb_id": 1}
        assert cache.get("otra") is None
        cache.set_miss("m")
        assert cache.is_miss("m")
        assert cache.stats()["titulos_cache"] == 1
        cache.close()


# ------------------------------------------------------ providers (parse) ---
class TestProvidersParse:
    def test_imdb(self):
        r = imdb.parse_suggestions(
            {"d": [{"id": "tt0133093", "l": "The Matrix", "y": 1999},
                   {"id": "nm00000", "l": "Nope"}]},
            "The Matrix", 1999)
        assert len(r) == 1
        assert r[0].ids["imdb_id"] == "tt0133093"
        assert r[0].confidence >= 0.9

    def test_anilist(self):
        r = anilist.parse_response(
            {"data": {"Page": {"media": [
                {"id": 154587, "idMal": 52991, "seasonYear": 2023,
                 "title": {"romaji": "Frieren",
                           "english": "Frieren: Beyond Journey's End"}}]}}},
            "Frieren", 2023)
        assert r[0].ids == {"anilist_id": 154587, "mal_id": 52991}

    def test_kitsu_mappings(self):
        ids = kitsu.parse_mappings(
            {"data": [
                {"attributes": {"externalSite": "myanimelist",
                               "externalId": "52991"}},
                {"attributes": {"externalSite": "anilist",
                               "externalId": "154587"}}]})
        assert ids == {"mal_id": 52991, "anilist_id": 154587}

    def test_tvmaze(self):
        r = tvmaze.parse_search(
            [{"show": {"name": "Breaking Bad", "premiered": "2008-01-20",
                       "externals": {"imdb": "tt0903747", "tvdb": 81189}}}],
            "Breaking Bad")
        assert r[0].ids["imdb_id"] == "tt0903747"
        assert r[0].ids["thetvdb"] == 81189

    def test_tmdb(self):
        r = tmdb.parse_search(
            {"results": [{"id": 603, "title": "The Matrix",
                         "release_date": "1999-03-31"}]},
            "The Matrix", 1999, "movie")
        assert r[0].ids == {"tmdb_id": 603}
        assert r[0].confidence >= 0.9


# ---------------------------------------------------------------- reporte ---
class TestReport:
    def test_escribe_md_json(self, tmp_path):
        rep = Report()
        rep.title("T")
        rep.table("M", ["a", "b"], [[1, 2]])
        rep.set("k", {"n": 1})
        md = str(tmp_path / "r.md")
        js = str(tmp_path / "r.json")
        rep.write(md, js, to_step_summary=False)
        assert "| a | b |" in Path(md).read_text(encoding="utf-8")
        assert json.loads(Path(js).read_text(encoding="utf-8"))["k"] == {"n": 1}


# ----------------------------------------------------------------- config ---
class TestConfig:
    def test_defaults(self):
        cfg = load()
        assert cfg.batch_size == 400
        assert cfg.keep_best_es_limit == 3
        assert cfg.title_column == "auto"

    def test_bool_env(self, monkeypatch):
        monkeypatch.setenv("DRY_RUN", "true")
        monkeypatch.setenv("KEEP_BEST_ES_ALLOW_SUBS_FALLBACK", "false")
        cfg = load()
        assert cfg.dry_run is True
        assert cfg.keep_best_es_allow_subs_fallback is False


# ------------------------------------------------- anti-deriva del SQL ---
EXPECTED_FNS = ["norm_torrent_text", "torrent_id_es_confiable",
                "torrent_title_key", "torrent_title_token_ok",
                "get_torrents_to_enrich", "get_torrents_to_enrich_expr",
                "get_torrents_missing_ids", "reset_missing_ids_for_retry",
                "apply_torrent_ids", "get_title_cache",
                "purge_blocked_torrents", "purge_absolute_only_torrents",
                "purge_junk_torrents", "purge_dead_torrents",
                "keep_best_torrents", "keep_best_torrents_es",
                "is_spanish_text", "report_spanish_gaps",
                "torrents_ids_stats", "torrents_quality_stats",
                "torrents_spanish_stats", "renumber_torrent_ids",
                "torrents_id_report", "torrents_security_audit"]


class TestSqlFiles:
    def test_existen_los_8(self):
        for i, n in [(0, "base"), (1, "helpers"), (2, "enrich"),
                     (3, "purge"), (4, "retry_spanish"),
                     (5, "renumber_limit"), (6, "signature_guard"),
                     (7, "permisos")]:
            assert (SQL / f"00{i}_{n}.sql").exists(), f"falta 00{i}_{n}.sql"

    def test_idempotentes(self):
        for f in sorted(SQL.glob("*.sql")):
            text = f.read_text(encoding="utf-8")
            assert "create or replace function" in text or \
                f.stem.startswith("006") or "if not exists" in text.lower(), f

    def test_apply_definido_una_vez(self):
        n = sum(t.read_text(encoding="utf-8").count(
            "function public.apply_torrent_ids(") for t in SQL.glob("*.sql"))
        assert n == 1

    def test_007_cubre_todo(self):
        s007 = (SQL / "007_permisos.sql").read_text(encoding="utf-8")
        for fn in EXPECTED_FNS:
            assert fn in s007, fn

    def test_006_cubre_todo(self):
        s006 = (SQL / "006_signature_guard.sql").read_text(encoding="utf-8")
        for fn in EXPECTED_FNS:
            assert fn in s006, fn

    def test_tope_en_firmas(self):
        s004 = (SQL / "004_retry_spanish.sql").read_text(encoding="utf-8")
        assert "p_max_deletes" in s004
        s003 = (SQL / "003_purge.sql").read_text(encoding="utf-8")
        assert "p_limit" in s003


# --------------------------------------------------------------- workflow ---
class TestWorkflow:
    def test_main_yml(self):
        yaml = pytest.importorskip("yaml")
        data = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "main.yml").read_text(
                encoding="utf-8"))
        assert len(data["jobs"]) >= 3
        inputs = data[True]["workflow_dispatch"]["inputs"]
        for req in ("modo", "dry_run", "max_deletes", "renumber_ids",
                    "allow_subs_fallback", "purge_empty"):
            assert req in inputs, req
        assert "renumber" in inputs["modo"]["options"]


# ------------------------------------------------- limpieza por tandas ---
class DbRango:
    """Base de mentira: solo sabe el rango de ids que tiene la tabla."""

    def __init__(self, lo=1, hi=100):
        self.lo, self.hi = lo, hi

    def id_bounds(self):
        return (self.lo, self.hi)


class DbPurge(DbRango):
    """Base de mentira con las 4 RPCs de limpieza."""

    def __init__(self, lo=1, hi=5):
        super().__init__(lo, hi)
        self.vistas = []

    def purge_junk(self, dry_run, limit, purge_empty,
                   min_id=None, max_id=None):
        self.vistas.append(("junk", min_id, max_id, limit))
        return {"matched": 2, "deleted": 1, "skipped_limit": 0, "sample": []}

    def purge_blocked(self, dry_run, limit, tokens=None, soft_tokens=None,
                      allow=None, min_id=None, max_id=None):
        self.vistas.append(("blocked", min_id, max_id, limit, tokens))
        return {"matched": 3, "deleted": 2, "skipped_limit": 0, "sample": []}

    def purge_absolute(self, action, require_season_null, dry_run, limit,
                       min_id=None, max_id=None):
        self.vistas.append(("absolute", min_id, max_id, limit))
        return {"matched": 1, "deleted": 0, "updated": 1, "skipped_limit": 0,
                "sample": []}

    def purge_dead(self, min_seeders, older_days, dry_run, limit,
                   min_id=None, max_id=None):
        self.vistas.append(("dead", min_id, max_id, limit))
        return {"matched": 4, "deleted": 4, "skipped_limit": 0, "sample": []}


def _cfg(**kw):
    base = dict(purge_junk=False, purge_blocked=False, purge_absolute_only=False,
                purge_dead=False, purge_soft_adult=False,
                purge_empty_title=False, purge_chunk_rows=10,
                purge_chunk_min=2, absolute_only_action="delete")
    base.update(kw)
    return Settings(**base)


class TestPurgePorTandas:
    def _ok(self, matched=3, deleted=1):
        return {"matched": matched, "deleted": deleted, "skipped_limit": 0,
                "sample": []}

    def test_tandas_cubren_toda_la_tabla(self):
        vistas = []

        def llamada(a, b, limite):
            vistas.append((a, b))
            return self._ok()

        out = _por_tandas(DbRango(1, 25), llamada, Budget(0), True, 10, 2,
                          "basura", "candidatas", "borradas")
        assert vistas == [(1, 10), (11, 20), (21, 25)]
        assert out["matched"] == 9 and out["deleted"] == 3
        assert out["tandas"] == 3 and out["errores"] == 0
        assert out["nota"].startswith("basura: 9 candidatas, 3 borradas")

    def test_timeout_encoge_la_tanda_y_reintenta(self):
        buenas = []
        intentos = []

        def llamada(a, b, limite):
            intentos.append(b - a + 1)
            if b - a + 1 > 4:
                raise DbError("RPC 'purge_blocked_torrents' HTTP 500: "
                              '{"code":"57014","message":"canceling '
                              'statement due to statement timeout"}')
            buenas.append((a, b))
            return self._ok()

        out = _por_tandas(DbRango(1, 20), llamada, Budget(0), True, 10, 2,
                          "bloqueados", "candidatos")
        assert out["errores"] == 0, out
        assert out["deleted"] == 10
        # probó 10, encogió a 5, y a partir de ahí fue de 2 en 2
        assert intentos[0] == 10 and intentos[1] == 5
        assert all(t <= 4 for t in intentos[2:])
        assert [b for _a, b in buenas] == [2, 4, 6, 8, 10, 12, 14, 16, 18, 20]

    def test_tanda_irrecuperable_se_anota_y_se_sigue(self):
        def llamada(a, b, limite):
            if a == 5:
                raise DbError("HTTP 500 57014 cancelling statement due to "
                              "statement timeout")
            return self._ok()

        out = _por_tandas(DbRango(1, 10), llamada, Budget(0), True, 4, 2,
                          "basura", "candidatas")
        assert out["errores"] == 1
        assert out["pendientes"][0][0] == 5
        assert out["deleted"] == 3          # (1-4), (7-8) y (9-10)
        assert "OJO" in out["nota"]
        assert etapas_incompletas({"basura": out}) == ["basura"]

    def test_error_de_red_se_reintenta_una_vez(self, monkeypatch):
        monkeypatch.setattr("src.purge.time.sleep", lambda _s: None)
        n = {"v": 0}

        def llamada(a, b, limite):
            n["v"] += 1
            if n["v"] == 1:
                raise DbError("RPC 'x': sin conexión (Connection error)")
            return self._ok()

        out = _por_tandas(DbRango(1, 10), llamada, Budget(0), True, 10, 2,
                          "basura", "candidatas")
        assert n["v"] == 2 and out["errores"] == 0 and out["deleted"] == 1

    def test_presupuesto_agotado_para_el_recorrido(self):
        def llamada(a, b, limite):
            return self._ok()

        presupuesto = Budget(3)
        out = _por_tandas(DbRango(1, 100), llamada, presupuesto, True, 10, 2,
                          "basura", "candidatas")
        assert presupuesto.used == 3 and out["tandas"] == 3
        assert out["parado_por_presupuesto"] is True
        assert "parcial" in out["nota"]

    def test_tabla_vacia_no_llama_a_la_rpc(self):
        def llamada(a, b, limite):
            raise AssertionError("no debería llamarse")

        out = _por_tandas(DbRango(1, 0), llamada, Budget(0), True, 10, 2,
                          "basura", "candidatas")
        assert out["matched"] == 0 and "vacía" in out["nota"]

    def test_sql_viejo_cae_a_una_sola_pasada(self):
        """Código nuevo + base sin p_min_id: no rompe, avisa y sigue."""
        vistas = []

        def llamada(a, b, limite):
            vistas.append((a, b))
            if a is not None:            # la base no conoce p_min_id
                raise RpcMissing("RPC 'purge_blocked_torrents' HTTP 404: "
                                 "Could not find the function")
            return self._ok(9, 9)

        out = _por_tandas(DbRango(1, 5000), llamada, Budget(0), True, 1000, 2,
                          "bloqueados", "candidatos", "borrados")
        assert vistas == [(1, 1000), (None, None)]   # 1 intento + pasada única
        assert out["tandas"] == 1 and out["errores"] == 0
        assert out["deleted"] == 9

    def test_sin_tandas_una_sola_llamada(self):
        vistas = []

        def llamada(a, b, limite):
            vistas.append((a, b, limite))
            return self._ok(5, 5)

        out = _por_tandas(DbRango(1, 1000), llamada, Budget(0), True, 0, 2,
                          "basura", "candidatas")
        assert vistas == [(None, None, 0)] and out["tandas"] == 1
        assert out["deleted"] == 5


class TestRunPurge:
    def test_tandas_y_acumulado(self):
        db = DbPurge(1, 25)
        out = run_purge(db, _cfg(purge_junk=True, purge_blocked=True,
                                 purge_absolute_only=True, purge_dead=True),
                        Budget(0), False)
        assert out["junk"]["deleted"] == 3        # 3 tandas x 1
        assert out["blocked"]["deleted"] == 6     # 3 tandas x 2
        assert etapas_incompletas(out) == []
        rangos = [v[1:3] for v in db.vistas if v[0] == "blocked"]
        assert rangos == [(1, 10), (11, 20), (21, 25)]

    def test_desactivadas_se_saltan(self):
        db = DbPurge(1, 5)
        out = run_purge(db, _cfg(purge_junk=True), Budget(0), True)
        assert out["junk"]["tandas"] == 1
        for etapa in ("blocked", "absolute", "dead"):
            assert out[etapa]["skipped"] is True
            assert "desactivada" in out[etapa]["nota"]

    def test_etapa_rota_no_tumba_las_demas(self):
        class DbRota(DbPurge):
            def purge_blocked(self, dry_run, limit, tokens=None,
                              soft_tokens=None, allow=None,
                              min_id=None, max_id=None):
                if min_id == 1:                  # solo la primera tanda
                    raise DbError("RPC 'purge_blocked_torrents' HTTP 500: "
                                  "57014 canceling statement due to "
                                  "statement timeout")
                return super().purge_blocked(dry_run, limit, tokens,
                                             soft_tokens, allow, min_id,
                                             max_id)

        db = DbRota(1, 30)
        out = run_purge(db, _cfg(purge_junk=True, purge_blocked=True),
                        Budget(0), True)
        assert out["blocked"]["errores"] == 1
        assert out["junk"]["deleted"] == 3        # junk sí corrió entera
        assert etapas_incompletas(out) == ["blocked"]
        assert "OJO" in out["blocked"]["nota"]

    def test_etapa_rota_en_todas_las_tandas_las_anota(self):
        class DbRota(DbPurge):
            def purge_blocked(self, *a, **k):
                raise DbError("HTTP 500 57014 statement timeout")

        out = run_purge(DbRota(1, 30), _cfg(purge_blocked=True,
                                            purge_chunk_rows=10,
                                            purge_chunk_min=10),
                        Budget(0), True)
        # una tanda de 10 sobre 1..30 = 3 tandas, las 3 apuntadas
        assert out["blocked"]["errores"] == 3
        assert len(out["blocked"]["pendientes"]) == 3
        assert out["blocked"]["matched"] == 0
        assert etapas_incompletas(out) == ["blocked"]

    def test_no_queda_sin_revisar_si_todo_va_bien(self):
        out = run_purge(DbPurge(1, 25), _cfg(purge_junk=True,
                                             purge_blocked=True), Budget(0), True)
        assert etapas_incompletas(out) == []


class TestDbLimpieza:
    """El driver manda p_min_id/p_max_id (y id_bounds se lee con orden)."""

    def _db_con_mock(self, handler):
        import httpx
        from src.db import DB
        db = DB("http://test.local", "clave")
        db.c = httpx.Client(transport=httpx.MockTransport(handler),
                            base_url="http://test.local")
        return db

    def test_id_bounds(self):
        import httpx

        def handler(request):
            orden = request.url.params.get("order")
            return httpx.Response(200, json=[{"id": 7 if orden == "id" else 999}])

        db = self._db_con_mock(handler)
        assert db.id_bounds() == (7, 999)

    def test_id_bounds_tabla_vacia(self):
        import httpx
        db = self._db_con_mock(lambda r: httpx.Response(200, json=[]))
        assert db.id_bounds() == (0, -1)

    def test_purge_manda_el_rango(self):
        import json

        import httpx
        cuerpos = []

        def handler(request):
            cuerpos.append(json.loads(request.read() or b"{}"))
            return httpx.Response(200, json=[{"matched": 1, "deleted": 1}])

        db = self._db_con_mock(handler)
        db.purge_blocked(False, 5, ["xxx"], ["nude"], ["pack"], 100, 199)
        assert cuerpos[-1] == {"p_tokens": ["xxx"], "p_soft_tokens": ["nude"],
                               "p_allow": ["pack"], "p_dry_run": False,
                               "p_limit": 5, "p_min_id": 100,
                               "p_max_id": 199}
        db.purge_junk(False, 0, True, 100, 199)
        assert cuerpos[-1]["p_min_id"] == 100 and cuerpos[-1]["p_max_id"] == 199
        db.purge_absolute("delete", True, False, 0, 1, 9)
        assert cuerpos[-1]["p_min_id"] == 1 and cuerpos[-1]["p_max_id"] == 9
        db.purge_dead(1, 60, False, 0, 1, 9)
        assert cuerpos[-1]["p_min_id"] == 1 and cuerpos[-1]["p_max_id"] == 9

    def test_purge_sin_rango_no_manda_los_args(self):
        """Sin rango NO se mandan p_min_id/p_max_id.

        Así una base con el SQL viejo (sin esos argumentos) responde en vez
        de dar 404: el driver degrada a una sola pasada.
        """
        import json

        import httpx
        cuerpos = []

        def handler(request):
            cuerpos.append(json.loads(request.read() or b"{}"))
            return httpx.Response(200, json=[{"matched": 0, "deleted": 0}])

        db = self._db_con_mock(handler)
        db.purge_junk(True, 0, False)
        db.purge_blocked(True, 0)
        db.purge_dead(1, 60, True, 0)
        assert all("p_min_id" not in c and "p_max_id" not in c
                   for c in cuerpos), cuerpos
        db.purge_junk(True, 0, False, 5, None)      # solo el mínimo
        assert cuerpos[-1]["p_min_id"] == 5
        assert "p_max_id" not in cuerpos[-1]


class TestConfigChunk:
    def test_por_defecto(self):
        c = load()
        assert c.purge_chunk_rows == 20000
        assert c.purge_chunk_min == 1000

    def test_desde_entorno(self, monkeypatch):
        monkeypatch.setenv("PURGE_CHUNK_ROWS", "5000")
        monkeypatch.setenv("PURGE_CHUNK_MIN", "500")
        c = load()
        assert c.purge_chunk_rows == 5000
        assert c.purge_chunk_min == 500

    def test_cero_apaga_las_tandas(self, monkeypatch):
        monkeypatch.setenv("PURGE_CHUNK_ROWS", "0")
        assert load().purge_chunk_rows == 0
