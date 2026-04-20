#!/usr/bin/env bash
# Optional: build assets/AppIcon.icns from assets/app_brand.png (Dock / Finder icon for the .app).
# Requires macOS (sips + iconutil). Run before PyInstaller if you want a custom bundle icon.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PNG="$ROOT/assets/app_brand.png"
SET="$ROOT/assets/AppIcon.iconset"
OUT="$ROOT/assets/AppIcon.icns"
if [[ ! -f "$PNG" ]]; then
  echo "Missing $PNG" >&2
  exit 1
fi
rm -rf "$SET"
mkdir -p "$SET"
# Standard iconset sizes for iconutil
sips -z 16 16   "$PNG" --out "$SET/icon_16x16.png"       >/dev/null
sips -z 32 32   "$PNG" --out "$SET/icon_16x16@2x.png"   >/dev/null
sips -z 32 32   "$PNG" --out "$SET/icon_32x32.png"      >/dev/null
sips -z 64 64   "$PNG" --out "$SET/icon_32x32@2x.png"   >/dev/null
sips -z 128 128 "$PNG" --out "$SET/icon_128x128.png"    >/dev/null
sips -z 256 256 "$PNG" --out "$SET/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$PNG" --out "$SET/icon_256x256.png"    >/dev/null
sips -z 512 512 "$PNG" --out "$SET/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$PNG" --out "$SET/icon_512x512.png"    >/dev/null
sips -z 1024 1024 "$PNG" --out "$SET/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$SET" -o "$OUT"
echo "Wrote $OUT"
