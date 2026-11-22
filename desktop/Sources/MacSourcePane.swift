import SwiftUI

struct MacSourcePane: View {
    let project: DeliveryProject
    let store: DeliveryStore
    @Binding var running: Bool
    @State private var issue: String?

    var body: some View {
        WorkCard(title: "Macソース監査") {
            WorkDetails(title: "検査対象と範囲") {
                Text(project.folder).textSelection(.enabled)
                Text("SDK設定、用途説明、HTTP接続先、ポリシー参照、Privacy Manifestの静的監査です。署名資格・実機・ストア申告は「手動確認」で確認します。")
                    .foregroundStyle(.secondary)
            }
            if let report = project.sourceReport {
                WorkNotice(text: !report.complete ? "監査を完了できませんでした。下の理由を確認してください。"
                           : report.blocked ? "提出前に直す指摘があります。修正後に再監査してください。"
                           : "静的監査に重大な指摘はありません。手動確認へ進めます。",
                           symbol: report.complete && !report.blocked ? "checkmark.circle" : "exclamationmark.triangle",
                           shade: report.complete && !report.blocked ? .green : .orange)
                if project.phase.editable && report.engineFingerprint != DeliveryAudit.sourceEngineFingerprint {
                    WorkNotice(text: "監査ルールが更新されています。再監査してください。")
                }
                ForEach(report.issues, id: \.self) { Text($0) }
                ForEach(Array(report.findings.enumerated()), id: \.offset) { _, finding in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(finding.severity == "critical" ? "重大" : finding.severity == "high" ? "高" : "要確認").bold()
                        FindingGuidance(finding: finding)
                        WorkDetails(title: "検出箇所とルール") {
                            if let location = finding.location { Text(location).textSelection(.enabled) }
                            Text(finding.rule).foregroundStyle(.secondary)
                        }
                    }.padding(.vertical, 5)
                }
                Text("\(report.checkedAt.formatted(date: .abbreviated, time: .shortened)) · \(report.fileCount)ファイル")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let issue { WorkNotice(text: issue) }
            if project.phase.editable {
                Button(project.sourceReport == nil ? "Macソース監査を実行" : "Macソースを再監査") { inspect() }
                    .buttonStyle(.borderedProminent).disabled(running || store.saveProblem != nil)
                if running { ProgressView("ソースを検査中…") }
            }
        }
    }

    private func inspect() {
        let folder = project.folder
        running = true; issue = nil
        Task {
            defer { running = false }
            do {
                let report = try await DeliveryAudit.checkSource(folder: folder)
                store.update(project.id) {
                    guard $0.folder == folder else { return }
                    $0.recordSource(report)
                }
            } catch { issue = error.localizedDescription }
        }
    }
}

struct FindingGuidance: View {
    let finding: ListingFinding
    var body: some View {
        if finding.field == "source" {
            Text(finding.message)
            Text(finding.fix).foregroundStyle(.secondary)
            if let detail = finding.detail {
                DisclosureGroup("指摘の詳細を表示") { Text(detail).textSelection(.enabled) }
                    .font(.caption).foregroundStyle(.secondary)
            }
        } else {
            Text(AuditGuidance.text(for: finding.rule, part: "message"))
            Text(AuditGuidance.text(for: finding.rule, part: "fix")).foregroundStyle(.secondary)
            DisclosureGroup("指摘の原文を表示") {
                Text(finding.message).textSelection(.enabled)
                if !finding.fix.isBlank { Text(finding.fix).textSelection(.enabled) }
            }.font(.caption).foregroundStyle(.secondary)
        }
    }
}

enum AuditGuidance {
    static func text(for rule: String, part: String) -> String {
        let key = "finding.\(rule).\(part)"
        let fallbackKey = "finding.unknown.\(part)"
        // The app currently presents Japanese guidance; the catalog is its sole text source.
        let bundle = Bundle.main.path(forResource: "ja", ofType: "lproj").flatMap(Bundle.init(path:)) ?? .main
        let fallback = bundle.localizedString(forKey: fallbackKey, value: "入力内容を確認してください。", table: "Audit")
        return bundle.localizedString(forKey: key, value: fallback, table: "Audit")
    }
}
