"""Read store and device facts for one registered release."""
import json
import re
import subprocess

from builds import run
from stores import Apple, Google, StoreError


def store_readback(target, release, phase):
    source = "play_api" if target["platform"] == "android" else "app_store_connect_api"
    try:
        if target["platform"] == "android":
            remote = Google(target).status(release)
        else:
            adapter = Apple(target)
            remote = adapter.test_status(release) if phase == "test" else adapter.status(release)
        return {"source": source, "outcome": "observed", **remote}
    except StoreError as exc:
        return {"source": source, "outcome": "unconfirmed", "http_status": exc.status, "message": str(exc)}
    except (KeyError, TypeError, ValueError, AttributeError, OSError, subprocess.TimeoutExpired):
        return {"source": source, "outcome": "unconfirmed", "message": "ストアの応答を読み取れません。公式画面で対象版を照合してください。"}


def ios_app(output, bundle):
    apps = json.loads(output)["result"]["apps"]
    matching = [app for app in apps if app.get("bundleIdentifier") == bundle]
    if not matching:
        return None
    if len(matching) != 1:
        raise ValueError("対象アプリのインストールを一意に照合できません。")
    app = matching[0]
    # CoreDevice exposes both marketing version and bundle version.
    version, build = app["version"], app["bundleVersion"]
    if type(version) not in (str, int) or type(build) not in (str, int):
        raise ValueError("端末の版またはビルド番号を取得できません。")
    return str(version), str(build)


def android_app(output, bundle):
    text = output.decode("utf-8", errors="replace")
    if "Unable to find package:" in text or re.search(r"^Package \S+ was not found", text, re.MULTILINE):
        return None
    packages = re.split(r"(?m)^[ \t]*Package \[([^\]]+)\][^\n]*\n", text)
    matching = [body for name, body in zip(packages[1::2], packages[2::2]) if name == bundle]
    if len(matching) != 1:
        raise ValueError("対象Androidアプリを一意に照合できません。")
    version = re.search(r"(?m)^[ \t]+versionName=([^\r\n]+)$", matching[0])
    build = (re.search(r"(?m)^[ \t]+longVersionCode=(\d+)(?:[ \t]|$)", matching[0]) or
             re.search(r"(?m)^[ \t]+versionCode=(\d+)(?:[ \t]|$)", matching[0]))
    if not version or not build:
        raise ValueError("対象Androidアプリの版とビルド番号を取得できません。")
    return version[1].strip(), build[1]


def device_readback(target, release, identifier):
    bundle = target["bundle"]
    android = target["platform"] == "android"
    source = "adb" if android else "devicectl"
    argv = (["adb", "-s", identifier, "shell", "dumpsys", "package", bundle] if android else
            ["xcrun", "devicectl", "device", "info", "apps", "--device", identifier,
             "--bundle-id", bundle, "--include-all-apps", "--timeout", "20", "--json-output", "-"])
    try:
        result = run(argv, timeout=25)
        if result.returncode:
            hint = ("USBデバッグの許可とadbの接続" if android else "Xcode、接続と端末の信頼状態")
            raise ValueError(f"{source} で端末を読み取れません。{hint}を確認してください。")
        inspected = (android_app if android else ios_app)(result.stdout, bundle)
        if inspected is None:
            return {"source": source, "outcome": "not_installed", "message": "指定端末で対象アプリを確認できません。"}
        version, build = inspected
        if (not version or len(version) > 128 or any(not char.isprintable() for char in version)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", build)):
            raise ValueError("端末の版またはビルド番号を取得できません。")
        same = (version, build) == (release["version"], release["build"])
        message = "指定端末で対象版とビルドを照合しました。" if same else "端末に入っている版またはビルドが対象と異なります。"
        return {"source": source, "outcome": "matches" if same else "mismatch", "message": message,
                "version": version, "build": build}
    except (KeyError, TypeError, ValueError, AttributeError, OSError, subprocess.TimeoutExpired) as exc:
        allowed = str(exc) if type(exc) is ValueError else f"{source} を実行できません。ツールと端末の接続を確認してください。"
        return {"source": source, "outcome": "unconfirmed", "message": allowed}
