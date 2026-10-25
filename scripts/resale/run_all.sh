#!/bin/bash
# ============================================================
# SD6103 Project: One-click setup for Resale tables
#
# Loads resale data via staging tables with JOIN-based ID
# resolution, compatible with GENERATED ALWAYS AS IDENTITY.
#
# Prerequisites:
#   - PostgreSQL installed and running
#   - Database sd6103_project exists
#   - Tables town and hdb_block already created and loaded
#
# Usage:
#   cd <project_root>
#   bash scripts/resale/run_all.sh
# ============================================================

set -e  # stop on first error

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "============================================"
echo "Step 1: Clean raw resale data"
echo "============================================"
python3 scripts/resale/01_clean_resale.py

echo ""
echo "============================================"
echo "Step 2: Prepare staging CSVs"
echo "============================================"
python3 scripts/resale/02_split_resale.py

echo ""
echo "============================================"
echo "Step 3: Create tables in PostgreSQL"
echo "============================================"
psql -U postgres -d sd6103_project -f scripts/resale/03_create_tables.sql

echo ""
echo "============================================"
echo "Step 4: Load all 5 files via staging tables"
echo "============================================"
cd data/src/resale/incremental
psql -U postgres -d sd6103_project -f "$PROJECT_ROOT/scripts/resale/04_incremental_load.sql"

echo ""
echo "============================================"
echo "Done! All resale data loaded."
echo "============================================"
psql -U postgres -d sd6103_project -c "SELECT 'hdb_flat' AS t, COUNT(*) FROM hdb_flat UNION ALL SELECT 'resale_transaction', COUNT(*) FROM resale_transaction;"
