import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import zlib

from assets import inspect_images
from builds import lock
from storegate import ROOT, database, dispatch_store, execute, guard_result, import_qa, migrate, save_config
from stores import Apple, Google, StoreError


def png(width=320, height=480, color=2):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    channels = 4 if color == 6 else 3
    rows = (b"\0" + b"\xff" * width * channels) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class MockStore:
    def __init__(self):
        self.remote = {"app": "12", "build": "1", "state": "draft"}
        self.calls = 0
        self.error = None

    def status(self, release):
        return dict(self.remote)

    def describe(self, action, release, remote):
        return {"effect": action}

    def apply(self, action, remote, operation):
        self.calls += 1
        if self.error:
            raise self.error
        self.remote["state"] = "in_review"

    def reconciled(self, action, before, remote, release):
        return remote["state"] == "in_review" and remote["build"] == release["build"]


class DeliveryTests(unittest.TestCase):
    @contextmanager
    def workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            with database(path) as db:
                yield path, db

    def test_migration_preserves_source_and_history(self):
        with self.workspace() as (folder, db):
            path = folder / "delivery-workspace.json"
            raw = json.dumps({"projects": [{"id": "A1", "name": "Mac project", "records": [{"note": "old build"}]}]})
            path.write_text(raw)
            self.assertEqual(migrate(db, path), 1)
            self.assertEqual(migrate(db, path), 0)
            item = execute(folder, db, argparse.Namespace(command="legacy", legacy_id="a1"))
            self.assertEqual(item["records"][0]["note"], "old build")
            self.assertEqual(path.read_text(), raw)

    def test_concurrent_process_cannot_enter_lock(self):
        with self.workspace() as (folder, _):
            path = folder / "app.lock"
            with lock(path):
                result = subprocess.run(
                    ["python3", "-c", f"from builds import lock; lock({str(path)!r}).__enter__()"],
                    cwd=Path(__file__).parent, capture_output=True, text=True)
                self.assertIn("同じ操作が実行中です", result.stderr)
            with lock(path):
                pass

    def test_play_assets_require_icon_feature_and_ordered_screenshots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "07_submission/image-manifest.json"
            manifest.parent.mkdir()
            entries = []

            def add(name, width, height, color):
                path = root / "02_images" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                data = png(width, height, color)
                path.write_bytes(data)
                entries.append({"file": str(path.relative_to(root)), "width": width, "height": height,
                                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
                manifest.write_text(json.dumps({"images": entries}))
                return path

            shot = add("screenshots/google-play/phone/frame-01.png", 320, 480, 2)
            images, issues = inspect_images(root, "android", ["ja"], "ja", {})
            self.assertEqual([item["slot"] for item in images], ["phone"])
            self.assertEqual(len(issues), 3)
            self.assertTrue(any("icon" in issue for issue in issues))
            self.assertTrue(any("feature" in issue for issue in issues))
            add("screenshots/google-play/phone/frame-02.png", 320, 480, 2)
            add("promo/google-play/feature.png", 1024, 500, 2)
            add("icon/google-play/icon.png", 512, 512, 2)
            self.assertTrue(inspect_images(root, "android", ["ja"], "ja", {})[1])
            entries.pop()
            add("icon/google-play/icon.png", 512, 512, 6)
            images, issues = inspect_images(root, "android", ["ja"], "ja", {})
            self.assertEqual(issues, [])
            self.assertEqual([item["slot"] for item in images], ["feature", "icon", "phone", "phone"])
            shot.write_bytes(shot.read_bytes()[:-1] + b"\x00")
            self.assertTrue(inspect_images(root, "android", ["ja"], "ja", {})[1])
            self.assertEqual(inspect_images(root, "ios", ["ja"], "ja", {}), ([], []))

    def test_play_production_draft_previews_audited_inputs_without_store_access(self):
        with self.workspace() as (folder, db):
            locale = folder / "ja-JP"
            locale.mkdir()
            for file, value in {"name": "Fixture", "short_description": "Short", "full_description": "Long"}.items():
                (locale / (file + ".txt")).write_text(value)
            profile = {"id": "fixture", "targets": {"android": {"bundle": "org.example.app", "locales": ["ja-JP"],
                                                             "play_staging": True}}}
            release = {"id": "release", "app": "fixture", "platform": "android", "track": "production",
                       "metadata": str(folder), "version": "1.0.0", "build": "1"}
            save_config(db, "profiles", profile)
            save_config(db, "releases", release)
            checks = [{"name": "account", "state": "pending"}, {"name": "screenshots", "state": "pass"},
                      {"name": "metadata", "state": "pass"}]
            state = {"checks": checks, "images": [], "console": "https://play.google.com/console"}
            with patch("storegate.snapshot", return_value=(state, {})), patch("stores.Google") as store:
                result = execute(folder, db, argparse.Namespace(command="draft", id="release"))
                store.assert_not_called()
            self.assertEqual(result["state"], "console_required")
            self.assertEqual(result["listing"]["ja-JP"]["title"], "Fixture")

    def test_approval_changes_and_unknown_reconcile_without_resending(self):
        with self.workspace() as (_, db):
            release = {"id": "fixture", "build": "1", "regions": ["JP"]}
            save_config(db, "releases", release)
            state = {key: "fixture" for key in ("app", "platform", "bundle", "store_id", "version", "build", "track", "console")}
            state.update(checks=[], images=[])
            adapter = MockStore()
            plan = dispatch_store(db, release, "submit", adapter, state, {"source": "v1"})
            self.assertEqual(adapter.calls, 0)
            with self.assertRaisesRegex(StoreError, "内容が変わりました"):
                dispatch_store(db, release, "submit", adapter, state, {"source": "v2"}, plan["confirmation"])
            self.assertEqual(adapter.calls, 0)
            adapter.error = StoreError("timeout", uncertain=True)
            with self.assertRaisesRegex(StoreError, "timeout"):
                dispatch_store(db, release, "submit", adapter, state, {"source": "v1"}, plan["confirmation"])
            with self.assertRaisesRegex(StoreError, "送信結果を確定できません"):
                dispatch_store(db, release, "submit", adapter, state, {"source": "v1"}, plan["confirmation"])
            self.assertEqual(adapter.calls, 1)
            adapter.remote["state"] = "in_review"
            dispatch_store(db, release, "store-status", adapter, state)
            self.assertEqual(db.execute("SELECT state FROM operations").fetchone()["state"], "received")
            self.assertEqual(adapter.calls, 1)

    def test_explicit_flutter_android_scope_skips_native_apple_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "pubspec.yaml").write_text("name: fixture")
            (root / "android/app/src/main").mkdir(parents=True)
            (root / "android/app/src/main/AndroidManifest.xml").write_text("<manifest><application/></manifest>")
            (root / "patches/fixture/macos").mkdir(parents=True)
            (root / "patches/fixture/macos/Fixture.swift").write_text('let optOut = "call us to cancel"')
            (root / "lib").mkdir()
            (root / "lib/config.dart").write_text('const policy = "https://fixture.invalid/privacy";')
            guard = ROOT / "agent-os/hooks/storegate-guard.sh"
            output = subprocess.run(["bash", str(guard), str(root), "--platform", "android"],
                                    capture_output=True, text=True, timeout=60)
            _, findings = guard_result(output.stdout, output.returncode, "android")
            self.assertIn("ios=0 android=1", output.stdout)
            self.assertFalse(any(finding["id"] == "BOTH-SUBSCRIPTION-HARD-CANCEL" for finding in findings))

    def test_real_high_findings_block_but_regulatory_deadlines_do_not(self):
        coverage = "Coverage. complete ios=1 android=0 web=0"
        deadline = "[HIGH] in 20 days. A deadline notice."
        clean = "\n".join((deadline, "Summary. critical=0 high=0 medium=0", coverage))
        self.assertEqual(guard_result(clean, 0, "ios"), ("pass", []))
        self.assertEqual(guard_result(clean, 0, "android"), ("not_checked", []))
        report = "\n".join(("  [HIGH]     APPLE-EXAMPLE  App finding", deadline,
                            "Summary. critical=0 high=1 medium=0", coverage))
        state, findings = guard_result(report, 0, "ios")
        self.assertEqual(state, "blocked")
        self.assertEqual([item["id"] for item in findings], ["APPLE-EXAMPLE"])

    def test_ci_report_must_be_for_the_signed_artifact(self):
        with self.workspace() as (folder, db):
            release = {"id": "fixture"}
            save_config(db, "releases", release)
            receipt = folder / "qa.json"
            receipt.write_text(json.dumps({"artifact_sha256": "wrong", "suite": "smoke", "run": "CI 123", "outcome": "pass"}))
            state = {"checks": [{"name": "artifact", "state": "pass", "sha256": "expected"}]}
            with patch("storegate.snapshot", return_value=(state, {"qa": "qa-context"})):
                with self.assertRaisesRegex(ValueError, "一致しません"):
                    import_qa(db, {}, release, receipt)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)
                receipt.write_text(receipt.read_text().replace("wrong", "expected"))
                import_qa(db, {}, release, receipt)
                result = db.execute("SELECT state,digest,data FROM evidence").fetchone()
                self.assertEqual((result["state"], result["digest"]), ("pass", "qa-context"))
                self.assertEqual(json.loads(result["data"])["kind"], "ci")

    def test_apple_refuses_draft_with_another_version(self):
        client = Mock()
        client.elements.side_effect = [
            [{"id": "draft", "attributes": {"state": "READY_FOR_REVIEW", "platform": "IOS"}}],
            [{"relationships": {"appStoreVersion": {"data": {"id": "wrong", "type": "appStoreVersions"}}}}],
        ]
        apple = Apple.__new__(Apple)
        apple.app, apple.client = "app", client
        with self.assertRaisesRegex(StoreError, "版が一致しません"):
            apple.review_draft({"platform": "IOS", "version_id": "correct"})
        client.request.assert_not_called()

    def test_google_status_is_scoped_to_track_and_build(self):
        google = Google.__new__(Google)
        google.app, google.client = "org.example.app", Mock()
        google.client.request.return_value = {"releases": [
            {"track": "production", "activeArtifacts": [{"versionCode": 108}], "releaseLifecycleState": "AVAILABLE"},
            {"track": "internal", "activeArtifacts": [{"versionCode": 109}], "releaseLifecycleState": "AVAILABLE"},
            {"track": "internal", "activeArtifacts": [{"versionCode": 108}], "releaseLifecycleState": "IN_REVIEW"},
        ]}
        result = google.status({"track": "internal", "build": "108"})
        self.assertEqual(result["state"], "IN_REVIEW")
        self.assertEqual(result["active_builds"], ["108"])

    def test_permission_rejection_does_not_claim_receipt(self):
        with self.workspace() as (_, db):
            release = {"id": "fixture", "build": "1"}
            state = {key: "fixture" for key in ("app", "platform", "bundle", "store_id", "version", "build", "track", "console")}
            state.update(checks=[], images=[])
            save_config(db, "releases", release)
            adapter = MockStore()
            adapter.error = StoreError("forbidden", status=403)
            plan = dispatch_store(db, release, "submit", adapter, state, {})
            with self.assertRaisesRegex(StoreError, "forbidden"):
                dispatch_store(db, release, "submit", adapter, state, {}, plan["confirmation"])
            self.assertEqual(db.execute("SELECT state FROM operations").fetchone()["state"], "rejected")


if __name__ == "__main__":
    unittest.main()
