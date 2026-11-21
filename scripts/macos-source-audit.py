#!/usr/bin/env python3
"""Bounded, offline macOS source checks. Never evaluates project/build scripts."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
from urllib.parse import urlsplit

EXCLUDED = {".git", ".build", ".local", "build", "DerivedData", "node_modules", "Pods",
            "vendor", "Carthage", ".venv", "venv", "Tests", "tests", "test", "__tests__"}
EXTENSIONS = {".swift", ".m", ".mm", ".h", ".plist", ".pbxproj", ".entitlements", ".xcprivacy", ".xcconfig", ".strings", ".xcstrings", ".stringsdict"}
PLATFORM_FILES = {"AndroidManifest.xml", "build.gradle", "build.gradle.kts", "pubspec.yaml", "package.json", "config.xml"}
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_FILES = 5000


def inventory(root):
    files, failures, total = [], [], 0
    def failed(_):
        failures.append("プロジェクト内の一部を読み取れません。アクセス権を確認してください。")
    for base, dirs, names in os.walk(root, followlinks=False, onerror=failed):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not d.endswith((".app", ".xcarchive"))
                         and not (Path(base) / d).is_symlink())
        for name in sorted(names):
            p = Path(base) / name
            if p.suffix not in EXTENSIONS and name not in PLATFORM_FILES:
                continue
            if p.is_symlink():
                failures.append("リンク経由のソースがあります。リンク先の対象も個別に監査してください。")
                continue
            if len(files) >= MAX_FILES or total >= MAX_TOTAL:
                return files, failures + ["静的監査の容量上限を超えました。対象アプリのフォルダを絞ってください。"]
            try:
                with p.open("rb") as stream:
                    data = stream.read(MAX_FILE + 1)
                if len(data) > MAX_FILE:
                    failures.append("容量上限を超えたファイルがあります。対象を絞ってください。")
                    continue
                total += len(data)
                if total > MAX_TOTAL:
                    return files, failures + ["静的監査の容量上限を超えました。対象を絞ってください。"]
                files.append((p.relative_to(root).as_posix(), data))
            except OSError:
                failed(None)
    return files, failures


def platform(files):
    mac, mobile = False, False
    for name, data in files:
        text = data.decode("utf-8", errors="replace")
        if name.endswith((".pbxproj", ".xcconfig")) or name.endswith("Package.swift"):
            mac |= bool(re.search(r"SDKROOT\s*=\s*[\"]?macosx|SUPPORTED_PLATFORMS\s*=\s*[\"]?macosx|\.macOS\s*\(", text))
            mobile |= bool(re.search(r"SDKROOT\s*=\s*[\"]?(?:iphone|appletv|watch|xros)|SUPPORTED_PLATFORMS\s*=[^;\n]*(?:iphoneos|iphonesimulator)", text))
            mobile |= bool(re.search(r"\.(?:iOS|tvOS|watchOS|visionOS)\s*\(", text))
        if Path(name).name in PLATFORM_FILES:
            mobile = True  # A mixed repository needs its native/mobile targets checked separately.
        if name.endswith("Info.plist"):
            try:
                info = plistlib.loads(data)
                mac |= isinstance(info, dict) and "LSMinimumSystemVersion" in info
                mobile |= isinstance(info, dict) and "LSRequiresIPhoneOS" in info and info["LSRequiresIPhoneOS"] is True
            except Exception:
                pass
    return "mixed" if mac and mobile else "macos" if mac else "other"


def release_text(text):
    # Preserve strings while removing comments; ignore explicit DEBUG-only branches.
    tokens = re.compile(r'"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*')
    text = tokens.sub(lambda m: "\n" * m[0].count("\n") if m[0].startswith(("//", "/*")) else m[0], text)
    stack, output = [], []
    for line in text.splitlines():
        directive = re.match(r"\s*#(if|elseif|else|endif)\b\s*(.*)", line)
        if directive:
            kind, condition = directive.groups()
            if kind == "if":
                stack.append(False if condition.strip() == "DEBUG" else True if condition.strip() == "!DEBUG" else None)
            elif kind == "else" and stack:
                stack[-1] = not stack[-1] if stack[-1] is not None else None
            elif kind == "elseif" and stack:
                stack[-1] = False if condition.strip() == "DEBUG" else None
            elif kind == "endif" and stack:
                stack.pop()
            output.append("")
        else:
            output.append(line if all(branch is not False for branch in stack) else "")
    return "\n".join(output)


def audit(root):
    root = Path(root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("not a directory")
    files, failures = inventory(root)
    kind = platform(files)
    findings = []
    def add(severity, rule, location, message, fix):
        findings.append(dict(severity=severity, rule=rule, field="source", location=location, message=message, fix=fix))
    texts = [(name, release_text(data.decode("utf-8", errors="replace")))
             for name, data in files if name.endswith((".swift", ".m", ".mm", ".h"))]
    for name, text in texts:
        for match in re.finditer(r'https?://[^\s"<>\\)]+', text):
            try:
                host = (urlsplit(match[0]).hostname or "").lower()
            except ValueError:
                continue
            if host in {"localhost", "127.0.0.1", "::1"} or host.startswith("staging.") or host.endswith(".ngrok.io"):
                add("critical", "APPLE-2.1-STAGING-BACKEND", f"{name}:{text[:match.start()].count(chr(10)) + 1}",
                    "本番対象のソースにローカル・検証用のHTTP接続先があります。", "配布対象で使う接続先か確認し、本番の接続先へ変更してください。")
                break
    joined = "\n".join(text for _, text in texts)
    policy_marker = re.compile(r"privacy[\s_-]*policy|privacy\.md|プライバシー[・\s]*ポリシー", re.I)
    policy_texts = [text for _, text in texts]
    for name, data in files:
        if name.endswith((".strings", ".xcstrings", ".stringsdict")):
            policy_texts.append(data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8", errors="replace"))
    policy_reference = False
    for text in policy_texts:
        for match in re.finditer(r'https?://[^\s"<>\\)]+', text):
            try:
                parsed = urlsplit(match[0])
                valid = bool(parsed.hostname and "." in parsed.hostname)
            except ValueError:
                valid = False
            context = text[max(0, match.start() - 300):match.end() + 300]
            if valid and (policy_marker.search(context) or re.search(r"privacy|policy", parsed.path, re.I)):
                policy_reference = True
                break
    if not policy_reference:
        add("high", "APPLE-5.1.1-MISSING-PRIVACY-POLICY", "", "ソースからプライバシーポリシーへの参照を確認できません。",
            "アプリ内のポリシー導線を確認してください。動的に生成している場合も、実機で開けることを記録します。")
    for name, data in files:
        if name.endswith("Info.plist"):
            try:
                info = plistlib.loads(data)
                if not isinstance(info, dict):
                    raise ValueError()
                for key, value in info.items():
                    if key.startswith("NS") and key.endswith("UsageDescription") and (not isinstance(value, str) or not value.strip()):
                        add("high", "APPLE-5.1.1-VAGUE-PURPOSE-STRING", name, f"{key} の用途説明が空です。", "必要な機能と取得するデータを具体的に記入してください。")
                if info.get("NSAppTransportSecurity", {}).get("NSAllowsArbitraryLoads") is True:
                    add("high", "APPLE-ATS-DISABLED", name, "通信の保護を広く無効にする設定があります。", "必要な接続先だけの例外に絞り、HTTPS通信を確認してください。")
            except Exception:
                failures.append("Info.plistを読み取れません。形式を確認してください。")
    manifests = [(name, data) for name, data in files if name.endswith(".xcprivacy")]
    if not manifests and re.search(r"UserDefaults\s*\.\s*standard|attributesOfItem|volumeAvailableCapacity", joined):
        add("high", "APPLE-PRIVACY-MANIFEST-MISSING", "", "理由の申告が必要なAPIの参照がありますが、Privacy Manifestが見つかりません。",
            "対象ターゲットとSDKが使うAPIを調べ、必要な理由をPrivacyInfo.xcprivacyに申告してください。")
    if manifests:
        spec = importlib.util.spec_from_file_location("mac_manifest_validator", Path(__file__).with_name("validate-privacy-manifest.py"))
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        for name, data in manifests:
            rows, unreadable = [], False
            try:
                value = plistlib.loads(data)
                if not isinstance(value, dict):
                    raise ValueError()
                validator._validate(value, name, rows)
            except Exception:
                rows = [("critical", "APPLE-MANIFEST-UNREADABLE", "Privacy Manifestを読み取れません。")]
                unreadable = True
            if unreadable:
                failures.append("Privacy Manifestを読み取れません。")
            for severity, rule, detail in rows:
                add(severity, rule, name, "Privacy Manifestの形式・必須値を確認してください。", "詳細の原文を確認し、対象ターゲットの宣言を修正してください。")
                findings[-1]["detail"] = detail[:1000]
    digest = hashlib.sha256()
    for name, data in sorted(files):
        digest.update(name.encode()); digest.update(b"\0"); digest.update(hashlib.sha256(data).digest())
    complete = kind == "macos" and not failures
    if kind != "macos":
        failures.append("Mac専用の対象を特定できません。macOSのSDK設定があるアプリのフォルダを指定してください。")
    return dict(findings=findings, fingerprint=digest.hexdigest(), complete=complete,
                platform=kind, fileCount=len(files), issues=list(dict.fromkeys(failures)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--guard-lines", action="store_true")
    args = parser.parse_args()
    try:
        if args.probe:
            files, _ = inventory(Path(args.project).expanduser().resolve(strict=True))
            print(platform(files)); return 0
        result = audit(args.project)
        if args.guard_lines:
            print("Platforms. macOS=1 iOS=0 Android=0 Web=0")
            for row in result["findings"]:
                values = [row[k] for k in ("severity", "rule", "message", "fix", "location")]
                print("\t".join(v.replace("\t", " ").replace("\n", " ") or "-" for v in values))
            for problem in result["issues"]:
                print("INCOMPLETE\t" + problem)
        else:
            print(json.dumps(result, ensure_ascii=False))
        return 1 if not result["complete"] else 2 if any(f["severity"] == "critical" for f in result["findings"]) else 0
    except Exception:
        print("Macソース監査を実行できません。対象と同梱ツールを確認してください。", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
