#!/usr/bin/env bash
# Build dist/SalesAutomation.app for macOS (double-clickable). Run on a Mac only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script must run on macOS (produces a .app bundle)." >&2
  exit 1
fi

PYTHON="${PYTHON:-python3}"
"$PYTHON" scripts/refresh_app_ico.py
"$PYTHON" -m PyInstaller build_mac.spec --noconfirm \
  --distpath "$ROOT/dist" \
  --workpath "$ROOT/build/pyi_work_mac"

echo ""
echo "Built: $ROOT/dist/SalesAutomation.app"
echo "Copy .env next to the .app (same folder) if you use Supabase upload."
