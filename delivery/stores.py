"""Store API clients. Credentials remain in the invoking process."""
import base64
import binascii
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class StoreError(ValueError):
    def __init__(self, message, uncertain=False, status=None):
        super().__init__(message)
        self.uncertain = uncertain
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base, target):
        self.base = base
        name = target.get("auth_env")
        self.token = os.environ.get(name, "") if name else ""
        if not self.token:
            raise StoreError("案件に紐づくストア認証を既存の認証ランナーから渡してください。")
        self.opener = build_opener(NoRedirect())

    def request(self, path, method="GET", data=None, params=None, *, media=None):
        url = path if path.startswith("https://") else self.base + path
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.netloc != urlparse(self.base).netloc
                or parsed.username or parsed.password or parsed.fragment):
            raise StoreError("ストアAPIの応答に予定外の接続先が含まれています。")
        if params:
            url += "?" + urlencode(params)
        body = json.dumps(data).encode() if data is not None else None
        content_type = "application/json"
        if media:
            source, content_type = media
            body = source.read_bytes()
        request = Request(url, body, {"Authorization": "Bearer " + self.token, "Content-Type": content_type},
                          method=method)
        try:
            with self.opener.open(request, timeout=120 if media else 30) as response:
                payload = response.read(8 * 1024 * 1024)
                return json.loads(payload) if payload else {}
        except HTTPError as exc:
            raise StoreError(f"ストアAPIのHTTP {exc.code}。管理画面で登録・権限・状態を確認してください。",
                             method != "GET" and exc.code >= 500, exc.code) from None
        except (URLError, OSError, ValueError):
            raise StoreError("ストアとの通信結果を確認できません。再照会してください。",
                             method != "GET") from None

    def elements(self, path, params=None):
        seen = set()
        while path:
            if path in seen or len(seen) >= 30:
                raise StoreError("ストアAPIのページをすべて取得できません。")
            seen.add(path)
            response = self.request(path, params=params)
            yield from response["data"]
            path, params = response.get("links", {}).get("next"), None


def ref(kind, value):
    return {"data": {"type": kind, "id": value}}


def object_data(kind, **fields):
    return {"data": {"type": kind, **fields}}


def get_one(items, message):
    items = list(items)
    if len(items) != 1:
        raise StoreError(message)
    return items[0]


class Apple:
    def __init__(self, target):
        if not target.get("store_id") or not target.get("provider"):
            raise StoreError("App Store ConnectのアプリIDと配信プロバイダを登録してください。")
        self.target = target
        self.client = Client("https://api.appstoreconnect.apple.com", target)
        self.app = target["store_id"]

    def status(self, release):
        app = self.client.request(f"/v1/apps/{self.app}")["data"]
        if app["id"] != self.app or app["attributes"]["bundleId"] != self.target["bundle"]:
            raise StoreError("App Store ConnectのアプリとBundle IDが一致しません。")
        platform = "MAC_OS" if self.target["platform"] == "macos" else "IOS"
        path = f"/v1/apps/{self.app}/appStoreVersions"
        versions = list(self.client.elements(path, {"filter[platform]": platform,
                                                   "filter[versionString]": release["version"]}))
        if not versions:
            return {"state": "registration_required", "app": self.app, "platform": platform}
        version = get_one((item for item in versions if item["attributes"].get("platform") == platform
                           and item["attributes"].get("versionString") == release["version"]),
                          "対象版のストアレコードを一意に照合できません。")
        version_id = version["id"]
        attrs = version["attributes"]
        build = self.client.request(f"/v1/appStoreVersions/{version_id}/build")["data"]
        build_number = build["attributes"]["version"] if build else None
        return {"state": attrs.get("appVersionState") or attrs.get("appStoreState"),
                "app": self.app, "platform": platform, "version_id": version_id,
                "build": build_number, "build_state": build["attributes"].get("processingState") if build else None,
                "build_id": build["id"] if build else None, "release_type": attrs.get("releaseType")}

    def available_build(self, release, platform):
        rows = self.client.elements("/v1/builds", {"filter[app]": self.app, "filter[version]": release["build"]})
        matches = []
        for row in rows:
            if row["attributes"].get("version") != release["build"]:
                continue
            train = self.client.request(f"/v1/builds/{row['id']}/preReleaseVersion")["data"]["attributes"]
            if (train.get("version"), train.get("platform")) == (release["version"], platform):
                matches.append(row)
        return get_one(matches, "対象版のビルドを一意に特定できません。") if matches else None

    def transporter(self, path):
        issuer = self.target.get("issuer_id")
        key = self.target.get("transporter_key_id")
        try:
            segment = self.client.token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
            if not issuer or not key or claims.get("iss") != issuer:
                raise ValueError()
        except (AttributeError, IndexError, ValueError, binascii.Error):
            raise StoreError("認証済みApp Store ConnectのチームとTransporterのIssuerを照合してください。") from None
        return ["xcrun", "iTMSTransporter", "-m", "upload", "-assetFile", str(path), "-apiIssuer", issuer,
                "-apiKey", key, "-asc_provider", self.target["provider"], "-v", "critical"]

    def bind_build(self, version, build):
        self.client.request(f"/v1/appStoreVersions/{version}/relationships/build", "PATCH", ref("builds", build))

    def localizations(self, version_id):
        return {row["attributes"]["locale"]: row for row in self.client.elements(
            f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations")}

    def sync_draft(self, release, images):
        from apple_assets import sync

        remote = self.status(release)
        if remote["state"] != "PREPARE_FOR_SUBMISSION":
            raise StoreError("App Store Connectで対象版の編集可能な下書きを確認してください。")
        localizations = self.localizations(remote["version_id"])
        changes = {}
        for locale in self.target["locales"]:
            if locale not in localizations:
                raise StoreError(f"App Store Connectで掲載言語 {locale} を登録してください。")
            folder = Path(release["metadata"]) / locale
            fields = {"description": "description", "keywords": "keywords", "support_url": "supportUrl",
                      "marketing_url": "marketingUrl", "promotional_text": "promotionalText", "release_notes": "whatsNew"}
            changes[locale] = {field: (folder / (file + ".txt")).read_text().strip()
                               for file, field in fields.items() if (folder / (file + ".txt")).is_file()}
            if not {"description", "keywords", "supportUrl"} <= changes[locale].keys():
                raise StoreError(f"{locale} の説明文・キーワード・サポートURLの原稿を確認してください。")
        sync(self.client, self.app, {locale: row["id"] for locale, row in localizations.items()}, self.target, images)
        for locale, fields in changes.items():
            row = localizations[locale]
            patch = {key: value for key, value in fields.items() if row["attributes"].get(key) != value}
            if patch:
                self.client.request(f"/v1/appStoreVersionLocalizations/{row['id']}", "PATCH",
                                    object_data("appStoreVersionLocalizations", id=row["id"], attributes=patch))
        actual = self.localizations(remote["version_id"])
        if any(actual[locale]["attributes"].get(key) != value
               for locale, fields in changes.items() for key, value in fields.items()):
            raise StoreError("App Store Connectから読み戻した掲載原稿が一致しません。")
        return {"state": "draft_verified", "localizations": sorted(changes), "screenshots": len(images)}

    def review_draft(self, remote, create=False):
        rows = list(self.client.elements("/v1/reviewSubmissions", {"filter[app]": self.app}))
        candidates = [
            row for row in rows if row["attributes"].get("state") == "READY_FOR_REVIEW"
            and row["attributes"].get("platform") == remote["platform"]
        ]
        if not candidates and create:
            row = self.client.request("/v1/reviewSubmissions", "POST", object_data(
                "reviewSubmissions", attributes={"platform": remote["platform"]},
                relationships={"app": ref("apps", self.app)}))["data"]
            candidates = [row]
        candidate = get_one(candidates, "対象プラットフォームの審査下書きを一意に特定できません。")
        submission = candidate["id"]
        items = list(self.client.elements(f"/v1/reviewSubmissions/{submission}/items",
                                          {"include": "appStoreVersion"}))
        if not items and create:
            self.client.request("/v1/reviewSubmissionItems", "POST", object_data(
                "reviewSubmissionItems", relationships={"reviewSubmission": ref("reviewSubmissions", submission),
                                                       "appStoreVersion": ref("appStoreVersions", remote["version_id"])}))
            items = list(self.client.elements(f"/v1/reviewSubmissions/{submission}/items", {"include": "appStoreVersion"}))
        item = get_one(items, "審査下書きに対象版以外の項目があります。公式画面で内容を確認してください。")
        if item.get("relationships", {}).get("appStoreVersion", {}).get("data") != ref("appStoreVersions", remote["version_id"])["data"]:
            raise StoreError("審査下書きに含まれるアプリの版が一致しません。")
        return submission

    def describe(self, action, release, remote):
        if remote.get("build") != release["build"] or remote.get("build_state") != "VALID":
            raise StoreError("ストアに紐づいた有効なビルドを対象版と照合してください。")
        if release["publication"] != "manual" or remote.get("release_type") != "MANUAL":
            raise StoreError("StoreGateからのApple審査・公開には、ストアと案件の双方で手動公開を設定してください。")
        localizations = self.localizations(remote["version_id"])
        listing = {locale: {key: row["attributes"].get(key) for key in
                            ("description", "keywords", "supportUrl", "marketingUrl", "promotionalText", "whatsNew")}
                   for locale, row in localizations.items()}
        screenshots = {}
        for locale, row in sorted(localizations.items()):
            locator = row["id"]
            placements = self.client.elements(f"/v1/appStoreVersionLocalizations/{locator}/placements",
                                              {"filter[placementType]": "APP_SCREENSHOT", "sort": "placementGroupPosition",
                                               "include": "image"})
            screenshots[locale] = [
                {"id": item["id"], "group": item["attributes"]["placementGroup"],
                 "image": item.get("relationships", {}).get("image", {}).get("data")}
                for item in placements
            ]
        if action == "submit":
            if remote["state"] not in {"PREPARE_FOR_SUBMISSION", "READY_FOR_REVIEW", "DEVELOPER_REJECTED", "REJECTED", "METADATA_REJECTED"}:
                raise StoreError("審査下書きの状態を確認してください。")
            return {"effect": "この版の審査をリクエストします。公開は別の承認操作です。",
                    "submission_id": self.review_draft(remote), "listing": listing, "store_screenshots": screenshots}
        if remote["state"] != "PENDING_DEVELOPER_RELEASE":
            raise StoreError("承認済みの手動公開待ちの版を確認してください。")
        return {"effect": "承認済みの対象版の公開をリクエストします。", "listing": listing, "store_screenshots": screenshots}

    def apply(self, action, remote, operation):
        if action == "submit":
            identifier = operation["submission_id"]
            self.client.request(f"/v1/reviewSubmissions/{identifier}", "PATCH", object_data(
                "reviewSubmissions", id=identifier, attributes={"submitted": True}))
        else:
            self.client.request("/v1/appStoreVersionReleaseRequests", "POST", object_data(
                "appStoreVersionReleaseRequests", relationships={"appStoreVersion": ref("appStoreVersions", remote["version_id"])}))

    def reconciled(self, action, before, remote, release):
        if (before.get("version_id"), release["build"]) != (remote.get("version_id"), remote.get("build")):
            return False
        accepted = {"WAITING_FOR_REVIEW", "IN_REVIEW", "PENDING_DEVELOPER_RELEASE", "PENDING_APPLE_RELEASE",
                    "PROCESSING_FOR_DISTRIBUTION", "READY_FOR_DISTRIBUTION", "READY_FOR_SALE"}
        if action == "publish":
            accepted -= {"WAITING_FOR_REVIEW", "IN_REVIEW", "PENDING_DEVELOPER_RELEASE"}
        return remote.get("state") in accepted


class Google:
    def __init__(self, target):
        if target.get("store_id") != target["bundle"] or not target.get("provider"):
            raise StoreError("Play Consoleのアカウントとアプリの登録を照合してください。")
        self.client = Client("https://androidpublisher.googleapis.com", target)
        self.app = target["bundle"]

    def status(self, release):
        track = release["track"]
        result = self.client.request(f"/androidpublisher/v3/applications/{self.app}/tracks/{track}/releases")
        versions = []
        for item in result.get("releases", []):
            active = [str(artifact.get("versionCode")) for artifact in item.get("activeArtifacts", [])]
            if item.get("track") == track and release["build"] in active:
                versions.append({"state": item["releaseLifecycleState"], "active_builds": active})
        if len(versions) > 1:
            raise StoreError("同じversionCodeが複数の配信に含まれています。Play Consoleで確認してください。")
        return {"app": self.app, "track": track, "versionCode": release["build"],
                **(versions[0] if versions else {"state": "not_observed"})}

    @property
    def edits(self):
        return f"/androidpublisher/v3/applications/{self.app}/edits"

    def new_edit(self):
        return self.client.request(self.edits, "POST", {})["id"]

    def stage_edit(self, edit, release, images, locales, artifact_sha):
        from builds import digest

        base = f"{self.edits}/{edit}"
        artifact = Path(release["artifact"])
        if digest(artifact) != artifact_sha:
            raise StoreError("AABが照合後に変更されました。")
        bundles = self.client.request(f"{base}/bundles").get("bundles", [])
        current = [item for item in bundles if str(item["versionCode"]) == release["build"]]
        if not current:
            current = [self.client.request("/upload" + base + "/bundles", "POST", params={"uploadType": "media"},
                                           media=(artifact, "application/octet-stream"))]
        bundle = get_one(current, "編集中のAABを一意に照合できません。")
        if str(bundle["versionCode"]) != release["build"] or bundle.get("sha256", "").lower() != artifact_sha:
            raise StoreError("Playが返したversionCodeまたはAABのハッシュが一致しません。")
        for locale in locales:
            folder = Path(release["metadata"]) / locale
            files = {"title": "name", "shortDescription": "short_description", "fullDescription": "full_description"}
            listing = {field: (folder / (file + ".txt")).read_text().strip() for field, file in files.items()}
            url = f"{base}/listings/{locale}"
            self.client.request(url, "PUT", {**listing, "language": locale})
            result = self.client.request(url)
            if any(result.get(key) != value for key, value in listing.items()):
                raise StoreError("Playから読み戻した原稿が一致しません。")
            for slot, image_type in {"phone": "phoneScreenshots", "feature": "featureGraphic"}.items():
                expected = [row for row in images if row["locale"] == locale and row["slot"] == slot]
                if not expected:
                    continue
                endpoint = f"{base}/listings/{locale}/{image_type}"
                previous = [row.get("sha256", "").lower() for row in self.client.request(endpoint).get("images", [])]
                hashes = [row["sha256"] for row in expected]
                if previous != hashes[:len(previous)]:
                    raise StoreError("Playの掲載枠に異なる画像があります。差分をPlay Consoleで確認してください。")
                for image in expected[len(previous):]:
                    path = Path(image["path"])
                    if digest(path) != image["sha256"]:
                        raise StoreError("掲載画像が照合後に変更されました。")
                    self.client.request("/upload" + endpoint, "POST", params={"uploadType": "media"},
                                        media=(path, "image/png"))
                observed = [row.get("sha256", "").lower() for row in self.client.request(endpoint).get("images", [])]
                if observed != hashes:
                    raise StoreError("Playから読み戻した画像または掲載順が一致しません。")
        track = release["track"]
        endpoint = f"{base}/tracks/{track}"
        value = self.client.request(endpoint)
        drafts = value.get("releases", [])
        expected = {"versionCodes": [release["build"]], "name": release["version"], "status": "draft"}
        if not drafts:
            self.client.request(endpoint, "PUT", {"track": track, "releases": [expected]})
        result = self.client.request(endpoint)
        rows = result.get("releases", [])
        if (result.get("track") != track or len(rows) != 1
                or any(rows[0].get(key) != field for key, field in expected.items())):
            raise StoreError("Playのトラックに別の配信があります。Play Consoleで差分を確認してください。")
        self.client.request(base + ":validate", "POST")
        return {"state": "uncommitted_edit_verified", "edit": edit, "track": track, "versionCode": release["build"]}

    def describe(self, action, release, remote):
        if action == "submit" and release.get("first"):
            detail = "初回審査の承認後は自動公開される可能性があります。公開条件も併せて確認してください。"
        else:
            detail = "Publishing overviewで対象版と、同時に送信される変更の範囲を確認してください。"
        raise StoreError("Play Consoleでの操作が必要です。" + detail)
