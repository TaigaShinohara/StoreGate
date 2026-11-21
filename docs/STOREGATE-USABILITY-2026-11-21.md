# StoreGate Desktop usability fixes — 2026-11-21

## Scope and result

The seven issues reviewed against `5bf2807` are addressed in the desktop workflow and its shared guard. This is a local prototype verification record, not approval to submit StoreGate to either store.

| Priority | Issue | Result |
| --- | --- | --- |
| 1 | File selection blocked registration in automated interaction | Folder and archive paths can be pasted or dropped. Existing directories and local file URLs are validated before use. The standard picker remains available. Empty-state registration and archive inspection succeeded using pasted paths. The original picker automation failure is not established as fixed. |
| 2 | No desktop source scan; Mac sources treated as iOS | A bounded, offline Mac scanner is connected to the desktop and guard. It distinguishes macOS SDK configuration, avoids treating a file URL host check as an HTTP endpoint, recognizes Japanese policy links, and excludes old build outputs. Unknown, mixed or unreadable scope does not become completed desktop evidence. Mixed repositories retain the generic checks and explicitly report incomplete Mac scope. |
| 3 | Basic project information could not be corrected | Name, folder and Bundle ID are editable before submission. A name-only change preserves evidence. Folder or identity changes invalidate archive-dependent evidence; a Bundle ID change also clears the stored App Store identity and account check. |
| 4 | Unsupported locales accepted; accidental additions could not be removed | Apple-supported metadata locales are selected from a list and normalized. Additional locales have a confirmation showing the locale and draft app name before removal. The primary locale cannot be deleted this way. |
| 5 | Historical completion expired the following day | Submitted and published records retain their recorded rule/listing evidence. Editable projects still require current rules. History does not need an unavailable editing action to remain complete. |
| 6 | Old listing results survived checker changes | Listing reports store input and bundled-engine SHA-256 fingerprints. An editable project must be inspected again when either changes. Legacy reports remain readable but cannot satisfy a current preparation gate. |
| 7 | English CLI guidance shown directly in Japanese UI | A Japanese String Catalog maps metadata rule IDs to reasons and actions for the editor. Original messages remain available in a disclosure. Mac source findings also show Japanese guidance and retain validator detail. |

Source reports also carry the inspected-file and engine fingerprints. Reinspection invalidates a source attestation when the inspected content changes, and handoff/observation actions rerun the source check before accepting the existing evidence.

The locale list was checked against Apple's [App Store localizations](https://developer.apple.com/help/app-store-connect/reference/app-information/app-store-localizations) and [metadata locale shortcodes](https://developer.apple.com/documentation/appstoreconnectapi/managing-metadata-in-your-app-by-using-locale-shortcodes) on 2026-11-21. No policy deadlines or regulatory claims were changed.

## Verification

- Native model checks: `bash desktop/build.sh --test`; covers state invalidation, historical completion, legacy/stale listing evidence, locale normalization/removal, path validation, persistence and existing archive gates.
- Native application: `bash desktop/build.sh`; Swift 6 compilation, Japanese catalog compilation and local ad-hoc signing.
- Mac scanner/catalog checks: `python3 scripts/test-macos-source-audit.py`; 10 regression cases, including a mixed Mac/Android scope, HTTP versus file URLs, debug/comment exclusion, Japanese policy references, malformed manifests, capacity limits and guard routing.
- Shared guard and installer suites, metadata audit, Fleet Sweep, documentation checks and data validation are required before merging this change. Test totals are recorded in the pull request and task record.
- Installer regression: the same owned guard remains recognized through a symlinked HOME path, including macOS temporary-directory aliases. A nearby unregistered guard is still rejected by the existing ownership checks.

GUI checks used a separate application Bundle ID and an initially empty workspace under the ignored build directory. They did not preload a project into storage:

1. Register an existing OneClip Mac project by pasting its source folder.
2. Save version/build/region/language and execute the source audit: 39 files, no critical/high findings in that inspected source scope.
3. Paste an existing OneClip Store archive path: identity matched, static signature valid, and signed Sandbox enabled for each inspected architecture.
4. Edit the project name and confirm archive evidence remains.
5. Inspect an empty listing and read the Japanese reasons/actions with original detail available.
6. Add `en-US`, remove it after confirmation, and return to the primary Japanese draft.
7. Quit and relaunch; confirm the project name and settings persist and the unverified account gate remains incomplete.

The GUI checks establish the pasted-path workflow. Native picker selection, actual pointer-driven drag-and-drop and VoiceOver interaction were not verified. No App Store upload, submission or publication was performed.

## Store submission review of StoreGate itself

The repository's automated guard was run against StoreGate after these changes. It reported `critical=0 high=2 medium=0`, with macOS source coverage. The guard's exit status and `CLEAR` line concern critical source findings only; they do not override the high findings below or establish store readiness. No real StoreGate listing was pulled, so the listing audit is not applicable to this record; its behavior was exercised through fixtures and desktop draft inspection.

| Severity | Finding | Evidence / required action |
| --- | --- | --- |
| High | No verifiable in-app privacy-policy reference | `APPLE-5.1.1-MISSING-PRIVACY-POLICY`. StoreGate's instructional text about other apps is not a policy link for StoreGate. Provide and verify its own published policy and in-app route before store submission. |
| High | Required-reason API use without a bundled manifest | `APPLE-PRIVACY-MANIFEST-MISSING`; archive inspection uses file attributes. Determine the applicable reason and bundle the target's declaration before submission. |
| High for distribution | Only a locally ad-hoc signed prototype was built | `desktop/build.sh` uses ad-hoc signing. Distribution signing, Sandbox entitlement suitability, Python runtime packaging, notarization when applicable, and store validation remain release work. |
| Unverified | Actual listing, legal declarations and accessibility | No StoreGate store listing or console declarations were inspected. VoiceOver, contrast and text scaling need actual interaction. |

All 15 mandatory review areas were mapped against the [pre-submission checklist](PRE-SUBMISSION-CHECKLIST.md). The following describes evidence for this native Mac prototype, with conditional items explicitly left for the applicable distribution scope:

| Area | Mapping | Current evidence / boundary |
| --- | --- | --- |
| 1. Permissions | Privacy and data; privacy/android rules; guard | No sensitive usage-description permission or new entitlement is added. The app reads user-selected local projects and archives. Distribution Sandbox behavior remains unverified. |
| 2. Privacy disclosures | Privacy and data; privacy rules | Workspace data is saved locally. No tracking/analytics SDK or upload path was found in the native sources. Own policy and manifest are still high findings; declarations must match final packaging/runtime. |
| 3. Screenshots | Metadata and listing; metadata rules | No StoreGate submission screenshots or creative assets were supplied or certified. Capture current actual use for a later listing review. |
| 4. Metadata | Metadata and listing; metadata auditor | No actual StoreGate listing was fetched. Fixture audits and Japanese editor guidance were tested; actual names, limits and claims remain unchecked. |
| 5. Age rating | Apple specific; global guide | No console questionnaire inspected. Content/age answers remain a submission gate. |
| 6. AI disclosures | EU and Global specific | The native app runs deterministic local inspection helpers; it has no generative-AI/provider integration in this scope. Reassess if such a feature is added. |
| 7. Subscriptions | Monetization; metadata auditor | No subscription or paywall implementation in the native app. Conditional disclosures are not exercised. |
| 8. Payments | Monetization; platform mechanics | No StoreKit or third-party payment flow in the native app. No digital-goods purchase was tested. |
| 9. Accessibility | EU specific; platform mechanics | New inputs have labels and use native controls. VoiceOver, scaling, contrast and applicable standards are not certified by this source review. |
| 10. Legal documents | EU and Global guides | Store territories, trader/content-rights declarations and any applicable child/privacy obligations need console and product review before distribution. |
| 11. Support URL | Shared; metadata auditor with URL checking | No StoreGate listing support URL was verified for reachability or support response. |
| 12. Privacy policy | Privacy and data; privacy rules | High finding above. A static link for another project or instruction text cannot satisfy StoreGate's policy gate. |
| 13. Terms of service | Monetization; platform mechanics | No subscription/UGC flow found. The final distribution model and required license/terms links remain to be reviewed. Bundled third-party license/notice files are preserved. |
| 14. Export and encryption | Apple specific; export rules | `desktop/Info.plist` does not declare `ITSAppUsesNonExemptEncryption`. No export or France declaration was verified; classification must follow final binary/runtime and territories. |
| 15. Cross-platform frameworks | Cross-platform guide; guard | Desktop is native SwiftUI. No Flutter/React Native/Ionic app dependency was added. Mixed target repositories are not certified by the Mac-only scanner. |

The rest of the checklist was reviewed by category: completeness and review access have only the local interactions above; monetization/UGC/account deletion/login conditions are not present in the native app; Apple developer agreements, storefront declarations and distribution mechanics are unverified; Google Play items have no Android StoreGate target. Region-specific items remain conditional on the final territories and product scope. No release is certified while the high findings or required manual evidence remain unresolved.

## Static scan limits

The Mac helper inspects bounded source/configuration files without executing the project's build system. It excludes generated/dependency/test directories and does not resolve target membership, prove runtime consent, inspect a signed binary's SDK inventory or establish URL reachability. It rejects incomplete evidence, but a completed scan means only that its stated source scope was read. Manual and store evidence remain independent gates.
