#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
qa_dir="$(mktemp -d "${STOREGATE_UI_BUILD_ROOT:-/private/tmp}/storegate-delivery-ui.XXXXXX")"
workspace="$(mktemp -d /private/tmp/storegate-delivery-state.XXXXXX)"
build="$qa_dir/build"
bundle="$build/StoreGate.app"
export TMPDIR="$qa_dir/tmp"
mkdir -p "$TMPDIR"
printf 'StoreGate UI fixture: %s\nState: %s\n' "$qa_dir" "$workspace"

STOREGATE_DESKTOP_BUILD_DIR="$build" bash "$here/build.sh"
xcrun --sdk macosx swiftc -swift-version 6 -D STOREGATE_UI_CHECK -target "$(uname -m)-apple-macosx14.0" \
  -module-cache-path "$build/ModuleCache" \
  "$here/Sources/SharedDeliveryView.swift" "$here/Sources/TestingPane.swift" "$here/Sources/ResumePane.swift" \
  "$here/Tests/SharedDeliveryUIChecks.swift" -o "$bundle/Contents/MacOS/StoreGate"
/usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier io.github.taigashinohara.storegate.delivery-ui-check' "$bundle/Contents/Info.plist"
/usr/bin/codesign --force --sign - "$bundle"

export PYTHONDONTWRITEBYTECODE=1
python3 - "$repo" "$workspace" <<'PY'
import json
from pathlib import Path
import sys

repo, fixture = map(Path, sys.argv[1:])
profile = {"id": "fixture", "name": "StoreGate UI Fixture", "repo": str(repo), "targets": {
    platform: {"bundle": "org.example.fixture", "build_method": "ci"}
    for platform in ("android", "ios")
}}
(fixture / "profile.json").write_text(json.dumps(profile))
release = {"app": "fixture", "version": "1.0.12", "build": "108"}
(fixture / "android.json").write_text(json.dumps({**release, "platform": "android", "track": "internal",
    "test_plan": {"Android QA": ["PDFを開く"]}}))
(fixture / "ios.json").write_text(json.dumps({**release, "platform": "ios", "track": "production", "price": "free"}))
PY
engine="$bundle/Contents/Resources/StoreGateTools/delivery/storegate.py"
gate() { python3 "$engine" --workspace "$workspace" "$@" >/dev/null; }
gate register "$workspace/profile.json"
gate release "$workspace/android.json"
gate release "$workspace/ios.json"
id=fixture/android/1.0.12/108/internal
gate test-link "$id" --url https://play.google.com/apps/testing/org.example.fixture
gate record-test "$id" distribution --outcome pass --source store --note '対象トラックが有効'
gate record-test "$id" participation --outcome pass --source tester --device 'Android QA' --note '参加した'
gate record-test "$id" availability --outcome blocked --source tester --device 'Android QA' --note 'アイテムが見つからない'
gate record-test "$id" installation --outcome pass --source tester --device 'Android QA' --version 1.0.12 \
    --build 108 --build-source store --note 'Playから取得'

bash "$here/run-ui-check.sh" "$bundle" "$workspace" python3 -c '
from pathlib import Path
import sys, time

result = Path(sys.argv[1]) / "ui-result.txt"
pid = int(sys.argv[2])
for _ in range(145):
    if result.exists():
        message = result.read_text()
        if message != "pass":
            sys.exit(message)
        print("GUI check passed. Invitation, download retry, QA history, and approval packet.")
        break
    time.sleep(0.3)
else:
    sys.exit(f"StoreGate GUI test PID {pid} did not finish.")
' "$workspace"
