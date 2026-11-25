"""Local release preparation checks and an optional read-only store probe."""
import json
import os
import plistlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
from xml.parsers.expat import ExpatError

from builds import BUILD_FREE_BYTES, disk_for, run, temporary_path
from intake import DEVICE_KEYS
from jdk import resolve_jdk
from stores import Apple, Google, StoreError, REVIEW_CONTACT_ENV


def row(name, title, state, message):
    return {"name": name, "title": title, "state": state, "message": message}


def build_inputs(target, release, repo):
    method = target["build_method"]
    fields = []
    if not (release.get("artifact") or release.get("artifact_pattern")):
        fields.append("署名済み成果物の受け取り先（artifact または artifact_pattern）")
    if method == "flutter" and not target.get("build_command"):
        fields.append("承認済みのFlutterビルド手順（build_command）")
    wrapper = Path(repo) / "gradlew"
    if method == "gradle" and (not wrapper.is_file() or not os.access(wrapper, os.X_OK)):
        fields.append("実行できるGradle Wrapper（gradlew）")
    if method == "xcode":
        descriptions = {"project": "Xcodeプロジェクト", "scheme": "共有スキーム", "archive": "Archive出力先",
                        "export_options": "ExportOptions.plist", "export_path": "書き出し先"}
        fields.extend(title for key, title in descriptions.items() if not target.get(key))
        if target.get("project") and not Path(target["project"]).is_dir():
            fields.append("存在するXcodeプロジェクト")
        if target.get("export_options"):
            try:
                options = plistlib.loads(Path(target["export_options"]).read_bytes())
                if not isinstance(options, dict):
                    raise ValueError()
                if options.get("teamID") and target.get("team_id") and options["teamID"] != target["team_id"]:
                    fields.append("ExportOptions.plistと配布チームの一致")
                if (target["platform"] == "ios" and options.get("signingStyle") == "manual"
                        and not options.get("provisioningProfiles", {}).get(target["bundle"])):
                    fields.append("対象Bundle ID用の配布プロビジョニングプロファイル")
            except (OSError, ValueError, TypeError, AttributeError, plistlib.InvalidFileException, ExpatError):
                fields.append("読み取れるExportOptions.plist")
        if not shutil.which("xcodebuild"):
            fields.append("実行できるxcodebuild")
    if fields:
        return row("build_inputs", "ビルドと成果物", "blocked", "確認する項目\n" + "\n".join(fields))
    message = "CIの署名済み成果物を受け取る設定です。" if method == "ci" else "ビルド入力と成果物の受け取り先を確認しました。"
    return row("build_inputs", "ビルドと成果物", "pass", message)


def signing_inputs(target):
    if target["platform"] == "android":
        fingerprint = target.get("signer_sha256") or ""
        errors = []
        if not re.fullmatch(r"[A-Fa-f0-9]{64}", fingerprint.replace(":", "")):
            errors.append("Play Consoleのアップロード証明書のSHA-256を指定してください（signer_sha256）。")
        if not target.get("bundletool") or not Path(target["bundletool"]).is_file():
            errors.append("ローカルのbundletool JARを指定してください（bundletool）。")
        if errors:
            return row("signing_inputs", "アップロード署名", "blocked", "\n".join(errors))
        return row("signing_inputs", "アップロード署名", "pass",
                   "公開証明書の指紋とbundletoolを設定済み。AABの署名と指紋の一致は成果物で照合します。")
    if not target.get("team_id"):
        return row("signing_inputs", "配布署名", "blocked", "Appleの配布チームを指定してください（team_id）。")
    return row("signing_inputs", "配布署名", "pass",
               "配布チームを設定済み。配布証明書と対象アプリのプロファイルを確認し、署名は成果物で照合します。")


def local_checks(target, release, repo):
    android = target["platform"] == "android"
    store_id = target.get("store_id") or ""
    has_app = store_id == target["bundle"] if android else bool(re.fullmatch(r"[0-9]+", store_id))
    if has_app and target.get("provider"):
        registration = row("registration", "ストアのアプリ登録", "pending",
                           "管理画面で対象アプリを確認するか、APIで読み取り照会してください。")
    else:
        action = ("Play Consoleで対象アプリIDとアカウントを確認してください。" if android else
                  "App Store Connectで対象Bundle IDのアプリレコードと配信プロバイダを確認してください。")
        registration = row("registration", "ストアのアプリ登録", "blocked", action)
    checks = [registration, build_inputs(target, release, repo), signing_inputs(target)]
    if android:
        try:
            jdk = resolve_jdk(target)
            result = row("jdk", "AndroidのJDK", "pass",
                         f"{jdk.home}\nJava {jdk.version} · {jdk.architecture} · 選択元 {jdk.source}")
            if jdk.fallback:
                result["message"] += "\n" + jdk.fallback
        except ValueError as exc:
            result = row("jdk", "AndroidのJDK", "blocked", str(exc))
        checks.append(result)
    return checks


def auth_checks(target, release):
    reference = target.get("auth_env")
    supplied = os.environ.get(reference, "").strip() if reference else ""
    authentication = row("authentication", "ストアAPIの認証参照", "pending",
                         "既存の管理画面で確認できます。APIを使う場合は auth_env を設定してください。")
    authentication["outcome"] = "reference_missing"
    if reference:
        authentication.update(outcome="credential_unavailable", message=
                              "認証参照は設定済みです。案件用認証ランナーを使うか、既存の管理画面で照合してください。")
        if supplied and not supplied.startswith("op://"):
            authentication.update(outcome="credential_present", message=
                                  "実行環境に認証情報があります。対象アプリを照会して読み取り権限を確認してください。")
    result = [authentication]
    if release["platform"] != "android":
        transporter = all(target.get(key) for key in ("provider", "issuer_id", "transporter_key_id"))
        result.append({**row("transporter", "Transporterの参照", "pending",
                             "API入稿用の参照は設定済みです。初回利用許諾・サインインは公式画面で確認してください。"
                             if transporter else "API入稿用の参照が不足しています。Transporterの利用許諾、サインインと配信プロバイダを確認してください。"),
                       "outcome": "reference_present" if transporter else "reference_missing"})
        if release["track"] == "production":
            present = sum(bool(os.environ.get(key, "").strip()) for key in REVIEW_CONTACT_ENV.values())
            result.append({**row("review_contact", "案件専用の審査連絡先", "pending",
                                 f"既存の案件専用 .env 等から渡された設定は {present}/4 項目です。公式画面で保存結果を照合してください。"),
                           "outcome": "reference_present" if present == 4 else "credential_unavailable"})
    return result


def build_outputs(target, release):
    outputs = [(title, target[key]) for key, title in (("archive", "Xcode Archive出力先"), ("export_path", "Xcode書き出し先"))
               if target.get(key)]
    path = release.get("artifact_pattern") or release.get("artifact")
    if path:
        folder = Path(path).parent
        for parent in reversed((folder, *folder.parents)):
            if any(char in parent.name for char in "*?["):
                folder = parent.parent
                break
        outputs.append(("成果物の出力先", folder))
    return outputs


def capacity_checks(workspace, repo, target, release):
    local_build = target["build_method"] != "ci"
    areas = [("案件の保存先", workspace, 256 * 1024**2, "workspace_write"),
             ("一時領域", temporary_path(), BUILD_FREE_BYTES if local_build else 256 * 1024**2,
              "build" if local_build else "temporary_write")]
    if local_build:
        areas += [("ソースの作業領域", repo, BUILD_FREE_BYTES, "build"),
                  *((title, path, BUILD_FREE_BYTES, "build") for title, path in build_outputs(target, release))]
    checks = []
    for number, (title, path, minimum, scope) in enumerate(areas):
        try:
            folder, free = disk_for(path)
            state = "pass" if free >= minimum else "blocked"
            message = f"{path}（測定先 {folder}）\n空き {free / 1024**3:.2f} GiB"
            if state == "blocked":
                message += f"。空き容量を {minimum / 1024**3:g} GiB 以上にするか、出力先を変更してください。"
        except (OSError, RuntimeError):
            state = "not_checked"
            message = f"{path} の空き容量を取得できません。パス・マウント・アクセス権を確認して再照会してください。"
        checks.append({**row(f"capacity_{number}", title, state, message), "scope": scope})
    return checks


def device_inventory(target):
    platform = target["platform"]
    if platform == "macos":
        local = sys.platform == "darwin"
        return {**row("connected_devices", "接続端末", "pass" if local else "not_checked",
                      "このMacで操作を確認できます。署名済み成果物や別のMacでの動作は別途照合してください。"
                      if local else "Macで事前確認を開き直し、対象版で操作を確認してください。"),
                "outcome": "connected" if local else "unconfirmed"}
    android = platform == "android"
    argv = ["xcrun", "devicectl", "list", "devices", "--timeout", "5", "--json-output", "-"]
    try:
        # Query an already running local adb server so opening a case does not start one.
        if android:
            from adb_inventory import connected_devices
            connected, unreadable, virtual = connected_devices()
        else:
            result = run(argv, timeout=7)
            if result.returncode:
                raise ValueError()
            entries = json.loads(result.stdout)["result"]["devices"]
            if not isinstance(entries, list):
                raise ValueError()
            states = [item.get("connectionProperties", {}).get("tunnelState") for item in entries]
            connected = states.count("connected")
            unreadable = sum(state not in ("connected", "disconnected") for state in states)
            virtual = 0
    except (KeyError, TypeError, ValueError, AttributeError, OSError, subprocess.TimeoutExpired):
        return {**row("connected_devices", "接続端末", "not_checked",
                      "端末を照会できません。端末ツール、デバッグ許可、接続を確認してから再照会してください。"),
                "outcome": "unconfirmed"}
    if connected:
        state, outcome = "pass", "connected"
        message = f"照会できる実機 {connected} 台。対象版のインストールと操作は端末ごとに照合してください。"
    elif unreadable:
        state, outcome = "blocked", "unreadable"
        message = "接続先を検出しました。端末を解除し、信頼またはデバッグを許可してから再照会してください。"
    else:
        state, outcome = "pending", "disconnected"
        message = "読み取れる実機は現在接続されていません。実機で確認する場合は接続してください。"
    if unreadable:
        message += f" 読み取り不可の端末 {unreadable} 台。"
    if virtual:
        message += f" 接続中のエミュレータ {virtual} 台。"
    message += " シミュレータ・エミュレータでは画面と基本操作を確認できます。ストアからの入手と実機固有の操作は実機で確認します。"
    return {**row("connected_devices", "接続端末", state, message), "outcome": outcome}


def device_choices(report):
    rows = []
    for field in report["fields"]:
        if field["key"] not in DEVICE_KEYS:
            continue
        outcome = field["value"] or "unconfirmed"
        text = {"available": "使える実機あり。接続またはテスターの端末結果を照合してください。",
                "none": "端末なし。別の実機を確保するまで、その機種の実機QAは待機。画面・基本操作はシミュレータで確認できます。",
                "simulator": "シミュレータのみ。画面・基本操作を確認し、ストアからの入手と実機固有の操作は実機の確保後に確認します。",
                "na": "今回の端末計画では該当なし。登録済みの必須QAは引き続き確認します。"}
        rows.append({**row(field["key"], field["title"], "pending",
                           text.get(outcome, "使える実機は未確認です。開始時の確認リストで記録してください。")),
                     "outcome": outcome})
    return rows


def probe_store(target, release):
    try:
        if target["platform"] == "android":
            Google(target).status(release)
            message = "Play APIで対象アプリIDとトラックを照会できました。"
        else:
            remote = Apple(target).status(release)
            if remote["state"] == "registration_required":
                return row("store_probe", "APIの読み取り結果", "pending",
                           "App Store ConnectのアプリとBundle IDを確認しました。対象版のレコードを管理画面で確認してください。")
            message = "App Store Connectのアプリ、Bundle ID、対象版を照会できました。"
        return {**row("store_probe", "APIの読み取り結果", "pass", message), "outcome": "verified"}
    except StoreError as exc:
        messages = {
            401: "認証を確認できません。認証ランナーで認証を更新してください。",
            403: "対象アプリの読み取り権限、契約やアカウント状態を管理画面で確認してください。",
            404: "対象ID・版・トラックと、アプリを参照できるアカウントかを管理画面で確認してください。",
        }
        outcome = {401: "sign_in_required", 403: "access_required", 404: "target_unconfirmed"}.get(exc.status, "unconfirmed")
        return {**row("store_probe", "APIの読み取り結果", "blocked", messages.get(exc.status, str(exc))),
                "outcome": outcome}
    except (KeyError, TypeError, ValueError, AttributeError, OSError, subprocess.TimeoutExpired):
        return row("store_probe", "APIの読み取り結果", "blocked", "ストア応答を確認できません。公式画面で対象を照合してください。")
