import Foundation

@main
struct ReleaseFlowChecks {
    static func main() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let state = root.appendingPathComponent("saved/progress.json")
        var progress = try ReleaseProgress.load(from: state)
        precondition(progress.next == .scope)
        precondition(!progress.confirm(.review))

        for step in [ReleaseStep.scope, .account, .source, .listing] {
            precondition(progress.confirm(step))
        }
        precondition(progress.next == .archive && !progress.confirm(.archive))
        try progress.save(to: state)
        progress = try .load(from: state)
        precondition(progress.next == .archive)

        let wrongID = BuildIdentity(bundleID: "com.example.other", version: "1.0", build: "1")
        let wrongVersion = BuildIdentity(bundleID: BuildIdentity.sample.bundleID, version: "2.0", build: "1")
        let wrongBuild = BuildIdentity(bundleID: BuildIdentity.sample.bundleID, version: "1.0", build: "2")
        let archive = root.appendingPathComponent("Test Archive.xcarchive")
        try FileManager.default.createDirectory(at: archive, withIntermediateDirectories: true)
        for expected in [wrongID, wrongVersion, wrongBuild, .sample] {
            let plist = ["ApplicationProperties": [
                "CFBundleIdentifier": expected.bundleID,
                "CFBundleShortVersionString": expected.version,
                "CFBundleVersion": expected.build,
            ]]
            let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .binary, options: 0)
            try data.write(to: archive.appendingPathComponent("Info.plist"))
            let parsed = try BuildIdentity.readArchive(at: archive)
            precondition(parsed == expected)
            precondition(parsed.differences(from: .sample).count == (expected == .sample ? 0 : 1))
            precondition(progress.confirmArchive(parsed, name: archive.lastPathComponent) == (expected == .sample))
        }
        precondition(progress.next == .review && progress.confirm(.review))

        var listingRevision = progress
        listingRevision.requestChanges(newBuild: false)
        precondition(listingRevision.next == .listing && listingRevision.archiveName != nil)
        precondition(listingRevision.confirm(.listing) && listingRevision.next == .review)

        var buildRevision = progress
        buildRevision.requestChanges(newBuild: true)
        precondition(buildRevision.next == .archive && buildRevision.archiveName == nil)
        precondition(buildRevision.completed.contains(.listing))
        precondition(buildRevision.expectedArchive.build == "2")
        precondition(!buildRevision.confirmArchive(.sample, name: "Old"))
        precondition(buildRevision.confirmArchive(buildRevision.expectedArchive, name: "New"))

        precondition(progress.confirm(.publication) && progress.next == nil)
        try progress.save(to: state)
        let restored = try ReleaseProgress.load(from: state)
        precondition(restored == progress)

        let malformed = root.appendingPathComponent("Malformed.xcarchive")
        try FileManager.default.createDirectory(at: malformed, withIntermediateDirectories: true)
        try Data("invalid plist".utf8).write(to: malformed.appendingPathComponent("Info.plist"))
        do {
            _ = try BuildIdentity.readArchive(at: malformed)
            preconditionFailure("Accepted a malformed archive")
        } catch {
            print("Release flow checks passed: order, persistence, archive identity, and review revisions.")
        }
    }
}
