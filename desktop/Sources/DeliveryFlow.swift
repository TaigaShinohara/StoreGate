import Darwin
import Foundation

enum DeliveryStep: String, CaseIterable, Codable, Identifiable {
    case scope, account, checks, listing, archive, review, publication

    var id: Self { self }
    var title: String {
        switch self {
        case .scope: "配信の設定"
        case .account: "アカウント"
        case .checks: "適用ルールと実機"
        case .listing: "掲載情報と審査メモ"
        case .archive: "配布用ビルド"
        case .review: "アップロードと審査"
        case .publication: "公開の確認"
        }
    }
}

enum ReleaseMethod: String, CaseIterable, Codable, Identifiable {
    case manual, automatic
    var id: Self { self }
    var title: String { self == .manual ? "承認後に手動で公開" : "承認後に自動で公開" }
}

enum DeliveryCheck: String, CaseIterable, Codable, Identifiable {
    case account, source, privacy, reviewAccess, purchases, runtime, distribution, storefront
    var id: Self { self }

    var title: String {
        switch self {
        case .account: "アプリ登録・契約・提出権限"
        case .source: "ソースと権限"
        case .privacy: "プライバシー申告"
        case .reviewAccess: "審査用アクセス"
        case .purchases: "課金の動作"
        case .runtime: "提出ビルドでの実機確認"
        case .distribution: "配布用署名と権限"
        case .storefront: "管理画面に保存した掲載情報"
        }
    }

    var instruction: String {
        switch self {
        case .account: "App Store Connectの「マイApp」で対象のBundle IDを確認。ビジネスで未対応の契約がないことと、アップロード・審査提出を行う権限を確認します。進められない場合は、必要な担当者と待っている内容を残します。"
        case .source: "Xcodeの対象ターゲットで、使っている権限と用途説明、依存SDKとPrivacy Manifestを照合します。使わない権限、テスト用接続先、仮のコンテンツがないことを確認します。"
        case .privacy: "配布用ビルドが使うデータとSDKを洗い出し、実際の挙動、アプリ内のポリシー、App Store Connectの申告を照合します。アプリ内からポリシーが開くことも確かめます。"
        case .reviewAccess: "配布用ビルドで審査員が使う経路を試し、ログインや制限がある場合は、審査メモに案内を書き、テスト用アカウントは公式画面に直接入力します。"
        case .purchases: "配布用の検証環境で購入と復元を試し、ストアの価格・期間・利用条件と表示の一致を確認します。"
        case .runtime: "対象ビルドをMacで起動し、主要操作・権限・必要なアクセシビリティを試します。確認したmacOSの版と操作内容を残します。"
        case .distribution: "Xcode Organizerで対象アーカイブを開き、配信先がMac App Store、チームが正しく、配布用署名と必要な権限が揃うことを確認します。"
        case .storefront: "App Store Connectの対象Mac版を開き、監査済みの原稿、各言語の掲載情報、画像、審査メモ、年齢区分、公開設定を実際に保存した値と確認します。"
        }
    }

    var canBeNotApplicable: Bool { self == .reviewAccess || self == .purchases }
}

struct Verification: Codable, Equatable {
    var note: String
    var notApplicable: Bool = false
    var date: Date = .now
}

struct DeliveryScope: Codable, Equatable {
    var version = ""
    var build = ""
    var regions = "JP"
    var locale = "ja"
    var method: ReleaseMethod = .manual
    var isUpdate = false
    var login = false
    var purchases = false
    var features: Set<String> = []

    var regionCodes: [String] {
        Array(Set(regions.uppercased().split(whereSeparator: { $0.isWhitespace || ",、".contains($0) }).map(String.init))).sorted()
    }
    var featureCodes: [String] { features.sorted() }

    var isValid: Bool {
        let regionIDs = Set(Locale.Region.isoRegions.map(\.identifier))
        return !version.isEmpty && version.split(separator: ".", omittingEmptySubsequences: false).allSatisfy(Self.number)
            && (1...3).contains(version.split(separator: ".", omittingEmptySubsequences: false).count)
            && !build.isEmpty && build.split(separator: ".", omittingEmptySubsequences: false).allSatisfy(Self.number)
            && !regionCodes.isEmpty && regionCodes.allSatisfy(regionIDs.contains)
            && locale.range(of: "^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$", options: .regularExpression) != nil
    }

    private static func number(_ value: Substring) -> Bool {
        !value.isEmpty && value.utf8.allSatisfy { (48...57).contains($0) }
    }
}

enum StoreProgress: String, Codable {
    case upload, processing, submission, reviewQueue, inReview, revision, approved, propagation, published

    var title: String {
        switch self {
        case .upload: "アップロード前"
        case .processing: "ビルドの処理待ち"
        case .submission: "審査に提出する"
        case .reviewQueue: "審査待ち"
        case .inReview: "審査中"
        case .revision: "差し戻し対応"
        case .approved: "承認済み"
        case .propagation: "公開の反映待ち"
        case .published: "公開確認済み"
        }
    }

    var editable: Bool {
        self == .upload || self == .processing || self == .submission || self == .revision
    }
}

enum RevisionArea: String, CaseIterable, Codable, Identifiable {
    case text, materials, privacy, binary
    var id: Self { self }
    var title: String {
        switch self {
        case .text: "掲載文"
        case .materials: "スクリーンショット・審査メモ"
        case .privacy: "データ取扱い・申告"
        case .binary: "アプリ本体・ビルド"
        }
    }
}

struct RevisionRecord: Codable, Equatable {
    let note: String
    let areas: Set<RevisionArea>
    let oldBuild: String
    let date: Date
}

struct StatusRecord: Codable, Equatable, Identifiable {
    var id = UUID()
    let state: StoreProgress
    let note: String
    let identity: BuildIdentity
    let date: Date

    enum CodingKeys: String, CodingKey { case id, state, note, identity, date }
}

struct PublicationProof: Codable, Equatable, Identifiable {
    let region: String
    let version: String
    let url: String
    let date: Date
    var id: String { region }
}

struct DeliveryProject: Codable, Equatable, Identifiable {
    var id = UUID()
    var name: String
    var folder: String
    var bundleID: String
    var scope = DeliveryScope()
    var scopeConfirmed = false
    var checks: [DeliveryCheck: Verification] = [:]
    var listing = ListingDraft()
    var listingReport: ListingReport?
    var localeDrafts: [String: ListingDraft] = [:]
    var localeReports: [String: ListingReport] = [:]
    var reviewNotes = ""
    var rules: [ApplicableRule]?
    var rulesCheckedAt: Date?
    var rulesFingerprint: String?
    var ruleProof: [String: Verification] = [:]
    var archive: ArchiveInspection?
    var phase: StoreProgress = .upload
    var records: [StatusRecord] = []
    var revisions: [RevisionRecord] = []
    var publications: [PublicationProof] = []
    var accountWait = ""
    var storeAppID = ""
    var observedBeforeReadiness = false
    var updatedAt: Date = .now

    var identity: BuildIdentity { .init(bundleID: bundleID, version: scope.version, build: scope.build) }
    var hasMatchingArchive: Bool { archive.map { $0.identity == identity && $0.signatureValid && $0.sandboxEnabled && $0.fingerprint != nil } ?? false }
    var listingBlocked: Bool {
        guard let listingReport, !listingReport.blocked else { return true }
        return localeDrafts.keys.contains { localeReports[$0]?.blocked != false }
    }

    var activeStep: DeliveryStep {
        if phase == .approved || phase == .propagation || phase == .published { return .publication }
        if phase == .reviewQueue || phase == .inReview || phase == .processing || phase == .submission { return .review }
        if !scopeConfirmed { return .scope }
        if checks[.account] == nil, accountWait.isEmpty { return .account }
        if phase == .revision, revisions.last?.areas.contains(.binary) == true, !hasMatchingArchive { return .archive }
        if listingBlocked || reviewNotes.isBlank || checks[.storefront] == nil { return .listing }
        if !hasMatchingArchive || checks[.distribution] == nil { return .archive }
        if !openChecks.isEmpty || !rulesReady { return .checks }
        if checks[.account] == nil { return .account }
        return .review
    }

    var openChecks: [DeliveryCheck] {
        DeliveryCheck.allCases.filter { checks[$0] == nil && ![.account, .distribution, .storefront].contains($0) }
    }

    var rulesReady: Bool {
        guard rulesCurrent, let rules else { return false }
        return rules.allSatisfy { !$0.blocksRelease || ruleProof[$0.id] != nil }
    }

    var rulesCurrent: Bool {
        guard let rulesCheckedAt, Calendar.current.isDateInToday(rulesCheckedAt) else { return false }
        return rulesFingerprint == DeliveryAudit.registerFingerprint
    }

    func completed(_ step: DeliveryStep) -> Bool {
        switch step {
        case .scope: scopeConfirmed
        case .account: checks[.account] != nil && !storeAppID.isEmpty
        case .checks: openChecks.isEmpty && rulesReady
        case .listing: !listingBlocked && checks[.storefront] != nil && !reviewNotes.isBlank
        case .archive: hasMatchingArchive && checks[.distribution] != nil
        case .review: [.reviewQueue, .inReview, .approved, .propagation, .published].contains(phase)
        case .publication: phase == .published
        }
    }

    var uploadBlockers: [DeliveryStep] {
        [.scope, .account, .checks, .listing, .archive].filter { !completed($0) }
    }

    mutating func setScope(_ value: DeliveryScope) {
        guard phase.editable, value.isValid else { return }
        if scopeConfirmed, scope != value {
            let buildChanged = scope.version != value.version || scope.build != value.build
            let applicabilityChanged = scope.regionCodes != value.regionCodes || scope.featureCodes != value.featureCodes || scope.login != value.login || scope.purchases != value.purchases
            if buildChanged { invalidateBuild() }
            if applicabilityChanged {
                rules = nil; rulesCheckedAt = nil; ruleProof = [:]
                for key in [DeliveryCheck.source, .privacy, .reviewAccess, .purchases] { checks[key] = nil }
            }
            if scope.locale != value.locale {
                localeDrafts[scope.locale] = listing
                if let listingReport { localeReports[scope.locale] = listingReport }
                listing = localeDrafts.removeValue(forKey: value.locale) ?? ListingDraft()
                listingReport = localeReports.removeValue(forKey: value.locale)
            }
            checks[.storefront] = nil
        }
        scope = value
        scopeConfirmed = true
    }

    func draft(for locale: String) -> ListingDraft { locale == scope.locale ? listing : localeDrafts[locale] ?? ListingDraft() }
    func report(for locale: String) -> ListingReport? { locale == scope.locale ? listingReport : localeReports[locale] }

    mutating func setListing(_ new: ListingDraft, locale: String) {
        guard phase.editable else { return }
        if locale == scope.locale {
            guard new != listing else { return }
            listing = new; listingReport = nil
        } else {
            guard localeDrafts[locale] != new else { return }
            localeDrafts[locale] = new; localeReports[locale] = nil
        }
        checks[.storefront] = nil
    }

    mutating func recordReport(_ report: ListingReport, for draft: ListingDraft, locale: String) {
        guard phase.editable, draft == self.draft(for: locale) else { return }
        if locale == scope.locale { listingReport = report }
        else { localeReports[locale] = report }
        if report.blocked { checks[.storefront] = nil }
    }

    mutating func clearReport(locale: String) {
        guard phase.editable else { return }
        if locale == scope.locale { listingReport = nil }
        else { localeReports[locale] = nil }
        checks[.storefront] = nil
    }

    mutating func setNotes(_ value: String) {
        guard phase.editable, reviewNotes != value else { return }
        reviewNotes = value
        checks[.storefront] = nil
    }

    mutating func setCheck(_ key: DeliveryCheck, note: String, notApplicable: Bool) {
        guard phase.editable, !note.isBlank else { return }
        if [.runtime, .distribution, .privacy, .reviewAccess, .purchases].contains(key), !hasMatchingArchive,
           !notApplicable { return }
        if key == .storefront && (listingBlocked || reviewNotes.isBlank) { return }
        if key == .account && !Self.validStoreID(storeAppID) { return }
        if notApplicable {
            guard key.canBeNotApplicable, scopeConfirmed else { return }
            if key == .reviewAccess && scope.login
                || key == .purchases && (scope.purchases || scope.features.contains("subscriptions")) { return }
        }
        checks[key] = Verification(note: note, notApplicable: notApplicable)
        if key == .account { accountWait = "" }
    }

    mutating func reopen(_ key: DeliveryCheck) {
        guard phase.editable else { return }
        checks[key] = nil
    }

    mutating func setStoreAppID(_ value: String) {
        guard phase.editable, Self.validStoreID(value), storeAppID != value else { return }
        if !storeAppID.isEmpty {
            checks[.account] = nil
            checks[.storefront] = nil
            if [.processing, .submission].contains(phase) { phase = .upload }
        }
        storeAppID = value
    }

    mutating func recordArchive(_ inspected: ArchiveInspection) {
        guard phase.editable, scopeConfirmed, inspected.identity == identity else { return }
        if let existing = archive, !inspected.sameSignedArtifact(as: existing) {
            invalidateBuild()
        }
        archive = inspected
    }

    mutating func replaceBuild(_ number: String) {
        guard phase.editable, scope.build != number else { return }
        var next = scope
        next.build = number
        guard next.isValid else { return }
        setScope(next)
    }

    mutating func invalidateBuild() {
        archive = nil
        for key in [DeliveryCheck.source, .privacy, .runtime, .reviewAccess, .purchases, .distribution, .storefront] { checks[key] = nil }
        ruleProof = [:]
        if phase == .processing || phase == .submission { phase = .upload }
        publications = []
    }

    mutating func setRules(_ rows: [ApplicableRule], for checkedScope: DeliveryScope) {
        guard phase.editable, scope == checkedScope else { return }
        let previous = rules ?? []
        ruleProof = ruleProof.filter { entry in rows.contains { row in row.id == entry.key && previous.contains(row) } }
        rules = rows
        rulesCheckedAt = .now
        rulesFingerprint = DeliveryAudit.registerFingerprint
    }

    mutating func advance(_ target: StoreProgress, note: String, observed: BuildIdentity) -> Bool {
        guard identity == observed, !note.isBlank else { return false }
        if phase == .revision && revisions.last?.areas.contains(.binary) == true && revisions.last?.oldBuild == scope.build { return false }
        switch (phase, target) {
        case (.upload, .processing), (.revision, .processing), (.processing, .submission), (.upload, .submission),
             (.revision, .submission), (.submission, .reviewQueue), (.submission, .inReview):
            guard uploadBlockers.isEmpty else { return false }
        case (.reviewQueue, .inReview), (.reviewQueue, .approved), (.inReview, .approved),
             (.approved, .propagation): break
        default: return false
        }
        phase = target
        records.append(StatusRecord(state: target, note: note, identity: observed, date: .now))
        return true
    }

    mutating func observeExistingStatus(_ target: StoreProgress, note: String, observed: BuildIdentity) -> Bool {
        guard phase.editable, scopeConfirmed, identity == observed, Self.validStoreID(storeAppID),
              [.reviewQueue, .inReview, .approved, .propagation].contains(target),
              !note.isBlank else { return false }
        if phase == .revision, revisions.last?.areas.contains(.binary) == true,
           revisions.last?.oldBuild == scope.build { return false }
        observedBeforeReadiness = observedBeforeReadiness || !uploadBlockers.isEmpty
        phase = target
        records.append(StatusRecord(state: target, note: "既存のストア状態を確認: " + note, identity: observed, date: .now))
        return true
    }

    mutating func requestRevision(note: String, areas: Set<RevisionArea>) {
        guard [.processing, .submission, .reviewQueue, .inReview].contains(phase),
              !note.isBlank, !areas.isEmpty else { return }
        revisions.append(RevisionRecord(note: note, areas: areas, oldBuild: scope.build, date: .now))
        if areas.contains(.binary) { invalidateBuild() }
        if areas.contains(.text) { listingReport = nil; localeReports = [:] }
        if areas.contains(.privacy) { checks[.privacy] = nil; ruleProof = [:] }
        checks[.storefront] = nil
        phase = .revision
        records.append(StatusRecord(state: .revision, note: note, identity: identity, date: .now))
    }

    mutating func confirmPublication(region: String, version: String, url: String) -> Bool {
        guard phase == .propagation || (phase == .approved && scope.method == .automatic),
              scope.regionCodes.contains(region), version == scope.version,
              let link = URL(string: url), link.scheme == "https", link.host == "apps.apple.com",
              link.pathComponents.dropFirst().first?.uppercased() == region,
              link.pathComponents.contains("app"),
              link.lastPathComponent == "id" + storeAppID, Self.validStoreID(storeAppID) else { return false }
        let appStoreID = link.lastPathComponent
        guard publications.allSatisfy({ URL(string: $0.url)?.lastPathComponent == appStoreID }) else { return false }
        publications.removeAll { $0.region == region }
        publications.append(PublicationProof(region: region, version: version, url: url, date: .now))
        if Set(publications.map(\.region)) == Set(scope.regionCodes) {
            phase = .published
            records.append(StatusRecord(state: .published, note: scope.regionCodes.joined(separator: ", "), identity: identity, date: .now))
        }
        return true
    }

    static func validStoreID(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.allSatisfy { (48...57).contains($0) }
    }

    mutating func reopenPublication(_ region: String) {
        guard phase == .published || phase == .propagation,
              let proof = publications.first(where: { $0.region == region }) else { return }
        publications.removeAll { $0.region == region }
        phase = .propagation
        records.append(StatusRecord(state: .propagation, note: "公開確認を開き直しました（\(region)）: \(proof.url)",
                                    identity: identity, date: .now))
    }
}

extension ArchiveInspection {
    func sameSignedArtifact(as other: Self) -> Bool {
        guard let fingerprint, fingerprint == other.fingerprint else { return false }
        return identity == other.identity && signatureValid && other.signatureValid && sandboxEnabled && other.sandboxEnabled
    }
}

extension String {
    var isBlank: Bool { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
}

struct DeliveryWorkspace: Codable, Equatable {
    var projects: [DeliveryProject] = []
    var lastProject: UUID?
    var savedAt: Date?

    static func read(_ url: URL) throws -> Self {
        do { return try JSONDecoder().decode(Self.self, from: Data(contentsOf: url)) }
        catch CocoaError.fileReadNoSuchFile { return Self() }
    }

    private static func backupURL(_ url: URL) -> URL { url.appendingPathExtension("previous") }

    static func hasBackup(_ url: URL) -> Bool {
        guard let data = try? Data(contentsOf: backupURL(url)) else { return false }
        return (try? JSONDecoder().decode(Self.self, from: data)) != nil
    }

    enum WriteError: Error { case lockUnavailable, conflict }

    private static func locked<T>(_ url: URL, perform: () throws -> T) throws -> T {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let descriptor = Darwin.open(url.appendingPathExtension("lock").path, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw WriteError.lockUnavailable }
        defer { _ = Darwin.close(descriptor) }
        guard Darwin.lockf(descriptor, F_TLOCK, 0) == 0 else { throw WriteError.lockUnavailable }
        return try perform()
    }

    func write(_ url: URL, expecting previousSave: Date? = nil, checkRevision: Bool = false) throws {
        try Self.locked(url) {
            if FileManager.default.fileExists(atPath: url.path) {
                let previous = try Data(contentsOf: url)
                let previousWorkspace = try JSONDecoder().decode(Self.self, from: previous)
                if checkRevision, previousWorkspace.savedAt != previousSave { throw WriteError.conflict }
                try previous.write(to: Self.backupURL(url), options: .atomic)
            } else if checkRevision && previousSave != nil {
                throw WriteError.conflict
            }
            try JSONEncoder().encode(self).write(to: url, options: .atomic)
        }
    }

    static func restore(_ url: URL) throws -> Self {
        try locked(url) {
            if let live = try? Data(contentsOf: url), let recovered = try? JSONDecoder().decode(Self.self, from: live) {
                return recovered
            }
            let data = try Data(contentsOf: backupURL(url))
            let recovered = try JSONDecoder().decode(Self.self, from: data)
            if FileManager.default.fileExists(atPath: url.path) {
                let preserved = url.appendingPathExtension("unreadable-\(UUID().uuidString)")
                try FileManager.default.copyItem(at: url, to: preserved)
            }
            try data.write(to: url, options: .atomic)
            return recovered
        }
    }
}
