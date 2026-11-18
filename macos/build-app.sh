#!/usr/bin/env bash
# Builds "NOTORYX Ops.app": a native window (macos/App/main.swift) that sets up the
# bundled station wheel in ~/Library/Application Support/NOTORYX Ops/venv on first run,
# starts the station and shows the dashboard. Needs Xcode command line tools to build
# and Python 3.10 or newer on the Mac that runs it.
# Usage: macos/build-app.sh <path to notoryx_ops wheel> <output folder>
set -euo pipefail
WHEEL="$1"
OUT="$2"
HERE="$(cd "$(dirname "$0")" && pwd)"
VERSION=$(basename "$WHEEL" | sed -E 's/notoryx_ops-([^-]+)-.*/\1/')
APP="$OUT/NOTORYX Ops.app"
BIN="$APP/Contents/MacOS/NOTORYX Ops"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

for arch in x86_64 arm64; do
  swiftc -swift-version 5 -O -target "$arch-apple-macos11" "$HERE/App/main.swift" -o "$TMP/ops-$arch"
done
lipo -create "$TMP/ops-x86_64" "$TMP/ops-arm64" -output "$BIN"

cp "$WHEEL" "$APP/Contents/Resources/"
cp "$HERE/AppIcon.icns" "$APP/Contents/Resources/"

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
  <key>CFBundleExecutable</key><string>NOTORYX Ops</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>MIT License</string>
  <key>NSBluetoothAlwaysUsageDescription</key><string>NOTORYX Ops connects to your MeshCore radio over Bluetooth.</string>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST

# Ad-hoc signature so Apple Silicon Macs run the universal binary. Not a Developer ID signature.
codesign --force --deep --sign - "$APP"
echo "Built $APP"
