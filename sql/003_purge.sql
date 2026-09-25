-- ============================================================================
--  sql/003_purge.sql · limpieza + mejores torrents (idempotente)
--
--  purge_junk_torrents ............ basura (readme/sample/trailer/test...)
--  purge_blocked_torrents ......... xxx/adultos (tokens + allowlist)
--  purge_absolute_only_torrents .... episodio SOLO en absolute_episode
--  purge_dead_torrents ............. 0 seeders y viejos (opt-in)
--  keep_best_torrents ............. top-N por obra+temporada+episodio
--  torrents_quality_stats ......... foto de la tabla (tipos, calidades...)
--
--  Todas las purgas: p_dry_run (no toca nada), p_limit (tope de borrado,
--  0 = sin tope) y devuelven matched/deleted/skipped/skipped_limit/sample.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Basura: títulos de relleno. Las filas SIN nombre solo se borran con
-- p_purge_empty=true (un borrado no se deshace).
-- ---------------------------------------------------------------------------
create or replace function public.purge_junk_torrents(
  p_dry_run boolean default true, p_limit integer default 0,
  p_purge_empty boolean default false)
returns table(matched integer, deleted integer, skipped integer,
              skipped_limit integer, sample jsonb, nota text)
language plpgsql as $fn$
declare
  v_matched integer; v_deleted integer := 0; v_skipped_limit integer;
  v_sample jsonb; v_cap integer;
begin
  drop table if exists _junk;
  create temp table _junk on commit drop as
  select t.id, public.torrent_effective_title(t.title, t.title_text) as titulo
    from public.torrents t
   where (
          -- nombre reconocible de relleno (coincidencia EXACTA: conservador)
          public.norm_torrent_text(
            public.torrent_effective_title(t.title, t.title_text))
            in ('readme', 'sample', 'trailer', 'test', 'placeholder',
                'rarbg', 'ettv', 'eztv', 'yts', 'yify')
          or public.torrent_title_token_ok(
            public.torrent_effective_title(t.title, t.title_text), 'readme')
        )
     or (coalesce(p_purge_empty, false)
         and public.torrent_effective_title(t.title, t.title_text) is null);

  select count(*) into v_matched from _junk;
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'title', titulo)
                            order by id), '[]'::jsonb)
    into v_sample
    from (select id, titulo from _junk order by id limit 5) s;

  v_cap := case when coalesce(p_limit, 0) > 0
                then least(p_limit, v_matched) else v_matched end;
  v_skipped_limit := v_matched - v_cap;

  if not coalesce(p_dry_run, true) and v_cap > 0 then
    delete from public.torrents t
     using (select id from _junk order by id limit v_cap) d
     where t.id = d.id;
    get diagnostics v_deleted = row_count;
  end if;

  return query select v_matched, v_deleted, v_matched - v_deleted,
    v_skipped_limit, v_sample,
    format('basura: %s candidatas, %s borradas', v_matched, v_deleted)::text;
end
$fn$;

-- ---------------------------------------------------------------------------
-- Contenido adulto: frase con límite de palabra en (título+grupo+tracker),
-- marca pegada (substring) SOLO en grupo/tracker. La allowlist siempre gana.
-- p_tokens null = lista interna; p_soft_tokens null = purga suave apagada.
-- ---------------------------------------------------------------------------
create or replace function public.purge_blocked_torrents(
  p_tokens text[] default null, p_soft_tokens text[] default null,
  p_allow text[] default null,
  p_dry_run boolean default true, p_limit integer default 0)
returns table(matched integer, deleted integer, skipped integer,
              skipped_limit integer, sample jsonb, nota text)
language plpgsql as $fn$
declare
  c_core  constant text[] := array[
    'xxx', 'onlyfans', 'porn', 'porno', 'hentai', 'jav', 'brazzers',
    'chaturbate', 'creampie', 'milf', 'nsfw', 'xvideos', 'xvideo', 'xnxx',
    'xhamster', 'pornhub', 'redtube', 'youporn', 'ahegao', 'bukkake',
    'gangbang', 'orgy', 'bdsm', 'fetish', 'escort', 'camgirl', 'stripchat',
    'livejasmin', 'myfreecams', 'fansly', 'manyvids', 'clips4sale',
    'naughtyamerica', 'realitykings', 'bangbros', 'mofos', 'teamskeet',
    'blacked', 'tushy', 'vixen', 'anal', 'blowjob', 'handjob', 'cumshot',
    'stepmom', 'stepsis', 'taboo', 'incest', 'adult', 'sex', '18+'];
  c_allow constant text[] := array[
    'xxx 2002', 'xxx 2005', 'xxx 2017', 'xander cage', 'state of the union',
    'adult swim', 'sex and the city', 'sex education',
    'analytics', 'the analytics of love'];
  v_matched integer; v_deleted integer := 0; v_skipped_limit integer;
  v_sample jsonb; v_cap integer;
begin
  drop table if exists _btoks; drop table if exists _ballow; drop table if exists _bad;
  -- tokens normalizados una sola vez (los raros, como '18+', van por raw)
  create temp table _btoks on commit drop as
  select tok, public.norm_torrent_text(tok) as ntok,
         (tok ~ '[^a-zA-Z0-9 ]') as raro
    from unnest(coalesce(p_tokens, c_core) || coalesce(p_soft_tokens, '{}')) tok;
  delete from _btoks where not raro and (ntok is null or ntok = '');
  create temp table _ballow on commit drop as
  select distinct public.norm_torrent_text(a) as atok
    from unnest(c_allow || coalesce(p_allow, '{}')) a;
  delete from _ballow where atok is null or atok = '';

  create temp table _bad on commit drop as
  select t.id, public.torrent_effective_title(t.title, t.title_text) as titulo
    from public.torrents t
   where exists (
           select 1 from _btoks b
            where (b.raro
                     and strpos(lower(coalesce(t.title, '') || ' '
                                      || coalesce(t.release_group, '') || ' '
                                      || coalesce(t.source_tracker, '')),
                                 lower(b.tok)) > 0)
               or (not b.raro
                     and (public.torrent_title_token_ok(
                            coalesce(t.title, '') || ' '
                            || coalesce(t.title_text, '') || ' '
                            || coalesce(t.release_group, '') || ' '
                            || coalesce(t.source_tracker, ''), b.tok)
                       or (replace(public.norm_torrent_text(
                              coalesce(t.release_group, '') || ' '
                              || coalesce(t.source_tracker, '')), ' ', '') <> ''
                           and strpos(replace(public.norm_torrent_text(
                              coalesce(t.release_group, '') || ' '
                              || coalesce(t.source_tracker, '')), ' ', ''),
                                       replace(b.ntok, ' ', '')) > 0))))
     and not exists (
           select 1 from _ballow a
            where strpos(public.norm_torrent_text(
                          public.torrent_effective_title(t.title, t.title_text)),
                        a.atok) > 0);

  select count(*) into v_matched from _bad;
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'title', titulo)
                            order by id), '[]'::jsonb)
    into v_sample
    from (select id, titulo from _bad order by id limit 5) s;

  v_cap := case when coalesce(p_limit, 0) > 0
                then least(p_limit, v_matched) else v_matched end;
  v_skipped_limit := v_matched - v_cap;

  if not coalesce(p_dry_run, true) and v_cap > 0 then
    delete from public.torrents t
     using (select id from _bad order by id limit v_cap) d
     where t.id = d.id;
    get diagnostics v_deleted = row_count;
  end if;

  return query select v_matched, v_deleted, v_matched - v_deleted,
    v_skipped_limit, v_sample,
    format('bloqueados: %s candidatos, %s borrados', v_matched, v_deleted)::text;
end
$fn$;

-- ---------------------------------------------------------------------------
-- Series/anime cuyo episodio SOLO vive en absolute_episode (episode null).
-- p_action: 'delete' borra la fila · 'nullify' solo limpia la columna.
-- ---------------------------------------------------------------------------
create or replace function public.purge_absolute_only_torrents(
  p_action text default 'delete', p_require_season_null boolean default true,
  p_dry_run boolean default true, p_limit integer default 0)
returns table(matched integer, deleted integer, updated integer, skipped integer,
              skipped_limit integer, sample jsonb, nota text)
language plpgsql as $fn$
declare
  v_matched integer; v_deleted integer := 0; v_updated integer := 0;
  v_skipped_limit integer; v_sample jsonb; v_cap integer;
begin
  drop table if exists _abs;
  create temp table _abs on commit drop as
  select t.id, public.torrent_effective_title(t.title, t.title_text) as titulo
    from public.torrents t
   where t.type in ('series', 'anime')
     and t.absolute_episode is not null
     and t.episode is null
     and (not coalesce(p_require_season_null, true) or t.season is null);

  select count(*) into v_matched from _abs;
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'title', titulo)
                            order by id), '[]'::jsonb)
    into v_sample
    from (select id, titulo from _abs order by id limit 5) s;

  v_cap := case when coalesce(p_limit, 0) > 0
                then least(p_limit, v_matched) else v_matched end;
  v_skipped_limit := v_matched - v_cap;

  if not coalesce(p_dry_run, true) and v_cap > 0 then
    if lower(coalesce(p_action, 'delete')) = 'nullify' then
      update public.torrents t
         set absolute_episode = null
        from (select id from _abs order by id limit v_cap) d
       where t.id = d.id;
      get diagnostics v_updated = row_count;
    else
      delete from public.torrents t
       using (select id from _abs order by id limit v_cap) d
       where t.id = d.id;
      get diagnostics v_deleted = row_count;
    end if;
  end if;

  return query select v_matched, v_deleted, v_updated,
    v_matched - v_deleted - v_updated,
    v_skipped_limit, v_sample,
    format('absolute_only (%s): %s candidatos, %s borrados, %s limpiados',
           lower(coalesce(p_action, 'delete')), v_matched, v_deleted, v_updated)::text;
end
$fn$;

-- ---------------------------------------------------------------------------
-- Torrents muertos: pocos seeders y viejos. Apagado por defecto (agresivo).
-- ---------------------------------------------------------------------------
create or replace function public.purge_dead_torrents(
  p_min_seeders integer default 1, p_older_days integer default 60,
  p_dry_run boolean default true, p_limit integer default 0)
returns table(matched integer, deleted integer, skipped integer,
              skipped_limit integer, sample jsonb, nota text)
language plpgsql as $fn$
declare
  v_matched integer; v_deleted integer := 0; v_skipped_limit integer;
  v_sample jsonb; v_cap integer;
begin
  drop table if exists _dead;
  create temp table _dead on commit drop as
  select t.id, public.torrent_effective_title(t.title, t.title_text) as titulo
    from public.torrents t
   where coalesce(t.seeders, 0) < coalesce(p_min_seeders, 1)
     and t.created_at < now() - make_interval(days => greatest(coalesce(p_older_days, 60), 0));

  select count(*) into v_matched from _dead;
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'title', titulo)
                            order by id), '[]'::jsonb)
    into v_sample
    from (select id, titulo from _dead order by id limit 5) s;

  v_cap := case when coalesce(p_limit, 0) > 0
                then least(p_limit, v_matched) else v_matched end;
  v_skipped_limit := v_matched - v_cap;

  if not coalesce(p_dry_run, true) and v_cap > 0 then
    delete from public.torrents t
     using (select id from _dead order by id limit v_cap) d
     where t.id = d.id;
    get diagnostics v_deleted = row_count;
  end if;

  return query select v_matched, v_deleted, v_matched - v_deleted,
    v_skipped_limit, v_sample,
    format('muertos: %s candidatos, %s borrados', v_matched, v_deleted)::text;
end
$fn$;

-- ---------------------------------------------------------------------------
-- Mejores torrents: top p_limit por obra (+temporada+episodio en series).
-- Obra = id DE FIAR (imdb>tmdb>anilist>kitsu>mal) o título. Los packs y las
-- filas sin clave no se tocan nunca. Orden: seeders > calidad > tamaño > id.
-- ---------------------------------------------------------------------------
create or replace function public.keep_best_torrents(
  p_limit integer default 3, p_min_seeders integer default 0,
  p_only_types text default null,
  p_dry_run boolean default true, p_max_deletes integer default 0)
returns table(groups integer, matched integer, deleted integer, kept integer,
              skipped integer, skipped_limit integer, sample jsonb, nota text)
language plpgsql as $fn$
declare
  v_groups integer; v_matched integer; v_deleted integer := 0;
  v_kept integer; v_skipped integer; v_cand integer; v_cap integer;
  v_sample jsonb;
begin
  drop table if exists _kb; drop table if exists _kbrank; drop table if exists _kbcand;
  create temp table _kb on commit drop as
  select s.id, s.obra, s.gseason, s.gep, s.seeders, s.qrank, s.size_bytes, s.titulo
    from (
      select t.id,
        case when public.torrent_id_es_confiable(t.ids_confidence, t.ids_source)
                  and (nullif(btrim(coalesce(t.imdb_id, '')), '') is not null
                    or t.tmdb_id is not null or t.anilist_id is not null
                    or t.kitsu_id is not null or t.mal_id is not null)
             then coalesce('imdb:' || nullif(btrim(t.imdb_id), ''),
                           'tmdb:' || t.tmdb_id,
                           'anilist:' || t.anilist_id,
                           'kitsu:' || t.kitsu_id,
                           'mal:' || t.mal_id)
             else 't:' || public.torrent_title_key(
                            public.torrent_effective_title(t.title, t.title_text))
        end as obra,
        case when t.type = 'movie' then null else t.season end as gseason,
        case when t.type = 'movie' then null
             else coalesce(t.episode, t.absolute_episode) end as gep,
        coalesce(t.seeders, 0) as seeders,
        public.torrent_quality_rank(t.quality) as qrank,
        t.size_bytes,
        public.torrent_effective_title(t.title, t.title_text) as titulo,
        (t.type in ('series', 'anime') and t.season is null
         and t.episode is null and t.absolute_episode is null) as pack
      from public.torrents t
     where (p_only_types is null or btrim(coalesce(p_only_types, '')) = ''
        or coalesce(t.type, '') in
           (select btrim(x) from unnest(string_to_array(p_only_types, ',')) x))
    ) s
   where s.obra is not null and not s.pack;

  select count(*) into v_matched from _kb;
  select count(*) into v_skipped from public.torrents t
   where (p_only_types is null or btrim(coalesce(p_only_types, '')) = ''
      or coalesce(t.type, '') in
         (select btrim(x) from unnest(string_to_array(p_only_types, ',')) x))
     and (t.id not in (select id from _kb));
  select count(*) into v_groups
    from (select distinct obra, gseason, gep from _kb) g;

  create temp table _kbrank on commit drop as
  select k.*,
         row_number() over (
           partition by k.obra, k.gseason, k.gep
           order by (k.seeders >= coalesce(p_min_seeders, 0)) desc,
                    k.seeders desc, k.qrank desc,
                    k.size_bytes asc nulls last, k.id desc) as rn
    from _kb k;

  create temp table _kbcand on commit drop as
  select id, titulo, obra, gseason, gep, rn
    from _kbrank where rn > greatest(coalesce(p_limit, 3), 1)
   order by obra, gseason nulls first, gep nulls first, rn;

  select count(*) into v_cand from _kbcand;
  select count(*) into v_kept from _kbrank
   where rn <= greatest(coalesce(p_limit, 3), 1);
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'title', titulo,
                                               'accion', 'borrar')
                            order by obra, gep nulls first, rn),
                  '[]'::jsonb)
    into v_sample from (select * from _kbcand limit 10) s;

  v_cap := case when coalesce(p_max_deletes, 0) > 0
                then least(p_max_deletes, v_cand) else v_cand end;

  if not coalesce(p_dry_run, true) and v_cap > 0 then
    delete from public.torrents t
     using (select id from _kbcand
             order by obra, gseason nulls first, gep nulls first, rn
             limit v_cap) d
     where t.id = d.id;
    get diagnostics v_deleted = row_count;
  end if;

  return query select v_groups, v_matched, v_deleted, v_kept, v_skipped,
    v_cand - v_cap, v_sample,
    format('keep_best: %s grupos, %s filas, %s borradas, %s conservadas',
           v_groups, v_matched, v_deleted, v_kept)::text;
end
$fn$;

-- ---------------------------------------------------------------------------
-- Foto de la tabla: total, por tipo, por calidad, sin seeders...
-- ---------------------------------------------------------------------------
create or replace function public.torrents_quality_stats()
returns table(total bigint, por_tipo jsonb, por_calidad jsonb,
              sin_seeders bigint, absolute_only bigint, sin_temporada bigint)
language sql stable as $fn$
  select count(*),
    (select coalesce(jsonb_object_agg(tipo, c), '{}'::jsonb) from (
       select coalesce(type, 'sin-tipo') as tipo, count(*) as c
         from public.torrents group by 1) s),
    (select coalesce(jsonb_object_agg(cal, c), '{}'::jsonb) from (
       select coalesce(quality, 'sin-calidad') as cal, count(*) as c
         from public.torrents group by 1) s),
    count(*) filter (where coalesce(seeders, 0) = 0),
    count(*) filter (where type in ('series', 'anime')
      and absolute_episode is not null and episode is null),
    count(*) filter (where type in ('series', 'anime') and season is null)
  from public.torrents;
$fn$;
