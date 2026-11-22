import Combine
import Darwin
import Foundation

@MainActor
final class OnboardingStore: ObservableObject {
    @Published private(set) var state = OnboardingState()
    @Published private(set) var problem: String?
    private(set) var loadFailed = false
    private let url: URL
    private var baseline: Data?

    init(url: URL) {
        self.url = url
        reload()
    }

    func automaticallyPresents(hasProjects: Bool, workspaceHealthy: Bool) -> Bool {
        workspaceHealthy && !hasProjects && !loadFailed && !state.isComplete
    }

    func reload() {
        do {
            let data = try read()
            var loaded = try data.map { try JSONDecoder().decode(OnboardingState.self, from: $0) } ?? OnboardingState()
            guard loaded.version <= OnboardingState.currentVersion else { throw StorageError.unsupportedVersion }
            if loaded.version < OnboardingState.currentVersion {
                loaded.version = OnboardingState.currentVersion
                loaded.page = .welcome
                loaded.practicedThisRun = false
            }
            state = loaded
            baseline = data
            problem = nil
            loadFailed = false
        } catch {
            loadFailed = true
            problem = "使い方の進み具合を読み込めません。案件のデータはそのまま利用できます。"
        }
    }

    @discardableResult
    func choose(_ focus: OnboardingFocus) -> Bool { change { $0.choose(focus) } }
    @discardableResult
    func practice() -> Bool { change { $0.practice() } }
    @discardableResult
    func advance(from page: OnboardingPage) -> Bool { change { $0.advance(from: page) } }
    @discardableResult
    func back() -> Bool { change { $0.back() } }
    @discardableResult
    func finish(_ reason: OnboardingCompletion) -> Bool { change { $0.finish(reason) } }
    @discardableResult
    func restart(at page: OnboardingPage = .welcome) -> Bool { change { $0.restart(at: page) } }

    func visit() {
        _ = change { value in value.count("view.\(value.page.rawValue)"); return true }
    }
    func leave() {
        guard state.completion == nil else { return }
        _ = change { value in value.count("leave.\(value.page.rawValue)"); return true }
    }

    private enum StorageError: Error { case tooLarge, locked, conflict, unsupportedVersion }

    private func read() throws -> Data? {
        do {
            let values = try url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
            guard values.isRegularFile == true, (values.fileSize ?? Int.max) <= 131_072 else { throw StorageError.tooLarge }
            return try Data(contentsOf: url)
        } catch CocoaError.fileReadNoSuchFile { return nil }
    }

    private func change(_ transform: (inout OnboardingState) -> Bool) -> Bool {
        guard !loadFailed else { return false }
        var candidate = state
        guard transform(&candidate) else { return false }
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            let descriptor = Darwin.open(url.appendingPathExtension("lock").path, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0o600)
            guard descriptor >= 0 else { throw StorageError.locked }
            defer { _ = Darwin.close(descriptor) }
            guard Darwin.lockf(descriptor, F_TLOCK, 0) == 0 else { throw StorageError.locked }
            guard try read() == baseline else { throw StorageError.conflict }
            let data = try JSONEncoder().encode(candidate)
            try data.write(to: url, options: .atomic)
            baseline = data
            state = candidate
            problem = nil
            return true
        } catch StorageError.conflict {
            problem = "別のウインドウで使い方の進み具合が変わりました。読み直すか、案内を閉じて続けられます。"
        } catch {
            problem = "使い方の進み具合を保存できません。案内を閉じても、案件のデータはそのままです。"
        }
        return false
    }
}
