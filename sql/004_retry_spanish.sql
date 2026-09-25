-- ============================================================================
--  sql/004_retry_spanish.sql · reintentos de NULL + español/dedupe (idempotente)
--
--  is_spanish_text ................ ¿menciona español? (audio/subs/título)
--  get_torrents_missing_ids ....... filas con TODOS los IDs en NULL
--  reset_missing_ids_for_retry .... las devuelve a la cola (con tope)
--  keep_best_torrents_es .......... dedupe + top-N + cuota de español con
--                                    CASCADA (audio -> subtitulado -> título)
--  report_spanish_gaps ............ grupos que se quedaron cortos de español
--  torrents_spanish_stats ......... foto de español en la tabla
-- ============================================================================

-- ---------------------------------------------------------------------------
-- ¿Texto en español? Tokens con límite de palabra ('es' NO caza 'estonian').
-- p_extra_tokens: lista separada por comas (ej. 'es-la,audio latino').
-- ---------------------------------------------------------------------------
create or replace function public.is_spanish_text(
  p_text text, p_extra_tokens text default null)
returns boolean language plpgsql immutable as $fn$
declare
  nt   text;
  tok  text;
  toks text[] := array[
    'es', 'spa', 'esp', 'spanish', 'espanol', 'castellano', 'castellanos',
    'latino', 'latina', 'latinos', 'latinas', 'latin', 'lat',
    'latinoamericano', 'vose', 'doblaje', 'doblado', 'doblada', 'doblados',
    'subtitulado', 'subtitulada', 'subtitulados', 'subtitulos'];
begin
  nt := public.norm_torrent_text(p_text);
  if nt = '' then
    return false;
  end if;
  if p_extra_tokens is not null and btrim(p_extra_tokens) <> '' then
    toks := toks || array(
      select public.norm_torrent_text(x)
        from unnest(string_to_array(p_extra_tokens, ',')) x);
  end if;
  nt := ' ' || nt || ' ';
  foreach tok slice 0 in array toks loop
    if tok <> '' and strpos(nt, ' ' || tok || ' ') > 0 then
      return true;
    end if;
  end loop;
  return false;
end
$fn$;

-- ---------------------------------------------------------------------------
-- Filas con TODOS los IDs en NULL, listas para reintentar (respeta el máximo
-- de intentos y la edad mínima desde el último intento).
-- ---------------------------------------------------------------------------
create or replace function public.get_torrents_missing_ids(
  p_batch integer default 400, p_min_age_minutes integer default 60,
  p_max_attempts integer default 4)
returns table(id bigint, title text, title_text text, title_alt text,
              type text, season integer, episode integer, absolute_episode integer)
language sql stable as $fn$
  select t.id,
         public.torrent_effective_title(t.title, t.title_text) as title,
         t.title_text,
         case when nullif(btrim(coalesce(t.title, '')), '') is not null
              then nullif(btrim(coalesce(t.title_text, '')), '')
              else nullif(btrim(coalesce(t.title, '')), '')
         end as title_alt,
         t.type, t.season, t.episode, t.absolute_episode
    from public.torrents t
   where t.tmdb_id is null
     and nullif(btrim(coalesce(t.imdb_id, '')), '') is null
     and t.anilist_id is null and t.kitsu_id is null and t.mal_id is null
     and coalesce(t.ids_attempts, 0) < coalesce(p_max_attempts, 4)
     and (t.ids_checked_at is null
       or t.ids_checked_at < now() - make_interval(mins => greatest(coalesce(p_min_age_minutes, 60), 0)))
   order by coalesce(t.ids_attempts, 0), t.ids_checked_at nulls first, t.id
   limit case when coalesce(p_batch, 400) > 0 then p_batch else 400 end;
$fn$;

-- Devuelve a la cola las filas con todos los IDs en NULL (nunca en bucle:
-- las que agotaron los intentos quedan marcadas como exhausted).
create or replace function public.reset_missing_ids_for_retry(
  p_max_attempts integer default 4, p_min_age_minutes integer default 60)
returns table(matched integer, queued integer, exhausted integer, nota text)
language plpgsql as $fn$
declare
  v_matched integer; v_queued integer := 0; v_exhausted integer;
begin
  select count(*) into v_matched from public.torrents t
   where t.tmdb_id is null
     and nullif(btrim(coalesce(t.imdb_id, '')), '') is null
     and t.anilist_id is null and t.kitsu_id is null and t.mal_id is null;
  select count(*) into v_exhausted from public.torrents t
   where t.tmdb_id is null
     and nullif(btrim(coalesce(t.imdb_id, '')), '') is null
     and t.anilist_id is null and t.kitsu_id is null and t.mal_id is null
     and coalesce(t.ids_attempts, 0) >= coalesce(p_max_attempts, 4);

  update public.torrents t
     set ids_checked_at = null
   where t.tmdb_id is null
     and nullif(btrim(coalesce(t.imdb_id, '')), '') is null
     and t.anilist_id is null and t.kitsu_id is null and t.mal_id is null
     and coalesce(t.ids_attempts, 0) < coalesce(p_max_attempts, 4)
     and (t.ids_checked_at is null
       or t.ids_checked_at < now() - make_interval(mins => greatest(coalesce(p_min_age_minutes, 60), 0)));
  get diagnostics v_queued = row_count;

  return query select v_matched, v_queued, v_exhausted,
    format('reintentos: %s sin ID, %s a la cola, %s agotados',
           v_matched, v_queued, v_exhausted)::text;
end
$fn$;

-- ---------------------------------------------------------------------------
-- Español + deduplicado: por obra (+temporada+episodio) borra repetidos,
-- deja máximo p_limit y garantiza p_min_spanish con español, en CASCADA:
--   3 = audio en español · 2 = subtítulos en español · 1 = el título lo
--   dice · 0 = nada. Con p_allow_subs_fallback=false el peldaño 2 no cuenta.
-- Los packs y las filas sin clave no se tocan. p_max_deletes = tope (los
-- duplicados lo gastan primero). En p_strict_spanish=true, el grupo que no
-- llega al mínimo se borra ENTERO.
-- ---------------------------------------------------------------------------
create or replace function public.keep_best_torrents_es(
  p_limit integer default 3, p_min_spanish integer default 2,
  p_min_seeders integer default 0, p_only_types text default null,
  p_dedupe boolean default true, p_strict_spanish boolean default false,
  p_use_title_hint boolean default true,
  p_allow_subs_fallback boolean default true,
  p_spanish_tokens text default null,
  p_dry_run boolean default true, p_max_deletes integer default 0)
returns table(groups integer, matched integer, deleted integer, kept integer,
              groups_ok_audio integer, groups_ok_spanish integer,
              groups_low_spanish integer, kept_with_subs integer,
              duplicates integer, skipped integer, skipped_limit integer,
              sample jsonb, nota text)
language plpgsql as $fn$
declare
  v_limit   integer := greatest(coalesce(p_limit, 3), 1);
  v_min_es  integer := greatest(coalesce(p_min_spanish, 2), 0);
  v_budget  integer := greatest(coalesce(p_max_deletes, 0), 0);
  v_strict  boolean := coalesce(p_strict_spanish, false);
  v_dry     boolean := coalesce(p_dry_run, true);
  v_groups  integer; v_matched integer; v_skipped integer;
  v_dupes   integer := 0; v_dupe_cap integer := 0; v_dupe_del integer := 0;
  v_cand    integer; v_cap integer := 0; v_over_del integer := 0;
  v_kept integer; v_ok_audio integer; v_ok_es integer; v_subs integer;
  v_sample jsonb;
begin
  drop table if exists _es;     drop table if exists _esdupe;
  drop table if exists _esrank;  drop table if exists _esgc;
  drop table if exists _escand;

  -- 1) ámbito (obra de fiar o título; packs y sin-clave fuera)
  create temp table _es on commit drop as
  select s.id, s.obra, s.gseason, s.gep, s.seeders, s.qrank, s.size_bytes,
         s.quality, s.codec, s.hdr, s.rgroup, s.titulo,
         s.audio_es, s.subs_es, s.hint_es,
         case when s.audio_es then 3
              when s.subs_es and coalesce(p_allow_subs_fallback, true) then 2
              when s.hint_es and coalesce(p_use_title_hint, true) then 1
              else 0 end as es_score
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
        t.size_bytes, t.quality, t.codec, t.hdr,
        t.release_group as rgroup,
        public.torrent_effective_title(t.title, t.title_text) as titulo,
        exists (select 1 from unnest(coalesce(t.audio, '{}')) a
                 where public.is_spanish_text(a, p_spanish_tokens)) as audio_es,
        exists (select 1 from unnest(coalesce(t.subtitles, '{}')) b
                 where public.is_spanish_text(b, p_spanish_tokens)) as subs_es,
        public.is_spanish_text(
          public.torrent_effective_title(t.title, t.title_text),
          p_spanish_tokens) as hint_es,
        (t.type in ('series', 'anime') and t.season is null
         and t.episode is null and t.absolute_episode is null) as pack
      from public.torrents t
     where (p_only_types is null or btrim(coalesce(p_only_types, '')) = ''
        or coalesce(t.type, '') in
           (select btrim(x) from unnest(string_to_array(p_only_types, ',')) x))
    ) s
   where s.obra is not null and not s.pack;

  select count(*) into v_matched from _es;
  select count(*) into v_skipped from public.torrents t
   where (p_only_types is null or btrim(coalesce(p_only_types, '')) = ''
      or coalesce(t.type, '') in
         (select btrim(x) from unnest(string_to_array(p_only_types, ',')) x))
     and t.id not in (select id from _es);
  select count(*) into v_groups
    from (select distinct obra, gseason, gep from _es) g;

  -- 2) deduplicado (misma obra+ep+calidad+codec+hdr+tamaño±1%+grupo)
  if coalesce(p_dedupe, true) then
    create temp table _esdupe on commit drop as
    select id from (
      select id, row_number() over (
        partition by obra, gseason, gep,
          lower(coalesce(quality, '')), lower(coalesce(codec, '')),
          lower(coalesce(hdr, '')),
          case when size_bytes is null or size_bytes <= 0 then 0
               else floor(size_bytes / greatest(size_bytes * 0.01, 1))::bigint end,
          lower(coalesce(rgroup, ''))
        order by id) as rn
      from _es) d where rn > 1;
  else
    create temp table _esdupe (id bigint) on commit drop;
  end if;
  select count(*) into v_dupes from _esdupe;
  v_dupe_cap := case when v_budget > 0 then least(v_budget, v_dupes) else v_dupes end;
  if not v_dry and v_dupe_cap > 0 then
    delete from public.torrents t
     using (select id from _esdupe order by id limit v_dupe_cap) d
     where t.id = d.id;
    get diagnostics v_dupe_del = row_count;
  end if;
  delete from _es where id in (select id from _esdupe);

  -- 3) ranking de supervivientes (español > seeders > calidad > tamaño)
  create temp table _esrank on commit drop as
  select e.*,
         row_number() over (
           partition by e.obra, e.gseason, e.gep
           order by e.es_score desc,
                    (e.seeders >= coalesce(p_min_seeders, 0)) desc,
                    e.seeders desc, e.qrank desc,
                    e.size_bytes asc nulls last, e.id desc) as rn
    from _es e;

  create temp table _esgc on commit drop as
  select obra, gseason, gep, count(*) as total,
         count(*) filter (where audio_es) as n_audio,
         count(*) filter (where es_score >= 1) as n_es
    from _esrank group by 1, 2, 3;

  -- 4) candidatas: sobrantes del top-N + grupos enteros en modo estricto
  create temp table _escand on commit drop as
  select r.id, r.titulo, r.obra, r.gseason, r.gep, r.rn,
         case when v_strict and g.n_es < v_min_es then 'strict' else 'top' end as motivo
    from _esrank r
    join _esgc g on (g.obra = r.obra
                 and g.gseason is not distinct from r.gseason
                 and g.gep is not distinct from r.gep)
   where (v_strict and g.n_es < v_min_es)
      or (not (v_strict and g.n_es < v_min_es) and r.rn > v_limit);

  select count(*) into v_cand from _escand;
  if v_budget > 0 then
    v_cap := least(greatest(v_budget - (case when v_dry then v_dupe_cap else v_dupe_del end), 0), v_cand);
  else
    v_cap := v_cand;
  end if;
  if not v_dry and v_cap > 0 then
    delete from public.torrents t
     using (select id from _escand
             order by (motivo = 'strict'), obra,
                      gseason nulls first, gep nulls first, rn
             limit v_cap) d
     where t.id = d.id;
    get diagnostics v_over_del = row_count;
  end if;

  -- 5) métricas sobre lo conservado
  select count(*) into v_kept from _esrank r
    join _esgc g on (g.obra = r.obra
                 and g.gseason is not distinct from r.gseason
                 and g.gep is not distinct from r.gep)
   where r.rn <= v_limit and not (v_strict and g.n_es < v_min_es);

  select count(*) into v_ok_audio
    from (select g.obra, g.gseason, g.gep
            from _esgc g
           where not (v_strict and g.n_es < v_min_es)
             and (select count(*) from _esrank r
                   where r.obra = g.obra
                     and r.gseason is not distinct from g.gseason
                     and r.gep is not distinct from g.gep
                     and r.rn <= v_limit and r.audio_es) >= v_min_es) s;

  select count(*) into v_ok_es
    from (select g.obra, g.gseason, g.gep
            from _esgc g
           where not (v_strict and g.n_es < v_min_es)
             and (select count(*) from _esrank r
                   where r.obra = g.obra
                     and r.gseason is not distinct from r.gseason
                     and r.gep is not distinct from g.gep
                     and r.rn <= v_limit and r.es_score >= 1) >= v_min_es) s;

  select count(*) into v_subs from _esrank r
    join _esgc g on (g.obra = r.obra
                 and g.gseason is not distinct from r.gseason
                 and g.gep is not distinct from r.gep)
   where r.rn <= v_limit and not (v_strict and g.n_es < v_min_es)
     and coalesce(p_allow_subs_fallback, true)
     and not r.audio_es and r.subs_es;

  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'title', titulo,
                                               'accion', 'borrar',
                                               'motivo', motivo)
                            order by (motivo = 'strict'), obra, rn),
                  '[]'::jsonb)
    into v_sample from (select * from _escand
             order by (motivo = 'strict'), obra,
                      gseason nulls first, gep nulls first, rn
             limit 10) s;

  return query select
    v_groups, v_matched,
    (case when v_dry then 0 else v_dupe_del + v_over_del end),
    v_kept, v_ok_audio, v_ok_es, v_groups - v_ok_es, v_subs,
    v_dupes, v_skipped,
    (v_dupes + v_cand)
      - (case when v_dry then v_dupe_cap + v_cap else v_dupe_del + v_over_del end),
    v_sample,
    format('español: %s grupos, %s filas, %s borradas (%s dupes), %s conservadas, %s por subtitulado',
           v_groups, v_matched,
           (case when v_dry then 0 else v_dupe_del + v_over_del end),
           v_dupes, v_kept, v_subs)::text;
end
$fn$;

-- ---------------------------------------------------------------------------
-- Grupos que se quedaron cortos de español (audio+subs < 2), los peores antes.
-- ---------------------------------------------------------------------------
create or replace function public.report_spanish_gaps(
  p_limit integer default 15, p_spanish_tokens text default null)
returns table(obra text, season integer, episode integer,
              total integer, con_audio integer, con_subs integer)
language sql stable as $fn$
  select min(s.titulo)::text, s.gseason, s.gep,
         count(*)::integer,
         (count(*) filter (where s.audio_es))::integer,
         (count(*) filter (where s.subs_es and not s.audio_es))::integer
    from (
      select
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
        public.torrent_effective_title(t.title, t.title_text) as titulo,
        exists (select 1 from unnest(coalesce(t.audio, '{}')) a
                 where public.is_spanish_text(a, p_spanish_tokens)) as audio_es,
        exists (select 1 from unnest(coalesce(t.subtitles, '{}')) b
                 where public.is_spanish_text(b, p_spanish_tokens)) as subs_es,
        (t.type in ('series', 'anime') and t.season is null
         and t.episode is null and t.absolute_episode is null) as pack
      from public.torrents t
    ) s
   where s.obra is not null and not s.pack
   group by s.obra, s.gseason, s.gep
  having count(*) filter (where s.audio_es or s.subs_es) < 2
   order by count(*) filter (where s.audio_es or s.subs_es), count(*) desc
   limit case when coalesce(p_limit, 15) > 0 then p_limit else 15 end;
$fn$;

-- ---------------------------------------------------------------------------
-- Foto de español: con audio, con subtítulos, solo-subtitulado, sin nada.
-- ---------------------------------------------------------------------------
create or replace function public.torrents_spanish_stats()
returns table(total bigint, con_audio_es bigint, con_subs_es bigint,
              solo_subs bigint, sin_espanol bigint)
language sql stable as $fn$
  select x.total, x.a, x.s, x.ss, x.total - x.a - x.ss
    from (
      select count(*) as total,
        count(*) filter (where exists (
          select 1 from unnest(coalesce(audio, '{}')) a
           where public.is_spanish_text(a, null))) as a,
        count(*) filter (where exists (
          select 1 from unnest(coalesce(subtitles, '{}')) b
           where public.is_spanish_text(b, null))) as s,
        count(*) filter (where exists (
            select 1 from unnest(coalesce(subtitles, '{}')) b
             where public.is_spanish_text(b, null))
          and not exists (
            select 1 from unnest(coalesce(audio, '{}')) a
             where public.is_spanish_text(a, null))) as ss
      from public.torrents) x;
$fn$;
