import AppKit
import SwiftUI

struct ArchivePane: View {
    let project: DeliveryProject
    let store: DeliveryStore
    @State private var inspecting = false
    @State private var error: String?
    @State private var differences: [String] = []
    @State private var newBuild = ""

    var body: some View {
        if !project.scopeConfirmed {
            WorkNotice(text: "先に配信設定で今回のバージョンとビルド番号を保存してください。")
        } else {
            WorkCard(title: "Xcodeで作成したアーカイブを照合する") {
                LabeledContent("予定のBundle ID", value: project.bundleID)
                LabeledContent("予定の版", value: "\(project.scope.version) · ビルド \(project.scope.build)")
                Text("Xcodeの Product > Archive で配布用アーカイブを作成し、Organizerで対象を選びます。「Show in Finder」で .xcarchive の場所を開けます。")
                    .foregroundStyle(.secondary)
                if let xcode = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.dt.Xcode") {
                    Link("Xcodeを開く", destination: xcode)
                }
                if inspecting { ProgressView("識別情報・署名・Sandboxを確認中…") }
                if let error { WorkNotice(text: error) }
                if !differences.isEmpty {
                    WorkNotice(text: "このアーカイブは案件と一致しません。Xcodeの対象ターゲットと版を見て、選び直してください。")
                    ForEach(differences, id: \.self) { Text($0).textSelection(.enabled) }
                }
                if project.phase.editable {
                    Button(project.archive == nil && differences.isEmpty && error == nil ? "アーカイブを選ぶ…" : "アーカイブを選び直す…") {
                        choose()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(inspecting)
                }
            }

            if let result = project.archive {
                WorkCard(title: "保存したアーカイブの結果") {
                    LabeledContent("ファイル", value: result.archiveName)
                    LabeledContent("照合した識別情報", value: "\(result.identity.bundleID) / \(result.identity.version) / \(result.identity.build)")
                    LabeledContent("ローカルでの署名検証", value: result.signatureValid ? "整合性を確認" : "確認できない")
                    LabeledContent("署名に含まれるSandbox", value: result.sandboxEnabled ? "有効を確認" : "有効を確認できない")
                    Text(result.checkedAt.formatted(date: .abbreviated, time: .shortened))
                        .font(.caption).foregroundStyle(.secondary)
                    if !result.diagnostic.isBlank { Text(result.diagnostic).textSelection(.enabled) }
                    Text("配布用のチームや証明書、実機動作は下の確認と「適用ルールと実機」に記録します。")
                        .foregroundStyle(.secondary)
                    Button("Finderで照合したファイルを表示") {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: result.path)])
                    }
                }
            }
            CheckCard(key: .distribution, project: project, store: store, action: ("Appleのアップロード手順", ReleaseLinks.upload))
                .disabled(inspecting)
            if project.phase.editable {
                DisclosureGroup("別のビルド番号に差し替える") {
                    Text("旧ビルドに依存する確認が未確認に戻ります。掲載文そのものの監査結果は引き継ぎます。")
                        .foregroundStyle(.secondary)
                    TextField("新しいビルド番号", text: $newBuild)
                    Button("新しいビルド番号を保存") {
                        store.update(project.id) { $0.replaceBuild(newBuild) }
                        newBuild = ""
                    }
                    .disabled(!canReplace || inspecting)
                }
            }
        }
    }

    private var canReplace: Bool {
        var value = project.scope
        value.build = newBuild
        return newBuild != project.scope.build && value.isValid
    }

    private func choose() {
        let picker = NSOpenPanel()
        picker.title = "配布用アーカイブを選ぶ"
        picker.message = "Xcodeで作成した .xcarchive を選びます。"
        picker.canChooseDirectories = true
        picker.canChooseFiles = true
        picker.treatsFilePackagesAsDirectories = false
        picker.begin { result in
            if result == .OK, let url = picker.url { inspect(url) }
        }
    }

    private func inspect(_ url: URL) {
        inspecting = true
        error = nil
        differences = []
        let expected = project.identity
        Task {
            defer { inspecting = false }
            do {
                let result = try await ArchiveInspection.inspect(url)
                differences = result.identity.differences(from: expected)
                guard differences.isEmpty else { return }
                guard store.current(project.id)?.identity == expected else {
                    error = "配信する版の設定が変わりました。表示されている版で照合し直してください。"
                    return
                }
                store.update(project.id) { $0.recordArchive(result) }
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
