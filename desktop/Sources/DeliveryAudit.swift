import CryptoKit
import Darwin
import Foundation

struct ListingDraft: Codable, Equatable, Sendable {
    var name = ""
    var subtitle = ""
    var description = ""
    var keywords = ""
    var privacyURL = ""
    var supportURL = ""
    var termsURL = ""
}

struct ListingFinding: Codable, Equatable, Sendable {
    let severity: String
    let rule: String
    let field: String
    let message: String
    let fix: String
    var location: String? = nil
    var detail: String? = nil
}

struct ListingReport: Codable, Equatable, Sendable {
    let findings: [ListingFinding]
    let checkedAt: Date
    var engineFingerprint: String? = nil
    var inputFingerprint: String? = nil

    var blocked: Bool { findings.contains { $0.severity == "critical" || $0.severity == "high" } }

    func current(for draft: ListingDraft) -> Bool {
        engineFingerprint == DeliveryAudit.listingEngineFingerprint
            && inputFingerprint == DeliveryAudit.fingerprint(draft)
    }
}

struct SourceReport: Codable, Equatable, Sendable {
    let findings: [ListingFinding]
    let fingerprint: String
    let complete: Bool
    let platform: String
    let fileCount: Int
    let issues: [String]
    var engineFingerprint: String? = nil
    var checkedAt: Date = .now
    var blocked: Bool { findings.contains { $0.severity == "critical" || $0.severity == "high" } }

    private enum CodingKeys: String, CodingKey {
        case findings, fingerprint, complete, platform, fileCount, issues, engineFingerprint, checkedAt
    }
    init(findings: [ListingFinding], fingerprint: String, complete: Bool, platform: String, fileCount: Int, issues: [String]) {
        self.findings = findings; self.fingerprint = fingerprint; self.complete = complete
        self.platform = platform; self.fileCount = fileCount; self.issues = issues
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        findings = try c.decode([ListingFinding].self, forKey: .findings)
        fingerprint = try c.decode(String.self, forKey: .fingerprint)
        complete = try c.decode(Bool.self, forKey: .complete)
        platform = try c.decode(String.self, forKey: .platform)
        fileCount = try c.decode(Int.self, forKey: .fileCount)
        issues = try c.decode([String].self, forKey: .issues)
        engineFingerprint = try c.decodeIfPresent(String.self, forKey: .engineFingerprint)
        checkedAt = try c.decodeIfPresent(Date.self, forKey: .checkedAt) ?? .now
    }
}

struct ApplicableRule: Codable, Equatable, Identifiable, Sendable {
    let id: String
    let title: String
    let check: String
    let revision: Int
    let blocksRelease: Bool
    let sourceVerified: Bool
    var sources: [String] = []
}

enum DeliveryAudit {
    private static let maxRequestBytes = 256 * 1024
    private static let maxResponseBytes = 1024 * 1024
    private static let toolResources = Bundle.main.resourceURL?
        .appending(path: "StoreGateTools", directoryHint: .isDirectory)

    static func fingerprint(_ draft: ListingDraft) -> String? {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(draft) else { return nil }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
    private static func engine(_ paths: [String]) -> String? {
        guard let toolResources else { return nil }
        var hash = SHA256()
        for path in paths {
            guard let data = try? Data(contentsOf: toolResources.appending(path: path)) else { return nil }
            hash.update(data: data)
        }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }
    static let listingEngineFingerprint = engine(["audit_bridge.py", "scripts/metadata-audit.py"])
    static let sourceEngineFingerprint = engine(["audit_bridge.py", "scripts/macos-source-audit.py", "scripts/validate-privacy-manifest.py"])

    static let registerFingerprint: String? = {
        guard let toolResources else { return nil }
        let paths = ["data/ruling-register.json", "scripts/ruling-register.py", "data/rejection-patterns.json"]
        var fingerprint = SHA256()
        for path in paths {
            guard let file = try? Data(contentsOf: toolResources.appending(path: path)) else { return nil }
            fingerprint.update(data: Data(SHA256.hash(data: file)))
        }
        return fingerprint.finalize().map { String(format: "%02x", $0) }.joined()
    }()

    static func checkListing(_ draft: ListingDraft) async throws -> ListingReport {
        let findings = try await request(.listing, input: draft, as: [ListingFinding].self)
        let severities = ["critical", "high", "medium", "low"]
        guard findings.allSatisfy({ severities.contains($0.severity) }) else {
            throw AuditError.invalidResponse
        }
        return ListingReport(findings: findings, checkedAt: Date(), engineFingerprint: listingEngineFingerprint, inputFingerprint: fingerprint(draft))
    }

    static func checkSource(folder: String) async throws -> SourceReport {
        var report = try await request(.source, input: ["folder": folder], as: SourceReport.self)
        guard report.findings.allSatisfy({ ["critical", "high", "medium", "low"].contains($0.severity) }) else { throw AuditError.invalidResponse }
        report.engineFingerprint = sourceEngineFingerprint
        return report
    }

    static func verifySource(_ project: DeliveryProject) async throws -> SourceReport {
        let latest = try await checkSource(folder: project.folder)
        guard latest.complete, !latest.blocked, latest.fingerprint == project.sourceReport?.fingerprint,
              latest.engineFingerprint == project.sourceReport?.engineFingerprint else { throw AuditError.sourceChanged }
        return latest
    }

    static func applicableRules(regions: [String], features: [String]) async throws -> [ApplicableRule] {
        try await request(.rules, input: RuleScope(regions: regions, features: features), as: [ApplicableRule].self)
    }

    private struct RuleScope: Encodable, Sendable {
        let regions: [String]
        let features: [String]
    }

    private enum Command: String, Sendable {
        case listing, rules, source
    }

    private enum AuditError: LocalizedError {
        case missingResource, missingRuntime, invalidInput, invalidRegister, invalidResponse, timedOut, unavailable, sourceChanged

        var errorDescription: String? {
            switch self {
            case .missingResource: "監査ツールが見つかりません。StoreGateを入れ直してください。"
            case .missingRuntime: "Python 3を起動できません。Xcodeのコマンドラインツールを確認してください。"
            case .invalidInput: "監査する入力の形式や長さを確認してください。"
            case .invalidRegister: "同梱の審査ルールを読み取れません。StoreGateを入れ直してください。"
            case .invalidResponse: "監査ツールから結果を取得できませんでした。StoreGateを入れ直してください。"
            case .timedOut: "監査が時間内に終わりませんでした。もう一度お試しください。"
            case .unavailable: "監査を実行できませんでした。もう一度お試しください。"
            case .sourceChanged: "ソースが監査時から変わったか、確認が終わっていません。「適用ルールと実機」でソース監査と確認をやり直してください。"
            }
        }
    }

    private static func request<Output: Decodable & Sendable>(
        _ command: Command, input: some Encodable & Sendable, as type: Output.Type
    ) async throws -> Output {
        try Task.checkCancellation()
        let data = try JSONEncoder().encode(input)
        guard data.count <= maxRequestBytes else { throw AuditError.invalidInput }
        let resource = toolResources?.appending(path: "audit_bridge.py")
        guard let bridge = resource, FileManager.default.isReadableFile(atPath: bridge.path) else {
            throw AuditError.missingResource
        }

        let worker = Task.detached(priority: .userInitiated) {
            try runBridge(bridge, command: command, input: data)
        }
        let output = try await withTaskCancellationHandler {
            try await worker.value
        } onCancel: {
            worker.cancel()
        }
        try Task.checkCancellation()
        do {
            return try JSONDecoder().decode(type, from: output)
        } catch {
            throw AuditError.invalidResponse
        }
    }

    private static func runBridge(_ bridge: URL, command: Command, input: Data) throws -> Data {
        try Task.checkCancellation()
        let fileManager = FileManager.default
        let folder = fileManager.temporaryDirectory.appending(path: UUID().uuidString, directoryHint: .isDirectory)
        try fileManager.createDirectory(at: folder, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? fileManager.removeItem(at: folder) }

        let inputURL = folder.appending(path: "input.json")
        try input.write(to: inputURL)
        let stdin = try FileHandle(forReadingFrom: inputURL)
        defer { try? stdin.close() }
        let stdout = try emptyFile(at: folder.appending(path: "output.json"))
        defer { try? stdout.close() }
        let stderr = try emptyFile(at: folder.appending(path: "error"))
        defer { try? stderr.close() }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = ["-I", "-B", bridge.path, command.rawValue]
        process.environment = ["PATH": "/usr/bin:/bin", "LC_ALL": "C"]
        process.currentDirectoryURL = folder
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr

        let finished = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in finished.signal() }
        do {
            try process.run()
        } catch {
            throw AuditError.missingRuntime
        }

        let deadline = DispatchTime.now() + .seconds(15)
        while finished.wait(timeout: .now() + .milliseconds(50)) == .timedOut {
            if Task.isCancelled || DispatchTime.now() >= deadline {
                if process.isRunning { process.terminate() }
                if finished.wait(timeout: .now() + .milliseconds(250)) == .timedOut, process.isRunning {
                    _ = kill(process.processIdentifier, SIGKILL)
                    _ = finished.wait(timeout: .now() + .milliseconds(250))
                }
                try Task.checkCancellation()
                throw AuditError.timedOut
            }
        }
        try Task.checkCancellation()

        if process.terminationStatus != 0 {
            let size = try stderr.seekToEnd()
            try stderr.seek(toOffset: size > 512 ? size - 512 : 0)
            let detail = String(decoding: try stderr.read(upToCount: 512) ?? Data(), as: UTF8.self)
            switch detail.split(whereSeparator: \.isNewline).last {
            case "INVALID_INPUT": throw AuditError.invalidInput
            case "INVALID_REGISTER": throw AuditError.invalidRegister
            case "AUDIT_ERROR": throw AuditError.unavailable
            default: throw AuditError.invalidResponse
            }
        }

        try stdout.seek(toOffset: 0)
        guard let result = try stdout.read(upToCount: maxResponseBytes + 1), !result.isEmpty,
              result.count <= maxResponseBytes else { throw AuditError.invalidResponse }
        return result
    }

    private static func emptyFile(at url: URL) throws -> FileHandle {
        guard FileManager.default.createFile(atPath: url.path, contents: nil) else { throw AuditError.unavailable }
        return try FileHandle(forUpdating: url)
    }
}
