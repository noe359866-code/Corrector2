#!/usr/bin/env bash
# ============================================================================
#  apply_sql.sh  ·  Aplica las migraciones a Supabase (o a cualquier Postgres)
#
#  Uso local:
#     export SUPABASE_DB_URL="postgresql://postgres.xxxx:PASS@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
#     bash scripts/apply_sql.sh
#
#  En GitHub Actions corre solo (job "migraciones" y en el job "pruebas").
#  Es idempotente: se puede ejecutar todas las veces que quieras.
#
#  IMPORTANTE (cadena de conexión):
#    Usa la del **Session pooler** (IPv4), NO la directa. La directa
#    (db.<ref>.supabase.co:5432) es solo IPv6 y GitHub Actions no tiene IPv6:
#    da "Network is unreachable".
#      Supabase -> Project Settings -> Database -> Connection string -> Session pooler
#      postgresql://postgres.<ref>:TU_PASSWORD@aws-0-<region>.pooler.supabase.com:5432/postgres
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_DIR="${HERE}/../sql"

# ---------------------------------------------------------------------------
# 1) ¿está la variable?
# ---------------------------------------------------------------------------
if [ -z "${SUPABASE_DB_URL:-}" ]; then
  echo "ERROR: falta la variable SUPABASE_DB_URL." >&2
  echo "  · En GitHub: Settings > Secrets and variables > Actions > Secrets" >&2
  echo "  · En local:  export SUPABASE_DB_URL=\"postgresql://...\"" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 2) ¿está psql?
# ---------------------------------------------------------------------------
if ! command -v psql >/dev/null 2>&1; then
  echo "ERROR: psql no está instalado. En Ubuntu: sudo apt-get install -y postgresql-client" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 3) ¿está la carpeta sql/ con archivos? (si el repo se subió incompleto, el
#    glob no expande y antes salía un críptico "--> *.sql")
# ---------------------------------------------------------------------------
if [ ! -d "$SQL_DIR" ]; then
  echo "ERROR: no encuentro la carpeta sql/ (busqué en: $SQL_DIR)." >&2
  echo "  Sube el repo COMPLETO a GitHub: sql/, src/, tests/ y scripts/." >&2
  exit 1
fi

shopt -s nullglob
ARCHIVOS=("${SQL_DIR}"/*.sql)
shopt -u nullglob
if [ "${#ARCHIVOS[@]}" -eq 0 ]; then
  echo "ERROR: la carpeta sql/ está vacía (no hay ningún .sql dentro)." >&2
  echo "  Sube los archivos sql/000_base.sql ... sql/007_permisos.sql a GitHub." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 4) comprobar la conexión ANTES de tocar nada, con pistas útiles
# ---------------------------------------------------------------------------
ANFITRION="$(printf '%s' "$SUPABASE_DB_URL" | sed -E 's#^[a-z]+://([^@/]+@)?([^:/?]+).*#\2#')"
echo "==> Aplicando migraciones en: $ANFITRION  (${#ARCHIVOS[@]} archivos sql)"

if ! SALIDA="$(psql "$SUPABASE_DB_URL" --no-psqlrc -tAc 'select 1' 2>&1)"; then
  echo "" >&2
  echo "ERROR: no pude conectar con la base." >&2
  echo "$SALIDA" | sed 's/^/    /' >&2
  echo "" >&2
  case "$SALIDA" in
    *"Network is unreachable"*|*"No route to host"*|*"could not translate host"*)
      echo "PISTA: estás usando la conexión DIRECTA (db.<ref>.supabase.co), que es solo IPv6," >&2
      echo "       y GitHub Actions no tiene IPv6. Cambia el secret SUPABASE_DB_URL por la del" >&2
      echo "       **Session pooler** (IPv4):" >&2
      echo "         Supabase > Project Settings > Database > Connection string > Session pooler" >&2
      echo "         postgresql://postgres.<ref>:TU_PASSWORD@aws-0-<region>.pooler.supabase.com:5432/postgres" >&2
      ;;
    *"password authentication failed"*|*"no password supplied"*)
      echo "PISTA: la contraseña no es correcta (o tiene caracteres raros sin codificar)." >&2
      echo "       Si tu contraseña lleva @ : / ? # & %  hay que escaparlos (ej. @ -> %40)." >&2
      echo "       La puedes resetear en Supabase > Project Settings > Database > Reset password." >&2
      ;;
    *"Tenant or user not found"*|*"role"*"does not exist"*)
      echo "PISTA: con el pooler el usuario es 'postgres.<project-ref>' (no solo 'postgres')." >&2
      echo "       Cópiala tal cual de Supabase > Project Settings > Database > Session pooler." >&2
      ;;
    *"self-signed certificate"*|*"SSL"*)
      echo "PISTA: añade ?sslmode=require al final de la URL." >&2
      ;;
  esac
  exit 1
fi
echo "    conexión OK"

# ---------------------------------------------------------------------------
# 5) aplicar los sql en orden (000 -> 007)
# ---------------------------------------------------------------------------
for f in "${ARCHIVOS[@]}"; do
  echo "--> $(basename "$f")"
  psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 --no-psqlrc -q -f "$f"
done

# ---------------------------------------------------------------------------
# 6) Verificación: ¿están TODAS las funciones que usa el pipeline?
# ---------------------------------------------------------------------------
ESPERADAS=(
  norm_torrent_text
  torrent_id_es_confiable
  torrent_title_key
  torrent_title_token_ok
  get_torrents_to_enrich
  get_torrents_to_enrich_expr
  get_torrents_missing_ids
  reset_missing_ids_for_retry
  apply_torrent_ids
  get_title_cache
  purge_blocked_torrents
  purge_absolute_only_torrents
  purge_junk_torrents
  purge_dead_torrents
  keep_best_torrents
  keep_best_torrents_es
  is_spanish_text
  report_spanish_gaps
  torrents_ids_stats
  torrents_quality_stats
  torrents_spanish_stats
  renumber_torrent_ids
  torrents_id_report
  torrents_security_audit
)

echo "==> Verificando funciones en la base"
FALTAN=0
for fn in "${ESPERADAS[@]}"; do
  existe=$(psql "$SUPABASE_DB_URL" --no-psqlrc -tAc \
    "select count(*) from pg_proc where pronamespace = 'public'::regnamespace and proname = '$fn';")
  if [ "$existe" -gt 0 ]; then
    printf '    ✔ %s\n' "$fn"
  else
    printf '    ✖ FALTA %s\n' "$fn"
    FALTAN=$((FALTAN + 1))
  fi
done

if [ "$FALTAN" -gt 0 ]; then
  echo "ERROR: faltan $FALTAN funciones. Asegúrate de que sql/ tiene los 8 archivos (000..007)." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 7) columnas de control + aviso de seguridad
# ---------------------------------------------------------------------------
echo "==> Columnas de control en public.torrents"
psql "$SUPABASE_DB_URL" --no-psqlrc -P pager=off -c \
  "select column_name, data_type
     from information_schema.columns
    where table_schema = 'public' and table_name = 'torrents'
      and column_name in ('ids_checked_at','ids_source','ids_confidence','ids_attempts','title_text')
    order by column_name;"

echo "==> Seguridad (¿puede la clave pública borrar?)"
psql "$SUPABASE_DB_URL" --no-psqlrc -P pager=off -c \
  "select nivel, left(hallazgo, 90) as hallazgo from public.torrents_security_audit();" || true

echo "==> OK: esquema y funciones listos ($(( ${#ESPERADAS[@]} - FALTAN ))/${#ESPERADAS[@]} funciones)"
