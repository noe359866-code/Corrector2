-- ============================================================================
--  sql/007_permisos.sql · SEGURIDAD: la clave pública NO puede borrar nada
--  (idempotente: se puede ejecutar tantas veces como quieras)
--
--  En Postgres toda función nueva es ejecutable por PUBLIC, y en Supabase el
--  esquema public está expuesto por la API REST: sin esto, cualquiera con la
--  clave anon (pública, viaja en el addon) podía llamar a purge_* y vaciar
--  la tabla. Aquí:
--    1) revoke de las funciones del pipeline a PUBLIC/anon/authenticated
--    2) grant solo a service_role (clave privada) y postgres
--    3) RLS activado en public.torrents + SOLO lectura pública
--    4) torrents_security_audit(): revisa (a) funciones abiertas, (b) DELETE/
--       INSERT/UPDATE de anon/authenticated, (c) políticas RLS de escritura
--       pública y (d) RLS desactivado.
-- ============================================================================

do $perms$
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
    'renumber_torrent_ids', 'torrents_id_report'];
  v_fn   text;
  r      record;
  v_anon boolean := exists (select 1 from pg_roles where rolname = 'anon');
  v_auth boolean := exists (select 1 from pg_roles where rolname = 'authenticated');
  v_srv  boolean := exists (select 1 from pg_roles where rolname = 'service_role');
begin
  foreach v_fn in array c_fns loop
    for r in select p.oid as oid,
                    pg_get_function_identity_arguments(p.oid) as args
               from pg_proc p
              where p.pronamespace = 'public'::regnamespace
                and p.proname = v_fn loop
      execute format('revoke all on function public.%I(%s) from public', v_fn, r.args);
      if v_anon then
        execute format('revoke all on function public.%I(%s) from anon', v_fn, r.args);
      end if;
      if v_auth then
        execute format('revoke all on function public.%I(%s) from authenticated', v_fn, r.args);
      end if;
      if v_srv then
        begin
          execute format('grant all on function public.%I(%s) to service_role', v_fn, r.args);
        exception when others then
          raise notice '007: no se pudo dar grant a service_role en %: %', v_fn, sqlerrm;
        end;
      end if;
    end loop;
  end loop;
end
$perms$;

-- RLS: solo lectura pública (la service_role se lo salta; el pipeline ni se entera).
do $rls$
begin
  if to_regclass('public.torrents') is not null then
    begin
      execute 'alter table public.torrents enable row level security';
    exception when others then
      raise notice '007: no se pudo activar RLS: %', sqlerrm;
    end;
    begin
      if not exists (select 1 from pg_policies
                      where schemaname = 'public' and tablename = 'torrents'
                        and policyname = 'lectura publica') then
        execute 'create policy "lectura publica" on public.torrents '
                'for select to anon, authenticated using (true)';
      end if;
    exception when others then
      raise notice '007: no se pudo crear la política de lectura: %', sqlerrm;
    end;
  end if;
end
$rls$;

-- Auditoría de seguridad: una fila por riesgo (o una fila OK si todo está bien).
create or replace function public.torrents_security_audit()
returns table(nivel text, hallazgo text)
language plpgsql stable as $fn$
declare
  c_danger constant text[] := array[
    'purge_junk_torrents', 'purge_blocked_torrents',
    'purge_absolute_only_torrents', 'purge_dead_torrents',
    'keep_best_torrents', 'keep_best_torrents_es',
    'renumber_torrent_ids', 'apply_torrent_ids'];
  v_anon  boolean := exists (select 1 from pg_roles where rolname = 'anon');
  v_auth  boolean := exists (select 1 from pg_roles where rolname = 'authenticated');
  v_n     integer := 0;
  r       record;
begin
  if to_regclass('public.torrents') is null then
    nivel := 'MEDIO'; hallazgo := 'la tabla public.torrents no existe';
    v_n := 1; return next; return;
  end if;

  -- a) funciones destructivas ejecutables por la clave pública
  for r in
    select p.proname as fn
      from pg_proc p
     where p.pronamespace = 'public'::regnamespace
       and p.proname = any (c_danger)
       and (case when v_anon then has_function_privilege('anon', p.oid, 'execute')
                 else false end
         or case when v_auth then has_function_privilege('authenticated', p.oid, 'execute')
                 else false end)
  loop
    nivel := 'ALTO';
    hallazgo := 'el rol anon/authenticated (clave pública) puede ejecutar ' || r.fn;
    v_n := v_n + 1; return next;
  end loop;

  -- b) escritura directa sobre la tabla
  if v_anon and (has_table_privilege('anon', 'public.torrents', 'DELETE')
              or has_table_privilege('anon', 'public.torrents', 'INSERT')
              or has_table_privilege('anon', 'public.torrents', 'UPDATE')) then
    nivel := 'ALTO';
    hallazgo := 'el rol anon (clave pública) tiene DELETE/INSERT/UPDATE sobre public.torrents';
    v_n := v_n + 1; return next;
  end if;
  if v_auth and (has_table_privilege('authenticated', 'public.torrents', 'DELETE')
              or has_table_privilege('authenticated', 'public.torrents', 'INSERT')
              or has_table_privilege('authenticated', 'public.torrents', 'UPDATE')) then
    nivel := 'ALTO';
    hallazgo := 'el rol authenticated tiene DELETE/INSERT/UPDATE sobre public.torrents';
    v_n := v_n + 1; return next;
  end if;

  -- c) políticas RLS que dejan escribir a la clave pública
  for r in
    select policyname as pol, cmd::text as cmd
      from pg_policies
     where schemaname = 'public' and tablename = 'torrents'
       and cmd in ('INSERT', 'UPDATE', 'DELETE', 'ALL')
       and (roles @> array['public']::name[]
         or roles @> array['anon']::name[]
         or roles @> array['authenticated']::name[])
  loop
    nivel := 'ALTO';
    hallazgo := 'la política RLS "' || r.pol || '" permite ' || r.cmd || ' a la clave pública';
    v_n := v_n + 1; return next;
  end loop;

  -- d) RLS desactivado
  if exists (select 1 from pg_class c
              join pg_namespace n on n.oid = c.relnamespace
             where n.nspname = 'public' and c.relname = 'torrents'
               and c.relrowsecurity is false) then
    nivel := 'MEDIO';
    hallazgo := 'RLS está desactivado en public.torrents (actívalo y deja solo SELECT público)';
    v_n := v_n + 1; return next;
  end if;

  if v_n = 0 then
    nivel := 'OK';
    hallazgo := 'sin riesgos: funciones cerradas (solo service_role) y RLS activo en public.torrents';
    return next;
  end if;
end
$fn$;

-- La propia auditoría también queda cerrada (lectura inocua, pero cerrada).
do $perms2$
declare
  r record;
begin
  for r in select p.oid as oid, pg_get_function_identity_arguments(p.oid) as args
             from pg_proc p
            where p.pronamespace = 'public'::regnamespace
              and p.proname = 'torrents_security_audit' loop
    execute format('revoke all on function public.torrents_security_audit(%s) from public', r.args);
    if exists (select 1 from pg_roles where rolname = 'anon') then
      execute format('revoke all on function public.torrents_security_audit(%s) from anon', r.args);
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
      execute format('revoke all on function public.torrents_security_audit(%s) from authenticated', r.args);
    end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
      begin
        execute format('grant all on function public.torrents_security_audit(%s) to service_role', r.args);
      exception when others then
        raise notice '007: no se pudo dar grant a service_role en audit: %', sqlerrm;
      end;
    end if;
  end loop;
end
$perms2$;
