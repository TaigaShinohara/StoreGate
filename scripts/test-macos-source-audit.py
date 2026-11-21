#!/usr/bin/env python3
"""Regression cases for macOS target selection and source evidence."""
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("mac_scan", ROOT / "scripts/macos-source-audit.py")
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class MacSourceChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "App.xcodeproj").mkdir()
        (self.root / "App.xcodeproj/project.pbxproj").write_text("SDKROOT = macosx;\n")
        self.source('let policy = Link("プライバシーポリシー", destination: URL(string: "https://host.invalid/policy")!)\n')

    def source(self, text):
        (self.root / "App.swift").write_text(text)

    def rules(self):
        return {row["rule"] for row in scanner.audit(self.root)["findings"]}

    def test_mac_is_not_ios_and_file_host_is_not_http(self):
        self.source('let policy = "https://host.invalid/privacy"\nlet ok = candidate.isFileURL && candidate.host == "localhost"\n')
        result = scanner.audit(self.root)
        self.assertTrue(result["complete"])
        self.assertEqual(result["platform"], "macos")
        self.assertEqual(result["findings"], [])

    def test_release_endpoint_but_not_comments_or_debug(self):
        self.source('''let policy = "PrivacyPolicy"
// https://localhost/comment
#if DEBUG
let url = "https://localhost/debug"
#else
let url = "https://api.host.invalid/live"
#endif
''')
        self.assertNotIn("APPLE-2.1-STAGING-BACKEND", self.rules())
        with (self.root / "App.swift").open("a") as stream:
            stream.write('let live = "http://127.0.0.1:9000/api"\n')
        self.assertIn("APPLE-2.1-STAGING-BACKEND", self.rules())

    def test_ignored_artifacts_and_fingerprint(self):
        before = scanner.audit(self.root)["fingerprint"]
        for directory in (".local/releases", ".build", "build", "Tests"):
            p = self.root / directory
            p.mkdir(parents=True)
            (p / "Old.swift").write_text('let url = "https://localhost/old"')
        self.assertEqual(scanner.audit(self.root)["fingerprint"], before)
        self.assertNotIn("APPLE-2.1-STAGING-BACKEND", self.rules())
        self.source('let policy = "privacy-policy"\nlet changed = 1')
        self.assertNotEqual(scanner.audit(self.root)["fingerprint"], before)

    def test_mixed_and_unknown_are_incomplete(self):
        (self.root / "App.xcodeproj/project.pbxproj").write_text("SDKROOT = macosx;\nSDKROOT = iphoneos;\n")
        self.assertFalse(scanner.audit(self.root)["complete"])
        (self.root / "App.xcodeproj/project.pbxproj").write_text("// unresolved build settings")
        self.assertFalse(scanner.audit(self.root)["complete"])

    def test_purpose_and_manifest_errors(self):
        (self.root / "Info.plist").write_bytes(plistlib.dumps({"NSCameraUsageDescription": ""}))
        self.assertIn("APPLE-5.1.1-VAGUE-PURPOSE-STRING", self.rules())
        (self.root / "PrivacyInfo.xcprivacy").write_text("broken plist")
        self.assertFalse(scanner.audit(self.root)["complete"])
        self.assertIn("APPLE-MANIFEST-UNREADABLE", self.rules())

    def test_other_targets_do_not_take_mac_only_route(self):
        (self.root / "Package.swift").write_text('let platforms = [.macOS(.v14), .iOS(.v17)]')
        self.assertEqual(scanner.audit(self.root)["platform"], "mixed")
        (self.root / "Package.swift").unlink()
        (self.root / "AndroidManifest.xml").write_text('<manifest package="test.app"/>')
        self.assertEqual(scanner.audit(self.root)["platform"], "mixed")
        result = subprocess.run(["bash", str(ROOT / "agent-os/hooks/storegate-guard.sh"), str(self.root)],
                                capture_output=True, text=True, timeout=30)
        self.assertIn("Coverage. incomplete", result.stdout)
        self.assertIn("android=1", result.stdout)
        self.assertIn("mixed macOS", result.stdout)

    def test_guidance_text_is_not_a_policy_link(self):
        self.source('let label = "プライバシーポリシーを確認してください"')
        self.assertIn("APPLE-5.1.1-MISSING-PRIVACY-POLICY", self.rules())
        (self.root / "ja.strings").write_text('"policy" = "プライバシーポリシー https://host.invalid/privacy";', encoding="utf-16")
        self.assertNotIn("APPLE-5.1.1-MISSING-PRIVACY-POLICY", self.rules())

    def test_limits_fail_closed(self):
        (self.root / "Huge.swift").write_bytes(b"x" * (scanner.MAX_FILE + 1))
        self.assertFalse(scanner.audit(self.root)["complete"])

    def test_guard_mac_routing(self):
        result = subprocess.run(["bash", str(ROOT / "agent-os/hooks/storegate-guard.sh"), str(self.root)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Platforms. macOS=1 iOS=0", result.stdout)
        self.assertIn("macos=1", result.stdout)
        self.assertNotIn("MISSING-PRIVACY-POLICY", result.stdout)

    def test_guidance_catalog_covers_apple_metadata(self):
        catalog = json.loads((ROOT / "desktop/Resources/Audit.xcstrings").read_text())
        metadata = (ROOT / "scripts/metadata-audit.py").read_text()
        import re
        rules = set(re.findall(r'"((?:APPLE|BOTH|CHINA)-[A-Z0-9.-]+)"', metadata))
        for rule in rules:
            for part in ("message", "fix"):
                entry = catalog["strings"][f"finding.{rule}.{part}"]
                self.assertTrue(entry["localizations"]["ja"]["stringUnit"]["value"])


if __name__ == "__main__":
    unittest.main()
