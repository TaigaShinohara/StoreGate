"""Derive handoffs from saved facts without turning an observation into approval."""
from pathlib import Path

from intake import DISTRIBUTION, unresolved

TEST_LABELS = {
    "distribution": "テスト配信",
    "participation": "テスター参加",
    "availability": "端末での入手可否",
    "installation": "端末へのインストール",
    "qa": "実機での操作確認",
}
CHECK_LABELS = {
    "conditions": "配信条件", "screenshots": "掲載画像", "artifact": "署名済み成果物",
    "build_preflight": "アプリの配布設定", "source": "ソース監査", "metadata": "掲載原稿",
    "account": "アカウントと契約", "device": "提出版の実機確認", "declarations": "確定した申告",
    "checklist": "提出前チェックリスト", "storefront": "ストア保存内容との照合",
    "qa": "登録済みQA", "publication": "公開確認", "intake_production": "開始時の確認リスト",
}

APPLE_BETA_ACTIVE = {"IN_BETA_TESTING", "IN_EXTERNAL_TESTING"}
PLAY_TRACK_PUBLISHED = "RELEASE_LIFECYCLE_STATE_PUBLISHED"
REVIEW_WAIT = {"WAITING_FOR_REVIEW", "IN_REVIEW", "PENDING_APPLE_RELEASE", "PROCESSING_FOR_DISTRIBUTION",
               "RELEASE_LIFECYCLE_STATE_IN_REVIEW"}
REVIEW_APPROVED = {"PENDING_DEVELOPER_RELEASE", "RELEASE_LIFECYCLE_STATE_APPROVED_NOT_PUBLISHED"}
REVIEW_REJECTED = {"REJECTED", "METADATA_REJECTED", "DEVELOPER_REJECTED", "INVALID_BINARY",
                   "RELEASE_LIFECYCLE_STATE_NOT_APPROVED"}
SUBMISSION_READY = {"PREPARE_FOR_SUBMISSION", "READY_FOR_REVIEW", "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW"}
PUBLISHED = {"READY_FOR_DISTRIBUTION", "READY_FOR_SALE", PLAY_TRACK_PUBLISHED}


def step(code, title, owner, state, reason, resume_when, *, args=None, device=None):
    info = {"code": code, "title": title, "owner": owner, "state": state, "reason": reason, "resume_when": resume_when}
    if args:
        info["command"] = args
    if device:
        info["device"] = device
    return info


def distribution_step(release, testing, receipt):
    rows = testing["stages"]["distribution"]
    if rows and rows[0]["outcome"] == "blocked":
        return step("distribution", "テスト配信の確認", "operator", "blocked", rows[0]["note"],
                    "公式画面で対象ビルドの配信を確認して記録する。")
    if receipt.get("outcome") == "observed":
        state = receipt.get("state")
        if state in APPLE_BETA_ACTIVE or receipt.get("external_state") in APPLE_BETA_ACTIVE or state == PLAY_TRACK_PUBLISHED:
            return None
        if state in ("PROCESSING", "IN_EXPORT_COMPLIANCE_REVIEW", "RELEASE_LIFECYCLE_STATE_IN_REVIEW"):
            return step("distribution", "ストア側の処理を確認", "store", "waiting", f"最新のストア状態は {state} です。",
                        "ストアの処理が進んだあとに対象ビルドを再照会する。", args=["resume", release["id"], "--phase", "test", "--store"])
        if state == "MISSING_EXPORT_COMPLIANCE":
            return step("distribution", "TestFlightの輸出申告", "operator", "waiting",
                        "対象ビルドで輸出コンプライアンスの確認が必要です。",
                        "本人が回答を確認し、App Store Connectで対象ビルドを再照会する。")
        if rows and rows[0]["outcome"] == "pass" and rows[0]["time"] > receipt["observed_at"]:
            return None
        if state == "RELEASE_LIFECYCLE_STATE_NOT_SENT_FOR_REVIEW" and receipt.get("versionCode") == release["build"]:
            return step("test_approval", "テスト配信開始の本人確認", "owner", "waiting",
                        "Playで対象ビルドの審査未送信を照合しました。",
                        "公式画面で対象トラック・テスター範囲・同時に送られる変更を確認し、この版で配信を始める個別承認を得る。")
        return step("distribution", "テスト配信を公式画面で確認", "operator", "waiting",
                    f"最新のストア照会で対象ビルドの配信を確認できません（{state}）。",
                    "公式画面で対象トラックまたはテストグループの配信を確認して記録する。")
    if rows and rows[0]["outcome"] == "pass":
        return None
    return step("distribution", "テスト配信を公式画面で確認", "operator", "waiting",
                "対象ビルドのテスト配信が未確認です。",
                "公式画面で対象トラックまたはテストグループの配信を確認して記録する。")


def tester_step(release, device, stages, operations, receipt):
    for name, resume_when in (
        ("participation", "招待または参加リンクをテスト端末で開き、対象アカウントで参加結果を記録する。"),
        ("availability", "参加したアカウントで配布ページを開き直し、入手できるか記録する。"),
        ("installation", "配布ページから対象版をインストールし、版と確認元を記録する。"),
    ):
        recorded = next((row for row in stages[name] if row["device"] == device), None)
        if not recorded or recorded["outcome"] != "pass":
            return step(name, TEST_LABELS[name], "tester", "blocked" if recorded else "waiting",
                        recorded["note"] if recorded else f"{TEST_LABELS[name]}の結果が未記録です。",
                        resume_when, device=device)
        if (name == "installation" and receipt.get("outcome") in ("mismatch", "not_installed")
                and receipt["observed_at"] >= recorded["time"]):
            return step("device_mismatch", "端末の対象版を照合", "tester", "blocked", receipt["message"],
                        "テスト配布から対象版を入れ直し、端末または本人報告で版を照合して記録する。", device=device)
        if name == "installation":
            recorded_identity = (recorded.get("version"), recorded.get("build"))
            device_identity_confirmed = (receipt.get("outcome") == "matches"
                                         and receipt["observed_at"] >= recorded["time"])
            if recorded_identity != (release["version"], release["build"]) and not device_identity_confirmed:
                return step("device_identity", "端末の版・ビルドを照合", "tester", "waiting",
                            "インストール済みの版とビルドの根拠が揃っていません。",
                            "端末を読み取るか、版・ビルドと確認元をインストール結果に記録する。", device=device)
    qa = {row["operation"]: row for row in stages["qa"] if row["device"] == device}
    missing = [name for name in operations if name not in qa or qa[name]["outcome"] != "pass"]
    failures = [name for name, row in qa.items() if row["outcome"] == "blocked"]
    remaining = list(dict.fromkeys([*failures, *missing]))
    if remaining or not qa:
        message = "確認する操作は " + "、".join(remaining) + " です。" if remaining else "実機での操作結果が未記録です。"
        return step("qa", "対象端末での操作確認", "tester", "blocked" if failures else "waiting", message,
                    "対象版で操作を実行し、操作ごとの結果を記録する。", device=device)
    return None


def test_audience(intake):
    if not intake or not intake["saved_at"]:
        return None
    pending = unresolved(intake, DISTRIBUTION)
    if pending:
        return step("test_audience", "テスターと案内方法を確認", "owner", "waiting",
                    "参加前に確認する項目 " + "、".join(pending),
                    "開始時の確認リストにテスターと案内方法を記録する。")
    if any(item["key"] in DISTRIBUTION and item["value"] == "na" for item in intake["fields"]):
        return step("test_audience", "テスト案内は対象外", "operator", "waiting",
                    "今回の参加案内は該当なしと記録されています。",
                    "参加案内が必要になった時点で案内先と実機QAを確認する。")
    return None


def test_steps(release, testing, readbacks, intake=None):
    distribution = distribution_step(release, testing, readbacks.get("test_store", {}))
    if distribution:
        return [distribution]
    stages = testing["stages"]
    matrix = release.get("test_plan", {})
    observed = {row["device"] for name in ("participation", "availability", "installation", "qa") for row in stages[name]}
    devices = sorted(matrix or (observed | readbacks.get("devices", {}).keys()))
    if not devices:
        if handoff := test_audience(intake):
            return [handoff]
        return [step("participation", "テスターに参加を案内", "operator", "ready", "配信済みのテスト版があります。",
                     "招待をテスト端末へ渡し、参加した端末を記録する。")]
    tasks = []
    for device in devices:
        task = tester_step(release, device, stages, matrix.get(device, []), readbacks.get("devices", {}).get(device, {}))
        if task:
            if task["code"] == "participation" and (handoff := test_audience(intake)):
                handoff["device"] = device
                task = handoff
            tasks.append(task)
    if tasks:
        return tasks
    if not matrix:
        return [step("test_plan", "実機QAの完了条件を確認", "operator", "waiting",
                     "記録済みの操作は確認済みです。必須の端末と操作が案件に未登録です。",
                     "配信設定の test_plan に必須の端末と操作を登録し、結果を照合する。")]
    return [step("test_complete", "計画した実機QAを確認済み", "none", "complete",
                 "対象ビルドのテスト配信と、登録した端末・操作の結果を確認しました。",
                 "本番準備へ進む場合は本番の確認項目を開く。")]


def production_steps(release, state, receipt):
    identifier = release["id"]
    remote_state = receipt.get("state") if receipt.get("outcome") == "observed" else None
    matches = receipt.get("build", receipt.get("versionCode")) == release["build"]
    if matches and remote_state in PUBLISHED:
        published = any(item["name"] == "publication" and item["state"] == "pass" for item in state["checks"])
        if published:
            return [step("complete", "公開先を確認済み", "none", "complete", "予定地域で対象版の公開を確認しました。", "この版の確認は完了です。")]
        command = ["verify-publication", identifier] if release["platform"] != "android" and state["regions"] else None
        return [step("publication", "予定地域での入手を確認", "ai" if command else "operator", "ready",
                     "ストアで対象ビルドの公開を確認しました。",
                     "予定地域ごとに対象版の入手を確認する。", args=command)]
    review_wait = matches and remote_state in REVIEW_WAIT
    tasks = []
    if matches and remote_state in REVIEW_REJECTED:
        tasks.append(step("review_feedback", "審査結果を確認", "operator", "blocked",
                          f"最新のストア状態は {remote_state} です。", "公式画面で指摘と対象版を確認して対応する。"))
    for check in state["checks"]:
        name = check["name"]
        if check["state"] == "pass" or name == "publication":
            continue
        args = None
        status = "waiting" if check["state"] == "pending" else "blocked"
        owner = {"device": "tester", "intake_production": "owner", "conditions": "owner"}.get(name, "operator")
        has_metadata = release.get("metadata") and Path(release["metadata"]).is_dir()
        if (name in ("source", "build_preflight") or name == "metadata" and has_metadata) and check["state"] in ("pending", "stale"):
            args, owner = ["resume", identifier, "--phase", "production", "--prepare"], "ai"
        elif (name == "qa" and check["state"] in ("pending", "stale") and state.get("can_run_qa")
              and any(item["name"] == "artifact" and item["state"] == "pass" for item in state["checks"])):
            args, owner = ["qa", identifier], "ai"
        if owner == "ai":
            status = "ready"
        if review_wait and owner != "ai":
            continue
        tasks.append(step(name, CHECK_LABELS.get(name, name), owner, status, check["message"],
                          f"{CHECK_LABELS.get(name, name)}の根拠を対象版で確認する。", args=args))
    if review_wait:
        waiting = step("review", "ストアの審査・配信処理待ち", "store", "waiting", f"最新のストア状態は {remote_state} です。",
                       "ストアの処理が進んだあとに対象版を再照会する。",
                       args=["resume", identifier, "--phase", "production", "--store"])
        return [waiting, *tasks]
    if tasks:
        return tasks
    if receipt.get("outcome") == "unconfirmed":
        return [step("store_connection", "ストアの接続を確認", "operator", "blocked", receipt["message"],
                     "読み取り権限を確認するか、公式画面で版・ビルドを照合する。")]
    if not remote_state:
        return [step("store_readback", "対象版のストア状態を確認", "ai", "ready", "ローカルの確認項目が揃っています。",
                     "公式画面または対象ストアの読み取り照会で、保存済みの版・ビルドを照合する。",
                     args=["resume", identifier, "--phase", "production", "--store"])]
    if not matches:
        return [step("store_build", "ストアの対象ビルドを照合", "operator", "blocked",
                     "最新のストア照会で対象ビルドを確認できません。",
                     "公式画面で対象版と紐づけるビルドを照合する。")]
    if remote_state not in REVIEW_APPROVED | SUBMISSION_READY:
        return [step("store_state", "対象版のストア状態を確認", "operator", "waiting",
                     f"最新の状態は {remote_state} です。",
                     "公式画面で対象版の状態と次の操作を確認する。")]
    action = "publish" if remote_state in REVIEW_APPROVED else "submit"
    label = "本番公開" if action == "publish" else "審査送信"
    return [step("approval", f"{label}の本人確認", "owner", "waiting",
                 "対象版と操作内容を本人が確認する段階です。",
                 f"{label}用の確認票を確認し、対象版と今回の操作を個別に承認する。",
                 args=["approval-packet", identifier, "--action", action])]


IMPACTS = {
    "conditions": "対象の価格・地域・公開方法を確定します。",
    "intake_production": "未定の回答に関係する本番申告・提出条件を確定します。",
    "test_approval": "対象トラックのテスターが対象ビルドを入手できる配信へ進みます。",
    "distribution": "対象トラックの配信状態を照合します。",
    "test_audience": "参加案内の宛先と経路に適用します。",
    "participation": "対象テスターのアカウントで参加状態を確認します。",
    "availability": "参加した端末とアカウントで入手できるか確認します。",
    "installation": "端末に対象ビルドを入れます。",
    "qa": "対象版の実機操作の根拠を記録します。",
    "device": "対象版の実機操作の根拠を記録します。",
    "review_draft": "対象版をAppleの提出下書きに追加し、読み戻します。審査送信は個別承認で行います。",
    "play_edit": "対象トラックの編集でAAB・掲載内容を検証します。公式画面でのレビュー保存は別工程です。",
    "play_review_save": "レビュー画面で変更を下書き保存し、版・ビルドと審査未送信を読み戻します。",
    "local_draft": "掲載原稿と画像をローカルで表示します。",
    "conditions_draft": "対象版の編集可能な下書きにだけ設定を反映し、保存内容を照合します。",
    "registration": "アプリへのストア接続・入稿に必要です。",
    "local_audits": "ローカルの検証結果を記録します。",
}


def decorate_steps(release, state, phase, steps):
    channel = "TestFlight" if phase == "test" and release["platform"] == "ios" else release["track"]
    scope = {"app": state.get("app") or release["app"], "platform": release["platform"],
             "version": release["version"], "build": release["build"], "track": channel}
    for item in steps:
        code, owner = item["code"], item["owner"]
        if code == "approval":
            impact = ("対象版をストアで公開します。" if item["command"][-1] == "publish" else
                      "対象版をストアの審査へ送信します。予定した公開方式が適用されます。")
        else:
            impact = item.get("impact") or IMPACTS.get(code) or (
                "対象版のローカル根拠を確認します。" if owner == "ai" and code in CHECK_LABELS else
                "対象版の次工程に必要な状態を照合します。")
        if item["state"] == "complete":
            category = "complete"
        elif owner == "ai" and item["state"] == "ready":
            category = "runnable"
        else:
            category = {"owner": "owner_action", "tester": "device_wait", "store": "store_wait"}.get(owner, "operator_action")
        item.update(scope=scope, category=category, impact=impact)
    return steps


def environment_steps(rows):
    tasks = []
    for row in rows:
        name = row["name"]
        if row.get("scope") == "build" and row["state"] != "pass":
            tasks.append(step(name, row["title"], "operator", "waiting", row["message"],
                              "出力先と空き容量を確認して preflight を再実行する。"))
            tasks[-1]["impact"] = "対象のローカルビルドは空き容量を確認できるまで待機します。"
        elif row.get("scope") in ("workspace_write", "temporary_write") and row["state"] != "pass":
            tasks.append(step(name, row["title"], "operator", "waiting", row["message"],
                              "この領域の空き容量を確保し、失敗した保存・検査を再実行する。"))
            tasks[-1]["impact"] = "この領域に書き込む工程に影響します。"
        elif name == "authentication" and row["outcome"] != "credential_present":
            tasks.append(step(name, row["title"], "operator", "waiting", row["message"],
                              "既存の認証ランナーまたは権限のある管理画面で対象アプリを照合する。"))
            tasks[-1]["impact"] = "ストアAPIを使う照会・反映に影響します。"
        elif name == "connected_devices" and row["state"] != "pass":
            tasks.append(step(name, row["title"], "tester", "waiting", row["message"],
                              "予定した端末を照会できる状態にするか、対象版の実機結果を記録する。"))
        elif name in ("iphone", "ipad", "android_phone", "android_tablet", "mac") and row["outcome"] in ("none", "simulator"):
            tasks.append(step(name, row["title"], "tester", "waiting", row["message"],
                              "その機種で必要な実機QAを行える端末が確保できた時点で再開する。"))
    return tasks


def draft_steps(release, state, receipt, phase):
    settings = state.get("draft_settings")
    if not settings:
        return []
    open_checks = {check["name"] for check in state["checks"] if check["state"] != "pass"}
    identifier = release["id"]
    android = release["platform"] == "android"
    tasks = []
    if (phase == "production" and receipt.get("state") in SUBMISSION_READY
            and state["intake"]["saved_at"] and "storefront" in open_checks
            and not open_checks & {"conditions", "account"}):
        tasks.append(step("conditions_draft", "確定済みの条件を下書きで照合", "ai", "ready",
                          "対象版の価格・地域・公開方法は登録済みです。",
                          "公式画面で版と設定を照合し、下書き内で反映・保存できる差分を読み戻す。公開や審査へ進むボタンは操作ごとの承認を確認する。"))
        tasks[-1]["surface"] = "console"
    if open_checks & {"metadata", "screenshots"} or not {"metadata", "screenshots"} <= {check["name"] for check in state["checks"]}:
        return tasks
    if android and (phase == "production" or not settings["play_staging"]):
        tasks.append(step("local_draft", "Playの掲載下書きを照合", "ai", "ready",
                          "監査済みの掲載原稿と画像を表示できます。",
                          "ローカルの原稿と画像を確認する。", args=["draft", identifier]))
    if android and phase == "test":
        edit = settings["play_edit_state"]
        if edit == "validated":
            tasks.append(step("play_review_save", "Playのレビュー画面で下書きを保存", "operator", "waiting",
                              "Play APIの編集中の内容を検証しました。公式画面への反映は未確認です。",
                              "公式画面の版と差分を確認する。編集画面で下書きを整えた場合は「次へ」のレビュー画面で下書きを保存し、審査未送信を読み戻す。"))
        elif edit in ("changed", "opening"):
            tasks.append(step("play_edit_reconcile", "既存のPlay編集を照合", "operator", "waiting",
                              "既存の編集と現在の案件設定が一致しないか、編集の開始結果が未確認です。",
                              "対象トラックと既存の変更を確認して再開する。"))
        elif settings["play_staging"] and not open_checks & {"account", "source", "artifact"} and settings["api_ready"]:
            tasks.append(step("play_edit", "Playの編集で下書きを検証", "ai", "ready", "対象版の原稿・画像・成果物を照合済みです。",
                              "編集を検証して対象ビルド・素材を読み戻す。", args=["draft", identifier]))
    if not android and phase == "production" and settings["api_ready"] and receipt.get("state") in SUBMISSION_READY:
        if not open_checks & {"account"} and receipt.get("state") == "PREPARE_FOR_SUBMISSION":
            tasks.append(step("apple_draft", "Appleの掲載下書きを保存・照合", "ai", "ready",
                              "編集可能な掲載下書きとローカル原稿を確認しました。",
                              "原稿・画像を保存し、対象版の内容を読み戻す。", args=["draft", identifier]))
            tasks[-1]["impact"] = "対象版の掲載下書きを更新します。"
        if open_checks <= {"intake_production"} and receipt.get("build") == release["build"] and receipt.get("state") != "READY_FOR_REVIEW":
            tasks.append(step("review_draft", "Appleの審査用下書きに追加", "ai", "ready",
                              "審査準備中の対象ビルドと提出前の根拠を照合しました。",
                              "審査用に追加し、下書き状態を読み戻す。", args=["review-draft", identifier]))
    return tasks


def workflows(release, state, readbacks):
    phases = {}
    if "testing" in state:
        phases["test"] = test_steps(release, state["testing"], readbacks, state.get("intake"))
    if release["track"] == "production":
        phases["production"] = production_steps(release, state, readbacks.get("production_store", {}))
    default = "test" if "test" in phases and phases["test"][0]["state"] != "complete" else next(reversed(phases), None)
    setup = next((item for item in (state.get("intake") or {}).get("fields", [])
                  if item["key"] == "account_setup" and item["state"] == "saved"), None)
    for phase, steps in phases.items():
        if steps[0]["state"] != "complete":
            steps.extend(draft_steps(release, state, readbacks.get(phase + "_store", {}), phase))
            actions = {"sign_in": "ストアにサインイン", "identity": "ストアで本人確認", "agreement": "規約・宣誓の内容を確認"}
            if setup and (label := actions.get(setup["value"])):
                tasks = step("registration", label, "owner", "waiting", "開始時に本人操作が必要と記録されています。",
                             "公式画面で操作し、開始時の確認リストを更新する。")
                steps.append(tasks)
        decorate_steps(release, state, phase, steps)
    return {"default_phase": default, "phases": phases,
            "pending_audits": pending_audits(release, state, ("pending", "stale", "not_checked")),
            "automatic_audits": pending_audits(release, state, ("pending", "stale"))}


def pending_audits(release, state, retryable):
    metadata = release.get("metadata")
    return [item["name"] for item in state["checks"] if item["state"] in retryable
            and (item["name"] in ("source", "build_preflight") or item["name"] == "metadata" and metadata and Path(metadata).is_dir())]
