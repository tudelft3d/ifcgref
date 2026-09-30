from pathlib import Path
import os
import plistlib
import shutil
import stat
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = sys.argv[1] if len(sys.argv) > 1 else "3.0"
APP_NAME = "IfcGref-Desktop"
BUILD_ROOT = ROOT / "build" / "macos-portable"
APP_BUNDLE = BUILD_ROOT / f"{APP_NAME}.app"
RESOURCES_APP = APP_BUNDLE / "Contents" / "Resources" / "app"
MACOS_DIR = APP_BUNDLE / "Contents" / "MacOS"
DIST_DIR = ROOT / "desktop_dist"
ZIP_PATH = DIST_DIR / f"{APP_NAME}-macOS-{VERSION}.zip"

INCLUDE_FILES = [
    "app.py",
    "desktop_app.py",
    "requirements.txt",
    "LICENSE",
]
INCLUDE_DIRS = [
    "georeference_ifc",
    "static",
    "templates",
]


def copy_tree(src, dst):
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    shutil.copytree(src, dst, ignore=ignore)


def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def zip_app_bundle():
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in APP_BUNDLE.rglob("*"):
            rel = path.relative_to(BUILD_ROOT).as_posix()
            info = zipfile.ZipInfo(rel + "/" if path.is_dir() else rel)
            mode = path.stat().st_mode
            if path == MACOS_DIR / APP_NAME:
                mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            info.external_attr = (mode & 0xFFFF) << 16
            if path.is_dir():
                archive.writestr(info, "")
            else:
                with path.open("rb") as file:
                    archive.writestr(info, file.read())


def main():
    if BUILD_ROOT.exists():
        shutil.rmtree(BUILD_ROOT)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    RESOURCES_APP.mkdir(parents=True, exist_ok=True)
    MACOS_DIR.mkdir(parents=True, exist_ok=True)

    include_files = list(INCLUDE_FILES)
    if (ROOT / ".env").exists():
        include_files.append(".env")

    for filename in include_files:
        shutil.copy2(ROOT / filename, RESOURCES_APP / filename)
    for dirname in INCLUDE_DIRS:
        copy_tree(ROOT / dirname, RESOURCES_APP / dirname)

    (APP_BUNDLE / "Contents" / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": APP_NAME,
        "CFBundleIdentifier": "nl.tudelft.ifcgref.desktop",
        "CFBundleName": "IfcGref",
        "CFBundleDisplayName": "IfcGref",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
    }))

    launcher = f"""#!/bin/zsh
set -e

APP_RESOURCES="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
DATA_DIR="${{HOME}}/Library/Application Support/IfcGref"
VENV_DIR="${{DATA_DIR}}/venv"
PYTHON_BIN="${{VENV_DIR}}/bin/python"

mkdir -p "$DATA_DIR"

if [ ! -x "$PYTHON_BIN" ]; then
    /usr/bin/python3 -m venv "$VENV_DIR"
fi

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r "$APP_RESOURCES/requirements.txt"

export IFCGREF_DESKTOP=1
export IFCGREF_DATA_DIR="$DATA_DIR"
export IFCGREF_DEBUG=0

cd "$APP_RESOURCES"
exec "$PYTHON_BIN" "$APP_RESOURCES/desktop_app.py"
"""
    launcher_path = MACOS_DIR / APP_NAME
    write_text(launcher_path, launcher)
    os.chmod(launcher_path, 0o755)

    zip_app_bundle()
    print(f"Portable macOS app package created: {ZIP_PATH}")


if __name__ == "__main__":
    main()
