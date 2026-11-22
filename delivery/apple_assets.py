"""Populate and read back App Store version screenshot placements."""
from collections import defaultdict
import hashlib
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request

from stores import StoreError, get_one, object_data, ref


def placements(client, localization, group):
    return list(client.elements(f"/v1/appStoreVersionLocalizations/{localization}/placements",
                                {"filter[placementType]": "APP_SCREENSHOT", "filter[placementGroup]": group,
                                 "sort": "placementGroupPosition", "include": "image"}))


def image_id(placement):
    return placement.get("relationships", {}).get("image", {}).get("data", {}).get("id")


def upload(client, library, image):
    data = Path(image["path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != image["sha256"]:
        raise StoreError("掲載画像が確認後に変更されました。")
    reference = "StoreGate " + image["sha256"]
    matches = list(client.elements(f"/v1/appAssetLibraries/{library}/images", {"filter[referenceName]": reference}))
    if len(matches) > 1:
        raise StoreError("同一画像の予約が複数あります。Asset Libraryで確認してください。")
    if matches:
        entry = matches[0]
    else:
        entry = client.request("/v1/appAssetLibraryImages", "POST", object_data(
            "appAssetLibraryImages",
            attributes={"fileName": Path(image["path"]).name, "fileSize": len(data),
                        "category": "APP_SCREENSHOTS_AND_PREVIEWS", "referenceName": reference},
            relationships={"assetLibrary": ref("appAssetLibraries", library)}))["data"]
    identifier, attributes = entry["id"], entry["attributes"]
    path = f"/v1/appAssetLibraryImages/{identifier}"
    if attributes["state"] == "AWAITING_UPLOAD":
        attributes = client.request(path)["data"]["attributes"]
        if attributes["state"] == "AWAITING_UPLOAD":
            operations = attributes.get("uploadOperations", [])
            offset = 0
            for part in sorted(operations, key=lambda item: item["offset"]):
                start, count = part["offset"], part["length"]
                url = urlparse(part["url"])
                if (start != offset or count <= 0 or start + count > len(data) or part["method"] != "PUT"
                        or url.scheme != "https" or not url.hostname or not url.hostname.endswith(".blobstore.apple.com")
                        or url.username or url.password or url.port not in (443, None)):
                    raise StoreError("Appleの画像転送指示を照合できません。")
                request = Request(part["url"], data[start:start + count], method="PUT",
                                  headers={item["name"]: item["value"] for item in part["requestHeaders"]})
                try:
                    with client.opener.open(request, timeout=60):
                        pass
                except OSError:
                    raise StoreError("Appleへの画像転送結果を再確認してください。", uncertain=True) from None
                offset += count
            if offset != len(data):
                raise StoreError("Appleの画像転送サイズが一致しません。")
            client.request(path, "PATCH", object_data("appAssetLibraryImages", id=identifier, attributes={"uploaded": True}))
    attributes = client.request(path)["data"]["attributes"]
    if attributes["state"] not in {"PREPARE_FOR_SUBMISSION", "APPROVED"}:
        raise StoreError(f"Appleの画像処理状態は {attributes['state']} です。処理後に準備を再開してください。")
    actual = attributes.get("imageAsset") or {}
    if (actual.get("width"), actual.get("height")) != (image["width"], image["height"]):
        raise StoreError("Appleが処理した画像のサイズが一致しません。")
    return identifier, attributes["specId"]


def sync(client, app, localizations, target, images):
    groups = target.get("placement_groups", {})
    if not images or set(groups) != {image["slot"] for image in images} or len(set(groups.values())) != len(groups):
        raise StoreError("Appleの掲載枠とAsset Libraryの配置グループを照合してください。")
    catalog = get_one(client.elements("/v1/appAssetLibraryRefData", {"filter[features]": "APP_STORE_VERSIONS"}),
                      "Appleの掲載仕様を取得できません。")["attributes"]
    available = {item["groupId"] for item in catalog["placementProfileGroups"]} if all(
        "groupId" in item for item in catalog["placementProfileGroups"]) else set()
    # Different catalog revisions expose group identifiers in the placement type's mapping.
    placement = get_one((item for item in catalog["placementTypes"]
                         if item.get("placementType", item.get("type")) == "APP_SCREENSHOT"),
                        "Appleのスクリーンショット枠の仕様を一意に取得できません。")
    mappings = {item.get("placementGroup", item.get("groupId")): set(item["specs"]) for item in placement["specMappings"]}
    if any(group not in mappings or (available and group not in available) for group in groups.values()):
        raise StoreError("Appleの現行仕様に存在しない掲載枠が含まれています。")
    expected = defaultdict(list)
    image_specs = {item["specId"]: item for item in catalog["imageSpecs"]}
    plans = []
    for image in images:
        locale, group = image["locale"], groups[image["slot"]]
        specs = set()
        for key in mappings[group]:
            row = image_specs[key]
            dim = row["dimensions"]
            if ((dim["minWidth"], dim["minHeight"]) == (image["width"], image["height"])
                    and ".png" in row["fileExtensions"] and Path(image["path"]).stat().st_size <= row["maxFileSize"]):
                specs.add(key)
        if not specs:
            raise StoreError(f"Appleの掲載枠 {group} の画像仕様と一致しません。")
        plans.append((locale, group, image, specs))
    library = client.request(f"/v1/apps/{app}/assetLibrary")["data"]["id"]
    for locale, group, image, specs in plans:
        identifier, spec = upload(client, library, image)
        if spec not in specs:
            raise StoreError("Appleの画像処理結果と掲載枠の仕様が一致しません。")
        expected[locale, group].append(identifier)
    for (locale, group), ids in expected.items():
        localization = localizations[locale]
        relation = ref("appStoreVersionLocalizations", localization)
        current = placements(client, localization, group)
        present = {image_id(row): row["id"] for row in current}
        if set(present) - set(ids) or len(present) != len(current) or len(set(ids)) != len(ids):
            raise StoreError("同じ掲載枠に別の画像があります。App Store Connectで差分を確認してください。")
        for identifier in ids:
            if identifier not in present:
                row = client.request("/v1/appAssetLibraryPlacements", "POST", object_data(
                    "appAssetLibraryPlacements", attributes={"placementType": "APP_SCREENSHOT", "placementGroup": group},
                    relationships={"image": ref("appAssetLibraryImages", identifier),
                                   "appStoreVersionLocalization": relation}))["data"]
                present[identifier] = row["id"]
        if [image_id(row) for row in placements(client, localization, group)] != ids:
            client.request("/v1/appAssetLibraryPlacementOrderingRequests", "POST", object_data(
                "appAssetLibraryPlacementOrderingRequests", attributes={"placementGroup": group},
                relationships={"orderedPlacements": {"data": [
                    {"type": "appAssetLibraryPlacements", "id": present[identifier]} for identifier in ids
                ]}, "appStoreVersionLocalization": relation}))
        if [image_id(row) for row in placements(client, localization, group)] != ids:
            raise StoreError("Appleから読み戻した掲載順が一致しません。")
