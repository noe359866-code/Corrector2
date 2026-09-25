#!/usr/bin/env bash
# ============================================================================
#  simulate_workflow.sh · corre en local los MISMOS pasos del GitHub Action
#
#    bash scripts/simulate_workflow.sh            # contra Supabase (.env cargado)
#    bash scripts/simulate_workflow.sh --local    # sin Supabase: Postgres local
#                                                 # + PostgREST simulado
#      Requiere: MOCK_DB_URL=postgresql://... (con sql/*.sql aplicado y el
#      fixture: psql "$MOCK_DB_URL" -f tests/fixture_e2e.sql)
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
cd "$ROOT"

LOCAL=0
if [ "${1:-}" = "--local" ]; then LOCAL=1; fi

MOCK_PID=""
cleanup() {
  if [ -n "$MOCK_PID" ]; then kill "$MOCK_PID" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

if [ "$LOCAL" = 1 ]; then
  [ -n "${MOCK_DB_URL:-}" ] || { echo "ERROR: falta MOCK_DB_URL"; exit 1; }
  export MOCK_PORT="${MOCK_PORT:-8899}"
  echo "==> mock PostgREST en :$MOCK_PORT"
  python tests/mock_postgrest.py > logs/mock.log 2>&1 &
  MOCK_PID=$!
  sleep 1
  export SUPABASE_URL="http://localhost:${MOCK_PORT}"
  export SUPABASE_SERVICE_KEY="mock"
fi

mkdir -p reportes logs

echo "==> doctor"
python -m src.main doctor || true

echo "==> 1/6 limpieza"
SUMMARY_FILE=reportes/1-limpieza.md SUMMARY_JSON=reportes/1-limpieza.json \
  REPORT_TO_STEP_SUMMARY=false python -m src.main purge

echo "==> 2/6 IDs"
SUMMARY_FILE=reportes/2-ids.md SUMMARY_JSON=reportes/2-ids.json \
  REPORT_TO_STEP_SUMMARY=false python -m src.main enrich

echo "==> 3/6 reintentos"
SUMMARY_FILE=reportes/3-reintentos.md SUMMARY_JSON=reportes/3-reintentos.json \
  REPORT_TO_STEP_SUMMARY=false python -m src.main retry || true

echo "==> 4/6 español"
SUMMARY_FILE=reportes/4-espanol.md SUMMARY_JSON=reportes/4-espanol.json \
  REPORT_TO_STEP_SUMMARY=false python -m src.main best

if [ "${RENUMBER_IDS:-true}" = "true" ]; then
  echo "==> 5/6 reordenar id"
  SUMMARY_FILE=reportes/5-reordenar.md SUMMARY_JSON=reportes/5-reordenar.json \
    REPORT_TO_STEP_SUMMARY=false python -m src.main renumber || true
fi

echo "==> 6/6 estado final"
python -m src.main stats || true

echo "==> uniendo reportes en summary.md"
: > summary.md
for f in reportes/*.md; do
  [ -f "$f" ] || continue
  { echo "<!-- $f -->"; cat "$f"; echo; echo "---"; echo; } >> summary.md
done
[ -s summary.md ] || echo "_Sin reportes en esta ejecución._" > summary.md
echo "reporte final: $(wc -l < summary.md) líneas"
