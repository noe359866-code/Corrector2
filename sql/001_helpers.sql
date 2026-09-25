-- ============================================================================
--  sql/001_helpers.sql · normalización + columnas de control + índices + caché
--  (idempotente: se puede ejecutar tantas veces como quieras)
--
--  Funciones:
--    norm_torrent_text(text) ............. minúsculas, sin acentos, [a-z0-9 ]
--    torrent_effective_title(a, b) ....... primer nombre no vacío (title/text)
--    torrent_title_key(text) ............. clave de agrupación (null si vacío)
--    torrent_title_token_ok(text, token) .. token con límite de palabra
--    torrent_id_es_confiable(conf, src) .. ¿se puede agrupar por id?
--    torrent_quality_rank(calidad) ....... 2160p=60 ... 360p=10, resto 0
-- ============================================================================

-- Red de seguridad: si alguien aplica este archivo sin el 000, la tabla mínima.
create table if not exists public.torrents (
  id    bigint generated always as identity primary key,
  title text
);

alter table public.torrents add column if not exists title_text       text;
alter table public.torrents add column if not exists type             text;
alter table public.torrents add column if not exists season           integer;
alter table public.torrents add column if not exists episode          integer;
alter table public.torrents add column if not exists absolute_episode integer;
alter table public.torrents add column if not exists quality          text;
alter table public.torrents add column if not exists codec            text;
alter table public.torrents add column if not exists hdr              text;
alter table public.torrents add column if not exists size_bytes       bigint;
alter table public.torrents add column if not exists seeders          integer default 0;
alter table public.torrents add column if not exists audio            text[] default '{}';
alter table public.torrents add column if not exists subtitles        text[] default '{}';
alter table public.torrents add column if not exists release_group    text;
alter table public.torrents add column if not exists source_tracker   text;
alter table public.torrents add column if not exists tmdb_id          integer;
alter table public.torrents add column if not exists imdb_id          text;
alter table public.torrents add column if not exists anilist_id       integer;
alter table public.torrents add column if not exists kitsu_id         integer;
alter table public.torrents add column if not exists mal_id           integer;
alter table public.torrents add column if not exists ids_checked_at   timestamptz;
alter table public.torrents add column if not exists ids_source       text;
alter table public.torrents add column if not exists ids_confidence   double precision;
alter table public.torrents add column if not exists ids_attempts     integer default 0;
alter table public.torrents add column if not exists created_at       timestamptz default now();
alter table public.torrents add column if not exists updated_at       timestamptz default now();

-- ---------------------------------------------------------------------------
-- Normalización (idéntica a src/textnorm.py: minúsculas, sin acentos,
-- solo [a-z0-9] separados por un espacio). '' si entra null o vacío.
-- ---------------------------------------------------------------------------
create or replace function public.norm_torrent_text(p_text text)
returns text language sql immutable as $fn$
  select coalesce(nullif(trim(both ' ' from regexp_replace(
    translate(lower(coalesce(p_text, '')),
      'áéíóúýüñàèìòùâêîôûäëïöüçÿ',
      'aeiouyunaeiouaeiouaeioucy'),
    '[^a-z0-9]+', ' ', 'g')), ''), '');
$fn$;

-- Primer nombre no vacío (title, si no title_text). null si no hay ninguno.
create or replace function public.torrent_effective_title(p_title text, p_title_text text)
returns text language sql immutable as $fn$
  select nullif(coalesce(nullif(btrim(coalesce(p_title, '')), ''),
                         nullif(btrim(coalesce(p_title_text, '')), '')), '');
$fn$;

-- Clave de agrupación por título. null = "sin clave: no agrupar nunca".
create or replace function public.torrent_title_key(p_text text)
returns text language sql immutable as $fn$
  select nullif(public.norm_torrent_text(
           public.torrent_effective_title(p_text, null)), '');
$fn$;

-- ¿Aparece el token con límite de palabra? ('anal' NO caza 'analytics').
create or replace function public.torrent_title_token_ok(p_text text, p_token text)
returns boolean language sql immutable as $fn$
  select t <> '' and k <> '' and (' ' || t || ' ') like '% ' || k || ' %'
    from (select public.norm_torrent_text(p_text)  as t,
                 public.norm_torrent_text(p_token) as k) s;
$fn$;

-- ¿Es un id DE FIAR para agrupar (y borrar por duplicado)?
-- Sí si el id ya venía en la tabla (source null/externo) o lo escribimos
-- con confianza >= 0.92. Si no, se agrupa por TÍTULO (conservador).
create or replace function public.torrent_id_es_confiable(
  p_confidence double precision, p_source text)
returns boolean language sql immutable as $fn$
  select p_source is null
      or lower(coalesce(p_source, '')) in
         ('manual', 'externo', 'externa', 'tabla', 'preexistente')
      or coalesce(p_confidence, 0) >= 0.92;
$fn$;

-- Ranking de calidad para ordenar de mejor a peor.
create or replace function public.torrent_quality_rank(p_quality text)
returns integer language sql immutable as $fn$
  select case
           when q like '%2160%' or q like '%4k%' or q like '%uhd%' then 60
           when q like '%1440%' then 50
           when q like '%1080%' then 40
           when q like '%720%'  then 30
           when q like '%480%'  then 20
           when q like '%360%' or q like '%240%' then 10
           else 0
         end
    from (select lower(coalesce(p_quality, '')) as q) s;
$fn$;

-- ---------------------------------------------------------------------------
-- Índices (todos IF NOT EXISTS: no molestan si ya existen).
-- ---------------------------------------------------------------------------
create index if not exists torrents_ids_checked_at_idx on public.torrents (ids_checked_at);
create index if not exists torrents_tmdb_idx    on public.torrents (tmdb_id);
create index if not exists torrents_imdb_idx    on public.torrents (imdb_id);
create index if not exists torrents_anilist_idx on public.torrents (anilist_id);
create index if not exists torrents_kitsu_idx   on public.torrents (kitsu_id);
create index if not exists torrents_mal_idx     on public.torrents (mal_id);
create index if not exists torrents_type_idx    on public.torrents (type);
create index if not exists torrents_title_key_idx on public.torrents
  ((public.torrent_title_key(public.torrent_effective_title(title, title_text))));

-- ---------------------------------------------------------------------------
-- Caché persistente de títulos -> IDs (entre corridas). Los MISS (found=false)
-- se reintentan a los 20 días (lo decide Python mirando updated_at).
-- ---------------------------------------------------------------------------
create table if not exists public.title_id_cache (
  cache_key    text primary key,
  tmdb_id      integer,
  imdb_id      text,
  anilist_id   integer,
  kitsu_id     integer,
  mal_id       integer,
  source       text,
  confidence   double precision,
  found        boolean default true,
  created_at   timestamptz default now(),
  updated_at   timestamptz default now()
);

alter table public.title_id_cache add column if not exists tmdb_id    integer;
alter table public.title_id_cache add column if not exists imdb_id    text;
alter table public.title_id_cache add column if not exists anilist_id integer;
alter table public.title_id_cache add column if not exists kitsu_id   integer;
alter table public.title_id_cache add column if not exists mal_id     integer;
alter table public.title_id_cache add column if not exists source     text;
alter table public.title_id_cache add column if not exists confidence double precision;
alter table public.title_id_cache add column if not exists found      boolean default true;
alter table public.title_id_cache add column if not exists created_at timestamptz default now();
alter table public.title_id_cache add column if not exists updated_at timestamptz default now();
update public.title_id_cache set found = true where found is null;
