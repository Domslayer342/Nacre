#!/usr/bin/env bash
# Build Nacre from source on Linux: virtualenv + dependencies + fonts, then (optionally) an app-menu entry.
#   ./scripts/build.sh                 set up .venv and fonts
#   ./scripts/build.sh --desktop       also add Nacre to the app menu
#   ./scripts/build.sh --system        use system PyQt6 (e.g. pacman python-pyqt6-webengine) instead of pip
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
PY=${PYTHON:-python3}
DESKTOP=0 SYSTEM=0
for a in "$@"; do
  case $a in
    --desktop) DESKTOP=1 ;;
    --system) SYSTEM=1 ;;
    -h|--help) sed -n 2,5p "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

"$PY" - <<'PYEOF' || { echo "Nacre needs Python 3.12 or newer" >&2; exit 1; }
import sys; sys.exit(sys.version_info < (3, 12))
PYEOF

if [ ! -d .venv ]; then
  echo "Creating .venv…"
  if [ $SYSTEM = 1 ]; then "$PY" -m venv --system-site-packages .venv; else "$PY" -m venv .venv; fi
fi
VPY="$ROOT/.venv/bin/python"
"$VPY" -m pip install --upgrade pip >/dev/null
if [ $SYSTEM = 1 ]; then
  "$VPY" -c "import PyQt6.QtWebEngineWidgets" 2>/dev/null \
    || { echo "System PyQt6-WebEngine not found (Arch: sudo pacman -S python-pyqt6-webengine)" >&2; exit 1; }
  grep -v '^PyQt6' requirements.txt | "$VPY" -m pip install -r /dev/stdin
else
  "$VPY" -m pip install -r requirements.txt
fi

echo "Fetching fonts…"
"$VPY" scripts/fetch_fonts.py
# web pages (reader mode, autofill menus) need them installed, not just loaded by Nacre
if command -v fc-list >/dev/null; then
  for pair in "Rubik:Rubik" "Material Symbols Rounded:MaterialSymbolsRounded"; do
    fam=${pair%%:*} file=${pair#*:}
    if ! fc-list | grep -q "$fam"; then
      mkdir -p ~/.local/share/fonts && cp nacre/fonts/"$file"*.ttf ~/.local/share/fonts/ && echo "  installed $fam"
    fi
  done
  fc-cache -f ~/.local/share/fonts >/dev/null 2>&1 || true
fi

echo "Checking that Nacre imports…"
"$VPY" -c "import nacre.app"

if [ $DESKTOP = 1 ]; then
  "$VPY" run.py --install-desktop
fi

cat <<MSG

Nacre is ready. Start it with:
  $VPY $ROOT/run.py
Add it to your app menu later with:  $VPY $ROOT/run.py --install-desktop
MSG
