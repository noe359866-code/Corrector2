#!/usr/bin/env bash
# ============================================================================
#  test_all.sh · pruebas completas en local
#
#    bash scripts/test_all.sh            # pytest + selftest (+ SQL si hay TEST_DB_URL)
#    bash scripts/test_all.sh --docker   # + Postgres 16 en Docker (apply 2x + smoke)
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
cd "$ROOT"

USE_DOCKER=0
if [ "${1:-}" = "--docker" ]; then USE_DOCKER=1; fi

echo "==> 1/3 pytest (unitarias offline)"
python -m pytest tests -q

echo "==> 2/3 selftest del CLI (offline)"
python -m src.main selftest

echo "==> 3/3 SQL contra Postgres real"
cleanup() {
  if [ -n "${CID:-}" ]; then docker rm -f "$CID" >/dev/null 2>&1 || true; fi
}
if [ "$USE_DOCKER" = 1 ]; then
  command -v docker >/dev/null || { echo "ERROR: falta docker"; exit 1; }
  CID="$(docker run -d --rm -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=postgres -e POSTGRES_DB=torrents_test -p 55433:5432 postgres:16)"
  trap cleanup EXIT
  export TEST_DB_URL="postgresql://postgres:postgres@localhost:55433/torrents_test"
  echo "    esperando a Postgres..."
  for _ in $(seq 1 30); do
    docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break
    sleep 1
  done
  psql "$TEST_DB_URL" -v ON_ERROR_STOP=1 -c "
    do \$\$ begin
      if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
      if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
      if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin; end if;
    end \$\$;"
  echo "    aplicando sql/ 2 veces (idempotencia)..."
  SUPABASE_DB_URL="$TEST_DB_URL" bash scripts/apply_sql.sh >/dev/null
  SUPABASE_DB_URL="$TEST_DB_URL" bash scripts/apply_sql.sh >/dev/null
  echo "    smoke test..."
  python tests/sql_smoke.py
elif [ -n "${TEST_DB_URL:-}" ]; then
  SUPABASE_DB_URL="$TEST_DB_URL" bash scripts/apply_sql.sh >/dev/null
  SUPABASE_DB_URL="$TEST_DB_URL" bash scripts/apply_sql.sh >/dev/null
  python tests/sql_smoke.py
else
  echo "    (sin TEST_DB_URL ni --docker: se omite la parte SQL)"
  echo "    pon TEST_DB_URL=postgresql://... o usa --docker para el todo."
fi

echo "==> OK: test_all.sh terminó"
