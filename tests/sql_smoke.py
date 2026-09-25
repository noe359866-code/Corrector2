#!/usr/bin/env python3
"""Smoke test del SQL contra Postgres real (TEST_DB_URL o SUPABASE_DB_URL).

Termina con  == N OK, 0 fallos ==  (o exit 1). OJO: borra y crea filas:
úsalo solo contra la base de pruebas (torrents_test), NUNCA Supabase real.
"""
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("TEST_DB_URL") or os.environ.get("SUPABASE_DB_URL")
if not DSN:
    print("ERROR: falta TEST_DB_URL (o SUPABASE_DB_URL)")
    sys.exit(2)
if "supabase" in DSN.lower() and os.environ.get("SMOKE_ALLOW_SUPABASE") != "1":
    print("ERROR: esto borra datos; no lo lances contra Supabase real "
          "(SMOKE_ALLOW_SUPABASE=1 para forzarlo).")
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
ok = 0
fails: list[str] = []


def check(name, cond, detail=""):
    global ok
    if cond:
        ok += 1
        print(f"  ✔ {name}", flush=True)
    else:
        fails.append(name)
        print(f"  ✖ {name} {detail}", flush=True)


def section(n, title):
    print(f"[{n}] {title}", flush=True)


conn = psycopg.connect(DSN, autocommit=True)
cur = conn.cursor(row_factory=dict_row)


def q(sql, params=None):
    cur.execute(sql, params or ())
    try:
        return cur.fetchall()
    except psycopg.ProgrammingError:
        return []


def q1(sql, params=None):
    rows = q(sql, params)
    return rows[0] if rows else {}


def add(title, **kw):
    cols = {"title": title}
    cols.update(kw)
    cur.execute(
        f"insert into public.torrents ({', '.join(cols)}) values "
        f"({', '.join(['%s'] * len(cols))}) returning id",
        list(cols.values()))
    return cur.fetchone()["id"]


def wipe():
    cur.execute("delete from public.torrents")
    cur.execute("delete from public.title_id_cache")


# ------------------------------------------------------------------ [1] ---
section(1, "helpers")
check("norm acentos MAYÚSCULA",
      q1("select public.norm_torrent_text('ÁÉÍÓÚ Ñ Ç Ü') as v")["v"]
      == "aeiou n c u")
check("norm puntuación",
      q1("select public.norm_torrent_text('  Hello,   World! ') as v")["v"]
      == "hello world")
check("effective_title",
      q1("select public.torrent_effective_title('  ', 'Real') as v")["v"]
      == "Real")
check("effective_title null",
      q1("select public.torrent_effective_title(null, null) as v")["v"]
      is None)
check("title_key vacío=null",
      q1("select public.torrent_title_key('') as v")["v"] is None)
check("token_ok no caza analytics",
      q1("select public.torrent_title_token_ok('The Analytics of Love',"
         " 'anal') as v")["v"] is False)
check("token_ok caza palabra",
      q1("select public.torrent_title_token_ok('Anal Tales 2024',"
         " 'anal') as v")["v"] is True)
check("id confiable preexistente",
      q1("select public.torrent_id_es_confiable(0.5, null) as v")["v"] is True)
check("id NO confiable",
      q1("select public.torrent_id_es_confiable(0.5, 'tmdb') as v")["v"]
      is False)
check("id confiable alta confianza",
      q1("select public.torrent_id_es_confiable(0.95, 'tmdb') as v")["v"]
      is True)
check("quality_rank ordena",
      q1("select public.torrent_quality_rank('1080p') as a,"
         " public.torrent_quality_rank('720p') as b")["a"] >
      q1("select public.torrent_quality_rank('720p') as b")["b"])

# ------------------------------------------------------------------ [2] ---
section(2, "enrich: ida y vuelta")
wipe()
r1 = add("The Matrix 1999 1080p", type="movie")
r2 = add("Breaking Bad S05E14", type="series", tmdb_id=1396)
r3 = add("Nombre interno 999", title_text="Frieren S01E12 1080p",
         type="anime")
rows = q("select * from public.get_torrents_to_enrich(10, 45, null)")
check("enrich devuelve 3", len(rows) == 3, f"len={len(rows)}")
alt = [r for r in rows if r["id"] == r3][0]
check("title_alt llega", alt["title"] == "Nombre interno 999"
      and alt["title_alt"] == "Frieren S01E12 1080p", str(alt))
ap = q1("select * from public.apply_torrent_ids(%s::jsonb, false)",
        [f'[{{"id": {r1}, "tmdb_id": 603, "imdb_id": "tt0133093",'
         f' "source": "tmdb", "confidence": 0.95, "cache_key": "k-matrix"}}]'])
check("apply escribe", ap["updated"] == 1 and ap["invalid"] == 0, str(ap))
row = q1("select tmdb_id, imdb_id, ids_confidence, ids_attempts from "
         "public.torrents where id = %s", [r1])
check("ids + confianza", row["tmdb_id"] == 603
      and row["imdb_id"] == "tt0133093"
      and abs(row["ids_confidence"] - 0.95) < 1e-6
      and row["ids_attempts"] == 0, str(row))
q("select * from public.apply_torrent_ids(%s::jsonb, false)",
  [f'[{{"id": {r2}, "tmdb_id": 999999, "source": "x"}}]'])
row2 = q1("select tmdb_id, ids_source from public.torrents where id = %s",
          [r2])
check("coalesce no pisa", row2["tmdb_id"] == 1396, str(row2))
st = q1("select * from public.torrents_ids_stats()")
check("stats", st["total"] == 3 and st["con_tmdb"] == 2, str(st))

# ------------------------------------------------------------------ [3] ---
section(3, "saneado: la basura no rompe el lote")
ap = q1("select * from public.apply_torrent_ids(%s::jsonb, false)",
        [f'[{{"id": {r3}, "imdb_id": "mal!", "tmdb_id": "603.5",'
         f' "mal_id": "12 34"}},'
         f' {{"id": {r1}, "mal_id": " 42 ", "source": "t"}}]'])
check("3 inválidos contados", ap["invalid"] == 3, str(ap))
check("mal con espacios fuera vale",
      q1("select mal_id from public.torrents where id = %s",
         [r1])["mal_id"] == 42)
check("lo malo no se escribe",
      q1("select tmdb_id, imdb_id from public.torrents where id = %s",
         [r3]) == {"tmdb_id": None, "imdb_id": None})

# ------------------------------------------------------------------ [4] ---
section(4, "caché persistente")
hit = q("select * from public.get_title_cache(array['k-matrix', 'nope'])")
check("get_title_cache", len(hit) == 1 and hit[0]["tmdb_id"] == 603
      and hit[0]["source"] == "tmdb", str(hit))
q("select * from public.apply_torrent_ids(%s::jsonb, false)",
  [f'[{{"id": {r3}, "source": "none", "cache_key": "k-miss"}}]'])
miss = q1("select found from public.title_id_cache "
          "where cache_key = 'k-miss'")
check("miss anotado", miss.get("found") is False, str(miss))
q("select * from public.apply_torrent_ids(%s::jsonb, false)",
  [f'[{{"id": {r3}, "tmdb_id": 1, "source": "t", "cache_key": "k-miss"}}]'])
miss2 = q1("select found, tmdb_id from public.title_id_cache "
           "where cache_key = 'k-miss'")
check("un hit pisa al miss", miss2.get("found") is True
      and miss2.get("tmdb_id") == 1, str(miss2))

# ------------------------------------------------------------------ [5] ---
section(5, "reintentos de NULL")
r4 = add("Serie Rara S01E01 720p", type="series")
cur.execute("update public.torrents set ids_checked_at = now() - "
            "interval '2 hours', ids_attempts = 1 where id = %s", [r4])
r5 = add("Agotada S01E01", type="series")
cur.execute("update public.torrents set ids_attempts = 99 where id = %s",
            [r5])
missing = q("select * from public.get_torrents_missing_ids(10, 60, 4)")
ids = {r["id"] for r in missing}
check("missing trae la vieja y no la agotada", r4 in ids and r5 not in ids,
      str(ids))
rs = q1("select * from public.reset_missing_ids_for_retry(4, 60)")
check("reset cola+agotados", rs["queued"] >= 1 and rs["exhausted"] >= 1,
      str(rs))

# ------------------------------------------------------------------ [6] ---
section(6, "purga basura")
rj = add("README")
d = q1("select * from public.purge_junk_torrents(true, 0, false)")
check("junk dry no borra", d["matched"] >= 1 and d["deleted"] == 0, str(d))
d = q1("select * from public.purge_junk_torrents(false, 0, false)")
check("junk borra", d["deleted"] >= 1
      and q1("select count(*) as c from public.torrents where id = %s",
             [rj])["c"] == 0, str(d))
re_ = add("", title_text="")
d = q1("select * from public.purge_junk_torrents(false, 0, false)")
check("sin nombre se conserva",
      q1("select count(*) as c from public.torrents where id = %s",
         [re_])["c"] == 1, str(d))
d = q1("select * from public.purge_junk_torrents(false, 0, true)")
check("sin nombre solo con permiso", d["deleted"] >= 1, str(d))

# ------------------------------------------------------------------ [7] ---
section(7, "purga xxx/adultos")
ra = add("OnlyFans Pack 2024 xxx")
rk = add("xXx 2002 1080p BluRay")
rt = add("XXXTentacion Mix 2024")
rg = add("Show S01E01 1080p", release_group="xXxTorrents")
d = q1("select * from public.purge_blocked_torrents(null, null, null,"
       " false, 0)")
left = {r["id"] for r in q("select id from public.torrents")}
check("adulto fuera, allowlist dentro",
      ra not in left and rg not in left and rk in left and rt in left,
      f"deleted={d['deleted']}")
rn1 = add("onlyfans leak 1")
rn2 = add("onlyfans leak 2")
d = q1("select * from public.purge_blocked_torrents(null, null, null,"
       " false, 1)")
check("tope: matched 2 deleted 1 skipped 1",
      d["matched"] == 2 and d["deleted"] == 1
      and d["skipped"] == 1 and d["skipped_limit"] == 1, str(d))
q("select * from public.purge_blocked_torrents(null, null, null, false, 0)")
rs = add("Nude Art Film")
d = q1("select * from public.purge_blocked_torrents(null, null, null,"
       " false, 0)")
check("soft apagada conserva",
      q1("select count(*) as c from public.torrents where id = %s",
         [rs])["c"] == 1)
d = q1("select * from public.purge_blocked_torrents(null, %s, null,"
       " false, 0)", [["nude"]])
check("soft encendida borra", d["deleted"] >= 1, str(d))

# ------------------------------------------------------------------ [8] ---
section(8, "purga absolute_only")
a1 = add("Anime X 12 1080p", type="anime", absolute_episode=12)
a2 = add("Anime Y S01 12", type="anime", season=1, absolute_episode=12)
a3 = add("Anime Z 13", type="anime", absolute_episode=13)
d = q1("select * from public.purge_absolute_only_torrents('delete', true,"
       " false, 0)")
left = {r["id"] for r in q("select id from public.torrents")}
check("absolute sin season fuera, con season dentro",
      a1 not in left and a3 not in left and a2 in left, str(d))
d = q1("select * from public.purge_absolute_only_torrents('nullify', false,"
       " false, 0)")
check("nullify limpia y conserva",
      d["updated"] >= 1
      and q1("select absolute_episode from public.torrents where id = %s",
             [a2])["absolute_episode"] is None, str(d))

# ------------------------------------------------------------------ [9] ---
section(9, "purga muertos")
m1 = add("Vieja sin seeders", seeders=0)
cur.execute("update public.torrents set created_at = now() - "
            "interval '70 days' where id = %s", [m1])
m2 = add("Nueva sin seeders", seeders=0)
d = q1("select * from public.purge_dead_torrents(1, 60, false, 0)")
left = {r["id"] for r in q("select id from public.torrents")}
check("muerta vieja fuera, nueva dentro", m1 not in left and m2 in left,
      str(d))

# ----------------------------------------------------------------- [10] ---
section(10, "keep_best")


def same_obra(n, seeders, quality="1080p"):
    return add(f"Obra KB {n} {quality}", type="movie",
               imdb_id="tt1000001", ids_source="tmdb",
               ids_confidence=0.99, seeders=seeders, quality=quality)


k1 = same_obra(1, 10)
k2 = same_obra(2, 50)
k3 = same_obra(3, 30)
k4 = same_obra(4, 5)
d = q1("select * from public.keep_best_torrents(3, 0, null, false, 0)")
left = {r["id"] for r in q("select id from public.torrents")}
check("top-3 borra la de menos seeders",
      k1 in left and k4 not in left and d["deleted"] == 1, str(d))
k5 = same_obra(5, 1)
k6 = same_obra(6, 2)
k7 = same_obra(7, 3)
d = q1("select * from public.keep_best_torrents(3, 0, null, false, 1)")
check("keep_best respeta tope",
      d["deleted"] == 1 and d["skipped_limit"] == 2, str(d))
q("select * from public.keep_best_torrents(3, 0, null, false, 0)")

# ----------------------------------------------------------------- [11] ---
section(11, "español: cascada audio -> subtitulado")


def spanish_fixture():
    wipe()
    f = {}
    f["m1"] = add("The Matrix 1999 1080p LAT", type="movie",
                  imdb_id="tt0133093", ids_source="tmdb",
                  ids_confidence=0.99, quality="1080p", codec="x264",
                  size_bytes=1000, release_group="LAT", seeders=10,
                  audio=["Latino"])
    f["m2"] = add("The Matrix 1999 1080p CAST", type="movie",
                  imdb_id="tt0133093", ids_source="tmdb",
                  ids_confidence=0.99, quality="1080p", codec="x264",
                  size_bytes=2000, release_group="CAST", seeders=9,
                  audio=["Castellano"])
    f["m3"] = add("The Matrix 1999 1080p LAT dup", type="movie",
                  imdb_id="tt0133093", ids_source="tmdb",
                  ids_confidence=0.99, quality="1080p", codec="x264",
                  size_bytes=1000, release_group="LAT", seeders=8,
                  audio=["Latino"])
    f["m4"] = add("The Matrix 1999 720p ENG", type="movie",
                  imdb_id="tt0133093", ids_source="tmdb",
                  ids_confidence=0.99, quality="720p", codec="x264",
                  size_bytes=500, release_group="ENG", seeders=95,
                  audio=["English"])
    f["k1"] = add("Kimetsu no Yaiba S04E01 1080p SUBS ES", type="series",
                  season=4, episode=1, quality="1080p", seeders=20,
                  audio=["Japanese"], subtitles=["Español"])
    f["k2"] = add("Kimetsu no Yaiba S04E01 480p", type="series",
                  season=4, episode=1, quality="480p", seeders=5,
                  audio=["Japanese"], subtitles=["Español"])
    f["j1"] = add("Jujutsu Kaisen S02E05 1080p SUBS ES", type="series",
                  season=2, episode=5, quality="1080p", seeders=7,
                  audio=["Japanese"], subtitles=["SUBS [ES]"])
    f["b1"] = add("Breaking Bad S05E14 LAT", type="series",
                  season=5, episode=14, quality="1080p", seeders=11,
                  audio=["Latino"])
    f["b2"] = add("Breaking Bad S05E14 ENG", type="series",
                  season=5, episode=14, quality="1080p", seeders=50,
                  audio=["English"])
    f["pack"] = add("Serie Rara Pack Completo", type="series",
                    quality="1080p", seeders=3, audio=["English"])
    return f


f = spanish_fixture()
d = q1("select * from public.keep_best_torrents_es(3, 2, 0, null, true,"
       " false, true, true, null, true, 0)")
check("es dry no borra", d["deleted"] == 0, str({k: d[k] for k in
      ("groups", "matched", "deleted")}))
d = q1("select * from public.keep_best_torrents_es(3, 2, 0, null, true,"
       " false, true, true, null, false, 0)")
left = {r["id"] for r in q("select id from public.torrents")}
check("dupe exacto fuera", f["m3"] not in left, str(d))
check("cascada conserva por subtitulado",
      d["kept_with_subs"] >= 1 and f["k1"] in left, str(d))
check("grupo audio ok", d["groups_ok_audio"] >= 1, str(d))
check("pack omitido", f["pack"] in left and d["skipped"] >= 1, str(d))

f = spanish_fixture()
d = q1("select * from public.keep_best_torrents_es(3, 2, 0, null, true,"
       " false, true, false, null, false, 0)")
check("cascada apagada -> kept_with_subs=0", d["kept_with_subs"] == 0,
      str(d))

f = spanish_fixture()
d = q1("select * from public.keep_best_torrents_es(3, 2, 0, null, true,"
       " true, true, true, null, false, 0)")
left = {r["id"] for r in q("select id from public.torrents")}
check("estricto borra grupo solo-subs", f["j1"] not in left, str(d))

check("is_spanish SUBS ES",
      q1("select public.is_spanish_text('SUBS [ES]') as v")["v"] is True)
check("is_spanish English=false",
      q1("select public.is_spanish_text('English') as v")["v"] is False)
check("is_spanish Estonian=false",
      q1("select public.is_spanish_text('Estonian') as v")["v"] is False)
gaps = q("select * from public.report_spanish_gaps(15, null)")
check("gaps responde", isinstance(gaps, list))
ss = q1("select * from public.torrents_spanish_stats()")
check("spanish stats", ss["total"] >= 1, str(ss))

# ----------------------------------------------------------------- [12] ---
section(12, "título vacío no se agrupa ni se borra")
wipe()
for i in range(8):
    add("", title_text=f"Release Distinto {i} S01E{i:02d} 1080p WEB-DL",
        type="series", season=1, episode=i, audio=["English"])
add("", title_text="")
d = q1("select * from public.keep_best_torrents_es(3, 2, 0, null, true,"
       " false, true, true, null, false, 0)")
check("8 distintos: nada borrado", d["deleted"] == 0, str(d))
check("sin nombre omitida",
      q1("select count(*) as c from public.torrents")["c"] == 9)

# ----------------------------------------------------------------- [13] ---
section(13, "permisos: anon NO puede borrar")
has_anon = q1("select count(*) as c from pg_roles "
              "where rolname = 'anon'")["c"] > 0
if has_anon:
    n = q1("select count(*) as c from pg_proc p "
           "join pg_namespace n on n.oid = p.pronamespace "
           "where n.nspname = 'public' and p.proname in "
           "('purge_junk_torrents','purge_blocked_torrents',"
           "'purge_absolute_only_torrents','purge_dead_torrents',"
           "'keep_best_torrents','keep_best_torrents_es',"
           "'renumber_torrent_ids','apply_torrent_ids') "
           "and (has_function_privilege('anon', p.oid, 'execute') "
           "or has_function_privilege('authenticated', p.oid, "
           "'execute'))")["c"]
    check("0 destructivas para anon/auth", n == 0, f"abiertas={n}")
    c2 = psycopg.connect(DSN, autocommit=True)
    c2.execute("set role anon")
    try:
        c2.execute("select * from public.purge_junk_torrents(true, 0, false)")
        denied = False
    except psycopg.errors.InsufficientPrivilege:
        denied = True
    except psycopg.Error as e:
        denied = "denied" in str(e).lower()
    c2.close()
    check("anon recibe permission denied", denied)
else:
    check("roles anon (skip: no existen)", True)
audit = q("select * from public.torrents_security_audit()")
check("auditoría responde sin ALTO",
      len(audit) >= 1 and all(r["nivel"] != "ALTO" for r in audit),
      str(audit))

# ----------------------------------------------------------------- [14] ---
section(14, "reordenador de id")
wipe()
cur.execute("truncate public.torrents restart identity")
ids9 = [add(f"Renum {i}") for i in range(9)]
# huecos en 2,3,7 (como si se hubieran borrado): quedan 1,4,5,6,8,9
for doomed in (ids9[1], ids9[2], ids9[6]):
    cur.execute("delete from public.torrents where id = %s", [doomed])
rep = q1("select * from public.torrents_id_report()")
check("informe: 6 total 3 huecos", rep["total"] == 6
      and rep["huecos"] == 3 and rep["listo"] is False, str(rep))
dry = q1("select * from public.renumber_torrent_ids(true, 200000, false, 1)")
check("dry dice 1..9 -> 1..6",
      "1..9" in dry["nota"] and "1..6" in dry["nota"], str(dry["nota"]))
upd_before = q1("select updated_at from public.torrents where id = 4")
real = q1("select * from public.renumber_torrent_ids(false, 200000, false, 1)")
now_ids = [r["id"] for r in q("select id from public.torrents order by id")]
check("renumera a 1..6", real["updated"] == 6 and now_ids == [1, 2, 3, 4, 5, 6],
      str((real, now_ids)))
upd_after = q1("select updated_at from public.torrents where id = 2")
check("updated_at conservado",  # la fila 4 pasó a la 2
      str(upd_before["updated_at"]) == str(upd_after["updated_at"]),
      f"{upd_before['updated_at']} vs {upd_after['updated_at']}")
nxt = add("Siguiente")
check("secuencia sincronizada (id 7)", nxt == 7, f"id={nxt}")
noop = q1("select * from public.renumber_torrent_ids(false, 200000, false, 1)")
check("segunda pasada no-op", noop["updated"] == 0
      and "ya estaba en orden" in noop["nota"], str(noop["nota"]))

# ----------------------------------------------------------------- [15] ---
section(15, "todo el SQL en una transacción + rollback no toca nada")
before_fns = q1("select count(*) as c from pg_proc "
                "where pronamespace = 'public'::regnamespace")["c"]
before_rows = q1("select count(*) as c from public.torrents")["c"]
conn2 = psycopg.connect(DSN)  # sin autocommit = una transacción
try:
    with conn2.cursor() as c:
        for f in sorted((ROOT / "sql").glob("*.sql")):
            c.execute(f.read_text(encoding="utf-8"))
    conn2.rollback()
except Exception as e:  # noqa: BLE001
    conn2.rollback()
    check("aplica entero en transacción", False, str(e)[:200])
else:
    check("aplica entero en transacción", True)
finally:
    conn2.close()
after_fns = q1("select count(*) as c from pg_proc "
               "where pronamespace = 'public'::regnamespace")["c"]
after_rows = q1("select count(*) as c from public.torrents")["c"]
check("rollback no toca nada",
      before_fns == after_fns and before_rows == after_rows)

# ----------------------------------------------------------------- [16] ---
section(16, "ninguna función sobrecargada")
names = ["norm_torrent_text", "torrent_id_es_confiable",
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
multi = q("select proname, count(*) as c from pg_proc "
          "where pronamespace = 'public'::regnamespace "
          "and proname = any(%s) group by 1 having count(*) > 1", [names])
check("una sola firma por función", len(multi) == 0, str(multi))

wipe()
print(f"== {ok} OK, {len(fails)} fallos ==", flush=True)
sys.exit(1 if fails else 0)
