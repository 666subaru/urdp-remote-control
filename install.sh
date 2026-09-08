#!/bin/sh
# User-level install: no root, nothing outside $HOME is touched.
set -e

root=$(cd -P "$(dirname "$0")" && pwd)
bindir="${XDG_BIN_HOME:-$HOME/.local/bin}"
appdir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

mkdir -p "$bindir" "$appdir"
ln -sf "$root/bin/urdp" "$bindir/urdp"
sed "s|^Exec=urdp|Exec=$bindir/urdp|" "$root/urdp.desktop" > "$appdir/urdp.desktop"

command -v update-desktop-database >/dev/null 2>&1 &&
    update-desktop-database "$appdir" >/dev/null 2>&1 || true

echo "Kuruldu:"
echo "  $bindir/urdp"
echo "  $appdir/urdp.desktop"
case ":$PATH:" in
    *":$bindir:"*) ;;
    *) echo "Not: $bindir PATH'te değil. Fish için:"
       echo "  fish_add_path $bindir" ;;
esac
