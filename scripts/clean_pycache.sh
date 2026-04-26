#!/usr/bin/env bash
# clean_pycache.sh — Purge all __pycache__ dirs and .pyc files from the project.
# Run before deployment to prevent logic drift from stale bytecode.
#
# Usage:
#   bash scripts/clean_pycache.sh
#   chmod +x scripts/clean_pycache.sh && ./scripts/clean_pycache.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "[clean_pycache] Purging __pycache__ directories and .pyc files..."
echo "[clean_pycache] Project root: $PROJECT_ROOT"

find "$PROJECT_ROOT" \
  -not -path "$PROJECT_ROOT/.venv/*" \
  -type d -name "__pycache__" \
  -exec rm -rf {} + 2>/dev/null || true

find "$PROJECT_ROOT" \
  -not -path "$PROJECT_ROOT/.venv/*" \
  -name "*.pyc" -o -name "*.pyo" \
  -delete 2>/dev/null || true

echo "[clean_pycache] Done. All stale bytecode removed."
