"""Build a versioned review packet from the actual local release inputs."""
import json
from pathlib import Path

from intake import WORLDWIDE, WORLDWIDE_LABEL, summary_rows
from workflow import CHECK_LABELS, TEST_LABELS


LISTING_FIELDS = {
    "name": "アプリ名", "subtitle": "サブタイトル", "short_description": "短い説明",
    "description": "説明", "keywords": "キーワード", "promotional_text": "プロモーション文",
    "release_notes": "更新内容", "privacy_url": "プライバシーポリシー",
    "support_url": "サポートURL", "marketing_url": "マーケティングURL", "terms_url": "利用規約URL",
}
ALIASES = {"name": ("name", "title", "app_name"),
           "release_notes": ("release_notes", "whats_new"),
           "promotional_text": ("promotional_text", "promo_text"),
           "privacy_url": ("privacy_url", "privacy_policy_url"),
           "terms_url": ("terms_url", "eula_url", "license_agreement_url")}
STATE = {"pass": "確認済み", "blocked": "要対応", "pending": "未確認", "stale": "再確認",
         "not_checked": "検査未完了"}
SOURCE = {"store": "ストア管理画面", "device": "端末で直接確認", "tester": "テスター報告"}


def read_listing_file(path, root):
    try:
        if (path.is_symlink() or not path.resolve().is_relative_to(root)
                or not path.is_file() or path.stat().st_size > 64 * 1024):
            return ""
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError, RuntimeError):
        return ""


def public_listing(root, locales, platform):
    if not root or not Path(root).is_dir():
        return {}
    root = Path(root).resolve()
    description = ("full_description", "description") if platform == "android" else ("description",)
    aliases = {**ALIASES, "description": description}
    texts = {}
    for locale in locales:
        completed = {}
        for folder in (root, root / "default", root / locale):
            try:
                data = json.loads(read_listing_file(folder / "listing.json", root))
                data = data if isinstance(data, dict) else {}
            except ValueError:
                data = {}
            for field in LISTING_FIELDS:
                for name in aliases.get(field, (field,)):
                    text = read_listing_file(folder / f"{name}.txt", root) or data.get(name)
                    if isinstance(text, str) and text.strip():
                        completed[field] = text.strip()
                        break
        if completed:
            texts[locale] = completed
    return texts


def section(code, title, rows):
    return {"id": code, "title": title, "rows": [{"label": label, "value": value} for label, value in rows]}


def packet_sections(release, target, state, readbacks, action, metadata_digest, linked_testing=()):
    regions = "、".join(release.get("regions") or [])
    if release.get("region_scope") == WORLDWIDE:
        regions = f"{WORLDWIDE_LABEL}（{regions or 'ストアの配信対象地域を照合待ち'}）"
    fields = (
        ("アプリ", state["app"]), ("アプリID", target["bundle"]), ("ストア登録ID", target.get("store_id")),
        ("版・ビルド", f"{release['version']}（{release['build']}）"),
        ("対象", f"{state['store']} · {release['track']}"),
        ("予定地域", regions),
        ("価格", release.get("price")),
        ("支援購入", {True: "あり", False: "なし"}.get(release.get("purchases"))),
        ("公開方式", {"manual": "手動", "automatic": "自動"}.get(release.get("publication"))),
        ("初回公開", {True: "はい", False: "いいえ"}.get(release.get("first"))),
    )
    checks = {item["name"]: item for item in state["checks"]}
    artifact = checks["artifact"]
    listing = public_listing(release.get("metadata"), target["locales"], release["platform"])
    listing_rows = [(f"{locale} · {LISTING_FIELDS[key]}", value) for locale, fields in listing.items()
                    for key, value in fields.items()]
    image_rows = [(f"{image['locale']} · {image['slot']} · {image['order']}枚目",
                   f"{Path(image['path']).name}\nSHA-256 {image['sha256']}") for image in state["images"]]
    listed_checks = [(CHECK_LABELS.get(item["name"], item["name"]), STATE.get(item["state"], item["state"]))
                     for item in state["checks"] if item["name"] != "publication"]
    attestations = [(CHECK_LABELS[name], f"{STATE.get(checks[name]['state'], '未確認')}\n{checks[name]['message']}")
                    for name in ("declarations", "device", "storefront", "qa") if name in checks]
    for test in (state.get("testing"), *linked_testing):
        if not test:
            continue
        for name, observations in test["stages"].items():
            for item in observations:
                label = " · ".join(filter(None, (test["channel"], TEST_LABELS[name], item["device"], item["operation"])))
                observed = ""
                if name in ("installation", "qa"):
                    build = item.get("build") or "ビルド未確認"
                    build_source = SOURCE.get(item.get("build_source"), "確認元未記録")
                    observed = f" · 版 {item.get('version') or '未確認'} · ビルド {build}（{build_source}）"
                attestations.append((label, f"{STATE[item['outcome']]} · {SOURCE[item['source']]}{observed}"))
    for device, receipt in sorted(readbacks.get("devices", {}).items()):
        identity = f"版 {receipt.get('version') or '未確認'} · ビルド {receipt.get('build') or '未確認'}"
        attestations.append((f"接続端末の照合 · {device}", f"{receipt['source']} · {identity}\n{receipt['message']}"))
        attestations.append(("照会日時", receipt["observed_at"]))
    store = readbacks.get("production_store", {})
    if store.get("outcome") == "observed":
        remote_text = f"{store.get('state') or '未確認'} · ビルド {store.get('build', store.get('versionCode')) or '未確認'}"
    else:
        remote_text = "対象版を公式画面または読み取り照会で確認してください。"
    effect = "対象版の公開可否を本人が確認します。" if action == "publish" else "対象版の審査送信内容を本人が確認します。"
    if release["platform"] == "android" and action == "submit":
        effect += " Play Consoleで同時に送信される変更を確認してください。"
        if release.get("first"):
            effect += " 初回は審査後の公開条件まで確認してください。"
    report = state.get("intake")
    intake_sections = ([section("intake", "開始時の回答", summary_rows(report))]
                       if report and report["saved_at"] else [])
    return [
        section("release", "対象版と配信条件",
                [(name, value if isinstance(value, str) and value else "未設定") for name, value in fields]),
        *intake_sections,
        section("artifact", "成果物", [("署名と版の照合", STATE[artifact["state"]]),
                                       ("SHA-256", artifact.get("sha256") or "未確認")]),
        section("listing", "ローカルの掲載原稿", listing_rows + [("原稿の確認値", metadata_digest)]),
        section("images", "掲載画像と順序", image_rows or [("掲載画像", "未確認")]),
        section("evidence", "申告とQAの根拠", attestations),
        section("checks", "提出前の確認結果", listed_checks),
        section("store", "ストア照会と操作の範囲",
                [("ストアの記録", remote_text), ("照会日時", store.get("observed_at") or "未確認"), ("操作", effect)]),
    ]


def comparable_sections(sections):
    return [{**item, "rows": [row for row in item["rows"] if row["label"] != "照会日時"]} for item in sections]


def changes_since(previous, current):
    if previous is None:
        return []
    before = {item["id"]: item for item in comparable_sections(previous)}
    changes = []
    for item in comparable_sections(current):
        old = (before.get(item["id"]) or {}).get("rows", [])
        new = item["rows"]
        if old == new:
            continue
        stale_rows = [row for row in old if row not in new]
        updated_rows = [row for row in new if row not in old]
        labels = list(dict.fromkeys(row["label"] for row in [*stale_rows, *updated_rows]))
        changes.append({"id": item["id"], "title": item["title"], "labels": labels or ["順序"]})
    return changes
