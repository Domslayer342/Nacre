#!/usr/bin/env bash
# Linux CI smoke test: launch Nacre under a virtual X server with packaging/smoke.py and check it reported back.
#   smoke.sh <label> <program> [args...]
set -u
label=$1; shift
root=$(cd "$(dirname "$0")/.." && pwd)
out="$root/smoke/$label"; mkdir -p "$out"
tmp=$(mktemp -d)
export XDG_CONFIG_HOME=$tmp/config XDG_DATA_HOME=$tmp/data XDG_CACHE_HOME=$tmp/cache
export NACRE_SMOKE_OUT=$out NACRE_SCRIPT=$root/packaging/smoke.py QT_QPA_PLATFORM=xcb
export QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-sandbox"       # CI runners have no GPU; root-less sandbox off
timeout 150 xvfb-run -a -s "-screen 0 1600x1000x24" "$@" >"$out/stdout.txt" 2>"$out/stderr.txt"
code=$?
[ -f "$XDG_CACHE_HOME/nacre/crash.log" ] && cp "$XDG_CACHE_HOME/nacre/crash.log" "$out/"
if [ -f "$out/smoke.txt" ]; then
  echo "[$label] exit=$code ok"; sed 's/^/  /' "$out/smoke.txt"; exit 0
fi
echo "[$label] exit=$code FAILED"; tail -20 "$out/stderr.txt" | sed 's/^/  /'
[ -f "$out/crash.log" ] && head -20 "$out/crash.log" | sed 's/^/  /'
exit 1
