import AppKit
import SwiftUI

struct ChecksPane: View {
    let project: DeliveryProject
    let store: DeliveryStore
    let showArchive: () -> Void
    @State private var running = false
    @State private var sourceRunning = false
    @State private var issue: String?
    @State private var section = 0
    @State private var selectedCheck = DeliveryCheck.source
    @State private var checkDrafts: [DeliveryCheck: CheckEntryDraft] = [:]
    @State private var ruleNotes: [String: String] = [:]

    private let manualChecks: [DeliveryCheck] = [.source, .privacy, .reviewAccess, .purchases, .runtime]

    var body: some View {
        Picker("確認の種類", selection: $section) {
            Text("ソース監査").tag(0)
            Text("適用ルール").tag(1)
            Text("手動確認").tag(2)
        }
        .pickerStyle(.segmented)
        .disabled(sourceRunning || running)

        if section == 0 {
            MacSourcePane(project: project, store: store, running: $sourceRunning)
        } else if section == 1 {
            rules
        } else {
            Picker("確認項目", selection: $selectedCheck) {
                ForEach(manualChecks) { key in
                    Text(key.title + (project.openChecks.contains(key) ? " · 未確認" : " · 確認済み")).tag(key)
                }
            }
            if !project.hasMatchingArchive {
                WorkNotice(text: "実機・署名の確認には配布用ビルドが必要です。", symbol: "info.circle", shade: .accentColor)
                Button("配布用ビルドへ移動", action: showArchive)
            }
            CheckCard(key: selectedCheck, project: project, store: store,
                      action: selectedCheck == .source ? ("StoreGateのチェックリスト", ReleaseLinks.checklist) : nil,
                      draft: checkDraft(selectedCheck))
                .id(selectedCheck)
        }
        Color.clear.frame(height: 0)
            .accessibilityHidden(true)
            .onAppear {
                section = !project.sourceReady ? 0 : !project.rulesReady ? 1 : 2
                selectedCheck = project.openChecks.first ?? .source
            }
    }

    @ViewBuilder private var rules: some View {
        WorkCard(title: "配信地域・機能に当てはまるルール") {
            if !project.scopeConfirmed {
                Text("先に配信条件を保存してください。")
            } else {
                Text("配信地域：\(project.scope.regionCodes.joined(separator: ", "))")
                if (project.rulesCurrent || !project.phase.editable), let checkedAt = project.rulesCheckedAt, let rules = project.rules {
                    Text("台帳の照合 \(checkedAt.formatted(date: .abbreviated, time: .shortened)) · \(rules.count)件")
                    if rules.isEmpty {
                        Text("同梱台帳に該当項目はありません。")
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
            if project.rulesCurrent || !project.phase.editable {
                ForEach(rows) { row in
                    ruleCard(row)
                }
            } else if !rows.isEmpty {
                DisclosureGroup("前回の適用ルールを参照（再確認前）") {
                    ForEach(rows) { row in
                        ruleCard(row)
                    }
                }
            }
        }
    }

    private func checkDraft(_ key: DeliveryCheck) -> Binding<CheckEntryDraft> {
        Binding(get: { checkDrafts[key] ?? CheckEntryDraft() }, set: { checkDrafts[key] = $0 })
    }

    private func ruleCard(_ row: ApplicableRule) -> some View {
        RuleCard(row: row, project: project, store: store,
                 note: Binding(get: { ruleNotes[row.id] ?? "" }, set: { ruleNotes[row.id] = $0 }))
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
    @Binding var note: String

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
    @State private var localeToRemove: String?
    @State private var savingAudit = false
    @State private var issue: String?
    @FocusState private var fieldFocus: String?
    @State private var section = 0

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
        guard currentDraft == savedDraft, let report = project.report(for: language) else { return nil }
        return !project.phase.editable || report.current(for: currentDraft) ? report : nil
    }

    var body: some View {
        Group {
            if !project.scopeConfirmed {
                WorkNotice(text: "配信設定で掲載言語を決めてから原稿を準備します。")
            } else {
                Picker("掲載準備の作業", selection: $section) {
                    Text("掲載原稿").tag(0)
                    Text("審査メモ").tag(1)
                    Text("ストアとの照合").tag(2)
                }
                .pickerStyle(.segmented)
                .disabled(savingAudit)
                if section == 0 {
                    listingCard
                } else if section == 1 {
                    notesCard
                } else {
                    CheckCard(key: .storefront, project: project, store: store,
                              action: ("App Store Connectを開く", ReleaseLinks.connect))
                }
            }
        }
        .onAppear {
            section = project.listingBlocked ? 0 : project.reviewNotes.isBlank ? 1 : 2
        }
    }

    private var listingCard: some View {
        WorkCard(title: "掲載原稿") {
            Picker("掲載言語", selection: languageSelection) {
                ForEach(availableLocales, id: \.self) { Text(StoreLocales.label($0)).tag($0) }
            }
            .disabled(savingAudit)
            if project.phase.editable {
                WorkDetails(title: "掲載言語の追加・削除") {
                    HStack {
                        Picker("追加する掲載言語", selection: $newLanguage) {
                            Text("言語を選ぶ").tag("")
                            ForEach(StoreLocales.codes.filter { !availableLocales.contains($0) }, id: \.self) {
                                Text(StoreLocales.label($0)).tag($0)
                            }
                        }
                        Button("掲載言語を追加") { addLocale() }
                            .disabled(savingAudit || !localeIsValid(newLanguage) || availableLocales.contains(newLanguage))
                        if language != project.scope.locale {
                            Button("この掲載言語を削除…", role: .destructive) { localeToRemove = language }
                                .disabled(savingAudit)
                        }
                    }
                }
            }
            if project.report(for: language) != nil && report == nil {
                WorkNotice(text: "監査ルールが更新されたため、この原稿を再検査してください。保存した原稿は保持しています。")
            }
            editor.id(language)
            actions
            result
        }
        .alert("この掲載言語を削除しますか？", isPresented: Binding(get: { localeToRemove != nil }, set: { if !$0 { localeToRemove = nil } })) {
            Button("キャンセル", role: .cancel) { localeToRemove = nil }
            Button("原稿と監査結果を削除", role: .destructive) {
                guard let locale = localeToRemove else { return }
                store.update(project.id) { $0.removeLocale(locale) }
                if store.saveProblem == nil { selectedLanguage = project.scope.locale; localeToRemove = nil }
            }
        } message: {
            let locale = localeToRemove ?? language
            Text("\(StoreLocales.label(locale)) の原稿と監査結果を削除します。アプリ名：\(project.draft(for: locale).name)。主要掲載言語は残ります。")
        }
        .onChange(of: language) { _, _ in
            issue = nil
            fieldFocus = nil
        }
    }

    private var notesCard: some View {
        WorkCard(title: "審査メモ") {
            Text("目的と主要操作の手順を記入します。パスワードはApp Store Connectへ直接入力してください。")
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
            WorkNotice(text: report.blocked ? "提出前に直す指摘があります。" : "掲載文の静的監査に重大な指摘はありません。",
                       symbol: report.blocked ? "exclamationmark.triangle" : "checkmark.circle",
                       shade: report.blocked ? .orange : .green)
            WorkDetails(title: "検査範囲と日時") {
                Text("URLの到達性・画像・実際の掲載内容は「ストアとの照合」で確認します。")
                Text(report.checkedAt.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(Array(report.findings.enumerated()), id: \.offset) { _, item in
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(severity(item.severity)) · \(fieldLabel(item.field))").font(.callout.bold())
                    FindingGuidance(finding: item)
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
        StoreLocales.canonical(locale) != nil
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
