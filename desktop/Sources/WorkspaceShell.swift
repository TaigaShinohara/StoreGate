import AppKit
import SwiftUI
import UniformTypeIdentifiers

@MainActor
struct WorkspaceShell: View {
    @ObservedObject var store: DeliveryStore
    var openSample: () -> Void
    var openOnboarding: () -> Void
    var practiceOnboarding: () -> Void
    var onboardingFocus: OnboardingFocus?
    var hasPracticed: Bool
    @Binding var startNewProject: Bool

    @State private var showingNewProject = false
    @State private var editingProject: DeliveryProject?
    @State private var confirmingRestore = false
    @AccessibilityFocusState private var accessibilityFocus: FocusTarget?

    private enum FocusTarget: Hashable { case projects, loadProblem, saveProblem }

    private var selectedProject: DeliveryProject? {
        store.workspace.projects.first { $0.id == store.workspace.lastProject }
    }

    var body: some View {
        Group {
            if let problem = store.loadProblem {
                page { loadRecovery(problem) }
            } else if let project = selectedProject {
                VStack(spacing: 0) {
                    HStack {
                        Button("案件一覧", systemImage: "chevron.left") { store.select(nil) }
                            .buttonStyle(.borderless)
                            .accessibilityLabel("配信案件の一覧に戻る")
                        Spacer()
                        Button("基本情報…", systemImage: "pencil") { editingProject = project }
                            .accessibilityLabel("案件の基本情報を編集")
                            .disabled(!project.phase.editable || store.saveProblem != nil)
                    }
                    .padding(.horizontal, 22)
                    .padding(.vertical, 10)
                    Divider()
                    if let problem = store.saveProblem {
                        saveRecovery(problem)
                            .padding(.horizontal, 22)
                            .padding(.vertical, 12)
                    }
                    DeliveryConsole(project: project, store: store)
                        .id(project.id)
                }
            } else {
                page { projectList }
            }
        }
        .sheet(isPresented: $showingNewProject, onDismiss: focusProjectList) {
            DeliveryProjectEditor(store: store)
        }
        .sheet(item: $editingProject) { project in
            DeliveryProjectEditor(store: store, editing: project)
        }
        .alert("バックアップから復元しますか？", isPresented: $confirmingRestore) {
            Button("キャンセル", role: .cancel) {}
            Button("バックアップから復元") { store.restoreBackup() }
        } message: {
            Text("読み取れない保存ファイルは別名で保管し、バックアップを作成した時点の案件と進捗を開きます。")
        }
        .onChange(of: store.workspace.lastProject) { _, _ in focusProjectList() }
        .onChange(of: store.loadProblem, initial: true) { _, problem in
            if problem != nil { accessibilityFocus = .loadProblem }
            else { focusProjectList() }
        }
        .onChange(of: store.saveProblem) { _, problem in
            if problem != nil && !showingNewProject && store.loadProblem == nil {
                accessibilityFocus = .saveProblem
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if store.recoveryCopy != nil {
                HStack {
                    Label("未保存の変更は別ファイルに保管しました。", systemImage: "doc.on.doc")
                    Spacer()
                    Button("控えをFinderで表示") { store.revealRecoveryCopy() }
                    Button("閉じる") { store.dismissRecoveryCopy() }
                }
                .padding(12)
                .background(.regularMaterial)
            }
        }
        .task(id: startNewProject) {
            guard startNewProject, store.loadProblem == nil, store.saveProblem == nil else { return }
            await Task.yield()
            guard !Task.isCancelled, startNewProject else { return }
            startNewProject = false
            showingNewProject = true
        }
    }

    private func focusProjectList() {
        if selectedProject == nil && store.loadProblem == nil {
            accessibilityFocus = store.saveProblem == nil ? .projects : .saveProblem
        }
    }

    private func page<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24, content: content)
                .frame(maxWidth: 740, alignment: .leading)
                .padding(.horizontal, 36)
                .padding(.vertical, 36)
                .frame(maxWidth: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var projectList: some View {
        VStack(alignment: .leading, spacing: 24) {
            if let problem = store.saveProblem { saveRecovery(problem) }

            HStack(alignment: .firstTextBaseline, spacing: 20) {
                Text("配信案件")
                    .font(.largeTitle.weight(.semibold))
                    .accessibilityAddTraits(.isHeader)
                    .accessibilityFocused($accessibilityFocus, equals: .projects)
                Spacer(minLength: 0)
                Button("新規案件を作成…", systemImage: "plus") { showingNewProject = true }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(store.saveProblem != nil)
            }

            if store.workspace.projects.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text(onboardingFocus == .starting ? "アプリを登録して、最初の作業へ進みましょう。"
                         : onboardingFocus == .checks ? "確認したいアプリを登録しましょう。"
                         : "Mac App Storeへ配信するアプリを登録します。")
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(20)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
            } else {
                let projects = store.workspace.projects.sorted { $0.updatedAt > $1.updatedAt }
                LazyVStack(spacing: 0) {
                    ForEach(projects) { project in
                        projectRow(project)
                        if project.id != projects.last?.id { Divider().padding(.leading, 16) }
                    }
                }
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color(nsColor: .separatorColor)))
                .disabled(store.saveProblem != nil)
            }

            HStack(spacing: 18) {
                sampleEntry
                Menu("使い方") {
                    Button("はじめての使い方を見返す", action: openOnboarding)
                    Button(hasPracticed ? "次の作業をもう一度試す" : "次の作業を試す", action: practiceOnboarding)
                }
                .fixedSize()
                .disabled(store.saveProblem != nil)
            }

        }
    }

    private func projectRow(_ project: DeliveryProject) -> some View {
        let status = project.phase == .upload ? project.activeStep.title : project.phase.title
        let action = project.phase == .published ? "確認結果を開く" : "作業を再開"
        return HStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 5) {
                Text(project.name).font(.headline)
                Text(project.bundleID)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(project.bundleID)
                if !project.scope.version.isEmpty {
                    Text("バージョン \(project.scope.version)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .accessibilityElement(children: .combine)

            Spacer(minLength: 0)

            VStack(alignment: .trailing, spacing: 5) {
                Text(status).font(.callout.weight(.medium))
                Text(project.updatedAt, format: .dateTime.month().day().hour().minute())
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("最終更新 \(project.updatedAt.formatted(date: .abbreviated, time: .shortened))")
            }
            .accessibilityElement(children: .combine)

            Button(action, systemImage: "chevron.right") { store.select(project.id) }
                .accessibilityLabel("\(project.name)の\(action)")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }

    private var sampleEntry: some View {
        Button("サンプルで試す", systemImage: "play.rectangle", action: openSample)
            .buttonStyle(.link)
            .help("架空の案件で配信手順を試せます。")
    }

    private func loadRecovery(_ problem: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("配信案件を読み込めません", systemImage: "exclamationmark.triangle")
                .font(.largeTitle.weight(.semibold))
                .accessibilityAddTraits(.isHeader)
                .accessibilityFocused($accessibilityFocus, equals: .loadProblem)
            Text(problem).fixedSize(horizontal: false, vertical: true)
            Text(store.hasBackup
                 ? "まず読み込みを再試行します。解消しない場合は、バックアップから復元できます。"
                 : "読み込みを再試行できます。解消しない場合は、Finderで保存先を確認してください。")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 12) {
                Button("読み込みを再試行", systemImage: "arrow.clockwise") { store.reload() }
                    .buttonStyle(.borderedProminent)
                if store.hasBackup {
                    Button("バックアップから復元…") { confirmingRestore = true }
                }
                Button("保存先をFinderで開く") { store.revealStorage() }
                    .buttonStyle(.link)
            }
            .padding(.top, 4)

            Divider().padding(.vertical, 8)
            sampleEntry
        }
    }

    private func saveRecovery(_ problem: String) -> some View {
        HStack(alignment: .center, spacing: 18) {
            VStack(alignment: .leading, spacing: 4) {
                Label("変更を保存できません", systemImage: "exclamationmark.triangle")
                    .font(.headline)
                    .accessibilityAddTraits(.isHeader)
                    .accessibilityFocused($accessibilityFocus, equals: .saveProblem)
                Text(problem).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            VStack(alignment: .trailing, spacing: 7) {
                Button("保存を再試行") { store.retrySave() }
                    .buttonStyle(.borderedProminent)
                Button("変更を控えに保存して読み直す") { store.preservePendingAndReload() }
                Button("保存先をFinderで開く") { store.revealStorage() }
                    .buttonStyle(.link)
            }
        }
        .padding(14)
        .background(Color.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
    }
}

@MainActor
private struct DeliveryProjectEditor: View {
    @ObservedObject var store: DeliveryStore
    @Environment(\.dismiss) private var dismiss

    let editing: DeliveryProject?
    @State private var name: String
    @State private var folderPath: String
    @State private var bundleID: String
    @State private var choosingFolder = false

    init(store: DeliveryStore, editing: DeliveryProject? = nil) {
        self.store = store
        self.editing = editing
        _name = State(initialValue: editing?.name ?? "")
        _folderPath = State(initialValue: editing?.folder ?? "")
        _bundleID = State(initialValue: editing?.bundleID ?? "")
    }
    @State private var problem: String?
    @State private var projectIDsBeforeCreation: Set<UUID>?
    @FocusState private var field: Field?
    @AccessibilityFocusState private var problemFocused: Bool

    private enum Field: Hashable { case name, folder, bundleID }

    private var creating: Bool { projectIDsBeforeCreation != nil && store.saveProblem == nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(editing == nil ? "配信案件を作成" : "案件の基本情報を編集")
                .font(.title2.weight(.semibold))
                .accessibilityAddTraits(.isHeader)

            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 14) {
                GridRow {
                    Text("アプリ名").fontWeight(.medium).accessibilityHidden(true)
                    TextField("アプリ名", text: $name, prompt: Text("例：メモ帳"))
                        .labelsHidden()
                        .accessibilityLabel("アプリ名")
                        .focused($field, equals: .name)
                }
                GridRow {
                    Text("フォルダ").fontWeight(.medium)
                    VStack(alignment: .leading, spacing: 6) {
                        TextField("プロジェクトフォルダのパス", text: $folderPath)
                            .accessibilityLabel("プロジェクトフォルダのパス")
                            .focused($field, equals: .folder)
                            .autocorrectionDisabled()
                        Button("フォルダを選ぶ…", systemImage: "folder") { choosingFolder = true }
                            .help("Finderからドラッグするか、パスを貼り付けられます。")
                    }
                    .dropDestination(for: URL.self) { urls, _ in
                        guard urls.count == 1, urls[0].isFileURL else { return false }
                        folderPath = urls[0].path(percentEncoded: false)
                        return true
                    }
                }
                GridRow(alignment: .top) {
                    Text("Bundle ID").fontWeight(.medium).accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 5) {
                        TextField("Bundle ID", text: $bundleID, prompt: Text("例：com.example.notes"))
                            .labelsHidden()
                            .accessibilityLabel("Bundle ID")
                            .focused($field, equals: .bundleID)
                            .autocorrectionDisabled()
                            .help("Xcodeの対象アプリにある Bundle Identifier を入力します。")
                    }
                }
            }
            .disabled(creating || store.saveProblem != nil)

            if editing != nil {
                Text("場所やBundle IDを変えると、アーカイブと関連する確認が再確認になります。Bundle IDの変更時はストアのアプリIDも記録し直します。")
                    .font(.callout).foregroundStyle(.secondary)
            }
            if let message = store.saveProblem ?? problem {
                Label(message, systemImage: "exclamationmark.triangle")
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityFocused($problemFocused)
            }

            HStack {
                if store.saveProblem != nil {
                    Button("保存先をFinderで開く") { store.revealStorage() }
                        .buttonStyle(.link)
                } else if creating {
                    ProgressView("案件を保存中")
                        .controlSize(.small)
                }
                Spacer()
                Button(store.saveProblem == nil ? "キャンセル" : "閉じる", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                    .disabled(creating)
                if store.saveProblem != nil {
                    Button("保存を再試行") {
                        store.retrySave()
                        if editing != nil && store.saveProblem == nil { dismiss() }
                    }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut(.defaultAction)
                } else {
                    Button(editing == nil ? "案件を作成" : "変更を保存") { createProject() }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut(.defaultAction)
                        .disabled(creating)
                }
            }
        }
        .padding(24)
        .frame(width: 560)
        .defaultFocus($field, .name)
        .fileImporter(isPresented: $choosingFolder, allowedContentTypes: [.folder]) { result in
            switch result {
            case .success(let url): folderPath = url.path(percentEncoded: false); problem = nil; field = .bundleID
            case .failure: problem = "フォルダを選べませんでした。パスの貼り付けかドラッグも使えます。"
            }
        }
        .onChange(of: store.saveProblem) { _, message in
            if message != nil { problemFocused = true }
        }
        .onChange(of: store.workspace.projects.map(\.id)) { _, _ in openNewProjectIfSaved() }
    }

    private func createProject() {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let bundleID = bundleID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return showProblem("アプリ名を入力してください。", at: .name) }
        let folder: URL
        do { folder = try ProjectLocation.url(folderPath) }
        catch { return showProblem(error.localizedDescription, at: .folder) }
        guard !bundleID.isEmpty else { return showProblem("Bundle IDを入力してください。", at: .bundleID) }
        problem = nil
        if let editing {
            let changed = store.update(editing.id) { $0.setBasicInfo(name: name, folder: folder.path(percentEncoded: false), bundleID: bundleID) }
            if changed && store.saveProblem == nil { dismiss() }
            else if store.saveProblem == nil {
                if store.current(editing.id)?.name == name && store.current(editing.id)?.folder == folder.path(percentEncoded: false)
                    && store.current(editing.id)?.bundleID == bundleID { dismiss() }
                else { showProblem("Bundle IDと入力内容を確認してください。", at: .bundleID) }
            }
            return
        }
        projectIDsBeforeCreation = Set(store.workspace.projects.map(\.id))
        if !store.add(name: name, folder: folder.path(percentEncoded: false), bundleID: bundleID) {
            projectIDsBeforeCreation = nil
            showProblem("案件を作成できませんでした。XcodeでBundle IDを確認し、半角英数字、ピリオド、ハイフンで入力してください。", at: .bundleID)
        }
    }

    private func openNewProjectIfSaved() {
        guard let existingIDs = projectIDsBeforeCreation,
              store.workspace.projects.contains(where: { !existingIDs.contains($0.id) }) else { return }
        dismiss()
    }

    private func showProblem(_ message: String, at field: Field) {
        problem = message
        self.field = field
        problemFocused = true
    }
}
