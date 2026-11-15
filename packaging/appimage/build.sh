#!/bin/sh
# Build URDP-Remote-Control-x86_64.AppImage.
#
# Needs python-appimage (pip install python-appimage) and a C compiler.  The
# image carries Python, PyQt6 and URDP; FreeRDP stays a system package, and the
# application offers to install it when it is missing.
set -e

here=$(cd -P "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
out=${1:-$root/dist}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# A clean copy of the package, plus the prebuilt file-list library.
mkdir -p "$work/stage" "$work/recipe"
cp -r "$root/urdp" "$work/stage/urdp"
find "$work/stage" -name __pycache__ -prune -exec rm -rf {} +
cc -O2 -shared -fPIC -o "$work/stage/urdp/native/libfilelist.so" \
    "$work/stage/urdp/native/filelist.c" -ldl

cp "$here/entrypoint.sh" "$here/requirements.txt" "$here/urdp.desktop" \
    "$work/recipe/"
QT_QPA_PLATFORM=offscreen PYTHONPATH="$work/stage" "${QT_PYTHON:-/usr/bin/python3}" - "$work/recipe/urdp.png" <<'PY'
import sys
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from urdp.widgets import draw_monitor_pixmap
draw_monitor_pixmap(256).save(sys.argv[1])
PY

# python-appimage passes the output name to a shell unquoted, and the name
# comes from the desktop file ("URDP Remote Control"), so it only prepares the
# AppDir and appimagetool packs it here.
(cd "$work" && PYTHONPATH="$work/stage" python-appimage build app -p 3.12 \
    --no-packaging "$work/recipe")
appdir=$(find "$work" -maxdepth 1 -type d -name '*-x86_64' | head -1)
chmod -R a+rX "$appdir"

mkdir -p "$out"
ARCH=x86_64 "${APPIMAGETOOL:-appimagetool}" --no-appstream "$appdir" \
    "$out/URDP-Remote-Control-x86_64.AppImage"
ls -l "$out/URDP-Remote-Control-x86_64.AppImage"
