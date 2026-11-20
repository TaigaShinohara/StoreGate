import Foundation

@main
struct DeliveryFlowChecks {
    static func main() throws {
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
        print("Delivery workspace: prerequisites, stale audits, revisions, regional publication, restart, and recovery passed.")
    }

    static func archive(_ identity: BuildIdentity, valid: Bool = true) -> ArchiveInspection {
        ArchiveInspection(identity: identity, archiveName: "Fixture.xcarchive", path: "/tmp/Fixture.xcarchive",
                          signatureValid: valid, sandboxEnabled: valid, checkedAt: .now, diagnostic: "",
                          fingerprint: valid ? "fixture" : nil)
    }
}
