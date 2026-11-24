import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from readback import android_app, device_readback
from storegate import ROOT, database, execute, save_config
from stores import Apple


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.db_context = database(self.folder)
        self.db = self.db_context.__enter__()
        self.addCleanup(self.db_context.__exit__, None, None, None)
        self.profile = {"id": "fixture", "name": "Fixture", "repo": str(ROOT), "targets": {
            platform: {"platform": platform, "bundle": "org.example.fixture", "locales": ["ja"], "build_method": "ci"}
            for platform in ("ios", "android")}}
        save_config(self.db, "profiles", self.profile)

    def release(self, platform="ios", track="production", **fields):
        identifier = f"fixture/{platform}/1.0.12/108/{track}"
        version = {"id": identifier, "app": "fixture", "platform": platform, "version": "1.0.12",
                   "build": "108", "track": track, "regions": ["JP"], **fields}
        save_config(self.db, "releases", version)
        return version

    def call(self, command, release, **kwargs):
        return execute(self.folder, self.db, argparse.Namespace(command=command, id=release["id"], **kwargs))

    def resume(self, release, **kwargs):
        data = {"phase": None, "prepare": False, "store": False, "device": None, "device_id": None, **kwargs}
        return self.call("resume", release, **data)["resume"]

    def observe(self, release, stage, **kwargs):
        return self.call("record-test", release, **{
            "stage": stage, "device": "Phone", "note": "fixture evidence", "outcome": "pass", "source": "tester",
            "operation": None, "os": None, "version": None, "build": None, "build_source": None, **kwargs})

    def test_test_waiting_still_exposes_independent_audits_and_device_scope(self):
        release = self.release("android", "internal", test_plan={"Phone": ["Open PDF", "Reopen after restart"]})
        self.observe(release, "distribution", device=None, source="store")
        self.observe(release, "participation")
        self.observe(release, "availability", note="Item not found", outcome="blocked")
        plan = self.resume(release)
        self.assertEqual((plan["steps"][0]["code"], plan["steps"][0]["owner"]), ("availability", "tester"))
        self.assertIn("--prepare", plan["runnable"][0]["command"])
        self.observe(release, "availability")
        self.observe(release, "installation", version="1.0.12", build="108", build_source="store")
        self.observe(release, "qa", operation="Open PDF")
        self.assertIn("Reopen after restart", self.resume(release)["steps"][0]["reason"])
        self.observe(release, "qa", operation="Reopen after restart")
        self.assertEqual(self.resume(release)["state"], "complete")
        result = {"source": "adb", "outcome": "mismatch", "version": "1.0.11", "build": "107",
                  "message": "Wrong version"}
        with patch("storegate.device_readback", return_value=result) as inspect:
            resumed = self.resume(release, device="Phone", device_id="fixture-serial")
            inspect.assert_called_once_with(self.profile["targets"]["android"], release, "fixture-serial")
        self.assertEqual(resumed["steps"][0]["code"], "device_mismatch")
        self.assertNotIn("fixture-serial", json.dumps(resumed))
        self.assertIn("observed_at", resumed["readbacks"]["devices"]["Phone"])
        failure = {"source": "adb", "outcome": "unconfirmed", "message": "Disconnected"}
        with patch("storegate.device_readback", return_value=failure):
            retry = self.resume(release, device="Phone", device_id="fixture-serial")
        self.assertEqual(retry["steps"][0]["code"], "device_mismatch")
        self.assertEqual(retry["readbacks"]["history"][0]["outcome"], "unconfirmed")
        self.assertEqual(retry["readbacks"]["devices"]["Phone"]["outcome"], "mismatch")
        self.assertTrue(self.call("test-status", release)["testing"]["stages"]["qa"])

    def test_resume_continues_after_failed_store_and_local_checks(self):
        release = self.release(metadata=str(self.folder))
        with patch("storegate.store_readback", return_value={"source": "app_store_connect_api", "outcome": "unconfirmed",
                                                             "message": "denied"}), \
             patch("storegate.device_readback", return_value={"source": "devicectl", "outcome": "matches"}), \
             patch("storegate.audit_source", side_effect=subprocess.TimeoutExpired("fixture", 10)) as source, \
             patch("storegate.audit_metadata") as metadata, patch("storegate.audit") as project:
            resumed = self.resume(release, store=True, prepare=True, device="Phone", device_id="fixture-a")
        self.assertEqual([(item["check"], item["outcome"]) for item in resumed["progress"]],
                         [("store", "unconfirmed"), ("device", "matches"), ("source", "unconfirmed"), ("metadata", "checked")])
        source.assert_called_once()
        metadata.assert_called_once()
        project.assert_not_called()
        self.assertFalse(resumed["runnable"])
        self.assertFalse(self.db.execute("SELECT 1 FROM operations").fetchall())

    def test_resume_reads_store_when_source_is_unavailable(self):
        self.profile["repo"] = str(self.folder / "missing-project")
        save_config(self.db, "profiles", self.profile)
        release = self.release("android", "internal")
        receipt = {"source": "play_api", "outcome": "observed", "state": "RELEASE_LIFECYCLE_STATE_PUBLISHED"}
        with patch("storegate.store_readback", return_value=receipt):
            result = self.resume(release, store=True, prepare=True)
        self.assertTrue(result["local_issue"])
        self.assertEqual(result["steps"][0]["code"], "participation")
        listed = execute(self.folder, self.db, argparse.Namespace(command="list"))["releases"][0]
        self.assertEqual(listed["workflow"]["phases"]["test"][0]["code"], "participation")

    def test_production_can_prepare_while_store_review_waits(self):
        release = self.release(metadata=str(self.folder))
        review = {"source": "app_store_connect_api", "outcome": "observed", "state": "IN_REVIEW", "build": "108"}
        with patch("storegate.store_readback", return_value=review):
            plan = self.resume(release, phase="production", store=True)
        self.assertEqual(plan["steps"][0]["code"], "review")
        self.assertEqual(len(plan["runnable"]), 1)
        self.assertIn("--prepare", plan["runnable"][0]["command"])

    def test_failed_audit_can_be_retried_explicitly_without_automatic_retry_loop(self):
        release = self.release("android", "internal")
        with patch("storegate.audit_source", side_effect=subprocess.TimeoutExpired("fixture", 10)):
            first = self.resume(release, prepare=True)
        self.assertEqual(next(check["state"] for check in first["checks"] if check["name"] == "source"), "not_checked")
        plan = self.resume(release)
        self.assertEqual(plan["workflow"]["pending_audits"], ["source"])
        self.assertEqual(plan["runnable"], [])
        with patch("storegate.audit_source") as restored:
            self.assertEqual(self.resume(release, prepare=True)["progress"], [{"check": "source", "outcome": "checked"}])
            restored.assert_called_once()

    def test_test_complete_requires_identity_even_after_qa_passes(self):
        release = self.release("android", "internal", test_plan={"Phone": ["Open PDF"]})
        self.observe(release, "distribution", device=None, source="store")
        for stage in ("participation", "availability", "installation"):
            self.observe(release, stage)
        self.observe(release, "qa", operation="Open PDF")
        self.assertEqual(self.resume(release)["steps"][0]["code"], "device_identity")
        result = {"source": "adb", "outcome": "matches", "version": "1.0.12", "build": "108",
                  "message": "Verified installed build"}
        with patch("storegate.device_readback", return_value=result):
            self.assertEqual(self.resume(release, device="Phone", device_id="fixture")["state"], "complete")

    def test_approval_packet_compares_content_without_granting_approval(self):
        metadata = self.folder / "metadata"
        current = metadata / "ja"
        current.mkdir(parents=True)
        (current / "listing.json").write_text(json.dumps({"description": "First", "review_notes": "private fixture"}))
        outside = self.folder / "outside.txt"
        outside.write_text("outside fixture")
        (current / "name.txt").symlink_to(outside)
        release = self.release(metadata=str(metadata), price="free", publication="manual")
        with patch("storegate.store_readback") as store:
            original = self.call("approval-packet", release, action="submit", baseline=True)["approval_packet"]
            self.assertTrue(original["baseline_at"])
            self.assertNotIn("private fixture", json.dumps(original))
            self.assertNotIn("outside fixture", json.dumps(original))
            (current / "listing.json").write_text(json.dumps({"description": "Updated"}))
            revised = self.call("approval-packet", release, action="submit", baseline=False)["approval_packet"]
            store.assert_not_called()
        self.assertEqual(original["baseline_at"], revised["baseline_at"])
        change = next(item for item in revised["changes"] if item["id"] == "listing")
        self.assertIn("ja · 説明", change["labels"])
        self.assertTrue(self.call("approval-packet", release, action="publish", baseline=False)["approval_packet"]["baseline_at"] is None)
        self.assertFalse(self.db.execute("SELECT 1 FROM operations").fetchall())

    def test_play_packet_includes_same_build_test_evidence_without_passing_production_qa(self):
        internal = self.release("android", "internal")
        production = self.release("android", "production")
        self.observe(internal, "installation", version="1.0.12", build="108", build_source="store")
        self.observe(internal, "qa", operation="Open PDF")
        packet = self.call("approval-packet", production, action="submit", baseline=False)["approval_packet"]
        evidence = next(section["rows"] for section in packet["sections"] if section["id"] == "evidence")
        self.assertTrue(any("internal" in row["label"] and "Open PDF" in row["label"] for row in evidence))
        self.assertTrue(any(row["label"] == "提出版の実機確認" and row["value"].startswith("未確認") for row in evidence))

    def test_packet_reads_platform_specific_description_file(self):
        metadata = self.folder / "metadata"
        locale = metadata / "ja"
        locale.mkdir(parents=True)
        (locale / "description.txt").write_text("Apple description")
        (locale / "full_description.txt").write_text("Play description")
        for platform, text in (("ios", "Apple description"), ("android", "Play description")):
            with self.subTest(platform=platform):
                release = self.release(platform, metadata=str(metadata))
                packet = self.call("approval-packet", release, action="submit", baseline=False)["approval_packet"]
                listing = next(item["rows"] for item in packet["sections"] if item["id"] == "listing")
                self.assertEqual(next(row["value"] for row in listing if row["label"] == "ja · 説明"), text)

    def test_read_only_beta_and_device_probes_scope_exact_build(self):
        release = self.release()
        apple = Apple.__new__(Apple)
        apple.app = "42"
        apple.target = self.profile["targets"]["ios"]
        apple.client = Mock()
        apple.client.request.side_effect = [
            {"data": {"id": "42", "attributes": {"bundleId": "org.example.fixture"}}},
            {"data": {"attributes": {"internalBuildState": "MISSING_EXPORT_COMPLIANCE"}}},
        ]
        with patch.object(apple, "available_build", return_value={"id": "build-108",
                          "attributes": {"processingState": "VALID"}}) as available:
            self.assertEqual(apple.test_status(release)["state"], "MISSING_EXPORT_COMPLIANCE")
            available.assert_called_once_with(release, "IOS")
        self.assertTrue(all(len(call.args) == 1 for call in apple.client.request.call_args_list))
        apps = {"result": {"apps": [{"bundleIdentifier": "org.example.other", "version": "1.0.12", "bundleVersion": "108"},
                                    {"bundleIdentifier": "org.example.fixture", "version": "1.0.12", "bundleVersion": "108"}]}}
        with patch("readback.run", return_value=Mock(returncode=0, stdout=json.dumps(apps).encode())):
            self.assertEqual(device_readback(self.profile["targets"]["ios"], release, "device")["outcome"], "matches")
        adb = b"Packages:\n  Package [org.example.fixture] (hash):\n    versionCode=108 minSdk=23\n    versionName=1.0.12\n"
        self.assertEqual(android_app(adb, "org.example.fixture"), ("1.0.12", "108"))
        with self.assertRaises(ValueError):
            android_app(adb, "org.example.other")
