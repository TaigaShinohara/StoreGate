import AppKit
import SwiftUI

@MainActor
final class ReleaseStore: ObservableObject {
    @Published private(set) var progress = ReleaseProgress()
    @Published private(set) var error: String?
    @Published private(set) var archiveIssue: [String] = []
    @Published private(set) var canContinue = true
    private let stateURL: URL

    init(stateURL: URL) {
        self.stateURL = stateURL
        do {
            progress = try .load(from: stateURL)
        } catch {
            self.error = "保存した進捗を読み込めませんでした。最初から試すにはサンプルをリセットしてください。"
            canContinue = false
        }
    }

    func confirm(_ step: ReleaseStep) {
        var updated = progress
        if updated.confirm(step) { save(updated) }
    }

    func chooseArchive() {
        let panel = NSOpenPanel()
        panel.title = "配布用アーカイブを選ぶ"
        panel.message = "Xcodeで作成した .xcarchive を選んでください。"
        panel.prompt = "照合する"
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.treatsFilePackagesAsDirectories = false

        panel.begin { [weak self] response in
            if response == .OK, let url = panel.url {
                self?.readArchive(url)
            }
        }
    }

    private func readArchive(_ url: URL) {
        do {
            let identity = try BuildIdentity.readArchive(at: url)
            checkArchive(identity, name: url.lastPathComponent)
        } catch {
            archiveIssue = ["アーカイブ内のアプリ情報を読み取れませんでした。Xcodeで作成した .xcarchive を選び直してください。"]
        }
    }

    func checkSampleArchive() {
        checkArchive(progress.expectedArchive, name: "サンプルのアーカイブ情報")
    }

    func requestChanges(newBuild: Bool) {
        var updated = progress
        updated.requestChanges(newBuild: newBuild)
        save(updated)
    }

    func reset() {
        archiveIssue = []
        save(ReleaseProgress())
    }

    private func checkArchive(_ identity: BuildIdentity, name: String) {
        archiveIssue = identity.differences(from: progress.expectedArchive)
        var updated = progress
        if updated.confirmArchive(identity, name: name) { save(updated) }
    }

    private func save(_ updated: ReleaseProgress) {
        do {
            try updated.save(to: stateURL)
            progress = updated
            canContinue = true
            error = nil
        } catch {
            self.error = "進捗を保存できませんでした。空き容量やフォルダへのアクセスを確認して、もう一度お試しください。"
        }
    }
}

@main
struct StoreGateApp: App {
    @NSApplicationDelegateAdaptor(DeliveryApplicationDelegate.self) private var applicationDelegate
    @StateObject private var sample = ReleaseStore(
        stateURL: URL.applicationSupportDirectory
            .appendingPathComponent("StoreGate Prototype", isDirectory: true)
            .appendingPathComponent("release-progress.json")
    )
    @StateObject private var delivery = DeliveryStore(url: Self.workspaceFile())
    @StateObject private var onboarding = OnboardingStore(
        url: Self.workspaceFile().deletingLastPathComponent().appendingPathComponent("onboarding-state.json")
    )

    var body: some Scene {
        WindowGroup("StoreGate") {
            StoreGateEntry(delivery: delivery, sample: sample, onboarding: onboarding,
                           workspace: Self.workspaceFile().deletingLastPathComponent())
                .frame(minWidth: 820, minHeight: 580)
                .tint(.storeGate)
                .onAppear { applicationDelegate.store = delivery }
        }
        .defaultSize(width: 980, height: 690)
    }

    private static func workspaceFile() -> URL {
        let directory = ProcessInfo.processInfo.environment["STOREGATE_WORKSPACE_DIR"]
            .map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? URL.applicationSupportDirectory.appendingPathComponent("StoreGate", isDirectory: true)
        return directory.appendingPathComponent("delivery-workspace.json")
    }
}

@MainActor
final class DeliveryApplicationDelegate: NSObject, NSApplicationDelegate {
    weak var store: DeliveryStore?

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let store, store.saveProblem != nil else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "保存できていない変更があります"
        alert.informativeText = "終了する場合は、未保存の内容を別ファイルに保管します。保管できない場合は作業に戻ります。"
        alert.addButton(withTitle: "作業に戻る")
        alert.addButton(withTitle: "控えを保存して終了")
        guard alert.runModal() == .alertSecondButtonReturn, store.preservePendingAndReload() else {
            return .terminateCancel
        }
        return .terminateNow
    }
}

private struct StoreGateEntry: View {
    @ObservedObject var delivery: DeliveryStore
    @ObservedObject var sample: ReleaseStore
    @ObservedObject var onboarding: OnboardingStore
    @State private var showingSample = false
    @State private var showingOnboarding: Bool
    @State private var startNewProject = false
    @State private var showingShared = true
    let workspace: URL

    init(delivery: DeliveryStore, sample: ReleaseStore, onboarding: OnboardingStore, workspace: URL) {
        self.workspace = workspace
        self.delivery = delivery
        self.sample = sample
        self.onboarding = onboarding
        _showingOnboarding = State(initialValue: onboarding.automaticallyPresents(
            hasProjects: !delivery.workspace.projects.isEmpty, workspaceHealthy: delivery.loadProblem == nil
        ))
    }

    var body: some View {
        Group {
            if showingOnboarding {
                OnboardingView(store: onboarding) { destination in
                    showingOnboarding = false
                    startNewProject = destination == .createProject
                    showingShared = !startNewProject
                }
            } else if showingSample {
                VStack(spacing: 0) {
                    HStack {
                        Button("配信案件に戻る", systemImage: "chevron.left") { showingSample = false }
                        Spacer()
                        Label("サンプルモード", systemImage: "testtube.2")
                            .foregroundStyle(.secondary)
                    }
                    .padding(12)
                    Divider()
                    ReleaseWindow(store: sample)
                }
            } else if showingShared {
                SharedDeliveryView(workspace: workspace, openMac: { showingShared = false })
                    .toolbar { Button("Mac App Storeの従来の案件") { showingShared = false } }
            } else {
                WorkspaceShell(store: delivery, openSample: { showingSample = true },
                               openOnboarding: { openGuide(at: .welcome) },
                               practiceOnboarding: { openGuide(at: .practice) },
                               onboardingFocus: onboarding.state.focus,
                               hasPracticed: onboarding.state.hasPracticed,
                               startNewProject: $startNewProject)
                    .toolbar { Button("配信案件一覧") { showingShared = true } }
            }
        }
    }

    private func openGuide(at page: OnboardingPage) {
        startNewProject = false
        _ = onboarding.restart(at: page)
        showingOnboarding = true
    }
}

private extension Color {
    static let storeGate = Color(red: 0.07, green: 0.42, blue: 0.37)
}

private struct ReleaseWindow: View {
    @ObservedObject var store: ReleaseStore
    @State private var showReset = false
    @State private var showChanges = false

    var body: some View {
        HStack(spacing: 0) {
            sidebar
                .frame(width: 236)
                .background(.regularMaterial)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    if let error = store.error {
                        notice("進捗を確認してください", messages: [error])
                    }
                    if let step = store.progress.next {
                        stepContent(step)
                    } else {
                        header("サンプルの配信手順を完了", subtitle: "登録から公開確認までの流れを試しました。実際のストアへの送信や公開はありません。")
                        facts
                    }
                }
                .frame(maxWidth: 680, alignment: .leading)
                .padding(.horizontal, 36)
                .padding(.vertical, 32)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Color(nsColor: .windowBackgroundColor))
        }
        .alert("サンプルを最初から試しますか？", isPresented: $showReset) {
            Button("キャンセル", role: .cancel) {}
            Button("リセット", role: .destructive) { store.reset() }
        } message: {
            Text("このサンプル案件の進捗を消去します。")
        }
        .confirmationDialog("どの内容を修正しますか？", isPresented: $showChanges, titleVisibility: .visible) {
            Button("掲載情報を修正") { store.requestChanges(newBuild: false) }
            Button("ビルドを差し替え") { store.requestChanges(newBuild: true) }
            Button("キャンセル", role: .cancel) {}
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 10) {
                Image(nsImage: NSApplication.shared.applicationIconImage)
                    .resizable()
                    .scaledToFit()
                    .frame(width: 36, height: 36)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text("StoreGate").font(.title3.weight(.semibold))
                    Text("Mac App Store").font(.caption).foregroundStyle(.secondary)
                }
            }

            VStack(alignment: .leading, spacing: 7) {
                ProgressView(value: Double(store.progress.completed.count), total: Double(ReleaseStep.allCases.count))
                    .accessibilityLabel("配信の進捗")
                    .accessibilityValue("\(store.progress.completed.count) / \(ReleaseStep.allCases.count) 完了")
            }
            .accessibilityElement(children: .combine)

            VStack(alignment: .leading, spacing: 4) {
                ForEach(ReleaseStep.allCases) { step in
                    stepRow(step)
                }
            }
            Spacer(minLength: 8)
            Button("サンプルをリセット", systemImage: "arrow.counterclockwise") { showReset = true }
                .buttonStyle(.borderless)
                .foregroundStyle(.secondary)
                .disabled(store.progress == ReleaseProgress() && store.canContinue)
        }
        .padding(19)
        .frame(maxHeight: .infinity)
    }

    private func stepRow(_ step: ReleaseStep) -> some View {
        let completed = store.progress.completed.contains(step)
        let active = store.progress.next == step
        return HStack(spacing: 9) {
            Image(systemName: completed ? "checkmark.circle.fill" : step.symbol)
                .frame(width: 18)
                .foregroundStyle(completed || active ? Color.storeGate : Color.secondary)
                .accessibilityHidden(true)
            Text(step.title)
                .font(.callout.weight(active ? .semibold : .regular))
            Spacer(minLength: 0)
            if active {
                Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.secondary)
                    .accessibilityHidden(true)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 9)
        .background(active ? Color.storeGate.opacity(0.12) : Color.clear, in: RoundedRectangle(cornerRadius: 7))
        .accessibilityLabel(step.title + (completed ? "、完了" : active ? "、次の作業" : "、未完了"))
    }

    private func stepContent(_ step: ReleaseStep) -> some View {
        VStack(alignment: .leading, spacing: 22) {
            header(step.instruction, subtitle: step == .archive
                ? "Xcodeの .xcarchive とサンプルの識別情報を照合します。"
                : "")

            if step == .scope || step == .archive {
                facts
            }

            VStack(alignment: .leading, spacing: 8) {
                Label("完了の目安", systemImage: "checklist")
                    .font(.headline)
                    .accessibilityAddTraits(.isHeader)
                Text(step.completion)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if step == .archive {
                    Text("試作で照合するのは識別情報です。署名・Sandbox・実機動作は未検査です。")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }

            if step == .archive, !store.archiveIssue.isEmpty {
                notice("アーカイブを確認できません", messages: store.archiveIssue)
            }

            HStack(spacing: 12) {
                if step == .archive {
                    Button("アーカイブを選ぶ…", systemImage: "folder") { store.chooseArchive() }
                        .buttonStyle(.borderedProminent)
                    Button("サンプルで照合") { store.checkSampleArchive() }
                } else {
                    Button(step == .publication ? "サンプルの公開を確認" : "確認して次へ") { store.confirm(step) }
                        .buttonStyle(.borderedProminent)
                    if step == .publication {
                        Button("差し戻しを試す…") { showChanges = true }
                    }
                }
            }
            .controlSize(.large)
            .disabled(!store.canContinue)

            links(step)
        }
    }

    private func header(_ title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.largeTitle.weight(.semibold))
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            if !subtitle.isEmpty {
                Text(subtitle)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var facts: some View {
        VStack(alignment: .leading, spacing: 11) {
            Text("Sample Notes").font(.headline)
            LabeledContent("Bundle ID", value: store.progress.expectedArchive.bundleID)
            LabeledContent("バージョン", value: "\(store.progress.expectedArchive.version) ・ ビルド \(store.progress.expectedArchive.build)")
            LabeledContent("配信", value: "日本 ・ 初回 ・ ログインと課金なし")
            if let archive = store.progress.archiveName {
                LabeledContent("照合した情報", value: archive)
            }
        }
        .font(.callout)
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 11))
        .overlay(RoundedRectangle(cornerRadius: 11).strokeBorder(Color.secondary.opacity(0.18)))
    }

    private func notice(_ title: String, messages: [String]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: "exclamationmark.triangle").font(.headline)
            ForEach(messages, id: \.self) { Text($0).textSelection(.enabled) }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 9))
    }

    private func links(_ step: ReleaseStep) -> some View {
        HStack(spacing: 16) {
            if let path = step.referencePath,
               let url = URL(string: "https://github.com/TaigaShinohara/StoreGate/blob/main/" + path) {
                Link(destination: url) { Label("StoreGateの資料", systemImage: "book") }
            }
            if let url = step.officialLink {
                Link(destination: url) {
                    Label(step == .account ? "App Store Connectを開く" : "Appleの手順", systemImage: "arrow.up.right.square")
                }
            }
        }
        .font(.callout)
    }
}
