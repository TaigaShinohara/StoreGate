import AppKit
import SwiftUI

struct ReleaseHandoff: Decodable, Sendable {
    var default_phase: String?
    var phases: [String: [HandoffStep]]
    let pending_audits: [String]
}

struct HandoffStep: Decodable, Identifiable, Sendable {
    var id: String { "\(code)/\(device ?? "")" }
    let code: String
    let title: String
    let owner: String
    let state: String
    let reason: String
    let resume_when: String
    let device: String?

    var ownerLabel: String {
        ["ai": "AI", "operator": "配信担当", "tester": "テスター", "owner": "本人", "store": "ストア"][owner] ?? ""
    }

    var stateLabel: String {
        ["ready": "着手可", "blocked": "要対応", "waiting": "確認待ち", "complete": "完了"][state] ?? state
    }
}

struct ReleaseReadbacks: Decodable, Sendable {
    var history: [Observation]

    struct Observation: Decodable, Identifiable, Sendable {
        let id: Int
        let kind: String
        let subject: String
        let source: String
        let outcome: String
        let observed_at: String
        let state: String?
        let message: String?
        let version: String?
        let build: String?
        let versionCode: String?

        var title: String {
            switch kind {
            case "device": "端末 · \(subject)"
            case "test_store": "ストア · テスト配信"
            default: "ストア · 本番配信"
            }
        }
    }
}

struct ReleaseApprovalPacket: Decodable, Sendable {
    let baseline_at: String?
    let changes: [Change]
    let sections: [Section]

    struct Change: Decodable, Identifiable, Sendable {
        let id: String
        let title: String
        let labels: [String]
    }

    struct Section: Decodable, Identifiable, Sendable {
        let id: String
        let title: String
        let rows: [Row]

        struct Row: Decodable, Sendable {
            let label: String
            let value: String
        }
    }
}

struct ResumePane: View {
    let steps: [HandoffStep]
    let history: [ReleaseReadbacks.Observation]

    var body: some View {
        if let first = steps.first {
            task(first)
            if steps.count > 1 {
                DisclosureGroup("残りの作業 \(steps.count - 1)件") {
                    ForEach(steps.dropFirst()) { remaining in
                        task(remaining).padding(.top, 6)
                    }
                }
            }
        }
        if !history.isEmpty {
            DisclosureGroup("読み取り照会の履歴") {
                ForEach(history) { observation in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(observation.title).font(.headline)
                        if let state = observation.state { Text(state) }
                        if let message = observation.message { Text(message) }
                        if let build = observation.build ?? observation.versionCode {
                            Text([observation.version, "ビルド \(build)"].compactMap { $0 }.joined(separator: " · "))
                        }
                        Text("\(observation.source) · \(displayTime(observation.observed_at))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }

    private func task(_ step: HandoffStep) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text([step.title, step.device].compactMap { $0 }.joined(separator: " · ")).font(.headline)
            Text([step.stateLabel, step.ownerLabel].filter { !$0.isEmpty }.joined(separator: " · "))
                .font(.callout).foregroundStyle(.secondary)
            Text(step.reason)
            if step.state != "complete" { Text(step.resume_when).foregroundStyle(.secondary) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct ApprovalPacketBody: View {
    let packet: ReleaseApprovalPacket

    var body: some View {
        if let baseline = packet.baseline_at {
            Text("差分の比較元 \(displayTime(baseline))").foregroundStyle(.secondary)
            if packet.changes.isEmpty {
                Text("比較元から変更なし")
            } else {
                ForEach(packet.changes) { change in
                    LabeledContent(change.title, value: change.labels.joined(separator: "、"))
                }
            }
        }
        ForEach(packet.sections) { section in
            if section.id == "release" {
                Text(section.title).font(.headline)
                rows(section)
            } else {
                DisclosureGroup(section.title) { rows(section).padding(.top, 8) }
            }
        }
    }

    private func rows(_ section: ReleaseApprovalPacket.Section) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(section.rows.indices, id: \.self) { index in
                let row = section.rows[index]
                VStack(alignment: .leading, spacing: 3) {
                    Text(row.label).font(.callout).foregroundStyle(.secondary)
                    Text(row.value).textSelection(.enabled)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private func displayTime(_ date: String) -> String {
    (try? Date.ISO8601FormatStyle(includingFractionalSeconds: true).parse(date))
        .map { $0.formatted(date: .abbreviated, time: .shortened) } ?? date
}

extension View {
    @ViewBuilder func storeGateCheckID(_ id: String) -> some View {
        #if STOREGATE_UI_CHECK
        accessibilityIdentifier(id)
            .background { DeliveryCheckAnchor(id: id).allowsHitTesting(false) }
            .onDisappear { DeliveryCheckAnchor.views.removeValue(forKey: id) }
        #else
        accessibilityIdentifier(id)
        #endif
    }
}

#if STOREGATE_UI_CHECK
@MainActor
struct DeliveryCheckAnchor: NSViewRepresentable {
    static var views: [String: NSView] = [:]
    let id: String

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        Self.views[id] = view
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) { Self.views[id] = nsView }
}
#endif
