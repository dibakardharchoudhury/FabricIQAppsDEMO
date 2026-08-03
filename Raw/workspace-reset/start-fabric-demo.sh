#!/usr/bin/env bash
# Thin macOS/Linux shim -- all the real logic lives in the cross-platform launch.py.
# Double-click on macOS (may need: chmod +x) or run:  bash start-fabric-demo.sh
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else
  echo
  echo "  [X] Python is not installed."
  echo "      macOS:  brew install python   (or https://www.python.org/downloads/)"
  echo "      Linux:  sudo apt install python3 python3-pip   (or your distro's package manager)"
  echo
  read -r -p "  Press Enter to close..." _ || true
  exit 1
fi

exec "$PY" launch.py
