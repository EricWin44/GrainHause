#!/bin/bash
# Builds GrainHause.app for macOS (Apple Silicon or Intel — builds for the Mac you run it on).
# Needs Python 3.10+ (python.org installer or `brew install python`).
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --windowed --name GrainHause --icon assets/icon.icns --add-data "assets:assets" \
  --argv-emulation \
  --osx-bundle-identifier com.grainhause.app \
  --collect-all rawpy --collect-all imagecodecs --collect-all pillow_heif \
  run.py
echo
echo "Done: dist/GrainHause.app"
echo "Drag it into /Applications. First launch: right-click > Open (it isn't signed by Apple)."
