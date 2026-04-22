#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install -r requirements-build.txt --quiet

python3 -m PyInstaller --noconfirm --clean \
  --windowed \
  --name InternetLimiter \
  --osx-bundle-identifier com.local.internetlimiter \
  --collect-all customtkinter \
  app_gui.py

echo
echo "Done. App bundle: dist/InternetLimiter.app"
echo "Open it from Finder (you may need to allow it in Privacy & Security on first run)."
