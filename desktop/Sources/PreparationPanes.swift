import AppKit
import SwiftUI

struct ChecksPane: View {
    let project: DeliveryProject
    let store: DeliveryStore
    let showArchive: () -> Void
    @State private var running = false
    @State private var issue: String?

    var body: some View {
        WorkCard(title: "配信地域・機能に当てはまるルール") {
            if !project.scopeConfirmed {
                Text("先に配信条件を保存してください。")
            } else {
                Text("配信地域 \(project.scope.regionCodes.joined(separator: ", "))。StoreGateに同梱された規制台帳を、この案件の設定で照合します。")
                    .foregroundStyle(.secondary)
                if project.rulesCurrent, let checkedAt = project.rulesCheckedAt, let rules = project.rules {
                    Text("台帳の照合 \(checkedAt.formatted(date: .abbreviated, time: .shortened)) · \(rules.count)件")
                    if rules.isEmpty {
                        Text("同梱台帳に該当項目はありません。他の確認は下に続きます。")
                            .foregroundStyle(.secondary)
                    }
                } else if project.rules != nil && !running {
                    WorkNotice(text: "前回の台帳照合は再確認待ちです。照合が完了すると提出前の確認に使えます。")
                }
                if running { ProgressView("適用ルールを確認中…") }
                if let issue { WorkNotice(text: issue) }
                Button(project.rules == nil ? "適用ルールを確認" : "適用ルールを再確認") { auditRules() }
                    .buttonStyle(.borderedProminent)
                    .disabled(running || !project.phase.editable)
            }
        }
        if let rows = project.rules {
            if project.rulesCurrent {
                ForEach(rows) { row in
                    RuleCard(row: row, project: project, store: store)
                }
            } else if !rows.isEmpty {
                DisclosureGroup("前回の適用ルールを参照（再確認前）") {
                    ForEach(rows) { row in
                        RuleCard(row: row, project: project, store: store)
                    }
                }
            }
        }
        if !project.hasMatchingArchive {
            WorkNotice(text: "実機や署名が必要な項目は、配布用ビルドを照合したあと確認します。", symbol: "info.circle", shade: .accentColor)
            Button("配布用ビルドへ移動", action: showArchive)
        }
        ForEach([DeliveryCheck.source, .privacy, .reviewAccess, .purchases, .runtime]) { key in
            CheckCard(key: key, project: project, store: store,
                      action: key == .source ? ("StoreGateのチェックリスト", ReleaseLinks.checklist) : nil)
        }
    }

    private func auditRules() {
        issue = nil
        let scope = project.scope
        store.update(project.id) { $0.rulesCheckedAt = nil }
        guard store.saveProblem == nil else { return }
        running = true
        Task {
            defer { running = false }
            do {
                let rows = try await DeliveryAudit.applicableRules(regions: scope.regionCodes, features: scope.featureCodes)
                store.update(project.id) { $0.setRules(rows, for: scope) }
            } catch {
                issue = error.localizedDescription
            }
        }
    }
}

private struct RuleCard: View {
    let row: ApplicableRule
    let project: DeliveryProject
    let store: DeliveryStore
    @State private var note = ""

    private var canEdit: Bool { project.phase.editable && project.rulesCurrent }

    var body: some View {
        WorkCard(title: row.title) {
            Label(row.blocksRelease ? "提出前の確認が必要" : row.sourceVerified ? "参考・施行時期を確認" : "公式根拠を確認してください",
                  systemImage: row.blocksRelease ? "exclamationmark.circle" : "info.circle")
            Text(row.check).fixedSize(horizontal: false, vertical: true)
            ForEach(Array(Set(row.sources)).sorted(), id: \.self) { source in
                if let url = URL(string: source), url.scheme == "https" {
                    Link("根拠を開く · \(url.host ?? source)", destination: url)
                }
            }
            if let saved = project.ruleProof[row.id] {
                Text("\(project.rulesCurrent ? "確認済み" : "前回の確認") · \(saved.note)").textSelection(.enabled)
                if canEdit {
                    Button("確認を開き直す") { store.update(project.id) { $0.ruleProof[row.id] = nil } }
                }
            } else if row.blocksRelease && canEdit {
                TextField("確認した資料と結果", text: $note)
                Button("この要件の確認結果を保存") {
                    store.update(project.id) {
                        guard $0.phase.editable, $0.rulesCurrent, $0.rules?.contains(row) == true else { return }
                        $0.ruleProof[row.id] = Verification(note: note)
                    }
                }
                .disabled(note.isBlank)
            }
        }
    }
}

struct ListingPane: View {
    let project: DeliveryProject
    let store: DeliveryStore
    @State private var selectedLanguage: String?
    @State private var newLanguage = ""
    @State private var savingAudit = false
    @State private var issue: String?
    @FocusState private var fieldFocus: String?

    private var availableLocales: [String] {
        [project.scope.locale] + project.localeDrafts.keys.filter { $0 != project.scope.locale }.sorted()
    }
    private var language: String {
        if let selectedLanguage, availableLocales.contains(selectedLanguage) { return selectedLanguage }
        return project.scope.locale
    }
    private var languageSelection: Binding<String> {
        Binding(get: { language }, set: { selectedLanguage = $0 })
    }
    private var notes: Binding<String> {
        Binding(
            get: { store.current(project.id)?.reviewNotes ?? project.reviewNotes },
            set: { text in store.update(project.id) { $0.setNotes(text) } }
        )
    }
    private var report: ListingReport? {
        let savedDraft = project.draft(for: language)
        let currentDraft = store.current(project.id)?.draft(for: language) ?? savedDraft
        return currentDraft == savedDraft ? project.report(for: language) : nil
    }

    var body: some View {
        Group {
            if !project.scopeConfirmed {
                WorkNotice(text: "配信設定で掲載言語を決めてから原稿を準備します。")
            } else {
                WorkCard(title: "掲載原稿を検査する") {
                    Picker("掲載言語", selection: languageSelection) {
                        ForEach(availableLocales, id: \.self) { Text($0).tag($0) }
                    }
                    .disabled(savingAudit)
                    if project.phase.editable {
                        HStack {
                            TextField("追加する言語のコード", text: $newLanguage)
                                .accessibilityLabel("追加する掲載言語")
                            Button("掲載言語を追加") { addLocale() }
                                .disabled(savingAudit || !localeIsValid(newLanguage) || availableLocales.contains(newLanguage))
                        }
                    }
                    if project.phase.editable {
                        Text("入力は自動で保存されます。原稿の変更後は、掲載情報を検査してください。")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    editor.id(language)
                    actions
                    result
                }
                .onChange(of: language) { _, _ in
                    issue = nil
                    fieldFocus = nil
                }

                WorkCard(title: "審査担当者への案内を書く") {
                    Text("主要機能への手順と、アプリの目的を記入します。テスト用パスワードは記入せず、App Store Connectへ直接入力してください。")
                        .foregroundStyle(.secondary)
                    TextEditor(text: notes)
                        .font(.body)
                        .frame(minHeight: 82)
                        .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(Color.secondary.opacity(0.3)))
                        .accessibilityLabel("審査メモ")
                        .disabled(!project.phase.editable)
                    HStack {
                        if !project.reviewNotes.isBlank {
                            Button("審査メモをコピー") {
                                NSPasteboard.general.clearContents()
                                NSPasteboard.general.setString(project.reviewNotes, forType: .string)
                            }
                        }
                        Link("StoreGateの審査メモ見本", destination: ReleaseLinks.reviewNotes)
                    }
                }
                CheckCard(key: .storefront, project: project, store: store, action: ("App Store Connectを開く", ReleaseLinks.connect))
                    .disabled(savingAudit)
            }
        }
    }

    private var editor: some View {
        Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
            GridRow { Text("アプリ名"); entry("name", field(\.name), "アプリ名") }
            GridRow { Text("サブタイトル"); entry("subtitle", field(\.subtitle), "サブタイトル") }
            GridRow {
                Text("説明文")
                TextEditor(text: field(\.description))
                    .font(.body)
                    .frame(minHeight: 85)
                    .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(Color.secondary.opacity(0.3)))
                    .accessibilityLabel("説明文")
                    .focused($fieldFocus, equals: "description")
            }
            GridRow { Text("キーワード"); entry("keywords", field(\.keywords), "キーワード") }
            GridRow { Text("プライバシーURL"); entry("privacy_url", field(\.privacyURL), "プライバシーURL") }
            GridRow { Text("サポートURL"); entry("support_url", field(\.supportURL), "サポートURL") }
            GridRow { Text("利用規約URL"); entry("terms_url", field(\.termsURL), "利用規約URL") }
        }
        .disabled(!project.phase.editable)
    }

    private func field(_ keyPath: WritableKeyPath<ListingDraft, String>) -> Binding<String> {
        let locale = language
        return Binding(
            get: {
                let draft = store.current(project.id)?.draft(for: locale) ?? project.draft(for: locale)
                return draft[keyPath: keyPath]
            },
            set: { text in
                store.update(project.id) { value in
                    var draft = value.draft(for: locale)
                    draft[keyPath: keyPath] = text
                    value.setListing(draft, locale: locale)
                }
            }
        )
    }

    private func entry(_ key: String, _ value: Binding<String>, _ title: String) -> some View {
        TextField(title, text: value)
            .accessibilityLabel(title)
            .focused($fieldFocus, equals: key)
    }

    private var actions: some View {
        HStack {
            Button(report == nil ? "掲載情報を検査" : "掲載情報を再検査") { inspect() }
                .buttonStyle(.borderedProminent)
                .disabled(savingAudit || !project.phase.editable)
            if savingAudit { ProgressView().controlSize(.small).accessibilityLabel("掲載情報を検査中") }
        }
    }

    @ViewBuilder private var result: some View {
        if let issue { WorkNotice(text: issue) }
        if let report {
            WorkNotice(text: report.blocked ? "提出前に直す指摘があります。" : "掲載文の静的監査で重大な指摘はありません。URLの到達性・画像・実際の掲載内容は別の確認です。",
                       symbol: report.blocked ? "exclamationmark.triangle" : "checkmark.circle",
                       shade: report.blocked ? .orange : .green)
            Text("検査日時 \(report.checkedAt.formatted(date: .abbreviated, time: .shortened))")
                .font(.caption).foregroundStyle(.secondary)
            ForEach(Array(report.findings.enumerated()), id: \.offset) { _, item in
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(severity(item.severity)) · \(fieldLabel(item.field))").font(.callout.bold())
                    Text(item.message)
                    if !item.fix.isBlank { Text(item.fix).foregroundStyle(.secondary) }
                    if ["name", "description", "subtitle", "keywords", "privacy_url", "support_url", "terms_url"].contains(item.field) {
                        Button("\(fieldLabel(item.field))の入力欄に移動") { fieldFocus = item.field }
                            .disabled(!project.phase.editable)
                    }
                    Text(item.rule).font(.caption).foregroundStyle(.secondary)
                }
            }
        } else if issue == nil && !savingAudit {
            Text("この言語の原稿は未検査です。")
                .font(.callout).foregroundStyle(.secondary)
        }
    }

    private func severity(_ value: String) -> String {
        ["critical": "重大", "high": "高", "medium": "要確認", "low": "参考"][value] ?? value
    }

    private func fieldLabel(_ key: String) -> String {
        ["name": "アプリ名", "description": "説明文", "subtitle": "サブタイトル", "keywords": "キーワード",
         "privacy_url": "プライバシーURL", "support_url": "サポートURL", "terms_url": "利用規約URL"][key] ?? key
    }

    private func localeIsValid(_ locale: String) -> Bool {
        locale.range(of: "^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$", options: .regularExpression) != nil
    }

    private func addLocale() {
        let value = newLanguage
        store.update(project.id) { $0.setListing(ListingDraft(), locale: value) }
        if store.saveProblem == nil {
            newLanguage = ""
            selectedLanguage = value
        }
    }

    private func inspect() {
        let locale = language
        let snapshot = (store.current(project.id) ?? project).draft(for: locale)
        savingAudit = true
        issue = nil
        store.update(project.id) { $0.clearReport(locale: locale) }
        guard store.saveProblem == nil else { savingAudit = false; return }
        Task {
            defer { savingAudit = false }
            do {
                let report = try await DeliveryAudit.checkListing(snapshot)
                store.update(project.id) { $0.recordReport(report, for: snapshot, locale: locale) }
            } catch {
                issue = error.localizedDescription
            }
        }
    }
}
