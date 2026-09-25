-- ============================================================================
--  sql/002_enrich.sql · resolución de IDs por nombre (idempotente)
--
--  get_torrents_to_enrich[_expr] .. filas a las que les falta algún ID,
--                                    con el título EFECTIVO + title_alt.
--  apply_torrent_ids .............. escribe IDs saneados (coalesce: nunca
--                                    pisa un ID bueno) + alimenta la caché.
--  get_title_cache ................ precarga persistente por claves.
--  torrents_ids_stats ............. cuántas filas y cuántos IDs faltan.
--
--  ids_attempts = FALLOS consecutivos: se pone a 0 cuando la fila tiene
--  algún ID y sube en 1 cuando se procesa y sigue sin ninguno.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Filas pendientes de IDs. title = efectivo (title, si no title_text);
-- title_alt = el otro nombre (puede ser null).
-- ---------------------------------------------------------------------------
create or replace function public.get_torrents_to_enrich(
  p_batch integer default 400, p_recheck_days integer default 45,
  p_only_types text default null)
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
   where (t.tmdb_id is null
       or nullif(btrim(coalesce(t.imdb_id, '')), '') is null
       or t.anilist_id is null or t.kitsu_id is null or t.mal_id is null)
     and (t.ids_checked_at is null
       or t.ids_checked_at < now() - make_interval(days => greatest(coalesce(p_recheck_days, 45), 0)))
     and (p_only_types is null or btrim(coalesce(p_only_types, '')) = ''
       or coalesce(t.type, '') in
          (select btrim(x) from unnest(string_to_array(p_only_types, ',')) x))
   order by t.ids_checked_at nulls first, t.id
   limit case when coalesce(p_batch, 400) > 0 then p_batch else 400 end;
$fn$;

-- Igual, pero eligiendo la columna principal: 'title' | 'title_text' | 'auto'.
create or replace function public.get_torrents_to_enrich_expr(
  p_expr text default 'auto', p_batch integer default 400,
  p_recheck_days integer default 45, p_only_types text default null)
returns table(id bigint, title text, title_text text, title_alt text,
              type text, season integer, episode integer, absolute_episode integer)
language sql stable as $fn$
  select t.id,
         case lower(coalesce(p_expr, 'auto'))
           when 'title_text' then public.torrent_effective_title(t.title_text, t.title)
           else public.torrent_effective_title(t.title, t.title_text)
         end as title,
         t.title_text,
         case lower(coalesce(p_expr, 'auto'))
           when 'title_text' then nullif(btrim(coalesce(t.title, '')), '')
           when 'title'      then nullif(btrim(coalesce(t.title_text, '')), '')
           else case when nullif(btrim(coalesce(t.title, '')), '') is not null
                     then nullif(btrim(coalesce(t.title_text, '')), '')
                     else nullif(btrim(coalesce(t.title, '')), '')
                end
         end as title_alt,
         t.type, t.season, t.episode, t.absolute_episode
    from public.torrents t
   where (t.tmdb_id is null
       or nullif(btrim(coalesce(t.imdb_id, '')), '') is null
       or t.anilist_id is null or t.kitsu_id is null or t.mal_id is null)
     and (t.ids_checked_at is null
       or t.ids_checked_at < now() - make_interval(days => greatest(coalesce(p_recheck_days, 45), 0)))
     and (p_only_types is null or btrim(coalesce(p_only_types, '')) = ''
       or coalesce(t.type, '') in
          (select btrim(x) from unnest(string_to_array(p_only_types, ',')) x))
   order by t.ids_checked_at nulls first, t.id
   limit case when coalesce(p_batch, 400) > 0 then p_batch else 400 end;
$fn$;

-- ---------------------------------------------------------------------------
-- Precarga de caché persistente. found=false + updated_at viejo = reintentar.
-- ---------------------------------------------------------------------------
create or replace function public.get_title_cache(p_keys text[])
returns table(cache_key text, tmdb_id integer, imdb_id text, anilist_id integer,
              kitsu_id integer, mal_id integer, source text,
              confidence double precision, found boolean, updated_at timestamptz)
language sql stable as $fn$
  select c.cache_key, c.tmdb_id, c.imdb_id, c.anilist_id, c.kitsu_id, c.mal_id,
         c.source, c.confidence, c.found, c.updated_at
    from public.title_id_cache c
   where c.cache_key = any (coalesce(p_keys, '{}'));
$fn$;

-- ---------------------------------------------------------------------------
-- Escribe un lote de IDs. p_ids = jsonb array de:
--   {"id":1,"tmdb_id":603,"imdb_id":"tt0133093","anilist_id":null,...,
--    "source":"tmdb","confidence":0.95,"cache_key":"the matrix|1999|movie"}
--
--  * Sanea: tmdb/anilist/kitsu/mal enteros > 0 ('603.5' se rechaza);
--    imdb ^tt[0-9]+ (lo demás se rechaza). Lo inválido se CUENTA, no rompe.
--  * coalesce: nunca pisa un ID que ya estaba en la tabla.
--  * source/confidence solo se tocan si se escribió algo nuevo.
--  * Alimenta title_id_cache (hits y misses; un miss nunca pisa un hit).
-- ---------------------------------------------------------------------------
create or replace function public.apply_torrent_ids(
  p_ids jsonb, p_dry_run boolean default false)
returns table(updated integer, cached integer, invalid integer, nota text)
language plpgsql as $fn$
declare
  elem     jsonb;
  old      public.torrents%rowtype;
  v_id     bigint;
  v_tmdb   integer; v_imdb text; v_anilist integer; v_kitsu integer; v_mal integer;
  v_source text;    v_conf double precision;       v_key  text;
  v_txt    text;
  wrote    boolean;
  has_any  boolean;
  v_updated integer := 0;
  v_cached  integer := 0;
  v_invalid integer := 0;
begin
  if p_ids is null or jsonb_typeof(p_ids) <> 'array'
     or jsonb_array_length(p_ids) = 0 then
    return query select 0, 0, 0, 'nada que aplicar'::text;
    return;
  end if;

  for elem in select * from jsonb_array_elements(p_ids) loop
    begin
      v_id := (elem ->> 'id')::bigint;
    exception when others then
      v_id := null;
    end;
    if v_id is null then
      v_invalid := v_invalid + 1;
      continue;
    end if;
    select * into old from public.torrents where id = v_id;
    if not found then
      v_invalid := v_invalid + 1;
      continue;
    end if;

    -- --- saneo de numéricos: solo dígitos (con espacios fuera vale) y > 0 ---
    v_txt := btrim(coalesce(elem ->> 'tmdb_id', ''));
    if v_txt = '' or v_txt is null then v_tmdb := null;
    elsif v_txt ~ '^[0-9]+$' and v_txt::bigint between 1 and 2147483647 then v_tmdb := v_txt::integer;
    else v_tmdb := null; v_invalid := v_invalid + 1; end if;

    v_txt := btrim(coalesce(elem ->> 'anilist_id', ''));
    if v_txt = '' or v_txt is null then v_anilist := null;
    elsif v_txt ~ '^[0-9]+$' and v_txt::bigint between 1 and 2147483647 then v_anilist := v_txt::integer;
    else v_anilist := null; v_invalid := v_invalid + 1; end if;

    v_txt := btrim(coalesce(elem ->> 'kitsu_id', ''));
    if v_txt = '' or v_txt is null then v_kitsu := null;
    elsif v_txt ~ '^[0-9]+$' and v_txt::bigint between 1 and 2147483647 then v_kitsu := v_txt::integer;
    else v_kitsu := null; v_invalid := v_invalid + 1; end if;

    v_txt := btrim(coalesce(elem ->> 'mal_id', ''));
    if v_txt = '' or v_txt is null then v_mal := null;
    elsif v_txt ~ '^[0-9]+$' and v_txt::bigint between 1 and 2147483647 then v_mal := v_txt::integer;
    else v_mal := null; v_invalid := v_invalid + 1; end if;

    -- --- saneo de imdb: ^tt[0-9]+ ---
    v_txt := lower(btrim(coalesce(elem ->> 'imdb_id', '')));
    if v_txt = '' then v_imdb := null;
    elsif v_txt ~ '^tt[0-9]{1,10}$' then v_imdb := v_txt;
    else v_imdb := null; v_invalid := v_invalid + 1; end if;

    -- --- fuente y confianza (0.5 = desconocida) ---
    v_source := nullif(btrim(coalesce(elem ->> 'source', '')), '');
    begin
      v_conf := (elem ->> 'confidence')::double precision;
    exception when others then
      v_conf := null;
    end;
    v_conf := coalesce(v_conf, 0.5);
    if v_conf < 0 then v_conf := 0; elsif v_conf > 1 then v_conf := 1; end if;
    v_key := nullif(btrim(coalesce(elem ->> 'cache_key', '')), '');

    if coalesce(p_dry_run, false) then
      v_updated := v_updated + 1;
      if v_key is not null then v_cached := v_cached + 1; end if;
      continue;
    end if;

    wrote := (v_tmdb    is not null and old.tmdb_id    is null)
          or (v_imdb    is not null and nullif(btrim(coalesce(old.imdb_id, '')), '') is null)
          or (v_anilist is not null and old.anilist_id is null)
          or (v_kitsu   is not null and old.kitsu_id   is null)
          or (v_mal     is not null and old.mal_id     is null);

    has_any := coalesce(old.tmdb_id, v_tmdb) is not null
          or nullif(btrim(coalesce(old.imdb_id, v_imdb, '')), '') is not null
          or coalesce(old.anilist_id, v_anilist) is not null
          or coalesce(old.kitsu_id, v_kitsu) is not null
          or coalesce(old.mal_id, v_mal) is not null;

    update public.torrents
       set tmdb_id        = coalesce(tmdb_id, v_tmdb),
           imdb_id        = coalesce(nullif(btrim(imdb_id), ''), v_imdb),
           anilist_id     = coalesce(anilist_id, v_anilist),
           kitsu_id       = coalesce(kitsu_id, v_kitsu),
           mal_id         = coalesce(mal_id, v_mal),
           ids_checked_at = now(),
           ids_attempts   = case when has_any then 0 else coalesce(ids_attempts, 0) + 1 end,
           ids_source     = case when wrote then coalesce(v_source, 'sin-fuente') else ids_source end,
           ids_confidence = case when wrote then v_conf else ids_confidence end
     where id = v_id;
    v_updated := v_updated + 1;

    -- --- caché persistente ---
    if v_key is not null then
      if v_tmdb is not null or v_imdb is not null or v_anilist is not null
         or v_kitsu is not null or v_mal is not null then
        insert into public.title_id_cache
              (cache_key, tmdb_id, imdb_id, anilist_id, kitsu_id, mal_id,
               source, confidence, found, updated_at)
        values (v_key, v_tmdb, v_imdb, v_anilist, v_kitsu, v_mal,
                coalesce(v_source, 'sin-fuente'), v_conf, true, now())
        on conflict (cache_key) do update
           set tmdb_id    = coalesce(public.title_id_cache.tmdb_id, excluded.tmdb_id),
               imdb_id    = coalesce(public.title_id_cache.imdb_id, excluded.imdb_id),
               anilist_id = coalesce(public.title_id_cache.anilist_id, excluded.anilist_id),
               kitsu_id   = coalesce(public.title_id_cache.kitsu_id, excluded.kitsu_id),
               mal_id     = coalesce(public.title_id_cache.mal_id, excluded.mal_id),
               source     = coalesce(public.title_id_cache.source, excluded.source),
               confidence = coalesce(public.title_id_cache.confidence, excluded.confidence),
               found      = true,
               updated_at = now();
        v_cached := v_cached + 1;
      else
        -- miss: se anota solo si la clave no existía (nunca pisa un hit)
        insert into public.title_id_cache
              (cache_key, source, confidence, found, updated_at)
        values (v_key, 'none', 0, false, now())
        on conflict (cache_key) do nothing;
        v_cached := v_cached + 1;
      end if;
    end if;
  end loop;

  return query select v_updated, v_cached, v_invalid,
    format('aplicados %s, caché %s, inválidos %s', v_updated, v_cached, v_invalid)::text;
end
$fn$;

-- ---------------------------------------------------------------------------
-- Estado de los IDs en la tabla.
-- ---------------------------------------------------------------------------
create or replace function public.torrents_ids_stats()
returns table(total bigint, sin_id bigint, con_tmdb bigint, con_imdb bigint,
              con_anilist bigint, con_kitsu bigint, con_mal bigint)
language sql stable as $fn$
  select count(*),
         count(*) filter (where tmdb_id is null
           and nullif(btrim(coalesce(imdb_id, '')) , '') is null
           and anilist_id is null and kitsu_id is null and mal_id is null),
         count(*) filter (where tmdb_id is not null),
         count(*) filter (where nullif(btrim(coalesce(imdb_id, '')), '') is not null),
         count(*) filter (where anilist_id is not null),
         count(*) filter (where kitsu_id is not null),
         count(*) filter (where mal_id is not null)
    from public.torrents;
$fn$;
