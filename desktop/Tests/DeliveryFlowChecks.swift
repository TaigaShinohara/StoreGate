import Foundation

@main
struct DeliveryFlowChecks {
    @MainActor
    static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("delivery-workspace.json")
        let missing = try DeliveryWorkspace.read(file)
        precondition(missing.projects.isEmpty)
        var project = DeliveryProject(name: "Fixture Notes", folder: "/tmp/fixture/project", bundleID: "com.example.fixture")
        precondition(project.activeStep == .scope)
        var scope = DeliveryScope()
        scope.regions = "JP, US"
        scope.version = "2.1"
        scope.build = "42"
        project.setScope(scope)
        precondition(project.activeStep == .account)
        project.accountWait = "Waiting for the account holder"
        precondition(project.activeStep == .listing)
        precondition(!project.advance(.processing, note: "upload", observed: project.identity))

        let empty = project.listing
        project.recordReport(ListingReport(findings: [
            ListingFinding(severity: "high", rule: "TEST", field: "privacy_url", message: "Missing", fix: "Enter URL"),
        ], checkedAt: .now), for: empty, locale: "ja")
        precondition(project.listingBlocked)
        var draft = empty
        draft.privacyURL = "https://example.org/privacy"
        project.setListing(draft, locale: "ja")
        precondition(project.listingReport == nil)
        let withoutFindings = ListingReport(findings: [], checkedAt: .now)
        project.recordReport(withoutFindings, for: empty, locale: "ja")
        precondition(project.listingReport == nil)
        project.recordReport(withoutFindings, for: draft, locale: "ja")
        project.setListing(ListingDraft(), locale: "en-US")
        precondition(project.listingBlocked)
        project.recordReport(withoutFindings, for: ListingDraft(), locale: "en-US")
        precondition(!project.listingBlocked)
        project.setNotes("Open the Notes screen and create a note")
        project.setCheck(.storefront, note: "Both languages and screenshots match", notApplicable: false)
        project.setStoreAppID("12345")
        project.setCheck(.account, note: "App and access verified", notApplicable: false)
        precondition(project.accountWait.isEmpty)
        precondition(project.activeStep == .archive)

        let wrong = BuildIdentity(bundleID: "com.example.other", version: "2.1", build: "42")
        project.recordArchive(archive(wrong))
        precondition(project.archive == nil)
        project.recordArchive(archive(project.identity, valid: false))
        precondition(!project.hasMatchingArchive)
        project.setCheck(.runtime, note: "This must be refused", notApplicable: false)
        precondition(project.checks[.runtime] == nil)
        project.recordArchive(archive(project.identity))
        for check in [DeliveryCheck.source, .privacy, .runtime, .distribution, .storefront] {
            project.setCheck(check, note: "Verified evidence", notApplicable: false)
        }
        project.setCheck(.reviewAccess, note: "No login", notApplicable: true)
        project.setCheck(.purchases, note: "No in-app purchases", notApplicable: true)
        project.setRules([], for: scope)
        precondition(project.uploadBlockers.isEmpty)
        let originalArchive = project.archive
        project.recordArchive(archive(project.identity))
        precondition(project.archive != originalArchive && project.checks[.runtime] != nil)
        project.rulesCheckedAt = .distantPast
        precondition(project.uploadBlockers.contains(.checks))
        project.setRules([], for: scope)
        precondition(project.activeStep == .review)
        precondition(!project.advance(.processing, note: "wrong", observed: wrong))
        precondition(project.advance(.processing, note: "Uploaded through Xcode", observed: project.identity))
        var replaced = project
        replaced.recordArchive(archive(replaced.identity, fingerprint: "different signed artifact"))
        precondition(replaced.phase == .upload && replaced.conflictsWithUploadedBuild)
        precondition(replaced.activeStep == .archive && replaced.uploadBlockers.contains(.archive))
        precondition(!replaced.advance(.submission, note: "Existing build shown in the console", observed: replaced.identity))
        replaced.replaceBuild("43")
        replaced.recordArchive(archive(replaced.identity, fingerprint: "different signed artifact"))
        precondition(!replaced.conflictsWithUploadedBuild)

        var legacy = project
        legacy.records[legacy.records.count - 1].archiveFingerprint = nil
        legacy.recordArchive(archive(legacy.identity, fingerprint: "different signed artifact"))
        precondition(legacy.conflictsWithUploadedBuild)
        precondition(!project.advance(.approved, note: "not reviewed", observed: project.identity))
        precondition(project.advance(.submission, note: "Processed, build 42 selectable", observed: project.identity))
        precondition(project.advance(.reviewQueue, note: "Waiting for Review", observed: project.identity))

        var external = DeliveryProject(name: "External", folder: "/tmp/project", bundleID: "com.example.fixture")
        external.setScope(scope)
        external.setStoreAppID("12345")
        precondition(!external.observeExistingStatus(.approved, note: "wrong app", observed: wrong))
        precondition(external.observeExistingStatus(.approved, note: "Pending Developer Release", observed: external.identity))
        precondition(external.observedBeforeReadiness && external.records.count == 1)

        var workspace = DeliveryWorkspace(projects: [project], lastProject: project.id, savedAt: .now)
        try workspace.write(file)
        let resumed = try DeliveryWorkspace.read(file)
        precondition(resumed.projects.first?.phase == .reviewQueue)
        precondition(resumed.projects.first?.storeAppID == "12345")
        precondition(resumed.lastProject == project.id)

        var binaryRevision = project
        binaryRevision.requestRevision(note: "Use the production server", areas: [.binary, .privacy])
        precondition(binaryRevision.phase == .revision && binaryRevision.archive == nil)
        precondition(binaryRevision.checks[.source] == nil && binaryRevision.checks[.privacy] == nil)
        precondition(binaryRevision.listingReport != nil)
        precondition(binaryRevision.revisions.last?.note == "Use the production server")
        binaryRevision.replaceBuild("43")
        precondition(binaryRevision.identity.build == "43")

        var textRevision = project
        textRevision.requestRevision(note: "Correct the description", areas: [.text])
        precondition(textRevision.archive != nil && textRevision.checks[.runtime] != nil)
        precondition(textRevision.listingReport == nil && textRevision.localeReports.isEmpty)

        precondition(project.advance(.inReview, note: "In Review", observed: project.identity))
        precondition(project.advance(.approved, note: "Pending Developer Release", observed: project.identity))
        let jp = "https://apps.apple.com/jp/app/fixture-notes/id12345"
        let us = "https://apps.apple.com/us/app/fixture-notes/id12345"
        precondition(!project.confirmPublication(region: "JP", version: "2.1", url: jp))
        precondition(project.advance(.propagation, note: "Released; checking storefronts", observed: project.identity))
        precondition(!project.confirmPublication(region: "JP", version: "2.0", url: jp))
        precondition(!project.confirmPublication(region: "JP", version: "2.1", url: us))
        precondition(!project.confirmPublication(region: "JP", version: "2.1", url: jp.replacingOccurrences(of: "12345", with: "456")))
        precondition(project.confirmPublication(region: "JP", version: "2.1", url: jp) && project.phase == .propagation)
        precondition(project.confirmPublication(region: "US", version: "2.1", url: us) && project.phase == .published)
        project.reopenPublication("US")
        precondition(project.publications.count == 1 && project.phase == .propagation)

        workspace.projects = [project]
        let firstSavedAt = workspace.savedAt
        workspace.savedAt = .now.addingTimeInterval(1)
        try workspace.write(file, expecting: firstSavedAt, checkRevision: true)
        precondition(DeliveryWorkspace.hasBackup(file))
        do {
            try workspace.write(file, expecting: firstSavedAt, checkRevision: true)
            preconditionFailure("Overwrote a newer workspace")
        } catch DeliveryWorkspace.WriteError.conflict { }
        let bad = Data("broken JSON".utf8)
        try bad.write(to: file)
        do {
            try workspace.write(file)
            preconditionFailure("Overwrote unreadable original")
        } catch DecodingError.dataCorrupted { }
        let recovered = try DeliveryWorkspace.restore(file)
        precondition(recovered.projects.first?.phase == .reviewQueue)
        let preserved = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .first { $0.pathExtension.hasPrefix("unreadable-") }
        let preservedData = try preserved.map { try Data(contentsOf: $0) }
        precondition(preservedData == bad)

        try FileManager.default.removeItem(at: file)
        do {
            _ = try DeliveryWorkspace.read(file)
            preconditionFailure("Missing primary was treated as a new workspace")
        } catch DeliveryWorkspace.WriteError.primaryMissing { }
        do {
            try DeliveryWorkspace().write(file)
            preconditionFailure("Overwrote recoverable workspace with an empty file")
        } catch DeliveryWorkspace.WriteError.primaryMissing { }
        let missingPrimaryRestored = try DeliveryWorkspace.restore(file)
        precondition(missingPrimaryRestored.projects.first?.phase == .reviewQueue)

        try verifyConflictingSave(at: root.appendingPathComponent("conflict.json"))
        try await verifyArchiveLimits(at: root)
        try await verifySignedArchive(at: root)
        print("Delivery workspace: prerequisites, stale audits, revisions, regional publication, restart, recovery, and archive limits passed.")
    }

    @MainActor
    static func verifyConflictingSave(at url: URL) throws {
        let writer = DeliveryStore(url: url)
        precondition(writer.add(name: "Shared", folder: "/tmp/project", bundleID: "com.example.project"))
        let id = writer.workspace.projects[0].id
        let other = DeliveryStore(url: url)
        precondition(writer.update(id) { $0.reviewNotes = "Newer original" })
        precondition(!other.update(id) { $0.reviewNotes = "Unsaved alternate" })
        other.retrySave()
        precondition(other.saveProblem != nil)
        precondition(other.preservePendingAndReload())
        precondition(other.workspace.projects[0].reviewNotes == "Newer original")
        guard let copy = other.recoveryCopy else { preconditionFailure("No recovery copy") }
        let recovered = try DeliveryWorkspace.read(copy)
        precondition(recovered.projects[0].reviewNotes == "Unsaved alternate")
        precondition(other.saveProblem == nil)
    }

    static func verifyArchiveLimits(at root: URL) async throws {
        do {
            _ = try ArchiveInspection.run("/bin/sleep", ["5"], deadline: .now() + .milliseconds(50))
            preconditionFailure("A tool exceeded its deadline")
        } catch ArchiveInspection.ToolError.timedOut { }
        do {
            _ = try ArchiveInspection.run("/usr/bin/head", ["-c", "4096", "/dev/zero"],
                                          deadline: .now() + .seconds(5), limit: 512)
            preconditionFailure("Accepted oversized tool output")
        } catch ArchiveInspection.ToolError.tooMuchOutput { }
        let oversized = root.appendingPathComponent("Oversized.xcarchive")
        try FileManager.default.createDirectory(at: oversized, withIntermediateDirectories: true)
        try Data(count: 4 * 1024 * 1024 + 1).write(to: oversized.appendingPathComponent("Info.plist"))
        do {
            _ = try await ArchiveInspection.inspect(oversized)
            preconditionFailure("Accepted an oversized archive descriptor")
        } catch {
            precondition(error.localizedDescription.contains("大きすぎ"))
        }
    }

    static func verifySignedArchive(at root: URL) async throws {
        let archive = root.appendingPathComponent("Signed fixture.xcarchive")
        let app = archive.appendingPathComponent("Products/Applications/Fixture.app")
        let contents = app.appendingPathComponent("Contents")
        let programs = contents.appendingPathComponent("MacOS")
        try FileManager.default.createDirectory(at: programs, withIntermediateDirectories: true)
        try FileManager.default.copyItem(at: URL(fileURLWithPath: "/usr/bin/true"), to: programs.appendingPathComponent("Fixture"))

        let identity = BuildIdentity(bundleID: "com.example.storegatefixture", version: "1.0", build: "1")
        let identityFields = ["CFBundleIdentifier": identity.bundleID, "CFBundleShortVersionString": identity.version, "CFBundleVersion": identity.build]
        var manifest = identityFields
        manifest["ApplicationPath"] = "Applications/Fixture.app"
        try writePlist(["ApplicationProperties": manifest], to: archive.appendingPathComponent("Info.plist"))
        var appInfo = identityFields
        appInfo["CFBundleExecutable"] = "Fixture"
        appInfo["CFBundlePackageType"] = "APPL"
        try writePlist(appInfo, to: contents.appendingPathComponent("Info.plist"))
        let entitlements = root.appendingPathComponent("fixture-entitlements.plist")
        try writePlist(["com.apple.security.app-sandbox": true], to: entitlements)
        let signature = try ArchiveInspection.run(
            "/usr/bin/codesign", ["--force", "--sign", "-", "--entitlements", entitlements.path, app.path],
            includeErrors: true, deadline: .now() + .seconds(10)
        )
        precondition(signature.status == 0, String(decoding: signature.output, as: UTF8.self))
        let result = try await ArchiveInspection.inspect(archive)
        precondition(result.identity == identity && result.signatureValid && result.sandboxEnabled && result.fingerprint != nil,
                     result.diagnostic)
    }

    static func writePlist(_ fields: [String: Any], to url: URL) throws {
        let data = try PropertyListSerialization.data(fromPropertyList: fields, format: .xml, options: 0)
        try data.write(to: url)
    }

    static func archive(_ identity: BuildIdentity, valid: Bool = true, fingerprint: String = "fixture") -> ArchiveInspection {
        ArchiveInspection(identity: identity, archiveName: "Fixture.xcarchive", path: "/tmp/Fixture.xcarchive",
                          signatureValid: valid, sandboxEnabled: valid, checkedAt: .now, diagnostic: "",
                          fingerprint: valid ? fingerprint : nil)
    }
}
