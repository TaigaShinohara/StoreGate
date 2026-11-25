"""Questions and answers stored with a registered release."""
import hashlib
import json
import re

PENDING = "pending"
WORLDWIDE = "worldwide"
WORLDWIDE_LABEL = "全世界"
BASE = ("first", "price", "regions", "purchases", "publication")
DECLARATIONS = ("ads", "data", "age", "rights", "encryption", "app_access", "support", "public_support")
DISTRIBUTION = ("tester_group", "delivery_method")
DEVICE_KEYS = ("iphone", "ipad", "android_phone", "android_tablet", "mac")
OPTION_LABELS = {PENDING: "未定", "na": "該当なし"}


def question(key, title, hint, choices=(), group="conditions"):
    return {"key": key, "title": title, "hint": hint, "group": group,
            "options": [{"value": value, "label": label} for value, label in (*choices, (PENDING, "未定"))]
                       if choices else []}


YES_NO = (("yes", "あり"), ("no", "なし"))
NA = (("na", "該当なし"),)
DEVICE = (("available", "使える実機あり"), ("none", "端末なし"),
          ("simulator", "シミュレータのみ"), *NA)
SUPPORT = (("reply", "返信のみ・対応完了後30日以内に削除"),
           ("diagnostics", "返信・不具合調査に利用し、対応完了後30日以内に削除"),
           ("documented", "別の確定済み運用を既存の公開ポリシー／申告根拠で参照"), *NA)


def questions(release):
    platform = release["platform"]
    production = release["track"] == "production"
    testing = platform == "ios" or platform == "android" and not production
    yield question("first", "初回か更新か", "例　このストアでの初回公開", (("yes", "初回"), ("no", "更新")))
    yield question("account_setup", "本人によるストアの初期設定", "例　初回の規約・宣誓が表示されている",
                   (("ready", "済み"), ("sign_in", "サインインが必要"), ("agreement", "規約・宣誓の確認が必要"),
                    ("identity", "本人確認が必要"), *NA))
    if production:
        yield question("price", "価格", "例　無料は free、有料は JPY 400")
        yield question("regions", "配信地域", "例　全世界、日本のみは JP、複数の場合は JP, US")
        yield question("purchases", "アプリ内課金・支援購入", "例　このビルドでは支援購入を提供しない", YES_NO)
        yield question("publication", "本番の公開方法", "例　審査通過後に本人が公開", (("manual", "手動"), ("automatic", "自動")))
        pairs = {
            "ios": (("phone", "iPhone"), ("tablet", "iPad"), ("both", "iPhone・iPad")),
            "android": (("phone", "スマートフォン"), ("tablet", "タブレット"), ("both", "スマートフォン・タブレット")),
            "macos": (("mac", "Mac"),),
        }
        yield question("supported_devices", "掲載・配信の対象端末", "例　iPhone・iPadの両方に対応", pairs[platform])
        yield question("ads", "広告", "例　第三者広告の表示なし", YES_NO, "declarations")
        yield question("data", "データの扱い", "例　端末内に保存し、問い合わせ・SDKの通信も含めて確認",
                       (("local", "端末内のみ"), ("transmitted", "端末外へ送るデータあり")), "declarations")
        ages = (("children", "子どもを対象に含む"), ("general", "子どもを対象に指定しない"),
                ("adults", "成人向け"))
        yield question("age", "対象年齢", "例　成人向け。ストアの年齢レーティング質問と合わせて照合", ages, "declarations")
        yield question("support", "問い合わせ・診断データの運用", "例　問い合わせへの返信と不具合調査に使い、対応完了後30日以内に削除", SUPPORT, "declarations")
        yield question("public_support", "公開する問い合わせ窓口", "例　既存の公開用メールまたはサポートURLを案件の設定から参照",
                       (("configured", "公開用の窓口は確定済み"), *NA), "declarations")
        yield question("rights", "掲載・配信するコンテンツの権利", "例　第三者の素材は配信許諾取得済み",
                       (("owned", "自作・権利保有"), ("licensed", "第三者の配信許諾取得済み"), *NA), "declarations")
        yield question("app_access", "審査での特別なアクセス", "例　ログイン不要。必要な場合は審査用の専用設定を参照",
                       (("required", "審査用アカウント・手順あり"), *NA), "declarations")
    if platform != "android":
        yield question("encryption", "暗号化・輸出申告の判断", "例　既存の輸出コンプライアンス根拠を参照",
                       (("exempt", "非該当・免除の根拠あり"), ("declaration", "申告する根拠あり")), "declarations")
    if testing:
        groups = (("self", "自分の端末のみ"), ("invited", "指名済みテスター"), ("closed", "製品版アクセス条件のあるクローズドテスト"))
        if platform == "ios":
            groups = groups[:2]
        yield question("tester_group", "テストする人", "例　指名済みのテスターだけに案内", (*groups, *NA), "testing")
        yield question("delivery_method", "参加リンクの届け方", "例　確認済みテスター宛てのメールに参加リンクとビルド番号を記載",
                       (("email", "確認済み宛先へメール"), ("direct", "端末で開ける既存の連絡経路"), *NA), "testing")
        yield question("test_plan", "実機QAの端末と必須操作", "例　Android QA: PDFを開く, 再起動後に表示（1端末につき1行）",
                       group="devices")
    devices = {"ios": (("iphone", "iPhone"), ("ipad", "iPad")),
               "android": (("android_phone", "Androidスマートフォン"), ("android_tablet", "Androidタブレット")),
               "macos": (("mac", "Mac"),)}
    for key, label in devices[platform]:
        yield question(key, label + "の実機", "例　端末なし。画面・基本操作のみシミュレータで確認", DEVICE, "devices")


def identity(target):
    return {key: target.get(key) for key in ("bundle", "store_id", "provider")}


def plan_text(plan):
    return "\n".join(f"{device}: {', '.join(operations)}" for device, operations in sorted(plan.items()))


def prefill(release, target):
    current = release.get("intake", {})
    valid_scope = current.get("identity") == identity(target)
    values = current.get("answers", {}).copy() if valid_scope else {}
    for key in ("first", "purchases"):
        value = release.get(key)
        if type(value) is bool:
            values[key] = "yes" if value else "no"
    for key in ("price", "publication"):
        if release.get(key):
            values[key] = release[key]
    if release.get("region_scope") == WORLDWIDE:
        values["regions"] = WORLDWIDE_LABEL
    elif release.get("regions"):
        values["regions"] = ", ".join(release["regions"])
    if release.get("test_plan"):
        values["test_plan"] = plan_text(release["test_plan"])
    return values, current if valid_scope else {}


def checklist(release, target):
    values, saved = prefill(release, target)
    fields = []
    for item in questions(release):
        key = item["key"]
        value = values.get(key, "")
        if saved and value != saved["submitted"].get(key, ""):
            state = "changed"
        elif not value:
            state = "missing"
        elif value == PENDING:
            state = "undecided"
        else:
            state = "saved" if saved else "prefilled"
        fields.append({**item, "value": value, "state": state})
    inputs = [identity(target), saved, values]
    if release.get("region_scope") == WORLDWIDE:
        inputs.append(release.get("regions", []))
    revision = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return {"id": release["id"], "saved_at": saved.get("saved_at"), "fields": fields, "revision": revision}


def parse_plan(value):
    plan = {}
    for line in value.splitlines():
        device, separator, operations = line.partition(":")
        device = device.strip()
        names = [operation.strip() for operation in operations.split(",")]
        if (not separator or not device or device in plan or any(not name for name in names)
                or len(names) != len(set(names))):
            raise ValueError("実機QAは「端末の表示名: 必須操作, 必須操作」の形式で1端末ずつ入力してください。")
        plan[device] = names
    return plan


def save_answers(release, target, answers, timestamp):
    fields = {item["key"]: item for item in questions(release)}
    if not isinstance(answers, dict) or not answers or answers.keys() - fields.keys():
        raise ValueError("開始時の確認リストに表示された項目を指定してください。")
    values, _ = prefill(release, target)
    for key, value in answers.items():
        if not isinstance(value, str) or len(value) > (2048 if key == "test_plan" else 180):
            raise ValueError("開始時の回答の形式を確認してください。")
        value = value.strip()
        if key == "regions" and value.lower() == WORLDWIDE:
            value = WORLDWIDE_LABEL
        if value == values.get(key):
            continue
        options = {item["value"] for item in fields[key]["options"]}
        if key == "test_plan" and value == "na":
            release.pop(key, None)
            values[key] = value
            continue
        if value in ("", PENDING) and not options:
            value = value or PENDING
        elif options and value not in options and value:
            raise ValueError(f"{fields[key]['title']}の選択肢を確認してください。")
        if value == PENDING or not value:
            if key in (*BASE, "test_plan"):
                release.pop(key, None)
            if key == "regions":
                release.pop("region_scope", None)
            values[key] = value
            continue
        if not options and (re.search(r"[@/]|\+\d|\d{2,}[- ]\d{2,}[- ]\d{3,}", value)
                            or any(not char.isprintable() and char != "\n" for char in value)):
            raise ValueError("開始時の確認リストには連絡先、URLや認証情報を入力しないでください。")
        if key in ("first", "purchases"):
            release[key] = value == "yes"
        elif key == "price":
            if value != "free" and not re.fullmatch(r"[A-Z]{3} [0-9]{1,8}(?:\.[0-9]{1,2})?", value):
                raise ValueError("価格は free または通貨コードと金額（例 JPY 400）で入力してください。")
            release[key] = value
        elif key == "regions":
            if value == WORLDWIDE_LABEL:
                release["region_scope"] = WORLDWIDE
                release["regions"] = []
            else:
                regions = re.split(r"[\s,、]+", value.upper())
                if any(not re.fullmatch(r"[A-Z]{2}", region) for region in regions):
                    raise ValueError("全世界または二文字の地域コードで入力してください（例 JP, US）。")
                release.pop("region_scope", None)
                release["regions"] = sorted(set(regions))
                value = ", ".join(release["regions"])
        elif key == "publication":
            release[key] = value
        elif key == "test_plan":
            release[key] = parse_plan(value)
            value = plan_text(release[key])
        values[key] = value
    release["intake"] = {
        "identity": identity(target), "saved_at": timestamp,
        "submitted": {key: values.get(key, "") for key in fields},
        "answers": {key: value for key, value in values.items()
                    if key in fields and (key not in (*BASE, "test_plan") or value in (PENDING, "na"))},
    }


def unresolved(report, keys):
    return [item["title"] for item in report["fields"]
            if item["key"] in keys and item["state"] in ("missing", "undecided", "changed")]


def summary_rows(report):
    rows = []
    for item in report["fields"]:
        labels = {option["value"]: option["label"] for option in item["options"]}
        value = item["value"]
        display = labels.get(value, OPTION_LABELS.get(value, value)) or "未記入"
        if item["state"] == "changed":
            display += "（登録設定が変更されました）"
        rows.append((item["title"], display))
    return rows
