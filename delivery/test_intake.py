import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from builds import BUILD_FREE_BYTES
from preflight import device_inventory
from storegate import database, execute, fetch, snapshot
from stores import StoreError


class IntakeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.folder = root / "workspace"
        self.repo = root / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.org", "commit", "-qm", "fixture", "--allow-empty"], check=True)
        self.profile_file = root / "profile.json"
        self.release_file = root / "release.json"
        self.settings = {"app": "fixture", "platform": "ios", "version": "1.0.12", "build": "108",
                         "regions": ["JP"], "price": "free", "publication": "manual", "purchases": False, "first": True}
        self.profile_file.write_text(json.dumps({"id": "fixture", "name": "Synthetic Fixture", "repo": str(self.repo),
            "targets": {
                "ios": {"bundle": "org.example.fixture", "store_id": "123", "provider": "fixture",
                        "build_method": "xcode", "export_path": "exports", "preflight": ["fixture-check"]},
                "android": {"bundle": "org.example.fixture", "store_id": "org.example.fixture", "provider": "fixture",
                            "build_method": "ci"},
            }}))
        self.call("register", file=self.profile_file)
        inventory = patch("storegate.device_inventory", return_value=None)
        inventory.start()
        self.addCleanup(inventory.stop)
        self.register()

    def call(self, command, **kwargs):
        defaults = {"id": getattr(self, "release_id", None)}
        with database(self.folder) as db:
            return execute(self.folder, db, argparse.Namespace(command=command, **(defaults | kwargs)))

    def register(self):
        self.release_file.write_text(json.dumps(self.settings))
        registration = self.call("release", file=self.release_file)
        self.release_id = registration["id"]
        return registration["intake"]

    def intake(self):
        return self.call("intake", answers_json=None, revision=None)["intake"]

    def save(self, answers, **kwargs):
        with patch("storegate.audit_source"), patch("storegate.audit_build_preflight"), \
             patch("storegate.store_readback") as store:
            result = self.call("intake", answers_json=json.dumps(answers), **({"revision": None} | kwargs))
            store.assert_not_called()
        return result

    def test_prefill_save_and_restart_keep_scope_and_actual_release_conditions(self):
        start = self.intake()
        self.assertIsNone(start["saved_at"])
        self.assertEqual(self.fields(start)["price"]["state"], "prefilled")
        self.assertEqual(self.fields(start)["age"]["state"], "missing")
        result = self.save({"regions": "JP, US", "price": "pending", "ipad": "none",
                            "tester_group": "self", "delivery_method": "email", "test_plan": "iPhone QA: Open PDF"})
        fields = self.fields(self.intake())
        self.assertEqual(fields["price"]["state"], "undecided")
        self.assertEqual(fields["ipad"]["value"], "none")
        self.assertEqual({item["check"] for item in result["resume"]["progress"]}, {"build_preflight", "source"})
        with database(self.folder) as db:
            release = fetch(db, "releases", self.release_id)
            self.assertEqual(release["regions"], ["JP", "US"])
            self.assertNotIn("price", release)
            self.assertEqual(release["test_plan"], {"iPhone QA": ["Open PDF"]})
            self.assertEqual(db.execute("SELECT count(*) FROM operations").fetchone()[0], 0)
        packet = self.call("approval-packet", action="submit", baseline=False)["approval_packet"]
        self.assertIn("開始時の回答", [section["title"] for section in packet["sections"]])
        with self.assertRaisesRegex(ValueError, "更新されました"):
            self.save({"price": "free"}, revision=start["revision"])

    def test_import_keeps_answers_marks_edited_settings_and_never_reuses_other_scope(self):
        self.save({"ads": "no", "price": "JPY 400"})
        self.settings["price"] = "JPY 500"
        fields = self.fields(self.register())
        self.assertEqual((fields["price"]["state"], fields["price"]["value"]), ("changed", "JPY 500"))
        self.assertEqual(fields["ads"]["value"], "no")
        profile = json.loads(self.profile_file.read_text())
        profile["targets"]["ios"]["store_id"] = "456"
        self.profile_file.write_text(json.dumps(profile))
        self.call("register", file=self.profile_file)
        self.assertIsNone(self.intake()["saved_at"])
        self.assertEqual(self.fields(self.intake())["ads"]["state"], "missing")

    def test_form_refuses_contact_values_and_accepts_not_applicable(self):
        with self.assertRaisesRegex(ValueError, "連絡先"):
            self.save({"test_plan": "sensitive@example.org: Open PDF"})
        self.assertIsNone(self.intake()["saved_at"])
        self.assertEqual(self.fields(self.save({"app_access": "na", "test_plan": "na"})["intake"])["app_access"]["state"], "saved")

    def test_capacity_and_missing_auth_only_flag_affected_work(self):
        self.save({"ipad": "none"})
        def free(path):
            path = Path(path)
            return path, 30 * 1024**2 if path.resolve() in (self.folder.resolve(), (self.repo / "exports").resolve()) else BUILD_FREE_BYTES * 2
        with patch("preflight.disk_for", side_effect=free), patch("preflight.Apple") as network:
            response = self.call("preflight", store=False)["preflight"]
            result = self.call("resume", phase="production", prepare=False, store=False, device=None, device_id=None)["resume"]
            network.assert_not_called()
        checks = {item["name"]: item for item in response["checks"]}
        self.assertEqual(checks["authentication"]["outcome"], "reference_missing")
        self.assertEqual(checks["ipad"]["outcome"], "none")
        self.assertEqual({row["scope"] for row in checks.values() if row["name"].startswith("capacity") and row["state"] == "blocked"},
                         {"workspace_write", "build"})
        self.assertIn("--prepare", result["runnable"][0]["command"])
        self.assertTrue(all(row["scope"]["build"] == "108" for row in result["steps"]))

    def test_read_only_auth_keeps_last_verified_evidence_after_expiry(self):
        with patch("preflight.Apple") as adapter:
            adapter.return_value.status.return_value = {"state": "PREPARE_FOR_SUBMISSION"}
            self.call("preflight", store=True)
            adapter.return_value.status.side_effect = StoreError("fixture", status=401)
            result = self.call("preflight", store=True)
            self.assertEqual(adapter.return_value.status.call_count, 2)
        rows = {item["name"]: item for item in result["preflight"]["checks"]}
        self.assertEqual(rows["store_probe"]["outcome"], "sign_in_required")
        self.assertEqual(rows["store_probe_verified"]["state"], "pass")
        self.assertTrue(rows["store_probe_verified"]["observed_at"])

    def test_device_inventory_keeps_connection_and_readability_distinct(self):
        target = {"platform": "android"}
        for sample, outcome in [((0, 1, 0), "unreadable"), ((0, 0, 1), "disconnected"), ((1, 1, 0), "connected")]:
            with self.subTest(sample=sample), patch("adb_inventory.connected_devices", return_value=sample):
                self.assertEqual(device_inventory(target)["outcome"], outcome)
        with patch("adb_inventory.connected_devices", side_effect=OSError):
            self.assertEqual(device_inventory(target)["outcome"], "unconfirmed")

    def test_ordinary_apple_draft_and_play_activation_have_distinct_boundaries(self):
        self.save({"age": "pending", "account_setup": "agreement"})
        with patch("storegate.require_ready") as requirements, patch("stores.Apple") as adapter:
            adapter.return_value.sync_draft.return_value = {"state": "draft_verified"}
            self.assertEqual(self.call("draft")["state"], "draft_verified")
            self.assertIn("intake_production", requirements.call_args.args[1])
            adapter.return_value.apply.assert_not_called()
        with database(self.folder) as db:
            release = fetch(db, "releases", self.release_id)
            profile = fetch(db, "profiles", release["app"])
            state = snapshot(db, profile, release)[0]
        self.assertIn("intake_production", [row["name"] for row in state["checks"]])
        tasks = state["workflow"]["phases"]["production"]
        self.assertEqual(next(row["category"] for row in tasks if row["code"] == "registration"), "owner_action")

        self.settings.update(platform="android", track="alpha")
        self.register()
        with patch("storegate.store_readback", return_value={"source": "play_api", "outcome": "observed",
                   "state": "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW", "versionCode": "108"}), \
             patch("stores.Google") as api:
            plan = self.call("resume", phase="test", prepare=False, store=True, device=None, device_id=None)["resume"]
            api.assert_not_called()
        self.assertEqual(plan["steps"][0]["category"], "owner_action")
        self.assertEqual(plan["steps"][0]["code"], "test_approval")
        self.assertNotIn("command", plan["steps"][0])

    @staticmethod
    def fields(intake):
        return {item["key"]: item for item in intake["fields"]}
