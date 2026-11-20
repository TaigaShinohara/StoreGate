import Foundation

enum ReleaseStep: String, CaseIterable, Codable, Identifiable {
    case scope, account, source, listing, archive, review, publication

    var id: Self { self }

    var title: String {
        switch self {
        case .scope: "配信案件"
        case .account: "開発者アカウント"
        case .source: "ソースの確認"
        case .listing: "掲載情報と審査メモ"
        case .archive: "配布用アーカイブ"
        case .review: "審査への提出"
        case .publication: "公開の確認"
        }
    }

    var symbol: String {
        switch self {
        case .scope: "folder"
        case .account: "person.crop.circle"
        case .source: "doc.text.magnifyingglass"
        case .listing: "text.document"
        case .archive: "shippingbox"
        case .review: "paperplane"
        case .publication: "globe"
        }
    }

    var instruction: String {
        switch self {
        case .scope: "配信するアプリを確認する"
        case .account: "アプリを提出できる状態か確認する"
        case .source: "修正が必要な箇所を確認する"
        case .listing: "ストアに載せる内容をそろえる"
        case .archive: "提出するアーカイブを選ぶ"
        case .review: "審査に送ったことを確認する"
        case .publication: "ストアに公開されたことを確認する"
        }
    }

    var completion: String {
        switch self {
        case .scope: "アプリ名、Bundle ID、バージョン、配信先、配信地域が、今回配信する内容と一致している。"
        case .account: "対象アプリをApp Store Connectで開ける。提出権限と、手続きを止めている未対応の契約がないことを確認した。"
        case .source: "対象アプリに必要なチェックを実施し、提出を止める指摘を解消した。"
        case .listing: "掲載文、スクリーンショット、プライバシー申告、審査メモを今回のバージョンと照合した。"
        case .archive: "選んだアーカイブのBundle ID、バージョン、ビルド番号が案件と一致している。"
        case .review: "対象ビルドを選択し、App Store Connectで審査提出済みの状態を確認した。"
        case .publication: "対象の国や地域からストアを表示し、今回のバージョンが公開されていることを確認した。"
        }
    }

    var referencePath: String? {
        switch self {
        case .scope, .account: "docs/PRE-SUBMISSION-CHECKLIST.md"
        case .source: "agent-os/skill/SKILL.md"
        case .listing: "templates/REVIEW-NOTES-TEMPLATE.md"
        case .archive: "references/guidelines/by-app-type/macos-and-the-mac-app-store.md"
        case .review, .publication: nil
        }
    }

    var officialLink: URL? {
        switch self {
        case .account: URL(string: "https://appstoreconnect.apple.com/apps")
        case .review: URL(string: "https://developer.apple.com/help/app-store-connect/manage-submissions-to-app-review/submit-an-app/")
        case .publication: URL(string: "https://developer.apple.com/help/app-store-connect/manage-your-apps-availability/select-an-app-store-version-release-option/")
        default: nil
        }
    }
}

struct BuildIdentity: Codable, Equatable, Sendable {
    let bundleID: String
    let version: String
    let build: String

    static let sample = Self(bundleID: "com.example.storegate.sample", version: "1.0", build: "1")

    func differences(from expected: Self) -> [String] {
        let fields = [
            ("Bundle ID", expected.bundleID, bundleID),
            ("バージョン", expected.version, version),
            ("ビルド番号", expected.build, build),
        ]
        return fields.compactMap { name, expected, actual in
            expected == actual ? nil : "\(name) — 予定 \(expected) ／ 選択 \(actual)"
        }
    }

    static func readArchive(at url: URL) throws -> Self {
        guard url.pathExtension.lowercased() == "xcarchive" else { throw ArchiveError.wrongType }
        let data = try Data(contentsOf: url.appendingPathComponent("Info.plist"))
        let manifest = try PropertyListDecoder().decode(ArchiveManifest.self, from: data)
        let app = manifest.ApplicationProperties
        guard [app.CFBundleIdentifier, app.CFBundleShortVersionString, app.CFBundleVersion]
            .allSatisfy({ !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) else {
            throw ArchiveError.missingIdentity
        }
        return Self(
            bundleID: app.CFBundleIdentifier,
            version: app.CFBundleShortVersionString,
            build: app.CFBundleVersion
        )
    }

    private struct ArchiveManifest: Decodable {
        let ApplicationProperties: Application

        struct Application: Decodable {
            let CFBundleIdentifier: String
            let CFBundleShortVersionString: String
            let CFBundleVersion: String
        }
    }

    private enum ArchiveError: Error {
        case wrongType, missingIdentity
    }
}

struct ReleaseProgress: Codable, Equatable {
    private(set) var completed: Set<ReleaseStep> = []
    private(set) var archiveName: String?
    private(set) var replacementBuild: Int?

    var next: ReleaseStep? { ReleaseStep.allCases.first { !completed.contains($0) } }

    var expectedArchive: BuildIdentity {
        BuildIdentity(
            bundleID: BuildIdentity.sample.bundleID,
            version: BuildIdentity.sample.version,
            build: replacementBuild.map(String.init) ?? BuildIdentity.sample.build
        )
    }

    @discardableResult
    mutating func confirm(_ step: ReleaseStep) -> Bool {
        guard next == step, step != .archive else { return false }
        completed.insert(step)
        return true
    }

    @discardableResult
    mutating func confirmArchive(_ identity: BuildIdentity, name: String) -> Bool {
        guard next == .archive, identity == expectedArchive else { return false }
        archiveName = name
        completed.insert(.archive)
        return true
    }

    mutating func requestChanges(newBuild: Bool) {
        guard completed.contains(.review), next == .publication else { return }
        completed.remove(.review)
        if newBuild {
            archiveName = nil
            replacementBuild = (replacementBuild ?? 1) + 1
            completed.remove(.archive)
        } else {
            completed.remove(.listing)
        }
    }

    func save(to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(self).write(to: url, options: .atomic)
    }

    static func load(from url: URL) throws -> Self {
        do {
            return try JSONDecoder().decode(Self.self, from: Data(contentsOf: url))
        } catch CocoaError.fileReadNoSuchFile {
            return Self()
        }
    }
}
