import AppKit
import SwiftUI

@MainActor
final class DeliveryStore: ObservableObject {
    @Published private(set) var workspace = DeliveryWorkspace()
    @Published private(set) var loadProblem: String?
    @Published private(set) var saveProblem: String?
    @Published private(set) var hasBackup = false
    private var pending: DeliveryWorkspace?
    private let url: URL

    init(url: URL) {
        self.url = url
        reload()
    }

    func reload() {
        do {
            workspace = try .read(url)
            loadProblem = nil
        } catch {
            loadProblem = "案件ファイルを読み込めません。もう一度読み込むか、直前に保存した控えから復旧できます。元のファイルはそのままです。"
        }
        hasBackup = DeliveryWorkspace.hasBackup(url)
    }

    func restoreBackup() {
        guard loadProblem != nil, hasBackup else { return }
        do {
            workspace = try DeliveryWorkspace.restore(url)
            pending = nil
            loadProblem = nil
            saveProblem = nil
        } catch {
            loadProblem = "バックアップから復旧できませんでした。Finderで保存場所を確認してください。"
        }
    }

    func retrySave() {
        guard let pending else { return }
        guard let current = try? DeliveryWorkspace.read(url), current.savedAt != workspace.savedAt else {
            persist(pending)
            return
        }
        var merged = current
        for change in pending.projects {
            let previous = workspace.projects.first { $0.id == change.id }
            guard previous != change else { continue }
            if let index = merged.projects.firstIndex(where: { $0.id == change.id }) {
                guard merged.projects[index] == previous else {
                    saveProblem = "同じ案件が別のStoreGateで更新されています。上書きしていません。この画面の入力を控えてから、保存先を確認してください。"
                    return
                }
                merged.projects[index] = change
            } else if previous == nil {
                merged.projects.insert(change, at: 0)
            } else {
                saveProblem = "別のStoreGateで案件が変更されています。現在の入力はこの画面に保持しています。"
                return
            }
        }
        merged.lastProject = pending.lastProject
        persist(merged, from: current)
    }

    func revealStorage() {
        let directory = url.deletingLastPathComponent()
        NSWorkspace.shared.activateFileViewerSelecting([FileManager.default.fileExists(atPath: url.path) ? url : directory])
    }

    func select(_ id: UUID?) {
        guard loadProblem == nil, pending == nil else { return }
        var value = workspace
        guard value.lastProject != id else { return }
        value.lastProject = id
        persist(value)
    }

    @discardableResult
    func add(name: String, folder: String, bundleID: String) -> Bool {
        guard loadProblem == nil, pending == nil, !name.isBlank, !folder.isBlank,
              bundleID.range(of: "^[A-Za-z0-9-]+(\\.[A-Za-z0-9-]+)+$", options: .regularExpression) != nil else { return false }
        var value = workspace
        let project = DeliveryProject(name: name, folder: folder, bundleID: bundleID)
        value.projects.insert(project, at: 0)
        value.lastProject = project.id
        persist(value)
        // The failed write retains the new project to retry, without duplicating it on a second click.
        return true
    }

    func current(_ id: UUID) -> DeliveryProject? {
        (pending ?? workspace).projects.first(where: { $0.id == id })
    }

    @discardableResult
    func update(_ id: UUID, _ transform: (inout DeliveryProject) -> Void) -> Bool {
        guard loadProblem == nil, pending == nil else { return false }
        var value = workspace
        guard let index = value.projects.firstIndex(where: { $0.id == id }) else { return false }
        let previous = value.projects[index]
        transform(&value.projects[index])
        guard value.projects[index] != previous else { return false }
        value.projects[index].updatedAt = .now
        return persist(value)
    }

    @discardableResult
    private func persist(_ candidate: DeliveryWorkspace, from baseline: DeliveryWorkspace? = nil) -> Bool {
        var value = candidate
        value.savedAt = .now
        do {
            try value.write(url, expecting: (baseline ?? workspace).savedAt, checkRevision: true)
            workspace = value
            pending = nil
            saveProblem = nil
            hasBackup = DeliveryWorkspace.hasBackup(url)
            return true
        } catch {
            if pending == nil { pending = value }
            if case DeliveryWorkspace.WriteError.conflict = error {
                saveProblem = "別のStoreGateで新しい保存が見つかりました。上書きせず入力を保持しています。「保存を再試行」で競合しない変更を保存できます。"
            } else {
                saveProblem = "変更を保存できませんでした。表示しているのは最後に保存できた内容です。「保存を再試行」で変更を保存できます。"
            }
            return false
        }
    }
}
