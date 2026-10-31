#!/usr/bin/env bash
# Open psql against the database that run_local.sh built.
#
#   ./scripts/local/psql.sh                   # interactive session
#   ./scripts/local/psql.sh -c '\dt'          # list tables
#   ./scripts/local/psql.sh -f scripts/queries/Q4_hawker_proximity_effect.sql

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export PGHOST=${SOCK_DIR:-/tmp}
export PGPORT=${PGPORT:-5544}
export PGUSER=postgres
export PGDATABASE=${DBNAME:-sd6103}

PG_BIN=${PG_BIN:-$REPO_ROOT/tools/pgsql/bin}
if [[ ! -x "$PG_BIN/psql" ]]; then
    echo "psql not found in $PG_BIN - run ./scripts/local/run_local.sh --download first," >&2
    echo "or set PG_BIN=/path/to/postgres/bin" >&2
    exit 1
fi

exec "$PG_BIN/psql" "$@"
