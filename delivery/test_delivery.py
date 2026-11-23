import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
import zlib

from assets import inspect_images
from builds import lock
from storegate import database, dispatch_store, execute, migrate, save_config
from stores import Apple, Google, StoreError


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b""))


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

    def test_manifest_tracks_missing_frame_and_substitution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "02_images/screenshots/google-play/phone/frame-01.png"
            path.parent.mkdir(parents=True)
            data = png()
            path.write_bytes(data)
            manifest = root / "07_submission/image-manifest.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({"images": [{"file": str(path.relative_to(root)), "width": 1,
                                                        "height": 1, "bytes": len(data),
                                                        "sha256": hashlib.sha256(data).hexdigest()}]}))
            images, issues = inspect_images(root, "android", ["ja"], "ja", {"phone": 1, "feature": 1})
            self.assertEqual([item["slot"] for item in images], ["phone"])
            self.assertEqual(len(issues), 1)
            self.assertIn("feature", issues[0])
            path.write_bytes(data[:-1] + b"\x00")
            self.assertFalse(inspect_images(root, "android", ["ja"], "ja", {"phone": 1})[0])

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
