import SwiftUI

struct StoreProgressPane: View {
    let project: DeliveryProject
    let store: DeliveryStore
    let show: (DeliveryStep?) -> Void
    let publication: Bool
    @State private var observation: ObservationAction?
    @State private var showRevision = false
    @State private var existingState: StoreProgress = .reviewQueue
    @State private var verifiedFingerprint: String?
    @State private var verifyingArchive = false
    @State private var archiveIssue: String?
    @State private var region = ""
    @State private var publicVersion = ""
    @State private var publicURL = ""
    @State private var publicIssue: String?

    var body: some View {
        WorkCard(title: project.phase.title) {
            LabeledContent("対象", value: "\(project.bundleID) · \(project.scope.version) · ビルド \(project.scope.build)")
            if let event = project.records.last {
                Text("最後に記録したストア状態 · \(event.date.formatted(date: .abbreviated, time: .shortened))")
                    .font(.caption).foregroundStyle(.secondary)
                Text(event.note).textSelection(.enabled)
            }
            if project.observedBeforeReadiness && !project.uploadBlockers.isEmpty {
                WorkNotice(text: "ストアで実際に見た状態を記録しています。StoreGate側には未確認の項目が残っています。", symbol: "info.circle", shade: .accentColor)
            }
            if publication { publicationActions } else if requiresHandoffVerification { handoffActions } else { reviewActions }
        }

        if let revision = project.revisions.last, project.phase == .revision {
            WorkCard(title: "差し戻しの理由と修正範囲") {
                Text(revision.note).textSelection(.enabled)
                Text(revision.areas.map(\.title).sorted().joined(separator: "、"))
                    .foregroundStyle(.secondary)
                if revision.areas.contains(.binary) && project.identity.build == revision.oldBuild {
                    WorkNotice(text: "新しいビルド番号を保存し、アーカイブを照合してください。")
                    Button("配布用ビルドへ移動") { show(.archive) }
                }
            }
        }

        if !publication && project.phase.editable && !project.uploadBlockers.isEmpty {
            WorkCard(title: "審査へ進む前に残っている確認") {
                ForEach(project.uploadBlockers) { item in
                    Button("\(item.title)を確認", systemImage: "arrow.forward") { show(item) }
                }
                if !project.accountWait.isBlank {
                    Text(project.accountWait).foregroundStyle(.secondary)
                }
            }
        }

        if !publication && project.scopeConfirmed && project.phase.editable && DeliveryProject.validStoreID(project.storeAppID) {
            DisclosureGroup("すでに審査へ送った案件を再開") {
                Text("現在App Store Connectで確認した状態から再開します。StoreGateで未確認の項目は残ります。")
                    .foregroundStyle(.secondary)
                Picker("現在のストア状態", selection: $existingState) {
                    ForEach([StoreProgress.reviewQueue, .inReview, .approved, .propagation], id: \.self) {
                        Text($0.title).tag($0)
                    }
                }
                Button("今のストア状態を記録…") {
                    observation = ObservationAction(label: "今のストア状態を記録", target: existingState, existing: true)
                }
            }
        }

        if !project.records.isEmpty {
            DisclosureGroup("ストア状態の記録") {
                ForEach(Array(project.records.reversed())) { event in
                    VStack(alignment: .leading, spacing: 3) {
                        Text("\(event.state.title) · \(event.identity.version) / \(event.identity.build)")
                            .font(.callout.bold())
                        Text(event.note)
                        Text(event.date.formatted(date: .abbreviated, time: .shortened))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        Color.clear.frame(height: 1)
            .accessibilityHidden(true)
            .sheet(item: $observation) { item in
                ObservationSheet(action: item, project: project, store: store)
            }
            .sheet(isPresented: $showRevision) {
                RevisionSheet(project: project, store: store)
            }
            .onChange(of: project.phase) { _, _ in verifiedFingerprint = nil }
    }

    private var requiresHandoffVerification: Bool {
        guard [.upload, .revision, .submission].contains(project.phase) else { return false }
        guard let fingerprint = project.archive?.fingerprint else { return true }
        return !project.uploadBlockers.isEmpty || verifiedFingerprint != fingerprint
    }

    @ViewBuilder private var handoffActions: some View {
        if !project.uploadBlockers.isEmpty {
            WorkNotice(text: "まだ必要な確認が残っています。下の項目を開いて結果を記録してください。")
        } else {
            Text("送付や選択を案内する前に、保存したアーカイブが未変更か照合します。")
                .foregroundStyle(.secondary)
            if let archiveIssue { WorkNotice(text: archiveIssue) }
            HStack {
                Button("提出対象のアーカイブを再確認") { verifyHandoffArchive() }
                    .buttonStyle(.borderedProminent)
                    .disabled(verifyingArchive)
                if verifyingArchive { ProgressView().controlSize(.small) }
                if archiveIssue != nil {
                    Button("配布用ビルドへ戻る") { show(.archive) }
                }
            }
        }
    }

    private func verifyHandoffArchive() {
        guard let evidence = project.archive else { return }
        verifyingArchive = true
        archiveIssue = nil
        Task {
            defer { verifyingArchive = false }
            do {
                _ = try await DeliveryAudit.verifySource(store.current(project.id) ?? project)
                let latest = try await ArchiveInspection.inspect(URL(fileURLWithPath: evidence.path))
                guard latest.sameSignedArtifact(as: evidence),
                      store.current(project.id)?.archive?.sameSignedArtifact(as: evidence) == true else {
                    archiveIssue = "照合したアーカイブが変わっています。配布用ビルドを選び直し、関係する確認をやり直してください。"
                    return
                }
                verifiedFingerprint = latest.fingerprint
            } catch {
                archiveIssue = error.localizedDescription
            }
        }
    }

    @ViewBuilder private var reviewActions: some View {
        switch project.phase {
        case .upload, .revision:
            Text("残っている確認を済ませたら、Xcode OrganizerからMac App Store用の対象ビルドをアップロードしてください。既に同じビルドを送り終えている場合は、処理済みで選べることを確認します。")
            Link("Appleのアップロード手順", destination: ReleaseLinks.upload)
            observationButton("アップロードしたことを記録", target: .processing, needsReadiness: true)
            observationButton("処理済みで選べるビルドを記録", target: .submission, needsReadiness: true, prominent: false)
        case .processing:
            Text("App Store Connectに対象のビルド番号が現れ、当該バージョンで選べるまで待ちます。待っている間はアプリを閉じても再開できます。")
            Link("App Store Connectでビルドを確認", destination: ReleaseLinks.connect)
            observationButton("処理済みで選べるビルドを記録", target: .submission, needsReadiness: true)
            Button("アップロードの問題を記録…") { showRevision = true }
        case .submission:
            Text("App Store Connectで対象Mac版を開き、正しいビルドを選択します。「審査に追加」は下書きです。「審査に提出」を実行し、審査待ちになったことを確認してください。")
            Link("App Store Connectを開く", destination: ReleaseLinks.connect)
            Link("Appleの提出手順", destination: ReleaseLinks.review)
            observationButton("審査待ちになったことを記録", target: .reviewQueue, needsReadiness: true)
            observationButton("すでに審査中の状態を記録", target: .inReview, needsReadiness: true, prominent: false)
            Button("ビルド・掲載情報の問題を記録…") { showRevision = true }
        case .reviewQueue, .inReview:
            Text("App Store Connectの「App審査」で対象バージョンを確認します。進展がなければ今は待機です。この状態から再開できます。")
            Link("App Store Connectで状況を確認", destination: ReleaseLinks.connect)
            Link("審査状態の見方", destination: ReleaseLinks.status)
            if project.phase == .reviewQueue {
                observationButton("審査が始まったことを記録", target: .inReview, prominent: false)
            }
            observationButton("承認されたことを記録", target: .approved)
            Button("差し戻しを記録…") { showRevision = true }
        case .approved, .propagation, .published:
            Button("公開の確認を表示") { show(.publication) }
        }
    }

    @ViewBuilder private var publicationActions: some View {
        switch project.phase {
        case .approved:
            let recordOnly = project.scope.method == .manual && project.observedBeforeReadiness && !project.uploadBlockers.isEmpty
            Text(recordOnly
                 ? "StoreGate側の確認が残っているため、ここではストアで見た事実の記録だけを行います。"
                 : project.scope.method == .manual
                 ? "App Store Connectの対象バージョンで「このバージョンをリリース」を実行します。処理が終わるまで、ここで公開確認をしないでください。"
                 : "自動公開が設定されています。公開の反映を待ち、指定した地域のストアで今回のバージョンを確認します。")
            if recordOnly {
                Link("App Store Connectで状態を確認", destination: ReleaseLinks.connect)
                observationButton("公開処理が始まっていることを記録", target: .propagation)
            } else {
                Link("App Store Connectを開く", destination: ReleaseLinks.connect)
                Link("公開方法の手順", destination: ReleaseLinks.release)
                observationButton(project.scope.method == .manual ? "公開操作を実行したことを記録" : "反映待ちに進む", target: .propagation)
            }
        case .propagation:
            Text("配信先のApp Store商品ページを開き、バージョン履歴で今回の版を確認します。旧版や別のアプリのページは完了にできません。")
            Link("App Store Connectで配信状況を確認", destination: ReleaseLinks.connect)
            Picker("確認する地域", selection: $region) {
                Text("地域を選ぶ").tag("")
                ForEach(project.scope.regionCodes, id: \.self) { code in Text(code).tag(code) }
            }
            TextField("商品ページに表示されたバージョン", text: $publicVersion)
            TextField("App Storeの商品ページURL", text: $publicURL)
            if let publicIssue { WorkNotice(text: publicIssue) }
            Button("この地域の公開を記録") { recordPublication() }
                .buttonStyle(.borderedProminent)
                .disabled(region.isEmpty || publicVersion.isBlank || publicURL.isBlank)
        case .published:
            Label("対象の版が全地域で公開されていることを記録しました。", systemImage: "checkmark.circle.fill")
        default:
            Text("審査の承認後、公開方法に応じた手順をここに表示します。")
            Button("アップロードと審査を表示") { show(.review) }
        }
        ForEach(project.publications.sorted(by: { $0.region < $1.region })) { item in
            if let url = URL(string: item.url) {
                Link("\(item.region) · 版 \(item.version) · \(item.date.formatted(date: .abbreviated, time: .shortened))", destination: url)
            }
            if project.phase == .published || project.phase == .propagation {
                Button("\(item.region) の公開確認を開き直す") {
                    store.update(project.id) { $0.reopenPublication(item.region) }
                }
                .buttonStyle(.borderless)
            }
        }
    }

    @ViewBuilder private func observationButton(
        _ label: String, target: StoreProgress, needsReadiness: Bool = false, prominent: Bool = true
    ) -> some View {
        if prominent {
            Button(label) { observation = ObservationAction(label: label, target: target, verifyArchive: needsReadiness) }
                .buttonStyle(.borderedProminent)
                .disabled(needsReadiness && !project.uploadBlockers.isEmpty)
        } else {
            Button(label) { observation = ObservationAction(label: label, target: target, verifyArchive: needsReadiness) }
                .disabled(needsReadiness && !project.uploadBlockers.isEmpty)
        }
    }

    private func recordPublication() {
        var confirmed = false
        let persisted = store.update(project.id) {
            confirmed = $0.confirmPublication(region: region, version: publicVersion, url: publicURL.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        if confirmed && persisted {
            publicIssue = nil
            publicVersion = ""; publicURL = ""; region = ""
        } else if !confirmed && store.saveProblem == nil {
            publicIssue = "今回のバージョン、配信地域、登録したアプリIDと一致する App Store のURLを確認してください。"
        }
    }
}

private struct ObservationAction: Identifiable {
    var id: String { target.rawValue }
    let label: String
    let target: StoreProgress
    var existing = false
    var verifyArchive = false
}

private struct ObservationSheet: View {
    let action: ObservationAction
    let project: DeliveryProject
    @ObservedObject var store: DeliveryStore
    @Environment(\.dismiss) private var dismiss
    @State private var version = ""
    @State private var build = ""
    @State private var appID = ""
    @State private var note = ""
    @State private var issue: String?
    @State private var acceptedForRetry = false
    @State private var checking = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(action.label).font(.title2).accessibilityAddTraits(.isHeader)
            Text("見た画面で対象アプリと版を照合し、確認した状態を記録します。予定は Apple ID \(project.storeAppID) / 版 \(project.scope.version) / ビルド \(project.scope.build) です。")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Group {
                TextField("確認したApple ID", text: $appID)
                TextField("確認したバージョン", text: $version)
                TextField("確認したビルド番号", text: $build)
                TextField("確認した画面と状態", text: $note, axis: .vertical)
            }
            .disabled(store.saveProblem != nil || checking)
            if checking { ProgressView("提出対象のアーカイブを確認中…") }
            if let issue { WorkNotice(text: issue) }
            if let problem = store.saveProblem { WorkNotice(text: problem) }
            HStack {
                Button(store.saveProblem == nil ? "キャンセル" : "閉じる") { dismiss() }
                    .keyboardShortcut(.cancelAction).disabled(checking)
                Spacer()
                if acceptedForRetry && store.saveProblem != nil {
                    Button("保存を再試行") {
                        store.retrySave()
                        if store.saveProblem == nil { dismiss() }
                    }
                    .buttonStyle(.borderedProminent)
                } else {
                    Button("確認結果を保存") { submit() }
                        .buttonStyle(.borderedProminent)
                        .disabled([appID, version, build, note].contains(where: \.isBlank) || store.saveProblem != nil || checking)
                }
            }
        }
        .padding(24)
        .frame(width: 485)
    }

    private func submit() {
        let observed = BuildIdentity(bundleID: project.bundleID, version: version, build: build)
        guard action.verifyArchive else {
            persist(observed)
            return
        }
        guard let saved = store.current(project.id)?.archive else {
            issue = "照合結果がありません。「配布用ビルド」でアーカイブを選んでください。"
            return
        }
        checking = true
        issue = nil
        Task {
            defer { checking = false }
            do { _ = try await DeliveryAudit.verifySource(store.current(project.id) ?? project) }
            catch { issue = error.localizedDescription; return }
            guard let latest = try? await ArchiveInspection.inspect(URL(fileURLWithPath: saved.path)),
                  latest.sameSignedArtifact(as: saved),
                  store.current(project.id)?.archive?.sameSignedArtifact(as: saved) == true else {
                issue = "提出対象のアーカイブが見つからないか、照合時から変わりました。「配布用ビルド」で選び直してください。"
                return
            }
            persist(observed)
        }
    }

    private func persist(_ observed: BuildIdentity) {
        var accepted = false
        let persisted = store.update(project.id) {
            guard $0.storeAppID == appID else { return }
            accepted = action.existing
                ? $0.observeExistingStatus(action.target, note: note, observed: observed)
                : $0.advance(action.target, note: note, observed: observed)
        }
        if accepted && persisted { dismiss() }
        else if accepted { acceptedForRetry = true }
        else { issue = "確認したApple ID・版・ビルド、または必要な工程の記録が、今回の案件と一致しません。" }
    }
}

private struct RevisionSheet: View {
    let project: DeliveryProject
    @ObservedObject var store: DeliveryStore
    @Environment(\.dismiss) private var dismiss
    @State private var note = ""
    @State private var areas: Set<RevisionArea> = []

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("修正する理由と範囲を記録").font(.title2).accessibilityAddTraits(.isHeader)
            Group {
                TextField("Appleの指摘、問題、次に直すこと", text: $note, axis: .vertical)
                ForEach(RevisionArea.allCases) { area in
                    Toggle(area.title, isOn: Binding(get: { areas.contains(area) }, set: {
                        if $0 { areas.insert(area) } else { areas.remove(area) }
                    }))
                }
            }
            .disabled(store.saveProblem != nil)
            Text("選んだ範囲に関連する確認を未確認に戻します。ビルドを変える場合は新しいビルド番号が必要です。")
                .foregroundStyle(.secondary)
            if let problem = store.saveProblem { WorkNotice(text: problem) }
            HStack {
                Button(store.saveProblem == nil ? "キャンセル" : "閉じる") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                if store.saveProblem != nil {
                    Button("保存を再試行") {
                        store.retrySave()
                        if store.saveProblem == nil { dismiss() }
                    }
                    .buttonStyle(.borderedProminent)
                } else {
                    Button("修正作業に戻る") {
                        if store.update(project.id, { $0.requestRevision(note: note, areas: areas) }) { dismiss() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(note.isBlank || areas.isEmpty)
                }
            }
        }
        .padding(24)
        .frame(width: 485)
    }
}
