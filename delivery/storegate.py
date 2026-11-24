#!/usr/bin/env python3
"""Shared local StoreGate workflow for the CLI and the macOS app."""
import argparse
import hashlib
import glob
import importlib.util
import json
import os
import plistlib
import re
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from assets import PLAY_COUNTS, SLOTS, inspect_images
from builds import (build_commands, build_host, digest, file_identity, git_source, lock,
                    manifest_digest, run, tree_digest)
from testing import TEST_STAGES, save_observation, test_progress, validate_invite

ROOT = Path(__file__).resolve().parents[1]
STORES = {"ios": ("App Store Connect", "https://appstoreconnect.apple.com/apps"),
          "macos": ("App Store Connect", "https://appstoreconnect.apple.com/apps"),
          "android": ("Play Console", "https://play.google.com/console")}
MANUAL = {
    "account": "アプリ登録、契約、アカウント権限と対象ストアIDの照合",
    "device": "対象成果物での主要操作・端末・アクセシビリティ確認",
    "declarations": "プライバシー・年齢・暗号化等の担当者による申告確定",
    "checklist": "提出前チェックリストの適用項目と指摘への対応",
    "storefront": "保存済みのビルド、掲載内容、価格、地域、公開方法の照合",
}
IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z")
BUNDLE = re.compile(r"[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)+\Z")


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def workspace():
    return Path(os.environ.get("STOREGATE_WORKSPACE_DIR") or Path.home() / "Library/Application Support/StoreGate")


def token(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("識別子・版・ビルド番号の形式を確認してください。")
    return value


def absolute(base, value):
    return str((Path(base) / value).resolve()) if value else None


def fields_only(value, keys):
    if not isinstance(value, dict) or value.keys() - set(keys.split()):
        raise ValueError("設定に認識できない項目があります。サンプルの項目名を確認してください。")


def manifest_path(value):
    if not isinstance(value, str):
        return False
    path = Path(value)
    return (not path.is_absolute() and ".." not in path.parts and value == path.as_posix()
            and path.name == "PrivacyInfo.xcprivacy")


def profile_from(source):
    profile = json.loads(source.read_text())
    fields_only(profile, "id name repo targets")
    token(profile["id"])
    if not isinstance(profile["name"], str) or not profile["name"].strip():
        raise ValueError("アプリ名を入力してください。")
    profile["repo"] = absolute(source.parent, profile["repo"])
    if not profile["repo"] or not Path(profile["repo"]).is_dir():
        raise ValueError("リポジトリの場所を確認してください。")
    if not profile["targets"] or profile["targets"].keys() - STORES.keys():
        raise ValueError("ストアの対象を登録してください。")
    for platform, target in profile["targets"].items():
        fields_only(target, "bundle store_id provider auth_env team_id signer_sha256 build_method build_command preflight qa_command capture_command project scheme archive export_options export_path gradle_task bundletool required_entries slots locales placement_groups play_staging issuer_id transporter_key_id privacy_manifest_sources")
        if not BUNDLE.fullmatch(target["bundle"]):
            raise ValueError("Bundle ID / applicationId を確認してください。")
        if target["build_method"] not in {"flutter", "xcode", "gradle", "ci"}:
            raise ValueError("ビルド方式を確認してください。")
        for name in ("store_id", "provider", "auth_env", "team_id", "issuer_id", "transporter_key_id"):
            if target.get(name):
                token(target[name])
        for name in ("build_command", "preflight", "qa_command", "capture_command"):
            if name in target:
                argv = target[name]
                if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg for arg in argv):
                    raise ValueError("実行するコマンドを引数の配列で指定してください。")
        locales = target.get("locales", ["ja"])
        if not isinstance(locales, list) or not locales or len(set(locales)) != len(locales):
            raise ValueError("掲載言語を確認してください。")
        for locale in locales:
            token(locale)
        target["locales"] = locales
        slots = target.get("slots", {})
        limits = PLAY_COUNTS if platform == "android" else {slot: (1, 10) for slot in SLOTS[platform].values()}
        if (slots.keys() - limits.keys()
                or any(type(count) is not int or not limits[slot][0] <= count <= limits[slot][1]
                       for slot, count in slots.items())):
            raise ValueError("端末枠と画像の枚数を確認してください。")
        for name in ("project", "archive", "export_options", "export_path", "bundletool"):
            if target.get(name):
                target[name] = absolute(profile["repo"], target[name])
        for group in target.get("placement_groups", {}).values():
            token(group)
        if "play_staging" in target and type(target["play_staging"]) is not bool:
            raise ValueError("play_staging には true または false を指定してください。")
        mapping = target.get("privacy_manifest_sources", {})
        if not isinstance(mapping, dict) or (
            mapping and (platform != "ios" or any(not manifest_path(path) for path in (*mapping.keys(), *mapping.values()))
                         or len(set(mapping.values())) != len(mapping))
        ):
            raise ValueError("iOSのソースと同梱Privacy Manifestの相対パスを確認してください。")
        target["platform"] = platform
    return profile


def release_from(source, profile):
    value = json.loads(source.read_text())
    fields_only(value, "app platform version build track pack artifact artifact_pattern metadata regions price purchases publication first")
    if value["app"] != profile["id"]:
        raise ValueError("配信設定のアプリが一致しません。")
    target = profile["targets"][value["platform"]]
    version, build = token(value["version"]), token(value["build"])
    track = token(value.get("track", "production"))
    if target["platform"] != "android" and track != "production":
        raise ValueError("Appleのストア配信案件はproductionを指定してください。")
    for name in ("first", "purchases"):
        if name in value and type(value[name]) is not bool:
            raise ValueError("初回・支援購入には true または false を指定してください。")
    if value.get("publication") not in (None, "manual", "automatic"):
        raise ValueError("公開方式を確認してください。")
    regions = value.get("regions", [])
    if not isinstance(regions, list) or any(not re.fullmatch(r"[A-Z]{2}", region) for region in regions):
        raise ValueError("配信地域には二文字の地域コードを指定してください。")
    value["regions"] = sorted(set(regions))
    for name in ("pack", "artifact", "artifact_pattern", "metadata"):
        value[name] = absolute(source.parent, value.get(name))
    value.update(version=version, build=build, track=track,
                 id="/".join((value["app"], value["platform"], version, build, track)))
    return value


@contextmanager
def database(folder):
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = folder / "delivery.sqlite3"
    with closing(sqlite3.connect(path, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS releases (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS evidence (
                release TEXT NOT NULL REFERENCES releases(id), name TEXT NOT NULL,
                digest TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL, time TEXT NOT NULL,
                PRIMARY KEY(release, name, digest)
            );
            CREATE TABLE IF NOT EXISTS operations (
                release TEXT NOT NULL REFERENCES releases(id), action TEXT NOT NULL,
                digest TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL, time TEXT NOT NULL,
                PRIMARY KEY(release, action, digest)
            );
            CREATE TABLE IF NOT EXISTS legacy_mac (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, time TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS test_observations (
                id INTEGER PRIMARY KEY, release TEXT NOT NULL REFERENCES releases(id),
                scope TEXT NOT NULL, stage TEXT NOT NULL, device TEXT NOT NULL,
                operation TEXT NOT NULL, data TEXT NOT NULL, time TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS test_observations_scope
                ON test_observations(release, scope, stage, device, operation, id);
        """)
        yield db


def migrate(db, path):
    try:
        content = path.read_text()
    except FileNotFoundError:
        if path.with_suffix(".json.previous").is_file():
            raise ValueError("既存のMac案件は、Mac App Storeの画面でバックアップから復元してください。")
        return 0
    projects = json.loads(content)["projects"]
    if not isinstance(projects, list):
        raise ValueError("既存Mac案件の形式を確認してください。")
    before = db.total_changes
    with db:
        for project in projects:
            db.execute("INSERT OR IGNORE INTO legacy_mac VALUES (?, ?, ?)",
                       (token(project["id"]).lower(), encoded(project), now()))
    return db.total_changes - before


def fetch(db, table, key):
    row = db.execute(f"SELECT data FROM {table} WHERE id = ?", (key,)).fetchone()
    if not row:
        raise ValueError(f"登録を確認してください。{key}")
    return json.loads(row["data"])


def save_config(db, table, value):
    with db:
        db.execute(f"INSERT INTO {table} VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                   (value["id"], encoded(value)))


def record(db, release, name, digest, state, data):
    with db:
        db.execute("INSERT OR REPLACE INTO evidence VALUES (?, ?, ?, ?, ?, ?)",
                   (release["id"], name, digest, state, encoded(data), now()))


def finding(name, state, message, **extra):
    return {"name": name, "state": state, "message": message, **extra}


def observe(db, release, name, digest, missing):
    row = db.execute("SELECT * FROM evidence WHERE release=? AND name=? AND digest=?",
                     (release["id"], name, digest)).fetchone()
    if row:
        evidence = json.loads(row["data"])
        return finding(name, row["state"], evidence.pop("message"), **evidence)
    old = db.execute("SELECT 1 FROM evidence WHERE release=? AND name=? LIMIT 1", (release["id"], name)).fetchone()
    return finding(name, "stale" if old else "pending", missing)


def contexts(profile, target, release, source, images, artifact):
    identity = [target["platform"], target["bundle"], release["version"], release["build"], release["track"]]
    store = [identity, target.get("store_id"), target.get("provider"), target.get("auth_env")]
    local_metadata = release.get("metadata")
    listing = tree_digest(Path(local_metadata)) if local_metadata and Path(local_metadata).is_dir() else None
    assets = [{key: value for key, value in image.items() if key != "path"} for image in images]
    terms = [release.get(key) for key in ("regions", "price", "purchases", "publication", "first")]
    artifact_digest = artifact.get("sha256")
    guard_rules = [digest(ROOT / name) for name in (
        "agent-os/hooks/storegate-guard.sh", "scripts/fleet-sweep.py", "scripts/macos-source-audit.py",
        "scripts/android-target-sdk.py", "scripts/validate-privacy-manifest.py",
        "scripts/deadline-checker.py", "data/regulatory-deadlines.json"
    )]
    listing_rules = digest(ROOT / "scripts/metadata-audit.py")
    source_rules = [identity, profile["repo"], source, guard_rules]
    if target["platform"] == "android":
        source_rules.extend([artifact_digest, artifact.get("sdk")])
    if target.get("privacy_manifest_sources"):
        source_rules.extend([target["privacy_manifest_sources"], artifact_digest])
    return {name: fingerprint(value) for name, value in {
        "source": source_rules,
        "build_preflight": [identity, source, target.get("preflight")],
        "metadata": [identity, listing, target["locales"], listing_rules],
        "account": store,
        "device": [identity, artifact_digest],
        "declarations": [store, source, artifact_digest, terms],
        "checklist": [store, source, artifact_digest, assets, listing, terms, guard_rules, listing_rules],
        "storefront": [store, artifact_digest, assets, listing, terms],
        "qa": [identity, artifact_digest, target.get("qa_command")],
        "publication": [store, terms],
    }.items()}


def artifact_status(target, release):
    if not release.get("artifact"):
        return finding("artifact", "pending", "署名済み成果物を登録するかビルドしてください。")
    try:
        path = Path(release["artifact"])
        artifact = file_identity(path, target)
        if (artifact.bundle, artifact.version, artifact.build) != (target["bundle"], release["version"], release["build"]):
            raise ValueError("成果物のアプリID・版・ビルド番号が案件と一致しません。")
        manifests = {"privacy_manifests": artifact.manifests} if artifact.manifests else {}
        return finding("artifact", "pass", "成果物の識別情報と署名を照合しました。",
                       sdk=artifact.sdk, sha256=artifact.sha256, format=path.suffix, **manifests)
    except (ValueError, OSError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
        message = str(exc) if type(exc) is ValueError else "成果物または検査ツールを確認してください。"
        return finding("artifact", "blocked", message)


def testing_scope(target, release):
    if release["platform"] == "ios" or (release["platform"] == "android" and release["track"] != "production"):
        return fingerprint([target["bundle"], target.get("store_id"), target.get("provider")])
    return None


def testing_status(db, target, release, scope):
    invite = observe(db, release, "test-invite", scope, "").get("url")
    channel = "TestFlight" if target["platform"] == "ios" else f"Google Play · {release['track']}"
    return {"channel": channel, "invite": invite, **test_progress(db, release, scope)}


def testing_section(db, target, release):
    scope = testing_scope(target, release)
    return {"testing": testing_status(db, target, release, scope)} if scope else {}


def snapshot(db, profile, release):
    target = profile["targets"][release["platform"]]
    images, problems = inspect_images(release.get("pack"), target["platform"], target["locales"],
                                      target["locales"][0], target.get("slots", {}))
    artifact = artifact_status(target, release)
    source = git_source(Path(profile["repo"]))
    keys = contexts(profile, target, release, source, images, artifact)
    stages = [finding("screenshots", "blocked" if problems else "pass",
                      "\n".join(problems) if problems else f"{len(images)} 枚の掲載画像を照合しました。"), artifact]
    if target.get("preflight"):
        stages.append(observe(db, release, "build_preflight", keys["build_preflight"],
                              "アプリの配布設定を検査してください。"))
    terms_set = (release.get("regions") and isinstance(release.get("price"), str)
                 and release["price"].strip() and release.get("publication")
                 and type(release.get("purchases")) is bool and type(release.get("first")) is bool)
    stages.append(finding("conditions", "pass" if terms_set else "pending",
                          "価格・地域・支援購入・公開方式・初回扱いを確定してください。" if not terms_set else
                          "配信条件を登録しました。"))
    source_check = observe(db, release, "source", keys["source"], "ソース監査を実行してください。")
    if source_check["state"] in ("pass", "blocked") and "findings" in source_check:
        unresolved = dict.fromkeys(("critical", "high", "medium"), 0)
        resolved = 0
        for item in source_check["findings"]:
            if "resolution" in item:
                resolved += 1
            else:
                unresolved[item["severity"]] += 1
        source_check["finding_counts"] = {"unresolved": unresolved, "resolved": resolved}
    stages.append(source_check)
    for name, prompt in {"metadata": "掲載原稿を登録して監査してください。", **MANUAL}.items():
        stages.append(observe(db, release, name, keys[name], prompt))
    if db.execute("SELECT 1 FROM evidence WHERE release=? AND name='publication'", (release["id"],)).fetchone():
        stages.append(observe(db, release, "publication", keys["publication"], "公開状況を再確認してください。"))
    has_qa = db.execute("SELECT 1 FROM evidence WHERE release=? AND name='qa'", (release["id"],)).fetchone()
    if target.get("qa_command") or has_qa:
        stages.append(observe(db, release, "qa", keys["qa"], "対象成果物で登録済みQAを実行してください。"))
    store, console = STORES[target["platform"]]
    operations = []
    for row in db.execute(
        "SELECT action,state,time,data FROM operations WHERE release=? ORDER BY time DESC", (release["id"],)
    ):
        entry = {name: row[name] for name in ("action", "state", "time")}
        if row["action"] == "store-status":
            entry["remoteState"] = json.loads(row["data"]).get("state")
        operations.append(entry)
    publication = next((item for item in stages if item["name"] == "publication"), None)
    blocked = next((item["message"] for item in stages if item["state"] != "pass"), None)
    if publication and publication["state"] == "pass":
        blocked = "予定地域で対象版の公開を確認しました。"
    return {"id": release["id"], "app": profile["name"], "platform": target["platform"], "store": store,
            "console": console, "bundle": target["bundle"], "store_id": target.get("store_id"),
            "version": release["version"], "build": release["build"], "track": release["track"],
            "regions": release.get("regions", []), "images": images, "checks": stages, "operations": operations,
            "next": blocked or "対象版のストア状態を照会してください。",
            **testing_section(db, target, release)}, keys


def expand(argv, release):
    values = {key: release.get(key) or "" for key in ("version", "build", "artifact", "pack")}
    return [re.sub(r"\{(version|build|artifact|pack)\}", lambda match: values[match[1]], arg) for arg in argv]


def privacy_corrections(repo, target, artifact):
    if target["platform"] != "ios" or artifact["state"] != "pass" or artifact.get("format") != ".ipa":
        return {}
    root = Path(repo).resolve()
    packaged = artifact.get("privacy_manifests", {})
    corrections = {}
    for raw, resource in target.get("privacy_manifest_sources", {}).items():
        source = root / raw
        if not source.resolve().is_relative_to(root) or not packaged.get(resource):
            continue
        try:
            plist = plistlib.loads(source.read_bytes())
            if not isinstance(plist, dict) or plist.get("NSPrivacyAccessedAPITypes") != []:
                continue
            del plist["NSPrivacyAccessedAPITypes"]
            if manifest_digest(plist) == packaged[resource]:
                corrections[str(source)] = {"packaged_manifest": resource, "artifact_sha256": artifact["sha256"]}
        except (OSError, ValueError, TypeError):
            continue
    return corrections


def guard_result(report, code, platform, corrections=None):
    # Use the Fleet Sweep parser so deadlines and findings keep the same meaning in both workflows.
    spec = importlib.util.spec_from_file_location("storegate_fleet", ROOT / "scripts/fleet-sweep.py")
    fleet = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fleet)
    findings, summaries, coverages = fleet.parse_report(report)
    problem = fleet.integrity_problem(code, findings, summaries, coverages)
    if platform == "ios" and not problem and corrections:
        for item in findings:
            file, marker, _ = item["title"].partition(": NSPrivacyAccessedAPITypes is an empty array.")
            if marker and item["id"] == "APPLE-MANIFEST-API-TYPES-EMPTY" and file in corrections:
                item["resolution"] = corrections[file]
    if any(item["severity"] in {"critical", "high"} and "resolution" not in item for item in findings):
        return "blocked", findings
    if problem:
        return "not_checked", findings
    state, ios, android, _web, macos, _reason = coverages[0]
    covered = {"ios": ios, "android": android, "macos": macos}
    return ("pass" if state == "complete" and covered[platform] == "1" else "not_checked"), findings


def audit(db, profile, release):
    target = profile["targets"][release["platform"]]
    state, keys = snapshot(db, profile, release)
    artifact = next(item for item in state["checks"] if item["name"] == "artifact")
    if target.get("preflight"):
        result = run(expand(target["preflight"], release), profile["repo"])
        message = ("アプリの配布設定を検査しました。" if not result.returncode else
                   "アプリの配布設定を確認してください。登録された事前検査が通っていません。")
        record(db, release, "build_preflight", keys["build_preflight"],
               "blocked" if result.returncode else "pass", {"message": message})
    command = ["bash", str(ROOT / "agent-os/hooks/storegate-guard.sh"), profile["repo"], "--platform", release["platform"]]
    sdk_evidence = {}
    if target["platform"] == "android" and artifact["state"] == "pass" and artifact.get("format") == ".aab":
        command.extend(["--android-artifact-sdk", str(int(artifact["sdk"]))])
        sdk_evidence["android_target"] = {"sdk": artifact["sdk"], "artifact_sha256": artifact["sha256"]}
    env = dict(os.environ)
    env.pop("APP_STORE_GUARD_OK", None)
    guard = run(command, timeout=180, env=env)
    lines = [line for line in guard.stdout.decode(errors="replace").strip().splitlines() if line]
    # The standalone guard is advisory and can return zero with incomplete evidence.
    text = "\n".join(lines)
    corrections = privacy_corrections(profile["repo"], target, artifact)
    result, findings = guard_result(text, guard.returncode, release["platform"], corrections)
    resolved_count = sum("resolution" in item for item in findings)
    if resolved_count:
        text = f"署名済みIPAでPrivacy Manifestの修正を {resolved_count} 件確認しました。\n" + text
    if sdk_evidence:
        text = f"署名済みAABの対象SDKはAPI {artifact['sdk']}です。Gradleの静的検査結果も記録しています。\n" + text
    record(db, release, "source", keys["source"], result,
           {"message": text or "ガードの実行結果を確認してください。", "findings": findings, **sdk_evidence})
    if release.get("metadata"):
        cmd = [sys.executable, str(ROOT / "scripts/metadata-audit.py"), release["metadata"],
               "--platform", "google" if target["platform"] == "android" else "apple", "--check-urls"]
        meta = run(cmd, timeout=240)
        record(db, release, "metadata", keys["metadata"], "blocked" if meta.returncode else "pass",
               {"message": meta.stdout.decode(errors="replace").strip() or "掲載原稿の監査結果を確認してください。"})


def build(db, profile, release):
    target = profile["targets"][release["platform"]]
    repo = Path(profile["repo"])
    if target.get("preflight"):
        result = run(expand(target["preflight"], release), repo)
        if result.returncode:
            raise ValueError("アプリの配布設定が未完了です。登録されたビルド前検査を確認してください。")
    pattern = release.get("artifact_pattern")
    artifact = release.get("artifact")
    if not pattern and not artifact:
        raise ValueError("受け取る署名成果物のパスを配信設定に登録してください。")
    def candidates():
        paths = glob.glob(pattern) if pattern else [artifact]
        return {str(path): path.stat().st_mtime_ns for path in map(Path, paths) if path.exists()}

    with build_host(repo):
        previous = candidates()
        for argv in build_commands(profile, target):
            result = run(expand(argv, release), repo, timeout=7200)
            if result.returncode:
                raise ValueError(f"ビルドが終了コード {result.returncode} を返しました。アプリのビルド手順と署名を確認してください。")
        changed = [path for path, modified in candidates().items() if previous.get(path) != modified]
        if len(changed) != 1:
            raise ValueError("今回生成した成果物を一意に特定できません。出力パスを確認してください。")
        release["artifact"] = changed[0]
        result = artifact_status(target, release)
        if result["state"] != "pass":
            raise ValueError(result["message"])
        save_config(db, "releases", release)
    state, _ = snapshot(db, profile, release)
    return state


def list_releases(db):
    registered = []
    for row in db.execute("SELECT data FROM releases ORDER BY id"):
        release = json.loads(row["data"])
        profile = None
        try:
            profile = fetch(db, "profiles", release["app"])
            state, _ = snapshot(db, profile, release)
        except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
            state = {**release, "app": (profile or {}).get("name", release["app"]),
                     "next": str(exc), "checks": [], "images": []}
            target = profile["targets"].get(release["platform"]) if profile else None
            if target:
                state.update(testing_section(db, target, release))
                state["console"] = STORES[release["platform"]][1]
        registered.append(state)
    legacy = []
    for row in db.execute("SELECT id,data FROM legacy_mac ORDER BY id"):
        item = json.loads(row["data"])
        scope = item.get("scope", {})
        legacy.append({"id": row["id"], "app": item["name"], "platform": "macos",
                       "version": scope.get("version", ""), "build": scope.get("build", ""),
                       "phase": item.get("phase", "upload"), "history_count": len(item.get("records", []))})
    return {"releases": registered, "legacy": legacy}


class Blocked(ValueError):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


def require_ready(state, excluded=()):
    open_checks = [item for item in state["checks"] if item["state"] != "pass" and item["name"] not in excluded]
    if open_checks:
        raise Blocked("対象版の必須項目が残っています。", state)


def history(db, release, action):
    return list(db.execute("SELECT * FROM operations WHERE release=? AND action=? ORDER BY time DESC",
                           (release["id"], action)))


def save_operation(db, release, action, digest, state, data):
    with db:
        db.execute("INSERT OR REPLACE INTO operations VALUES (?, ?, ?, ?, ?, ?)",
                   (release["id"], action, digest, state, encoded(data), now()))


def dispatch_store(db, release, action, adapter, state=None, keys=None, approval=None):
    from stores import StoreError

    remote = adapter.status(release)
    if action == "store-status":
        for pending_action in ("submit", "publish"):
            for previous in history(db, release, pending_action):
                if previous["state"] not in {"running", "unknown", "requested"}:
                    continue
                details = json.loads(previous["data"])
                if adapter.reconciled(pending_action, details["before"], remote, release):
                    save_operation(db, release, pending_action, previous["digest"], "received", details)
        observation = fingerprint(remote)
        save_operation(db, release, action, observation, "observed", remote)
        return {"remote": remote, "console": state["console"]}
    for previous in history(db, release, action):
        details = json.loads(previous["data"])
        if previous["state"] in {"running", "unknown", "requested"}:
            if adapter.reconciled(action, details["before"], remote, release):
                save_operation(db, release, action, previous["digest"], "received", details)
                return {"state": "received", "remote": remote}
            raise StoreError("送信結果を確定できません。公式画面で対象版を確認してください。")
        if previous["state"] == "received" and adapter.reconciled(action, details["before"], remote, release):
            return {"state": "received", "remote": remote}

    require_ready(state, ("publication",))
    operation = adapter.describe(action, release, remote)
    summary = {key: state[key] for key in ("app", "platform", "bundle", "store_id", "version", "build", "track")}
    summary.update({key: release.get(key) for key in ("price", "regions", "purchases", "publication", "first")})
    summary.update(operation, action=action, before=remote, evidence=keys, images=state["images"])
    artifact = next((item for item in state["checks"] if item["name"] == "artifact"), None)
    if artifact:
        summary["artifact_sha256"] = artifact.get("sha256")
    confirmation = fingerprint(summary)
    if approval is None:
        return {"state": "confirmation_required", "confirmation": confirmation, "summary": summary}
    if approval != confirmation:
        raise StoreError("確認後に内容が変わりました。対象版と差分を確認し直してください。")
    save_operation(db, release, action, confirmation, "running", summary)
    try:
        adapter.apply(action, remote, operation)
    except StoreError as exc:
        save_operation(db, release, action, confirmation, "unknown" if exc.uncertain else "rejected", summary)
        raise
    save_operation(db, release, action, confirmation, "requested", summary)
    return {"state": "requested", "remote": adapter.status(release)}


def attest(db, profile, release, args):
    state, keys = snapshot(db, profile, release)
    if not args.note.strip():
        raise ValueError("確認結果・根拠を記録してください。")
    if args.check in {"device", "declarations", "storefront"} and any(
        item["name"] == "artifact" and item["state"] != "pass" for item in state["checks"]
    ):
        raise ValueError("対象成果物の署名と版を照合してから記録してください。")
    evidence = {"message": args.note.strip()}
    if args.check == "publication":
        if not release["regions"] or sorted(args.region or []) != release["regions"]:
            raise ValueError("予定地域すべてで対象版の入手を確認してください。")
        from stores import Apple, Google
        target = profile["targets"][release["platform"]]
        adapter = Google(target) if release["platform"] == "android" else Apple(target)
        remote = adapter.status(release)
        if not adapter.is_published(release, remote):
            raise ValueError("対象版が配信中であることをストアAPIから確認できません。")
        evidence.update(source="manual", regions=release["regions"], remote=remote)
    record(db, release, args.check, keys[args.check], args.outcome, evidence)
    return snapshot(db, profile, release)[0]


def verify_publication(db, profile, release):
    from stores import Apple

    if release["platform"] == "android":
        raise ValueError("Google Playの予定地域で配布版を確認し、公開確認の根拠を記録してください。")
    if not release["regions"]:
        raise ValueError("公開状況を照会する前に配信地域を登録してください。")
    _, keys = snapshot(db, profile, release)
    target = profile["targets"][release["platform"]]
    adapter = Apple(target)
    remote = adapter.status(release)
    rows = []
    outcome = "blocked"
    if not adapter.is_published(release, remote):
        message = "App Store Connectで対象版・ビルドの配信開始を確認できません。"
    else:
        for region in release["regions"]:
            row = adapter.public_storefront(region)
            rows.append({**row, "matches": row.get("version") == release["version"]})
        missing = [row["region"] for row in rows if not row["matches"]]
        if missing:
            message = f"対象版の公開を確認できない地域があります（{', '.join(missing)}）。"
        else:
            outcome = "pass"
            message = f"{', '.join(release['regions'])} のApp Storeで版 {release['version']} の公開を確認しました。"
    evidence = {"message": message, "source": "apple_public_storefront", "storefronts": rows, "remote": remote}
    record(db, release, "publication", keys["publication"], outcome, evidence)
    return {"state": "verified" if outcome == "pass" else "unconfirmed", **evidence}


def parse():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=workspace())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    commands.add_parser("migrate")
    commands.add_parser("legacy").add_argument("legacy_id")
    commands.add_parser("register").add_argument("file", type=Path)
    commands.add_parser("release").add_argument("file", type=Path)
    receipt = commands.add_parser("import-qa")
    receipt.add_argument("id")
    receipt.add_argument("file", type=Path)
    commands.add_parser("test-status", help="対象版のテストの記録を表示する").add_argument("id")
    invite = commands.add_parser("test-link", help="対象版のテスト参加リンクを記録する")
    invite.add_argument("id")
    link_action = invite.add_mutually_exclusive_group(required=True)
    link_action.add_argument("--url")
    link_action.add_argument("--clear", action="store_true")
    observation = commands.add_parser("record-test", help="テスト配信・端末の確認結果を記録する")
    observation.add_argument("id")
    observation.add_argument("stage", choices=TEST_STAGES)
    observation.add_argument("--outcome", required=True, choices=("pass", "blocked"))
    observation.add_argument("--source", required=True, choices=("store", "device", "tester"))
    observation.add_argument("--note", required=True)
    observation.add_argument("--device")
    observation.add_argument("--os")
    observation.add_argument("--operation")
    observation.add_argument("--version")
    observation.add_argument("--build")
    observation.add_argument("--build-source", choices=("device", "tester", "store"))
    for name in ("check", "build", "prepare", "status", "store-status", "submit", "publish", "verify-publication",
                 "attest", "qa", "capture", "review-draft", "draft", "upload-build"):
        command = commands.add_parser(name)
        command.add_argument("id", nargs="?" if name == "prepare" else None)
        if name in ("submit", "publish"):
            command.add_argument("--approve")
        if name == "attest":
            command.add_argument("check", choices=[*MANUAL, "publication"])
            command.add_argument("--outcome", default="pass", choices=("pass", "blocked"))
            command.add_argument("--note", required=True)
            command.add_argument("--region", action="append")
    return parser.parse_args()


def import_qa(db, profile, release, path):
    receipt = json.loads(path.read_text())
    fields_only(receipt, "artifact_sha256 suite run outcome")
    state, keys = snapshot(db, profile, release)
    artifact = next(item for item in state["checks"] if item["name"] == "artifact")
    if artifact["state"] != "pass" or receipt["artifact_sha256"] != artifact["sha256"]:
        raise ValueError("CIのQA結果が対象の署名済み成果物と一致しません。")
    if receipt["outcome"] not in {"pass", "blocked"} or any(
        not isinstance(receipt[key], str) or not receipt[key].strip() for key in ("suite", "run")
    ):
        raise ValueError("CIのテスト名、実行記録、結果を確認してください。")
    record(db, release, "qa", keys["qa"], receipt["outcome"],
           {"message": f"CIのQA結果を取り込みました。{receipt['suite']} · {receipt['run']}", "kind": "ci"})
    return snapshot(db, profile, release)[0]


def execute(folder, db, args):
    command = args.command
    if command == "migrate":
        return {"imported": migrate(db, folder / "delivery-workspace.json")}
    if command == "list":
        result = list_releases(db)
        try:
            added = migrate(db, folder / "delivery-workspace.json")
            return list_releases(db) if added else result
        except (ValueError, KeyError, TypeError, OSError):
            return {**result, "migration_error": "既存Mac案件の読み込みに失敗しました。Mac App Store画面から確認してください。"}
    if command == "legacy":
        return fetch(db, "legacy_mac", args.legacy_id)
    if command == "register":
        profile = profile_from(args.file.resolve())
        with lock(folder / (profile["id"] + ".lock")):
            save_config(db, "profiles", profile)
        return {"app": profile["id"]}
    if command == "release":
        profile = fetch(db, "profiles", json.loads(args.file.read_text())["app"])
        with lock(folder / (profile["id"] + ".lock")):
            profile = fetch(db, "profiles", profile["id"])
            release = release_from(args.file.resolve(), profile)
            save_config(db, "releases", release)
        return {"id": release["id"]}
    if command == "prepare" and not args.id:
        batch = []
        for row in list(db.execute("SELECT id FROM releases ORDER BY id")):
            try:
                batch.append({"id": row["id"], "result": execute(folder, db, argparse.Namespace(command="prepare", id=row["id"]))})
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                batch.append({"id": row["id"], "error": str(exc)})
        return {"releases": batch}
    release = fetch(db, "releases", args.id)
    profile = fetch(db, "profiles", release["app"])
    if command == "test-status":
        result = testing_section(db, profile["targets"][release["platform"]], release)
        if not result:
            raise ValueError("iOSのTestFlight案件またはGoogle Playのテストトラック案件を選んでください。")
        return result
    if command == "status":
        return snapshot(db, profile, release)[0]
    with lock(folder / (profile["id"] + ".lock")):
        release = fetch(db, "releases", args.id)
        profile = fetch(db, "profiles", release["app"])
        target = profile["targets"][release["platform"]]
        if command in ("test-link", "record-test"):
            scope = testing_scope(target, release)
            if scope is None:
                raise ValueError("iOSのTestFlight案件またはGoogle Playのテストトラック案件を選んでください。")
            if command == "test-link":
                url = None if args.clear else validate_invite(args.url, target)
                record(db, release, "test-invite", scope, "pass", {"message": "", "url": url})
            else:
                save_observation(db, release, scope, args, now())
            return {"testing": testing_status(db, target, release, scope)}
        if command in ("check", "prepare"):
            audit(db, profile, release)
            return snapshot(db, profile, release)[0]
        if command == "build":
            return build(db, profile, release)
        if command == "attest":
            return attest(db, profile, release, args)
        if command == "verify-publication":
            return verify_publication(db, profile, release)
        if command == "import-qa":
            return import_qa(db, profile, release, args.file)
        if command in ("qa", "capture"):
            argv = target.get(command + "_command")
            if not argv:
                raise ValueError("アプリの実行手順を登録してください。")
            state, keys = snapshot(db, profile, release)
            if command == "qa" and next(item["state"] for item in state["checks"] if item["name"] == "artifact") != "pass":
                raise ValueError("署名済み成果物を確認してからQAを実行してください。")
            result = run(expand(argv, release), profile["repo"], timeout=7200)
            if command == "qa":
                record(db, release, "qa", keys["qa"], "blocked" if result.returncode else "pass",
                       {"message": f"登録済みQAの終了コードは {result.returncode} です。"})
            elif result.returncode:
                raise ValueError(f"再撮影の終了コードは {result.returncode} です。")
            return snapshot(db, profile, release)[0]
        from stores import Apple, Google, play_listing
        state, keys = snapshot(db, profile, release)
        android = release["platform"] == "android"
        if command == "draft" and android and (not target.get("play_staging") or release["track"] == "production"):
            require_ready(state, {item["name"] for item in state["checks"]} - {"screenshots", "metadata"})
            return {"state": "console_required", "console": state["console"], "images": state["images"],
                    "metadata": release["metadata"], "bundle": target["bundle"], "track": release["track"],
                    "version": release["version"], "build": release["build"],
                    "listing": {locale: play_listing(release["metadata"], locale) for locale in target["locales"]}}
        adapter = Google(target) if android else Apple(target)
        if command == "upload-build":
            if release["platform"] == "android":
                raise ValueError("AndroidのAABはPlay Consoleまたは登録済みのテストトラック編集中に投入してください。")
            require_ready(state, set(item["name"] for item in state["checks"]) - {"artifact", "account"})
            path = Path(release["artifact"])
            expected = ".pkg" if release["platform"] == "macos" else ".ipa"
            if path.suffix != expected:
                raise ValueError("ストア提出用に書き出した成果物を選択してください。")
            remote = adapter.status(release)
            if remote["state"] != "PREPARE_FOR_SUBMISSION":
                raise ValueError("App Store Connectで対象版の編集可能な下書きを確認してください。")
            sha = next(item["sha256"] for item in state["checks"] if item["name"] == "artifact")
            known = history(db, release, "upload-build")
            if known and known[0]["digest"] != sha:
                raise ValueError("同じビルド番号で異なる成果物の投入履歴があります。ビルド番号を更新してください。")
            existing = adapter.available_build(release, remote["platform"])
            if existing:
                if not known:
                    raise ValueError("同じ版・ビルド番号がストアにあります。公式画面で成果物を照合してください。")
                if existing["attributes"].get("processingState") != "VALID":
                    return {"state": "processing", "build": release["build"]}
                if remote["build"] and remote["build"] != release["build"]:
                    raise ValueError("対象版に別のビルドが紐づいています。App Store Connectで確認してください。")
                if not remote["build"]:
                    adapter.bind_build(remote["version_id"], existing["id"])
                readback = adapter.status(release)
                if readback["build_id"] != existing["id"]:
                    raise ValueError("ストアのビルド紐づけを読み戻せません。")
                save_operation(db, release, "upload-build", sha, "verified", {})
                return {"state": "build_verified", "build": release["build"], "remote": readback}
            if known:
                raise ValueError("Transporterの送信結果が未確定です。App Store Connectのビルド処理を照会してください。")
            argv = adapter.transporter(path)
            if digest(path) != sha:
                raise ValueError("投入前に成果物が変更されました。")
            save_operation(db, release, "upload-build", sha, "running", {})
            result = run(argv, timeout=3600)
            if result.returncode:
                save_operation(db, release, "upload-build", sha, "unknown", {})
                raise ValueError("Transporterの送信結果を確認できません。対象版のストア状態を照会してください。")
            save_operation(db, release, "upload-build", sha, "requested", {})
            return {"state": "processing", "build": release["build"]}
        if command == "draft":
            required = {"screenshots", "metadata", "account"}
            require_ready(state, set(item["name"] for item in state["checks"]) - required)
            if release["platform"] == "android":
                require_ready(state, {"device", "declarations", "checklist", "storefront", "qa", "publication"})
                artifact = next(item["sha256"] for item in state["checks"] if item["name"] == "artifact")
                edit_key = fingerprint([target, keys, artifact])
                prior = history(db, release, "play-edit")
                if prior and (prior[0]["state"] == "opening" or prior[0]["digest"] != edit_key):
                    raise ValueError("既存の編集中のPlay変更を、公式画面と照合してください。")
                if prior:
                    edit = json.loads(prior[0]["data"])["edit"]
                else:
                    adapter.status(release)
                    save_operation(db, release, "play-edit", edit_key, "opening", {})
                    edit = adapter.new_edit()
                    if not isinstance(edit, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", edit):
                        raise ValueError("Playが返した編集IDを確認できません。")
                    save_operation(db, release, "play-edit", edit_key, "open", {"edit": edit})
                result = adapter.stage_edit(edit, release, state["images"], target["locales"], artifact)
                save_operation(db, release, "play-edit", edit_key, "validated", {"edit": edit})
                return result
            return adapter.sync_draft(release, state["images"])
        if command == "review-draft":
            if release["platform"] == "android":
                raise ValueError("Play ConsoleのPublishing overviewで変更の範囲を確認してください。")
            require_ready(state)
            remote = adapter.status(release)
            if remote.get("build") != release["build"] or remote["state"] not in {"PREPARE_FOR_SUBMISSION", "READY_FOR_REVIEW"}:
                raise ValueError("App Store Connectで対象版とビルドの下書きを確認してください。")
            return {"draft": adapter.review_draft(remote, create=True)}
        return dispatch_store(db, release, command, adapter, state, keys, getattr(args, "approve", None))


def main():
    args = parse()
    try:
        with database(args.workspace.resolve()) as db:
            result = execute(args.workspace.resolve(), db, args)
        print(encoded(result))
        return 0
    except Blocked as exc:
        print(encoded({"error": str(exc), "details": exc.result}))
    except subprocess.TimeoutExpired:
        print(encoded({"error": "登録された処理が時間内に完了しませんでした。実行状況を確認してください。"}))
    except (ValueError, KeyError, OSError, sqlite3.Error, TypeError, AttributeError) as exc:
        print(encoded({"error": str(exc) if isinstance(exc, ValueError) else
                       "設定・ファイル・ツールまたはストア応答を確認してください。"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
