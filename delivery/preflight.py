"""Local release preparation checks and an optional read-only store probe."""
import os
import plistlib
from pathlib import Path
import re
import shutil
from xml.parsers.expat import ExpatError

from jdk import resolve_jdk
from stores import Apple, Google, StoreError


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
    auth_env = target.get("auth_env")
    if has_app and target.get("provider"):
        registration = row("registration", "ストアのアプリ登録", "pending",
                           "管理画面で対象アプリを確認するか、APIで読み取り照会してください。")
    else:
        action = ("Play Consoleで対象アプリIDとアカウントを確認してください。" if android else
                  "App Store Connectで対象Bundle IDのアプリレコードと配信プロバイダを確認してください。")
        registration = row("registration", "ストアのアプリ登録", "blocked", action)
    if not auth_env:
        auth = row("authentication", "接続方法", "pending",
                   "既存の管理画面で確認できます。APIを使う場合は認証の環境変数名（auth_env）を設定してください。")
    elif not os.environ.get(auth_env):
        auth = row("authentication", "接続方法", "pending",
                   f"認証ランナーから {auth_env} を渡すか、既存の管理画面で確認してください。")
    else:
        auth = row("authentication", "接続方法", "pending", "認証情報を受領しています。APIの読み取り結果で照合してください。")
    checks = [registration, auth, build_inputs(target, release, repo), signing_inputs(target)]
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
        return row("store_probe", "APIの読み取り結果", "pass", message)
    except StoreError as exc:
        messages = {
            401: "認証を確認できません。認証ランナーで認証を更新してください。",
            403: "対象アプリの読み取り権限、契約やアカウント状態を管理画面で確認してください。",
            404: "対象ID・版・トラックと、アプリを参照できるアカウントかを管理画面で確認してください。",
        }
        return row("store_probe", "APIの読み取り結果", "blocked", messages.get(exc.status, str(exc)))
    except (KeyError, TypeError, ValueError):
        return row("store_probe", "APIの読み取り結果", "blocked", "ストア応答を確認できません。公式画面で対象を照合してください。")
