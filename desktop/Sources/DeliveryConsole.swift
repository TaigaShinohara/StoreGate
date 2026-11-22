import AppKit
import SwiftUI

enum ReleaseLinks {
    static let connect = URL(string: "https://appstoreconnect.apple.com/apps")!
    static let upload = URL(string: "https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds/")!
    static let review = URL(string: "https://developer.apple.com/help/app-store-connect/manage-submissions-to-app-review/submit-an-app/")!
    static let status = URL(string: "https://developer.apple.com/help/app-store-connect/reference/app-information/app-and-submission-statuses/")!
    static let release = URL(string: "https://developer.apple.com/help/app-store-connect/manage-your-apps-availability/select-an-app-store-version-release-option/")!
    static let checklist = URL(string: "https://github.com/TaigaShinohara/StoreGate/blob/main/docs/PRE-SUBMISSION-CHECKLIST.md")!
    static let reviewNotes = URL(string: "https://github.com/TaigaShinohara/StoreGate/blob/main/templates/REVIEW-NOTES-TEMPLATE.md")!
}

struct DeliveryConsole: View {
    let project: DeliveryProject
    @ObservedObject var store: DeliveryStore
    @State private var selected: DeliveryStep?
    @AccessibilityFocusState private var headingFocused: Bool
    private var step: DeliveryStep { selected ?? project.activeStep }

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text(project.name + "  ·  " + (project.scopeConfirmed ? "Mac App Store  /  " + project.scope.version : "配信案件"))
                        .font(.subheadline).foregroundStyle(.secondary)
                    Text(step.title)
                        .font(.largeTitle.weight(.semibold))
                        .accessibilityAddTraits(.isHeader)
                        .accessibilityFocused($headingFocused)
                    if !project.phase.editable && step != .review && step != .publication {
                        Label("提出時の記録を表示しています", systemImage: "lock")
                            .foregroundStyle(.secondary)
                    }
                    if project.phase == .revision, let revision = project.revisions.last, step != .review {
                        WorkCard(title: "差し戻しで直すこと") {
                            Text(revision.note).textSelection(.enabled)
                            Text(revision.areas.map(\.title).sorted().joined(separator: "、")).foregroundStyle(.secondary)
                        }
                    }
                    detail
                        .disabled(store.saveProblem != nil)
                }
                .frame(maxWidth: 760, alignment: .leading)
                .padding(30)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .id(step)
        }
        .onChange(of: project.activeStep) { _, _ in changeStep(nil) }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("配信の工程").font(.headline)
            ForEach(DeliveryStep.allCases) { item in
                Button { changeStep(item) } label: {
                    Label(item.title, systemImage: project.completed(item) ? "checkmark.circle.fill" : item == project.activeStep ? "arrow.right.circle.fill" : "circle")
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(7)
                        .background(item == step ? Color.accentColor.opacity(0.15) : Color.clear, in: RoundedRectangle(cornerRadius: 6))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(item.title + (project.completed(item) ? "、確認済み" : "、未完了") + (item == step ? "、表示中" : item == project.activeStep ? "、次の作業" : ""))
            }
            if selected != nil && step != project.activeStep {
                Button("次の作業を表示", systemImage: "arrow.forward") { changeStep(nil) }
            }
            Spacer()
            if let date = store.workspace.savedAt {
                Text("保存 \(date.formatted(date: .abbreviated, time: .shortened))")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(17)
        .frame(width: 224)
        .background(.regularMaterial)
    }

    private func changeStep(_ selection: DeliveryStep?) {
        selected = selection
        Task { @MainActor in
            await Task.yield()
            headingFocused = true
        }
    }

    @ViewBuilder private var detail: some View {
        switch step {
        case .scope:
            ScopePane(project: project, store: store)
        case .account:
            AccountPane(project: project, store: store)
        case .checks:
            ChecksPane(project: project, store: store, showArchive: { changeStep(.archive) })
        case .listing:
            ListingPane(project: project, store: store)
        case .archive:
            ArchivePane(project: project, store: store)
        case .review, .publication:
            StoreProgressPane(project: project, store: store, show: changeStep, publication: step == .publication)
        }
    }
}

struct WorkCard<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline).accessibilityAddTraits(.isHeader)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(15)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color.secondary.opacity(0.17)))
    }
}

struct WorkNotice: View {
    var text: String
    var symbol = "exclamationmark.triangle"
    var shade: Color = .orange

    var body: some View {
        Label(text, systemImage: symbol)
            .fixedSize(horizontal: false, vertical: true)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(shade.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
    }
}

/// Secondary information stays reachable without competing with the current task.
struct WorkDetails<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 10) { content }
                .padding(.top, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
        } label: {
            Text(title).font(.callout.weight(.medium))
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.16), value: expanded)
    }
}

struct ScopePane: View {
    let project: DeliveryProject
    let store: DeliveryStore
    @State private var draft = DeliveryScope()

    static let features = [
        ("personal_data", "個人データ"), ("generative_ai", "生成AI"), ("social", "ソーシャル・投稿"),
        ("children", "子ども向け"), ("subscriptions", "サブスクリプション"),
        ("marketplace", "マーケットプレイス"), ("medical", "医療"),
        ("lending", "貸付"), ("crypto", "暗号資産")
    ]

    var body: some View {
        WorkCard(title: "バージョンと地域") {
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
                GridRow { Text("バージョン"); TextField("例 1.0", text: $draft.version).accessibilityLabel("バージョン") }
                GridRow { Text("ビルド番号"); TextField("例 1", text: $draft.build).accessibilityLabel("ビルド番号") }
                GridRow { Text("配信地域"); TextField("例 JP, US", text: $draft.regions).accessibilityLabel("配信地域") }
                GridRow {
                    Text("主要掲載言語").accessibilityHidden(true)
                    Picker("主要掲載言語", selection: $draft.locale) {
                        if !StoreLocales.codes.contains(draft.locale) { Text("保存値を選び直す：\(draft.locale)").tag(draft.locale) }
                        ForEach(StoreLocales.codes, id: \.self) { Text(StoreLocales.label($0)).tag($0) }
                    }.labelsHidden().accessibilityLabel("主要掲載言語")
                }
            }
            WorkDetails(title: "地域コードの入力例") {
                Text("JP：日本、US：米国。複数の地域はカンマで区切り、公開後に各地域を確認します。")
                    .foregroundStyle(.secondary)
            }
            Picker("公開方法", selection: $draft.method) {
                ForEach(ReleaseMethod.allCases) { Text($0.title).tag($0) }
            }
            Toggle("既存アプリの更新", isOn: $draft.isUpdate)
            Toggle("ログインがある", isOn: $draft.login)
            Toggle("アプリ内購入がある", isOn: $draft.purchases)
            WorkDetails(title: "その他の機能（\(draft.features.count)件選択）") {
                ForEach(Self.features, id: \.0) { code, label in
                    Toggle(label, isOn: Binding(get: { draft.features.contains(code) }, set: {
                        if $0 { draft.features.insert(code) } else { draft.features.remove(code) }
                    }))
                }
            }
            Button("配信条件を保存") {
                store.update(project.id) { $0.setScope(draft) }
            }
            .buttonStyle(.borderedProminent)
            .disabled(!draft.isValid || !project.phase.editable)
        }
        .onAppear { draft = project.scope }
        .disabled(!project.phase.editable)
    }
}

struct AccountPane: View {
    let project: DeliveryProject
    let store: DeliveryStore
    @State private var waiting = ""
    @State private var appID = ""

    var body: some View {
        WorkCard(title: "ストアのアプリID") {
            Link("App Store Connectを開く", destination: ReleaseLinks.connect)
            Text("「App情報」にあるApple ID（数字）を入力します。")
                .foregroundStyle(.secondary)
            if project.checks[.account] == nil && project.phase.editable {
                TextField("Apple ID（数字）", text: $appID)
                    .accessibilityLabel("ストアのアプリID")
                Button("アプリIDを記録") { store.update(project.id) { $0.setStoreAppID(appID) } }
                    .disabled(!DeliveryProject.validStoreID(appID))
            }
            if !project.storeAppID.isEmpty && (project.checks[.account] != nil || !project.phase.editable) {
                LabeledContent("記録したアプリID", value: project.storeAppID)
            }
        }
        .onAppear { appID = project.storeAppID }
        CheckCard(key: .account, project: project, store: store)
        if project.checks[.account] == nil {
            WorkDetails(title: project.accountWait.isBlank ? "担当者の対応待ちを記録" : "対応待ちの理由を編集") {
                Text("待機中も他の工程を準備できます。")
                    .foregroundStyle(.secondary)
                TextField("例 契約をAccount Holderに確認依頼中", text: $waiting, axis: .vertical)
                    .accessibilityLabel("待機理由")
                Button("待機理由を保存") { store.update(project.id) { $0.accountWait = waiting } }
                    .disabled(waiting.isBlank || !project.phase.editable)
            }
            .onAppear { waiting = project.accountWait }
        }
    }
}

struct CheckEntryDraft {
    var note = ""
    var notApplicable = false
}

struct CheckCard: View {
    let key: DeliveryCheck
    let project: DeliveryProject
    let store: DeliveryStore
    var action: (String, URL)? = nil
    var draft: Binding<CheckEntryDraft>? = nil
    @State private var localDraft = CheckEntryDraft()

    private var form: Binding<CheckEntryDraft> { draft ?? $localDraft }

    private var needsArchive: Bool { [.privacy, .reviewAccess, .purchases, .runtime, .distribution].contains(key) }
    private var canSkip: Bool {
        project.scopeConfirmed && (key == .reviewAccess && !project.scope.login
            || key == .purchases && !project.scope.purchases && !project.scope.features.contains("subscriptions"))
    }

    var body: some View {
        WorkCard(title: key.title) {
            if let saved = project.checks[key], key != .source || !project.phase.editable || project.sourceReady {
                Label(saved.notApplicable ? "対象外として記録済み" : "本人が確認済み", systemImage: "checkmark.circle")
                WorkDetails(title: "確認記録") {
                    Text(saved.note).textSelection(.enabled)
                    Text(saved.date.formatted(date: .abbreviated, time: .shortened))
                        .font(.caption).foregroundStyle(.secondary)
                    if project.phase.editable {
                        Button("この確認を開き直す") {
                            form.wrappedValue = CheckEntryDraft(note: saved.note, notApplicable: saved.notApplicable)
                            store.update(project.id) { $0.reopen(key) }
                        }
                    }
                }
            } else {
                Text(key.instruction).foregroundStyle(.secondary)
                if let (label, url) = action {
                    Link(label, destination: url)
                }
                if canSkip {
                    Toggle("この案件には当てはまらない", isOn: form.notApplicable)
                }
                if key == .source && !project.sourceReady {
                    Text("「ソース監査」で検査を実行し、指摘を解消してください。").foregroundStyle(.secondary)
                } else if needsArchive && !project.hasMatchingArchive && !form.wrappedValue.notApplicable {
                    Text("先に配布用ビルドの照合を済ませてください。").foregroundStyle(.secondary)
                } else if project.phase.editable {
                    TextField(form.wrappedValue.notApplicable ? "対象外の理由" : "見た画面・項目・結果など", text: form.note, axis: .vertical)
                        .accessibilityLabel(form.wrappedValue.notApplicable ? "対象外の理由" : "確認した結果")
                    Button(form.wrappedValue.notApplicable ? "対象外として記録" : "確認結果を保存") {
                        store.update(project.id) { $0.setCheck(key, note: form.wrappedValue.note, notApplicable: form.wrappedValue.notApplicable) }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(form.wrappedValue.note.isBlank || (key == .account && !DeliveryProject.validStoreID(project.storeAppID))
                        || (key == .storefront && (project.listingBlocked || project.reviewNotes.isBlank)))
                }
            }
        }
    }
}
