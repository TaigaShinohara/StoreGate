#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build="$here/.build"
bundle="$build/StoreGate.app"
repo="$(dirname "$here")"
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
    swift_build "$here/Sources/ReleaseFlow.swift" "$here/Sources/DeliveryFlow.swift" \
      "$here/Sources/ArchiveInspection.swift" "$here/Sources/DeliveryAudit.swift" \
      "$here/Tests/DeliveryFlowChecks.swift" -o "$build/DeliveryFlowChecks"
    "$build/DeliveryFlowChecks"
    ;;
  "")
    swift_build "$here/Sources/"*.swift -o "$build/StoreGate"
    mkdir -p "$bundle/Contents/MacOS"
    resources="$bundle/Contents/Resources/StoreGateTools"
    mkdir -p "$resources/scripts" "$resources/data"
    cp "$here/Resources/audit_bridge.py" "$resources/"
    cp "$repo/scripts/metadata-audit.py" "$repo/scripts/ruling-register.py" "$resources/scripts/"
    cp "$repo/data/rejection-patterns.json" "$repo/data/ruling-register.json" "$resources/data/"
    cp "$repo/LICENSE" "$repo/NOTICE" "$resources/"
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
