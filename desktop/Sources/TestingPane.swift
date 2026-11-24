import AppKit
import SwiftUI

struct SharedTesting: Decodable, Sendable {
    let channel: String
    let invite: String?
    let stages: [String: [TestObservation]]
    let history: [TestObservation]

    var installedDevices: [String] {
        (stages["installation"] ?? []).filter { $0.outcome == "pass" }.map(\.device)
    }
}

struct TestObservation: Decodable, Identifiable, Sendable {
    let id: Int
    let stage: TestStage
    let device: String
    let operation: String
    let time: String
    let outcome: String
    let source: TestSource
    let note: String
    let os: String?
    let version: String?
    let build: String?
    let buildSource: TestSource?
    let installationSource: TestSource?

    enum CodingKeys: String, CodingKey {
        case id, stage, device, operation, time, outcome, source, note, os, version, build
        case buildSource = "build_source"
        case installationSource = "installation_source"
    }

    var result: String { outcome == "pass" ? "確認済み" : "要確認" }
    var symbol: String { outcome == "pass" ? "checkmark.circle" : "exclamationmark.triangle" }

    var buildDetails: String? {
        guard let version, let build else { return nil }
        let versionText = version.isEmpty ? "版未確認" : "版 \(version)（\((installationSource ?? source).title)）"
        let buildText = build.isEmpty ? "ビルド未確認" : "ビルド \(build)（\(buildSource?.title ?? "確認元未記録")）"
        return [os, versionText, buildText].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }
}

enum TestSource: String, CaseIterable, Decodable, Identifiable, Sendable {
    case store, device, tester

    var id: Self { self }

    var title: String {
        switch self {
        case .store: "ストア管理画面"
        case .device: "端末で直接確認"
        case .tester: "テスター報告"
        }
    }
}

enum TestStage: String, CaseIterable, Decodable, Identifiable, Sendable {
    case distribution, participation, availability, installation, qa

    var id: Self { self }

    var title: String {
        switch self {
        case .distribution: "ストアのテスト配信"
        case .participation: "テストへの参加"
        case .availability: "配布ページでの入手可否"
        case .installation: "端末へのインストール"
        case .qa: "実機での操作確認"
        }
    }

    var sources: [TestSource] {
        switch self {
        case .distribution: [.store]
        case .participation: [.tester, .device, .store]
        case .availability, .installation, .qa: [.tester, .device]
        }
    }
}

@MainActor
struct TestingPane: View {
    let testing: SharedTesting
    let releaseID: String
    let app: String
    let version: String
    let build: String
    let busy: Bool
    let save: ([String], @escaping (String?) -> Void) -> Void

    @State private var edit: Edit?
    @State private var copied = false

    private enum Edit: Identifiable {
        case link
        case observation(TestStage, TestObservation?)

        var id: String {
            switch self {
            case .link: "link"
            case let .observation(stage, item): "\(stage.rawValue)/\(item?.id ?? 0)"
            }
        }

        var observation: (TestStage?, TestObservation?) {
            if case let .observation(stage, item) = self { return (stage, item) }
            return (nil, nil)
        }
    }

    private var isTestFlight: Bool { testing.channel == "TestFlight" }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("\(testing.channel) のテスト").font(.headline).accessibilityAddTraits(.isHeader)
            HStack {
                Button(testing.invite == nil ? "参加リンクを登録…" : "参加リンクを編集…") { edit = .link }
                if let invite = testing.invite {
                    Button("端末向け案内をコピー") {
                        NSPasteboard.general.clearContents()
                        let directions = isTestFlight
                            ? "テスト端末でリンクを開き、TestFlightで対象ビルドをインストールしてください。"
                            : "テスト端末でリンクを開き、登録済みのGoogleアカウントで参加してからインストールしてください。"
                        let text = "\(app) \(version)（ビルド \(build)）\n\(testing.channel)\n\(directions)\n\(invite)"
                        copied = NSPasteboard.general.setString(text, forType: .string)
                    }
                    if copied { Text("コピー済み").foregroundStyle(.secondary) }
                }
            }
            .onChange(of: testing.invite) { _, _ in copied = false }
            ForEach(TestStage.allCases) { stage in
                let items = testing.stages[stage.rawValue] ?? []
                HStack {
                    Text(stage.title)
                    if items.isEmpty { Text("未記録").foregroundStyle(.secondary) }
                    Spacer()
                    Button("記録…") { edit = .observation(stage, nil) }
                        .accessibilityLabel("\(stage.title)を記録")
                        .disabled(stage == .qa && testing.installedDevices.isEmpty)
                }
                ForEach(items) { item in
                    observation(item, inHistory: false)
                }
            }
            if !testing.history.isEmpty {
                DisclosureGroup("過去のテスト記録") {
                    ForEach(testing.history) { item in
                        observation(item, inHistory: true)
                    }
                }
            }
        }
        .disabled(busy)
        .sheet(item: $edit) { item in
            let (stage, entry) = item.observation
            TestEntrySheet(
                releaseID: releaseID, scope: "\(app) \(version)（ビルド \(build)） · \(testing.channel)",
                stage: stage, entry: entry, installedDevices: testing.installedDevices, isTestFlight: isTestFlight,
                invite: testing.invite, busy: busy, save: save
            )
        }
    }

    private func observation(_ item: TestObservation, inHistory: Bool) -> some View {
        let title = [inHistory ? item.stage.title : "", item.device, item.operation, item.result]
            .filter { !$0.isEmpty }.joined(separator: " · ")
        return DisclosureGroup {
            VStack(alignment: .leading, spacing: 5) {
                if let build = item.buildDetails { Text(build).font(.caption) }
                Text(item.note).textSelection(.enabled)
                if let date = try? Date.ISO8601FormatStyle(includingFractionalSeconds: true).parse(item.time) {
                    Text("\(item.source.title) · \(date.formatted(date: .abbreviated, time: .shortened))")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(item.source.title).font(.caption).foregroundStyle(.secondary)
                }
                if !inHistory {
                    Button("再記録…") { edit = .observation(item.stage, item) }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: {
            Label(title, systemImage: item.symbol)
        }
    }
}

@MainActor
private struct TestEntrySheet: View {
    let releaseID: String
    let scope: String
    let stage: TestStage?
    let installedDevices: [String]
    let isTestFlight: Bool
    let hadInvite: Bool
    let busy: Bool
    let save: ([String], @escaping (String?) -> Void) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var device: String
    @State private var source: TestSource
    @State private var outcome: String
    @State private var operation: String
    @State private var os: String
    @State private var version: String
    @State private var build: String
    @State private var buildSource: TestSource
    @State private var note = ""
    @State private var url: String
    @State private var error: String?

    init(releaseID: String, scope: String, stage: TestStage?, entry: TestObservation?, installedDevices: [String], isTestFlight: Bool,
         invite: String?, busy: Bool, save: @escaping ([String], @escaping (String?) -> Void) -> Void) {
        self.releaseID = releaseID
        self.scope = scope
        self.stage = stage
        self.installedDevices = installedDevices
        self.isTestFlight = isTestFlight
        self.hadInvite = invite != nil
        self.busy = busy
        self.save = save
        _device = State(initialValue: entry?.device ?? (stage == .qa ? installedDevices.first ?? "" : ""))
        _source = State(initialValue: entry?.source ?? stage?.sources.first ?? .tester)
        _outcome = State(initialValue: entry?.outcome ?? "pass")
        _operation = State(initialValue: entry?.operation ?? "")
        _os = State(initialValue: entry?.os ?? "")
        _version = State(initialValue: entry?.version ?? "")
        _build = State(initialValue: entry?.build ?? "")
        _buildSource = State(initialValue: entry?.buildSource ?? .device)
        _url = State(initialValue: invite ?? "")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            Text(stage?.title ?? "参加リンク").font(.headline)
            Text(scope).foregroundStyle(.secondary)
            if let stage {
                Form {
                    if stage == .qa {
                        Picker("端末", selection: $device) {
                            ForEach(installedDevices, id: \.self) { Text($0).tag($0) }
                        }
                        TextField("操作名", text: $operation, prompt: Text("例　PDFの取り込み"))
                    } else if stage != .distribution {
                        TextField("端末名", text: $device, prompt: Text("例　テスト用iPhone"))
                    }
                    Picker("結果", selection: $outcome) {
                        Text("確認済み").tag("pass")
                        Text("要確認").tag("blocked")
                    }
                    if stage != .distribution {
                        Picker("確認方法", selection: $source) {
                            ForEach(stage.sources) { Text($0.title).tag($0) }
                        }
                    }
                    if stage == .installation {
                        TextField("OS（任意）", text: $os)
                        TextField("端末で確認した版（任意）", text: $version)
                        TextField("ビルド番号（任意）", text: $build)
                        if !trim(build).isEmpty {
                            Picker("ビルド番号の確認元", selection: $buildSource) {
                                ForEach(TestSource.allCases) { Text($0.title).tag($0) }
                            }
                        }
                    }
                    TextField("結果の根拠", text: $note, axis: .vertical).lineLimit(2...4)
                }
                .formStyle(.columns)
            } else {
                TextField("テスト参加URL", text: $url)
                if isTestFlight {
                    Text("内部テスターにはApp Store Connectから招待します。")
                        .foregroundStyle(.secondary)
                }
            }
            if let error { Text(error).foregroundStyle(.red) }
            HStack {
                if stage == nil && hadInvite {
                    Button("リンクを外す") { submit(["test-link", releaseID, "--clear"]) }
                }
                Spacer()
                Button("キャンセル", role: .cancel) { dismiss() }.disabled(busy)
                Button("保存") { submit(arguments) }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
                    .disabled(!canSave)
            }
            .disabled(busy)
        }
        .padding(22)
        .frame(width: 490)
        .interactiveDismissDisabled(busy)
    }

    private var canSave: Bool {
        guard let stage else { return !trim(url).isEmpty }
        return !trim(note).isEmpty && (stage == .distribution || !trim(device).isEmpty)
            && (stage != .qa || !trim(operation).isEmpty)
    }

    private var arguments: [String] {
        guard let stage else { return ["test-link", releaseID, "--url", trim(url)] }
        var result = ["record-test", releaseID, stage.rawValue, "--outcome", outcome, "--source", source.rawValue,
                      "--note=\(trim(note))"]
        let fields = [("device", device), ("operation", operation), ("os", os), ("version", version), ("build", build)]
        result += fields.filter { !trim($0.1).isEmpty }.map { "--\($0.0)=\(trim($0.1))" }
        if !trim(build).isEmpty { result += ["--build-source", buildSource.rawValue] }
        return result
    }

    private func submit(_ arguments: [String]) {
        error = nil
        save(arguments) { message in
            if let message { error = message } else { dismiss() }
        }
    }

    private func trim(_ value: String) -> String {
        value.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
