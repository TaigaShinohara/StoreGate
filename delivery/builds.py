"""Run registered local build commands and inspect their actual output."""
import fcntl
import hashlib
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple, Optional

from jdk import resolve_jdk


class Artifact(NamedTuple):
    bundle: str
    version: str
    build: str
    sdk: str
    sha256: str
    manifests: dict
    jdk: Optional[dict] = None


ROOT = Path(__file__).resolve().parents[1]
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def run(argv, cwd=None, timeout=120, env=None):
    return subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, check=False, env=env)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def tree_digest(path):
    result = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        name = item.relative_to(path).as_posix()
        if item.is_symlink():
            value = os.readlink(item)
        elif item.is_file():
            value = digest(item)
        else:
            continue
        result.update(repr((name, value)).encode())
    return result.hexdigest()


def git_source(repo):
    tree = run(["git", "rev-parse", "HEAD^{tree}"], repo)
    changes = run(["git", "--no-optional-locks", "status", "--porcelain=v1", "-z",
                   "--no-renames", "--untracked-files=all"], repo)
    if tree.returncode or changes.returncode:
        raise ValueError("Git管理下のソースを確認してください。")
    result = hashlib.sha256(tree.stdout)
    for entry in sorted(set(changes.stdout.split(b"\0")) - {b""}):
        path = repo / os.fsdecode(entry[3:])
        if path.is_symlink():
            value = os.readlink(path)
        elif path.is_file():
            value = digest(path)
        elif path.is_dir():
            value = tree_digest(path)
        else:
            value = "missing"
        result.update(repr((entry, value)).encode())
    return result.hexdigest()


@contextmanager
def lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("同じ操作が実行中です。状態を再表示してください。") from None
        yield
    finally:
        os.close(fd)


@contextmanager
def build_host(repo):
    scratch = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
    minimum_free = 10 * 1024**3
    if shutil.disk_usage(scratch).free < minimum_free:
        raise ValueError("ビルド用の一時ディレクトリがあるボリュームの空き容量が10 GiBを下回っています。")
    path = scratch / f"storegate-build-{os.getuid()}.lock"
    with lock(path):
        if shutil.disk_usage(repo).free < minimum_free:
            raise ValueError("ビルド先の空き容量が10 GiBを下回っています。")
        if shutil.which("sysctl") and os.uname().sysname == "Darwin":
            pressure = run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"])
            if pressure.returncode or pressure.stdout.strip() != b"1":
                raise ValueError("Macのメモリプレッシャーを確認してください。")
        processes = run(["ps", "-axo", "comm=,args="])
        if processes.returncode or any(
            re.search(rb"(?:^|/)(xcodebuild|swift-frontend|swift-build|ninja)(?:\s|$)|GradleWrapperMain|flutter_tools\.snapshot.*\bbuild\b", line)
            for line in processes.stdout.splitlines()
            if b"GradleDaemon" not in line
        ):
            raise ValueError("実行中のビルドを確認し、完了後に再実行してください。")
        yield


def checked(argv, cwd=None, timeout=120):
    result = run(argv, cwd, timeout)
    if result.returncode:
        raise ValueError(f"{Path(argv[0]).name} が終了コード {result.returncode} を返しました。")
    return result.stdout + result.stderr


def build_commands(profile, target):
    repo = Path(profile["repo"])
    kind = target["build_method"]
    if kind == "flutter":
        if not target.get("build_command"):
            raise ValueError("アプリで承認済みのFlutterビルド手順を登録してください。")
        return [target["build_command"]]
    if kind == "xcode":
        return [
            ["xcodebuild", "-project", target["project"], "-scheme", target["scheme"],
             "-configuration", "Release", "-destination", "generic/platform=" + ("macOS" if target["platform"] == "macos" else "iOS"),
             "-archivePath", target["archive"], "archive"],
            ["xcodebuild", "-exportArchive", "-archivePath", target["archive"],
             "-exportOptionsPlist", target["export_options"], "-exportPath", target["export_path"]],
        ]
    if kind == "gradle":
        return [[str(repo / "gradlew"), target.get("gradle_task", ":app:bundleRelease")]]
    raise ValueError("CI成果物のパスを登録してください。")


def android_identity(data):
    try:
        manifest = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("署名済みAABのベースマニフェストを解釈できません。") from exc
    application = manifest.find("application")
    sdk = manifest.find("uses-sdk")
    level = sdk.get(ANDROID_NS + "targetSdkVersion", "") if sdk is not None else ""
    if manifest.tag != "manifest" or application is None or not re.fullmatch(r"[0-9]{1,3}", level):
        raise ValueError("AABのアプリ設定と対象SDKを読み取れません。")
    if application.get(ANDROID_NS + "debuggable", "false") != "false":
        raise ValueError("AABでデバッグが有効です。配布用ビルドを選択してください。")
    if int(level) < 36:
        raise ValueError(f"AABの対象SDKは {level} です。Google Play向け電話アプリはAPI 36以上が必要です。")
    return manifest.get("package"), manifest.get(ANDROID_NS + "versionName"), manifest.get(ANDROID_NS + "versionCode"), level


def manifest_digest(data):
    return hashlib.sha256(plistlib.dumps(data, sort_keys=True)).hexdigest()


def packaged_privacy_manifests(app):
    paths = sorted(app.rglob("PrivacyInfo.xcprivacy"))
    if not paths:
        return {}
    root = app.resolve()
    if any(not path.is_file() or not path.resolve().is_relative_to(root) for path in paths):
        raise ValueError("アプリ外を参照するか読めないPrivacy Manifestがあります。")
    result = run([sys.executable, str(ROOT / "scripts/validate-privacy-manifest.py"), *map(str, paths)])
    if result.returncode:
        failures = result.stdout.decode(errors="replace").replace(str(app) + "/", "").strip()
        raise ValueError("成果物に含まれるPrivacy Manifestを確認してください。\n" + failures)
    return {path.relative_to(app).as_posix(): manifest_digest(plistlib.loads(path.read_bytes())) for path in paths}


def verified_digest(path, before):
    after = digest(path)
    if after != before:
        raise ValueError("検査中に成果物が変更されました。対象版を照合し直してください。")
    return after


def jdk_checked(jdk, tool, args):
    try:
        return checked([jdk.tool(tool), *args])
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        detail = str(exc) if isinstance(exc, ValueError) else f"{tool} を実行できません。"
        raise ValueError(f"{detail}\n使用したJDKは {jdk.home}（Java {jdk.version}、{jdk.architecture}）です。"
                         "ツールと署名の設定を確認してください。別のJDKを使う場合は java_home を指定してください。") from None


def file_identity(path, target):
    if not path.exists():
        raise ValueError("署名済み成果物を登録するかビルドしてください。")
    if target["platform"] == "android":
        if path.suffix != ".aab":
            raise ValueError("Release AABを選択してください。")
        before = digest(path)
        expected = target.get("signer_sha256", "").replace(":", "").lower()
        with zipfile.ZipFile(path) as bundle:
            if "base/manifest/AndroidManifest.xml" not in bundle.namelist():
                raise ValueError("AABのベースマニフェストがありません。")
            if not any(name.startswith(("base/dex/", "base/lib/")) for name in bundle.namelist()):
                raise ValueError("AABのベースモジュールに実行コードがありません。")
            required = target.get("required_entries", [])
            if any(entry not in bundle.namelist() for entry in required):
                raise ValueError("AABに必要な同梱ファイルがありません。")
        jdk = resolve_jdk(target)
        verified = jdk_checked(jdk, "jarsigner", ["-J-Duser.language=en", "-verify", str(path)]).lower()
        if b"jar verified." not in verified or b"unsigned entries" in verified:
            raise ValueError("AABの署名をすべてのエントリで確認できません。")
        cert = jdk_checked(jdk, "keytool", ["-J-Duser.language=en", "-printcert", "-jarfile", str(path)])
        fingerprints = re.findall(rb"SHA256:\s*([A-Fa-f0-9:]+)", cert)
        actual = {value.decode().replace(":", "").lower() for value in fingerprints}
        if not expected or actual != {expected}:
            raise ValueError("AABのアップロード証明書が登録済みの指紋と一致しません。")
        tool = target.get("bundletool")
        if not tool:
            raise ValueError("マニフェスト検査用のbundletool JARを登録してください。")
        manifest = jdk_checked(jdk, "java", ["-jar", tool, "dump", "manifest", "--bundle=" + str(path), "--module=base"])
        return Artifact(*android_identity(manifest), verified_digest(path, before), {}, jdk.details())

    if path.is_file() and path.suffix in (".ipa", ".pkg"):
        before = digest(path)
        # An archive alone does not prove the identity of a separately supplied export.
        with tempfile.TemporaryDirectory(prefix="storegate-export-") as temporary:
            folder = Path(temporary)
            if path.suffix == ".ipa" and target["platform"] == "ios":
                checked(["ditto", "-x", "-k", str(path), str(folder)])
                apps = list((folder / "Payload").glob("*.app"))
            elif path.suffix == ".pkg" and target["platform"] == "macos":
                installer = checked(["pkgutil", "--check-signature", str(path)]).decode(errors="replace")
                team = target.get("team_id", "")
                if not team or not re.search(r"(?:3rd Party Mac Developer Installer|Mac Installer Distribution):[^\n]*\(" + re.escape(team) + r"\)", installer):
                    raise ValueError("PKGの配信チームを照合できません。")
                expanded = folder / "package"
                checked(["pkgutil", "--expand-full", str(path), str(expanded)])
                apps = [app for app in expanded.rglob("*.app") if not any(parent.suffix == ".app" for parent in app.parents)]
            else:
                raise ValueError("プラットフォームに対応する提出形式を選択してください。")
            if len(apps) != 1:
                raise ValueError("書き出し内のアプリを一意に特定できません。")
            artifact = apple_app(apps[0], target, before)
            verified_digest(path, before)
            return artifact
    if path.suffix != ".xcarchive":
        raise ValueError("Xcodeアーカイブかストア用の書き出しを選択してください。")
    apps = list((path / "Products/Applications").glob("*.app"))
    if len(apps) != 1:
        raise ValueError("アーカイブ内のアプリを一意に特定できません。")
    return apple_app(apps[0], target, tree_digest(path))


def apple_app(app, target, sha256):
    plist = app / ("Contents/Info.plist" if target["platform"] == "macos" else "Info.plist")
    values = plistlib.loads(plist.read_bytes())
    checked(["codesign", "--verify", "--deep", "--strict", str(app)])
    signature = checked(["codesign", "-dv", "--verbose=4", str(app)]).decode(errors="replace")
    line = signature.splitlines()
    if (not target.get("team_id") or f"TeamIdentifier={target['team_id']}" not in line
            or not any(item.startswith(("Authority=Apple Distribution:", "Authority=iPhone Distribution:",
                                        "Authority=3rd Party Mac Developer Application:")) for item in line)):
        raise ValueError("Appleの配布用署名とチームを照合できません。")
    if target["platform"] == "macos":
        entitlements = plistlib.loads(run(["codesign", "-d", "--entitlements", "-", str(app)]).stdout)
        if entitlements.get("com.apple.security.app-sandbox") is not True:
            raise ValueError("署名済みApp Sandboxの設定を確認してください。")
    sdk = values.get("DTSDKName")
    expected_sdk = "macosx" if target["platform"] == "macos" else "iphoneos"
    if not isinstance(sdk, str) or not sdk.startswith(expected_sdk):
        raise ValueError("対象プラットフォームのSDKを成果物から確認できません。")
    if any(not (app / entry).resolve().is_relative_to(app.resolve()) or not (app / entry).is_file()
           for entry in target.get("required_entries", [])):
        raise ValueError("アプリに必要な同梱ファイルがありません。")
    return Artifact(values.get("CFBundleIdentifier"), values.get("CFBundleShortVersionString"),
                    values.get("CFBundleVersion"), sdk, sha256, packaged_privacy_manifests(app))
