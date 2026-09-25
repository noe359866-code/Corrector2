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
# ============================================================================
set -euo pipefail

: "${SUPABASE_DB_URL:?Falta la variable SUPABASE_DB_URL (Connection string de Supabase)}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_DIR="${HERE}/../sql"

if ! command -v psql >/dev/null 2>&1; then
  echo "ERROR: psql no está instalado. En Ubuntu: sudo apt-get install -y postgresql-client" >&2
  exit 1
fi

echo "==> Aplicando migraciones en: ${SUPABASE_DB_URL%%\?*}"
for f in "${SQL_DIR}"/*.sql; do
  echo "--> $(basename "$f")"
  psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 --no-psqlrc -q -f "$f"
done

# ---------------------------------------------------------------------------
# Verificación: ¿están TODAS las funciones que usa el pipeline?
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
  echo "ERROR: faltan $FALTAN funciones. Revisa que se hayan aplicado los 7 archivos sql/." >&2
  exit 1
fi
echo "==> OK: esquema y funciones listos ($(( ${#ESPERADAS[@]} - FALTAN ))/${#ESPERADAS[@]} funciones)"
