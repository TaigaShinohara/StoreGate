import CoreFoundation
import Foundation

struct ArchiveInspection: Codable, Equatable, Sendable {
    let identity: BuildIdentity
    let archiveName: String
    let path: String
    let signatureValid: Bool
    let sandboxEnabled: Bool
    let checkedAt: Date
    let diagnostic: String
    var fingerprint: String? = nil

    static func inspect(_ url: URL) async throws -> Self {
        let work = Task.detached(priority: .utility) { try inspectLocally(url) }
        return try await withTaskCancellationHandler {
            try await work.value
        } onCancel: {
            work.cancel()
        }
    }

    private static let scope = "このアーカイブをローカルで照合しました。Mac App Store用の署名資格、アップロードしたビルド、実行時の動作は別途確認してください。"

    private static func inspectLocally(_ url: URL) throws -> Self {
        try Task.checkCancellation()
        guard url.isFileURL, url.pathExtension.lowercased() == "xcarchive" else {
            throw InspectionError(message: "Xcodeで作成した .xcarchive を選んでください。")
        }

        let archive = url.standardizedFileURL
        try requireType(.typeDirectory, at: archive)
        let root = archive.resolvingSymlinksInPath()
        let manifest = try plist(at: root.appendingPathComponent("Info.plist"))
        guard let properties = manifest["ApplicationProperties"] as? [String: Any] else {
            throw InspectionError(message: "アーカイブの Info.plist に ApplicationProperties がありません。")
        }
        let summary = try buildIdentity(in: properties, label: "アーカイブ")
        let relativePath = try string("ApplicationPath", in: properties, label: "アーカイブ")
        let products = root.appendingPathComponent("Products", isDirectory: true)
        let app = try appDirectory(relativePath, inside: products)
        let contents = app.appendingPathComponent("Contents", isDirectory: true)
        try requireType(.typeDirectory, at: contents)
        let appInfo = try plist(at: contents.appendingPathComponent("Info.plist"))
        let identity = try buildIdentity(in: appInfo, label: "アプリ本体")
        let differences = identity.differences(from: summary)
        guard differences.isEmpty else {
            throw InspectionError(message: "アーカイブの概要とアプリ本体の識別情報が一致しません。\n\(differences.joined(separator: "\n"))")
        }

        let executableName = try string("CFBundleExecutable", in: appInfo, label: "アプリ本体")
        guard safeComponents(executableName)?.count == 1 else {
            throw InspectionError(message: "アプリ本体の CFBundleExecutable が正しいファイル名ではありません。")
        }
        let executableDirectory = contents.appendingPathComponent("MacOS", isDirectory: true)
        try requireType(.typeDirectory, at: executableDirectory)
        let executable = executableDirectory.appendingPathComponent(executableName)
        try requireType(.typeRegular, at: executable)

        let verification = try run("/usr/bin/codesign", ["--verify", "--strict", "--deep", app.path], includeErrors: true)
        let signatureValid = verification.status == 0
        let sandbox: (enabled: Bool, message: String)
        let fingerprint: String?
        if signatureValid, let architectures = try architectures(in: executable) {
            sandbox = try checkSandbox(in: app, architectures: architectures)
            fingerprint = try signedFingerprint(in: app, architectures: architectures)
        } else if signatureValid {
            sandbox = (false, "署名の静的検証は通過しました。実行ファイルのアーキテクチャを判定できず、App Sandbox は未確認です。")
            fingerprint = nil
        } else {
            let detail = String(decoding: verification.output, as: UTF8.self)
                .trimmingCharacters(in: .whitespacesAndNewlines).prefix(800)
            sandbox = (false, "署名の静的検証に失敗しました（終了コード \(verification.status)）。App Sandbox は未確認です。\n\(detail)")
            fingerprint = nil
        }
        let fingerprintNote = signatureValid && fingerprint == nil ? "\n署名の識別値を取得できず、前回と同じ内容か判定できません。" : ""
        try Task.checkCancellation()
        return Self(identity: identity, archiveName: archive.lastPathComponent, path: root.path,
                    signatureValid: signatureValid, sandboxEnabled: sandbox.enabled, checkedAt: Date(),
                    diagnostic: "\(sandbox.message)\(fingerprintNote)\n\(scope)", fingerprint: fingerprint)
    }

    private static func appDirectory(_ relativePath: String, inside products: URL) throws -> URL {
        guard let components = safeComponents(relativePath),
              components.last?.lowercased().hasSuffix(".app") == true else {
            throw InspectionError(message: "ApplicationPath は Products 内の .app を指す相対パスにしてください。")
        }
        try requireType(.typeDirectory, at: products)
        var cursor = products
        // Xcode's archive directories are real directories. Reject links before using any child path.
        for component in components {
            cursor.appendPathComponent(component, isDirectory: true)
            try requireType(.typeDirectory, at: cursor)
        }
        return cursor
    }

    private static func safeComponents(_ path: String) -> [String]? {
        let parts = path.components(separatedBy: "/")
        guard !path.contains("\0"), parts.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else { return nil }
        return parts
    }

    private static func requireType(_ type: FileAttributeType, at url: URL) throws {
        let attributes = try? FileManager.default.attributesOfItem(atPath: url.path)
        guard attributes?[.type] as? FileAttributeType == type else {
            throw InspectionError(message: "\(url.lastPathComponent) を確認できません。必要なファイルやフォルダが存在し、シンボリックリンクを経由していないか確認してください。")
        }
    }

    private static func plist(at url: URL) throws -> [String: Any] {
        try requireType(.typeRegular, at: url)
        guard let data = try? Data(contentsOf: url),
              let result = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] else {
            throw InspectionError(message: "\(url.deletingLastPathComponent().lastPathComponent) の Info.plist を読み取れませんでした。")
        }
        return result
    }

    private static func buildIdentity(in plist: [String: Any], label: String) throws -> BuildIdentity {
        try BuildIdentity(
            bundleID: string("CFBundleIdentifier", in: plist, label: label),
            version: string("CFBundleShortVersionString", in: plist, label: label),
            build: string("CFBundleVersion", in: plist, label: label)
        )
    }

    private static func string(_ key: String, in plist: [String: Any], label: String) throws -> String {
        guard let value = plist[key] as? String, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw InspectionError(message: "\(label) の Info.plist で \(key) を確認してください。")
        }
        return value
    }

    private static func architectures(in executable: URL) throws -> [String]? {
        let result = try run("/usr/bin/lipo", ["-archs", executable.path])
        let names = String(decoding: result.output, as: UTF8.self).split(whereSeparator: \.isWhitespace).map(String.init)
        guard result.status == 0, !names.isEmpty, Set(names).count == names.count else { return nil }
        return names.sorted()
    }

    private static func checkSandbox(in app: URL, architectures: [String]) throws -> (enabled: Bool, message: String) {
        for architecture in architectures {
            let entitlements = try run("/usr/bin/codesign", ["--display", "--architecture", architecture, "--entitlements", "-", "--xml", app.path])
            guard entitlements.status == 0 else {
                return (false, "署名の静的検証は通過しました。\(architecture) の署名済みエンタイトルメントを読み取れませんでした。")
            }
            if entitlements.output.isEmpty {
                return (false, "署名の静的検証は通過しました。\(architecture) の署名にエンタイトルメントがありません。")
            }
            guard let dictionary = try? PropertyListSerialization.propertyList(from: entitlements.output, format: nil) as? [String: Any] else {
                return (false, "署名の静的検証は通過しました。\(architecture) の署名済みエンタイトルメントを解析できませんでした。")
            }
            guard let sandbox = dictionary["com.apple.security.app-sandbox"] as? NSNumber,
                  CFGetTypeID(sandbox) == CFBooleanGetTypeID(), sandbox.boolValue else {
                return (false, "署名の静的検証は通過しました。\(architecture) の App Sandbox が有効になっていません。")
            }
        }
        return (true, "識別情報が一致し、署名の静的検証は通過しました。アプリ本体の全アーキテクチャの署名で App Sandbox が有効です。")
    }

    private static func signedFingerprint(in app: URL, architectures: [String]) throws -> String? {
        let prefix = "CDHash="
        var hashes: [String] = []
        for architecture in architectures {
            let display = try run("/usr/bin/codesign", ["--display", "--architecture", architecture, "--verbose=4", app.path], includeErrors: true)
            let lines = String(decoding: display.output, as: UTF8.self).split(whereSeparator: \.isNewline)
            let candidates = lines.filter { $0.hasPrefix(prefix) }
            guard display.status == 0, candidates.count == 1, let candidate = candidates.first else { return nil }
            let hash = candidate.dropFirst(prefix.count).lowercased()
            guard hash.utf8.count == 40, hash.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else { return nil }
            hashes.append("\(architecture)=\(hash)")
        }
        return hashes.joined(separator: ";")
    }

    private static func run(_ executable: String, _ arguments: [String], includeErrors: Bool = false) throws -> (status: Int32, output: Data) {
        try Task.checkCancellation()
        let process = Process()
        let pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.standardOutput = pipe
        process.standardError = includeErrors ? pipe : FileHandle.nullDevice
        try process.run()
        // Drain output before waiting so that a diagnostic cannot fill the pipe and block the tool.
        let output = try pipe.fileHandleForReading.readToEnd() ?? Data()
        process.waitUntilExit()
        return (process.terminationStatus, output)
    }

    private struct InspectionError: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }
}
