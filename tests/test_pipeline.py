"""Batería offline: parser, blacklist, matching, pipeline, fallback, SQL files."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import blacklist, matching
from src.cache import LocalCache
from src.config import load
from src.enrich import sanitize_ids
from src.providers import anilist, imdb, kitsu, tvmaze, tmdb
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
