#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
case "${1:-help}" in
  setup)
    HOMEBREW_NO_AUTO_UPDATE=1 brew install python@3.14 ninja pkgconf glib pixman dtc keystone
    /opt/homebrew/bin/python3.14 -m venv "$ROOT/.venv"
    "$PY" -m pip install -r "$ROOT/requirements-macos.txt"
    "$PY" -c 'from pathlib import Path; import keystone as k' 2>/dev/null || ln -sf /opt/homebrew/opt/keystone/lib/libkeystone.dylib "$ROOT/.venv/lib/python3.14/site-packages/keystone/libkeystone.dylib"
    ;;
  build|smoke|verify|board-check|probe)
    action=$1; shift
    exec "$PY" -B "$ROOT/tools/macos_runtime.py" "$action" "$@"
    ;;
  doctor|test|import)
    exec "$PY" -B "$ROOT/tools/emu_macos.py" "$@"
    ;;
  start|serve|status|stop)
    exec "$PY" -B "$ROOT/tools/emu_macos.py" "$@"
    ;;
  *)
    printf '%s\n' 'MEB-EMU 8704 HVF: start | serve | status | stop | setup | doctor | verify | test'
    ;;
esac
