#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-3.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="IfcGref-Desktop"
DIST_DIR="$ROOT/desktop_dist"
WORK_DIR="$ROOT/build/desktop"
ZIP_PATH="$DIST_DIR/$NAME-macOS-$VERSION.zip"

cd "$ROOT"
mkdir -p "$DIST_DIR"

ENV_DATA_ARGS=()
if [ -f "$ROOT/.env" ]; then
    ENV_DATA_ARGS=(--add-data ".env:.")
fi

python3 -m pip install -r requirements.txt
python3 -m pip install pyinstaller

rm -rf "$ROOT/dist/$NAME.app" "$WORK_DIR"

python3 -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name "$NAME" \
    --distpath "$ROOT/dist" \
    --workpath "$WORK_DIR" \
    --add-data "templates:templates" \
    --add-data "static:static" \
    --add-data "georeference_ifc:georeference_ifc" \
    "${ENV_DATA_ARGS[@]}" \
    --collect-all ifcopenshell \
    --collect-all pyproj \
    --collect-all scipy \
    --collect-all pandas \
    --hidden-import ifcopenshell.geom \
    --hidden-import ifcopenshell.util.element \
    --hidden-import ifcopenshell.util.placement \
    desktop_app.py

rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$ROOT/dist/$NAME.app" "$ZIP_PATH"
echo "Desktop app package created: $ZIP_PATH"
