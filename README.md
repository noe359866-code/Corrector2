# 🎬 Editor de torrents · IDs (TMDB · IMDb · AniList · Kitsu · MAL) + limpieza automática

Pipeline que corre en **GitHub Actions** contra tu tabla `public.torrents` de Supabase:

| # | Etapa | Qué hace |
|---|-------|----------|
| 1 | **Limpieza basura** | Borra títulos vacíos / `readme` / `sample` / sin valor |
| 2 | **Limpieza xxx** | Borra **xxx, OnlyFans, porn, hentai, JAV, brazzers, chaturbate…** (título, grupo y tracker), con allowlist para `xXx (2002/2005/2017)`, `Adult Swim`, `Sex and the City`, etc. |
| 3 | **`absolute_episode`** | Borra series/anime donde el episodio/temporada **solo existe en `absolute_episode`** (`episode is null` y sin `season`) |
| 4 | **IDs por nombre** | Toma `title` (o `title_text`) y resuelve **`tmdb_id`, `imdb_id`, `anilist_id`, `kitsu_id`, `mal_id`** (el anime también se queda con su `imdb_id`) |
| 5 | **Reintento de NULL** | Los que **no obtuvieron ningún ID** vuelven a la cola: se busca con `title_text`, variantes agresivas e **IMDb/TVmaze (sin clave)** hasta agotar `RETRY_MAX_ATTEMPTS` |
| 6 | **Español + dedupe** | Por película/episodio/cap: borra **repetidos**, deja **máximo 3** y garantiza **al menos 2 con español**, en **cascada**: primero **audio en español**; si no hay, **subtitulado en español** |
| 7 | **Mejores torrents** | Regla general opcional: conserva los N mejores por obra+temporada+episodio (seeders → calidad → tamaño) |
| 7b | **Reordenador** | Tras los borrados deja la columna **`id` en 1..N sin huecos** (si borrabas el 3 quedaba 1,2,4) y sincroniza la secuencia identity |
| 8 | **Tope por corrida** | `MAX_DELETES_PER_RUN` limita cuántas filas borra la corrida (ej. **500**); lo que sobra queda para la siguiente |
| 9 | **Reporte** | Tabla antes/después (con **«Fuera por tope»**) + auditoría de español + estado de la columna `id` en el *Step Summary* + `summary.md` / `summary.json` / `run.log` |

Todo el trabajo pesado (rankings, borrados, merges) se ejecuta **dentro de PostgreSQL** vía funciones RPC: no se transfieren miles de filas por HTTP.

---

## 1) Puesta en marcha (5 minutos)

### 1.1 Claves de API

| API | ¿Necesita clave? | Dónde |
|-----|------------------|-------|
| **TMDB** | Sí (gratis) | https://www.themoviedb.org/settings/api → *API Key (v3 auth)* |
| **OMDb** | Sí (gratis, 1.000/día) | https://www.omdbapi.com/apikey.aspx |
| **AniList** | **No** | GraphQL público |
| **Kitsu** | **No** | API pública (header `application/vnd.api+json`, ya implementado) |
| **MAL** | **No** | vía AniList `idMal` / mappings de Kitsu (Jikan como 3.er respaldo) |
| **IMDb (suggestions)** | **No** | `v3.sg.media-imdb.com/suggestion/x/…` — el mismo buscador de imdb.com |
| **TVmaze** | **No** | API pública; además entrega el `thetvdb` de cada serie |

> **Sin ninguna clave** ya se resuelven: `anilist_id`, `kitsu_id`, `mal_id` y `imdb_id` (IMDb suggestions), más el `thetvdb` vía TVmaze.
> Las claves de TMDB/OMDb solo añaden el `tmdb_id` (y sinopsis/portadas si luego quieres).

Verificado en vivo: `The.Matrix → tt0133093`, `Breaking.Bad → tt0903747`, `Dune.Part.Two → tt15239678`,
`Frieren → tt22248376` (IMDb suggestions, confianza 1.00).

### 1.2 Secrets del repositorio (GitHub → Settings → Secrets and variables → Actions)

| Secret | Obligatorio | Valor |
|--------|-------------|-------|
| `SUPABASE_URL` | ✅ | `https://xxxx.supabase.co` (Project Settings → API) |
| `SUPABASE_SERVICE_KEY` | ✅ | clave **service_role** (⚠️ nunca en el navegador) |
| `SUPABASE_DB_URL` | recomendado | Connection string URI (Project Settings → Database) → permite aplicar el SQL automáticamente |
| `TMDB_API_KEY` | recomendado | clave v3 o token v4 de TMDB |
| `OMDB_API_KEY` | opcional | clave de OMDb |

### 1.3 Ejecutar

Actions → **“Editor de torrents (IDs TMDB/AniList/Kitsu/MAL + limpieza + español)”** →
*Run workflow*. También corre **solo cada 6 horas**.

El workflow tiene **3 jobs**:

1. **Pruebas** — `pytest` (65) + `selftest` + valida el YAML y los scripts + levanta un
   **Postgres 16 real** en el runner, aplica `sql/*.sql` **dos veces** (idempotencia) y corre el
   smoke test SQL (63 comprobaciones) + `live_providers` (informativo).
2. **Migraciones** — si existe el secret `SUPABASE_DB_URL`, aplica los 5 archivos SQL y verifica
   que las 20 funciones existen. Si falta, avisa y sigue.
3. **Pipeline** — `doctor` → **limpieza** → **IDs** → **reintentos** → **español/deduplicado**,
   con un paso final *Estado final de la tabla* (`always()`) y un paso que **une los reportes**
   en `summary.md` y los publica en el **Step Summary** + como **artefacto**.

Al lanzarlo a mano puedes elegir:

| Input | Def. | Para qué |
| --- | --- | --- |
| `modo` | `all` | `all`, `purge`, `enrich`, `retry`, `best`, `stats`, `doctor`, `selftest` |
| `dry_run` | false | ver qué haría sin borrar ni escribir nada |
| `title_column` | `auto` | `auto`, `title` o `title_text` |
| `keep_best_es_limit` | 3 | tope de torrents por película/episodio |
| `min_spanish` | 2 | cuántos con español garantizar |
| `allow_subs_fallback` | **true** | **la cascada**: si no hay audio en español, vale el subtitulado |
| `keep_best_es_strict` | false | borrar el grupo si no llega al mínimo |
| `purge_soft_adult` | false | purga agresiva extra |
| `max_deletes` | `0` | **TOPE de filas borradas en esta corrida** (0 = sin tope). Ej.: `500` |
| `renumber_ids` | **true** | reordena la columna `id` a 1..N al terminar (rellena los huecos) |

La primera ejecución la puedes lanzar con `dry_run = true` para ver qué borraría sin tocar nada.

### 1.4 ¿Y si no quieres el SQL automático?

Si no pones `SUPABASE_DB_URL`, abre **Supabase → SQL Editor** y ejecuta en orden:

```
sql/000_base.sql   (opcional, idempotente: asegura tabla/trigger)
sql/001_helpers.sql
sql/002_enrich.sql
sql/003_purge.sql
sql/004_retry_spanish.sql   (reintentos de IDs + español/deduplicado con la cascada)
sql/005_renumber_limit.sql  (TOPE total de filas por corrida + reordenador de la columna id)
sql/006_signature_guard.sql (guardián de firmas: borra versiones viejas de las funciones)
sql/007_permisos.sql        (SEGURIDAD: cierra las funciones del pipeline a la clave pública)
```

Los **ocho** archivos son **idempotentes** (se pueden correr varias veces) y `scripts/apply_sql.sh`
lo hace por ti: aplica los 8 y después comprueba que las **23 funciones** y las columnas de
control (`ids_checked_at`, `ids_source`, `ids_confidence`, `ids_attempts`) existen.

---

## 2) Uso local

```bash
git clone <tu-repo> && cd torrents-enricher
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

cp config.example.env .env
# edita .env con tus claves y cárgalo:
set -a && source .env && set +a

python -m src.main selftest   # pruebas offline (parser + blacklist), sin red ni base
python -m src.main doctor     # revisa credenciales, RPCs y la columna del título
python -m src.main stats      # cuántas filas y cuántos IDs faltan

python -m src.main purge --dry-run    # ver qué borraría
python -m src.main all                # purge -> enrich -> retry -> best (lo que corre el workflow)

# el ejemplo del usuario: "solo quiero que edite 500"
python -m src.main purge --max-deletes 500
MAX_DELETES_PER_RUN=500 python -m src.main all

# la limpieza va por tandas de ids (para no pasarse del timeout de la base)
python -m src.main purge --chunk-rows 5000      # tandas de 5.000 filas
PURGE_CHUNK_ROWS=20000 python -m src.main purge  # lo mismo por variable de entorno

# el reordenador de la columna id (deja 1..N sin huecos)
python -m src.main renumber                 # respeta RENUMBER_IDS
python -m src.main renumber --dry-run       # dice a dónde iría sin tocar nada
RENUMBER_IDS=true RENUMBER_IDS_FORCE=true python -m src.main renumber
```

Otros comandos: `enrich`, `retry`, `best`, `renumber`, `purge`, `stats`, `doctor`, `selftest`.
Flags útiles: `--dry-run`, `--limit 100`, `--rounds 3`, `--max-deletes 500`, `--chunk-rows 20000`,
`--renumber`, `-v`.

### Pruebas

```bash
python -m pytest tests -q            # 68 pruebas: parser, blacklist, matching, pipeline, fallback,
                                     # title_text, tope por corrida, ids saneados, reordenador
                                     # y la limpieza por tandas (timeout, presupuesto, pendientes)
python tests/live_providers.py       # consulta REAL a AniList/Kitsu/Jikan (necesita red)
TEST_DB_URL=postgresql://... python tests/sql_smoke.py   # 63 pruebas del SQL contra Postgres
                                                         # (purgas, keep_best, cascada de español, reintentos,
                                                         #  tope por corrida y reordenador de ids)

bash scripts/test_all.sh --docker    # todo de una (levanta Postgres 16 en Docker)
```

Todas en verde hoy: **68 passed**, **63 OK / 0 fallos**, `live_providers` OK.

### Probar TODO el pipeline sin Supabase (PostgREST simulado)

`tests/mock_postgrest.py` implementa un PostgREST de mentira que ejecuta las mismas RPCs
contra un Postgres local (introspecciona la firma de cada función y usa notación
nombrada + cast, igual que PostgREST real):

```bash
# 1) Postgres local con el esquema
psql "$DB" -f sql/000_base.sql -f sql/001_helpers.sql -f sql/002_enrich.sql -f sql/003_purge.sql
# 2) servidor simulado
MOCK_DB_URL="$DB" MOCK_PORT=8899 python tests/mock_postgrest.py
# 3) pipeline apuntando al mock (usa las APIs reales de anime)
export SUPABASE_URL=http://localhost:8899 SUPABASE_SERVICE_KEY=mock
python -m src.main all
```

### Probar EXACTAMENTE lo que corre en GitHub Actions

`scripts/simulate_workflow.sh` ejecuta los mismos pasos del Action (limpieza → IDs →
reintentos → español) en tu máquina y deja el mismo reporte unido:

```bash
# sin Supabase: Postgres local + PostgREST simulado + datos de ejemplo
psql "$DB" -f tests/fixture_e2e.sql          # 9 filas: duplicados, adulto, absolute_episode, title_text…
bash scripts/simulate_workflow.sh --local    # deja reportes/*.md y summary.md

# contra tu Supabase real (carga tu .env antes)
set -a && source .env && set +a
bash scripts/simulate_workflow.sh
```

`tests/fixture_e2e.sql` trae a propósito: 3 versiones de *The Matrix* (Latino, Castellano y
una en inglés), un grupo de anime **solo subtitulado**, un adulto (`OnlyFans`), una fila con
**solo `absolute_episode`** y una cuyo nombre "interno" está en `title` mientras el release
real vive en `title_text`.

Verificado extremo e2e: la limpieza borró 2 filas, las 7 restantes quedaron con IDs
(`imdb:suggest`, `anilist+kitsu`, +`imdb_id` para anime), la regla de español borró 1
duplicado y conservó **4 por subtitulado** cuando no había audio en español; una segunda
corrida reutilizó el **caché persistente sin una sola llamada a las APIs** y conservó el
`ids_source` original de cada fila.

---

## 2.5) Seguridad: que la clave pública NO pueda borrar nada

En Postgres, **toda función nueva es ejecutable por PUBLIC** (o sea, por cualquiera). En Supabase el
esquema `public` está expuesto por la API REST y la clave `anon` es **pública** (viaja dentro del
addon de Stremio). Sin cerrar esto, cualquiera con esa clave podía llamar a las funciones del
pipeline y **vaciarte la tabla**:

```bash
# así se borraba (comprobado): la clave pública bastaba
POST https://<tu-proyecto>.supabase.co/rest/v1/rpc/purge_junk_torrents  {"p_dry_run": false}
POST https://<tu-proyecto>.supabase.co/rest/v1/rpc/renumber_torrent_ids {"p_start": 500}
```

`sql/007_permisos.sql` (lo aplica `apply_sql.sh`, idempotente) hace tres cosas:

1. `revoke` de las **23 funciones** a `PUBLIC`, `anon` y `authenticated`.
2. `grant` solo a `service_role` (la clave privada, que vive en GitHub Secrets) y a `postgres`.
3. crea `public.torrents_security_audit()`, que revisa: (a) si alguna función del pipeline volvió a
   quedar ejecutable por `anon`/`authenticated`, (b) si esos roles tienen `DELETE`/`INSERT`/`UPDATE`
   sobre `public.torrents`, (c) si hay alguna **política RLS** que deje escribir a la clave pública
   y (d) si RLS está desactivado.

`python -m src.main doctor` (y el job de pruebas del workflow) lo comprueban en cada corrida:

```
🛡️  seguridad: funciones del pipeline cerradas (solo service_role) y RLS activo en public.torrents
🚨 SEGURIDAD (ALTO): el rol anon (clave pública) puede ejecutar ...
```

Extras de esta misma revisión:

* **Nada se borra sin pedirlo**: una fila sin ningún nombre (`title` y `title_text` vacíos) **no** se
  borra salvo que pongas `PURGE_EMPTY_TITLE=true` (un borrado no se deshace).
* **El nombre del release se busca en `title` y, si está vacío, en `title_text`**: antes, con
  `title` vacío en toda la tabla, todas las filas compartían la misma clave, se agrupaban como una
  sola obra y la regla de español borraba todo menos 3. Ahora usa el *título efectivo* y las filas
  sin clave no se agrupan nunca.
* **El reordenador toma un candado** (`lock table ... access exclusive` con `lock_timeout` de 15 s)
  para que un INSERT de tu app no choque con un id a medio asignar.

* **Un ID dudoso ya no puede borrar nada** (el fallo más peligroso que encontramos). El enriquecedor
  prueba variantes del título cuando no encuentra match: `"Obra 2"` → `"Obra"`. Si existe una
  película que se llama literalmente `Obra`, la coincidencia puntúa 1.0 y ese **mismo id** acababa
  escrito en cientos de filas distintas. La limpieza agrupaba por ese id, las veía como "la misma
  obra" y **borraba todo menos el top N** (probado: 402 filas de 1.000). Ahora:
  1. los matches conseguidos con una **variante sintética** del título valen ×0.8 (`PENALIZACION_VARIANTE`),
  2. la caché **no inventa** confianza (guarda la real; si no la tiene, 0.5 = "desconocida"),
  3. y las funciones que borran solo agrupan por un id **de fiar**
     (`torrent_id_es_confiable`: el id ya venía en la tabla, o lo escribimos con confianza ≥ 0.92).
     Si no es de fiar, se agrupa por **título**, que es mucho más conservador.
  Mismo escenario después del arreglo: **11 filas borradas en vez de 402**, y las familias de
  torrents intactas.

---

## 3) Cómo funciona la resolución de IDs

```
                ┌─ movie / series ─► TMDB (título + año) ─► imdb_id (/external_ids) ─► OMDb (respaldo)
title(text) ────┤
                └─ anime ─────────► AniList ─► idMal ─► Kitsu (+mappings) ─► Jikan/MAL ─► TMDB (título canónico)
                                    └─ IMDb suggestions (sin clave) → imdb_id → TMDB /find
```

* **Parseo del release**: `The.Matrix.1999.1080p.BluRay.x264-AMIABLE` → título `The Matrix`, año 1999.
  `[SubsPlease] Jujutsu Kaisen - 24 (1080p)` → `Jujutsu Kaisen`, episodio absoluto 24.
  `Dune.Part.Two.2024.2160p…` → `Dune Part Two` (no confunde “Part” ni el año con un episodio).
* **Variantes de búsqueda**: si el título completo no da un match seguro se prueban variantes
  (`Frieren: Beyond Journey's End` → `Frieren`, sin artículo inicial, primeras 2–4 palabras), exigiendo **más** confianza en las variantes recortadas.
* **Confianza** (`matching.py`): similitud difusa (`rapidfuzz` `token_set_ratio` + `partial_ratio`) + bonus por año exacto. Acepta ≥ 0.82 (≥ 0.90 en variantes laxas).
* **Caminos exactos y gratis** (sin búsqueda): `imdb → tmdb` (`/find`), `tmdb → imdb`, `mal → anilist` (`idMal`), `kitsu → mal/anilist` (mappings), `mal → kitsu` (external links).
* **Nunca pisa** un ID ya bueno: en SQL se usa `coalesce(t.columna, valor_nuevo)`.
* **`title` vs `title_text`**: si la fila tiene el nombre "interno" en `title` y el release real en
  `title_text` (o al revés), el pipeline **mide cuál parece más un nombre de release**
  (`release_signals` / `looks_like_release_name`: nº de marcas 1080p·x264·WEB-DL·S01E02, puntos,
  corchetes…) y busca con ese, guardando el otro como respaldo. Funciona igual con nombres
  separados por espacios y con puntos (`Frieren Beyond Journeys End S01E01 1080p WEB-DL x264`
  y `Frieren.Beyond.Journeys.End.S01E12.1080p.WEB-DL`). Ambos nombres llegan en `title_alt`
  desde SQL (`get_torrents_to_enrich` / `get_torrents_missing_ids`).
* **Fallback agresivo** (para los que quedaron en `NULL`):
  1. el **otro nombre** de la fila (`title_alt`)
  2. variantes agresivas: corta `S01E02`/`Temporada 2`, convierte números romanos (`Rocky II → Rocky 2`), recorta palabras
  3. **IMDb suggestions** (sin clave) → `imdb_id`
  4. **TVmaze** (sin clave) → `imdb_id` + `thetvdb`
  5. `imdb_id → tmdb_id` vía TMDB `/find`
  6. TMDB probando el tipo contrario (`movie ↔ tv`)
  7. **anime**: reintenta AniList/Kitsu/Jikan con el nombre alterno y las variantes agresivas
     (si no, una fila cuyo nombre bueno vive en `title_text` se quedaba sin `anilist_id`,
     `kitsu_id` ni `mal_id`)
  8. OMDb por el título alterno
* **Caché de dos niveles**:
  1. **SQLite local** dentro de la corrida: los mismos títulos se repiten muchísimo (misma película en 1080p/720p/2160p), así que la segunda vez no se consulta nada.
  2. **`title_id_cache` en Supabase**, persistente entre corridas: se **precarga en UNA sola llamada** por ronda (`get_title_cache(p_keys)`) y se alimenta dentro de la misma llamada que escribe los IDs (`apply_torrent_ids`). Los *miss* se reintentan a los 20 días.
  El acierto de caché viaja **con su procedencia**: la fila se escribe con el `ids_source`
  original (p. ej. `imdb:suggest`) y `ids_confidence` 0.95, así que el reporte sigue diciendo
  de dónde salió cada ID aunque no se haya llamado a ninguna API.
  Resultado medido: segunda corrida sobre los mismos títulos ⇒ 3/3 resueltos con **0 peticiones a las APIs**.

### Trabajo incremental (importante)

`001_helpers.sql` añade 3 columnas de control al final de la tabla:

| Columna | Para qué |
|---------|----------|
| `ids_checked_at` | Marca cuándo se intentó resolver. Sin esto, el job repetiría las mismas filas en cada corrida y **nunca avanzaría**. |
| `ids_source` | Origen del match (`tmdb`, `omdb`, `anilist`, `kitsu`, `jikan`, `cache`, `none`) |
| `ids_confidence` | Confianza 0..1 del match |

Si quieres **repasar todo de nuevo**: `python -m src.main enrich --rounds 1` con `RECHECK_DAYS=0`.

---

## 4) Cómo funciona la limpieza

### 4.1 Contenido adulto / xxx

Se evalúa `title + release_group + source_tracker` con **dos reglas** (`src/blacklist.py`):

1. **Frase con límite de palabra** (~200 tokens: `xxx`, `onlyfans`, `porn`, `hentai`, `jav`, `brazzers`, `chaturbate`, `creampie`, `milf`, `18+`, `nsfw`, `xvideos`, `nhentai`…). El matching por frase evita falsos positivos: `xxx` **no** mata a `XXXTentacion`, `anal` no mata a `Analytics`.
2. **Marca pegada (substring) sólo en `release_group` / `source_tracker`**: atrapa trackers/grupos como `xXxTorrents`, `PornHubRips`, `Brazzers-RG` sin arriesgar títulos legítimos.

**Allowlist** (nunca se borran): `xXx (2002)`, `xXx: State of the Union (2005)`, `xXx: Return of Xander Cage (2017)`, `Adult Swim`, `Sex and the City`, `Sex Education`, `The Analytics of Love`…

- Purga extra opt-in: `PURGE_SOFT_ADULT=true` añade `nude`, `erotic`, `playboy`, `webcam`, `swingers`…
- Listas propias: `BLOCKED_TOKENS_EXTRA`, `ALLOW_TOKENS_EXTRA` (separadas por coma).

La misma lógica existe en SQL (`public.norm_torrent_text` + `purge_blocked_torrents`) para que el borrado ocurra en la base.

**Va por tandas, no de golpe.** La purga recorre la tabla en rangos cortos de `id`
(`PURGE_CHUNK_ROWS`, 20.000 por defecto). Motivo: la API REST de Supabase mata cualquier
*statement* que pase de ~8 s con

```
❌ RPC 'purge_blocked_torrents' HTTP 500: {"code":"57014","message":"canceling statement
due to statement timeout"}
```

y antes eso mataba la corrida entera (exit 2) sin llegar a las etapas siguientes. Ahora cada
tanda usa el índice de la primary key (`Index Cond: id >= X AND id <= Y`) y cabe de sobra;
además el SQL se optimizó (cada fila se normaliza 3 veces en vez de ~4 por token, y los ~50
tokens se compilan en una sola regex): **la misma purga es ~40x más rápida** (25 s → 0,6 s
cada 20.000 filas) y borra **exactamente** lo mismo.

Si una tanda se pasa del timeout, se **encoge sola** (a la mitad, hasta `PURGE_CHUNK_MIN`) y
se reintenta. Si ni así cabe, se apunta el rango en `pendientes`, se sigue con la siguiente y
al final el run **avisa y sale con exit 2** en vez de decir "OK" cuando faltó trabajo:

```
⚠️ limpieza INCOMPLETA en: blocked (tandas que se pasaron del timeout de la base).
    Repetir `python -m src.main purge` remata lo que falta.
```

Repetir la corrida es barato: lo ya borrado no se vuelve a mirar.

**Si actualizas el código pero todavía no aplicaste el SQL**, no pasa nada: la purga lo
detecta, avisa (`la base no acepta rangos de id`) y hace la pasada única de siempre. Aplica
`sql/003_purge.sql` (el job de *migraciones* del workflow lo hace solo si tienes
`SUPABASE_DB_URL`) y la siguiente corrida ya irá por tandas.

### 4.2 `absolute_episode`

```sql
type in ('series','anime') and absolute_episode is not null and episode is null
and (season is null)          -- ABSOLUTE_REQUIRE_SEASON_NULL=true
```

* `ABSOLUTE_ONLY_ACTION=delete` (por defecto) → borra la fila.
* `ABSOLUTE_ONLY_ACTION=nullify` → solo limpia `absolute_episode` y conserva la fila (útil si prefieres re-parsearla después).
* `ABSOLUTE_REQUIRE_SEASON_NULL=false` → borra también las que sí tienen temporada (`One.Punch.Man.S01.012` con `episode=null`).

### 4.3 “Los mejores torrents”

`keep_best_torrents(limit, min_seeders, only_types)` agrupa por:

* **obra**: `imdb_id` → `tmdb_id` → `anilist_id` → `kitsu_id` → `mal_id` → título normalizado
* **temporada/episodio**: `season` + (`episode` o `absolute_episode`); las películas se agrupan solo por obra

y ordena por **`seeders` desc → calidad desc (2160p>1440p>1080p>720p>…) → tamaño asc → id desc**, conservando los primeros `KEEP_BEST_LIMIT`.

### 4.4 Español + deduplicado (la regla de "3 máximo, 2 en español")

`keep_best_torrents_es(limit, min_spanish, ...)` agrupa por **obra + temporada + episodio** y:

1. **Deduplica**: borra repetidos exactos (misma calidad + códec + HDR + tamaño ±1 % + grupo).
2. **Tope**: conserva como máximo **`KEEP_BEST_ES_LIMIT`** (por defecto **3**).
3. **Cuota de español**: de esos, garantiza **`KEEP_BEST_ES_MIN_SPANISH`** (por defecto **2**) con
   español, si el grupo los tiene. Si solo hay 1 en español, se conserva ese y se completa con los mejores.

Orden de conservación: **español primero → seeders → calidad (2160p > 1080p > 720p…) → tamaño menor**.

#### La cascada: *si no hay audio, vale el subtitulado* (obligatoria)

Cuando un grupo **no llega al mínimo con audio en español**, la regla **baja al subtitulado**
antes de darlo por perdido (`KEEP_BEST_ES_ALLOW_SUBS_FALLBACK=true`, por defecto). Los peldaños son:

| Peldaño | Qué cuenta | Campo |
| --- | --- | --- |
| 3 | **Audio en español** (Latino, Castellano, Español, `es-*`, `spa`) | `audio[]` |
| 2 | **Subtítulos en español** (`Subtítulos: ES`, `Subtitulado Español`, `SUBS [ES]`, `Latin`) | `subtitles[]` |
| 1 | El **título** lo dice (`latino/castellano/español/vose…`) | `title` |
| 0 | Nada de español | — |

* Primero se colocan **todos** los que tienen audio en español, después los **solo-subtitulados** y
  al final los mejores sin español, siempre hasta el tope (`KEEP_BEST_ES_LIMIT`).
* Si pones `KEEP_BEST_ES_ALLOW_SUBS_FALLBACK=false`, el comportamiento vuelve al de antes: sin audio
  en español no cuenta para la cuota. En modo **estricto** (`KEEP_BEST_ES_STRICT=true`) con la
  cascada apagada, los grupos solo-subtitulados se borran.
* El reporte lo separa en tres columnas: **Grupos con 2+ en AUDIO**, **con audio o subtítulos** y
  **conservados POR SUBTITULADO** — y debajo lista los grupos que aun así se quedaron cortos
  (`report_spanish_gaps` → `con_audio`, `con_subs`).

Extra: para un release **solo subtitulado** (Japonés + `subtitles[]=Español`) llenamos también
`audio[]`/`subtitles[]` como vengan; la cascada **no** inventa audio: solo deja que el subtítulo
cuente para la cuota del grupo.

```sql
-- así queda cada grupo (ejemplo real del test del SQL: 3 grupos + 1 pack):
-- The.Matrix.1999.1080p.BluRay.x264-LAT   (Latino)            KEEP  ← audio ES
-- The.Matrix.1999.1080p.BluRay.x264-LAT2  (Castellano)        KEEP  ← audio ES
-- The.Matrix.1999.1080p.BluRay.x264-LAT2  (duplicado exacto)  BORRA
-- The.Matrix.1999.720p.BluRay.x264-ENG    (English, 95 seed)  KEEP  ← mejor del resto
-- Kimetsu.no.Yaiba.S04E01.1080p.[SUBS ES] (Japonés + subs ES) KEEP  ← POR SUBTITULADO (cascada)
-- Kimetsu.no.Yaiba.S04E01.480p.HDTV       (Japonés + subs ES) BORRA (tope 3 / peor calidad)
-- Jujutsu.Kaisen.S02E05.1080p.SUBS[ES]    (Japonés + subs ES) KEEP  ← POR SUBTITULADO
-- Serie.Rara.Pack.Completo                (sin season/episode) KEEP  ← pack: no se toca
```

**¿Qué cuenta como "español"?** `audio[]` o `subtitles[]` con `es`, `spa`, `esp`, `spanish`,
`español`, `castellano`, `latino`, `vose`… y (opcional) el propio título
(`KEEP_BEST_ES_USE_TITLE_HINT=true`). Función: `public.is_spanish_text(texto, tokens)`.

**Salvaguarda**: series/anime **sin `season`, sin `episode` y sin `absolute_episode`** (packs
especiales) **no se agrupan ni se tocan** — el reporte dice cuántas filas se omitieron (`skipped`).

Opciones:
* `KEEP_BEST_ES_STRICT=true` → borra el grupo completo si no alcanza el mínimo de español (útil si
  solo quieres catálogo en español).
* `SPANISH_TOKENS_EXTRA=es-la,audio latino` → tus propios tokens.
* El reporte lista en un `<details>` **dónde falta español** (`report_spanish_gaps`).

### 4.5 Tope por corrida: «solo quiero que edite 500»

`MAX_DELETES_PER_RUN` (o `--max-deletes 500`, o el input **`max_deletes`** del workflow) pone
un techo **total** de filas borradas **por corrida** (el presupuesto se comparte entre
etapas: lo que gasta la limpieza ya no lo puede gastar la regla de español):

```bash
MAX_DELETES_PER_RUN=500 python -m src.main purge   # borra como mucho 500 y para
python -m src.main purge                           # la siguiente corrida remata el resto
```

* `0` (por defecto) = **sin tope**, como antes.
* El reporte añade la columna **«Fuera por tope»** con las que quedaron pendientes, y cada
  función devuelve `skipped` / `skipped_limit`.
* En la regla de español los **duplicados exactos gastan primero el tope** (son basura segura)
  y el resto del tope se usa para el top-N y la cuota.
* Nada se pierde: lo que no entra en la corrida sigue ahí para la próxima.

### 4.6 Reordenador: la columna `id` siempre 1..N

Borrar deja huecos (1,**2**,3,4 → borras el 3 → 1,2,4). `renumber_torrent_ids()` lo arregla:

```bash
python -m src.main renumber            # deja 1..N conservando el orden
python -m src.main renumber --dry-run  # "se pasaría de 1..105 a 1..100 (5 huecos)"
```

Cómo lo hace bien:

| Detalle | Por qué |
| --- | --- |
| **Dos fases** (id + desfase) | la PRIMARY KEY no es diferible: mover 5→3 chocaría con la fila 3 todavía viva |
| **`setval` de la secuencia** | si no, el siguiente INSERT choca con un id recién asignado |
| **Conserva `updated_at`** | apaga el trigger `update_torrents_updated_at` mientras dura y restaura los valores |
| **Guarda de FK** | si algo apunta a `torrents(id)` **no hace nada** (`saltado=true`), salvo `RENUMBER_IDS_FORCE=true` |
| **`RENUMBER_MAX_ROWS`** | 200 000 por defecto: evita reescribir una tabla gigante por accidente |
| **Idempotente** | si ya está en orden devuelve `ya estaba en orden (1..N)` y no toca nada |
| **`torrents_id_report()`** | informa total, min/max, **huecos** y `listo` (además de las FK que apuntan a la tabla) |

En el workflow es el paso **5/6**, va **después** de limpieza/IDs/español y se puede apagar con
`renumber_ids = false` (o `RENUMBER_IDS=false`).

### 4.7 Reintentos de IDs que quedaron NULL

Muchas filas se quedan sin ID porque el nombre no coincide exactamente. El paso `retry`
(`python -m src.main retry`) hace:

1. `reset_missing_ids_for_retry()` devuelve a la cola las filas con **todos los IDs en NULL**
   (respetando `RETRY_MAX_ATTEMPTS`, por defecto 4 → **nunca entra en bucle infinito**).
2. `get_torrents_missing_ids()` las entrega **con `title` y `title_alt`** (los dos nombres).
3. Se busca con el nombre que parece release + el fallback agresivo descrito arriba.
4. `apply_torrent_ids()` guarda lo que encontró (**aunque sea parcial**) e incrementa `ids_attempts`.

Verificado: una fila con `title = 'Nombre interno 12345'` y
`title_text = 'Frieren.Beyond.Journeys.End.S01E12.1080p.WEB-DL...'` resolvió
`anilist_id=154587`, `kitsu_id=46474`, `mal_id=52991`.

### 4.6 Opcional: torrents muertos

`PURGE_DEAD=true` borra los de **0 seeders** más viejos que `DEAD_OLDER_DAYS`. Desactivado por defecto (es el que más peso libera, pero también el más agresivo).

---

## 5) Variables de entorno

Todas se configuran en `config.example.env` o como *Secrets/Variables* del repo.

| Variable | Def. | Descripción |
|----------|------|-------------|
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | — | obligatorios |
| `SUPABASE_DB_URL` | — | permite aplicar el SQL desde el workflow |
| `TMDB_API_KEY`, `OMDB_API_KEY` | — | metadatos de películas/series |
| `TITLE_COLUMN` | `auto` | `title`, `title_text` o `auto` (auto-detecta la columna invertida) |
| `BATCH_SIZE` | 400 | filas por ronda |
| `ENRICH_ROUNDS` | 25 | rondas por corrida |
| `WORKERS` | 8 | hilos paralelos |
| `RECHECK_DAYS` | 45 | reintentar filas ya chequeadas (0 = todas) |
| `FILL_ALL_IDS` | true | exige todos los IDs por tipo |
| `ONLY_TYPES` | — | `movie,series,anime` para limitar |
| `DRY_RUN` | false | no escribe ni borra |
| `MAX_DELETES_PER_RUN` | 0 | **TOPE TOTAL de filas borradas por corrida** (0 = sin tope; lo que sobre queda para la próxima) |
| `RENUMBER_IDS`, `RENUMBER_IDS_FORCE`, `RENUMBER_MAX_ROWS` | false, false, 200000 | reordenador de `id` (1..N), forzar con FK, tope de seguridad |
| `PURGE_JUNK` / `PURGE_BLOCKED` / `PURGE_ABSOLUTE_ONLY` / `PURGE_DEAD` | true/true/true/false | etapas de limpieza |
| `PURGE_SOFT_ADULT` | false | purga agresiva extra |
| `PURGE_EMPTY_TITLE` | false | borrar también las filas SIN nombre (`title` y `title_text` vacíos). Irreversible |
| `ABSOLUTE_ONLY_ACTION` | delete | `delete` \| `nullify` |
| `ABSOLUTE_REQUIRE_SEASON_NULL` | true | exigir `season is null` |
| `KEEP_BEST`, `KEEP_BEST_LIMIT`, `KEEP_BEST_MIN_SEEDERS`, `KEEP_BEST_ONLY_TYPES` | false, 3, 0, — | “mejores torrents” (regla simple) |
| `KEEP_BEST_ES`, `KEEP_BEST_ES_LIMIT`, `KEEP_BEST_ES_MIN_SPANISH` | true, 3, 2 | regla de español: máximo por episodio y cuántos en español |
| `KEEP_BEST_ES_DEDUPE`, `KEEP_BEST_ES_STRICT`, `KEEP_BEST_ES_USE_TITLE_HINT` | true, false, true | deduplicado / borrar si no hay español / mirar el título |
| `KEEP_BEST_ES_ALLOW_SUBS_FALLBACK` | true | **cascada**: si no hay audio en español, el subtitulado en español cuenta para la cuota |
| `SPANISH_TOKENS_EXTRA`, `SPANISH_REPORT_LIMIT` | —, 15 | tokens propios de español / filas a listar en el reporte |
| `RETRY_MISSING`, `RETRY_MAX_ATTEMPTS`, `RETRY_MIN_AGE_MINUTES`, `RETRY_ROUNDS` | true, 4, 60, 10 | reintentos de IDs NULL |
| `AGGRESSIVE_FALLBACK` | true | usar title_alt + IMDb/TVmaze + variantes |
| `SUMMARY_FILE`, `SUMMARY_JSON` | summary.md/.json | dónde escribe cada paso su reporte (el workflow usa `reportes/N-paso.md`) |
| `REPORT_TO_STEP_SUMMARY` | true | `false` = no volcar cada paso al Step Summary (el workflow los une al final) |
| `BLOCKED_TOKENS_EXTRA`, `ALLOW_TOKENS_EXTRA` | — | listas propias |
| `DEAD_MIN_SEEDERS`, `DEAD_OLDER_DAYS` | 1, 60 | para `PURGE_DEAD` |
| `PURGE_CHUNK_ROWS`, `PURGE_CHUNK_MIN` | 20000, 1000 | filas por tanda de limpieza (y suelo al encoger por timeout). `0` = tabla entera de una vez |

---

## 6) Estructura del repo

```
.
├─ .github/workflows/torrents-editor.yml   # Action: pruebas → migraciones → pipeline → reporte
├─ sql/
│  ├─ 000_base.sql        # esquema base (opcional para pruebas / entornos nuevos)
│  ├─ 001_helpers.sql     # norm_torrent_text + columnas ids_* + índices + caché
│  ├─ 002_enrich.sql      # get_torrents_to_enrich[_expr] (title/title_alt) + apply_torrent_ids + caché + stats
│  ├─ 003_purge.sql       # purge_blocked / absolute_only / junk / dead + keep_best + stats
│  ├─ 004_retry_spanish.sql  # reintentos de NULL + is_spanish_text + keep_best_torrents_es + gaps
│  ├─ 005_renumber_limit.sql # TOPE total de filas por corrida (p_limit/p_max_deletes) + reordenador de ids
│  ├─ 006_signature_guard.sql # guardián de firmas (evita funciones sobrecargadas/ambiguas)
│  └─ 007_permisos.sql    # SEGURIDAD: la clave pública (anon) no puede borrar nada
├─ datos/ · cache/ · logs/ · tmp/ · reportes/   # carpetas del pipeline (con .gitkeep)
├─ src/
│  ├─ main.py             # CLI: all | purge | enrich | retry | best | renumber | stats | doctor | selftest
│  ├─ config.py           # toda la configuración por entorno
│  ├─ db.py               # PostgREST + RPCs (service_role)
│  ├─ enrich.py           # motor de resolución de IDs
│  ├─ titleparse.py       # nombre de release -> {título, año, temporada, episodio}
│  ├─ matching.py         # confianza/umbrales (rapidfuzz)
│  ├─ blacklist.py        # tokens adulto + allowlist
│  ├─ cache.py            # caché SQLite local
│  ├─ purge.py            # llamadas a las RPCs de limpieza
│  ├─ report.py           # reporte Markdown/JSON (incl. sección 🇪🇸 de la cascada)
│  ├─ textnorm.py         # normalización (idéntica a la de SQL)
│  ├─ http_client.py      # reintentos, backoff, circuit breaker
│  └─ providers/          # tmdb · omdb · anilist · kitsu · jikan · imdb (suggestions) · tvmaze
├─ scripts/
│  ├─ apply_sql.sh        # aplica sql/*.sql con psql + verifica las 22 funciones
│  ├─ simulate_workflow.sh # corre en local los MISMOS pasos del Action (--local = sin Supabase)
│  └─ test_all.sh         # pruebas completas (--docker)
├─ tests/                 # test_pipeline.py · sql_smoke.py · live_providers.py · mock_postgrest.py
│                         # fixture_e2e.sql (9 filas realistas: duplicados, adulto, absolute_episode, title_text)
├─ docs/guia.html         # guía visual (resumen del proyecto)
├─ requirements*.txt · config.example.env · .gitignore
```

---

## 7) Notas y solución de problemas

* **`title` vs `title_text`**: si en tu tabla el nombre del release está en `title_text` (o `title` está vacío), el pipeline lo detecta solo (`detect_title_column_swap`) y usa la función `get_torrents_to_enrich_expr`. Puedes forzarlo con `TITLE_COLUMN=title_text`. No renombramos ni borramos columnas: no rompe tu app Stremio.
* **`RPC ... does not exist`** → no aplicaste `sql/*.sql` (o faltó `SUPABASE_DB_URL`). El reporte lo marca con ⚠️.
* **La tabla está vacía después de un `all` en un entorno de pruebas**: revisa `summary.md`; `KEEP_BEST_LIMIT`, `PURGE_DEAD` y `PURGE_SOFT_ADULT` son las palancas más agresivas.
* **Muchos “sin match”**: revisa `ids_source = 'none'` y `ids_attempts`; el paso `retry` los reintenta hasta `RETRY_MAX_ATTEMPTS` con `title_text`, variantes e IMDb/TVmaze. Si el nombre está muy roto, sube `RETRY_MAX_ATTEMPTS` o revisa `title_text`.
* **`title_alt` viene vacío**: tu tabla no tiene `title_text`, así que solo hay un nombre. Es normal.
* **Quiero SOLO las que tengan 2 en español**: `KEEP_BEST_ES_STRICT=true` (borra el grupo si no llega).
* **«Se me desordenaron los ids»**: es lo normal al borrar filas; corre `python -m src.main renumber`
  (o deja `RENUMBER_IDS=true`, que el workflow ya lo hace como paso 5/6).
* **«No quiero que borre todo de golpe»**: `MAX_DELETES_PER_RUN=500` (tope **total** de filas
  borradas en la corrida; lo que sobre queda para la siguiente) y repite corridas.
* **`57014` / «canceling statement due to statement timeout» en la limpieza**: la base mató
  el statement por pasarse del timeout de la API (~8 s). Desde la versión con tandas ya no
  debería pasar (baja `PURGE_CHUNK_ROWS` si tu base es lenta). Si aun así aparece, el reporte
  dice **qué rangos de id** quedaron sin revisar: repite `python -m src.main purge` y los
  remata. Para una purga puntual enorme también puedes correrla desde el SQL Editor con más
  tiempo: `set statement_timeout='10min'; select * from public.purge_blocked_torrents(p_dry_run=>false);`
* **«Apliqué el SQL y sigue fallando»**: aplica `scripts/apply_sql.sh` **completo**. Si aplicas
  `sql/004` *después* de `sql/005`, la función `keep_best_torrents_es` queda con dos firmas
  (Postgres no la reemplaza porque cambió `p_max_deletes`) y las llamadas se vuelven ambiguas.
  El archivo `sql/006_signature_guard.sql` borra las versiones viejas: aplícalo y listo.
* **«¿Es peligroso correr esto?»**: lo más peligroso era la clave pública, y ya está cerrado con
  `sql/007_permisos.sql`. El pipeline borra **solo** lo que pidas (xxx/onlyfans, absolute_episode,
  duplicados y sobrantes del top-N), respeta `MAX_DELETES_PER_RUN`, no toca filas sin nombre ni
  packs sin temporada/episodio, y `DRY_RUN=true` no escribe nada.
* **«La corrida terminó bien pero no hizo nada»**: ya no puede pasar en silencio. Al arrancar se
  hace un *ping* a la base y, si no responde, el proceso sale con error y un mensaje claro
  (revisa `SUPABASE_URL` / `SUPABASE_SERVICE_KEY`). Los IDs con formato inválido se cuentan en el
  reporte como **«IDs descartados por formato inválido»** en vez de tumbarse la fila.
* **El reordenador no hace nada**: revisa `RENUMBER_IDS`, `RENUMBER_MAX_ROWS` y si hay FK hacia
  `torrents(id)` (el RPC devuelve `saltado=true` con el motivo en `nota`).
* **Jikan (MAL) devuelve 504**: es frecuente (MLB/servicio de MAL caído). Por eso el `mal_id` se obtiene primero de `AniList.idMal` y de los *mappings* de Kitsu; Jikan es solo el tercer respaldo.
* **Límites**: TMDB ~50 req/s (holgado), OMDb 1.000/día (por eso la caché), AniList ~30–90 req/min, Jikan ~60 req/min (el cliente auto-limita a ~2,4 req/s).
* **Coste/tiempo**: 20.000 filas ≈ 6–12 min con `WORKERS=8` y caché caliente. El timeout del job es 300 min.
* **Seguridad**: la `service_role` se salta RLS. Vive solo en GitHub Secrets y en tu `.env` local (está en `.gitignore`).

> ⚠️ Este pipeline **borra filas**. Corre primero `modo=purge` + `dry_run=true` y revisa el reporte antes de dejarlo automático.

---

## 8) Licencia / uso

Código de ejemplo para gestionar **tu propia** base de datos de metadatos/torrents. Eres responsable del contenido que indexas y de cumplir las leyes y los términos de servicio de cada API y tracker.
