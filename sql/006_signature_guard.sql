-- ============================================================================
--  sql/006_signature_guard.sql · guardián de firmas (idempotente)
--
--  Si alguna función del pipeline quedó con DOS versiones (sobrecarga: pasa
--  cuando se aplica 004 después de 005 a mano y Postgres no reemplaza porque
--  cambió el número de argumentos), las llamadas se vuelven AMBIGUAS y
--  PostgREST falla. Este bloque borra las versiones viejas y deja UNA sola:
--  la de más argumentos (la más nueva; a igualdad, la creada después).
--  No crea nada: en un repo sano no hace nada (no-op).
-- ============================================================================

do $guard$
declare
  c_fns constant text[] := array[
    'norm_torrent_text', 'torrent_effective_title', 'torrent_title_key',
    'torrent_title_token_ok', 'torrent_id_es_confiable', 'torrent_quality_rank',
    'get_torrents_to_enrich', 'get_torrents_to_enrich_expr',
    'get_torrents_missing_ids', 'reset_missing_ids_for_retry',
    'apply_torrent_ids', 'get_title_cache',
    'purge_blocked_torrents', 'purge_absolute_only_torrents',
    'purge_junk_torrents', 'purge_dead_torrents',
    'keep_best_torrents', 'keep_best_torrents_es',
    'is_spanish_text', 'report_spanish_gaps',
    'torrents_ids_stats', 'torrents_quality_stats', 'torrents_spanish_stats',
    'renumber_torrent_ids', 'torrents_id_report', 'torrents_security_audit'];
  r   record;
  i   integer;
begin
  for r in
    select p.proname as fn,
           array_agg(p.oid order by p.pronargs desc, p.oid desc) as oids
      from pg_proc p
     where p.pronamespace = 'public'::regnamespace
       and p.proname = any (c_fns)
     group by 1 having count(*) > 1
  loop
    for i in 2 .. array_length(r.oids, 1) loop
      execute format('drop function if exists public.%I(%s)',
                     r.fn, pg_get_function_identity_arguments(r.oids[i]));
      raise notice '006_signature_guard: eliminada sobrecarga vieja de %', r.fn;
    end loop;
  end loop;
end
$guard$;
