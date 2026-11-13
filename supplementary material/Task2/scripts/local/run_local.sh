#!/usr/bin/env bash
#
# Bring the project up locally: PostgreSQL cluster -> staging CSVs -> sp01..sp08.
#
#   ./scripts/local/run_local.sh            # build (drops and recreates the database)
#   ./scripts/local/run_local.sh --psql     # build, then open a psql session
#   ./scripts/local/run_local.sh --download # fetch portable PG binaries first
#
# A full build takes about 40 s. It always starts from an empty database, since
# sp05 appends transactions without a duplicate guard and a second run doubles
# the table.
#
# Requires PostgreSQL 16+ server binaries. Set PG_BIN=/path/to/bin to point at
# a specific install; otherwise PATH, Homebrew and Postgres.app are searched.

set -euo pipefail

TASK_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
# data/ and tools/ stay in the repository root, above supplementary material/
REPO_ROOT=$(cd "$TASK_DIR/../.." && pwd)
DBNAME=${DBNAME:-sd6103}
PGPORT=${PGPORT:-5544}
PGDATA_DIR=${PGDATA:-$REPO_ROOT/tools/pgdata}
SOCK_DIR=${SOCK_DIR:-/tmp}
CLEAN="$REPO_ROOT/data/clean_data"

OPEN_PSQL=0
DO_DOWNLOAD=0
for arg in "$@"; do
    case "$arg" in
        --psql)     OPEN_PSQL=1 ;;
        --download) DO_DOWNLOAD=1 ;;
        -h|--help)  sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

find_pg_bin() {
    if [[ -n "${PG_BIN:-}" ]]; then echo "$PG_BIN"; return 0; fi
    if command -v initdb >/dev/null 2>&1; then dirname "$(command -v initdb)"; return 0; fi
    local d
    for d in "$REPO_ROOT/tools/pgsql/bin" \
             /opt/homebrew/opt/postgresql@17/bin \
             /opt/homebrew/opt/postgresql@16/bin \
             /usr/local/opt/postgresql@17/bin \
             /usr/local/opt/postgresql@16/bin \
             /Applications/Postgres.app/Contents/Versions/latest/bin; do
        [[ -x "$d/initdb" ]] && { echo "$d"; return 0; }
    done
    return 1
}

download_pg() {
    local dest="$REPO_ROOT/tools"
    mkdir -p "$dest"
    if [[ -x "$dest/pgsql/bin/initdb" ]]; then
        say "portable PostgreSQL already in tools/pgsql"
        return 0
    fi
    say "downloading PostgreSQL 17.6 binaries (~360 MB, one-off)"
    curl -L --progress-bar -o "$dest/pg.zip" \
        "https://get.enterprisedb.com/postgresql/postgresql-17.6-1-osx-binaries.zip"
    unzip -q -o "$dest/pg.zip" -d "$dest"
    rm -f "$dest/pg.zip"
}

if [[ $DO_DOWNLOAD -eq 1 ]]; then download_pg; fi

if ! PG_BIN_DIR=$(find_pg_bin); then
    cat >&2 <<'EOF'
No PostgreSQL server binaries found.

Pick one of:
  brew install postgresql@17
  PG_BIN=/opt/homebrew/opt/postgresql@17/bin ./scripts/local/run_local.sh
  ./scripts/local/run_local.sh --download     # portable binaries into tools/
EOF
    exit 1
fi

PSQL="$PG_BIN_DIR/psql"
export PGHOST="$SOCK_DIR" PGPORT PGUSER=postgres PGDATABASE="$DBNAME"

say "PostgreSQL: $("$PG_BIN_DIR/postgres" --version)"
say "cluster:    $PGDATA_DIR (port $PGPORT)"

if [[ ! -f "$PGDATA_DIR/PG_VERSION" ]]; then
    mkdir -p "$(dirname "$PGDATA_DIR")"
    "$PG_BIN_DIR/initdb" -D "$PGDATA_DIR" -U postgres --auth=trust -E UTF8 >/dev/null
    echo "initialised a new cluster"
fi

if ! "$PG_BIN_DIR/pg_isready" -q; then
    "$PG_BIN_DIR/pg_ctl" -D "$PGDATA_DIR" -l "$PGDATA_DIR/server.log" -w \
        -o "-p $PGPORT -k $SOCK_DIR -c listen_addresses=127.0.0.1" start >/dev/null
    echo "server started (log: $PGDATA_DIR/server.log)"
fi

say "recreating database $DBNAME"
"$PSQL" -d postgres -q -c "DROP DATABASE IF EXISTS $DBNAME" \
        -c "CREATE DATABASE $DBNAME" 

# ---------------------------------------------------------------------------
# staging: raw landing tables mirror each CSV header, then a narrow staging
# table with the column names the procedures expect. Everything stays TEXT so
# a malformed value shows up as a cast error in the procedure, not in COPY.
# ---------------------------------------------------------------------------

raw_ddl() {  # header -> column list (lower-cased, so the INSERTs can name them plainly)
    printf '%s' "$1" | tr -d '\r' | awk -F, '{
        for (i = 1; i <= NF; i++) { gsub(/"/, "", $i); printf "%s\"%s\" text", (i > 1 ? ", " : ""), tolower($i) }
        print ""
    }'
}

header_of() { head -1 "$1" | tr -d '\r'; }

LOAD_SQL=$(mktemp -t sd6103_load).sql
trap 'rm -f "$LOAD_SQL"' EXIT

{
    echo '\set ON_ERROR_STOP on'
    echo 'DROP SCHEMA IF EXISTS stg CASCADE;'
    echo 'DROP SCHEMA IF EXISTS staging CASCADE;'
    echo 'CREATE SCHEMA stg;'
    echo 'CREATE SCHEMA staging;'

    add_source() {  # name, staging-ddl, insert-select, files...
        local name=$1 ddl=$2 ins=$3; shift 3
        echo "CREATE TABLE stg.$name ($(raw_ddl "$(header_of "$1")"));"
        local f
        for f in "$@"; do
            echo "\\copy stg.$name FROM '$f' WITH (FORMAT csv, HEADER true)"
        done
        echo "CREATE TABLE staging.$name ($ddl);"
        echo "INSERT INTO staging.$name $ins;"
    }

    add_source town "town_name text" \
        "SELECT townname FROM stg.town" \
        "$CLEAN/hdb_town.csv"

    add_source hdb_block \
        "town_name text, block_number text, street text, year_completed text, latitude text, longitude text, postal text" \
        "SELECT town_name, blk_no, street, year_completed, lat, lng, postal FROM stg.hdb_block" \
        "$CLEAN/hdb_block_clean.csv"

    add_source resale \
        "blk_no text, street text, flat_type text, storey_min text, storey_max text, floor_area_sqm text, flat_model text, transaction_year text, transaction_month text, remaining_lease_years text, resale_price text" \
        "SELECT blk_no, street, flat_type, storey_min, storey_max, floor_area_sqm, flat_model, transaction_year, transaction_month, remaining_lease_years, resale_price FROM stg.resale" \
        "$CLEAN/resale/incremental/file1_staging.csv" \
        "$CLEAN/resale/incremental/file2_staging.csv" \
        "$CLEAN/resale/incremental/file3_staging.csv" \
        "$CLEAN/resale/incremental/file4_staging.csv" \
        "$CLEAN/resale/incremental/file5_staging.csv"

    add_source hawker_centre \
        "hawker_name text, street text, latitude text, longitude text, postal text, no_of_mkt_produce_stalls text, no_of_cooked_food_stalls text" \
        "SELECT hawker_name, street, latitude, longitude, postal, no_of_mkt_produce_stalls, no_of_cooked_food_stalls FROM stg.hawker_centre" \
        "$CLEAN/hawker_centre.csv"

    add_source proximity_level \
        "level_id text, category text, distance_min_m text, distance_max_m text" \
        "SELECT level_id, category, distance_min_m, distance_max_m FROM stg.proximity_level" \
        "$CLEAN/proximity_level.csv"

    add_source proximity \
        "blk_no text, street text, hawker_name text, level_id text" \
        "SELECT blk_no, street, hawker_name, level_id FROM stg.proximity" \
        "$CLEAN/proximity.csv"
} > "$LOAD_SQL"

say "loading staging tables"
"$PSQL" -q -f "$LOAD_SQL"
"$PSQL" -q -tAc "
    SELECT format('  %-12s %8s rows', table_name,
                  (xpath('/row/c/text()',
                         query_to_xml(format('SELECT count(*) AS c FROM staging.%I', table_name),
                                      false, true, '')))[1]::text)
    FROM information_schema.tables
    WHERE table_schema = 'staging' ORDER BY table_name;"

# ---------------------------------------------------------------------------
# procedures: deploy then call, in filename order (sp01 -> sp08)
# ---------------------------------------------------------------------------

say "deploying and calling stored procedures"
for f in "$TASK_DIR"/StoredProcedures/sp*.sql; do
    proc=$(grep -oiE 'CREATE +(OR +REPLACE +)?PROCEDURE +[a-zA-Z0-9_.]+' "$f" | head -1 | awk '{print $NF}')
    if [[ -z "$proc" ]]; then echo "  skip $(basename "$f") (no procedure)"; continue; fi
    t0=$SECONDS
    "$PSQL" -q -v ON_ERROR_STOP=1 -f "$f" >/dev/null
    "$PSQL" -q -v ON_ERROR_STOP=1 -c "CALL $proc();" 2>&1 | sed 's/^NOTICE:  /  /'
    echo "  $(basename "$f") -> $proc  ($((SECONDS - t0))s)"
done

say "row counts"
"$PSQL" -c "
SELECT 'town' AS table, count(*) FROM public.town
UNION ALL SELECT 'hdb_block',          count(*) FROM public.hdb_block
UNION ALL SELECT 'hdb_flat',           count(*) FROM public.hdb_flat
UNION ALL SELECT 'resale_transaction', count(*) FROM public.resale_transaction
UNION ALL SELECT 'hawker_centre',      count(*) FROM public.hawker_centre
UNION ALL SELECT 'proximity_level',    count(*) FROM public.proximity_level
UNION ALL SELECT 'proximity',          count(*) FROM public.proximity;"

cat <<EOF

Done. Database "$DBNAME" on port $PGPORT (127.0.0.1 and the socket in $SOCK_DIR).

  ./scripts/local/psql.sh     # opens psql in $DBNAME
  ./scripts/local/psql.sh -c '\\dt'                  # list tables
  ./scripts/local/psql.sh -c '\\d proximity'          # one table's columns and keys
  ./scripts/local/psql.sh -c 'TABLE proximity_level;'
  ./scripts/local/psql.sh -f scripts/queries/Q4_hawker_proximity_effect.sql

GUI clients connect to 127.0.0.1:$PGPORT, database "$DBNAME", user postgres,
no password (pgAdmin/DBeaver/TablePlus).

Start the server again later:  $PG_BIN_DIR/pg_ctl -D "$PGDATA_DIR" start
Stop it:                       $PG_BIN_DIR/pg_ctl -D "$PGDATA_DIR" stop
EOF

if [[ $OPEN_PSQL -eq 1 ]]; then
    exec "$PSQL"
fi
