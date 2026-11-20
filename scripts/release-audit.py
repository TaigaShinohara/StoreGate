#!/usr/bin/env python3
"""Release readiness audit. Usage. release-audit.py <app-dir> [--metadata <pulled-listing-dir>]
Exit 2 blocked on a critical or high finding, 3 incomplete (including manual review), 1 bad input.
This report does not import manual evidence and cannot certify a release as ready."""

import os
import sys
import subprocess
import json
import re
import html

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD_NAME = "storegate-guard.sh"
BLOCKING = ("critical", "high")
LISTING_HINT = "Store listing not audited. Pull it with scripts/pull-metadata.sh, then pass --metadata <dir>."
MANUAL_REVIEW_HINT = (
    "Manual evidence is not imported by this report. Re-running scanners cannot complete manual review. "
    "For a single app, record applicable checks and evidence in docs/PRE-SUBMISSION-CHECKLIST.md; "
    "Fleet Sweep is an optional record of owner actions and attestations, not a replacement for verifying them."
)

# 13 Required areas
REQUIRED_AREAS = [
    "Apple requirements",
    "Google Play requirements",
    "Web requirements",
    "Privacy",
    "Security",
    "Accessibility",
    "AI regulations",
    "Store metadata",
    "Permissions",
    "Legal documentation",
    "SDK compatibility",
    "Deprecated APIs",
    "Platform announcements",
]

# Recommended reviewers for each area
RECOMMENDED_REVIEWERS = {
    "Apple requirements": "Mobile Tech Lead, iOS Platform Architect",
    "Google Play requirements": "Mobile Tech Lead, Android Platform Architect",
    "Web requirements": "Frontend Technical Lead, Web Architect",
    "Privacy": "Data Protection Officer (DPO), Legal Counsel (Privacy)",
    "Security": "Product Security Engineering Team, DevSecOps Lead",
    "Accessibility": "Frontend QA Team, Accessibility Specialist",
    "AI regulations": "AI Ethics and Governance Committee, Lead AI Architect",
    "Store metadata": "Product Marketing Manager (PMM), App Store Optimization (ASO) Specialist",
    "Permissions": "Lead Developer, Mobile Platform Leads",
    "Legal documentation": "Legal Counsel (Commercial/IP), Compliance Officer",
    "SDK compatibility": "Lead Mobile Developer, Architecture Review Board",
    "Deprecated APIs": "Lead Developer, Tech Debt/Platform Team",
    "Platform announcements": "Lead Developer, Mobile Release Manager",
}

# These are scope questions and evidence requests, not additional automated checks.
MANUAL_CHECKS = {
    "Apple requirements": ("Apple specific", "For an iOS release, verify the submitted build on a real device, review access, age-rating answers, export declarations, and App Store Connect account state."),
    "Google Play requirements": ("Google Play specific", "For an Android release, verify the submitted build on a real device, Play Console declarations, tester/account requirements, and the release track."),
    "Web requirements": ("Platform mechanics gate (both stores, if the item applies)", "Confirm whether web or wrapped web flows apply; exercise those flows in the released product and record the applicable platform checks."),
    "Privacy": ("Privacy and data", "Compare actual data collection and SDK behavior with consent, the privacy policy, Apple privacy labels and Play Data Safety for the platforms shipped."),
    "Security": ("Mobile Security and Best Practices", "Review the release configuration, transport, authentication, secrets and data handling against the shipped build."),
    "Accessibility": ("Platform mechanics gate (both stores, if the item applies)", "Test applicable accessibility behavior on devices, including screen readers, text scaling and contrast; record results and relevant legal scope."),
    "AI regulations": ("EU specific (legal layer, if the app reaches EU users)", "Establish AI features and distribution regions; verify applicable consent, AI notices, content safeguards and age restrictions in the product."),
    "Store metadata": ("Metadata and listing", "Compare every submitted locale, screenshot, preview, URL and claim with the actual build; URL reachability needs --check-urls or a documented manual check."),
    "Permissions": ("Privacy and data", "Exercise permission requests on devices and confirm each declared permission and purpose string matches a necessary user-facing feature."),
    "Legal documentation": ("EU specific (legal layer, if the app reaches EU users)", "Establish distribution regions, audience and product features; verify applicable legal documents, trader/child-privacy declarations, terms and export obligations, including Global specific checks."),
    "SDK compatibility": ("Platform mechanics gate (both stores, if the item applies)", "Confirm applicable SDK, billing, framework and privacy-manifest requirements against the dependencies and behavior of the built release."),
    "Deprecated APIs": ("Platform mechanics gate (both stores, if the item applies)", "Review build diagnostics and dependencies for deprecated or prohibited APIs that static source scanning may not see."),
    "Platform announcements": ("Account and program readiness (added September 2026)", "Check current official platform notices and console/account requirements for the platforms and release date in scope."),
}

# Manual mapping of specific patterns to areas
MAP_PATTERNS_TO_AREAS = {
    "APPLE-2.1-MISSING-DEMO-ACCOUNT": ["Apple requirements"],
    "APPLE-2.1-PLACEHOLDER-CONTENT": ["Apple requirements", "Store metadata"],
    "APPLE-2.1-STAGING-BACKEND": ["Apple requirements", "Security"],
    "APPLE-5.1.1-MISSING-PRIVACY-POLICY": ["Apple requirements", "Privacy"],
    "APPLE-5.1.1-VAGUE-PURPOSE-STRING": ["Apple requirements", "Permissions"],
    "APPLE-5.1.1-MISSING-USAGE-DESCRIPTION": ["Apple requirements", "Permissions"],
    "APPLE-5.1.1-NO-ACCOUNT-DELETION": ["Apple requirements", "Privacy"],
    "APPLE-5.1.2-MISSING-ATT": ["Apple requirements", "Privacy"],
    "APPLE-3.1.1-EXTERNAL-PAYMENT": ["Apple requirements", "SDK compatibility"],
    "APPLE-4.8-SOCIAL-LOGIN-ONLY": ["Apple requirements", "Privacy"],
    "APPLE-4.2-WEB-WRAPPER": ["Apple requirements", "Web requirements"],
    "APPLE-2.5.1-PRIVATE-API": ["Apple requirements", "Deprecated APIs", "Security"],
    "APPLE-2.3-CROSS-PLATFORM-REFERENCE": ["Apple requirements", "Store metadata"],
    "APPLE-2.3-AGE-RATING-2026": ["Apple requirements", "Platform announcements"],
    "APPLE-5.1.2-AI-NO-CONSENT-MODAL": [
        "Apple requirements",
        "AI regulations",
        "Privacy",
    ],
    "GOOGLE-DATASAFETY-MISMATCH": ["Google Play requirements", "Privacy"],
    "GOOGLE-PERM-BACKGROUND-LOCATION": ["Google Play requirements", "Permissions"],
    "GOOGLE-PERM-ALL-FILES": ["Google Play requirements", "Permissions"],
    "GOOGLE-PERM-SMS-CALLLOG": ["Google Play requirements", "Permissions"],
    "GOOGLE-PERM-ACCESSIBILITY-MISUSE": ["Google Play requirements", "Accessibility"],
    "GOOGLE-TARGET-API": ["Google Play requirements", "Platform announcements"],
    "GOOGLE-12-TESTER-RULE": ["Google Play requirements", "Platform announcements"],
    "GOOGLE-PLAY-BILLING": ["Google Play requirements", "SDK compatibility"],
    "GOOGLE-MISSING-PRIVACY-POLICY": ["Google Play requirements", "Privacy"],
    "GOOGLE-MISLEADING-LISTING": ["Google Play requirements", "Store metadata"],
    "GOOGLE-FAMILIES-AD-SDK": ["Google Play requirements", "SDK compatibility"],
    "BOTH-SDK-SUPPLY-CHAIN": ["SDK compatibility"],
    "BOTH-LOOTBOX-ODDS": ["Legal documentation"],
    "APPLE-PRIVACY-MANIFEST-MISSING": ["Apple requirements", "Privacy"],
    "APPLE-EXPORT-COMPLIANCE-MISSING": ["Apple requirements", "Legal documentation"],
    "APPLE-RESTORE-PURCHASES-MISSING": ["Apple requirements"],
    "APPLE-ACCOUNT-DELETION-WEAK": ["Apple requirements", "Privacy"],
    "ANDROID-DYNAMIC-CODE-LOADING": ["Google Play requirements", "Security"],
    "ANDROID-QUERY-ALL-PACKAGES": ["Google Play requirements", "Permissions"],
    "ANDROID-OVERLAY-TAPJACKING": ["Google Play requirements", "Security"],
    "ANDROID-ACCOUNT-DELETION-URL": ["Google Play requirements", "Privacy"],
    "BOTH-AI-GENERATED-CONTENT": ["AI regulations"],
    "BOTH-METADATA-DECORATION": ["Store metadata"],
    "BOTH-FINGERPRINTING": ["Privacy", "Security"],
    "APPLE-2.3-FUTURE-FUNCTIONALITY": ["Apple requirements", "Store metadata"],
    "APPLE-2.3-NEGATIVE-APPLE-SENTIMENT": ["Apple requirements", "Store metadata"],
    "BOTH-UNREACHABLE-METADATA-URL": ["Store metadata"],
    "APPLE-5.2.5-APPLE-DEVICE-IMAGE": ["Apple requirements", "Store metadata"],
    "APPLE-2.3.4-DEVICE-FRAMES-PREVIEW": ["Apple requirements", "Store metadata"],
    "APPLE-3.1.2-MISLEADING-PRICING": ["Apple requirements", "Store metadata"],
    "APPLE-1.2-UGC-24H-ACTION": ["Apple requirements", "Legal documentation"],
    "CHINA-AI-REFERENCES": ["AI regulations"],
    "APPLE-2.4.5-UNUSED-ENTITLEMENTS": ["Apple requirements"],
    "APPLE-4.0-SIWA-UX": ["Apple requirements"],
    "APPLE-5.1.1-UNNECESSARY-DATA": ["Apple requirements", "Privacy"],
    "APPLE-2.1-DEBUG-FEATURES": ["Apple requirements", "Security"],
    "APPLE-2.1-CLOUD-NOT-IN-PRODUCTION": ["Apple requirements"],
    "APPLE-2.1-REVIEW-NOTES-INCOMPLETE": ["Apple requirements", "Store metadata"],
    "BOTH-INVALID-METADATA-URL": ["Store metadata"],
    "APPLE-MISSING-SUPPORT-URL": ["Apple requirements", "Store metadata"],
    "GOOGLE-MISSING-CONTACT-EMAIL": ["Google Play requirements", "Store metadata"],
    "BOTH-MISSING-REQUIRED-METADATA": ["Store metadata"],
}


def resolve_guard():
    """Find the guard. APP_STORE_GUARD_PATH wins, then the repo layout, then an installed skill.
    Returns (path, None) or (None, reason)."""
    env = os.environ.get("APP_STORE_GUARD_PATH", "").strip()
    if env:
        if os.path.isfile(env):
            return os.path.abspath(env), None
        return None, f"APP_STORE_GUARD_PATH points at {env}, which is not a file"
    candidates = [
        os.path.join(ROOT, "agent-os", "hooks", GUARD_NAME),
        # installed. <config>/skills/app-store-compliance/scripts -> <config>/hooks
        os.path.normpath(os.path.join(ROOT, "..", "..", "hooks", GUARD_NAME)),
    ]
    # shared skill in ~/.agents. the installer records each tool's guard path in .guards
    try:
        with open(os.path.join(ROOT, ".guards"), encoding="utf-8") as f:
            candidates += [ln.rstrip("\n").partition("\t")[2] for ln in f if "\t" in ln]
    except OSError:
        pass
    for c in candidates:
        if c and os.path.isfile(c):
            return c, None
    return None, "looked in " + " and ".join(candidates)


def scan_problems(name, code, out, err, ok_codes=(0, 2)):
    """Reasons a scanner run cannot be trusted as a complete check. Empty means it ran fine."""
    problems = []
    text = (out or "") + "\n" + (err or "")
    if code not in ok_codes:
        problems.append(f"{name} exited {code}")
    for marker in ("NOT CHECKED", "Nothing was audited", "Coverage. incomplete", "Partial field audit"):
        if marker in text:
            line = next((ln.strip() for ln in text.splitlines() if marker in ln), marker)
            problems.append(f"{name} did not check everything. {line}")
    return problems


def tail(text, n=8):
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[-n:]


def decide_status(findings, problems):
    """Blockers take priority; manual evidence remains unverified even after complete scans."""
    if any(f["severity"] in BLOCKING for f in findings):
        return "BLOCKED", 2
    return "INCOMPLETE", 3


def run_command(args, cwd=ROOT):
    try:
        res = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
        return res.returncode, res.stdout, res.stderr
    except Exception as e:
        return -1, "", str(e)


DEADLINE_LINE_RE = re.compile(r"\(mandatory \d{4}-\d{2}-\d{2}\)")

FINDING_SHAPE_RE = re.compile(r"^\s*\[\w+\]\s+[A-Z0-9]+(-[A-Z0-9.]+)+\s")


def is_deadline_line(line):
    """A deadline-checker line carries a date after the word mandatory. A finding does not."""
    if FINDING_SHAPE_RE.match(line):
        return False
    return bool(DEADLINE_LINE_RE.search(line))


def load_patterns():
    path = os.path.join(ROOT, "data", "rejection-patterns.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {p["id"]: p for p in data.get("patterns", [])}
        except Exception:
            pass
    return {}


def get_areas_for_pattern(pid, patterns_dict):
    if pid in MAP_PATTERNS_TO_AREAS:
        return MAP_PATTERNS_TO_AREAS[pid]

    pdata = patterns_dict.get(pid, {})
    platform = pdata.get("platform", "").lower()

    areas = []
    if platform == "apple":
        areas.append("Apple requirements")
    elif platform == "google":
        areas.append("Google Play requirements")
    elif platform == "web":
        areas.append("Web requirements")
    elif platform == "both":
        areas.append("Apple requirements")
        areas.append("Google Play requirements")

    title_lower = pdata.get("title", "").lower() + " " + pid.lower()

    if (
        "privacy" in title_lower
        or "data-safety" in title_lower
        or "tracking" in title_lower
        or "fingerprinting" in title_lower
    ):
        areas.append("Privacy")
    if (
        "security" in title_lower
        or "staging" in title_lower
        or "backend" in title_lower
        or "private-api" in title_lower
        or "overlay" in title_lower
        or "dynamic" in title_lower
    ):
        areas.append("Security")
    if "accessibility" in title_lower:
        areas.append("Accessibility")
    if (
        "ai" in title_lower
        or "openai" in title_lower
        or "gemini" in title_lower
        or "claude" in title_lower
    ):
        areas.append("AI regulations")
    if (
        "metadata" in title_lower
        or "placeholder" in title_lower
        or "future-func" in title_lower
        or "unreachable" in title_lower
    ):
        areas.append("Store metadata")
    if "perm" in title_lower or "usage-description" in title_lower:
        areas.append("Permissions")
    if "billing" in title_lower or "payment" in title_lower or "sdk" in title_lower:
        areas.append("SDK compatibility")

    if not areas:
        areas.append("Apple requirements")
        areas.append("Google Play requirements")

    return list(set(areas))


def find_affected_files(target_dir, patterns_dict):
    affected = {}
    exclude_dirs = {
        "node_modules",
        "Pods",
        ".git",
        "build",
        "DerivedData",
        "vendor",
        ".dart_tool",
        "Carthage",
        "androidTest",
        "__tests__",
    }
    allowed_exts = {
        ".swift",
        ".m",
        ".h",
        ".kt",
        ".java",
        ".xml",
        ".plist",
        ".gradle",
        ".kts",
        ".json",
        ".js",
        ".ts",
        ".dart",
        ".xcconfig",
        ".pbxproj",
        ".entitlements",
        ".md",
    }

    for root, dirs, files in os.walk(target_dir):
        # modify dirs in place to prune excluded dirs
        dirs[:] = [
            d
            for d in dirs
            if d not in exclude_dirs
            and not d.endswith("Tests")
            and not d.endswith("Tests__")
        ]

        for file in files:
            ext = os.path.splitext(file)[1]
            if ext not in allowed_exts:
                continue

            filepath = os.path.join(root, file)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue

            for pid, pdata in patterns_dict.items():
                signals = pdata.get("signals", [])
                counter_signals = pdata.get("counterSignals", [])

                if not signals:
                    continue

                # BOTH-PLACEHOLDER needs a refined regex; the JSON signal is a plain word.
                has_signal = False
                if pid == "BOTH-PLACEHOLDER":
                    # Check custom regexes or simple substrings
                    ph_regex = r'lorem ipsum|example\.(com|org)|YOUR_[A-Z_]+_(KEY|HERE)|INSERT_[A-Z_]+_HERE|dummy (text|content|data)|(john|jane)@example|"Acme( Inc| Corp)?"'
                    if re.search(ph_regex, content, re.IGNORECASE):
                        has_signal = True
                else:
                    for sig in signals:
                        if sig in content:
                            has_signal = True
                            break

                if has_signal:
                    has_counter = False
                    for csig in counter_signals:
                        if csig in content:
                            has_counter = True
                            break

                    if not has_counter:
                        rel_path = os.path.relpath(filepath, target_dir)
                        if pid not in affected:
                            affected[pid] = []
                        # Avoid adding the tool's own definition files if possible, unless they are the target
                        if (
                            "rejection-patterns.json" not in rel_path
                            and "release-audit.py" not in rel_path
                            and "storegate-guard.sh" not in rel_path
                            and "RELEASE-READINESS-REPORT.md" not in rel_path
                        ):
                            affected[pid].append(rel_path)

    return affected


def main():
    args = sys.argv[1:]
    meta_dir = None
    if "--metadata" in args:
        i = args.index("--metadata")
        meta_dir = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        if not os.path.isdir(meta_dir):
            print(f"release-audit. --metadata '{meta_dir}' is not a directory.", file=sys.stderr)
            return 1
        meta_dir = os.path.abspath(meta_dir)
    target_dir = ROOT
    if args:
        if not os.path.isdir(args[0]):
            print(f"release-audit. '{args[0]}' is not a directory.", file=sys.stderr)
            return 1
        target_dir = os.path.abspath(args[0])

    guard_path, guard_missing = resolve_guard()
    if not guard_path:
        print(f"release-audit. compliance guard not found, {guard_missing}.", file=sys.stderr)
        print("Set APP_STORE_GUARD_PATH to the guard script, or reinstall with scripts/install.sh.", file=sys.stderr)
        return 1

    print("== Starting Release Readiness Compliance Audit ==")
    print(f"Target Directory: {target_dir}")
    print(f"Guard. {guard_path}")
    print(f"Store listing. {meta_dir or target_dir + ' (pass --metadata to audit a pulled listing)'}")
    print("")

    # --- Step 1. Run Internal Validation and Test Engines ---
    print("Step 1 of 2. Checking the playbook itself first. This takes about two minutes.", flush=True)

    val_code, val_out, val_err = run_command(["python3", "scripts/validate.py"])
    if val_code != 0:
        print("  ERROR: validate.py failed")
        print(val_out)
        print(val_err)
        return 1

    guard_test = os.path.join(ROOT, "agent-os", "hooks", "storegate-guard-test.sh")
    if os.path.isfile(guard_test):
        guard_test_code, guard_test_out, guard_test_err = run_command(["bash", guard_test])
        if guard_test_code != 0:
            print("  ERROR: compliance guard tests failed")
            print(guard_test_out)
            print(guard_test_err)
            return 1
    else:
        print("  The guard's own tests ship only with the repository, skipped in this installed copy.")

    meta_test_code, meta_test_out, meta_test_err = run_command(
        ["bash", "scripts/metadata-audit-test.sh"]
    )
    if meta_test_code != 0:
        print("  ERROR: metadata audit tests failed")
        print(meta_test_out)
        print(meta_test_err)
        return 1

    print("Internal validation and test engines passed successfully.")
    print("")

    # --- Step 2. Execute Compliance Scanners ---
    print("Step 2 of 2. Scanning your app...", flush=True)

    # Run the compliance guard
    guard_code, guard_out, guard_err = run_command(["bash", guard_path, target_dir])

    # Run metadata-audit.py
    # The pulled listing from --metadata when given, otherwise the app folder itself
    meta_code, meta_out, meta_err = run_command(
        ["python3", "scripts/metadata-audit.py", meta_dir or target_dir]
    )

    patterns_dict = load_patterns()
    findings = parse_findings(guard_out + "\n" + meta_out)

    problems = scan_problems("compliance guard", guard_code, guard_out, guard_err)
    listing_missing = meta_dir is None and "Nothing was audited" in meta_out + meta_err
    if listing_missing:
        problems.append(LISTING_HINT)
    else:
        problems += scan_problems("metadata audit", meta_code, meta_out, meta_err)
    if guard_code == 2 and not any(f["severity"] in BLOCKING for f in parse_findings(guard_out)):
        problems.append("compliance guard exited 2 but reported no critical or high finding")
    if problems:
        print("")
        print("Scan problems. These checks remain incomplete even when a finding also blocks the release.")
        for p in problems:
            print(f"  - {p}")
        for name, out, err in (("compliance guard", guard_out, guard_err), ("metadata audit", meta_out, meta_err)):
            if name in " ".join(problems):
                for ln in tail(err) or tail(out):
                    print(f"    {name}. {ln}")
        if listing_missing:
            seen = next((ln.strip() for ln in (meta_out + meta_err).splitlines() if "Nothing was audited" in ln), "")
            print(f"    metadata audit. {seen}")
        print("")

    # Programmatically scan for affected files
    affected_files_map = find_affected_files(target_dir, patterns_dict)
    return compile_report(target_dir, findings, problems, patterns_dict, affected_files_map)


def parse_findings(all_scanner_stdout):
    findings = []
    seen = set()
    lines = all_scanner_stdout.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if is_deadline_line(line):
            i += 1
            continue
        match = re.match(
            r"^\s*\[(CRITICAL|HIGH|MEDIUM|LOW)\]\s+([A-Z0-9-._]+)\s+(.+)$",
            line,
            re.IGNORECASE,
        )
        # every real finding id is hyphenated (APPLE-5.1.1-..., BOTH-PLACEHOLDER); a bare word is prose
        if match and "-" not in match.group(2):
            match = None
        if match:
            sev = match.group(1).lower()
            pid = match.group(2)
            header = match.group(3).strip()
            metadata = re.fullmatch(r"\(([^,]+),\s*(.+)\)", header)
            field, locale = (metadata.group(1).strip(), metadata.group(2).strip()) if metadata else ("", "")
            message = [] if metadata else [header]
            fix, file = [], []
            # Both scanners indent details. Metadata puts its message between the
            # header and fix; guard may put a file there. Keep every part.
            j = i + 1
            current = message
            while j < len(lines) and lines[j][:1].isspace():
                detail = lines[j].strip()
                if re.match(r"\[(CRITICAL|HIGH|MEDIUM|LOW)\]\s", detail, re.IGNORECASE):
                    break
                tagged = re.match(r"(file|fix)\.\s*(.*)$", detail, re.IGNORECASE)
                if tagged:
                    current = file if tagged.group(1).lower() == "file" else fix
                    current.append(tagged.group(2))
                else:
                    current.append(detail)
                j += 1
            text = "\n".join(message)
            finding = {
                "id": pid, "severity": sev, "title": text, "message": text,
                "fix": "\n".join(fix), "field": field, "locale": locale, "file": "\n".join(file),
            }
            # Only identical occurrences are duplicates. The same rule in another
            # file, locale, field, or with another message still needs remediation.
            key = tuple(finding[k] for k in ("id", "severity", "message", "fix", "field", "locale", "file"))
            if key not in seen:
                seen.add(key)
                findings.append(finding)
            i = j - 1
        i += 1
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(findings, key=lambda f: order[f["severity"]])


def markdown_cell(value):
    """Keep untrusted scanner text inside one Markdown table cell."""
    text = str("" if value is None else value).replace("\r\n", "\n").replace("\r", "\n")
    return html.escape(text, quote=False).replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def static_status(findings):
    if any(f["severity"] in BLOCKING for f in findings):
        return "BLOCKED"
    return "ADVISORY" if findings else "NO_STATIC_FINDINGS"


def compile_report(target_dir, findings, problems, patterns_dict, affected_files_map):
    # --- Step 3. Compile Report and Map to 13 Areas ---
    area_findings = {area: [] for area in REQUIRED_AREAS}
    overall_status, exit_code = decide_status(findings, problems)
    has_blocking = any(f["severity"] in BLOCKING for f in findings)

    for f in findings:
        pid = f["id"]
        areas = get_areas_for_pattern(pid, patterns_dict)
        for area in areas:
            if area in area_findings:
                # Add finding to this area's list
                area_findings[area].append(f)

    # Compile the Markdown report text (Strictly NO EMOJIS or emoticons)
    report_lines = []
    report_lines.append("# Release Readiness Compliance Report")
    report_lines.append("")
    report_lines.append(f"Target Directory: {target_dir}")

    report_lines.append(f"Overall Compliance Status: {overall_status}")
    report_lines.append("")

    report_lines.append("## Executive Summary")
    if has_blocking:
        report_lines.append(
            "The release is currently BLOCKED due to one or more critical or high compliance issues that must be resolved before submitting to the platforms."
        )
    else:
        report_lines.append(
            "The audit is INCOMPLETE. No critical or high finding was reported, but manual review evidence remains unverified. This scanner summary cannot approve submission."
        )
    report_lines.append("")
    report_lines.append(MANUAL_REVIEW_HINT)
    report_lines.append("")
    report_lines.append("## Incomplete Checks")
    report_lines.append("")
    if problems:
        for p in problems:
            report_lines.append(f"- {markdown_cell(p)}")
    else:
        report_lines.append("- Automated scans reported no execution or coverage problems. This does not verify manual evidence.")
    report_lines.append("- Manual review: REVIEW_REQUIRED for applicable areas. Determine scope from the checklist; record evidence or a reason an item does not apply. Applicability has not been established by this report.")
    report_lines.append("")

    report_lines.append("## Compliance Summary Table")
    report_lines.append("")
    report_lines.append("Static results describe only reported findings. If a scan was incomplete, absence of findings provides no coverage assurance. Manual review is tracked separately below.")
    report_lines.append("")
    report_lines.append("| Area | Static Results | Manual Review | Risks Found | Recommended Reviewers |")
    report_lines.append("| --- | --- | --- | --- | --- |")

    for area in REQUIRED_AREAS:
        area_f = area_findings[area]
        num_risks = len(area_f)
        reviewers = RECOMMENDED_REVIEWERS.get(area, "Lead Developer")
        cells = (area, static_status(area_f), "REVIEW_REQUIRED", num_risks, reviewers)
        report_lines.append("| " + " | ".join(markdown_cell(c) for c in cells) + " |")
    report_lines.append("")

    report_lines.append("## Detailed Compliance Analysis")
    report_lines.append("")

    for idx, area in enumerate(REQUIRED_AREAS, 1):
        area_f = area_findings[area]

        report_lines.append(f"### {idx}. {area}")
        report_lines.append(f"- Static results: {static_status(area_f)}")
        report_lines.append("- Manual review: REVIEW_REQUIRED (confirm applicability and record evidence or a reason the check does not apply).")
        report_lines.append(
            f"- Recommended Reviewers: {RECOMMENDED_REVIEWERS.get(area, 'Lead Developer')}"
        )
        section, action = MANUAL_CHECKS[area]
        checklist = os.path.join(ROOT, "docs", "PRE-SUBMISSION-CHECKLIST.md")
        report_lines.append(f"- Next check: {action}")
        report_lines.append(f"- Checklist: [docs/PRE-SUBMISSION-CHECKLIST.md](<{checklist}>) — {section}.")
        report_lines.append("")

        if not area_f:
            report_lines.append("No static findings were reported for this area; manual checks and applicability remain unverified.")
            report_lines.append("")
            continue

        report_lines.append(
            "| Finding ID | Severity | Description | Required Action | Field | Locale | Affected Files |"
        )
        report_lines.append("| --- | --- | --- | --- | --- | --- | --- |")

        for af in area_f:
            pid = af["id"]
            sev = af["severity"].upper()
            title = af.get("message") or af.get("title", "")
            fix = af["fix"] or "Refer to guidelines for remediation."

            # A scanner's occurrence location is authoritative. The heuristic
            # fallback supplies context only when the scanner gave no location.
            aff_files = [af["file"]] if af.get("file") else affected_files_map.get(pid, [])
            if not af.get("file") and (af.get("field") or af.get("locale")):
                aff_files = []
            if not aff_files:
                files_str = "None detected (Config/Listing check)"
            else:
                # Limit to first 5 paths to keep the table clean
                files_str = "\n".join(aff_files[:5])
                if len(aff_files) > 5:
                    files_str += f"\n... and {len(aff_files) - 5} more files"

            cells = (pid, sev, title, fix, af.get("field", ""), af.get("locale", ""), files_str)
            report_lines.append("| " + " | ".join(markdown_cell(c) for c in cells) + " |")
        report_lines.append("")

    report_lines.append("## Complete the Manual Review")
    report_lines.append("")
    report_lines.append(MANUAL_REVIEW_HINT)
    report_lines.append("")
    fleet_docs = os.path.join(ROOT, "docs", "FLEET-SWEEP.md")
    report_lines.append(f"See [docs/FLEET-SWEEP.md](<{fleet_docs}>) and templates/fleet.example.json if a fleet record is useful. Replace sample values with actual app scope, owners and verified evidence, then run python3 scripts/fleet-sweep.py fleet.json. Its result reflects its configured checks and declarations; this report does not import or verify that evidence.")
    report_lines.append("After fixing static findings or missing inputs, re-run this audit. A single-app checklist review can be completed separately; the automated report will still return INCOMPLETE without a blocker because it has no manual-evidence import.")

    # Write report file into the audited target, not this playbook's own root
    report_path = os.path.join(target_dir, "RELEASE-READINESS-REPORT.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")

    print(f"Release readiness report generated successfully at: {report_path}")
    print(
        f"Summary: critical={sum(1 for f in findings if f['severity'] == 'critical')} high={sum(1 for f in findings if f['severity'] == 'high')} medium={sum(1 for f in findings if f['severity'] == 'medium')} low={sum(1 for f in findings if f['severity'] == 'low')}"
    )
    print(f"Overall Status: {overall_status}")

    if overall_status == "BLOCKED":
        print("Release is BLOCKED. Resolve the critical and high issues.")
    else:
        print("Release is NOT APPROVED by this report. Manual review remains required; see Incomplete Checks in the report.")
    if problems:
        print("Incomplete checks also remain. See Incomplete Checks in the report.")
    print(MANUAL_REVIEW_HINT)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
