#!/bin/bash
# 编译 TermiusPlus.app 并安装到 /Applications（可用第一个参数指定其他目录）。项目移动后需重新运行。
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
project="$(dirname "$here")"
dest="${1:-/Applications}"
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
app="$build/TermiusPlus.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"

xcrun swiftc -O -o "$app/Contents/MacOS/TermiusPlus" "$here/main.swift" -framework Cocoa -framework WebKit

iconset="$build/AppIcon.iconset"
mkdir -p "$iconset"
xcrun swift "$here/icon.swift" "$build/icon.png"
for s in 16 32 128 256 512; do
  sips -z $s $s "$build/icon.png" --out "$iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$build/icon.png" --out "$iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$app/Contents/Resources/AppIcon.icns"

cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>TermiusPlus</string>
  <key>CFBundleDisplayName</key><string>TermiusPlus</string>
  <key>CFBundleIdentifier</key><string>local.lxd.termiusplus</string>
  <key>CFBundleExecutable</key><string>TermiusPlus</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>TermiusPlusProjectDir</key><string>$project</string>
</dict></plist>
PLIST

codesign --force --deep --sign - "$app"
rm -rf "$dest/TermiusPlus.app"
cp -R "$app" "$dest/"
touch "$dest/TermiusPlus.app"
echo "已安装：${dest}/TermiusPlus.app（服务目录 ${project}）"
