#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build="$here/.build"
bundle="$build/StoreGate.app"
mkdir -p "$build/ModuleCache"
sdk="$(xcrun --sdk macosx --show-sdk-path)"
arch="$(uname -m)"

swift_build() {
  xcrun --sdk macosx swiftc \
    -swift-version 6 -target "$arch-apple-macosx14.0" -sdk "$sdk" \
    -module-cache-path "$build/ModuleCache" "$@"
}

case "${1:-}" in
  --test)
    swift_build "$here/Sources/ReleaseFlow.swift" "$here/Tests/ReleaseFlowChecks.swift" -o "$build/ReleaseFlowChecks"
    "$build/ReleaseFlowChecks"
    ;;
  "")
    swift_build "$here/Sources/ReleaseFlow.swift" "$here/Sources/StoreGateApp.swift" -o "$build/StoreGate"
    mkdir -p "$bundle/Contents/MacOS"
    /usr/bin/plutil -lint "$here/Info.plist"
    cp "$here/Info.plist" "$bundle/Contents/Info.plist"
    mv -f "$build/StoreGate" "$bundle/Contents/MacOS/StoreGate"
    /usr/bin/codesign --force --sign - "$bundle"
    printf '%s\n' "$bundle"
    ;;
  *)
    printf 'Usage: bash %s [--test]\n' "$0" >&2
    exit 2
    ;;
esac
