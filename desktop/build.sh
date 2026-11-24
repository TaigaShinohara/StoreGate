#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build="${STOREGATE_DESKTOP_BUILD_DIR:-$here/.build}"
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

build_icon() {
  local iconset="$build/StoreGate.iconset"
  local source="$here/Resources/AppIcon.png"
  local size retina
  mkdir -p "$iconset"
  for size in 16 32 128 256 512; do
    retina=$((size * 2))
    /usr/bin/sips -z "$size" "$size" "$source" \
      --out "$iconset/icon_${size}x${size}.png" >/dev/null
    /usr/bin/sips -z "$retina" "$retina" "$source" \
      --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
  done
  /usr/bin/iconutil --convert icns --output "$bundle/Contents/Resources/StoreGate.icns" "$iconset"
}

case "${1:-}" in
  --test)
    swift_build "$here/Sources/OnboardingFlow.swift" "$here/Sources/OnboardingStore.swift" \
      "$here/Tests/OnboardingChecks.swift" -o "$build/OnboardingChecks"
    "$build/OnboardingChecks"
    swift_build "$here/Sources/ReleaseFlow.swift" "$here/Tests/ReleaseFlowChecks.swift" -o "$build/ReleaseFlowChecks"
    "$build/ReleaseFlowChecks"
    swift_build "$here/Sources/ReleaseFlow.swift" "$here/Sources/DeliveryInputs.swift" "$here/Sources/DeliveryFlow.swift" \
      "$here/Sources/ArchiveInspection.swift" "$here/Sources/DeliveryAudit.swift" \
      "$here/Sources/DeliveryStore.swift" \
      "$here/Tests/DeliveryFlowChecks.swift" -o "$build/DeliveryFlowChecks"
    "$build/DeliveryFlowChecks"
    ;;
  "")
    swift_build "$here/Sources/"*.swift -o "$build/StoreGate"
    mkdir -p "$bundle/Contents/MacOS"
    resources="$bundle/Contents/Resources/StoreGateTools"
    mkdir -p "$resources/scripts" "$resources/data" "$resources/delivery" "$resources/agent-os/hooks"
    build_icon
    cp "$here/Resources/audit_bridge.py" "$resources/"
    cp "$repo/delivery/"{storegate,assets,builds,stores,apple_assets,testing,jdk,preflight}.py "$resources/delivery/"
    cp "$here/Resources/PrivacyInfo.xcprivacy" "$bundle/Contents/Resources/"
    python3 "$repo/scripts/validate-privacy-manifest.py" "$bundle/Contents/Resources/PrivacyInfo.xcprivacy"
    cp "$repo/agent-os/hooks/storegate-guard.sh" "$resources/agent-os/hooks/"
    cp "$repo/scripts/metadata-audit.py" "$repo/scripts/ruling-register.py" \
      "$repo/scripts/macos-source-audit.py" "$repo/scripts/validate-privacy-manifest.py" \
      "$repo/scripts/android-target-sdk.py" "$repo/scripts/fleet-sweep.py" \
      "$repo/scripts/deadline-checker.py" "$resources/scripts/"
    xcrun xcstringstool compile "$here/Resources/Audit.xcstrings" --output-directory "$bundle/Contents/Resources"
    cp "$repo/data/rejection-patterns.json" "$repo/data/ruling-register.json" \
      "$repo/data/regulatory-deadlines.json" "$resources/data/"
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
