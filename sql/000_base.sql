-- ============================================================================
--  sql/000_base.sql · esquema base (OPCIONAL, idempotente)
--
--  Asegura que existan la tabla public.torrents y el trigger de updated_at.
--  Si tu tabla ya existe en Supabase (el caso normal), no toca nada: solo
--  añade las columnas que falten. Se puede ejecutar tantas veces como quieras.
-- ============================================================================

create table if not exists public.torrents (
  id               bigint generated always as identity primary key,
  title            text,
  title_text       text,
  type             text,                       -- movie | series | anime
  season           integer,
  episode          integer,
  absolute_episode integer,
  quality          text,                       -- 2160p, 1080p, 720p...
  codec            text,                       -- x264, x265, hevc...
  hdr              text,                       -- hdr, dolby vision...
  size_bytes       bigint,
  seeders          integer default 0,
  audio            text[]  default '{}',       -- idiomas de audio
  subtitles        text[]  default '{}',       -- idiomas de subtítulos
  release_group    text,
  source_tracker   text,
  tmdb_id          integer,
  imdb_id          text,
  anilist_id       integer,
  kitsu_id         integer,
  mal_id           integer,
  ids_checked_at   timestamptz,                -- cuándo se intentó resolver
  ids_source       text,                       -- tmdb, anilist, cache, none...
  ids_confidence   double precision,           -- 0..1
  ids_attempts     integer default 0,          -- fallos consecutivos
  created_at       timestamptz default now(),
  updated_at       timestamptz default now()
);

-- Por si la tabla ya existía con menos columnas, se añaden las que falten.
alter table public.torrents add column if not exists title            text;
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

-- Trigger: mantiene updated_at al día (el reordenador lo apaga y restaura).
create or replace function public.update_torrents_updated_at()
returns trigger language plpgsql as $trg$
begin
  new.updated_at = now();
  return new;
end
$trg$;

do $do$
begin
  if not exists (select 1 from pg_trigger
                 where tgname = 'update_torrents_updated_at'
                   and tgrelid = 'public.torrents'::regclass) then
    create trigger update_torrents_updated_at
      before update on public.torrents
      for each row execute function public.update_torrents_updated_at();
  end if;
end
$do$;
