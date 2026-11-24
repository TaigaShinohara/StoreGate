import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse
import zlib
import zipfile

from assets import inspect_images
from builds import android_identity, build_host, file_identity, lock, packaged_privacy_manifests
from jdk import JDK, resolve_jdk
from storegate import (ROOT, audit, contexts, database, dispatch_store, execute, guard_result, import_qa, migrate,
                       privacy_corrections, record, save_config, snapshot)
from stores import Apple, Google, StoreError, apple_storefront, review_contact_from_env


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

    def test_build_requires_free_space_on_repository_and_temporary_volumes(self):
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            repo = scratch / "repo"
            repo.mkdir()
            enough = 20 * 1024**3
            for low_space_path, diagnostic in ((scratch, "一時ディレクトリ"), (repo, "ビルド先")):
                with self.subTest(path=low_space_path):
                    def usage(path):
                        return Mock(free=1024 if path == low_space_path else enough)

                    with (patch.dict("builds.os.environ", {"TMPDIR": folder}),
                          patch("builds.shutil.disk_usage", side_effect=usage),
                          patch("builds.run") as run_command):
                        with self.assertRaisesRegex(ValueError, diagnostic), build_host(repo):
                            self.fail("A build must not run with insufficient free space")
                        run_command.assert_not_called()

    def test_concurrent_process_cannot_enter_lock(self):
        with self.workspace() as (folder, _):
            path = folder / "app.lock"
            with lock(path):
                result = subprocess.run(
                    ["python3", "-c", f"from builds import android_identity, lock, packaged_privacy_manifests; lock({str(path)!r}).__enter__()"],
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

    def test_test_rollout_preserves_availability_and_device_qa_history(self):
        with self.workspace() as (folder, db):
            profile = {"id": "fixture", "name": "Fixture", "repo": str(ROOT),
                       "targets": {"android": {"bundle": "org.example.app", "platform": "android", "locales": ["ja"]}}}
            release = {"id": "fixture/android/1.0.12/108/internal", "app": "fixture", "platform": "android",
                       "version": "1.0.12", "build": "108", "track": "internal"}
            save_config(db, "profiles", profile)
            save_config(db, "releases", release)

            def cli(*arguments, success=True):
                result = subprocess.run([sys.executable, str(ROOT / "delivery/storegate.py"), "--workspace",
                                         str(folder), arguments[0], release["id"], *arguments[1:]],
                                        text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0 if success else 1, result.stdout + result.stderr)
                return json.loads(result.stdout)

            def observe_test(stage, outcome, source, note, *options, success=True):
                return cli("record-test", stage, "--outcome", outcome, "--source", source, "--note", note,
                           *options, success=success)

            link = "https://play.google.com/apps/testing/org.example.app"
            self.assertEqual(cli("test-link", "--url", link)["testing"]["invite"], link)
            cli("test-link", "--url", "https://play.google.com/apps/testing/org.example.other", success=False)
            cli("test-link", "--url", "https://play.google.com.evil.test/apps/testing/org.example.app", success=False)
            observe_test("distribution", "pass", "store", "Internal track active")
            observe_test("participation", "pass", "tester", "Opted in", "--device", "Phone")
            blocked = observe_test("availability", "blocked", "tester", "Item not found", "--device", "Phone")
            self.assertEqual(blocked["testing"]["stages"]["distribution"][0]["outcome"], "pass")
            self.assertEqual(blocked["testing"]["stages"]["participation"][0]["outcome"], "pass")
            self.assertEqual(blocked["testing"]["stages"]["availability"][0]["outcome"], "blocked")
            self.assertFalse(blocked["testing"]["stages"]["installation"])
            observe_test("availability", "pass", "tester", "Page opened later", "--device", "Phone")
            observe_test("qa", "pass", "tester", "Opened", "--device", "Phone", "--operation", "Open PDF", success=False)
            observe_test("installation", "pass", "tester", "Installed", "--device", "Phone", "--version", "1.0.11",
                         success=False)
            installation = ("--device", "Phone", "--os", "Android 16", "--version", "1.0.12",
                            "--build", "108", "--build-source", "store")
            observe_test("installation", "pass", "tester", "Installed from Play", *installation)
            observe_test("qa", "pass", "tester", "Opened", "--device", "Phone", "--operation", "Open PDF")
            observe_test("qa", "pass", "tester", "Opened", "--device", "Phone", "--operation", "Open PDF")
            observe_test("qa", "blocked", "tester", "PDF absent", "--device", "Phone", "--operation", "Open after restart")
            status = cli("status")
            progress = status["testing"]
            self.assertEqual(progress["invite"], link)
            self.assertEqual([entry["note"] for entry in progress["history"]], ["Item not found"])
            self.assertEqual({entry["operation"]: entry["outcome"] for entry in progress["stages"]["qa"]},
                             {"Open PDF": "pass", "Open after restart": "blocked"})
            self.assertTrue(all(entry["build_source"] == "store" and entry["version"] == "1.0.12"
                                for entry in progress["stages"]["qa"]))
            checks = {entry["name"]: entry["state"] for entry in status["checks"]}
            self.assertEqual(checks["device"], "pending")
            self.assertNotIn("qa", checks)
            revised = observe_test("qa", "pass", "tester", "Opened after retry", "--device", "Phone",
                                   "--operation", "Open after restart")["testing"]
            self.assertEqual(revised["stages"]["qa"][0]["note"], "Opened after retry")
            self.assertEqual(revised["stages"]["qa"][0]["build_source"], "store")
            self.assertEqual(revised["history"][0]["note"], "PDF absent")
            observe_test("installation", "blocked", "tester", "Store app no longer installed", "--device", "Phone")
            self.assertFalse(cli("status")["testing"]["stages"]["qa"])
            self.assertIsNone(cli("test-link", "--clear")["testing"]["invite"])
            profile["repo"] = str(folder)
            save_config(db, "profiles", profile)
            self.assertEqual(cli("test-status")["testing"]["stages"]["availability"][0]["outcome"], "pass")
            listed = execute(folder, db, argparse.Namespace(command="list"))["releases"][0]
            self.assertTrue(listed["testing"]["stages"]["availability"])

    def test_test_rollout_is_scoped_to_the_store_target_and_test_build(self):
        with self.workspace() as (folder, db):
            ios = {"bundle": "org.example.app", "platform": "ios", "locales": ["ja"], "store_id": "42"}
            android = {"bundle": "org.example.app", "platform": "android", "locales": ["ja"]}
            profile = {"id": "fixture", "name": "Fixture", "repo": str(ROOT), "targets": {"ios": ios, "android": android}}
            release = {"id": "ios-one", "app": "fixture", "platform": "ios", "version": "1.0", "build": "1",
                       "track": "production"}
            next_build = {**release, "id": "ios-two", "build": "2"}
            production = {**release, "id": "android-production", "platform": "android"}
            save_config(db, "profiles", profile)
            for item in (release, next_build, production):
                save_config(db, "releases", item)
            link = "https://testflight.apple.com/join/Fixture1"
            execute(folder, db, argparse.Namespace(command="test-link", id=release["id"], url=link, clear=False))
            self.assertEqual(snapshot(db, profile, release)[0]["testing"]["invite"], link)
            self.assertIsNone(snapshot(db, profile, next_build)[0]["testing"]["invite"])
            self.assertNotIn("testing", snapshot(db, profile, production)[0])
            with self.assertRaisesRegex(ValueError, "テストトラック"):
                execute(folder, db, argparse.Namespace(command="test-link", id=production["id"], url=link, clear=False))
            ios["store_id"] = "43"
            self.assertIsNone(snapshot(db, profile, release)[0]["testing"]["invite"])

    def test_preflight_surfaces_initial_setup_without_using_live_stores(self):
        with self.workspace() as (folder, db):
            target = {"bundle": "org.example.app", "platform": "ios", "locales": ["ja"], "build_method": "xcode",
                      "store_id": "42", "provider": "fixture", "team_id": "TEAM123", "auth_env": "STOREGATE_TEST_JWT"}
            profile = {"id": "fixture", "name": "Fixture", "repo": str(ROOT), "targets": {"ios": target}}
            release = {"id": "fixture/ios/1.0/1/production", "app": "fixture", "platform": "ios",
                       "version": "1.0", "build": "1", "track": "production", "regions": []}
            save_config(db, "profiles", profile)
            save_config(db, "releases", release)
            args = argparse.Namespace(command="preflight", id=release["id"], store=False)
            with patch("preflight.Apple") as apple, patch.dict(os.environ, {"STOREGATE_TEST_JWT": "fixture-only"}):
                status = execute(folder, db, args)
                self.assertNotIn("fixture-only", json.dumps(status))
                rows = {item["name"]: item for item in status["preflight"]["checks"]}
                self.assertEqual(rows["build_inputs"]["state"], "blocked")
                self.assertEqual(rows["declaration_plan"]["state"], "pending")
                self.assertIn("TestFlight", rows["declaration_plan"]["message"])
                apple.assert_not_called()

                evidence = argparse.Namespace(command="attest", id=release["id"], check="declaration_plan",
                                              note="Drafted in advance", outcome="pass")
                self.assertEqual(next(row for row in execute(folder, db, evidence)["checks"]
                                      if row["name"] == "declarations")["state"], "pending")
                apple.return_value.status.return_value = {"state": "registration_required"}
                args.store = True
                status = execute(folder, db, args)
                apple.return_value.status.assert_called_once_with(release)
                rows = {item["name"]: item for item in status["preflight"]["checks"]}
                self.assertEqual(rows["declaration_plan"]["state"], "pass")
                self.assertIn("対象版", rows["store_probe"]["message"])
                apple.return_value.status.side_effect = StoreError("untrusted response", status=403)
                status = execute(folder, db, args)
                self.assertIn("読み取り権限", next(item["message"] for item in status["preflight"]["checks"]
                                               if item["name"] == "store_probe"))
                args.store = False
                self.assertEqual(next(item["state"] for item in execute(folder, db, args)["preflight"]["checks"]
                                      if item["name"] == "store_probe"), "blocked")

    def test_apple_preflight_finds_export_team_and_provisioning_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "Fixture.xcodeproj"
            project.mkdir()
            options = root / "ExportOptions.plist"
            options.write_bytes(plistlib.dumps({"teamID": "ANOTHER", "signingStyle": "manual",
                                                "provisioningProfiles": {"org.example.other": "profile"}}))
            from preflight import build_inputs
            target = {"platform": "ios", "bundle": "org.example.app", "team_id": "TEAM123", "build_method": "xcode",
                      "project": str(project), "scheme": "Fixture", "archive": "Fixture.xcarchive",
                      "export_path": str(root), "export_options": str(options)}
            with patch("preflight.shutil.which", return_value="/usr/bin/xcodebuild"):
                result = build_inputs(target, {"artifact": "Fixture.ipa"}, root)
                self.assertEqual(result["state"], "blocked")
                self.assertIn("配布チームの一致", result["message"])
                self.assertIn("対象Bundle ID", result["message"])
                options.write_bytes(plistlib.dumps({"teamID": "TEAM123", "signingStyle": "manual",
                                                    "provisioningProfiles": {"org.example.app": "profile"}}))
                self.assertEqual(build_inputs(target, {"artifact": "Fixture.ipa"}, root)["state"], "pass")

    def test_jdk_falls_back_from_broken_environment_but_requires_explicit_override(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wrong = root / "broken"
            native = root / "native"
            for home in (wrong, native):
                (home / "bin").mkdir(parents=True)
                for tool in ("java", "jarsigner", "keytool"):
                    executable = home / "bin" / tool
                    executable.touch()
                    executable.chmod(0o700)

            def probe(argv):
                if argv[0] == "/usr/libexec/java_home":
                    self.assertEqual(argv[-1], "arm64")
                    return Mock(returncode=0, stdout=os.fsencode(native) + b"\n")
                if Path(argv[0]).parent.parent == wrong.resolve():
                    raise OSError("wrong architecture")
                return Mock(returncode=0, stdout=b"", stderr=b"    java.version = 17.0.20\n    os.arch = aarch64\n")

            with patch.dict(os.environ, {"JAVA_HOME": str(wrong)}), patch("jdk.platform.system", return_value="Darwin"), \
                 patch("jdk.platform.machine", return_value="arm64"), patch("jdk.command", side_effect=probe):
                result = resolve_jdk({})
                self.assertEqual(result.home, native.resolve())
                self.assertEqual(result.architecture, "aarch64")
                self.assertIn("JAVA_HOME", result.fallback)
                self.assertEqual(result.environment()["JAVA_HOME"], str(native.resolve()))
                with self.assertRaisesRegex(ValueError, "java_home"):
                    resolve_jdk({"java_home": str(wrong)})

    def test_aab_uses_one_jdk_for_all_tools_and_reports_which_one_failed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            artifact = root / "build.aab"
            with zipfile.ZipFile(artifact, "w") as bundle:
                bundle.writestr("base/manifest/AndroidManifest.xml", b"fixture")
                bundle.writestr("base/dex/classes.dex", b"fixture")
            jdk = JDK(root / "jdk", "17", "aarch64", "java_home")
            target = {"platform": "android", "signer_sha256": "aa" * 32, "bundletool": str(root / "bundletool.jar")}
            manifest = (b'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.example.app" '
                        b'android:versionName="1.2" android:versionCode="3">'
                        b'<uses-sdk android:targetSdkVersion="36"/><application/></manifest>')
            outputs = [b"jar verified.", b"SHA256: " + b"aa:" * 31 + b"aa", manifest]
            with patch("builds.resolve_jdk", return_value=jdk), patch("builds.checked", side_effect=outputs) as checked:
                inspected = file_identity(artifact, target)
                self.assertEqual(inspected.jdk, jdk.details())
                self.assertEqual([call.args[0][0] for call in checked.call_args_list],
                                 [jdk.tool("jarsigner"), jdk.tool("keytool"), jdk.tool("java")])
            with patch("builds.resolve_jdk", return_value=jdk), patch("builds.checked", side_effect=ValueError("exit 1")):
                with self.assertRaisesRegex(ValueError, r"Java 17、aarch64"):
                    file_identity(artifact, target)

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

    def test_ios_deep_link_finding_names_the_app_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "create_app.dart").write_text("const scheme = '<key>CFBundleURLSchemes</key>';")
            info = root / "Info.plist"
            guard = ROOT / "agent-os/hooks/storegate-guard.sh"
            for config, expected in (({}, False), ({"CFBundleURLTypes": [{"CFBundleURLSchemes": ["acme"]}]}, True)):
                info.write_bytes(plistlib.dumps(config))
                report = subprocess.run(["bash", str(guard), str(root), "--platform", "ios"],
                                        capture_output=True, text=True, timeout=60).stdout
                self.assertEqual("BOTH-UNSAFE-DEEPLINK" in report, expected)
                if expected:
                    self.assertRegex(report, r"Info\.plist:\d+")
                    self.assertNotIn("create_app.dart", report)

    def test_android_deep_link_checks_inbound_filters_not_package_queries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "android/app/src/main/AndroidManifest.xml"
            path.parent.mkdir(parents=True)
            query = """<queries><intent>
                <action android:name="android.intent.action.VIEW" />
                <data android:scheme="custom" />
                <data android:scheme="https" />
                </intent></queries>"""
            receiver = """<activity android:name=".Main" android:exported="true"><intent-filter>
                <action android:name="android.intent.action.VIEW" />
                <category android:name="android.intent.category.BROWSABLE" />
                <data android:scheme="custom" />
                </intent-filter></activity>"""
            template = """<manifest xmlns:android="http://schemas.android.com/apk/res/android">
                <application>{}</application>""" + query + "</manifest>"
            guard = ROOT / "agent-os/hooks/storegate-guard.sh"
            for activity, expected in (("", False), (receiver, True)):
                path.write_text(template.format(activity))
                report = subprocess.run(["bash", str(guard), str(root), "--platform", "android"],
                                        capture_output=True, text=True, timeout=60).stdout
                self.assertEqual("BOTH-UNSAFE-DEEPLINK" in report, expected)

    def test_aab_effective_sdk_and_debuggability(self):
        manifest = """<manifest xmlns:android="http://schemas.android.com/apk/res/android"
            package="org.example.app" android:versionName="1.0.0" android:versionCode="3">
            <uses-sdk android:targetSdkVersion="{sdk}" />
            <application android:debuggable="{debug}" />
        </manifest>"""
        identity = android_identity(manifest.format(sdk="36", debug="false"))
        self.assertEqual(identity, ("org.example.app", "1.0.0", "3", "36"))
        with self.assertRaisesRegex(ValueError, "API 36"):
            android_identity(manifest.format(sdk="35", debug="false"))
        with self.assertRaisesRegex(ValueError, "デバッグ"):
            android_identity(manifest.format(sdk="36", debug="true"))
        with self.assertRaisesRegex(ValueError, "対象SDK"):
            android_identity(manifest.format(sdk="VanillaIceCream", debug="false"))

    def test_release_sdk_runs_versioned_rules_and_preserves_resolved_source_risks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            app = root / "app"
            manifest = app / "src/main/AndroidManifest.xml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
                                '<uses-permission android:name="android.permission.READ_CONTACTS"/>'
                                '<application/></manifest>')
            gradle = app / "build.gradle.kts"
            gradle.write_text('plugins { id("com.android.application") }\n'
                              'android { defaultConfig { targetSdk = versions.phone } }\n')
            command = ["bash", str(ROOT / "agent-os/hooks/storegate-guard.sh"), str(root), "--platform", "android"]
            def scan(*args, override=False):
                env = {**os.environ, "APP_STORE_GUARD_OK": "1" if override else "0"}
                return subprocess.run([*command, *args], capture_output=True, text=True, timeout=60, env=env)

            static = scan()
            self.assertIn("Coverage. incomplete", static.stdout)
            self.assertNotIn("GOOGLE-CONTACTS-PICKER-REQUIRED", static.stdout)

            artifact = scan("--android-artifact-sdk", "37")
            self.assertIn("Coverage. complete", artifact.stdout)
            self.assertIn("GOOGLE-TARGET-API-UNCHECKED", artifact.stdout)
            self.assertIn("GOOGLE-CONTACTS-PICKER-REQUIRED", artifact.stdout)

            gradle.write_text('plugins { id("com.android.application") }\n'
                              'android { defaultConfig { targetSdk = 35 } }\n')
            old_source = scan("--android-artifact-sdk", "37", override=True)
            self.assertEqual(old_source.returncode, 2, old_source.stdout)
            self.assertIn("GOOGLE-TARGET-API", old_source.stdout)
            self.assertIn("targetSdk is 35", old_source.stdout)
            self.assertEqual(scan("--android-artifact-sdk", "unresolved").returncode, 1)

            manifest.write_text("<manifest")
            broken = scan("--android-artifact-sdk", "37")
            self.assertIn("Coverage. incomplete", broken.stdout)
            self.assertIn("Android permission declarations could not be parsed", broken.stdout)

    def test_delivery_uses_only_identity_verified_android_sdk(self):
        with self.workspace() as (folder, db):
            profile = {"repo": str(folder), "targets": {"android": {
                "platform": "android", "bundle": "org.example.app", "locales": ["ja"],
            }}}
            release = {"id": "fixture", "platform": "android", "version": "1", "build": "2", "track": "internal"}
            save_config(db, "releases", release)
            verified = {"name": "artifact", "state": "pass", "format": ".aab", "sdk": "37", "sha256": "a" * 64}
            report = b"Summary. critical=0 high=0 medium=0\nCoverage. complete ios=0 android=1 web=0"
            for artifact, expected in ((verified, True), ({"name": "artifact", "state": "blocked"}, False)):
                with self.subTest(state=artifact["state"]):
                    fingerprints = contexts(profile, profile["targets"]["android"], release, "src", [], artifact)
                    with patch("storegate.snapshot", return_value=({"checks": [artifact]}, fingerprints)), \
                         patch("storegate.run", return_value=Mock(stdout=report, returncode=0)) as runner:
                        audit(db, profile, release)
                    command = runner.call_args.args[0]
                    self.assertEqual("--android-artifact-sdk" in command, expected)
                    if expected:
                        self.assertEqual(command[-2:], ["--android-artifact-sdk", "37"])
                    row = db.execute("SELECT data FROM evidence WHERE name='source' AND digest=?",
                                     (fingerprints["source"],)).fetchone()
                    proof = json.loads(row["data"]).get("android_target")
                    self.assertEqual(proof, {"sdk": "37", "artifact_sha256": "a" * 64} if expected else None)
            other = contexts(profile, profile["targets"]["android"], release, "src", [], {**verified, "sha256": "b" * 64})
            original = contexts(profile, profile["targets"]["android"], release, "src", [], verified)
            self.assertNotEqual(original["source"], other["source"])

    def test_packaged_privacy_checks_and_reconciles_only_exact_source_finding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            app = root / "Payload/Fixture.app"
            resource = "plugin.bundle/PrivacyInfo.xcprivacy"
            packaged = app / resource
            packaged.parent.mkdir(parents=True)
            original = {"NSPrivacyAccessedAPITypes": [], "NSPrivacyTracking": False}
            packaged.write_bytes(plistlib.dumps(original))
            with self.assertRaisesRegex(ValueError, "APPLE-MANIFEST-API-TYPES-EMPTY"):
                packaged_privacy_manifests(app)
            packaged.write_bytes(plistlib.dumps({"NSPrivacyTracking": False}, fmt=plistlib.FMT_BINARY))

            source = root / "plugin/PrivacyInfo.xcprivacy"
            source.parent.mkdir()
            source.write_bytes(plistlib.dumps(original))
            target = {"platform": "ios", "bundle": "org.example.app", "locales": ["ja"],
                      "privacy_manifest_sources": {"plugin/PrivacyInfo.xcprivacy": resource}}
            artifact = {"state": "pass", "format": ".ipa", "sha256": "sha-one",
                        "privacy_manifests": packaged_privacy_manifests(app)}
            corrections = privacy_corrections(root, target, artifact)
            self.assertEqual(corrections[str(source)]["packaged_manifest"], resource)
            coverage = "Coverage. complete ios=1 android=0 web=0"
            finding = f"  [CRITICAL] APPLE-MANIFEST-API-TYPES-EMPTY  {source}: NSPrivacyAccessedAPITypes is an empty array."
            report = "\n".join((finding, "Summary. critical=1 high=0 medium=0", coverage))
            state, findings = guard_result(report, 2, "ios", corrections)
            self.assertEqual(state, "pass")
            self.assertEqual(findings[0]["resolution"]["artifact_sha256"], "sha-one")
            self.assertEqual(guard_result(report, 2, "ios")[0], "blocked")
            more_findings = "\n".join((finding, "  [HIGH] APPLE-EXAMPLE  Other issue",
                                       "Summary. critical=1 high=1 medium=0", coverage))
            self.assertEqual(guard_result(more_findings, 2, "ios", corrections)[0], "blocked")
            self.assertEqual(guard_result(report, 1, "ios", corrections)[0], "blocked")
            self.assertEqual(privacy_corrections(root, target, {**artifact, "format": ".xcarchive"}), {})
            self.assertEqual(privacy_corrections(root, target, {**artifact, "state": "blocked"}), {})
            self.assertEqual(privacy_corrections(root, target, {**artifact, "privacy_manifests": {}}), {})
            self.assertEqual(privacy_corrections(root, target, {**artifact, "privacy_manifests": {resource: "different"}}), {})

            release = {"version": "1.0.0", "build": "3", "track": "production"}
            before = contexts({"repo": str(root)}, target, release, "src", [], artifact)
            after = contexts({"repo": str(root)}, target, release, "src", [], {**artifact, "sha256": "sha-two"})
            self.assertNotEqual(before["source"], after["source"])

    def test_source_status_counts_unresolved_findings_from_current_saved_evidence(self):
        with self.workspace() as (folder, db):
            target = {"platform": "ios", "bundle": "org.example.app", "locales": ["ja"],
                      "privacy_manifest_sources": {"plugin/PrivacyInfo.xcprivacy": "plugin.bundle/PrivacyInfo.xcprivacy"}}
            profile = {"name": "Fixture", "repo": str(folder), "targets": {"ios": target}}
            release = {"id": "fixture", "platform": "ios", "version": "1", "build": "2", "track": "production"}
            save_config(db, "releases", release)
            artifact = {"name": "artifact", "state": "pass", "sha256": "a" * 64}
            keys = contexts(profile, target, release, "src", [], artifact)
            data = {
                "message": "Summary. critical=1 high=1 medium=1",
                "findings": [
                    {"severity": "critical", "resolution": {"artifact_sha256": artifact["sha256"]}},
                    {"severity": "high"},
                    {"severity": "medium"},
                ],
            }
            record(db, release, "source", keys["source"], "blocked", data)

            def source_check():
                state, _ = snapshot(db, profile, release)
                return next(item for item in state["checks"] if item["name"] == "source")

            with patch("storegate.git_source", return_value="src"), \
                 patch("storegate.inspect_images", return_value=([], [])), \
                 patch("storegate.artifact_status", return_value=artifact):
                check = source_check()
                self.assertEqual(check["state"], "blocked")
                self.assertEqual(check["finding_counts"], {
                    "unresolved": {"critical": 0, "high": 1, "medium": 1}, "resolved": 1,
                })
                self.assertEqual(check["message"], data["message"])
                self.assertEqual(check["findings"], data["findings"])

                record(db, release, "source", keys["source"], "not_checked", {**data, "findings": []})
                self.assertNotIn("finding_counts", source_check())

                artifact["sha256"] = "b" * 64
                check = source_check()
                self.assertEqual(check["state"], "stale")
                self.assertNotIn("finding_counts", check)

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

    def test_apple_review_contact_updates_only_changed_contact_fields(self):
        contact = {
            "contactFirstName": "Test",
            "contactLastName": "Person",
            "contactPhone": "+15005550006",
            "contactEmail": "tester@example.invalid",
        }
        apple = Apple.__new__(Apple)
        apple.client = Mock()
        apple.status = Mock(return_value={
            "state": "PREPARE_FOR_SUBMISSION", "build": "108", "build_state": "VALID", "version_id": "version",
        })
        previous = {"id": "detail", "attributes": {**contact, "contactEmail": "old@example.invalid", "notes": "Keep"}}
        actual = {"id": "detail", "attributes": contact}
        apple.client.request.side_effect = [{"data": previous}, {}, {"data": actual}, {"data": actual}]
        release = {"version": "1.0.12", "build": "108"}
        verified = {"state": "review_contact_verified"}
        self.assertEqual(apple.sync_review_contact(release, contact), verified)
        self.assertEqual(apple.sync_review_contact(release, contact), verified)
        patch = apple.client.request.call_args_list[1]
        self.assertEqual(patch.args, (
            "/v1/appStoreReviewDetails/detail", "PATCH", {
                "data": {"type": "appStoreReviewDetails", "id": "detail",
                         "attributes": {"contactEmail": contact["contactEmail"]}},
            },
        ))
        self.assertEqual(apple.client.request.call_count, 4)

        apple.client.reset_mock()
        apple.status.return_value["build"] = "109"
        with self.assertRaisesRegex(StoreError, "対象版"):
            apple.sync_review_contact(release, contact)
        apple.client.request.assert_not_called()

    def test_review_contact_preflight_keeps_values_out_of_errors(self):
        with patch.dict(os.environ, {
            "STOREGATE_REVIEW_CONTACT_FIRST_NAME": "Test",
            "STOREGATE_REVIEW_CONTACT_LAST_NAME": "Person",
            "STOREGATE_REVIEW_CONTACT_PHONE": "private-invalid-phone",
            "STOREGATE_REVIEW_CONTACT_EMAIL": "tester@example.invalid",
        }, clear=True):
            with self.assertRaises(StoreError) as failure:
                review_contact_from_env()
            self.assertNotIn("private-invalid-phone", str(failure.exception))
            os.environ["STOREGATE_REVIEW_CONTACT_PHONE"] = "+15005550006"
            self.assertEqual(review_contact_from_env()["contactPhone"], "+15005550006")

    def test_google_status_is_scoped_to_track_and_build(self):
        google = Google.__new__(Google)
        google.app, google.client = "org.example.app", Mock()
        published = "RELEASE_LIFECYCLE_STATE_PUBLISHED"
        in_review = "RELEASE_LIFECYCLE_STATE_IN_REVIEW"
        google.client.request.return_value = {"releases": [
            {"track": "production", "activeArtifacts": [{"versionCode": 108}], "releaseLifecycleState": published},
            {"track": "qa", "activeArtifacts": [{"versionCode": 109}], "releaseLifecycleState": published},
            {"track": "qa", "activeArtifacts": [{"versionCode": 108}], "releaseLifecycleState": in_review},
        ]}
        internal = {"track": "internal", "build": "108"}
        result = google.status(internal)
        google.client.request.assert_called_once_with("/androidpublisher/v3/applications/org.example.app/tracks/qa/releases")
        self.assertEqual(result["state"], in_review)
        self.assertEqual(result["track"], "internal")
        self.assertEqual(result["active_builds"], ["108"])
        self.assertFalse(google.is_published(internal, result))
        production = {**internal, "track": "production"}
        self.assertTrue(google.is_published(production, google.status(production)))
        self.assertFalse(google.is_published(production, result))

    def test_apple_lookup_requires_identity_platform_and_requested_region(self):
        row = {"trackId": 123456, "bundleId": "org.example.app", "kind": "mac-software", "version": "1.2",
               "trackViewUrl": "https://apps.apple.com/jp/app/example/id123456?uo=4"}
        with patch("stores.build_opener") as factory:
            opener = factory.return_value
            response = opener.open.return_value.__enter__.return_value
            response.read.return_value = json.dumps({"results": [row]}).encode()
            observed = apple_storefront("123456", "org.example.app", "macos", "JP")
            self.assertEqual(observed, {"region": "JP", "state": "observed", "version": "1.2", "url": row["trackViewUrl"]})
            request = opener.open.call_args.args[0]
            self.assertEqual(parse_qs(urlparse(request.full_url).query),
                             {"id": ["123456"], "country": ["jp"], "entity": ["macSoftware"]})
            self.assertIsNone(request.get_header("Authorization"))
            self.assertEqual(apple_storefront("123456", "org.example.app", "ios", "JP")["state"], "not_observed")
            self.assertEqual(apple_storefront("123456", "org.example.other", "macos", "JP")["state"], "not_observed")
            with self.assertRaises(StoreError):
                apple_storefront("123456", "org.example.app", "macos", "US")

    def test_apple_publication_requires_serving_build_and_all_regions(self):
        with self.workspace() as (folder, db):
            target = {"platform": "ios", "bundle": "org.example.app", "store_id": "123456", "locales": ["ja"]}
            profile = {"id": "fixture", "name": "Fixture", "repo": str(ROOT), "targets": {"ios": target}}
            release = {"id": "fixture/ios/1.2/108/production", "app": "fixture", "platform": "ios",
                       "version": "1.2", "build": "108", "track": "production", "regions": ["JP", "US"]}
            save_config(db, "profiles", profile)
            save_config(db, "releases", release)
            command = argparse.Namespace(command="verify-publication", id=release["id"])
            remote = {"state": "READY_FOR_DISTRIBUTION", "build": "109"}
            jp = {"region": "JP", "state": "observed", "version": "1.2"}
            us = {"region": "US", "state": "observed", "version": "1.1"}
            with (patch.object(Apple, "__init__", return_value=None),
                  patch.object(Apple, "status", return_value=remote),
                  patch.object(Apple, "public_storefront") as storefront):
                self.assertEqual(execute(folder, db, command)["state"], "unconfirmed")
                storefront.assert_not_called()
                remote["build"] = "108"
                storefront.side_effect = [jp, us]
                result = execute(folder, db, command)
                self.assertEqual(result["state"], "unconfirmed")
                self.assertEqual([entry["matches"] for entry in result["storefronts"]], [True, False])
                storefront.side_effect = [jp, {**us, "version": "1.2"}]
                self.assertEqual(execute(folder, db, command)["state"], "verified")
            status = execute(folder, db, argparse.Namespace(command="status", id=release["id"]))
            evidence = next(item for item in status["checks"] if item["name"] == "publication")
            self.assertEqual(evidence["state"], "pass")
            self.assertEqual(evidence["remote"]["build"], "108")
            self.assertEqual(status["regions"], ["JP", "US"])

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
