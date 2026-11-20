"""Offline JSON adapter for the StoreGate desktop app's bundled audit tools."""

import datetime as dt
import importlib.util
import json
from pathlib import Path
import re
import resource
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024

LISTING_FIELDS = {
    "name": "name",
    "subtitle": "subtitle",
    "description": "description",
    "keywords": "keywords",
    "privacyURL": "privacy_url",
    "supportURL": "support_url",
    "termsURL": "terms_url",
}


class BridgeError(Exception):
    pass


def bundled_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    if spec is None or spec.loader is None:
        raise BridgeError("AUDIT_ERROR")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_listing(payload):
    if payload.keys() != LISTING_FIELDS.keys() or not all(isinstance(v, str) for v in payload.values()):
        raise BridgeError("INVALID_INPUT")
    metadata = bundled_module("storegate_metadata", "metadata-audit.py")
    fields = {target: payload[key] for key, target in LISTING_FIELDS.items()}
    return metadata.audit_fields(fields, check_urls=False, platform="apple", require_listing=True)


def usable_source(url):
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and not re.search(r"[\s\x00-\x1f\x7f]", url)
        )
    except ValueError:
        return False


def applicable_rules(payload):
    if payload.keys() != {"regions", "features"} or not all(
        isinstance(items, list) and all(isinstance(v, str) for v in items) for items in payload.values()
    ):
        raise BridgeError("INVALID_INPUT")
    regions = {region.strip().upper() for region in payload["regions"]}
    features = set(payload["features"])
    register = bundled_module("storegate_register", "ruling-register.py")
    if (
        not regions
        or any(not register.COUNTRY.fullmatch(code) for code in regions)
        or features - set(register.FEATURES)
    ):
        raise BridgeError("INVALID_INPUT")

    try:
        document = register.load(register.REGISTER)
        errors = register.validate(document, register.pattern_ids())
    except SystemExit:
        raise BridgeError("INVALID_REGISTER") from None
    if errors:
        raise BridgeError("INVALID_REGISTER")

    sources = {row["id"]: row["sources"] for row in document["requirements"]}
    rows = register.due(document, regions, {"apple"}, features, dt.date.today())
    return [
        dict(
            id=row["id"],
            title=row["title"],
            check=row["check"],
            revision=row["revision"],
            blocksRelease=row["blocks_release"],
            sourceVerified=row["source_verified"],
            sources=list(dict.fromkeys(
                source["url"] for source in sources[row["id"]] if usable_source(source["url"])
            )),
        )
        for row in rows
    ]


COMMANDS = {"listing": check_listing, "rules": applicable_rules}


def main():
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESPONSE_BYTES, MAX_RESPONSE_BYTES))
        if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
            raise BridgeError("INVALID_INPUT")
        data = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(data) > MAX_REQUEST_BYTES:
            raise BridgeError("INVALID_INPUT")
        try:
            payload = json.loads(data)
        except (ValueError, RecursionError) as exc:
            raise BridgeError("INVALID_INPUT") from exc
        if not isinstance(payload, dict):
            raise BridgeError("INVALID_INPUT")
        response = json.dumps(COMMANDS[sys.argv[1]](payload), ensure_ascii=True).encode("ascii")
        if len(response) > MAX_RESPONSE_BYTES:
            raise BridgeError("AUDIT_ERROR")
        sys.stdout.buffer.write(response)
        return 0
    except BridgeError as exc:
        print(exc, file=sys.stderr)
    except Exception:
        # Avoid including input, file paths or environment contents in diagnostics.
        print("AUDIT_ERROR", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
