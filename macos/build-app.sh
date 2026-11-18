#!/usr/bin/env bash
# Builds "NOTORYX Ops.app": a small launcher that installs the bundled wheel into
# ~/Library/Application Support/NOTORYX Ops/venv on first run, starts the station
# and opens the dashboard. Needs Python 3.10 or newer from python.org or Homebrew.
# Usage: macos/build-app.sh <path to notoryx_ops wheel> <output folder>
set -euo pipefail
WHEEL="$1"
OUT="$2"
VERSION=$(basename "$WHEEL" | sed -E 's/notoryx_ops-([^-]+)-.*/\1/')
APP="$OUT/NOTORYX Ops.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$WHEEL" "$APP/Contents/Resources/"
cp "$(dirname "$0")/AppIcon.icns" "$APP/Contents/Resources/" 2>/dev/null || true

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>NOTORYX Ops</string>
  <key>CFBundleDisplayName</key><string>NOTORYX Ops</string>
  <key>CFBundleIdentifier</key><string>com.notoryx.ops</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleExecutable</key><string>notoryx-ops-launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><true/>
  <key>NSBluetoothAlwaysUsageDescription</key><string>NOTORYX Ops connects to your MeshCore radio over Bluetooth.</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/notoryx-ops-launcher" <<'LAUNCH'
#!/bin/bash
set -u
HERE="$(cd "$(dirname "$0")/../Resources" && pwd)"
SUPPORT="$HOME/Library/Application Support/NOTORYX Ops"
VENV="$SUPPORT/venv"
LOG="$SUPPORT/station.log"
mkdir -p "$SUPPORT"
WHEEL=$(ls "$HERE"/notoryx_ops-*.whl | head -1)
STAMP="$VENV/.installed-$(basename "$WHEEL")"

say() { osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with title \"NOTORYX Ops\"" >/dev/null 2>&1; }

PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /usr/bin/python3; do
  if [ -x "$c" ] && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  say "NOTORYX Ops needs Python 3.10 or newer. Install it from python.org, then open NOTORYX Ops again."
  exit 1
fi

if [ ! -f "$STAMP" ]; then
  rm -rf "$VENV"
  "$PY" -m venv "$VENV" >>"$LOG" 2>&1 && "$VENV/bin/pip" install --quiet "$WHEEL" >>"$LOG" 2>&1 || {
    say "Setting up NOTORYX Ops failed. Details are in $LOG"
    exit 1
  }
  touch "$STAMP"
fi

PORT=$("$VENV/bin/python" - <<'PY'
from notoryx_ops import config
print(config.load().port)
PY
)
if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/station"; then
  open "http://127.0.0.1:$PORT/"
  exit 0
fi
exec "$VENV/bin/notoryx-ops" run --open >>"$LOG" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/notoryx-ops-launcher"
echo "Built $APP"
