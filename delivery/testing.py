"""Locally recorded observations of a store test rollout."""
import json
import re


TEST_STAGES = ("distribution", "participation", "availability", "installation", "qa")


def validate_invite(url, target):
    url = url.strip()
    if target["platform"] == "ios":
        allowed = r"https://testflight\.apple\.com/join/[A-Za-z0-9]+/?"
        hint = "TestFlightの公開招待リンクを指定してください。内部テスターにはApp Store Connectから招待します。"
    else:
        bundle = re.escape(target["bundle"])
        allowed = rf"https://play\.google\.com/apps/(?:testing/{bundle}|internaltest/[0-9]+)/?"
        hint = "Play Consoleから対象アプリのテスト参加リンクをコピーしてください。"
    if not re.fullmatch(allowed, url):
        raise ValueError(hint)
    return url


def decode(row):
    return {**json.loads(row["data"]), **{key: row[key] for key in ("id", "stage", "device", "operation", "time")}}


def last_observation(db, release, scope, stage, device, operation=""):
    return db.execute(
        """SELECT * FROM test_observations WHERE release=? AND scope=? AND stage=? AND device=? AND operation=?
           ORDER BY id DESC LIMIT 1""",
        (release["id"], scope, stage, device, operation),
    ).fetchone()


def save_observation(db, release, scope, args, timestamp):
    stage = args.stage
    device = (args.device or "").strip()
    operation = (args.operation or "").strip()
    note = args.note.strip()
    if not note:
        raise ValueError("結果の根拠を入力してください。")
    if (stage != "distribution") != bool(device):
        raise ValueError("テスターの確認には端末名を入力してください。配信状況は案件ごとに記録します。")
    if (stage == "qa") != bool(operation):
        raise ValueError("実機QAでは操作名を入力してください。")
    if stage == "distribution" and args.source != "store":
        raise ValueError("対象ビルドの配信状況をストア管理画面で確認してください。")
    if stage in ("availability", "installation", "qa") and args.source == "store":
        raise ValueError("端末での確認結果、またはテスターからの報告を記録してください。")
    if stage != "installation" and any((args.os, args.version, args.build, args.build_source)):
        raise ValueError("OS・版・ビルドの情報はインストールの確認に記録してください。")

    data = {"outcome": args.outcome, "source": args.source, "note": note}
    if stage == "installation":
        version = (args.version or "").strip()
        build = (args.build or "").strip()
        if bool(build) != bool(args.build_source):
            raise ValueError("ビルド番号とその確認元を両方入力してください。")
        if any(value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value)
               for value in (version, build)):
            raise ValueError("版・ビルド番号の形式を確認してください。")
        if args.outcome == "pass" and ((version and version != release["version"])
                                       or (build and build != release["build"])):
            raise ValueError("対象版・ビルドと異なる場合は、要確認として記録してください。")
        data.update(os=(args.os or "").strip(), version=version, build=build, build_source=args.build_source)
    if stage == "qa":
        row = last_observation(db, release, scope, "installation", device)
        if not row or decode(row)["outcome"] != "pass":
            raise ValueError("同じ端末でテスト配布からインストールできたことを記録してください。")
        installation = decode(row)
        data.update({key: installation[key] for key in ("os", "version", "build", "build_source")})
        data.update(installation_id=installation["id"], installation_source=installation["source"])

    previous = last_observation(db, release, scope, stage, device, operation)
    if previous and json.loads(previous["data"]) == data:
        return
    with db:
        db.execute(
            """INSERT INTO test_observations (release, scope, stage, device, operation, data, time)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (release["id"], scope, stage, device, operation, json.dumps(data, ensure_ascii=False), timestamp),
        )


def test_progress(db, release, scope):
    rows = db.execute("SELECT * FROM test_observations WHERE release=? AND scope=? ORDER BY id DESC",
                      (release["id"], scope))
    latest = {}
    history = []
    for row in rows:
        item = decode(row)
        key = (item["stage"], item["device"], item["operation"])
        if key in latest:
            history.append(item)
        else:
            latest[key] = item
    stages = {stage: [] for stage in TEST_STAGES}
    for (stage, device, _), item in latest.items():
        if stage == "qa" and latest[("installation", device, "")]["id"] != item["installation_id"]:
            history.append(item)
        else:
            stages[stage].append(item)
    return {"stages": stages, "history": sorted(history, key=lambda item: item["id"], reverse=True)}
