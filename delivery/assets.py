"""Read a versioned tiger_screen_shot export and verify the submission files."""
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path

PNG = b"\x89PNG\r\n\x1a\n"
SLOTS = {
    "ios": {"screenshots/apple/ipad": "ipad", "screenshots/apple/iphone": "iphone",
            "screenshots/apple/iphone-medium": "iphone-medium"},
    "android": {"screenshots/google-play/phone": "phone", "screenshots/google-play/feature": "feature",
                "promo/google-play": "feature", "icon/google-play": "icon"},
    "macos": {"screenshots/apple/mac": "mac"},
}
PLAY_COUNTS = {"phone": (2, 8), "feature": (1, 1), "icon": (1, 1)}
ORDER = re.compile(r"-(\d+)(?:-|$)")


def inspect_png(data):
    if data[:8] != PNG:
        raise ValueError("PNG signature")
    offset = 8
    dimensions = None
    pixels = False
    alpha = False
    while offset + 12 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, offset)
        end = offset + 12 + length
        if end > len(data) or zlib.crc32(data[offset + 4:end - 4]) != struct.unpack_from(">I", data, end - 4)[0]:
            raise ValueError("PNG chunk")
        if offset == 8:
            if kind != b"IHDR" or length != 13:
                raise ValueError("PNG header")
            width, height, depth, color = struct.unpack_from(">IIBB", data, offset + 8)
            if not width or not height or depth != 8 or color not in {0, 2, 3, 4, 6}:
                raise ValueError("PNG dimensions or color")
            dimensions = (width, height)
            alpha = color in {4, 6}
        if kind == b"tRNS":
            alpha = True
        if kind == b"IDAT":
            pixels |= length > 0
        if kind == b"IEND" and length == 0 and end == len(data) and pixels:
            return (*dimensions, color, alpha)
        offset = end
    raise ValueError("incomplete PNG")


def inspect_images(pack, platform, locales, default_locale, expected_slots):
    if not pack:
        return [], ["素材パックを登録してください。"]
    if platform == "android":
        expected_slots = {**{slot: limits[0] for slot, limits in PLAY_COUNTS.items()}, **expected_slots}
    root = Path(pack).resolve()
    try:
        manifest = json.loads((root / "07_submission/image-manifest.json").read_text())
        items = manifest["images"]
        if not isinstance(items, list) or manifest.get("count", len(items)) != len(items):
            raise ValueError()
    except (OSError, KeyError, TypeError, ValueError):
        return [], ["画像マニフェストを読み込めません。"]
    images, problems, seen = [], [], set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("file"), str):
            problems.append("画像マニフェストに不正な項目があります。")
            continue
        relative = Path(item["file"])
        parts = relative.parts
        base_length = 4 if parts[1:2] == ("screenshots",) else 3
        if parts[:1] != ("02_images",) or len(parts) not in (base_length + 1, base_length + 2):
            problems.append("掲載画像のパスが不正です。")
            continue
        folder = "/".join(parts[1:base_length])
        slot = SLOTS[platform].get(folder)
        if not slot:
            if not any(folder in slots for slots in SLOTS.values()):
                problems.append(f"未登録の掲載枠です。{folder}")
            continue
        match = ORDER.search(relative.stem)
        locale = item.get("locale", parts[base_length] if len(parts) == base_length + 2 else default_locale)
        order = item.get("order", int(match[1]) if match else (1 if slot in {"feature", "icon"} else 0))
        try:
            path = (root / relative).resolve()
            key = (locale, slot, order)
            if (".." in parts or not path.is_relative_to(root)
                    or locale not in locales or type(order) is not int or not 1 <= order <= 10 or key in seen):
                raise ValueError()
            limit = 1024 * 1024 if slot == "icon" else 8 * 1024 * 1024
            if path.suffix.lower() != ".png" or path.stat().st_size > limit:
                raise ValueError()
            data = path.read_bytes()
            width, height, color, alpha = inspect_png(data)
            digest = hashlib.sha256(data).hexdigest()
            if ((width, height) != (item.get("width"), item.get("height"))
                    or len(data) != item.get("bytes") or digest != item.get("sha256")):
                raise ValueError()
            if slot == "icon":
                if (width, height, color) != (512, 512, 6):
                    raise ValueError()
            elif alpha or (platform == "android" and color != 2):
                raise ValueError()
            if slot == "feature" and (width, height) != (1024, 500):
                raise ValueError()
            if slot == "phone":
                short, long = sorted((width, height))
                if short < 320 or long > 3840 or long * 10 > short * 23:
                    raise ValueError()
        except (OSError, TypeError, ValueError):
            problems.append(f"画像の形式・寸法・順序・ハッシュを確認できません。{relative.name}")
            continue
        seen.add(key)
        images.append(dict(path=str(path), slot=slot, locale=locale, order=order,
                           width=width, height=height, bytes=len(data), sha256=digest))
    for locale in locales:
        for slot, count in expected_slots.items():
            orders = sorted(order for lang, frame, order in seen if lang == locale and frame == slot)
            if orders != list(range(1, count + 1)):
                problems.append(f"{locale} / {slot} の掲載順または枚数を確認してください（予定 {count} 枚）。")
    return sorted(images, key=lambda image: (image["locale"], image["slot"], image["order"])), problems
