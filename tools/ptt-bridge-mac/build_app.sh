#!/bin/bash
# Builds "Radio Remote PTT.app" (Apple silicon + Intel) and radio-remote-ptt-mac.zip. Needs the Xcode command line tools.
set -euo pipefail
cd "$(dirname "$0")"
VER="${1:-$(sed -n 's/^__version__ = "\(.*\)"/\1/p' ../../backend/radio_remote/__init__.py)}"
OUT=build; APP="$OUT/Radio Remote PTT.app"
rm -rf "$OUT"; mkdir -p "$APP/Contents/MacOS"
for arch in arm64 x86_64; do swiftc -O -target "$arch-apple-macos13" -o "$OUT/ptt-$arch" ptt-bridge.swift; done
lipo -create -output "$APP/Contents/MacOS/ptt-bridge" "$OUT/ptt-arm64" "$OUT/ptt-x86_64"
cat > "$APP/Contents/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>org.radioremote.pttbridge</string>
<key>CFBundleName</key><string>Radio Remote PTT</string>
<key>CFBundleExecutable</key><string>ptt-bridge</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>$VER</string>
<key>CFBundleShortVersionString</key><string>$VER</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>LSUIElement</key><true/>
<key>NSBluetoothAlwaysUsageDescription</key><string>Radio Remote PTT reads the PTT button of your Bluetooth speaker-mic and passes it to the Radio Remote page on this computer.</string>
</dict></plist>
PL
codesign --force --sign - "$APP"
ditto -c -k --keepParent "$APP" "$OUT/radio-remote-ptt-mac.zip"
echo "built $OUT/radio-remote-ptt-mac.zip"
