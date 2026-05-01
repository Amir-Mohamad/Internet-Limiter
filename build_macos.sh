#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install -r requirements-build.txt --quiet

# Onedir + BUNDLE in InternetLimiter_macos.spec (PyInstaller 6: avoids onefile+.app issues).
python3 -m PyInstaller --noconfirm --clean InternetLimiter_macos.spec

echo
echo "Done. App bundle: dist/InternetLimiter.app"
echo "Open it from Finder (you may need to allow it in Privacy & Security on first run)."
